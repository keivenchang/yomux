// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
package finder

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

type recoveryCollisionContext struct {
	context.Context
	root, path, quarantine string
	injectionErr           error
}

func TestPreparedDeletionRejectsReplacementTargets(t *testing.T) {
	for _, kind := range []string{"directory", "file", "symlink"} {
		t.Run(kind, func(t *testing.T) {
			service, root := newTestService(t, nil)
			path := filepath.Join(root, "target")
			if err := os.Mkdir(path, 0o700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(path, "child"), []byte("original"), 0o600); err != nil {
				t.Fatal(err)
			}
			deletion, failure := service.PrepareDeletion(context.Background(), path)
			if failure != nil {
				t.Fatal(failure)
			}
			defer deletion.Close()
			result, failure := deletion.Delete(context.Background(), false)
			if failure != nil || result["pending"] != "subtree" {
				t.Fatalf("bounded delete: %#v %v", result, failure)
			}
			preserved := path + "-original"
			if err := os.Rename(path, preserved); err != nil {
				t.Fatal(err)
			}
			var err error
			switch kind {
			case "directory":
				err = os.Mkdir(path, 0o700)
			case "file":
				err = os.WriteFile(path, []byte("replacement"), 0o600)
			case "symlink":
				err = os.Symlink(preserved, path)
			}
			if err != nil {
				t.Fatal(err)
			}
			before, err := os.Lstat(path)
			if err != nil {
				t.Fatal(err)
			}
			if _, failure := deletion.Delete(context.Background(), true); failure == nil || failure.Code != "changed_on_disk" {
				t.Fatalf("replacement was not rejected: %#v", failure)
			}
			after, err := os.Lstat(path)
			if err != nil || !os.SameFile(before, after) {
				t.Fatalf("replacement changed: %v", err)
			}
			if got, err := os.ReadFile(filepath.Join(preserved, "child")); err != nil || string(got) != "original" {
				t.Fatalf("original generation changed: %q %v", got, err)
			}
		})
	}
}

func TestPreparedDeletionKeepsPinnedParentAcrossNamespaceReplacement(t *testing.T) {
	service, root := newTestService(t, nil)
	parent := filepath.Join(root, "parent")
	if err := os.MkdirAll(filepath.Join(parent, "target"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(parent, "target", "child"), []byte("original"), 0o600); err != nil {
		t.Fatal(err)
	}
	deletion, failure := service.PrepareDeletion(context.Background(), filepath.Join(parent, "target"))
	if failure != nil {
		t.Fatal(failure)
	}
	defer deletion.Close()
	if result, failure := deletion.Delete(context.Background(), false); failure != nil || result["pending"] != "subtree" {
		t.Fatalf("bounded delete: %#v %v", result, failure)
	}
	if err := os.Rename(parent, parent+"-original"); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(parent, "target"), 0o700); err != nil {
		t.Fatal(err)
	}
	replacement := filepath.Join(parent, "target", "child")
	if err := os.WriteFile(replacement, []byte("replacement"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, failure := deletion.Delete(context.Background(), true); failure != nil {
		t.Fatal(failure)
	}
	if got, err := os.ReadFile(replacement); err != nil || string(got) != "replacement" {
		t.Fatalf("replacement parent was consumed: %q %v", got, err)
	}
	if _, err := os.Stat(filepath.Join(parent+"-original", "target")); !os.IsNotExist(err) {
		t.Fatalf("original pinned target was not removed: %v", err)
	}
}

func (c *recoveryCollisionContext) Err() error {
	if c.quarantine != "" {
		return context.Canceled
	}
	entries, err := os.ReadDir(c.root)
	if err != nil {
		c.injectionErr = err
		return context.Canceled
	}
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), ".yolomux-mutation-") {
			c.quarantine = entry.Name()
			c.injectionErr = os.WriteFile(c.path, []byte("competing owner"), 0600)
			return context.Canceled
		}
	}
	return nil
}

func TestMutationRestorationFailureRetainsSourceAndReplacement(t *testing.T) {
	for _, operation := range []string{"write", "delete"} {
		t.Run(operation, func(t *testing.T) {
			service, root := newTestService(t, nil)
			path := filepath.Join(root, "note.md")
			ctx := &recoveryCollisionContext{Context: context.Background(), root: root, path: path}
			var failure *Error
			if operation == "write" {
				_, failure = service.Write(ctx, path, "new content", nil)
			} else {
				_, failure = service.Delete(ctx, path)
			}
			if ctx.injectionErr != nil || ctx.quarantine == "" {
				t.Fatalf("repro not reached: %v %q", ctx.injectionErr, ctx.quarantine)
			}
			recovered, err := os.ReadFile(filepath.Join(root, ctx.quarantine))
			if err != nil || string(recovered) != "hello\n" {
				t.Fatalf("original recovery lost %q %v", recovered, err)
			}
			current, err := os.ReadFile(path)
			if err != nil || string(current) != "competing owner" {
				t.Fatalf("replacement owner lost %q %v", current, err)
			}
			if failure == nil || failure.Code != "restoration_failed" || !strings.Contains(failure.Message, filepath.Join(root, ctx.quarantine)) || failure.Details["recovery_path"] != filepath.Join(root, ctx.quarantine) || failure.MessageKey != "common.errorDetail" || failure.MessageParams["error"] != failure.Message {
				t.Fatalf("recovery error hidden: failure=%+v original retained at %s", failure, ctx.quarantine)
			}
		})
	}
}

func TestMutationGitRenameRestorationFailureRetainsSourceAndReplacement(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	source, target := filepath.Join(root, "note.txt"), filepath.Join(root, "renamed.txt")
	original, err := os.ReadFile(source)
	if err != nil {
		t.Fatal(err)
	}
	service.publishGitRename = func(ctx context.Context, plan *gitRenamePlan, path string) error {
		if err := plan.publish(ctx, path); err != nil {
			return err
		}
		if err := os.WriteFile(source, []byte("competing owner"), 0600); err != nil {
			return err
		}
		return errors.New("forced post-publication failure")
	}
	_, failure := service.Rename(context.Background(), source, "renamed.txt")
	retained, err := os.ReadFile(target)
	if err != nil || string(retained) != string(original) {
		t.Fatalf("retained original lost: %q %v", retained, err)
	}
	replacement, err := os.ReadFile(source)
	if err != nil || string(replacement) != "competing owner" {
		t.Fatalf("competing owner lost: %q %v", replacement, err)
	}
	if failure == nil || failure.Code != "restoration_failed" || failure.Details["recovery_path"] != target || failure.MessageKey != "common.errorDetail" || failure.MessageParams["error"] != failure.Message || !strings.Contains(failure.Message, target) {
		t.Fatalf("retained destination recovery hidden: %+v", failure)
	}
}

func TestMutationRestorationPreservesTypedCauseAndFailedRecoveryChain(t *testing.T) {
	for _, collision := range []bool{false, true} {
		service, root := newTestService(t, nil)
		path := filepath.Join(root, "note.md")
		parent, failure := service.openMutationParent(path)
		if failure != nil {
			t.Fatal(failure)
		}
		defer parent.Close()
		quarantine, failure := service.quarantineMutationTarget(parent)
		if failure != nil {
			t.Fatal(failure)
		}
		if collision {
			if err := os.WriteFile(path, []byte("replacement"), 0600); err != nil {
				t.Fatal(err)
			}
		}
		cause := cancelled(path)
		restored := restoreMutationFailure(parent, quarantine, cause)
		if !errors.Is(restored, cause) {
			t.Fatal("original operation cause discarded")
		}
		result := filesystemError(path, restored)
		if !collision {
			if result != cause {
				t.Fatalf("successful restoration changed typed outcome: %+v", result)
			}
		} else {
			if !errors.Is(restored, syscall.EEXIST) || result.Code != "restoration_failed" {
				t.Fatalf("restoration failure lost: %+v %v", result, restored)
			}
			if _, err := os.Stat(filepath.Join(root, quarantine)); err != nil {
				t.Fatal(err)
			}
		}
	}
}
