// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const (
	MaxWriteBytes        = 5 * 1024 * 1024
	MaxUploadBytes       = 300 * 1024 * 1024
	MaxUploadFiles       = 16
	MaxMutationEntries   = 100_000
	MaxBatchRequests     = 64
	MaxCandidatePaths    = 8
	MaxSearchFiles       = 50_000
	MaxSearchDirectories = 20_000
)

var searchExcludedDirectoryNames = map[string]struct{}{
	".git": {}, ".hg": {}, ".svn": {}, ".jj": {}, ".ssh": {}, ".gnupg": {}, ".aws": {},
	".azure": {}, ".kube": {}, ".docker": {}, ".ngc": {}, ".cache": {}, ".mypy_cache": {},
	".pytest_cache": {}, ".ruff_cache": {}, ".tox": {}, ".venv": {}, ".coverage": {},
	".hypothesis": {}, ".nox": {}, ".pnpm-store": {}, ".yarn": {}, ".vscode": {}, ".idea": {},
	".uploads": {}, "venv": {}, "node_modules": {}, "__pycache__": {}, "dist": {}, "build": {},
	"target": {}, "coverage": {}, "htmlcov": {},
}

func isSearchExcludedDirectory(name string) bool {
	_, excluded := searchExcludedDirectoryNames[name]
	return excluded
}

const defaultUploadFilenameTemplate = "{date:%Y%m%d}-{seq:03d}-{name}{ext}"

type searchSortKey struct {
	minimum   int
	total     int
	negative  int
	depth     int
	length    int
	lowerName string
}

func compactSearchText(value string) string {
	return strings.ToLower(strings.Join(strings.Fields(value), ""))
}

func alnumSearchText(value string) string {
	var builder strings.Builder
	for _, value := range strings.ToLower(value) {
		if (value >= 'a' && value <= 'z') || (value >= '0' && value <= '9') {
			builder.WriteRune(value)
		}
	}
	return builder.String()
}

func fuzzySubsequenceSpan(query, text string) (int, bool) {
	needle := compactSearchText(query)
	if needle == "" {
		return 0, true
	}
	haystack := strings.ToLower(text)
	position, start, end := 0, -1, -1
	for _, value := range needle {
		found := strings.IndexRune(haystack[position:], value)
		if found < 0 {
			return 0, false
		}
		if start < 0 {
			start = position + found
		}
		end = position + found
		position = end + 1
	}
	return end - start + 1, true
}

func searchTokenNameRank(token, name string) (int, bool) {
	needle := compactSearchText(token)
	if needle == "" {
		return 0, true
	}
	basename := compactSearchText(name)
	stemName := name
	if extension := filepath.Ext(name); extension != "" {
		stemName = strings.TrimSuffix(name, extension)
	}
	stem := compactSearchText(stemName)
	doitNeedle := alnumSearchText(token)
	if !strings.HasPrefix(doitNeedle, "doit") || len(doitNeedle) < 4 {
		doitNeedle = ""
	}
	if doitNeedle != "" {
		basenameAlnum, stemAlnum := alnumSearchText(name), alnumSearchText(stemName)
		if doitNeedle == stemAlnum || doitNeedle == basenameAlnum {
			return 0, true
		}
		if strings.HasPrefix(stemAlnum, doitNeedle) || strings.HasPrefix(basenameAlnum, doitNeedle) {
			return 10, true
		}
		if index := strings.Index(stemAlnum, doitNeedle); index >= 0 {
			return 20 + index, true
		}
		if index := strings.Index(basenameAlnum, doitNeedle); index >= 0 {
			return 30 + index, true
		}
		return 0, false
	}
	if needle == stem || needle == basename {
		return 0, true
	}
	if strings.HasPrefix(stem, needle) || strings.HasPrefix(basename, needle) {
		return 10, true
	}
	if index := strings.Index(stem, needle); index >= 0 {
		return 20 + index, true
	}
	if index := strings.Index(basename, needle); index >= 0 {
		return 30 + index, true
	}
	if span, ok := fuzzySubsequenceSpan(needle, stem); ok {
		return 50 + span, true
	}
	if span, ok := fuzzySubsequenceSpan(needle, basename); ok {
		return 60 + span, true
	}
	return 0, false
}

func searchTokenRank(token, name, relative string) (int, bool) {
	if rank, ok := searchTokenNameRank(token, name); ok {
		return rank, true
	}
	needle := compactSearchText(token)
	doitNeedle := alnumSearchText(token)
	if strings.HasPrefix(doitNeedle, "doit") && len(doitNeedle) >= 4 {
		if index := strings.Index(alnumSearchText(relative), doitNeedle); index >= 0 {
			return 90 + index, true
		}
		return 0, false
	}
	relativeText := compactSearchText(relative)
	if index := strings.Index(relativeText, needle); index >= 0 {
		return 90 + index, true
	}
	if span, ok := fuzzySubsequenceSpan(needle, relativeText); ok {
		return 130 + strings.Count(relative, "/")*4 + span, true
	}
	return 0, false
}

func searchEntrySortKey(name, relative string, tokens []string) (searchSortKey, bool) {
	key := searchSortKey{minimum: 0, total: 0, negative: 0, depth: strings.Count(relative, "/"), length: len(relative), lowerName: strings.ToLower(relative)}
	if len(tokens) == 0 {
		return key, true
	}
	key.minimum = 1 << 30
	for _, token := range tokens {
		rank, ok := searchTokenRank(token, name, relative)
		if !ok {
			return searchSortKey{}, false
		}
		if rank < key.minimum {
			key.minimum = rank
		}
		key.total += rank
		if rank < 90 {
			key.negative--
		}
	}
	return key, true
}

func compareSearchSortKey(left, right any) bool {
	leftKey, leftOK := left.(searchSortKey)
	rightKey, rightOK := right.(searchSortKey)
	if !leftOK || !rightOK {
		return fmt.Sprint(left) < fmt.Sprint(right)
	}
	if leftKey.minimum != rightKey.minimum {
		return leftKey.minimum < rightKey.minimum
	}
	if leftKey.total != rightKey.total {
		return leftKey.total < rightKey.total
	}
	if leftKey.negative != rightKey.negative {
		return leftKey.negative < rightKey.negative
	}
	if leftKey.depth != rightKey.depth {
		return leftKey.depth < rightKey.depth
	}
	if leftKey.length != rightKey.length {
		return leftKey.length < rightKey.length
	}
	return leftKey.lowerName < rightKey.lowerName
}

func generatedUploadName(name string) bool {
	base := filepath.Base(name)
	if len(base) < 13 || base[8] != '-' {
		return false
	}
	for index, value := range base[:8] {
		if value < '0' || value > '9' || index >= 8 {
			return false
		}
	}
	for _, value := range base[9:12] {
		if value < '0' || value > '9' {
			return false
		}
	}
	dot := strings.LastIndexByte(base, '.')
	if dot < 12 || len(base[dot+1:]) < 1 || len(base[dot+1:]) > 12 {
		return false
	}
	if dot > 12 && (base[12] != '-' || dot == 13) {
		return false
	}
	for _, value := range base[dot+1:] {
		if !((value >= 'a' && value <= 'z') || (value >= 'A' && value <= 'Z') || (value >= '0' && value <= '9')) {
			return false
		}
	}
	return true
}

func (s *Service) Mkdir(ctx context.Context, raw string) (map[string]any, *Error) {
	parent, finderErr := s.openMutationParent(raw)
	if finderErr != nil {
		return nil, finderErr
	}
	defer parent.Close()
	requested := parent.requested
	if err := ctx.Err(); err != nil {
		return nil, cancelled(requested)
	}
	if _, finderErr := s.requireMutationTarget(parent, true); finderErr != nil {
		return nil, finderErr
	} else if parent.targetIdentity != nil {
		return nil, targetExists(requested)
	}
	if err := mkdirAt(parent.directory, parent.name, 0o755); err != nil {
		if os.IsExist(err) {
			return nil, targetExists(requested)
		}
		return nil, filesystemError(requested, err)
	}
	s.noteChange([]string{requested}, "mkdir")
	return map[string]any{"ok": true, "path": requested, "created": true, "kind": "dir", "operation_id": operationID()}, nil
}

func (s *Service) Write(ctx context.Context, raw, content string, expectedMtime *int64) (map[string]any, *Error) {
	if len([]byte(content)) > MaxWriteBytes {
		return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "content_too_large", MessageKey: "fs.error.contentTooLarge", Path: raw, Message: fmt.Sprintf("content is too large (max %d bytes)", MaxWriteBytes)}
	}
	parent, finderErr := s.openMutationParent(raw)
	if finderErr != nil {
		return nil, finderErr
	}
	defer parent.Close()
	requested := parent.requested
	if err := ctx.Err(); err != nil {
		return nil, cancelled(requested)
	}
	existing, finderErr := s.requireMutationTarget(parent, true)
	if finderErr != nil {
		return nil, finderErr
	}
	if existing != nil && existing.IsDir() {
		return nil, invalid(requested, "path is a directory")
	}
	if expectedMtime != nil && existing != nil && !mtimeMatches(*expectedMtime, existing.ModTime().UnixNano(), existing.ModTime().Unix()) {
		return nil, changed(requested)
	}
	mode := uint32(0o600)
	if existing != nil && existing.Mode().Perm() != 0 {
		mode = uint32(existing.Mode().Perm())
	}
	temporaryName := fmt.Sprintf(".yolomux-write-%d-%d", os.Getpid(), time.Now().UnixNano())
	file, err := openAt(parent.directory, temporaryName, syscall.O_WRONLY|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW, mode)
	if err != nil {
		return nil, filesystemError(requested, err)
	}
	removeTemporary := true
	defer func() {
		_ = file.Close()
		if removeTemporary {
			_ = unlinkAt(parent.directory, temporaryName, false)
		}
	}()
	if _, err := io.WriteString(file, content); err != nil {
		return nil, filesystemError(requested, err)
	}
	if err := ctx.Err(); err != nil {
		return nil, cancelled(requested)
	}
	if err := file.Sync(); err != nil {
		return nil, filesystemError(requested, err)
	}
	if err := ctx.Err(); err != nil {
		return nil, cancelled(requested)
	}
	stat, err := file.Stat()
	if err != nil {
		return nil, filesystemError(requested, err)
	}
	if existing != nil {
		if s.beforeWriteConflictRecheck != nil {
			s.beforeWriteConflictRecheck()
		}
		current, currentErr := s.requireMutationTarget(parent, false)
		if currentErr != nil {
			return nil, currentErr
		}
		if expectedMtime != nil && !mtimeMatches(*expectedMtime, current.ModTime().UnixNano(), current.ModTime().Unix()) {
			return nil, changed(requested)
		}
	}
	if existing == nil {
		if err := renameNoReplace(parent.directory, temporaryName, parent.name); err != nil {
			if errors.Is(err, syscall.EEXIST) {
				return nil, changed(requested)
			}
			return nil, filesystemError(requested, err)
		}
	} else {
		quarantine, quarantineErr := s.quarantineMutationTarget(parent)
		if quarantineErr != nil {
			return nil, quarantineErr
		}
		// Re-read the quarantined name after the namespace move. The initial mtime check can race
		// with an in-place editor write while the temporary file is being filled; device/inode still
		// matches in that case, but the caller's expected content version does not. Restore the
		// original name before returning the typed conflict so the external edit remains authoritative.
		if expectedMtime != nil {
			quarantinedInfo, statErr := os.Lstat(descriptorChildPath(parent.directory, quarantine))
			if statErr != nil {
				_ = restoreMutationTarget(parent, quarantine)
				return nil, filesystemError(requested, statErr)
			}
			if !mtimeMatches(*expectedMtime, quarantinedInfo.ModTime().UnixNano(), quarantinedInfo.ModTime().Unix()) {
				_ = restoreMutationTarget(parent, quarantine)
				return nil, changed(requested)
			}
		}
		if err := ctx.Err(); err != nil {
			_ = restoreMutationTarget(parent, quarantine)
			return nil, cancelled(requested)
		}
		if err := renameNoReplace(parent.directory, temporaryName, parent.name); err != nil {
			_ = restoreMutationTarget(parent, quarantine)
			if errors.Is(err, syscall.EEXIST) || errors.Is(err, syscall.ENOENT) {
				return nil, changed(requested)
			}
			return nil, filesystemError(requested, err)
		}
		if err := unlinkAt(parent.directory, quarantine, false); err != nil {
			return nil, filesystemError(requested, err)
		}
	}
	removeTemporary = false
	if err := parent.directory.Sync(); err != nil {
		return nil, filesystemError(requested, err)
	}
	result := map[string]any{"path": requested, "size": stat.Size(), "mtime": stat.ModTime().Unix(), "mtime_ns": stat.ModTime().UnixNano(), "file_id": fileID(stat), "file_identity": "id:" + fileID(stat)}
	s.noteChange([]string{requested}, "write")
	return result, nil
}

func (s *Service) Rename(ctx context.Context, raw, newName string) (map[string]any, *Error) {
	parent, finderErr := s.openMutationParent(raw)
	if finderErr != nil {
		return nil, finderErr
	}
	defer parent.Close()
	requested := parent.requested
	newName = strings.TrimSpace(newName)
	if newName == "" || newName == "." || newName == ".." || strings.ContainsAny(newName, "/\\\x00\r\n") {
		return nil, invalid(requested, "name contains illegal characters")
	}
	if err := ctx.Err(); err != nil {
		return nil, cancelled(requested)
	}
	if _, finderErr := s.requireMutationTarget(parent, false); finderErr != nil {
		return nil, finderErr
	}
	gitPlan, err := prepareGitRename(ctx, filepath.Join(parent.resolvedParent, parent.name))
	if err != nil {
		if errors.Is(err, errGitIndexChanged) {
			return nil, gitIndexChanged(requested, err)
		}
		return nil, filesystemError(requested, err)
	}
	target := filepath.Join(filepath.Dir(requested), newName)
	if _, err := os.Lstat(descriptorChildPath(parent.directory, newName)); err == nil {
		return nil, targetExists(target)
	} else if !os.IsNotExist(err) {
		return nil, filesystemError(target, err)
	}
	quarantine, quarantineErr := s.quarantineMutationTarget(parent)
	if quarantineErr != nil {
		return nil, quarantineErr
	}
	if err := ctx.Err(); err != nil {
		_ = restoreMutationTarget(parent, quarantine)
		return nil, cancelled(requested)
	}
	if err := renameNoReplace(parent.directory, quarantine, newName); err != nil {
		_ = restoreMutationTarget(parent, quarantine)
		if errors.Is(err, syscall.EEXIST) {
			return nil, targetExists(target)
		}
		if os.IsNotExist(err) {
			return nil, changed(requested)
		}
		return nil, filesystemError(requested, err)
	}
	if gitPlan != nil {
		newRelative, relativeErr := filepath.Rel(gitPlan.root, filepath.Join(parent.resolvedParent, newName))
		if relativeErr != nil || newRelative == "." || strings.HasPrefix(newRelative, ".."+string(filepath.Separator)) {
			if restoreErr := restoreMutationTarget(parent, quarantine); restoreErr != nil {
				return nil, filesystemError(requested, fmt.Errorf("Git rename target escaped repository and filesystem rollback failed: %w", restoreErr))
			}
			return nil, gitIndexChanged(requested, errors.New("Git rename target escaped repository"))
		}
		publish := gitPlan.publish
		if s.publishGitRename != nil {
			publish = func(ctx context.Context, path string) error { return s.publishGitRename(ctx, gitPlan, path) }
		}
		if publishErr := publish(ctx, newRelative); publishErr != nil {
			rollbackErr := gitPlan.rollback(ctx, newRelative)
			if restoreErr := renameNoReplace(parent.directory, newName, parent.name); restoreErr != nil {
				if rollbackErr != nil {
					return nil, filesystemError(requested, fmt.Errorf("Git rename failed, index rollback failed: %v, and filesystem rollback failed: %w", rollbackErr, restoreErr))
				}
				return nil, filesystemError(requested, fmt.Errorf("Git rename failed and filesystem rollback failed: %w", restoreErr))
			}
			if rollbackErr != nil {
				return nil, filesystemError(requested, fmt.Errorf("Git rename failed and index rollback failed: %v", rollbackErr))
			}
			if errors.Is(publishErr, errGitIndexChanged) {
				return nil, gitIndexChanged(requested, publishErr)
			}
			return nil, filesystemError(requested, publishErr)
		}
	}
	s.noteChange([]string{requested, target}, "rename")
	return map[string]any{"path": target, "old_path": requested, "name": newName}, nil
}

func (s *Service) Delete(ctx context.Context, raw string, recursive ...bool) (map[string]any, *Error) {
	parent, finderErr := s.openMutationParent(raw)
	if finderErr != nil {
		return nil, finderErr
	}
	defer parent.Close()
	requested := parent.requested
	info, finderErr := s.requireMutationTarget(parent, false)
	if finderErr != nil {
		return nil, finderErr
	}
	if err := ctx.Err(); err != nil {
		return nil, cancelled(requested)
	}
	quarantine, quarantineErr := s.quarantineMutationTarget(parent)
	if quarantineErr != nil {
		return nil, quarantineErr
	}
	if err := ctx.Err(); err != nil {
		_ = restoreMutationTarget(parent, quarantine)
		return nil, cancelled(requested)
	}
	removeRecursively := len(recursive) > 0 && recursive[0]
	if info.IsDir() && !removeRecursively {
		if err := unlinkAt(parent.directory, quarantine, true); err != nil {
			if errors.Is(err, syscall.ENOTEMPTY) || errors.Is(err, syscall.EEXIST) {
				_ = restoreMutationTarget(parent, quarantine)
				return map[string]any{"path": requested, "deleted": false, "kind": "dir", "pending": "subtree"}, nil
			}
			_ = restoreMutationTarget(parent, quarantine)
			return nil, filesystemError(requested, err)
		}
		s.noteChange([]string{requested}, "delete")
		return map[string]any{"path": requested, "deleted": true, "kind": "dir"}, nil
	}
	if info.IsDir() {
		directory, err := openAt(parent.directory, quarantine, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
		if err != nil {
			_ = restoreMutationTarget(parent, quarantine)
			return nil, filesystemError(requested, err)
		}
		deleted := make([]string, 0)
		deleteErr := s.deleteDirectoryContents(ctx, directory, requested, &deleted)
		_ = directory.Close()
		if deleteErr != nil {
			_ = restoreMutationTarget(parent, quarantine)
			if stopped, ok := deleteErr.(recursiveDeleteError); ok {
				return nil, partialDelete(requested, stopped.reason, stopped.failedPath, deleted)
			}
			if errors.Is(deleteErr, context.Canceled) || errors.Is(deleteErr, context.DeadlineExceeded) {
				return nil, partialDelete(requested, deleteReason(deleteErr), requested, deleted)
			}
			if _, limited := deleteErr.(fsWalkLimitError); limited {
				return nil, partialDelete(requested, "limit_exceeded", requested, deleted)
			}
			if len(deleted) > 0 {
				return nil, partialDelete(requested, "entry_failed", requested, deleted)
			}
			return nil, filesystemError(requested, deleteErr)
		}
		if err := unlinkAt(parent.directory, quarantine, true); err != nil {
			_ = restoreMutationTarget(parent, quarantine)
			if len(deleted) > 0 {
				return nil, partialDelete(requested, "entry_failed", requested, deleted)
			}
			return nil, filesystemError(requested, err)
		}
		s.noteChange([]string{requested}, "delete")
		return map[string]any{"path": requested, "deleted": true, "kind": "dir"}, nil
	}
	if err := unlinkAt(parent.directory, quarantine, false); err != nil {
		_ = restoreMutationTarget(parent, quarantine)
		return nil, filesystemError(requested, err)
	}
	s.noteChange([]string{requested}, "delete")
	return map[string]any{"path": requested, "deleted": true, "kind": "file"}, nil
}

func (s *Service) deleteDirectoryContents(ctx context.Context, directory *os.File, requestedRoot string, deleted *[]string) error {
	entries, err := directory.ReadDir(MaxDirectoryEntries + 1)
	if err != nil && !errors.Is(err, io.EOF) {
		return recursiveDeleteError{reason: "entry_failed", failedPath: requestedRoot, err: err}
	}
	if len(entries) > MaxDirectoryEntries {
		return recursiveDeleteError{reason: "limit_exceeded", failedPath: requestedRoot, err: fsWalkLimitError{}}
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Name() < entries[j].Name() })
	for _, entry := range entries {
		select {
		case <-ctx.Done():
			return recursiveDeleteError{reason: deleteReason(ctx.Err()), failedPath: childPathForDelete(requestedRoot, entry.Name()), err: ctx.Err()}
		default:
		}
		if len(*deleted) >= MaxMutationEntries {
			return recursiveDeleteError{reason: "limit_exceeded", failedPath: requestedRoot, err: fsWalkLimitError{}}
		}
		name := entry.Name()
		childPath := filepath.Join(requestedRoot, name)
		linkInfo, statErr := os.Lstat(descriptorChildPath(directory, name))
		if statErr != nil {
			if os.IsNotExist(statErr) {
				continue
			}
			return recursiveDeleteError{reason: "entry_failed", failedPath: childPath, err: statErr}
		}
		quarantine, quarantineErr := quarantineDeleteEntry(directory, name, identityFor(linkInfo))
		if quarantineErr != nil {
			return recursiveDeleteError{reason: "changed_on_disk", failedPath: childPath, err: quarantineErr}
		}
		quarantinedInfo, infoErr := os.Lstat(descriptorChildPath(directory, quarantine))
		if infoErr != nil {
			_ = restoreMutationTarget(&mutationParent{directory: directory, name: name}, quarantine)
			return recursiveDeleteError{reason: "entry_failed", failedPath: childPath, err: infoErr}
		}
		if quarantinedInfo.Mode()&os.ModeSymlink != 0 || !quarantinedInfo.IsDir() {
			if err := unlinkAt(directory, quarantine, false); err != nil {
				_ = restoreMutationTarget(&mutationParent{directory: directory, name: name}, quarantine)
				return recursiveDeleteError{reason: "entry_failed", failedPath: childPath, err: err}
			}
			*deleted = append(*deleted, childPath)
			continue
		}
		child, openErr := openAt(directory, quarantine, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
		if openErr != nil {
			_ = restoreMutationTarget(&mutationParent{directory: directory, name: name}, quarantine)
			return recursiveDeleteError{reason: "entry_failed", failedPath: childPath, err: openErr}
		}
		childErr := s.deleteDirectoryContents(ctx, child, childPath, deleted)
		_ = child.Close()
		if childErr != nil {
			_ = restoreMutationTarget(&mutationParent{directory: directory, name: name}, quarantine)
			return childErr
		}
		if err := unlinkAt(directory, quarantine, true); err != nil {
			_ = restoreMutationTarget(&mutationParent{directory: directory, name: name}, quarantine)
			return recursiveDeleteError{reason: "entry_failed", failedPath: childPath, err: err}
		}
		*deleted = append(*deleted, childPath)
	}
	return nil
}

func childPathForDelete(root, name string) string { return filepath.Join(root, name) }

func quarantineDeleteEntry(directory *os.File, name string, expected descriptorIdentity) (string, error) {
	// Threat-model boundary: the service does not grant unrelated principals write access to this
	// directory, so the checked quarantine name is protected from ordinary path replacement races.
	// Linux has no unlink-by-file-descriptor primitive; a same-UID actor that can concurrently
	// mutate this directory could still replace the quarantine name after this check. Closing that
	// residual requires a private quarantine namespace with stronger access isolation or a
	// platform-specific inode-removal primitive, neither of which is available in this phase.
	for sequence := 0; sequence < 1000; sequence++ {
		quarantine := fmt.Sprintf(".yolomux-delete-%d-%d-%d", os.Getpid(), time.Now().UnixNano(), sequence)
		if err := renameNoReplace(directory, name, quarantine); err != nil {
			if errors.Is(err, syscall.EEXIST) {
				continue
			}
			return "", err
		}
		info, err := os.Lstat(descriptorChildPath(directory, quarantine))
		if err == nil && sameDescriptorIdentity(identityFor(info), expected) {
			return quarantine, nil
		}
		if err == nil {
			if restoreErr := renameNoReplace(directory, quarantine, name); restoreErr != nil {
				_ = unlinkAt(directory, quarantine, info.IsDir())
			}
			return "", errChangedOnDisk
		}
		return "", err
	}
	return "", errors.New("unable to reserve recursive delete entry")
}

type recursiveDeleteError struct {
	reason     string
	failedPath string
	err        error
}

func (e recursiveDeleteError) Error() string { return e.err.Error() }

func (e recursiveDeleteError) Unwrap() error { return e.err }

func deleteReason(err error) string {
	if errors.Is(err, context.DeadlineExceeded) {
		return "deadline_exceeded"
	}
	if errors.Is(err, context.Canceled) {
		return "cancelled"
	}
	return "entry_failed"
}

func partialDelete(path, reason, failedPath string, deleted []string) *Error {
	return &Error{
		Status: http.StatusConflict, Code: "filesystem_error", MessageKey: "fs.error.operationFailed", Path: path,
		Retryable: true, Message: fmt.Sprintf("recursive delete stopped at %s", failedPath), Partial: len(deleted) > 0,
		DeleteReason: reason, FailedPath: failedPath, DeletedPaths: append([]string(nil), deleted...),
	}
}

func (s *Service) ResolveCandidates(ctx context.Context, values []string) (map[string]any, *Error) {
	if len(values) == 0 || len(values) > MaxCandidatePaths {
		return nil, invalid("", fmt.Sprintf("paths must contain 1 to %d items", MaxCandidatePaths))
	}
	misses := make([]any, 0)
	seen := make(map[string]struct{}, len(values))
	for _, value := range values {
		if value == "" {
			return nil, invalid(value, "path is required")
		}
		if _, exists := seen[value]; exists {
			return nil, invalid(value, "paths must be unique strings")
		}
		seen[value] = struct{}{}
	}
	for _, value := range values {
		info, finderErr := s.Info(ctx, value, false)
		if finderErr != nil {
			if finderErr.Status == http.StatusNotFound {
				misses = append(misses, map[string]any{"path": value, "status": http.StatusNotFound})
				continue
			}
			return nil, finderErr
		}
		if info["kind"] == "file" {
			return map[string]any{"path": info["path"], "info": info, "misses": misses}, nil
		}
		misses = append(misses, map[string]any{"path": value, "status": http.StatusNotFound})
	}
	return map[string]any{"path": "", "info": nil, "misses": misses}, nil
}

type SearchChunkPublisher func(files []map[string]any, coverage map[string]any) error

type SearchOptions struct {
	Cursor      string
	IndexedOnly bool
}

func (s *Service) Search(ctx context.Context, root, query string, limit int, recursive, directOnly, minimal bool) (map[string]any, *Error) {
	return s.SearchWithOptions(ctx, root, query, limit, recursive, directOnly, minimal, SearchOptions{})
}

func (s *Service) SearchWithOptions(ctx context.Context, root, query string, limit int, recursive, directOnly, minimal bool, options SearchOptions) (map[string]any, *Error) {
	return s.search(ctx, root, query, limit, recursive, directOnly, minimal, options, nil)
}

func (s *Service) SearchStream(ctx context.Context, root, query string, limit int, recursive, directOnly, minimal bool, publish SearchChunkPublisher) (map[string]any, *Error) {
	return s.search(ctx, root, query, limit, recursive, directOnly, minimal, SearchOptions{}, publish)
}

func (s *Service) search(ctx context.Context, root, query string, limit int, recursive, directOnly, minimal bool, options SearchOptions, publish SearchChunkPublisher) (map[string]any, *Error) {
	if limit < 1 {
		limit = 1
	} else if limit > MaxSearchResults {
		limit = MaxSearchResults
	}
	directory, requested, resolved, finderErr := s.open(root, true)
	if finderErr != nil {
		return nil, finderErr
	}
	defer directory.Close()
	query = strings.TrimSpace(strings.ToLower(query))
	if options.Cursor != "" {
		return map[string]any{
			"root": requested, "query": query, "limit": limit,
			"rebase_required": true, "reason": "index_unavailable", "indexed": false,
		}, nil
	}
	if options.IndexedOnly {
		return map[string]any{
			"root": requested, "query": query, "limit": limit,
			"files": []map[string]any{}, "count": 0, "complete": false, "truncated": false,
			"recursive": recursive, "indexed": false, "index_state": "warming", "index_coverage": "pending",
			"progressive_coverage": map[string]any{"full_coverage": false, "visited_files": 0, "visited_directories": 0},
		}, nil
	}
	tokens := strings.Fields(query)
	files := make([]map[string]any, 0, limit)
	matchedFiles := 0
	visitedFiles := 0
	visitedDirs := 0
	match := func(name, relative string) (searchSortKey, bool) {
		return searchEntrySortKey(name, relative, tokens)
	}
	appendEntry := func(walked walkedEntry, countVisited ...bool) error {
		count := true
		if len(countVisited) > 0 {
			count = countVisited[0]
		}
		if walked.IsDir {
			if count {
				visitedDirs++
			}
			if count && visitedDirs > MaxSearchDirectories {
				return errWalkStop
			}
			return nil
		}
		if count {
			visitedFiles++
		}
		if (count && visitedFiles > MaxSearchFiles) || matchedFiles >= limit {
			return errWalkStop
		}
		sortKey, matched := match(walked.Name, walked.Relative)
		if !matched {
			return nil
		}
		info := walked.Info
		row := map[string]any{"path": walked.Path, "name": walked.Name, "relative_path": walked.Relative, "kind": "file", "size": info.Size(), "mtime": info.ModTime().Unix(), "mtime_ns": info.ModTime().UnixNano(), "realpath": walked.Resolved, "file_id": fileID(info), "file_identity": "id:" + fileID(info), "uploaded": generatedUploadName(walked.Name), "_sort_key": sortKey}
		if minimal {
			row = map[string]any{"path": walked.Path, "name": walked.Name, "relative_path": walked.Relative, "kind": "file"}
		}
		matchedFiles++
		if publish != nil {
			streamRow := make(map[string]any, len(row)-1)
			for key, value := range row {
				if key != "_sort_key" {
					streamRow[key] = value
				}
			}
			if err := publish([]map[string]any{streamRow}, map[string]any{"visited_files": visitedFiles, "visited_directories": visitedDirs}); err != nil {
				return err
			}
		} else {
			files = append(files, row)
		}
		return nil
	}
	var walkErr error
	truncated := false
	if directOnly || !recursive {
		entries, err := directory.ReadDir(MaxDirectoryEntries + 1)
		if err != nil && !errors.Is(err, io.EOF) {
			return nil, filesystemError(requested, err)
		}
		if len(entries) > MaxDirectoryEntries {
			entries = entries[:MaxDirectoryEntries]
			truncated = true
		}
		for _, entry := range entries {
			entryInfo, infoErr := entry.Info()
			if infoErr != nil || entry.Type()&os.ModeSymlink != 0 || (!entryInfo.Mode().IsRegular() && !entryInfo.IsDir()) {
				continue
			}
			if entryInfo.IsDir() && isSearchExcludedDirectory(entry.Name()) {
				continue
			}
			if entryInfo.IsDir() {
				visitedDirs++
				if visitedDirs > MaxSearchDirectories {
					truncated = true
					break
				}
			} else {
				visitedFiles++
				if visitedFiles > MaxSearchFiles {
					truncated = true
					break
				}
			}
			relative := filepath.ToSlash(entry.Name())
			sortKey, matched := match(entry.Name(), relative)
			if !matched {
				continue
			}
			child, err := openAt(directory, entry.Name(), syscall.O_RDONLY|syscall.O_NOFOLLOW, 0)
			if err != nil {
				continue
			}
			info, infoErr := child.Stat()
			if infoErr == nil && (info.Mode().IsRegular() || info.IsDir()) && sameDescriptorIdentity(identityFor(entryInfo), identityFor(info)) {
				if info.IsDir() {
					if !directOnly {
						_ = child.Close()
						continue
					}
					if matchedFiles >= limit {
						truncated = true
						_ = child.Close()
						break
					}
					row := map[string]any{"path": filepath.Join(requested, entry.Name()), "name": entry.Name(), "relative_path": relative, "kind": "dir", "_sort_key": sortKey}
					if minimal {
						row = map[string]any{"path": filepath.Join(requested, entry.Name()), "name": entry.Name(), "relative_path": relative, "kind": "dir", "_sort_key": sortKey}
					}
					if publish != nil {
						streamRow := make(map[string]any, len(row)-1)
						for key, value := range row {
							if key != "_sort_key" {
								streamRow[key] = value
							}
						}
						if err := publish([]map[string]any{streamRow}, map[string]any{"visited_files": visitedFiles, "visited_directories": visitedDirs}); err != nil {
							walkErr = err
						}
					} else {
						files = append(files, row)
					}
					matchedFiles++
				} else {
					walkErr = appendEntry(walkedEntry{Name: entry.Name(), Relative: relative, Path: filepath.Join(requested, entry.Name()), Resolved: filepath.Join(resolved, entry.Name()), File: child, Info: info}, false)
				}
			}
			_ = child.Close()
			if walkErr != nil {
				break
			}
		}
	} else {
		walkErr = s.walkDescriptors(ctx, directory, requested, resolved, func(walked walkedEntry) error {
			if walked.IsDir && isSearchExcludedDirectory(walked.Name) {
				return errWalkSkipDirectory
			}
			return appendEntry(walked)
		})
	}
	if walkErr != nil {
		if walkErr == context.Canceled || walkErr == context.DeadlineExceeded {
			return nil, cancelled(requested)
		}
		if errors.Is(walkErr, errWalkDirectoryLimit) {
			truncated = true
		} else if !errors.Is(walkErr, errWalkStop) {
			return nil, filesystemError(requested, walkErr)
		}
	}
	sort.SliceStable(files, func(i, j int) bool { return compareSearchSortKey(files[i]["_sort_key"], files[j]["_sort_key"]) })
	for _, row := range files {
		delete(row, "_sort_key")
	}
	truncated = truncated || matchedFiles >= limit || visitedFiles > MaxSearchFiles || visitedDirs > MaxSearchDirectories
	return map[string]any{"root": requested, "query": query, "files": files, "count": matchedFiles, "limit": limit, "complete": !truncated, "truncated": truncated, "recursive": recursive, "indexed": false, "index_state": "on_demand", "progressive_coverage": map[string]any{"full_coverage": recursive && !truncated, "visited_files": visitedFiles, "visited_directories": visitedDirs}}, nil
}

func (s *Service) IndexStatus(ctx context.Context, root string) (map[string]any, *Error) {
	directory, requested, _, finderErr := s.open(root, true)
	if finderErr != nil {
		return nil, finderErr
	}
	defer directory.Close()
	if err := ctx.Err(); err != nil {
		return nil, cancelled(requested)
	}
	return map[string]any{"root": requested, "status": "on_demand", "index_state": "not_indexed", "indexed": false, "ready": false, "stale": false, "refresh_pending": false, "progressive_coverage": map[string]any{"full_coverage": false}}, nil
}

// Unindex is intentionally idempotent while Go uses bounded on-demand search. It still
// authorizes and pins the requested directory so the route has the same root and permission
// contract as the eventual persistent-index owner.
func (s *Service) Unindex(ctx context.Context, root string) (map[string]any, *Error) {
	directory, requested, _, finderErr := s.open(root, true)
	if finderErr != nil {
		return nil, finderErr
	}
	defer directory.Close()
	if err := ctx.Err(); err != nil {
		return nil, cancelled(requested)
	}
	return map[string]any{"root": requested, "ok": true, "indexed": false, "index_state": "not_indexed"}, nil
}

func (s *Service) Count(ctx context.Context, raw string) (map[string]any, *Error) {
	directory, requested, resolved, finderErr := s.open(raw, true)
	if finderErr != nil {
		return nil, finderErr
	}
	defer directory.Close()
	count := 0
	walkErr := s.walkDescriptors(ctx, directory, requested, resolved, func(entry walkedEntry) error {
		if !entry.IsDir {
			count++
			if count > MaxMutationEntries {
				return fsWalkLimitError{}
			}
		}
		return nil
	})
	if walkErr != nil {
		if walkErr == context.Canceled || walkErr == context.DeadlineExceeded {
			return nil, cancelled(requested)
		}
		if _, limited := walkErr.(fsWalkLimitError); limited || errors.Is(walkErr, errWalkDirectoryLimit) {
			return nil, tooLarge(requested, int64(count), MaxMutationEntries)
		}
		return nil, filesystemError(requested, walkErr)
	}
	return map[string]any{"path": requested, "kind": "dir", "files": count, "recursive": true}, nil
}

func (s *Service) Zip(ctx context.Context, raw string, transferLimit ...int64) ([]byte, string, *Error) {
	directory, requested, resolved, finderErr := s.open(raw, true)
	if finderErr != nil {
		return nil, "", finderErr
	}
	defer directory.Close()
	maxBytes := int64(MaxRawBytes)
	if len(transferLimit) > 0 && transferLimit[0] > 0 {
		maxBytes = transferLimit[0]
	}
	var buffer bytes.Buffer
	archive := zip.NewWriter(&buffer)
	count := 0
	rootName := filepath.Base(requested)
	if rootName == "." || rootName == string(filepath.Separator) || rootName == "" {
		rootName = "root"
	}
	rootHeader := &zip.FileHeader{Name: rootName + "/", Method: zip.Store}
	if _, err := archive.CreateHeader(rootHeader); err != nil {
		return nil, "", filesystemError(requested, err)
	}
	sourceBytes := int64(0)
	walkErr := s.walkDescriptors(ctx, directory, requested, resolved, func(entry walkedEntry) error {
		if entry.IsDir {
			header := &zip.FileHeader{Name: filepath.ToSlash(filepath.Join(rootName, entry.Relative)) + "/", Method: zip.Store}
			_, err := archive.CreateHeader(header)
			return err
		}
		data, err := readDescriptorContext(ctx, entry.File, maxBytes)
		if err != nil {
			return err
		}
		sourceBytes += int64(len(data))
		if sourceBytes > maxBytes {
			return fsWalkLimitError{}
		}
		header := &zip.FileHeader{Name: filepath.ToSlash(filepath.Join(rootName, entry.Relative)), Method: zip.Deflate}
		header.SetMode(entry.Info.Mode())
		writer, err := archive.CreateHeader(header)
		if err != nil {
			return err
		}
		if _, err := writer.Write(data); err != nil {
			return err
		}
		count++
		return nil
	})
	if walkErr != nil {
		_ = archive.Close()
		if walkErr == context.Canceled || walkErr == context.DeadlineExceeded {
			return nil, "", cancelled(requested)
		}
		if _, limited := walkErr.(fsWalkLimitError); limited || errors.Is(walkErr, errWalkDirectoryLimit) {
			return nil, "", tooLarge(requested, int64(buffer.Len()), maxBytes)
		}
		return nil, "", filesystemError(requested, walkErr)
	}
	if err := archive.Close(); err != nil {
		return nil, "", filesystemError(requested, err)
	}
	if int64(buffer.Len()) > maxBytes {
		return nil, "", tooLarge(requested, int64(buffer.Len()), maxBytes)
	}
	return buffer.Bytes(), rootName + ".zip", nil
}

func (s *Service) checkMutationTarget(path, resolved string) *Error {
	if path == s.root || resolved == s.root {
		return forbidden(path, "fs.error.rootMutationBlocked", "the Finder service root cannot be mutated")
	}
	return nil
}

func (s *Service) noteChange(paths []string, reason string) {
	s.recordChange(paths, reason)
}

func operationID() string { return fmt.Sprintf("fs-%d", time.Now().UnixNano()) }

func targetExists(path string) *Error {
	return &Error{Status: http.StatusConflict, Code: "target_exists", MessageKey: "fs.error.targetExists", Path: path, Message: "target already exists"}
}

func gitIndexChanged(path string, err error) *Error {
	message := "Git index changed during rename"
	if err != nil && err.Error() != "" {
		message = err.Error()
	}
	return &Error{Status: http.StatusConflict, Code: "filesystem_error", MessageKey: "fs.error.gitRepositoryChanged", Path: path, Retryable: true, Message: message}
}

func mtimeMatches(expected, actualNS, actualLegacy int64) bool {
	if expected == actualLegacy {
		return true
	}
	const tolerance = int64(10 * time.Millisecond)
	return expected >= 1_000_000_000_000_000 && absInt64(expected-actualNS) <= tolerance
}

func absInt64(value int64) int64 {
	if value < 0 {
		return -value
	}
	return value
}

type fsWalkLimitError struct{}

func (fsWalkLimitError) Error() string { return "filesystem operation exceeded its bound" }

type UploadFile struct {
	Name string
	Data []byte
}

type mutationParent struct {
	directory      *os.File
	requested      string
	resolvedParent string
	name           string
	targetIdentity *descriptorIdentity
}

func (p *mutationParent) Close() { _ = p.directory.Close() }

func (s *Service) openMutationParent(raw string) (*mutationParent, *Error) {
	requested, resolved, finderErr := s.authorize(raw, true)
	if finderErr != nil {
		return nil, finderErr
	}
	if finderErr = s.checkMutationTarget(requested, resolved); finderErr != nil {
		return nil, finderErr
	}
	parentRequested := filepath.Dir(requested)
	parentResolved, err := filepath.EvalSymlinks(parentRequested)
	if err != nil {
		return nil, filesystemError(parentRequested, err)
	}
	parentResolved, err = filepath.Abs(parentResolved)
	if err != nil {
		return nil, filesystemError(parentRequested, err)
	}
	identities, err := captureResolvedIdentities(parentResolved)
	if err != nil {
		return nil, filesystemError(parentRequested, err)
	}
	directory, err := openResolvedDescriptor(parentResolved, true, identities)
	if err != nil {
		if errors.Is(err, errChangedOnDisk) {
			return nil, changed(requested)
		}
		return nil, filesystemError(parentRequested, err)
	}
	result := &mutationParent{directory: directory, requested: requested, resolvedParent: parentResolved, name: filepath.Base(requested)}
	if info, statErr := os.Lstat(descriptorChildPath(directory, result.name)); statErr == nil {
		identity := identityFor(info)
		result.targetIdentity = &identity
	} else if !os.IsNotExist(statErr) {
		result.Close()
		return nil, filesystemError(requested, statErr)
	}
	return result, nil
}

func (s *Service) requireMutationTarget(parent *mutationParent, allowMissing bool) (os.FileInfo, *Error) {
	info, err := os.Lstat(descriptorChildPath(parent.directory, parent.name))
	if err != nil {
		if allowMissing && os.IsNotExist(err) {
			return nil, nil
		}
		return nil, filesystemError(parent.requested, err)
	}
	if parent.targetIdentity != nil && !sameDescriptorIdentity(identityFor(info), *parent.targetIdentity) {
		return nil, changed(parent.requested)
	}
	return info, nil
}

// quarantineMutationTarget atomically moves the name being mutated to a private sibling and
// verifies that the moved inode is still the one authorized by openMutationParent.  The final
// mutation can then install, rename, or remove that sibling without overwriting a replacement
// that won the check-to-mutate race.
func (s *Service) quarantineMutationTarget(parent *mutationParent) (string, *Error) {
	if parent.targetIdentity == nil {
		return "", notFound(parent.requested)
	}
	for sequence := 0; sequence < 1000; sequence++ {
		name := fmt.Sprintf(".yolomux-mutation-%d-%d-%d", os.Getpid(), time.Now().UnixNano(), sequence)
		if err := renameNoReplace(parent.directory, parent.name, name); err != nil {
			if errors.Is(err, syscall.EEXIST) {
				continue
			}
			if errors.Is(err, syscall.ENOENT) {
				return "", changed(parent.requested)
			}
			return "", filesystemError(parent.requested, err)
		}
		info, err := os.Lstat(descriptorChildPath(parent.directory, name))
		if err == nil && sameDescriptorIdentity(identityFor(info), *parent.targetIdentity) {
			return name, nil
		}
		if err == nil {
			_ = restoreMutationTarget(parent, name)
			return "", changed(parent.requested)
		}
		return "", filesystemError(parent.requested, err)
	}
	return "", filesystemError(parent.requested, errors.New("unable to reserve mutation target"))
}

func restoreMutationTarget(parent *mutationParent, name string) error {
	return renameNoReplace(parent.directory, name, parent.name)
}

func (s *Service) Upload(ctx context.Context, session, username, editorPath, baseDir string, files []UploadFile) (map[string]any, *Error) {
	uploadLimits := s.currentUploadLimits()
	if len(files) == 0 {
		return nil, invalid("", "no files supplied")
	}
	if len(files) > uploadLimits.MaxFiles {
		return nil, &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_many_files", MessageKey: "upload.error.tooManyFiles", Message: fmt.Sprintf("too many files; limit is %d", uploadLimits.MaxFiles)}
	}
	var totalBytes int64
	for _, file := range files {
		totalBytes += int64(len(file.Data))
		if totalBytes > uploadLimits.MaxBytes {
			return nil, tooLarge(file.Name, totalBytes, uploadLimits.MaxBytes)
		}
	}
	targetDir := ""
	targetSource := "central_user_uploads"
	var target *os.File
	baseDir = strings.TrimSpace(baseDir)
	if editorPath != "" || baseDir != "" {
		targetBase := baseDir
		if editorPath != "" {
			parent, finderErr := s.openMutationParent(editorPath)
			if finderErr != nil {
				return nil, finderErr
			}
			info, targetErr := s.requireMutationTarget(parent, true)
			requested := parent.requested
			parent.Close()
			if targetErr != nil {
				return nil, targetErr
			}
			if info != nil && info.IsDir() {
				return nil, invalid(requested, "editor path is a directory")
			}
			if targetBase == "" {
				targetBase = filepath.Dir(requested)
			}
		}
		base, baseRequested, _, baseErr := s.open(targetBase, true)
		if baseErr != nil {
			return nil, baseErr
		}
		targetDir = baseRequested
		if editorPath != "" {
			targetName := strings.TrimSuffix(filepath.Base(editorPath), filepath.Ext(editorPath))
			if targetName == "" || targetName == "." || targetName == ".." {
				_ = base.Close()
				return nil, invalid(editorPath, "editor path has no usable file stem")
			}
			targetDir = filepath.Join(targetDir, targetName)
			targetSource = "editor_file_directory"
			var targetErr error
			target, targetErr = openAt(base, targetName, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
			if targetErr != nil && errors.Is(targetErr, os.ErrNotExist) {
				if mkdirErr := mkdirAt(base, targetName, 0o700); mkdirErr != nil && !errors.Is(mkdirErr, syscall.EEXIST) {
					_ = base.Close()
					return nil, filesystemError(targetDir, mkdirErr)
				}
				target, targetErr = openAt(base, targetName, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
			}
			baseErr = nil
			if targetErr != nil {
				baseErr = filesystemError(targetDir, targetErr)
			}
		} else {
			targetSource = "editor_base_directory"
			target = base
			base = nil
		}
		if base != nil {
			_ = base.Close()
		}
		if baseErr != nil {
			return nil, filesystemError(targetDir, baseErr)
		}
	} else {
		user := uploadPathComponent(username, "default")
		name := uploadPathComponent(session, "session")
		targetDir = filepath.Join(os.TempDir(), fmt.Sprintf("yolomux-server-%d", os.Getuid()), "shared", "yolomux."+user, "uploads", name)
		var err error
		target, err = ensurePrivateUploadTree(targetDir)
		if err != nil {
			return nil, filesystemError(targetDir, err)
		}
	}
	if target == nil {
		return nil, filesystemError(targetDir, errors.New("upload target directory is unavailable"))
	}
	defer target.Close()
	saved := make([]any, 0, len(files))
	for _, file := range files {
		if err := ctx.Err(); err != nil {
			return nil, cancelled(targetDir)
		}
		template := uploadLimits.FilenameTemplate
		if template == "" {
			template = os.Getenv("YOLOMUX_UPLOAD_FILENAME_TEMPLATE")
		}
		savedName, err := saveUploadAtWithTemplate(ctx, target, file.Name, file.Data, template)
		if err != nil {
			return nil, filesystemError(filepath.Join(targetDir, sanitizeUploadFilename(file.Name)), err)
		}
		candidate := filepath.Join(targetDir, savedName)
		relative := savedName
		if editorPath != "" {
			relative = filepath.Base(targetDir) + "/" + savedName
		}
		saved = append(saved, map[string]any{"name": file.Name, "saved_name": savedName, "path": candidate, "relative_path": relative, "size": len(file.Data)})
	}
	resultBaseDir := baseDir
	if resultBaseDir == "" && editorPath != "" {
		resultBaseDir = filepath.Dir(editorPath)
	}
	return map[string]any{"session": session, "target_dir": targetDir, "target_source": targetSource, "base_dir": resultBaseDir, "files": saved}, nil
}

func uploadPathComponent(value, fallback string) string {
	original := strings.TrimSpace(value)
	if original == "" {
		return fallback
	}
	value = original
	var builder strings.Builder
	for _, runeValue := range value {
		if runeValue == '.' || runeValue == '_' || runeValue == '-' || runeValue >= 'a' && runeValue <= 'z' || runeValue >= 'A' && runeValue <= 'Z' || runeValue >= '0' && runeValue <= '9' {
			builder.WriteRune(runeValue)
		} else {
			builder.WriteByte('_')
		}
	}
	result := strings.Trim(builder.String(), "._-")
	if result == "" || result == "." || result == ".." {
		return fallback
	}
	if result != original {
		digest := sha256.Sum256([]byte(original))
		hexDigest := fmt.Sprintf("%x", digest[:])[:10]
		if len(result) > 69 {
			result = result[:69]
		}
		result += "-" + hexDigest
	}
	if len(result) > 80 {
		result = result[:80]
	}
	return result
}

func sanitizeUploadFilename(filename string) string {
	name := filepath.Base(strings.ReplaceAll(filename, "\\", "/"))
	var builder strings.Builder
	for _, runeValue := range name {
		if runeValue == '.' || runeValue == '_' || runeValue == '-' || runeValue == ' ' || runeValue >= 'a' && runeValue <= 'z' || runeValue >= 'A' && runeValue <= 'Z' || runeValue >= '0' && runeValue <= '9' {
			builder.WriteRune(runeValue)
		} else {
			builder.WriteByte('_')
		}
	}
	name = strings.Join(strings.Fields(builder.String()), " ")
	name = strings.TrimSpace(name)
	if name == "" || name == "." || name == ".." {
		return "upload.bin"
	}
	if len(name) > 180 {
		name = name[:180]
	}
	return name
}

func ensurePrivateUploadTree(path string) (*os.File, error) {
	privateRoot := filepath.Join(os.TempDir(), fmt.Sprintf("yolomux-server-%d", os.Getuid()), "shared")
	cleanPath := filepath.Clean(path)
	privateRelative, err := filepath.Rel(privateRoot, cleanPath)
	if err != nil || privateRelative == ".." || strings.HasPrefix(privateRelative, ".."+string(os.PathSeparator)) || filepath.IsAbs(privateRelative) {
		return nil, errors.New("upload target escaped private root")
	}
	relative, err := filepath.Rel(os.TempDir(), cleanPath)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) || filepath.IsAbs(relative) {
		return nil, errors.New("upload target escaped private root")
	}
	current, err := os.Open(os.TempDir())
	if err != nil {
		return nil, err
	}
	for _, part := range strings.Split(relative, string(os.PathSeparator)) {
		if part == "" || part == "." {
			continue
		}
		child, openErr := openAt(current, part, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
		if errors.Is(openErr, os.ErrNotExist) {
			if mkdirErr := mkdirAt(current, part, 0o700); mkdirErr != nil && !errors.Is(mkdirErr, syscall.EEXIST) {
				_ = current.Close()
				return nil, mkdirErr
			}
			child, openErr = openAt(current, part, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
		}
		if openErr != nil {
			_ = current.Close()
			return nil, openErr
		}
		info, statErr := child.Stat()
		if statErr != nil {
			_ = child.Close()
			_ = current.Close()
			return nil, statErr
		}
		if !info.IsDir() {
			_ = child.Close()
			_ = current.Close()
			return nil, errors.New("unsafe private upload directory")
		}
		if stat, ok := info.Sys().(*syscall.Stat_t); !ok || stat.Uid != uint32(os.Geteuid()) {
			_ = child.Close()
			_ = current.Close()
			return nil, errors.New("unsafe private upload directory")
		}
		if chmodErr := child.Chmod(0o700); chmodErr != nil {
			_ = child.Close()
			_ = current.Close()
			return nil, chmodErr
		}
		_ = current.Close()
		current = child
	}
	return current, nil
}

func saveUploadAt(ctx context.Context, directory *os.File, original string, data []byte) (string, error) {
	return saveUploadAtWithTemplate(ctx, directory, original, data, os.Getenv("YOLOMUX_UPLOAD_FILENAME_TEMPLATE"))
}

func saveUploadAtWithTemplate(ctx context.Context, directory *os.File, original string, data []byte, template string) (string, error) {
	safeName := sanitizeUploadFilename(original)
	template = strings.TrimSpace(template)
	if template == "" {
		template = defaultUploadFilenameTemplate
	}
	date := time.Now().Format("20060102")
	base := strings.TrimSuffix(safeName, filepath.Ext(safeName))
	extension := filepath.Ext(safeName)
	for sequence := 1; sequence < 1000; sequence++ {
		candidate := safeName
		if pasteDate, pasteSequence, pasteSuffix, ok := parsePasteUploadName(safeName); ok && template == defaultUploadFilenameTemplate {
			candidate = fmt.Sprintf("%s-%03d%s", pasteDate, pasteSequence+sequence-1, pasteSuffix)
		} else {
			candidate = strings.ReplaceAll(template, "{date:%Y%m%d}", date)
			candidate = strings.ReplaceAll(candidate, "{date}", date)
			candidate = strings.ReplaceAll(candidate, "{seq:03d}", fmt.Sprintf("%03d", sequence))
			candidate = strings.ReplaceAll(candidate, "{seq}", fmt.Sprint(sequence))
			candidate = strings.ReplaceAll(candidate, "{name}", base)
			candidate = strings.ReplaceAll(candidate, "{ext}", extension)
			candidate = sanitizeUploadFilename(candidate)
		}
		file, err := openAt(directory, candidate, syscall.O_WRONLY|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW, 0o600)
		if errors.Is(err, syscall.EEXIST) {
			continue
		}
		if err != nil {
			return "", err
		}
		for offset := 0; offset < len(data) && err == nil; {
			if contextErr := ctx.Err(); contextErr != nil {
				err = contextErr
				break
			}
			end := offset + 64*1024
			if end > len(data) {
				end = len(data)
			}
			var count int
			count, err = file.Write(data[offset:end])
			offset += count
			if err == nil && count == 0 {
				err = io.ErrShortWrite
			}
		}
		if err == nil {
			if contextErr := ctx.Err(); contextErr != nil {
				err = contextErr
			} else {
				err = file.Sync()
			}
		}
		if err == nil {
			err = ctx.Err()
		}
		closeErr := file.Close()
		if err == nil {
			err = closeErr
		}
		if err != nil {
			_ = unlinkAt(directory, candidate, false)
			return "", err
		}
		return candidate, nil
	}
	return "", errors.New("unable to reserve upload filename")
}

func parsePasteUploadName(name string) (string, int, string, bool) {
	if len(name) < 13 || name[8] != '-' || name[12] != '.' {
		return "", 0, "", false
	}
	for index, value := range name {
		if index < 8 && (value < '0' || value > '9') {
			return "", 0, "", false
		}
	}
	sequence, err := strconv.Atoi(name[9:12])
	if err != nil || sequence < 0 {
		return "", 0, "", false
	}
	suffix := name[12:]
	if len(suffix) < 2 || len(suffix) > 9 {
		return "", 0, "", false
	}
	return name[:8], sequence, suffix, true
}
