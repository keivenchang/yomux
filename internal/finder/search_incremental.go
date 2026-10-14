// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime/pprof"
	"sort"
)

func mergeIndexChanges(root string, left, right []string) []string {
	if len(left) == 0 || len(right) == 0 {
		return nil
	} // A full reconciliation supersedes deltas.
	paths := uniqueWatchPaths(append(append([]string{}, left...), right...))
	sort.Strings(paths)
	compact := []string{}
	for _, path := range paths {
		if !indexRootWithin(root, path) {
			return nil
		}
		if len(compact) > 0 && indexRootWithin(compact[len(compact)-1], path) {
			continue
		}
		compact = append(compact, path)
	}
	return compact
}

func bareRepositoryPolicyRoot(root, changedPath string, changedPaths []string) string {
	for candidate := filepath.Dir(changedPath); indexRootWithin(root, candidate); candidate = filepath.Dir(candidate) {
		markers := []struct {
			name      string
			directory bool
		}{
			{name: "HEAD"}, {name: "config"}, {name: "objects", directory: true}, {name: "refs", directory: true},
		}
		matches := true
		for _, marker := range markers {
			path := filepath.Join(candidate, marker.name)
			touched := false
			for _, changed := range changedPaths {
				if indexRootWithin(path, changed) || indexRootWithin(changed, path) {
					touched = true
					break
				}
			}
			if touched {
				continue
			}
			info, err := os.Lstat(path)
			if err != nil || marker.directory && !info.IsDir() || !marker.directory && !info.Mode().IsRegular() {
				matches = false
				break
			}
		}
		if matches {
			return candidate
		}
		if candidate == root {
			break
		}
	}
	return ""
}

// Delta discovery reuses the published generation and consumes only changed subtrees.
// Partial snapshots require full discovery because an unrelated missing entry may now fit.
func (engine *searchIndexEngine) discoverBuildPaths(ctx context.Context, job indexBuildJob, prior int) ([]discoveredIndexPath, string, string, error) {
	pprof.SetGoroutineLabels(pprof.WithLabels(context.Background(), pprof.Labels("phase", "index-discovery")))
	defer pprof.SetGoroutineLabels(context.Background())
	var identity, coverage string
	var revision uint64
	err := engine.store.db.QueryRow(`SELECT identity,coverage,settings_revision FROM roots WHERE root=? AND generation=?`, job.root, prior).Scan(&identity, &coverage, &revision)
	if len(job.changedPaths) == 0 || prior == 0 || err != nil || identity != job.identity || revision != job.settingsRevision || coverage != "complete" {
		engine.mu.Lock()
		engine.fullDiscoveries++
		engine.mu.Unlock()
		visited := uint64(0)
		paths, coverage, reason, err := discoverIndexPaths(ctx, job.root, job.excludeDirs, job.excludePaths, job.maxFiles, func() { visited++ })
		engine.mu.Lock()
		engine.directoriesVisited += visited
		engine.mu.Unlock()
		return paths, coverage, reason, err
	}
	changes := mergeIndexChanges(job.root, job.changedPaths, job.changedPaths)
	if len(changes) == 0 || changes[0] == job.root {
		job.changedPaths = nil
		return engine.discoverBuildPaths(ctx, job, prior)
	}
	for _, path := range changes {
		if repositoryRoot := bareRepositoryPolicyRoot(job.root, path, changes); repositoryRoot != "" {
			changes = append(changes, repositoryRoot)
		}
	}
	changes = mergeIndexChanges(job.root, changes, changes)
	if len(changes) == 0 || changes[0] == job.root {
		job.changedPaths = nil
		return engine.discoverBuildPaths(ctx, job, prior)
	}
	paths := []discoveredIndexPath{}
	entries, err := engine.store.loadNormalizedPaths(ctx, job.root, prior)
	if err != nil {
		return nil, "", "", err
	}
	for _, entry := range entries {
		if !indexRootWithinAny(changes, filepath.Join(job.root, filepath.FromSlash(entry.relativePath()))) {
			paths = append(paths, entry)
		}
	}
	policy, err := compileSearchPolicy(job.root, job.excludeDirs, job.excludePaths)
	if err != nil {
		return nil, "", "", err
	}
	rootIdentities, err := captureResolvedIdentities(job.root)
	if err != nil {
		return nil, "", "", err
	}
	rootDirectory, err := openResolvedDescriptorContext(ctx, job.root, true, rootIdentities)
	if err != nil {
		return nil, "", "", err
	}
	defer rootDirectory.Close()
	bareCache := make(map[string]bool)
	visited := uint64(0)
	limitReached := false
	appendEntry := func(path string, info os.FileInfo) {
		if len(paths) >= job.maxFiles {
			limitReached = true
			return
		}
		relative, _ := filepath.Rel(job.root, path)
		paths = append(paths, discoveredIndexPath{
			relative: filepath.ToSlash(relative), name: filepath.Base(path), identity: fileID(info),
			size: info.Size(), mtimeNS: info.ModTime().UnixNano(),
		})
	}
	for _, path := range changes {
		if err := ctx.Err(); err != nil {
			return nil, "", "", err
		}
		relative, _ := filepath.Rel(job.root, path)
		if policy.excludedSearchRoot() || policy.excludedPath(path) {
			continue
		}
		info, err := os.Lstat(path)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return nil, "", "", err
		}
		if info.IsDir() && hasName(policy.names, info.Name()) {
			continue
		}
		if info.Mode()&os.ModeSymlink != 0 {
			continue
		}
		if !info.IsDir() {
			if !info.Mode().IsRegular() {
				continue
			}
			pinned, openErr := openIndexedSearchFile(rootDirectory, filepath.ToSlash(relative), policy, bareCache)
			if errors.Is(openErr, os.ErrNotExist) {
				continue
			}
			if openErr != nil {
				return nil, "", "", openErr
			}
			pinnedInfo, statErr := pinned.Stat()
			pinned.Close()
			if statErr != nil {
				return nil, "", "", statErr
			}
			if len(paths) >= job.maxFiles {
				limitReached = true
				break
			}
			appendEntry(path, pinnedInfo)
			continue
		}
		pinned, openErr := openIndexedSearchDirectory(rootDirectory, filepath.ToSlash(relative), policy, bareCache)
		if errors.Is(openErr, os.ErrNotExist) {
			continue
		}
		if openErr != nil {
			return nil, "", "", openErr
		}
		visited++
		walkErr := walkSearchDescriptors(ctx, pinned, path, path, policy, func(entry walkedEntry) error {
			if entry.IsDir {
				visited++
				return nil
			}
			if len(paths) >= job.maxFiles {
				limitReached = true
				return errWalkStop
			}
			appendEntry(entry.Path, entry.Info)
			return nil
		})
		pinned.Close()
		if limitReached {
			job.changedPaths = nil
			return engine.discoverBuildPaths(ctx, job, prior)
		}
		if walkErr != nil {
			if errors.Is(walkErr, errWalkDirectoryLimit) {
				job.changedPaths = nil
				return engine.discoverBuildPaths(ctx, job, prior)
			}
			return nil, "", "", walkErr
		}
	}
	if limitReached {
		job.changedPaths = nil
		return engine.discoverBuildPaths(ctx, job, prior)
	}
	engine.mu.Lock()
	engine.incrementalDiscoveries++
	engine.directoriesVisited += visited
	engine.mu.Unlock()
	sort.Slice(paths, func(i, j int) bool { return paths[i].relativePath() < paths[j].relativePath() })
	if len(paths) > job.maxFiles {
		return paths[:job.maxFiles], "partial", indexCoverageFileLimit, nil
	}
	return paths, "complete", "", nil
}
