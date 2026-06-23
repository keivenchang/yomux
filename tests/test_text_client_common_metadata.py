# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from text_client_common import (  # noqa: E402
    ANSI_BOLD,
    ANSI_ITALIC,
    ANSI_RESET,
    CLAUDE_CONFIG_KEYS,
    CLAUDE_OUTPUT_TERMS,
    CLIENT_SHARED_CONFIG_KEYS,
    CLIENT_INTENT_ROWS,
    CLIENT_PERMISSION_DEFAULTS,
    CODEX_CONFIG_KEYS,
    CODEX_OUTPUT_TERMS,
    METRICS_OUTPUT_PREFIX,
    PromptInputSession,
    PromptLineEditor,
    TOOL_OUTPUT_PREFIX,
    TextClientBase,
    TurnMetrics,
    client_slash_commands,
    client_intent_markdown_rows,
    client_slash_help_rows,
    prefixed_output_labels,
    render_terminal_markdown_line,
)
import claude  # noqa: E402
import codex  # noqa: E402
from yolomux_lib.agent_comms.json_rpc import json_rpc_request  # noqa: E402
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


def test_terminal_markdown_line_renders_common_answer_spans():
    line = "- **Left (east)**: Texas\n- *Right (west)*: `Arizona`\n"

    rendered = render_terminal_markdown_line(line, enabled=True, code_color="\033[35m")

    assert f"{ANSI_BOLD}Left (east){ANSI_RESET}" in rendered
    assert f"{ANSI_ITALIC}Right (west){ANSI_RESET}" in rendered
    assert "\033[35mArizona" in rendered
    assert rendered.endswith(f"{ANSI_RESET}\n")
    assert render_terminal_markdown_line(line, enabled=False, code_color="\033[35m") == line


def test_answer_markdown_renderer_buffers_streaming_lines(capsys):
    client = TextClientBase(str(ROOT), prefixed_labels=())
    client.use_answer_markdown = True
    client.tool_color = "\033[35m"

    client.write_answer_stdout("- **Left")
    assert capsys.readouterr().out == ""

    client.write_answer_stdout(" (east)**: Texas\nBack: `north`")
    output = capsys.readouterr().out
    assert output == f"- {ANSI_BOLD}Left (east){ANSI_RESET}: Texas\n"

    client.finish_answer_output()
    output = capsys.readouterr().out
    assert output == f"Back: \033[35mnorth{ANSI_RESET}\n"


def test_permissive_defaults_are_single_source():
    assert CLIENT_PERMISSION_DEFAULTS.claude_permission_mode == "bypassPermissions"
    assert CLIENT_PERMISSION_DEFAULTS.codex_sandbox == "danger-full-access"
    assert CLIENT_PERMISSION_DEFAULTS.codex_approval_policy == "never"
    assert CLIENT_PERMISSION_DEFAULTS.codex_bypass_hook_trust is False
    assert CLIENT_PERMISSION_DEFAULTS.codex_text_client_approval_mode == "accept"
    assert CLIENT_PERMISSION_DEFAULTS.claude_skip_permissions_flag == "--dangerously-skip-permissions"
    assert CLIENT_PERMISSION_DEFAULTS.codex_bypass_approvals_flag == "--dangerously-bypass-approvals-and-sandbox"
    assert CLIENT_PERMISSION_DEFAULTS.codex_bypass_hook_trust_flag == "--dangerously-bypass-hook-trust"
    assert CLIENT_PERMISSION_DEFAULTS.codex_is_permissive("danger-full-access", "never")


def test_codex_bypass_hook_trust_requires_explicit_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["codex.py"])
    assert codex.parse_args().dangerously_bypass_hook_trust is False

    monkeypatch.setattr(sys, "argv", ["codex.py", "--dangerously-bypass-hook-trust"])
    assert codex.parse_args().dangerously_bypass_hook_trust is True

    monkeypatch.setattr(sys, "argv", ["codex.py", "-c", "bypass_hook_trust=true"])
    assert codex.parse_args().dangerously_bypass_hook_trust is True


def test_codex_parser_defaults_to_mini_model_and_medium_effort(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["codex.py"])
    args = codex.parse_args()

    assert args.model == "gpt-5.4-mini"
    assert args.effort == "medium"


def test_codex_accepts_claude_style_rendering_flags(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex.py",
            "--effort",
            "xhigh",
            "--hide-thinking",
            "--hide-tool-output",
            "--show-metrics",
            "--raw-json",
            "--timeout",
            "12.5",
        ],
    )
    args = codex.parse_args()

    assert args.effort == "xhigh"
    assert args.show_reasoning_summary is False
    assert args.show_raw_reasoning is False
    assert args.show_tool_output is False
    assert args.show_metrics is True
    assert args.debug_json is True
    assert args.timeout == 12.5


def test_codex_show_thinking_restores_summary_from_config_none(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["codex.py", "-c", "model_reasoning_summary=none", "--show-thinking"])
    args = codex.parse_args()

    assert args.reasoning_summary == "concise"
    assert args.show_reasoning_summary is True


def test_codex_tui_configuration_uses_parser_model_and_effort(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["codex.py"])
    args = codex.parse_args()
    try:
        codex.configure_codex_tui(args)

        assert codex.mock_agent_common.MODEL == "gpt-5.4-mini"
        assert codex.mock_agent_common.EFFORT == "medium"
        assert codex.mock_agent_common.live_composer_status_line().startswith("  gpt-5.4-mini medium · ")
    finally:
        codex.mock_agent_common.configure_codex_mock()


def test_text_clients_accept_mock_mode(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["codex.py", "--mock"])
    assert codex.parse_args().mock is True

    monkeypatch.setattr(sys, "argv", ["claude.py", "--mock"])
    assert claude.parse_args().mock is True


def test_text_clients_accept_dump_fixtures(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["codex.py", "--dump-fixtures"])
    assert codex.parse_args().dump_fixtures is True

    monkeypatch.setattr(sys, "argv", ["claude.py", "--dump-fixtures"])
    assert claude.parse_args().dump_fixtures is True


def test_claude_thinking_defaults_on(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["claude.py"])
    assert claude.parse_args().show_thinking is True

    monkeypatch.setattr(sys, "argv", ["claude.py", "--hide-thinking"])
    assert claude.parse_args().show_thinking is False


def test_codex_accepts_thinking_config_alias(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["codex.py", "--strict-config", "-c", "text_client.show_thinking=false"])
    args = codex.parse_args()

    assert args.show_reasoning_summary is False
    assert args.config_values[CODEX_CONFIG_KEYS.hidden_work_summary] is False
    assert codex.CODEX_SHOW_THINKING_CONFIG_ALIAS not in args.config_values


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


def test_codex_resolves_prompt_defaults_from_model_catalog():
    rows = [
        {"model": "gpt-5.4-mini", "defaultReasoningEffort": "medium", "hidden": False, "isDefault": False},
        {"model": "gpt-5.5", "defaultReasoningEffort": "xhigh", "hidden": False, "isDefault": True},
    ]
    assert codex.resolved_model_settings_from_catalog(rows, "", "") == ("gpt-5.4-mini", "medium")
    assert codex.resolved_model_settings_from_catalog(rows, "", "low") == ("gpt-5.4-mini", "low")
    assert codex.resolved_model_settings_from_catalog(rows, "gpt-5.4-mini", "") == ("gpt-5.4-mini", "medium")
    assert codex.resolved_model_settings_from_catalog(rows, "custom-model", "") == ("gpt-5.4-mini", "medium")
    assert codex.resolved_model_settings_from_catalog(rows, "gpt-5.1", "xhigh") == ("gpt-5.4-mini", "medium")

    client = CodexTextClient(codex_args(model="", effort=""))
    client.model_catalog_rows = lambda include_hidden: rows
    client.resolve_default_model_settings()
    assert client.args.model == "gpt-5.4-mini"
    assert client.args.effort == "medium"
    assert client.args.config_values[CODEX_CONFIG_KEYS.effort] == "medium"
    assert client.prompt_text().startswith("gpt-5.4-mini[medium] ")


def test_codex_help_explains_model_and_effort_defaults():
    help_text = codex.build_config_help([])

    assert "Default model: gpt-5.4-mini" in help_text
    assert "Default reasoning (aka thinking) effort: medium" in help_text
    assert "Change model at launch: -m <model> (default: gpt-5.4-mini)" in help_text
    assert 'Change model via config: -c model="<model>"' in help_text
    assert 'Change reasoning (aka thinking) effort: --effort medium or -c model_reasoning_effort="medium" (default: medium)' in help_text
    assert "Common config settings:" in help_text
    assert "Inside the REPL, use: /model <model> [effort], for example /model gpt-5.4-mini medium" in help_text


def test_claude_help_explains_model_and_config_defaults():
    help_text = claude.build_config_help()

    assert "Models:" in help_text
    assert "Default model: haiku" in help_text
    assert "Default effort: medium" in help_text
    assert "Change model at launch: -m <model> (default: haiku)" in help_text
    assert "Change effort: --effort medium or /config effort=medium (default: medium)" in help_text
    assert "Common config settings:" in help_text
    assert "Inside the REPL:" in help_text


def test_codex_help_cli_does_not_require_server_auth(tmp_path):
    env = {"PATH": "/usr/bin", "HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "codex.py"), "--help"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--dump-fixtures" in result.stdout
    assert "--mock" in result.stdout
    assert "--effort" in result.stdout
    assert "--show-thinking" in result.stdout
    assert "--hide-thinking" in result.stdout
    assert "--show-metrics" in result.stdout
    assert "--hide-metrics" in result.stdout
    assert "--raw-json" in result.stdout
    assert "--timeout" in result.stdout
    assert "Default model: gpt-5.4-mini" in result.stdout
    assert "codex CLI not found on PATH" in result.stdout


def test_text_client_help_outputs_share_sections(tmp_path):
    env = {"PATH": "/usr/bin", "HOME": str(tmp_path)}
    codex_result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "codex.py"), "--help"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    claude_result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "claude.py"), "--help"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert codex_result.returncode == 0, codex_result.stderr
    assert claude_result.returncode == 0, claude_result.stderr
    shared_sections = ["Models:", "Common config settings:", "Inside the REPL:"]
    for section in shared_sections:
        assert section in codex_result.stdout
        assert section in claude_result.stdout
    assert "Common -c settings:" not in codex_result.stdout


def test_codex_human_error_message_extracts_json_detail():
    assert codex.human_error_message('{"detail":"bad model"}') == "bad model"
    assert codex.human_error_message({"message": '{"detail":"bad model"}'}) == "bad model"


def test_codex_turn_start_json_matches_model_summary_support():
    model_rows = [
        {"model": "gpt-5.5", "defaultReasoningEffort": "xhigh", "hidden": False},
        {"model": "gpt-5.4", "defaultReasoningEffort": "medium", "hidden": False},
        {"model": "gpt-5.4-mini", "defaultReasoningEffort": "medium", "hidden": False},
        {"model": "gpt-5.3-codex-spark", "defaultReasoningEffort": "high", "hidden": False},
        {"model": "gpt-oss-120b", "defaultReasoningEffort": "medium", "hidden": True},
        {"model": "gpt-oss-20b", "defaultReasoningEffort": "medium", "hidden": True},
        {"model": "codex-auto-review", "defaultReasoningEffort": "medium", "hidden": True},
    ]
    for row in model_rows:
        model = row["model"]
        client = CodexTextClient(codex_args(model=model, effort=row["defaultReasoningEffort"], reasoning_summary="concise"))
        client.thread_id = "thread-1"
        payload = json_rpc_request("turn-start-1", "turn/start", client.turn_params("hello"))
        decoded = json.loads(json.dumps(payload, sort_keys=True))
        params = decoded["params"]
        assert params["model"] == model
        assert params["effort"] == row["defaultReasoningEffort"]
        assert params["input"] == [{"type": "text", "text": "hello", "text_elements": []}]
        if model == "gpt-5.3-codex-spark":
            assert "summary" not in params
            assert client.thread_settings_for_config_key("model_reasoning_summary") == {}
        else:
            assert params["summary"] == "concise"
            assert client.thread_settings_for_config_key("model_reasoning_summary") == {"summary": "concise"}


def test_codex_learns_future_summary_unsupported_models():
    client = CodexTextClient(codex_args(model="future-codex-model", reasoning_summary="detailed"))
    client.thread_id = "thread-1"
    assert client.turn_params("hello")["summary"] == "detailed"
    assert codex.reasoning_summary_unsupported_error({"message": "Unsupported parameter: 'reasoning.summary' is not supported with the 'future-codex-model' model."})
    client.register_reasoning_summary_unsupported("future-codex-model")
    assert "summary" not in client.turn_params("hello")


def test_codex_reconnect_error_event_is_nonterminal(capsys):
    client = CodexTextClient(codex_args())
    assert client.handle_async_message({"jsonrpc": "2.0", "method": "error", "params": {"message": "Reconnecting... 1/5"}}, 999999999.0) is False
    assert client.turn_error_message == ""
    assert "Reconnecting... 1/5" in capsys.readouterr().err


def test_codex_tui_mode_suppresses_internal_thread_banners(capsys):
    client = CodexTextClient(codex_args())
    client.thread_id = "thread-1"
    client.print_thread_banner()
    assert "[thread] thread-1" in capsys.readouterr().err

    client = CodexTextClient(codex_args())
    client.tui_mode = True
    client.request = lambda _method, _params: {"thread": {"id": "thread-2"}}
    assert client.ensure_thread() == "thread-2"
    client.print_exit_hint()
    output = capsys.readouterr()
    assert "[thread]" not in output.err
    assert "[resume]" not in output.err


def test_claude_command_json_is_valid_for_all_client_models():
    for model in claude.CLAUDE_MODEL_CHOICES:
        client = ClaudeTextClient(claude_args(model=model, effort="medium"))
        command = client.command_for_turn("hello")
        decoded = json.loads(json.dumps({"argv": command}, sort_keys=True))
        assert decoded["argv"][decoded["argv"].index("--model") + 1] == model
        assert decoded["argv"][decoded["argv"].index("--effort") + 1] == "medium"
        assert decoded["argv"][-2:] == ["--", "hello"]


def test_prompt_editor_renders_paste_as_placeholder():
    editor = PromptLineEditor(PromptInputSession(["help", "status"]), "P> ")
    editor.insert_text("ask ")
    editor.insert_paste("line1\nline2")
    rendered, cursor = editor.rendered_text_and_cursor()
    assert rendered == "ask [Pasted content 11 chars]"
    assert cursor == len(rendered)
    assert editor.text == "ask line1\nline2"
    editor.backspace()
    assert editor.text == "ask "


def test_prompt_editor_shows_single_line_paste():
    editor = PromptLineEditor(PromptInputSession(["help", "status"]), "P> ")
    editor.insert_text("ask ")
    editor.insert_paste("status --verbose")
    rendered, cursor = editor.rendered_text_and_cursor()
    assert rendered == "ask status --verbose"
    assert cursor == len(rendered)
    assert editor.text == "ask status --verbose"

    editor.cursor = 4
    rendered, cursor = editor.rendered_text_and_cursor()
    assert rendered == "ask status --verbose"
    assert cursor == len("ask ")


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
        "effort": "medium",
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
        "dangerously_bypass_hook_trust": False,
        "strict_config": False,
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
        "show_thinking": True,
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


def test_codex_model_command_does_not_exit_when_resume_thread_is_not_loaded(capsys):
    client = CodexTextClient(codex_args(thread_id="thread-missing"))
    client.thread_id = "thread-missing"

    def missing_thread_request(method, params=None):
        assert method == "thread/settings/update"
        assert params == {"threadId": "thread-missing", "model": "gpt-5.3-codex-spark", "effort": "medium"}
        raise RuntimeError("thread/settings/update failed: thread not found: thread-missing")

    client.request = missing_thread_request
    assert client.handle_repl_command("/model gpt-5.3-codex-spark medium") == "handled"
    output = capsys.readouterr()
    assert "stored settings locally" in output.err
    assert "model changed: gpt-5.3-codex-spark (medium)" in output.out
    assert client.thread_id == "thread-missing"
    assert client.args.model == "gpt-5.3-codex-spark"
    assert client.args.effort == "medium"


def test_codex_model_command_shows_current_model_and_available_options(capsys):
    rows = [
        {"model": "gpt-5.4-mini", "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "medium"}], "hidden": False, "displayName": "GPT-5.4-Mini"},
        {"model": "gpt-5.5", "defaultReasoningEffort": "xhigh", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}, {"reasoningEffort": "xhigh"}], "hidden": False, "displayName": "GPT-5.5"},
    ]
    client = CodexTextClient(codex_args(model="gpt-5.4-mini", effort="medium"))
    client.model_catalog_rows = lambda include_hidden=True: rows

    assert client.handle_repl_command("/model") == "handled"

    output = capsys.readouterr()
    assert "compatibility command" not in output.err
    assert "Select model" in output.out
    assert "Current: gpt-5.4-mini" in output.out
    assert "Reasoning (aka Thinking) effort: medium" in output.out
    assert "Usage: /model <model> [effort]" in output.out
    assert "gpt-5.5" in output.out
    assert "GPT-5.4-Mini" in output.out


def test_codex_model_command_rejects_invalid_effort(capsys):
    client = CodexTextClient(codex_args(model="gpt-5.4-mini", effort="medium"))

    assert client.handle_repl_command("/model gpt-5.5 ultra") == "handled"

    output = capsys.readouterr()
    assert "effort must be one of" in output.out
    assert client.args.model == "gpt-5.4-mini"
    assert client.args.effort == "medium"


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

    assert client.handle_repl_command("/config text_client.show_thinking=true") == "handled"
    output = capsys.readouterr()
    assert "text_client.show_thinking = true" in output.out
    assert client.args.show_reasoning_summary is True
    assert client.args.config_values[CODEX_CONFIG_KEYS.hidden_work_summary] is True
    assert codex.CODEX_SHOW_THINKING_CONFIG_ALIAS not in client.args.config_values


def test_claude_reasoning_alias_toggles_thinking(capsys):
    client = ClaudeTextClient(claude_args(show_thinking=False))
    assert client.handle_repl_command("/reasoning on") == "handled"
    output = capsys.readouterr()
    assert "compatibility command" in output.err
    assert client.args.show_thinking is True


def test_claude_model_command_reports_effective_runtime_model(capsys):
    client = ClaudeTextClient(claude_args(model="haiku", effort="medium"))
    client.init_model = "claude-haiku-4-5-20251001"
    assert client.prompt_text().startswith("claude-haiku-4-5-20251001[medium] ")
    assert client.handle_repl_command("/model") == "handled"
    output = capsys.readouterr()
    assert "Select model" in output.out
    assert "Current:    claude-haiku-4-5-20251001" in output.out
    assert "Configured: haiku" in output.out
    assert "claude-haiku-4-5" in output.out

    assert client.handle_repl_command("/model sonnet") == "handled"
    assert client.init_model == ""
    assert client.prompt_text().startswith("sonnet[medium] ")

    assert client.handle_repl_command("/model default") == "handled"
    assert client.args.model == claude.DEFAULT_CLAUDE_MODEL


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
