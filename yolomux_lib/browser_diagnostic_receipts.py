"""Exact validation for browser diagnostic durable-receipt barriers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


JAVASCRIPT_MAX_SAFE_INTEGER = 2**53 - 1
_BARRIER_FIELDS = frozenset({"epoch", "accepted", "pending", "retrying", "rejected", "dropped", "quiescent", "blocking"})
_BLOCKER_REQUIRED_FIELDS = frozenset({"key", "epoch", "eventId", "requestId", "source", "route", "event", "wallTime", "deliveryOutcome", "httpStatus", "status"})
_STORAGE_FAILURE_FIELDS = _BLOCKER_REQUIRED_FIELDS | frozenset({"globalBlocker", "storageFailure"})
_OVERFLOW_FIELDS = _BLOCKER_REQUIRED_FIELDS | frozenset({"globalBlocker", "journalOverflow", "omitted"})
_BLOCKING_STATUSES = frozenset({"pending", "retrying", "rejected", "dropped"})
_RECEIPT_STATUSES = _BLOCKING_STATUSES | frozenset({"accepted"})
_STORAGE_FAILURE_KEY = "__yolomux_receipt_storage_failure__"
_OVERFLOW_KEY = "__yolomux_receipt_journal_overflow__"
_TOKEN_CHARACTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-")


class BrowserReceiptBarrierValidationError(AssertionError):
    """A receipt barrier violated the exact product schema."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"browser receipt barrier is malformed: {code}")


def _safe_integer(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and 0 <= value <= JAVASCRIPT_MAX_SAFE_INTEGER


def _epoch(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 128 and all(character.isascii() and (character.isalnum() or character in "._*-") for character in value)


def _identifier(value: Any, maximum: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum and value[0].isascii() and value[0].isalnum() and all(character.isascii() and (character.isalnum() or character in "._-") for character in value)


def _bounded_text(value: Any, maximum: int, *, empty: bool = True, token: bool = False) -> bool:
    return isinstance(value, str) and len(value.encode("utf-16-le", errors="surrogatepass")) // 2 <= maximum and (empty or bool(value)) and not any(ord(character) < 32 or ord(character) == 127 for character in value) and (not token or all(character in _TOKEN_CHARACTERS for character in value))


def _normal_blocker(blocker: Mapping[str, Any]) -> bool:
    event_id = blocker.get("eventId")
    return (
        set(blocker) == _BLOCKER_REQUIRED_FIELDS
        and _identifier(blocker.get("epoch"), 128)
        and blocker.get("epoch") != "*"
        and _safe_integer(event_id)
        and blocker.get("key") == f"{blocker['epoch']}:{event_id}"
        and _bounded_text(blocker.get("requestId"), 128)
        and _bounded_text(blocker.get("source"), 240, empty=False)
        and _bounded_text(blocker.get("route"), 240, empty=False)
        and _bounded_text(blocker.get("event"), 64, empty=False, token=True)
        and _bounded_text(blocker.get("wallTime"), 64)
        and _bounded_text(blocker.get("deliveryOutcome"), 32, empty=False, token=True)
        and (blocker.get("httpStatus") is None or (_safe_integer(blocker.get("httpStatus")) and 100 <= blocker["httpStatus"] <= 599))
        and blocker.get("status") in _BLOCKING_STATUSES
    )


def _normal_receipt(receipt: Mapping[str, Any]) -> bool:
    return _normal_blocker(receipt) or (
        set(receipt) == _BLOCKER_REQUIRED_FIELDS
        and _identifier(receipt.get("epoch"), 128)
        and receipt.get("epoch") != "*"
        and _safe_integer(receipt.get("eventId"))
        and receipt.get("key") == f"{receipt['epoch']}:{receipt['eventId']}"
        and _bounded_text(receipt.get("requestId"), 128)
        and _bounded_text(receipt.get("source"), 240, empty=False)
        and _bounded_text(receipt.get("route"), 240, empty=False)
        and _bounded_text(receipt.get("event"), 64, empty=False, token=True)
        and _bounded_text(receipt.get("wallTime"), 64)
        and _bounded_text(receipt.get("deliveryOutcome"), 32, empty=False, token=True)
        and (receipt.get("httpStatus") is None or (_safe_integer(receipt.get("httpStatus")) and 100 <= receipt["httpStatus"] <= 599))
        and receipt.get("status") == "accepted"
    )


def _storage_failure_blocker(blocker: Mapping[str, Any]) -> bool:
    return (
        set(blocker) == _STORAGE_FAILURE_FIELDS
        and blocker.get("key") == _STORAGE_FAILURE_KEY
        and blocker.get("epoch") == "*"
        and blocker.get("eventId") is None
        and blocker.get("requestId") == ""
        and blocker.get("source") == "/"
        and blocker.get("route") == "/"
        and blocker.get("event") == "receipt_storage_failure"
        and blocker.get("wallTime") == ""
        and blocker.get("deliveryOutcome") == "failed"
        and blocker.get("httpStatus") is None
        and blocker.get("status") == "dropped"
        and blocker.get("globalBlocker") is True
        and _bounded_text(blocker.get("storageFailure"), 64, empty=False, token=True)
    )


def _overflow_blocker(blocker: Mapping[str, Any]) -> bool:
    return (
        set(blocker) == _OVERFLOW_FIELDS
        and blocker.get("key") == _OVERFLOW_KEY
        and blocker.get("epoch") == "*"
        and blocker.get("eventId") is None
        and blocker.get("requestId") == ""
        and blocker.get("source") == "/"
        and blocker.get("route") == "/"
        and blocker.get("event") == "receipt_journal_overflow"
        and blocker.get("wallTime") == ""
        and blocker.get("deliveryOutcome") == "dropped"
        and blocker.get("httpStatus") is None
        and blocker.get("status") == "dropped"
        and blocker.get("globalBlocker") is True
        and blocker.get("journalOverflow") is True
        and _safe_integer(blocker.get("omitted"))
        and blocker["omitted"] >= 1
    )


def _blocker_valid(blocker: Any) -> bool:
    if not isinstance(blocker, Mapping):
        return False
    if blocker.get("key") == _STORAGE_FAILURE_KEY:
        return _storage_failure_blocker(blocker)
    if blocker.get("key") == _OVERFLOW_KEY:
        return _overflow_blocker(blocker)
    return _normal_blocker(blocker)


def validate_browser_receipt_barrier(value: Any) -> dict[str, Any]:
    """Require the exact browser-owned durable-receipt barrier contract."""

    if not isinstance(value, Mapping) or set(value) != _BARRIER_FIELDS:
        raise BrowserReceiptBarrierValidationError("top_level_fields")
    if not _epoch(value.get("epoch")) or value.get("epoch") == "*":
        raise BrowserReceiptBarrierValidationError("epoch")
    counts = {status: value.get(status) for status in ("accepted", "pending", "retrying", "rejected", "dropped")}
    if any(not _safe_integer(count) for count in counts.values()):
        raise BrowserReceiptBarrierValidationError("counts")
    if sum(counts.values()) > JAVASCRIPT_MAX_SAFE_INTEGER:
        raise BrowserReceiptBarrierValidationError("count_total")
    if not isinstance(value.get("quiescent"), bool) or not isinstance(value.get("blocking"), list):
        raise BrowserReceiptBarrierValidationError("quiescence_or_blockers")
    blocker_status_counts = {status: 0 for status in _BLOCKING_STATUSES}
    blocker_keys: set[str] = set()
    for blocker in value["blocking"]:
        if not _blocker_valid(blocker):
            raise BrowserReceiptBarrierValidationError("blocker_shape")
        status = blocker.get("status")
        if status not in _BLOCKING_STATUSES:
            raise BrowserReceiptBarrierValidationError("blocker_status")
        key = blocker["key"]
        if key in blocker_keys:
            raise BrowserReceiptBarrierValidationError("duplicate_blocker_key")
        blocker_keys.add(key)
        if blocker["epoch"] != "*" and value["epoch"] != "all" and blocker["epoch"] != value["epoch"]:
            raise BrowserReceiptBarrierValidationError("blocker_epoch")
        blocker_status_counts[status] += 1
    overflow = next((blocker for blocker in value["blocking"] if blocker["key"] == _OVERFLOW_KEY), None)
    if overflow is not None and len(value["blocking"]) - 1 + overflow["omitted"] > JAVASCRIPT_MAX_SAFE_INTEGER:
        raise BrowserReceiptBarrierValidationError("overflow_total")
    if any(counts[status] != blocker_status_counts[status] for status in blocker_status_counts):
        raise BrowserReceiptBarrierValidationError("blocker_counts")
    blocking_count = sum(blocker_status_counts.values())
    if value["quiescent"] != (blocking_count == 0):
        raise BrowserReceiptBarrierValidationError("quiescence")
    return {
        "epoch": value["epoch"],
        "accepted": value["accepted"],
        "pending": value["pending"],
        "retrying": value["retrying"],
        "rejected": value["rejected"],
        "dropped": value["dropped"],
        "quiescent": value["quiescent"],
        "blocking": [dict(blocker) for blocker in value["blocking"]],
    }


def validate_browser_receipt_projection(value: Any) -> dict[str, Any]:
    """Validate the bounded persisted receipt journal and its exact barrier projection."""

    if not isinstance(value, Mapping) or set(value) != {"receipts", "barrier"} or not isinstance(value.get("receipts"), list) or len(value["receipts"]) > 500:
        raise BrowserReceiptBarrierValidationError("projection_shape")
    barrier = validate_browser_receipt_barrier(value["barrier"])
    receipts: list[dict[str, Any]] = []
    keys: set[str] = set()
    for receipt in value["receipts"]:
        valid = isinstance(receipt, Mapping) and (
            _normal_receipt(receipt)
            or (receipt.get("key") == _STORAGE_FAILURE_KEY and _storage_failure_blocker(receipt))
            or (receipt.get("key") == _OVERFLOW_KEY and _overflow_blocker(receipt))
        )
        if not valid:
            raise BrowserReceiptBarrierValidationError("projection_receipt_shape")
        key = receipt["key"]
        if key in keys:
            raise BrowserReceiptBarrierValidationError("projection_duplicate_key")
        keys.add(key)
        receipts.append(dict(receipt))
    selected = [receipt for receipt in receipts if barrier["epoch"] == "all" or receipt["epoch"] == barrier["epoch"] or receipt.get("globalBlocker") is True]
    counts = {status: sum(receipt["status"] == status for receipt in selected) for status in _RECEIPT_STATUSES}
    if any(counts[status] != barrier[status] for status in _RECEIPT_STATUSES):
        raise BrowserReceiptBarrierValidationError("projection_counts")
    blocking = [receipt for receipt in selected if receipt["status"] != "accepted"]
    if blocking != barrier["blocking"]:
        raise BrowserReceiptBarrierValidationError("projection_blocking")
    return {"receipts": receipts, "barrier": barrier}
