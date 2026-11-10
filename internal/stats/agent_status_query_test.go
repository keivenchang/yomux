package stats

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"math"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

func TestQueryAgentStatusFromSQLiteFixture(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO observations VALUES ('status-1','agent_status','host',125,'epoch',7,'{"counts":{"total":2,"running":1},"agents":[{"id":"agent-1"}],"states":{"agent-1":"working"}}')`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO usage_atoms VALUES ('token-in','input','text','none','tokens',90,'{"quantity":60,"provider":"openai","model":"gpt-5"}')`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO usage_atoms VALUES ('token-out','output','text','none','tokens',95,'{"quantity":30,"provider":"openai","model":"gpt-5"}')`); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	result, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(130, 0), RateWindow: 60 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	if !result.Available || result.SourceGeneration != 4 || result.Freshness == nil || result.Freshness.AgeSeconds != 5 {
		t.Fatalf("result metadata = %+v", result)
	}
	if result.Counts["running"] != float64(1) || len(result.Agents) != 1 || result.TokenRates.InputPerSecond != 1 || result.TokenRates.OutputPerSecond != 0.5 || len(result.TokenRates.ProviderModels) != 1 || result.TokenRates.ProviderModels[0].TotalPerSecond != 1.5 {
		t.Fatalf("result = %+v", result)
	}
	if _, err := json.Marshal(result); err != nil {
		t.Fatal(err)
	}
}

func TestQueryAgentStatusReportsUnavailableReason(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO unavailable_spans VALUES ('agent_status','host','epoch',100,110,10,'inventory-unavailable',7)`); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	result, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(130, 0)})
	if err != nil || result.Available || result.Unavailable == nil || result.Unavailable.Reason != "inventory-unavailable" {
		t.Fatalf("result = %+v, err = %v", result, err)
	}
}

func TestQueryAgentStatusTreatsStaleObservationAsUnavailable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO observations VALUES ('status-stale','agent_status','host',100,'epoch',7,'{"states":{"agent-1":"idle"}}')`); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	result, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(121, 0)})
	if err != nil || result.Available || result.Unavailable == nil || result.Unavailable.Reason != "agent-status-stale" {
		t.Fatalf("stale result = %+v, err = %v", result, err)
	}
}

func TestQueryAgentStatusUsesIdleCoverageCadenceForFreshness(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO observations VALUES ('status-idle','agent_status','host',100,'idle-epoch',7,'{"counts":{"total":1},"states":{"agent-1":"idle"}}')`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO coverage_epochs VALUES ('agent_status','host','idle-epoch',100,160,60,7)`); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	result, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(130, 0)})
	if err != nil || !result.Available || result.Unavailable != nil {
		t.Fatalf("idle-cadence result = %+v, err = %v; sample is within its 60-second cadence", result, err)
	}
	result, err = database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(160, 0)})
	if err != nil || !result.Available || result.Unavailable != nil {
		t.Fatalf("idle-cadence boundary result = %+v, err = %v; the cadence boundary remains fresh", result, err)
	}
	result, err = database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(170, 1)})
	if err != nil || result.Available || result.Unavailable == nil || result.Unavailable.Reason != "agent-status-stale" {
		t.Fatalf("idle successor scan exceeded its budget: %+v, err=%v", result, err)
	}
}

func TestQueryAgentStatusUnavailableAfterLatestObservationWins(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO observations VALUES ('status-old','agent_status','host',100,'epoch',7,'{"states":{"agent-1":"run"}}')`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO unavailable_spans VALUES ('agent_status','host','epoch-2',110,120,10,'pane-capture-unavailable',8)`); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	result, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(115, 0)})
	if err != nil || result.Available || result.Unavailable == nil || result.Unavailable.Reason != "pane-capture-unavailable" {
		t.Fatalf("unavailable result = %+v, err = %v", result, err)
	}
}

func TestQueryAgentStatusNewerObservationSupersedesStartedUnavailableSpan(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO observations VALUES ('status-recovered','agent_status','host',115,'epoch-3',9,'{"counts":{"total":1},"states":{"agent-1":"run"}}')`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO unavailable_spans VALUES ('agent_status','host','epoch-2',110,120,10,'pane-capture-unavailable',8)`); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	result, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(116, 0)})
	if err != nil || !result.Available || result.Unavailable != nil {
		t.Fatalf("recovered result = %+v, err = %v; observation at 115 should supersede a span that began at 110", result, err)
	}
}

func TestQueryAgentStatusIgnoresFutureUnavailableSpan(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO observations VALUES ('status-current','agent_status','host',100,'epoch',7,'{"states":{"agent-1":"idle"}}')`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO coverage_epochs VALUES ('agent_status','host','epoch',100,110,10,7)`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO unavailable_spans VALUES ('agent_status','host','future',110,120,10,'scheduled-failure',8)`); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	result, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(105, 0)})
	if err != nil || !result.Available || result.Unavailable != nil {
		t.Fatalf("future unavailable span changed a fresh status: %+v, err=%v", result, err)
	}
}

func TestAgentStatusGenerationUsesTheQuerySnapshot(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	writer, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.Exec("UPDATE schema_meta SET source_generation=5 WHERE singleton=1"); err != nil {
		t.Fatal(err)
	}
	result, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(130, 0)})
	if err != nil || result.SourceGeneration != 5 {
		t.Fatalf("source_generation=%d want5 err=%v", result.SourceGeneration, err)
	}
}

func TestAgentStatusPinsGenerationTokensAndStatusDuringConcurrentCommits(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	writer, err := sql.Open("sqlite3", writerURI(path))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	for _, query := range []string{
		"PRAGMA journal_mode=WAL",
		`INSERT INTO observations VALUES ('status','agent_status','host',125,'epoch',5,'{"counts":{"version":5}}')`,
		`INSERT INTO usage_atoms VALUES ('tokens','input','text','none','tokens',125,'{"quantity":5}')`,
		"UPDATE schema_meta SET source_generation=5 WHERE singleton=1",
	} {
		if _, err := writer.Exec(query); err != nil {
			t.Fatal(err)
		}
	}
	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	done, failures := make(chan struct{}), make(chan error, 1)
	go func() {
		defer close(done)
		for index := 0; index < 250; index++ {
			value := 5 + index%2
			tx, err := writer.Begin()
			if err != nil {
				failures <- err
				return
			}
			for _, statement := range []string{
				fmt.Sprintf("UPDATE schema_meta SET source_generation=%d WHERE singleton=1", value),
				fmt.Sprintf(`UPDATE observations SET payload_json='{"counts":{"version":%d}}' WHERE event_id='status'`, value),
				fmt.Sprintf(`UPDATE usage_atoms SET payload_json='{"quantity":%d}' WHERE event_id='tokens'`, value),
			} {
				if _, err := tx.Exec(statement); err != nil {
					tx.Rollback()
					failures <- err
					return
				}
			}
			if err := tx.Commit(); err != nil {
				failures <- err
				return
			}
		}
	}()
	defer func() { <-done }()
	for range 250 {
		result, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(130, 0)})
		if err != nil {
			t.Fatal(err)
		}
		version, _ := result.Counts["version"].(float64)
		if version != float64(result.SourceGeneration) || math.Abs(result.TokenRates.TotalPerSecond*60-version) > 1e-9 {
			t.Fatalf("mixed SQLite generations: generation=%d status=%v tokens=%v", result.SourceGeneration, version, result.TokenRates.TotalPerSecond*60)
		}
	}
	<-done
	select {
	case err := <-failures:
		t.Fatal(err)
	default:
	}
}

func TestQueryAgentStatusRejectsMalformedQuantities(t *testing.T) {
	for _, quantity := range []string{"NaN", "Inf", "1 garbage"} {
		t.Run(quantity, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "stats.sqlite3")
			createSQLiteFixture(t, path)
			writer, err := sql.Open("sqlite3", path)
			if err != nil {
				t.Fatal(err)
			}
			defer writer.Close()
			payload, _ := json.Marshal(map[string]any{"quantity": quantity})
			if _, err := writer.Exec(`INSERT INTO usage_atoms VALUES ('tokens','input','text','none','tokens',125,?)`, string(payload)); err != nil {
				t.Fatal(err)
			}
			database, err := OpenReadOnly(context.Background(), path)
			if err != nil {
				t.Fatal(err)
			}
			defer database.Close()
			result, err := database.QueryAgentStatus(context.Background(), AgentStatusQuery{Now: time.Unix(130, 0)})
			if err == nil {
				t.Fatalf("malformed numeric quantity admitted: %q rate=%v", quantity, result.TokenRates.TotalPerSecond)
			}
		})
	}
}
func TestObservationReadersPreserveOwnerGeneration(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	createSQLiteFixture(t, path)
	writer, err := sql.Open("sqlite3", path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	const want = int64(9007199254740993)
	if _, err := writer.Exec(`INSERT INTO observations VALUES ('large','host_cpu','host',125,'epoch',?,'{}')`, want); err != nil {
		t.Fatal(err)
	}
	database, err := OpenReadOnly(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	ordinary, err := database.ReadObservations(context.Background(), 120, 130, 10)
	if err != nil {
		t.Fatal(err)
	}
	paged, _, err := database.ReadObservationsPage(context.Background(), 120, 130, nil, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(ordinary) != 1 || len(paged) != 1 || ordinary[0].OwnerGeneration != uint64(want) || ordinary[0].OwnerGeneration != paged[0].OwnerGeneration {
		t.Fatalf("owner identity drift: ordinary=%v paged=%v", ordinary, paged)
	}
}

func TestOwnerGenerationNumericBoundaries(t *testing.T) {
	for _, value := range []any{uint64(math.MaxUint64), "18446744073709551615", []byte("18446744073709551615"), json.Number("18446744073709551615")} {
		got, err := uint64Value(value)
		if err != nil || got != math.MaxUint64 {
			t.Fatalf("exact owner generation %v: got %d, err %v", value, got, err)
		}
	}
	for _, value := range []any{"18446744073709551616", math.Exp2(64), -1, 1.5, "9007199254740992.5", "1e1000000000", "1e-1000000000", "1 garbage", "NaN", "Inf"} {
		if got, err := uint64Value(value); err == nil {
			t.Fatalf("invalid owner generation %v admitted as %d", value, got)
		}
	}
}

func TestOwnerGenerationExactDecimalCompatibility(t *testing.T) {
	for _, value := range []string{"1.0", "1e3", "9007199254740993.0", "9.007199254740993e15"} {
		want := uint64(9007199254740993)
		if value == "1.0" {
			want = 1
		}
		if value == "1e3" {
			want = 1000
		}
		if got, err := uint64Value(value); err != nil || got != want {
			t.Fatalf("exact decimal %q: got %d want %d, err %v", value, got, want, err)
		}
	}
}
