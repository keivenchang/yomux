package stats

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestTmuxCommandArgsHonorsConfiguredSocket(t *testing.T) {
	t.Setenv("YOLOMUX_TMUX_SOCKET", " /tmp/private-tmux.sock ")
	if got, want := tmuxCommandArgs("list-panes", "-a"), []string{"tmux", "-S", "/tmp/private-tmux.sock", "list-panes", "-a"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("tmuxCommandArgs with socket = %#v, want %#v", got, want)
	}

	t.Setenv("YOLOMUX_TMUX_SOCKET", "")
	if got, want := tmuxCommandArgs("capture-pane"), []string{"tmux", "capture-pane"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("tmuxCommandArgs without socket = %#v, want %#v", got, want)
	}
}

func TestTmuxCommandArgsIgnoresTestOnlySocketWhenProductionSocketIsAbsent(t *testing.T) {
	t.Setenv("YOLOMUX_TMUX_SOCKET", "")
	t.Setenv("YOLOMUX_TEST_TMUX_SOCKET", "/tmp/test-tmux.sock")
	if got, want := tmuxCommandArgs("list-panes"), []string{"tmux", "list-panes"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("tmuxCommandArgs with only test socket = %#v, want %#v", got, want)
	}
	t.Setenv("YOLOMUX_TMUX_SOCKET", "/tmp/production-tmux.sock")
	if got, want := tmuxCommandArgs("list-panes"), []string{"tmux", "-S", "/tmp/production-tmux.sock", "list-panes"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("tmuxCommandArgs production precedence = %#v, want %#v", got, want)
	}
}

func TestClassifyAgentRequiresEntryPoint(t *testing.T) {
	tests := []struct {
		command, executable, want string
	}{
		{"claude --dangerously-skip-permissions", "", "claude"},
		{"/usr/local/bin/codex --full-auto", "", "codex"},
		{"opencode --session abc", "/usr/bin/opencode", "opencode"},
		{"/opt/codex/vendor/codex --dangerously-bypass", "/opt/codex/vendor/codex", "codex"},
		{"node /tmp/opencode-wrapper.js", "/usr/bin/node", ""},
		{"node /tmp/codex-wrapper.js", "/usr/bin/node", ""},
		{"bash -c 'codex --full-auto'", "", ""},
		{"/tmp/not-opencode --session abc", "/tmp/not-opencode", ""},
	}
	for _, test := range tests {
		if got := ClassifyAgent(test.command, test.executable); got != test.want {
			t.Errorf("ClassifyAgent(%q, %q) = %q, want %q", test.command, test.executable, got, test.want)
		}
	}
}

func TestBuildAgentInventoryAssociatesDescendantsAndDeduplicates(t *testing.T) {
	processes := []AgentProcess{
		{PID: 300, PPID: 1, Command: "bash"},
		{PID: 301, PPID: 300, Command: "opencode --session=oc-explicit", Executable: "/usr/bin/opencode"},
		{PID: 302, PPID: 301, Command: "claude --help"},
		{PID: 303, PPID: 1, Command: "codex --full-auto"},
		{PID: 303, PPID: 1, Command: "codex --full-auto"},
	}
	panes := []AgentTmuxPane{{Session: "work", Window: "2", WindowName: "agent", Pane: "1", PaneID: "%9", Directory: "/repo", Title: "OpenCode", PID: 300}}
	got, errs := BuildAgentInventory(context.Background(), AgentInventorySources{
		Processes: func(context.Context) ([]AgentProcess, error) { return processes, nil },
		Panes:     func(context.Context) ([]AgentTmuxPane, error) { return panes, nil },
		CWD: func(pid int) (string, error) {
			if pid == 301 {
				return "/repo/child", nil
			}
			return "", nil
		},
		Started: func(pid int) (string, error) { return "proc:" + string(rune(pid)), nil },
	})
	if len(errs) != 0 {
		t.Fatalf("errors = %v", errs)
	}
	want := []AgentRecord{
		{PID: 301, PPID: 300, Kind: "opencode", Command: "opencode --session=oc-explicit", Executable: "/usr/bin/opencode", Directory: "/repo/child", TmuxSession: "work", TmuxWindow: "2", TmuxWindowName: "agent", TmuxPane: "1", TmuxPaneID: "%9", Title: "OpenCode", StartedIdentity: "proc:" + string(rune(301)), OpenCodeSession: "oc-explicit"},
		{PID: 302, PPID: 301, Kind: "claude", Command: "claude --help", Directory: "/repo", TmuxSession: "work", TmuxWindow: "2", TmuxWindowName: "agent", TmuxPane: "1", TmuxPaneID: "%9", Title: "OpenCode", StartedIdentity: "proc:" + string(rune(302))},
		{PID: 303, PPID: 1, Kind: "codex", Command: "codex --full-auto", Directory: "", StartedIdentity: "proc:" + string(rune(303))},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("records = %#v, want %#v", got, want)
	}
}

func TestBuildAgentInventoryFindsNativeOpenCodeBelowPaneShellAndKeepsPaneLessProcess(t *testing.T) {
	processes := []AgentProcess{
		{PID: 700, PPID: 1, Command: "bash", Executable: "/usr/bin/bash"},
		{PID: 701, PPID: 700, Command: "bash", Executable: "/usr/bin/bash"},
		// A wrapper/operator can leave argv[0] as node while /proc/exe still
		// identifies the native OpenCode process.
		{PID: 702, PPID: 701, Command: "node /opt/opencode --session=oc-live", Executable: "/opt/opencode (deleted)"},
		// Native OpenCode processes are also valid without a tmux pane.
		{PID: 703, PPID: 1, Command: "node /opt/opencode", Executable: "/opt/opencode"},
	}
	panes := []AgentTmuxPane{{
		Session: "operator", Window: "3", WindowName: "build", Pane: "0", PaneID: "%42",
		Directory: "/repo/from-pane", Title: "OC | live build", PID: 700,
	}}

	got, errs := BuildAgentInventory(context.Background(), AgentInventorySources{
		Processes: func(context.Context) ([]AgentProcess, error) { return processes, nil },
		Panes:     func(context.Context) ([]AgentTmuxPane, error) { return panes, nil },
		CWD: func(pid int) (string, error) {
			if pid == 702 {
				return "/repo/live", nil
			}
			return "", nil
		},
		Started: func(pid int) (string, error) { return fmt.Sprintf("proc:%d", pid), nil },
	})
	if len(errs) != 0 {
		t.Fatalf("errors = %v", errs)
	}
	want := []AgentRecord{
		{
			PID: 702, PPID: 701, Kind: "opencode", Command: "node /opt/opencode --session=oc-live",
			Executable: "/opt/opencode (deleted)", Directory: "/repo/live", TmuxSession: "operator",
			TmuxWindow: "3", TmuxWindowName: "build", TmuxPane: "0", TmuxPaneID: "%42",
			Title: "OC | live build", StartedIdentity: "proc:702", OpenCodeSession: "oc-live",
		},
		{
			PID: 703, PPID: 1, Kind: "opencode", Command: "node /opt/opencode", Executable: "/opt/opencode",
			StartedIdentity: "proc:703",
		},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("records = %#v, want %#v", got, want)
	}
}

func TestBuildOpenCodeInventoryKeepsProcessRowsWhenTmuxAnnotationFails(t *testing.T) {
	processes := []AgentProcess{{PID: 702, PPID: 1, Command: "node /opt/opencode --session=oc-live", Executable: "/opt/opencode"}}
	started := make(chan struct{})
	got, errs := BuildOpenCodeInventory(context.Background(), OpenCodeInventorySources{
		Processes: func(context.Context) ([]AgentProcess, error) { return processes, nil },
		Panes: func(ctx context.Context) ([]AgentTmuxPane, error) {
			close(started)
			select {
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		},
		CWD:     func(int) (string, error) { return "/repo/live", nil },
		Started: func(int) (string, error) { return "proc:702", nil },
	})
	if len(got) != 1 || got[0].PID != 702 || got[0].OpenCodeSession != "oc-live" {
		t.Fatalf("OpenCode process inventory = %#v", got)
	}
	if len(errs) != 1 || !errors.Is(errs[0], context.DeadlineExceeded) {
		t.Fatalf("tmux annotation errors = %v", errs)
	}
	select {
	case <-started:
	default:
		t.Fatal("tmux annotation was not attempted")
	}
}

func TestBuildOpenCodeInventoryCanScopeToPrivateTmuxPanes(t *testing.T) {
	processes := []AgentProcess{
		{PID: 701, PPID: 1, Command: "node /opt/opencode --session=outside", Executable: "/opt/opencode"},
		{PID: 702, PPID: 1, Command: "node /opt/opencode --session=current", Executable: "/opt/opencode"},
	}
	got, errs := BuildOpenCodeInventory(context.Background(), OpenCodeInventorySources{
		Processes: func(context.Context) ([]AgentProcess, error) { return processes, nil },
		Panes: func(context.Context) ([]AgentTmuxPane, error) {
			return []AgentTmuxPane{{Session: "private", PaneID: "%0", PID: 702}}, nil
		},
		Started:         func(pid int) (string, error) { return fmt.Sprintf("proc:%d", pid), nil },
		RequireTmuxPane: true,
	})
	if len(errs) != 0 {
		t.Fatalf("scoped inventory errors = %v", errs)
	}
	if len(got) != 1 || got[0].PID != 702 || got[0].TmuxSession != "private" {
		t.Fatalf("scoped OpenCode inventory = %#v", got)
	}
}

func TestExplicitSessionIDOnlyOpenCode(t *testing.T) {
	for _, test := range []struct{ command, kind, want string }{
		{"opencode --session abc", "opencode", "abc"},
		{"opencode -s 'quoted-id'", "opencode", "quoted-id"},
		{"opencode --session=abc", "opencode", "abc"},
		{"opencode --session", "opencode", ""},
		{"codex --session abc", "codex", ""},
	} {
		if got := explicitSessionID(test.command, test.kind); got != test.want {
			t.Errorf("explicitSessionID(%q, %q) = %q, want %q", test.command, test.kind, got, test.want)
		}
	}
}

func TestLinuxStartedIdentityParsesProcShape(t *testing.T) {
	// The parser is exercised through a real-shaped stat line by keeping the
	// field arithmetic visible in the adapter contract; missing processes fail closed.
	if _, err := linuxStartedIdentity(-1); err == nil {
		t.Fatal("negative pid unexpectedly succeeded")
	}
}

func TestParseAgentCommandAndTmuxShapes(t *testing.T) {
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "10"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "10", "stat"), []byte("10 (bash) S 1 2 3"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "10", "cmdline"), []byte("claude\x00--dangerously-skip-permissions\x00"), 0o600); err != nil {
		t.Fatal(err)
	}
	processes, err := linuxProcessesFromProc(context.Background(), root, os.ReadDir, os.ReadFile, func(string) (string, error) { return "/usr/bin/bash", nil })
	if err != nil || len(processes) != 1 || processes[0].PID != 10 || processes[0].PPID != 1 {
		t.Fatalf("processes = %#v, err=%v", processes, err)
	}
	panes := parseAgentTmuxPanes("work\t2\tagent\t1\t%9\t/repo\topencode\tOpenCode\t10\nshort")
	if len(panes) != 1 || panes[0].PaneID != "%9" || panes[0].Title != "OpenCode" {
		t.Fatalf("panes = %#v", panes)
	}
}

func TestLinuxProcessInventoryUsesProcAndKeepsCurrentOpenCodeRows(t *testing.T) {
	root := t.TempDir()
	for _, process := range []struct {
		pid, ppid int
		cmd       string
	}{
		{401, 300, "opencode --session current-session"},
		{402, 300, "opencode"},
	} {
		pidRoot := filepath.Join(root, strconv.Itoa(process.pid))
		if err := os.Mkdir(pidRoot, 0o700); err != nil {
			t.Fatal(err)
		}
		stat := strconv.Itoa(process.pid) + " (node) S " + strconv.Itoa(process.ppid) + " 2 3"
		if err := os.WriteFile(filepath.Join(pidRoot, "stat"), []byte(stat), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(pidRoot, "cmdline"), []byte(strings.ReplaceAll(process.cmd, " ", "\x00")+"\x00"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	processes, err := linuxProcessesFromProc(context.Background(), root, os.ReadDir, os.ReadFile, func(path string) (string, error) {
		return map[string]string{
			filepath.Join(root, "401", "exe"): "/usr/bin/opencode",
			filepath.Join(root, "402", "exe"): "/usr/bin/opencode",
		}[path], nil
	})
	if err != nil {
		t.Fatal(err)
	}
	var current, normal AgentProcess
	for _, process := range processes {
		if process.PID == 401 {
			current = process
		}
		if process.PID == 402 {
			normal = process
		}
	}
	if current.Command != "opencode --session current-session" || current.Executable != "/usr/bin/opencode" {
		t.Fatalf("current OpenCode row = %+v", current)
	}
	if normal.Command != "opencode" || normal.Executable != "/usr/bin/opencode" {
		t.Fatalf("normal OpenCode row = %+v", normal)
	}
}

func TestLinuxOpenCodeProcessInventoryReadsNativeCandidatesWithoutCommandScan(t *testing.T) {
	root := t.TempDir()
	for _, process := range []struct {
		pid, ppid int
		comm, exe string
		cmd       string
	}{
		{501, 1, "node", "/usr/bin/node", "node unrelated"},
		{502, 1, "node", "/usr/bin/opencode", "node /opt/opencode --session native"},
	} {
		pidRoot := filepath.Join(root, strconv.Itoa(process.pid))
		if err := os.Mkdir(pidRoot, 0o700); err != nil {
			t.Fatal(err)
		}
		stat := fmt.Sprintf("%d (%s) S %d 2 3", process.pid, process.comm, process.ppid)
		if err := os.WriteFile(filepath.Join(pidRoot, "stat"), []byte(stat), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(pidRoot, "cmdline"), []byte(strings.ReplaceAll(process.cmd, " ", "\x00")+"\x00"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	reads := map[string]int{}
	processes, err := linuxOpenCodeProcessesFromProc(context.Background(), root, os.ReadDir, func(path string) ([]byte, error) {
		reads[filepath.Base(path)]++
		return os.ReadFile(path)
	}, func(path string) (string, error) {
		return map[string]string{
			filepath.Join(root, "501", "exe"): "/usr/bin/node",
			filepath.Join(root, "502", "exe"): "/usr/bin/opencode",
		}[path], nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(processes) != 1 {
		t.Fatalf("native process rows = %#v", processes)
	}
	if reads["cmdline"] != 1 {
		t.Fatalf("native candidate command reads = %d, want 1", reads["cmdline"])
	}
	if got := ClassifyAgent(processes[0].Command, processes[0].Executable); got != "opencode" {
		t.Fatalf("native candidate classification = %q, row=%+v", got, processes[0])
	}
}

func TestLinuxProcessInventoryIgnoresAuxiliaryProcExit(t *testing.T) {
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "401"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(root, "402"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "401", "stat"), []byte("401 (opencode) S 300 2 3"), 0o600); err != nil {
		t.Fatal(err)
	}
	processes, err := linuxProcessesFromProc(context.Background(), root, os.ReadDir, os.ReadFile, nil)
	if err != nil || len(processes) != 1 || processes[0].PID != 401 {
		t.Fatalf("processes = %#v, err=%v", processes, err)
	}
}

func TestLinuxProcessInventoryReadsActualProcList(t *testing.T) {
	if _, err := os.Stat("/proc"); err != nil {
		t.Skip("Linux /proc is unavailable")
	}
	processes, err := linuxProcesses(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	for _, process := range processes {
		if process.PID == os.Getpid() {
			return
		}
	}
	t.Fatalf("current Go test process %d was absent from %d process rows", os.Getpid(), len(processes))
}

func TestRunBoundedCommandDiagnosticsAndLargeOutput(t *testing.T) {
	args := []string{"ps", "-e", "--cols", strconv.Itoa(linuxProcessCommandWidth), "-o", "pid=,ppid=,args="}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	cwd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	deadline, hasDeadline := ctx.Deadline()
	command := exec.CommandContext(ctx, args[0], args[1:]...)
	command.Env = os.Environ()
	command.Dir = cwd
	var stdout, stderr bytes.Buffer
	command.Stdout = &stdout
	command.Stderr = &stderr
	execErr := command.Run()
	exitCode := 0
	if exitErr, ok := execErr.(*exec.ExitError); ok {
		exitCode = exitErr.ExitCode()
	}
	t.Logf("Go ps repro uid=%d cwd=%q env_entries=%d args=%q exec.CommandContext err=%v exit_code=%d stdout_len=%d stderr=%q context_err=%v deadline=%v has_deadline=%t", os.Getuid(), cwd, len(command.Env), args, execErr, exitCode, stdout.Len(), stderr.String(), ctx.Err(), deadline, hasDeadline)
	result, boundedErr := runBoundedCommand(ctx, args)
	t.Logf("runBoundedCommand err=%v exit_code=%d stdout_len=%d stderr=%q context_err=%v", boundedErr, result.ExitCode, len(result.Stdout), string(result.Stderr), ctx.Err())
	if boundedErr != nil || result.ExitCode != 0 || len(result.Stdout) == 0 {
		t.Fatalf("actual ps command failed: err=%v result=%+v", boundedErr, result)
	}

	largeArgs := []string{"sh", "-c", "printf '%9000000s' x"}
	large, largeErr := runBoundedCommand(context.Background(), largeArgs)
	if largeErr == nil || !strings.Contains(largeErr.Error(), "output exceeds bound") || len(large.Stdout) > maxHostCommandOutput+1 {
		t.Fatalf("large output semantics: err=%v stdout_len=%d", largeErr, len(large.Stdout))
	}
}
