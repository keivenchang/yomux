// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build linux

package finder

import (
	"fmt"
	"golang.org/x/sys/unix"
	"os"
	"path/filepath"
	"syscall"
	"unsafe"
)

type darwinGitSnapshotCache struct{}

func newDarwinGitSnapshotCache() *darwinGitSnapshotCache {
	return &darwinGitSnapshotCache{}
}
func (*darwinGitSnapshotCache) close() {}

const atRemovedir = 0x200
const sysRenameat2 = 316

func platformOpenat(fd int, name string, flags int, mode uint32) (int, error) {
	return syscall.Openat(fd, name, flags, mode)
}
func platformEnterGitWorktree(*os.File) (func() error, error) {
	return func() error { return nil }, nil
}
func platformFstat(fd int) (syscall.Stat_t, error) {
	var stat syscall.Stat_t
	err := syscall.Fstat(fd, &stat)
	return stat, err
}
func platformOpenRoot() (int, error) {
	return syscall.Open("/", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_CLOEXEC, 0)
}
func statMtimeNS(stat *syscall.Stat_t) int64      { return stat.Mtim.Sec*1e9 + stat.Mtim.Nsec }
func statCTime(stat *syscall.Stat_t) int64        { return stat.Ctim.Sec*1e9 + stat.Ctim.Nsec }
func platformDescriptorPath(file *os.File) string { return fmt.Sprintf("/proc/self/fd/%d", file.Fd()) }
func lstatAt(dir *os.File, name string) (os.FileInfo, error) {
	return os.Lstat(descriptorChildPath(dir, name))
}
func statAt(dir *os.File, name string) (os.FileInfo, error) {
	return os.Stat(descriptorChildPath(dir, name))
}
func readlinkAt(dir *os.File, name string) (string, error) {
	return os.Readlink(descriptorChildPath(dir, name))
}
func resolvedDescriptorPath(file *os.File) (string, error) {
	return filepath.EvalSymlinks(platformDescriptorPath(file))
}

func descriptorPathMayAlias(os.FileInfo) bool { return false }

func resolvedChildPath(dir *os.File, name string) (string, error) {
	return filepath.EvalSymlinks(descriptorChildPath(dir, name))
}
func platformMkdirAt(dir *os.File, name string, mode uint32) error {
	nameBytes := append([]byte(name), 0)
	_, _, errno := syscall.Syscall(syscall.SYS_MKDIRAT, dir.Fd(), uintptr(unsafe.Pointer(&nameBytes[0])), uintptr(mode))
	if errno != 0 {
		return errno
	}
	return nil
}
func platformUnlinkAt(dir *os.File, name string, removeDirectory bool) error {
	nameBytes := append([]byte(name), 0)
	flags := uintptr(0)
	if removeDirectory {
		flags = atRemovedir
	}
	_, _, errno := syscall.Syscall(syscall.SYS_UNLINKAT, dir.Fd(), uintptr(unsafe.Pointer(&nameBytes[0])), flags)
	if errno != 0 {
		return errno
	}
	return nil
}
func platformLinkNoReplace(dir *os.File, oldName, newName string) error {
	oldBytes := append([]byte(oldName), 0)
	newBytes := append([]byte(newName), 0)
	_, _, errno := syscall.Syscall6(syscall.SYS_LINKAT, dir.Fd(), uintptr(unsafe.Pointer(&oldBytes[0])), dir.Fd(), uintptr(unsafe.Pointer(&newBytes[0])), 0, 0)
	if errno != 0 {
		return errno
	}
	return platformUnlinkAt(dir, oldName, false)
}
func platformRenameBetweenNoReplace(dir *os.File, oldName string, target *os.File, newName string) error {
	oldBytes := append([]byte(oldName), 0)
	newBytes := append([]byte(newName), 0)
	_, _, errno := syscall.Syscall6(sysRenameat2, dir.Fd(), uintptr(unsafe.Pointer(&oldBytes[0])), target.Fd(), uintptr(unsafe.Pointer(&newBytes[0])), 1, 0)
	if errno != 0 {
		return errno
	}
	return nil
}
func platformChildFDPath(fd int) string         { return fmt.Sprintf("/proc/self/fd/%d", fd) }
func platformGitPath(_ *os.File, fd int) string { return platformChildFDPath(fd) }

func platformMetadataTimes(stat *syscall.Stat_t) (any, any)   { return stat.Atim.Sec, nil }
func platformMetadataRestriction(stat *syscall.Stat_t) string { return "" }
func platformBirthAt(parent *os.File, name string) *int64 {
	var stat unix.Statx_t
	if unix.Statx(int(parent.Fd()), name, unix.AT_SYMLINK_NOFOLLOW, unix.STATX_BTIME, &stat) == nil && stat.Mask&unix.STATX_BTIME != 0 {
		value := stat.Btime.Sec
		return &value
	}
	return nil
}
func platformFilesystemRestriction(file *os.File) (string, error) {
	var stat unix.Statfs_t
	if err := unix.Fstatfs(int(file.Fd()), &stat); err != nil {
		return "", err
	}
	if stat.Flags&unix.ST_RDONLY != 0 {
		return "The filesystem is read-only", nil
	}

	var attributes unix.Statx_t
	if err := unix.Statx(int(file.Fd()), "", unix.AT_EMPTY_PATH, unix.STATX_BASIC_STATS, &attributes); err != nil {
		return "", err
	}
	if attributes.Attributes&(unix.STATX_ATTR_IMMUTABLE|unix.STATX_ATTR_APPEND) != 0 {
		return "The item is immutable or append-only", nil
	}
	return "", nil
}
func platformMetadataOpenFlags() int { return unix.O_PATH | unix.O_NOFOLLOW | unix.O_CLOEXEC }
func platformChmod(file *os.File, mode os.FileMode) error {
	n := numericMode(mode)
	err := unix.Fchmodat(int(file.Fd()), "", n, unix.AT_EMPTY_PATH)
	if err == unix.EOPNOTSUPP || err == unix.ENOSYS {
		return os.Chmod(platformDescriptorPath(file), mode)
	}
	return err
}
