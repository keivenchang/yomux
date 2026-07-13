from __future__ import annotations

from dataclasses import dataclass
import json
import queue
import threading
import time
from typing import Any

# the canonical set of server-pushed client-event type names — previously ~11 bare string
# literals scattered across app.py's publish_client_event() calls, with no single owner and no guard. The
# names are a contract the browser (the EventSource consumer) depends on; centralizing them here gives a
# single place to read/extend the set and lets a test catch a server-side typo'd event name (a typo would
# silently mean "no client ever hears this event"). The `*_ready` events carry freshly-computed payloads;
# the `*_changed` events are signal-only nudges to refetch.
CLIENT_EVENT_TYPES: frozenset[str] = frozenset({
    "activity_summary_ready",
    "attention_acks_changed",
    "auto_approve_changed",
    "background_owner_changed",
    "background_refresh_done",
    "background_refresh_requested",
    "pricing_catalog_changed",
    "chat_messages_changed",
    "chat_typing_changed",
    "context_changed",
    "context_items_ready",
    "files_changed",
    "fs_changed",
    "roots_changed",
    "session_files_ready",
    "settings_changed",
    "stats_sample",
    "transcripts_changed",
    "tmux_signals_changed",
    "update_available",
    "watched_prs_changed",
    "yoagent_conversation_changed",
    "yoagent_jobs_changed",
    "yoagent_skills_changed",
    "yoagent_stream_delta",
})

CLIENT_EVENT_CHANNELS: frozenset[str] = frozenset({
    "activity",
    "attention",
    "chat",
    "core",
    "files",
    "status",
    "stats",
    "transcripts",
    "yoagent",
})

CLIENT_EVENT_TYPE_CHANNELS: dict[str, frozenset[str]] = {
    "activity_summary_ready": frozenset({"activity"}),
    "attention_acks_changed": frozenset({"status", "attention"}),
    "auto_approve_changed": frozenset({"status", "attention"}),
    "background_owner_changed": frozenset({"core"}),
    "background_refresh_done": frozenset({"core"}),
    "background_refresh_requested": frozenset({"core"}),
    "chat_messages_changed": frozenset({"chat"}),
    "chat_typing_changed": frozenset({"chat"}),
    "context_changed": frozenset({"transcripts"}),
    "context_items_ready": frozenset({"transcripts"}),
    "files_changed": frozenset({"files"}),
    "fs_changed": frozenset({"files"}),
    "roots_changed": frozenset({"files"}),
    "session_files_ready": frozenset({"files"}),
    "settings_changed": frozenset({"core"}),
    # One compact durable owner sample for a visible YO!stats graph.  This
    # separate demand channel avoids sending a one-per-second metrics stream
    # to pages which are not displaying YO!stats.
    "stats_sample": frozenset({"stats"}),
    "transcripts_changed": frozenset({"transcripts"}),
    "tmux_signals_changed": frozenset({"status", "attention"}),
    "update_available": frozenset({"core"}),
    "watched_prs_changed": frozenset({"core", "attention"}),
    "yoagent_conversation_changed": frozenset({"yoagent"}),
    "yoagent_jobs_changed": frozenset({"yoagent", "attention"}),
    "yoagent_skills_changed": frozenset({"yoagent"}),
    "yoagent_stream_delta": frozenset({"yoagent"}),
}

CLIENT_EVENT_SNAPSHOT_CLIENT_LIMIT = 32


def normalize_client_event_client_id(client_id: Any) -> str:
    return "".join(character for character in str(client_id or "")[:128] if character.isalnum() or character in "._:-")[:64]


def normalize_client_event_channels(channels: Any = None) -> frozenset[str]:
    if channels is None:
        return CLIENT_EVENT_CHANNELS
    values = channels.split(",") if isinstance(channels, str) else channels
    if not isinstance(values, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(value or "").strip() for value in values if str(value or "").strip() in CLIENT_EVENT_CHANNELS)


def client_event_type_channels(event_type: str) -> frozenset[str]:
    return CLIENT_EVENT_TYPE_CHANNELS.get(str(event_type or ""), frozenset({"core"}))


@dataclass
class ClientEventSubscriberRecord:
    queue: queue.Queue[dict[str, Any]]
    channels: frozenset[str]
    client_id: str = ""
    delivered_events: int = 0
    delivered_bytes: int = 0


class ClientEventBroker:
    def __init__(self, max_queue_size: int = 256):
        self.max_queue_size = max(1, max_queue_size)
        self.lock = threading.RLock()
        self.next_subscriber_id = 1
        self.next_event_id = 1
        self.subscribers: dict[int, ClientEventSubscriberRecord] = {}
        self.published_events = 0
        self.published_bytes = 0
        self.delivered_events = 0
        self.delivered_bytes = 0
        self.filtered_events = 0
        self.filtered_bytes = 0
        self.published_by_type: dict[str, dict[str, int]] = {}
        self.delivered_by_type: dict[str, dict[str, int]] = {}
        self.filtered_by_type: dict[str, dict[str, int]] = {}

    def subscribe(self, channels: Any = None, client_id: str = "") -> tuple[int, queue.Queue[dict[str, Any]]]:
        subscriber_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=self.max_queue_size)
        channel_set = normalize_client_event_channels(channels)
        with self.lock:
            subscriber_id = self.next_subscriber_id
            self.next_subscriber_id += 1
            self.subscribers[subscriber_id] = ClientEventSubscriberRecord(
                queue=subscriber_queue,
                channels=channel_set,
                client_id=normalize_client_event_client_id(client_id),
            )
        return subscriber_id, subscriber_queue

    def unsubscribe(self, subscriber_id: int) -> None:
        with self.lock:
            self.subscribers.pop(subscriber_id, None)

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        safe_type = str(event_type or "event")
        with self.lock:
            event = {
                "id": self.next_event_id,
                "type": safe_type,
                "time": time.time(),
                "payload": dict(payload or {}),
            }
            self.next_event_id += 1
            event_bytes = len(json.dumps(event, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
            event_channels = client_event_type_channels(safe_type)
            self.published_events += 1
            self.published_bytes += event_bytes
            self.increment_type_counter(self.published_by_type, safe_type, event_bytes)
            subscribers = list(self.subscribers.values())
            for subscriber in subscribers:
                if subscriber.channels.isdisjoint(event_channels):
                    self.filtered_events += 1
                    self.filtered_bytes += event_bytes
                    self.increment_type_counter(self.filtered_by_type, safe_type, event_bytes)
                    continue
                subscriber.delivered_events += 1
                subscriber.delivered_bytes += event_bytes
                self.delivered_events += 1
                self.delivered_bytes += event_bytes
                self.increment_type_counter(self.delivered_by_type, safe_type, event_bytes)
                self.enqueue(subscriber.queue, event)
        return event

    def aggregate_channels(self) -> frozenset[str]:
        with self.lock:
            return frozenset(channel for subscriber in self.subscribers.values() for channel in subscriber.channels)

    def has_demand(self, *channels: str) -> bool:
        requested = frozenset(str(channel or "") for channel in channels)
        return not self.aggregate_channels().isdisjoint(requested)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            channel_counts = {
                channel: sum(1 for subscriber in self.subscribers.values() if channel in subscriber.channels)
                for channel in sorted(CLIENT_EVENT_CHANNELS)
            }
            return {
                "subscribers": len(self.subscribers),
                "channel_counts": channel_counts,
                "published_events": self.published_events,
                "published_bytes": self.published_bytes,
                "delivered_events": self.delivered_events,
                "delivered_bytes": self.delivered_bytes,
                "filtered_events": self.filtered_events,
                "filtered_bytes": self.filtered_bytes,
                "published_by_type": self.counter_snapshot(self.published_by_type),
                "delivered_by_type": self.counter_snapshot(self.delivered_by_type),
                "filtered_by_type": self.counter_snapshot(self.filtered_by_type),
                "clients": [
                    {
                        "client_id": subscriber.client_id,
                        "channels": sorted(subscriber.channels),
                        "delivered_events": subscriber.delivered_events,
                        "delivered_bytes": subscriber.delivered_bytes,
                    }
                    for subscriber in list(self.subscribers.values())[:CLIENT_EVENT_SNAPSHOT_CLIENT_LIMIT]
                ],
            }

    @staticmethod
    def increment_type_counter(counters: dict[str, dict[str, int]], event_type: str, event_bytes: int) -> None:
        counter = counters.setdefault(event_type, {"events": 0, "bytes": 0})
        counter["events"] += 1
        counter["bytes"] += event_bytes

    @staticmethod
    def counter_snapshot(counters: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
        return {event_type: dict(counters[event_type]) for event_type in sorted(counters)}

    def enqueue(self, subscriber_queue: queue.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        try:
            subscriber_queue.put_nowait(event)
            return
        except queue.Full:
            pass
        try:
            subscriber_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            subscriber_queue.put_nowait(event)
        except queue.Full:
            pass
