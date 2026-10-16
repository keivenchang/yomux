// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package productversion

import (
	"bufio"
	"fmt"
	"io"
	"regexp"
	"strings"
)

var valuePattern = regexp.MustCompile(`^version: "(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"$`)

// Parse accepts exactly one version key and rejects every other nonblank YAML line.
func Parse(reader io.Reader) (string, error) {
	scanner := bufio.NewScanner(reader)
	var version string
	for line := 1; scanner.Scan(); line++ {
		value := strings.TrimSpace(scanner.Text())
		if value == "" || strings.HasPrefix(value, "#") {
			continue
		}
		match := valuePattern.FindStringSubmatch(value)
		if match == nil {
			return "", fmt.Errorf("version.yaml:%d: expected version: \"major.minor.patch\"", line)
		}
		if version != "" {
			return "", fmt.Errorf("version.yaml:%d: duplicate version field", line)
		}
		version = match[1] + "." + match[2] + "." + match[3]
	}
	if err := scanner.Err(); err != nil {
		return "", err
	}
	if version == "" {
		return "", fmt.Errorf("version.yaml: missing version field")
	}
	return version, nil
}
