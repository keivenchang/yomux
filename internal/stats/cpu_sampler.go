package stats

import (
	"math"
	"sort"
	"strings"
	"time"
)

// CPUHostCounters are cumulative host counters. Busy and Total use the same
// unit, and Total must include Busy.
type CPUHostCounters struct {
	Busy  uint64
	Total uint64
}

// CPUProcessCounters identify a process independently of its collector key.
// CPU is a cumulative process counter in the snapshot's process-counter unit.
type CPUProcessCounters struct {
	Identity string
	CPU      uint64
	Binary   string
}

// CPUSnapshot contains only collector-provided values. Process CPU counters
// are cumulative ticks; ProcessTicksPerSecond identifies their unit.
type CPUSnapshot struct {
	Host                  CPUHostCounters
	Processes             map[string]CPUProcessCounters
	ProcessTicksPerSecond uint64
}

type CPUMeasurement struct {
	Value float64
	Valid bool
}

type CPUSampleReason string

const (
	CPUValid                 CPUSampleReason = "valid"
	CPUFirstSample           CPUSampleReason = "first_sample"
	CPUInvalidDuration       CPUSampleReason = "invalid_duration"
	CPUHostCounterReset      CPUSampleReason = "host_counter_reset"
	CPUProcessCounterReset   CPUSampleReason = "process_counter_reset"
	CPUProcessRestart        CPUSampleReason = "process_restart"
	CPUGroupMembershipChange CPUSampleReason = "group_membership_change"
	CPUUnavailable           CPUSampleReason = "unavailable"
)

type CPUSample struct {
	Host              CPUMeasurement
	Process           CPUMeasurement
	Group             CPUMeasurement
	ProcessCPUPercent map[string]float64
	Reason            CPUSampleReason
}

// CPUDeltaSampler compares cumulative counters with the previous accepted
// snapshot. Host CPU is normalized to host Total; wall-clock process CPU is
// normalized to the process counter rate and is not host-normalized.
type CPUDeltaSampler struct {
	processKey  string
	groupKeys   []string
	logicalCPUs int
	wallProcess bool
	previous    *CPUSnapshot
}

// NewCPUDeltaSampler retains the original API. The optional logical CPU count
// is used for grouped binary percentages; one is the historical default.
func NewCPUDeltaSampler(processKey string, groupKeys []string, logicalCPUs ...int) *CPUDeltaSampler {
	group := append([]string(nil), groupKeys...)
	sort.Strings(group)
	cpus := 1
	if len(logicalCPUs) > 0 && logicalCPUs[0] > 0 {
		cpus = logicalCPUs[0]
	}
	return &CPUDeltaSampler{processKey: processKey, groupKeys: group, logicalCPUs: cpus, wallProcess: len(logicalCPUs) > 0}
}

func (s *CPUDeltaSampler) Sample(snapshot CPUSnapshot, wallDuration time.Duration) CPUSample {
	result := CPUSample{}
	if s.previous == nil {
		s.previous = cloneCPUSnapshot(snapshot)
		result.Reason = CPUFirstSample
		return result
	}
	previous := s.previous
	s.previous = cloneCPUSnapshot(snapshot)
	if wallDuration <= 0 {
		result.Reason = CPUInvalidDuration
		return result
	}
	if snapshot.Host.Busy < previous.Host.Busy || snapshot.Host.Total < previous.Host.Total {
		result.Reason = CPUHostCounterReset
		return result
	}
	hostDelta := snapshot.Host.Total - previous.Host.Total
	if hostDelta == 0 || snapshot.Host.Busy < previous.Host.Busy {
		result.Reason = CPUUnavailable
		return result
	}
	result.Host = validMeasurement(percentage(snapshot.Host.Busy-previous.Host.Busy, hostDelta))

	process, previousProcess, ok := selectedProcessPair(snapshot, *previous, s.processKey)
	if !ok {
		result.Reason = CPUUnavailable
		return result
	}
	if process.Identity != previousProcess.Identity {
		result.Reason = CPUProcessRestart
		return result
	}
	if process.CPU < previousProcess.CPU {
		result.Reason = CPUProcessCounterReset
		return result
	}
	processPercent := percentage(process.CPU-previousProcess.CPU, hostDelta)
	if s.wallProcess {
		processPercent = wallPercentage(process.CPU-previousProcess.CPU, wallDuration, processCounterRate(snapshot, *previous))
	}
	result.Process = validMeasurement(processPercent)

	groupKeys := s.groupKeys
	autoGroups := len(groupKeys) == 0
	if autoGroups {
		groupKeys = stableProcessBinaries(snapshot, *previous)
	}
	if hasBinaries(snapshot, groupKeys) && hasBinaries(*previous, groupKeys) {
		var delta uint64
		for _, binary := range groupKeys {
			current, old, ok := binaryPair(snapshot, *previous, binary)
			if !ok || current < old {
				if autoGroups {
					continue
				}
				result.Reason = CPUProcessRestart
				return result
			}
			delta += current - old
		}
		if s.wallProcess {
			result.Group = validMeasurement(cpuPercentage(delta, wallDuration, s.logicalCPUs, processCounterRate(snapshot, *previous)))
			result.ProcessCPUPercent = groupedProcessPercent(snapshot, *previous, wallDuration, s.logicalCPUs, groupKeys)
		} else {
			result.Group = validMeasurement(percentage(delta, hostDelta))
		}
	} else if !autoGroups && len(s.groupKeys) > 0 {
		result.Reason = CPUGroupMembershipChange
		return result
	}
	result.Reason = CPUValid
	return result
}

func validMeasurement(value float64) CPUMeasurement { return CPUMeasurement{Value: value, Valid: true} }

func percentage(delta, total uint64) float64 {
	value := math.Round(float64(delta)/float64(total)*10000) / 100
	if value < 0 {
		return 0
	}
	if value > 100 {
		return 100
	}
	return value
}

func hasBinaries(snapshot CPUSnapshot, binaries []string) bool {
	for _, binary := range binaries {
		if _, _, ok := binaryCounters(snapshot, binary); !ok {
			return false
		}
	}
	return true
}

func processPair(current, previous CPUSnapshot, key string) (CPUProcessCounters, CPUProcessCounters, bool) {
	a, aok := current.Processes[key]
	b, bok := previous.Processes[key]
	return a, b, aok && bok
}

func selectedProcessPair(current, previous CPUSnapshot, key string) (CPUProcessCounters, CPUProcessCounters, bool) {
	if a, b, ok := processPair(current, previous, key); ok {
		return a, b, true
	}
	var currentCPU, previousCPU uint64
	var currentIDs, previousIDs []string
	for identity, process := range current.Processes {
		if process.Binary == key || (process.Binary == "" && (identity == key || len(current.Processes) == 1)) {
			currentCPU += process.CPU
			currentIDs = append(currentIDs, identity)
		}
	}
	for identity, process := range previous.Processes {
		if process.Binary == key || (process.Binary == "" && (identity == key || len(previous.Processes) == 1)) {
			previousCPU += process.CPU
			previousIDs = append(previousIDs, identity)
		}
	}
	if len(currentIDs) == 0 || len(previousIDs) == 0 {
		return CPUProcessCounters{}, CPUProcessCounters{}, false
	}
	sort.Strings(currentIDs)
	sort.Strings(previousIDs)
	return CPUProcessCounters{Identity: strings.Join(currentIDs, "\x00"), CPU: currentCPU, Binary: key}, CPUProcessCounters{Identity: strings.Join(previousIDs, "\x00"), CPU: previousCPU, Binary: key}, true
}

func binaryCounters(snapshot CPUSnapshot, binary string) (uint64, uint64, bool) {
	var total uint64
	found := false
	for key, process := range snapshot.Processes {
		if process.Binary == binary || (process.Binary == "" && key == binary) {
			total += process.CPU
			found = true
		}
	}
	return total, 0, found
}

func binaryPair(current, previous CPUSnapshot, binary string) (uint64, uint64, bool) {
	a, _, aok := binaryCounters(current, binary)
	b, _, bok := binaryCounters(previous, binary)
	return a, b, aok && bok
}

func groupedProcessPercent(current, previous CPUSnapshot, wall time.Duration, logicalCPUs int, binaries []string) map[string]float64 {
	result := make(map[string]float64)
	for _, binary := range binaries {
		if a, b, ok := binaryPair(current, previous, binary); ok && a >= b {
			result[binary] = cpuPercentage(a-b, wall, logicalCPUs, processCounterRate(current, previous))
		}
	}
	return result
}

func stableProcessBinaries(current, previous CPUSnapshot) []string {
	currentBinaries := processBinaries(current)
	result := make([]string, 0, len(currentBinaries))
	for _, binary := range currentBinaries {
		if stableBinaryMembership(current, previous, binary) {
			result = append(result, binary)
		}
	}
	return result
}

func stableBinaryMembership(current, previous CPUSnapshot, binary string) bool {
	currentIDs := binaryIdentities(current, binary)
	previousIDs := binaryIdentities(previous, binary)
	if len(currentIDs) == 0 || len(currentIDs) != len(previousIDs) {
		return false
	}
	for identity := range currentIDs {
		if !previousIDs[identity] {
			return false
		}
	}
	return true
}

func binaryIdentities(snapshot CPUSnapshot, binary string) map[string]bool {
	result := make(map[string]bool)
	for key, process := range snapshot.Processes {
		if process.Binary == binary || (process.Binary == "" && key == binary) {
			identity := process.Identity
			if identity == "" {
				identity = key
			}
			result[identity] = true
		}
	}
	return result
}

func processBinaries(snapshot CPUSnapshot) []string {
	seen := map[string]bool{}
	for _, process := range snapshot.Processes {
		if process.Binary != "" {
			seen[process.Binary] = true
		}
	}
	result := make([]string, 0, len(seen))
	for binary := range seen {
		result = append(result, binary)
	}
	sort.Strings(result)
	return result
}

func processKeys(snapshot CPUSnapshot) []string {
	keys := make([]string, 0, len(snapshot.Processes))
	for key := range snapshot.Processes {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func cloneCPUSnapshot(snapshot CPUSnapshot) *CPUSnapshot {
	clone := snapshot
	clone.Processes = make(map[string]CPUProcessCounters, len(snapshot.Processes))
	for key, process := range snapshot.Processes {
		clone.Processes[key] = process
	}
	return &clone
}

func processCounterRate(current, previous CPUSnapshot) uint64 {
	if current.ProcessTicksPerSecond > 0 {
		return current.ProcessTicksPerSecond
	}
	if previous.ProcessTicksPerSecond > 0 {
		return previous.ProcessTicksPerSecond
	}
	return clockTicksPerSec
}

func wallPercentage(delta uint64, duration time.Duration, ticksPerSecond uint64) float64 {
	return cpuPercentage(delta, duration, 1, ticksPerSecond)
}

func cpuPercentage(delta uint64, duration time.Duration, logicalCPUs int, ticksPerSecond uint64) float64 {
	if duration <= 0 || logicalCPUs <= 0 || ticksPerSecond == 0 {
		return 0
	}
	return math.Round(float64(delta)/float64(ticksPerSecond)/duration.Seconds()/float64(logicalCPUs)*10000) / 100
}
