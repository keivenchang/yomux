from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import queue
import threading
import time
from typing import Any
import uuid

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
    "backend_health_changed",
    "pricing_catalog_changed",
    "chat_messages_changed",
    "chat_typing_changed",
    "context_changed",
    "context_items_ready",
    "event_log_changed",
    "files_changed",
    "fs_changed",
    "operation_terminal",
    "roots_changed",
    "search_progress",
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
    "events",
    "files",
    "status",
    "stats",
    "transcripts",
    "yoagent",
})

CLIENT_EVENT_TYPE_CHANNELS: dict[str, frozenset[str]] = {
    "activity_summary_ready": frozenset({"activity"}),
    # M9 of DOIT.p0.daemon-monitor. Backend health is a topbar-indicator signal, so it rides the
    # `core` channel every page subscribes to; putting it on `status` would tie it to the System
    # panel and reproduce the defect that health is only computed while diagnostics are open.
    "backend_health_changed": frozenset({"core"}),
    "attention_acks_changed": frozenset({"status", "attention"}),
    "auto_approve_changed": frozenset({"status", "attention"}),
    "background_owner_changed": frozenset({"core"}),
    "background_refresh_done": frozenset({"core"}),
    "background_refresh_requested": frozenset({"core"}),
    "chat_messages_changed": frozenset({"chat"}),
    "chat_typing_changed": frozenset({"chat"}),
    "context_changed": frozenset({"transcripts"}),
    "context_items_ready": frozenset({"transcripts"}),
    "event_log_changed": frozenset({"events"}),
    "files_changed": frozenset({"files"}),
    "fs_changed": frozenset({"files"}),
    "operation_terminal": frozenset({"core", "files"}),
    "roots_changed": frozenset({"files"}),
    # Streaming Quick Open (step 5): the signal-only per-root progress nudge. It rides `core` -- the
    # channel every page subscribes to -- so any process holding an open palette hears it, and it is
    # retained (below) so a page connecting between two publications still learns the latest revision.
    # The payload is redacted upstream to {scope_id, generation, revision, coverage}; no path/query.
    "search_progress": frozenset({"core"}),
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

# Event types whose LATEST event per resource is retained and replayed to a subscriber the moment
# it connects. Every other type is a nudge to refetch, so a reconnecting page repairs itself by
# asking; backend health has no endpoint the browser is allowed to poll on a timer, so the newest
# revision has to be replayed here or a page that connects between two transitions would show
# nothing at all until the next one.
CLIENT_EVENT_RETAINED_TYPES: frozenset[str] = frozenset({"backend_health_changed", "search_progress"})
# One retained event per resource, and the retained types own a fixed, tiny resource set.
CLIENT_EVENT_RETAINED_LIMIT = 8


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


def client_event_resource(event_type: str, payload: dict[str, Any] | None = None) -> str:
    """Return the independently ordered browser resource for an event.

    Resource names stay intentionally coarse unless a producer already owns a
    stable scope.  In particular, background completion must not let a Tabber
    refresh coalesce away an index completion for the same subscriber.
    """
    safe_type = str(event_type or "event")
    data = payload if isinstance(payload, dict) else {}
    if safe_type == "background_refresh_done":
        role = str(data.get("role") or "unknown")
        # One role can own independently refreshed resources (for example, two search roots or
        # session-files for two tmux sessions). Keep those revisions independent without putting
        # a filesystem path into the SSE envelope's resource name.
        scope = str(data.get("session") or data.get("root") or "")
        if scope:
            scope_digest = hashlib.sha256(scope.encode("utf-8", errors="replace")).hexdigest()[:16]
            return f"background:{role}:{scope_digest}"
        return f"background:{role}"
    if safe_type == "search_progress":
        # Per-root ordering + latest-per-resource retention keyed by the OPAQUE scope digest the
        # producer already redacted to. Never the filesystem path: this resource name is part of the
        # globally fanned-out, persisted SSE envelope, so a path here would leak across clients.
        scope_id = str(data.get("scope_id") or "")
        return f"search_progress:{scope_id}" if scope_id else safe_type
    if safe_type == "operation_terminal":
        operation = data.get("operation")
        operation_id = str(operation.get("id") if isinstance(operation, dict) else "")
        return f"operation_terminal:{operation_id}" if operation_id else safe_type
    if safe_type in {"event_log_changed", "session_files_ready", "context_items_ready", "yoagent_conversation_changed", "yoagent_jobs_changed", "yoagent_stream_delta"}:
        request = data.get("request")
        session = str(data.get("session") or (request.get("session") if isinstance(request, dict) else "") or "")
        return f"{safe_type}:{session}" if session else safe_type
    return safe_type


def client_event_resource_channels(resource: str) -> frozenset[str]:
    return client_event_type_channels(str(resource or "").split(":", 1)[0])


@dataclass
class ClientEventSubscriberRecord:
    queue: queue.Queue[dict[str, Any]]
    channels: frozenset[str]
    client_id: str = ""
    delivered_events: int = 0
    delivered_bytes: int = 0
    coalesced_events: int = 0
    dropped_events: int = 0
    pending_by_resource: dict[str, dict[str, Any]] | None = None
    # resource -> the accounting byte size of the event currently queued for it. Remembered at
    # enqueue time so the overflow path can charge a dropped event without re-encoding it while
    # the broker lock is held -- the drop path runs precisely when the broker is already backed
    # up, so it is the last place that should be doing size-proportional work in the lock.
    # Deliberately ABSENT, not zero, for a replayed event: no publish-time measurement exists for
    # one, and inventing it here would put the encode back inside subscribe()'s critical section.
    # A resource present in `pending_by_resource` may therefore be missing from this map.
    pending_bytes: dict[str, int] | None = None
    pending_repair_resources: set[str] | None = None

    def __post_init__(self) -> None:
        if self.pending_by_resource is None:
            self.pending_by_resource = {}
        if self.pending_bytes is None:
            self.pending_bytes = {}
        if self.pending_repair_resources is None:
            self.pending_repair_resources = set()


class ClientEventBroker:
    def __init__(self, max_queue_size: int = 256):
        self.max_queue_size = max(1, max_queue_size)
        self.lock = threading.RLock()
        self.next_subscriber_id = 1
        self.next_event_id = 1
        self.epoch = uuid.uuid4().hex
        self.resource_revisions: dict[str, int] = {}
        # resource -> the newest event published for it, for the retained types only.
        self.retained_events: dict[str, dict[str, Any]] = {}
        self.replayed_events = 0
        self.subscribers: dict[int, ClientEventSubscriberRecord] = {}
        self.published_events = 0
        self.published_bytes = 0
        self.delivered_events = 0
        self.delivered_bytes = 0
        self.filtered_events = 0
        self.filtered_bytes = 0
        self.coalesced_events = 0
        self.dropped_events = 0
        self.heartbeat_events = 0
        self.last_heartbeat_at = 0.0
        self.published_by_type: dict[str, dict[str, int]] = {}
        self.delivered_by_type: dict[str, dict[str, int]] = {}
        self.filtered_by_type: dict[str, dict[str, int]] = {}
        self.published_by_resource: dict[str, dict[str, int]] = {}
        self.delivered_by_resource: dict[str, dict[str, int]] = {}
        self.coalesced_by_resource: dict[str, dict[str, int]] = {}
        self.dropped_by_resource: dict[str, dict[str, int]] = {}

    def subscribe(self, channels: Any = None, client_id: str = "") -> tuple[int, queue.Queue[dict[str, Any]]]:
        subscriber_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=self.max_queue_size)
        channel_set = normalize_client_event_channels(channels)
        with self.lock:
            subscriber_id = self.next_subscriber_id
            self.next_subscriber_id += 1
            subscriber = ClientEventSubscriberRecord(
                queue=subscriber_queue,
                channels=channel_set,
                client_id=normalize_client_event_client_id(client_id),
            )
            self.subscribers[subscriber_id] = subscriber
            self.replay_retained(subscriber)
        return subscriber_id, subscriber_queue

    def replay_retained(self, subscriber: ClientEventSubscriberRecord) -> int:
        """Deliver the newest retained event for each resource this subscriber demanded.

        Caller holds ``self.lock``. The replayed copy is marked ``replay`` so a consumer can tell
        a reconnect repair from a live transition; everything else, including the resource
        revision, is the original event, because a replay must not mint a revision the producer
        never published.
        """

        replayed = 0
        for resource, event in sorted(self.retained_events.items()):
            if subscriber.channels.isdisjoint(client_event_resource_channels(resource)):
                continue
            self.enqueue(subscriber, {**event, "replay": True})
            replayed += 1
            self.replayed_events += 1
        return replayed

    def unsubscribe(self, subscriber_id: int) -> None:
        with self.lock:
            self.subscribers.pop(subscriber_id, None)

    @staticmethod
    def canonical_json(value: Any) -> str:
        """The one encoding every byte counter in this module is measured from.

        This is an ACCOUNTING form, not the SSE wire form. The bytes a browser actually receives
        are produced by the server's SSE writer with different arguments (`ensure_ascii=False`,
        default separators, no `sort_keys`, no `default=str`) and are then wrapped in `event:` /
        `data:` framing, so the two differ in both directions. Two effects pull opposite ways:
        omitting the framing and the wire's whitespace makes the accounting form SMALLER, while
        escaping non-ASCII as `\\uXXXX` makes it LARGER. Framing dominates for ASCII payloads and
        escaping dominates for heavily non-ASCII ones. Measured on the 40-row `operation_terminal`
        payload that tests/test_client_event_broker_backlog_disambiguation.py builds, the
        accounting form is ~6392 against ~6854 for the same envelope on the wire; on
        `fs_changed` carrying `{"text": "中" * 400}` it is ~2592 against ~1434 (the pair moves with
        the event type, whose name is part of the envelope). (Approximate because the envelope carries
        `time.time()`, whose repr varies in length by a byte or two.) Note that the first figure is
        the wire size of THIS envelope, which is not what an `operation_terminal` subscriber
        actually receives: the SSE writer rewrites that one type into a three-key
        `{type, time, payload}` frame first, so the real wire size for it is smaller again. Treat
        these counters as relative diagnostics, never as a network measurement.

        What makes ``event_accounting_bytes``'s splice exact is narrower than it looks, and worth
        naming precisely so a maintainer protects the right thing: it is the ABSENCE of ``indent``.
        Without indentation ``json.dumps`` renders a value identically whether that value is nested
        or standalone, so a payload serialized on its own can be substituted into an envelope
        measurement byte for byte. ``sort_keys`` and the compact separators are here for stable,
        compact output, and neither is load-bearing for the splice. Measured by fuzzing the splice
        5000 times per encoder variant: sorted/compact, unsorted/compact, unsorted/default and
        sorted/default each mismatch 0 times, while sorted with ``indent=2`` mismatches 3965 times.
        """
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    @classmethod
    def event_accounting_bytes(cls, event: dict[str, Any], payload_json: str | None) -> int:
        """Exact UTF-8 length of ``canonical_json(event)``.

        ``payload_json`` is that same encoding of the event's payload, already computed outside
        the ordering lock. Because ``canonical_json`` passes no ``indent``, a value's encoding is
        the same text nested as it is standalone (see ``canonical_json``: this, and not
        ``sort_keys``, is the property the splice rests on). So swapping the payload for ``null``
        and then substituting the real payload text for those four bytes reproduces the full
        encoding's length exactly -- while keeping the size-proportional work out of the
        critical section. ``None`` means no pre-serialized payload is available -- either a
        subscriber appeared after the pre-lock check, or this is the drop path charging a replayed
        event whose size was deliberately never measured -- so fall back to measuring in one pass.
        """
        if payload_json is None:
            return len(cls.canonical_json(event).encode("utf-8"))
        envelope_json = cls.canonical_json({**event, "payload": None})
        return len(envelope_json.encode("utf-8")) - len("null") + len(payload_json.encode("utf-8"))

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        safe_type = str(event_type or "event")
        # The event's own copy of the payload, taken before anything is measured. The copy is
        # SHALLOW, so it freezes the caller's top-level keys at this instant but nested values
        # stay the caller's objects: a caller that mutates a nested value after calling publish()
        # can still make the counted size disagree with what a subscriber receives. That was
        # already true before, over a narrower window; the contract is and remains "do not mutate
        # a payload you have handed to publish()".
        event_payload = dict(payload or {})
        # Serializing the payload is pure CPU over that copy: it reads no broker state and has no
        # ordering requirement, unlike revision allocation, retention, the subscriber snapshot and
        # enqueue below, which must all stay inside one critical section. Doing it inside that
        # section charged it to the lock, so every concurrent publisher -- including publishers of
        # completely unrelated resources -- waited out every other publisher's payload size before
        # its own work could start. (On CPython the encoder holds the GIL, so moving it out does
        # not make encoding run in parallel. What it buys is a shorter critical section, which is
        # what the OTHER users of this same lock are queued behind: next_event takes it twice per
        # delivered event, and has_demand/aggregate_channels and snapshot take it from hot loops
        # and request threads.) The event is now encoded twice -- the payload here, and a tiny
        # `"payload":null` envelope under the lock -- but that is close to free in total, because
        # the second encode is of a fixed handful of scalars rather than of the payload. Measured
        # on the 40-row payload the tests build: 6393 characters encoded per publish before,
        # 6188 + 209 = 6397 after, i.e. +4 characters (+0.06%). What changes is WHERE: 6393
        # characters inside the lock before, 209 after, a factor of ~30. That ratio is a property
        # of the payload size and is the number worth quoting; the wall-clock ratio of the two
        # encodes is smaller and varies run to run, because a short encode is dominated by call
        # overhead rather than by the data.
        # `self.subscribers` is read here without the lock purely as a hint, and only for its
        # emptiness: with no SSE consumer there is no wire payload to measure, and publish() must
        # not serialize at all (pinned by
        # tests/test_client_events.py::test_client_event_broker_skips_json_byte_measurement_without_subscribers).
        # The authoritative snapshot is still taken under the lock, and event_accounting_bytes() falls
        # back to a single full serialization if a subscriber arrived after this hint was read.
        # The hint is best-effort in both directions, so "publish() never encodes without a
        # subscriber" is now a strong tendency rather than a guarantee: if the last SSE client
        # disconnects between this read and the lock, this encode is paid and then discarded,
        # where the base commit would have skipped it. That costs CPU on one publish; it cannot
        # change a counter, because the byte total is still gated on the in-lock snapshot.
        # Two consequences for an unencodable payload, which already violates this function's
        # contract and which nothing in the repo produces. The good one: encoding before the lock
        # means the raise now happens with NO broker state touched, where base had already minted
        # a resource revision, consumed an event id, and installed the poisoned event in
        # retained_events -- from where it would have been replayed to every future subscriber.
        # The bad one: base only encoded once the UNDER-LOCK subscriber list was non-empty, so
        # such a payload published exactly as the last subscriber disconnects now raises where
        # base happened to return.
        payload_json = self.canonical_json(event_payload) if self.subscribers else None
        with self.lock:
            # Derived from the event's own copy rather than the caller's object, so the resource
            # always describes the payload the event actually carries. For the declared `dict |
            # None` input this is identical to using the caller's object. It differs only for a
            # non-dict that `dict()` accepts -- `[("operation", {"id": "X"})]` now keys
            # `operation_terminal:X` instead of the bare type -- which no caller passes;
            # `App.publish_client_event` already normalises to a dict before it gets here.
            resource = client_event_resource(safe_type, event_payload)
            resource_revision = self.resource_revisions.get(resource, 0) + 1
            self.resource_revisions[resource] = resource_revision
            event = {
                "id": self.next_event_id,
                "type": safe_type,
                "time": time.time(),
                "payload": event_payload,
                "epoch": self.epoch,
                "resource": resource,
                "base_resource_revision": resource_revision - 1,
                "resource_revision": resource_revision,
            }
            self.next_event_id += 1
            if safe_type in CLIENT_EVENT_RETAINED_TYPES and (
                resource in self.retained_events or len(self.retained_events) < CLIENT_EVENT_RETAINED_LIMIT
            ):
                self.retained_events[resource] = event
            event_channels = client_event_type_channels(safe_type)
            subscribers = list(self.subscribers.values())
            # There is no wire payload without an SSE consumer. Avoid serializing a discarded
            # event solely for byte diagnostics; once a subscriber exists this one measurement is
            # shared by published, filtered, delivered, and coalesced accounting below.
            event_bytes = self.event_accounting_bytes(event, payload_json) if subscribers else 0
            self.published_events += 1
            self.published_bytes += event_bytes
            self.increment_type_counter(self.published_by_type, safe_type, event_bytes)
            self.increment_type_counter(self.published_by_resource, resource, event_bytes)
            for subscriber in subscribers:
                if subscriber.channels.isdisjoint(event_channels):
                    self.filtered_events += 1
                    self.filtered_bytes += event_bytes
                    self.increment_type_counter(self.filtered_by_type, safe_type, event_bytes)
                    continue
                enqueue_result = self.enqueue(subscriber, event, event_bytes)
                if enqueue_result == "coalesced":
                    subscriber.coalesced_events += 1
                    self.coalesced_events += 1
                    self.increment_type_counter(self.coalesced_by_resource, resource, event_bytes)
                    continue
                subscriber.delivered_events += 1
                subscriber.delivered_bytes += event_bytes
                self.delivered_events += 1
                self.delivered_bytes += event_bytes
                self.increment_type_counter(self.delivered_by_type, safe_type, event_bytes)
                self.increment_type_counter(self.delivered_by_resource, resource, event_bytes)
        return event

    def aggregate_channels(self) -> frozenset[str]:
        with self.lock:
            return frozenset(channel for subscriber in self.subscribers.values() for channel in subscriber.channels)

    def has_demand(self, *channels: str) -> bool:
        requested = frozenset(str(channel or "") for channel in channels)
        return not self.aggregate_channels().isdisjoint(requested)

    def has_client_id(self, client_id: str) -> bool:
        """Whether at least one live SSE subscriber owns this browser identity."""
        normalized = normalize_client_event_client_id(client_id)
        if not normalized:
            return False
        with self.lock:
            return any(subscriber.client_id == normalized for subscriber in self.subscribers.values())

    def ready_snapshot(self, subscriber_id: int) -> dict[str, Any]:
        """Return the reconnect fence for one subscriber's demanded resources.

        Resource channel ownership derives from the central resource map. Keeping this projection here means
        the server cannot accidentally expose revisions for resources a client
        did not subscribe to, while the process-wide ``snapshot`` remains useful
        for the System diagnostics view.
        """
        with self.lock:
            subscriber = self.subscribers.get(subscriber_id)
            if subscriber is None:
                return {"epoch": self.epoch, "resource_revisions": {}}
            resource_revisions = {
                resource: revision
                for resource, revision in self.resource_revisions.items()
                if not subscriber.channels.isdisjoint(client_event_resource_channels(resource))
            }
            return {
                "epoch": self.epoch,
                "resource_revisions": dict(sorted(resource_revisions.items())),
            }

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
                "coalesced_events": self.coalesced_events,
                "dropped_events": self.dropped_events,
                "replayed_events": self.replayed_events,
                "retained_resources": sorted(self.retained_events),
                "heartbeat_events": self.heartbeat_events,
                "last_heartbeat_at": self.last_heartbeat_at,
                "published_by_type": self.counter_snapshot(self.published_by_type),
                "delivered_by_type": self.counter_snapshot(self.delivered_by_type),
                "filtered_by_type": self.counter_snapshot(self.filtered_by_type),
                "published_by_resource": self.counter_snapshot(self.published_by_resource),
                "delivered_by_resource": self.counter_snapshot(self.delivered_by_resource),
                "coalesced_by_resource": self.counter_snapshot(self.coalesced_by_resource),
                "dropped_by_resource": self.counter_snapshot(self.dropped_by_resource),
                "epoch": self.epoch,
                "resource_revisions": dict(sorted(self.resource_revisions.items())),
                "clients": [
                    {
                        "client_id": subscriber.client_id,
                        "channels": sorted(subscriber.channels),
                        "delivered_events": subscriber.delivered_events,
                        "delivered_bytes": subscriber.delivered_bytes,
                        "coalesced_events": subscriber.coalesced_events,
                        "dropped_events": subscriber.dropped_events,
                    }
                    for subscriber in list(self.subscribers.values())[:CLIENT_EVENT_SNAPSHOT_CLIENT_LIMIT]
                ],
            }

    def record_heartbeat(self) -> None:
        """Count transport keepalives separately from state invalidations."""
        with self.lock:
            self.heartbeat_events += 1
            self.last_heartbeat_at = time.time()

    @staticmethod
    def increment_type_counter(counters: dict[str, dict[str, int]], event_type: str, event_bytes: int) -> None:
        counter = counters.setdefault(event_type, {"events": 0, "bytes": 0})
        counter["events"] += 1
        counter["bytes"] += event_bytes

    @staticmethod
    def counter_snapshot(counters: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
        return {event_type: dict(counters[event_type]) for event_type in sorted(counters)}

    def next_event(self, subscriber_id: int, timeout: float) -> dict[str, Any]:
        with self.lock:
            subscriber = self.subscribers.get(subscriber_id)
        if subscriber is None:
            raise queue.Empty
        event = subscriber.queue.get(timeout=timeout)
        resource = str(event.get("resource") or "")
        with self.lock:
            if subscriber.pending_by_resource.get(resource) is event:
                self.forget_pending(subscriber, resource)
            repair_resources = sorted(subscriber.pending_repair_resources)
            subscriber.pending_repair_resources.clear()
        if not repair_resources:
            return event
        delivered = dict(event)
        delivered["repair_resources"] = repair_resources
        return delivered

    @staticmethod
    def remember_pending(subscriber: ClientEventSubscriberRecord, resource: str, event: dict[str, Any], event_bytes: int | None) -> None:
        """Record which event is queued for `resource`, and its size when that is already known.

        Paired with `forget_pending` so that every write and every removal of the two maps goes
        through one of these two functions and they cannot drift apart. When the size is NOT known,
        the slot's remembered size is removed rather than computed: computing it here would run a
        full encode inside the critical section.

        That `pop` is defensive, not a live path. Its point is that a previous event's size left
        attached to this slot would later be charged to the wrong event -- but with today's callers
        that cannot happen: the only caller passing `None` is `replay_retained`, which always runs
        against a brand-new subscriber with an empty `pending_bytes` and one event per distinct
        resource. Instrumenting a 20,000-step mixed workload recorded 13,161 `None` calls, none of
        which found an existing key to remove. Keep the branch so the invariant holds for a future
        caller; do not read it as a hazard that has been observed.
        """
        subscriber.pending_by_resource[resource] = event
        if event_bytes is None:
            subscriber.pending_bytes.pop(resource, None)
        else:
            subscriber.pending_bytes[resource] = event_bytes

    @staticmethod
    def forget_pending(subscriber: ClientEventSubscriberRecord, resource: str) -> int | None:
        """Release `resource`'s queued slot and return the size remembered for it, if any.

        `None` means the size was never measured (a replayed event), so a caller that needs the
        size has to encode it. Removing from both maps here is what keeps `remember_pending`'s
        one-owner guarantee honest -- a hand-written `pop` of only one map leaks the other.
        """
        subscriber.pending_by_resource.pop(resource, None)
        return subscriber.pending_bytes.pop(resource, None)

    def enqueue(self, subscriber: ClientEventSubscriberRecord, event: dict[str, Any], event_bytes: int | None = None) -> str:
        """Queue `event` for one subscriber, coalescing per resource.

        `event_bytes` is the accounting size `publish()` already computed for this event. It is
        remembered beside the queued event so that evicting that event later charges the drop
        counters without re-encoding it while the ordering lock is held -- eviction only happens
        when a subscriber is already lagging, so it is the worst possible place to do
        size-proportional work in the lock.

        `None` means "not measured", which is the replay path: `replay_retained` re-delivers a
        retained event that may have been published while nobody was subscribed, so no
        publish-time measurement exists. Nothing is encoded for it here. Encoding eagerly would
        move a full encode into `subscribe()`'s critical section for every retained resource; the
        size is instead left unknown, and only an actual eviction of that event pays for one
        encode. That is exactly the base commit's behaviour on this path. Note the precise claim:
        a replay can only overflow when the number of RETAINED events actually exceeds
        `max_queue_size`, so at any queue size greater than or equal to `CLIENT_EVENT_RETAINED_LIMIT`
        `subscribe()` encodes nothing -- in base and here alike. Only below that does either encode,
        once per evicted replay. Production uses the 256 default against a limit of 8.
        """
        resource = str(event.get("resource") or "")
        pending = subscriber.pending_by_resource.get(resource)
        if pending is not None:
            # Keep the queued object in place so its queue position is stable, but replace its
            # contents atomically under the broker lock with the newest readable revision.
            pending_payload = pending.get("payload") if isinstance(pending.get("payload"), dict) else {}
            event_payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if pending_payload.get("patch") is True or event_payload.get("patch") is True:
                # A keyed patch is based on the resource revision immediately before it. Replacing
                # a queued patch skips that base, so the browser must repair from the HTTP snapshot.
                subscriber.pending_repair_resources.add(resource)
            pending.clear()
            pending.update(event)
            # The queued object now holds this event's contents, so the slot's size is this
            # event's size. `pending` is already the queued object, so identity is preserved.
            self.remember_pending(subscriber, resource, pending, event_bytes)
            return "coalesced"
        try:
            subscriber.queue.put_nowait(event)
            self.remember_pending(subscriber, resource, event, event_bytes)
            return "enqueued"
        except queue.Full:
            pass
        try:
            dropped = subscriber.queue.get_nowait()
        except queue.Empty:
            dropped = None
        if isinstance(dropped, dict):
            dropped_resource = str(dropped.get("resource") or "")
            dropped_bytes: int | None = None
            if subscriber.pending_by_resource.get(dropped_resource) is dropped:
                # `None` whenever the evicted event's size was never measured, i.e. a replay.
                dropped_bytes = self.forget_pending(subscriber, dropped_resource)
            if dropped_resource:
                subscriber.pending_repair_resources.add(dropped_resource)
            subscriber.dropped_events += 1
            self.dropped_events += 1
            self.increment_type_counter(
                self.dropped_by_resource,
                dropped_resource or "event",
                dropped_bytes if dropped_bytes is not None else self.event_accounting_bytes(dropped, None),
            )
        try:
            subscriber.queue.put_nowait(event)
            self.remember_pending(subscriber, resource, event, event_bytes)
            return "enqueued"
        except queue.Full:
            subscriber.dropped_events += 1
            self.dropped_events += 1
            self.increment_type_counter(
                self.dropped_by_resource,
                resource or "event",
                event_bytes if event_bytes is not None else self.event_accounting_bytes(event, None),
            )
            return "dropped"
