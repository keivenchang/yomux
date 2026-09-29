package stats

import (
	"context"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"
)

func finite(value float64) bool { return !math.IsNaN(value) && !math.IsInf(value, 0) }

const maxHostCommandOutput = 8 << 20

// LinuxHostSources adapts bounded Linux files and optional GPU commands to the
// collector boundary. The parser functions remain pure and are tested with
// synthetic inputs in host_collectors_test.go.
type LinuxHostSources struct {
	procRoot          string
	gpuRoot           string
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

// DarwinHostSources adapts bounded macOS command output to the common host
// collector boundary. Commands are injected so captured plist/proc fixtures
// can exercise the same path without requiring macOS in the test runner.
type DarwinHostSources struct {
	run      func(context.Context, []string) (CommandOutput, error)
	readFile func(string) ([]byte, error)
}

func NewHostSources() HostSources {
	if runtime.GOOS == "darwin" {
		return &DarwinHostSources{run: runBoundedCommand, readFile: boundedOSReadFile}
	}
	if runtime.GOOS != "linux" {
		return unavailableHostSources{}
	}
	return &LinuxHostSources{
		procRoot:    "/proc",
		gpuRoot:     "/sys/class/drm",
		readFile:    boundedOSReadFile,
		readDir:     readDirNames,
		readLink:    os.Readlink,
		run:         runBoundedCommand,
		pageSize:    uint64(os.Getpagesize()),
		clockTick:   100,
		binaryCache: make(map[string]string),
	}
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

func (s *DarwinHostSources) CPU(ctx context.Context) (CPUSnapshot, error) {
	result, err := s.run(ctx, []string{"sysctl", "-n", "kern.cp_time"})
	if err != nil || result.ExitCode != 0 {
		return CPUSnapshot{}, errors.New("Darwin CPU counters unavailable")
	}
	fields := strings.Fields(string(result.Stdout))
	if len(fields) < 4 {
		return CPUSnapshot{}, errors.New("invalid Darwin CPU counters")
	}
	var total, idle uint64
	for index, field := range fields {
		value, parseErr := strconv.ParseUint(field, 10, 64)
		if parseErr != nil || total > ^uint64(0)-value {
			return CPUSnapshot{}, errors.New("invalid Darwin CPU counters")
		}
		total += value
		if index == 3 {
			idle = value
		}
	}
	if total == 0 || idle > total {
		return CPUSnapshot{}, errors.New("invalid Darwin CPU counters")
	}
	rows, ok := DarwinProcessCensus(s.run, ctx)
	if !ok {
		return CPUSnapshot{}, errors.New("Darwin process census unavailable")
	}
	processes := make(map[string]CPUProcessCounters, len(rows))
	for _, row := range rows {
		cpu, ok := cpuTicks(row.CPUSeconds, 100)
		if !ok || strings.TrimSpace(row.Identity) == "" {
			continue
		}
		processes[row.Identity] = CPUProcessCounters{Identity: row.Identity, CPU: cpu, Binary: row.Binary}
	}
	if len(rows) > 0 && len(processes) == 0 {
		return CPUSnapshot{}, errors.New("invalid Darwin process census")
	}
	return CPUSnapshot{Host: CPUHostCounters{Total: total, Busy: total - idle}, Processes: processes, ProcessTicksPerSecond: 100}, nil
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
	rows, _ := DarwinProcessCensus(s.run, ctx)
	return MemoryCounters{CapacityBytes: total, UsedBytes: total - darwinAvailableMemory(s, ctx, total)}, rows, nil
}

func (s *DarwinHostSources) GPU(ctx context.Context) ([]GPUDevice, error) {
	result, err := s.run(ctx, []string{"ioreg", "-a", "-r", "-d1", "-w0", "-c", "IOAccelerator"})
	if err != nil || result.ExitCode != 0 {
		return nil, errors.New("ioreg unavailable")
	}
	devices := ParseIOAccelerator(result.Stdout)
	return devices, nil
}

func DarwinProcessCensus(run func(context.Context, []string) (CommandOutput, error), ctx context.Context) ([]ProcessCensusRow, bool) {
	result, err := run(ctx, []string{"ps", "-axo", "pid=,rss=,time=,lstart=,comm="})
	if err != nil || result.ExitCode != 0 {
		return nil, false
	}
	rows := []ProcessCensusRow{}
	for _, line := range strings.Split(string(result.Stdout), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 9 {
			continue
		}
		pid, e1 := strconv.Atoi(fields[0])
		rss, e2 := strconv.ParseUint(fields[1], 10, 64)
		if e1 != nil || e2 != nil || rss > ^uint64(0)/1024 {
			continue
		}
		cpu := parseDarwinCPUTime(fields[2])
		if !finite(cpu) {
			continue
		}
		binary := normalizeHostBinary(strings.Join(fields[8:], " "))
		if binary != "" {
			rows = append(rows, ProcessCensusRow{PID: pid, Identity: fmt.Sprintf("%d:%s", pid, strings.Join(fields[3:8], " ")), Binary: binary, CPUSeconds: cpu, RSSBytes: rss * 1024})
		}
	}
	return rows, len(rows) > 0
}

func parseDarwinCPUTime(value string) float64 {
	parts := strings.Split(value, ":")
	if len(parts) != 3 {
		return math.NaN()
	}
	hours, e1 := strconv.ParseFloat(parts[0], 64)
	minutes, e2 := strconv.ParseFloat(parts[1], 64)
	seconds, e3 := strconv.ParseFloat(parts[2], 64)
	if e1 != nil || e2 != nil || e3 != nil {
		return math.NaN()
	}
	return hours*3600 + minutes*60 + seconds
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
	rows, ok := s.processCensus()
	if !ok {
		return CPUSnapshot{}, errors.New("Linux process census unavailable")
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
	return CPUSnapshot{Host: host, Processes: processes, ProcessTicksPerSecond: s.clockTick}, nil
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
	rows, _ := s.processCensus()
	return memory, rows, nil
}

func (s *LinuxHostSources) GPU(ctx context.Context) ([]GPUDevice, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	devices, ok := LinuxGPUDevices(HostCollectorInputs{ReadFile: s.readFile, ReadDir: s.readDir}, s.gpuRoot)
	if !ok {
		return nil, errors.New("Linux GPU metrics unavailable")
	}
	return devices, nil
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
	entries, err := os.ReadDir(name)
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(entries))
	for _, entry := range entries {
		names = append(names, entry.Name())
	}
	return names, nil
}

func runBoundedCommand(ctx context.Context, args []string) (CommandOutput, error) {
	if len(args) == 0 {
		return CommandOutput{}, errors.New("empty host command")
	}
	command := exec.CommandContext(ctx, args[0], args[1:]...)
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
