// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build darwin

package main

import "fmt"

func darwinDescendantProcessEntries(rootPID int) ([]procEntry, error) {
	if rootPID <= 0 {
		return nil, fmt.Errorf("invalid process root %d", rootPID)
	}
	processes, err := processEntries()
	if err != nil {
		return nil, err
	}
	children := make(map[int][]procEntry)
	for _, process := range processes {
		children[process.PPID] = append(children[process.PPID], process)
	}
	queue := append([]procEntry(nil), children[rootPID]...)
	result := make([]procEntry, 0, len(queue))
	seen := make(map[int]struct{}, len(queue))
	for len(queue) > 0 && len(result) < maxProcEntries {
		process := queue[0]
		queue = queue[1:]
		if _, exists := seen[process.PID]; exists {
			continue
		}
		seen[process.PID] = struct{}{}
		result = append(result, process)
		queue = append(queue, children[process.PID]...)
	}
	return result, nil
}
