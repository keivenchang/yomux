// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"os"
	"path/filepath"
	"sync"
	"syscall"
	"weak"
)

// GrantedFile retains the admitted namespace generation, including every parent.
// Authorized Finder saves advance the admitted inode. ReadCurrent can also
// admit a regular replacement at the same basename under the pinned parents.
type GrantedFile struct {
	mu         sync.Mutex
	identityMu sync.RWMutex
	service    *Service
	path       string
	identities map[string]descriptorIdentity
}

func (s *Service) GrantFile(ctx context.Context, raw string) (*GrantedFile, *Error) {
	requested, resolved, failure := s.authorize(raw, false)
	if failure != nil {
		return nil, failure
	}
	// A grant must never acquire authority through a symlink, even at creation.
	if filepath.Clean(requested) != filepath.Clean(resolved) {
		return nil, invalid(raw, "symlinks cannot be granted")
	}
	ids, err := captureResolvedIdentitiesContext(ctx, resolved)
	if err != nil {
		return nil, filesystemError(raw, err)
	}
	for _, id := range ids {
		if id.mode&os.ModeSymlink != 0 {
			return nil, invalid(raw, "symlinks cannot be granted")
		}
	}
	g := &GrantedFile{service: s, path: resolved, identities: ids}
	if _, _, failure := g.Read(ctx, MaxRawBytes); failure != nil {
		return nil, failure
	}
	s.grantRegistryMu.Lock()
	if s.grantedFiles == nil {
		s.grantedFiles = make(map[string][]weak.Pointer[GrantedFile])
	}
	for path, pointers := range s.grantedFiles {
		live := pointers[:0]
		for _, pointer := range pointers {
			if pointer.Value() != nil {
				live = append(live, pointer)
			}
		}
		if len(live) == 0 {
			delete(s.grantedFiles, path)
		} else {
			s.grantedFiles[path] = live
		}
	}
	s.grantedFiles[g.path] = append(s.grantedFiles[g.path], weak.Make(g))
	s.grantRegistryMu.Unlock()
	return g, nil
}

func (g *GrantedFile) Read(ctx context.Context, limit int64) ([]byte, os.FileInfo, *Error) {
	return g.read(ctx, limit, false)
}

func (g *GrantedFile) ReadCurrent(ctx context.Context, limit int64) ([]byte, os.FileInfo, *Error) {
	return g.read(ctx, limit, true)
}

func (g *GrantedFile) read(ctx context.Context, limit int64, followReplacement bool) ([]byte, os.FileInfo, *Error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	identities := g.identitySnapshot()
	previous := identities[g.path]
	if followReplacement {
		delete(identities, g.path)
	}
	f, err := openResolvedDescriptorContext(ctx, g.path, false, identities)
	if err != nil {
		return nil, nil, changed(g.path)
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return nil, nil, filesystemError(g.path, err)
	}
	if !info.Mode().IsRegular() {
		return nil, nil, invalid(g.path, "not a regular file")
	}
	current := identityFor(info)
	replaced := followReplacement && !sameDescriptorIdentity(previous, current)
	if replaced {
		stat, ok := info.Sys().(*syscall.Stat_t)
		if !ok || current.dev != previous.dev || current.uid != previous.uid || stat.Nlink != 1 {
			return nil, nil, changed(g.path)
		}
	}
	if info.Size() > limit {
		return nil, nil, tooLarge(g.path, info.Size(), limit)
	}
	data, err := readDescriptorContext(ctx, f, limit)
	if err != nil {
		return nil, nil, filesystemError(g.path, err)
	}
	if int64(len(data)) > limit {
		return nil, nil, tooLarge(g.path, int64(len(data)), limit)
	}
	if replaced {
		g.identityMu.Lock()
		if sameDescriptorIdentity(g.identities[g.path], previous) {
			g.identities[g.path] = current
		}
		g.identityMu.Unlock()
	}
	return data, info, nil
}

func (g *GrantedFile) mutationParent(ctx context.Context) (*mutationParent, *Error) {
	directory, err := openResolvedDescriptorContext(ctx, filepath.Dir(g.path), true, g.identitySnapshot())
	if err != nil {
		return nil, changed(g.path)
	}
	id := g.identitySnapshot()[g.path]
	return &mutationParent{directory: directory, requested: g.path, resolvedParent: filepath.Dir(g.path), name: filepath.Base(g.path), targetIdentity: &id}, nil
}

func (g *GrantedFile) Write(ctx context.Context, content string, expected os.FileInfo) (map[string]any, *Error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	mtime := expected.ModTime().UnixNano()
	return g.service.write(ctx, g.path, content, nil, &mtime, fileID(expected), false, g)
}

// ReserveWrites linearizes collaboration admission against ordinary Finder saves.
// The write transaction checks this reservation after canonical parent resolution.
func (g *GrantedFile) ReserveWrites(ctx context.Context) *Error {
	return g.service.ReserveGrantedWrites(ctx, g)
}

func (s *Service) ReserveGrantedWrites(ctx context.Context, grants ...*GrantedFile) *Error {
	if err := s.openFileMutationMu.LockContext(ctx, s.stop); err != nil {
		return cancelled("")
	}
	defer s.openFileMutationMu.Unlock()
	for _, g := range grants {
		parent, failure := g.mutationParent(ctx)
		if failure != nil {
			return failure
		}
		_, failure = s.requireMutationTarget(parent, false)
		parent.Close()
		if failure != nil {
			return failure
		}
		if existing := s.writeReservations[g.path]; existing != nil && existing != g {
			return changed(g.path)
		}
	}
	if s.writeReservations == nil {
		s.writeReservations = make(map[string]*GrantedFile)
	}
	for _, g := range grants {
		s.writeReservations[g.path] = g
	}
	return nil
}

func (g *GrantedFile) ReleaseWrites(ctx context.Context) *Error {
	s := g.service
	if err := s.openFileMutationMu.LockContext(ctx, s.stop); err != nil {
		return cancelled(g.path)
	}
	defer s.openFileMutationMu.Unlock()
	if s.writeReservations[g.path] == g {
		delete(s.writeReservations, g.path)
	}
	return nil
}

func (g *GrantedFile) identitySnapshot() map[string]descriptorIdentity {
	g.identityMu.RLock()
	defer g.identityMu.RUnlock()
	ids := make(map[string]descriptorIdentity, len(g.identities))
	for path, id := range g.identities {
		ids[path] = id
	}
	return ids
}
func (s *Service) advanceGrantedFiles(path string, previous, next os.FileInfo) {
	if previous == nil {
		return
	}
	s.grantRegistryMu.Lock()
	defer s.grantRegistryMu.Unlock()
	for _, pointer := range s.grantedFiles[path] {
		g := pointer.Value()
		if g == nil {
			continue
		}
		g.identityMu.Lock()
		if sameDescriptorIdentity(g.identities[path], identityFor(previous)) {
			g.identities[path] = identityFor(next)
		}
		g.identityMu.Unlock()
	}
}
func (g *GrantedFile) Reauthorize(ctx context.Context) ([]byte, os.FileInfo, *Error) {
	replacement, failure := g.service.GrantFile(ctx, g.path)
	if failure != nil {
		return nil, nil, failure
	}
	data, info, failure := replacement.Read(ctx, MaxReadBytes)
	if failure != nil {
		return nil, nil, failure
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	g.identityMu.Lock()
	g.identities = replacement.identitySnapshot()
	g.identityMu.Unlock()
	return data, info, nil
}
