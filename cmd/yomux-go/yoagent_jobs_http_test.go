// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/keivenchang/yomux/internal/yoagent"
)

func TestNativeYOAgentJobRoutesPersistDeduplicateConfirmAndCancel(t *testing.T) {
	store, err := yoagent.NewStore(filepath.Join(t.TempDir(), "yoagent"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	s := &server{
		yoagentState: store, events: newEventBroker(),
		tmuxMetadataState: tmuxMetadataState{tmuxSessionRosterFn: func() ([]string, error) { return []string{"agent-a", "agent-b"}, nil }},
	}
	admin := func(request *http.Request) *http.Request {
		return request.WithContext(context.WithValue(request.Context(), authContextKey{}, authUser{username: "operator", role: "admin"}))
	}
	create := func(body string) *httptest.ResponseRecorder {
		record := httptest.NewRecorder()
		s.yoagentJobMutation(record, admin(httptest.NewRequest(http.MethodPost, "/api/yoagent/jobs", strings.NewReader(body))))
		return record
	}
	first := create(`{"type":"notify_session_idle","session":"agent-a"}`)
	if first.Code != http.StatusOK {
		t.Fatalf("create response = %d %s", first.Code, first.Body.String())
	}
	var created struct {
		Job map[string]any `json:"job"`
	}
	if err := json.Unmarshal(first.Body.Bytes(), &created); err != nil {
		t.Fatal(err)
	}
	firstID, _ := created.Job["id"].(string)
	if firstID == "" || created.Job["status"] != "queued" {
		t.Fatalf("created job = %#v", created.Job)
	}
	duplicate := create(`{"type":"notify_session_idle","session":"agent-a"}`)
	if duplicate.Code != http.StatusConflict || !strings.Contains(duplicate.Body.String(), `"duplicate":true`) {
		t.Fatalf("duplicate response = %d %s", duplicate.Code, duplicate.Body.String())
	}

	send := create(`{"type":"wait_then_send","session":"agent-b","text":"inspect status","requires_confirmation":true}`)
	if send.Code != http.StatusOK {
		t.Fatalf("send job response = %d %s", send.Code, send.Body.String())
	}
	var sendResult struct {
		Job map[string]any `json:"job"`
	}
	if err := json.Unmarshal(send.Body.Bytes(), &sendResult); err != nil {
		t.Fatal(err)
	}
	sendID, _ := sendResult.Job["id"].(string)
	if sendID == "" || sendResult.Job["status"] != "pending_confirmation" {
		t.Fatalf("send job was not confirmation-gated: %#v", sendResult.Job)
	}
	confirm := httptest.NewRecorder()
	s.yoagentJobMutation(confirm, admin(httptest.NewRequest(http.MethodPost, "/api/yoagent/jobs/"+sendID+"/confirm", strings.NewReader(`{}`))))
	if confirm.Code != http.StatusOK || !strings.Contains(confirm.Body.String(), `"status":"queued"`) {
		t.Fatalf("confirm response = %d %s", confirm.Code, confirm.Body.String())
	}
	cancel := httptest.NewRecorder()
	s.yoagentJobsCancelSession(cancel, admin(httptest.NewRequest(http.MethodPost, "/api/yoagent/jobs/cancel-session", strings.NewReader(`{"session":"agent-b"}`))))
	if cancel.Code != http.StatusOK || !strings.Contains(cancel.Body.String(), `"count":1`) {
		t.Fatalf("cancel-session response = %d %s", cancel.Code, cancel.Body.String())
	}
	job, err := store.LoadJob(context.Background(), sendID)
	if err != nil || job["status"] != "cancelled" {
		t.Fatalf("persisted cancelled job = %#v, %v", job, err)
	}
}
