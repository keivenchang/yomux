// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yoagent

import (
	"bufio"
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/keivenchang/yomux/internal/atomicfile"
	"github.com/keivenchang/yomux/internal/mutablefs"
	_ "github.com/mattn/go-sqlite3"
)

const (
	cliSessionsLimit = 1024 * 1024
	legacyChatLimit  = 32 * 1024 * 1024
	legacyStateLimit = 8 * 1024 * 1024
	jobRecordLimit   = 128 * 1024
	jobLimit         = 200
	databaseVersion  = 2
)

type RecoveryIssue struct {
	Path   string
	Line   int
	Reason string
}

type Store struct {
	db               *sql.DB
	directory        string
	database         string
	legacyMessages   string
	legacyCLISession string
	legacyJobs       string
	conversationMax  int
}

func (store *Store) ConversationPath() string { return store.database }

func NewStore(directory string) (*Store, error) {
	if strings.TrimSpace(directory) == "" {
		return nil, errors.New("YO!agent state directory is empty")
	}
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return nil, fmt.Errorf("create YO!agent state directory: %w", err)
	}
	if err := os.Chmod(directory, 0o700); err != nil {
		return nil, fmt.Errorf("secure YO!agent state directory: %w", err)
	}
	database := filepath.Join(directory, "yoagent.sqlite3")
	if info, err := os.Lstat(database); err == nil && !info.Mode().IsRegular() {
		return nil, errors.New("YO!agent database must be a regular file")
	} else if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("inspect YO!agent database: %w", err)
	}
	database, err := mutablefs.Canonical(database)
	if err != nil {
		return nil, fmt.Errorf("resolve YO!agent database: %w", err)
	}
	if err := mutablefs.RequireLocal(database); err != nil {
		return nil, fmt.Errorf("YO!agent database must use local storage: %w", err)
	}
	if err := privateDatabaseFiles(database); err != nil {
		return nil, fmt.Errorf("secure YO!agent database files: %w", err)
	}
	uri := url.URL{Scheme: "file", Path: filepath.ToSlash(database)}
	query := url.Values{"_busy_timeout": {"5000"}, "_foreign_keys": {"on"}, "_txlock": {"immediate"}}
	uri.RawQuery = query.Encode()
	db, err := sql.Open("sqlite3", uri.String())
	if err != nil {
		return nil, fmt.Errorf("open YO!agent database: %w", err)
	}
	db.SetMaxOpenConns(1)
	store := &Store{
		db: db, directory: directory, database: database,
		legacyMessages:   filepath.Join(directory, "conversation.jsonl"),
		legacyCLISession: filepath.Join(directory, "cli-sessions.json"),
		legacyJobs:       filepath.Join(directory, "state.json"),
		conversationMax:  ConversationLimit,
	}
	if err := store.initialize(context.Background()); err != nil {
		return nil, errors.Join(fmt.Errorf("initialize YO!agent database: %w", err), db.Close())
	}
	return store, nil
}

func privateDatabaseFiles(path string) error {
	for _, suffix := range []string{"", "-wal", "-shm"} {
		flags := os.O_RDWR | syscall.O_NOFOLLOW
		if suffix == "" {
			flags |= os.O_CREATE
		}
		file, err := os.OpenFile(path+suffix, flags, 0o600)
		if errors.Is(err, os.ErrNotExist) && suffix != "" {
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
			return errors.Join(errors.New("YO!agent database files must be regular"), file.Close())
		}
		if err := errors.Join(file.Chmod(0o600), file.Close()); err != nil {
			return err
		}
	}
	return nil
}

func (store *Store) Close() error { return store.db.Close() }

func (store *Store) initialize(ctx context.Context) error {
	if err := store.db.PingContext(ctx); err != nil {
		return err
	}
	tx, err := store.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var version int
	if err := tx.QueryRowContext(ctx, "PRAGMA user_version").Scan(&version); err != nil {
		return err
	}
	if version < 0 || version > databaseVersion {
		return fmt.Errorf("unsupported YO!agent database schema %d (maximum %d)", version, databaseVersion)
	}
	if version == 0 {
		var tables int
		if err := tx.QueryRowContext(ctx, `SELECT count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'`).Scan(&tables); err != nil {
			return err
		}
		if tables != 0 {
			return errors.New("unversioned YO!agent database contains unknown objects")
		}
		if _, err := tx.ExecContext(ctx, `
CREATE TABLE yoagent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_json TEXT NOT NULL
);
CREATE TABLE yoagent_cli_sessions (
    backend TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    activity_signature TEXT NOT NULL,
    updated_ts REAL NOT NULL
);
CREATE TABLE yoagent_recovery_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    line INTEGER NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE yoagent_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE yoagent_jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_ts REAL NOT NULL,
    idempotency_key TEXT NOT NULL DEFAULT '',
    job_json TEXT NOT NULL
);
CREATE INDEX yoagent_jobs_created ON yoagent_jobs(created_ts DESC);
CREATE INDEX yoagent_jobs_idempotency ON yoagent_jobs(idempotency_key,status);
`); err != nil {
			return err
		}
		if _, err := tx.ExecContext(ctx, "PRAGMA user_version = 2"); err != nil {
			return err
		}
	} else if version == 1 {
		if _, err := tx.ExecContext(ctx, `
CREATE TABLE yoagent_jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_ts REAL NOT NULL,
    idempotency_key TEXT NOT NULL DEFAULT '',
    job_json TEXT NOT NULL
);
CREATE INDEX yoagent_jobs_created ON yoagent_jobs(created_ts DESC);
CREATE INDEX yoagent_jobs_idempotency ON yoagent_jobs(idempotency_key,status);
PRAGMA user_version = 2;
`); err != nil {
			return err
		}
	}
	if err := importLegacyState(ctx, tx, store); err != nil {
		return err
	}
	return tx.Commit()
}

func importLegacyState(ctx context.Context, tx *sql.Tx, store *Store) error {
	var imported string
	err := tx.QueryRowContext(ctx, `SELECT value FROM yoagent_metadata WHERE key='legacy_json_import_v1'`).Scan(&imported)
	if err == nil {
		if imported != "1" {
			return errors.New("invalid YO!agent legacy import marker")
		}
		return nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	if err := importLegacyMessages(ctx, tx, store.legacyMessages); err != nil {
		return err
	}
	if err := importLegacyCLISessions(ctx, tx, store.legacyCLISession); err != nil {
		return err
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO yoagent_metadata(key,value) VALUES('legacy_json_import_v1','1')`)
	return err
}

func importLegacyMessages(ctx context.Context, tx *sql.Tx, path string) error {
	file, err := openPrivateRegular(path, os.O_RDONLY)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("open legacy YO!agent conversation: %w", err)
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, legacyChatLimit+1))
	if err != nil {
		return fmt.Errorf("read legacy YO!agent conversation: %w", err)
	}
	if len(data) > legacyChatLimit {
		_, err := tx.ExecContext(ctx, `INSERT INTO yoagent_recovery_issues(path,line,reason) VALUES(?,0,'legacy_file_too_large')`, path)
		return err
	}
	statement, err := tx.PrepareContext(ctx, `INSERT INTO yoagent_messages(message_json) VALUES(?)`)
	if err != nil {
		return err
	}
	defer statement.Close()
	scanner := bufio.NewScanner(bytes.NewReader(data))
	scanner.Buffer(make([]byte, 64*1024), 8*1024*1024)
	for line := 1; scanner.Scan(); line++ {
		var raw any
		decoder := json.NewDecoder(strings.NewReader(scanner.Text()))
		decoder.UseNumber()
		decodeErr := decoder.Decode(&raw)
		if decodeErr == nil {
			var trailing any
			if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
				decodeErr = errors.New("trailing JSON")
			}
		}
		if decodeErr != nil {
			if _, err := tx.ExecContext(ctx, `INSERT INTO yoagent_recovery_issues(path,line,reason) VALUES(?,?,?)`, path, line, "invalid_json"); err != nil {
				return err
			}
			continue
		}
		message, sanitizeErr := SanitizeMessage(raw, MessageOptions{}, time.Now())
		if sanitizeErr != nil || message == nil {
			reason := "invalid_message"
			if sanitizeErr == nil {
				reason = "empty_message"
			}
			if _, err := tx.ExecContext(ctx, `INSERT INTO yoagent_recovery_issues(path,line,reason) VALUES(?,?,?)`, path, line, reason); err != nil {
				return err
			}
			continue
		}
		encoded, err := json.Marshal(message)
		if err != nil {
			return fmt.Errorf("encode legacy YO!agent message at line %d: %w", line, err)
		}
		if _, err := statement.ExecContext(ctx, string(encoded)); err != nil {
			return fmt.Errorf("import legacy YO!agent message at line %d: %w", line, err)
		}
	}
	return scanner.Err()
}

func importLegacyCLISessions(ctx context.Context, tx *sql.Tx, path string) error {
	file, err := openPrivateRegular(path, os.O_RDONLY)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("open legacy YO!agent CLI sessions: %w", err)
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, cliSessionsLimit+1))
	if err != nil {
		return fmt.Errorf("read legacy YO!agent CLI sessions: %w", err)
	}
	if len(data) > cliSessionsLimit {
		_, err := tx.ExecContext(ctx, `INSERT INTO yoagent_recovery_issues(path,line,reason) VALUES(?,0,'legacy_file_too_large')`, path)
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var raw any
	decodeErr := decoder.Decode(&raw)
	if decodeErr == nil {
		var trailing any
		if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
			decodeErr = errors.New("trailing JSON")
		}
	}
	if decodeErr != nil {
		_, err := tx.ExecContext(ctx, `INSERT INTO yoagent_recovery_issues(path,line,reason) VALUES(?,0,'invalid_json')`, path)
		return err
	}
	sessions, err := SanitizeCLISessions(raw, float64(time.Now().UnixNano())/1e9, ManagedBackends())
	if err != nil {
		_, insertErr := tx.ExecContext(ctx, `INSERT INTO yoagent_recovery_issues(path,line,reason) VALUES(?,0,'invalid_state')`, path)
		return insertErr
	}
	for backend, state := range sessions {
		if _, err := tx.ExecContext(ctx, `INSERT INTO yoagent_cli_sessions(backend,session_id,activity_signature,updated_ts) VALUES(?,?,?,?)`, backend, state.SessionID, state.ActivitySignature, state.UpdatedTS); err != nil {
			return fmt.Errorf("import legacy YO!agent %s session: %w", backend, err)
		}
	}
	return nil
}

func openPrivateRegular(path string, flags int) (*os.File, error) {
	file, err := os.OpenFile(path, flags|syscall.O_NOFOLLOW, 0o600)
	if err != nil {
		return nil, err
	}
	info, err := file.Stat()
	if err != nil {
		return nil, errors.Join(err, file.Close())
	}
	if !info.Mode().IsRegular() {
		return nil, errors.Join(errors.New("YO!agent state must be a regular file"), file.Close())
	}
	if flags&(os.O_WRONLY|os.O_RDWR|os.O_CREATE) != 0 {
		if err := file.Chmod(0o600); err != nil {
			return nil, errors.Join(err, file.Close())
		}
	}
	return file, nil
}

func (store *Store) LoadMessages(ctx context.Context, limit int) ([]Message, []RecoveryIssue, error) {
	if limit < 1 {
		limit = 1
	}
	if limit > store.conversationMax {
		limit = store.conversationMax
	}
	messages, err := store.readMessages(ctx, limit)
	if err != nil {
		return nil, nil, err
	}
	issues, err := store.loadIssues(ctx, store.legacyMessages)
	return messages, issues, err
}

func (store *Store) readMessages(ctx context.Context, limit int) ([]Message, error) {
	return loadMessagesFrom(ctx, store.db, limit)
}

type rowQueryer interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}

func loadMessagesFrom(ctx context.Context, queryer rowQueryer, limit int) ([]Message, error) {
	query := `SELECT message_json FROM yoagent_messages ORDER BY id DESC`
	var rows *sql.Rows
	var err error
	if limit > 0 {
		query += ` LIMIT ?`
		rows, err = queryer.QueryContext(ctx, query, limit)
	} else {
		rows, err = queryer.QueryContext(ctx, query)
	}
	if err != nil {
		return nil, fmt.Errorf("query YO!agent conversation: %w", err)
	}
	messages := make([]Message, 0)
	for rows.Next() {
		var encoded string
		if err := rows.Scan(&encoded); err != nil {
			return nil, errors.Join(fmt.Errorf("read YO!agent conversation row: %w", err), rows.Close())
		}
		var raw any
		decoder := json.NewDecoder(strings.NewReader(encoded))
		decoder.UseNumber()
		if err := decoder.Decode(&raw); err != nil {
			return nil, errors.Join(fmt.Errorf("decode YO!agent conversation row: %w", err), rows.Close())
		}
		message, err := SanitizeMessage(raw, MessageOptions{}, time.Now())
		if err != nil || message == nil {
			return nil, errors.Join(errors.New("YO!agent database contains an invalid message"), rows.Close())
		}
		messages = append(messages, message)
	}
	if err := rows.Err(); err != nil {
		return nil, errors.Join(fmt.Errorf("read YO!agent conversation rows: %w", err), rows.Close())
	}
	if err := rows.Close(); err != nil {
		return nil, fmt.Errorf("close YO!agent conversation rows: %w", err)
	}
	for left, right := 0, len(messages)-1; left < right; left, right = left+1, right-1 {
		messages[left], messages[right] = messages[right], messages[left]
	}
	return messages, nil
}

func (store *Store) loadIssues(ctx context.Context, path string) ([]RecoveryIssue, error) {
	return loadIssuesFrom(ctx, store.db, path)
}

func loadJSONMap(encoded string) (map[string]any, error) {
	var value map[string]any
	decoder := json.NewDecoder(strings.NewReader(encoded))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		if err == nil {
			err = errors.New("multiple JSON values")
		}
		return nil, err
	}
	if value == nil {
		return nil, errors.New("JSON value is not an object")
	}
	return value, nil
}

func normalizedJob(value map[string]any) (map[string]any, string, string, float64, error) {
	return normalizeJob(value, false)
}

func normalizeJob(value map[string]any, allowFiring bool) (map[string]any, string, string, float64, error) {
	id, _ := value["id"].(string)
	if id == "" {
		id, _ = value["job_id"].(string)
	}
	id = strings.TrimSpace(id)
	if id == "" || len(id) > 120 {
		return nil, "", "", 0, errors.New("invalid YO!agent job id")
	}
	status, _ := value["status"].(string)
	if status == "" {
		status = "queued"
	}
	if status == "firing" && !allowFiring {
		status = "failed"
		value["failed_at"] = time.Now().UTC().Format(time.RFC3339Nano)
		value["error"] = "job was interrupted while sending; automatic retry is suppressed"
	}
	switch status {
	case "queued", "pending_confirmation", "fired", "cancelled", "failed", "timed_out":
	case "firing":
		if !allowFiring {
			status = "failed"
		}
	default:
		status = "queued"
	}
	value["id"] = id
	value["job_id"] = id
	value["status"] = status
	createdTS := number(value["created_ts"])
	if createdTS <= 0 || math.IsNaN(createdTS) || math.IsInf(createdTS, 0) {
		createdTS = float64(time.Now().UnixNano()) / 1e9
		value["created_ts"] = createdTS
	}
	idempotencyKey, _ := value["idempotency_key"].(string)
	return value, id, idempotencyKey, createdTS, nil
}

func (store *Store) ImportLegacyJobs(ctx context.Context, path string) error {
	if strings.TrimSpace(path) == "" {
		return errors.New("legacy YO!agent jobs path is empty")
	}
	store.legacyJobs = path
	tx, err := store.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var imported string
	err = tx.QueryRowContext(ctx, `SELECT value FROM yoagent_metadata WHERE key='legacy_jobs_import_v1'`).Scan(&imported)
	if err == nil {
		if imported != "1" {
			return errors.New("invalid YO!agent jobs import marker")
		}
		return tx.Commit()
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	file, err := openPrivateRegular(path, os.O_RDONLY)
	if errors.Is(err, os.ErrNotExist) {
		_, err = tx.ExecContext(ctx, `INSERT INTO yoagent_metadata(key,value) VALUES('legacy_jobs_import_v1','1')`)
		if err != nil {
			return err
		}
		return tx.Commit()
	}
	if err != nil {
		return fmt.Errorf("open legacy YO!agent jobs state: %w", err)
	}
	data, readErr := io.ReadAll(io.LimitReader(file, legacyStateLimit+1))
	closeErr := file.Close()
	if err := errors.Join(readErr, closeErr); err != nil {
		return fmt.Errorf("read legacy YO!agent jobs state: %w", err)
	}
	if len(data) > legacyStateLimit {
		if _, err := tx.ExecContext(ctx, `INSERT INTO yoagent_recovery_issues(path,line,reason) VALUES(?,0,'legacy_state_too_large')`, path); err != nil {
			return err
		}
	} else {
		state, decodeErr := loadJSONMap(string(data))
		if decodeErr != nil {
			if _, err := tx.ExecContext(ctx, `INSERT INTO yoagent_recovery_issues(path,line,reason) VALUES(?,0,'invalid_legacy_state_json')`, path); err != nil {
				return err
			}
		} else if err := store.importLegacyJobValues(ctx, tx, path, state["yoagent_jobs"]); err != nil {
			return err
		}
	}
	if _, err := tx.ExecContext(ctx, `INSERT INTO yoagent_metadata(key,value) VALUES('legacy_jobs_import_v1','1')`); err != nil {
		return err
	}
	return tx.Commit()
}

func (store *Store) importLegacyJobValues(ctx context.Context, tx *sql.Tx, path string, value any) error {
	items := make([]any, 0)
	switch jobs := value.(type) {
	case []any:
		items = jobs
	case map[string]any:
		for _, job := range jobs {
			items = append(items, job)
		}
	case nil:
		return nil
	default:
		_, err := tx.ExecContext(ctx, `INSERT INTO yoagent_recovery_issues(path,line,reason) VALUES(?,0,'invalid_legacy_jobs')`, path)
		return err
	}
	sort.SliceStable(items, func(left, right int) bool {
		leftJob, _ := items[left].(map[string]any)
		rightJob, _ := items[right].(map[string]any)
		return number(leftJob["created_ts"]) > number(rightJob["created_ts"])
	})
	start := 0
	if len(items) > jobLimit {
		start = len(items) - jobLimit
	}
	for index, item := range items[start:] {
		job, ok := item.(map[string]any)
		if !ok {
			if _, err := tx.ExecContext(ctx, `INSERT INTO yoagent_recovery_issues(path,line,reason) VALUES(?,?,?)`, path, start+index+1, "invalid_legacy_job"); err != nil {
				return err
			}
			continue
		}
		clean, id, idempotencyKey, createdTS, err := normalizedJob(job)
		if err != nil {
			if _, err := tx.ExecContext(ctx, `INSERT INTO yoagent_recovery_issues(path,line,reason) VALUES(?,?,?)`, path, start+index+1, "invalid_legacy_job"); err != nil {
				return err
			}
			continue
		}
		encoded, err := json.Marshal(clean)
		if err != nil {
			return fmt.Errorf("encode legacy YO!agent job %q: %w", id, err)
		}
		if len(encoded) > jobRecordLimit {
			if _, err := tx.ExecContext(ctx, `INSERT INTO yoagent_recovery_issues(path,line,reason) VALUES(?,?,?)`, path, start+index+1, "legacy_job_too_large"); err != nil {
				return err
			}
			continue
		}
		if _, err := tx.ExecContext(ctx, `INSERT OR REPLACE INTO yoagent_jobs(job_id,status,created_ts,idempotency_key,job_json) VALUES(?,?,?,?,?)`, id, clean["status"], createdTS, idempotencyKey, string(encoded)); err != nil {
			return fmt.Errorf("import legacy YO!agent job %q: %w", id, err)
		}
	}
	return nil
}

func (store *Store) LoadJobs(ctx context.Context) ([]map[string]any, []RecoveryIssue, error) {
	jobs, err := loadJobsFrom(ctx, store.db)
	if err != nil {
		return nil, nil, err
	}
	issues, err := store.loadIssues(ctx, store.legacyJobs)
	return jobs, issues, err
}

func loadJobsFrom(ctx context.Context, queryer rowQueryer) ([]map[string]any, error) {
	rows, err := queryer.QueryContext(ctx, `SELECT job_json FROM yoagent_jobs ORDER BY created_ts DESC,job_id LIMIT ?`, jobLimit)
	if err != nil {
		return nil, fmt.Errorf("query YO!agent jobs: %w", err)
	}
	jobs := make([]map[string]any, 0)
	for rows.Next() {
		var encoded string
		if err := rows.Scan(&encoded); err != nil {
			return nil, errors.Join(fmt.Errorf("read YO!agent job: %w", err), rows.Close())
		}
		job, err := loadJSONMap(encoded)
		if err != nil {
			return nil, errors.Join(fmt.Errorf("decode YO!agent job: %w", err), rows.Close())
		}
		jobs = append(jobs, job)
	}
	if err := rows.Err(); err != nil {
		return nil, errors.Join(fmt.Errorf("read YO!agent jobs: %w", err), rows.Close())
	}
	if err := rows.Close(); err != nil {
		return nil, fmt.Errorf("close YO!agent jobs: %w", err)
	}
	return jobs, nil
}

func (store *Store) SaveJob(ctx context.Context, value map[string]any) error {
	clean, id, idempotencyKey, createdTS, err := normalizedJob(value)
	if err != nil {
		return err
	}
	encoded, err := json.Marshal(clean)
	if err != nil {
		return fmt.Errorf("encode YO!agent job %q: %w", id, err)
	}
	if len(encoded) > jobRecordLimit {
		return fmt.Errorf("YO!agent job %q exceeds %d bytes", id, jobRecordLimit)
	}
	tx, err := store.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin YO!agent job save: %w", err)
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, `INSERT INTO yoagent_jobs(job_id,status,created_ts,idempotency_key,job_json) VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,created_ts=excluded.created_ts,idempotency_key=excluded.idempotency_key,job_json=excluded.job_json`, id, clean["status"], createdTS, idempotencyKey, string(encoded)); err != nil {
		return fmt.Errorf("persist YO!agent job %q: %w", id, err)
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM yoagent_jobs WHERE job_id NOT IN (SELECT job_id FROM yoagent_jobs ORDER BY created_ts DESC,job_id LIMIT ?)`, jobLimit); err != nil {
		return fmt.Errorf("prune YO!agent job history: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit YO!agent job save: %w", err)
	}
	return nil
}

// CreateJob persists a new job unless an active job with the same idempotency key already exists.
// The duplicate check and insert share one transaction so parallel requests cannot enqueue twins.
func (store *Store) CreateJob(ctx context.Context, value map[string]any) (map[string]any, bool, error) {
	clean, id, idempotencyKey, createdTS, err := normalizedJob(value)
	if err != nil {
		return nil, false, err
	}
	encoded, err := json.Marshal(clean)
	if err != nil {
		return nil, false, fmt.Errorf("encode YO!agent job %q: %w", id, err)
	}
	if len(encoded) > jobRecordLimit {
		return nil, false, fmt.Errorf("YO!agent job %q exceeds %d bytes", id, jobRecordLimit)
	}
	tx, err := store.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, false, fmt.Errorf("begin YO!agent job creation: %w", err)
	}
	defer tx.Rollback()
	var existingJSON string
	err = tx.QueryRowContext(ctx, `SELECT job_json FROM yoagent_jobs WHERE idempotency_key=? AND status IN ('queued','pending_confirmation') ORDER BY created_ts DESC,job_id LIMIT 1`, idempotencyKey).Scan(&existingJSON)
	if err == nil {
		existing, decodeErr := loadJSONMap(existingJSON)
		if decodeErr != nil {
			return nil, false, fmt.Errorf("decode duplicate YO!agent job: %w", decodeErr)
		}
		if err := tx.Commit(); err != nil {
			return nil, false, fmt.Errorf("finish duplicate YO!agent job lookup: %w", err)
		}
		return existing, false, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return nil, false, fmt.Errorf("check duplicate YO!agent job: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `INSERT INTO yoagent_jobs(job_id,status,created_ts,idempotency_key,job_json) VALUES(?,?,?,?,?)`, id, clean["status"], createdTS, idempotencyKey, string(encoded)); err != nil {
		return nil, false, fmt.Errorf("persist YO!agent job %q: %w", id, err)
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM yoagent_jobs WHERE job_id NOT IN (SELECT job_id FROM yoagent_jobs ORDER BY created_ts DESC,job_id LIMIT ?)`, jobLimit); err != nil {
		return nil, false, fmt.Errorf("prune YO!agent job history: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, false, fmt.Errorf("commit YO!agent job creation: %w", err)
	}
	return clean, true, nil
}

func (store *Store) LoadJob(ctx context.Context, id string) (map[string]any, error) {
	var encoded string
	if err := store.db.QueryRowContext(ctx, `SELECT job_json FROM yoagent_jobs WHERE job_id=?`, id).Scan(&encoded); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, os.ErrNotExist
		}
		return nil, fmt.Errorf("query YO!agent job %q: %w", id, err)
	}
	job, err := loadJSONMap(encoded)
	if err != nil {
		return nil, fmt.Errorf("decode YO!agent job %q: %w", id, err)
	}
	return job, nil
}

func (store *Store) TransitionJob(ctx context.Context, id string, expected []string, job map[string]any) (bool, error) {
	clean, cleanID, idempotencyKey, createdTS, err := normalizeJob(job, true)
	if err != nil {
		return false, err
	}
	if cleanID != id {
		return false, errors.New("YO!agent job transition id mismatch")
	}
	encoded, err := json.Marshal(clean)
	if err != nil {
		return false, fmt.Errorf("encode YO!agent job %q: %w", id, err)
	}
	if len(encoded) > jobRecordLimit {
		return false, fmt.Errorf("YO!agent job %q exceeds %d bytes", id, jobRecordLimit)
	}
	tx, err := store.db.BeginTx(ctx, nil)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(ctx, `UPDATE yoagent_jobs SET status=?,created_ts=?,idempotency_key=?,job_json=? WHERE job_id=? AND status IN (`+sqlPlaceholders(len(expected))+`)`, append([]any{clean["status"], createdTS, idempotencyKey, string(encoded), id}, stringArguments(expected)...)...)
	if err != nil {
		return false, fmt.Errorf("transition YO!agent job %q: %w", id, err)
	}
	changed, err := result.RowsAffected()
	if err != nil {
		return false, err
	}
	if err := tx.Commit(); err != nil {
		return false, fmt.Errorf("commit YO!agent job transition: %w", err)
	}
	return changed == 1, nil
}

func sqlPlaceholders(count int) string {
	if count < 1 {
		return "NULL"
	}
	return strings.TrimSuffix(strings.Repeat("?,", count), ",")
}

func stringArguments(values []string) []any {
	arguments := make([]any, len(values))
	for index, value := range values {
		arguments[index] = value
	}
	return arguments
}

func loadIssuesFrom(ctx context.Context, queryer rowQueryer, path string) ([]RecoveryIssue, error) {
	rows, err := queryer.QueryContext(ctx, `SELECT path,line,reason FROM yoagent_recovery_issues WHERE path=? ORDER BY id`, path)
	if err != nil {
		return nil, fmt.Errorf("query YO!agent recovery issues: %w", err)
	}
	defer rows.Close()
	issues := make([]RecoveryIssue, 0)
	for rows.Next() {
		var issue RecoveryIssue
		if err := rows.Scan(&issue.Path, &issue.Line, &issue.Reason); err != nil {
			return nil, fmt.Errorf("read YO!agent recovery issue: %w", err)
		}
		issues = append(issues, issue)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("read YO!agent recovery issues: %w", err)
	}
	return issues, nil
}

func (store *Store) AppendMessage(ctx context.Context, value any) (Message, error) {
	message, err := SanitizeMessage(value, MessageOptions{}, time.Now())
	if err != nil {
		return nil, err
	}
	if message == nil {
		return nil, nil
	}
	encoded, err := json.Marshal(message)
	if err != nil {
		return nil, fmt.Errorf("encode YO!agent message: %w", err)
	}
	if _, err := store.db.ExecContext(ctx, `INSERT INTO yoagent_messages(message_json) VALUES(?)`, string(encoded)); err != nil {
		return nil, fmt.Errorf("persist YO!agent message: %w", err)
	}
	return message, nil
}

func (store *Store) ClearMessages(ctx context.Context) error {
	if _, err := store.db.ExecContext(ctx, `DELETE FROM yoagent_messages`); err != nil {
		return fmt.Errorf("clear YO!agent conversation: %w", err)
	}
	return nil
}

func (store *Store) LoadCLISessions(ctx context.Context, monotonicNow float64, backends []string) (map[string]CLIState, []RecoveryIssue, error) {
	states, err := loadCLISessionsFrom(ctx, store.db, monotonicNow, backends)
	if err != nil {
		issues, issueErr := store.loadIssues(ctx, store.legacyCLISession)
		return nil, issues, errors.Join(err, issueErr)
	}
	issues, err := store.loadIssues(ctx, store.legacyCLISession)
	return states, issues, err
}

func loadCLISessionsFrom(ctx context.Context, queryer rowQueryer, monotonicNow float64, backends []string) (map[string]CLIState, error) {
	rows, err := queryer.QueryContext(ctx, `SELECT backend,session_id,activity_signature,updated_ts FROM yoagent_cli_sessions`)
	if err != nil {
		return nil, fmt.Errorf("query YO!agent CLI sessions: %w", err)
	}
	candidate := make(map[string]any)
	for rows.Next() {
		var backend, sessionID, signature string
		var updated float64
		if err := rows.Scan(&backend, &sessionID, &signature, &updated); err != nil {
			return nil, errors.Join(fmt.Errorf("read YO!agent CLI session: %w", err), rows.Close())
		}
		candidate[backend] = map[string]any{"session_id": sessionID, "activity_signature": signature, "updated_ts": updated}
	}
	if err := rows.Err(); err != nil {
		return nil, errors.Join(fmt.Errorf("read YO!agent CLI sessions: %w", err), rows.Close())
	}
	if err := rows.Close(); err != nil {
		return nil, fmt.Errorf("close YO!agent CLI sessions: %w", err)
	}
	states, err := SanitizeCLISessions(candidate, monotonicNow, backends)
	if err != nil {
		return nil, fmt.Errorf("validate YO!agent CLI sessions: %w", err)
	}
	return states, nil
}

func (store *Store) SaveCLISessions(ctx context.Context, sessions map[string]CLIState, backends []string) error {
	candidate := make(map[string]any, len(sessions))
	for backend, state := range sessions {
		candidate[backend] = map[string]any{
			"session_id": state.SessionID, "activity_signature": state.ActivitySignature,
			"updated_ts": state.UpdatedTS, "updated_monotonic": state.UpdatedMonotonic,
		}
	}
	clean, err := SanitizeCLISessions(candidate, float64(time.Now().UnixNano())/1e9, backends)
	if err != nil {
		return fmt.Errorf("validate YO!agent CLI sessions: %w", err)
	}
	tx, err := store.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, `DELETE FROM yoagent_cli_sessions`); err != nil {
		return fmt.Errorf("replace YO!agent CLI sessions: %w", err)
	}
	for backend, state := range clean {
		updated := state.UpdatedTS
		if updated <= 0 {
			updated = float64(time.Now().UnixNano()) / 1e9
		}
		if _, err := tx.ExecContext(ctx, `INSERT INTO yoagent_cli_sessions(backend,session_id,activity_signature,updated_ts) VALUES(?,?,?,?)`, backend, state.SessionID, state.ActivitySignature, updated); err != nil {
			return fmt.Errorf("save YO!agent %s session: %w", backend, err)
		}
	}
	return tx.Commit()
}

func (store *Store) ClearCLISessions(ctx context.Context) error {
	if _, err := store.db.ExecContext(ctx, `DELETE FROM yoagent_cli_sessions`); err != nil {
		return fmt.Errorf("clear YO!agent CLI sessions: %w", err)
	}
	return nil
}

func (store *Store) Reset(ctx context.Context) ([]string, error) {
	tx, err := store.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	messages, err := loadMessagesFrom(ctx, tx, 0)
	if err != nil {
		return nil, err
	}
	states, err := loadCLISessionsFrom(ctx, tx, float64(time.Now().UnixNano())/1e9, ManagedBackends())
	if err != nil {
		return nil, err
	}
	issues, err := loadIssuesFrom(ctx, tx, store.legacyMessages)
	if err != nil {
		return nil, err
	}
	cliIssues, err := loadIssuesFrom(ctx, tx, store.legacyCLISession)
	if err != nil {
		return nil, err
	}
	issues = append(issues, cliIssues...)
	jobIssues, err := loadIssuesFrom(ctx, tx, store.legacyJobs)
	if err != nil {
		return nil, err
	}
	issues = append(issues, jobIssues...)
	jobs, err := loadJobsFrom(ctx, tx)
	if err != nil {
		return nil, err
	}
	if len(messages) == 0 && len(states) == 0 && len(issues) == 0 && len(jobs) == 0 {
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("finish empty YO!agent reset: %w", err)
		}
		return nil, nil
	}
	backup, err := json.MarshalIndent(map[string]any{"messages": messages, "cli_sessions": states, "jobs": jobs, "recovery_issues": issues}, "", "  ")
	if err != nil {
		return nil, fmt.Errorf("encode YO!agent reset backup: %w", err)
	}
	backup = append(backup, '\n')
	path := filepath.Join(store.directory, "reset-"+time.Now().UTC().Format("20060102T150405.000000000Z")+".json")
	if err := atomicfile.Write(path, backup); err != nil {
		return nil, fmt.Errorf("write YO!agent reset backup: %w", err)
	}
	for _, table := range []string{"yoagent_messages", "yoagent_cli_sessions", "yoagent_jobs", "yoagent_recovery_issues"} {
		if _, err := tx.ExecContext(ctx, `DELETE FROM `+table); err != nil {
			return nil, fmt.Errorf("reset YO!agent %s: %w", table, err)
		}
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit YO!agent reset: %w", err)
	}
	return []string{path}, nil
}
