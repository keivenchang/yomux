// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/testsupport"
	"gopkg.in/yaml.v3"
)

// The prompt detector's real captures also qualify the runtime status reader:
// fabricated "Working..." rows missed both clients' actual animated chrome.
func TestAgentStatusRealWorkingCorpus(t *testing.T) {
	paths, err := filepath.Glob(filepath.Join(testsupport.RepoPath(t, "tests/fixtures/prompt_corpus/captures"), "*.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	counts := map[string]int{}
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		var fixture struct {
			Agent             string `yaml:"agent"`
			RawCapture        string `yaml:"raw_capture"`
			ExpectedScreenKey string `yaml:"expected_screen_key"`
			ExpectedPromoted  struct {
				ScreenKey string `yaml:"screen_key"`
			} `yaml:"expected_promoted"`
		}
		if err := yaml.Unmarshal(data, &fixture); err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		if fixture.Agent != "claude" && fixture.Agent != "codex" {
			continue
		}
		if fixture.ExpectedScreenKey != "working" && fixture.ExpectedPromoted.ScreenKey != "working" {
			continue
		}
		counts[fixture.Agent]++
		t.Run(filepath.Base(path), func(t *testing.T) {
			got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: fixture.Agent, PaneTarget: "%1"}, AgentStatusSources{
				CapturePane: func(context.Context, string) (AgentStatusPane, error) {
					return AgentStatusPane{VisibleText: fixture.RawCapture}, nil
				},
			})
			if got.Status != AgentStatusRun {
				t.Fatalf("real %s working capture classified as %+v", fixture.Agent, got)
			}
		})
	}
	if counts["claude"] < 6 || counts["codex"] < 3 {
		t.Fatalf("real working coverage missing: %v", counts)
	}
}

func TestAgentStatusAnimatedFramesNeverFinishRun(t *testing.T) {
	for _, kind := range []string{"codex", "claude"} {
		t.Run(kind, func(t *testing.T) {
			visible := ""
			collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
				return []AgentRecord{{Kind: kind, TmuxSession: "s", TmuxWindow: "0", TmuxPaneID: "%1"}}, nil
			}, func(ctx context.Context, request AgentStatusRequest) AgentStatusResult {
				return ClassifyAgentStatus(ctx, request, AgentStatusSources{CapturePane: func(context.Context, string) (AgentStatusPane, error) {
					return AgentStatusPane{VisibleText: visible}, nil
				}})
			}, nil, nil, AgentStatusCollectorConfig{})
			frames := []rune("•◦○◌⠋⠙⠹✶✻✽✢·*")
			for index, frame := range frames {
				if kind == "codex" {
					visible = fmt.Sprintf("Worked for 3s\n%s Working (%ds • esc to interrupt)\n\n› Ask Codex to do anything\nGPT-6.1-Sol · Goal achieved", string(frame), index+1)
				} else {
					visible = fmt.Sprintf("%s Channelling… (%ds · ↓ 471 tokens)\n────────────────\n❯\n────────────────\n⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← for agents", string(frame), index+1)
				}
				facts := collector.collect(context.Background(), HostFactContext{SourceID: "test", EpochID: "animation", CadenceSeconds: 10, ObservedAt: float64(100 + index*10)})
				row := facts.Observations[0].Payload["agents"].([]map[string]any)[0]
				if row["state"] != "run" || row["working_stopped_ts"] != nil || row["transition_id"] != nil || row["cooldown_attention_key"] != nil {
					t.Errorf("animation frame %q invented a completion: %+v", frame, row)
				}
			}
			visible = "Worked for 14m 54s\n› Ask Codex to do anything"
			if kind == "claude" {
				visible = "✻ Cogitated for 14m 54s\n────────────────\n❯\n────────────────\n⏵⏵ bypass permissions on (shift+tab to cycle)"
			}
			var transitionID any
			for index := range 3 {
				facts := collector.collect(context.Background(), HostFactContext{SourceID: "test", EpochID: "animation", CadenceSeconds: 10, ObservedAt: float64(300 + index*10)})
				row := facts.Observations[0].Payload["agents"].([]map[string]any)[0]
				if index == 0 {
					transitionID = row["transition_id"]
				}
				if row["state"] != "transition" || row["working_stopped_ts"] != float64(300) || transitionID == nil || row["transition_id"] != transitionID {
					t.Errorf("completion must have one stable lifecycle: %+v", row)
				}
			}
		})
	}
}

func TestAgentStatusInterruptTextIsNotWorkingChrome(t *testing.T) {
	for _, text := range []string{
		"The shortcut is esc to interrupt.\n› Ask Codex to do anything",
		"`○ Working (3s • esc to interrupt)`\n› Ask Codex to do anything",
		"> ○ Working (3s • esc to interrupt)\n› Ask Codex to do anything",
		"$ echo 'esc to interrupt'\n› Ask Codex to do anything",
		"❯ echo esc to interrupt\n────────────────\n⏵⏵ bypass permissions on (shift+tab to cycle)",
		"○ Working (3s • esc to interrupt)\nWorked for 3s\n› Ask Codex to do anything",
		"⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt\n✻ Worked for 3s\n────────────────\n❯\n────────────────\n⏵⏵ bypass permissions on (shift+tab to cycle)",
		"The footer was:\n⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt\n› Ask Codex to do anything",
		"```\n○ Working (3s • esc to interrupt)\n```\n› Ask Codex to do anything",
		"```\n❯\n────────────────\n⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt\n```\n› Ask Codex to do anything",
	} {
		t.Run(strings.ReplaceAll(text, "\n", "/"), func(t *testing.T) {
			got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "codex", PaneTarget: "%1", Now: time.Unix(100, 0)}, AgentStatusSources{CapturePane: func(context.Context, string) (AgentStatusPane, error) {
				return AgentStatusPane{VisibleText: text}, nil
			}})
			if got.Status == AgentStatusRun {
				t.Fatalf("non-current interrupt text became working: %+v", got)
			}
		})
	}
}

func TestAgentStatusClaudePermissionModeFooters(t *testing.T) {
	for _, footer := range []string{
		"⏸ manual mode on · esc to interrupt · ← for agents", // Claude 2.1.311, launched against held local provider.
		"⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt",
		"⏸ plan mode on (shift+tab to cycle) · esc to interrupt",
		"▶▶ auto mode on (shift+tab to cycle) · ← for agents · PR #93 · esc to interrupt",
		"⏵⏵ accept edits on (shift+tab to cycle) · PR #98 · esc to interrupt · ← for agents",
	} {
		t.Run(footer, func(t *testing.T) {
			for _, verb := range []string{"Doodling…", "Channelling…", "Compacting conversation…"} {
				visible := "✶ " + verb + "\n────────────────\n❯\n────────────────\n" + footer
				if got := classifyVisibleAgentText(visible); got.Status != AgentStatusRun {
					t.Errorf("%s: %+v", verb, got)
				}
				settled := strings.ReplaceAll(visible, " · esc to interrupt", "")
				if got := classifyVisibleAgentText(settled); got.Status == AgentStatusRun {
					t.Errorf("idle footer: %+v", got)
				}
			}
		})
	}
}

func TestAgentStatusUnclosedToolFenceCannotHideLiveChrome(t *testing.T) {
	for _, chrome := range []string{
		"○ Working (12s • esc to interrupt)\n› Ask Codex to do anything",
		"✻ Doodling…\n────────────────\n❯\n────────────────\n⏸ manual mode on · esc to interrupt · ← for agents",
	} {
		for _, content := range []string{"```\nstreamed tool output\n", "```python\nprint('hello')\n", "```\ncomplete example\n```\n```\nunclosed example\n"} {
			if got := classifyVisibleAgentText(content + chrome); got.Status != AgentStatusRun {
				t.Errorf("tool fence hid live chrome: %+v", got)
			}
		}
	}
}

func TestAgentStatusEmptyUnicodeComposerAfterOutput(t *testing.T) {
	for _, prompt := range []string{"❯", "❯\u00a0", "›", "› "} {
		if got := classifyVisibleAgentText("Task completed successfully.\n" + prompt); got.Status != AgentStatusTransition {
			t.Errorf("explicit completion with empty composer %q: %+v", prompt, got)
		}
		visible := strings.Repeat("completed response output\n", 30) + "────────────────\n" + prompt + "\n────────────────\n⏸ manual mode on"
		if got := classifyVisibleAgentText(visible); got.Status != AgentStatusIdle {
			t.Errorf("empty composer %q after a long response: %+v", prompt, got)
		}
	}
	for _, prompt := range []string{"❯ 1. Yes", "› 2. No"} {
		if got := classifyVisibleAgentText(prompt); got.Status == AgentStatusIdle {
			t.Errorf("selected menu option %q became an idle composer", prompt)
		}
	}
}
