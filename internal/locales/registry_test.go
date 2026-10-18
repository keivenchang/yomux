// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package locales

import (
	"os"
	"path/filepath"
	"testing"
)

func TestNormalizeShippedCodes(t *testing.T) {
	specs := []Spec{{Code: "en"}, {Code: "zh-Hant"}}
	for _, example := range []struct {
		Value  string
		System bool
		Want   string
	}{
		{" EN ", false, "en"}, {"\u001cZH-hant\u001f", false, "zh-Hant"}, {"en-xa", false, "en-XA"},
		{"system", true, "system"}, {"system", false, "fallback"}, {"unknown", true, "fallback"},
	} {
		if actual := Normalize(specs, example.Value, "fallback", example.System); actual != example.Want {
			t.Fatalf("normalize=%q want %q", actual, example.Want)
		}
	}
}

func TestLoadRejectsInvalidRegistry(t *testing.T) {
	for _, content := range []string{
		`[]`, `[{"code":"de"}]`, `[{"code":"en"},{"code":"EN"}]`,
		`[{"code":"en"},{"code":"../outside"}]`, `[{"code":"en"},{"code":"en-XA"}]`,
		`[{"code":"en"},{"code":"."}]`,
		"[{\"code\":\"en\"}, {\"code\":\"\xff\"}]",
	} {
		root := t.TempDir()
		path := filepath.Join(root, "assets/runtime/locale-registry.json")
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := Load(root); err == nil {
			t.Fatal("invalid registry accepted")
		}
	}
}
