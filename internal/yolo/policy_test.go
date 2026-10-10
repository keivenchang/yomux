// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yolo

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func mustParse(t *testing.T, text string) *Policy {
	t.Helper()
	policy, err := Parse([]byte(text))
	if err != nil {
		t.Fatalf("Parse() error = %v", err)
	}
	return policy
}

func TestParseValidatesPolicySchema(t *testing.T) {
	tests := []struct {
		name string
		text string
	}{
		{name: "root mapping", text: "- approve"},
		{name: "unknown default", text: "default: allow"},
		{name: "numeric default", text: "default: 3"},
		{name: "rules not list", text: "rules: {}"},
		{name: "rule not mapping", text: "rules:\n  - nope"},
		{name: "invalid action", text: "rules:\n  - type: command\n    match: rm\n    action: allow"},
		{name: "invalid matcher", text: "rules:\n  - type: shell\n    match: rm\n    action: block"},
		{name: "missing matcher input", text: "rules:\n  - type: command\n    action: block"},
		{name: "empty matcher list", text: "rules:\n  - type: command\n    match: []\n    action: block"},
		{name: "non-string matcher", text: "rules:\n  - type: command\n    match: [rm, 5]\n    action: block"},
		{name: "invalid regex", text: "rules:\n  - type: regex\n    match: \"(\"\n    action: block"},
		{name: "non-string risk", text: "rules:\n  - type: command\n    match: rm\n    action: block\n    risk: 9"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := Parse([]byte(test.text)); err == nil {
				t.Fatal("Parse() succeeded for invalid policy")
			}
		})
	}
}

func TestParseDefaultsAndNullRules(t *testing.T) {
	policy := mustParse(t, "rules: null\n")
	if policy.Default != Ask || len(policy.Rules) != 0 {
		t.Fatalf("Parse() = %#v, want ask default and no rules", policy)
	}
}

func TestEvaluateSupportsAllRuleActionsInFirstMatchOrder(t *testing.T) {
	actions := []Action{Approve, Decline, Block, Ask, Notify, Off}
	for _, action := range actions {
		t.Run(string(action), func(t *testing.T) {
			policy := mustParse(t, "default: ask\nrules:\n  - name: first match\n    type: contains\n    match: dangerous-token\n    action: "+string(action)+"\n    risk: process\n  - name: later match\n    type: contains\n    match: dangerous\n    action: approve\n")
			decision := Evaluate(policy, "dangerous-token command", false)
			if decision.Action != action || decision.RuleName != "first match" || decision.Risk != "process" {
				t.Fatalf("Evaluate() = %#v, want first %q action", decision, action)
			}
		})
	}
}

func TestEvaluateMatchers(t *testing.T) {
	tests := []struct {
		name    string
		rule    string
		command string
	}{
		{name: "command wrapper and compound", rule: "type: command\n    match: rm", command: "sudo rm -rf /tmp/x && echo done"},
		{name: "sudo option value", rule: "type: command\n    match: rm", command: "sudo -u root rm -rf /tmp/x"},
		{name: "command extension", rule: "type: command\n    match: git", command: "git.remote helper"},
		{name: "command shell c payload", rule: "type: command\n    match: rm", command: "bash -lc 'rm -rf /tmp/x'"},
		{name: "docker exec payload", rule: "type: command\n    match: rm", command: "docker exec box rm -rf /tmp/x"},
		{name: "docker exec option value", rule: "type: command\n    match: rm", command: "docker exec -u root box rm -rf /tmp/x"},
		{name: "kubectl exec payload", rule: "type: command\n    match: rm", command: "kubectl exec pod -- rm -rf /tmp/x"},
		{name: "ssh payload", rule: "type: command\n    match: rm", command: "ssh host rm -rf /tmp/x"},
		{name: "xargs payload", rule: "type: command\n    match: rm", command: "xargs rm -rf /tmp/x"},
		{name: "find exec payload", rule: "type: command\n    match: rm", command: "find /tmp -exec rm -rf /tmp/x ;"},
		{name: "nested substitution", rule: "type: command\n    match: rm", command: "echo $(echo $(rm -rf /tmp/x))"},
		{name: "regex", rule: "type: regex\n    match: \"^git push\"", command: "git push origin branch"},
		{name: "glob slash matching", rule: "type: glob\n    match: \"git * origin/**\"", command: "git push origin/feature"},
		{name: "glob Unicode", rule: "type: glob\n    match: \"tool caf?\"", command: "tool café"},
		{name: "contains", rule: "type: contains\n    match: token=secret", command: "tool token=secret"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			policy := mustParse(t, "default: ask\nrules:\n  - name: hit\n    "+strings.ReplaceAll(test.rule, "\n    ", "\n    ")+"\n    action: block\n")
			decision := Evaluate(policy, test.command, false)
			if decision.Action != Block || decision.RuleName != "hit" {
				t.Fatalf("Evaluate() = %#v, want matching block rule", decision)
			}
		})
	}
}

func TestEvaluateHardSafetyFloor(t *testing.T) {
	policy := mustParse(t, "default: approve\nrules: []\n")
	tests := []struct {
		command string
		rule    string
	}{
		{command: "rm -rf /", rule: "built-in hard floor: rm root"},
		{command: "rm -rf /tmp/../*", rule: "built-in hard floor: rm root"},
		{command: "rm -rf /tmp/../*.txt", rule: "built-in hard floor: rm root"},
		{command: "rm -rf /{tmp/../*,etc}", rule: "built-in hard floor: rm root"},
		{command: "rm -rf $HOME", rule: "built-in hard floor: rm root"},
		{command: "rm -rf " + "$" + "{HOME}", rule: "built-in hard floor: rm root"},
		{command: "sudo rm --recursive /etc", rule: "built-in hard floor: rm root"},
		{command: "rmdir /", rule: "built-in hard floor: rmdir root"},
		{command: "mkfs.ext4 /dev/sda1", rule: "built-in hard floor: mkfs"},
		{command: "dd if=image of=/dev/nvme0n1", rule: "built-in hard floor: dd block device"},
		{command: "bash -lc 'mkfs.ext4 /dev/sda1'", rule: "built-in hard floor: mkfs"},
		{command: "docker exec -u root box rm -rf /", rule: "built-in hard floor: rm root"},
		{command: "echo $(rm -rf /)", rule: "built-in hard floor: rm root"},
		{command: "echo data > /dev/sda", rule: "built-in hard floor: block-device redirect"},
		{command: "f(){ f|f& };f", rule: "built-in hard floor: fork bomb"},
	}
	for _, test := range tests {
		t.Run(test.command, func(t *testing.T) {
			decision := Evaluate(policy, test.command, true)
			if decision.Action != Block || decision.RuleName != test.rule || decision.Source != "hard-floor" {
				t.Fatalf("Evaluate() = %#v, want hard-floor block %q", decision, test.rule)
			}
		})
	}
}

func TestEvaluateFailsClosedForAmbiguousDestructiveWrappers(t *testing.T) {
	policy := mustParse(t, "default: approve\nrules: []\n")
	for _, command := range []string{
		"xargs -a /dev/null rm -rf /",
		"sudo xargs -a /dev/null rm -rf /",
		"env -S 'rm -rf /'",
		"env -u YOLO_FLAG -S 'rm -rf /'",
		"YOLO_FLAG=1 env -S 'rm -rf /'",
		"env --split-string='rm -rf /'",
		"docker run --rm -v /:/host --entrypoint /bin/sh alpine -c 'rm -rf /host/*'",
		"docker run --mount type=bind,src=/tmp/..,target=/host --entrypoint /bin/sh alpine -c 'rm -rf /host/*'",
		"rm -rf /{,/etc}",
		"rm -rf /{, etc}",
	} {
		t.Run(command, func(t *testing.T) {
			decision := Evaluate(policy, command, false)
			if decision.Action != Ask || decision.Source != "error" || decision.ReasonCode != "command_parse_failed" {
				t.Fatalf("Evaluate(%q) = %#v, want fail-closed Ask", command, decision)
			}
		})
	}
}

func TestEvaluateFailsClosed(t *testing.T) {
	policy := mustParse(t, "default: approve\nrules:\n  - type: command\n    match: rm\n    action: block\n")
	tests := []struct {
		name    string
		policy  *Policy
		command string
		reason  string
	}{
		{name: "missing policy", policy: nil, command: "echo ok", reason: "rules_file_unavailable"},
		{name: "incomplete quote with command rule", policy: policy, command: "rm -rf '", reason: "command_parse_failed"},
		{name: "incomplete substitution", policy: policy, command: "echo $(rm -rf /", reason: "command_parse_failed"},
		{name: "incomplete backtick substitution", policy: policy, command: "echo " + string(rune(96)) + "rm -rf /", reason: "command_parse_failed"},
		{name: "unresolved command variable", policy: policy, command: "$YOLO_COMMAND -rf /", reason: "command_parse_failed"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			decision := Evaluate(test.policy, test.command, false)
			if decision.Action != Ask || decision.ReasonCode != test.reason || decision.Error == "" {
				t.Fatalf("Evaluate() = %#v, want ask with reason %q", decision, test.reason)
			}
		})
	}
}

func TestLoadAndEvaluateMissingOrMalformedRulesFailsClosed(t *testing.T) {
	dir := t.TempDir()
	for _, path := range []string{filepath.Join(dir, "missing.yaml"), filepath.Join(dir, "bad.yaml")} {
		if strings.HasSuffix(path, "bad.yaml") {
			if err := os.WriteFile(path, []byte("rules: ["), 0o600); err != nil {
				t.Fatal(err)
			}
		}
		decision := LoadAndEvaluate(path, "git status", false)
		if decision.Action != Ask || decision.ReasonCode != "rules_file_unavailable" || decision.Error == "" {
			t.Fatalf("LoadAndEvaluate(%q) = %#v, want fail-closed ask", path, decision)
		}
	}
}
