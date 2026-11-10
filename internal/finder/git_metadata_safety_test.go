// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestGitAlternateFIFOIsRejectedWithoutBlocking(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	service := newService(root, nil, nil, nativeWatchStatus{}, UploadLimits{})
	defer service.Close()
	directory, e := os.Open(root)
	if e != nil {
		t.Fatal(e)
	}
	defer directory.Close()
	path := filepath.Join(root, ".git", "objects", "info", "alternates")
	if e := syscall.Mkfifo(path, 0600); e != nil {
		t.Fatal(e)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	done := make(chan *Error, 1)
	go func() {
		n, e := service.openGitNamespaceGenericContext(ctx, directory, root, root)
		if n != nil {
			n.close()
		}
		done <- e
	}()
	select {
	case e := <-done:
		if e == nil {
			t.Fatal("FIFO admitted")
		}
	case <-time.After(350 * time.Millisecond):
		f, e := os.OpenFile(path, os.O_WRONLY|syscall.O_NONBLOCK, 0)
		if e == nil {
			f.Close()
		}
		<-done
		t.Fatal("FIFO read ignored request cancellation")
	}
}
func TestBlameSHA256Metadata(t *testing.T) {
	sha := "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	rows := parseBlamePorcelain(sha + " 1 1 1\nauthor Tester\nauthor-time 1\nsummary hello\nfilename file\n\thello\n")
	if len(rows) != 1 {
		t.Fatalf("SHA256 blame lost rows: %v", rows)
	}
}

func TestUnifiedDiffEmptyRangeUsesPrecedingLine(t *testing.T) {
	for _, item := range []struct{ original, working, header string }{
		{"", "hi\n", "@@ -0,0 +1 @@"},
		{"hi\n", "", "@@ -1 +0,0 @@"},
	} {
		if got := unifiedFileDiff(item.original, item.working, "note"); !strings.Contains(got, item.header) {
			t.Fatalf("diff=%s want=%s", got, item.header)
		}
	}
}

func TestBlameSHA256Repository(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "note.txt")
	if err := os.WriteFile(path, []byte("committed\n"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"init", "-q", "--object-format=sha256", root}, {"-C", root, "config", "user.name", "Finder Test"}, {"-C", root, "config", "user.email", "test@example.invalid"}, {"-C", root, "add", "note.txt"}, {"-C", root, "commit", "-qm", "initial"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v %s", args, err, output)
		}
	}
	service := newService(root, nil, nil, nativeWatchStatus{}, UploadLimits{})
	defer service.Close()
	for _, current := range []bool{false, true} {
		if current {
			if err := os.WriteFile(path, []byte("working\n"), 0600); err != nil {
				t.Fatal(err)
			}
		}
		result, err := service.Blame(context.Background(), path, "working")
		if err != nil {
			t.Fatal(err)
		}
		rows := result["lines"].(map[string]map[string]any)
		if len(rows) != 1 || len(rows["1"]["sha"].(string)) != 64 || current && rows["1"]["sha"] != strings.Repeat("0", 64) {
			t.Fatalf("current=%t rows=%v", current, rows)
		}
	}
}
