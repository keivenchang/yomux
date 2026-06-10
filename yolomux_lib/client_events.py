from __future__ import annotations

import queue
import threading
import time
from typing import Any


class ClientEventBroker:
    def __init__(self, max_queue_size: int = 256):
        self.max_queue_size = max(1, max_queue_size)
        self.lock = threading.RLock()
        self.next_subscriber_id = 1
        self.next_event_id = 1
        self.subscribers: dict[int, queue.Queue[dict[str, Any]]] = {}

    def subscribe(self) -> tuple[int, queue.Queue[dict[str, Any]]]:
        subscriber_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=self.max_queue_size)
        with self.lock:
            subscriber_id = self.next_subscriber_id
            self.next_subscriber_id += 1
            self.subscribers[subscriber_id] = subscriber_queue
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
            subscribers = list(self.subscribers.values())
        for subscriber_queue in subscribers:
            self.enqueue(subscriber_queue, event)
        return event

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
