// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build !linux

package main

import "fmt"

func nativeProcessEnvironment(pid int) (map[string]string, error) {
	return nil, fmt.Errorf("process environment inspection is unsupported for pid %d", pid)
}

func nativeProcessArguments(pid int) ([]string, error) {
	return nil, fmt.Errorf("process argument inspection is unsupported for pid %d", pid)
}
