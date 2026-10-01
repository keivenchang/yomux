package stats

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"syscall"
	"testing"
	"time"
)

// This fixed synthetic workload is opt-in so normal gates do not mix process
// resource measurements with concurrently executing tests.
func TestAgentTokenCheckpointMeasurement(t *testing.T) {
	if os.Getenv("YOLOMUX_MEASURE_CHECKPOINTS") != "1" {
		t.Skip("set YOLOMUX_MEASURE_CHECKPOINTS=1")
	}
	const files, rows, cycles = 32, 128, 12
	root := t.TempDir()
	sources := make([]TranscriptSource, files)
	line := func(id int) string {
		return fmt.Sprintf(`{"type":"assistant","timestamp":"2026-09-01T00:00:00Z","message":{"id":"message-%d","model":"fixture","usage":{"input_tokens":1},"padding":"%s"}}`+"\n", id, strings.Repeat("x", 768))
	}
	for sourceIndex := range sources {
		path := filepath.Join(root, fmt.Sprintf("source-%02d.jsonl", sourceIndex))
		var body strings.Builder
		for row := 0; row < rows; row++ {
			body.WriteString(line(sourceIndex*rows + row))
		}
		if err := os.WriteFile(path, []byte(body.String()), 0600); err != nil {
			t.Fatal(err)
		}
		sources[sourceIndex] = TranscriptSource{Path: path, Provider: "claude", SourceID: fmt.Sprintf("fixture-%d", sourceIndex), ThreadID: fmt.Sprintf("thread-%d", sourceIndex)}
	}
	writer, err := OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	collector := NewAgentTokenCollector(writer, realHostCollectorClock{}, AgentTokenCollectorConfig{CursorPath: filepath.Join(root, "opencode.json"), TranscriptCursorPath: filepath.Join(root, "transcripts.json"), TranscriptSources: func(context.Context, []AgentRecord) ([]TranscriptSource, error) { return sources, nil }, TranscriptMaxBytesPerCycle: 64 << 20, TranscriptMaxRecordsPerCycle: files*rows + files, EpochStartedAt: 1788220800})
	defer collector.Stop()
	observed := float64(1788220800)
	run := func() {
		batch, receipts, err := collector.collectTranscripts(context.Background(), observed, 5, nil, nil, AppendBatch{}, nil)
		if err != nil {
			t.Fatal(err)
		}
		if ok, err := collector.appendCollected(context.Background(), batch, receipts); !ok || err != nil {
			t.Fatalf("append: ok=%v err=%v", ok, err)
		}
		observed += 5
	}
	measure := func(name string, prepare func(int), count int) {
		runtime.GC()
		var before, after runtime.MemStats
		runtime.ReadMemStats(&before)
		var cpuBefore, cpuAfter syscall.Rusage
		if err := syscall.Getrusage(syscall.RUSAGE_SELF, &cpuBefore); err != nil {
			t.Fatal(err)
		}
		durations := make([]time.Duration, 0, count)
		for index := 0; index < count; index++ {
			if prepare != nil {
				prepare(index)
			}
			start := time.Now()
			run()
			durations = append(durations, time.Since(start))
		}
		if err := syscall.Getrusage(syscall.RUSAGE_SELF, &cpuAfter); err != nil {
			t.Fatal(err)
		}
		runtime.GC()
		runtime.ReadMemStats(&after)
		sort.Slice(durations, func(i, j int) bool { return durations[i] < durations[j] })
		cpuNS := func(r syscall.Rusage) int64 { return r.Utime.Nano() + r.Stime.Nano() }
		status, err := os.ReadFile("/proc/self/status")
		if err != nil {
			t.Fatal(err)
		}
		rss := ""
		for _, row := range strings.Split(string(status), "\n") {
			if strings.HasPrefix(row, "VmRSS:") {
				rss = strings.TrimSpace(row)
			}
		}
		t.Logf("checkpoint_measurement mode=%s files=%d initial_rows=%d cycles=%d heap_retained_bytes=%d alloc_bytes=%d cpu_ms=%.3f p50_ms=%.3f p95_ms=%.3f rss=%s", name, files, rows, count, after.HeapAlloc, after.TotalAlloc-before.TotalAlloc, float64(cpuNS(cpuAfter)-cpuNS(cpuBefore))/1e6, float64(durations[len(durations)/2])/1e6, float64(durations[(len(durations)*95-1)/100])/1e6, rss)
		runtime.KeepAlive(collector)
	}
	measure("history", nil, 1)
	measure("unchanged", nil, cycles)
	measure("append", func(cycle int) {
		for sourceIndex, source := range sources {
			f, err := os.OpenFile(source.Path, os.O_APPEND|os.O_WRONLY, 0600)
			if err != nil {
				t.Fatal(err)
			}
			_, err = f.WriteString(line(files*rows + sourceIndex*cycles + cycle))
			closeErr := f.Close()
			if err != nil {
				t.Fatal(err)
			}
			if closeErr != nil {
				t.Fatal(closeErr)
			}
		}
	}, cycles)
}
