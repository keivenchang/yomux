// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yoagent

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

func TestStoreImportsLegacyConversationAndUsesSQLite(t *testing.T) {
	directory := filepath.Join(t.TempDir(), "yoagent")
	legacyPath := filepath.Join(directory, "conversation.jsonl")
	if err := os.MkdirAll(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	legacy := `{"content":"old question","createdAt":"2026-01-01T00:00:00Z","role":"user"}` + "\n"
	if err := os.WriteFile(legacyPath, []byte(legacy), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := NewStore(directory)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	loaded, issues, err := store.LoadMessages(context.Background(), ConversationLimit)
	if err != nil || len(issues) != 0 || len(loaded) != 1 || loaded[0]["content"] != "old question" {
		t.Fatalf("LoadMessages = %#v, %#v, %v", loaded, issues, err)
	}
	appended, err := store.AppendMessage(context.Background(), map[string]any{"role": "assistant", "content": "answer", "createdAt": "2026-01-01T00:00:01Z"})
	if err != nil || appended["content"] != "answer" {
		t.Fatalf("AppendMessage = %#v, %v", appended, err)
	}
	loaded, issues, err = store.LoadMessages(context.Background(), ConversationLimit)
	if err != nil || len(issues) != 0 || len(loaded) != 2 || loaded[1]["content"] != "answer" {
		t.Fatalf("reloaded messages = %#v, %#v, %v", loaded, issues, err)
	}
	if filepath.Base(store.ConversationPath()) != "yoagent.sqlite3" {
		t.Fatalf("conversation path = %q, want the native database", store.ConversationPath())
	}
	if got, err := os.ReadFile(legacyPath); err != nil || string(got) != legacy {
		t.Fatalf("legacy conversation was not retained as migration evidence: %q, %v", got, err)
	}
	var count int
	if err := store.db.QueryRow(`SELECT count(*) FROM yoagent_messages`).Scan(&count); err != nil || count != 2 {
		t.Fatalf("database message count = %d, %v", count, err)
	}
}

func TestStoreReportsCorruptLegacyLinesWithoutDiscardingGoodMessages(t *testing.T) {
	directory := filepath.Join(t.TempDir(), "yoagent")
	if err := os.MkdirAll(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	data := `{"role":"user","content":"kept","createdAt":"fixed"}` + "\n" + "not-json\n"
	if err := os.WriteFile(filepath.Join(directory, "conversation.jsonl"), []byte(data), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := NewStore(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	loaded, issues, err := store.LoadMessages(context.Background(), ConversationLimit)
	if err != nil || len(loaded) != 1 || len(issues) != 1 || issues[0].Reason != "invalid_json" {
		t.Fatalf("recovery report = %#v, %#v, %v", loaded, issues, err)
	}
}

func TestStoreRejectsLegacyConversationLineWithTrailingJSON(t *testing.T) {
	directory := filepath.Join(t.TempDir(), "yoagent")
	if err := os.MkdirAll(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	line := `{"role":"user","content":"must not load"} {"role":"assistant","content":"second value"}` + "\n"
	if err := os.WriteFile(filepath.Join(directory, "conversation.jsonl"), []byte(line), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := NewStore(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	loaded, issues, err := store.LoadMessages(context.Background(), ConversationLimit)
	if err != nil || len(loaded) != 0 || len(issues) != 1 || issues[0].Reason != "invalid_json" {
		t.Fatalf("trailing conversation data = %#v, %#v, %v", loaded, issues, err)
	}
}

func TestStoreImportsLegacyCLIStateAndRoundTripsInDatabase(t *testing.T) {
	directory := filepath.Join(t.TempDir(), "yoagent")
	if err := os.MkdirAll(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	legacy := `{"claude":{"session_id":"session-a","activity_signature":"sig","updated_ts":10}}`
	if err := os.WriteFile(filepath.Join(directory, "cli-sessions.json"), []byte(legacy), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := NewStore(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	loaded, issues, err := store.LoadCLISessions(context.Background(), 123, []string{"claude", "codex"})
	if err != nil || len(issues) != 0 || loaded["claude"].SessionID != "session-a" || loaded["claude"].UpdatedMonotonic != 123 {
		t.Fatalf("LoadCLISessions = %#v, %#v, %v", loaded, issues, err)
	}
	states := map[string]CLIState{"codex": {SessionID: "session-b", ActivitySignature: "sig-b", UpdatedTS: 20}}
	if err := store.SaveCLISessions(context.Background(), states, []string{"claude", "codex"}); err != nil {
		t.Fatal(err)
	}
	loaded, issues, err = store.LoadCLISessions(context.Background(), 124, []string{"claude", "codex"})
	if err != nil || len(issues) != 0 || loaded["codex"].SessionID != "session-b" || loaded["claude"].SessionID != "" {
		t.Fatalf("saved CLI state = %#v, %#v, %v", loaded, issues, err)
	}
	var stored string
	if err := store.db.QueryRow(`SELECT session_id FROM yoagent_cli_sessions WHERE backend='codex'`).Scan(&stored); err != nil || stored != "session-b" {
		t.Fatalf("database CLI session = %q, %v", stored, err)
	}
	if err := store.ClearCLISessions(context.Background()); err != nil {
		t.Fatal(err)
	}
	loaded, issues, err = store.LoadCLISessions(context.Background(), 125, []string{"claude", "codex"})
	if err != nil || len(issues) != 0 || len(loaded) != 0 {
		t.Fatalf("cleared CLI state = %#v, %#v, %v", loaded, issues, err)
	}
}

func TestStoreRecordsCorruptLegacyCLIStateForRecovery(t *testing.T) {
	directory := filepath.Join(t.TempDir(), "yoagent")
	if err := os.MkdirAll(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "cli-sessions.json"), []byte(`{"claude":{"session_id":"one"}} {"codex":{"session_id":"two"}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := NewStore(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	loaded, issues, err := store.LoadCLISessions(context.Background(), 123, []string{"claude", "codex"})
	if err != nil || len(loaded) != 0 || len(issues) != 1 || issues[0].Reason != "invalid_json" {
		t.Fatalf("recovery state = %#v, %#v, %v", loaded, issues, err)
	}
}

func TestStoreImportsLegacyJobsAndRecoversInterruptedSend(t *testing.T) {
	directory := filepath.Join(t.TempDir(), "yoagent")
	if err := os.MkdirAll(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	statePath := filepath.Join(t.TempDir(), "state.json")
	legacy := `{"other_state":{"retained":true},"yoagent_jobs":{"job-1":{"id":"job-1","status":"firing","created_ts":10,"prompt":"token=secret"},"job-2":{"id":"job-2","status":"queued","created_ts":20}}}`
	if err := os.WriteFile(statePath, []byte(legacy), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := NewStore(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if err := store.ImportLegacyJobs(context.Background(), statePath); err != nil {
		t.Fatal(err)
	}
	if err := store.ImportLegacyJobs(context.Background(), statePath); err != nil {
		t.Fatal(err)
	}
	jobs, issues, err := store.LoadJobs(context.Background())
	if err != nil || len(issues) != 0 || len(jobs) != 2 {
		t.Fatalf("imported jobs = %#v, issues=%#v, err=%v", jobs, issues, err)
	}
	var recovered map[string]any
	for _, job := range jobs {
		if job["id"] == "job-1" {
			recovered = job
		}
	}
	if recovered == nil || recovered["status"] != "failed" || !strings.Contains(text(recovered["error"]), "interrupted while sending") {
		t.Fatalf("interrupted send was not fenced from automatic replay: %#v", recovered)
	}
	if got, err := os.ReadFile(statePath); err != nil || string(got) != legacy {
		t.Fatalf("legacy state file changed during import: %q, %v", got, err)
	}
}

func TestStoreJobTransitionsUseExpectedStatusAndResetBacksUpJobs(t *testing.T) {
	store, err := NewStore(filepath.Join(t.TempDir(), "yoagent"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	job := map[string]any{"id": "job-1", "status": "pending_confirmation", "created_ts": 10.0, "idempotency_key": "key", "prompt": "token=secret"}
	if err := store.SaveJob(context.Background(), job); err != nil {
		t.Fatal(err)
	}
	job["status"] = "queued"
	changed, err := store.TransitionJob(context.Background(), "job-1", []string{"pending_confirmation"}, job)
	if err != nil || !changed {
		t.Fatalf("job transition changed=%v err=%v", changed, err)
	}
	job["status"] = "fired"
	changed, err = store.TransitionJob(context.Background(), "job-1", []string{"pending_confirmation"}, job)
	if err != nil || changed {
		t.Fatalf("stale transition changed=%v err=%v", changed, err)
	}
	backups, err := store.Reset(context.Background())
	if err != nil || len(backups) != 1 {
		t.Fatalf("reset backups=%v err=%v", backups, err)
	}
	data, err := os.ReadFile(backups[0])
	if err != nil || !strings.Contains(string(data), "job-1") || !strings.Contains(string(data), "token=secret") {
		t.Fatalf("job reset backup=%s err=%v", data, err)
	}
	jobs, issues, err := store.LoadJobs(context.Background())
	if err != nil || len(jobs) != 0 || len(issues) != 0 {
		t.Fatalf("post-reset jobs=%#v issues=%#v err=%v", jobs, issues, err)
	}
}

func TestStoreRejectsLegacySymlinkAndHonorsCanceledDatabaseContext(t *testing.T) {
	root := t.TempDir()
	directory := filepath.Join(root, "yoagent")
	if err := os.MkdirAll(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(root, "outside.jsonl")
	if err := os.WriteFile(outside, []byte("preserve\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(directory, "conversation.jsonl")); err != nil {
		t.Fatal(err)
	}
	if _, err := NewStore(directory); err == nil {
		t.Fatal("legacy import followed a conversation symlink")
	}
	if got, err := os.ReadFile(outside); err != nil || string(got) != "preserve\n" {
		t.Fatalf("symlink target changed: %q, %v", got, err)
	}
	if err := os.Remove(filepath.Join(directory, "conversation.jsonl")); err != nil {
		t.Fatal(err)
	}
	store, err := NewStore(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := store.AppendMessage(ctx, map[string]any{"role": "user", "content": "must not write"}); !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled insert error = %v", err)
	}
	if strings.TrimSpace(store.ConversationPath()) == "" {
		t.Fatal("database path was empty")
	}
}

func TestStoreResetBacksUpAndClearsDatabaseState(t *testing.T) {
	store, err := NewStore(filepath.Join(t.TempDir(), "yoagent"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	for index := range ConversationLimit + 1 {
		content := "message-" + strconv.Itoa(index)
		if index == 0 {
			content = "keep in backup"
		}
		if _, err := store.AppendMessage(context.Background(), map[string]any{"role": "user", "content": content}); err != nil {
			t.Fatal(err)
		}
	}
	if err := store.SaveCLISessions(context.Background(), map[string]CLIState{"codex": {SessionID: "resume"}}, []string{"codex"}); err != nil {
		t.Fatal(err)
	}
	backups, err := store.Reset(context.Background())
	if err != nil || len(backups) != 1 {
		t.Fatalf("reset backups = %v, %v", backups, err)
	}
	data, err := os.ReadFile(backups[0])
	var backup map[string]any
	if err != nil || json.Unmarshal(data, &backup) != nil || !strings.Contains(string(data), "keep in backup") || !strings.Contains(string(data), "resume") {
		t.Fatalf("reset backup = %s, %v", data, err)
	}
	if messages, ok := backup["messages"].([]any); !ok || len(messages) != ConversationLimit+1 {
		t.Fatalf("reset backup contains %d conversation messages, want %d", len(messages), ConversationLimit+1)
	}
	messages, issues, err := store.LoadMessages(context.Background(), ConversationLimit)
	if err != nil || len(messages) != 0 || len(issues) != 0 {
		t.Fatalf("post-reset conversation = %#v, %#v, %v", messages, issues, err)
	}
}

func TestStoreLegacyMigrationIsIdempotentAndOriginalsRemainAvailable(t *testing.T) {
	directory := filepath.Join(t.TempDir(), "yoagent")
	if err := os.MkdirAll(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	legacy := `{"role":"user","content":"once"}` + "\n"
	legacyPath := filepath.Join(directory, "conversation.jsonl")
	if err := os.WriteFile(legacyPath, []byte(legacy), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := NewStore(directory)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.AppendMessage(context.Background(), map[string]any{"role": "assistant", "content": "native"}); err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	store, err = NewStore(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	messages, _, err := store.LoadMessages(context.Background(), ConversationLimit)
	if err != nil || len(messages) != 2 || messages[0]["content"] != "once" || messages[1]["content"] != "native" {
		t.Fatalf("restart imported legacy data more than once: %#v, %v", messages, err)
	}
	if got, err := os.ReadFile(legacyPath); err != nil || string(got) != legacy {
		t.Fatalf("legacy file changed during migration: %q, %v", got, err)
	}
	var version int
	if err := store.db.QueryRow(`PRAGMA user_version`).Scan(&version); err != nil {
		t.Fatal(err)
	}
	if version != databaseVersion {
		t.Fatalf("database version = %d, want %d", version, databaseVersion)
	}
}
