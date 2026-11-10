// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestWatchChangesRetainDistinctFileGenerations(t *testing.T) {
	for _, mutation := range []string{"write", "restored-mtime", "replace", "permissions", "symlink-target"} {
		t.Run(mutation, func(t *testing.T) {
			root := t.TempDir()
			target, path := filepath.Join(root, "target"), filepath.Join(root, "target")
			if err := os.WriteFile(target, []byte("first"), 0o600); err != nil {
				t.Fatal(err)
			}
			if mutation == "symlink-target" {
				path = filepath.Join(root, "link")
				if err := os.Symlink(target, path); err != nil {
					t.Fatal(err)
				}
			}
			service := &Service{root: root, epoch: "audit", watches: map[string]watchRecord{"client": {Files: []string{path}}}}
			service.recordChange([]string{path}, "native-event")
			token := service.watchTokenLocked()
			read, finderErr := service.Read(context.Background(), path)
			if finderErr != nil || read["content"] != "first" {
				t.Fatalf("first generation read=%v err=%v", read, finderErr)
			}
			// An unchanged duplicate remains coalesced even after its first read.
			service.recordChange([]string{path}, "filesystem-change")
			if service.watchTokenLocked() != token {
				t.Fatal("unchanged duplicate advanced the token")
			}
			before, err := os.Stat(target)
			if err != nil {
				t.Fatal(err)
			}
			switch mutation {
			case "replace":
				if err := os.WriteFile(target+".new", []byte("later"), 0o600); err != nil {
					t.Fatal(err)
				}
				err = os.Rename(target+".new", target)
			case "permissions":
				err = os.Chmod(target, 0o644)
			default:
				err = os.WriteFile(target, []byte("later"), 0o600)
			}
			if err != nil {
				t.Fatal(err)
			}
			if mutation == "restored-mtime" {
				if err := os.Chtimes(target, before.ModTime(), before.ModTime()); err != nil {
					t.Fatal(err)
				}
			}
			service.recordChange([]string{path}, "native-event")
			diff, finderErr := service.WatchDiff(context.Background(), token, false)
			if finderErr != nil || diff["token"] == token || len(diff["files"].([]any)) != 1 {
				t.Fatalf("new generation lost after first read: diff=%v err=%v", diff, finderErr)
			}
		})
	}
}

func TestWatchRootInvalidationsDoNotAssumeUnchangedDescendants(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "file")
	if err := os.WriteFile(path, []byte("first"), 0o600); err != nil {
		t.Fatal(err)
	}
	service := &Service{root: root, epoch: "audit", watches: map[string]watchRecord{"client": {Roots: []string{root}}}}
	service.recordChange([]string{root}, "native-overflow")
	token := service.watchTokenLocked()
	if err := os.WriteFile(path, []byte("later"), 0o600); err != nil {
		t.Fatal(err)
	}
	service.recordChange([]string{root}, "native-overflow")
	diff, finderErr := service.WatchDiff(context.Background(), token, false)
	if finderErr != nil || diff["token"] == token || len(diff["directories"].([]any)) != 1 {
		t.Fatalf("changed descendant lost behind root metadata: diff=%v err=%v", diff, finderErr)
	}
}
