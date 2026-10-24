package stats

import (
	"context"
	"database/sql"
	"encoding/json"
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
