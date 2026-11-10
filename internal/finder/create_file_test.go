// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestCreateFileDoesNotReplaceEntryAppearingDuringWrite(t *testing.T) {
	for _, kind := range []string{"file", "symlink"} {
		t.Run(kind, func(t *testing.T) {
			service, root := newTestService(t, nil)
			path := filepath.Join(root, "new.txt")
			target := filepath.Join(root, "note.md")
			service.beforeWriteConflictRecheck = func() {
				var err error
				if kind == "file" {
					err = os.WriteFile(path, []byte("external"), 0600)
				} else {
					err = os.Symlink(target, path)
				}
				if err != nil {
					t.Fatal(err)
				}
			}
			_, failure := service.CreateFile(context.Background(), path, "replacement")
			if failure == nil || failure.Status != 409 {
				t.Fatalf("race failure=%v", failure)
			}
			content, err := os.ReadFile(path)
			expected := "external"
			if kind == "symlink" {
				expected = "hello\n"
				if _, err := os.Readlink(path); err != nil {
					t.Fatal(err)
				}
			}
			if err != nil || string(content) != expected {
				t.Fatalf("content=%q error=%v", content, err)
			}
		})
	}
}
