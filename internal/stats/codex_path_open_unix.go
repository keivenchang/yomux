//go:build linux

package stats

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/sys/unix"
)

var (
	errCodexForkRepairNotDirectory = errors.New("Codex fork repair entry is not a directory")
	errCodexForkRepairSymlink      = errors.New("Codex fork repair entry is a symlink")
)

func openCodexForkRepairPathNoSymlinks(root, path string, directory bool) (*os.File, os.FileInfo, error) {
	if !codexForkRepairPathWithinRoot(root, path, directory) {
		return nil, nil, fmt.Errorf("Codex fork repair path is outside its catalog root")
	}
	physicalRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		return nil, nil, fmt.Errorf("resolve Codex repair catalog root: %w", err)
	}
	rootParts := strings.Split(strings.TrimPrefix(filepath.Clean(physicalRoot), string(filepath.Separator)), string(filepath.Separator))
	fd, err := unix.Open(string(filepath.Separator), unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, nil, err
	}
	for _, part := range rootParts {
		if part == "" || part == "." {
			continue
		}
		next, openErr := unix.Openat(fd, part, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC|unix.O_NOFOLLOW, 0)
		_ = unix.Close(fd)
		if openErr != nil {
			return nil, nil, fmt.Errorf("open Codex repair root component %q: %w", part, openErr)
		}
		fd = next
	}
	relative, _ := filepath.Rel(root, path)
	parts := []string(nil)
	if relative != "." {
		parts = strings.Split(relative, string(filepath.Separator))
	}
	for index, part := range parts {
		flags := unix.O_RDONLY | unix.O_CLOEXEC | unix.O_NOFOLLOW | unix.O_NONBLOCK
		if index < len(parts)-1 || directory {
			flags |= unix.O_DIRECTORY
		}
		next, openErr := unix.Openat(fd, part, flags, 0)
		_ = unix.Close(fd)
		if openErr != nil {
			if index == len(parts)-1 && openErr == unix.ELOOP {
				return nil, nil, fmt.Errorf("open Codex repair path component %q: %w", part, errCodexForkRepairSymlink)
			}
			if index == len(parts)-1 && directory && openErr == unix.ENOTDIR {
				return nil, nil, fmt.Errorf("open Codex repair path component %q: %w", part, errCodexForkRepairNotDirectory)
			}
			return nil, nil, fmt.Errorf("open Codex repair path component %q: %w", part, openErr)
		}
		fd = next
	}
	file := os.NewFile(uintptr(fd), path)
	info, err := file.Stat()
	if err != nil {
		_ = file.Close()
		return nil, nil, err
	}
	return file, info, nil
}
