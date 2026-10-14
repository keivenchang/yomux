//go:build linux

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"errors"
	"golang.org/x/sys/unix"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
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
	paths     map[string]int
	scopes    map[string]bool
	coverage  map[string]bool
	visited   uint64
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
		paths:     make(map[string]int),
		roots:     make(map[int]string),
		closed:    make(chan struct{}),
		done:      make(chan struct{}),
		wakeRead:  wake[0],
		wakeWrite: wake[1],
	}
	go watcher.readLoop()
	return watcher, nativeWatchStatus{available: true}
}

// Both directions change together under mu, including descriptor reuse and
// ignored events. Scope coverage remains owned by the existing reconciliation.
func (w *linuxNativeWatcher) rememberRegistration(wd int, path string) {
	if w.paths == nil {
		w.paths = make(map[string]int)
	}
	if path := w.watches[wd]; w.paths[path] == wd {
		delete(w.paths, path)
	}
	w.watches[wd] = path
	w.paths[path] = wd
}

func (w *linuxNativeWatcher) forgetRegistration(wd int) {
	if path := w.watches[wd]; w.paths[path] == wd {
		delete(w.paths, path)
	}
	delete(w.watches, wd)
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
	scopes := make(map[string]bool, len(roots))
	for _, root := range roots {
		scopes[root] = true
	}
	return w.SetScopes(ctx, scopes, true)
}

func (w *linuxNativeWatcher) SetScopes(ctx context.Context, scopes map[string]bool, force bool) nativeWatchStatus {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.scopes == nil {
		w.scopes = map[string]bool{}
		w.coverage = map[string]bool{}
	}
	for wd, path := range w.watches {
		needed := false
		for root, recursive := range scopes {
			if path == root || recursive && indexRootWithin(root, path) {
				needed = true
				break
			}
		}
		if !needed {
			_, _ = syscall.InotifyRmWatch(w.fd, uint32(wd))
			w.forgetRegistration(wd)
			delete(w.roots, wd)
		}
	}
	roots := make([]string, 0, len(scopes))
	for root := range scopes {
		roots = append(roots, root)
	}
	sort.Strings(roots)
	status := nativeWatchStatus{available: true, registrations: len(w.watches)}
	visitedEntries := 0
	for _, root := range roots {
		if recursive, exists := w.scopes[root]; exists && recursive == scopes[root] && !force && w.coverage[root] {
			continue
		}
		rootFailed := false
		if ctx != nil {
			select {
			case <-ctx.Done():
				rootFailed = true
				status.degraded = true
				status.reason = "registration-cancelled"
				return status
			default:
			}
		}
		w.coverage[root] = false
		pinnedRoot, pinErr := openPinnedWatchDirectory(root)
		if pinErr != nil {
			rootFailed = true
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
			if entry != nil && entry.IsDir() {
				w.visited++
			}
			if visitedEntries > maxNativeWatchEntries {
				rootFailed = true
				status.degraded = true
				status.reason = "native-entry-capacity-exceeded"
				return errStopNativeWalk
			}
			if ctx != nil {
				select {
				case <-ctx.Done():
					rootFailed = true
					status.degraded = true
					status.reason = "registration-cancelled"
					return context.Canceled
				default:
				}
			}
			if err != nil {
				rootFailed = true
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
			if filepath.Base(logicalPath) == ".git" {
				if logicalPath != root {
					return fs.SkipDir
				}
				return nil
			}
			_, existingPath := w.paths[logicalPath]
			if status.registrations >= maxNativeWatchRegistrations && !existingPath {
				rootFailed = true
				status.degraded = true
				status.reason = "native-capacity-exceeded"
				return errStopNativeWalk
			}
			watchDescriptor, addErr := syscall.InotifyAddWatch(w.fd, path, inotifyMask)
			if addErr != nil {
				rootFailed = true
				status.degraded = true
				if status.reason == "" {
					status.reason = "native-registration-error"
				}
				return nil
			}
			_, existed := w.watches[watchDescriptor]
			w.rememberRegistration(watchDescriptor, logicalPath)
			w.roots[watchDescriptor] = root
			if !existed {
				status.registrations++
			}
			if logicalPath == root && !scopes[root] {
				return fs.SkipDir
			}
			return nil
		})
		_ = pinnedRoot.Close()
		w.coverage[root] = !rootFailed
		if rootFailed {
			status.uncovered = append(status.uncovered, root)
		}
		if errors.Is(walkErr, errStopNativeWalk) {
			break
		}
	}
	status.uncovered = nil
	for root := range scopes {
		if !w.coverage[root] {
			status.uncovered = append(status.uncovered, root)
			status.degraded = true
		}
	}
	sort.Strings(status.uncovered)

	for root := range w.coverage {
		if _, ok := scopes[root]; !ok {
			delete(w.coverage, root)
		}
	}
	w.scopes = make(map[string]bool, len(scopes))
	for root, recursive := range scopes {
		w.scopes[root] = recursive
	}
	for descriptor, path := range w.watches {
		if owner := nativeWatchOwner(scopes, path); owner != "" {
			w.roots[descriptor] = owner
		}
	}
	status.directoriesVisited = w.visited
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
		descriptors := []unix.PollFd{{Fd: int32(w.fd), Events: unix.POLLIN}, {Fd: int32(w.wakeRead), Events: unix.POLLIN}}
		_, err := unix.Poll(descriptors, -1)
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
		if descriptors[0].Revents&(unix.POLLERR|unix.POLLHUP|unix.POLLNVAL) != 0 {
			return
		}
		if descriptors[0].Revents&unix.POLLIN == 0 {
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
			if known && mask&(inIgnored|inDeleteSelf|inMoveSelf) != 0 {
				w.coverage[root] = false
			}
			if mask&inIgnored != 0 {
				w.forgetRegistration(int(watchDescriptor))
				delete(w.roots, int(watchDescriptor))
			}
			w.mu.Unlock()
			if mask&inIgnored != 0 {
				if known {
					w.emit(nativeWatchEvent{root: root, overflow: true})
				}
				offset += recordLength
				continue
			}
			if known {
				name := strings.TrimRight(string(buffer[offset+16:offset+recordLength]), "\x00")
				path := parent
				if name != "" {
					path = filepath.Join(parent, name)
				}
				if mask&inIsDir != 0 && mask&(inCreate|inMovedTo) != 0 && w.recursiveRoot(root) {
					w.addDirectoryTree(context.Background(), path, root)
				}
				w.emit(nativeWatchEvent{path: path, root: root, overflow: mask&(inDeleteSelf|inMoveSelf) != 0})
			}
			offset += recordLength
		}
	}
}

func (w *linuxNativeWatcher) recursiveRoot(root string) bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	for scope, recursive := range w.scopes {
		if recursive && indexRootWithin(scope, root) {
			return true
		}
	}
	return false
}

func (w *linuxNativeWatcher) addDirectoryTree(ctx context.Context, root, watchRoot string) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if filepath.Base(root) == ".git" {
		return
	}
	remaining := maxNativeWatchRegistrations - len(w.watches)
	if remaining <= 0 {
		w.coverage[watchRoot] = false
		w.emit(nativeWatchEvent{root: watchRoot, overflow: true})
		return
	}
	pinnedRoot, pinErr := openPinnedWatchDirectory(root)
	if pinErr != nil {
		w.coverage[watchRoot] = false
		w.emit(nativeWatchEvent{root: watchRoot, overflow: true})
		return
	}
	defer pinnedRoot.Close()
	// See SetRoots: the trailing "/." makes WalkDir traverse the pinned descriptor root.
	walkRoot := descriptorPath(pinnedRoot) + string(os.PathSeparator) + "."
	visitedEntries := 0
	walkErr := filepath.WalkDir(walkRoot, func(path string, entry fs.DirEntry, err error) error {
		visitedEntries++
		if entry != nil && entry.IsDir() {
			w.visited++
		}
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
		if !entry.IsDir() || filepath.Base(logicalPath) == ".git" {
			if entry.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		if _, exists := w.paths[logicalPath]; exists {
			return nil
		}
		watchDescriptor, addErr := syscall.InotifyAddWatch(w.fd, path, inotifyMask)
		if addErr != nil {
			return addErr
		}
		w.rememberRegistration(watchDescriptor, logicalPath)
		w.roots[watchDescriptor] = watchRoot
		remaining--
		return nil
	})
	if walkErr != nil {
		w.coverage[watchRoot] = false
		w.emit(nativeWatchEvent{root: watchRoot, overflow: true})
	}
}

func (w *linuxNativeWatcher) InvalidateRoot(root string) {
	w.mu.Lock()
	defer w.mu.Unlock()
	for scope, recursive := range w.scopes {
		if scope == root || indexRootWithin(root, scope) || recursive && indexRootWithin(scope, root) {
			w.coverage[scope] = false
		}
	}
	for wd, path := range w.watches {
		if indexRootWithin(root, path) {
			_, _ = syscall.InotifyRmWatch(w.fd, uint32(wd))
			w.forgetRegistration(wd)
			delete(w.roots, wd)
		}
	}
}

func nativeWatchOwner(scopes map[string]bool, path string) string {
	owner := ""
	ownerRecursive := false
	for root, recursive := range scopes {
		if path != root && (!recursive || !indexRootWithin(root, path)) {
			continue
		}
		if owner == "" || recursive && !ownerRecursive || recursive == ownerRecursive && len(root) < len(owner) {
			owner = root
			ownerRecursive = recursive
		}
	}
	return owner
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
			case w.events <- nativeWatchEvent{overflow: true}:
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
			case w.events <- nativeWatchEvent{overflow: true}:
			default:
			}
		}
	}
}

func (w *linuxNativeWatcher) Status() nativeWatchStatus {
	w.mu.Lock()
	defer w.mu.Unlock()
	status := nativeWatchStatus{available: true, registrations: len(w.watches), directoriesVisited: w.visited}
	for root := range w.scopes {
		if !w.coverage[root] {
			status.uncovered = append(status.uncovered, root)
			status.degraded = true
			status.reason = "native-uncovered"
		}
	}
	sort.Strings(status.uncovered)
	return status
}
