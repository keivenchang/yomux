// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"

	"github.com/bazelbuild/rules_go/go/runfiles"
)

func openTestStore(t *testing.T, path string, clock func() float64) *Store {
	t.Helper()
	store, err := OpenStore(context.Background(), path, StoreOptions{Clock: clock})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := store.Close(); err != nil {
			t.Error(err)
		}
	})
	return store
}

func TestStoreOriginalPythonContract(t *testing.T) {
	path, err := runfiles.Rlocation("yomux/tests/fixtures/chat_store_contract.json")
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Steps []struct {
			Now             float64         `json:"now"`
			Method          string          `json:"method"`
			Arguments       json.RawMessage `json:"arguments"`
			Result          json.RawMessage `json:"result"`
			ValidationError string          `json:"validation_error"`
		} `json:"steps"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Steps) != 55 {
		t.Fatalf("contract steps=%d want 55", len(fixture.Steps))
	}
	now := fixture.Steps[0].Now
	store := openTestStore(t, filepath.Join(t.TempDir(), "chat.sqlite3"), func() float64 { return now })
	ctx := context.Background()
	for i, step := range fixture.Steps {
		t.Run(fmt.Sprintf("%02d_%s", i, step.Method), func(t *testing.T) {
			now = step.Now
			var args struct {
				Message
				BrowserInstance       string   `json:"browser_instance_id"`
				Timestamp             *float64 `json:"created_at_utc"`
				BeforeID              *int64   `json:"before_id"`
				AfterID               int64    `json:"after_id"`
				MessageID             int64    `json:"message_id"`
				Cursor                *int64   `json:"cursor"`
				Query                 string   `json:"query"`
				Limit                 *int     `json:"limit"`
				RetentionDays         *int     `json:"retention_days"`
				HardRowLimit          *int     `json:"hard_row_limit"`
				PreviousRetentionDays *int     `json:"previous_retention_days"`
				Before                *int     `json:"before"`
				After                 *int     `json:"after"`
				Typing                bool     `json:"typing"`
				Key                   string   `json:"key"`
				Value                 string   `json:"value"`
			}
			if err := json.Unmarshal(step.Arguments, &args); err != nil {
				t.Fatal(err)
			}
			options := PageOptions{Limit: args.Limit, RetentionDays: args.RetentionDays}
			var actual any
			var err error
			switch step.Method {
			case "test_set_metadata":
				_, err = store.db.ExecContext(ctx, `INSERT INTO chat_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value`, args.Key, args.Value)
			case "insert_message":
				message, created, insertErr := store.Insert(ctx, NewMessage{Message: args.Message, Timestamp: args.Timestamp})
				actual, err = []any{message, created}, insertErr
			case "bootstrap":
				actual, err = store.Bootstrap(ctx, args.Room, args.Username, options)
			case "read_up_to":
				actual, err = store.ReadUpTo(ctx, args.Room, args.Username, args.MessageID)
			case "set_typing":
				actual, err = store.SetTyping(ctx, args.Room, args.Username, args.BrowserInstance, args.Typing)
			case "typing_snapshot":
				actual, err = store.TypingSnapshot(ctx, args.Room)
			case "page_before":
				actual, err = store.PageBefore(ctx, args.Room, args.BeforeID, options)
			case "messages_after":
				actual, err = store.MessagesAfter(ctx, args.Room, args.AfterID, options)
			case "context":
				actual, err = store.Context(ctx, args.Room, args.MessageID, args.Before, args.After, args.RetentionDays)
			case "search":
				actual, err = store.Search(ctx, args.Room, args.Query, args.Cursor, args.Limit, nil, nil, args.RetentionDays)
			case "prune":
				actual, err = store.Prune(ctx, args.RetentionDays, args.HardRowLimit)
			case "prune_if_due":
				actual, err = store.PruneIfDue(ctx, args.RetentionDays, args.PreviousRetentionDays)
			default:
				t.Fatalf("unknown contract method %q", step.Method)
			}
			if step.ValidationError != "" {
				var validation *ValidationError
				if !errors.As(err, &validation) || validation.Reason != step.ValidationError {
					t.Fatalf("error=%v want validation reason %q", err, step.ValidationError)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			assertContractJSON(t, actual, step.Result)
		})
	}
}

func TestStoreMakesExistingParentPrivateBeforeSQLite(t *testing.T) {
	parent := filepath.Join(t.TempDir(), "existing-state")
	if err := os.Mkdir(parent, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(parent, 0755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(parent, "chat.sqlite3")
	openTestStore(t, path, func() float64 { return 1700000000 })
	for name, want := range map[string]os.FileMode{parent: 0700, path: 0600, path + "-wal": 0600, path + "-shm": 0600} {
		info, err := os.Stat(name)
		if os.IsNotExist(err) && strings.HasPrefix(name, path+"-") {
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		if info.Mode().Perm() != want {
			t.Errorf("%s permissions=%04o want %04o", filepath.Base(name), info.Mode().Perm(), want)
		}
	}
}

func pythonSchema(t *testing.T) []string {
	t.Helper()
	path, err := runfiles.Rlocation("yomux/tests/fixtures/chat_schema_contract.json")
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Schema []string `json:"schema"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Schema) != 10 {
		t.Fatal("incomplete original schema fixture")
	}
	return fixture.Schema
}

func TestStoreMigratesOriginalSchemasWithConsistentBackup(t *testing.T) {
	for _, version := range []int{1, 2, 3} {
		t.Run(fmt.Sprint(version), func(t *testing.T) {
			ctx := context.Background()
			path := filepath.Join(t.TempDir(), "chat.sqlite3")
			db, err := sql.Open("sqlite3", sqliteURI(path, false))
			if err != nil {
				t.Fatal(err)
			}
			for _, statement := range pythonSchema(t) {
				if _, err := db.Exec(statement); err != nil {
					t.Fatal(err)
				}
			}
			if version < 3 {
				if _, err := db.Exec(`DROP TABLE chat_read_cursors; CREATE TABLE chat_read_cursors(room_id TEXT NOT NULL,username TEXT NOT NULL,reader_id TEXT NOT NULL,read_up_to_id INTEGER NOT NULL,updated_at_utc REAL NOT NULL,PRIMARY KEY(room_id,username,reader_id)); INSERT INTO chat_read_cursors VALUES('global','alice','a',4,10),('global','alice','b',9,20)`); err != nil {
					t.Fatal(err)
				}
			}
			if version == 1 {
				if _, err := db.Exec(`ALTER TABLE chat_messages DROP COLUMN sender_ip`); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := db.Exec(`INSERT INTO chat_messages(created_at_utc,room_id,username,sender_instance_id,client_message_uuid,body,is_question) VALUES(1,'global','guest','browser','old','Original café',0)`); err != nil {
				t.Fatal(err)
			}
			if _, err := db.Exec(fmt.Sprintf("PRAGMA user_version=%d", version)); err != nil {
				t.Fatal(err)
			}
			if err := db.Close(); err != nil {
				t.Fatal(err)
			}
			store := openTestStore(t, path, func() float64 { return 2 })
			page, err := store.PageBefore(ctx, "", nil, PageOptions{})
			if err != nil || len(page.Messages) != 1 || page.Messages[0].Body != "Original café" || page.Messages[0].SenderIP != "" {
				t.Fatalf("migrated page=%+v err=%v", page, err)
			}
			if version < 3 {
				bootstrap, err := store.Bootstrap(ctx, "", "alice", PageOptions{})
				if err != nil || bootstrap.ReadCursor.ReadUpTo != 9 || bootstrap.ReadCursor.UpdatedAt != 20 {
					t.Fatalf("cursor migration=%+v err=%v", bootstrap.ReadCursor, err)
				}
			}
			backups, err := filepath.Glob(path + ".pre-native-chat-*/" + filepath.Base(path))
			if err != nil || len(backups) != 1 {
				t.Fatalf("backups=%v err=%v", backups, err)
			}
			backup, err := sql.Open("sqlite3", sqliteURI(backups[0], true))
			if err != nil {
				t.Fatal(err)
			}
			var originalVersion, count int
			if err := backup.QueryRow("PRAGMA user_version").Scan(&originalVersion); err != nil {
				t.Fatal(err)
			}
			if err := backup.QueryRow("SELECT count(*) FROM chat_messages").Scan(&count); err != nil {
				t.Fatal(err)
			}
			if err := backup.Close(); err != nil {
				t.Fatal(err)
			}
			if originalVersion != version || count != 1 {
				t.Fatalf("backup version=%d rows=%d", originalVersion, count)
			}
			if err := store.Close(); err != nil {
				t.Fatal(err)
			}
			openTestStore(t, path, func() float64 { return 2 })
			after, err := filepath.Glob(path + ".pre-native-chat-*/" + filepath.Base(path))
			if err != nil || !reflect.DeepEqual(after, backups) {
				t.Fatalf("reopen unexpectedly changed backups: %v err=%v", after, err)
			}
		})
	}
}

func TestStoreIndependentWritersKeepRetriesAndJournalsIdempotent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "chat.sqlite3")
	const messages = 24
	var group sync.WaitGroup
	errorsFound := make(chan error, 4)
	for range 2 {
		group.Go(func() {
			store, err := OpenStore(context.Background(), path, StoreOptions{Clock: func() float64 { return 1700000000 }})
			if err != nil {
				errorsFound <- err
				return
			}
			defer func() { errorsFound <- store.Close() }()
			for index := range messages {
				_, _, err := store.Insert(context.Background(), NewMessage{Message: Message{MessagePayload: MessagePayload{Username: "alice", SenderInstance: "browser", ClientUUID: fmt.Sprint(index), Body: fmt.Sprintf("message %d 😀", index)}}})
				if err != nil {
					errorsFound <- err
					return
				}
			}
		})
	}
	group.Wait()
	close(errorsFound)
	for err := range errorsFound {
		if err != nil {
			t.Fatal(err)
		}
	}
	store := openTestStore(t, path, func() float64 { return 1700000000 })
	page, err := store.PageBefore(context.Background(), "", nil, PageOptions{Limit: testInt(200)})
	if err != nil || len(page.Messages) != messages {
		t.Fatalf("messages=%d err=%v", len(page.Messages), err)
	}
	for index, message := range page.Messages {
		if message.ID != int64(index+1) {
			t.Fatalf("retry consumed sequence: index=%d id=%d", index, message.ID)
		}
	}
	lines, ids, err := readHistory(filepath.Join(store.historyDir, "2023-11-14.jsonl"))
	if err != nil || len(lines) != messages || len(ids) != messages {
		t.Fatalf("journal lines=%d ids=%d err=%v", len(lines), len(ids), err)
	}
}

func TestStoreRejectsIncompatibleWithoutChangingDatabase(t *testing.T) {
	for _, kind := range []string{"future", "unversioned", "corrupt"} {
		t.Run(kind, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "chat.sqlite3")
			if kind == "corrupt" {
				if err := os.WriteFile(path, []byte("not a database"), 0600); err != nil {
					t.Fatal(err)
				}
			} else {
				db, err := sql.Open("sqlite3", sqliteURI(path, false))
				if err != nil {
					t.Fatal(err)
				}
				statement := "PRAGMA user_version=4"
				if kind == "unversioned" {
					statement = "CREATE TABLE unrelated(value TEXT)"
				}
				if _, err := db.Exec(statement); err != nil {
					t.Fatal(err)
				}
				if err := db.Close(); err != nil {
					t.Fatal(err)
				}
			}
			before, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if store, err := OpenStore(context.Background(), path, StoreOptions{}); err == nil {
				store.Close()
				t.Fatal("incompatible source accepted")
			}
			after, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(before, after) {
				t.Fatal("rejected database was modified")
			}
		})
	}
}

func TestStoreBackfillKeepsJournalOnlyRecoveryData(t *testing.T) {
	path := filepath.Join(t.TempDir(), "chat.sqlite3")
	store := openTestStore(t, path, func() float64 { return 1700000000 })
	if _, _, err := store.Insert(context.Background(), NewMessage{Message: Message{MessagePayload: MessagePayload{Username: "alice", SenderInstance: "browser", ClientUUID: "original", Body: "committed"}}}); err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	journal := filepath.Join(store.historyDir, "2023-11-14.jsonl")
	recovery := `{"version":1,"id":999,"created_at_utc":1700000000,"body":"recovery-only"}` + "\nmalformed recovery evidence\n"
	if err := os.WriteFile(journal, []byte(recovery), 0600); err != nil {
		t.Fatal(err)
	}
	openTestStore(t, path, func() float64 { return 1700000000 })
	raw, err := os.ReadFile(journal)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(string(raw), recovery) {
		t.Fatal("journal-only recovery lines were rewritten")
	}
	lines, ids, err := readHistory(journal)
	if err != nil || len(lines) != 3 || !ids[1] || !ids[999] {
		t.Fatalf("backfill lines=%d ids=%d err=%v", len(lines), len(ids), err)
	}
}

func TestStoreExactRetentionCutoff(t *testing.T) {
	now := float64(1700000000)
	store := openTestStore(t, filepath.Join(t.TempDir(), "chat.sqlite3"), func() float64 { return now })
	for index, timestamp := range []float64{now - 7*86400 - 1, now - 7*86400, now} {
		if _, _, err := store.Insert(context.Background(), NewMessage{Message: Message{MessagePayload: MessagePayload{Username: "alice", SenderInstance: "browser", ClientUUID: fmt.Sprint(index), Body: "retention"}}, Timestamp: &timestamp}); err != nil {
			t.Fatal(err)
		}
	}
	result, err := store.Prune(context.Background(), testInt(7), nil)
	if err != nil || result.DeletedExpired != 1 || result.RemainingRows != 2 {
		t.Fatalf("prune=%+v err=%v", result, err)
	}
	_, ids, err := readHistory(filepath.Join(store.historyDir, historyDay(now-7*86400)+".jsonl"))
	if err != nil || ids[1] || !ids[2] {
		t.Fatalf("cutoff history ids=%v err=%v", ids, err)
	}
}

func TestStoreUnicodeByteLimitAndJournalRepair(t *testing.T) {
	ctx := context.Background()
	store := openTestStore(t, filepath.Join(t.TempDir(), "chat.sqlite3"), func() float64 { return 1700000000 })
	input := NewMessage{Message: Message{MessagePayload: MessagePayload{Username: "alice", SenderInstance: "browser", ClientUUID: "original", Body: strings.Repeat("😀", 2048)}}}
	blockedJournal := filepath.Join(store.historyDir, "2023-11-14.jsonl")
	if err := os.Mkdir(blockedJournal, 0700); err != nil {
		t.Fatal(err)
	}
	if _, _, err := store.Insert(ctx, input); err == nil {
		t.Fatal("journal failure was hidden")
	}
	if err := os.Remove(blockedJournal); err != nil {
		t.Fatal(err)
	}
	message, created, err := store.Insert(ctx, input)
	if err != nil || created || message.ID != 1 || message.Body != input.Body {
		t.Fatalf("repair created=%v id=%d err=%v", created, message.ID, err)
	}
	input.ClientUUID = "oversized"
	input.Body += "😀"
	if _, _, err := store.Insert(ctx, input); err == nil {
		t.Fatal("oversized Unicode body accepted")
	}
	lines, ids, err := readHistory(blockedJournal)
	if err != nil || len(lines) != 1 || !ids[1] {
		t.Fatalf("repaired journal lines=%d err=%v", len(lines), err)
	}
}

func testInt(value int) *int { return &value }

func TestStorePruneRepairsOnlyInvalidSchedulingMetadata(t *testing.T) {
	store := openTestStore(t, filepath.Join(t.TempDir(), "chat.sqlite3"), func() float64 { return 1700000000 })
	ctx := context.Background()
	for _, value := range []string{"broken", "", "NaN", "+Inf"} {
		if _, err := store.db.Exec(`INSERT INTO chat_metadata(key,value) VALUES('last_prune_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value`, value); err != nil {
			t.Fatal(err)
		}
		result, err := store.PruneIfDue(ctx, nil, nil)
		if err != nil || !result.Ran || result.RecoveryReason != "invalid_prune_timestamp" {
			t.Fatalf("repair result=%+v err=%v", result, err)
		}
	}
	if _, err := store.db.Exec(`DROP TABLE chat_metadata`); err != nil {
		t.Fatal(err)
	}
	if _, err := store.PruneIfDue(ctx, nil, nil); err == nil {
		t.Fatal("SQL failure was hidden as scheduling repair")
	}
}

func assertContractJSON(t *testing.T, actual any, expected json.RawMessage) {
	t.Helper()
	encoded, err := json.Marshal(actual)
	if err != nil {
		t.Fatal(err)
	}
	var got, want any
	if err := json.Unmarshal(encoded, &got); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(expected, &want); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("contract differs\ngot: %s\nwant: %s", encoded, expected)
	}
}
