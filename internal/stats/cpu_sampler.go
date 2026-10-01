package stats

import (
	"crypto/sha256"
	"fmt"
	"math"
	"sort"
	"strconv"
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
	// ProcessCensusIncomplete marks snapshots that retain the owned process
	// after a host-wide census failed or was partial. Those rows may support
	// owned-process measurements but must not be published as a complete host
	// process breakdown.
	ProcessCensusIncomplete bool
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
	Host                    CPUMeasurement
	Process                 CPUMeasurement
	Group                   CPUMeasurement
	ProcessCPUPercent       map[string]float64
	ProcessCensusIncomplete bool
	// OwnedProcessCPUPercent is the wall-clock CPU series for the process
	// identity selected by the collector. ProcessCPUPercent keeps the host-wide
	// top-process view; same-named yolomux-go processes are keyed by instance so
	// separate servers do not collapse into one chart series.
	OwnedProcessCPUPercent map[string]float64
	Reason                 CPUSampleReason
}

// CPUDeltaSampler compares cumulative counters with the previous accepted
// snapshot. Host CPU is normalized to host Total; wall-clock process CPU is
// normalized to the process counter rate and is not host-normalized.
type CPUDeltaSampler struct {
	processKey      string
	processIdentity string
	groupIdentity   string
	groupKeys       []string
	logicalCPUs     int
	wallProcess     bool
	previous        *CPUSnapshot
}

// clone preserves the pending delta boundary while a host collector decides
// whether the corresponding facts were durably appended. CPU samples are
// speculative until that append succeeds; retaining a private copy lets the
// collector replay the same interval after a transient writer failure.
func (s *CPUDeltaSampler) clone() *CPUDeltaSampler {
	if s == nil {
		return nil
	}
	copySampler := *s
	copySampler.groupKeys = append([]string(nil), s.groupKeys...)
	if s.previous != nil {
		copySampler.previous = cloneCPUSnapshot(*s.previous)
	}
	return &copySampler
}

func (s *CPUDeltaSampler) restore(snapshot *CPUDeltaSampler) {
	if s == nil || snapshot == nil {
		return
	}
	*s = *snapshot
	s.groupKeys = append([]string(nil), snapshot.groupKeys...)
	if snapshot.previous != nil {
		s.previous = cloneCPUSnapshot(*snapshot.previous)
	}
}

const maxProcessCPUSeries = 4

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
	result.ProcessCensusIncomplete = snapshot.ProcessCensusIncomplete || previous.ProcessCensusIncomplete
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

	process, previousProcess, ok := s.selectedProcessPair(snapshot, *previous)
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
	ticksPerSecond := processCounterRate(snapshot, *previous)
	if s.wallProcess && ticksPerSecond == 0 {
		result.Reason = CPUUnavailable
		return result
	}
	processPercent := percentage(process.CPU-previousProcess.CPU, hostDelta)
	if s.wallProcess {
		processPercent = wallPercentage(process.CPU-previousProcess.CPU, wallDuration, ticksPerSecond)
	}
	result.Process = validMeasurement(processPercent)
	if s.processIdentity != "" {
		binary := process.Binary
		if binary == "" {
			binary = s.processKey
		}
		if binary != "" {
			result.OwnedProcessCPUPercent = map[string]float64{binary: processPercent}
		}
	}
	if result.ProcessCensusIncomplete {
		result.Reason = CPUValid
		return result
	}

	groupKeys := s.groupKeys
	autoGroups := len(groupKeys) == 0
	if autoGroups {
		groupKeys = stableProcessBinaries(snapshot, *previous)
	}
	if s.hasBinaries(snapshot, groupKeys) && s.hasBinaries(*previous, groupKeys) {
		var delta uint64
		for _, binary := range groupKeys {
			current, old, ok := s.binaryPair(snapshot, *previous, binary)
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
			result.Group = validMeasurement(cpuPercentage(delta, wallDuration, s.logicalCPUs, ticksPerSecond))
			result.ProcessCPUPercent = s.groupedProcessPercent(snapshot, *previous, wallDuration, s.logicalCPUs, groupKeys)
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

func (s *CPUDeltaSampler) ownedIdentity(identity string) bool {
	if s.processIdentity == "" {
		return true
	}
	if identity == s.processIdentity {
		return true
	}
	return strings.HasSuffix(s.processIdentity, ":") && strings.HasPrefix(identity, s.processIdentity)
}

func (s *CPUDeltaSampler) selectedProcessPair(current, previous CPUSnapshot) (CPUProcessCounters, CPUProcessCounters, bool) {
	if s.processIdentity == "" {
		return selectedProcessPair(current, previous, s.processKey)
	}
	var currentProcess, previousProcess CPUProcessCounters
	var currentOK, previousOK bool
	for key, process := range current.Processes {
		identity := process.Identity
		if identity == "" {
			identity = key
		}
		if s.ownedIdentity(identity) && process.Binary == s.processKey {
			currentProcess, currentOK = process, true
			break
		}
	}
	for key, process := range previous.Processes {
		identity := process.Identity
		if identity == "" {
			identity = key
		}
		if s.ownedIdentity(identity) && process.Binary == s.processKey {
			previousProcess, previousOK = process, true
			break
		}
	}
	return currentProcess, previousProcess, currentOK && previousOK
}

func (s *CPUDeltaSampler) hasBinaries(snapshot CPUSnapshot, binaries []string) bool {
	for _, binary := range binaries {
		if _, _, ok := s.groupBinaryPair(snapshot, snapshot, binary); !ok {
			return false
		}
	}
	return true
}

func (s *CPUDeltaSampler) binaryPair(current, previous CPUSnapshot, binary string) (uint64, uint64, bool) {
	return s.groupBinaryPair(current, previous, binary)
}

func (s *CPUDeltaSampler) groupOwnedIdentity(identity string) bool {
	if s.groupIdentity == "" {
		return true
	}
	if identity == s.groupIdentity {
		return true
	}
	return strings.HasSuffix(s.groupIdentity, ":") && strings.HasPrefix(identity, s.groupIdentity)
}

func (s *CPUDeltaSampler) groupBinaryPair(current, previous CPUSnapshot, binary string) (uint64, uint64, bool) {
	if s.groupIdentity == "" {
		return binaryPair(current, previous, binary)
	}
	var currentValue, previousValue uint64
	var currentOK, previousOK bool
	for key, process := range current.Processes {
		identity := process.Identity
		if identity == "" {
			identity = key
		}
		if s.groupOwnedIdentity(identity) && process.Binary == binary {
			currentValue, currentOK = process.CPU, true
			break
		}
	}
	for key, process := range previous.Processes {
		identity := process.Identity
		if identity == "" {
			identity = key
		}
		if s.groupOwnedIdentity(identity) && process.Binary == binary {
			previousValue, previousOK = process.CPU, true
			break
		}
	}
	return currentValue, previousValue, currentOK && previousOK
}

func (s *CPUDeltaSampler) groupedProcessPercent(current, previous CPUSnapshot, wall time.Duration, logicalCPUs int, binaries []string) map[string]float64 {
	if s.groupIdentity == "" {
		return groupedProcessPercent(current, previous, wall, logicalCPUs, binaries)
	}
	values := make(map[string]float64)
	for _, binary := range binaries {
		if a, b, ok := s.binaryPair(current, previous, binary); ok && a >= b {
			values[binary] = cpuPercentage(a-b, wall, logicalCPUs, processCounterRate(current, previous))
		}
	}
	result := make(map[string]float64, len(values))
	for binary, value := range values {
		result[binary] = math.Min(100, value)
	}
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
	values := make(map[string]float64)
	for _, binary := range binaries {
		if isYoloMuxGoBinary(binary) {
			currentRows, previousRows := binaryProcessRows(current, binary), binaryProcessRows(previous, binary)
			if len(currentRows) == 0 || len(currentRows) != len(previousRows) {
				continue
			}
			stable := true
			for identity, process := range currentRows {
				old, ok := previousRows[identity]
				if !ok || process.CPU < old.CPU {
					stable = false
					break
				}
			}
			if !stable {
				continue
			}
			for identity, process := range currentRows {
				old := previousRows[identity]
				values[hostProcessInstanceSeriesKey(binary, identity)] = cpuPercentage(process.CPU-old.CPU, wall, logicalCPUs, processCounterRate(current, previous))
			}
			continue
		}
		if a, b, ok := binaryPair(current, previous, binary); ok && a >= b {
			values[binary] = cpuPercentage(a-b, wall, logicalCPUs, processCounterRate(current, previous))
		}
	}
	// The Python collector keeps only the four largest binary series. Apply the
	// same deterministic bound here so the current-family writer contract cannot
	// reject an otherwise valid host sample on a busy machine with many binaries.
	ranked := make([]string, 0, len(values))
	for binary := range values {
		ranked = append(ranked, binary)
	}
	sort.Slice(ranked, func(i, j int) bool {
		if values[ranked[i]] != values[ranked[j]] {
			return values[ranked[i]] > values[ranked[j]]
		}
		return ranked[i] < ranked[j]
	})
	if len(ranked) > maxProcessCPUSeries {
		ranked = ranked[:maxProcessCPUSeries]
	}
	result := make(map[string]float64, len(ranked))
	for _, binary := range ranked {
		result[binary] = math.Min(100, values[binary])
	}
	return result
}

func binaryProcessRows(snapshot CPUSnapshot, binary string) map[string]CPUProcessCounters {
	rows := make(map[string]CPUProcessCounters)
	for key, process := range snapshot.Processes {
		if process.Binary != binary {
			continue
		}
		identity := process.Identity
		if identity == "" {
			identity = key
		}
		rows[identity] = process
	}
	return rows
}

func isYoloMuxGoBinary(binary string) bool {
	return binary == "yomux-go" || binary == "yolomux-go" || binary == "yolomux_go"
}

func hostProcessInstanceSeriesKey(binary, identity string) string {
	pidText, _, _ := strings.Cut(identity, ":")
	if _, err := strconv.ParseUint(pidText, 10, 64); err != nil {
		return binary
	}
	digest := sha256.Sum256([]byte(identity))
	return fmt.Sprintf("%s-pid-%s-%x", binary, pidText, digest[:4])
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
	return 0
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
