// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package productenv

import (
	"os"
	"strings"
	"testing"
)

func preserveProductEnvironment(t *testing.T) {
	t.Helper()
	original := make(map[string]string)
	for _, entry := range os.Environ() {
		name, value, _ := strings.Cut(entry, "=")
		if strings.HasPrefix(name, "YOMUX_") || strings.HasPrefix(name, "YOLOMUX_") {
			original[name] = value
		}
	}
	t.Cleanup(func() {
		for _, entry := range os.Environ() {
			name, _, _ := strings.Cut(entry, "=")
			if strings.HasPrefix(name, "YOMUX_") || strings.HasPrefix(name, "YOLOMUX_") {
				if _, existed := original[name]; !existed {
					_ = os.Unsetenv(name)
				}
			}
		}
		for name, value := range original {
			_ = os.Setenv(name, value)
		}
	})
}

func TestPresenceAndWarningProvenance(t *testing.T) {
	cases := []struct {
		name   string
		values map[string]string
		want   string
		warns  bool
	}{
		{"legacy", map[string]string{"YOLOMUX_FIXTURE": "old"}, "old", true},
		{"canonical", map[string]string{"YOMUX_FIXTURE": "new"}, "new", false},
		{"matching", map[string]string{"YOMUX_FIXTURE": "same", "YOLOMUX_FIXTURE": "same"}, "same", true},
		{"conflict", map[string]string{"YOMUX_FIXTURE": "new", "YOLOMUX_FIXTURE": "old"}, "new", true},
		{"empty", map[string]string{"YOMUX_FIXTURE": "", "YOLOMUX_FIXTURE": "old"}, "", true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			preserveProductEnvironment(t)
			for _, entry := range os.Environ() {
				name, _, _ := strings.Cut(entry, "=")
				if strings.HasPrefix(name, "YOMUX_") || strings.HasPrefix(name, "YOLOMUX_") {
					t.Setenv(name, "")
					_ = os.Unsetenv(name)
				}
			}
			for name, value := range c.values {
				t.Setenv(name, value)
			}
			if got := Normalize(); got != c.warns {
				t.Fatalf("raw legacy input=%t, want %t", got, c.warns)
			}
			if Value("FIXTURE") != c.want || Value("YOLOMUX_FIXTURE") != c.want {
				t.Fatal("canonical presence was not preserved")
			}
			if Normalize() {
				t.Fatal("generated aliases reported as raw legacy input")
			}
			Assign("FIXTURE", "")
			if value, present := os.LookupEnv("YOLOMUX_FIXTURE"); !present || value != "" {
				t.Fatal("internally authored empty setting did not replace its alias")
			}
			if Normalize() {
				t.Fatal("internally authored setting generated a false warning")
			}
			t.Setenv("YOLOMUX_SECOND", "private-value")
			Assign("FIXTURE", "internal-value")
			if !Normalize() {
				t.Fatal("new legacy handoff was not detected")
			}
			t.Setenv("YOMUX_FIXTURE", "canonical-change")
			if Normalize() {
				t.Fatal("canonical changes must not warn for retained generated aliases")
			}
			t.Setenv("YOLOMUX_FIXTURE", "legacy-change")
			if !Normalize() {
				t.Fatal("changed legacy handoff was not detected")
			}
			t.Setenv("YOLOMUX_FIXTURE", "raw-before-assignment")
			Assign("FIXTURE", "internal")
			if !Normalize() {
				t.Fatal("assignment discarded raw legacy input before recording provenance")
			}
			if Normalize() {
				t.Fatal("assignment warning repeated after consumption")
			}
		})
	}
}

func TestSuffixLookupAndUnset(t *testing.T) {
	preserveProductEnvironment(t)
	for _, key := range Names("READ_FIXTURE") {
		_ = os.Unsetenv(key)
	}
	if _, present := Lookup("READ_FIXTURE"); present {
		t.Fatal("missing setting is present")
	}
	t.Setenv(Names("READ_FIXTURE")[1], "legacy")
	if Value("READ_FIXTURE") != "legacy" {
		t.Fatal("legacy suffix lookup failed")
	}
	t.Setenv(Name("READ_FIXTURE"), "")
	if value, present := Lookup("READ_FIXTURE"); !present || value != "" {
		t.Fatal("empty canonical setting lost presence")
	}
	t.Setenv(Name("READ_FIXTURE"), "late")
	if Value("READ_FIXTURE") != "late" {
		t.Fatal("late update was cached")
	}
	Unset("READ_FIXTURE")
	if _, present := Lookup("READ_FIXTURE"); present {
		t.Fatal("unset revived an alias")
	}
}

func TestNamespaceRename(t *testing.T) {
	old := namespace
	namespace = environmentNamespace{Canonical: "NEXT_", Legacy: []string{"YOMUX_", "YOLOMUX_"}}
	t.Cleanup(func() { namespace = old })
	preserveProductEnvironment(t)
	t.Setenv("NEXT_RENAME_FIXTURE", "")
	t.Setenv("YOMUX_RENAME_FIXTURE", "stale")
	if value, present := Lookup("RENAME_FIXTURE"); !present || value != "" {
		t.Fatal("rename precedence failed")
	}
	Assign("RENAME_FIXTURE", "new")
	if Value("RENAME_FIXTURE") != "new" {
		t.Fatal("renamed assignment failed")
	}
	Unset("RENAME_FIXTURE")
	if _, present := Lookup("RENAME_FIXTURE"); present {
		t.Fatal("renamed unset failed")
	}
}
