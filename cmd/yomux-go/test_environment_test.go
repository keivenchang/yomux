// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"fmt"
	"os"
	"strings"
	"testing"

	"github.com/keivenchang/yomux/internal/testsupport"
)

func TestMain(main *testing.M) {
	cleanup, err := testsupport.NewPrivateTestEnvironment()
	if err != nil {
		fmt.Fprintln(os.Stderr, "create private test environment:", err)
		os.Exit(2)
	}
	code := main.Run()
	cleanup()
	os.Exit(code)
}

func TestPrivateTestEnvironmentPropagatesShortTempRoot(t *testing.T) {
	tempRoot := os.Getenv("TMPDIR")
	if tempRoot == "" || os.TempDir() != tempRoot {
		t.Fatalf("private temp root = %q, os.TempDir() = %q", tempRoot, os.TempDir())
	}
	for _, entry := range testsupport.SyntheticEnvironment() {
		if strings.HasPrefix(entry, "TMPDIR=") {
			if got := strings.TrimPrefix(entry, "TMPDIR="); got != tempRoot {
				t.Fatalf("synthetic child TMPDIR = %q, want %q", got, tempRoot)
			}
			return
		}
	}
	t.Fatal("synthetic child environment omitted private TMPDIR")
}
