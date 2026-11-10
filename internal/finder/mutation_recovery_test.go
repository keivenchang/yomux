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
