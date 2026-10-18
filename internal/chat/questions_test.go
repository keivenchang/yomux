// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/bazelbuild/rules_go/go/runfiles"
)

func TestQuestionClassifierRejectsMalformedMetadata(t *testing.T) {
	registryPath, err := runfiles.Rlocation("yomux/assets/runtime/locale-registry.json")
	if err != nil {
		t.Fatal(err)
	}
	rulesPath := filepath.Join(filepath.Dir(registryPath), "chat-question-rules.json")
	registry, err := os.ReadFile(registryPath)
	if err != nil {
		t.Fatal(err)
	}
	rules, err := os.ReadFile(rulesPath)
	if err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		name, registry, rules, reason string
	}{
		{"empty prefix", `[{"code":"en","interrogatives":[""]}]`, string(rules), "empty locale question prefix"},
		{"null prefix", `[{"code":"en","interrogatives":[null]}]`, string(rules), "empty locale question prefix"},
		{"C0 whitespace prefix", `[{"code":"en","interrogatives":[" \t\u001c"]}]`, string(rules), "empty locale question prefix"},
		{"missing prefixes", `[{"code":"en"}]`, string(rules), "missing locale question prefixes"},
		{"unknown compact locale", string(registry), `{"punctuation":"?","trailing_closers":")","leading_exclusions":">","url_or_code_pattern":"url","compact_prefix_locales":["zz"]}`, "unknown compact-prefix locale"},
		{"empty compact locale", string(registry), `{"punctuation":"?","trailing_closers":")","leading_exclusions":">","url_or_code_pattern":"url","compact_prefix_locales":[""]}`, "unknown compact-prefix locale"},
		{"null compact locale", string(registry), `{"punctuation":"?","trailing_closers":")","leading_exclusions":">","url_or_code_pattern":"url","compact_prefix_locales":[null]}`, "unknown compact-prefix locale"},
		{"pseudo compact locale", string(registry), `{"punctuation":"?","trailing_closers":")","leading_exclusions":">","url_or_code_pattern":"url","compact_prefix_locales":["en-XA"]}`, "unknown compact-prefix locale"},
		{"incomplete grammar", string(registry), `{"punctuation":"?"}`, "incomplete chat question rules"},
		{"invalid expression", string(registry), `{"punctuation":"?","trailing_closers":")","leading_exclusions":">","url_or_code_pattern":"["}`, "error parsing regexp"},
		{"invalid UTF-8", string(registry), string([]byte{0xff}), "not UTF-8"},
		{"invalid JSON", string(registry), "{", "unexpected end of JSON"},
	} {
		t.Run(test.name, func(t *testing.T) {
			root := t.TempDir()
			data := filepath.Join(root, "assets", "runtime")
			if err := os.MkdirAll(data, 0700); err != nil {
				t.Fatal(err)
			}
			for name, content := range map[string]string{"locale-registry.json": test.registry, "chat-question-rules.json": test.rules} {
				if err := os.WriteFile(filepath.Join(data, name), []byte(content), 0600); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := NewQuestionClassifier(root); err == nil || !strings.Contains(err.Error(), test.reason) {
				t.Fatalf("constructor error=%v, want reason %q", err, test.reason)
			}
		})
	}
}

func TestQuestionClassifierContractAndConcurrentLocales(t *testing.T) {
	registry, err := runfiles.Rlocation("yomux/assets/runtime/locale-registry.json")
	if err != nil {
		t.Fatal(err)
	}
	root := filepath.Dir(filepath.Dir(filepath.Dir(registry)))
	classifier, err := NewQuestionClassifier(root)
	if err != nil {
		t.Fatal(err)
	}
	path, err := runfiles.Rlocation("yomux/tests/fixtures/chat_question_contract.json")
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var examples []struct {
		Locale   string `json:"locale"`
		Body     string `json:"body"`
		Question bool   `json:"question"`
	}
	if err := json.Unmarshal(raw, &examples); err != nil {
		t.Fatal(err)
	}
	if len(examples) != 114 {
		t.Fatalf("question contract rows=%d want 114", len(examples))
	}
	for index, example := range examples {
		t.Run(fmt.Sprintf("%d_%s", index, example.Locale), func(t *testing.T) {
			t.Parallel()
			if actual := classifier.IsQuestion(example.Body, example.Locale); actual != example.Question {
				t.Fatalf("case %d: question=%v want %v", index, actual, example.Question)
			}
		})
	}
}
