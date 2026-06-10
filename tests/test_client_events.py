import queue

import pytest

from yolomux_lib.client_events import ClientEventBroker


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
