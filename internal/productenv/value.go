// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package productenv

import (
	"crypto/sha256"
	_ "embed"
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

//go:embed namespace.json
var namespaceJSON []byte

type environmentNamespace struct {
	Canonical string   `json:"canonical"`
	Legacy    []string `json:"legacy"`
}

var namespace = loadNamespace()

func loadNamespace() environmentNamespace {
	var value environmentNamespace
	if err := json.Unmarshal(namespaceJSON, &value); err != nil {
		panic(err)
	}
	return value
}

// Suffix identifies external product names at compatibility boundaries.
func Suffix(name string) (string, bool) {
	for _, prefix := range append([]string{namespace.Canonical}, namespace.Legacy...) {
		if strings.HasPrefix(name, prefix) {
			return strings.TrimPrefix(name, prefix), true
		}
	}
	return "", false
}

func setting(name string) string {
	if suffix, ok := Suffix(name); ok {
		return suffix
	}
	return name
}

// Name constructs canonical names only at external handoff boundaries.
func Name(suffix string) string { return namespace.Canonical + setting(suffix) }

func Names(suffix string) []string {
	names := []string{Name(suffix)}
	for _, prefix := range namespace.Legacy {
		names = append(names, prefix+setting(suffix))
	}
	return names
}

// DeprecationWarning belongs to the namespace owner, never a caller literal.
func DeprecationWarning() string {
	return strings.Join(namespace.Legacy, "*, ") + "* environment names are deprecated; use " + namespace.Canonical + "*"
}

// Value resolves product aliases without mutating the environment. An explicitly
// empty canonical value must clear a legacy setting as it does at CLI startup.
func Value(name string) string {
	value, _ := Lookup(name)
	return value
}

// Lookup preserves explicit empty values for presence-sensitive callers.
func Lookup(name string) (string, bool) {
	for _, key := range Names(name) {
		if value, present := os.LookupEnv(key); present {
			return value, true
		}
	}
	return "", false
}

// Assign publishes an internally authored setting through the compatibility
// owner so child processes do not mistake the generated alias for legacy input.
func Assign(name, value string) {
	legacyUsed := Normalize()
	for _, key := range Names(name)[1:] {
		_ = os.Unsetenv(key)
	}
	_ = os.Setenv(Name(name), value)
	if Normalize() || legacyUsed {
		_ = os.Setenv(Name("ENVIRONMENT_LEGACY_INPUT"), "1")
	}
}

// Unset retires all aliases together without reviving a stale legacy value.
func Unset(name string) {
	legacyUsed := Normalize()
	for _, key := range Names(name) {
		_ = os.Unsetenv(key)
	}
	if Normalize() || legacyUsed {
		_ = os.Setenv(Name("ENVIRONMENT_LEGACY_INPUT"), "1")
	}
}

// Normalize exposes compatibility aliases and records their provenance for
// repeated startup and child processes. The return value identifies raw input.
func Normalize() bool {
	legacyUsed := Value("ENVIRONMENT_LEGACY_INPUT") != ""
	priorAliases := Value("ENVIRONMENT_ALIASES")
	entries := os.Environ()
	for _, entry := range entries {
		name, value, ok := strings.Cut(entry, "=")
		suffix, product := Suffix(name)
		if ok && product && name != Name(suffix) && !strings.HasPrefix(suffix, "ENVIRONMENT_") {
			fingerprint := fmt.Sprintf("%x", sha256.Sum256([]byte(value)))
			if !strings.Contains(priorAliases, ","+name+"="+fingerprint+",") {
				legacyUsed = true
			}
		}
	}
	aliases := ","
	for _, entry := range entries {
		name, _, ok := strings.Cut(entry, "=")
		suffix, product := Suffix(name)
		if !ok || !product || strings.HasPrefix(suffix, "ENVIRONMENT_") {
			continue
		}
		value := Value(suffix)
		for _, key := range Names(suffix) {
			_ = os.Setenv(key, value)
			if key != Name(suffix) {
				record := key + "=" + fmt.Sprintf("%x", sha256.Sum256([]byte(value))) + ","
				if !strings.Contains(aliases, ","+record) {
					aliases += record
				}
			}
		}
	}
	for _, suffix := range []string{"NORMALIZED", "ALIASES", "LEGACY_INPUT"} {
		for _, key := range Names("ENVIRONMENT_" + suffix)[1:] {
			_ = os.Unsetenv(key)
		}
	}
	_ = os.Setenv(Name("ENVIRONMENT_NORMALIZED"), "1")
	_ = os.Setenv(Name("ENVIRONMENT_ALIASES"), aliases)
	_ = os.Setenv(Name("ENVIRONMENT_LEGACY_INPUT"), "")
	return legacyUsed
}
