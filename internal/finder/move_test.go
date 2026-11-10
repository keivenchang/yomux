// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestFinderCommittedMoveFollowsHardlinkedDocuments(t *testing.T) {
	for _, moveParent := range []bool{false, true} {
		for _, directoryAlias := range []bool{false, true} {
			t.Run(fmt.Sprintf("parent=%t/alias=%t", moveParent, directoryAlias), func(t *testing.T) {
				events := []map[string]any{}
				s, root := newTestService(t, func(name string, payload map[string]any) error {
					if name == "file_relocated" {
						events = append(events, payload)
					}
					return nil
				})
				source := filepath.Join(root, "src", "note.md")
				if err := os.WriteFile(source, []byte("document"), 0600); err != nil {
					t.Fatal(err)
				}
				untouched := filepath.Join(root, "other-link.md")
				if err := os.Link(source, untouched); err != nil {
					t.Fatal(err)
				}
				namespace := root
				if directoryAlias {
					namespace = filepath.Join(t.TempDir(), "alias")
					if err := os.Symlink(root, namespace); err != nil {
						t.Fatal(err)
					}
				}
				old := filepath.Join(namespace, "src", "note.md")
				info, err := os.Stat(source)
				if err != nil {
					t.Fatal(err)
				}
				first := registerTestOpenFile(t, s, "one", old, fileID(info), "", []any{namespace})
				second := registerTestOpenFile(t, s, "two", old, fileID(info), "", []any{namespace})
				other := registerTestOpenFile(t, s, "other", untouched, fileID(info), "", []any{root})
				if first["tracking_id"] != second["tracking_id"] {
					t.Fatal("clients did not share the moved document")
				}
				target := filepath.Join(root, "target")
				if err := os.Mkdir(target, 0700); err != nil {
					t.Fatal(err)
				}
				moving, expected := source, filepath.Join(namespace, "target", "note.md")
				if moveParent {
					moving, expected = filepath.Dir(source), filepath.Join(namespace, "target", "src", "note.md")
				}
				if _, failure := s.Move(context.Background(), []string{moving}, target); failure != nil {
					t.Fatal(failure)
				}
				for _, client := range []string{"one", "two"} {
					row := registerTestOpenFile(t, s, client, old, fileID(info), first["tracking_id"].(string), []any{namespace})
					if row["path"] != expected || row["revision"] != uint64(2) {
						t.Fatalf("%s snapshot = %#v, want %s at revision 2", client, row, expected)
					}
				}
				unchanged := registerTestOpenFile(t, s, "other", untouched, fileID(info), other["tracking_id"].(string), []any{root})
				if unchanged["path"] != untouched || unchanged["revision"] != uint64(1) {
					t.Fatalf("untouched hardlink moved: %#v", unchanged)
				}
				if len(events) != 1 || events[0]["new_path"] != expected || events[0]["tracking_id"] != first["tracking_id"] {
					t.Fatalf("relocation events = %#v", events)
				}
			})
		}
	}
}

func TestFinderMoveSelectionAndPreflight(t *testing.T) {
	root := t.TempDir()
	source, destination := filepath.Join(root, "source"), filepath.Join(root, "destination")
	for _, dir := range []string{source, destination, filepath.Join(source, "folder")} {
		if err := os.MkdirAll(dir, 0700); err != nil {
			t.Fatal(err)
		}
	}
	file := filepath.Join(source, "note.txt")
	child := filepath.Join(source, "folder", "child.txt")
	for _, path := range []string{file, child} {
		if err := os.WriteFile(path, []byte("original"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	collision := filepath.Join(destination, "note.txt")
	if err := os.WriteFile(collision, []byte("keep"), 0600); err != nil {
		t.Fatal(err)
	}
	if result, failure := service.Move(context.Background(), []string{filepath.Dir(child), file}, destination); failure == nil {
		t.Fatalf("collision accepted: %#v", result)
	}
	if _, err := os.Stat(child); err != nil {
		t.Fatalf("preflight moved earlier item: %v", err)
	}
	if err := os.Remove(collision); err != nil {
		t.Fatal(err)
	}
	result, failure := service.Move(context.Background(), []string{file, filepath.Dir(child), child, file}, destination)
	if failure != nil || result["complete"] != true {
		t.Fatalf("move = %#v, %v", result, failure)
	}
	if len(result["moved"].([]map[string]any)) != 2 {
		t.Fatalf("selection not normalized: %#v", result)
	}
	for _, path := range []string{filepath.Join(destination, "note.txt"), filepath.Join(destination, "folder", "child.txt")} {
		if data, err := os.ReadFile(path); err != nil || string(data) != "original" {
			t.Fatalf("destination %s: %q %v", path, data, err)
		}
	}
	for _, path := range []string{file, filepath.Dir(child)} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("source remains: %s %v", path, err)
		}
	}
}

func TestFinderMoveRefusesInvalidTargets(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "folder")
	child := filepath.Join(dir, "child")
	if err := os.MkdirAll(child, 0700); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	for _, destination := range []string{dir, child, root, filepath.Join(root, "absent")} {
		if result, failure := service.Move(context.Background(), []string{dir}, destination); failure == nil {
			t.Fatalf("invalid destination %s accepted: %#v", destination, result)
		}
	}
	if _, err := os.Stat(child); err != nil {
		t.Fatal(err)
	}
	if _, failure := service.Move(context.Background(), []string{"relative"}, root); failure == nil {
		t.Fatal("accepted relative path")
	}
}

func TestFinderMoveSymlinkAndCancellation(t *testing.T) {
	root := t.TempDir()
	destination := filepath.Join(root, "destination")
	if err := os.Mkdir(destination, 0700); err != nil {
		t.Fatal(err)
	}
	original, link := filepath.Join(root, "original"), filepath.Join(root, "link")
	if err := os.WriteFile(original, []byte("preserve"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(original, link); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	result, failure := service.Move(ctx, []string{link}, destination)
	if failure == nil && result["complete"] == true {
		t.Fatal("cancelled move succeeded")
	}
	if _, err := os.Lstat(link); err != nil {
		t.Fatal(err)
	}
	result, failure = service.Move(context.Background(), []string{link}, destination)
	if failure != nil || result["complete"] != true {
		t.Fatalf("symlink move: %#v %v", result, failure)
	}
	if target, err := os.Readlink(filepath.Join(destination, "link")); err != nil || target != original {
		t.Fatalf("symlink = %s %v", target, err)
	}
	if data, err := os.ReadFile(original); err != nil || string(data) != "preserve" {
		t.Fatalf("original = %q %v", data, err)
	}
}

func TestFinderMoveGitRollbackAndPartialResult(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	destination := filepath.Join(root, "destination")
	if err := os.Mkdir(destination, 0700); err != nil {
		t.Fatal(err)
	}
	second := filepath.Join(root, "second.txt")
	third := filepath.Join(root, "third.txt")
	for _, path := range []string{second, third} {
		if err := os.WriteFile(path, []byte("source"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	service.publishGitRename = func(context.Context, *gitRenamePlan, string) error { return errors.New("injected index failure") }
	result, failure := service.Move(context.Background(), []string{filepath.Join(root, "note.txt")}, destination)
	if failure != nil || result["complete"] != false {
		t.Fatalf("rollback result: %#v %v", result, failure)
	}
	if _, err := os.Stat(filepath.Join(root, "note.txt")); err != nil {
		t.Fatal("rollback lost source", err)
	}
	service.publishGitRename = func(ctx context.Context, plan *gitRenamePlan, path string) error {
		if err := plan.publish(ctx, path); err != nil {
			return err
		}
		return os.WriteFile(filepath.Join(destination, "second.txt"), []byte("concurrent owner"), 0600)
	}
	result, failure = service.Move(context.Background(), []string{filepath.Join(root, "note.txt"), second, third}, destination)
	if failure != nil || result["complete"] != false || len(result["moved"].([]map[string]any)) != 1 || len(result["unattempted"].([]string)) != 1 {
		t.Fatalf("partial result: %#v %v", result, failure)
	}
	if data, err := os.ReadFile(filepath.Join(destination, "second.txt")); err != nil || string(data) != "concurrent owner" {
		t.Fatalf("overwrote collision: %q %v", data, err)
	}
	for _, path := range []string{second, third} {
		if _, err := os.Stat(path); err != nil {
			t.Fatal("lost unmoved source", err)
		}
	}
	command := exec.Command("git", "-C", root, "show", ":destination/note.txt")
	command.Env = gitCommandEnvironment()
	if data, err := command.Output(); err != nil || string(data) != "one\n" {
		t.Fatalf("index content: %q %v", data, err)
	}
}

func TestFinderMoveAcrossRepositoriesPreservesIndexes(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	target, _ := newCommittedTestRepo(t)
	destination := filepath.Join(target, "destination")
	if err := os.Mkdir(destination, 0700); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	result, failure := service.Move(context.Background(), []string{filepath.Join(root, "note.txt")}, destination)
	if failure != nil || result["complete"] != true {
		t.Fatalf("cross-repo move: %#v %v", result, failure)
	}
	for _, repo := range []string{root, target} {
		command := exec.Command("git", "-C", repo, "diff", "--cached", "--exit-code")
		command.Env = gitCommandEnvironment()
		if output, err := command.CombinedOutput(); err != nil {
			t.Fatalf("changed index: %s %v", output, err)
		}
	}
}

func TestFinderMoveRefusesStagedDestinationWithoutDiskFile(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	destination := filepath.Join(root, "destination")
	if err := os.Mkdir(destination, 0700); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(destination, "note.txt")
	if err := os.WriteFile(target, []byte("unique staged content"), 0600); err != nil {
		t.Fatal(err)
	}
	command := exec.Command("git", "-C", root, "add", "--", target)
	command.Env = gitCommandEnvironment()
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("stage: %s %v", output, err)
	}
	if err := os.Remove(target); err != nil {
		t.Fatal(err)
	}
	index := filepath.Join(root, ".git", "index")
	before, err := os.ReadFile(index)
	if err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	result, failure := service.Move(context.Background(), []string{filepath.Join(root, "note.txt")}, destination)
	if failure == nil && result["complete"] == true {
		t.Fatalf("overwrote staged destination: %#v", result)
	}
	after, err := os.ReadFile(index)
	if err != nil || string(before) != string(after) {
		t.Fatal("index changed", err)
	}
	if _, err := os.Stat(filepath.Join(root, "note.txt")); err != nil {
		t.Fatal("source moved", err)
	}
}

func TestFinderMovePreflightPinsSourceIdentity(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(root, "destination")
	source := filepath.Join(root, "source.txt")
	if err := os.Mkdir(target, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(source, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	plan, failure := service.prepareRename(context.Background(), source, filepath.Join(target, "source.txt"))
	if failure != nil {
		t.Fatal(failure)
	}
	defer plan.Close()
	if err := os.Rename(source, filepath.Join(root, "retained.txt")); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(source, []byte("replacement"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, failure := service.commitRename(context.Background(), plan); failure == nil {
		t.Fatal("moved replacement source")
	}
	if data, err := os.ReadFile(source); err != nil || string(data) != "replacement" {
		t.Fatalf("source = %q %v", data, err)
	}
}

func TestFinderMoveProtectedRootAndPermissionFailure(t *testing.T) {
	root := t.TempDir()
	destination := filepath.Join(root, "destination")
	if err := os.Mkdir(destination, 0700); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(root, "source")
	if err := os.WriteFile(source, []byte("preserve"), 0600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	if _, failure := service.Move(context.Background(), []string{root}, destination); failure == nil || failure.Status != 403 {
		t.Fatalf("protected root: %v", failure)
	}
	if os.Geteuid() == 0 {
		t.Skip("permission denial requires unprivileged user")
	}
	if err := os.Chmod(destination, 0500); err != nil {
		t.Fatal(err)
	}
	defer os.Chmod(destination, 0700)
	result, failure := service.Move(context.Background(), []string{source}, destination)
	if failure == nil && result["complete"] != false {
		t.Fatalf("permission failure = %#v %v", result, failure)
	}
	if data, err := os.ReadFile(source); err != nil || string(data) != "preserve" {
		t.Fatalf("lost source: %q %v", data, err)
	}
}

func TestFinderMoveAcrossFilesystemsPreservesSource(t *testing.T) {
	if _, err := os.Stat("/dev/shm"); err != nil {
		t.Skip("second filesystem not available")
	}
	destination, err := os.MkdirTemp("/dev/shm", "finder-move-")
	if err != nil {
		t.Skipf("second filesystem unavailable: %v", err)
	}
	defer os.RemoveAll(destination)
	root := t.TempDir()
	source := filepath.Join(root, "source.txt")
	if err := os.WriteFile(source, []byte("preserve"), 0600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	result, failure := service.Move(context.Background(), []string{source}, destination)
	if failure != nil {
		t.Fatal(failure)
	}
	if result["complete"] != false {
		t.Fatalf("expected cross-filesystem refusal: %#v", result)
	}
	if data, err := os.ReadFile(source); err != nil || string(data) != "preserve" {
		t.Fatalf("lost source: %q %v", data, err)
	}
}

func TestFinderRolledBackMoveKeepsOpenFileAtSource(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	destination := filepath.Join(root, "destination")
	if err := os.Mkdir(destination, 0700); err != nil {
		t.Fatal(err)
	}
	s, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	old := filepath.Join(root, "note.txt")
	info, _ := os.Stat(old)
	row := registerTestOpenFile(t, s, "audit", old, fileID(info), "", []any{root})
	id := row["tracking_id"].(string)
	s.publishGitRename = func(context.Context, *gitRenamePlan, string) error {
		s.reconcileOpenFiles()
		s.ReleaseWatchClient("audit")
		return errors.New("injected index failure")
	}
	result, failure := s.Move(context.Background(), []string{old}, destination)
	if failure != nil || result["complete"] != false {
		t.Fatalf("bad fixture receipt: %v %v", result, failure)
	}
	if _, err := os.Stat(old); err != nil {
		t.Fatal("rollback did not restore source", err)
	}
	s.reconcileOpenFiles()
	s.mu.Lock()
	retained := s.openFiles[id].path
	s.mu.Unlock()
	t.Logf("restored=%s retained=%s", old, retained)
	if retained != old {
		t.Fatal("failed move left the retained document at an absent destination")
	}
}

func TestFinderCommittedMoveFollowsDisconnectedOpenFilesInTheirNamespaces(t *testing.T) {
	for _, directory := range []bool{false, true} {
		name := "file"
		if directory {
			name = "directory"
		}
		t.Run(name, func(t *testing.T) {
			root, _ := newCommittedTestRepo(t)
			for _, path := range []string{filepath.Join(root, "src", "folder"), filepath.Join(root, "destination")} {
				if err := os.MkdirAll(path, 0700); err != nil {
					t.Fatal(err)
				}
			}
			if output, err := exec.Command("git", "-C", root, "mv", "note.txt", "src/folder/note.txt").CombinedOutput(); err != nil {
				t.Fatalf("fixture: %s %v", output, err)
			}
			alias := filepath.Join(t.TempDir(), "alias")
			if err := os.Symlink(root, alias); err != nil {
				t.Fatal(err)
			}
			sourceAlias := filepath.Join(t.TempDir(), "source-alias")
			if err := os.Symlink(filepath.Join(root, "src", "folder"), sourceAlias); err != nil {
				t.Fatal(err)
			}
			events := make(chan map[string]any, 4)
			s, err := New(root, func(kind string, payload map[string]any) error {
				if kind == "file_relocated" {
					events <- payload
				}
				return nil
			})
			if err != nil {
				t.Fatal(err)
			}
			defer s.Close()
			ids := make(map[string]string)
			for client, namespace := range map[string]string{"physical": root, "alias": alias, "source-alias": sourceAlias} {
				path := filepath.Join(namespace, "src", "folder", "note.txt")
				if namespace == sourceAlias {
					path = filepath.Join(namespace, "note.txt")
				}
				info, err := os.Stat(path)
				if err != nil {
					t.Fatal(err)
				}
				row := registerTestOpenFile(t, s, client, path, fileID(info), "", []any{namespace})
				ids[namespace] = row["tracking_id"].(string)
			}
			s.publishGitRename = func(ctx context.Context, plan *gitRenamePlan, path string) error {
				s.reconcileOpenFiles()
				if len(events) != 0 {
					t.Fatal("uncommitted move published a document relocation")
				}
				s.ReleaseWatchClient("physical")
				s.ReleaseWatchClient("alias")
				s.ReleaseWatchClient("source-alias")
				return plan.publish(ctx, path)
			}
			source := filepath.Join(root, "src", "folder", "note.txt")
			relative := "note.txt"
			if directory {
				source = filepath.Dir(source)
				relative = filepath.Join("folder", "note.txt")
			}
			result, failure := s.Move(context.Background(), []string{source}, filepath.Join(root, "destination"))
			if failure != nil || result["complete"] != true {
				t.Fatalf("receipt: %v %v", result, failure)
			}
			for namespace, id := range ids {
				if namespace == sourceAlias {
					namespace = root
				}
				want := filepath.Join(namespace, "destination", relative)
				s.mu.Lock()
				got := s.openFiles[id].path
				scope := s.openFiles[id].scope.path
				s.mu.Unlock()
				if got != want || scope != filepath.Dir(want) {
					t.Fatalf("committed document: path=%s scope=%s want=%s", got, scope, want)
				}
			}
			if len(events) != len(ids) {
				t.Fatalf("committed relocations=%d want=%d", len(events), len(ids))
			}
		})
	}
}
