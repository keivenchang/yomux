// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"runtime"
	"strings"
	"time"

	"github.com/keivenchang/yomux/internal/productenv"
)

var platformUUIDPattern = regexp.MustCompile(`"IOPlatformUUID"\s*=\s*"([A-Za-z0-9-]+)"`)

func normalizeHostPartitionID(value, source string) (string, error) {
	value = strings.ToLower(strings.TrimSpace(value))
	if len(value) == 0 || len(value) > 128 || value == "." || value == ".." {
		return "", fmt.Errorf("invalid stable host ID from %s", source)
	}
	for index, character := range value {
		valid := character >= 'a' && character <= 'z' || character >= '0' && character <= '9' || character == '.' || character == '_' || character == '-'
		if !valid || index == 0 && (character == '.' || character == '_' || character == '-') {
			return "", fmt.Errorf("invalid stable host ID from %s", source)
		}
	}
	return value, nil
}

func resolveHostPartitionID(platform, override string, overridePresent bool, machineID, platformOutput string) (string, error) {
	if overridePresent {
		return normalizeHostPartitionID(override, "YOMUX_HOST_ID")
	}
	if platform == "darwin" {
		match := platformUUIDPattern.FindStringSubmatch(platformOutput)
		if len(match) != 2 {
			return "", errors.New("cannot read macOS platform UUID for host-partitioned state")
		}
		return normalizeHostPartitionID(match[1], "ioreg IOPlatformUUID")
	}
	if strings.TrimSpace(machineID) == "" {
		return "", errors.New("cannot read machine ID for host-partitioned state; set YOMUX_HOST_ID when unavailable")
	}
	return normalizeHostPartitionID(machineID, "/etc/machine-id")
}

func productHostPartitionID() (string, error) {
	override, overridePresent := productenv.Lookup("HOST_ID")
	if overridePresent {
		return resolveHostPartitionID(runtime.GOOS, override, true, "", "")
	}
	if runtime.GOOS == "darwin" {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		output, err := exec.CommandContext(ctx, "ioreg", "-rd1", "-c", "IOPlatformExpertDevice").Output()
		if err != nil {
			return "", fmt.Errorf("read macOS platform UUID: %w", err)
		}
		return resolveHostPartitionID(runtime.GOOS, "", false, "", string(output))
	}
	machineID, err := os.ReadFile("/etc/machine-id")
	if err != nil {
		return "", fmt.Errorf("read machine ID for host-partitioned state: %w", err)
	}
	return resolveHostPartitionID(runtime.GOOS, "", false, string(machineID), "")
}
