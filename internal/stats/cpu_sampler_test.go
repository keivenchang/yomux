package stats

import (
	"reflect"
	"testing"
	"time"
)

func cpuSnapshot(busy, total uint64, processes ...struct {
	key, identity string
	cpu           uint64
}) CPUSnapshot {
	items := make(map[string]CPUProcessCounters, len(processes))
	for _, process := range processes {
		items[process.key] = CPUProcessCounters{Identity: process.identity, CPU: process.cpu}
	}
	return CPUSnapshot{Host: CPUHostCounters{Busy: busy, Total: total}, Processes: items}
}

func TestCPUDeltaSamplerScenarios(t *testing.T) {
	base := cpuSnapshot(50, 100, struct {
		key, identity string
		cpu           uint64
	}{"a", "pid-a", 10}, struct {
		key, identity string
		cpu           uint64
	}{"b", "pid-b", 20})
	cases := []struct {
		name       string
		group      []string
		duration   time.Duration
		next       CPUSnapshot
		reason     CPUSampleReason
		hostValid  bool
		procValid  bool
		groupValid bool
		hostValue  float64
		procValue  float64
		groupValue float64
	}{
		{name: "first sample", group: []string{"a", "b"}, duration: time.Second, next: base, reason: CPUFirstSample},
		{name: "valid deltas", group: []string{"b", "a"}, duration: time.Second, next: cpuSnapshot(70, 140, struct {
			key, identity string
			cpu           uint64
		}{"a", "pid-a", 25}, struct {
			key, identity string
			cpu           uint64
		}{"b", "pid-b", 35}), reason: CPUValid, hostValid: true, procValid: true, groupValid: true, hostValue: 50, procValue: 37.5, groupValue: 75},
		{name: "host reset", group: []string{"a", "b"}, duration: time.Second, next: cpuSnapshot(40, 90, struct {
			key, identity string
			cpu           uint64
		}{"a", "pid-a", 11}, struct {
			key, identity string
			cpu           uint64
		}{"b", "pid-b", 21}), reason: CPUHostCounterReset},
		{name: "process restart", group: []string{"a", "b"}, duration: time.Second, next: cpuSnapshot(70, 140, struct {
			key, identity string
			cpu           uint64
		}{"a", "pid-new", 25}, struct {
			key, identity string
			cpu           uint64
		}{"b", "pid-b", 35}), reason: CPUProcessRestart, hostValid: true, hostValue: 50},
		{name: "changing group membership", group: []string{"a", "b"}, duration: time.Second, next: cpuSnapshot(70, 140, struct {
			key, identity string
			cpu           uint64
		}{"a", "pid-a", 25}), reason: CPUGroupMembershipChange, hostValid: true, procValid: true, hostValue: 50, procValue: 37.5},
		{name: "zero duration", group: []string{"a", "b"}, duration: 0, next: cpuSnapshot(70, 140, struct {
			key, identity string
			cpu           uint64
		}{"a", "pid-a", 25}, struct {
			key, identity string
			cpu           uint64
		}{"b", "pid-b", 35}), reason: CPUInvalidDuration},
		{name: "negative duration", group: []string{"a", "b"}, duration: -time.Second, next: cpuSnapshot(70, 140, struct {
			key, identity string
			cpu           uint64
		}{"a", "pid-a", 25}, struct {
			key, identity string
			cpu           uint64
		}{"b", "pid-b", 35}), reason: CPUInvalidDuration},
		{name: "clamped", group: []string{"a"}, duration: time.Second, next: cpuSnapshot(200, 200, struct {
			key, identity string
			cpu           uint64
		}{"a", "pid-a", 200}), reason: CPUValid, hostValid: true, procValid: true, groupValid: true, hostValue: 100, procValue: 100, groupValue: 100},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			sampler := NewCPUDeltaSampler("a", tc.group)
			if tc.name != "first sample" {
				sampler.Sample(base, time.Second)
			}
			got := sampler.Sample(tc.next, tc.duration)
			if got.Reason != tc.reason || got.Host.Valid != tc.hostValid || got.Process.Valid != tc.procValid || got.Group.Valid != tc.groupValid {
				t.Fatalf("sample = %+v", got)
			}
			if got.Host.Valid && got.Host.Value != tc.hostValue || got.Process.Valid && got.Process.Value != tc.procValue || got.Group.Valid && got.Group.Value != tc.groupValue {
				t.Fatalf("values = %+v", got)
			}
		})
	}
}

func TestCPUDeltaSamplerDeterministicAndRecoversAfterInvalidSample(t *testing.T) {
	first := cpuSnapshot(10, 20, struct {
		key, identity string
		cpu           uint64
	}{"a", "pid-a", 2}, struct {
		key, identity string
		cpu           uint64
	}{"b", "pid-b", 3})
	second := cpuSnapshot(20, 40, struct {
		key, identity string
		cpu           uint64
	}{"a", "pid-a", 4}, struct {
		key, identity string
		cpu           uint64
	}{"b", "pid-b", 6})
	a := NewCPUDeltaSampler("a", []string{"b", "a"})
	b := NewCPUDeltaSampler("a", []string{"a", "b"})
	a.Sample(first, time.Second)
	b.Sample(first, time.Second)
	if gotA, gotB := a.Sample(second, time.Second), b.Sample(second, time.Second); !reflect.DeepEqual(gotA, gotB) {
		t.Fatalf("non-deterministic samples: %+v != %+v", gotA, gotB)
	}
	if got := a.Sample(second, time.Second); got.Reason != CPUUnavailable || got.Host.Valid {
		t.Fatalf("same counters should have no delta: %+v", got)
	}
}

func TestCPUDeltaSamplerUsesWallTimeAndLogicalCPUCount(t *testing.T) {
	first := cpuSnapshot(0, 0, struct {
		key, identity string
		cpu           uint64
	}{"pid:1", "pid-1", 0})
	second := cpuSnapshot(100, 800, struct {
		key, identity string
		cpu           uint64
	}{"pid:1", "pid-1", 200})
	sampler := NewCPUDeltaSampler("pid:1", nil, 8)
	sampler.Sample(first, time.Second)
	got := sampler.Sample(second, 2*time.Second)
	if got.Reason != CPUValid || got.Process.Value != 100 {
		t.Fatalf("process sample = %+v, want 100%% for one busy core over two seconds", got)
	}
}

func TestCPUDeltaSamplerKeepsProcessRelativeCPUAboveOneCore(t *testing.T) {
	first := CPUSnapshot{Host: CPUHostCounters{Busy: 0, Total: 1000}, Processes: map[string]CPUProcessCounters{
		"pid:1": {Identity: "pid-1", CPU: 0, Binary: "yolomux-go"},
	}}
	second := CPUSnapshot{Host: CPUHostCounters{Busy: 100, Total: 1100}, Processes: map[string]CPUProcessCounters{
		"pid:1": {Identity: "pid-1", CPU: 450, Binary: "yolomux-go"},
	}}
	sampler := NewCPUDeltaSampler("pid:1", nil, 8)
	sampler.Sample(first, time.Second)
	got := sampler.Sample(second, time.Second)
	if got.Reason != CPUValid || got.Process.Value != 450 {
		t.Fatalf("process-relative sample = %+v, want 450%% for 4.5 busy cores", got)
	}
	if got.Group.Value != 56.25 {
		t.Fatalf("logical-CPU-normalized group sample = %+v, want 56.25%%", got)
	}
}

func TestCPUDeltaSamplerUsesSnapshotProcessCounterRate(t *testing.T) {
	first := CPUSnapshot{Host: CPUHostCounters{Busy: 0, Total: 1000}, Processes: map[string]CPUProcessCounters{
		"yolomux-go": {Identity: "pid-1", CPU: 0, Binary: "yolomux-go"},
	}}
	first.ProcessTicksPerSecond = 250
	second := CPUSnapshot{Host: CPUHostCounters{Busy: 250, Total: 2000}, Processes: map[string]CPUProcessCounters{
		"yolomux-go": {Identity: "pid-1", CPU: 250, Binary: "yolomux-go"},
	}}
	second.ProcessTicksPerSecond = 250

	sampler := NewCPUDeltaSampler("yolomux-go", nil, 8)
	sampler.Sample(first, time.Second)
	got := sampler.Sample(second, time.Second)
	if got.Reason != CPUValid || got.Process.Value != 100 || got.Group.Value != 12.5 {
		t.Fatalf("process/group sample = %+v, want 100%% and 12.5%%", got)
	}
	if got.ProcessCPUPercent["yolomux-go"] != 12.5 {
		t.Fatalf("grouped process sample = %#v, want 12.5%%", got.ProcessCPUPercent)
	}
}

func TestCPUDeltaSamplerPreservesPIDMembershipAndReset(t *testing.T) {
	first := cpuSnapshot(10, 20, struct {
		key, identity string
		cpu           uint64
	}{"pid:1", "pid-1", 2}, struct {
		key, identity string
		cpu           uint64
	}{"pid:2", "pid-2", 3})
	sampler := NewCPUDeltaSampler("pid:1", []string{"pid:1", "pid:2"}, 1)
	sampler.Sample(first, time.Second)
	missing := cpuSnapshot(20, 40, struct {
		key, identity string
		cpu           uint64
	}{"pid:1", "pid-1", 4})
	if got := sampler.Sample(missing, time.Second); got.Reason != CPUGroupMembershipChange {
		t.Fatalf("membership change = %+v", got)
	}
	restarted := cpuSnapshot(30, 60, struct {
		key, identity string
		cpu           uint64
	}{"pid:1", "pid-new", 5}, struct {
		key, identity string
		cpu           uint64
	}{"pid:2", "pid-2", 6})
	if got := sampler.Sample(restarted, time.Second); got.Reason != CPUProcessRestart {
		t.Fatalf("restart = %+v", got)
	}
}

func TestCPUDeltaSamplerRuntimeSnapshotsEmitAllStableBinarySeries(t *testing.T) {
	first := CPUSnapshot{
		Host: CPUHostCounters{Busy: 100, Total: 200},
		Processes: map[string]CPUProcessCounters{
			"101:10": {Identity: "101:10", CPU: 100, Binary: "yolomux-go"},
			"102:20": {Identity: "102:20", CPU: 50, Binary: "python"},
			"103:30": {Identity: "103:30", CPU: 25, Binary: "python"},
			"104:40": {Identity: "104:40", CPU: 10, Binary: "node"},
		},
	}
	second := CPUSnapshot{
		Host: CPUHostCounters{Busy: 140, Total: 240},
		Processes: map[string]CPUProcessCounters{
			"101:10": {Identity: "101:10", CPU: 120, Binary: "yolomux-go"},
			"102:20": {Identity: "102:20", CPU: 60, Binary: "python"},
			"103:30": {Identity: "103:30", CPU: 35, Binary: "python"},
			"104:40": {Identity: "104:40", CPU: 15, Binary: "node"},
		},
	}
	sampler := NewCPUDeltaSampler("yolomux-go", nil, 1)
	sampler.Sample(first, time.Second)
	got := sampler.Sample(second, time.Second)
	if got.Reason != CPUValid || !got.Process.Valid || !got.Group.Valid {
		t.Fatalf("runtime sample = %+v", got)
	}
	want := map[string]float64{"node": 5, "python": 20, "yolomux-go": 20}
	if !reflect.DeepEqual(got.ProcessCPUPercent, want) {
		t.Fatalf("grouped series = %#v, want %#v", got.ProcessCPUPercent, want)
	}
}

func TestCPUDeltaSamplerRuntimeSnapshotsSkipChangedBinaryMembership(t *testing.T) {
	first := CPUSnapshot{
		Host: CPUHostCounters{Busy: 100, Total: 200},
		Processes: map[string]CPUProcessCounters{
			"101:10": {Identity: "101:10", CPU: 100, Binary: "yolomux-go"},
			"102:20": {Identity: "102:20", CPU: 50, Binary: "python"},
		},
	}
	second := CPUSnapshot{
		Host: CPUHostCounters{Busy: 140, Total: 240},
		Processes: map[string]CPUProcessCounters{
			"101:10": {Identity: "101:10", CPU: 120, Binary: "yolomux-go"},
			"103:30": {Identity: "103:30", CPU: 60, Binary: "python"},
		},
	}
	sampler := NewCPUDeltaSampler("yolomux-go", nil, 1)
	sampler.Sample(first, time.Second)
	got := sampler.Sample(second, time.Second)
	if got.Reason != CPUValid {
		t.Fatalf("membership sample = %+v", got)
	}
	if !reflect.DeepEqual(got.ProcessCPUPercent, map[string]float64{"yolomux-go": 20}) {
		t.Fatalf("grouped series = %#v", got.ProcessCPUPercent)
	}
}

func TestCPUDeltaSamplerOwnedIdentityExcludesSameNamedCotenants(t *testing.T) {
	first := CPUSnapshot{Host: CPUHostCounters{Busy: 100, Total: 200}, Processes: map[string]CPUProcessCounters{
		"101:10": {Identity: "101:10", CPU: 10, Binary: "yolomux-go"},
		"202:20": {Identity: "202:20", CPU: 30, Binary: "yolomux-go"},
		"303:30": {Identity: "303:30", CPU: 40, Binary: "python"},
	}}
	second := CPUSnapshot{Host: CPUHostCounters{Busy: 140, Total: 240}, Processes: map[string]CPUProcessCounters{
		"101:10": {Identity: "101:10", CPU: 20, Binary: "yolomux-go"},
		"202:20": {Identity: "202:20", CPU: 80, Binary: "yolomux-go"},
		"303:30": {Identity: "303:30", CPU: 60, Binary: "python"},
	}}
	sampler := NewCPUDeltaSampler("yolomux-go", []string{"yolomux-go"}, 1)
	sampler.processIdentity = "101:"
	sampler.groupIdentity = "101:"
	sampler.Sample(first, time.Second)
	got := sampler.Sample(second, time.Second)
	if got.Reason != CPUValid || !got.Process.Valid || !got.Group.Valid {
		t.Fatalf("owned sample = %+v", got)
	}
	if got.Process.Value != 10 || got.Group.Value != 10 {
		t.Fatalf("owned CPU percentages = process=%v group=%v, want 10%%", got.Process.Value, got.Group.Value)
	}
	if !reflect.DeepEqual(got.ProcessCPUPercent, map[string]float64{"yolomux-go": 10}) {
		t.Fatalf("owned daemon series = %#v", got.ProcessCPUPercent)
	}
}

func TestCPUDeltaSamplerScopesProcessButKeepsHostTopUsers(t *testing.T) {
	first := CPUSnapshot{Host: CPUHostCounters{Busy: 100, Total: 200}, Processes: map[string]CPUProcessCounters{
		"101:10": {Identity: "101:10", CPU: 100, Binary: "yolomux-go"},
		"202:20": {Identity: "202:20", CPU: 200, Binary: "yolomux-go"},
		"303:30": {Identity: "303:30", CPU: 50, Binary: "python"},
	}}
	second := CPUSnapshot{Host: CPUHostCounters{Busy: 140, Total: 240}, Processes: map[string]CPUProcessCounters{
		"101:10": {Identity: "101:10", CPU: 110, Binary: "yolomux-go"},
		"202:20": {Identity: "202:20", CPU: 260, Binary: "yolomux-go"},
		"303:30": {Identity: "303:30", CPU: 70, Binary: "python"},
	}}
	sampler := NewCPUDeltaSampler("yolomux-go", nil, 1)
	sampler.processIdentity = "101:"
	sampler.Sample(first, time.Second)
	got := sampler.Sample(second, time.Second)
	if got.Reason != CPUValid || !got.Process.Valid || got.Process.Value != 10 {
		t.Fatalf("owned process sample = %+v, want 10%%", got)
	}
	wantUsers := map[string]float64{"yolomux-go": 70, "python": 20}
	if !reflect.DeepEqual(got.ProcessCPUPercent, wantUsers) {
		t.Fatalf("host top-user series = %#v, want %#v", got.ProcessCPUPercent, wantUsers)
	}
	if !reflect.DeepEqual(got.OwnedProcessCPUPercent, map[string]float64{"yolomux-go": 10}) {
		t.Fatalf("owned process series = %#v, want yolomux-go=10", got.OwnedProcessCPUPercent)
	}
}

func TestCPUDeltaSamplerBoundsBinarySeriesToTopFour(t *testing.T) {
	first := CPUSnapshot{Host: CPUHostCounters{Busy: 100, Total: 200}, Processes: map[string]CPUProcessCounters{}}
	second := CPUSnapshot{Host: CPUHostCounters{Busy: 200, Total: 300}, Processes: map[string]CPUProcessCounters{}}
	for index, binary := range []string{"alpha", "bravo", "charlie", "delta", "echo", "foxtrot"} {
		identity := binary + "-pid"
		first.Processes[identity] = CPUProcessCounters{Identity: identity, CPU: 0, Binary: binary}
		cpu := uint64(index + 1)
		if binary == "foxtrot" {
			cpu = 200
		}
		second.Processes[identity] = CPUProcessCounters{Identity: identity, CPU: cpu, Binary: binary}
	}
	sampler := NewCPUDeltaSampler("alpha", nil, 1)
	sampler.Sample(first, time.Second)
	got := sampler.Sample(second, time.Second)
	if got.Reason != CPUValid {
		t.Fatalf("sample = %+v", got)
	}
	want := map[string]float64{"foxtrot": 100, "echo": 5, "delta": 4, "charlie": 3}
	if !reflect.DeepEqual(got.ProcessCPUPercent, want) {
		t.Fatalf("grouped series = %#v, want %#v", got.ProcessCPUPercent, want)
	}
}
