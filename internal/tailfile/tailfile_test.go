// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package tailfile

import (
	"os"
	"path/filepath"
	"testing"
)

func TestReadSuffixLinesAndByteWindow(t *testing.T) {
	path := filepath.Join(t.TempDir(), "messages.jsonl")
	content := "old\nsecond\nthird\n"
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := Read(path, 2, 0)
	if err != nil || got != "second\nthird\n" {
		t.Fatalf("Read suffix = %q, %v", got, err)
	}
	got, err = Read(path, 2, 7)
	if err != nil || got != "third\n" {
		t.Fatalf("Read byte-bounded suffix = %q, %v", got, err)
	}
}

func TestReadDoesNotReturnPartialFirstLine(t *testing.T) {
	path := filepath.Join(t.TempDir(), "messages.jsonl")
	if err := os.WriteFile(path, []byte("partial-long\nkept\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := Read(path, 1, 10)
	if err != nil || got != "kept\n" {
		t.Fatalf("Read partial byte window = %q, %v", got, err)
	}
}
