// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/keivenchang/yomux/internal/atomicfile"
	"github.com/keivenchang/yomux/internal/mutablefs"
	_ "github.com/mattn/go-sqlite3"
)

const SchemaVersion = 3

// Store owns connections to one host-partitioned chat database. It is not
// enabled by a capability until the service and HTTP contracts are migrated.
type Store struct {
	db         *sql.DB
	path       string
	historyDir string
	clock      func() float64
}

type StoreOptions struct {
	Clock      func() float64
	HistoryDir string
}

type ValidationError struct{ Reason string }

func (e *ValidationError) Error() string { return e.Reason }

func sqliteURI(path string, readOnly bool) string {
	location := url.URL{Scheme: "file", Path: filepath.ToSlash(path)}
	query := url.Values{"_busy_timeout": {"5000"}, "_foreign_keys": {"on"}, "_txlock": {"immediate"}}
	if readOnly {
		query.Set("mode", "ro")
	}
	location.RawQuery = query.Encode()
	return location.String()
}

func OpenStore(ctx context.Context, path string, options StoreOptions) (_ *Store, err error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if info, err := os.Lstat(path); err == nil && !info.Mode().IsRegular() {
		return nil, errors.New("chat database must be a regular file")
	} else if err != nil && !os.IsNotExist(err) {
		return nil, err
	}
	path, err = mutablefs.Canonical(path)
	if err != nil {
		return nil, err
	}
	if err := mutablefs.RequireLocal(path); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return nil, err
	}
	// Tighten an existing state directory before opening SQLite. Sidecars
	// inherit the main file's mode, so publication must already be private.
	if err := os.Chmod(filepath.Dir(path), 0700); err != nil {
		return nil, err
	}
	lease, err := atomicfile.AcquireLock(ctx, filepath.Join(filepath.Dir(path), "."+filepath.Base(path)+".native-init.lock"))
	if err != nil {
		return nil, err
	}
	if err := privateDatabaseFiles(path); err != nil {
		return nil, errors.Join(err, lease.Close())
	}
	db, err := sql.Open("sqlite3", sqliteURI(path, false))
	if err != nil {
		return nil, errors.Join(err, lease.Close())
	}
	defer func() {
		err = errors.Join(err, lease.Close())
		if err != nil {
			err = errors.Join(err, db.Close())
		}
	}()
	clock := options.Clock
	if clock == nil {
		clock = func() float64 { return float64(time.Now().UnixNano()) / 1e9 }
	}
	history := options.HistoryDir
	if history == "" {
		history = filepath.Join(filepath.Dir(path), strings.TrimSuffix(filepath.Base(path), filepath.Ext(path))+"-history")
	}
	store := &Store{db: db, path: path, historyDir: history, clock: clock}
	if err = store.initialize(ctx); err != nil {
		return nil, fmt.Errorf("initialize chat database: %w", err)
	}
	return store, nil
}

func (s *Store) Close() error { return s.db.Close() }

func privateDatabaseFiles(path string) error {
	for _, suffix := range []string{"", "-wal", "-shm"} {
		flags := os.O_RDWR | syscall.O_NOFOLLOW
		if suffix == "" {
			flags |= os.O_CREATE
		}
		file, err := os.OpenFile(path+suffix, flags, 0600)
		if suffix != "" && os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return err
		}
		info, err := file.Stat()
		if err != nil {
			return errors.Join(err, file.Close())
		}
		if !info.Mode().IsRegular() {
			return errors.Join(errors.New("chat database files must be regular"), file.Close())
		}
		if err := errors.Join(file.Chmod(0600), file.Close()); err != nil {
			return err
		}
	}
	return nil
}

const baseSchema = `
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at_utc REAL NOT NULL,
    room_id TEXT NOT NULL,
    username TEXT NOT NULL,
    sender_ip TEXT NOT NULL DEFAULT '',
    sender_instance_id TEXT NOT NULL,
    client_message_uuid TEXT NOT NULL,
    body TEXT NOT NULL,
    is_question INTEGER NOT NULL CHECK (is_question IN (0, 1)),
    UNIQUE (room_id, username, sender_instance_id, client_message_uuid)
);
CREATE INDEX IF NOT EXISTS chat_messages_room_time ON chat_messages(room_id, created_at_utc, id);
CREATE TABLE IF NOT EXISTS chat_read_cursors (
    room_id TEXT NOT NULL,
    username TEXT NOT NULL,
    read_up_to_id INTEGER NOT NULL DEFAULT 0,
    updated_at_utc REAL NOT NULL,
    PRIMARY KEY (room_id, username)
);
CREATE TABLE IF NOT EXISTS chat_typing_leases (
    room_id TEXT NOT NULL,
    username TEXT NOT NULL,
    browser_instance_id TEXT NOT NULL,
    expires_at_utc REAL NOT NULL,
    updated_at_utc REAL NOT NULL,
    PRIMARY KEY (room_id, username, browser_instance_id)
);
CREATE INDEX IF NOT EXISTS chat_typing_room_expiry ON chat_typing_leases(room_id, expires_at_utc);
CREATE TABLE IF NOT EXISTS chat_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
`

const searchSchema = `
CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
    body, content='chat_messages', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS chat_messages_fts_insert AFTER INSERT ON chat_messages BEGIN
    INSERT INTO chat_messages_fts(rowid, body) VALUES (new.id, new.body);
END;
CREATE TRIGGER IF NOT EXISTS chat_messages_fts_delete AFTER DELETE ON chat_messages BEGIN
    INSERT INTO chat_messages_fts(chat_messages_fts, rowid, body) VALUES ('delete', old.id, old.body);
END;
CREATE TRIGGER IF NOT EXISTS chat_messages_fts_update AFTER UPDATE OF body ON chat_messages BEGIN
    INSERT INTO chat_messages_fts(chat_messages_fts, rowid, body) VALUES ('delete', old.id, old.body);
    INSERT INTO chat_messages_fts(rowid, body) VALUES (new.id, new.body);
END;
`

func (s *Store) initialize(ctx context.Context) error {
	var ftsEnabled int
	if err := s.db.QueryRowContext(ctx, `SELECT sqlite_compileoption_used('ENABLE_FTS5')`).Scan(&ftsEnabled); err != nil {
		return err
	}
	if ftsEnabled != 1 {
		return errors.New("chat requires SQLite FTS5; use the canonical native build")
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var version int
	if err := tx.QueryRowContext(ctx, "PRAGMA user_version").Scan(&version); err != nil {
		return err
	}
	if version < 0 || version > SchemaVersion {
		return fmt.Errorf("unsupported chat schema %d (maximum %d)", version, SchemaVersion)
	}
	firstNative := true
	if version > 0 {
		var hasMetadata bool
		if err := tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_metadata')`).Scan(&hasMetadata); err != nil {
			return err
		}
		var marker string
		err := sql.ErrNoRows
		if hasMetadata {
			err = tx.QueryRowContext(ctx, `SELECT value FROM chat_metadata WHERE key='go_native_schema3_v1'`).Scan(&marker)
		}
		if err != nil && !errors.Is(err, sql.ErrNoRows) {
			return err
		}
		firstNative = errors.Is(err, sql.ErrNoRows)
		if !firstNative && (marker != "1" || version != SchemaVersion) {
			return errors.New("incompatible native chat schema marker")
		}
	}
	if version == 0 {
		var tables int
		if err := tx.QueryRowContext(ctx, `SELECT count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'`).Scan(&tables); err != nil {
			return err
		}
		if tables != 0 {
			return errors.New("unversioned chat database contains unknown objects")
		}
		if _, err := tx.ExecContext(ctx, baseSchema); err != nil {
			return err
		}
	} else {
		// A separate read-only connection can snapshot the committed source
		// while this transaction excludes all writers, including older Python.
		if firstNative {
			if _, err := s.backup(ctx); err != nil {
				return err
			}
		}
		if version == 1 {
			if _, err := tx.ExecContext(ctx, `ALTER TABLE chat_messages ADD COLUMN sender_ip TEXT NOT NULL DEFAULT ''`); err != nil {
				return err
			}
		}
		if version < 3 {
			var present bool
			if err := tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_read_cursors')`).Scan(&present); err != nil {
				return err
			}
			if present {
				if _, err := tx.ExecContext(ctx, `ALTER TABLE chat_read_cursors RENAME TO chat_read_cursors_v2`); err != nil {
					return err
				}
			}
			if _, err := tx.ExecContext(ctx, `CREATE TABLE chat_read_cursors (
                room_id TEXT NOT NULL, username TEXT NOT NULL,
                read_up_to_id INTEGER NOT NULL DEFAULT 0, updated_at_utc REAL NOT NULL,
                PRIMARY KEY (room_id, username))`); err != nil {
				return err
			}
			if present {
				if _, err := tx.ExecContext(ctx, `INSERT INTO chat_read_cursors
                    SELECT room_id, username, MAX(read_up_to_id), MAX(updated_at_utc)
                    FROM chat_read_cursors_v2 GROUP BY room_id, username;
                    DROP TABLE chat_read_cursors_v2;`); err != nil {
					return err
				}
			}
		}
	}
	if _, err := tx.ExecContext(ctx, baseSchema); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, searchSchema); err != nil {
		return err
	}
	for _, query := range []string{
		`SELECT id,created_at_utc,room_id,username,sender_ip,sender_instance_id,client_message_uuid,body,is_question FROM chat_messages LIMIT 0`,
		`SELECT room_id,username,read_up_to_id,updated_at_utc FROM chat_read_cursors LIMIT 0`,
		`SELECT room_id,username,browser_instance_id,expires_at_utc,updated_at_utc FROM chat_typing_leases LIMIT 0`,
	} {
		rows, err := tx.QueryContext(ctx, query)
		if err != nil {
			return err
		}
		if err := rows.Close(); err != nil {
			return err
		}
	}
	// The external-content table's row count cannot detect a missing index.
	if firstNative {
		if _, err := tx.ExecContext(ctx, `INSERT INTO chat_messages_fts(chat_messages_fts) VALUES('rebuild');
            INSERT INTO chat_metadata(key,value) VALUES('go_native_schema3_v1','1');`); err != nil {
			return err
		}
	}
	if _, err := tx.ExecContext(ctx, `PRAGMA user_version=3;`); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	if _, err := s.db.ExecContext(ctx, `PRAGMA journal_mode=WAL`); err != nil {
		return err
	}
	return s.backfillHistory(ctx)
}

func (s *Store) backup(ctx context.Context) (_ string, err error) {
	reader, err := sql.Open("sqlite3", sqliteURI(s.path, true))
	if err != nil {
		return "", err
	}
	defer func() { err = errors.Join(err, reader.Close()) }()
	directory, err := os.MkdirTemp(filepath.Dir(s.path), filepath.Base(s.path)+".pre-native-chat-")
	if err != nil {
		return "", err
	}
	target := filepath.Join(directory, filepath.Base(s.path))
	if _, err := reader.ExecContext(ctx, "VACUUM INTO ?", target); err != nil {
		return "", fmt.Errorf("backup existing chat database: %w", err)
	}
	if err := os.Chmod(target, 0600); err != nil {
		return "", err
	}
	for _, path := range []string{target, directory, filepath.Dir(directory)} {
		file, err := os.Open(path)
		if err != nil {
			return "", err
		}
		if err := errors.Join(file.Sync(), file.Close()); err != nil {
			return "", err
		}
	}
	return target, nil
}
