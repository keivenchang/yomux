// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
)

func TestFinderCreateOnlyWritePreservesExistingEntry(t *testing.T) {
	for _, kind := range []string{"new", "file", "symlink"} {
		t.Run(kind, func(t *testing.T) {
			root := t.TempDir()
			path := filepath.Join(root, "note.txt")
			target := filepath.Join(root, "target.txt")
			if err := os.WriteFile(target, []byte("valuable"), 0600); err != nil {
				t.Fatal(err)
			}
			if kind == "file" {
				if err := os.WriteFile(path, []byte("valuable"), 0600); err != nil {
					t.Fatal(err)
				}
			} else if kind == "symlink" {
				if err := os.Symlink(target, path); err != nil {
					t.Fatal(err)
				}
			}
			service, err := finder.New(root, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer service.Close()
			scheduler := newFinderJobScheduler()
			defer scheduler.Close()
			s := &server{root: root, finder: service, events: newEventBroker(), finderJobs: scheduler, operationState: operationState{operations: map[string]*goOperation{}, operationsPath: filepath.Join(root, "ops.json"), operationsJournal: filepath.Join(root, "ops.jsonl")}}
			body, err := json.Marshal(map[string]any{"path": path, "content": "", "create_only": true})
			if err != nil {
				t.Fatal(err)
			}
			response := httptest.NewRecorder()
			s.fsWrite(response, httptest.NewRequest(http.MethodPost, "/api/fs/write", bytes.NewReader(body)))
			if response.Code != http.StatusAccepted {
				t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
			}
			var envelope struct {
				Operation struct {
					ID string `json:"id"`
				} `json:"operation"`
			}
			if err := json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
				t.Fatal(err)
			}
			deadline := time.After(2 * time.Second)
			for {
				op := s.operationRecord(envelope.Operation.ID)
				if op != nil && operationIsTerminal(op) {
					if kind == "new" {
						if op.State != "ready" {
							t.Fatalf("create state=%s result=%#v", op.State, op.Result)
						}
					} else {
						if op.State != "failed" {
							t.Fatalf("duplicate create state=%s result=%#v", op.State, op.Result)
						}
						content, err := os.ReadFile(path)
						if err != nil || string(content) != "valuable" {
							t.Fatalf("content=%q err=%v", content, err)
						}
						if kind == "symlink" {
							if _, err := os.Readlink(path); err != nil {
								t.Fatal(err)
							}
						}
					}
					return
				}
				select {
				case <-deadline:
					t.Fatal("operation did not finish")
				case <-time.After(time.Millisecond):
				}
			}
		})
	}
}
