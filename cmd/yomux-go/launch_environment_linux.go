// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build linux

package main

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
)

func nativeProcessEnvironment(pid int) (map[string]string, error) {
	data, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "environ"))
	if err != nil {
		return nil, err
	}
	return decodeNativeEnvironment(data), nil
}

func nativeProcessArguments(pid int) ([]string, error) {
	data, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "cmdline"))
	if err != nil {
		return nil, err
	}
	if len(data) == 0 || data[len(data)-1] != 0 {
		return nil, fmt.Errorf("process arguments are unavailable or truncated")
	}
	parts := bytes.Split(data[:len(data)-1], []byte{0})
	arguments := make([]string, len(parts))
	for index, part := range parts {
		arguments[index] = string(part)
	}
	return arguments, nil
}
