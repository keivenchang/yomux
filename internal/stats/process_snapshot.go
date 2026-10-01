// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"errors"
	"os"
	"sort"
	"strings"
)

type DarwinProcess struct {
	PID, PPID                      int
	Identity                       string
	Command, Executable, Directory string
	CPUSeconds                     float64
	RSSBytes                       uint64
	StartedAt                      float64
}

func (row DarwinProcess) censusRow() ProcessCensusRow {
	return ProcessCensusRow{PID: row.PID, Identity: row.Identity, Binary: normalizeHostBinary(row.Executable), CPUSeconds: row.CPUSeconds, RSSBytes: row.RSSBytes}
}

// DarwinProcessInventory retains observable processes even when protected PIDs
// make the host-wide metrics census incomplete. Discovery needs measured rows,
// while CPU and memory breakdowns additionally require a complete census.
func DarwinProcessInventory() ([]DarwinProcess, error) {
	return darwinProcessInventory(DarwinProcessSnapshot)
}

func darwinProcessInventory(snapshot func() ([]DarwinProcess, bool)) ([]DarwinProcess, error) {
	rows, _ := snapshot()
	if len(rows) == 0 {
		return nil, errors.New("Darwin process inventory unavailable")
	}
	return rows, nil
}

func assembleDarwinProcessSnapshot(pids []int, complete bool, read func(int) (DarwinProcess, error)) ([]DarwinProcess, bool) {
	rows := make([]DarwinProcess, 0, len(pids))
	for _, pid := range pids {
		if pid <= 0 {
			continue
		}
		row, err := read(pid)
		if err != nil {
			// Exited processes are a normal census race; unreadable live PIDs
			// must not make the retained rows look like a full host breakdown.
			if !errors.Is(err, os.ErrNotExist) {
				complete = false
			}
			continue
		}
		if row.PID != pid || strings.TrimSpace(row.Identity) == "" || strings.TrimSpace(row.Executable) == "" {
			complete = false
			continue
		}
		rows = append(rows, row)
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].PID < rows[j].PID })
	return rows, complete
}
