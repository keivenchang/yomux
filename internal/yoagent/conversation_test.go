// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yoagent

import (
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/bazelbuild/rules_go/go/runfiles"
)

func conversationJSONEqual(t *testing.T, index int, method string, actual any, expected json.RawMessage) {
	t.Helper()
	raw, err := json.Marshal(actual)
	if err != nil {
		t.Fatal(err)
	}
	var got, want any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(expected, &want); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("original conversation contract case %d (%s) differs", index, method)
	}
}

func TestConversationOriginalPythonContract(t *testing.T) {
	path, err := runfiles.Rlocation("yomux/tests/fixtures/agent_conversation_contract.json")
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		SourceCommit string `json:"source_commit"`
		SourceBlob   string `json:"source_blob"`
		Cases        []struct {
			Method string          `json:"method"`
			Value  any             `json:"value"`
			Kwargs map[string]any  `json:"kwargs"`
			Result json.RawMessage `json:"result"`
		} `json:"cases"`
	}
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.UseNumber()
	if err := decoder.Decode(&fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Cases) != 25 || fixture.SourceCommit != "cf0be86a89df87b7b43d9c2d25979ccf96353d55" || fixture.SourceBlob == "" {
		t.Fatal("conversation contract provenance/count changed")
	}
	for index, example := range fixture.Cases {
		var actual any
		switch example.Method {
		case "sanitize_message":
			clean, err := SanitizeMessage(example.Value, MessageOptions{}, time.Unix(0, 0))
			if err != nil {
				t.Fatal(err)
			}
			actual = clean
			if clean != nil {
				repeated, err := SanitizeMessage(clean, MessageOptions{}, time.Unix(0, 0))
				if err != nil {
					t.Fatal(err)
				}
				encoded, err := json.Marshal(clean)
				if err != nil {
					t.Fatal(err)
				}
				conversationJSONEqual(t, index, "idempotent message normalization", repeated, encoded)
			}
		case "bounded_auxiliary_lines":
			lines, truncated := BoundedAuxiliaryLines(example.Value)
			actual = []any{lines, truncated}
		case "bounded_stream_items":
			items, truncated, err := BoundedStreamItems(example.Value)
			if err != nil {
				t.Fatal(err)
			}
			actual = []any{items, truncated}
		case "sanitize_cli_sessions":
			clean, err := SanitizeCLISessions(example.Value, number(example.Kwargs["monotonic_now"]), []string{"claude", "codex"})
			if err != nil {
				t.Fatal(err)
			}
			actual = clean
		default:
			t.Fatalf("unhandled contract method %s", example.Method)
		}
		conversationJSONEqual(t, index, example.Method, actual, example.Result)
	}
}

func TestConversationAuxiliaryRedactionUnicodeBoundaries(t *testing.T) {
	for _, example := range []struct{ input, want string }{
		{"token=value", "token=<redacted>"},
		{"TOKEN = value password=other", "TOKEN=<redacted> password=<redacted>"},
		{"åtoken=value secret\u001c=\u00a0hidden", "åtoken=value secret=<redacted>"},
		{"api-key=value\nnext", "api-key=<redacted>\nnext"},
		{"token=   ", "token=   "},
	} {
		if got := RedactAuxiliary(example.input); got != example.want {
			t.Fatal("credential boundary or whitespace redaction changed")
		}
	}
}

func TestConversationNormalizationOwnsNestedActionsAndLabels(t *testing.T) {
	action := map[string]any{"instruction": "reviewed"}
	params := map[string]any{"name": "search"}
	input := map[string]any{"role": "assistant", "content": "answer", "createdAt": "fixed", "actions": []any{action}, "streamItems": []any{map[string]any{"kind": "tool", "text": "result", "label": map[string]any{"key": "tool", "params": params}}}}
	clean, err := SanitizeMessage(input, MessageOptions{}, time.Unix(0, 0))
	if err != nil {
		t.Fatal(err)
	}
	action["instruction"] = "changed"
	params["name"] = "changed"
	if array(clean["actions"])[0].(map[string]any)["instruction"] != "reviewed" {
		t.Fatal("saved action aliases caller's mutable state")
	}
	items := clean["streamItems"].([]map[string]any)
	if object(object(items[0]["label"])["params"])["name"] != "search" {
		t.Fatal("saved label aliases caller's mutable state")
	}
}

func TestConversationNumericZeroAndFiniteResponse(t *testing.T) {
	for _, zero := range []json.Number{"0e0", "-0", "0.00", "-0.0", "0E+20", "1e-1000"} {
		clean, err := SanitizeMessage(map[string]any{"role": "assistant", "content": "answer", "createdAt": "fixed", "auxiliaryLines": []any{"line"}, "auxiliaryDone": zero}, MessageOptions{}, time.Unix(0, 0))
		if err != nil {
			t.Fatal(err)
		}
		if _, exists := clean["auxiliaryDone"]; exists {
			t.Errorf("numeric zero %s became true", zero)
		}
		clean, err = SanitizeMessage(map[string]any{"role": "user", "content": zero, "createdAt": "fixed"}, MessageOptions{}, time.Unix(0, 0))
		if err != nil || clean != nil {
			t.Errorf("numeric zero %s became content", zero)
		}
	}
	for _, example := range []struct{ input, want float64 }{{1e308, 1e308}, {2.6755, 2.675}, {12.34567, 12.346}} {
		clean, err := SanitizeMessage(map[string]any{"role": "assistant", "content": "answer", "createdAt": "fixed", "responseMs": example.input}, MessageOptions{}, time.Unix(0, 0))
		if err != nil {
			t.Fatal(err)
		}
		if clean["responseMs"] != example.want {
			t.Errorf("response duration %g lost finite decimal rounding", example.input)
		}
		if _, err := json.Marshal(clean); err != nil {
			t.Errorf("finite message became unserializable: %v", err)
		}
	}
}

func TestConversationCredentialAliasesRedactLikeOriginal(t *testing.T) {
	for _, key := range []string{"ap\u0131_key", "ap\u0130_key", "AP\u0130-KEY"} {
		if got := RedactAuxiliary(key + "=fixture-value"); got != key+"=<redacted>" {
			t.Error("Unicode credential alias escaped redaction")
		}
	}
}

func TestPublicJobRedactsAndBoundsPromptAndActionText(t *testing.T) {
	job := map[string]any{
		"id": "job-1", "prompt": "send token=private to session",
		"action": map[string]any{"text": "password=hunter2 " + strings.Repeat("x", 300)},
	}
	public, err := PublicJob(job)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(public["prompt"].(string), "private") || strings.Contains(public["action"].(map[string]any)["text"].(string), "hunter2") {
		t.Fatalf("public YO!agent job exposed credentials: %#v", public)
	}
	if len([]rune(public["action"].(map[string]any)["text"].(string))) != 243 {
		t.Fatalf("public action text was not bounded: %#v", public["action"])
	}
	if job["prompt"] != "send token=private to session" {
		t.Fatalf("public redaction mutated persisted job data: %#v", job)
	}
}

func TestConversationCLIRejectsAmbiguousAndNonfiniteState(t *testing.T) {
	for _, value := range []any{
		map[string]any{"codex": map[string]any{"session_id": "session", "updated_ts": "Infinity"}},
		map[string]any{"claude": map[string]any{"session_id": "one"}, " CLAUDE ": map[string]any{"session_id": "two"}},
	} {
		clean, err := SanitizeCLISessions(value, 123.5, []string{"claude", "codex"})
		if len(clean) != 0 || (!errors.Is(err, ErrAmbiguousBackend) && !errors.Is(err, ErrInvalidTimestamp)) {
			t.Fatalf("unsafe CLI state admission error=%v", err)
		}
	}
}

func TestConversationStreamCountRejectsOverflow(t *testing.T) {
	_, _, err := BoundedStreamItems([]any{map[string]any{"kind": "tool", "text": "result", "tokenCount": json.Number("1e309")}})
	if err == nil {
		t.Fatal("overflowing stream count was admitted")
	}
}
