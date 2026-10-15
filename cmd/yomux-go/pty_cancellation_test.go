// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"errors"
	"os"
	"os/exec"
	"testing"
	"time"
)

func TestPTYSlowChildWriteDeadlineAndClose(t *testing.T) {
	pair, err := openPTY(36, 120)
	if err != nil {
		t.Fatal(err)
	}
	child := exec.Command("sh", "-c", "stty raw -echo; printf ready; exec sleep 30")
	child.Stdin, child.Stdout, child.Stderr = pair.slave, pair.slave, pair.slave
	if err := child.Start(); err != nil {
		pair.close()
		t.Fatal(err)
	}
	defer func() { child.Process.Kill(); child.Wait(); pair.close() }()
	ready := make([]byte, 5)
	if _, err := pair.master.Read(ready); err != nil {
		t.Fatal(err)
	}
	if err := pair.master.SetWriteDeadline(time.Now().Add(100 * time.Millisecond)); err != nil {
		t.Fatalf("PTY input has no cancellation deadline: %v", err)
	}
	_, err = pair.master.Write(bytes.Repeat([]byte("x"), 8<<20))
	if !errors.Is(err, os.ErrDeadlineExceeded) {
		t.Fatalf("slow child write = %v", err)
	}
	if err := pair.master.SetWriteDeadline(time.Time{}); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { _, err := pair.master.Write(bytes.Repeat([]byte("x"), 8<<20)); done <- err }()
	if err := pair.master.Close(); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("write survived close")
		}
	case <-time.After(time.Second):
		t.Fatal("close did not interrupt PTY write")
	}
}
