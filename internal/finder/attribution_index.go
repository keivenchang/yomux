// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/keivenchang/yomux/internal/atomicfile"
	"github.com/keivenchang/yomux/internal/mutablefs"
	"github.com/keivenchang/yomux/internal/tailfile"
)

var attributionFallbackRoot = "/__yomux_attribution_relative__/" + strings.Repeat("cwd/", 64) + "leaf"

const attributionMaxSources = 4096
const attributionMaxOperations = 250000

// Admission stays at 4 MiB; the final admitted full-frame read can bring a
// cycle to at most 12.25 MiB. Actual reads, including errors, stop further turns.
const attributionCycleReadAdmissionBytes int64 = 4 << 20

var ErrAttributionDiscoveryPending = errors.New("attribution discovery pending")

type AttributionSource struct {
	Path, Kind string
	Historical bool
}
type AttributionProgress struct {
	State     string `json:"state"`
	Partial   bool   `json:"partial"`
	Updated   int64  `json:"last_successful_update"`
	Error     string `json:"error,omitempty"`
	Gaps      int    `json:"generation_gaps,omitempty"`
	BytesRead int64  `json:"bytes_read"`
	Records   int64  `json:"records_indexed"`
	CatchupMs int64  `json:"catchup_ms"`
}

// AttributionIndex stores normalized file operations and independent durable
// positions. The sole worker owns reading/commits; requests only query and wake it.
type AttributionIndex struct {
	db                 *sql.DB
	ingestion          *tailfile.Ingestion
	mu                 sync.Mutex
	queue              []AttributionSource
	deferred           []AttributionSource
	overflowLost       bool
	lostAdmissions     map[string]AttributionSource
	unknownAdmissions  bool
	liveStreak         int
	pending            map[string]bool
	wake               chan struct{}
	progress           AttributionProgress
	discoveryPending   bool
	discoveryComplete  bool
	failures           map[string]error
	active             map[string]time.Time
	coverageIncomplete bool
	reconcileOffset    int
}

func OpenAttributionIndex(directory string, ingestion *tailfile.Ingestion) (*AttributionIndex, error) {
	if err := mutablefs.RequireLocal(directory); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(directory, 0700); err != nil {
		return nil, err
	}
	path := filepath.Join(directory, "attribution.sqlite3")
	if info, statErr := os.Lstat(path); statErr == nil && !info.Mode().IsRegular() {
		return nil, fmt.Errorf("attribution database is not a regular file")
	} else if statErr != nil && !os.IsNotExist(statErr) {
		return nil, statErr
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, err
	}
	if err = f.Close(); err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite3", atomicfile.SQLiteURI(path, "_busy_timeout=1000&_journal_mode=WAL&_foreign_keys=on"))
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(2)
	_, err = db.Exec(`CREATE TABLE IF NOT EXISTS attribution_sources(path TEXT PRIMARY KEY,kind TEXT NOT NULL,position BLOB NOT NULL,cwd TEXT NOT NULL,mtime INTEGER NOT NULL,complete INTEGER NOT NULL,updated INTEGER NOT NULL,gap TEXT NOT NULL,historical INTEGER NOT NULL);
 CREATE TABLE IF NOT EXISTS attribution_operations(source TEXT NOT NULL REFERENCES attribution_sources(path) ON DELETE CASCADE,offset INTEGER NOT NULL,path TEXT NOT NULL,status TEXT NOT NULL,relative INTEGER NOT NULL,PRIMARY KEY(source,offset,path));
 CREATE TABLE IF NOT EXISTS attribution_coverage(path TEXT PRIMARY KEY,kind TEXT NOT NULL,mtime INTEGER NOT NULL,gap TEXT NOT NULL,position BLOB NOT NULL);
 CREATE INDEX IF NOT EXISTS attribution_recent ON attribution_sources(kind,mtime DESC);`)
	if err != nil {
		db.Close()
		return nil, err
	}
	if ingestion == nil {
		ingestion = &tailfile.Ingestion{}
	}
	updated, incomplete, gaps, err := attributionIndexSummary(context.Background(), db)
	if err != nil {
		db.Close()
		return nil, err
	}
	return &AttributionIndex{db: db, ingestion: ingestion, pending: make(map[string]bool), failures: make(map[string]error), active: make(map[string]time.Time), wake: make(chan struct{}, 1), coverageIncomplete: incomplete > 0, progress: AttributionProgress{State: "indexing", Partial: true, Updated: updated, Gaps: gaps}}, nil
}

// A fully replayed current generation can be ready while an earlier generation
// remains unrecoverable. Keep that durable gap visible independently of coverage.
func attributionIndexSummary(ctx context.Context, db *sql.DB) (updated int64, incomplete, gaps int, err error) {
	err = db.QueryRowContext(ctx, `SELECT coalesce(max(updated),0),
      coalesce(sum(CASE WHEN gap IN ('source_deleted','index_evicted') THEN 1 ELSE 0 END),0)+(SELECT count(*) FROM attribution_coverage),
      coalesce(sum(CASE WHEN gap<>'' THEN 1 ELSE 0 END),0)+(SELECT count(*) FROM attribution_coverage)
      FROM attribution_sources`).Scan(&updated, &incomplete, &gaps)
	return
}

func (i *AttributionIndex) Close() error                       { return i.db.Close() }
func (s *Service) SetAttributionIndex(index *AttributionIndex) { s.attributionIndex = index }
func (i *AttributionIndex) Enqueue(source AttributionSource, priority bool) {
	if source.Path == "" || source.Kind != "codex" && source.Kind != "claude" {
		return
	}
	i.mu.Lock()
	i.enqueueLocked(source, priority)
	i.mu.Unlock()
	select {
	case i.wake <- struct{}{}:
	default:
	}
}

func (i *AttributionIndex) enqueueLocked(source AttributionSource, priority bool) {
	if lost, ok := i.lostAdmissions[source.Path]; ok {
		source.Historical = source.Historical || lost.Historical
	}
	if len(i.active) >= attributionMaxSources {
		for path, at := range i.active {
			if time.Since(at) >= 5*time.Minute {
				delete(i.active, path)
			}
		}
	}
	if priority {
		i.active[source.Path] = time.Now()
	}
	if i.pending[source.Path] {
		for _, pending := range [][]AttributionSource{i.queue, i.deferred} {
			for n := range pending {
				if pending[n].Path == source.Path && source.Historical {
					pending[n].Historical = true
				}
			}
		}
	}
	if !i.pending[source.Path] && len(i.queue) < 512 {
		i.pending[source.Path] = true
		i.queue = append(i.queue, source)
	}
	if len(i.queue) >= 512 && !i.pending[source.Path] {
		if len(i.deferred) < attributionMaxSources {
			i.deferred = append(i.deferred, source)
			i.pending[source.Path] = true
		} else {
			if i.lostAdmissions == nil {
				i.lostAdmissions = make(map[string]AttributionSource)
			}
			_, retained := i.lostAdmissions[source.Path]
			if len(i.lostAdmissions) < attributionMaxSources || retained {
				i.lostAdmissions[source.Path] = source
			} else {
				// Without a retained identity, rediscovery cannot prove recovery.
				i.unknownAdmissions = true
			}
			i.overflowLost = true
			i.failures["queue"] = fmt.Errorf("attribution queue capacity reached; discovery coverage incomplete")
		}
		i.progress.Partial = true
		i.progress.State = "indexing"
	}
	if i.pending[source.Path] {
		delete(i.lostAdmissions, source.Path)
		i.overflowLost = i.unknownAdmissions || len(i.lostAdmissions) > 0
		if !i.overflowLost {
			delete(i.failures, "queue")
		}
		if priority {
			i.promoteLocked(source.Path)
		}
	}
}

// Promote the existing owner without changing bounded admission or dropping the
// displaced archive source. The scheduler still reserves every third live turn.
func (i *AttributionIndex) promoteLocked(path string) {
	for n, source := range i.queue {
		if source.Path == path {
			copy(i.queue[1:n+1], i.queue[:n])
			i.queue[0] = source
			return
		}
	}
	for n, source := range i.deferred {
		if source.Path == path {
			i.deferred[n] = i.queue[len(i.queue)-1]
			copy(i.queue[1:], i.queue[:len(i.queue)-1])
			i.queue[0] = source
			return
		}
	}
}

func (i *AttributionIndex) dequeueLocked() AttributionSource {
	chosen := 0
	if i.liveStreak >= 2 {
		for n, candidate := range i.queue {
			if time.Since(i.active[candidate.Path]) >= 5*time.Minute {
				chosen = n
				break
			}
		}
	}
	source := i.queue[chosen]
	i.queue = append(i.queue[:chosen], i.queue[chosen+1:]...)
	if len(i.deferred) > 0 {
		i.queue = append(i.queue, i.deferred[0])
		i.deferred = i.deferred[1:]
	}
	if time.Since(i.active[source.Path]) < 5*time.Minute {
		i.liveStreak++
	} else {
		i.liveStreak = 0
	}
	delete(i.pending, source.Path)
	return source
}
func (i *AttributionIndex) Progress() AttributionProgress {
	i.mu.Lock()
	defer i.mu.Unlock()
	return i.progress
}
func (i *AttributionIndex) setError(err error) { i.setSourceError("query", err) }
func (i *AttributionIndex) setSourceError(key string, err error) {
	i.mu.Lock()
	defer i.mu.Unlock()
	if err == nil {
		delete(i.failures, key)
	} else {
		i.failures[key] = err
	}
	i.progress.Error = ""
	firstKey := ""
	for source, failure := range i.failures {
		if i.progress.Error == "" || source < firstKey {
			firstKey = source
			i.progress.Error = failure.Error()
		}
	}
	if i.progress.Error != "" {
		i.progress.State = "error"
		i.progress.Partial = true
	}
}

func (i *AttributionIndex) setDiscoveryProgress(err error) {
	i.mu.Lock()
	if err == nil {
		i.discoveryComplete = true
	}
	// Routine directory pages do not invalidate an already enumerated archive;
	// unprepared sources and lost coverage remain separate readiness guards.
	i.discoveryPending = !i.discoveryComplete
	if i.discoveryPending {
		i.progress.Partial = true
		if i.progress.Error == "" {
			i.progress.State = "indexing"
		}
	}
	i.mu.Unlock()
	if err == nil || errors.Is(err, ErrAttributionDiscoveryPending) {
		i.setSourceError("discovery", nil)
	}
	if err != nil && !errors.Is(err, ErrAttributionDiscoveryPending) {
		i.setSourceError("discovery", fmt.Errorf("discover attribution: %w", err))
	}
}

func (i *AttributionIndex) setSourceResult(path string, complete bool, err error) {
	if err != nil {
		i.setSourceError(path, fmt.Errorf("index %s: %w", path, err))
	} else if complete {
		i.setSourceError(path, nil)
	}
}

// Run combines discovery and indexing under a bounded cycle. Recent/live work
// may move forward, but each cycle also consumes the oldest queued source.
func (i *AttributionIndex) Run(ctx context.Context, discover func(context.Context) ([]AttributionSource, error)) {
	i.ingestion.Start(ctx)
	ticks, unsubscribe := i.ingestion.Subscribe()
	defer unsubscribe()
	reconcile := func() {
		discoveryCtx, cancel := context.WithTimeout(ctx, 500*time.Millisecond)
		sources, err := discover(discoveryCtx)
		defer cancel()
		for _, source := range sources {
			i.Enqueue(source, false)
		}
		if reconcileErr := i.reconcilePrepared(discoveryCtx); reconcileErr != nil && !errors.Is(reconcileErr, context.DeadlineExceeded) {
			i.setSourceError("reconciliation", reconcileErr)
		} else if reconcileErr == nil {
			i.setSourceError("reconciliation", nil)
		}
		i.mu.Lock()
		available := len(i.deferred) == 0 && !i.overflowLost
		i.mu.Unlock()
		if available {
			i.setSourceError("queue", nil)
		}
		i.setDiscoveryProgress(err)
	}
	started := time.Now()
	reconcile()
	for {
		i.indexQueuedSources(ctx)
		ready, readyErr := i.preparedQueue(ctx)
		if readyErr != nil {
			i.setSourceError("coverage", readyErr)
		} else {
			i.setSourceError("coverage", nil)
		}
		i.mu.Lock()
		if !ready || i.discoveryPending || i.coverageIncomplete || len(i.deferred) > 0 || i.overflowLost {
			i.progress.Partial = true
			if i.progress.Error == "" {
				i.progress.State = "indexing"
			}
		} else if i.progress.Error == "" {
			i.progress.State = "ready"
			i.progress.Partial = false
			if i.progress.CatchupMs == 0 {
				i.progress.CatchupMs = time.Since(started).Milliseconds()
			}
		}
		i.mu.Unlock()

		select {
		case <-ctx.Done():
			return
		case <-ticks:
			reconcile()
		case <-i.wake:
			// A wake requests one bounded cycle. Queued work naturally continues on
			// the reconciliation cadence instead of spinning on an unfinished EOF row.
			select {
			case <-ticks:
				reconcile()
			default:
			}
		}
	}
}

func (i *AttributionIndex) indexQueuedSources(ctx context.Context) int64 {
	bytesLeft, recordsLeft := attributionCycleReadAdmissionBytes, 4096
	i.mu.Lock()
	cycleSources := len(i.queue)
	i.mu.Unlock()
	for processed := 0; processed < cycleSources && bytesLeft > 0 && recordsLeft > 0 && ctx.Err() == nil; processed++ {
		i.mu.Lock()
		if len(i.queue) == 0 {
			i.mu.Unlock()
			break
		}
		source := i.dequeueLocked()
		i.mu.Unlock()
		used, records, complete, err := i.index(ctx, source, tailfile.CompleteRecordBatchBytes, recordsLeft)
		bytesLeft -= max(used, 1)
		recordsLeft -= max(records, 1)
		i.setSourceResult(source.Path, complete, err)
		if err != nil {
			continue
		}
		if !complete {
			i.mu.Lock()
			i.enqueueLocked(source, false)
			i.mu.Unlock()
		}
	}
	return attributionCycleReadAdmissionBytes - bytesLeft
}

func (i *AttributionIndex) index(ctx context.Context, source AttributionSource, maxBytes int64, maxRecords int) (int64, int, bool, error) {
	var encoded []byte
	var cwd string
	var priorMtime int64
	var priorGap string
	var priorHistorical bool
	err := i.db.QueryRowContext(ctx, "SELECT position,cwd,mtime,gap,historical FROM attribution_sources WHERE path=?", source.Path).Scan(&encoded, &cwd, &priorMtime, &priorGap, &priorHistorical)
	var position tailfile.Position
	if err != nil && err != sql.ErrNoRows {
		return 0, 0, false, err
	}
	if err == sql.ErrNoRows {
		// Eviction keeps only validation identity, not normalized operations or CWD.
		var tombstone []byte
		coverageErr := i.db.QueryRowContext(ctx, "SELECT position FROM attribution_coverage WHERE path=?", source.Path).Scan(&tombstone)
		if coverageErr != nil && coverageErr != sql.ErrNoRows {
			return 0, 0, false, coverageErr
		}
		if len(tombstone) > 0 {
			var prior tailfile.Position
			if err = json.Unmarshal(tombstone, &prior); err != nil {
				return 0, 0, false, err
			}
			position.Generation = prior.Generation
		}
	}
	if len(encoded) > 0 {
		if err = json.Unmarshal(encoded, &position); err != nil {
			return 0, 0, false, err
		}
	}
	batch, err := i.ingestion.ReadBatch(ctx, source.Path, position, maxBytes, maxRecords)
	if err != nil {
		if os.IsNotExist(err) {
			_, gapErr := i.db.ExecContext(ctx, "UPDATE attribution_sources SET complete=0,gap='source_deleted' WHERE path=?", source.Path)
			if gapErr != nil {
				return batch.BytesRead, 0, false, errors.Join(err, gapErr)
			}
		}
		return batch.BytesRead, 0, false, err
	}

	if batch.Reset {
		cwd = ""
	}
	for _, record := range batch.Records {
		if len(record.Data) > maxSessionTranscriptLineBytes {
			return batch.BytesRead, 0, false, fmt.Errorf("attribution record exceeds %d-byte limit", maxSessionTranscriptLineBytes)
		}
		if source.Kind == "codex" {
			if value := parseCodexTranscriptRecordedCWD(record.Data); value != "" {
				cwd = value
			}
		}
	}
	oldEncoded := encoded
	encoded, err = json.Marshal(batch.After)
	if err != nil {
		return batch.BytesRead, 0, false, err
	}
	if bytes.Equal(oldEncoded, encoded) && priorMtime == batch.ModifiedNS && priorGap != "source_deleted" && priorGap != "index_evicted" && (!source.Historical || priorHistorical) && !batch.Reset {
		return batch.BytesRead, len(batch.Records), batch.Complete, nil
	}
	tx, err := i.db.BeginTx(ctx, nil)
	if err != nil {
		return batch.BytesRead, 0, false, err
	}
	defer tx.Rollback()
	if batch.Reset {
		if _, err = tx.ExecContext(ctx, "DELETE FROM attribution_operations WHERE source=?", source.Path); err != nil {
			return batch.BytesRead, 0, false, err
		}
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO attribution_sources VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET kind=excluded.kind,position=excluded.position,cwd=excluded.cwd,mtime=excluded.mtime,complete=excluded.complete,updated=excluded.updated,historical=max(attribution_sources.historical,excluded.historical),gap=CASE WHEN excluded.complete=1 AND attribution_sources.gap IN ('source_deleted','index_evicted') THEN excluded.gap WHEN excluded.gap='' THEN attribution_sources.gap ELSE excluded.gap END`, source.Path, source.Kind, encoded, cwd, batch.ModifiedNS, batch.Complete, time.Now().UnixNano(), batch.Gap, source.Historical)
	if err != nil {
		return batch.BytesRead, 0, false, err
	}
	for _, record := range batch.Records {
		changes := make(map[string]*sessionFileAttribution)
		agent := SessionFileAgent{Kind: source.Kind, CWD: attributionFallbackRoot}
		if source.Kind == "codex" {
			scanCodexTranscriptLine(record.Data, agent, 0, changes)
		} else {
			scanClaudeTranscriptLine(record.Data, agent, 0, changes)
		}
		for path, change := range changes {
			relative := strings.HasPrefix(path, "/__yomux_attribution_relative__/")
			if relative {
				path, err = filepath.Rel(attributionFallbackRoot, path)
				if err != nil {
					return batch.BytesRead, 0, false, err
				}
			}
			if _, err = tx.ExecContext(ctx, "INSERT OR REPLACE INTO attribution_operations VALUES(?,?,?,?,?)", source.Path, record.End, path, change.status, relative); err != nil {
				return batch.BytesRead, 0, false, err
			}
		}
	}
	// Eviction deletes only complete inactive sources, never partially committed
	// operations. The global partial flag is set before the durable deletion.
	var count, operations int
	var operationBytes int64
	if err = tx.QueryRowContext(ctx, "SELECT count(*) FROM attribution_sources").Scan(&count); err != nil {
		return batch.BytesRead, 0, false, err
	}
	if err = tx.QueryRowContext(ctx, "SELECT count(*),coalesce(sum(length(path)+length(source)+96),0) FROM attribution_operations").Scan(&operations, &operationBytes); err != nil {
		return batch.BytesRead, 0, false, err
	}
	if count > attributionMaxSources || operations > attributionMaxOperations || operationBytes > 64<<20 {
		i.mu.Lock()
		i.progress.Partial = true
		i.progress.State = "indexing"
		i.mu.Unlock()
		candidates, evictErr := tx.QueryContext(ctx, "SELECT path,kind,position FROM attribution_sources WHERE complete=1 AND path<>? ORDER BY mtime LIMIT 64", source.Path)
		if evictErr != nil {
			return batch.BytesRead, 0, false, evictErr
		}
		type evictionCandidate struct {
			AttributionSource
			Position []byte
		}
		victims := []evictionCandidate{}
		for candidates.Next() {
			var victim evictionCandidate
			if err = candidates.Scan(&victim.Path, &victim.Kind, &victim.Position); err != nil {
				candidates.Close()
				return batch.BytesRead, 0, false, err
			}
			victims = append(victims, victim)
		}
		if err = candidates.Err(); err != nil {
			candidates.Close()
			return batch.BytesRead, 0, false, err
		}
		candidates.Close()
		for _, victim := range victims {
			i.mu.Lock()
			active := time.Since(i.active[victim.Path]) < 5*time.Minute
			i.mu.Unlock()
			if active {
				continue
			}
			if _, err = tx.ExecContext(ctx, "DELETE FROM attribution_operations WHERE source=?", victim.Path); err != nil {
				return batch.BytesRead, 0, false, err
			}
			if count > attributionMaxSources {
				if _, err = tx.ExecContext(ctx, "INSERT OR REPLACE INTO attribution_coverage SELECT path,kind,mtime,'index_evicted',position FROM attribution_sources WHERE path=?", victim.Path); err != nil {
					return batch.BytesRead, 0, false, err
				}
				if _, err = tx.ExecContext(ctx, "DELETE FROM attribution_sources WHERE path=?", victim.Path); err != nil {
					return batch.BytesRead, 0, false, err
				}
				count--
				continue
			}
			var prior tailfile.Position
			if err = json.Unmarshal(victim.Position, &prior); err != nil {
				return batch.BytesRead, 0, false, err
			}
			resetPosition, encodeErr := json.Marshal(tailfile.Position{Generation: prior.Generation})
			if encodeErr != nil {
				return batch.BytesRead, 0, false, encodeErr
			}
			if _, err = tx.ExecContext(ctx, "UPDATE attribution_sources SET complete=0,position=?,cwd='',gap='index_evicted' WHERE path=?", resetPosition, victim.Path); err != nil {
				return batch.BytesRead, 0, false, err
			}
		}
		if err = tx.QueryRowContext(ctx, "SELECT count(*),coalesce(sum(length(path)+length(source)+96),0) FROM attribution_operations").Scan(&operations, &operationBytes); err != nil {
			return batch.BytesRead, 0, false, err
		}
		if count > attributionMaxSources || operations > attributionMaxOperations || operationBytes > 64<<20 {
			return batch.BytesRead, 0, false, fmt.Errorf("attribution index storage budget exhausted")
		}

	}
	if batch.Complete {
		if _, err = tx.ExecContext(ctx, "DELETE FROM attribution_coverage WHERE path=?", source.Path); err != nil {
			return batch.BytesRead, 0, false, err
		}
	}
	var coverageCount int
	if err = tx.QueryRowContext(ctx, "SELECT count(*) FROM attribution_coverage").Scan(&coverageCount); err != nil {
		return batch.BytesRead, 0, false, err
	}
	if coverageCount > attributionMaxSources {
		if _, err = tx.ExecContext(ctx, "INSERT OR REPLACE INTO attribution_coverage VALUES('','',0,'coverage_budget_exhausted','{}')"); err != nil {
			return batch.BytesRead, 0, false, err
		}
		if _, err = tx.ExecContext(ctx, "DELETE FROM attribution_coverage WHERE path IN (SELECT path FROM attribution_coverage WHERE path<>'' ORDER BY mtime LIMIT ?)", coverageCount-attributionMaxSources+1); err != nil {
			return batch.BytesRead, 0, false, err
		}
	}
	if err = ctx.Err(); err != nil {
		return batch.BytesRead, 0, false, err
	}
	if err = tx.Commit(); err != nil {
		return batch.BytesRead, 0, false, err
	}
	updated, incomplete, gaps, err := attributionIndexSummary(ctx, i.db)
	if err != nil {
		return batch.BytesRead, len(batch.Records), false, err
	}
	i.mu.Lock()
	i.coverageIncomplete = incomplete > 0
	i.progress.Gaps = gaps
	i.progress.Updated = updated
	i.progress.BytesRead += batch.BytesRead
	i.progress.Records += int64(len(batch.Records))
	i.mu.Unlock()
	return batch.BytesRead, len(batch.Records), batch.Complete, nil
}

type indexedAttributionOperation struct {
	path, status string
	relative     bool
}
type indexedAttributionSource struct {
	historical      bool
	gap             string
	path, kind, cwd string
	mtime           int64
	complete        bool
	operations      []indexedAttributionOperation
}

func (i *AttributionIndex) query(ctx context.Context, agents []SessionFileAgent, roots []string, hours float64) (map[string]*sessionFileAttribution, error) {
	eligible := make([]SessionFileAgent, 0, len(agents))
	for _, agent := range agents {
		if (agent.Kind == "claude" || agent.Kind == "codex") && agent.Transcript != "" {
			eligible = append(eligible, agent)
			i.Enqueue(AttributionSource{Path: agent.Transcript, Kind: agent.Kind}, true)
		}
	}
	cutoff := time.Now().Add(-time.Duration(hours * float64(time.Hour))).UnixNano()
	selection := "s.path IN (SELECT path FROM attribution_sources WHERE mtime>=? AND historical=1 AND kind='codex' ORDER BY mtime DESC,path LIMIT 256)"
	if len(roots) == 0 {
		selection = "s.mtime>=? AND 0"
	}
	args := []any{cutoff}
	for _, agent := range eligible {
		selection += " OR s.path=?"
		args = append(args, agent.Transcript)
	}
	rows, err := i.db.QueryContext(ctx, `SELECT s.path,s.kind,s.cwd,s.mtime,s.complete,s.historical,s.gap,o.path,o.status,o.relative FROM attribution_sources s LEFT JOIN attribution_operations o ON o.source=s.path WHERE `+selection+` ORDER BY s.mtime DESC,s.path,o.offset`, args...)

	if err != nil {
		return nil, err
	}
	sources := make([]indexedAttributionSource, 0)
	for rows.Next() {
		var source indexedAttributionSource
		var path, status sql.NullString
		var relative sql.NullBool
		if err = rows.Scan(&source.path, &source.kind, &source.cwd, &source.mtime, &source.complete, &source.historical, &source.gap, &path, &status, &relative); err != nil {
			rows.Close()
			return nil, err
		}
		if len(sources) == 0 || sources[len(sources)-1].path != source.path {
			sources = append(sources, source)
		}
		if path.Valid {
			sources[len(sources)-1].operations = append(sources[len(sources)-1].operations, indexedAttributionOperation{path.String, status.String, relative.Bool})
		}
	}
	if err = rows.Err(); err != nil {
		rows.Close()
		return nil, err
	}
	if err = rows.Close(); err != nil {
		return nil, err
	}
	changes := make(map[string]*sessionFileAttribution)
	live := make(map[string]SessionFileAgent)
	for _, agent := range eligible {
		if _, seen := live[agent.Transcript]; !seen {
			live[agent.Transcript] = agent
		}
	}
	cwds := sessionFileTranscriptCWDs(agents, roots)
	historicalCount := 0
	seenLive := make(map[string]bool)
	for _, source := range sources {
		project := func(agent SessionFileAgent) map[string]*sessionFileAttribution {
			result := make(map[string]*sessionFileAttribution)
			for _, op := range source.operations {
				path := op.path
				if op.relative {
					path = resolveSessionTranscriptPath(path, agent.CWD)
				}
				recordSessionTranscriptPath(result, path, agent.CWD, agent, op.status, source.mtime/int64(time.Second))
			}
			return result
		}
		if source.historical && source.mtime >= cutoff {
			historicalCount++
		}
		if source.gap == "source_deleted" || source.gap == "index_evicted" {
			continue
		}
		if agent, ok := live[source.path]; ok {
			seenLive[source.path] = true
			if source.mtime < cutoff {
				continue
			}
			if !source.complete {
				i.mu.Lock()
				i.progress.Partial = true
				i.progress.State = "indexing"
				i.mu.Unlock()
			}
			for path, a := range project(agent) {
				mergeSessionFileAttribution(changes, path, a)
			}
			continue
		}
		if !source.historical || source.kind != "codex" || len(roots) == 0 || historicalCount > maxHistoricalCodexFiles {
			continue
		}
		if cwd := resolveSessionTranscriptPath(source.cwd, ""); cwd != "" {
			for path, a := range historicalCodexChangesForRoots(project(SessionFileAgent{Kind: "codex", CWD: cwd}), cwds) {
				mergeSessionFileAttribution(changes, path, a)
			}
		} else {
			for _, cwd := range cwds {
				projected := project(SessionFileAgent{Kind: "codex", CWD: cwd})
				if historicalCodexChangesTouchRoot(projected, cwd) {
					for path, a := range projected {
						mergeSessionFileAttribution(changes, path, a)
					}
					break
				}
			}
		}
	}
	for path := range live {
		if !seenLive[path] {
			i.mu.Lock()
			i.progress.Partial = true
			i.progress.State = "indexing"
			i.mu.Unlock()
		}
	}
	i.setSourceError("query", nil)
	return changes, nil
}
func (s *Service) sessionFileAttributions(ctx context.Context, agents []SessionFileAgent, roots []string, hours float64) map[string]*sessionFileAttribution {
	if s.attributionUnavailable != nil {
		return map[string]*sessionFileAttribution{}
	}
	if s.attributionIndex == nil {
		return collectSessionFileAttributions(ctx, agents, roots, hours)
	}
	changes, err := s.attributionIndex.query(ctx, agents, roots, hours)
	if err != nil {
		s.attributionIndex.setError(err)
		return map[string]*sessionFileAttribution{}
	}
	return changes
}

func (s *Service) AttributionPreparation() AttributionProgress {
	if s.attributionUnavailable != nil {
		return AttributionProgress{State: "error", Partial: true, Error: s.attributionUnavailable.Error()}
	}
	if s.attributionIndex == nil {
		return AttributionProgress{State: "ready"}
	}
	return s.attributionIndex.Progress()
}

func (s *Service) SetAttributionUnavailable(err error) { s.attributionUnavailable = err }

// Directory discovery cannot enumerate deleted files. Reconcile a bounded page
// of durable source identities so disappearance also invalidates old coverage.
func (i *AttributionIndex) reconcilePrepared(ctx context.Context) error {
	rows, err := i.db.QueryContext(ctx, "SELECT path,kind,historical FROM attribution_sources ORDER BY path LIMIT 256 OFFSET ?", i.reconcileOffset)
	if err != nil {
		return err
	}
	sources := []AttributionSource{}
	for rows.Next() {
		var source AttributionSource
		if err = rows.Scan(&source.Path, &source.Kind, &source.Historical); err != nil {
			rows.Close()
			return err
		}
		sources = append(sources, source)
	}
	if err = rows.Err(); err != nil {
		rows.Close()
		return err
	}
	if err = rows.Close(); err != nil {
		return err
	}
	i.reconcileOffset += len(sources)
	if len(sources) < 256 {
		i.reconcileOffset = 0
	}
	for _, source := range sources {
		if err = ctx.Err(); err != nil {
			return err
		}
		_, statErr := os.Stat(source.Path)
		if statErr != nil && !os.IsNotExist(statErr) {
			return statErr
		}
		if os.IsNotExist(statErr) {
			if _, err = i.db.ExecContext(ctx, "UPDATE attribution_sources SET complete=0,gap='source_deleted' WHERE path=?", source.Path); err != nil {
				return err
			}
			i.mu.Lock()
			i.coverageIncomplete = true
			i.progress.Partial = true
			i.progress.State = "indexing"
			i.mu.Unlock()
		}
	}
	return nil
}

func (i *AttributionIndex) preparedQueue(ctx context.Context) (bool, error) {
	rows, err := i.db.QueryContext(ctx, "SELECT path,complete FROM attribution_sources")
	if err != nil {
		return false, err
	}
	prepared := make(map[string]bool)
	ready := true
	for rows.Next() {
		var path string
		var complete bool
		if err = rows.Scan(&path, &complete); err != nil {
			rows.Close()
			return false, err
		}
		prepared[path] = complete
		if !complete {
			ready = false
		}
	}
	if err = rows.Err(); err != nil {
		rows.Close()
		return false, err
	}
	if err = rows.Close(); err != nil {
		return false, err
	}
	i.mu.Lock()
	defer i.mu.Unlock()
	for _, source := range i.queue {
		if !prepared[source.Path] {
			ready = false
		}
	}
	return ready, nil
}
