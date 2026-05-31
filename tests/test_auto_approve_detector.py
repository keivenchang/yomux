import os

os.environ.setdefault("YOLOMUX_CONFIG_DIR", "/tmp/yolomux-test-config")
os.environ.setdefault("YOLOMUX_STATE_DIR", "/tmp/yolomux-test-state")

import auto_approve_tmux
from yolomux_lib import prompt_detector


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


def test_extract_command_rejoins_wrapped_codex_command():
    visible_text = "\n".join([
        "  Would you like to run the following command?",
        "",
        "  Reason: Verify the persisted YOLOmux settings file now defaults",
        "  editor.engine to codemirror.",
        "",
        '  $ python3 -c "from yolomux_lib.settings import settings_payload;',
        '  print(settings_payload()[\'settings\'][\'editor\'][\'engine\'])"',
        "",
        "› 1. Yes, proceed (y)",
        "  2. No, and tell Codex what to do differently (esc)",
        "",
        "  Press enter to confirm or esc to cancel",
    ])

    assert prompt_detector.extract_command(visible_text) == (
        'python3 -c "from yolomux_lib.settings import settings_payload; '
        'print(settings_payload()[\'settings\'][\'editor\'][\'engine\'])"'
    )


def test_approval_prompt_ignores_exact_claude_ctrl_b_footer():
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " (ctrl+b to run in background)",
    )

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is False
    state = prompt_detector.approval_prompt_state(visible_text)
    assert state["visible"] is True
    assert state["type"] == "bash"


def test_approval_prompt_ignores_dot_separated_ctrl_hint_cluster():
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " ctrl+b to run in background. ctrl+t to hide tasks",
    )

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is False
    assert prompt_detector.approval_prompt_state(visible_text)["visible"] is True


def test_claude_no_caret_prompt_defaults_to_yes_when_current():
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
    ).replace(" ❯ 1. Yes", "   1. Yes")

    assert prompt_detector.approval_prompt_has_later_activity(visible_text) is False
    assert prompt_detector.yes_is_selected(visible_text) is True
    state = prompt_detector.approval_prompt_state(visible_text)
    assert state["visible"] is True
    assert state["yes_selected"] is True


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


def test_visible_agent_working_detects_codex_working_footer():
    visible_text = "◦ Working (1m 21s • esc to interrupt)\n"

    assert prompt_detector.visible_agent_working(visible_text) is True
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_detects_codex_bullet_working_footer():
    visible_text = "• Working (6m 38s • esc to interrupt)\n"

    assert prompt_detector.visible_agent_working(visible_text) is True
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_detects_esc_to_interrupt_without_working_word():
    visible_text = "◦ Reviewing files (24s • esc to interrupt)\n"

    assert prompt_detector.visible_agent_working(visible_text) is True
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_detects_codex_working_with_input_prompt_below():
    visible_text = "\n".join([
        "○ Working (4m 09s • esc to interrupt)",
        "",
        "› Implement {feature}",
        "",
        "  gpt-5.5 xhigh · ~",
    ])

    assert prompt_detector.visible_agent_working(visible_text) is True
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_detects_claude_random_status_verb():
    samples = [
        "✱ Imagining… (4s · ↓ 98 tokens)\n  ⎿  Tip: Connect Claude to your IDE · /ide\n",
        "✦ Comboublahblah… (7s · ↓ 123 tokens)\n",
        "✳ Doodooshit… (1m 2s · ↓ 1.2k tokens)\n",
        "☉ Refactoring... (2.3s · ↑ 13 tokens · high effort)\n",
    ]

    for visible_text in samples:
        assert prompt_detector.visible_agent_working(visible_text) is True
        assert prompt_detector.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_ignores_context_used_status_line():
    visible_text = "\n".join([
        "✶ Thinking… (1s · ↑ 26.9k tokens · esc to interrupt)",
        "100% context used",
        "▶▶ bypass permissions on · 1 shell · esc to interrupt",
    ])

    assert prompt_detector.visible_agent_working(visible_text) is True
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_detects_claude_multi_agent_header_with_token_sublines():
    visible_text = "\n".join([
        "⠿ Running 2 agents…",
        "  ├ Verify detector fixtures · 14 tool uses · 31.2k tokens",
        "  └ Check current Claude pane state · 23 tool uses · 77.5k tokens",
        "",
        "(ctrl+b to run in background)",
    ])

    assert prompt_detector.visible_agent_working(visible_text) is True
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "working"


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


def test_visible_agent_working_detects_claude_boxed_input_chrome_below_spinner():
    visible_text = "\n".join([
        "● Lollygagging… (2m 1s · ↓ 8.0k tokens · thinking with xhigh effort)",
        "",
        "╭────────────────────────────────────────────╮",
        "│ >                                          │",
        "╰────────────────────────────────────────────╯",
        "⏺ xhigh /effort",
        "▶▶ bypass permissions on · 1 shell · esc to interrupt",
    ])

    assert prompt_detector.visible_agent_working(visible_text) is True
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_detects_claude_work_queue_below_spinner():
    visible_text = "\n".join([
        "● Honking… (1m 12s · ↓ 5.8k tokens)",
        "",
        "● main  Fix preferences focus  ↑/↓ to select · Enter to view",
        "○ Explore  Check current pane  47s",
        "● xhigh /effort",
    ])

    assert prompt_detector.visible_agent_working(visible_text) is True
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_ignores_boxed_input_chrome_without_working_line():
    visible_text = "\n".join([
        "╭────────────────────────────────────────────╮",
        "│ >                                          │",
        "╰────────────────────────────────────────────╯",
        "⏺ xhigh /effort",
    ])

    assert prompt_detector.visible_agent_working(visible_text) is False
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "idle"


def test_visible_agent_working_ignores_stale_example_above_prompt():
    visible_text = "\n".join([
        "  Then sleep 10 approval should show:",
        "",
        "  • Working (10s • esc to interrupt)",
        "",
        "  with Working animated in the real TTY.",
        "",
        "› Explain this codebase",
        "",
        "  gpt-5.5 xhigh · ~",
    ])

    assert prompt_detector.visible_agent_working(visible_text) is False
    assert prompt_detector.agent_screen_state(visible_text)["key"] == "idle"


def test_visible_agent_working_ignores_non_parenthesized_footer_hint():
    visible_text = "  esc to interrupt · ctrl+t to hide tasks\n"

    assert prompt_detector.visible_agent_working(visible_text) is False


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


def test_target_resolution_self_test_cases_live_in_pytest():
    sessions = ["project1", "project2", "project3", "misc"]

    assert auto_approve_tmux._resolve_targets_from_sessions(["project1"], sessions) == ["project1"]
    assert auto_approve_tmux._resolve_targets_from_sessions(["project1", "project2,project3"], sessions) == [
        "project1",
        "project2",
        "project3",
    ]
    assert auto_approve_tmux._resolve_targets_from_sessions(["project*"], sessions) == [
        "project1",
        "project2",
        "project3",
    ]
    assert auto_approve_tmux._resolve_targets_from_sessions(["project*:0.1"], sessions) == [
        "project1:0.1",
        "project2:0.1",
        "project3:0.1",
    ]
    assert auto_approve_tmux.specs_have_wildcards(["project1", "project2:0.1"]) is False
    assert auto_approve_tmux.specs_have_wildcards(["project1", "dyn*"]) is True
    assert auto_approve_tmux.tmux_exact_target_from_sessions("1", ["1", "6", "ant"]) == "1:"
    assert auto_approve_tmux.tmux_exact_target_from_sessions("%79", ["1", "6", "ant"]) == "%79"
