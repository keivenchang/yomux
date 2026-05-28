import auto_approve_tmux


def test_visible_agent_working_detects_codex_working_footer():
    visible_text = "◦ Working (1m 21s • esc to interrupt)\n"

    assert auto_approve_tmux.visible_agent_working(visible_text) is True
    assert auto_approve_tmux.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_detects_esc_to_interrupt_without_working_word():
    visible_text = "◦ Reviewing files (24s • esc to interrupt)\n"

    assert auto_approve_tmux.visible_agent_working(visible_text) is True
    assert auto_approve_tmux.agent_screen_state(visible_text)["key"] == "working"


def test_visible_agent_working_ignores_non_parenthesized_footer_hint():
    visible_text = "  esc to interrupt · ctrl+t to hide tasks\n"

    assert auto_approve_tmux.visible_agent_working(visible_text) is False
