package stats

import (
	"crypto/sha256"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"math"
	"path"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

const (
	maxHostInputBytes = 1 << 20
	maxProcessEntries = 32768
	pageSizeBytes     = 4096
	clockTicksPerSec  = 100
)

// HostFactContext is the common timing and ownership context for one host poll.
type HostFactContext struct {
	EpochID         string
	EpochStartedAt  float64
	ObservedAt      float64
	CadenceSeconds  float64
	OwnerGeneration uint64
	SourceID        string
}

// MacMemoryDetails contains optional macOS counters. Nil values are omitted.
type MacMemoryDetails map[string]any

// BuildCPUFacts converts a measured CPU delta into current YO!stats facts.
// A non-valid sample produces an unavailable span rather than a fabricated zero.
func BuildCPUFacts(sample CPUSample, processCPUPercent map[string]float64, context HostFactContext) AppendBatch {
	if sample.Reason != CPUValid || !sample.Process.Valid || !sample.Host.Valid || !finite(sample.Process.Value) || !finite(sample.Host.Value) {
		reason := string(sample.Reason)
		if reason == "" {
			reason = "missing_sample"
		}
		return BuildUnavailableFacts("cpu", context, reason)
	}
	payload := map[string]any{
		"process_percent": sample.Process.Value,
		"system_percent":  sample.Host.Value,
	}
	if processCPUPercent != nil {
		series := make(map[string]any, len(processCPUPercent))
		for binary, value := range processCPUPercent {
			if !finite(value) {
				continue
			}
			series[binary] = value
		}
		payload["process_cpu_percent"] = series
	}
	return singleHostFacts("cpu", context, payload)
}

// BuildGPUFacts converts valid parsed devices into one observation per device.
func BuildGPUFacts(devices []GPUDevice, context HostFactContext) AppendBatch {
	if len(devices) == 0 {
		return BuildUnavailableFacts("gpu", context, "missing_sample")
	}
	result := AppendBatch{}
	for _, device := range devices {
		if strings.TrimSpace(device.SourceID) == "" || strings.TrimSpace(device.Label) == "" || !finite(device.Utilization) {
			continue
		}
		itemContext := context
		itemContext.SourceID = device.SourceID
		item := singleHostFacts("gpu", itemContext, map[string]any{
			"util_percent":          device.Utilization,
			"memory_used_bytes":     device.MemoryUsedBytes,
			"memory_capacity_bytes": device.MemoryTotalBytes,
			"label":                 device.Label,
		})
		result.Observations = append(result.Observations, item.Observations...)
		result.CoverageEpochs = append(result.CoverageEpochs, item.CoverageEpochs...)
	}
	if len(result.Observations) == 0 {
		return BuildUnavailableFacts("gpu", context, "invalid_sample")
	}
	return result
}

// BuildSystemMemoryFacts converts memory counters and optional process census data.
func BuildSystemMemoryFacts(memory MemoryCounters, rows []ProcessCensusRow, mac MacMemoryDetails, context HostFactContext) AppendBatch {
	if memory.CapacityBytes == 0 || memory.UsedBytes > memory.CapacityBytes {
		return BuildUnavailableFacts("system_memory", context, "invalid_sample")
	}
	payload := map[string]any{"used_bytes": memory.UsedBytes, "capacity_bytes": memory.CapacityBytes}
	if len(rows) > 0 {
		processMemory := map[string]any{}
		for _, row := range topProcessMemory(rows) {
			if row.Binary != "" && row.RSSBytes > 0 {
				processMemory[row.Binary] = row.RSSBytes
			}
		}
		if len(processMemory) > 0 {
			payload["process_memory_bytes"] = processMemory
		}
	}
	for key, value := range mac {
		if value != nil {
			payload["mac_"+key] = value
		}
	}
	return singleHostFacts("system_memory", context, payload)
}

// BuildUnavailableFacts records the next cadence as unknown data.
func BuildUnavailableFacts(family string, context HostFactContext, reason string) AppendBatch {
	if !finite(context.CadenceSeconds) || context.CadenceSeconds <= 0 || context.ObservedAt < context.EpochStartedAt {
		return AppendBatch{}
	}
	if !validFactContext(context) {
		return AppendBatch{}
	}
	return AppendBatch{UnavailableSpans: []UnavailableSpan{{
		Family: family, SourceID: context.SourceID, EpochID: context.EpochID,
		StartedAt: context.ObservedAt, EndedAt: context.ObservedAt + context.CadenceSeconds,
		NativeCadenceSeconds: context.CadenceSeconds, Reason: reason,
		OwnerGeneration: context.OwnerGeneration,
	}}}
}

func validHostCadence(family string, cadence float64) bool {
	if !finite(cadence) || cadence <= 0 {
		return false
	}
	return family != ""
}

func singleHostFacts(family string, context HostFactContext, payload map[string]any) AppendBatch {
	if !validFactContext(context) {
		return AppendBatch{}
	}
	encoded, err := json.Marshal(payload)
	if err != nil || len(encoded) > MaxPayloadBytes {
		return BuildUnavailableFacts(family, context, "payload_too_large")
	}
	eventID := hostEventID(family, context.SourceID, context.EpochID, context.ObservedAt, "sample")
	return AppendBatch{
		Observations: []Observation{{EventID: eventID, Family: family, SourceID: context.SourceID,
			ObservedAt: context.ObservedAt, EpochID: context.EpochID, OwnerGeneration: context.OwnerGeneration, Payload: payload}},
		CoverageEpochs: []CoverageEpoch{{Family: family, SourceID: context.SourceID, EpochID: context.EpochID,
			StartedAt: context.EpochStartedAt, EndedAt: floatPointer(context.ObservedAt + context.CadenceSeconds),
			NativeCadenceSeconds: context.CadenceSeconds, OwnerGeneration: context.OwnerGeneration}},
	}
}

func validFactContext(context HostFactContext) bool {
	if strings.TrimSpace(context.EpochID) == "" || strings.TrimSpace(context.SourceID) == "" ||
		!finite(context.EpochStartedAt) || !finite(context.ObservedAt) ||
		!finite(context.CadenceSeconds) || context.CadenceSeconds <= 0 {
		return false
	}
	end := context.ObservedAt + context.CadenceSeconds
	return finite(end) && end >= context.ObservedAt
}

func hostEventID(family, source, epoch string, observed float64, eventKey string) string {
	parts := []string{family, source, epoch, pythonFloatHex(observed), eventKey}
	encoded := make([]byte, 0)
	for _, part := range parts {
		encoded = append(encoded, []byte(strconv.Itoa(len(part)))...)
		encoded = append(encoded, ':')
		encoded = append(encoded, part...)
	}
	return fmt.Sprintf("obs-%s-%x", family, sha256.Sum256(encoded))
}

func pythonFloatHex(value float64) string {
	bits := math.Float64bits(value)
	if value == 0 && bits>>63 == 0 {
		return "0x0.0p+0"
	}
	if value == 0 {
		return "-0x0.0p+0"
	}
	sign := ""
	if bits>>63 != 0 {
		sign, value = "-", -value
	}
	bits = math.Float64bits(value)
	exponent := int((bits >> 52) & 0x7ff)
	mantissa := bits & ((uint64(1) << 52) - 1)
	if exponent == 0 {
		exponent = -1022
	} else {
		mantissa |= uint64(1) << 52
		exponent -= 1023
	}
	return fmt.Sprintf("%s0x1.%013xp%+d", sign, mantissa&((uint64(1)<<52)-1), exponent)
}

func floatPointer(value float64) *float64 { return &value }

// HostCollectorInputs keeps platform collection testable. Production callers may
// use the OS adapters below; parsers themselves never perform I/O.
type HostCollectorInputs struct {
	ReadFile  func(string) ([]byte, error)
	ReadDir   func(string) ([]string, error)
	ReadLink  func(string) (string, error)
	Run       func([]string) (CommandOutput, error)
	PageSize  uint64
	ClockTick uint64
}

type CommandOutput struct {
	Stdout   []byte
	Stderr   []byte
	ExitCode int
}

func CollectGPU(inputs HostCollectorInputs) ([]GPUDevice, bool) {
	if inputs.Run == nil {
		return nil, false
	}
	result, err := inputs.Run([]string{"gpu-info", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"})
	if err != nil || result.ExitCode != 0 || len(result.Stdout) > maxHostInputBytes {
		return nil, false
	}
	devices := ParseGPUCSV(string(result.Stdout))
	return devices, len(devices) > 0
}

func CollectIOAccelerator(inputs HostCollectorInputs) ([]GPUDevice, bool) {
	if inputs.Run == nil {
		return nil, false
	}
	result, err := inputs.Run([]string{"ioreg", "-a", "-r", "-d1", "-w0", "-c", "IOAccelerator"})
	if err != nil || result.ExitCode != 0 || len(result.Stdout) > maxHostInputBytes {
		return nil, false
	}
	devices := ParseIOAccelerator(result.Stdout)
	return devices, len(devices) > 0
}

func CollectSystemProfiler(inputs HostCollectorInputs) (SystemMetadata, bool) {
	if inputs.Run == nil {
		return SystemMetadata{}, false
	}
	result, err := inputs.Run([]string{"system_profiler", "-json", "SPHardwareDataType", "SPMemoryDataType", "SPDisplaysDataType"})
	if err != nil || result.ExitCode != 0 || len(result.Stdout) > maxHostInputBytes {
		return SystemMetadata{}, false
	}
	metadata := ParseSystemProfilerMetadata(result.Stdout)
	return metadata, metadata != (SystemMetadata{})
}

type ProcessCensusRow struct {
	PID        int
	Identity   string
	Binary     string
	CPUSeconds float64
	RSSBytes   uint64
}

type MemoryCounters struct {
	UsedBytes     uint64
	CapacityBytes uint64
}

type GPUDevice struct {
	SourceID         string
	Label            string
	Utilization      float64
	MemoryUsedBytes  uint64
	MemoryTotalBytes uint64
}

type SystemMetadata struct {
	CPULabel          string
	GPULabel          string
	SystemMemoryLabel string
}

func ParseLinuxCPU(text string) (CPUHostCounters, bool) {
	for _, line := range strings.Split(text, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 || fields[0] != "cpu" {
			continue
		}
		var total, idle uint64
		for i, field := range fields[1:] {
			value, err := strconv.ParseUint(field, 10, 64)
			if err != nil || total > ^uint64(0)-value {
				return CPUHostCounters{}, false
			}
			total += value
			if i == 3 || i == 4 {
				if idle > ^uint64(0)-value {
					return CPUHostCounters{}, false
				}
				idle += value
			}
		}
		if total == 0 || idle > total {
			return CPUHostCounters{}, false
		}
		return CPUHostCounters{Total: total, Busy: total - idle}, true
	}
	return CPUHostCounters{}, false
}

func ParseLinuxProcessStat(text, executable string, pageSize, clockTicks uint64) (ProcessCensusRow, bool) {
	if pageSize == 0 {
		pageSize = pageSizeBytes
	}
	if clockTicks == 0 {
		clockTicks = clockTicksPerSec
	}
	close := strings.LastIndex(text, ") ")
	open := strings.Index(text, " (")
	if open < 1 || close < open {
		return ProcessCensusRow{}, false
	}
	pid, err := strconv.Atoi(strings.TrimSpace(text[:open]))
	if err != nil {
		return ProcessCensusRow{}, false
	}
	comm := text[open+2 : close]
	fields := strings.Fields(text[close+2:])
	if len(fields) <= 21 {
		return ProcessCensusRow{}, false
	}
	utime, e1 := strconv.ParseUint(fields[11], 10, 64)
	stime, e2 := strconv.ParseUint(fields[12], 10, 64)
	start, e3 := strconv.ParseUint(fields[19], 10, 64)
	rss, e4 := strconv.ParseInt(fields[21], 10, 64)
	if e1 != nil || e2 != nil || e3 != nil || e4 != nil || rss < 0 {
		return ProcessCensusRow{}, false
	}
	binary := normalizeHostBinary(executable)
	if binary == "" {
		binary = normalizeHostBinary(comm)
	}
	if binary == "" {
		return ProcessCensusRow{}, false
	}
	if pageSize != 0 && uint64(rss) > ^uint64(0)/pageSize || utime > ^uint64(0)-stime {
		return ProcessCensusRow{}, false
	}
	cpu := float64(utime+stime) / float64(clockTicks)
	if !finite(cpu) {
		return ProcessCensusRow{}, false
	}
	return ProcessCensusRow{PID: pid, Identity: fmt.Sprintf("%d:%d", pid, start), Binary: binary, CPUSeconds: cpu, RSSBytes: uint64(rss) * pageSize}, true
}

func LinuxProcessCensus(inputs HostCollectorInputs, procRoot string) ([]ProcessCensusRow, bool) {
	if inputs.ReadDir == nil || inputs.ReadFile == nil {
		return nil, false
	}
	if procRoot == "" {
		procRoot = "/proc"
	}
	names, err := inputs.ReadDir(procRoot)
	if err != nil {
		return nil, false
	}
	if len(names) > maxProcessEntries {
		return nil, false
	}
	sort.Strings(names)
	rows := make([]ProcessCensusRow, 0, len(names))
	for _, name := range names {
		if _, err := strconv.Atoi(name); err != nil {
			continue
		}
		data, err := boundedRead(inputs.ReadFile, path.Join(procRoot, name, "stat"))
		if err != nil {
			continue
		}
		executable := ""
		if inputs.ReadLink != nil {
			executable, _ = inputs.ReadLink(path.Join(procRoot, name, "exe"))
		}
		if row, ok := ParseLinuxProcessStat(string(data), executable, inputs.PageSize, inputs.ClockTick); ok {
			rows = append(rows, row)
		}
	}
	if len(names) > 0 && len(rows) == 0 {
		return nil, false
	}
	return rows, true
}

func ParseLinuxMeminfo(text string) (MemoryCounters, bool) {
	values := map[string]uint64{}
	for _, line := range strings.Split(text, "\n") {
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		fields := strings.Fields(parts[1])
		if len(fields) == 0 {
			continue
		}
		value, err := strconv.ParseUint(fields[0], 10, 64)
		if err != nil {
			continue
		}
		multiplier := uint64(1)
		if len(fields) > 1 {
			switch strings.ToLower(fields[1]) {
			case "kb":
				multiplier = 1024
			case "mb":
				multiplier = 1024 * 1024
			}
		}
		if value > ^uint64(0)/multiplier {
			continue
		}
		values[strings.TrimSpace(parts[0])] = value * multiplier
	}
	total, okTotal := values["MemTotal"]
	available, okAvailable := values["MemAvailable"]
	if !okAvailable {
		available, okAvailable = values["MemFree"]
	}
	if !okTotal || !okAvailable || total == 0 || available > total {
		return MemoryCounters{}, false
	}
	return MemoryCounters{CapacityBytes: total, UsedBytes: total - available}, true
}

func ParseGPUCSV(text string) []GPUDevice {
	devices := []GPUDevice{}
	for _, line := range strings.Split(text, "\n") {
		parts := strings.Split(line, ",")
		if len(parts) != 5 {
			continue
		}
		index, e1 := strconv.Atoi(strings.TrimSpace(parts[0]))
		util, e2 := strconv.ParseFloat(strings.TrimSpace(parts[2]), 64)
		used, e3 := strconv.ParseFloat(strings.TrimSpace(parts[3]), 64)
		total, e4 := strconv.ParseFloat(strings.TrimSpace(parts[4]), 64)
		if e1 != nil || e2 != nil || e3 != nil || e4 != nil || index < 0 || !finite(util) || !finite(used) || !finite(total) {
			continue
		}
		if util < 0 {
			util = 0
		}
		if util > 100 {
			util = 100
		}
		if used < 0 {
			used = 0
		}
		if total < 0 {
			total = 0
		}
		devices = append(devices, GPUDevice{SourceID: fmt.Sprintf("gpu:%d", index), Label: gpuLabel(index, strings.TrimSpace(parts[1])), Utilization: util, MemoryUsedBytes: mibBytes(used), MemoryTotalBytes: mibBytes(total)})
	}
	return devices
}

func ParseIOAcceleratorJSON(data []byte) []GPUDevice {
	var rows []map[string]any
	if json.Unmarshal(data, &rows) != nil {
		return nil
	}
	devices := []GPUDevice{}
	for index, row := range rows {
		stats, ok := row["PerformanceStatistics"].(map[string]any)
		if !ok {
			continue
		}
		value, ok := firstNumber(stats, "Device Utilization %", "GPU Activity(%)", "GPU Activity")
		if !ok {
			continue
		}
		used, _ := firstNumber(stats, "In use system memory", "In use video memory")
		if !finite(value) || !finite(used) {
			continue
		}
		if value < 0 {
			value = 0
		}
		if value > 100 {
			value = 100
		}
		if used < 0 {
			used = 0
		}
		devices = append(devices, GPUDevice{SourceID: fmt.Sprintf("gpu:%d", index), Label: fmt.Sprintf("GPU %d", index), Utilization: value, MemoryUsedBytes: uint64(used)})
	}
	return devices
}

// ParseIOAccelerator accepts the XML plist emitted by ioreg -a and the JSON
// fixture format used by older callers.
func ParseIOAccelerator(data []byte) []GPUDevice {
	trimmed := strings.TrimSpace(string(data))
	if strings.HasPrefix(trimmed, "{") || strings.HasPrefix(trimmed, "[") {
		return ParseIOAcceleratorJSON(data)
	}
	value, ok := parsePlist(data)
	if !ok {
		return nil
	}
	rows, ok := value.([]any)
	if !ok {
		return nil
	}
	devices := []GPUDevice{}
	for index, item := range rows {
		row, ok := item.(map[string]any)
		if !ok {
			continue
		}
		stats, ok := row["PerformanceStatistics"].(map[string]any)
		if !ok {
			continue
		}
		value, vok := plistNumber(stats, "Device Utilization %", "GPU Activity(%)", "GPU Activity")
		used, _ := plistNumber(stats, "In use system memory", "In use video memory")
		if !vok || !finite(value) || !finite(used) {
			continue
		}
		devices = append(devices, GPUDevice{SourceID: fmt.Sprintf("gpu:%d", index), Label: fmt.Sprintf("GPU %d", index), Utilization: clamp(value), MemoryUsedBytes: nonNegativeBytes(used)})
	}
	return devices
}

func clamp(value float64) float64 {
	if value < 0 {
		return 0
	}
	if value > 100 {
		return 100
	}
	return value
}
func nonNegativeBytes(value float64) uint64 {
	if !finite(value) || value <= 0 || value >= float64(^uint64(0)) {
		return 0
	}
	return uint64(value)
}
func topProcessMemory(rows []ProcessCensusRow) []ProcessCensusRow {
	totals := make(map[string]uint64, len(rows))
	for _, row := range rows {
		binary := normalizeHostBinary(row.Binary)
		if binary != "" && row.RSSBytes > 0 {
			totals[binary] += row.RSSBytes
		}
	}
	result := make([]ProcessCensusRow, 0, len(totals))
	for binary, rss := range totals {
		result = append(result, ProcessCensusRow{Binary: binary, RSSBytes: rss})
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].RSSBytes != result[j].RSSBytes {
			return result[i].RSSBytes > result[j].RSSBytes
		}
		return result[i].Binary < result[j].Binary
	})
	if len(result) > 5 {
		result = result[:5]
	}
	return result
}

func plistNumber(m map[string]any, keys ...string) (float64, bool) {
	for _, key := range keys {
		if value, ok := m[key]; ok {
			switch value := value.(type) {
			case float64:
				return value, true
			case int64:
				return float64(value), true
			case string:
				parsed, err := strconv.ParseFloat(strings.TrimSpace(value), 64)
				if err == nil {
					return parsed, true
				}
			}
		}
	}
	return 0, false
}

func parsePlist(data []byte) (any, bool) {
	decoder := xml.NewDecoder(strings.NewReader(string(data)))
	for {
		token, err := decoder.Token()
		if err != nil {
			return nil, false
		}
		if start, ok := token.(xml.StartElement); ok && start.Name.Local != "plist" {
			value, err := parsePlistElement(decoder, start)
			return value, err == nil
		}
	}
}
func parsePlistElement(decoder *xml.Decoder, start xml.StartElement) (any, error) {
	switch start.Name.Local {
	case "dict":
		result := map[string]any{}
		var key string
		for {
			token, err := decoder.Token()
			if err != nil {
				return nil, err
			}
			switch token := token.(type) {
			case xml.StartElement:
				value, err := parsePlistElement(decoder, token)
				if err != nil {
					return nil, err
				}
				if token.Name.Local == "key" {
					key, _ = value.(string)
				} else if key != "" {
					result[key] = value
					key = ""
				}
			case xml.EndElement:
				if token.Name == start.Name {
					return result, nil
				}
			}
		}
	case "array":
		result := []any{}
		for {
			token, err := decoder.Token()
			if err != nil {
				return nil, err
			}
			switch token := token.(type) {
			case xml.StartElement:
				value, err := parsePlistElement(decoder, token)
				if err != nil {
					return nil, err
				}
				result = append(result, value)
			case xml.EndElement:
				if token.Name == start.Name {
					return result, nil
				}
			}
		}
	case "key", "string":
		var value string
		if err := decoder.DecodeElement(&value, &start); err != nil {
			return nil, err
		}
		return value, nil
	case "real":
		var value string
		if err := decoder.DecodeElement(&value, &start); err != nil {
			return nil, err
		}
		return strconv.ParseFloat(strings.TrimSpace(value), 64)
	case "integer":
		var value string
		if err := decoder.DecodeElement(&value, &start); err != nil {
			return nil, err
		}
		return strconv.ParseInt(strings.TrimSpace(value), 10, 64)
	case "true":
		return true, skipPlistElement(decoder, start)
	case "false":
		return false, skipPlistElement(decoder, start)
	}
	return nil, skipPlistElement(decoder, start)
}
func skipPlistElement(decoder *xml.Decoder, start xml.StartElement) error {
	for {
		token, err := decoder.Token()
		if err != nil {
			return err
		}
		if end, ok := token.(xml.EndElement); ok && end.Name == start.Name {
			return nil
		}
	}
}

func ParseSystemProfilerMetadata(data []byte) SystemMetadata {
	var payload map[string]any
	if json.Unmarshal(data, &payload) != nil {
		return SystemMetadata{}
	}
	hardware := firstObject(payload, "SPHardwareDataType")
	memory := firstObject(payload, "SPMemoryDataType")
	display := firstObject(payload, "SPDisplaysDataType")
	chip := firstString(hardware, "chip_type")
	if chip == "" {
		chip = firstString(display, "sppci_model")
	}
	processors := firstString(hardware, "number_processors")
	digits := regexp.MustCompile(`\d+`).FindAllString(processors, -1)
	cpu := chip
	if len(digits) >= 3 {
		cpu = fmt.Sprintf("%s · %s cores (%s performance + %s efficiency)", chip, digits[0], digits[1], digits[2])
	} else if len(digits) > 0 {
		cpu = strings.Trim(strings.Join([]string{chip, digits[0] + " cores"}, " · "), " ·")
	}
	result := SystemMetadata{CPULabel: cpu, GPULabel: firstString(display, "sppci_model")}
	if result.GPULabel == "" {
		result.GPULabel = chip
	}
	if kind := firstString(memory, "dimm_type"); kind != "" {
		result.SystemMemoryLabel = kind + " unified memory"
	}
	return result
}

func boundedRead(read func(string) ([]byte, error), name string) ([]byte, error) {
	data, err := read(name)
	if err != nil {
		return nil, err
	}
	if len(data) > maxHostInputBytes {
		return nil, errors.New("host input exceeds bound")
	}
	return data, nil
}
func mibBytes(value float64) uint64 {
	if !finite(value) || value <= 0 || value >= float64(^uint64(0))/(1024*1024) {
		return 0
	}
	return uint64(value * 1024 * 1024)
}
func gpuLabel(index int, name string) string {
	if name == "" {
		return fmt.Sprintf("GPU %d", index)
	}
	return fmt.Sprintf("GPU %d (%s)", index, name)
}
func firstNumber(m map[string]any, keys ...string) (float64, bool) {
	for _, key := range keys {
		switch value := m[key].(type) {
		case float64:
			return value, true
		case string:
			if parsed, err := strconv.ParseFloat(strings.TrimSpace(value), 64); err == nil {
				return parsed, true
			}
		}
	}
	return 0, false
}
func firstString(m map[string]any, key string) string {
	if value, ok := m[key].(string); ok {
		return strings.TrimSpace(value)
	}
	return ""
}
func firstObject(payload map[string]any, key string) map[string]any {
	if rows, ok := payload[key].([]any); ok && len(rows) > 0 {
		if row, ok := rows[0].(map[string]any); ok {
			return row
		}
	}
	return nil
}

var binarySafe = regexp.MustCompile(`[^a-z0-9._+-]+`)
var pythonBinary = regexp.MustCompile(`(?i)^python(?:\d+(?:\.\d+)*)?$`)
var versionBinary = regexp.MustCompile(`^\d+(?:\.\d+){1,3}$`)

func normalizeHostBinary(value string) string {
	name := path.Base(strings.TrimSuffix(strings.TrimSpace(value), " (deleted)"))
	if name == "" {
		return ""
	}
	if versionBinary.MatchString(name) {
		parts := strings.Split(strings.TrimSuffix(strings.TrimSpace(value), " (deleted)"), "/")
		for index, part := range parts {
			if part == "versions" && index > 0 {
				return normalizeHostBinary(parts[index-1])
			}
		}
	}
	if pythonBinary.MatchString(name) {
		return "python"
	}
	if strings.EqualFold(name, "nodejs") {
		return "node"
	}
	safe := strings.Trim(binarySafe.ReplaceAllString(strings.ToLower(name), "-"), "-._")
	if safe == "" {
		return ""
	}
	if safe == name && len(safe) <= 64 {
		return safe
	}
	digest := fmt.Sprintf("%x", sha256.Sum256([]byte(name)))[:8]
	prefix := strings.TrimRight(safe[:minInt(len(safe), 55)], "-._")
	if prefix == "" {
		prefix = "binary"
	}
	return prefix + "-" + digest
}
func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
