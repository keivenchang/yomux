package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"runtime"
	"runtime/debug"
	"runtime/metrics"
	"runtime/pprof"
	"sort"
	"strings"
	"sync"
	"time"
)

const memoryHistoryLimit = 720
const memorySampleInterval = 5 * time.Second
const heapProfileLimit = 32 << 20
const heapRecordLimit = 131072
const heapCaptureInterval = time.Minute

// These classes partition runtime-managed, unreleased memory. They are not RSS:
// Go cannot tell which of its pages the OS has made resident.
var memoryClassMetrics = map[string][]string{
	"Heap objects":       {"/memory/classes/heap/objects:bytes"},
	"Heap unused spans":  {"/memory/classes/heap/unused:bytes"},
	"Heap free retained": {"/memory/classes/heap/free:bytes"},
	"Goroutine stacks":   {"/memory/classes/heap/stacks:bytes"},
	"OS stacks":          {"/memory/classes/os-stacks:bytes"},
	"Runtime metadata":   {"/memory/classes/metadata/mcache/free:bytes", "/memory/classes/metadata/mcache/inuse:bytes", "/memory/classes/metadata/mspan/free:bytes", "/memory/classes/metadata/mspan/inuse:bytes", "/memory/classes/metadata/other:bytes"},
	"Profiling buckets":  {"/memory/classes/profiling/buckets:bytes"},
	"Other runtime":      {"/memory/classes/other:bytes"},
}
var memoryGaugeMetrics = []string{"/memory/classes/total:bytes", "/memory/classes/heap/released:bytes", "/gc/heap/live:bytes", "/gc/heap/goal:bytes", "/gc/cycles/total:gc-cycles", "/sched/goroutines:goroutines"}

type runtimeMemorySample struct {
	At          float64           `json:"at"`
	PID         int               `json:"pid"`
	Categories  map[string]uint64 `json:"categories"`
	Gauges      map[string]uint64 `json:"gauges"`
	Unavailable []string          `json:"unavailable"`
}
type heapAllocation struct {
	Key        string   `json:"key"`
	Function   string   `json:"function"`
	Stack      []string `json:"stack"`
	InuseBytes int64    `json:"inuse_bytes"`
	AllocBytes int64    `json:"alloc_bytes"`
}
type heapCapture struct {
	At            float64          `json:"at"`
	RankedAt      float64          `json:"ranked_at"`
	ArtifactAt    float64          `json:"artifact_at"`
	PID           int              `json:"pid"`
	Binary        string           `json:"binary"`
	BinarySHA256  string           `json:"binary_sha256"`
	Build         string           `json:"build"`
	GoVersion     string           `json:"go_version"`
	DurationMS    float64          `json:"duration_ms"`
	SampleRate    int              `json:"sample_rate_bytes"`
	InuseBytes    int64            `json:"inuse_bytes"`
	AllocBytes    int64            `json:"alloc_bytes"`
	TopInuse      []heapAllocation `json:"top_inuse"`
	TopAlloc      []heapAllocation `json:"top_alloc"`
	OtherInuse    int64            `json:"other_inuse_bytes"`
	ArtifactBytes int              `json:"artifact_bytes"`
}
type runtimeMemoryState struct {
	mu          sync.Mutex
	history     []runtimeMemorySample
	lastSample  time.Time
	active      bool
	lastAttempt time.Time
	last        *heapCapture
	captures    []*heapCapture
	artifact    []byte
	lastError   string
}

func readRuntimeMemory(at time.Time) runtimeMemorySample {
	names := append([]string{}, memoryGaugeMetrics...)
	for _, group := range memoryClassMetrics {
		names = append(names, group...)
	}
	samples := make([]metrics.Sample, len(names))
	for i, name := range names {
		samples[i].Name = name
	}
	metrics.Read(samples)
	result := runtimeMemorySample{At: unixSeconds(at), PID: os.Getpid(), Categories: map[string]uint64{}, Gauges: map[string]uint64{}, Unavailable: []string{}}
	for _, sample := range samples {
		if sample.Value.Kind() != metrics.KindUint64 {
			result.Unavailable = append(result.Unavailable, sample.Name)
			continue
		}
		result.Gauges[sample.Name] = sample.Value.Uint64()
	}
	for category, group := range memoryClassMetrics {
		var sum uint64
		available := true
		for _, name := range group {
			value, ok := result.Gauges[name]
			sum += value
			available = available && ok
		}
		if available {
			result.Categories[category] = sum
		}
	}
	sort.Strings(result.Unavailable)
	return result
}

func (m *runtimeMemoryState) sample(at time.Time) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if !m.lastSample.IsZero() && at.Sub(m.lastSample) < memorySampleInterval {
		return
	}
	m.lastSample = at
	m.history = append(m.history, readRuntimeMemory(at))
	if len(m.history) > memoryHistoryLimit {
		copy(m.history, m.history[len(m.history)-memoryHistoryLimit:])
		m.history = m.history[:memoryHistoryLimit]
	}
}

func (m *runtimeMemoryState) diagnostics() map[string]any {
	m.sample(time.Now())
	m.mu.Lock()
	defer m.mu.Unlock()
	history := append([]runtimeMemorySample{}, m.history...)
	captures := append([]*heapCapture{}, m.captures...)
	return map[string]any{"available": true, "unit": "bytes", "history": history, "sample": history[len(history)-1], "active": m.active, "last": m.last, "captures": captures, "error": m.lastError, "capture_interval_seconds": heapCaptureInterval.Seconds(), "history_limit": memoryHistoryLimit,
		"sample_interval_seconds": memorySampleInterval.Seconds(), "reason": "Runtime classes partition Go-managed unreleased memory, not resident pages or exact struct sizes. Heap stacks are sampled estimates, may lag two GC cycles, and exclude native allocations. No forced GC."}
}

// runtime.MemProfile uses Poisson sampling. Match the scaling in Go's heap
// profile writer rather than presenting raw sampled bytes as the whole heap.
func scaledHeapBytes(bytes, objects int64, rate int) int64 {
	if bytes <= 0 || objects <= 0 {
		return 0
	}
	if rate <= 1 {
		return bytes
	}
	scale := 1 / -math.Expm1(-float64(bytes)/float64(objects)/float64(rate))
	return int64(float64(bytes) * scale)
}
func rankHeapRecords(records []runtime.MemProfileRecord, rate int) ([]heapAllocation, error) {
	if len(records) > heapRecordLimit {
		return nil, fmt.Errorf("heap profile exceeds %d allocation stacks", heapRecordLimit)
	}
	byStack := make(map[string]heapAllocation, len(records))
	for _, record := range records {
		inuse := scaledHeapBytes(record.InUseBytes(), record.InUseObjects(), rate)
		allocated := scaledHeapBytes(record.AllocBytes, record.AllocObjects, rate)
		if inuse == 0 && allocated == 0 {
			continue
		}
		frames := runtime.CallersFrames(record.Stack())
		stack := make([]string, 0, 8)
		ownerFunction := ""
		for {
			frame, more := frames.Next()
			if frame.Function != "" {
				formatted := fmt.Sprintf("%s %s:%d", frame.Function, frame.File, frame.Line)
				stack = append(stack, formatted)
				if ownerFunction == "" && !strings.HasPrefix(frame.Function, "runtime.") && !strings.HasPrefix(frame.Function, "internal/runtime/") {
					ownerFunction = frame.Function
				}
			}
			if !more {
				break
			}
		}
		if ownerFunction == "" && len(stack) > 0 {
			ownerFunction = stack[0]
		}
		// A shared allocator can appear under unrelated owners. Rank complete stacks so
		// each row's bytes always describe the exact stack displayed in the UI.
		keyHash := sha256.Sum256([]byte(strings.Join(stack, "\n")))
		key := fmt.Sprintf("%x", keyHash[:8])
		row := byStack[key]
		if row.Key == "" {
			row = heapAllocation{Key: key, Function: ownerFunction, Stack: stack}
		}
		row.InuseBytes += inuse
		row.AllocBytes += allocated
		byStack[key] = row
	}
	rows := make([]heapAllocation, 0, len(byStack))
	for _, row := range byStack {
		rows = append(rows, row)
	}
	return rows, nil
}

type limitedHeapWriter struct {
	data      bytes.Buffer
	remaining int
	exceeded  bool
}

func (w *limitedHeapWriter) Write(p []byte) (int, error) {
	if len(p) > w.remaining {
		w.exceeded = true
		return 0, fmt.Errorf("heap artifact exceeds %d bytes", heapProfileLimit)
	}
	n, err := w.data.Write(p)
	w.remaining -= n
	return n, err
}

func writeBoundedHeapProfile(writer *limitedHeapWriter, write func(io.Writer) error) error {
	if err := write(writer); err != nil {
		return err
	}
	// runtime/pprof's protobuf writer ignores gzip Write and Close errors. Check
	// the underlying bounded writer so an over-limit partial profile is rejected.
	if writer.exceeded {
		return fmt.Errorf("heap artifact exceeds %d bytes", heapProfileLimit)
	}
	return nil
}

func hashExecutable(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}

func runningExecutableHashPath(path, goos string) string {
	if goos == "linux" {
		// /proc/self/exe remains attached to the mapped executable if its path is
		// replaced or unlinked while this process is running.
		return "/proc/self/exe"
	}
	return path
}

func collectHeapCapture() (*heapCapture, []byte, error) {
	started := time.Now()
	rankedAt := time.Now()
	n, _ := runtime.MemProfile(nil, true)
	if n > heapRecordLimit {
		return nil, nil, fmt.Errorf("heap profile has %d allocation stacks; limit is %d", n, heapRecordLimit)
	}
	// A little headroom permits new stacks during the read without an unbounded retry.
	records := make([]runtime.MemProfileRecord, min(n+256, heapRecordLimit))
	n, ok := runtime.MemProfile(records, true)
	if !ok {
		return nil, nil, fmt.Errorf("heap allocation stack set grew during capture; capture again after cooldown")
	}
	rows, err := rankHeapRecords(records[:n], runtime.MemProfileRate)
	if err != nil {
		return nil, nil, err
	}
	result := &heapCapture{At: unixSeconds(started), RankedAt: unixSeconds(rankedAt), PID: os.Getpid(), SampleRate: runtime.MemProfileRate, GoVersion: runtime.Version()}
	result.Binary, err = os.Executable()
	if err != nil {
		return nil, nil, err
	}
	result.BinarySHA256, err = hashExecutable(runningExecutableHashPath(result.Binary, runtime.GOOS))
	if err != nil {
		return nil, nil, err
	}
	result.Build = runtimeBuildRevision()
	if result.Build == "" {
		result.Build = "revision unavailable"
	}
	for _, row := range rows {
		result.InuseBytes += row.InuseBytes
		result.AllocBytes += row.AllocBytes
	}
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].InuseBytes == rows[j].InuseBytes {
			return rows[i].Key < rows[j].Key
		}
		return rows[i].InuseBytes > rows[j].InuseBytes
	})
	result.TopInuse = append([]heapAllocation{}, rows[:min(12, len(rows))]...)
	result.OtherInuse = result.InuseBytes
	for _, row := range result.TopInuse {
		result.OtherInuse -= row.InuseBytes
	}
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].AllocBytes == rows[j].AllocBytes {
			return rows[i].Key < rows[j].Key
		}
		return rows[i].AllocBytes > rows[j].AllocBytes
	})
	result.TopAlloc = append([]heapAllocation{}, rows[:min(12, len(rows))]...)
	writer := &limitedHeapWriter{remaining: heapProfileLimit}
	result.ArtifactAt = unixSeconds(time.Now())
	if err := writeBoundedHeapProfile(writer, pprof.WriteHeapProfile); err != nil {
		return nil, nil, err
	}
	artifactRecords, _ := runtime.MemProfile(nil, true)
	if artifactRecords > heapRecordLimit {
		return nil, nil, fmt.Errorf("heap profile grew to %d allocation stacks; limit is %d", artifactRecords, heapRecordLimit)
	}
	result.ArtifactBytes = writer.data.Len()
	result.DurationMS = time.Since(started).Seconds() * 1000
	return result, writer.data.Bytes(), nil
}

func runtimeBuildRevision() string {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return ""
	}
	for _, setting := range info.Settings {
		if setting.Key == "vcs.revision" {
			if setting.Value == "" {
				return ""
			}
			for _, metadata := range info.Settings {
				if metadata.Key == "vcs.modified" && metadata.Value == "true" {
					return setting.Value + " (modified)"
				}
			}
			return setting.Value
		}
	}
	return ""
}

func (m *runtimeMemoryState) capture(now time.Time, collect func() (*heapCapture, []byte, error)) (int, error) {
	m.mu.Lock()
	if m.active {
		m.mu.Unlock()
		return http.StatusConflict, fmt.Errorf("heap capture already active")
	}
	if !m.lastAttempt.IsZero() && now.Sub(m.lastAttempt) < heapCaptureInterval {
		m.mu.Unlock()
		return http.StatusTooManyRequests, fmt.Errorf("heap capture cooldown is one minute")
	}
	m.active = true
	m.lastAttempt = now
	m.mu.Unlock()
	result, artifact, err := collect()
	m.mu.Lock()
	defer m.mu.Unlock()
	m.active = false
	if err != nil {
		m.lastError = err.Error()
		return http.StatusServiceUnavailable, err
	}
	m.lastError = ""
	m.last = result
	m.artifact = artifact
	m.captures = append(m.captures, result)
	if len(m.captures) > 24 {
		m.captures = append([]*heapCapture{}, m.captures[len(m.captures)-24:]...)
	}
	return http.StatusOK, nil
}

func (s *server) runtimeHeapProfile(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	switch r.Method {
	case http.MethodPost:
		status, err := s.runtimeMemory.capture(time.Now(), collectHeapCapture)
		if err != nil {
			jsonResponse(w, status, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		jsonResponse(w, http.StatusOK, s.runtimeMemory.diagnostics())
	case http.MethodGet:
		s.runtimeMemory.mu.Lock()
		artifact := s.runtimeMemory.artifact
		s.runtimeMemory.mu.Unlock()
		if len(artifact) == 0 {
			jsonResponse(w, http.StatusNotFound, map[string]any{"ok": false, "error": "No completed heap capture"})
			return
		}
		w.Header().Set("Content-Type", "application/octet-stream")
		w.Header().Set("Content-Disposition", `attachment; filename="heap.pprof"`)
		_, _ = io.Copy(w, bytes.NewReader(artifact))
	default:
		w.Header().Set("Allow", "GET, POST")
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}
