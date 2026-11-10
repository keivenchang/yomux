// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yoagent

import (
	"encoding/json"
	"testing"
	"time"
)

func TestModelIntentFileAndRuntimeAdmissionAgree(t *testing.T) {
	for _, example := range []struct {
		name   string
		change func(*Skill)
		valid  bool
	}{
		{"valid", func(*Skill) {}, true},
		{"scalar-default", func(s *Skill) { s.ModelIntent["defaults"] = map[string]any{"notify": true} }, true},
		{"null-default", func(s *Skill) { s.ModelIntent["defaults"] = map[string]any{"value": nil} }, false},
		{"object-default", func(s *Skill) { s.ModelIntent["defaults"] = map[string]any{"value": map[string]any{}} }, false},
		{"list-default", func(s *Skill) { s.ModelIntent["defaults"] = map[string]any{"value": []any{}} }, false},
		{"disabled", func(s *Skill) { s.Enabled = false }, false},
		{"user", func(s *Skill) { s.Builtin = false }, false},
		{"invalid-name", func(s *Skill) { s.Name = "Bad Name" }, false},
		{"unknown-handler", func(s *Skill) { s.ModelIntent["handler"] = "unknown-handler" }, false},
		{"invalid-priority", func(s *Skill) { object(s.ModelIntent["candidate"])["priority"] = 101 }, false},
		{"unknown-schema", func(s *Skill) { object(s.ModelIntent["output"])["schema"] = "unknown-schema" }, false},
	} {
		t.Run(example.name, func(t *testing.T) {
			skill := testLoopIntentSkill()
			example.change(&skill)
			encoded, err := json.Marshal(map[string]any{
				"name": skill.Name, "description": "Plan a bounded send", "enabled": skill.Enabled, "model_intent": skill.ModelIntent,
			})
			if err != nil {
				t.Fatal(err)
			}
			_, fileErr := parseSkill(encoded, "fixture.yaml", skill.Name, skill.Builtin)
			_, runtimeAccepted := modelIntentFromSkill(skill)
			if (fileErr == nil) != example.valid || runtimeAccepted != example.valid {
				t.Fatalf("file err=%v runtime accepted=%t, want valid=%t", fileErr, runtimeAccepted, example.valid)
			}
		})
	}
}

func testLoopIntentSkill() Skill {
	return Skill{
		Name: "loop-send", Enabled: true, Builtin: true,
		ModelIntent: map[string]any{
			"handler": "loop-send", "confirmation": "none",
			"defaults":  map[string]any{"interval_seconds": 30, "max_runs": 10},
			"candidate": map[string]any{"any_of": []any{"loop", "repeat", "every"}, "require_any": []any{"ask", "tell", "send", "run", "have"}, "min_known_sessions": 1, "session_scope": "request", "priority": 100},
			"output":    map[string]any{"tag": "yoagent-loop-plan", "schema": "loop-send"},
		},
	}
}

func TestModelIntentSelectionSchemaValidationAndJobPayload(t *testing.T) {
	intent := SelectModelIntent([]Skill{testLoopIntentSkill()}, "Every 5 seconds have session 1 ask for status twice.", []string{"1", "2"})
	if intent == nil || intent.Handler != "loop-send" {
		t.Fatalf("selected intent = %#v", intent)
	}
	providerSchema := ModelIntentProviderSchema(*intent, []string{"1", "2"})
	sessionSchema := object(object(providerSchema["properties"])["session"])
	if sessionSchema["enum"] == nil || sessionSchema["format"] != nil {
		t.Fatalf("known-session schema was not lowered: %#v", sessionSchema)
	}
	plan := ParseModelIntentPlan(`{"session":"1","prompt":"check status","interval_seconds":5,"max_runs":2}`, *intent, []string{"1", "2"})
	if plan == nil {
		t.Fatal("valid model plan was rejected")
	}
	if invalid := ParseModelIntentPlan(`{"session":"other","prompt":"check status","interval_seconds":5,"max_runs":2}`, *intent, []string{"1", "2"}); invalid != nil {
		t.Fatalf("unknown session plan accepted: %#v", invalid)
	}
	if invalid := ParseModelIntentPlan(`{"session":"1","prompt":"check status","interval_seconds":5,"max_runs":2,"shell":"echo unsafe"}`, *intent, []string{"1", "2"}); invalid != nil {
		t.Fatalf("additional model field accepted: %#v", invalid)
	}
	if invalid := ParseModelIntentPlan(`{"session":"1","prompt":"check status","interval_seconds":5,"max_runs":2} trailing`, *intent, []string{"1", "2"}); invalid != nil {
		t.Fatalf("trailing model output accepted: %#v", invalid)
	}
	job, err := BuildJob(ModelIntentJobPayload(*intent, plan), []string{"1", "2"}, "operator", time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if job["type"] != "loop_send" || job["max_runs"] != 2 || job["confirm_required"] != false {
		t.Fatalf("validated plan job = %#v", job)
	}
}

func TestModelIntentSelectionRequiresUniqueHighestPriorityAndKnownTargets(t *testing.T) {
	skill := testLoopIntentSkill()
	if selected := SelectModelIntent([]Skill{skill}, "every 5 seconds have session 9 ask for status", []string{"1", "2"}); selected != nil {
		t.Fatalf("unknown session triggered intent: %#v", selected)
	}
	duplicate := testLoopIntentSkill()
	duplicate.Name = "loop-again"
	duplicate.ModelIntent = cloneModelIntentMap(skill.ModelIntent)
	if selected := SelectModelIntent([]Skill{skill, duplicate}, "every 5 seconds have session 1 ask for status", []string{"1"}); selected != nil {
		t.Fatalf("ambiguous top-priority intent selected: %#v", selected)
	}
	userSkill := skill
	userSkill.Builtin = false
	if selected := SelectModelIntent([]Skill{userSkill}, "every 5 seconds have session 1 ask for status", []string{"1"}); selected != nil {
		t.Fatalf("user skill triggered built-in model intent: %#v", selected)
	}
}

func cloneModelIntentMap(value map[string]any) map[string]any {
	copyValue := make(map[string]any, len(value))
	for key, item := range value {
		copyValue[key] = item
	}
	return copyValue
}
