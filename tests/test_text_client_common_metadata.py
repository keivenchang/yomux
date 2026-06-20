# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import ast
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from text_client_common import (  # noqa: E402
    CLAUDE_CONFIG_KEYS,
    CLAUDE_OUTPUT_TERMS,
    CLIENT_SHARED_CONFIG_KEYS,
    CLIENT_INTENT_ROWS,
    CLIENT_PERMISSION_DEFAULTS,
    CODEX_CONFIG_KEYS,
    CODEX_OUTPUT_TERMS,
    METRICS_OUTPUT_PREFIX,
    TOOL_OUTPUT_PREFIX,
    TurnMetrics,
    client_slash_commands,
    client_intent_markdown_rows,
    client_slash_help_rows,
    prefixed_output_labels,
)
import claude  # noqa: E402
import codex  # noqa: E402
from yolomux_lib.agent_comms.stream_events import ClaudeStreamJsonNormalizer  # noqa: E402
from yolomux_lib.agent_comms.stream_events import normalize_codex_app_server_message  # noqa: E402
from yolomux_lib.yoagent.backends import yoagent_response_details  # noqa: E402
from claude import ClaudeTextClient  # noqa: E402
from codex import APP_SERVER_TIMEOUT_SECONDS, CodexTextClient  # noqa: E402


GENERIC_HELP_FLAGS = {"-V", "--version"}


def parser_startup_flags(module_path: Path, flag_constants: dict[str, str]) -> set[str]:
    tree = ast.parse(module_path.read_text())
    parse_args = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "parse_args")
    flags: set[str] = set()
    for node in ast.walk(parse_args):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
            continue
        for arg in node.args:
            value = None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                value = arg.value
            elif isinstance(arg, ast.Name):
                value = flag_constants.get(arg.id)
            if value and value.startswith("-"):
                flags.add(value)
    return flags - GENERIC_HELP_FLAGS


def documented_startup_flags(client: str) -> set[str]:
    docs = (ROOT / "tools" / "CLIENTS.md").read_text()
    heading = f"## {client.title()} Flags And Settings"
    stop = f"Useful {client.title()}"
    start_index = docs.index(heading)
    stop_index = docs.index(stop, start_index)
    section = docs[start_index:stop_index]
    flags: set[str] = set()
    for match in re.finditer(r"(?m)^- `([^`]+)`", section):
        for token in re.split(r"[\s,]+", match.group(1)):
            if re.fullmatch(r"-{1,2}[A-Za-z][A-Za-z0-9-]*", token):
                flags.add(token)
    return flags - GENERIC_HELP_FLAGS


def fixture_slash_commands(client: str) -> dict[str, object]:
    fixture_paths = {
        "claude": ROOT / "tests" / "fixtures" / "text_clients" / "claude_slash_commands_2_1_183.json",
        "codex": ROOT / "tests" / "fixtures" / "text_clients" / "codex_slash_commands_0_141_0.json",
    }
    return json.loads(fixture_paths[client].read_text())


def slash_commands_printed_by_help(output: str) -> set[str]:
    return set(re.findall(r"(?m)(?<!\S)/([A-Za-z0-9-]+)\b", output))


def stable_stream_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{key: value for key, value in event.items() if key != "timestamp"} for event in events]


def test_output_terminology_uses_cross_product_names():
    assert CODEX_OUTPUT_TERMS.title_label == "Reasoning (aka Thinking)"
    assert CODEX_OUTPUT_TERMS.lower_label == "reasoning (aka thinking)"
    assert CODEX_OUTPUT_TERMS.prefix == "reasoning"
    assert CLAUDE_OUTPUT_TERMS.title_label == "Thinking (aka Reasoning)"
    assert CLAUDE_OUTPUT_TERMS.lower_label == "thinking (aka reasoning)"
    assert CLAUDE_OUTPUT_TERMS.prefix == "thinking"


def test_permissive_defaults_are_single_source():
    assert CLIENT_PERMISSION_DEFAULTS.claude_permission_mode == "bypassPermissions"
    assert CLIENT_PERMISSION_DEFAULTS.codex_sandbox == "danger-full-access"
    assert CLIENT_PERMISSION_DEFAULTS.codex_approval_policy == "never"
    assert CLIENT_PERMISSION_DEFAULTS.codex_bypass_hook_trust is True
    assert CLIENT_PERMISSION_DEFAULTS.codex_text_client_approval_mode == "accept"
    assert CLIENT_PERMISSION_DEFAULTS.claude_skip_permissions_flag == "--dangerously-skip-permissions"
    assert CLIENT_PERMISSION_DEFAULTS.codex_bypass_approvals_flag == "--dangerously-bypass-approvals-and-sandbox"
    assert CLIENT_PERMISSION_DEFAULTS.codex_bypass_hook_trust_flag == "--dangerously-bypass-hook-trust"
    assert CLIENT_PERMISSION_DEFAULTS.codex_is_permissive("danger-full-access", "never")


def test_shared_config_keys_map_same_intent_names():
    assert CLIENT_SHARED_CONFIG_KEYS.model == "model"
    assert CODEX_CONFIG_KEYS.model == CLIENT_SHARED_CONFIG_KEYS.model
    assert CLAUDE_CONFIG_KEYS.model == CLIENT_SHARED_CONFIG_KEYS.model
    assert CODEX_CONFIG_KEYS.tool_output == CLIENT_SHARED_CONFIG_KEYS.tool_output
    assert CLAUDE_CONFIG_KEYS.tool_output == CLIENT_SHARED_CONFIG_KEYS.tool_output
    assert CODEX_CONFIG_KEYS.metrics == CLIENT_SHARED_CONFIG_KEYS.metrics
    assert CLAUDE_CONFIG_KEYS.metrics == CLIENT_SHARED_CONFIG_KEYS.metrics
    assert CODEX_CONFIG_KEYS.effort == "model_reasoning_effort"
    assert CLAUDE_CONFIG_KEYS.effort == "effort"
    assert CODEX_CONFIG_KEYS.hidden_work_raw == "text_client.show_raw_reasoning"
    assert CLAUDE_CONFIG_KEYS.hidden_work_visibility == "text_client.show_thinking"


def test_prefixed_output_labels_share_common_tool_and_metrics_labels():
    assert prefixed_output_labels(CODEX_OUTPUT_TERMS) == ("reasoning", TOOL_OUTPUT_PREFIX, METRICS_OUTPUT_PREFIX)
    assert prefixed_output_labels(CLAUDE_OUTPUT_TERMS) == ("thinking", TOOL_OUTPUT_PREFIX, METRICS_OUTPUT_PREFIX)


def test_text_clients_use_shared_agent_comms_primitives():
    codex_source = (TOOLS_DIR / "codex.py").read_text()
    claude_source = (TOOLS_DIR / "claude.py").read_text()
    transport_source = (ROOT / "yolomux_lib" / "yoagent" / "transports.py").read_text()
    assert "from yolomux_lib.agent_comms.codex_app_server import CodexAppServerProtocol" in codex_source
    assert "from yolomux_lib.agent_comms.json_rpc import json_rpc_request" in codex_source
    assert "from yolomux_lib.agent_comms.stream_events import normalize_codex_app_server_message" in codex_source
    assert "def json_rpc_request(" not in codex_source
    assert "def json_rpc_notification(" not in codex_source
    assert "from yolomux_lib.agent_comms.stream_events import ClaudeStreamJsonNormalizer" in claude_source
    assert "def _readline(" not in transport_source
    assert "def _read_message(" not in transport_source
    assert "def _read_response(" not in transport_source
    assert "def _wait_turn_complete(" not in transport_source
    assert isinstance(codex.CodexTextClient(codex_args()).last_normalized_events, list)
    assert isinstance(claude.ClaudeTextClient(claude_args()).stream_normalizer, object)


def test_text_client_normalized_events_match_shared_agent_comms(capsys):
    codex_message = {
        "jsonrpc": "2.0",
        "method": "item/agentMessage/delta",
        "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "delta": "Hello"},
    }
    codex_client = CodexTextClient(codex_args())
    codex_client.thread_id = "thread-1"
    codex_client.handle_async_message(codex_message, 999999999.0)
    assert stable_stream_events(codex_client.last_normalized_events) == stable_stream_events(normalize_codex_app_server_message(codex_message))

    claude_message = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}}
    claude_client = ClaudeTextClient(claude_args())
    claude_client.handle_message(claude_message)
    assert stable_stream_events(claude_client.last_normalized_events) == stable_stream_events(ClaudeStreamJsonNormalizer().normalize_item(claude_message))
    capsys.readouterr()


def test_yoagent_response_details_reports_codex_transport_timing():
    details = yoagent_response_details({
        "backend_used": "codex",
        "timing": {"ttfr_ms": 1500},
        "cli": {
            "transport": "codex-app-server",
            "process_reused": True,
            "thread_started": False,
            "thread_ready_ms": 1.2,
            "turn_start_ack_ms": 3.4,
            "first_stream_event_ms": 5.6,
            "first_assistant_delta_ms": 7.8,
            "turn_complete_ms": 9.0,
            "elapsed_ms": 10,
            "prompt_chars": 42,
        },
    })

    assert "- Codex transport: `warm reuse`, `thread reused`" in details
    assert "first answer delta 7.8ms" in details
    assert "- model CLI time: `0.010s`" in details


def test_terminology_markdown_rows_are_valid_table_rows():
    rows = client_intent_markdown_rows()
    assert len(rows) == len(CLIENT_INTENT_ROWS) + 2
    assert all(row.count("|") == 6 for row in rows)
    assert any("reasoning&#124; ..." in row for row in rows)
    assert any("thinking&#124; ..." in row for row in rows)


def test_clients_doc_uses_shared_terminology_table():
    docs = (ROOT / "tools" / "CLIENTS.md").read_text()
    assert "\n".join(client_intent_markdown_rows()) in docs


def test_documented_startup_flags_match_wrapper_parsers():
    codex_flags = parser_startup_flags(
        ROOT / "tools" / "codex.py",
        {
            "CODEX_BYPASS_APPROVALS_FLAG": codex.CODEX_BYPASS_APPROVALS_FLAG,
            "CODEX_BYPASS_HOOK_TRUST_FLAG": codex.CODEX_BYPASS_HOOK_TRUST_FLAG,
        },
    )
    claude_flags = parser_startup_flags(
        ROOT / "tools" / "claude.py",
        {"CLAUDE_SKIP_PERMISSIONS_FLAG": claude.CLAUDE_SKIP_PERMISSIONS_FLAG},
    )
    assert codex_flags == documented_startup_flags("codex")
    assert claude_flags == documented_startup_flags("claude")


def test_slash_command_help_matches_versioned_real_client_fixtures(capsys):
    fixture = fixture_slash_commands("codex")
    assert fixture["version"] == "codex-cli 0.141.0"
    expected = set(fixture["real_commands"]) | set(fixture["wrapper_compat_commands"])
    codex_commands = set(codex.REPL_COMMANDS)
    assert expected <= codex_commands
    codex_client = CodexTextClient(codex_args())
    codex_client.model_catalog_rows = lambda include_hidden=False: []
    codex_client.print_repl_help()
    codex_help_commands = slash_commands_printed_by_help(capsys.readouterr().out)
    assert expected <= codex_help_commands

    fixture = fixture_slash_commands("claude")
    assert fixture["version"] == "2.1.183 (Claude Code)"
    expected = set(fixture["real_commands"]) | set(fixture["wrapper_compat_commands"])
    claude_commands = set(claude.REPL_COMMANDS)
    assert expected <= claude_commands
    claude_client = ClaudeTextClient(claude_args())
    claude_client.print_repl_help()
    claude_help_commands = slash_commands_printed_by_help(capsys.readouterr().out)
    assert expected <= claude_help_commands


def codex_args(**overrides):
    values = {
        "cwd": str(ROOT),
        "thread_id": "",
        "model": "gpt-5.4-mini",
        "effort": "low",
        "reasoning_summary": "concise",
        "sandbox": "danger-full-access",
        "approval_policy": "never",
        "add_dir": [],
        "service_tier": "",
        "raw_output": False,
        "show_reasoning_summary": True,
        "show_raw_reasoning": False,
        "show_tool_output": True,
        "show_metrics": False,
        "config_values": {},
        "search": False,
        "approval_mode": "accept",
        "debug_json": False,
        "include_hidden_models": False,
        "timeout": APP_SERVER_TIMEOUT_SECONDS,
        "dangerously_bypass_hook_trust": True,
        "ephemeral": False,
        "base_instructions": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def claude_args(**overrides):
    values = {
        "cwd": str(ROOT),
        "resume": "",
        "session_id": "",
        "model": "sonnet",
        "effort": "low",
        "add_dir": [],
        "allowed_tools": [],
        "disallowed_tools": [],
        "tools": [],
        "permission_mode": "bypassPermissions",
        "continue_last": False,
        "system_prompt": "",
        "append_system_prompt": "",
        "max_budget_usd": "",
        "show_status": False,
        "show_tool_output": True,
        "show_thinking": False,
        "raw_json": False,
        "show_metrics": False,
        "timeout": 900.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_shared_slash_command_registry_drives_completion_and_help():
    codex_commands = client_slash_commands("codex")
    claude_commands = client_slash_commands("claude")
    for command in {"context", "clear", "cls", "permission", "permission-mode", "permissions", "thinking", "reasoning"}:
        assert command in codex_commands
        assert command in claude_commands
    assert any("/reasoning [mode]" in row for row in client_slash_help_rows("codex"))
    assert any("/thinking [on|off]" in row for row in client_slash_help_rows("claude"))
    assert any("/cls" in row and "terminal" in row for row in client_slash_help_rows("codex"))
    assert sorted(codex.REPL_COMMANDS) == client_slash_commands("codex", codex.UNIMPLEMENTED_CODEX_COMMANDS)
    assert sorted(claude.REPL_COMMANDS) == client_slash_commands("claude", claude.UNIMPLEMENTED_CLAUDE_COMMANDS)


def test_repl_help_uses_shared_command_rows(capsys):
    codex_client = CodexTextClient(codex_args())
    codex_client.model_catalog_rows = lambda include_hidden=False: []
    codex_client.print_repl_help()
    codex_help = capsys.readouterr().out
    for row in client_slash_help_rows("codex"):
        assert row.strip() in codex_help

    claude_client = ClaudeTextClient(claude_args())
    claude_client.print_repl_help()
    claude_help = capsys.readouterr().out
    for row in client_slash_help_rows("claude"):
        assert row.strip() in claude_help


def test_codex_permission_aliases_share_permissions_handler(capsys):
    client = CodexTextClient(codex_args())
    assert client.handle_repl_command("/permission-mode read-only on-request") == "handled"
    output = capsys.readouterr()
    assert "compatibility command" in output.err
    assert "Permissions: Read Only (on-request)" in output.out
    assert client.args.sandbox == "read-only"
    assert client.args.approval_policy == "on-request"


def test_codex_reasoning_and_thinking_aliases_update_config(capsys):
    client = CodexTextClient(codex_args(show_reasoning_summary=False, show_raw_reasoning=False))
    assert client.handle_repl_command("/thinking raw") == "handled"
    output = capsys.readouterr()
    assert "compatibility command" in output.err
    assert client.args.show_raw_reasoning is True
    assert client.args.config_values[CODEX_CONFIG_KEYS.hidden_work_raw] is True

    assert client.handle_repl_command("/reasoning none") == "handled"
    assert client.args.reasoning_summary == "none"
    assert client.args.show_reasoning_summary is False
    assert client.args.show_raw_reasoning is False


def test_claude_reasoning_alias_toggles_thinking(capsys):
    client = ClaudeTextClient(claude_args(show_thinking=False))
    assert client.handle_repl_command("/reasoning on") == "handled"
    output = capsys.readouterr()
    assert "compatibility command" in output.err
    assert client.args.show_thinking is True


def test_clear_resets_conversation_and_cls_is_terminal_only(capsys):
    codex_client = CodexTextClient(codex_args(thread_id="thread-1"))
    assert codex_client.handle_repl_command("/clear") == "handled"
    assert codex_client.thread_id == ""
    assert "next turn starts a new Codex thread" in capsys.readouterr().out

    claude_client = ClaudeTextClient(claude_args(resume="session-1"))
    claude_client.last_usage = {"input_tokens": 1}
    assert claude_client.handle_repl_command("/clear") == "handled"
    assert claude_client.session_id == ""
    assert claude_client.last_usage == {}
    assert "next turn starts a new Claude session" in capsys.readouterr().out

    assert claude_client.handle_repl_command("/cls") == "handled"
    assert "\033c" in capsys.readouterr().out


def test_codex_usage_prints_token_usage_not_metrics_only(capsys):
    client = CodexTextClient(codex_args())
    metrics = TurnMetrics("hello", 1.0)
    metrics.usage = {"input_tokens": 4, "output_tokens": 9}
    metrics.completed_at = 2.0
    client.last_metrics = metrics
    assert client.handle_repl_command("/usage") == "handled"
    output = capsys.readouterr().out
    assert "Codex Usage" in output
    assert "input_tokens: 4" in output
    assert "output_tokens: 9" in output
    assert "No turn metrics yet." not in output
