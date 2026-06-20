import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import yolomux_lib.yoagent.transports as transport_module
from yolomux_lib.yoagent.transports import ClaudeChannelsTransport
from yolomux_lib.yoagent.transports import ClaudeSdkTransport
from yolomux_lib.yoagent.transports import ClaudeStreamJsonTransport
from yolomux_lib.yoagent.transports import CodexAppServerTransport
from yolomux_lib.yoagent.transports import CodexAppServerSession
from yolomux_lib.yoagent.transports import CodexExecTransport
from yolomux_lib.yoagent.transports import CodexMcpServerTransport
from yolomux_lib.yoagent.transports import CodexSdkTransport
from yolomux_lib.yoagent.transports import TMUX_LEGACY_TRANSPORT_ID
from yolomux_lib.yoagent.transports import TmuxLegacyTransport
from yolomux_lib.yoagent.transports import default_yoagent_transport_registry
from yolomux_lib.yoagent.transports import normalize_yoagent_transport_id


def test_transport_registry_order_and_aliases():
    registry = default_yoagent_transport_registry()

    assert [transport.id for transport in registry.ordered()] == [
        "claude-sdk",
        "claude-channels",
        "codex-sdk",
        "codex-app-server",
        "codex-mcp-server",
        "codex-exec",
        "claude-stream-json",
        "tmux-legacy",
    ]
    assert normalize_yoagent_transport_id("pane-paste") == TMUX_LEGACY_TRANSPORT_ID
    assert registry.get("pane-paste").id == TMUX_LEGACY_TRANSPORT_ID
    assert registry.get("tmux-legacy").label == "legacy tmux pane paste + Return"


def test_transport_registry_prefers_structured_provider_only_for_managed_targets(monkeypatch):
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    registry = default_yoagent_transport_registry()

    visible_target = {"session": "6", "pane_target": "%6", "agent_kind": "codex"}
    managed_target = {"session": "job-1", "pane_target": "%6", "agent_kind": "codex", "managed": True}

    assert registry.first_available(visible_target).id == TMUX_LEGACY_TRANSPORT_ID
    assert registry.first_available(managed_target).id == "codex-exec"


def test_tmux_legacy_transport_sends_with_injected_paste_func():
    calls = []

    def fake_paste(target, text, submit=False):
        calls.append((target, text, submit))
        return subprocess.CompletedProcess(["tmux"], 0, "", "")

    result = TmuxLegacyTransport().send(
        {"session": "6", "pane_target": "%6"},
        "tell me the date",
        submit=True,
        tmux_paste_text=fake_paste,
    )

    assert calls == [("%6", "tell me the date", True)]
    assert result.as_dict() == {
        "ok": True,
        "sent": True,
        "transport": "tmux-legacy",
        "transport_label": "legacy tmux pane paste + Return",
        "result_source": "transcript-or-screen",
        "pasted": True,
        "reason_code": "submitted",
        "returncode": 0,
    }


def test_codex_exec_transport_uses_output_last_message(monkeypatch, tmp_path):
    codex_home = tmp_path / "codex-home"
    calls = []
    monkeypatch.setenv("YOLOMUX_CODEX_HOME", str(codex_home))
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        result_path = Path(args[args.index("-o") + 1])
        result_path.write_text("Final answer from codex exec.", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, '{"type":"irrelevant"}\n', "")

    result = CodexExecTransport().send(
        {
            "session": "job-1",
            "agent_kind": "codex",
            "transport": "codex-exec",
            "managed": True,
            "cwd": "/repo/app",
            "agent_model": "gpt-5.4-mini",
            "agent_effort": "low",
            "service_tier": "fast",
        },
        "summarize the diff",
        run=fake_run,
        timeout=3,
    )

    assert result.ok is True
    assert result.transport == "codex-exec"
    assert result.result_source == "codex-exec-jsonl"
    assert result.text == "Final answer from codex exec."
    args, kwargs = calls[0]
    assert args[:2] == ["codex", "exec"]
    assert "--json" in args
    assert args[args.index("-m") + 1] == "gpt-5.4-mini"
    assert 'model_reasoning_effort="low"' in args
    assert 'service_tier="fast"' in args
    assert "-o" in args
    assert kwargs["input"] == "summarize the diff"
    assert kwargs["cwd"] == "/repo/app"
    assert kwargs["env"]["CODEX_HOME"] == str(codex_home)
    assert kwargs["env"]["TERM"] == "xterm-256color"
    assert kwargs["env"]["NO_COLOR"] == "1"


def test_codex_exec_transport_reports_timeout(monkeypatch):
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    result = CodexExecTransport().send(
        {"session": "job-1", "agent_kind": "codex", "transport": "codex-exec", "managed": True, "cwd": "/repo/app"},
        "summarize the diff",
        run=fake_run,
        timeout=3,
    )

    assert result.ok is False
    assert result.sent is False
    assert "timed out after 3" in result.error


def test_claude_sdk_transport_reports_missing_sdk(monkeypatch):
    monkeypatch.setattr(transport_module.importlib.util, "find_spec", lambda name: None)

    result = ClaudeSdkTransport().send(
        {"session": "job-1", "agent_kind": "claude", "transport": "claude-sdk", "managed": True, "cwd": "/repo/app"},
        "summarize the diff",
    )

    assert result.ok is False
    assert result.sent is False
    assert result.error == "claude-code-sdk Python package is not installed"


def test_claude_sdk_transport_uses_client_until_result_message(monkeypatch):
    monkeypatch.setattr(transport_module.importlib.util, "find_spec", lambda name: object() if name == "claude_code_sdk" else None)
    calls = []

    class FakeTextBlock:
        def __init__(self, text):
            self.text = text

    class FakeAssistantMessage:
        def __init__(self, content):
            self.content = content

    class FakeResultMessage:
        def __init__(self, result):
            self.result = result

    class FakeClaudeSDKClient:
        def __init__(self, options=None):
            calls.append(("client", options))

        async def __aenter__(self):
            calls.append(("enter",))
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            calls.append(("exit",))

        async def query(self, text, session_id="default"):
            calls.append(("query", text, session_id))

        async def receive_response(self):
            yield FakeAssistantMessage([FakeTextBlock("intermediate text")])
            yield FakeResultMessage("Final Claude SDK answer.")

    fake_sdk = SimpleNamespace(
        ClaudeSDKClient=FakeClaudeSDKClient,
        ClaudeCodeOptions=lambda **kwargs: SimpleNamespace(**kwargs),
        AssistantMessage=FakeAssistantMessage,
        TextBlock=FakeTextBlock,
        ResultMessage=FakeResultMessage,
    )
    monkeypatch.setattr(transport_module.importlib, "import_module", lambda name: fake_sdk if name == "claude_code_sdk" else None)

    result = ClaudeSdkTransport().send(
        {"session": "job-1", "agent_kind": "claude", "transport": "claude-sdk", "managed": True, "cwd": "/repo/app", "agent_model": "opus", "agent_session_id": "claude-thread"},
        "summarize the diff",
        timeout=3,
    )

    assert result.ok is True
    assert result.transport == "claude-sdk"
    assert result.result_source == "claude-sdk"
    assert result.text == "Final Claude SDK answer."
    assert calls[0][0] == "client"
    assert calls[0][1].cwd == "/repo/app"
    assert calls[0][1].model == "opus"
    assert calls[0][1].resume == "claude-thread"
    assert calls[2] == ("query", "summarize the diff", "claude-thread")


def test_claude_channels_transport_reports_missing_channel():
    result = ClaudeChannelsTransport().send(
        {"session": "6", "agent_kind": "claude", "transport": "claude-channels", "managed": False},
        "summarize the diff",
    )

    assert result.ok is False
    assert result.sent is False
    assert result.error == "target Claude pane lacks a YOLOmux Claude Channel; use tmux-legacy"


def test_claude_channels_transport_reports_missing_cli_flag(monkeypatch):
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "claude" else None)
    monkeypatch.setattr(
        transport_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "Claude help without channel support", ""),
    )

    result = ClaudeChannelsTransport().send(
        {"session": "6", "agent_kind": "claude", "transport": "claude-channels", "channel": "yolomux-channel-6"},
        "summarize the diff",
    )

    assert result.ok is False
    assert result.sent is False
    assert result.error == "installed claude CLI does not expose --channels"


def test_claude_stream_json_transport_uses_result_message(monkeypatch):
    calls = []
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "claude" else None)

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        stdout = "\n".join([
            json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning"}}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "intermediate"}]}}),
            json.dumps({"type": "result", "is_error": False, "result": "Final Claude stream answer.", "stop_reason": "end_turn"}),
        ])
        return subprocess.CompletedProcess(args, 0, stdout, "")

    result = ClaudeStreamJsonTransport().send(
        {"session": "job-1", "agent_kind": "claude", "transport": "claude-stream-json", "managed": True, "cwd": "/repo/app", "agent_model": "sonnet", "agent_effort": "low", "agent_session_id": "claude-session"},
        "summarize the diff",
        run=fake_run,
        timeout=3,
    )

    assert result.ok is True
    assert result.transport == "claude-stream-json"
    assert result.result_source == "claude-stream-json"
    assert result.text == "Final Claude stream answer."
    args, kwargs = calls[0]
    assert args[:6] == ["claude", "-p", "--verbose", "--input-format", "text", "--output-format"]
    assert "stream-json" in args
    assert ["--resume", "claude-session"] == args[args.index("--resume"):args.index("--resume") + 2]
    assert ["--model", "sonnet"] == args[args.index("--model"):args.index("--model") + 2]
    assert ["--effort", "low"] == args[args.index("--effort"):args.index("--effort") + 2]
    assert kwargs["input"] == "summarize the diff"
    assert kwargs["cwd"] == "/repo/app"


def test_claude_stream_json_transport_emits_normalized_stream_events(monkeypatch):
    events = []
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "claude" else None)

    def fake_run(args, **kwargs):
        stdout = "\n".join([
            json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Visible"}}),
            json.dumps({"type": "content_block_delta", "index": 1, "delta": {"type": "thinking_delta", "thinking": "Reading files"}}),
            json.dumps({"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Bash"}}),
            json.dumps({"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"command":"pwd"}'}}),
            json.dumps({"type": "content_block_stop", "index": 2}),
            json.dumps({"type": "result", "is_error": False, "result": "Final Claude stream answer.", "stop_reason": "end_turn"}),
        ])
        return subprocess.CompletedProcess(args, 0, stdout, "")

    result = ClaudeStreamJsonTransport().send(
        {"session": "job-1", "agent_kind": "claude", "transport": "claude-stream-json", "managed": True, "cwd": "/repo/app"},
        "summarize the diff",
        run=fake_run,
        on_event=events.append,
        timeout=3,
    )

    assert result.ok is True
    assert [event["kind"] for event in events] == ["assistant_delta", "hidden_work_delta", "tool_call_started", "tool_call_delta", "tool_call_finished", "turn_done"]
    assert events[0]["text"] == "Visible"
    assert events[1]["text"] == "Reading files"
    assert events[2]["tool_name"] == "Bash"
    assert events[4]["text"] == "pwd"


def test_claude_stream_json_transport_reports_result_error(monkeypatch):
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "claude" else None)

    def fake_run(args, **kwargs):
        stdout = json.dumps({"type": "result", "is_error": True, "result": "permission denied"})
        return subprocess.CompletedProcess(args, 0, stdout, "")

    result = ClaudeStreamJsonTransport().send(
        {"session": "job-1", "agent_kind": "claude", "transport": "claude-stream-json", "managed": True, "cwd": "/repo/app"},
        "summarize the diff",
        run=fake_run,
        timeout=3,
    )

    assert result.ok is False
    assert result.sent is False
    assert result.error == "permission denied"


def test_codex_sdk_transport_reports_missing_sdk(monkeypatch):
    monkeypatch.setattr(transport_module.importlib.util, "find_spec", lambda name: None)

    result = CodexSdkTransport().send(
        {"session": "job-1", "agent_kind": "codex", "transport": "codex-sdk", "managed": True, "cwd": "/repo/app"},
        "summarize the diff",
    )

    assert result.ok is False
    assert result.sent is False
    assert result.error == "openai-codex Python SDK is not installed"


def test_codex_sdk_transport_starts_thread_with_fake_sdk(monkeypatch):
    monkeypatch.setattr(transport_module.importlib.util, "find_spec", lambda name: object() if name == "openai_codex" else None)
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    calls = []

    class FakeThread:
        id = "thread-1"

        def run(self, text, **kwargs):
            calls.append(("run", text, kwargs))
            return SimpleNamespace(final_response="SDK final answer.")

    class FakeCodex:
        def __init__(self, config=None):
            calls.append(("Codex", config))

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            calls.append(("close",))

        def thread_start(self, **kwargs):
            calls.append(("thread_start", kwargs))
            return FakeThread()

    fake_sdk = SimpleNamespace(
        Codex=FakeCodex,
        CodexConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        Sandbox=SimpleNamespace(read_only="read-only", workspace_write="workspace-write", full_access="full-access"),
        ApprovalMode=SimpleNamespace(deny_all="deny-all", auto_review="auto-review"),
    )
    monkeypatch.setattr(transport_module.importlib, "import_module", lambda name: fake_sdk if name == "openai_codex" else None)

    result = CodexSdkTransport().send(
        {"session": "job-1", "agent_kind": "codex", "transport": "codex-sdk", "managed": True, "cwd": "/repo/app", "agent_model": "gpt-5", "sandbox": "workspace-write"},
        "summarize the diff",
    )

    assert result.ok is True
    assert result.transport == "codex-sdk"
    assert result.result_source == "codex-sdk"
    assert result.text == "SDK final answer."
    assert calls[0][0] == "Codex"
    assert calls[0][1].codex_bin == "/usr/bin/codex"
    assert calls[0][1].cwd == "/repo/app"
    assert calls[1] == ("thread_start", {"approval_mode": "auto-review", "cwd": "/repo/app", "model": "gpt-5", "sandbox": "workspace-write", "ephemeral": True})
    assert calls[2] == ("run", "summarize the diff", {"approval_mode": "auto-review", "cwd": "/repo/app", "model": "gpt-5", "sandbox": "workspace-write"})


def test_codex_sdk_transport_resumes_thread_with_fake_sdk(monkeypatch):
    monkeypatch.setattr(transport_module.importlib.util, "find_spec", lambda name: object() if name == "openai_codex" else None)
    calls = []

    class FakeThread:
        def run(self, text, **kwargs):
            calls.append(("run", text, kwargs))
            return SimpleNamespace(final_response="Resumed SDK answer.")

    class FakeCodex:
        def __init__(self, config=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return None

        def thread_resume(self, thread_id, **kwargs):
            calls.append(("thread_resume", thread_id, kwargs))
            return FakeThread()

    fake_sdk = SimpleNamespace(
        Codex=FakeCodex,
        CodexConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        Sandbox=SimpleNamespace(read_only="read-only", workspace_write="workspace-write", full_access="full-access"),
        ApprovalMode=SimpleNamespace(deny_all="deny-all", auto_review="auto-review"),
    )
    monkeypatch.setattr(transport_module.importlib, "import_module", lambda name: fake_sdk if name == "openai_codex" else None)

    result = CodexSdkTransport().send(
        {"session": "job-1", "agent_kind": "codex", "transport": "codex-sdk", "managed": True, "cwd": "/repo/app", "agent_session_id": "thread-existing", "approval_policy": "never"},
        "continue",
    )

    assert result.ok is True
    assert result.text == "Resumed SDK answer."
    assert calls[0] == ("thread_resume", "thread-existing", {"approval_mode": "deny-all", "cwd": "/repo/app", "model": None, "sandbox": "read-only"})
    assert calls[1] == ("run", "continue", {"approval_mode": "deny-all", "cwd": "/repo/app", "model": None, "sandbox": "read-only"})


class FakeCodexAppServerStdin:
    def __init__(self):
        self.messages = []

    def write(self, text):
        self.messages.append(json.loads(text))
        return len(text)

    def flush(self):
        return None


class FakeCodexAppServerProcess:
    def __init__(self, messages):
        self.stdin = FakeCodexAppServerStdin()
        self.stdout = io.StringIO("\n".join(json.dumps(message) for message in messages) + "\n")
        self.stderr = io.StringIO("")
        self._returncode = None
        self.terminated = False

    def poll(self):
        return self._returncode

    def terminate(self):
        self.terminated = True
        self._returncode = 0

    def wait(self, timeout=None):
        self._returncode = 0
        return 0

    def kill(self):
        self._returncode = -9


def test_codex_app_server_transport_runs_stdio_json_rpc_until_turn_completed(monkeypatch, tmp_path):
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("YOLOMUX_CODEX_HOME", str(codex_home))
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    messages = [
        {"jsonrpc": "2.0", "id": "initialize-1", "result": {"userAgent": "codex-cli", "codexHome": "/tmp/codex", "platformFamily": "unix", "platformOs": "linux"}},
        {"jsonrpc": "2.0", "id": "thread-1", "result": {"thread": {"id": "thread-1"}, "model": "gpt-5", "modelProvider": "openai", "cwd": "/repo/app"}},
        {"jsonrpc": "2.0", "id": "turn-1", "result": {"turn": {"id": "turn-1", "items": [], "status": "inProgress"}}},
        {"jsonrpc": "2.0", "method": "item/agentMessage/delta", "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "delta": "intermediate text"}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "items": [{"type": "agentMessage", "id": "item-1", "text": "Final app-server answer."}], "status": "completed"}}},
    ]
    fake_process = FakeCodexAppServerProcess(messages)
    calls = []
    stream_events = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return fake_process

    result = CodexAppServerTransport().send(
        {"session": "job-1", "agent_kind": "codex", "transport": "codex-app-server", "managed": True, "cwd": "/repo/app", "agent_model": "gpt-5"},
        "summarize the diff",
        popen=fake_popen,
        timeout=3,
        on_event=stream_events.append,
    )

    assert result.ok is True
    assert result.transport == "codex-app-server"
    assert result.result_source == "codex-app-server-json-rpc"
    assert result.text == "Final app-server answer."
    assert calls[0][0] == ["codex", "app-server", "--listen", "stdio://"]
    assert calls[0][1]["cwd"] == "/repo/app"
    assert calls[0][1]["env"]["CODEX_HOME"] == str(codex_home)
    assert calls[0][1]["env"]["TERM"] == "xterm-256color"
    assert calls[0][1]["env"]["NO_COLOR"] == "1"
    assert fake_process.terminated is True
    assert [message["method"] for message in fake_process.stdin.messages] == ["initialize", "initialized", "thread/start", "turn/start"]
    assert fake_process.stdin.messages[2]["params"]["cwd"] == "/repo/app"
    assert fake_process.stdin.messages[2]["params"]["model"] == "gpt-5"
    assert fake_process.stdin.messages[3]["params"]["input"] == [{"type": "text", "text": "summarize the diff", "text_elements": []}]
    assert [event["kind"] for event in stream_events] == ["assistant_delta", "turn_done"]
    assert stream_events[0]["text"] == "intermediate text"
    assert stream_events[0]["thread_id"] == "thread-1"
    assert stream_events[0]["turn_id"] == "turn-1"
    assert stream_events[0]["item_id"] == "item-1"
    assert stream_events[0]["native_type"] == "item/agentMessage/delta"
    assert stream_events[1]["native_type"] == "turn/completed"


def test_codex_app_server_transport_resumes_existing_thread(monkeypatch):
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    messages = [
        {"jsonrpc": "2.0", "id": "initialize-1", "result": {}},
        {"jsonrpc": "2.0", "id": "thread-1", "result": {"thread": {"id": "thread-existing"}}},
        {"jsonrpc": "2.0", "id": "turn-1", "result": {"turn": {"id": "turn-1", "items": [], "status": "inProgress"}}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "thread-existing", "turn": {"id": "turn-1", "items": [{"type": "agentMessage", "id": "item-1", "text": "Resumed answer."}], "status": "completed"}}},
    ]
    fake_process = FakeCodexAppServerProcess(messages)

    result = CodexAppServerTransport().send(
        {"session": "job-1", "agent_kind": "codex", "transport": "codex-app-server", "managed": True, "cwd": "/repo/app", "agent_session_id": "thread-existing"},
        "continue",
        popen=lambda *_args, **_kwargs: fake_process,
        timeout=3,
    )

    assert result.ok is True
    assert result.text == "Resumed answer."
    assert fake_process.stdin.messages[2]["method"] == "thread/resume"
    assert fake_process.stdin.messages[2]["params"]["threadId"] == "thread-existing"


def test_codex_app_server_session_resumes_or_starts_after_process_restart(monkeypatch):
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    messages = [
        {"jsonrpc": "2.0", "id": "initialize-1", "result": {}},
        {"jsonrpc": "2.0", "id": "thread-1", "error": {"code": -32600, "message": "thread not found: exec-thread"}},
        {"jsonrpc": "2.0", "id": "thread-2", "result": {"thread": {"id": "thread-new"}}},
        {"jsonrpc": "2.0", "id": "turn-1", "result": {"turn": {"id": "turn-1", "items": [], "status": "inProgress"}}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "thread-new", "turn": {"id": "turn-1", "items": [{"type": "agentMessage", "id": "item-1", "text": "Fresh thread answer."}], "status": "completed"}}},
    ]
    fake_process = FakeCodexAppServerProcess(messages)
    target = {
        "session": "job-1",
        "agent_kind": "codex",
        "transport": "codex-app-server",
        "managed": True,
        "cwd": "/repo/app",
        "agent_session_id": "exec-thread",
    }
    session = CodexAppServerSession(target, popen=lambda *_args, **_kwargs: fake_process)
    session.thread_id = "exec-thread"

    try:
        result, status = session.send("continue", target, timeout=3)
    finally:
        session.close()

    assert result.ok is True
    assert result.text == "Fresh thread answer."
    assert status["resume_error"]
    assert status["thread_started"] is True
    assert fake_process.stdin.messages[2]["method"] == "thread/resume"
    assert fake_process.stdin.messages[2]["params"]["threadId"] == "exec-thread"
    assert fake_process.stdin.messages[3]["method"] == "thread/start"
    assert fake_process.stdin.messages[4]["method"] == "turn/start"
    assert fake_process.stdin.messages[4]["params"]["threadId"] == "thread-new"


def test_codex_app_server_transport_accepts_idle_status_as_turn_done(monkeypatch):
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    messages = [
        {"jsonrpc": "2.0", "id": "initialize-1", "result": {}},
        {"jsonrpc": "2.0", "id": "thread-1", "result": {"thread": {"id": "thread-1"}}},
        {"jsonrpc": "2.0", "id": "turn-1", "result": {"turn": {"id": "turn-1", "items": [], "status": "inProgress"}}},
        {"jsonrpc": "2.0", "method": "item/agentMessage/delta", "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "delta": "Idle "}},
        {"jsonrpc": "2.0", "method": "item/agentMessage/delta", "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "delta": "answer."}},
        {"jsonrpc": "2.0", "method": "thread/status/changed", "params": {"threadId": "thread-1", "status": {"type": "idle"}}},
    ]
    fake_process = FakeCodexAppServerProcess(messages)
    events = []

    result = CodexAppServerTransport().send(
        {"session": "job-1", "agent_kind": "codex", "transport": "codex-app-server", "managed": True, "cwd": "/repo/app"},
        "continue",
        popen=lambda *_args, **_kwargs: fake_process,
        timeout=3,
        on_event=events.append,
    )

    assert result.ok is True
    assert result.text == "Idle answer."
    assert [event["kind"] for event in events] == ["assistant_delta", "assistant_delta", "turn_done"]
    assert events[-1]["native_type"] == "thread/status/changed"


def test_codex_app_server_transport_reports_unhandled_server_request(monkeypatch):
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    messages = [
        {"jsonrpc": "2.0", "id": "initialize-1", "result": {}},
        {"jsonrpc": "2.0", "id": "thread-1", "result": {"thread": {"id": "thread-1"}}},
        {"jsonrpc": "2.0", "id": "turn-1", "result": {"turn": {"id": "turn-1", "items": [], "status": "inProgress"}}},
        {"jsonrpc": "2.0", "id": "approval-1", "method": "execCommandApproval", "params": {"threadId": "thread-1"}},
    ]
    fake_process = FakeCodexAppServerProcess(messages)

    result = CodexAppServerTransport().send(
        {"session": "job-1", "agent_kind": "codex", "transport": "codex-app-server", "managed": True, "cwd": "/repo/app"},
        "run tests",
        popen=lambda *_args, **_kwargs: fake_process,
        timeout=3,
    )

    assert result.ok is False
    assert result.sent is True
    assert result.result_source == "codex-app-server-json-rpc"
    assert "approval relay is not implemented yet" in result.error


def test_codex_app_server_transport_reports_json_rpc_response_errors(monkeypatch):
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    messages = [
        {"jsonrpc": "2.0", "id": "initialize-1", "result": {}},
        {"jsonrpc": "2.0", "id": "thread-1", "error": {"code": -32000, "message": "server overloaded"}},
    ]
    fake_process = FakeCodexAppServerProcess(messages)

    result = CodexAppServerTransport().send(
        {"session": "job-1", "agent_kind": "codex", "transport": "codex-app-server", "managed": True, "cwd": "/repo/app"},
        "run tests",
        popen=lambda *_args, **_kwargs: fake_process,
        timeout=3,
    )

    assert result.ok is False
    assert result.sent is False
    assert "server overloaded" in result.error


def test_codex_mcp_server_transport_calls_codex_tool(monkeypatch):
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    messages = [
        {"jsonrpc": "2.0", "id": "initialize-1", "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {"listChanged": True}}}},
        {"jsonrpc": "2.0", "id": "tools-1", "result": {"tools": [{"name": "codex"}, {"name": "codex-reply"}]}},
        {"jsonrpc": "2.0", "id": "call-1", "result": {"structuredContent": {"threadId": "thread-1", "content": "MCP final answer."}}},
    ]
    fake_process = FakeCodexAppServerProcess(messages)

    result = CodexMcpServerTransport().send(
        {"session": "job-1", "agent_kind": "codex", "transport": "codex-mcp-server", "managed": True, "cwd": "/repo/app", "agent_model": "gpt-5"},
        "summarize the diff",
        popen=lambda *_args, **_kwargs: fake_process,
        timeout=3,
    )

    assert result.ok is True
    assert result.transport == "codex-mcp-server"
    assert result.result_source == "codex-mcp-json-rpc"
    assert result.text == "MCP final answer."
    assert [message["method"] for message in fake_process.stdin.messages] == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    call = fake_process.stdin.messages[3]
    assert call["params"]["name"] == "codex"
    assert call["params"]["arguments"]["cwd"] == "/repo/app"
    assert call["params"]["arguments"]["sandbox"] == "read-only"
    assert call["params"]["arguments"]["model"] == "gpt-5"


def test_codex_mcp_server_transport_calls_codex_reply_for_existing_thread(monkeypatch):
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    messages = [
        {"jsonrpc": "2.0", "id": "initialize-1", "result": {"protocolVersion": "2025-03-26"}},
        {"jsonrpc": "2.0", "id": "tools-1", "result": {"tools": [{"name": "codex"}, {"name": "codex-reply"}]}},
        {"jsonrpc": "2.0", "id": "call-1", "result": {"content": [{"type": "text", "text": "Reply final answer."}]}},
    ]
    fake_process = FakeCodexAppServerProcess(messages)

    result = CodexMcpServerTransport().send(
        {"session": "job-1", "agent_kind": "codex", "transport": "codex-mcp-server", "managed": True, "cwd": "/repo/app", "agent_session_id": "thread-existing"},
        "continue",
        popen=lambda *_args, **_kwargs: fake_process,
        timeout=3,
    )

    assert result.ok is True
    assert result.text == "Reply final answer."
    call = fake_process.stdin.messages[3]
    assert call["params"]["name"] == "codex-reply"
    assert call["params"]["arguments"] == {"prompt": "continue", "threadId": "thread-existing"}
