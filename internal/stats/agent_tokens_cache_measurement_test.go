package stats

import (
	"context"
	"path/filepath"
	"testing"
	"time"
)

func TestAgentTokenCollectorSyntheticCacheComparison(t *testing.T) {
	databasePath := copyOpenCodeFixture(t)
	makeCollector := func(cursorName string) *AgentTokenCollector {
		return NewAgentTokenCollector(countingTokenWriter{}, realHostCollectorClock{}, AgentTokenCollectorConfig{
			OpenCodeDatabase: databasePath,
			OpenCodeInventory: func(context.Context) ([]AgentRecord, []error) {
				return []AgentRecord{{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: "opencode"}}, nil
			},
			CursorPath: filepath.Join(t.TempDir(), cursorName), SkipTranscriptScan: true,
		})
	}

	const cycles = 50
	cached := makeCollector("cached.json")
	started := time.Now()
	for index := 0; index < cycles; index++ {
		cached.RunOnce(context.Background())
	}
	cachedDuration := time.Since(started)
	cachedCounters := cached.Counters()
	cached.Stop()

	reopened := makeCollector("reopened.json")
	started = time.Now()
	for index := 0; index < cycles; index++ {
		reopened.RunOnce(context.Background())
		reopened.Stop()
	}
	reopenedDuration := time.Since(started)
	reopenedCounters := reopened.Counters()

	t.Logf("synthetic OpenCode cache comparison: cycles=%d cached=%s reopened=%s cached_opens=%d cached_reuses=%d reopened_opens=%d reopened_reuses=%d cached_open_ms=%.3f reopened_open_ms=%.3f cached_collect_ms=%.3f reopened_collect_ms=%.3f cached_sqlite_queries=%d reopened_sqlite_queries=%d cached_sqlite_ms=%.3f reopened_sqlite_ms=%.3f", cycles, cachedDuration, reopenedDuration, cachedCounters.OpenCodeDatabaseOpens, cachedCounters.OpenCodeDatabaseReuses, reopenedCounters.OpenCodeDatabaseOpens, reopenedCounters.OpenCodeDatabaseReuses, cachedCounters.OpenCodeDatabaseOpenTotal.Seconds()*1000, reopenedCounters.OpenCodeDatabaseOpenTotal.Seconds()*1000, cachedCounters.OpenCodeCollectionTotal.Seconds()*1000, reopenedCounters.OpenCodeCollectionTotal.Seconds()*1000, cachedCounters.OpenCodeSQLiteQueries, reopenedCounters.OpenCodeSQLiteQueries, cachedCounters.OpenCodeSQLiteTotal.Seconds()*1000, reopenedCounters.OpenCodeSQLiteTotal.Seconds()*1000)
	if cachedCounters.OpenCodeDatabaseOpens != 1 || cachedCounters.OpenCodeDatabaseReuses != cycles-1 {
		t.Fatalf("cached lifecycle = %+v", cachedCounters)
	}
	if reopenedCounters.OpenCodeDatabaseOpens != cycles || reopenedCounters.OpenCodeDatabaseReuses != 0 {
		t.Fatalf("reopened lifecycle = %+v", reopenedCounters)
	}
}
