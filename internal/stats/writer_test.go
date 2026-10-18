package stats

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"
)

func writerBatch() AppendBatch {
	end := 20.0
	return AppendBatch{
		Observations:     []Observation{{EventID: "obs-1", Family: "cpu", SourceID: "host", ObservedAt: 10, EpochID: "epoch-1", Payload: map[string]any{"value": 1}}},
		CoverageEpochs:   []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 10, EndedAt: &end, NativeCadenceSeconds: 1, OwnerGeneration: 1}},
		UnavailableSpans: []UnavailableSpan{{Family: "gpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 10, EndedAt: 20, NativeCadenceSeconds: 5, Reason: "offline", OwnerGeneration: 1}},
		UsageAtoms:       []UsageAtom{{EventID: "codex:thread-1:event-1", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 10, Payload: map[string]any{"quantity": 4.0, "provider": "openai", "model": "gpt", "thread_id": "thread-1", "execution_source": "codex"}}},
	}
}

func TestWriterConcurrentCloseAndReads(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	start := make(chan struct{})
	var workers sync.WaitGroup
	errors := make(chan error, 16)
	for index := 0; index < 16; index++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			<-start
			if err := writer.Close(); err != nil {
				errors <- err
			}
			for iteration := 0; iteration < 32; iteration++ {
				_, _ = writer.MigrationStatus(ctx)
				_, _ = writer.statsDatabaseIdentity()
			}
			if err := writer.Close(); err != nil {
				errors <- err
			}
		}()
	}
	close(start)
	workers.Wait()
	close(errors)
	for err := range errors {
		t.Errorf("concurrent Close: %v", err)
	}
	if _, err := writer.AppendBatch(ctx, writerBatch()); err == nil {
		t.Error("closed writer accepted an append")
	}
	if _, err := writer.statsDatabaseIdentity(); err == nil {
		t.Error("closed writer exposed an active database identity")
	}
}

func TestOpenWriterCreatesSchema8AndReadback(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	result, err := writer.AppendBatch(context.Background(), writerBatch())
	if err != nil {
		t.Fatal(err)
	}
	if result.Generation != 1 || result.ObservationsAccepted != 1 || result.CoverageChanged != 1 || result.UnavailableAccepted != 1 || result.UsageAccepted != 1 {
		t.Fatalf("result = %+v", result)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	reader, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	observations, err := reader.ReadObservations(context.Background(), 0, 100, 10)
	if err != nil || len(observations) != 1 || observations[0].EventID != "obs-1" {
		t.Fatalf("observations = %+v, err = %v", observations, err)
	}
	usage, err := reader.ReadUsageAtoms(context.Background(), 0, 100, 10)
	if err != nil || len(usage) != 1 {
		t.Fatalf("usage = %+v, err = %v", usage, err)
	}
}

func TestOpenWriterRepairsMissingRingTriggers(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	for _, table := range []string{"aggregate_publication", "aggregate_rings", "aggregate_ring_slots"} {
		for _, operation := range []string{"insert", "delete"} {
			if _, err := db.ExecContext(ctx, "DROP TRIGGER "+table+"_reject_"+operation); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	writer, err = OpenWriter(ctx, path)
	if err != nil {
		t.Fatalf("same-schema database with repairable missing triggers was rejected: %v", err)
	}
	defer writer.Close()
	var triggerCount int
	if err := writer.db.QueryRowContext(ctx, "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('aggregate_publication','aggregate_rings','aggregate_ring_slots')").Scan(&triggerCount); err != nil {
		t.Fatal(err)
	}
	if triggerCount != 6 {
		t.Fatalf("ring trigger count = %d, want 6", triggerCount)
	}
}

func TestRingValidationRejectsMissingReplayCursorRow(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	mutation, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := mutation.ExecContext(ctx, "DELETE FROM ring_replay_cursor WHERE resolution_seconds=1"); err != nil {
		t.Fatal(err)
	}
	if err := mutation.Close(); err != nil {
		t.Fatal(err)
	}

	if _, err := OpenReadOnly(ctx, path); err == nil || !strings.Contains(err.Error(), "ring_replay_cursor") {
		t.Fatalf("read-only validation error = %v, want missing fixed cursor row", err)
	}
	if err := validateWriterDatabaseReadOnly(ctx, path); err == nil || !strings.Contains(err.Error(), "ring_replay_cursor") {
		t.Fatalf("writer preflight error = %v, want missing fixed cursor row", err)
	}
}

func TestRingValidationRejectsPythonSchemaMismatch(t *testing.T) {
	tests := []struct {
		name, want string
		mutations  []string
	}{
		{
			name: "extra slot resolution",
			mutations: []string{
				"PRAGMA foreign_keys=OFF",
				"DROP TRIGGER aggregate_ring_slots_reject_insert",
				"INSERT INTO aggregate_ring_slots(resolution_seconds,slot_index) VALUES(999,0)",
				"CREATE TRIGGER aggregate_ring_slots_reject_insert BEFORE INSERT ON aggregate_ring_slots BEGIN SELECT RAISE(ABORT, 'aggregate ring rows are fixed'); END",
			},
			want: "slot counts",
		},
		{
			name:      "misaligned ring head",
			mutations: []string{"PRAGMA ignore_check_constraints=ON", "UPDATE aggregate_rings SET newest_bucket_start=1 WHERE resolution_seconds=10"},
			want:      "head is not resolution-aligned",
		},
		{
			name:      "unexpected trigger on core table",
			mutations: []string{"CREATE TRIGGER unexpected_test_trigger AFTER INSERT ON observations BEGIN SELECT 1; END"},
			want:      "trigger set is not exact",
		},
		{
			name:      "unexpected table",
			mutations: []string{"CREATE TABLE unexpected_test_table(value TEXT)"},
			want:      "table set does not match",
		},
		{
			name: "unexpected core columns",
			mutations: []string{
				"ALTER TABLE observations RENAME TO observations_old",
				"CREATE TABLE observations(event_id TEXT)",
				"DROP TABLE observations_old",
			},
			want: "table \"observations\" has unexpected columns",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			ctx := context.Background()
			path := filepath.Join(t.TempDir(), "stats.sqlite3")
			writer, err := OpenWriter(ctx, path)
			if err != nil {
				t.Fatal(err)
			}
			if err := writer.Close(); err != nil {
				t.Fatal(err)
			}
			mutation, err := sql.Open("sqlite3", path)
			if err != nil {
				t.Fatal(err)
			}
			mutation.SetMaxOpenConns(1)
			for _, statement := range test.mutations {
				if _, err := mutation.ExecContext(ctx, statement); err != nil {
					_ = mutation.Close()
					t.Fatalf("apply synthetic schema mutation %q: %v", statement, err)
				}
			}
			if err := mutation.Close(); err != nil {
				t.Fatal(err)
			}

			if _, err := OpenReadOnly(ctx, path); err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("read-only validation error = %v, want %q", err, test.want)
			}
			if err := validateWriterDatabaseReadOnly(ctx, path); err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("writer preflight error = %v, want %q", err, test.want)
			}
		})
	}
}

func TestSchema9UsesPythonAffinityAndRejectsInvalidConstraints(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	var bucketJSONType string
	if err := writer.db.QueryRowContext(ctx, "SELECT type FROM pragma_table_info('aggregate_ring_slots') WHERE name='bucket_json'").Scan(&bucketJSONType); err != nil {
		t.Fatal(err)
	}
	if bucketJSONType != "TEXT" {
		t.Fatalf("aggregate_ring_slots.bucket_json type=%q, want TEXT", bucketJSONType)
	}

	invalid := []struct {
		name, query string
		args        []any
	}{
		{name: "negative source generation", query: "UPDATE schema_meta SET source_generation=-1 WHERE singleton=1"},
		{name: "coverage end before start", query: "INSERT INTO coverage_epochs VALUES ('cpu','schema-test','bad-coverage',10,9,1,1)"},
		{name: "unavailable non-positive span", query: "INSERT INTO unavailable_spans VALUES ('gpu','schema-test','bad-gap',10,10,1,'offline',1)"},
		{name: "misaligned ring slot", query: "INSERT INTO aggregate_ring_slots(resolution_seconds,slot_index,bucket_start,bucket_json,complete,source_generation,ring_generation,published_at,payload_version) VALUES (10,180,1,'{}',1,1,1,1,1)"},
		{name: "partial ring slot", query: "INSERT INTO aggregate_ring_slots(resolution_seconds,slot_index,bucket_start,bucket_json,complete,source_generation,ring_generation,published_at,payload_version) VALUES (10,180,NULL,'{}',0,0,0,0,0)"},
		{name: "misaligned invalidation", query: "INSERT INTO ring_invalidations(resolution_seconds,bucket_start,source_generation,reason,created_at) VALUES (10,1,1,'bad',1)"},
		{name: "negative applied timestamp", query: "INSERT INTO ring_invalidations(resolution_seconds,bucket_start,source_generation,reason,created_at,applied_at) VALUES (10,0,1,'bad',1,-1)"},
	}
	for _, test := range invalid {
		t.Run(test.name, func(t *testing.T) {
			if _, err := writer.db.ExecContext(ctx, test.query, test.args...); err == nil {
				t.Fatalf("invalid schema row was accepted: %s", test.query)
			}
		})
	}
}

func TestAppendBatchDuplicateReplayDoesNotAdvanceGeneration(t *testing.T) {
	writer, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	batch := writerBatch()
	if _, err = writer.AppendBatch(context.Background(), batch); err != nil {
		t.Fatal(err)
	}
	result, err := writer.AppendBatch(context.Background(), batch)
	if err != nil {
		t.Fatal(err)
	}
	if result.Generation != 1 || result.ObservationsDuplicate != 1 || result.CoverageDuplicate != 1 || result.UnavailableDuplicate != 1 || result.UsageDuplicate != 1 {
		t.Fatalf("replay result = %+v", result)
	}
}

func TestAppendUsageChunkBoundariesPreserveReplayRepairAndRollback(t *testing.T) {
	for _, size := range []int{1, maxUsageStatementRows - 1, maxUsageStatementRows, maxUsageStatementRows + 1, 2*maxUsageStatementRows + 1} {
		t.Run(fmt.Sprint(size), func(t *testing.T) {
			ctx := context.Background()
			writer, _ := checkpointTestWriter(t)
			atoms := make([]UsageAtom, size)
			var prior []UsageAtom
			for index := range atoms {
				atoms[index] = writerBatch().UsageAtoms[0]
				atoms[index].EventID = fmt.Sprintf("codex:thread-1:chunk-%d", index)
				atoms[index].Payload["model"] = "unknown"
				if index%2 == 0 {
					prior = append(prior, atoms[index])
				}
			}
			if _, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: prior}); err != nil {
				t.Fatal(err)
			}
			atoms[0].Payload = cloneUsagePayload(atoms[0].Payload)
			atoms[0].Payload["model"] = "gpt"
			result, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: atoms})
			if err != nil || result.UsageAccepted != size-len(prior)+1 || result.UsageDuplicate != len(prior)-1 || result.Generation != 2 {
				t.Fatalf("mixed chunk append = %+v, err=%v", result, err)
			}
			before, err := writer.refillBaseline(ctx, 0, 100)
			if err != nil || len(before) != size {
				t.Fatalf("mixed chunk readback = %d, err=%v", len(before), err)
			}
			// A conflict after a full fresh chunk must roll back facts and checkpoint.
			fresh := append([]UsageAtom{}, atoms...)
			for index := range fresh {
				fresh[index].EventID += "-new"
			}
			conflict := atoms[0]
			conflict.Payload = cloneUsagePayload(conflict.Payload)
			conflict.Payload["quantity"] = 99.0
			checkpoint := Checkpoint{Key: "chunk-test", Kind: "transcript", Version: 1}
			_, err = writer.AppendWithCheckpoints(ctx, AppendBatch{UsageAtoms: append(fresh, conflict)}, []Checkpoint{checkpoint})
			if err == nil || err.Error() != "usage atom identity conflicts with stored data" {
				t.Fatalf("chunk conflict = %v", err)
			}
			after, err := writer.refillBaseline(ctx, 0, 100)
			if err != nil || !reflect.DeepEqual(before, after) || writer.SourceGeneration() != 2 {
				t.Fatalf("chunk conflict changed facts/generation: err=%v", err)
			}
			if _, found, err := writer.LoadCheckpoint(ctx, checkpoint.Key); err != nil || found {
				t.Fatalf("chunk conflict committed checkpoint: found=%v err=%v", found, err)
			}
			for _, conflicting := range []bool{false, true} {
				duplicate := atoms[0]
				want := "append_batch.identity: duplicate idempotency key"
				if conflicting {
					duplicate = conflict
					want = "append_batch.identity: conflicting duplicate idempotency key"
				}
				_, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: append(append([]UsageAtom{}, atoms...), duplicate)})
				if err == nil || err.Error() != want {
					t.Fatalf("chunk duplicate = %v, want %q", err, want)
				}
			}
			// A repair after capture must survive replacement across chunk boundaries.
			repair := before[len(before)-1]
			repair.Payload = cloneUsagePayload(repair.Payload)
			if size > 1 {
				repair.Payload["model"] = "gpt"
				if _, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{repair}}); err != nil {
					t.Fatal(err)
				}
			}
			expected := size
			if size > 1 {
				expected--
			}
			result, err = writer.appendReplacingUsage(ctx, AppendBatch{UsageAtoms: before}, nil, nil, before)
			if err != nil || result.TombstonesAccepted != expected || result.UsageAccepted != expected || result.UsageDuplicate != 0 {
				t.Fatalf("chunk replacement = %+v, err=%v", result, err)
			}
			after, err = writer.refillBaseline(ctx, 0, 100)
			if err != nil || len(after) != size {
				t.Fatalf("chunk replacement changed row count: rows=%d err=%v", len(after), err)
			}
			for _, item := range after {
				if item.EventID == repair.EventID && !reflect.DeepEqual(item.Payload, repair.Payload) {
					t.Fatalf("chunk replacement lost repaired atom: %+v", item)
				}
			}
		})
	}
}

func TestAppendBatchConflictRollsBackEarlierRows(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err = writer.AppendBatch(context.Background(), writerBatch()); err != nil {
		t.Fatal(err)
	}
	beforeGeneration := writer.SourceGeneration()
	beforeCounts := make(map[string]int)
	for _, table := range []string{"observations", "coverage_epochs", "unavailable_spans", "usage_atoms", "ring_invalidations"} {
		var count int
		if err = writer.db.QueryRow("SELECT count(*) FROM " + table).Scan(&count); err != nil {
			t.Fatalf("count %s before conflict: %v", table, err)
		}
		beforeCounts[table] = count
	}
	conflict := writerBatch()
	conflict.Observations = append(conflict.Observations, Observation{EventID: "new", Family: "cpu", SourceID: "host", ObservedAt: 11, EpochID: "epoch-1", Payload: map[string]any{"value": 2}})
	conflict.UsageAtoms[0].Payload = map[string]any{"quantity": 99.0, "provider": "openai", "model": "gpt", "thread_id": "thread-1", "execution_source": "codex"}
	if _, err = writer.AppendBatch(context.Background(), conflict); err == nil {
		t.Fatal("conflict accepted")
	}
	if got := writer.SourceGeneration(); got != beforeGeneration {
		t.Fatalf("failed append advanced in-memory generation from %d to %d", beforeGeneration, got)
	}
	var durableGeneration int64
	if err = writer.db.QueryRow("SELECT source_generation FROM schema_meta WHERE singleton=1").Scan(&durableGeneration); err != nil {
		t.Fatal(err)
	}
	if durableGeneration != beforeGeneration {
		t.Fatalf("failed append advanced durable generation from %d to %d", beforeGeneration, durableGeneration)
	}
	for table, before := range beforeCounts {
		var after int
		if err = writer.db.QueryRow("SELECT count(*) FROM " + table).Scan(&after); err != nil {
			t.Fatalf("count %s after conflict: %v", table, err)
		}
		if after != before {
			t.Errorf("failed append changed %s row count from %d to %d", table, before, after)
		}
	}
}

func TestAppendBatchRollsBackWhenRingInvalidationWriteFails(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.AppendBatch(ctx, writerBatch()); err != nil {
		t.Fatal(err)
	}
	// Invalidations only target populated ring cells. Publish the cell that the
	// next append contradicts so the trigger exercises the late transaction step.
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{
		ResolutionSeconds: 10, BucketStart: 30, BucketJSON: `{"value":0}`, Complete: true,
	}}, writer.SourceGeneration(), 40, true); err != nil {
		t.Fatal(err)
	}
	readRows := func(table string) [][]any {
		t.Helper()
		probe, queryErr := writer.db.QueryContext(ctx, "SELECT * FROM "+table+" LIMIT 0")
		if queryErr != nil {
			t.Fatalf("read %s before failed append: %v", table, queryErr)
		}
		columns, columnsErr := probe.Columns()
		if columnsErr != nil {
			_ = probe.Close()
			t.Fatalf("read %s columns: %v", table, columnsErr)
		}
		if closeErr := probe.Close(); closeErr != nil {
			t.Fatalf("close %s schema probe: %v", table, closeErr)
		}
		order := make([]string, len(columns))
		for index := range columns {
			order[index] = fmt.Sprint(index + 1)
		}
		rows, queryErr := writer.db.QueryContext(ctx, "SELECT * FROM "+table+" ORDER BY "+strings.Join(order, ","))
		if queryErr != nil {
			t.Fatalf("read %s rows: %v", table, queryErr)
		}
		defer rows.Close()
		var result [][]any
		for rows.Next() {
			values := make([]any, len(columns))
			destinations := make([]any, len(columns))
			for index := range values {
				destinations[index] = &values[index]
			}
			if scanErr := rows.Scan(destinations...); scanErr != nil {
				t.Fatalf("scan %s before failed append: %v", table, scanErr)
			}
			result = append(result, values)
		}
		if rowsErr := rows.Err(); rowsErr != nil {
			t.Fatalf("iterate %s before failed append: %v", table, rowsErr)
		}
		return result
	}
	tables := []string{"observations", "coverage_epochs", "unavailable_spans", "usage_atoms", "ring_invalidations"}
	before := make(map[string][][]any, len(tables))
	for _, table := range tables {
		before[table] = readRows(table)
	}
	beforeGeneration := writer.SourceGeneration()
	if _, err := writer.db.ExecContext(ctx, `CREATE TRIGGER synthetic_abort_ring_invalidation
		BEFORE INSERT ON ring_invalidations
		BEGIN SELECT RAISE(ABORT, 'synthetic ring invalidation failure'); END`); err != nil {
		t.Fatal(err)
	}
	end := 40.0
	batch := AppendBatch{
		Observations:     []Observation{{EventID: "rollback-new-fact", Family: "cpu", SourceID: "host", ObservedAt: 30, EpochID: "epoch-2", Payload: map[string]any{"value": 7}}},
		CoverageEpochs:   []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "epoch-2", StartedAt: 30, EndedAt: &end, NativeCadenceSeconds: 1, OwnerGeneration: 2}},
		UnavailableSpans: []UnavailableSpan{{Family: "gpu", SourceID: "device-0", EpochID: "epoch-2", StartedAt: 30, EndedAt: 40, NativeCadenceSeconds: 1, Reason: "synthetic outage", OwnerGeneration: 2}},
	}
	if _, err := writer.AppendBatch(ctx, batch); err == nil || !strings.Contains(err.Error(), "synthetic ring invalidation failure") {
		t.Fatalf("append with failing synthetic invalidation trigger error = %v", err)
	}
	if got := writer.SourceGeneration(); got != beforeGeneration {
		t.Fatalf("failed append advanced in-memory generation from %d to %d", beforeGeneration, got)
	}
	var durableGeneration int64
	if err := writer.db.QueryRowContext(ctx, "SELECT source_generation FROM schema_meta WHERE singleton=1").Scan(&durableGeneration); err != nil {
		t.Fatal(err)
	}
	if durableGeneration != beforeGeneration {
		t.Fatalf("failed append advanced durable generation from %d to %d", beforeGeneration, durableGeneration)
	}
	for _, table := range tables {
		if after := readRows(table); !reflect.DeepEqual(after, before[table]) {
			t.Errorf("failed append changed %s rows: before=%#v after=%#v", table, before[table], after)
		}
	}
}

func TestCoverageUnavailableCoverageSameFamilySource(t *testing.T) {
	writer, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	end := 10.0
	if _, err = writer.AppendBatch(context.Background(), AppendBatch{CoverageEpochs: []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 0, EndedAt: &end, NativeCadenceSeconds: 1}}}); err != nil {
		t.Fatal(err)
	}
	if _, err = writer.AppendBatch(context.Background(), AppendBatch{UnavailableSpans: []UnavailableSpan{{Family: "cpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 10, EndedAt: 20, NativeCadenceSeconds: 1, Reason: "offline"}}}); err != nil {
		t.Fatal(err)
	}
	end = 30
	result, err := writer.AppendBatch(context.Background(), AppendBatch{CoverageEpochs: []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "epoch-2", StartedAt: 20, EndedAt: &end, NativeCadenceSeconds: 1}}})
	if err != nil || result.CoverageChanged != 1 {
		t.Fatalf("success after unavailable interval = %+v, err = %v", result, err)
	}
}

func TestCoverageAndUnavailableIntervalConflictsRollBack(t *testing.T) {
	writer, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	end := 10.0
	if _, err = writer.AppendBatch(context.Background(), AppendBatch{CoverageEpochs: []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 0, EndedAt: &end, NativeCadenceSeconds: 1}}}); err != nil {
		t.Fatal(err)
	}
	conflict := AppendBatch{
		Observations:     []Observation{{EventID: "kept", Family: "cpu", SourceID: "host", ObservedAt: 1, EpochID: "epoch-1", Payload: map[string]any{"value": 1}}},
		UnavailableSpans: []UnavailableSpan{{Family: "cpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 5, EndedAt: 15, NativeCadenceSeconds: 1, Reason: "offline"}},
	}
	if _, err = writer.AppendBatch(context.Background(), conflict); err == nil {
		t.Fatal("unavailable span overlapping coverage accepted")
	}
	var count int
	if err = writer.db.QueryRow("SELECT count(*) FROM observations").Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("rollback left %d observations", count)
	}

	if _, err = writer.AppendBatch(context.Background(), AppendBatch{UnavailableSpans: []UnavailableSpan{{Family: "gpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 0, EndedAt: 10, NativeCadenceSeconds: 1, Reason: "offline"}}}); err != nil {
		t.Fatal(err)
	}
	conflict = AppendBatch{
		Observations:   []Observation{{EventID: "kept-2", Family: "gpu", SourceID: "host", ObservedAt: 1, EpochID: "epoch-1", Payload: map[string]any{"value": 1}}},
		CoverageEpochs: []CoverageEpoch{{Family: "gpu", SourceID: "host", EpochID: "epoch-2", StartedAt: 5, NativeCadenceSeconds: 1}},
	}
	if _, err = writer.AppendBatch(context.Background(), conflict); err == nil {
		t.Fatal("coverage overlapping unavailable accepted")
	}
	if err = writer.db.QueryRow("SELECT count(*) FROM observations").Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("second rollback left %d observations", count)
	}
}

func TestWriterCheckpointReportsPinnedReaderProgress(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.db.ExecContext(ctx, "PRAGMA wal_autocheckpoint=0"); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "checkpoint-before-reader", Family: "cpu", SourceID: "host", ObservedAt: 10,
		EpochID: "synthetic", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 1.0},
	}}}); err != nil {
		t.Fatal(err)
	}
	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	readTx, err := reader.sqlDB.BeginTx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer readTx.Rollback()
	var initialCount int
	if err := readTx.QueryRowContext(ctx, "SELECT count(*) FROM observations").Scan(&initialCount); err != nil || initialCount != 1 {
		t.Fatalf("pin initial WAL snapshot: count=%d err=%v", initialCount, err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "checkpoint-after-reader", Family: "cpu", SourceID: "host", ObservedAt: 11,
		EpochID: "synthetic", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 2.0},
	}}}); err != nil {
		t.Fatal(err)
	}
	partial, err := writer.Checkpoint(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if partial.LogFrames <= partial.CheckpointedFrames {
		t.Fatalf("PASSIVE checkpoint hid incomplete reader-pinned progress: %+v", partial)
	}
	if err := readTx.Commit(); err != nil {
		t.Fatal(err)
	}
	complete, err := writer.Checkpoint(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if complete.Busy != 0 || complete.LogFrames != complete.CheckpointedFrames {
		t.Fatalf("checkpoint did not catch up after reader released its snapshot: %+v", complete)
	}
}

func TestCoverageEpochEndCannotMoveBackward(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	firstEnd := 1060.0
	if _, err := writer.AppendBatch(ctx, AppendBatch{CoverageEpochs: []CoverageEpoch{{
		Family: "cpu", SourceID: "host", EpochID: "epoch", StartedAt: 1000,
		EndedAt: &firstEnd, NativeCadenceSeconds: 10, OwnerGeneration: 1,
	}}}); err != nil {
		t.Fatal(err)
	}
	earlierEnd := 1020.0
	_, err = writer.AppendBatch(ctx, AppendBatch{CoverageEpochs: []CoverageEpoch{{
		Family: "cpu", SourceID: "host", EpochID: "epoch", StartedAt: 1000,
		EndedAt: &earlierEnd, NativeCadenceSeconds: 10, OwnerGeneration: 2,
	}}})
	if err == nil || !strings.Contains(err.Error(), "end cannot move backward") {
		t.Fatalf("backward coverage end error = %v", err)
	}
	var stored float64
	if err := writer.db.QueryRowContext(ctx, "SELECT ended_at FROM coverage_epochs WHERE family='cpu' AND source_id='host' AND epoch_id='epoch'").Scan(&stored); err != nil {
		t.Fatal(err)
	}
	if stored != firstEnd {
		t.Fatalf("backward coverage update changed stored end to %v", stored)
	}
}

func TestCoverageAndUnavailableCadenceChangesAreRejected(t *testing.T) {
	writer, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	end := 10.0
	coverage := CoverageEpoch{Family: "cpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 0, EndedAt: &end, NativeCadenceSeconds: 1}
	if _, err = writer.AppendBatch(context.Background(), AppendBatch{CoverageEpochs: []CoverageEpoch{coverage}}); err != nil {
		t.Fatal(err)
	}
	coverage.NativeCadenceSeconds = 2
	if _, err = writer.AppendBatch(context.Background(), AppendBatch{CoverageEpochs: []CoverageEpoch{coverage}}); err == nil {
		t.Fatal("coverage cadence change accepted")
	}
	span := UnavailableSpan{Family: "gpu", SourceID: "host", EpochID: "epoch-1", StartedAt: 0, EndedAt: 10, NativeCadenceSeconds: 1, Reason: "offline"}
	if _, err = writer.AppendBatch(context.Background(), AppendBatch{UnavailableSpans: []UnavailableSpan{span}}); err != nil {
		t.Fatal(err)
	}
	span.NativeCadenceSeconds = 2
	if _, err = writer.AppendBatch(context.Background(), AppendBatch{UnavailableSpans: []UnavailableSpan{span}}); err == nil {
		t.Fatal("unavailable cadence change accepted")
	}
}

func TestUsageTombstoneDeletesMatchingAtomAndReplayIsDuplicate(t *testing.T) {
	writer, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	batch := writerBatch()
	if _, err = writer.AppendBatch(context.Background(), batch); err != nil {
		t.Fatal(err)
	}
	batch.Observations = nil
	batch.CoverageEpochs = nil
	batch.UnavailableSpans = nil
	batch.UsageAtoms = nil
	batch.UsageTombstones = []UsageTombstone{{EventID: "codex:thread-1:event-1", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 10, Quantity: 4, Provider: "openai", Model: "gpt", ThreadID: "thread-1"}}
	result, err := writer.AppendBatch(context.Background(), batch)
	if err != nil || result.TombstonesAccepted != 1 {
		t.Fatalf("tombstone result = %+v, err = %v", result, err)
	}
	result, err = writer.AppendBatch(context.Background(), batch)
	if err != nil || result.TombstonesDuplicate != 1 || result.Generation != 2 {
		t.Fatalf("tombstone replay = %+v, err = %v", result, err)
	}
	var count int
	if err = writer.db.QueryRow("SELECT count(*) FROM usage_atoms").Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("tombstone left %d usage atoms", count)
	}
}

func TestWriterCanceledContext(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3")); !errors.Is(err, context.Canceled) {
		t.Fatalf("open error = %v", err)
	}
	writer, err := OpenWriter(context.Background(), filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err = writer.AppendBatch(ctx, writerBatch()); !errors.Is(err, context.Canceled) {
		t.Fatalf("append error = %v", err)
	}
	var count int
	if err = writer.db.QueryRow("SELECT count(*) FROM observations").Scan(&count); err != nil && !errors.Is(err, sql.ErrNoRows) {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("canceled append wrote %d rows", count)
	}
}

func TestWriterPruneDeletesAndClipsFactsAtomically(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	retainedEnd := 200.0
	if _, err = writer.AppendBatch(ctx, AppendBatch{
		Observations: []Observation{
			{EventID: "old", Family: "cpu", SourceID: "host", ObservedAt: 50, EpochID: "epoch", Payload: map[string]any{"value": 1}},
			{EventID: "new", Family: "cpu", SourceID: "host", ObservedAt: 150, EpochID: "epoch", Payload: map[string]any{"value": 2}},
		},
		CoverageEpochs:   []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "epoch", StartedAt: 0, EndedAt: &retainedEnd, NativeCadenceSeconds: 10}},
		UnavailableSpans: []UnavailableSpan{{Family: "gpu", SourceID: "host", EpochID: "gpu", StartedAt: 0, EndedAt: retainedEnd, NativeCadenceSeconds: 10, Reason: "offline"}},
		UsageAtoms: []UsageAtom{
			{EventID: "old-token", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 50, Payload: map[string]any{"quantity": 1}},
			{EventID: "new-token", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 150, Payload: map[string]any{"quantity": 2}},
		},
	}); err != nil {
		t.Fatal(err)
	}
	if _, err = writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 0, BucketJSON: `{"value":1}`, Complete: true}}, 1, 1, true); err != nil {
		t.Fatal(err)
	}
	result, err := writer.Prune(ctx, float64(RetentionSeconds+100))
	if err != nil {
		t.Fatal(err)
	}
	if result.Generation != 2 || result.Observations != 1 || result.UsageAtoms != 1 || result.CoverageClipped != 1 || result.UnavailableClipped != 1 {
		t.Fatalf("prune result = %+v", result)
	}
	var count int
	if err = writer.db.QueryRow("SELECT count(*) FROM observations WHERE observed_at < 100").Scan(&count); err != nil || count != 0 {
		t.Fatalf("old observations = %d, err=%v", count, err)
	}
	if err = writer.db.QueryRow("SELECT count(*) FROM usage_atoms WHERE observed_at < 100").Scan(&count); err != nil || count != 0 {
		t.Fatalf("old usage atoms = %d, err=%v", count, err)
	}
	var started float64
	if err = writer.db.QueryRow("SELECT started_at FROM coverage_epochs WHERE epoch_id='epoch'").Scan(&started); err != nil || started != 100 {
		t.Fatalf("clipped coverage start = %v, err=%v", started, err)
	}
	pending, err := writer.PendingInvalidationCells(ctx)
	if err != nil || len(pending) == 0 {
		t.Fatalf("pending prune invalidation = %+v, err=%v", pending, err)
	}
	t.Logf("pending invalidation = %+v", pending[0])
}

func TestWriterPruneCompletesWithActiveReadSnapshot(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{
		{EventID: "retained-old", Family: "cpu", SourceID: "host", ObservedAt: 50, EpochID: "epoch", Payload: map[string]any{"value": 1}},
		{EventID: "retained-new", Family: "cpu", SourceID: "host", ObservedAt: 150, EpochID: "epoch", Payload: map[string]any{"value": 2}},
	}}); err != nil {
		t.Fatal(err)
	}
	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	snapshot, closeSnapshot, err := reader.beginReadSnapshot(ctx)
	if err != nil {
		_ = reader.Close()
		t.Fatal(err)
	}
	defer reader.Close()
	before, err := snapshot.ReadObservations(ctx, 0, 200, MaxFactReadLimit)
	if err != nil || len(before) != 2 {
		t.Fatalf("active snapshot observations=%+v err=%v", before, err)
	}
	finished := make(chan error, 1)
	go func() {
		_, pruneErr := writer.Prune(ctx, float64(RetentionSeconds+100))
		finished <- pruneErr
	}()
	select {
	case pruneErr := <-finished:
		if pruneErr != nil {
			t.Fatalf("prune with active read snapshot failed: %v", pruneErr)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("prune blocked behind an active read snapshot")
	}
	closeSnapshot()
	after, err := reader.ReadObservations(ctx, 0, 200, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	if len(after) != 1 || after[0].EventID != "retained-new" {
		t.Fatalf("post-prune observations=%+v, want retained-new only", after)
	}
	if len(before) != 2 || before[0].EventID != "retained-old" {
		t.Fatalf("active snapshot changed after prune: %+v", before)
	}
}

func TestProductionScaleRetentionWithConcurrentReadersAndVacuum(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()

	// Keep the fixture large enough to exercise the production transaction and
	// WAL paths while remaining bounded for the normal and race test suites.
	now := float64(RetentionSeconds * 4)
	cutoff := now - float64(RetentionSeconds)
	const oldObservations = 12000
	const newObservations = 8000
	const oldUsage = 6000
	const newUsage = 4000
	batch := AppendBatch{
		Observations: make([]Observation, 0, oldObservations+newObservations),
		UsageAtoms:   make([]UsageAtom, 0, oldUsage+newUsage),
	}
	for index := 0; index < oldObservations; index++ {
		batch.Observations = append(batch.Observations, Observation{
			EventID: fmt.Sprintf("production-old-observation-%05d", index), Family: "cpu", SourceID: "host",
			ObservedAt: cutoff - 3600 - float64(index%30), EpochID: "production", OwnerGeneration: 1,
			Payload: map[string]any{"process_percent": index % 100},
		})
	}
	for index := 0; index < newObservations; index++ {
		batch.Observations = append(batch.Observations, Observation{
			EventID: fmt.Sprintf("production-new-observation-%05d", index), Family: "cpu", SourceID: "host",
			ObservedAt: cutoff + 1 + float64(index%300), EpochID: "production", OwnerGeneration: 1,
			Payload: map[string]any{"process_percent": index % 100},
		})
	}
	for index := 0; index < oldUsage; index++ {
		batch.UsageAtoms = append(batch.UsageAtoms, UsageAtom{
			EventID: fmt.Sprintf("production-old-usage-%05d", index), Direction: "input", Modality: "text",
			CacheRole: "none", Unit: "tokens", ObservedAt: cutoff - 3600 - float64(index%30),
			Payload: map[string]any{"quantity": float64(index + 1), "provider": "openai", "model": "gpt"},
		})
	}
	for index := 0; index < newUsage; index++ {
		batch.UsageAtoms = append(batch.UsageAtoms, UsageAtom{
			EventID: fmt.Sprintf("production-new-usage-%05d", index), Direction: "output", Modality: "text",
			CacheRole: "none", Unit: "tokens", ObservedAt: cutoff + 1 + float64(index%300),
			Payload: map[string]any{"quantity": float64(index + 1), "provider": "openai", "model": "gpt"},
		})
	}
	if _, err := writer.AppendBatch(ctx, batch); err != nil {
		t.Fatal(err)
	}

	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	readerCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	readerErrors := make(chan error, 4)
	var readers sync.WaitGroup
	for worker := 0; worker < 4; worker++ {
		readers.Add(1)
		go func() {
			defer readers.Done()
			for iteration := 0; iteration < 40; iteration++ {
				if _, err := reader.ReadObservations(readerCtx, cutoff-7200, now, MaxFactReadLimit); err != nil {
					select {
					case readerErrors <- err:
					default:
					}
					return
				}
			}
		}()
	}
	if _, err := writer.Prune(ctx, now); err != nil {
		t.Fatal(err)
	}
	readers.Wait()
	close(readerErrors)
	for err := range readerErrors {
		t.Fatalf("concurrent reader failed during prune: %v", err)
	}

	var count int
	if err := writer.db.QueryRowContext(ctx, "SELECT count(*) FROM observations WHERE observed_at < ?", cutoff).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("prune left %d old observations", count)
	}
	if err := writer.db.QueryRowContext(ctx, "SELECT count(*) FROM observations WHERE observed_at >= ?", cutoff).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != newObservations {
		t.Fatalf("retained observations = %d, want %d", count, newObservations)
	}
	if err := writer.db.QueryRowContext(ctx, "SELECT count(*) FROM usage_atoms WHERE observed_at < ?", cutoff).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("prune left %d old usage atoms", count)
	}
	if err := writer.db.QueryRowContext(ctx, "SELECT count(*) FROM usage_atoms WHERE observed_at >= ?", cutoff).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != newUsage {
		t.Fatalf("retained usage atoms = %d, want %d", count, newUsage)
	}

	if err := writer.Vacuum(ctx, now+1); err != nil {
		t.Fatal(err)
	}
	marker, err := writer.LastVacuumedAt(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if marker != now+1 {
		t.Fatalf("vacuum completion marker = %v, want %v", marker, now+1)
	}
	ratio := writer.reclaimableRatioAtLastVacuum()
	if ratio < 0 || ratio > 1 {
		t.Fatalf("vacuum reclaimable baseline ratio = %v", ratio)
	}
	if info, err := os.Stat(path + "-wal"); err == nil && info.Size() > WALAllocationCeilingBytes {
		t.Fatalf("WAL remains above allocation ceiling after vacuum: %d bytes", info.Size())
	}
}

func publishTestBuckets(t *testing.T, writer *Writer, resolution int, starts ...int64) {
	t.Helper()
	rows := make([]RingBucketWrite, 0, len(starts))
	for _, start := range starts {
		rows = append(rows, RingBucketWrite{ResolutionSeconds: resolution, BucketStart: start, BucketJSON: `{"value":1}`, Complete: true})
	}
	if _, err := writer.PublishRingBuckets(context.Background(), rows, 1, float64(100+len(starts)), true); err != nil {
		t.Fatal(err)
	}
}

func pendingStarts(t *testing.T, writer *Writer, resolution int) []int64 {
	t.Helper()
	rows, err := writer.PendingInvalidationCells(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	result := make([]int64, 0)
	for _, row := range rows {
		if row.ResolutionSeconds == resolution {
			result = append(result, row.BucketStart)
		}
	}
	return result
}

func TestAppendInvalidatesOnlyAcceptedPointMutations(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	first := AppendBatch{Observations: []Observation{{EventID: "first", Family: "cpu", SourceID: "host", ObservedAt: 0, EpochID: "e", Payload: map[string]any{"value": 1}}}}
	if _, err := writer.AppendBatch(ctx, first); err != nil {
		t.Fatal(err)
	}
	publishTestBuckets(t, writer, 10, 0, 10)
	second := AppendBatch{Observations: append(first.Observations, Observation{EventID: "second", Family: "cpu", SourceID: "host", ObservedAt: 10, EpochID: "e", Payload: map[string]any{"value": 2}})}
	if _, err := writer.AppendBatch(ctx, second); err != nil {
		t.Fatal(err)
	}
	if got := pendingStarts(t, writer, 10); len(got) != 1 || got[0] != 10 {
		t.Fatalf("pending point cells = %v, want [10]", got)
	}
}

func TestRepeatedMutationsKeepOnlyNewestPendingGeneration(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "seed", Family: "cpu", SourceID: "host", ObservedAt: 5, EpochID: "epoch", Payload: map[string]any{"value": 1},
	}}}); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{
		{ResolutionSeconds: 10, BucketStart: 0, BucketJSON: `{"value":1}`, Complete: true},
		{ResolutionSeconds: 10, BucketStart: 10, BucketJSON: `{"value":1}`, Complete: true},
	}, 1, 1, true); err != nil {
		t.Fatal(err)
	}
	for index := 0; index < 2; index++ {
		if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
			EventID: fmt.Sprintf("mutation-%d", index), Family: "cpu", SourceID: "host", ObservedAt: 6 + float64(index), EpochID: "epoch", Payload: map[string]any{"value": index + 2},
		}}}); err != nil {
			t.Fatal(err)
		}
	}
	var count, generation int
	if err := writer.db.QueryRowContext(ctx, "SELECT count(*), max(source_generation) FROM ring_invalidations WHERE resolution_seconds=10 AND bucket_start=0 AND applied_at IS NULL").Scan(&count, &generation); err != nil {
		t.Fatal(err)
	}
	if count != 1 || generation != 3 {
		t.Fatalf("pending generations = count %d generation %d, want one row at generation 3", count, generation)
	}
}

func TestConcurrentAppendsDoNotTriggerExternalGenerationReconciliation(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{
		{ResolutionSeconds: 10, BucketStart: 0, BucketJSON: `{"value":0}`, Complete: true},
	}, 0, 1, true); err != nil {
		t.Fatal(err)
	}
	const workers = 16
	errs := make(chan error, workers)
	var wg sync.WaitGroup
	for index := 0; index < workers; index++ {
		index := index
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, appendErr := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
				EventID: fmt.Sprintf("concurrent-%d", index), Family: "cpu", SourceID: "host", ObservedAt: float64(index + 1), EpochID: "epoch", Payload: map[string]any{"value": index},
			}}})
			errs <- appendErr
		}()
	}
	wg.Wait()
	close(errs)
	for appendErr := range errs {
		if appendErr != nil {
			t.Fatal(appendErr)
		}
	}
	var external int
	if err := writer.db.QueryRowContext(ctx, "SELECT count(*) FROM ring_invalidations WHERE reason='external_generation_reconciliation'").Scan(&external); err != nil {
		t.Fatal(err)
	}
	if external != 0 {
		t.Fatalf("concurrent appends triggered %d external reconciliation rows", external)
	}
}

func TestProductFreshScopesPendingCellsAndReloadsAfterRestart(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "seed", Family: "cpu", SourceID: "host", ObservedAt: 5, EpochID: "epoch", Payload: map[string]any{"value": 1},
	}}}); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{
		{ResolutionSeconds: 10, BucketStart: 0, BucketJSON: `{"value":1}`, Complete: true},
		{ResolutionSeconds: 10, BucketStart: 10, BucketJSON: `{"value":1}`, Complete: true},
	}, 1, 1, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "outside", Family: "cpu", SourceID: "host", ObservedAt: 15, EpochID: "epoch", Payload: map[string]any{"value": 2},
	}}}); err != nil {
		t.Fatal(err)
	}
	if !writer.ProductFresh(10, 0, 10) || writer.ProductFresh(10, 10, 20) {
		t.Fatalf("pending index did not scope the changed bucket")
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	restarted, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer restarted.Close()
	if !restarted.ProductFresh(10, 0, 10) || restarted.ProductFresh(10, 10, 20) {
		t.Fatalf("reloaded pending index did not preserve the changed bucket")
	}
}

func TestAppendReconcilesExternallyAdvancedGeneration(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	base := time.Now().Unix() - 100
	base -= base % 10
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{
		ResolutionSeconds: 10, BucketStart: base, BucketJSON: `{"value":0}`, Complete: true,
	}}, 1, 1, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, "INSERT INTO unavailable_spans VALUES ('gpu','gpu:0','legacy',?, ?,10,'driver unavailable',1)", base+5, base+7); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.db.ExecContext(ctx, `UPDATE schema_meta SET source_generation=2 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "current", Family: "cpu", SourceID: "host", ObservedAt: float64(base + 20), EpochID: "epoch", Payload: map[string]any{"value": 1},
	}}}); err != nil {
		t.Fatal(err)
	}
	if got := pendingStarts(t, writer, 10); len(got) != 1 || got[0] != base {
		t.Fatalf("external-generation pending cells = %v, want [%d]", got, base)
	}
}

func TestAppendAfterStartupReconciliationRescansWhenDataVersionChanges(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	baseSeconds := time.Now().Unix() - 100
	baseSeconds -= baseSeconds % 10
	base := float64(baseSeconds)
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "startup-fact", Family: "cpu", SourceID: "host", ObservedAt: base, EpochID: "epoch", Payload: map[string]any{"value": 1},
	}}}); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{
		{ResolutionSeconds: 10, BucketStart: int64(base), BucketJSON: `{"value":0}`, Complete: true},
		{ResolutionSeconds: 10, BucketStart: int64(base + 10), BucketJSON: `{"value":0}`, Complete: true},
		{ResolutionSeconds: 10, BucketStart: int64(base + 20), BucketJSON: `{"value":0}`, Complete: true},
	}, 1, base+1, true); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	mutate := func(eventID string, observedAt float64, generation int64) {
		database, openErr := sql.Open("sqlite3", writerURI(path))
		if openErr != nil {
			t.Fatal(openErr)
		}
		if _, execErr := database.ExecContext(ctx, `INSERT INTO observations(event_id,family,source_id,observed_at,epoch_id,owner_generation,payload_json) VALUES(?,?,?,?,?,?,?)`, eventID, "cpu", "host", observedAt, "external", 1, `{"value":2}`); execErr != nil {
			_ = database.Close()
			t.Fatal(execErr)
		}
		if _, execErr := database.ExecContext(ctx, "UPDATE schema_meta SET source_generation=? WHERE singleton=1", generation); execErr != nil {
			_ = database.Close()
			t.Fatal(execErr)
		}
		if closeErr := database.Close(); closeErr != nil {
			t.Fatal(closeErr)
		}
	}
	mutate("external-before-reopen", base+10, 2)

	reopened, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	mutate("external-after-reopen", base+20, 3)
	if _, err := reopened.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "current-fact", Family: "cpu", SourceID: "host", ObservedAt: base + 30, EpochID: "epoch", Payload: map[string]any{"value": 3},
	}}}); err != nil {
		t.Fatal(err)
	}
	pending, err := reopened.PendingInvalidationCells(ctx)
	if err != nil {
		t.Fatal(err)
	}
	foundExternal := false
	for _, cell := range pending {
		if cell.ResolutionSeconds == 10 && cell.BucketStart == int64(base+20) {
			foundExternal = true
			break
		}
	}
	if !foundExternal {
		t.Fatalf("external post-reopen fact was not reconciled: %+v", pending)
	}
}

func TestAppendAfterStartupReconciliationRescansSameGenerationAfterPricingInvalidation(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	base := time.Now().Unix() - 100
	base -= base % 10
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "startup-fact", Family: "cpu", SourceID: "host", ObservedAt: float64(base + 10), EpochID: "epoch", Payload: map[string]any{"value": 1},
	}}}); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{
		{ResolutionSeconds: 10, BucketStart: base + 10, BucketJSON: `{"value":0}`, Complete: true},
		{ResolutionSeconds: 10, BucketStart: base + 20, BucketJSON: `{"value":0}`, Complete: true},
		{ResolutionSeconds: 10, BucketStart: base + 30, BucketJSON: `{"value":0}`, Complete: true},
	}, 1, float64(base+21), true); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := sql.Open("sqlite3", writerURI(path))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := database.ExecContext(ctx, "UPDATE aggregate_publication SET source_generation=0, ring_generation=0, published_at=0 WHERE singleton=1"); err != nil {
		_ = database.Close()
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}

	reopened, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	external, err := sql.Open("sqlite3", writerURI(path))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := external.ExecContext(ctx, `INSERT INTO observations(event_id,family,source_id,observed_at,epoch_id,owner_generation,payload_json) VALUES(?,?,?,?,?,?,?)`, "same-generation-external", "cpu", "host", float64(base+20), "external", 1, `{"value":2}`); err != nil {
		_ = external.Close()
		t.Fatal(err)
	}
	if err := external.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := reopened.InvalidatePricingCatalog(ctx, float64(base+100)); err != nil {
		t.Fatal(err)
	}
	external, err = sql.Open("sqlite3", writerURI(path))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := external.ExecContext(ctx, `INSERT INTO observations(event_id,family,source_id,observed_at,epoch_id,owner_generation,payload_json) VALUES(?,?,?,?,?,?,?)`, "same-generation-external-after-pricing", "cpu", "host", float64(base+30), "external", 1, `{"value":3}`); err != nil {
		_ = external.Close()
		t.Fatal(err)
	}
	if err := external.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := reopened.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "current-fact", Family: "cpu", SourceID: "host", ObservedAt: float64(base + 40), EpochID: "epoch", Payload: map[string]any{"value": 4},
	}}}); err != nil {
		t.Fatal(err)
	}
	got := pendingStarts(t, reopened, 10)
	if !slices.Contains(got, base+20) {
		t.Fatalf("same-generation external bucket was not reconciled: %v, want bucket %d", got, base+20)
	}
	if !slices.Contains(got, base+30) {
		t.Fatalf("same-generation post-pricing external bucket was not reconciled: %v, want bucket %d", got, base+30)
	}
}

func TestInvalidationCreatedAtUsesCommitTime(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 0, BucketJSON: `{"value":0}`, Complete: true}}, 0, 1, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{
		EventID: "old-fact-time", Family: "cpu", SourceID: "host", ObservedAt: 1, EpochID: "epoch", Payload: map[string]any{"value": 1},
	}}}); err != nil {
		t.Fatal(err)
	}
	var createdAt float64
	if err := writer.db.QueryRowContext(ctx, "SELECT created_at FROM ring_invalidations WHERE resolution_seconds=10 AND bucket_start=0 AND applied_at IS NULL").Scan(&createdAt); err != nil {
		t.Fatal(err)
	}
	if createdAt <= 1 {
		t.Fatalf("invalidation created_at = %v, expected commit time after fact timestamp", createdAt)
	}
}

func TestCoverageAndUnavailableChangesInvalidateIntersectingCells(t *testing.T) {
	ctx := context.Background()
	writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	publishTestBuckets(t, writer, 10, 0, 10, 20)
	end := 10.0
	if _, err := writer.AppendBatch(ctx, AppendBatch{CoverageEpochs: []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "coverage", StartedAt: 0, EndedAt: &end, NativeCadenceSeconds: 1}}}); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{{ResolutionSeconds: 10, BucketStart: 0, BucketJSON: `{"value":2}`, Complete: true}}, 2, 104, true); err != nil {
		t.Fatal(err)
	}
	end = 30
	if _, err := writer.AppendBatch(ctx, AppendBatch{CoverageEpochs: []CoverageEpoch{{Family: "cpu", SourceID: "host", EpochID: "coverage", StartedAt: 0, EndedAt: &end, NativeCadenceSeconds: 1}}}); err != nil {
		t.Fatal(err)
	}
	if got := pendingStarts(t, writer, 10); len(got) != 2 || got[0] != 10 || got[1] != 20 {
		t.Fatalf("coverage pending cells = %v, want [10 20]", got)
	}
	if _, err := writer.PublishRingBuckets(ctx, []RingBucketWrite{
		{ResolutionSeconds: 10, BucketStart: 10, BucketJSON: `{"value":3}`, Complete: true},
		{ResolutionSeconds: 10, BucketStart: 20, BucketJSON: `{"value":4}`, Complete: true},
	}, 3, 105, true); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{UnavailableSpans: []UnavailableSpan{{Family: "gpu", SourceID: "host", EpochID: "unavailable", StartedAt: 10, EndedAt: 25, NativeCadenceSeconds: 1, Reason: "offline"}}}); err != nil {
		t.Fatal(err)
	}
	if got := pendingStarts(t, writer, 10); len(got) != 2 || got[0] != 10 || got[1] != 20 {
		t.Fatalf("unavailable pending cells = %v, want [10 20]", got)
	}
}
