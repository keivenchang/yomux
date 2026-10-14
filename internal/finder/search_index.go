// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bufio"
	"container/heap"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"runtime/pprof"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/mattn/go-sqlite3"
)

const (
	DefaultIndexedFiles       = 1_000_000
	DefaultIndexBytes         = int64(1024 * 1024 * 1024)
	MaxIndexBytes             = int64(1024 * 1024 * 1024)
	indexBatchSize            = 256
	indexQueueLimit           = 200_000
	indexQueueByteLimit       = 32 * 1024 * 1024
	searchIndexSchemaVersion  = 2
	indexCoverageFileLimit    = "per-root file limit reached"
	indexCoverageStorageLimit = "snapshot storage budget reached"
	indexCoverageSQLiteLimit  = "SQLite storage budget reached"
)

type indexRequest struct {
	ID                     uint64         `json:"id"`
	CancelID               uint64         `json:"cancel_id,omitempty"`
	Action                 string         `json:"action"`
	Root                   string         `json:"root"`
	Identity               string         `json:"identity,omitempty"`
	Query                  string         `json:"query,omitempty"`
	Limit                  int            `json:"limit,omitempty"`
	Cursor                 string         `json:"cursor,omitempty"`
	MaxFiles               int            `json:"max_files,omitempty"`
	MaxBytes               int64          `json:"max_bytes,omitempty"`
	Background             bool           `json:"background,omitempty"`
	ChangedPaths           []string       `json:"changed_paths,omitempty"`
	ForceRefresh           bool           `json:"force_refresh,omitempty"`
	DemandScoped           bool           `json:"demand_scoped,omitempty"`
	RefreshSec             int            `json:"refresh_seconds,omitempty"`
	ExcludeDirs            []string       `json:"exclude_dirs,omitempty"`
	ExcludePaths           []string       `json:"exclude_paths,omitempty"`
	ApplicationEpoch       uint64         `json:"application_epoch,omitempty"`
	SettingsRevision       uint64         `json:"settings_revision,omitempty"`
	LegacySettingsRevision uint64         `json:"legacy_settings_revision,omitempty"`
	FenceRoots             []string       `json:"fence_roots,omitempty"`
	Invalidate             bool           `json:"invalidate,omitempty"`
	PreserveSnapshots      bool           `json:"preserve_snapshots,omitempty"`
	Options                map[string]any `json:"options,omitempty"`
}

type indexResponse struct {
	ID      uint64         `json:"id"`
	OK      bool           `json:"ok"`
	Error   string         `json:"error,omitempty"`
	Payload map[string]any `json:"payload,omitempty"`
}

type workerReply struct {
	response indexResponse
	err      error
}

// RunSearchIndexWorker serves a private newline-delimited protocol over inherited stdin/stdout.
// It is called only by the supervised server child process; stdout never carries logs.
func RunSearchIndexWorker(input io.Reader, output io.Writer, databasePath string) error {
	var policy *indexRequest
	if os.Getenv("YOMUX_INDEX_INITIAL_POLICY") == "stdin" {
		buffered := bufio.NewReader(input)
		var encoded []byte
		for {
			fragment, err := buffered.ReadSlice('\n')
			encoded = append(encoded, fragment...)
			if len(encoded) > 2<<20 {
				return errors.New("initial index policy exceeds 2 MiB")
			}
			if err == nil {
				break
			}
			if !errors.Is(err, bufio.ErrBufferFull) {
				return err
			}
		}
		policy = &indexRequest{}
		if err := json.Unmarshal(encoded, policy); err != nil {
			return err
		}
		if policy.Action != "settings_fence" {
			return errors.New("initial index policy is required before storage initialization")
		}
		names := make(map[string]struct{}, len(policy.ExcludeDirs))
		for _, name := range policy.ExcludeDirs {
			names[name] = struct{}{}
		}
		for _, root := range policy.FenceRoots {
			if _, err := compileSearchPolicy(root, names, policy.ExcludePaths); err != nil {
				return err
			}
		}
		input = buffered
	}
	store, err := openSearchIndexStore(databasePath, policy)
	if err != nil {
		return err
	}
	defer func() { store.db.Close() }()
	engine := newSearchIndexEngine(store)
	if policy != nil {
		if _, err := engine.handleContext(context.Background(), *policy); err != nil {
			return err
		}
	}
	reader := bufio.NewScanner(input)
	reader.Buffer(make([]byte, 4096), 1<<20)
	writer := bufio.NewWriter(output)
	var activeMu sync.Mutex
	active := make(map[uint64]context.CancelFunc)
	var writerMu sync.Mutex
	var workers sync.WaitGroup
	engine.onComplete = func(status map[string]any) {
		data, err := json.Marshal(indexResponse{ID: 0, OK: true, Payload: status})
		if err != nil {
			return
		}
		writerMu.Lock()
		defer writerMu.Unlock()
		if _, err := writer.Write(append(data, '\n')); err == nil {
			_ = writer.Flush()
		}
	}
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

func openSearchIndexStore(path string, initialPolicy ...*indexRequest) (*searchIndexStore, error) {
	if path != ":memory:" {
		if err := recoverIndexPublication(path); err != nil {
			return nil, err
		}
		if err := migrateSearchIndex(path, initialPolicy...); err != nil {
			return nil, err
		}
	}
	return createSearchIndexStore(path)
}

func createSearchIndexStore(path string) (*searchIndexStore, error) {
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
		`CREATE TABLE IF NOT EXISTS roots(root TEXT PRIMARY KEY, identity TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL, coverage TEXT NOT NULL DEFAULT 'pending', count INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0, updated_ns INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', settings_revision INTEGER NOT NULL DEFAULT 0, active_revision INTEGER NOT NULL DEFAULT 0, scan_duration_ns INTEGER NOT NULL DEFAULT 0, last_success_ns INTEGER NOT NULL DEFAULT 0)`,
		`CREATE TABLE IF NOT EXISTS root_generations(root TEXT PRIMARY KEY, generation INTEGER NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS settings_fences(root TEXT PRIMARY KEY, revision INTEGER NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS settings_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL, policy TEXT NOT NULL DEFAULT '')`,
		`CREATE TABLE IF NOT EXISTS root_keys(id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE)`,
		`CREATE TABLE IF NOT EXISTS directories(id INTEGER PRIMARY KEY, parent INTEGER NOT NULL, name TEXT NOT NULL, UNIQUE(parent,name))`,
		`INSERT OR IGNORE INTO directories(id,parent,name) VALUES(1,0,'')`,
		`CREATE TABLE IF NOT EXISTS entries(root INTEGER NOT NULL, generation INTEGER NOT NULL, directory INTEGER NOT NULL, name TEXT NOT NULL, file_identity TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, PRIMARY KEY(root,generation,directory,name)) WITHOUT ROWID`,
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
	newRootColumns := map[string]string{"active_revision": `INTEGER NOT NULL DEFAULT 0`, "scan_duration_ns": `INTEGER NOT NULL DEFAULT 0`, "last_success_ns": `INTEGER NOT NULL DEFAULT 0`}
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
			// Keep the current allocation limit until the refresh can atomically
			// replace an older, larger snapshot with a budget-compliant generation.
			pageBudget = pageCount
		} else if err := store.reclaimFreePages(); err != nil {
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

func (store *searchIndexStore) markRootRefreshing(root, identity string, prior int, updatedNS int64) error {
	_, err := store.db.Exec(`INSERT INTO roots(root,identity,generation,state,coverage,count,bytes,updated_ns,error,settings_revision) VALUES(?,?,?,'warming','pending',0,0,?,'',0) ON CONFLICT(root) DO UPDATE SET identity=excluded.identity,state=CASE WHEN generation=0 THEN 'warming' ELSE state END,error=CASE WHEN generation=0 THEN '' ELSE error END`, root, identity, prior, updatedNS)
	return err
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
	settingsPolicy         string
	settingsApplyMu        sync.Mutex
	applicationEpoch       uint64
	budgetBytes            int64
	fenceLoadErr           error
	incarnation            string
	activeBuild            bool
	activeRoot             string
	activeSettingsRevision uint64
	demandRoots            []string
	buildCancels           map[string]context.CancelFunc
	mutationGeneration     uint64
	snapshotMutations      int
	snapshots              map[string]*searchIndexSnapshot
	crawlPause             sync.RWMutex
	onComplete             func(map[string]any)
	cpuCapture             CPUCapture
	fullDiscoveries        uint64
	incrementalDiscoveries uint64
	directoriesVisited     uint64
	queuedJobs             uint64
	coalescedJobs          uint64
}

type indexBuildJob struct {
	root, identity   string
	maxFiles         int
	maxBytes         int64
	fence            uint64
	settingsRevision uint64
	excludeDirs      map[string]struct{}
	excludePaths     []string
	changedPaths     []string
	background       bool
	visible          bool
	demandScoped     bool
	explicit         bool
	sequence         uint64
}

type discoveredIndexPath struct {
	directory string
	relative  string
	name      string
	identity  string
	size      int64
	mtimeNS   int64
}

func (path discoveredIndexPath) relativePath() string {
	if path.relative != "" {
		return path.relative
	}
	return path.directory + path.name
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
		budgetBytes:  DefaultIndexBytes,
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
	if err := store.db.QueryRow(`SELECT revision,policy FROM settings_state WHERE singleton=1`).Scan(&engine.settingsFloor, &engine.settingsPolicy); err == nil {
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
		engine.settingsApplyMu.Lock()
		defer engine.settingsApplyMu.Unlock()
		names := make(map[string]struct{}, len(request.ExcludeDirs))
		for _, name := range request.ExcludeDirs {
			names[name] = struct{}{}
		}
		if _, err := compileSearchPolicy("/", names, request.ExcludePaths); err != nil {
			return nil, err
		}
		engine.mu.Lock()
		if request.ApplicationEpoch != 0 && request.ApplicationEpoch < engine.applicationEpoch {
			engine.mu.Unlock()
			return nil, errors.New("stale settings application epoch")
		}
		engine.mu.Unlock()
		if !request.PreserveSnapshots && engine.compatibleStoredPolicy(request) {
			request.PreserveSnapshots = true
		}
		if !request.PreserveSnapshots && request.LegacySettingsRevision != 0 && engine.legacySnapshotsMatch(request.FenceRoots, request.LegacySettingsRevision) {
			request.PreserveSnapshots = true
		}
		engine.mu.Lock()
		unchanged := request.PreserveSnapshots && !request.Invalidate && engine.settingsFloorSet && engine.settingsFloor == request.SettingsRevision
		engine.mu.Unlock()
		payload := map[string]any{"ok": true, "revision": strconv.FormatUint(request.SettingsRevision, 10)}
		var err error
		if !unchanged {
			payload, err = engine.fenceSettings(request.FenceRoots, request.SettingsRevision, request.Invalidate, request.PreserveSnapshots)
		}
		if err != nil {
			return nil, err
		}
		engine.mu.Lock()
		engine.applicationEpoch = request.ApplicationEpoch
		if request.MaxBytes > 0 {
			engine.budgetBytes = request.MaxBytes
		}
		engine.mu.Unlock()
		policy, marshalErr := json.Marshal(request)
		if marshalErr != nil {
			return nil, marshalErr
		}
		engine.crawlPause.RLock()
		_, policyErr := engine.store.db.Exec(`UPDATE settings_state SET policy=? WHERE singleton=1 AND policy<>?`, string(policy), string(policy))
		if policyErr == nil {
			policyErr = engine.store.reclaimDirectories()
		}
		engine.crawlPause.RUnlock()
		if policyErr != nil {
			return nil, policyErr
		}
		engine.mu.Lock()
		engine.settingsPolicy = string(policy)
		engine.mu.Unlock()
		payload["application_epoch"] = strconv.FormatUint(request.ApplicationEpoch, 10)
		payload["worker_incarnation"] = engine.incarnation
		return payload, nil
	}
	if request.Action == "demand" {
		roots := make([]string, 0, len(request.FenceRoots))
		seen := make(map[string]struct{}, len(request.FenceRoots))
		for _, candidate := range request.FenceRoots {
			absolute, err := filepath.Abs(candidate)
			if err != nil || candidate == "" {
				continue
			}
			absolute = filepath.Clean(absolute)
			if _, exists := seen[absolute]; exists {
				continue
			}
			seen[absolute] = struct{}{}
			roots = append(roots, absolute)
		}
		sort.Strings(roots)
		engine.mu.Lock()
		engine.demandRoots = roots
		for root, job := range engine.pendingBuild {
			if IndexRootDemanded(root, roots) {
				if job.background {
					job.background = false
					job.visible = true
				}
				engine.pendingBuild[root] = job
				continue
			}
			if !job.demandScoped {
				continue
			}
			delete(engine.pendingBuild, root)
			delete(engine.builds, root)
			if done := engine.buildDone[root]; done != nil {
				close(done)
				delete(engine.buildDone, root)
			}
		}
		for root, job := range engine.rerun {
			if IndexRootDemanded(root, roots) {
				if job.background {
					job.background = false
					job.visible = true
				}
				engine.rerun[root] = job
			} else if job.demandScoped {
				delete(engine.rerun, root)
			}
		}
		engine.mu.Unlock()
		return map[string]any{"ok": true}, nil
	}
	if request.Action == "cpu_profile" {
		mode, _ := request.Options["mode"].(string)
		seconds, _ := request.Options["seconds"].(float64)
		if mode == "start" {
			if err := engine.cpuCapture.Start(int(seconds)); err != nil {
				return nil, err
			}
			return engine.cpuCapture.Status(), nil
		}
		if mode == "status" {
			return engine.cpuCapture.Status(), nil
		}
		if mode == "artifact" {
			status := engine.cpuCapture.Status()
			status["artifact"] = base64.StdEncoding.EncodeToString(engine.cpuCapture.Artifact())
			return status, nil
		}
		if mode != "start" || seconds < 1 || seconds > 60 {
			return nil, errors.New("invalid worker CPU profile request")
		}
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
			engine.mu.Lock()
			accepted := engine.settingsRevisionAcceptedLocked(root, request.SettingsRevision)
			engine.mu.Unlock()
			if !accepted {
				return nil, errors.New("stale settings revision: refresh was not scheduled")
			}
			engine.schedule(root, identity, request.MaxFiles, request.MaxBytes, refreshSec, request.ExcludeDirs, request.ExcludePaths, request.SettingsRevision, request)
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

func (engine *searchIndexEngine) compatibleStoredPolicy(request indexRequest) bool {
	engine.mu.Lock()
	raw, floor := engine.settingsPolicy, engine.settingsFloor
	engine.mu.Unlock()
	if raw != "" {
		var previous indexRequest
		if json.Unmarshal([]byte(raw), &previous) != nil {
			return false
		}
		return reflect.DeepEqual(previous.ExcludeDirs, request.ExcludeDirs) && reflect.DeepEqual(previous.ExcludePaths, request.ExcludePaths)
	}
	// Legacy v1 persisted only the opaque fingerprint. Qualify a budget-only
	// mismatch by reproducing that fingerprint with otherwise identical policy.
	if request.MaxFiles < 1 {
		return false
	}
	for mb := int64(1); mb <= MaxIndexBytes/(1024*1024); mb++ {
		encoded, err := json.Marshal(struct {
			Roots        []string `json:"roots"`
			ExcludeDirs  []string `json:"exclude_dirs"`
			ExcludePaths []string `json:"exclude_paths"`
			MaxFiles     int      `json:"max_files"`
			MaxBytes     int64    `json:"max_bytes"`
			RefreshSecs  int      `json:"refresh_secs"`
			Persist      bool     `json:"persist"`
		}{request.FenceRoots, request.ExcludeDirs, request.ExcludePaths, request.MaxFiles, mb * 1024 * 1024, request.RefreshSec, true})
		if err != nil {
			return false
		}
		digest := sha256.Sum256(encoded)
		if binary.BigEndian.Uint64(digest[:8])&uint64(1<<63-1) == floor {
			return true
		}
	}
	return false
}

func (engine *searchIndexEngine) legacySnapshotsMatch(roots []string, revision uint64) bool {
	engine.mu.Lock()
	floor, floorSet := engine.settingsFloor, engine.settingsFloorSet
	engine.mu.Unlock()
	if !floorSet || floor != revision {
		return false
	}
	rows, err := engine.store.db.Query("SELECT root,settings_revision FROM roots")
	if err != nil {
		return false
	}
	defer rows.Close()
	matched := false
	for rows.Next() {
		var root string
		var storedRevision uint64
		if rows.Scan(&root, &storedRevision) != nil {
			return false
		}
		if SearchRootConfigured(roots, root) {
			matched = true
			if storedRevision != revision {
				return false
			}
		}
	}
	return matched && rows.Err() == nil
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
		if (invalidate && indexRootWithinAny(normalizedRoots, candidate)) || (!invalidate && (jobRevision != revision || !SearchRootConfigured(normalizedRoots, candidate))) {
			targets[candidate] = struct{}{}
		}
	}
	for candidate := range engine.pendingBuild {
		job := engine.pendingBuild[candidate]
		if (invalidate && indexRootWithinAny(normalizedRoots, candidate)) || (!invalidate && (job.settingsRevision != revision || !SearchRootConfigured(normalizedRoots, candidate))) {
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
			if !preserveSnapshots || !SearchRootConfigured(normalizedRoots, target) {
				targets[target] = struct{}{}
			}
		}
	}
	removeTargets := targets
	if preserveSnapshots && !invalidate {
		removeTargets = make(map[string]struct{})
		for target := range targets {
			if !SearchRootConfigured(normalizedRoots, target) {
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
			if SearchRootConfigured(normalizedRoots, root) {
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
			if _, err := engine.store.db.Exec(`UPDATE roots SET active_revision=?,state=CASE WHEN generation>0 THEN 'warming' ELSE state END WHERE root=? AND active_revision<>?`, revision, root, revision); err != nil {
				engine.crawlPause.RUnlock()
				return nil, err
			}
		}
		engine.crawlPause.RUnlock()
	}
	return map[string]any{"ok": true, "revision": strconv.FormatUint(revision, 10), "fenced_roots": len(targets)}, nil
}

func (engine *searchIndexEngine) pauseBuildAdmission() func() {
	// Keep scheduling closed from target discovery through deletion. A new-revision
	// request must not start a replacement crawl after the affected builds drain.
	engine.mu.Lock()
	engine.snapshotMutations++
	engine.mutationGeneration++
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
		if _, err := engine.store.db.Exec(`DELETE FROM entries WHERE root=(SELECT id FROM root_keys WHERE path=?)`, target); err != nil {
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

func SearchRootConfigured(configuredRoots []string, candidate string) bool {
	return indexRootWithinAny(configuredRoots, candidate)
}

func (engine *searchIndexEngine) storedSettingsReconcileTargets(configuredRoots []string, revision uint64) (map[string]struct{}, error) {
	rows, err := engine.store.db.Query(`SELECT root,active_revision FROM roots`)
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
		if storedRevision != revision || !SearchRootConfigured(configuredRoots, root) {
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
	if _, err := tx.Exec(`DELETE FROM entries WHERE root=(SELECT id FROM root_keys WHERE path=?) AND generation<>?`, root, keep); err != nil {
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
	for {
		var free int
		if err := store.db.QueryRow(`PRAGMA freelist_count`).Scan(&free); err != nil {
			return err
		}
		if free == 0 {
			return checkpointIndex(store.db)
		}
		rows, err := store.db.Query(`PRAGMA incremental_vacuum(64)`)
		if err != nil {
			return err
		}
		for rows.Next() {
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			return err
		}
		rows.Close()
		if err := checkpointIndex(store.db); err != nil {
			return err
		}
	}
}

func (engine *searchIndexEngine) schedule(root, identity string, maxFiles int, maxBytes int64, refreshSec int, excludeDirs, excludePaths []string, settingsRevision uint64, requests ...indexRequest) {
	if maxFiles < 1 || maxFiles > DefaultIndexedFiles {
		maxFiles = DefaultIndexedFiles
	}
	if maxBytes < 1 || maxBytes > MaxIndexBytes {
		maxBytes = DefaultIndexBytes
	}
	forceRefresh := len(requests) > 0 && requests[0].ForceRefresh
	engine.mu.Lock()
	blocked := engine.snapshotMutations > 0 || !engine.settingsRevisionAcceptedLocked(root, settingsRevision)
	engine.mu.Unlock()
	if blocked {
		return
	}
	if !forceRefresh && !engine.needsRefresh(root, identity, refreshSec, settingsRevision, time.Now()) {
		return
	}
	engine.mu.Lock()
	if len(requests) > 0 && requests[0].DemandScoped && !IndexRootDemanded(root, engine.demandRoots) {
		engine.mu.Unlock()
		return
	}
	if engine.snapshotMutations > 0 || !engine.settingsRevisionAcceptedLocked(root, settingsRevision) {
		engine.mu.Unlock()
		return
	}
	job := indexBuildJob{root: root, identity: identity, maxFiles: maxFiles, maxBytes: maxBytes, fence: engine.fences[root], settingsRevision: settingsRevision, excludeDirs: make(map[string]struct{}, len(excludeDirs)), excludePaths: append([]string(nil), excludePaths...)}
	if len(requests) > 0 {
		job.changedPaths = append([]string(nil), requests[0].ChangedPaths...)
		job.background = requests[0].Background
		job.demandScoped = requests[0].DemandScoped
		job.explicit = !job.background && !job.demandScoped && (requests[0].Action == "search" || requests[0].Action == "ensure")
		job.visible = !job.background && (requests[0].Action == "ensure" || requests[0].Action == "search" || requests[0].Action == "repair")
	}
	if job.background && IndexRootDemanded(root, engine.demandRoots) {
		job.background, job.visible = false, true
	}
	for _, name := range excludeDirs {
		if strings.TrimSpace(name) != "" {
			job.excludeDirs[name] = struct{}{}
		}
	}
	if engine.builds[root] {
		engine.coalescedJobs++
		if pending, exists := engine.pendingBuild[root]; exists {
			engine.pendingBuild[root] = mergeIndexBuildJobs(pending, job)
		} else if forceRefresh {
			if previous, exists := engine.rerun[root]; exists {
				job = mergeIndexBuildJobs(previous, job)
			} else {
				engine.queuedJobs++
				job.sequence = engine.queuedJobs
			}
			engine.rerun[root] = job
		}
		engine.mu.Unlock()
		return
	}
	engine.builds[root] = true
	engine.queuedJobs++
	job.sequence = engine.queuedJobs
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
	engine.crawlPause.RLock()
	defer engine.crawlPause.RUnlock()
	var storedIdentity, state, lastError string
	var count int
	var storedRevision uint64
	var updatedNS int64
	statusErr := engine.store.db.QueryRow(`SELECT identity,state,settings_revision,updated_ns,count,error FROM roots WHERE root=?`, root).Scan(&storedIdentity, &state, &storedRevision, &updatedNS, &count, &lastError)
	return statusErr != nil || state == "warming" || (state == "partial" && count == 0 && lastError == "") || storedIdentity != identity || storedRevision != settingsRevision || refreshSec > 0 && now.Sub(time.Unix(0, updatedNS)) >= time.Duration(refreshSec)*time.Second
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
		// A committed generation is useful immediately, even if another refresh
		// is queued. Root completion waiters remain open until its work drains.
		engine.mu.Lock()
		delete(engine.buildCancels, job.root)
		if rerun, exists := engine.rerun[job.root]; exists && rerun.fence == engine.fences[job.root] {
			engine.pendingBuild[job.root] = rerun
		} else {
			delete(engine.builds, job.root)
			close(engine.buildDone[job.root])
			delete(engine.buildDone, job.root)
		}
		delete(engine.rerun, job.root)
		next, found := engine.nextBuildLocked()
		if found {
			engine.activeRoot = next.root
			engine.activeSettingsRevision = next.settingsRevision
			engine.mu.Unlock()
			engine.notifyCompletion(job.root, job.identity)
			job = next
			continue
		}
		engine.activeBuild = false
		engine.activeRoot = ""
		engine.activeSettingsRevision = 0
		engine.mu.Unlock()
		engine.notifyCompletion(job.root, job.identity)
		return
	}
}

func mergeIndexBuildJobs(previous, next indexBuildJob) indexBuildJob {
	next.changedPaths = mergeIndexChanges(next.root, previous.changedPaths, next.changedPaths)
	next.visible = next.visible || previous.visible
	next.explicit = next.explicit || previous.explicit
	next.background = next.background && previous.background
	// Visibility can promote priority, but it cannot take ownership of an
	// independently requested search or scheduled background reconciliation.
	next.demandScoped = next.demandScoped && previous.demandScoped
	next.sequence = previous.sequence
	return next
}

func (engine *searchIndexEngine) nextBuildLocked() (indexBuildJob, bool) {
	var next indexBuildJob
	found := false
	for _, candidate := range engine.pendingBuild {
		if !found || indexBuildPriority(candidate) > indexBuildPriority(next) ||
			indexBuildPriority(candidate) == indexBuildPriority(next) &&
				(candidate.sequence < next.sequence || candidate.sequence == next.sequence && candidate.root < next.root) {
			next, found = candidate, true
		}
	}
	if found {
		delete(engine.pendingBuild, next.root)
	}
	return next, found
}

func indexBuildPriority(job indexBuildJob) int {
	if job.explicit {
		return 3
	}
	if job.visible {
		return 2
	}
	if !job.background {
		return 1
	}
	return 0
}

// IndexRootDemanded reports overlap between an index root and consumer scopes.
func IndexRootDemanded(root string, scopes []string) bool {
	for _, scope := range scopes {
		if indexRootWithin(root, scope) || indexRootWithin(scope, root) {
			return true
		}
	}
	return false
}

func (engine *searchIndexEngine) notifyCompletion(root, identity string) {
	if engine.onComplete == nil {
		return
	}
	status, err := engine.status(root, identity)
	if err == nil {
		engine.onComplete(status)
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
	policy, err := compileSearchPolicy(root, nil, exclusions)
	return err != nil || policy.excludedPath(path)
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
	engine.crawlPause.RLock()
	defer engine.crawlPause.RUnlock()
	return engine.statusUnlocked(root, identity)
}

func (engine *searchIndexEngine) statusUnlocked(root, identity string) (payload map[string]any, statusErr error) {
	defer func() {
		if statusErr != nil {
			return
		}
		engine.mu.Lock()
		active, budget := engine.settingsFloor, engine.budgetBytes
		engine.mu.Unlock()
		var build, ack uint64
		_ = engine.store.db.QueryRow(`SELECT settings_revision,active_revision FROM roots WHERE root=?`, root).Scan(&build, &ack)
		payload["configured_revision"] = strconv.FormatUint(active, 10)
		payload["active_revision"] = strconv.FormatUint(ack, 10)
		payload["snapshot_revision"] = strconv.FormatUint(build, 10)
		payload["snapshot_compatible"] = ack == active || !engine.settingsFloorSet
		payload["configured_budget_bytes"], payload["max_bytes"] = budget, budget
		usage, err := engine.store.diskUsage()
		if err != nil {
			statusErr = err
			return
		}
		for key, value := range usage {
			payload[key] = value
		}
		payload["over_budget"] = usage["database_bytes"]+usage["wal_bytes"] > budget
	}()
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
	diagnostics := map[string]any{"full_discoveries": engine.fullDiscoveries, "incremental_discoveries": engine.incrementalDiscoveries, "directories_visited": engine.directoriesVisited, "queued_jobs": engine.queuedJobs, "coalesced_jobs": engine.coalescedJobs}
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
	presentedState := state
	if state == "error" && coverage == "partial" && indexCoverageLimitReason(lastError) {
		// Older builds persisted expected coverage limits as errors. Their searchable
		// generation is valid partial coverage and can be presented directly.
		presentedState = "partial"
	}
	return map[string]any{"root": root, "status": presentedState, "index_state": presentedState, "indexed": generation > 0, "ready": generation > 0, "building": building, "generation": generation, "count": count, "error": lastError, "coverage_reason": lastError, "stale": presentedState == "error", "refreshing": building, "freshness": map[bool]string{true: "stale", false: "fresh"}[presentedState == "error"], "worker_incarnation": engine.incarnation, "index_coverage": coverage, "discovery_coverage": coverage, "enrichment_state": "disabled", "scan_duration_ms": float64(scanDurationNS) / float64(time.Millisecond), "last_successful_scan": lastSuccessfulScan, "index_diagnostics": diagnostics, "progressive_coverage": map[string]any{"full_coverage": coverage == "complete", "visited_files": count}}, nil
}

func indexCoverageLimitReason(reason string) bool {
	switch reason {
	case indexCoverageFileLimit, indexCoverageStorageLimit, indexCoverageSQLiteLimit:
		return true
	default:
		return false
	}
}

func (engine *searchIndexEngine) build(job indexBuildJob) error {
	return engine.buildContext(context.Background(), job)
}

func (engine *searchIndexEngine) buildContext(ctx context.Context, job indexBuildJob) error {
	started := time.Now()
	if err := ctx.Err(); err != nil {
		return err
	}
	if engine.fenced(job.root, job.fence, job.settingsRevision) {
		return nil
	}
	var prior int
	_ = engine.store.db.QueryRow(`SELECT generation FROM roots WHERE root=? AND identity=?`, job.root, job.identity).Scan(&prior)
	paths, coverage, reason, err := engine.discoverBuildPaths(ctx, job, prior)
	if err != nil {
		return engine.fail(job.root, job.identity, prior, err)
	}
	if prior > 0 && coverage == "partial" && reason != indexCoverageFileLimit {
		return engine.fail(job.root, job.identity, prior, errors.New(reason))
	}
	if engine.fenced(job.root, job.fence, job.settingsRevision) {
		return nil
	}
	err = engine.replaceMeasuredSnapshot(ctx, job, prior, paths, coverage, reason, started)
	if err != nil && !errors.Is(err, context.Canceled) && !engine.fenced(job.root, job.fence, job.settingsRevision) {
		return engine.fail(job.root, job.identity, prior, err)
	}
	return err
}

func (engine *searchIndexEngine) writeDiscoveredPaths(root string, generation int, paths []discoveredIndexPath) error {
	transaction, err := engine.store.db.Begin()
	if err != nil {
		return err
	}
	if err := writeDiscoveredPathsTx(transaction, root, generation, paths); err != nil {
		_ = transaction.Rollback()
		return err
	}
	return transaction.Commit()
}

func (engine *searchIndexEngine) replaceGenerationWithinBudget(job indexBuildJob, generation, count int, bytes int64, reason string, duration time.Duration, snapshot *searchIndexSnapshot, paths []discoveredIndexPath) (bool, error) {
	engine.crawlPause.Lock()
	defer engine.crawlPause.Unlock()
	engine.mu.Lock()
	defer engine.mu.Unlock()
	if engine.fences[job.root] != job.fence || !engine.settingsRevisionAcceptedLocked(job.root, job.settingsRevision) {
		return false, nil
	}
	transaction, err := engine.store.db.Begin()
	if err != nil {
		return false, err
	}
	if _, err := transaction.Exec(`DELETE FROM entries WHERE root=?`, job.root); err != nil {
		_ = transaction.Rollback()
		return false, err
	}
	if err := writeDiscoveredPathsTx(transaction, job.root, generation, paths); err != nil {
		_ = transaction.Rollback()
		return false, err
	}
	now := time.Now().UnixNano()
	result, err := transaction.Exec(`UPDATE roots SET identity=?,generation=?,state='partial',coverage='partial',count=?,bytes=?,updated_ns=?,error=?,settings_revision=?,scan_duration_ns=?,last_success_ns=? WHERE root=?`, job.identity, generation, count, bytes, now, reason, job.settingsRevision, duration.Nanoseconds(), now, job.root)
	if err == nil {
		var affected int64
		affected, err = result.RowsAffected()
		if err == nil && affected != 1 {
			err = errors.New("root snapshot disappeared before publication")
		}
	}
	if err != nil {
		_ = transaction.Rollback()
		return false, err
	}
	if err := transaction.Commit(); err != nil {
		return false, err
	}
	engine.snapshots[job.root] = snapshot
	return true, nil
}

func writeDiscoveredPathsTx(transaction *sql.Tx, root string, generation int, paths []discoveredIndexPath) error {
	return writeNormalizedPaths(transaction, root, generation, paths)
}

func discoverIndexPaths(ctx context.Context, root string, excludedNames map[string]struct{}, excludedPaths []string, maxFiles int, visited ...func()) ([]discoveredIndexPath, string, string, error) {
	if maxFiles < 1 || maxFiles > DefaultIndexedFiles {
		maxFiles = DefaultIndexedFiles
	}
	policy, err := compileSearchPolicy(root, excludedNames, excludedPaths)
	if err != nil {
		return nil, "", "", err
	}
	indexPolicy := *policy
	policy = &indexPolicy
	policy.directoryLimit = indexQueueLimit
	directory, err := openResolvedDescriptorContext(ctx, root, true, nil)
	if err != nil {
		return nil, "", "", err
	}
	defer directory.Close()
	paths := make([]discoveredIndexPath, 0, min(maxFiles, 65536))
	limited := false
	prefixes := make(map[string]string)
	walkErr := walkSearchDescriptors(ctx, directory, root, root, policy, func(entry walkedEntry) error {
		if entry.IsDir {
			if len(visited) > 0 {
				visited[0]()
			}
			return nil
		}
		if len(paths) == maxFiles {
			limited = true
			return errWalkStop
		}
		prefix, _ := splitSearchPath(entry.Relative)
		shared, exists := prefixes[prefix]
		if !exists {
			shared = prefix
			prefixes[prefix] = shared
		}
		paths = append(paths, discoveredIndexPath{directory: shared, name: entry.Name, identity: fileID(entry.Info), size: entry.Info.Size(), mtimeNS: entry.Info.ModTime().UnixNano()})
		return nil
	})
	if err := ctx.Err(); err != nil {
		return nil, "", "", err
	}
	reason := ""
	if walkErr != nil && !errors.Is(walkErr, errWalkStop) {
		reason = walkErr.Error()
	}
	coverage, reason := indexDiscoveryCoverage(limited, reason)
	return paths, coverage, reason, nil
}

func indexDiscoveryCoverage(fileLimitReached bool, traversalReason string) (string, string) {
	if fileLimitReached {
		if traversalReason != "" {
			return "partial", indexCoverageFileLimit + "; traversal error: " + traversalReason
		}
		return "partial", indexCoverageFileLimit
	}
	if traversalReason != "" {
		return "partial", traversalReason
	}
	return "complete", ""
}

func (engine *searchIndexEngine) publishGeneration(job indexBuildJob, generation int, state, coverage string, count int, bytes int64, reason string, duration time.Duration, snapshot *searchIndexSnapshot) (bool, error) {
	pprof.SetGoroutineLabels(pprof.WithLabels(context.Background(), pprof.Labels("phase", "index-publication")))
	defer pprof.SetGoroutineLabels(context.Background())
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
		result, err = publish.Exec(`UPDATE roots SET identity=?,generation=?,state=?,coverage=?,count=?,bytes=?,updated_ns=?,error=?,settings_revision=?,active_revision=?,scan_duration_ns=?,last_success_ns=? WHERE root=?`, job.identity, generation, state, coverage, count, bytes, now, reason, job.settingsRevision, job.settingsRevision, duration.Nanoseconds(), now, job.root)
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

func indexInteger(value any) int64 {
	switch value := value.(type) {
	case int:
		return int64(value)
	case int64:
		return value
	default:
		panic("invalid index integer")
	}
}

func (engine *searchIndexEngine) writeBatch(batch [][]any) error {
	transaction, err := engine.store.db.Begin()
	if err != nil {
		return err
	}
	defer transaction.Rollback()
	for _, row := range batch {
		path := discoveredIndexPath{relative: row[2].(string), name: row[3].(string), identity: row[4].(string), size: indexInteger(row[5]), mtimeNS: indexInteger(row[6])}
		if err := writeNormalizedPaths(transaction, row[0].(string), row[1].(int), []discoveredIndexPath{path}); err != nil {
			return err
		}
	}
	return transaction.Commit()
}

func (engine *searchIndexEngine) fail(root, identity string, prior int, cause error) error {
	engine.crawlPause.RLock()
	defer engine.crawlPause.RUnlock()
	_, err := engine.store.db.Exec(`INSERT INTO roots(root,identity,generation,state,coverage,error,updated_ns) VALUES(?,?,?,'error','pending',?,?) ON CONFLICT(root) DO UPDATE SET identity=excluded.identity,generation=CASE WHEN roots.identity=excluded.identity THEN roots.generation ELSE 0 END,count=CASE WHEN roots.identity=excluded.identity THEN roots.count ELSE 0 END,state='error',coverage=CASE WHEN roots.identity=excluded.identity THEN roots.coverage ELSE 'pending' END,error=excluded.error,updated_ns=excluded.updated_ns`, root, identity, prior, cause.Error(), time.Now().UnixNano())
	return errors.Join(cause, err)
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

// Keep the strongest bounded shortlist regardless of filesystem enumeration order.
func canRetainRankedIndexEntry(best rankedIndexHeap, key searchSortKey, limit int) bool {
	return len(best) < limit || compareSearchSortKey(key, best[0].key)
}

func retainRankedIndexEntry(best *rankedIndexHeap, candidate rankedIndexEntry, limit int) {
	if !canRetainRankedIndexEntry(*best, candidate.key, limit) {
		return
	}
	if len(*best) < limit {
		heap.Push(best, candidate)
	} else {
		(*best)[0] = candidate
		heap.Fix(best, 0)
	}
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
	status, err := engine.statusUnlocked(root, identity)
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
	if err := engine.store.db.QueryRow(`SELECT generation,active_revision FROM roots WHERE root=? AND identity=?`, root, identity).Scan(&generation, &storedRevision); err != nil {
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
		paths, err := engine.store.loadNormalizedPaths(ctx, root, generation)
		if err != nil {
			return nil, err
		}
		loaded.entries = paths
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
		directory, base := path.directory, path.name
		if path.relative != "" {
			directory, base = splitSearchPath(path.relative)
		}
		key, matched := searchEntrySortKeySegmentsPrepared(path.name, directory, base, preparedTokens)
		if !matched {
			continue
		}
		matchedCount++
		if !canRetainRankedIndexEntry(best, key, limit) {
			continue
		}
		relative := directory + base
		absolute := filepath.Join(root, filepath.FromSlash(relative))
		row := map[string]any{"path": absolute, "name": path.name, "relative_path": relative, "kind": "file", "size": path.size, "mtime": path.mtimeNS / 1e9, "mtime_ns": path.mtimeNS, "realpath": absolute, "file_id": path.identity, "file_identity": "id:" + path.identity, "uploaded": generatedUploadName(path.name)}
		retainRankedIndexEntry(&best, rankedIndexEntry{key: key, row: row}, limit)
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
type contextMutex struct {
	initOnce sync.Once
	token    chan struct{}
}

func (mutex *contextMutex) Lock() {
	_ = mutex.LockContext(context.Background(), nil)
}

func (mutex *contextMutex) LockContext(ctx context.Context, abort <-chan struct{}) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	mutex.initOnce.Do(func() { mutex.token = make(chan struct{}, 1) })
	select {
	case mutex.token <- struct{}{}:
		if err := ctx.Err(); err != nil {
			mutex.Unlock()
			return err
		}
		select {
		case <-abort:
			mutex.Unlock()
			return errors.New("search index worker is shutting down")
		default:
			return nil
		}
	case <-ctx.Done():
		return ctx.Err()
	case <-abort:
		return errors.New("search index worker is shutting down")
	}
}

func (mutex *contextMutex) Unlock() {
	<-mutex.token
}

type SearchIndexWorkerClient struct {
	replies        chan workerReply
	OnComplete     func(map[string]any)
	repairRequests map[string]indexRequest
	mu             contextMutex
	repairMu       contextMutex
	stateDir       string
	persistent     bool
	closed         bool
	repairs        map[string]*time.Timer
	repairEpochs   map[string]uint64
	repairFlights  map[string]map[chan struct{}]struct{}
	closing        chan struct{}
	closeOnce      sync.Once
	cmd            *exec.Cmd
	input          io.WriteCloser
	output         *bufio.Reader
	stdin          io.WriteCloser
	stdout         io.ReadCloser
	initialPolicy  *indexRequest
	nextID         uint64
}

func NewSearchIndexWorkerClient(stateDir string) *SearchIndexWorkerClient {
	return &SearchIndexWorkerClient{stateDir: stateDir, persistent: true, repairs: make(map[string]*time.Timer), repairRequests: make(map[string]indexRequest), repairEpochs: make(map[string]uint64), repairFlights: make(map[string]map[chan struct{}]struct{}), closing: make(chan struct{})}
}

func (client *SearchIndexWorkerClient) QueueRepair(request indexRequest, onScheduled func()) {
	if request.Root == "" {
		return
	}
	client.repairMu.Lock()
	defer client.repairMu.Unlock()
	if existing := client.repairs[request.Root]; existing != nil {
		previous := client.repairRequests[request.Root]
		request.ChangedPaths = mergeIndexChanges(request.Root, previous.ChangedPaths, request.ChangedPaths)
		client.repairRequests[request.Root] = request
		return
	}
	client.repairRequests[request.Root] = request
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
		request = client.repairRequests[request.Root]
		delete(client.repairRequests, request.Root)
		request.Action = "repair"
		// Keep map ownership short; policy fences still wait for admitted repairs
		// before retiring their root, without blocking unrelated repair queues.
		done := make(chan struct{})
		if client.repairFlights[request.Root] == nil {
			client.repairFlights[request.Root] = make(map[chan struct{}]struct{})
		}
		client.repairFlights[request.Root][done] = struct{}{}
		client.repairMu.Unlock()
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		_, err := client.Request(ctx, request)
		cancel()
		close(done)
		client.repairMu.Lock()
		current := client.repairEpochs[request.Root] == epoch
		delete(client.repairFlights[request.Root], done)
		if len(client.repairFlights[request.Root]) == 0 {
			delete(client.repairFlights, request.Root)
		}
		client.repairMu.Unlock()
		if err == nil && current && onScheduled != nil {
			onScheduled()
		}
	})
	client.repairs[request.Root] = timer
}

func (client *SearchIndexWorkerClient) SetPersistent(persistent bool) error {
	client.repairMu.Lock()
	defer client.repairMu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := client.cancelAllRepairsLocked(ctx); err != nil {
		return err
	}
	client.mu.Lock()
	defer client.mu.Unlock()
	return client.setPersistentLocked(persistent)
}

func (client *SearchIndexWorkerClient) setPersistentLocked(persistent bool) error {
	if client.closed {
		return errors.New("search index worker client is closed")
	}
	if client.persistent == persistent {
		return nil
	}
	client.stopLocked()
	client.persistent = persistent
	return nil
}

func (client *SearchIndexWorkerClient) startLocked(ctx context.Context) error {
	if client.cmd != nil {
		return nil
	}
	if client.initialPolicy == nil {
		return errors.New("search index settings unavailable: initial policy is required")
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
	var initial []byte
	if client.initialPolicy != nil {
		initial, err = json.Marshal(client.initialPolicy)
		if err != nil {
			return err
		}
		cmd.Env = append(cmd.Env, "YOMUX_INDEX_INITIAL_POLICY=stdin")
	}
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
	replies := make(chan workerReply, 1)
	client.replies = replies
	reader := client.output
	notify := client.OnComplete
	go func() {
		for {
			line, err := reader.ReadBytes('\n')
			var response indexResponse
			if err == nil {
				err = json.Unmarshal(line, &response)
			}
			if err != nil {
				replies <- workerReply{err: err}
				return
			}
			if response.ID == 0 {
				if notify != nil {
					notify(response.Payload)
				}
				continue
			}
			replies <- workerReply{response: response}
		}
	}()
	if initial != nil {
		if err := client.writeFrameLocked(ctx, initial); err != nil {
			client.stopLocked()
			return err
		}
	}
	return nil
}

// Bootstrap and request frames share cancellation: a worker doing migration
// may stop consuming stdin before a large policy fits in the pipe.
func (client *SearchIndexWorkerClient) writeFrameLocked(ctx context.Context, frame []byte) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	input := client.input
	written := make(chan error, 1)
	go func() {
		_, err := input.Write(append(frame, '\n'))
		written <- err
	}()
	select {
	case err := <-written:
		return err
	case <-ctx.Done():
		client.stopLocked()
		<-written
		return ctx.Err()
	case <-client.closing:
		client.stopLocked()
		<-written
		return errors.New("search index worker is shutting down")
	}
}

func withSearchIndexWorkerCPUCap(environment []string) []string {
	allowed := map[string]bool{"PATH": true, "HOME": true, "TMPDIR": true, "TMP": true, "TEMP": true, "LANG": true, "LC_ALL": true, "LC_CTYPE": true, "TZ": true, "GODEBUG": true, "GOTRACEBACK": true, "GOMEMLIMIT": true, "LD_LIBRARY_PATH": true, "DYLD_LIBRARY_PATH": true, "SystemRoot": true, "RUNFILES_DIR": true, "RUNFILES_MANIFEST_FILE": true, "YOMUX_TEST_INDEX_WORKER": true}
	bounded := make([]string, 0, len(allowed)+1)
	for _, entry := range environment {
		key, _, _ := strings.Cut(entry, "=")
		if allowed[key] {
			bounded = append(bounded, entry)
		}
	}
	return append(bounded, "GOMAXPROCS=1")
}

func (client *SearchIndexWorkerClient) Request(ctx context.Context, request indexRequest) (map[string]any, error) {
	if err := client.mu.LockContext(ctx, client.closing); err != nil {
		return nil, err
	}
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
	if err := client.startLocked(ctx); err != nil {
		return nil, err
	}
	client.nextID++
	request.ID = client.nextID
	data, err := json.Marshal(request)
	if err != nil {
		return nil, err
	}
	if err := client.writeFrameLocked(ctx, data); err != nil {
		client.stopLocked()
		return nil, err
	}
	result := make(chan struct {
		response indexResponse
		err      error
	}, 1)
	replies := client.replies
	go func() {
		reply := <-replies
		result <- struct {
			response indexResponse
			err      error
		}{reply.response, reply.err}
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
		cancelContext, cancelWrite := context.WithTimeout(context.Background(), 5*time.Second)
		writeErr := client.writeFrameLocked(cancelContext, cancelData)
		cancelWrite()
		if writeErr != nil {
			client.stopLocked()
			return nil, ctx.Err()
		}
		response, completed := waitForShutdown()
		if !completed {
			return nil, ctx.Err()
		}
		if response.err != nil || response.response.ID != request.ID {
			_, err := finishResponse(response)
			return nil, err
		}
		return nil, ctx.Err()
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
	if err := client.repairMu.LockContext(ctx, client.closing); err != nil {
		return err
	}
	defer client.repairMu.Unlock()
	if err := client.cancelRepairLocked(ctx, root); err != nil {
		return err
	}
	_, err := client.Request(ctx, indexRequest{Action: "fence", Root: root, SettingsRevision: settingsRevision})
	return err
}

func (client *SearchIndexWorkerClient) FenceSettings(ctx context.Context, roots []string, settingsRevision uint64, preserveSnapshots ...bool) error {
	if err := client.repairMu.LockContext(ctx, client.closing); err != nil {
		return err
	}
	defer client.repairMu.Unlock()
	for _, root := range roots {
		if err := client.cancelRepairLocked(ctx, root); err != nil {
			return err
		}
	}
	preserve := len(preserveSnapshots) > 0 && preserveSnapshots[0]
	_, err := client.Request(ctx, indexRequest{Action: "settings_fence", FenceRoots: append([]string(nil), roots...), SettingsRevision: settingsRevision, Invalidate: !preserve, PreserveSnapshots: preserve})
	return err
}

func (client *SearchIndexWorkerClient) ApplySettings(ctx context.Context, roots []string, revision, epoch uint64, persistent bool, maxBytes int64, preserve bool, initialOptions ...SearchOptions) (map[string]any, error) {
	if err := client.repairMu.LockContext(ctx, client.closing); err != nil {
		return nil, err
	}
	defer client.repairMu.Unlock()
	if err := client.cancelAllRepairsLocked(ctx); err != nil {
		return nil, err
	}
	if err := client.mu.LockContext(ctx, client.closing); err != nil {
		return nil, err
	}
	defer client.mu.Unlock()
	if client.closed {
		return nil, errors.New("search index worker client is closed")
	}
	request := indexRequest{Action: "settings_fence", FenceRoots: append([]string(nil), roots...), SettingsRevision: revision, ApplicationEpoch: epoch, MaxBytes: maxBytes, PreserveSnapshots: preserve}
	if len(initialOptions) > 0 {
		options := initialOptions[0]
		request.MaxFiles, request.RefreshSec = options.MaxFiles, options.RefreshSec
		request.LegacySettingsRevision = options.LegacySettingsRevision
		request.ExcludeDirs, request.ExcludePaths = append([]string(nil), options.ExcludeDirs...), append([]string(nil), options.ExcludePaths...)
	}
	client.initialPolicy = &request
	if err := client.setPersistentLocked(persistent); err != nil {
		return nil, err
	}
	return client.requestLocked(ctx, request)
}

func (client *SearchIndexWorkerClient) SyncSettings(ctx context.Context, roots []string, settingsRevision uint64, legacyRevisions ...uint64) error {
	request := indexRequest{Action: "settings_fence", FenceRoots: append([]string(nil), roots...), SettingsRevision: settingsRevision}
	if len(legacyRevisions) > 0 {
		request.LegacySettingsRevision = legacyRevisions[0]
	}
	_, err := client.Request(ctx, request)
	return err
}

func (client *SearchIndexWorkerClient) Profile(ctx context.Context, mode string, seconds int) (map[string]any, error) {
	return client.Request(ctx, indexRequest{Action: "cpu_profile", Options: map[string]any{"mode": mode, "seconds": seconds}})
}

func (client *SearchIndexWorkerClient) UpdateDemand(ctx context.Context, roots []string) error {
	_, err := client.Request(ctx, indexRequest{Action: "demand", FenceRoots: roots})
	return err
}

func (client *SearchIndexWorkerClient) Unindex(ctx context.Context, root string) error {
	if err := client.repairMu.LockContext(ctx, client.closing); err != nil {
		return err
	}
	defer client.repairMu.Unlock()
	if err := client.cancelRepairLocked(ctx, root); err != nil {
		return err
	}
	_, err := client.Request(ctx, indexRequest{Action: "unindex", Root: root})
	return err
}

func (client *SearchIndexWorkerClient) cancelAllRepairsLocked(ctx context.Context) error {
	roots := make(map[string]bool, len(client.repairs)+len(client.repairFlights))
	for root := range client.repairs {
		roots[root] = true
	}
	for root := range client.repairFlights {
		roots[root] = true
	}
	for root := range roots {
		if err := client.cancelRepairLocked(ctx, root); err != nil {
			return err
		}
	}
	return nil
}

func (client *SearchIndexWorkerClient) cancelRepairLocked(ctx context.Context, root string) error {
	delete(client.repairRequests, root)
	client.repairEpochs[root]++
	if timer := client.repairs[root]; timer != nil {
		timer.Stop()
		delete(client.repairs, root)
	}
	// No IPC mutex is held while waiting. A root can have several admitted
	// repairs; all must settle before its fence, and the caller retains its deadline.
	for done := range client.repairFlights[root] {
		select {
		case <-done:
		case <-ctx.Done():
			return ctx.Err()
		case <-client.closing:
			return errors.New("search index worker client is closing")
		}
	}
	return nil
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
	for root := range client.repairs {
		_ = client.cancelRepairLocked(context.Background(), root)
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
