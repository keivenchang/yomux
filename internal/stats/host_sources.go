package stats

import (
	"context"
	"errors"
	"fmt"
	"github.com/keivenchang/yomux/internal/tmuxcmd"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

func finite(value float64) bool { return !math.IsNaN(value) && !math.IsInf(value, 0) }

const maxHostCommandOutput = 8 << 20

// NewHostCommand creates a host collector command with the deterministic C
// locale required by parsers of ps output.
func NewHostCommand(ctx context.Context, name string, args ...string) *exec.Cmd {
	if filepath.Base(name) == "tmux" {
		return tmuxcmd.Command(ctx, args...)
	}
	command := exec.CommandContext(ctx, name, args...)
	if filepath.Base(name) == "ps" || filepath.Base(name) == "df" {
		command.Env = hostCommandEnvironment("LC_ALL", "C")
	}
	return command
}

func hostCommandEnvironment(name, value string) []string {
	environment := os.Environ()
	result := make([]string, 0, len(environment)+1)
	set := false
	for _, entry := range environment {
		if strings.HasPrefix(entry, name+"=") {
			if !set {
				result = append(result, name+"="+value)
				set = true
			}
			continue
		}
		result = append(result, entry)
	}
	if !set {
		result = append(result, name+"="+value)
	}
	return result
}

// LinuxHostSources adapts bounded Linux files and optional GPU commands to the
// collector boundary. The parser functions remain pure and are tested with
// synthetic inputs in host_collectors_test.go.
type LinuxHostSources struct {
	procRoot string
	gpuRoot  string
	// processPID is the server-owned process whose CPU counter must remain
	// measurable even when a host-wide census is too large to scan within the
	// collector budget. It is zero for injected/test sources that do not have an
	// owner fallback.
	processPID        int
	logicalCPUs       int
	readFile          func(string) ([]byte, error)
	readDir           func(string) ([]string, error)
	readLink          func(string) (string, error)
	run               func(context.Context, []string) (CommandOutput, error)
	pageSize          uint64
	clockTick         uint64
	processMu         sync.Mutex
	binaryCache       map[string]string
	processCacheAt    time.Time
	processCacheRows  []ProcessCensusRow
	processCacheValid bool
}

const linuxProcessCensusCacheTTL = 500 * time.Millisecond

const linuxClockTickQueryTimeout = 500 * time.Millisecond
const maxDiskMounts = 512

// DarwinHostSources adapts bounded macOS system commands and native Mach/libproc
// snapshots to the common host collector boundary.
type DarwinHostSources struct {
	run         func(context.Context, []string) (CommandOutput, error)
	logicalCPUs int
}

func NewHostSources() HostSources {
	if runtime.GOOS == "darwin" {
		return &DarwinHostSources{run: runBoundedCommand, logicalCPUs: darwinSystemLogicalCPUs()}
	}
	if runtime.GOOS != "linux" {
		return unavailableHostSources{}
	}
	return &LinuxHostSources{
		procRoot:    "/proc",
		gpuRoot:     "/sys/class/drm",
		processPID:  os.Getpid(),
		logicalCPUs: linuxSystemLogicalCPUs(boundedOSReadFile),
		readFile:    boundedOSReadFile,
		readDir:     readDirNames,
		readLink:    os.Readlink,
		run:         runBoundedCommand,
		pageSize:    uint64(os.Getpagesize()),
		clockTick:   linuxClockTickRate(runBoundedCommand),
		binaryCache: make(map[string]string),
	}
}

func linuxClockTickRate(run func(context.Context, []string) (CommandOutput, error)) uint64 {
	if run == nil {
		return 0
	}
	ctx, cancel := context.WithTimeout(context.Background(), linuxClockTickQueryTimeout)
	defer cancel()
	result, err := run(ctx, []string{"getconf", "CLK_TCK"})
	if err != nil || result.ExitCode != 0 || len(result.Stdout) > 64 {
		return 0
	}
	rate, err := strconv.ParseUint(strings.TrimSpace(string(result.Stdout)), 10, 64)
	if err != nil || rate == 0 {
		return 0
	}
	return rate
}

func (s *LinuxHostSources) SystemLogicalCPUs() int {
	if s == nil {
		return 0
	}
	return s.logicalCPUs
}

// ProcessTicksPerSecond reports the measured Linux /proc CPU counter unit.
// Zero means getconf could not provide the unit and CPU rates must stay unavailable.
func (s *LinuxHostSources) ProcessTicksPerSecond() uint64 {
	if s == nil {
		return 0
	}
	return s.clockTick
}

func linuxSystemLogicalCPUs(readFile func(string) ([]byte, error)) int {
	if readFile == nil {
		return 0
	}
	data, err := boundedRead(readFile, "/sys/devices/system/cpu/online")
	if err != nil {
		return 0
	}
	return ParseLinuxOnlineCPUCount(data)
}

// ParseLinuxOnlineCPUCount counts a synthetic or captured Linux CPU-list file
// such as "0-3,5". Malformed and overlapping ranges return zero so callers do
// not publish grouped CPU values with a guessed denominator.
func ParseLinuxOnlineCPUCount(data []byte) int {
	const maxLogicalCPUs = 1 << 16 // reject corrupt ranges before expanding them
	value := strings.TrimSpace(string(data))
	if value == "" {
		return 0
	}
	count := 0
	seen := make(map[int]struct{})
	for _, item := range strings.Split(value, ",") {
		if item == "" {
			return 0
		}
		bounds := strings.Split(item, "-")
		if len(bounds) > 2 {
			return 0
		}
		first, err := strconv.Atoi(bounds[0])
		if err != nil || first < 0 {
			return 0
		}
		last := first
		if len(bounds) == 2 {
			last, err = strconv.Atoi(bounds[1])
			if err != nil || last < first {
				return 0
			}
		}
		rangeOffset := last - first
		if rangeOffset >= maxLogicalCPUs {
			return 0
		}
		rangeCount := rangeOffset + 1
		if rangeCount > maxLogicalCPUs-count {
			return 0
		}
		for cpu := first; ; cpu++ {
			if _, exists := seen[cpu]; exists || count == int(^uint(0)>>1) {
				return 0
			}
			seen[cpu] = struct{}{}
			count++
			if cpu == last {
				break
			}
		}
	}
	return count
}

func (s *LinuxHostSources) processCensus() ([]ProcessCensusRow, bool) {
	s.processMu.Lock()
	defer s.processMu.Unlock()
	now := time.Now()
	if s.processCacheValid && !now.Before(s.processCacheAt) && now.Sub(s.processCacheAt) < linuxProcessCensusCacheTTL {
		return append([]ProcessCensusRow(nil), s.processCacheRows...), true
	}
	if s.binaryCache == nil {
		s.binaryCache = make(map[string]string)
	}
	rows, ok := LinuxProcessCensus(HostCollectorInputs{
		ReadFile: s.readFile, ReadDir: s.readDir, ReadLink: s.readLink,
		PageSize: s.pageSize, ClockTick: s.clockTick, BinaryCache: s.binaryCache,
	}, s.procRoot)
	if ok {
		s.processCacheRows = append(s.processCacheRows[:0], rows...)
		s.processCacheAt = now
		s.processCacheValid = true
	}
	return rows, ok
}

func (s *DarwinHostSources) SystemLogicalCPUs() int { return s.logicalCPUs }

const darwinProcessTicksPerSecond = 1_000_000_000

func (s *DarwinHostSources) CPU(ctx context.Context) (CPUSnapshot, error) {
	if err := ctx.Err(); err != nil {
		return CPUSnapshot{}, err
	}
	total, idle, available := darwinHostCPU()
	if !available {
		return CPUSnapshot{}, errors.New("Darwin CPU counters unavailable")
	}
	if idle > total {
		return CPUSnapshot{}, errors.New("invalid Darwin CPU counters")
	}
	rows, complete := DarwinProcessSnapshot()
	processes := make(map[string]CPUProcessCounters, len(rows))
	for _, row := range rows {
		cpu, ok := cpuTicks(row.CPUSeconds, darwinProcessTicksPerSecond)
		if !ok || row.Identity == "" {
			continue
		}
		processes[row.Identity] = CPUProcessCounters{Identity: row.Identity, CPU: cpu, Binary: normalizeHostBinary(row.Executable)}
	}
	return CPUSnapshot{Host: CPUHostCounters{Total: total, Busy: total - idle}, Processes: processes, ProcessCensusIncomplete: !complete, ProcessTicksPerSecond: darwinProcessTicksPerSecond}, nil
}

func (s *DarwinHostSources) Memory(ctx context.Context) (MemoryCounters, []ProcessCensusRow, error) {
	result, err := s.run(ctx, []string{"sysctl", "-n", "hw.memsize"})
	if err != nil || result.ExitCode != 0 {
		return MemoryCounters{}, nil, errors.New("Darwin memory counters unavailable")
	}
	total, err := strconv.ParseUint(strings.TrimSpace(string(result.Stdout)), 10, 64)
	if err != nil || total == 0 {
		return MemoryCounters{}, nil, errors.New("invalid Darwin memory counters")
	}
	rows, complete := DarwinProcessSnapshot()
	processRows := make([]ProcessCensusRow, 0, len(rows))
	for _, row := range rows {
		processRows = append(processRows, row.censusRow())
	}
	return MemoryCounters{CapacityBytes: total, UsedBytes: total - darwinAvailableMemory(s, ctx, total), ProcessCensusIncomplete: !complete}, processRows, nil
}

func (s *DarwinHostSources) GPU(ctx context.Context) ([]GPUDevice, error) {
	result, err := s.run(ctx, []string{"ioreg", "-a", "-r", "-d1", "-w0", "-c", "IOAccelerator"})
	if err != nil || result.ExitCode != 0 {
		return nil, errors.New("ioreg unavailable")
	}
	devices := ParseIOAccelerator(result.Stdout)
	return devices, nil
}

func (s *DarwinHostSources) Disk(ctx context.Context) ([]DiskUsageSample, error) {
	return collectDiskUsageFromDF(ctx, s.run)
}

func darwinAvailableMemory(s *DarwinHostSources, ctx context.Context, total uint64) uint64 {
	result, err := s.run(ctx, []string{"vm_stat"})
	if err != nil || result.ExitCode != 0 {
		return 0
	}
	page := uint64(os.Getpagesize())
	var available uint64
	for _, line := range strings.Split(string(result.Stdout), "\n") {
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		fields := strings.Fields(parts[1])
		if len(fields) == 0 {
			continue
		}
		value, err := strconv.ParseUint(strings.TrimSuffix(fields[0], "."), 10, 64)
		if err != nil || value > ^uint64(0)/page {
			continue
		}
		if strings.HasPrefix(parts[0], "Pages free") || strings.HasPrefix(parts[0], "Pages inactive") || strings.HasPrefix(parts[0], "Pages speculative") {
			if available <= ^uint64(0)-value*page {
				available += value * page
			}
		}
	}
	if available > total {
		return total
	}
	return available
}

func (s *LinuxHostSources) CPU(ctx context.Context) (CPUSnapshot, error) {
	if err := ctx.Err(); err != nil {
		return CPUSnapshot{}, err
	}
	data, err := s.readFile(filepath.Join(s.procRoot, "stat"))
	if err != nil {
		return CPUSnapshot{}, err
	}
	host, ok := ParseLinuxCPU(string(data))
	if !ok {
		return CPUSnapshot{}, errors.New("invalid Linux CPU counters")
	}
	if s.clockTick == 0 {
		// Keep host CPU available, but never publish process rates using a
		// guessed tick frequency when getconf could not establish the unit.
		return CPUSnapshot{Host: host, ProcessCensusIncomplete: true}, nil
	}
	rows, censusComplete, censusAvailable := s.processCensusWithOwnerFallback()
	if !censusAvailable {
		// A large or transiently unreadable /proc must not erase the valid host
		// CPU sample. Recover only the owned process, which is enough for the
		// Go process series and keeps the top-process view honest: it contains
		// one measured owner, not an invented partial census.
		rows = s.ownerProcessCensusRow()
		if len(rows) == 0 {
			return CPUSnapshot{}, errors.New("Linux process census unavailable")
		}
	}
	processes := make(map[string]CPUProcessCounters, len(rows))
	for _, row := range rows {
		cpu, ok := cpuTicks(row.CPUSeconds, s.clockTick)
		if !ok || strings.TrimSpace(row.Identity) == "" {
			continue
		}
		processes[row.Identity] = CPUProcessCounters{Identity: row.Identity, CPU: cpu, Binary: row.Binary}
	}
	if len(rows) > 0 && len(processes) == 0 {
		return CPUSnapshot{}, errors.New("invalid Linux process census")
	}
	return CPUSnapshot{
		Host: host, Processes: processes, ProcessTicksPerSecond: s.clockTick,
		ProcessCensusIncomplete: !censusComplete,
	}, nil
}

func (s *LinuxHostSources) ownerProcessCensusRow() []ProcessCensusRow {
	if s == nil || s.processPID <= 0 || s.readFile == nil {
		return nil
	}
	name := strconv.Itoa(s.processPID)
	data, err := boundedRead(s.readFile, filepath.Join(s.procRoot, name, "stat"))
	if err != nil {
		return nil
	}
	executable := ""
	if s.readLink != nil {
		executable, _ = s.readLink(filepath.Join(s.procRoot, name, "exe"))
	}
	row, ok := ParseLinuxProcessStat(string(data), executable, s.pageSize, s.clockTick)
	if !ok || row.PID != s.processPID {
		return nil
	}
	return []ProcessCensusRow{row}
}

// processCensusWithOwnerFallback keeps the server-owned process visible to
// both CPU and memory products when a host-wide /proc census is too large or
// races with process exit. A syntactically valid partial census that omitted
// the owner is not accepted as complete: publishing it would make the Go
// process disappear from one family while the host sample remains current.
func (s *LinuxHostSources) processCensusWithOwnerFallback() ([]ProcessCensusRow, bool, bool) {
	rows, censusComplete := s.processCensus()
	if censusComplete && s.processPID > 0 {
		ownerFound := false
		for _, row := range rows {
			if row.PID == s.processPID {
				ownerFound = true
				break
			}
		}
		censusComplete = ownerFound
	}
	if censusComplete {
		return rows, true, true
	}
	ownerRows := s.ownerProcessCensusRow()
	if len(ownerRows) == 0 {
		return nil, false, false
	}
	return ownerRows, false, true
}

func cpuTicks(seconds float64, ticksPerSecond uint64) (uint64, bool) {
	if !finite(seconds) || seconds < 0 || ticksPerSecond == 0 {
		return 0, false
	}
	value := seconds * float64(ticksPerSecond)
	if !finite(value) || value >= float64(^uint64(0)) {
		return 0, false
	}
	return uint64(value), true
}

func (s *LinuxHostSources) Memory(ctx context.Context) (MemoryCounters, []ProcessCensusRow, error) {
	if err := ctx.Err(); err != nil {
		return MemoryCounters{}, nil, err
	}
	data, err := s.readFile(filepath.Join(s.procRoot, "meminfo"))
	if err != nil {
		return MemoryCounters{}, nil, err
	}
	memory, ok := ParseLinuxMeminfo(string(data))
	if !ok {
		return MemoryCounters{}, nil, errors.New("invalid Linux memory counters")
	}
	rows, censusComplete, censusAvailable := s.processCensusWithOwnerFallback()
	memory.ProcessCensusIncomplete = !censusComplete || !censusAvailable
	return memory, rows, nil
}

func (s *LinuxHostSources) GPU(ctx context.Context) ([]GPUDevice, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	inputs := HostCollectorInputs{
		ReadFile: s.readFile,
		ReadDir:  s.readDir,
		Run: func(args []string) (CommandOutput, error) {
			if s.run == nil {
				return CommandOutput{}, errors.New("Linux GPU command unavailable")
			}
			return s.run(ctx, args)
		},
	}
	devices, _ := LinuxGPUDevices(inputs, s.gpuRoot)
	if nvidiaDevices, ok := CollectNvidiaSMI(inputs); ok {
		devices = withoutDuplicateNvidiaDRMDevices(devices, nvidiaDevices)
		devices = append(devices, nvidiaDevices...)
	}
	if len(devices) == 0 {
		return nil, errors.New("Linux GPU metrics unavailable")
	}
	return devices, nil
}

func withoutDuplicateNvidiaDRMDevices(drmDevices, nvidiaDevices []GPUDevice) []GPUDevice {
	nvidiaPCIAddresses := make(map[string]struct{}, len(nvidiaDevices))
	for _, device := range nvidiaDevices {
		if device.pciAddress != "" {
			nvidiaPCIAddresses[device.pciAddress] = struct{}{}
		}
	}
	result := make([]GPUDevice, 0, len(drmDevices))
	for _, device := range drmDevices {
		if _, duplicate := nvidiaPCIAddresses[device.pciAddress]; device.pciAddress != "" && duplicate {
			continue
		}
		result = append(result, device)
	}
	return result
}

func (s *LinuxHostSources) Disk(ctx context.Context) ([]DiskUsageSample, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return collectDiskUsageFromDF(ctx, s.run)
}

type unavailableHostSources struct{}

func (unavailableHostSources) CPU(context.Context) (CPUSnapshot, error) {
	return CPUSnapshot{}, errors.New("host sources unavailable on this platform")
}
func (unavailableHostSources) Memory(context.Context) (MemoryCounters, []ProcessCensusRow, error) {
	return MemoryCounters{}, nil, errors.New("host sources unavailable on this platform")
}
func (unavailableHostSources) GPU(context.Context) ([]GPUDevice, error) {
	return nil, errors.New("host sources unavailable on this platform")
}
func (unavailableHostSources) Disk(context.Context) ([]DiskUsageSample, error) {
	return nil, errors.New("host sources unavailable on this platform")
}

func ParseLinuxMountInfo(data []byte) ([]string, bool) {
	mounts := make(map[string]struct{})
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		if line == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 10 {
			return nil, false
		}
		mountpoint, ok := unescapeMountInfoPath(fields[4])
		if !ok || !filepath.IsAbs(mountpoint) || len(mountpoint) > 4096 {
			return nil, false
		}
		mounts[mountpoint] = struct{}{}
		if len(mounts) > maxDiskMounts {
			return nil, false
		}
	}
	return sortedMountpoints(mounts), true
}

func unescapeMountInfoPath(value string) (string, bool) {
	var result strings.Builder
	for index := 0; index < len(value); index++ {
		if value[index] != '\\' {
			result.WriteByte(value[index])
			continue
		}
		if index+3 >= len(value) {
			return "", false
		}
		code := value[index+1 : index+4]
		var decoded byte
		switch code {
		case "040":
			decoded = ' '
		case "011":
			decoded = '\t'
		case "012":
			decoded = '\n'
		case "134":
			decoded = '\\'
		default:
			return "", false
		}
		result.WriteByte(decoded)
		index += 3
	}
	return result.String(), true
}

func parseFstabMountpoints(data []byte) []string {
	mounts := make(map[string]struct{})
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 3 {
			continue
		}
		mountpoint, ok := unescapeMountInfoPath(fields[1])
		if !ok || !filepath.IsAbs(mountpoint) || len(mountpoint) > 4096 {
			continue
		}
		mounts[mountpoint] = struct{}{}
		if len(mounts) >= maxDiskMounts {
			break
		}
	}
	return sortedMountpoints(mounts)
}

func sortedMountpoints(mounts map[string]struct{}) []string {
	result := make([]string, 0, len(mounts))
	for mountpoint := range mounts {
		result = append(result, mountpoint)
	}
	sort.Strings(result)
	return result
}

// collectDiskUsageFromDF uses df's own visible filesystem list as the chart's
// source of truth. Mount tables also contain implementation mounts that df
// intentionally omits, which should not become graph series.
func collectDiskUsageFromDF(ctx context.Context, run func(context.Context, []string) (CommandOutput, error)) ([]DiskUsageSample, error) {
	if run == nil {
		return nil, errors.New("disk probe unavailable")
	}
	result, err := run(ctx, []string{"df", "-kP"})
	samples, ok := ParseDiskUsageDFListing(result.Stdout)
	if result.ExitCode != 0 {
		err = errors.Join(err, errors.New("disk usage partially unavailable"))
	}
	if !ok {
		err = errors.Join(err, errors.New("invalid disk usage listing"))
	}
	return samples, err
}

// ParseDiskUsageDFListing parses the POSIX one-filesystem-per-line form of
// df -kP and retains only mounts that df reports. It locates the four numeric
// columns from the right so filesystem names and mountpoints may contain
// whitespace.
func ParseDiskUsageDFListing(data []byte) ([]DiskUsageSample, bool) {
	if len(data) == 0 || len(data) > maxHostCommandOutput {
		return nil, false
	}
	lines := strings.Split(strings.TrimSuffix(string(data), "\n"), "\n")
	if len(lines) < 2 || len(lines) > maxDiskMounts+1 {
		return nil, false
	}
	valid := true
	samples := make([]DiskUsageSample, 0, len(lines)-1)
	seen := make(map[string]struct{}, len(lines)-1)
	for _, line := range lines[1:] {
		fields := strings.Fields(line)
		positions := make([]int, 0, len(fields))
		offset := 0
		for _, field := range fields {
			start := strings.Index(line[offset:], field) + offset
			positions = append(positions, start)
			offset = start + len(field)
		}
		if len(fields) < 6 {
			valid = false
			continue
		}
		percentIndex := -1
		for index := 4; index < len(fields)-1; index++ {
			mountpoint := line[positions[index+1]:]
			if !filepath.IsAbs(mountpoint) {
				continue
			}
			marker := strings.ToLower(fields[index])
			if strings.HasSuffix(marker, "%") || marker == "-" || marker == "n/a" || marker == "na" || marker == "?" {
				percentIndex = index
				break
			}
		}
		if percentIndex < 4 {
			valid = false
			continue
		}
		blocks, blocksErr := strconv.ParseUint(fields[percentIndex-3], 10, 64)
		used, usedErr := strconv.ParseUint(fields[percentIndex-2], 10, 64)
		available, availableErr := strconv.ParseUint(fields[percentIndex-1], 10, 64)
		percentText := strings.TrimSuffix(fields[percentIndex], "%")
		percent, percentErr := strconv.ParseUint(percentText, 10, 8)
		mountpoint := line[positions[percentIndex+1]:]
		if !filepath.IsAbs(mountpoint) || len(mountpoint) > 4096 {
			valid = false
			continue
		}
		if _, exists := seen[mountpoint]; exists {
			continue
		}
		seen[mountpoint] = struct{}{}
		sample := DiskUsageSample{
			SourceID:   diskMountSourceID(mountpoint),
			Mountpoint: mountpoint,
		}
		if blocksErr != nil || usedErr != nil || availableErr != nil || percentErr != nil || blocks == 0 || used > blocks || available > blocks || percent > 100 || used > ^uint64(0)-available || used+available == 0 || used > ^uint64(0)/1024 || used+available > ^uint64(0)/1024 {
			sample.Reason = "invalid_sample"
		} else {
			sample.UsedBytes = used * 1024
			sample.CapacityBytes = (used + available) * 1024
		}
		samples = append(samples, sample)
	}
	if len(samples) == 0 {
		return nil, false
	}
	return samples, valid
}

func boundedOSReadFile(name string) ([]byte, error) {
	file, err := os.Open(name)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, maxHostInputBytes+1))
	if err != nil {
		return nil, err
	}
	if len(data) > maxHostInputBytes {
		return nil, fmt.Errorf("host input exceeds %d bytes", maxHostInputBytes)
	}
	return data, nil
}

func readDirNames(name string) ([]string, error) {
	return ReadDirNamesBounded(name, maxProcessEntries+1)
}

// ReadDirNamesBounded reads at most limit entries incrementally. Callers can
// request their accepted maximum plus one to distinguish complete input from
// an oversized directory without materializing the whole directory first.
func ReadDirNamesBounded(name string, limit int) ([]string, error) {
	if limit <= 0 {
		return nil, errors.New("directory entry limit must be positive")
	}
	directory, err := os.Open(name)
	if err != nil {
		return nil, err
	}
	defer directory.Close()
	names := make([]string, 0, limit)
	for len(names) < limit {
		batchSize := min(256, limit-len(names))
		entries, readErr := directory.ReadDir(batchSize)
		for _, entry := range entries {
			names = append(names, entry.Name())
		}
		if errors.Is(readErr, io.EOF) {
			return names, nil
		}
		if readErr != nil {
			return nil, readErr
		}
	}
	return names, nil
}

func runBoundedCommand(ctx context.Context, args []string) (CommandOutput, error) {
	if len(args) == 0 {
		return CommandOutput{}, errors.New("empty host command")
	}
	command := NewHostCommand(ctx, args[0], args[1:]...)
	stdout := boundedCommandBuffer{limit: maxHostCommandOutput}
	stderr := boundedCommandBuffer{limit: maxHostCommandOutput}
	command.Stdout = &stdout
	command.Stderr = &stderr
	err := command.Run()
	result := CommandOutput{Stdout: stdout.Bytes(), Stderr: stderr.Bytes()}
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			result.ExitCode = exitErr.ExitCode()
			if stdout.exceeded || stderr.exceeded {
				return result, errors.New("host command output exceeds bound")
			}
			return result, err
		}
		return result, err
	}
	if stdout.exceeded || stderr.exceeded {
		return result, errors.New("host command output exceeds bound")
	}
	return result, nil
}

type boundedCommandBuffer struct {
	data     []byte
	limit    int
	exceeded bool
}

func (b *boundedCommandBuffer) Write(data []byte) (int, error) {
	remaining := b.limit + 1 - len(b.data)
	if remaining <= 0 {
		b.exceeded = true
		return len(data), nil
	}
	if len(data) > remaining {
		b.data = append(b.data, data[:remaining]...)
		b.exceeded = true
		return len(data), nil
	}
	b.data = append(b.data, data...)
	return len(data), nil
}

func (b *boundedCommandBuffer) Bytes() []byte { return b.data }
