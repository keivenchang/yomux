// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"unsafe"
)

// Go's syscall package does not expose these Linux constants on every supported Go release. The
// test and deployment images are amd64 Linux, where renameat2 is syscall 316.
const (
	atRemovedir  = 0x200
	sysRenameat2 = 316
)

// A filesystem request is authorized by name but consumed by descriptor.  Keeping this small
// owner in the finder package prevents each route (and each recursive producer) from inventing a
// separate check-then-open sequence.
type descriptorIdentity struct {
	dev     uint64
	ino     uint64
	mode    os.FileMode
	size    int64
	mtimeNS int64
}

func identityFor(info os.FileInfo) descriptorIdentity {
	identity := descriptorIdentity{mode: info.Mode(), size: info.Size(), mtimeNS: info.ModTime().UnixNano()}
	if stat, ok := info.Sys().(*syscall.Stat_t); ok {
		identity.dev = uint64(stat.Dev)
		identity.ino = uint64(stat.Ino)
	}
	return identity
}

func sameDescriptorIdentity(left, right descriptorIdentity) bool {
	// Device/inode is the namespace generation identity. Size and mtime are mutable file content
	// metadata and must not turn an otherwise descriptor-safe read into a false race failure.
	return left.dev == right.dev && left.ino == right.ino
}

func identityForFD(fd int) (descriptorIdentity, error) {
	var stat syscall.Stat_t
	if err := syscall.Fstat(fd, &stat); err != nil {
		return descriptorIdentity{}, err
	}
	return descriptorIdentity{
		dev: uint64(stat.Dev), ino: uint64(stat.Ino), mode: os.FileMode(stat.Mode), size: stat.Size,
		mtimeNS: stat.Mtim.Sec*1_000_000_000 + stat.Mtim.Nsec,
	}, nil
}

func descriptorPath(file *os.File) string {
	return "/proc/self/fd/" + fmt.Sprint(file.Fd())
}

func descriptorChildPath(directory *os.File, name string) string {
	return filepath.Join(descriptorPath(directory), name)
}

func openResolvedDescriptor(resolved string, directory bool, identities map[string]descriptorIdentity) (*os.File, error) {
	resolved = filepath.Clean(resolved)
	if !filepath.IsAbs(resolved) {
		return nil, fmt.Errorf("resolved path is not absolute")
	}
	flags := syscall.O_RDONLY | syscall.O_CLOEXEC | syscall.O_NOFOLLOW
	if directory {
		flags |= syscall.O_DIRECTORY
	}
	rootFD, err := syscall.Open("/", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	currentFD := rootFD
	currentPath := string(filepath.Separator)
	parts := strings.Split(strings.TrimPrefix(resolved, string(filepath.Separator)), string(filepath.Separator))
	if len(parts) == 1 && parts[0] == "" {
		if !directory {
			_ = syscall.Close(currentFD)
			return nil, syscall.EISDIR
		}
		return os.NewFile(uintptr(currentFD), resolved), nil
	}
	for index, part := range parts {
		if part == "" || part == "." {
			continue
		}
		// O_NONBLOCK keeps a special file from pinning a Finder worker in open(2). Regular files
		// and directories retain their normal behavior, while unsupported device/FIFO paths fail
		// through the typed Finder error boundary instead of consuming a worker indefinitely.
		openFlags := syscall.O_RDONLY | syscall.O_CLOEXEC | syscall.O_NOFOLLOW | syscall.O_NONBLOCK
		if index < len(parts)-1 || directory {
			openFlags |= syscall.O_DIRECTORY
		}
		fd, openErr := syscall.Openat(currentFD, part, openFlags, 0)
		if openErr != nil {
			_ = syscall.Close(currentFD)
			return nil, openErr
		}
		_ = syscall.Close(currentFD)
		currentFD = fd
		currentPath = filepath.Join(currentPath, part)
		if want, ok := identities[currentPath]; ok {
			actual, statErr := identityForFD(currentFD)
			if statErr != nil || !sameDescriptorIdentity(actual, want) {
				_ = syscall.Close(currentFD)
				return nil, errChangedOnDisk
			}
		}
	}
	return os.NewFile(uintptr(currentFD), resolved), nil
}

func captureResolvedIdentities(resolved string) (map[string]descriptorIdentity, error) {
	identities := map[string]descriptorIdentity{}
	current := filepath.Clean(string(filepath.Separator))
	rootInfo, err := os.Lstat(current)
	if err != nil {
		return nil, err
	}
	identities[current] = identityFor(rootInfo)
	for _, part := range strings.Split(strings.TrimPrefix(filepath.Clean(resolved), string(filepath.Separator)), string(filepath.Separator)) {
		if part == "" || part == "." {
			continue
		}
		current = filepath.Join(current, part)
		info, statErr := os.Lstat(current)
		if statErr != nil {
			return nil, statErr
		}
		identities[current] = identityFor(info)
	}
	return identities, nil
}

func verifyOpenedChain(file *os.File, resolved string, identities map[string]descriptorIdentity) error {
	info, err := file.Stat()
	if err != nil {
		return err
	}
	want, ok := identities[filepath.Clean(resolved)]
	if !ok || !sameDescriptorIdentity(identityFor(info), want) {
		return errChangedOnDisk
	}
	return nil
}

var errChangedOnDisk = errors.New("authorized filesystem generation changed before consumption")
var errWalkStop = errors.New("authorized filesystem walk reached its bound")
var errWalkSkipDirectory = errors.New("authorized filesystem walk skipped a directory")
var errWalkDirectoryLimit = errors.New("authorized filesystem walk reached its per-directory bound")

func openDescriptorChild(directory *os.File, name string, wantDirectory bool) (*os.File, os.FileInfo, os.FileInfo, string, error) {
	return openDescriptorChildWithKind(directory, name, &wantDirectory)
}

func openDescriptorEntry(directory *os.File, name string) (*os.File, os.FileInfo, os.FileInfo, string, error) {
	return openDescriptorChildWithKind(directory, name, nil)
}

func openDescriptorChildWithKind(directory *os.File, name string, wantDirectory *bool) (*os.File, os.FileInfo, os.FileInfo, string, error) {
	path := descriptorChildPath(directory, name)
	linkInfo, err := os.Lstat(path)
	if err != nil {
		return nil, nil, nil, "", err
	}
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil {
		if os.IsNotExist(err) && linkInfo.Mode()&os.ModeSymlink != 0 {
			return nil, linkInfo, nil, "", nil
		}
		return nil, nil, nil, "", err
	}
	resolved, err = filepath.Abs(resolved)
	if err != nil {
		return nil, nil, nil, "", err
	}
	identities, err := captureResolvedIdentities(resolved)
	if err != nil {
		return nil, nil, nil, resolved, err
	}
	directoryTarget := wantDirectory != nil && *wantDirectory
	file, err := openResolvedDescriptor(resolved, directoryTarget, identities)
	if err != nil {
		return nil, nil, nil, resolved, err
	}
	targetInfo, err := file.Stat()
	if err != nil {
		_ = file.Close()
		return nil, nil, nil, resolved, err
	}
	if wantDirectory != nil && *wantDirectory != targetInfo.IsDir() {
		_ = file.Close()
		return nil, nil, nil, resolved, errors.New("child kind mismatch")
	}
	if linkInfo.Mode()&os.ModeSymlink == 0 && !sameDescriptorIdentity(identityFor(linkInfo), identityFor(targetInfo)) {
		_ = file.Close()
		return nil, nil, nil, resolved, errChangedOnDisk
	}
	return file, linkInfo, targetInfo, resolved, nil
}

func isRepositoryDescriptor(parent *os.File, name string) bool {
	child, _, targetInfo, _, err := openDescriptorChild(parent, name, true)
	if err != nil || targetInfo == nil {
		return false
	}
	defer child.Close()
	git, err := openAt(child, ".git", syscall.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return false
	}
	defer git.Close()
	info, err := git.Stat()
	return err == nil && (info.IsDir() || info.Mode().IsRegular())
}

// isRepositoryMarkerAt is the bounded fast-list probe. The parent directory has already been
// authorized and pinned by Service.open, so reopening the child through openDescriptorChild would
// only repeat the full path-resolution and identity walk for a marker that needs two openat calls.
// The full isRepositoryDescriptor path remains the owner for rich listings and symlink targets;
// FastList deliberately treats a symlinked repo as deferred metadata instead of paying that cost.
func isRepositoryMarkerAt(parent *os.File, name string) bool {
	child, err := openAt(parent, name, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return false
	}
	defer child.Close()
	for _, marker := range []string{".git", ".hg", ".svn", ".jj"} {
		markerFile, markerErr := openAt(child, marker, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
		if markerErr != nil {
			continue
		}
		markerInfo, statErr := markerFile.Stat()
		_ = markerFile.Close()
		if statErr != nil {
			continue
		}
		if marker == ".git" {
			// Keep the established Go contract: a .git file (linked worktree) or directory
			// is enough to classify the child during the cheap first-paint pass.
			return markerInfo.Mode().IsRegular() || markerInfo.IsDir()
		}
		if !markerInfo.IsDir() {
			continue
		}
		required := map[string][]string{
			".hg":  {"requires", "store"},
			".svn": {"wc.db", "entries"},
			".jj":  {"repo", "working_copy"},
		}[marker]
		for _, childName := range required {
			requiredFile, requiredErr := openAt(child, filepath.Join(marker, childName), syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
			if requiredErr == nil {
				_ = requiredFile.Close()
				return true
			}
		}
	}
	return false
}

func copyDescriptor(file *os.File) (*os.File, error) {
	fd, err := syscall.Dup(int(file.Fd()))
	if err != nil {
		return nil, err
	}
	return os.NewFile(uintptr(fd), file.Name()), nil
}

func openAt(directory *os.File, name string, flags int, mode uint32) (*os.File, error) {
	fd, err := syscall.Openat(int(directory.Fd()), name, flags|syscall.O_CLOEXEC, mode)
	if err != nil {
		return nil, err
	}
	return os.NewFile(uintptr(fd), name), nil
}

func mkdirAt(directory *os.File, name string, mode uint32) error {
	nameBytes := append([]byte(name), 0)
	_, _, errno := syscall.Syscall(syscall.SYS_MKDIRAT, directory.Fd(), uintptr(unsafe.Pointer(&nameBytes[0])), uintptr(mode))
	if errno != 0 {
		return errno
	}
	return nil
}

func unlinkAt(directory *os.File, name string, removeDirectory bool) error {
	nameBytes := append([]byte(name), 0)
	flags := uintptr(0)
	if removeDirectory {
		flags = atRemovedir
	}
	_, _, errno := syscall.Syscall(syscall.SYS_UNLINKAT, directory.Fd(), uintptr(unsafe.Pointer(&nameBytes[0])), flags)
	if errno != 0 {
		return errno
	}
	return nil
}

func renameNoReplace(directory *os.File, oldName, newName string) error {
	return renameAt2(directory, oldName, newName, 1)
}

func renameAt2(directory *os.File, oldName, newName string, flags uintptr) error {
	oldBytes := append([]byte(oldName), 0)
	newBytes := append([]byte(newName), 0)
	_, _, errno := syscall.Syscall6(
		sysRenameat2,
		directory.Fd(), uintptr(unsafe.Pointer(&oldBytes[0])),
		directory.Fd(), uintptr(unsafe.Pointer(&newBytes[0])), flags, 0,
	)
	if errno != 0 {
		return errno
	}
	return nil
}

func readDescriptorContext(ctx context.Context, file *os.File, limit int64) ([]byte, error) {
	copy, err := copyDescriptor(file)
	if err != nil {
		return nil, err
	}
	defer copy.Close()
	if limit < 0 {
		return nil, fmt.Errorf("negative read limit")
	}
	data := make([]byte, 0, minInt64(limit+1, 64*1024))
	buffer := make([]byte, 64*1024)
	for int64(len(data)) <= limit {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
		}
		remaining := limit + 1 - int64(len(data))
		if remaining < int64(len(buffer)) {
			buffer = buffer[:remaining]
		}
		if len(buffer) == 0 {
			break
		}
		count, readErr := copy.Read(buffer)
		data = append(data, buffer[:count]...)
		if readErr != nil {
			if readErr == io.EOF {
				return data, nil
			}
			return nil, readErr
		}
		if count == 0 {
			return data, nil
		}
	}
	return data, nil
}

func readFullDescriptorContext(ctx context.Context, file *os.File, destination []byte) error {
	for offset := 0; offset < len(destination); {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		count, err := file.Read(destination[offset:])
		offset += count
		if err != nil {
			if err == io.EOF && offset == len(destination) {
				return nil
			}
			return err
		}
		if count == 0 {
			return io.ErrUnexpectedEOF
		}
	}
	return nil
}

func minInt64(left, right int64) int {
	if left < right {
		return int(left)
	}
	return int(right)
}
