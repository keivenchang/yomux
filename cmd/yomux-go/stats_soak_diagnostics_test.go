// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptrace"
	"os"
	"path/filepath"
	"runtime"
	"runtime/trace"
	"sync"
	"syscall"
	"testing"
	"time"
)

type statsSoakStage struct {
	Name      string `json:"name"`
	ElapsedNS int64  `json:"elapsed_ns"`
	Detail    string `json:"detail,omitempty"`
}

type statsSoakRead struct {
	Value string `json:"value,omitempty"`
	Error string `json:"error,omitempty"`
}

type statsSoakResources struct {
	At          time.Time                `json:"at"`
	UserCPUNS   int64                    `json:"user_cpu_ns"`
	SystemCPUNS int64                    `json:"system_cpu_ns"`
	UsageError  string                   `json:"usage_error,omitempty"`
	Files       map[string]statsSoakRead `json:"files"`
	Threads     map[string]statsSoakRead `json:"thread_schedstat"`
}

func captureStatsSoakResources() statsSoakResources {
	result := statsSoakResources{At: time.Now(), Files: map[string]statsSoakRead{}, Threads: map[string]statsSoakRead{}}
	var usage syscall.Rusage
	if err := syscall.Getrusage(syscall.RUSAGE_SELF, &usage); err != nil {
		result.UsageError = err.Error()
	} else {
		result.UserCPUNS = int64(usage.Utime.Sec)*int64(time.Second) + int64(usage.Utime.Usec)*int64(time.Microsecond)
		result.SystemCPUNS = int64(usage.Stime.Sec)*int64(time.Second) + int64(usage.Stime.Usec)*int64(time.Microsecond)
	}
	read := func(path string) statsSoakRead {
		data, err := os.ReadFile(path)
		if err != nil {
			return statsSoakRead{Error: err.Error()}
		}
		return statsSoakRead{Value: string(data)}
	}
	for _, path := range []string{"/proc/self/stat", "/proc/self/cgroup", "/proc/pressure/cpu", "/sys/fs/cgroup/cpu.max", "/sys/fs/cgroup/cpu.stat", "/sys/fs/cgroup/cpu.pressure"} {
		result.Files[path] = read(path)
	}
	threads, err := os.ReadDir("/proc/self/task")
	if err != nil {
		result.Files["/proc/self/task"] = statsSoakRead{Error: err.Error()}
	} else {
		for _, thread := range threads {
			result.Threads[thread.Name()] = read(filepath.Join("/proc/self/task", thread.Name(), "schedstat"))
		}
	}
	return result
}

type statsSoakDiagnostics struct {
	started  time.Time
	mu       sync.Mutex
	stages   map[string][]statsSoakStage
	listener string
}

func newStatsSoakDiagnostics(t *testing.T, database string) *statsSoakDiagnostics {
	t.Helper()
	d := &statsSoakDiagnostics{started: time.Now(), stages: map[string][]statsSoakStage{}}
	output := os.Getenv("TEST_UNDECLARED_OUTPUTS_DIR")
	if output == "" {
		output = t.TempDir()
	}
	output = filepath.Join(output, t.Name())
	if err := os.MkdirAll(output, 0700); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(database)
	if err != nil {
		t.Fatal(err)
	}
	identity := map[string]any{"path": database, "size": info.Size(), "mtime": info.ModTime()}
	if stat, ok := info.Sys().(*syscall.Stat_t); ok {
		identity["device"] = stat.Dev
		identity["inode"] = stat.Ino
	}
	before := captureStatsSoakResources()
	const windowBytes = 16 << 20
	const windowAge = 10 * time.Second
	recorder := trace.NewFlightRecorder(trace.FlightRecorderConfig{MaxBytes: windowBytes, MinAge: windowAge})
	if err := recorder.Start(); err != nil {
		t.Fatalf("start fixture execution trace: %v", err)
	}
	// Record only this workload with a rolling 16 MiB target, not a memory bound.
	// Artifact writes happen after requests settle, outside their latency budget.
	t.Cleanup(func() {
		after := captureStatsSoakResources()
		tracePath := filepath.Join(output, "execution.trace")
		traceFile, openErr := os.Create(tracePath)
		var traceBytes int64
		var traceErr error
		if openErr != nil {
			traceErr = openErr
		} else {
			traceBytes, traceErr = recorder.WriteTo(traceFile)
			if err := traceFile.Close(); traceErr == nil {
				traceErr = err
			}
		}
		recorder.Stop()
		d.mu.Lock()
		manifest := map[string]any{"test": t.Name(), "pid": os.Getpid(), "parent_pid": os.Getppid(), "database": identity, "listener": d.listener,
			"gomaxprocs": runtime.GOMAXPROCS(0), "logical_cpus": runtime.NumCPU(), "shard_index": os.Getenv("TEST_SHARD_INDEX"), "total_shards": os.Getenv("TEST_TOTAL_SHARDS"),
			"default_transport": fmt.Sprintf("%T:%p", http.DefaultTransport, http.DefaultTransport), "started": d.started, "resources_before": before, "resources_after": after, "requests": d.stages,
			"trace_path": tracePath, "trace_bytes": traceBytes, "trace_window_target_bytes": windowBytes, "trace_window_age_ns": int64(windowAge)}
		if traceErr != nil {
			manifest["trace_error"] = traceErr.Error()
		}
		body, encodeErr := json.MarshalIndent(manifest, "", "  ")
		d.mu.Unlock()
		manifestPath := filepath.Join(output, "requests.json")
		if encodeErr != nil {
			t.Errorf("encode soak diagnostics: %v", encodeErr)
		} else if err := os.WriteFile(manifestPath, append(body, '\n'), 0600); err != nil {
			t.Errorf("write soak diagnostics: %v", err)
		}
		if traceErr != nil {
			t.Errorf("write fixture execution trace: %v", traceErr)
		}
		t.Logf("HTTP/SSE diagnostic artifacts: requests=%s trace=%s bytes=%d", manifestPath, tracePath, traceBytes)
	})
	return d
}

func (d *statsSoakDiagnostics) mark(ctx context.Context, id, name, detail string) {
	elapsed := time.Since(d.started).Nanoseconds()
	d.mu.Lock()
	d.stages[id] = append(d.stages[id], statsSoakStage{Name: name, ElapsedNS: elapsed, Detail: detail})
	d.mu.Unlock()
	trace.Log(ctx, name, id+" "+detail)
}

func (d *statsSoakDiagnostics) handler(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Yomux-Soak-Request")
		if id == "" {
			id = r.URL.Query().Get("client_id")
		}
		ctx, task := trace.NewTask(r.Context(), "stats-soak-handler")
		defer task.End()
		d.mark(ctx, id, "handler_enter", r.URL.Path)
		defer d.mark(ctx, id, "handler_return", r.URL.Path)
		next(w, r.WithContext(ctx))
	}
}

func (d *statsSoakDiagnostics) request(url, id string) (*http.Request, func(), error) {
	ctx, task := trace.NewTask(context.Background(), "stats-soak-client")
	hooks := &httptrace.ClientTrace{
		GetConn: func(host string) { d.mark(ctx, id, "get_conn", host) },
		GotConn: func(info httptrace.GotConnInfo) {
			d.mark(ctx, id, "got_conn", fmt.Sprintf("reused=%t was_idle=%t idle_ns=%d local=%s remote=%s", info.Reused, info.WasIdle, info.IdleTime.Nanoseconds(), info.Conn.LocalAddr(), info.Conn.RemoteAddr()))
		},
		ConnectStart: func(network, addr string) { d.mark(ctx, id, "connect_start", network+" "+addr) },
		ConnectDone: func(network, addr string, err error) {
			d.mark(ctx, id, "connect_done", fmt.Sprintf("%s %s error=%v", network, addr, err))
		},
		WroteRequest:         func(info httptrace.WroteRequestInfo) { d.mark(ctx, id, "wrote_request", fmt.Sprint(info.Err)) },
		GotFirstResponseByte: func() { d.mark(ctx, id, "first_response_byte", "") },
	}
	request, err := http.NewRequestWithContext(httptrace.WithClientTrace(ctx, hooks), http.MethodGet, url, nil)
	if err != nil {
		task.End()
		return nil, func() {}, err
	}
	request.Header.Set("X-Yomux-Soak-Request", id)
	return request, task.End, nil
}
