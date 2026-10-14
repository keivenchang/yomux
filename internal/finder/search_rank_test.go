// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"encoding/json"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestFilenameSearchRankMatchesBrowser(t *testing.T) {
	root := filepath.Join("..", "..")
	if directory := os.Getenv("TEST_SRCDIR"); directory != "" {
		root = filepath.Join(directory, os.Getenv("TEST_WORKSPACE"))
	}
	read := func(name string) string {
		t.Helper()
		data, err := os.ReadFile(filepath.Join(root, "static_src", "js", "yomux", name))
		if err != nil {
			t.Fatal(err)
		}
		return string(data)
	}
	extract := func(source, start, end string) string {
		t.Helper()
		first := strings.Index(source, start)
		if first < 0 {
			t.Fatalf("missing source owner %s", start)
		}
		last := strings.Index(source[first:], end)
		if last < 0 {
			t.Fatalf("missing source boundary %s", end)
		}
		return source[first : first+last]
	}
	core, layout := read("10_core_utils.js"), read("20_layout_state.js")
	script := "const searchRankWeights = " + string(searchRankWeightsJSON) + ";\n" +
		extract(core, "function fuzzySubsequenceMatch(", "function focusPanelSearchInput(") +
		extract(core, "function fuzzySearchScore(", "\nfunction ") +
		extract(layout, "function commandPaletteFileNameBonus(", "\nfunction ") +
		`console.log(JSON.stringify(JSON.parse(process.argv[1]).map(row =>
 fuzzySearchScore(row.query, [row.name]) + commandPaletteFileNameBonus({category:'file', searchFields:[row.name]}, row.query))));`
	queries := []string{"", "a", "ab", "world hello", "hello world", "DOIT2", "z a", "md", "a-b", "\u754c", "\U0001f600a", "\u0130", "i\u0307", "\u039f\u03a3", "a\ufeffb", "a\u0085b", "\u03bf\u03c2"}
	names := []string{"a", "ab", "a.extension-0000-long", "xa-0000.txt", "zzzzz-a", "hello-world.txt", "DOIT.2.md", "z-a.md", "a-b", "\u754c-a", "\U0001f600-a", "\u0130", "i\u0307", "\u039f\u03a3", "a\ufeffb", "a\u0085b", "\u03bf\u03c2"}
	var cases []struct{ Query, Name string }
	for _, query := range queries {
		for _, name := range names {
			cases = append(cases, struct{ Query, Name string }{query, name})
		}
	}
	// The browser fixture uses its production query/field casing.
	data, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	script = strings.ReplaceAll(script, "row.query", "row.Query")
	script = strings.ReplaceAll(script, "row.name", "row.Name")
	out, err := exec.Command("node", "-e", script, string(data)).CombinedOutput()
	if err != nil {
		t.Fatalf("browser score fixture: %v %s", err, out)
	}
	var scores []*float64
	if err := json.Unmarshal(out, &scores); err != nil {
		t.Fatal(err)
	}
	if len(scores) != len(cases) {
		t.Fatalf("scores=%d cases=%d", len(scores), len(cases))
	}
	for index, row := range cases {
		key, matched := searchFilenameSortKey(row.Name, prepareFilenameSearchTokens(row.Query))
		if matched != (scores[index] != nil) || matched && math.Abs(key.score-*scores[index]) > 1e-8 {
			t.Fatalf("query=%q name=%q server=%v matched=%t browser=%v", row.Query, row.Name, key.score, matched, scores[index])
		}
	}
}
