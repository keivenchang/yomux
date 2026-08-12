# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""M9 of DOIT.p0.daemon-monitor: the `backend_health_changed` client event.

The event carries EXACTLY `{epoch, revision, overall_state, degraded_resources}` on the existing
`core` channel. Everything else it needs -- resource revisions, subscriber filtering,
latest-per-resource coalescing, reconnect replay, counters -- already belongs to
`ClientEventBroker`, so the only delivery machinery added here is the retained-event replay, and
these tests pin that it stays the broker's job.

The observer drives every event in this file. Not one test opens a panel, subscribes before the
event is produced, or issues an HTTP request: health is generated while System and YO!stats are
hidden, which is the defect the milestone removes.
"""

from __future__ import annotations

import http.client
import json
import queue
import urllib.request
from pathlib import Path

import pytest
from test_backend_health_observer import Harness

from yolomux_lib.backend_health.observer import BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_EVENT
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_EVENT_MAX_RESOURCES
from yolomux_lib.client_events import CLIENT_EVENT_CHANNELS
from yolomux_lib.client_events import CLIENT_EVENT_RETAINED_TYPES
from yolomux_lib.client_events import CLIENT_EVENT_TYPE_CHANNELS
from yolomux_lib.client_events import CLIENT_EVENT_TYPES
from yolomux_lib.client_events import ClientEventBroker
from yolomux_lib.client_events import client_event_resource
from yolomux_lib.local_services.rpc import reset_local_service_traffic


PAYLOAD_KEYS = {"epoch", "revision", "overall_state", "degraded_resources"}
DEGRADED_KEYS = {"id", "label", "state", "reason_code"}


class BrokerHarness(Harness):
    """The observer wired to a real `ClientEventBroker`, exactly as `cli.py` wires it."""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.broker = ClientEventBroker()
        self.observer._publish = self.publish

    def publish(self, event_type: str, payload: dict) -> dict:
        self.published.append((event_type, payload))
        return self.broker.publish(event_type, payload)


@pytest.fixture
def live(tmp_path: Path):
    reset_local_service_traffic()
    harness = BrokerHarness(tmp_path)
    yield harness
    harness.observer.stop()
    reset_local_service_traffic()


def drain(broker: ClientEventBroker, subscriber_id: int) -> list[dict]:
    """Read a subscriber's queue the way `server.py` does.

    Deliberately `next_event` rather than `queue.get_nowait()`: the broker keeps a reference to
    the queued object so a later publication can coalesce into it in place, and only
    `next_event` releases that reference. Reading around it would make an already-delivered
    event mutate under the test.
    """

    events = []
    while True:
        try:
            events.append(broker.next_event(subscriber_id, timeout=0))
        except queue.Empty:
            return events


# -- the contract ------------------------------------------------------------------------


def test_the_event_is_declared_on_the_existing_core_channel():
    assert BACKEND_HEALTH_EVENT == "backend_health_changed"
    assert BACKEND_HEALTH_EVENT in CLIENT_EVENT_TYPES
    assert CLIENT_EVENT_TYPE_CHANNELS[BACKEND_HEALTH_EVENT] == frozenset({"core"})
    assert "core" in CLIENT_EVENT_CHANNELS
    # One resource for the whole event type: that is what makes coalescing latest-per-resource.
    assert client_event_resource(BACKEND_HEALTH_EVENT, {"revision": 4}) == BACKEND_HEALTH_EVENT


def test_the_payload_is_exactly_four_keys(live: BrokerHarness):
    live.cycle(2)
    live.services["statsd"].down("statsd worker exited")
    live.cycle()

    event_type, payload = live.published[-1]
    assert event_type == BACKEND_HEALTH_EVENT
    assert set(payload) == PAYLOAD_KEYS
    assert isinstance(payload["epoch"], str) and payload["epoch"]
    assert payload["revision"] == 3
    assert payload["overall_state"] == "down"
    assert payload["degraded_resources"] == [
        {"id": "statsd", "label": "label:statsd", "state": "down", "reason_code": "exited"}
    ]
    for row in payload["degraded_resources"]:
        assert set(row) == DEGRADED_KEYS


def test_the_payload_carries_no_history_and_no_private_detail(live: BrokerHarness):
    live.cycle(2)
    live.services["watchd"].down("watchd exited")
    live.cycle()

    text = json.dumps(live.published[-1][1], sort_keys=True)
    for forbidden in (
        "transitions",
        "aggregate",
        "last_sample",
        "coverage",
        "process_epoch",
        "pid",
        "socket",
        "/",
        "reason\":",
    ):
        assert forbidden not in text, forbidden
    # The store meanwhile keeps the whole history, which is exactly why the event does not.
    assert live.store.document()["resources"]["watchd"]["transitions"]


def test_a_healthy_revision_names_no_degraded_resource(live: BrokerHarness):
    live.cycle(2)
    _, payload = live.published[-1]
    assert payload["overall_state"] == "ready"
    assert payload["degraded_resources"] == []


def test_an_absent_demand_scoped_service_is_not_degraded(live: BrokerHarness):
    live.cycle(2)
    live.services["indexd"].absent()
    live.cycle(2)
    _, payload = live.published[-1]
    assert payload["overall_state"] == "starting"
    assert payload["degraded_resources"] == []


def test_the_degraded_list_is_bounded():
    assert BACKEND_HEALTH_EVENT_MAX_RESOURCES == 16


# -- delivery ----------------------------------------------------------------------------


def test_the_event_reaches_core_subscribers_and_nobody_else(live: BrokerHarness):
    core_id, _core_queue = live.broker.subscribe(channels={"core"})
    stats_id, _stats_queue = live.broker.subscribe(channels={"stats"})
    try:
        delivered = []
        for _ in range(2):
            live.cycle()
            delivered.extend(event for event in drain(live.broker, core_id) if event["type"] == BACKEND_HEALTH_EVENT)
        assert [event["payload"]["revision"] for event in delivered] == [1, 2]
        assert set(delivered[-1]["payload"]) == PAYLOAD_KEYS
        assert drain(live.broker, stats_id) == []
    finally:
        live.broker.unsubscribe(core_id)
        live.broker.unsubscribe(stats_id)


def test_coalescing_keeps_only_the_latest_revision_per_resource(live: BrokerHarness):
    core_id, _core_queue = live.broker.subscribe(channels={"core"})
    try:
        # Never drained between transitions: this is a browser that stopped reading.
        live.cycle(2)
        live.services["jobd"].down("jobd exited")
        live.cycle()
        live.services["jobd"].up()
        live.cycle(2)

        events = [event for event in drain(live.broker, core_id) if event["type"] == BACKEND_HEALTH_EVENT]
        assert len(events) == 1, [event["payload"]["revision"] for event in events]
        assert events[0]["payload"]["revision"] == live.store.document()["revision"]
        assert events[0]["payload"]["overall_state"] == "ready"
    finally:
        live.broker.unsubscribe(core_id)


def test_a_reconnecting_client_is_replayed_the_latest_revision(live: BrokerHarness):
    # Nothing is subscribed while the transition happens: System and YO!stats are closed.
    live.cycle(2)
    live.services["statusd"].down("statusd exited")
    live.cycle()
    assert live.broker.subscribers == {}

    core_id, _core_queue = live.broker.subscribe(channels={"core"})
    try:
        replayed = [event for event in drain(live.broker, core_id) if event["type"] == BACKEND_HEALTH_EVENT]
        assert len(replayed) == 1
        assert replayed[0]["replay"] is True
        assert replayed[0]["payload"]["revision"] == live.store.document()["revision"]
        assert replayed[0]["payload"]["degraded_resources"][0]["id"] == "statusd"
        # The replay reuses the producer's revision instead of minting one.
        snapshot = live.broker.ready_snapshot(core_id)
        assert snapshot["resource_revisions"][BACKEND_HEALTH_EVENT] == replayed[0]["resource_revision"]
    finally:
        live.broker.unsubscribe(core_id)


def test_only_retained_types_are_replayed(live: BrokerHarness):
    # `search_progress` is intentionally retained + replayed (streaming Quick Open: a reconnecting
    # page receives the latest per-scope revision signal and pulls the delta by cursor).
    assert CLIENT_EVENT_RETAINED_TYPES == frozenset({BACKEND_HEALTH_EVENT, "search_progress"})
    live.broker.publish("settings_changed", {"data": {}})
    core_id, _core_queue = live.broker.subscribe(channels={"core"})
    try:
        assert drain(live.broker, core_id) == []
    finally:
        live.broker.unsubscribe(core_id)


def test_a_non_core_subscriber_is_not_replayed(live: BrokerHarness):
    live.cycle(2)
    files_id, _files_queue = live.broker.subscribe(channels={"files"})
    try:
        assert drain(live.broker, files_id) == []
    finally:
        live.broker.unsubscribe(files_id)


# -- exactly one event per change ---------------------------------------------------------


def test_exactly_one_event_is_published_per_signature_change(live: BrokerHarness):
    core_id, _core_queue = live.broker.subscribe(channels={"core"})
    try:
        published = 0
        for _ in range(3):
            cycle = live.cycle()
            published += 1 if cycle.published else 0
            drain(live.broker, core_id)
        live.services["approvald"].down("approvald exited")
        cycle = live.cycle()
        published += 1 if cycle.published else 0
        drain(live.broker, core_id)
        for _ in range(4):
            cycle = live.cycle()
            published += 1 if cycle.published else 0
            drain(live.broker, core_id)

        counter = live.broker.snapshot()["published_by_type"][BACKEND_HEALTH_EVENT]["events"]
        assert counter == published
        # starting baseline, ready, down. Seven further cycles changed nothing.
        assert published == 3
        assert live.store.document()["revision"] == 3
    finally:
        live.broker.unsubscribe(core_id)


# -- no browser polling -------------------------------------------------------------------


def test_no_polling_endpoint_is_introduced_for_health(live: BrokerHarness, monkeypatch):
    """Health arrives by push, so there is nothing to fetch on a timer -- measured, not grepped.

    This used to scan observer.py, client_events.py and cli.py for the strings "system-status"
    and "setInterval". That is an assertion about how three modules are spelled: it stays green
    for a poller written under any other name, and it can never fail for `setInterval`, which
    is not Python. What is measured here instead is the behaviour that makes polling
    unnecessary, and the behaviour that would betray a poller:

      * a subscriber that issues NO request is handed every state change, and the last payload
        it was pushed carries the same revision and state the retained document holds -- which
        is exactly what `/api/system-status` would have been polled for;
      * producing every one of those changes issues zero HTTP requests of any kind.

    The browser half of the same invariant is measured at runtime in
    `tests/backend_health_indicator.test.js` ("no polling was added: health arrives on the
    pushed event and issues no request"): five pushed revisions through a recording fetch
    produce zero requests.
    """

    issued: list[str] = []
    monkeypatch.setattr(
        http.client.HTTPConnection,
        "request",
        lambda self, method, url, *args, **kwargs: issued.append(f"{method} {url}"),
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: issued.append("urlopen"))

    core_id, _core_queue = live.broker.subscribe(channels={"core"})
    received: list[dict] = []

    def pushed() -> list[dict]:
        events = [event for event in drain(live.broker, core_id) if event["type"] == BACKEND_HEALTH_EVENT]
        received.extend(events)
        return events

    try:
        live.cycle(2)
        pushed()
        live.services["statsd"].down("statsd worker exited")
        live.cycle()
        pushed()
        live.services["watchd"].down("watchd exited")
        live.cycle()
        pushed()
        live.services["statsd"].up()
        live.services["watchd"].up()
        live.cycle(BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS)
        pushed()
    finally:
        live.broker.unsubscribe(core_id)

    # Nothing on the health path asked an endpoint for anything.
    assert issued == [], issued
    # Four state changes reached a subscriber that never issued a request.
    assert [event["payload"]["overall_state"] for event in received] == ["ready", "down", "down", "ready"]
    assert [[row["id"] for row in event["payload"]["degraded_resources"]] for event in received] == [
        [],
        ["statsd"],
        ["statsd", "watchd"],
        [],
    ]
    # ...and the last thing it was pushed IS the current truth, so a fetch would add nothing.
    document = live.store.document()
    assert received[-1]["payload"]["revision"] == document["revision"]
    assert received[-1]["payload"]["epoch"] == document["observer_epoch"]
    # The event is self-describing: it carries the state, so there is nothing for the browser to
    # go and fetch on a timer. That is what makes "do not add browser polling" enforceable.
    assert set(received[-1]["payload"]) == PAYLOAD_KEYS
    assert PAYLOAD_KEYS == {"epoch", "revision", "overall_state", "degraded_resources"}
