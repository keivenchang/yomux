// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"os"
	"path/filepath"
	"sync"
	"syscall"
)

const gitObjectLayoutCacheLimit = 4096

type gitObjectDirectoryStamp struct {
	identity descriptorIdentity
	ctimeNS  int64
}

type gitObjectDirectory struct {
	stamp    gitObjectDirectoryStamp
	children []string
}

// Directory ctime changes when a child is added, removed, or replaced, even if mtime is
// restored. Recheck every directory on each use, but only reread changed directories' entries.
// This preserves the symlink guard without enumerating every Git object before every command.
type gitObjectLayoutCache struct {
	mu      sync.Mutex
	entries map[string]gitObjectDirectory
}

func gitObjectDirectoryIdentity(path string) (gitObjectDirectoryStamp, error) {
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return gitObjectDirectoryStamp{}, errGitNamespaceChanged
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return gitObjectDirectoryStamp{}, errGitNamespaceChanged
	}
	return gitObjectDirectoryStamp{identityFor(info), statCTime(stat)}, nil
}

func (cache *gitObjectLayoutCache) validate(objects string) error {
	return cache.validateContext(context.Background(), objects)
}

func (cache *gitObjectLayoutCache) validateContext(ctx context.Context, objects string) error {
	return cache.validateDirectory(ctx, objects, true)
}

func (cache *gitObjectLayoutCache) validateDirectory(ctx context.Context, path string, root bool) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	before, err := gitObjectDirectoryIdentity(path)
	if err != nil {
		return err
	}
	cache.mu.Lock()
	cached, ok := cache.entries[path]
	cache.mu.Unlock()
	children := cached.children
	if !ok || cached.stamp != before {
		entries, err := os.ReadDir(path)
		if err != nil {
			return errGitNamespaceChanged
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		children = nil
		for _, entry := range entries {
			if err := ctx.Err(); err != nil {
				return err
			}
			if entry.Type()&os.ModeSymlink != 0 {
				return errGitNamespaceChanged
			}
			name := entry.Name()
			if entry.IsDir() {
				children = append(children, filepath.Join(path, name))
			}
		}
		after, err := gitObjectDirectoryIdentity(path)
		if err != nil || before != after {
			return errGitNamespaceChanged
		}
		cache.mu.Lock()
		if cache.entries == nil {
			cache.entries = make(map[string]gitObjectDirectory)
		}
		if len(cache.entries) >= gitObjectLayoutCacheLimit {
			for key := range cache.entries {
				delete(cache.entries, key)
				break
			}
		}
		cache.entries[path] = gitObjectDirectory{stamp: before, children: children}
		cache.mu.Unlock()
	}
	// The first scan can involve hundreds of NFS directory reads. Bound parallelism here;
	// subsequent validations only check cached directory stamps and remain synchronous.
	var pending sync.WaitGroup
	var failures chan error
	var slots chan struct{}
	if root && !ok {
		failures = make(chan error, len(children))
		slots = make(chan struct{}, 8)
	}
	for _, child := range children {
		if err := ctx.Err(); err != nil {
			break
		}
		name := filepath.Base(child)
		if root && name != "info" && name != "pack" && !isGitLooseObjectDirectory(name) {
			continue
		}
		if slots == nil {
			if err := cache.validateDirectory(ctx, child, false); err != nil {
				return err
			}
			continue
		}
		select {
		case slots <- struct{}{}:
		case <-ctx.Done():
			break
		}
		if ctx.Err() != nil {
			break
		}
		pending.Add(1)
		go func(child string) {
			defer pending.Done()
			defer func() { <-slots }()
			if err := cache.validateDirectory(ctx, child, false); err != nil && ctx.Err() == nil {
				failures <- err
			}
		}(child)
	}
	pending.Wait()
	if err := ctx.Err(); err != nil {
		return err
	}
	if failures != nil {
		close(failures)
		for err := range failures {
			return err
		}
	}

	if err := ctx.Err(); err != nil {
		return err
	}
	after, err := gitObjectDirectoryIdentity(path)
	if err != nil || before != after {
		return errGitNamespaceChanged
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	return nil
}
