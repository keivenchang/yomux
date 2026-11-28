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
	for _, name := range []string{"codex", "claude", "claude-counter"} {
		t.Run(name, func(t *testing.T) {
			agentKind, usesCounter := strings.CutSuffix(name, "-counter")
			visible := ""
			observedAt := time.Unix(100, 0)
			sources := agentStatusSources("", nil)
			sources.CapturePane = func(context.Context, string) (AgentStatusPane, error) {
				return AgentStatusPane{VisibleText: visible, ObservedAt: observedAt}, nil
			}
			collector := NewAgentStatusCollector(func(context.Context) ([]AgentRecord, []error) {
				return []AgentRecord{{Kind: agentKind, TmuxSession: "s", TmuxWindow: "0", TmuxPaneID: "%1"}}, nil
			}, func(ctx context.Context, request AgentStatusRequest) AgentStatusResult {
				return ClassifyAgentStatus(ctx, request, sources)
			}, nil, nil, AgentStatusCollectorConfig{})
			frames := []rune("•◦○◌⠋⠙⠹✶✻✽✢·*")
			for index, frame := range frames {
				observedAt = time.Unix(int64(100+index*10), 0)
				if usesCounter {
					visible = fmt.Sprintf("%s Channelling… (%ds · ↓ 471 tokens)\n──────── Check status ──\n❯\n────────────────\n? for shortcuts · ← for agents", string(frame), index+1)
				} else if agentKind == "codex" {
					visible = fmt.Sprintf("Worked for 3s\n%s Working (%ds • esc to interrupt)\n\n› Ask Codex to do anything\nGPT-6.1-Sol · Goal achieved", string(frame), index+1)
				} else {
					visible = fmt.Sprintf("%s Channelling… (%ds · ↓ 471 tokens)\n────────────────\n❯\n────────────────\n⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← for agents", string(frame), index+1)
				}
				facts := collector.collect(context.Background(), HostFactContext{SourceID: "test", EpochID: "animation", CadenceSeconds: 10, ObservedAt: float64(100 + index*10)})
				row := facts.Observations[0].Payload["agents"].([]map[string]any)[0]
				want := "run"
				if usesCounter && index == 0 {
					want = "idle"
				}
				if row["state"] != want || row["working_stopped_ts"] != nil || row["transition_id"] != nil || row["cooldown_attention_key"] != nil {
					t.Errorf("animation frame %q invented a completion: %+v", frame, row)
				}
			}
			visible = "Worked for 14m 54s\n› Ask Codex to do anything"
			if agentKind == "claude" {
				visible = "✻ Cogitated for 14m 54s\n────────────────\n❯\n────────────────\n⏵⏵ bypass permissions on (shift+tab to cycle)"
			}
			var transitionID any
			for index := range 3 {
				observedAt = time.Unix(int64(300+index*10), 0)
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

func TestClaudeActivityUsesAdvancingCounterAndSpinner(t *testing.T) {
	composer := "\n────────────────\n❯\u00a0\n────────────────"
	update := "\n    ✔ Update installed · Restart to update"
	prefix := "✻ Churned for 1m 25s · done 5:19 PM\n" +
		"     (ctrl+b ctrl+b (twice) to run in background)\n\n"
	active := func(symbol, verb, elapsed string) string {
		return prefix + symbol + " " + verb + "… (" + elapsed + " · ↓ 1.1k tokens)" + update + composer
	}
	now := time.Unix(100, 0)
	sources := agentStatusSources("", func(_ context.Context, pid int) (AgentStatusProcess, error) {
		return AgentStatusProcess{PID: pid, StartedIdentity: "process", Alive: true}, nil
	})
	for _, sample := range []struct {
		name, text string
		seconds    int
		pid        int
		want       AgentStatus
	}{
		{"static Working label is not activity", active("✻", "Working", "1m 25s"), 0, 1, AgentStatusIdle},
		{"timer advances through verb change", active("✻", "Doodling", "1m 26s"), 1, 1, AgentStatusRun},
		{"same animation frame", active("✻", "Doodling", "1m 26s"), 2, 1, AgentStatusRun},
		{"older capture cannot erase progress", prefix + composer, 1, 1, AgentStatusRun},
		{"spinner tick with same time", active("✽", "Doodling", "1m 26s"), 19, 1, AgentStatusRun},
		{"bullet animation phase", active("•", "Doodling", "1m 26s"), 20, 1, AgentStatusRun},
		{"middle dot animation phase", active("·", "Doodling", "1m 26s"), 21, 1, AgentStatusRun},
		{"asterisk animation phase", active("*", "Doodling", "1m 26s"), 22, 1, AgentStatusRun},
		{"tick refreshes liveness", active("*", "Doodling", "1m 26s"), 24, 1, AgentStatusRun},
		{"stationary counter expires even with Working label", active("*", "Working", "1m 26s"), 43, 1, AgentStatusIdle},
		{"complete", prefix + composer, 44, 1, AgentStatusIdle},
		{"new run seeds its own counter", active("✻", "Cooking", "4s"), 45, 1, AgentStatusIdle},
		{"advance in new run", active("✻", "Cooking", "5s"), 46, 1, AgentStatusRun},
		{"process replacement", active("✽", "Cooking", "6s"), 47, 2, AgentStatusIdle},
		{"approval takes priority", active("✻", "Cooking", "7s") + "\nWould you like to run the following command?\nEnter to select", 48, 2, AgentStatusAsk},
		{"unrelated output separates old spinner from composer", prefix + "✻ Cooking… (8s · ↓ 1.1k tokens)\nA process printed a result." + composer, 49, 2, AgentStatusIdle},
		{"quoted example", prefix + "```\n✻ Cooking… (9s · ↓ 1.1k tokens)\n```" + composer, 50, 2, AgentStatusIdle},
	} {
		t.Run(sample.name, func(t *testing.T) {
			at := now.Add(time.Duration(sample.seconds) * time.Second)
			sources.CapturePane = func(context.Context, string) (AgentStatusPane, error) {
				return AgentStatusPane{VisibleText: sample.text, ObservedAt: at}, nil
			}
			got := ClassifyAgentStatus(context.Background(), AgentStatusRequest{
				Kind: "claude", PaneTarget: "%94", PID: sample.pid, Now: at,
			}, sources)
			if got.Status != sample.want {
				t.Fatalf("status = %q (%v), want %q", got.Status, got.Evidence, sample.want)
			}
		})
	}
}

func TestClaudeWorkingFooterSurvivesSubagentStatusChrome(t *testing.T) {
	const prefix = "* Orchestrating… (%s · ↓ 35.8k tokens)\n" +
		"\nTip: Use /clear to start fresh when switching topics and free\n" +
		"up context\n\n✔ Update installed · Restart to update\n" +
		"────────────────\n❯\u00a0\n────────────────\n" +
		"⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← for agents\n" +
		"› ● main\n◯ general-purpose Running test_cache… 39m 39s · ↓ 175.5k tokens"
	var visible string
	observedAt := time.Unix(1000, 0)
	sources := agentStatusSources("", func(_ context.Context, pid int) (AgentStatusProcess, error) {
		return AgentStatusProcess{PID: pid, StartedIdentity: "claude-process", Alive: true}, nil
	})
	sources.CapturePane = func(context.Context, string) (AgentStatusPane, error) {
		return AgentStatusPane{VisibleText: visible, ObservedAt: observedAt}, nil
	}
	classify := func() AgentStatusResult {
		return ClassifyAgentStatus(context.Background(), AgentStatusRequest{Kind: "claude", PID: 7, PaneTarget: "%7", Now: observedAt}, sources)
	}
	visible = fmt.Sprintf(prefix, "39m 57s")
	if got := classify(); got.Status != AgentStatusRun {
		t.Fatalf("Claude's active interrupt footer was hidden by subagent status chrome: %+v", got)
	}
	observedAt = observedAt.Add(time.Second)
	visible = fmt.Sprintf(prefix, "39m 58s")
	if got := classify(); got.Status != AgentStatusRun {
		t.Fatalf("advancing Claude timer below wrapped Tip chrome = %+v, want run", got)
	}
	observedAt = observedAt.Add(time.Second)
	visible = fmt.Sprintf(prefix, "39m 59s") + "\n────────────────\n❯\u00a0\n────────────────"
	if got := classify(); got.Status != AgentStatusIdle {
		t.Fatalf("a newer Claude composer must still supersede the older interrupt footer: %+v", got)
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
				if got := classifyVisibleAgentText("claude", visible); got.Status != AgentStatusRun {
					t.Errorf("%s: %+v", verb, got)
				}
				settled := strings.ReplaceAll(visible, " · esc to interrupt", "")
				if got := classifyVisibleAgentText("claude", settled); got.Status == AgentStatusRun {
					t.Errorf("idle footer: %+v", got)
				}
			}
		})
	}
}

func TestAgentStatusUnclosedToolFenceCannotHideLiveChrome(t *testing.T) {
	for kind, chrome := range map[string]string{
		"codex":  "○ Working (12s • esc to interrupt)\n› Ask Codex to do anything",
		"claude": "✻ Doodling…\n────────────────\n❯\n────────────────\n⏸ manual mode on · esc to interrupt · ← for agents",
	} {
		for _, content := range []string{"```\nstreamed tool output\n", "```python\nprint('hello')\n", "```\ncomplete example\n```\n```\nunclosed example\n"} {
			if got := classifyVisibleAgentText(kind, content+chrome); got.Status != AgentStatusRun {
				t.Errorf("tool fence hid live chrome: %+v", got)
			}
		}
	}
}

func TestAgentStatusEmptyUnicodeComposerAfterOutput(t *testing.T) {
	for _, prompt := range []string{"❯", "❯\u00a0", "›", "› "} {
		if got := classifyVisibleAgentText("codex", "Task completed successfully.\n"+prompt); got.Status != AgentStatusTransition {
			t.Errorf("explicit completion with empty composer %q: %+v", prompt, got)
		}
		visible := strings.Repeat("completed response output\n", 30) + "────────────────\n" + prompt + "\n────────────────\n⏸ manual mode on"
		if got := classifyVisibleAgentText("claude", visible); got.Status != AgentStatusIdle {
			t.Errorf("empty composer %q after a long response: %+v", prompt, got)
		}
	}
	for _, prompt := range []string{"❯ 1. Yes", "› 2. No"} {
		if got := classifyVisibleAgentText("codex", prompt); got.Status == AgentStatusIdle {
			t.Errorf("selected menu option %q became an idle composer", prompt)
		}
	}
}
