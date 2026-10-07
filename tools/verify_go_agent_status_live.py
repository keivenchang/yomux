#!/usr/bin/env python3
"""Run a deterministic live verification of the Go agent collectors."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = Path(__file__).resolve().parents[1]
CLIENTS = ("claude", "codex", "opencode")
SYNTHETIC_OPENCODE_SESSION_ID = "ses-synthetic"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.product_environment import product_env, product_env_name, require_product_env
from tools.instance_isolation import normalize_yomux_environment
from tools.instance_isolation import synthetic_test_environment
from tools.generate_go_version import generate_go_version_overlay


def run(*args: str, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, env=env, text=True, capture_output=True, check=check)


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def copy_live_jsonl(source: Path, destination: Path, now: float) -> None:
    records = []
    for line in source.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        record["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - (len(records) + 1)))
        records.append(json.dumps(record, separators=(",", ":")))
    destination.write_text("\n".join(records) + "\n", encoding="utf-8")


def write_synthetic_codex_token_usage_jsonl(destination: Path, now: float) -> None:
    """Create the current Codex token_usage_record shape without host input."""
    records = [
        {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 2)),
            "type": "session_meta",
            "payload": {"id": "synthetic-codex-thread", "model": "gpt-synthetic"},
        },
    ]
    for offset, input_tokens, cached_tokens, output_tokens, thread_input, thread_cached, thread_output in (
        (1, 10, 2, 5, 10, 2, 5),
        (0, 6, 3, 4, 16, 5, 9),
    ):
        usage = {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "reasoning_output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
        thread_usage = {
            "input_tokens": thread_input,
            "cached_input_tokens": thread_cached,
            "output_tokens": thread_output,
            "reasoning_output_tokens": thread_output,
            "total_tokens": thread_input + thread_output,
        }
        records.append(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - offset)),
                "type": "token_usage_record",
                "payload": {
                    "thread_id": "synthetic-codex-thread",
                    "turn_id": f"synthetic-turn-{offset}",
                    "response_id": f"synthetic-response-{offset}",
                    "usage": usage,
                    "turn_token_usage": usage,
                    "thread_token_usage": thread_usage,
                },
            }
        )
    destination.write_text(
        "\n".join(json.dumps(record, separators=(",", ":")) for record in records) + "\n",
        encoding="utf-8",
    )


def make_opencode_database(path: Path, now_ms: int, session_id: str = "ses-live") -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE session (id TEXT, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL);
        CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
        CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
        CREATE TABLE session_input (id TEXT, session_id TEXT, delivery TEXT, admitted_seq INTEGER, promoted_seq INTEGER, time_created INTEGER);
        """
    )
    # The mock process inherits the repository cwd, matching the directory OpenCode
    # persists for a live project session. A database temp directory is not the
    # process identity boundary and made directory-based resolution miss.
    connection.execute("INSERT INTO session VALUES (?, ?, ?, ?, ?, ?, ?)", (session_id, str(REPO), "build", '{"providerID":"openai","id":"gpt-5.6-luna"}', now_ms - 45000, now_ms - 5000, 0))
    connection.execute("INSERT INTO message VALUES (?, ?, ?, ?, ?)", ("msg-live", session_id, now_ms - 30000, now_ms - 30000, '{"role":"assistant","providerID":"openai","modelID":"gpt-5.6-luna"}'))
    connection.execute("INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)", ("part-live", "msg-live", session_id, now_ms - 30000, now_ms - 30000, '{"type":"step-finish","tokens":{"total":10,"input":3,"output":5,"cache":{"read":2,"write":0}}}'))
    connection.commit()
    connection.close()


def write_auth(config: Path) -> None:
    config.mkdir(mode=0o700, parents=True, exist_ok=True)
    (config / "auth.yaml").write_text("users:\n  - username: live\n    password: live-password\n    role: admin\n", encoding="utf-8")
    (config / "auth-cookie-secret").write_text("11" * 32, encoding="ascii")


def wait_for(url: str, headers: dict[str, str], process: subprocess.Popen[str], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Go server exited: {process.returncode}")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for {url}")


def get_json(url: str, headers: dict[str, str]) -> dict[str, object]:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as response:
        return json.load(response)


def wait_for_roster(
    url: str,
    headers: dict[str, str],
    expected: dict[str, str],
    *,
    timeout: float = 20,
) -> dict[str, object]:
    """Wait for one persisted agent-status roster, not just a successful HTTP response."""
    deadline = time.monotonic() + timeout
    latest: dict[str, object] = {}
    while time.monotonic() < deadline:
        latest = get_json(url, headers)
        roster = latest.get("agents")
        if isinstance(roster, list):
            states = {
                str(item.get("kind")): str(item.get("state"))
                for item in roster
                if isinstance(item, dict) and item.get("kind")
            }
            if all(states.get(kind) == state for kind, state in expected.items()):
                return latest
        time.sleep(0.25)
    raise AssertionError(f"agent roster did not reach {expected}: {latest}")


def set_opencode_running_fixture(database: Path, running: bool) -> None:
    """Toggle a synthetic running-tool row; never touches an operator database."""
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM part WHERE id = 'part-running-fixture'")
        if running:
            connection.execute(
                "INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "part-running-fixture",
                    "msg-live",
                    "ses-live",
                    int(time.time() * 1000),
                    int(time.time() * 1000),
                    '{"type":"tool","state":{"status":"running"}}',
                ),
            )
        connection.commit()


def main() -> int:
    tmux = shutil.which("tmux")
    python = shutil.which("python3")
    if not tmux or not python:
        raise RuntimeError("tmux and python3 are required")

    with tempfile.TemporaryDirectory(prefix="yolomux-go-live-") as raw_root:
        root = Path(raw_root)
        server_root = root / "server-root"
        server_root.mkdir()
        socket_path = root / "tmux.sock"
        config = root / "config"
        stats = root / "stats.sqlite3"
        home = root / "home"
        (home / "claude" / "projects").mkdir(parents=True)
        (home / "codex" / "sessions").mkdir(parents=True)
        (home / ".GlobalProtect").mkdir()
        write_auth(config)
        now = time.time()
        make_opencode_database(root / "opencode.db", int(now * 1000))
        copy_live_jsonl(REPO / "tests/fixtures/current_model_usage/observed.claude.jsonl", home / "claude" / "projects" / "live.jsonl", now)
        write_synthetic_codex_token_usage_jsonl(home / "codex" / "sessions" / "live.jsonl", now)
        opencode_interpreter = root / "opencode"
        shutil.copy(Path(python).resolve(), opencode_interpreter)
        opencode_interpreter.chmod(0o700)
        prefix = "yolo-live-" + root.name[-8:]
        sessions = {client: f"{prefix}-{client}" for client in CLIENTS}
        env = synthetic_test_environment()
        private_environment_roots = {
            "XDG_CONFIG_HOME": root / "xdg-config",
            "XDG_DATA_HOME": root / "xdg-data",
            "XDG_STATE_HOME": root / "xdg-state",
            "XDG_CACHE_HOME": root / "xdg-cache",
            "XDG_RUNTIME_DIR": root / "xdg-runtime",
            "CODEX_HOME": root / "codex-home",
            "CLAUDE_CONFIG_DIR": root / "claude-config",
            "TMPDIR": root / "tmp",
        }
        for private_root in private_environment_roots.values():
            private_root.mkdir(parents=True, exist_ok=True)
        env.update({
            "HOME": str(home),
            **{key: str(value) for key, value in private_environment_roots.items()},
            "PYTHONPATH": str(REPO), "TERM": "xterm-256color", "PYTHONUNBUFFERED": "1",
            product_env_name('TMUX_SOCKET'): str(socket_path), product_env_name('OPENCODE_DATABASE'): str(root / "opencode.db"),
            product_env_name('CLAUDE_TRANSCRIPT_ROOT'): str(home / "claude" / "projects"),
            product_env_name('CODEX_TRANSCRIPT_ROOT'): str(home / "codex" / "sessions"),
            "PATH": os.environ.get("PATH", ""),
        })
        started_sessions: list[str] = []
        server: subprocess.Popen[str] | None = None
        stats_stream = None
        server_log_path = root / "server.log"
        try:
            for client in CLIENTS:
                script = REPO / "tools/mockers" / f"{client}.py"
                if client == "opencode":
                    command = f"exec -a opencode {opencode_interpreter} {script} --mock --session ses-live"
                else:
                    command = f"exec -a {client} {python} {script} --mock"
                run(tmux, "-S", str(socket_path), "new-session", "-d", "-s", sessions[client], "-x", "126", "-y", "35", "bash", "-lc", command, env=env)
                started_sessions.append(sessions[client])
            for session in started_sessions:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    capture = run(tmux, "-S", str(socket_path), "capture-pane", "-p", "-J", "-t", f"{session}:", env=env, check=False)
                    if capture.returncode == 0 and capture.stdout.strip():
                        break
                    time.sleep(0.1)
                else:
                    raise TimeoutError(f"empty pane: {session}")

            # Keep each pane in its native idle state. Injecting a fixture command
            # after startup leaves the mock's persistent prompt/composer visible
            # alongside the fixture's completion or working markers, which is
            # intentionally ambiguous evidence to the production classifier.

            inventory = run(tmux, "-S", str(socket_path), "list-panes", "-a", "-F", "#{session_name}\t#{pane_id}\t#{pane_pid}\t#{pane_current_command}", env=env).stdout

            port = free_port()
            configured_binary = product_env('GO_BINARY', "", environ=os.environ).strip()
            go_binary = Path(configured_binary) if configured_binary else None
            if go_binary is None:
                go_binary = root / "yolomux-go"
                build_env = os.environ.copy()
                generated_version = root / "product_version_generated.go"
                go_overlay = root / "go-overlay.json"
                generate_go_version_overlay(
                    REPO / "yomux_lib" / "version.py",
                    generated_version,
                    go_overlay,
                    REPO / "cmd" / "yomux-go" / "product_version_generated.go",
                )
                run("go", "build", "-buildvcs=false", "-overlay", str(go_overlay), "-o", str(go_binary), "./cmd/yomux-go", env=build_env)
            server_env = env | {
                product_env_name('DEBUG_AGENT_STATUS'): "1",
                # This verifier owns a private tmux socket. Scope native
                # OpenCode discovery to that socket so unrelated operator
                # processes cannot contaminate the isolated fixture's root.
                product_env_name('OPENCODE_TMUX_SCOPE'): "1",
                product_env_name('OPENCODE_DATABASE'): str(root / "opencode.db"),
                # Never admit host /proc, memory, or GPU input into a
                # development verifier. The Go server's explicit synthetic
                # source carries the measured CPU fixtures instead.
                product_env_name('STATS_SYNTHETIC_HOST'): "1",
                product_env_name('STATS_SYNTHETIC_INVENTORY'): "1",
                product_env_name('CLAUDE_TRANSCRIPT_ROOT'): str(home / "claude" / "projects"),
                product_env_name('CODEX_TRANSCRIPT_ROOT'): str(home / "codex" / "sessions"),
                product_env_name('PRICING_CATALOG'): str(REPO / "yomux_lib" / "data" / "model_pricing.json"),
            }
            print(json.dumps({"verification": "starting", product_env_name('TMUX_SOCKET'): require_product_env('TMUX_SOCKET', environ=server_env), "tmux_panes": inventory.splitlines()}, sort_keys=True), flush=True)
            server_log = server_log_path.open("w", encoding="utf-8")
            command = [str(go_binary), "--http", "--host", "127.0.0.1", "--port", str(port), "--root", str(server_root), "--config", str(config), "--stats", str(stats)]
            if product_env('LIVE_STRACE', environ=os.environ):
                command = ["strace", "-f", "-e", "trace=execve", "-o", str(root / "strace.log"), *command]
            server = subprocess.Popen(command, env=server_env, stdout=server_log, stderr=subprocess.STDOUT, text=True)
            base = f"http://127.0.0.1:{port}"
            auth = {"Authorization": "Basic bGl2ZTpsaXZlLXBhc3N3b3Jk"}
            wait_for(base + "/healthz", {}, server)
            wait_for(base + "/api/stats-agent-status", auth, server)
            # Keep the same demand lease that the browser's stats stream holds. Without
            # an active stream the collector intentionally falls back to its 60-second
            # idle cadence, so a 45-second probe would misclassify a healthy roster as stale.
            stats_stream = urllib.request.urlopen(
                urllib.request.Request(
                    base + "/api/stats-stream?range_seconds=300&resolution=AUTO&client_id=live-status",
                    headers=auth,
                ),
                timeout=5,
            )
            first_stream_line = stats_stream.readline().decode("utf-8", errors="replace")
            if not first_stream_line.startswith("id: "):
                raise AssertionError(f"stats stream did not start with an SSE id: {first_stream_line!r}")
            system_status = get_json(base + "/api/system-status", auth)
            watcher = system_status.get("tmux_signal_watcher")
            if not isinstance(watcher, dict) or watcher.get("state") != "attached" or watcher.get("healthy") is not True:
                raise AssertionError(f"synthetic tmux watcher did not attach: {system_status}")
            if set(watcher.get("sessions", [])) != set(started_sessions) or watcher.get("process_pid") != 0:
                raise AssertionError(f"synthetic tmux watcher roster/identity was wrong: {watcher}")
            status_url = base + "/api/stats-agent-status"
            wait_for_roster(status_url, auth, {"claude": "idle", "codex": "idle", "opencode": "idle"})
            # Exercise the actual pane polling and the OpenCode state reader in one
            # synthetic roster: Codex is visibly working, Claude is asking for input,
            # and OpenCode has a matching synthetic running-tool row. This proves that
            # the persisted roster exposes both green (run) and red (ask) activity.
            set_opencode_running_fixture(root / "opencode.db", True)
            for client, command in (("claude", "mock ask_user_question"), ("codex", "mock goal_active")):
                sent = run(tmux, "-S", str(socket_path), "send-keys", "-t", f"{sessions[client]}:", command, "Enter", env=env, check=False)
                if sent.returncode != 0:
                    raise AssertionError(f"could not drive {client} synthetic state: {sent.stderr}")
            status = wait_for_roster(status_url, auth, {"claude": "ask", "codex": "run", "opencode": "run"})
            counts = status.get("counts")
            if not isinstance(counts, dict) or counts.get("running") != 2 or counts.get("red") != 1:
                raise AssertionError(f"running/red counts did not follow the synthetic roster: {status}")
            time.sleep(45)
            status = get_json(status_url, auth)
            system_status_after = get_json(base + "/api/system-status", auth)
            runtime_diagnostics = system_status_after.get("stats_runtime", {})
            host_diagnostics = runtime_diagnostics.get("collectors", {}).get("host", {})
            process_diagnostics = runtime_diagnostics.get("process_diagnostics", {})
            if host_diagnostics.get("last_cpu_process_rows") != 3 or host_diagnostics.get("last_memory_process_rows") != 3:
                raise AssertionError(
                    "synthetic host collector admitted non-fixture rows: "
                    f"{host_diagnostics}"
                )
            if process_diagnostics.get("last_agent_inventory_rows") != 3:
                raise AssertionError(
                    "synthetic agent inventory admitted non-fixture rows: "
                    f"{process_diagnostics}"
                )
            captures = {}
            for session in started_sessions:
                captures[session] = run(tmux, "-S", str(socket_path), "capture-pane", "-p", "-J", "-S", "-12", "-t", f"{session}:", env=env, check=False).stdout
            connection = sqlite3.connect(stats)
            observations = connection.execute("SELECT family, payload_json FROM observations ORDER BY family, observed_at").fetchall()
            unavailable = connection.execute("SELECT family, source_id, reason FROM unavailable_spans ORDER BY family, started_at").fetchall()
            usage = connection.execute("SELECT direction, cache_role, unit, payload_json FROM usage_atoms ORDER BY event_id, direction").fetchall()
            usage_times = [row[0] for row in connection.execute("SELECT observed_at FROM usage_atoms WHERE unit = 'tokens'").fetchall()]
            connection.close()
            if not usage:
                raise AssertionError(
                    f"live collector persisted no usage_atoms; observations={observations}; unavailable={unavailable}; "
                    f"inventory={inventory}; status={status}"
                )
            cpu_rows = [json.loads(payload) for family, payload in observations if family == "cpu"]
            memory_rows = [json.loads(payload) for family, payload in observations if family == "system_memory"]
            gpu_rows = [json.loads(payload) for family, payload in observations if family == "gpu"]
            if len(cpu_rows) < 2 or not any(float(row.get("system_percent", 0)) > 0 for row in cpu_rows):
                raise AssertionError(f"live collector persisted insufficient CPU facts: {cpu_rows}")
            if not memory_rows or not any(int(row.get("used_bytes", 0)) > 0 for row in memory_rows):
                raise AssertionError(f"live collector persisted no positive memory facts: {memory_rows}")
            if not gpu_rows and not any(family == "gpu" for family, _source_id, _reason in unavailable):
                raise AssertionError("live collector persisted neither GPU facts nor explicit GPU unavailable evidence")
            if any(timestamp < now - 60 or timestamp >= now for timestamp in usage_times):
                raise AssertionError(f"live usage_atoms timestamps are outside the query window: {usage_times}")
            usage_payloads = [json.loads(payload) for _, _, _, payload in usage]
            providers_models = {(payload.get("provider"), payload.get("model")) for payload in usage_payloads}
            # Codex is priced through Python's OpenAI catalog identity; the source kind remains
            # explicit so provider attribution and execution attribution are both preserved.
            expected_providers = {"claude", "openai"}
            if {provider for provider, _ in providers_models} != expected_providers:
                raise AssertionError(f"live usage_atoms providers = {providers_models}")
            if not any(payload.get("provider") == "openai" and payload.get("execution_source") == "codex" for payload in usage_payloads):
                raise AssertionError(f"live usage_atoms omitted Python-compatible Codex execution attribution: {usage_payloads}")
            rates = status["token_rates"]
            if not rates["total_per_second"] > 0 or not rates["input_per_second"] > 0 or not rates["output_per_second"] > 0:
                raise AssertionError(f"live token rates are not positive: {rates}")
            if not rates.get("provider_models"):
                raise AssertionError("live token rates have no provider/model attribution")
            snapshot = get_json(base + "/api/stats-snapshot?range_seconds=300&resolution=AUTO&client_id=live-status", auth)
            cost_report = snapshot.get("cost_report", {})
            if int(cost_report.get("total_tokens", 0)) <= 0 or int(cost_report.get("unpriced", {}).get("tokens", 0)) <= 0:
                raise AssertionError(f"live cost report did not preserve usage: {cost_report}")
            print(json.dumps({"tmux_panes": inventory.splitlines(), "tmux_watcher": watcher, "captures": captures, "api": status, "system_status": system_status_after, "snapshot": snapshot, "db": {"observations": [{"family": family, "payload": json.loads(payload)} for family, payload in observations], "unavailable": [{"family": family, "source_id": source_id, "reason": reason} for family, source_id, reason in unavailable], "usage_atoms": [{"direction": direction, "cache_role": cache_role, "unit": unit, "payload": json.loads(payload)} for direction, cache_role, unit, payload in usage]}}, sort_keys=True))
            server_log.flush()
            print(server_log_path.read_text(encoding="utf-8"), flush=True)
        finally:
            if server and server.poll() is None:
                server.send_signal(signal.SIGTERM)
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
            if stats_stream is not None:
                stats_stream.close()
            if 'server_log' in locals():
                server_log.close()
            for session in started_sessions:
                run(tmux, "-S", str(socket_path), "kill-session", "-t", session, env=env, check=False)
            run(tmux, "-S", str(socket_path), "kill-server", env=env, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
