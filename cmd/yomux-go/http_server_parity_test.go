package main

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/stats"
	"github.com/keivenchang/yomux/internal/testsupport"
)

// syntheticStatsEnvironment is the only environment builder used by this
// parity harness. Development and test processes must not inherit host
// product roots, collector inputs, or disable flags; each caller supplies its
// fixture-owned values explicitly. In particular, appending overrides to
// os.Environ is unsafe because exec.Cmd keeps the first duplicate key.
func syntheticStatsEnvironment(overrides ...string) []string {
	return testsupport.SyntheticEnvironment(overrides...)
}

func TestSyntheticStatsEnvironmentRemovesAmbientHostInputs(t *testing.T) {
	t.Setenv("HOME", "/host/home")
	t.Setenv("XDG_DATA_HOME", "/host/data")
	t.Setenv("CODEX_HOME", "/host/codex")
	t.Setenv("YOMUX_ROOT", "/host/yomux")
	t.Setenv("YOMUX_STATS_DISABLE_COLLECTORS", "0")
	t.Setenv("YOLOMUX_OPENCODE_DATABASE", "/host/opencode.db")
	t.Setenv("YOLOMUX_CLAUDE_TRANSCRIPT_ROOT", "/host/claude")
	t.Setenv("YOLOMUX_CODEX_TRANSCRIPT_ROOT", "/host/codex-sessions")
	t.Setenv("YOLOMUX_STATS_DISABLE_COLLECTORS", "0")
	environment := syntheticStatsEnvironment(
		"HOME=/fixture/home",
		"PYTHONPATH=/fixture/source",
		"YOLOMUX_STATS_DISABLE_COLLECTORS=1",
	)
	values := make(map[string][]string)
	for _, entry := range environment {
		key, value, ok := strings.Cut(entry, "=")
		if ok {
			values[key] = append(values[key], value)
		}
	}
	for _, key := range []string{
		"XDG_DATA_HOME", "CODEX_HOME", "YOMUX_ROOT", "YOMUX_STATS_DISABLE_COLLECTORS", "YOLOMUX_OPENCODE_DATABASE",
		"YOLOMUX_CLAUDE_TRANSCRIPT_ROOT", "YOLOMUX_CODEX_TRANSCRIPT_ROOT",
	} {
		if _, present := values[key]; present {
			t.Fatalf("ambient host input %s survived synthetic environment: %v", key, values[key])
		}
	}
	for key, expected := range map[string]string{
		"HOME": "/fixture/home", "PYTHONPATH": "/fixture/source", "YOLOMUX_STATS_DISABLE_COLLECTORS": "1",
	} {
		if got := values[key]; !reflect.DeepEqual(got, []string{expected}) {
			t.Fatalf("synthetic override %s = %v, want [%q]", key, got, expected)
		}
	}
}

// TestStatsProductionPythonAndGoObservationBoundaryParity compares the real
// Python and compiled Go HTTP owners at the browser-upload boundary.  The
// parser-only corpus is useful for fast feedback, but it cannot catch route
// status/header drift, writer readiness, duplicate receipts, or persistence
// differences between the two serving processes.

// normalizeProductionSSESnapshot removes only values that depend on the two
// independently scheduled test processes crossing a live bucket boundary.
// The populated bucket payload, source counts, series values, and cost report
// remain exact parity requirements.  HTTP snapshot parity separately checks
// no-data spans and populated bucket starts.  The stream request is made after
// that snapshot probe, so the two independently scheduled servers can cross a
// ten-second boundary between the probes; omitting no-data here prevents that
// wall-clock race from masquerading as an SSE framing mismatch.

// productionNoDataShape validates the only timing-sensitive no-data form in
// the live production fixture. The two servers build their current ring on
// independent clocks, so an open persisted bucket may be historical for one
// request and still the right edge for the other. Real unavailable spans are
// not permitted by this fixture and therefore remain a hard test failure.

type synchronizedBuffer struct {
	mu sync.Mutex
	bytes.Buffer
}

func (buffer *synchronizedBuffer) Write(value []byte) (int, error) {
	buffer.mu.Lock()
	defer buffer.mu.Unlock()
	return buffer.Buffer.Write(value)
}

func (buffer *synchronizedBuffer) ReadFrom(source io.Reader) (int64, error) {
	var total int64
	var chunk [32 * 1024]byte
	for {
		count, err := source.Read(chunk[:])
		if count > 0 {
			written, writeErr := buffer.Write(chunk[:count])
			total += int64(written)
			if writeErr != nil {
				return total, writeErr
			}
		}
		if err != nil {
			if err == io.EOF {
				return total, nil
			}
			return total, err
		}
	}
}

func (buffer *synchronizedBuffer) String() string {
	buffer.mu.Lock()
	defer buffer.mu.Unlock()
	return buffer.Buffer.String()
}

// parityProcess owns Wait and output drainage, including early exits and shutdown failures.
type parityProcess struct {
	command  *exec.Cmd
	output   *synchronizedBuffer
	done     chan struct{}
	waitErr  error
	stopOnce sync.Once
	stopErr  error
}

func startParityProcess(t *testing.T, command *exec.Cmd, output *synchronizedBuffer) *parityProcess {
	t.Helper()
	command.WaitDelay = 2 * time.Second
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	process := &parityProcess{command: command, output: output, done: make(chan struct{})}
	go func() { process.waitErr = command.Wait(); close(process.done) }()
	t.Cleanup(func() { process.checkStop(t) })
	return process
}

func (process *parityProcess) exited() bool {
	select {
	case <-process.done:
		return true
	default:
		return false
	}
}

func (process *parityProcess) readyLine(timeout time.Duration) (string, error) {
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		line, _, complete := strings.Cut(process.output.String(), "\n")
		if process.exited() {
			return "", fmt.Errorf("child exited before readiness: %s", process.output.String())
		}
		if complete {
			return line, nil
		}
		select {
		case <-timer.C:
			return "", fmt.Errorf("child readiness timeout: %s", process.output.String())
		case <-ticker.C:
		}
	}
}

func (process *parityProcess) stop(timeout time.Duration) error {
	return process.stopWithSignal(timeout, func() error { return process.command.Process.Signal(os.Interrupt) })
}

func (process *parityProcess) stopWithSignal(timeout time.Duration, signal func() error) error {
	process.stopOnce.Do(func() {
		early := process.exited()
		var signalErr error
		forced := false
		if !early {
			signalErr = signal()
			timer := time.NewTimer(timeout)
			select {
			case <-process.done:
			case <-timer.C:
				forced = true
				_ = process.command.Process.Kill()
				<-process.done
			}
			timer.Stop()
		}
		output := process.output.String()
		if strings.Contains(output, "WARNING: DATA RACE") || strings.Contains(output, "ThreadSanitizer") {
			process.stopErr = fmt.Errorf("child race report: %s", output)
		} else if forced {
			process.stopErr = fmt.Errorf("child ignored shutdown signal: %s", output)
		} else if early {
			process.stopErr = fmt.Errorf("child exited before shutdown: %v: %s", process.waitErr, output)
		} else if signalErr != nil {
			process.stopErr = fmt.Errorf("child shutdown signal failed: %v: %s", signalErr, output)
		} else if process.waitErr != nil {
			exit, ok := process.waitErr.(*exec.ExitError)
			expectedInterrupt := false
			if ok {
				status, valid := exit.Sys().(syscall.WaitStatus)
				expectedInterrupt = valid && status.Signaled() && status.Signal() == syscall.SIGINT
			}
			if !expectedInterrupt {
				process.stopErr = fmt.Errorf("child failed: %v: %s", process.waitErr, output)
			}
		}
	})
	return process.stopErr
}

func (process *parityProcess) checkStop(t *testing.T) {
	t.Helper()
	if err := process.stop(5 * time.Second); err != nil {
		t.Error(err)
	}
}

func TestParityProcessShutdownControls(t *testing.T) {
	for _, test := range []struct {
		name, body, want string
		early            bool
	}{
		{"drain", "signal.signal(signal.SIGINT, lambda *_: (print('tail' * 65536, flush=True), sys.exit(0)))", "", false},
		{"exit66", "signal.signal(signal.SIGINT, lambda *_: sys.exit(66))", "child failed", false},
		{"raceAfterReady", "signal.signal(signal.SIGINT, lambda *_: (print('WARNING: DATA RACE', flush=True), sys.exit(0)))", "child race report", false},
		{"earlyExit", "sys.exit(0)", "before shutdown", true},
		{"ignoredInterrupt", "signal.signal(signal.SIGINT, signal.SIG_IGN)", "ignored shutdown", false},
	} {
		t.Run(test.name, func(t *testing.T) {
			script := "import signal,sys,time\n" + test.body + "\nprint('READY', flush=True)\nwhile True: signal.pause()\n"
			command := exec.Command("python3", "-u", "-c", script)
			var output synchronizedBuffer
			command.Stdout, command.Stderr = &output, &output
			command.WaitDelay = time.Second
			if err := command.Start(); err != nil {
				t.Fatal(err)
			}
			process := &parityProcess{command: command, output: &output, done: make(chan struct{})}
			go func() { process.waitErr = command.Wait(); close(process.done) }()
			defer process.stop(time.Second)
			deadline := time.NewTimer(5 * time.Second)
			defer deadline.Stop()
			ticker := time.NewTicker(time.Millisecond)
			defer ticker.Stop()
			for !strings.Contains(output.String(), "READY") && !process.exited() {
				select {
				case <-deadline.C:
					t.Fatal("child readiness timeout")
				case <-ticker.C:
				}
			}
			if test.early {
				<-process.done
			}
			started := time.Now()
			err := process.stop(100 * time.Millisecond)
			if test.want == "" {
				if err != nil {
					t.Fatal(err)
				}
				if !strings.Contains(output.String(), strings.Repeat("tail", 65536)) {
					t.Fatal("child output was not drained")
				}
			} else if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("stop = %v, want %q", err, test.want)
			}
			if time.Since(started) > 3*time.Second {
				t.Fatal("shutdown exceeded bound")
			}
		})
	}
}

func TestParityProcessRejectsExitBeforeWaitNotification(t *testing.T) {
	command := exec.Command("python3", "-c", "pass")
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	if err := command.Wait(); err != nil {
		t.Fatal(err)
	}
	// Wait can reap the process before its output-copy goroutines complete. Hold
	// the completion notification to reproduce shutdown during that interval.
	process := &parityProcess{command: command, output: &synchronizedBuffer{}, done: make(chan struct{})}
	err := process.stopWithSignal(time.Second, func() error {
		// The real OS signal must fail before the Wait notification is delivered.
		err := command.Process.Signal(os.Interrupt)
		close(process.done)
		return err
	})
	if err == nil || !strings.Contains(err.Error(), "shutdown signal failed") {
		t.Fatalf("stop accepted an exit before output drainage completed: %v", err)
	}
}

func TestStatsConcurrentStreamsDoNotBlockSnapshotReads(t *testing.T) {
	ctx := context.Background()
	now := time.Unix(2000, 0)
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := stats.OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.AppendBatch(ctx, stats.AppendBatch{
		Observations: []stats.Observation{{
			EventID: "stream-cpu", Family: "cpu", SourceID: "host", ObservedAt: 1990,
			EpochID: "cpu", OwnerGeneration: 1,
			Payload: map[string]any{"process_percent": 20.0, "system_percent": 40.0},
		}},
		CoverageEpochs: []stats.CoverageEpoch{{
			Family: "cpu", SourceID: "host", EpochID: "cpu", StartedAt: 1800,
			NativeCadenceSeconds: 1, OwnerGeneration: 1,
		}},
	}); err != nil {
		t.Fatal(err)
	}
	service := publishedStatsService(t, writer, []stats.ProductKey{{RangeSeconds: 300, ResolutionSeconds: 10}}, now)
	server := httptest.NewServer(statsParityMux(&server{statsPath: path, statsService: service, statsNow: func() time.Time { return now }, statsHeartbeat: time.Hour}))
	defer server.Close()

	const streamCount = 8
	release := make(chan struct{})
	ready := make(chan heldStatsStream, streamCount)
	for index := 0; index < streamCount; index++ {
		go holdStatsStream(t, server.URL+"/api/stats-stream?range_seconds=300&resolution=10&client_id=soak-"+strconv.Itoa(index), release, ready)
	}
	held := make([]heldStatsStream, 0, streamCount)
	for index := 0; index < streamCount; index++ {
		select {
		case stream := <-ready:
			held = append(held, stream)
		case <-time.After(5 * time.Second):
			close(release)
			t.Fatal("concurrent stats streams did not all become ready")
		}
	}
	started := time.Now()
	response := getRawResponse(t, server.URL+"/api/stats-snapshot?range_seconds=300&resolution=10&client_id=latency-probe")
	elapsed := time.Since(started)
	if response.status != http.StatusOK {
		t.Fatalf("snapshot read during concurrent streams = %+v", response)
	}
	if elapsed > time.Second {
		t.Fatalf("snapshot read blocked behind concurrent streams: %s", elapsed)
	}
	close(release)
	for _, stream := range held {
		stream.cancel()
		_ = stream.response.Body.Close()
	}
}

type heldStatsStream struct {
	cancel   context.CancelFunc
	response *http.Response
}

func holdStatsStream(t *testing.T, url string, release <-chan struct{}, ready chan<- heldStatsStream) {
	t.Helper()
	request, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		t.Errorf("build held stats stream request: %v", err)
		return
	}
	ctx, cancel := context.WithCancel(request.Context())
	request = request.WithContext(ctx)
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		cancel()
		t.Errorf("open held stats stream: %v", err)
		return
	}
	if response.StatusCode != http.StatusOK {
		cancel()
		_ = response.Body.Close()
		t.Errorf("held stats stream status = %d", response.StatusCode)
		return
	}
	scanner := bufio.NewScanner(response.Body)
	for scanner.Scan() {
		if scanner.Text() == "event: ready" {
			ready <- heldStatsStream{cancel: cancel, response: response}
			<-release
			return
		}
	}
	cancel()
	_ = response.Body.Close()
	if err := scanner.Err(); err != nil && !strings.Contains(err.Error(), "use of closed network connection") {
		t.Errorf("held stats stream ended before ready: %v", err)
	}
}

type rawHTTPResponse struct {
	status      int
	contentType string
	headers     map[string]string
	body        string
}

func getRawResponse(t *testing.T, url string) rawHTTPResponse {
	t.Helper()
	response, err := http.Get(url)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	headers := map[string]string{}
	for _, name := range []string{"Cache-Control", "Connection", "X-Accel-Buffering"} {
		if value := response.Header.Get(name); value != "" {
			headers[name] = value
		}
	}
	return rawHTTPResponse{status: response.StatusCode, contentType: response.Header.Get("Content-Type"), headers: headers, body: string(body)}
}

func statsParityMux(server *server) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/stats-capabilities", server.statsCapabilities)
	mux.HandleFunc("/api/stats-snapshot", server.statsSnapshot)
	mux.HandleFunc("/api/stats-delta", server.statsDelta)
	mux.HandleFunc("/api/stats-stream", server.statsStream)
	return mux
}
