// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"html"
	"io"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	MaxDirectoryEntries      = 1000
	MaxReadBytes             = 20 * 1024 * 1024
	MaxRawBytes              = 100 * 1024 * 1024
	MaxSearchResults         = 2_000
	MaxWatchRoots            = 128
	MaxWatchFiles            = 128
	maxWatchSignatureEntries = 4096
	watchPollInterval        = 500 * time.Millisecond
	watchLeaseDuration       = 300 * time.Second
	watchHistoryLimit        = 128
	watchEventCoalesceWindow = time.Second
)

type EventPublisher func(kind string, payload map[string]any) error

type UploadLimits struct {
	MaxBytes         int64
	MaxFiles         int
	FilenameTemplate string
}

type Error struct {
	Status        int
	Code          string
	MessageKey    string
	MessageParams map[string]any
	Details       map[string]any
	Origin        string
	Stack         []map[string]any
	Path          string
	Retryable     bool
	Message       string
	Partial       bool
	DeleteReason  string
	FailedPath    string
	DeletedPaths  []string
}

func (e *Error) Error() string { return e.Message }

func (e *Error) Payload() map[string]any {
	payload := map[string]any{
		"error":       e.Message,
		"error_code":  e.Code,
		"message_key": e.MessageKey,
		"retryable":   e.Retryable,
		"status":      e.Status,
	}
	if e.Path != "" {
		payload["path"] = e.Path
	}
	if e.DeleteReason != "" || e.FailedPath != "" || e.DeletedPaths != nil {
		payload["partial"] = e.Partial
		payload["delete_reason"] = e.DeleteReason
		payload["failed_path"] = e.FailedPath
		payload["deleted_paths"] = append([]string(nil), e.DeletedPaths...)
	}
	return payload
}

func invalid(path, message string) *Error {
	return &Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalid", Path: path, Message: message}
}

func forbidden(path, messageKey, message string) *Error {
	return &Error{Status: http.StatusForbidden, Code: "forbidden", MessageKey: messageKey, Path: path, Message: message}
}

func notFound(path string) *Error {
	return &Error{Status: http.StatusNotFound, Code: "path_not_found", MessageKey: "common.pathNotFound", Path: path, Message: "path not found"}
}

func tooLarge(path string, size, limit int64) *Error {
	return &Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.tooLarge", Path: path, Message: fmt.Sprintf("file is too large (%d bytes; max %d)", size, limit)}
}

func changed(path string) *Error {
	return &Error{Status: http.StatusConflict, Code: "changed_on_disk", MessageKey: "fs.error.changedOnDisk", Path: path, Message: "file changed on disk"}
}

type Service struct {
	root                       string
	uploadMu                   sync.RWMutex
	uploadLimits               UploadLimits
	publish                    EventPublisher
	mu                         sync.Mutex
	nativeMu                   sync.Mutex
	watches                    map[string]watchRecord
	watchGeneration            uint64
	epoch                      string
	revision                   uint64
	changes                    []watchChange
	native                     nativeWatchController
	nativeStatus               nativeWatchStatus
	publishGitRename           func(context.Context, *gitRenamePlan, string) error
	beforeWriteConflictRecheck func()
	stop                       chan struct{}
	done                       chan struct{}
	stopOnce                   sync.Once
	lastChangeAt               time.Time
	lastChangePaths            []string
	gitProductMu               sync.Mutex
	gitHistoryCache            map[string]map[string]any
	gitCommitCache             map[string]map[string]any
	gitHistoryStatsCache       map[string]gitHistoryStatsInfo
	gitHistoryStatsCacheOrder  []string
}

type watchRecord struct {
	Roots           []string
	Files           []string
	BackgroundFiles []string
	RootSurfaces    []map[string]any
	ContextItems    []map[string]any
	SessionFiles    []map[string]any
	ActivitySummary map[string]any
	Signatures      map[string]string
	UpdatedAt       time.Time
	ExpiresAt       time.Time
	Generation      uint64
}

type watchChange struct {
	revision     uint64
	paths        []string
	removedRoots []string
}

type walkedEntry struct {
	Name     string
	Relative string
	Path     string
	Resolved string
	File     *os.File
	Info     os.FileInfo
	IsDir    bool
}

// walkDescriptors keeps every child operation relative to the already-pinned parent descriptor.
// The callback runs while a child descriptor is live; the walker closes it immediately afterward.
func (s *Service) walkDescriptors(ctx context.Context, root *os.File, requestedRoot, resolvedRoot string, callback func(walkedEntry) error) error {
	var walk func(*os.File, string) error
	walk = func(directory *os.File, relative string) error {
		entries, err := directory.ReadDir(MaxDirectoryEntries + 1)
		if err != nil && !errors.Is(err, io.EOF) {
			return err
		}
		directoryLimited := len(entries) > MaxDirectoryEntries
		if directoryLimited {
			entries = entries[:MaxDirectoryEntries]
		}
		sort.Slice(entries, func(i, j int) bool { return entries[i].Name() < entries[j].Name() })
		for _, entry := range entries {
			select {
			case <-ctx.Done():
				return ctx.Err()
			default:
			}
			name := entry.Name()
			childRelative := filepath.Join(relative, name)
			requested := filepath.Join(requestedRoot, childRelative)
			linkInfo, statErr := os.Lstat(descriptorChildPath(directory, name))
			if statErr != nil || linkInfo.Mode()&os.ModeSymlink != 0 {
				continue
			}
			if !linkInfo.Mode().IsRegular() && !linkInfo.IsDir() {
				continue
			}
			if name == ".git" {
				continue
			}
			child, openErr := openAt(directory, name, syscall.O_RDONLY, 0)
			if openErr != nil {
				continue
			}
			info, infoErr := child.Stat()
			if infoErr != nil {
				_ = child.Close()
				continue
			}
			if !sameDescriptorIdentity(identityFor(linkInfo), identityFor(info)) {
				_ = child.Close()
				continue
			}
			resolved := filepath.Join(resolvedRoot, childRelative)
			walked := walkedEntry{Name: name, Relative: filepath.ToSlash(childRelative), Path: requested, Resolved: resolved, File: child, Info: info, IsDir: info.IsDir()}
			callbackErr := callback(walked)
			if closeErr := child.Close(); callbackErr == nil && closeErr != nil {
				callbackErr = closeErr
			}
			if errors.Is(callbackErr, errWalkSkipDirectory) {
				continue
			}
			if callbackErr != nil {
				return callbackErr
			}
			if info.IsDir() {
				// Re-open the directory relative to the authorized parent, without following a
				// replacement symlink.  The name can be changed after the first descriptor was
				// consumed, so verify that this descriptor is still the same directory before
				// recursing into it.
				child, openErr = openAt(directory, name, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
				if openErr != nil {
					continue
				}
				reopenedInfo, statErr := child.Stat()
				if statErr != nil || !reopenedInfo.IsDir() || !sameDescriptorIdentity(identityFor(reopenedInfo), identityFor(info)) {
					_ = child.Close()
					continue
				}
				if err := walk(child, childRelative); err != nil {
					_ = child.Close()
					return err
				}
				_ = child.Close()
			}
		}
		if directoryLimited {
			return errWalkDirectoryLimit
		}
		return nil
	}
	return walk(root, "")
}

func New(root string, publish EventPublisher) (*Service, error) {
	return NewWithLimits(root, publish, UploadLimits{MaxBytes: MaxUploadBytes, MaxFiles: MaxUploadFiles})
}

func NewWithLimits(root string, publish EventPublisher, uploadLimits UploadLimits) (*Service, error) {
	if root == "" {
		return nil, errors.New("finder root is empty")
	}
	canonical, err := filepath.EvalSymlinks(root)
	if err != nil {
		return nil, fmt.Errorf("finder root: %w", err)
	}
	canonical, err = filepath.Abs(canonical)
	if err != nil {
		return nil, err
	}
	native, nativeStatus := newNativeWatcher()
	if uploadLimits.MaxBytes <= 0 {
		uploadLimits.MaxBytes = MaxUploadBytes
	}
	if uploadLimits.MaxFiles <= 0 {
		uploadLimits.MaxFiles = MaxUploadFiles
	}
	s := newService(canonical, publish, native, nativeStatus, uploadLimits)
	return s, nil
}

func newService(root string, publish EventPublisher, native nativeWatchController, nativeStatus nativeWatchStatus, uploadLimits UploadLimits) *Service {
	s := &Service{root: root, uploadLimits: uploadLimits, publish: publish, watches: make(map[string]watchRecord), native: native, nativeStatus: nativeStatus, epoch: fmt.Sprintf("finder-%d", time.Now().UnixNano()), stop: make(chan struct{}), done: make(chan struct{})}
	go s.watchLoop()
	return s
}

func (s *Service) Close() {
	s.stopOnce.Do(func() {
		close(s.stop)
		if s.native != nil {
			s.nativeMu.Lock()
			s.native.Close()
			s.nativeMu.Unlock()
		}
	})
	<-s.done
}

func (s *Service) Root() string { return s.root }

// SetUploadLimits applies settings changes to uploads already served by this
// process. The settings file is shared with Python, but the Finder service
// keeps its upload policy in memory for the duration of an upload request.
func (s *Service) SetUploadLimits(uploadLimits UploadLimits) {
	if uploadLimits.MaxBytes <= 0 {
		uploadLimits.MaxBytes = MaxUploadBytes
	}
	if uploadLimits.MaxFiles <= 0 {
		uploadLimits.MaxFiles = MaxUploadFiles
	}
	s.uploadMu.Lock()
	s.uploadLimits = uploadLimits
	s.uploadMu.Unlock()
}

func (s *Service) currentUploadLimits() UploadLimits {
	s.uploadMu.RLock()
	defer s.uploadMu.RUnlock()
	return s.uploadLimits
}

func (s *Service) WatchStatus() (available, degraded bool, reason string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.nativeStatus.available, s.nativeStatus.degraded, s.nativeStatus.reason
}

func (s *Service) authorize(raw string, allowMissing bool) (string, string, *Error) {
	if raw == "" || !filepath.IsAbs(raw) || strings.ContainsRune(raw, 0) {
		return "", "", invalid(raw, "path must be an absolute path")
	}
	requested := filepath.Clean(raw)
	resolved, err := filepath.EvalSymlinks(requested)
	if err != nil {
		if !allowMissing || !errors.Is(err, os.ErrNotExist) {
			if errors.Is(err, os.ErrNotExist) {
				return "", "", notFound(requested)
			}
			return "", "", &Error{Status: http.StatusInternalServerError, Code: "filesystem_error", MessageKey: "fs.error.operationFailed", Path: requested, Retryable: true, Message: err.Error()}
		}
		parent, parentErr := filepath.EvalSymlinks(filepath.Dir(requested))
		if parentErr != nil {
			return "", "", notFound(requested)
		}
		resolved = filepath.Join(parent, filepath.Base(requested))
	}
	resolved, err = filepath.Abs(resolved)
	if err != nil {
		return "", "", &Error{Status: http.StatusInternalServerError, Code: "filesystem_error", MessageKey: "fs.error.operationFailed", Path: requested, Retryable: true, Message: err.Error()}
	}
	return requested, filepath.Clean(resolved), nil
}

func (s *Service) open(raw string, directory bool) (*os.File, string, string, *Error) {
	file, requested, resolved, authErr := s.openAny(raw)
	if authErr != nil {
		return nil, "", "", authErr
	}
	info, err := file.Stat()
	if err != nil {
		_ = file.Close()
		return nil, "", "", filesystemError(requested, err)
	}
	if directory && !info.IsDir() {
		_ = file.Close()
		return nil, "", "", invalid(requested, "path is not a directory")
	}
	if !directory && info.IsDir() {
		_ = file.Close()
		return nil, "", "", invalid(requested, "path is a directory")
	}
	return file, requested, resolved, nil
}

// openAny authorizes and consumes one descriptor without imposing a file/directory kind. Info
// needs this shared path because Python's path_info exposes both capabilities, while list/read
// impose their kind after the descriptor has been pinned.
func (s *Service) openAny(raw string) (*os.File, string, string, *Error) {
	requested, resolved, authErr := s.authorize(raw, false)
	if authErr != nil {
		return nil, "", "", authErr
	}
	identities, err := captureResolvedIdentities(resolved)
	if err != nil {
		return nil, "", "", filesystemError(requested, err)
	}
	file, err := openResolvedDescriptor(resolved, false, identities)
	if err != nil {
		if errors.Is(err, errChangedOnDisk) {
			return nil, "", "", changed(requested)
		}
		if errors.Is(err, os.ErrNotExist) {
			return nil, "", "", notFound(requested)
		}
		if errors.Is(err, syscall.ELOOP) {
			return nil, "", "", changed(requested)
		}
		if errors.Is(err, os.ErrPermission) {
			return nil, "", "", forbidden(requested, "fs.error.permissionDenied", "permission denied")
		}
		return nil, "", "", &Error{Status: http.StatusInternalServerError, Code: "filesystem_error", MessageKey: "fs.error.operationFailed", Path: requested, Retryable: true, Message: err.Error()}
	}
	if _, err := file.Stat(); err != nil {
		file.Close()
		return nil, "", "", &Error{Status: http.StatusInternalServerError, Code: "filesystem_error", MessageKey: "fs.error.operationFailed", Path: requested, Retryable: true, Message: err.Error()}
	}
	if err := verifyOpenedChain(file, resolved, identities); err != nil {
		_ = file.Close()
		if errors.Is(err, errChangedOnDisk) {
			return nil, "", "", changed(requested)
		}
		return nil, "", "", filesystemError(requested, err)
	}
	return file, requested, resolved, nil
}

func (s *Service) FastList(ctx context.Context, raw string) (map[string]any, *Error) {
	return s.listWithEntryInfo(ctx, raw, func(directory *os.File, requestedDir, resolvedDir string, entry os.DirEntry) (map[string]any, bool) {
		return fastEntryInfoAt(directory, resolvedDir, entry)
	})
}

func (s *Service) List(ctx context.Context, raw string) (map[string]any, *Error) {
	return s.list(ctx, raw, true)
}

func (s *Service) ListWithOptions(ctx context.Context, raw string, includeRepo bool) (map[string]any, *Error) {
	return s.list(ctx, raw, includeRepo)
}

func (s *Service) list(ctx context.Context, raw string, includeRepo bool) (map[string]any, *Error) {
	return s.listWithEntryInfo(ctx, raw, func(directory *os.File, requestedDir, resolvedDir string, entry os.DirEntry) (map[string]any, bool) {
		return s.entryInfoAt(directory, requestedDir, resolvedDir, entry.Name(), includeRepo)
	})
}

func (s *Service) listWithEntryInfo(ctx context.Context, raw string, entryInfo func(*os.File, string, string, os.DirEntry) (map[string]any, bool)) (map[string]any, *Error) {
	directory, requested, resolved, authErr := s.open(raw, true)
	if authErr != nil {
		return nil, authErr
	}
	defer directory.Close()
	entries, err := directory.ReadDir(MaxDirectoryEntries + 1)
	if err != nil && !errors.Is(err, io.EOF) {
		return nil, &Error{Status: http.StatusInternalServerError, Code: "filesystem_error", MessageKey: "fs.error.operationFailed", Path: requested, Retryable: true, Message: err.Error()}
	}
	truncated := len(entries) > MaxDirectoryEntries
	if truncated {
		entries = entries[:MaxDirectoryEntries]
	}
	result := make([]map[string]any, 0, len(entries))
	for _, entry := range entries {
		select {
		case <-ctx.Done():
			return nil, &Error{Status: http.StatusRequestTimeout, Code: "cancelled", MessageKey: "request.error.cancelled", Path: requested, Retryable: true, Message: "directory listing cancelled"}
		default:
		}
		row, include := entryInfo(directory, requested, resolved, entry)
		if include {
			result = append(result, row)
		}
	}
	sort.Slice(result, func(i, j int) bool {
		leftDir := result[i]["kind"] == "dir"
		rightDir := result[j]["kind"] == "dir"
		if leftDir != rightDir {
			return leftDir
		}
		return strings.ToLower(fmt.Sprint(result[i]["name"])) < strings.ToLower(fmt.Sprint(result[j]["name"]))
	})
	var parent any = filepath.Dir(requested)
	if requested == string(filepath.Separator) {
		parent = nil
	}
	return map[string]any{"path": requested, "parent": parent, "entries": result, "truncated": truncated, "entry_limit": MaxDirectoryEntries}, nil
}

// fastEntryInfoAt is intentionally a metadata snapshot.  FastList is the first-paint route: it
// must not reopen every child through the full descriptor authorization chain or inspect Git for
// every directory.  Actions that consume a selected path still re-authorize it through Info or a
// mutation, and the deferred INFO wave supplies repository metadata after the rows are visible.
func fastEntryInfoAt(directory *os.File, resolvedDir string, entry os.DirEntry) (map[string]any, bool) {
	linkInfo, err := entry.Info()
	if err != nil {
		if linkInfo != nil && entry.Type()&os.ModeSymlink != 0 {
			return brokenSymlinkEntry(directory, entry.Name(), linkInfo)
		}
		return nil, false
	}
	isSymlink := linkInfo.Mode()&os.ModeSymlink != 0
	targetInfo := linkInfo
	resolved := filepath.Join(resolvedDir, entry.Name())
	if isSymlink {
		targetInfo, err = os.Stat(descriptorChildPath(directory, entry.Name()))
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				return brokenSymlinkEntry(directory, entry.Name(), linkInfo)
			}
			return nil, false
		}
		resolved, err = filepath.EvalSymlinks(descriptorChildPath(directory, entry.Name()))
		if err != nil {
			return nil, false
		}
		resolved, err = filepath.Abs(resolved)
		if err != nil {
			return nil, false
		}
	}
	kind := "other"
	if targetInfo.IsDir() {
		kind = "dir"
	} else if targetInfo.Mode().IsRegular() {
		kind = "file"
	}
	size := targetInfo.Size()
	if kind == "dir" {
		size = 0
	}
	row := map[string]any{
		"name": entry.Name(), "kind": kind, "size": size, "mtime": linkInfo.ModTime().Unix(),
		"mtime_ns": linkInfo.ModTime().UnixNano(), "is_symlink": isSymlink, "realpath": resolved,
		"file_id": fileID(targetInfo), "file_identity": "id:" + fileID(targetInfo),
	}
	if isSymlink {
		if target, targetErr := os.Readlink(descriptorChildPath(directory, entry.Name())); targetErr == nil {
			row["symlink_target"] = target
		}
	}
	if kind == "dir" {
		// Match Python's fast listing contract: the descriptor-relative repository marker probe is
		// cheap enough for first paint, while branch/status/history metadata stays in the later INFO
		// wave. Do not mark ordinary directories for enrichment; a large tree otherwise turns one
		// fast listing into an INFO request for every child directory.
		if isSymlink {
			// A symlinked directory needs the full target-aware probe so FastList preserves the
			// existing repository marker contract; ordinary directories take the cheap parent-FD path.
			row["is_repo"] = isRepositoryDescriptor(directory, entry.Name())
		} else {
			row["is_repo"] = isRepositoryMarkerAt(directory, entry.Name())
		}
		if row["is_repo"] == true {
			row["repo_info_deferred"] = true
		}
	}
	return row, true
}

// entryInfoAt consumes a directory entry relative to the directory descriptor that was already
// authorized.  In particular, it never turns the entry name back into an absolute pathname for a
// second authorization/open operation.
func (s *Service) entryInfoAt(directory *os.File, requestedDir, resolvedDir, name string, includeRepo bool) (map[string]any, bool) {
	child, linkInfo, targetInfo, resolved, err := openDescriptorEntry(directory, name)
	if err != nil {
		if linkInfo != nil && linkInfo.Mode()&os.ModeSymlink != 0 && errors.Is(err, os.ErrNotExist) {
			return brokenSymlinkEntry(directory, name, linkInfo)
		}
		return nil, false
	}
	if targetInfo == nil {
		if linkInfo != nil && linkInfo.Mode()&os.ModeSymlink != 0 {
			return brokenSymlinkEntry(directory, name, linkInfo)
		}
		return nil, false
	}
	if child == nil || linkInfo == nil {
		return nil, false
	}
	defer child.Close()
	isSymlink := linkInfo.Mode()&os.ModeSymlink != 0
	actualResolved, resolveErr := filepath.EvalSymlinks(descriptorPath(child))
	if resolveErr != nil {
		return nil, false
	}
	actualResolved, resolveErr = filepath.Abs(actualResolved)
	if resolveErr != nil {
		return nil, false
	}
	resolved = actualResolved
	kind := "other"
	if targetInfo.IsDir() {
		kind = "dir"
	} else if targetInfo.Mode().IsRegular() {
		kind = "file"
	}
	size := targetInfo.Size()
	if kind == "dir" {
		size = 0
	}
	row := map[string]any{
		"name": name, "kind": kind, "size": size, "mtime": linkInfo.ModTime().Unix(),
		"mtime_ns": linkInfo.ModTime().UnixNano(), "is_symlink": isSymlink, "realpath": resolved,
		"file_id": fileID(targetInfo), "file_identity": "id:" + fileID(targetInfo),
	}
	if isSymlink {
		if target, targetErr := os.Readlink(descriptorChildPath(directory, name)); targetErr == nil {
			row["symlink_target"] = target
		}
	}
	if kind == "dir" {
		row["is_repo"] = isRepositoryDescriptor(directory, name)
		if !includeRepo && row["is_repo"] == true {
			row["repo_info_deferred"] = true
		}
	}
	return row, true
}

func brokenSymlinkEntry(directory *os.File, name string, linkInfo os.FileInfo) (map[string]any, bool) {
	row := map[string]any{"name": name, "kind": "symlink-broken", "size": 0, "mtime": linkInfo.ModTime().Unix(), "mtime_ns": linkInfo.ModTime().UnixNano(), "is_symlink": true}
	if target, targetErr := os.Readlink(descriptorChildPath(directory, name)); targetErr == nil {
		row["symlink_target"] = target
	}
	return row, true
}

func (s *Service) entryInfo(raw string, entry os.DirEntry, includeRepo bool) map[string]any {
	row := map[string]any{"name": entry.Name(), "is_symlink": entry.Type()&os.ModeSymlink != 0}
	info, err := entry.Info()
	if err != nil {
		row["kind"] = "symlink-broken"
		row["size"] = 0
		return row
	}
	kind := "other"
	if info.IsDir() {
		kind = "dir"
	} else if info.Mode().IsRegular() {
		kind = "file"
	}
	row["kind"] = kind
	row["size"] = info.Size()
	row["mtime"] = info.ModTime().Unix()
	row["mtime_ns"] = info.ModTime().UnixNano()
	row["file_id"] = fileID(info)
	row["file_identity"] = "id:" + fileID(info)
	if resolved, err := filepath.EvalSymlinks(raw); err == nil {
		row["realpath"] = resolved
		if entry.Type()&os.ModeSymlink != 0 {
			if target, targetErr := os.Readlink(raw); targetErr == nil {
				row["symlink_target"] = target
			}
		}
	}
	if kind == "dir" {
		row["is_repo"] = includeRepo && isRepository(raw)
	}
	return row
}

func (s *Service) Read(ctx context.Context, raw string, includeGit ...bool) (map[string]any, *Error) {
	file, requested, resolved, authErr := s.open(raw, false)
	if authErr != nil {
		return nil, authErr
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, filesystemError(requested, err)
	}
	if info.Size() > MaxReadBytes {
		return nil, tooLarge(requested, info.Size(), MaxReadBytes)
	}
	data, err := readDescriptorContext(ctx, file, MaxReadBytes)
	if err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return nil, cancelled(requested)
		}
		return nil, &Error{Status: http.StatusInternalServerError, Code: "filesystem_error", MessageKey: "fs.error.operationFailed", Path: requested, Retryable: true, Message: err.Error()}
	}
	if len(data) > MaxReadBytes {
		return nil, tooLarge(requested, int64(len(data)), MaxReadBytes)
	}
	if !s.pathStillStable(file, info) {
		return nil, changed(requested)
	}
	if bytesLookBinary(data) {
		return nil, &Error{Status: http.StatusUnsupportedMediaType, Code: "binary", MessageKey: "fs.error.binary", Path: requested, Message: "file appears to be binary"}
	}
	select {
	case <-ctx.Done():
		return nil, &Error{Status: http.StatusRequestTimeout, Code: "cancelled", MessageKey: "request.error.cancelled", Path: requested, Retryable: true, Message: "file read cancelled"}
	default:
	}
	result := map[string]any{"path": requested, "realpath": resolved, "size": info.Size(), "mtime": info.ModTime().Unix(), "mtime_ns": info.ModTime().UnixNano(), "content": string(data), "extension": strings.ToLower(filepath.Ext(requested)), "is_text_extension": isTextPath(requested), "file_id": fileID(info), "file_identity": "id:" + fileID(info), "git_root": "", "git_tracked": false, "git_history": []any{}, "git_has_history": false, "git_enrichment": map[string]any{"available": false, "reason": "deferred"}}
	if len(includeGit) > 0 && includeGit[0] {
		s.addGitMetadata(ctx, result, requested, resolved, file)
		if !s.pathStillStable(file, info) {
			return nil, changed(requested)
		}
	}
	return result, nil
}

func (s *Service) Info(ctx context.Context, raw string, includeGit ...bool) (map[string]any, *Error) {
	select {
	case <-ctx.Done():
		return nil, &Error{Status: http.StatusRequestTimeout, Code: "cancelled", MessageKey: "request.error.cancelled", Path: raw, Retryable: true, Message: "file info cancelled"}
	default:
	}
	file, requested, resolved, openErr := s.openAny(raw)
	if openErr != nil {
		if openErr.Status != http.StatusNotFound {
			return nil, openErr
		}
		// A broken symlink has no target descriptor to consume. Probe the entry relative to its
		// authorized parent descriptor so this exceptional metadata path does not re-open the raw
		// absolute pathname after authorization.
		requested, _, authErr := s.authorize(raw, true)
		if authErr != nil {
			return nil, authErr
		}
		parentResolved, err := filepath.EvalSymlinks(filepath.Dir(requested))
		if err != nil {
			return nil, openErr
		}
		identities, err := captureResolvedIdentities(parentResolved)
		if err != nil {
			return nil, filesystemError(filepath.Dir(requested), err)
		}
		parent, err := openResolvedDescriptor(parentResolved, true, identities)
		if err != nil {
			return nil, filesystemError(filepath.Dir(requested), err)
		}
		defer parent.Close()
		_, linkInfo, targetInfo, childResolved, err := openDescriptorEntry(parent, filepath.Base(requested))
		if err != nil || linkInfo == nil || linkInfo.Mode()&os.ModeSymlink == 0 || targetInfo != nil {
			return nil, openErr
		}
		return map[string]any{
			"path": requested, "name": filepath.Base(requested), "kind": "symlink-broken", "size": nil,
			"mtime": nil, "mtime_ns": nil, "preview_mime": "", "diff_capable": false, "repo_root": "",
			"git_tracked": false, "git_has_history": false, "relative_path": "", "repo": nil,
			"git_enrichment": map[string]any{"available": false, "reason": "deferred"},
			"realpath":       childResolved,
		}, nil
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, filesystemError(requested, err)
	}
	if info.IsDir() {
		result := map[string]any{"path": requested, "name": filepath.Base(requested), "kind": "dir", "size": nil, "mtime": nil, "mtime_ns": nil, "realpath": resolved, "file_id": fileID(info), "file_identity": "id:" + fileID(info), "preview_mime": "", "diff_capable": false, "repo_root": "", "git_tracked": false, "git_has_history": false, "relative_path": "", "repo": nil, "git_enrichment": map[string]any{"available": false, "reason": "deferred"}}
		if len(includeGit) > 0 && includeGit[0] {
			s.addGitMetadata(ctx, result, requested, resolved, file)
			if !s.pathStillStable(file, info) {
				return nil, changed(requested)
			}
		}
		return result, nil
	}
	if !info.Mode().IsRegular() {
		return nil, invalid(requested, "path is not a regular file")
	}
	sample, err := readDescriptorContext(ctx, file, 512)
	if err != nil {
		return nil, filesystemError(requested, err)
	}
	preview := sniffContentType(sample, requested)
	diffCapable := info.Size() <= MaxReadBytes && preview == "" && !bytesLookBinary(sample)
	result := map[string]any{"path": requested, "name": filepath.Base(requested), "kind": "file", "size": info.Size(), "mtime": info.ModTime().Unix(), "mtime_ns": info.ModTime().UnixNano(), "realpath": resolved, "file_id": fileID(info), "file_identity": "id:" + fileID(info), "preview_mime": preview, "diff_capable": diffCapable, "repo_root": "", "git_tracked": false, "git_has_history": false, "relative_path": "", "repo": nil, "git_enrichment": map[string]any{"available": false, "reason": "deferred"}}
	if len(includeGit) > 0 && includeGit[0] {
		s.addGitMetadata(ctx, result, requested, resolved, file)
		if !s.pathStillStable(file, info) {
			return nil, changed(requested)
		}
	} else if !s.pathStillStable(file, info) {
		return nil, changed(requested)
	}
	return result, nil
}

// HTMLPreview returns a sandboxed document matching the Python preview owner. The source is
// escaped before it enters srcdoc, and the iframe has no same-origin capability.
func (s *Service) HTMLPreview(ctx context.Context, raw, locale string, transferLimit ...int64) ([]byte, *Error) {
	extension := strings.ToLower(filepath.Ext(raw))
	if extension != ".html" && extension != ".htm" {
		return nil, &Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "fs.error.htmlFileRequired", Path: raw, Message: "path must be an HTML file"}
	}
	maxBytes := int64(MaxRawBytes)
	if len(transferLimit) > 0 && transferLimit[0] > 0 {
		maxBytes = transferLimit[0]
	}
	data, _, _, finderErr := s.Raw(ctx, raw, maxBytes)
	if finderErr != nil {
		return nil, finderErr
	}
	code := strings.TrimSpace(strings.SplitN(locale, ",", 2)[0])
	if code == "" {
		code = "en"
	}
	for index, value := range code {
		if !(value == '-' || value == '_' || value >= 'a' && value <= 'z' || value >= 'A' && value <= 'Z') || index > 15 {
			code = "en"
			break
		}
	}
	direction := "ltr"
	if strings.HasPrefix(strings.ToLower(code), "ar") {
		direction = "rtl"
	}
	title := html.EscapeString(filepath.Base(raw))
	if title == "" || title == "." {
		title = "HTML Preview"
	}
	escapedSource := html.EscapeString(string(data))
	document := fmt.Sprintf(`<!doctype html>
<html lang="%s" dir="%s">
<head>
  <meta charset="utf-8">
  <title>%s</title>
  <style>
    html, body, iframe { width: 100%%; height: 100%%; margin: 0; border: 0; background: #fff; }
    iframe { display: block; }
  </style>
</head>
<body>
  <iframe title="%s" sandbox="allow-scripts allow-forms allow-popups" srcdoc="%s"></iframe>
</body>
</html>`, html.EscapeString(code), direction, title, title, escapedSource)
	return []byte(document), nil
}

func (s *Service) Raw(ctx context.Context, raw string, maxBytes int64) ([]byte, string, string, *Error) {
	if maxBytes <= 0 {
		maxBytes = MaxRawBytes
	}
	file, requested, _, authErr := s.open(raw, false)
	if authErr != nil {
		return nil, "", "", authErr
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, "", "", filesystemError(requested, err)
	}
	if info.Size() > maxBytes {
		return nil, "", "", tooLarge(requested, info.Size(), maxBytes)
	}
	data, err := readDescriptorContext(ctx, file, maxBytes)
	if err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return nil, "", "", cancelled(requested)
		}
		return nil, "", "", &Error{Status: http.StatusInternalServerError, Code: "filesystem_error", MessageKey: "fs.error.operationFailed", Path: requested, Retryable: true, Message: err.Error()}
	}
	if int64(len(data)) > maxBytes {
		return nil, "", "", tooLarge(requested, int64(len(data)), maxBytes)
	}
	if !s.pathStillStable(file, info) {
		return nil, "", "", changed(requested)
	}
	select {
	case <-ctx.Done():
		return nil, "", "", &Error{Status: http.StatusRequestTimeout, Code: "cancelled", MessageKey: "request.error.cancelled", Path: requested, Retryable: true, Message: "raw read cancelled"}
	default:
	}
	contentType := mime.TypeByExtension(strings.ToLower(filepath.Ext(requested)))
	if contentType == "" {
		contentType = http.DetectContentType(data)
	}
	if contentType == "application/octet-stream" && len(data) == 0 {
		contentType = "application/octet-stream"
	}
	return data, contentType, filepath.Base(requested), nil
}

func (s *Service) RawRange(ctx context.Context, raw string, maxBytes, start, end int64) ([]byte, string, string, int64, *Error) {
	if start < 0 || end < start {
		return nil, "", "", 0, invalid(raw, "invalid byte range")
	}
	if maxBytes <= 0 {
		maxBytes = MaxRawBytes
	}
	file, requested, _, authErr := s.open(raw, false)
	if authErr != nil {
		return nil, "", "", 0, authErr
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, "", "", 0, filesystemError(requested, err)
	}
	if info.Size() > maxBytes {
		return nil, "", "", info.Size(), tooLarge(requested, info.Size(), maxBytes)
	}
	if start >= info.Size() && info.Size() > 0 {
		return nil, "", "", info.Size(), &Error{Status: http.StatusRequestedRangeNotSatisfiable, Code: "range_not_satisfiable", MessageKey: "fs.error.rangeNotSatisfiable", Path: requested, Message: "byte range is outside the file"}
	}
	if info.Size() == 0 {
		return nil, "", "", info.Size(), &Error{Status: http.StatusRequestedRangeNotSatisfiable, Code: "range_not_satisfiable", MessageKey: "fs.error.rangeNotSatisfiable", Path: requested, Message: "byte range is outside the file"}
	} else if end >= info.Size() {
		end = info.Size() - 1
	}
	length := int64(0)
	if end >= start {
		length = end - start + 1
		if _, err := file.Seek(start, io.SeekStart); err != nil {
			return nil, "", "", info.Size(), filesystemError(requested, err)
		}
	}
	data := make([]byte, length)
	if length > 0 {
		if err := readFullDescriptorContext(ctx, file, data); err != nil {
			if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
				return nil, "", "", info.Size(), cancelled(requested)
			}
			return nil, "", "", info.Size(), filesystemError(requested, err)
		}
	}
	if !s.pathStillStable(file, info) {
		return nil, "", "", info.Size(), changed(requested)
	}
	select {
	case <-ctx.Done():
		return nil, "", "", info.Size(), cancelled(requested)
	default:
	}
	contentType := mime.TypeByExtension(strings.ToLower(filepath.Ext(requested)))
	if contentType == "" {
		contentType = http.DetectContentType(data)
	}
	return data, contentType, filepath.Base(requested), info.Size(), nil
}

func (s *Service) WatchRoots(ctx context.Context, payload map[string]any) (map[string]any, *Error) {
	clientID, _ := payload["client_id"].(string)
	if clientID == "" {
		clientID = "legacy"
	}
	roots, err := stringList(payload["roots"], MaxWatchRoots)
	if err != nil {
		return nil, invalid("", err.Error())
	}
	files, err := stringList(payload["files"], MaxWatchFiles)
	if err != nil {
		return nil, invalid("", err.Error())
	}
	normalized := make([]string, 0, len(roots))
	for _, raw := range roots {
		requested, _, authErr := s.authorize(raw, false)
		if authErr != nil {
			return nil, authErr
		}
		normalized = append(normalized, requested)
	}
	normalizedFiles := make([]string, 0, len(files))
	for _, raw := range files {
		requested, _, authErr := s.authorize(raw, true)
		if authErr != nil {
			return nil, authErr
		}
		normalizedFiles = append(normalizedFiles, requested)
	}
	files = normalizedFiles
	watchPayload, payloadErr := s.normalizeWatchPayload(payload, normalized, files)
	if payloadErr != nil {
		return nil, payloadErr
	}
	if err := ctx.Err(); err != nil {
		return nil, cancelled("")
	}
	signaturePaths := append(append(append([]string{}, normalized...), files...), watchPayload.BackgroundFiles...)
	signatures := make(map[string]string, len(signaturePaths))
	for _, path := range signaturePaths {
		if err := ctx.Err(); err != nil {
			return nil, cancelled(path)
		}
		signatures[path] = s.signature(ctx, path)
	}
	s.mu.Lock()
	if _, exists := s.watches[clientID]; !exists && len(s.watches) >= maxActiveWatchClients {
		s.mu.Unlock()
		return nil, &Error{Status: http.StatusTooManyRequests, Code: "watch_capacity_exceeded", MessageKey: "fs.error.watchCapacity", Retryable: true, Message: "watch client capacity exceeded"}
	}
	activeRoots := 0
	activeFiles := 0
	for existingID, record := range s.watches {
		if existingID != clientID {
			activeRoots += len(record.Roots)
			activeFiles += len(record.Files) + len(record.BackgroundFiles)
		}
	}
	if activeRoots+len(normalized) > maxActiveWatchRoots {
		s.mu.Unlock()
		return nil, &Error{Status: http.StatusTooManyRequests, Code: "watch_capacity_exceeded", MessageKey: "fs.error.watchCapacity", Retryable: true, Message: "watch root capacity exceeded"}
	}
	if activeFiles+len(files)+len(watchPayload.BackgroundFiles) > maxActiveWatchFiles {
		s.mu.Unlock()
		return nil, &Error{Status: http.StatusTooManyRequests, Code: "watch_capacity_exceeded", MessageKey: "fs.error.watchCapacity", Retryable: true, Message: "watch file capacity exceeded"}
	}
	previous, hadPrevious := s.watches[clientID]
	removedRoots := removedWatchRoots(previous.Roots, normalized)
	rootsChanged := hadPrevious && !sameWatchRoots(previous.Roots, normalized)
	now := time.Now()
	s.watchGeneration++
	generation := s.watchGeneration
	s.watches[clientID] = watchRecord{
		Roots: normalized, Files: files, BackgroundFiles: watchPayload.BackgroundFiles,
		RootSurfaces: watchPayload.RootSurfaces, ContextItems: watchPayload.ContextItems,
		SessionFiles: watchPayload.SessionFiles, ActivitySummary: watchPayload.ActivitySummary,
		Signatures: signatures, UpdatedAt: now, ExpiresAt: now.Add(watchLeaseDuration), Generation: generation,
	}
	result := map[string]any{
		"ok": true, "roots": normalized, "files": files, "background_files": watchPayload.BackgroundFiles,
		"removed_roots":         removedRoots,
		"root_surfaces_version": watchPayload.RootSurfacesVersion, "root_surfaces": watchPayload.RootSurfaces,
		"context_items": watchPayload.ContextItems, "session_files": watchPayload.SessionFiles,
		"activity_summary": watchPayload.ActivitySummary, "mode": "ttl-fallback", "ttl_seconds": 30,
	}
	result["watch_token"] = s.watchTokenLocked()
	result["revision"] = s.revision
	result["watch_status"] = s.watchStatusPayloadLocked()
	result["watch_degraded"] = s.nativeStatus.degraded
	result["watch_degradation_reason"] = s.nativeStatus.reason
	s.mu.Unlock()
	if err := s.reconfigureNative(ctx); err != nil {
		s.mu.Lock()
		current, exists := s.watches[clientID]
		if exists && current.Generation == generation {
			if hadPrevious {
				s.watches[clientID] = previous
			} else {
				delete(s.watches, clientID)
			}
		}
		s.mu.Unlock()
		// SetRoots may have applied a partial registration before observing cancellation. Restore
		// the native state for the surviving watch set before returning the cancelled request.
		_ = s.reconfigureNative(context.Background())
		return nil, cancelled("")
	}
	s.mu.Lock()
	var rootChangePayload map[string]any
	if rootsChanged {
		s.revision++
		changedRoots := append(append([]string{}, previous.Roots...), normalized...)
		changedRoots = uniqueWatchPaths(changedRoots)
		s.changes = append(s.changes, watchChange{revision: s.revision, paths: changedRoots, removedRoots: append([]string(nil), removedRoots...)})
		if len(s.changes) > watchHistoryLimit {
			s.changes = s.changes[len(s.changes)-watchHistoryLimit:]
		}
		rootChangePayload = map[string]any{
			"paths": changedRoots, "roots": normalized, "removed_roots": removedRoots,
			"client_id": clientID, "reason": "watch-roots-replaced", "token": s.watchTokenLocked(), "revision": s.revision,
		}
	}
	result["mode"] = s.watchModeLocked()
	result["ttl_seconds"] = int(watchLeaseDuration / time.Second)
	result["watch_token"] = s.watchTokenLocked()
	result["revision"] = s.revision
	result["watch_status"] = s.watchStatusPayloadLocked()
	result["watch_degraded"] = s.nativeStatus.degraded
	result["watch_degradation_reason"] = s.nativeStatus.reason
	s.mu.Unlock()
	if rootChangePayload != nil && s.publish != nil {
		_ = s.publish("fs_changed", rootChangePayload)
		_ = s.publish("files_changed", rootChangePayload)
	}
	return result, nil
}

func sameWatchRoots(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for index, value := range left {
		if value != right[index] {
			return false
		}
	}
	return true
}

func removedWatchRoots(previous, current []string) []string {
	currentSet := make(map[string]struct{}, len(current))
	for _, root := range current {
		currentSet[root] = struct{}{}
	}
	removed := make([]string, 0)
	for _, root := range previous {
		if _, present := currentSet[root]; !present {
			removed = append(removed, root)
		}
	}
	return removed
}

func uniqueWatchPaths(paths []string) []string {
	seen := make(map[string]struct{}, len(paths))
	unique := make([]string, 0, len(paths))
	for _, path := range paths {
		if path == "" {
			continue
		}
		if _, present := seen[path]; present {
			continue
		}
		seen[path] = struct{}{}
		unique = append(unique, path)
	}
	return unique
}

func (s *Service) WatchDiff(ctx context.Context, since string, forceFull bool) (map[string]any, *Error) {
	s.pruneExpiredWatches(time.Now())
	if err := ctx.Err(); err != nil {
		return nil, cancelled("")
	}
	s.mu.Lock()
	epoch := s.epoch
	oldRevision, oldEpoch, validToken := parseWatchToken(since)
	full := forceFull || since == "" || !validToken || oldEpoch != epoch
	if !full && len(s.changes) > 0 && oldRevision < s.changes[0].revision-1 {
		full = true
	}
	changed := make([]string, 0)
	removedRoots := make([]string, 0)
	if !full {
		seen := map[string]bool{}
		seenRemovedRoots := map[string]bool{}
		for _, change := range s.changes {
			if change.revision <= oldRevision {
				continue
			}
			for _, path := range change.paths {
				if !seen[path] {
					seen[path] = true
					changed = append(changed, path)
				}
			}
			for _, root := range change.removedRoots {
				if !seenRemovedRoots[root] {
					seenRemovedRoots[root] = true
					removedRoots = append(removedRoots, root)
				}
			}
		}
	}
	watchRoots := make([]string, 0)
	watchFiles := make([]string, 0)
	seenRoots := map[string]bool{}
	seenFiles := map[string]bool{}
	for _, record := range s.watches {
		for _, path := range record.Roots {
			if !seenRoots[path] {
				seenRoots[path] = true
				watchRoots = append(watchRoots, path)
			}
		}
		for _, path := range record.Files {
			if !seenFiles[path] {
				seenFiles[path] = true
				watchFiles = append(watchFiles, path)
			}
		}
		for _, path := range record.BackgroundFiles {
			if !seenFiles[path] {
				seenFiles[path] = true
				watchFiles = append(watchFiles, path)
			}
		}
	}
	token := s.watchTokenLocked()
	s.mu.Unlock()
	sort.Strings(watchRoots)
	sort.Strings(watchFiles)
	if full {
		changed = append([]string(nil), watchRoots...)
	}
	if !full && len(changed) == 0 {
		s.mu.Lock()
		watchStatus := s.watchStatusPayloadLocked()
		watchDegraded := s.nativeStatus.degraded
		watchReason := s.nativeStatus.reason
		s.mu.Unlock()
		return map[string]any{"ok": true, "token": token, "watch_token": token, "mode": "delta", "directories": []any{}, "files": []any{}, "removed_paths": []string{}, "removed_roots": []string{}, "refresh": false, "change_summary": map[string]any{"paths_changed": 0, "roots_changed": 0}, "watch_status": watchStatus, "watch_degraded": watchDegraded, "watch_degradation_reason": watchReason}, nil
	}
	directories := make([]any, 0)
	directoryPaths := map[string]bool{}
	if full {
		for _, root := range watchRoots {
			directoryPaths[root] = true
		}
	} else {
		for _, path := range changed {
			if seenRoots[path] {
				directoryPaths[path] = true
				continue
			}
			directoryPaths[filepath.Dir(path)] = true
		}
	}
	for path := range directoryPaths {
		select {
		case <-ctx.Done():
			return nil, cancelled(path)
		default:
		}
		payload, finderErr := s.List(ctx, path)
		if finderErr != nil {
			if finderErr.Status == http.StatusNotFound {
				continue
			}
			return nil, finderErr
		}
		directories = append(directories, map[string]any{"path": path, "data": payload, "ok": true, "status": http.StatusOK})
	}
	sort.Slice(directories, func(i, j int) bool {
		return fmt.Sprint(directories[i].(map[string]any)["path"]) < fmt.Sprint(directories[j].(map[string]any)["path"])
	})
	files := make([]any, 0)
	filePaths := watchFiles
	if !full {
		filePaths = filePaths[:0]
		for _, path := range watchFiles {
			for _, changedPath := range changed {
				if path == changedPath {
					filePaths = append(filePaths, path)
					break
				}
			}
		}
	}
	for _, path := range filePaths {
		signature, signatureErr := s.watchSignature(ctx, path)
		if signatureErr != nil {
			return nil, signatureErr
		}
		files = append(files, map[string]any{"path": path, "signature": signature})
	}
	removed := make([]string, 0)
	for _, path := range changed {
		if s.signature(ctx, path) == "missing" {
			removed = append(removed, path)
		}
	}
	s.mu.Lock()
	watchStatus := s.watchStatusPayloadLocked()
	watchDegraded := s.nativeStatus.degraded
	watchReason := s.nativeStatus.reason
	s.mu.Unlock()
	return map[string]any{"ok": true, "token": token, "watch_token": token, "mode": map[bool]string{true: "full", false: "delta"}[full], "directories": directories, "files": files, "removed_paths": removed, "removed_roots": removedRoots, "refresh": full, "change_summary": map[string]any{"paths_changed": len(changed), "roots_changed": len(directoryPaths), "full": full}, "watch_status": watchStatus, "watch_degraded": watchDegraded, "watch_degradation_reason": watchReason}, nil
}

func (s *Service) watchTokenLocked() string { return fmt.Sprintf("%s:%d", s.epoch, s.revision) }

func parseWatchToken(value string) (uint64, string, bool) {
	parts := strings.Split(value, ":")
	if len(parts) != 2 || parts[0] == "" {
		return 0, "", false
	}
	revision, err := strconv.ParseUint(parts[1], 10, 64)
	return revision, parts[0], err == nil
}

func (s *Service) watchSignature(ctx context.Context, raw string) ([]any, *Error) {
	file, requested, _, finderErr := s.openAny(raw)
	if finderErr != nil {
		if finderErr.Status == http.StatusNotFound {
			requested, _, authErr := s.authorize(raw, true)
			if authErr == nil {
				return []any{requested, "missing"}, nil
			}
			return nil, authErr
		}
		return nil, finderErr
	}
	defer file.Close()
	return s.descriptorWatchSignature(ctx, file, requested), nil
}

func (s *Service) WatchSignature(ctx context.Context, raw string) ([]any, *Error) {
	file, requested, _, finderErr := s.openAny(raw)
	if finderErr != nil {
		return nil, finderErr
	}
	defer file.Close()
	if err := ctx.Err(); err != nil {
		return nil, cancelled(requested)
	}
	return s.descriptorWatchSignature(ctx, file, requested), nil
}

func (s *Service) watchLoop() {
	defer close(s.done)
	ticker := time.NewTicker(watchPollInterval)
	defer ticker.Stop()
	var nativeEvents <-chan nativeWatchEvent
	if s.native != nil {
		nativeEvents = s.native.Events()
	}
	for {
		select {
		case <-s.stop:
			return
		case <-ticker.C:
			s.pruneExpiredWatches(time.Now())
			s.pollWatches()
		case event, ok := <-nativeEvents:
			if !ok {
				nativeEvents = nil
				continue
			}
			s.handleNativeEvent(event)
		}
	}
}

func (s *Service) handleNativeEvent(event nativeWatchEvent) {
	s.mu.Lock()
	roots := s.activeWatchRootsLocked()
	paths := make([]string, 0, 1)
	if event.overflow {
		if event.root != "" {
			paths = append(paths, event.root)
		} else {
			paths = append(paths, roots...)
		}
	} else if event.path != "" {
		for _, root := range roots {
			if event.root != "" && root != event.root {
				continue
			}
			if event.path == root || strings.HasPrefix(event.path, root+string(os.PathSeparator)) {
				paths = append(paths, event.path)
				break
			}
		}
	}
	s.mu.Unlock()
	if len(paths) == 0 {
		return
	}
	reason := "native-event"
	if event.overflow {
		reason = "native-overflow"
	}
	s.recordChange(paths, reason)
	// The native watcher admits newly created directories while consuming the event. Rebuilding
	// every registration for ordinary file changes makes a high-churn tree spend its event reader
	// doing repeated full walks; overflow is the explicit signal that requires reconciliation.
	if event.overflow {
		s.reconfigureNative(context.Background())
	}
}

func (s *Service) pollWatches() {
	s.mu.Lock()
	snapshots := make(map[string]watchRecord, len(s.watches))
	for clientID, record := range s.watches {
		snapshots[clientID] = record
	}
	s.mu.Unlock()
	events := make([]map[string]any, 0)
	for clientID, record := range snapshots {
		changedPaths := []string{}
		watchedPaths := append(append([]string{}, record.Roots...), record.Files...)
		watchedPaths = append(watchedPaths, record.BackgroundFiles...)
		for _, path := range watchedPaths {
			signature := s.signature(context.Background(), path)
			if previous := record.Signatures[path]; previous != signature {
				record.Signatures[path] = signature
				changedPaths = append(changedPaths, path)
			}
		}
		if len(changedPaths) == 0 {
			continue
		}
		s.mu.Lock()
		current, exists := s.watches[clientID]
		if !exists || !current.UpdatedAt.Equal(record.UpdatedAt) {
			s.mu.Unlock()
			continue
		}
		current.Signatures = record.Signatures
		s.watches[clientID] = current
		s.revision++
		s.changes = append(s.changes, watchChange{revision: s.revision, paths: append([]string(nil), changedPaths...)})
		if len(s.changes) > watchHistoryLimit {
			s.changes = s.changes[len(s.changes)-watchHistoryLimit:]
		}
		events = append(events, map[string]any{"paths": changedPaths, "roots": current.Roots, "client_id": clientID, "reason": "filesystem-change", "token": s.watchTokenLocked(), "revision": s.revision})
		s.mu.Unlock()
	}
	if s.publish == nil {
		return
	}
	for _, payload := range events {
		if err := s.publish("fs_changed", payload); err == nil {
			_ = s.publish("files_changed", payload)
		}
	}
}

func (s *Service) activeWatchRootsLocked() []string {
	seen := map[string]bool{}
	roots := make([]string, 0)
	for _, record := range s.watches {
		for _, root := range record.Roots {
			if !seen[root] {
				seen[root] = true
				roots = append(roots, root)
			}
		}
	}
	sort.Strings(roots)
	return roots
}

func (s *Service) pruneExpiredWatches(now time.Time) {
	s.mu.Lock()
	removed := false
	for clientID, record := range s.watches {
		if !record.ExpiresAt.IsZero() && !record.ExpiresAt.After(now) {
			delete(s.watches, clientID)
			removed = true
		}
	}
	s.mu.Unlock()
	if removed {
		s.reconfigureNative(context.Background())
	}
}

func (s *Service) reconfigureNative(ctx context.Context) error {
	if s.native == nil {
		return nil
	}
	s.nativeMu.Lock()
	defer s.nativeMu.Unlock()
	s.mu.Lock()
	roots := s.activeWatchRootsLocked()
	s.mu.Unlock()
	status := s.native.SetRoots(ctx, roots)
	s.mu.Lock()
	s.nativeStatus = status
	s.mu.Unlock()
	if ctx != nil && ctx.Err() != nil {
		return ctx.Err()
	}
	return nil
}

func (s *Service) watchModeLocked() string {
	if s.nativeStatus.available && !s.nativeStatus.degraded {
		return "native"
	}
	if s.nativeStatus.available {
		return "native-degraded"
	}
	return "polling-fallback"
}

func (s *Service) watchStatusPayloadLocked() map[string]any {
	return map[string]any{
		"available":      s.nativeStatus.available,
		"degraded":       s.nativeStatus.degraded,
		"reason":         s.nativeStatus.reason,
		"registrations":  s.nativeStatus.registrations,
		"lease_seconds":  int(watchLeaseDuration / time.Second),
		"active_clients": len(s.watches),
		"active_roots":   len(s.activeWatchRootsLocked()),
	}
}

func (s *Service) recordChange(paths []string, reason string) {
	unique := make([]string, 0, len(paths))
	seen := map[string]bool{}
	for _, path := range paths {
		if path != "" && !seen[path] {
			seen[path] = true
			unique = append(unique, path)
		}
	}
	if len(unique) == 0 {
		return
	}
	s.mu.Lock()
	now := time.Now()
	if !s.lastChangeAt.IsZero() && now.Sub(s.lastChangeAt) <= watchEventCoalesceWindow && sameStringSet(unique, s.lastChangePaths) {
		s.mu.Unlock()
		return
	}
	s.revision++
	s.changes = append(s.changes, watchChange{revision: s.revision, paths: unique})
	if len(s.changes) > watchHistoryLimit {
		s.changes = s.changes[len(s.changes)-watchHistoryLimit:]
	}
	s.lastChangeAt = now
	s.lastChangePaths = append(s.lastChangePaths[:0], unique...)
	payload := map[string]any{"paths": unique, "reason": reason, "token": s.watchTokenLocked(), "revision": s.revision}
	s.mu.Unlock()
	if s.publish != nil {
		_ = s.publish("fs_changed", payload)
		_ = s.publish("files_changed", payload)
	}
}

func sameStringSet(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	seen := make(map[string]struct{}, len(left))
	for _, value := range left {
		seen[value] = struct{}{}
	}
	for _, value := range right {
		if _, ok := seen[value]; !ok {
			return false
		}
	}
	return true
}

func (s *Service) signature(ctx context.Context, path string) string {
	file, _, _, finderErr := s.openAny(path)
	if finderErr != nil {
		if finderErr.Status == http.StatusNotFound {
			return "missing"
		}
		return "error:" + finderErr.Code
	}
	defer file.Close()
	return s.descriptorSignature(ctx, file, path)
}

func (s *Service) descriptorWatchSignature(ctx context.Context, file *os.File, path string) []any {
	if ctx != nil && ctx.Err() != nil {
		return []any{path, "cancelled"}
	}
	info, err := file.Stat()
	if err != nil {
		return []any{path, "missing"}
	}
	kind := "other"
	if info.IsDir() {
		kind = "dir"
	} else if info.Mode().IsRegular() {
		kind = "file"
	}
	if !info.IsDir() {
		return []any{path, kind, info.ModTime().UnixNano(), info.Size()}
	}
	entries, err := file.ReadDir(maxWatchSignatureEntries + 1)
	if err != nil && !errors.Is(err, io.EOF) {
		return []any{path, kind, info.ModTime().UnixNano(), info.Size()}
	}
	directoryLimited := len(entries) > maxWatchSignatureEntries
	if directoryLimited {
		entries = entries[:maxWatchSignatureEntries]
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Name() < entries[j].Name() })
	children := make([]any, 0, minFinderInt(len(entries), 512))
	for _, entry := range entries {
		if len(children) >= 512 {
			break
		}
		if ctx != nil {
			select {
			case <-ctx.Done():
				return []any{path, "cancelled"}
			default:
			}
		}
		row, include := s.entryInfoAt(file, path, path, entry.Name(), false)
		if !include {
			continue
		}
		childKind, _ := row["kind"].(string)
		childMtime, _ := row["mtime_ns"].(int64)
		childSize, _ := row["size"].(int64)
		children = append(children, []any{entry.Name(), childKind, childMtime, childSize})
	}
	if directoryLimited {
		children = append(children, []any{"!truncated", "", int64(0), int64(0)})
	}
	return []any{path, kind, info.ModTime().UnixNano(), info.Size(), children}
}

func (s *Service) descriptorSignature(ctx context.Context, file *os.File, path string) string {
	info, err := file.Stat()
	if err != nil {
		return "missing"
	}
	hash := sha256.New()
	_, _ = fmt.Fprintf(hash, "%d:%d:%d:%s", info.Mode(), info.Size(), info.ModTime().UnixNano(), fileID(info))
	if !info.IsDir() {
		return hex.EncodeToString(hash.Sum(nil))
	}
	entries := 0
	walkErr := s.walkDescriptors(ctx, file, path, path, func(entry walkedEntry) error {
		if entries >= maxWatchSignatureEntries {
			_, _ = io.WriteString(hash, "!truncated")
			return errWalkStop
		}
		_, _ = fmt.Fprintf(hash, "%q:%d:%d:%d:%s", entry.Relative, entry.Info.Mode(), entry.Info.Size(), entry.Info.ModTime().UnixNano(), fileID(entry.Info))
		entries++
		return nil
	})
	if walkErr != nil && !errors.Is(walkErr, errWalkStop) && !errors.Is(walkErr, errWalkDirectoryLimit) {
		_, _ = fmt.Fprintf(hash, "!walk-error:%v", walkErr)
	}
	if errors.Is(walkErr, errWalkDirectoryLimit) {
		_, _ = io.WriteString(hash, "!directory-truncated")
	}
	return hex.EncodeToString(hash.Sum(nil))
}

func (s *Service) addGitMetadata(ctx context.Context, result map[string]any, requested, resolved string, file *os.File) {
	result["git_enrichment"] = map[string]any{"available": false, "reason": "not_git_repo"}
	info, err := file.Stat()
	if err != nil {
		return
	}
	gitDirectory := file
	var parent *os.File
	if !info.IsDir() {
		parentResolved := filepath.Dir(resolved)
		identities, identityErr := captureResolvedIdentities(parentResolved)
		if identityErr != nil {
			return
		}
		parent, err = openResolvedDescriptor(parentResolved, true, identities)
		if err != nil {
			return
		}
		defer parent.Close()
		gitDirectory = parent
	}
	gitCtx, cancel := context.WithTimeout(ctx, 400*time.Millisecond)
	defer cancel()
	rootBytes, _, err := runGitBounded(gitCtx, gitDirectory, nil, []string{"rev-parse", "--show-toplevel"}, 4096)
	if err != nil {
		return
	}
	root := filepath.Clean(strings.TrimSpace(string(rootBytes)))
	root, err = filepath.Abs(root)
	if err != nil {
		return
	}
	workingDirectory := filepath.Dir(resolved)
	if info.IsDir() {
		workingDirectory = resolved
	}
	namespace, namespaceErr := s.openGitNamespace(gitDirectory, workingDirectory, requested)
	if namespaceErr != nil {
		return
	}
	defer namespace.close()
	relative, err := filepath.Rel(root, resolved)
	if err != nil || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
		return
	}
	rootDirectory, rootErr := openGitRootDescriptor(root, requested)
	if rootErr != nil {
		return
	}
	defer rootDirectory.Close()
	gitDirectory = rootDirectory
	run := func(args ...string) ([]byte, error) {
		output, _, commandErr := runGitBounded(gitCtx, gitDirectory, nil, args, 64*1024, namespace)
		return output, commandErr
	}
	rootPath := relative == "."
	if rootPath {
		relative = ""
	}
	repo := map[string]any{"root": root, "name": filepath.Base(root), "branch": "", "detached": false, "head_sha": "", "upstream": "", "dirty_count": 0, "ahead": 0, "behind": 0}
	if branch, branchErr := run("symbolic-ref", "--short", "HEAD"); branchErr == nil {
		repo["branch"] = strings.TrimSpace(string(branch))
	} else {
		repo["detached"] = true
	}
	if head, headErr := run("rev-parse", "HEAD"); headErr == nil {
		repo["head_sha"] = strings.TrimSpace(string(head))
	}
	if status, statusErr := run("status", "--porcelain", "--untracked-files=no"); statusErr == nil {
		repo["dirty_count"] = len(nonEmptyLines(string(status)))
	}
	history := []any{}
	tracked := false
	if !rootPath {
		trackedArgs := []string{"ls-files", "--error-unmatch"}
		trackedArgs = append(trackedArgs, historyPathArgs(filepath.ToSlash(relative))...)
		trackedOutput, trackedErr := run(trackedArgs...)
		tracked = trackedErr == nil && strings.TrimSpace(string(trackedOutput)) != ""
		if tracked {
			logArgs := []string{"log", "-n", "60", "--format=%H%x1f%h%x1f%s%x1e"}
			logArgs = append(logArgs, historyPathArgs(filepath.ToSlash(relative))...)
			if logOutput, logErr := run(logArgs...); logErr == nil {
				for _, record := range strings.Split(string(logOutput), "\x1e") {
					fields := strings.Split(strings.TrimSpace(record), "\x1f")
					if len(fields) < 3 || fields[0] == "" {
						continue
					}
					history = append(history, map[string]any{"ref": fields[0], "sha": fields[0], "short": fields[1], "subject": fields[2]})
				}
			}
		}
	}
	result["git_root"] = root
	result["repo_root"] = root
	result["relative_path"] = filepath.ToSlash(relative)
	result["git_tracked"] = tracked
	result["git_history"] = history
	result["git_has_history"] = len(history) > 0
	result["repo"] = repo
	result["git_enrichment"] = map[string]any{"available": true, "reason": ""}
	_ = requested
}

func (s *Service) pathStillStable(file *os.File, before os.FileInfo) bool {
	if file == nil || before == nil {
		return false
	}
	after, err := file.Stat()
	if err != nil {
		return false
	}
	left, right := identityFor(before), identityFor(after)
	return sameDescriptorIdentity(left, right) && left.size == right.size && left.mtimeNS == right.mtimeNS
}

func nonEmptyLines(value string) []string {
	lines := strings.Split(strings.TrimSpace(value), "\n")
	result := make([]string, 0, len(lines))
	for _, line := range lines {
		if strings.TrimSpace(line) != "" {
			result = append(result, line)
		}
	}
	return result
}

func filesystemError(path string, err error) *Error {
	if errors.Is(err, os.ErrPermission) {
		return forbidden(path, "fs.error.permissionDenied", "permission denied")
	}
	if errors.Is(err, os.ErrNotExist) {
		return notFound(path)
	}
	return &Error{Status: http.StatusInternalServerError, Code: "filesystem_error", MessageKey: "fs.error.operationFailed", Path: path, Retryable: true, Message: err.Error()}
}

func cancelled(path string) *Error {
	return &Error{Status: http.StatusRequestTimeout, Code: "cancelled", MessageKey: "request.error.cancelled", Path: path, Retryable: true, Message: "filesystem operation cancelled"}
}

func stringList(value any, limit int) ([]string, error) {
	if value == nil {
		return []string{}, nil
	}
	values, ok := value.([]any)
	if !ok {
		return nil, errors.New("value must be a list")
	}
	if len(values) > limit {
		return nil, fmt.Errorf("list contains more than %d items", limit)
	}
	result := make([]string, 0, len(values))
	seen := map[string]bool{}
	for _, value := range values {
		text, ok := value.(string)
		if !ok || text == "" || seen[text] {
			if !ok || text == "" {
				return nil, errors.New("list contains an invalid path")
			}
			continue
		}
		seen[text] = true
		result = append(result, text)
	}
	return result, nil
}

func fileID(info os.FileInfo) string {
	if stat, ok := info.Sys().(*syscall.Stat_t); ok {
		return fmt.Sprintf("%d:%d", stat.Dev, stat.Ino)
	}
	return fmt.Sprintf("0:%d", info.ModTime().UnixNano())
}

func bytesLookBinary(data []byte) bool {
	if len(data) > 8192 {
		data = data[:8192]
	}
	for _, value := range data {
		if value == 0 {
			return true
		}
	}
	return false
}

func sniffContentType(data []byte, path string) string {
	if len(data) >= 8 && string(data[:8]) == "\x89PNG\r\n\x1a\n" {
		return "image/png"
	}
	if len(data) >= 3 && string(data[:3]) == "\xff\xd8\xff" {
		return "image/jpeg"
	}
	if len(data) >= 6 && (string(data[:6]) == "GIF87a" || string(data[:6]) == "GIF89a") {
		return "image/gif"
	}
	if len(data) >= 5 && string(data[:5]) == "%PDF-" {
		return "application/pdf"
	}
	if len(data) >= 12 && string(data[:4]) == "RIFF" && string(data[8:12]) == "WEBP" {
		return "image/webp"
	}
	if len(data) >= 4 && string(data[:4]) == "PK\x03\x04" {
		return "application/zip"
	}
	return previewMime(path)
}

func isTextPath(path string) bool {
	name := strings.ToLower(filepath.Base(path))
	ext := strings.ToLower(filepath.Ext(path))
	if ext == "" {
		return name == "makefile" || name == "dockerfile" || name == "license" || name == "readme"
	}
	for _, value := range []string{".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".go", ".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".html", ".htm", ".sh", ".bash", ".rs", ".c", ".h", ".cpp", ".java", ".sql"} {
		if ext == value {
			return true
		}
	}
	return false
}

func previewMime(path string) string {
	ext := strings.ToLower(filepath.Ext(path))
	for _, value := range []string{".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".pdf", ".mp3", ".wav", ".mp4", ".webm", ".mov"} {
		if ext == value {
			if value == ".jpg" || value == ".jpeg" {
				return "image/jpeg"
			}
			if value == ".svg" {
				return "image/svg+xml"
			}
			return mime.TypeByExtension(ext)
		}
	}
	return ""
}

func isRepository(path string) bool {
	info, err := os.Stat(filepath.Join(path, ".git"))
	return err == nil && (info.IsDir() || info.Mode().IsRegular())
}
