// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/keivenchang/yomux/internal/capabilities"
)

func TestNativeYOAgentSkillFileRoutesReadWriteAndDelete(t *testing.T) {
	root, config := t.TempDir(), t.TempDir()
	builtin := filepath.Join(root, "yomux_lib", "yoagent", "builtin_skills")
	if err := os.MkdirAll(builtin, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(builtin, "built-in.yaml"), []byte("name: built-in\nkind: workflow\ndescription: Built in.\ntools: [read_activity]\n"), 0600); err != nil {
		t.Fatal(err)
	}
	s := &server{root: root, config: config, events: newEventBroker()}

	get := httptest.NewRecorder()
	s.yoagentSkills(get, httptest.NewRequest(http.MethodGet, "/api/yoagent/skills", nil))
	var skills map[string]any
	if err := json.Unmarshal(get.Body.Bytes(), &skills); err != nil {
		t.Fatal(err)
	}
	if get.Code != http.StatusOK || skills["ok"] != true || len(skills["skills"].([]any)) != 1 {
		t.Fatalf("skills response=%d %s", get.Code, get.Body.String())
	}

	body := "{\"kind\":\"skill\",\"name\":\"local-status\",\"text\":\"name: local-status\\nkind: workflow\\ndescription: Ask for status.\\ntools: [read_activity]\\ntriggers: [status]\\n\"}"
	upsert := httptest.NewRecorder()
	s.yoagentSkillFileUpsert(upsert, httptest.NewRequest(http.MethodPost, "/api/yoagent/skill-files/upsert", strings.NewReader(body)))
	if upsert.Code != http.StatusOK || !strings.Contains(upsert.Body.String(), "\"name\":\"local-status\"") {
		t.Fatalf("upsert response=%d %s", upsert.Code, upsert.Body.String())
	}
	path := filepath.Join(config, "skills.d", "local-status.yaml")
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("persisted skill mode=%v err=%v", info, err)
	}

	fileResponse := httptest.NewRecorder()
	s.yoagentSkillFiles(fileResponse, httptest.NewRequest(http.MethodGet, "/api/yoagent/skill-files?kind=skill&name=local-status", nil))
	if fileResponse.Code != http.StatusOK || !strings.Contains(fileResponse.Body.String(), "Ask for status.") {
		t.Fatalf("skill read response=%d %s", fileResponse.Code, fileResponse.Body.String())
	}

	deleted := httptest.NewRecorder()
	s.yoagentSkillFileDelete(deleted, httptest.NewRequest(http.MethodPost, "/api/yoagent/skill-files/delete", strings.NewReader("{\"kind\":\"skill\",\"name\":\"local-status\"}")))
	if deleted.Code != http.StatusOK || !strings.Contains(deleted.Body.String(), "\"deleted\":true") {
		t.Fatalf("delete response=%d %s", deleted.Code, deleted.Body.String())
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("skill file remains after delete: %v", err)
	}
}

func TestNativeYOAgentSkillRoutesAreAdminOnlyAndCapabilityGated(t *testing.T) {
	for _, path := range []string{"/api/yoagent/skills", "/api/yoagent/skill-files", "/api/yoagent/skill-files/upsert", "/api/yoagent/skill-files/delete"} {
		method := http.MethodGet
		if strings.HasSuffix(path, "/upsert") || strings.HasSuffix(path, "/delete") {
			method = http.MethodPost
		}
		policy := capabilities.Policy(method, path)
		if policy.Path == "" || policy.Capability != "pane.agent.v1" || policy.Role != "admin" {
			t.Fatalf("route policy for %s %s = %+v", method, path, policy)
		}
	}
}
