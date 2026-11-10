// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build darwin

package finder

import (
	"os"
	"path/filepath"
	"testing"
)

func TestOpenFilesFinalTargetObservationRejectsNewHardlink(t *testing.T) {
	s, root := newTestService(t, nil)
	original := filepath.Join(root, "note.md")
	alias := filepath.Join(root, "alias.md")
	info, err := os.Stat(original)
	if err != nil {
		t.Fatal(err)
	}
	row := registerTestOpenFile(t, s, "one", original, fileID(info), "", nil)
	s.mu.Lock()
	defer s.mu.Unlock()
	entry := s.openFiles[row["tracking_id"].(string)]
	before, err := entry.file.Stat()
	if err != nil || descriptorPathMayAlias(before) {
		t.Fatalf("initial descriptor must be unambiguous: %v", err)
	}
	// External changes land between the retained-descriptor and target snapshots.
	if err := os.Link(original, alias); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(t.TempDir(), "outside.md")
	if err := os.Rename(original, outside); err != nil {
		t.Fatal(err)
	}
	target, _, resolved, openErr := s.openAny(alias)
	if openErr != nil {
		t.Fatal(openErr)
	}
	defer target.Close()
	destination, err := resolvedDescriptorPath(entry.file)
	if err != nil {
		t.Fatal(err)
	}
	outsideResolved, err := filepath.EvalSymlinks(outside)
	if err != nil {
		t.Fatal(err)
	}
	if destination != resolved && destination != outsideResolved {
		t.Fatalf("descriptor named neither live hardlink: %s", destination)
	}
	t.Logf("descriptor path=%s; verifying possible alias candidate=%s", destination, resolved)
	after, err := target.Stat()
	if err != nil {
		t.Fatal(err)
	}
	// Either vnode-cache answer is legal; exercise the hazardous in-scope answer
	// without requiring the kernel to choose it during this particular run.
	if s.verifiedOpenFileRelocationLocked(entry, resolved, resolved, after, false) {
		t.Fatal("final target observation accepted a newly ambiguous descriptor")
	}
	if !entry.pathAmbiguous {
		t.Fatal("final target observation did not retain ambiguity")
	}
}
