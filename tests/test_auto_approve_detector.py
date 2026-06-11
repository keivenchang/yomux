import json
import os

import pytest


import auto_approve_tmux
from yolomux_lib import approvals
from yolomux_lib import prompt_detector
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import SessionInfo


def claude_bash_prompt_with_footer(*footer_lines):
    return "\n".join([
        "Bash command (unsandboxed)",
        "",
        "   echo one",
        "   Pause before continuing",
        "",
        " Permission rule Bash requires confirmation for this command.",
        "",
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. No",
        "",
        *footer_lines,
    ])


def test_auto_approve_tmux_reexports_detector_helpers():
    assert auto_approve_tmux.detect_prompt is prompt_detector.detect_prompt
    assert auto_approve_tmux.extract_command is prompt_detector.extract_command
    assert auto_approve_tmux.approval_prompt_state is prompt_detector.approval_prompt_state
    assert auto_approve_tmux.prompt_hash is prompt_detector.prompt_hash


def test_approval_detection_pipeline_has_one_shared_owner():
    # The read-path (app.py), the act-path worker (via the auto_approve_tmux module seam), and the CLI
    # must all run the SAME detection functions — no parallel re-implementation. Detection lives in
    # yolomux_lib.approvals; the root CLI re-exports it and app.py imports it directly. Identity here
    # means a future divergent copy fails this test instead of silently drifting the two paths apart.
    from yolomux_lib import app as app_module
    from yolomux_lib import approvals

    assert auto_approve_tmux.hybrid_approval_prompt_state is approvals.hybrid_approval_prompt_state
    assert auto_approve_tmux.transcript_approval_prompt_state is approvals.transcript_approval_prompt_state
    assert auto_approve_tmux.blank_prompt_state is approvals.blank_prompt_state
    assert auto_approve_tmux.PROMPT_RETRY_SECONDS == approvals.PROMPT_RETRY_SECONDS
    assert app_module.hybrid_approval_prompt_state is approvals.hybrid_approval_prompt_state
    assert app_module.blank_prompt_state is approvals.blank_prompt_state


def test_standalone_bash_decision_fails_closed_without_extracted_command(monkeypatch):
    def unexpected_evaluate(*_args, **_kwargs):
        raise AssertionError("missing commands must not fall through to rule evaluation")

    monkeypatch.setattr(auto_approve_tmux.yolo_rules, "evaluate", unexpected_evaluate)

    decision = auto_approve_tmux.standalone_bash_decision(None, "6")

    assert decision["action"] == "ask"
    assert decision["command_missing"] is True
    assert decision["rule_name"] == "command extraction failed"


def test_standalone_bash_decision_uses_yolo_rule_engine(monkeypatch):
    calls = []

    def fake_evaluate(cmd, prompt_type="bash", agent="", session="", dangerously_yolo=False):
        calls.append((cmd, prompt_type, agent, session, dangerously_yolo))
        return {
            "action": "notify",
            "rule_name": "custom default",
            "risk": "unknown",
            "source": "file",
            "path": "/tmp/yolo-rules.yaml",
        }

    monkeypatch.setattr(auto_approve_tmux.yolo_rules, "evaluate", fake_evaluate)

    decision = auto_approve_tmux.standalone_bash_decision("python3 script.py", "6")

    assert calls == [("python3 script.py", "bash", "", "6", False)]
    assert decision["action"] == "notify"
    assert decision["rule_name"] == "custom default"
    assert decision["command_missing"] is False


def test_hybrid_approval_prompt_state_uses_recent_transcript_when_pane_header_is_missing(monkeypatch, tmp_path):
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "make test"}}],
            },
        }) + "\n",
        encoding="utf-8",
    )
    info = SessionInfo(
        session="6",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=123,
                pane_target="6:0.0",
                command="claude",
                cwd=None,
                status=None,
                session_id="session-6",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    monkeypatch.setattr(approvals, "discover_sessions", lambda sessions: ({"6": info}, []))

    state = auto_approve_tmux.hybrid_approval_prompt_state("6", "❯ 1. Yes\n  2. No")

    assert state["visible"] is True
    assert state["source"] == "transcript"
    assert state["type"] == "bash"
    assert state["command"] == "make test"
    assert state["selected_option"] == 1


def test_hybrid_approval_prompt_state_does_not_use_transcript_without_visible_selector(monkeypatch, tmp_path):
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "make test"}}],
            },
        }) + "\n",
        encoding="utf-8",
    )
    info = SessionInfo(
        session="6",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=123,
                pane_target="6:0.0",
                command="claude",
                cwd=None,
                status=None,
                session_id="session-6",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    monkeypatch.setattr(approvals, "discover_sessions", lambda sessions: ({"6": info}, []))

    state = auto_approve_tmux.hybrid_approval_prompt_state("6", "agent is thinking")

    assert state["visible"] is False
    assert state["type"] == ""
    assert "no visible selectable prompt" in state["reason"]


def test_extract_command_rejoins_wrapped_codex_command():
    visible_text = "\n".join([
        "  Would you like to run the following command?",
        "",
        "  Reason: Verify the persisted YOLOmux settings file now defaults",
        "  the editor font size to 13.",
        "",
        '  $ python3 -c "from yolomux_lib.settings import settings_payload;',
        '  print(settings_payload()[\'settings\'][\'appearance\'][\'editor_font_size\'])"',
        "",
        "› 1. Yes, proceed (y)",
        "  2. No, and tell Codex what to do differently (esc)",
        "",
        "  Press enter to confirm or esc to cancel",
    ])

    assert prompt_detector.extract_command(visible_text) == (
        'python3 -c "from yolomux_lib.settings import settings_payload; '
        'print(settings_payload()[\'settings\'][\'appearance\'][\'editor_font_size\'])"'
    )


def test_extract_command_does_not_truncate_at_a_safe_complete_prefix():
    # #61: `git push origin main` parses shlex-complete, but the dangerous tail wraps onto the next
    # visual line — the FULL joined command must be classified, not the safe-looking prefix.
    visible_text = "\n".join([
        "  Would you like to run the following command?",
        "  Reason: push the branch",
        "  $ git push origin main",
        "    --force-with-lease --no-verify",
        "› 1. Yes, proceed (y)",
        "  2. No, and tell Codex what to do differently (esc)",
        "  Press enter to confirm or esc to cancel",
    ])
    assert prompt_detector.extract_command(visible_text) == "git push origin main --force-with-lease --no-verify"


def test_extract_command_returns_none_when_codex_block_has_no_selector():
    # #61: a capture that ends WITHOUT the selector may be truncated mid-command, so it is treated as
    # incomplete (None) and the caller falls to `ask` instead of trusting a prefix.
    visible_text = "\n".join([
        "  Would you like to run the following command?",
        "  Reason: clean up",
        "  $ rm -rf /tmp/build && curl http://example",
    ])
    assert prompt_detector.extract_command(visible_text) is None


def test_extract_command_anchors_to_bash_call_arg_not_prose():
    # anchor to the ● Bash(...) arg — never fold the description prose into the command.
    visible_text = "\n".join([
        "● Bash(git status --short)",
        "  Show the working tree status so we can decide what to commit next and keep the repo tidy",
        " Permission rule Bash requires confirmation for this command.",
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. No",
    ])
    assert prompt_detector.extract_command(visible_text) == "git status --short"


def test_extract_command_does_not_cross_separator_into_prior_step():
    # the LIVE Claude prompt shows the command in the box with NO `● Bash()` (that renders only
    # after approval). The `● Bash()` anchor search must be bounded to the current block so it does not
    # walk past the `─────` separator and return the PREVIOUS step's (stale, safe-looking) command.
    visible_text = "\n".join([
        "● Bash(chmod +x scripts/deploy.sh)",
        "  ⎿  ok",
        "● Done.",
        "──────────────────────────────────────────────",
        " Bash command (unsandboxed)",
        "",
        "   cp -r src/ dist/",
        "   [3/10] Copy sources into dist/",
        "",
        " Permission rule Bash requires confirmation for this command.",
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. No",
    ])
    cmd = prompt_detector.extract_command(visible_text)
    assert cmd is not None and cmd.startswith("cp -r src/ dist/")
    assert "chmod" not in cmd


def test_extract_command_does_not_fold_long_description_prose():
    # a long description line (no shell metacharacters) is NOT folded into the command.
    visible_text = "\n".join([
        "Bash command",
        "",
        "   rm -rf /tmp/build",
        "   This removes the build directory and triggers a clean rebuild from scratch every time now",
        "",
        " Permission rule Bash requires confirmation for this command.",
        " Do you want to proceed?",
        " ❯ 1. Yes",
    ])
    assert prompt_detector.extract_command(visible_text) == "rm -rf /tmp/build"


def test_approval_prompt_ignores_exact_claude_ctrl_b_footer():
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " (ctrl+b to run in background)",
    )

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is False
    state = prompt_detector.approval_prompt_state(visible_text)
    assert state["visible"] is True
    assert state["type"] == "bash"


def test_approval_prompt_ignores_multi_key_parenthetical_footer():
    # a footer hint with one-or-more keys plus a parenthetical — e.g.
    # "(ctrl+b ctrl+b (twice) to run in background)" — must read as a footer (not later activity), so the
    # live approval prompt is still detected and auto-approvable.
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " (ctrl+b ctrl+b (twice) to run in background)",
    )
    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is False
    state = prompt_detector.agent_screen_state(visible_text)
    assert state["key"] == "approval"
    assert "Do you want to proceed?" in state["text"]
    # Sanity: a real command sentence ending in "to <verb>" is NOT swallowed as a footer.
    assert prompt_detector._FOOTER_HINT_PART_RE.match("rm -rf / to delete everything") is None


def test_approval_prompt_ignores_dot_separated_ctrl_hint_cluster():
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " ctrl+b to run in background. ctrl+t to hide tasks",
    )

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is False
    assert prompt_detector.approval_prompt_state(visible_text)["visible"] is True


def test_approval_prompt_fires_with_ctrl_t_task_list_below_prompt():
    # Ctrl-T renders the todo overlay BELOW the prompt footer (header + items + "+N pending" + boxed
    # input). The whole block is chrome under a LIVE prompt; it must not read as "later activity" or
    # the prompt looks dismissed and auto-approve never fires (image 090).
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " ctrl+b to run in background · ctrl+t to hide tasks",
        " 11 tasks (0 done, 1 in progress, 10 open)",
        "   First task description that wraps onto",
        "   a second continuation line",
        "   Second task",
        "   +6 pending",
        " │ > │",
    )

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is False
    assert prompt_detector.detect_prompt(visible_text) == "bash"
    assert prompt_detector.selected_prompt_option(visible_text) == 1
    state = prompt_detector.approval_prompt_state(visible_text, visible_text)
    assert state["visible"] is True
    assert state["type"] == "bash"
    assert state["yes_selected"] is True
    assert state["action"] == "option1"


def test_task_list_header_break_does_not_mask_real_dismissal_above_it():
    # REGRESSION GUARD: genuine agent output (● bullet / ⎿ result) ABOVE a later task list must still
    # mark the prompt dismissed — the header break only short-circuits chrome between footer and header.
    visible_text = "\n".join([
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. No",
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " ● Ran the command and moved on",
        "   ⎿  output line",
        " 3 tasks (1 done, 0 in progress, 2 open)",
        "   Later task",
    ])

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is True
    assert prompt_detector.approval_prompt_state(visible_text)["visible"] is False


def test_claude_no_caret_prompt_is_not_treated_as_selected():
    # a prompt with NO selector glyph (nothing highlighted — e.g. a redraw frame) must NOT
    # be auto-confirmed from a positional "option 1 is Yes" guess. A send requires a visible ❯/›/box.
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
    ).replace(" ❯ 1. Yes", "   1. Yes")

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is False
    assert prompt_detector.yes_is_selected(visible_text) is False
    assert prompt_detector.selected_prompt_option(visible_text) == 0
    assert prompt_detector.approval_prompt_state(visible_text)["yes_selected"] is False


def test_claude_no_caret_prompt_does_not_default_when_stale():
    visible_text = "\n".join([
        claude_bash_prompt_with_footer(
            " Esc to cancel · Tab to amend · ctrl+e to explain",
        ).replace(" ❯ 1. Yes", "   1. Yes"),
        "● User approved Claude's request",
        "● Bash(echo one)",
        "  ⎿  ok",
        "",
        "❯ ",
    ])

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is True
    assert prompt_detector.yes_is_selected(visible_text) is False
    assert prompt_detector.approval_prompt_state(visible_text)["visible"] is False


def test_approval_prompt_detects_activity_after_claude_ctrl_b_footer():
    visible_text = "\n".join([
        claude_bash_prompt_with_footer(
            " Esc to cancel · Tab to amend · ctrl+e to explain",
            " (ctrl+b to run in background)",
        ),
        "● User approved Claude's request",
        "● Bash(echo one)",
        "  ⎿  ok",
        "",
        "❯ ",
    ])

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is True
    assert prompt_detector.approval_prompt_state(visible_text)["visible"] is False


VISIBLE_AGENT_WORKING_CASES = [
    pytest.param("◦ Working (1m 21s • esc to interrupt)\n", True, "working", id="codex-circle-working-footer"),
    pytest.param("• Working (6m 38s • esc to interrupt)\n", True, "working", id="codex-bullet-working-footer"),
    pytest.param("◦ Reviewing files (24s • esc to interrupt)\n", True, "working", id="codex-esc-to-interrupt-no-working-word"),
    pytest.param(
        "\n".join([
            "○ Working (4m 09s • esc to interrupt)",
            "",
            "› Implement {feature}",
            "",
            "  gpt-5.5 xhigh · ~",
        ]),
        True,
        "working",
        id="codex-working-with-input-prompt-below",
    ),
    pytest.param(
        "✱ Imagining… (4s · ↓ 98 tokens)\n  ⎿  Tip: Connect Claude to your IDE · /ide\n",
        True,
        "working",
        id="claude-random-status-imagining",
    ),
    pytest.param("✦ Comboublahblah… (7s · ↓ 123 tokens)\n", True, "working", id="claude-random-status-combo"),
    pytest.param("✳ Doodooshit… (1m 2s · ↓ 1.2k tokens)\n", True, "working", id="claude-random-status-doodoo"),
    pytest.param("☉ Refactoring... (2.3s · ↑ 13 tokens · high effort)\n", True, "working", id="claude-random-status-refactor"),
    pytest.param(
        "\n".join([
            "✶ Thinking… (1s · ↑ 26.9k tokens · esc to interrupt)",
            "100% context used",
            "▶▶ bypass permissions on · 1 shell · esc to interrupt",
        ]),
        True,
        "working",
        id="claude-context-used-status-line",
    ),
    pytest.param(
        "\n".join([
            "⠿ Running 2 agents…",
            "  ├ Verify detector fixtures · 14 tool uses · 31.2k tokens",
            "  └ Check current Claude pane state · 23 tool uses · 77.5k tokens",
            "",
            "(ctrl+b to run in background)",
        ]),
        True,
        "working",
        id="claude-multi-agent-header",
    ),
    pytest.param(
        "\n".join([
            "● Lollygagging… (2m 1s · ↓ 8.0k tokens · thinking with xhigh effort)",
            "",
            "╭────────────────────────────────────────────╮",
            "│ >                                          │",
            "╰────────────────────────────────────────────╯",
            "⏺ xhigh /effort",
            "▶▶ bypass permissions on · 1 shell · esc to interrupt",
        ]),
        True,
        "working",
        id="claude-boxed-input-chrome-below-spinner",
    ),
    pytest.param(
        "\n".join([
            "● Updated 3 files and finished the task.",
            "",
            "╭────────────────────────────────────────────╮",
            "│ >                                          │",
            "╰────────────────────────────────────────────╯",
            "▶▶ bypass permissions on · 1 shell",
        ]),
        False,
        None,
        id="finished-agent-without-live-spinner",
    ),
    pytest.param(
        "\n".join([
            "● Honking… (1m 12s · ↓ 5.8k tokens)",
            "",
            "● main  Fix preferences focus  ↑/↓ to select · Enter to view",
            "○ Explore  Check current pane  47s",
            "● xhigh /effort",
        ]),
        True,
        "working",
        id="claude-work-queue-below-spinner",
    ),
    pytest.param(
        "\n".join([
            "╭────────────────────────────────────────────╮",
            "│ >                                          │",
            "╰────────────────────────────────────────────╯",
            "⏺ xhigh /effort",
        ]),
        False,
        "idle",
        id="boxed-input-chrome-without-working-line",
    ),
    pytest.param(
        "\n".join([
            "  Then sleep 10 approval should show:",
            "",
            "  • Working (10s • esc to interrupt)",
            "",
            "  with Working animated in the real TTY.",
            "",
            "› Explain this codebase",
            "",
            "  gpt-5.5 xhigh · ~",
        ]),
        False,
        "idle",
        id="stale-example-above-prompt",
    ),
    pytest.param("  esc to interrupt · ctrl+t to hide tasks\n", False, None, id="non-parenthesized-footer-hint"),
    pytest.param(
        "◦ Working (1m 21s • esc to interrupt)\n"
        "  □ Pending task one\n"
        "  ✓ Done task two\n"
        "  ✗ Failed task three\n"
        "  ◯ Another pending\n",
        True,
        "working",
        id="unicode-task-glyphs",
    ),
]


@pytest.mark.parametrize("visible_text, expected_working, expected_key", VISIBLE_AGENT_WORKING_CASES)
def test_visible_agent_working_cases(visible_text, expected_working, expected_key):
    assert prompt_detector.visible_agent_working(visible_text) is expected_working
    if expected_key is not None:
        assert prompt_detector.agent_screen_state(visible_text)["key"] == expected_key


def test_claude_multi_agent_header_does_not_hide_live_approval_prompt():
    visible_text = "\n".join([
        "⠿ Running 2 agents…",
        "  ├ Verify detector fixtures · 14 tool uses · 31.2k tokens",
        "  └ Check current Claude pane state · 23 tool uses · 77.5k tokens",
        "",
        claude_bash_prompt_with_footer(" Esc to cancel · Tab to amend · ctrl+e to explain"),
    ])

    prompt_state = prompt_detector.approval_prompt_state(visible_text)
    screen_state = prompt_detector.agent_screen_state(visible_text)

    assert prompt_state["visible"] is True
    assert prompt_state["type"] == "bash"
    assert screen_state["key"] == "approval"
    assert screen_state["key"] != "working"


def test_detect_prompt_real_prompt_shapes_and_bottom_most_prompt_wins():
    codex_pane = "\n".join([
        "◦ Running gh api repos/ai-project/project/pulls/9579/comments",
        "",
        "  Would you like to run the following command?",
        "",
        "  Reason: Do you want to allow GitHub network access so I can fetch PR #9579 status?",
        "",
        "  $ gh api repos/ai-project/project/pulls/9579/comments",
        "",
        "› 1. Yes, proceed (y)",
        "  2. Yes, and don't ask again for commands that start with `gh api` (p)",
        "  3. No, and tell Codex what to do differently (esc)",
    ])
    assert prompt_detector.detect_prompt(codex_pane) == "bash"
    assert prompt_detector.prompt_text(codex_pane) == "Would you like to run the following command?"

    assert prompt_detector.detect_prompt(" Do you want to delete old_script.sh?\n ❯ 1. Yes\n   2. No\n") is None

    stale_then_current = "\n".join([
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. No",
        "",
        "● Some more work",
        "",
        " Do you want to make this edit to SKILL.md?",
        " ❯ 1. Yes",
        "   2. Yes, and allow Claude to edit its own settings",
        "   3. No",
    ])
    assert prompt_detector.detect_prompt(stale_then_current) == "file"


def test_action_for_prompt_preserves_codex_bash_option_policy():
    assert prompt_detector.action_for_prompt("bash") == "option1"
    assert prompt_detector.action_for_prompt("file") == "option2"
    assert prompt_detector.action_for_prompt("tool") == "option2"
    assert prompt_detector.action_for_prompt(None) is None
    assert prompt_detector.action_for_prompt("unknown") is None
    assert prompt_detector.action_for_bash_prompt(
        "  Would you like to run the following command?\n"
        "  $ gh api repos/ai-project/project/pulls/9579/comments\n"
        "› 1. Yes, proceed (y)\n"
        "  2. Yes, and don't ask again for commands that start with `gh api` (p)\n"
    ) == "option2"
    assert prompt_detector.action_for_bash_prompt(
        "  Would you like to run the following command?\n"
        "  $ ~/ai-config/claude/bin/dyn_gh_ops.py pr-status --pr 9579\n"
        "› 1. Yes, proceed (y)\n"
        "  2. Yes, and don't ask again for commands that start with "
        "`'~/ai-config/claude/bin/dyn_gh_ops.py' pr-status --pr 9579` (p)\n"
    ) == "option1"


def test_approval_prompt_state_reports_selected_codex_option():
    visible_text = "\n".join([
        "  Would you like to run the following command?",
        "",
        "  $ curl -sk -u yolomux:yolomux https://localhost:7777/",
        "",
        "  1. Yes, proceed (y)",
        "› 2. Yes, and don't ask again for commands that start with `curl -sk -u` (p)",
        "  3. No, and tell Codex what to do differently (esc)",
    ])

    state = prompt_detector.approval_prompt_state(visible_text)

    assert state["visible"] is True
    assert state["type"] == "bash"
    assert state["yes_selected"] is False
    assert state["selected_option"] == 2
    assert state["action"] == "option1"


def test_prompt_hash_includes_command_context():
    yes_no_prompt = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
    )
    other_prompt = yes_no_prompt.replace("echo one", "echo two")

    assert prompt_detector.prompt_hash(yes_no_prompt) == prompt_detector.prompt_hash(yes_no_prompt)
    assert prompt_detector.prompt_hash(yes_no_prompt) != prompt_detector.prompt_hash(other_prompt)


def test_approval_prompt_state_extracts_command_and_dangerous_flag():
    pane_text = "\n".join([
        "─────────────────────────────────────────────",
        " Bash command",
        "",
        "   rm -rf /tmp/foo",
        "   Delete temp directory",
        "",
        " Permission rule Bash requires confirmation for this command.",
        "",
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. No",
    ])

    state = prompt_detector.approval_prompt_state(pane_text, pane_text)

    assert state["visible"] is True
    assert state["type"] == "bash"
    assert state["action"] == "option1"
    assert state["command"] == "rm -rf /tmp/foo"
    assert state["dangerous"] is True


def test_visible_choice_prompt_text_detects_current_user_question():
    visible_text = "\n".join([
        "Which backend should I use?",
        "❯ 1. vLLM",
        "  2. SGLang",
        "",
    ])

    assert prompt_detector.visible_choice_prompt_text(visible_text) == "Which backend should I use?\n1. vLLM\n2. SGLang"
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "needs-input"


def test_ask_user_question_ui_is_needs_input_not_auto_approved():
    # Claude Code's AskUserQuestion multi-option UI (image 20260602-014). The selected option
    # is box-highlighted (no ❯), and a preview box / "Notes:" / "Chat about this" sit between the options
    # and the footer. It must be flagged needs-input, but is NOT a yes/no permission prompt.
    visible_text = "\n".join([
        "How should the YO!info | YO!agent sub-tab toggle look inside the merged panel?",
        "  1. Segmented control under pane tabs",
        "  2. Pills in the content header",
        "┌──────────────────────────────┐",
        "│ Preview: segmented control…   │",
        "└──────────────────────────────┘",
        "Notes: press n to add notes",
        "Chat about this",
        "Enter to select · ↑/↓ to navigate · n to add notes · Tab to switch questions · Esc to cancel",
    ])

    assert prompt_detector.detect_prompt(visible_text) is None, "AskUserQuestion is not a yes/no permission prompt"
    state = prompt_detector.agent_screen_state(visible_text)
    assert state["key"] == "needs-input"
    assert state["text"] == "How should the YO!info | YO!agent sub-tab toggle look inside the merged panel?"


def test_ask_user_question_footer_parts_are_recognized():
    # The AskUserQuestion footer hints ("↑/↓ to navigate", "n to add notes", "Tab to switch questions")
    # count as a footer line so the block is bounded correctly.
    footer = "Enter to select · ↑/↓ to navigate · n to add notes · Tab to switch questions · Esc to cancel"
    assert prompt_detector._is_footer_hint_line(footer)
    assert prompt_detector._is_ask_user_question_footer(footer)


def test_prompt_trailing_ui_line_accepts_unicode_task_glyphs():
    # a Ctrl-T task list using □/✓ (U+25A1/U+2713, this Claude version) below a working
    # footer must read as prompt-trailing chrome, not new output — else visible_agent_working flips to
    # False and the YO ball stops spinning while the agent works.
    # the new glyph rows are recognized as prompt-trailing UI; the ballot-box family still is too.
    assert prompt_detector._is_prompt_trailing_ui_line("  □ Pending task") is True
    assert prompt_detector._is_prompt_trailing_ui_line("  ✓ Done task") is True
    assert prompt_detector._is_prompt_trailing_ui_line("✗ Failed task") is True
    assert prompt_detector._is_prompt_trailing_ui_line("  ☐ Old-style pending") is True
