// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"database/sql"
	"path/filepath"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

// Existing chat databases use external-content FTS5 tables and write triggers.
// Merely substituting LIKE for search would leave every insert failing.
func TestSQLiteSupportsExistingChatFTS5Triggers(t *testing.T) {
	db, err := sql.Open("sqlite3", filepath.Join(t.TempDir(), "chat.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := db.Close(); err != nil {
			t.Error(err)
		}
	})
	for _, statement := range []string{
		`CREATE TABLE chat_messages(id INTEGER PRIMARY KEY, body TEXT NOT NULL)`,
		`CREATE VIRTUAL TABLE chat_messages_fts USING fts5(body, content='chat_messages', content_rowid='id', tokenize='unicode61')`,
		`CREATE TRIGGER chat_messages_fts_insert AFTER INSERT ON chat_messages BEGIN
            INSERT INTO chat_messages_fts(rowid, body) VALUES (new.id, new.body); END`,
		`CREATE TRIGGER chat_messages_fts_delete AFTER DELETE ON chat_messages BEGIN
            INSERT INTO chat_messages_fts(chat_messages_fts, rowid, body) VALUES ('delete', old.id, old.body); END`,
		`CREATE TRIGGER chat_messages_fts_update AFTER UPDATE OF body ON chat_messages BEGIN
            INSERT INTO chat_messages_fts(chat_messages_fts, rowid, body) VALUES ('delete', old.id, old.body);
            INSERT INTO chat_messages_fts(rowid, body) VALUES (new.id, new.body); END`,
		`INSERT INTO chat_messages VALUES (1, 'Original café message')`,
	} {
		if _, err := db.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
	assertSearch := func(query string, want int) {
		t.Helper()
		var count int
		if err := db.QueryRow(`SELECT count(*) FROM chat_messages_fts WHERE chat_messages_fts MATCH ?`, query).Scan(&count); err != nil {
			t.Fatal(err)
		}
		if count != want {
			t.Fatalf("search count=%d want %d", count, want)
		}
	}
	assertSearch("cafe", 1)
	if _, err := db.Exec(`UPDATE chat_messages SET body='Replacement text' WHERE id=1`); err != nil {
		t.Fatal(err)
	}
	assertSearch("cafe", 0)
	assertSearch("replacement", 1)
	if _, err := db.Exec(`DELETE FROM chat_messages WHERE id=1`); err != nil {
		t.Fatal(err)
	}
	assertSearch("replacement", 0)
}
