from __future__ import annotations

import json

import pytest

from tests.gate_harness import aged_state_root  # noqa: F401
from tests.gate_harness import assert_browser_journey_error_free
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from yolomux_lib.local_services.rpc import encode_metadata
from yolomux_lib.local_services.rpc import LOCAL_RPC_MAX_METADATA_BYTES
from yolomux_lib.local_services.rpc import new_envelope
from yolomux_lib.stats_current import storage
from yolomux_lib.workspace import session_files


class _MissingJsDebugStoreDriver:
    def execute_script(self, _script):
        return {"reachable": False, "isArray": False, "events": [], "errors": []}

    def get_log(self, _kind):
        raise AssertionError("console must not be read after the JS error store fails closed")


class _NonArrayJsDebugStoreDriver:
    def execute_script(self, _script):
        return {"reachable": True, "isArray": False, "events": {}, "errors": []}

    def get_log(self, _kind):
        raise AssertionError("console must not be read after the JS error store fails closed")


def test_browser_journey_error_gate_fails_when_js_debug_store_is_unreachable():
    with pytest.raises(AssertionError, match="jsDebugEvents is unreachable"):
        assert_browser_journey_error_free(_MissingJsDebugStoreDriver())


def test_browser_journey_error_gate_fails_when_js_debug_store_is_not_an_array():
    with pytest.raises(AssertionError, match="jsDebugEvents is not an array"):
        assert_browser_journey_error_free(_NonArrayJsDebugStoreDriver())


def test_aged_state_recipes_are_opt_in_and_shared_host_caches_coexist(aged_state_root):
    assert aged_state_root.results == {}

    result = aged_state_root.apply("coexisting_transcript_caches", shared_count=3, host_count=2)

    shared_dir, host_dir = result.paths
    assert len(list(shared_dir.glob("*.json"))) == 3
    assert len(list(host_dir.glob("*.json"))) == 2
    assert shared_dir.parent == aged_state_root.state_dir
    assert host_dir.parent == aged_state_root.host_state_dir
    assert set(aged_state_root.results) == {"coexisting_transcript_caches"}
    with pytest.raises(ValueError, match="already applied"):
        aged_state_root.apply("coexisting_transcript_caches", shared_count=1, host_count=1)


def test_aged_state_eof_cursor_and_nonempty_wal_use_current_product_owners(aged_state_root):
    cursor_result = aged_state_root.apply("eof_transcript_cursor")
    wal_result = aged_state_root.apply("stats_wal", minimum_wal_bytes=32 * 1024, payload_bytes=512)

    transcript, cursor = cursor_result.paths
    identity = tuple(cursor_result.details["identity"])
    state = session_files.load_transcript_scan_state(identity, transcript, session_files.new_codex_transcript_scan_state)
    assert state is not None
    assert state["offset"] == state["size"] == transcript.stat().st_size
    assert cursor.parent == session_files.transcript_scan_store_dir()

    database, wal_path = wal_result.paths
    assert database == storage.default_database_path(aged_state_root.state_dir)
    assert wal_path.stat().st_size >= 32 * 1024
    with storage.Store.open_reader(database) as reader:
        snapshot = reader.read_snapshot()
    assert len(snapshot.observations) == wal_result.details["observations"]


def test_aged_state_event_density_and_owner_epochs_are_selectable(aged_state_root):
    event_counts = {"state_changed": 7, "stale_owner_heartbeat": 3}
    events = aged_state_root.apply("event_history", counts=event_counts)
    owners = aged_state_root.apply("stale_owner_epochs", epoch_count=3)

    lines = [json.loads(line) for line in events.paths[0].read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 10
    assert {name: sum(row["type"] == name for row in lines) for name in event_counts} == event_counts
    owner = json.loads(owners.paths[0].read_text(encoding="utf-8"))
    index = json.loads(owners.paths[1].read_text(encoding="utf-8"))
    assert owner["last_heartbeat"] == owners.details["stale_heartbeat"] - 2
    assert len(index["records"]) == 3
    assert set(aged_state_root.results) == {"event_history", "stale_owner_epochs"}


def test_aged_state_finder_history_and_rpc_payloads_capture_real_boundaries(aged_state_root):
    finder = aged_state_root.apply("finder_resource_history", top_level_entries=6, nested_entries=3)
    boundaries = aged_state_root.apply("rpc_metadata_boundaries")

    assert not finder.paths[2].exists()
    assert finder.paths[3].is_symlink()
    assert finder.paths[3].resolve() == finder.paths[4].resolve()
    assert [row["phase"] for row in finder.details["expansion_records"]] == [
        "expand-dev",
        "open-subdirectory",
        "collapse-dev",
        "reexpand-dev",
    ]

    below_payload = json.loads(boundaries.paths[0].read_text(encoding="utf-8"))
    above_payload = json.loads(boundaries.paths[1].read_text(encoding="utf-8"))
    below_size = len(encode_metadata(new_envelope("jobd", "submit", below_payload, timeout_seconds=0.5, trace_id="a" * 32)))
    above_size = len(encode_metadata(new_envelope("jobd", "submit", above_payload, timeout_seconds=0.5, trace_id="a" * 32)))
    assert below_size == LOCAL_RPC_MAX_METADATA_BYTES - 1
    assert above_size == LOCAL_RPC_MAX_METADATA_BYTES + 1
