// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bufio"
	"container/heap"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/mattn/go-sqlite3"
)

const (
	DefaultIndexedFiles      = 1_000_000
	DefaultIndexBytes        = int64(512 * 1024 * 1024)
	MaxIndexBytes            = int64(1024 * 1024 * 1024)
	indexBatchSize           = 256
	indexQueueLimit          = 200_000
	indexQueueByteLimit      = 32 * 1024 * 1024
	searchIndexSchemaVersion = 1
)

type indexRequest struct {
	ID                uint64         `json:"id"`
	CancelID          uint64         `json:"cancel_id,omitempty"`
	Action            string         `json:"action"`
	Root              string         `json:"root"`
	Identity          string         `json:"identity,omitempty"`
	Query             string         `json:"query,omitempty"`
	Limit             int            `json:"limit,omitempty"`
	Cursor            string         `json:"cursor,omitempty"`
	MaxFiles          int            `json:"max_files,omitempty"`
	MaxBytes          int64          `json:"max_bytes,omitempty"`
	RefreshSec        int            `json:"refresh_seconds,omitempty"`
	ExcludeDirs       []string       `json:"exclude_dirs,omitempty"`
	ExcludePaths      []string       `json:"exclude_paths,omitempty"`
	SettingsRevision  uint64         `json:"settings_revision,omitempty"`
	FenceRoots        []string       `json:"fence_roots,omitempty"`
	Invalidate        bool           `json:"invalidate,omitempty"`
	PreserveSnapshots bool           `json:"preserve_snapshots,omitempty"`
	Options           map[string]any `json:"options,omitempty"`
}

type indexResponse struct {
	ID      uint64         `json:"id"`
	OK      bool           `json:"ok"`
	Error   string         `json:"error,omitempty"`
	Payload map[string]any `json:"payload,omitempty"`
}

// RunSearchIndexWorker serves a private newline-delimited protocol over inherited stdin/stdout.
// It is called only by the supervised server child process; stdout never carries logs.
func RunSearchIndexWorker(input io.Reader, output io.Writer, databasePath string) error {
	store, err := openSearchIndexStore(databasePath)
	if err != nil {
		return err
	}
	defer store.db.Close()
	engine := newSearchIndexEngine(store)
	reader := bufio.NewScanner(input)
	reader.Buffer(make([]byte, 4096), 1<<20)
	writer := bufio.NewWriter(output)
	var activeMu sync.Mutex
	active := make(map[uint64]context.CancelFunc)
	var writerMu sync.Mutex
	var workers sync.WaitGroup
	for reader.Scan() {
		var request indexRequest
		if err := json.Unmarshal(reader.Bytes(), &request); err != nil {
			request.Action = "invalid"
		}
		if request.Action == "cancel" {
			activeMu.Lock()
			cancel := active[request.CancelID]
			activeMu.Unlock()
			if cancel != nil {
				cancel()
			}
			continue
		}
		requestContext, cancel := context.WithCancel(context.Background())
		activeMu.Lock()
		active[request.ID] = cancel
		activeMu.Unlock()
		workers.Add(1)
		go func(request indexRequest, requestContext context.Context, cancel context.CancelFunc) {
			defer workers.Done()
			defer cancel()
			response := indexResponse{ID: request.ID}
			payload, requestErr := engine.handleContext(requestContext, request)
			response.Payload = payload
			response.OK = requestErr == nil
			if requestErr != nil {
				response.Error = requestErr.Error()
			}
			activeMu.Lock()
			delete(active, request.ID)
			activeMu.Unlock()
			data, marshalErr := json.Marshal(response)
			if marshalErr != nil {
				return
			}
			writerMu.Lock()
			defer writerMu.Unlock()
			if _, err := writer.Write(append(data, '\n')); err != nil {
				return
			}
			_ = writer.Flush()
		}(request, requestContext, cancel)
	}
	activeMu.Lock()
	for _, cancel := range active {
		cancel()
	}
	activeMu.Unlock()
	workers.Wait()
	if err := engine.quiesceBuilds(); err != nil {
		return err
	}
	return reader.Err()
}

type searchIndexStore struct {
	db   *sql.DB
	path string
}

func openSearchIndexStore(path string) (*searchIndexStore, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite3", "file:"+filepath.ToSlash(path)+"?_busy_timeout=5000&_journal_mode=WAL&_synchronous=NORMAL&_cache_size=-32768&_mmap_size=0")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(4)
	var schemaVersion int
	if err := db.QueryRow(`PRAGMA user_version`).Scan(&schemaVersion); err != nil {
		_ = db.Close()
		return nil, err
	}
	if schemaVersion > searchIndexSchemaVersion {
		_ = db.Close()
		return nil, fmt.Errorf("Finder index schema %d is newer than supported version %d", schemaVersion, searchIndexSchemaVersion)
	}
	statements := []string{
		`PRAGMA auto_vacuum=INCREMENTAL`, `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, `PRAGMA journal_size_limit=0`, `PRAGMA cache_size=-32768`, `PRAGMA mmap_size=0`,
		`CREATE TABLE IF NOT EXISTS roots(root TEXT PRIMARY KEY, identity TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL, coverage TEXT NOT NULL DEFAULT 'pending', count INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0, updated_ns INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', settings_revision INTEGER NOT NULL DEFAULT 0, scan_duration_ns INTEGER NOT NULL DEFAULT 0, last_success_ns INTEGER NOT NULL DEFAULT 0)`,
		`CREATE TABLE IF NOT EXISTS root_generations(root TEXT PRIMARY KEY, generation INTEGER NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS settings_fences(root TEXT PRIMARY KEY, revision INTEGER NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS settings_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS entries(root TEXT NOT NULL, generation INTEGER NOT NULL, relative_path TEXT NOT NULL, name TEXT NOT NULL, file_identity TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, PRIMARY KEY(root,generation,relative_path)) WITHOUT ROWID`,
		`CREATE INDEX IF NOT EXISTS entries_by_name ON entries(root,generation,name)`,
	}
	for _, statement := range statements {
		if _, err := db.Exec(statement); err != nil {
			_ = db.Close()
			return nil, err
		}
	}
	rootColumns := map[string]bool{}
	columnRows, err := db.Query(`PRAGMA table_info(roots)`)
	if err != nil {
		_ = db.Close()
		return nil, err
	}
	for columnRows.Next() {
		var ordinal, notNull, primaryKey int
		var name, columnType string
		var defaultValue any
		if err := columnRows.Scan(&ordinal, &name, &columnType, &notNull, &defaultValue, &primaryKey); err != nil {
			_ = columnRows.Close()
			_ = db.Close()
			return nil, err
		}
		rootColumns[name] = true
	}
	if err := columnRows.Close(); err != nil {
		_ = db.Close()
		return nil, err
	}
	newRootColumns := map[string]string{"scan_duration_ns": `INTEGER NOT NULL DEFAULT 0`, "last_success_ns": `INTEGER NOT NULL DEFAULT 0`}
	for name, definition := range newRootColumns {
		if !rootColumns[name] {
			if _, err := db.Exec(`ALTER TABLE roots ADD COLUMN ` + name + ` ` + definition); err != nil {
				_ = db.Close()
				return nil, err
			}
		}
	}
	if _, err := db.Exec(fmt.Sprintf(`PRAGMA user_version=%d`, searchIndexSchemaVersion)); err != nil {
		_ = db.Close()
		return nil, err
	}
	var vacuumMode int
	if err := db.QueryRow(`PRAGMA auto_vacuum`).Scan(&vacuumMode); err != nil {
		_ = db.Close()
		return nil, err
	}
	// Existing databases need one rebuild to enable bounded, incremental page reclamation.
	if vacuumMode != 2 {
		if _, err := db.Exec(`PRAGMA auto_vacuum=INCREMENTAL`); err != nil {
			_ = db.Close()
			return nil, err
		}
		if _, err := db.Exec(`VACUUM`); err != nil {
			_ = db.Close()
			return nil, err
		}
	}
	return &searchIndexStore{db: db, path: path}, nil
}

func (store *searchIndexStore) setStorageBudget(maxBytes int64) error {
	if maxBytes < 1 {
		return errors.New("index storage budget must be positive")
	}
	var pageSize, pageCount int64
	if err := store.db.QueryRow(`PRAGMA page_size`).Scan(&pageSize); err != nil {
		return err
	}
	if err := store.db.QueryRow(`PRAGMA page_count`).Scan(&pageCount); err != nil {
		return err
	}
	pageBudget := indexStorageLimit(maxBytes) / pageSize
	if pageBudget < 1 {
		return errors.New("index storage budget is smaller than the SQLite reserve")
	}
	if pageCount > pageBudget {
		used, err := store.usedBytes()
		if err != nil {
			return err
		}
		if used > pageBudget*pageSize {
			return errors.New("existing index snapshot exceeds the configured storage budget")
		}
		if _, err := store.db.Exec(`VACUUM`); err != nil {
			return err
		}
	}
	var applied int64
	if err := store.db.QueryRow(fmt.Sprintf(`PRAGMA max_page_count=%d`, pageBudget)).Scan(&applied); err != nil {
		return err
	}
	if applied < pageBudget {
		return errors.New("SQLite could not apply the configured storage budget")
	}
	return nil
}

func indexStorageLimit(maxBytes int64) int64 {
	reserve := maxBytes / 16
	if reserve < 64*1024 {
		reserve = 64 * 1024
	}
	if reserve > 8*1024*1024 {
		reserve = 8 * 1024 * 1024
	}
	return maxBytes - reserve
}

func isSQLiteStorageFull(err error) bool {
	var sqliteErr sqlite3.Error
	return errors.As(err, &sqliteErr) && sqliteErr.Code == sqlite3.ErrFull
}

func (store *searchIndexStore) allocatedBytes() (int64, error) {
	if store.path == ":memory:" {
		var pageSize, pageCount int64
		if err := store.db.QueryRow(`PRAGMA page_size`).Scan(&pageSize); err != nil {
			return 0, err
		}
		if err := store.db.QueryRow(`PRAGMA page_count`).Scan(&pageCount); err != nil {
			return 0, err
		}
		return pageSize * pageCount, nil
	}
	var allocated int64
	for _, path := range []string{store.path, store.path + "-wal"} {
		info, err := os.Stat(path)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return 0, err
		}
		allocated += info.Size()
	}
	return allocated, nil
}

type searchIndexEngine struct {
	store                  *searchIndexStore
	mu                     sync.Mutex
	builds                 map[string]bool
	pendingBuild           map[string]indexBuildJob
	buildDone              map[string]chan struct{}
	rerun                  map[string]indexBuildJob
	fences                 map[string]uint64
	settingsRevisions      map[string]uint64
	settingsFloor          uint64
	settingsFloorSet       bool
	fenceLoadErr           error
	incarnation            string
	activeBuild            bool
	activeRoot             string
	activeSettingsRevision uint64
	buildCancels           map[string]context.CancelFunc
	snapshotMutations      int
	snapshots              map[string]*searchIndexSnapshot
	crawlPause             sync.RWMutex
}

type indexBuildJob struct {
	root, identity   string
	maxFiles         int
	maxBytes         int64
	fence            uint64
	settingsRevision uint64
	excludeDirs      map[string]struct{}
	excludePaths     []string
}

type discoveredIndexPath struct {
	relative string
	name     string
	identity string
	size     int64
	mtimeNS  int64
}

type searchIndexSnapshot struct {
	generation int
	identity   string
	entries    []discoveredIndexPath
}

func newSearchIndexEngine(store *searchIndexStore) *searchIndexEngine {
	engine := &searchIndexEngine{
		store: store, builds: make(map[string]bool), pendingBuild: make(map[string]indexBuildJob),
		buildDone: make(map[string]chan struct{}), rerun: make(map[string]indexBuildJob), fences: make(map[string]uint64), settingsRevisions: make(map[string]uint64), snapshots: make(map[string]*searchIndexSnapshot),
		buildCancels: make(map[string]context.CancelFunc),
		incarnation:  fmt.Sprintf("%d-%d", os.Getpid(), time.Now().UnixNano()),
	}
	rows, err := store.db.Query(`SELECT root,revision FROM settings_fences`)
	if err != nil {
		engine.fenceLoadErr = err
		return engine
	}
	defer rows.Close()
	for rows.Next() {
		var root string
		var revision uint64
		if err := rows.Scan(&root, &revision); err != nil {
			engine.fenceLoadErr = err
			return engine
		}
		engine.settingsRevisions[root] = revision
	}
	if err := rows.Err(); err != nil {
		engine.fenceLoadErr = err
	}
	if err := rows.Close(); err != nil {
		engine.fenceLoadErr = err
	}
	if err := store.db.QueryRow(`SELECT revision FROM settings_state WHERE singleton=1`).Scan(&engine.settingsFloor); err == nil {
		engine.settingsFloorSet = true
	} else if !errors.Is(err, sql.ErrNoRows) {
		engine.fenceLoadErr = err
	}
	return engine
}

func (engine *searchIndexEngine) handle(request indexRequest) (map[string]any, error) {
	return engine.handleContext(context.Background(), request)
}

func (engine *searchIndexEngine) handleContext(ctx context.Context, request indexRequest) (map[string]any, error) {
	if engine.fenceLoadErr != nil {
		return nil, fmt.Errorf("search index settings fences unavailable: %w", engine.fenceLoadErr)
	}
	if request.Action == "purge" {
		resume := engine.pauseBuildAdmission()
		defer resume()
		if err := engine.quiesceBuilds(); err != nil {
			return nil, err
		}
		if err := engine.removeSnapshots(nil, true); err != nil {
			return nil, err
		}
		return map[string]any{"ok": true, "purged": true}, nil
	}
	if request.Action == "settings_fence" {
		return engine.fenceSettings(request.FenceRoots, request.SettingsRevision, request.Invalidate, request.PreserveSnapshots)
	}
	root, err := filepath.Abs(request.Root)
	if err != nil || request.Root == "" {
		return nil, errors.New("root is required")
	}
	root = filepath.Clean(root)
	switch request.Action {
	case "status", "search", "ensure", "repair":
		info, err := os.Stat(root)
		if err != nil || !info.IsDir() {
			return nil, errors.New("root is unavailable")
		}
		identity := fileID(info)
		if request.Identity != "" && request.Identity != identity {
			return nil, errors.New("root identity changed")
		}
		if request.Action == "ensure" || request.Action == "search" || request.Action == "repair" {
			refreshSec := request.RefreshSec
			if request.Action == "repair" {
				refreshSec = 0
			}
			engine.schedule(root, identity, request.MaxFiles, request.MaxBytes, refreshSec, request.ExcludeDirs, request.ExcludePaths, request.SettingsRevision)
		}
		if request.Action == "search" {
			return engine.searchContext(ctx, root, identity, request.Query, request.Limit, request.Cursor, request.SettingsRevision)
		}
		return engine.status(root, identity)
	case "unindex", "fence":
		resume := engine.pauseBuildAdmission()
		defer resume()
		storedTargets, err := engine.storedFenceTargets(root, request.Action == "fence")
		if err != nil {
			return nil, err
		}
		engine.mu.Lock()
		if request.Action == "fence" {
			if _, err := engine.store.db.Exec(`INSERT INTO settings_state(singleton,revision) VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision`, request.SettingsRevision); err != nil {
				engine.mu.Unlock()
				return nil, err
			}
			engine.settingsFloor = request.SettingsRevision
			engine.settingsFloorSet = true
			if _, err := engine.store.db.Exec(`INSERT INTO settings_fences(root,revision) VALUES(?,?) ON CONFLICT(root) DO UPDATE SET revision=excluded.revision`, root, request.SettingsRevision); err != nil {
				engine.mu.Unlock()
				return nil, err
			}
			engine.settingsRevisions[root] = request.SettingsRevision
		}
		targets := storedTargets
		for candidate := range engine.builds {
			if indexRootWithin(root, candidate) {
				targets[candidate] = struct{}{}
			}
		}
		for candidate := range engine.pendingBuild {
			if indexRootWithin(root, candidate) {
				targets[candidate] = struct{}{}
			}
		}
		done := make([]chan struct{}, 0, len(targets))
		for target := range targets {
			engine.fences[target]++
			delete(engine.rerun, target)
			if _, queued := engine.pendingBuild[target]; queued {
				delete(engine.pendingBuild, target)
				delete(engine.builds, target)
				close(engine.buildDone[target])
				delete(engine.buildDone, target)
				continue
			}
			if buildDone := engine.buildDone[target]; buildDone != nil {
				if cancel := engine.buildCancels[target]; cancel != nil {
					cancel()
				}
				done = append(done, buildDone)
			}
		}
		engine.mu.Unlock()
		for _, buildDone := range done {
			<-buildDone
		}
		if err := engine.removeSnapshots(targets, false); err != nil {
			return nil, err
		}
		state := "not_indexed"
		if request.Action == "fence" {
			state = "warming"
		}
		return map[string]any{"root": root, "ok": true, "indexed": false, "index_state": state}, nil
	default:
		return nil, fmt.Errorf("unknown worker action %q", request.Action)
	}
}

func (engine *searchIndexEngine) fenceSettings(roots []string, revision uint64, invalidate bool, preserveRequested ...bool) (map[string]any, error) {
	preserveSnapshots := len(preserveRequested) > 0 && preserveRequested[0]
	resume := engine.pauseBuildAdmission()
	defer resume()
	targets := make(map[string]struct{})
	normalizedRoots := make([]string, 0, len(roots))
	for _, root := range roots {
		absolute, err := filepath.Abs(root)
		if err != nil || strings.TrimSpace(root) == "" {
			return nil, errors.New("invalid root in settings fence")
		}
		absolute = filepath.Clean(absolute)
		normalizedRoots = append(normalizedRoots, absolute)
		if invalidate {
			targets[absolute] = struct{}{}
		}
	}
	engine.mu.Lock()
	if _, err := engine.store.db.Exec(`INSERT INTO settings_state(singleton,revision) VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision`, revision); err != nil {
		engine.mu.Unlock()
		return nil, err
	}
	engine.settingsFloor = revision
	engine.settingsFloorSet = true
	for candidate := range engine.builds {
		jobRevision := engine.activeSettingsRevision
		if candidate != engine.activeRoot {
			jobRevision = engine.pendingBuild[candidate].settingsRevision
		}
		if (invalidate && indexRootWithinAny(normalizedRoots, candidate)) || (!invalidate && (jobRevision != revision || !indexRootConfigured(normalizedRoots, candidate))) {
			targets[candidate] = struct{}{}
		}
	}
	for candidate := range engine.pendingBuild {
		job := engine.pendingBuild[candidate]
		if (invalidate && indexRootWithinAny(normalizedRoots, candidate)) || (!invalidate && (job.settingsRevision != revision || !indexRootConfigured(normalizedRoots, candidate))) {
			targets[candidate] = struct{}{}
		}
	}
	done := make([]chan struct{}, 0, len(targets))
	for target := range targets {
		engine.fences[target]++
		delete(engine.rerun, target)
		if _, queued := engine.pendingBuild[target]; queued {
			delete(engine.pendingBuild, target)
			delete(engine.builds, target)
			close(engine.buildDone[target])
			delete(engine.buildDone, target)
			continue
		}
		if buildDone := engine.buildDone[target]; buildDone != nil {
			if cancel := engine.buildCancels[target]; cancel != nil {
				cancel()
			}
			done = append(done, buildDone)
		}
	}
	engine.mu.Unlock()
	for _, buildDone := range done {
		<-buildDone
	}
	if invalidate {
		for _, root := range normalizedRoots {
			stored, err := engine.storedFenceTargets(root, true)
			if err != nil {
				return nil, err
			}
			for target := range stored {
				targets[target] = struct{}{}
			}
		}
	} else {
		stale, err := engine.storedSettingsReconcileTargets(normalizedRoots, revision)
		if err != nil {
			return nil, err
		}
		for target := range stale {
			if !preserveSnapshots || !indexRootConfigured(normalizedRoots, target) {
				targets[target] = struct{}{}
			}
		}
	}
	removeTargets := targets
	if preserveSnapshots && !invalidate {
		removeTargets = make(map[string]struct{})
		for target := range targets {
			if !indexRootConfigured(normalizedRoots, target) {
				removeTargets[target] = struct{}{}
			}
		}
	}
	if err := engine.removeSnapshots(removeTargets, false); err != nil {
		return nil, err
	}
	if preserveSnapshots {
		engine.crawlPause.RLock()
		rows, err := engine.store.db.Query(`SELECT root FROM roots`)
		if err != nil {
			engine.crawlPause.RUnlock()
			return nil, err
		}
		retainedRoots := make([]string, 0)
		for rows.Next() {
			var root string
			if err := rows.Scan(&root); err != nil {
				_ = rows.Close()
				engine.crawlPause.RUnlock()
				return nil, err
			}
			if indexRootConfigured(normalizedRoots, root) {
				retainedRoots = append(retainedRoots, root)
			}
		}
		if err := rows.Err(); err != nil {
			_ = rows.Close()
			engine.crawlPause.RUnlock()
			return nil, err
		}
		if err := rows.Close(); err != nil {
			engine.crawlPause.RUnlock()
			return nil, err
		}
		for _, root := range retainedRoots {
			if _, err := engine.store.db.Exec(`UPDATE roots SET settings_revision=?,state=CASE WHEN generation>0 THEN 'warming' ELSE state END WHERE root=?`, revision, root); err != nil {
				engine.crawlPause.RUnlock()
				return nil, err
			}
		}
		engine.crawlPause.RUnlock()
	}
	return map[string]any{"ok": true, "revision": revision, "fenced_roots": len(targets)}, nil
}

func (engine *searchIndexEngine) pauseBuildAdmission() func() {
	// Keep scheduling closed from target discovery through deletion. A new-revision
	// request must not start a replacement crawl after the affected builds drain.
	engine.mu.Lock()
	engine.snapshotMutations++
	engine.mu.Unlock()
	return func() {
		engine.mu.Lock()
		engine.snapshotMutations--
		engine.mu.Unlock()
	}
}

func (engine *searchIndexEngine) removeSnapshots(targets map[string]struct{}, purge bool) error {
	// Fences are already installed and builds quiesced. Drain readers before
	// removing their selected rows, and hold the fence through the acknowledgment.
	engine.crawlPause.Lock()
	defer engine.crawlPause.Unlock()
	if purge {
		for _, statement := range []string{`PRAGMA secure_delete=ON`, `DELETE FROM entries`, `DELETE FROM roots`, `DELETE FROM root_generations`, `VACUUM`} {
			if _, err := engine.store.db.Exec(statement); err != nil {
				return err
			}
		}
		engine.mu.Lock()
		clear(engine.snapshots)
		engine.mu.Unlock()
		return nil
	}
	for target := range targets {
		if _, err := engine.store.db.Exec(`DELETE FROM entries WHERE root=?`, target); err != nil {
			return err
		}
		if _, err := engine.store.db.Exec(`DELETE FROM roots WHERE root=?`, target); err != nil {
			return err
		}
		engine.mu.Lock()
		delete(engine.snapshots, target)
		engine.mu.Unlock()
	}
	return nil
}

func indexRootWithinAny(parents []string, candidate string) bool {
	for _, parent := range parents {
		if indexRootWithin(parent, candidate) {
			return true
		}
	}
	return false
}

func indexRootConfigured(configuredRoots []string, candidate string) bool {
	return indexRootWithinAny(configuredRoots, candidate)
}

func (engine *searchIndexEngine) storedSettingsReconcileTargets(configuredRoots []string, revision uint64) (map[string]struct{}, error) {
	rows, err := engine.store.db.Query(`SELECT root,settings_revision FROM roots`)
	if err != nil {
		return nil, err
	}
	targets := make(map[string]struct{})
	for rows.Next() {
		var root string
		var storedRevision uint64
		if err := rows.Scan(&root, &storedRevision); err != nil {
			_ = rows.Close()
			return nil, err
		}
		if storedRevision != revision || !indexRootConfigured(configuredRoots, root) {
			targets[root] = struct{}{}
		}
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return nil, err
	}
	if err := rows.Close(); err != nil {
		return nil, err
	}
	return targets, nil
}

func (engine *searchIndexEngine) storedFenceTargets(root string, includeDescendants bool) (map[string]struct{}, error) {
	targets := map[string]struct{}{root: {}}
	if !includeDescendants {
		return targets, nil
	}
	rows, err := engine.store.db.Query(`SELECT root FROM roots UNION SELECT root FROM root_generations`)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var candidate string
		if err := rows.Scan(&candidate); err != nil {
			_ = rows.Close()
			return nil, err
		}
		if indexRootWithin(root, candidate) {
			targets[candidate] = struct{}{}
		}
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return nil, err
	}
	if err := rows.Close(); err != nil {
		return nil, err
	}
	return targets, nil
}

func indexRootWithin(parent, candidate string) bool {
	relative, err := filepath.Rel(parent, candidate)
	return err == nil && (relative == "." || (relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))))
}

func (engine *searchIndexEngine) quiesceBuilds() error {
	rows, err := engine.store.db.Query(`SELECT root FROM roots UNION SELECT root FROM root_generations`)
	if err != nil {
		return err
	}
	roots := make(map[string]struct{})
	for rows.Next() {
		var root string
		if err := rows.Scan(&root); err != nil {
			_ = rows.Close()
			return err
		}
		roots[root] = struct{}{}
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return err
	}
	if err := rows.Close(); err != nil {
		return err
	}

	engine.mu.Lock()
	if engine.activeRoot != "" {
		roots[engine.activeRoot] = struct{}{}
	}
	for root := range engine.pendingBuild {
		roots[root] = struct{}{}
	}
	for root := range engine.builds {
		roots[root] = struct{}{}
	}
	done := make([]chan struct{}, 0, len(roots))
	for root := range roots {
		engine.fences[root]++
		delete(engine.rerun, root)
		if _, queued := engine.pendingBuild[root]; queued {
			delete(engine.pendingBuild, root)
			delete(engine.builds, root)
			if buildDone := engine.buildDone[root]; buildDone != nil {
				close(buildDone)
				delete(engine.buildDone, root)
			}
			continue
		}
		if buildDone := engine.buildDone[root]; buildDone != nil {
			if cancel := engine.buildCancels[root]; cancel != nil {
				cancel()
			}
			done = append(done, buildDone)
		}
	}
	engine.mu.Unlock()
	for _, buildDone := range done {
		<-buildDone
	}
	return nil
}

// Free pages remain allocated on disk, but SQLite reuses them without growing the file.
func (store *searchIndexStore) usedBytes() (int64, error) {
	var pages, free, size int64
	for _, item := range []struct {
		query string
		value *int64
	}{
		{"PRAGMA page_count", &pages}, {"PRAGMA freelist_count", &free}, {"PRAGMA page_size", &size},
	} {
		if err := store.db.QueryRow(item.query).Scan(item.value); err != nil {
			return 0, err
		}
	}
	return (pages - free) * size, nil
}

func (store *searchIndexStore) deleteGenerations(root string, keep int) error {
	tx, err := store.db.Begin()
	if err != nil {
		return err
	}
	if _, err := tx.Exec(`DELETE FROM entries WHERE root=? AND generation<>?`, root, keep); err != nil {
		_ = tx.Rollback()
		return err
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	_, err = store.db.Exec(`PRAGMA wal_checkpoint(TRUNCATE)`)
	return err
}

func (store *searchIndexStore) reclaimFreePages() error {
	var free int
	if err := store.db.QueryRow(`PRAGMA freelist_count`).Scan(&free); err != nil {
		return err
	}
	if free == 0 {
		return nil
	}
	rows, err := store.db.Query(`PRAGMA incremental_vacuum`)
	if err != nil {
		return err
	}
	for rows.Next() {
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return err
	}
	if err := rows.Close(); err != nil {
		return err
	}
	_, err = store.db.Exec(`PRAGMA wal_checkpoint(TRUNCATE)`)
	return err
}

func (engine *searchIndexEngine) schedule(root, identity string, maxFiles int, maxBytes int64, refreshSec int, excludeDirs, excludePaths []string, settingsRevision uint64) {
	if maxFiles < 1 || maxFiles > DefaultIndexedFiles {
		maxFiles = DefaultIndexedFiles
	}
	if maxBytes < 1 || maxBytes > MaxIndexBytes {
		maxBytes = DefaultIndexBytes
	}
	if !engine.needsRefresh(root, identity, refreshSec, settingsRevision, time.Now()) {
		return
	}
	engine.mu.Lock()
	if engine.snapshotMutations > 0 || !engine.settingsRevisionAcceptedLocked(root, settingsRevision) {
		engine.mu.Unlock()
		return
	}
	job := indexBuildJob{root: root, identity: identity, maxFiles: maxFiles, maxBytes: maxBytes, fence: engine.fences[root], settingsRevision: settingsRevision, excludeDirs: make(map[string]struct{}, len(excludeDirs)), excludePaths: append([]string(nil), excludePaths...)}
	for _, name := range excludeDirs {
		if strings.TrimSpace(name) != "" {
			job.excludeDirs[name] = struct{}{}
		}
	}
	if engine.builds[root] {
		if refreshSec <= 0 {
			engine.rerun[root] = job
		}
		engine.mu.Unlock()
		return
	}
	engine.builds[root] = true
	engine.buildDone[root] = make(chan struct{})
	if engine.activeBuild {
		engine.pendingBuild[root] = job
		engine.mu.Unlock()
		return
	}
	engine.activeBuild = true
	engine.activeRoot = root
	engine.activeSettingsRevision = job.settingsRevision
	engine.mu.Unlock()
	go engine.runBuilds(job)
}

func (engine *searchIndexEngine) settingsRevisionAcceptedLocked(root string, settingsRevision uint64) bool {
	if engine.settingsFloorSet && settingsRevision != engine.settingsFloor {
		return false
	}
	for fenceRoot, acceptedRevision := range engine.settingsRevisions {
		if indexRootWithin(fenceRoot, root) && !engine.settingsFloorSet && settingsRevision != acceptedRevision {
			return false
		}
	}
	return true
}

func (engine *searchIndexEngine) needsRefresh(root, identity string, refreshSec int, settingsRevision uint64, now time.Time) bool {
	if refreshSec <= 0 {
		return true
	}
	var storedIdentity, state, lastError string
	var count int
	var storedRevision uint64
	var updatedNS int64
	statusErr := engine.store.db.QueryRow(`SELECT identity,state,settings_revision,updated_ns,count,error FROM roots WHERE root=?`, root).Scan(&storedIdentity, &state, &storedRevision, &updatedNS, &count, &lastError)
	return statusErr != nil || state == "warming" || (state == "partial" && count == 0 && lastError == "") || storedIdentity != identity || storedRevision != settingsRevision || now.Sub(time.Unix(0, updatedNS)) >= time.Duration(refreshSec)*time.Second
}

func (engine *searchIndexEngine) runBuilds(job indexBuildJob) {
	for {
		buildContext, cancel := context.WithCancel(context.Background())
		engine.mu.Lock()
		engine.buildCancels[job.root] = cancel
		engine.mu.Unlock()
		if err := engine.buildContext(buildContext, job); err != nil && !errors.Is(err, context.Canceled) {
			slog.Error("search index build failed", "root", job.root, "settings_revision", job.settingsRevision, "error", err)
		}
		cancel()
		engine.mu.Lock()
		delete(engine.buildCancels, job.root)
		if rerun, exists := engine.rerun[job.root]; exists && rerun.fence == engine.fences[job.root] {
			delete(engine.rerun, job.root)
			engine.activeSettingsRevision = rerun.settingsRevision
			engine.mu.Unlock()
			job = rerun
			continue
		}
		delete(engine.rerun, job.root)
		delete(engine.builds, job.root)
		close(engine.buildDone[job.root])
		delete(engine.buildDone, job.root)
		var next indexBuildJob
		found := false
		for root, candidate := range engine.pendingBuild {
			if !found || root < next.root {
				next, found = candidate, true
			}
		}
		if found {
			delete(engine.pendingBuild, next.root)
			engine.activeRoot = next.root
			engine.activeSettingsRevision = next.settingsRevision
			engine.mu.Unlock()
			job = next
			continue
		}
		engine.activeBuild = false
		engine.activeRoot = ""
		engine.activeSettingsRevision = 0
		engine.mu.Unlock()
		return
	}
}

func (engine *searchIndexEngine) fenced(root string, fence, settingsRevision uint64) bool {
	engine.mu.Lock()
	defer engine.mu.Unlock()
	return engine.fences[root] != fence || !engine.settingsRevisionAcceptedLocked(root, settingsRevision)
}

func hasName(names map[string]struct{}, name string) bool {
	_, exists := names[name]
	return exists
}

func indexDirectoryQueueFits(queueCount int, queuedPathBytes int64, absolute, relative string) bool {
	return queueCount < indexQueueLimit && queuedPathBytes+int64(len(absolute)+len(relative)) <= indexQueueByteLimit
}

func indexEntryInfo(entry os.DirEntry, excludedNames map[string]struct{}) (os.FileInfo, bool, error) {
	info, err := entry.Info()
	if err != nil {
		return nil, false, err
	}
	excludedDirectory := info.IsDir() && (isSearchExcludedDirectory(entry.Name()) || hasName(excludedNames, entry.Name()))
	return info, excludedDirectory, nil
}

func excludedIndexPath(root, path string, exclusions []string) bool {
	home, _ := os.UserHomeDir()
	root, _ = filepath.Abs(root)
	path, _ = filepath.Abs(path)
	root, path = filepath.Clean(root), filepath.Clean(path)
	relatives := []string{}
	if relative, err := filepath.Rel(root, path); err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		relative = filepath.ToSlash(relative)
		if !containsString(relatives, relative) {
			relatives = append(relatives, relative)
		}
	}
	for _, raw := range exclusions {
		rule := strings.TrimSpace(raw)
		if rule == "" {
			continue
		}
		if rule == "~" {
			rule = home
		} else if strings.HasPrefix(rule, "~/") {
			rule = filepath.Join(home, rule[2:])
		}
		if strings.HasPrefix(rule, "glob:") {
			pattern := strings.TrimLeft(strings.ReplaceAll(strings.TrimSpace(strings.TrimPrefix(rule, "glob:")), "\\", "/"), "/")
			if pattern == "" {
				continue
			}
			matcher, err := regexp.Compile(globPatternRegex(pattern))
			if err != nil {
				continue
			}
			for _, relative := range relatives {
				for _, candidate := range []string{relative, "_/" + relative, relative + "/", "_/" + relative + "/"} {
					if matcher.MatchString(candidate) {
						return true
					}
				}
			}
			continue
		}
		if strings.HasPrefix(rule, "regex:") {
			matcher, err := regexp.Compile(strings.TrimSpace(strings.TrimPrefix(rule, "regex:")))
			if err != nil {
				continue
			}
			for _, relative := range relatives {
				if matcher.MatchString(relative) {
					return true
				}
			}
			continue
		}
		if !filepath.IsAbs(rule) {
			rule = filepath.Join(root, rule)
		}
		relative, err := filepath.Rel(filepath.Clean(rule), filepath.Clean(path))
		if err == nil && (relative == "." || (relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)))) {
			return true
		}
	}
	return false
}

func globPatternRegex(pattern string) string {
	var builder strings.Builder
	builder.WriteByte('^')
	for index := 0; index < len(pattern); index++ {
		switch pattern[index] {
		case '*':
			if index+1 < len(pattern) && pattern[index+1] == '*' {
				index++
				if index+1 < len(pattern) && pattern[index+1] == '/' {
					index++
					builder.WriteString("(?:.*/)?")
				} else {
					builder.WriteString(".*")
				}
			} else {
				builder.WriteString("[^/]*")
			}
		case '?':
			builder.WriteString("[^/]")
		default:
			builder.WriteString(regexp.QuoteMeta(string(pattern[index])))
		}
	}
	builder.WriteByte('$')
	return builder.String()
}

func (engine *searchIndexEngine) status(root, identity string) (map[string]any, error) {
	var generation, count int
	var scanDurationNS, lastSuccessNS int64
	var state, coverage, storedIdentity, lastError string
	err := engine.store.db.QueryRow(`SELECT generation,count,state,coverage,identity,error,scan_duration_ns,last_success_ns FROM roots WHERE root=?`, root).Scan(&generation, &count, &state, &coverage, &storedIdentity, &lastError, &scanDurationNS, &lastSuccessNS)
	if errors.Is(err, sql.ErrNoRows) || (err == nil && storedIdentity != identity) {
		return map[string]any{"root": root, "status": "warming", "index_state": "warming", "indexed": false, "ready": false, "generation": 0, "count": 0, "worker_incarnation": engine.incarnation, "index_coverage": "pending", "discovery_coverage": "pending", "enrichment_state": "disabled", "scan_duration_ms": float64(0), "last_successful_scan": "", "progressive_coverage": map[string]any{"full_coverage": false}}, nil
	}
	if err != nil {
		return nil, err
	}
	engine.mu.Lock()
	building := engine.builds[root]
	engine.mu.Unlock()
	lastSuccessfulScan := ""
	if lastSuccessNS > 0 {
		scanTime := time.Unix(0, lastSuccessNS)
		if location, err := time.LoadLocation("America/Los_Angeles"); err == nil {
			scanTime = scanTime.In(location)
		} else {
			scanTime = scanTime.Local()
		}
		lastSuccessfulScan = scanTime.Format(time.RFC3339Nano)
	}
	return map[string]any{"root": root, "status": state, "index_state": state, "indexed": generation > 0, "ready": generation > 0, "building": building, "generation": generation, "count": count, "error": lastError, "coverage_reason": lastError, "stale": state == "error", "refreshing": building, "freshness": map[bool]string{true: "stale", false: "fresh"}[state == "error"], "worker_incarnation": engine.incarnation, "index_coverage": coverage, "discovery_coverage": coverage, "enrichment_state": "disabled", "scan_duration_ms": float64(scanDurationNS) / float64(time.Millisecond), "last_successful_scan": lastSuccessfulScan, "progressive_coverage": map[string]any{"full_coverage": coverage == "complete", "visited_files": count}}, nil
}

func (engine *searchIndexEngine) build(job indexBuildJob) error {
	return engine.buildContext(context.Background(), job)
}

func (engine *searchIndexEngine) buildContext(ctx context.Context, job indexBuildJob) error {
	started := time.Now()
	root, identity, maxFiles, maxBytes := job.root, job.identity, job.maxFiles, job.maxBytes
	if err := ctx.Err(); err != nil {
		return err
	}
	if engine.fenced(root, job.fence, job.settingsRevision) {
		return nil
	}
	var prior int
	_ = engine.store.db.QueryRow(`SELECT generation FROM roots WHERE root=? AND identity=?`, root, identity).Scan(&prior)
	if _, err := engine.store.db.Exec(`PRAGMA wal_checkpoint(TRUNCATE)`); err != nil {
		return engine.fail(root, identity, prior, err)
	}
	deleteErr := engine.store.deleteGenerations(root, prior)
	if deleteErr != nil {
		return engine.fail(root, identity, prior, deleteErr)
	}
	if err := engine.store.setStorageBudget(maxBytes * 2); err != nil {
		return engine.fail(root, identity, prior, err)
	}
	rootInfo, err := os.Stat(root)
	if err != nil || fileID(rootInfo) != identity {
		return errors.New("root identity changed before crawl")
	}
	paths, coverage, coverageReason, err := discoverIndexPaths(ctx, root, job.excludeDirs, job.excludePaths, maxFiles)
	if err != nil {
		return engine.fail(root, identity, prior, err)
	}
	if prior > 0 && coverage == "partial" {
		if coverageReason == "" {
			coverageReason = "discovery traversal was incomplete"
		}
		return engine.fail(root, identity, prior, errors.New(coverageReason))
	}
	if engine.fenced(root, job.fence, job.settingsRevision) {
		return nil
	}
	currentInfo, statErr := os.Stat(root)
	if statErr != nil || fileID(currentInfo) != identity {
		return engine.fail(root, identity, prior, errors.New("root identity changed before publish"))
	}
	var otherSnapshotBytes int64
	if err := engine.store.db.QueryRow(`SELECT COALESCE(SUM(bytes),0) FROM roots WHERE root<>?`, root).Scan(&otherSnapshotBytes); err != nil {
		return err
	}
	estimatedBytes := int64(4096)
	for _, path := range paths {
		estimatedBytes += int64(len(path.relative) + len(path.name) + 256)
	}
	storageLimit := maxBytes
	if globalLimit := indexStorageLimit(maxBytes*2) - otherSnapshotBytes; globalLimit < storageLimit {
		storageLimit = globalLimit
	}
	if estimatedBytes > storageLimit {
		if prior > 0 {
			return engine.fail(root, identity, prior, errors.New("snapshot storage budget reached"))
		}
		trimmed := paths[:0]
		used := int64(4096)
		for _, path := range paths {
			entryBytes := int64(len(path.relative) + len(path.name) + 256)
			if used+entryBytes > storageLimit {
				break
			}
			trimmed = append(trimmed, path)
			used += entryBytes
		}
		paths = trimmed
		coverage, coverageReason = "partial", "snapshot storage budget reached"
	}
	var generation int
	if err := engine.store.db.QueryRow(`SELECT generation FROM root_generations WHERE root=?`, root).Scan(&generation); errors.Is(err, sql.ErrNoRows) {
		generation = prior
	} else if err != nil {
		return err
	}
	generation++
	if _, err := engine.store.db.Exec(`INSERT INTO root_generations(root,generation) VALUES(?,?) ON CONFLICT(root) DO UPDATE SET generation=excluded.generation`, root, generation); err != nil {
		return err
	}
	if _, err := engine.store.db.Exec(`INSERT INTO roots(root,identity,generation,state,coverage,count,bytes,updated_ns,error,settings_revision) VALUES(?,?,?,'warming','pending',0,0,?,'',0) ON CONFLICT(root) DO UPDATE SET identity=excluded.identity,state=CASE WHEN generation=0 THEN 'warming' ELSE state END,error=''`, root, identity, prior, time.Now().UnixNano()); err != nil {
		return err
	}
	for {
		err := engine.writeDiscoveredPaths(root, generation, paths)
		if err == nil {
			break
		}
		if !isSQLiteStorageFull(err) || prior > 0 || len(paths) == 0 {
			return engine.fail(root, identity, prior, err)
		}
		paths = paths[:len(paths)/2]
		coverage, coverageReason = "partial", "SQLite storage budget reached"
	}
	if engine.fenced(root, job.fence, job.settingsRevision) {
		_ = engine.store.deleteGenerations(root, prior)
		return nil
	}
	state := "ready"
	if coverage == "partial" {
		state = "partial"
		if coverageReason == "" {
			coverageReason = "some eligible paths could not be traversed"
		}
	}
	snapshotBytes := int64(4096)
	for _, path := range paths {
		snapshotBytes += int64(len(path.relative) + len(path.name) + 256)
	}
	snapshot := &searchIndexSnapshot{generation: generation, identity: identity, entries: paths}
	published, err := engine.publishGeneration(job, generation, state, coverage, len(paths), snapshotBytes, coverageReason, time.Since(started), snapshot)
	if err != nil {
		return engine.fail(root, identity, prior, err)
	}
	if !published {
		return nil
	}
	deleteErr = engine.store.deleteGenerations(root, generation)
	if deleteErr != nil {
		return deleteErr
	}
	return engine.store.reclaimFreePages()
}

func (engine *searchIndexEngine) writeDiscoveredPaths(root string, generation int, paths []discoveredIndexPath) error {
	transaction, err := engine.store.db.Begin()
	if err != nil {
		return err
	}
	statement, err := transaction.Prepare(`INSERT INTO entries(root,generation,relative_path,name,file_identity,size,mtime_ns) VALUES(?,?,?,?,'',0,0)`)
	if err != nil {
		_ = transaction.Rollback()
		return err
	}
	for _, path := range paths {
		if _, err := statement.Exec(root, generation, path.relative, path.name); err != nil {
			_ = statement.Close()
			_ = transaction.Rollback()
			return err
		}
	}
	if err := statement.Close(); err != nil {
		_ = transaction.Rollback()
		return err
	}
	return transaction.Commit()
}

func discoverIndexPaths(ctx context.Context, root string, excludedNames map[string]struct{}, excludedPaths []string, maxFiles int) ([]discoveredIndexPath, string, string, error) {
	if maxFiles < 1 || maxFiles > DefaultIndexedFiles {
		maxFiles = DefaultIndexedFiles
	}
	binary, err := exec.LookPath("find")
	paths := make([]discoveredIndexPath, 0, min(maxFiles, 65536))
	coverage, reason := "complete", ""
	appendPath := func(path string) bool {
		if path == "" || excludedIndexPath(root, path, excludedPaths) {
			return true
		}
		relative, err := filepath.Rel(root, path)
		if err != nil || relative == "." || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			return true
		}
		if len(paths) == maxFiles {
			coverage, reason = "partial", "per-root file limit reached"
			return false
		}
		name := filepath.Base(path)
		paths = append(paths, discoveredIndexPath{relative: filepath.ToSlash(relative), name: name})
		return true
	}
	if err == nil {
		args := []string{root, "(", "-type", "d", "(", "-name", ".git"}
		for name := range searchExcludedDirectoryNames {
			if name == ".git" {
				continue
			}
			args = append(args, "-o", "-name", name)
		}
		for name := range excludedNames {
			if name == ".git" || hasName(searchExcludedDirectoryNames, name) {
				continue
			}
			if name != "" {
				args = append(args, "-o", "-name", name)
			}
		}
		for _, raw := range excludedPaths {
			rule := strings.TrimSpace(raw)
			if rule == "" || strings.HasPrefix(rule, "regex:") {
				continue
			}
			if strings.HasPrefix(rule, "glob:") {
				pattern := strings.TrimLeft(strings.ReplaceAll(strings.TrimPrefix(rule, "glob:"), "\\", "/"), "/")
				if pattern != "" && !strings.Contains(pattern, "?") && !strings.Contains(strings.ReplaceAll(pattern, "**", ""), "*") {
					args = append(args, "-o", "-path", filepath.Join(root, filepath.FromSlash(pattern)))
				}
				continue
			}
			if rule == "~" {
				rule, _ = os.UserHomeDir()
			} else if strings.HasPrefix(rule, "~/") {
				home, _ := os.UserHomeDir()
				rule = filepath.Join(home, rule[2:])
			} else if !filepath.IsAbs(rule) {
				rule = filepath.Join(root, rule)
			}
			args = append(args, "-o", "-path", filepath.Clean(rule))
		}
		args = append(args, ")", "-prune", ")", "-o", "(", "-type", "f", "-print0", ")")
		command := exec.CommandContext(ctx, binary, args...)
		stdout, err := command.StdoutPipe()
		if err != nil {
			return nil, "", "", err
		}
		var stderr strings.Builder
		command.Stderr = &stderr
		if err := command.Start(); err != nil {
			return nil, "", "", err
		}
		reader := bufio.NewReaderSize(stdout, 256*1024)
		for {
			path, readErr := reader.ReadBytes(0)
			if len(path) > 0 {
				path = path[:len(path)-1]
				if !appendPath(string(path)) {
					_ = command.Process.Kill()
					break
				}
			}
			if readErr != nil {
				if !errors.Is(readErr, io.EOF) {
					_ = command.Process.Kill()
					_ = command.Wait()
					return nil, "", "", readErr
				}
				break
			}
		}
		waitErr := command.Wait()
		if waitErr != nil {
			if ctx.Err() != nil {
				return nil, "", "", ctx.Err()
			}
			if coverage == "complete" {
				coverage = "partial"
				reason = strings.TrimSpace(stderr.String())
			}
		}
		return paths, coverage, reason, nil
	}
	walkErr := filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if walkErr != nil {
			coverage, reason = "partial", walkErr.Error()
			return nil
		}
		relative, relErr := filepath.Rel(root, path)
		if relErr != nil {
			coverage, reason = "partial", relErr.Error()
			return nil
		}
		if entry.IsDir() && path != root && (isSearchExcludedDirectory(entry.Name()) || hasName(excludedNames, entry.Name()) || excludedIndexPath(root, path, excludedPaths)) {
			return filepath.SkipDir
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return nil
		}
		if !entry.IsDir() && relative != "." && !excludedIndexPath(root, path, excludedPaths) && !appendPath(path) {
			return filepath.SkipAll
		}
		return nil
	})
	if errors.Is(walkErr, filepath.SkipAll) {
		return paths, coverage, reason, nil
	}
	if walkErr != nil {
		if ctx.Err() != nil {
			return nil, "", "", ctx.Err()
		}
		coverage, reason = "partial", walkErr.Error()
	}
	return paths, coverage, reason, nil
}

func (engine *searchIndexEngine) publishGeneration(job indexBuildJob, generation int, state, coverage string, count int, bytes int64, reason string, duration time.Duration, snapshot *searchIndexSnapshot) (bool, error) {
	// Readers pin metadata and rows with the exclusive lock. Wait for them before
	// advancing the generation, so subsequent reclamation cannot remove their rows.
	engine.crawlPause.Lock()
	defer engine.crawlPause.Unlock()
	// Keep the settings/root fence locked through publication, including transaction commit.
	engine.mu.Lock()
	defer engine.mu.Unlock()
	if engine.fences[job.root] != job.fence || !engine.settingsRevisionAcceptedLocked(job.root, job.settingsRevision) {
		return false, nil
	}
	publish, err := engine.store.db.Begin()
	if err == nil {
		var result sql.Result
		now := time.Now().UnixNano()
		result, err = publish.Exec(`UPDATE roots SET identity=?,generation=?,state=?,coverage=?,count=?,bytes=?,updated_ns=?,error=?,settings_revision=?,scan_duration_ns=?,last_success_ns=? WHERE root=?`, job.identity, generation, state, coverage, count, bytes, now, reason, job.settingsRevision, duration.Nanoseconds(), now, job.root)
		if err == nil {
			var affected int64
			affected, err = result.RowsAffected()
			if err == nil && affected != 1 {
				err = errors.New("root snapshot disappeared before publication")
			}
		}
		if err == nil {
			err = publish.Commit()
			if err == nil {
				engine.snapshots[job.root] = snapshot
			}
		} else {
			_ = publish.Rollback()
		}
	}
	return err == nil, err
}

func (engine *searchIndexEngine) writeBatch(batch [][]any) error {
	tx, err := engine.store.db.Begin()
	if err != nil {
		return err
	}
	statement, err := tx.Prepare(`INSERT OR REPLACE INTO entries(root,generation,relative_path,name,file_identity,size,mtime_ns) VALUES(?,?,?,?,?,?,?)`)
	if err != nil {
		_ = tx.Rollback()
		return err
	}
	for _, row := range batch {
		if _, err := statement.Exec(row...); err != nil {
			_ = statement.Close()
			_ = tx.Rollback()
			return err
		}
	}
	if err := statement.Close(); err != nil {
		_ = tx.Rollback()
		return err
	}
	return tx.Commit()
}

func (engine *searchIndexEngine) fail(root, identity string, prior int, cause error) error {
	_, _ = engine.store.db.Exec(`UPDATE roots SET identity=?,generation=?,state='error',error=?,updated_ns=? WHERE root=?`, identity, prior, cause.Error(), time.Now().UnixNano(), root)
	return cause
}

type rankedIndexEntry struct {
	key searchSortKey
	row map[string]any
}

type rankedIndexHeap []rankedIndexEntry

func (entries rankedIndexHeap) Len() int { return len(entries) }
func (entries rankedIndexHeap) Less(left, right int) bool {
	return compareSearchSortKey(entries[right].key, entries[left].key)
}
func (entries rankedIndexHeap) Swap(left, right int) {
	entries[left], entries[right] = entries[right], entries[left]
}
func (entries *rankedIndexHeap) Push(value any) {
	*entries = append(*entries, value.(rankedIndexEntry))
}
func (entries *rankedIndexHeap) Pop() any {
	old := *entries
	last := old[len(old)-1]
	*entries = old[:len(old)-1]
	return last
}

func (engine *searchIndexEngine) search(root, identity, query string, limit int, cursor string, settingsRevision uint64) (map[string]any, error) {
	return engine.searchContext(context.Background(), root, identity, query, limit, cursor, settingsRevision)
}

func (engine *searchIndexEngine) searchContext(ctx context.Context, root, identity, query string, limit int, cursor string, settingsRevision uint64) (map[string]any, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	engine.crawlPause.RLock()
	defer engine.crawlPause.RUnlock()
	engine.mu.Lock()
	settingsAccepted := engine.settingsRevisionAcceptedLocked(root, settingsRevision)
	engine.mu.Unlock()
	if !settingsAccepted {
		return map[string]any{
			"root": root, "query": strings.TrimSpace(strings.ToLower(query)), "files": []map[string]any{}, "count": 0,
			"complete": false, "truncated": false, "indexed": false, "ready": false, "index_state": "warming", "status": "warming",
			"index_coverage": "pending", "progressive_coverage": map[string]any{"full_coverage": false},
		}, nil
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if limit < 1 {
		limit = 1
	}
	if limit > MaxSearchResults {
		limit = MaxSearchResults
	}
	status, err := engine.status(root, identity)
	if err != nil {
		return nil, err
	}
	if status["indexed"] != true {
		status["query"] = strings.TrimSpace(strings.ToLower(query))
		status["files"] = []map[string]any{}
		status["count"] = 0
		status["complete"] = false
		status["truncated"] = false
		status["cursor"] = "0"
		status["generation"] = 0
		return status, nil
	}
	var generation int
	var storedRevision uint64
	if err := engine.store.db.QueryRow(`SELECT generation,settings_revision FROM roots WHERE root=? AND identity=?`, root, identity).Scan(&generation, &storedRevision); err != nil {
		return nil, err
	}
	if storedRevision != settingsRevision {
		status["indexed"], status["ready"], status["index_state"], status["status"] = false, false, "warming", "warming"
		status["files"], status["count"], status["index_coverage"] = []map[string]any{}, 0, "pending"
		status["progressive_coverage"] = map[string]any{"full_coverage": false}
		return status, nil
	}
	engine.mu.Lock()
	snapshot := engine.snapshots[root]
	engine.mu.Unlock()
	if snapshot == nil || snapshot.generation != generation || snapshot.identity != identity {
		loaded := &searchIndexSnapshot{generation: generation, identity: identity}
		rows, err := engine.store.db.QueryContext(ctx, `SELECT relative_path,name,file_identity,size,mtime_ns FROM entries WHERE root=? AND generation=?`, root, generation)
		if err != nil {
			return nil, err
		}
		for rows.Next() {
			var entry discoveredIndexPath
			if err := rows.Scan(&entry.relative, &entry.name, &entry.identity, &entry.size, &entry.mtimeNS); err != nil {
				_ = rows.Close()
				return nil, err
			}
			loaded.entries = append(loaded.entries, entry)
		}
		if err := rows.Err(); err != nil {
			_ = rows.Close()
			return nil, err
		}
		if err := rows.Close(); err != nil {
			return nil, err
		}
		snapshot = loaded
		engine.mu.Lock()
		engine.snapshots[root] = snapshot
		engine.mu.Unlock()
	}
	generationCursor := fmt.Sprintf("%s:%d", engine.incarnation, generation)
	if cursor != "" {
		cursorIncarnation, cursorGenerationText, hasIncarnation := strings.Cut(cursor, ":")
		cursorGeneration, parseErr := strconv.Atoi(cursorGenerationText)
		if hasIncarnation && cursorIncarnation == engine.incarnation && parseErr == nil && cursorGeneration == generation {
			status["query"], status["files"], status["count"], status["limit"] = strings.TrimSpace(strings.ToLower(query)), []map[string]any{}, 0, limit
			status["cursor"], status["generation"], status["unchanged"], status["rebase_required"] = generationCursor, generation, true, false
			status["changes"] = []map[string]any{}
			return status, nil
		}
		status["cursor"], status["generation"], status["rebase_required"], status["reason"] = generationCursor, generation, true, "generation_changed"
		return status, nil
	}
	tokens := strings.Fields(strings.ToLower(strings.TrimSpace(query)))
	preparedTokens := prepareSearchTokens(tokens)
	best := make(rankedIndexHeap, 0, limit)
	heap.Init(&best)
	matchedCount := 0
	for _, path := range snapshot.entries {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		key, matched := searchEntrySortKeyPrepared(path.name, path.relative, preparedTokens)
		if !matched {
			continue
		}
		matchedCount++
		if len(best) == limit && !compareSearchSortKey(key, best[0].key) {
			continue
		}
		absolute := filepath.Join(root, filepath.FromSlash(path.relative))
		row := map[string]any{"path": absolute, "name": path.name, "relative_path": path.relative, "kind": "file", "size": path.size, "mtime": path.mtimeNS / 1e9, "mtime_ns": path.mtimeNS, "realpath": absolute, "file_id": path.identity, "file_identity": "id:" + path.identity, "uploaded": generatedUploadName(path.name)}
		candidate := rankedIndexEntry{key: key, row: row}
		if len(best) < limit {
			heap.Push(&best, candidate)
		} else {
			heap.Pop(&best)
			heap.Push(&best, candidate)
		}
	}
	files := make([]map[string]any, 0, len(best))
	sort.Slice(best, func(left, right int) bool { return compareSearchSortKey(best[left].key, best[right].key) })
	for _, entry := range best {
		files = append(files, entry.row)
	}
	status["query"], status["files"], status["count"], status["limit"], status["complete"], status["truncated"], status["result_limit_hit"], status["recursive"] = strings.TrimSpace(strings.ToLower(query)), files, len(files), limit, true, matchedCount > limit, matchedCount > limit, true
	status["indexed"], status["index_state"] = true, status["status"]
	status["cursor"], status["generation"] = generationCursor, generation
	return status, nil
}

// SearchIndexWorkerClient supervises one worker process and serializes its private RPC stream.
type SearchIndexWorkerClient struct {
	mu           sync.Mutex
	repairMu     sync.Mutex
	stateDir     string
	persistent   bool
	closed       bool
	repairs      map[string]*time.Timer
	repairEpochs map[string]uint64
	closing      chan struct{}
	closeOnce    sync.Once
	cmd          *exec.Cmd
	input        io.WriteCloser
	output       *bufio.Reader
	stdin        io.WriteCloser
	stdout       io.ReadCloser
	nextID       uint64
}

func NewSearchIndexWorkerClient(stateDir string) *SearchIndexWorkerClient {
	return &SearchIndexWorkerClient{stateDir: stateDir, persistent: true, repairs: make(map[string]*time.Timer), repairEpochs: make(map[string]uint64), closing: make(chan struct{})}
}

func (client *SearchIndexWorkerClient) QueueRepair(request indexRequest, onScheduled func()) {
	if request.Root == "" {
		return
	}
	client.repairMu.Lock()
	defer client.repairMu.Unlock()
	if existing := client.repairs[request.Root]; existing != nil {
		existing.Stop()
	}
	client.repairEpochs[request.Root]++
	epoch := client.repairEpochs[request.Root]
	var timer *time.Timer
	timer = time.AfterFunc(2*time.Second, func() {
		client.repairMu.Lock()
		if client.repairs[request.Root] != timer || client.repairEpochs[request.Root] != epoch {
			client.repairMu.Unlock()
			return
		}
		delete(client.repairs, request.Root)
		request.Action = "repair"
		_, err := client.Request(context.Background(), request)
		client.repairMu.Unlock()
		if err == nil && onScheduled != nil {
			onScheduled()
		}
	})
	client.repairs[request.Root] = timer
}

func (client *SearchIndexWorkerClient) SetPersistent(persistent bool) error {
	client.repairMu.Lock()
	defer client.repairMu.Unlock()
	client.mu.Lock()
	defer client.mu.Unlock()
	if client.persistent == persistent {
		return nil
	}
	for root, timer := range client.repairs {
		timer.Stop()
		client.repairEpochs[root]++
		delete(client.repairs, root)
	}
	if client.persistent && !persistent {
		if client.closed {
			return errors.New("search index worker client is closed")
		}
		if err := client.startLocked(); err != nil {
			return err
		}
		if _, err := client.requestLocked(context.Background(), indexRequest{Action: "purge"}); err != nil {
			return err
		}
	}
	client.stopLocked()
	client.persistent = persistent
	return nil
}

func (client *SearchIndexWorkerClient) startLocked() error {
	if client.cmd != nil {
		return nil
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	databasePath := ":memory:"
	if client.persistent {
		databasePath = filepath.Join(client.stateDir, "search-index.sqlite3")
	}
	cmd := execCommand(executable, "--search-index-worker", "--index-db", databasePath)
	cmd.Env = withSearchIndexWorkerCPUCap(os.Environ())
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		return err
	}
	client.cmd, client.stdin, client.stdout = cmd, stdin, stdout
	client.input, client.output = stdin, bufio.NewReader(stdout)
	return nil
}

func withSearchIndexWorkerCPUCap(environment []string) []string {
	bounded := make([]string, 0, len(environment)+1)
	for _, entry := range environment {
		if strings.HasPrefix(entry, "GOMAXPROCS=") {
			continue
		}
		bounded = append(bounded, entry)
	}
	return append(bounded, "GOMAXPROCS=1")
}

func (client *SearchIndexWorkerClient) Request(ctx context.Context, request indexRequest) (map[string]any, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	client.mu.Lock()
	defer client.mu.Unlock()
	if client.closed {
		return nil, errors.New("search index worker client is closed")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return client.requestLocked(ctx, request)
}

func (client *SearchIndexWorkerClient) requestLocked(ctx context.Context, request indexRequest) (map[string]any, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if err := client.startLocked(); err != nil {
		return nil, err
	}
	client.nextID++
	request.ID = client.nextID
	data, err := json.Marshal(request)
	if err != nil {
		return nil, err
	}
	if _, err := client.input.Write(append(data, '\n')); err != nil {
		client.stopLocked()
		return nil, err
	}
	result := make(chan struct {
		response indexResponse
		err      error
	}, 1)
	go func() {
		line, readErr := client.output.ReadBytes('\n')
		var response indexResponse
		if readErr == nil {
			readErr = json.Unmarshal(line, &response)
		}
		result <- struct {
			response indexResponse
			err      error
		}{response, readErr}
	}()
	finishResponse := func(result struct {
		response indexResponse
		err      error
	}) (map[string]any, error) {
		if result.err != nil {
			client.stopLocked()
			return nil, result.err
		}
		if result.response.ID != request.ID {
			client.stopLocked()
			return nil, errors.New("search index worker response mismatch")
		}
		if !result.response.OK {
			return nil, errors.New(result.response.Error)
		}
		return result.response.Payload, nil
	}
	waitForShutdown := func() (struct {
		response indexResponse
		err      error
	}, bool) {
		timer := time.NewTimer(5 * time.Second)
		defer timer.Stop()
		select {
		case response := <-result:
			return response, true
		case <-timer.C:
			client.stopLocked()
			return struct {
				response indexResponse
				err      error
			}{}, false
		}
	}
	select {
	case <-ctx.Done():
		cancelData, marshalErr := json.Marshal(indexRequest{Action: "cancel", CancelID: request.ID})
		if marshalErr != nil {
			client.stopLocked()
			return nil, marshalErr
		}
		if _, writeErr := client.input.Write(append(cancelData, '\n')); writeErr != nil {
			client.stopLocked()
			return nil, writeErr
		}
		select {
		case response := <-result:
			if response.err != nil || response.response.ID != request.ID {
				_, err := finishResponse(response)
				return nil, err
			}
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			if _, err := finishResponse(response); err != nil {
				return nil, err
			}
			return nil, ctx.Err()
		case <-client.closing:
			response, completed := waitForShutdown()
			if !completed {
				return nil, ctx.Err()
			}
			if response.err != nil || response.response.ID != request.ID {
				_, err := finishResponse(response)
				return nil, err
			}
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			if _, err := finishResponse(response); err != nil {
				return nil, err
			}
			return nil, ctx.Err()
		}
	case <-client.closing:
		response, completed := waitForShutdown()
		if !completed {
			return nil, errors.New("search index worker stopped during shutdown")
		}
		if _, err := finishResponse(response); err != nil {
			return nil, err
		}
		return nil, errors.New("search index worker is shutting down")
	case response := <-result:
		return finishResponse(response)
	}
}

func (client *SearchIndexWorkerClient) Fence(ctx context.Context, root string, settingsRevision uint64) error {
	client.repairMu.Lock()
	client.cancelRepairLocked(root)
	_, err := client.Request(ctx, indexRequest{Action: "fence", Root: root, SettingsRevision: settingsRevision})
	client.repairMu.Unlock()
	return err
}

func (client *SearchIndexWorkerClient) FenceSettings(ctx context.Context, roots []string, settingsRevision uint64, preserveSnapshots ...bool) error {
	client.repairMu.Lock()
	defer client.repairMu.Unlock()
	for _, root := range roots {
		client.cancelRepairLocked(root)
	}
	preserve := len(preserveSnapshots) > 0 && preserveSnapshots[0]
	_, err := client.Request(ctx, indexRequest{Action: "settings_fence", FenceRoots: append([]string(nil), roots...), SettingsRevision: settingsRevision, Invalidate: !preserve, PreserveSnapshots: preserve})
	return err
}

func (client *SearchIndexWorkerClient) SyncSettings(ctx context.Context, roots []string, settingsRevision uint64) error {
	_, err := client.Request(ctx, indexRequest{Action: "settings_fence", FenceRoots: append([]string(nil), roots...), SettingsRevision: settingsRevision})
	return err
}

func (client *SearchIndexWorkerClient) Unindex(ctx context.Context, root string) error {
	client.repairMu.Lock()
	client.cancelRepairLocked(root)
	_, err := client.Request(ctx, indexRequest{Action: "unindex", Root: root})
	client.repairMu.Unlock()
	return err
}

func (client *SearchIndexWorkerClient) cancelRepairLocked(root string) {
	client.repairEpochs[root]++
	if timer := client.repairs[root]; timer != nil {
		timer.Stop()
		delete(client.repairs, root)
	}
}

func (client *SearchIndexWorkerClient) stopLocked() {
	if client.stdin != nil {
		_ = client.stdin.Close()
	}
	if client.cmd != nil {
		_ = client.cmd.Process.Kill()
		_ = client.cmd.Wait()
	}
	client.cmd = nil
	client.input = nil
	client.output = nil
	client.stdin = nil
	client.stdout = nil
}

func (client *SearchIndexWorkerClient) Close() {
	client.closeOnce.Do(func() { close(client.closing) })
	client.repairMu.Lock()
	for root, timer := range client.repairs {
		timer.Stop()
		client.repairEpochs[root]++
		delete(client.repairs, root)
	}
	client.repairMu.Unlock()
	client.mu.Lock()
	defer client.mu.Unlock()
	client.closed = true
	if client.stdin != nil {
		_ = client.stdin.Close()
	}
	if client.cmd != nil {
		done := make(chan error, 1)
		go func() { done <- client.cmd.Wait() }()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			_ = client.cmd.Process.Kill()
			<-done
		}
	}
	client.cmd = nil
	client.input = nil
	client.output = nil
	client.stdin = nil
	client.stdout = nil
}

var execCommand = func(path string, args ...string) *exec.Cmd { return exec.Command(path, args...) }
