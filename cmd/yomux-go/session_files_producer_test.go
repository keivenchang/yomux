// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
)

func TestSessionFilesProducerSubscribersReplayAndDisconnectIndependently(t *testing.T) {
	srv := &server{}
	plan := newSessionFilesPlan(sessionFilesRequest{session: "1", sessionIdentity: "incarnation", hours: 24})
	producer := &sessionFilesProducer{changed: make(chan struct{})}
	key := plan.viewID + ":" + plan.request.sessionIdentity
	srv.sessionFilesCache.producers = map[string]*sessionFilesProducer{key: producer}
	ctx, cancel := context.WithCancel(context.Background())
	first := make(chan *finder.Error, 1)
	second := make(chan *finder.Error, 1)
	firstRows, secondRows := make(chan bool, 2), make(chan bool, 2)
	go func() {
		_, err := srv.sharedSessionFilesProduce(ctx, plan, nil, nil, func(map[string]any) error { firstRows <- true; return nil })
		first <- err
	}()
	go func() {
		_, err := srv.sharedSessionFilesProduce(context.Background(), plan, nil, nil, func(map[string]any) error { secondRows <- true; return nil })
		second <- err
	}()
	publish := func() {
		srv.sessionFilesCache.mu.Lock()
		encoded, err := json.Marshal(map[string]any{"repos": []any{"repo"}})
		if err != nil {
			t.Fatal(err)
		}
		producer.chunks = append(producer.chunks, encoded)
		close(producer.changed)
		producer.changed = make(chan struct{})
		srv.sessionFilesCache.mu.Unlock()
	}
	publish()
	for _, rows := range []chan bool{firstRows, secondRows} {
		select {
		case <-rows:
		case <-time.After(5 * time.Second):
			t.Fatal("subscriber did not replay first repository")
		}
	}
	cancel()
	select {
	case err := <-first:
		if err == nil || err.Code != "cancelled" {
			t.Fatal("disconnect did not retire subscriber", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("disconnect blocked")
	}
	publish()
	select {
	case <-secondRows:
	case <-time.After(5 * time.Second):
		t.Fatal("one browser cancelled its sibling")
	}
	srv.sessionFilesCache.mu.Lock()
	producer.done = true
	producer.payload = map[string]any{"loaded": true}
	close(producer.changed)
	srv.sessionFilesCache.mu.Unlock()
	select {
	case err := <-second:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("completion blocked")
	}
	if len(srv.sessionFilesCache.producers) != 1 {
		t.Fatal("subscribers started duplicate producers")
	}
}

func TestSessionFilesColdProducerCoalescesRealGitAndSurvivesFirstDisconnect(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	root, held := t.TempDir(), t.TempDir()
	for _, repo := range []string{root, held} {
		for _, args := range [][]string{{"init", "-q", "--initial-branch=main", repo}, {"-C", repo, "config", "user.name", "Fixture"}, {"-C", repo, "config", "user.email", "fixture@example.invalid"}} {
			if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
				t.Fatalf("fixture git: %v %s", err, output)
			}
		}
		if err := os.WriteFile(filepath.Join(repo, "note.py"), []byte("old\n"), 0600); err != nil {
			t.Fatal(err)
		}
		for _, args := range [][]string{{"-C", repo, "add", "note.py"}, {"-C", repo, "commit", "-qm", "initial"}} {
			if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
				t.Fatalf("fixture git: %v %s", err, output)
			}
		}
		if err := os.WriteFile(filepath.Join(repo, "note.py"), []byte("new\n"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	// Git's diff opens the worktree attributes file, so a FIFO there holds the second
	// repository's real Git work until the test supplies its end of file.
	pipe := filepath.Join(held, ".gitattributes")
	if err = syscall.Mkfifo(pipe, 0600); err != nil {
		t.Fatal(err)
	}
	writerReady := make(chan *os.File, 1)
	writerFailed := make(chan error, 1)
	go func() {
		writer, openErr := os.OpenFile(pipe, os.O_WRONLY, 0600)
		if openErr != nil {
			writerFailed <- openErr
			return
		}
		writerReady <- writer
	}()
	var writer *os.File
	srv := &server{finder: service}
	defer func() {
		if writer != nil {
			writer.Close()
		}
		srv.stopBackgroundWork()
	}()

	plan := newSessionFilesPlan(sessionFilesRequest{session: "1", sessionIdentity: "cold", hours: 24, fromRef: "HEAD", toRef: "current"})
	roots := []string{root, held}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	first, second := make(chan *finder.Error, 1), make(chan *finder.Error, 1)
	rows := make(chan bool, 16)
	go func() {
		_, err := srv.sharedSessionFilesProduce(ctx, plan, roots, nil, func(map[string]any) error { rows <- true; return nil })
		first <- err
	}()
	select {
	case <-rows:
	case <-time.After(5 * time.Second):
		t.Fatal("cold Git repository did not stream before the held repository")
	}
	select {
	case writer = <-writerReady:
	case err := <-writerFailed:
		t.Fatal(err)
	case <-time.After(5 * time.Second):
		t.Fatal("Git did not reach the held repository")
	}
	go func() {
		_, err := srv.sharedSessionFilesProduce(context.Background(), plan, roots, nil, nil)
		second <- err
	}()
	key := plan.viewID + ":" + plan.request.sessionIdentity
	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	poll := time.NewTicker(time.Millisecond)
	defer poll.Stop()
	for {
		srv.sessionFilesCache.mu.Lock()
		producer := srv.sessionFilesCache.producers[key]
		joined := producer != nil && producer.subscribers == 2
		count := len(srv.sessionFilesCache.producers)
		srv.sessionFilesCache.mu.Unlock()
		if joined {
			if count != 1 {
				t.Fatal("cold tabs duplicated the Git producer")
			}
			break
		}
		select {
		case <-deadline.C:
			t.Fatal("second tab did not join cold producer")
		case <-poll.C:
		}
	}
	cancel()
	select {
	case err := <-first:
		if err == nil || err.Code != "cancelled" {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("disconnect blocked")
	}
	// Git can open the attributes file again after this read; later opens see a regular file.
	released := filepath.Join(t.TempDir(), "gitattributes")
	if err = os.WriteFile(released, nil, 0600); err != nil {
		t.Fatal(err)
	}
	if err = os.Rename(released, pipe); err != nil {
		t.Fatal(err)
	}
	if err = writer.Close(); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-second:
		if err != nil {
			t.Fatal("first browser cancelled shared work", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("sibling did not complete")
	}
	srv.sessionFilesCache.mu.Lock()
	bytes := srv.sessionFilesCache.producerBytes
	srv.sessionFilesCache.mu.Unlock()
	if bytes != 0 {
		t.Fatal("completed subscriber retained replay allocation", bytes)
	}
}
