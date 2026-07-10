# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Server-authoritative API contract for the global YO!chat room."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import defaultdict
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Any
from typing import Callable

from .chat_questions import chat_message_is_question
from .chat_store import CHAT_CONTEXT_LIMIT_MAX
from .chat_store import CHAT_DEFAULT_RETENTION_DAYS
from .chat_store import CHAT_MESSAGE_BODY_MAX_BYTES
from .chat_store import CHAT_PAGE_LIMIT
from .chat_store import CHAT_PAGE_LIMIT_MAX
from .chat_store import CHAT_SEARCH_LIMIT_MAX
from .chat_store import ChatMessage
from .chat_store import ChatMessageContext
from .chat_store import ChatStore
from .chat_store import ChatStoreValidationError


CHAT_ID_MAX_LENGTH = 64
CHAT_SEND_RATE_LIMIT = 20
CHAT_QUERY_RATE_LIMIT = 120
CHAT_RATE_WINDOW_SECONDS = 60.0
CHAT_YO_COMMAND_RE = re.compile(r"^/yo\s+(.+)$", re.DOTALL)
CHAT_YOAGENT_USERNAME = "YO!agent"
CHAT_YOAGENT_INSTANCE_ID = "yolomux-yoagent"


class ChatServiceError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def normalize_chat_client_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    safe = "".join(character for character in text if character.isalnum() or character in "._:-")[:CHAT_ID_MAX_LENGTH]
    if not safe or safe != text or len(text) > CHAT_ID_MAX_LENGTH:
        raise ChatServiceError(f"invalid {field}", code="invalid_id")
    return safe


def chat_message_payload(message: ChatMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "created_at_utc": message.created_at_utc,
        "username": message.username,
        "sender_ip": message.sender_ip,
        "sender_instance_id": message.sender_instance_id,
        "client_message_uuid": message.client_message_uuid,
        "body": message.body,
        "is_question": message.is_question,
    }


def chat_context_payload(context: ChatMessageContext) -> dict[str, Any]:
    return {
        "target": chat_message_payload(context.target),
        "before": [chat_message_payload(message) for message in context.before],
        "after": [chat_message_payload(message) for message in context.after],
    }


class ChatRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.records: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, username: str, action: str, limit: int) -> None:
        now = self.clock()
        key = (username, action)
        with self.lock:
            record = self.records[key]
            while record and record[0] <= now - CHAT_RATE_WINDOW_SECONDS:
                record.popleft()
            if len(record) >= limit:
                raise ChatServiceError("chat rate limit exceeded", code="rate_limited", status=429)
            record.append(now)


class ChatCursorCodec:
    def __init__(self, secret_path: Path):
        self.secret_path = Path(secret_path)
        self._secret: bytes | None = None
        self._lock = threading.Lock()

    def _load_secret(self) -> bytes:
        if self._secret is not None:
            return self._secret
        with self._lock:
            if self._secret is not None:
                return self._secret
            self.secret_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                secret = self.secret_path.read_bytes()
            except FileNotFoundError:
                secret = secrets.token_bytes(32)
                try:
                    descriptor = os.open(self.secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    secret = self.secret_path.read_bytes()
                else:
                    with os.fdopen(descriptor, "wb") as output:
                        output.write(secret)
            if len(secret) < 32:
                raise ChatServiceError("invalid chat cursor key", code="server_error", status=500)
            self._secret = secret
            return secret

    def encode(self, kind: str, message_id: int) -> str:
        payload = json.dumps({"v": 1, "kind": kind, "id": int(message_id)}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self._load_secret(), payload, hashlib.sha256).digest()[:16]
        return base64.urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")

    def decode(self, token: Any, kind: str) -> int:
        text = str(token or "")
        try:
            raw = base64.urlsafe_b64decode(text + ("=" * (-len(text) % 4)))
            payload, signature = raw[:-16], raw[-16:]
            expected = hmac.new(self._load_secret(), payload, hashlib.sha256).digest()[:16]
            decoded = json.loads(payload.decode("utf-8"))
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ChatServiceError("invalid chat cursor", code="invalid_cursor") from error
        if not hmac.compare_digest(signature, expected) or decoded.get("v") != 1 or decoded.get("kind") != kind:
            raise ChatServiceError("invalid chat cursor", code="invalid_cursor")
        try:
            return max(0, int(decoded["id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ChatServiceError("invalid chat cursor", code="invalid_cursor") from error


class ChatService:
    def __init__(
        self,
        store: ChatStore,
        *,
        cursor_secret_path: Path,
        retention_days: Callable[[], int] = lambda: CHAT_DEFAULT_RETENTION_DAYS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.store = store
        self.retention_days = retention_days
        self.cursor_codec = ChatCursorCodec(cursor_secret_path)
        self.rate_limiter = ChatRateLimiter(clock)
        self.metrics_lock = threading.Lock()
        self.metrics: dict[str, dict[str, float | int]] = {}

    def _record_metric(self, operation: str, started_at: float, payload: dict[str, Any], rows: int) -> None:
        elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000)
        payload_bytes = len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        with self.metrics_lock:
            record = self.metrics.setdefault(operation, {
                "count": 0,
                "rows": 0,
                "bytes": 0,
                "latency_ms_total": 0.0,
                "latency_ms_max": 0.0,
            })
            record["count"] = int(record["count"]) + 1
            record["rows"] = int(record["rows"]) + max(0, int(rows))
            record["bytes"] = int(record["bytes"]) + payload_bytes
            record["latency_ms_total"] = float(record["latency_ms_total"]) + elapsed_ms
            record["latency_ms_max"] = max(float(record["latency_ms_max"]), elapsed_ms)

    def diagnostics(self) -> dict[str, Any]:
        with self.metrics_lock:
            operations = {
                name: {
                    "count": int(record["count"]),
                    "rows": int(record["rows"]),
                    "bytes": int(record["bytes"]),
                    "latency_ms_avg": round(float(record["latency_ms_total"]) / max(1, int(record["count"])), 3),
                    "latency_ms_max": round(float(record["latency_ms_max"]), 3),
                }
                for name, record in sorted(self.metrics.items())
            }
        return {"store": self.store.diagnostics(), "operations": operations}

    def _prune(self) -> int:
        retention_days = self.retention_days()
        self.store.prune_if_due(retention_days=retention_days)
        return retention_days

    def bootstrap(self, *, username: str, browser_instance_id: Any) -> dict[str, Any]:
        instance = normalize_chat_client_id(browser_instance_id, "browser instance ID")
        retention_days = self._prune()
        result = self.store.bootstrap(username=username, retention_days=retention_days)
        typing = [asdict(lease) for lease in result.typing if lease.browser_instance_id != instance]
        return {
            "revision": result.latest_message_id,
            "newer_cursor": self.cursor_codec.encode("newer", result.latest_message_id),
            "older_cursor": self.cursor_codec.encode(
                "older",
                result.unread_messages[0].id if result.unread_messages else result.latest_message_id + 1,
            ) if result.has_more_older else None,
            "has_more_older": result.has_more_older,
            "read_up_to_id": result.read_cursor.read_up_to_id,
            "first_registration": result.first_registration,
            "messages": [chat_message_payload(message) for message in result.unread_messages],
            "typing": typing,
            "retention_days": retention_days,
        }

    def page(self, *, username: str, before: Any = "", limit: Any = CHAT_PAGE_LIMIT) -> dict[str, Any]:
        started_at = time.perf_counter()
        self.rate_limiter.check(username, "query", CHAT_QUERY_RATE_LIMIT)
        retention_days = self._prune()
        before_id = self.cursor_codec.decode(before, "older") if before else None
        result = self.store.page_before(before_id=before_id, limit=limit, retention_days=retention_days)
        payload = {
            "messages": [chat_message_payload(message) for message in result.messages],
            "older_cursor": self.cursor_codec.encode("older", result.older_cursor) if result.older_cursor is not None else None,
            "has_more": result.has_more,
        }
        self._record_metric("page", started_at, payload, len(result.messages))
        return payload

    def delta(self, *, username: str, after: Any, limit: Any = CHAT_PAGE_LIMIT_MAX) -> dict[str, Any]:
        self.rate_limiter.check(username, "query", CHAT_QUERY_RATE_LIMIT)
        retention_days = self._prune()
        after_id = self.cursor_codec.decode(after, "newer") if after else 0
        messages = self.store.messages_after(after_id=after_id, limit=limit, retention_days=retention_days)
        revision = messages[-1].id if messages else after_id
        return {
            "messages": [chat_message_payload(message) for message in messages],
            "revision": revision,
            "newer_cursor": self.cursor_codec.encode("newer", revision),
        }

    def context(self, *, username: str, message_id: Any, before: Any = 3, after: Any = 3) -> dict[str, Any]:
        self.rate_limiter.check(username, "query", CHAT_QUERY_RATE_LIMIT)
        context = self.store.context(
            message_id=message_id,
            before=min(CHAT_CONTEXT_LIMIT_MAX, max(0, int(before))),
            after=min(CHAT_CONTEXT_LIMIT_MAX, max(0, int(after))),
            retention_days=self._prune(),
        )
        if context is None:
            raise ChatServiceError("chat message is no longer available", code="expired", status=404)
        return chat_context_payload(context)

    def search(self, *, username: str, query: Any, cursor: Any = "", limit: Any = 20) -> dict[str, Any]:
        started_at = time.perf_counter()
        self.rate_limiter.check(username, "search", CHAT_QUERY_RATE_LIMIT)
        cursor_id = self.cursor_codec.decode(cursor, "search") if cursor else None
        result = self.store.search(
            query=str(query or ""),
            cursor=cursor_id,
            limit=min(CHAT_SEARCH_LIMIT_MAX, max(1, int(limit))),
            retention_days=self._prune(),
        )
        payload = {
            "hits": [chat_context_payload(hit.context) for hit in result.hits],
            "next_cursor": self.cursor_codec.encode("search", result.next_cursor) if result.next_cursor is not None else None,
            "has_more": result.has_more,
        }
        self._record_metric("search", started_at, payload, len(result.hits))
        return payload

    def yoagent_source(self, *, username: str, browser_instance_id: Any, message_id: Any) -> tuple[ChatMessage, str]:
        instance = normalize_chat_client_id(browser_instance_id, "browser instance ID")
        try:
            source_id = int(message_id)
        except (TypeError, ValueError) as error:
            raise ChatServiceError("invalid chat message ID", code="invalid_message") from error
        context = self.store.context(message_id=source_id, before=0, after=0, retention_days=self._prune())
        if context is None:
            raise ChatServiceError("chat message is no longer available", code="expired", status=404)
        source = context.target
        if source.username != username or source.sender_instance_id != instance:
            raise ChatServiceError("chat message does not belong to this sender", code="forbidden", status=403)
        match = CHAT_YO_COMMAND_RE.fullmatch(source.body.strip())
        if match is None or not match.group(1).strip():
            raise ChatServiceError("chat message is not a /yo command", code="invalid_message")
        return source, match.group(1).strip()

    def record_yoagent_reply(self, *, source: ChatMessage, answer: str) -> tuple[dict[str, Any], bool]:
        encoded = str(answer or "").encode("utf-8")
        bounded_answer = encoded[:CHAT_MESSAGE_BODY_MAX_BYTES].decode("utf-8", errors="ignore")
        if not bounded_answer:
            raise ChatServiceError("YO!agent returned an empty answer", code="server_error", status=500)
        try:
            message, created = self.store.insert_message(
                username=CHAT_YOAGENT_USERNAME,
                sender_instance_id=CHAT_YOAGENT_INSTANCE_ID,
                client_message_uuid=f"yo-reply-{source.id}",
                body=bounded_answer,
                is_question=False,
            )
        except ChatStoreValidationError as error:
            raise ChatServiceError(str(error), code="server_error", status=500) from error
        return {"message": chat_message_payload(message), "revision": message.id, "created": created}, created

    def send(self, *, username: str, payload: dict[str, Any], locale: str, sender_ip: str = "") -> tuple[dict[str, Any], bool]:
        started_at = time.perf_counter()
        self.rate_limiter.check(username, "send", CHAT_SEND_RATE_LIMIT)
        instance = normalize_chat_client_id(payload.get("browser_instance_id"), "browser instance ID")
        message_uuid = normalize_chat_client_id(payload.get("client_message_uuid"), "client message UUID")
        body = str(payload.get("body") or "")
        try:
            message, created = self.store.insert_message(
                username=username,
                sender_ip=sender_ip,
                sender_instance_id=instance,
                client_message_uuid=message_uuid,
                body=body,
                is_question=chat_message_is_question(body, locale),
            )
        except ChatStoreValidationError as error:
            raise ChatServiceError(str(error), code="invalid_message") from error
        response = {"message": chat_message_payload(message), "revision": message.id, "created": created}
        self._record_metric("send", started_at, response, 1)
        return response, created

    def typing(self, *, username: str, browser_instance_id: Any, typing: Any) -> dict[str, Any]:
        instance = normalize_chat_client_id(browser_instance_id, "browser instance ID")
        leases = self.store.set_typing(username=username, browser_instance_id=instance, typing=bool(typing))
        return {"typing": [asdict(lease) for lease in leases if lease.browser_instance_id != instance]}

    def read(self, *, username: str, message_id: Any) -> dict[str, Any]:
        cursor = self.store.read_up_to(username=username, message_id=message_id)
        return {"read_up_to_id": cursor.read_up_to_id}
