# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded acceptance ledger for asynchronous HTTP delivery promises."""

from __future__ import annotations

import collections
import copy
from datetime import datetime
from datetime import timezone
import json
import hashlib
import logging
from pathlib import Path
import threading
import time
import uuid
from http import HTTPStatus
from typing import Any
from typing import Callable
from typing import Mapping

from ..infra.atomic_file import append_fsync_text
from ..infra.atomic_file import atomic_write_text
from ..infra.atomic_file import file_lock


QUEUED_DELIVERY_FRAME_LIMIT = 512
QUEUED_OPERATION_RECORD_LIMIT = 512
QUEUED_OPERATION_RETENTION_SECONDS = 10 * 60
QUEUED_OPERATION_REPLAY_RECORD_BYTES = 256 * 1024
QUEUED_OPERATION_REPLAY_TOTAL_BYTES = 2 * 1024 * 1024
QUEUED_OPERATION_STATE_VERSION = 1
QUEUED_OPERATION_JOURNAL_VERSION = 2
QUEUED_OPERATION_ACK_JOURNAL_VERSION = 3
QUEUED_OPERATION_COMPACT_BYTES = 4 * 1024 * 1024
QUEUED_OPERATION_COMPACT_RECORDS = 256
QUEUED_OPERATION_COMPACT_MIN_INTERVAL_SECONDS = 30.0
QUEUED_OPERATION_COMPACT_MAX_DEFER_SECONDS = 5 * 60.0
QUEUED_OPERATION_COMPACT_RETRY_SECONDS = 5.0
QUEUED_OPERATION_ACCEPTED_STATUS = int(HTTPStatus.ACCEPTED)
QUEUED_DELIVERY_OPEN_STATES = frozenset({"pending", "queued", "running"})
QUEUED_DELIVERY_DONE_STATES = frozenset({"done", "ready", "success"})
QUEUED_DELIVERY_ERROR_STATES = frozenset({"error", "failed", "unavailable"})
logger = logging.getLogger(__name__)


class QueuedDeliveryLedger:
    """Track each qualified-key/epoch promise until one terminal response."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        frame_limit: int = QUEUED_DELIVERY_FRAME_LIMIT,
        state_path: Path | None = None,
        operation_epoch: str = "",
        operation_id_factory: Callable[[], str] | None = None,
        operation_retention_seconds: float = QUEUED_OPERATION_RETENTION_SECONDS,
        compaction_clock: Callable[[], float] = time.monotonic,
        compaction_signal: Callable[[], None] | None = None,
    ) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._outstanding: dict[tuple[str, int], dict[str, Any]] = {}
        self._frames: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=max(1, int(frame_limit)),
        )
        self._state_path = Path(state_path) if state_path is not None else None
        self._operation_epoch = str(operation_epoch or uuid.uuid4().hex)
        self._operation_id_factory = operation_id_factory or (lambda: f"op-{uuid.uuid4().hex}")
        self._operation_retention_seconds = max(1.0, float(operation_retention_seconds))
        self._operations: dict[str, dict[str, Any]] = {}
        self._compaction_clock = compaction_clock
        self._compaction_signal = compaction_signal
        self._journal_tail_bytes = 0
        self._journal_tail_records = 0
        self._journal_mutation_generation = 0
        self._ack_generation = 0
        self._pending_ack_since: float | None = None
        self._latest_ack_at = 0.0
        self._last_compaction_at = 0.0
        self._load_operations()

    @staticmethod
    def _deadline_text(deadline_at: float) -> str:
        return datetime.fromtimestamp(float(deadline_at), timezone.utc).isoformat().replace("+00:00", "Z")

    def _load_operations(self) -> None:
        if self._state_path is None or not self._state_path.is_file():
            return
        try:
            raw = self._state_path.read_text(encoding="utf-8")
            self._load_raw_operations(raw)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            logger.exception("failed to load queued-operation state %s", self._state_path)
            self._operations.clear()
            self._journal_tail_bytes = 0
            self._journal_tail_records = 0

    def _load_raw_operations(self, raw: str) -> None:
        try:
            state = json.loads(raw)
        except json.JSONDecodeError:
            state = None
        if isinstance(state, dict) and int(state.get("version") or 0) == QUEUED_OPERATION_STATE_VERSION:
            self._load_snapshot_operations(state)
            self._journal_tail_bytes = 0
            self._journal_tail_records = 0
        else:
            self._load_journal_operations(raw)
        self._prune_operations_locked()
        self._bound_terminal_replays_locked()

    def _load_snapshot_operations(self, state: dict[str, Any]) -> None:
        records = state.get("operations")
        if not isinstance(records, list):
            raise ValueError("queued-operation records must be a list")
        self._operations.clear()
        self._load_operation_records(records)
        epoch = str(state.get("epoch") or "")
        if epoch:
            self._operation_epoch = epoch

    def _load_operation_records(self, records: list[Any]) -> None:
        for value in records:
            if not isinstance(value, dict):
                continue
            operation_id = str(value.get("id") or "")
            if operation_id.startswith("op-"):
                record = copy.deepcopy(value)
                event = record.get("terminal_event")
                if isinstance(event, dict):
                    event_bytes = record.get("terminal_event_bytes")
                    if not isinstance(event_bytes, int) or isinstance(event_bytes, bool) or event_bytes <= 0:
                        record["terminal_event_bytes"] = self._terminal_event_bytes(event)
                self._operations[operation_id] = record

    def _load_journal_operations(self, raw: str) -> None:
        lines = raw.splitlines()
        self._journal_tail_bytes = 0
        self._journal_tail_records = 0
        pending_ack_records = 0
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                if index == len(lines) - 1:
                    return
                raise ValueError("malformed queued-operation journal record") from None
            if not isinstance(entry, dict):
                raise ValueError("unsupported queued-operation journal record")
            version = int(entry.get("version") or 0)
            kind = str(entry.get("type") or "")
            if version == QUEUED_OPERATION_JOURNAL_VERSION and kind == "snapshot":
                records = entry.get("operations")
                if not isinstance(records, list):
                    raise ValueError("queued-operation journal snapshot must contain records")
                self._operations.clear()
                self._load_operation_records(records)
                self._journal_tail_bytes = 0
                self._journal_tail_records = 0
                pending_ack_records = 0
            elif version == QUEUED_OPERATION_JOURNAL_VERSION and kind == "operation":
                record = entry.get("record")
                if not isinstance(record, dict):
                    raise ValueError("queued-operation journal record must contain an operation")
                self._load_operation_records([record])
                self._journal_tail_bytes += len(line.encode("utf-8")) + 1
                self._journal_tail_records += 1
            elif version == QUEUED_OPERATION_ACK_JOURNAL_VERSION and kind == "ack":
                acknowledgments = entry.get("acks")
                if not isinstance(acknowledgments, list) or not all(isinstance(item, dict) for item in acknowledgments):
                    raise ValueError("queued-operation acknowledgement record must contain acknowledgements")
                self._apply_operation_acknowledgments_locked(acknowledgments)
                self._journal_tail_bytes += len(line.encode("utf-8")) + 1
                self._journal_tail_records += 1
                pending_ack_records += 1
            else:
                raise ValueError("unsupported queued-operation journal record type")
            epoch = str(entry.get("epoch") or "")
            if epoch:
                self._operation_epoch = epoch
        self._journal_mutation_generation = self._journal_tail_records
        if pending_ack_records:
            now = self._compaction_clock()
            self._ack_generation = pending_ack_records
            self._latest_ack_at = now
            self._pending_ack_since = now

    def _prune_operations_locked(self) -> None:
        now = self.clock()
        expired = [
            operation_id
            for operation_id, record in self._operations.items()
            if str(record.get("state") or "") != "queued"
            and now - float(record.get("terminal_at") or record.get("created_at") or now) > self._operation_retention_seconds
        ]
        for operation_id in expired:
            self._operations.pop(operation_id, None)
        if len(self._operations) <= QUEUED_OPERATION_RECORD_LIMIT:
            return
        terminal = sorted(
            (
                record
                for record in self._operations.values()
                if str(record.get("state") or "") != "queued"
            ),
            key=lambda record: float(record.get("terminal_at") or record.get("created_at") or 0.0),
        )
        for record in terminal[:max(0, len(self._operations) - QUEUED_OPERATION_RECORD_LIMIT)]:
            self._operations.pop(str(record.get("id") or ""), None)

    @staticmethod
    def _terminal_event_bytes(event: dict[str, Any]) -> int:
        return len(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    @staticmethod
    def _bounded_terminal_event(record: dict[str, Any]) -> dict[str, Any]:
        operation_id = str(record.get("id") or "")
        request_id = str(record.get("request_id") or "")
        cursor = copy.deepcopy(record.get("cursor") or {})
        result = {
            "state": "failed",
            "request": {"id": request_id},
            "error": {
                "code": "operation_replay_evicted",
                "message": {
                    "key": "common.operationReplayEvicted",
                    "params": {"operation_id": operation_id},
                    "fallback": "The completed operation is no longer available for replay.",
                },
                "origin": "server.operation_ledger",
                "retryable": False,
                "details": {"operation_id": operation_id},
                "stack": [{
                    "component": "server.operation_ledger",
                    "operation": "GET /api/operations/{id}",
                    "code": "operation_replay_evicted",
                }],
            },
        }
        return {
            "operation": {"id": operation_id, "cursor": cursor},
            "result": result,
            "status": int(HTTPStatus.GONE),
        }

    def _bound_terminal_replays_locked(self) -> bool:
        changed = False
        exposed = sorted(
            (
                record
                for record in self._operations.values()
                if bool(record.get("delivery_acknowledged")) and isinstance(record.get("terminal_event"), dict)
            ),
            key=lambda record: float(record.get("terminal_at") or record.get("created_at") or 0.0),
        )
        total_bytes = sum(int(record.get("terminal_event_bytes") or 0) for record in exposed)
        for record in exposed:
            event_bytes = int(record.get("terminal_event_bytes") or 0)
            if event_bytes <= QUEUED_OPERATION_REPLAY_RECORD_BYTES and total_bytes <= QUEUED_OPERATION_REPLAY_TOTAL_BYTES:
                continue
            bounded = self._bounded_terminal_event(record)
            if record["terminal_event"] == bounded:
                continue
            bounded_bytes = self._terminal_event_bytes(bounded)
            record["terminal_event"] = bounded
            record["terminal_event_bytes"] = bounded_bytes
            record["http_status"] = int(HTTPStatus.GONE)
            total_bytes += bounded_bytes - event_bytes
            changed = True
        return changed

    def _snapshot_payload_locked(self) -> dict[str, Any]:
        return {
            "version": QUEUED_OPERATION_JOURNAL_VERSION,
            "type": "snapshot",
            "epoch": self._operation_epoch,
            "operations": sorted(
                (copy.deepcopy(record) for record in self._operations.values()),
                key=lambda record: (float(record.get("created_at") or 0.0), str(record.get("id") or "")),
            ),
        }

    @staticmethod
    def _journal_text(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"

    def _append_payload_locked(self, payload: dict[str, Any]) -> None:
        if self._state_path is None:
            return
        text = self._journal_text(payload)
        append_fsync_text(self._state_path, text, mode=0o600)
        self._journal_tail_bytes += len(text.encode("utf-8"))
        self._journal_tail_records += 1
        self._journal_mutation_generation += 1

    def _append_operation_locked(self, record: dict[str, Any]) -> None:
        if self._state_path is None:
            return
        payload = {
            "version": QUEUED_OPERATION_JOURNAL_VERSION,
            "type": "operation",
            "epoch": self._operation_epoch,
            "record": record,
        }
        self._append_payload_locked(payload)

    def _append_acknowledgments_locked(self, acknowledgments: list[dict[str, Any]]) -> None:
        self._append_payload_locked({
            "version": QUEUED_OPERATION_ACK_JOURNAL_VERSION,
            "type": "ack",
            "epoch": self._operation_epoch,
            "acks": acknowledgments,
        })

    def compact_operations(self) -> None:
        """Replace the journal with one durable snapshot; call only from an out-of-band owner."""
        if self._state_path is None:
            return
        request = self.operation_compaction_request(force=True)
        compact_queued_delivery_journal(
            self._state_path,
            clock=self.clock,
            operation_retention_seconds=self._operation_retention_seconds,
        )
        if request is not None:
            self.note_operation_compaction_succeeded(request)

    def accept_operation(
        self,
        *,
        request_id: str,
        route: str,
        deadline_at: float,
        progress: dict[str, Any],
        producer: dict[str, Any],
        kind: str = "",
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically persist one accepted operation before exposing its receipt."""
        with self._lock:
            operation_id = self._operation_id_factory()
            while operation_id in self._operations:
                operation_id = self._operation_id_factory()
            cursor = {"epoch": self._operation_epoch, "seq": 0}
            operation_context = copy.deepcopy(context or {})
            operation = {
                "id": operation_id,
                "deadline_at": self._deadline_text(deadline_at),
                "status_url": f"/api/operations/{operation_id}",
                "events_url": f"/api/client-events?operation_id={operation_id}",
                "cursor": cursor,
                "progress": copy.deepcopy(progress),
            }
            if kind:
                operation["kind"] = str(kind)
            if context:
                operation["context"] = operation_context
            receipt = {
                "state": "queued",
                "request": {"id": str(request_id)},
                "operation": operation,
            }
            self._operations[operation_id] = {
                "id": operation_id,
                "state": "queued",
                "request_id": str(request_id),
                "route": str(route),
                "kind": str(kind),
                "context": operation_context,
                "created_at": self.clock(),
                "deadline_at": float(deadline_at),
                "cursor": cursor,
                "producer": copy.deepcopy(producer),
                "receipt": receipt,
                "receipt_exposed": False,
                "delivery_acknowledged": False,
                "http_status": QUEUED_OPERATION_ACCEPTED_STATUS,
            }
            self._prune_operations_locked()
            self._append_operation_locked(self._operations[operation_id])
            return copy.deepcopy(receipt)

    def terminalize_operation(
        self,
        operation_id: str,
        result: dict[str, Any],
        status: HTTPStatus | int,
    ) -> dict[str, Any] | None:
        """Persist one terminal result, returning its event only for the new transition."""
        with self._lock:
            record = self._operations.get(str(operation_id))
            if record is None:
                return None
            existing = record.get("terminal_event")
            if isinstance(existing, dict):
                return None
            cursor = {
                "epoch": str(record.get("cursor", {}).get("epoch") or self._operation_epoch),
                "seq": int(record.get("cursor", {}).get("seq") or 0) + 1,
            }
            event = {
                "operation": {"id": str(operation_id), "cursor": cursor},
                "result": copy.deepcopy(result),
                "status": int(status),
            }
            record.update({
                "state": str(result.get("state") or "failed"),
                "cursor": cursor,
                "terminal_at": self.clock(),
                "terminal_event": event,
                "terminal_event_bytes": self._terminal_event_bytes(event),
                "http_status": int(status),
            })
            self._prune_operations_locked()
            self._append_operation_locked(record)
            return copy.deepcopy(event)

    # Exactly the scheduling facts needed to attribute a slow operation, and nothing else. A
    # completed operation's total wall time cannot distinguish "the task was slow" from "the task
    # waited behind a lane holder"; queue_wait_ms versus execution_ms can, and lane/task name which
    # holder to look at. Values are numbers and short identifiers only -- no paths.
    OPERATION_SCHEDULE_FIELDS: tuple[str, ...] = (
        "task", "priority", "lane",
        "submitted_at", "running_started_at", "completed_at",
        "queue_wait_ms", "execution_ms", "transient_polls",
    )

    def record_operation_schedule(self, operation_id: str, schedule: Mapping[str, object]) -> bool:
        """Retain one operation's bounded lane/wait/execution facts for later attribution."""
        bounded = {
            key: (round(float(value), 3) if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)[:64])
            for key, value in schedule.items()
            if key in self.OPERATION_SCHEDULE_FIELDS
        }
        if not bounded:
            return False
        with self._lock:
            record = self._operations.get(str(operation_id))
            if record is None:
                return False
            record["schedule"] = bounded
            return True

    def _matching_operation_acknowledgments_locked(
        self,
        acknowledgments: list[dict[str, Any]],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        acknowledged: list[str] = []
        newly_acknowledged: list[dict[str, Any]] = []
        new_ids: set[str] = set()
        for acknowledgment in acknowledgments:
            operation_id = str(acknowledgment.get("id") or "")
            cursor = acknowledgment.get("cursor")
            record = self._operations.get(operation_id)
            event = record.get("terminal_event") if isinstance(record, dict) else None
            event_cursor = event.get("operation", {}).get("cursor") if isinstance(event, dict) else None
            if not isinstance(cursor, dict) or not isinstance(event_cursor, dict) or event_cursor != cursor:
                continue
            acknowledged.append(operation_id)
            if bool(record.get("delivery_acknowledged")) or operation_id in new_ids:
                continue
            newly_acknowledged.append({"id": operation_id, "cursor": copy.deepcopy(cursor)})
            new_ids.add(operation_id)
        return acknowledged, newly_acknowledged

    def _apply_operation_acknowledgments_locked(self, acknowledgments: list[dict[str, Any]]) -> list[str]:
        acknowledged, newly_acknowledged = self._matching_operation_acknowledgments_locked(acknowledgments)
        for acknowledgment in newly_acknowledged:
            self._operations[acknowledgment["id"]]["delivery_acknowledged"] = True
        return acknowledged

    def acknowledge_operation_deliveries(self, acknowledgments: list[dict[str, Any]]) -> list[str]:
        """Retire exact replay bytes only after the browser processed each terminal."""

        signal: Callable[[], None] | None = None
        with self._lock:
            acknowledged, newly_acknowledged = self._matching_operation_acknowledgments_locked(acknowledgments)
            if newly_acknowledged:
                # Persistence is the commit point. If append/fsync fails, live state remains
                # unacknowledged and the browser's exact retry can durably try again.
                self._append_acknowledgments_locked(newly_acknowledged)
                self._apply_operation_acknowledgments_locked(newly_acknowledged)
                self._bound_terminal_replays_locked()
                now = self._compaction_clock()
                self._ack_generation += 1
                self._latest_ack_at = now
                if self._pending_ack_since is None:
                    self._pending_ack_since = now
                signal = self._compaction_signal
        if signal is not None:
            signal()
        return acknowledged

    def set_operation_compaction_signal(self, signal: Callable[[], None] | None) -> None:
        with self._lock:
            self._compaction_signal = signal

    def operation_compaction_request(self, *, force: bool = False) -> dict[str, Any] | None:
        with self._lock:
            if self._state_path is None:
                return None
            now = self._compaction_clock()
            if not force and self._pending_ack_since is None:
                return None
            threshold_due = (
                self._journal_tail_bytes >= QUEUED_OPERATION_COMPACT_BYTES
                or self._journal_tail_records >= QUEUED_OPERATION_COMPACT_RECORDS
            )
            due_at = now if force or threshold_due else float(self._pending_ack_since) + QUEUED_OPERATION_COMPACT_MAX_DEFER_SECONDS
            due_at = max(due_at, self._last_compaction_at + QUEUED_OPERATION_COMPACT_MIN_INTERVAL_SECONDS)
            return {
                "state_path": self._state_path,
                "due_at": due_at,
                "requested_at": now,
                "tail_bytes": self._journal_tail_bytes,
                "tail_records": self._journal_tail_records,
                "mutation_generation": self._journal_mutation_generation,
                "ack_generation": self._ack_generation,
            }

    def note_operation_compaction_succeeded(self, request: Mapping[str, Any]) -> None:
        with self._lock:
            self._journal_tail_bytes = max(0, self._journal_tail_bytes - int(request.get("tail_bytes") or 0))
            self._journal_tail_records = max(0, self._journal_tail_records - int(request.get("tail_records") or 0))
            self._last_compaction_at = self._compaction_clock()
            if self._ack_generation <= int(request.get("ack_generation") or 0):
                self._pending_ack_since = None
            else:
                self._pending_ack_since = self._latest_ack_at

    def acknowledge_operation_delivery(self, operation_id: str, cursor: dict[str, Any]) -> bool:
        return bool(self.acknowledge_operation_deliveries([{"id": operation_id, "cursor": cursor}]))

    def operation_status(self, operation_id: str) -> tuple[dict[str, Any], HTTPStatus] | None:
        with self._lock:
            record = self._operations.get(str(operation_id))
            if record is None:
                return None
            event = record.get("terminal_event")
            if isinstance(event, dict) and isinstance(event.get("result"), dict):
                return copy.deepcopy(event["result"]), HTTPStatus(int(record.get("http_status") or HTTPStatus.INTERNAL_SERVER_ERROR))
            receipt = record.get("receipt")
            if isinstance(receipt, dict):
                return copy.deepcopy(receipt), HTTPStatus.ACCEPTED
            return None

    def operation_replay_event(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._operations.get(str(operation_id))
            event = record.get("terminal_event") if isinstance(record, dict) else None
            return copy.deepcopy(event) if isinstance(event, dict) else None

    def operation_context(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._operations.get(str(operation_id))
            if not isinstance(record, dict):
                return None
            context = record.get("context")
            return copy.deepcopy(context) if isinstance(context, dict) else {}

    def open_operations(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                copy.deepcopy(record)
                for record in self._operations.values()
                if str(record.get("state") or "") == "queued"
            ]

    @staticmethod
    def _candidate_payloads(payload: object) -> tuple[dict[str, Any], ...]:
        if not isinstance(payload, dict):
            return ()
        return (payload, *(value for value in payload.values() if isinstance(value, dict)))

    @staticmethod
    def _identity(payload: dict[str, Any]) -> tuple[str, int] | None:
        key = str(payload.get("key") or "").strip()
        epoch = payload.get("epoch")
        if not key or isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
            return None
        return key, epoch

    def _terminalize_qualified_promise_locked(
        self,
        identity: tuple[str, int],
        terminal_state: str,
        *,
        reason: str = "",
    ) -> bool:
        if identity not in self._outstanding:
            return False
        key, epoch = identity
        self._outstanding.pop(identity)
        frame = {
            "stream": key,
            "epoch": epoch,
            "seq": 1,
            "state": terminal_state,
        }
        if terminal_state == "error" and reason:
            frame["reason"] = reason
        self._frames.append(frame)
        return True

    def observe_ready_product(self, key: str, epoch: int) -> None:
        """Register a ready byte product before its response boundary is written."""

        identity = self._identity({"key": key, "epoch": epoch})
        if identity is None:
            raise ValueError("ready product promise requires a non-empty key and non-negative epoch")
        with self._lock:
            if not self._terminalize_qualified_promise_locked(identity, "done"):
                raise ValueError("ready product promise was not registered as outstanding")

    def observe_http_commit(self, payload: object, status: HTTPStatus | int) -> None:
        """Register accepted/committed OUTSTANDING queued state and terminal transitions.

        Invariant: outstanding registration reflects server-side queued state and is honest
        BEFORE the response flush -- the operation is queued server-side regardless of whether the
        accepted-response bytes reach the client. The server calls this before the flush so a
        causally-later client read cannot out-race this writer thread's ledger update. It records
        no client-delivery outcome; that is observe_http_receipt's job, run only after the write.
        """

        status_code = int(status)
        with self._lock:
            for candidate in self._candidate_payloads(payload):
                identity = self._identity(candidate)
                if identity is None:
                    continue
                state = str(candidate.get("status") or "").strip().lower()
                if status_code == int(HTTPStatus.ACCEPTED) and state in QUEUED_DELIVERY_OPEN_STATES:
                    if identity in self._outstanding:
                        continue
                    key, epoch = identity
                    issued_at = self.clock()
                    self._outstanding[identity] = {
                        "key": key,
                        "epoch": epoch,
                        "issued_at": issued_at,
                    }
                    self._frames.append({
                        "stream": key,
                        "epoch": epoch,
                        "seq": 0,
                        "state": "open",
                    })
                    continue
                if identity not in self._outstanding:
                    continue
                terminal_state = "done" if state in QUEUED_DELIVERY_DONE_STATES else (
                    "error" if state in QUEUED_DELIVERY_ERROR_STATES else ""
                )
                if not terminal_state:
                    continue
                reason = str(candidate.get("reason") or candidate.get("error") or "").strip()
                self._terminalize_qualified_promise_locked(identity, terminal_state, reason=reason)

    def observe_http_receipt(self, payload: object, status: HTTPStatus | int) -> None:
        """Record that an ACCEPTED operation receipt actually reached the client.

        Invariant: receipt_exposed reflects the ACTUAL client write and must run only AFTER a
        successful flush -- it must never claim exposure on a failed write. The server calls this
        after the write returns, so a BrokenPipe/OSError on the flush leaves receipt_exposed unset
        and the operation honestly recorded as committed-but-undelivered.
        """

        status_code = int(status)
        if status_code != int(HTTPStatus.ACCEPTED):
            return
        with self._lock:
            for candidate in self._candidate_payloads(payload):
                operation = candidate.get("operation") if isinstance(candidate.get("operation"), dict) else {}
                operation_id = str(operation.get("id") or "")
                if not operation_id:
                    continue
                record = self._operations.get(operation_id)
                if isinstance(record, dict) and not bool(record.get("receipt_exposed")):
                    record["receipt_exposed"] = True
                    self._append_operation_locked(record)

    def observe_http_response(self, payload: object, status: HTTPStatus | int) -> None:
        """Record a fully delivered response: server-side commit then client receipt exposure.

        The single-call composition for callers that model a completed delivery (the bytes reached
        the client). The server does not call this; it splits the two halves around the flush so a
        failed write never claims receipt exposure. See observe_http_commit / observe_http_receipt.
        """

        self.observe_http_commit(payload, status)
        self.observe_http_receipt(payload, status)

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            outstanding = sorted(
                (dict(row) for row in self._outstanding.values()),
                key=lambda row: (float(row["issued_at"]), str(row["key"]), int(row["epoch"])),
            )
            oldest_age = max(0.0, self.clock() - float(outstanding[0]["issued_at"])) if outstanding else 0.0
            return {
                "outstanding_queued": outstanding,
                "outstanding_queued_count": len(outstanding),
                "oldest_queued_age_seconds": oldest_age,
                "queued_delivery_frames": [dict(frame) for frame in self._frames],
                "accepted_operations": [
                    {
                        "id": str(record.get("id") or ""),
                        "state": str(record.get("state") or ""),
                        "request_id": str(record.get("request_id") or ""),
                        "route": str(record.get("route") or ""),
                        "created_at": float(record.get("created_at") or 0.0),
                        "terminal_at": float(record.get("terminal_at") or 0.0),
                        "kind": str(record.get("kind") or ""),
                        # Subtype and the uncoalesced reason come from the accept-time context, so
                        # they are present for an operation that is still stuck -- which is the one
                        # whose holder somebody actually needs to name.
                        "subtype": str((record.get("context") or {}).get("operation") or ""),
                        "uncoalesced": str((record.get("context") or {}).get("uncoalesced") or ""),
                        "schedule": dict(record.get("schedule") or {}),
                    }
                    for record in sorted(
                        self._operations.values(),
                        key=lambda value: (float(value.get("created_at") or 0.0), str(value.get("id") or "")),
                    )
                ],
            }


class QueuedDeliveryCompactionOwner:
    """Submit and observe at most one out-of-band ledger compaction at a time."""

    def __init__(
        self,
        ledger: QueuedDeliveryLedger,
        submit: Callable[[Path, str], dict[str, Any]],
        result: Callable[[str], dict[str, Any]],
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ledger = ledger
        self._submit = submit
        self._result = result
        self._monotonic = monotonic
        self._condition = threading.Condition()
        self._stopped = False
        self._worker: threading.Thread | None = None
        self._ledger.set_operation_compaction_signal(self.request)
        if self._ledger.operation_compaction_request() is not None:
            self.request()

    def request(self) -> None:
        with self._condition:
            if self._stopped:
                return
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._run,
                    name="queued-delivery-compaction",
                    daemon=True,
                )
                self._worker.start()
            self._condition.notify_all()

    def _wait_until(self, deadline: float) -> bool:
        with self._condition:
            while not self._stopped:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return True
                self._condition.wait(remaining)
            return False

    def _retry_after_failure(self) -> bool:
        return self._wait_until(self._monotonic() + QUEUED_OPERATION_COMPACT_RETRY_SECONDS)

    def _run(self) -> None:
        try:
            while True:
                request = self._ledger.operation_compaction_request()
                if request is None:
                    return
                remaining = float(request["due_at"]) - self._monotonic()
                if remaining > 0:
                    with self._condition:
                        if self._stopped:
                            return
                        self._condition.wait(remaining)
                    continue
                path = Path(request["state_path"])
                digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:32]
                try:
                    response = self._submit(path, f"operation-ledger-compact:{digest}")
                except (OSError, RuntimeError, ValueError):
                    if not self._retry_after_failure():
                        return
                    continue
                job = response.get("job") if isinstance(response.get("job"), dict) else {}
                job_id = str(job.get("job_id") or "")
                state = str(job.get("status") or "")
                if response.get("ok") is not True or not job_id:
                    if not self._retry_after_failure():
                        return
                    continue
                poll_seconds = 0.05
                while state not in {"completed", "failed", "cancelled", "superseded", "timed_out"}:
                    if not self._wait_until(self._monotonic() + poll_seconds):
                        return
                    try:
                        completed = self._result(job_id)
                    except (OSError, RuntimeError, ValueError):
                        completed = {"ok": False}
                    completed_job = completed.get("job") if isinstance(completed.get("job"), dict) else {}
                    if completed.get("ok") is not True or not completed_job:
                        state = "failed"
                        break
                    state = str(completed_job.get("status") or "")
                    poll_seconds = min(0.5, poll_seconds * 2.0)
                if state == "completed":
                    self._ledger.note_operation_compaction_succeeded(request)
                    continue
                if not self._retry_after_failure():
                    return
        finally:
            with self._condition:
                if self._worker is threading.current_thread():
                    self._worker = None

    def stop(self) -> None:
        self._ledger.set_operation_compaction_signal(None)
        with self._condition:
            self._stopped = True
            worker = self._worker
            self._condition.notify_all()
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.0)


def compact_queued_delivery_journal(
    state_path: Path,
    *,
    clock: Callable[[], float] = time.time,
    operation_retention_seconds: float = QUEUED_OPERATION_RETENTION_SECONDS,
) -> dict[str, int]:
    """Compact the current durable journal without trusting a web process's stale memory."""

    path = Path(state_path)
    with file_lock(path, dir_mode=0o700):
        if not path.is_file():
            return {"before_bytes": 0, "after_bytes": 0, "operations": 0}
        raw = path.read_text(encoding="utf-8")
        ledger = QueuedDeliveryLedger(
            clock=clock,
            state_path=None,
            operation_retention_seconds=operation_retention_seconds,
        )
        ledger._load_raw_operations(raw)
        text = ledger._journal_text(ledger._snapshot_payload_locked())
        atomic_write_text(path, text, mode=0o600)
        return {
            "before_bytes": len(raw.encode("utf-8")),
            "after_bytes": len(text.encode("utf-8")),
            "operations": len(ledger._operations),
        }
