//go:build linux

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"unsafe"
)

const (
	inAccess     = 0x00000001
	inModify     = 0x00000002
	inAttrib     = 0x00000004
	inCloseWrite = 0x00000008
	inMovedFrom  = 0x00000040
	inMovedTo    = 0x00000080
	inCreate     = 0x00000100
	inDelete     = 0x00000200
	inDeleteSelf = 0x00000400
	inMoveSelf   = 0x00000800
	inQOverflow  = 0x00004000
	inIgnored    = 0x00008000
	inIsDir      = 0x40000000
)

const inotifyMask = inAttrib | inCloseWrite | inMovedFrom | inMovedTo | inCreate | inDelete | inDeleteSelf | inMoveSelf

type linuxNativeWatcher struct {
	fd        int
	events    chan nativeWatchEvent
	mu        sync.Mutex
	watches   map[int]string
	roots     map[int]string
	closed    chan struct{}
	done      chan struct{}
	wakeRead  int
	wakeWrite int
	closeOnce sync.Once
}

func newNativeWatcher() (nativeWatchController, nativeWatchStatus) {
	fd, err := syscall.InotifyInit1(syscall.O_CLOEXEC)
	if err != nil {
		return nil, nativeWatchStatus{degraded: true, reason: "inotify-unavailable"}
	}
	wake := []int{0, 0}
	if err := syscall.Pipe2(wake, syscall.O_CLOEXEC); err != nil {
		_ = syscall.Close(fd)
		return nil, nativeWatchStatus{degraded: true, reason: "inotify-wake-unavailable"}
	}
	watcher := &linuxNativeWatcher{
		fd:        fd,
		events:    make(chan nativeWatchEvent, 256),
		watches:   make(map[int]string),
		roots:     make(map[int]string),
		closed:    make(chan struct{}),
		done:      make(chan struct{}),
		wakeRead:  wake[0],
		wakeWrite: wake[1],
	}
	go watcher.readLoop()
	return watcher, nativeWatchStatus{available: true}
}

func (w *linuxNativeWatcher) Events() <-chan nativeWatchEvent { return w.events }

func (w *linuxNativeWatcher) Close() {
	w.closeOnce.Do(func() {
		close(w.closed)
		_, _ = syscall.Write(w.wakeWrite, []byte{1})
		<-w.done
		_ = syscall.Close(w.fd)
		_ = syscall.Close(w.wakeRead)
		_ = syscall.Close(w.wakeWrite)
	})
}

func (w *linuxNativeWatcher) SetRoots(ctx context.Context, roots []string) nativeWatchStatus {
	w.mu.Lock()
	defer w.mu.Unlock()
	for wd := range w.watches {
		_, _ = syscall.InotifyRmWatch(w.fd, uint32(wd))
	}
	w.watches = make(map[int]string)
	w.roots = make(map[int]string)
	status := nativeWatchStatus{available: true}
	visitedEntries := 0
	for _, root := range roots {
		if ctx != nil {
			select {
			case <-ctx.Done():
				status.degraded = true
				status.reason = "registration-cancelled"
				return status
			default:
			}
		}
		if status.registrations >= maxNativeWatchRegistrations {
			status.degraded = true
			status.reason = "native-capacity-exceeded"
			break
		}
		pinnedRoot, pinErr := openPinnedWatchDirectory(root)
		if pinErr != nil {
			status.degraded = true
			if status.reason == "" {
				status.reason = "native-registration-error"
			}
			continue
		}
		// WalkDir does not follow a symlink when the walk root itself is one. Adding "/." forces
		// /proc/self/fd/N to be consumed as the already-pinned directory while retaining the
		// descriptor identity if the namespace changes during registration.
		walkRoot := descriptorPath(pinnedRoot) + string(os.PathSeparator) + "."
		walkErr := filepath.WalkDir(walkRoot, func(path string, entry fs.DirEntry, err error) error {
			visitedEntries++
			if visitedEntries > maxNativeWatchEntries {
				status.degraded = true
				status.reason = "native-entry-capacity-exceeded"
				return errStopNativeWalk
			}
			if ctx != nil {
				select {
				case <-ctx.Done():
					status.degraded = true
					status.reason = "registration-cancelled"
					return context.Canceled
				default:
				}
			}
			if err != nil {
				status.degraded = true
				if status.reason == "" {
					status.reason = "native-registration-error"
				}
				return nil
			}
			logicalPath := logicalWatchPath(walkRoot, root, path)
			if logicalPath != root && (entry.Type()&os.ModeSymlink != 0 || !entry.IsDir()) {
				if entry.IsDir() {
					return fs.SkipDir
				}
				return nil
			}
			if isSecretPath(logicalPath) || filepath.Base(logicalPath) == ".git" {
				if logicalPath != root {
					return fs.SkipDir
				}
				return nil
			}
			if status.registrations >= maxNativeWatchRegistrations {
				status.degraded = true
				status.reason = "native-capacity-exceeded"
				return errStopNativeWalk
			}
			watchDescriptor, addErr := syscall.InotifyAddWatch(w.fd, path, inotifyMask)
			if addErr != nil {
				status.degraded = true
				if status.reason == "" {
					status.reason = "native-registration-error"
				}
				return nil
			}
			w.watches[watchDescriptor] = logicalPath
			w.roots[watchDescriptor] = root
			status.registrations++
			return nil
		})
		_ = pinnedRoot.Close()
		if errors.Is(walkErr, errStopNativeWalk) {
			break
		}
	}
	if status.degraded && status.reason == "" {
		status.reason = "native-degraded"
	}
	return status
}

var errStopNativeWalk = errors.New("native watcher registration limit reached")

func (w *linuxNativeWatcher) readLoop() {
	defer close(w.done)
	defer close(w.events)
	buffer := make([]byte, 64*1024)
	for {
		readSet := syscall.FdSet{}
		setLinuxFD(&readSet, w.fd)
		setLinuxFD(&readSet, w.wakeRead)
		maxFD := w.fd
		if w.wakeRead > maxFD {
			maxFD = w.wakeRead
		}
		_, err := syscall.Select(maxFD+1, &readSet, nil, nil, nil)
		if err != nil {
			if errors.Is(err, syscall.EINTR) {
				continue
			}
			return
		}
		select {
		case <-w.closed:
			return
		default:
		}
		if !isLinuxFDSet(&readSet, w.fd) {
			continue
		}
		count, err := syscall.Read(w.fd, buffer)
		if err != nil {
			if errors.Is(err, syscall.EINTR) {
				continue
			}
			return
		}
		for offset := 0; offset+16 <= count; {
			watchDescriptor := int32(*(*uint32)(unsafe.Pointer(&buffer[offset])))
			mask := *(*uint32)(unsafe.Pointer(&buffer[offset+4]))
			nameLength := int(*(*uint32)(unsafe.Pointer(&buffer[offset+12])))
			recordLength := 16 + nameLength
			if recordLength < 16 || offset+recordLength > count {
				break
			}
			if mask&inQOverflow != 0 {
				w.emit(nativeWatchEvent{overflow: true})
				offset += recordLength
				continue
			}
			w.mu.Lock()
			parent, known := w.watches[int(watchDescriptor)]
			root := w.roots[int(watchDescriptor)]
			if mask&inIgnored != 0 {
				delete(w.watches, int(watchDescriptor))
				delete(w.roots, int(watchDescriptor))
			}
			w.mu.Unlock()
			if mask&inIgnored != 0 {
				offset += recordLength
				continue
			}
			if known {
				name := strings.TrimRight(string(buffer[offset+16:offset+recordLength]), "\x00")
				path := parent
				if name != "" {
					path = filepath.Join(parent, name)
				}
				if mask&inIsDir != 0 && mask&(inCreate|inMovedTo) != 0 {
					w.addDirectoryTree(context.Background(), path, root)
				}
				w.emit(nativeWatchEvent{path: path, root: root})
			}
			offset += recordLength
		}
	}
}

func setLinuxFD(set *syscall.FdSet, fd int) {
	set.Bits[fd/64] |= int64(1) << uint(fd%64)
}

func isLinuxFDSet(set *syscall.FdSet, fd int) bool {
	return set.Bits[fd/64]&(int64(1)<<uint(fd%64)) != 0
}

func (w *linuxNativeWatcher) addDirectoryTree(ctx context.Context, root, watchRoot string) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if isSecretPath(root) || filepath.Base(root) == ".git" {
		return
	}
	remaining := maxNativeWatchRegistrations - len(w.watches)
	if remaining <= 0 {
		return
	}
	pinnedRoot, pinErr := openPinnedWatchDirectory(root)
	if pinErr != nil {
		return
	}
	defer pinnedRoot.Close()
	// See SetRoots: the trailing "/." makes WalkDir traverse the pinned descriptor root.
	walkRoot := descriptorPath(pinnedRoot) + string(os.PathSeparator) + "."
	visitedEntries := 0
	_ = filepath.WalkDir(walkRoot, func(path string, entry fs.DirEntry, err error) error {
		visitedEntries++
		if visitedEntries > maxNativeWatchEntries {
			return errStopNativeWalk
		}
		if ctx != nil {
			select {
			case <-ctx.Done():
				return context.Canceled
			default:
			}
		}
		if err != nil || remaining <= 0 {
			return errStopNativeWalk
		}
		logicalPath := logicalWatchPath(walkRoot, root, path)
		if entry.Type()&os.ModeSymlink != 0 {
			if entry.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		if !entry.IsDir() || isSecretPath(logicalPath) || filepath.Base(logicalPath) == ".git" {
			if entry.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		for _, existing := range w.watches {
			if existing == logicalPath {
				return nil
			}
		}
		watchDescriptor, addErr := syscall.InotifyAddWatch(w.fd, path, inotifyMask)
		if addErr != nil {
			return nil
		}
		w.watches[watchDescriptor] = logicalPath
		w.roots[watchDescriptor] = watchRoot
		remaining--
		return nil
	})
}

func openPinnedWatchDirectory(path string) (*os.File, error) {
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	return os.NewFile(uintptr(fd), path), nil
}

func logicalWatchPath(walkRoot, logicalRoot, path string) string {
	relative, err := filepath.Rel(walkRoot, path)
	if err != nil || relative == "." {
		return logicalRoot
	}
	return filepath.Join(logicalRoot, relative)
}

func (w *linuxNativeWatcher) emit(event nativeWatchEvent) {
	// Keep one slot free for the reconciliation marker. A marker is the safety signal that turns a
	// dropped burst into a bounded full scan; allowing ordinary events to consume the final slot
	// would lose that signal exactly when the queue is saturated.
	if !event.overflow && len(w.events) >= cap(w.events)-1 {
		if event.root != "" {
			select {
			case w.events <- nativeWatchEvent{root: event.root, overflow: true}:
			case <-w.closed:
			default:
			}
		}
		return
	}
	select {
	case w.events <- event:
	case <-w.closed:
	default:
		// A full event queue is equivalent to native queue overflow: reconcile the affected root.
		if event.root != "" {
			select {
			case w.events <- nativeWatchEvent{root: event.root, overflow: true}:
			default:
			}
		}
	}
}
