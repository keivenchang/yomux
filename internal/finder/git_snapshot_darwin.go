// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build darwin

package finder

import (
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"

	"golang.org/x/sys/unix"
)

const darwinGitSnapshotMaxEntries = 250_000
const darwinGitSnapshotMaxBytes = 2 << 30
const darwinGitConfigDataMaxBytes = 16 << 20
const darwinGitSnapshotCloneWorkers = 8

var errGitAlternateObjectStore = errors.New("Git alternate object stores are not supported")

func (s *Service) openDarwinGitNamespace(ctx context.Context, directory *os.File, workingDirectory, requested string) (*gitNamespace, *Error) {
	_ = workingDirectory
	if ctx.Err() != nil {
		return nil, cancelled(requested)
	}
	current, err := openAt(directory, ".", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, filesystemError(requested, err)
	}
	for depth := 0; depth < 4096; depth++ {
		if ctx.Err() != nil {
			_ = current.Close()
			return nil, cancelled(requested)
		}
		rootPath, pathErr := resolvedDescriptorPath(current)
		if pathErr != nil {
			_ = current.Close()
			return nil, filesystemError(requested, pathErr)
		}
		marker, markerErr := lstatAt(current, ".git")
		if markerErr == nil {
			namespace, openErr := s.openDarwinNamespaceAt(ctx, current, rootPath, marker)
			_ = current.Close()
			if openErr != nil {
				if ctx.Err() != nil {
					return nil, cancelled(requested)
				}
				if errors.Is(openErr, errGitNamespaceChanged) {
					return nil, gitNamespaceChanged(requested)
				}
				if errors.Is(openErr, errGitAlternateObjectStore) {
					return nil, forbidden(requested, "fs.error.outsideRoots", "Git alternate object stores are not supported")
				}
				return nil, filesystemError(requested, openErr)
			}
			return namespace, nil
		}
		if !errors.Is(markerErr, os.ErrNotExist) {
			_ = current.Close()
			return nil, filesystemError(requested, markerErr)
		}
		parent, parentErr := openAt(current, "..", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
		if parentErr != nil {
			_ = current.Close()
			return nil, filesystemError(requested, parentErr)
		}
		currentInfo, currentErr := current.Stat()
		parentInfo, statErr := parent.Stat()
		if currentErr != nil || statErr != nil || sameDescriptorIdentity(identityFor(currentInfo), identityFor(parentInfo)) {
			_ = current.Close()
			_ = parent.Close()
			break
		}
		_ = current.Close()
		current = parent
	}
	return nil, &Error{Status: 400, Code: "not_git_repo", MessageKey: "fs.error.notGitRepo", Path: requested, Message: "path is not in a Git repository"}
}

func (s *Service) openDarwinNamespaceAt(ctx context.Context, workTree *os.File, workTreePath string, marker os.FileInfo) (*gitNamespace, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if marker.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("Git metadata symlinks are not supported")
	}
	var gitDir *os.File
	var gitDirPath string
	var err error
	if marker.IsDir() {
		var err error
		gitDir, err = openAt(workTree, ".git", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
		if err != nil {
			return nil, err
		}
		gitInfo, statErr := gitDir.Stat()
		if statErr != nil || !sameDescriptorIdentity(identityFor(marker), identityFor(gitInfo)) {
			_ = gitDir.Close()
			return nil, errGitNamespaceChanged
		}
		gitDirPath, err = resolvedDescriptorPath(gitDir)
		if err != nil {
			_ = gitDir.Close()
			return nil, err
		}
	} else if marker.Mode().IsRegular() && marker.Size() <= 4096 {
		data, err := readDarwinGitMetadata(ctx, workTree, ".git")
		if err != nil {
			return nil, err
		}
		line := strings.TrimSpace(string(data))
		if !strings.HasPrefix(line, "gitdir:") {
			return nil, errors.New("invalid Git directory marker")
		}
		gitDirPath = strings.TrimSpace(strings.TrimPrefix(line, "gitdir:"))
		if !filepath.IsAbs(gitDirPath) {
			gitDirPath = filepath.Join(workTreePath, gitDirPath)
		}
		gitDirPath = filepath.Clean(gitDirPath)
		gitDir, err = openDarwinDirectoryPath(ctx, gitDirPath)
		if err != nil {
			return nil, err
		}
	} else {
		return nil, errors.New("unsupported Git directory marker")
	}
	commonDir := gitDir
	commonPath := gitDirPath
	if info, err := lstatAt(gitDir, "commondir"); err == nil {
		if !info.Mode().IsRegular() || info.Size() > 4096 {
			_ = gitDir.Close()
			return nil, errors.New("invalid Git common directory marker")
		}
		data, readErr := readDarwinGitMetadata(ctx, gitDir, "commondir")
		if readErr != nil {
			_ = gitDir.Close()
			return nil, readErr
		}
		commonPath = strings.TrimSpace(string(data))
		if !filepath.IsAbs(commonPath) {
			commonPath = filepath.Join(gitDirPath, commonPath)
		}
		commonPath = filepath.Clean(commonPath)
		commonDir, err = openDarwinDirectoryPath(ctx, commonPath)
		if err != nil {
			_ = gitDir.Close()
			return nil, err
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		_ = gitDir.Close()
		return nil, err
	}
	if commonDir == gitDir {
		commonDir, err = copyDescriptor(gitDir)
		if err != nil {
			_ = gitDir.Close()
			return nil, err
		}
	}
	objects, err := openAt(commonDir, "objects", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		_ = commonDir.Close()
		_ = gitDir.Close()
		return nil, err
	}
	objectPath, err := resolvedDescriptorPath(objects)
	if err != nil {
		_ = objects.Close()
		_ = commonDir.Close()
		_ = gitDir.Close()
		return nil, err
	}
	if err := s.gitObjectLayouts.validateContext(ctx, objectPath); err != nil {
		_ = objects.Close()
		_ = commonDir.Close()
		_ = gitDir.Close()
		return nil, errGitNamespaceChanged
	}
	namespace := &gitNamespace{
		gitDir: gitDir, commonDir: commonDir, objects: objects,
		gitDirPath: gitDirPath, commonPath: commonPath, objectsPath: objectPath,
		alternates: filepath.Join(objectPath, "info", "alternates"), layoutCache: &s.gitObjectLayouts,
		workTreePath: workTreePath,
	}
	if err := namespace.readDarwinAlternates(ctx); err != nil {
		namespace.close()
		return nil, err
	}
	return namespace, nil
}

func readDarwinGitMetadata(ctx context.Context, directory *os.File, name string) ([]byte, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	file, err := openAt(directory, name, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 4096 {
		return nil, errors.New("invalid Git metadata file")
	}
	return readDescriptorContext(ctx, file, 4096)
}

func openDarwinDirectoryPath(ctx context.Context, path string) (*os.File, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	path = filepath.Clean(path)
	identities, err := captureResolvedIdentitiesContext(ctx, path)
	if err != nil {
		return nil, err
	}
	return openResolvedDescriptorContext(ctx, path, true, identities)
}

func (namespace *gitNamespace) readDarwinAlternates(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	infoDir, err := openAt(namespace.objects, "info", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return errGitNamespaceChanged
	}
	defer infoDir.Close()
	info, err := lstatAt(infoDir, "alternates")
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
		return errGitNamespaceChanged
	}
	data, err := readDarwinGitMetadata(ctx, infoDir, "alternates")
	if err != nil {
		return errGitNamespaceChanged
	}
	if strings.TrimSpace(string(data)) != "" {
		return errGitAlternateObjectStore
	}
	namespace.alternateSet = true
	namespace.alternateID = identityFor(info)
	return nil
}

func (namespace *gitNamespace) prepareSnapshot(ctx context.Context, _ *os.File) error {
	namespace.snapshotMu.Lock()
	defer namespace.snapshotMu.Unlock()
	if namespace.snapshotReady {
		return namespace.snapshotErr
	}
	namespace.snapshotReady = true
	root, err := os.MkdirTemp("", "yomux-git-snapshot-")
	if err != nil {
		namespace.snapshotErr = err
		return err
	}
	namespace.snapshotRoot = root
	for _, name := range []string{"worktree", "git", "common"} {
		if err := os.Mkdir(filepath.Join(root, name), 0o700); err != nil {
			namespace.snapshotErr = err
			return err
		}
	}
	budget := newDarwinSnapshotBudget()
	copyTree := func(name string, source *os.File, options darwinSnapshotTreeOptions) error {
		destination, openErr := os.Open(filepath.Join(root, name))
		if openErr != nil {
			return openErr
		}
		copyErr := cloneDarwinTree(ctx, source, destination, budget, 0, options)
		_ = destination.Close()
		return copyErr
	}
	gitInfo, gitErr := namespace.gitDir.Stat()
	commonInfo, commonErr := namespace.commonDir.Stat()
	if gitErr != nil || commonErr != nil {
		namespace.snapshotErr = errGitNamespaceChanged
		return namespace.snapshotErr
	}
	sameGitAndCommon := sameDescriptorIdentity(identityFor(gitInfo), identityFor(commonInfo))
	if err := copyTree("git", namespace.gitDir, darwinSnapshotTreeOptions{skipGitConfig: true, skipObjects: true, skipNonDisplayMetadata: true}); err != nil {
		namespace.snapshotErr = err
		return err
	}
	if sameGitAndCommon {
		namespace.snapshotCommon = filepath.Join(root, "git")
	} else {
		if err := copyTree("common", namespace.commonDir, darwinSnapshotTreeOptions{skipGitConfig: true, skipObjects: true, skipGitSharedWorktreeData: true, skipNonDisplayMetadata: true}); err != nil {
			namespace.snapshotErr = err
			return err
		}
		namespace.snapshotCommon = filepath.Join(root, "common")
	}
	if err := os.Mkdir(filepath.Join(namespace.snapshotCommon, "objects"), 0o700); err != nil {
		namespace.snapshotErr = err
		return err
	}
	objectsSnapshot, err := os.Open(filepath.Join(namespace.snapshotCommon, "objects"))
	if err != nil {
		namespace.snapshotErr = err
		return err
	}
	objectsErr := cloneDarwinTree(ctx, namespace.objects, objectsSnapshot, budget, 0, darwinSnapshotTreeOptions{})
	_ = objectsSnapshot.Close()
	if objectsErr != nil {
		namespace.snapshotErr = objectsErr
		return objectsErr
	}
	if err := writeDarwinSnapshotConfig(ctx, namespace.commonDir, namespace.snapshotCommon, namespace.workTreePath); err != nil {
		namespace.snapshotErr = err
		return err
	}
	if err := writeDarwinSnapshotWorktreeConfig(ctx, namespace.gitDir, filepath.Join(root, "git"), namespace.workTreePath); err != nil {
		namespace.snapshotErr = err
		return err
	}
	namespace.snapshotWork = filepath.Join(root, "worktree")
	namespace.snapshotGit = filepath.Join(root, "git")
	if !sameGitAndCommon {
		if err := linkDarwinSnapshotWorktree(namespace.snapshotGit, namespace.snapshotCommon, namespace.snapshotWork); err != nil {
			namespace.snapshotErr = err
			return err
		}
	}
	return nil
}

func linkDarwinSnapshotWorktree(gitDirectory, commonDirectory, workTree string) error {
	commondirPath := filepath.Join(gitDirectory, "commondir")
	if _, err := os.Lstat(commondirPath); err != nil {
		return err
	}
	if err := os.WriteFile(commondirPath, []byte(commonDirectory+"\n"), 0o600); err != nil {
		return err
	}
	gitdirPath := filepath.Join(gitDirectory, "gitdir")
	if _, err := os.Lstat(gitdirPath); err != nil {
		return err
	}
	workTreeGitMarker := filepath.Join(workTree, ".git")
	if err := os.WriteFile(gitdirPath, []byte(workTreeGitMarker+"\n"), 0o600); err != nil {
		return err
	}
	return os.WriteFile(workTreeGitMarker, []byte("gitdir: "+gitDirectory+"\n"), 0o600)
}

func writeDarwinSnapshotConfig(ctx context.Context, commonDirectory *os.File, snapshotCommon, workTreePath string) error {
	config, err := openAt(commonDirectory, "config", syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if errors.Is(err, os.ErrNotExist) {
		return os.WriteFile(filepath.Join(snapshotCommon, "config"), nil, 0o600)
	}
	if err != nil {
		return err
	}
	defer config.Close()
	info, err := config.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 16*gitConfigMaxBytes {
		return errors.New("Git config file is invalid or too large")
	}
	command := exec.CommandContext(ctx, "git", "config", "--no-includes", "--null", "--file", platformChildFDPath(3), "--get-regexp", "^(extensions\\..*|log\\.excludedecoration|core\\.(filemode|ignorecase|excludesfile|attributesfile)|branch\\..*\\.(remote|merge)|remote\\..*\\.fetch)$")
	command.ExtraFiles = []*os.File{config}
	command.Env = gitCommandEnvironment()
	output, commandErr := command.Output()
	if commandErr != nil {
		if exitError, ok := commandErr.(*exec.ExitError); !ok || exitError.ExitCode() != 1 {
			return errors.New("could not inspect Git repository format configuration")
		}
	}
	if len(output) > 16*gitConfigMaxBytes {
		return errors.New("Git repository format configuration is too large")
	}
	extensions := map[string]string{}
	coreSettings := map[string]string{}
	coreDataSettings := map[string]string{}
	coreDataProcessed := map[string]bool{}
	var decorationExclusions []string
	branchSettings := map[string]map[string][]string{}
	remoteFetchSettings := map[string][]string{}
	for _, record := range strings.Split(strings.TrimSuffix(string(output), "\x00"), "\x00") {
		if record == "" {
			continue
		}
		configKey, value, hasValue := strings.Cut(record, "\n")
		name := strings.ToLower(configKey)
		value = strings.TrimSpace(value)
		if strings.ContainsAny(value, "\r\n\x00") {
			return errors.New("Git repository format configuration is invalid")
		}
		if name == "log.excludedecoration" {
			if value == "" || strings.Trim(value, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/*-^") != "" {
				return errors.New("Git decoration exclusion is unsupported")
			}
			decorationExclusions = append(decorationExclusions, value)
			continue
		}
		if handled, err := addDarwinGitUpstreamSetting(configKey, value, branchSettings, remoteFetchSettings); handled {
			if err != nil {
				return err
			}
			continue
		}
		if name == "core.filemode" || name == "core.ignorecase" {
			value, valid := normalizeDarwinGitBoolean(value, hasValue)
			if !valid {
				return errors.New("Git core configuration is invalid")
			}
			coreSettings[strings.TrimPrefix(name, "core.")] = value
			continue
		}
		if name == "core.excludesfile" || name == "core.attributesfile" {
			if coreDataProcessed[name] {
				continue
			}
			coreDataProcessed[name] = true
			path, found, pathErr := copyDarwinGitConfigDataFile(ctx, config, snapshotCommon, workTreePath, name)
			if pathErr != nil {
				return pathErr
			}
			if found {
				coreDataSettings[strings.TrimPrefix(name, "core.")] = path
			}
			continue
		}
		if !strings.HasPrefix(name, "extensions.") {
			return errors.New("Git repository format configuration is invalid")
		}
		name = strings.TrimPrefix(name, "extensions.")
		value = strings.ToLower(value)
		switch name {
		case "objectformat":
			if value != "sha1" && value != "sha256" {
				return errors.New("Git object format is unsupported")
			}
		case "refstorage":
			if value != "files" && value != "reftable" {
				return errors.New("Git ref storage format is unsupported")
			}
		case "preciousobjects":
			normalized, valid := normalizeDarwinGitBoolean(value, hasValue)
			if !valid {
				return errors.New("Git repository format configuration is invalid")
			}
			value = normalized
		case "worktreeconfig":
			normalized, valid := normalizeDarwinGitBoolean(value, hasValue)
			if !valid {
				return errors.New("Git repository format configuration is invalid")
			}
			value = normalized
		default:
			return errors.New("Git repository extension is unsupported: " + name)
		}
		if previous, exists := extensions[name]; exists && previous != value {
			return errors.New("Git repository format configuration is ambiguous")
		}
		extensions[name] = value
	}
	if len(extensions) == 0 && len(coreSettings) == 0 && len(coreDataSettings) == 0 && len(decorationExclusions) == 0 && len(branchSettings) == 0 && len(remoteFetchSettings) == 0 {
		return os.WriteFile(filepath.Join(snapshotCommon, "config"), nil, 0o600)
	}
	if err := os.WriteFile(filepath.Join(snapshotCommon, "config"), nil, 0o600); err != nil {
		return err
	}
	keys := make([]string, 0, len(extensions))
	for name := range extensions {
		keys = append(keys, name)
	}
	sort.Strings(keys)
	file, err := os.OpenFile(filepath.Join(snapshotCommon, "config"), os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	defer file.Close()
	var sanitized strings.Builder
	if len(extensions) > 0 || len(coreSettings) > 0 || len(coreDataSettings) > 0 {
		sanitized.WriteString("[core]\n")
		if len(extensions) > 0 {
			sanitized.WriteString("\trepositoryformatversion = 1\n")
		}
		coreKeys := make([]string, 0, len(coreSettings))
		for name := range coreSettings {
			coreKeys = append(coreKeys, name)
		}
		sort.Strings(coreKeys)
		for _, name := range coreKeys {
			appendDarwinGitConfigValue(&sanitized, name, coreSettings[name])
		}
		dataKeys := make([]string, 0, len(coreDataSettings))
		for name := range coreDataSettings {
			dataKeys = append(dataKeys, name)
		}
		sort.Strings(dataKeys)
		for _, name := range dataKeys {
			appendDarwinGitConfigValue(&sanitized, name, coreDataSettings[name])
		}
		if len(extensions) > 0 {
			sanitized.WriteString("[extensions]\n")
			for _, name := range keys {
				appendDarwinGitConfigValue(&sanitized, name, extensions[name])
			}
		}
	}
	if len(decorationExclusions) > 0 {
		sanitized.WriteString("[log]\n")
		for _, exclusion := range decorationExclusions {
			appendDarwinGitConfigValue(&sanitized, "excludeDecoration", exclusion)
		}
	}
	appendDarwinGitUpstreamConfig(&sanitized, branchSettings, remoteFetchSettings)
	if _, err := io.WriteString(file, sanitized.String()); err != nil {
		return err
	}
	return file.Sync()
}

func appendDarwinGitConfigValue(sanitized *strings.Builder, key, value string) {
	sanitized.WriteString("\t" + key + " = " + strconv.Quote(value) + "\n")
}

func safeDarwinGitConfigName(value string) bool {
	return value != "" && !strings.ContainsAny(value, "\r\n\x00")
}

func safeDarwinGitBranchName(value string) bool {
	if !safeDarwinGitConfigName(value) {
		return false
	}
	command := exec.Command("git", "check-ref-format", "--branch", value)
	command.Env = gitCommandEnvironment()
	return command.Run() == nil
}

func safeDarwinGitConfigValue(key, value string) bool {
	if value == "" || strings.ContainsAny(value, "\r\n\x00") {
		return false
	}
	switch key {
	case "remote":
		return value == "." || safeDarwinGitConfigName(value)
	case "merge":
		if !strings.HasPrefix(value, "refs/") || !safeDarwinGitConfigName(value) {
			return false
		}
		command := exec.Command("git", "check-ref-format", value)
		command.Env = gitCommandEnvironment()
		return command.Run() == nil
	case "fetch":
		return strings.Trim(value, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/*:+^-") == ""
	default:
		return false
	}
}

func appendDarwinGitUpstreamConfig(sanitized *strings.Builder, branchSettings map[string]map[string][]string, remoteFetchSettings map[string][]string) {
	branchNames := make([]string, 0, len(branchSettings))
	for name := range branchSettings {
		branchNames = append(branchNames, name)
	}
	sort.Strings(branchNames)
	for _, name := range branchNames {
		sanitized.WriteString("[branch " + strconv.Quote(name) + "]\n")
		for _, key := range []string{"merge", "remote"} {
			for _, value := range branchSettings[name][key] {
				appendDarwinGitConfigValue(sanitized, key, value)
			}
		}
	}
	remoteNames := make([]string, 0, len(remoteFetchSettings))
	for name := range remoteFetchSettings {
		remoteNames = append(remoteNames, name)
	}
	sort.Strings(remoteNames)
	for _, name := range remoteNames {
		sanitized.WriteString("[remote " + strconv.Quote(name) + "]\n")
		for _, value := range remoteFetchSettings[name] {
			appendDarwinGitConfigValue(sanitized, "fetch", value)
		}
	}
}

func addDarwinGitUpstreamSetting(configKey, value string, branchSettings map[string]map[string][]string, remoteFetchSettings map[string][]string) (bool, error) {
	name := strings.ToLower(configKey)
	if strings.HasPrefix(name, "branch.") {
		setting := ""
		for _, suffix := range []string{".remote", ".merge"} {
			if strings.HasSuffix(name, suffix) {
				setting = suffix[1:]
				break
			}
		}
		if setting == "" || len(configKey) <= len("branch.")+len(setting)+1 {
			return true, errors.New("Git upstream configuration is unsupported")
		}
		branchConfigKey := configKey[:len(configKey)-len(setting)-1]
		branch := branchConfigKey[len("branch."):]
		if !strings.EqualFold(branchConfigKey[:len("branch.")], "branch.") || !safeDarwinGitBranchName(branch) || !safeDarwinGitConfigValue(setting, value) {
			return true, errors.New("Git upstream configuration is unsupported")
		}
		if branchSettings[branch] == nil {
			branchSettings[branch] = map[string][]string{}
		}
		branchSettings[branch][setting] = append(branchSettings[branch][setting], value)
		return true, nil
	}
	if strings.HasPrefix(name, "remote.") {
		if !strings.HasSuffix(name, ".fetch") || len(configKey) <= len("remote.")+len(".fetch") {
			return true, errors.New("Git remote fetch configuration is unsupported")
		}
		remoteConfigKey := configKey[:len(configKey)-len(".fetch")]
		remote := remoteConfigKey[len("remote."):]
		if !strings.EqualFold(remoteConfigKey[:len("remote.")], "remote.") || !safeDarwinGitConfigName(remote) || !safeDarwinGitConfigValue("fetch", value) {
			return true, errors.New("Git remote fetch configuration is unsupported")
		}
		remoteFetchSettings[remote] = append(remoteFetchSettings[remote], value)
		return true, nil
	}
	return false, nil
}

func writeDarwinSnapshotWorktreeConfig(ctx context.Context, gitDirectory *os.File, snapshotGit, workTreePath string) error {
	config, err := openAt(gitDirectory, "config.worktree", syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	defer config.Close()
	info, err := config.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 16*gitConfigMaxBytes {
		return errors.New("Git worktree config file is invalid or too large")
	}
	command := exec.CommandContext(ctx, "git", "config", "--no-includes", "--null", "--file", platformChildFDPath(3), "--get-regexp", "^(core\\.(filemode|ignorecase|excludesfile|attributesfile)|branch\\..*\\.(remote|merge)|remote\\..*\\.fetch)$")
	command.ExtraFiles = []*os.File{config}
	command.Env = gitCommandEnvironment()
	output, commandErr := command.Output()
	if commandErr != nil {
		if exitError, ok := commandErr.(*exec.ExitError); !ok || exitError.ExitCode() != 1 {
			return errors.New("could not inspect Git worktree configuration")
		}
	}
	if len(output) > 16*gitConfigMaxBytes {
		return errors.New("Git worktree configuration is too large")
	}
	var sanitized strings.Builder
	coreDataSettings := map[string]string{}
	coreDataProcessed := map[string]bool{}
	branchSettings := map[string]map[string][]string{}
	remoteFetchSettings := map[string][]string{}
	for _, record := range strings.Split(strings.TrimSuffix(string(output), "\x00"), "\x00") {
		if record == "" {
			continue
		}
		configKey, configValue, hasValue := strings.Cut(record, "\n")
		name := strings.ToLower(configKey)
		configValue = strings.TrimSpace(configValue)
		if name == "core.filemode" || name == "core.ignorecase" {
			value, valid := normalizeDarwinGitBoolean(configValue, hasValue)
			if !valid {
				return errors.New("Git worktree boolean configuration is invalid")
			}
			if sanitized.Len() == 0 {
				sanitized.WriteString("[core]\n")
			}
			sanitized.WriteString("\t" + strings.TrimPrefix(name, "core.") + " = " + value + "\n")
			continue
		}
		if name == "core.excludesfile" || name == "core.attributesfile" {
			if coreDataProcessed[name] {
				continue
			}
			coreDataProcessed[name] = true
			path, found, pathErr := copyDarwinGitConfigDataFile(ctx, config, snapshotGit, workTreePath, name)
			if pathErr != nil {
				return pathErr
			}
			if found {
				coreDataSettings[strings.TrimPrefix(name, "core.")] = path
			}
			continue
		}
		if handled, err := addDarwinGitUpstreamSetting(configKey, configValue, branchSettings, remoteFetchSettings); handled {
			if err != nil {
				return err
			}
			continue
		}
		return errors.New("Git worktree configuration is unsupported")
	}
	if sanitized.Len() == 0 && len(coreDataSettings) == 0 && len(branchSettings) == 0 && len(remoteFetchSettings) == 0 {
		return nil
	}
	if len(coreDataSettings) > 0 {
		if sanitized.Len() == 0 {
			sanitized.WriteString("[core]\n")
		}
		keys := make([]string, 0, len(coreDataSettings))
		for name := range coreDataSettings {
			keys = append(keys, name)
		}
		sort.Strings(keys)
		for _, name := range keys {
			sanitized.WriteString("\t" + name + " = " + strconv.Quote(coreDataSettings[name]) + "\n")
		}
	}
	appendDarwinGitUpstreamConfig(&sanitized, branchSettings, remoteFetchSettings)
	return os.WriteFile(filepath.Join(snapshotGit, "config.worktree"), []byte(sanitized.String()), 0o600)
}

func copyDarwinGitConfigDataFile(ctx context.Context, config *os.File, destinationDirectory, workTreePath, key string) (string, bool, error) {
	if _, err := config.Seek(0, io.SeekStart); err != nil {
		return "", false, errors.New("could not reread Git config data path")
	}
	command := exec.CommandContext(ctx, "git", "config", "--no-includes", "--null", "--path", "--file", platformChildFDPath(3), "--get", key)
	command.ExtraFiles = []*os.File{config}
	command.Env = gitCommandEnvironment()
	output, err := command.Output()
	if err != nil {
		if exitError, ok := err.(*exec.ExitError); ok && exitError.ExitCode() == 1 {
			return "", false, nil
		}
		return "", false, errors.New("could not resolve Git config data path")
	}
	if len(output) < 2 || len(output) > 4096 || output[len(output)-1] != 0 || strings.IndexByte(string(output[:len(output)-1]), 0) >= 0 {
		return "", false, errors.New("Git config data path is invalid")
	}
	path := string(output[:len(output)-1])
	if !filepath.IsAbs(path) {
		path = filepath.Join(workTreePath, path)
	}
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return "", false, nil
		}
		return "", false, errors.New("Git config data file is unavailable")
	}
	identities, err := captureResolvedIdentitiesContext(ctx, resolved)
	if err != nil {
		return "", false, errors.New("Git config data file is unavailable")
	}
	file, err := openResolvedDescriptorContext(ctx, resolved, false, identities)
	if err != nil {
		return "", false, errors.New("Git config data file is unavailable")
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > darwinGitConfigDataMaxBytes {
		return "", false, errors.New("Git config data file is invalid or too large")
	}
	data, err := io.ReadAll(io.LimitReader(file, darwinGitConfigDataMaxBytes+1))
	if err != nil || len(data) > darwinGitConfigDataMaxBytes {
		return "", false, errors.New("Git config data file is invalid or too large")
	}
	filename := ".yomux-git-attributes"
	if key == "core.excludesfile" {
		filename = ".yomux-git-excludes"
	}
	if err := os.WriteFile(filepath.Join(destinationDirectory, filename), data, 0o600); err != nil {
		return "", false, err
	}
	return filepath.Join(destinationDirectory, filename), true, nil
}

func removeDarwinSnapshot(root string) {
	var directories []string
	_ = filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err == nil && entry.IsDir() {
			directories = append(directories, path)
		}
		return nil
	})
	for index := len(directories) - 1; index >= 0; index-- {
		_ = os.Chmod(directories[index], 0o700)
	}
	_ = os.RemoveAll(root)
}

type darwinSnapshotBudget struct {
	mu          sync.Mutex
	entries     int
	bytes       int64
	clones      chan struct{}
	active      int
	maxLive     int
	beforeClone func()
}

func newDarwinSnapshotBudget() *darwinSnapshotBudget {
	return &darwinSnapshotBudget{clones: make(chan struct{}, darwinGitSnapshotCloneWorkers)}
}

func (budget *darwinSnapshotBudget) beginClone() {
	budget.mu.Lock()
	budget.active++
	if budget.active > budget.maxLive {
		budget.maxLive = budget.active
	}
	budget.mu.Unlock()
	if budget.beforeClone != nil {
		budget.beforeClone()
	}
}

func (budget *darwinSnapshotBudget) endClone() {
	budget.mu.Lock()
	budget.active--
	budget.mu.Unlock()
}

type darwinSnapshotTreeOptions struct {
	skipGitConfig             bool
	skipObjects               bool
	skipGitSharedWorktreeData bool
	// Read-only diff and history commands never execute hooks or read reflogs.
	skipNonDisplayMetadata bool
}

func cloneDarwinTree(ctx context.Context, source, destination *os.File, budget *darwinSnapshotBudget, depth int, options darwinSnapshotTreeOptions) error {
	if depth > 256 {
		return errors.New("Git snapshot directory depth exceeds limit")
	}
	reader, err := openAt(source, ".", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return err
	}
	defer reader.Close()
	entries, err := reader.ReadDir(-1)
	if err != nil {
		return err
	}
	var fileClones sync.WaitGroup
	var cloneErrorMu sync.Mutex
	var cloneError error
	defer fileClones.Wait()
	for _, entry := range entries {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		if (options.skipGitConfig && (entry.Name() == "config" || entry.Name() == "config.worktree")) || (options.skipObjects && entry.Name() == "objects") || (depth == 0 && options.skipGitSharedWorktreeData && (entry.Name() == "worktrees" || entry.Name() == "index")) || (depth == 0 && options.skipNonDisplayMetadata && (entry.Name() == "hooks" || entry.Name() == "logs")) {
			continue
		}
		budget.entries++
		if budget.entries > darwinGitSnapshotMaxEntries {
			return errors.New("Git snapshot entry limit exceeded")
		}
		info, err := lstatAt(reader, entry.Name())
		if err != nil {
			return err
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return errors.New("Git metadata snapshot contains a symlink")
		}
		if info.IsDir() {
			if err := mkdirAt(destination, entry.Name(), 0o700); err != nil {
				return err
			}
			child, err := openAt(reader, entry.Name(), syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
			if err != nil {
				return err
			}
			childInfo, statErr := child.Stat()
			if statErr != nil || !sameDescriptorIdentity(identityFor(info), identityFor(childInfo)) {
				_ = child.Close()
				return errChangedOnDisk
			}
			childDestination, openErr := openAt(destination, entry.Name(), syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
			if openErr == nil {
				openErr = cloneDarwinTree(ctx, child, childDestination, budget, depth+1, options)
				_ = childDestination.Close()
			}
			_ = child.Close()
			if openErr != nil {
				return openErr
			}
			continue
		}
		if !info.Mode().IsRegular() {
			return errors.New("Git snapshot contains an unsupported special file")
		}
		select {
		case budget.clones <- struct{}{}:
		case <-ctx.Done():
			return ctx.Err()
		}
		fileClones.Add(1)
		go func(name string, info os.FileInfo) {
			defer fileClones.Done()
			defer func() { <-budget.clones }()
			budget.beginClone()
			defer budget.endClone()
			if err := cloneDarwinFile(ctx, reader, destination, name, info, budget); err != nil {
				cloneErrorMu.Lock()
				if cloneError == nil {
					cloneError = err
				}
				cloneErrorMu.Unlock()
			}
		}(entry.Name(), info)
	}
	fileClones.Wait()
	cloneErrorMu.Lock()
	defer cloneErrorMu.Unlock()
	return cloneError
}

func normalizeDarwinGitBoolean(value string, hasValue bool) (string, bool) {
	if !hasValue {
		return "true", true
	}
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "true", "yes", "on":
		return "true", true
	case "", "false", "no", "off":
		return "false", true
	}
	number, err := strconv.ParseInt(strings.TrimSpace(value), 10, 64)
	if err != nil {
		return "", false
	}
	if number != 0 {
		return "true", true
	}
	return "false", true
}

func cloneDarwinFile(ctx context.Context, sourceDirectory, destinationDirectory *os.File, name string, before os.FileInfo, budget *darwinSnapshotBudget) error {
	source, err := openAt(sourceDirectory, name, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return err
	}
	defer source.Close()
	sourceInfo, err := source.Stat()
	if err != nil || !sameDescriptorIdentity(identityFor(before), identityFor(sourceInfo)) {
		return errChangedOnDisk
	}
	if cloneErr := unix.Fclonefileat(int(source.Fd()), int(destinationDirectory.Fd()), name, 0); cloneErr == nil {
		destination, openErr := openAt(destinationDirectory, name, syscall.O_RDONLY|syscall.O_NOFOLLOW, 0)
		if openErr != nil {
			return openErr
		}
		cloneInfo, statErr := destination.Stat()
		_ = destination.Close()
		afterInfo, sourceErr := source.Stat()
		if statErr != nil || sourceErr != nil || cloneInfo.Size() != sourceInfo.Size() || !cloneInfo.ModTime().Equal(sourceInfo.ModTime()) || afterInfo.Size() != sourceInfo.Size() || !afterInfo.ModTime().Equal(sourceInfo.ModTime()) {
			_ = unlinkAt(destinationDirectory, name, false)
			return errChangedOnDisk
		}
		return nil
	} else if !errors.Is(cloneErr, syscall.ENOTSUP) && !errors.Is(cloneErr, syscall.EOPNOTSUPP) && !errors.Is(cloneErr, syscall.EXDEV) && !errors.Is(cloneErr, syscall.EINVAL) && !errors.Is(cloneErr, syscall.ENOSYS) && !errors.Is(cloneErr, syscall.EPERM) {
		return cloneErr
	}
	if err := budget.addBytes(sourceInfo.Size()); err != nil {
		return err
	}
	destination, err := openAt(destinationDirectory, name, syscall.O_WRONLY|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW, 0o600)
	if err != nil {
		return err
	}
	copyErr := copyDarwinSnapshotFile(ctx, destination, source)
	if copyErr == nil {
		copyErr = destination.Chmod(sourceInfo.Mode().Perm())
	}
	closeErr := destination.Close()
	if copyErr != nil || closeErr != nil {
		_ = unlinkAt(destinationDirectory, name, false)
		if copyErr != nil {
			return copyErr
		}
		return closeErr
	}
	afterInfo, err := source.Stat()
	if err != nil || afterInfo.Size() != sourceInfo.Size() || !afterInfo.ModTime().Equal(sourceInfo.ModTime()) {
		_ = unlinkAt(destinationDirectory, name, false)
		return errChangedOnDisk
	}
	return nil
}

func (budget *darwinSnapshotBudget) addBytes(count int64) error {
	budget.mu.Lock()
	defer budget.mu.Unlock()
	if count < 0 || count > darwinGitSnapshotMaxBytes-budget.bytes {
		return errors.New("Git snapshot byte limit exceeded")
	}
	budget.bytes += count
	return nil
}

func copyDarwinSnapshotFile(ctx context.Context, destination, source *os.File) error {
	buffer := make([]byte, 128*1024)
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		count, readErr := source.Read(buffer)
		if count > 0 {
			written := 0
			for written < count {
				writeCount, writeErr := destination.Write(buffer[written:count])
				written += writeCount
				if writeErr != nil {
					return writeErr
				}
				if writeCount == 0 {
					return io.ErrShortWrite
				}
			}
		}
		if errors.Is(readErr, io.EOF) {
			return nil
		}
		if readErr != nil {
			return readErr
		}
	}
}
