// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
	"github.com/keivenchang/yomux/internal/work"
)

func TestFinderDeleteEscalationPreservesAuthorizedGeneration(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(root, "target")
	create := func(path, content string) {
		t.Helper()
		if err := os.Mkdir(path, 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(path, "child"), []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	create(target, "original")
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	point, bulk := work.New(1, 2), work.New(1, 2)
	defer point.Close()
	defer bulk.Close()
	started, release := make(chan struct{}), make(chan struct{})
	if _, err := bulk.Submit(context.Background(), "held-bulk", func(ctx context.Context) (any, error) {
		close(started)
		select {
		case <-release:
		case <-ctx.Done():
		}
		return nil, nil
	}); err != nil {
		t.Fatal(err)
	}
	<-started
	s := &server{root: root, finder: service, events: newEventBroker(), finderJobs: point, finderBulkJobs: bulk, operationState: operationState{operations: map[string]*goOperation{}}}
	response := httptest.NewRecorder()
	body, err := json.Marshal(map[string]string{"path": target})
	if err != nil {
		t.Fatal(err)
	}
	s.fsDelete(response, httptest.NewRequest(http.MethodPost, "/api/fs/delete", bytes.NewReader(body)))
	if response.Code != http.StatusAccepted {
		t.Fatalf("delete admission: %d %s", response.Code, response.Body.String())
	}
	var receipt struct {
		Operation struct{ ID string }
	}
	if err := json.Unmarshal(response.Body.Bytes(), &receipt); err != nil {
		t.Fatal(err)
	}
	// FIFO completion proves the bounded rmdir has restored the original name.
	// The held bulk worker prevents the recursive continuation from consuming it.
	afterPoint, err := point.Submit(context.Background(), "after-delete", func(context.Context) (any, error) { return nil, nil })
	if err != nil {
		t.Fatal(err)
	}
	if completed := <-afterPoint; completed.Err != nil {
		t.Fatal(completed.Err)
	}
	preserved := target + "-original"
	if err := os.Rename(target, preserved); err != nil {
		t.Fatal(err)
	}
	create(target, "replacement must survive")
	close(release)
	waitFixtureCondition(t, "delete terminal receipt", 2*time.Second, func() bool {
		record := s.operationRecord(receipt.Operation.ID)
		return record != nil && record.TerminalEvent != nil
	})
	for path, want := range map[string]string{preserved: "original", target: "replacement must survive"} {
		got, err := os.ReadFile(filepath.Join(path, "child"))
		if err != nil || string(got) != want {
			t.Fatalf("delete crossed namespace generations: path=%s content=%q error=%v", path, got, err)
		}
	}
	if result := s.operationRecord(receipt.Operation.ID).Result; result["state"] != "failed" {
		t.Fatalf("replaced target must produce a typed failure: %#v", result)
	}
}
