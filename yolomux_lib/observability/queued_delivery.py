# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded acceptance ledger for asynchronous HTTP delivery promises."""

from __future__ import annotations

import collections
import copy
from datetime import datetime
from datetime import timezone
import json
import logging
from pathlib import Path
import threading
import time
import uuid
from http import HTTPStatus
from typing import Any
from typing import Callable

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
        self._load_operations()

    @staticmethod
    def _deadline_text(deadline_at: float) -> str:
        return datetime.fromtimestamp(float(deadline_at), timezone.utc).isoformat().replace("+00:00", "Z")

    def _load_operations(self) -> None:
        if self._state_path is None or not self._state_path.is_file():
            return
        try:
            raw = self._state_path.read_text(encoding="utf-8")
            try:
                state = json.loads(raw)
            except json.JSONDecodeError:
                state = None
            if isinstance(state, dict) and int(state.get("version") or 0) == QUEUED_OPERATION_STATE_VERSION:
                self._load_snapshot_operations(state)
            else:
                self._load_journal_operations(raw)
            self._prune_operations_locked()
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            logger.exception("failed to load queued-operation state %s", self._state_path)
            self._operations.clear()

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
                self._operations[operation_id] = copy.deepcopy(value)

    def _load_journal_operations(self, raw: str) -> None:
        lines = raw.splitlines()
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                if index == len(lines) - 1:
                    return
                raise ValueError("malformed queued-operation journal record") from None
            if not isinstance(entry, dict) or int(entry.get("version") or 0) != QUEUED_OPERATION_JOURNAL_VERSION:
                raise ValueError("unsupported queued-operation journal record")
            kind = str(entry.get("type") or "")
            if kind == "snapshot":
                records = entry.get("operations")
                if not isinstance(records, list):
                    raise ValueError("queued-operation journal snapshot must contain records")
                self._operations.clear()
                self._load_operation_records(records)
            elif kind == "operation":
                record = entry.get("record")
                if not isinstance(record, dict):
                    raise ValueError("queued-operation journal record must contain an operation")
                self._load_operation_records([record])
            else:
                raise ValueError("unsupported queued-operation journal record type")
            epoch = str(entry.get("epoch") or "")
            if epoch:
                self._operation_epoch = epoch

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
        total_bytes = sum(self._terminal_event_bytes(record["terminal_event"]) for record in exposed)
        for record in exposed:
            event_bytes = self._terminal_event_bytes(record["terminal_event"])
            if event_bytes <= QUEUED_OPERATION_REPLAY_RECORD_BYTES and total_bytes <= QUEUED_OPERATION_REPLAY_TOTAL_BYTES:
                continue
            bounded = self._bounded_terminal_event(record)
            bounded_bytes = self._terminal_event_bytes(bounded)
            if record["terminal_event"] == bounded:
                continue
            record["terminal_event"] = bounded
            record["http_status"] = int(HTTPStatus.GONE)
            total_bytes += bounded_bytes - event_bytes
            changed = True
        return changed

    def _write_snapshot_locked(self) -> None:
        if self._state_path is None:
            return
        payload = {
            "version": QUEUED_OPERATION_JOURNAL_VERSION,
            "type": "snapshot",
            "epoch": self._operation_epoch,
            "operations": sorted(
                (copy.deepcopy(record) for record in self._operations.values()),
                key=lambda record: (float(record.get("created_at") or 0.0), str(record.get("id") or "")),
            ),
        }
        with file_lock(self._state_path, dir_mode=0o700):
            atomic_write_text(
                self._state_path,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                mode=0o600,
            )

    def _append_operation_locked(self, record: dict[str, Any]) -> None:
        if self._state_path is None:
            return
        payload = {
            "version": QUEUED_OPERATION_JOURNAL_VERSION,
            "type": "operation",
            "epoch": self._operation_epoch,
            "record": record,
        }
        append_fsync_text(
            self._state_path,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            mode=0o600,
        )

    def compact_operations(self) -> None:
        """Replace the journal with one durable snapshot; call only from an out-of-band owner."""
        if self._state_path is None:
            return
        with self._lock:
            self._prune_operations_locked()
            self._write_snapshot_locked()

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
                "http_status": int(status),
            })
            self._prune_operations_locked()
            self._append_operation_locked(record)
            return copy.deepcopy(event)

    def acknowledge_operation_deliveries(self, acknowledgments: list[dict[str, Any]]) -> list[str]:
        """Retire exact replay bytes only after the browser processed each terminal."""

        acknowledged: list[str] = []
        changed = False
        with self._lock:
            for acknowledgment in acknowledgments:
                operation_id = str(acknowledgment.get("id") or "")
                cursor = acknowledgment.get("cursor")
                record = self._operations.get(operation_id)
                event = record.get("terminal_event") if isinstance(record, dict) else None
                event_cursor = event.get("operation", {}).get("cursor") if isinstance(event, dict) else None
                if not isinstance(cursor, dict) or not isinstance(event_cursor, dict) or event_cursor != cursor:
                    continue
                acknowledged.append(operation_id)
                if bool(record.get("delivery_acknowledged")):
                    continue
                record["delivery_acknowledged"] = True
                changed = True
            if changed:
                self._bound_terminal_replays_locked()
                self._write_snapshot_locked()
        return acknowledged

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

    def observe_http_response(self, payload: object, status: HTTPStatus | int) -> None:
        status_code = int(status)
        with self._lock:
            for candidate in self._candidate_payloads(payload):
                operation = candidate.get("operation") if isinstance(candidate.get("operation"), dict) else {}
                operation_id = str(operation.get("id") or "")
                if status_code == int(HTTPStatus.ACCEPTED) and operation_id:
                    record = self._operations.get(operation_id)
                    if isinstance(record, dict) and not bool(record.get("receipt_exposed")):
                        record["receipt_exposed"] = True
                        self._append_operation_locked(record)
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
                    }
                    for record in sorted(
                        self._operations.values(),
                        key=lambda value: (float(value.get("created_at") or 0.0), str(value.get("id") or "")),
                    )
                ],
            }
