from concurrent.futures import ThreadPoolExecutor
import hashlib
from http import HTTPStatus
import io
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs
from urllib.parse import urlparse

import pytest
import yaml

from yolomux_lib import app as app_module
from yolomux_lib import common
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib.common import UploadedFile
from yolomux_lib.yoagent import session_summaries as session_summaries_module
from yolomux_lib.yoagent import controller as controller_module
from yolomux_lib.yoagent import transports as transport_module


PROMPT_STATE_KEYS = set(app_module.blank_prompt_state())
PROMOTED_CAPTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "prompt_corpus" / "captures"
pytestmark = pytest.mark.usefixtures("no_control_socket", "isolated_yoagent_conversation_state", "isolated_tmux_socket")


class StatsRoleOwner:
    def __init__(self, *, owner: bool, port: int):
        self.owner = owner
        self.port = port
        self.follower_stale_reads = []

    def can_run(self, role):
        return self.owner and role == app_module.BACKGROUND_ROLE_STATS_SAMPLER

    def owner_payload(self):
        return {"port": self.port}

    def record_follower_stale_read(self, role):
        self.follower_stale_reads.append(role)


def test_session_http_guards_use_shared_decorator():
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert "def requires_known_session(" in source
    assert source.count("unknown = self.require_known_session(session)") == 1
    assert "@requires_known_session(refresh=True)\n    def rename_session" in source
    assert "@requires_known_session()\n    def tmux_snapshot" in source
    assert source.count("@requires_known_session(") >= 10


def test_stats_sample_payload_reports_portable_process_cpu(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    wall_times = iter([1000.0, 1002.0])
    monotonic_times = iter([50.0, 52.0])
    process_times = iter([10.0, 10.5])
    system_cpu_times = iter([(100.0, 20.0), (104.0, 22.0)])
    monkeypatch.setattr(app_module, "SERVER_STARTED_AT", 990.0)
    monkeypatch.setattr(app_module.time, "time", lambda: next(wall_times))
    monkeypatch.setattr(app_module.time, "monotonic", lambda: next(monotonic_times))
    monkeypatch.setattr(app_module.time, "process_time", lambda: next(process_times))
    monkeypatch.setattr(app_module, "current_system_cpu_times", lambda: next(system_cpu_times))
    monkeypatch.setattr(app_module, "current_system_cpu_percent_from_ps", lambda: None)
    monkeypatch.setattr(app_module, "current_process_rss_bytes", lambda: 123456)
    try:
        first = webapp.stats_sample_payload()
        second = webapp.stats_sample_payload()
    finally:
        webapp.control_server.stop()

    assert first["ok"] is True
    assert first["cpu_percent"] == 0.0
    assert first["system_cpu_percent"] == 0.0
    assert first["uptime_seconds"] == 10.0
    assert second["time"] == 1002.0
    assert second["pid"] == os.getpid()
    assert second["cpu_percent"] == 25.0
    assert second["system_cpu_percent"] == 50.0
    assert second["uptime_seconds"] == 12.0
    assert second["rss_bytes"] == 123456
    assert second["history"]["sequence"] >= 2
    assert second["history"]["records"]
    assert any(record["system_cpu_count"] for record in second["history"]["records"])


def test_stats_sample_payload_reuses_short_window_sample(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    wall_times = iter([1000.0, 1000.2, 1002.0])
    monotonic_times = iter([50.0, 50.2, 52.0])
    process_times = iter([10.0, 10.5])
    system_cpu_times = iter([(100.0, 20.0), (104.0, 22.0)])
    monkeypatch.setattr(app_module, "SERVER_STARTED_AT", 990.0)
    monkeypatch.setattr(app_module.time, "time", lambda: next(wall_times))
    monkeypatch.setattr(app_module.time, "monotonic", lambda: next(monotonic_times))
    monkeypatch.setattr(app_module.time, "process_time", lambda: next(process_times))
    monkeypatch.setattr(app_module, "current_system_cpu_times", lambda: next(system_cpu_times))
    monkeypatch.setattr(app_module, "current_system_cpu_percent_from_ps", lambda: None)
    monkeypatch.setattr(app_module, "current_process_rss_bytes", lambda: 123456)
    try:
        first = webapp.stats_sample_payload()
        duplicate = webapp.stats_sample_payload()
        third = webapp.stats_sample_payload()
    finally:
        webapp.control_server.stop()

    assert duplicate["time"] == first["time"]
    assert duplicate["cpu_percent"] == first["cpu_percent"]
    assert duplicate["system_cpu_percent"] == first["system_cpu_percent"]
    assert duplicate["history"]["sequence"] == first["history"]["sequence"]
    assert third["time"] == 1002.0
    assert third["cpu_percent"] == 25.0
    assert third["system_cpu_percent"] == 50.0
    assert third["history"]["sequence"] > duplicate["history"]["sequence"]


def test_stats_history_payload_limits_records_to_requested_visible_range():
    webapp = app_module.TmuxWebtermApp([])
    try:
        with webapp.stats_history_lock:
            for sample_time in (900.0, 960.0, 1000.0):
                webapp.stats_history_merge_record_locked({
                    "time": sample_time,
                    "cpu_total_percent": 1.0,
                    "cpu_count": 1.0,
                }, now=1000.0, fields=app_module.STATS_HISTORY_SERVER_FIELDS, client_id=None)
            history = webapp.stats_history_payload_locked(history_start=950)
    finally:
        webapp.control_server.stop()

    assert [record["start"] for record in history["records"]] == [960, 1000]


def test_system_cpu_percent_from_times_clamps_to_single_100_percent_scale():
    assert app_module.system_cpu_percent_from_times((100.0, 20.0), (104.0, 22.0)) == 50.0
    assert app_module.system_cpu_percent_from_times((100.0, 20.0), (104.0, 200.0)) == 100.0
    assert app_module.system_cpu_percent_from_times((100.0, 20.0), (100.0, 21.0)) == 0.0


def test_stats_history_remembers_browser_deltas_and_rolls_old_buckets(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    now = 200000.0
    monkeypatch.setattr(app_module.time, "time", lambda: now)
    records = [{
        "start": now - (2 * 60 * 60) + i,
        "duration": 1,
        "api_count": 1,
        "sse_count": 1 if i % 2 == 0 else 0,
        "latency_total_ms": 10 + i,
        "latency_count": 1,
        "bandwidth_bytes": 100 + i,
        "system_cpu_total_percent": 5,
        "system_cpu_count": 1,
    } for i in range(20)]
    try:
        payload, status = webapp.record_stats_history_payload({"records": records})
        incremental, _status = webapp.record_stats_history_payload({"since": payload["history"]["sequence"], "records": []})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["history"]["raw_window_seconds"] == app_module.STATS_HISTORY_RAW_WINDOW_SECONDS
    assert payload["history"]["rollup_bucket_seconds"] == app_module.STATS_HISTORY_ROLLUP_BUCKET_SECONDS
    assert len(payload["history"]["records"]) <= 3
    assert sum(record["api_count"] for record in payload["history"]["records"]) == 20
    assert sum(record["system_cpu_count"] for record in payload["history"]["records"]) == 0
    assert sum(record["agent_activity_samples"] for record in payload["history"]["records"]) == 0
    assert any(record["duration"] == app_module.STATS_HISTORY_ROLLUP_BUCKET_SECONDS for record in payload["history"]["records"])
    assert incremental["history"]["records"] == []


def test_stats_history_wide_token_history_is_server_aggregated(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    now = 200000.0
    monkeypatch.setattr(app_module.time, "time", lambda: now)
    try:
        with webapp.stats_history_lock:
            for offset in range(0, 60 * 60, 60):
                webapp.stats_history_merge_record_locked({
                    "start": now - (2 * 60 * 60) + offset,
                    "tokens_per_agent_total": 10,
                    "agent_token_samples": 1,
                    "agent_token_rates": [{"key": "1|0|codex", "label": "1:0:codex", "total": 10, "samples": 1, "tokens": 100, "seconds": 60, "source": "transcript"}],
                }, now, fields=app_module.STATS_HISTORY_SERVER_FIELDS, client_id=None)
            history = webapp.stats_history_payload_locked(0, token_since=0, token_resolution_seconds=300)
    finally:
        webapp.control_server.stop()

    token_history = history["agent_token_history"]
    assert token_history["snapshot"] is True
    assert token_history["resolution_seconds"] == 300
    assert len(token_history["records"]) < 20
    assert sum(item["tokens"] for record in token_history["records"] for item in record["agent_token_rates"]) == 6000
    assert all(record["duration"] == 300 for record in token_history["records"])


def test_stats_history_keeps_browser_deltas_per_client_and_global_samples_shared(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    now = 200000.0
    monkeypatch.setattr(app_module.time, "time", lambda: now)
    try:
        with webapp.stats_history_lock:
            webapp.stats_history_merge_record_locked(
                {
                    "start": now,
                    "cpu_total_percent": 25,
                    "cpu_count": 1,
                    "system_cpu_total_percent": 40,
                    "system_cpu_count": 1,
                    "ask_agent_total": 1,
                    "run_agent_total": 1,
                    "idle_agent_total": 2,
                    "active_agent_total": 2,
                    "inactive_agent_total": 2,
                    "agent_activity_samples": 1,
                },
                now,
                fields=app_module.STATS_HISTORY_SERVER_FIELDS,
                client_id=None,
            )
        client_a, status_a = webapp.record_stats_history_payload({
            "client_id": "client-a",
            "records": [{"start": now, "api_count": 2, "latency_total_ms": 30, "latency_count": 2, "bandwidth_bytes": 1000}],
        })
        client_b, status_b = webapp.record_stats_history_payload({
            "client_id": "client-b",
            "records": [{"start": now, "api_count": 7, "sse_count": 3, "latency_total_ms": 90, "latency_count": 3, "bandwidth_bytes": 3000}],
        })
        with webapp.stats_history_lock:
            history_a = webapp.stats_history_payload_locked(0, client_id="client-a")
            history_b = webapp.stats_history_payload_locked(0, client_id="client-b")
            history_a_after_b = webapp.stats_history_payload_locked(client_a["history"]["sequence"], client_id="client-a")
    finally:
        webapp.control_server.stop()

    assert status_a == HTTPStatus.OK
    assert status_b == HTTPStatus.OK
    assert sum(record["api_count"] for record in history_a["records"]) == 2
    assert sum(record["sse_count"] for record in history_a["records"]) == 0
    assert sum(record["latency_count"] for record in history_a["records"]) == 2
    assert sum(record["bandwidth_bytes"] for record in history_a["records"]) == 1000
    assert sum(record["api_count"] for record in history_b["records"]) == 7
    assert sum(record["sse_count"] for record in history_b["records"]) == 3
    assert sum(record["latency_count"] for record in history_b["records"]) == 3
    assert sum(record["bandwidth_bytes"] for record in history_b["records"]) == 3000
    assert sum(record["cpu_count"] for record in history_a["records"]) == 1
    assert sum(record["cpu_count"] for record in history_b["records"]) == 1
    assert sum(record["agent_activity_samples"] for record in history_a["records"]) == 1
    assert sum(record["agent_activity_samples"] for record in history_b["records"]) == 1
    clients = history_a["records"][0]["clients"]
    assert clients["client-a"]["api_count"] == 2
    assert clients["client-b"]["api_count"] == 7
    assert clients["client-b"]["bandwidth_bytes"] == 3000
    assert history_a_after_b["sequence"] == client_b["history"]["sequence"]
    assert len(history_a_after_b["records"]) == 1
    assert history_a_after_b["records"][0]["api_count"] == 2
    assert history_a_after_b["records"][0]["clients"]["client-b"]["api_count"] == 7


def test_record_stats_global_sample_fills_history_without_browser_poll(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    clock = {"wall": 1000.0, "monotonic": 50.0, "process": 10.0}
    system_cpu_times = iter([(100.0, 20.0), (104.0, 22.0)])
    monkeypatch.setattr(app_module, "SERVER_STARTED_AT", 990.0)
    monkeypatch.setattr(app_module.time, "time", lambda: clock["wall"])
    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock["monotonic"])
    monkeypatch.setattr(app_module.time, "process_time", lambda: clock["process"])
    monkeypatch.setattr(app_module, "current_system_cpu_times", lambda: next(system_cpu_times))
    monkeypatch.setattr(app_module, "current_system_cpu_percent_from_ps", lambda: None)
    monkeypatch.setattr(app_module, "current_process_rss_bytes", lambda: 123456)
    try:
        first = webapp.record_stats_global_sample()
        clock.update({"wall": 1002.0, "monotonic": 52.0, "process": 10.5})
        second = webapp.record_stats_global_sample()
        with webapp.stats_history_lock:
            history = webapp.stats_history_payload_locked(0, client_id="client-a")
    finally:
        webapp.control_server.stop()

    assert first["cpu_percent"] == 0.0
    assert second["cpu_percent"] == 25.0
    assert sum(record["cpu_count"] for record in history["records"]) == 2
    assert sum(record["system_cpu_count"] for record in history["records"]) == 2
    assert sum(record["api_count"] for record in history["records"]) == 0
    with webapp.performance_record_lock:
        assert any(record["role"] == app_module.BACKGROUND_ROLE_STATS_SAMPLER for record in webapp.performance_records)


def test_shared_stats_owner_writes_and_follower_reads_recent_global_history(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "TMUX_AI_STATUS_PATH", tmp_path / "tmux-AI-status.json")
    monkeypatch.setattr(common, "STATS_CLIENT_HISTORY_PATH", tmp_path / "stats-client-history.json")
    monkeypatch.setattr(common, "LEGACY_ATTENTION_ACKS_PATH", tmp_path / "attention-acks.json")
    owner = app_module.TmuxWebtermApp(["1"])
    follower = app_module.TmuxWebtermApp(["1"])
    owner.background_owner = StatsRoleOwner(owner=True, port=8003)
    follower.background_owner = StatsRoleOwner(owner=False, port=8002)
    sample_time = time.time()
    sample = {
        "time": sample_time,
        "pid": 8003,
        "started_at": sample_time - 20.0,
        "uptime_seconds": 20.0,
        "cpu_percent": 12.5,
        "system_cpu_percent": 33.0,
        "rss_bytes": 456789,
    }
    monkeypatch.setattr(owner, "current_stats_sample", lambda: (dict(sample), True))
    monkeypatch.setattr(owner, "stats_agent_activity_record", lambda _sample_time, include_token_rates=True: {
        "ask_agent_total": 1,
        "run_agent_total": 2,
        "transition_agent_total": 3,
        "idle_agent_total": 4,
        "active_agent_total": 6,
        "inactive_agent_total": 4,
        "agent_activity_samples": 1,
    })
    monkeypatch.setattr(follower, "current_stats_sample", lambda: (_ for _ in ()).throw(AssertionError("fresh shared stats should avoid follower sampling")))
    try:
        owner.record_stats_global_sample(trigger="sampler")
        payload = follower.stats_sample_payload(client_id="client-a")
    finally:
        owner.control_server.stop()
        follower.control_server.stop()

    assert payload["pid"] == 8003
    assert payload["system_cpu_percent"] == 33.0
    assert payload["shared_stats"]["role"] == "follower"
    assert payload["shared_stats"]["fresh"] is True
    records = payload["history"]["records"]
    assert sum(record["cpu_count"] for record in records) == 1
    assert sum(record["system_cpu_count"] for record in records) == 1
    assert sum(record["ask_agent_total"] for record in records) == 1
    assert sum(record["run_agent_total"] for record in records) == 2
    assert sum(record["transition_agent_total"] for record in records) == 3
    assert sum(record["idle_agent_total"] for record in records) == 4
    data = json.loads((tmp_path / "tmux-AI-status.json").read_text(encoding="utf-8"))
    assert data["stats_history"]["writer"]["port"] == 8003
    assert data["stats_history"]["raw_buckets"]
    assert isinstance(data["stats_history"]["raw_buckets"][0], list)


def test_shared_stats_client_history_survives_other_server_writes_and_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "TMUX_AI_STATUS_PATH", tmp_path / "tmux-AI-status.json")
    monkeypatch.setattr(common, "STATS_CLIENT_HISTORY_PATH", tmp_path / "stats-client-history.json")
    monkeypatch.setattr(common, "LEGACY_ATTENTION_ACKS_PATH", tmp_path / "attention-acks.json")
    owner = app_module.TmuxWebtermApp([])
    follower = app_module.TmuxWebtermApp([])
    restarted = app_module.TmuxWebtermApp([])
    owner.background_owner = StatsRoleOwner(owner=True, port=8003)
    follower.background_owner = StatsRoleOwner(owner=False, port=8002)
    restarted.background_owner = StatsRoleOwner(owner=False, port=8001)
    sample_time = time.time()
    sample = {"time": sample_time, "pid": 8003, "cpu_percent": 12.0, "system_cpu_percent": 24.0}
    monkeypatch.setattr(owner, "current_stats_sample", lambda: (dict(sample), True))
    monkeypatch.setattr(owner, "stats_agent_activity_record", lambda _sample_time, include_token_rates=True: None)
    try:
        owner.record_stats_global_sample(trigger="sampler")
        follower.record_stats_history_payload({
            "client_id": "client-b",
            "records": [{"start": sample_time, "api_count": 7, "latency_total_ms": 90, "latency_count": 3, "bandwidth_bytes": 3000}],
        })
        owner.record_stats_global_sample(trigger="sampler")
        payload = restarted.stats_sample_payload(client_id="client-a")
    finally:
        owner.control_server.stop()
        follower.control_server.stop()
        restarted.control_server.stop()

    records = payload["history"]["records"]
    clients = next(record["clients"] for record in records if "client-b" in record["clients"])
    assert clients["client-b"]["api_count"] == 7
    assert clients["client-b"]["latency_total_ms"] == 90
    assert clients["client-b"]["bandwidth_bytes"] == 3000
    owner_data = json.loads((tmp_path / "tmux-AI-status.json").read_text(encoding="utf-8"))
    assert owner_data["stats_history"]["writer"]["port"] == 8003
    client_data = json.loads((tmp_path / "stats-client-history.json").read_text(encoding="utf-8"))
    persisted_bucket = client_data["raw_buckets"][0]
    assert len(persisted_bucket) == len(app_module.STATS_CLIENT_HISTORY_BUCKET_FIELDS)
    assert app_module.stats_history_client_id("client-b") in persisted_bucket[-2]
    process_key = f"port:{owner.background_owner.port}"
    assert persisted_bucket[-1][process_key]["cpu_total_percent"] == 24.0


def test_shared_stats_history_keeps_cpu_for_every_yolomux_server(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "TMUX_AI_STATUS_PATH", tmp_path / "tmux-AI-status.json")
    monkeypatch.setattr(common, "STATS_CLIENT_HISTORY_PATH", tmp_path / "stats-client-history.json")
    monkeypatch.setattr(common, "LEGACY_ATTENTION_ACKS_PATH", tmp_path / "attention-acks.json")
    servers = [app_module.TmuxWebtermApp([]) for _port in range(3)]
    reader = app_module.TmuxWebtermApp([])
    ports = (9101, 9102, 9103)
    sample_time = time.time()
    for server, port in zip(servers, ports, strict=True):
        server.background_owner = StatsRoleOwner(owner=port == 8003, port=port)
    reader.background_owner = StatsRoleOwner(owner=False, port=9199)
    try:
        for server, port in zip(servers, ports, strict=True):
            server.record_stats_process_sample({
                "time": sample_time,
                "pid": port,
                "started_at": sample_time - port,
                "cpu_percent": float(port - 9090),
            })
        reader.merge_shared_stats_client_history()
        with reader.stats_history_lock:
            history = reader.stats_history_payload_locked()
    finally:
        for server in [*servers, reader]:
            server.control_server.stop()

    processes = next(record["servers"] for record in history["records"] if len(record["servers"]) == 3)
    assert set(processes) == {"port:9101", "port:9102", "port:9103"}
    assert processes["port:9101"]["label"] == "yolomux.py :9101"
    assert processes["port:9102"]["cpu_total_percent"] == 12.0
    assert processes["port:9103"]["cpu_count"] == 1.0


def test_shared_stats_discards_agent_token_history_from_older_schema(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "TMUX_AI_STATUS_PATH", tmp_path / "tmux-AI-status.json")
    monkeypatch.setattr(common, "STATS_CLIENT_HISTORY_PATH", tmp_path / "stats-client-history.json")
    monkeypatch.setattr(common, "LEGACY_ATTENTION_ACKS_PATH", tmp_path / "attention-acks.json")
    writer = app_module.TmuxWebtermApp([])
    reader = app_module.TmuxWebtermApp([])
    reader.background_owner = StatsRoleOwner(owner=False, port=8002)
    sample_time = time.time()
    sample = {"time": sample_time, "pid": 8003, "cpu_percent": 12.0, "system_cpu_percent": 24.0}
    try:
        with writer.stats_history_lock:
            writer.stats_history_merge_record_locked({
                "time": sample_time,
                "cpu_total_percent": 12.0,
                "cpu_count": 1,
                "tokens_per_agent_total": 600.0,
                "agent_token_samples": 1,
                "agent_token_rates": [{"key": "1|0|codex", "label": "1:0:codex", "total": 600.0, "samples": 1, "tokens": 600.0, "seconds": 60.0, "source": "transcript"}],
            }, sample_time, fields=app_module.STATS_HISTORY_SERVER_FIELDS, client_id=None)
        with writer.stats_agent_token_lock:
            writer.stats_agent_token_state = {"1|0|codex": {"tokens": 600.0, "time": sample_time, "label": "1:0:codex", "source": "transcript", "identity": "old-transcript"}}
        old_snapshot = writer.stats_history_shared_snapshot(sample)
        old_snapshot.pop("agent_token_schema_version")
        old_snapshot["rev"] = 1
        status = writer.tmux_ai_status_empty()
        status["stats_history"] = old_snapshot
        common.TMUX_AI_STATUS_PATH.write_text(json.dumps(status), encoding="utf-8")

        assert reader.merge_shared_stats_history(max_age_seconds=None)["ok"] is True
        with reader.stats_history_lock:
            history = reader.stats_history_payload_locked()
        with reader.stats_agent_token_lock:
            token_state = dict(reader.stats_agent_token_state)
    finally:
        writer.control_server.stop()
        reader.control_server.stop()

    assert history["agent_token_schema_version"] == app_module.STATS_AGENT_TOKEN_SCHEMA_VERSION
    assert sum(record["cpu_count"] for record in history["records"]) == 1
    assert sum(record["agent_token_samples"] for record in history["records"]) == 0
    assert all(not record["agent_token_rates"] for record in history["records"])
    assert token_state == {}


def test_shared_stats_token_consumer_interest_reaches_owner_sampler(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "TMUX_AI_STATUS_PATH", tmp_path / "tmux-AI-status.json")
    monkeypatch.setattr(common, "STATS_CLIENT_HISTORY_PATH", tmp_path / "stats-client-history.json")
    monkeypatch.setattr(common, "LEGACY_ATTENTION_ACKS_PATH", tmp_path / "attention-acks.json")
    owner = app_module.TmuxWebtermApp(["1"])
    follower = app_module.TmuxWebtermApp(["1"])
    owner.background_owner = StatsRoleOwner(owner=True, port=8003)
    follower.background_owner = StatsRoleOwner(owner=False, port=8002)
    sample_time = time.time() + 1.0
    previous_time = sample_time - 60.0
    monkeypatch.setattr(follower, "current_stats_sample", lambda: ({
        "time": sample_time - 1.0,
        "pid": 8002,
        "started_at": sample_time - 10.0,
        "uptime_seconds": 9.0,
        "cpu_percent": 1.0,
        "system_cpu_percent": 2.0,
        "rss_bytes": 123,
    }, True))
    monkeypatch.setattr(owner, "current_stats_sample", lambda: ({
        "time": sample_time,
        "pid": 8003,
        "started_at": sample_time - 20.0,
        "uptime_seconds": 20.0,
        "cpu_percent": 3.0,
        "system_cpu_percent": 4.0,
        "rss_bytes": 456,
    }, True))
    monkeypatch.setattr(owner, "stats_agent_window_rows", lambda: [{
        "session": "1",
        "kind": "codex",
        "state": "working",
        "window_index": 0,
        "window_label": "0:codex",
        "transcript": "/tmp/codex.jsonl",
    }])
    def token_count(row):
        row["_stats_agent_token_source"] = "transcript"
        row["_stats_agent_token_identity"] = "codex:dev:inode:digest:/tmp/codex.jsonl"
        return 250.0

    monkeypatch.setattr(owner, "stats_agent_token_count", token_count)
    try:
        follower.stats_sample_payload(token_consumer=True)
        owner.merge_shared_stats_history(max_age_seconds=None)
        with owner.stats_agent_token_lock:
            assert owner.stats_agent_token_consumer_until >= sample_time
            owner.stats_agent_token_state = {
                "1|0|codex": {"tokens": 100.0, "time": previous_time, "label": "1:0:codex", "source": "transcript", "identity": "codex:dev:inode:digest:/tmp/codex.jsonl"}
            }
            owner.stats_agent_token_next_sample_at = 0.0
        owner.record_stats_global_sample(trigger="sampler", token_consumer=False)
        with owner.stats_history_lock:
            history = owner.stats_history_payload_locked(0, client_id="client-a")
    finally:
        owner.control_server.stop()
        follower.control_server.stop()

    token_records = [item for record in history["records"] for item in record["agent_token_rates"]]
    assert sum(item["tokens"] for item in token_records) == pytest.approx(150.0)
    assert sum(record["agent_token_samples"] for record in history["records"]) >= 1
    data = json.loads((tmp_path / "tmux-AI-status.json").read_text(encoding="utf-8"))
    assert data["stats_history"]["agent_token_schema_version"] == app_module.STATS_AGENT_TOKEN_SCHEMA_VERSION
    assert data["stats_history"]["agent_token_state"]["1|0|codex"]["identity"] == "codex:dev:inode:digest:/tmp/codex.jsonl"


def test_background_status_includes_performance_summary():
    webapp = app_module.TmuxWebtermApp([])
    try:
        webapp.record_performance_sample(
            app_module.BACKGROUND_ROLE_SESSION_FILES,
            "payload",
            trigger="request",
            compute_ms=12.5,
            payload={"files": [{"path": "/repo/a.py"}]},
            cache_key=("payload", "session"),
            cache_status="hit:fresh",
            cache_hit=True,
            cache_fresh=True,
            owner_role="owner",
        )
        payload, status = webapp.background_owner_status_payload()
        control_response = webapp.handle_control_request({"action": "background_status"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    perf = payload["perf"]
    assert perf["record_count"] == 1
    assert perf["recent"][0]["cache_key_kind"] == "payload"
    assert perf["recent"][0]["cache_hit"] is True
    assert perf["recent"][0]["cache_fresh"] is True
    assert perf["summary"] == [{
        "role": app_module.BACKGROUND_ROLE_SESSION_FILES,
        "surface": "payload",
        "count": 1,
        "compute_ms_avg": 12.5,
        "compute_ms_max": 12.5,
        "payload_bytes_total": len(json.dumps({"files": [{"path": "/repo/a.py"}]}, sort_keys=True, separators=(",", ":")).encode("utf-8")),
        "cache": {"hit:fresh": 1},
    }]
    assert control_response["ok"] is True
    assert control_response["status"]["perf"]["record_count"] == 1


def test_background_owner_claim_payload_reports_claim_noop_and_conflict():
    class ClaimOwner:
        def __init__(self, *, owner=False, takeover=True, error="") -> None:
            self.owner = owner
            self.takeover = takeover
            self.error = error
            self.calls = 0

        def is_owner(self):
            return self.owner

        def attempt_takeover(self):
            self.calls += 1
            if self.takeover:
                self.owner = True
            return self.takeover

        def status_payload(self):
            return {
                "owner": self.owner,
                "last_error": self.error,
                "roles": {
                    "search-index": {"owner": self.owner, "status": "owner" if self.owner else "follower"},
                    "stats-sampler": {"owner": self.owner, "status": "owner" if self.owner else "follower"},
                    "session-files": {"owner": self.owner, "status": "owner" if self.owner else "follower"},
                },
            }

    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.performance_metrics_payload = lambda: {"record_count": 0}

    webapp.background_owner = ClaimOwner(owner=False, takeover=True)
    payload, status = webapp.background_owner_claim_payload()
    assert status == HTTPStatus.OK
    assert payload["ok"] is True
    assert payload["claimed"] is True
    assert payload["was_owner"] is False
    assert payload["status"]["owner"] is True

    webapp.background_owner = ClaimOwner(owner=True, takeover=True)
    payload, status = webapp.background_owner_claim_payload()
    assert status == HTTPStatus.OK
    assert payload["ok"] is True
    assert payload["claimed"] is False
    assert payload["was_owner"] is True

    webapp.background_owner = ClaimOwner(owner=False, takeover=False, error="owner lock is held")
    payload, status = webapp.background_owner_claim_payload()
    assert status == HTTPStatus.CONFLICT
    assert payload["ok"] is False
    assert payload["claimed"] is False
    assert payload["was_owner"] is False
    assert payload["error"] == "owner lock is held"
    assert payload["user_message"]["key"] == "common.requestFailed"
    assert payload["diagnostic"] == "owner lock is held"


def test_sampled_background_event_forwards_one_shared_descriptor_parent():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.background_refresh_event_log_lock = threading.Lock()
    webapp.background_refresh_event_log_counts = {}
    webapp.background_refresh_event_log_last_emit_counts = {}
    calls = []
    webapp.log_event = lambda *args, **kwargs: calls.append((args, kwargs)) or {"time": "event-1"}
    target = {
        "key": "backgroundOwner.sessionFiles",
        "params": {},
        "fallback": "Session files",
    }

    saved = webapp.log_sampled_background_refresh_event(
        "background_refresh_started",
        app_module.BACKGROUND_ROLE_SESSION_FILES,
        "Session-files background refresh started",
        {"role": app_module.BACKGROUND_ROLE_SESSION_FILES},
        message_key="events.message.backgroundRefresh.started",
        message_params={"target": target},
    )

    assert saved == {"time": "event-1"}
    assert calls == [(
        (
            None,
            "background_refresh_started",
            "Session-files background refresh started",
            {"role": app_module.BACKGROUND_ROLE_SESSION_FILES, "sample_count": 1},
        ),
        {
            "message_key": "events.message.backgroundRefresh.started",
            "message_params": {"target": target},
        },
    )]


def test_performance_metrics_payload_ranks_response_bytes():
    webapp = app_module.TmuxWebtermApp([])
    try:
        webapp.record_performance_sample("http-endpoint", "GET /api/small", payload_bytes=50, cache_status="200", owner_role="server")
        webapp.record_performance_sample("http-endpoint", "GET /api/large", payload_bytes=400, cache_status="200", owner_role="server")
        webapp.record_performance_sample("http-endpoint", "GET /api/large", payload_bytes=200, cache_status="200", owner_role="server")
        payload = webapp.performance_metrics_payload()
    finally:
        webapp.control_server.stop()

    assert [row["surface"] for row in payload["top_payload_bytes"][:2]] == ["GET /api/large", "GET /api/small"]
    assert payload["top_payload_bytes"][0]["payload_bytes_total"] == 600
    assert payload["top_payload_bytes"][0]["count"] == 2


def test_runtime_report_payload_reports_owner_cache_endpoints_events_and_transcripts(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    monkeypatch.setattr(app_module, "TABBER_ACTIVITY_CACHE_DIR", tmp_path / "activity-cache")
    monkeypatch.setattr(app_module.file_index, "INDEX_DIR", tmp_path / "search-index")
    for dirname in ("session-files-cache", "activity-cache", "search-index"):
        (tmp_path / dirname).mkdir()
    (tmp_path / "session-files-cache" / "a.json").write_text("abc", encoding="utf-8")
    (tmp_path / "activity-cache" / "b.json").write_text("12345", encoding="utf-8")
    (tmp_path / "search-index" / "c.sqlite3").write_text("index", encoding="utf-8")
    small = tmp_path / "small.jsonl"
    large = tmp_path / "large.jsonl"
    small.write_text("small", encoding="utf-8")
    large.write_text("large transcript payload", encoding="utf-8")
    webapp = app_module.TmuxWebtermApp([])
    webapp.event_log = app_module.EventLog(tmp_path / "events.jsonl")
    webapp.event_log.append(None, "background_refresh_done", "done", {})
    webapp.event_log.append(None, "background_refresh_done", "done again", {})
    webapp.event_log.append("8002", "stats_history_error", "stats failed", {})
    webapp.transcripts_payload = lambda force=False: {
        "sessions": {
            "8002": {"agents": [{"kind": "codex", "pid": 123, "transcript": str(large)}]},
            "8003": {"agents": [{"kind": "claude", "pid": 456, "transcript": str(small)}]},
        },
        "cache": {"hit": False},
    }
    background_status = {
        "owner": True,
        "status": "owner",
        "current_owner": {"port": 8002},
        "search_index": {"mode": "indexing-server"},
        "roles": {"session-files": {"status": "owner"}},
        "counters": {"coalesced_refresh_requests": 3},
        "refresh_queue": {"recent_pending_count": 2, "recent_pending_by_role": {"session-files": 2}},
        "perf": {
            "summary": [
                {"role": "stats-sampler", "surface": "global-sample", "count": 1, "compute_ms_max": 17.0, "payload_bytes_total": 100},
                {"role": "session-files", "surface": "cache-entry", "count": 4, "compute_ms_max": 44.0, "payload_bytes_total": 1000},
                {"role": "http-endpoint", "surface": "GET /api/session-files", "count": 2, "compute_ms_max": 7.0, "payload_bytes_total": 900},
            ],
            "top_payload_bytes": [
                {"role": "http-endpoint", "surface": "GET /api/session-files", "count": 2, "payload_bytes_total": 900},
                {"role": "session-files", "surface": "cache-read", "count": 4, "payload_bytes_total": 1000},
            ],
        },
    }

    try:
        payload = webapp.runtime_report_payload(background_status=background_status, owner_debug={"generations": []}, owner_control_response={"ok": True})
    finally:
        webapp.control_server.stop()

    assert payload["owner"]["current_owner"] == {"port": 8002}
    assert payload["refresh"]["coalescing"]["recent_pending_count"] == 2
    assert payload["caches"]["session_files"]["files"] == 1
    assert payload["caches"]["session_files"]["bytes"] == 3
    assert payload["caches"]["activity"]["bytes"] == 5
    assert payload["top_endpoints"][0]["surface"] == "GET /api/session-files"
    assert payload["top_background_work"][0]["role"] == "session-files"
    assert payload["top_background_work"][0]["surface"] == "cache-entry"
    assert payload["top_event_types"][0] == {"type": "background_refresh_done", "count": 2}
    assert payload["largest_active_transcripts"][0]["path"] == str(large)
    assert payload["largest_active_transcripts"][0]["bytes"] == len("large transcript payload")


def test_background_refresh_control_uses_nested_payload(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    calls = []
    monkeypatch.setattr(webapp, "request_background_refresh", lambda role, payload: calls.append((role, payload)) or {"ok": True, "accepted": True})
    try:
        response = webapp.handle_control_request({
            "action": "background_refresh",
            "role": app_module.BACKGROUND_ROLE_SESSION_FILES,
            "payload": {"cache_key": "same", "reason": "follower"},
            "requester": {"pid": 123},
        })
    finally:
        webapp.control_server.stop()

    assert response == {"ok": True, "accepted": True, "role": app_module.BACKGROUND_ROLE_SESSION_FILES}
    assert calls == [(app_module.BACKGROUND_ROLE_SESSION_FILES, {"cache_key": "same", "reason": "follower"})]


def test_stats_sampler_records_shared_agent_activity_and_token_rates(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    wall_times = iter([1000.0, 1060.0])
    monotonic_times = iter([50.0, 110.0])
    process_times = iter([10.0, 10.5])
    system_cpu_times = iter([(100.0, 20.0), (104.0, 22.0)])
    claude_transcript = tmp_path / "claude.jsonl"
    codex_transcript = tmp_path / "codex.jsonl"
    previous_claude_tokens = [0]

    def write_usage_transcripts(claude_tokens: int, codex_tokens: int, mtime: float) -> None:
        claude_delta = claude_tokens - previous_claude_tokens[0]
        previous_claude_tokens[0] = claude_tokens
        with claude_transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "assistant", "message": {"id": f"msg-{claude_tokens}", "usage": {"output_tokens": claude_delta}, "content": []}}) + "\n")
        with codex_transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "response_item", "payload": {"info": {"total_token_usage": {"output_tokens": codex_tokens}}}}) + "\n")
        os.utime(claude_transcript, (mtime, mtime))
        os.utime(codex_transcript, (mtime, mtime))

    usage_samples = [(100, 200), (1600, 2600)]

    def fake_agent_rows():
        claude_tokens, codex_tokens = usage_samples.pop(0)
        write_usage_transcripts(claude_tokens, codex_tokens, 1000.0 + len(usage_samples))
        return [
            {"session": "1", "kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude", "transcript": str(claude_transcript)},
            {"session": "1", "kind": "codex", "state": "idle", "window_index": 1, "window_label": "1:codex"},
            {"session": "2", "kind": "codex", "state": "needs-input", "window_index": 0, "window_label": "0:codex", "transcript": str(codex_transcript)},
        ]

    monkeypatch.setattr(app_module, "SERVER_STARTED_AT", 990.0)
    monkeypatch.setattr(app_module.time, "time", lambda: next(wall_times))
    monkeypatch.setattr(app_module.time, "monotonic", lambda: next(monotonic_times))
    monkeypatch.setattr(app_module.time, "process_time", lambda: next(process_times))
    monkeypatch.setattr(app_module, "current_system_cpu_times", lambda: next(system_cpu_times))
    monkeypatch.setattr(app_module, "current_system_cpu_percent_from_ps", lambda: None)
    monkeypatch.setattr(app_module, "current_process_rss_bytes", lambda: 123456)
    monkeypatch.setattr(webapp, "stats_agent_window_rows", fake_agent_rows)
    try:
        first = webapp.record_stats_global_sample(trigger="sampler", token_consumer=True)
        second = webapp.record_stats_global_sample(trigger="sampler", token_consumer=True)
        with webapp.stats_history_lock:
            history = webapp.stats_history_payload_locked(0, client_id="client-a")
    finally:
        webapp.control_server.stop()

    assert first["time"] == 1000.0
    assert second["time"] == 1060.0
    records = history["records"]
    assert sum(record["active_agent_total"] for record in records) == 4
    assert sum(record["inactive_agent_total"] for record in records) == 2
    assert sum(record["ask_agent_total"] for record in records) == 2
    assert sum(record["run_agent_total"] for record in records) == 2
    assert sum(record["transition_agent_total"] for record in records) == 0
    assert sum(record["idle_agent_total"] for record in records) == 2
    assert sum(record["ask_agent_total"] + record["run_agent_total"] + record["transition_agent_total"] + record["idle_agent_total"] for record in records) == 6
    assert sum(record["agent_activity_samples"] for record in records) == 2
    token_records = [item for record in records for item in record["agent_token_rates"]]
    tokens_by_key: dict[str, float] = {}
    labels_by_key: dict[str, str] = {}
    sources_by_key: dict[str, set[str]] = {}
    for record in token_records:
        key = record["key"]
        labels_by_key[key] = record["label"]
        tokens_by_key[key] = tokens_by_key.get(key, 0.0) + float(record.get("tokens") or 0.0)
        sources_by_key.setdefault(key, set()).add(record.get("source") or "")
    assert labels_by_key["1|0|claude"] == "1:0:claude"
    assert tokens_by_key["1|0|claude"] == pytest.approx(1500.0)
    assert sources_by_key["1|0|claude"] == {"transcript"}
    assert labels_by_key["2|0|codex"] == "2:0:codex"
    assert tokens_by_key["2|0|codex"] == pytest.approx(2400.0)
    assert sources_by_key["2|0|codex"] == {"transcript"}
    assert sum(record["agent_token_samples"] for record in records) == 4


def test_stats_agent_token_rate_tracks_claude_subagent_appends(monkeypatch, tmp_path):
    app_module.session_files._CLAUDE_TRANSCRIPT_SCAN_CACHE.clear()
    webapp = app_module.TmuxWebtermApp(["1"])
    transcript = tmp_path / "session-id.jsonl"
    subagent = tmp_path / "session-id" / "subagents" / "agent-a.jsonl"
    subagent.parent.mkdir(parents=True)

    def line(message_id: str, output_tokens: int) -> str:
        return json.dumps({
            "type": "assistant",
            "message": {"id": message_id, "usage": {"output_tokens": output_tokens}, "content": []},
        }) + "\n"

    transcript.write_text(line("parent", 100), encoding="utf-8")
    subagent.write_text(line("child-first", 50), encoding="utf-8")
    monkeypatch.setattr(webapp, "stats_agent_window_rows", lambda: [{
        "session": "1",
        "kind": "claude",
        "state": "working",
        "window_index": 0,
        "window_label": "0:claude",
        "transcript": str(transcript),
    }])
    try:
        first = webapp.stats_agent_activity_record(1000.0, include_token_rates=True)
        with subagent.open("a", encoding="utf-8") as handle:
            handle.write(line("child-second", 120))
        second = webapp.stats_agent_activity_record(1060.0, include_token_rates=True)
    finally:
        webapp.control_server.stop()

    assert "_agent_token_records" not in first
    assert sum(
        rate["tokens"]
        for record in second["_agent_token_records"]
        for rate in record["agent_token_rates"]
    ) == pytest.approx(120.0)


def test_stats_sampler_bootstraps_token_scan_without_consumer(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    wall_times = iter([1000.0])
    monotonic_times = iter([50.0])
    process_times = iter([10.0])
    system_cpu_times = iter([(100.0, 20.0)])
    monkeypatch.setattr(app_module, "SERVER_STARTED_AT", 990.0)
    monkeypatch.setattr(app_module.time, "time", lambda: next(wall_times))
    monkeypatch.setattr(app_module.time, "monotonic", lambda: next(monotonic_times))
    monkeypatch.setattr(app_module.time, "process_time", lambda: next(process_times))
    monkeypatch.setattr(app_module, "current_system_cpu_times", lambda: next(system_cpu_times))
    monkeypatch.setattr(app_module, "current_system_cpu_percent_from_ps", lambda: None)
    monkeypatch.setattr(app_module, "current_process_rss_bytes", lambda: 123456)
    monkeypatch.setattr(webapp, "stats_agent_window_rows", lambda: [{"session": "1", "kind": "codex", "state": "working", "window_index": 0, "window_label": "0:codex", "transcript": "/tmp/rollout.jsonl"}])
    token_scans = []
    monkeypatch.setattr(webapp, "stats_agent_token_count", lambda _row: token_scans.append(True) or 100.0)
    try:
        payload = webapp.record_stats_global_sample(trigger="sampler")
        with webapp.stats_history_lock:
            history = webapp.stats_history_payload_locked(0, client_id="client-a")
    finally:
        webapp.control_server.stop()

    assert payload["time"] == 1000.0
    assert token_scans == [True]
    assert sum(record["agent_activity_samples"] for record in history["records"]) == 1
    assert sum(record["agent_token_samples"] for record in history["records"]) == 0


def test_stats_token_sampling_uses_idle_cadence_and_accelerates_for_consumer():
    webapp = app_module.TmuxWebtermApp([])
    try:
        with webapp.stats_agent_token_lock:
            webapp.stats_agent_token_bootstrap_pending = False
            webapp.stats_agent_token_next_sample_at = 0.0
            webapp.stats_agent_token_consumer_until = 0.0
        assert webapp.stats_agent_token_sampling_due(1000.0) is True
        assert webapp.stats_agent_token_next_sample_at == 1000.0 + app_module.STATS_AGENT_TOKEN_IDLE_SAMPLE_SECONDS
        assert webapp.stats_agent_token_sampling_due(1001.0) is False
        assert webapp.stats_agent_token_sampling_due(1001.0, token_consumer=True) is True
        assert webapp.stats_agent_token_next_sample_at == 1001.0 + app_module.STATS_AGENT_TOKEN_SAMPLE_SECONDS
    finally:
        webapp.control_server.stop()


def test_stats_sample_payload_defers_cold_token_scan_until_sampler(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    calls = []
    monkeypatch.setattr(webapp, "stats_agent_window_rows", lambda: [{"session": "1", "kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude", "transcript": "/tmp/claude.jsonl"}])

    def slow_token_count(_row):
        calls.append(time.monotonic())
        time.sleep(0.15)
        return 100.0

    monkeypatch.setattr(webapp, "stats_agent_token_count", slow_token_count)
    try:
        started = time.monotonic()
        payload = webapp.stats_sample_payload(token_consumer=True)
        response_elapsed = time.monotonic() - started
        webapp.stats_sample_cached_monotonic = None
        webapp.record_stats_global_sample(trigger="sampler")
    finally:
        webapp.control_server.stop()

    assert payload["pid"] == os.getpid()
    assert payload["uptime_seconds"] >= 0
    assert response_elapsed < 0.1
    assert len(calls) == 1


def test_stats_token_sampling_bootstraps_rate_before_steady_consumer_cadence(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    sample_times = iter([1000.0, 1001.0])
    token_totals = iter([100.0, 500.0])

    def current_sample():
        sample_time = next(sample_times)
        return {
            "time": sample_time,
            "pid": os.getpid(),
            "started_at": 990.0,
            "uptime_seconds": max(0.0, sample_time - 990.0),
            "cpu_percent": 1.0,
            "system_cpu_percent": 2.0,
            "rss_bytes": 123456,
        }, True

    monkeypatch.setattr(webapp, "current_stats_sample", current_sample)
    monkeypatch.setattr(webapp, "stats_agent_window_rows", lambda: [{"session": "1", "kind": "codex", "state": "working", "window_index": 0, "window_label": "0:codex", "transcript": "/tmp/rollout.jsonl"}])
    monkeypatch.setattr(webapp, "stats_agent_token_count", lambda _row: next(token_totals))
    try:
        first = webapp.record_stats_global_sample(trigger="sampler", token_consumer=True)
        second = webapp.record_stats_global_sample(trigger="sampler", token_consumer=True)
    finally:
        webapp.control_server.stop()

    assert first["time"] == 1000.0
    assert second["time"] == 1001.0
    assert webapp.stats_agent_token_next_sample_at == 1011.0
    with webapp.stats_history_lock:
        history = webapp.stats_history_payload_locked(0, client_id="client-a")
    token_records = [item for record in history["records"] for item in record["agent_token_rates"]]
    assert len(token_records) == 1
    assert token_records[-1]["tokens"] == pytest.approx(400.0)
    assert token_records[-1]["source"] == "transcript"


def test_stats_agent_token_count_uses_transcript_only(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    transcript_tokens = iter([654.0])
    monkeypatch.setattr(webapp, "stats_agent_transcript_token_count", lambda _row: next(transcript_tokens))
    try:
        status_only = {"session": "1", "kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude", "status_tokens": 321}
        monkeypatch.setattr(webapp, "stats_agent_transcript_token_count", lambda _row: None)
        assert webapp.stats_agent_token_count(status_only) is None
        assert "_stats_agent_token_source" not in status_only
        transcript_row = {"session": "1", "kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude", "status_tokens": 321, "transcript": "/tmp/claude.jsonl"}
        monkeypatch.setattr(webapp, "stats_agent_transcript_token_count", lambda _row: 654.0)
        assert webapp.stats_agent_token_count(transcript_row) == 654.0
        assert transcript_row["_stats_agent_token_source"] == "transcript"
    finally:
        webapp.control_server.stop()


def test_stats_agent_token_rates_use_transcript_counter_and_reset_baseline(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    rows = iter([
        [{"session": "1", "kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude", "transcript": "/tmp/claude.jsonl"}],
        [{"session": "1", "kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude", "transcript": "/tmp/claude.jsonl"}],
        [{"session": "1", "kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude", "transcript": "/tmp/claude.jsonl"}],
    ])
    transcript_tokens = iter([100.0, 220.0, 30.0])
    monkeypatch.setattr(webapp, "stats_agent_window_rows", lambda: next(rows))
    monkeypatch.setattr(webapp, "stats_agent_transcript_token_count", lambda _row: next(transcript_tokens))
    try:
        first = webapp.stats_agent_activity_record(1020.0, include_token_rates=True)
        second = webapp.stats_agent_activity_record(1080.0, include_token_rates=True)
        reset = webapp.stats_agent_activity_record(1140.0, include_token_rates=True)
    finally:
        webapp.control_server.stop()

    assert "_agent_token_records" not in first
    assert second["_agent_token_records"][0]["time"] == 1020.0
    assert second["_agent_token_records"][0]["agent_token_rates"][0]["tokens"] == pytest.approx(120.0)
    assert second["_agent_token_records"][0]["agent_token_rates"][0]["source"] == "transcript"
    assert "_agent_token_records" not in reset


def test_stats_agent_token_rate_resets_baseline_on_transcript_identity_change(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    rows = [[{"session": "1", "kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude"}] for _index in range(3)]
    token_samples = iter([(100.0, "transcript-a"), (220.0, "transcript-b"), (340.0, "transcript-b")])
    monkeypatch.setattr(webapp, "stats_agent_window_rows", lambda: rows.pop(0))

    def token_count(row):
        tokens, identity = next(token_samples)
        row["_stats_agent_token_source"] = "transcript"
        row["_stats_agent_token_identity"] = identity
        return tokens

    monkeypatch.setattr(webapp, "stats_agent_token_count", token_count)
    try:
        first = webapp.stats_agent_activity_record(1000.0, include_token_rates=True)
        switched = webapp.stats_agent_activity_record(1060.0, include_token_rates=True)
        continued = webapp.stats_agent_activity_record(1120.0, include_token_rates=True)
    finally:
        webapp.control_server.stop()

    assert "_agent_token_records" not in first
    assert "_agent_token_records" not in switched
    assert sum(record["agent_token_rates"][0]["tokens"] for record in continued["_agent_token_records"]) == pytest.approx(120.0)


def test_stats_agent_token_rate_resets_baseline_after_unobserved_gap(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    rows = [[{"session": "1", "kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude"}] for _index in range(2)]
    transcript_tokens = iter([100.0, 220.0])
    monkeypatch.setattr(webapp, "stats_agent_window_rows", lambda: rows.pop(0))
    monkeypatch.setattr(webapp, "stats_agent_transcript_token_count", lambda _row: next(transcript_tokens))
    try:
        first = webapp.stats_agent_activity_record(1000.0, include_token_rates=True)
        resumed = webapp.stats_agent_activity_record(1000.0 + app_module.STATS_AGENT_TOKEN_MAX_ATTRIBUTION_GAP_SECONDS + 1.0, include_token_rates=True)
    finally:
        webapp.control_server.stop()

    assert "_agent_token_records" not in first
    assert "_agent_token_records" not in resumed


def test_stats_agent_token_delta_records_accumulate_actual_overlap_seconds():
    webapp = app_module.TmuxWebtermApp([])
    try:
        records = [
            *webapp.stats_agent_token_delta_records("1|0|codex", "1:0:codex", 1000.0, 1010.0, 100.0),
            *webapp.stats_agent_token_delta_records("1|0|codex", "1:0:codex", 1010.0, 1020.0, 100.0),
        ]
        with webapp.stats_history_lock:
            for record in records:
                webapp.stats_history_merge_record_locked(record, 1020.0, fields=app_module.STATS_HISTORY_SERVER_FIELDS, client_id=None)
            history = webapp.stats_history_payload_locked()
    finally:
        webapp.control_server.stop()

    token_item = next(item for record in history["records"] for item in record["agent_token_rates"])
    assert token_item["tokens"] == pytest.approx(200.0)
    assert token_item["seconds"] == pytest.approx(20.0)
    assert token_item["tokens"] / token_item["seconds"] * 60 == pytest.approx(600.0)


def test_stats_agent_token_rates_ignore_visible_counter_changes(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    rows = iter([
        [{"session": "1", "kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude", "transcript": "/tmp/claude.jsonl", "status_tokens": 80}],
        [{"session": "1", "kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude", "transcript": "/tmp/claude.jsonl", "status_tokens": 8000}],
        [{"session": "1", "kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude", "transcript": "/tmp/claude.jsonl", "status_tokens": 14000}],
    ])
    transcript_tokens = iter([1000.0, 1000.0, 1060.0])
    monkeypatch.setattr(webapp, "stats_agent_window_rows", lambda: next(rows))
    monkeypatch.setattr(webapp, "stats_agent_transcript_token_count", lambda _row: next(transcript_tokens))
    try:
        first = webapp.stats_agent_activity_record(1020.0, include_token_rates=True)
        unchanged = webapp.stats_agent_activity_record(1080.0, include_token_rates=True)
        transcript_delta = webapp.stats_agent_activity_record(1140.0, include_token_rates=True)
    finally:
        webapp.control_server.stop()

    assert "_agent_token_records" not in first
    assert "_agent_token_records" not in unchanged
    assert transcript_delta["_agent_token_records"][0]["agent_token_rates"][0]["tokens"] == pytest.approx(60.0)


def test_stats_agent_activity_record_tracks_shared_transition_state(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    rows = iter([
        [{"session": "1", "kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude"}],
        [{"session": "1", "kind": "claude", "state": "idle", "window_index": 0, "window_label": "0:claude"}],
        [{"session": "1", "kind": "claude", "state": "idle", "window_index": 0, "window_label": "0:claude"}],
    ])
    monkeypatch.setattr(webapp, "stats_agent_window_rows", lambda: next(rows))
    monkeypatch.setattr(webapp, "stats_agent_transition_seconds", lambda: 60.0)
    try:
        running = webapp.stats_agent_activity_record(1000.0)
        transition = webapp.stats_agent_activity_record(1010.0)
        idle = webapp.stats_agent_activity_record(1071.0)
    finally:
        webapp.control_server.stop()

    assert running["run_agent_total"] == 1
    assert running["transition_agent_total"] == 0
    assert transition["run_agent_total"] == 0
    assert transition["transition_agent_total"] == 1
    assert transition["inactive_agent_total"] == 0
    assert idle["transition_agent_total"] == 0
    assert idle["idle_agent_total"] == 1
    assert idle["inactive_agent_total"] == 1


def test_stats_agent_activity_record_counts_each_logical_window_once(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["8001"])
    monkeypatch.setattr(
        webapp,
        "stats_agent_window_rows",
        lambda: [
            {"session": "8001", "kind": "codex", "state": "working", "window_index": 0, "window_label": "0:codex", "pid": 101},
            {"session": "8001", "kind": "codex", "state": "working", "window_index": 0, "window_label": "0:codex", "pid": 102},
            {"session": "8001", "kind": "codex", "state": "working", "window_index": 0, "window_label": "0:codex", "pid": 103},
        ],
    )
    try:
        record = webapp.stats_agent_activity_record(1000.0, include_token_rates=False)
    finally:
        webapp.control_server.stop()

    assert record["run_agent_total"] == 1
    assert record["idle_agent_total"] == 0
    assert record["active_agent_total"] == 1
    assert sum(record[key] for key in ("ask_agent_total", "run_agent_total", "transition_agent_total", "idle_agent_total")) == 1


def test_stats_agent_activity_record_counts_sticky_cooldown_as_transition(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(
        webapp,
        "stats_agent_window_rows",
        lambda: [{"session": "1", "kind": "codex", "state": "idle", "window_index": 2, "window_label": "2:codex", "working_stopped_ts": 900.0}],
    )
    monkeypatch.setattr(webapp, "stats_agent_transition_seconds", lambda: 60.0)
    try:
        record = webapp.stats_agent_activity_record(1000.0)
    finally:
        webapp.control_server.stop()

    assert record["transition_agent_total"] == 1
    assert record["idle_agent_total"] == 0
    assert record["inactive_agent_total"] == 0
    assert record["active_agent_total"] == 1


def test_stats_agent_activity_record_keeps_zero_timeout_run_to_idle_transition(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    rows = iter([
        [{"session": "1", "kind": "codex", "state": "working", "window_index": 2, "window_label": "2:codex"}],
        [{"session": "1", "kind": "codex", "state": "idle", "window_index": 2, "window_label": "2:codex"}],
        [{"session": "1", "kind": "codex", "state": "idle", "window_index": 2, "window_label": "2:codex"}],
    ])
    monkeypatch.setattr(webapp, "stats_agent_window_rows", lambda: next(rows))
    monkeypatch.setattr(webapp, "stats_agent_transition_seconds", lambda: 0.0)
    try:
        running = webapp.stats_agent_activity_record(1000.0)
        first_idle = webapp.stats_agent_activity_record(1010.0)
        later_idle = webapp.stats_agent_activity_record(1120.0)
    finally:
        webapp.control_server.stop()

    assert running["run_agent_total"] == 1
    assert first_idle["transition_agent_total"] == 1
    assert first_idle["idle_agent_total"] == 0
    assert later_idle["transition_agent_total"] == 1
    assert later_idle["idle_agent_total"] == 0


def test_stats_agent_activity_record_drops_acknowledged_attention_and_cooldown(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(
        webapp,
        "stats_agent_window_rows",
        lambda: [
            {"session": "1", "kind": "claude", "state": "needs-input", "window_index": 1, "window_label": "1:claude", "attention_acknowledged": True},
            {"session": "1", "kind": "codex", "state": "idle", "window_index": 2, "window_label": "2:codex", "working_stopped_ts": 900.0, "cooldown_acknowledged": True},
        ],
    )
    monkeypatch.setattr(webapp, "stats_agent_transition_seconds", lambda: 0.0)
    try:
        record = webapp.stats_agent_activity_record(1000.0)
    finally:
        webapp.control_server.stop()

    assert record["ask_agent_total"] == 0
    assert record["transition_agent_total"] == 0
    assert record["idle_agent_total"] == 2
    assert record["inactive_agent_total"] == 2
    assert record["active_agent_total"] == 0


def test_stats_agent_idle_means_not_ask_run_or_transition(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(webapp, "stats_agent_transition_seconds", lambda: 60.0)
    try:
        with webapp.stats_agent_token_lock:
            active_kind = webapp.stats_agent_activity_kind_locked({"state": "active"}, "active-agent", 1000.0, 60.0)
            settled_kind = webapp.stats_agent_activity_kind_locked({"state": "settled"}, "settled-agent", 1000.0, 60.0)
            ask_kind = webapp.stats_agent_activity_kind_locked({"state": "needs-input"}, "ask-agent", 1000.0, 60.0)
            run_kind = webapp.stats_agent_activity_kind_locked({"state": "working"}, "run-agent", 1000.0, 60.0)
            transition_kind = webapp.stats_agent_activity_kind_locked({"state": "cooldown"}, "transition-agent", 1000.0, 60.0)
    finally:
        webapp.control_server.stop()

    assert active_kind == "idle"
    assert settled_kind == "idle"
    assert ask_kind == "ask"
    assert run_kind == "run"
    assert transition_kind == "transition"


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


def test_auto_approve_status_refreshes_session_order(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["old"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["new"], None))
    monkeypatch.setattr(webapp, "auto_approve_session_status", lambda session, **_kwargs: {"target": session})
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    try:
        payload, status = webapp.auto_approve_status()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["session_order"] == ["new"]
    assert payload["sessions"] == {"new": {"target": "new"}}


def test_auto_approve_status_reuses_cached_roster(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    build_calls = []

    def fake_build_auto_approve_status(session=None, timings=None):
        build_calls.append(session)
        if timings is not None:
            timings["refresh_sessions"] = 1.0
        return {"session_order": ["1"], "sessions": {"1": {"enabled": False}}, "errors": [], "rules": {}}, HTTPStatus.OK

    monkeypatch.setattr(webapp, "build_auto_approve_status", fake_build_auto_approve_status)
    try:
        first, first_status = webapp.auto_approve_status()
        second, second_status = webapp.auto_approve_status()
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert first["sessions"] == second["sessions"] == {"1": {"enabled": False}}
    assert build_calls == [None]
    assert second["cache"]["stale"] is False


def test_attention_acknowledgement_is_server_owned(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "ACTIVITY_PATH", tmp_path / "activity.json")
    monkeypatch.setattr(app_module, "ACTIVITY_HEARTBEATS_PATH", tmp_path / "activity-heartbeats.jsonl")
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1"], None))
    monkeypatch.setattr(app_module, "auto_approve_lock_owner", lambda _session: None)
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(webapp, "prompt_and_screen_status", lambda *args, **kwargs: (
        {"visible": True, "yes_selected": True, "text": "Run sleep 10?", "signature": "prompt-sig"},
        {"key": "idle", "text": ""},
    ))
    monkeypatch.setattr(webapp, "agent_window_status_payloads", lambda *args, **kwargs: [])
    try:
        payload = webapp.auto_approve_session_status("1", discovered_sessions={})
        key = webapp.attention_ack_key("prompt", "1", "prompt-sig")
        assert payload["prompt_attention_key"] == key
        assert payload["prompt"]["attention_key"] == key
        assert payload["prompt_attention_acknowledged"] is False

        result, status = webapp.acknowledge_attention({"keys": [key]})
        assert status == HTTPStatus.OK
        assert result["acknowledged"] == [key]

        payload = webapp.auto_approve_session_status("1", discovered_sessions={})
        assert payload["attention_acks"]["keys"] == [key]
        assert payload["attention_acks"]["acknowledged_at"][key] > 0
        assert payload["prompt_attention_acknowledged"] is True
        assert payload["prompt"]["attention_acknowledged"] is True
    finally:
        webapp.control_server.stop()


def test_agent_window_attention_key_uses_shared_per_window_hash_transitions(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module.common, "TMUX_AI_STATUS_PATH", tmp_path / "tmux-AI-status.json")
    monkeypatch.setattr(app_module.common, "LEGACY_ATTENTION_ACKS_PATH", tmp_path / "attention-acks.json")
    webapp = app_module.TmuxWebtermApp(["1"])
    first_screen = {"key": "approval", "question_text": "Do you want to proceed?", "prompt_hash": "command-a"}
    second_screen = {"key": "approval", "question_text": "Do you want to proceed?", "prompt_hash": "command-b"}
    try:
        signature = lambda screen: webapp.shared_agent_window_attention_instance_signature(
            "8001", "1", "%15", "claude", "approval", webapp.agent_window_attention_signature("approval", screen)
        )
        first_a = signature(first_screen)
        repeated_a = signature(first_screen)
        first_b = signature(second_screen)
        returned_a = signature(first_screen)
        webapp.shared_agent_window_attention_instance_signature("8001", "1", "%15", "claude", "idle", "")
        after_idle_a = signature(first_screen)
    finally:
        webapp.control_server.stop()

    assert first_a == repeated_a == "command-a:1"
    assert first_b == "command-b:2"
    assert returned_a == "command-a:3"
    assert after_idle_a == "command-a:4"


def test_auto_approve_status_returns_stale_cache_while_refreshing(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    refreshes = []
    stale_payload = {"session_order": ["1"], "sessions": {"1": {"enabled": True}}, "errors": [], "rules": {}}
    with webapp.auto_approve_cache_condition:
        webapp.auto_approve_cache = (time.monotonic() - app_module.AUTO_APPROVE_CACHE_MAX_AGE_SECONDS - 1.0, (stale_payload, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "merge_shared_attention_acks", lambda: False)
    monkeypatch.setattr(webapp, "start_auto_approve_cache_refresh", lambda: refreshes.append("refresh") or True)
    try:
        payload, status = webapp.auto_approve_status()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["sessions"] == stale_payload["sessions"]
    assert payload["cache"]["stale"] is True
    assert refreshes == ["refresh"]


def test_stats_agent_window_rows_reuses_fresh_auto_approve_cache(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    cached_payload = {
        "session_order": ["1"],
        "sessions": {
            "1": {
                "target": "1",
                "enabled": True,
                "agent_windows": [
                    {"kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude", "transcript": "/tmp/claude.jsonl"},
                ],
            },
        },
        "errors": [],
        "rules": {},
    }
    with webapp.auto_approve_cache_condition:
        webapp.auto_approve_cache = (time.monotonic(), (cached_payload, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "refresh_sessions", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stats should use the fresh auto-approve cache")))
    try:
        rows = webapp.stats_agent_window_rows()
    finally:
        webapp.control_server.stop()

    assert rows == [
        {"kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude", "transcript": "/tmp/claude.jsonl", "session": "1"},
    ]
    assert "session" not in cached_payload["sessions"]["1"]["agent_windows"][0]


def test_auto_approve_session_status_skips_roster_cache(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    build_calls = []

    def fake_build_auto_approve_status(session=None, timings=None):
        build_calls.append(session)
        return {"target": session, "enabled": False}, HTTPStatus.OK

    monkeypatch.setattr(webapp, "build_auto_approve_status", fake_build_auto_approve_status)
    try:
        payload, status = webapp.auto_approve_status("5")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload == {"target": "5", "enabled": False}
    assert build_calls == ["5"]
    assert webapp.auto_approve_cache is None


def test_auto_approve_session_lock_owner_probes_agent_pane_targets(monkeypatch):
    # Regression: YO workers lock the agent PANE target (e.g. %7), NOT the bare session, so a server
    # without a local worker must probe the pane-target lock to notice another server's ownership.
    # Probing only the session lock (None here) missed every agent-backed session and silently
    # dropped the cross-server "YO running elsewhere" (yellow) marker.
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        monkeypatch.setattr(webapp, "auto_approve_agent_targets", lambda session, *a, **k: ["%7"] if session == "7" else [])
        owners = {"%7": {"pid": 4242, "project_root": "/home/x/remote-worktree"}}
        monkeypatch.setattr(app_module, "auto_approve_lock_owner", lambda target: owners.get(target))
        # The pane-target lock is found even though the bare-session lock is unheld.
        assert webapp.auto_approve_session_lock_owner("7") == owners["%7"]
        # A session whose pane target is unlocked stays None, so no false yellow.
        assert webapp.auto_approve_session_lock_owner("5") is None
    finally:
        webapp.control_server.stop()


def test_auto_approve_session_lock_owner_falls_back_to_bare_session(monkeypatch):
    # No detected agent (e.g. a plain shell): the worker locks the bare session, so the detector
    # must still probe it.
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        monkeypatch.setattr(webapp, "auto_approve_agent_targets", lambda session, *a, **k: [])
        owners = {"9": {"pid": 99}}
        monkeypatch.setattr(app_module, "auto_approve_lock_owner", lambda target: owners.get(target))
        assert webapp.auto_approve_session_lock_owner("9") == owners["9"]
    finally:
        webapp.control_server.stop()


def test_auto_approve_status_reports_elsewhere_for_agent_pane_lock(monkeypatch):
    # End to end: with the agent pane locked by another server and no local worker, the roster
    # payload for that session must carry enabled_elsewhere/locked so the UI paints it yellow.
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        monkeypatch.setattr(webapp, "auto_approve_agent_targets", lambda session, *a, **k: ["%7"] if session == "7" else [])
        owners = {"%7": {"pid": 4242, "project_root": "/home/x/remote-worktree"}}
        monkeypatch.setattr(app_module, "auto_approve_lock_owner", lambda target: owners.get(target))
        monkeypatch.setattr(webapp, "prompt_and_screen_status", lambda *a, **k: (app_module.blank_prompt_state(), {"key": "idle", "text": ""}))
        payload = webapp.auto_approve_session_status("7")
        assert payload["enabled"] is False
        assert payload["enabled_elsewhere"] is True
        assert payload["locked"] is True
        assert payload["lock_owner"] == owners["%7"]
    finally:
        webapp.control_server.stop()


def test_auto_approve_agent_targets_include_codex_process_under_node(monkeypatch):
    # Real Codex panes often expose `node` as pane_current_command; the process tree is the stronger
    # signal for auto-approve worker targeting, otherwise YO watches Claude and misses Codex prompts.
    fixture = yaml.safe_load((PROMOTED_CAPTURE_DIR / "shell_approval_touch_command__codex-cli-0.141.0_20260620.yaml").read_text(encoding="utf-8"))
    assert fixture["agent"] == "codex"
    assert fixture["cursor"]["current_command"] == "node"
    assert fixture["expected_promoted"]["approval_visible"] is True
    assert fixture["expected_promoted"]["approval_type"] == "bash"

    info = SessionInfo(
        session="8002",
        panes=[
            PaneInfo(
                session="8002",
                window="0",
                window_name="node",
                pane="0",
                pane_id="%73",
                target="%73",
                current_path="/repo",
                command=fixture["cursor"]["current_command"],
                active=True,
                window_active=True,
                title="[ ! ] Action Required | repo",
                pid=3000,
                process_label="codex",
                process_label_pid=3001,
            ),
            PaneInfo(
                session="8002",
                window="1",
                window_name="claude",
                pane="0",
                pane_id="%5",
                target="%5",
                current_path="/repo",
                command="claude",
                active=True,
                window_active=False,
                title="Claude",
                pid=4000,
                process_label="claude",
                process_label_pid=4000,
            ),
        ],
        selected_pane=None,
        agents=[
            AgentInfo("8002", "codex", 3001, "%73", "codex resume sid", "/repo", None, "sid", "/tmp/codex.jsonl", None),
            AgentInfo("8002", "claude", 4000, "%5", "claude", "/repo", "idle", "cid", "/tmp/claude.jsonl", None),
        ],
    )
    signal_payload = {
        "ok": True,
        "agents": [
            {"session": "8002", "target": "%5", "pane_id": "%5", "agent": "claude", "dead": False},
        ],
        "windows": [],
    }
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"8002": info}, []))
    webapp = app_module.TmuxWebtermApp(["8002"])
    try:
        assert webapp.auto_approve_agent_targets("8002", payload=signal_payload) == ["%73", "%5"]
    finally:
        webapp.control_server.stop()


def test_share_token_url_seeds_whole_layout_sessions_and_layout():
    webapp = app_module.TmuxWebtermApp(["6", "7"])
    try:
        payload, status = webapp.create_share_token(
            "6",
            900,
            sessions=["6", "7"],
            base_url="https://yolo.example.test:8002",
            created_by="keivenc",
            layout="row@50(left,slot1)",
            tabs="left:6;slot1:7",
            finder={"root": "/home/keivenc/yolomux.dev1", "rootMode": "fixed", "mode": "tabber", "session": "7"},
            scheme="https",
            request_is_https=True,
            tls_available=True,
        )
        parsed = urlparse(payload["url"])
        params = parse_qs(parsed.query)

        assert status == HTTPStatus.OK
        assert payload["ok"] is True
        assert payload["session"] == "6"
        assert parsed.scheme == "https"
        assert parsed.netloc == "yolo.example.test:8002"
        assert parsed.path == f"/share/{payload['short_id']}"
        assert parsed.fragment == f"t={payload['token']}"
        assert "token" not in params
        assert "sessions" not in params
        assert "layout" not in params
        assert "tabs" not in params
        assert payload["sessions"] == ["6", "7"]
        assert payload["layout"] == "row@50(left,slot1)"
        assert payload["tabs"] == "left:6;slot1:7"
        assert payload["finder"] == {"root": "/home/keivenc/yolomux.dev1", "rootMode": "fixed", "mode": "tabber", "session": "7"}
        record = webapp.verify_share_token(payload["token"])
        assert record["session"] == "6"
        assert record["sessions"] == ["6", "7"]
        assert record["finder"] == {"root": "/home/keivenc/yolomux.dev1", "rootMode": "fixed", "mode": "tabber", "session": "7"}
        assert record["mode"] == "ro"
        assert record["scheme"] == "https"
        assert record["max_viewers"] == app_module.SHARE_MAX_VIEWERS_DEFAULT
        assert webapp.share_record_for_short_id(payload["short_id"])["token"] == payload["token"]
    finally:
        webapp.control_server.stop()


def test_share_debug_profile_is_opt_in_and_redacted(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SHARE_DEBUG_PROFILE_LOG_DIR", tmp_path)
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        disabled, disabled_status = webapp.create_share_token("6", 900)
        enabled, enabled_status = webapp.create_share_token("6", 900, debug_profile=True)

        assert disabled_status == HTTPStatus.OK
        assert enabled_status == HTTPStatus.OK
        assert disabled["debug_profile"] is False
        assert disabled["debugProfile"] is False
        assert enabled["debug_profile"] is True
        assert enabled["debugProfile"] is True
        assert webapp.verify_share_token(disabled["token"])["debug_profile"] is False
        assert webapp.verify_share_token(enabled["token"])["debug_profile"] is True

        denied, denied_status = webapp.record_share_debug_profile(disabled["token"], {"kind": "share-replay-health"})
        assert denied_status == HTTPStatus.FORBIDDEN
        assert denied["error"] == "debug/profiling upload is not enabled for this share"
        assert denied["user_message"]["key"] == "share.error.debugProfileDisabled"

        payload, status = webapp.record_share_debug_profile(
            enabled["token"],
            {
                "kind": "share-geometry-drift",
                "viewerId": "viewer-a",
                "url": f"https://host.example/share/{enabled['short_id']}#t={enabled['token']}",
                "nested": {"shareToken": enabled["token"], "text": f"token={enabled['token']}"},
            },
            ip="203.0.113.9",
            user_agent="Mozilla/5.0 Version/26.0 Safari/605.1.15",
        )

        assert status == HTTPStatus.OK
        assert payload["ok"] is True
        assert payload["logged"] is True
        record = webapp.verify_share_token(enabled["token"])
        stored_text = json.dumps(record["debug_profile_events"][-1], sort_keys=True)
        assert record["debug_profile_events"][-1]["browser"] == "Safari 26.0"
        assert "share-geometry-drift" in stored_text
        assert enabled["token"] not in stored_text
        assert f"/share/{enabled['short_id']}" not in stored_text
        assert "[redacted-share-token]" in stored_text
        log_text = (tmp_path / f"{enabled['short_id']}.jsonl").read_text(encoding="utf-8")
        assert enabled["token"] not in log_text
        assert f"/share/{enabled['short_id']}" not in log_text
    finally:
        webapp.control_server.stop()


def test_share_token_clamps_mode_scheme_viewers_and_allows_concurrent_shares():
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        payload, status = webapp.create_share_token(
            "6",
            900,
            base_url="https://yolo.example.test:8002",
            mode="rw",
            scheme="http",
            request_is_https=True,
            tls_available=True,
        )
        assert status == HTTPStatus.BAD_REQUEST
        assert payload["error"] == "write shares require https"
        assert payload["user_message"]["key"] == "share.error.writeHttps"

        payload, status = webapp.create_share_token(
            "6",
            900,
            base_url="https://yolo.example.test:8002",
            mode="rw",
            scheme="https",
            max_viewers=999,
            request_is_https=True,
            tls_available=True,
        )
        assert status == HTTPStatus.OK
        assert payload["mode"] == "rw"
        assert payload["scheme"] == "https"
        assert payload["max_viewers"] == app_module.SHARE_MAX_VIEWERS_HARD_LIMIT

        second, second_status = webapp.create_share_token(
            "6",
            120,
            mode="ro",
            scheme="http",
            request_is_https=False,
            tls_available=True,
        )
        assert second_status == HTTPStatus.OK
        assert second["mode"] == "ro"
        assert second["scheme"] == "http"
        active = webapp.active_share_payload()[0]
        assert {share["token"] for share in active["shares"]} == {payload["token"], second["token"]}
        assert {share["mode"] for share in active["shares"]} == {"rw", "ro"}
    finally:
        webapp.control_server.stop()


def test_share_token_forces_readonly_without_tls():
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        payload, status = webapp.create_share_token(
            "6",
            900,
            base_url="https://yolo.example.test:8002",
            mode="rw",
            scheme="https",
            request_is_https=False,
            tls_available=False,
        )

        assert status == HTTPStatus.OK
        assert payload["mode"] == "ro"
        assert payload["scheme"] == "http"
        assert urlparse(payload["url"]).scheme == "http"
    finally:
        webapp.control_server.stop()


def test_active_share_payload_and_stop_active_share():
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        empty, empty_status = webapp.active_share_payload(base_url="https://yolo.example.test:8002")
        assert empty_status == HTTPStatus.OK
        assert empty == {"ok": True, "active": False, "shares": []}

        payload, status = webapp.create_share_token(
            "6",
            900,
            base_url="https://yolo.example.test:8002",
            scheme="https",
            request_is_https=True,
            tls_available=True,
            layout="left",
            tabs="left:6",
            ui_state={"viewport": {"width": 1440, "height": 900}, "editor": {"modes": [{"path": "/tmp/a.md", "mode": "split"}]}},
        )
        assert status == HTTPStatus.OK

        active, active_status = webapp.active_share_payload(base_url="https://new-host.example.test:9443")
        assert active_status == HTTPStatus.OK
        assert active["active"] is True
        assert active["token"] == payload["token"]
        assert active["url"].startswith("https://new-host.example.test:9443/share/")
        assert active["url"].endswith(f"#t={payload['token']}")
        assert active["shares"][0]["token"] == payload["token"]

        scoped_status, scoped_status_code = webapp.share_status_payload(payload["token"], base_url="https://viewer.example.test:9443")
        assert scoped_status_code == HTTPStatus.OK
        assert scoped_status["token"] == payload["token"]
        assert scoped_status["url"].startswith("https://viewer.example.test:9443/share/")
        assert scoped_status["shares"] == []
        assert scoped_status["layout"] == "left"
        assert scoped_status["tabs"] == "left:6"
        assert scoped_status["viewport"] == {"width": 1440, "height": 900}
        assert scoped_status["uiState"]["editor"] == {"modes": [{"path": "/tmp/a.md", "mode": "split"}]}

        missing_status, missing_status_code = webapp.share_status_payload("wrong-token")
        assert missing_status_code == HTTPStatus.UNAUTHORIZED
        assert missing_status["active"] is False
        assert missing_status["user_message"]["key"] == "share.error.tokenExpired"

        second, second_status = webapp.create_share_token(
            "6",
            120,
            base_url="http://new-host.example.test:9443",
            scheme="http",
            request_is_https=False,
            tls_available=True,
        )
        assert second_status == HTTPStatus.OK

        scoped, scoped_status = webapp.stop_active_share(payload["short_id"])
        assert scoped_status == HTTPStatus.OK
        assert scoped["stopped"] == 1
        assert scoped["active"] is True
        assert webapp.verify_share_token(payload["token"]) is None
        assert webapp.verify_share_token(second["token"]) is not None

        stopped, stopped_status = webapp.stop_active_share()
        assert stopped_status == HTTPStatus.OK
        assert stopped["stopped"] == 1
        assert webapp.verify_share_token(second["token"]) is None
        assert webapp.active_share_payload()[0] == {"ok": True, "active": False, "shares": []}
    finally:
        webapp.control_server.stop()


def test_share_viewer_registration_enforces_cap_and_decrements():
    webapp = app_module.TmuxWebtermApp(["6", "7"])
    try:
        payload, status = webapp.create_share_token("6", 900, sessions=["6", "7"], max_viewers=1)
        assert status == HTTPStatus.OK

        user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
        viewer, viewer_status = webapp.register_share_viewer(payload["token"], "6", "viewer-a", "203.0.113.4", user_agent)
        assert viewer_status == HTTPStatus.OK
        assert viewer["viewers"] == 1
        active = webapp.active_share_payload()[0]
        assert active["viewers"] == 1
        assert active["viewer_details"][0]["ip"] == "203.0.113.4"
        assert active["viewer_details"][0]["browser"] == "Chrome 125.0.0.0"
        assert active["viewer_details"][0]["connected_seconds"] >= 0

        same_viewer, same_viewer_status = webapp.register_share_viewer(payload["token"], "7", "viewer-a")
        assert same_viewer_status == HTTPStatus.OK
        assert same_viewer["viewers"] == 1
        assert webapp.active_share_payload()[0]["viewers"] == 1

        wrong_session, wrong_status = webapp.register_share_viewer(payload["token"], "8")
        assert wrong_status == HTTPStatus.FORBIDDEN
        assert wrong_session["error"] == "share token is scoped to a different session"
        assert wrong_session["user_message"]["key"] == "share.error.sessionScope"

        rejected, rejected_status = webapp.register_share_viewer(payload["token"], "6", "viewer-b")
        assert rejected_status == HTTPStatus.FORBIDDEN
        assert rejected["error"] == "share viewer limit reached"
        assert rejected["user_message"]["key"] == "share.error.viewerLimitReached"
        status_frame = webapp.share_status_frame_payload(payload["token"])
        assert status_frame["viewer_details"][0]["ip"] == "203.0.113.4"
        assert status_frame["viewer_details"][0]["browser"] == "Chrome 125.0.0.0"

        assert webapp.unregister_share_viewer(payload["token"], "viewer-a") == 1
        assert webapp.unregister_share_viewer(payload["token"], "viewer-a") == 0
        assert webapp.active_share_payload()[0]["viewers"] == 0
    finally:
        webapp.control_server.stop()


def test_share_token_revokes_when_session_disappears():
    webapp = app_module.TmuxWebtermApp(["6", "7"])
    try:
        payload, status = webapp.create_share_token("6", 900, sessions=["6", "7"])
        assert status == HTTPStatus.OK
        assert webapp.verify_share_token(payload["token"])["session"] == "6"
        assert webapp.verify_share_token(payload["token"])["sessions"] == ["6", "7"]

        assert webapp.revoke_share_tokens_for_missing_sessions({"6"}) == 1
        assert webapp.verify_share_token(payload["token"]) is None
    finally:
        webapp.control_server.stop()


def test_share_extend_updates_expiry_and_status_frame_is_secret_free():
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        payload, status = webapp.create_share_token(
            "6",
            900,
            base_url="https://yolo.example.test:8002",
            scheme="https",
            request_is_https=True,
            tls_available=True,
            max_viewers=5,
        )
        assert status == HTTPStatus.OK
        before = payload["expires_at"]

        extended, extend_status = webapp.extend_share_token(payload["short_id"], 600)

        assert extend_status == HTTPStatus.OK
        assert extended["extended"] is True
        assert extended["expires_at"] > before
        status_frame = webapp.share_status_frame_payload(payload["token"])
        assert status_frame["active"] is True
        assert status_frame["short_id"] == payload["short_id"]
        assert status_frame["viewers"] == 0
        assert status_frame["max_viewers"] == 5
        assert "token" not in status_frame
        assert "url" not in status_frame
    finally:
        webapp.control_server.stop()


def test_share_record_ui_state_updates_late_viewer_layout_and_files():
    webapp = app_module.TmuxWebtermApp(["6", "7"])
    try:
        payload, status = webapp.create_share_token("6", 900, sessions=["6", "7"], tabs="left:6")
        assert status == HTTPStatus.OK
        token = payload["token"]

        webapp.update_share_record_ui_state(token, {
            "layout": "row@60(left,slot1)",
            "tabs": "left:6;slot1:file:/tmp/a.md,filediff:/tmp/c.py,filecopy:copy-1:/tmp/d.py,image:/tmp/screen.png",
            "finder": {"root": "/tmp", "rootMode": "fixed", "mode": "tabber", "session": "7"},
            "uiState": {"editor": {"modes": [{"path": "/tmp/b.md", "mode": "split"}]}},
        })

        record = webapp.verify_share_token(token)
        assert record["layout"] == "row@60(left,slot1)"
        assert record["tabs"] == "left:6;slot1:file:/tmp/a.md,filediff:/tmp/c.py,filecopy:copy-1:/tmp/d.py,image:/tmp/screen.png"
        assert record["finder"] == {"root": "/tmp", "rootMode": "fixed", "mode": "tabber", "session": "7"}
        assert record["ui_state"] == {"editor": {"modes": [{"path": "/tmp/b.md", "mode": "split"}]}}
        assert webapp.share_record_allows_file_path(record, "/tmp/a.md")
        assert webapp.share_record_allows_file_path(record, "/tmp/b.md")
        assert webapp.share_record_allows_file_path(record, "/tmp/c.py")
        assert webapp.share_record_allows_file_path(record, "/tmp/d.py")
        assert webapp.share_record_allows_file_path(record, "/tmp/screen.png")
        assert not webapp.share_record_allows_file_path(record, "/tmp/private.md")
    finally:
        webapp.control_server.stop()


def test_share_record_ui_state_updates_late_viewer_sessions_from_layout_tabs():
    webapp = app_module.TmuxWebtermApp(["6", "7"])
    try:
        payload, status = webapp.create_share_token("6", 900, sessions=["6"], tabs="left:6")
        assert status == HTTPStatus.OK
        token = payload["token"]

        webapp.update_share_record_ui_state(token, {
            "layout": "row@50(left,right)",
            "tabs": "left:6;right:7,ghost,file:/tmp/a.md",
            "finder": {"root": "/tmp", "rootMode": "fixed", "mode": "diff", "session": "7"},
        })

        record = webapp.verify_share_token(token)
        assert record["session"] == "6"
        assert record["sessions"] == ["6", "7"]
        assert record["finder"]["session"] == "7"
    finally:
        webapp.control_server.stop()


def test_share_ui_state_normalizes_viewport_and_appearance():
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        normalized = webapp.normalize_share_ui_state({
            "viewport": {"w": "1440.4", "h": "900.2"},
            "appearance": {
                "locale": "ja",
                "languagePref": "system",
                "uiFontSize": 999,
                "terminalFontSize": 15,
                "terminalLineHeight": 1,
                "editorFontSize": 14,
                "previewFontSize": 16,
                "fileExplorerFontSize": 13,
                "tabWidth": 240,
                "paneSpacing": -4,
                "theme": "dark",
                "resolvedTheme": "dark",
                "terminalTheme": "follow-app",
                "activeColor": "green",
                "separatorColor": "theme",
                "unknown": "ignored",
            },
            "chrome": {"tabMetaVisible": False, "infoSubTab": "yoagent"},
            "editor": {"modes": [{"path": "/tmp/a.py", "item": "file:/tmp/a.py", "mode": "diff", "diffExpandUnchanged": True}]},
            "scroll": [
                {"target": "preferences", "kind": "preferences", "top": "444.6", "left": 12, "ignored": "drop"},
                {"target": "editor:file:/tmp/a.py:editor", "kind": "editor", "path": "/tmp/a.py", "item": "file:/tmp/a.py", "source": "editor", "top": 80, "left": 2, "anchor": 5, "head": 7},
            ],
        })
        assert normalized["viewport"] == {"width": 1440, "height": 900}
        assert normalized["appearance"]["uiFontSize"] == 20
        assert normalized["appearance"]["paneSpacing"] == 0
        assert normalized["appearance"]["locale"] == "ja"
        assert normalized["appearance"]["languagePref"] == "system"
        assert normalized["appearance"]["terminalTheme"] == "follow-app"
        assert normalized["chrome"] == {"tabMetaVisible": False, "infoSubTab": "yoagent"}
        assert normalized["editor"]["modes"][0]["diffExpandUnchanged"] is True
        assert normalized["scroll"] == [
            {"target": "preferences", "kind": "preferences", "top": 445, "left": 12},
            {"target": "editor:file:/tmp/a.py:editor", "kind": "editor", "top": 80, "left": 2, "path": "/tmp/a.py", "item": "file:/tmp/a.py", "source": "editor", "anchor": 5, "head": 7},
        ]
        assert "unknown" not in normalized["appearance"]
    finally:
        webapp.control_server.stop()


def test_share_ui_state_patch_merges_geometry_without_losing_editor_state():
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        payload, status = webapp.create_share_token(
            "6",
            900,
            tabs="left:file:/tmp/a.md",
            ui_state={"editor": {"modes": [{"path": "/tmp/a.md", "mode": "split"}]}},
        )
        assert status == HTTPStatus.OK
        webapp.update_share_record_ui_state(payload["token"], {"uiStatePatch": {"viewport": {"width": 1280, "height": 720}}})
        webapp.update_share_record_ui_state(payload["token"], {"uiStateScroll": {"target": "preferences", "kind": "preferences", "top": 444, "left": 12}})
        webapp.update_share_record_ui_state(payload["token"], {"uiStateScroll": {"target": "info", "kind": "info", "top": 20, "left": 30}})
        record = webapp.verify_share_token(payload["token"])
        assert record["ui_state"]["editor"] == {"modes": [{"path": "/tmp/a.md", "mode": "split"}]}
        assert record["ui_state"]["viewport"] == {"width": 1280, "height": 720}
        assert record["ui_state"]["scroll"] == [
            {"target": "preferences", "kind": "preferences", "top": 444, "left": 12},
            {"target": "info", "kind": "info", "top": 20, "left": 30},
        ]
        assert webapp.share_record_allows_file_path(record, "/tmp/a.md")
    finally:
        webapp.control_server.stop()


def test_share_file_read_allowlist_tracks_current_editor_state():
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        payload, status = webapp.create_share_token(
            "6",
            900,
            tabs="left:6",
            ui_state={"editor": {"modes": [{"path": "/tmp/a.md", "mode": "edit"}]}},
        )
        assert status == HTTPStatus.OK
        record = webapp.verify_share_token(payload["token"])
        assert webapp.share_record_allows_file_path(record, "/tmp/a.md")

        webapp.update_share_record_ui_state(payload["token"], {"uiState": {"editor": {"modes": [{"path": "/tmp/b.md", "mode": "preview"}]}}})
        record = webapp.verify_share_token(payload["token"])
        assert not webapp.share_record_allows_file_path(record, "/tmp/a.md")
        assert webapp.share_record_allows_file_path(record, "/tmp/b.md")
    finally:
        webapp.control_server.stop()


def test_server_event_poll_seconds_accepts_fast_server_side_interval(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(
            app_module,
            "settings_payload",
            lambda: {"settings": {"performance": {"server_event_poll_ms": 100}}},
        )
        assert webapp.server_event_poll_seconds() == 0.25
        monkeypatch.setattr(
            app_module,
            "settings_payload",
            lambda: {"settings": {"performance": {"server_event_poll_ms": 850}}},
        )
        assert webapp.server_event_poll_seconds() == 0.85
    finally:
        webapp.control_server.stop()


def test_server_directory_event_poll_seconds_uses_own_interval(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(
            app_module,
            "settings_payload",
            lambda: {"settings": {"performance": {
                "server_event_poll_ms": 250,
                "server_background_file_event_poll_ms": 5000,
                "server_directory_event_poll_ms": 1250,
            }}},
        )
        assert webapp.server_event_poll_seconds() == 0.25
        assert webapp.server_background_file_event_poll_seconds() == 5.0
        assert webapp.server_directory_event_poll_seconds() == 1.25
    finally:
        webapp.control_server.stop()


def test_backend_poll_interval_fallbacks_use_settings_defaults(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    defaults = app_module.DEFAULT_PERFORMANCE_SETTINGS
    try:
        monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"performance": {}}})

        assert webapp.server_event_poll_seconds() == pytest.approx(defaults["server_event_poll_ms"] / 1000.0)
        assert webapp.server_background_file_event_poll_seconds() == pytest.approx(defaults["server_background_file_event_poll_ms"] / 1000.0)
        assert webapp.server_directory_event_poll_seconds() == pytest.approx(defaults["server_directory_event_poll_ms"] / 1000.0)
        assert webapp.tabber_activity_refresh_seconds() == pytest.approx(defaults["tabber_activity_refresh_ms"] / 1000.0)
        assert webapp.auto_approve_interval_seconds() == pytest.approx(defaults["auto_approve_interval_seconds"])
    finally:
        webapp.control_server.stop()


def test_session_files_cache_seconds_default_is_not_aggressive():
    assert app_module.SESSION_FILES_CACHE_SECONDS >= 30.0


def test_session_files_cache_key_ignores_transcript_append_mtime_and_size(tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"type": "response_item", "payload": {"info": {"total_token_usage": {"output_tokens": 10}}}}) + "\n", encoding="utf-8")
    info = SessionInfo(
        session="5",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="5",
                kind="codex",
                pid=123,
                pane_target="5:0.0",
                command="codex",
                cwd=str(tmp_path),
                status="running",
                session_id="session-5",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        first_key = webapp.session_files_cache_key("payload", {"5": info}, "5", 24.0, None, None, None)
        first_path, first_signature = webapp.session_files_disk_cache_path(first_key)
        first_stat = transcript.stat()
        transcript.write_text(
            transcript.read_text(encoding="utf-8") + json.dumps({"type": "response_item", "payload": {"info": {"total_token_usage": {"output_tokens": 20}}}}) + "\n",
            encoding="utf-8",
        )
        second_stat = transcript.stat()
        second_key = webapp.session_files_cache_key("payload", {"5": info}, "5", 24.0, None, None, None)
        second_path, second_signature = webapp.session_files_disk_cache_path(second_key)
    finally:
        webapp.control_server.stop()

    assert first_key[1] == app_module.SESSION_FILES_CACHE_KEY_VERSION
    assert second_stat.st_size > first_stat.st_size
    assert second_stat.st_mtime_ns >= first_stat.st_mtime_ns
    assert second_key == first_key
    assert second_signature == first_signature
    assert second_path == first_path


def test_client_status_poll_fallbacks_are_interactive_with_jitter(monkeypatch):
    assert app_module.SERVER_AUTO_APPROVE_EVENT_POLL_SECONDS == pytest.approx(1.5)
    assert app_module.SERVER_TMUX_SIGNAL_EVENT_POLL_SECONDS == pytest.approx(1.5)
    assert app_module.SERVER_INTERACTIVE_EVENT_POLL_JITTER_SECONDS == pytest.approx(0.5)

    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(app_module.random, "uniform", lambda lower, upper: upper)
        assert webapp.server_auto_approve_event_poll_seconds() == pytest.approx(1.875)
        assert webapp.server_tmux_signal_event_poll_seconds() == pytest.approx(1.875)
        monkeypatch.setattr(app_module.random, "uniform", lambda lower, upper: lower)
        assert webapp.server_auto_approve_event_poll_seconds() == pytest.approx(1.125)
        assert webapp.server_tmux_signal_event_poll_seconds() == pytest.approx(1.125)
    finally:
        webapp.control_server.stop()


def test_tmux_session_exists_payload_is_read_only_and_refreshes_roster(monkeypatch):
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1", "3"], None))
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    try:
        payload, status = webapp.tmux_session_exists_payload("2")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload == {"session": "2", "exists": False, "ok": True}
    assert webapp.sessions == ["1", "3"]


def test_user_facing_route_failures_keep_localizable_descriptors(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: ([], "tmux discovery failed"))
    webapp.refresh_sessions = lambda maintenance=False: []
    try:
        failures = [
            webapp.record_stats_history_payload("invalid"),
            webapp.client_event({"session": "missing", "type": "test", "message": "message"}),
            webapp.acknowledge_attention({}),
            webapp.build_auto_approve_status("missing"),
            webapp.cancel_yoagent_chat(""),
            webapp.tmux_session_exists_payload("1"),
        ]
    finally:
        webapp.control_server.stop()

    assert all(status != HTTPStatus.OK for _payload, status in failures)
    assert all(payload.get("user_message", {}).get("key") for payload, _status in failures)
    assert failures[0][0]["user_message"] == {
        "key": "request.error.object",
        "params": {"field": "payload"},
        "fallback": "payload must be an object",
    }
    assert failures[-1][0]["diagnostic"] == "tmux discovery failed"
    command_failure = app_module.tmux_command_failure_payload("1", "raw tmux stderr")
    assert command_failure["diagnostic"] == "raw tmux stderr"
    assert command_failure["user_message"]["key"] == "terminal.window.failed"
    assert command_failure["user_message"]["params"]["error"]["key"] == "common.requestFailed"


def test_session_files_memory_cache_is_bounded():
    webapp = app_module.TmuxWebtermApp([])
    try:
        for index in range(app_module.SESSION_FILES_CACHE_MAX_ITEMS + 3):
            webapp.set_session_files_memory_cache((index,), {"files": [index]}, HTTPStatus.OK)
        assert len(webapp.session_files_cache) == app_module.SESSION_FILES_CACHE_MAX_ITEMS
        assert (0,) not in webapp.session_files_cache
        assert (app_module.SESSION_FILES_CACHE_MAX_ITEMS + 2,) in webapp.session_files_cache
    finally:
        webapp.control_server.stop()


def test_client_event_watch_sleep_uses_next_due_preference(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(
            app_module,
            "settings_payload",
            lambda: {"settings": {"performance": {"server_event_poll_ms": 250}}},
        )
        webapp.client_event_next_file_poll_at = 100.5
        webapp.client_event_next_background_file_poll_at = 100.75
        webapp.client_event_next_signature_poll_at = 100.25
        webapp.client_event_next_auto_poll_at = 101.0
        webapp.client_event_next_attention_ack_poll_at = 100.6
        webapp.client_event_next_watched_pr_poll_at = 200.0
        assert webapp.client_event_watch_sleep_seconds(100.0) == pytest.approx(0.25)
        webapp.client_event_next_signature_poll_at = 0.0
        assert webapp.client_event_watch_sleep_seconds(100.0) == pytest.approx(0.25)
    finally:
        webapp.control_server.stop()


def test_timer_client_event_polls_initialize_without_initial_push(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    auto_payloads = [
        ({"sessions": {}, "version": 1}, HTTPStatus.OK),
        ({"sessions": {"1": {"enabled": True}}, "version": 2}, HTTPStatus.OK),
    ]
    tmux_payloads = [
        {"ok": True, "window_count": 0, "windows": [], "generated_at": 1.0},
        {"ok": True, "window_count": 1, "windows": [{"key": "1:0"}], "generated_at": 2.0},
    ]
    watched_payloads = [
        {"items": []},
        {"items": [{"repo": "owner/repo", "number": 1}]},
    ]
    monkeypatch.setattr(webapp, "refresh_auto_approve_cache_sync", lambda: auto_payloads.pop(0))
    monkeypatch.setattr(webapp, "tmux_signal_snapshot", lambda force=False: tmux_payloads.pop(0))
    monkeypatch.setattr(webapp, "watched_prs_payload", lambda: watched_payloads.pop(0))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.poll_auto_approve_client_event_once() == []
        assert webapp.poll_tmux_signals_client_event_once() == []
        assert webapp.poll_watched_prs_client_event_once() == []
        assert events == []
        assert webapp.poll_auto_approve_client_event_once() == ["auto_approve_changed"]
        assert webapp.poll_tmux_signals_client_event_once() == ["tmux_signals_changed"]
        assert webapp.poll_watched_prs_client_event_once() == ["watched_prs_changed"]
    finally:
        webapp.control_server.stop()

    assert [event_type for event_type, _payload in events] == ["auto_approve_changed", "tmux_signals_changed", "watched_prs_changed"]
    assert events[0][1]["refresh"] is True
    assert "signature" in events[0][1]
    assert "data" not in events[0][1]


def test_timer_client_event_polls_ignore_volatile_status_changes(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    auto_payloads = [
        (
            {
                "sessions": {
                    "1": {
                        "enabled": False,
                        "screen": {"status_elapsed_seconds": 1.0, "status_line": "working 1s", "status_marker": "a"},
                        "agent_windows": [{"state": "working", "observed_ts": 1.0, "working_elapsed_seconds": 1.0, "status_tokens": 100, "last_active_ts": 1.0, "idle_since": 1.0}],
                    }
                }
            },
            HTTPStatus.OK,
        ),
        (
            {
                "sessions": {
                    "1": {
                        "enabled": False,
                        "screen": {"status_elapsed_seconds": 2.0, "status_line": "working 2s", "status_marker": "b"},
                        "agent_windows": [{"state": "working", "observed_ts": 2.0, "working_elapsed_seconds": 2.0, "status_tokens": 200, "last_active_ts": 2.0, "idle_since": 2.0}],
                    }
                }
            },
            HTTPStatus.OK,
        ),
        ({"sessions": {"1": {"enabled": True, "screen": {}, "agent_windows": [{"state": "working"}]}}}, HTTPStatus.OK),
    ]
    tmux_payloads = [
        {"ok": True, "window_count": 1, "windows": [{"key": "1:0", "activity_age_seconds": 1.0, "activity_ts": 10, "panes": [{"pane_id": "%1", "title": "a work", "history_bytes": 10, "history_size": 1}]}], "generated_at": 1.0},
        {"ok": True, "window_count": 1, "windows": [{"key": "1:0", "activity_age_seconds": 2.0, "activity_ts": 11, "panes": [{"pane_id": "%1", "title": "b work", "history_bytes": 20, "history_size": 2}]}], "generated_at": 2.0},
        {"ok": True, "window_count": 1, "windows": [{"key": "1:0", "active": True, "activity_age_seconds": 3.0, "activity_ts": 12, "panes": [{"pane_id": "%1", "title": "c work", "history_bytes": 30, "history_size": 3}]}], "generated_at": 3.0},
    ]
    monkeypatch.setattr(webapp, "refresh_auto_approve_cache_sync", lambda: auto_payloads.pop(0))
    monkeypatch.setattr(webapp, "tmux_signal_snapshot", lambda force=False: tmux_payloads.pop(0))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.poll_auto_approve_client_event_once() == []
        assert webapp.poll_tmux_signals_client_event_once() == []
        assert webapp.poll_auto_approve_client_event_once() == []
        assert webapp.poll_tmux_signals_client_event_once() == []
        assert webapp.poll_auto_approve_client_event_once() == ["auto_approve_changed"]
        assert webapp.poll_tmux_signals_client_event_once() == ["tmux_signals_changed"]
    finally:
        webapp.control_server.stop()

    assert [event_type for event_type, _payload in events] == ["auto_approve_changed", "tmux_signals_changed"]
    assert events[0][1]["refresh"] is True
    assert "data" not in events[0][1]


def test_stable_signature_payload_drops_volatile_keys_recursively():
    webapp = app_module.TmuxWebtermApp([])
    try:
        first = {
            "ok": True,
            "generated_at": "first",
            "nested": {"generated_ts": 1.0, "compute_ms": 10.0, "value": "same"},
            "items": [{"activity_ts": 100.0, "name": "same"}],
        }
        second = {
            "ok": True,
            "generated_at": "second",
            "nested": {"generated_ts": 2.0, "compute_ms": 20.0, "value": "same"},
            "items": [{"activity_ts": 200.0, "name": "same"}],
        }
        expected = {"ok": True, "nested": {"value": "same"}, "items": [{"name": "same"}]}

        assert webapp.stable_signature_payload(first) == expected
        assert webapp.stable_signature_payload(second) == expected
        assert webapp.stable_client_event_payload_signature(first) == webapp.stable_client_event_payload_signature(second)
    finally:
        webapp.control_server.stop()


def test_activity_summary_ready_signature_ignores_generated_timestamps(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    payloads = [
        {"locale": "en", "generated_at": "first", "generated_ts": 1.0, "global": {"headline": "same"}, "sessions": {}},
        {"locale": "en", "generated_at": "second", "generated_ts": 2.0, "global": {"headline": "same"}, "sessions": {}},
    ]
    monkeypatch.setattr(webapp, "client_watch_state_snapshot", lambda: ([], [], {"visible": True, "locale": "en", "hours": 24}))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: payloads.pop(0))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.publish_activity_summary_ready_events(trigger="refresh") == ["activity_summary_ready"]
        assert webapp.publish_activity_summary_ready_events(trigger="refresh") == []
    finally:
        webapp.control_server.stop()

    assert [event_type for event_type, _payload in events] == ["activity_summary_ready"]


def test_tmux_signal_event_publishes_changed_window_patch(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    payloads = [
        {"ok": True, "window_count": 2, "windows": [{"session": "1", "window_index": 0, "active": True}, {"session": "1", "window_index": 1, "active": False}], "generated_at": 1.0},
        {"ok": True, "window_count": 2, "windows": [{"session": "1", "window_index": 0, "active": False}, {"session": "1", "window_index": 1, "active": True}], "generated_at": 2.0},
    ]
    monkeypatch.setattr(webapp, "tmux_signal_snapshot", lambda force=False: payloads.pop(0))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.poll_tmux_signals_client_event_once() == []
        assert webapp.poll_tmux_signals_client_event_once() == ["tmux_signals_changed"]
    finally:
        webapp.control_server.stop()

    assert events == [("tmux_signals_changed", {"patch": True, "windows": [{"session": "1", "window_index": 0, "active": False}, {"session": "1", "window_index": 1, "active": True}], "removed_window_keys": [], "window_count": 2, "ok": True, "generated_at": 2.0, "compute_ms": None})]


def test_tmux_signal_event_publishes_removed_window_origin(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    payloads = [
        {"ok": True, "window_count": 2, "windows": [{"session": "1", "window_index": 0}, {"session": "1", "window_index": 1}], "generated_at": 10.0},
        {"ok": True, "window_count": 1, "windows": [{"session": "1", "window_index": 0}], "generated_at": 10.4},
    ]
    monkeypatch.setattr(webapp, "tmux_signal_snapshot", lambda force=False: payloads.pop(0))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.poll_tmux_signals_client_event_once() == []
        webapp.handle_tmux_signal_event({"type": "pane-exited", "time": 10.25})
        assert webapp.poll_tmux_signals_client_event_once() == ["tmux_signals_changed"]
    finally:
        webapp.control_server.stop()

    assert events == [("tmux_signals_changed", {
        "patch": True,
        "windows": [],
        "removed_window_keys": ["1:1"],
        "window_count": 1,
        "ok": True,
        "generated_at": 10.4,
        "compute_ms": None,
        "removed_window_event_at": 10.25,
        "removed_window_event_type": "pane-exited",
    })]


def test_tmux_signal_full_snapshot_keeps_removed_window_origin(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    payloads = [
        {"ok": True, "window_count": 2, "pane_count": 2, "windows": [{"session": "1", "window_index": 0}, {"session": "1", "window_index": 1}], "generated_at": 20.0},
        {"ok": True, "window_count": 1, "pane_count": 1, "windows": [{"session": "1", "window_index": 0}], "generated_at": 20.4},
    ]
    monkeypatch.setattr(webapp, "tmux_signal_snapshot", lambda force=False: payloads.pop(0))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.poll_tmux_signals_client_event_once() == []
        webapp.handle_tmux_signal_event({"type": "pane-died", "time": 20.1})
        assert webapp.poll_tmux_signals_client_event_once() == ["tmux_signals_changed"]
    finally:
        webapp.control_server.stop()

    assert events == [("tmux_signals_changed", {"data": {
        "ok": True,
        "window_count": 1,
        "pane_count": 1,
        "windows": [{"session": "1", "window_index": 0}],
        "generated_at": 20.4,
        "removed_window_keys": ["1:1"],
        "removed_window_event_at": 20.1,
        "removed_window_event_type": "pane-died",
    }})]


def test_tmux_signal_event_does_not_force_auto_approve_poll():
    webapp = app_module.TmuxWebtermApp([])
    try:
        webapp.client_event_next_auto_poll_at = 123.0
        webapp.client_event_next_tmux_signal_poll_at = 456.0
        webapp.tmux_signal_cache.set("snapshot", {"ok": True})

        webapp.handle_tmux_signal_event({"event": "pane_changed"})

        assert webapp.client_event_next_auto_poll_at == pytest.approx(123.0)
        assert webapp.client_event_next_tmux_signal_poll_at == pytest.approx(0.0)
        assert webapp.tmux_signal_cache.get_or_miss("snapshot") is app_module.CACHE_MISS
        assert webapp.client_watch_wake_event.is_set()
    finally:
        webapp.control_server.stop()


def test_save_settings_active_color_syncs_existing_tmux_theme(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    calls = []
    events = []

    monkeypatch.setattr(
        app_module,
        "save_settings",
        lambda patch: {"settings": {"appearance": {"active_color": "blue"}}, "mtime_ns": 123},
    )
    monkeypatch.setattr(
        app_module,
        "apply_tmux_theme_color_to_existing",
        lambda color, runner: calls.append((color, runner)) or {"applied": True, "errors": []},
    )
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **kwargs: events.append((event_type, payload or {}, kwargs)))
    try:
        payload = webapp.save_settings({"appearance": {"active_color": "blue"}})
    finally:
        webapp.control_server.stop()

    assert payload["settings"]["appearance"]["active_color"] == "blue"
    assert calls == [("blue", app_module.tmux)]
    assert webapp.tmux_theme_color == "blue"
    assert events[0][0] == "settings_changed"
    assert webapp.client_watch_wake_event.is_set()


def test_create_next_session_applies_saved_active_color_to_new_tmux(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp([])
    refresh_calls = []
    tmux_calls = []

    def fake_refresh_sessions(maintenance=True):
        refresh_calls.append(maintenance)
        if len(refresh_calls) >= 2:
            webapp.sessions = ["1"]
        return []

    def fake_tmux(args, timeout=5.0):
        tmux_calls.append((args, timeout))
        return app_module.subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(webapp, "refresh_sessions", fake_refresh_sessions)
    monkeypatch.setattr(app_module, "available_agent_commands", lambda: ["term"])
    monkeypatch.setattr(app_module, "session_workdir", lambda session: tmp_path)
    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"appearance": {"active_color": "purple"}}})
    monkeypatch.setattr(app_module, "tmux", fake_tmux)
    try:
        payload, status = webapp.create_next_session("term")
    finally:
        webapp.control_server.stop()

    commands = [args for args, _timeout in tmux_calls]
    assert status == HTTPStatus.OK
    assert payload["session"] == "1"
    assert commands[0][:6] == ["new-session", "-d", "-s", "1", "-c", str(tmp_path)]
    assert ["set-option", "-t", "1:", "status", "off"] in commands
    assert ["set-option", "-t", "1:", "status-style", "bg=#7c3aed,fg=#ffffff"] in commands
    assert ["set-window-option", "-t", "1:", "pane-active-border-style", "fg=#7c3aed"] in commands
    assert commands[-1] == ["refresh-client", "-S"]
    assert webapp.tmux_theme_color == "purple"

def test_cycle_tmux_status_mode_reads_and_updates_one_session(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    tmux_calls = []

    def fake_tmux(args, timeout=5.0):
        tmux_calls.append((args, timeout))
        if args[-1] == "status":
            return app_module.subprocess.CompletedProcess(args, 0, "on\n", "")
        if args[-1] == "status-position":
            return app_module.subprocess.CompletedProcess(args, 0, "top\n", "")
        return app_module.subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(app_module, "tmux", fake_tmux)
    try:
        payload, status = webapp.cycle_tmux_status_mode("1")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload == {"session": "1", "status": "bottom"}
    commands = [args for args, _timeout in tmux_calls]
    assert commands == [
        ["show-options", "-A", "-t", "1:", "-v", "status"],
        ["show-options", "-A", "-t", "1:", "-v", "status-position"],
        ["set-option", "-t", "1:", "status", "on"],
        ["set-option", "-t", "1:", "status-position", "bottom"],
    ]

def test_cycle_tmux_status_mode_turns_bottom_off(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    tmux_calls = []

    def fake_tmux(args, timeout=5.0):
        tmux_calls.append((args, timeout))
        stdout = "on\n" if args[-1] == "status" else "bottom\n" if args[-1] == "status-position" else ""
        return app_module.subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(app_module, "tmux", fake_tmux)
    try:
        payload, status = webapp.cycle_tmux_status_mode("1")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload == {"session": "1", "status": "none"}
    assert [args for args, _timeout in tmux_calls][-1] == ["set-option", "-t", "1:", "status", "off"]


def test_start_client_event_watcher_defers_expensive_timer_polls(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    started = []

    class FakeThread:
        def __init__(self, target, name=None, daemon=None):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            started.append(self.name)

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(app_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(webapp, "server_auto_approve_event_poll_seconds", lambda: 10.0)
    monkeypatch.setattr(webapp, "server_attention_ack_event_poll_seconds", lambda: 12.0)
    monkeypatch.setattr(webapp, "server_tmux_signal_event_poll_seconds", lambda: 15.0)
    monkeypatch.setattr(webapp, "start_tmux_signal_event_watcher", lambda: True)
    try:
        webapp.start_client_event_watcher()
        assert started == ["client-event-watch"]
        assert webapp.client_event_next_auto_poll_at == pytest.approx(110.0)
        assert webapp.client_event_next_attention_ack_poll_at == pytest.approx(112.0)
        assert webapp.client_event_next_tmux_signal_poll_at == pytest.approx(115.0)
    finally:
        webapp.stop_client_event_watcher()
        webapp.control_server.stop()


@pytest.mark.parametrize("method_name", ["events_payload", "search_payload", "auto_approve_status"])
def test_session_scoped_endpoints_refresh_before_unknown_session_guard(monkeypatch, method_name):
    webapp = app_module.TmuxWebtermApp(["old"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["new"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    monkeypatch.setattr(webapp, "auto_approve_session_status", lambda session, **_kwargs: {"target": session})
    try:
        if method_name == "search_payload":
            payload, status = webapp.search_payload("", session="new")
        else:
            payload, status = getattr(webapp, method_name)("new")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["session" if method_name != "auto_approve_status" else "target"] == "new"


def test_auto_approve_roster_uses_live_pane_working_signal(monkeypatch):
    # #28: the roster's working/idle signal comes from the LIVE pane (a cheap visible-only capture),
    # not transcript recency, while still discovering once and skipping the expensive hybrid prompt fan-out.
    info5 = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    info6 = SessionInfo(
        session="6",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="6",
                kind="codex",
                pid=123,
                pane_target="6:1.0",
                command="codex",
                cwd=None,
                status=None,
                session_id=None,
                transcript=None,
                error=None,
            )
        ],
    )
    discover_calls = []
    capture_calls = []
    pane_text = {"5": "working pane", "6": "idle pane", "6:1.0": "approval pane"}
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["5", "6"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: (discover_calls.append(tuple(sessions)) or {"5": info5, "6": info6}, []))

    def fake_capture(session, *_args, **kwargs):
        capture_calls.append((session, kwargs.get("visible_only")))
        return pane_text.get(session, "")

    monkeypatch.setattr(app_module, "tmux_capture_pane", fake_capture)
    screen_calls = []

    def fake_screen_state(text, **kwargs):
        screen_calls.append((text, kwargs.get("pane_target")))
        return {"key": "approval" if text == "approval pane" else "working" if text == "working pane" else "idle", "text": text}

    monkeypatch.setattr(app_module, "agent_screen_state", fake_screen_state)
    monkeypatch.setattr(
        app_module,
        "approval_prompt_state",
        lambda text: {"visible": text == "approval pane", "type": "bash" if text == "approval pane" else "", "text": "Do you want to proceed?" if text == "approval pane" else "", "yes_selected": text == "approval pane", "action": ""},
    )
    monkeypatch.setattr(app_module, "hybrid_approval_prompt_state", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("roster must not run the prompt-detection fan-out")))
    monkeypatch.setattr(app_module, "auto_approve_lock_owner", lambda _session: None)
    webapp = app_module.TmuxWebtermApp(["5", "6"])
    monkeypatch.setattr(webapp, "auto_approve_capture_allowed_for_target", lambda _target: True)
    discover_calls.clear()
    capture_calls.clear()
    screen_calls.clear()
    try:
        payload, status = webapp.auto_approve_status()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert discover_calls == [("5", "6")]  # discovered once for the whole roster, not per session
    assert {session for session, _visible in capture_calls} == {"5", "6:1.0"}
    assert screen_calls == [("working pane", "5"), ("approval pane", "6:1.0")]
    assert all(visible_only is True for _session, visible_only in capture_calls)  # cheap visible-only capture only
    assert payload["sessions"]["5"]["screen"]["key"] == "working"  # live working pane spins
    assert payload["sessions"]["6"]["screen"]["key"] == "approval"  # pending approval lights the roster
    assert payload["sessions"]["5"]["prompt"]["visible"] is False  # no live prompt fan-out in the roster
    assert payload["sessions"]["6"]["prompt"]["visible"] is True


def test_auto_approve_payload_includes_agent_window_statuses(monkeypatch, tmp_path):
    pane0 = PaneInfo(
        session="5",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%10",
        target="%10",
        current_path="/repo/claude",
        command="claude",
        active=True,
        window_active=True,
        title="claude",
        pid=10,
        process_label="claude",
        process_label_pid=10,
    )
    pane1 = PaneInfo(
        session="5",
        window="1",
        window_name="codex",
        pane="0",
        pane_id="%11",
        target="%11",
        current_path="/repo/codex",
        command="codex",
        active=True,
        window_active=False,
        title="codex",
        pid=11,
        process_label="codex",
        process_label_pid=11,
    )
    info = SessionInfo(
        session="5",
        panes=[pane0, pane1],
        selected_pane=pane0,
        agents=[
            AgentInfo("5", "claude", 10, "%10", "claude", "/repo/claude", "running", "claude-id", str(tmp_path / "claude.jsonl"), None),
            AgentInfo("5", "codex", 11, "%11", "codex", "/repo/codex", "running", "codex-id", str(tmp_path / "codex.jsonl"), None),
        ],
    )
    monkeypatch.setattr(app_module, "ACTIVITY_PATH", tmp_path / "activity.json")
    monkeypatch.setattr(app_module, "ACTIVITY_HEARTBEATS_PATH", tmp_path / "activity-heartbeats.jsonl")
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["5"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    capture_calls = []

    def fake_capture(target, *_args, **kwargs):
        capture_calls.append((target, kwargs.get("visible_only")))
        return "working screen" if target == "%10" else "idle screen"

    monkeypatch.setattr(app_module, "tmux_capture_pane", fake_capture)

    def fake_screen_state(text, **kwargs):
        if kwargs.get("pane_target") == "%10":
            return {"key": "working", "text": "agent is working", "status_elapsed_seconds": 158.0, "display_elapsed_seconds": 3720.0}
        return {"key": "idle", "text": text}

    monkeypatch.setattr(app_module, "agent_screen_state", fake_screen_state)
    monkeypatch.setattr(app_module, "auto_approve_lock_owner", lambda _session: None)

    git_calls = []

    def fake_git_inventory(cwd):
        git_calls.append(str(cwd))
        root = str(cwd)
        return {
            "root": root,
            "branch": f"{Path(root).name}-branch",
            "head": "abc123 test head",
            "ahead": 1,
            "behind": 0,
            "dirty_count": 2 if "claude" in root else 0,
        }

    monkeypatch.setattr(app_module, "git_inventory", fake_git_inventory)
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.cached_session_files_payload_for_info = lambda _info: {
        "files": [
            {"repo": "/repo/claude-touched", "abs_path": "/repo/claude-touched/app.py", "mtime": 20, "status": "M", "agent_windows": [{"kind": "claude", "window": "0", "window_index": 0, "pane": "0", "pane_target": "%10"}]},
            {"repo": "/repo/codex-touched", "abs_path": "/repo/codex-touched/app.py", "mtime": 10, "status": "M", "agent_windows": [{"kind": "codex", "window": "1", "window_index": 1, "pane": "0", "pane_target": "%11"}]},
        ]
    }
    webapp.activity_ledger.heartbeat("5", "1", ts=1000.0, byte_count=1)
    webapp.activity_ledger.note_agent_active("5", "1", ts=1010.0)
    monkeypatch.setattr(webapp, "auto_approve_capture_allowed_for_target", lambda _target: True)
    capture_calls.clear()
    try:
        payload, status = webapp.auto_approve_status()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    agent_windows = payload["sessions"]["5"]["agent_windows"]
    by_kind = {row["kind"]: row for row in agent_windows}
    assert [row["kind"] for row in agent_windows] == ["claude", "codex"]
    assert by_kind["claude"]["state"] == "working"
    assert by_kind["claude"]["working_elapsed_seconds"] == 3720.0
    assert by_kind["claude"]["pid"] == 10
    assert "active" not in by_kind["claude"]
    assert by_kind["claude"]["current"] is True
    assert by_kind["claude"]["window_active"] is True
    assert by_kind["claude"]["paths"] == []
    assert by_kind["claude"]["path_entries"] == []
    assert by_kind["claude"]["git"] is None
    assert by_kind["codex"]["state"] == "idle"
    assert by_kind["codex"]["idle_since"] == 1010.0
    assert by_kind["codex"]["pid"] == 11
    assert "active" not in by_kind["codex"]
    assert by_kind["codex"]["current"] is False
    assert by_kind["codex"]["window_active"] is False
    assert by_kind["codex"]["paths"] == []
    assert by_kind["codex"]["path_entries"] == []
    assert by_kind["codex"]["git"] is None
    assert capture_calls == [("%10", True), ("%11", True)]
    assert git_calls == []


def test_agent_window_status_payloads_use_real_run_captures_without_transcripts(monkeypatch, tmp_path):
    claude_capture = yaml.safe_load((PROMOTED_CAPTURE_DIR / "working_visible_counter__claude-code-2.1.183_20260620.yaml").read_text(encoding="utf-8"))["raw_capture"]
    codex_capture = yaml.safe_load((PROMOTED_CAPTURE_DIR / "working_command_counter__codex-cli-0.141.0_20260620.yaml").read_text(encoding="utf-8"))["raw_capture"]
    pane0 = PaneInfo(
        session="mock",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%claude",
        target="%claude",
        current_path=str(tmp_path),
        command="python3",
        active=True,
        window_active=True,
        title="claude.py",
        pid=10,
        process_label="claude",
        process_label_pid=10,
    )
    pane1 = PaneInfo(
        session="mock",
        window="1",
        window_name="codex",
        pane="0",
        pane_id="%codex",
        target="%codex",
        current_path=str(tmp_path),
        command="python3",
        active=True,
        window_active=False,
        title="codex.py",
        pid=11,
        process_label="codex",
        process_label_pid=11,
    )
    info = SessionInfo(
        session="mock",
        panes=[pane0, pane1],
        selected_pane=pane0,
        agents=[
            AgentInfo("mock", "claude", 10, "%claude", "python3 tools/claude.py --mock", str(tmp_path), None, None, None, "mock no transcript"),
            AgentInfo("mock", "codex", 11, "%codex", "python3 tools/codex.py --mock", str(tmp_path), None, None, None, "mock no transcript"),
        ],
    )
    captures = {"%claude": claude_capture, "%codex": codex_capture}
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, **_kwargs: captures[target])

    before = time.time()
    webapp = app_module.TmuxWebtermApp(["mock"])
    try:
        rows = webapp.agent_window_status_payloads("mock", info=info, discovered_sessions={"mock": info})
    finally:
        webapp.control_server.stop()
    after = time.time()

    by_kind = {row["kind"]: row for row in rows}
    assert by_kind["claude"]["state"] == "working"
    assert by_kind["claude"]["working_elapsed_seconds"] == 11.0
    assert by_kind["claude"]["status_tokens"] == 471
    assert by_kind["codex"]["state"] == "working"
    assert by_kind["codex"]["working_elapsed_seconds"] == 0.0
    assert before <= by_kind["claude"]["observed_ts"] <= after
    assert before <= by_kind["codex"]["observed_ts"] <= after


def test_idle_current_agent_window_is_not_active(monkeypatch, tmp_path):
    pane = PaneInfo(
        session="2",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%20",
        target="%20",
        current_path="/repo/idle",
        command="claude",
        active=True,
        window_active=True,
        title="claude",
        pid=20,
        process_label="claude",
        process_label_pid=20,
    )
    info = SessionInfo(
        session="2",
        panes=[pane],
        selected_pane=pane,
        agents=[AgentInfo("2", "claude", 20, "%20", "claude", "/repo/idle", "idle", "claude-id", str(tmp_path / "claude.jsonl"), None)],
    )
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda _target, **_kwargs: "idle prompt")
    monkeypatch.setattr(app_module, "agent_screen_state", lambda _text, **_kwargs: {"key": "idle", "text": ""})

    webapp = app_module.TmuxWebtermApp(["2"])
    try:
        rows = webapp.agent_window_status_payloads("2", info=info, discovered_sessions={"2": info})
    finally:
        webapp.control_server.stop()

    assert len(rows) == 1
    assert rows[0]["state"] == "idle"
    assert "active" not in rows[0]
    assert rows[0]["current"] is True
    assert rows[0]["window_active"] is True


def test_agent_window_working_completion_gets_a_fresh_pause_timestamp(monkeypatch, tmp_path):
    pane = PaneInfo(
        session="2",
        window="0",
        window_name="codex",
        pane="0",
        pane_id="%20",
        target="%20",
        current_path="/repo/working",
        command="codex",
        active=True,
        window_active=True,
        title="codex",
        pid=20,
        process_label="codex",
        process_label_pid=20,
    )
    info = SessionInfo(
        session="2",
        panes=[pane],
        selected_pane=pane,
        agents=[AgentInfo("2", "codex", 20, "%20", "codex", "/repo/working", "idle", "codex-id", str(tmp_path / "codex.jsonl"), None)],
    )
    states = iter(({"key": "working", "text": "working"}, {"key": "idle", "text": "done"}, {"key": "idle", "text": "done"}))
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda _target, **_kwargs: "fixture")
    monkeypatch.setattr(app_module, "agent_screen_state", lambda _text, **_kwargs: next(states))
    webapp = app_module.TmuxWebtermApp(["2"])
    try:
        working = webapp.agent_window_status_payloads("2", info=info, discovered_sessions={"2": info})[0]
        completed = webapp.agent_window_status_payloads("2", info=info, discovered_sessions={"2": info})[0]
        still_completed = webapp.agent_window_status_payloads("2", info=info, discovered_sessions={"2": info})[0]
    finally:
        webapp.control_server.stop()
    assert working["state"] == "working"
    assert completed["state"] == "idle"
    assert completed["working_stopped_ts"] >= working["observed_ts"]
    assert still_completed["working_stopped_ts"] == completed["working_stopped_ts"]


def test_agent_window_idle_baseline_does_not_turn_historical_activity_into_completion():
    webapp = app_module.TmuxWebtermApp(["interview"])
    try:
        stopped_at = webapp.agent_window_working_stopped_ts("interview", "0", "%20", "codex", "idle", 200.0)
    finally:
        webapp.control_server.stop()
    assert stopped_at == 0.0


def test_auto_approve_fans_out_to_server_wide_agent_panes(monkeypatch):
    created_targets = []

    class FakeAutoApproveWorker:
        def __init__(self, target, **kwargs):
            self.target = target
            self.kwargs = kwargs
            self.stopped = False
            created_targets.append(target)

        def start(self):
            return True, None

        def alive(self):
            return not self.stopped

        def stop(self):
            self.stopped = True
            return True

        def status(self):
            return {
                "target": self.target,
                "enabled": self.alive(),
                "approved": 1 if self.target == "%11" else 2,
                "blocked": 0,
                "last_action": f"watching {self.target}",
            }

        def has_pending_prompt(self):
            return False

    signal_payload = {
        "ok": True,
        "agents": [
            {"session": "6", "target": "%11", "pane_id": "%11", "agent": "codex", "dead": False},
            {"session": "6", "target": "%12", "pane_id": "%12", "agent": "claude", "dead": False},
            {"session": "7", "target": "%21", "pane_id": "%21", "agent": "codex", "dead": False},
        ],
        "windows": [],
    }
    monkeypatch.setattr(app_module, "AutoApproveWorker", FakeAutoApproveWorker)
    monkeypatch.setattr(app_module, "tmux_has_exact_session", lambda session: session == "6")
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(webapp, "tmux_signal_snapshot", lambda force=False: signal_payload)
    monkeypatch.setattr(webapp, "prompt_and_screen_status", lambda *args, **kwargs: (app_module.normalized_prompt_state(), {"key": "idle", "text": ""}))
    try:
        payload, status = webapp.set_auto_approve("6", True, persist=False)
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert created_targets == ["%11", "%12"]
    assert set(webapp.auto_workers) == {"%11", "%12"}
    assert webapp.auto_worker_sessions == {"%11": "6", "%12": "6"}
    assert payload["target"] == "6"
    assert payload["worker_targets"] == ["%11", "%12"]
    assert payload["approved"] == 3
    assert payload["enabled"] is True


def test_prompt_and_screen_status_skips_idle_tmux_signal_capture(monkeypatch):
    capture_calls = []
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda *args, **kwargs: capture_calls.append((args, kwargs)) or "should not capture")
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(webapp, "auto_approve_capture_allowed_for_target", lambda _target: False)
    try:
        prompt, screen = webapp.prompt_and_screen_status("6", capture_pane=False)
    finally:
        webapp.control_server.stop()

    assert prompt["visible"] is False
    assert screen == {"key": "idle", "text": "tmux activity quiet"}
    assert capture_calls == []


def test_tmux_signal_window_recently_active_resolves_pane_targets(monkeypatch):
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    webapp = app_module.TmuxWebtermApp(["6"])
    payload = {
        "windows": [
            {
                "key": "6:0",
                "session": "6",
                "active": True,
                "activity_ts": 800,
                "activity_flag": False,
                "panes": [{"target": "%11", "pane_id": "%11"}],
            },
            {
                "key": "6:1",
                "session": "6",
                "active": False,
                "activity_ts": 990,
                "activity_flag": False,
                "panes": [{"target": "%12", "pane_id": "%12"}],
            },
        ],
    }
    try:
        assert webapp.tmux_signal_window_recently_active("%11", payload=payload, threshold_seconds=120.0) is False
        assert webapp.tmux_signal_window_recently_active("%12", payload=payload, threshold_seconds=120.0) is True
    finally:
        webapp.control_server.stop()


def test_tmux_recency_ordered_sessions_uses_session_and_window_activity(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["1", "2", "3", "4"])
    payload = {
        "sessions": {
            "1": {"activity_ts": 20, "last_attached_ts": 0},
            "2": {"activity_ts": 0, "last_attached_ts": 90},
            "3": {"activity_ts": 0, "last_attached_ts": 0},
        },
        "windows": [
            {"session": "3", "activity_ts": 120, "session_activity_ts": 0, "session_last_attached_ts": 0},
            {"session": "outside", "activity_ts": 999},
        ],
    }
    try:
        assert webapp.tmux_recency_ordered_sessions(payload=payload) == ["3", "2", "1", "4"]
    finally:
        webapp.control_server.stop()


def test_activity_summary_payload_prioritizes_tmux_recent_sessions(monkeypatch):
    infos = {
        name: SessionInfo(session=name, panes=[], selected_pane=None, agents=[])
        for name in ("1", "2", "3")
    }
    calls = []
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: (infos, []))
    monkeypatch.setattr(app_module, "session_project_metadata", lambda info, cache, allow_network=False: {})

    def fake_build_summary(info, project, files, locale="en"):
        calls.append((info.session, locale))
        return {
            "session": info.session,
            "agent": "",
            "active": False,
            "repos": [],
            "files": {"count": 0, "added": 0, "removed": 0},
            "lines": [],
        }

    monkeypatch.setattr(app_module, "build_session_activity_summary", fake_build_summary)
    webapp = app_module.TmuxWebtermApp(["1", "2", "3"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    webapp.cached_session_files_payload_for_info = lambda info, hours=24.0: {"files": [], "repos": [], "errors": []}
    webapp.tmux_signal_snapshot = lambda force=False: {
        "sessions": {
            "1": {"activity_ts": 10, "last_attached_ts": 0},
            "2": {"activity_ts": 100, "last_attached_ts": 0},
            "3": {"activity_ts": 0, "last_attached_ts": 0},
        },
        "windows": [{"session": "3", "activity_ts": 200}],
    }
    try:
        payload = webapp.activity_summary_payload()
    finally:
        webapp.control_server.stop()

    assert calls[-3:] == [("3", "en"), ("2", "en"), ("1", "en")]
    assert payload["session_order"] == ["3", "2", "1"]


def test_activity_summary_payload_all_scope_includes_visible_tmux_sessions(monkeypatch):
    infos = {
        name: SessionInfo(session=name, panes=[], selected_pane=None, agents=[])
        for name in ("1", "external")
    }
    discovered = []
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1", "external"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: discovered.append(list(sessions)) or ({name: infos[name] for name in sessions if name in infos}, []))
    monkeypatch.setattr(app_module, "session_project_metadata", lambda info, cache, allow_network=False: {})
    monkeypatch.setattr(app_module, "build_session_activity_summary", lambda info, project, files, locale="en": {"session": info.session, "agent": "", "active": False, "repos": [], "files": {"count": 0, "added": 0, "removed": 0}, "lines": []})
    webapp = app_module.TmuxWebtermApp(["1"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    summary_hours = []

    def fake_cached_session_files_payload_for_info(info, hours=24.0):
        summary_hours.append(hours)
        return {"files": [], "repos": [], "errors": []}

    webapp.cached_session_files_payload_for_info = fake_cached_session_files_payload_for_info
    webapp.tmux_signal_snapshot = lambda force=False: {
        "sessions": {
            "1": {"activity_ts": 10, "last_attached_ts": 0},
            "external": {"activity_ts": 100, "last_attached_ts": 0},
        },
        "windows": [],
    }
    try:
        configured = webapp.activity_summary_payload()
        all_sessions = webapp.activity_summary_payload(session_scope="all", hours=336)
    finally:
        webapp.control_server.stop()

    assert ["1"] in discovered
    assert ["1", "external"] in discovered
    assert configured["session_order"] == ["1"]
    assert configured["session_scope"] == "configured"
    assert all_sessions["session_order"] == ["external", "1"]
    assert all_sessions["session_scope"] == "all"
    assert all_sessions["session_file_hours"] == 336.0
    assert set(all_sessions["sessions"]) == {"1", "external"}
    assert summary_hours[-2:] == [336.0, 336.0]


def test_activity_summary_payload_batches_recent_events_for_multiple_sessions(monkeypatch):
    infos = {
        name: SessionInfo(session=name, panes=[], selected_pane=None, agents=[])
        for name in ("1", "2", "3")
    }
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({name: infos[name] for name in sessions if name in infos}, []))
    monkeypatch.setattr(app_module, "session_project_metadata", lambda info, cache, allow_network=False: {})
    monkeypatch.setattr(app_module, "build_session_activity_summary", lambda info, project, files, locale="en": {"session": info.session, "agent": "", "active": False, "repos": [], "files": {"count": 0, "added": 0, "removed": 0}, "lines": []})
    webapp = app_module.TmuxWebtermApp(["1", "2", "3"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    webapp.cached_session_files_payload_for_info = lambda info, hours=24.0: {"files": [], "repos": [], "errors": []}
    webapp.tmux_recency_ordered_sessions = lambda session_names=None, payload=None: ["3", "2", "1"]
    tail_many_calls = []

    def fake_tail_many(sessions, limit=100):
        tail_many_calls.append((tuple(sessions), limit))
        return {
            session: [{"session": session, "message": f"{session} recent"}]
            for session in sessions
        }

    def fail_tail(*_args, **_kwargs):
        raise AssertionError("activity summary must batch recent events with tail_many")

    webapp.event_log.tail_many = fake_tail_many
    webapp.event_log.tail = fail_tail
    try:
        payload = webapp.activity_summary_payload()
    finally:
        webapp.control_server.stop()

    assert tail_many_calls == [(("3", "2", "1"), 5)]
    assert payload["session_order"] == ["3", "2", "1"]
    assert payload["session_info"]["3"]["recent_events"][0]["message"] == "3 recent"
    assert payload["session_info"]["2"]["recent_events"][0]["message"] == "2 recent"
    assert payload["session_info"]["1"]["recent_events"][0]["message"] == "1 recent"


def test_activity_payload_and_summary_tick_prioritize_tmux_recent_sessions(monkeypatch):
    agent_infos = {
        name: SessionInfo(
            session=name,
            panes=[],
            selected_pane=None,
            agents=[
                AgentInfo(
                    session=name,
                    kind="codex",
                    pid=100 + index,
                    pane_target=f"{name}:0.0",
                    command="codex",
                    cwd="/repo",
                    status="running",
                    session_id=f"sid-{name}",
                    transcript=None,
                    error=None,
                )
            ],
        )
        for index, name in enumerate(("1", "2"))
    }
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: (agent_infos, []))
    webapp.tmux_signal_snapshot = lambda force=False: {
        "sessions": {
            "1": {"activity_ts": 50, "last_attached_ts": 0},
            "2": {"activity_ts": 150, "last_attached_ts": 0},
        },
        "windows": [],
    }
    warmed_sessions = []

    def fake_cached_session_files_payloads(infos, hours=24.0):
        warmed_sessions.append(list(infos))
        return {session: {"files": [], "repos": []} for session in infos}

    webapp.cached_session_files_payloads_for_infos = fake_cached_session_files_payloads
    try:
        activity = webapp.build_activity_payload()
        updated = []

        def fake_update_summary(session, info, settings=None, force=False):
            updated.append(session)
            return {"session": session, "updated": False, "reason": "test"}

        webapp.update_yoagent_session_summary = fake_update_summary
        tick = webapp.tick_yoagent_session_summaries({"backend": "codex", "invocation": "cli"})
    finally:
        webapp.control_server.stop()

    assert warmed_sessions == [["2", "1"]]
    assert [row["session"] for row in activity["agents"]] == ["2", "1"]
    assert updated == ["2", "1"]
    assert [item["session"] for item in tick["skipped"]] == ["2", "1"]


def test_activity_payload_all_scope_uses_visible_tmux_sessions(monkeypatch):
    agent_infos = {
        name: SessionInfo(
            session=name,
            panes=[],
            selected_pane=None,
            agents=[
                AgentInfo(
                    session=name,
                    kind="codex",
                    pid=200 + index,
                    pane_target=f"{name}:0.0",
                    command="codex",
                    cwd="/repo",
                    status="running",
                    session_id=f"sid-{name}",
                    transcript=None,
                    error=None,
                )
            ],
        )
        for index, name in enumerate(("1", "external"))
    }
    discovered = []
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1", "external"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: discovered.append(list(sessions)) or ({name: agent_infos[name] for name in sessions if name in agent_infos}, []))
    webapp = app_module.TmuxWebtermApp(["1"])
    webapp.tmux_signal_snapshot = lambda force=False: {
        "sessions": {
            "1": {"activity_ts": 10, "last_attached_ts": 0},
            "external": {"activity_ts": 100, "last_attached_ts": 0},
        },
        "windows": [],
    }
    activity_hours = []

    def fake_cached_session_files_payloads_for_infos(infos, hours=24.0):
        activity_hours.append(hours)
        return {session: {"files": [], "repos": []} for session in infos}

    webapp.cached_session_files_payloads_for_infos = fake_cached_session_files_payloads_for_infos
    try:
        configured = webapp.build_activity_payload()
        all_sessions = webapp.build_activity_payload(session_scope="all", hours=0.5)
    finally:
        webapp.control_server.stop()

    assert ["1"] in discovered
    assert ["1", "external"] in discovered
    assert [row["session"] for row in configured["agents"]] == ["1"]
    assert configured["session_scope"] == "configured"
    assert [row["session"] for row in all_sessions["agents"]] == ["external", "1"]
    assert all_sessions["session_scope"] == "all"
    assert all_sessions["session_file_hours"] == 0.5
    assert activity_hours[-1] == 0.5


def test_recent_agents_payload_filters_paths_by_agent_window():
    panes = [
        PaneInfo(session="5", window="0", pane="0", pane_id="%50", target="5:0.0", current_path="/repo/codex", command="codex", active=True, window_active=True, title="", pid=50, process_label="codex"),
        PaneInfo(session="5", window="1", pane="0", pane_id="%51", target="5:1.0", current_path="/repo/claude", command="claude", active=True, window_active=False, title="", pid=51, process_label="claude"),
    ]
    info = SessionInfo(
        session="5",
        panes=panes,
        selected_pane=panes[0],
        agents=[
            AgentInfo("5", "codex", 50, "5:0.0", "codex", "/repo/codex", "running", "codex-sid", None, None),
            AgentInfo("5", "claude", 51, "5:1.0", "claude", "/repo/claude", "running", "claude-sid", None, None),
        ],
    )
    files_payload = {
        "files": [
            {"repo": "/repo/codex", "abs_path": "/repo/codex/app.py", "mtime": 20, "status": "M", "agent_windows": [{"kind": "codex", "window": "0", "window_index": 0, "pane": "0", "pane_target": "5:0.0"}]},
            {"repo": "/repo/claude", "abs_path": "/repo/claude/app.py", "mtime": 10, "status": "M", "agent_windows": [{"kind": "claude", "window": "1", "window_index": 1, "pane": "0", "pane_target": "5:1.0"}]},
        ]
    }

    rows = app_module.build_recent_agents_payload({"5": info}, ["5"], session_files_by_session={"5": files_payload})
    by_target = {row["pane_target"]: row for row in rows}

    assert [item["path"] for item in by_target["5:0.0"]["recent_paths"]] == ["/repo/codex"]
    assert [item["path"] for item in by_target["5:1.0"]["recent_paths"]] == ["/repo/claude"]


def test_tmux_snapshot_bounds_and_skips_unchanged_history(monkeypatch):
    pane = PaneInfo(
        session="6",
        window="0",
        pane="0",
        pane_id="%11",
        target="%11",
        current_path="/repo/app",
        command="codex",
        active=True,
        window_active=True,
        title="codex",
        pid=1234,
    )
    info = SessionInfo(session="6", panes=[pane], selected_pane=pane, agents=[])
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"6": info}, []))
    calls = []

    def fake_tmux(args, timeout=0):
        calls.append((args, timeout))
        return SimpleNamespace(returncode=0, stdout="line one\nline two\n", stderr="")

    monkeypatch.setattr(app_module, "tmux", fake_tmux)
    signal_payload = {
        "windows": [{
            "key": "6:0",
            "session": "6",
            "active": True,
            "panes": [{"target": "%11", "pane_id": "%11", "active": True, "history_size": 12, "history_bytes": 120}],
        }],
    }
    webapp = app_module.TmuxWebtermApp(["6"])
    webapp.tmux_signal_snapshot = lambda force=False: signal_payload
    try:
        first, first_status = webapp.tmux_snapshot("6", 1000)
        second, second_status = webapp.tmux_snapshot("6", 1000)
        signal_payload["windows"][0]["panes"][0]["history_bytes"] = 121
        third, third_status = webapp.tmux_snapshot("6", 1000)
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert third_status == HTTPStatus.OK
    assert first["lines"] == 12
    assert first["history_size"] == 12
    assert first["history_bytes"] == 120
    assert first["unchanged"] is False
    assert second["unchanged"] is True
    assert second["text"] == ""
    assert third["history_bytes"] == 121
    assert [call[0] for call in calls] == [
        ["capture-pane", "-t", "%11", "-p", "-J", "-S", "-12"],
        ["capture-pane", "-t", "%11", "-p", "-J", "-S", "-12"],
    ]


def test_transcripts_payload_exposes_server_version(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    monkeypatch.setattr(app_module, "yolomux_client_revision", lambda: "client-rev-test")
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(webapp, "refresh_sessions", lambda *args, **kwargs: [])
    monkeypatch.setattr(webapp, "warm_metadata_cache_async", lambda sessions: None)
    monkeypatch.setattr(webapp, "start_transcripts_payload_refresh", lambda *args, **kwargs: False)
    try:
        payload = webapp.transcripts_payload()
    finally:
        webapp.control_server.stop()

    assert payload["server_version"] == app_module.YOLOMUX_VERSION
    assert payload["client_revision"] == "client-rev-test"
    assert payload["server_started_at"] == app_module.SERVER_STARTED_AT
    assert payload["server_uptime_seconds"] >= 0


def test_transcripts_payload_includes_indexed_repos_only_on_full_metadata(monkeypatch):
    indexed = [{"root": "/repo", "other_branches": {"branches": [{"name": "feature"}]}}]
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    monkeypatch.setattr(app_module, "indexed_repo_summaries", lambda cache=None, allow_network=False: indexed)
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(webapp, "refresh_sessions", lambda *args, **kwargs: [])
    monkeypatch.setattr(webapp, "warm_metadata_cache_async", lambda sessions: None)
    try:
        full = webapp.build_transcripts_payload()
        lightweight = webapp.build_transcripts_payload(lightweight=True)
    finally:
        webapp.control_server.stop()

    assert full["indexed_repos"] == indexed
    assert lightweight["indexed_repos"] == []


def test_transcripts_payload_returns_stale_cache_and_refreshes(monkeypatch):
    calls = []
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])

    def fake_discover(sessions):
        calls.append(len(calls) + 1)
        return {"5": info}, []

    monkeypatch.setattr(app_module, "discover_sessions", fake_discover)
    monkeypatch.setattr(app_module, "session_to_json", lambda info, cache, allow_network=False, include_metadata=True: {"session": info.session, "call": calls[-1], "metadata": include_metadata})
    monkeypatch.setattr(app_module, "agent_auth_status", lambda: {})
    webapp = app_module.TmuxWebtermApp(["5"])
    calls.clear()
    monkeypatch.setattr(webapp, "refresh_sessions", lambda *args, **kwargs: [])
    monkeypatch.setattr(webapp, "warm_metadata_cache_async", lambda sessions: None)
    webapp.start_transcripts_payload_refresh = lambda: (webapp.refresh_transcripts_payload_cache() or True)
    try:
        first = webapp.transcripts_payload(force=True)
        with webapp.transcripts_payload_cache_lock:
            stored_at, value = webapp.transcripts_payload_cache
            webapp.transcripts_payload_cache = (stored_at - app_module.TRANSCRIPTS_PAYLOAD_CACHE_SECONDS - 1.0, value)
        second = webapp.transcripts_payload()
        third = webapp.transcripts_payload()
    finally:
        webapp.control_server.stop()

    assert first["sessions"]["5"]["call"] == 1
    assert second["sessions"]["5"]["call"] == 1
    assert second["cache"]["hit"] is True
    assert second["cache"]["stale"] is True
    assert third["sessions"]["5"]["call"] == 2
    assert calls == [1, 2]


def test_transcripts_payload_cold_returns_lightweight_and_starts_full_refresh(monkeypatch):
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    include_metadata_values = []
    refresh_calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def fake_session_to_json(info, cache, allow_network=False, include_metadata=True):
        include_metadata_values.append(include_metadata)
        return {"session": info.session, "metadata_loading": not include_metadata}

    monkeypatch.setattr(app_module, "session_to_json", fake_session_to_json)
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp, "refresh_sessions", lambda *args, **kwargs: [])
    monkeypatch.setattr(webapp, "start_transcripts_payload_refresh", lambda publish=False, defer=False: refresh_calls.append((publish, defer)) or True)
    try:
        payload = webapp.transcripts_payload()
    finally:
        webapp.control_server.stop()

    assert payload["metadata_loading"] is True
    assert payload["sessions"]["5"]["metadata_loading"] is True
    assert payload["cache"]["stale"] is True
    assert payload["cache"]["lightweight"] is True
    assert payload["cache"]["refreshing"] is True
    assert include_metadata_values == [False]
    assert refresh_calls == [(True, True)]


def test_refresh_transcripts_payload_cache_publishes_full_payload_when_requested(monkeypatch):
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    events = []
    include_metadata_values = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module, "agent_auth_status", lambda: {})
    monkeypatch.setattr(app_module, "session_to_json", lambda info, cache, allow_network=False, include_metadata=True: include_metadata_values.append(include_metadata) or {"session": info.session, "metadata_loading": not include_metadata})
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp, "refresh_sessions", lambda *args, **kwargs: [])
    monkeypatch.setattr(webapp, "warm_metadata_cache_async", lambda sessions: None)
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **kwargs: events.append((event_type, payload or {}, kwargs)))
    try:
        webapp.refresh_transcripts_payload_cache(publish=True)
    finally:
        webapp.control_server.stop()

    assert include_metadata_values == [True]
    assert events and events[0][0] == "transcripts_changed"
    assert events[0][1]["data"]["metadata_loading"] is False
    assert events[0][2]["trigger"] == "transcripts_refresh"


def test_transcripts_payload_event_signature_ignores_volatile_fields():
    webapp = app_module.TmuxWebtermApp([])
    base = {
        "server_time": "2026-06-24 12:00:00 PDT",
        "server_uptime_seconds": 1.0,
        "session_order": ["5"],
        "sessions": {
            "5": {
                "session": "5",
                "metadata_badge_pulse_remaining_ms": {"pr": 900},
                "project": {"git": {"branch": "main"}},
            },
        },
    }
    changed = {
        **base,
        "server_time": "2026-06-24 12:00:05 PDT",
        "server_uptime_seconds": 6.0,
        "sessions": {
            "5": {
                **base["sessions"]["5"],
                "metadata_badge_pulse_remaining_ms": {"pr": 400},
            },
        },
    }
    try:
        assert webapp.transcripts_payload_event_signature(base) == webapp.transcripts_payload_event_signature(changed)
        real_change = {**changed, "sessions": {"5": {**changed["sessions"]["5"], "project": {"git": {"branch": "feature"}}}}}
        assert webapp.transcripts_payload_event_signature(base) != webapp.transcripts_payload_event_signature(real_change)
    finally:
        webapp.control_server.stop()


def test_client_watch_snapshot_skips_volatile_transcript_payload_push(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    payloads = [
        {
            "server_time": "2026-06-24 12:00:00 PDT",
            "server_uptime_seconds": 1.0,
            "session_order": ["5"],
            "sessions": {"5": {"session": "5", "project": {"git": {"branch": "main"}}}},
        },
        {
            "server_time": "2026-06-24 12:00:05 PDT",
            "server_uptime_seconds": 6.0,
            "session_order": ["5"],
            "sessions": {"5": {"session": "5", "project": {"git": {"branch": "main"}}}},
        },
    ]
    monkeypatch.setattr(webapp, "build_transcripts_payload", lambda: payloads.pop(0))
    monkeypatch.setattr(webapp, "publish_context_items_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "publish_activity_summary_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "publish_session_files_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "client_watch_roots_snapshot", lambda: [])
    monkeypatch.setattr(webapp, "background_can_run", lambda role: False)
    monkeypatch.setattr(webapp, "request_watch_roots_owner_refresh", lambda roots, reason: None)
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **kwargs: events.append((event_type, payload or {}, kwargs)))
    try:
        webapp.publish_client_watch_snapshot()
        webapp.publish_client_watch_snapshot()
    finally:
        webapp.control_server.stop()

    assert [event_type for event_type, _payload, _kwargs in events] == ["transcripts_changed"]
    assert events[0][2]["trigger"] == "watch_state"
    assert events[0][1]["refresh"] is True
    assert "data" not in events[0][1]


def test_metadata_badge_pulse_expiry_does_not_persist(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    signature = {"main": "", "pr": "123", "status": "open", "ci": "pending"}
    persist_calls = []
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(webapp, "metadata_badge_signatures_for_session", lambda _payload: signature)
    monkeypatch.setattr(webapp, "persist_metadata_badge_state_locked", lambda: persist_calls.append("persist"))
    webapp.metadata_badge_signatures = {"6": dict(signature)}
    webapp.metadata_badge_pulse_until = {"6": {"ci": 99.0}}
    try:
        payloads = {"6": {}}
        webapp.apply_metadata_badge_pulses(payloads)
    finally:
        webapp.control_server.stop()

    assert persist_calls == []
    assert webapp.metadata_badge_pulse_until == {}
    assert "metadata_badge_pulse_remaining_ms" not in payloads["6"]


def test_metadata_badge_signature_change_persists(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    next_signature = {"main": "", "pr": "123", "status": "merged", "ci": "passing"}
    persist_calls = []
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(webapp, "metadata_badge_signatures_for_session", lambda _payload: next_signature)
    monkeypatch.setattr(webapp, "persist_metadata_badge_state_locked", lambda: persist_calls.append("persist"))
    webapp.metadata_badge_signatures = {"6": {"main": "", "pr": "123", "status": "open", "ci": "pending"}}
    try:
        webapp.apply_metadata_badge_pulses({"6": {}})
    finally:
        webapp.control_server.stop()

    assert persist_calls == ["persist"]
    assert webapp.metadata_badge_signatures == {"6": next_signature}


def test_prompt_and_screen_status_uses_transcript_activity_when_visible_pane_is_idle(monkeypatch, tmp_path):
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "make test"}}],
            },
        }) + "\n",
        encoding="utf-8",
    )
    info = SessionInfo(
        session="6",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=123,
                pane_target="6:0.0",
                command="claude",
                cwd=None,
                status=None,
                session_id="session-6",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda session, visible_only=False: "❯ ")
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"6": info}, []))
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        prompt, screen = webapp.prompt_and_screen_status("6")
    finally:
        webapp.control_server.stop()

    assert prompt["visible"] is False
    assert screen["key"] == "working"
    assert "Bash" in screen["text"]


def test_prompt_and_screen_status_captures_discovered_agent_pane(monkeypatch):
    info = SessionInfo(
        session="6",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="6",
                kind="codex",
                pid=123,
                pane_target="6:1.0",
                command="codex",
                cwd=None,
                status=None,
                session_id=None,
                transcript=None,
                error=None,
            )
        ],
    )
    capture_calls = []
    hybrid_targets = []

    def fake_capture(target, visible_only=False):
        capture_calls.append((target, visible_only))
        return "Do you want to proceed?\n❯ 1. Yes\n  2. No"

    def fake_hybrid(target, _visible_text, pane_text=None, **_kwargs):
        hybrid_targets.append((target, pane_text is not None))
        return {"visible": True, "type": "bash", "text": "Do you want to proceed?", "yes_selected": True, "action": "approve"}

    monkeypatch.setattr(app_module, "tmux_capture_pane", fake_capture)
    monkeypatch.setattr(app_module, "hybrid_approval_prompt_state", fake_hybrid)
    monkeypatch.setattr(app_module, "agent_screen_state", lambda _text, **_kwargs: {"key": "approval", "text": "Do you want to proceed?"})
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        prompt, screen = webapp.prompt_and_screen_status("6", discovered_sessions={"6": info})
    finally:
        webapp.control_server.stop()

    assert prompt["visible"] is True
    assert set(prompt) == PROMPT_STATE_KEYS
    assert screen["key"] == "approval"
    assert capture_calls == [("6:1.0", True), ("6:1.0", False)]
    assert hybrid_targets == [("6", False), ("6", True)]


def test_prompt_and_screen_status_prefers_selected_agent_pane(monkeypatch):
    idle_claude = PaneInfo(
        session="6",
        window="0",
        pane="0",
        pane_id="%155",
        target="%155",
        current_path="/tmp",
        command="claude",
        active=False,
        window_active=False,
        title="",
        pid=155,
    )
    selected_codex = PaneInfo(
        session="6",
        window="1",
        pane="0",
        pane_id="%146",
        target="%146",
        current_path="/tmp",
        command="codex",
        active=True,
        window_active=True,
        title="",
        pid=146,
    )
    info = SessionInfo(
        session="6",
        panes=[idle_claude, selected_codex],
        selected_pane=selected_codex,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=155,
                pane_target="%155",
                command="claude",
                cwd=None,
                status=None,
                session_id=None,
                transcript=None,
                error=None,
            ),
            AgentInfo(
                session="6",
                kind="codex",
                pid=146,
                pane_target="%146",
                command="codex",
                cwd=None,
                status=None,
                session_id=None,
                transcript=None,
                error=None,
            ),
        ],
    )
    capture_calls = []

    def fake_capture(target, visible_only=False):
        capture_calls.append((target, visible_only))
        if target == "%146":
            return "Working (12m 56s · esc to interrupt)"
        return "› "

    monkeypatch.setattr(app_module, "tmux_capture_pane", fake_capture)
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        prompt, screen = webapp.prompt_and_screen_status("6", discovered_sessions={"6": info}, capture_pane=False)
    finally:
        webapp.control_server.stop()

    assert prompt["visible"] is False
    assert screen["key"] == "working"
    assert capture_calls == [("%146", True)]


def test_prompt_and_screen_status_reports_os_errors(monkeypatch):
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("tmux failed")))
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        prompt, screen = webapp.prompt_and_screen_status("6")
    finally:
        webapp.control_server.stop()

    assert prompt["error"] == "tmux failed"
    assert set(prompt) == PROMPT_STATE_KEYS | {"error"}
    assert screen == {"key": "error", "text": "tmux failed"}


def test_prompt_and_screen_status_does_not_hide_programmer_errors(monkeypatch):
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda *_args, **_kwargs: "visible")
    monkeypatch.setattr(app_module, "hybrid_approval_prompt_state", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bug")))
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        with pytest.raises(RuntimeError, match="bug"):
            webapp.prompt_and_screen_status("6")
    finally:
        webapp.control_server.stop()


def test_activity_summary_payload_reuses_cached_session_summary(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "Fix tabs"}}) + "\n", encoding="utf-8")
    info = SessionInfo(
        session="5",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="5",
                kind="codex",
                pid=123,
                pane_target="5:0.0",
                command="codex",
                cwd=str(tmp_path),
                status="running",
                session_id="session-5",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    files_payload = {"files": [{"status": "M", "repo": str(tmp_path), "path": "README.md", "abs_path": str(tmp_path / "README.md"), "added": 1, "removed": 0, "mtime": 10}], "repos": [{"repo": str(tmp_path), "count": 1}], "errors": []}
    calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    project_payload = {
        "git": {"root": str(tmp_path), "cwd": str(tmp_path), "branch": "main", "dirty_count": 1, "ahead": 2, "behind": 3},
        "pull_request": {
            "number": 42,
            "title": "Add info drawer",
            "url": "https://example.test/pull/42",
            "status_label": "passing",
            "checks": {"status_label": "passing"},
        },
        "linear": [{"identifier": "GUI-7", "title": "Info drawer metadata", "state": "In Progress"}],
    }
    monkeypatch.setattr(app_module, "session_project_metadata", lambda info, cache, allow_network=False: project_payload)
    monkeypatch.setattr(app_module.session_files, "session_files_payload_for_info", lambda info, hours=24.0, **_kwargs: files_payload)

    def fake_build(info, project, files, locale="en"):
        calls.append((info.session, locale))
        return {"session": info.session, "agent": "codex", "active": False, "repos": [str(tmp_path)], "files": {"count": 1, "added": 1, "removed": 0}, "lines": ["cached test"], "local": "cached test"}

    monkeypatch.setattr(app_module, "build_session_activity_summary", fake_build)
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    tail_many_calls = []

    def fake_tail_many(sessions, limit=100):
        tail_many_calls.append((tuple(sessions), limit))
        return {"5": [{"session": "5", "message": "ready"}]}

    def fail_tail(*_args, **_kwargs):
        raise AssertionError("activity summary must use tail_many instead of per-session tail")

    webapp.event_log.tail_many = fake_tail_many
    webapp.event_log.tail = fail_tail
    try:
        first = webapp.activity_summary_payload()
        second = webapp.activity_summary_payload()
        third = webapp.activity_summary_payload(force=True)
        localized = webapp.activity_summary_payload(locale="zh-Hant")
    finally:
        webapp.control_server.stop()

    assert calls == [("5", "en"), ("5", "en"), ("5", "zh-Hant")]
    assert first["global"]["files"] == {"count": 1, "added": 1, "removed": 0}
    assert first["agents"][0]["label"] == "session '5' 0:codex"
    assert first["agents"][0]["recent_paths"][0]["path"] == str(tmp_path)
    assert first["session_info"]["5"]["path"] == str(tmp_path)
    assert first["session_info"]["5"]["git"] == project_payload["git"]
    assert first["session_info"]["5"]["pull_request"]["number"] == 42
    assert first["session_info"]["5"]["ci"] == {"status_label": "passing"}
    assert first["session_info"]["5"]["linear"][0]["identifier"] == "GUI-7"
    assert first["session_info"]["5"]["latest_summary"] == "cached test"
    assert first["session_info"]["5"]["recent_events"][0]["message"] == "ready"
    assert tail_many_calls == [(("5",), 5), (("5",), 5), (("5",), 5), (("5",), 5)]
    assert second["sessions"]["5"]["local"] == "cached test"
    assert third["sessions"]["5"]["local"] == "cached test"
    assert localized["locale"] == "zh-Hant"


def test_activity_session_info_payload_normalizes_malformed_project_git(tmp_path):
    info = SessionInfo(
        session="5",
        panes=[],
        selected_pane=PaneInfo(
            session="5",
            window="0",
            pane="0",
            pane_id="%1",
            target="5:0.0",
            current_path=str(tmp_path),
            command="zsh",
            active=True,
            window_active=True,
            title="",
            pid=100,
        ),
        agents=[],
    )
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        payload = webapp.activity_session_info_payload(
            "5",
            info,
            {"git": "not-a-dict", "pull_request": None, "linear": []},
            {"files": [], "repos": [], "errors": []},
            {"files": {}},
            recent_events=[],
        )
    finally:
        webapp.control_server.stop()

    assert payload["git"] == {}
    assert payload["path"] == str(tmp_path)


def test_activity_payload_returns_indefinite_stale_cache_and_refreshes(monkeypatch):
    snapshots = [
        {"5": {"last_user_input_ts": 100}},
        {"5": {"last_user_input_ts": 200}},
    ]
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        calls = []

        def fake_snapshot():
            calls.append("snapshot")
            return snapshots[min(len(calls) - 1, len(snapshots) - 1)]

        webapp.activity_ledger.snapshot = fake_snapshot
        webapp.refresh_tabber_activity_cache()
        first, status = webapp.activity_payload()
        second, _status = webapp.activity_payload()

        assert status == HTTPStatus.OK
        assert first["activity"]["5"]["last_user_input_ts"] == 100
        assert first["agents"] == []
        assert second["activity"]["5"]["last_user_input_ts"] == 100
        assert second["cache"]["hit"] is True
        assert second["cache"]["stale"] is False
        assert calls == ["snapshot"]

        stored_at, payload = webapp.tabber_activity_cache
        webapp.tabber_activity_cache = (
            stored_at - webapp.tabber_activity_refresh_seconds() - 1,
            payload,
        )
        monkeypatch.setattr(webapp, "read_tabber_activity_disk_cache", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(webapp, "start_tabber_activity_cache_refresh", lambda: "queued")
        stale, _status = webapp.activity_payload()

        assert stale["activity"]["5"]["last_user_input_ts"] == 100
        assert stale["cache"]["stale"] is True
        assert stale["cache"]["refreshing"] == "queued"
        assert calls == ["snapshot"]
    finally:
        webapp.control_server.stop()


def test_activity_warm_takeover_reads_disk_cache_without_rebuild_or_rewrite(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "TABBER_ACTIVITY_CACHE_DIR", tmp_path / "activity-cache")
    payload = {
        "activity": {"5": {"last_user_input_ts": 100}},
        "agents": [],
        "agent_windows": {},
        "errors": [],
        "session_scope": "configured",
        "session_file_hours": 24.0,
    }
    seed_app = app_module.TmuxWebtermApp(["5"])
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        source_signature = seed_app.tabber_activity_source_signature()
        seed_app.set_tabber_activity_cache(payload, source_signature=source_signature)
        path, signature = seed_app.tabber_activity_cache_disk_path(24.0, source_signature)
        payload_mtime = path.stat().st_mtime_ns
        monkeypatch.setattr(webapp, "build_activity_payload", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("warm takeover must not rebuild activity")))

        webapp.warm_start_tabber_activity_cache()
        cached = webapp.get_tabber_activity_cache(float("inf"), allow_stale=True, hours=24.0, source_signature=source_signature)
    finally:
        seed_app.control_server.stop()
        webapp.control_server.stop()

    assert cached is not None
    cached_payload, fresh, age_seconds = cached
    assert cached_payload["activity"] == {"5": {"last_user_input_ts": 100}}
    assert fresh is True
    assert age_seconds >= 0
    assert path.stat().st_mtime_ns == payload_mtime
    manifest = json.loads(seed_app.tabber_activity_cache_manifest_path(signature).read_text(encoding="utf-8"))
    assert manifest["payload_signature"] == seed_app.session_files_payload_signature(payload)


def test_activity_recency_ignores_terminal_report_heartbeats(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        webapp.active_window_for = lambda session: "1"
        webapp.activity_ledger.heartbeat("6", "1", ts=1000.0, byte_count=1)
        monkeypatch.setattr(webapp.activity_ledger, "_clock", lambda: 1065.0)

        for control_report in ("\x1b[12;40R", "\x1b[<0;12;34M", "\x1b[<0;12;34m", "\x1b[<64;80;24M"):
            webapp.record_user_input("6", len(control_report), data=control_report)
        activity = webapp.activity_snapshot_with_recency()

        assert 1065.0 - activity["6"]["active_recency_ts"] >= 60.0
        assert 1065.0 - activity["6:1"]["active_recency_ts"] >= 60.0
        assert activity["6"]["last_user_input_ts"] == 1000.0
        assert activity["6:1"]["last_user_input_ts"] == 1000.0
        assert activity["6"]["input_events"] == 1
        assert activity["6:1"]["input_events"] == 1
    finally:
        webapp.control_server.stop()


def test_activity_recency_records_genuine_just_active_input(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        webapp.cached_active_window_for = lambda session: "1"
        webapp.activity_ledger.heartbeat("6", "1", ts=1000.0, byte_count=1)
        monkeypatch.setattr(webapp.activity_ledger, "_clock", lambda: 1012.0)
        monkeypatch.setattr(app_module.time, "time", lambda: 1012.0)

        webapp.record_user_input("6", 1, data="x")
        assert webapp.flush_input_heartbeats()
        activity = webapp.activity_snapshot_with_recency()

        assert 1012.0 - activity["6"]["active_recency_ts"] < 15.0
        assert 1012.0 - activity["6:1"]["active_recency_ts"] < 15.0
        assert activity["6"]["last_user_input_ts"] == 1012.0
        assert activity["6:1"]["last_user_input_ts"] == 1012.0
        assert activity["6"]["input_events"] == 2
        assert activity["6:1"]["input_events"] == 2
    finally:
        webapp.stop_input_heartbeat_worker()
        webapp.control_server.stop()


def test_record_user_input_coalesces_heartbeats_off_hot_path(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    monkeypatch.setattr(app_module, "INPUT_HEARTBEAT_COALESCE_SECONDS", 60.0)
    times = iter([1012.0, 1012.02])
    monkeypatch.setattr(app_module.time, "time", lambda: next(times))
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        webapp.cached_active_window_for = lambda session: "1"
        webapp.activity_ledger.heartbeat("6", "1", ts=1000.0, byte_count=1)

        webapp.record_user_input("6", 1, data="x")
        webapp.record_user_input("6", 2, data="yy")
        before_flush = webapp.activity_snapshot_with_recency()
        assert before_flush["6"]["last_user_input_ts"] == 1000.0
        assert before_flush["6:1"]["last_user_input_ts"] == 1000.0

        assert webapp.flush_input_heartbeats()
        activity = webapp.activity_snapshot_with_recency()

        assert activity["6"]["last_user_input_ts"] == 1012.02
        assert activity["6:1"]["last_user_input_ts"] == 1012.02
        assert activity["6"]["input_events"] == 2
        assert activity["6:1"]["input_events"] == 2
        assert activity["6"]["input_bytes"] == 4
        assert activity["6:1"]["input_bytes"] == 4
    finally:
        webapp.stop_input_heartbeat_worker()
        webapp.control_server.stop()


def test_record_user_input_cache_miss_avoids_tmux_and_refreshes_out_of_band(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["7777"])
    refreshes = []

    def fail_tmux(*_args, **_kwargs):
        raise AssertionError("record_user_input must not call tmux")

    try:
        webapp.set_transcripts_payload_cache({"sessions": {"7777": {"panes": []}}})
        monkeypatch.setattr(app_module, "tmux", fail_tmux)
        monkeypatch.setattr(webapp, "start_transcripts_payload_refresh", lambda publish=False, defer=False: refreshes.append((publish, defer)) or True)
        monkeypatch.setattr(webapp.activity_ledger, "_clock", lambda: 2000.0)
        monkeypatch.setattr(app_module.time, "time", lambda: 2000.0)

        webapp.record_user_input("7777", 1, data="x")
        assert webapp.flush_input_heartbeats()
        activity = webapp.activity_snapshot_with_recency()

        assert activity["7777"]["last_user_input_ts"] == 2000.0
        assert "7777:0" not in activity
        assert refreshes == [(False, True)]
    finally:
        webapp.stop_input_heartbeat_worker()
        webapp.control_server.stop()


def test_active_window_for_can_refresh_live_tmux_window_off_input_path(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["7777"])

    def fake_tmux(args, timeout=5.0):
        assert args == ["display-message", "-p", "-t", "7777:", "#{window_index}"]
        return app_module.subprocess.CompletedProcess(args, 0, "0\n", "")

    try:
        webapp.set_transcripts_payload_cache({"sessions": {"7777": {"panes": []}}})
        monkeypatch.setattr(app_module, "tmux", fake_tmux)

        assert webapp.active_window_for("7777") == "0"
    finally:
        webapp.control_server.stop()


def test_tabber_activity_refresh_seconds_uses_performance_setting(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"performance": {"tabber_activity_refresh_ms": 2500}}})
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        assert webapp.tabber_activity_refresh_seconds() == 2.5
    finally:
        webapp.control_server.stop()


def test_tabber_activity_cache_warmer_refreshes_snapshot(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    refreshes = []
    events = []

    def stop_after_sleep(seconds):
        raise RuntimeError(f"stop after sleeping {seconds}")

    try:
        webapp.tabber_activity_cache_warmer_running = True
        webapp.mark_tabber_activity_consumer()
        monkeypatch.setattr(webapp, "refresh_tabber_activity_cache", lambda: refreshes.append("refresh") or {})
        monkeypatch.setattr(webapp, "publish_activity_summary_ready_events", lambda trigger: events.append(trigger) or [])
        monkeypatch.setattr(webapp, "tabber_activity_refresh_seconds", lambda: 15.0)
        monkeypatch.setattr(app_module.time, "sleep", stop_after_sleep)

        with pytest.raises(RuntimeError, match="stop after sleeping"):
            webapp.tabber_activity_cache_warmer_loop()
    finally:
        webapp.control_server.stop()

    assert refreshes == ["refresh"]
    assert events == []
    assert webapp.tabber_activity_cache_warmer_running is False


def test_tabber_activity_cache_warmer_skips_without_visible_consumer(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    refreshes = []
    sleeps = []

    def stop_after_sleep(seconds):
        sleeps.append(seconds)
        raise RuntimeError(f"stop after sleeping {seconds}")

    try:
        webapp.tabber_activity_cache_warmer_running = True
        monkeypatch.setattr(webapp, "refresh_tabber_activity_cache", lambda: refreshes.append("refresh") or {})
        monkeypatch.setattr(webapp, "tabber_activity_refresh_seconds", lambda: 15.0)
        monkeypatch.setattr(app_module.time, "sleep", stop_after_sleep)

        with pytest.raises(RuntimeError, match="stop after sleeping"):
            webapp.tabber_activity_cache_warmer_loop()
    finally:
        webapp.control_server.stop()

    assert refreshes == []
    assert len(sleeps) == 1
    assert 59.9 <= sleeps[0] <= 60.0
    recent = webapp.performance_metrics_payload()["recent"]
    assert recent[-1]["role"] == app_module.BACKGROUND_ROLE_TABBER_ACTIVITY
    assert recent[-1]["cache_status"] == "skipped:no-consumer"
    assert webapp.tabber_activity_cache_warmer_running is False


def test_activity_payload_hidden_consumer_does_not_refresh_stale_cache(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        payload = {"activity": {"5": {"last_user_input_ts": 100}}, "agents": [], "agent_windows": {}, "errors": [], "session_scope": "configured", "session_file_hours": 24.0}
        webapp.set_tabber_activity_cache(payload, write_disk=False, source_signature=webapp.tabber_activity_source_signature())
        stored_at, cached_payload = webapp.tabber_activity_cache
        webapp.tabber_activity_cache = (stored_at - webapp.tabber_activity_refresh_seconds() - 1, cached_payload)
        monkeypatch.setattr(webapp, "read_tabber_activity_disk_cache", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(webapp, "start_tabber_activity_cache_refresh", lambda: (_ for _ in ()).throw(AssertionError("hidden activity request must not queue refresh")))

        hidden, status = webapp.activity_payload(visible=False)
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert hidden["cache"]["stale"] is True
    assert hidden["cache"]["refreshing"] is False
    assert hidden["cache"]["idle_no_consumer"] is True


def test_activity_summary_ready_auto_triggers_do_not_regenerate(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    calls = []
    try:
        monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: calls.append((args, kwargs)) or {"generated_at": "now", "global": {"headline": "changed"}, "sessions": {}})
        webapp.client_watch_activity_summary = {"visible": True, "locale": "en", "scope": "all", "hours": 24}

        assert webapp.publish_activity_summary_ready_events(trigger="watch_state") == []
        assert webapp.publish_activity_summary_ready_events(trigger="transcripts_changed") == []
        assert webapp.publish_activity_summary_ready_events(trigger="tabber_activity") == []
    finally:
        webapp.control_server.stop()

    assert calls == []


def test_activity_summary_agents_come_from_tabber_activity_cache(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        cached_agent = {
            "label": "session '5' 0:codex",
            "session": "5",
            "window_label": "0:codex",
            "agent_kind": "codex",
            "recent_paths": [{"path": "/repo/yolomux"}],
        }
        source_signature = webapp.tabber_activity_source_signature()
        webapp.set_tabber_activity_cache({"activity": {}, "agents": [cached_agent], "errors": []}, write_disk=False, source_signature=source_signature)
        payload = webapp.activity_summary_payload()
        assert payload["agents"] == [cached_agent]
    finally:
        webapp.control_server.stop()


def test_refresh_sessions_rotates_activity_heartbeats_hourly(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["5"], None))
    try:
        calls = []
        monkeypatch.setattr(webapp.activity_ledger, "rotate_heartbeats", lambda: calls.append("rotate") or 1)

        assert webapp.refresh_sessions() == []
        assert webapp.refresh_sessions() == []
        webapp.activity_heartbeat_next_rotate_at = 0
        assert webapp.refresh_sessions() == []

        assert calls == ["rotate", "rotate"]
    finally:
        webapp.control_server.stop()


def test_corrupt_activity_ledger_does_not_break_app_start(monkeypatch, tmp_path):
    activity_path = tmp_path / "activity.json"
    heartbeat_path = tmp_path / "activity-heartbeats.jsonl"
    activity_path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(app_module, "ACTIVITY_PATH", activity_path)
    monkeypatch.setattr(app_module, "ACTIVITY_HEARTBEATS_PATH", heartbeat_path)
    monkeypatch.setattr(app_module, "TABBER_ACTIVITY_CACHE_DIR", tmp_path / "activity-cache")
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))

    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        payload, status = webapp.activity_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["activity"] == {}


def test_normalized_client_session_files_uses_shared_lookback_bounds():
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        items = webapp.normalized_client_session_files([
            {"session": "half-hour", "hours": 0.5},
            {"session": "two-weeks", "hours": 336},
            {"session": "too-high", "hours": 24 * 365},
        ])
    finally:
        webapp.control_server.stop()

    assert [item["session"] for item in items] == ["half-hour", "two-weeks", "too-high"]
    assert [item["hours"] for item in items] == [0.5, 336.0, float(app_module.session_files.SESSION_FILES_MAX_HOURS)]


def test_session_files_payload_reuses_short_cache(monkeypatch):
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def fake_session_files_payload(session, infos, hours, from_ref=None, to_ref=None, repo_refs=None, **_kwargs):
        calls.append((session, tuple(infos), hours, from_ref, to_ref, repo_refs))
        return {"session": session, "files": [], "repos": [], "errors": []}, HTTPStatus.OK

    monkeypatch.setattr(app_module.session_files, "session_files_payload", fake_session_files_payload)
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.refresh_sessions = lambda *args, **kwargs: []
    try:
        first, first_status = webapp.session_files_payload("5")
        second, second_status = webapp.session_files_payload("5")
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert calls == [("5", ("5",), 24.0, None, None, None)]
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert first["files"] == second["files"] == []
    assert first["repos"] == second["repos"] == []
    assert first["errors"] == second["errors"] == []


def test_session_files_payload_reuses_shared_disk_cache_between_apps(monkeypatch):
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def fake_session_files_payload(session, infos, hours, from_ref=None, to_ref=None, repo_refs=None, **_kwargs):
        calls.append((session, tuple(infos), hours, from_ref, to_ref, repo_refs))
        return {"session": session, "files": [{"path": "/repo/one.txt"}], "repos": [{"path": "/repo"}], "errors": []}, HTTPStatus.OK

    monkeypatch.setattr(app_module.session_files, "session_files_payload", fake_session_files_payload)
    first_app = app_module.TmuxWebtermApp(["5"])
    second_app = app_module.TmuxWebtermApp(["5"])
    first_app.refresh_sessions = lambda *args, **kwargs: []
    second_app.refresh_sessions = lambda *args, **kwargs: []
    try:
        first, first_status = first_app.session_files_payload("5")
        second, second_status = second_app.session_files_payload("5")
    finally:
        first_app.control_server.stop()
        second_app.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert calls == [("5", ("5",), 24.0, None, None, None)]
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert second["files"] == [{"path": "/repo/one.txt"}]
    assert second["repos"] == [{"path": "/repo"}]
    assert second["errors"] == []


def test_activity_warmup_adopts_session_files_disk_cache_without_rebuild(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"timestamp": "2026-06-15T00:00:00Z"}) + "\n", encoding="utf-8")
    info = SessionInfo(
        session="5",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="5",
                kind="codex",
                pid=123,
                pane_target="5:0.0",
                command="codex",
                cwd=str(tmp_path),
                status="running",
                session_id="session-5",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    files_payload = {"session": "5", "files": [{"path": "README.md", "repo": str(tmp_path)}], "repos": [], "errors": []}
    calls = []

    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module.session_files, "session_files_payload_for_info", lambda info, hours=24.0, **_kwargs: calls.append("info") or files_payload)
    monkeypatch.setattr(app_module.session_files, "session_files_payload", lambda *_args, **_kwargs: calls.append("payload") or {"files": [], "repos": [], "errors": []})
    seed_app = app_module.TmuxWebtermApp(["5"])
    webapp = app_module.TmuxWebtermApp(["5"])
    seed_app.refresh_sessions = lambda *args, **kwargs: []
    webapp.refresh_sessions = lambda *args, **kwargs: []
    try:
        key = seed_app.session_files_cache_key("payload", {"5": info}, "5", 24.0, None, None, None)
        seed_app.set_session_files_cache(key, files_payload, HTTPStatus.OK)
        path, _signature = seed_app.session_files_disk_cache_path(key)
        payload_mtime = path.stat().st_mtime_ns
        webapp.warm_start_session_files_payload_cache()
        payload, status = webapp.session_files_payload("5")
    finally:
        seed_app.control_server.stop()
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["cache"]["hit"] is True
    assert payload["files"] == [{"path": "README.md", "repo": str(tmp_path)}]
    assert calls == []
    assert path.stat().st_mtime_ns == payload_mtime


def test_session_files_batch_payload_discovers_once_and_uses_per_session_cache(monkeypatch):
    info5 = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    info6 = SessionInfo(session="6", panes=[], selected_pane=None, agents=[])
    discover_calls = []
    payload_calls = []

    def fake_discover(sessions):
        discover_calls.append(tuple(sessions))
        infos = {"5": info5, "6": info6}
        return {session: infos[session] for session in sessions if session in infos}, []

    def fake_session_files_payload(session, infos, hours, from_ref=None, to_ref=None, repo_refs=None, **_kwargs):
        payload_calls.append((session, tuple(infos), hours, from_ref, to_ref, repo_refs))
        return {"session": session, "files": [{"path": f"{session}.txt"}], "repos": [], "errors": []}, HTTPStatus.OK

    monkeypatch.setattr(app_module, "discover_sessions", fake_discover)
    monkeypatch.setattr(app_module.session_files, "session_files_payload", fake_session_files_payload)
    webapp = app_module.TmuxWebtermApp(["5", "6"])
    webapp.refresh_sessions = lambda *args, **kwargs: []
    discover_calls.clear()
    payload_calls.clear()
    try:
        first, first_status = webapp.session_files_batch_payload(["5", "6"])
        second, second_status = webapp.session_files_batch_payload(["5", "6"])
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert discover_calls == [("5", "6"), ("5", "6")]
    assert sorted(payload_calls) == [
        ("5", ("5",), 24.0, None, None, None),
        ("6", ("6",), 24.0, None, None, None),
    ]
    assert first["sessions"]["5"]["cache"]["hit"] is False
    assert first["sessions"]["6"]["cache"]["hit"] is False
    assert second["sessions"]["5"]["cache"]["hit"] is True
    assert second["sessions"]["6"]["cache"]["hit"] is True
    assert first["sessions"]["5"]["files"] == [{"path": "5.txt"}]
    assert first["sessions"]["6"]["files"] == [{"path": "6.txt"}]


def test_session_files_payload_returns_stale_cache_and_refreshes(monkeypatch):
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def fake_session_files_payload(session, infos, hours, from_ref=None, to_ref=None, repo_refs=None, **_kwargs):
        calls.append(len(calls) + 1)
        return {"session": session, "files": [{"path": f"file-{calls[-1]}.txt"}], "repos": [], "errors": []}, HTTPStatus.OK

    monkeypatch.setattr(app_module.session_files, "session_files_payload", fake_session_files_payload)
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.refresh_sessions = lambda *args, **kwargs: []
    webapp.start_session_files_cache_refresh = lambda cache_key, target, *args: (target(cache_key, *args) or True)
    try:
        first, first_status = webapp.session_files_payload("5")
        key = next(iter(webapp.session_files_cache))
        with webapp.session_files_cache_lock:
            stored_at, value = webapp.session_files_cache[key]
            webapp.session_files_cache[key] = (stored_at - app_module.SESSION_FILES_CACHE_SECONDS - 1.0, value)
        path, signature = webapp.session_files_disk_cache_path(key)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["stored_at"] = float(record["stored_at"]) - app_module.SESSION_FILES_CACHE_SECONDS - 1.0
        path.write_text(json.dumps(record), encoding="utf-8")
        manifest_path = webapp.session_files_disk_manifest_path(signature)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["stored_at"] = float(manifest["stored_at"]) - app_module.SESSION_FILES_CACHE_SECONDS - 1.0
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        second, second_status = webapp.session_files_payload("5")
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert first["files"] == [{"path": "file-1.txt"}]
    assert second["files"] == [{"path": "file-1.txt"}]
    assert second["cache"]["hit"] is True
    assert second["cache"]["stale"] is True
    assert calls == [1, 2]


def test_session_files_disk_cache_manifest_refreshes_without_rewriting_unchanged_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    real_atomic_write_text = app_module.atomic_write_text
    writes = []

    def tracking_atomic_write_text(path, text, mode=None):
        writes.append(path.name)
        real_atomic_write_text(path, text, mode=mode)

    monkeypatch.setattr(app_module, "atomic_write_text", tracking_atomic_write_text)
    webapp = app_module.TmuxWebtermApp(["5"])
    key = ("payload", "5")
    payload = {"files": [{"path": "same.py"}], "repos": [], "errors": []}
    try:
        path, signature = webapp.session_files_disk_cache_path(key)
        manifest_path = webapp.session_files_disk_manifest_path(signature)
        webapp.write_session_files_disk_cache(key, payload, HTTPStatus.OK)
        first_record = json.loads(path.read_text(encoding="utf-8"))
        webapp.write_session_files_disk_cache(key, payload, HTTPStatus.OK)
        second_record = json.loads(path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        cached = webapp.read_session_files_disk_cache(key, max_age_seconds=app_module.SESSION_FILES_CACHE_SECONDS)
    finally:
        webapp.control_server.stop()

    assert writes.count(path.name) == 1
    assert writes.count(manifest_path.name) == 2
    assert first_record == second_record
    assert manifest["payload_changed"] is False
    assert manifest["payload_signature"] == first_record["payload_signature"]
    assert cached is not None
    assert cached[0]["files"] == [{"path": "same.py"}]
    assert cached[2] is True


def test_session_files_disk_cache_prune_removes_old_entries_and_caps_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    cache_dir = app_module.SESSION_FILES_CACHE_DIR
    cache_dir.mkdir(parents=True)
    now = 10_000.0

    def write_entry(signature: str, payload_size: int, manifest_size: int, mtime: float) -> tuple[Path, Path]:
        payload_path = cache_dir / f"{signature}.json"
        manifest_path = cache_dir / f"{signature}.manifest.json"
        payload_path.write_text("p" * payload_size, encoding="utf-8")
        manifest_path.write_text("m" * manifest_size, encoding="utf-8")
        os.utime(payload_path, (mtime, mtime))
        os.utime(manifest_path, (mtime, mtime))
        return payload_path, manifest_path

    old_payload, old_manifest = write_entry("old", 70, 10, now - 200)
    older_fresh_payload, older_fresh_manifest = write_entry("older-fresh", 70, 10, now - 50)
    newest_payload, newest_manifest = write_entry("newest", 70, 10, now - 10)
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        result = webapp.prune_session_files_disk_cache(max_age_seconds=100, max_bytes=100, now=now)
    finally:
        webapp.control_server.stop()

    assert result["entries"] == 3
    assert result["removed_entries"] == 2
    assert result["removed_files"] == 4
    assert result["kept_bytes"] == 80
    assert not old_payload.exists()
    assert not old_manifest.exists()
    assert not older_fresh_payload.exists()
    assert not older_fresh_manifest.exists()
    assert newest_payload.exists()
    assert newest_manifest.exists()


def test_cache_hash_helpers_reuse_client_event_payload_signature(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    calls = []

    def fake_signature(payload):
        calls.append(payload)
        return f"encoded-{len(calls)}"

    webapp.client_event_payload_signature = fake_signature
    try:
        _path, disk_signature = webapp.session_files_disk_cache_path(("payload", {"session": "5"}))
        payload_signature = webapp.session_files_payload_signature({"files": [{"path": "same.py"}]})
        tabber_signature = webapp.tabber_activity_source_signature()
    finally:
        webapp.control_server.stop()

    assert disk_signature == hashlib.sha256(b"encoded-1").hexdigest()
    assert payload_signature == hashlib.sha256(b"encoded-2").hexdigest()
    assert tabber_signature == hashlib.sha256(b"encoded-3").hexdigest()
    assert calls[0] == ("payload", {"session": "5"})
    assert calls[1] == {"files": [{"path": "same.py"}]}
    assert calls[2] == {"scope": "configured", "sessions": [("5", None)]}


def test_update_client_watch_roots_filters_and_expires(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(app_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    try:
        payload = webapp.update_client_watch_roots({
            "roots": ["/repo", "relative", "", "/repo"],
            "files": ["/repo/DOIT.51.md", "relative"],
            "background_files": ["/repo/README.md", "/repo/DOIT.51.md", "relative"],
        })
        assert payload["roots"] == ["/repo"]
        assert payload["files"] == ["/repo/DOIT.51.md"]
        assert payload["background_files"] == ["/repo/README.md"]
        assert webapp.client_watch_roots_snapshot() == ["/repo"]
        assert webapp.client_watch_files_snapshot() == ["/repo/DOIT.51.md"]
        assert webapp.client_watch_background_files_snapshot() == ["/repo/README.md"]
        monkeypatch.setattr(app_module.time, "monotonic", lambda: 1000.0)
        monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
        assert webapp.client_watch_roots_snapshot() == []
        assert webapp.client_watch_files_snapshot() == []
        assert webapp.client_watch_background_files_snapshot() == []
    finally:
        webapp.control_server.stop()


def test_client_watch_roots_are_shared_across_app_instances(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "WATCH_INDEX_PATH", tmp_path / "watch-index.json")
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    app1 = app_module.TmuxWebtermApp([])
    app2 = app_module.TmuxWebtermApp([])
    try:
        app1.update_client_watch_roots({"roots": ["/repo/one"]})
        app2.update_client_watch_roots({"roots": ["/repo/two"]})

        assert app1.client_watch_roots_snapshot() == ["/repo/one", "/repo/two"]
        assert app2.client_watch_roots_snapshot() == ["/repo/one", "/repo/two"]
        assert not (tmp_path / "watch-index.json").exists()
        owner_files = sorted((tmp_path / "watch-index.json.owners").glob("*.json"))
        assert len(owner_files) == 2
        owner_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in owner_files]
        assert sorted(payload["owner_id"] for payload in owner_payloads) == sorted([app1.watch_root_owner_id, app2.watch_root_owner_id])
    finally:
        app1.control_server.stop()
        app2.control_server.stop()


def test_client_watch_roots_concurrent_writes_do_not_clobber(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "WATCH_INDEX_PATH", tmp_path / "watch-index.json")
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    app1 = app_module.TmuxWebtermApp([])
    app2 = app_module.TmuxWebtermApp([])
    barrier = threading.Barrier(2)

    def update(app, root):
        barrier.wait(timeout=5)
        app.update_client_watch_roots({"roots": [root]})

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(update, app1, "/repo/one"),
                executor.submit(update, app2, "/repo/two"),
            ]
            for future in futures:
                future.result(timeout=5)
        assert app1.client_watch_roots_snapshot() == ["/repo/one", "/repo/two"]
        assert app2.client_watch_roots_snapshot() == ["/repo/one", "/repo/two"]
    finally:
        app1.control_server.stop()
        app2.control_server.stop()


def test_client_watch_roots_lock_free_read_during_write(monkeypatch, tmp_path):
    index_path = tmp_path / "watch-index.json"
    monkeypatch.setattr(app_module, "WATCH_INDEX_PATH", index_path)
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    writer = app_module.TmuxWebtermApp([])
    reader = app_module.TmuxWebtermApp([])
    try:
        writer.update_client_watch_roots({"roots": ["/repo/old"]})
        observed: list[list[str]] = []

        owner_path = writer.watch_root_index.owner_path
        with app_module.file_lock(owner_path):
            thread = threading.Thread(target=lambda: observed.append(reader.client_watch_roots_snapshot()))
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive()
            assert observed == [["/repo/old"]]
            replacement = {
                "version": 2,
                "owner_id": writer.watch_root_owner_id,
                "entries": {
                    "client:/repo/new": {
                        "path": "/repo/new",
                        "source": "client",
                        "expires_at": 200.0,
                        "updated_at": 100.0,
                    }
                },
            }
            app_module.atomic_write_text(owner_path, json.dumps(replacement, separators=(",", ":")), mode=0o600)

        assert reader.client_watch_roots_snapshot() == ["/repo/new"]
        owner_path.write_text("{not-json", encoding="utf-8")
        assert reader.client_watch_roots_snapshot() == []
    finally:
        writer.control_server.stop()
        reader.control_server.stop()


def test_client_watch_roots_limit_keeps_multiple_owners_visible(tmp_path, caplog):
    index_path = tmp_path / "watch-index.json"
    clock = lambda: 100.0
    owner_a = app_module.SharedWatchRootIndex(index_path, "owner-a", limit=2, clock=clock)
    owner_b = app_module.SharedWatchRootIndex(index_path, "owner-b", limit=2, clock=clock)

    owner_a.update_client_roots(["/repo/a1", "/repo/a2"])
    owner_b.update_client_roots(["/repo/b1", "/repo/b2"])

    with caplog.at_level("WARNING"):
        assert owner_a.snapshot() == ["/repo/a1", "/repo/b1"]
    assert "shared watch-root index truncated from 4 live roots across 2 owners to 2" in caplog.text


def test_client_watch_roots_updates_only_current_owner_file(tmp_path):
    index_path = tmp_path / "watch-index.json"
    clock = lambda: 100.0
    owner_a = app_module.SharedWatchRootIndex(index_path, "owner-a", limit=10, clock=clock)
    owner_b = app_module.SharedWatchRootIndex(index_path, "owner-b", limit=10, clock=clock)

    owner_a.update_client_roots(["/repo/a"])
    owner_b.update_client_roots(["/repo/b"])
    before_b = owner_b.owner_path.read_text(encoding="utf-8")
    owner_a.update_active_roots({"1": "/repo/a-active"})

    assert not index_path.exists()
    assert owner_b.owner_path.read_text(encoding="utf-8") == before_b
    assert owner_a.snapshot() == ["/repo/a", "/repo/a-active", "/repo/b"]


def test_filesystem_change_summary_counts_entry_changes():
    previous = (
        (
            "/repo",
            (
                "/repo",
                "dir",
                100,
                0,
                (
                    ("old.txt", "file", 100, 10),
                    ("same.txt", "file", 100, 10),
                    ("old-dir", "dir", 100, 0),
                    ("mod.txt", "file", 100, 10),
                ),
            ),
        ),
    )
    current = (
        (
            "/repo",
            (
                "/repo",
                "dir",
                200,
                0,
                (
                    ("new.txt", "file", 100, 10),
                    ("same.txt", "file", 100, 10),
                    ("new-dir", "dir", 100, 0),
                    ("mod.txt", "file", 200, 10),
                ),
            ),
        ),
        ("/new-root", ("/new-root", "missing")),
    )

    summary = app_module.filesystem_change_summary(previous, current)

    assert summary["roots_changed"] == 2
    assert summary["roots_added"] == 1
    assert summary["roots_removed"] == 0
    assert summary["entries_added"] == 2
    assert summary["entries_removed"] == 2
    assert summary["entries_modified"] == 1
    assert summary["files_added"] == 1
    assert summary["files_removed"] == 1
    assert summary["files_modified"] == 1
    assert summary["dirs_added"] == 1
    assert summary["dirs_removed"] == 1
    assert summary["dirs_modified"] == 0


def test_poll_client_events_once_publishes_changed_signatures(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp([])
    events = []
    settings_signatures = [("settings", 1), ("settings", 2)]
    transcript_signatures = [("transcripts", 1), ("transcripts", 2)]
    filesystem_signatures = [
        (("/repo", ("/repo", "dir", 100, 0, (("old.txt", "file", 100, 10),))),),
        (("/repo", ("/repo", "dir", 200, 0, (("new.txt", "file", 100, 10),))),),
    ]
    monkeypatch.setattr(webapp, "settings_watch_signature", lambda: settings_signatures.pop(0))
    monkeypatch.setattr(webapp, "transcripts_watch_signature", lambda sessions: transcript_signatures.pop(0))
    monkeypatch.setattr(webapp, "filesystem_roots_watch_signature", lambda sessions: filesystem_signatures.pop(0))
    monkeypatch.setattr(webapp, "filesystem_roots_for_watch", lambda sessions: ["/repo"])
    monkeypatch.setattr(webapp, "filesystem_push_payload", lambda roots: (_ for _ in ()).throw(AssertionError("diff-only fs_changed must not list directories")))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        webapp.client_watch_filesystem_last_full_at = time.monotonic()
        webapp.set_session_files_cache(("k",), {"files": []}, HTTPStatus.OK)
        webapp.transcripts_payload_cache = (1.0, {"sessions": {}})
        assert webapp.poll_client_events_once() == []
        assert webapp.poll_client_events_once() == ["settings_changed", "transcripts_changed", "fs_changed"]
    finally:
        webapp.control_server.stop()

    assert [event_type for event_type, _payload in events] == ["settings_changed", "transcripts_changed", "fs_changed"]
    fs_payload = events[-1][1]
    assert fs_payload["refresh"] is True
    assert fs_payload["mode"] == "diff"
    assert fs_payload["token"]
    assert "directories" not in fs_payload
    assert fs_payload["change_summary"]["roots_changed"] == 1
    assert fs_payload["change_summary"]["entries_added"] == 1
    assert fs_payload["change_summary"]["entries_removed"] == 1
    assert "listing_summary" not in fs_payload
    assert webapp.session_files_cache != {}
    assert webapp.transcripts_payload_cache is None


def test_publish_filesystem_ready_event_sends_initial_diff_then_keyframe(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    listed_roots = []
    signatures = [
        (("/repo", ("/repo", "dir", 100, 0, (("one.txt", "file", 100, 10),))),),
        (("/repo", ("/repo", "dir", 200, 0, (("two.txt", "file", 200, 20),))),),
        (("/repo", ("/repo", "dir", 300, 0, (("three.txt", "file", 300, 30),))),),
    ]

    def fake_push_payload(roots):
        listed_roots.append(list(roots))
        return {
            "roots": list(roots),
            "directories": [{"path": root, "status": 200, "ok": True, "data": {"path": root, "entries": []}} for root in roots],
            "listing_summary": {"roots_requested": len(roots), "roots_listed": len(roots), "roots_error": 0, "entries_listed": 0, "files_listed": 0, "dirs_listed": 0},
            "compute_ms": 1.0,
        }

    monkeypatch.setattr(webapp, "filesystem_push_payload", fake_push_payload)
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.publish_filesystem_ready_event(["/repo"], current_signature=signatures[0]) == ["fs_changed"]
        assert webapp.publish_filesystem_ready_event(["/repo"], current_signature=signatures[1]) == ["fs_changed"]
        webapp.client_watch_filesystem_last_full_at = time.monotonic() - app_module.FILESYSTEM_WATCH_KEYFRAME_SECONDS - 1.0
        assert webapp.publish_filesystem_ready_event(["/repo"], current_signature=signatures[2]) == ["fs_changed"]
    finally:
        webapp.control_server.stop()

    assert [payload["mode"] for _event_type, payload in events] == ["full", "diff", "full"]
    assert events[0][1]["directories"]
    assert events[1][1]["refresh"] is True
    assert "directories" not in events[1][1]
    assert events[2][1]["directories"]
    assert listed_roots == [["/repo"], ["/repo"]]


def test_filesystem_watch_diff_payload_lists_only_changed_roots(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    previous = (
        ("/repo", ("/repo", "dir", 100, 0, (("old.txt", "file", 100, 10),))),
        ("/unchanged", ("/unchanged", "dir", 100, 0, (("same.txt", "file", 100, 10),))),
    )
    current = (
        ("/repo", ("/repo", "dir", 200, 0, (("new.txt", "file", 100, 10),))),
        ("/unchanged", ("/unchanged", "dir", 100, 0, (("same.txt", "file", 100, 10),))),
        ("/added-root", ("/added-root", "dir", 100, 0, (("fresh.txt", "file", 100, 10),))),
    )
    listed_roots = []

    def fake_push_payload(roots):
        listed_roots.append(list(roots))
        return {
            "roots": list(roots),
            "directories": [
                {"path": root, "status": 200, "ok": True, "data": {"path": root, "entries": []}}
                for root in roots
            ],
            "listing_summary": {"roots_requested": len(roots), "roots_listed": len(roots), "roots_error": 0, "entries_listed": 0, "files_listed": 0, "dirs_listed": 0},
            "compute_ms": 1.0,
        }

    monkeypatch.setattr(webapp, "filesystem_push_payload", fake_push_payload)
    try:
        since = webapp.record_filesystem_watch_snapshot(previous)
        current_token = webapp.record_filesystem_watch_snapshot(current)
        payload = webapp.filesystem_watch_diff_payload(since)
    finally:
        webapp.control_server.stop()

    assert payload["mode"] == "diff"
    assert payload["since"] == since
    assert payload["token"] == current_token
    assert listed_roots == [["/added-root", "/repo"]]
    assert [item["path"] for item in payload["directories"]] == ["/added-root", "/repo"]
    assert payload["change_summary"]["roots_changed"] == 2


def test_filesystem_watch_diff_payload_returns_full_when_since_is_stale(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    current = (
        ("/repo", ("/repo", "dir", 200, 0, (("new.txt", "file", 100, 10),))),
        ("/unchanged", ("/unchanged", "dir", 100, 0, (("same.txt", "file", 100, 10),))),
    )
    listed_roots = []
    monkeypatch.setattr(webapp, "filesystem_push_payload", lambda roots: listed_roots.append(list(roots)) or {"roots": list(roots), "directories": [], "listing_summary": {}, "compute_ms": 1.0})
    try:
        token = webapp.record_filesystem_watch_snapshot(current)
        payload = webapp.filesystem_watch_diff_payload("missing-token")
    finally:
        webapp.control_server.stop()

    assert payload["mode"] == "full"
    assert payload["reason"] == "stale-since"
    assert payload["token"] == token
    assert listed_roots == [["/repo", "/unchanged"]]


def test_session_files_ready_skips_unchanged_fs_republish(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    events = []
    requests = [{"session": "5", "hours": 24}]
    monkeypatch.setattr(webapp, "client_watch_state_snapshot", lambda: ([], requests, {}))
    monkeypatch.setattr(webapp, "session_files_payload", lambda *args, **kwargs: ({"files": [{"path": "/repo/a.py"}], "repos": [], "errors": [], "cache": {"age": time.time()}}, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.publish_session_files_ready_events(trigger="fs_changed") == ["session_files_ready"]
        assert webapp.publish_session_files_ready_events(trigger="fs_changed") == []
    finally:
        webapp.control_server.stop()

    assert [event_type for event_type, _payload in events] == ["session_files_ready"]


def test_poll_client_events_once_transcript_change_is_lightweight_timing_regression(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp([])
    events = []
    settings_signatures = [("settings", 1), ("settings", 1)]
    transcript_signatures = [("transcripts", 1), ("transcripts", 2)]
    filesystem_signatures = [(("/repo", ("stable",)),), (("/repo", ("stable",)),)]

    def slow_full_transcript_payload():
        time.sleep(0.35)
        return {"sessions": {"slow": {}}}

    monkeypatch.setattr(webapp, "settings_watch_signature", lambda: settings_signatures.pop(0))
    monkeypatch.setattr(webapp, "transcripts_watch_signature", lambda sessions: transcript_signatures.pop(0))
    monkeypatch.setattr(webapp, "filesystem_roots_watch_signature", lambda sessions: filesystem_signatures.pop(0))
    monkeypatch.setattr(webapp, "build_transcripts_payload", slow_full_transcript_payload)
    monkeypatch.setattr(webapp, "publish_context_items_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "publish_activity_summary_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "publish_session_files_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        webapp.transcripts_payload_cache = (1.0, {"sessions": {}})
        assert webapp.poll_client_events_once() == []
        started = time.perf_counter()
        assert webapp.poll_client_events_once() == ["transcripts_changed"]
        elapsed = time.perf_counter() - started
    finally:
        webapp.control_server.stop()

    assert elapsed < 0.2
    assert events == [("transcripts_changed", {"signature": ("transcripts", 2), "refresh": True})]
    assert "data" not in events[0][1]


def test_poll_client_events_once_transcript_content_change_skips_metadata_refresh(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp([])
    settings_signatures = [("settings", 1), ("settings", 1)]
    transcript_signatures = [("transcripts", 1), ("transcripts", 1)]
    transcript_content_signatures = [("content", 1), ("content", 2)]
    filesystem_signatures = [(("/repo", ("stable",)),), (("/repo", ("stable",)),)]
    context_triggers = []
    session_file_triggers = []
    published_events = []
    monkeypatch.setattr(webapp, "settings_watch_signature", lambda: settings_signatures.pop(0))
    monkeypatch.setattr(webapp, "transcripts_watch_signature", lambda sessions: transcript_signatures.pop(0))
    monkeypatch.setattr(webapp, "transcript_content_watch_signature", lambda sessions: transcript_content_signatures.pop(0))
    monkeypatch.setattr(webapp, "filesystem_roots_watch_signature", lambda sessions: filesystem_signatures.pop(0))
    monkeypatch.setattr(webapp, "publish_context_items_ready_events", lambda trigger="watch": context_triggers.append(trigger) or ["context_items_ready"])
    monkeypatch.setattr(webapp, "publish_activity_summary_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "publish_session_files_ready_events", lambda trigger="watch": session_file_triggers.append(trigger) or ["session_files_ready"])
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: published_events.append((event_type, payload or {})))
    try:
        webapp.transcripts_payload_cache = (1.0, {"sessions": {"cached": {}}})
        webapp.transcript_tail_cache = {("tail",): (1.0, "cached")}
        webapp.context_items_cache = {("context",): (1.0, [{"cached": True}])}
        assert webapp.poll_client_events_once() == []
        assert webapp.poll_client_events_once() == ["context_items_ready", "session_files_ready"]
    finally:
        webapp.control_server.stop()

    assert context_triggers == ["transcript_content_changed"]
    assert session_file_triggers == ["transcript_content_changed"]
    assert published_events == []
    assert webapp.transcripts_payload_cache == (1.0, {"sessions": {"cached": {}}})
    assert webapp.transcript_tail_cache == {}
    assert webapp.context_items_cache == {}


def test_poll_client_events_once_refreshes_session_files_on_transcript_change(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp([])
    settings_signatures = [("settings", 1), ("settings", 1)]
    transcript_signatures = [("transcripts", 1), ("transcripts", 2)]
    filesystem_signatures = [(("/repo", ("stable",)),), (("/repo", ("stable",)),)]
    session_file_triggers = []
    monkeypatch.setattr(webapp, "settings_watch_signature", lambda: settings_signatures.pop(0))
    monkeypatch.setattr(webapp, "transcripts_watch_signature", lambda sessions: transcript_signatures.pop(0))
    monkeypatch.setattr(webapp, "filesystem_roots_watch_signature", lambda sessions: filesystem_signatures.pop(0))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(webapp, "publish_context_items_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "publish_activity_summary_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "publish_session_files_ready_events", lambda trigger="watch": session_file_triggers.append(trigger) or ["session_files_ready"])
    try:
        assert webapp.poll_client_events_once() == []
        assert webapp.poll_client_events_once() == ["transcripts_changed", "session_files_ready"]
    finally:
        webapp.control_server.stop()

    assert session_file_triggers == ["transcripts_changed"]


def test_poll_client_file_events_once_publishes_active_file_changes(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    signatures = [
        (("/repo/DOIT.51.md", ("/repo/DOIT.51.md", "file", 100, 10)),),
        (("/repo/DOIT.51.md", ("/repo/DOIT.51.md", "file", 200, 12)),),
    ]
    monkeypatch.setattr(webapp, "files_watch_signature", lambda: signatures.pop(0))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.poll_client_file_events_once() == []
        assert webapp.poll_client_file_events_once() == ["files_changed"]
    finally:
        webapp.control_server.stop()

    assert events == [("files_changed", {"files": [{"path": "/repo/DOIT.51.md", "signature": ("/repo/DOIT.51.md", "file", 200, 12)}], "count": 1})]


def test_poll_client_background_file_events_once_uses_own_signature(monkeypatch):
    events = []
    signatures = [
        (("/repo/README.md", ("/repo/README.md", "file", 100, 10)),),
        (("/repo/README.md", ("/repo/README.md", "file", 200, 12)),),
    ]
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(webapp, "background_files_watch_signature", lambda: signatures.pop(0))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.poll_client_background_file_events_once() == []
        assert webapp.poll_client_background_file_events_once() == ["files_changed"]
    finally:
        webapp.control_server.stop()

    assert events == [("files_changed", {"files": [{"path": "/repo/README.md", "signature": ("/repo/README.md", "file", 200, 12)}], "count": 1})]


def test_filesystem_roots_for_watch_auto_indexes_active_directory(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    src = repo / "src"
    watched = tmp_path / "watched"
    transcript = tmp_path / "transcripts" / "codex.jsonl"
    src.mkdir(parents=True)
    info = SessionInfo(
        session="5",
        panes=[
            PaneInfo(
                session="5",
                window="0",
                pane="0",
                pane_id="%5",
                target="5:0.0",
                current_path=str(src),
                command="codex",
                active=True,
                window_active=True,
                title="codex",
                pid=123,
            )
        ],
        selected_pane=PaneInfo(
            session="5",
            window="0",
            pane="0",
            pane_id="%5",
            target="5:0.0",
            current_path=str(src),
            command="codex",
            active=True,
            window_active=True,
            title="codex",
            pid=123,
        ),
        agents=[
            AgentInfo(
                session="5",
                kind="codex",
                pid=123,
                pane_target="5:1.0",
                command="codex",
                cwd=str(repo),
                status=None,
                session_id="session-5",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    monkeypatch.setattr(app_module, "WATCH_INDEX_PATH", tmp_path / "watch-index.json")
    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"file_explorer": {"companion_dirs": []}}})
    monkeypatch.setattr(app_module.filesystem, "git_root_for_path", lambda path: str(repo) if str(path).startswith(str(repo)) else "")
    webapp = app_module.TmuxWebtermApp([])
    try:
        webapp.update_client_watch_roots({"roots": [str(watched)]})
        roots = webapp.filesystem_roots_for_watch({"5": info})
    finally:
        webapp.control_server.stop()

    assert str(watched) in roots
    assert str(repo) in roots
    assert str(src) not in roots
    assert str(transcript.parent) not in roots


def test_context_items_reuses_transcript_tail_cache(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "Check latency"}}) + "\n", encoding="utf-8")
    info = SessionInfo(
        session="5",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="5",
                kind="codex",
                pid=123,
                pane_target="5:0.0",
                command="codex",
                cwd=str(tmp_path),
                status="running",
                session_id="session-5",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def fake_tail_file_lines(path, lines):
        calls.append((path, lines))
        return transcript.read_text(encoding="utf-8")

    monkeypatch.setattr(app_module, "tail_file_lines", fake_tail_file_lines)
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        first, first_status = webapp.context_items("5", 20)
        second, second_status = webapp.context_items("5", 20)
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert calls == [(transcript, app_module.MAX_TRANSCRIPT_TAIL_LINES)]
    assert first["items"] == second["items"]


def test_yoagent_session_summary_updates_from_transcript_delta(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(
        "\n".join([
            json.dumps({"timestamp": "2026-06-07T10:00:00Z", "payload": {"type": "user_message", "message": "Fix the YO!agent summary table"}}),
            json.dumps({"timestamp": "2026-06-07T10:00:01Z", "payload": {"type": "agent_message", "message": "Added clickable session links."}}),
        ]) + "\n",
        encoding="utf-8",
    )
    info = SessionInfo(
        session="5",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="5",
                kind="codex",
                pid=123,
                pane_target="5:0.0",
                command="codex",
                cwd=str(tmp_path),
                status="running",
                session_id="session-5",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    prompts = []

    def fake_direct_backend(backend, prompt, **_kwargs):
        prompts.append(prompt)
        summary = "state: working\nsummary: Updating YO!agent session summaries from transcript deltas." if len(prompts) == 1 else "state: done\nsummary: Verified the rolling summary update path."
        return summary, "", {"backend": backend, "prompt_chars": len(prompt)}

    monkeypatch.setattr(app_module, "transcript_activity_is_recent", lambda *_args, **_kwargs: False)
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "run_yoagent_direct_prompt_backend", fake_direct_backend)
    settings = {"backend": "codex", "invocation": "cli"}
    try:
        first = webapp.update_yoagent_session_summary("5", info, settings)
        unchanged = webapp.update_yoagent_session_summary("5", info, settings)
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": "2026-06-07T10:05:00Z", "payload": {"type": "agent_message", "message": "Tests now pass."}}) + "\n")
        second = webapp.update_yoagent_session_summary("5", info, settings)
        state = app_module.read_yolomux_state().get(app_module.YOAGENT_SESSION_SUMMARIES_STATE_KEY, {})
    finally:
        webapp.control_server.stop()

    assert first["updated"] is True
    assert unchanged["updated"] is False
    assert unchanged["reason"] == "no new transcript lines"
    assert second["updated"] is True
    assert second["state"] == "done"
    assert "Fix the YO!agent summary table" in prompts[0]
    assert "Tests now pass." not in prompts[0]
    assert "Prior summary:\nUpdating YO!agent session summaries from transcript deltas." in prompts[1]
    assert "Tests now pass." in prompts[1]
    assert "Fix the YO!agent summary table" not in prompts[1]
    assert state["5"]["rolling_summary"] == "Verified the rolling summary update path."


def test_yoagent_session_summary_worker_runs_once_per_server_launch(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    started_threads = []
    ticks = []

    class FakeThread:
        def __init__(self, target, name=None, daemon=False):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            started_threads.append((self.name, self.daemon))
            self.target()

    monkeypatch.setattr(session_summaries_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(webapp, "tick_yoagent_session_summaries", lambda settings=None, **kwargs: ticks.append((settings, kwargs)) or {"enabled": True})
    try:
        webapp.maybe_start_yoagent_summary_worker()
        webapp.maybe_start_yoagent_summary_worker()
    finally:
        webapp.control_server.stop()

    assert started_threads == [("yoagent-summary-first-launch", True)]
    assert ticks == [(webapp.yoagent_settings(), {"force": True})]
    assert webapp.yoagent_summary_first_launch_started is True
    assert webapp.yoagent_summary_worker_running is False


def test_visible_yoagent_launch_starts_first_launch_summary_worker(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    starts = []
    monkeypatch.setattr(webapp, "maybe_start_yoagent_summary_worker", lambda: starts.append("summary"))
    monkeypatch.setattr(app_module, "resolve_yoagent_backend", lambda _requested: "deterministic")
    try:
        background_payload, background_status = webapp.yoagent_prewarm({"visible": False})
        visible_payload, visible_status = webapp.yoagent_prewarm({"visible": True})
    finally:
        webapp.control_server.stop()

    assert background_status == HTTPStatus.OK
    assert background_payload["started"] is False
    assert starts == ["summary"]
    assert visible_status == HTTPStatus.OK
    assert visible_payload["reason"] == "no CLI backend available"


def test_cancel_yoagent_chat_marks_request_and_interrupts_active_backend(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    stream_events = []
    interrupts = []
    monkeypatch.setattr(webapp, "publish_yoagent_stream_delta", lambda *args, **kwargs: stream_events.append((args, kwargs)))
    try:
        event = webapp.yoagent_controller.register_yoagent_chat_request("chat-test", "stream-test", "codex")
        webapp.set_yoagent_chat_request_interrupt("chat-test", lambda: interrupts.append("called") or {"ok": True, "interrupted": True})
        payload, status = webapp.cancel_yoagent_chat("chat-test")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["cancelled"] is True
    assert event.is_set()
    assert interrupts == ["called"]
    assert stream_events == [(("stream-test", ""), {"phase": "stopped", "done": True, "aborted": True, "auxiliary_done": True})]


def test_yoagent_chat_uses_deterministic_fallback(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "sessions": {
            "5": {
                "session": "5",
                "agent": "codex",
                "agent_label": "Codex",
                "active": True,
                "repos": ["/repo/yolomux"],
                "files": {"count": 1, "added": 2, "removed": 0},
                "work": "editor fixes",
                "local": "Codex session 5 is active in yolomux.",
            }
        },
        "errors": [],
    })
    try:
        payload, status = webapp.yoagent_chat({"message": "what is session 5 doing?"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "deterministic"
    assert payload["fallback"] is False
    assert "editor fixes" in payload["answer"]
    assert "tmux session `5`" in payload["context_lines"][1]


def test_yoagent_chat_sends_to_accepting_agent_pane_without_extra_confirmation(monkeypatch):
    pane = PaneInfo(
        session="6",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%6",
        target="%6",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="6",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=123,
                pane_target="%6",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-6",
                transcript="/tmp/claude-session-6.jsonl",
                error=None,
                model="opus",
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["6"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"6": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {
        "generated_at": "2026-06-13T17:40:00+00:00",
        "session_order": ["6"],
        "global": {"headline": "Session 6 is idle."},
        "sessions": {"6": {"local": "Claude session 6 is idle in /repo/app."}},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    pastes = []

    def fake_tmux_paste_text(target, text, submit=False):
        pastes.append((target, text, submit))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(app_module, "tmux_paste_text", fake_tmux_paste_text)
    monkeypatch.setattr(webapp, "run_yoagent_claude_cli", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("visible sends must not use native resume")))

    try:
        payload, status = webapp.yoagent_chat({"message": "wait for session 6 to be done, then ask for date"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "yolomux"
    assert "accepting an AI prompt" in payload["answer"]
    assert "I am sending this exact prompt" in payload["answer"]
    assert "```text\ntell me the date\n```" in payload["answer"]
    assert payload["actions"] == []
    assert pastes == [("%6", "tell me the date", True)]


def test_yoagent_chat_does_not_send_to_agent_waiting_for_question_input(monkeypatch):
    pane = PaneInfo(
        session="1",
        window="1",
        window_name="claude",
        pane="0",
        pane_id="%1",
        target="%1",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="1",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="1",
                kind="claude",
                pid=123,
                pane_target="%1",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-1",
                transcript="/tmp/claude-session-1.jsonl",
                error=None,
                model="opus",
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "needs-input", "text": "Want me to keep using system PT?"}))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {
        "generated_at": "2026-06-13T17:40:00+00:00",
        "session_order": ["1"],
        "global": {"headline": "Session 1 is waiting for input."},
        "sessions": {"1": {"local": "Claude session 1 is waiting in /repo/app."}},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    pastes = []

    def fake_tmux_paste_text(target, text, submit=False):
        pastes.append((target, text, submit))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(app_module, "tmux_paste_text", fake_tmux_paste_text)

    try:
        payload, status = webapp.yoagent_chat({"message": "ask session 1 what it has done today"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "yolomux"
    assert "did not send anything" in payload["answer"]
    assert "asking a question" in payload["answer"]
    assert "I am sending this exact prompt" not in payload["answer"]
    assert "ask session 1 what it has done today" not in payload["answer"]
    assert payload["actions"] == []
    assert pastes == []


def test_yoagent_chat_sends_and_starts_background_result_watch(monkeypatch):
    pane = PaneInfo(
        session="6",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%6",
        target="%6",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="6",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=123,
                pane_target="%6",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-6",
                transcript="/tmp/claude-session-6.jsonl",
                error=None,
                model="opus",
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["6"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"6": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {
        "generated_at": "2026-06-13T17:40:00+00:00",
        "session_order": ["6"],
        "global": {"headline": "Session 6 is idle."},
        "sessions": {"6": {"local": "Claude session 6 is idle in /repo/app."}},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda target, text, submit=False: SimpleNamespace(returncode=0, stdout="", stderr=""))
    watchers = []

    def fake_start_result_watcher(preview, marker):
        watchers.append((preview, marker))
        webapp.register_yoagent_action_wait("wait-1", preview, marker)
        return {"id": "wait-1", "started": True}

    monkeypatch.setattr(webapp, "start_yoagent_action_result_watcher", fake_start_result_watcher)

    try:
        payload, status = webapp.yoagent_chat({"message": "send `date` to tmux session 6"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert "I am awaiting the response" in payload["answer"]
    assert "```text\ndate\n```" in payload["answer"]
    assert watchers
    preview, marker = watchers[0]
    assert preview["return_result"] is True
    assert preview["target"]["transport"] == "tmux-legacy"
    assert preview["target"]["transport_label"] == "legacy tmux pane paste + Return"
    assert preview["target"]["agent_transcript"] == "/tmp/claude-session-6.jsonl"
    assert marker["transcript"] == "/tmp/claude-session-6.jsonl"
    pending_waits = payload["conversation"]["pending_waits"]
    assert len(pending_waits) == 1
    assert pending_waits[0]["id"] == "wait-1"
    assert pending_waits[0]["session"] == "6"
    assert pending_waits[0]["transcript"] == "/tmp/claude-session-6.jsonl"


def test_yoagent_chat_direct_send_can_opt_out_of_background_result_watch(monkeypatch):
    pane = PaneInfo(
        session="6",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%6",
        target="%6",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="6",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=123,
                pane_target="%6",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-6",
                transcript="/tmp/claude-session-6.jsonl",
                error=None,
                model="opus",
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["6"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"6": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {
        "generated_at": "2026-06-13T17:40:00+00:00",
        "session_order": ["6"],
        "global": {"headline": "Session 6 is idle."},
        "sessions": {"6": {"local": "Claude session 6 is idle in /repo/app."}},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda target, text, submit=False: SimpleNamespace(returncode=0, stdout="", stderr=""))
    watchers = []
    monkeypatch.setattr(webapp, "start_yoagent_action_result_watcher", lambda preview, marker: watchers.append((preview, marker)) or {"started": True})

    try:
        payload, status = webapp.yoagent_chat({"message": "send `date` to tmux session 6 but do not wait for the result"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert "I am awaiting the response" not in payload["answer"]
    assert watchers == []


def test_yoagent_managed_transport_result_is_recorded_without_tmux_watcher(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["7"])
    target = {
        "session": "7",
        "pane_target": "%7",
        "agent_kind": "codex",
        "agent_session_id": "thread-7",
        "agent_model": "gpt-5",
        "agent_transcript": "",
        "transport": "codex-sdk",
        "transport_label": "Codex SDK",
        "transport_kind": "managed-session",
        "transport_capabilities": ["sdk"],
        "prompt": {},
        "screen": {"key": "idle", "text": ""},
    }

    class FakeManagedTransport:
        id = "codex-sdk"
        label = "Codex SDK"
        kind = "managed-session"
        capabilities = ("sdk",)

        def send(self, _target, text, **_kwargs):
            assert text == "summarize the diff"
            return transport_module.TransportSendResult(
                ok=True,
                sent=True,
                transport=self.id,
                transport_label=self.label,
                result_source="codex-sdk",
                text="Final managed SDK answer.",
            )

    class FakeRegistry:
        def get(self, _transport):
            return FakeManagedTransport()

    preview = {
        "id": "preview-1",
        "status": "ready",
        "session": "7",
        "text": "summarize the diff",
        "submit": True,
        "return_result": True,
        "target": target,
        "created_ts": app_module.time.time(),
    }
    webapp.yoagent_action_previews["preview-1"] = preview
    webapp.yoagent_transports = FakeRegistry()
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "yoagent_action_acceptance", lambda current: (True, "target agent is accepting an AI prompt"))
    monkeypatch.setattr(webapp, "start_yoagent_action_result_watcher", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("managed transport result should not start tmux watcher")))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})

    try:
        result, status = webapp.execute_yoagent_send_action({"preview_id": "preview-1"}, persist_result=True, start_result_watch=True)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert result["result_recorded"] is True
    assert result["result_source"] == "codex-sdk"
    assert "```text\nsummarize the diff\n```" in result["answer"]
    assert "I am awaiting the response" in result["answer"]
    assert conversation["messages"][0]["content"] == result["answer"]
    assert "Final managed SDK answer." in conversation["messages"][-1]["content"]
    assert "Result from Codex SDK target `7`" in conversation["messages"][-1]["content"]


def test_yoagent_handoff_uses_structured_transport_for_managed_target(monkeypatch):
    pane = PaneInfo(
        session="2",
        window="0",
        window_name="codex",
        pane="0",
        pane_id="%2",
        target="%2",
        current_path="/repo/app",
        command="codex",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="2",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="2",
                kind="codex",
                pid=123,
                pane_target="%2",
                command="codex",
                cwd="/repo/app",
                status=None,
                session_id="codex-session-2",
                transcript="",
                error=None,
                model="gpt-5",
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    webapp.yoagent_managed_targets["codex-session-2"] = {"managed": True, "transport": "codex-sdk"}
    sends = []

    class FakeManagedTransport:
        id = "codex-sdk"
        label = "Codex SDK"
        kind = "managed-session"
        capabilities = ("sdk",)

        def send(self, target, text, **_kwargs):
            sends.append((target, text))
            return transport_module.TransportSendResult(
                ok=True,
                sent=True,
                transport=self.id,
                transport_label=self.label,
                result_source="codex-sdk",
                text="Structured handoff answer.",
            )

    class FakeRegistry:
        managed = FakeManagedTransport()
        tmux = transport_module.TmuxLegacyTransport()

        def get(self, transport):
            return self.managed if transport == "codex-sdk" else self.tmux

        def first_available(self, target):
            return self.managed if target.get("transport") == "codex-sdk" else self.tmux

    source_preview = {
        "session": "1",
        "text": "what changed?",
        "target": {"session": "1", "pane_target": "%1", "agent_kind": "claude", "transport": "tmux-legacy", "transport_label": "legacy tmux pane paste + Return"},
        "handoff": {"source_session": "1", "session": "2", "instruction": "summarize that"},
    }
    webapp.yoagent_transports = FakeRegistry()
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1", "2"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"2": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})

    try:
        result = webapp.continue_yoagent_handoff(source_preview, "Session 1 found three changed files.")
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert result["ok"] is True
    assert sends
    target, text = sends[0]
    assert target["transport"] == "codex-sdk"
    assert "Use this context: Session 1 found three changed files." in text
    assert "Structured handoff answer." in conversation["messages"][-1]["content"]
    assert "Codex SDK target `2`" in conversation["messages"][-1]["content"]


def test_yoagent_action_target_prefers_managed_codex_transport(monkeypatch):
    pane = PaneInfo(
        session="7",
        window="0",
        window_name="codex",
        pane="0",
        pane_id="%7",
        target="%7",
        current_path="/repo/app",
        command="codex",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="7",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="7",
                kind="codex",
                pid=123,
                pane_target="%7",
                command="codex",
                cwd="/repo/app",
                status=None,
                session_id="codex-session-7",
                transcript="/tmp/codex-session-7.jsonl",
                error=None,
                model="gpt-5",
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["7"])
    webapp.yoagent_managed_targets["codex-session-7"] = {"managed": True}
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["7"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"7": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))

    try:
        target, status = webapp.yoagent_action_target("7")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert target["managed"] is True
    assert target["transport"] == "codex-exec"
    assert target["transport_label"] == "Codex exec JSONL"
    assert target["transport_kind"] == "managed-one-shot"
    assert "structured-jsonl" in target["transport_capabilities"]


def test_yoagent_action_result_watcher_appends_transcript_result(monkeypatch, tmp_path):
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text("", encoding="utf-8")
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "claude",
        "agent_transcript": str(transcript),
        "transport": "pane-paste",
    }
    preview = {"session": "6", "text": "tell me the date", "return_result": True, "target": target}
    marker = webapp.yoagent_action_result_marker(target)
    transcript.write_text(
        json.dumps({"timestamp": "2026-06-13T17:41:00Z", "payload": {"type": "agent_message", "message": "The date is June 13, 2026."}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    events = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        result = webapp.run_yoagent_action_result_watcher(preview, marker, wait_seconds=1, poll_seconds=0.01)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert result["ok"] is True
    assert result["source"] == "transcript"
    assert "Result from tmux session `6`" in conversation["messages"][-1]["content"]
    assert "June 13, 2026" in conversation["messages"][-1]["content"]
    assert conversation["messages"][-1]["kind"] == "agent_result"
    assert conversation["messages"][-1]["session"] == "6"
    assert events == [("yoagent_conversation_changed", {"reason": "yoagent_result"})]


def test_yoagent_action_result_watcher_waits_for_claude_final_after_tool_use(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["2"])
    target = {
        "session": "2",
        "pane_target": "%2",
        "agent_kind": "claude",
        "agent_transcript": "/tmp/claude-session-2.jsonl",
        "transport": "pane-paste",
    }
    preview = {"session": "2", "text": "check the time", "return_result": True, "target": target}
    initial_text = json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "18:17:46 + 6 minutes = 18:23:46 PDT. Checking the clock now:"}],
            "stop_reason": "tool_use",
        },
    })
    tool_use = json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "date"}}],
            "stop_reason": "tool_use",
        },
    })
    tool_result = json.dumps({
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "18:17:57"}],
        },
    })
    final_text = json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Final answer: the projected time is 349 seconds ahead of now."}],
            "stop_reason": "end_turn",
        },
    })
    deltas = [
        initial_text,
        "\n".join([initial_text, tool_use]),
        "\n".join([initial_text, tool_use, tool_result]),
        "\n".join([initial_text, tool_use, tool_result, final_text]),
    ]
    calls = {"count": 0}

    def fake_delta(_marker):
        index = min(calls["count"], len(deltas) - 1)
        calls["count"] += 1
        return deltas[index]

    monkeypatch.setattr(webapp, "yoagent_transcript_delta_text", fake_delta)
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        result = webapp.run_yoagent_action_result_watcher(preview, {"transcript": target["agent_transcript"]}, wait_seconds=1, poll_seconds=0.01)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert result["ok"] is True
    assert calls["count"] >= 4
    assert "Final answer" in conversation["messages"][-1]["content"]
    assert "Checking the clock now" not in conversation["messages"][-1]["content"]


def test_yoagent_action_result_watcher_waits_for_codex_task_complete(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["3"])
    target = {
        "session": "3",
        "pane_target": "%3",
        "agent_kind": "codex",
        "agent_transcript": "/tmp/codex-session-3.jsonl",
        "transport": "pane-paste",
    }
    preview = {"session": "3", "text": "check the time", "return_result": True, "target": target}
    started = json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}})
    initial_delta = json.dumps({"type": "event_msg", "payload": {"type": "agent_message_delta", "delta": "I will check the clock now."}})
    tool_call = json.dumps({"type": "event_msg", "payload": {"type": "function_call", "call_id": "call-1", "name": "shell", "arguments": "{\"cmd\":\"date\"}"}})
    tool_output = json.dumps({"type": "event_msg", "payload": {"type": "function_call_output", "call_id": "call-1", "output": "18:17:57"}})
    final_text = json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "Final answer: the projected time is 349 seconds ahead of now."}})
    deltas = [
        "\n".join([started, initial_delta]),
        "\n".join([started, initial_delta, tool_call]),
        "\n".join([started, initial_delta, tool_call, tool_output]),
        "\n".join([started, initial_delta, tool_call, tool_output, final_text]),
    ]
    calls = {"count": 0}

    def fake_delta(_marker):
        index = min(calls["count"], len(deltas) - 1)
        calls["count"] += 1
        return deltas[index]

    monkeypatch.setattr(webapp, "yoagent_transcript_delta_text", fake_delta)
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        result = webapp.run_yoagent_action_result_watcher(preview, {"transcript": target["agent_transcript"]}, wait_seconds=1, poll_seconds=0.01)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert result["ok"] is True
    assert calls["count"] >= 4
    assert "Final answer" in conversation["messages"][-1]["content"]
    assert "I will check the clock now" not in conversation["messages"][-1]["content"]


def test_yoagent_action_result_watcher_does_not_record_visible_composer_draft(monkeypatch):
    visible_text = "\n".join([
        "● The current time is 21:26 (9:26 PM) PDT, Thursday, June 18, 2026 (Pacific Time).",
        "",
        "✻ Cogitated for 7s",
        "",
        "────────────────────────────────────────────────────────────────",
        "❯ what's the date in UTC",
        "────────────────────────────────────────────────────────────────",
        "  ▶▶ bypass permissions on (shift+tab to cycle) · ← for agents",
    ])
    webapp = app_module.TmuxWebtermApp(["1"])
    target = {
        "session": "1",
        "pane_target": "%1",
        "agent_kind": "claude",
        "agent_transcript": "",
        "transport": "pane-paste",
    }
    preview = {"session": "1", "text": "what is the time?", "return_result": True, "target": target}
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: visible_text)
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        assert webapp.yoagent_visible_composer_text(visible_text) == "what's the date in UTC"
        assert webapp.yoagent_action_visible_result_text(target) == ""
        result = webapp.run_yoagent_action_result_watcher(preview, {"transcript": ""}, wait_seconds=1, poll_seconds=0.01)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert result["ok"] is False
    assert result["source"] == ""
    assert result["timed_out"] is True
    assert "did not see a result before the wait timed out" in conversation["messages"][-1]["content"]
    assert "what's the date in UTC" not in conversation["messages"][-1]["content"]
    assert "Partial result" not in conversation["messages"][-1]["content"]


def test_yoagent_action_result_watcher_prefers_edited_files_over_visible_fallback(monkeypatch, tmp_path):
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text("", encoding="utf-8")
    webapp = app_module.TmuxWebtermApp(["1"])
    target = {
        "session": "1",
        "pane_target": "%1",
        "agent_kind": "claude",
        "agent_transcript": str(transcript),
        "cwd": str(tmp_path),
        "transport": "pane-paste",
    }
    preview = {"session": "1", "text": "edit notes", "return_result": True, "target": target}
    marker = webapp.yoagent_action_result_marker(target)
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "notes.md"}}],
            },
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "yoagent_action_visible_result_text", lambda _target: "stale visible pane text")
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        result = webapp.run_yoagent_action_result_watcher(preview, marker, wait_seconds=1, poll_seconds=0.01)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert result == {"ok": True, "session": "1", "source": "edited-files", "timed_out": False}
    assert "Edited files detected after the request" in conversation["messages"][-1]["content"]
    assert f"M {tmp_path / 'notes.md'}" in conversation["messages"][-1]["content"]
    assert "stale visible pane text" not in conversation["messages"][-1]["content"]


def test_yoagent_action_result_watcher_timeout_clears_pending_wait(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    target = {
        "session": "1",
        "pane_target": "%1",
        "agent_kind": "claude",
        "agent_transcript": "",
        "transport": "tmux-legacy",
    }
    preview = {"session": "1", "text": "what is the time?", "return_result": True, "target": target}
    marker = {"transcript": ""}
    events = []
    monkeypatch.setattr(webapp, "yoagent_transcript_delta_text", lambda _marker: "")
    monkeypatch.setattr(webapp, "yoagent_action_visible_result_text", lambda _target: "")
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        webapp.register_yoagent_action_wait("wait-1", preview, marker)
        waiting = webapp.yoagent_conversation_payload()["pending_waits"]
        result = webapp.run_yoagent_action_result_watcher(preview, marker, watch_id="wait-1", wait_seconds=1, poll_seconds=0.01)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert waiting and waiting[0]["id"] == "wait-1"
    assert result == {"ok": False, "session": "1", "source": "", "timed_out": True}
    assert conversation["pending_waits"] == []
    assert "did not see a result before the wait timed out" in conversation["messages"][-1]["content"]
    assert "tmux session `1`" in conversation["messages"][-1]["content"]
    assert conversation["messages"][-1]["kind"] == "agent_result"
    assert events == [
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_started"}),
        ("yoagent_conversation_changed", {"reason": "yoagent_result"}),
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_finished"}),
    ]


def test_yoagent_action_result_watcher_success_clears_pending_wait(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    target = {
        "session": "1",
        "pane_target": "%1",
        "agent_kind": "claude",
        "agent_transcript": "/tmp/claude-session-1.jsonl",
        "transport": "tmux-legacy",
    }
    preview = {"session": "1", "text": "what is the time?", "return_result": True, "target": target}
    marker = {"transcript": "/tmp/claude-session-1.jsonl"}
    final_text = json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Final answer: it is 9:26 PM."}],
            "stop_reason": "end_turn",
        },
    })
    events = []
    monkeypatch.setattr(webapp, "yoagent_transcript_delta_text", lambda _marker: final_text)
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        webapp.register_yoagent_action_wait("wait-1", preview, marker)
        result = webapp.run_yoagent_action_result_watcher(preview, marker, watch_id="wait-1", wait_seconds=1, poll_seconds=0.01)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert result == {"ok": True, "session": "1", "source": "transcript", "timed_out": False}
    assert conversation["pending_waits"] == []
    assert "Final answer: it is 9:26 PM." in conversation["messages"][-1]["content"]
    assert "Partial result" not in conversation["messages"][-1]["content"]
    assert events == [
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_started"}),
        ("yoagent_conversation_changed", {"reason": "yoagent_result"}),
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_finished"}),
    ]


def test_yoagent_action_result_watcher_partial_timeout_clears_pending_wait(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    target = {
        "session": "1",
        "pane_target": "%1",
        "agent_kind": "claude",
        "agent_transcript": "/tmp/claude-session-1.jsonl",
        "transport": "tmux-legacy",
    }
    preview = {"session": "1", "text": "what is the time?", "return_result": True, "target": target}
    marker = {"transcript": "/tmp/claude-session-1.jsonl"}
    events = []
    monkeypatch.setattr(webapp, "yoagent_transcript_delta_text", lambda _marker: "partial transcript delta")
    monkeypatch.setattr(webapp, "yoagent_action_result_text_from_transcript_delta", lambda _delta: "Partial answer before timeout.")
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "working", "text": "still working"}))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        webapp.register_yoagent_action_wait("wait-1", preview, marker)
        result = webapp.run_yoagent_action_result_watcher(preview, marker, watch_id="wait-1", wait_seconds=1, poll_seconds=0.01)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert result == {"ok": True, "session": "1", "source": "transcript", "timed_out": True, "partial": True}
    assert conversation["pending_waits"] == []
    assert "Partial result from tmux session `1`" in conversation["messages"][-1]["content"]
    assert "Partial answer before timeout." in conversation["messages"][-1]["content"]
    assert events == [
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_started"}),
        ("yoagent_conversation_changed", {"reason": "yoagent_result"}),
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_finished"}),
    ]


def test_yoagent_pending_waits_show_and_clear(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "claude",
        "agent_transcript": "/tmp/claude-session-6.jsonl",
        "transport": "pane-paste",
    }
    preview = {"session": "6", "text": "tell me the date", "return_result": True, "target": target}
    marker = {"transcript": "/tmp/claude-session-6.jsonl"}
    events = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))

    try:
        webapp.register_yoagent_action_wait("wait-1", preview, marker)
        waiting = webapp.yoagent_conversation_payload()["pending_waits"]
        webapp.finish_yoagent_action_wait("wait-1", "yoagent_wait_finished")
        cleared = webapp.yoagent_conversation_payload()["pending_waits"]
    finally:
        webapp.control_server.stop()

    assert waiting == [
        {
            "id": "wait-1",
            "session": "6",
            "label": "Waiting for tmux session `6` to reply",
            "started_ts": waiting[0]["started_ts"],
            "wait_seconds": app_module.YOAGENT_ACTION_RESULT_WAIT_SECONDS,
            "transcript": "/tmp/claude-session-6.jsonl",
        }
    ]
    assert cleared == []
    assert events == [
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_started"}),
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_finished"}),
    ]


def test_clear_yoagent_action_wait_uses_existing_wait_store(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "claude",
        "agent_transcript": "/tmp/claude-session-6.jsonl",
        "transport": "pane-paste",
    }
    preview = {"session": "6", "text": "tell me the date", "return_result": True, "target": target}
    marker = {"transcript": "/tmp/claude-session-6.jsonl"}
    events = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))

    try:
        webapp.record_yoagent_message("assistant", "Result from tmux session `6`: done", kind="agent_result", session="6")
        webapp.register_yoagent_action_wait("wait-1", preview, marker)
        payload, status = webapp.clear_yoagent_action_wait("wait-1")
        missing, missing_status = webapp.clear_yoagent_action_wait("wait-1")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["ok"] is True
    assert payload["conversation"]["pending_waits"] == []
    assert payload["conversation"]["messages"][-1]["content"] == "Result from tmux session `6`: done"
    assert missing_status == HTTPStatus.NOT_FOUND
    assert missing["conversation"]["messages"][-1]["content"] == "Result from tmux session `6`: done"
    assert events == [
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_started"}),
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_cleared"}),
    ]


def test_yoagent_pending_waits_multiple_in_flight_coexist_and_clear_independently(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6", "7"])
    preview_one = {
        "session": "6",
        "text": "tell me the date",
        "return_result": True,
        "target": {"session": "6", "pane_target": "%6", "agent_kind": "claude", "agent_transcript": "/tmp/claude-session-6.jsonl", "transport": "pane-paste"},
    }
    preview_two = {
        "session": "7",
        "text": "what time is it?",
        "return_result": True,
        "target": {"session": "7", "pane_target": "%7", "agent_kind": "codex", "agent_transcript": "/tmp/codex-session-7.jsonl", "transport": "pane-paste"},
    }
    events = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        webapp.register_yoagent_action_wait("wait-1", preview_one, {"transcript": "/tmp/claude-session-6.jsonl"})
        webapp.register_yoagent_action_wait("wait-2", preview_two, {"transcript": "/tmp/codex-session-7.jsonl"})
        waiting = webapp.yoagent_conversation_payload()["pending_waits"]
        webapp.record_yoagent_action_result(preview_one, "Session 6 date result.")
        webapp.finish_yoagent_action_wait("wait-1", "yoagent_wait_finished")
        remaining = webapp.yoagent_conversation_payload()["pending_waits"]
        webapp.record_yoagent_action_result(preview_two, "Session 7 time result.")
        webapp.finish_yoagent_action_wait("wait-2", "yoagent_wait_finished")
        conversation = webapp.yoagent_conversation_payload()
        cleared = conversation["pending_waits"]
    finally:
        webapp.control_server.stop()

    assert [item["id"] for item in waiting] == ["wait-1", "wait-2"]
    assert [item["session"] for item in waiting] == ["6", "7"]
    assert [item["transcript"] for item in waiting] == ["/tmp/claude-session-6.jsonl", "/tmp/codex-session-7.jsonl"]
    assert [item["wait_seconds"] for item in waiting] == [app_module.YOAGENT_ACTION_RESULT_WAIT_SECONDS, app_module.YOAGENT_ACTION_RESULT_WAIT_SECONDS]
    assert [item["id"] for item in remaining] == ["wait-2"]
    assert cleared == []
    result_messages = [item for item in conversation["messages"] if item.get("kind") == "agent_result"]
    assert [item["session"] for item in result_messages] == ["6", "7"]
    assert "Session 6 date result." in result_messages[0]["content"]
    assert "Session 7 time result." in result_messages[1]["content"]
    assert events == [
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_started"}),
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_started"}),
        ("yoagent_conversation_changed", {"reason": "yoagent_result"}),
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_finished"}),
        ("yoagent_conversation_changed", {"reason": "yoagent_result"}),
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_finished"}),
    ]


def test_yoagent_handoff_pending_wait_label_includes_regarding(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    preview = {
        "session": "1",
        "text": "what time is it?",
        "return_result": True,
        "target": {
            "session": "1",
            "pane_target": "%1",
            "agent_kind": "claude",
            "agent_transcript": "/tmp/claude-session-1.jsonl",
            "transport": "pane-paste",
        },
        "handoff": {
            "source_session": "1",
            "session": "2",
            "instruction": "add 6 minutes and say how far off that is",
        },
    }
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})

    try:
        webapp.register_yoagent_action_wait("wait-1", preview, {"transcript": "/tmp/claude-session-1.jsonl"})
        waiting = webapp.yoagent_conversation_payload()["pending_waits"]
    finally:
        webapp.control_server.stop()

    assert waiting[0]["label"] == (
        "Waiting for tmux session `1` to respond (regarding what time is it?), before handing off "
        "the next request to tmux session `2` (regarding add 6 minutes and say how far off that is)"
    )
    assert waiting[0]["handoff"] == {
        "source_session": "1",
        "session": "2",
        "source_regarding": "what time is it?",
        "target_regarding": "add 6 minutes and say how far off that is",
    }


def test_yoagent_handoff_sends_to_second_session_and_watches_result(monkeypatch):
    pane1 = PaneInfo(
        session="1",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%1",
        target="%1",
        current_path="/repo/one",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=101,
    )
    pane2 = PaneInfo(
        session="2",
        window="0",
        window_name="codex",
        pane="0",
        pane_id="%2",
        target="%2",
        current_path="/repo/two",
        command="codex",
        active=True,
        window_active=True,
        title="",
        pid=202,
    )
    info1 = SessionInfo(
        session="1",
        panes=[pane1],
        selected_pane=pane1,
        agents=[
            AgentInfo(
                session="1",
                kind="claude",
                pid=101,
                pane_target="%1",
                command="claude",
                cwd="/repo/one",
                status=None,
                session_id="claude-session-1",
                transcript="/tmp/claude-session-1.jsonl",
                error=None,
            )
        ],
    )
    info2 = SessionInfo(
        session="2",
        panes=[pane2],
        selected_pane=pane2,
        agents=[
            AgentInfo(
                session="2",
                kind="codex",
                pid=202,
                pane_target="%2",
                command="codex",
                cwd="/repo/two",
                status=None,
                session_id="codex-session-2",
                transcript="/tmp/codex-session-2.jsonl",
                error=None,
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    preview = {
        "session": "1",
        "text": "what time is it?",
        "return_result": True,
        "target": {
            "session": "1",
            "pane_target": "%1",
            "agent_kind": "claude",
            "agent_transcript": "/tmp/claude-session-1.jsonl",
            "transport": "pane-paste",
        },
        "handoff": {
            "source_session": "1",
            "session": "2",
            "instruction": "take that result, add 35 minutes, and ask session 2 if that is correct",
        },
    }
    sent = []
    watchers = []
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1", "2"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": info1, "2": info2}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda target, text, submit=False: sent.append((target, text, submit)) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(webapp, "start_yoagent_action_result_watcher", lambda action, marker: watchers.append((action, marker)) or {"started": True})

    try:
        result = webapp.continue_yoagent_handoff(preview, "The time is **2026-06-13 Sat 17:35:43 PDT** (Pacific Time).")
    finally:
        webapp.control_server.stop()

    assert result["ok"] is True
    assert sent and sent[0][0] == "%2"
    assert sent[0][2] is True
    assert sent[0][1] == "Is 6:10 PM the correct time now?"
    assert "tmux session `1` replied" not in sent[0][1]
    assert "ask session 2" not in sent[0][1].lower()
    assert watchers
    assert watchers[0][0]["session"] == "2"
    assert watchers[0][0]["return_result"] is True
    assert watchers[0][0]["target"]["agent_transcript"] == "/tmp/codex-session-2.jsonl"


def test_yoagent_handoff_right_time_now_sends_clean_single_question():
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    preview = {
        "session": "2",
        "target": {"session": "2"},
        "handoff": {
            "source_session": "2",
            "session": "1",
            "instruction": "add 10 minutes to it, and ask session 1 if that is the right time now",
        },
    }
    response = "\n".join([
        "It's **11:17 PM PDT** (2026-06-13 Sat, 23:17).",
        "",
        "Worth flagging: my clock jumped from ~6:16 PM to 11:17 PM.",
    ])

    try:
        prompt = webapp.yoagent_handoff_prompt(preview, response)
    finally:
        webapp.control_server.stop()

    assert prompt == "Is 11:27 PM the correct time now?"
    assert "\n" not in prompt
    assert "session 1" not in prompt.lower()


def test_yoagent_generic_handoff_prompt_hides_source_and_target_identity():
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    preview = {
        "session": "1",
        "target": {"session": "1"},
        "handoff": {
            "source_session": "1",
            "session": "2",
            "instruction": "summarize that result and ask session 2 if the risk is real",
        },
    }

    try:
        prompt = webapp.yoagent_handoff_prompt(preview, "The cache invalidation path can drop dirty files.")
    finally:
        webapp.control_server.stop()

    assert prompt == "Use this context: The cache invalidation path can drop dirty files. Task: summarize the context and say if the risk is real."
    assert "\n" not in prompt
    assert "tmux session" not in prompt
    assert "session 1" not in prompt.lower()
    assert "session 2" not in prompt.lower()


def test_yoagent_send_does_not_claim_success_when_text_remains_in_composer(monkeypatch):
    pane = PaneInfo(
        session="1",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%1",
        target="%1",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="1",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="1",
                kind="claude",
                pid=123,
                pane_target="%1",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-1",
                transcript="/tmp/claude-session-1.jsonl",
                error=None,
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda target, text, submit=False: SimpleNamespace(returncode=0, stdout="", stderr=""))
    still_in_composer = "\n".join([
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ Use this context: hello Task: answer.",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: still_in_composer)
    monkeypatch.setattr(app_module, "tmux_capture_pane_styled", lambda target, visible_only=False: "")
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        preview, preview_status = webapp.create_yoagent_action_preview({"type": "send_prompt", "session": "1", "text": "Use this context: hello Task: answer."})
        result, result_status = webapp.execute_yoagent_send_action({"preview_id": preview["id"]}, persist_result=True, start_result_watch=True)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert preview_status == HTTPStatus.OK
    assert result_status == HTTPStatus.CONFLICT
    assert result["sent"] is False
    assert result["pasted"] is True
    assert result["reason_code"] == "unsubmitted"
    assert "still in the target input box" in result["error"]
    assert conversation["messages"] == []


@pytest.mark.parametrize(
    ("changed_key", "changed_value"),
    [
        ("pane_target", "%2"),
        ("agent_kind", "codex"),
        ("agent_session_id", "agent-session-2"),
        ("transport", "codex-sdk"),
    ],
)
def test_yoagent_send_revalidates_target_identity_before_paste(monkeypatch, changed_key, changed_value):
    webapp = app_module.TmuxWebtermApp(["1"])
    base_target = {
        "session": "1",
        "pane_target": "%1",
        "agent_kind": "claude",
        "agent_session_id": "agent-session-1",
        "agent_transcript": "/tmp/claude-session-1.jsonl",
        "transport": "tmux-legacy",
        "transport_label": "legacy tmux pane paste + Return",
        "transport_kind": "terminal",
        "prompt": {},
        "screen": {"key": "idle", "text": ""},
    }
    preview_id = f"preview-stale-{changed_key}"
    webapp.yoagent_action_previews[preview_id] = {
        "id": preview_id,
        "status": "ready",
        "session": "1",
        "text": "what time is it?",
        "submit": True,
        "created_ts": app_module.time.time(),
        "target": dict(base_target),
    }
    current_target = {**base_target, changed_key: changed_value, "screen": {"key": "idle", "text": ""}}
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda _session: (current_target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "yoagent_action_acceptance", lambda _target: (True, "target agent is accepting an AI prompt"))
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale target must not receive paste")))

    try:
        result, status = webapp.execute_yoagent_send_action({"preview_id": preview_id})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.CONFLICT
    assert result["reason_code"] == "stale-target"
    assert result["error"] == "action target changed; create a fresh preview"


def test_yoagent_action_preview_allows_existing_target_composer_text_with_clear(monkeypatch):
    pane = PaneInfo(
        session="1",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%1",
        target="%1",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="1",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="1",
                kind="claude",
                pid=123,
                pane_target="%1",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-1",
                transcript="/tmp/claude-session-1.jsonl",
                error=None,
            )
        ],
    )
    visible_text = "\n".join([
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ Use this context:",
        "",
        "  It's 11:17 PM PDT.",
        "",
        "  Task: add 10 minutes and say if that is right.",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": info}, []))
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: visible_text)

    try:
        preview, status = webapp.create_yoagent_action_preview({"type": "send_prompt", "session": "1", "text": "what time is it?"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert preview["status"] == "ready"
    assert preview["screen"]["key"] == "input-draft"
    assert preview["screen"]["detected_text"] == "Use this context: It's 11:17 PM PDT. Task: add 10 minutes and say if that is right."
    assert preview["screen"]["detected_text_preview"] == "Use this context: It's 11:17 PM PDT. Task: add 10 minutes and say if that is right."
    assert "will clear it before sending" in preview["acceptance_text"]


def test_yoagent_chat_clears_existing_draft_before_send(monkeypatch):
    pane = PaneInfo(
        session="1",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%1",
        target="%1",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="1",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="1",
                kind="claude",
                pid=123,
                pane_target="%1",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-1",
                transcript="/tmp/claude-session-1.jsonl",
                error=None,
            )
        ],
    )
    draft_text = "\n".join([
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ token=secret-value run the release",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    empty_text = "\n".join([
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ ",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    cleared = {"value": False}
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": info}, []))
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: empty_text if cleared["value"] else draft_text)
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {
        "generated_at": "2026-06-13T17:40:00+00:00",
        "session_order": ["1"],
        "global": {"headline": "Session 1 is idle."},
        "sessions": {"1": {"local": "Claude session 1 is idle in /repo/app."}},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    operations = []

    def fake_clear(target):
        operations.append(("clear", target))
        cleared["value"] = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_paste(target, text, submit=False):
        operations.append(("paste", target, text, submit))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(app_module, "tmux_clear_input", fake_clear)
    monkeypatch.setattr(app_module, "tmux_paste_text", fake_paste)

    try:
        payload, status = webapp.yoagent_chat({"message": "ask session 1 what time it is"})
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert "cleared existing target input" in payload["answer"]
    assert "```text\nwhat time is it?\n```" in payload["answer"]
    assert operations == [
        ("clear", "%1"),
        ("paste", "%1", "what time is it?", True),
    ]
    assert "secret-value" not in payload["answer"]
    assert "secret-value" not in payload["details"]
    assert "secret-value" not in json.dumps(conversation)


def test_yoagent_send_refuses_when_existing_draft_does_not_clear(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    target = {
        "session": "1",
        "pane_target": "%1",
        "agent_kind": "claude",
        "agent_model": "opus",
        "agent_transcript": "/tmp/claude-session-1.jsonl",
        "transport": "tmux-legacy",
        "transport_label": "legacy tmux pane paste + Return",
        "transport_kind": "terminal",
        "prompt": {},
        "screen": {"key": "input-draft", "text": "target input box already contains unsent text", "detected_text": "old draft"},
    }
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    draft_text = "\n".join([
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ old draft",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: draft_text)
    monkeypatch.setattr(app_module, "tmux_capture_pane_styled", lambda target, visible_only=False: "")
    monkeypatch.setattr(app_module, "tmux_clear_input", lambda target: SimpleNamespace(returncode=1, stdout="", stderr="target input box did not clear"))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("uncleared draft must not receive paste")))

    try:
        preview, preview_status = webapp.create_yoagent_action_preview({"type": "send_prompt", "session": "1", "text": "what time is it?"})
        result, status = webapp.execute_yoagent_send_action({"preview_id": preview["id"]})
    finally:
        webapp.control_server.stop()

    assert preview_status == HTTPStatus.OK
    assert preview["status"] == "ready"
    assert status == HTTPStatus.CONFLICT
    assert result["sent"] is False
    assert result["cleared_input"] is False
    assert result["reason_code"] == "draft-unclearable"
    assert result["cleared_text_preview"] == "old draft"
    assert "did not clear" in result["error"]


def test_yoagent_claude_try_suggestion_is_idle_and_accepting(monkeypatch):
    visible_text = "\n".join([
        "✻ Welcome back",
        "────────────────────────────────────────────────────────────────",
        "❯ Try \"fix typecheck errors\"",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    webapp = app_module.TmuxWebtermApp(["target-agent"])
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: visible_text)
    info = SessionInfo(session="target-agent", panes=[], selected_pane=None, agents=[])

    try:
        prompt, screen = webapp.yoagent_action_pane_status("target-agent", "%77", discovered_sessions={"target-agent": info})
        accepting, acceptance_text = webapp.yoagent_action_acceptance({
            "agent_kind": "claude",
            "pane_target": "%77",
            "prompt": prompt,
            "screen": screen,
        })
    finally:
        webapp.control_server.stop()

    assert webapp.yoagent_visible_composer_text(visible_text) == ""
    assert screen["key"] == "idle"
    assert screen["text"] == ""
    assert screen["negative_reason"] == "idle composer"
    assert accepting is True
    assert acceptance_text == "target agent is accepting an AI prompt"


def test_yoagent_send_to_claude_try_suggestion_does_not_clear(monkeypatch):
    pane = PaneInfo(
        session="target-agent",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%77",
        target="%77",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="target-agent",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="target-agent",
                kind="claude",
                pid=123,
                pane_target="%77",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-target-agent",
                transcript="/tmp/claude-session-target-agent.jsonl",
                error=None,
            )
        ],
    )
    visible_text = "\n".join([
        "────────────────────────────────────────────────────────────────",
        "❯ Try \"fix typecheck errors\"",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    operations = []
    webapp = app_module.TmuxWebtermApp(["target-agent"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["target-agent"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"target-agent": info}, []))
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: visible_text)
    monkeypatch.setattr(app_module, "tmux_clear_input", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("placeholder must not be cleared")))
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda target, text, submit=False: operations.append(("paste", target, text, submit)) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        preview, preview_status = webapp.create_yoagent_action_preview({"type": "send_prompt", "session": "target-agent", "text": "tell me the date"})
        result, result_status = webapp.execute_yoagent_send_action({"preview_id": preview["id"]})
    finally:
        webapp.control_server.stop()

    assert preview_status == HTTPStatus.OK
    assert preview["screen"]["key"] == "idle"
    assert preview["screen"]["text"] == ""
    assert preview["screen"]["negative_reason"] == "idle composer"
    assert preview["acceptance_text"] == "target agent is accepting an AI prompt"
    assert result_status == HTTPStatus.OK
    assert result["sent"] is True
    assert result.get("cleared_input") is None
    assert operations == [("paste", "%77", "tell me the date", True)]


def test_yoagent_send_to_claude_nbsp_suggestion_does_not_clear(monkeypatch):
    pane_target = "yoagent-test-claude-placeholder-pane"
    pane = PaneInfo(
        session="target-agent",
        window="0",
        window_name="claude",
        pane="0",
        pane_id=pane_target,
        target=pane_target,
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="target-agent",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                    session="target-agent",
                    kind="claude",
                    pid=123,
                    pane_target=pane_target,
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-target-agent",
                transcript="/tmp/claude-session-target-agent.jsonl",
                error=None,
            )
        ],
    )
    visible_text = "\n".join([
        "✻ Baked for 4s",
        "",
        "────────────────────────────────────────────────────────────────",
        "❯\xa0commit the DYN_PARSER_DEBUG change",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
    ])
    operations = []
    webapp = app_module.TmuxWebtermApp(["target-agent"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["target-agent"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"target-agent": info}, []))
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: visible_text)
    monkeypatch.setattr(app_module, "tmux_clear_input", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("placeholder must not be cleared")))
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda target, text, submit=False: operations.append(("paste", target, text, submit)) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    watchers = []
    monkeypatch.setattr(webapp, "start_yoagent_action_result_watcher", lambda preview, marker: watchers.append((preview, marker)) or {"id": "watch-1", "started": True, "wait_seconds": app_module.YOAGENT_ACTION_RESULT_WAIT_SECONDS})

    try:
        preview, preview_status = webapp.create_yoagent_action_preview({"type": "send_prompt", "session": "target-agent", "text": "tell me the date", "return_result": True})
        result, result_status = webapp.execute_yoagent_send_action({"preview_id": preview["id"]}, persist_result=True, start_result_watch=True)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert preview_status == HTTPStatus.OK
    assert preview["screen"]["key"] == "idle"
    assert preview["screen"]["text"] == ""
    assert preview["screen"]["negative_reason"] == "idle composer"
    assert result_status == HTTPStatus.OK
    assert result["sent"] is True
    assert result.get("cleared_input") is None
    assert result["result_watch"]["started"] is True
    assert len(watchers) == 1
    assert "target input box did not clear" not in json.dumps(result)
    assert "target input box did not clear" not in json.dumps(conversation)
    assert operations == [("paste", pane_target, "tell me the date", True)]


def test_yoagent_send_to_codex_dim_suggestion_does_not_clear(monkeypatch):
    pane_target = "yoagent-test-codex-placeholder-pane"
    pane = PaneInfo(
        session="target-agent",
        window="0",
        window_name="codex",
        pane="0",
        pane_id=pane_target,
        target=pane_target,
        current_path="/repo/app",
        command="codex",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="target-agent",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                    session="target-agent",
                    kind="codex",
                    pid=123,
                    pane_target=pane_target,
                command="codex",
                cwd="/repo/app",
                status=None,
                session_id="codex-session-target-agent",
                transcript="/tmp/codex-session-target-agent.jsonl",
                error=None,
            )
        ],
    )
    plain_text = "\n".join([
        "• 2026-06-19 Fri 19:39:00 PDT",
        "",
        "────────────────────────────────────────────────────────────────────────────────────────────────────",
        "",
        "› Summarize recent commits",
        "",
        "  gpt-5.5 xhigh · ~",
    ])
    styled_text = "\n".join([
        "• 2026-06-19 Fri 19:39:00 PDT",
        "",
        "────────────────────────────────────────────────────────────────────────────────────────────────────",
        "",
        "\x1b[0;1m›\x1b[0m \x1b[2mSummarize recent commits",
        "",
        "  gpt-5.5 xhigh · ~",
    ])
    operations = []
    webapp = app_module.TmuxWebtermApp(["target-agent"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["target-agent"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"target-agent": info}, []))
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: plain_text)
    monkeypatch.setattr(app_module, "tmux_capture_pane_styled", lambda target, visible_only=False: styled_text)
    monkeypatch.setattr(app_module, "tmux_clear_input", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("placeholder must not be cleared")))
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda target, text, submit=False: operations.append(("paste", target, text, submit)) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    watchers = []
    monkeypatch.setattr(webapp, "start_yoagent_action_result_watcher", lambda preview, marker: watchers.append((preview, marker)) or {"id": "watch-1", "started": True, "wait_seconds": app_module.YOAGENT_ACTION_RESULT_WAIT_SECONDS})

    try:
        preview, preview_status = webapp.create_yoagent_action_preview({"type": "send_prompt", "session": "target-agent", "text": "tell me the date", "return_result": True})
        result, result_status = webapp.execute_yoagent_send_action({"preview_id": preview["id"]}, persist_result=True, start_result_watch=True)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert preview_status == HTTPStatus.OK
    assert preview["screen"]["key"] == "idle"
    assert preview["screen"]["text"] == ""
    assert result_status == HTTPStatus.OK
    assert result["sent"] is True
    assert result.get("cleared_input") is None
    assert result["result_watch"]["started"] is True
    assert len(watchers) == 1
    assert "target input box did not clear" not in json.dumps(result)
    assert "target input box did not clear" not in json.dumps(conversation)
    assert operations == [("paste", pane_target, "tell me the date", True)]


def test_yoagent_composer_text_ignores_completed_prompt_history():
    visible_text = "\n".join([
        "❯ what time it is",
        "",
        "  Ran 1 shell command",
        "",
        "● It's 11:17 PM PDT (2026-06-13 Sat, 23:17).",
        "",
        "✻ Sautéed for 12s",
        "                                                             new task? /clear to save 967.7k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ ",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    webapp = app_module.TmuxWebtermApp(["2"])
    try:
        assert webapp.yoagent_visible_composer_text(visible_text) == ""
    finally:
        webapp.control_server.stop()


def test_yoagent_composer_text_ignores_submitted_queue_above_blank_prompt():
    visible_text = "\n".join([
        "❯ Queue: change background to white and document agent handoffs",
        "",
        "● Please run /login · API Error: 401 Invalid authentication credentials",
        "",
        "✻ Crunched for 4s · 1 shell still running",
        "                                          new task? /clear to save 328.2k tokens · ◎ /goal active (1d)",
        "────────────────────────────────────────────────────────────────",
        "❯ ",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ accept edits on · 1 shell · ← for agents · ↓ to manage",
    ])
    webapp = app_module.TmuxWebtermApp(["1"])
    try:
        assert webapp.yoagent_visible_composer_text(visible_text) == ""
    finally:
        webapp.control_server.stop()


def test_yoagent_composer_text_ignores_submitted_prompt_waiting_for_output():
    visible_text = "\n".join([
        "Earlier assistant output.",
        "",
        "❯ what time it is",
        "",
    ])
    webapp = app_module.TmuxWebtermApp(["2"])
    try:
        assert webapp.yoagent_visible_composer_text(visible_text) == ""
    finally:
        webapp.control_server.stop()


def test_yoagent_composer_text_keeps_real_multiline_draft():
    visible_text = "\n".join([
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ Use this context:",
        "",
        "  It's 11:17 PM PDT.",
        "",
        "  Task: add 10 minutes and say if that is right.",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    webapp = app_module.TmuxWebtermApp(["1"])
    try:
        assert webapp.yoagent_visible_composer_text(visible_text) == "Use this context: It's 11:17 PM PDT. Task: add 10 minutes and say if that is right."
    finally:
        webapp.control_server.stop()


def test_yoagent_composer_text_keeps_real_claude_draft(monkeypatch):
    visible_text = "\n".join([
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ Write tests for @filename",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: visible_text)
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])

    try:
        prompt, screen = webapp.yoagent_action_pane_status("1", "%1", discovered_sessions={"1": info})
        accepting, acceptance_text = webapp.yoagent_action_acceptance({
            "agent_kind": "claude",
            "pane_target": "%1",
            "prompt": prompt,
            "screen": screen,
        })
    finally:
        webapp.control_server.stop()

    assert webapp.yoagent_visible_composer_text(visible_text) == "Write tests for @filename"
    assert screen["key"] == "input-draft"
    assert screen["detected_text"] == "Write tests for @filename"
    assert accepting is True
    assert acceptance_text == "target input box has unsent text; YO!agent will clear it before sending"


def test_yoagent_composer_text_ignores_nbsp_suggestion_rows():
    claude_text = "\n".join([
        "✻ Baked for 4s",
        "",
        "────────────────────────────────────────────────────────────────",
        "❯\xa0commit the DYN_PARSER_DEBUG change",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
    ])
    codex_text = "\n".join([
        "• Wrote /tmp/hangman.py and verified it.",
        "",
        "\x1b[0;1m›\x1b[0m \x1b[2mSummarize recent commits",
        "",
        "  gpt-5.5 medium · ~",
    ])
    webapp = app_module.TmuxWebtermApp(["1"])
    try:
        assert webapp.yoagent_visible_composer_text(claude_text) == ""
        assert webapp.yoagent_visible_composer_text(codex_text) == ""
    finally:
        webapp.control_server.stop()


def test_yoagent_composer_text_ignores_live_suggestion_captures():
    claude_text = "\n".join([
        "  then popped it — it auto-merged with no conflict despite 3 incoming commits touching the same",
        "  file. The DYN_PARSER_DEBUG const is intact (now at line ~315, shifted by upstream additions), no",
        "  conflict markers.",
        "  - Untracked devcontainer dirs, pyrightconfig.json, and the PARITY.html artifacts are untouched as",
        "  expected.",
        "",
        "✻ Baked for 39s",
        "",
        "❯  tell me the date",
        "",
        "  Ran 1 shell command",
        "",
        "● Today is Friday, 2026-06-19 (19:33 PDT).",
        "",
        "✻ Baked for 4s",
        "",
        "────────────────────────────────────────────────────────────────────────────────────────────────────",
        "❯\xa0commit the DYN_PARSER_DEBUG change",
        "────────────────────────────────────────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
    ])
    codex_text = "\n".join([
        "⚠ `--dangerously-bypass-hook-trust` is enabled. Enabled hooks may run without review for this",
        "  invocation.",
        "",
        "› sleep 10, then get the date",
        "",
        "• I’ll wait 10 seconds, then read the Pacific Time date from the shell.",
        "",
        "• Ran sleep 10; TZ=America/Los_Angeles date '+%Y-%m-%d %a %H:%M:%S %Z'",
        "  └ 2026-06-19 Fri 19:38:01 PDT",
        "",
        "────────────────────────────────────────────────────────────────────────────────────────────────────",
        "",
        "• 2026-06-19 Fri 19:38:01 PDT",
        "",
        "────────────────────────────────────────────────────────────────────────────────────────────────────",
        "",
        "› sleep 10, then get the date",
        "",
        "• I’ll wait 10 seconds again, then read the Pacific Time date.",
        "",
        "• Ran sleep 10; TZ=America/Los_Angeles date '+%Y-%m-%d %a %H:%M:%S %Z'",
        "  └ 2026-06-19 Fri 19:39:00 PDT",
        "",
        "────────────────────────────────────────────────────────────────────────────────────────────────────",
        "",
        "• 2026-06-19 Fri 19:39:00 PDT",
        "",
        "────────────────────────────────────────────────────────────────────────────────────────────────────",
        "",
        "\x1b[0;1m›\x1b[0m \x1b[2mSummarize recent commits",
        "",
        "  gpt-5.5 xhigh · ~",
    ])
    webapp = app_module.TmuxWebtermApp(["1"])
    try:
        assert webapp.yoagent_visible_composer_text(claude_text) == ""
        assert webapp.yoagent_visible_composer_text(codex_text) == ""
    finally:
        webapp.control_server.stop()


def test_yoagent_codex_dim_suggestion_is_idle_and_accepting(monkeypatch):
    plain_text = "\n".join([
        "• 2026-06-19 Fri 19:39:00 PDT",
        "",
        "────────────────────────────────────────────────────────────────────────────────────────────────────",
        "",
        "› Summarize recent commits",
        "",
        "  gpt-5.5 xhigh · ~",
    ])
    styled_text = "\n".join([
        "• 2026-06-19 Fri 19:39:00 PDT",
        "",
        "────────────────────────────────────────────────────────────────────────────────────────────────────",
        "",
        "\x1b[0;1m›\x1b[0m \x1b[2mSummarize recent commits",
        "",
        "  gpt-5.5 xhigh · ~",
    ])
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: plain_text)
    monkeypatch.setattr(app_module, "tmux_capture_pane_styled", lambda target, visible_only=False: styled_text)
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    try:
        prompt, screen = webapp.yoagent_action_pane_status("1", "%1", discovered_sessions={"1": info})
        accepting, acceptance_text = webapp.yoagent_action_acceptance({
            "agent_kind": "codex",
            "pane_target": "%1",
            "prompt": prompt,
            "screen": screen,
        })
    finally:
        webapp.control_server.stop()

    assert webapp.yoagent_visible_composer_text(plain_text) == "Summarize recent commits"
    assert webapp.yoagent_visible_composer_text(styled_text) == ""
    assert screen["key"] == "idle"
    assert screen["text"] == ""
    assert accepting is True
    assert acceptance_text == "target agent is accepting an AI prompt"


def test_yoagent_composer_text_keeps_same_words_when_typed_with_plain_space():
    claude_text = "\n".join([
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ commit the DYN_PARSER_DEBUG change",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    codex_text = "\n".join([
        "• Wrote /tmp/hangman.py and verified it.",
        "",
        "› Summarize recent commits",
        "",
        "  gpt-5.5 medium · ~",
    ])
    webapp = app_module.TmuxWebtermApp(["1"])
    try:
        assert webapp.yoagent_visible_composer_text(claude_text) == "commit the DYN_PARSER_DEBUG change"
        assert webapp.yoagent_visible_composer_text(codex_text) == "Summarize recent commits"
    finally:
        webapp.control_server.stop()


def test_yoagent_composer_text_ignores_numbered_choice_and_approval_rows(monkeypatch):
    numbered_choice = "\n".join([
        "Which backend should I use?",
        "❯ 1. vLLM",
        "  2. SGLang",
        "Enter to select · ↑/↓ to navigate · Esc to cancel",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    approval_text = "\n".join([
        "Would you like to run the following command?",
        "$ python3 tools/check.py",
        "❯ 1. Yes",
        "  2. No",
    ])
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: approval_text)
    monkeypatch.setattr(app_module, "hybrid_approval_prompt_state", lambda *_args, **_kwargs: {"visible": True, "type": "bash", "text": "Would you like to run the following command?", "action": "python3 tools/check.py"})
    monkeypatch.setattr(app_module, "agent_screen_state", lambda _text, **_kwargs: {"key": "approval", "text": "Would you like to run the following command?"})
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    try:
        assert webapp.yoagent_visible_composer_text(numbered_choice) == ""
        prompt, screen = webapp.yoagent_action_pane_status("1", "%1", discovered_sessions={"1": info})
    finally:
        webapp.control_server.stop()

    assert prompt["visible"] is True
    assert screen["key"] == "approval"


def test_yoagent_composer_text_ignores_codex_template_placeholder():
    visible_text = "\n".join([
        "╭─────────────────────────────────────────────╮",
        "│ >_ OpenAI Codex (v0.141.0)                  │",
        "╰─────────────────────────────────────────────╯",
        "",
        "› Implement {feature}",
        "",
        "  gpt-5.5 xhigh · ~/yolomux.dev8001",
    ])
    webapp = app_module.TmuxWebtermApp(["9"])
    try:
        assert webapp.yoagent_visible_composer_text(visible_text) == ""
    finally:
        webapp.control_server.stop()


def test_yoagent_composer_text_keeps_codex_bottom_draft():
    visible_text = "\n".join([
        "• Wrote /tmp/hangman.py and verified it.",
        "",
        "› Write tests for @filename",
        "",
        "  gpt-5.5 medium · ~",
    ])
    webapp = app_module.TmuxWebtermApp(["9"])
    try:
        assert webapp.yoagent_visible_composer_text(visible_text) == "Write tests for @filename"
    finally:
        webapp.control_server.stop()


def test_yoagent_clear_target_composer_ignores_claude_try_placeholder(monkeypatch):
    visible_text = "\n".join([
        "────────────────────────────────────────────────────────────────",
        "❯ Try \"fix typecheck errors\"",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    webapp = app_module.TmuxWebtermApp(["target-agent"])
    clear_calls = []
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: visible_text)
    monkeypatch.setattr(app_module, "tmux_clear_input", lambda target: clear_calls.append(target) or SimpleNamespace(returncode=0, stdout="", stderr=""))

    try:
        result = webapp.yoagent_clear_target_composer({"session": "target-agent", "pane_target": "%77"}, wait_seconds=0)
    finally:
        webapp.control_server.stop()

    assert result == {"ok": True, "cleared": False, "detected_text": ""}
    assert clear_calls == []


def test_yoagent_clear_target_composer_accepts_claude_placeholder_after_clear(monkeypatch):
    draft_text = "\n".join([
        "────────────────────────────────────────────────────────────────",
        "❯ Write tests for @filename",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    placeholder_text = "\n".join([
        "────────────────────────────────────────────────────────────────",
        "❯ Try \"fix typecheck errors\"",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    cleared = {"value": False}
    webapp = app_module.TmuxWebtermApp(["1"])
    clear_calls = []
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: placeholder_text if cleared["value"] else draft_text)

    def fake_clear(target):
        clear_calls.append(target)
        cleared["value"] = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(app_module, "tmux_clear_input", fake_clear)

    try:
        result = webapp.yoagent_clear_target_composer({"session": "1", "pane_target": "%1"}, wait_seconds=0)
    finally:
        webapp.control_server.stop()

    assert result == {"ok": True, "cleared": True, "detected_text": "Write tests for @filename"}
    assert clear_calls == ["%1"]


def test_yoagent_clear_target_composer_accepts_nbsp_suggestion_after_clear(monkeypatch):
    draft_text = "\n".join([
        "────────────────────────────────────────────────────────────────",
        "❯ Write tests for @filename",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    suggestion_text = "\n".join([
        "────────────────────────────────────────────────────────────────",
        "❯\xa0commit the DYN_PARSER_DEBUG change",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    cleared = {"value": False}
    webapp = app_module.TmuxWebtermApp(["1"])
    clear_calls = []
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: suggestion_text if cleared["value"] else draft_text)

    def fake_clear(target):
        clear_calls.append(target)
        cleared["value"] = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(app_module, "tmux_clear_input", fake_clear)

    try:
        result = webapp.yoagent_clear_target_composer({"session": "1", "pane_target": "%1"}, wait_seconds=0)
    finally:
        webapp.control_server.stop()

    assert result == {"ok": True, "cleared": True, "detected_text": "Write tests for @filename"}
    assert clear_calls == ["%1"]


def test_yoagent_clear_target_composer_still_fails_when_real_draft_remains(monkeypatch):
    visible_text = "\n".join([
        "────────────────────────────────────────────────────────────────",
        "❯ Write tests for @filename",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    webapp = app_module.TmuxWebtermApp(["1"])
    clear_calls = []
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: visible_text)
    monkeypatch.setattr(app_module, "tmux_clear_input", lambda target: clear_calls.append(target) or SimpleNamespace(returncode=0, stdout="", stderr=""))

    try:
        result = webapp.yoagent_clear_target_composer({"session": "1", "pane_target": "%1"}, wait_seconds=0)
    finally:
        webapp.control_server.stop()

    assert result["ok"] is False
    assert result["cleared"] is False
    assert result["detected_text"] == "Write tests for @filename"
    assert result["remaining_text"] == "Write tests for @filename"
    assert "did not clear" in result["error"]
    assert clear_calls == ["%1"]


def test_yoagent_chat_preview_only_when_confirmation_requested(monkeypatch):
    pane = PaneInfo(
        session="6",
        window="0",
        window_name="codex",
        pane="0",
        pane_id="%6",
        target="%6",
        current_path="/repo/app",
        command="codex",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="6",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="6",
                kind="codex",
                pid=123,
                pane_target="%6",
                command="codex",
                cwd="/repo/app",
                status=None,
                session_id="codex-session-6",
                transcript="/tmp/codex-session-6.jsonl",
                error=None,
                model="gpt-5",
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["6"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"6": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {
        "generated_at": "2026-06-13T17:40:00+00:00",
        "session_order": ["6"],
        "global": {"headline": "Session 6 is idle."},
        "sessions": {"6": {"local": "Codex session 6 is idle in /repo/app."}},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("confirmation request must not auto-send")))

    try:
        payload, status = webapp.yoagent_chat({"message": "send `date` to tmux session 6, ask me before"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "yolomux"
    assert "confirmed send action" in payload["answer"]
    assert len(payload["actions"]) == 1
    assert payload["actions"][0]["requires_confirmation"] is True
    assert payload["actions"][0]["status"] == "ready"
    assert payload["actions"][0]["target"]["agent_kind"] == "codex"


def test_yoagent_chat_does_not_send_when_target_agent_is_working(monkeypatch):
    pane = PaneInfo(
        session="6",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%6",
        target="%6",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="6",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=123,
                pane_target="%6",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-6",
                transcript="/tmp/claude-session-6.jsonl",
                error=None,
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["6"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"6": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "working", "text": "agent is working"}))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {
        "generated_at": "2026-06-13T17:40:00+00:00",
        "session_order": ["6"],
        "global": {"headline": "Session 6 is working."},
        "sessions": {"6": {"local": "Claude session 6 is working in /repo/app."}},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("working target must not receive paste")))

    try:
        payload, status = webapp.yoagent_chat({"message": "tell session 6 to run date"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "yolomux"
    assert "still working" in payload["answer"]
    assert payload["actions"] == []


def install_fake_yolomux_state(monkeypatch):
    state = {}
    monkeypatch.setattr(app_module, "read_yolomux_state", lambda: dict(state))
    monkeypatch.setattr(app_module, "update_yolomux_state", lambda updates: state.update(updates))
    return state


def test_notify_status_defaults_browser_notifications_off(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp([])

    try:
        assert webapp.notify_status() == {"enabled": False}
    finally:
        webapp.control_server.stop()


def test_notify_status_respects_browser_notifications_opt_in(monkeypatch):
    state = install_fake_yolomux_state(monkeypatch)
    state["notify_enabled"] = True
    webapp = app_module.TmuxWebtermApp([])

    try:
        assert webapp.notify_status() == {"enabled": True}
    finally:
        webapp.control_server.stop()


def test_yoagent_notify_job_create_dedupe_and_cancel(monkeypatch):
    state = install_fake_yolomux_state(monkeypatch)
    events = []
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        payload, status = webapp.create_yoagent_job({"type": "notify_session_idle", "session": "6", "quiet_seconds": 0})
        duplicate, duplicate_status = webapp.create_yoagent_job({"type": "notify_session_idle", "session": "6", "quiet_seconds": 0})
        jobs, jobs_status = webapp.yoagent_jobs_payload()
        cancelled, cancel_status = webapp.cancel_yoagent_job(payload["job"]["id"])
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert duplicate_status == HTTPStatus.CONFLICT
    assert duplicate["duplicate"] is True
    assert jobs_status == HTTPStatus.OK
    assert len(jobs["jobs"]) == 1
    assert cancel_status == HTTPStatus.OK
    assert cancelled["job"]["status"] == "cancelled"
    assert state[app_module.YOAGENT_JOBS_STATE_KEY][payload["job"]["id"]]["status"] == "cancelled"
    assert any(item[0] == "yoagent_jobs_changed" for item in events)


def test_yoagent_wait_then_send_job_fires_when_target_accepts(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    events = []
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "codex",
        "agent_model": "gpt-5",
        "agent_transcript": "/tmp/codex-session-6.jsonl",
        "transport": "pane-paste",
        "prompt": {},
        "screen": {"key": "idle", "text": ""},
    }
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "execute_yoagent_send_action", lambda payload, **_kwargs: ({
        "ok": True,
        "preview_id": payload["preview_id"],
        "transport": "tmux-legacy",
        "result_source": "transcript-or-screen",
        "result_marker": {"transcript": "/tmp/codex-session-6.jsonl", "size": 10},
    }, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        payload, status = webapp.create_yoagent_job({"type": "wait_then_send", "session": "6", "text": "date", "quiet_seconds": 0})
        fired = webapp.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["job"]["job_id"] == payload["job"]["id"]
    assert payload["job"]["prompt"] == "date"
    assert payload["job"]["prompt_preview"] == "date"
    assert payload["job"]["public_text"] == "date"
    assert payload["job"]["transport"] == ""
    assert payload["job"]["result_marker"] == {}
    assert payload["job"]["result_source"] == ""
    assert fired == [payload["job"]["id"]]
    assert jobs["jobs"][0]["status"] == "fired"
    assert jobs["jobs"][0]["started_at"]
    assert jobs["jobs"][0]["transport"] == "tmux-legacy"
    assert jobs["jobs"][0]["result_source"] == "transcript-or-screen"
    assert jobs["jobs"][0]["result_marker"] == {"transcript": "/tmp/codex-session-6.jsonl", "size": 10}
    assert jobs["jobs"][0]["result"]["send"]["ok"] is True
    assert any(item[0] == "yoagent_jobs_changed" and item[1].get("reason") == "yoagent_job_fired" for item in events)


def fake_agent_tui_send_result():
    return SimpleNamespace(
        ok=True,
        sent=True,
        pasted=True,
        cleared=False,
        reason_code="submitted",
        returncode=0,
        error="",
        clear_result=SimpleNamespace(as_dict=lambda: {}),
    )


def test_yoagent_direct_send_uses_tmux_legacy_agent_tui_send(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "codex",
        "agent_model": "gpt-5",
        "agent_session_id": "codex-session-6",
        "agent_transcript": "/tmp/codex-session-6.jsonl",
        "transport": "pane-paste",
        "prompt": {},
        "screen": {"key": "idle", "text": ""},
    }
    send_calls = []
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda _session: (dict(target), HTTPStatus.OK))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        transport_module,
        "send_prompt",
        lambda send_target, text, **kwargs: send_calls.append((send_target, text, kwargs)) or fake_agent_tui_send_result(),
    )
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("direct send must go through agent_tui send_prompt")))

    try:
        preview, preview_status = webapp.create_yoagent_action_preview({"type": "send_prompt", "session": "6", "text": "date"})
        result, status = webapp.execute_yoagent_send_action({"preview_id": preview["id"]})
    finally:
        webapp.control_server.stop()

    assert preview_status == HTTPStatus.OK
    assert status == HTTPStatus.OK
    assert result["sent"] is True
    assert len(send_calls) == 1
    send_target, text, kwargs = send_calls[0]
    assert send_target["pane_target"] == "%6"
    assert text == "date"
    assert kwargs["clear_existing"] is False
    assert kwargs["verify_submit"] is True


def test_yoagent_prompt_answer_uses_verified_selector_path(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    target = {
        "session": "1",
        "pane_target": "%1",
        "agent_kind": "claude",
        "agent_session_id": "claude-session-1",
        "agent_transcript": "/tmp/claude-session-1.jsonl",
        "transport": "pane-paste",
        "transport_label": "legacy tmux pane paste + Return",
        "transport_kind": "terminal",
        "prompt": {"visible": True, "selected_option": 1, "options": [{"text": "Approve"}, {"text": "Reject"}]},
        "screen": {"key": "approval", "text": "Approve this?", "selected_option": 1, "options": [{"text": "Approve"}, {"text": "Reject"}]},
    }
    moved = []
    entered = []
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda _session: (dict(target), HTTPStatus.OK))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(controller_module, "tmux_move_to_option", lambda pane, option, selected_option=None: moved.append((pane, option, selected_option)))
    monkeypatch.setattr(controller_module, "tmux_send_enter", lambda pane: entered.append(pane))
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda _target, visible_only=False: "  1. Approve\n❯ 2. Reject\nEnter to select · ↑/↓ to navigate · Esc to cancel")
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("prompt answers must not paste free text")))

    try:
        preview, preview_status = webapp.create_yoagent_action_preview({"type": "send_prompt", "session": "1", "text": "2"})
        result, status = webapp.execute_yoagent_send_action({"preview_id": preview["id"]}, persist_result=False)
    finally:
        webapp.control_server.stop()

    assert preview_status == HTTPStatus.OK
    assert preview["status"] == "ready"
    assert preview["prompt_answer"]["option"] == 2
    assert status == HTTPStatus.OK
    assert result["prompt_answer"] is True
    assert result["option"] == 2
    assert moved == [("%1", 2, 1)]
    assert entered == ["%1"]


def test_yoagent_controller_reuses_shared_locale_keys(monkeypatch):
    calls = []

    def fake_yoagent_text(locale, key, **params):
        calls.append((key, params))
        return key

    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(controller_module, "yoagent_text", fake_yoagent_text)
    monkeypatch.setattr(webapp, "record_yoagent_message", lambda _role, content, **_kwargs: {"content": content})
    monkeypatch.setattr(webapp, "publish_yoagent_conversation_changed", lambda _reason: None)
    monkeypatch.setattr(webapp, "log_event", lambda *_args, **_kwargs: None)
    target = {
        "prompt": {"visible": True},
        "screen": {"key": "approval"},
    }

    try:
        prefix = webapp.yoagent_controller.yoagent_prompt_answer_error_prefix(target)
        result = webapp.record_yoagent_action_result(
            {"session": "1", "target": {"session": "1", "transport": "tmux-legacy"}},
            "done",
        )
    finally:
        webapp.control_server.stop()

    assert prefix == "yoagent.action.acceptance.approval"
    assert result is not None
    assert ("common.tmuxSession", {"label": "`1`"}) in calls
    assert ("common.result", {}) in calls


def test_yoagent_prompt_target_rejects_free_text_with_options_status(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    target = {
        "session": "1",
        "pane_target": "%1",
        "agent_kind": "claude",
        "agent_session_id": "claude-session-1",
        "agent_transcript": "/tmp/claude-session-1.jsonl",
        "transport": "pane-paste",
        "transport_label": "legacy tmux pane paste + Return",
        "transport_kind": "terminal",
        "prompt": {"visible": True, "selected_option": 1, "options": [{"text": "Pane capture"}, {"text": "Transcript capture"}]},
        "screen": {"key": "needs-input", "text": "Which verifier mode?", "selected_option": 1, "options": [{"text": "Pane capture"}, {"text": "Transcript capture"}]},
    }
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda _session: (dict(target), HTTPStatus.OK))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("prompt targets must not receive free text")))

    try:
        response, status = webapp.yoagent_chat({"message": "tell session 1 to run date"}, access_role="admin")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert "I resolved tmux session `1`, but I did not send anything" in response["answer"]
    assert "answer with an option number, Enter, or Esc" in response["answer"]
    assert "1. Pane capture; 2. Transcript capture" in response["answer"]


def test_yoagent_wait_then_send_job_uses_tmux_legacy_agent_tui_send(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    events = []
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "codex",
        "agent_model": "gpt-5",
        "agent_session_id": "codex-session-6",
        "agent_transcript": "/tmp/codex-session-6.jsonl",
        "transport": "pane-paste",
        "prompt": {},
        "screen": {"key": "idle", "text": ""},
    }
    send_calls = []
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda _session: (dict(target), HTTPStatus.OK))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})
    monkeypatch.setattr(
        transport_module,
        "send_prompt",
        lambda send_target, text, **kwargs: send_calls.append((send_target, text, kwargs)) or fake_agent_tui_send_result(),
    )
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("wait-then-send must go through agent_tui send_prompt")))

    try:
        payload, status = webapp.create_yoagent_job({"type": "wait_then_send", "session": "6", "text": "date", "quiet_seconds": 0})
        fired = webapp.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert fired == [payload["job"]["id"]]
    assert jobs["jobs"][0]["status"] == "fired"
    assert jobs["jobs"][0]["transport"] == "tmux-legacy"
    assert len(send_calls) == 1
    send_target, text, kwargs = send_calls[0]
    assert send_target["pane_target"] == "%6"
    assert text == "date"
    assert kwargs["verify_submit"] is True


def test_yoagent_risky_chat_send_requires_preview_confirmation_and_redacts_secret(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "codex",
        "agent_model": "gpt-5",
        "agent_transcript": "/tmp/codex-session-6.jsonl",
        "transport": "pane-paste",
        "prompt": {},
        "screen": {"key": "idle", "text": ""},
    }
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {"generated_at": "now", "session_order": ["6"], "global": {"headline": "Session 6 is idle."}, "sessions": {}, "errors": []})
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("risky target text must wait for confirmation")))

    try:
        payload, status = webapp.yoagent_chat({"message": "tell session 6 to run token=super-secret-value"})
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["actions"]
    assert payload["actions"][0]["requires_confirmation"] is True
    assert payload["actions"][0]["risk_labels"] == ["secret-like-text"]
    assert payload["actions"][0]["text"] == "token=<redacted>"
    assert "super-secret-value" not in payload["answer"]
    assert "super-secret-value" not in json.dumps(conversation)


def test_yoagent_risky_wait_then_send_job_starts_pending_confirmation_and_redacts(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})

    try:
        payload, status = webapp.create_yoagent_job({"type": "wait_then_send", "session": "6", "text": "api_key=super-secret-value", "quiet_seconds": 0})
        fired = webapp.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["job"]["status"] == "pending_confirmation"
    assert payload["job"]["confirm_required"] is True
    assert payload["job"]["action"]["risk_labels"] == ["secret-like-text"]
    assert payload["job"]["action"]["text"] == "api_key=<redacted>"
    assert payload["job"]["prompt"] == "api_key=<redacted>"
    assert payload["job"]["public_text"] == "api_key=<redacted>"
    assert fired == []
    assert jobs["jobs"][0]["status"] == "pending_confirmation"
    assert "super-secret-value" not in json.dumps(jobs)


def test_yoagent_notify_all_idle_job_tracks_blockers_then_fires(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    events = []
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    states = {"1": "idle", "2": "working"}

    def target(session):
        return {
            "session": session,
            "pane_target": f"%{session}",
            "agent_kind": "codex",
            "transport": "pane-paste",
            "prompt": {},
            "screen": {"key": states[session], "text": states[session]},
        }, HTTPStatus.OK

    monkeypatch.setattr(webapp, "yoagent_action_target", target)
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        payload, status = webapp.create_yoagent_job({"type": "notify_all_idle", "quiet_seconds": 0})
        first = webapp.poll_yoagent_jobs_once()
        waiting, _waiting_status = webapp.yoagent_jobs_payload()
        states["2"] = "idle"
        second = webapp.poll_yoagent_jobs_once()
        fired, _fired_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["job"]["target"]["roster"] == ["1", "2"]
    assert first == []
    assert waiting["jobs"][0]["last_observed_state"]["blockers"] == ["2"]
    assert waiting["jobs"][0]["last_observed_state"]["states"] == {"1": "idle", "2": "working"}
    assert second == [payload["job"]["id"]]
    assert fired["jobs"][0]["status"] == "fired"
    notifications = [
        item[1]["notification"]
        for item in events
        if item[0] == "yoagent_jobs_changed" and isinstance(item[1].get("notification"), dict)
    ]
    assert any(notification.get("body") == "all watched tmux sessions are idle" for notification in notifications)
    assert any(
        notification.get("title_key") == "brand.tab.agent"
        and notification.get("body_key") == "yoagent.job.notification.allIdle"
        and notification.get("body_params") == {}
        for notification in notifications
    )


def test_yoagent_notify_needs_input_and_blocked_jobs_fire_on_prompt_states(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    events = []
    webapp = app_module.TmuxWebtermApp(["6"])
    state = {"screen": "idle", "prompt_visible": False, "question": ""}

    def target(_session):
        return {
            "session": "6",
            "pane_target": "%6",
            "agent_kind": "claude",
            "transport": "pane-paste",
            "prompt": {"visible": state["prompt_visible"], "question_text": state["question"]},
            "screen": {"key": state["screen"], "text": state["question"], "question_text": state["question"]},
        }, HTTPStatus.OK

    monkeypatch.setattr(webapp, "yoagent_action_target", target)
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        needs_input, needs_status = webapp.create_yoagent_job({"type": "notify_session_needs_input", "session": "6", "quiet_seconds": 0})
        blocked, blocked_status = webapp.create_yoagent_job({"type": "notify_session_blocked", "session": "6", "quiet_seconds": 0})
        first = webapp.poll_yoagent_jobs_once()
        state.update({"screen": "needs-input", "question": "Which branch should I use?"})
        needs_fired = webapp.poll_yoagent_jobs_once()
        waiting, _waiting_status = webapp.yoagent_jobs_payload()
        state.update({"screen": "idle", "prompt_visible": True, "question": "Do you want to proceed?"})
        blocked_fired = webapp.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert needs_status == HTTPStatus.OK
    assert blocked_status == HTTPStatus.OK
    assert first == []
    assert needs_fired == [needs_input["job"]["id"]]
    waiting_by_id = {job["id"]: job for job in waiting["jobs"]}
    assert waiting_by_id[needs_input["job"]["id"]]["last_observed_state"]["question_text"] == "Which branch should I use?"
    assert blocked_fired == [blocked["job"]["id"]]
    by_id = {job["id"]: job for job in jobs["jobs"]}
    assert by_id[needs_input["job"]["id"]]["status"] == "fired"
    assert by_id[blocked["job"]["id"]]["status"] == "fired"
    assert any(item[0] == "yoagent_jobs_changed" and item[1].get("notification", {}).get("body") == "tmux session `6` needs input" for item in events)
    assert any(item[0] == "yoagent_jobs_changed" and item[1].get("notification", {}).get("body") == "tmux session `6` is blocked" for item in events)


def test_yoagent_done_after_working_job_requires_working_transition(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    events = []
    webapp = app_module.TmuxWebtermApp(["6"])
    state = {"screen": "idle"}

    def target(_session):
        return {
            "session": "6",
            "pane_target": "%6",
            "agent_kind": "codex",
            "transport": "pane-paste",
            "prompt": {},
            "screen": {"key": state["screen"], "text": state["screen"]},
        }, HTTPStatus.OK

    monkeypatch.setattr(webapp, "yoagent_action_target", target)
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        payload, status = webapp.create_yoagent_job({"type": "notify_session_done_after_working", "session": "6", "quiet_seconds": 0})
        already_idle = webapp.poll_yoagent_jobs_once()
        idle_jobs, _idle_status = webapp.yoagent_jobs_payload()
        state["screen"] = "working"
        working = webapp.poll_yoagent_jobs_once()
        state["screen"] = "idle"
        finished = webapp.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["job"]["predicate"]["type"] == "session_done_after_working"
    assert already_idle == []
    assert idle_jobs["jobs"][0]["last_observed_state"]["seen_working"] is False
    assert working == []
    assert finished == [payload["job"]["id"]]
    assert jobs["jobs"][0]["status"] == "fired"
    assert jobs["jobs"][0]["last_observed_state"]["seen_working"] is True
    assert any(item[0] == "yoagent_jobs_changed" and item[1].get("notification", {}).get("body") == "tmux session `6` finished after working" for item in events)


def test_yoagent_cancel_pending_jobs_by_session(monkeypatch):
    state = install_fake_yolomux_state(monkeypatch)
    events = []
    webapp = app_module.TmuxWebtermApp(["6", "7"])
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        idle, idle_status = webapp.create_yoagent_job({"type": "notify_session_idle", "session": "6"})
        blocked, blocked_status = webapp.create_yoagent_job({"type": "notify_session_blocked", "session": "6"})
        other, other_status = webapp.create_yoagent_job({"type": "notify_session_idle", "session": "7"})
        cancelled, cancel_status = webapp.cancel_yoagent_jobs_for_session("6")
        jobs, _jobs_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert idle_status == HTTPStatus.OK
    assert blocked_status == HTTPStatus.OK
    assert other_status == HTTPStatus.OK
    assert cancel_status == HTTPStatus.OK
    assert cancelled["count"] == 2
    by_id = {job["id"]: job for job in jobs["jobs"]}
    assert by_id[idle["job"]["id"]]["status"] == "cancelled"
    assert by_id[blocked["job"]["id"]]["status"] == "cancelled"
    assert by_id[other["job"]["id"]]["status"] == "queued"
    assert state[app_module.YOAGENT_JOBS_STATE_KEY][idle["job"]["id"]]["status"] == "cancelled"
    assert any(item[0] == "yoagent_jobs_changed" and item[1].get("reason") == "yoagent_jobs_cancelled_for_session" and item[1].get("count") == 2 for item in events)


def test_yoagent_jobs_reload_from_persisted_state(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    first_app = app_module.TmuxWebtermApp(["6"])
    second_app = None
    monkeypatch.setattr(first_app, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(first_app, "publish_client_event", lambda *args, **kwargs: {})

    try:
        payload, status = first_app.create_yoagent_job({"type": "notify_session_idle", "session": "6"})
        second_app = app_module.TmuxWebtermApp(["6"])
        jobs, jobs_status = second_app.yoagent_jobs_payload()
    finally:
        first_app.control_server.stop()
        if second_app is not None:
            second_app.control_server.stop()

    assert status == HTTPStatus.OK
    assert jobs_status == HTTPStatus.OK
    assert jobs["jobs"][0]["id"] == payload["job"]["id"]
    assert jobs["jobs"][0]["status"] == "queued"


def test_yoagent_job_fails_and_notifies_when_target_disappears(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    events = []
    logged = []
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: logged.append((args, kwargs)) or {"time": f"event-{len(logged)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        payload, status = webapp.create_yoagent_job({"type": "wait_then_send", "session": "6", "text": "date", "quiet_seconds": 0})
        monkeypatch.setattr(webapp, "yoagent_action_target", lambda session: ({"error": "unknown session: 6"}, HTTPStatus.NOT_FOUND))
        fired = webapp.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert fired == []
    assert jobs["jobs"][0]["id"] == payload["job"]["id"]
    assert jobs["jobs"][0]["status"] == "failed"
    assert jobs["jobs"][0]["result"]["error"] == "unknown session: 6"
    assert any(item[0] == "yoagent_jobs_changed" and item[1].get("reason") == "yoagent_job_failed" and item[1].get("notification") for item in events)
    failed_args, failed_kwargs = next(item for item in logged if item[0][1] == "yoagent_job_failed")
    assert failed_args[3]["diagnostic"] == "unknown session: 6"
    assert failed_kwargs["message_key"] == "yoagent.job.notification.failed"
    assert failed_kwargs["message_params"]["reason"] == {
        "key": "yoagent.error.targetSessionMissing",
        "params": {},
        "fallback": "The target tmux session no longer exists.",
    }


def test_yoagent_action_preview_blocks_approval_prompt(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "claude",
        "transport": "pane-paste",
        "prompt": {"visible": True, "type": "bash"},
        "screen": {"key": "idle", "text": ""},
    }
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})

    try:
        preview, status = webapp.create_yoagent_action_preview({"type": "send_prompt", "session": "6", "text": "date"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert preview["status"] == "waiting"
    assert preview["acceptance_text"] == "target agent is at an approval prompt; answer with an option number, Enter, or Esc."


def test_yoagent_chat_wait_then_send_queues_job_when_target_is_working(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "codex",
        "agent_model": "gpt-5",
        "agent_transcript": "/tmp/codex-session-6.jsonl",
        "transport": "pane-paste",
        "prompt": {},
        "screen": {"key": "working", "text": "working"},
    }
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {"generated_at": "now", "session_order": ["6"], "global": {"headline": "Session 6 is working."}, "sessions": {}, "errors": []})
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("queued job must not paste now")))

    try:
        payload, status = webapp.yoagent_chat({"message": "wait for session 6 to finish, then tell it to run date"})
        jobs, _jobs_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "yolomux"
    assert "created yo!agent job" in payload["answer"].lower()
    assert len(jobs["jobs"]) == 1
    assert jobs["jobs"][0]["type"] == "wait_then_send"
    assert jobs["jobs"][0]["action"]["text"] == "date"


def test_yoagent_chat_cancels_pending_jobs_for_session(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {"type": "yoagent_jobs_changed"})

    try:
        created, created_status = webapp.create_yoagent_job({"type": "notify_session_idle", "session": "6"})
        payload, status = webapp.yoagent_chat({"message": "cancel pending jobs for session 6"})
        jobs, _jobs_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert created_status == HTTPStatus.OK
    assert status == HTTPStatus.OK
    assert "cancelled 1 pending yo!agent job" in payload["answer"].lower()
    assert jobs["jobs"][0]["id"] == created["job"]["id"]
    assert jobs["jobs"][0]["status"] == "cancelled"


def test_yoagent_capability_question_is_grounded_and_readonly(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "capabilities": app_module.yoagent_capabilities_payload(),
        "sessions": {},
        "errors": [],
    })
    try:
        payload, status = webapp.yoagent_chat({"message": "Can YO!agent read, poll, monitor, notify, and send commands to tmux panes?"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert "can read tmux panes" in payload["answer"]
    assert "poll live session state" in payload["answer"]
    assert "notify when configured transitions" in payload["answer"]
    assert "send explicit target-session requests" in payload["answer"]
    assert "must not ask one target session to contact another directly" in payload["answer"]
    assert "~/.config/yolomux/skills.d/" in payload["answer"]
    assert "verified against a live Claude/Codex prompt" in payload["answer"]
    assert any("capability: YOLOmux can read tmux panes" in line for line in payload["context_lines"])
    assert any("YO!agent can execute explicit target-session sends" in line for line in payload["context_lines"])
    assert any("preserves perspectives" in line and "ask agent 1 to <do ...>" in line for line in payload["context_lines"])
    assert any("background-watches the target transcript" in line for line in payload["context_lines"])
    assert any("manage_user_skills" not in line and "~/.config/yolomux/skills.d/" in line for line in payload["context_lines"])


def test_yoagent_chat_can_update_user_skill_files(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "capabilities": app_module.yoagent_capabilities_payload(),
        "sessions": {},
        "errors": [],
    })
    writes = []

    def fake_write_user_skill_file(kind, name, text):
        writes.append((kind, name, text))
        return {
            "kind": kind,
            "name": name,
            "path": f"/tmp/yolomux/{kind}s.d/{name}.yaml",
            "text": text,
            "valid": True,
        }

    monkeypatch.setattr(app_module, "write_user_skill_file", fake_write_user_skill_file)
    monkeypatch.setattr(webapp, "yoagent_skills_payload", lambda: {"skills": []})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    try:
        payload, status = webapp.yoagent_chat({"message": "create skill local-checks description: Ask idle agents to run focused tests."})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert writes == [("skill", "local-checks", "name: local-checks\nkind: workflow\ndescription: Ask idle agents to run focused tests.\nconfirmation: none")]
    assert "Updated user-local `skill` `local-checks`" in payload["answer"]
    assert "/tmp/yolomux/skills.d/local-checks.yaml" in payload["answer"]


def test_yoagent_cli_auth_failure_is_actionable(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "claude", "invocation": "cli"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "sessions": {},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "run_yoagent_claude_cli", lambda prompt, session_id="", resume=False, **_kwargs: ("", "Error: not logged in. Run claude login."))
    try:
        payload, status = webapp.yoagent_chat({"message": "status?"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend"] == "claude"
    assert payload["backend_used"] == "deterministic"
    assert payload["fallback"] is True
    assert "The Claude CLI backend is not logged in" in payload["fallback_reason"]
    assert "claude auth login" in payload["fallback_reason"]
    assert payload["fallback_reason_key"] == "det.noBackend.noCredentials"
    assert payload["fallback_reason_params"] == {"provider": "Claude CLI", "command": "`claude auth login`"}


def test_yoagent_cli_fallback_localizes_non_auth_error():
    reason = app_module.yoagent_cli_fallback_reason("codex", "model overloaded")
    assert reason == "The Codex CLI backend failed; showing the activity context."


def test_yoagent_direct_backend_keeps_raw_failure_as_cli_diagnostic(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(webapp, "run_yoagent_codex_cli", lambda *_args, **_kwargs: ("", "model overloaded", ""))
    try:
        answer, fallback_reason, cli = webapp.run_yoagent_direct_prompt_backend("codex", "status?", locale="en")
    finally:
        webapp.control_server.stop()

    assert answer == ""
    assert fallback_reason == "The Codex CLI backend failed; showing the activity context."
    assert cli["error"] == "model overloaded"
    assert cli["fallback_reason_message"] == {
        "key": "yoagent.error.backendFailed",
        "params": {"backend": "Codex CLI"},
        "fallback": "The Codex CLI backend failed; showing the activity context.",
    }


def test_resolve_yoagent_backend_auto_prefers_codex_then_claude(monkeypatch):
    # #41: auto resolves to codex first, then claude, then deterministic. A transient unknown auth
    # result still tries the installed provider; only confirmed logged_in=False suppresses it.
    def status(claude_in, codex_in):
        return lambda *a, **k: {
            "claude": {"installed": True, "logged_in": claude_in},
            "codex": {"installed": True, "logged_in": codex_in},
        }

    monkeypatch.setattr(app_module, "agent_auth_status", status(True, True))
    assert app_module.resolve_yoagent_backend("auto") == "codex"
    monkeypatch.setattr(app_module, "agent_auth_status", status(True, False))
    assert app_module.resolve_yoagent_backend("auto") == "claude"
    monkeypatch.setattr(app_module, "agent_auth_status", status(False, False))
    assert app_module.resolve_yoagent_backend("auto") == "deterministic"
    # an installed-but-logged-out codex is skipped in favor of a logged-in claude
    monkeypatch.setattr(app_module, "agent_auth_status", status(True, False))
    assert app_module.resolve_yoagent_backend("auto") == "claude"
    monkeypatch.setattr(app_module, "agent_auth_status", status(False, None))
    assert app_module.resolve_yoagent_backend("auto") == "codex"
    # explicit selections are never auto-resolved
    monkeypatch.setattr(app_module, "agent_auth_status", status(False, False))
    assert app_module.resolve_yoagent_backend("claude") == "claude"
    assert app_module.resolve_yoagent_backend("deterministic") == "deterministic"


def test_yoagent_language_directive_only_for_non_english_locales():
    # Phase 1: a non-English UI locale asks the LLM to answer in that language.
    assert app_module.yoagent_language_directive("zh-Hant") == "\n\n請用繁體中文回答。"
    assert app_module.yoagent_language_directive("zh-Hans") == "\n\n请用简体中文回答。"
    assert app_module.yoagent_language_directive("es") == "\n\nResponde en español."
    assert app_module.yoagent_language_directive("en") == ""
    assert app_module.yoagent_language_directive("en-XA") == ""
    assert app_module.yoagent_language_directive("system") == ""
    assert app_module.yoagent_language_directive("") == ""


def test_yoagent_chat_appends_language_directive_to_the_llm_prompt(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(app_module, "agent_auth_status", lambda *a, **k: {
        "claude": {"installed": True, "logged_in": False},
        "codex": {"installed": True, "logged_in": True},
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "auto", "invocation": "cli"})
    captured = {}

    def fake_codex(prompt, session_id="", resume=False, settings=None, stream_callback=None, request_id=""):
        captured["prompt"] = prompt
        return ("respuesta", "", "s1", {"transport": "codex-app-server", "persistent": True})
    monkeypatch.setattr(webapp, "run_yoagent_codex_app_server", fake_codex)
    try:
        payload, status = webapp.yoagent_chat({"message": "estado?", "locale": "zh-Hant"})
    finally:
        webapp.control_server.stop()
    assert status == HTTPStatus.OK
    assert "你是優!助手" in captured["prompt"]
    assert "優樂mux" in captured["prompt"]
    assert "You are YO!agent" not in captured["prompt"]
    assert "請用繁體中文回答。" in captured["prompt"]


def test_yoagent_chat_auto_runs_logged_in_agent(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(app_module, "agent_auth_status", lambda *a, **k: {
        "claude": {"installed": True, "logged_in": False},
        "codex": {"installed": True, "logged_in": True},
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "auto", "invocation": "cli"})
    monkeypatch.setattr(webapp, "run_yoagent_codex_app_server", lambda prompt, session_id="", resume=False, settings=None, stream_callback=None, request_id="": ("codex answer", "", "codex-session-1", {"transport": "codex-app-server", "persistent": True}))
    try:
        payload, status = webapp.yoagent_chat({"message": "status?"})
    finally:
        webapp.control_server.stop()
    assert payload["backend"] == "auto"
    assert payload["backend_used"] == "codex"
    assert payload["answer"] == "codex answer"


def test_yoagent_chat_serializes_cli_backend_turns(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "claude", "invocation": "cli"})
    monkeypatch.setattr(app_module, "resolve_yoagent_backend", lambda backend: "claude")
    entered_first = threading.Event()
    release_first = threading.Event()
    active_lock = threading.Lock()
    active_count = 0
    max_active = 0
    started_questions: list[str] = []

    def fake_backend(backend, question, activity_payload, settings, history, locale="en", **kwargs):
        nonlocal active_count, max_active
        with active_lock:
            active_count += 1
            max_active = max(max_active, active_count)
            started_questions.append(question)
        if question == "first":
            entered_first.set()
            assert release_first.wait(2)
        with active_lock:
            active_count -= 1
        return f"{question} answer", "", {"session_id": f"{question}-session"}

    monkeypatch.setattr(webapp, "run_yoagent_cli_backend", fake_backend)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(webapp.yoagent_chat, {"message": "first"})
            assert entered_first.wait(1)
            second = executor.submit(webapp.yoagent_chat, {"message": "second"})
            time.sleep(0.05)
            assert started_questions == ["first"]
            release_first.set()
            first_payload, first_status = first.result(timeout=2)
            second_payload, second_status = second.result(timeout=2)
    finally:
        release_first.set()
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert first_payload["answer"] == "first answer"
    assert second_payload["answer"] == "second answer"
    assert started_questions == ["first", "second"]
    assert max_active == 1


def test_yoagent_codex_backend_reuses_persistent_app_server(monkeypatch, tmp_path):
    messages = [
        {"jsonrpc": "2.0", "id": "initialize-1", "result": {}},
        {"jsonrpc": "2.0", "id": "thread-1", "result": {"thread": {"id": "thread-1"}}},
        {"jsonrpc": "2.0", "id": "turn-1", "result": {"turn": {"id": "turn-1", "items": [], "status": "inProgress"}}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "items": [{"type": "agentMessage", "id": "item-1", "text": "first answer"}], "status": "completed"}}},
        {"jsonrpc": "2.0", "id": "turn-2", "result": {"turn": {"id": "turn-2", "items": [], "status": "inProgress"}}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "thread-1", "turn": {"id": "turn-2", "items": [{"type": "agentMessage", "id": "item-2", "text": "second answer"}], "status": "completed"}}},
    ]
    fake_process = FakeCodexAppServerProcess(messages)
    calls = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return fake_process

    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Session 5 is editing YO!agent."},
        "sessions": {},
        "errors": [],
    }
    webapp = app_module.TmuxWebtermApp(["5"])
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("YOLOMUX_CODEX_HOME", str(codex_home))
    monkeypatch.setattr(app_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    monkeypatch.setattr(transport_module.subprocess, "Popen", fake_popen)
    try:
        settings = {"codex_model": "gpt-5.4-mini", "codex_effort": "low"}
        first, first_reason, first_status = webapp.run_yoagent_cli_backend("codex", "first?", activity, settings, [])
        second, second_reason, second_status = webapp.run_yoagent_cli_backend("codex", "second?", activity, settings, [{"role": "user", "content": "first?"}])
        terminated_before_shutdown = fake_process.terminated
    finally:
        webapp.stop_auto_approve_all()

    assert first == "first answer"
    assert second == "second answer"
    assert first_reason == ""
    assert second_reason == ""
    codex_app_server_calls = [call for call in calls if call[0][:4] == ["codex", "app-server", "--listen", "stdio://"]]
    assert len(codex_app_server_calls) == 1
    launch_args, launch_kwargs = codex_app_server_calls[0]
    assert launch_args[:4] == ["codex", "app-server", "--listen", "stdio://"]
    assert 'model_reasoning_effort="low"' in launch_args
    assert 'service_tier="fast"' in launch_args
    assert launch_kwargs["env"]["CODEX_HOME"] == str(codex_home)
    assert launch_kwargs["env"]["TERM"] == "xterm-256color"
    assert launch_kwargs["env"]["NO_COLOR"] == "1"
    assert first_status["transport"] == "codex-app-server"
    assert first_status["persistent"] is True
    assert first_status["process_started"] is True
    assert first_status["thread_started"] is True
    assert first_status["thread_ready_ms"] >= 0
    assert first_status["turn_start_ack_ms"] >= first_status["turn_start_request_ms"] >= 0
    assert first_status["first_stream_event_ms"] >= first_status["turn_start_ack_ms"]
    assert first_status["turn_complete_ms"] >= first_status["turn_start_ack_ms"]
    assert second_status["process_reused"] is True
    assert second_status["thread_started"] is False
    assert second_status["thread_ready_ms"] >= 0
    assert second_status["turn_start_ack_ms"] >= second_status["turn_start_request_ms"] >= 0
    assert second_status["first_stream_event_ms"] >= second_status["turn_start_ack_ms"]
    assert second_status["turn_complete_ms"] >= second_status["turn_start_ack_ms"]
    assert first_status["session_id"] == "thread-1"
    assert second_status["session_id"] == "thread-1"
    assert webapp.yoagent_cli_sessions["codex"]["session_id"] == "thread-1"
    methods = [message["method"] for message in fake_process.stdin.messages]
    assert methods == ["initialize", "initialized", "thread/start", "turn/start", "turn/start"]
    assert fake_process.stdin.messages[2]["params"]["model"] == "gpt-5.4-mini"
    assert "first?" in fake_process.stdin.messages[3]["params"]["input"][0]["text"]
    assert "second?" in fake_process.stdin.messages[4]["params"]["input"][0]["text"]
    assert terminated_before_shutdown is False
    assert fake_process.terminated is True


def test_yoagent_codex_first_ask_reuses_server_start_prewarm(monkeypatch, tmp_path):
    messages = [
        {"jsonrpc": "2.0", "id": "initialize-1", "result": {}},
        {"jsonrpc": "2.0", "id": "thread-1", "result": {"thread": {"id": "thread-1"}}},
        {"jsonrpc": "2.0", "id": "turn-1", "result": {"turn": {"id": "turn-1", "items": [], "status": "inProgress"}}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "items": [{"type": "agentMessage", "id": "item-1", "text": "warm answer"}], "status": "completed"}}},
    ]
    fake_process = FakeCodexAppServerProcess(messages)
    calls = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return fake_process

    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Session 5 is editing YO!agent."},
        "sessions": {},
        "errors": [],
    }
    webapp = app_module.TmuxWebtermApp(["5"])
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("YOLOMUX_CODEX_HOME", str(codex_home))
    monkeypatch.setattr(app_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    monkeypatch.setattr(transport_module.subprocess, "Popen", fake_popen)
    settings = {"backend": "codex", "invocation": "cli", "codex_model": "gpt-5.4-mini", "codex_effort": "low"}
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: dict(settings))
    monkeypatch.setattr(app_module, "resolve_yoagent_backend", lambda backend: "codex")
    try:
        prewarm, prewarm_status = webapp.start_yoagent_backend_prewarm(reason="server_start")
        for _attempt in range(100):
            with webapp.yoagent_prewarm_lock:
                if not webapp.yoagent_prewarm_running:
                    prewarm_state = dict(webapp.yoagent_prewarm_status)
                    break
            time.sleep(0.01)
        else:
            raise AssertionError("prewarm did not finish")
        answer, reason, status = webapp.run_yoagent_cli_backend("codex", "first after idle?", activity, settings, [], include_activity_context=False)
    finally:
        webapp.stop_auto_approve_all()

    assert prewarm_status == HTTPStatus.ACCEPTED
    assert prewarm["started"] is True
    assert prewarm_state["warmed"] is True
    assert prewarm_state["cli"]["process_started"] is True
    assert answer == "warm answer"
    assert reason == ""
    assert status["process_reused"] is True
    assert status["thread_started"] is False
    assert status["session_id"] == "thread-1"
    assert len([call for call in calls if call[0][:4] == ["codex", "app-server", "--listen", "stdio://"]]) == 1
    methods = [message["method"] for message in fake_process.stdin.messages]
    assert methods == ["initialize", "initialized", "thread/start", "turn/start"]


def test_yoagent_codex_backend_falls_back_to_exec_when_app_server_fails(monkeypatch):
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Session 5 is editing YO!agent."},
        "sessions": {},
        "errors": [],
    }
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(app_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    monkeypatch.setattr(transport_module.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("app-server failed")))
    monkeypatch.setattr(webapp, "run_yoagent_codex_cli", lambda prompt, session_id="", resume=False, **_kwargs: ("exec fallback answer", "", "exec-thread"))
    try:
        answer, reason, status = webapp.run_yoagent_cli_backend("codex", "status?", activity, {"codex_model": "gpt-5.4-mini", "codex_effort": "low"}, [])
    finally:
        webapp.stop_auto_approve_all()

    assert answer == "exec fallback answer"
    assert reason == ""
    assert status["transport"] == "codex-exec"
    assert status["persistent"] is False
    assert status["fallback_transport"] == "codex-exec"
    assert "app-server failed" in status["fast_backend_error"]
    assert status["session_id"] == "exec-thread"


def test_yoagent_permission_block_answer_is_preserved(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp, "run_yoagent_claude_cli", lambda prompt, session_id="", resume=False, **_kwargs: ("I'm blocked — the harness denied access to ~/.claude/projects/**/*.jsonl.", ""))
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Your most recent work is about editor fixes."},
        "sessions": {},
        "errors": [],
    }
    try:
        answer, reason, status = webapp.run_yoagent_cli_backend("claude", "status?", activity, {}, [])
    finally:
        webapp.control_server.stop()

    assert answer == "I'm blocked — the harness denied access to ~/.claude/projects/**/*.jsonl."
    assert reason == ""
    assert status["backend"] == "claude"


def test_reset_yoagent_chat_clears_cli_sessions():
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        webapp.yoagent_cli_sessions["claude"] = {"session_id": "old"}
        webapp.record_yoagent_message("user", "persisted question")
        app_module.yoagent_conversation.save_cli_sessions({"claude": {"session_id": "old"}})
        assert app_module.yoagent_conversation.YOAGENT_CONVERSATION_PATH.exists()
        assert app_module.yoagent_conversation.YOAGENT_CLI_STATE_PATH.exists()
        assert webapp.reset_yoagent_chat()["ok"] is True
        assert webapp.yoagent_cli_sessions == {}
        assert not app_module.yoagent_conversation.YOAGENT_CONVERSATION_PATH.exists()
        assert not app_module.yoagent_conversation.YOAGENT_CLI_STATE_PATH.exists()
    finally:
        webapp.control_server.stop()


def test_yoagent_chat_persists_conversation_until_reset(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Session 5 is editing YO!agent."},
        "sessions": {"5": {"local": "Codex session 5 is editing YO!agent."}},
        "errors": [],
    })
    try:
        payload, status = webapp.yoagent_chat({"message": "what changed?"})
        persisted = webapp.yoagent_conversation_payload()
        reset = webapp.reset_yoagent_chat()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert [item["role"] for item in payload["conversation"]["messages"]] == ["user", "assistant"]
    assert payload["conversation"]["messages"][0]["content"] == "what changed?"
    assert persisted["messages"] == payload["conversation"]["messages"]
    assert persisted["transcript_path"].endswith("conversation.jsonl")
    assert reset["conversation"]["messages"] == []


def test_yoagent_prompt_history_prefers_persisted_transcript_over_frontend_history():
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        webapp.record_yoagent_message("user", "persisted question")
        webapp.record_yoagent_message("assistant", "persisted answer")
        history = webapp.yoagent_prompt_history(
            [
                {"role": "user", "content": "stale frontend question"},
                {"role": "assistant", "content": "stale frontend answer"},
            ],
            "next question",
        )
    finally:
        webapp.control_server.stop()

    assert history == [
        {"role": "user", "content": "persisted question"},
        {"role": "assistant", "content": "persisted answer"},
    ]


def test_yoagent_model_chat_appends_history_and_skips_activity_for_simple_followup(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    calls = []
    answers = iter(["first answer", "second answer"])

    def fake_backend(backend, question, activity_payload, settings, history, locale="en", **kwargs):
        calls.append({
            "backend": backend,
            "question": question,
            "activity_payload": activity_payload,
            "history": history,
            "include_activity_context": kwargs.get("include_activity_context"),
        })
        return next(answers), "", {"session_id": "model-session", "prompt_chars": 120}

    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "claude", "invocation": "cli"})
    monkeypatch.setattr(app_module, "resolve_yoagent_backend", lambda backend: "claude")
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("simple follow-up should not build activity context")))
    monkeypatch.setattr(webapp, "run_yoagent_cli_backend", fake_backend)
    try:
        first, first_status = webapp.yoagent_chat({"message": "hello"})
        second, second_status = webapp.yoagent_chat({"message": "what model are you?", "history": [{"role": "user", "content": "stale frontend"}]})
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert first["answer"] == "first answer"
    assert second["answer"] == "second answer"
    assert [message["role"] for message in conversation["messages"]] == ["user", "assistant", "user", "assistant"]
    assert [message["content"] for message in conversation["messages"]] == ["hello", "first answer", "what model are you?", "second answer"]
    assert calls[0]["activity_payload"] == {}
    assert calls[0]["include_activity_context"] is False
    assert calls[0]["history"] == []
    assert calls[1]["activity_payload"] == {}
    assert calls[1]["include_activity_context"] is False
    assert calls[1]["history"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "first answer"},
    ]


def test_yoagent_live_external_data_question_uses_backend_tools(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "claude", "invocation": "cli"})
    monkeypatch.setattr(app_module, "resolve_yoagent_backend", lambda backend: "claude")
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("weather question should not build YOLOmux activity context")))
    calls = []

    def fake_backend(backend, question, activity_payload, settings, history, locale="en", **kwargs):
        calls.append({
            "backend": backend,
            "question": question,
            "activity_payload": activity_payload,
            "include_activity_context": kwargs.get("include_activity_context"),
            "require_external_tools": kwargs.get("require_external_tools"),
        })
        return "It is 72F and clear.", "", {"transport": "claude-stream-json"}

    monkeypatch.setattr(webapp, "run_yoagent_cli_backend", fake_backend)
    try:
        payload, status = webapp.yoagent_chat({"message": "what is the weather in Cupertino now?"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "claude"
    assert payload["answer"] == "It is 72F and clear."
    assert calls == [{
        "backend": "claude",
        "question": "what is the weather in Cupertino now?",
        "activity_payload": {},
        "include_activity_context": False,
        "require_external_tools": True,
    }]
    assert payload["cli"]["tool_capabilities"]["enabled"] is True


def test_yoagent_codex_live_external_data_uses_search_exec(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    calls = []

    def fake_codex_cli(prompt, session_id="", resume=False, settings=None, enable_search=False):
        calls.append({
            "session_id": session_id,
            "resume": resume,
            "settings": dict(settings or {}),
            "enable_search": enable_search,
            "prompt": prompt,
        })
        return "It is 72F and clear.", "", "search-thread"

    monkeypatch.setattr(webapp, "run_yoagent_codex_cli", fake_codex_cli)
    monkeypatch.setattr(webapp, "run_yoagent_codex_app_server", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("live external data must use search-capable codex exec")))
    try:
        answer, reason, status = webapp.run_yoagent_cli_backend(
            "codex",
            "what is the weather in Cupertino now?",
            {},
            {"codex_model": "gpt-5.4-mini", "codex_effort": "low"},
            [],
            stream_id="stream-weather",
            include_activity_context=False,
            require_external_tools=True,
        )
    finally:
        webapp.control_server.stop()

    assert answer == "It is 72F and clear."
    assert reason == ""
    assert calls and calls[0]["enable_search"] is True
    assert calls[0]["session_id"] == ""
    assert calls[0]["resume"] is False
    assert status["transport"] == "codex-exec"
    assert status["external_tools_enabled"] is True
    assert status["web_search_enabled"] is True
    assert status["external_tools_required"] is True


def test_yoagent_live_external_data_question_reports_missing_tools(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(app_module, "resolve_yoagent_backend", lambda backend: "deterministic")
    monkeypatch.setattr(webapp, "run_yoagent_cli_backend", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("missing tools should not call a model backend")))
    try:
        payload, status = webapp.yoagent_chat({"message": "what is the weather in Cupertino now?"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "yolomux"
    assert "no Claude/Codex chat backend is available" in payload["answer"]


def test_yoagent_visible_prewarm_persists_startup_response(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    events = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type, "payload": payload or {}})
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "codex", "invocation": "cli", "codex_model": "gpt-5.4-mini"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Session 5 is editing YO!agent."},
        "sessions": {"5": {"local": "Codex session 5 is editing YO!agent."}},
        "errors": [],
    })
    calls = []

    def fake_backend(backend, question, activity_payload, settings, history, locale="en", stream_id=""):
        calls.append((backend, question, stream_id, activity_payload, settings, history, locale))
        return "Start with the YO!agent streaming fix.", "", {"transport": "codex-app-server", "persistent": True, "elapsed_ms": 12, "prompt_chars": 345}

    monkeypatch.setattr(webapp, "run_yoagent_cli_backend", fake_backend)
    try:
        payload, status = webapp.yoagent_prewarm({"visible": True, "locale": "en"})
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["visible"] is True
    assert payload["answer"] == "Start with the YO!agent streaming fix."
    assert payload["stream_id"].startswith("startup-")
    assert calls and calls[0][0] == "codex"
    assert calls[0][1] == app_module.YOAGENT_STARTUP_QUESTION
    assert calls[0][2] == payload["stream_id"]
    assert [message["role"] for message in conversation["messages"]] == ["assistant"]
    assert conversation["messages"][0]["content"] == "Start with the YO!agent streaming fix."
    assert any(row["key"] == "yoagent.details.modelCliTime" for row in conversation["messages"][0]["detailRows"])
    assert conversation["messages"][0]["responseMs"] > 0
    assert any(event_type == "yoagent_stream_delta" for event_type, _payload in events)
    assert any(event_type == "yoagent_conversation_changed" for event_type, _payload in events)


def test_yoagent_conversation_persists_response_ms(tmp_path):
    path = tmp_path / "conversation.jsonl"

    written = app_module.yoagent_conversation.append_message(
        {
            "role": "assistant",
            "content": "Visible answer",
            "details": "- response time: `5.300s` (`5300.0ms`)",
            "responseMs": 5300,
        },
        path=path,
    )
    loaded = app_module.yoagent_conversation.load_messages(path=path)

    assert written is not None
    assert written["responseMs"] == 5300
    assert loaded == [written]


def test_yoagent_cli_sessions_persist_across_restart(monkeypatch):
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Session 5 is editing YO!agent."},
        "sessions": {},
        "errors": [],
    }
    first_app = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(first_app, "run_yoagent_claude_cli", lambda prompt, session_id="", resume=False, **_kwargs: ("answer", ""))
    try:
        answer, reason, status = first_app.run_yoagent_cli_backend("claude", "status?", activity, {}, [])
        session_id = status["session_id"]
    finally:
        first_app.control_server.stop()

    second_app = app_module.TmuxWebtermApp(["5"])
    try:
        loaded = second_app.yoagent_cli_sessions.get("claude", {})
    finally:
        second_app.control_server.stop()

    assert answer == "answer"
    assert reason == ""
    assert session_id
    assert loaded["session_id"] == session_id


def test_yoagent_cli_backend_resumes_and_trims_context(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    calls = []

    def fake_claude(prompt, session_id="", resume=False, **kwargs):
        calls.append({"prompt": prompt, "session_id": session_id, "resume": resume, **kwargs})
        return ("seeded" if not resume else "resumed", "")

    monkeypatch.setattr(webapp, "run_yoagent_claude_cli", fake_claude)
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "sessions": {
            "5": {
                "agent": "codex",
                "agent_label": "Codex",
                "active": True,
                "repos": ["/repo/yolomux"],
                "files": {"count": 1, "added": 2, "removed": 0},
                "work": "editor fixes",
                "file_lines": ["M static/yolomux.js (+2/-0)"],
            }
        },
        "errors": [],
    }
    try:
        settings = {"claude_model": "claude-haiku-4-5", "claude_effort": "low"}
        first, first_reason, first_status = webapp.run_yoagent_cli_backend("claude", "first?", activity, settings, [])
        second, second_reason, second_status = webapp.run_yoagent_cli_backend("claude", "second?", activity, settings, [{"role": "user", "content": "first?"}])
    finally:
        webapp.control_server.stop()

    assert first == "seeded"
    assert first_reason == ""
    assert second == "resumed"
    assert second_reason == ""
    assert calls[0]["resume"] is False
    assert calls[1]["resume"] is True
    assert calls[0]["session_id"] == calls[1]["session_id"]
    assert calls[0]["model"] == "claude-haiku-4-5"
    assert calls[0]["effort"] == "low"
    assert calls[0]["tools"] == "default"
    assert calls[0]["permission_mode"] == "bypassPermissions"
    assert calls[1]["tools"] == "default"
    assert calls[1]["permission_mode"] == "bypassPermissions"
    assert calls[1]["effort"] == "low"
    assert first_status["seeded"] is True
    assert first_status["external_tools_enabled"] is True
    assert first_status["tools"] == "default"
    assert first_status["permission_mode"] == "bypassPermissions"
    assert second_status["resumed"] is True
    assert second_status["activity_context_forced"] is True
    assert second_status["activity_context_sent"] is True
    assert second_status["context_changed"] is True
    assert "Activity summary changed" in calls[1]["prompt"]
    assert "M static/yolomux.js" in calls[1]["prompt"]


def test_yoagent_codex_resumed_cold_session_receives_context(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    calls = []
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Session 5 is editing YO!agent."},
        "sessions": {
            "5": {
                "agent": "codex",
                "agent_label": "Codex",
                "active": True,
                "repos": ["/repo/yolomux"],
                "files": {"count": 1, "added": 2, "removed": 0},
                "work": "editor fixes",
                "file_lines": ["M static/yolomux.js (+2/-0)"],
            }
        },
        "errors": [],
    }
    signature = app_module.yoagent_activity_payload_signature(activity)
    webapp.yoagent_cli_sessions["codex"] = {
        "session_id": "thread-1",
        "activity_signature": signature,
        "updated_ts": time.time(),
        "updated_monotonic": time.monotonic(),
    }

    def fake_codex(prompt, session_id="", resume=False, **kwargs):
        calls.append({"prompt": prompt, "session_id": session_id, "resume": resume, **kwargs})
        return "answer", "", "thread-1", {"transport": "codex-app-server", "persistent": True}

    monkeypatch.setattr(webapp, "run_yoagent_codex_app_server", fake_codex)
    try:
        answer, reason, status = webapp.run_yoagent_cli_backend("codex", "summarize this project", activity, {}, [])
    finally:
        webapp.control_server.stop()

    assert answer == "answer"
    assert reason == ""
    assert calls[0]["resume"] is True
    assert calls[0]["session_id"] == "thread-1"
    assert "M static/yolomux.js" in calls[0]["prompt"]
    assert status["activity_context_forced"] is True
    assert status["activity_context_sent"] is True
    assert status["context_changed"] is True
    assert webapp.yoagent_cli_sessions["codex"]["context_injected_signature"] == signature


def test_yoagent_cli_backend_does_not_hold_state_lock_during_cli(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    observed = []

    def fake_claude(_prompt, session_id="", resume=False, **_kwargs):
        def probe_lock():
            acquired = webapp.yoagent_cli_lock.acquire(timeout=0.1)
            observed.append(acquired)
            if acquired:
                webapp.yoagent_cli_lock.release()

        thread = threading.Thread(target=probe_lock)
        thread.start()
        thread.join()
        return ("answer", "")

    monkeypatch.setattr(webapp, "run_yoagent_claude_cli", fake_claude)
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "sessions": {},
        "errors": [],
    }
    try:
        answer, reason, status = webapp.run_yoagent_cli_backend("claude", "status?", activity, {}, [])
    finally:
        webapp.control_server.stop()

    assert answer == "answer"
    assert reason == ""
    assert observed == [True]
    assert status["backend"] == "claude"
    assert status["external_tools_enabled"] is True
    assert status["tools"] == "default"
    assert status["permission_mode"] == "bypassPermissions"


def test_codex_event_session_id_extracts_common_shapes():
    assert app_module.codex_event_session_id({"type": "thread.started", "thread_id": "abc"}) == "abc"
    assert app_module.codex_event_session_id({"thread": {"id": "nested"}}) == "nested"


def test_yoagent_codex_cli_persists_then_resumes(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp(["5"])
    codex_home = tmp_path / "codex-home"
    calls = []
    envs = []

    def fake_run(args, input, cwd, env, text, capture_output, timeout, check):
        calls.append(args)
        envs.append(env)
        stdout = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "codex-session"}),
            json.dumps({"type": "agent_message", "text": "answer"}),
        ])
        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    monkeypatch.setattr(app_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("YOLOMUX_CODEX_HOME", str(codex_home))
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    try:
        settings = {"codex_model": "gpt-5.4-mini", "codex_effort": "low"}
        first_answer, first_error, first_session = webapp.run_yoagent_codex_cli("first", resume=False, settings=settings)
        second_answer, second_error, second_session = webapp.run_yoagent_codex_cli("second", session_id=first_session, resume=True, settings=settings)
    finally:
        webapp.control_server.stop()

    assert first_answer == "answer"
    assert first_error == ""
    assert first_session == "codex-session"
    assert second_answer == "answer"
    assert second_error == ""
    assert second_session == "codex-session"
    assert calls[0][:3] == ["codex", "exec", "--json"]
    assert calls[0][calls[0].index("-m") + 1] == "gpt-5.4-mini"
    assert 'model_reasoning_effort="low"' in calls[0]
    assert 'service_tier="fast"' in calls[0]
    assert "--ephemeral" not in calls[0]
    assert "--sandbox" in calls[0]
    assert calls[1][:4] == ["codex", "exec", "resume", "--json"]
    assert "codex-session" in calls[1]
    assert calls[0][calls[0].index("--sandbox") + 1] == "read-only"
    # `codex exec resume` rejects --sandbox/--cd (it restores the original session's cwd + sandbox), so
    # the resume call must NOT pass them — passing them raised "unexpected argument '--sandbox'".
    assert "--sandbox" not in calls[1]
    assert "--cd" not in calls[1]
    assert envs[0]["CODEX_HOME"] == str(codex_home)
    assert envs[0]["TERM"] == "xterm-256color"
    assert envs[0]["NO_COLOR"] == "1"


def test_watched_prs_payload_shapes_result_and_logs_truncation_once(monkeypatch):
    # watched_prs_payload returns {watched_prs, truncated, invalid}.
    # the cap is logged only when the capped state CHANGES — not on every poll.
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp([])
    truncated_box = {"n": 3}
    monkeypatch.setattr(
        app_module,
        "watched_pr_metadata",
        lambda refs, cache, allow_network=True: {
            "watched_prs": [{"ref": "o/r#1", "url": "u", "number": 1, "status_label": "open"}],
            "truncated": truncated_box["n"],
            "invalid": ["bad"],
        },
    )
    events = []
    monkeypatch.setattr(webapp, "log_event", lambda *a, **k: events.append(a))

    payload = webapp.watched_prs_payload(allow_network=False)
    assert payload["watched_prs"][0]["ref"] == "o/r#1"
    assert payload["truncated"] == 3
    assert payload["invalid"] == ["bad"]
    assert "refresh_ms" not in payload
    truncation_events = lambda: [a for a in events if "watched_pr_truncated" in str(a)]
    assert len(truncation_events()) == 1, "logs the truncation on first cap"

    # A second poll with the SAME capped state does NOT log again.
    webapp.watched_prs_payload(allow_network=False)
    assert len(truncation_events()) == 1, "does not re-log an unchanged capped state every poll"

    # A changed truncation count logs a new event.
    truncated_box["n"] = 5
    webapp.watched_prs_payload(allow_network=False)
    assert len(truncation_events()) == 2, "a changed capped state logs again"


def test_apply_upload_subdir_defaults_to_dot_uploads(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"subdir": ".uploads"}}})
        target = webapp._apply_upload_subdir(tmp_path)
        assert target == tmp_path / ".uploads"
        assert target.is_dir()
    finally:
        webapp.control_server.stop()


def test_apply_upload_subdir_empty_writes_into_cwd(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"subdir": ""}}})
        assert webapp._apply_upload_subdir(tmp_path) == tmp_path
    finally:
        webapp.control_server.stop()


def test_apply_upload_subdir_rejects_escaping_value(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"subdir": "../escape"}}})
        assert webapp._apply_upload_subdir(tmp_path) == tmp_path
        assert not (tmp_path.parent / "escape").exists()
    finally:
        webapp.control_server.stop()


def test_apply_upload_subdir_falls_back_when_uncreatable(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"subdir": ".uploads"}}})
        base = tmp_path / "afile"
        base.write_text("not a dir", encoding="utf-8")
        assert webapp._apply_upload_subdir(base) == base
    finally:
        webapp.control_server.stop()


def test_editor_upload_defaults_to_sibling_dot_uploads(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp([])
    docs = tmp_path / "docs"
    docs.mkdir()
    editor_path = docs / "note.md"
    editor_path.write_text("# Note\n", encoding="utf-8")
    try:
        monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"subdir": ".uploads", "filename_template": "{name}{ext}"}}})
        payload, status = webapp.upload_editor_files([UploadedFile(filename="screen.png", content=b"png")], editor_path=str(editor_path))
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["target_dir"] == str(docs / ".uploads")
    assert payload["base_dir"] == str(docs)
    assert payload["files"][0]["relative_path"] == ".uploads/screen.png"
    assert (docs / ".uploads" / "screen.png").read_bytes() == b"png"


def test_editor_upload_empty_subdir_writes_next_to_markdown(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp([])
    docs = tmp_path / "docs"
    docs.mkdir()
    editor_path = docs / "note.md"
    editor_path.write_text("# Note\n", encoding="utf-8")
    try:
        monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"subdir": "", "filename_template": "{name}{ext}"}}})
        payload, status = webapp.upload_editor_files([UploadedFile(filename="screen.png", content=b"png")], editor_path=str(editor_path))
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["target_dir"] == str(docs)
    assert payload["files"][0]["relative_path"] == "screen.png"
    assert (docs / "screen.png").read_bytes() == b"png"


def test_editor_upload_escaping_subdir_is_ignored(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp([])
    docs = tmp_path / "docs"
    docs.mkdir()
    try:
        monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"subdir": "../escape", "filename_template": "{name}{ext}"}}})
        payload, status = webapp.upload_editor_files([UploadedFile(filename="../screen.png", content=b"png")], base_dir=str(docs))
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["target_dir"] == str(docs)
    assert payload["files"][0]["saved_name"] == "screen.png"
    assert payload["files"][0]["relative_path"] == "screen.png"
    assert not (tmp_path / "escape").exists()
    assert (docs / "screen.png").read_bytes() == b"png"


def test_editor_upload_filenames_are_sanitized_and_unique(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp([])
    docs = tmp_path / "docs"
    docs.mkdir()
    uploads = docs / ".uploads"
    uploads.mkdir()
    (uploads / "screen-001.png").write_bytes(b"old")
    try:
        monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"subdir": ".uploads", "filename_template": "{name}-{seq}{ext}"}}})
        payload, status = webapp.upload_editor_files([UploadedFile(filename="../../screen.png", content=b"new")], base_dir=str(docs))
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["files"][0]["saved_name"] == "screen-002.png"
    assert payload["files"][0]["saved_name"].endswith(".png")
    assert payload["files"][0]["relative_path"] == ".uploads/screen-002.png"
    assert (uploads / "screen-001.png").read_bytes() == b"old"
    assert Path(payload["files"][0]["path"]).read_bytes() == b"new"


def test_self_update_dryrun_is_noop_with_plan():
    webapp = app_module.TmuxWebtermApp(["1"])
    result = webapp.perform_self_update(dryrun=True)
    assert result["ok"] is True
    assert result["dryrun"] is True
    assert result["restarting"] is False
    assert result["error"] == "dryrun: nothing pulled, server not restarted"
    assert result["user_message"] == {
        "key": "update.result.dryRun",
        "params": {},
        "fallback": "dryrun: nothing pulled, server not restarted",
    }
    assert any("git pull" in step for step in result["plan"])


def test_self_update_requires_xterm_assets_before_restart(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp.__new__(app_module.TmuxWebtermApp)
    monkeypatch.setattr(app_module.common, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_module.common, "git", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(app_module, "ensure_xterm_runtime_assets", lambda _root: (False, "npm is required to install xterm runtime assets"))
    monkeypatch.setattr(webapp, "_spawn_self_restart", lambda: (_ for _ in ()).throw(AssertionError("must not restart without xterm assets")))

    result = webapp.perform_self_update()

    assert result["ok"] is False
    assert result["restarting"] is False
    assert "xterm" in result["error"]
    assert result["user_message"]["key"] == "update.result.assetsUnavailable"
    assert result["user_message"]["fallback"] == result["error"]
    assert any("xterm assets" in step for step in result["plan"])


def test_self_update_restarts_after_xterm_assets_are_ready(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp.__new__(app_module.TmuxWebtermApp)
    calls = []
    monkeypatch.setattr(app_module.common, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_module.common, "git", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(app_module, "ensure_xterm_runtime_assets", lambda root: calls.append(root) or (True, ""))
    monkeypatch.setattr(app_module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(webapp, "_spawn_self_restart", lambda: True)

    result = webapp.perform_self_update()

    assert result["ok"] is True
    assert result["restarting"] is True
    assert result["error"] == "updated; restarting now"
    assert result["user_message"]["key"] == "update.result.restarting"
    assert calls == [str(tmp_path)]


def test_visible_session_and_upload_errors_keep_diagnostics_with_locale_keys(monkeypatch):
    webapp = app_module.TmuxWebtermApp.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["1", "2"]
    webapp.refresh_sessions = lambda maintenance=True: []

    invalid_window, invalid_window_status = webapp.tmux_select_window("1", "bad")
    renamed, renamed_status = webapp.rename_session("1", "2")
    monkeypatch.setattr(app_module, "tmux_has_exact_session", lambda _session: False)
    missing, missing_status = webapp.ensure_session("1")
    monkeypatch.setattr(app_module, "available_agent_commands", lambda: [])
    unavailable, unavailable_status = webapp.create_next_session("codex")
    no_files, no_files_status = webapp.upload_editor_files([])

    assert invalid_window_status == HTTPStatus.BAD_REQUEST
    assert invalid_window["error"] == "window must be a non-negative integer"
    assert invalid_window["user_message"]["key"] == "terminal.window.invalidNumber"
    assert renamed_status == HTTPStatus.CONFLICT
    assert renamed["error"] == "session already exists: 2"
    assert renamed["user_message"] == {
        "key": "rename.error.exists",
        "params": {"name": "2"},
        "fallback": "session already exists: 2",
    }
    assert missing_status == HTTPStatus.NOT_FOUND
    assert missing["error"] == "session no longer exists: 1"
    assert missing["user_message"]["key"] == "status.sessionEnded"
    assert unavailable_status == HTTPStatus.NOT_FOUND
    assert unavailable["error"] == "codex is not available on this server PATH"
    assert unavailable["user_message"]["key"] == "session.error.agentUnavailablePath"
    assert no_files_status == HTTPStatus.BAD_REQUEST
    assert no_files["error"] == "no files supplied"
    assert no_files["user_message"]["key"] == "upload.error.noFiles"


def test_ensure_xterm_runtime_assets_downloads_static_fallback_without_npm(monkeypatch, tmp_path):
    downloads = []

    def fake_which(name):
        return "/usr/bin/curl" if name == "curl" else None

    def fake_run(args, **_kwargs):
        downloads.append(args)
        output = Path(args[args.index("--output") + 1])
        output.write_text("asset", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(app_module.shutil, "which", fake_which)
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    assert app_module.ensure_xterm_runtime_assets(tmp_path) == (True, "")
    assert app_module.xterm_runtime_assets_ready(tmp_path) is True
    assert [args[-1] for args in downloads] == [details["url"] for details in app_module.XTERM_RUNTIME_ASSETS.values()]


def _self_restart_context(monkeypatch, tmp_path, argv, *, main_module_name=None):
    checkout_root = tmp_path / "xyz"
    checkout_root.mkdir()
    (checkout_root / "yolomux.py").write_text("from yolomux_lib.cli import main\n", encoding="utf-8")
    monkeypatch.setattr(app_module.common, "PROJECT_ROOT", checkout_root)
    monkeypatch.setattr(app_module.sys, "argv", list(argv))
    monkeypatch.setattr(app_module.sys, "executable", "/usr/bin/python3")
    if main_module_name:
        monkeypatch.setattr(
            app_module.sys.modules["__main__"],
            "__spec__",
            SimpleNamespace(name=main_module_name),
            raising=False,
        )
    webapp = app_module.TmuxWebtermApp.__new__(app_module.TmuxWebtermApp)
    return checkout_root, webapp._self_restart_context()


def test_self_update_restart_context_resolves_relative_script_launcher(monkeypatch, tmp_path):
    checkout_root, context = _self_restart_context(
        monkeypatch,
        tmp_path,
        ["yolomux.py", "--host", "0.0.0.0", "--port", "9101", "--dang", "--self-signed", "--dev"],
    )

    assert context.root == str(checkout_root.resolve())
    assert context.argv == [
        "/usr/bin/python3",
        str((checkout_root / "yolomux.py").resolve()),
        "--host",
        "0.0.0.0",
        "--port",
        "9101",
        "--dang",
        "--self-signed",
        "--dev",
    ]


def test_self_update_restart_context_preserves_absolute_script_launcher(monkeypatch, tmp_path):
    checkout_root, context = _self_restart_context(
        monkeypatch,
        tmp_path,
        [str(tmp_path / "xyz" / "yolomux.py"), "--port", "8002", "--sessions", "2"],
    )

    assert context.root == str(checkout_root.resolve())
    assert context.argv == [
        "/usr/bin/python3",
        str((checkout_root / "yolomux.py").resolve()),
        "--port",
        "8002",
        "--sessions",
        "2",
    ]


def test_self_update_restart_context_preserves_module_launcher(monkeypatch, tmp_path):
    checkout_root, context = _self_restart_context(
        monkeypatch,
        tmp_path,
        [str(tmp_path / "xyz" / "yolomux.py"), "--port", "8003", "--sessions", "3"],
        main_module_name="yolomux",
    )

    assert context.root == str(checkout_root.resolve())
    assert context.argv == ["/usr/bin/python3", "-m", "yolomux", "--port", "8003", "--sessions", "3"]


def test_self_update_restart_context_preserves_stripped_launcher_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("YOLOMUX_EXTRA_PATH", "/opt/yolomux-agents")
    monkeypatch.setenv("YOLOMUX_TEST_AUTH_BYPASS", "1")
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)
    _checkout_root, context = _self_restart_context(
        monkeypatch,
        tmp_path,
        ["yolomux.py", "--port", "8004"],
    )

    path_parts = context.env["PATH"].split(os.pathsep)
    assert path_parts[0] == "/opt/yolomux-agents"
    assert "/usr/bin" in path_parts
    assert str(Path.home() / ".local" / "bin") in path_parts
    assert context.env["TERM"] == "xterm-256color"
    assert context.env["PYTHONUNBUFFERED"] == "1"
    assert context.env["YOLOMUX_TEST_AUTH_BYPASS"] == "1"


def test_self_update_restart_uses_running_checkout(monkeypatch, tmp_path):
    checkout_root = tmp_path / "xyz"
    checkout_root.mkdir()
    (checkout_root / "yolomux.py").write_text("from yolomux_lib.cli import main\n", encoding="utf-8")
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace()

    monkeypatch.setattr(app_module.common, "PROJECT_ROOT", checkout_root)
    monkeypatch.setattr(app_module.sys, "argv", ["yolomux.py", "--host", "0.0.0.0", "--port", "9101", "--dang", "--self-signed"])
    monkeypatch.setattr(app_module.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(app_module.os, "getpid", lambda: 424242)
    monkeypatch.setenv("PATH", "/home/test/.local/bin:/usr/bin")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)
    webapp = app_module.TmuxWebtermApp.__new__(app_module.TmuxWebtermApp)
    assert webapp._spawn_self_restart() is True

    args = captured["args"]
    assert args[:3] == ["nohup", "bash", "-lc"]
    helper_cmd = args[-1]
    assert "kill 424242" in helper_cmd
    assert "sleep 2" in helper_cmd
    assert "kill -9 424242" in helper_cmd
    assert f"cd {checkout_root.resolve()}" in helper_cmd
    assert "nohup env" in helper_cmd
    assert "PATH=" in helper_cmd
    assert "/home/test/.local/bin:/usr/bin" in helper_cmd
    assert "TERM=xterm-256color" in helper_cmd
    assert "PYTHONUNBUFFERED=1" in helper_cmd
    assert str((checkout_root / "yolomux.py").resolve()) in helper_cmd
    assert "--host 0.0.0.0 --port 9101 --dang --self-signed" in helper_cmd
    assert app_module.SELF_RESTART_LOG_PATH in helper_cmd
    assert "systemd-run" not in helper_cmd
    assert "systemctl" not in helper_cmd
    assert "pkill" not in helper_cmd
    assert captured["kwargs"]["cwd"] == str(checkout_root.resolve())
    assert captured["kwargs"]["stdin"] is app_module.subprocess.DEVNULL
    assert captured["kwargs"]["stdout"] is app_module.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is app_module.subprocess.DEVNULL
    assert captured["kwargs"]["start_new_session"] is True


def _fake_update_git(remote_version="0.3.25", remote_sha="remoteabcdef1"):
    def fake_git(args, cwd, timeout=3.0):
        assert cwd == "/repo"
        if args == ["fetch", "--quiet", "origin", "main"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args == ["rev-parse", "--short=12", "origin/main"]:
            return SimpleNamespace(returncode=0, stdout=f"{remote_sha}\n", stderr="")
        if args == ["show", "origin/main:yolomux_lib/common.py"]:
            return SimpleNamespace(returncode=0, stdout=f'YOLOMUX_VERSION = "{remote_version}"\n', stderr="")
        raise AssertionError(f"unexpected git args: {args}")
    return fake_git


def test_update_check_status_ignores_sha_only_changes(monkeypatch):
    monkeypatch.setattr(app_module.common, "YOLOMUX_VERSION", "0.3.25")
    monkeypatch.setattr(app_module.common, "yolomux_commit_sha", lambda: "localabcdef1")
    monkeypatch.setattr(app_module.common, "git_ahead_behind_counts", lambda cwd, left: (0, 1))
    monkeypatch.setattr(app_module.common, "git", _fake_update_git(remote_version="0.3.25"))

    status = app_module.common.update_check_status("/repo")

    assert status["available"] is False
    assert status["current"] == "0.3.25"
    assert status["target"] == "0.3.25"
    assert status["current_sha"] == "localabcdef1"
    assert status["target_sha"] == "remoteabcdef1"
    assert status["behind"] == 1


def test_update_check_status_reports_newer_version(monkeypatch):
    monkeypatch.setattr(app_module.common, "YOLOMUX_VERSION", "0.3.25")
    monkeypatch.setattr(app_module.common, "yolomux_commit_sha", lambda: "localabcdef1")
    monkeypatch.setattr(app_module.common, "git_ahead_behind_counts", lambda cwd, left: (0, 1))
    monkeypatch.setattr(app_module.common, "git", _fake_update_git(remote_version="0.3.26"))

    status = app_module.common.update_check_status("/repo")

    assert status["available"] is True
    assert status["current"] == "0.3.25"
    assert status["target"] == "0.3.26"
    assert status["target_version"] == "0.3.26"
    assert status["target_sha"] == "remoteabcdef1"


def test_update_status_dryrun_reports_available():
    webapp = app_module.TmuxWebtermApp(["1"])
    status = webapp.update_status_payload(dryrun=True)
    assert status["available"] is True
    assert status["target"] == "dryrun"
    assert status["dryrun"] is True
    assert status["enabled"] is True
    assert status["notify"] is True
    assert status["notify_level"] == "patch"
    assert status["version_change_level"] == "patch"


def test_update_status_notify_level_respects_semver_threshold(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    status_payload = {
        "available": True,
        "target": "abc123",
        "dryrun": False,
        "version_change_level": "patch",
    }
    monkeypatch.setattr(app_module.common, "update_check_status", lambda *_args, **_kwargs: dict(status_payload))

    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"updates": {"notify_level": "minor"}}})
    assert webapp.update_status_payload()["notify"] is False

    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"updates": {"notify_level": "patch"}}})
    assert webapp.update_status_payload()["notify"] is True

    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"updates": {"notify_level": "none"}}})
    assert webapp.update_status_payload()["notify"] is False

    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"updates": {"check_enabled": False, "notify_level": "patch"}}})
    status = webapp.update_status_payload()
    assert status["enabled"] is True
    assert status["notify"] is True


def test_version_change_level_classifies_semver_bumps():
    assert app_module.common.version_change_level("0.3.25", "0.3.26") == "patch"
    assert app_module.common.version_change_level("0.3.25", "0.4.0") == "minor"
    assert app_module.common.version_change_level("0.3.25", "1.0.0") == "major"
    assert app_module.common.version_change_level("0.3.25", "0.3.25") == "none"
    assert app_module.common.version_change_level("0.3.25", "not-a-version") == "none"
