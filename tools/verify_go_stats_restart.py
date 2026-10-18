#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Qualify fixture-only Go storage, live stats, and crash/restart convergence."""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
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
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.instance_isolation import synthetic_test_environment
from tools.verify_go_agent_status_live import make_opencode_database, run, write_auth


AUTH = {"Authorization": "Basic " + base64.b64encode(b"live:live-password").decode()}
SNAPSHOT = "/api/stats-snapshot?range_seconds=300&resolution=10&client_id=restart-qualification"
STREAM = "/api/stats-stream?range_seconds=300&resolution=10&client_id=restart-qualification"


def request(base: str, path: str, headers: dict[str, str] = AUTH) -> tuple[int, dict, float]:
    started = time.monotonic()
    try:
        response = urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        payload = json.loads(raw) if raw.startswith(b"{") else {}
        return response.status, payload, time.monotonic() - started


class FixtureServer:
    def __init__(self, binary: Path, root: Path, config: Path, env: dict[str, str], name: str):
        self.log_path = root / f"{name}.log"
        self.log = self.log_path.open("w", encoding="utf-8")
        self.frozen = False
        self.database = root / f"{name}-store" / "stats-v9.sqlite3"
        self.database.parent.mkdir(exist_ok=True)
        # Hand off a reserved listener, avoiding a free-port selection race.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            self.base = f"http://127.0.0.1:{port}"
            self.process = subprocess.Popen(
                [str(binary), "--http", "--host", "127.0.0.1", "--port", str(port),
                 "--listen-fd", str(listener.fileno()), "--root", str(root / name),
                 "--config", str(config), "--stats", str(self.database)],
                env=env, pass_fds=(listener.fileno(),), stdout=self.log, stderr=subprocess.STDOUT,
            )

    def stop(self, kill: bool = False) -> None:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGKILL if kill or self.frozen else signal.SIGTERM)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
                raise AssertionError("fixture server did not shut down")
        self.log.close()

    def healthy(self) -> float:
        started = time.monotonic()
        while time.monotonic() - started < 15:
            if self.process.poll() is not None:
                raise AssertionError(f"fixture server exited: {self.log_path.read_text()}")
            try:
                if request(self.base, "/healthz", {})[0] == 200:
                    return time.monotonic() - started
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.05)
        raise AssertionError("health readiness exceeded 15 seconds")


class StreamProbe:
    def __init__(self, base: str):
        self.events: list[dict] = []
        self.buckets: dict[tuple[int, int], dict] = {}
        self.tokens = 0
        self.generation = None
        self.ready = False
        self.last_id = -1
        self.error: Exception | None = None
        parsed = urlsplit(base)
        self.connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=15)
        self.connection.request("GET", STREAM, headers=AUTH)
        self.response = self.connection.getresponse()
        self.socket = self.connection.sock
        self.closed = False
        if self.response.status != 200 or self.response.getheader("Content-Type", "").split(";", 1)[0].strip() != "text/event-stream":
            self.response.close()
            self.connection.close()
            raise AssertionError("authenticated SSE did not connect")
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self) -> None:
        event, identity, data = "", "", []
        try:
            for raw in self.response:
                line = raw.decode().rstrip("\r\n")
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("id:"):
                    identity = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data.append(line.split(":", 1)[1].lstrip())
                elif not line and event:
                    payload = json.loads("\n".join(data))
                    frame_id = int(identity)
                    if frame_id <= self.last_id:
                        raise AssertionError("SSE frame identity did not advance")
                    self.last_id = frame_id
                    if event == "delta":
                        if payload["base_cache_generation"] != self.generation or payload["cache_generation"] <= self.generation:
                            raise AssertionError("SSE delta cannot apply to the delivered generation")
                    if event in {"ack", "snapshot", "delta", "ready"}:
                        self.generation = payload["cache_generation"]
                    if event in {"snapshot", "delta"}:
                        if event == "snapshot":
                            self.buckets.clear()
                        for bucket in payload["buckets"]:
                            self.buckets[(bucket["start"], bucket["duration"])] = bucket
                        for removed in payload.get("tombstones", []):
                            if removed["kind"] == "bucket":
                                self.buckets.pop((removed["start"], removed["duration"]), None)
                        self.tokens = round(sum(
                            series["value"] * bucket["duration"] / 60
                            for bucket in self.buckets.values()
                            for key, series in bucket["series"].items()
                            if key.startswith("model_tokens_per_minute:all:")
                        ))
                    if event == "ready":
                        self.ready = True
                    self.events.append({"at": time.monotonic(), "event": event, "id": frame_id,
                                        "generation": self.generation, "tokens": self.tokens})
                    event, identity, data = "", "", []
        except (OSError, ValueError, http.client.HTTPException, AssertionError, KeyError) as error:
            self.error = error

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.socket is not None:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.thread.join(timeout=16)
        self.response.close()
        self.connection.close()
        if self.thread.is_alive():
            raise AssertionError("SSE reader outlived fixture cleanup")


def record(number: int, observed: float, padding: int = 0, history: int | None = None) -> bytes:
    identity = f"live-{number}" if history is None else f"history-{history}-{number}"
    value = {"timestamp": observed, "type": "assistant", "message": {
        "id": identity, "model": "claude-sonnet-synthetic", "usage": {"input_tokens": 2, "output_tokens": 1}},
        "padding": "x" * padding}
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def facts(path: Path) -> dict:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3) as database:
        row = database.execute("""SELECT count(*), coalesce(sum(json_extract(payload_json,'$.quantity')),0),
            coalesce(sum(CASE WHEN event_id LIKE '%:live-%' THEN json_extract(payload_json,'$.quantity') ELSE 0 END),0),
            coalesce(sum(CASE WHEN event_id LIKE '%:history-%' THEN json_extract(payload_json,'$.quantity') ELSE 0 END),0)
            FROM usage_atoms""").fetchone()
        checkpoints = database.execute("SELECT source_id,revision,byte_offset,file_size FROM collector_checkpoints WHERE kind='transcript'").fetchall()
    return {"atoms": row[0], "tokens": row[1], "live_tokens": row[2], "history_tokens": row[3], "checkpoints": checkpoints}


def resource_sample(pid: int, phase: str) -> dict:
    sample = {"at": time.monotonic(), "phase": phase, "pid": pid}
    # This reads only the fixture process, never the host inventory.
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        fields = stat.read_text().rsplit(")", 1)[1].split()
        sample.update(cpu_ticks=int(fields[11]) + int(fields[12]), rss_bytes=int(fields[21]) * os.sysconf("SC_PAGE_SIZE"), ticks_per_second=os.sysconf("SC_CLK_TCK"))
    return sample


def qualify(binary: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="yg-stats-") as temporary:
        root = Path(temporary)
        directories = {key: root / name for key, name in {
            "HOME": "home", "TMPDIR": "tmp", "XDG_CONFIG_HOME": "xdg-config", "XDG_DATA_HOME": "xdg-data",
            "XDG_STATE_HOME": "xdg-state", "XDG_CACHE_HOME": "xdg-cache", "XDG_RUNTIME_DIR": "xdg-runtime",
            "CODEX_HOME": "codex-home", "CLAUDE_CONFIG_DIR": "claude-config"}.items()}
        for directory in directories.values():
            directory.mkdir(mode=0o700)
        projects, codex = root / "claude/projects", root / "codex/sessions"
        projects.mkdir(parents=True)
        codex.mkdir(parents=True)
        config, state = root / "config", root / "state"
        state.mkdir()
        write_auth(config)
        make_opencode_database(root / "opencode.db", int(time.time() * 1000))
        socket_path = root / "tmux.sock"
        env = synthetic_test_environment() | {key: str(value) for key, value in directories.items()} | {
            "YOMUX_CONFIG_DIR": str(config), "YOMUX_STATE_DIR": str(state), "YOMUX_TMUX_SOCKET": str(socket_path),
            "YOMUX_CLAUDE_TRANSCRIPT_ROOT": str(projects), "YOMUX_CODEX_TRANSCRIPT_ROOT": str(codex),
            "YOMUX_OPENCODE_DATABASE": str(root / "opencode.db"), "YOMUX_STATS_SYNTHETIC_HOST": "1",
            "YOMUX_STATS_SYNTHETIC_INVENTORY": "1", "YOMUX_PRICING_CATALOG": str(REPO / "assets/runtime/model_pricing.json"),
        }
        tmux = shutil.which("tmux")
        if tmux is None:
            raise AssertionError("tmux required for real-server stats qualification")
        servers: list[FixtureServer] = []
        streams: list[StreamProbe] = []
        stop_appender = threading.Event()
        appender: threading.Thread | None = None
        try:
            run(tmux, "-S", str(socket_path), "new-session", "-d", "-s", "fixture-claude", "-c", str(root), "sleep", "1000", env=env)
            # Exercise the original shared-config reproduction with separate source,
            # state, and stats roots, before any long qualification workload.
            first = FixtureServer(binary, root, config, env | {"YOMUX_STATS_DISABLE_COLLECTORS": "1"}, "owner")
            servers.append(first)
            first.healthy()
            journal = config / "go-events.jsonl"
            before = journal.read_bytes() if journal.exists() else b""
            alias = root / "config-alias"
            alias.symlink_to(config, target_is_directory=True)
            second_env = env | {"YOMUX_STATE_DIR": str(root / "other-state"), "YOMUX_STATS_DISABLE_COLLECTORS": "1"}
            second = FixtureServer(binary, root, alias, second_env, "rejected")
            servers.append(second)
            if second.process.wait(timeout=15) == 0 or "config/journal storage is already owned" not in second.log_path.read_text():
                raise AssertionError("duplicate config process was not rejected")
            after = journal.read_bytes() if journal.exists() else b""
            if before != after or second.database.exists():
                raise AssertionError("rejected owner changed journals or created stats")
            first.stop()
            # A fatal startup must release ownership through process exit.
            bad_env = env | {"YOMUX_OPENCODE_DATABASE": ""}
            failed = FixtureServer(binary, root, config, bad_env, "failed-start")
            servers.append(failed)
            if failed.process.wait(timeout=15) == 0:
                raise AssertionError("invalid startup fixture unexpectedly succeeded")
            for history in range(64):
                path = projects / f"history-{history:02}.jsonl"
                path.write_bytes(b"".join(record(number, time.time() - 3600, 262000, history) for number in range(4)))
                old = time.time() - 3600 - history
                os.utime(path, (old, old))
            live = projects / "live.jsonl"
            live.write_bytes(record(0, time.time()))
            history_bytes = sum(path.stat().st_size for path in projects.glob("history-*.jsonl"))
            server = FixtureServer(binary, root, config, env, "stats")
            servers.append(server)
            health = [server.healthy()]
            stream = StreamProbe(server.base)
            streams.append(stream)
            snapshots, resources, published = [], [], []
            began = time.monotonic()
            restart_seconds = None
            before_restart = None
            demand_started = began
            demand_readiness = []
            generation_ready = False
            appended = [threading.Event() for _ in range(181)]
            def append_live() -> None:
                for item in range(1, 181):
                    if stop_appender.wait(max(0, began + item - time.monotonic())):
                        return
                    with live.open("ab") as handle:
                        handle.write(record(item, time.time()))
                    published.append((time.monotonic(), item))
                    appended[item].set()

            appender = threading.Thread(target=append_live)
            appender.start()
            for number in range(1, 181):
                # Cadence is workload input, not a retry of a failed assertion.
                due = began + number
                delay = due - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                if not appended[number].wait(timeout=5):
                    raise AssertionError("live appender missed its scheduled record")
                if number == 90:
                    # Freeze only our fixture so the measured uncommitted tail
                    # cannot be consumed between checkpoint inspection and kill.
                    server.process.send_signal(signal.SIGSTOP)
                    server.frozen = True
                    stopped, status = os.waitpid(server.process.pid, os.WUNTRACED)
                    if stopped != server.process.pid or not os.WIFSTOPPED(status):
                        raise AssertionError("fixture did not stop before crash inspection")
                    before_restart = facts(server.database)
                    if not any(source.endswith("live.jsonl") and offset < live.stat().st_size for source, _, offset, _ in before_restart["checkpoints"]):
                        raise AssertionError("SIGKILL fixture did not contain an uncommitted live checkpoint tail")
                    if before_restart["history_tokens"] <= 0:
                        raise AssertionError("cold history made no progress before crash")
                    server.stop(kill=True)
                    stream.close()
                    restarted_at = time.monotonic()
                    server = FixtureServer(binary, root, config, env, "stats")
                    servers.append(server)
                    health.append(server.healthy())
                    stream = StreamProbe(server.base)
                    streams.append(stream)
                    restart_seconds = time.monotonic() - restarted_at
                    demand_started = restarted_at
                    generation_ready = False
                if not stream.thread.is_alive():
                    raise AssertionError(f"SSE terminated before fixture shutdown: {stream.error}")
                status, payload, latency = request(server.base, SNAPSHOT)
                snapshots.append({"at": time.monotonic(), "status": status, "latency": latency,
                                  "tokens": payload.get("cost_report", {}).get("total_tokens", 0)})
                stream_tokens = stream.tokens
                written_tokens = live.read_bytes().count(b"\n") * 3
                if snapshots[-1]["tokens"] > written_tokens or stream_tokens > written_tokens:
                    raise AssertionError("short-window publication included historical or duplicate tokens")
                if status not in (200, 202, 503):
                    raise AssertionError(f"snapshot failed: {status} {payload}")
                # A new generation may be pending briefly. No live record older
                # than three five-second collector cadences may remain unpublished.
                required = max((n for at, n in published if time.monotonic() - at >= 15), default=0)
                if status == 200 and not generation_ready:
                    demand_readiness.append(time.monotonic() - demand_started)
                    generation_ready = True
                if not generation_ready and time.monotonic() - demand_started > 15:
                    raise AssertionError("snapshot readiness exceeded three collector cadences")
                if generation_ready and required and snapshots[-1]["tokens"] < (required + 1) * 3:
                    raise AssertionError(f"live publication exceeded 15s: required {required}, snapshot {snapshots[-1]}")
                if stream.ready and required and stream.tokens < (required + 1) * 3:
                    raise AssertionError(f"live SSE publication exceeded 15s: required {required}, tokens {stream.tokens}")
                if not stream.ready and time.monotonic() - demand_started > 15:
                    raise AssertionError("SSE readiness exceeded three collector cadences")
                resources.append(resource_sample(server.process.pid, "cold" if number <= 30 else "restart" if 90 <= number < 120 else "settled"))
            appender.join(timeout=5)
            if appender.is_alive():
                raise AssertionError("live appender outlived its workload")
            deadline = began + 600
            expected_tokens = (256 + 181) * 3
            while time.monotonic() < deadline:
                final = facts(server.database)
                if final["tokens"] == expected_tokens and len(final["checkpoints"]) >= 65 and all(offset == size for _, _, offset, size in final["checkpoints"]):
                    break
                time.sleep(1)
            else:
                raise AssertionError(f"stats convergence exceeded ten minutes: {final}")
            if final["atoms"] != (256 + 181) * 2 or final["live_tokens"] != 181 * 3 or final["history_tokens"] != 256 * 3:
                raise AssertionError(f"replay lost or duplicated token facts: {final}")
            previous = {source: (revision, offset) for source, revision, offset, _ in before_restart["checkpoints"]}
            for source, revision, offset, _ in final["checkpoints"]:
                if source in previous and (revision < previous[source][0] or offset < previous[source][1]):
                    raise AssertionError("checkpoint regressed across restart")
            events = [event for probe in streams for event in probe.events]
            if any("ready" not in [frame["event"] for frame in probe.events] for probe in streams):
                raise AssertionError("SSE did not become ready on both process generations")
            if any("delta" not in [frame["event"] for frame in probe.events] for probe in streams):
                raise AssertionError("SSE did not publish live deltas on both process generations")
            demand_at = time.monotonic()
            while time.monotonic() - demand_at < 30:
                status, coarse, latency = request(server.base, "/api/stats-snapshot?range_seconds=86400&resolution=300&client_id=restart-coarse")
                if status == 200 and coarse.get("cost_report", {}).get("total_tokens") == expected_tokens:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError(f"demanded coarse product exceeded 30s: status={status}, tokens={coarse.get('cost_report', {}).get('total_tokens')}, generation={coarse.get('source_generation')}")
            return {"binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "history_files": 64,
                    "history_bytes": history_bytes, "live_appends": 180, "health_seconds": health,
                    "restart_seconds": restart_seconds, "demand_readiness_seconds": demand_readiness, "convergence_seconds": time.monotonic() - began,
                    "coarse_demand_seconds": time.monotonic() - demand_at, "facts": final,
                    "crash_facts": before_restart, "history_pending_at_crash": before_restart["history_tokens"] < 256 * 3,
                    "snapshot_samples": snapshots, "resource_samples": resources, "sse_events": events,
                    "qualification": "fixture workload; not a production-scale benchmark"}
        except BaseException:
            for fixture in servers:
                if not fixture.log.closed:
                    fixture.log.flush()
                print(f"fixture log {fixture.log_path.name}: {fixture.log_path.read_text()}", file=sys.stderr)
            raise
        finally:
            stop_appender.set()
            if appender is not None:
                appender.join(timeout=5)
            for probe in streams:
                probe.close()
            for fixture in reversed(servers):
                fixture.stop()
            run(tmux, "-S", str(socket_path), "kill-server", env=env, check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="fixture measurements as JSON")
    arguments = parser.parse_args()
    result = qualify(arguments.binary.resolve())
    if arguments.output:
        arguments.output.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"snapshot_samples", "resource_samples", "sse_events", "facts"}}, sort_keys=True))


if __name__ == "__main__":
    main()
