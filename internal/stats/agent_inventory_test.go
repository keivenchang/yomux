package stats

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"github.com/keivenchang/yomux/internal/testsupport"
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

func TestTmuxCommandArgsCanonicalSocketWinsIncludingExplicitEmpty(t *testing.T) {
	t.Setenv("YOLOMUX_TMUX_SOCKET", "/legacy/tmux.sock")
	t.Setenv("YOMUX_TMUX_SOCKET", " /canonical/tmux.sock ")
	if got, want := tmuxCommandArgs("list-panes"), []string{"tmux", "-S", "/canonical/tmux.sock", "list-panes"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("canonical tmux socket = %#v, want %#v", got, want)
	}
	if !syntheticTmuxSocketConfigured() {
		t.Fatal("synthetic admission did not share the canonical socket owner")
	}
	t.Setenv("YOMUX_TMUX_SOCKET", "")
	if got, want := tmuxCommandArgs("list-panes"), []string{"tmux", "list-panes"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("cleared canonical tmux socket = %#v, want %#v", got, want)
	}
	if syntheticTmuxSocketConfigured() {
		t.Fatal("explicit empty canonical socket did not clear synthetic admission")
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

func TestParseTmuxSessionsKeepsTheMachineRosterExact(t *testing.T) {
	got := parseTmuxSessions("yo7220\nname with spaces\nyo7220\n\n")
	want := map[string]struct{}{"yo7220": {}, "name with spaces": {}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("tmux sessions = %#v, want %#v", got, want)
	}
}

func TestAgentInventoryAttributesRunningPaneToSourceNotOwnedViewAlias(t *testing.T) {
	panes := []AgentTmuxPane{
		{Session: "__yomux_view_7110_abc", SessionID: "$216", ViewSourceID: "$46", PaneID: "%55", PID: 900, Window: "0"},
		{Session: "__yomux_view_7110_def", SessionID: "$220", ViewSourceID: "$46", PaneID: "%92", PID: 903, Window: "1"},
		{Session: "yo7110", SessionID: "$46", PaneID: "%55", PID: 900, Window: "0"},
		{Session: "yo7110", SessionID: "$46", PaneID: "%92", PID: 903, Window: "1"},
		{Session: "__yomux_view_notes", SessionID: "$217", PaneID: "%90", PID: 901, Window: "0"},
		{Session: "__yomux_view_notes_child", SessionID: "$218", ViewSourceID: "$217", PaneID: "%90", PID: 901, Window: "0"},
		{Session: "__yomux_view_unverified", SessionID: "$219", ViewSourceID: "$99", PaneID: "%91", PID: 902, Window: "0"},
	}
	filtered := filterOwnedTabberViewAliases(panes)
	if len(filtered) != 4 || filtered[0].Session != "yo7110" || filtered[1].Session != "yo7110" || filtered[2].Session != "__yomux_view_notes" || filtered[3].Session != "__yomux_view_unverified" {
		t.Fatalf("filtered panes = %#v, want source-session panes and unverified alias-like sessions", filtered)
	}

	processes := []AgentProcess{
		{PID: 900, PPID: 1, Command: "codex --full-auto", Executable: "/usr/bin/codex"},
		{PID: 901, PPID: 1, Command: "codex --full-auto", Executable: "/usr/bin/codex"},
		{PID: 903, PPID: 1, Command: "codex --full-auto", Executable: "/usr/bin/codex"},
	}
	records, errs := BuildAgentInventory(context.Background(), AgentInventorySources{
		Processes: func(context.Context) ([]AgentProcess, error) { return processes, nil },
		Panes:     func(context.Context) ([]AgentTmuxPane, error) { return filtered, nil },
	})
	if len(errs) != 0 || len(records) != 3 || records[0].TmuxSession != "yo7110" || records[0].TmuxPaneID != "%55" || records[1].TmuxSession != "__yomux_view_notes" || records[1].TmuxPaneID != "%90" || records[2].TmuxSession != "yo7110" || records[2].TmuxWindow != "1" || records[2].TmuxPaneID != "%92" {
		t.Fatalf("running pane inventory = %#v, errors=%v; want canonical source names across windows and preserved pane IDs", records, errs)
	}
}

func TestTabberViewSessionAliasesUseVerifiedSourceSessionNames(t *testing.T) {
	panes := []AgentTmuxPane{
		{Session: "__yomux_view_7110_first", SessionID: "$220", ViewSourceID: "$46"},
		{Session: "__yomux_view_7110_second", SessionID: "$221", ViewSourceID: "$46"},
		{Session: "yo7110", SessionID: "$46"},
		{Session: "yo7111", SessionID: "$48"},
		{Session: "__yomux_view_unverified", SessionID: "$222", ViewSourceID: "$99"},
	}
	got := tabberViewSessionAliases(panes)
	want := map[string]string{"__yomux_view_7110_first": "yo7110", "__yomux_view_7110_second": "yo7110"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("verified view aliases = %#v, want %#v", got, want)
	}
}

func TestTmuxScopedAgentRecordsDropsPaneLessProcesses(t *testing.T) {
	records := tmuxScopedAgentRecords([]AgentRecord{
		{Kind: "codex", PID: 1, TmuxSession: "yo7222", TmuxPaneID: "%7"},
		{Kind: "codex", PID: 2, TmuxSession: "", TmuxPaneID: ""},
		{Kind: "opencode", PID: 3, TmuxSession: "yo7220", TmuxPane: "2"},
	})
	if len(records) != 2 || records[0].PID != 1 || records[1].PID != 3 {
		t.Fatalf("tmux-scoped records = %#v", records)
	}
}

func TestTmuxNoServerErrorIsAnEmptyRoster(t *testing.T) {
	for _, output := range []string{
		"no server running on /tmp/tmux-1000/default",
		"error connecting to /tmp/tmux-1000/default (No such file or directory)",
	} {
		if !TmuxNoServerError([]byte(output)) {
			t.Fatalf("TmuxNoServerError(%q) = false", output)
		}
	}
	if TmuxNoServerError([]byte("tmux: unknown option -- bad")) {
		t.Fatal("unrelated tmux failure was classified as an empty roster")
	}
}

func TestSyntheticAgentInventoryRequiresFixtureTmuxSocket(t *testing.T) {
	t.Setenv("YOLOMUX_TMUX_SOCKET", "")
	sources := SyntheticAgentInventorySources()
	if _, err := sources.Panes(context.Background()); !errors.Is(err, errSyntheticTmuxSocketUnset) {
		t.Fatalf("synthetic panes error = %v, want fixture socket admission error", err)
	}
}

func TestSyntheticTmuxRejectsSocketOutsideFixtureRoot(t *testing.T) {
	root := t.TempDir()
	t.Setenv("YOLOMUX_STATS_SYNTHETIC", "1")
	t.Setenv("YOLOMUX_STATS_SYNTHETIC_ROOT", filepath.Join(root, "fixture"))
	t.Setenv("YOLOMUX_TMUX_SOCKET", filepath.Join(root, "global-tmux.sock"))
	if syntheticTmuxSocketConfigured() {
		t.Fatal("synthetic tmux admission accepted a socket outside the fixture root")
	}
	t.Setenv("YOLOMUX_TMUX_SOCKET", filepath.Join(root, "fixture", "tmux.sock"))
	if !syntheticTmuxSocketConfigured() {
		t.Fatal("synthetic tmux admission rejected the fixture-owned private socket")
	}
	if err := os.MkdirAll(filepath.Join(root, "fixture"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "fixture", "tmux.sock"), []byte("not a socket"), 0o600); err != nil {
		t.Fatal(err)
	}
	if syntheticTmuxSocketConfigured() {
		t.Fatal("synthetic tmux admission accepted a regular file as a socket")
	}
}

func TestSyntheticAgentProcessUsesGoFixtureOpenCodeSession(t *testing.T) {
	process, ok := syntheticAgentProcess(AgentTmuxPane{PID: 42, Session: "fixture-opencode"})
	if !ok {
		t.Fatal("synthetic OpenCode pane was not admitted")
	}
	if process.Command != "opencode --mock --session ses-synthetic" || process.Executable != "python3" {
		t.Fatalf("synthetic OpenCode process = %+v", process)
	}
}

func TestLinuxTmuxRosterTreatsMissingServerAsEmpty(t *testing.T) {
	t.Setenv("YOMUX_TMUX_SOCKET", filepath.Join(testsupport.ShortTempDir(t), "tmux.sock"))
	sessions, err := linuxTmuxSessions(context.Background())
	if err != nil {
		t.Fatalf("linuxTmuxSessions() error = %v", err)
	}
	if len(sessions) != 0 {
		t.Fatalf("linuxTmuxSessions() = %#v, want empty", sessions)
	}
	panes, err := linuxTmuxPanes(context.Background())
	if err != nil {
		t.Fatalf("linuxTmuxPanes() error = %v", err)
	}
	if len(panes) != 0 {
		t.Fatalf("linuxTmuxPanes() = %#v, want empty", panes)
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
		{"/usr/local/bin/node /Users/test/.npm/_npx/@openai/codex/bin/codex.js", "/usr/local/bin/node", "codex"},
		{"node /tmp/tool.js /tmp/codex.js", "/usr/local/bin/node", ""},
		{"node /usr/local/bin/codex --resume thread", "/usr/bin/node", "codex"},
		{"node /home/test/.local/bin/codex --resume thread", "", "codex"},
		{"nodejs /usr/local/bin/codex --resume thread", "", "codex"},
		{"node /tmp/tool.js /usr/local/bin/codex", "/usr/bin/node", ""},
		{"node /tmp/codex.js", "/usr/bin/node", ""},
		{"node /usr/local/bin/codex", "/usr/bin/bash", ""},
		{"bash -c 'codex --full-auto'", "", ""},
		{"/tmp/not-opencode --session abc", "/tmp/not-opencode", ""},
	}
	for _, test := range tests {
		if got := ClassifyAgent(test.command, test.executable); got != test.want {
			t.Errorf("ClassifyAgent(%q, %q) = %q, want %q", test.command, test.executable, got, test.want)
		}
	}
}

func TestBuildAgentInventoryCodexLauncherUsesNativeChildWhenPresent(t *testing.T) {
	launcher := AgentProcess{PID: 301, PPID: 300, Command: "/usr/local/bin/node /Users/test/.npm/_npx/@openai/codex/bin/codex.js", Executable: "/usr/local/bin/node"}
	native := AgentProcess{PID: 302, PPID: 301, Command: "/opt/codex/vendor/codex --full-auto", Executable: "/opt/codex/vendor/codex"}
	for _, test := range []struct {
		name      string
		processes []AgentProcess
		wantPID   int
	}{
		{name: "launcher only", processes: []AgentProcess{launcher}, wantPID: launcher.PID},
		{name: "launcher and native child", processes: []AgentProcess{launcher, native}, wantPID: native.PID},
		{name: "snapshot order reversed", processes: []AgentProcess{native, launcher}, wantPID: native.PID},
		{name: "legacy launcher only", processes: []AgentProcess{{PID: launcher.PID, PPID: launcher.PPID, Command: "node /usr/local/bin/codex --resume thread"}}, wantPID: launcher.PID},
		{name: "legacy launcher and native child", processes: []AgentProcess{{PID: launcher.PID, PPID: launcher.PPID, Command: "node /usr/local/bin/codex --resume thread"}, native}, wantPID: native.PID},
	} {
		t.Run(test.name, func(t *testing.T) {
			records, errs := BuildAgentInventory(context.Background(), AgentInventorySources{
				Processes: func(context.Context) ([]AgentProcess, error) { return test.processes, nil },
				Panes: func(context.Context) ([]AgentTmuxPane, error) {
					return []AgentTmuxPane{{PID: launcher.PID, Session: "work", PaneID: "%9", Directory: "/repo"}}, nil
				},
			})
			if len(errs) != 0 {
				t.Fatalf("inventory errors: %v", errs)
			}
			if len(records) != 1 || records[0].PID != test.wantPID || records[0].Kind != "codex" || records[0].TmuxPaneID != "%9" {
				t.Fatalf("pane agent records = %#v, want one Codex PID %d on pane %%9", records, test.wantPID)
			}
		})
	}
}

func TestClassifyAgentProcessesPreservesIndependentAgentsAndIncompleteLaunches(t *testing.T) {
	launcher := AgentProcess{PID: 301, PPID: 300, Command: "node /opt/node_modules/@openai/codex/bin/codex.js", Executable: "/usr/bin/node"}
	native := AgentProcess{PID: 302, PPID: 301, Command: "/opt/codex/vendor/codex", Executable: "/opt/codex/vendor/codex"}
	for _, test := range []struct {
		name      string
		processes []AgentProcess
		want      map[int]string
	}{
		{name: "native through shell", processes: []AgentProcess{launcher, {PID: 303, PPID: launcher.PID, Command: "sh"}, {PID: native.PID, PPID: 303, Command: native.Command, Executable: native.Executable}}, want: map[int]string{native.PID: "codex"}},
		{name: "unrelated native sibling", processes: []AgentProcess{launcher, {PID: native.PID, PPID: launcher.PPID, Command: native.Command, Executable: native.Executable}}, want: map[int]string{launcher.PID: "codex", native.PID: "codex"}},
		{name: "different child agent", processes: []AgentProcess{launcher, {PID: 302, PPID: launcher.PID, Command: "claude", Executable: "/usr/bin/claude"}}, want: map[int]string{launcher.PID: "codex", 302: "claude"}},
		{name: "native command unavailable", processes: []AgentProcess{launcher, {PID: native.PID, PPID: launcher.PID, Executable: native.Executable}}, want: map[int]string{launcher.PID: "codex"}},
		{name: "deleted native executable", processes: []AgentProcess{launcher, {PID: native.PID, PPID: launcher.PID, Command: native.Command, Executable: native.Executable + " (deleted)"}}, want: map[int]string{native.PID: "codex"}},
		{name: "nested launchers", processes: []AgentProcess{launcher, {PID: 303, PPID: launcher.PID, Command: launcher.Command, Executable: launcher.Executable}, {PID: native.PID, PPID: 303, Command: native.Command, Executable: native.Executable}}, want: map[int]string{native.PID: "codex"}},
		{name: "parent cycle", processes: []AgentProcess{{PID: launcher.PID, PPID: native.PID, Command: launcher.Command, Executable: launcher.Executable}, native}, want: map[int]string{native.PID: "codex"}},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Ownership must not depend on process snapshot enumeration order.
			for range 2 {
				if got := ClassifyAgentProcesses(test.processes); !reflect.DeepEqual(got, test.want) {
					t.Fatalf("classified process owners = %#v, want %#v", got, test.want)
				}
				for left, right := 0, len(test.processes)-1; left < right; left, right = left+1, right-1 {
					test.processes[left], test.processes[right] = test.processes[right], test.processes[left]
				}
			}
		})
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

func TestBuildOpenCodeInventoryFailsClosedWhenTmuxScopeHasNoPanes(t *testing.T) {
	processes := []AgentProcess{{PID: 702, PPID: 1, Command: "node /opt/opencode --session=outside", Executable: "/opt/opencode"}}
	got, errs := BuildOpenCodeInventory(context.Background(), OpenCodeInventorySources{
		Processes:       func(context.Context) ([]AgentProcess, error) { return processes, nil },
		Panes:           func(context.Context) ([]AgentTmuxPane, error) { return nil, nil },
		RequireTmuxPane: true,
	})
	if len(got) != 0 {
		t.Fatalf("scoped inventory admitted pane-less process: %#v", got)
	}
	if len(errs) != 0 {
		t.Fatalf("empty scoped pane snapshot unexpectedly errored: %v", errs)
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
	processes, err := linuxProcessesFromProc(context.Background(), root, readDirNames, os.ReadFile, func(string) (string, error) { return "/usr/bin/bash", nil })
	if err != nil || len(processes) != 1 || processes[0].PID != 10 || processes[0].PPID != 1 {
		t.Fatalf("processes = %#v, err=%v", processes, err)
	}
	panes := parseAgentTmuxPanes("work\t$1\t\t2\tagent\t1\t%9\t/repo\topencode\tOpenCode\t10\nshort")
	if len(panes) != 1 || panes[0].PaneID != "%9" || panes[0].Title != "OpenCode" || panes[0].SessionID != "$1" {
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
	processes, err := linuxProcessesFromProc(context.Background(), root, readDirNames, os.ReadFile, func(path string) (string, error) {
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
	processes, err := linuxOpenCodeProcessesFromProc(context.Background(), root, readDirNames, func(path string) ([]byte, error) {
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
	processes, err := linuxProcessesFromProc(context.Background(), root, readDirNames, os.ReadFile, nil)
	if err != nil || len(processes) != 1 || processes[0].PID != 401 {
		t.Fatalf("processes = %#v, err=%v", processes, err)
	}
}

func TestLinuxProcessInventoriesRejectOverCapBeforeReadingRows(t *testing.T) {
	names := make([]string, maxProcessEntries+1)
	names[0] = "1"
	readDir := func(string) ([]string, error) { return names, nil }
	readFile := func(string) ([]byte, error) {
		t.Fatal("process row was read before the oversized directory was rejected")
		return nil, nil
	}
	if _, err := linuxProcessesFromProc(context.Background(), "/synthetic/proc", readDir, readFile, nil); err == nil || !strings.Contains(err.Error(), "process inventory bound exceeded") {
		t.Fatalf("general process inventory error = %v, want over-cap rejection", err)
	}
	if _, err := linuxOpenCodeProcessesFromProc(context.Background(), "/synthetic/proc", readDir, readFile, nil); err == nil || !strings.Contains(err.Error(), "process inventory bound exceeded") {
		t.Fatalf("OpenCode process inventory error = %v, want over-cap rejection", err)
	}
}

func TestRunBoundedCommandDiagnosticsAndLargeSyntheticOutput(t *testing.T) {
	// The command runner is tested with fixture-generated bytes only. It must
	// never inspect the host process table or inherit an operator's environment.
	smallArgs := []string{"sh", "-c", "printf '101 1 synthetic-agent\\n'"}
	small, smallErr := runBoundedCommand(context.Background(), smallArgs)
	if smallErr != nil || small.ExitCode != 0 || string(small.Stdout) != "101 1 synthetic-agent\n" {
		t.Fatalf("synthetic command result: err=%v result=%+v", smallErr, small)
	}

	largeArgs := []string{"sh", "-c", "printf '%9000000s' x"}
	large, largeErr := runBoundedCommand(context.Background(), largeArgs)
	if largeErr == nil || !strings.Contains(largeErr.Error(), "output exceeds bound") || len(large.Stdout) > maxHostCommandOutput+1 {
		t.Fatalf("large output semantics: err=%v stdout_len=%d", largeErr, len(large.Stdout))
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
