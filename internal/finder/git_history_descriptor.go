// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bytes"
	"errors"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"

	"github.com/go-git/go-billy/v5"
)

// descriptorGitFS exposes a virtual .git directory. It never resolves a repository
// pathname: each component is opened relative to an inherited authorization handle.
// Chroot is a logical view so it cannot leak unowned directory handles.
type descriptorGitFS struct {
	git, common, objects *os.File
	prefix               string
	config               []byte
	afterOpen            func(string, string)
}

var _ billy.Filesystem = (*descriptorGitFS)(nil)

func descriptorGitName(name string) (string, error) {
	if filepath.IsAbs(name) || strings.ContainsRune(name, 0) {
		return "", billy.ErrCrossedBoundary
	}
	for _, part := range strings.Split(name, "/") {
		if part == ".." {
			return "", billy.ErrCrossedBoundary
		}
	}
	return filepath.Clean(name), nil
}

func isWorktreeGitRef(name string) bool {
	for _, prefix := range []string{"refs/worktree", "refs/bisect", "refs/rewritten"} {
		if name == prefix || strings.HasPrefix(name, prefix+"/") {
			return true
		}
	}
	return false
}

func (fs *descriptorGitFS) route(name string) (*os.File, string, error) {
	name, err := descriptorGitName(name)
	if err != nil {
		return nil, "", err
	}
	name = filepath.Join(fs.prefix, name)
	if name == "objects" || strings.HasPrefix(name, "objects/") {
		return fs.objects, strings.TrimPrefix(strings.TrimPrefix(name, "objects"), "/"), nil
	}
	if name == "HEAD" || name == "config.worktree" || isWorktreeGitRef(name) {
		return fs.git, name, nil
	}
	return fs.common, name, nil
}

func (fs *descriptorGitFS) open(name string) (*os.File, error) {
	root, relative, err := fs.route(name)
	if err != nil {
		return nil, err
	}
	current, err := openAt(root, ".", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, err
	}
	parts := strings.Split(relative, "/")
	for i, part := range parts {
		if part == "" || part == "." {
			continue
		}
		flags := syscall.O_RDONLY | syscall.O_NOFOLLOW | syscall.O_NONBLOCK
		if i < len(parts)-1 {
			flags |= syscall.O_DIRECTORY
		}
		next, openErr := openAt(current, part, flags, 0)
		_ = current.Close()
		if openErr != nil {
			return nil, openErr
		}
		current = next
		info, statErr := current.Stat()
		if statErr != nil || (!info.IsDir() && !info.Mode().IsRegular()) {
			_ = current.Close()
			return nil, errors.New("unsupported Git metadata entry")
		}
		if fs.afterOpen != nil {
			fs.afterOpen(name, strings.Join(parts[:i+1], "/"))
		}
	}
	return current, nil
}

type descriptorGitFile struct{ file *os.File }

func (f *descriptorGitFile) Name() string                            { return f.file.Name() }
func (f *descriptorGitFile) Read(b []byte) (int, error)              { return f.file.Read(b) }
func (f *descriptorGitFile) ReadAt(b []byte, off int64) (int, error) { return f.file.ReadAt(b, off) }
func (f *descriptorGitFile) Seek(off int64, whence int) (int64, error) {
	return f.file.Seek(off, whence)
}
func (f *descriptorGitFile) Close() error { return f.file.Close() }

func (*descriptorGitFile) Write([]byte) (int, error) { return 0, billy.ErrReadOnly }
func (*descriptorGitFile) Truncate(int64) error      { return billy.ErrReadOnly }
func (*descriptorGitFile) Lock() error               { return billy.ErrReadOnly }
func (*descriptorGitFile) Unlock() error             { return billy.ErrReadOnly }

type descriptorGitConfig struct{ *bytes.Reader }

func (*descriptorGitConfig) Name() string              { return "config" }
func (*descriptorGitConfig) Close() error              { return nil }
func (*descriptorGitConfig) Write([]byte) (int, error) { return 0, billy.ErrReadOnly }
func (*descriptorGitConfig) Truncate(int64) error      { return billy.ErrReadOnly }
func (*descriptorGitConfig) Lock() error               { return billy.ErrReadOnly }
func (*descriptorGitConfig) Unlock() error             { return billy.ErrReadOnly }

func (fs *descriptorGitFS) Open(name string) (billy.File, error) {
	clean, err := descriptorGitName(name)
	if err != nil {
		return nil, err
	}
	if filepath.Join(fs.prefix, clean) == "config" && fs.config != nil {
		return &descriptorGitConfig{bytes.NewReader(fs.config)}, nil
	}
	f, err := fs.open(name)
	if err != nil {
		return nil, err
	}
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() {
		_ = f.Close()
		return nil, errors.New("Git entry is not a regular file")
	}
	return &descriptorGitFile{f}, nil
}
func (fs *descriptorGitFS) OpenFile(name string, flags int, _ os.FileMode) (billy.File, error) {
	if flags != os.O_RDONLY {
		return nil, billy.ErrReadOnly
	}
	return fs.Open(name)
}
func (fs *descriptorGitFS) Stat(name string) (os.FileInfo, error) {
	f, err := fs.open(name)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return f.Stat()
}
func (fs *descriptorGitFS) Lstat(name string) (os.FileInfo, error) { return fs.Stat(name) }
func (fs *descriptorGitFS) ReadDir(name string) ([]os.FileInfo, error) {
	f, err := fs.open(name)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	entries, err := f.Readdir(gitMetadataMaxEntries + 1)
	if err != nil && err != io.EOF {
		return nil, err
	}
	if len(entries) > gitMetadataMaxEntries {
		return nil, errGitOutputLimit
	}
	for _, entry := range entries {
		if entry.Mode()&os.ModeSymlink != 0 {
			return nil, errors.New("Git metadata symlinks are unsupported")
		}
	}
	if filepath.Join(fs.prefix, name) == "refs" {
		for _, special := range []string{"worktree", "bisect", "rewritten"} {
			filtered := entries[:0]
			for _, entry := range entries {
				if entry.Name() != special {
					filtered = append(filtered, entry)
				}
			}
			entries = filtered
			info, statErr := fs.Stat(filepath.Join(name, special))
			if errors.Is(statErr, os.ErrNotExist) {
				continue
			}
			if statErr != nil {
				return nil, statErr
			}
			entries = append(entries, info)
		}
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Name() < entries[j].Name() })
	return entries, nil
}
func (fs *descriptorGitFS) Chroot(name string) (billy.Filesystem, error) {
	name, err := descriptorGitName(name)
	if err != nil {
		return nil, err
	}
	copy := *fs
	copy.prefix = filepath.Join(fs.prefix, name)
	return &copy, nil
}
func (*descriptorGitFS) Root() string                { return "." }
func (*descriptorGitFS) Join(parts ...string) string { return filepath.Join(parts...) }
func (*descriptorGitFS) Capabilities() billy.Capability {
	return billy.ReadCapability | billy.SeekCapability
}
func (*descriptorGitFS) Create(string) (billy.File, error)           { return nil, billy.ErrReadOnly }
func (*descriptorGitFS) TempFile(string, string) (billy.File, error) { return nil, billy.ErrReadOnly }
func (*descriptorGitFS) Rename(string, string) error                 { return billy.ErrReadOnly }
func (*descriptorGitFS) Remove(string) error                         { return billy.ErrReadOnly }
func (*descriptorGitFS) MkdirAll(string, os.FileMode) error          { return billy.ErrReadOnly }
func (*descriptorGitFS) Symlink(string, string) error                { return billy.ErrReadOnly }
func (*descriptorGitFS) Readlink(string) (string, error)             { return "", billy.ErrNotSupported }

func descriptorGitRead(fs *descriptorGitFS, name string, limit int) ([]byte, error) {
	f, err := fs.Open(name)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, int64(limit+1)))
	if len(data) > limit {
		return nil, errGitOutputLimit
	}
	return data, err
}
