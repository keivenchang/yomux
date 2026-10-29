"""Opt-in Docker E2E scenarios for real agent CLIs and a local fake LLM.

The clients are deliberately exercised in private tmux servers.  This test does not
turn a process exit, an empty pane, or an unsupported client feature into a claimed
agent state; the JSON evidence records those outcomes for debugging instead.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import socket
import sqlite3
from pathlib import Path
import shutil
import signal
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.helpers.agent_client_fixtures import TEST_MODELS, write_client_config
from tools.product_environment import product_env
from yomux_lib.approval import prompt_detector


REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENTS = ("codex", "claude", "opencode")
SAFE_PROMPT = "Reply with exactly YOLOMUX_FAKE_OK and do nothing else."


# These are prompt-corpus fixtures rendered by the same mock TUI entry points used
# by browser tests.  Keep the matrix here deterministic and provider-independent.
MOCK_STATE_CASES = {
    "claude": {
        "active": ("fixture", "goal_active"),
        "done": ("fixture", "idle_empty_prompt"),
        "ask": ("fixture", "ask_user_question"),
    },
    "codex": {
        "active": ("fixture", "goal_active"),
        "done": ("fixture", "goal_achieved_idle_draft"),
        "ask": ("fixture", "choice_question_real"),
    },
    "opencode": {
        "active": ("fixture", "working_coordinator"),
        "done": ("fixture", "completed_output"),
        "ask": ("raw", "question"),
    },
}


@dataclass(frozen=True)
class AgentScenario:
    name: str
    fake_scenario: str
    fake_path: str
    prompt: str
    expected_evidence: tuple[str, ...]
    expected_state: str
    timeout: float
    model: str
    ask_mode: bool = False


def _opencode_events(output: str) -> list[dict]:
    """Parse OpenCode 1.18.x ``run --format json`` event lines without text scraping."""
    events = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            events.append(value)
    return events


def _opencode_output_evidence(output: str) -> dict[str, object]:
    events = _opencode_events(output)
    text = "".join(
        str(part.get("text", ""))
        for event in events
        for part in (event.get("part",),)
        if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
    )
    return {
        "events": events,
        "event_types": [event["type"] for event in events],
        "text": text,
        "completed": any(event.get("type") in {"step_finish", "step-finish"} for event in events),
        "error": any(event.get("type") == "error" for event in events),
        "tool": any(event.get("type") in {"tool_use", "tool_call", "tool_result"} for event in events),
    }


def _opencode_database_evidence(data_home: Path, *, start_ms: int = 0) -> dict[str, object]:
    database = data_home / "opencode" / "opencode.db"
    evidence: dict[str, object] = {"path": str(database), "exists": database.exists(), "sessions": 0, "parts": 0, "token_rows": 0}
    if not database.exists():
        return evidence
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            session_count = connection.execute("SELECT count(*) FROM session WHERE time_updated >= ?", (start_ms,)).fetchone()[0]
            part_count = connection.execute("SELECT count(*) FROM part WHERE time_updated >= ?", (start_ms,)).fetchone()[0]
            token_rows = connection.execute(
                "SELECT count(*) FROM part WHERE time_updated >= ? AND json_extract(data, '$.type') = 'step-finish' AND json_extract(data, '$.tokens') IS NOT NULL",
                (start_ms,),
            ).fetchone()[0]
        evidence.update({"sessions": session_count, "parts": part_count, "token_rows": token_rows})
    except sqlite3.Error as error:
        evidence["error"] = str(error)
    return evidence


SCENARIOS = (
    AgentScenario("active", "hold", "/scenario/active", SAFE_PROMPT, ("YOLOMUX_FAKE_OK",), "active", 8.0, ""),
    AgentScenario("done", "normal", "/scenario/done", SAFE_PROMPT, ("YOLOMUX_FAKE_OK",), "done", 12.0, ""),
    AgentScenario("ask", "tool", "/scenario/ask", "Run the harmless fake approval action and wait for approval.", ("approval", "permission", "Bash", "exec_command"), "ask", 12.0, "", True),
    AgentScenario("query", "normal", "/scenario/query", SAFE_PROMPT, ("YOLOMUX_FAKE_OK",), "query", 12.0, ""),
)


def _capability(client: str) -> tuple[bool, str]:
    if product_env("AGENT_DOCKER_E2E") != "1":
        return False, f"{client}: real-client E2E is disabled; set YOMUX_AGENT_DOCKER_E2E=1"
    artifact = shutil.which(client)
    if artifact is None:
        return False, f"{client}: required executable is unavailable"
    if not os.path.exists(artifact):
        return False, f"{client}: required client artifact is unavailable ({artifact})"
    if not os.environ.get("HOME") or not os.environ.get("XDG_CONFIG_HOME"):
        return False, f"{client}: isolated HOME/XDG configuration is unavailable"
    if client == "codex" and not os.environ.get("CODEX_HOME"):
        return False, "codex: isolated CODEX_HOME is unavailable"
    return True, ""


def _require_capability(client: str) -> None:
    capable, reason = _capability(client)
    if not capable:
        if product_env("AGENT_DOCKER_E2E") == "1":
            pytest.fail(reason)
        pytest.skip(reason)


def _tmux(binary: str, socket_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([binary, "-S", str(socket_path), *args], text=True, capture_output=True, check=False, timeout=8)


def _cleanup_tmux_session(binary: str, socket_path: Path, session: str) -> None:
    """Retire one fixture-owned session and prove it is gone."""
    result = _tmux(binary, socket_path, "kill-session", "-t", session)
    probe = _tmux(binary, socket_path, "has-session", "-t", session)
    if probe.returncode == 0:
        detail = result.stderr.strip() or result.stdout.strip() or "kill-session did not retire the session"
        raise AssertionError(f"tmux session {session} survived cleanup: {detail}")
    if probe.returncode != 1:
        detail = probe.stderr.strip() or probe.stdout.strip() or f"has-session exited {probe.returncode}"
        raise AssertionError(f"could not prove tmux session {session} was retired: {detail}")


def _cleanup_tmux_server(binary: str, socket_path: Path) -> None:
    """Stop the fixture-owned server and prove the private socket has no server."""
    result = _tmux(binary, socket_path, "kill-server")
    probe = _tmux(binary, socket_path, "has-server")
    if probe.returncode == 0:
        detail = result.stderr.strip() or result.stdout.strip() or "kill-server did not retire the server"
        raise AssertionError(f"tmux server survived cleanup: {detail}")
    if probe.returncode != 1:
        detail = probe.stderr.strip() or probe.stdout.strip() or f"has-server exited {probe.returncode}"
        raise AssertionError(f"could not prove tmux server was retired: {detail}")


def _cleanup_all_tmux_sessions(binary: str, socket_path: Path) -> None:
    """Retire every session on this private server, then prove the server is gone."""

    listing = _tmux(binary, socket_path, "list-sessions", "-F", "#S")
    if listing.returncode not in (0, 1):
        detail = listing.stderr.strip() or listing.stdout.strip() or f"list-sessions exited {listing.returncode}"
        raise AssertionError(f"could not enumerate private tmux sessions: {detail}")
    for listed in listing.stdout.splitlines():
        if listed:
            _cleanup_tmux_session(binary, socket_path, listed)
    _cleanup_tmux_server(binary, socket_path)


def _stop_process(process: subprocess.Popen | None, label: str) -> None:
    """Stop a fixture child and fail if its process remains alive."""
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    if process.poll() is None:
        raise AssertionError(f"{label} survived cleanup")


def _remove_owned_directory(path: Path, label: str) -> None:
    """Remove a fixture-owned directory and prove removal succeeded."""
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise AssertionError(f"{label} cleanup failed for {path}: {error}") from error
    if path.exists():
        raise AssertionError(f"{label} remained after cleanup: {path}")


def _pid_is_alive(pid: str) -> bool:
    if not pid.isdigit():
        return False
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _wait_for_stream(stream) -> str:
    value = stream.readline()
    if not value:
        raise AssertionError("fake LLM server exited before publishing its port")
    return value.strip()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_fake(fake_env: dict[str, str], port: int) -> subprocess.Popen:
    env = {**fake_env, "FAKE_LLM_PORT": str(port)}
    return subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "tools" / "fake_llm_server.py")],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )


def _probe_local_http_server(server: ThreadingHTTPServer) -> float:
    started = time.monotonic()
    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=1) as response:
        assert response.status == 200
    return time.monotonic() - started


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib handler API
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        return


def _run_failure_recovery(client: str, failure: str, tmp_path: Path) -> dict[str, object]:
    """Exercise a failed request, teardown, provider restart, and a fresh successful request."""
    tmux_binary = shutil.which("tmux")
    if not tmux_binary:
        pytest.skip("tmux is unavailable")
    root = tmp_path / f"{client}-{failure}"
    home, xdg, state_dir, config_dir = (root / name for name in ("home", "xdg", "state", "client-config"))
    for path in (home, xdg, state_dir, config_dir):
        path.mkdir(mode=0o700, parents=True)
    log = root / "fake-llm.jsonl"
    # tmux rejects long socket names. Keep the private socket directory shallow while
    # retaining all human-readable evidence under the pytest-owned tmp_path.
    socket_dir = Path(tempfile.mkdtemp(prefix="yo-t-"))
    socket_path = socket_dir / "s"
    session = f"yolo-recovery-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    fake_env = {
        **os.environ,
        "FAKE_LLM_LOG": str(log),
        "FAKE_LLM_TIMESTAMP_START": "2000",
        "FAKE_LLM_TIMESTAMP_STEP": "1",
        "FAKE_LLM_TIMESTAMP_SEQUENCE": "0",
    }
    fake = _start_fake(fake_env, port)
    health = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    health_thread = threading.Thread(target=health.serve_forever, daemon=True)
    health_thread.start()
    evidence: dict[str, object] = {"client": client, "failure": failure, "session": session, "port": port}
    try:
        _wait_for_stream(fake.stdout)
        failure_scenario = AgentScenario("failure", failure, f"/scenario/{failure}", SAFE_PROMPT, (), "failed", 4.0, "")
        config = write_client_config(client, config_dir, port, failure_scenario.fake_path)
        command, client_env = _client_command(client, TEST_MODELS[client], config, failure_scenario.prompt)
        if client == "opencode":
            client_env = {**client_env, "OPENCODE_E2E_OUTPUT": str(root / "opencode-events.jsonl")}
        endpoint = f"http://127.0.0.1:{port}{failure_scenario.fake_path}"
        env = {
            **os.environ,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg / "config"),
            "XDG_DATA_HOME": str(xdg / "data"),
            "XDG_STATE_HOME": str(xdg / "state"),
            "YOLOMUX_STATE_DIR": str(state_dir),
            "YOLOMUX_TMUX_SOCKET": str(socket_path),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "OPENAI_API_KEY": "yomux-test-only",
            "ANTHROPIC_API_KEY": "yomux-test-only",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            **client_env,
            **_endpoint_environment(client, endpoint),
        }
        if client == "opencode":
            env["XDG_CONFIG_HOME"] = str(config.parent.parent)
        passed = {"HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "YOLOMUX_STATE_DIR", "YOLOMUX_TMUX_SOCKET", "NO_PROXY", "no_proxy", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", *client_env, *_endpoint_environment(client, endpoint)}
        created = _tmux(tmux_binary, socket_path, "new-session", "-d", "-s", session, "-x", "100", "-y", "35", "env", *[f"{key}={env[key]}" for key in passed], *command)
        assert created.returncode == 0, created.stderr
        client_process = _pane_process(tmux_binary, socket_path, session)
        failed_records = _wait_for_request(log, client, failure_scenario, 10.0)
        probe_latency = _probe_local_http_server(health)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and not _request_records(log):
            probe_latency = min(probe_latency, _probe_local_http_server(health))
            time.sleep(0.1)
        failed_records = _wait_for_scenario_records(log, failure, 10.0)
        assert failed_records, f"fake provider did not log the {failure} request: {log}; records={_request_records(log)}"
        _assert_scenario_paths(failed_records, failure_scenario)
        evidence.update({"failure_records": failed_records, "health_probe_seconds": probe_latency, "pane_before_cleanup": _capture(tmux_binary, socket_path, session)})
        evidence["processes_before_cleanup"] = subprocess.run(
            ["ps", "-eo", "pid,ppid,stat,args"], text=True, capture_output=True, check=False
        ).stdout

        # Exercise lifecycle loss independently from provider failure.  The pane PID is captured
        # before killing it so the evidence identifies the process that was intentionally retired.
        if failure == "client-exit":
            pane_pid = client_process["pid"]
            evidence["client_pane_pid"] = pane_pid
            evidence["client_process_command"] = client_process["command"]
            if pane_pid.isdigit():
                try:
                    os.kill(int(pane_pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and _pid_is_alive(pane_pid):
                time.sleep(0.1)
            evidence["client_exited"] = not _pid_is_alive(pane_pid)
        if failure == "tmux-loss":
            _tmux(tmux_binary, socket_path, "kill-server")
            evidence["tmux_lost"] = _tmux(tmux_binary, socket_path, "has-session", "-t", session).returncode != 0

        # Explicit client/session loss must not leave a surviving child or stale tmux session.
        _cleanup_tmux_session(tmux_binary, socket_path, session)
        evidence["session_gone"] = True
        _stop_process(fake, "fake LLM server")
        evidence["fake_server_stopped"] = fake.poll() is not None
        restarted: subprocess.Popen | None = None
        restarted = _start_fake({**fake_env, "FAKE_LLM_SCENARIO": "normal"}, port)
        try:
            _wait_for_stream(restarted.stdout)
            recovery = AgentScenario("recovery", "normal", "/scenario/normal", SAFE_PROMPT, ("YOLOMUX_FAKE_OK",), "done", 8.0, "")
            recovery_config = write_client_config(client, config_dir / "recovery", port, recovery.fake_path)
            recovery_command, recovery_client_env = _client_command(
                client, TEST_MODELS[client], recovery_config, recovery.prompt
            )
            if client == "opencode":
                recovery_client_env = {
                    **recovery_client_env,
                    "OPENCODE_E2E_OUTPUT": str(root / "recovery-opencode-events.jsonl"),
                }
            recovery_endpoint = f"http://127.0.0.1:{port}{recovery.fake_path}"
            recovery_env = {**env, **recovery_client_env, **_endpoint_environment(client, recovery_endpoint)}
            if client == "opencode":
                recovery_env["XDG_CONFIG_HOME"] = str(recovery_config.parent.parent)
            recovery_passed = {
                "HOME",
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
                "XDG_STATE_HOME",
                "YOLOMUX_STATE_DIR",
                "YOLOMUX_TMUX_SOCKET",
                "NO_PROXY",
                "no_proxy",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                *recovery_client_env,
                *_endpoint_environment(client, recovery_endpoint),
            }
            recovery_session = f"{session}-recovery"
            created = _tmux(
                tmux_binary,
                socket_path,
                "new-session",
                "-d",
                "-s",
                recovery_session,
                "-x",
                "100",
                "-y",
                "35",
                "env",
                *[f"{key}={recovery_env[key]}" for key in recovery_passed],
                *recovery_command,
            )
            assert created.returncode == 0, created.stderr
            recovery_records = _wait_for_request(log, client, recovery, 10.0)
            deadline = time.monotonic() + recovery.timeout
            while time.monotonic() < deadline and "YOLOMUX_FAKE_OK" not in _capture(tmux_binary, socket_path, recovery_session):
                time.sleep(0.2)
            recovery_pane = _capture(tmux_binary, socket_path, recovery_session)
            recovery_records = _wait_for_scenario_records(log, "normal", 2.0)
            _assert_scenario_paths(recovery_records, recovery)
            # Noninteractive clients exit after printing the response, so the tmux pane may be
            # empty by the time the provider record is observed. The provider record is the
            # durable recovery proof in that case.
            evidence.update({"recovery_session": recovery_session, "recovery_pane": recovery_pane, "recovery_records": recovery_records, "recovered": bool(recovery_records)})
            evidence["processes_after_recovery"] = subprocess.run(
                ["ps", "-eo", "pid,ppid,stat,args"], text=True, capture_output=True, check=False
            ).stdout
            _cleanup_tmux_session(tmux_binary, socket_path, recovery_session)
        finally:
            _stop_process(restarted, "restarted fake LLM server")
        evidence_path = root / "evidence.json"
        evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        evidence["evidence_path"] = str(evidence_path)
        return evidence
    finally:
        _cleanup_tmux_session(tmux_binary, socket_path, session)
        _cleanup_tmux_server(tmux_binary, socket_path)
        _stop_process(fake, "fake LLM server")
        _remove_owned_directory(socket_dir, "tmux socket directory")
        health.shutdown()
        health.server_close()
        health_thread.join(timeout=5)
        if health_thread.is_alive():
            raise AssertionError("health server thread survived cleanup")


def _client_command(client: str, model: str, config: Path, prompt: str) -> tuple[list[str], dict[str, str]]:
    if client == "codex":
        workspace = config.parent.parent / "workspace"
        if "approval" in prompt.lower():
            return [client, "exec", "--ephemeral", "--skip-git-repo-check", "--model", model, "--sandbox", "workspace-write", "--cd", str(workspace), prompt], {"CODEX_HOME": str(config.parent)}
        return [client, "exec", "--ephemeral", "--skip-git-repo-check", "--model", model, "--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust", "--cd", str(workspace), prompt], {"CODEX_HOME": str(config.parent)}
    if client == "claude":
        if "approval" in prompt.lower():
            return [client, "--bare", "--no-session-persistence", "--model", model, "--settings", str(config), "--allowedTools", "Bash", prompt], {"CLAUDE_CONFIG_DIR": str(config.parent)}
        return [client, "--bare", "--print", "--no-session-persistence", "--model", model, "--settings", str(config), prompt], {"CLAUDE_CONFIG_DIR": str(config.parent)}
    command = " ".join(shlex.quote(value) for value in (client, "run", "--format", "json", "--model", model, prompt) if value)
    if "approval" in prompt.lower():
        command = " ".join(shlex.quote(value) for value in (client, "run", "--format", "json", "--model", model, "--interactive", prompt))
    return ["sh", "-lc", f"{command} > \"$OPENCODE_E2E_OUTPUT\"; status=$?; cat \"$OPENCODE_E2E_OUTPUT\"; exit $status"], {"XDG_CONFIG_HOME": str(config.parent.parent)}


@pytest.mark.parametrize("client", CLIENTS)
def test_client_command_uses_one_scenario_prompt(client: str, tmp_path: Path):
    config = tmp_path / "config"
    prompt = "scenario-specific prompt"
    command, _ = _client_command(client, "test-model", config, prompt)
    rendered = shlex.join(command)
    assert rendered.count(prompt) == 1
    assert SAFE_PROMPT not in rendered


def _endpoint_environment(client: str, endpoint: str) -> dict[str, str]:
    if client == "codex":
        return {"OPENAI_BASE_URL": f"{endpoint}/v1"}
    if client == "claude":
        return {"ANTHROPIC_BASE_URL": endpoint}
    return {}


def _capture(tmux_binary: str, socket_path: Path, session: str, *, scrollback: bool = False) -> str:
    history = ["-S", "-2000"] if scrollback else []
    captured = _tmux(tmux_binary, socket_path, "capture-pane", "-p", "-J", *history, "-t", f"{session}:")
    return captured.stdout


@pytest.mark.parametrize("scrollback", [False, True])
def test_capture_preserves_empty_pane_after_noninteractive_client_exit(monkeypatch, tmp_path, scrollback):
    monkeypatch.setattr(sys.modules[__name__], "_tmux", lambda *_args: subprocess.CompletedProcess("tmux", 1, "", "can't find pane"))
    assert _capture("tmux", tmp_path / "s", "exited-client", scrollback=scrollback) == ""


def _pane_process(tmux_binary: str, socket_path: Path, session: str) -> dict[str, str]:
    result = _tmux(tmux_binary, socket_path, "list-panes", "-t", session, "-F", "#{pane_pid}\t#{pane_current_command}")
    pid, _, command = result.stdout.strip().partition("\t")
    return {"pid": pid, "command": command}


def _provider_scenario(scenario: AgentScenario) -> str:
    return scenario.fake_path.split("/scenario/", 1)[-1].split("/", 1)[0]


def _mock_command(client: str) -> list[str]:
    return [sys.executable, str(REPO_ROOT / "tools" / "mockers" / f"{client}.py"), "--mock"]


def _start_private_mock(tmux_binary: str, socket_path: Path, session: str, client: str, root: Path, ready_file: Path) -> None:
    env = {
        "HOME": str(root / "home"),
        "PYTHONPATH": str(REPO_ROOT),
        "TERM": "xterm-256color",
        "PYTHONUNBUFFERED": "1",
        "YOLOMUX_MOCK_INPUT_READY_FILE": str(ready_file),
    }
    (root / "home").mkdir(parents=True, exist_ok=True)
    passed = [f"{key}={value}" for key, value in env.items()]
    command = _tmux(
        tmux_binary,
        socket_path,
        "new-session",
        "-d",
        "-s",
        session,
        "-x",
        "126",
        "-y",
        "35",
        "env",
        *passed,
        *_mock_command(client),
    )
    assert command.returncode == 0, command.stderr


def _send_mock_input(tmux_binary: str, socket_path: Path, session: str, value: str) -> None:
    sent = _tmux(tmux_binary, socket_path, "send-keys", "-t", f"{session}:", value, "Enter")
    assert sent.returncode == 0, sent.stderr


def _wait_for_mock_state(
    tmux_binary: str,
    socket_path: Path,
    session: str,
    client: str,
    expected: set[str],
    timeout: float = 5.0,
    response_markers: tuple[str, ...] = (),
) -> tuple[str, dict[str, object]]:
    deadline = time.monotonic() + timeout
    pane = ""
    transcript = ""
    state: dict[str, object] = {}
    while time.monotonic() < deadline:
        pane = _capture(tmux_binary, socket_path, session)
        state = prompt_detector.agent_screen_state(pane, pane_target=f"%{session}", agent_kind=client)
        if client == "opencode" and state.get("key") == "idle" and "idle" not in expected:
            # Match the production fallback when OpenCode's footer hides the choice block.
            state = prompt_detector.agent_screen_state(pane, pane_target=f"%{session}")
        # A completed response can scroll above the viewport; only the visible pane owns activity.
        transcript = _capture(tmux_binary, socket_path, session, scrollback=True) if response_markers else ""
        markers_ready = not response_markers or any(marker in transcript.lower() for marker in response_markers)
        if state.get("key") in expected and markers_ready:
            return pane, state
        time.sleep(0.1)
    raise AssertionError(f"mock state deadline exceeded: {json.dumps({'socket': str(socket_path), 'session': session, 'client': client, 'expected': sorted(expected), 'state': state, 'response_markers': response_markers, 'pane': pane, 'transcript': transcript})}")


@pytest.mark.parametrize("client", CLIENTS)
def test_mock_state_classifies_visible_pane_and_finds_scrolled_response(monkeypatch, tmp_path, client):
    module = sys.modules[__name__]
    visible = "idle composer"
    transcript = "response marker\nDo you want to proceed?\n" + visible
    monkeypatch.setattr(module, "_capture", lambda *_args, scrollback=False: transcript if scrollback else visible)

    def classify(pane, **_kwargs):
        assert pane == visible, "historic prompts must not enter the activity classifier"
        return {"key": "idle"}

    monkeypatch.setattr(prompt_detector, "agent_screen_state", classify)
    pane, state = _wait_for_mock_state("tmux", tmp_path / "s", "fixture", client, {"idle"}, response_markers=("response marker",))
    assert pane == visible and state["key"] == "idle"


@pytest.mark.parametrize("key,transcript", [("idle", "missing response"), ("working", "response marker")])
def test_mock_state_deadline_rejects_missing_response_or_wrong_state(monkeypatch, tmp_path, key, transcript):
    module = sys.modules[__name__]
    clock = iter((0.0, 0.0, 6.0))
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _seconds: None))
    monkeypatch.setattr(module, "_capture", lambda *_args, scrollback=False: transcript if scrollback else "visible pane")
    monkeypatch.setattr(prompt_detector, "agent_screen_state", lambda *_args, **_kwargs: {"key": key})
    with pytest.raises(AssertionError, match="mock state deadline exceeded") as failure:
        _wait_for_mock_state("tmux", tmp_path / "s", "fixture", "codex", {"idle"}, response_markers=("response marker",))
    assert "visible pane" in str(failure.value) and transcript in str(failure.value)


def test_mock_state_wait_preserves_opencode_choice_fallback(monkeypatch, tmp_path):
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_capture", lambda *_args, **_kwargs: "choice block above footer")
    monkeypatch.setattr(prompt_detector, "agent_screen_state", lambda *_args, **kwargs: {"key": "idle" if kwargs.get("agent_kind") else "needs-input"})
    _, state = _wait_for_mock_state("tmux", tmp_path / "s", "fixture", "opencode", {"needs-input", "approval"})
    assert state["key"] == "needs-input"


def _wait_for_mock_input_ready(ready_file: Path, tmux_binary: str, socket_path: Path, session: str, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            marker = ready_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            marker = ""
        if "input_surface=ready" in marker:
            return marker
        time.sleep(0.05)
    pane = _tmux(tmux_binary, socket_path, "capture-pane", "-p", "-t", f"{session}:")
    processes = _tmux(tmux_binary, socket_path, "list-panes", "-t", session, "-F", "#{pane_pid}\t#{pane_dead}\t#{pane_dead_status}\t#{pane_current_command}")
    process_rows = subprocess.run(["ps", "-eo", "pid,ppid,stat,comm"], capture_output=True, text=True, check=False, timeout=8)
    diagnostics = {
        "ready_file": str(ready_file), "ready_file_exists": ready_file.exists(),
        "pane": pane.stdout, "pane_error": pane.stderr,
        "pane_process": processes.stdout, "pane_process_error": processes.stderr,
        "processes": process_rows.stdout,
    }
    pane_pid = processes.stdout.partition("\t")[0].strip()
    if pane_pid.isdecimal():
        try:
            raw_environment = Path(f"/proc/{pane_pid}/environ").read_bytes()
        except (FileNotFoundError, PermissionError) as error:
            diagnostics["marker_environment_error"] = str(error)
        else:
            diagnostics["marker_environment"] = [
                value.decode("utf-8", errors="replace") for value in raw_environment.split(b"\x00")
                if value.startswith((b"YOMUX_MOCK_INPUT_READY_FILE=", b"YOLOMUX_MOCK_INPUT_READY_FILE="))
            ]
    raise AssertionError(f"mock input surface did not become ready: {json.dumps(diagnostics)}")


@pytest.mark.parametrize("client", CLIENTS)
def test_mock_tmux_state_lane_classifies_active_done_ask_and_query(client: str, tmp_path: Path):
    """Exercise deterministic mock TUI states in a private tmux server.

    This lane intentionally does not use the fake provider: it covers the visual
    state classifier and response pane independently of live ask feasibility.
    """
    tmux_binary = shutil.which("tmux")
    if not tmux_binary:
        pytest.skip("tmux is unavailable")
    socket_dir = Path(tempfile.mkdtemp(prefix="yo-state-"))
    socket_path = socket_dir / "s"
    evidence: dict[str, object] = {"client": client, "states": {}}
    try:
        for state_name, (command, fixture) in MOCK_STATE_CASES[client].items():
            session = f"yolo-state-{client}-{state_name}-{uuid.uuid4().hex[:8]}"
            ready_file = tmp_path / f"mock-ready-{client}-{state_name}-{uuid.uuid4().hex[:8]}"
            _start_private_mock(tmux_binary, socket_path, session, client, tmp_path, ready_file)
            _wait_for_mock_input_ready(ready_file, tmux_binary, socket_path, session)
            _wait_for_mock_state(tmux_binary, socket_path, session, client, {"idle", "working", "needs-input", "approval"})
            input_value = fixture if command == "raw" else f"{command} {fixture}"
            _send_mock_input(tmux_binary, socket_path, session, input_value)
            expected = {"working"} if state_name == "active" else {"idle"} if state_name == "done" else {"needs-input", "approval"}
            pane, state = _wait_for_mock_state(tmux_binary, socket_path, session, client, expected)
            evidence["states"][state_name] = {"key": state.get("key"), "pane": pane}
            assert state.get("key") in expected, evidence
            _cleanup_tmux_session(tmux_binary, socket_path, session)

        session = f"yolo-state-{client}-query-{uuid.uuid4().hex[:8]}"
        ready_file = tmp_path / f"mock-ready-{client}-query-{uuid.uuid4().hex[:8]}"
        _start_private_mock(tmux_binary, socket_path, session, client, tmp_path, ready_file)
        _wait_for_mock_input_ready(ready_file, tmux_binary, socket_path, session)
        _wait_for_mock_state(tmux_binary, socket_path, session, client, {"idle"})
        _send_mock_input(tmux_binary, socket_path, session, "What model are you using?")
        response_marker = "i'm running" if client == "opencode" else "i don't know how to handle"
        pane, state = _wait_for_mock_state(
            tmux_binary, socket_path, session, client, {"idle"}, response_markers=(response_marker,)
        )
        transcript = _capture(tmux_binary, socket_path, session, scrollback=True)
        evidence["query"] = {"key": state.get("key"), "pane": pane, "transcript": transcript,
            "socket": str(socket_path), "session": session, "process": _pane_process(tmux_binary, socket_path, session), "root": str(tmp_path)}
        assert state.get("key") == "idle", evidence
        assert response_marker in transcript.lower(), evidence
    finally:
        _cleanup_all_tmux_sessions(tmux_binary, socket_path)
        _remove_owned_directory(socket_dir, "tmux socket directory")


def _request_records(log: Path) -> list[dict]:
    if not log.exists():
        return []
    records = []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _event_records(log: Path) -> list[dict]:
    if not log.exists():
        return []
    records = []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("phase") in {"started", "completed"}:
            records.append(value)
    return records


def _read_exit_code(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _scenario_evidence(
    client: str,
    scenario: AgentScenario,
    *,
    provider_records: list[dict],
    lifecycle_records: list[dict],
    stdout: str,
    stderr: str,
    pane: str,
    opencode_output: str,
    opencode_database: dict[str, object] | None,
    exit_code: int | None,
    process_alive: bool,
) -> dict[str, object]:
    matching = [record for record in lifecycle_records if record.get("scenario") == _provider_scenario(scenario)]
    started = any(record.get("phase") == "started" for record in matching)
    completed = any(record.get("phase") == "completed" for record in matching)
    output = _opencode_output_evidence(opencode_output) if client == "opencode" else {}
    combined = f"{stdout}\n{stderr}\n{pane}".lower()
    tool_response = any(record.get("response_kind") == "tool" for record in matching)
    client_tool = bool(output.get("tool")) if client == "opencode" else any(
        marker in combined for marker in ("tool_use", "tool call", "tool_call", "function_call", "request_approval")
    )
    approval = any(marker in combined for marker in ("approval", "approve?", "permission", "request_approval"))
    if not approval and client == "claude":
        approval = tool_response and client_tool
    successful_exit = exit_code == 0
    database_completion = bool(opencode_database and (
        opencode_database.get("sessions", 0) and opencode_database.get("parts", 0)
    ))
    durable_output = "yolomux_fake_ok" in f"{stdout}\n{stderr}".lower()
    durable_completion = successful_exit and (
        (bool(output.get("completed")) and output.get("text") == "YOLOMUX_FAKE_OK")
        and database_completion
        if client == "opencode"
        else durable_output
    )
    latest_phase = next((record.get("phase") for record in reversed(matching)), None)
    if scenario.expected_state == "active":
        state = "active" if started and latest_phase == "started" and process_alive else "unknown"
    elif scenario.name == "ask":
        state = "ask" if started and tool_response and client_tool and approval and process_alive else (
            "done" if started and completed and tool_response and successful_exit and not process_alive else "unknown"
        )
    elif scenario.expected_state in {"done", "query"}:
        state = scenario.expected_state if started and completed and durable_completion else "unknown"
    else:
        state = "unknown"
    return {
        "state": state,
        "provider_request": started,
        "provider_completed": completed,
        "provider_tool_response": tool_response,
        "client_tool": client_tool,
        "approval": approval,
        "process_alive": process_alive,
        "exit_code": exit_code,
        "durable_completion": durable_completion,
        "opencode_output": output,
        "opencode_database": opencode_database,
        "provider_records": provider_records,
    }


def _wait_for_scenario_records(log: Path, scenario: str, timeout: float) -> list[dict]:
    deadline = time.monotonic() + timeout
    records = _request_records(log)
    while time.monotonic() < deadline and not any(record.get("scenario") == scenario for record in records):
        time.sleep(0.1)
        records = _request_records(log)
    return [record for record in records if record.get("scenario") == scenario]


def _client_output_diagnostics(directory: Path) -> str:
    diagnostics = ""
    for name in ("client.stdout", "client.stderr"):
        path = directory / name
        if path.exists():
            diagnostics += f"\n{name}:\n{path.read_text(encoding='utf-8', errors='replace')}"
    return diagnostics


def _wait_for_request(log: Path, client: str, scenario: AgentScenario, timeout: float) -> list[dict]:
    deadline = time.monotonic() + timeout
    records = _request_records(log)
    expected = _provider_scenario(scenario)
    while time.monotonic() < deadline and not any(record.get("scenario") == expected for record in records):
        time.sleep(0.1)
        records = _request_records(log)
    matching = [record for record in records if record.get("scenario") == expected]
    diagnostics = _client_output_diagnostics(log.parent)
    assert matching, f"{client}/{scenario.name}: real client did not reach configured fake-provider scenario; log={log}; records={records}{diagnostics}"
    return matching


def _wait_for_provider_start(event_log: Path, scenario: AgentScenario, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(
            event.get("scenario") == _provider_scenario(scenario) and event.get("phase") == "started"
            for event in _event_records(event_log)
        ):
            return
        time.sleep(0.1)
    raise AssertionError(
        f"provider did not start {scenario.name} request: {event_log}; events={_event_records(event_log)}"
        f"{_client_output_diagnostics(event_log.parent)}"
    )


def _assert_scenario_paths(records: list[dict], scenario: AgentScenario) -> None:
    expected_prefix = scenario.fake_path + "/"
    assert all(str(record.get("path", "")).startswith(expected_prefix) for record in records), (
        scenario.name,
        scenario.fake_path,
        records,
    )


def _usage(records: list[dict]) -> dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for record in records:
        usage = record.get("usage") if isinstance(record, dict) else None
        if not isinstance(usage, dict):
            continue
        input_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
        output_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["total_tokens"] += int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
    return totals


def _token_evidence(records: list[dict]) -> dict[str, object]:
    """Aggregate structured fake-provider usage over its deterministic request window."""
    token_records = [record for record in records if isinstance(record, dict) and isinstance(record.get("usage"), dict)]
    totals = _usage(token_records)
    timestamps = [float(record["timestamp"]) for record in token_records if isinstance(record.get("timestamp"), (int, float))]
    timestamps.sort()
    elapsed = timestamps[-1] - timestamps[0] if len(timestamps) > 1 else 0.0
    rates = {
        key.replace("_tokens", "_tokens_per_minute"): (value * 60.0 / elapsed if elapsed > 0 else 0.0)
        for key, value in totals.items()
    }
    return {
        "input_tokens": totals["input_tokens"],
        "output_tokens": totals["output_tokens"],
        "total_tokens": totals["total_tokens"],
        "input_tokens_per_minute": rates["input_tokens_per_minute"],
        "output_tokens_per_minute": rates["output_tokens_per_minute"],
        "total_tokens_per_minute": rates["total_tokens_per_minute"],
        "elapsed_seconds": elapsed,
        "request_timestamps": timestamps,
    }


def _yostats_snapshot(state_dir: Path, start: float, end: float) -> dict[str, int] | None:
    """Read the disposable YO!stats usage projection when a real client created it."""
    database = state_dir / "stats" / "yostats.sqlite3"
    if not database.exists():
        return None
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT direction, payload_json FROM usage_atoms WHERE observed_at >= ? AND observed_at < ?",
                (start, end),
            )
            for direction, payload_json in rows:
                quantity = int(json.loads(payload_json).get("quantity", 0))
                if direction == "input":
                    totals["input_tokens"] += quantity
                elif direction == "output":
                    totals["output_tokens"] += quantity
                totals["total_tokens"] += quantity
    except (sqlite3.Error, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return totals


def _scenario_matched(evidence: dict[str, object], scenario: AgentScenario) -> bool:
    return evidence.get("state") == scenario.expected_state


def _run_scenario(client: str, scenario: AgentScenario, tmp_path: Path, *, require_match: bool = True) -> dict:
    tmux_binary = shutil.which("tmux")
    if not tmux_binary:
        pytest.skip("tmux is unavailable")
    home, xdg, state_dir, config_dir = (tmp_path / name for name in ("home", "xdg", "state", "client-config"))
    for path in (home, xdg, state_dir, config_dir):
        path.mkdir(mode=0o700)
    log = tmp_path / "fake-llm.jsonl"
    evidence_root = Path(os.environ.get("YOLOMUX_E2E_EVIDENCE_DIR", str(tmp_path)))
    evidence_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    evidence_path = evidence_root / f"{client}-{scenario.name}-{uuid.uuid4().hex[:8]}.json"
    # Keep the tmux socket under a short /tmp path; the pytest tmp_path can be deeply
    # nested in Docker/Bazel and exceeds tmux's Unix-socket path limit.
    socket_dir = Path(tempfile.mkdtemp(prefix="yo-t-"))
    socket_path = socket_dir / "s"
    session = f"yolo-agent-e2e-{uuid.uuid4().hex[:8]}"
    fake_env = {
        **os.environ,
        "FAKE_LLM_LOG": str(log),
        "FAKE_LLM_EVENT_LOG": str(tmp_path / "fake-llm-events.jsonl"),
        "FAKE_LLM_SCENARIO": scenario.fake_scenario,
        "FAKE_LLM_TIMESTAMP_START": "1000",
        "FAKE_LLM_TIMESTAMP_STEP": "10",
    }
    fake = _start_fake(fake_env, _free_port())
    pane_samples: list[dict[str, object]] = []
    evidence: dict[str, object] = {"client": client, "scenario": scenario.name, "expected_state": scenario.expected_state, "socket": str(socket_path)}
    try:
        port = int(_wait_for_stream(fake.stdout))
        config = write_client_config(client, config_dir, port, scenario.fake_path)
        command, client_env = _client_command(client, scenario.model or TEST_MODELS[client], config, scenario.prompt)
        stdout_path = tmp_path / "client.stdout"
        stderr_path = tmp_path / "client.stderr"
        exit_path = tmp_path / "client.exit"
        command = [
            "sh",
            "-lc",
            f"{shlex.join(command)} >{shlex.quote(str(stdout_path))} 2>{shlex.quote(str(stderr_path))}; status=$?; printf '%s\\n' $status >{shlex.quote(str(exit_path))}; exit $status",
        ]
        opencode_output_path = tmp_path / "opencode-events.jsonl"
        if client == "opencode":
            client_env = {**client_env, "OPENCODE_E2E_OUTPUT": str(opencode_output_path)}
        endpoint = f"http://127.0.0.1:{port}{scenario.fake_path}"
        env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(xdg / "config"), "XDG_DATA_HOME": str(xdg / "data"), "XDG_STATE_HOME": str(xdg / "state"), "YOLOMUX_STATE_DIR": str(state_dir), "YOLOMUX_TMUX_SOCKET": str(socket_path), "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost", "OPENAI_API_KEY": "yomux-test-only", "ANTHROPIC_API_KEY": "yomux-test-only", "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", **client_env, **_endpoint_environment(client, endpoint)}
        if client == "opencode":
            env["XDG_CONFIG_HOME"] = str(config.parent.parent)
        passed = {"HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "YOLOMUX_STATE_DIR", "YOLOMUX_TMUX_SOCKET", "NO_PROXY", "no_proxy", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", *client_env, *_endpoint_environment(client, endpoint)}
        created = _tmux(tmux_binary, socket_path, "new-session", "-d", "-s", session, "-x", "100", "-y", "35", "env", *[f"{key}={env[key]}" for key in passed], *command)
        assert created.returncode == 0, created.stderr
        initial_process = _pane_process(tmux_binary, socket_path, session)
        time.sleep(0.5)
        evidence.update({"expected_evidence": scenario.expected_evidence})
        event_log = tmp_path / "fake-llm-events.jsonl"
        if scenario.expected_state == "active":
            _wait_for_provider_start(event_log, scenario, min(10.0, scenario.timeout))
            records = _request_records(log)
        else:
            records = _wait_for_request(log, client, scenario, min(10.0, scenario.timeout))
        if records:
            _assert_scenario_paths(records, scenario)
        deadline = time.monotonic() + scenario.timeout
        matched = False
        while time.monotonic() < deadline:
            pane = _capture(tmux_binary, socket_path, session)
            opencode_output = opencode_output_path.read_text(encoding="utf-8", errors="replace") if client == "opencode" and opencode_output_path.exists() else ""
            records = _request_records(log)
            lifecycle_records = _event_records(event_log)
            stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
            exit_code = _read_exit_code(exit_path)
            pane_process = _pane_process(tmux_binary, socket_path, session)
            pane_pid = pane_process["pid"]
            process_alive = _pid_is_alive(pane_pid)
            database_evidence = _opencode_database_evidence(xdg / "data", start_ms=0) if client == "opencode" else None
            scenario_evidence = _scenario_evidence(
                client,
                scenario,
                provider_records=records,
                lifecycle_records=lifecycle_records,
                stdout=stdout,
                stderr=stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else "",
                pane=pane,
                opencode_output=opencode_output,
                opencode_database=database_evidence,
                exit_code=exit_code,
                process_alive=process_alive,
            )
            pane_samples.append({"at": time.monotonic(), "pane": pane, "opencode_output": opencode_output, "request_count": len(records), "scenario_evidence": scenario_evidence})
            if _scenario_matched(scenario_evidence, scenario):
                matched = True
                break
            time.sleep(0.25)
        records = _request_records(log)
        pane = _capture(tmux_binary, socket_path, session) if _tmux(tmux_binary, socket_path, "has-session", "-t", session).returncode == 0 else ""
        listing = _tmux(tmux_binary, socket_path, "list-sessions", "-F", "#{session_name}:#{pane_pid}")
        process_listing = subprocess.run(["ps", "-eo", "pid,ppid,stat,args"], text=True, capture_output=True, check=False).stdout
        token_evidence = _token_evidence(records)
        timestamps = token_evidence["request_timestamps"]
        token_evidence["yostats_snapshot"] = _yostats_snapshot(
            state_dir,
            timestamps[0] if timestamps else 0,
            timestamps[-1] + 1 if timestamps else 1,
        )
        opencode_output = opencode_output_path.read_text(encoding="utf-8", errors="replace") if client == "opencode" and opencode_output_path.exists() else ""
        output_evidence = _opencode_output_evidence(opencode_output) if client == "opencode" else None
        if client == "opencode":
            output_evidence["pane"] = _opencode_output_evidence(pane)
        database_evidence = _opencode_database_evidence(xdg / "data", start_ms=0) if client == "opencode" else None
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
        exit_code = _read_exit_code(exit_path)
        lifecycle_records = _event_records(event_log)
        pane_process = _pane_process(tmux_binary, socket_path, session)
        pane_pid = pane_process["pid"]
        process_alive = _pid_is_alive(pane_pid)
        final_scenario_evidence = _scenario_evidence(
            client,
            scenario,
            provider_records=records,
            lifecycle_records=lifecycle_records,
            stdout=stdout,
            stderr=stderr,
            pane=pane,
            opencode_output=opencode_output,
            opencode_database=database_evidence,
            exit_code=exit_code,
            process_alive=process_alive,
        )
        evidence.update({"model": TEST_MODELS[client], "config": str(config), "command": command, "matched": matched, "state_claimed": scenario.expected_state if matched else None, "scenario_evidence": final_scenario_evidence, "client_process_pid": initial_process["pid"], "client_process_command": initial_process["command"], "client_process_pid_at_final_capture": pane_pid, "client_process_alive_at_capture": process_alive, "pane": pane, "pane_samples": pane_samples, "tmux": listing.stdout, "ps": process_listing, "request_count": len(records), "usage": _usage(records), "token_evidence": token_evidence, "requests": records, "provider_events": lifecycle_records, "opencode_output": output_evidence, "opencode_database": database_evidence, "stdout": stdout, "stderr": stderr, "exit_code": exit_code})
        evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        if require_match:
            assert matched, f"{client}/{scenario.name}: expected evidence/state was not observed; evidence: {evidence_path}"
        return evidence
    finally:
        _cleanup_tmux_session(tmux_binary, socket_path, session)
        _cleanup_tmux_server(tmux_binary, socket_path)
        _stop_process(fake, "fake LLM server")
        _remove_owned_directory(socket_dir, "tmux socket directory")


@pytest.mark.e2e
@pytest.mark.no_browser
@pytest.mark.socket
@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_real_agent_status_scenarios(client: str, scenario: AgentScenario, tmp_path: Path):
    _require_capability(client)
    if scenario.ask_mode:
        pytest.skip(f"{client}: noninteractive fixture cannot hold an interactive approval prompt; mock ask coverage is explicit")
    evidence = _run_scenario(client, scenario, tmp_path)
    assert evidence["request_count"] > 0
    assert evidence["state_claimed"] == scenario.expected_state
    token_evidence = evidence["token_evidence"]
    assert token_evidence["total_tokens"] > 0, evidence
    assert token_evidence["input_tokens"] > 0 and token_evidence["output_tokens"] > 0, evidence
    assert len(token_evidence["request_timestamps"]) == evidence["request_count"], evidence
    assert all(request["scenario"] == _provider_scenario(scenario) for request in evidence["requests"]), evidence
    assert all(request["model"] == TEST_MODELS[client].split("/", 1)[-1] for request in evidence["requests"]), evidence
    if client == "opencode":
        output = evidence["opencode_output"]
        database = evidence["opencode_database"]
        if scenario.name == "done":
            assert output["completed"] and output["text"] == "YOLOMUX_FAKE_OK", evidence
            assert database["sessions"] > 0 and database["token_rows"] > 0, evidence
        elif scenario.name == "ask":
            assert output["tool"] or "permission" in evidence["pane"].lower(), evidence


@pytest.mark.e2e
@pytest.mark.no_browser
@pytest.mark.socket
@pytest.mark.parametrize("client", ("claude", "codex"))
def test_real_agent_request_smoke(client: str, tmp_path: Path):
    """The real CLI must make one successful request to the fake provider."""
    _require_capability(client)
    evidence = _run_scenario(client, SCENARIOS[1], tmp_path, require_match=False)
    assert evidence["request_count"] > 0, evidence
    assert evidence["scenario_evidence"]["durable_completion"], evidence


def test_token_evidence_rate_is_deterministic() -> None:
    records = [
        {"timestamp": 1000, "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}},
        {"timestamp": 1010, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}},
    ]
    evidence = _token_evidence(records)
    assert evidence["total_tokens"] == 20
    assert evidence["elapsed_seconds"] == 10
    assert evidence["total_tokens_per_minute"] == 120


def test_scenario_matcher_does_not_require_tmux_for_completed_clients() -> None:
    scenario = AgentScenario("done", "normal", "/scenario/done", SAFE_PROMPT, (), "done", 1, "")
    evidence = _scenario_evidence(
        "claude",
        scenario,
        provider_records=[{"scenario": "normal"}],
        lifecycle_records=[{"scenario": "done", "phase": "started"}, {"scenario": "done", "phase": "completed"}],
        stdout="YOLOMUX_FAKE_OK\n",
        stderr="",
        pane="",
        opencode_output="",
        opencode_database=None,
        exit_code=0,
        process_alive=False,
    )
    assert _scenario_matched(evidence, scenario)
    assert evidence["process_alive"] is False


def test_scenario_matcher_requires_inflight_request_for_active() -> None:
    scenario = SCENARIOS[0]
    completed = _scenario_evidence(
        "codex",
        scenario,
        provider_records=[{"scenario": "hold"}],
        lifecycle_records=[{"scenario": "hold", "phase": "started"}, {"scenario": "hold", "phase": "completed"}],
        stdout="",
        stderr="",
        pane="",
        opencode_output="",
        opencode_database=None,
        exit_code=None,
        process_alive=True,
    )
    assert not _scenario_matched(completed, scenario)


def test_scenario_matcher_rejects_auto_completed_tool_as_ask() -> None:
    scenario = SCENARIOS[2]
    evidence = _scenario_evidence(
        "codex",
        scenario,
        provider_records=[{"scenario": "tool"}],
        lifecycle_records=[{"scenario": "ask", "phase": "started", "response_kind": ""}, {"scenario": "ask", "phase": "completed", "response_kind": "tool"}],
        stdout="tool_call request_approval\n",
        stderr="",
        pane="",
        opencode_output="",
        opencode_database=None,
        exit_code=0,
        process_alive=False,
    )
    assert evidence["state"] == "done"
    assert not _scenario_matched(evidence, scenario)


def test_scenario_matcher_requires_explicit_tool_and_approval_for_ask() -> None:
    scenario = SCENARIOS[2]
    evidence = _scenario_evidence(
        "claude",
        scenario,
        provider_records=[{"scenario": "tool"}],
        lifecycle_records=[{"scenario": "ask", "phase": "started"}, {"scenario": "ask", "phase": "completed", "response_kind": "tool"}],
        stdout="request_approval\n",
        stderr="",
        pane="Permission required. Approve?",
        opencode_output="",
        opencode_database=None,
        exit_code=None,
        process_alive=True,
    )
    assert _scenario_matched(evidence, scenario)


@pytest.mark.e2e
@pytest.mark.no_browser
@pytest.mark.socket
@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("failure", ("hold", "disconnect", "error", "client-exit", "tmux-loss"))
def test_real_agent_failure_recovery_and_isolation(client: str, failure: str, tmp_path: Path):
    """Opt-in real-client coverage for provider faults and a later successful query."""
    _require_capability(client)
    evidence = _run_failure_recovery(client, failure, tmp_path)
    assert evidence["session_gone"] is True, evidence
    assert evidence["recovered"] is True, evidence
    assert float(evidence["health_probe_seconds"]) < 2.0, evidence
    assert evidence["failure_records"], evidence
    assert any(record["scenario"] == failure for record in evidence["failure_records"]), evidence
    assert any(record["scenario"] == "normal" for record in evidence["recovery_records"]), evidence
    if failure == "client-exit":
        assert evidence["client_exited"] is True, evidence
        assert evidence.get("client_pane_pid", "").isdigit(), evidence
    if failure == "tmux-loss":
        assert evidence["tmux_lost"] is True, evidence


def test_canonical_real_client_opt_in_is_admitted(monkeypatch):
    monkeypatch.setenv("YOMUX_AGENT_DOCKER_E2E", "1")
    monkeypatch.delenv("YOLOMUX_AGENT_DOCKER_E2E", raising=False)
    monkeypatch.setenv("HOME", "/fixture/home")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/fixture/config")
    monkeypatch.setenv("CODEX_HOME", "/fixture/codex")
    monkeypatch.setattr(shutil, "which", lambda _name: "/fixture/client")
    monkeypatch.setattr(os.path, "exists", lambda _name: True)
    assert _capability("codex") == (True, "")


def test_opted_in_missing_client_fails_instead_of_skipping(monkeypatch):
    monkeypatch.setenv("YOMUX_AGENT_DOCKER_E2E", "1")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(pytest.fail.Exception, match="required executable"):
        _require_capability("codex")
