// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bytes"
	"context"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"unicode/utf8"

	"github.com/keivenchang/yomux/internal/cache"
)

const (
	gitHistoryDefaultLimit     = 200
	gitHistoryMaxLimit         = 200
	gitHistoryMaxOutput        = 4 * 1024 * 1024
	gitHistoryMaxPayload       = 384 * 1024
	gitHistoryMaxText          = 8 * 1024
	gitHistoryMaxCursor        = 2048
	gitHistoryMaxOffset        = 1_000_000
	gitCommitMaxFiles          = 1000
	gitCommitMaxMessage        = 64 * 1024
	gitCommitMetadataOverhead  = 64 * 1024
	gitCommitMaxOutput         = 8 * 1024 * 1024
	gitCommitProductMaxOutput  = gitCommitMaxOutput + gitCommitMaxMessage + gitCommitMetadataOverhead
	gitCommitMaxPayload        = 384 * 1024
	gitConfigMaxBytes          = 1024 * 1024
	gitHistoryMaxStatusEntries = 1000
)

func MaxGitHistoryLimit() int { return gitHistoryMaxLimit }

func MaxGitHistoryOffset() int { return gitHistoryMaxOffset }

func MaxGitHistoryScheduledBatches() int { return gitHistoryMaxScheduledBatches }

const maxGitProductCacheEntries = 128
const maxGitHistoryStatsCacheEntries = 1000

const gitHistoryMaxScheduledBatches = 16

var gitObjectIDPattern = regexp.MustCompile(`^(?:[0-9a-f]{40}|[0-9a-f]{64})$`)
var gitStatusPattern = regexp.MustCompile(`^[A-Z][0-9]{0,3}$`)
var gitRemoteSectionPattern = regexp.MustCompile(`^\[\s*remote\s+"([^"\\]+)"\s*\]$`)
var gitRemoteSCPPattern = regexp.MustCompile(`^(?:[^@/:]+@)?([A-Za-z0-9.-]+):(.+)$`)
var gitRemoteHostPattern = regexp.MustCompile(`^[a-z0-9.-]+$`)
var gitRemotePathSegmentPattern = regexp.MustCompile(`^[A-Za-z0-9._~-]+$`)

type gitHistoryScope struct {
	path            string
	repo            string
	identity        string
	relative        string
	branch          string
	shallowBoundary string
	hostedRemote    map[string]string
	directory       *os.File
	namespace       *gitNamespace
}

type gitHistoryScopeOptions struct {
	readHostedRemote bool
}

func (scope *gitHistoryScope) close() {
	if scope != nil && scope.directory != nil {
		_ = scope.directory.Close()
	}
	if scope != nil {
		scope.namespace.close()
	}
}

type gitHistoryCursor struct {
	Repo     string `json:"repo"`
	Identity string `json:"identity"`
	Relative string `json:"relative"`
	Head     string `json:"head"`
	Offset   int    `json:"offset"`
}

func (s *Service) openGitHistoryScope(raw string) (*gitHistoryScope, *Error) {
	return s.openGitHistoryScopeContext(context.Background(), raw)
}

func (s *Service) openGitHistoryScopeContext(ctx context.Context, raw string) (*gitHistoryScope, *Error) {
	return s.openGitHistoryScopeWithOptions(ctx, raw, gitHistoryScopeOptions{
		readHostedRemote: true,
	})
}

func (s *Service) openGitHistoryScopeWithOptions(ctx context.Context, raw string, options gitHistoryScopeOptions) (*gitHistoryScope, *Error) {
	if ctx.Err() != nil {
		return nil, cancelled(raw)
	}
	// Authorize with allowMissing first so the parent is resolved before the descriptor open. The
	// subsequent descriptor open keeps the normal 404 behavior for a missing path.
	if _, _, finderErr := s.authorize(raw, true); finderErr != nil {
		return nil, finderErr
	}
	directory, requested, resolved, finderErr := s.open(raw, true)
	if finderErr != nil {
		if finderErr.Message == "path is not a directory" {
			finderErr.MessageKey = "fs.error.gitHistoryDirectoryRequired"
			finderErr.Message = "Git history requires a directory"
		}
		return nil, finderErr
	}
	var root string
	var err error
	var namespace *gitNamespace
	if runtime.GOOS == "darwin" {
		namespace, finderErr = s.openGitNamespaceContext(ctx, directory, resolved, requested)
		if finderErr == nil {
			root = namespace.workTreePath
		}
	} else {
		rootBytes, _, gitErr := runGitBounded(ctx, directory, nil, []string{"rev-parse", "--show-toplevel"}, 4096)
		if gitErr != nil {
			_ = directory.Close()
			if ctx.Err() != nil {
				return nil, cancelled(requested)
			}
			return nil, &Error{Status: http.StatusBadRequest, Code: "not_git_repo", MessageKey: "fs.error.notGitRepo", Path: requested, Message: "path is not in a Git repository"}
		}
		root, err = filepath.Abs(filepath.Clean(strings.TrimSpace(string(rootBytes))))
		if err != nil {
			_ = directory.Close()
			return nil, filesystemError(requested, err)
		}
		namespace, finderErr = s.openGitNamespaceContext(ctx, directory, resolved, requested)
	}
	if finderErr != nil {
		_ = directory.Close()
		return nil, finderErr
	}
	var hostedRemote map[string]string
	if options.readHostedRemote {
		hostedRemote, finderErr = readHostedGitRemote(ctx, namespace, requested)
		if finderErr != nil {
			namespace.close()
			_ = directory.Close()
			return nil, finderErr
		}
	} else if finderErr = validateGitConfigFile(ctx, namespace, requested); finderErr != nil {
		namespace.close()
		_ = directory.Close()
		return nil, finderErr
	}
	branch, finderErr := readCurrentGitBranch(ctx, namespace, requested)
	if finderErr != nil {
		namespace.close()
		_ = directory.Close()
		return nil, finderErr
	}
	rootDirectory, rootErr := openGitRootDescriptor(ctx, root, requested)
	if rootErr != nil {
		namespace.close()
		_ = directory.Close()
		return nil, rootErr
	}
	relative, err := relativePathWithinGitRoot(root, resolved, directory, rootDirectory)
	if err != nil || filepath.IsAbs(relative) || relative == ".." || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
		_ = rootDirectory.Close()
		namespace.close()
		_ = directory.Close()
		return nil, forbidden(requested, "fs.error.outsideRoots", "path is outside the Git repository")
	}
	if relative == "." {
		relative = ""
	}
	relative = filepath.ToSlash(relative)
	if !utf8.ValidString(relative) {
		_ = rootDirectory.Close()
		namespace.close()
		_ = directory.Close()
		return nil, &Error{Status: http.StatusUnprocessableEntity, Code: "git_path_encoding", MessageKey: "fs.error.gitPathEncoding", Path: requested, Message: errGitPathEncoding.Error()}
	}
	_ = directory.Close()
	return &gitHistoryScope{path: requested, repo: displayGitRootPath(requested, relative, root), identity: namespace.identity(), relative: relative, branch: branch, hostedRemote: hostedRemote, directory: rootDirectory, namespace: namespace}, nil
}

func gitRemoteURLs(config []byte) [][2]string {
	currentRemote := ""
	remotes := make([][2]string, 0)
	for _, rawLine := range strings.Split(string(config), "\n") {
		line := strings.TrimSpace(rawLine)
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
			continue
		}
		if strings.HasPrefix(line, "[") {
			match := gitRemoteSectionPattern.FindStringSubmatch(line)
			if match == nil {
				currentRemote = ""
			} else {
				currentRemote = match[1]
			}
			continue
		}
		if currentRemote == "" {
			continue
		}
		key, value, found := strings.Cut(line, "=")
		if !found {
			fields := strings.SplitN(line, " ", 2)
			if len(fields) != 2 {
				continue
			}
			key, value = fields[0], fields[1]
		}
		if strings.ToLower(strings.TrimSpace(key)) != "url" {
			continue
		}
		value = strings.TrimSpace(value)
		if len(value) >= 2 && value[0] == value[len(value)-1] && value[0] == '"' && !strings.Contains(value, "\\") {
			value = value[1 : len(value)-1]
		}
		remotes = append(remotes, [2]string{currentRemote, value})
	}
	return remotes
}

func hostedGitRemoteURL(rawURL string) map[string]string {
	if rawURL == "" || strings.ContainsAny(rawURL, "\x00\r\n") {
		return nil
	}
	host, repositoryPath, port := "", "", ""
	if !strings.Contains(rawURL, "://") {
		match := gitRemoteSCPPattern.FindStringSubmatch(rawURL)
		if match == nil {
			return nil
		}
		host, repositoryPath = match[1], match[2]
	} else {
		parsed, err := url.Parse(rawURL)
		if err != nil || (parsed.Scheme != "https" && parsed.Scheme != "ssh" && parsed.Scheme != "git") || parsed.RawQuery != "" || parsed.Fragment != "" {
			return nil
		}
		if parsed.Scheme == "https" && parsed.User != nil && (parsed.User.Username() != "" || parsed.User.String() != parsed.User.Username()) {
			return nil
		}
		host = parsed.Hostname()
		repositoryPath = parsed.EscapedPath()
		if strings.Contains(repositoryPath, "%") {
			return nil
		}
		if parsed.Scheme == "https" {
			port = parsed.Port()
		}
	}
	host = strings.ToLower(strings.TrimSuffix(host, "."))
	if !gitRemoteHostPattern.MatchString(host) {
		return nil
	}
	provider := ""
	if host == "github.com" || strings.HasPrefix(host, "github.") || strings.Contains(host, ".github.") {
		provider = "github"
	} else if host == "gitlab.com" || strings.HasPrefix(host, "gitlab.") || strings.Contains(host, ".gitlab.") {
		provider = "gitlab"
	} else {
		return nil
	}
	path := strings.Trim(repositoryPath, "/")
	if strings.HasSuffix(path, ".git") {
		path = strings.TrimSuffix(path, ".git")
	}
	segments := strings.Split(path, "/")
	if len(segments) < 2 {
		return nil
	}
	for _, segment := range segments {
		if segment == "." || segment == ".." || !gitRemotePathSegmentPattern.MatchString(segment) {
			return nil
		}
	}
	authority := host
	if port != "" {
		authority += ":" + port
	}
	return map[string]string{"provider": provider, "base_url": "https://" + authority + "/" + strings.Join(segments, "/")}
}

func hostedGitRemote(config []byte) map[string]string {
	remotes := gitRemoteURLs(config)
	var selected map[string]string
	selectedPriority, selectedIndex := 3, len(remotes)
	for index, remote := range remotes {
		parsed := hostedGitRemoteURL(remote[1])
		if parsed == nil {
			continue
		}
		priority := 2
		if remote[0] == "origin" {
			priority = 0
		} else if remote[0] == "upstream" {
			priority = 1
		}
		if priority < selectedPriority || (priority == selectedPriority && index < selectedIndex) {
			selected, selectedPriority, selectedIndex = parsed, priority, index
		}
	}
	return selected
}

func openHostedGitConfig(ctx context.Context, namespace *gitNamespace, requested string) (*os.File, os.FileInfo, *Error) {
	if ctx.Err() != nil {
		return nil, nil, cancelled(requested)
	}
	if namespace == nil || namespace.validateContext(ctx) != nil {
		if ctx.Err() != nil {
			return nil, nil, cancelled(requested)
		}
		return nil, nil, gitNamespaceChanged(requested)
	}
	config, err := openAt(namespace.commonDir, "config", syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil, nil
	}
	if err != nil {
		return nil, nil, &Error{Status: http.StatusConflict, Code: "git_repository_changed", MessageKey: "fs.error.gitRepositoryChanged", Path: requested, Message: "Git configuration is unavailable"}
	}
	if ctx.Err() != nil {
		_ = config.Close()
		return nil, nil, cancelled(requested)
	}
	before, err := config.Stat()
	if err != nil || !before.Mode().IsRegular() {
		_ = config.Close()
		return nil, nil, &Error{Status: http.StatusConflict, Code: "git_repository_changed", MessageKey: "fs.error.gitRepositoryChanged", Path: requested, Message: "Git configuration is not a regular file"}
	}
	return config, before, nil
}

func validateGitConfigFile(ctx context.Context, namespace *gitNamespace, requested string) *Error {
	config, before, finderErr := openHostedGitConfig(ctx, namespace, requested)
	if finderErr != nil || config == nil {
		return finderErr
	}
	defer config.Close()
	after, err := config.Stat()
	if err != nil || identityFor(before) != identityFor(after) || namespace.validateContext(ctx) != nil {
		if ctx.Err() != nil {
			return cancelled(requested)
		}
		return &Error{Status: http.StatusConflict, Code: "git_repository_changed", MessageKey: "fs.error.gitRepositoryChanged", Path: requested, Message: "Git configuration changed during history read"}
	}
	return nil
}

func readHostedGitRemote(ctx context.Context, namespace *gitNamespace, requested string) (map[string]string, *Error) {
	config, before, finderErr := openHostedGitConfig(ctx, namespace, requested)
	if finderErr != nil || config == nil {
		return nil, finderErr
	}
	defer config.Close()
	data, err := io.ReadAll(io.LimitReader(config, gitConfigMaxBytes+1))
	if err != nil {
		if ctx.Err() != nil {
			return nil, cancelled(requested)
		}
		return nil, &Error{Status: http.StatusConflict, Code: "git_repository_changed", MessageKey: "fs.error.gitRepositoryChanged", Path: requested, Message: "Git configuration changed during history read"}
	}
	if ctx.Err() != nil {
		return nil, cancelled(requested)
	}
	if len(data) > gitConfigMaxBytes {
		return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitHistoryTooLarge", Path: requested, Message: "Git configuration exceeds the metadata limit"}
	}
	after, err := config.Stat()
	if err != nil || identityFor(before) != identityFor(after) || namespace.validateContext(ctx) != nil {
		if ctx.Err() != nil {
			return nil, cancelled(requested)
		}
		return nil, &Error{Status: http.StatusConflict, Code: "git_repository_changed", MessageKey: "fs.error.gitRepositoryChanged", Path: requested, Message: "Git configuration changed during history read"}
	}
	return hostedGitRemote(data), nil
}

func readCurrentGitBranch(ctx context.Context, namespace *gitNamespace, requested string) (string, *Error) {
	if ctx.Err() != nil {
		return "", cancelled(requested)
	}
	if namespace == nil {
		return "", gitNamespaceChanged(requested)
	}
	if err := namespace.validateContext(ctx); err != nil {
		if ctx.Err() != nil {
			return "", cancelled(requested)
		}
		return "", gitNamespaceChanged(requested)
	}
	head, err := openAt(namespace.gitDir, "HEAD", syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return "", gitNamespaceChanged(requested)
	}
	defer head.Close()
	before, err := head.Stat()
	if err != nil || !before.Mode().IsRegular() {
		return "", gitNamespaceChanged(requested)
	}
	data, err := io.ReadAll(io.LimitReader(head, 4097))
	if err != nil || len(data) > 4096 {
		if ctx.Err() != nil {
			return "", cancelled(requested)
		}
		return "", gitNamespaceChanged(requested)
	}
	if ctx.Err() != nil {
		return "", cancelled(requested)
	}
	after, err := head.Stat()
	if err != nil || identityFor(before) != identityFor(after) {
		return "", gitNamespaceChanged(requested)
	}
	record := strings.TrimSuffix(string(data), "\n")
	record = strings.TrimSuffix(record, "\r")
	if strings.HasPrefix(record, "ref: refs/heads/") {
		branch := strings.TrimPrefix(record, "ref: refs/heads/")
		if branch != "" && !strings.ContainsAny(branch, "\x00\r\n") {
			if err := namespace.validateContext(ctx); err != nil {
				if ctx.Err() != nil {
					return "", cancelled(requested)
				}
				return "", gitNamespaceChanged(requested)
			}
			return branch, nil
		}
	}
	if gitObjectIDPattern.MatchString(record) {
		if err := namespace.validateContext(ctx); err != nil {
			if ctx.Err() != nil {
				return "", cancelled(requested)
			}
			return "", gitNamespaceChanged(requested)
		}
		return "", nil
	}
	return "", gitNamespaceChanged(requested)
}

func encodeGitHistoryCursor(cursor gitHistoryCursor) (string, error) {
	data, err := json.Marshal(cursor)
	if err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(data), nil
}

func decodeGitHistoryCursor(raw string) (gitHistoryCursor, error) {
	if len(raw) > gitHistoryMaxCursor {
		return gitHistoryCursor{}, errors.New("Git history cursor is too large")
	}
	data, err := base64.RawURLEncoding.DecodeString(raw)
	if err != nil {
		return gitHistoryCursor{}, err
	}
	var cursor gitHistoryCursor
	if err := json.Unmarshal(data, &cursor); err != nil || cursor.Repo == "" || cursor.Identity == "" || cursor.Head == "" || cursor.Offset < 0 || cursor.Offset > gitHistoryMaxOffset {
		return gitHistoryCursor{}, errors.New("malformed Git history cursor")
	}
	if !gitObjectIDPattern.MatchString(cursor.Head) {
		return gitHistoryCursor{}, errors.New("malformed Git history cursor head")
	}
	return cursor, nil
}

func gitHistoryLimit(raw string) (int, error) {
	if strings.TrimSpace(raw) == "" {
		return gitHistoryDefaultLimit, nil
	}
	value, err := strconv.Atoi(strings.TrimSpace(raw))
	if err != nil {
		return 0, errors.New("limit must be a positive integer")
	}
	if value < 1 {
		return 1, nil
	}
	if value > gitHistoryMaxLimit {
		value = gitHistoryMaxLimit
	}
	return value, nil
}

func gitHistoryHead(ctx context.Context, scope *gitHistoryScope) (string, *Error) {
	output, stderr, err := runGitMetadataBounded(ctx, scope.directory, nil, []string{"rev-parse", "--verify", "--quiet", "HEAD"}, 512, scope.namespace)
	if err == nil {
		return strings.TrimSpace(string(output)), nil
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return "", cancelled(scope.path)
	}
	if errors.Is(err, errGitNamespaceChanged) {
		return "", gitNamespaceChanged(scope.path)
	}
	if len(output) == 0 && len(stderr) == 0 {
		return "", nil
	}
	return "", &Error{Status: http.StatusInternalServerError, Code: "git_history_failed", MessageKey: "fs.error.gitHistoryFailed", Path: scope.path, Retryable: true, Message: "Git history failed"}
}

func gitVerifiedHistoryHead(ctx context.Context, scope *gitHistoryScope, messageKey string) (string, *Error) {
	output, stderr, err := runGitMetadataBounded(ctx, scope.directory, nil, []string{"rev-parse", "--verify", "--quiet", "HEAD^{commit}"}, 512, scope.namespace)
	if err == nil {
		return strings.TrimSpace(string(output)), nil
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return "", cancelled(scope.path)
	}
	if errors.Is(err, errGitNamespaceChanged) {
		return "", gitNamespaceChanged(scope.path)
	}
	if gitExitStatus(err, 1) && len(output) == 0 && len(stderr) == 0 {
		// An empty repository and a dangling HEAD both fail this peel quietly. Only the
		// uncommon failure path needs the unpeeled ref lookup to distinguish them.
		head, headErr := gitHistoryHead(ctx, scope)
		if headErr != nil {
			return "", headErr
		}
		if head == "" {
			return "", nil
		}
		return "", &Error{Status: http.StatusInternalServerError, Code: "git_history_failed", MessageKey: messageKey, Path: scope.path, Retryable: true, Message: "Git HEAD object lookup failed"}
	}
	message := "Git HEAD object lookup failed"
	if strings.TrimSpace(string(stderr)) != "" {
		message = strings.TrimSpace(string(stderr))
	}
	return "", &Error{Status: http.StatusInternalServerError, Code: "git_history_failed", MessageKey: messageKey, Path: scope.path, Retryable: true, Message: message}
}

// A Git snapshot is request-local so a later request cannot reuse stale refs or repository identity.
type gitSnapshot struct {
	scope        *gitHistoryScope
	head         string
	refSignature string
}

func (s *Service) captureGitSnapshot(ctx context.Context, scope *gitHistoryScope, headObjectMessageKey string) (gitSnapshot, *Error) {
	snapshot := gitSnapshot{scope: scope}
	head, finderErr := gitVerifiedHistoryHead(ctx, scope, headObjectMessageKey)
	if finderErr != nil {
		return gitSnapshot{}, finderErr
	}
	snapshot.head = head
	return snapshot, nil
}

func (s *Service) prepareGitHistoryNamespace(ctx context.Context, scope *gitHistoryScope) *Error {
	if err := scope.namespace.prepareSnapshot(ctx, scope.directory); err != nil {
		return gitHistoryReadError(scope.path, err)
	}
	return nil
}

func (s *Service) validateFrozenGitHistoryHead(ctx context.Context, snapshot gitSnapshot, frozenHead string) *Error {
	if snapshot.scope == nil || frozenHead == snapshot.head {
		return nil
	}
	found, ancestor, finderErr := s.gitCommitReachability(ctx, snapshot.scope, frozenHead, snapshot.head)
	if finderErr != nil {
		return finderErr
	}
	if !found || !ancestor {
		return &Error{Status: http.StatusConflict, Code: "git_history_stale", MessageKey: "fs.error.gitHistoryStale", Path: snapshot.scope.path, Message: "Git history snapshot is stale"}
	}
	return nil
}

func (s *Service) validateGitHistoryCursor(ctx context.Context, snapshot gitSnapshot, raw string) (gitHistoryCursor, *Error) {
	cursor, cursorErr := decodeGitHistoryCursor(raw)
	if cursorErr != nil || snapshot.scope == nil || cursor.Repo != snapshot.scope.repo || cursor.Identity != snapshot.scope.identity || cursor.Relative != snapshot.scope.relative {
		path := ""
		if snapshot.scope != nil {
			path = snapshot.scope.path
		}
		return gitHistoryCursor{}, &Error{Status: http.StatusBadRequest, Code: "invalid_cursor", MessageKey: "fs.error.gitHistoryCursor", Path: path, Message: "invalid Git history cursor"}
	}
	if finderErr := s.validateFrozenGitHistoryHead(ctx, snapshot, cursor.Head); finderErr != nil {
		return gitHistoryCursor{}, finderErr
	}
	return cursor, nil
}

func historyPathArgs(relative string) []string {
	if relative == "" {
		return nil
	}
	// runGitBounded sets --literal-pathspecs for every Git command. Keep the pathspec itself plain
	// so Git does not interpret the literal-mode prefix as part of a filename.
	return []string{"--", relative}
}

func boundedGitHistoryText(value string) (string, bool) {
	raw := []byte(value)
	truncated := len(raw) > gitHistoryMaxText
	if truncated {
		raw = raw[:gitHistoryMaxText]
	}
	normalized := strings.ToValidUTF8(string(raw), "\uFFFD")
	return normalized, truncated || normalized != string(raw)
}

func cloneGitProductValue(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		cloned := make(map[string]any, len(typed))
		for key, item := range typed {
			cloned[key] = cloneGitProductValue(item)
		}
		return cloned
	case map[string]string:
		cloned := make(map[string]string, len(typed))
		for key, item := range typed {
			cloned[key] = item
		}
		return cloned
	case []map[string]any:
		cloned := make([]map[string]any, len(typed))
		for index, item := range typed {
			cloned[index] = cloneGitProductValue(item).(map[string]any)
		}
		return cloned
	case []any:
		cloned := make([]any, len(typed))
		for index, item := range typed {
			cloned[index] = cloneGitProductValue(item)
		}
		return cloned
	case []string:
		return append([]string(nil), typed...)
	default:
		return value
	}
}

func gitProductCacheKey(parts ...string) string {
	return strings.Join(parts, "\x1f")
}

type gitHistoryStatsInfo struct {
	files       int
	added       int
	removed     int
	binaryFiles int
}

func gitHistoryStatsCacheKey(scope *gitHistoryScope, sha string) string {
	return gitProductCacheKey("history-sha-stats", scope.repo, scope.identity, scope.relative, scope.shallowBoundary, sha)
}

func gitHistoryStatsPageCacheKey(scope *gitHistoryScope, currentHead, frozenHead, refSignature string, offset, limit int) string {
	return gitProductCacheKey("history", "stats", scope.repo, scope.path, scope.identity, scope.relative, currentHead, frozenHead, refSignature, strconv.Itoa(offset), strconv.Itoa(limit))
}

func gitHistoryRefSignature(ctx context.Context, scope *gitHistoryScope) (string, *Error) {
	// Decorations are part of the cached history rows, and refs can move without HEAD moving.
	args := []string{"for-each-ref", "--sort=refname", "--format=%(refname)%x00%(objectname)%x00"}
	output, stderr, err := runGitMetadataBounded(ctx, scope.directory, nil, args, gitHistoryMaxOutput, scope.namespace)
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return "", cancelled(scope.path)
	}
	if errors.Is(err, errGitNamespaceChanged) {
		return "", gitNamespaceChanged(scope.path)
	}
	if err != nil {
		message := "Git history ref snapshot failed"
		if strings.TrimSpace(string(stderr)) != "" {
			message = strings.TrimSpace(string(stderr))
		}
		return "", &Error{Status: http.StatusInternalServerError, Code: "git_history_failed", MessageKey: "fs.error.gitHistoryFailed", Path: scope.path, Retryable: true, Message: message}
	}
	shallowSignature, shallowErr := gitHistoryShallowBoundarySignature(scope.namespace)
	if shallowErr != nil {
		return "", gitNamespaceChanged(scope.path)
	}
	scope.shallowBoundary = shallowSignature
	signatureInput := make([]byte, 0, len(scope.branch)+1+len(output)+1+len(shallowSignature))
	signatureInput = append(signatureInput, scope.branch...)
	signatureInput = append(signatureInput, 0)
	signatureInput = append(signatureInput, output...)
	signatureInput = append(signatureInput, 0)
	signatureInput = append(signatureInput, shallowSignature...)
	digest := sha256.Sum256(signatureInput)
	return fmt.Sprintf("%x", digest[:]), nil
}

func gitHistoryShallowBoundarySignature(namespace *gitNamespace) (string, error) {
	boundary, err := gitHistoryShallowBoundary(namespace)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(boundary)
	return fmt.Sprintf("%x", digest[:]), nil
}

func gitHistoryShallowBoundaryMatches(namespace *gitNamespace, expected string) bool {
	current, err := gitHistoryShallowBoundarySignature(namespace)
	return err == nil && current == expected
}

func gitHistoryShallowBoundary(namespace *gitNamespace) ([]byte, error) {
	if namespace == nil {
		return nil, errGitNamespaceChanged
	}
	for _, directory := range []*os.File{namespace.commonDir, namespace.gitDir} {
		if directory == nil {
			continue
		}
		file, err := openAt(directory, "shallow", syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return nil, errGitNamespaceChanged
		}
		before, statErr := file.Stat()
		if statErr != nil || !before.Mode().IsRegular() || before.Size() > gitHistoryMaxOutput {
			_ = file.Close()
			return nil, errGitNamespaceChanged
		}
		data, readErr := io.ReadAll(io.LimitReader(file, gitHistoryMaxOutput+1))
		after, afterErr := file.Stat()
		closeErr := file.Close()
		if readErr != nil || afterErr != nil || closeErr != nil || len(data) > gitHistoryMaxOutput || !sameDescriptorIdentity(identityFor(before), identityFor(after)) {
			return nil, errGitNamespaceChanged
		}
		return data, nil
	}
	return nil, nil
}

func (s *Service) cachedGitHistoryStats(key string) (gitHistoryStatsInfo, bool) {
	s.gitProductMu.Lock()
	defer s.gitProductMu.Unlock()
	return s.gitHistoryStatsCache.Get(key)
}

func (s *Service) cacheGitHistoryStats(key string, value gitHistoryStatsInfo) {
	s.gitProductMu.Lock()
	defer s.gitProductMu.Unlock()
	s.gitHistoryStatsCache.Set(key, value)
}

func (s *Service) attachCachedGitHistoryStats(scope *gitHistoryScope, commits []map[string]any) {
	for _, commit := range commits {
		sha, _ := commit["sha"].(string)
		if sha == "" {
			continue
		}
		if stats, ok := s.cachedGitHistoryStats(gitHistoryStatsCacheKey(scope, sha)); ok {
			commit["files"], commit["added"], commit["removed"], commit["binary_files"] = stats.files, stats.added, stats.removed, stats.binaryFiles
		}
	}
}

// Git config and signature trust can change while commit SHAs stay fixed, so cached rows refresh those fields.
func (s *Service) refreshGitHistoryCachedMetadata(ctx context.Context, scope *gitHistoryScope, commits []map[string]any, includeSignatureStatus bool) *Error {
	shas := make([]string, 0, len(commits))
	for _, commit := range commits {
		sha, _ := commit["sha"].(string)
		if gitObjectIDPattern.MatchString(sha) {
			shas = append(shas, sha)
		}
	}
	if len(shas) == 0 {
		return nil
	}
	format := "%H%x00%D"
	if includeSignatureStatus {
		format = "%H%x00%G?%x00%D"
	}
	args := []string{"log", "--no-walk", "--format=" + format}
	args = append(args, shas...)
	output, _, err := runGitMetadataBounded(ctx, scope.directory, nil, args, gitHistoryMaxOutput, scope.namespace)
	if err != nil {
		return gitHistoryReadError(scope.path, err)
	}
	records := bytes.Split(bytes.TrimSuffix(output, []byte("\n")), []byte("\n"))
	metadata := make(map[string][]string, len(records))
	for _, record := range records {
		fields := bytes.Split(record, []byte{0})
		if includeSignatureStatus && len(fields) == 3 {
			metadata[string(fields[0])] = []string{string(fields[1]), string(fields[2])}
		} else if !includeSignatureStatus && len(fields) == 2 {
			metadata[string(fields[0])] = []string{string(fields[1])}
		} else {
			return gitHistoryReadError(scope.path, errors.New("malformed cached Git history metadata"))
		}
	}
	for _, commit := range commits {
		sha, _ := commit["sha"].(string)
		fields, ok := metadata[sha]
		if !ok {
			return gitHistoryReadError(scope.path, errors.New("cached Git history commit disappeared"))
		}
		if includeSignatureStatus {
			commit["signature_status"] = fields[0]
			commit["decorations"] = fields[1]
		} else {
			commit["decorations"] = fields[0]
		}
	}
	return nil
}

func (s *Service) cachedGitHistory(key string) (map[string]any, bool) {
	s.gitProductMu.Lock()
	defer s.gitProductMu.Unlock()
	value, ok := s.gitHistoryCache.Get(key)
	if !ok {
		return nil, false
	}
	return cloneGitProductValue(value).(map[string]any), true
}

func (s *Service) cacheGitHistory(key string, value map[string]any) {
	s.gitProductMu.Lock()
	defer s.gitProductMu.Unlock()
	s.gitHistoryCache.Set(key, cloneGitProductValue(value).(map[string]any))
}

func (s *Service) cachedGitCommit(key string) (map[string]any, bool) {
	s.gitProductMu.Lock()
	defer s.gitProductMu.Unlock()
	value, ok := s.gitCommitCache.Get(key)
	if !ok {
		return nil, false
	}
	return cloneGitProductValue(value).(map[string]any), true
}

func (s *Service) cacheGitCommit(key string, value map[string]any) {
	s.gitProductMu.Lock()
	defer s.gitProductMu.Unlock()
	s.gitCommitCache.Set(key, cloneGitProductValue(value).(map[string]any))
}

func gitReachabilityCacheKey(scope *gitHistoryScope, ancestor, descendant string) string {
	return gitProductCacheKey("git-ancestor", scope.repo, scope.identity, scope.relative, scope.shallowBoundary, ancestor, descendant)
}

func (s *Service) cachedGitReachability(key string) bool {
	s.gitProductMu.Lock()
	defer s.gitProductMu.Unlock()
	if s.gitReachabilityCache == nil {
		s.gitReachabilityCache = cache.NewLRU[string, struct{}](maxGitHistoryStatsCacheEntries)
	}
	_, ok := s.gitReachabilityCache.Get(key)
	return ok
}

func (s *Service) cacheGitReachability(key string) {
	s.gitProductMu.Lock()
	defer s.gitProductMu.Unlock()
	if s.gitReachabilityCache == nil {
		s.gitReachabilityCache = cache.NewLRU[string, struct{}](maxGitHistoryStatsCacheEntries)
	}
	s.gitReachabilityCache.Set(key, struct{}{})
}

const gitHistoryFormat = "record%x00%H%x00%h%x00%P%x00%G?%x00%an%x00%at%x00%D%x00%s%x00"

func parseGitHistoryMetadata(part string) (map[string]any, string, error) {
	fields := strings.SplitN(part, "\x00", 9)
	if len(fields) < 9 {
		return nil, "", errors.New("malformed Git history output")
	}
	tail := fields[8]
	if !strings.HasPrefix(tail, "\x00") {
		return nil, "", errors.New("malformed Git history terminator")
	}
	at, err := strconv.ParseInt(strings.TrimSpace(fields[5]), 10, 64)
	if err != nil || !gitObjectIDPattern.MatchString(fields[0]) {
		return nil, "", errors.New("malformed Git history metadata")
	}
	authorText, authorTruncated := boundedGitHistoryText(fields[4])
	decorationText, decorationTruncated := boundedGitHistoryText(fields[6])
	subjectText, subjectTruncated := boundedGitHistoryText(fields[7])
	parentList := []string{}
	if strings.TrimSpace(fields[2]) != "" {
		parentList = strings.Fields(fields[2])
	}
	return map[string]any{
		"sha": fields[0], "short": fields[1], "parents": parentList, "subject": subjectText,
		"signature_status": strings.TrimSpace(fields[3]), "author": authorText, "authored_at": at,
		"decorations": decorationText, "metadata_truncated": authorTruncated || decorationTruncated || subjectTruncated,
	}, tail, nil
}

func parseGitHistory(raw []byte, outputTruncated bool) ([]map[string]any, bool, error) {
	parts := strings.Split(string(raw), "record\x00")
	commits := make([]map[string]any, 0, len(parts)-1)
	truncated := outputTruncated
	for _, part := range parts[1:] {
		commit, _, err := parseGitHistoryMetadata(part)
		if err != nil {
			if outputTruncated {
				return commits, true, nil
			}
			return nil, false, err
		}
		commits = append(commits, commit)
	}
	return commits, truncated, nil
}

func parseGitHistoryStats(raw []byte, outputTruncated bool) ([]map[string]any, bool, error) {
	parts := strings.Split(string(raw), "record\x00")
	commits := make([]map[string]any, 0, len(parts)-1)
	truncated := outputTruncated
	for _, part := range parts[1:] {
		commit, stats, err := parseGitHistoryMetadata(part)
		if err != nil {
			if outputTruncated {
				return commits, true, nil
			}
			return nil, false, err
		}
		files, added, removed, binaryFiles := 0, 0, 0, 0
		stats = strings.TrimPrefix(stats, "\x00")
		statTokens := strings.Split(stats, "\x00")
		recordComplete := true
		for index, token := range statTokens {
			if index == 0 {
				token = strings.TrimPrefix(token, "\n")
			}
			if token == "" {
				continue
			}
			statFields := strings.SplitN(token, "\t", 3)
			if len(statFields) != 3 || (statFields[0] != "-" && !allDigits(statFields[0])) {
				if outputTruncated {
					recordComplete = false
					truncated = true
					break
				}
				return nil, false, errors.New("malformed Git history numstat output")
			}
			if statFields[2] == "" {
				if outputTruncated {
					recordComplete = false
					truncated = true
					break
				}
				return nil, false, errors.New("malformed Git history path")
			}
			removedBinary := statFields[1] == "-"
			addedBinary := statFields[0] == "-"
			if removedBinary != addedBinary {
				return nil, false, errors.New("malformed Git history counts")
			}
			files++
			if addedBinary {
				binaryFiles++
				continue
			}
			rowAdded, addErr := strconv.Atoi(statFields[0])
			rowRemoved, removeErr := strconv.Atoi(statFields[1])
			if addErr != nil || removeErr != nil || rowAdded < 0 || rowRemoved < 0 {
				return nil, false, errors.New("malformed Git history counts")
			}
			added += rowAdded
			removed += rowRemoved
		}
		if !recordComplete {
			break
		}
		commit["files"], commit["added"], commit["removed"], commit["binary_files"] = files, added, removed, binaryFiles
		commits = append(commits, commit)
	}
	return commits, truncated, nil
}

func allDigits(value string) bool {
	if value == "" {
		return false
	}
	for _, character := range value {
		if character < '0' || character > '9' {
			return false
		}
	}
	return true
}

func gitWorktreeStatusCode(xy string) string {
	if strings.ContainsRune(xy, '?') {
		return "??"
	}
	for _, code := range xy {
		if code == ' ' {
			continue
		}
		switch code {
		case 'A', 'C', 'D', 'M', 'R', 'T', 'U':
			return string(code)
		default:
			return "M"
		}
	}
	return "M"
}

func parseGitWorktreeStatus(raw []byte, outputTruncated bool) ([]map[string]any, bool, error) {
	if !outputTruncated && len(raw) > 0 && raw[len(raw)-1] != 0 {
		return nil, false, errors.New("malformed Git worktree status terminator")
	}
	tokens := strings.Split(string(raw), "\x00")
	truncated := outputTruncated
	entries := make([]map[string]any, 0)
	for index := 0; index < len(tokens); {
		token := tokens[index]
		index++
		if token == "" {
			continue
		}
		if len(token) < 4 || token[2] != ' ' {
			if outputTruncated {
				return entries, true, nil
			}
			return nil, false, errors.New("malformed Git worktree status output")
		}
		xy, path := token[:2], token[3:]
		if path == "" || !utf8.ValidString(path) || !sessionFilesValidRelativePath(path) {
			if outputTruncated {
				return entries, true, nil
			}
			return nil, false, errors.New("malformed Git worktree status path")
		}
		entry := map[string]any{"status": gitWorktreeStatusCode(xy), "path": filepath.ToSlash(path)}
		if xy[0] == 'R' || xy[0] == 'C' {
			if index >= len(tokens) || tokens[index] == "" {
				if outputTruncated {
					return entries, true, nil
				}
				return nil, false, errors.New("malformed Git worktree rename status")
			}
			oldPath := tokens[index]
			index++
			if !utf8.ValidString(oldPath) || !sessionFilesValidRelativePath(oldPath) {
				if outputTruncated {
					return entries, true, nil
				}
				return nil, false, errors.New("malformed Git worktree rename path")
			}
			entry["old_path"] = filepath.ToSlash(oldPath)
		}
		entries = append(entries, entry)
		if len(entries) >= gitHistoryMaxStatusEntries {
			truncated = true
			break
		}
	}
	return entries, truncated, nil
}

func gitWorktreePathModTime(root *os.File, relative string) (int64, error) {
	parts := strings.Split(filepath.FromSlash(relative), string(os.PathSeparator))
	current, err := copyDescriptor(root)
	if err != nil {
		return 0, err
	}
	for index, part := range parts {
		child, openErr := openAt(current, part, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
		_ = current.Close()
		if openErr != nil {
			return 0, openErr
		}
		info, statErr := child.Stat()
		if statErr != nil {
			_ = child.Close()
			return 0, statErr
		}
		if index == len(parts)-1 {
			modTime := info.ModTime().Unix()
			_ = child.Close()
			return modTime, nil
		}
		if !info.IsDir() {
			_ = child.Close()
			return 0, syscall.ENOTDIR
		}
		current = child
	}
	return 0, syscall.ENOENT
}

func (s *Service) gitWorktreeDiffStats(ctx context.Context, scope *gitHistoryScope) (gitHistoryStatsInfo, map[string]gitCommitCount, bool) {
	args := []string{"diff", "--numstat", "-z", "--find-renames", "--no-ext-diff", "--no-textconv", "HEAD"}
	args = append(args, historyPathArgs(scope.relative)...)
	raw, _, gitErr := runGitBounded(ctx, scope.directory, nil, args, gitHistoryMaxOutput, scope.namespace)
	if gitErr != nil {
		return gitHistoryStatsInfo{}, nil, false
	}
	counts, _, parseErr := parseGitNumstat(raw, false)
	if parseErr != nil {
		return gitHistoryStatsInfo{}, nil, false
	}
	stats := gitHistoryStatsInfo{}
	for _, count := range counts {
		stats.files++
		if count.binary {
			stats.binaryFiles++
			continue
		}
		stats.added += sessionFilesNumber(count.added)
		stats.removed += sessionFilesNumber(count.removed)
	}
	return stats, counts, true
}

func (s *Service) gitWorktreeStatus(ctx context.Context, scope *gitHistoryScope) map[string]any {
	args := append([]string{"status", "--porcelain=v1", "-z", "--untracked-files=all"}, historyPathArgs(scope.relative)...)
	raw, _, gitErr := runGitBounded(ctx, scope.directory, nil, args, gitHistoryMaxOutput, scope.namespace)
	if gitErr != nil {
		return map[string]any{"available": false, "dirty": false, "entries": []map[string]any{}, "truncated": false}
	}
	entries, truncated, parseErr := parseGitWorktreeStatus(raw, false)
	if parseErr != nil {
		return map[string]any{"available": false, "dirty": false, "entries": []map[string]any{}, "truncated": false}
	}
	statusCounts := make(map[string]int)
	var modifiedAt int64
	for index, entry := range entries {
		statusCode, _ := entry["status"].(string)
		statusCounts[statusCode]++
		if index >= 200 {
			continue
		}
		path, _ := entry["path"].(string)
		if modTime, statErr := gitWorktreePathModTime(scope.directory, path); statErr == nil && modTime > modifiedAt {
			modifiedAt = modTime
		}
	}
	sort.SliceStable(entries, func(left, right int) bool {
		return fmt.Sprint(entries[left]["path"]) < fmt.Sprint(entries[right]["path"])
	})
	statsAvailable := true
	stats := gitHistoryStatsInfo{}
	fileCounts := map[string]gitCommitCount{}
	if len(entries) > 0 {
		stats, fileCounts, statsAvailable = s.gitWorktreeDiffStats(ctx, scope)
		if fileCounts == nil {
			fileCounts = map[string]gitCommitCount{}
		}
	}
	fileCountIndex := indexGitCommitCounts(fileCounts)
	for _, entry := range entries {
		path, _ := entry["path"].(string)
		added, removed, binary := numstatForPath(fileCountIndex, path)
		countsAvailable := added != nil || removed != nil || binary
		statusCode, _ := entry["status"].(string)
		if statusCode == "??" && !countsAvailable {
			added, binary = s.untrackedLineCount(ctx, scope.repo, path)
			removed = 0
			countsAvailable = added != nil || binary
		}
		entry["counts_available"] = countsAvailable
		entry["binary"] = binary
		if countsAvailable {
			entry["added"], entry["removed"] = added, removed
		} else {
			entry["added"], entry["removed"] = nil, nil
		}
	}
	return map[string]any{
		"available":       true,
		"dirty":           len(entries) > 0,
		"entries":         entries,
		"truncated":       truncated,
		"files":           len(entries),
		"added":           stats.added,
		"removed":         stats.removed,
		"binary_files":    stats.binaryFiles,
		"stats_available": statsAvailable,
		"modified_at":     modifiedAt,
		"status_counts":   statusCounts,
	}
}

func (s *Service) attachGitWorktreeStatus(ctx context.Context, scope *gitHistoryScope, payload map[string]any) map[string]any {
	payload["worktree_status"] = s.gitWorktreeStatus(ctx, scope)
	return payload
}

func (s *Service) GitHistory(ctx context.Context, raw, limitRaw, cursorRaw string, includeStats ...bool) (map[string]any, *Error) {
	withStats := len(includeStats) > 0 && includeStats[0]
	return s.gitHistory(ctx, raw, limitRaw, cursorRaw, withStats, nil)
}

// Metadata and mutable worktree status have separate request lifetimes so the list can paint
// before scanning modified and untracked files.
func (s *Service) GitHistoryMetadata(ctx context.Context, raw, limitRaw, cursorRaw string, offset int) (map[string]any, *Error) {
	var requestedOffset *int
	if offset >= 0 {
		if cursorRaw == "" || offset > gitHistoryMaxOffset {
			return nil, invalid(raw, "start_offset requires a valid frozen Git history cursor")
		}
		requestedOffset = &offset
	}
	return s.gitHistory(ctx, raw, limitRaw, cursorRaw, false, requestedOffset, false)
}

func (s *Service) GitHistoryWorktree(ctx context.Context, raw string) (map[string]any, *Error) {
	scope, finderErr := s.openGitHistoryScopeWithOptions(ctx, raw, gitHistoryScopeOptions{})
	if finderErr != nil {
		return nil, finderErr
	}
	defer scope.close()
	gitCtx, cancel := gitViewContext(ctx)
	defer cancel()
	if finderErr := s.prepareGitHistoryNamespace(gitCtx, scope); finderErr != nil {
		return nil, finderErr
	}
	return s.attachGitWorktreeStatus(gitCtx, scope, map[string]any{"path": scope.path, "repository_identity": scope.identity}), nil
}

func (s *Service) GitHistoryPageAtOffset(ctx context.Context, raw, limitRaw, cursorRaw string, offset int) (map[string]any, *Error) {
	if cursorRaw == "" || offset < 0 || offset > gitHistoryMaxOffset {
		return nil, invalid(raw, "start_offset requires a valid frozen Git history cursor")
	}
	return s.gitHistory(ctx, raw, limitRaw, cursorRaw, false, &offset, false)
}

// GitHistoryAtOffset reads stats at a frozen cursor position without walking preceding rows.
func (s *Service) GitHistoryAtOffset(ctx context.Context, raw, limitRaw, cursorRaw string, offset int) (map[string]any, *Error) {
	if cursorRaw == "" || offset < 0 || offset > gitHistoryMaxOffset {
		return nil, invalid(raw, "stats_offset requires a valid frozen Git history cursor")
	}
	return s.gitHistory(ctx, raw, limitRaw, cursorRaw, true, &offset, false)
}

// GitHistoryStatsStream emits frozen history stats in caller-sized batches while keeping the
// authorized Git scope open for the whole request.
func (s *Service) GitHistoryStatsStream(ctx context.Context, raw, cursorRaw string, count, batchSize int, emit func(map[string]any) error) *Error {
	if cursorRaw == "" || count < 1 || batchSize < 1 || count > gitHistoryMaxOffset || batchSize > gitHistoryMaxLimit {
		return invalid(raw, "invalid Git history stats stream parameters")
	}
	if emit == nil {
		return invalid(raw, "Git history stats stream callback is required")
	}
	batchSizes := make([]int, 0, (count+batchSize-1)/batchSize)
	for remaining := count; remaining > 0; {
		limit := batchSize
		if remaining < limit {
			limit = remaining
		}
		batchSizes = append(batchSizes, limit)
		remaining -= limit
	}
	_, finderErr := s.gitHistoryStatsBatches(ctx, raw, cursorRaw, batchSizes, false, emit)
	return finderErr
}

// GitHistoryStatsScheduled emits one payload for each caller-supplied batch. The batches use
// disjoint offsets from one frozen cursor and are calculated concurrently, so a fast batch can
// backfill the UI before a slower batch finishes. The returned cursor advances past the highest
// offset that was actually available.
func (s *Service) GitHistoryStatsScheduled(ctx context.Context, raw, cursorRaw string, batchSizes []int, emit func(map[string]any) error, startOffset ...int) (string, *Error) {
	if cursorRaw == "" || len(batchSizes) == 0 || len(batchSizes) > gitHistoryMaxScheduledBatches {
		return "", invalid(raw, "invalid Git history stats schedule parameters")
	}
	total := 0
	for _, batchSize := range batchSizes {
		if batchSize < 1 || batchSize > gitHistoryMaxLimit {
			return "", invalid(raw, "invalid Git history stats schedule batch size")
		}
		if total > gitHistoryMaxOffset-batchSize {
			return "", invalid(raw, "Git history stats schedule is too large")
		}
		total += batchSize
	}
	if emit == nil {
		return "", invalid(raw, "Git history stats stream callback is required")
	}
	return s.gitHistoryStatsBatches(ctx, raw, cursorRaw, batchSizes, true, emit, startOffset...)
}

type gitHistoryStatsBatch struct {
	offset int
	limit  int
}

type gitHistoryStatsResult struct {
	offset     int
	payload    map[string]any
	finderErr  *Error
	nextCursor string
}

func (s *Service) gitHistoryStatsBatches(ctx context.Context, raw, cursorRaw string, batchSizes []int, concurrent bool, emit func(map[string]any) error, startOffset ...int) (string, *Error) {
	scope, finderErr := s.openGitHistoryScopeContext(ctx, raw)
	if finderErr != nil {
		return "", finderErr
	}
	defer scope.close()
	gitCtx, cancel := gitViewContext(ctx)
	defer cancel()
	if finderErr := s.prepareGitHistoryNamespace(gitCtx, scope); finderErr != nil {
		return "", finderErr
	}
	snapshot, finderErr := s.captureGitSnapshot(gitCtx, scope, "fs.error.gitHistoryFailed")
	if finderErr != nil {
		return "", finderErr
	}
	currentHead := snapshot.head
	if currentHead == "" {
		return "", &Error{Status: http.StatusConflict, Code: "git_history_stale", MessageKey: "fs.error.gitHistoryStale", Path: scope.path, Message: "Git history snapshot is stale"}
	}
	cursor, cursorErr := s.validateGitHistoryCursor(gitCtx, snapshot, cursorRaw)
	if cursorErr != nil {
		return "", cursorErr
	}
	frozenHead := cursor.Head
	offset := cursor.Offset
	if len(startOffset) > 0 {
		if len(startOffset) != 1 || startOffset[0] < 0 || startOffset[0] > gitHistoryMaxOffset {
			return "", invalid(raw, "invalid Git history stats offset")
		}
		offset = startOffset[0]
	}
	batches := make([]gitHistoryStatsBatch, 0, len(batchSizes))
	for _, limit := range batchSizes {
		if offset > gitHistoryMaxOffset-limit {
			return "", invalid(raw, "Git history stats schedule is too large")
		}
		batches = append(batches, gitHistoryStatsBatch{offset: offset, limit: limit})
		offset += limit
	}
	snapshot.refSignature, finderErr = gitHistoryRefSignature(gitCtx, scope)
	if finderErr != nil {
		return "", finderErr
	}
	if concurrent {
		return s.gitHistoryStatsConcurrent(gitCtx, scope, currentHead, frozenHead, snapshot.refSignature, batches, emit)
	}
	var nextCursor string
	for _, batch := range batches {
		cacheKey := gitHistoryStatsPageCacheKey(scope, currentHead, frozenHead, snapshot.refSignature, batch.offset, batch.limit)
		payload, payloadErr := s.gitHistoryStatsAtOffset(gitCtx, scope, frozenHead, batch.offset, batch.limit, cacheKey)
		if payloadErr != nil {
			return "", payloadErr
		}
		commits, _ := payload["commits"].([]map[string]any)
		if len(commits) == 0 {
			return nextCursor, nil
		}
		if emitErr := emit(payload); emitErr != nil {
			if errors.Is(gitCtx.Err(), context.Canceled) || errors.Is(gitCtx.Err(), context.DeadlineExceeded) {
				return "", cancelled(scope.path)
			}
			return nextCursor, nil
		}
		nextCursor, _ = payload["next_cursor"].(string)
		if len(commits) < batch.limit || strings.TrimSpace(nextCursor) == "" {
			return nextCursor, nil
		}
	}
	return nextCursor, nil
}

func (s *Service) gitHistoryStatsConcurrent(ctx context.Context, scope *gitHistoryScope, currentHead, frozenHead, refSignature string, batches []gitHistoryStatsBatch, emit func(map[string]any) error) (string, *Error) {
	batchContext, cancel := context.WithCancel(ctx)
	defer cancel()
	results := make(chan gitHistoryStatsResult, len(batches))
	var waitGroup sync.WaitGroup
	for _, batch := range batches {
		batch := batch
		waitGroup.Add(1)
		go func() {
			defer waitGroup.Done()
			cacheKey := gitHistoryStatsPageCacheKey(scope, currentHead, frozenHead, refSignature, batch.offset, batch.limit)
			payload, finderErr := s.gitHistoryStatsAtOffset(batchContext, scope, frozenHead, batch.offset, batch.limit, cacheKey)
			result := gitHistoryStatsResult{offset: batch.offset, payload: payload, finderErr: finderErr}
			if payload != nil {
				result.nextCursor, _ = payload["next_cursor"].(string)
			}
			results <- result
		}()
	}
	go func() {
		waitGroup.Wait()
		close(results)
	}()

	var firstErr *Error
	maxOffset := -1
	nextCursor := ""
	for result := range results {
		if result.finderErr != nil {
			if firstErr == nil || firstErr.Code == "cancelled" {
				firstErr = result.finderErr
				cancel()
			}
			continue
		}
		commits, _ := result.payload["commits"].([]map[string]any)
		if result.offset >= maxOffset {
			maxOffset = result.offset
			nextCursor = result.nextCursor
		}
		if firstErr == nil && len(commits) > 0 {
			if emitErr := emit(result.payload); emitErr != nil {
				firstErr = cancelled(scope.path)
				cancel()
			}
		}
	}
	if firstErr != nil {
		return "", firstErr
	}
	return nextCursor, nil
}

func gitHistoryReadError(path string, err error) *Error {
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return cancelled(path)
	}
	if errors.Is(err, errGitNamespaceChanged) {
		return gitNamespaceChanged(path)
	}
	if errors.Is(err, errGitOutputLimit) {
		return &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitHistoryTooLarge", Path: path, Message: "Git history is too large"}
	}
	return &Error{Status: http.StatusInternalServerError, Code: "git_history_failed", MessageKey: "fs.error.gitHistoryFailed", Path: path, Retryable: true, Message: err.Error()}
}

// Fetch metadata first so pagination and cache hits never calculate unused diffs.
func (s *Service) gitHistoryStatsAtOffset(ctx context.Context, scope *gitHistoryScope, frozenHead string, offset, limit int, cacheKey string) (map[string]any, *Error) {
	if cached, ok := s.cachedGitHistory(cacheKey); ok {
		cached["hosted_remote"] = scope.hostedRemote
		commits, _ := cached["commits"].([]map[string]any)
		if finderErr := s.refreshGitHistoryCachedMetadata(ctx, scope, commits, true); finderErr != nil {
			return nil, finderErr
		}
		if !gitHistoryShallowBoundaryMatches(scope.namespace, scope.shallowBoundary) {
			return nil, gitNamespaceChanged(scope.path)
		}
		return cached, nil
	}
	args := []string{"log", "--topo-order", "--no-patch", "--decorate=short", "-z", "--max-count=" + strconv.Itoa(limit+1), "--skip=" + strconv.Itoa(offset), "--format=" + strings.ReplaceAll(gitHistoryFormat, "%G?", ""), frozenHead}
	args = append(args, historyPathArgs(scope.relative)...)
	output, _, gitErr := runGitMetadataBounded(ctx, scope.directory, nil, args, gitHistoryMaxOutput, scope.namespace)
	if gitErr != nil {
		return nil, gitHistoryReadError(scope.path, gitErr)
	}
	commits, outputTruncated, parseErr := parseGitHistory(output, false)
	if parseErr != nil {
		return nil, gitHistoryReadError(scope.path, parseErr)
	}
	visible := commits
	if len(visible) > limit {
		visible = visible[:limit]
	}
	s.attachCachedGitHistoryStats(scope, visible)
	missing := make([]string, 0, len(visible))
	for _, commit := range visible {
		if _, complete := commit["files"]; !complete {
			missing = append(missing, commit["sha"].(string))
		}
	}
	if len(missing) > 0 {
		// Explicit SHAs avoid calculating counts for the pagination sentinel or cached rows.
		args = []string{"-c", "core.quotePath=false", "show", "--root", "--diff-merges=first-parent", "--no-renames", "--numstat", "-z", "--format=" + gitHistoryFormat}
		args = append(args, gitCommitDiffOptions()...)
		args = append(args, missing...)
		args = append(args, historyPathArgs(scope.relative)...)
		output, _, gitErr = runGitMetadataBounded(ctx, scope.directory, nil, args, gitHistoryMaxOutput, scope.namespace)
		if gitErr != nil {
			return nil, gitHistoryReadError(scope.path, gitErr)
		}
		stats, _, parseErr := parseGitHistoryStats(output, false)
		if parseErr != nil {
			return nil, gitHistoryReadError(scope.path, parseErr)
		}
		if !gitHistoryShallowBoundaryMatches(scope.namespace, scope.shallowBoundary) {
			return nil, gitNamespaceChanged(scope.path)
		}
		for _, commit := range stats {
			s.cacheGitHistoryStats(gitHistoryStatsCacheKey(scope, commit["sha"].(string)), gitHistoryStatsInfo{files: commit["files"].(int), added: commit["added"].(int), removed: commit["removed"].(int), binaryFiles: commit["binary_files"].(int)})
		}
		s.attachCachedGitHistoryStats(scope, visible)
	}
	if finderErr := s.refreshGitHistoryCachedMetadata(ctx, scope, visible, true); finderErr != nil {
		return nil, finderErr
	}
	if !gitHistoryShallowBoundaryMatches(scope.namespace, scope.shallowBoundary) {
		return nil, gitNamespaceChanged(scope.path)
	}

	snapshotCursor, _ := encodeGitHistoryCursor(gitHistoryCursor{Repo: scope.repo, Identity: scope.identity, Relative: scope.relative, Head: frozenHead, Offset: 0})
	snapshotCursorLimited := len(snapshotCursor) > gitHistoryMaxCursor
	if snapshotCursorLimited {
		snapshotCursor = ""
	}
	hasMore := outputTruncated || len(commits) > len(visible)
	nextOffset := offset + len(visible)
	cursorLimited := snapshotCursorLimited || (hasMore && len(visible) > 0 && nextOffset > gitHistoryMaxOffset)
	nextCursor := ""
	if hasMore && len(visible) > 0 && !cursorLimited {
		nextCursor, _ = encodeGitHistoryCursor(gitHistoryCursor{Repo: scope.repo, Identity: scope.identity, Relative: scope.relative, Head: frozenHead, Offset: nextOffset})
		if len(nextCursor) > gitHistoryMaxCursor {
			nextCursor = ""
			cursorLimited = true
		}
	}
	reasons := make([]string, 0, 3)
	if outputTruncated {
		reasons = append(reasons, "output_bytes")
	}
	for _, commit := range commits {
		if commit["metadata_truncated"] == true {
			reasons = append(reasons, "metadata_bytes")
			break
		}
	}
	if cursorLimited {
		reasons = append(reasons, "cursor_limit")
	}
	payload := map[string]any{"path": scope.path, "repo": scope.repo, "repository_identity": scope.identity, "shallow_boundary": scope.shallowBoundary, "relative_path": scope.relative, "branch": scope.branch, "head": frozenHead, "hosted_remote": scope.hostedRemote, "snapshot_cursor": snapshotCursor, "start_offset": offset, "commits": visible, "next_cursor": nextCursor, "truncated": len(reasons) > 0}
	if len(reasons) > 0 {
		payload["truncation_reason"] = strings.Join(reasons, ",")
	}
	encoded, _ := json.Marshal(payload)
	if len(encoded) > gitHistoryMaxPayload {
		return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitHistoryTooLarge", Path: scope.path, Message: "Git history is too large"}
	}
	s.cacheGitHistory(cacheKey, payload)
	return payload, nil
}

func (s *Service) gitHistory(ctx context.Context, raw, limitRaw, cursorRaw string, withStats bool, requestedOffset *int, worktree ...bool) (map[string]any, *Error) {
	withWorktree := len(worktree) == 0 || worktree[0]
	limit, err := gitHistoryLimit(limitRaw)
	if err != nil {
		return nil, invalid(raw, err.Error())
	}
	scope, finderErr := s.openGitHistoryScopeContext(ctx, raw)
	if finderErr != nil {
		return nil, finderErr
	}
	defer scope.close()
	gitCtx, cancel := gitViewContext(ctx)
	defer cancel()
	if finderErr := s.prepareGitHistoryNamespace(gitCtx, scope); finderErr != nil {
		return nil, finderErr
	}
	snapshot, finderErr := s.captureGitSnapshot(gitCtx, scope, "fs.error.gitHistoryFailed")
	if finderErr != nil {
		return nil, finderErr
	}
	currentHead := snapshot.head
	if currentHead == "" {
		if cursorRaw != "" {
			return nil, &Error{Status: http.StatusConflict, Code: "git_history_stale", MessageKey: "fs.error.gitHistoryStale", Path: scope.path, Message: "Git history snapshot is stale"}
		}
		shallowBoundary, boundaryErr := gitHistoryShallowBoundarySignature(scope.namespace)
		if boundaryErr != nil {
			return nil, gitNamespaceChanged(scope.path)
		}
		scope.shallowBoundary = shallowBoundary
		payload := map[string]any{"path": scope.path, "repo": scope.repo, "repository_identity": scope.identity, "shallow_boundary": scope.shallowBoundary, "relative_path": scope.relative, "branch": scope.branch, "head": "", "hosted_remote": scope.hostedRemote, "snapshot_cursor": "", "commits": []map[string]any{}, "next_cursor": "", "truncated": false}
		if withWorktree {
			s.attachGitWorktreeStatus(gitCtx, scope, payload)
		}
		return payload, nil
	}
	frozenHead, offset := currentHead, 0
	if cursorRaw != "" {
		cursor, cursorErr := s.validateGitHistoryCursor(gitCtx, snapshot, cursorRaw)
		if cursorErr != nil {
			return nil, cursorErr
		}
		frozenHead, offset = cursor.Head, cursor.Offset
	}
	if requestedOffset != nil {
		offset = *requestedOffset
	}
	snapshot.refSignature, finderErr = gitHistoryRefSignature(gitCtx, scope)
	if finderErr != nil {
		return nil, finderErr
	}
	statsKey := "metadata"
	if !withWorktree {
		statsKey = "metadata-fast"
	}
	if withStats {
		statsKey = "stats"
	}
	cacheKey := ""
	if withStats {
		cacheKey = gitHistoryStatsPageCacheKey(scope, currentHead, frozenHead, snapshot.refSignature, offset, limit)
	} else {
		cacheKey = gitProductCacheKey("history", statsKey, scope.repo, scope.path, scope.identity, scope.relative, currentHead, frozenHead, snapshot.refSignature, strconv.Itoa(offset), strconv.Itoa(limit))
	}
	// The browser persists the offset-zero snapshot cursor in the layout URL. That cursor is
	// still the first visible page, so it needs the same live dirty-path summary as an uncursored
	// request. Later history pages and stats backfills do not need to repeat this Git status scan.
	includeWorktreeStatus := withWorktree && !withStats && (cursorRaw == "" || offset == 0)
	if cached, ok := s.cachedGitHistory(cacheKey); ok {
		cached["hosted_remote"] = scope.hostedRemote
		if !withWorktree {
			delete(cached, "worktree_status")
		}
		commits, _ := cached["commits"].([]map[string]any)
		if !withWorktree {
			for _, commit := range commits {
				delete(commit, "signature_status")
			}
		}
		if !withStats {
			s.attachCachedGitHistoryStats(scope, commits)
		}
		if includeWorktreeStatus {
			s.attachGitWorktreeStatus(gitCtx, scope, cached)
		}
		if finderErr := s.refreshGitHistoryCachedMetadata(gitCtx, scope, commits, withWorktree); finderErr != nil {
			return nil, finderErr
		}
		if !gitHistoryShallowBoundaryMatches(scope.namespace, scope.shallowBoundary) {
			return nil, gitNamespaceChanged(scope.path)
		}
		return cached, nil
	}
	if withStats {
		return s.gitHistoryStatsAtOffset(gitCtx, scope, frozenHead, offset, limit, cacheKey)
	}
	args := []string{"-c", "core.quotePath=false", "log", "--topo-order", "--root", "--diff-merges=first-parent", "--no-ext-diff", "--no-textconv", "--no-renames", "--no-patch", "--decorate=short"}
	format := gitHistoryFormat
	if !withWorktree {
		// Signature verification is enrichment; it must not delay first paint.
		format = strings.ReplaceAll(format, "%G?", "")
	}
	args = append(args, "-z", "--max-count="+strconv.Itoa(limit+1), "--skip="+strconv.Itoa(offset), "--format="+format, frozenHead)
	args = append(args, historyPathArgs(scope.relative)...)
	output, stderr, gitErr := runGitMetadataBounded(gitCtx, scope.directory, nil, args, gitHistoryMaxOutput, scope.namespace)
	if errors.Is(gitErr, context.Canceled) || errors.Is(gitErr, context.DeadlineExceeded) {
		return nil, cancelled(scope.path)
	}
	outputTruncated := false
	if errors.Is(gitErr, errGitOutputLimit) && len(stderr) == 0 && len(output) > 0 {
		outputTruncated = true
		gitErr = nil
	}
	if gitErr != nil {
		if errors.Is(gitErr, errGitNamespaceChanged) {
			return nil, gitNamespaceChanged(scope.path)
		}
		if errors.Is(gitErr, errGitOutputLimit) {
			return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitHistoryTooLarge", Path: scope.path, Message: "Git history is too large"}
		}
		message := "Git history failed"
		if strings.TrimSpace(string(stderr)) != "" {
			message = strings.TrimSpace(string(stderr))
		}
		return nil, &Error{Status: http.StatusInternalServerError, Code: "git_history_failed", MessageKey: "fs.error.gitHistoryFailed", Path: scope.path, Retryable: true, Message: message}
	}
	commits, outputTruncated, parseErr := parseGitHistory(output, outputTruncated)
	if parseErr != nil {
		return nil, &Error{Status: http.StatusInternalServerError, Code: "git_history_failed", MessageKey: "fs.error.gitHistoryFailed", Path: scope.path, Retryable: true, Message: parseErr.Error()}
	}
	if !withWorktree {
		for _, commit := range commits {
			delete(commit, "signature_status")
		}
	}
	visible := commits
	if len(visible) > limit {
		visible = visible[:limit]
	}
	if !withStats {
		s.attachCachedGitHistoryStats(scope, visible)
	}
	snapshotCursor, _ := encodeGitHistoryCursor(gitHistoryCursor{Repo: scope.repo, Identity: scope.identity, Relative: scope.relative, Head: frozenHead, Offset: 0})
	snapshotCursorLimited := len(snapshotCursor) > gitHistoryMaxCursor
	if snapshotCursorLimited {
		snapshotCursor = ""
	}
	payloadBytesTruncated, snapshotCursorTruncated := false, false
	var payload map[string]any
	for {
		hasMore := outputTruncated || payloadBytesTruncated || len(commits) > len(visible)
		nextOffset := offset + len(visible)
		cursorLimited := snapshotCursorLimited || (hasMore && len(visible) > 0 && nextOffset > gitHistoryMaxOffset)
		nextCursor := ""
		if hasMore && len(visible) > 0 && !cursorLimited {
			nextCursor, err = encodeGitHistoryCursor(gitHistoryCursor{Repo: scope.repo, Identity: scope.identity, Relative: scope.relative, Head: frozenHead, Offset: nextOffset})
			if err != nil {
				return nil, &Error{Status: http.StatusInternalServerError, Code: "git_history_failed", MessageKey: "fs.error.gitHistoryFailed", Path: scope.path, Retryable: true, Message: "Git history cursor failed"}
			}
			if len(nextCursor) > gitHistoryMaxCursor {
				nextCursor = ""
				cursorLimited = true
			}
		}
		reasons := make([]string, 0, 4)
		if outputTruncated {
			reasons = append(reasons, "output_bytes")
		}
		for _, commit := range commits {
			if commit["metadata_truncated"] == true {
				reasons = append(reasons, "metadata_bytes")
				break
			}
		}
		if payloadBytesTruncated {
			reasons = append(reasons, "payload_bytes")
		}
		if snapshotCursorTruncated {
			reasons = append(reasons, "snapshot_cursor")
		}
		if cursorLimited {
			reasons = append(reasons, "cursor_limit")
		}
		payload = map[string]any{"path": scope.path, "repo": scope.repo, "repository_identity": scope.identity, "shallow_boundary": scope.shallowBoundary, "relative_path": scope.relative, "branch": scope.branch, "head": frozenHead, "hosted_remote": scope.hostedRemote, "snapshot_cursor": snapshotCursor, "start_offset": offset, "commits": visible, "next_cursor": nextCursor, "truncated": len(reasons) > 0}
		if len(reasons) > 0 {
			payload["truncation_reason"] = strings.Join(reasons, ",")
		}
		if includeWorktreeStatus {
			s.attachGitWorktreeStatus(gitCtx, scope, payload)
		}
		encoded, _ := json.Marshal(payload)
		if len(encoded) <= gitHistoryMaxPayload {
			if !gitHistoryShallowBoundaryMatches(scope.namespace, scope.shallowBoundary) {
				return nil, gitNamespaceChanged(scope.path)
			}
			s.cacheGitHistory(cacheKey, payload)
			return payload, nil
		}
		if len(visible) == 1 && snapshotCursor != "" && nextCursor != "" {
			snapshotCursor = ""
			snapshotCursorTruncated = true
			payloadBytesTruncated = true
			continue
		}
		if len(visible) == 0 {
			return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitHistoryTooLarge", Path: scope.path, Message: "Git history is too large"}
		}
		visible = visible[:len(visible)-1]
		payloadBytesTruncated = true
	}
}

func validateGitObjectID(value, key string) *Error {
	if !gitObjectIDPattern.MatchString(value) {
		return &Error{Status: http.StatusBadRequest, Code: "unknown_commit", MessageKey: key, Message: "invalid Git object ID"}
	}
	return nil
}

func gitExitStatus(err error, status int) bool {
	var exitErr *os.PathError
	if errors.As(err, &exitErr) {
		return false
	}
	var commandErr interface{ ExitCode() int }
	return errors.As(err, &commandErr) && commandErr.ExitCode() == status
}

func (s *Service) gitCommitExists(ctx context.Context, scope *gitHistoryScope, oid string) (bool, *Error) {
	output, stderr, err := runGitMetadataBounded(ctx, scope.directory, nil, []string{"rev-parse", "--verify", "--quiet", oid + "^{commit}"}, 512, scope.namespace)
	if err == nil && strings.TrimSpace(string(output)) == oid {
		return true, nil
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return false, cancelled(scope.path)
	}
	if errors.Is(err, errGitNamespaceChanged) {
		return false, gitNamespaceChanged(scope.path)
	}
	if gitExitStatus(err, 1) && len(stderr) == 0 {
		return false, nil
	}
	if err != nil {
		return false, &Error{Status: http.StatusInternalServerError, Code: "git_commit_failed", MessageKey: "fs.error.gitCommitFailed", Path: scope.path, Retryable: true, Message: "Git commit lookup failed"}
	}
	return false, nil
}

// gitCommitReachability combines the common existence and ancestry checks. A successful or
// non-ancestor merge-base result proves the candidate object exists; only an unknown-object error
// needs a second lookup to preserve the established unknown-versus-outside-history response.
func (s *Service) gitCommitReachability(ctx context.Context, scope *gitHistoryScope, ancestor, descendant string) (bool, bool, *Error) {
	if err := ctx.Err(); err != nil {
		return false, false, cancelled(scope.path)
	}
	shallowBoundary, boundaryErr := gitHistoryShallowBoundarySignature(scope.namespace)
	if boundaryErr != nil {
		return false, false, gitNamespaceChanged(scope.path)
	}
	scope.shallowBoundary = shallowBoundary
	cacheKey := gitReachabilityCacheKey(scope, ancestor, descendant)
	if s.cachedGitReachability(cacheKey) {
		return true, true, nil
	}
	_, stderr, err := runGitMetadataBounded(ctx, scope.directory, nil, []string{"merge-base", "--is-ancestor", ancestor, descendant}, 512, scope.namespace)
	if err == nil {
		if !gitHistoryShallowBoundaryMatches(scope.namespace, scope.shallowBoundary) {
			return false, false, gitNamespaceChanged(scope.path)
		}
		s.cacheGitReachability(cacheKey)
		return true, true, nil
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return false, false, cancelled(scope.path)
	}
	if errors.Is(err, errGitNamespaceChanged) {
		return false, false, gitNamespaceChanged(scope.path)
	}
	if gitExitStatus(err, 1) && len(stderr) == 0 {
		if !gitHistoryShallowBoundaryMatches(scope.namespace, scope.shallowBoundary) {
			return false, false, gitNamespaceChanged(scope.path)
		}
		return true, false, nil
	}
	if gitExitStatus(err, 128) {
		found, finderErr := s.gitCommitExists(ctx, scope, ancestor)
		if finderErr != nil {
			return false, false, finderErr
		}
		if !gitHistoryShallowBoundaryMatches(scope.namespace, scope.shallowBoundary) {
			return false, false, gitNamespaceChanged(scope.path)
		}
		if !found {
			return false, false, nil
		}
	}
	return false, false, &Error{Status: http.StatusInternalServerError, Code: "git_commit_failed", MessageKey: "fs.error.gitCommitFailed", Path: scope.path, Retryable: true, Message: "Git ancestry lookup failed"}
}

func boundedGitCommitMessage(raw []byte) (string, bool) {
	trimmed := bytes.TrimRight(raw, "\n")
	if len(trimmed) <= gitCommitMaxMessage {
		return strings.ToValidUTF8(string(trimmed), "\uFFFD"), false
	}
	return strings.ToValidUTF8(string(trimmed[:gitCommitMaxMessage]), "\uFFFD"), true
}

func parseGitCommitMetadata(raw []byte, outputTruncated ...bool) (map[string]any, error) {
	truncated := len(outputTruncated) > 0 && outputTruncated[0]
	hasTerminator := bytes.HasSuffix(raw, []byte("\x00\n"))
	if !truncated && !hasTerminator {
		return nil, errors.New("malformed Git commit metadata terminator")
	}
	payload := raw
	if hasTerminator {
		payload = raw[:len(raw)-2]
	}
	fields := strings.SplitN(string(payload), "\x00", 6)
	if len(fields) < 6 {
		return nil, errors.New("malformed Git commit metadata")
	}
	at, err := strconv.ParseInt(strings.TrimSpace(fields[3]), 10, 64)
	if err != nil {
		return nil, errors.New("malformed Git commit timestamp")
	}
	parents := []string{}
	if strings.TrimSpace(fields[1]) != "" {
		parents = strings.Fields(fields[1])
	}
	message, messageTruncated := boundedGitCommitMessage([]byte(fields[5]))
	return map[string]any{
		"sha": strings.ToValidUTF8(fields[0], "\uFFFD"), "parents": parents,
		"author": strings.ToValidUTF8(fields[2], "\uFFFD"), "authored_at": at,
		"subject": strings.ToValidUTF8(fields[4], "\uFFFD"), "message": message,
		"message_truncated": messageTruncated || truncated,
	}, nil
}

func gitEmptyTreeOID(commit string) string {
	data := []byte("tree 0\x00")
	if len(commit) == 64 {
		digest := sha256.Sum256(data)
		return fmt.Sprintf("%x", digest)
	}
	digest := sha1.Sum(data)
	return fmt.Sprintf("%x", digest)
}

type gitCommitCount struct {
	added, removed any
	binary         bool
}

type gitCommitCountIndex map[string]gitCommitCount

func indexGitCommitCounts(counts map[string]gitCommitCount) gitCommitCountIndex {
	index := make(gitCommitCountIndex, len(counts)*2)
	for key, count := range counts {
		oldPath, newPath, found := strings.Cut(key, "\x00")
		if !found {
			continue
		}
		if _, exists := index[oldPath]; !exists {
			index[oldPath] = count
		}
		if _, exists := index[newPath]; !exists {
			index[newPath] = count
		}
	}
	return index
}

func parseGitNameStatus(raw []byte, outputTruncated bool) ([]map[string]string, bool, error) {
	if !outputTruncated && len(raw) > 0 && raw[len(raw)-1] != 0 {
		return nil, false, errors.New("malformed Git status terminator")
	}
	tokens := strings.Split(string(raw), "\x00")
	truncated := outputTruncated
	// A bounded read can end in the middle of a NUL-delimited record. The final
	// unterminated token is not a row and must not be mistaken for one.
	if outputTruncated && len(raw) > 0 && raw[len(raw)-1] != 0 && len(tokens) > 0 {
		tokens = tokens[:len(tokens)-1]
	}
	rows := make([]map[string]string, 0)
	identities := make(map[string]struct{})
	for index := 0; index < len(tokens); {
		if tokens[index] == "" {
			index++
			continue
		}
		status := tokens[index]
		if !gitStatusPattern.MatchString(status) {
			if outputTruncated {
				return rows, true, nil
			}
			return nil, false, errors.New("malformed Git status output")
		}
		index++
		oldPath, newPath := "", ""
		if status[0] == 'R' || status[0] == 'C' {
			if index+1 >= len(tokens) || tokens[index] == "" || tokens[index+1] == "" {
				if outputTruncated {
					return rows, true, nil
				}
				return nil, false, errors.New("malformed Git rename output")
			}
			oldPath, newPath = tokens[index], tokens[index+1]
			index += 2
		} else {
			if index >= len(tokens) || tokens[index] == "" {
				if outputTruncated {
					return rows, true, nil
				}
				return nil, false, errors.New("malformed Git status path")
			}
			newPath = tokens[index]
			index++
		}
		if !utf8.ValidString(oldPath) || !utf8.ValidString(newPath) {
			return nil, false, errGitPathEncoding
		}
		identity := oldPath + "\x00" + newPath
		if _, exists := identities[identity]; exists {
			return nil, false, errors.New("duplicate Git status path")
		}
		identities[identity] = struct{}{}
		rows = append(rows, map[string]string{"status": status[:1], "old_path": oldPath, "path": newPath})
	}
	return rows, truncated, nil
}

func capGitCommitRows(rows []map[string]string) ([]map[string]string, bool) {
	if len(rows) <= gitCommitMaxFiles {
		return rows, false
	}
	return rows[:gitCommitMaxFiles], true
}

func parseGitNumstat(raw []byte, outputTruncated bool) (map[string]gitCommitCount, bool, error) {
	if !outputTruncated && len(raw) > 0 && raw[len(raw)-1] != 0 {
		return nil, false, errors.New("malformed Git numstat terminator")
	}
	tokens := strings.Split(string(raw), "\x00")
	truncated := outputTruncated
	if outputTruncated && len(raw) > 0 && raw[len(raw)-1] != 0 && len(tokens) > 0 {
		tokens = tokens[:len(tokens)-1]
	}
	counts := map[string]gitCommitCount{}
	for index := 0; index < len(tokens); {
		if tokens[index] == "" {
			index++
			continue
		}
		fields := strings.SplitN(tokens[index], "\t", 3)
		if len(fields) != 3 {
			if outputTruncated {
				return counts, true, nil
			}
			return nil, false, errors.New("malformed Git numstat output")
		}
		index++
		oldPath, newPath := fields[2], fields[2]
		if fields[2] == "" {
			if index+1 >= len(tokens) || tokens[index] == "" || tokens[index+1] == "" {
				if outputTruncated {
					return counts, true, nil
				}
				return nil, false, errors.New("malformed Git numstat rename output")
			}
			oldPath, newPath = tokens[index], tokens[index+1]
			index += 2
		}
		key := oldPath + "\x00" + newPath
		if !utf8.ValidString(oldPath) || !utf8.ValidString(newPath) {
			return nil, false, errGitPathEncoding
		}
		addedBinary, removedBinary := fields[0] == "-", fields[1] == "-"
		if addedBinary != removedBinary {
			return nil, false, errors.New("malformed Git numstat counts")
		}
		if _, exists := counts[key]; exists {
			return nil, false, errors.New("duplicate Git numstat path")
		}
		if addedBinary {
			counts[key] = gitCommitCount{binary: true}
			continue
		}
		added, addErr := strconv.Atoi(fields[0])
		removed, removeErr := strconv.Atoi(fields[1])
		if addErr != nil || removeErr != nil || added < 0 || removed < 0 {
			return nil, false, errors.New("malformed Git numstat counts")
		}
		counts[key] = gitCommitCount{added: added, removed: removed}
	}
	return counts, truncated, nil
}

const gitCommitMetadataFormat = "%H%x00%P%x00%an%x00%at%x00%s%x00%B%x00"

// Counts cached by SHA must use the same algorithm regardless of local diff preferences.
func gitCommitDiffOptions() []string {
	return []string{"--diff-algorithm=myers", "--no-indent-heuristic", "--no-ext-diff", "--no-textconv"}
}

func gitCommitProductArgs(commit, relative string) []string {
	args := []string{"show", "--root", "--diff-merges=first-parent", "--raw", "--numstat", "-z", "--find-renames", "--find-copies-harder", "--format=" + gitCommitMetadataFormat}
	args = append(args, gitCommitDiffOptions()...)
	args = append(args, commit)
	return append(args, historyPathArgs(relative)...)
}

// splitGitCommitProduct separates commit metadata from raw status and numstat rows emitted by
// one bounded `git show`, then restores the terminator expected by the existing metadata parser.
func splitGitCommitProduct(raw []byte) ([]byte, []byte, bool) {
	separator := bytes.Index(raw, []byte("\n\x00\x00\n"))
	if separator < 0 {
		return nil, nil, false
	}
	metadata := make([]byte, 0, separator+3)
	metadata = append(metadata, raw[:separator+2]...)
	metadata = append(metadata, '\n')
	return metadata, raw[separator+4:], true
}

func gitDiffTreeArgs(parent, commit, relative string, options ...string) []string {
	args := append([]string{"diff-tree", "-r", "--no-commit-id"}, options...)
	if parent == "" {
		args = append(args, "--root", commit)
	} else {
		args = append(args, parent, commit)
	}
	return append(args, historyPathArgs(relative)...)
}

func parseGitRawNumstat(raw []byte, outputTruncated bool) ([]map[string]string, map[string]gitCommitCount, bool, error) {
	if !outputTruncated && len(raw) > 0 && raw[len(raw)-1] != 0 {
		return nil, nil, false, errors.New("malformed Git diff terminator")
	}
	tokens := strings.Split(string(raw), "\x00")
	rows := make([]map[string]string, 0)
	counts := make(map[string]gitCommitCount)
	truncated := outputTruncated
	for index := 0; index < len(tokens); {
		token := tokens[index]
		if token == "" {
			index++
			continue
		}
		if strings.HasPrefix(token, ":") {
			fields := strings.Fields(token)
			if len(fields) < 5 || !gitStatusPattern.MatchString(fields[4]) {
				if outputTruncated {
					return rows, counts, true, nil
				}
				return nil, nil, false, errors.New("malformed Git raw status output")
			}
			status := fields[4]
			index++
			if index >= len(tokens) || tokens[index] == "" {
				if outputTruncated {
					return rows, counts, true, nil
				}
				return nil, nil, false, errors.New("malformed Git raw status path")
			}
			oldPath, path := "", tokens[index]
			index++
			if status[0] == 'R' || status[0] == 'C' {
				if index >= len(tokens) || tokens[index] == "" {
					if outputTruncated {
						return rows, counts, true, nil
					}
					return nil, nil, false, errors.New("malformed Git raw rename output")
				}
				oldPath, path = path, tokens[index]
				index++
			}
			if !utf8.ValidString(oldPath) || !utf8.ValidString(path) {
				return nil, nil, false, errGitPathEncoding
			}
			rows = append(rows, map[string]string{"status": status[:1], "old_path": oldPath, "path": path})
			continue
		}
		statFields := strings.SplitN(token, "\t", 3)
		if len(statFields) != 3 || (statFields[0] != "-" && !allDigits(statFields[0])) {
			if outputTruncated {
				truncated = true
				return rows, counts, truncated, nil
			}
			return nil, nil, false, errors.New("malformed Git numstat output")
		}
		index++
		oldPath, path := statFields[2], statFields[2]
		if statFields[2] == "" {
			if index+1 >= len(tokens) || tokens[index] == "" || tokens[index+1] == "" {
				if outputTruncated {
					return rows, counts, true, nil
				}
				return nil, nil, false, errors.New("malformed Git numstat rename output")
			}
			oldPath, path = tokens[index], tokens[index+1]
			index += 2
		}
		if !utf8.ValidString(oldPath) || !utf8.ValidString(path) {
			return nil, nil, false, errGitPathEncoding
		}
		addedBinary, removedBinary := statFields[0] == "-", statFields[1] == "-"
		if addedBinary != removedBinary {
			return nil, nil, false, errors.New("malformed Git numstat counts")
		}
		key := oldPath + "\x00" + path
		if _, exists := counts[key]; exists {
			return nil, nil, false, errors.New("duplicate Git numstat path")
		}
		if addedBinary {
			counts[key] = gitCommitCount{binary: true}
			continue
		}
		added, addErr := strconv.Atoi(statFields[0])
		removed, removeErr := strconv.Atoi(statFields[1])
		if addErr != nil || removeErr != nil || added < 0 || removed < 0 {
			return nil, nil, false, errors.New("malformed Git numstat counts")
		}
		counts[key] = gitCommitCount{added: added, removed: removed}
	}
	return rows, counts, truncated, nil
}

func (s *Service) GitCommit(ctx context.Context, raw, commitRaw, headRaw string, repositoryIdentity ...string) (map[string]any, *Error) {
	if finderErr := validateGitObjectID(commitRaw, "fs.error.gitCommitUnknown"); finderErr != nil {
		return nil, finderErr
	}
	if finderErr := validateGitObjectID(headRaw, "fs.error.gitHistoryStale"); finderErr != nil {
		return nil, finderErr
	}
	scope, finderErr := s.openGitHistoryScopeWithOptions(ctx, raw, gitHistoryScopeOptions{})
	if finderErr != nil {
		return nil, finderErr
	}
	defer scope.close()
	gitCtx, cancel := gitViewContext(ctx)
	defer cancel()
	if finderErr := s.prepareGitHistoryNamespace(gitCtx, scope); finderErr != nil {
		return nil, finderErr
	}
	snapshot, finderErr := s.captureGitSnapshot(gitCtx, scope, "fs.error.gitCommitFailed")
	if finderErr != nil {
		return nil, finderErr
	}
	currentHead := snapshot.head
	if currentHead == "" {
		return nil, &Error{Status: http.StatusConflict, Code: "git_history_stale", MessageKey: "fs.error.gitHistoryStale", Path: scope.path, Message: "Git history snapshot is stale"}
	}
	if len(repositoryIdentity) > 0 && strings.TrimSpace(repositoryIdentity[0]) != "" && repositoryIdentity[0] != scope.identity {
		return nil, &Error{Status: http.StatusConflict, Code: "git_history_stale", MessageKey: "fs.error.gitHistoryStale", Path: scope.path, Message: "Git repository snapshot is stale"}
	}
	if finderErr := s.validateFrozenGitHistoryHead(gitCtx, snapshot, headRaw); finderErr != nil {
		return nil, finderErr
	}
	// Git treats shallow commits as roots, so a changed boundary changes their diff product.
	shallowBoundary, boundaryErr := gitHistoryShallowBoundarySignature(scope.namespace)
	if boundaryErr != nil {
		return nil, gitNamespaceChanged(scope.path)
	}
	scope.shallowBoundary = shallowBoundary
	if commitRaw != headRaw {
		found, inHistory, reachabilityErr := s.gitCommitReachability(gitCtx, scope, commitRaw, headRaw)
		if reachabilityErr != nil {
			return nil, reachabilityErr
		}
		if !found {
			return nil, &Error{Status: http.StatusNotFound, Code: "unknown_commit", MessageKey: "fs.error.gitCommitUnknown", Path: scope.path, Message: "unknown Git commit"}
		}
		if !inHistory {
			return nil, &Error{Status: http.StatusNotFound, Code: "unknown_commit", MessageKey: "fs.error.gitCommitUnknown", Path: scope.path, Message: "commit is outside the selected history"}
		}
	}
	// Reuse commit detail across HEADs while the repository identity and shallow boundary stay fixed.
	cacheKey := gitProductCacheKey("commit", scope.repo, scope.path, scope.identity, scope.relative, scope.shallowBoundary, commitRaw)
	if cached, ok := s.cachedGitCommit(cacheKey); ok {
		return cached, nil
	}
	productRaw, stderr, err := runGitMetadataBounded(gitCtx, scope.directory, nil, gitCommitProductArgs(commitRaw, scope.relative), gitCommitProductMaxOutput, scope.namespace)
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return nil, cancelled(scope.path)
	}
	if errors.Is(err, errGitNamespaceChanged) {
		return nil, gitNamespaceChanged(scope.path)
	}
	productOutputTruncated := false
	if errors.Is(err, errGitOutputLimit) && len(stderr) == 0 && len(productRaw) > 0 {
		productOutputTruncated = true
		err = nil
	}
	if err != nil {
		if errors.Is(err, errGitOutputLimit) {
			return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitCommitTooLarge", Path: scope.path, Message: "Git commit is too large"}
		}
		message := "Git commit detail failed"
		if strings.TrimSpace(string(stderr)) != "" {
			message = strings.TrimSpace(string(stderr))
		}
		return nil, &Error{Status: http.StatusInternalServerError, Code: "git_commit_failed", MessageKey: "fs.error.gitCommitFailed", Path: scope.path, Retryable: true, Message: message}
	}
	metadataRaw, diffRaw, productFound := splitGitCommitProduct(productRaw)
	if !productFound {
		if productOutputTruncated {
			return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitCommitTooLarge", Path: scope.path, Message: "Git commit metadata is too large"}
		}
		return nil, &Error{Status: http.StatusInternalServerError, Code: "git_commit_failed", MessageKey: "fs.error.gitCommitFailed", Path: scope.path, Retryable: true, Message: "Git commit detail output was malformed"}
	}
	metadata, parseErr := parseGitCommitMetadata(metadataRaw)
	if parseErr != nil || metadata["sha"] != commitRaw {
		return nil, &Error{Status: http.StatusNotFound, Code: "unknown_commit", MessageKey: "fs.error.gitCommitUnknown", Path: scope.path, Message: "unknown Git commit"}
	}
	parents, _ := metadata["parents"].([]string)
	parent := ""
	if len(parents) > 0 {
		parent = parents[0]
	}
	statusRows, counts, diffTruncated, parseErr := parseGitRawNumstat(diffRaw, productOutputTruncated)
	if parseErr != nil {
		if errors.Is(parseErr, errGitPathEncoding) {
			return nil, &Error{Status: http.StatusUnprocessableEntity, Code: "git_path_encoding", MessageKey: "fs.error.gitPathEncoding", Path: scope.path, Message: parseErr.Error()}
		}
		return nil, &Error{Status: http.StatusInternalServerError, Code: "git_commit_failed", MessageKey: "fs.error.gitCommitFailed", Path: scope.path, Retryable: true, Message: parseErr.Error()}
	}
	statusKeys := make(map[string]struct{}, len(statusRows))
	for _, statusRow := range statusRows {
		oldPath, path := statusRow["old_path"], statusRow["path"]
		keyOldPath := oldPath
		if keyOldPath == "" {
			keyOldPath = path
		}
		statusKeys[keyOldPath+"\x00"+path] = struct{}{}
	}
	if !diffTruncated && len(statusKeys) != len(counts) {
		return nil, &Error{Status: http.StatusInternalServerError, Code: "git_commit_failed", MessageKey: "fs.error.gitCommitFailed", Path: scope.path, Retryable: true, Message: "Git status and numstat output disagree"}
	}
	for key := range statusKeys {
		if _, present := counts[key]; !present {
			if diffTruncated {
				continue
			}
			return nil, &Error{Status: http.StatusInternalServerError, Code: "git_commit_failed", MessageKey: "fs.error.gitCommitFailed", Path: scope.path, Retryable: true, Message: "Git status and numstat output disagree"}
		}
	}
	statusRows, rowsTruncated := capGitCommitRows(statusRows)
	filesTruncated := diffTruncated || rowsTruncated
	rows := make([]map[string]any, 0, len(statusRows))
	for _, statusRow := range statusRows {
		oldPath, path := statusRow["old_path"], statusRow["path"]
		keyOldPath := oldPath
		if keyOldPath == "" {
			keyOldPath = path
		}
		key := keyOldPath + "\x00" + path
		count, ok := counts[key]
		row := map[string]any{"status": statusRow["status"], "path": path, "old_path": oldPath, "counts_available": ok, "binary": false}
		if ok {
			row["binary"] = count.binary
			row["added"], row["removed"] = count.added, count.removed
		} else {
			row["added"], row["removed"] = nil, nil
		}
		rows = append(rows, row)
	}
	if len(rows) == 0 && diffTruncated {
		return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitCommitTooLarge", Path: scope.path, Message: "Git commit file list exceeds the output limit"}
	}
	if len(rows) == 0 {
		return nil, &Error{Status: http.StatusNotFound, Code: "commit_outside_scope", MessageKey: "fs.error.gitCommitScope", Path: scope.path, Message: "commit does not change the selected history scope"}
	}
	metadata["repo"] = scope.repo
	metadata["shallow_boundary"] = scope.shallowBoundary
	metadata["scope_path"] = scope.relative
	metadata["from_ref"] = parent
	if parent == "" {
		metadata["from_ref"] = gitEmptyTreeOID(commitRaw)
	}
	metadata["to_ref"] = commitRaw
	metadata["files"] = rows
	metadata["files_truncated"] = filesTruncated
	messageTruncated, _ := metadata["message_truncated"].(bool)
	metadata["truncated"] = messageTruncated || filesTruncated
	encoded, _ := json.Marshal(metadata)
	if len(encoded) > gitCommitMaxPayload {
		metadata["files_truncated"] = true
		metadata["truncated"] = true
		low, high := 0, len(rows)
		for low < high {
			middle := (low + high + 1) / 2
			metadata["files"] = rows[:middle]
			candidate, _ := json.Marshal(metadata)
			if len(candidate) <= gitCommitMaxPayload {
				low = middle
			} else {
				high = middle - 1
			}
		}
		metadata["files"] = rows[:low]
		encoded, _ = json.Marshal(metadata)
		if len(encoded) > gitCommitMaxPayload {
			return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitCommitTooLarge", Path: scope.path, Message: "Git commit metadata is too large"}
		}
	}
	if !gitHistoryShallowBoundaryMatches(scope.namespace, scope.shallowBoundary) {
		return nil, gitNamespaceChanged(scope.path)
	}
	s.cacheGitCommit(cacheKey, metadata)
	return metadata, nil
}
