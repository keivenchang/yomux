// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

const (
	gitHistoryDefaultLimit = 200
	gitHistoryMaxLimit     = 200
	gitHistoryMaxOutput    = 4 * 1024 * 1024
	gitHistoryMaxPayload   = 384 * 1024
	gitCommitMaxFiles      = 1000
	gitCommitMaxOutput     = 8 * 1024 * 1024
	gitCommitMaxPayload    = 384 * 1024
)

var gitObjectIDPattern = regexp.MustCompile(`^(?:[0-9a-f]{40}|[0-9a-f]{64})$`)
var gitShortStatPattern = regexp.MustCompile(`([0-9]+) files? changed(?:, ([0-9]+) insertions?\(\+\))?(?:, ([0-9]+) deletions?\(-\))?`)

type gitHistoryScope struct {
	path      string
	repo      string
	identity  string
	relative  string
	directory *os.File
	namespace *gitNamespace
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
	directory, requested, resolved, finderErr := s.open(raw, true)
	if finderErr != nil {
		return nil, finderErr
	}
	rootBytes, _, gitErr := runGitBounded(context.Background(), directory, nil, []string{"rev-parse", "--show-toplevel"}, 4096)
	if gitErr != nil {
		_ = directory.Close()
		return nil, &Error{Status: http.StatusBadRequest, Code: "not_git_repo", MessageKey: "fs.error.notGitRepo", Path: requested, Message: "path is not in a Git repository"}
	}
	root, err := filepath.Abs(filepath.Clean(strings.TrimSpace(string(rootBytes))))
	if err != nil || !s.allowed(root) {
		_ = directory.Close()
		return nil, forbidden(requested, "fs.error.outsideRoots", "Git repository is outside configured filesystem roots")
	}
	namespace, finderErr := s.openGitNamespace(directory, resolved, requested)
	if finderErr != nil {
		_ = directory.Close()
		return nil, finderErr
	}
	relative, err := filepath.Rel(root, resolved)
	if err != nil || filepath.IsAbs(relative) || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
		namespace.close()
		_ = directory.Close()
		return nil, forbidden(requested, "fs.error.outsideRoots", "path is outside the Git repository")
	}
	if relative == "." {
		relative = ""
	}
	relative = filepath.ToSlash(relative)
	if !utf8.ValidString(relative) {
		namespace.close()
		_ = directory.Close()
		return nil, &Error{Status: http.StatusUnprocessableEntity, Code: "git_path_encoding", MessageKey: "fs.error.gitPathEncoding", Path: requested, Message: errGitPathEncoding.Error()}
	}
	return &gitHistoryScope{path: requested, repo: root, identity: namespace.identity(), relative: relative, directory: directory, namespace: namespace}, nil
}

func encodeGitHistoryCursor(cursor gitHistoryCursor) (string, error) {
	data, err := json.Marshal(cursor)
	if err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(data), nil
}

func decodeGitHistoryCursor(raw string) (gitHistoryCursor, error) {
	if len(raw) > 2048 {
		return gitHistoryCursor{}, errors.New("Git history cursor is too large")
	}
	data, err := base64.RawURLEncoding.DecodeString(raw)
	if err != nil {
		return gitHistoryCursor{}, err
	}
	var cursor gitHistoryCursor
	if err := json.Unmarshal(data, &cursor); err != nil || cursor.Repo == "" || cursor.Identity == "" || cursor.Head == "" || cursor.Offset < 0 || cursor.Offset > 1_000_000 {
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
	if err != nil || value < 1 {
		return 0, errors.New("limit must be a positive integer")
	}
	if value > gitHistoryMaxLimit {
		value = gitHistoryMaxLimit
	}
	return value, nil
}

func gitHistoryHead(ctx context.Context, scope *gitHistoryScope) (string, *Error) {
	output, stderr, err := runGitBounded(ctx, scope.directory, nil, []string{"rev-parse", "--verify", "--quiet", "HEAD"}, 512, scope.namespace)
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

func historyPathArgs(relative string) []string {
	if relative == "" {
		return nil
	}
	return []string{"--", relative}
}

func parseGitShortStat(raw string) (files, added, removed int) {
	match := gitShortStatPattern.FindStringSubmatch(raw)
	if len(match) == 0 {
		return 0, 0, 0
	}
	files, _ = strconv.Atoi(match[1])
	if match[2] != "" {
		added, _ = strconv.Atoi(match[2])
	}
	if match[3] != "" {
		removed, _ = strconv.Atoi(match[3])
	}
	return files, added, removed
}

func parseGitHistory(raw []byte) ([]map[string]any, error) {
	parts := strings.Split(string(raw), "record\x00")
	commits := make([]map[string]any, 0, len(parts)-1)
	for _, part := range parts[1:] {
		fields := strings.SplitN(part, "\x00", 7)
		if len(fields) < 7 {
			return nil, errors.New("malformed Git history output")
		}
		sha, short, parents, author, authoredAt, subject := fields[0], fields[1], fields[2], fields[3], fields[4], fields[5]
		rest := fields[6]
		if index := strings.IndexByte(rest, 0); index >= 0 {
			rest = rest[index+1:]
		} else {
			return nil, errors.New("malformed Git history terminator")
		}
		if index := strings.IndexByte(rest, 0); index >= 0 {
			rest = rest[index+1:]
		}
		at, err := strconv.ParseInt(strings.TrimSpace(authoredAt), 10, 64)
		if err != nil || !gitObjectIDPattern.MatchString(sha) {
			return nil, errors.New("malformed Git history metadata")
		}
		files, added, removed := parseGitShortStat(rest)
		parentList := []string{}
		if strings.TrimSpace(parents) != "" {
			parentList = strings.Fields(parents)
		}
		commits = append(commits, map[string]any{
			"sha": sha, "short": short, "parents": parentList, "subject": subject,
			"author": author, "authored_at": at, "files": files, "added": added,
			"removed": removed, "binary_files": 0,
		})
	}
	return commits, nil
}

func (s *Service) GitHistory(ctx context.Context, raw, limitRaw, cursorRaw string) (map[string]any, *Error) {
	limit, err := gitHistoryLimit(limitRaw)
	if err != nil {
		return nil, invalid(raw, err.Error())
	}
	scope, finderErr := s.openGitHistoryScope(raw)
	if finderErr != nil {
		return nil, finderErr
	}
	defer scope.close()
	gitCtx, cancel := gitViewContext(ctx)
	defer cancel()
	currentHead, finderErr := gitHistoryHead(gitCtx, scope)
	if finderErr != nil {
		return nil, finderErr
	}
	if currentHead == "" {
		if cursorRaw != "" {
			return nil, &Error{Status: http.StatusConflict, Code: "git_history_stale", MessageKey: "fs.error.gitHistoryStale", Path: scope.path, Message: "Git history snapshot is stale"}
		}
		return map[string]any{"path": scope.path, "repo": scope.repo, "repository_identity": scope.identity, "relative_path": scope.relative, "head": "", "hosted_remote": nil, "snapshot_cursor": "", "commits": []map[string]any{}, "next_cursor": "", "truncated": false}, nil
	}
	frozenHead, offset := currentHead, 0
	if cursorRaw != "" {
		cursor, cursorErr := decodeGitHistoryCursor(cursorRaw)
		if cursorErr != nil || cursor.Repo != scope.repo || cursor.Identity != scope.identity || cursor.Relative != scope.relative {
			return nil, &Error{Status: http.StatusBadRequest, Code: "invalid_cursor", MessageKey: "fs.error.gitHistoryCursor", Path: scope.path, Message: "invalid Git history cursor"}
		}
		if cursor.Head != currentHead {
			found, findErr := s.gitCommitExists(gitCtx, scope, cursor.Head)
			if findErr != nil {
				return nil, findErr
			}
			ancestor, ancestorErr := s.gitCommitAncestor(gitCtx, scope, cursor.Head, currentHead)
			if ancestorErr != nil && found {
				return nil, ancestorErr
			}
			if !found || !ancestor {
				return nil, &Error{Status: http.StatusConflict, Code: "git_history_stale", MessageKey: "fs.error.gitHistoryStale", Path: scope.path, Message: "Git history snapshot is stale"}
			}
		}
		frozenHead, offset = cursor.Head, cursor.Offset
	}
	args := []string{"log", "--topo-order", "--root", "--no-patch", "--shortstat", "-z", "--max-count=" + strconv.Itoa(limit+1), "--skip=" + strconv.Itoa(offset), "--format=record%x00%H%x00%h%x00%P%x00%an%x00%at%x00%s%x00", frozenHead}
	args = append(args, historyPathArgs(scope.relative)...)
	output, stderr, gitErr := runGitBounded(gitCtx, scope.directory, nil, args, gitHistoryMaxOutput, scope.namespace)
	if errors.Is(gitErr, context.Canceled) || errors.Is(gitErr, context.DeadlineExceeded) {
		return nil, cancelled(scope.path)
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
	commits, parseErr := parseGitHistory(output)
	if parseErr != nil {
		return nil, &Error{Status: http.StatusInternalServerError, Code: "git_history_failed", MessageKey: "fs.error.gitHistoryFailed", Path: scope.path, Retryable: true, Message: parseErr.Error()}
	}
	hasMore := len(commits) > limit
	if hasMore {
		commits = commits[:limit]
	}
	nextCursor := ""
	if hasMore && len(commits) > 0 {
		nextCursor, err = encodeGitHistoryCursor(gitHistoryCursor{Repo: scope.repo, Identity: scope.identity, Relative: scope.relative, Head: frozenHead, Offset: offset + len(commits)})
		if err != nil {
			return nil, &Error{Status: http.StatusInternalServerError, Code: "git_history_failed", MessageKey: "fs.error.gitHistoryFailed", Path: scope.path, Retryable: true, Message: "Git history cursor failed"}
		}
	}
	snapshotCursor, _ := encodeGitHistoryCursor(gitHistoryCursor{Repo: scope.repo, Identity: scope.identity, Relative: scope.relative, Head: frozenHead, Offset: 0})
	payload := map[string]any{"path": scope.path, "repo": scope.repo, "repository_identity": scope.identity, "relative_path": scope.relative, "head": frozenHead, "hosted_remote": nil, "snapshot_cursor": snapshotCursor, "commits": commits, "next_cursor": nextCursor, "truncated": false}
	encoded, _ := json.Marshal(payload)
	if len(encoded) > gitHistoryMaxPayload {
		return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitHistoryTooLarge", Path: scope.path, Message: "Git history is too large"}
	}
	return payload, nil
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
	output, stderr, err := runGitBounded(ctx, scope.directory, nil, []string{"rev-parse", "--verify", "--quiet", oid + "^{commit}"}, 512, scope.namespace)
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

func (s *Service) gitCommitAncestor(ctx context.Context, scope *gitHistoryScope, ancestor, descendant string) (bool, *Error) {
	_, stderr, err := runGitBounded(ctx, scope.directory, nil, []string{"merge-base", "--is-ancestor", ancestor, descendant}, 512, scope.namespace)
	if err == nil {
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
	return false, &Error{Status: http.StatusInternalServerError, Code: "git_commit_failed", MessageKey: "fs.error.gitCommitFailed", Path: scope.path, Retryable: true, Message: "Git ancestry lookup failed"}
}

func parseGitCommitMetadata(raw []byte) (map[string]any, error) {
	fields := strings.SplitN(string(raw), "\x00", 6)
	if len(fields) < 6 {
		return nil, errors.New("malformed Git commit metadata")
	}
	if index := strings.IndexByte(fields[5], 0); index >= 0 {
		fields[5] = fields[5][:index]
	}
	at, err := strconv.ParseInt(strings.TrimSpace(fields[3]), 10, 64)
	if err != nil {
		return nil, errors.New("malformed Git commit timestamp")
	}
	parents := []string{}
	if strings.TrimSpace(fields[1]) != "" {
		parents = strings.Fields(fields[1])
	}
	return map[string]any{"sha": fields[0], "parents": parents, "author": fields[2], "authored_at": at, "subject": fields[4], "message": strings.TrimRight(fields[5], "\n")}, nil
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

func parseGitNameStatus(raw []byte) ([]map[string]string, error) {
	tokens := strings.Split(string(raw), "\x00")
	rows := make([]map[string]string, 0)
	for index := 0; index < len(tokens); {
		if tokens[index] == "" {
			index++
			continue
		}
		status := tokens[index]
		if len(status) == 0 || status[0] < 'A' || status[0] > 'Z' {
			return nil, errors.New("malformed Git status output")
		}
		index++
		oldPath, newPath := "", ""
		if status[0] == 'R' || status[0] == 'C' {
			if index+1 >= len(tokens) || tokens[index] == "" || tokens[index+1] == "" {
				return nil, errors.New("malformed Git rename output")
			}
			oldPath, newPath = tokens[index], tokens[index+1]
			index += 2
		} else {
			if index >= len(tokens) || tokens[index] == "" {
				return nil, errors.New("malformed Git status path")
			}
			newPath = tokens[index]
			index++
		}
		if !utf8.ValidString(oldPath) || !utf8.ValidString(newPath) {
			return nil, errGitPathEncoding
		}
		rows = append(rows, map[string]string{"status": status[:1], "old_path": oldPath, "path": newPath})
	}
	return rows, nil
}

func capGitCommitRows(rows []map[string]string) ([]map[string]string, bool) {
	if len(rows) <= gitCommitMaxFiles {
		return rows, false
	}
	return rows[:gitCommitMaxFiles], true
}

func parseGitNumstat(raw []byte) (map[string]gitCommitCount, error) {
	tokens := strings.Split(string(raw), "\x00")
	counts := map[string]gitCommitCount{}
	for index := 0; index < len(tokens); {
		if tokens[index] == "" {
			index++
			continue
		}
		fields := strings.SplitN(tokens[index], "\t", 3)
		if len(fields) != 3 {
			return nil, errors.New("malformed Git numstat output")
		}
		index++
		oldPath, newPath := fields[2], fields[2]
		if fields[2] == "" {
			if index+1 >= len(tokens) || tokens[index] == "" || tokens[index+1] == "" {
				return nil, errors.New("malformed Git numstat rename output")
			}
			oldPath, newPath = tokens[index], tokens[index+1]
			index += 2
		}
		key := oldPath + "\x00" + newPath
		if !utf8.ValidString(oldPath) || !utf8.ValidString(newPath) {
			return nil, errGitPathEncoding
		}
		if fields[0] == "-" || fields[1] == "-" {
			counts[key] = gitCommitCount{binary: true}
			continue
		}
		added, addErr := strconv.Atoi(fields[0])
		removed, removeErr := strconv.Atoi(fields[1])
		if addErr != nil || removeErr != nil || added < 0 || removed < 0 {
			return nil, errors.New("malformed Git numstat counts")
		}
		counts[key] = gitCommitCount{added: added, removed: removed}
	}
	return counts, nil
}

func gitCommitDiffArgs(mode, parent, commit, relative string) []string {
	args := []string{"diff-tree", "-r", "--no-commit-id", "--" + mode, "-z", "--find-renames", "--find-copies-harder", "--no-ext-diff", "--no-textconv"}
	if parent == "" {
		args = append(args, "--root", commit)
	} else {
		args = append(args, parent, commit)
	}
	return append(args, historyPathArgs(relative)...)
}

func (s *Service) GitCommit(ctx context.Context, raw, commitRaw, headRaw string, repositoryIdentity ...string) (map[string]any, *Error) {
	if finderErr := validateGitObjectID(commitRaw, "fs.error.gitCommitUnknown"); finderErr != nil {
		return nil, finderErr
	}
	if finderErr := validateGitObjectID(headRaw, "fs.error.gitHistoryStale"); finderErr != nil {
		return nil, finderErr
	}
	scope, finderErr := s.openGitHistoryScope(raw)
	if finderErr != nil {
		return nil, finderErr
	}
	defer scope.close()
	gitCtx, cancel := gitViewContext(ctx)
	defer cancel()
	currentHead, finderErr := gitHistoryHead(gitCtx, scope)
	if finderErr != nil {
		return nil, finderErr
	}
	if currentHead == "" {
		return nil, &Error{Status: http.StatusConflict, Code: "git_history_stale", MessageKey: "fs.error.gitHistoryStale", Path: scope.path, Message: "Git history snapshot is stale"}
	}
	if len(repositoryIdentity) > 0 && strings.TrimSpace(repositoryIdentity[0]) != "" && repositoryIdentity[0] != scope.identity {
		return nil, &Error{Status: http.StatusConflict, Code: "git_history_stale", MessageKey: "fs.error.gitHistoryStale", Path: scope.path, Message: "Git repository snapshot is stale"}
	}
	frozenFound, frozenErr := s.gitCommitExists(gitCtx, scope, headRaw)
	if frozenErr != nil {
		return nil, frozenErr
	}
	frozenAncestor, frozenAncestorErr := s.gitCommitAncestor(gitCtx, scope, headRaw, currentHead)
	if frozenAncestorErr != nil && frozenFound {
		return nil, frozenAncestorErr
	}
	if !frozenFound || !frozenAncestor {
		return nil, &Error{Status: http.StatusConflict, Code: "git_history_stale", MessageKey: "fs.error.gitHistoryStale", Path: scope.path, Message: "Git history snapshot is stale"}
	}
	found, finderErr := s.gitCommitExists(gitCtx, scope, commitRaw)
	if finderErr != nil {
		return nil, finderErr
	}
	if !found {
		return nil, &Error{Status: http.StatusNotFound, Code: "unknown_commit", MessageKey: "fs.error.gitCommitUnknown", Path: scope.path, Message: "unknown Git commit"}
	}
	inHistory, finderErr := s.gitCommitAncestor(gitCtx, scope, commitRaw, headRaw)
	if finderErr != nil {
		return nil, finderErr
	}
	if !inHistory {
		return nil, &Error{Status: http.StatusNotFound, Code: "unknown_commit", MessageKey: "fs.error.gitCommitUnknown", Path: scope.path, Message: "commit is outside the selected history"}
	}
	metadataRaw, stderr, err := runGitBounded(gitCtx, scope.directory, nil, []string{"show", "--no-patch", "--format=%H%x00%P%x00%an%x00%at%x00%s%x00%B%x00", commitRaw}, 64*1024, scope.namespace)
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return nil, cancelled(scope.path)
	}
	if err != nil {
		if errors.Is(err, errGitNamespaceChanged) {
			return nil, gitNamespaceChanged(scope.path)
		}
		if errors.Is(err, errGitOutputLimit) {
			return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitCommitTooLarge", Path: scope.path, Message: "Git commit is too large"}
		}
		message := "Git commit metadata failed"
		if strings.TrimSpace(string(stderr)) != "" {
			message = strings.TrimSpace(string(stderr))
		}
		return nil, &Error{Status: http.StatusInternalServerError, Code: "git_commit_failed", MessageKey: "fs.error.gitCommitFailed", Path: scope.path, Retryable: true, Message: message}
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
	statusRaw, statusStderr, statusErr := runGitBounded(gitCtx, scope.directory, nil, gitCommitDiffArgs("name-status", parent, commitRaw, scope.relative), gitCommitMaxOutput, scope.namespace)
	if errors.Is(statusErr, context.Canceled) || errors.Is(statusErr, context.DeadlineExceeded) {
		return nil, cancelled(scope.path)
	}
	if statusErr != nil {
		if errors.Is(statusErr, errGitNamespaceChanged) {
			return nil, gitNamespaceChanged(scope.path)
		}
		if errors.Is(statusErr, errGitOutputLimit) {
			return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitCommitTooLarge", Path: scope.path, Message: "Git commit is too large"}
		}
		return nil, &Error{Status: http.StatusInternalServerError, Code: "git_commit_failed", MessageKey: "fs.error.gitCommitFailed", Path: scope.path, Retryable: true, Message: strings.TrimSpace(string(statusStderr))}
	}
	numstatRaw, numstatStderr, numstatErr := runGitBounded(gitCtx, scope.directory, nil, gitCommitDiffArgs("numstat", parent, commitRaw, scope.relative), gitCommitMaxOutput, scope.namespace)
	if errors.Is(numstatErr, context.Canceled) || errors.Is(numstatErr, context.DeadlineExceeded) {
		return nil, cancelled(scope.path)
	}
	if numstatErr != nil {
		if errors.Is(numstatErr, errGitNamespaceChanged) {
			return nil, gitNamespaceChanged(scope.path)
		}
		if errors.Is(numstatErr, errGitOutputLimit) {
			return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitCommitTooLarge", Path: scope.path, Message: "Git commit is too large"}
		}
		return nil, &Error{Status: http.StatusInternalServerError, Code: "git_commit_failed", MessageKey: "fs.error.gitCommitFailed", Path: scope.path, Retryable: true, Message: strings.TrimSpace(string(numstatStderr))}
	}
	statusRows, parseErr := parseGitNameStatus(statusRaw)
	if parseErr != nil {
		if errors.Is(parseErr, errGitPathEncoding) {
			return nil, &Error{Status: http.StatusUnprocessableEntity, Code: "git_path_encoding", MessageKey: "fs.error.gitPathEncoding", Path: scope.path, Message: parseErr.Error()}
		}
		return nil, &Error{Status: http.StatusInternalServerError, Code: "git_commit_failed", MessageKey: "fs.error.gitCommitFailed", Path: scope.path, Retryable: true, Message: parseErr.Error()}
	}
	counts, parseErr := parseGitNumstat(numstatRaw)
	if parseErr != nil {
		if errors.Is(parseErr, errGitPathEncoding) {
			return nil, &Error{Status: http.StatusUnprocessableEntity, Code: "git_path_encoding", MessageKey: "fs.error.gitPathEncoding", Path: scope.path, Message: parseErr.Error()}
		}
		return nil, &Error{Status: http.StatusInternalServerError, Code: "git_commit_failed", MessageKey: "fs.error.gitCommitFailed", Path: scope.path, Retryable: true, Message: parseErr.Error()}
	}
	statusRows, filesTruncated := capGitCommitRows(statusRows)
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
	if len(rows) == 0 {
		return nil, &Error{Status: http.StatusNotFound, Code: "commit_outside_scope", MessageKey: "fs.error.gitCommitScope", Path: scope.path, Message: "commit does not change the selected history scope"}
	}
	metadata["repo"] = scope.repo
	metadata["scope_path"] = scope.relative
	metadata["from_ref"] = parent
	if parent == "" {
		metadata["from_ref"] = gitEmptyTreeOID(commitRaw)
	}
	metadata["to_ref"] = commitRaw
	metadata["files"] = rows
	metadata["message_truncated"] = false
	metadata["files_truncated"] = filesTruncated
	metadata["truncated"] = metadata["files_truncated"]
	encoded, _ := json.Marshal(metadata)
	if len(encoded) > gitCommitMaxPayload {
		return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.gitCommitTooLarge", Path: scope.path, Message: "Git commit is too large"}
	}
	return metadata, nil
}
