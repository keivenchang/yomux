// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yoagent

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadSkillSnapshotOverlaysUserSkillAndContext(t *testing.T) {
	root, config := t.TempDir(), t.TempDir()
	builtin := filepath.Join(root, "yomux_lib", "yoagent")
	for _, directory := range []string{filepath.Join(builtin, "builtin_skills"), filepath.Join(builtin, "builtin_context"), filepath.Join(config, "skills.d"), filepath.Join(config, "context.d")} {
		if err := os.MkdirAll(directory, 0700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(builtin, "builtin_skills", "status-check.yaml"), []byte("name: status-check\nkind: workflow\ndescription: Built-in status workflow.\ntools: [read_activity]\ntriggers: [status]\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(config, "skills.d", "status-check.yaml"), []byte("name: status-check\nkind: workflow\ndescription: User status workflow.\ntools: [read_activity]\ntriggers: [status]\nenabled: false\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(config, "context.d", "project.md"), []byte("Keep answers specific to this project.\n"), 0600); err != nil {
		t.Fatal(err)
	}
	snapshot, err := LoadSkillSnapshot(root, config)
	if err != nil {
		t.Fatal(err)
	}
	if !snapshot.OK || len(snapshot.Skills) != 1 || snapshot.Skills[0].Builtin || snapshot.Skills[0].Enabled || len(snapshot.Contexts) != 1 || len(snapshot.ContextLines) != 1 {
		t.Fatalf("snapshot=%+v", snapshot)
	}
}

func TestUserSkillFilesValidateAndUsePrivateAtomicStorage(t *testing.T) {
	config := t.TempDir()
	text := "name: local-status\nkind: workflow\ndescription: Ask for local status.\ntools: [read_activity]\ntriggers: [status]\n"
	file, err := WriteUserSkillFile(context.Background(), config, "skill", "local-status.yaml", text)
	if err != nil || file["valid"] != true {
		t.Fatalf("file=%v err=%v", file, err)
	}
	path := filepath.Join(config, "skills.d", "local-status.yaml")
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("skill file mode=%v err=%v", info, err)
	}
	if _, err := WriteUserSkillFile(context.Background(), config, "skill", "local-status", "name: local-status\ndescription: invalid tool\ntools: [shell]\n"); err == nil {
		t.Fatal("unknown tool was accepted")
	}
	if _, err := WriteUserSkillFile(context.Background(), config, "skill", "local-status", "name: another-skill\ndescription: wrong destination\ntools: [read_activity]\n"); err == nil {
		t.Fatal("skill name different from the destination filename was accepted")
	}
	if _, err := WriteUserSkillFile(context.Background(), config, "skill", "../outside", text); err == nil {
		t.Fatal("path traversal was accepted")
	}
	deleted, err := DeleteUserSkillFile(context.Background(), config, "skill", "local-status")
	if err != nil || deleted["deleted"] != true {
		t.Fatalf("delete result=%v err=%v", deleted, err)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("deleted skill still exists: %v", err)
	}
}

func TestSkillDirectoryIgnoresSymlinksAndReportsInvalidFiles(t *testing.T) {
	root, config := t.TempDir(), t.TempDir()
	directory := filepath.Join(root, "yomux_lib", "yoagent", "builtin_skills")
	if err := os.MkdirAll(directory, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(root, "outside"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "outside", "secret.yaml"), []byte("name: secret\ndescription: must not load\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(root, "outside", "secret.yaml"), filepath.Join(directory, "linked.yaml")); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "broken.yaml"), []byte("name: broken\nkind: workflow\n"), 0600); err != nil {
		t.Fatal(err)
	}
	snapshot, err := LoadSkillSnapshot(root, config)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.OK || len(snapshot.Skills) != 0 || len(snapshot.Errors) != 1 {
		t.Fatalf("invalid skill snapshot=%+v", snapshot)
	}
}

func TestBuiltinModelIntentValidationMatchesDeclarativeContract(t *testing.T) {
	valid := `name: delegated-prompt
kind: workflow
description: Delegate a prompt.
enabled: true
model_intent:
  handler: wait-then-send
  confirmation: required
  defaults:
    quiet_seconds: 3
  candidate:
    any_of: [research, investigate]
    require_any: []
    collective_terms: []
    min_known_sessions: 1
    session_scope: condition
    priority: 30
  output:
    tag: yoagent-delegated-prompt
    schema: delegated-prompt
`
	skill, err := parseSkill([]byte(valid), "builtin/delegated-prompt.yaml", "delegated-prompt", true)
	if err != nil || skill.ModelIntent == nil {
		t.Fatalf("valid built-in model intent = %#v, %v", skill, err)
	}
	for _, test := range []struct {
		name  string
		input string
	}{
		{name: "user-local", input: valid},
		{name: "disabled", input: strings.Replace(valid, "enabled: true", "enabled: false", 1)},
		{name: "missing handler", input: strings.Replace(valid, "handler: wait-then-send", "handler: invalid handler", 1)},
		{name: "empty candidates", input: strings.Replace(valid, "any_of: [research, investigate]", "any_of: []", 1)},
		{name: "unsupported schema template", input: strings.Replace(valid, "schema: delegated-prompt", "schema: unknown-plan", 1)},
		{name: "nested default", input: strings.Replace(valid, "quiet_seconds: 3", "quiet_seconds: {seconds: 3}", 1)},
	} {
		t.Run(test.name, func(t *testing.T) {
			builtin := test.name != "user-local"
			_, err := parseSkill([]byte(test.input), "skill.yaml", "delegated-prompt", builtin)
			if err == nil {
				t.Fatal("invalid model intent was accepted")
			}
		})
	}
}
