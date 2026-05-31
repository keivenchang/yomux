from yolomux_lib.workdir import agent_command


def test_agent_command_uses_plain_agent_cli_unless_dangerously_yolo():
    assert agent_command("claude", dangerously_yolo=False) == "claude"
    assert agent_command("codex", dangerously_yolo=False) == "codex"
    assert agent_command("claude", dangerously_yolo=True) == "claude --dangerously-skip-permissions"
    assert agent_command("codex", dangerously_yolo=True) == "codex --dangerously-bypass-approvals-and-sandbox"
