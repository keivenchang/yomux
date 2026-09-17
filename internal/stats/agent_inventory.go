package stats

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	linuxProcessCommandWidth      = 256
	opencodeTmuxAnnotationTimeout = 100 * time.Millisecond
)

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
	Session    string
	Window     string
	WindowName string
	Pane       string
	PaneID     string
	Directory  string
	Command    string
	Title      string
	PID        int
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
// collection. Processes are discovered from /proc first; tmux is only an
// opportunistic annotation and is never required to identify OpenCode.
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

	records := make([]AgentRecord, 0)
	seen := make(map[string]bool)
	for _, process := range processes {
		kind := ClassifyAgent(process.Command, process.Executable)
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
	if sources.RequireTmuxPane && len(paneByPID) > 0 {
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
	// Codex's Linux launcher is a Node entry point whose child native binary
	// remains the agent executable. Use that executable identity only for the
	// native child; arbitrary wrapper arguments must not become agent records.
	if strings.EqualFold(filepath.Base(strings.TrimSuffix(executable, " (deleted)")), "codex") {
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

// LinuxOpenCodeInventorySources deliberately uses a bounded /proc scan that
// reads command lines only for OpenCode candidates. Tmux enrichment is bounded
// separately by the token collector and remains optional.
func LinuxOpenCodeInventorySources() OpenCodeInventorySources {
	return OpenCodeInventorySources{
		Processes: linuxOpenCodeProcesses, Panes: linuxTmuxPanes, CWD: linuxCWD,
		Started: linuxStartedIdentity, StartTime: linuxProcessStartedAt,
		RequireTmuxPane: os.Getenv("YOLOMUX_OPENCODE_TMUX_SCOPE") == "1",
	}
}

func linuxProcesses(ctx context.Context) ([]AgentProcess, error) {
	commandContext, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	return linuxProcessesFromProc(commandContext, "/proc", os.ReadDir, boundedProcReadFile, os.Readlink)
}

func linuxOpenCodeProcesses(ctx context.Context) ([]AgentProcess, error) {
	return linuxOpenCodeProcessesFromProc(ctx, "/proc", os.ReadDir, boundedProcReadFile, os.Readlink)
}

func linuxOpenCodeProcessesFromProc(
	ctx context.Context,
	procRoot string,
	readDir func(string) ([]os.DirEntry, error),
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
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			return processes, err
		}
		pid, err := strconv.Atoi(entry.Name())
		if err != nil || pid <= 0 {
			continue
		}
		pidRoot := filepath.Join(procRoot, entry.Name())
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
	readDir func(string) ([]os.DirEntry, error),
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
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		pid, err := strconv.Atoi(entry.Name())
		if err != nil || pid <= 0 {
			continue
		}
		pidRoot := filepath.Join(procRoot, entry.Name())
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

func linuxTmuxPanes(ctx context.Context) ([]AgentTmuxPane, error) {
	commandContext, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	format := "#{session_name}\t#{window_index}\t#{window_name}\t#{pane_index}\t#{pane_id}\t#{pane_current_path}\t#{pane_current_command}\t#{pane_title}\t#{pane_pid}"
	output, err := runBoundedCommand(commandContext, tmuxCommandArgs("list-panes", "-a", "-F", format))
	if err != nil || output.ExitCode != 0 {
		return nil, errors.New("tmux list-panes failed")
	}
	return parseAgentTmuxPanes(string(output.Stdout)), nil
}

func parseAgentTmuxPanes(text string) []AgentTmuxPane {
	result := []AgentTmuxPane{}
	for _, line := range strings.Split(text, "\n") {
		parts := strings.Split(line, "\t")
		if len(parts) != 9 {
			continue
		}
		pid, err := strconv.Atoi(parts[8])
		if err != nil || pid <= 0 {
			continue
		}
		result = append(result, AgentTmuxPane{Session: parts[0], Window: parts[1], WindowName: parts[2], Pane: parts[3], PaneID: parts[4], Directory: parts[5], Command: parts[6], Title: parts[7], PID: pid})
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
