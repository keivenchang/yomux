package main


package main

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

var runtimeMemoryTestRetained []byte

func TestRuntimeMemoryHistoryRetainsNewestBoundedWindow(t *testing.T) {
	state := &runtimeMemoryState{}
	start := time.Unix(1_000, 0)
	for index := 0; index < memoryHistoryLimit+3; index++ {
		state.sample(start.Add(time.Duration(index) * memorySampleInterval))
	}
	if got := len(state.history); got != memoryHistoryLimit {
		t.Fatalf("history length=%d, want limit %d", got, memoryHistoryLimit)
	}
	wantFirst := unixSeconds(start.Add(3 * memorySampleInterval))
	wantLast := unixSeconds(start.Add(time.Duration(memoryHistoryLimit+2) * memorySampleInterval))
	if state.history[0].At != wantFirst || state.history[len(state.history)-1].At != wantLast {
		t.Fatalf("history window=[%v,%v], want [%v,%v]", state.history[0].At, state.history[len(state.history)-1].At, wantFirst, wantLast)
	}
}

func TestReadRuntimeMemoryUsesByteUnitsAndDisjointClasses(t *testing.T) {
	sample := readRuntimeMemory(time.Unix(123, 0))
	if sample.PID != os.Getpid() || sample.At != 123 {
		t.Fatalf("identity/time = %+v", sample)
	}
	for _, name := range []string{"Heap objects", "Heap unused spans", "Heap free retained", "Goroutine stacks", "OS stacks", "Runtime metadata", "Profiling buckets", "Other runtime"} {
		if _, ok := sample.Categories[name]; !ok {
			t.Errorf("missing supported category %q (unavailable=%v)", name, sample.Unavailable)
		}
	}
	total := sample.Gauges["/memory/classes/total:bytes"]
	var classes uint64
	for _, value := range sample.Categories {
		classes += value
	}
	if classes > total {
		t.Fatalf("disjoint class total %d exceeds runtime total %d", classes, total)
	}
	for _, name := range []string{"/gc/heap/live:bytes", "/gc/heap/goal:bytes", "/gc/cycles/total:gc-cycles", "/sched/goroutines:goroutines", "/memory/classes/heap/released:bytes"} {
		if _, ok := sample.Gauges[name]; !ok {
			t.Errorf("missing gauge %s (unavailable=%v)", name, sample.Unavailable)
		}
	}
}

func TestRankHeapRecordsScalesAndPreservesAllocationStacks(t *testing.T) {
	var pcs [32]uintptr
	n := runtime.Callers(0, pcs[:])
	if n == 0 {
		t.Fatal("runtime.Callers returned no stack")
	}
	record := runtime.MemProfileRecord{AllocBytes: 8000, AllocObjects: 8, FreeBytes: 2000, FreeObjects: 2}
	copy(record.Stack0[:], pcs[:n])
	rows, err := rankHeapRecords([]runtime.MemProfileRecord{record}, 1000)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0].AllocBytes <= record.AllocBytes || rows[0].InuseBytes <= 0 || len(rows[0].Stack) == 0 || rows[0].Key == "" {
		t.Fatalf("sampled allocation was not scaled and attributed: %+v", rows)
	}
	if rows[0].Function == "" {
		t.Fatalf("stack has no source function: %+v", rows[0])
	}
}

func heapRecordFromOwnerOne() runtime.MemProfileRecord {
	var pcs [32]uintptr
	n := runtime.Callers(1, pcs[:])
	record := runtime.MemProfileRecord{AllocBytes: 6400, AllocObjects: 8, FreeBytes: 0, FreeObjects: 0}
	copy(record.Stack0[:], pcs[:n])
	return record
}

func heapRecordFromOwnerTwo() runtime.MemProfileRecord {
	var pcs [32]uintptr
	n := runtime.Callers(1, pcs[:])
	record := runtime.MemProfileRecord{AllocBytes: 9600, AllocObjects: 12, FreeBytes: 0, FreeObjects: 0}
	copy(record.Stack0[:], pcs[:n])
	return record
}

func TestRankHeapRecordsKeepsSharedAllocatorCallersSeparate(t *testing.T) {
	rows, err := rankHeapRecords([]runtime.MemProfileRecord{heapRecordFromOwnerOne(), heapRecordFromOwnerTwo()}, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 2 {
		t.Fatalf("unrelated caller stacks collapsed into %d ranked rows: %+v", len(rows), rows)
	}
	seen := map[string]bool{}
	for _, row := range rows {
		seen[strings.Join(row.Stack, "\n")] = true
	}
	if len(seen) != 2 {
		t.Fatalf("ranked rows do not preserve distinct full stacks: %+v", rows)
	}
}

func TestHashExecutable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "candidate")
	contents := []byte("candidate binary identity")
	if err := os.WriteFile(path, contents, 0600); err != nil {
		t.Fatal(err)
	}
	want := fmt.Sprintf("%x", sha256.Sum256(contents))
	got, err := hashExecutable(path)
	if err != nil {
		t.Fatal(err)
	}
	if got != want {
		t.Fatalf("hashExecutable() = %s, want %s", got, want)
	}
	if got := runningExecutableHashPath(path, "linux"); got != "/proc/self/exe" {
		t.Fatalf("Linux executable identity path=%s", got)
	}
	if got := runningExecutableHashPath(path, "darwin"); got != path {
		t.Fatalf("non-Linux executable identity path=%s", got)
	}
}

func TestWriteBoundedHeapProfileRejectsSwallowedOverflow(t *testing.T) {
	writer := &limitedHeapWriter{remaining: 3}
	err := writeBoundedHeapProfile(writer, func(destination io.Writer) error {
		_, _ = destination.Write([]byte("too large"))
		return nil
	})
	if err == nil || !writer.exceeded || writer.data.Len() != 0 {
		t.Fatalf("overflow was published: err=%v exceeded=%v bytes=%d", err, writer.exceeded, writer.data.Len())
	}
}

func retainKnownHeapAllocation() {
	runtimeMemoryTestRetained = make([]byte, 16<<20)
	for index := 0; index < len(runtimeMemoryTestRetained); index += 4096 {
		runtimeMemoryTestRetained[index] = 1
	}
}

func TestCollectHeapCaptureRanksKnownRetainedAllocation(t *testing.T) {
	previousRate := runtime.MemProfileRate
	runtime.MemProfileRate = 64 << 10
	defer func() { runtime.MemProfileRate = previousRate }()
	retainKnownHeapAllocation()
	runtime.GC()
	t.Cleanup(func() { runtimeMemoryTestRetained = nil })
	result, artifact, err := collectHeapCapture()
	runtime.KeepAlive(runtimeMemoryTestRetained)
	if err != nil {
		t.Fatal(err)
	}
	if result.ArtifactBytes == 0 || len(artifact) != result.ArtifactBytes || len(artifact) > heapProfileLimit {
		t.Fatalf("artifact length=%d metadata=%d limit=%d", len(artifact), result.ArtifactBytes, heapProfileLimit)
	}
	for _, row := range result.TopInuse {
		if row.InuseBytes > 0 && strings.Contains(strings.Join(row.Stack, "\n"), "retainKnownHeapAllocation") {
			return
		}
	}
	t.Fatalf("known retained allocation was absent from ranked heap capture: %+v", result.TopInuse)
}

func TestRuntimeMemoryCaptureBoundsConcurrencyAndFrequency(t *testing.T) {
	state := &runtimeMemoryState{}
	now := time.Unix(100, 0)
	entered, resume := make(chan struct{}), make(chan struct{})
	done := make(chan error, 1)
	go func() {
		_, err := state.capture(now, func() (*heapCapture, []byte, error) {
			close(entered)
			<-resume
			return &heapCapture{At: 100, InuseBytes: 42}, []byte("bounded"), nil
		})
		done <- err
	}()
	<-entered
	if status, err := state.capture(now, func() (*heapCapture, []byte, error) { t.Fatal("concurrent capture ran"); return nil, nil, nil }); status != http.StatusConflict || err == nil {
		t.Fatalf("concurrent capture = %d, %v", status, err)
	}
	close(resume)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if len(state.artifact) != len("bounded") || state.last == nil || state.last.InuseBytes != 42 {
		t.Fatalf("capture was not retained: %+v", state.last)
	}
	if status, err := state.capture(now.Add(30*time.Second), func() (*heapCapture, []byte, error) { t.Fatal("cooldown capture ran"); return nil, nil, nil }); status != http.StatusTooManyRequests || err == nil {
		t.Fatalf("cooldown capture = %d, %v", status, err)
	}
	if status, err := state.capture(now.Add(time.Minute), func() (*heapCapture, []byte, error) { return nil, nil, os.ErrInvalid }); status != http.StatusServiceUnavailable || err == nil || state.active {
		t.Fatalf("failed capture cleanup = %d, %v, active=%v", status, err, state.active)
	}
}

func TestProtectedHeapProfileRequiresAdminForCaptureAndDownload(t *testing.T) {
	dir := t.TempDir()
	auth := "users:\n  - username: guest\n    password: guest\n    role: readonly\n  - username: admin\n    password: admin\n    role: admin\n"
	if err := os.WriteFile(filepath.Join(dir, "auth.yaml"), []byte(auth), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "auth-cookie-secret"), []byte(strings.Repeat("11", 32)), 0600); err != nil {
		t.Fatal(err)
	}
	s := &server{config: dir}
	s.runtimeMemory.artifact = []byte("synthetic pprof")
	route := s.protected(http.HandlerFunc(s.runtimeHeapProfile), "admin")
	for _, tc := range []struct {
		name string
		user authUser
		want int
	}{{"readonly", authUser{username: "guest", password: "guest", role: "readonly"}, http.StatusForbidden}, {"admin", authUser{username: "admin", password: "admin", role: "admin"}, http.StatusOK}} {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/api/diagnostics/heap", nil)
			req.AddCookie(&http.Cookie{Name: s.authCookieName(), Value: s.cookieValue(tc.user)})
			rec := httptest.NewRecorder()
			route.ServeHTTP(rec, req)
			if rec.Code != tc.want {
				t.Fatalf("status=%d body=%s", rec.Code, rec.Body.String())
			}
			if tc.want == http.StatusOK && rec.Body.String() != "synthetic pprof" {
				t.Fatalf("artifact=%q", rec.Body.String())
			}
		})
	}
	request := httptest.NewRequest(http.MethodPost, "/api/diagnostics/heap", nil)
	request.AddCookie(&http.Cookie{Name: s.authCookieName(), Value: s.cookieValue(authUser{username: "guest", password: "guest", role: "readonly"})})
	recorder := httptest.NewRecorder()
	route.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusForbidden || !s.runtimeMemory.lastAttempt.IsZero() {
		t.Fatalf("readonly capture request status=%d capture_attempt=%v body=%s", recorder.Code, s.runtimeMemory.lastAttempt, recorder.Body.String())
	}
	adminCookie := &http.Cookie{Name: s.authCookieName(), Value: s.cookieValue(authUser{username: "admin", password: "admin", role: "admin"})}
	request = httptest.NewRequest(http.MethodPost, "/api/diagnostics/heap", nil)
	request.AddCookie(adminCookie)
	recorder = httptest.NewRecorder()
	route.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("admin capture status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var diagnostics map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &diagnostics); err != nil {
		t.Fatal(err)
	}
	last, ok := diagnostics["last"].(map[string]any)
	if !ok || last["binary_sha256"] == "" || last["artifact_bytes"].(float64) <= 0 {
		t.Fatalf("admin capture identity/artifact missing: %#v", diagnostics["last"])
	}
	request = httptest.NewRequest(http.MethodGet, "/api/diagnostics/heap", nil)
	request.AddCookie(adminCookie)
	recorder = httptest.NewRecorder()
	route.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK || recorder.Header().Get("Content-Disposition") != `attachment; filename="heap.pprof"` || recorder.Body.Len() != int(last["artifact_bytes"].(float64)) {
		t.Fatalf("admin artifact download status=%d headers=%v bytes=%d expected=%v", recorder.Code, recorder.Header(), recorder.Body.Len(), last["artifact_bytes"])
	}
}
