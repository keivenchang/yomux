// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build darwin

package finder

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/unix"
)

func platformOpenat(fd int, name string, flags int, mode uint32) (int, error) {
	return unix.Openat(fd, name, flags, mode)
}

func platformEnterGitWorktree(directory *os.File) (func() error, error) {
	runtime.LockOSThread()
	previousDirectory, err := os.Open(".")
	if err != nil {
		runtime.UnlockOSThread()
		return nil, err
	}
	if err := unix.PthreadFchdir(int(directory.Fd())); err != nil {
		_ = previousDirectory.Close()
		runtime.UnlockOSThread()
		return nil, err
	}
	return func() error {
		restoreErr := unix.PthreadFchdir(int(previousDirectory.Fd()))
		closeErr := previousDirectory.Close()
		runtime.UnlockOSThread()
		if restoreErr != nil {
			return restoreErr
		}
		return closeErr
	}, nil
}

func platformFstat(fd int) (syscall.Stat_t, error) {
	var stat syscall.Stat_t
	err := syscall.Fstat(fd, &stat)
	return stat, err
}
func statMtimeNS(stat *syscall.Stat_t) int64 { return stat.Mtimespec.Sec*1e9 + stat.Mtimespec.Nsec }
func statCTime(stat *syscall.Stat_t) int64   { return stat.Ctimespec.Sec*1e9 + stat.Ctimespec.Nsec }
func platformDescriptorPath(file *os.File) string {
	path, _ := darwinPathForDescriptor(file)
	return path
}
func fileInfoAt(dir *os.File, name string, flags int) (os.FileInfo, error) {
	var source unix.Stat_t
	if err := unix.Fstatat(int(dir.Fd()), name, &source, flags); err != nil {
		return nil, err
	}
	stat := syscall.Stat_t{
		Dev: source.Dev, Mode: source.Mode, Nlink: source.Nlink, Ino: source.Ino, Uid: source.Uid, Gid: source.Gid, Rdev: source.Rdev,
		Atimespec:     syscall.Timespec{Sec: source.Atim.Sec, Nsec: source.Atim.Nsec},
		Mtimespec:     syscall.Timespec{Sec: source.Mtim.Sec, Nsec: source.Mtim.Nsec},
		Ctimespec:     syscall.Timespec{Sec: source.Ctim.Sec, Nsec: source.Ctim.Nsec},
		Birthtimespec: syscall.Timespec{Sec: source.Btim.Sec, Nsec: source.Btim.Nsec},
		Size:          source.Size, Blocks: source.Blocks, Blksize: source.Blksize, Flags: source.Flags, Gen: source.Gen,
	}
	return darwinFileInfo{name: name, stat: stat}, nil
}

type darwinFileInfo struct {
	name string
	stat syscall.Stat_t
}

func (info darwinFileInfo) Name() string { return info.name }
func (info darwinFileInfo) Size() int64  { return info.stat.Size }
func (info darwinFileInfo) ModTime() time.Time {
	return time.Unix(info.stat.Mtimespec.Sec, info.stat.Mtimespec.Nsec)
}
func (info darwinFileInfo) IsDir() bool { return info.Mode().IsDir() }
func (info darwinFileInfo) Sys() any    { return &info.stat }
func (info darwinFileInfo) Mode() os.FileMode {
	mode := os.FileMode(info.stat.Mode & 0o777)
	if info.stat.Mode&0o4000 != 0 {
		mode |= os.ModeSetuid
	}
	if info.stat.Mode&0o2000 != 0 {
		mode |= os.ModeSetgid
	}
	if info.stat.Mode&0o1000 != 0 {
		mode |= os.ModeSticky
	}
	switch info.stat.Mode & unix.S_IFMT {
	case unix.S_IFDIR:
		mode |= os.ModeDir
	case unix.S_IFLNK:
		mode |= os.ModeSymlink
	case unix.S_IFIFO:
		mode |= os.ModeNamedPipe
	case unix.S_IFSOCK:
		mode |= os.ModeSocket
	case unix.S_IFCHR:
		mode |= os.ModeDevice | os.ModeCharDevice
	case unix.S_IFBLK:
		mode |= os.ModeDevice
	}
	return mode
}
func lstatAt(dir *os.File, name string) (os.FileInfo, error) {
	return fileInfoAt(dir, name, unix.AT_SYMLINK_NOFOLLOW)
}
func statAt(dir *os.File, name string) (os.FileInfo, error) { return fileInfoAt(dir, name, 0) }
func readlinkAt(dir *os.File, name string) (string, error) {
	size := 256
	for size <= 1<<20 {
		buffer := make([]byte, size)
		count, err := unix.Readlinkat(int(dir.Fd()), name, buffer)
		if err != nil {
			return "", err
		}
		if count < len(buffer) {
			return string(buffer[:count]), nil
		}
		size *= 2
	}
	return "", errors.New("symlink target exceeds limit")
}
func resolvedDescriptorPath(file *os.File) (string, error) {
	path, err := darwinPathForDescriptor(file)
	if err != nil {
		return "", err
	}
	return filepath.Clean(path), nil
}

func descriptorPathMayAlias(info os.FileInfo) bool {
	stat, ok := info.Sys().(*syscall.Stat_t)
	// F_GETPATH follows the vnode path cache, which any hardlink opener can change.
	return !ok || stat.Nlink != 1
}

func resolvedChildPath(dir *os.File, name string) (string, error) {
	fd, err := unix.Openat(int(dir.Fd()), name, unix.O_RDONLY|unix.O_CLOEXEC|unix.O_NONBLOCK, 0)
	if err != nil {
		return "", err
	}
	file := os.NewFile(uintptr(fd), name)
	defer file.Close()
	return resolvedDescriptorPath(file)
}
func platformMkdirAt(dir *os.File, name string, mode uint32) error {
	return unix.Mkdirat(int(dir.Fd()), name, mode)
}
func platformUnlinkAt(dir *os.File, name string, removeDirectory bool) error {
	flags := 0
	if removeDirectory {
		flags = unix.AT_REMOVEDIR
	}
	return unix.Unlinkat(int(dir.Fd()), name, flags)
}
func platformLinkNoReplace(dir *os.File, oldName, newName string) error {
	if err := unix.Linkat(int(dir.Fd()), oldName, int(dir.Fd()), newName, 0); err != nil {
		return err
	}
	return platformUnlinkAt(dir, oldName, false)
}
func platformRenameBetweenNoReplace(dir *os.File, oldName string, target *os.File, newName string) error {
	return unix.RenameatxNp(int(dir.Fd()), oldName, int(target.Fd()), newName, unix.RENAME_EXCL)
}
func platformOpenRoot() (int, error) {
	fd, err := unix.Open("/", unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err != nil {
		return -1, fmt.Errorf("open root: %w", err)
	}
	return fd, nil
}

func darwinPathForDescriptor(file *os.File) (string, error) {
	buffer := make([]byte, unix.PathMax)
	_, _, errno := unix.Syscall(unix.SYS_FCNTL, file.Fd(), unix.F_GETPATH, uintptr(unsafe.Pointer(&buffer[0])))
	if errno != 0 {
		return "", errno
	}
	for index, value := range buffer {
		if value == 0 {
			return string(buffer[:index]), nil
		}
	}
	return "", errors.New("descriptor path exceeds system limit")
}

func platformChildFDPath(fd int) string           { return fmt.Sprintf("/dev/fd/%d", fd) }
func platformGitPath(file *os.File, _ int) string { return platformDescriptorPath(file) }

func platformMetadataTimes(stat *syscall.Stat_t) (any, any) {
	return stat.Atimespec.Sec, stat.Birthtimespec.Sec
}
func platformMetadataRestriction(stat *syscall.Stat_t) string {
	if stat.Flags&(unix.UF_IMMUTABLE|unix.SF_IMMUTABLE|unix.UF_APPEND|unix.SF_APPEND) != 0 {
		return "The item is immutable or append-only"
	}
	return ""
}
func platformBirthAt(parent *os.File, name string) *int64 {
	info, err := lstatAt(parent, name)
	if err != nil {
		return nil
	}
	value := info.Sys().(*syscall.Stat_t).Birthtimespec.Sec
	return &value
}
func platformFilesystemRestriction(file *os.File) (string, error) {
	var stat unix.Statfs_t
	if err := unix.Fstatfs(int(file.Fd()), &stat); err != nil {
		return "", err
	}
	if stat.Flags&unix.MNT_RDONLY != 0 {
		return "The filesystem is read-only", nil
	}
	return "", nil
}

func platformMetadataOpenFlags() int                      { return unix.O_EVTONLY | unix.O_NOFOLLOW | unix.O_CLOEXEC }
func platformChmod(file *os.File, mode os.FileMode) error { return file.Chmod(mode) }
