// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"errors"
	"strconv"
	"strings"
)

const gitMetadataMaxEntries = 250_000

// Both the snapshot and descriptor readers enforce this repository extension allowlist.
func normalizeGitRepositoryExtension(name, value string, hasValue bool) (string, error) {
	value = strings.ToLower(strings.TrimSpace(value))
	switch name {
	case "objectformat":
		if value != "sha1" && value != "sha256" {
			return "", errors.New("Git object format is unsupported")
		}
	case "refstorage":
		if value != "files" && value != "reftable" {
			return "", errors.New("Git ref storage format is unsupported")
		}
	case "preciousobjects", "worktreeconfig":
		normalized, valid := normalizeDarwinGitBoolean(value, hasValue)
		if !valid {
			return "", errors.New("Git repository format configuration is invalid")
		}
		value = normalized
	default:
		return "", errors.New("Git repository extension is unsupported: " + name)
	}
	return value, nil
}

func normalizeDarwinGitBoolean(value string, hasValue bool) (string, bool) {
	if !hasValue {
		return "true", true
	}
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "true", "yes", "on":
		return "true", true
	case "", "false", "no", "off":
		return "false", true
	}
	number, err := strconv.ParseInt(strings.TrimSpace(value), 10, 64)
	if err != nil {
		return "", false
	}
	if number != 0 {
		return "true", true
	}
	return "false", true
}
