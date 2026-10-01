// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

// Git metadata and object validation can traverse an NFS-backed worktree's shared object store
// several times. Keep a bounded request lifetime, but leave enough time for external worktrees
// such as yo7220 to complete under normal filesystem contention.
const gitViewTimeout = 30 * time.Second

var gitRefPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$`)
var blameSHA = regexp.MustCompile(`^[0-9a-f]{40}$`)
var blamePR = regexp.MustCompile(`\(#([0-9]+)\)`)

func gitCommandEnvironment() []string {
	environment := make([]string, 0, len(os.Environ())+9)
	for _, entry := range os.Environ() {
		if strings.HasPrefix(entry, "GIT_") {
			continue
		}
		environment = append(environment, entry)
	}
	return append(environment,
		"GIT_OPTIONAL_LOCKS=0",
		"GIT_TERMINAL_PROMPT=0",
		"GIT_CONFIG_NOSYSTEM=1",
		"GIT_CONFIG_GLOBAL="+os.DevNull,
		"GIT_CONFIG_SYSTEM="+os.DevNull,
		"GIT_ATTR_NOSYSTEM=1",
		"GIT_NO_REPLACE_OBJECTS=1",
		"GIT_GRAFT_FILE="+os.DevNull,
		"GIT_NO_LAZY_FETCH=1",
		"GIT_ALTERNATE_OBJECT_DIRECTORIES=",
		"LC_ALL=C",
		"LANG=C",
	)
}

type gitView struct {
	requested      string
	resolved       string
	repo           string
	relative       string
	directory      *os.File
	working        *os.File
	workingMissing bool
	namespace      *gitNamespace
}

func openGitRootDescriptor(ctx context.Context, root, requested string) (*os.File, *Error) {
	identities, err := captureResolvedIdentitiesContext(ctx, root)
	if err != nil {
		if ctx.Err() != nil {
			return nil, cancelled(requested)
		}
		return nil, filesystemError(requested, err)
	}
	directory, err := openResolvedDescriptorContext(ctx, root, true, identities)
	if err != nil {
		if ctx.Err() != nil {
			return nil, cancelled(requested)
		}
		return nil, filesystemError(requested, err)
	}
	return directory, nil
}

func displayGitRootPath(requested, relative, resolvedRoot string) string {
	components := 0
	if relative != "." && relative != "" {
		for _, component := range strings.Split(filepath.Clean(relative), string(os.PathSeparator)) {
			if component != "" && component != "." {
				components++
			}
		}
	}
	candidate := filepath.Clean(requested)
	for index := 0; index < components; index++ {
		candidate = filepath.Dir(candidate)
	}
	resolvedCandidate, err := filepath.EvalSymlinks(candidate)
	if err != nil {
		return resolvedRoot
	}
	resolvedCandidate, err = filepath.Abs(resolvedCandidate)
	if err != nil || filepath.Clean(resolvedCandidate) != filepath.Clean(resolvedRoot) {
		return resolvedRoot
	}
	return candidate
}

var (
	errGitOutputLimit      = errors.New("Git output exceeded limit")
	errGitPathEncoding     = errors.New("Git path is not valid UTF-8")
	errGitNamespaceChanged = errors.New("Git repository changed during the operation")
)

type gitNamespace struct {
	gitDir         *os.File
	commonDir      *os.File
	objects        *os.File
	gitDirPath     string
	commonPath     string
	objectsPath    string
	alternates     string
	alternateID    descriptorIdentity
	alternateSet   bool
	layoutCache    *gitObjectLayoutCache
	snapshotMu     sync.Mutex
	snapshotRoot   string
	snapshotWork   string
	snapshotGit    string
	snapshotCommon string
	snapshotErr    error
	snapshotReady  bool
	workTreePath   string
}

func (namespace *gitNamespace) identity() string {
	if namespace == nil {
		return ""
	}
	identities := make([]string, 0, 3)
	for _, file := range []*os.File{namespace.gitDir, namespace.commonDir, namespace.objects} {
		if file == nil {
			return ""
		}
		info, err := file.Stat()
		if err != nil {
			return ""
		}
		identity := identityFor(info)
		identities = append(identities, fmt.Sprintf("%d:%d", identity.dev, identity.ino))
	}
	return strings.Join(identities, ":")
}

func isGitLooseObjectDirectory(name string) bool {
	return len(name) == 2 && strings.ContainsRune("0123456789abcdef", rune(name[0])) && strings.ContainsRune("0123456789abcdef", rune(name[1]))
}

func (namespace *gitNamespace) close() {
	if namespace == nil {
		return
	}
	for _, file := range []*os.File{namespace.gitDir, namespace.commonDir, namespace.objects} {
		if file != nil {
			_ = file.Close()
		}
	}
	if namespace.snapshotRoot != "" {
		removeDarwinSnapshot(namespace.snapshotRoot)
	}
}

func (namespace *gitNamespace) validateContext(ctx context.Context) error {
	if namespace == nil {
		return nil
	}
	for _, item := range []struct {
		path string
		file *os.File
	}{
		{namespace.gitDirPath, namespace.gitDir},
		{namespace.commonPath, namespace.commonDir},
		{namespace.objectsPath, namespace.objects},
	} {
		if err := ctx.Err(); err != nil {
			return err
		}
		info, err := os.Lstat(item.path)
		if err != nil || info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
			return errGitNamespaceChanged
		}
		pinned, err := item.file.Stat()
		if err != nil || !sameDescriptorIdentity(identityFor(info), identityFor(pinned)) {
			return errGitNamespaceChanged
		}
	}
	if err := namespace.layoutCache.validateContext(ctx, namespace.objectsPath); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	info, err := os.Lstat(namespace.alternates)
	if errors.Is(err, os.ErrNotExist) {
		if namespace.alternateSet {
			return errGitNamespaceChanged
		}
		return nil
	}
	if err != nil || info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return errGitNamespaceChanged
	}
	if !namespace.alternateSet || !sameDescriptorIdentity(namespace.alternateID, identityFor(info)) {
		return errGitNamespaceChanged
	}
	data, err := os.ReadFile(namespace.alternates)
	if err != nil {
		return errGitNamespaceChanged
	}
	if len(bytes.TrimSpace(data)) != 0 {
		return errGitNamespaceChanged
	}
	return nil
}

func (s *Service) openGitView(ctx context.Context, raw string) (*gitView, *Error) {
	if ctx.Err() != nil {
		return nil, cancelled(raw)
	}
	working, requested, resolved, finderErr := s.openAny(raw)
	workingMissing := false
	if finderErr != nil {
		if ctx.Err() != nil {
			return nil, cancelled(raw)
		}
		if finderErr.Status != http.StatusNotFound {
			return nil, finderErr
		}
		requested, resolved, finderErr = s.authorize(raw, true)
		if finderErr != nil {
			return nil, finderErr
		}
		workingMissing = true
	}
	if !workingMissing {
		info, err := working.Stat()
		if err != nil {
			_ = working.Close()
			return nil, filesystemError(requested, err)
		}
		if !info.Mode().IsRegular() {
			_ = working.Close()
			return nil, invalid(requested, "path is not a regular file")
		}
	}
	parentResolved := filepath.Dir(resolved)
	identities, err := captureResolvedIdentitiesContext(ctx, parentResolved)
	if err != nil {
		if working != nil {
			_ = working.Close()
		}
		if ctx.Err() != nil {
			return nil, cancelled(requested)
		}
		return nil, filesystemError(requested, err)
	}
	directory, err := openResolvedDescriptorContext(ctx, parentResolved, true, identities)
	if err != nil {
		if working != nil {
			_ = working.Close()
		}
		if ctx.Err() != nil {
			return nil, cancelled(requested)
		}
		return nil, filesystemError(requested, err)
	}
	view := &gitView{requested: requested, resolved: resolved, directory: directory, working: working, workingMissing: workingMissing}
	var root string
	var namespace *gitNamespace
	if runtime.GOOS == "darwin" {
		namespace, finderErr = s.openGitNamespaceContext(ctx, directory, parentResolved, requested)
		if finderErr == nil {
			root = namespace.workTreePath
		}
	} else {
		rootBytes, _, gitErr := runGitBounded(ctx, directory, nil, []string{"rev-parse", "--show-toplevel"}, 4096)
		if gitErr != nil {
			view.close()
			if ctx.Err() != nil {
				return nil, cancelled(requested)
			}
			return nil, &Error{Status: http.StatusBadRequest, Code: "not_git_repo", MessageKey: "fs.error.notGitRepo", Path: requested, Message: "path is not in a Git repository"}
		}
		root = filepath.Clean(strings.TrimSpace(string(rootBytes)))
		root, err = filepath.Abs(root)
		if err != nil {
			view.close()
			return nil, filesystemError(requested, err)
		}
		namespace, finderErr = s.openGitNamespaceContext(ctx, directory, parentResolved, requested)
	}
	if finderErr != nil {
		view.close()
		return nil, finderErr
	}
	view.namespace = namespace
	relative, err := filepath.Rel(root, resolved)
	if err != nil || relative == "." || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) || filepath.IsAbs(relative) {
		view.close()
		return nil, forbidden(requested, "fs.error.outsideRoots", "path is outside the Git repository")
	}
	view.repo = displayGitRootPath(requested, relative, root)
	view.relative = filepath.ToSlash(relative)
	if !utf8.ValidString(view.relative) {
		view.close()
		return nil, &Error{Status: http.StatusUnprocessableEntity, Code: "git_path_encoding", MessageKey: "fs.error.gitPathEncoding", Path: requested, Message: errGitPathEncoding.Error()}
	}
	rootDirectory, rootErr := openGitRootDescriptor(ctx, root, requested)
	if rootErr != nil {
		view.close()
		return nil, rootErr
	}
	_ = view.directory.Close()
	view.directory = rootDirectory
	return view, nil
}

func resolveGitNamespacePath(raw, base string) (string, error) {
	path := strings.TrimSpace(raw)
	if path == "" {
		return "", errors.New("Git namespace path is empty")
	}
	if !filepath.IsAbs(path) {
		path = filepath.Join(base, path)
	}
	path, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	return filepath.EvalSymlinks(path)
}

func (s *Service) openGitNamespace(directory *os.File, workingDirectory, requested string) (*gitNamespace, *Error) {
	return s.openGitNamespaceContext(context.Background(), directory, workingDirectory, requested)
}

func (s *Service) openGitNamespaceContext(ctx context.Context, directory *os.File, workingDirectory, requested string) (*gitNamespace, *Error) {
	if runtime.GOOS == "darwin" {
		return s.openDarwinGitNamespace(ctx, directory, workingDirectory, requested)
	}
	return s.openGitNamespaceGenericContext(ctx, directory, workingDirectory, requested)
}

func (s *Service) openGitNamespaceGenericContext(ctx context.Context, directory *os.File, workingDirectory, requested string) (*gitNamespace, *Error) {
	paths, _, err := runGitBounded(ctx, directory, nil, []string{"rev-parse", "--git-dir", "--git-common-dir", "--git-path", "objects"}, 3*4096)
	if err != nil && ctx.Err() != nil {
		return nil, cancelled(requested)
	}
	var pathLines []string
	if err == nil {
		combined := strings.Split(strings.TrimSuffix(string(paths), "\n"), "\n")
		if len(combined) == 3 {
			pathLines = combined
		}
	}
	if pathLines == nil {
		// A legal filesystem path can contain newlines, so retain the individual commands when
		// combined output cannot be separated unambiguously.
		pathLines = make([]string, 3)
		commands := [][]string{{"rev-parse", "--git-dir"}, {"rev-parse", "--git-common-dir"}, {"rev-parse", "--git-path", "objects"}}
		for index, args := range commands {
			output, _, commandErr := runGitBounded(ctx, directory, nil, args, 4096)
			if commandErr != nil {
				if ctx.Err() != nil {
					return nil, cancelled(requested)
				}
				if index == 0 {
					return nil, &Error{Status: http.StatusBadRequest, Code: "not_git_repo", MessageKey: "fs.error.notGitRepo", Path: requested, Message: "path is not in a Git repository"}
				}
				if index == 1 {
					return nil, &Error{Status: http.StatusBadRequest, Code: "not_git_repo", MessageKey: "fs.error.notGitRepo", Path: requested, Message: "Git common directory is unavailable"}
				}
				return nil, &Error{Status: http.StatusBadRequest, Code: "not_git_repo", MessageKey: "fs.error.notGitRepo", Path: requested, Message: "Git object store is unavailable"}
			}
			pathLines[index] = strings.TrimSpace(string(output))
		}
	}
	gitDir, err := resolveGitNamespacePath(pathLines[0], workingDirectory)
	if err != nil {
		return nil, filesystemError(requested, err)
	}
	commonDir, err := resolveGitNamespacePath(pathLines[1], workingDirectory)
	if err != nil {
		return nil, filesystemError(requested, err)
	}
	objects, err := resolveGitNamespacePath(pathLines[2], workingDirectory)
	if err != nil {
		return nil, filesystemError(requested, err)
	}
	objectsInfo, err := os.Lstat(objects)
	if err != nil {
		return nil, filesystemError(requested, err)
	}
	if objectsInfo.Mode()&os.ModeSymlink != 0 || !objectsInfo.IsDir() {
		return nil, forbidden(requested, "fs.error.outsideRoots", "Git object store is not a local directory")
	}
	if err := s.gitObjectLayouts.validateContext(ctx, objects); err != nil {
		if ctx.Err() != nil {
			return nil, cancelled(requested)
		}
		return nil, gitNamespaceChanged(requested)
	}
	alternates := filepath.Join(objects, "info", "alternates")
	namespace := &gitNamespace{gitDirPath: gitDir, commonPath: commonDir, objectsPath: objects, alternates: alternates, layoutCache: &s.gitObjectLayouts}
	for _, item := range []struct {
		path      string
		directory **os.File
	}{
		{gitDir, &namespace.gitDir},
		{commonDir, &namespace.commonDir},
		{objects, &namespace.objects},
	} {
		identities, identityErr := captureResolvedIdentitiesContext(ctx, item.path)
		if identityErr != nil {
			namespace.close()
			if ctx.Err() != nil {
				return nil, cancelled(requested)
			}
			return nil, filesystemError(requested, identityErr)
		}
		opened, openErr := openResolvedDescriptorContext(ctx, item.path, true, identities)
		if openErr != nil {
			namespace.close()
			if ctx.Err() != nil {
				return nil, cancelled(requested)
			}
			return nil, filesystemError(requested, openErr)
		}
		*item.directory = opened
	}
	if info, statErr := os.Lstat(alternates); statErr == nil {
		if info.Mode()&os.ModeSymlink != 0 {
			namespace.close()
			return nil, forbidden(requested, "fs.error.outsideRoots", "Git alternate object store is not trusted")
		}
		data, readErr := os.ReadFile(alternates)
		if readErr != nil {
			namespace.close()
			return nil, filesystemError(requested, readErr)
		}
		if strings.TrimSpace(string(data)) != "" {
			namespace.close()
			return nil, forbidden(requested, "fs.error.outsideRoots", "Git alternate object stores are not supported")
		}
		namespace.alternateSet = true
		namespace.alternateID = identityFor(info)
	} else if !errors.Is(statErr, os.ErrNotExist) {
		namespace.close()
		return nil, filesystemError(requested, statErr)
	}
	return namespace, nil
}

func (view *gitView) close() {
	if view == nil {
		return
	}
	if view.working != nil {
		_ = view.working.Close()
	}
	if view.directory != nil {
		_ = view.directory.Close()
	}
	view.namespace.close()
}

type boundedBuffer struct {
	data  []byte
	limit int
	over  bool
}

func (buffer *boundedBuffer) Write(data []byte) (int, error) {
	if len(buffer.data)+len(data) > buffer.limit {
		remaining := buffer.limit - len(buffer.data)
		if remaining > 0 {
			buffer.data = append(buffer.data, data[:remaining]...)
		}
		buffer.over = true
		return len(data), errGitOutputLimit
	}
	buffer.data = append(buffer.data, data...)
	return len(data), nil
}

func runGitBounded(ctx context.Context, directory, working *os.File, args []string, limit int, namespaces ...*gitNamespace) ([]byte, []byte, error) {
	return runGitBoundedWithWorktree(ctx, directory, working, args, limit, true, namespaces...)
}

// runGitMetadataBounded is for commands that read Git metadata and committed objects only. On
// macOS it avoids copying the repository worktree; callers that need current file bytes must read
// them through an already-open descriptor.
func runGitMetadataBounded(ctx context.Context, directory, working *os.File, args []string, limit int, namespaces ...*gitNamespace) ([]byte, []byte, error) {
	return runGitBoundedWithWorktree(ctx, directory, working, args, limit, false, namespaces...)
}

func runGitBoundedWithWorktree(ctx context.Context, directory, working *os.File, args []string, limit int, includeWorktree bool, namespaces ...*gitNamespace) ([]byte, []byte, error) {
	if directory == nil || limit <= 0 {
		return nil, nil, errors.New("invalid Git command scope")
	}
	directoryCopy, err := copyDescriptor(directory)
	if err != nil {
		return nil, nil, err
	}
	defer directoryCopy.Close()
	extraFiles := []*os.File{directoryCopy}
	var namespace *gitNamespace
	if len(namespaces) > 1 {
		return nil, nil, errors.New("invalid Git namespace scope")
	}
	if len(namespaces) == 1 {
		namespace = namespaces[0]
		if err := namespace.validateContext(ctx); err != nil {
			return nil, nil, err
		}
		if runtime.GOOS == "darwin" {
			if err := namespace.prepareSnapshot(ctx, directory); err != nil {
				return nil, nil, err
			}
		}
	}
	gitWorkingPath := platformGitPath(directoryCopy, 3)
	if namespace != nil && runtime.GOOS == "darwin" {
		gitWorkingPath = namespace.snapshotWork
	}
	commandPrefix := []string{"-C", gitWorkingPath, "--literal-pathspecs", "-c", "advice.graftFileDeprecated=false"}
	if namespace != nil && runtime.GOOS == "darwin" && includeWorktree {
		commandPrefix = []string{"--literal-pathspecs", "-c", "advice.graftFileDeprecated=false"}
	}
	commandArgs := append(commandPrefix, args...)
	if working != nil {
		workingCopy, copyErr := copyDescriptor(working)
		if copyErr != nil {
			return nil, nil, copyErr
		}
		defer workingCopy.Close()
		extraFiles = append(extraFiles, workingCopy)
	}
	namespaceFDs := map[string]int{}
	if namespace != nil && runtime.GOOS != "darwin" {
		for _, item := range []struct {
			name string
			file *os.File
		}{
			{"git", namespace.gitDir}, {"common", namespace.commonDir}, {"objects", namespace.objects},
		} {
			copy, copyErr := copyDescriptor(item.file)
			if copyErr != nil {
				return nil, nil, copyErr
			}
			defer copy.Close()
			namespaceFDs[item.name] = 3 + len(extraFiles)
			extraFiles = append(extraFiles, copy)
		}
	}
	command := execCommandContext(ctx, commandArgs, extraFiles)
	if namespace != nil {
		gitDir, commonDir, objects, workTree := platformGitPath(namespace.gitDir, namespaceFDs["git"]), platformGitPath(namespace.commonDir, namespaceFDs["common"]), platformGitPath(namespace.objects, namespaceFDs["objects"]), platformGitPath(directoryCopy, 3)
		if runtime.GOOS == "darwin" {
			gitDir, commonDir, objects, workTree = namespace.snapshotGit, namespace.snapshotCommon, filepath.Join(namespace.snapshotCommon, "objects"), namespace.snapshotWork
			if includeWorktree {
				workTree = "."
			}
		}
		command.Env = append(gitCommandEnvironment(),
			"GIT_DIR="+gitDir,
			"GIT_COMMON_DIR="+commonDir,
			"GIT_OBJECT_DIRECTORY="+objects,
			"GIT_WORK_TREE="+workTree,
		)
	}
	stdout := &boundedBuffer{limit: limit}
	stderr := &boundedBuffer{limit: 64 * 1024}
	command.Stdout = stdout
	command.Stderr = stderr
	restoreWorktree := func() error { return nil }
	if namespace != nil && runtime.GOOS == "darwin" && includeWorktree {
		restoreWorktree, err = platformEnterGitWorktree(directoryCopy)
		if err != nil {
			return nil, nil, err
		}
	}
	err = command.Run()
	if restoreErr := restoreWorktree(); restoreErr != nil && err == nil {
		err = restoreErr
	}
	if stdout.over || stderr.over {
		return stdout.data, stderr.data, errGitOutputLimit
	}
	if namespace != nil {
		if namespaceErr := namespace.validateContext(ctx); namespaceErr != nil {
			return stdout.data, stderr.data, namespaceErr
		}
	}
	if ctx.Err() != nil {
		return stdout.data, stderr.data, ctx.Err()
	}
	return stdout.data, stderr.data, err
}

// Kept as a variable so the bounded runner remains easy to exercise without replacing os/exec
// globally in tests. It is initialized in service.go with the standard command constructor.
var execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
	command := exec.CommandContext(ctx, "git", args...)
	command.ExtraFiles = extraFiles
	command.Env = gitCommandEnvironment()
	return command
}

func gitViewContext(parent context.Context) (context.Context, context.CancelFunc) {
	if parent == nil {
		parent = context.Background()
	}
	return context.WithTimeout(parent, gitViewTimeout)
}

func gitNamespaceChanged(path string) *Error {
	return &Error{Status: http.StatusConflict, Code: "git_repository_changed", MessageKey: "fs.error.gitRepositoryChanged", Path: path, Message: errGitNamespaceChanged.Error()}
}

func validateGitRef(value, key string) *Error {
	if value == "current" || value == "HEAD" || gitRefPattern.MatchString(value) {
		return nil
	}
	return &Error{Status: http.StatusBadRequest, Code: "invalid_ref", MessageKey: key, Message: "invalid Git ref"}
}

func (s *Service) gitBlob(ctx context.Context, view *gitView, ref, label string) (string, string, *Error) {
	if ref == "current" {
		return "", "", invalid(view.requested, "current is the working tree")
	}
	output, _, err := runGitMetadataBounded(ctx, view.directory, nil, []string{"show", ref + ":" + view.relative}, MaxReadBytes+1, view.namespace)
	if errors.Is(err, errGitNamespaceChanged) {
		return "", "", gitNamespaceChanged(view.requested)
	}
	if len(output) > MaxReadBytes {
		return "", "", &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitBlobTooLarge", Path: view.requested, Message: fmt.Sprintf("%s is too large", label)}
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return "", "", cancelled(view.requested)
	}
	if err != nil {
		return "", "", nil
	}
	if bytesLookBinary(output) {
		return "", label + " file appears to be binary", nil
	}
	return string(output), "", nil
}

type unifiedDiffOpcode struct {
	tag     string
	oldFrom int
	oldTo   int
	newFrom int
	newTo   int
}

type unifiedDiffEdit struct {
	tag     byte
	oldLine int
	newLine int
}

func splitUnifiedDiffLines(value string) []string {
	if value == "" {
		return nil
	}
	lines := strings.SplitAfter(value, "\n")
	// Python's splitlines(keepends=True) does not add a synthetic empty line after a final
	// newline; strings.SplitAfter does, so remove only that sentinel.
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	return lines
}

func cloneUnifiedDiffFrontier(frontier map[int]int) map[int]int {
	clone := make(map[int]int, len(frontier))
	for diagonal, oldLine := range frontier {
		clone[diagonal] = oldLine
	}
	return clone
}

// myersUnifiedDiffOpcodes produces the line operations needed by Python's unified_diff shape.
// It keeps the edit frontier proportional to the number of changed lines instead of allocating
// an old-lines by new-lines matrix for a large source file.
func myersUnifiedDiffOpcodes(oldLines, newLines []string) []unifiedDiffOpcode {
	if len(oldLines) == 0 && len(newLines) == 0 {
		return nil
	}
	frontier := map[int]int{1: 0}
	trace := make([]map[int]int, 0, len(oldLines)+len(newLines)+1)
	maxDistance := len(oldLines) + len(newLines)
	for distance := 0; distance <= maxDistance; distance++ {
		for diagonal := -distance; diagonal <= distance; diagonal += 2 {
			oldLine := 0
			if diagonal == -distance || (diagonal != distance && frontier[diagonal-1] < frontier[diagonal+1]) {
				oldLine = frontier[diagonal+1]
			} else {
				oldLine = frontier[diagonal-1] + 1
			}
			newLine := oldLine - diagonal
			for oldLine < len(oldLines) && newLine < len(newLines) && oldLines[oldLine] == newLines[newLine] {
				oldLine++
				newLine++
			}
			frontier[diagonal] = oldLine
			if oldLine >= len(oldLines) && newLine >= len(newLines) {
				trace = append(trace, cloneUnifiedDiffFrontier(frontier))
				return unifiedDiffOpcodesFromTrace(trace, oldLines, newLines)
			}
		}
		trace = append(trace, cloneUnifiedDiffFrontier(frontier))
	}
	return nil
}

func unifiedDiffOpcodesFromTrace(trace []map[int]int, oldLines, newLines []string) []unifiedDiffOpcode {
	oldLine, newLine := len(oldLines), len(newLines)
	edits := make([]unifiedDiffEdit, 0, oldLine+newLine)
	for distance := len(trace) - 1; distance > 0; distance-- {
		frontier := trace[distance-1]
		diagonal := oldLine - newLine
		previousDiagonal := diagonal - 1
		if diagonal == -distance || (diagonal != distance && frontier[diagonal-1] < frontier[diagonal+1]) {
			previousDiagonal = diagonal + 1
		}
		previousOldLine := frontier[previousDiagonal]
		previousNewLine := previousOldLine - previousDiagonal
		for oldLine > previousOldLine && newLine > previousNewLine {
			oldLine--
			newLine--
			edits = append(edits, unifiedDiffEdit{tag: '=', oldLine: oldLine, newLine: newLine})
		}
		if oldLine == previousOldLine {
			newLine--
			edits = append(edits, unifiedDiffEdit{tag: '+', oldLine: oldLine, newLine: newLine})
		} else {
			oldLine--
			edits = append(edits, unifiedDiffEdit{tag: '-', oldLine: oldLine, newLine: newLine})
		}
	}
	for oldLine > 0 && newLine > 0 {
		oldLine--
		newLine--
		edits = append(edits, unifiedDiffEdit{tag: '=', oldLine: oldLine, newLine: newLine})
	}
	for oldLine > 0 {
		oldLine--
		edits = append(edits, unifiedDiffEdit{tag: '-', oldLine: oldLine, newLine: newLine})
	}
	for newLine > 0 {
		newLine--
		edits = append(edits, unifiedDiffEdit{tag: '+', oldLine: oldLine, newLine: newLine})
	}
	for left, right := 0, len(edits)-1; left < right; left, right = left+1, right-1 {
		edits[left], edits[right] = edits[right], edits[left]
	}

	opcodes := make([]unifiedDiffOpcode, 0)
	oldCursor, newCursor := 0, 0
	for index := 0; index < len(edits); {
		if edits[index].tag == '=' {
			startOld, startNew := oldCursor, newCursor
			for index < len(edits) && edits[index].tag == '=' {
				oldCursor++
				newCursor++
				index++
			}
			opcodes = append(opcodes, unifiedDiffOpcode{tag: "equal", oldFrom: startOld, oldTo: oldCursor, newFrom: startNew, newTo: newCursor})
			continue
		}
		startOld, startNew := oldCursor, newCursor
		hasDelete, hasInsert := false, false
		for index < len(edits) && edits[index].tag != '=' {
			if edits[index].tag == '-' {
				oldCursor++
				hasDelete = true
			} else {
				newCursor++
				hasInsert = true
			}
			index++
		}
		tag := "replace"
		if !hasDelete {
			tag = "insert"
		} else if !hasInsert {
			tag = "delete"
		}
		opcodes = append(opcodes, unifiedDiffOpcode{tag: tag, oldFrom: startOld, oldTo: oldCursor, newFrom: startNew, newTo: newCursor})
	}
	return opcodes
}

func groupedUnifiedDiffOpcodes(opcodes []unifiedDiffOpcode, contextLines int) [][]unifiedDiffOpcode {
	if len(opcodes) == 0 {
		return nil
	}
	if contextLines < 0 {
		contextLines = 0
	}
	first := opcodes[0]
	if first.tag == "equal" {
		first.oldFrom = maxUnifiedDiffInt(first.oldFrom, first.oldTo-contextLines)
		first.newFrom = maxUnifiedDiffInt(first.newFrom, first.newTo-contextLines)
		opcodes[0] = first
	}
	lastIndex := len(opcodes) - 1
	last := opcodes[lastIndex]
	if last.tag == "equal" {
		last.oldTo = minUnifiedDiffInt(last.oldTo, last.oldFrom+contextLines)
		last.newTo = minUnifiedDiffInt(last.newTo, last.newFrom+contextLines)
		opcodes[lastIndex] = last
	}
	groups := make([][]unifiedDiffOpcode, 0)
	group := make([]unifiedDiffOpcode, 0)
	for _, opcode := range opcodes {
		if opcode.tag == "equal" && opcode.oldTo-opcode.oldFrom > 2*contextLines {
			group = append(group, unifiedDiffOpcode{tag: "equal", oldFrom: opcode.oldFrom, oldTo: opcode.oldFrom + contextLines, newFrom: opcode.newFrom, newTo: opcode.newFrom + contextLines})
			if len(group) > 0 {
				groups = append(groups, group)
			}
			group = []unifiedDiffOpcode{{tag: "equal", oldFrom: opcode.oldTo - contextLines, oldTo: opcode.oldTo, newFrom: opcode.newTo - contextLines, newTo: opcode.newTo}}
			continue
		}
		group = append(group, opcode)
	}
	if len(group) > 0 && !(len(group) == 1 && group[0].tag == "equal") {
		groups = append(groups, group)
	}
	return groups
}

func maxUnifiedDiffInt(left, right int) int {
	if left > right {
		return left
	}
	return right
}

func minUnifiedDiffInt(left, right int) int {
	if left < right {
		return left
	}
	return right
}

func unifiedDiffRange(from, to int) string {
	start := from + 1
	length := to - from
	if length == 1 {
		return strconv.Itoa(start)
	}
	return fmt.Sprintf("%d,%d", start, length)
}

func unifiedFileDiff(original, working, relative string) string {
	if original == working {
		return ""
	}
	oldLines, newLines := splitUnifiedDiffLines(original), splitUnifiedDiffLines(working)
	groups := groupedUnifiedDiffOpcodes(myersUnifiedDiffOpcodes(oldLines, newLines), 3)
	if len(groups) == 0 {
		return ""
	}
	var builder strings.Builder
	fmt.Fprintf(&builder, "diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n", relative, relative, relative, relative)
	for _, group := range groups {
		first, last := group[0], group[len(group)-1]
		fmt.Fprintf(&builder, "@@ -%s +%s @@\n", unifiedDiffRange(first.oldFrom, last.oldTo), unifiedDiffRange(first.newFrom, last.newTo))
		for _, opcode := range group {
			switch opcode.tag {
			case "equal":
				for index := opcode.oldFrom; index < opcode.oldTo; index++ {
					builder.WriteByte(' ')
					builder.WriteString(oldLines[index])
				}
			case "delete":
				for index := opcode.oldFrom; index < opcode.oldTo; index++ {
					builder.WriteByte('-')
					builder.WriteString(oldLines[index])
				}
			case "insert":
				for index := opcode.newFrom; index < opcode.newTo; index++ {
					builder.WriteByte('+')
					builder.WriteString(newLines[index])
				}
			case "replace":
				for index := opcode.oldFrom; index < opcode.oldTo; index++ {
					builder.WriteByte('-')
					builder.WriteString(oldLines[index])
				}
				for index := opcode.newFrom; index < opcode.newTo; index++ {
					builder.WriteByte('+')
					builder.WriteString(newLines[index])
				}
			}
		}
	}
	return builder.String()
}

func (s *Service) Diff(ctx context.Context, raw string, fromRef, toRef string) (map[string]any, *Error) {
	view, finderErr := s.openGitView(ctx, raw)
	if finderErr != nil {
		return nil, finderErr
	}
	defer view.close()
	from := strings.TrimSpace(fromRef)
	to := strings.TrimSpace(toRef)
	if from == "" {
		from = "HEAD"
	}
	if to == "" {
		to = "current"
	}
	if refErr := validateGitRef(from, "common.unknownFromRef"); refErr != nil {
		return nil, refErr
	}
	if refErr := validateGitRef(to, "common.unknownToRef"); refErr != nil {
		return nil, refErr
	}
	gitCtx, cancel := gitViewContext(ctx)
	defer cancel()
	trackedArgs := []string{"ls-files", "--error-unmatch"}
	trackedArgs = append(trackedArgs, historyPathArgs(view.relative)...)
	trackedOutput, _, trackedErr := runGitMetadataBounded(gitCtx, view.directory, nil, trackedArgs, 64*1024, view.namespace)
	if errors.Is(trackedErr, errGitNamespaceChanged) {
		return nil, gitNamespaceChanged(view.requested)
	}
	tracked := trackedErr == nil && strings.TrimSpace(string(trackedOutput)) != ""
	requestedRefs := strings.TrimSpace(fromRef) != "" || strings.TrimSpace(toRef) != ""
	if to == "current" && trackedErr != nil && !view.workingMissing {
		// An untracked file still has a valid current-tree diff.
		tracked = false
	}
	if !(to == "current" && !tracked) {
		for _, refInfo := range []struct {
			ref string
			key string
		}{{from, "common.unknownFromRef"}, {to, "common.unknownToRef"}} {
			ref := refInfo.ref
			if ref == "current" {
				continue
			}
			if _, _, err := runGitMetadataBounded(gitCtx, view.directory, nil, []string{"rev-parse", "--verify", "--quiet", ref + "^{commit}"}, 4096, view.namespace); err != nil {
				if errors.Is(err, errGitNamespaceChanged) {
					return nil, gitNamespaceChanged(view.requested)
				}
				if requestedRefs {
					from, to = "HEAD", "current"
					break
				}
				return nil, &Error{Status: http.StatusBadRequest, Code: "unknown_ref", MessageKey: refInfo.key, Path: view.requested, Message: "unknown Git ref"}
			}
		}
		if to != "current" {
			if from == "current" {
				return nil, &Error{Status: http.StatusBadRequest, Code: "ref_order", MessageKey: "fs.error.refOrderCurrent", Path: view.requested, Message: "current cannot precede a commit"}
			}
			_, _, orderErr := runGitMetadataBounded(gitCtx, view.directory, nil, []string{"merge-base", "--is-ancestor", from, to}, 4096, view.namespace)
			if errors.Is(orderErr, errGitNamespaceChanged) {
				return nil, gitNamespaceChanged(view.requested)
			}
			if orderErr != nil {
				if requestedRefs {
					from, to = "HEAD", "current"
				} else {
					return nil, &Error{Status: http.StatusBadRequest, Code: "ref_order", MessageKey: "fs.error.refOrder", Path: view.requested, Message: "FROM ref must precede TO ref"}
				}
			}
		}
	}
	var original, originalError, working, workingError string
	if to == "current" {
		if tracked {
			original, originalError, finderErr = s.gitBlob(gitCtx, view, from, "original")
			if finderErr != nil {
				return nil, finderErr
			}
		}
		if !view.workingMissing {
			data, err := readDescriptorContext(gitCtx, view.working, MaxReadBytes)
			if err != nil {
				if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
					return nil, cancelled(view.requested)
				}
				return nil, filesystemError(view.requested, err)
			}
			if len(data) > MaxReadBytes {
				return nil, tooLarge(view.requested, int64(len(data)), MaxReadBytes)
			}
			if bytesLookBinary(data) {
				workingError = "working file appears to be binary"
			} else {
				working = string(data)
			}
		}
	} else {
		original, originalError, finderErr = s.gitBlob(gitCtx, view, from, "original")
		if finderErr != nil {
			return nil, finderErr
		}
		working, workingError, finderErr = s.gitBlob(gitCtx, view, to, "working")
		if finderErr != nil {
			return nil, finderErr
		}
	}
	diff := unifiedFileDiff(original, working, view.relative)
	if to != "current" && originalError == "" && workingError == "" {
		diffArgs := []string{"diff", from, to}
		diffArgs = append(diffArgs, historyPathArgs(view.relative)...)
		output, _, diffErr := runGitMetadataBounded(gitCtx, view.directory, nil, diffArgs, MaxReadBytes+1, view.namespace)
		if errors.Is(diffErr, errGitNamespaceChanged) {
			return nil, gitNamespaceChanged(view.requested)
		}
		if len(output) > MaxReadBytes {
			return nil, tooLarge(view.requested, int64(len(output)), MaxReadBytes)
		}
		if diffErr != nil {
			return nil, &Error{Status: http.StatusInternalServerError, Code: "git_diff_failed", MessageKey: "fs.error.gitDiffFailed", Path: view.requested, Retryable: true, Message: "Git diff failed"}
		}
		diff = string(output)
	}
	if originalError != "" || workingError != "" {
		diff = ""
		if tracked && (original != working || originalError != "" && workingError != "") {
			binaryArgs := []string{"diff", from}
			if to != "current" {
				binaryArgs = append(binaryArgs, to)
			}
			binaryArgs = append(binaryArgs, historyPathArgs(view.relative)...)
			var binaryDiff []byte
			var binaryErr error
			if to == "current" {
				binaryDiff, _, binaryErr = runGitBounded(gitCtx, view.directory, nil, binaryArgs, MaxReadBytes+1, view.namespace)
			} else {
				binaryDiff, _, binaryErr = runGitMetadataBounded(gitCtx, view.directory, nil, binaryArgs, MaxReadBytes+1, view.namespace)
			}
			if errors.Is(binaryErr, errGitNamespaceChanged) {
				return nil, gitNamespaceChanged(view.requested)
			}
			if errors.Is(binaryErr, context.Canceled) || errors.Is(binaryErr, context.DeadlineExceeded) {
				return nil, cancelled(view.requested)
			}
			if binaryErr == nil && len(binaryDiff) > 0 {
				diff = string(binaryDiff)
			} else if original != working {
				diff = fmt.Sprintf("Binary files a/%s and b/%s differ\n", view.relative, view.relative)
			}
		}
	}
	if len(diff) > MaxReadBytes {
		return nil, tooLarge(view.requested, int64(len(diff)), MaxReadBytes)
	}
	return map[string]any{
		"path": view.requested, "repo": view.repo, "relative_path": view.relative, "diff": diff,
		"original": original, "original_error": originalError, "working": working, "working_error": workingError,
		"working_missing": view.workingMissing, "from_ref": from, "to_ref": to, "untracked": !tracked,
	}, nil
}

func parseBlamePorcelain(text string) map[string]map[string]any {
	lines := map[string]map[string]any{}
	metadata := map[string]map[string]any{}
	currentSHA := ""
	finalLine := 0
	for _, raw := range strings.Split(text, "\n") {
		if raw == "" {
			continue
		}
		if strings.HasPrefix(raw, "\t") {
			if finalLine == 0 {
				continue
			}
			info := metadata[currentSHA]
			if info == nil {
				info = map[string]any{}
			}
			uncommitted := currentSHA == strings.Repeat("0", 40)
			summary, _ := info["summary"].(string)
			author, _ := info["author"].(string)
			line := map[string]any{"sha": currentSHA, "author": author, "time": info["author_time"], "summary": summary, "pr": nil}
			if uncommitted {
				line["author"], line["time"], line["summary"] = "You", time.Now().Unix(), "Uncommitted changes"
			}
			if match := blamePR.FindStringSubmatch(summary); len(match) == 2 {
				line["pr"], _ = strconv.Atoi(match[1])
			}
			lines[strconv.Itoa(finalLine)] = line
			continue
		}
		fields := strings.SplitN(raw, " ", 4)
		if len(fields) >= 3 && blameSHA.MatchString(fields[0]) {
			currentSHA = fields[0]
			finalLine, _ = strconv.Atoi(fields[2])
			if metadata[currentSHA] == nil {
				metadata[currentSHA] = map[string]any{}
			}
			continue
		}
		if strings.HasPrefix(raw, "author ") {
			metadata[currentSHA]["author"] = strings.TrimPrefix(raw, "author ")
		} else if strings.HasPrefix(raw, "author-time ") {
			metadata[currentSHA]["author_time"], _ = strconv.ParseInt(strings.TrimPrefix(raw, "author-time "), 10, 64)
		} else if strings.HasPrefix(raw, "summary ") {
			metadata[currentSHA]["summary"] = strings.TrimPrefix(raw, "summary ")
		}
	}
	return lines
}

func (s *Service) Blame(ctx context.Context, raw, ref string) (map[string]any, *Error) {
	view, finderErr := s.openGitView(ctx, raw)
	if finderErr != nil {
		if finderErr.MessageKey == "fs.error.notGitRepo" {
			return map[string]any{"path": raw, "repo": "", "relative_path": "", "in_repo": false, "lines": map[string]map[string]any{}}, nil
		}
		return nil, finderErr
	}
	defer view.close()
	if view.workingMissing {
		return map[string]any{"path": view.requested, "repo": view.repo, "relative_path": view.relative, "in_repo": true, "lines": map[string]map[string]any{}}, nil
	}
	useRef := strings.TrimSpace(ref)
	if useRef == "working" {
		// Python treats the working-tree spelling as the current contents supplied through --contents.
		useRef = "current"
	}
	if err := validateGitRef(useRef, "common.unknownFromRef"); err != nil && useRef != "" {
		return nil, err
	}
	gitCtx, cancel := gitViewContext(ctx)
	defer cancel()
	args := []string{"blame", "--line-porcelain", "--contents", platformChildFDPath(4)}
	if useRef != "" && useRef != "HEAD" && useRef != "current" {
		args = append(args, useRef)
	}
	args = append(args, historyPathArgs(view.relative)...)
	output, stderr, err := runGitMetadataBounded(gitCtx, view.directory, view.working, args, MaxReadBytes, view.namespace)
	if err != nil {
		if errors.Is(err, errGitNamespaceChanged) {
			return nil, gitNamespaceChanged(view.requested)
		}
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return nil, cancelled(view.requested)
		}
		if errors.Is(err, errGitOutputLimit) {
			return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitHistoryTooLarge", Path: view.requested, Message: "Git blame is too large"}
		}
		errorText := strings.TrimSpace(string(stderr))
		if errorText == "" {
			errorText = "not committed yet"
		}
		return map[string]any{"path": view.requested, "repo": view.repo, "relative_path": view.relative, "in_repo": true, "lines": map[string]map[string]any{}, "error": errorText}, nil
	}
	head, _, headErr := runGitMetadataBounded(gitCtx, view.directory, nil, []string{"rev-parse", "HEAD"}, 4096, view.namespace)
	if errors.Is(headErr, errGitNamespaceChanged) {
		return nil, gitNamespaceChanged(view.requested)
	}
	return map[string]any{"path": view.requested, "repo": view.repo, "relative_path": view.relative, "head": strings.TrimSpace(string(head)), "in_repo": true, "lines": parseBlamePorcelain(string(output))}, nil
}
