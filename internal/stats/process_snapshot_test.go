// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"errors"
	"fmt"
	"os"
	"testing"
	"time"
)

func TestDarwinProcessInventoryRetainsAgentsFromPartialCensus(t *testing.T) {
	rows, complete := assembleDarwinProcessSnapshot([]int{42, 43, 44}, true, func(pid int) (DarwinProcess, error) {
		switch pid {
		case 42:
			return DarwinProcess{PID: 42, Identity: "42:1:0", Command: "bash", Executable: "/bin/bash"}, nil
		case 43:
			return DarwinProcess{PID: 43, PPID: 42, Identity: "43:1:0", Command: "codex --resume thread", Executable: "/usr/bin/codex"}, nil
		default:
			return DarwinProcess{}, os.ErrPermission
		}
	})
	if complete {
		t.Fatal("permission-denied sibling PID must make host metrics incomplete")
	}
	observed, err := darwinProcessInventory(func() ([]DarwinProcess, bool) { return rows, complete })
	if err != nil || len(observed) != 2 {
		t.Fatalf("usable inventory rows=%+v err=%v, want retained shell and native agent", observed, err)
	}
	processes := make([]AgentProcess, 0, len(observed))
	for _, row := range observed {
		processes = append(processes, AgentProcess{PID: row.PID, PPID: row.PPID, Command: row.Command, Executable: row.Executable})
	}
	records, inventoryErrors := BuildAgentInventory(context.Background(), AgentInventorySources{
		Processes: func(context.Context) ([]AgentProcess, error) { return processes, nil },
		Panes: func(context.Context) ([]AgentTmuxPane, error) {
			return []AgentTmuxPane{{PID: 42, Session: "fixture", Window: "0", PaneID: "%1"}}, nil
		},
	})
	if len(inventoryErrors) != 0 || len(records) != 1 || records[0].PID != 43 || records[0].TmuxPaneID != "%1" {
		t.Fatalf("partial-census agent inventory records=%+v errors=%v, want native Codex in its pane", records, inventoryErrors)
	}
	for _, emptyComplete := range []bool{false, true} {
		if _, err := darwinProcessInventory(func() ([]DarwinProcess, bool) { return nil, emptyComplete }); err == nil {
			t.Fatalf("empty inventory complete=%t admitted unavailable discovery", emptyComplete)
		}
	}
}

func TestDarwinProcessSnapshotMarksUnreadablePIDIncomplete(t *testing.T) {
	owner := DarwinProcess{PID: 42, Identity: "42:1:0", Executable: "yomux-go", CPUSeconds: 1, RSSBytes: 100}
	rows, complete := assembleDarwinProcessSnapshot([]int{42, 43}, true, func(pid int) (DarwinProcess, error) {
		if pid == owner.PID {
			return owner, nil
		}
		return DarwinProcess{}, os.ErrPermission
	})
	if complete || len(rows) != 1 || rows[0] != owner {
		t.Fatalf("snapshot rows=%+v complete=%t, want retained owner and incomplete census", rows, complete)
	}
	snapshot := CPUSnapshot{Host: CPUHostCounters{Total: 100, Busy: 10}, ProcessTicksPerSecond: 1_000_000_000, ProcessCensusIncomplete: !complete, Processes: map[string]CPUProcessCounters{
		owner.Identity: {Identity: owner.Identity, Binary: "yomux-go", CPU: 1_000_000_000},
	}}
	next := *cloneCPUSnapshot(snapshot)
	next.Host.Total += 100
	next.Host.Busy += 10
	process := next.Processes[owner.Identity]
	process.CPU += 100_000_000
	next.Processes[owner.Identity] = process
	sampler := NewCPUDeltaSampler("yomux-go", nil, 1)
	sampler.processIdentity = owner.Identity
	sampler.Sample(snapshot, time.Second)
	sample := sampler.Sample(next, time.Second)
	facts := BuildCPUFacts(sample, sample.ProcessCPUPercent, hostFactContext())
	if len(facts.Observations) != 1 || facts.Observations[0].Payload["process_cpu_percent"] != nil || facts.Observations[0].Payload["owned_process_cpu_percent"] == nil {
		t.Fatalf("partial census CPU facts = %+v, want owned CPU without host process breakdown", facts)
	}
	memoryFacts := BuildSystemMemoryFacts(MemoryCounters{CapacityBytes: 1000, UsedBytes: 200, ProcessCensusIncomplete: !complete}, []ProcessCensusRow{owner.censusRow()}, nil, hostFactContext())
	if len(memoryFacts.Observations) != 1 || memoryFacts.Observations[0].Payload["process_memory_bytes"] != nil {
		t.Fatalf("partial census memory facts = %+v, want host memory without process breakdown", memoryFacts)
	}
}

func TestDarwinProcessSnapshotCompletenessAcrossReadOutcomes(t *testing.T) {
	owner := DarwinProcess{PID: 42, Identity: "42:1:0", Executable: "yomux-go"}
	for _, tc := range []struct {
		name         string
		listComplete bool
		row          DarwinProcess
		err          error
		wantComplete bool
		wantRows     int
	}{
		{"complete", true, owner, nil, true, 2},
		{"truncated PID list", false, owner, nil, false, 2},
		{"process exited", true, DarwinProcess{}, os.ErrNotExist, true, 1},
		{"wrapped process exit", true, DarwinProcess{}, fmt.Errorf("proc_pidinfo: %w", os.ErrNotExist), true, 1},
		{"permission denied", true, DarwinProcess{}, os.ErrPermission, false, 1},
		{"short process read", true, DarwinProcess{}, errors.New("short observation"), false, 1},
		{"missing identity", true, DarwinProcess{PID: 43, Executable: "codex"}, nil, false, 1},
		{"missing executable", true, DarwinProcess{PID: 43, Identity: "43:1:0"}, nil, false, 1},
		{"different PID", true, owner, nil, false, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			row := tc.row
			if tc.wantRows == 2 {
				row.PID = 43
				row.Identity = "43:1:0"
			}
			for _, pids := range [][]int{{43, 0, -1, 42}, {42, -1, 0, 43}} {
				rows, complete := assembleDarwinProcessSnapshot(pids, tc.listComplete, func(pid int) (DarwinProcess, error) {
					if pid == owner.PID {
						return owner, nil
					}
					return row, tc.err
				})
				if complete != tc.wantComplete || len(rows) != tc.wantRows || rows[0] != owner {
					t.Fatalf("snapshot rows=%+v complete=%t, want %d sorted rows complete=%t", rows, complete, tc.wantRows, tc.wantComplete)
				}
			}
		})
	}
}
