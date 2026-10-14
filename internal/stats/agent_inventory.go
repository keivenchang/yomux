package stats

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/keivenchang/yomux/internal/productenv"
)

const (
	linuxProcessCommandWidth      = 256
	opencodeTmuxAnnotationTimeout = 100 * time.Millisecond
	syntheticOpenCodeSession      = "ses-synthetic"
)

// TabberViewSessionPrefix marks the reserved-looking name prefix used for internal window-view aliases.
const TabberViewSessionPrefix = "__yomux_view_"

// AgentProcess and AgentTmuxPane are deliberately small snapshots. Keeping the
// OS-shaped inputs separate from AgentRecord makes the inventory testable and
// prevents a transient process exit from becoming a partially populated row.
type AgentProcess struct {
	PID        int
	PPID       int
	Command    string
	Executable string
}

type AgentTmuxPane struct {
	Session      string
	SessionID    string
	ViewSourceID string
	Window       string
	WindowName   string
	Pane         string
	PaneID       string
	Directory    string
	Command      string
	Title        string
	PID          int
}

type AgentRecord struct {
	PID             int
	PPID            int
	Kind            string
	Command         string
	Executable      string
	Directory       string
	TmuxSession     string
	TmuxWindow      string
	TmuxWindowName  string
	TmuxPane        string
	TmuxPaneID      string
	Title           string
	StartedIdentity string
	StartedAt       float64
	OpenCodeSession string
	// TranscriptPath is an inventory-derived exact path when the runtime has
	// already resolved the agent's transcript. It avoids searching a host-wide
	// transcript root for a path the inventory already knows.
	TranscriptPath string
}

type AgentInventorySources struct {
	Processes func(context.Context) ([]AgentProcess, error)
	Panes     func(context.Context) ([]AgentTmuxPane, error)
	CWD       func(int) (string, error)
	Started   func(int) (string, error)
	StartTime func(int) (float64, error)
}

// OpenCodeInventorySources is the narrow inventory boundary used by token
// collection. Processes are discovered from /proc first; production YO!stats
// requires a matching pane in the machine-wide tmux roster, while synthetic
// tests may leave that scope disabled when they are testing native discovery.
type OpenCodeInventorySources struct {
	Processes func(context.Context) ([]AgentProcess, error)
	Panes     func(context.Context) ([]AgentTmuxPane, error)
	CWD       func(int) (string, error)
	Started   func(int) (string, error)
	StartTime func(int) (float64, error)
	// RequireTmuxPane scopes a test/managed instance to processes visible from
	// its private tmux server. The normal operator inventory remains
	// machine-wide so pane-less native OpenCode processes are still collected.
	RequireTmuxPane bool
}

// BuildAgentInventory creates stable records from process and tmux snapshots.
// A pane's process and all descendants inherit its context; the nearest pane
// in the supplied deterministic order wins when panes overlap.
func BuildAgentInventory(ctx context.Context, sources AgentInventorySources) ([]AgentRecord, []error) {
	processes, processErr := sources.Processes(ctx)
	panes, paneErr := sources.Panes(ctx)
	errorsOut := []error{}
	if processErr != nil {
		errorsOut = append(errorsOut, processErr)
	}
	if paneErr != nil {
		errorsOut = append(errorsOut, paneErr)
	}
	if processErr != nil {
		return nil, errorsOut
	}

	processes = deduplicateProcesses(processes)
	children := make(map[int][]AgentProcess)
	for _, process := range processes {
		children[process.PPID] = append(children[process.PPID], process)
	}
	for pid := range children {
		sort.Slice(children[pid], func(i, j int) bool { return children[pid][i].PID < children[pid][j].PID })
	}

	paneByPID := make(map[int]AgentTmuxPane)
	for _, pane := range panes {
		for _, process := range append([]AgentProcess{{PID: pane.PID}}, descendants(pane.PID, children)...) {
			if _, exists := paneByPID[process.PID]; !exists {
				paneByPID[process.PID] = pane
			}
		}
	}

	kindsByPID := ClassifyAgentProcesses(processes)
	records := make([]AgentRecord, 0)
	seen := make(map[string]bool)
	for _, process := range processes {
		kind := kindsByPID[process.PID]
		if kind == "" {
			continue
		}
		pane := paneByPID[process.PID]
		directory := pane.Directory
		if sources.CWD != nil {
			if cwd, err := sources.CWD(process.PID); err == nil && cwd != "" {
				directory = cwd
			}
		}
		started := ""
		if sources.Started != nil {
			if identity, err := sources.Started(process.PID); err == nil {
				started = identity
			}
		}
		startedAt := float64(0)
		if sources.StartTime != nil {
			if value, err := sources.StartTime(process.PID); err == nil {
				startedAt = value
			}
		}
		key := fmt.Sprintf("%d\x1f%s", process.PID, started)
		if seen[key] {
			continue
		}
		seen[key] = true
		records = append(records, AgentRecord{
			PID: process.PID, PPID: process.PPID, Kind: kind, Command: process.Command, Executable: process.Executable,
			Directory: directory, TmuxSession: pane.Session, TmuxWindow: pane.Window,
			TmuxWindowName: pane.WindowName, TmuxPane: pane.Pane, TmuxPaneID: pane.PaneID,
			Title: pane.Title, StartedIdentity: started,
			StartedAt:       startedAt,
			OpenCodeSession: explicitSessionID(process.Command, kind),
		})
	}
	sort.Slice(records, func(i, j int) bool {
		if records[i].PID != records[j].PID {
			return records[i].PID < records[j].PID
		}
		return records[i].StartedIdentity < records[j].StartedIdentity
	})
	return records, errorsOut
}

// BuildOpenCodeInventory keeps native OpenCode discovery independent from the
// full Claude/Codex inventory. A process snapshot can be useful even when a
// concurrent /proc exit or best-effort tmux annotation reports an error.
func BuildOpenCodeInventory(ctx context.Context, sources OpenCodeInventorySources) ([]AgentRecord, []error) {
	if sources.Processes == nil {
		return nil, []error{errors.New("process inventory unavailable")}
	}
	processes, processErr := sources.Processes(ctx)
	errorsOut := []error{}
	if processErr != nil {
		errorsOut = append(errorsOut, processErr)
	}
	processes = deduplicateProcesses(processes)
	children := make(map[int][]AgentProcess)
	for _, process := range processes {
		children[process.PPID] = append(children[process.PPID], process)
	}
	for pid := range children {
		sort.Slice(children[pid], func(i, j int) bool { return children[pid][i].PID < children[pid][j].PID })
	}

	paneByPID := make(map[int]AgentTmuxPane)
	if sources.Panes != nil && len(processes) > 0 {
		// Pane metadata improves attribution, but a stuck or unavailable tmux
		// server must not hide a machine-wide native OpenCode process. Give this
		// enrichment its own very small budget and return process rows first.
		paneCtx, cancel := context.WithTimeout(ctx, opencodeTmuxAnnotationTimeout)
		panes, paneErr := sources.Panes(paneCtx)
		if paneErr != nil {
			errorsOut = append(errorsOut, paneErr)
		} else {
			for _, pane := range panes {
				for _, process := range append([]AgentProcess{{PID: pane.PID}}, descendants(pane.PID, children)...) {
					if _, exists := paneByPID[process.PID]; !exists {
						paneByPID[process.PID] = pane
					}
				}
			}
		}
		cancel()
	}
	if sources.RequireTmuxPane {
		scoped := make([]AgentProcess, 0, len(processes))
		for _, process := range processes {
			if _, ok := paneByPID[process.PID]; ok {
				scoped = append(scoped, process)
			}
		}
		processes = scoped
	}

	records := make([]AgentRecord, 0)
	seen := make(map[string]bool)
	for _, process := range processes {
		if ClassifyAgent(process.Command, process.Executable) != "opencode" {
			continue
		}
		pane := paneByPID[process.PID]
		directory := pane.Directory
		if sources.CWD != nil {
			if cwd, err := sources.CWD(process.PID); err == nil && cwd != "" {
				directory = cwd
			}
		}
		started := ""
		if sources.Started != nil {
			if identity, err := sources.Started(process.PID); err == nil {
				started = identity
			}
		}
		startedAt := float64(0)
		if sources.StartTime != nil {
			if value, err := sources.StartTime(process.PID); err == nil {
				startedAt = value
			}
		}
		key := fmt.Sprintf("%d\x1f%s", process.PID, started)
		if seen[key] {
			continue
		}
		seen[key] = true
		records = append(records, AgentRecord{
			PID: process.PID, PPID: process.PPID, Kind: "opencode", Command: process.Command, Executable: process.Executable,
			Directory: directory, TmuxSession: pane.Session, TmuxWindow: pane.Window,
			TmuxWindowName: pane.WindowName, TmuxPane: pane.Pane, TmuxPaneID: pane.PaneID,
			Title: pane.Title, StartedIdentity: started, StartedAt: startedAt,
			OpenCodeSession: explicitSessionID(process.Command, "opencode"),
		})
	}
	sort.Slice(records, func(i, j int) bool {
		if records[i].PID != records[j].PID {
			return records[i].PID < records[j].PID
		}
		return records[i].StartedIdentity < records[j].StartedIdentity
	})
	return records, errorsOut
}

func deduplicateProcesses(processes []AgentProcess) []AgentProcess {
	byPID := make(map[int]AgentProcess, len(processes))
	for _, process := range processes {
		if process.PID <= 0 {
			continue
		}
		if current, ok := byPID[process.PID]; !ok || process.Command < current.Command {
			byPID[process.PID] = process
		}
	}
	result := make([]AgentProcess, 0, len(byPID))
	for _, process := range byPID {
		result = append(result, process)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].PID < result[j].PID })
	return result
}

func descendants(root int, children map[int][]AgentProcess) []AgentProcess {
	result := []AgentProcess{}
	queue := append([]AgentProcess(nil), children[root]...)
	// Mark the root before walking so malformed or transient /proc snapshots
	// cannot turn a parent cycle into a second copy of the pane process.
	seen := map[int]bool{root: true}
	for len(queue) > 0 {
		process := queue[0]
		queue = queue[1:]
		if seen[process.PID] {
			continue
		}
		seen[process.PID] = true
		result = append(result, process)
		queue = append(queue, children[process.PID]...)
	}
	return result
}

// tmuxScopedAgentRecords is the shared product boundary for activity and token
// attribution. A process that is not attached to a pane in the machine-wide
// tmux roster is not an agent YO!stats should poll; admitting it would also
// make transcript discovery walk unrelated historical roots.
func tmuxScopedAgentRecords(records []AgentRecord) []AgentRecord {
	result := make([]AgentRecord, 0, len(records))
	for _, record := range records {
		if tmuxPaneTarget(record) != "" {
			result = append(result, record)
		}
	}
	return result
}

// ClassifyAgentProcesses selects the process that owns each agent instance.
// Codex's Node launcher stays discoverable until its native descendant appears;
// retaining both would count one pane twice and poll the launcher's empty rollout.
func ClassifyAgentProcesses(processes []AgentProcess) map[int]string {
	byPID := make(map[int]AgentProcess, len(processes))
	for _, process := range processes {
		byPID[process.PID] = process
	}
	kinds := make(map[int]string)
	for pid, process := range byPID {
		if kind := ClassifyAgent(process.Command, process.Executable); kind != "" {
			kinds[pid] = kind
		}
	}
	for _, process := range byPID {
		if kinds[process.PID] != "codex" || !codexNativeExecutable(process.Executable) {
			continue
		}
		seen := map[int]bool{process.PID: true}
		for parentPID := process.PPID; parentPID > 0 && !seen[parentPID]; {
			seen[parentPID] = true
			parent, ok := byPID[parentPID]
			if !ok {
				break
			}
			if codexNodeEntryPoint(shellWords(parent.Command), parent.Executable) {
				delete(kinds, parentPID)
			}
			parentPID = parent.PPID
		}
	}
	return kinds
}

// ClassifyAgent only accepts an executable entry point, never an argument that
// happens to contain an agent name. Native OpenCode is identified by its
// executable path even when a shell/operator wrapper leaves a different argv[0].
// Its child tools cannot satisfy that executable identity.
func ClassifyAgent(command, executable string) string {
	tokens := shellWords(command)
	if len(tokens) == 0 {
		return ""
	}
	argvName := strings.ToLower(filepath.Base(strings.Trim(tokens[0], "\"'")))
	if argvName == "claude" || argvName == "codex" {
		return argvName
	}
	if strings.EqualFold(filepath.Base(strings.TrimSuffix(executable, " (deleted)")), "opencode") {
		return "opencode"
	}
	// The native executable identifies Codex even when argv[0] is rewritten.
	if codexNativeExecutable(executable) {
		return "codex"
	}
	if codexNodeEntryPoint(tokens, executable) {
		return "codex"
	}
	// The repository's deterministic mock entry point uses exec -a opencode
	// with a Python interpreter. Keep that explicitly marked test process
	// discoverable without treating arbitrary wrappers as native OpenCode.
	if argvName == "opencode" && strings.Contains(command, "--mock") &&
		(strings.EqualFold(filepath.Base(executable), "python") || strings.HasPrefix(strings.ToLower(filepath.Base(executable)), "python3")) {
		return "opencode"
	}
	return ""
}

// agentProcessMatchesKind validates a process against an already admitted
// agent record. The inventory classifier intentionally rejects generic Node
// wrappers, but a pane-scoped Codex record may transiently point at Codex's
// Node launcher while its native child is between process-tree observations.
func agentProcessMatchesKind(kind, command, executable string) bool {
	if ClassifyAgent(command, executable) == kind {
		return true
	}
	return kind == "codex" && codexNodeEntryPoint(shellWords(command), executable)
}

func codexNativeExecutable(executable string) bool {
	return strings.EqualFold(filepath.Base(strings.TrimSuffix(executable, " (deleted)")), "codex")
}

func codexNodeEntryPoint(tokens []string, executable string) bool {
	if len(tokens) < 2 {
		return false
	}
	executableBase := strings.ToLower(filepath.Base(strings.TrimSuffix(executable, " (deleted)")))
	if executable == "" {
		// Some tmux process observations retain argv without an executable path.
		executableBase = strings.ToLower(filepath.Base(strings.Trim(tokens[0], "\"'")))
	}
	if executableBase != "node" && executableBase != "nodejs" {
		return false
	}
	entryPoint := strings.ToLower(strings.Trim(tokens[1], "\"'"))
	return filepath.Base(entryPoint) == "codex" || strings.Contains(entryPoint, "/@openai/codex/") && strings.HasSuffix(entryPoint, "/codex.js")
}

func explicitSessionID(command, kind string) string {
	if kind != "opencode" {
		return ""
	}
	tokens := shellWords(command)
	for i, token := range tokens {
		if (token == "--session" || token == "-s") && i+1 < len(tokens) {
			return cleanSessionID(tokens[i+1])
		}
		if strings.HasPrefix(token, "--session=") {
			return cleanSessionID(strings.TrimPrefix(token, "--session="))
		}
	}
	return ""
}

func cleanSessionID(value string) string {
	value = strings.Trim(value, "\"'")
	if value == "" || len(value) > 256 || strings.IndexFunc(value, func(r rune) bool { return r < 0x20 || r == 0x7f }) >= 0 {
		return ""
	}
	return value
}

func shellWords(command string) []string { return strings.Fields(command) }

// LinuxAgentInventorySources returns injectable adapters backed by bounded /proc,
// tmux, and /proc reads. Agent discovery does not depend on a shell command or
// on the size and lifetime of a host-wide ps snapshot.
func LinuxAgentInventorySources() AgentInventorySources {
	return AgentInventorySources{Processes: linuxProcesses, Panes: linuxTmuxPanes, CWD: linuxCWD, Started: linuxStartedIdentity, StartTime: linuxProcessStartedAt}
}

// CurrentPlatformAgentInventorySources shares one bounded native process snapshot
// across status, transcript attribution, and host metrics on supported hosts.
func CurrentPlatformAgentInventorySources() AgentInventorySources {
	if runtime.GOOS != "darwin" {
		return LinuxAgentInventorySources()
	}
	return AgentInventorySources{
		Processes: func(ctx context.Context) ([]AgentProcess, error) {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
			rows, err := DarwinProcessInventory()
			if err != nil {
				return nil, err
			}
			result := make([]AgentProcess, 0, len(rows))
			for _, row := range rows {
				result = append(result, AgentProcess{PID: row.PID, PPID: row.PPID, Command: row.Command, Executable: row.Executable})
			}
			return result, nil
		},
		Panes: linuxTmuxPanes,
		CWD: func(pid int) (string, error) {
			row, ok := darwinProcessIdentity(pid)
			if !ok || row.Directory == "" {
				return "", errors.New("process cwd unavailable")
			}
			return row.Directory, nil
		},
		Started: func(pid int) (string, error) {
			row, ok := darwinProcessIdentity(pid)
			if !ok {
				return "", errors.New("process start unavailable")
			}
			return row.Identity, nil
		},
		StartTime: func(pid int) (float64, error) {
			row, ok := darwinProcessIdentity(pid)
			if !ok || row.StartedAt <= 0 {
				return 0, errors.New("process start unavailable")
			}
			return row.StartedAt, nil
		},
	}
}

// SyntheticAgentInventorySources keeps development and integration runs on
// the fixture-owned tmux server. It derives agent identities from pane names
// and never scans the host process table or /proc; the pane PID is only an
// opaque fixture identity used to join the two synthetic snapshots.
func SyntheticAgentInventorySources() AgentInventorySources {
	panes := func(ctx context.Context) ([]AgentTmuxPane, error) {
		if !syntheticTmuxSocketConfigured() {
			return nil, errSyntheticTmuxSocketUnset
		}
		return linuxTmuxPanes(ctx)
	}
	return AgentInventorySources{
		Processes: func(ctx context.Context) ([]AgentProcess, error) {
			rows, err := panes(ctx)
			if err != nil {
				return nil, err
			}
			processes := make([]AgentProcess, 0, len(rows))
			for _, pane := range rows {
				if process, ok := syntheticAgentProcess(pane); ok {
					processes = append(processes, process)
				}
			}
			return processes, nil
		},
		Panes: panes,
	}
}

func syntheticAgentProcess(pane AgentTmuxPane) (AgentProcess, bool) {
	kind := syntheticPaneAgentKind(pane.Session)
	switch kind {
	case "claude", "codex":
		return AgentProcess{PID: pane.PID, Command: kind, Executable: kind}, true
	case "opencode":
		// Keep this command aligned with the session seeded by the Go synthetic
		// fixture. A stale token makes status unavailable and drops valid rows.
		return AgentProcess{PID: pane.PID, Command: "opencode --mock --session " + syntheticOpenCodeSession, Executable: "python3"}, true
	default:
		return AgentProcess{}, false
	}
}

func syntheticPaneAgentKind(session string) string {
	parts := strings.Split(strings.TrimSpace(session), "-")
	if len(parts) == 0 {
		return ""
	}
	return strings.ToLower(parts[len(parts)-1])
}

// LinuxOpenCodeInventorySources deliberately uses a bounded /proc scan that
// reads command lines only for OpenCode candidates. Tmux enrichment is bounded
// separately by the token collector; the serving process enables the global
// roster requirement explicitly, while isolated tests can opt into their own
// private socket through YOMUX_OPENCODE_TMUX_SCOPE.
func LinuxOpenCodeInventorySources() OpenCodeInventorySources {
	return OpenCodeInventorySources{
		Processes: linuxOpenCodeProcesses, Panes: linuxTmuxPanes, CWD: linuxCWD,
		Started: linuxStartedIdentity, StartTime: linuxProcessStartedAt,
		RequireTmuxPane: productenv.Value("OPENCODE_TMUX_SCOPE") == "1",
	}
}

func linuxProcesses(ctx context.Context) ([]AgentProcess, error) {
	commandContext, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	return linuxProcessesFromProc(commandContext, "/proc", readDirNames, boundedProcReadFile, os.Readlink)
}

func linuxOpenCodeProcesses(ctx context.Context) ([]AgentProcess, error) {
	return linuxOpenCodeProcessesFromProc(ctx, "/proc", readDirNames, boundedProcReadFile, os.Readlink)
}

func linuxOpenCodeProcessesFromProc(
	ctx context.Context,
	procRoot string,
	readDir func(string) ([]string, error),
	readFile func(string) ([]byte, error),
	readLink func(string) (string, error),
) ([]AgentProcess, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	entries, err := readDir(procRoot)
	if err != nil {
		return nil, fmt.Errorf("read process directory: %w", err)
	}
	if len(entries) > maxProcessEntries {
		return nil, errors.New("process inventory bound exceeded")
	}
	processes := make([]AgentProcess, 0)
	for _, name := range entries {
		if err := ctx.Err(); err != nil {
			return processes, err
		}
		pid, err := strconv.Atoi(name)
		if err != nil || pid <= 0 {
			continue
		}
		pidRoot := filepath.Join(procRoot, name)
		statData, err := readFile(filepath.Join(pidRoot, "stat"))
		if err != nil {
			continue
		}
		statPID, ppid, comm, ok := parseLinuxProcessParent(string(statData))
		if !ok || statPID != pid {
			continue
		}
		executable := ""
		if readLink != nil {
			executable, _ = readLink(filepath.Join(pidRoot, "exe"))
		}
		command := comm
		// Native OpenCode is identified by /proc/exe. The command-line read is
		// also needed for the explicit deterministic Python mock entry point.
		candidate := strings.EqualFold(filepath.Base(strings.TrimSuffix(executable, " (deleted)")), "opencode")
		if !candidate && strings.HasPrefix(strings.ToLower(filepath.Base(executable)), "python") {
			if commandData, readErr := readFile(filepath.Join(pidRoot, "cmdline")); readErr == nil {
				command = boundedProcCommand(commandData)
			}
			candidate = ClassifyAgent(command, executable) == "opencode"
		}
		if !candidate {
			continue
		}
		if commandData, readErr := readFile(filepath.Join(pidRoot, "cmdline")); readErr == nil {
			if value := boundedProcCommand(commandData); value != "" {
				command = value
			}
		}
		processes = append(processes, AgentProcess{PID: pid, PPID: ppid, Command: command, Executable: executable})
	}
	sort.Slice(processes, func(i, j int) bool { return processes[i].PID < processes[j].PID })
	return processes, nil
}

func linuxProcessesFromProc(
	ctx context.Context,
	procRoot string,
	readDir func(string) ([]string, error),
	readFile func(string) ([]byte, error),
	readLink func(string) (string, error),
) ([]AgentProcess, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	entries, err := readDir(procRoot)
	if err != nil {
		return nil, fmt.Errorf("read process directory: %w", err)
	}
	if len(entries) > maxProcessEntries {
		return nil, errors.New("process inventory bound exceeded")
	}
	processes := make([]AgentProcess, 0, len(entries))
	for _, name := range entries {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		pid, err := strconv.Atoi(name)
		if err != nil || pid <= 0 {
			continue
		}
		pidRoot := filepath.Join(procRoot, name)
		statData, err := readFile(filepath.Join(pidRoot, "stat"))
		if err != nil {
			// Processes can exit between enumeration and the first read.
			continue
		}
		statPID, ppid, comm, ok := parseLinuxProcessParent(string(statData))
		if !ok || statPID != pid {
			continue
		}
		command := comm
		if commandData, readErr := readFile(filepath.Join(pidRoot, "cmdline")); readErr == nil {
			if candidate := boundedProcCommand(commandData); candidate != "" {
				command = candidate
			}
		}
		executable := ""
		if readLink != nil {
			executable, _ = readLink(filepath.Join(pidRoot, "exe"))
		}
		processes = append(processes, AgentProcess{PID: pid, PPID: ppid, Command: command, Executable: executable})
	}
	sort.Slice(processes, func(i, j int) bool { return processes[i].PID < processes[j].PID })
	return processes, nil
}

func parseLinuxProcessParent(text string) (pid, ppid int, comm string, ok bool) {
	open := strings.Index(text, " (")
	close := strings.LastIndex(text, ") ")
	if open < 1 || close < open {
		return 0, 0, "", false
	}
	pid, err := strconv.Atoi(strings.TrimSpace(text[:open]))
	if err != nil {
		return 0, 0, "", false
	}
	comm = text[open+2 : close]
	fields := strings.Fields(text[close+2:])
	if len(fields) < 2 {
		return 0, 0, "", false
	}
	ppid, err = strconv.Atoi(fields[1])
	if err != nil || ppid < 0 {
		return 0, 0, "", false
	}
	return pid, ppid, comm, true
}

func boundedProcCommand(data []byte) string {
	if len(data) > maxHostInputBytes {
		data = data[:maxHostInputBytes]
	}
	command := strings.TrimRight(strings.ReplaceAll(string(data), "\x00", " "), " ")
	if len(command) > linuxProcessCommandWidth {
		command = command[:linuxProcessCommandWidth]
	}
	return strings.TrimSpace(command)
}

func boundedProcReadFile(name string) ([]byte, error) {
	return boundedOSReadFile(name)
}

func linuxTmuxSessions(ctx context.Context) (map[string]struct{}, error) {
	commandContext, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	output, err := runBoundedCommand(commandContext, tmuxCommandArgs("list-sessions", "-F", "#{session_name}"))
	if err != nil || output.ExitCode != 0 {
		if TmuxNoServerError(output.Stderr) {
			return map[string]struct{}{}, nil
		}
		return nil, errors.New("tmux list-sessions failed")
	}
	sessions := parseTmuxSessions(string(output.Stdout))
	return sessions, nil
}

func parseTmuxSessions(text string) map[string]struct{} {
	sessions := make(map[string]struct{})
	for _, line := range strings.Split(text, "\n") {
		name := strings.TrimSuffix(line, "\r")
		if name != "" {
			sessions[name] = struct{}{}
		}
	}
	return sessions
}

func linuxTmuxPanes(ctx context.Context) ([]AgentTmuxPane, error) {
	panes, err := linuxTmuxPaneRoster(ctx)
	if err != nil {
		return nil, err
	}
	return filterOwnedTabberViewAliases(panes), nil
}

// TabberViewSessionAliases returns only aliases whose source tmux session is
// present in the same roster. Historical token facts can outlive the view
// session, so callers use this bounded live mapping when presenting old series.
func TabberViewSessionAliases(ctx context.Context) (map[string]string, error) {
	panes, err := linuxTmuxPaneRoster(ctx)
	if err != nil {
		return nil, err
	}
	return tabberViewSessionAliases(panes), nil
}

func tabberViewSessionAliases(panes []AgentTmuxPane) map[string]string {
	sourceNames := make(map[string]string, len(panes))
	for _, pane := range panes {
		if pane.SessionID != "" && pane.ViewSourceID == "" {
			sourceNames[pane.SessionID] = pane.Session
		}
	}
	aliases := make(map[string]string)
	for _, pane := range panes {
		if !strings.HasPrefix(pane.Session, TabberViewSessionPrefix) || pane.ViewSourceID == "" {
			continue
		}
		if sourceName, exists := sourceNames[pane.ViewSourceID]; exists && sourceName != pane.Session {
			aliases[pane.Session] = sourceName
		}
	}
	return aliases
}

func linuxTmuxPaneRoster(ctx context.Context) ([]AgentTmuxPane, error) {
	commandContext, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	format := "#{session_name}\t#{session_id}\t#{@yomux_view_source_id}\t#{window_index}\t#{window_name}\t#{pane_index}\t#{pane_id}\t#{pane_current_path}\t#{pane_current_command}\t#{pane_title}\t#{pane_pid}"
	output, err := runBoundedCommand(commandContext, tmuxCommandArgs("list-panes", "-a", "-F", format))
	if err != nil || output.ExitCode != 0 {
		if TmuxNoServerError(output.Stderr) {
			return []AgentTmuxPane{}, nil
		}
		return nil, errors.New("tmux list-panes failed")
	}
	return parseAgentTmuxPanes(string(output.Stdout)), nil
}

func filterOwnedTabberViewAliases(panes []AgentTmuxPane) []AgentTmuxPane {
	aliases := tabberViewSessionAliases(panes)
	filtered := make([]AgentTmuxPane, 0, len(panes))
	for _, pane := range panes {
		_, ownedAlias := aliases[pane.Session]
		if !ownedAlias {
			filtered = append(filtered, pane)
		}
	}
	return filtered
}

func parseAgentTmuxPanes(text string) []AgentTmuxPane {
	result := []AgentTmuxPane{}
	for _, line := range strings.Split(text, "\n") {
		parts := strings.Split(line, "\t")
		if len(parts) != 11 {
			continue
		}
		pid, err := strconv.Atoi(parts[10])
		if err != nil || pid <= 0 {
			continue
		}
		result = append(result, AgentTmuxPane{Session: parts[0], SessionID: parts[1], ViewSourceID: parts[2], Window: parts[3], WindowName: parts[4], Pane: parts[5], PaneID: parts[6], Directory: parts[7], Command: parts[8], Title: parts[9], PID: pid})
	}
	return result
}

func linuxCWD(pid int) (string, error) {
	return os.Readlink(filepath.Join("/proc", strconv.Itoa(pid), "cwd"))
}

func linuxStartedIdentity(pid int) (string, error) {
	data, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "stat"))
	if err != nil {
		return "", err
	}
	line := string(data)
	closeParen := strings.LastIndex(line, ")")
	if closeParen < 0 {
		return "", errors.New("invalid proc stat")
	}
	fields := strings.Fields(line[closeParen+1:])
	if len(fields) <= 19 {
		return "", errors.New("missing proc start time")
	}
	return "proc:" + fields[19], nil
}

func linuxProcessStartedAt(pid int) (float64, error) {
	statData, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "stat"))
	if err != nil {
		return 0, err
	}
	closeParen := strings.LastIndex(string(statData), ")")
	if closeParen < 0 {
		return 0, errors.New("invalid proc stat")
	}
	fields := strings.Fields(string(statData)[closeParen+1:])
	if len(fields) <= 19 {
		return 0, errors.New("missing proc start time")
	}
	startTicks, err := strconv.ParseFloat(fields[19], 64)
	if err != nil || startTicks < 0 {
		return 0, errors.New("invalid proc start time")
	}
	bootTime := float64(0)
	procStat, err := os.ReadFile("/proc/stat")
	if err != nil {
		return 0, err
	}
	for _, line := range strings.Split(string(procStat), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 2 && fields[0] == "btime" {
			bootTime, err = strconv.ParseFloat(fields[1], 64)
			if err != nil {
				return 0, errors.New("invalid proc boot time")
			}
			break
		}
	}
	if bootTime <= 0 {
		return 0, errors.New("missing proc boot time")
	}
	return bootTime + startTicks/100.0, nil
}
