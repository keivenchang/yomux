import queue
import re
from pathlib import Path

import pytest

import yolomux_lib.client_events as client_events
from yolomux_lib.client_events import CLIENT_EVENT_TYPES
from yolomux_lib.client_events import ClientEventBroker


def test_client_event_broker_skips_json_byte_measurement_without_subscribers(monkeypatch):
    broker = ClientEventBroker()
    monkeypatch.setattr(client_events.json, "dumps", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no-subscriber publish must not serialize for metrics")))

    event = broker.publish("fs_changed", {"paths": ["/repo/app.py"]})

    assert event["type"] == "fs_changed"
    assert broker.snapshot()["published_events"] == 1
    assert broker.snapshot()["published_bytes"] == 0

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_client_event_broker_publishes_to_subscribers():
    broker = ClientEventBroker()
    subscriber_id, subscriber_queue = broker.subscribe()

    event = broker.publish("fs_changed", {"paths": ["/repo/app.py"]})

    assert subscriber_queue.get_nowait() == event
    assert event["id"] == 1
    assert event["type"] == "fs_changed"
    assert event["payload"] == {"paths": ["/repo/app.py"]}
    assert event["resource"] == "fs_changed"
    assert event["resource_revision"] == 1
    assert event["epoch"]

    broker.unsubscribe(subscriber_id)
    broker.publish("fs_changed", {"paths": ["/repo/other.py"]})
    with pytest.raises(queue.Empty):
        subscriber_queue.get_nowait()


def test_client_event_broker_counts_transport_heartbeats_separately_from_events():
    broker = ClientEventBroker()

    broker.record_heartbeat()
    snapshot = broker.snapshot()

    assert snapshot["heartbeat_events"] == 1
    assert snapshot["last_heartbeat_at"] > 0
    assert snapshot["published_events"] == 0


def test_client_event_broker_assigns_monotonic_revisions_per_resource_and_exposes_ready_snapshot():
    broker = ClientEventBroker()

    first_files = broker.publish("fs_changed", {"paths": ["/repo/one"]})
    status = broker.publish("auto_approve_changed", {"refresh": True})
    second_files = broker.publish("fs_changed", {"paths": ["/repo/two"]})

    assert first_files["epoch"] == status["epoch"] == second_files["epoch"]
    assert first_files["resource"] == second_files["resource"] == "fs_changed"
    assert (first_files["resource_revision"], second_files["resource_revision"]) == (1, 2)
    assert status["resource"] == "auto_approve_changed"
    assert status["resource_revision"] == 1
    snapshot = broker.snapshot()
    assert snapshot["epoch"] == first_files["epoch"]
    assert snapshot["resource_revisions"] == {"auto_approve_changed": 1, "fs_changed": 2}


def test_client_event_broker_ready_snapshot_contains_only_subscriber_demanded_resources():
    broker = ClientEventBroker()
    files_id, _files_queue = broker.subscribe(channels={"files"})
    status_id, _status_queue = broker.subscribe(channels={"status"})

    files = broker.publish("fs_changed", {"paths": ["/repo/one"]})
    status = broker.publish("auto_approve_changed", {"refresh": True})
    stats = broker.publish("stats_sample", {"sequence": 9})

    assert broker.ready_snapshot(files_id) == {
        "epoch": files["epoch"],
        "resource_revisions": {"fs_changed": files["resource_revision"]},
    }
    assert broker.ready_snapshot(status_id) == {
        "epoch": status["epoch"],
        "resource_revisions": {"auto_approve_changed": status["resource_revision"]},
    }
    assert stats["resource"] not in broker.ready_snapshot(files_id)["resource_revisions"]
    assert broker.ready_snapshot(9999)["resource_revisions"] == {}


def test_client_event_broker_drops_oldest_event_when_subscriber_lags():
    broker = ClientEventBroker(max_queue_size=1)
    _subscriber_id, subscriber_queue = broker.subscribe()

    broker.publish("first", {})
    latest = broker.publish("second", {})

    assert subscriber_queue.get_nowait() == latest


def test_client_event_broker_coalesces_pending_resource_to_its_latest_revision():
    broker = ClientEventBroker(max_queue_size=3)
    subscriber_id, subscriber_queue = broker.subscribe()

    broker.publish("fs_changed", {"paths": ["/repo/one"]})
    latest = broker.publish("fs_changed", {"paths": ["/repo/two"]})

    assert subscriber_queue.get_nowait() == latest
    snapshot = broker.snapshot()
    assert snapshot["coalesced_events"] == 1
    assert snapshot["dropped_events"] == 0
    assert snapshot["clients"][0]["coalesced_events"] == 1
    broker.unsubscribe(subscriber_id)


def test_client_event_broker_keeps_background_role_scopes_as_independent_resources():
    broker = ClientEventBroker(max_queue_size=3)
    subscriber_id, subscriber_queue = broker.subscribe(channels={"core"})

    index = broker.publish("background_refresh_done", {"role": "search-index", "root": "/repo"})
    second_index = broker.publish("background_refresh_done", {"role": "search-index", "root": "/other-repo"})
    tabber = broker.publish("background_refresh_done", {"role": "tabber-activity"})

    assert [subscriber_queue.get_nowait(), subscriber_queue.get_nowait()] == [index, second_index]
    assert subscriber_queue.get_nowait() == tabber
    assert index["resource"].startswith("background:search-index:")
    assert second_index["resource"].startswith("background:search-index:")
    assert index["resource"] != second_index["resource"]
    assert tabber["resource"] == "background:tabber-activity"
    snapshot = broker.snapshot()
    assert snapshot["published_by_resource"][index["resource"]]["events"] == 1
    assert snapshot["published_by_resource"][second_index["resource"]]["events"] == 1
    assert snapshot["published_by_resource"]["background:tabber-activity"]["events"] == 1
    broker.unsubscribe(subscriber_id)


def test_client_event_broker_releases_resource_after_server_dequeues_it():
    broker = ClientEventBroker(max_queue_size=2)
    subscriber_id, _subscriber_queue = broker.subscribe()

    first = broker.publish("fs_changed", {"paths": ["/repo/one"]})
    assert broker.next_event(subscriber_id, timeout=0.01) == first
    second = broker.publish("fs_changed", {"paths": ["/repo/two"]})

    assert broker.next_event(subscriber_id, timeout=0.01) == second
    assert broker.snapshot()["coalesced_events"] == 0
    broker.unsubscribe(subscriber_id)


def test_client_event_broker_records_queue_overflow_drop():
    broker = ClientEventBroker(max_queue_size=1)
    subscriber_id, _subscriber_queue = broker.subscribe()

    broker.publish("fs_changed", {"paths": ["/repo/one"]})
    latest = broker.publish("auto_approve_changed", {"refresh": True})

    delivered = broker.next_event(subscriber_id, timeout=0.01)
    assert delivered["id"] == latest["id"]
    assert delivered["repair_resources"] == ["fs_changed"]
    snapshot = broker.snapshot()
    assert snapshot["dropped_events"] == 1
    assert snapshot["dropped_by_resource"]["fs_changed"]["events"] == 1
    assert snapshot["clients"][0]["dropped_events"] == 1


def test_client_event_broker_filters_disjoint_subscriber_channels_and_accounts_bytes():
    broker = ClientEventBroker()
    files_id, files_queue = broker.subscribe(channels={"files"}, client_id="files-client/<script>")
    status_id, status_queue = broker.subscribe(channels={"status"}, client_id="status-client")

    files_event = broker.publish("fs_changed", {"data": "x" * 1024})
    status_event = broker.publish("auto_approve_changed", {"refresh": True})

    assert files_queue.get_nowait() == files_event
    assert status_queue.get_nowait() == status_event
    with pytest.raises(queue.Empty):
        files_queue.get_nowait()
    with pytest.raises(queue.Empty):
        status_queue.get_nowait()
    snapshot = broker.snapshot()
    assert snapshot["subscribers"] == 2
    assert snapshot["channel_counts"]["files"] == 1
    assert snapshot["channel_counts"]["status"] == 1
    assert snapshot["delivered_events"] == 2
    assert snapshot["filtered_events"] == 2
    assert snapshot["filtered_bytes"] > 1024
    assert snapshot["published_by_type"]["fs_changed"]["events"] == 1
    assert snapshot["delivered_by_type"]["fs_changed"]["bytes"] > 1024
    assert snapshot["filtered_by_type"]["fs_changed"]["events"] == 1
    assert snapshot["clients"][0]["client_id"] == "files-clientscript"
    assert snapshot["clients"][0]["channels"] == ["files"]

    broker.unsubscribe(files_id)
    broker.unsubscribe(status_id)
    assert broker.snapshot()["subscribers"] == 0


def test_client_event_broker_delivers_live_stats_only_to_stats_demand():
    broker = ClientEventBroker()
    stats_id, stats_queue = broker.subscribe(channels={"stats"})
    core_id, core_queue = broker.subscribe(channels={"core"})

    event = broker.publish("stats_sample", {"sequence": 9, "record": {"start": 1000, "duration": 1}})

    assert stats_queue.get_nowait() == event
    with pytest.raises(queue.Empty):
        core_queue.get_nowait()
    snapshot = broker.snapshot()
    assert snapshot["channel_counts"]["stats"] == 1
    assert snapshot["delivered_by_type"]["stats_sample"]["events"] == 1
    assert snapshot["filtered_by_type"]["stats_sample"]["events"] == 1
    broker.unsubscribe(stats_id)
    broker.unsubscribe(core_id)


def test_client_event_broker_delivers_event_log_invalidations_only_to_open_log_demand():
    broker = ClientEventBroker()
    events_id, events_queue = broker.subscribe(channels={"events"})
    activity_id, activity_queue = broker.subscribe(channels={"activity"})

    event = broker.publish("event_log_changed", {"session": "1"})

    assert events_queue.get_nowait() == event
    with pytest.raises(queue.Empty):
        activity_queue.get_nowait()
    assert broker.ready_snapshot(events_id)["resource_revisions"] == {"event_log_changed:1": 1}
    broker.unsubscribe(events_id)
    broker.unsubscribe(activity_id)


def test_app_publishes_only_known_client_event_types():
    # every event name app.py emits via publish_client_event("...") must be in the canonical
    # CLIENT_EVENT_TYPES set. The browser's EventSource subscribes by these names, so a server-side typo
    # silently means "no client ever hears this event" — this catches that drift at test time.
    app_src = (REPO_ROOT / "yolomux_lib" / "app.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r"publish_client_event\(\s*[\"']([a-z_]+)[\"']", app_src))
    assert emitted, "expected to find publish_client_event(\"...\") string-literal calls"
    unknown = emitted - CLIENT_EVENT_TYPES
    assert not unknown, f"app.py emits client-event types not in CLIENT_EVENT_TYPES: {sorted(unknown)}"


def test_browser_client_event_contract_matches_server_event_types():
    source = (REPO_ROOT / "static_src" / "js" / "yolomux" / "99_terminal_boot.js").read_text(encoding="utf-8")
    match = re.search(r"const clientPushEventTypes = Object\.freeze\(\[(.*?)\]\);", source, flags=re.DOTALL)
    assert match, "browser EventSource/dispatch contract must have one declared event table"
    browser_types = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert browser_types == CLIENT_EVENT_TYPES, (
        "browser EventSource/dispatch event types drifted from ClientEventBroker: "
        f"browser-only={sorted(browser_types - CLIENT_EVENT_TYPES)}, "
        f"server-only={sorted(CLIENT_EVENT_TYPES - browser_types)}"
    )


def test_update_available_is_a_known_client_event():
    # The server pushes "update_available" over /api/client-events; the browser subscribes by name.
    assert "update_available" in CLIENT_EVENT_TYPES
