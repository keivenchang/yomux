// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yolo

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

func TestDockerMountPolicyProgressAndRootAdmission(t *testing.T) {
	policy := mustParse(t, "default: approve\n")
	for _, engine := range []string{"docker", "podman"} {
		for _, source := range []string{"/tmp", "/"} {
			for _, options := range []string{
				"--volume=" + source + ":/data", "-v=" + source + ":/data", "-v" + source + ":/data",
				"--volume " + source + ":/data", "-v " + source + ":/data",
				"--mount=type=bind,src=" + source + ",target=/data", "--mount type=bind,source=" + source + ",target=/data",
				"--name=fixture --volume=" + source + ":/data --network=none",
				"--name fixture --volume " + source + ":/data --network none",
				"-itv" + source + ":/data", "-ditv " + source + ":/data",
				"--cpus=1 -itv" + source + ":/data",
			} {
				command := engine + " run " + options + " image echo ok"
				if !t.Run(command, func(t *testing.T) {
					done := make(chan Decision, 1)
					go func() { done <- Evaluate(policy, command, false) }()
					select {
					case decision := <-done:
						want := Approve
						if source == "/" {
							want = Ask
						}
						if decision.Action != want {
							t.Fatalf("mount admission=%s, want %s", decision.Action, want)
						}
					case <-time.After(time.Second):
						t.Fatal("mount option parser did not advance")
					}
				}) {
					return
				}
			}
		}
		for _, options := range []string{"--volume", "-v", "--mount", "--volume=", "-v=", "--mount="} {
			if decision := Evaluate(policy, engine+" run "+options, false); decision.Action != Ask {
				t.Fatalf("missing mount value was approved: %s %s", engine, options)
			}
		}
	}
	for _, options := range [][]string{{"--", "--volume=/:/data"}, {"image", "--volume=/:/data"}} {
		if parsed := parseDockerOptions(options); parsed.mountsRoot || parsed.ambiguous {
			t.Fatal("image or post-separator arguments were classified as wrapper mounts")
		}
	}
}

func TestDockerOptionBoundaryAdmission(t *testing.T) {
	policy := mustParse(t, "default: approve\n")
	for _, engine := range []string{"docker", "podman"} {
		for _, action := range []string{"run", "exec", "container run", "container exec"} {
			for _, options := range []string{"--cpus 1", "--hostname fixture", "--future-option value", "-X value", "-itX value", "-X=value"} {
				command := engine + " " + action + " " + options + " --volume=/:/host image true"
				if decision := Evaluate(policy, command, false); decision.Action != Ask {
					t.Errorf("unknown wrapper boundary admitted: %s decision=%+v", command, decision)
				}
			}
			command := engine + " " + action + " -itu root -- image rm -rf /"
			if decision := Evaluate(policy, command, false); decision.Action != Block {
				t.Errorf("shared parser missed destructive payload: %s decision=%+v", command, decision)
			}
		}
		for _, command := range []string{engine + " --context fixture run -v/:/host image true", engine + " --host=fixture container exec image rm -rf /"} {
			if decision := Evaluate(policy, command, true); decision.Action != Ask {
				t.Errorf("unknown global wrapper admitted: %s decision=%+v", command, decision)
			}
		}
		for _, options := range []string{"-itv/:/host", "-ditv /:/host", "--cpus=1 --hostname=fixture -itv/:/host"} {
			command := engine + " run " + options + " image true"
			if decision := Evaluate(policy, command, true); decision.Action != Ask {
				t.Errorf("root mount bypassed hard floor: %s decision=%+v", command, decision)
			}
		}
	}

}

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

func TestShellNewlineSafety(t *testing.T) {
	policy := mustParse(t, "default: approve\nrules: []\n")
	for _, command := range []string{
		"echo safe\nrm -rf /",
		"echo safe\nmkfs.ext4 /dev/sda1",
		"\\\n rm -rf /",
		"echo safe; \\\n \\\n rm -rf /",
		"echo $\\\n(rm -rf /)",
		"echo \"$\\\n(rm -rf /)\"",
		"echo safe >\\\n /dev/sda",
		"echo safe >/dev/s\\\nda",
		"bash -c 'echo safe >\\\n /dev/sda'",
		"echo $(echo safe >\\\n /dev/sda)",
	} {
		if got := Evaluate(policy, command, false); got.Action != Block {
			t.Fatalf("%q = %+v, want block", command, got)
		}
	}
	for _, command := range []string{"echo '\n' rm -rf /", "echo \"\n\" rm -rf /", "echo 'safe\nrm -rf /'", "echo safe\\\nrm -rf /", "echo \"safe\\\nrm -rf /\""} {
		if got := Evaluate(policy, command, false); got.Action != Approve {
			t.Fatalf("%q = %+v, want approve", command, got)
		}
	}
	if got := Evaluate(policy, "echo safe # comment \\\n rm -rf /", false); got.Action != Ask {
		t.Fatalf("continuation in a possible comment = %+v, want manual approval", got)
	}
}

func TestShellContinuationDoesNotCreateAToken(t *testing.T) {
	for _, test := range []struct {
		input string
		want  []string
	}{
		{"\\\nprintf ok", []string{"printf", "ok"}},
		{"printf \\\n ok", []string{"printf", "ok"}},
		{"pri\\\nntf ok", []string{"printf", "ok"}},
		{"printf \"\\\n\" ok", []string{"printf", "", "ok"}},
		{"printf '\\\n' ok", []string{"printf", "\\\n", "ok"}},
		{"printf \\\\\nnext", []string{"printf", "\\", ";", "next"}},
		{"printf ok &\\\n& printf next", []string{"printf", "ok", "&&", "printf", "next"}},
	} {
		got, err := shellTokens(test.input)
		if err != nil || !slices.Equal(got, test.want) {
			t.Errorf("shellTokens(%q) = %q, %v, want %q", test.input, got, err, test.want)
		}
	}
}

func TestCodexCommandRowInterpretations(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rules.yaml")
	for _, test := range []struct {
		name, rows, rules string
		want              Action
	}{
		{"escaped space before newline", "echo harmless \\ \nrm -rf /", "default: approve\n", Block},
		{"newline hard floor", "echo harmless\nrm -rf /", "default: approve\n", Block},
		{"continued command hard floor", "echo harmless; \\\nrm -rf /", "default: approve\n", Block},
		{"continued substitution hard floor", "echo $\\\n(rm -rf /)", "default: approve\n", Block},
		{"mixed hard floor", "echo harmless\nrm\n-rf /", "default: approve\n", Block},
		{"mixed soft decision", "echo harmless\nrm\n/tmp/fixture", "default: approve\nrules:\n  - type: regex\n    match: '^echo harmless\\nrm /tmp/fixture$'\n    action: decline\n", Ask},
		{"wrapped safe", "echo harmless\nfixture", "default: approve\n", Approve},
		{"wrapped word crosses rule boundary", "echo /tmp/private-\nfixture", "default: approve\nrules:\n  - type: regex\n    match: '/tmp/private-fixture'\n    action: decline\n", Ask},
		{"limit", strings.Repeat("echo harmless\n", 9) + "fixture", "default: approve\n", Ask},
		{"unsupported exec", "echo harmless\nexec rm -rf /", "default: approve\n", Ask},
		{"unsupported trap", "echo harmless\ntrap 'rm -rf /' EXIT", "default: approve\n", Ask},
		{"single row normalization", "echo harmless  ", "default: ask\nrules:\n  - type: regex\n    match: '^echo harmless$'\n    action: approve\n", Approve},
		{"unsupported control flow", "echo harmless\nif true; then rm -rf /; fi", "default: approve\n", Ask},
		{"unsupported nested control flow", "echo harmless\nbash -c 'if true; then rm -rf /; fi'", "default: approve\n", Ask},
		{"unsupported heredoc", "cat <<EOF\nfixture\nEOF", "default: approve\n", Ask},
		{"unsupported", "echo harmless\n$COMMAND", "default: approve\n", Ask},
		{"soft block", "echo harmless\nfixture", "default: block\n", Ask},
		{"decline", "echo harmless\nfixture", "default: decline\n", Ask},
		{"five breaks", strings.Repeat("echo harmless\n", 5) + "fixture", "default: approve\n", Approve},
		{"six breaks", strings.Repeat("echo harmless\n", 6) + "fixture", "default: approve\n", Ask},
	} {
		t.Run(test.name, func(t *testing.T) {
			if err := os.WriteFile(path, []byte(test.rules), 0600); err != nil {
				t.Fatal(err)
			}
			prompt := ClassifyPrompt("Would you like to run the following command?\nEnvironment: local\n$ "+test.rows+"\n› 1. Yes, proceed (y)\n  2. No (esc)\nPress enter to confirm or esc to cancel", "")
			if !prompt.Actionable {
				t.Fatalf("fixture = %+v", prompt)
			}
			if got := LoadAndEvaluatePrompt(path, prompt, false); got.Action != test.want {
				t.Fatalf("decision = %+v, want %s", got, test.want)
			}
		})
	}
}
