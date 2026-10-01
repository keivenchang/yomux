// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build !darwin

package stats

func DarwinProcessSnapshot() ([]DarwinProcess, bool)          { return nil, false }
func darwinHostCPU() (uint64, uint64, bool)                   { return 0, 0, false }
func darwinSystemLogicalCPUs() int                            { return 0 }
func darwinProcessIdentity(int) (DarwinProcess, bool)         { return DarwinProcess{}, false }
func DarwinProcessMetricsForPID(int) (ProcessCensusRow, bool) { return ProcessCensusRow{}, false }
