from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
from http import HTTPStatus
import io
import json
import os
from pathlib import Path
import stat
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs
from urllib.parse import urlparse

import pytest
import yaml

from yolomux_lib import activity_summary
from yolomux_lib import app as app_module
from yolomux_lib.stats_current import host_collectors
from yolomux_lib import common
from yolomux_lib import jobd
from yolomux_lib import metadata
from yolomux_lib import state_services
from yolomux_lib import statusd_protocol
from yolomux_lib import transcripts
from yolomux_lib import uploads as uploads_module
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib.common import UploadedFile
from yolomux_lib.local_services.rpc import encode_metadata
from yolomux_lib.local_services.rpc import LOCAL_RPC_MAX_METADATA_BYTES
from yolomux_lib.local_services.rpc import new_envelope
from yolomux_lib import server_logs
from yolomux_lib.yoagent import session_summaries as session_summaries_module
from yolomux_lib.yoagent import controller as controller_module
from yolomux_lib.yoagent import transports as transport_module

from _git_helpers import git
from _git_helpers import init_repo


PROMPT_STATE_KEYS = set(app_module.blank_prompt_state())
PROMOTED_CAPTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "prompt_corpus" / "captures"
pytestmark = pytest.mark.usefixtures("no_control_socket", "isolated_yoagent_conversation_state", "isolated_tmux_socket")


def test_darwin_memory_details_match_one_native_vm_snapshot(monkeypatch):
    counters = app_module.DarwinVmStatistics64()
    counters.internal_page_count = 30
    counters.wire_count = 10
    counters.compressor_page_count = 20
    counters.external_page_count = 40
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    monkeypatch.setattr(app_module, "current_darwin_vm_statistics", lambda: (1000, 10, counters))
    monkeypatch.setattr(app_module, "darwin_sysctl_structure", lambda name, value_type: SimpleNamespace(used_bytes=25))
    native_values = {
        "kern.memorystatus_level": 80,
        "kern.memorystatus_vm_pressure_level": 2,
    }
    monkeypatch.setattr(app_module, "darwin_sysctl_value", lambda name, value_type: native_values.get(name))

    assert app_module.current_darwin_system_memory_details() == app_module.DarwinSystemMemoryDetails(
        physical_memory_bytes=1000,
        memory_used_bytes=600,
        cached_files_bytes=400,
        app_memory_bytes=300,
        wired_memory_bytes=100,
        compressed_memory_bytes=200,
        swap_used_bytes=25,
        pressure_percent=20.0,
        pressure_level=2,
    )


def test_darwin_memory_details_leave_unavailable_swap_and_pressure_empty(monkeypatch):
    counters = app_module.DarwinVmStatistics64()
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    monkeypatch.setattr(app_module, "current_darwin_vm_statistics", lambda: (1000, 10, counters))
    monkeypatch.setattr(app_module, "darwin_sysctl_structure", lambda name, value_type: None)
    monkeypatch.setattr(app_module, "darwin_sysctl_value", lambda name, value_type: None)

    details = app_module.current_darwin_system_memory_details()

    assert details is not None
    assert details.swap_used_bytes is None
    assert details.pressure_percent is None
    assert details.pressure_level is None


@pytest.mark.parametrize(("native_level", "expected"), [(1, 1), (2, 2), (4, 4), (0, None), (3, None), (5, None)])
def test_darwin_memory_details_accept_only_native_pressure_states(monkeypatch, native_level, expected):
    counters = app_module.DarwinVmStatistics64()
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    monkeypatch.setattr(app_module, "current_darwin_vm_statistics", lambda: (1000, 10, counters))
    monkeypatch.setattr(app_module, "darwin_sysctl_structure", lambda name, value_type: None)
    monkeypatch.setattr(
        app_module,
        "darwin_sysctl_value",
        lambda name, value_type: native_level if name == "kern.memorystatus_vm_pressure_level" else 80,
    )

    details = app_module.current_darwin_system_memory_details()

    assert details is not None
    assert details.pressure_percent == 20.0
    assert details.pressure_level == expected


def test_wait_for_jobd_product_uses_shared_bounded_cadence_until_ready(monkeypatch):
    clock = [100.0]
    sleeps = []
    responses = iter([
        ({"ok": True, "state": "pending", "generation": 1}, None),
        ({"ok": True, "state": "ready", "generation": 2}, b"ready"),
    ])

    class Client:
        def product(self, key):
            assert key == "product-key"
            return next(responses)

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(app_module.time, "sleep", fake_sleep)

    meta, body, state = app_module.wait_for_jobd_product(Client(), "product-key", 2, 20.0)

    assert (meta["generation"], body, state) == (2, b"ready", "ready")
    assert sleeps == [app_module.JOBD_PRODUCT_POLL_INITIAL_SECONDS]


def test_wait_for_jobd_product_caps_its_final_sleep_at_deadline(monkeypatch):
    clock = [100.0]
    sleeps = []

    class Client:
        def product(self, key):
            return {"ok": True, "state": "pending", "generation": 1}, None

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(app_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(app_module, "JOBD_PRODUCT_POLL_INITIAL_SECONDS", 0.25)

    meta, body, state = app_module.wait_for_jobd_product(Client(), "product-key", 2, 0.3)

    assert (meta, body, state) == (None, None, "pending")
    assert sleeps == [0.25, pytest.approx(0.05)]
    assert sum(sleeps) == pytest.approx(0.3)


def test_wait_for_jobd_product_backs_off_to_a_bounded_broker_cadence(monkeypatch):
    clock = [100.0]
    sleeps = []

    class Client:
        def product(self, key):
            return ({"ok": True, "state": "ready", "generation": 2}, b"ready") if len(sleeps) == 3 else ({"ok": True, "state": "pending", "generation": 1}, None)

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(app_module.time, "sleep", fake_sleep)
    meta, body, state = app_module.wait_for_jobd_product(Client(), "product-key", 2, 20.0)
    assert (meta["generation"], body, state) == (2, b"ready", "ready")
    assert sleeps == [0.25, 0.5, 1.0]


def test_wait_for_jobd_product_keeps_broker_failure_distinct():
    class Client:
        def product(self, key):
            return {"ok": False}, None

    with pytest.raises(app_module.JobdProductRpcUnavailable, match="rpc unavailable"):
        app_module.wait_for_jobd_product(Client(), "product-key", 2, 20.0)


class StatsRoleOwner:
    def __init__(self, *, owner: bool, port: int):
        self.owner = owner
        self.port = port
        self.follower_stale_reads = []
        self.refresh_requests = []

    def can_run(self, role):
        return self.owner and role == app_module.BACKGROUND_ROLE_STATS_SAMPLER

    def owner_payload(self):
        return {"port": self.port}

    def record_follower_stale_read(self, role):
        self.follower_stale_reads.append(role)

    def request_owner_refresh(self, role, payload):
        self.refresh_requests.append((role, payload))
        return {"ok": True, "accepted": True, "role": role, "local_owner": self.owner, "fallback": False}


def test_record_owned_direct_image_usage_preserves_structured_image_token_classes():
    submitted = []
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_current_client = SimpleNamespace(
        ensure_started=lambda: True,
        append=lambda *, usage_atoms: submitted.extend(usage_atoms) or {"ok": True},
    )
    webapp.settings_payload = lambda: {"settings": {"cost": {"openai_pricing_profile": "subscription"}}}

    assert webapp.record_owned_usage_atoms(
        provider="openai",
        model="gpt-image-2",
        usage={"input_tokens_details": {"text_tokens": 11, "image_tokens": 22}, "output_tokens": 33, "total_tokens": 66},
        source="Image tool",
        event_id="image-request-1",
        endpoint="images",
        thread_id="root-thread",
        timestamp=1_000,
    ) is True

    atoms = submitted
    assert {(atom.direction, atom.modality, atom.payload["quantity"]) for atom in atoms} == {
        ("input", "text", 11.0), ("input", "image", 22.0), ("output", "image", 33.0),
    }
    assert all(
        atom.payload["model"] == "gpt-image-2"
        and atom.payload["execution_source"] == "images"
        and atom.payload["thread_id"] == "root-thread"
        and atom.payload["pricing_profile"] == "default"
        for atom in atoms
    )


def test_stats_agent_token_rows_preserve_tmux_window_identity_for_cost_attribution():
    webapp = object.__new__(app_module.TmuxWebtermApp)

    rows = webapp.stats_agent_token_rows([{
        "session": "s",
        "window_index": 2,
        "window_label": "build",
        "label": "fallback",
        "kind": "codex",
        "transcript": "/tmp/rollout.jsonl",
    }])

    assert rows == [{
        "key": "s|2|codex",
        "label": "s:build",
        "transcript": "/tmp/rollout.jsonl",
        "kind": "codex",
        "session": "s",
        "window": "2",
        "window_label": "build",
    }]


def test_stats_agent_token_rows_do_not_collapse_distinct_panes_in_one_window():
    webapp = object.__new__(app_module.TmuxWebtermApp)

    rows = webapp.stats_agent_token_rows([
        {"session": "s", "window_index": 2, "pane_target": "%10", "kind": "codex", "transcript": "/tmp/one.jsonl"},
        {"session": "s", "window_index": 2, "pane_target": "%11", "kind": "codex", "transcript": "/tmp/two.jsonl"},
    ])

    assert [row["key"] for row in rows] == ["s|2|%10|codex", "s|2|%11|codex"]


def test_stats_agent_token_rows_enriches_only_the_token_path_when_statusd_omits_transcripts(monkeypatch, tmp_path):
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["s"]
    pane = PaneInfo("s", "3", "0", "%3", "s:3.0", str(tmp_path), "codex", True, True, "codex", 9)
    agent = AgentInfo("s", "codex", 9, "%3", "codex", str(tmp_path), None, "thread", str(tmp_path / "rollout.jsonl"), None)
    info = SessionInfo("s", [pane], pane, [agent])
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"s": info}, []))

    rows = webapp.stats_agent_token_rows([{"session": "s", "kind": "codex", "transcript": ""}])

    assert rows == [{
        "key": "s|3|codex", "label": "s:3:codex", "transcript": str(tmp_path / "rollout.jsonl"),
        "kind": "codex", "session": "s", "window": "3", "window_label": "3:codex",
    }]


def test_stats_agent_token_rows_keeps_existing_transcript_rows_when_enriching_missing_ones(monkeypatch, tmp_path):
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["s"]
    pane = PaneInfo("s", "3", "0", "%3", "s:3.0", str(tmp_path), "claude", True, True, "claude", 9)
    agent = AgentInfo("s", "claude", 9, "%3", "claude", str(tmp_path), None, "thread", str(tmp_path / "claude.jsonl"), None)
    info = SessionInfo("s", [pane], pane, [agent])
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"s": info}, []))

    rows = webapp.stats_agent_token_rows([
        {"session": "s", "window_index": 1, "kind": "codex", "transcript": "/tmp/codex.jsonl"},
        {"session": "s", "window_index": 3, "kind": "claude", "transcript": ""},
    ])

    assert {row["key"] for row in rows} == {"s|1|codex", "s|3|claude"}


def test_stats_agent_token_rows_stop_re_enriching_a_permanently_unresolvable_roster(monkeypatch, tmp_path):
    """Session yo7770 runs in a tree with no matching Codex rollout, so its statusd row can never
    carry a transcript. That made `any(not row["transcript"])` permanently true and forced a full
    discover_sessions (measured 1.53-2.05s CPU) on every collector sample."""

    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["s"]
    pane = PaneInfo("s", "3", "0", "%3", "s:3.0", str(tmp_path), "codex", True, True, "codex", 9)
    agent = AgentInfo("s", "codex", 9, "%3", "codex", str(tmp_path), None, "thread", str(tmp_path / "rollout.jsonl"), None)
    info = SessionInfo("s", [pane], pane, [agent])
    discover_calls = []
    monkeypatch.setattr(
        app_module, "discover_sessions",
        lambda sessions: discover_calls.append(tuple(sessions)) or ({"s": info}, []),
    )
    clock = _StepClock()
    monkeypatch.setattr(webapp.stats_agent_token_enrich_memo(), "clock", clock)
    statusd_rows = [
        {"session": "s", "window_index": 3, "kind": "codex", "transcript": ""},
        {"session": "yo7770", "window_index": 1, "kind": "codex", "transcript": ""},
    ]

    first = webapp.stats_agent_token_rows(list(statusd_rows))
    assert len(discover_calls) == 1

    # Five more samples at the 10s watched cadence, inside the memo TTL, same unresolved roster.
    for _ in range(5):
        clock.advance(10.0)
        assert webapp.stats_agent_token_rows(list(statusd_rows)) == first
    assert len(discover_calls) == 1


def test_stats_agent_token_rows_re_enrich_immediately_when_a_new_agent_appears(monkeypatch, tmp_path):
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["s"]
    pane = PaneInfo("s", "3", "0", "%3", "s:3.0", str(tmp_path), "codex", True, True, "codex", 9)
    agent = AgentInfo("s", "codex", 9, "%3", "codex", str(tmp_path), None, "thread", str(tmp_path / "rollout.jsonl"), None)
    info = SessionInfo("s", [pane], pane, [agent])
    discover_calls = []
    monkeypatch.setattr(
        app_module, "discover_sessions",
        lambda sessions: discover_calls.append(tuple(sessions)) or ({"s": info}, []),
    )
    clock = _StepClock()
    monkeypatch.setattr(webapp.stats_agent_token_enrich_memo(), "clock", clock)

    webapp.stats_agent_token_rows([{"session": "s", "window_index": 3, "kind": "codex", "transcript": ""}])
    assert len(discover_calls) == 1

    clock.advance(1.0)
    webapp.stats_agent_token_rows([
        {"session": "s", "window_index": 3, "kind": "codex", "transcript": ""},
        {"session": "s", "window_index": 4, "kind": "claude", "transcript": ""},
    ])

    # A roster change is not subject to the TTL: a pane that just started is enriched next sample.
    assert len(discover_calls) == 2


def test_stats_agent_token_rows_re_enrich_after_the_memo_ttl_for_an_unchanged_roster(monkeypatch, tmp_path):
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["s"]
    pane = PaneInfo("s", "3", "0", "%3", "s:3.0", str(tmp_path), "codex", True, True, "codex", 9)
    agent = AgentInfo("s", "codex", 9, "%3", "codex", str(tmp_path), None, "thread", str(tmp_path / "rollout.jsonl"), None)
    info = SessionInfo("s", [pane], pane, [agent])
    discover_calls = []
    monkeypatch.setattr(
        app_module, "discover_sessions",
        lambda sessions: discover_calls.append(tuple(sessions)) or ({"s": info}, []),
    )
    clock = _StepClock()
    monkeypatch.setattr(webapp.stats_agent_token_enrich_memo(), "clock", clock)
    statusd_rows = [{"session": "s", "window_index": 3, "kind": "codex", "transcript": ""}]

    webapp.stats_agent_token_rows(list(statusd_rows))
    clock.advance(app_module.STATS_AGENT_TOKEN_ENRICH_MEMO_TTL_SECONDS + 1.0)
    webapp.stats_agent_token_rows(list(statusd_rows))

    assert len(discover_calls) == 2


class _StepClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_record_owned_direct_image_stream_ignores_partial_fragments_until_final_usage():
    submitted = []
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_current_client = SimpleNamespace(
        ensure_started=lambda: True,
        append=lambda *, usage_atoms: submitted.extend(usage_atoms) or {"ok": True},
    )

    # A partial image stream result carries no completed provider usage.  It
    # cannot be priced from image bytes, a provisional total, or prose.
    assert webapp.record_owned_usage_atoms(
        provider="openai", model="gpt-image-2", usage={"partial_images": [{"id": "partial-1"}]},
        source="Image stream", event_id="request-2", endpoint="images", timestamp=1_000,
    ) is False
    assert submitted == []
    assert webapp.record_owned_usage_atoms(
        provider="openai", model="gpt-image-2",
        usage={"input_tokens_details": {"text_tokens": 3, "image_tokens": 7}, "output_tokens": 11},
        source="Image stream", event_id="request-2", endpoint="images", timestamp=1_001,
    ) is True
    assert {(atom.direction, atom.modality, atom.payload["quantity"]) for atom in submitted} == {
        ("input", "text", 3.0), ("input", "image", 7.0), ("output", "image", 11.0),
    }


def test_record_owned_responses_image_tool_keeps_mainline_and_opaque_child_separate():
    submitted = []
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_current_client = SimpleNamespace(
        ensure_started=lambda: True,
        append=lambda *, usage_atoms: submitted.extend(usage_atoms) or {"ok": True},
    )
    webapp.settings_payload = lambda: {"settings": {"cost": {"openai_pricing_profile": "subscription"}}}

    assert webapp.record_owned_usage_atoms(
        provider="openai", model="gpt-5.6", usage={"input_tokens": 10, "output_tokens": 5}, source="Responses",
        event_id="response-1", endpoint="responses", opaque_image_tool=True, pricing_profile="subscription", timestamp=1_000,
    ) is True

    atoms = submitted
    assert {(atom.payload["model"], atom.modality, atom.unit, atom.payload["quantity"]) for atom in atoms} == {
        ("gpt-5.6", "text", "tokens", 10.0), ("gpt-5.6", "text", "tokens", 5.0), ("unknown", "image", "requests", 1.0),
    }
    assert {atom.payload["pricing_profile"] for atom in atoms} == {"default"}
    opaque = next(atom for atom in atoms if atom.payload["model"] == "unknown")
    assert opaque.payload["telemetry_complete"] is False


def test_state_services_own_independent_cache_and_watcher_records_without_app():
    session_files = state_services.SessionFilesService()
    first, first_owner = session_files.claim_work(("session", "1"), 10)
    second, second_owner = session_files.claim_work(("session", "1"), 11)
    activity = state_services.ActivityTranscriptService()
    activity.activity_summary_cache[("configured", "en")] = {"sessions": []}
    watch = state_services.ClientWatchService(descriptors={
        "client-1": state_services.ClientWatchDescriptor(
            expires_at=time.monotonic() + 60.0,
            context_items=[{"session": "1", "messages": 1, "id": "context"}],
            session_files=[{"session": "1"}],
            activity_summary={"visible": True, "ok": True},
        ),
    })
    stats = state_services.StatsCollectionState()

    assert first_owner is True and second_owner is False and second is first
    assert activity.activity_summary_cache[("configured", "en")]["sessions"] == []
    assert watch.snapshot() == ([{"session": "1", "messages": 1, "id": "context"}], [{"session": "1"}], {"visible": True, "ok": True})
    assert stats.sample_record.cached_payload is None
    assert stats.agent_activity_state == {}


def test_session_files_owner_demotion_fences_reserved_work():
    session_files = state_services.SessionFilesService()
    record = session_files.reserve_work(("payload", "old"), "stable-view")

    assert record is not None
    assert session_files.stable_generation_is_current(record) is True
    session_files.cancel_all_work()

    assert session_files.work_records == {}
    assert session_files.latest_stable_generations == {}
    assert session_files.stable_generation_is_current(record) is False


def test_runtime_report_exposes_shared_local_service_lifecycle_clients(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(webapp.search_indexer, "runtime_status", lambda: {"service": "indexd", "pid": 0, "resources": {}})
        monkeypatch.setattr(webapp.stats_current_runtime, "status", lambda: {
            "leased": False,
            "families": {},
            "service": {"ok": True, "version": 23, "pid": 0, "migration": {"state": "ready"}},
        })
        monkeypatch.setattr(webapp.job_client, "runtime_status", lambda: {"service": "jobd", "pid": 0, "resources": {}})
        monkeypatch.setattr(webapp.status_client, "runtime_status", lambda: {"service": "statusd", "pid": 0, "resources": {}})
        monkeypatch.setattr(webapp.approval_client, "runtime_status", lambda: {"service": "approvald", "pid": 0, "resources": {}})
        services = webapp.runtime_local_services()
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert [row["service"] for row in services["services"]] == ["indexd", "statsd", "jobd", "statusd", "watchd", "approvald"]
    assert services["totals"] == {"processes": 0, "cpu_percent": 0.0, "rss_bytes": 0}


def test_runtime_local_services_derives_uptime_for_running_services(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    try:
        now = time.time()
        # A running service reports a started_at; an idle one has no pid.
        monkeypatch.setattr(webapp.search_indexer, "runtime_status", lambda: {"service": "indexd", "pid": 4321, "started_at": now - 42.0, "resources": {"cpu_percent": 1.0, "rss_bytes": 2048}})
        monkeypatch.setattr(webapp.stats_current_runtime, "status", lambda: {
            "service": {"ok": True, "pid": 0, "started_at": 0.0, "migration": {"state": "ready"}},
            "families": {},
        })
        monkeypatch.setattr(webapp.job_client, "runtime_status", lambda: {"service": "jobd", "pid": 0, "resources": {}})
        monkeypatch.setattr(webapp.approval_client, "runtime_status", lambda: {"service": "approvald", "pid": 0, "resources": {}})
        services = webapp.runtime_local_services()["services"]
    finally:
        webapp.control_server.stop()

    by_name = {row["service"]: row for row in services}
    # The Local-services table's Uptime cell reads service.uptime_seconds; a
    # running service must expose it (was absent -> the cell showed an em dash).
    assert by_name["indexd"]["uptime_seconds"] is not None
    assert 41.0 <= by_name["indexd"]["uptime_seconds"] <= 60.0
    assert by_name["statsd"]["uptime_seconds"] is None


def test_runtime_local_services_jobd_row_exposes_product_counters_and_cache(monkeypatch):
    # The System view's "Cache" and "Products" diagnostic rows read service.cache /
    # service.product_counters directly off the jobd runtime_status row; prove the
    # checkbox-3 jobd product-layer counters actually reach that surface.
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(webapp.search_indexer, "runtime_status", lambda: {"service": "indexd", "pid": 0, "resources": {}})
        monkeypatch.setattr(webapp.stats_current_runtime, "status", lambda: {
            "service": {"ok": True, "pid": 0, "started_at": 0.0, "migration": {"state": "ready"}},
            "families": {},
        })
        monkeypatch.setattr(webapp.job_client, "runtime_status", lambda: {
            "service": "jobd", "pid": 4242, "resources": {},
            "cache": {"records": 3, "coalesced": 1, "record_limit": 256, "products": 2, "products_stale": 1},
            "product_counters": {"transcript_view": {"accepted": 5, "coalesced": 2, "completed": 4, "failed": 0, "superseded": 1}},
            "product_runtime_ms": {"transcript_view": {"count": 4, "total_ms": 240.0, "max_ms": 90.0, "avg_ms": 60.0}},
            "product_phase_runtime_ms": {"session_files_view": {"git-snapshot": {"count": 2, "total_ms": 30.0, "max_ms": 20.0, "avg_ms": 15.0}}},
            "product_work_totals": {"session_files_view": {"sessions": 2, "repositories": 1, "files": 4, "git_snapshots": 1, "result_bytes": 512}},
            "source_change_counters": {"initial": 1},
            "last_success": 1784386100.0,
        })
        monkeypatch.setattr(webapp.approval_client, "runtime_status", lambda: {"service": "approvald", "pid": 0, "resources": {}})
        services = webapp.runtime_local_services()["services"]
    finally:
        webapp.control_server.stop()

    jobd_row = next(row for row in services if row["service"] == "jobd")
    assert jobd_row["cache"] == {"records": 3, "coalesced": 1, "record_limit": 256, "products": 2, "products_stale": 1}
    assert jobd_row["last_success"] == 1784386100.0
    assert jobd_row["product_counters"]["transcript_view"]["completed"] == 4
    assert jobd_row["product_counters"]["transcript_view"]["accepted"] == 5
    # Checkbox 10: per-product runtime totals/maxima reach the same diagnostics surface.
    assert jobd_row["product_runtime_ms"]["transcript_view"]["max_ms"] == 90.0
    assert jobd_row["product_runtime_ms"]["transcript_view"]["avg_ms"] == 60.0
    assert jobd_row["product_phase_runtime_ms"]["session_files_view"]["git-snapshot"]["max_ms"] == 20.0
    assert jobd_row["product_work_totals"]["session_files_view"]["git_snapshots"] == 1
    assert jobd_row["source_change_counters"] == {"initial": 1}


def test_stats_usage_health_warns_only_for_committed_growth_without_fresh_atoms():
    warning = app_module.stats_current_usage_health(
        {"last_accepted_at": 800.0},
        {"last_visible_append_at": 995.0},
        10.0,
        now=1000.0,
    )
    resumed = app_module.stats_current_usage_health(
        {"last_accepted_at": 999.0},
        {"last_visible_append_at": 995.0},
        10.0,
        now=1000.0,
    )
    idle = app_module.stats_current_usage_health(
        {"last_accepted_at": 800.0},
        {"last_visible_append_at": 700.0},
        10.0,
        now=1000.0,
    )

    assert warning["state"] == "warning"
    assert warning["stale_bound_seconds"] == 120.0
    assert resumed["state"] == "ok"
    assert idle["state"] == "idle"


def test_stats_usage_health_warns_for_a_sustained_sampler_failure_loop():
    warning = app_module.stats_current_usage_health(
        {"last_accepted_at": 999.0},
        {"last_visible_append_at": 995.0},
        10.0,
        sampler_families={
            "cpu": {
                "cadence_seconds": 1.0,
                "attempts": 12,
                "failures": 12,
                "last_attempt_at": 999.5,
                "last_success_at": 800.0,
                "last_failure": "FileNotFoundError: statsd.sock missing",
            },
        },
        now=1000.0,
    )
    healthy = app_module.stats_current_usage_health(
        {"last_accepted_at": 999.0},
        {"last_visible_append_at": 995.0},
        10.0,
        sampler_families={
            "cpu": {
                "cadence_seconds": 1.0,
                "attempts": 12,
                "failures": 2,
                "last_attempt_at": 999.5,
                "last_success_at": 998.0,
                "last_failure": "FileNotFoundError: statsd.sock missing",
            },
        },
        now=1000.0,
    )

    assert warning["state"] == "warning"
    assert "sustained sampler failure loop in cpu" in warning["reason"]
    assert warning["sampler_warning"]["family"] == "cpu"
    assert healthy["state"] == "ok"
    assert healthy["sampler_warning"] is None


def test_runtime_local_services_exposes_bounded_stats_usage_health(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(webapp.search_indexer, "runtime_status", lambda: {"service": "indexd", "pid": 0, "resources": {}})
        monkeypatch.setattr(webapp.stats_current_runtime, "status", lambda: {
            "families": {
                "agent_tokens": {"cadence_seconds": 10},
                "cpu": {
                    "cadence_seconds": 1,
                    "attempts": 9,
                    "failures": 9,
                    "last_attempt_at": time.time(),
                    "last_success_at": 1.0,
                    "last_failure": "FileNotFoundError: statsd.sock missing",
                },
            },
            "service": {
                "ok": True,
                "pid": 0,
                "migration": {"state": "ready"},
                "usage": {"last_accepted_at": 1.0, "quarantined_conflict_count": 2},
            },
        })
        monkeypatch.setattr(webapp.stats_current_transcript_usage, "status", lambda: {
            "committed_appended_bytes": 14,
            "last_visible_append_at": time.time(),
            "visible_append_age_seconds": 0.0,
            "legacy_fork_repair": {"active": True, "remaining_files": 2},
        })
        monkeypatch.setattr(webapp.job_client, "runtime_status", lambda: {"service": "jobd", "pid": 0, "resources": {}})
        monkeypatch.setattr(webapp.approval_client, "runtime_status", lambda: {"service": "approvald", "pid": 0, "resources": {}})
        statsd = next(row for row in webapp.runtime_local_services()["services"] if row["service"] == "statsd")
    finally:
        webapp.control_server.stop()

    assert statsd["usage"]["health"]["state"] == "warning"
    assert "sustained sampler failure loop in cpu" in statsd["usage"]["health"]["reason"]
    assert statsd["usage"]["health"]["sampler_warning"]["family"] == "cpu"
    assert statsd["usage"]["quarantined_conflict_count"] == 2
    assert set(statsd["usage"]["transcripts"]) == {
        "committed_appended_bytes",
        "last_visible_append_at",
        "visible_append_age_seconds",
        "legacy_fork_repair",
    }


def test_session_http_guards_use_shared_decorator():
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert "def requires_known_session(" in source
    assert source.count("unknown = self.require_known_session(session)") == 1
    assert "@requires_known_session(refresh=True)\n    def rename_session" in source
    assert "@requires_known_session()\n    def tmux_snapshot" in source
    assert source.count("@requires_known_session(") >= 10


def test_yoagent_controller_facade_allows_only_declared_dependencies(monkeypatch):
    app = SimpleNamespace(sessions=["1"])
    deps = app_module.YoagentAppDeps(app)

    assert deps.sessions == ["1"]
    deps.sessions = ["2"]
    assert app.sessions == ["2"]
    with pytest.raises(AttributeError):
        _ = deps.undeclared_app_capability
    monkeypatch.setattr(app_module, "normalized_prompt_state", lambda _prompt=None: {"source": "patched"})
    assert deps.normalized_prompt_state() == {"source": "patched"}

    app_source = Path(app_module.__file__).read_text(encoding="utf-8")
    facade_source = app_source[app_source.index("class YoagentAppDeps:"):app_source.index("class TmuxWebtermApp:")]
    route_source = (Path(app_module.__file__).parent / "http_routes.py").read_text(encoding="utf-8")
    assert "def __getattr__" not in facade_source
    assert "return self.yoagent_controller." not in app_source
    assert "self.yoagent_controller.poll_yoagent_jobs_once()" in app_source
    assert "self.poll_yoagent_jobs_once()" not in app_source
    assert "request.server.app.yoagent_chat(" not in route_source
    assert "request.server.app.yoagent_controller.yoagent_chat(" in route_source


def test_darwin_cpu_path_uses_native_ticks_before_ps_fallback(monkeypatch):
    class MissingProcStat:
        def __init__(self, *_args, **_kwargs):
            pass

        def read_text(self, **_kwargs):
            raise OSError("no procfs")

    monkeypatch.setattr(app_module, "Path", MissingProcStat)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    monkeypatch.setattr(app_module, "current_darwin_system_cpu_times", lambda: (200.0, 80.0))
    monkeypatch.setattr(app_module, "current_system_cpu_percent_from_ps", lambda: pytest.fail("ps fallback must not run when Mach ticks are available"))

    assert app_module.current_system_cpu_times() == (200.0, 80.0)


def test_stats_GPU_gpu_metrics_uses_aggregate_devices_without_process_scans(monkeypatch):
    responses = iter([SimpleNamespace(returncode=0, stdout="0, GPU Model A, 75, 4000, 8000\n")])
    calls = []

    def run(*args, **_kwargs):
        calls.append(args[0])
        return next(responses)

    monkeypatch.setattr(host_collectors.subprocess, "run", run)

    metrics = app_module.stats_GPU_gpu_metrics()

    assert metrics["devices"]["gpu:0"]["util_percent"] == 75.0
    assert metrics["devices"]["gpu:0"]["memory_used_bytes"] == 4000 * 1024 * 1024
    assert metrics["devices"]["gpu:0"]["memory_capacity_bytes"] == 8000 * 1024 * 1024
    assert metrics["devices"]["gpu:0"]["label"] == "GPU 0 (GPU Model A)"
    assert metrics == {"devices": metrics["devices"]}
    assert calls == [["gpu-info", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"]]


def test_stats_macos_gpu_metrics_read_ioreg_activity_and_unified_memory(monkeypatch):
    payload = host_collectors.plistlib.dumps([{
        "PerformanceStatistics": {"GPU Activity(%)": 44, "In use system memory": 2 * 1024 * 1024},
    }])
    monkeypatch.setattr(host_collectors.sys, "platform", "darwin")
    monkeypatch.setattr(host_collectors.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=payload))

    metrics = app_module.stats_macos_gpu_metrics("Apple M4 Pro")

    assert metrics["devices"]["gpu:0"] == {"label": "GPU 0", "util_percent": 44.0, "memory_used_bytes": 2 * 1024 * 1024, "memory_capacity_bytes": 0}
    assert metrics == {"devices": metrics["devices"]}


def test_stats_macos_gpu_metrics_reads_current_device_utilization_key(monkeypatch):
    payload = host_collectors.plistlib.dumps([{
        "PerformanceStatistics": {"Device Utilization %": 8, "In use system memory": 2 * 1024 * 1024},
    }])
    monkeypatch.setattr(host_collectors.sys, "platform", "darwin")
    monkeypatch.setattr(host_collectors.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=payload))

    metrics = app_module.stats_macos_gpu_metrics("Apple M4 Pro")

    assert metrics["devices"]["gpu:0"]["util_percent"] == 8.0


def test_stats_macos_gpu_metrics_drops_records_without_utilization(monkeypatch):
    payload = host_collectors.plistlib.dumps([{
        "PerformanceStatistics": {"In use system memory": 2 * 1024 * 1024},
    }])
    monkeypatch.setattr(host_collectors.sys, "platform", "darwin")
    monkeypatch.setattr(host_collectors.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=payload))

    assert app_module.stats_macos_gpu_metrics("Apple M4 Pro") == {}


def test_stats_macos_hardware_metadata_labels_cpu_gpu_and_unified_memory(monkeypatch):
    payload = json.dumps({
        "SPHardwareDataType": [{"chip_type": "Apple M4 Pro", "number_processors": "proc 14:10:4:0"}],
        "SPMemoryDataType": [{"dimm_type": "LPDDR5"}],
        "SPDisplaysDataType": [{"sppci_model": "Apple M4 Pro", "sppci_cores": "20"}],
    })
    monkeypatch.setattr(host_collectors.sys, "platform", "darwin")
    monkeypatch.setattr(host_collectors.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=payload))

    assert app_module.stats_macos_hardware_metadata() == {
        "cpu_label": "Apple M4 Pro · 14 cores (10 performance + 4 efficiency)",
        "gpu_label": "Apple M4 Pro",
        "system_memory_label": "LPDDR5 unified memory",
    }


def test_stats_sample_parallel_scalars_are_retired():
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    for name in (
        "stats_sample_last_monotonic",
        "stats_sample_last_process_time",
        "stats_sample_last_system_cpu_times",
        "stats_sample_cached_monotonic",
        "stats_sample_cached_payload",
    ):
        assert f"self.{name}" not in source


def test_stats_history_sampler_parallel_state_is_retired():
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert "self.stats_history_sampler_thread" not in source
    assert "self.stats_history_sampler_stop_event" not in source
    assert "self.stats_history_sampler_running" not in source
    assert "def stats_history_sampler_loop" not in source
    assert "def start_stats_history_sampler" not in source


def test_system_cpu_percent_from_times_clamps_to_single_100_percent_scale():
    assert app_module.system_cpu_percent_from_times((100.0, 20.0), (104.0, 22.0)) == 50.0
    assert app_module.system_cpu_percent_from_times((100.0, 20.0), (104.0, 200.0)) == 100.0
    assert app_module.system_cpu_percent_from_times((100.0, 20.0), (100.0, 21.0)) == 0.0


def test_current_stats_sample_is_a_statsd_push_reader(monkeypatch):
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_collection_state = state_services.StatsCollectionState()
    record = webapp.stats_collection_state.sample_record
    record.cached_payload = {"pid": 12, "cpu_percent": 17.0, "system_cpu_percent": 25.0}

    sample, recorded = webapp.current_stats_sample(force=True)

    assert recorded is False
    assert sample == record.cached_payload


def test_statsd_cpu_push_updates_the_web_cache_without_a_web_sampler():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_collection_state = state_services.StatsCollectionState()
    webapp.update_server_cpu_budget = lambda sample: {"status": "ok", "current_percent": sample["cpu_percent"]}

    response = webapp.handle_control_request({
        "action": "stats_cpu_sample",
        "sample": {"time": 100.0, "pid": os.getpid(), "cpu_percent": 42.0, "system_cpu_percent": 11.0, "rss_bytes": 123},
    })

    assert response == {"ok": True, "cpu_budget": {"status": "ok", "current_percent": 42.0}}
    assert webapp.latest_stats_sample()["cpu_percent"] == 42.0


def test_cpu_budget_marks_a_missing_statsd_push_stale():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.stats_collection_state = state_services.StatsCollectionState()

    payload = webapp.server_cpu_budget_payload(now=None)

    assert payload["status"] == "stale"
    assert payload["source"] == "statsd_push"
    assert payload["sample_age_seconds"] is None
    assert payload["stale"] is True


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
        diagnostics_calls = []
        profile_payload = {
            "ok": True,
            "retained": 1,
            "maximum": 256,
            "items": [{"kind": "api", "endpoint": "/api/session-metadata", "ttfb_ms": 8400}],
        }
        observation_status_payload = {
            "ok": True, "accepted_reports": 2, "confirmed_real_failures": 1,
        }
        webapp.stats_current_client.browser_diagnostics = lambda: diagnostics_calls.append("combined") or {
            "ok": True,
            "profiles": profile_payload,
            "observation_status": observation_status_payload,
        }
        diagnostics = webapp.performance_diagnostics_payload()
        control_response = webapp.handle_control_request({"action": "background_status"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert "perf" not in payload
    perf = diagnostics["perf"]
    assert diagnostics["browser_profiles"] == {
        "retained": 1,
        "maximum": 256,
        "items": [{"kind": "api", "endpoint": "/api/session-metadata", "ttfb_ms": 8400}],
    }
    assert diagnostics["browser_observation_status"] == {"accepted_reports": 2, "confirmed_real_failures": 1}
    assert diagnostics_calls == ["combined"]
    assert perf["record_count"] == 1
    assert perf["recent"][0]["cache_key_kind"] == "payload"
    assert perf["recent"][0]["cache_hit"] is True
    assert perf["recent"][0]["cache_fresh"] is True
    assert perf["summary"] == [{
        "role": app_module.BACKGROUND_ROLE_SESSION_FILES,
        "surface": "payload",
        "count": 1,
        "compute_ms_total": 12.5,
        "compute_ms_avg": 12.5,
        "compute_ms_max": 12.5,
        "request_total_ms_avg": 0.0,
        "request_total_ms_max": 0.0,
        "accept_to_route_ms_avg": 0.0,
        "accept_to_route_ms_max": 0.0,
        "payload_bytes_total": len(json.dumps({"files": [{"path": "/repo/a.py"}]}, sort_keys=True, separators=(",", ":")).encode("utf-8")),
        "cache": {"hit:fresh": 1},
    }]
    assert control_response["ok"] is True
    assert "perf" not in control_response["status"]
    assert set(control_response["search_index_runtime"]) >= {
        "build_count",
        "full_build_count",
        "incremental_build_count",
        "scanned_entries",
        "ignored_entries",
        "cache_bytes",
        "write_bytes",
        "truncated_roots",
        "roots",
    }


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


def test_log_event_publishes_shared_event_log_invalidation_after_append(tmp_path):
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.event_log = app_module.EventLog(tmp_path / "events.jsonl")
    published = []
    webapp.publish_background_client_event = lambda *args, **kwargs: published.append((args, kwargs))

    saved = webapp.log_event("8001", "manual", "saved", {"source": "test"})

    assert saved["session"] == "8001"
    assert app_module.BACKGROUND_CLIENT_EVENT_POLICIES["event_log_changed"] == {"truth": "event log", "delivery": "push"}
    assert "event_log_changed" in app_module.BACKGROUND_CLIENT_EVENT_TYPES
    assert published == [
        (
            ("event_log_changed", {"session": "8001"}),
            {"trigger": "event-log", "cache": "ready"},
        ),
    ]
    assert webapp.event_log.tail(session="8001") == [saved]


def test_sampled_background_event_forwards_one_shared_descriptor_parent():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.background_refresh_event_log_lock = threading.Lock()
    webapp.background_refresh_event_log_records = {}
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


def test_capture_measurement_metrics_exclude_other_browser_records():
    webapp = app_module.TmuxWebtermApp([])
    try:
        webapp.record_performance_sample("http-endpoint", "GET /api/ping", compute_ms=2.0, details={"measurement_scope": "capture", "request_total_ms": 9.0, "accept_to_route_ms": 4.0})
        webapp.record_performance_sample("http-endpoint", "GET /api/fs/watch-diff", compute_ms=99.0)
        response = webapp.handle_control_request({"action": "runtime_measurement_metrics", "scope": "capture"})
    finally:
        webapp.control_server.stop()

    assert response["ok"] is True
    assert [(row["surface"], row["compute_ms_total"]) for row in response["performance"]["summary"]] == [("GET /api/ping", 2.0)]
    row = response["performance"]["summary"][0]
    assert row["request_total_ms_max"] == 9.0
    assert row["accept_to_route_ms_max"] == 4.0
    assert webapp.handle_control_request({"action": "runtime_measurement_metrics", "scope": "other"}) == {"ok": False, "error": "unsupported measurement scope"}


def test_server_cpu_budget_warns_after_sustained_window_with_top_consumers(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    logs = []
    events = []
    monkeypatch.setattr(app_module, "emit_server_log", lambda *args, **kwargs: logs.append((args, kwargs)))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))
    try:
        webapp.record_performance_sample("http-endpoint", "POST /api/fs/batch", compute_ms=40.0)
        webapp.record_performance_sample("session-files", "phase:git-snapshot", compute_ms=25.0)
        first = webapp.update_server_cpu_budget({"cpu_percent": 31.0}, now=1000.0)
        early = webapp.update_server_cpu_budget({"cpu_percent": 35.0}, now=1299.0)
        warned = webapp.update_server_cpu_budget({"cpu_percent": 36.0}, now=1300.0)
        duplicate = webapp.update_server_cpu_budget({"cpu_percent": 37.0}, now=1400.0)
        recovered = webapp.update_server_cpu_budget({"cpu_percent": 10.0}, now=1401.0)
    finally:
        webapp.control_server.stop()

    assert first["status"] == early["status"] == "watching"
    assert warned["status"] == duplicate["status"] == "warning"
    assert warned["sustained_seconds"] == 300.0
    assert warned["top_consumers"][:2] == [
        {"role": "http-endpoint", "surface": "POST /api/fs/batch", "count": 1, "compute_ms_total": 40.0},
        {"role": "session-files", "surface": "phase:git-snapshot", "count": 1, "compute_ms_total": 25.0},
    ]
    assert len(logs) == 1 and logs[0][0][:2] == ("warning", "stats-cpu")
    assert len(events) == 1 and events[0][0][1] == "server_cpu_budget_warning"
    assert recovered["status"] == "ok" and recovered["sustained_seconds"] == 0.0


def test_server_cpu_budget_warning_spans_the_breach_window_and_states_its_coverage(monkeypatch):
    # The warning explained a 300s breach with a 60s slice of profiling and no denominator, so a
    # consumer worth 0.4% of the CPU read as the cause. Two defects, one message: the summary
    # window must be the breach window, and the report must say what share of the measured CPU it
    # actually accounts for. A live 7771 breach burned ~267 CPU-s and attributed 0.9ms of it.
    webapp = app_module.TmuxWebtermApp([])
    logs = []
    events = []
    monkeypatch.setattr(app_module, "emit_server_log", lambda *args, **kwargs: logs.append((args, kwargs)))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))
    try:
        # 120s old: inside the 300s breach window, outside the 60s summary default that was used.
        webapp.record_performance_sample(
            "http-endpoint", "GET /api/logs", compute_ms=90.0, record_time=time.time() - 120.0,
        )
        webapp.update_server_cpu_budget({"cpu_percent": 90.0}, now=2000.0)
        early = webapp.update_server_cpu_budget({"cpu_percent": 90.0}, now=2299.0)
        warned = webapp.update_server_cpu_budget({"cpu_percent": 90.0}, now=2300.0)
    finally:
        webapp.control_server.stop()

    # Unchanged: the breach threshold and the moment the warning fires.
    assert early["status"] == "watching" and warned["status"] == "warning"
    assert warned["sustained_seconds"] == 300.0

    # The breach window, not the 60s default: a 120s-old record must be counted.
    assert warned["top_consumers"] == [
        {"role": "http-endpoint", "surface": "GET /api/logs", "count": 1, "compute_ms_total": 90.0},
    ]

    fields = events[0][0][3]
    # 90% of a core integrated across 2000->2300 is 270 CPU-s; 90ms of it is profiled.
    assert fields["cpu_ms_consumed"] == pytest.approx(270000.0, rel=1e-6)
    assert fields["attributed_ms"] == pytest.approx(90.0)
    assert fields["attributed_percent"] == pytest.approx(0.033, abs=0.001)

    message = logs[0][0][2]
    assert "consuming 270.0 CPU-s" in message
    assert "unattributed 100.0%" in message
    assert "the cause is unprofiled, not the list below" in message
    assert "(latest 1s sample)" in message


def test_runtime_python_profile_reports_named_native_threads():
    webapp = app_module.TmuxWebtermApp([])
    try:
        response = webapp.handle_control_request({
            "action": "runtime_profile",
            "duration_seconds": 0.01,
            "interval_seconds": 0.005,
        })
    finally:
        webapp.control_server.stop()

    assert response["ok"] is True
    profile = response["profile"]
    assert profile["duration_seconds"] == 0.05
    assert profile["sample_rounds"] >= 1
    current = next(row for row in profile["threads"] if row["native_id"] == threading.get_native_id())
    assert current["name"] == threading.current_thread().name
    assert current["top_stacks"]


def test_runtime_control_report_returns_only_safe_in_memory_filesystem_batch_attribution(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    try:
        webapp.record_performance_sample(
            "http-endpoint",
            "POST /api/fs/batch",
            compute_ms=17.5,
            payload_bytes=321,
            count=1,
            details={
                "fs_batch": True,
                "fs_batch_size": 2,
                "fs_batch_operations": '{"info": 1, "list": 1}',
                "fs_batch_path_hashes": '["0123456789abcdef"]',
                "fs_batch_triggers": '{"watch-diff-fallback": 2}',
                "fs_batch_client_revision": "1234-5678",
                "fs_batch_client_scope": "browser",
                "paths": "/private/credential.txt",
            },
        )
        monkeypatch.setattr(webapp, "runtime_cache_dir_stats", lambda *_args: (_ for _ in ()).throw(AssertionError("control report must not scan cache trees")))
        monkeypatch.setattr(webapp, "runtime_local_services", lambda: (_ for _ in ()).throw(AssertionError("control report must not probe local services")))
        monkeypatch.setattr(webapp, "transcripts_payload", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("control report must not scan transcripts")))
        response = webapp.handle_control_request({"action": "runtime_report"})
    finally:
        webapp.control_server.stop()

    assert response["ok"] is True
    report = response["report"]
    assert {"state_dir", "owner", "refresh", "caches", "search_index", "local_services", "top_endpoints", "top_background_work", "top_event_types", "client_events", "chat", "login_throttle", "largest_active_transcripts", "transcripts_cache", "filesystem_batch"} <= report.keys()
    assert report["refresh"]["bounded"] is True
    assert report["caches"]["session_files"]["truncated"] is True
    assert len(report["filesystem_batch"]) == 1
    row = report["filesystem_batch"][0]
    assert row["time"] > 0
    assert {key: value for key, value in row.items() if key != "time"} == {
        "compute_ms": 17.5,
        "payload_bytes": 321,
        "batch_size": 2,
        "operations": '{"info": 1, "list": 1}',
        "path_hashes": '["0123456789abcdef"]',
        "triggers": '{"watch-diff-fallback": 2}',
        "client_revision": "1234-5678",
        "client_scope": "browser",
    }
    assert "credential.txt" not in json.dumps(report, sort_keys=True)


def test_runtime_report_payload_reports_owner_cache_endpoints_events_and_transcripts(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    monkeypatch.setattr(app_module, "TABBER_ACTIVITY_CACHE_DIR", tmp_path / "activity-cache")
    monkeypatch.setattr(app_module.file_index, "INDEX_DIR", tmp_path / "search-index")
    monkeypatch.setattr(app_module.file_index, "runtime_diagnostics", lambda: {
        "root_count": 1,
        "build_count": 2,
        "full_build_count": 1,
        "incremental_build_count": 1,
        "scanned_entries": 7,
        "ignored_entries": 3,
        "cache_bytes": 5,
        "write_bytes": 9,
        "truncated_roots": 0,
        "roots": [{"root": "/repo", "last_duration_ms": 4.2}],
    })
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
        monkeypatch.setattr(webapp.search_indexer, "runtime_status", lambda: {
            "service": "indexd",
            "pid": 4321,
            "started_at": 100.0,
            "version": 1,
            "socket": "/tmp/indexd.sock",
            "healthy": True,
            "clients": 2,
            "queues": {"interactive": 0, "normal": 1, "maintenance": 0},
            "active_task": "index-refresh",
            "cache": {"roots": 1, "bytes": 5, "write_bytes": 9},
            "last_success": 101.0,
            "last_failure": "",
            "restart_backoff_seconds": 0.0,
            "generation": 0,
            "record": {},
            "resources": {"cpu_percent": None, "rss_bytes": None},
        })
        monkeypatch.setattr(webapp.stats_current_runtime, "status", lambda: {
            "service": {"ok": True, "pid": 0, "migration": {"state": "ready"}},
            "families": {},
        })
        monkeypatch.setattr(webapp.job_client, "runtime_status", lambda: {
            "service": "jobd", "pid": 0, "resources": {"cpu_percent": None, "rss_bytes": None},
        })
        monkeypatch.setattr(webapp.approval_client, "runtime_status", lambda: {
            "service": "approvald", "pid": 0, "resources": {"cpu_percent": None, "rss_bytes": None},
        })
        payload = webapp.runtime_report_payload(background_status=background_status, owner_debug={"generations": []}, owner_control_response={"ok": True})
    finally:
        webapp.control_server.stop()

    assert payload["owner"]["current_owner"] == {"port": 8002}
    assert payload["refresh"]["coalescing"]["recent_pending_count"] == 2
    recurring = payload["refresh"]["recurring_work"]
    assert {row["owner"] for row in recurring} == {*app_module.CLIENT_EVENT_RECURRING_WORK_SPECS, "sse_heartbeat", "update_check", "approvald_auto_approve"}
    assert all(set(row) == {"owner", "class", "cadence_seconds", "demanded", "attempts", "useful", "no_change", "failures", "last_attempt_at", "last_useful_at", "next_due_in_seconds"} for row in recurring)
    assert all(row["attempts"] == 0 and row["useful"] == 0 and row["no_change"] == 0 for row in recurring)
    assert payload["caches"]["session_files"]["files"] == 1
    assert payload["caches"]["session_files"]["bytes"] == 3
    assert payload["caches"]["activity"]["bytes"] == 5
    assert payload["search_index"]["build_count"] == 2
    assert payload["search_index"]["incremental_build_count"] == 1
    assert payload["search_index"]["scanned_entries"] == 7
    assert payload["search_index"]["ignored_entries"] == 3
    assert payload["search_index"]["cache_bytes"] == 5
    assert payload["search_index"]["write_bytes"] == 9
    assert payload["local_services"]["totals"] == {"processes": 1, "cpu_percent": 0.0, "rss_bytes": 0}
    assert payload["local_services"]["services"][0]["socket"] == "/tmp/indexd.sock"
    assert payload["local_services"]["services"][0]["started_at"] == 100.0
    assert "prompt" not in payload["local_services"]["services"][0]
    assert payload["top_endpoints"][0]["surface"] == "GET /api/session-files"
    assert payload["top_background_work"][0]["role"] == "session-files"
    assert payload["top_background_work"][0]["surface"] == "cache-entry"
    assert payload["top_event_types"][0] == {"type": "background_refresh_done", "count": 2}
    assert payload["largest_active_transcripts"][0]["path"] == str(large)
    assert payload["largest_active_transcripts"][0]["bytes"] == len("large transcript payload")
    assert payload["chat"]["subscribers"] == 0
    assert set(payload["chat"]["events"]) == {"chat_messages_changed", "chat_typing_changed"}
    assert set(payload["chat"]["store"]) >= {"database_bytes", "message_rows", "typing_leases", "prune_runs"}
    assert "body" not in payload["chat"] and "query" not in payload["chat"] and "browser" not in payload["chat"]
    # Login throttle aggregates are present and privacy-safe (counts/latency only).
    throttle = payload["login_throttle"]
    assert set(throttle) >= {"allowed", "blocked", "blocked_total", "active_rows", "locked_usernames", "decision_ms_avg", "healthy", "schema_version"}
    assert isinstance(throttle["blocked"], dict)
    assert "row_key" not in throttle and "username" not in throttle and "ip" not in throttle


def test_system_status_payload_is_live_and_does_not_force_transcript_refresh(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    transcript_forces = []
    monkeypatch.setattr(webapp, "transcripts_payload", lambda force=False: transcript_forces.append(force) or {"sessions": {}, "cache": {"hit": True}})
    monkeypatch.setattr(webapp, "current_stats_sample", lambda: pytest.fail("System diagnostics must not collect CPU"))
    monkeypatch.setattr(webapp, "latest_stats_sample", lambda: {
        "pid": 321,
        "started_at": 100.0,
        "uptime_seconds": 25.0,
        "cpu_percent": 3.5,
        "system_cpu_percent": 12.0,
        "rss_bytes": 4096,
    })
    try:
        payload = webapp.system_status_payload()
    finally:
        webapp.control_server.stop()

    assert transcript_forces == [False]
    assert payload["ok"] is True
    assert payload["generated_at"] > 0
    assert payload["server"] == {
        "version": app_module.YOLOMUX_VERSION,
        "pid": 321,
        "started_at": 100.0,
        "uptime_seconds": 25.0,
        "cpu_percent": 3.5,
        "system_cpu_percent": 12.0,
        "rss_bytes": 4096,
    }
    assert payload["tmux_signal_watcher"] == {
        "state": "never-started",
        "healthy": False,
        "reason_code": "not_started",
        "reason": "Tmux signal watcher has not been started",
        "sessions": [],
        "thread_alive": False,
        "process_pid": 0,
        "demanded": False,
    }
    subscriber_id, _ = webapp.client_events.subscribe(channels="core")
    try:
        demanded = webapp.tmux_signal_event_watcher_status()
    finally:
        webapp.client_events.unsubscribe(subscriber_id)
    assert demanded["state"] == "never-started"
    assert demanded["demanded"] is True


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


def test_stats_agent_idle_means_not_ask_run_or_transition(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(webapp, "notification_transition_seconds", lambda: 60.0)
    try:
        with webapp.stats_collection_state.agent_activity_lock:
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


def test_stats_agent_old_completion_is_not_counted_as_an_indefinite_transition():
    webapp = app_module.TmuxWebtermApp(["1"])
    try:
        with webapp.stats_collection_state.agent_activity_lock:
            recent = webapp.stats_agent_activity_kind_locked({"state": "idle", "working_stopped_ts": 950.0}, "recent", 1000.0, 60.0)
            expired = webapp.stats_agent_activity_kind_locked({"state": "idle", "working_stopped_ts": 900.0}, "expired", 1000.0, 60.0)
    finally:
        webapp.control_server.stop()

    assert recent == "transition"
    assert expired == "idle"


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

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.wait()
        return False

    def terminate(self):
        self.terminated = True
        self._returncode = 0

    def wait(self, timeout=None):
        self._returncode = 0
        return 0

    def communicate(self, input=None, timeout=None):
        self._returncode = 0
        return self.stdout.read(), self.stderr.read()

    def kill(self):
        self._returncode = -9


def test_auto_approve_status_refreshes_session_order(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["old"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["new"], None))
    monkeypatch.setattr(webapp, "auto_approve_session_status", lambda session, **_kwargs: {"target": session})
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    try:
        payload, status = webapp.build_auto_approve_status()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["session_order"] == ["new"]
    assert payload["sessions"] == {"new": {"target": "new"}}
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
        assert "attention_acks" not in payload
        assert payload["prompt_attention_acknowledged"] is True
        assert payload["prompt"]["attention_acknowledged"] is True
    finally:
        webapp.control_server.stop()


def test_agent_window_attention_key_uses_shared_per_window_hash_transitions(tmp_path):
    webapp = app_module.TmuxWebtermApp(["1"])
    status_path = webapp.host_identity.namespaced_path(tmp_path / "state", "tmux-AI-status.json")
    webapp.tmux_ai_status_path = status_path
    assert webapp.tmux_ai_status_path == status_path
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
    assert status_path.exists()


def test_stats_agent_window_rows_uses_statusd_snapshot(monkeypatch):
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
    monkeypatch.setattr(webapp, "status_snapshot_payload", lambda: cached_payload)
    try:
        rows = webapp.stats_agent_window_rows()
    finally:
        webapp.control_server.stop()

    assert rows == [
        {"kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude", "transcript": "/tmp/claude.jsonl", "session": "1"},
    ]
    assert "session" not in cached_payload["sessions"]["1"]["agent_windows"][0]


def test_stats_agent_window_rows_does_not_refresh_web_cache(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    cached_payload = {
        "session_order": ["1"],
        "sessions": {"1": {"agent_windows": [{"kind": "codex", "state": "working", "window_index": 0}]}},
    }
    monkeypatch.setattr(webapp, "status_snapshot_payload", lambda: cached_payload)
    try:
        rows = webapp.stats_agent_window_rows()
    finally:
        webapp.control_server.stop()

    assert rows == [{"kind": "codex", "state": "working", "window_index": 0, "session": "1"}]


def test_stats_agent_window_rows_excludes_a_roster_marked_stale_pane(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    cached_payload = {
        "session_order": ["1"],
        "sessions": {"1": {"agent_windows": [
            {"kind": "codex", "state": "working", "window_index": 0, "pane_target": "%1"},
            {"kind": "codex", "state": "transition", "window_index": 1, "pane_target": "%2", "stale": True},
        ]}},
    }
    monkeypatch.setattr(webapp, "status_snapshot_payload", lambda: cached_payload)
    try:
        rows = webapp.stats_agent_window_rows()
    finally:
        webapp.control_server.stop()

    assert rows == [{"kind": "codex", "state": "working", "window_index": 0, "pane_target": "%1", "session": "1"}]


def test_stats_agent_window_rows_returns_empty_when_statusd_is_unavailable(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(webapp, "status_snapshot_payload", lambda: None)
    try:
        rows = webapp.stats_agent_window_rows()
    finally:
        webapp.control_server.stop()

    assert rows == []


def test_tabber_activity_keeps_statusd_roster_when_tmux_discovery_temporarily_has_no_agents(monkeypatch):
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(app_module, "discover_sessions", lambda _sessions: ({"1": info}, []))
    monkeypatch.setattr(webapp, "tmux_recency_ordered_sessions", lambda session_names=None, payload=None: ["1"])
    monkeypatch.setattr(webapp, "activity_snapshot_with_recency", lambda: {})
    monkeypatch.setattr(webapp, "merge_shared_attention_acks", lambda: False)
    monkeypatch.setattr(webapp, "status_snapshot_payload", lambda: {
        "agent_window_snapshot_revision": 7,
        "sessions": {"1": {"agent_windows": [{
            "window_index": 2, "pane_target": "%9", "kind": "codex", "state": "working", "window_label": "2:codex",
        }]}},
    })
    try:
        payload = webapp.build_activity_payload()
    finally:
        webapp.control_server.stop()

    assert payload["agent_window_snapshot_revision"] == 7
    assert payload["agent_windows"] == {"1": [{
        "window_index": 2, "pane_target": "%9", "kind": "codex", "state": "working", "window_label": "2:codex",
    }]}
    assert [(row["session"], row["window"], row["pane_target"], row["agent_kind"], row["state"])
            for row in payload["agents"]] == [("1", "2", "%9", "codex", "working")]


def test_status_snapshot_payload_preserves_statusd_generation_for_agent_window_consumers(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    body = json.dumps({
        "sessions": {
            "1": {"agent_windows": [{"window_index": 0, "pane_target": "%1", "kind": "codex", "state": "idle"}]},
        },
    }).encode("utf-8")
    metadata = {
        "ok": True,
        "protocol_version": statusd_protocol.STATUSD_PROTOCOL_VERSION,
        "generation": 17,
        "status": 200,
        "built_at": 1.0,
    }
    monkeypatch.setattr(webapp.status_client, "snapshot", lambda sessions, timeout: (metadata, body))
    try:
        payload = webapp.status_snapshot_payload()
    finally:
        webapp.control_server.stop()

    assert payload is not None
    revision, rows = webapp.agent_window_snapshot_rows_by_target(payload)
    assert revision == 17
    assert rows[("1", "0", "%1", "codex")]["state"] == "idle"


def test_tabber_activity_replaces_jobd_empty_rows_with_same_revision_status_roster(monkeypatch):
    session = "1"
    pane = PaneInfo(session, "0", "0", "%1", "1:0.0", "/repo", "codex", True, True, "codex", 101)
    info = SessionInfo(session=session, panes=[pane], selected_pane=pane, agents=[AgentInfo(session, "codex", 101, "%1", "codex", "/repo", "running", "sid-1", None, None)])
    webapp = app_module.TmuxWebtermApp([session])
    monkeypatch.setattr(app_module, "discover_sessions", lambda _sessions: ({session: info}, []))
    monkeypatch.setattr(webapp, "tmux_recency_ordered_sessions", lambda session_names=None, payload=None: [session])
    monkeypatch.setattr(webapp, "cached_session_files_payloads_for_infos", lambda agent_infos, hours=24.0: {session: {}})
    monkeypatch.setattr(webapp, "activity_snapshot_with_recency", lambda: {})
    monkeypatch.setattr(webapp, "merge_shared_attention_acks", lambda: False)
    monkeypatch.setattr(webapp, "agent_window_screen_state", lambda agent: {"key": "idle", "text": ""})
    monkeypatch.setattr(webapp, "compute_tabber_activity_rows_via_jobd", lambda *args, **kwargs: {session: {"agents": [], "agent_windows": []}})
    monkeypatch.setattr(webapp, "status_snapshot_payload", lambda: {"agent_window_snapshot_revision": 7, "sessions": {session: {"agent_windows": [{"window_index": 0, "pane_target": "%1", "kind": "codex", "state": "working"}]}}})
    try:
        payload = webapp.build_activity_payload()
    finally:
        webapp.control_server.stop()
    assert payload["agent_windows"][session][0]["state"] == "working"
    assert [(row["session"], row["agent_kind"], row["state"]) for row in payload["agents"]] == [(session, "codex", "working")]
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


def test_client_status_attention_fallback_is_interactive_with_jitter(monkeypatch):
    assert app_module.SERVER_AUTO_APPROVE_EVENT_POLL_SECONDS == pytest.approx(1.5)
    assert app_module.SERVER_TMUX_SIGNAL_EVENT_POLL_SECONDS == pytest.approx(1.5)
    assert app_module.SERVER_INTERACTIVE_EVENT_POLL_JITTER_SECONDS == pytest.approx(0.5)

    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(app_module.random, "uniform", lambda lower, upper: upper)
        assert webapp.server_attention_ack_event_poll_seconds() == pytest.approx(1.875)
        assert webapp.server_tmux_signal_event_poll_seconds() == pytest.approx(1.875)
        monkeypatch.setattr(app_module.random, "uniform", lambda lower, upper: lower)
        assert webapp.server_attention_ack_event_poll_seconds() == pytest.approx(1.125)
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
            webapp.client_event({"session": "missing", "type": "test", "message": "message"}),
            webapp.acknowledge_attention({}),
            webapp.build_auto_approve_status("missing"),
            webapp.yoagent_controller.cancel_yoagent_chat(""),
            webapp.tmux_session_exists_payload("1"),
        ]
    finally:
        webapp.control_server.stop()

    assert all(status != HTTPStatus.OK for _payload, status in failures)
    assert all(payload.get("user_message", {}).get("key") for payload, _status in failures)
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
        assert len(webapp.session_files_service.cache) == app_module.SESSION_FILES_CACHE_MAX_ITEMS
        assert (0,) not in webapp.session_files_service.cache
        assert (app_module.SESSION_FILES_CACHE_MAX_ITEMS + 2,) in webapp.session_files_service.cache
    finally:
        webapp.control_server.stop()


def test_client_event_watch_sleep_ignores_watchd_owned_filesystem_deadlines(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    subscriber_id = None
    try:
        monkeypatch.setattr(
            app_module,
            "settings_payload",
            lambda: {"settings": {"performance": {"server_event_poll_ms": 250}}},
        )
        record = webapp.client_watch_service.event_watcher_record
        record.next_file_poll_at = 100.5
        record.next_background_file_poll_at = 100.75
        record.next_signature_poll_at = 100.25
        record.next_attention_ack_poll_at = 100.6
        record.next_watched_pr_poll_at = 200.0
        assert webapp.client_event_watch_sleep_seconds(100.0) == pytest.approx(60.0)
        subscriber_id, _subscriber_queue = webapp.client_events.subscribe(channels={"files"})
        assert webapp.client_event_watch_sleep_seconds(100.0) == pytest.approx(60.0)
        record.filesystem_healthy = True
        record.next_signature_poll_at = 101.25
        assert webapp.client_event_watch_sleep_seconds(100.0) == pytest.approx(60.0)
        record.filesystem_healthy = False
        record.next_signature_poll_at = 0.0
        assert webapp.client_event_watch_sleep_seconds(100.0) == pytest.approx(60.0)
    finally:
        if subscriber_id is not None:
            webapp.client_events.unsubscribe(subscriber_id)
        webapp.control_server.stop()


def test_client_event_watch_uses_watchd_without_visible_or_background_scans():
    webapp = app_module.TmuxWebtermApp([])
    subscriber_id, _subscriber_queue = webapp.client_events.subscribe(channels={"files"})
    record = webapp.client_watch_service.event_watcher_record

    class StopAfterOneWait:
        def clear(self):
            return None

        def wait(self, _timeout=None):
            record.stop_event.set()
            return True

    record.filesystem_healthy = True
    record.next_signature_poll_at = time.monotonic() + 60.0
    record.wake_event = StopAfterOneWait()
    assert not hasattr(webapp, "poll_client_file_events_once")
    assert not hasattr(webapp, "poll_client_background_file_events_once")
    try:
        webapp.client_event_watch_loop(record)
    finally:
        webapp.client_events.unsubscribe(subscriber_id)
        webapp.control_server.stop()


def test_client_event_watch_does_not_poll_tmux_while_control_watcher_is_healthy(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    subscriber_id, _subscriber_queue = webapp.client_events.subscribe(channels={"status"})
    record = webapp.client_watch_service.event_watcher_record

    class StopAfterOneWait:
        def clear(self):
            return None

        def wait(self, _timeout=None):
            record.stop_event.set()
            return True

    now = time.monotonic()
    record.next_attention_ack_poll_at = now + 60.0
    record.next_tmux_signal_poll_at = 0.0
    record.wake_event = StopAfterOneWait()
    monkeypatch.setattr(webapp, "tmux_signal_event_watcher_healthy", lambda: True)
    monkeypatch.setattr(webapp, "poll_tmux_signals_client_event_once", lambda: pytest.fail("healthy control watcher must not run fallback tmux poll"))
    try:
        webapp.client_event_watch_loop(record)
    finally:
        webapp.client_events.unsubscribe(subscriber_id)
        webapp.control_server.stop()


def test_client_event_watch_polls_tmux_when_control_watcher_is_unhealthy(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    subscriber_id, _subscriber_queue = webapp.client_events.subscribe(channels={"status"})
    record = webapp.client_watch_service.event_watcher_record

    class StopAfterOneWait:
        def clear(self):
            return None

        def wait(self, _timeout=None):
            record.stop_event.set()
            return True

    now = time.monotonic()
    calls = []
    record.next_attention_ack_poll_at = now + 60.0
    record.next_tmux_signal_poll_at = 0.0
    record.wake_event = StopAfterOneWait()
    monkeypatch.setattr(webapp, "tmux_signal_event_watcher_healthy", lambda: False)
    monkeypatch.setattr(webapp, "poll_tmux_signals_client_event_once", lambda: calls.append(time.monotonic()) or [])
    try:
        webapp.client_event_watch_loop(record)
    finally:
        webapp.client_events.unsubscribe(subscriber_id)
        webapp.control_server.stop()

    assert len(calls) == 1
    assert record.next_tmux_signal_poll_at > now


def test_client_event_watch_does_not_poll_attention_acks_while_native_watcher_is_healthy(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    subscriber_id, _subscriber_queue = webapp.client_events.subscribe(channels={"attention"})
    record = webapp.client_watch_service.event_watcher_record

    class StopAfterOneWait:
        def clear(self):
            return None

        def wait(self, _timeout=None):
            record.stop_event.set()
            return True

    now = time.monotonic()
    record.filesystem_healthy = True
    record.next_attention_ack_poll_at = 0.0
    record.next_tmux_signal_poll_at = now + 60.0
    record.wake_event = StopAfterOneWait()
    monkeypatch.setattr(webapp, "poll_attention_acks_client_event_once", lambda: pytest.fail("healthy native watcher must not run attention-ack timer poll"))
    monkeypatch.setattr(webapp, "tmux_signal_event_watcher_healthy", lambda: True)
    try:
        webapp.client_event_watch_loop(record)
    finally:
        webapp.client_events.unsubscribe(subscriber_id)
        webapp.control_server.stop()


def test_status_generation_waiter_publishes_once_per_advanced_generation(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    responses = iter([
        {"ok": True, "changed": False, "generation": 7},
        {"ok": True, "changed": True, "generation": 8},
    ])
    record = webapp.client_watch_service.event_watcher_record
    record.status_generation = 7
    monkeypatch.setattr(webapp.status_client, "probe_generation", lambda _generation: next(responses))
    monkeypatch.setattr(
        webapp.status_client,
        "snapshot",
        lambda _sessions, timeout: (
            {"ok": True, "protocol_version": statusd_protocol.STATUSD_PROTOCOL_VERSION, "status": 200, "generation": 8, "stale": False, "built_at": 1.0, "content_type": "application/json"},
            b'{"session_order":[],"sessions":{}}',
        ),
    )
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    original_publish = webapp.publish_client_event
    def publish_then_stop(*args, **kwargs):
        result = original_publish(*args, **kwargs)
        record.status_generation_stop_event.set()
        return result
    monkeypatch.setattr(webapp, "publish_client_event", publish_then_stop)
    try:
        webapp.status_generation_wait_loop(record)
    finally:
        webapp.control_server.stop()

    assert [event_type for event_type, _payload in events] == ["auto_approve_changed"]
    assert events[0][1]["generation"] == 8
    assert events[0][1]["refresh"] is False
    assert events[0][1]["data"] == {"session_order": [], "sessions": {}}


def test_status_generation_waiter_uses_immediate_probe_and_stop_aware_cadence(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    record = webapp.client_watch_service.event_watcher_record
    probes = []
    waits = []
    events = []
    responses = iter([
        {"ok": True, "changed": False, "generation": 7},
        {"ok": True, "changed": True, "generation": 8},
    ])

    class CadenceStopEvent:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, timeout):
            waits.append(timeout)
            return self.stopped

        def set(self):
            self.stopped = True

        def clear(self):
            self.stopped = False

    record.status_generation = 7
    record.status_generation_stop_event = CadenceStopEvent()
    monkeypatch.setattr(webapp.status_client, "probe_generation", lambda generation: probes.append(generation) or next(responses), raising=False)
    monkeypatch.setattr(webapp.status_client, "wait_generation", lambda *_args, **_kwargs: pytest.fail("generation watcher must not occupy a long-poll handler"))
    monkeypatch.setattr(
        webapp.status_client,
        "snapshot",
        lambda _sessions, timeout: (
            {"ok": True, "protocol_version": statusd_protocol.STATUSD_PROTOCOL_VERSION, "status": 200, "generation": 8, "stale": False, "built_at": 1.0, "content_type": "application/json"},
            b'{"session_order":[],"sessions":{}}',
        ),
    )

    def publish_then_stop(event_type, payload=None, **_kwargs):
        events.append((event_type, payload or {}))
        record.status_generation_stop_event.set()

    monkeypatch.setattr(webapp, "publish_client_event", publish_then_stop)
    try:
        webapp.status_generation_wait_loop(record)
    finally:
        webapp.control_server.stop()

    assert probes == [7, 7]
    assert waits == [app_module.STATUS_GENERATION_RPC_WAIT_SECONDS]
    assert [event_type for event_type, _payload in events] == ["auto_approve_changed"]
    assert events[0][1]["generation"] == 8


def test_status_generation_waiter_suppresses_generation_only_snapshot_change(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    record = webapp.client_watch_service.event_watcher_record
    record.status_generation = 7
    webapp.client_watch_service.auto_approve_payload = {
        "agent_window_snapshot_revision": 7,
        "session_order": [],
        "sessions": {},
    }
    monkeypatch.setattr(webapp.status_client, "probe_generation", lambda _generation: {"ok": True, "changed": True, "generation": 8})

    def snapshot(_sessions, timeout):
        record.stop_event.set()
        return (
            {"ok": True, "protocol_version": statusd_protocol.STATUSD_PROTOCOL_VERSION, "status": 200, "generation": 8, "stale": False, "built_at": 1.0, "content_type": "application/json"},
            b'{"agent_window_snapshot_revision":8,"session_order":[],"sessions":{}}',
        )

    monkeypatch.setattr(webapp.status_client, "snapshot", snapshot)
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        webapp.status_generation_wait_loop(record)
    finally:
        webapp.control_server.stop()

    assert events == []
    assert record.status_generation == 8
    assert webapp.client_watch_service.auto_approve_payload["agent_window_snapshot_revision"] == 8


def test_status_generation_watcher_is_demand_scoped_and_releases_its_lease(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    record = webapp.client_watch_service.event_watcher_record
    wait_entered = threading.Event()
    released = []
    monkeypatch.setattr(webapp.status_client, "acquire_generation_lease", lambda: {"ok": True, "lease_id": "lease-1"})
    monkeypatch.setattr(webapp.status_client, "snapshot", lambda _sessions, timeout: ({"ok": True, "status": 200, "generation": 7}, b"{}"))

    def probe_generation(_generation):
        wait_entered.set()
        return {"ok": True, "changed": False, "generation": 7}

    monkeypatch.setattr(webapp.status_client, "probe_generation", probe_generation)
    monkeypatch.setattr(webapp.status_client, "release_generation_lease", lambda lease_id: released.append(lease_id) or {"ok": True})
    try:
        assert webapp.start_status_generation_watcher(record) is True
        assert wait_entered.wait(timeout=1.0)
        webapp.stop_status_generation_watcher(record)
    finally:
        webapp.control_server.stop()

    assert released == ["lease-1"]
    assert record.status_generation_worker is None


def test_status_generation_watcher_stop_does_not_leave_an_obsolete_probe_or_cadence(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    record = webapp.client_watch_service.event_watcher_record
    wait_entered = threading.Event()
    probes = []
    monkeypatch.setattr(webapp.status_client, "acquire_generation_lease", lambda: {"ok": True, "lease_id": "lease-1"})
    monkeypatch.setattr(webapp.status_client, "snapshot", lambda _sessions, timeout: ({"ok": True, "status": 200, "generation": 7}, b"{}"))
    monkeypatch.setattr(webapp.status_client, "release_generation_lease", lambda _lease_id: {"ok": True})

    def probe_generation(generation):
        probes.append(generation)
        wait_entered.set()
        return {"ok": True, "changed": False, "generation": 7}

    monkeypatch.setattr(webapp.status_client, "probe_generation", probe_generation)
    try:
        assert webapp.start_status_generation_watcher(record) is True
        assert wait_entered.wait(timeout=1.0)
        webapp.stop_status_generation_watcher(record)
        assert record.status_generation_worker is None
        assert not any(
            thread.name == "statusd-generation-wait" and thread.is_alive()
            for thread in threading.enumerate()
        )
    finally:
        webapp.control_server.stop()

    assert probes == [7]
    assert app_module.STATUS_GENERATION_RPC_WAIT_SECONDS <= 1.0


def test_client_event_recurring_work_is_fixed_name_and_tracks_useful_vs_no_change():
    webapp = app_module.TmuxWebtermApp([])
    record = webapp.client_watch_service.event_watcher_record
    try:
        webapp.note_client_event_recurring_work(record, "watched_pr_reconcile", useful=False)
        webapp.note_client_event_recurring_work(record, "watched_pr_reconcile", useful=True)
        rows = {row["owner"]: row for row in webapp.client_event_recurring_work_snapshot(record, now=0.0)}
    finally:
        webapp.control_server.stop()

    watched_pr = rows["watched_pr_reconcile"]
    assert watched_pr["class"] == "external-reconcile"
    assert watched_pr["cadence_seconds"] == 60.0
    assert watched_pr["attempts"] == 2
    assert watched_pr["useful"] == 1
    assert watched_pr["no_change"] == 1
    assert watched_pr["last_attempt_at"] > 0
    assert watched_pr["last_useful_at"] > 0
    assert set(rows) == set(app_module.CLIENT_EVENT_RECURRING_WORK_SPECS)


def test_runtime_refresh_aggregates_approvald_recurring_work_without_target_identity():
    webapp = app_module.TmuxWebtermApp([])
    try:
        refresh = webapp.runtime_refresh_state({}, {"services": [{
            "service": "approvald",
            "recurring_work": {
                "class": "sample", "cadence_seconds": 0.5, "demanded": True,
                "attempts": 8, "useful": 2, "no_change": 5, "failures": 1,
                "last_attempt_at": 40.0, "last_useful_at": 30.0,
            },
        }]})
    finally:
        webapp.control_server.stop()

    row = next(item for item in refresh["recurring_work"] if item["owner"] == "approvald_auto_approve")
    assert row == {
        "owner": "approvald_auto_approve", "class": "sample", "cadence_seconds": 0.5, "demanded": True,
        "attempts": 8, "useful": 2, "no_change": 5, "failures": 1,
        "last_attempt_at": 40.0, "last_useful_at": 30.0, "next_due_in_seconds": 0.5,
    }
    assert "target" not in str(row)


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


def test_activity_summary_ready_signature_ignores_generated_timestamps(monkeypatch, legacy_activity_summary_enabled):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    payloads = [
        {"locale": "en", "generated_at": "first", "generated_ts": 1.0, "global": {"headline": "same"}, "sessions": {}},
        {"locale": "en", "generated_at": "second", "generated_ts": 2.0, "global": {"headline": "same"}, "sessions": {}},
    ]
    monkeypatch.setattr(webapp.client_watch_service, "snapshot", lambda: ([], [], {"visible": True, "locale": "en", "hours": 24}))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: payloads.pop(0))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.publish_activity_summary_ready_events(trigger="refresh") == ["activity_summary_ready"]
        assert webapp.publish_activity_summary_ready_events(trigger="refresh") == []
    finally:
        webapp.control_server.stop()

    assert [event_type for event_type, _payload in events] == ["activity_summary_ready"]


def test_auto_approve_client_event_patch_suppresses_noop_and_sends_changed_sessions_only():
    webapp = app_module.TmuxWebtermApp([])
    previous = {
        "agent_window_snapshot_revision": 7,
        "generated_at": 1.0,
        "session_order": ["1", "2"],
        "sessions": {
            "1": {"target": "1", "enabled": False, "agent_windows": [{"window_index": 0, "state": "idle"}]},
            "2": {"target": "2", "enabled": True, "agent_windows": [{"window_index": 1, "state": "working"}]},
        },
        "rules": {"mode": "safe"},
    }
    same = copy.deepcopy(previous)
    same.update({"agent_window_snapshot_revision": 8, "generated_at": 2.0})
    changed = copy.deepcopy(same)
    changed["agent_window_snapshot_revision"] = 9
    changed["sessions"]["2"]["agent_windows"][0]["state"] = "needs-input"
    try:
        assert webapp.auto_approve_client_event_patch(previous, same) is None
        patch = webapp.auto_approve_client_event_patch(same, changed)
    finally:
        webapp.control_server.stop()

    assert patch == {
        "patch": True,
        "collection": "sessions",
        "changes": {"2": changed["sessions"]["2"]},
        "removed_keys": [],
        "fields": {"agent_window_snapshot_revision": 9},
        "removed_fields": [],
    }
    assert len(json.dumps(patch, separators=(",", ":"))) < len(json.dumps({"data": changed}, separators=(",", ":")))


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

    assert events == [("tmux_signals_changed", {
        "patch": True,
        "collection": "windows",
        "changes": {
            "1:0": {"session": "1", "window_index": 0, "active": False},
            "1:1": {"session": "1", "window_index": 1, "active": True},
        },
        "removed_keys": [],
        "fields": {"generated_at": 2.0},
        "removed_fields": [],
    })]


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
        "collection": "windows",
        "changes": {},
        "removed_keys": ["1:1"],
        "fields": {
            "window_count": 1,
            "generated_at": 10.4,
            "removed_window_keys": ["1:1"],
            "removed_window_event_at": 10.25,
            "removed_window_event_type": "pane-exited",
        },
        "removed_fields": [],
    })]


def test_tmux_signal_patch_keeps_removed_window_origin_when_metadata_changes(monkeypatch):
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

    assert events == [("tmux_signals_changed", {
        "patch": True,
        "collection": "windows",
        "changes": {},
        "removed_keys": ["1:1"],
        "fields": {
            "pane_count": 1,
            "window_count": 1,
            "generated_at": 20.4,
            "removed_window_keys": ["1:1"],
            "removed_window_event_at": 20.1,
            "removed_window_event_type": "pane-died",
        },
        "removed_fields": [],
    })]


def test_tmux_signal_event_does_not_force_auto_approve_poll():
    webapp = app_module.TmuxWebtermApp([])
    try:
        record = webapp.client_watch_service.event_watcher_record
        record.next_tmux_signal_poll_at = 456.0
        webapp.tmux_signal_cache.set("snapshot", {"ok": True})

        webapp.handle_tmux_signal_event({"event": "pane_changed"})

        assert record.next_tmux_signal_poll_at == pytest.approx(456.0)
        assert record.tmux_signal_refresh_at > 0
        assert webapp.tmux_signal_cache.get_or_miss("snapshot") is app_module.CACHE_MISS
        assert record.wake_event.is_set()
    finally:
        webapp.control_server.stop()


def test_tmux_topology_event_invalidates_the_retained_statusd_roster(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    invalidations = []
    monkeypatch.setattr(webapp.status_client, "invalidate", lambda reason: invalidations.append(reason))
    try:
        webapp.handle_tmux_signal_event({"event": "pane-died", "time": 1.0})
        webapp.handle_tmux_signal_event({"event": "output"})
    finally:
        webapp.control_server.stop()
    assert invalidations == ["tmux-topology"]


def test_status_discovery_prunes_dead_agent_window_transition_identity():
    pane = PaneInfo("1", "0", "0", "%1", "1:0.0", "/repo", "codex", True, True, "codex", 1)
    info = SessionInfo("1", [pane], pane, [AgentInfo("1", "codex", 1, "%1", "codex", "/repo", "running", "sid", None, None)])
    webapp = app_module.TmuxWebtermApp([])
    try:
        live_key = "1\x1f0\x1f%1\x1fcodex"
        dead_key = "1\x1f0\x1f%2\x1fcodex"
        other_session = "2\x1f0\x1f%3\x1fcodex"
        webapp.agent_window_transition_state = {live_key: {"state": "working"}, dead_key: {"state": "idle"}, other_session: {"state": "idle"}}
        webapp.prune_absent_agent_window_transition_state({"1": info})
        assert set(webapp.agent_window_transition_state) == {live_key, other_session}
    finally:
        webapp.control_server.stop()


def test_tmux_output_events_share_one_debounced_metadata_refresh(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    clock = [100.0]
    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
    try:
        record = webapp.client_watch_service.event_watcher_record
        record.next_tmux_signal_poll_at = 456.0
        webapp.tmux_signal_cache.set("snapshot", {"ok": True})

        webapp.handle_tmux_signal_event({"type": "output"})

        scheduled_at = 100.0 + app_module.TMUX_SIGNAL_SNAPSHOT_TTL_SECONDS
        assert record.next_tmux_signal_poll_at == pytest.approx(456.0)
        assert record.tmux_signal_refresh_at == pytest.approx(scheduled_at)
        assert webapp.tmux_signal_cache.get_or_miss("snapshot") is app_module.CACHE_MISS
        assert record.wake_event.is_set()

        record.wake_event.clear()
        webapp.tmux_signal_cache.set("snapshot", {"ok": True})
        clock[0] = 100.1
        webapp.handle_tmux_signal_event({"type": "extended-output"})

        assert record.tmux_signal_refresh_at == pytest.approx(scheduled_at)
        assert webapp.tmux_signal_cache.get_or_miss("snapshot") == {"ok": True}
        assert record.wake_event.is_set() is False
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
    assert webapp.client_watch_service.event_watcher_record.wake_event.is_set()


def test_save_settings_retention_reduction_prunes_chat_immediately(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    calls = []
    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"chat": {"retention_days": 30}}})
    monkeypatch.setattr(
        app_module,
        "save_settings",
        lambda patch: {"settings": {"chat": {"retention_days": 7}, "appearance": {}}, "mtime_ns": 123},
    )
    monkeypatch.setattr(webapp.chat_store, "prune_if_due", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(webapp, "sync_tmux_theme_from_settings", lambda *args, **kwargs: None)
    try:
        webapp.save_settings({"chat": {"retention_days": 7}})
    finally:
        webapp.control_server.stop()

    assert calls == [{"retention_days": 7, "previous_retention_days": 30}]


def test_two_webapps_reconcile_chat_from_shared_database_and_fanout_once(monkeypatch, tmp_path):
    class FakeControlServer:
        def __init__(self, _handler):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(app_module.common, "STATE_DIR", tmp_path)
    monkeypatch.setattr(app_module, "YolomuxControlServer", FakeControlServer)
    app1 = app_module.TmuxWebtermApp([])
    app2 = app_module.TmuxWebtermApp([])
    subscriber_id, subscriber_queue = app2.client_events.subscribe("chat", "browser-b")

    def fanout(event_type, payload=None, **_kwargs):
        app2.handle_background_client_event({"event_type": event_type, "payload": payload or {}})
        return {"type": event_type, "payload": payload or {}}

    monkeypatch.setattr(app1, "publish_background_client_event", fanout)
    try:
        sent = app1.chat_send(
            "alice",
            {"browser_instance_id": "browser-a", "client_message_uuid": "message-a", "body": "cross-process 😀"},
            "en",
        )
        event = subscriber_queue.get_nowait()
        assert event["type"] == "chat_messages_changed"
        assert subscriber_queue.empty()
        delta = app2.chat_delta("bob", after="")
        assert [message["body"] for message in delta["messages"]] == ["cross-process 😀"]
        assert delta["revision"] == sent["revision"]

        app1.chat_typing("alice", "browser-a", True)
        assert subscriber_queue.get_nowait()["type"] == "chat_typing_changed"
        bootstrap = app2.chat_bootstrap("bob", "browser-b")
        assert [lease["username"] for lease in bootstrap["typing"]] == ["alice"]
    finally:
        app2.client_events.unsubscribe(subscriber_id)
        app1.control_server.stop()
        app2.control_server.stop()


def test_chat_yoagent_delegates_to_existing_controller_and_publishes_reply(monkeypatch):
    source = SimpleNamespace(id=17)
    calls = []
    service = SimpleNamespace(
        yoagent_source=lambda **kwargs: calls.append(("source", kwargs)) or (source, "what should I work on?"),
        record_yoagent_reply=lambda **kwargs: calls.append(("record", kwargs)) or ({
            "message": {"id": 18, "username": "YO!agent", "body": kwargs["answer"]}, "revision": 18,
        }, True),
    )
    def fake_yoagent(payload, access_role):
        calls.append(("yoagent", payload, access_role))
        time.sleep(0.03)
        return {"answer": "Work on the failing test."}, HTTPStatus.OK

    monkeypatch.setattr(app_module, "CHAT_TYPING_LEASE_SECONDS", 0.02)
    webapp = SimpleNamespace(
        chat_service=service,
        chat_typing=lambda username, instance, active: calls.append(("typing", username, instance, active)),
        yoagent_controller=SimpleNamespace(yoagent_chat=fake_yoagent),
        publish_background_client_event=lambda *args, **kwargs: calls.append(("publish", args, kwargs)),
    )

    result = app_module.TmuxWebtermApp.chat_yoagent(
        webapp,
        "guest",
        "readonly",
        {"browser_instance_id": "browser-a", "message_id": 17, "message": "spoofed"},
        "en",
    )

    assert result["source_message_id"] == 17
    typing_calls = [call for call in calls if call[0] == "typing"]
    assert typing_calls[0] == ("typing", "YO!agent", "yolomux-yoagent-17", True)
    assert typing_calls[-1] == ("typing", "YO!agent", "yolomux-yoagent-17", False)
    assert sum(call[-1] is True for call in typing_calls) >= 2, "long YO!agent work refreshes the shared five-second lease"
    assert next(call for call in calls if call[0] == "yoagent") == (
        "yoagent",
        {"message": "what should I work on?", "locale": "en", "request_id": "yochat-17"},
        "readonly",
    )
    record_call = next(call for call in calls if call[0] == "record")
    publish_call = next(call for call in calls if call[0] == "publish")
    assert record_call[1]["answer"] == "Work on the failing test."
    assert publish_call[1][0] == "chat_messages_changed"


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
        payload, status = webapp.create_next_session("term", terminal="bash")
    finally:
        webapp.control_server.stop()

    commands = [args for args, _timeout in tmux_calls]
    assert status == HTTPStatus.OK
    assert payload["session"] == "1"
    assert payload["terminal"] == "bash"
    assert commands[0][:8] == ["new-session", "-d", "-s", "1", "-e", "TERM=xterm-256color", "-c", str(tmp_path)]
    assert ["set-option", "-t", "1:", "status", "off"] in commands
    assert ["set-option", "-t", "1:", "status-style", "bg=#7c3aed,fg=#ffffff"] in commands
    assert ["set-window-option", "-t", "1:", "pane-active-border-style", "fg=#7c3aed"] in commands
    assert commands[-1] == ["refresh-client", "-S"]
    assert webapp.tmux_theme_color == "purple"


def test_create_next_session_plan_generations_are_unique_and_javascript_safe(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(webapp, "refresh_sessions", lambda maintenance=True: [])
    try:
        first, first_status = webapp.create_next_session_plan()
        second, second_status = webapp.create_next_session_plan()
    finally:
        webapp.control_server.stop()

    assert first_status == second_status == HTTPStatus.OK
    assert first["session"] == second["session"] == "1"
    assert 0 < first["generation"] < second["generation"] <= (1 << 53) - 1


def test_create_next_session_uses_the_explicit_full_access_choice(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp([], dangerously_yolo=True)
    tmux_calls = []

    def fake_refresh_sessions(maintenance=True):
        del maintenance
        return []

    def fake_tmux(args, timeout=5.0):
        tmux_calls.append((args, timeout))
        return app_module.subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(webapp, "refresh_sessions", fake_refresh_sessions)
    monkeypatch.setattr(app_module, "available_agent_commands", lambda: ["codex"])
    monkeypatch.setattr(app_module, "session_workdir", lambda session: tmp_path)
    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"appearance": {}}})
    monkeypatch.setattr(app_module, "tmux", fake_tmux)
    try:
        payload, status = webapp.create_next_session("codex", dangerously_yolo=True)
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["dangerously_yolo"] is True
    assert tmux_calls[0][0][-1] == "codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust"


def test_create_next_session_rejects_full_access_without_server_opt_in(monkeypatch):
    webapp = app_module.TmuxWebtermApp([], dangerously_yolo=False)
    monkeypatch.setattr(webapp, "refresh_sessions", lambda maintenance=True: [])
    monkeypatch.setattr(app_module, "available_agent_commands", lambda: ["codex"])
    try:
        payload, status = webapp.create_next_session("codex", dangerously_yolo=True)
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.FORBIDDEN
    assert "--dangerously-yolo" in payload["error"]


def test_create_next_session_rejects_an_implicit_terminal(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(webapp, "refresh_sessions", lambda maintenance=True: [])
    monkeypatch.setattr(app_module, "available_agent_commands", lambda: ["term"])
    try:
        payload, status = webapp.create_next_session("term")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.BAD_REQUEST
    assert payload["error"] == "choose an explicit terminal command"

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
        def __init__(self, target, args=(), name=None, daemon=None):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon

        def start(self):
            started.append(self.name)

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(app_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(webapp, "server_attention_ack_event_poll_seconds", lambda: 12.0)
    monkeypatch.setattr(webapp, "server_tmux_signal_event_poll_seconds", lambda: 15.0)
    monkeypatch.setattr(webapp, "start_tmux_signal_event_watcher", lambda: True)
    try:
        webapp.start_client_event_watcher()
        assert started == ["client-event-watch", "watchd-revision"]
        record = webapp.client_watch_service.event_watcher_record
        assert record.next_attention_ack_poll_at == pytest.approx(112.0)
        assert record.next_tmux_signal_poll_at == pytest.approx(115.0)
    finally:
        webapp.stop_client_event_watcher()
        webapp.control_server.stop()


def test_client_event_watcher_restart_does_not_reuse_or_clobber_old_generation(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(webapp, "start_tmux_signal_event_watcher", lambda: True)
    monkeypatch.setattr(webapp, "stop_tmux_signal_event_watcher", lambda: None)
    monkeypatch.setattr(webapp, "start_watchd_revision_watcher", lambda record: False)
    try:
        webapp.start_client_event_watcher()
        old_record = webapp.client_watch_service.event_watcher_record
        webapp.stop_client_event_watcher()
        assert old_record.stop_event.is_set()
        assert webapp.client_watch_service.event_watcher_record is not old_record

        webapp.start_client_event_watcher()
        replacement = webapp.client_watch_service.event_watcher_record
        assert replacement is not old_record
        assert replacement.stop_event is not old_record.stop_event
        assert replacement.wake_event is not old_record.wake_event
        stale_revision = {
            "epoch": "old",
            "revision": 1,
            "watch_generation": 1,
            "token": "old:1",
            "roots": ["/old"],
            "root_generations": {"/old": 1},
        }
        assert webapp.apply_watchd_revision(old_record, stale_revision) == []
        assert webapp.client_watch_service.event_watcher_record is replacement
        assert replacement.watchd_revision == 0
    finally:
        webapp.stop_client_event_watcher()
        webapp.control_server.stop()


def test_client_event_watcher_parallel_lifecycle_attributes_are_retired():
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    for name in (
        "client_watch_thread",
        "client_watch_running",
        "client_watch_wake_event",
        "client_watch_stop_event",
        "client_directory_poll_running",
        "client_event_next_signature_poll_at",
        "client_event_next_file_poll_at",
        "client_event_next_background_file_poll_at",
        "client_event_next_attention_ack_poll_at",
        "client_event_next_tmux_signal_poll_at",
        "client_event_next_watched_pr_poll_at",
        "client_event_next_yoagent_job_poll_at",
    ):
        assert f"self.{name}" not in source



@pytest.mark.parametrize(
    "method_name",
    (
        "events_payload",
        "search_payload",
        "run_history_payload",
        "session_files_payload",
        "build_auto_approve_status",
    ),
)
def test_session_scoped_endpoints_refresh_before_unknown_session_guard(monkeypatch, method_name):
    webapp = app_module.TmuxWebtermApp(["old"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["new"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    monkeypatch.setattr(webapp, "auto_approve_session_status", lambda session, **_kwargs: {"target": session})
    if method_name == "session_files_payload":
        monkeypatch.setattr(
            webapp,
            "session_files_payload_for_infos",
            lambda session, *_args, **_kwargs: ({"session": session}, HTTPStatus.OK),
        )
    try:
        if method_name == "search_payload":
            payload, status = webapp.search_payload("", session="new")
        else:
            payload, status = getattr(webapp, method_name)("new")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["session" if method_name != "build_auto_approve_status" else "target"] == "new"


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
        payload, status = webapp.build_auto_approve_status()
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
        payload, status = webapp.build_auto_approve_status()
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


def test_cached_agent_window_git_inventory_skips_spawn_on_unchanged_watcher_generation(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["mock"])
    try:
        calls: list[str] = []

        def fake_inventory(root):
            calls.append(root)
            return {"root": root, "branch": "main"}

        monkeypatch.setattr(app_module, "git_inventory", fake_inventory)
        monkeypatch.setattr(webapp, "watcher_covers_repo", lambda repo: True)
        generation = {"value": 5}
        monkeypatch.setattr(webapp, "repo_dirty_generation", lambda root: generation["value"])

        first = webapp.cached_agent_window_git_inventory("/repo")
        second = webapp.cached_agent_window_git_inventory("/repo")
        assert first == second == {"root": "/repo", "branch": "main"}
        # A warm refresh over an unchanged dirty generation reuses the cached inventory (no git spawn).
        assert calls == ["/repo"]

        generation["value"] = 6
        webapp.cached_agent_window_git_inventory("/repo")
        # A dirty-generation bump re-spawns git_inventory.
        assert calls == ["/repo", "/repo"]

        monkeypatch.setattr(webapp, "watcher_covers_repo", lambda repo: False)
        webapp.cached_agent_window_git_inventory("/repo")
        webapp.cached_agent_window_git_inventory("/repo")
        # An uncovered repo is never cached and always re-spawns, preserving always-fresh behavior.
        assert len(calls) == 4
    finally:
        webapp.control_server.stop()


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
            AgentInfo("mock", "claude", 10, "%claude", "python3 tools/mockers/claude.py --mock", str(tmp_path), None, None, None, "mock no transcript"),
            AgentInfo("mock", "codex", 11, "%codex", "python3 tools/mockers/codex.py --mock", str(tmp_path), None, None, None, "mock no transcript"),
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
    current_path = tmp_path / "repo" / "idle"
    current_path.mkdir(parents=True)
    pane = PaneInfo(
        session="2",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%20",
        target="%20",
        current_path=str(current_path),
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
        agents=[AgentInfo("2", "claude", 20, "%20", "claude", str(current_path), "idle", "claude-id", str(tmp_path / "claude.jsonl"), None)],
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


def test_agent_window_working_completion_gets_a_fresh_pause_timestamp_after_idle_confirmation(monkeypatch, tmp_path):
    current_path = tmp_path / "repo" / "working"
    current_path.mkdir(parents=True)
    pane = PaneInfo(
        session="2",
        window="0",
        window_name="codex",
        pane="0",
        pane_id="%20",
        target="%20",
        current_path=str(current_path),
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
        agents=[AgentInfo("2", "codex", 20, "%20", "codex", str(current_path), "idle", "codex-id", str(tmp_path / "codex.jsonl"), None)],
    )
    states = iter(({"key": "working", "text": "working"}, {"key": "idle", "text": "done"}, {"key": "idle", "text": "done"}, {"key": "idle", "text": "done"}))
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda _target, **_kwargs: "fixture")
    monkeypatch.setattr(app_module, "agent_screen_state", lambda _text, **_kwargs: next(states))
    webapp = app_module.TmuxWebtermApp(["2"])
    try:
        now = [100.0]
        monkeypatch.setattr(app_module.time, "time", lambda: now[0])
        working = webapp.agent_window_status_payloads("2", info=info, discovered_sessions={"2": info})[0]
        now[0] = 200.0
        pending = webapp.agent_window_status_payloads("2", info=info, discovered_sessions={"2": info})[0]
        now[0] = 205.0
        completed = webapp.agent_window_status_payloads("2", info=info, discovered_sessions={"2": info})[0]
        still_completed = webapp.agent_window_status_payloads("2", info=info, discovered_sessions={"2": info})[0]
    finally:
        webapp.control_server.stop()
    assert working["state"] == "working"
    assert pending["state"] == "idle"
    assert pending["working_stopped_ts"] is None
    assert completed["state"] == "idle"
    assert completed["working_stopped_ts"] == 200.0
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

    class FakeApprovalWorkerHandle:
        def __init__(self, target):
            self.target = target
            self.stopped = False
            created_targets.append(target)

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

    class FakeApprovalClient:
        def __init__(self):
            self.statuses = {}

        def start_worker(self, *, session, target, owner_extra, dangerously_yolo):
            handle = FakeApprovalWorkerHandle(target)
            status = {**handle.status(), "session": session}
            self.statuses[target] = status
            return handle, status

        def status_session(self, session):
            return [status for status in self.statuses.values() if status.get("session") == session and status.get("enabled")]

        def stop_session(self, session):
            for target, status in list(self.statuses.items()):
                if status.get("session") == session:
                    self.statuses.pop(target, None)
            return {"ok": True, "session": session}

    signal_payload = {
        "ok": True,
        "agents": [
            {"session": "6", "target": "%11", "pane_id": "%11", "agent": "codex", "dead": False},
            {"session": "6", "target": "%12", "pane_id": "%12", "agent": "claude", "dead": False},
            {"session": "7", "target": "%21", "pane_id": "%21", "agent": "codex", "dead": False},
        ],
        "windows": [],
    }
    monkeypatch.setattr(app_module, "tmux_has_exact_session", lambda session: session == "6")
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["6"])
    approval_client = FakeApprovalClient()
    webapp.approval_client = approval_client
    monkeypatch.setattr(webapp, "tmux_signal_snapshot", lambda force=False: signal_payload)
    monkeypatch.setattr(webapp, "prompt_and_screen_status", lambda *args, **kwargs: (app_module.normalized_prompt_state(), {"key": "idle", "text": ""}))
    try:
        payload, status = webapp.set_auto_approve("6", True, persist=False)
        record_sessions = {target: item["session"] for target, item in approval_client.statuses.items()}
        released = webapp.disable_auto_approve_for_takeover("6", {"pid": 123})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert created_targets == ["%11", "%12"]
    assert record_sessions == {"%11": "6", "%12": "6"}
    assert payload["target"] == "6"
    assert payload["worker_targets"] == ["%11", "%12"]
    assert payload["approved"] == 3
    assert payload["enabled"] is True
    assert released["ok"] is True
    assert approval_client.statuses == {}


def test_auto_approve_persistence_uses_approvald_as_single_worker_owner(monkeypatch):
    class FakeApprovalClient:
        def service_status(self):
            return {
                "targets": [
                    {"session": "6", "target": "%11", "enabled": True},
                    {"session": "7", "target": "%21", "enabled": False},
                ]
            }

    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.sessions = ["6", "7"]
    webapp.approval_client = FakeApprovalClient()
    persisted = []
    monkeypatch.setattr(app_module, "read_yolomux_state", lambda: {"auto_approve_enabled": ["6"]})
    monkeypatch.setattr(app_module, "update_yolomux_state", lambda payload: persisted.append(payload))
    webapp.auto_approve_session_lock_owner = lambda session: pytest.fail(f"local session {session} was misclassified as external")

    webapp.persist_auto_sessions()
    assert persisted == [{"auto_approve_enabled": ["6"]}]


def test_auto_approve_worker_parallel_maps_are_retired():
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert "self.auto_workers:" not in source
    assert "self.auto_workers =" not in source
    assert "self.auto_workers." not in source
    assert "auto_worker_records" not in source
    assert "auto_workers_lock" not in source
    assert "AutoApproveWorkerRecord" not in source
    assert "auto_worker_sessions" not in source
    assert "auto_worker_session_map" not in source


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


def test_activity_summary_payload_prioritizes_tmux_recent_sessions(monkeypatch, legacy_activity_summary_enabled):
    infos = {
        name: SessionInfo(session=name, panes=[], selected_pane=None, agents=[])
        for name in ("1", "2", "3")
    }
    calls = []
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: (infos, []))
    monkeypatch.setattr(app_module, "session_work_graph", lambda info, cache, allow_network=False: metadata.empty_work_graph())

    def fake_build_summary(info, work, files, locale="en", **_kwargs):
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
    webapp.cached_session_files_payload_for_info = lambda info, hours=24.0, wait_for_fresh=True: {"files": [], "repos": [], "errors": []}
    webapp.tmux_signal_snapshot = lambda force=False: {
        "sessions": {
            "1": {"activity_ts": 10, "last_attached_ts": 0},
            "2": {"activity_ts": 100, "last_attached_ts": 0},
            "3": {"activity_ts": 0, "last_attached_ts": 0},
        },
        "windows": [{"session": "3", "activity_ts": 200}],
    }
    try:
        payload = webapp.assemble_activity_summary_payload()
    finally:
        webapp.control_server.stop()

    assert calls[-3:] == [("3", "en"), ("2", "en"), ("1", "en")]
    assert payload["session_order"] == ["3", "2", "1"]


def test_activity_summary_payload_single_flights_equivalent_concurrent_requests(monkeypatch, legacy_activity_summary_enabled):
    caller_count = 8
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    discovered_count = 0
    discovered_lock = threading.Lock()
    all_discovered = threading.Event()

    def discover(_sessions):
        nonlocal discovered_count
        with discovered_lock:
            discovered_count += 1
            if discovered_count == caller_count:
                all_discovered.set()
        return {"1": info}, []

    graph_calls = 0
    graph_lock = threading.Lock()
    graph_started = threading.Event()
    release_graph = threading.Event()

    def build_graph(_info, _cache, allow_network=False):
        nonlocal graph_calls
        assert allow_network is False
        with graph_lock:
            graph_calls += 1
            graph_started.set()
        assert release_graph.wait(5)
        return metadata.empty_work_graph()

    monkeypatch.setattr(app_module, "discover_sessions", discover)
    monkeypatch.setattr(app_module, "session_work_graph", build_graph)
    monkeypatch.setattr(
        app_module,
        "build_session_activity_summary",
        lambda session_info, work, files, locale="en", **_kwargs: {
            "session": session_info.session,
            "agent": "",
            "active": False,
            "repos": [],
            "files": {"count": 0, "added": 0, "removed": 0},
            "lines": [],
        },
    )
    webapp = app_module.TmuxWebtermApp(["1"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    webapp.cached_session_files_payload_for_info = lambda session_info, hours=24.0, wait_for_fresh=True: {
        "files": [],
        "repos": [],
        "errors": [],
    }
    webapp.tmux_recency_ordered_sessions = lambda session_names=None, payload=None: ["1"]
    try:
        with ThreadPoolExecutor(max_workers=caller_count) as executor:
            futures = [executor.submit(webapp.assemble_activity_summary_payload) for _ in range(caller_count)]
            assert all_discovered.wait(5)
            assert graph_started.wait(5)
            release_graph.set()
            payloads = [future.result(timeout=5) for future in futures]
    finally:
        release_graph.set()
        webapp.control_server.stop()

    assert graph_calls == 1
    assert all(payload == payloads[0] for payload in payloads)


def test_activity_summary_payload_reuses_transcripts_work_graph_without_duplicate_warm(monkeypatch, legacy_activity_summary_enabled):
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    cached_graph = {**metadata.empty_work_graph(), "generation": 17}
    observed_graphs = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda _sessions: ({"1": info}, []))
    monkeypatch.setattr(
        app_module,
        "session_work_graph",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("activity summary rebuilt a cached work graph")),
    )
    monkeypatch.setattr(
        app_module,
        "activity_work_summary_from_graph",
        lambda graph: observed_graphs.append(graph) or {},
    )
    monkeypatch.setattr(
        app_module,
        "build_session_activity_summary",
        lambda session_info, work, files, locale="en", **_kwargs: {
            "session": session_info.session,
            "agent": "",
            "active": False,
            "repos": [],
            "files": {"count": 0, "added": 0, "removed": 0},
            "lines": [],
        },
    )
    webapp = app_module.TmuxWebtermApp(["1"])
    webapp.set_transcripts_payload_cache({"sessions": {"1": {"work_graph": cached_graph}}})
    webapp.warm_metadata_cache_async = lambda _sessions: (_ for _ in ()).throw(
        AssertionError("activity summary spawned a duplicate metadata warm")
    )
    webapp.cached_session_files_payload_for_info = lambda session_info, hours=24.0, wait_for_fresh=True: {
        "files": [],
        "repos": [],
        "errors": [],
    }
    webapp.tmux_recency_ordered_sessions = lambda session_names=None, payload=None: ["1"]
    try:
        payload = webapp.assemble_activity_summary_payload(force=True)
    finally:
        webapp.control_server.stop()

    assert payload["session_order"] == ["1"]
    assert observed_graphs == [cached_graph]


def test_activity_summary_payload_all_scope_includes_visible_tmux_sessions(monkeypatch, legacy_activity_summary_enabled):
    infos = {
        name: SessionInfo(session=name, panes=[], selected_pane=None, agents=[])
        for name in ("1", "external")
    }
    discovered = []
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1", "external"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: discovered.append(list(sessions)) or ({name: infos[name] for name in sessions if name in infos}, []))
    monkeypatch.setattr(app_module, "session_work_graph", lambda info, cache, allow_network=False: metadata.empty_work_graph())
    monkeypatch.setattr(app_module, "build_session_activity_summary", lambda info, work, files, locale="en", **_kwargs: {"session": info.session, "agent": "", "active": False, "repos": [], "files": {"count": 0, "added": 0, "removed": 0}, "lines": []})
    webapp = app_module.TmuxWebtermApp(["1"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    summary_hours = []

    def fake_cached_session_files_payload_for_info(info, hours=24.0, wait_for_fresh=True):
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
        configured = webapp.assemble_activity_summary_payload()
        all_sessions = webapp.assemble_activity_summary_payload(session_scope="all", hours=336)
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


def test_activity_summary_payload_batches_recent_events_for_multiple_sessions(monkeypatch, legacy_activity_summary_enabled):
    infos = {
        name: SessionInfo(session=name, panes=[], selected_pane=None, agents=[])
        for name in ("1", "2", "3")
    }
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({name: infos[name] for name in sessions if name in infos}, []))
    monkeypatch.setattr(app_module, "session_work_graph", lambda info, cache, allow_network=False: metadata.empty_work_graph())
    monkeypatch.setattr(app_module, "build_session_activity_summary", lambda info, work, files, locale="en", **_kwargs: {"session": info.session, "agent": "", "active": False, "repos": [], "files": {"count": 0, "added": 0, "removed": 0}, "lines": []})
    webapp = app_module.TmuxWebtermApp(["1", "2", "3"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    webapp.cached_session_files_payload_for_info = lambda info, hours=24.0, wait_for_fresh=True: {"files": [], "repos": [], "errors": []}
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
        payload = webapp.assemble_activity_summary_payload()
    finally:
        webapp.control_server.stop()

    assert tail_many_calls == [(("3", "2", "1"), 5)]
    assert payload["session_order"] == ["3", "2", "1"]
    assert payload["session_info"]["3"]["recent_events"][0]["message"] == "3 recent"
    assert payload["session_info"]["2"]["recent_events"][0]["message"] == "2 recent"
    assert payload["session_info"]["1"]["recent_events"][0]["message"] == "1 recent"


def test_activity_summary_payload_forwards_exact_statusd_body_without_web_work_graph(monkeypatch, legacy_activity_summary_enabled):
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": info}, []))
    monkeypatch.setattr(
        app_module,
        "session_work_graph",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("activity summary must not build work graphs in the web process")
        ),
    )
    webapp = app_module.TmuxWebtermApp(["1"])
    expected = {
        "generated_at": "2026-08-04T00:00:00+00:00",
        "generated_ts": 1785801600.0,
        "session_order": ["1"],
        "sessions": {"1": {"session": "1", "local": "ready"}},
        "session_info": {"1": {"session": "1", "recent_events": [{"session": "1", "message": "ready"}]}},
        "agents": [],
        "global": {"total_agents": 1, "lines": ["ready"]},
        "capabilities": {},
        "errors": [],
        "locale": "en",
        "session_scope": "configured",
        "session_file_hours": 24.0,
        "yoagent_summaries": {"mode": "first_launch", "first_launch_started": False, "running": False, "updated_ts": 0.0, "updated_at": ""},
    }
    calls = []
    cached_graph = metadata.empty_work_graph()
    expected_work = metadata.activity_work_summary_from_graph(cached_graph)

    class ActivityStatusClient:
        def activity_summary(self, sessions, **kwargs):
            calls.append((list(sessions), kwargs))
            return {
                "ok": True,
                "protocol_version": statusd_protocol.STATUSD_PROTOCOL_VERSION,
                "status": 200,
                "built_at": 1785801600.0,
            }, json.dumps(expected, separators=(",", ":")).encode("utf-8")

    webapp.status_client = ActivityStatusClient()
    webapp.set_transcripts_payload_cache({"sessions": {"1": {"work_graph": cached_graph}}})
    webapp.cached_transcripts_work_graph = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("web activity forwarder deep-copied a full cached work graph")
    )
    try:
        payload = webapp.activity_summary_payload()
    finally:
        webapp.control_server.stop()

    assert calls == [(["1"], {"force": False, "locale": "en", "session_scope": "configured", "hours": 24.0, "work_by_session": {"1": expected_work}})]
    assert payload == expected


def test_activity_summary_cached_work_projection_matches_daemon_local_graph_assembly(monkeypatch, legacy_activity_summary_enabled):
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    graph = metadata.empty_work_graph()
    projected_work = metadata.activity_work_summary_from_graph(graph)
    monkeypatch.setattr(app_module, "discover_sessions", lambda _sessions: ({"1": info}, []))
    monkeypatch.setattr(app_module, "session_work_graph", lambda *_args, **_kwargs: graph)
    monkeypatch.setattr(
        app_module,
        "build_session_activity_summary",
        lambda session_info, work, files, locale="en", **_kwargs: {
            "session": session_info.session,
            "agent": "",
            "active": False,
            "repos": work.get("repos", []),
            "files": {"count": 0, "added": 0, "removed": 0},
            "lines": [],
        },
    )

    session_files_wait_flags = []

    def configured_app():
        app = app_module.TmuxWebtermApp(["1"], status_service_mode=True)
        app.cached_session_files_payload_for_info = lambda _info, hours=24.0, wait_for_fresh=True: session_files_wait_flags.append(wait_for_fresh) or {"files": [], "repos": [], "errors": []}
        app.tmux_recency_ordered_sessions = lambda session_names=None, payload=None: ["1"]
        app.tabber_activity_agents_snapshot = lambda force=False: []
        app.event_log.tail_many = lambda sessions, limit=5: {}
        return app

    graph_app = configured_app()
    projected_app = configured_app()
    graph_payload = graph_app.assemble_activity_summary_payload()
    projected_payload = projected_app.assemble_activity_summary_payload(work_by_session={"1": projected_work})
    for payload in (graph_payload, projected_payload):
        payload.pop("generated_at")
        payload.pop("generated_ts")

    assert projected_payload == graph_payload
    assert session_files_wait_flags == [False, False]


def test_cached_activity_work_projection_omits_an_entry_over_the_rpc_budget(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"], status_service_mode=True)
    webapp.set_transcripts_payload_cache({"sessions": {"1": {"work_graph": metadata.empty_work_graph()}}})
    monkeypatch.setattr(
        app_module,
        "activity_work_summary_from_graph",
        lambda _graph: {"git": {"blob": "x" * app_module.STATUSD_ACTIVITY_MAX_WORK_BYTES}},
    )

    assert webapp.cached_activity_work_by_session() == {}


def test_activity_summary_cold_session_files_schedules_refresh_without_waiting(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"], status_service_mode=True)
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    refreshes = []
    monkeypatch.setattr(webapp, "get_session_files_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        webapp,
        "start_session_files_cache_refresh",
        lambda cache_key, target, *args: refreshes.append((cache_key, target, args)) or True,
    )

    payload = webapp.cached_session_files_payload_for_info(info, wait_for_fresh=False)

    assert payload == {
        "session": "1",
        "hours": 24.0,
        "files": [],
        "repos": [],
        "errors": [],
        "refreshing_elsewhere": True,
    }
    assert len(refreshes) == 1
    assert refreshes[0][1] == webapp.refresh_session_files_info_cache


def test_activity_summary_bytes_returns_typed_terminal_statusd_failure(monkeypatch, legacy_activity_summary_enabled):
    webapp = app_module.TmuxWebtermApp(["1"], status_service_mode=True)
    monkeypatch.setattr(
        webapp.status_client,
        "activity_summary",
        lambda *_args, **_kwargs: ({"ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "unavailable"}, b""),
    )

    body, status = webapp.activity_summary_bytes()

    assert status == HTTPStatus.FAILED_DEPENDENCY
    assert json.loads(body) == {
        "status": "unavailable",
        "error": "unavailable",
        "terminal": True,
        "upstream": {"ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "unavailable"},
    }


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
    _install_fake_tabber_activity_jobd(monkeypatch, webapp)
    try:
        activity = webapp.build_activity_payload()
        updated = []

        def fake_update_summary(session, info, settings=None, force=False):
            updated.append(session)
            return {"session": session, "updated": False, "reason": "test"}

        webapp.yoagent_controller.update_yoagent_session_summary = fake_update_summary
        tick = webapp.yoagent_controller.tick_yoagent_session_summaries({"backend": "codex", "invocation": "cli"})
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
    _install_fake_tabber_activity_jobd(monkeypatch, webapp)
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


def test_tabber_activity_rebuilds_only_changed_session_rows_and_removes_deleted_sessions(monkeypatch):
    infos = {
        session: SessionInfo(
            session=session,
            panes=[],
            selected_pane=None,
            agents=[AgentInfo(session, "codex", 100 + int(session), f"%{session}", "codex", "/repo", "running", f"sid-{session}", None, None)],
        )
        for session in ("1", "2")
    }
    current_infos = dict(infos)
    screens = {"%1": {"key": "idle", "text": ""}, "%2": {"key": "idle", "text": ""}}

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({session: current_infos[session] for session in sessions if session in current_infos}, []))
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    webapp.tmux_recency_ordered_sessions = lambda session_names=None, payload=None: [session for session in ("2", "1") if session in (session_names or [])]
    webapp.cached_session_files_payloads_for_infos = lambda agent_infos, hours=24.0: {session: {"files": [], "repos": [], "errors": []} for session in agent_infos}
    webapp.activity_snapshot_with_recency = lambda snapshot=None: {"1": {"last_user_input_ts": 10}, "2": {"last_user_input_ts": 20}}
    webapp.agent_window_screen_state = lambda agent, preclassified_by_target=None: dict(screens[agent.pane_target])
    webapp.status_snapshot_payload = lambda: None
    webapp.merge_shared_attention_acks = lambda: False
    # `compute_tabber_activity_rows_via_jobd` submits one batch per `build_activity_payload()` call
    # containing every session whose signature changed since the last call, so this replaces the
    # old per-session `row_builds`/`recent_builds` tracking (now internal to the jobd task).
    submitted_session_batches = _install_fake_tabber_activity_jobd(monkeypatch, webapp)
    try:
        first = webapp.build_activity_payload()
        second = webapp.build_activity_payload()
        screens["%2"] = {"key": "working", "text": "Working"}
        changed = webapp.build_activity_payload()
        current_infos.pop("2")
        deleted = webapp.build_activity_payload()
        with webapp.client_watch_service.lock:
            webapp.client_watch_service.attention_ack_rev += 1
        acknowledged = webapp.build_activity_payload()
    finally:
        webapp.control_server.stop()

    assert [row["session"] for row in first["agents"]] == ["2", "1"]
    assert second == first
    assert changed["agent_windows"]["2"][0]["state"] == "working"
    assert "2" not in deleted["agent_windows"]
    assert "2" not in webapp.activity_transcript_service.tabber_cache_record.session_rows
    assert acknowledged["agent_windows"]["1"][0]["state"] == "idle"
    assert submitted_session_batches == [["1", "2"], ["2"], ["1"]]


def test_tabber_activity_rebuild_signature_reacts_to_owned_roster_row_change_alone(monkeypatch):
    # A roster-only change (e.g. a cooldown timer statusd advances) with no new tmux screen
    # capture must still bust the per-session reuse signature -- otherwise a REUSED session would
    # keep serving a stale owned row (state/attention/cooldown) until something else changed.
    session = "1"
    pane = PaneInfo(session, "0", "0", "%1", f"{session}:0.0", "/repo", "codex", True, True, "codex", 101)
    info = SessionInfo(
        session=session, panes=[pane], selected_pane=pane,
        agents=[AgentInfo(session, "codex", 101, "%1", "codex", "/repo", "running", "sid-1", None, None)],
    )
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({session: info}, []))
    webapp = app_module.TmuxWebtermApp([session])
    webapp.tmux_recency_ordered_sessions = lambda session_names=None, payload=None: [session]
    webapp.cached_session_files_payloads_for_infos = lambda agent_infos, hours=24.0: {name: {"files": [], "repos": [], "errors": []} for name in agent_infos}
    webapp.activity_snapshot_with_recency = lambda snapshot=None: {}
    webapp.agent_window_screen_state = lambda agent, preclassified_by_target=None: {"key": "idle", "text": ""}
    webapp.merge_shared_attention_acks = lambda: False
    roster_stopped_ts = {"value": 100.0}

    def fake_status_snapshot_payload():
        return {
            "agent_window_snapshot_revision": 1,
                "sessions": {session: {"agent_windows": [{"window_index": 0, "pane_target": "%1", "kind": "codex", "state": "idle", "working_stopped_ts": roster_stopped_ts["value"]}]}},
        }

    webapp.status_snapshot_payload = fake_status_snapshot_payload
    submitted = _install_fake_tabber_activity_jobd(monkeypatch, webapp)
    try:
        first = webapp.build_activity_payload()
        second = webapp.build_activity_payload()
        roster_stopped_ts["value"] = 200.0
        changed = webapp.build_activity_payload()
    finally:
        webapp.control_server.stop()

    assert first["agent_windows"][session][0]["working_stopped_ts"] == 100.0
    assert changed["agent_windows"][session][0]["working_stopped_ts"] == 200.0
    assert submitted == [[session], [session]]


def test_session_files_and_tabber_refreshes_are_per_target_single_flight(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")
    webapp = app_module.TmuxWebtermApp([])
    session_compute_started = threading.Event()
    release_session_compute = threading.Event()
    session_compute_calls = []

    def compute_session_files():
        session_compute_calls.append(True)
        session_compute_started.set()
        assert release_session_compute.wait(timeout=5)
        return {"files": [{"path": "shared.py"}], "repos": [], "errors": []}, HTTPStatus.OK

    try:
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(webapp.compute_session_files_cache_entry, ("same-target",), compute_session_files) for _index in range(6)]
            assert session_compute_started.wait(timeout=5)
            release_session_compute.set()
            results = [future.result(timeout=5) for future in futures]
        assert len(session_compute_calls) == 1
        assert all(result[0]["files"] == [{"path": "shared.py"}] for result in results)

        webapp.compute_session_files_cache_entry(
            ("changed-target",),
            lambda: ({"files": [{"path": "changed.py"}], "repos": [], "errors": []}, HTTPStatus.OK),
        )
        assert len(webapp.session_files_service.work_records) == 0

        source_signature = ["same-signature"]
        monkeypatch.setattr(webapp, "tabber_activity_source_signature", lambda: source_signature[0])
        tabber_started = threading.Event()
        release_tabber = threading.Event()
        tabber_calls = []

        def same_target_owner(hours, signature):
            tabber_calls.append((hours, signature))
            tabber_started.set()
            assert release_tabber.wait(timeout=5)
            return {"session_file_hours": hours, "signature": signature, "agents": []}

        monkeypatch.setattr(webapp, "refresh_tabber_activity_cache_owner", same_target_owner)
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(webapp.refresh_tabber_activity_cache, 24.0) for _index in range(5)]
            assert tabber_started.wait(timeout=5)
            release_tabber.set()
            payloads = [future.result(timeout=5) for future in futures]
        assert tabber_calls == [(24.0, "same-signature")]
        assert all(payload == payloads[0] for payload in payloads)

        barrier = threading.Barrier(2)
        tabber_calls.clear()

        def different_target_owner(hours, signature):
            tabber_calls.append((hours, signature))
            barrier.wait(timeout=5)
            return {"session_file_hours": hours, "signature": signature, "agents": []}

        monkeypatch.setattr(webapp, "refresh_tabber_activity_cache_owner", different_target_owner)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(webapp.refresh_tabber_activity_cache, (0.5, 24.0)))
        assert sorted(tabber_calls) == [(0.5, "same-signature"), (24.0, "same-signature")]
        assert sorted(payload["session_file_hours"] for payload in results) == [0.5, 24.0]
    finally:
        webapp.control_server.stop()


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


def test_tabber_jobd_request_projects_large_session_files_to_bounded_recent_paths():
    session = "5"
    target = "5:0.0"
    pane = PaneInfo(session=session, window="0", pane="0", pane_id="%50", target=target, current_path="/repo", command="codex", active=True, window_active=True, title="", pid=50, process_label="codex")
    agent = AgentInfo(session, "codex", 50, target, "codex", "/repo", "running", "codex-sid", None, None)
    info = SessionInfo(session=session, panes=[pane], selected_pane=pane, agents=[agent])
    files_payload = {
        "files": [
            {
                "repo": f"/repo/{index % 4}",
                "abs_path": f"/repo/{index % 4}/file-{index}.py",
                "mtime": float(index),
                "status": "M",
                "ignored_large_field": "x" * 512,
                "agent_windows": [{"kind": "codex", "window": "0", "window_index": 0, "pane": "0", "pane_target": target}],
            }
            for index in range(700)
        ],
        "repos": [],
    }
    expected = activity_summary.build_recent_agents_payload(
        {session: info},
        [session],
        session_files_by_session={session: files_payload},
    )[0]["recent_paths"]
    captured = {}

    class CaptureJobClient:
        def submit(self, task, payload, **kwargs):
            captured.update({"task": task, "payload": payload, "kwargs": kwargs})
            return {"ok": False, "error": "captured"}

    webapp = app_module.TmuxWebtermApp([session])
    webapp.job_client = CaptureJobClient()
    try:
        with pytest.raises(app_module.TabberActivityJobdUnavailable, match="captured"):
            webapp.compute_tabber_activity_rows_via_jobd(
                {session: info},
                discovered_sessions={session: info},
                session_files_by_session={session: files_payload},
                activity_snapshot={},
                preclassified_by_session={session: {target: {"key": "idle", "text": ""}}},
                owned_agent_rows={},
                snapshot_revision=7,
                scope="configured",
                bounded_hours=24.0,
                source_signature="large-files",
            )
    finally:
        webapp.control_server.stop()

    session_input = captured["payload"]["sessions"][session]
    request_payload = {
        "action": "submit",
        "task": captured["task"],
        "payload": captured["payload"],
        "priority": captured["kwargs"]["priority"],
        "generation": captured["kwargs"]["generation"],
        "coalesce_key": captured["kwargs"]["coalesce_key"],
        "deadline_ms": captured["kwargs"]["deadline_ms"],
    }
    encoded = encode_metadata(new_envelope("jobd", "submit", request_payload, timeout_seconds=0.5))
    result = activity_summary.tabber_activity_view_result(captured["payload"], max_bytes=512 * 1024)

    assert "files_payload" not in session_input
    assert len(encoded) <= LOCAL_RPC_MAX_METADATA_BYTES
    assert result["session_rows"][session]["agents"][0]["recent_paths"] == expected


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
    monkeypatch.setattr(app_module, "indexed_repo_summaries", lambda cache=None, allow_network=False, repo_roots=None: indexed)
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
    refreshes = []
    monkeypatch.setattr(webapp, "refresh_sessions", lambda *args, **kwargs: [])
    monkeypatch.setattr(webapp, "warm_metadata_cache_async", lambda sessions: None)
    monkeypatch.setattr(webapp, "start_transcripts_payload_refresh", lambda publish=False, defer=False, not_before=None: refreshes.append((publish, defer)) or True)
    try:
        webapp.set_transcripts_payload_cache(webapp.build_session_metadata_payload())
        first = webapp.transcripts_payload()
        with webapp.activity_transcript_service.transcripts_payload_cache_lock:
            webapp.activity_transcript_service.transcripts_payload_cache_record.stored_at -= app_module.TRANSCRIPTS_PAYLOAD_CACHE_SECONDS + 1.0
        second = webapp.transcripts_payload()
        webapp.refresh_transcripts_payload_cache()
        third = webapp.transcripts_payload()
    finally:
        webapp.control_server.stop()

    assert first["sessions"]["5"]["call"] == 1
    assert second["sessions"]["5"]["call"] == 1
    assert second["cache"]["hit"] is True
    assert second["cache"]["stale"] is True
    assert third["sessions"]["5"]["call"] == 2
    assert calls == [1, 2]
    assert refreshes == [(False, False)]


def test_forced_metadata_refresh_runs_a_build_that_starts_after_the_request(monkeypatch):
    """A forced metadata read must be answered by a build that can see the state it asks about.

    Regression: `force=1` is served from the payload cache, so its bytes always predate the request.
    The refresh it started was then coalesced onto whatever build was already running -- including
    one that began before the session being asked about existed -- and nothing re-ran. The browser
    kept rendering pre-create metadata with no generation to wait on, so a 15s watchdog was the only
    thing that ever noticed. The response must name the generation that WILL observe this instant,
    and that generation must actually be built and published.
    """

    webapp = app_module.TmuxWebtermApp([])
    entered = threading.Event()
    release = threading.Event()
    builds: list[float] = []
    published: list[tuple[str, int]] = []
    monkeypatch.setattr(
        webapp,
        "publish_client_event",
        lambda name, payload, **kwargs: published.append(
            (name, int((payload.get("data") or {}).get("metadata_generation") or 0))
        ),
    )

    def blocking_build(lightweight: bool = False) -> dict[str, object]:
        builds.append(time.monotonic())
        entered.set()
        if len(builds) == 1:
            assert release.wait(timeout=10)
        return {"sessions": {}, "session_order": [], "build": len(builds)}

    monkeypatch.setattr(webapp, "build_transcripts_payload", blocking_build)
    try:
        webapp.set_transcripts_payload_cache({"sessions": {}, "session_order": [], "build": 0})
        first = threading.Thread(target=lambda: webapp.session_metadata_payload(force=True), daemon=True)
        first.start()
        assert entered.wait(timeout=5), "the first forced refresh never started a build"

        second_requested_at = time.monotonic()
        second = webapp.session_metadata_payload(force=True)
        assert builds[0] < second_requested_at, "the probe did not model a build that predates the request"

        release.set()
        first.join(timeout=10)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(builds) < 2:
            time.sleep(0.02)

        assert len(builds) == 2, "the forced refresh adopted the older in-flight build instead of re-running"
        settled = webapp.session_metadata_payload()
        assert settled["build"] == 2
        pending = int(second["cache"]["pending_generation"])
        assert pending > int(second["cache"]["generation"]), second["cache"]
        assert int(settled["metadata_generation"]) >= pending, settled["cache"]
        assert ("transcripts_changed", pending) in published, published
    finally:
        release.set()
        webapp.background_owner.stop()
        webapp.control_server.stop()


def test_forced_metadata_refresh_reuses_a_build_that_already_started_after_the_request(monkeypatch):
    """Forward coalescing only. A build that began after the request already answers it, so the
    forced read must name that generation rather than queue a second identical rebuild."""

    webapp = app_module.TmuxWebtermApp([])
    entered = threading.Event()
    release = threading.Event()
    builds: list[int] = []

    def blocking_build(lightweight: bool = False) -> dict[str, object]:
        builds.append(len(builds) + 1)
        entered.set()
        assert release.wait(timeout=10)
        return {"sessions": {}, "session_order": [], "build": len(builds)}

    monkeypatch.setattr(webapp, "build_transcripts_payload", blocking_build)
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: None)
    try:
        webapp.set_transcripts_payload_cache({"sessions": {}, "session_order": [], "build": 0})
        worker = threading.Thread(target=lambda: webapp.refresh_transcripts_payload_cache(True), daemon=True)
        worker.start()
        assert entered.wait(timeout=5)
        record = webapp.activity_transcript_service.transcripts_payload_cache_record
        in_flight_generation = record.generation
        # Model a build that began after the request rather than racing one into that window: the
        # predicate under test is exactly "did this build start at or after the caller's instant".
        record.worker_started_at = time.monotonic() + 1.0

        payload = webapp.session_metadata_payload(force=True)

        assert record.rebuild_requested is False
        assert int(payload["cache"]["pending_generation"]) == in_flight_generation
        release.set()
        worker.join(timeout=10)
        assert builds == [1]
    finally:
        release.set()
        webapp.background_owner.stop()
        webapp.control_server.stop()


def test_transcripts_payload_worker_guard_supersedes_a_stalled_worker():
    """The single-flight refresh guard must not be pinned forever by a hung build.
    Within the deadline a second worker is refused; past it the stalled worker is
    superseded so refreshes resume, and its late finish is a no-op."""
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.activity_transcript_service = SimpleNamespace(
        transcripts_payload_cache_lock=threading.Lock(),
        transcripts_payload_cache_record=state_services.TranscriptsPayloadCacheRecord(),
    )
    record = webapp.activity_transcript_service.transcripts_payload_cache_record

    stalled = object()
    gen1 = webapp.begin_transcripts_payload_work(stalled)
    assert gen1 > 0
    assert record.worker is stalled
    # A fresh in-flight worker holds the single-flight guard.
    assert webapp.begin_transcripts_payload_work(object()) == 0

    # Simulate the worker stalling past the deadline.
    record.worker_started_at -= app_module.TRANSCRIPTS_PAYLOAD_WORKER_DEADLINE_SECONDS + 1.0
    successor = object()
    gen2 = webapp.begin_transcripts_payload_work(successor)
    assert gen2 > gen1
    assert record.worker is successor

    # The stalled worker's late finish/commit cannot clobber the successor.
    assert webapp.finish_transcripts_payload_work(gen1, stalled) is False
    assert webapp.commit_transcripts_payload_cache({"x": 1}, gen1) is False
    assert record.worker is successor


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
    monkeypatch.setattr(webapp, "start_transcripts_payload_refresh", lambda publish=False, defer=False, not_before=None: refresh_calls.append((publish, defer)) or True)
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


def test_forced_session_metadata_returns_cached_payload_without_superseding_live_refresh(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    old_started = threading.Event()
    release_old = threading.Event()
    returned = threading.Event()
    events = []

    def blocked_build():
        old_started.set()
        assert release_old.wait(timeout=3)
        return {"marker": "old"}

    def read_forced():
        try:
            return webapp.session_metadata_payload(force=True)
        finally:
            returned.set()

    webapp.set_transcripts_payload_cache({"marker": "cached"})
    monkeypatch.setattr(webapp, "build_transcripts_payload", blocked_build)
    monkeypatch.setattr(webapp, "build_session_metadata_payload", blocked_build)
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **kwargs: events.append((event_type, payload, kwargs)))
    try:
        assert webapp.start_transcripts_payload_refresh() is True
        with webapp.activity_transcript_service.transcripts_payload_cache_lock:
            old_worker = webapp.activity_transcript_service.transcripts_payload_cache_record.worker
        assert old_worker is not None
        assert old_started.wait(timeout=2)
        with ThreadPoolExecutor(max_workers=1) as executor:
            forced_future = executor.submit(read_forced)
            assert returned.wait(timeout=0.25)
            forced = forced_future.result(timeout=1)
        with webapp.activity_transcript_service.transcripts_payload_cache_lock:
            queued_follow_up = webapp.activity_transcript_service.transcripts_payload_cache_record.rebuild_requested
        release_old.set()
        old_worker.join(timeout=2)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with webapp.activity_transcript_service.transcripts_payload_cache_lock:
                if webapp.activity_transcript_service.transcripts_payload_cache_record.worker is None:
                    break
            time.sleep(0.02)
        with webapp.activity_transcript_service.transcripts_payload_cache_lock:
            cached = webapp.activity_transcript_service.transcripts_payload_cache_record.payload
            active_worker = webapp.activity_transcript_service.transcripts_payload_cache_record.worker
    finally:
        release_old.set()
        webapp.control_server.stop()

    assert forced["marker"] == "cached"
    assert forced["cache"]["hit"] is True
    assert forced["cache"]["refreshing"] is True
    # The live refresh is not superseded: it still commits its own result. The forced read is
    # coalesced FORWARD onto one queued follow-up instead, because a build that began before the
    # request cannot contain what the request is asking about.
    assert queued_follow_up is True
    assert cached["marker"] == "old"
    assert active_worker is None
    # Two publishes, not one: the live refresh delivers its own result, and the queued follow-up
    # delivers the one the forced read asked for. A single publish here means the forced read was
    # answered by a build that started before it.
    assert [event[0] for event in events] == ["transcripts_changed", "transcripts_changed"]


def test_clear_transcript_caches_invalidates_blocked_refresh(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    old_started = threading.Event()
    release_old = threading.Event()

    def blocked_build():
        old_started.set()
        assert release_old.wait(timeout=3)
        return {"marker": "old"}

    monkeypatch.setattr(webapp, "build_transcripts_payload", blocked_build)
    try:
        assert webapp.start_transcripts_payload_refresh() is True
        with webapp.activity_transcript_service.transcripts_payload_cache_lock:
            old_worker = webapp.activity_transcript_service.transcripts_payload_cache_record.worker
        assert old_worker is not None
        assert old_started.wait(timeout=2)

        webapp.clear_transcript_caches()
        release_old.set()
        old_worker.join(timeout=2)
        with webapp.activity_transcript_service.transcripts_payload_cache_lock:
            record = webapp.activity_transcript_service.transcripts_payload_cache_record
            cached = record.payload
            stored_at = record.stored_at
            active_worker = record.worker
    finally:
        release_old.set()
        webapp.control_server.stop()

    assert cached is None
    assert stored_at is None
    assert active_worker is None


def transcripts_payload_work_state(webapp):
    """The whole single-flight guard as one snapshot, so a partial release is visible."""

    with webapp.activity_transcript_service.transcripts_payload_cache_lock:
        record = webapp.activity_transcript_service.transcripts_payload_cache_record
        return {
            "worker": record.worker,
            "worker_started_at": record.worker_started_at,
            "publish_requested": record.publish_requested,
            "rebuild_requested": record.rebuild_requested,
            "rebuild_publish": record.rebuild_publish,
            "payload": record.payload,
            "stored_at": record.stored_at,
        }


def queued_transcripts_follow_up_app(monkeypatch):
    """An app whose in-flight build has exactly one forced follow-up queued behind it."""

    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.client_events = SimpleNamespace(epoch="epoch-under-test")
    webapp.activity_transcript_service = SimpleNamespace(
        transcripts_payload_cache_lock=threading.RLock(),
        transcripts_payload_cache_record=state_services.TranscriptsPayloadCacheRecord(),
        transcript_tail_cache_lock=threading.Lock(),
        transcript_tail_cache={},
        context_items_cache_lock=threading.Lock(),
        context_items_cache={},
    )
    started: list[bool] = []
    monkeypatch.setattr(
        app_module.TmuxWebtermApp,
        "start_transcripts_payload_refresh",
        lambda self, publish=False, defer=False, not_before=None: bool(started.append(publish)) or True,
    )
    record = webapp.activity_transcript_service.transcripts_payload_cache_record
    worker = object()
    generation = webapp.begin_transcripts_payload_work(worker)
    webapp.commit_transcripts_payload_cache({"sessions": {}}, generation)
    with webapp.activity_transcript_service.transcripts_payload_cache_lock:
        record.publish_requested = True
    # A forced refresh arriving during this build cannot be answered by it, so it queues one
    # follow-up: the exact state the invalidation below has to deal with.
    webapp.begin_transcripts_payload_work(object(), queue_rebuild_after=record.worker_started_at + 1.0, queue_rebuild_publish=True)
    assert (record.rebuild_requested, record.rebuild_publish) == (True, True)
    return webapp, record, worker, generation, started


def test_clear_transcript_caches_releases_the_whole_guard_and_drains_the_queued_follow_up(monkeypatch):
    """Cache invalidation must leave no worker and no queued intent behind.

    Regression: it cleared `worker`, `payload` and `stored_at` only. `worker_started_at`,
    `publish_requested`, `rebuild_requested` and `rebuild_publish` survived, and the invalidated
    worker could not drain them -- its `finish` is a generation mismatch and returns before the
    drain -- so the queued forced rebuild sat on the record until an unrelated later build inherited
    it and published an extra follow-up for a caller that had already been answered.
    """

    webapp, record, worker, generation, started = queued_transcripts_follow_up_app(monkeypatch)

    webapp.clear_transcript_caches()

    state = transcripts_payload_work_state(webapp)
    assert state == {
        "worker": None,
        "worker_started_at": None,
        "publish_requested": False,
        "rebuild_requested": False,
        "rebuild_publish": False,
        "payload": None,
        "stored_at": None,
    }
    # The promise is kept, not dropped: exactly one publishing follow-up build ran.
    assert started == [True]

    # The invalidated worker's late finish is a no-op and cannot start anything more.
    assert webapp.finish_transcripts_payload_work(generation, worker) is False
    assert started == [True]

    # A later unrelated build cannot inherit an intent that belonged to the superseded caller.
    next_worker = object()
    next_generation = webapp.begin_transcripts_payload_work(next_worker, replace=True)
    assert webapp.finish_transcripts_payload_work(next_generation, next_worker) is True
    assert started == [True]


def test_clear_transcript_caches_guard_assertion_fails_when_queued_intent_survives(monkeypatch):
    """Negative control for the test above.

    If invalidation leaves the queued intent alive -- the pre-fix behaviour, reproduced here by
    restoring the fields it used to leave -- the same snapshot assertion and the same
    inherited-follow-up assertion both go red. A green that cannot fail proves nothing.
    """

    webapp, record, worker, generation, started = queued_transcripts_follow_up_app(monkeypatch)
    stale_started_at = record.worker_started_at

    webapp.clear_transcript_caches()
    started.clear()
    # Reintroduce exactly the fields the old implementation left behind.
    with webapp.activity_transcript_service.transcripts_payload_cache_lock:
        record.worker = None
        record.worker_started_at = stale_started_at
        record.publish_requested = True
        record.rebuild_requested = True
        record.rebuild_publish = True

    state = transcripts_payload_work_state(webapp)
    assert state["worker_started_at"] is not None
    assert (state["publish_requested"], state["rebuild_requested"], state["rebuild_publish"]) == (True, True, True)

    # And the stale intent is inherited by the next unrelated build, which is the observable harm.
    next_worker = object()
    next_generation = webapp.begin_transcripts_payload_work(next_worker, replace=True)
    assert webapp.finish_transcripts_payload_work(next_generation, next_worker) is True
    assert started == [True], "an unrelated build inherited the superseded caller's publishing rebuild"


def test_forced_session_metadata_on_a_cold_cache_names_a_build_identity():
    """A forced read must always name the build that will observe it, cache hit or not.

    Regression: the cold branch started a refresh and returned `metadata_generation: 0` with NO
    `pending_generation`. The browser therefore waited for generation 0, which every payload already
    satisfies, so the force resolved instantly against a lightweight payload built before the
    mutation it was sent to confirm.
    """

    webapp = app_module.TmuxWebtermApp([])
    try:
        payload = webapp.session_metadata_payload(force=True)
        cache = payload["cache"]
        assert cache["hit"] is False, cache
        assert cache["refreshing"] is True, cache
        pending = cache["pending_identity"]
        assert pending["epoch"] == webapp.server_epoch
        assert pending["generation"] >= 1, cache
        # The scalar stays only as a projection of the identity object.
        assert cache["pending_generation"] == pending["generation"]
        # And the payload the client is being asked to replace names the same server.
        assert payload["metadata_identity"] == {"epoch": webapp.server_epoch, "generation": 0}
        assert payload["metadata_generation"] == 0
    finally:
        webapp.background_owner.stop()
        webapp.control_server.stop()


def test_unforced_session_metadata_on_a_cold_cache_names_no_build_identity():
    """Negative control: the pending identity is emitted for a FORCED read, not for every read.

    Only a forced refresh publishes its result, so only a forced refresh may hand a client a
    generation to wait on. If this assertion could not fail, the test above would be satisfied by
    stamping a pending identity unconditionally.
    """

    webapp = app_module.TmuxWebtermApp([])
    try:
        cache = webapp.session_metadata_payload(force=False)["cache"]
        assert "pending_identity" not in cache, cache
        assert "pending_generation" not in cache, cache
    finally:
        webapp.background_owner.stop()
        webapp.control_server.stop()


def test_metadata_identity_epoch_is_per_process_and_survives_invalidation():
    """The epoch partitions generations; it never orders them.

    Within one process, invalidating the cache advances the generation and keeps the epoch, so a
    client can compare the two generations. Across processes the epochs differ, and generation 0 of
    a new process is not comparable to generation 50 of the old one at all.
    """

    webapp = app_module.TmuxWebtermApp([])
    other = app_module.TmuxWebtermApp([])
    try:
        webapp.set_transcripts_payload_cache({"sessions": {}, "session_order": []})
        first = copy.deepcopy(webapp.activity_transcript_service.transcripts_payload_cache_record.payload)
        webapp.clear_transcript_caches()
        webapp.set_transcripts_payload_cache({"sessions": {}, "session_order": []})
        second = copy.deepcopy(webapp.activity_transcript_service.transcripts_payload_cache_record.payload)

        assert first["metadata_identity"]["epoch"] == second["metadata_identity"]["epoch"] == webapp.server_epoch
        assert second["metadata_identity"]["generation"] > first["metadata_identity"]["generation"]
        assert other.server_epoch != webapp.server_epoch, "each server process owns its own epoch"
        assert other.metadata_identity(0) == {"epoch": other.server_epoch, "generation": 0}
    finally:
        for instance in (webapp, other):
            instance.background_owner.stop()
            instance.control_server.stop()


def test_transcripts_payload_parallel_cache_state_is_retired():
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert "self.transcripts_payload_cache:" not in source
    assert "self.transcripts_payload_cache =" not in source
    assert "self.transcripts_payload_refreshing" not in source
    assert "self.client_watch_snapshot_running" not in source


def test_transcripts_payload_event_signature_ignores_volatile_fields():
    webapp = app_module.TmuxWebtermApp([])
    graph = metadata.empty_work_graph()
    base = {
        "server_time": "2026-06-24 12:00:00 PDT",
        "server_uptime_seconds": 1.0,
        "session_order": ["5"],
        "sessions": {
            "5": {
                "session": "5",
                "metadata_badge_pulse_remaining_ms": {"pr": 900},
                "work_graph": graph,
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
        changed_graph = metadata.empty_work_graph()
        changed_graph["local_branches"] = {"local-branch:feature": {"id": "local-branch:feature", "name": "feature"}}
        real_change = {**changed, "sessions": {"5": {**changed["sessions"]["5"], "work_graph": changed_graph}}}
        assert webapp.transcripts_payload_event_signature(base) != webapp.transcripts_payload_event_signature(real_change)
    finally:
        webapp.control_server.stop()


def test_warm_metadata_cache_refreshes_cached_graph_after_network_enrichment(monkeypatch):
    pane = PaneInfo("5", "0", "0", "%5", "5:0.0", "/repo", "claude", True, True, "claude", 5)
    info = SessionInfo(session="5", panes=[pane], selected_pane=pane, agents=[])
    cached_graph = metadata.empty_work_graph()
    cached_graph["generation"] = 10
    enriched_graph = metadata.empty_work_graph()
    enriched_graph["generation"] = 20
    enriched_graph["pull_requests"] = {"pull-request:github:80": {"id": "pull-request:github:80", "number": 80}}
    calls = []
    refreshes = []

    def fake_session_work_graph(_info, _cache, allow_network=True):
        calls.append(allow_network)
        return enriched_graph

    monkeypatch.setattr(app_module, "session_work_graph", fake_session_work_graph)
    webapp = app_module.TmuxWebtermApp(["5"])
    # The jobd network/git warm itself is tested independently (test_jobd.py); here only the
    # downstream "did the graph actually change" comparison is under test.
    monkeypatch.setattr(webapp, "warm_metadata_cache_via_jobd", lambda sessions, repository_generations=(): calls.append("jobd"))
    try:
        webapp.set_transcripts_payload_cache({"sessions": {"5": {"work_graph": cached_graph}}})
        monkeypatch.setattr(webapp, "start_transcripts_payload_refresh", lambda publish=False, defer=False, not_before=None: refreshes.append((publish, defer)) or True)
        webapp.warm_metadata_cache({"5": info}, threading.Event())
    finally:
        webapp.control_server.stop()

    assert calls == ["jobd", False]
    assert refreshes == [(True, True)]


def test_warm_metadata_cache_ignores_graph_generation_only(monkeypatch):
    pane = PaneInfo("5", "0", "0", "%5", "5:0.0", "/repo", "claude", True, True, "claude", 5)
    info = SessionInfo(session="5", panes=[pane], selected_pane=pane, agents=[])
    cached_graph = metadata.empty_work_graph()
    cached_graph["generation"] = 10
    enriched_graph = metadata.empty_work_graph()
    enriched_graph["generation"] = 20
    refreshes = []

    def fake_session_work_graph(_info, _cache, allow_network=True):
        return enriched_graph

    monkeypatch.setattr(app_module, "session_work_graph", fake_session_work_graph)
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp, "warm_metadata_cache_via_jobd", lambda sessions, repository_generations=(): None)
    try:
        webapp.set_transcripts_payload_cache({"sessions": {"5": {"work_graph": cached_graph}}})
        monkeypatch.setattr(webapp, "start_transcripts_payload_refresh", lambda publish=False, defer=False, not_before=None: refreshes.append((publish, defer)) or True)
        webapp.warm_metadata_cache({"5": info}, threading.Event())
    finally:
        webapp.control_server.stop()

    assert refreshes == []


def test_warm_metadata_cache_reuses_valid_repository_identity(monkeypatch):
    pane = PaneInfo("5", "0", "0", "%5", "5:0.0", "/repo", "claude", True, True, "claude", 5)
    agent = AgentInfo("5", "claude", 5, "%5", "claude", "/repo", "working", "agent-5", None, None)
    info = SessionInfo(session="5", panes=[pane], selected_pane=pane, agents=[agent])
    calls = []
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp, "warm_metadata_cache_via_jobd", lambda sessions, repository_generations=(): calls.append(sorted(sessions)))
    monkeypatch.setattr(app_module, "session_work_graph", lambda *_args, **_kwargs: metadata.empty_work_graph())
    monkeypatch.setattr(webapp, "watcher_covers_repo", lambda root: root == "/repo")
    try:
        cached_graph = metadata.empty_work_graph()
        cached_graph["git_worktrees"] = {"worktree:/repo": {"root": "/repo"}}
        webapp.set_transcripts_payload_cache({"sessions": {"5": {"work_graph": cached_graph}}})
        webapp.warm_metadata_cache({"5": info}, threading.Event())
        # Agent/status payload churn is outside the repository identity, so it must not cause a
        # second GitHub/Linear/Git warm while the normal metadata TTL is valid.
        idle_agent = AgentInfo("5", "claude", 5, "%5", "claude", "/repo", "idle", "agent-5", None, None)
        status_only = SessionInfo(session="5", panes=[pane], selected_pane=pane, agents=[idle_agent])
        webapp.warm_metadata_cache({"5": status_only}, threading.Event())
        assert calls == [["5"]]

        with webapp.session_files_service.cache_lock:
            webapp.session_files_service.repo_dirty_generations["/repo"] = 1
        webapp.warm_metadata_cache({"5": status_only}, threading.Event())
        assert calls == [["5"], ["5"]]

        changed_pane = PaneInfo("5", "0", "0", "%5", "5:0.0", "/other-repo", "claude", True, True, "claude", 5)
        changed_agent = AgentInfo("5", "claude", 5, "%5", "claude", "/other-repo", "idle", "agent-5", None, None)
        changed_path = SessionInfo(session="5", panes=[changed_pane], selected_pane=changed_pane, agents=[changed_agent])
        webapp.warm_metadata_cache({"5": changed_path}, threading.Event())
        assert calls == [["5"], ["5"], ["5"]]

        with webapp.metadata_warm_lock:
            signature, _deadline = webapp.metadata_warm_record.completed["5"]
            webapp.metadata_warm_record.completed["5"] = (signature, 0.0)
        webapp.warm_metadata_cache({"5": changed_path}, threading.Event())
    finally:
        webapp.control_server.stop()

    assert calls == [["5"], ["5"], ["5"], ["5"]]


def test_warm_metadata_cache_via_jobd_replays_product_entries_into_metadata_cache(monkeypatch):
    pane = PaneInfo("5", "0", "0", "%5", "5:0.0", "/repo", "claude", True, True, "claude", 5)
    info = SessionInfo(session="5", panes=[pane], selected_pane=pane, agents=[])

    def fake_session_work_graph(_info, cache, allow_network=True):
        cache.set("github-pr-branch:acme/repo:main", {"number": 9, "state": "open"}, ttl=120.0)
        return metadata.empty_work_graph()

    monkeypatch.setattr(metadata, "session_work_graph", fake_session_work_graph)
    webapp = app_module.TmuxWebtermApp(["5"])
    submitted = _install_fake_metadata_warm_jobd(monkeypatch, webapp)
    try:
        webapp.warm_metadata_cache_via_jobd({"5": info})
    finally:
        webapp.control_server.stop()

    assert submitted == [["5"]]
    assert webapp.metadata_cache.get("github-pr-branch:acme/repo:main") == {"number": 9, "state": "open"}


def test_metadata_warm_jobd_identity_ignores_agent_status_but_fences_watched_repo_changes(monkeypatch):
    pane = PaneInfo("5", "0", "0", "%5", "5:0.0", "/repo", "claude", True, True, "claude", 5)
    working = SessionInfo("5", [pane], pane, [AgentInfo("5", "claude", 5, "%5", "claude", "/repo", "working", "agent-5", None, None)])
    idle = SessionInfo("5", [pane], pane, [AgentInfo("5", "claude", 5, "%5", "claude", "/repo", "idle", "agent-5", None, None)])
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp, "watcher_covers_repo", lambda repo: str(repo) == "/repo")
    try:
        first = webapp.metadata_warm_source_signature({"5": working}, (("/repo", 0),))
        assert webapp.metadata_warm_source_signature({"5": idle}, (("/repo", 0),)) == first
        assert webapp.metadata_warm_source_signature({"5": idle}, (("/repo", 1),)) != first
    finally:
        webapp.control_server.stop()


def test_two_app_session_files_callers_share_jobd_product_until_watcher_generation_changes(tmp_path, monkeypatch):
    """The real app caller derives one cross-port product until its watcher state changes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "one.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "one.py")
    git(repo, "commit", "-m", "init")
    (repo / "one.py").write_text("x = 2\n", encoding="utf-8")
    broker = jobd.PersistentJobBroker(tmp_path / "jobd.sock", idle_seconds=10.0, workers=1)
    worker = threading.Thread(target=broker.run, daemon=True)
    worker.start()
    first = app_module.TmuxWebtermApp(["5"])
    second = app_module.TmuxWebtermApp(["5"])
    pane = PaneInfo("5", "0", "0", "%5", "5:0.0", str(repo), "claude", True, True, "claude", 5)
    working = SessionInfo("5", [pane], pane, [AgentInfo("5", "claude", 5, "%5", "claude", str(repo), "working", "agent-5", None, None)])
    idle = SessionInfo("5", [pane], pane, [AgentInfo("5", "claude", 5, "%5", "claude", str(repo), "idle", "agent-5", None, None)])
    try:
        for webapp in (first, second):
            webapp.job_client = jobd.JobClient(tmp_path / "jobd.sock")
            monkeypatch.setattr(webapp, "watcher_covers_repo", lambda candidate: Path(candidate) == repo)
        deadline = time.monotonic() + 2.0
        while not first.job_client.registry.healthy() and time.monotonic() < deadline:
            time.sleep(0.01)

        first_payload, first_status = first.session_files_payload_for_infos("5", {"5": working}, 24.0, force=True, requester="finder-visible")
        second_payload, second_status = second.session_files_payload_for_infos("5", {"5": idle}, 24.0, force=True, requester="finder-session-toggle")
        status_before = first.job_client.request({"action": "status"})

        for webapp in (first, second):
            with webapp.session_files_service.cache_lock:
                webapp.session_files_service.repo_dirty_generations[str(repo)] = 1
        changed_payload, changed_status = first.session_files_payload_for_infos("5", {"5": idle}, 24.0, force=True, requester="filesystem-event")
        status_after = first.job_client.request({"action": "status"})
    finally:
        first.control_server.stop()
        second.control_server.stop()
        first.job_client.request({"action": "shutdown"})
        worker.join(timeout=2.0)

    assert first_status == second_status == changed_status == HTTPStatus.OK
    assert first_payload == second_payload
    assert changed_payload["files"] == first_payload["files"]
    # Status/Finder churn may rebuild the lightweight attribution product, but the shared Git
    # snapshot must be reused until the watcher-authoritative repository generation changes.
    assert status_before["product_counters"]["session_files_view"]["completed"] == 2
    assert status_before["product_work_totals"]["session_files_view"]["git_snapshots"] == 1
    assert status_before["product_work_totals"]["session_files_view"]["git_snapshot_cache_hits"] == 1
    assert status_after["product_counters"]["session_files_view"]["completed"] == 3
    assert status_after["product_work_totals"]["session_files_view"]["git_snapshots"] == 2


def test_warm_metadata_cache_via_jobd_raises_when_jobd_submit_is_rejected(monkeypatch):
    pane = PaneInfo("5", "0", "0", "%5", "5:0.0", "/repo", "claude", True, True, "claude", 5)
    info = SessionInfo(session="5", panes=[pane], selected_pane=pane, agents=[])
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp.job_client, "submit", lambda *args, **kwargs: {"ok": False, "error": "queue full"})
    try:
        with pytest.raises(app_module.MetadataWarmJobdUnavailable):
            webapp.warm_metadata_cache_via_jobd({"5": info})
    finally:
        webapp.control_server.stop()


def test_client_watch_snapshot_skips_volatile_transcript_payload_push(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    graph = metadata.empty_work_graph()
    payloads = [
        {
            "server_time": "2026-06-24 12:00:00 PDT",
            "server_uptime_seconds": 1.0,
            "session_order": ["5"],
            "sessions": {"5": {"session": "5", "work_graph": graph}},
        },
        {
            "server_time": "2026-06-24 12:00:05 PDT",
            "server_uptime_seconds": 6.0,
            "session_order": ["5"],
            "sessions": {"5": {"session": "5", "work_graph": graph}},
        },
    ]
    monkeypatch.setattr(webapp, "build_transcripts_payload", lambda: payloads.pop(0))
    monkeypatch.setattr(webapp, "publish_context_items_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "publish_activity_summary_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "publish_session_files_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "client_watch_roots_snapshot", lambda: [])
    monkeypatch.setattr(webapp, "background_can_run", lambda role: False)
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


def test_client_watch_snapshot_replacement_rejects_retired_worker(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    old_started = threading.Event()
    release_old = threading.Event()
    replacement_started = threading.Event()
    release_replacement = threading.Event()
    events = []
    build_count = 0

    def build_payload():
        nonlocal build_count
        build_count += 1
        if build_count == 1:
            old_started.set()
            assert release_old.wait(timeout=3)
            return {"marker": "old"}
        replacement_started.set()
        assert release_replacement.wait(timeout=3)
        return {"marker": "new"}

    monkeypatch.setattr(webapp, "build_transcripts_payload", build_payload)
    monkeypatch.setattr(webapp, "publish_context_items_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "publish_activity_summary_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "publish_session_files_ready_events", lambda trigger="watch": [])
    monkeypatch.setattr(webapp, "client_watch_roots_snapshot", lambda: [])
    monkeypatch.setattr(webapp, "background_can_run", lambda role: False)
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **kwargs: events.append((event_type, payload or {}, kwargs)))
    try:
        old_record = webapp.client_watch_service.event_watcher_record
        assert webapp.start_client_watch_snapshot_publish() is True
        old_worker = old_record.snapshot_worker
        assert old_worker is not None
        assert old_started.wait(timeout=2)

        webapp.stop_client_event_watcher()
        replacement = webapp.client_watch_service.event_watcher_record
        assert replacement is not old_record
        assert webapp.start_client_watch_snapshot_publish() is True
        assert replacement_started.wait(timeout=2)
        replacement_worker = replacement.snapshot_worker
        assert replacement_worker is not None
        release_replacement.set()
        replacement_worker.join(timeout=2)

        release_old.set()
        old_worker.join(timeout=2)
        with webapp.activity_transcript_service.transcripts_payload_cache_lock:
            cached = webapp.activity_transcript_service.transcripts_payload_cache_record.payload
            cache_worker = webapp.activity_transcript_service.transcripts_payload_cache_record.worker
    finally:
        release_old.set()
        release_replacement.set()
        webapp.stop_client_event_watcher()
        webapp.control_server.stop()

    # The committing build stamps its own generation into the payload, so compare the marker that
    # identifies WHICH build won rather than the full committed dict.
    assert cached["marker"] == "new"
    assert cache_worker is None
    assert old_record.snapshot_worker is None
    assert replacement.snapshot_worker is None
    assert [event_type for event_type, _payload, _kwargs in events] == ["transcripts_changed"]


def test_client_watch_snapshot_thread_start_failure_allows_retry(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    real_thread = threading.Thread
    retry_started = threading.Event()
    release_retry = threading.Event()

    class FailingThread:
        def __init__(self, target=None, daemon=False):
            self.target = target
            self.daemon = daemon

        def start(self):
            raise RuntimeError("thread unavailable")

    monkeypatch.setattr(app_module.threading, "Thread", FailingThread)
    try:
        with pytest.raises(RuntimeError, match="thread unavailable"):
            webapp.start_client_watch_snapshot_publish()
        with webapp.client_watch_service.lock:
            assert webapp.client_watch_service.event_watcher_record.snapshot_worker is None
        with webapp.activity_transcript_service.transcripts_payload_cache_lock:
            assert webapp.activity_transcript_service.transcripts_payload_cache_record.worker is None

        monkeypatch.setattr(app_module.threading, "Thread", real_thread)
        def retry_build():
            retry_started.set()
            assert release_retry.wait(timeout=3)
            return {"marker": "retry"}

        monkeypatch.setattr(webapp, "build_transcripts_payload", retry_build)
        monkeypatch.setattr(webapp, "publish_context_items_ready_events", lambda trigger="watch": [])
        monkeypatch.setattr(webapp, "publish_activity_summary_ready_events", lambda trigger="watch": [])
        monkeypatch.setattr(webapp, "publish_session_files_ready_events", lambda trigger="watch": [])
        monkeypatch.setattr(webapp, "client_watch_roots_snapshot", lambda: [])
        monkeypatch.setattr(webapp, "background_can_run", lambda role: False)
        assert webapp.start_client_watch_snapshot_publish() is True
        assert retry_started.wait(timeout=2)
        worker = webapp.client_watch_service.event_watcher_record.snapshot_worker
        assert worker is not None
        release_retry.set()
        worker.join(timeout=2)
    finally:
        release_retry.set()
        monkeypatch.setattr(app_module.threading, "Thread", real_thread)
        webapp.stop_client_event_watcher()
        webapp.control_server.stop()

    assert webapp.activity_transcript_service.transcripts_payload_cache_record.payload["marker"] == "retry"
    assert webapp.client_watch_service.event_watcher_record.snapshot_worker is None


def test_metadata_badge_pulse_expiry_does_not_persist(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    signature = {"main": "", "pr": "123", "status": "open", "ci": "pending"}
    persist_calls = []
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(webapp, "metadata_badge_signatures_for_session", lambda _payload: signature)
    monkeypatch.setattr(webapp, "persist_metadata_badge_state_locked", lambda: persist_calls.append("persist"))
    webapp.metadata_badge_records = {
        "6": app_module.MetadataBadgeRecord(signature=dict(signature), pulse_until={"ci": 99.0})
    }
    try:
        payloads = {"6": {}}
        webapp.apply_metadata_badge_pulses(payloads)
    finally:
        webapp.control_server.stop()

    assert persist_calls == []
    assert webapp.metadata_badge_records["6"].pulse_until == {}
    assert "metadata_badge_pulse_remaining_ms" not in payloads["6"]


def test_metadata_badge_signature_change_persists(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    next_signature = {"main": "", "pr": "123", "status": "merged", "ci": "passing"}
    persist_calls = []
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(webapp, "metadata_badge_signatures_for_session", lambda _payload: next_signature)
    monkeypatch.setattr(webapp, "persist_metadata_badge_state_locked", lambda: persist_calls.append("persist"))
    webapp.metadata_badge_records = {
        "6": app_module.MetadataBadgeRecord(
            signature={"main": "", "pr": "123", "status": "open", "ci": "pending"},
            pulse_until={},
        )
    }
    try:
        webapp.apply_metadata_badge_pulses({"6": {}})
    finally:
        webapp.control_server.stop()

    assert persist_calls == ["persist"]
    assert webapp.metadata_badge_records["6"].signature == next_signature


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


def _install_fake_session_files_jobd(monkeypatch, webapp, response):
    """Mock only the `session_files_view` jobd product (checkbox 4/9 routes the
    request-thread compute through jobd submit()+product(), not an inline
    session_files call); every other task (e.g. transcript_view) still goes
    through the real job_client. `response` is either a payload dict or a
    call-tuple -> payload dict callable; returns the recorded submit calls as
    (session, infos_keys, hours, from_ref, to_ref, repo_refs) tuples."""
    calls = []
    coalesce_keys: set[str] = set()
    real_submit = webapp.job_client.submit
    real_product = webapp.job_client.product

    def fake_submit(task, payload, *, coalesce_key="", **kwargs):
        if task != "session_files_view":
            return real_submit(task, payload, coalesce_key=coalesce_key, **kwargs)
        coalesce_keys.add(coalesce_key)
        calls.append((
            payload.get("session") or None,
            tuple(payload.get("infos", {}).keys()),
            payload["hours"],
            payload.get("from_ref") or None,
            payload.get("to_ref") or None,
            payload.get("repo_refs") or None,
        ))
        return {"ok": True, "job": {"job_id": "fake"}}

    def fake_product(coalesce_key, timeout=0.5):
        if coalesce_key not in coalesce_keys:
            return real_product(coalesce_key, timeout=timeout)
        result = response(calls[-1]) if callable(response) else response
        body = json.dumps({"payload": result, "status": 200}).encode("utf-8")
        return {"ok": True, "state": "ready", "generation": 2**62}, body

    monkeypatch.setattr(webapp.job_client, "submit", fake_submit)
    monkeypatch.setattr(webapp.job_client, "product", fake_product)
    return calls


def _install_fake_tabber_activity_jobd(monkeypatch, webapp):
    """Mock only the `tabber_activity_view` jobd product; every other task still goes through the
    real job_client. Runs the REAL activity_summary.tabber_activity_view_result end-to-end (an
    honest simulation, not canned data), so the web-side gathering (agent_window_gathered_agents,
    which still uses whatever screen-state/discover_sessions mocks the calling test installed)
    feeds a real pure assembly. Returns the list of session names submitted per call, in order,
    so a test can assert which sessions were rebuilt without reaching into the old in-process call
    points that this migration retired."""
    submitted_session_batches: list[list[str]] = []
    coalesce_keys: set[str] = set()
    payloads_by_key: dict[str, dict] = {}
    real_submit = webapp.job_client.submit
    real_product = webapp.job_client.product

    def fake_submit(task, payload, *, coalesce_key="", **kwargs):
        if task != "tabber_activity_view":
            return real_submit(task, payload, coalesce_key=coalesce_key, **kwargs)
        coalesce_keys.add(coalesce_key)
        payloads_by_key[coalesce_key] = payload
        submitted_session_batches.append(sorted(payload.get("sessions", {}).keys()))
        return {"ok": True, "job": {"job_id": "fake"}}

    def fake_product(coalesce_key, timeout=0.5):
        if coalesce_key not in coalesce_keys:
            return real_product(coalesce_key, timeout=timeout)
        # compute_tabber_activity_rows_via_jobd reads {"session_rows": ...} directly from the
        # product body -- unlike session_files_view, there is no {"payload": ..., "status": ...}
        # envelope for this product.
        result = activity_summary.tabber_activity_view_result(payloads_by_key[coalesce_key], max_bytes=512 * 1024)
        body = json.dumps(result).encode("utf-8")
        return {"ok": True, "state": "ready", "generation": 2**62}, body

    monkeypatch.setattr(webapp.job_client, "submit", fake_submit)
    monkeypatch.setattr(webapp.job_client, "product", fake_product)
    return submitted_session_batches


def _install_fake_metadata_warm_jobd(monkeypatch, webapp):
    """Mock only the `metadata_warm_view` jobd product; every other task still goes through the
    real job_client. Runs the REAL metadata.metadata_warm_view_result end-to-end (a session's live
    `session_work_graph(..., allow_network=True)` call happens for real against whatever
    `metadata.session_work_graph`/network mocks the calling test installed), proving the returned
    entries actually replay into `webapp.metadata_cache`."""
    submitted_session_batches: list[list[str]] = []
    coalesce_keys: set[str] = set()
    payloads_by_key: dict[str, dict] = {}
    real_submit = webapp.job_client.submit
    real_product = webapp.job_client.product

    def fake_submit(task, payload, *, coalesce_key="", **kwargs):
        if task != "metadata_warm_view":
            return real_submit(task, payload, coalesce_key=coalesce_key, **kwargs)
        coalesce_keys.add(coalesce_key)
        payloads_by_key[coalesce_key] = payload
        submitted_session_batches.append(sorted(payload.get("sessions", {}).keys()))
        return {"ok": True, "job": {"job_id": "fake"}}

    def fake_product(coalesce_key, timeout=0.5):
        if coalesce_key not in coalesce_keys:
            return real_product(coalesce_key, timeout=timeout)
        # compute reads {"entries": ...} directly from the product body -- no {"payload": ...,
        # "status": ...} envelope, matching the tabber_activity_view convention.
        result = metadata.metadata_warm_view_result(payloads_by_key[coalesce_key], max_bytes=512 * 1024)
        body = json.dumps(result).encode("utf-8")
        return {"ok": True, "state": "ready", "generation": 2**62}, body

    monkeypatch.setattr(webapp.job_client, "submit", fake_submit)
    monkeypatch.setattr(webapp.job_client, "product", fake_product)
    return submitted_session_batches


def test_activity_summary_payload_reuses_cached_session_summary(monkeypatch, tmp_path, legacy_activity_summary_enabled):
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
    work_payload = {
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
    monkeypatch.setattr(app_module, "session_work_graph", lambda info, cache, allow_network=False: {"version": 1, "loading": False, "generation": 1, "git_worktrees": {}, "local_repositories": {}, "hosted_repositories": {}, "local_branches": {}, "pull_requests": {}, "linear_issues": {}, "path_observations": {}, "runtime_actors": {}, "tmux_sessions": {}, "tmux_windows": {}, "tmux_panes": {}, "worktree_branch_activity": {}})
    monkeypatch.setattr(app_module, "activity_work_summary_from_graph", lambda _graph: work_payload)

    def fake_build(info, work, files, locale="en", **_kwargs):
        calls.append((info.session, locale))
        return {"session": info.session, "agent": "codex", "active": False, "repos": [str(tmp_path)], "files": {"count": 1, "added": 1, "removed": 0}, "lines": ["cached test"], "local": "cached test"}

    monkeypatch.setattr(app_module, "build_session_activity_summary", fake_build)
    webapp = app_module.TmuxWebtermApp(["5"])
    _install_fake_session_files_jobd(monkeypatch, webapp, files_payload)
    webapp.cached_session_files_payload_for_info = lambda _info, hours=24.0, wait_for_fresh=True: files_payload
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
        first = webapp.assemble_activity_summary_payload()
        second = webapp.assemble_activity_summary_payload()
        third = webapp.assemble_activity_summary_payload(force=True)
        localized = webapp.assemble_activity_summary_payload(locale="zh-Hant")
    finally:
        webapp.control_server.stop()

    assert calls == [("5", "en"), ("5", "en"), ("5", "zh-Hant")]
    assert first["global"]["files"] == {"count": 1, "added": 1, "removed": 0}
    assert first["agents"][0]["label"] == "session '5' 0:codex"
    assert first["agents"][0]["recent_paths"][0]["path"] == str(tmp_path)
    assert first["session_info"]["5"]["path"] == str(tmp_path)
    assert first["session_info"]["5"]["git"] == work_payload["git"]
    assert first["session_info"]["5"]["pull_request"]["number"] == 42
    assert first["session_info"]["5"]["ci"] == {"status_label": "passing"}
    assert first["session_info"]["5"]["linear"][0]["identifier"] == "GUI-7"
    assert first["session_info"]["5"]["latest_summary"] == "cached test"
    assert first["session_info"]["5"]["recent_events"][0]["message"] == "ready"
    assert tail_many_calls == [(("5",), 5), (("5",), 5), (("5",), 5), (("5",), 5)]
    assert second["sessions"]["5"]["local"] == "cached test"
    assert third["sessions"]["5"]["local"] == "cached test"
    assert localized["locale"] == "zh-Hant"


def test_activity_session_info_payload_normalizes_malformed_work_git(tmp_path):
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

        webapp.activity_transcript_service.tabber_cache_record.stored_at -= webapp.tabber_activity_refresh_seconds() + 1
        monkeypatch.setattr(webapp, "read_tabber_activity_disk_cache", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(webapp, "start_tabber_activity_cache_refresh", lambda: "queued")
        stale, _status = webapp.activity_payload()

        assert stale["activity"]["5"]["last_user_input_ts"] == 100
        assert stale["cache"]["stale"] is True
        assert stale["cache"]["refreshing"] == "queued"
        assert calls == ["snapshot"]
    finally:
        webapp.control_server.stop()


def test_owner_activity_payload_without_cache_queues_one_shared_refresh(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    starts = iter([True, False])
    try:
        monkeypatch.setattr(webapp, "get_tabber_activity_cache", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(webapp, "start_tabber_activity_cache_refresh", lambda: next(starts))
        monkeypatch.setattr(webapp, "build_activity_payload", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cold requests must not rebuild synchronously")))

        first, first_status = webapp.activity_payload()
        second, second_status = webapp.activity_payload()
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert first["cache"]["refreshing"] is True
    assert second["cache"]["refreshing"] is False
    assert first["activity"] == second["activity"] == {}


def test_tabber_activity_cache_record_owns_signature_and_refresh(monkeypatch):
    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(webapp, "background_can_run", lambda _role: True)
    monkeypatch.setattr(webapp, "read_tabber_activity_disk_cache", lambda *_args, **_kwargs: None)
    payload = {"activity": {}, "agents": [], "session_file_hours": 24.0}
    try:
        webapp.set_tabber_activity_cache(payload, write_disk=False, source_signature="source-a")
        assert webapp.get_tabber_activity_cache(60.0, source_signature="source-a")[0] == payload
        assert webapp.get_tabber_activity_cache(60.0, source_signature="source-b") is None
        assert webapp.start_tabber_activity_cache_refresh() is True
        assert webapp.activity_transcript_service.tabber_cache_record.refresh_worker is not None
        assert webapp.start_tabber_activity_cache_refresh() is False
    finally:
        webapp.control_server.stop()


def test_tabber_activity_disk_cache_path_is_stable_across_source_generations():
    webapp = app_module.TmuxWebtermApp([])
    try:
        first_path, first_signature = webapp.tabber_activity_cache_disk_path(24.0, "source-a")
        second_path, second_signature = webapp.tabber_activity_cache_disk_path(24.0, "source-b")
    finally:
        webapp.control_server.stop()

    assert first_path == second_path
    assert first_signature == second_signature


def test_activity_payload_keeps_last_good_cache_during_source_generation_change(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    payload = {"activity": {"5": {"last_user_input_ts": 100}}, "agents": [], "agent_windows": {}, "errors": [], "session_scope": "configured", "session_file_hours": 24.0}
    try:
        webapp.set_tabber_activity_cache(payload, write_disk=False, source_signature="generation-one")
        monkeypatch.setattr(webapp, "tabber_activity_source_signature", lambda: "generation-two")
        monkeypatch.setattr(webapp, "start_tabber_activity_cache_refresh", lambda: True)

        result, status = webapp.activity_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert result["activity"] == payload["activity"]
    assert result["cache"]["stale"] is True
    assert result["cache"]["refreshing"] is True


def test_tabber_activity_parallel_cache_state_is_retired():
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert "self.tabber_activity_cache:" not in source
    assert "self.tabber_activity_cache =" not in source
    assert "self.tabber_activity_cache_source_signature" not in source
    assert "self.tabber_activity_cache_refreshing" not in source
    assert "tabber_activity_cache_record.refreshing" not in source


def test_tabber_activity_cache_refresh_failed_start_allows_retry(monkeypatch):
    workers = []

    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            workers.append(self)

        def start(self):
            if len(workers) == 1:
                raise RuntimeError("thread unavailable")

    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(webapp, "background_can_run", lambda _role: True)
    try:
        with pytest.raises(RuntimeError, match="thread unavailable"):
            webapp.start_tabber_activity_cache_refresh()
        assert webapp.activity_transcript_service.tabber_cache_record.refresh_worker is None
        assert webapp.start_tabber_activity_cache_refresh() is True
        assert webapp.activity_transcript_service.tabber_cache_record.refresh_worker is workers[1]
    finally:
        webapp.control_server.stop()


def test_retired_tabber_activity_cache_refresh_cannot_clear_replacement(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    old_worker = threading.Thread()
    replacement_worker = threading.Thread()
    webapp.activity_transcript_service.tabber_cache_record.refresh_worker = old_worker
    monkeypatch.setattr(webapp, "background_refresh_event_details", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(webapp, "log_sampled_background_refresh_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(webapp, "publish_background_refresh_done", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        webapp,
        "refresh_tabber_activity_cache",
        lambda: setattr(webapp.activity_transcript_service.tabber_cache_record, "refresh_worker", replacement_worker),
    )
    try:
        webapp.run_tabber_activity_cache_refresh(old_worker)
        assert webapp.activity_transcript_service.tabber_cache_record.refresh_worker is replacement_worker
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


def test_input_heartbeat_record_owns_real_worker_coalescing_stop_and_restart(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    monkeypatch.setattr(app_module, "INPUT_HEARTBEAT_COALESCE_SECONDS", 0.0)
    heartbeat_times = iter([1012.0, 1012.02, 1020.0])
    monkeypatch.setattr(app_module.time, "time", lambda: next(heartbeat_times))
    webapp = app_module.TmuxWebtermApp(["6"])
    calls = []
    flushed = [threading.Event(), threading.Event()]

    def record_heartbeat(session, window, *, ts, byte_count, source):
        calls.append((session, window, ts, byte_count, source))
        flushed[len(calls) - 1].set()

    real_start = webapp.start_input_heartbeat_worker
    try:
        webapp.cached_active_window_for = lambda session: "1"
        monkeypatch.setattr(webapp.activity_ledger, "heartbeat", record_heartbeat)

        monkeypatch.setattr(webapp, "start_input_heartbeat_worker", lambda: None)
        webapp.record_user_input("6", 1, data="x")
        webapp.record_user_input("6", 2, data="yy")
        assert webapp.input_heartbeat_record.pending[("6", "host")].byte_count == 3
        monkeypatch.setattr(webapp, "start_input_heartbeat_worker", real_start)
        real_start()
        first_worker = webapp.input_heartbeat_record.worker
        assert first_worker is not None
        assert flushed[0].wait(timeout=1.0)
        webapp.stop_input_heartbeat_worker()

        assert calls == [("6", "1", 1012.02, 3, "host")]
        assert first_worker.is_alive() is False
        assert webapp.input_heartbeat_record.worker is None
        assert webapp.input_heartbeat_record.stop_requested is True
        assert webapp.input_heartbeat_record.flush_active is False

        monkeypatch.setattr(webapp, "start_input_heartbeat_worker", lambda: None)
        webapp.record_user_input("6", 4, data="zzzz")
        monkeypatch.setattr(webapp, "start_input_heartbeat_worker", real_start)
        real_start()
        second_worker = webapp.input_heartbeat_record.worker
        assert second_worker is not None and second_worker is not first_worker
        assert webapp.input_heartbeat_record.stop_requested is False
        assert flushed[1].wait(timeout=1.0)
        webapp.stop_input_heartbeat_worker()

        assert calls == [
            ("6", "1", 1012.02, 3, "host"),
            ("6", "1", 1020.0, 4, "host"),
        ]
        assert second_worker.is_alive() is False
        assert webapp.input_heartbeat_record.worker is None
    finally:
        webapp.stop_input_heartbeat_worker()
        webapp.control_server.stop()


def test_input_heartbeat_parallel_lifecycle_attributes_are_retired():
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    for name in (
        "input_heartbeat_condition",
        "input_heartbeat_pending",
        "input_heartbeat_flush_active",
        "input_heartbeat_worker_stop",
        "input_heartbeat_worker_thread",
    ):
        assert f"self.{name}" not in source
    assert source.count("self.flush_input_heartbeat_batch(batch)") == 2


def test_record_user_input_cache_miss_avoids_tmux_and_refreshes_out_of_band(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["7770"])
    refreshes = []

    def fail_tmux(*_args, **_kwargs):
        raise AssertionError("record_user_input must not call tmux")

    try:
        webapp.set_transcripts_payload_cache({"sessions": {"7770": {"panes": []}}})
        monkeypatch.setattr(app_module, "tmux", fail_tmux)
        monkeypatch.setattr(webapp, "start_transcripts_payload_refresh", lambda publish=False, defer=False, not_before=None: refreshes.append((publish, defer)) or True)
        monkeypatch.setattr(webapp.activity_ledger, "_clock", lambda: 2000.0)
        monkeypatch.setattr(app_module.time, "time", lambda: 2000.0)

        webapp.record_user_input("7770", 1, data="x")
        assert webapp.flush_input_heartbeats()
        activity = webapp.activity_snapshot_with_recency()

        assert activity["7770"]["last_user_input_ts"] == 2000.0
        assert "7770:0" not in activity
        assert refreshes == [(False, True)]
    finally:
        webapp.stop_input_heartbeat_worker()
        webapp.control_server.stop()


def test_active_window_for_can_refresh_live_tmux_window_off_input_path(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["7770"])

    def fake_tmux(args, timeout=5.0):
        assert args == ["display-message", "-p", "-t", "7770:", "#{window_index}"]
        return app_module.subprocess.CompletedProcess(args, 0, "0\n", "")

    try:
        webapp.set_transcripts_payload_cache({"sessions": {"7770": {"panes": []}}})
        monkeypatch.setattr(app_module, "tmux", fake_tmux)

        assert webapp.active_window_for("7770") == "0"
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

    try:
        record = webapp.activity_transcript_service.tabber_warmer_record
        record.running = True
        webapp.mark_tabber_activity_consumer()
        record.refresh_due_at = time.monotonic()
        record.refresh_triggers.add("test")
        monkeypatch.setattr(webapp, "refresh_tabber_activity_cache", lambda: refreshes.append("refresh") or {})
        monkeypatch.setattr(webapp, "publish_tabber_activity_refresh_if_changed", lambda **_kwargs: events.append("published") or setattr(record, "running", False) or True)
        webapp.tabber_activity_cache_warmer_loop(record)
    finally:
        webapp.control_server.stop()

    assert refreshes == ["refresh"]
    assert events == ["published"]
    assert webapp.activity_transcript_service.tabber_warmer_record.running is False


def test_tabber_activity_refresh_publishes_only_new_cache_generation(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    published = []
    try:
        monkeypatch.setattr(webapp, "publish_background_refresh_done", lambda role, payload: published.append((role, payload)))
        record = webapp.activity_transcript_service.tabber_cache_record
        record.payload = {"activity": {"1": {}}, "session_file_hours": 24.0}
        record.source_signature = "generation-one"

        assert webapp.publish_tabber_activity_refresh_if_changed(compute_ms=12.5) is True
        assert webapp.publish_tabber_activity_refresh_if_changed(compute_ms=25.0) is False
        record.source_signature = "generation-two"
        assert webapp.publish_tabber_activity_refresh_if_changed(compute_ms=7.0) is True
    finally:
        webapp.control_server.stop()

    assert published == [
        (app_module.BACKGROUND_ROLE_TABBER_ACTIVITY, {"compute_ms": 12.5, "cache_changed": True}),
        (app_module.BACKGROUND_ROLE_TABBER_ACTIVITY, {"compute_ms": 7.0, "cache_changed": True}),
    ]


def test_tabber_activity_cache_warmer_parks_without_visible_consumer(monkeypatch):
    """Expired consumer demand must PARK the warmer (no recurring idle work),
    and a returning consumer must unpark it via mark_tabber_activity_consumer."""
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    refreshes = []
    sleeps = []

    def forbid_sleep(seconds):
        sleeps.append(seconds)
        raise RuntimeError(f"unexpected sleep {seconds}")

    try:
        record = webapp.activity_transcript_service.tabber_warmer_record
        record.running = True
        monkeypatch.setattr(webapp, "refresh_tabber_activity_cache", lambda: refreshes.append("refresh") or {})
        monkeypatch.setattr(webapp, "tabber_activity_refresh_seconds", lambda: 15.0)
        monkeypatch.setattr(app_module.time, "sleep", forbid_sleep)

        # No consumer: the loop parks on the wake event (no sleep, no refresh).
        waits = []

        def wake_then_stop():
            waits.append(True)
            record.running = False  # unparked by teardown -> loop exits

        monkeypatch.setattr(record.wake, "wait", wake_then_stop)
        webapp.tabber_activity_cache_warmer_loop(record)
        assert refreshes == []
        assert sleeps == []
        assert waits == [True]
        recent = webapp.performance_metrics_payload()["recent"]
        assert recent[-1]["role"] == app_module.BACKGROUND_ROLE_TABBER_ACTIVITY
        assert recent[-1]["cache_status"] == "skipped:no-consumer"

        # A returning consumer unparks the warmer (event set), never starts a thread.
        record.wake.clear()
        assert webapp.mark_tabber_activity_consumer() is True
        assert record.wake.is_set()

    finally:
        webapp.control_server.stop()


def test_tabber_activity_producer_refresh_is_demand_gated_and_debounced(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    try:
        record = webapp.activity_transcript_service.tabber_warmer_record
        monkeypatch.setattr(webapp, "background_can_run", lambda _role: True)
        monkeypatch.setattr(webapp, "start_tabber_activity_cache_warmer", lambda: False)
        assert webapp.request_tabber_activity_refresh("tmux") is False
        webapp.mark_tabber_activity_consumer()
        assert webapp.request_tabber_activity_refresh("tmux") is True
        first_due = record.refresh_due_at
        assert webapp.request_tabber_activity_refresh("transcript") is True
        assert record.refresh_due_at >= first_due
        assert record.refresh_triggers == {"tmux", "transcript"}
        assert record.wake.is_set()
    finally:
        webapp.control_server.stop()


def test_tabber_activity_warmer_record_reuses_worker_and_protects_replacement(monkeypatch):
    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon
            self.started = False

        def start(self):
            self.started = True

        def is_alive(self):
            return self.started

    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(webapp, "background_can_run", lambda _role: True)
    now = [100.0]
    monkeypatch.setattr(app_module.time, "monotonic", lambda: now[0])
    try:
        assert webapp.mark_tabber_activity_consumer() is True
        assert webapp.tabber_activity_has_recent_consumer() is True
        now[0] = webapp.activity_transcript_service.tabber_warmer_record.consumer_until
        assert webapp.tabber_activity_has_recent_consumer() is False

        assert webapp.start_tabber_activity_cache_warmer() is True
        old_record = webapp.activity_transcript_service.tabber_warmer_record
        assert webapp.start_tabber_activity_cache_warmer() is False
        replacement = app_module.TabberActivityWarmerRecord(running=True)
        with webapp.activity_transcript_service.tabber_cache_lock:
            webapp.activity_transcript_service.tabber_warmer_record = replacement
        webapp.tabber_activity_cache_warmer_loop(old_record)
        assert webapp.activity_transcript_service.tabber_warmer_record is replacement
        assert replacement.running is True
    finally:
        webapp.control_server.stop()


def test_tabber_activity_warmer_parallel_state_is_retired():
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert "self.tabber_activity_cache_warmer_thread" not in source
    assert "self.tabber_activity_cache_warmer_running" not in source
    assert "self.tabber_activity_consumer_until" not in source


def test_activity_payload_hidden_consumer_does_not_refresh_stale_cache(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        payload = {"activity": {"5": {"last_user_input_ts": 100}}, "agents": [], "agent_windows": {}, "errors": [], "session_scope": "configured", "session_file_hours": 24.0}
        webapp.set_tabber_activity_cache(payload, write_disk=False, source_signature=webapp.tabber_activity_source_signature())
        webapp.activity_transcript_service.tabber_cache_record.stored_at -= webapp.tabber_activity_refresh_seconds() + 1
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


def test_activity_summary_agents_come_from_tabber_activity_cache(monkeypatch, legacy_activity_summary_enabled):
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
        payload = webapp.assemble_activity_summary_payload()
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

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    calls = _install_fake_session_files_jobd(monkeypatch, webapp, lambda call: {"session": call[0], "files": [], "repos": [], "errors": []})
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

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    fake_payload = {"files": [{"path": "/repo/one.txt"}], "repos": [{"path": "/repo"}], "errors": []}
    first_app = app_module.TmuxWebtermApp(["5"])
    second_app = app_module.TmuxWebtermApp(["5"])
    calls = _install_fake_session_files_jobd(monkeypatch, first_app, lambda call: {"session": call[0], **fake_payload})
    calls2 = _install_fake_session_files_jobd(monkeypatch, second_app, lambda call: {"session": call[0], **fake_payload})
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
    assert calls2 == []  # the second app reused the shared disk cache; it never called jobd itself
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
    # This test owns warm-cache adoption, not the separately covered stale-refresh path. Keep CPU
    # contention from aging the just-written fixture past the production 30-second refresh window.
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_SECONDS", 60 * 60.0)
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
        # App construction owns separate warm-up coverage. Measure only the explicit disk-cache
        # adoption below so a slower worker cannot attribute constructor work to this assertion.
        calls.clear()
        webapp.warm_start_session_files_payload_cache()
        payload, status = webapp.session_files_payload("5")
    finally:
        seed_app.control_server.stop()
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["cache"]["hit"] is True
    assert payload["cache"]["stale"] is False
    assert payload["files"] == [{"path": "README.md", "repo": str(tmp_path)}]
    assert calls == []
    assert path.stat().st_mtime_ns == payload_mtime


def test_session_files_batch_payload_discovers_once_and_uses_per_session_cache(monkeypatch):
    info5 = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    info6 = SessionInfo(session="6", panes=[], selected_pane=None, agents=[])
    discover_calls = []
    test_thread_id = threading.get_ident()

    def fake_discover(sessions):
        discover_calls.append((threading.get_ident(), tuple(sessions)))
        infos = {"5": info5, "6": info6}
        return {session: infos[session] for session in sessions if session in infos}, []

    monkeypatch.setattr(app_module, "discover_sessions", fake_discover)
    webapp = app_module.TmuxWebtermApp(["5", "6"])
    payload_calls = _install_fake_session_files_jobd(monkeypatch, webapp, lambda call: {"session": call[0], "files": [{"path": f"{call[0]}.txt"}], "repos": [], "errors": []})
    webapp.refresh_sessions = lambda *args, **kwargs: []
    discover_calls.clear()
    try:
        first, first_status = webapp.session_files_batch_payload(["5", "6"])
        second, second_status = webapp.session_files_batch_payload(["5", "6"])
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert [sessions for thread_id, sessions in discover_calls if thread_id == test_thread_id] == [("5", "6"), ("5", "6")]
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


def test_session_files_cold_miss_never_calls_inline_compute_on_request_thread(monkeypatch):
    # Checkbox 9: the request-thread cold-miss path must never resurrect inline
    # git/discovery -- it routes through the jobd session_files_view product.
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def fail_inline(*_args, **_kwargs):
        raise AssertionError("request-thread cold miss must not call inline session_files compute")

    monkeypatch.setattr(app_module.session_files, "session_files_payload", fail_inline)
    monkeypatch.setattr(app_module.session_files, "session_files_payload_for_info", fail_inline)
    webapp = app_module.TmuxWebtermApp(["5"])
    _install_fake_session_files_jobd(monkeypatch, webapp, {"session": "5", "files": [{"path": "via-jobd.py"}], "repos": [], "errors": []})
    webapp.refresh_sessions = lambda *args, **kwargs: []
    try:
        payload, status = webapp.session_files_payload("5", force=True)
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["files"] == [{"path": "via-jobd.py"}]


def test_session_files_jobd_unavailable_returns_typed_terminal_error_never_inline_git(monkeypatch):
    # Checkbox 9: when jobd cannot produce the product, the request thread must
    # serve the bounded "refreshing elsewhere" shape, never fall back to inline git.
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def fail_inline(*_args, **_kwargs):
        raise AssertionError("jobd-unavailable fallback must not call inline session_files compute")

    monkeypatch.setattr(app_module.session_files, "session_files_payload", fail_inline)
    monkeypatch.setattr(app_module.session_files, "session_files_payload_for_info", fail_inline)
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp.job_client, "submit", lambda *args, **kwargs: {"ok": False, "error": "jobd down"})
    webapp.refresh_sessions = lambda *args, **kwargs: []
    try:
        payload, status = webapp.session_files_payload("5", force=True)
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.SERVICE_UNAVAILABLE
    assert {key: payload[key] for key in ("ok", "status", "reason", "terminal")} == {"ok": False, "status": "SERVICE_UNAVAILABLE", "reason": "jobd down", "terminal": True}
    assert payload["cache"]["refreshing_elsewhere"] is False


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

    # The owner-side background refresh now materializes the payload in jobd; simulate a successful
    # product so the stale-while-revalidate recompute is still observed by the in-process fake.
    def fake_via_jobd(session, infos, hours, from_ref, to_ref, repo_refs, cache_key, priority="freshness", requester="unknown"):
        assert requester in {"api-session-files", "background-refresh"}
        return fake_session_files_payload(session, infos, hours, from_ref, to_ref, repo_refs)

    monkeypatch.setattr(webapp, "compute_session_files_payload_via_jobd", fake_via_jobd)
    try:
        first, first_status = webapp.session_files_payload("5")
        key = next(iter(webapp.session_files_service.cache))
        with webapp.session_files_service.cache_lock:
            stored_at, value = webapp.session_files_service.cache[key]
            webapp.session_files_service.cache[key] = (stored_at - app_module.SESSION_FILES_CACHE_SECONDS - 1.0, value)
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


def test_session_files_disk_prune_record_coalesces_and_tracks_completion(monkeypatch):
    now = [100.0]
    submissions = []
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(app_module.time, "monotonic", lambda: now[0])
    webapp.job_client = SimpleNamespace(
        produce=lambda *args, **kwargs: submissions.append((args, kwargs)) or (
            {"ok": True, "job": {"job_id": f"prune-{len(submissions)}", "status": "queued"}},
            b"",
        ),
    )
    try:
        assert webapp.request_session_files_disk_cache_prune("first") is True
        assert webapp.request_session_files_disk_cache_prune("duplicate") is False
        assert webapp.session_files_service.disk_prune_record.running is False
        assert webapp.session_files_service.disk_prune_record.next_at == 100.0 + app_module.SESSION_FILES_DISK_CACHE_PRUNE_INTERVAL_SECONDS
        assert webapp.session_files_service.disk_prune_record.last_result == {"submitted": True, "reason": "first", "job_id": "prune-1"}
        assert webapp.request_session_files_disk_cache_prune("too-early") is False
        now[0] = webapp.session_files_service.disk_prune_record.next_at
        assert webapp.request_session_files_disk_cache_prune("due") is True
        assert len(submissions) == 2
    finally:
        webapp.control_server.stop()


def test_session_files_disk_prune_record_clears_running_after_failure(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(webapp, "prune_session_files_disk_cache", lambda: (_ for _ in ()).throw(OSError("disk failed")))
    webapp.session_files_service.disk_prune_record.running = True
    try:
        webapp.run_session_files_disk_cache_prune()
    finally:
        webapp.control_server.stop()

    assert webapp.session_files_service.disk_prune_record.running is False
    assert webapp.session_files_service.disk_prune_record.last_result == {"error": "disk failed"}


def test_session_files_disk_prune_submits_to_jobd_without_a_web_worker_thread(monkeypatch):
    submissions = []
    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = SimpleNamespace(
        produce=lambda *args, **kwargs: submissions.append((args, kwargs)) or (
            {"ok": True, "job": {"job_id": "prune-1", "status": "queued"}},
            b"",
        ),
    )
    monkeypatch.setattr(
        app_module.threading,
        "Thread",
        lambda **_kwargs: pytest.fail("session-files cache prune must not create a web-process thread"),
    )
    try:
        assert webapp.request_session_files_disk_cache_prune("write") is True
    finally:
        webapp.control_server.stop()

    assert len(submissions) == 1
    args, kwargs = submissions[0]
    assert args[0] == "session_files_cache_prune"
    assert args[1] == {
        "cache_dir": str(app_module.SESSION_FILES_CACHE_DIR),
        "max_age_seconds": app_module.SESSION_FILES_DISK_CACHE_MAX_AGE_SECONDS,
        "max_bytes": app_module.SESSION_FILES_DISK_CACHE_MAX_BYTES,
        "batch_size": app_module.SESSION_FILES_DISK_CACHE_PRUNE_BATCH_SIZE,
    }
    assert kwargs == {
        "priority": "maintenance",
        "generation": 1,
        "coalesce_key": "session-files-cache-prune",
        "delivery": "receipt",
    }


def test_session_files_disk_prune_parallel_state_is_retired():
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert "self.session_files_disk_prune_next_at" not in source
    assert "self.session_files_disk_prune_running" not in source
    assert "self.session_files_disk_prune_last_result" not in source


def test_record_owned_threads_rollback_failed_start_and_retry(monkeypatch, tmp_path):
    fail_next = [False]

    class FakeThread:
        def __init__(self, *, target, args=(), kwargs=None, name=None, daemon=None):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}
            self.name = name or getattr(target, "__name__", "worker")
            self.daemon = daemon
            self.started = False

        def start(self):
            if fail_next[0]:
                fail_next[0] = False
                raise RuntimeError(f"start failed: {self.name}")
            self.started = True

        def is_alive(self):
            return self.started

        def join(self, timeout=None):
            return None

    def fail_once(call):
        fail_next[0] = True
        with pytest.raises(RuntimeError, match="start failed"):
            call()

    webapp = app_module.TmuxWebtermApp([])
    signal_starts = []
    signal_stops = []
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(webapp, "background_can_run", lambda _role: True)
    monkeypatch.setattr(webapp, "start_tmux_signal_event_watcher", lambda: signal_starts.append(True))
    monkeypatch.setattr(webapp, "stop_tmux_signal_event_watcher", lambda: signal_stops.append(True))
    monkeypatch.setattr(webapp, "publish_yoagent_conversation_changed", lambda reason: None)
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "codex", "invocation": "cli"})
    monkeypatch.setattr(app_module, "resolve_yoagent_backend", lambda _backend: "codex")
    try:
        fail_once(webapp.start_client_event_watcher)
        assert webapp.client_watch_service.event_watcher_record.worker is None
        assert len(signal_starts) == 1 and len(signal_stops) == 1
        webapp.start_client_event_watcher()
        assert webapp.client_watch_service.event_watcher_record.worker is not None

        fail_once(webapp.start_input_heartbeat_worker)
        assert webapp.input_heartbeat_record.worker is None
        assert webapp.input_heartbeat_record.stop_requested is True
        webapp.start_input_heartbeat_worker()
        assert webapp.input_heartbeat_record.worker is not None

        fail_once(webapp.start_tabber_activity_cache_warmer)
        assert webapp.activity_transcript_service.tabber_warmer_record.running is False
        assert webapp.activity_transcript_service.tabber_warmer_record.thread is None
        assert webapp.start_tabber_activity_cache_warmer() is True

        fail_once(lambda: webapp.warm_metadata_cache_async({}))
        assert webapp.metadata_warm_record.worker is None
        webapp.warm_metadata_cache_async({})
        assert webapp.metadata_warm_record.worker is not None

        root_index = app_module.file_index.RootIndex(tmp_path)
        fail_once(lambda: app_module.file_index._start_build(root_index, set()))
        assert root_index.building is False
        assert root_index.thread is None
        app_module.file_index._start_build(root_index, set())
        assert root_index.building is True
        assert root_index.thread is not None

        fail_once(lambda: webapp.yoagent_controller.start_yoagent_action_result_watcher({"session": "1"}, {}))
        assert webapp.yoagent_action_waits == {}
        watch = webapp.yoagent_controller.start_yoagent_action_result_watcher({"session": "1"}, {})
        assert watch["started"] is True and watch["id"] in webapp.yoagent_action_waits

        fail_once(webapp.yoagent_controller.start_yoagent_backend_prewarm)
        assert webapp.yoagent_prewarm_record.prewarm_running is False
        assert webapp.yoagent_prewarm_record.prewarm_worker is None
        prewarm, status = webapp.yoagent_controller.start_yoagent_backend_prewarm()
        assert status == HTTPStatus.ACCEPTED and prewarm["started"] is True
        assert webapp.yoagent_prewarm_record.prewarm_worker is not None
    finally:
        webapp.control_server.stop()


def test_cache_hash_helpers_reuse_client_event_payload_signature(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    calls = []

    def fake_signature(payload):
        calls.append(payload)
        return f"encoded-{len(calls)}"

    def fake_merge_attention_acks():
        with webapp.client_watch_service.lock:
            webapp.client_watch_service.attention_ack_rev = 7

    webapp.client_event_payload_signature = fake_signature
    webapp.merge_shared_attention_acks = fake_merge_attention_acks
    try:
        cache_key = ("payload", app_module.SESSION_FILES_CACHE_KEY_VERSION, "5", 24.0, "", "", (), ("infos-sig",), ("repo-sigs",))
        _path, disk_signature = webapp.session_files_disk_cache_path(cache_key)
        payload_signature = webapp.session_files_payload_signature({"files": [{"path": "same.py"}]})
        tabber_signature = webapp.tabber_activity_source_signature()
    finally:
        webapp.control_server.stop()

    assert disk_signature == hashlib.sha256(b"encoded-1").hexdigest()
    assert payload_signature == hashlib.sha256(b"encoded-2").hexdigest()
    assert tabber_signature == hashlib.sha256(b"encoded-4").hexdigest()
    # The durable path hashes ONLY the stable logical view key; the volatile
    # info/repo signatures (last two elements) are the replaceable generation.
    assert calls[0] == cache_key[:-2]
    assert calls[1] == {"files": [{"path": "same.py"}]}
    assert calls[3] == {"scope": "configured", "sessions": [("5", None)], "attention_ack_rev": 7, "tmux_signature": "encoded-3"}


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

        background_payload = webapp.update_client_watch_roots({"background_files": ["/repo/DOIT.51.md"]})
        assert background_payload["files"] == []
        assert background_payload["background_files"] == ["/repo/DOIT.51.md"]
        assert webapp.client_watch_files_snapshot() == []
        assert webapp.client_watch_background_files_snapshot() == ["/repo/DOIT.51.md"]

        active_payload = webapp.update_client_watch_roots({
            "files": ["/repo/DOIT.51.md"],
            "background_files": ["/repo/DOIT.51.md"],
        })
        assert active_payload["files"] == ["/repo/DOIT.51.md"]
        assert active_payload["background_files"] == []
        assert webapp.client_watch_files_snapshot() == ["/repo/DOIT.51.md"]
        assert webapp.client_watch_background_files_snapshot() == []

        monkeypatch.setattr(app_module.time, "monotonic", lambda: 1000.0)
        monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
        assert webapp.client_watch_roots_snapshot() == []
        assert webapp.client_watch_files_snapshot() == []
        assert webapp.client_watch_background_files_snapshot() == []
    finally:
        webapp.control_server.stop()


def test_unchanged_client_watch_descriptor_refreshes_ttl_without_restarting_snapshot_work(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    wakes = []
    snapshots = []
    monkeypatch.setattr(webapp, "wake_client_event_watcher", lambda: wakes.append("wake"))
    monkeypatch.setattr(webapp, "start_client_watch_snapshot_publish", lambda: snapshots.append("snapshot") or True)
    subscriber, _queue = webapp.client_events.subscribe(channels="files", client_id="browser-a")
    descriptor = {"client_id": "browser-a", "roots": ["/repo"], "files": ["/repo/open.py"]}
    try:
        webapp.update_client_watch_roots(descriptor)
        webapp.update_client_watch_roots(descriptor)
        webapp.update_client_watch_roots({**descriptor, "roots": ["/repo-next"]})

        assert wakes == ["wake", "wake"]
        assert snapshots == ["snapshot", "snapshot"]
        assert webapp.client_watch_service.descriptors["browser-a"].descriptor_generation == 2
    finally:
        webapp.client_events.unsubscribe(subscriber)
        webapp.control_server.stop()


def test_client_watch_descriptors_union_contract_and_release_on_final_sse_disconnect(monkeypatch, tmp_path):
    """Two browser identities must never replace one another's Finder demand."""
    monkeypatch.setattr(app_module, "WATCH_INDEX_PATH", tmp_path / "watch-index.json")
    monotonic = [100.0]
    monkeypatch.setattr(app_module.time, "monotonic", lambda: monotonic[0])
    monkeypatch.setattr(app_module.time, "time", lambda: monotonic[0])
    webapp = app_module.TmuxWebtermApp([])
    first, _ = webapp.client_events.subscribe(channels="files", client_id="browser-a")
    second, _ = webapp.client_events.subscribe(channels="files", client_id="browser-a")
    other, _ = webapp.client_events.subscribe(channels="files", client_id="browser-b")
    try:
        assert webapp.update_client_watch_roots({
            "client_id": "browser-a", "roots": ["/repo/a"], "files": ["/repo/a/open.py"],
        })["mode"] == "lifecycle"
        assert webapp.update_client_watch_roots({
            "client_id": "browser-b", "roots": ["/repo/b"], "background_files": ["/repo/b/dirty.py"],
        })["mode"] == "lifecycle"
        assert webapp.client_watch_roots_snapshot() == ["/repo/a", "/repo/b"]
        assert webapp.client_watch_files_snapshot() == ["/repo/a/open.py"]
        assert webapp.client_watch_background_files_snapshot() == ["/repo/b/dirty.py"]

        # A pane contraction affects only its descriptor, not browser-b's demand.
        webapp.update_client_watch_roots({"client_id": "browser-a", "roots": ["/repo/a-next"]})
        assert webapp.client_watch_roots_snapshot() == ["/repo/a-next", "/repo/b"]
        assert webapp.client_watch_files_snapshot() == []
        assert webapp.client_watch_background_files_snapshot() == ["/repo/b/dirty.py"]

        webapp.client_events.unsubscribe(first)
        webapp.client_event_subscriber_disconnected("browser-a")
        assert webapp.client_watch_roots_snapshot() == ["/repo/a-next", "/repo/b"], "one reconnecting same-id stream retains demand"
        webapp.client_events.unsubscribe(second)
        webapp.client_event_subscriber_disconnected("browser-a")
        assert webapp.client_watch_roots_snapshot() == ["/repo/b"]
        assert webapp.client_watch_background_files_snapshot() == ["/repo/b/dirty.py"]

        # A dead legacy/no-stream descriptor is the bounded orphan fallback.
        webapp.update_client_watch_roots({"roots": ["/repo/legacy"]})
        monotonic[0] += app_module.CLIENT_WATCH_ROOT_TTL_SECONDS / 2 + 1
        webapp.touch_client_watch_descriptor("browser-b")
        monotonic[0] += app_module.CLIENT_WATCH_ROOT_TTL_SECONDS / 2 + 1
        assert webapp.client_watch_roots_snapshot() == ["/repo/b"]
    finally:
        webapp.client_events.unsubscribe(other)
        webapp.control_server.stop()


def test_client_watch_file_records_preserve_limits_order_and_exclusive_modes(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(app_module.time, "monotonic", lambda: 100.0)
    try:
        active_input = [f"/repo/active-{index:03d}" for index in reversed(range(app_module.CLIENT_WATCH_FILE_LIMIT + 3))]
        background_input = [f"/repo/background-{index:03d}" for index in reversed(range(app_module.CLIENT_WATCH_FILE_LIMIT + 3))]
        background_input.append("/repo/active-000")

        payload = webapp.update_client_watch_roots({"files": active_input, "background_files": background_input})
        expected_active = sorted(active_input)[:app_module.CLIENT_WATCH_FILE_LIMIT]
        expected_background = [
            path
            for path in sorted(set(background_input))
            if path not in set(expected_active)
        ][:app_module.CLIENT_WATCH_FILE_LIMIT]

        assert payload["files"] == expected_active
        assert payload["background_files"] == expected_background
        assert webapp.client_watch_files_snapshot() == expected_active
        assert webapp.client_watch_background_files_snapshot() == expected_background
        assert set(webapp.client_watch_files_snapshot()).isdisjoint(webapp.client_watch_background_files_snapshot())
    finally:
        webapp.control_server.stop()


def test_client_watch_file_parallel_state_maps_are_retired():
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert "self.client_watch_files:" not in source
    assert "self.client_watch_files =" not in source
    assert "self.client_watch_files." not in source
    assert "self.client_watch_background_files:" not in source
    assert "self.client_watch_background_files =" not in source
    assert "self.client_watch_background_files." not in source


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
        owner_files = sorted(app1.watch_root_index.owner_dir.glob("*.json"))
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
    changed_paths = app_module.filesystem_changed_paths(previous, current)

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
    assert changed_paths == [
        "/new-root",
        "/repo/mod.txt",
        "/repo/new-dir",
        "/repo/new.txt",
        "/repo/old-dir",
        "/repo/old.txt",
    ]



def test_publish_filesystem_ready_event_sends_initial_diff_then_keyframe(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    submitted_roots = []
    signatures = [
        (("/repo", ("/repo", "dir", 100, 0, (("one.txt", "file", 100, 10),))),),
        (("/repo", ("/repo", "dir", 200, 0, (("two.txt", "file", 200, 20),))),),
        (("/repo", ("/repo", "dir", 300, 0, (("three.txt", "file", 300, 30),))),),
    ]

    class ReadyBatchJob:
        def produce(self, task, payload, **kwargs):
            assert task == "filesystem_batch"
            signature = signatures[0 if not submitted_roots else 2][0][1]
            submitted_roots.append([request["path"] for request in payload["requests"]])
            responses = [
                {
                    "id": request["id"],
                    "ok": True,
                    "status": 200,
                    "payload": {"path": request["path"], "entries": []},
                    "watch_signature": list(signature),
                }
                for request in payload["requests"]
            ]
            return {
                "ok": True,
                "state": "ready",
                "job": {"job_id": f"job-{len(submitted_roots)}", "status": "completed", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": kwargs["generation"]},
            }, json.dumps({"responses": responses, "performance": {"operation_ms": 1.0}}).encode("utf-8")

    webapp.job_client = ReadyBatchJob()
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.publish_filesystem_ready_event(["/repo"], current_signature=signatures[0]) == ["fs_changed"]
        assert webapp.publish_filesystem_ready_event(["/repo"], current_signature=signatures[1]) == ["fs_changed"]
        webapp.client_watch_service.filesystem_last_full_at = time.monotonic() - app_module.FILESYSTEM_WATCH_KEYFRAME_SECONDS - 1.0
        assert webapp.publish_filesystem_ready_event(["/repo"], current_signature=signatures[2]) == ["fs_changed"]
    finally:
        webapp.control_server.stop()

    assert [payload["mode"] for _event_type, payload in events] == ["full", "diff", "full"]
    assert events[0][1]["directories"]
    assert events[1][1]["refresh"] is True
    assert "directories" not in events[1][1]
    assert events[2][1]["directories"]
    assert submitted_roots == [["/repo"], ["/repo"]]


def test_filesystem_watch_signature_for_roots_matches_watch_batch_signature(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "watched.txt").write_text("value\n", encoding="utf-8")
    submitted = []

    class ReadyBatchJob:
        def produce(self, task, payload, **kwargs):
            submitted.append((task, copy.deepcopy(payload), dict(kwargs)))
            product = app_module.filesystem.filesystem_batch_result(payload)
            return {
                "ok": True,
                "state": "ready",
                "job": {"job_id": "", "status": "completed", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": kwargs["generation"]},
            }, json.dumps(product).encode("utf-8")

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = ReadyBatchJob()
    payload = {
        "client_scope": "browser",
        "requests": [{
            "id": "root-list",
            "type": "list",
            "path": str(root),
            "trigger_counts": {"watch-diff": 1},
            "include_watch_signature": True,
        }],
    }
    try:
        batch, status = webapp.fs_batch_http_payload(payload)
        assert status == HTTPStatus.OK, batch
        response = batch["responses"][0]
        assert response["ok"] is True, response
        assert webapp.filesystem_watch_signature_for_roots([str(root)]) == ((
            str(root),
            app_module.immutable_watch_signature(response["watch_signature"]),
        ),)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()
    assert submitted[0][0] == "filesystem_batch"
    assert submitted[0][1]["requests"][0]["include_watch_signature"] is True


def test_filesystem_watch_diff_plan_lists_only_changed_roots():
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
    try:
        since = webapp.record_filesystem_watch_snapshot(previous)
        current_token = webapp.record_filesystem_watch_snapshot(current)
        payload, roots = webapp.filesystem_watch_diff_plan(since)
    finally:
        webapp.control_server.stop()

    assert payload["mode"] == "diff"
    assert payload["since"] == since
    assert payload["token"] == current_token
    assert roots == ["/added-root", "/repo"]
    assert payload["change_summary"]["roots_changed"] == 2


def test_filesystem_watch_diff_request_submits_bounded_jobd_batches_and_completes_via_operation(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    current = tuple(
        (f"/repo-{index:03d}", (f"/repo-{index:03d}", "dir", index + 1, 0, ()))
        for index in range(64)
    )
    submitted = []
    product_payloads = {}

    class CompletingBatchJob:
        def produce(self, task, payload, **kwargs):
            submitted.append((task, payload, kwargs))
            product_payloads[kwargs["coalesce_key"]] = payload
            index = len(submitted)
            return {
                "ok": True,
                "state": "queued",
                "job": {"job_id": f"job-{index}", "status": "queued", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

        def product(self, product_key):
            requests = product_payloads[product_key]["requests"]
            responses = [
                {
                    "id": request["id"],
                    "ok": True,
                    "status": 200,
                    "payload": {"path": request["path"], "entries": []},
                    "watch_signature": [request["path"], "dir", index + 1, 0, []],
                }
                for index, request in enumerate(requests)
            ]
            return {
                "ok": True,
                "state": "ready",
                "generation": 1,
                "inflight": False,
            }, json.dumps({"responses": responses, "performance": {"operation_ms": 1.0}}).encode("utf-8")

    terminal = threading.Event()
    published = []
    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = CompletingBatchJob()

    def capture_event(event_type, payload=None, **kwargs):
        published.append((event_type, payload, kwargs))
        if event_type == "operation_terminal":
            terminal.set()

    monkeypatch.setattr(webapp, "publish_client_event", capture_event)
    monkeypatch.setattr(
        app_module.filesystem,
        "list_directory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("watch-diff request must not list in web")),
    )
    try:
        token = webapp.record_filesystem_watch_snapshot(current)
        payload, status = webapp.filesystem_watch_diff_http_payload(
            since_token="missing",
            request_id="r-web-watch-diff",
        )
        operation_id = payload["operation"]["id"]
        assert terminal.wait(2.0), "accepted watch-diff product did not publish terminal completion"
        result, result_status = webapp.operation_status_payload(operation_id)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert status == HTTPStatus.ACCEPTED
    assert payload["state"] == "queued"
    assert payload["request"]["id"] == "r-web-watch-diff"
    assert payload["operation"]["kind"] == "fs_watch_diff"
    assert payload["operation"]["context"]["token"] == token
    assert [len(call[1]["requests"]) for call in submitted] == [64]
    assert all(call[0] == "filesystem_batch" for call in submitted)
    assert all(call[2]["delivery"] == "ready_or_receipt" for call in submitted)
    assert result_status == HTTPStatus.OK
    assert result["state"] == "ready"
    assert [directory["path"] for directory in result["data"]["directories"]] == [item[0] for item in current]
    assert [response["id"] for response in result["data"]["responses"]] == list(range(64))
    assert result["data"]["listing_summary"]["roots_listed"] == 64
    assert result["data"]["token"]
    assert webapp.filesystem_watch_record_for_token(result["data"]["token"])["signature"] == current
    assert published[-1][0] == "operation_terminal"
    assert published[-1][1]["operation"]["id"] == operation_id


def test_filesystem_watch_diff_warm_calls_return_ready_without_another_jobd_rpc(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    current = (("/repo", ("/repo", "dir", 1, 0, ())),)
    product_body = json.dumps({
        "responses": [{
            "id": 0,
            "ok": True,
            "status": 200,
            "payload": {"path": "/repo", "entries": []},
            "watch_signature": ["/repo", "dir", 1, 0, []],
        }],
        "performance": {"operation_ms": 1.0},
    }).encode("utf-8")
    submissions = []
    produce_started = threading.Event()

    class WarmBatchJob:
        def produce(self, task, payload, **kwargs):
            submissions.append((task, payload, kwargs))
            produce_started.set()
            return {
                "ok": True,
                "state": "queued",
                "job": {"job_id": "job-watch", "status": "queued", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

        def product(self, product_key):
            assert product_key == submissions[0][2]["coalesce_key"]
            return {"ok": True, "state": "ready", "generation": 1, "inflight": False}, product_body

    terminal = threading.Event()
    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = WarmBatchJob()
    accept_operation = webapp.queued_delivery_ledger.accept_operation

    def accept_after_producer_started(**kwargs):
        assert produce_started.wait(0.5), "cold producer did not overlap durable receipt persistence"
        return accept_operation(**kwargs)

    monkeypatch.setattr(webapp.queued_delivery_ledger, "accept_operation", accept_after_producer_started)

    def capture_event(event_type, _payload=None, **_kwargs):
        if event_type == "operation_terminal":
            terminal.set()

    monkeypatch.setattr(webapp, "publish_client_event", capture_event)
    try:
        webapp.record_filesystem_watch_snapshot(current)
        first, first_status = webapp.filesystem_watch_diff_http_payload(
            since_token="missing",
            request_id="r-watch-cold",
        )
        second, second_status = webapp.filesystem_watch_diff_http_payload(
            since_token="missing",
            request_id="r-watch-warm-1",
        )
        third, third_status = webapp.filesystem_watch_diff_http_payload(
            since_token="missing",
            request_id="r-watch-warm-2",
        )
        assert terminal.wait(2.0), "cold watch-diff operation did not reach its terminal product"
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert first_status == HTTPStatus.ACCEPTED
    assert first["state"] == "queued"
    assert [first_status, second_status, third_status] == [HTTPStatus.ACCEPTED, HTTPStatus.OK, HTTPStatus.OK]
    assert [second["mode"], third["mode"]] == ["full", "full"]
    assert [response["id"] for response in second["responses"]] == [0]
    assert [response["id"] for response in third["responses"]] == [0]
    assert len(submissions) == 1
    assert submissions[0][2]["delivery"] == "ready_or_receipt"


def test_filesystem_watch_diff_async_submit_failure_terminalizes_the_accepted_receipt(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    terminal = threading.Event()
    published = []

    class FailingBatchJob:
        def produce(self, *_args, **_kwargs):
            return {
                "ok": False,
                "error": "jobd response exceeded deadline",
                "_transport_error": "timeout",
            }, b""

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = FailingBatchJob()
    monkeypatch.setattr(webapp, "client_watch_roots_snapshot", lambda: ["/repo"])

    def capture_event(event_type, payload=None, **kwargs):
        published.append((event_type, payload, kwargs))
        if event_type == "operation_terminal":
            terminal.set()

    monkeypatch.setattr(webapp, "publish_client_event", capture_event)
    try:
        receipt, status = webapp.filesystem_watch_diff_http_payload(
            force_full=True,
            request_id="r-web-watch-submit-failure",
        )
        operation_id = receipt["operation"]["id"]
        assert terminal.wait(2.0), "failed watch-diff submit did not publish terminal completion"
        result, result_status = webapp.operation_status_payload(operation_id)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert status == HTTPStatus.ACCEPTED
    assert receipt["request"]["id"] == "r-web-watch-submit-failure"
    assert result_status == HTTPStatus.SERVICE_UNAVAILABLE
    assert result["state"] == "failed"
    assert result["request"]["id"] == "r-web-watch-submit-failure"
    assert result["error"]["details"]["reason"] == "timeout"
    assert result["error"]["stack"][-1]["operation"] == "jobd.produce"
    assert published[-1][1]["operation"]["id"] == operation_id


def test_filesystem_watch_diff_force_full_acceptance_does_not_submit_refresh_or_scan_in_request_thread(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    roots = ["/repo-a", "/repo-b"]
    submitted = []

    class PendingBatchJob:
        def produce(self, task, payload, **kwargs):
            submitted.append((task, payload, kwargs))
            return {
                "ok": True,
                "state": "queued",
                "job": {"job_id": "job-watch", "status": "queued", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

    class CapturingCompletionService:
        stop_event = threading.Event()

        def reserve(self):
            return True

        def release_reservation(self):
            raise AssertionError("accepted operation must retain its completion reservation")

        def submit_reserved(self, function, *args):
            self.submission = (function, args)
            return True

        def stop(self):
            self.stop_event.set()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("forced watch-diff acceptance must not refresh or scan in web")

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = PendingBatchJob()
    webapp.jobd_operation_service = CapturingCompletionService()
    monkeypatch.setattr(webapp, "client_watch_roots_snapshot", lambda: roots)
    monkeypatch.setattr(app_module, "discover_sessions", forbidden)
    monkeypatch.setattr(app_module.filesystem, "watch_signature", forbidden)
    monkeypatch.setattr(app_module.filesystem, "list_directory", forbidden)
    try:
        payload, status = webapp.filesystem_watch_diff_http_payload(
            force_full=True,
            request_id="r-web-force-full",
        )
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert status == HTTPStatus.ACCEPTED
    assert payload["request"]["id"] == "r-web-force-full"
    assert payload["operation"]["progress"]["phase"] == "refreshing_snapshot"
    assert payload["operation"]["progress"]["producer_state"] == "submitting"
    assert submitted == []
    completion, completion_args = webapp.jobd_operation_service.submission
    assert completion == webapp.complete_filesystem_watch_diff_operation
    assert completion_args[2] == {"mode": "full", "reason": "forced", "token": "", "removed_roots": []}
    assert completion_args[3] == roots


def test_filesystem_watch_diff_completion_worker_start_failure_is_a_produce_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")

    class RejectingCompletionService:
        stop_event = threading.Event()

        def reserve(self):
            return True

        def release_reservation(self):
            raise AssertionError("submit_reserved owns the accepted reservation")

        def submit_reserved(self, _function, *_args):
            return False

        def stop(self):
            self.stop_event.set()

    webapp = app_module.TmuxWebtermApp([])
    webapp.jobd_operation_service = RejectingCompletionService()
    monkeypatch.setattr(webapp, "client_watch_roots_snapshot", lambda: ["/repo"])
    try:
        result, status = webapp.filesystem_watch_diff_http_payload(
            force_full=True,
            request_id="r-web-worker-start",
        )
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert status == HTTPStatus.SERVICE_UNAVAILABLE
    assert result["state"] == "failed"
    assert result["request"]["id"] == "r-web-worker-start"
    assert result["error"]["stack"][-1]["operation"] == "jobd.produce"


def test_filesystem_watch_diff_rejects_105_roots_without_partial_or_multi_job_submission(monkeypatch):
    roots = [f"/repo-{index:03d}" for index in range(105)]
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(webapp, "client_watch_roots_snapshot", lambda: roots)
    monkeypatch.setattr(
        webapp.job_client,
        "produce",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("over-limit request must not submit partial jobs")),
    )
    try:
        payload, status = webapp.filesystem_watch_diff_http_payload(
            force_full=True,
            request_id="r-web-105-roots",
        )
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.BAD_REQUEST
    assert payload["state"] == "failed"
    assert payload["request"]["id"] == "r-web-105-roots"
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["details"]["roots"] == 105
    assert payload["error"]["details"]["maximum"] == 64


def test_filesystem_watch_diff_releases_completion_reservation_when_operation_acceptance_fails(monkeypatch):
    current = (("/repo", ("/repo", "dir", 1, 0, ())),)

    class TrackingCompletionService:
        stop_event = threading.Event()

        def __init__(self):
            self.reservations = 0
            self.workers = []

        def reserve(self):
            self.reservations += 1
            return True

        def release_reservation(self):
            self.reservations -= 1

        def submit_reserved(self, function, *args):
            def run():
                try:
                    function(*args)
                finally:
                    self.release_reservation()

            worker = threading.Thread(target=run)
            self.workers.append(worker)
            worker.start()
            return True

        def stop(self):
            self.stop_event.set()
            for worker in self.workers:
                worker.join(timeout=2.0)

    pending_batch = app_module.FilesystemWatchBatchProduct(
        producer=app_module.JobdProductOperation(job_id="job-1", product_key="fs-watch:test", generation=1),
        ready_product={"responses": []},
    )
    completion_service = TrackingCompletionService()
    webapp = app_module.TmuxWebtermApp([])
    webapp.jobd_operation_service = completion_service
    monkeypatch.setattr(
        webapp,
        "submit_filesystem_watch_batches",
        lambda _roots, _token, **_kwargs: (pending_batch,),
    )

    def reject_acceptance(**_kwargs):
        raise RuntimeError("ledger write failed")

    monkeypatch.setattr(webapp.queued_delivery_ledger, "accept_operation", reject_acceptance)
    try:
        webapp.record_filesystem_watch_snapshot(current)
        with pytest.raises(RuntimeError, match="ledger write failed"):
            webapp.filesystem_watch_diff_http_payload(since_token="missing")
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert completion_service.reservations == 0


def test_periodic_filesystem_full_frame_submits_jobd_without_inline_listing(monkeypatch):
    current = tuple(
        (f"/repo-{index:03d}", (f"/repo-{index:03d}", "dir", index + 1, 0, ()))
        for index in range(64)
    )
    roots = [item[0] for item in current]
    submitted = []
    published = []

    class PendingBatchJob:
        def produce(self, task, payload, **kwargs):
            submitted.append((task, payload, kwargs))
            index = len(submitted)
            return {
                "ok": True,
                "state": "queued",
                "job": {"job_id": f"job-{index}", "status": "queued", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

    class CapturingCompletionService:
        stop_event = threading.Event()

        def reserve(self):
            return True

        def release_reservation(self):
            return None

        def submit_reserved(self, function, *args):
            self.submission = (function, args)
            return True

        def stop(self):
            self.stop_event.set()

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = PendingBatchJob()
    webapp.jobd_operation_service = CapturingCompletionService()
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: published.append((event_type, payload or {})))
    monkeypatch.setattr(
        app_module.filesystem,
        "list_directory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("periodic full frame must not list in web")),
    )
    try:
        events = webapp.publish_filesystem_ready_event(roots, current_signature=current, force_full=True)
        repeated_events = webapp.publish_filesystem_ready_event(roots, current_signature=current, force_full=True)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert events == []
    assert repeated_events == []
    assert published == []
    assert [len(call[1]["requests"]) for call in submitted] == [64]
    assert all(call[0] == "filesystem_batch" for call in submitted)
    assert all(call[2]["delivery"] == "ready_or_receipt" for call in submitted)


def test_completed_filesystem_full_frame_publishes_latest_token_and_discards_stale_token(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    published = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: published.append((event_type, payload or {})))
    first_signature = (("/repo", ("/repo", "dir", 1, 0, ())),)
    second_signature = (("/repo", ("/repo", "dir", 2, 0, ())),)
    try:
        first_token = webapp.record_filesystem_watch_snapshot(first_signature)
        with webapp.client_watch_service.lock:
            webapp.client_watch_service.filesystem_full_inflight_token = first_token
            webapp.client_watch_service.filesystem_payload_signature = "previous-token"
        first_payload = {"mode": "full", "token": first_token, "roots": ["/repo"], "directories": []}
        assert webapp.publish_completed_filesystem_full_payload(first_token, "watch", first_payload) == ["fs_changed"]
        with webapp.client_watch_service.lock:
            first_full_at = webapp.client_watch_service.filesystem_last_full_at
            assert webapp.client_watch_service.filesystem_payload_signature == first_token
            assert webapp.client_watch_service.filesystem_full_inflight_token == ""

        second_token = webapp.record_filesystem_watch_snapshot(second_signature)
        with webapp.client_watch_service.lock:
            webapp.client_watch_service.filesystem_full_inflight_token = first_token
            webapp.client_watch_service.filesystem_payload_signature = second_token
        assert webapp.publish_completed_filesystem_full_payload(first_token, "watch", first_payload) == []
        with webapp.client_watch_service.lock:
            assert webapp.client_watch_service.filesystem_payload_signature == second_token
            assert webapp.client_watch_service.filesystem_full_inflight_token == ""
            assert webapp.client_watch_service.filesystem_last_full_at == first_full_at
    finally:
        webapp.control_server.stop()

    assert published == [("fs_changed", first_payload)]


def test_periodic_filesystem_full_frame_shutdown_clears_inflight_without_error(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    token = webapp.record_filesystem_watch_snapshot((("/repo", ("/repo", "dir", 1, 0, ())),))
    batch = app_module.FilesystemWatchBatchProduct(
        producer=app_module.JobdProductOperation(job_id="job-1", product_key="fs-watch:test", generation=1),
    )
    failures = []
    monkeypatch.setattr(webapp, "record_filesystem_watch_product_failure", lambda error, trigger: failures.append((error, trigger)))
    with webapp.client_watch_service.lock:
        webapp.client_watch_service.filesystem_full_inflight_token = token
    webapp.jobd_operation_service.stop_event.set()
    try:
        webapp.complete_filesystem_ready_event(
            token,
            "watch",
            {"mode": "full", "token": token, "removed_roots": []},
            ["/repo"],
            (batch,),
            time.time() + 1.0,
        )
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert failures == []
    assert webapp.client_watch_service.filesystem_full_inflight_token == ""


def test_filesystem_watch_diff_plan_returns_full_when_since_is_stale():
    webapp = app_module.TmuxWebtermApp([])
    current = (
        ("/repo", ("/repo", "dir", 200, 0, (("new.txt", "file", 100, 10),))),
        ("/unchanged", ("/unchanged", "dir", 100, 0, (("same.txt", "file", 100, 10),))),
    )
    try:
        token = webapp.record_filesystem_watch_snapshot(current)
        payload, roots = webapp.filesystem_watch_diff_plan("missing-token")
    finally:
        webapp.control_server.stop()

    assert payload["mode"] == "full"
    assert payload["reason"] == "stale-since"
    assert payload["token"] == token
    assert roots == ["/repo", "/unchanged"]


def test_session_files_ready_skips_unchanged_fs_republish(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    events = []
    requests = [{"session": "5", "hours": 24}]
    monkeypatch.setattr(webapp.client_watch_service, "snapshot", lambda: ([], requests, {}))
    monkeypatch.setattr(webapp, "session_files_payload", lambda *args, **kwargs: ({"files": [{"path": "/repo/a.py"}], "repos": [], "errors": [], "cache": {"age": time.time()}}, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.publish_session_files_ready_events(trigger="fs_changed") == ["session_files_ready"]
        assert webapp.publish_session_files_ready_events(trigger="fs_changed") == []
    finally:
        webapp.control_server.stop()

    assert [event_type for event_type, _payload in events] == ["session_files_ready"]



def test_publish_context_items_ready_events_on_transcript_watch_routes_through_jobd(monkeypatch, tmp_path):
    # Checkbox 8: transcript-identity watch events must invalidate the typed jobd transcript_view
    # product, not parse inline. watchd revision handling calls
    # publish_context_items_ready_events, which reaches transcript_compact_view_bounded and the
    # existing jobd owner; prove that wiring holds for the transcript-watch trigger path.
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "watched"}}) + "\n", encoding="utf-8")
    info = _single_agent_session_info("5", transcript, tmp_path)
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module, "tail_file_lines", lambda *a, **k: (_ for _ in ()).throw(AssertionError("transcript-watch-triggered refresh must not parse inline")))
    submitted_tasks = []

    class TrackingJobClient:
        def produce(self, task, payload, **kwargs):
            submitted_tasks.append(task)
            return {
                "ok": True,
                "state": "queued",
                "coalesced": False,
                "job": {"job_id": "job-1", "status": "running"},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

        def result(self, job_id):
            return {"ok": True, "job": {"status": "running"}}

        def product(self, coalesce_key, timeout=0.5):
            return {"ok": True, "state": "pending", "generation": 0}, b""

    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.job_client = TrackingJobClient()
    monkeypatch.setattr(webapp.client_watch_service, "snapshot", lambda: ([{"session": "5", "messages": 20}], [], []))
    published = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **kwargs: published.append(event_type))
    try:
        events = webapp.publish_context_items_ready_events(trigger="transcripts_changed")
    finally:
        webapp.control_server.stop()

    assert events == []
    assert published == []
    # A pending product remains owned by jobd and is not mislabeled as a ready push.
    assert submitted_tasks and set(submitted_tasks) == {"transcript_view"}


def test_publish_session_files_ready_events_on_fs_watch_routes_through_jobd_not_inline(monkeypatch):
    # Checkbox 8: filesystem/transcript watchd events must invalidate the typed jobd product,
    # not recompute inline. publish_session_files_ready_events uses cache-aware session_files_payload(),
    # which checkbox 9 already routes through compute_session_files_payload_via_jobd -- prove
    # that wiring holds for the fs-watch/transcript-watch trigger path specifically.
    info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": info}, []))

    def fail_inline(*_args, **_kwargs):
        raise AssertionError("fs-watch-triggered session-files refresh must not call inline compute")

    monkeypatch.setattr(app_module.session_files, "session_files_payload", fail_inline)
    monkeypatch.setattr(app_module.session_files, "session_files_payload_for_info", fail_inline)
    webapp = app_module.TmuxWebtermApp(["1"])
    calls = _install_fake_session_files_jobd(monkeypatch, webapp, {"session": "1", "files": [{"path": "watched.py"}], "repos": [], "errors": []})
    monkeypatch.setattr(webapp.client_watch_service, "snapshot", lambda: ([], [{"session": "1", "hours": 24.0, "from_ref": None, "to_ref": None, "repo_refs": None}], []))
    published = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **kwargs: published.append(event_type))
    try:
        events = webapp.publish_session_files_ready_events(trigger="fs_changed")
    finally:
        webapp.control_server.stop()

    assert events == ["session_files_ready"]
    assert published == ["session_files_ready"]
    assert calls == [("1", ("1",), 24.0, None, None, None)]  # jobd was submitted, inline never called
    # Checkbox 10: the trigger that drove this jobd-backed refresh is counted as a
    # dependency invalidation, bounded by trigger reason.
    assert webapp.client_watch_service.invalidation_counts == {"fs_changed": 1}


def test_publish_session_files_ready_events_keeps_watch_refresh_noninteractive(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    calls = []
    monkeypatch.setattr(webapp.client_watch_service, "snapshot", lambda: ([], [{"session": "1", "hours": 24.0}], []))
    monkeypatch.setattr(webapp, "session_files_payload", lambda *args, **kwargs: (calls.append((args, kwargs)) or ({"session": "1", "files": [], "repos": [], "errors": []}, HTTPStatus.OK)))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *_args, **_kwargs: None)
    try:
        webapp.publish_session_files_ready_events(trigger="fs_changed")
    finally:
        webapp.control_server.stop()

    assert calls == [(("1", 24.0), {"from_ref": None, "to_ref": None, "repo_refs": None, "force": False, "requester": "background-refresh"})]


def test_dependency_invalidation_counts_are_bounded_by_trigger_not_by_event_volume(monkeypatch):
    info_one = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
    info_two = SessionInfo(session="2", panes=[], selected_pane=None, agents=[])
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": info_one, "2": info_two}, []))

    def fail_inline(*_args, **_kwargs):
        raise AssertionError("must not call inline compute")

    monkeypatch.setattr(app_module.session_files, "session_files_payload", fail_inline)
    monkeypatch.setattr(app_module.session_files, "session_files_payload_for_info", fail_inline)
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    _install_fake_session_files_jobd(monkeypatch, webapp, lambda call: {"session": call[0], "hours": call[2], "files": [], "repos": [], "errors": []})
    # Use distinct hours per round so each round's cache_key is genuinely new -- the 30-second
    # in-process freshness window on the SAME key is an intentional anti-duplicate-work debounce
    # (compute_session_files_cache_entry), not something this counter test should fight.
    monkeypatch.setattr(webapp.client_watch_service, "snapshot", lambda: ([], [
        {"session": "1", "hours": 24.0, "from_ref": None, "to_ref": None, "repo_refs": None},
        {"session": "2", "hours": 24.0, "from_ref": None, "to_ref": None, "repo_refs": None},
    ], []))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: None)
    try:
        webapp.publish_session_files_ready_events(trigger="fs_changed")
        webapp.client_watch_service.snapshot = lambda: ([], [
            {"session": "1", "hours": 25.0, "from_ref": None, "to_ref": None, "repo_refs": None},
            {"session": "2", "hours": 25.0, "from_ref": None, "to_ref": None, "repo_refs": None},
        ], [])
        webapp.publish_session_files_ready_events(trigger="transcripts_changed")
    finally:
        webapp.control_server.stop()

    # Two sessions x two trigger calls = one entry per DISTINCT trigger reason, summed across
    # both sessions and both calls -- never one entry per event/session.
    assert webapp.client_watch_service.invalidation_counts == {"fs_changed": 2, "transcripts_changed": 2}



def test_context_items_uses_bounded_jobd_facts_without_request_time_local_parsing(monkeypatch, tmp_path):
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
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def unexpected_tail_file_lines(*_args, **_kwargs):
        raise AssertionError("context request must not parse transcript text in the web process")

    class CompletedTranscriptJob:
        def __init__(self):
            self.submissions = []

        def produce(self, task, payload, **kwargs):
            self.submissions.append((task, payload, kwargs))
            if len(self.submissions) == 1:
                return {
                    "ok": True,
                    "state": "queued",
                    "coalesced": False,
                    "job": {"job_id": "job-1", "status": "running"},
                    "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
                }, b""
            stat = transcript.stat()
            body = json.dumps({
                "read_generation": [stat.st_mtime_ns, stat.st_size],
                "generation": [stat.st_mtime_ns, stat.st_size],
                "identity": [stat.st_dev, stat.st_ino],
                "items": [{"role": "user", "timestamp": "", "cwd": "", "text": "Check latency"}],
                "compact_lines": [],
                "since_items": [],
                "since_stats": {},
            }).encode("utf-8")
            return {
                "ok": True,
                "state": "ready",
                "coalesced": True,
                "job": {"job_id": "job-1", "status": "completed", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": kwargs["generation"]},
            }, body

    monkeypatch.setattr(app_module, "tail_file_lines", unexpected_tail_file_lines)
    webapp = app_module.TmuxWebtermApp(["5"])
    worker = CompletedTranscriptJob()
    webapp.job_client = worker
    try:
        # Drive the single-shot core: the first request submits and returns pending without any
        # request-time parse; the second returns the bounded worker facts once jobd has completed.
        first, first_status = webapp.transcript_compact_view("5", 20)
        second, second_status = webapp.transcript_compact_view("5", 20)
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert first["pending"] is True
    assert first["stale"] is False
    assert first["items"] == []
    assert second["pending"] is False
    assert second["items"] == [{"role": "user", "timestamp": "", "cwd": "", "text": "Check latency"}]
    assert len(worker.submissions) == 2
    assert {submission[0] for submission in worker.submissions} == {"transcript_view"}
    # The coalesce key is byte-generation-stripped so appends supersede rather than re-key forever.
    coalesce_key = worker.submissions[0][2]["coalesce_key"]
    assert worker.submissions[1][2]["coalesce_key"] == coalesce_key
    assert coalesce_key.startswith(f"transcript:v{app_module.TRANSCRIPT_PARSER_GENERATION}:")
    assert str(transcript.stat().st_mtime_ns) not in coalesce_key


def _single_agent_session_info(session: str, transcript: Path, tmp_path: Path) -> SessionInfo:
    return SessionInfo(
        session=session,
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session=session,
                kind="codex",
                pid=123,
                pane_target=f"{session}:0.0",
                command="codex",
                cwd=str(tmp_path),
                status="running",
                session_id=f"session-{session}",
                transcript=str(transcript),
                error=None,
            )
        ],
    )


@pytest.mark.parametrize(
    ("method_name", "operation_kind"),
    (("context_tail", "context_tail"), ("context_items", "context_items")),
)
def test_context_http_boundaries_accept_one_jobd_product_without_request_thread_polling(
    monkeypatch,
    tmp_path,
    method_name,
    operation_kind,
):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "pending"}}) + "\n", encoding="utf-8")
    info = _single_agent_session_info("5", transcript, tmp_path)
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    request_thread = threading.current_thread().name
    actions = []

    class PendingProductJob:
        def produce(self, task, payload, **kwargs):
            actions.append(("produce", threading.current_thread().name))
            return {
                "ok": True,
                "state": "queued",
                "job": {"job_id": "job-1", "status": "running", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

        def result(self, job_id):
            actions.append(("result", threading.current_thread().name))
            return {"ok": True, "job": {"job_id": job_id, "status": "running"}}

        def submit(self, *_args, **_kwargs):
            actions.append(("submit", threading.current_thread().name))
            return {"ok": True, "job": {"job_id": "legacy-submit"}}

        def product(self, *_args, **_kwargs):
            actions.append(("product", threading.current_thread().name))
            return {"ok": True, "state": "pending", "generation": 0}, b""

    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.job_client = PendingProductJob()
    monkeypatch.setattr(
        app_module.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(AssertionError("context request must not sleep")),
    )
    try:
        method = webapp.context_tail if method_name == "context_tail" else webapp.context_items
        payload, status = method("5", 20)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert status == HTTPStatus.ACCEPTED
    assert payload["state"] == "queued"
    assert payload["request"]["id"].startswith("r-")
    assert payload["operation"]["id"].startswith("op-")
    assert payload["operation"]["kind"] == operation_kind
    assert payload["operation"]["context"] == {"messages": 20, "session": "5"}
    assert [action for action, thread_name in actions if thread_name == request_thread] == ["produce"]


def test_context_product_receipt_completes_through_operation_event_and_replay(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "complete me"}}) + "\n", encoding="utf-8")
    info = _single_agent_session_info("5", transcript, tmp_path)
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    stat = transcript.stat()

    class CompletingProductJob:
        def produce(self, task, payload, **kwargs):
            return {
                "ok": True,
                "state": "queued",
                "coalesced": False,
                "job": {"job_id": "job-context", "status": "running", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

        def result(self, job_id):
            assert job_id == "job-context"
            return {
                "ok": True,
                "job": {
                    "job_id": job_id,
                    "status": "completed",
                    "result": {
                        "generation": [stat.st_mtime_ns, stat.st_size],
                        "read_generation": [stat.st_mtime_ns, stat.st_size],
                        "identity": [stat.st_dev, stat.st_ino],
                        "items": [{"role": "user", "timestamp": "", "cwd": "", "text": "complete me"}],
                        "compact_lines": ["complete me"],
                        "since_items": [],
                        "since_stats": {},
                    },
                },
            }

    terminal = threading.Event()
    published = []
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.job_client = CompletingProductJob()

    def capture_event(event_type, payload=None, **kwargs):
        published.append((event_type, payload, kwargs))
        if event_type == "operation_terminal":
            terminal.set()

    monkeypatch.setattr(webapp, "publish_client_event", capture_event)
    try:
        receipt, status = webapp.context_items("5", 20)
        assert status == HTTPStatus.ACCEPTED
        operation_id = receipt["operation"]["id"]
        assert terminal.wait(2.0), "accepted context product did not publish terminal completion"
        result, result_status = webapp.operation_status_payload(operation_id)
        replay = webapp.operation_replay_payload(operation_id)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert result_status == HTTPStatus.OK
    assert result["state"] == "ready"
    assert result["data"]["items"] == [{"role": "user", "timestamp": "", "cwd": "", "text": "complete me"}]
    assert published[-1][0] == "operation_terminal"
    assert published[-1][1]["operation"]["id"] == operation_id
    assert replay == published[-1][1]


def test_context_product_completed_without_mapping_terminalizes_protocol_failure(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "malformed"}}) + "\n", encoding="utf-8")
    info = _single_agent_session_info("5", transcript, tmp_path)
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")

    class MalformedCompletionJob:
        def produce(self, task, payload, **kwargs):
            return {
                "ok": True,
                "state": "queued",
                "coalesced": False,
                "job": {"job_id": "job-context", "status": "running", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

        def result(self, job_id):
            assert job_id == "job-context"
            return {"ok": True, "job": {"job_id": job_id, "status": "completed", "result": []}}

    terminal = threading.Event()
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.job_client = MalformedCompletionJob()
    original_publish = webapp.publish_client_event

    def capture_event(event_type, payload=None, **kwargs):
        original_publish(event_type, payload, **kwargs)
        if event_type == "operation_terminal":
            terminal.set()

    monkeypatch.setattr(webapp, "publish_client_event", capture_event)
    try:
        receipt, status = webapp.context_items("5", 20)
        assert status == HTTPStatus.ACCEPTED
        operation_id = receipt["operation"]["id"]
        assert terminal.wait(0.5), "malformed completed product did not terminalize promptly"
        result, result_status = webapp.operation_status_payload(operation_id)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert result_status == HTTPStatus.SERVICE_UNAVAILABLE
    assert result["state"] == "failed"
    assert result["error"]["message"]["fallback"] == "malformed completed jobd product"


def test_context_product_unexpected_completion_failure_terminalizes_operation(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "explode"}}) + "\n", encoding="utf-8")
    info = _single_agent_session_info("5", transcript, tmp_path)
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")

    class CompletingProductJob:
        def produce(self, task, payload, **kwargs):
            return {
                "ok": True,
                "state": "queued",
                "job": {"job_id": "job-context", "status": "running", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

        def result(self, job_id):
            assert job_id == "job-context"
            return {"ok": True, "job": {"job_id": job_id, "status": "completed", "result": {}}}

    terminal = threading.Event()
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.job_client = CompletingProductJob()
    monkeypatch.setattr(webapp, "cache_transcript_product_result", lambda producer, result: (_ for _ in ()).throw(RuntimeError("cache exploded")))

    def capture_event(event_type, payload=None, **kwargs):
        if event_type == "operation_terminal":
            terminal.set()

    monkeypatch.setattr(webapp, "publish_client_event", capture_event)
    try:
        receipt, status = webapp.context_items("5", 20)
        assert status == HTTPStatus.ACCEPTED
        operation_id = receipt["operation"]["id"]
        assert terminal.wait(0.5), "unexpected completion failure left the accepted operation open"
        result, result_status = webapp.operation_status_payload(operation_id)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert result_status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert result["state"] == "failed"
    assert result["error"]["message"]["fallback"] == "cache exploded"


def test_filesystem_batch_receipt_completes_once_through_operation_sse(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    actions = []

    class CompletingBatchJob:
        def produce(self, task, payload, **kwargs):
            actions.append(("produce", threading.current_thread().name, task, payload, kwargs))
            return {
                "ok": True,
                "state": "queued",
                "job": {"job_id": "job-fs-batch", "status": "running", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

        def product(self, product_key):
            actions.append(("product", threading.current_thread().name, product_key))
            body = json.dumps({
                "responses": [
                    {"id": 0, "ok": True, "status": 200, "payload": {"path": "/repo", "entries": []}},
                    {"id": 1, "ok": True, "status": 200, "payload": {"path": "/repo", "kind": "dir"}},
                ],
                "performance": {"batch_size": 2},
            }).encode("utf-8")
            return {"ok": True, "state": "ready", "generation": 1, "inflight": False}, body

    terminal = threading.Event()
    published = []
    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = CompletingBatchJob()

    def capture_event(event_type, payload=None, **kwargs):
        published.append((event_type, payload, kwargs))
        if event_type == "operation_terminal":
            terminal.set()

    monkeypatch.setattr(webapp, "publish_client_event", capture_event)
    request_thread = threading.current_thread().name
    batch = {
        "client_scope": "browser",
        "requests": [
            {"id": "list", "type": "list", "path": "/repo", "trigger_counts": {"tree-render": 1}},
            {"id": "info", "type": "info", "path": "/repo", "trigger_counts": {"tree-render": 1}},
        ],
    }
    try:
        receipt, status = webapp.fs_batch_http_payload(batch)
        assert status == HTTPStatus.ACCEPTED
        operation_id = receipt["operation"]["id"]
        assert receipt["operation"]["kind"] == "fs_batch"
        assert receipt["operation"]["context"]["product_key"].startswith("fs-batch:")
        assert terminal.wait(2.0), "accepted filesystem batch did not publish terminal completion"
        result, result_status = webapp.operation_status_payload(operation_id)
        replay = webapp.operation_replay_payload(operation_id)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert [action[0] for action in actions if action[1] == request_thread] == ["produce"]
    produce = actions[0]
    assert produce[2] == "filesystem_batch"
    assert produce[3] == {
        **batch,
        "requests": [
            {**batch["requests"][0], "id": 0},
            {**batch["requests"][1], "id": 1},
        ],
    }
    assert produce[4]["delivery"] == "ready_or_receipt"
    assert result_status == HTTPStatus.OK
    assert all(response["ok"] is True for response in result["data"]["responses"])
    assert [response["id"] for response in result["data"]["responses"]] == ["list", "info"]
    assert published[-1][0] == "operation_terminal"
    assert replay == published[-1][1]


def _filesystem_json_product(body: bytes) -> dict[str, object]:
    return {
        "format": "json",
        "content_type": "application/json; charset=utf-8",
        "length": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "disposition": "inline",
        "filename": "",
    }


def test_filesystem_operation_cold_receipt_leaves_request_thread_before_worker_finishes(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    actions = []
    release = threading.Event()
    terminal = threading.Event()
    published = []

    class BlockingFilesystemJob:
        def produce(self, task, payload, **kwargs):
            actions.append(("produce", threading.current_thread().name, task, payload, kwargs))
            return {
                "ok": True,
                "state": "queued",
                "job": {"job_id": "job-fs-read", "status": "running", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

        def product(self, product_key):
            actions.append(("product", threading.current_thread().name, product_key))
            assert release.wait(2.0)
            body = json.dumps({"path": "/repo/note.txt", "content": "offloaded"}).encode("utf-8")
            return {
                "ok": True,
                "state": "ready",
                "generation": 1,
                "inflight": False,
                "product": {
                    "format": "json",
                    "content_type": "application/json; charset=utf-8",
                    "length": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "disposition": "inline",
                    "filename": "",
                },
            }, body

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = BlockingFilesystemJob()
    def capture_event(event_type, payload=None, **_kwargs):
        if event_type == "operation_terminal":
            published.append(payload)
            terminal.set()

    monkeypatch.setattr(webapp, "publish_client_event", capture_event)
    request_thread = threading.current_thread().name
    try:
        response = webapp.filesystem_operation_http_payload(
            route="GET /api/fs/read",
            operation="read",
            path="/repo/note.txt",
        )
        assert response.status == HTTPStatus.ACCEPTED
        assert response.payload["operation"]["kind"] == "filesystem_operation"
        assert not terminal.wait(0.05)
        release.set()
        assert terminal.wait(2.0)
        operation_id = response.payload["operation"]["id"]
        terminal_result, terminal_status = webapp.operation_status_payload(operation_id)
        replay = webapp.operation_replay_payload(operation_id)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert [action[0] for action in actions if action[1] == request_thread] == ["produce"]
    assert terminal_status == HTTPStatus.OK
    assert terminal_result["data"] == {"path": "/repo/note.txt", "content": "offloaded"}
    assert replay == published[-1]
    assert replay["result"] == terminal_result
    assert replay["status"] == HTTPStatus.OK


def test_duplicate_operation_terminalization_appends_and_publishes_once(monkeypatch, tmp_path):
    state_path = tmp_path / "operations.json"
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", state_path)
    webapp = app_module.TmuxWebtermApp([])
    published = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **kwargs: published.append((event_type, payload, kwargs)))
    receipt = webapp.queued_delivery_ledger.accept_operation(
        request_id="r-terminal-once",
        route="GET /api/fs/read",
        deadline_at=time.time() + 30,
        progress={"phase": "waiting_for_product"},
        producer={"service": "jobd", "job_id": "job-terminal-once"},
        kind="filesystem_operation",
        context={"operation": "read", "path": "/repo/file.txt"},
    )
    operation_id = receipt["operation"]["id"]
    ready = {"state": "ready", "request": receipt["request"], "data": {"path": "/repo/file.txt", "content": "stable"}}
    conflicting = {"state": "failed", "request": receipt["request"], "error": "duplicate must not replace ready"}
    try:
        first = webapp.terminalize_operation(operation_id, ready, HTTPStatus.OK)
        second = webapp.terminalize_operation(operation_id, conflicting, HTTPStatus.INTERNAL_SERVER_ERROR)
        status = webapp.operation_status_payload(operation_id)
        replay = webapp.operation_replay_payload(operation_id)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert first == replay
    assert second is None
    assert status == (ready, HTTPStatus.OK)
    assert replay["result"] == ready
    assert replay["status"] == HTTPStatus.OK
    assert [(event_type, payload) for event_type, payload, _kwargs in published] == [("operation_terminal", replay)]
    assert len(state_path.read_text(encoding="utf-8").splitlines()) == 2, "acceptance plus exactly one terminal append"


def test_conflicting_operation_terminalization_race_appends_and_publishes_once(monkeypatch, tmp_path):
    state_path = tmp_path / "operations.json"
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", state_path)
    webapp = app_module.TmuxWebtermApp([])
    published = []
    monkeypatch.setattr(
        webapp,
        "publish_client_event",
        lambda event_type, payload=None, **kwargs: published.append((event_type, payload, kwargs)),
    )
    receipt = webapp.queued_delivery_ledger.accept_operation(
        request_id="r-terminal-race",
        route="GET /api/fs/read",
        deadline_at=time.time() + 30,
        progress={"phase": "waiting_for_product"},
        producer={"service": "jobd", "job_id": "job-terminal-race"},
        kind="filesystem_operation",
        context={"operation": "read", "path": "/repo/file.txt"},
    )
    operation_id = receipt["operation"]["id"]
    ready = {"state": "ready", "request": receipt["request"], "data": {"content": "winner-a"}}
    failed = {"state": "failed", "request": receipt["request"], "error": "winner-b"}
    barrier = threading.Barrier(2)

    def terminalize(result, status):
        barrier.wait(timeout=2.0)
        return webapp.terminalize_operation(operation_id, result, status)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(terminalize, ready, HTTPStatus.OK),
                executor.submit(terminalize, failed, HTTPStatus.INTERNAL_SERVER_ERROR),
            )
            outcomes = [future.result() for future in futures]
        status = webapp.operation_status_payload(operation_id)
        replay = webapp.operation_replay_payload(operation_id)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert sum(outcome is not None for outcome in outcomes) == 1
    assert replay is not None and status == (replay["result"], HTTPStatus(replay["status"]))
    assert [(event_type, payload) for event_type, payload, _kwargs in published] == [("operation_terminal", replay)]
    assert len(state_path.read_text(encoding="utf-8").splitlines()) == 2, "acceptance plus one winning terminal"


@pytest.mark.parametrize(
    ("status", "message_key"),
    (
        (HTTPStatus.NOT_FOUND, "common.pathNotFound"),
        (HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "fs.error.tooLarge"),
        (HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "fs.error.binary"),
    ),
)
def test_filesystem_operation_cold_failure_replay_preserves_typed_status(monkeypatch, tmp_path, status, message_key):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    terminal = threading.Event()
    published = []
    filesystem_error = {
        "error": f"typed filesystem failure {int(status)}",
        "user_message": {"key": message_key, "params": {"path": "/repo/note.txt"}, "fallback": "typed failure"},
        "status": int(status),
        "path": "/repo/note.txt",
    }

    class FailingFilesystemJob:
        def produce(self, _task, _payload, **kwargs):
            return {
                "ok": True,
                "state": "queued",
                "job": {"job_id": "job-fs-failed", "status": "running", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

        def product(self, _product_key):
            return {"ok": True, "state": "none", "generation": 0, "inflight": False}, b""

        def result(self, job_id):
            assert job_id == "job-fs-failed"
            return {
                "ok": False,
                "job": {
                    "job_id": job_id,
                    "status": "failed",
                    "failure": {"filesystem_error": filesystem_error, "status": int(status)},
                },
            }

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = FailingFilesystemJob()

    def capture_event(event_type, payload=None, **_kwargs):
        if event_type == "operation_terminal":
            published.append(payload)
            terminal.set()

    monkeypatch.setattr(webapp, "publish_client_event", capture_event)
    try:
        response = webapp.filesystem_operation_http_payload(
            route="GET /api/fs/read",
            operation="read",
            path="/repo/note.txt",
        )
        assert response.status == HTTPStatus.ACCEPTED
        assert terminal.wait(2.0)
        operation_id = response.payload["operation"]["id"]
        result, result_status = webapp.operation_status_payload(operation_id)
        replay = webapp.operation_replay_payload(operation_id)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert result_status == status, (result, replay)
    assert result["state"] == "failed" and result["request"]["id"].startswith("r-")
    assert result["error"]["message"] == filesystem_error["user_message"]
    assert result["error"]["details"] == {
        "status": int(status), "path": "/repo/note.txt", "operation_id": operation_id,
        "diagnostic": filesystem_error["error"],
    }
    assert result["error"]["stack"][-1]["code"] in {
        "path_not_found", "request_too_large", "unsupported_media_type",
    }
    assert replay == published[-1]
    assert replay["result"] == result
    assert replay["status"] == status


@pytest.mark.parametrize(
    ("case", "expected_status", "message_key"),
    (
        ("missing", HTTPStatus.NOT_FOUND, "common.pathNotFound"),
        ("too-large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "fs.error.tooLarge"),
        ("binary", HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "fs.error.binary"),
    ),
)
def test_filesystem_operation_real_jobd_cold_failure_preserves_every_terminal_boundary(
    monkeypatch,
    tmp_path,
    case,
    expected_status,
    message_key,
):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    path = tmp_path / f"{case}.txt"
    if case == "too-large":
        with path.open("wb") as stream:
            stream.truncate(jobd.filesystem.MAX_READ_BYTES + 1)
    elif case == "binary":
        path.write_bytes(b"abc\0def")

    socket_path = tmp_path / "jobd.sock"
    broker = jobd.PersistentJobBroker(socket_path, idle_seconds=10.0, workers=1)
    broker_thread = threading.Thread(target=broker.run, daemon=True)
    broker_thread.start()
    real_client = jobd.JobClient(socket_path)
    result_responses = []

    class TrackingJobClient:
        def produce(self, *args, **kwargs):
            return real_client.produce(*args, **kwargs)

        def product(self, *args, **kwargs):
            return real_client.product(*args, **kwargs)

        def result(self, *args, **kwargs):
            response = real_client.result(*args, **kwargs)
            result_responses.append(response)
            return response

    deadline = time.monotonic() + 2.0
    while not real_client.registry.healthy() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert real_client.registry.healthy() is True

    terminal = threading.Event()
    published = []
    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = TrackingJobClient()

    def capture_event(event_type, payload=None, **_kwargs):
        if event_type == "operation_terminal":
            published.append(payload)
            terminal.set()

    monkeypatch.setattr(webapp, "publish_client_event", capture_event)
    try:
        response = webapp.filesystem_operation_http_payload(
            route="GET /api/fs/read",
            operation="read",
            path=str(path),
        )
        assert response.status == HTTPStatus.ACCEPTED
        assert response.payload["operation"]["kind"] == "filesystem_operation"
        assert terminal.wait(5.0)
        operation_id = response.payload["operation"]["id"]
        result, result_status = webapp.operation_status_payload(operation_id)
        replay = webapp.operation_replay_payload(operation_id)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()
        real_client.request({"action": "shutdown"})
        broker_thread.join(timeout=2.0)

    assert broker_thread.is_alive() is False
    assert result_responses
    process_failure = result_responses[-1]["job"]["failure"]
    filesystem_error = process_failure["filesystem_error"]
    assert process_failure["status"] == expected_status
    assert filesystem_error["status"] == expected_status
    assert filesystem_error["user_message"]["key"] == message_key
    assert result_status == expected_status
    assert result["state"] == "failed" and result["request"]["id"].startswith("r-")
    assert result["error"]["message"] == filesystem_error["user_message"]
    assert result["error"]["details"] == {
        "status": int(expected_status), "path": str(path), "operation_id": operation_id,
        "diagnostic": filesystem_error["error"],
    }
    assert result["error"]["stack"][-1]["code"] in {
        "path_not_found", "request_too_large", "unsupported_media_type",
    }
    assert replay == published[-1]
    assert replay["result"] == result
    assert replay["status"] == expected_status


def test_warm_filesystem_operation_typed_failure_returns_before_receipt_admission(monkeypatch):
    filesystem_error = {
        "error": "path not found: /repo/missing.txt",
        "user_message": {"key": "common.pathNotFound", "params": {"path": "/repo/missing.txt"}, "fallback": "File not found"},
        "status": int(HTTPStatus.NOT_FOUND),
        "path": "/repo/missing.txt",
    }

    class ImmediateFailureJob:
        def produce(self, *_args, **_kwargs):
            return {
                "ok": False,
                "job": {"status": "failed", "failure": {"filesystem_error": filesystem_error, "status": int(HTTPStatus.NOT_FOUND)}},
            }, b""

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = ImmediateFailureJob()
    monkeypatch.setattr(webapp, "filesystem_operation_product_generation", lambda: "watchd:epoch-a:7")
    try:
        response = webapp.filesystem_operation_http_payload(
            route="GET /api/fs/read",
            operation="read",
            path="/repo/missing.txt",
        )
        open_operations = webapp.queued_delivery_ledger.open_operations()
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert response.status == HTTPStatus.NOT_FOUND
    assert response.payload == {**filesystem_error, "terminal": True}
    assert open_operations == []


@pytest.mark.parametrize(
    "failure",
    (
        {"ok": False, "error": "unknown task"},
        {"ok": False, "_transport_error": "unavailable", "error": "jobd transport unavailable"},
    ),
    ids=("unknown-task", "transport-failure"),
)
def test_warm_filesystem_operation_non_filesystem_failures_remain_generic(failure):
    class ImmediateGenericFailureJob:
        def produce(self, *_args, **_kwargs):
            return copy.deepcopy(failure), b""

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = ImmediateGenericFailureJob()
    try:
        response = webapp.filesystem_operation_http_payload(
            route="GET /api/fs/read",
            operation="read",
            path="/repo/file.txt",
        )
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.payload["state"] == "failed"
    assert response.payload["error"]["code"] == "service_unavailable"
    assert "user_message" not in response.payload


@pytest.mark.parametrize("failure_kind", ("worker-crash", "malformed-product"))
def test_cold_filesystem_operation_non_filesystem_failures_terminalize_generic(monkeypatch, tmp_path, failure_kind):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    terminal = threading.Event()
    published = []

    class GenericColdFailureJob:
        def produce(self, _task, _payload, **kwargs):
            return {
                "ok": True,
                "state": "queued",
                "job": {"job_id": f"job-{failure_kind}", "status": "running", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

        def product(self, _product_key):
            if failure_kind == "malformed-product":
                body = b"not-json"
                return {
                    "ok": True,
                    "state": "ready",
                    "generation": 1,
                    "inflight": False,
                    "product": {"format": "json", "content_type": "application/json", "length": len(body)},
                }, body
            return {"ok": True, "state": "none", "generation": 0, "inflight": False}, b""

        def result(self, job_id):
            assert failure_kind == "worker-crash"
            assert job_id == "job-worker-crash"
            return {
                "ok": False,
                "job": {"job_id": job_id, "status": "failed", "error": "worker crashed", "failure": {"error": "worker crashed"}},
            }

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = GenericColdFailureJob()

    def capture_event(event_type, payload=None, **_kwargs):
        if event_type == "operation_terminal":
            published.append(payload)
            terminal.set()

    monkeypatch.setattr(webapp, "publish_client_event", capture_event)
    try:
        response = webapp.filesystem_operation_http_payload(
            route="GET /api/fs/read",
            operation="read",
            path="/repo/file.txt",
        )
        assert response.status == HTTPStatus.ACCEPTED
        assert terminal.wait(2.0)
        operation_id = response.payload["operation"]["id"]
        result, result_status = webapp.operation_status_payload(operation_id)
        replay = webapp.operation_replay_payload(operation_id)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert result_status in {HTTPStatus.SERVICE_UNAVAILABLE, HTTPStatus.INTERNAL_SERVER_ERROR}
    assert result["state"] == "failed"
    assert result["error"]["code"] in {"service_unavailable", "producer_failed"}
    assert "user_message" not in result
    assert replay == published[-1]
    assert replay["result"] == result
    assert replay["status"] == result_status


def test_filesystem_operation_relay_forwards_one_opaque_product_without_a_receipt():
    body = b"\x00raw\xff"
    product = {
        "format": "opaque_bytes",
        "content_type": "application/octet-stream",
        "length": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "disposition": "inline",
        "filename": "",
    }
    calls = []

    class RelayJob:
        def relay(self, task, payload, **kwargs):
            calls.append((task, payload, kwargs))
            return {"ok": True, "state": "ready", "product": product}, body

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = RelayJob()
    try:
        response = webapp.filesystem_operation_relay(
            route="GET /api/fs/raw",
            operation="raw",
            path="/repo/payload.bin",
            args={"download": False, "max_bytes": 1024},
        )
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert calls == [("filesystem_operation", {
        "op": "raw",
        "path": "/repo/payload.bin",
        "args": {"download": False, "max_bytes": 1024},
    }, {"deadline_ms": int(app_module.FS_BATCH_OPERATION_DEADLINE_SECONDS * 1000)})]
    assert response.status == HTTPStatus.OK
    assert response.payload is None
    assert response.body == body
    assert response.product == product
    assert not hasattr(response, "promise")


def test_filesystem_operation_relay_uses_shared_typed_failure_normalizer():
    filesystem_error = {
        "error": "path not found: /repo/missing.bin",
        "user_message": {"key": "common.pathNotFound", "params": {"path": "/repo/missing.bin"}, "fallback": "File not found"},
        "status": int(HTTPStatus.NOT_FOUND),
        "path": "/repo/missing.bin",
    }

    class RelayFailureJob:
        def relay(self, *_args, **_kwargs):
            return {
                "ok": False,
                "job": {"status": "failed", "failure": {"filesystem_error": filesystem_error, "status": int(HTTPStatus.NOT_FOUND)}},
            }, b""

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = RelayFailureJob()
    try:
        response = webapp.filesystem_operation_relay(
            route="GET /api/fs/raw",
            operation="raw",
            path="/repo/missing.bin",
        )
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert response.status == HTTPStatus.NOT_FOUND
    assert response.payload == {**filesystem_error, "terminal": True}
    assert "terminal" not in filesystem_error


def test_filesystem_operation_submission_is_stable_per_scope_and_watchd_revision():
    first_payload, first_key = app_module.filesystem_operation_submission(
        "list",
        "/repo/./src",
        {"limit": "400"},
        scope="share-token-a",
        generation="watchd:epoch-a:7",
    )
    second_payload, second_key = app_module.filesystem_operation_submission(
        "list",
        "/repo/src",
        {"limit": "400"},
        scope="share-token-a",
        generation="watchd:epoch-a:7",
    )
    _, other_scope_key = app_module.filesystem_operation_submission(
        "list",
        "/repo/src",
        {"limit": "400"},
        scope="share-token-b",
        generation="watchd:epoch-a:7",
    )
    _, newer_revision_key = app_module.filesystem_operation_submission(
        "list",
        "/repo/src",
        {"limit": "400"},
        scope="share-token-a",
        generation="watchd:epoch-a:8",
    )

    assert first_payload == {"op": "list", "path": "/repo/src", "args": {"limit": "400"}}
    assert second_payload == first_payload
    assert first_key == second_key
    assert other_scope_key != first_key
    assert newer_revision_key != first_key


def test_filesystem_operation_reuses_one_watchd_scoped_product_key(monkeypatch):
    body = b'{"path":"/repo/src","entries":[]}'
    product = {
        "format": "json",
        "content_type": "application/json; charset=utf-8",
        "length": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "disposition": "inline",
        "filename": "",
    }
    coalesce_keys = []

    class ReadyJob:
        def produce(self, _task, _payload, **kwargs):
            coalesce_keys.append(kwargs["coalesce_key"])
            return {"ok": True, "state": "ready", "product": product}, body

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = ReadyJob()
    monkeypatch.setattr(webapp, "filesystem_operation_product_generation", lambda: "watchd:epoch-a:7")
    try:
        first = webapp.filesystem_operation_http_payload(
            route="GET /api/fs/list",
            operation="list",
            path="/repo/./src",
            scope="user:readonly:alice",
        )
        second = webapp.filesystem_operation_http_payload(
            route="GET /api/fs/list",
            operation="list",
            path="/repo/src",
            scope="user:readonly:alice",
        )
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert first.product == product
    assert second.product == product
    assert coalesce_keys[0] == coalesce_keys[1]


def test_warm_filesystem_operation_relays_produce_bytes_without_a_second_product_read(monkeypatch):
    body = b'{"path":"/repo/src","entries":[]}'
    product = {
        "format": "json",
        "content_type": "application/json; charset=utf-8",
        "length": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "disposition": "inline",
        "filename": "",
    }
    calls = []

    class ReadyJob:
        def produce(self, task, payload, **kwargs):
            calls.append(("produce", task, payload, kwargs))
            return {"ok": True, "state": "ready", "product": product}, body

        def product(self, *_args, **_kwargs):
            raise AssertionError("warm product metadata must be returned by the produce relay")

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = ReadyJob()
    monkeypatch.setattr(webapp, "filesystem_operation_product_generation", lambda: "watchd:epoch-a:7")
    try:
        response = webapp.filesystem_operation_http_payload(
            route="GET /api/fs/list",
            operation="list",
            path="/repo/src",
            scope="user:readonly:alice",
        )
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert response.status == HTTPStatus.OK
    assert response.payload is None
    assert response.body == body
    assert response.product == product
    assert not hasattr(response, "promise")
    assert [call[0] for call in calls] == ["produce"]


def test_cold_terminal_then_same_key_warm_adds_no_receipt_or_terminal(monkeypatch, tmp_path):
    state_path = tmp_path / "operations.json"
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", state_path)
    body = b'{"path":"/repo/src","entries":[]}'
    product = _filesystem_json_product(body)
    terminal = threading.Event()
    published = []
    produce_calls = 0

    class ColdThenWarmJob:
        def produce(self, _task, _payload, **kwargs):
            nonlocal produce_calls
            produce_calls += 1
            if produce_calls == 1:
                return {
                    "ok": True,
                    "state": "queued",
                    "job": {"job_id": "job-cold-warm", "status": "running", "generation": kwargs["generation"]},
                    "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
                }, b""
            return {"ok": True, "state": "ready", "product": product}, body

        def product(self, _product_key):
            return {
                "ok": True,
                "state": "ready",
                "generation": 1,
                "inflight": False,
                "product": product,
            }, body

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = ColdThenWarmJob()
    monkeypatch.setattr(webapp, "filesystem_operation_product_generation", lambda: "watchd:epoch-a:7")

    def capture_event(event_type, payload=None, **_kwargs):
        if event_type == "operation_terminal":
            published.append(payload)
            terminal.set()

    monkeypatch.setattr(webapp, "publish_client_event", capture_event)
    try:
        cold = webapp.filesystem_operation_http_payload(
            route="GET /api/fs/list",
            operation="list",
            path="/repo/src",
            scope="user:readonly:alice",
        )
        assert cold.status == HTTPStatus.ACCEPTED
        assert terminal.wait(2.0)
        journal_before = state_path.read_bytes()
        warm = webapp.filesystem_operation_http_payload(
            route="GET /api/fs/list",
            operation="list",
            path="/repo/src",
            scope="user:readonly:alice",
        )
        journal_after = state_path.read_bytes()
        diagnostics = webapp.queued_delivery_ledger.diagnostics()
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert warm.status == HTTPStatus.OK
    assert warm.body == body and warm.product == product
    assert len(published) == 1
    assert journal_after == journal_before
    assert len(journal_after.splitlines()) == 2, "one acceptance and one terminal are durable"
    assert diagnostics["queued_delivery_frames"] == []
    assert diagnostics["outstanding_queued"] == []


@pytest.mark.parametrize("caller_count", (2, 8, 32))
def test_concurrent_warm_filesystem_same_key_callers_do_not_mutate_ledgers(monkeypatch, caller_count):
    body = b'{"path":"/repo/file.txt","diff":"stable"}'
    product = _filesystem_json_product(body)
    barrier = threading.Barrier(caller_count)
    coalesce_keys = []
    calls_lock = threading.Lock()

    class ConcurrentWarmJob:
        def produce(self, _task, _payload, **kwargs):
            with calls_lock:
                coalesce_keys.append(kwargs["coalesce_key"])
            barrier.wait(timeout=2.0)
            return {"ok": True, "state": "ready", "product": product}, body

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = ConcurrentWarmJob()
    monkeypatch.setattr(webapp, "filesystem_operation_product_generation", lambda: "watchd:epoch-a:7")
    try:
        with ThreadPoolExecutor(max_workers=caller_count) as executor:
            responses = list(executor.map(
                lambda _index: webapp.filesystem_operation_http_payload(
                    route="GET /api/fs/diff",
                    operation="diff",
                    path="/repo/file.txt",
                    args={"from_ref": "HEAD", "to_ref": "current"},
                    scope="user:readonly:alice",
                ),
                range(caller_count),
            ))
        diagnostics = webapp.queued_delivery_ledger.diagnostics()
        operations = webapp.queued_delivery_ledger.open_operations()
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert all(response.status == HTTPStatus.OK for response in responses)
    assert all(response.body == body and response.product == product for response in responses)
    assert len(set(coalesce_keys)) == 1
    assert diagnostics["queued_delivery_frames"] == []
    assert diagnostics["outstanding_queued"] == []
    assert operations == []


@pytest.mark.parametrize(
    ("route", "operation", "args"),
    (
        ("GET /api/fs/list", "list", {}),
        ("GET /api/fs/search", "search", {"query": "needle", "limit": 20, "recursive": True}),
        ("GET /api/fs/index-status", "index_status", {}),
        ("GET /api/fs/read", "read", {}),
        ("GET /api/fs/info", "info", {}),
        ("GET /api/fs/diff", "diff", {"from_ref": "HEAD", "to_ref": "current"}),
        ("GET /api/blame", "blame", {"ref": "HEAD"}),
        ("GET /api/fs/count", "count", {}),
    ),
)
def test_retained_filesystem_reads_scope_and_revision_warm_without_ledger_mutation(
    monkeypatch,
    route,
    operation,
    args,
):
    body = json.dumps({"operation": operation}, separators=(",", ":")).encode("utf-8")
    product = _filesystem_json_product(body)
    generation = ["watchd:epoch-a:7"]
    keys = []

    class RetainedReadJob:
        def produce(self, _task, _payload, **kwargs):
            keys.append(kwargs["coalesce_key"])
            return {"ok": True, "state": "ready", "product": product}, body

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = RetainedReadJob()
    monkeypatch.setattr(webapp, "filesystem_operation_product_generation", lambda: generation[0])
    try:
        first = webapp.filesystem_operation_http_payload(
            route=route, operation=operation, path="/repo/file.txt", args=args,
            scope="user:readonly:alice",
        )
        generation[0] = "watchd:epoch-a:8"
        revised = webapp.filesystem_operation_http_payload(
            route=route, operation=operation, path="/repo/file.txt", args=args,
            scope="user:readonly:alice",
        )
        other_scope = webapp.filesystem_operation_http_payload(
            route=route, operation=operation, path="/repo/file.txt", args=args,
            scope="user:readonly:bob",
        )
        diagnostics = webapp.queued_delivery_ledger.diagnostics()
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert all(response.status == HTTPStatus.OK for response in (first, revised, other_scope))
    assert len(keys) == len(set(keys)) == 3
    assert diagnostics["queued_delivery_frames"] == []
    assert diagnostics["outstanding_queued"] == []


@pytest.mark.parametrize("operation", ("write", "delete", "unindex", "rename", "mkdir"))
def test_immediate_filesystem_mutations_create_no_phantom_delivery_state(monkeypatch, operation):
    body = json.dumps({"operation": operation, "ok": True}, separators=(",", ":")).encode("utf-8")
    product = _filesystem_json_product(body)

    class ImmediateMutationJob:
        def produce(self, _task, _payload, **_kwargs):
            return {"ok": True, "state": "ready", "product": product}, body

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = ImmediateMutationJob()
    monkeypatch.setattr(webapp, "filesystem_operation_product_generation", lambda: "watchd:epoch-a:7")
    try:
        response = webapp.filesystem_operation_http_payload(
            route=f"POST /api/fs/{operation}", operation=operation, path="/repo/file.txt",
            args={"content": "next", "new_name": "renamed.txt"}, scope="user:admin:alice",
        )
        diagnostics = webapp.queued_delivery_ledger.diagnostics()
        operations = webapp.queued_delivery_ledger.open_operations()
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert response.status == HTTPStatus.OK
    assert response.body == body and response.product == product
    assert not hasattr(response, "promise")
    assert diagnostics["queued_delivery_frames"] == []
    assert diagnostics["outstanding_queued"] == []
    assert operations == []


def test_warm_filesystem_operation_timeout_falls_through_to_a_cold_receipt(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    calls = []

    class TimeoutThenReceiptJob:
        def produce(self, _task, _payload, **kwargs):
            calls.append(kwargs["delivery"])
            if kwargs["delivery"] == "ready_or_receipt":
                return {"ok": False, "_transport_error": "timeout", "error": "timeout"}, b""
            return {"ok": True, "state": "queued", "job": {"job_id": "job-timeout", "status": "queued"}}, b""

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = TimeoutThenReceiptJob()
    monkeypatch.setattr(webapp, "filesystem_operation_product_generation", lambda: "watchd:epoch-a:7")
    try:
        response = webapp.filesystem_operation_http_payload(
            route="GET /api/fs/list", operation="list", path="/repo/src", scope="user:readonly:alice",
        )
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert response.status == HTTPStatus.ACCEPTED
    assert response.payload["operation"]["kind"] == "filesystem_operation"
    assert calls == ["ready_or_receipt", "receipt"]


def test_filesystem_batch_ready_product_reuses_stable_key_and_materializes_current_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    submissions = []
    product_body = json.dumps({
        "responses": [
            {"id": 0, "ok": True, "status": 200, "payload": {"path": "/repo/a", "entries": []}},
            {"id": 1, "ok": True, "status": 200, "payload": {"path": "/repo/b", "kind": "dir"}},
        ],
        "performance": {"batch_size": 2, "operation_ms": 1.0},
    }).encode("utf-8")

    class ReadyBatchJob:
        def produce(self, task, payload, **kwargs):
            submissions.append((task, payload, kwargs))
            return {
                "ok": True,
                "state": "ready",
                "job": {"job_id": "", "status": "completed", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": kwargs["generation"]},
            }, product_body

    class ImmediateCompletionService:
        stop_event = threading.Event()

        def __init__(self):
            self.reservations = 0

        def reserve(self):
            self.reservations += 1
            return True

        def release_reservation(self):
            self.reservations -= 1

        def submit_reserved(self, *_args):
            raise AssertionError("ready filesystem batch must not start an operation completion")

        def stop(self):
            self.stop_event.set()

    webapp = app_module.TmuxWebtermApp([])
    completion_service = ImmediateCompletionService()
    webapp.job_client = ReadyBatchJob()
    webapp.jobd_operation_service = completion_service
    first = {
        "client_scope": "browser",
        "requests": [
            {"id": "first-a", "type": "list", "path": "/repo/a", "trigger_counts": {"tree-render": 1}},
            {"id": "first-b", "type": "info", "path": "/repo/b", "trigger_counts": {"tree-render": 1}},
        ],
    }
    second = copy.deepcopy(first)
    second["requests"][0]["id"] = "second-a"
    second["requests"][1]["id"] = "second-b"
    changed_token = copy.deepcopy(second)
    changed_token["watch_token"] = "watch-token-next"
    try:
        first_result, first_status = webapp.fs_batch_http_payload(first)
        second_result, second_status = webapp.fs_batch_http_payload(second)
        _changed_result, changed_status = webapp.fs_batch_http_payload(changed_token)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert [first_status, second_status, changed_status] == [HTTPStatus.OK, HTTPStatus.OK, HTTPStatus.OK]
    assert [response["id"] for response in first_result["responses"]] == ["first-a", "first-b"]
    assert [response["id"] for response in second_result["responses"]] == ["second-a", "second-b"]
    assert [submission[0] for submission in submissions] == ["filesystem_batch"] * 3
    assert [request["id"] for request in submissions[0][1]["requests"]] == [0, 1]
    assert submissions[0][1] == submissions[1][1]
    assert submissions[0][2]["coalesce_key"] == submissions[1][2]["coalesce_key"]
    assert submissions[2][2]["coalesce_key"] != submissions[1][2]["coalesce_key"]
    assert submissions[0][2]["coalesce_key"].startswith("fs-batch:")
    assert [submission[2]["delivery"] for submission in submissions] == ["ready_or_receipt"] * 3
    assert completion_service.reservations == 0


def test_filesystem_batch_capacity_refusal_does_not_submit_an_orphan_job(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    webapp = app_module.TmuxWebtermApp([])
    webapp.jobd_operation_service = state_services.JobdOperationService(worker_limit=1, operation_limit=1)
    assert webapp.jobd_operation_service.reserve() is True
    submissions = []
    webapp.job_client = SimpleNamespace(produce=lambda *args, **kwargs: submissions.append((args, kwargs)))
    try:
        result, status = webapp.fs_batch_http_payload({
            "requests": [{"id": "list", "type": "list", "path": "/repo", "trigger_counts": {"tree-render": 1}}],
        })
    finally:
        webapp.jobd_operation_service.release_reservation()
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert status == HTTPStatus.SERVICE_UNAVAILABLE
    assert result["state"] == "failed"
    assert result["error"]["code"] == "service_busy"
    assert submissions == []


def test_jobd_operation_service_bounds_accepted_completion_capacity():
    service = state_services.JobdOperationService(worker_limit=1, operation_limit=1)
    started = threading.Event()
    release = threading.Event()

    def work():
        started.set()
        assert release.wait(2.0)

    assert service.reserve() is True
    assert service.submit_reserved(work) is True
    assert started.wait(2.0)
    assert service.reserve() is False
    future = next(iter(service.futures))
    release.set()
    future.result(timeout=2.0)
    assert service.reserve() is True
    service.release_reservation()
    service.stop()


def test_transcript_compact_view_serves_last_known_good_product_stale_during_append(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "old"}}) + "\n", encoding="utf-8")
    # Simulate an in-progress append: a raw line jobd has not parsed yet. The web process must never
    # surface this raw text; it serves the prior complete product instead.
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write('{"payload":{"type":"user_message","message":"RAW-APPENDED-UNPARSED"}}\n')
    info = _single_agent_session_info("5", transcript, tmp_path)
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module, "tail_file_lines", lambda *a, **k: (_ for _ in ()).throw(AssertionError("web process must not parse")))
    stat = transcript.stat()
    product_body = json.dumps({
        "generation": [stat.st_mtime_ns - 10, stat.st_size - 5],
        "read_generation": [stat.st_mtime_ns - 10, stat.st_size - 5],
        "identity": [stat.st_dev, stat.st_ino],
        "items": [{"role": "assistant", "timestamp": "", "cwd": "", "text": "prior complete answer"}],
        "compact_lines": ["prior complete answer"],
        "since_items": [],
        "since_stats": {},
    }).encode("utf-8")

    class StaleProductJob:
        def produce(self, task, payload, **kwargs):
            return {
                "ok": True,
                "state": "stale",
                "coalesced": False,
                "job": {"job_id": "job-newer", "status": "running", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 1},
            }, product_body

    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.job_client = StaleProductJob()
    try:
        payload, status = webapp.transcript_compact_view("5", 20)
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["pending"] is False
    assert payload["stale"] is True
    assert payload["items"] == [{"role": "assistant", "timestamp": "", "cwd": "", "text": "prior complete answer"}]
    assert "RAW-APPENDED-UNPARSED" not in json.dumps(payload)


def test_transcript_compact_view_rejects_replaced_inode_result(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "current file"}}) + "\n", encoding="utf-8")
    info = _single_agent_session_info("5", transcript, tmp_path)
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module, "tail_file_lines", lambda *a, **k: (_ for _ in ()).throw(AssertionError("web process must not parse")))
    stat = transcript.stat()

    class ReplacedInodeJob:
        def __init__(self):
            self.submissions = 0

        def produce(self, task, payload, **kwargs):
            self.submissions += 1
            # Same [mtime, size] as the live file, but a DIFFERENT device+inode: a replaced file
            # that coincidentally reproduced the byte generation must not satisfy this key.
            body = json.dumps({
                "generation": [stat.st_mtime_ns, stat.st_size],
                "read_generation": [stat.st_mtime_ns, stat.st_size],
                "identity": [stat.st_dev + 1, stat.st_ino + 1],
                "items": [{"role": "user", "timestamp": "", "cwd": "", "text": "from a replaced file"}],
                "compact_lines": [],
                "since_items": [],
                "since_stats": {},
            }).encode("utf-8")
            return {
                "ok": True,
                "state": "ready",
                "coalesced": self.submissions > 1,
                "job": {"job_id": "job-1", "status": "completed", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": kwargs["generation"]},
            }, body

    webapp = app_module.TmuxWebtermApp(["5"])
    worker = ReplacedInodeJob()
    webapp.job_client = worker
    try:
        first, _ = webapp.transcript_compact_view("5", 20)
        second, _ = webapp.transcript_compact_view("5", 20)
    finally:
        webapp.control_server.stop()

    assert first["pending"] is True
    # The mismatched-inode completion is rejected, so the view stays pending and re-submits.
    assert second["pending"] is True
    assert second["items"] == []
    assert worker.submissions == 2
    assert "from a replaced file" not in json.dumps(second)


def test_bumping_transcript_parser_generation_busts_cached_transcript_job(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "hello"}}) + "\n", encoding="utf-8")
    info = _single_agent_session_info("5", transcript, tmp_path)
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module, "tail_file_lines", lambda *a, **k: (_ for _ in ()).throw(AssertionError("web process must not parse")))

    class SubmitTrackingJob:
        def __init__(self):
            self.coalesce_keys = []

        def produce(self, task, payload, **kwargs):
            self.coalesce_keys.append(kwargs.get("coalesce_key"))
            return {
                "ok": True,
                "state": "queued",
                "coalesced": False,
                "job": {"job_id": f"job-{len(self.coalesce_keys)}", "status": "running"},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

    webapp = app_module.TmuxWebtermApp(["5"])
    worker = SubmitTrackingJob()
    webapp.job_client = worker
    baseline = app_module.TRANSCRIPT_PARSER_GENERATION
    try:
        webapp.transcript_compact_view("5", 20)
        # A parser-shape change bumps the generation and must bust the previously keyed cache/product.
        monkeypatch.setattr(app_module, "TRANSCRIPT_PARSER_GENERATION", baseline + 1)
        webapp.transcript_compact_view("5", 20)
    finally:
        webapp.control_server.stop()

    assert len(worker.coalesce_keys) == 2
    assert worker.coalesce_keys[0].startswith(f"transcript:v{baseline}:")
    assert worker.coalesce_keys[1].startswith(f"transcript:v{baseline + 1}:")
    assert worker.coalesce_keys[0] != worker.coalesce_keys[1]


def test_context_items_bounded_wrapper_resolves_warm_product_without_local_parse(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "warm"}}) + "\n", encoding="utf-8")
    info = _single_agent_session_info("5", transcript, tmp_path)
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module, "tail_file_lines", lambda *a, **k: (_ for _ in ()).throw(AssertionError("web process must not parse")))
    stat = transcript.stat()

    class WarmProductJob:
        def produce(self, task, payload, **kwargs):
            body = json.dumps({
                "generation": [stat.st_mtime_ns - 1, stat.st_size - 1],
                "read_generation": [stat.st_mtime_ns - 1, stat.st_size - 1],
                "identity": [stat.st_dev, stat.st_ino],
                "items": [{"role": "user", "timestamp": "", "cwd": "", "text": "warm"}],
                "compact_lines": [],
                "since_items": [],
                "since_stats": {},
            }).encode("utf-8")
            return {
                "ok": True,
                "state": "ready",
                "coalesced": True,
                "job": {"job_id": "job-1", "status": "completed", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 1},
            }, body

    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.job_client = WarmProductJob()
    try:
        payload, status = webapp.transcript_compact_view_bounded("5", 20, wait_ms=0)
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    # A warm product returns synchronously as stale (distinct from pending), even with a zero wait.
    assert payload["pending"] is False
    assert payload["stale"] is True
    assert payload["items"] == [{"role": "user", "timestamp": "", "cwd": "", "text": "warm"}]


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

    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None

    def transcript_view(session, messages, *, since=None, **_kwargs):
        assert session == "5"
        text = transcript.read_text(encoding="utf-8")
        items, stats = transcripts.compact_transcript_items_since(text, since)
        newest = transcripts.newest_transcript_timestamp(text)
        return {
            "pending": False,
            "path": str(transcript),
            "items": transcripts.compact_transcript_items(text, messages),
            "since_items": items[-messages:],
            "since_stats": stats,
            "newest_timestamp": newest.isoformat() if newest else "",
            "activity_timestamp": "",
        }, HTTPStatus.OK

    webapp.transcript_compact_view = transcript_view
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_direct_prompt_backend", fake_direct_backend)
    settings = {"backend": "codex", "invocation": "cli"}
    try:
        first = webapp.yoagent_controller.update_yoagent_session_summary("5", info, settings)
        unchanged = webapp.yoagent_controller.update_yoagent_session_summary("5", info, settings)
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": "2026-06-07T10:05:00Z", "payload": {"type": "agent_message", "message": "Tests now pass."}}) + "\n")
        second = webapp.yoagent_controller.update_yoagent_session_summary("5", info, settings)
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
    monkeypatch.setattr(webapp.yoagent_controller, "tick_yoagent_session_summaries", lambda settings=None, **kwargs: ticks.append((settings, kwargs)) or {"enabled": True})
    try:
        webapp.yoagent_controller.maybe_start_yoagent_summary_worker()
        webapp.yoagent_controller.maybe_start_yoagent_summary_worker()
    finally:
        webapp.control_server.stop()

    assert started_threads == [("yoagent-summary-first-launch", True)]
    assert ticks == [(webapp.yoagent_settings(), {"force": True})]
    assert webapp.yoagent_summary_worker_record.first_launch_started is True
    assert webapp.yoagent_summary_worker_record.running is False
    assert webapp.yoagent_summary_worker_record.worker is None


def test_yoagent_session_summary_worker_start_failure_allows_retry(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    start_attempts = []
    ticks = []

    class FlakyThread:
        def __init__(self, target, name=None, daemon=False):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            start_attempts.append(self.name)
            if len(start_attempts) == 1:
                raise RuntimeError("thread unavailable")
            self.target()

    monkeypatch.setattr(session_summaries_module.threading, "Thread", FlakyThread)
    monkeypatch.setattr(webapp.yoagent_controller, "tick_yoagent_session_summaries", lambda settings=None, **kwargs: ticks.append((settings, kwargs)) or {"enabled": True})
    try:
        with pytest.raises(RuntimeError, match="thread unavailable"):
            webapp.yoagent_controller.maybe_start_yoagent_summary_worker()
        failed_record = webapp.yoagent_summary_worker_record
        assert failed_record.worker is None
        assert failed_record.running is False
        assert failed_record.first_launch_started is False

        webapp.yoagent_controller.maybe_start_yoagent_summary_worker()
    finally:
        webapp.control_server.stop()

    assert start_attempts == ["yoagent-summary-first-launch", "yoagent-summary-first-launch"]
    assert ticks == [(webapp.yoagent_settings(), {"force": True})]
    assert webapp.yoagent_summary_worker_record.first_launch_started is True
    assert webapp.yoagent_summary_worker_record.running is False
    assert webapp.yoagent_summary_worker_record.worker is None


def test_yoagent_session_summary_parallel_worker_fields_are_retired():
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert "self.yoagent_summary_worker_running" not in source
    assert "self.yoagent_summary_first_launch_started" not in source


def test_visible_yoagent_launch_starts_first_launch_summary_worker(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    starts = []
    monkeypatch.setattr(webapp.yoagent_controller, "maybe_start_yoagent_summary_worker", lambda: starts.append("summary"))
    monkeypatch.setattr(app_module, "resolve_yoagent_backend", lambda _requested: "deterministic")
    try:
        background_payload, background_status = webapp.yoagent_controller.yoagent_prewarm({"visible": False})
        visible_payload, visible_status = webapp.yoagent_controller.yoagent_prewarm({"visible": True})
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
        webapp.yoagent_controller.set_yoagent_chat_request_interrupt("chat-test", lambda: interrupts.append("called") or {"ok": True, "interrupted": True})
        payload, status = webapp.yoagent_controller.cancel_yoagent_chat("chat-test")
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
    monkeypatch.setattr(
        webapp,
        "activity_summary_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled activity summary must not serve deterministic fallback")
        ),
    )
    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "what is session 5 doing?"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "deterministic"
    assert payload["fallback"] is False
    assert "No AI agent activity is available yet." in payload["answer"]
    assert any("capability: YOLOmux can read tmux panes" in line for line in payload["context_lines"])
    assert all("tmux session `5`" not in line for line in payload["context_lines"])


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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
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
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_claude_cli", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("visible sends must not use native resume")))

    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "wait for session 6 to be done, then ask for date"})
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "needs-input", "text": "Want me to keep using system PT?"}))
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
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "ask session 1 what it has done today"})
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
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
        webapp.yoagent_controller.register_yoagent_action_wait("wait-1", preview, marker)
        return {"id": "wait-1", "started": True}

    monkeypatch.setattr(webapp.yoagent_controller, "start_yoagent_action_result_watcher", fake_start_result_watcher)

    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "send `date` to tmux session 6"})
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
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
    monkeypatch.setattr(webapp.yoagent_controller, "start_yoagent_action_result_watcher", lambda preview, marker: watchers.append((preview, marker)) or {"started": True})

    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "send `date` to tmux session 6 but do not wait for the result"})
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_acceptance", lambda current: (True, "target agent is accepting an AI prompt"))
    monkeypatch.setattr(webapp.yoagent_controller, "start_yoagent_action_result_watcher", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("managed transport result should not start tmux watcher")))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})

    try:
        result, status = webapp.yoagent_controller.execute_yoagent_send_action({"preview_id": "preview-1"}, persist_result=True, start_result_watch=True)
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})

    try:
        result = webapp.yoagent_controller.continue_yoagent_handoff(source_preview, "Session 1 found three changed files.")
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))

    try:
        target, status = webapp.yoagent_controller.yoagent_action_target("7")
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
    marker = webapp.yoagent_controller.yoagent_action_result_marker(target)
    transcript.write_text(
        json.dumps({"timestamp": "2026-06-13T17:41:00Z", "payload": {"type": "agent_message", "message": "The date is June 13, 2026."}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    events = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        result = webapp.yoagent_controller.run_yoagent_action_result_watcher(preview, marker, wait_seconds=1, poll_seconds=0.01)
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

    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_transcript_delta_text", fake_delta)
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        result = webapp.yoagent_controller.run_yoagent_action_result_watcher(preview, {"transcript": target["agent_transcript"]}, wait_seconds=1, poll_seconds=0.01)
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

    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_transcript_delta_text", fake_delta)
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        result = webapp.yoagent_controller.run_yoagent_action_result_watcher(preview, {"transcript": target["agent_transcript"]}, wait_seconds=1, poll_seconds=0.01)
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        assert webapp.yoagent_controller.yoagent_visible_composer_text(visible_text) == "what's the date in UTC"
        assert webapp.yoagent_controller.yoagent_action_visible_result_text(target) == ""
        result = webapp.yoagent_controller.run_yoagent_action_result_watcher(preview, {"transcript": ""}, wait_seconds=1, poll_seconds=0.01)
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
    marker = webapp.yoagent_controller.yoagent_action_result_marker(target)
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_visible_result_text", lambda _target: "stale visible pane text")
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        result = webapp.yoagent_controller.run_yoagent_action_result_watcher(preview, marker, wait_seconds=1, poll_seconds=0.01)
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_transcript_delta_text", lambda _marker: "")
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_visible_result_text", lambda _target: "")
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        webapp.yoagent_controller.register_yoagent_action_wait("wait-1", preview, marker)
        waiting = webapp.yoagent_conversation_payload()["pending_waits"]
        result = webapp.yoagent_controller.run_yoagent_action_result_watcher(preview, marker, watch_id="wait-1", wait_seconds=1, poll_seconds=0.01)
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_transcript_delta_text", lambda _marker: final_text)
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        webapp.yoagent_controller.register_yoagent_action_wait("wait-1", preview, marker)
        result = webapp.yoagent_controller.run_yoagent_action_result_watcher(preview, marker, watch_id="wait-1", wait_seconds=1, poll_seconds=0.01)
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_transcript_delta_text", lambda _marker: "partial transcript delta")
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_result_text_from_transcript_delta", lambda _delta: "Partial answer before timeout.")
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "working", "text": "still working"}))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        webapp.yoagent_controller.register_yoagent_action_wait("wait-1", preview, marker)
        result = webapp.yoagent_controller.run_yoagent_action_result_watcher(preview, marker, watch_id="wait-1", wait_seconds=1, poll_seconds=0.01)
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
        webapp.yoagent_controller.register_yoagent_action_wait("wait-1", preview, marker)
        waiting = webapp.yoagent_conversation_payload()["pending_waits"]
        webapp.yoagent_controller.finish_yoagent_action_wait("wait-1", "yoagent_wait_finished")
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
        webapp.yoagent_controller.register_yoagent_action_wait("wait-1", preview, marker)
        payload, status = webapp.yoagent_controller.clear_yoagent_action_wait("wait-1")
        missing, missing_status = webapp.yoagent_controller.clear_yoagent_action_wait("wait-1")
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
        webapp.yoagent_controller.register_yoagent_action_wait("wait-1", preview_one, {"transcript": "/tmp/claude-session-6.jsonl"})
        webapp.yoagent_controller.register_yoagent_action_wait("wait-2", preview_two, {"transcript": "/tmp/codex-session-7.jsonl"})
        waiting = webapp.yoagent_conversation_payload()["pending_waits"]
        webapp.yoagent_controller.record_yoagent_action_result(preview_one, "Session 6 date result.")
        webapp.yoagent_controller.finish_yoagent_action_wait("wait-1", "yoagent_wait_finished")
        remaining = webapp.yoagent_conversation_payload()["pending_waits"]
        webapp.yoagent_controller.record_yoagent_action_result(preview_two, "Session 7 time result.")
        webapp.yoagent_controller.finish_yoagent_action_wait("wait-2", "yoagent_wait_finished")
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
        webapp.yoagent_controller.register_yoagent_action_wait("wait-1", preview, {"transcript": "/tmp/claude-session-1.jsonl"})
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda target, text, submit=False: sent.append((target, text, submit)) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(webapp.yoagent_controller, "start_yoagent_action_result_watcher", lambda action, marker: watchers.append((action, marker)) or {"started": True})

    try:
        result = webapp.yoagent_controller.continue_yoagent_handoff(preview, "The time is **2026-06-13 Sat 17:35:43 PDT** (Pacific Time).")
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
        prompt = webapp.yoagent_controller.yoagent_handoff_prompt(preview, response)
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
        prompt = webapp.yoagent_controller.yoagent_handoff_prompt(preview, "The cache invalidation path can drop dirty files.")
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
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
        preview, preview_status = webapp.yoagent_controller.create_yoagent_action_preview({"type": "send_prompt", "session": "1", "text": "Use this context: hello Task: answer."})
        result, result_status = webapp.yoagent_controller.execute_yoagent_send_action({"preview_id": preview["id"]}, persist_result=True, start_result_watch=True)
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda _session: (current_target, HTTPStatus.OK))
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_acceptance", lambda _target: (True, "target agent is accepting an AI prompt"))
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale target must not receive paste")))

    try:
        result, status = webapp.yoagent_controller.execute_yoagent_send_action({"preview_id": preview_id})
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
        preview, status = webapp.yoagent_controller.create_yoagent_action_preview({"type": "send_prompt", "session": "1", "text": "what time is it?"})
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
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "ask session 1 what time it is"})
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
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
        preview, preview_status = webapp.yoagent_controller.create_yoagent_action_preview({"type": "send_prompt", "session": "1", "text": "what time is it?"})
        result, status = webapp.yoagent_controller.execute_yoagent_send_action({"preview_id": preview["id"]})
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
        prompt, screen = webapp.yoagent_controller.yoagent_action_pane_status("target-agent", "%77", discovered_sessions={"target-agent": info})
        accepting, acceptance_text = webapp.yoagent_controller.yoagent_action_acceptance({
            "agent_kind": "claude",
            "pane_target": "%77",
            "prompt": prompt,
            "screen": screen,
        })
    finally:
        webapp.control_server.stop()

    assert webapp.yoagent_controller.yoagent_visible_composer_text(visible_text) == ""
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
        preview, preview_status = webapp.yoagent_controller.create_yoagent_action_preview({"type": "send_prompt", "session": "target-agent", "text": "tell me the date"})
        result, result_status = webapp.yoagent_controller.execute_yoagent_send_action({"preview_id": preview["id"]})
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
    monkeypatch.setattr(webapp.yoagent_controller, "start_yoagent_action_result_watcher", lambda preview, marker: watchers.append((preview, marker)) or {"id": "watch-1", "started": True, "wait_seconds": app_module.YOAGENT_ACTION_RESULT_WAIT_SECONDS})

    try:
        preview, preview_status = webapp.yoagent_controller.create_yoagent_action_preview({"type": "send_prompt", "session": "target-agent", "text": "tell me the date", "return_result": True})
        result, result_status = webapp.yoagent_controller.execute_yoagent_send_action({"preview_id": preview["id"]}, persist_result=True, start_result_watch=True)
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
    monkeypatch.setattr(webapp.yoagent_controller, "start_yoagent_action_result_watcher", lambda preview, marker: watchers.append((preview, marker)) or {"id": "watch-1", "started": True, "wait_seconds": app_module.YOAGENT_ACTION_RESULT_WAIT_SECONDS})

    try:
        preview, preview_status = webapp.yoagent_controller.create_yoagent_action_preview({"type": "send_prompt", "session": "target-agent", "text": "tell me the date", "return_result": True})
        result, result_status = webapp.yoagent_controller.execute_yoagent_send_action({"preview_id": preview["id"]}, persist_result=True, start_result_watch=True)
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
        assert webapp.yoagent_controller.yoagent_visible_composer_text(visible_text) == ""
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
        assert webapp.yoagent_controller.yoagent_visible_composer_text(visible_text) == ""
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
        assert webapp.yoagent_controller.yoagent_visible_composer_text(visible_text) == ""
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
        assert webapp.yoagent_controller.yoagent_visible_composer_text(visible_text) == "Use this context: It's 11:17 PM PDT. Task: add 10 minutes and say if that is right."
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
        prompt, screen = webapp.yoagent_controller.yoagent_action_pane_status("1", "%1", discovered_sessions={"1": info})
        accepting, acceptance_text = webapp.yoagent_controller.yoagent_action_acceptance({
            "agent_kind": "claude",
            "pane_target": "%1",
            "prompt": prompt,
            "screen": screen,
        })
    finally:
        webapp.control_server.stop()

    assert webapp.yoagent_controller.yoagent_visible_composer_text(visible_text) == "Write tests for @filename"
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
        assert webapp.yoagent_controller.yoagent_visible_composer_text(claude_text) == ""
        assert webapp.yoagent_controller.yoagent_visible_composer_text(codex_text) == ""
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
        assert webapp.yoagent_controller.yoagent_visible_composer_text(claude_text) == ""
        assert webapp.yoagent_controller.yoagent_visible_composer_text(codex_text) == ""
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
        prompt, screen = webapp.yoagent_controller.yoagent_action_pane_status("1", "%1", discovered_sessions={"1": info})
        accepting, acceptance_text = webapp.yoagent_controller.yoagent_action_acceptance({
            "agent_kind": "codex",
            "pane_target": "%1",
            "prompt": prompt,
            "screen": screen,
        })
    finally:
        webapp.control_server.stop()

    assert webapp.yoagent_controller.yoagent_visible_composer_text(plain_text) == "Summarize recent commits"
    assert webapp.yoagent_controller.yoagent_visible_composer_text(styled_text) == ""
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
        assert webapp.yoagent_controller.yoagent_visible_composer_text(claude_text) == "commit the DYN_PARSER_DEBUG change"
        assert webapp.yoagent_controller.yoagent_visible_composer_text(codex_text) == "Summarize recent commits"
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
        assert webapp.yoagent_controller.yoagent_visible_composer_text(numbered_choice) == ""
        prompt, screen = webapp.yoagent_controller.yoagent_action_pane_status("1", "%1", discovered_sessions={"1": info})
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
        assert webapp.yoagent_controller.yoagent_visible_composer_text(visible_text) == ""
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
        assert webapp.yoagent_controller.yoagent_visible_composer_text(visible_text) == "Write tests for @filename"
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
        result = webapp.yoagent_controller.yoagent_clear_target_composer({"session": "target-agent", "pane_target": "%77"}, wait_seconds=0)
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
        result = webapp.yoagent_controller.yoagent_clear_target_composer({"session": "1", "pane_target": "%1"}, wait_seconds=0)
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
        result = webapp.yoagent_controller.yoagent_clear_target_composer({"session": "1", "pane_target": "%1"}, wait_seconds=0)
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
        result = webapp.yoagent_controller.yoagent_clear_target_composer({"session": "1", "pane_target": "%1"}, wait_seconds=0)
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
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
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "send `date` to tmux session 6, ask me before"})
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "working", "text": "agent is working"}))
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
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "tell session 6 to run date"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "yolomux"
    assert "still working" in payload["answer"]
    assert payload["actions"] == []


def install_fake_yolomux_state(monkeypatch):
    state = {}
    lock = threading.Lock()
    monkeypatch.setattr(app_module, "read_yolomux_state", lambda: dict(state))
    monkeypatch.setattr(app_module, "update_yolomux_state", lambda updates: state.update(updates))
    monkeypatch.setattr(app_module, "mutate_yolomux_state", lambda mutator: lock_and_mutate(lock, state, mutator))
    return state


def lock_and_mutate(lock, state, mutator):
    with lock:
        return mutator(state)


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
        payload, status = webapp.yoagent_controller.create_yoagent_job({"type": "notify_session_idle", "session": "6", "quiet_seconds": 0})
        duplicate, duplicate_status = webapp.yoagent_controller.create_yoagent_job({"type": "notify_session_idle", "session": "6", "quiet_seconds": 0})
        jobs, jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
        cancelled, cancel_status = webapp.yoagent_controller.cancel_yoagent_job(payload["job"]["id"])
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp.yoagent_controller, "execute_yoagent_send_action", lambda payload, **_kwargs: ({
        "ok": True,
        "preview_id": payload["preview_id"],
        "transport": "tmux-legacy",
        "result_source": "transcript-or-screen",
        "result_marker": {"transcript": "/tmp/codex-session-6.jsonl", "size": 10},
    }, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        payload, status = webapp.yoagent_controller.create_yoagent_job({"type": "wait_then_send", "session": "6", "text": "date", "quiet_seconds": 0})
        fired = webapp.yoagent_controller.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
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


def test_yoagent_wait_roster_then_send_job_validates_dedupes_and_redacts(monkeypatch):
    state = install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp(["1", "2", "3", "4"])
    wakes = []
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp.yoagent_controller, "wake_client_event_watcher", lambda: wakes.append(True))
    payload = {
        "type": "wait_roster_then_send",
        "roster": ["1", "2", "3", "4", "2"],
        "action": {"session": "1", "text": "api_key=super-secret-value", "submit": True, "return_result": False},
    }

    try:
        created, created_status = webapp.yoagent_controller.create_yoagent_job(payload)
        duplicate, duplicate_status = webapp.yoagent_controller.create_yoagent_job(payload)
        unknown, unknown_status = webapp.yoagent_controller.create_yoagent_job({
            "type": "wait_roster_then_send",
            "roster": ["1", "9"],
            "action": {"session": "1", "text": "date"},
        })
    finally:
        webapp.control_server.stop()

    assert created_status == HTTPStatus.OK
    assert duplicate_status == HTTPStatus.CONFLICT
    assert duplicate["duplicate"] is True
    job = created["job"]
    assert job["target"] == {"roster": ["1", "2", "3", "4"]}
    assert job["predicate"] == {"type": "all_calm", "quiet_seconds": 10.0}
    assert job["action"]["session"] == "1"
    assert job["action"]["return_result"] is False
    assert job["status"] == "pending_confirmation"
    assert job["action"]["risk_labels"] == ["secret-like-text"]
    assert job["action"]["text"] == "api_key=<redacted>"
    assert job["prompt"] == "api_key=<redacted>"
    assert "super-secret-value" not in json.dumps(state)
    assert wakes == [True]
    assert unknown_status == HTTPStatus.NOT_FOUND
    assert unknown["sessions"] == ["9"]


def test_yoagent_all_calm_requires_idle_or_done_without_draft_or_attention(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    states = {"1": "idle", "2": "done"}

    def target(session):
        state = states[session]
        return {
            "session": session,
            "pane_target": f"%{session}",
            "agent_kind": "claude",
            "transport": "pane-paste",
            "prompt": {},
            "screen": {"key": state, "text": state},
        }, HTTPStatus.OK

    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", target)
    try:
        ready = webapp.yoagent_controller.yoagent_roster_observed_state(["1", "2"], "all_calm")
        blocking_states = {}
        for state in ["working", "needs-input", "approval", "error", "disconnected", "input-draft"]:
            states["2"] = state
            blocking_states[state] = webapp.yoagent_controller.yoagent_roster_observed_state(["1", "2"], "all_calm")
        states["2"] = "input-draft"
        legacy_idle = webapp.yoagent_controller.yoagent_roster_observed_state(["1", "2"], "all_idle")
    finally:
        webapp.control_server.stop()

    assert ready == {"ready": True, "state": "all_calm", "states": {"1": "idle", "2": "done"}, "blockers": []}
    for state, observed in blocking_states.items():
        assert observed["ready"] is False
        assert observed["state"] == "waiting"
        assert observed["states"]["2"] == state
        assert observed["blockers"] == ["2"]
    assert legacy_idle["ready"] is True


def test_yoagent_roster_calm_quiet_window_resets_after_activity(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    state = {"2": "idle"}
    sent = []

    def target(session):
        screen = state.get(session, "idle")
        return {
            "session": session,
            "pane_target": f"%{session}",
            "agent_kind": "codex",
            "transport": "pane-paste",
            "prompt": {},
            "screen": {"key": screen, "text": screen},
        }, HTTPStatus.OK

    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", target)
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        webapp.yoagent_controller,
        "execute_yoagent_send_action",
        lambda payload, **_kwargs: sent.append(payload) or ({"ok": True, "sent": True}, HTTPStatus.OK),
    )
    try:
        created, created_status = webapp.yoagent_controller.create_yoagent_job({
            "type": "wait_roster_then_send",
            "roster": ["1", "2"],
            "action": {"session": "1", "text": "/dyn-tps-report 1 2 EOD"},
        })
        base = created["job"]["created_ts"]
        clock = {"value": base}
        monkeypatch.setattr(controller_module.time, "time", lambda: clock["value"])
        first = webapp.yoagent_controller.poll_yoagent_jobs_once()
        clock["value"] = base + 9.0
        before_quiet = webapp.yoagent_controller.poll_yoagent_jobs_once()
        state["2"] = "working"
        clock["value"] = base + 9.5
        activity = webapp.yoagent_controller.poll_yoagent_jobs_once()
        state["2"] = "idle"
        clock["value"] = base + 10.0
        reset = webapp.yoagent_controller.poll_yoagent_jobs_once()
        clock["value"] = base + 19.9
        still_quiet = webapp.yoagent_controller.poll_yoagent_jobs_once()
        clock["value"] = base + 20.1
        fired = webapp.yoagent_controller.poll_yoagent_jobs_once()
    finally:
        webapp.control_server.stop()

    assert created_status == HTTPStatus.OK
    assert first == before_quiet == activity == reset == still_quiet == []
    assert fired == [created["job"]["id"]]
    assert len(sent) == 1


def test_yoagent_roster_job_revalidates_destination_and_resets_wait_without_sending(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    phase = {"value": "becomes_busy"}
    calls = []
    sends = []

    def target(session):
        calls.append(session)
        state = "idle"
        if phase["value"] == "becomes_busy" and len(calls) == 3 and session == "1":
            state = "working"
        return {
            "session": session,
            "pane_target": f"%{session}",
            "agent_kind": "codex",
            "transport": "pane-paste",
            "prompt": {},
            "screen": {"key": state, "text": state},
        }, HTTPStatus.OK

    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", target)
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        webapp.yoagent_controller,
        "execute_yoagent_send_action",
        lambda payload, **_kwargs: sends.append(payload) or ({"ok": True, "sent": True}, HTTPStatus.OK),
    )

    try:
        created, created_status = webapp.yoagent_controller.create_yoagent_job({
            "type": "wait_roster_then_send",
            "roster": ["1", "2"],
            "action": {"session": "1", "text": "/dyn-tps-report 1 2 EOD"},
            "quiet_seconds": 0,
        })
        first = webapp.yoagent_controller.poll_yoagent_jobs_once()
        waiting, _waiting_status = webapp.yoagent_controller.yoagent_jobs_payload()
        sends_before_idle = list(sends)
        phase["value"] = "idle"
        second = webapp.yoagent_controller.poll_yoagent_jobs_once()
        fired, _fired_status = webapp.yoagent_controller.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert created_status == HTTPStatus.OK
    assert first == []
    assert sends_before_idle == []
    assert waiting["jobs"][0]["status"] == "queued"
    assert waiting["jobs"][0]["last_observed_state"]["ready"] is False
    assert second == [created["job"]["id"]]
    assert fired["jobs"][0]["status"] == "fired"
    assert len(sends) == 1


def test_yoagent_roster_job_claim_allows_only_one_overlapping_send(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    send_started = threading.Event()
    allow_send_finish = threading.Event()
    sends = []

    def target(session):
        return {
            "session": session,
            "pane_target": f"%{session}",
            "agent_kind": "codex",
            "transport": "pane-paste",
            "prompt": {},
            "screen": {"key": "idle", "text": "idle"},
        }, HTTPStatus.OK

    def send(payload, **_kwargs):
        sends.append(payload)
        send_started.set()
        assert allow_send_finish.wait(2.0)
        return {"ok": True, "sent": True}, HTTPStatus.OK

    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", target)
    monkeypatch.setattr(webapp.yoagent_controller, "execute_yoagent_send_action", send)
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    try:
        created, created_status = webapp.yoagent_controller.create_yoagent_job({
            "type": "wait_roster_then_send",
            "roster": ["1", "2"],
            "action": {"session": "1", "text": "/dyn-tps-report 1 2 EOD"},
            "quiet_seconds": 0,
        })
        first = threading.Thread(target=webapp.yoagent_controller.poll_yoagent_jobs_once)
        first.start()
        assert send_started.wait(2.0)
        second = webapp.yoagent_controller.poll_yoagent_jobs_once()
        allow_send_finish.set()
        first.join(timeout=2.0)
        jobs, jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert created_status == HTTPStatus.OK
    assert first.is_alive() is False
    assert second == []
    assert jobs_status == HTTPStatus.OK
    assert jobs["jobs"][0]["status"] == "fired"
    assert len(sends) == 1


def test_yoagent_roster_job_shared_state_claim_allows_only_one_server_send(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    first_app = app_module.TmuxWebtermApp(["1", "2"])
    second_app = None
    send_started = threading.Event()
    allow_send_finish = threading.Event()
    sends = []

    def target(session):
        return {
            "session": session,
            "pane_target": f"%{session}",
            "agent_kind": "codex",
            "transport": "pane-paste",
            "prompt": {},
            "screen": {"key": "idle", "text": "idle"},
        }, HTTPStatus.OK

    def first_send(payload, **_kwargs):
        sends.append(("first", payload))
        send_started.set()
        assert allow_send_finish.wait(2.0)
        return {"ok": True, "sent": True}, HTTPStatus.OK

    def second_send(payload, **_kwargs):
        sends.append(("second", payload))
        return {"ok": True, "sent": True}, HTTPStatus.OK

    try:
        monkeypatch.setattr(first_app, "log_event", lambda *args, **kwargs: {"time": "event"})
        monkeypatch.setattr(first_app, "publish_client_event", lambda *args, **kwargs: {})
        created, created_status = first_app.yoagent_controller.create_yoagent_job({
            "type": "wait_roster_then_send",
            "roster": ["1", "2"],
            "action": {"session": "1", "text": "/dyn-tps-report 1 2 EOD"},
            "quiet_seconds": 0,
        })
        second_app = app_module.TmuxWebtermApp(["1", "2"])
        for webapp, sender in [(first_app, first_send), (second_app, second_send)]:
            monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", target)
            monkeypatch.setattr(webapp.yoagent_controller, "execute_yoagent_send_action", sender)
            monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
            monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
        first_results = []
        thread = threading.Thread(target=lambda: first_results.append(first_app.yoagent_controller.poll_yoagent_jobs_once()))
        thread.start()
        assert send_started.wait(2.0)
        second_results = second_app.yoagent_controller.poll_yoagent_jobs_once()
        allow_send_finish.set()
        thread.join(timeout=2.0)
        jobs, jobs_status = first_app.yoagent_controller.yoagent_jobs_payload()
    finally:
        first_app.control_server.stop()
        if second_app is not None:
            second_app.control_server.stop()

    assert created_status == HTTPStatus.OK
    assert first_results == [[created["job"]["id"]]]
    assert second_results == []
    assert jobs_status == HTTPStatus.OK
    assert jobs["jobs"][0]["status"] == "fired"
    assert [source for source, _payload in sends] == ["first"]


def test_yoagent_roster_job_fails_for_missing_watched_session_and_cancels_by_roster_member(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    events = []

    def target(session):
        if session == "2":
            return {"error": "tmux session 2 disappeared"}, HTTPStatus.NOT_FOUND
        return {
            "session": session,
            "pane_target": f"%{session}",
            "agent_kind": "codex",
            "transport": "pane-paste",
            "prompt": {},
            "screen": {"key": "idle", "text": "idle"},
        }, HTTPStatus.OK

    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", target)
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {})
    try:
        failed_job, failed_status = webapp.yoagent_controller.create_yoagent_job({
            "type": "wait_roster_then_send",
            "roster": ["1", "2"],
            "action": {"session": "1", "text": "/dyn-tps-report 1 2 EOD"},
            "quiet_seconds": 0,
        })
        fired = webapp.yoagent_controller.poll_yoagent_jobs_once()
        failed_jobs, _failed_jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
        queued_job, queued_status = webapp.yoagent_controller.create_yoagent_job({
            "type": "wait_roster_then_send",
            "roster": ["1", "2"],
            "action": {"session": "1", "text": "/dyn-tps-report queued EOD"},
        })
        cancelled, cancel_status = webapp.yoagent_controller.cancel_yoagent_jobs_for_session("2")
    finally:
        webapp.control_server.stop()

    assert failed_status == HTTPStatus.OK
    assert fired == []
    assert failed_jobs["jobs"][0]["id"] == failed_job["job"]["id"]
    assert failed_jobs["jobs"][0]["status"] == "failed"
    assert "target session is missing: 2" in failed_jobs["jobs"][0]["result"]["error"]
    assert queued_status == HTTPStatus.OK
    assert cancel_status == HTTPStatus.OK
    assert cancelled["count"] == 1
    assert cancelled["jobs"][0]["id"] == queued_job["job"]["id"]
    assert any(event[0] == "yoagent_jobs_changed" and event[1].get("reason") == "yoagent_job_failed" for event in events)


def test_yoagent_roster_job_times_out_without_sending(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    sends = []
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda _session: (_ for _ in ()).throw(AssertionError("expired job must not inspect or send")))
    monkeypatch.setattr(webapp.yoagent_controller, "execute_yoagent_send_action", lambda payload, **_kwargs: sends.append(payload) or ({"ok": True}, HTTPStatus.OK))
    try:
        created, created_status = webapp.yoagent_controller.create_yoagent_job({
            "type": "wait_roster_then_send",
            "roster": ["1", "2"],
            "action": {"session": "1", "text": "/dyn-tps-report 1 2 EOD"},
        })
        with webapp.yoagent_job_lock:
            webapp.yoagent_jobs[created["job"]["id"]]["timeout_ts"] = time.time() - 1
        fired = webapp.yoagent_controller.poll_yoagent_jobs_once()
        jobs, jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert created_status == HTTPStatus.OK
    assert fired == []
    assert jobs_status == HTTPStatus.OK
    assert jobs["jobs"][0]["status"] == "timed_out"
    assert sends == []


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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda _session: (dict(target), HTTPStatus.OK))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        transport_module,
        "send_prompt",
        lambda send_target, text, **kwargs: send_calls.append((send_target, text, kwargs)) or fake_agent_tui_send_result(),
    )
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("direct send must go through agent_tui send_prompt")))

    try:
        preview, preview_status = webapp.yoagent_controller.create_yoagent_action_preview({"type": "send_prompt", "session": "6", "text": "date"})
        result, status = webapp.yoagent_controller.execute_yoagent_send_action({"preview_id": preview["id"]})
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda _session: (dict(target), HTTPStatus.OK))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(controller_module, "tmux_move_to_option", lambda pane, option, selected_option=None: moved.append((pane, option, selected_option)))
    monkeypatch.setattr(controller_module, "tmux_send_enter", lambda pane: entered.append(pane))
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda _target, visible_only=False: "  1. Approve\n❯ 2. Reject\nEnter to select · ↑/↓ to navigate · Esc to cancel")
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("prompt answers must not paste free text")))

    try:
        preview, preview_status = webapp.yoagent_controller.create_yoagent_action_preview({"type": "send_prompt", "session": "1", "text": "2"})
        result, status = webapp.yoagent_controller.execute_yoagent_send_action({"preview_id": preview["id"]}, persist_result=False)
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
        result = webapp.yoagent_controller.record_yoagent_action_result(
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda _session: (dict(target), HTTPStatus.OK))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("prompt targets must not receive free text")))

    try:
        response, status = webapp.yoagent_controller.yoagent_chat({"message": "tell session 1 to run date"}, access_role="admin")
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda _session: (dict(target), HTTPStatus.OK))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})
    monkeypatch.setattr(
        transport_module,
        "send_prompt",
        lambda send_target, text, **kwargs: send_calls.append((send_target, text, kwargs)) or fake_agent_tui_send_result(),
    )
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("wait-then-send must go through agent_tui send_prompt")))

    try:
        payload, status = webapp.yoagent_controller.create_yoagent_job({"type": "wait_then_send", "session": "6", "text": "date", "quiet_seconds": 0})
        fired = webapp.yoagent_controller.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {"generated_at": "now", "session_order": ["6"], "global": {"headline": "Session 6 is idle."}, "sessions": {}, "errors": []})
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("risky target text must wait for confirmation")))

    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "tell session 6 to run token=super-secret-value"})
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
        payload, status = webapp.yoagent_controller.create_yoagent_job({"type": "wait_then_send", "session": "6", "text": "api_key=super-secret-value", "quiet_seconds": 0})
        fired = webapp.yoagent_controller.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
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

    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", target)
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        payload, status = webapp.yoagent_controller.create_yoagent_job({"type": "notify_all_idle", "quiet_seconds": 0})
        first = webapp.yoagent_controller.poll_yoagent_jobs_once()
        waiting, _waiting_status = webapp.yoagent_controller.yoagent_jobs_payload()
        states["2"] = "idle"
        second = webapp.yoagent_controller.poll_yoagent_jobs_once()
        fired, _fired_status = webapp.yoagent_controller.yoagent_jobs_payload()
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

    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", target)
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        needs_input, needs_status = webapp.yoagent_controller.create_yoagent_job({"type": "notify_session_needs_input", "session": "6", "quiet_seconds": 0})
        blocked, blocked_status = webapp.yoagent_controller.create_yoagent_job({"type": "notify_session_blocked", "session": "6", "quiet_seconds": 0})
        first = webapp.yoagent_controller.poll_yoagent_jobs_once()
        state.update({"screen": "needs-input", "question": "Which branch should I use?"})
        needs_fired = webapp.yoagent_controller.poll_yoagent_jobs_once()
        waiting, _waiting_status = webapp.yoagent_controller.yoagent_jobs_payload()
        state.update({"screen": "idle", "prompt_visible": True, "question": "Do you want to proceed?"})
        blocked_fired = webapp.yoagent_controller.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
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

    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", target)
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        payload, status = webapp.yoagent_controller.create_yoagent_job({"type": "notify_session_done_after_working", "session": "6", "quiet_seconds": 0})
        already_idle = webapp.yoagent_controller.poll_yoagent_jobs_once()
        idle_jobs, _idle_status = webapp.yoagent_controller.yoagent_jobs_payload()
        state["screen"] = "working"
        working = webapp.yoagent_controller.poll_yoagent_jobs_once()
        state["screen"] = "idle"
        finished = webapp.yoagent_controller.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
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
        idle, idle_status = webapp.yoagent_controller.create_yoagent_job({"type": "notify_session_idle", "session": "6"})
        blocked, blocked_status = webapp.yoagent_controller.create_yoagent_job({"type": "notify_session_blocked", "session": "6"})
        other, other_status = webapp.yoagent_controller.create_yoagent_job({"type": "notify_session_idle", "session": "7"})
        cancelled, cancel_status = webapp.yoagent_controller.cancel_yoagent_jobs_for_session("6")
        jobs, _jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
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
        payload, status = first_app.yoagent_controller.create_yoagent_job({"type": "notify_session_idle", "session": "6"})
        second_app = app_module.TmuxWebtermApp(["6"])
        jobs, jobs_status = second_app.yoagent_controller.yoagent_jobs_payload()
    finally:
        first_app.control_server.stop()
        if second_app is not None:
            second_app.control_server.stop()

    assert status == HTTPStatus.OK
    assert jobs_status == HTTPStatus.OK
    assert jobs["jobs"][0]["id"] == payload["job"]["id"]
    assert jobs["jobs"][0]["status"] == "queued"


def test_yoagent_roster_job_recovers_queued_and_suppresses_interrupted_firing_retry(monkeypatch):
    state = install_fake_yolomux_state(monkeypatch)
    first_app = app_module.TmuxWebtermApp(["1", "2"])
    second_app = None
    third_app = None
    monkeypatch.setattr(first_app, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(first_app, "publish_client_event", lambda *args, **kwargs: {})
    try:
        created, created_status = first_app.yoagent_controller.create_yoagent_job({
            "type": "wait_roster_then_send",
            "roster": ["1", "2"],
            "action": {"session": "1", "text": "/dyn-tps-report 1 2 EOD"},
        })
        second_app = app_module.TmuxWebtermApp(["1", "2"])
        queued, queued_status = second_app.yoagent_controller.yoagent_jobs_payload()
        state[app_module.YOAGENT_JOBS_STATE_KEY][created["job"]["id"]]["status"] = "firing"
        third_app = app_module.TmuxWebtermApp(["1", "2"])
        recovered, recovered_status = third_app.yoagent_controller.yoagent_jobs_payload()
        fired = third_app.yoagent_controller.poll_yoagent_jobs_once()
    finally:
        first_app.control_server.stop()
        if second_app is not None:
            second_app.control_server.stop()
        if third_app is not None:
            third_app.control_server.stop()

    assert created_status == HTTPStatus.OK
    assert queued_status == HTTPStatus.OK
    assert queued["jobs"][0]["status"] == "queued"
    assert recovered_status == HTTPStatus.OK
    assert recovered["jobs"][0]["status"] == "failed"
    assert "automatic retry is suppressed" in recovered["jobs"][0]["error"]
    assert state[app_module.YOAGENT_JOBS_STATE_KEY][created["job"]["id"]]["status"] == "failed"
    assert fired == []


def test_yoagent_job_fails_and_notifies_when_target_disappears(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    events = []
    logged = []
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: logged.append((args, kwargs)) or {"time": f"event-{len(logged)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        payload, status = webapp.yoagent_controller.create_yoagent_job({"type": "wait_then_send", "session": "6", "text": "date", "quiet_seconds": 0})
        monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda session: ({"error": "unknown session: 6"}, HTTPStatus.NOT_FOUND))
        fired = webapp.yoagent_controller.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})

    try:
        preview, status = webapp.yoagent_controller.create_yoagent_action_preview({"type": "send_prompt", "session": "6", "text": "date"})
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
    monkeypatch.setattr(webapp.yoagent_controller, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {"generated_at": "now", "session_order": ["6"], "global": {"headline": "Session 6 is working."}, "sessions": {}, "errors": []})
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("queued job must not paste now")))

    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "wait for session 6 to finish, then tell it to run date"})
        jobs, _jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
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
        created, created_status = webapp.yoagent_controller.create_yoagent_job({"type": "notify_session_idle", "session": "6"})
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "cancel pending jobs for session 6"})
        jobs, _jobs_status = webapp.yoagent_controller.yoagent_jobs_payload()
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
    monkeypatch.setattr(
        webapp,
        "activity_summary_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("static capability answers must not call the disabled activity summary")
        ),
    )
    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "Can YO!agent read, poll, monitor, notify, and send commands to tmux panes?"})
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
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "create skill local-checks description: Ask idle agents to run focused tests."})
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
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_claude_cli", lambda prompt, session_id="", resume=False, **_kwargs: ("", "Error: not logged in. Run claude login."))
    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "status?"})
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
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_codex_cli", lambda *_args, **_kwargs: ("", "model overloaded", ""))
    try:
        answer, fallback_reason, cli = webapp.yoagent_controller.run_yoagent_direct_prompt_backend("codex", "status?", locale="en")
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
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_codex_app_server", fake_codex)
    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "estado?", "locale": "zh-Hant"})
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
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_codex_app_server", lambda prompt, session_id="", resume=False, settings=None, stream_callback=None, request_id="": ("codex answer", "", "codex-session-1", {"transport": "codex-app-server", "persistent": True}))
    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "status?"})
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

    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_cli_backend", fake_backend)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(webapp.yoagent_controller.yoagent_chat, {"message": "first"})
            assert entered_first.wait(1)
            second = executor.submit(webapp.yoagent_controller.yoagent_chat, {"message": "second"})
            time.sleep(0.05)
            assert started_questions == ["first"]
            release_first.set()
            # Full Mac gates run browser and stats lanes concurrently on a
            # 10-core, memory-constrained host. Keep this synchronization
            # bounded without mistaking scheduler delay for deadlock.
            first_payload, first_status = first.result(timeout=10)
            second_payload, second_status = second.result(timeout=10)
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
    real_popen = transport_module.subprocess.Popen

    def fake_popen(args, **kwargs):
        if list(args)[:4] != ["codex", "app-server", "--listen", "stdio://"]:
            return real_popen(args, **kwargs)
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
        first, first_reason, first_status = webapp.yoagent_controller.run_yoagent_cli_backend("codex", "first?", activity, settings, [])
        second, second_reason, second_status = webapp.yoagent_controller.run_yoagent_cli_backend("codex", "second?", activity, settings, [{"role": "user", "content": "first?"}])
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
    real_popen = transport_module.subprocess.Popen

    def fake_popen(args, **kwargs):
        if list(args)[:4] != ["codex", "app-server", "--listen", "stdio://"]:
            return real_popen(args, **kwargs)
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
        prewarm, prewarm_status = webapp.yoagent_controller.start_yoagent_backend_prewarm(reason="server_start")
        for _attempt in range(100):
            with webapp.yoagent_prewarm_lock:
                if not webapp.yoagent_prewarm_record.prewarm_running:
                    prewarm_state = dict(webapp.yoagent_prewarm_record.prewarm_status)
                    break
            time.sleep(0.01)
        else:
            raise AssertionError("prewarm did not finish")
        answer, reason, status = webapp.yoagent_controller.run_yoagent_cli_backend("codex", "first after idle?", activity, settings, [], include_activity_context=False)
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
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_codex_cli", lambda prompt, session_id="", resume=False, **_kwargs: ("exec fallback answer", "", "exec-thread"))
    try:
        answer, reason, status = webapp.yoagent_controller.run_yoagent_cli_backend("codex", "status?", activity, {"codex_model": "gpt-5.4-mini", "codex_effort": "low"}, [])
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
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_claude_cli", lambda prompt, session_id="", resume=False, **_kwargs: ("I'm blocked — the harness denied access to ~/.claude/projects/**/*.jsonl.", ""))
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Your most recent work is about editor fixes."},
        "sessions": {},
        "errors": [],
    }
    try:
        answer, reason, status = webapp.yoagent_controller.run_yoagent_cli_backend("claude", "status?", activity, {}, [])
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
        assert webapp.yoagent_controller.reset_yoagent_chat()["ok"] is True
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
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "what changed?"})
        persisted = webapp.yoagent_conversation_payload()
        reset = webapp.yoagent_controller.reset_yoagent_chat()
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
        history = webapp.yoagent_controller.yoagent_prompt_history(
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
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_cli_backend", fake_backend)
    try:
        first, first_status = webapp.yoagent_controller.yoagent_chat({"message": "hello"})
        second, second_status = webapp.yoagent_controller.yoagent_chat({"message": "what model are you?", "history": [{"role": "user", "content": "stale frontend"}]})
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

    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_cli_backend", fake_backend)
    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "what is the weather in Cupertino now?"})
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

    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_codex_cli", fake_codex_cli)
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_codex_app_server", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("live external data must use search-capable codex exec")))
    try:
        answer, reason, status = webapp.yoagent_controller.run_yoagent_cli_backend(
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
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_cli_backend", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("missing tools should not call a model backend")))
    try:
        payload, status = webapp.yoagent_controller.yoagent_chat({"message": "what is the weather in Cupertino now?"})
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

    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_cli_backend", fake_backend)
    try:
        payload, status = webapp.yoagent_controller.yoagent_prewarm({"visible": True, "locale": "en"})
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


def test_yoagent_prewarm_lifecycle_uses_one_record():
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        assert isinstance(webapp.yoagent_prewarm_record, app_module.YoagentPrewarmRecord)
        assert {
            "yoagent_prewarm_running",
            "yoagent_prewarm_status",
            "yoagent_startup_response_running",
        }.isdisjoint(webapp.__dict__)
    finally:
        webapp.control_server.stop()


def test_yoagent_reset_invalidates_blocked_startup_and_blocks_reset_overlap(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    old_started = threading.Event()
    release_old = threading.Event()
    reset_clearing = threading.Event()
    release_reset = threading.Event()
    stream_events = []
    real_clear_messages = app_module.yoagent_conversation.clear_messages

    monkeypatch.setattr(webapp.yoagent_controller, "maybe_start_yoagent_summary_worker", lambda: None)
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "codex", "invocation": "cli"})
    monkeypatch.setattr(app_module, "resolve_yoagent_backend", lambda backend: "codex")
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {"generated_at": "now", "session_order": [], "sessions": {}, "errors": []})
    monkeypatch.setattr(webapp, "publish_yoagent_stream_delta", lambda *args, **kwargs: stream_events.append((args, kwargs)))

    def blocked_backend(*_args, **_kwargs):
        old_started.set()
        assert release_old.wait(timeout=3)
        return "obsolete startup answer", "", {"transport": "codex-app-server"}

    def blocked_clear_messages():
        reset_clearing.set()
        assert release_reset.wait(timeout=3)
        real_clear_messages()

    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_cli_backend", blocked_backend)
    monkeypatch.setattr(app_module.yoagent_conversation, "clear_messages", blocked_clear_messages)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            old_future = executor.submit(webapp.yoagent_controller.yoagent_prewarm, {"visible": True})
            assert old_started.wait(timeout=2)
            reset_future = executor.submit(webapp.yoagent_controller.reset_yoagent_chat)
            assert reset_clearing.wait(timeout=2)

            overlap_payload, overlap_status = webapp.yoagent_controller.yoagent_prewarm({"visible": True})
            assert overlap_status == HTTPStatus.ACCEPTED
            assert overlap_payload["started"] is False
            assert overlap_payload["reason"] == "conversation reset in progress"

            release_reset.set()
            reset_payload = reset_future.result(timeout=2)
            release_old.set()
            old_payload, old_status = old_future.result(timeout=2)
    finally:
        release_reset.set()
        release_old.set()
        webapp.control_server.stop()

    assert reset_payload["conversation"]["messages"] == []
    assert old_status == HTTPStatus.OK
    assert old_payload["aborted"] is True
    assert old_payload.get("answer", "") == ""
    assert old_payload["conversation"]["messages"] == []
    assert webapp.yoagent_conversation_payload()["messages"] == []
    assert any(event[1].get("phase") == "stopped" and event[1].get("aborted") is True for event in stream_events)


def test_yoagent_replacement_startup_survives_stale_request_finally(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    old_started = threading.Event()
    replacement_started = threading.Event()
    release_old = threading.Event()
    release_replacement = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    monkeypatch.setattr(webapp.yoagent_controller, "maybe_start_yoagent_summary_worker", lambda: None)
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "codex", "invocation": "cli"})
    monkeypatch.setattr(app_module, "resolve_yoagent_backend", lambda backend: "codex")
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {"generated_at": "now", "session_order": [], "sessions": {}, "errors": []})

    def blocked_backend(*_args, **_kwargs):
        nonlocal call_count
        with call_lock:
            call_count += 1
            call_index = call_count
        if call_index == 1:
            old_started.set()
            assert release_old.wait(timeout=3)
            return "obsolete startup answer", "", {"transport": "codex-app-server"}
        replacement_started.set()
        assert release_replacement.wait(timeout=3)
        return "replacement startup answer", "", {"transport": "codex-app-server"}

    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_cli_backend", blocked_backend)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            old_future = executor.submit(webapp.yoagent_controller.yoagent_prewarm, {"visible": True})
            assert old_started.wait(timeout=2)
            assert webapp.yoagent_controller.reset_yoagent_chat()["conversation"]["messages"] == []

            replacement_future = executor.submit(webapp.yoagent_controller.yoagent_prewarm, {"visible": True})
            assert replacement_started.wait(timeout=2)
            with webapp.yoagent_prewarm_lock:
                replacement_generation = webapp.yoagent_prewarm_record.active_startup_generation
            assert replacement_generation is not None

            release_old.set()
            old_payload, old_status = old_future.result(timeout=2)
            with webapp.yoagent_prewarm_lock:
                assert webapp.yoagent_prewarm_record.active_startup_generation == replacement_generation

            release_replacement.set()
            replacement_payload, replacement_status = replacement_future.result(timeout=2)
    finally:
        release_old.set()
        release_replacement.set()
        webapp.control_server.stop()

    assert old_status == HTTPStatus.OK and old_payload["aborted"] is True
    assert replacement_status == HTTPStatus.OK and replacement_payload["answer"] == "replacement startup answer"
    assert [message["content"] for message in replacement_payload["conversation"]["messages"]] == ["replacement startup answer"]
    assert [message["content"] for message in webapp.yoagent_conversation_payload()["messages"]] == ["replacement startup answer"]
    with webapp.yoagent_prewarm_lock:
        assert webapp.yoagent_prewarm_record.active_startup_generation is None


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
    monkeypatch.setattr(first_app.yoagent_controller, "run_yoagent_claude_cli", lambda prompt, session_id="", resume=False, **_kwargs: ("answer", ""))
    try:
        answer, reason, status = first_app.yoagent_controller.run_yoagent_cli_backend("claude", "status?", activity, {}, [])
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

    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_claude_cli", fake_claude)
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
        first, first_reason, first_status = webapp.yoagent_controller.run_yoagent_cli_backend("claude", "first?", activity, settings, [])
        second, second_reason, second_status = webapp.yoagent_controller.run_yoagent_cli_backend("claude", "second?", activity, settings, [{"role": "user", "content": "first?"}])
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

    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_codex_app_server", fake_codex)
    try:
        answer, reason, status = webapp.yoagent_controller.run_yoagent_cli_backend("codex", "summarize this project", activity, {}, [])
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

    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_claude_cli", fake_claude)
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "sessions": {},
        "errors": [],
    }
    try:
        answer, reason, status = webapp.yoagent_controller.run_yoagent_cli_backend("claude", "status?", activity, {}, [])
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
        first_answer, first_error, first_session = webapp.yoagent_controller.run_yoagent_codex_cli("first", resume=False, settings=settings)
        second_answer, second_error, second_session = webapp.yoagent_controller.run_yoagent_codex_cli("second", session_id=first_session, resume=True, settings=settings)
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


def test_terminal_upload_uses_authenticated_users_central_session_tree(monkeypatch, tmp_path):
    monkeypatch.setattr(uploads_module, "UPLOAD_TMP_BASE", tmp_path / "tmp")
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"retention_days": 7, "filename_template": "{name}{ext}"}}})
    webapp = app_module.TmuxWebtermApp(["s"])
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    try:
        payload, status = webapp.upload_files("s", [UploadedFile(filename="screen.png", content=b"png")], auth_username="alice")
    finally:
        webapp.control_server.stop()

    target = tmp_path / "tmp" / "yolomux.alice" / "uploads" / "s"
    assert status == HTTPStatus.OK
    assert payload["target_dir"] == str(target)
    assert payload["target_source"] == "central_user_uploads"
    assert Path(payload["files"][0]["path"]).read_bytes() == b"png"
    assert stat.S_IMODE(target.parent.parent.stat().st_mode) == 0o700
    assert not (worktree / ".uploads").exists()


def test_editor_upload_uses_absolute_central_path_not_document_relative(monkeypatch, tmp_path):
    monkeypatch.setattr(uploads_module, "UPLOAD_TMP_BASE", tmp_path / "tmp")
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"retention_days": 7, "filename_template": "{name}{ext}"}}})
    webapp = app_module.TmuxWebtermApp([])
    docs = tmp_path / "docs"
    docs.mkdir()
    editor_path = docs / "note.md"
    editor_path.write_text("# Note\n", encoding="utf-8")
    try:
        payload, status = webapp.upload_editor_files(
            [UploadedFile(filename="screen.png", content=b"png")],
            editor_path=str(editor_path),
            auth_username="alice",
        )
    finally:
        webapp.control_server.stop()

    target = tmp_path / "tmp" / "yolomux.alice" / "uploads" / "editor" / "screen.png"
    assert status == HTTPStatus.OK
    assert payload["target_dir"] == str(target.parent)
    assert payload["base_dir"] == str(docs)
    assert payload["files"][0]["relative_path"] == str(target)
    assert target.read_bytes() == b"png"
    assert not (docs / ".uploads").exists()


def test_multiple_servers_reserve_shared_upload_names_atomically(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"filename_template": "{name}-{seq}{ext}"}}})
    target = tmp_path / "uploads"
    target.mkdir()
    apps = [object.__new__(app_module.TmuxWebtermApp) for _index in range(8)]

    def save(index):
        return apps[index]._save_uploaded_files(target, [UploadedFile(filename="same.png", content=str(index).encode("ascii"))])

    with ThreadPoolExecutor(max_workers=len(apps)) as pool:
        results = list(pool.map(save, range(len(apps))))

    assert all(status == HTTPStatus.OK and error is None for _saved, error, status in results)
    paths = [Path(saved[0]["path"]) for saved, _error, _status in results]
    assert len(set(paths)) == len(apps)
    assert {path.read_text(encoding="ascii") for path in paths} == {str(index) for index in range(len(apps))}
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths)


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
    monkeypatch.setattr(app_module, "ensure_xterm_runtime_assets", lambda _root: (False, "tracked xterm vendor asset is missing"))
    monkeypatch.setattr(webapp, "_spawn_self_restart", lambda: (_ for _ in ()).throw(AssertionError("must not restart without xterm assets")))

    result = webapp.perform_self_update()

    assert result["ok"] is False
    assert result["restarting"] is False
    assert "xterm" in result["error"]
    assert result["user_message"]["key"] == "update.result.assetsUnavailable"
    assert result["user_message"]["fallback"] == result["error"]
    assert "validate tracked xterm vendor assets" in result["plan"]


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


def test_self_update_static_build_failure_stops_before_restart(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp.__new__(app_module.TmuxWebtermApp)
    monkeypatch.setattr(app_module.common, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_module.common, "git", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(app_module, "ensure_xterm_runtime_assets", lambda _root: (True, ""))
    monkeypatch.setattr(app_module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="bundle generation failed"))
    monkeypatch.setattr(webapp, "_spawn_self_restart", lambda: (_ for _ in ()).throw(AssertionError("must not restart after a failed static build")))

    result = webapp.perform_self_update()

    assert result["ok"] is False
    assert result["restarting"] is False
    assert result["error"] == "static build failed: bundle generation failed"
    assert result["user_message"]["key"] == "update.result.blocked"
    assert result["user_message"]["fallback"] == result["error"]


def test_self_update_static_build_timeout_stops_before_restart(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp.__new__(app_module.TmuxWebtermApp)
    monkeypatch.setattr(app_module.common, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_module.common, "git", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(app_module, "ensure_xterm_runtime_assets", lambda _root: (True, ""))
    monkeypatch.setattr(app_module.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(app_module.subprocess.TimeoutExpired(args[0], 120)))
    monkeypatch.setattr(webapp, "_spawn_self_restart", lambda: (_ for _ in ()).throw(AssertionError("must not restart after a timed-out static build")))

    result = webapp.perform_self_update()

    assert result["ok"] is False
    assert result["restarting"] is False
    assert result["error"].startswith("static build failed:")
    assert "timed out" in result["error"]


def test_update_notification_iteration_deduplicates_initialized_target():
    webapp = app_module.TmuxWebtermApp([])
    events = []
    webapp.update_status_payload = lambda dryrun=False: {"available": True, "notify": True, "target": "0.4.0", "dryrun": dryrun}
    webapp.publish_client_event = lambda event, payload, **details: events.append((event, payload, details))

    assert webapp.update_check_thread is None
    assert webapp._update_last_target is None
    webapp.publish_update_notification_if_available()
    webapp.publish_update_notification_if_available()

    assert webapp._update_last_target == "0.4.0"
    assert events == [("update_available", {"available": True, "notify": True, "target": "0.4.0", "dryrun": False}, {"trigger": "update-check"})]


def test_update_check_loop_logs_iteration_failure(monkeypatch, caplog):
    webapp = app_module.TmuxWebtermApp.__new__(app_module.TmuxWebtermApp)
    webapp.update_check_record = app_module.UpdateCheckRecord()
    webapp.updates_settings = lambda: {"notify_level": "patch", "check_interval_minutes": 1}
    webapp.update_notify_level = lambda _section: "patch"
    webapp.publish_update_notification_if_available = lambda: (_ for _ in ()).throw(RuntimeError("update probe exploded"))
    monkeypatch.setattr(app_module.time, "sleep", lambda _seconds: (_ for _ in ()).throw(StopIteration))

    with caplog.at_level("ERROR"), pytest.raises(StopIteration):
        webapp.update_check_loop()

    assert any("update check failed: update probe exploded" in record.message for record in caplog.records)
    assert webapp.update_check_recurring_work_snapshot()["failures"] == 1


def test_update_check_recurring_work_excludes_disabled_idle_sleep():
    webapp = app_module.TmuxWebtermApp.__new__(app_module.TmuxWebtermApp)
    webapp.update_check_record = app_module.UpdateCheckRecord()

    webapp.note_update_check(useful=False, next_due_seconds=60.0, enabled=False)
    row = webapp.update_check_recurring_work_snapshot()

    assert row["class"] == "external-reconcile"
    assert row["demanded"] is False
    assert row["attempts"] == row["useful"] == row["no_change"] == row["failures"] == 0


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

def test_ensure_xterm_runtime_assets_uses_only_tracked_vendor_files(monkeypatch, tmp_path):
    vendor_dir = tmp_path / "static" / "vendor"
    vendor_dir.mkdir(parents=True)
    for name in app_module.XTERM_RUNTIME_ASSETS:
        (vendor_dir / name).write_text(f"vendor-{name}", encoding="utf-8")
        (tmp_path / "static" / name).write_text(f"contaminated-{name}", encoding="utf-8")

    monkeypatch.setattr(app_module.shutil, "which", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("package tools must not be probed")))
    monkeypatch.setattr(app_module.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("asset installers must not run")))

    assert app_module.ensure_xterm_runtime_assets(tmp_path) == (True, "")
    assert app_module.xterm_runtime_assets_ready(tmp_path) is True
    assert {(vendor_dir / name).read_text(encoding="utf-8") for name in app_module.XTERM_RUNTIME_ASSETS} == {
        f"vendor-{name}" for name in app_module.XTERM_RUNTIME_ASSETS
    }
    assert {(tmp_path / "static" / name).read_text(encoding="utf-8") for name in app_module.XTERM_RUNTIME_ASSETS} == {
        f"contaminated-{name}" for name in app_module.XTERM_RUNTIME_ASSETS
    }

    (vendor_dir / "xterm.js").unlink()
    assert app_module.ensure_xterm_runtime_assets(tmp_path) == (
        False,
        "tracked xterm vendor assets are missing: static/vendor/xterm.js",
    )


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
    monkeypatch.setenv("MALLOC_ARENA_MAX", "2")
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
    assert context.env["MALLOC_ARENA_MAX"] == "2"
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


def test_indexed_repo_discovery_is_submitted_to_jobd_and_consumed_as_a_snapshot(tmp_path):
    class FakeJobClient:
        def __init__(self):
            self.submissions = []
            self.release_result = threading.Event()

        def submit(self, task, payload, **options):
            self.submissions.append((task, payload, options))
            return {"ok": True, "job": {"job_id": "repo-job", "status": "queued"}}

        def result(self, job_id):
            assert job_id == "repo-job"
            assert self.release_result.wait(timeout=2.0)
            return {"ok": True, "job": {"status": "completed", "result": {"roots": [str(tmp_path / "repo")]}}}

    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.activity_transcript_service = app_module.ActivityTranscriptService()
    webapp.job_client = FakeJobClient()
    webapp.settings_payload = lambda: {"settings": {"file_explorer": {"indexed_dirs": [str(tmp_path)]}}}

    assert webapp.indexed_repo_roots_snapshot() == []
    worker = webapp.activity_transcript_service.indexed_repo_record.worker
    assert worker is not None
    assert worker.is_alive() is True
    webapp.job_client.release_result.set()
    worker.join(timeout=2.0)
    assert worker.is_alive() is False
    assert webapp.job_client.submissions[0][0] == "indexed_repo_roots"
    assert webapp.job_client.submissions[0][2]["priority"] == "maintenance"
    assert webapp.indexed_repo_roots_snapshot() == [str(tmp_path / "repo")]
    assert len(webapp.job_client.submissions) == 1


def test_indexed_repo_discovery_reuses_healthy_generation_until_a_descendant_changes(tmp_path):
    class FakeJobClient:
        def __init__(self):
            self.submissions = []

        def submit(self, task, payload, **options):
            self.submissions.append((task, payload, options))
            return {"ok": True, "job": {"job_id": f"repo-job-{len(self.submissions)}", "status": "queued"}}

        def result(self, job_id):
            return {"ok": True, "job": {"status": "completed", "result": {"roots": [str(tmp_path / "repo")]}}}

    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.activity_transcript_service = app_module.ActivityTranscriptService()
    webapp.client_watch_service = app_module.ClientWatchService()
    webapp.client_watch_service.event_watcher_record.filesystem_healthy = True
    webapp.job_client = FakeJobClient()
    webapp.settings_payload = lambda: {"settings": {"file_explorer": {"indexed_dirs": [str(tmp_path)]}}}

    webapp.indexed_repo_roots_snapshot()
    first = webapp.activity_transcript_service.indexed_repo_record.worker
    assert first is not None
    first.join(timeout=2.0)
    assert len(webapp.job_client.submissions) == 1
    assert webapp.indexed_repo_roots_snapshot() == [str(tmp_path / "repo")]
    assert len(webapp.job_client.submissions) == 1, "an unchanged healthy root must not re-walk on the old 30-second clock"

    webapp.mark_indexed_repo_discovery_dirty([tmp_path / "repo" / ".git" / "HEAD"])
    webapp.indexed_repo_roots_snapshot()
    second = webapp.activity_transcript_service.indexed_repo_record.worker
    assert second is not None and second is not first
    second.join(timeout=2.0)
    assert len(webapp.job_client.submissions) == 2
    assert webapp.job_client.submissions[0][2]["generation"] != webapp.job_client.submissions[1][2]["generation"]


def test_session_files_index_updates_append_a_journal_instead_of_rewriting_the_base(tmp_path, monkeypatch):
    """A durable cache write must not read and rewrite the whole O(historical
    entries) JSON index; it appends one journal line, reads merge base+journal,
    and a full rewrite folds the journal in and truncates it."""
    cache_dir = tmp_path / "cache"
    base = app_module.session_files.disk_cache_index_path(cache_dir)
    journal = base.with_name(base.name + ".journal")
    app_module.session_files.update_disk_cache_index(cache_dir, "sig-a", size=10, mtime=1.0)
    app_module.session_files.update_disk_cache_index(cache_dir, "sig-b", size=20, mtime=2.0)
    app_module.session_files.update_disk_cache_index(cache_dir, "sig-a", size=30, mtime=3.0)  # later wins

    assert not base.exists()  # the base was never rewritten per write
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 3
    merged = app_module.session_files._read_disk_cache_index_unlocked(base)
    assert merged["entries"]["sig-a"] == {"size": 30, "mtime": 3.0}
    assert merged["entries"]["sig-b"] == {"size": 20, "mtime": 2.0}

    # A full rewrite (the prune path) folds the merged view into the base and
    # truncates the journal; the merged view is unchanged afterwards.
    app_module.session_files._write_disk_cache_index_unlocked(base, merged)
    assert base.exists() and not journal.exists()
    assert app_module.session_files._read_disk_cache_index_unlocked(base)["entries"] == merged["entries"]


def test_session_files_durable_cache_replaces_one_file_per_logical_view(tmp_path, monkeypatch):
    """The durable filename derives ONLY from the stable logical view key
    (kind, version, session, hours, refs, per-repo ref overrides); the volatile
    info/repo signatures are a replaceable source generation INSIDE the record.
    Frequent agent-status/transcript changes must replace the same file, never
    mint key-per-generation filenames; an older generation serves only as a
    stale last-known-good and never as a fresh hit."""
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "cache")
    stable = ("payload", app_module.SESSION_FILES_CACHE_KEY_VERSION, "1", 24.0, "", "", ())
    payload_one = {"files": [{"path": "one.py"}], "repos": [], "errors": []}
    payload_two = {"files": [{"path": "two.py"}], "repos": [], "errors": []}
    try:
        # Ten source generations of the same logical view: agent status flips.
        for index in range(10):
            key = (*stable, (("1", f"info-sig-{index}"),), (("~/repo", ("id", index)),))
            webapp.write_session_files_disk_cache(key, payload_one if index % 2 else payload_two, HTTPStatus.OK)
        cache_files = sorted(path.name for path in (tmp_path / "cache").glob("*.json"))
        payload_files = [name for name in cache_files if not name.endswith(("manifest.json",)) and name != app_module.SESSION_FILES_DISK_CACHE_INDEX_FILENAME and not name.endswith(".journal")]
        assert len(payload_files) == 1  # one durable file, replaced ten times

        newest = (*stable, (("1", "info-sig-9"),), (("~/repo", ("id", 9)),))
        older = (*stable, (("1", "info-sig-0"),), (("~/repo", ("id", 0)),))
        fresh = webapp.read_session_files_disk_cache(newest, max_age_seconds=60.0)
        assert fresh is not None and fresh[2] is True and fresh[0]["files"] == payload_one["files"]

        # Older generation: no fresh hit; stale last-known-good only.
        assert webapp.read_session_files_disk_cache(older, max_age_seconds=60.0) is None
        stale = webapp.read_session_files_disk_cache(older, allow_stale=True)
        assert stale is not None and stale[2] is False and stale[0]["files"] == payload_one["files"]
        # The stale serve must not have poisoned the memory cache for `older`.
        assert older not in webapp.session_files_service.cache

        # Different logical views (other hours; pane switch to another session)
        # each get their own single file.
        other_view = ("payload", app_module.SESSION_FILES_CACHE_KEY_VERSION, "1", 8.0, "", "", (), (("1", "info-sig-9"),), ())
        webapp.write_session_files_disk_cache(other_view, payload_two, HTTPStatus.OK)
        pane_switch = ("payload", app_module.SESSION_FILES_CACHE_KEY_VERSION, "2", 24.0, "", "", (), (("2", "info-sig-9"),), ())
        webapp.write_session_files_disk_cache(pane_switch, payload_two, HTTPStatus.OK)
        payload_files = [name for name in sorted(path.name for path in (tmp_path / "cache").glob("*.json")) if not name.endswith("manifest.json") and name != app_module.SESSION_FILES_DISK_CACHE_INDEX_FILENAME]
        assert len(payload_files) == 3

        # An old-format key-per-generation file is never imported into active
        # serving: it does not match any stable-name lookup and is left on disk
        # for the existing prune (never deleted here).
        legacy = tmp_path / "cache" / (hashlib.sha256(b"old-full-key-hash").hexdigest() + ".json")
        legacy.write_text(json.dumps({"version": 1, "payload": {"files": [{"path": "legacy.py"}]}}), encoding="utf-8")
        fresh_again = webapp.read_session_files_disk_cache(newest, max_age_seconds=60.0)
        assert fresh_again is not None and fresh_again[0]["files"] == payload_one["files"]
        assert legacy.exists()
    finally:
        webapp.control_server.stop()


def test_stale_session_files_survive_a_failing_refresh_and_never_go_empty(monkeypatch):
    """Last-known-good through refresh errors: a stale populated payload is
    served immediately, the failing background refresh replaces nothing, and a
    later read still returns the populated payload — never an empty placeholder."""
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["1"])
    try:
        info = SessionInfo(session="1", panes=[], selected_pane=None, agents=[])
        key = webapp.session_files_cache_key("payload", {"1": info}, "1", 24.0, None, None, None)
        populated = {"files": [{"path": "keep.py"}], "repos": [], "errors": []}
        webapp.set_session_files_memory_cache(key, populated, HTTPStatus.OK, stored_at=app_module.time.monotonic() - 999.0)

        compute_calls = []

        def failing_via_jobd(*args, **kwargs):
            compute_calls.append(True)
            raise app_module.SessionFilesJobdUnavailable("refresh blew up")

        # The refresh WORKER (now a jobd product materialization) fails; the request-side stale serve
        # must not care, and the failing refresh must replace nothing.
        monkeypatch.setattr(webapp, "compute_session_files_payload_via_jobd", failing_via_jobd)

        payload = webapp.cached_session_files_payload_for_info(info)
        assert payload["files"] == populated["files"]

        # Let the background refresh worker run (and fail).
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with webapp.session_files_service.cache_lock:
                busy = bool(webapp.session_files_service.work_records)
            if not busy and compute_calls:
                break
            time.sleep(0.02)
        assert compute_calls, "the stale hit must have scheduled one background refresh"

        # The populated stale entry is still there; nothing replaced it with empty.
        cached = webapp.get_session_files_cache(key, max_age_seconds=None, allow_stale=True)
        assert cached is not None and cached[0]["files"] == populated["files"]
    finally:
        webapp.control_server.stop()
