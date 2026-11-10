// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"fmt"
	"io"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"syscall"
	"time"
)

func permissionString(mode os.FileMode) string {
	text := []byte("-rwxrwxrwx")
	switch {
	case mode.IsDir():
		text[0] = 'd'
	case mode&os.ModeSymlink != 0:
		text[0] = 'l'
	case mode&os.ModeNamedPipe != 0:
		text[0] = 'p'
	case mode&os.ModeSocket != 0:
		text[0] = 's'
	case mode&os.ModeCharDevice != 0:
		text[0] = 'c'
	case mode&os.ModeDevice != 0:
		text[0] = 'b'
	}
	for i := 0; i < 9; i++ {
		if mode.Perm()&(1<<(8-i)) == 0 {
			text[i+1] = '-'
		}
	}
	for _, bit := range []struct {
		flag         os.FileMode
		pos          int
		lower, upper byte
	}{{os.ModeSetuid, 3, 's', 'S'}, {os.ModeSetgid, 6, 's', 'S'}, {os.ModeSticky, 9, 't', 'T'}} {
		if mode&bit.flag != 0 {
			if text[bit.pos] == 'x' {
				text[bit.pos] = bit.lower
			} else {
				text[bit.pos] = bit.upper
			}
		}
	}
	return string(text)
}

func numericMode(mode os.FileMode) uint32 {
	n := uint32(mode.Perm())
	if mode&os.ModeSetuid != 0 {
		n |= 0o4000
	}
	if mode&os.ModeSetgid != 0 {
		n |= 0o2000
	}
	if mode&os.ModeSticky != 0 {
		n |= 0o1000
	}
	return n
}

// List and Get Info display the entry's own mode, including symlinks and Unix special bits.
func addEntryMetadata(row map[string]any, info os.FileInfo) {
	row["entry_size"] = info.Size()
	row["permissions"] = permissionString(info.Mode())
	row["mode"] = numericMode(info.Mode())
	row["octal_mode"] = fmt.Sprintf("%04o", numericMode(info.Mode()))
	row["metadata_identity"] = fileID(info)
	row["mtime"] = info.ModTime().Unix()
	row["mtime_ns"] = info.ModTime().UnixNano()
	row["created"] = nil
	row["accessed"] = nil
	row["allocated_size"] = nil
	row["owner_id"], row["group_id"] = nil, nil
	row["owner"], row["group"] = nil, nil
	row["permission_edit_reason"] = "OS ownership information is unavailable"
	if stat, ok := info.Sys().(*syscall.Stat_t); ok {
		row["owner_id"], row["group_id"] = stat.Uid, stat.Gid
		row["allocated_size"] = stat.Blocks * 512
		row["accessed"], row["created"] = platformMetadataTimes(stat)
		reason := ""
		if os.Geteuid() != 0 && uint32(os.Geteuid()) != stat.Uid {
			reason = "The account running YOmux does not own this item"
		}
		if restriction := platformMetadataRestriction(stat); restriction != "" {
			reason = restriction
		}
		row["permission_edit_reason"] = reason
	}
	if info.Mode()&os.ModeSymlink != 0 {
		row["permission_edit_reason"] = "Permissions cannot be edited on symlinks"
	}
	if !info.IsDir() && !info.Mode().IsRegular() && info.Mode()&os.ModeSymlink == 0 {
		row["permission_edit_reason"] = "Permissions cannot be edited on this filesystem item type"
	}
}

// Atomic replacement uses the parent's permissions; also enforce access to the existing file.
func contentWriteAccessAt(directory *os.File, name, path string, expected os.FileInfo) *Error {
	if !expected.Mode().IsRegular() || expected.Mode().Perm()&0o222 == 0 {
		return forbidden(path, "fs.error.permissionDenied", "File is read-only")
	}
	file, err := openAt(directory, name, syscall.O_WRONLY|syscall.O_NONBLOCK|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return filesystemError(path, err)
	}
	defer file.Close()
	current, err := file.Stat()
	if err != nil {
		return filesystemError(path, err)
	}
	if !sameDescriptorIdentity(identityFor(expected), identityFor(current)) {
		return changed(path)
	}
	if current.Mode().Perm()&0o222 == 0 {
		return forbidden(path, "fs.error.permissionDenied", "File is read-only")
	}
	return nil
}

func (s *Service) addContentWriteMetadata(row map[string]any, path string, expected os.FileInfo) {
	row["writable"], row["write_reason"] = false, "File is read-only"
	if !expected.Mode().IsRegular() {
		return
	}
	parent, failure := s.openMutationParent(path)
	if failure == nil {
		defer parent.Close()
		var current os.FileInfo
		current, failure = s.requireMutationTarget(parent, false)
		if failure == nil && !sameDescriptorIdentity(identityFor(expected), identityFor(current)) {
			failure = changed(path)
		}
		if failure == nil {
			failure = contentWriteAccessAt(parent.directory, parent.name, path, current)
		}
	}
	if failure != nil {
		row["write_reason"] = failure.Message
		return
	}
	row["writable"], row["write_reason"] = true, ""
}

func (s *Service) entryMetadata(raw string) (map[string]any, *Error) {
	requested, resolved, authErr := s.authorize(raw, true)
	if authErr != nil {
		return nil, authErr
	}
	parent, _, _, openErr := s.open(filepath.Dir(requested), true)
	if openErr != nil {
		return nil, openErr
	}
	defer parent.Close()
	info, err := lstatAt(parent, filepath.Base(requested))
	if err != nil {
		return nil, filesystemError(requested, err)
	}
	kind := "other"
	if info.IsDir() {
		kind = "dir"
	} else if info.Mode().IsRegular() {
		kind = "file"
	} else if info.Mode()&os.ModeSymlink != 0 {
		kind = "symlink"
	}
	row := map[string]any{"path": requested, "name": filepath.Base(requested), "realpath": resolved, "kind": kind, "size": info.Size(), "is_symlink": info.Mode()&os.ModeSymlink != 0}
	addEntryMetadata(row, info)
	if birth := platformBirthAt(parent, filepath.Base(requested)); birth != nil {
		row["created"] = *birth
	}
	if row["is_symlink"] == true {
		target, err := readlinkAt(parent, filepath.Base(requested))
		if err != nil {
			return nil, filesystemError(requested, err)
		}
		row["symlink_target"] = target
	}
	if stat, ok := info.Sys().(*syscall.Stat_t); ok {
		if owner, err := user.LookupId(strconv.FormatUint(uint64(stat.Uid), 10)); err == nil {
			row["owner"] = owner.Username
		}
		if group, err := user.LookupGroupId(strconv.FormatUint(uint64(stat.Gid), 10)); err == nil {
			row["group"] = group.Name
		}
	}
	if file, err := openAt(parent, filepath.Base(requested), platformMetadataOpenFlags(), 0); err == nil {
		if restriction, err := platformFilesystemRestriction(file); err != nil {
			row["permission_edit_reason"] = "Filesystem restrictions could not be checked: " + err.Error()
		} else if restriction != "" {
			row["permission_edit_reason"] = restriction
		}
		file.Close()
	}
	s.addContentWriteMetadata(row, requested, info)
	return row, nil
}

type ChmodRequest struct {
	Authorize    func() error `json:"-"`
	Path         string       `json:"path"`
	Identity     string       `json:"identity"`
	OriginalMode *uint32      `json:"original_mode"`
	Mode         *uint32      `json:"mode"`
	Recursive    bool         `json:"recursive"`
	Confirmed    bool         `json:"confirmed"`
}

// Descriptor-relative post-order walking keeps parent access until all descendants have run.
// ReadDir batches bound enumeration memory; symlinks are observed but never opened or followed.
func walkMetadata(ctx context.Context, file *os.File, path string, visit func(*os.File, string, os.FileInfo, error) error) error {
	return walkMetadataDepth(ctx, file, path, visit, 0)
}
func walkMetadataDepth(ctx context.Context, file *os.File, path string, visit func(*os.File, string, os.FileInfo, error) error, depth int) error {
	if depth >= 128 {
		return visit(nil, path, nil, fmt.Errorf("directory depth limit reached"))
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	info, err := file.Stat()
	if err != nil {
		return visit(nil, path, nil, err)
	}
	if info.IsDir() {
		for {
			if err := ctx.Err(); err != nil {
				return err
			}
			entries, readErr := file.ReadDir(128)
			for _, entry := range entries {
				if err := ctx.Err(); err != nil {
					return err
				}
				childPath := filepath.Join(path, entry.Name())
				before, err := lstatAt(file, entry.Name())
				if err != nil {
					if err := visit(nil, childPath, nil, err); err != nil {
						return err
					}
					continue
				}
				if before.Mode()&os.ModeSymlink != 0 {
					if err := visit(nil, childPath, before, nil); err != nil {
						return err
					}
					continue
				}
				flags := platformMetadataOpenFlags()
				if before.IsDir() {
					flags = syscall.O_RDONLY | syscall.O_DIRECTORY | syscall.O_NOFOLLOW | syscall.O_CLOEXEC
				}
				child, err := openAt(file, entry.Name(), flags, 0)
				if err != nil {
					if err := visit(nil, childPath, before, err); err != nil {
						return err
					}
					continue
				}
				after, err := child.Stat()
				if err == nil && !sameDescriptorIdentity(identityFor(before), identityFor(after)) {
					err = errChangedOnDisk
				}
				if err == nil {
					err = walkMetadataDepth(ctx, child, childPath, visit, depth+1)
				} else {
					err = visit(nil, childPath, before, err)
				}
				child.Close()
				if err != nil {
					return err
				}
			}
			if readErr == io.EOF {
				break
			}
			if readErr != nil {
				if err := visit(nil, path, nil, readErr); err != nil {
					return err
				}
				break
			}
		}
	}
	return visit(file, path, info, nil)
}

func (s *Service) Chmod(ctx context.Context, request ChmodRequest, progress func(map[string]any) error) (map[string]any, *Error) {
	if request.Mode == nil || request.OriginalMode == nil || *request.Mode > 0o777 || *request.OriginalMode > 0o7777 || request.Identity == "" {
		return nil, invalid(request.Path, "identity, original_mode and rwx mode are required")
	}
	if request.Recursive && !request.Confirmed {
		return nil, invalid(request.Path, "recursive permission changes require explicit confirmation")
	}
	parent, finderErr := s.openMutationParent(request.Path)
	if finderErr != nil {
		return nil, finderErr
	}
	defer parent.Close()
	before, finderErr := s.requireMutationTarget(parent, false)
	if finderErr != nil {
		return nil, finderErr
	}
	if before.Mode()&os.ModeSymlink != 0 {
		return nil, forbidden(request.Path, "fs.error.permissionDenied", "Permissions cannot be edited on symlinks")
	}
	if fileID(before) != request.Identity || numericMode(before.Mode()) != *request.OriginalMode {
		return nil, changed(request.Path)
	}
	file, err := openAt(parent.directory, parent.name, platformMetadataOpenFlags(), 0)
	if err != nil {
		return nil, filesystemError(request.Path, err)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, filesystemError(request.Path, err)
	}
	if !sameDescriptorIdentity(identityFor(before), identityFor(info)) || numericMode(info.Mode()) != *request.OriginalMode {
		return nil, changed(request.Path)
	}
	counts := map[string]any{"changed": 0, "skipped": 0, "failed": 0, "errors": []map[string]any{}, "cancelled": false}
	var lastProgress time.Time
	visit := func(target *os.File, path string, observed os.FileInfo, failure error) error {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		key := "changed"
		if failure == nil && observed.Mode()&os.ModeSymlink != 0 {
			key = "skipped"
		} else if failure == nil {
			current, err := target.Stat()
			failure = err
			if failure == nil && (!sameDescriptorIdentity(identityFor(observed), identityFor(current)) || current.Mode() != observed.Mode()) {
				failure = errChangedOnDisk
			}
			if failure == nil {
				_, resolved, authErr := s.authorize(path, false)
				if authErr == nil {
					authErr = s.checkMutationTarget(path, resolved)
				}
				if authErr != nil {
					failure = authErr
				}
			}

			if failure == nil {
				metadata, metadataErr := s.entryMetadata(path)
				if metadataErr != nil {
					failure = metadataErr
				} else if metadata["metadata_identity"] != fileID(current) || metadata["mode"] != numericMode(current.Mode()) {
					failure = errChangedOnDisk
				}
			}
			if failure == nil {
				metadata := map[string]any{}
				addEntryMetadata(metadata, current)
				if reason := metadata["permission_edit_reason"].(string); reason != "" {
					key = "skipped"
					failure = fmt.Errorf("%s", reason)
				}
				if restriction, err := platformFilesystemRestriction(target); err != nil {
					failure = err
				} else if restriction != "" {
					key = "skipped"
					failure = fmt.Errorf("%s", restriction)
				}
				if failure == nil && request.Authorize != nil {
					failure = request.Authorize()
				}
				if failure == nil {
					failure = platformChmod(target, os.FileMode(*request.Mode)|current.Mode()&(os.ModeSetuid|os.ModeSetgid|os.ModeSticky))
				}
			}
		}
		if failure != nil {
			if key != "skipped" {
				key = "failed"
			}
			appendMetadataError(counts, path, failure)
		}
		counts[key] = counts[key].(int) + 1
		if key == "changed" {
			s.noteChange([]string{path}, "chmod")
		}
		if progress != nil && time.Since(lastProgress) >= 150*time.Millisecond {
			lastProgress = time.Now()
			return progress(counts)
		}
		return nil
	}
	if request.Recursive && info.IsDir() {
		file.Close()
		file, err = openAt(parent.directory, parent.name, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
		if err != nil {
			return nil, filesystemError(request.Path, err)
		}
		defer file.Close()
		current, statErr := file.Stat()
		if statErr != nil {
			return nil, filesystemError(request.Path, statErr)
		}
		if fileID(current) != request.Identity || numericMode(current.Mode()) != *request.OriginalMode {
			return nil, changed(request.Path)
		}
		err = walkMetadata(ctx, file, request.Path, visit)
	} else {
		err = visit(file, request.Path, info, nil)
	}
	if err != nil {
		counts["cancelled"] = ctx.Err() != nil
		counts["partial"] = true
		counts["operation_error"] = err.Error()
	}
	metadata, metadataErr := s.entryMetadata(request.Path)
	if metadataErr != nil {
		counts["metadata_error"] = metadataErr.Message
	} else {
		counts["metadata"] = metadata
	}
	return counts, nil
}

func (s *Service) DirectorySize(ctx context.Context, raw string, progress func(map[string]any) error) (map[string]any, *Error) {
	metadata, metadataErr := s.entryMetadata(raw)
	if metadataErr != nil {
		return nil, metadataErr
	}
	if metadata["is_symlink"] == true {
		return nil, invalid(raw, "Folder contents calculation does not follow symlinks")
	}
	file, requested, _, finderErr := s.open(raw, true)
	if finderErr != nil {
		return nil, finderErr
	}
	defer file.Close()
	totals := map[string]any{"bytes": int64(0), "allocated_bytes": int64(0), "files": 0, "folders": 0, "symlinks": 0, "partial": false, "complete": false, "errors": []map[string]any{}}
	seen := make(map[string]bool)
	var lastProgress time.Time
	err := walkMetadata(ctx, file, requested, func(_ *os.File, path string, info os.FileInfo, failure error) error {
		if failure != nil {
			totals["partial"] = true
			appendMetadataError(totals, path, failure)
			return nil
		}
		if path != requested {
			if info.IsDir() {
				totals["folders"] = totals["folders"].(int) + 1
			} else {
				totals["files"] = totals["files"].(int) + 1
				totals["bytes"] = totals["bytes"].(int64) + info.Size()
			}
			if info.Mode()&os.ModeSymlink != 0 {
				totals["symlinks"] = totals["symlinks"].(int) + 1
			}
			if stat, ok := info.Sys().(*syscall.Stat_t); ok {
				id := fileID(info)
				if !seen[id] {
					totals["allocated_bytes"] = totals["allocated_bytes"].(int64) + stat.Blocks*512
					if stat.Nlink > 1 && !info.IsDir() {
						if len(seen) >= 65536 {
							totals["allocated_partial"] = true
							totals["partial"] = true
							appendMetadataError(totals, path, fmt.Errorf("hard-link accounting limit reached"))
						} else {
							seen[id] = true
						}
					}
				}
			} else {
				totals["allocated_bytes"] = nil
				totals["partial"] = true
			}
		}
		if progress != nil && time.Since(lastProgress) >= 150*time.Millisecond {
			lastProgress = time.Now()
			return progress(totals)
		}
		return nil
	})
	if err != nil {
		totals["partial"] = true
		totals["cancelled"] = ctx.Err() != nil
		totals["operation_error"] = err.Error()
	} else {
		totals["complete"] = totals["partial"] == false
	}
	return totals, nil
}

func appendMetadataError(result map[string]any, path string, err error) {
	errors := result["errors"].([]map[string]any)
	if len(errors) < 128 {
		result["errors"] = append(errors, map[string]any{"path": path, "error": err.Error()})
	} else {
		result["errors_truncated"] = true
	}
}
