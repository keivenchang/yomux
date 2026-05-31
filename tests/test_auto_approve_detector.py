import auto_approve_tmux


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

    assert auto_approve_tmux.extract_command(visible_text) == (
        'python3 -c "from yolomux_lib.settings import settings_payload; '
        'print(settings_payload()[\'settings\'][\'editor\'][\'engine\'])"'
    )


def test_approval_prompt_ignores_exact_claude_ctrl_b_footer():
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " (ctrl+b to run in background)",
    )

    assert auto_approve_tmux.approval_prompt_has_later_activity(visible_text) is False
    state = auto_approve_tmux.approval_prompt_state(visible_text)
    assert state["visible"] is True
    assert state["type"] == "bash"


def test_approval_prompt_ignores_dot_separated_ctrl_hint_cluster():
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " ctrl+b to run in background. ctrl+t to hide tasks",
    )

    assert auto_approve_tmux.approval_prompt_has_later_activity(visible_text) is False
    assert auto_approve_tmux.approval_prompt_state(visible_text)["visible"] is True


def test_claude_no_caret_prompt_defaults_to_yes_when_current():
    visible_text = claude_bash_prompt_with_footer(
        " Esc to cancel · Tab to amend · ctrl+e to explain",
    ).replace(" ❯ 1. Yes", "   1. Yes")

    assert auto_approve_tmux.approval_prompt_has_later_activity(visible_text) is False
    assert auto_approve_tmux.yes_is_selected(visible_text) is True
    state = auto_approve_tmux.approval_prompt_state(visible_text)
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

    assert auto_approve_tmux.approval_prompt_has_later_activity(visible_text) is True
    assert auto_approve_tmux.yes_is_selected(visible_text) is False
    assert auto_approve_tmux.approval_prompt_state(visible_text)["visible"] is False


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

    assert auto_approve_tmux.approval_prompt_has_later_activity(visible_text) is True
    assert auto_approve_tmux.approval_prompt_state(visible_text)["visible"] is False


def test_visible_agent_working_detects_codex_working_footer():
    visible_text = "◦ Working (1m 21s • esc to interrupt)\n"

    assert auto_approve_tmux.visible_agent_working(visible_text) is True
    assert auto_approve_tmux.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_detects_codex_bullet_working_footer():
    visible_text = "• Working (6m 38s • esc to interrupt)\n"

    assert auto_approve_tmux.visible_agent_working(visible_text) is True
    assert auto_approve_tmux.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_detects_esc_to_interrupt_without_working_word():
    visible_text = "◦ Reviewing files (24s • esc to interrupt)\n"

    assert auto_approve_tmux.visible_agent_working(visible_text) is True
    assert auto_approve_tmux.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_detects_codex_working_with_input_prompt_below():
    visible_text = "\n".join([
        "○ Working (4m 09s • esc to interrupt)",
        "",
        "› Implement {feature}",
        "",
        "  gpt-5.5 xhigh · ~",
    ])

    assert auto_approve_tmux.visible_agent_working(visible_text) is True
    assert auto_approve_tmux.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_detects_claude_random_status_verb():
    samples = [
        "✱ Imagining… (4s · ↓ 98 tokens)\n  ⎿  Tip: Connect Claude to your IDE · /ide\n",
        "✦ Comboublahblah… (7s · ↓ 123 tokens)\n",
        "✳ Doodooshit… (1m 2s · ↓ 1.2k tokens)\n",
        "☉ Refactoring... (2.3s · ↑ 13 tokens · high effort)\n",
    ]

    for visible_text in samples:
        assert auto_approve_tmux.visible_agent_working(visible_text) is True
        assert auto_approve_tmux.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_ignores_context_used_status_line():
    visible_text = "\n".join([
        "✶ Thinking… (1s · ↑ 26.9k tokens · esc to interrupt)",
        "100% context used",
        "▶▶ bypass permissions on · 1 shell · esc to interrupt",
    ])

    assert auto_approve_tmux.visible_agent_working(visible_text) is True
    assert auto_approve_tmux.agent_screen_state(visible_text)["key"] == "working"


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

    assert auto_approve_tmux.visible_agent_working(visible_text) is False
    assert auto_approve_tmux.agent_screen_state(visible_text)["key"] == "idle"


def test_visible_agent_working_ignores_non_parenthesized_footer_hint():
    visible_text = "  esc to interrupt · ctrl+t to hide tasks\n"

    assert auto_approve_tmux.visible_agent_working(visible_text) is False
