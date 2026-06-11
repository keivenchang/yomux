import queue
import re
from pathlib import Path

import pytest

from yolomux_lib.client_events import CLIENT_EVENT_TYPES
from yolomux_lib.client_events import ClientEventBroker

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_client_event_broker_publishes_to_subscribers():
    broker = ClientEventBroker()
    subscriber_id, subscriber_queue = broker.subscribe()

    event = broker.publish("fs_changed", {"paths": ["/repo/app.py"]})

    assert subscriber_queue.get_nowait() == event
    assert event["id"] == 1
    assert event["type"] == "fs_changed"
    assert event["payload"] == {"paths": ["/repo/app.py"]}

    broker.unsubscribe(subscriber_id)
    broker.publish("fs_changed", {"paths": ["/repo/other.py"]})
    with pytest.raises(queue.Empty):
        subscriber_queue.get_nowait()


def test_client_event_broker_drops_oldest_event_when_subscriber_lags():
    broker = ClientEventBroker(max_queue_size=1)
    _subscriber_id, subscriber_queue = broker.subscribe()

    broker.publish("first", {})
    latest = broker.publish("second", {})

    assert subscriber_queue.get_nowait() == latest


def test_app_publishes_only_known_client_event_types():
    # every event name app.py emits via publish_client_event("...") must be in the canonical
    # CLIENT_EVENT_TYPES set. The browser's EventSource subscribes by these names, so a server-side typo
    # silently means "no client ever hears this event" — this catches that drift at test time.
    app_src = (REPO_ROOT / "yolomux_lib" / "app.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r"publish_client_event\(\s*[\"']([a-z_]+)[\"']", app_src))
    assert emitted, "expected to find publish_client_event(\"...\") string-literal calls"
    unknown = emitted - CLIENT_EVENT_TYPES
    assert not unknown, f"app.py emits client-event types not in CLIENT_EVENT_TYPES: {sorted(unknown)}"
