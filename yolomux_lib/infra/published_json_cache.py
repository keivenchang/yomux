# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared record-then-manifest publication for durable JSON caches."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Generic
from typing import Protocol
from typing import TypeVar

from .atomic_file import atomic_write_text
from ..filesystem.io_ops import read_json_file


Payload = TypeVar("Payload")
PayloadCo = TypeVar("PayloadCo", covariant=True)
PayloadContra = TypeVar("PayloadContra", contravariant=True)
FreshnessKey = TypeVar("FreshnessKey")
FreshnessKeyContra = TypeVar("FreshnessKeyContra", contravariant=True)


class PublishedJsonCodec(Protocol):
    def read(self, path: Path) -> dict[str, Any] | None: ...

    def encode(self, value: dict[str, Any]) -> str: ...


class PublishedJsonValidator(Protocol[PayloadCo]):
    def payload(self, record: dict[str, Any], signature: str) -> PayloadCo | None: ...


@dataclass(frozen=True)
class PublishedJsonFreshness:
    current: bool
    fresh: bool
    age_seconds: float
    stored_at: float


class PublishedJsonFreshnessStrategy(Protocol[PayloadContra, FreshnessKeyContra]):
    def evaluate(
        self,
        record: dict[str, Any],
        manifest: dict[str, Any] | None,
        payload: PayloadContra,
        freshness_key: FreshnessKeyContra,
        *,
        now: float,
        max_age_seconds: float | None,
    ) -> PublishedJsonFreshness | None: ...


class PublishedJsonRecordManifestStrategy(Protocol[PayloadContra, FreshnessKeyContra]):
    def payload_signature(self, payload: PayloadContra) -> str: ...

    def payload_changed(
        self,
        existing: dict[str, Any] | None,
        signature: str,
        payload_signature: str,
        freshness_key: FreshnessKeyContra,
    ) -> bool: ...

    def record(
        self,
        signature: str,
        payload: PayloadContra,
        payload_signature: str,
        freshness_key: FreshnessKeyContra,
        stored_at: float,
    ) -> dict[str, Any]: ...

    def manifest(
        self,
        signature: str,
        payload_signature: str,
        freshness_key: FreshnessKeyContra,
        stored_at: float,
        payload_changed: bool,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PublishedJsonWrite:
    record_path: Path
    manifest_path: Path
    record_written: bool
    record_bytes: int
    manifest_bytes: int
    stored_at: float


class PublishedJsonPostWriteStrategy(Protocol[FreshnessKeyContra]):
    def after_write(self, result: PublishedJsonWrite, signature: str, freshness_key: FreshnessKeyContra) -> None: ...


@dataclass(frozen=True)
class PublishedJsonRead(Generic[Payload]):
    payload: Payload
    freshness: PublishedJsonFreshness


class DeterministicJsonCodec:
    """The existing compact, sorted JSON bytes and fail-closed disk reader."""

    def read(self, path: Path) -> dict[str, Any] | None:
        value = read_json_file(
            path,
            None,
            exceptions=(FileNotFoundError, json.JSONDecodeError, OSError, TypeError),
        )
        return value if isinstance(value, dict) else None

    def encode(self, value: dict[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))


class PublishedJsonCache(Generic[Payload, FreshnessKey]):
    """Publish one logical payload record followed by its refresh manifest."""

    def __init__(
        self,
        *,
        codec: PublishedJsonCodec,
        validator: PublishedJsonValidator[Payload],
        freshness: PublishedJsonFreshnessStrategy[Payload, FreshnessKey],
        record_manifest: PublishedJsonRecordManifestStrategy[Payload, FreshnessKey],
        post_write: PublishedJsonPostWriteStrategy[FreshnessKey],
        clock: Callable[[], float],
        writer: Callable[..., None] = atomic_write_text,
    ) -> None:
        self.codec = codec
        self.validator = validator
        self.freshness = freshness
        self.record_manifest = record_manifest
        self.post_write = post_write
        self.clock = clock
        self.writer = writer

    @staticmethod
    def manifest_path(record_path: Path, signature: str) -> Path:
        return record_path.parent / f"{signature}.manifest.json"

    def read(
        self,
        record_path: Path,
        signature: str,
        freshness_key: FreshnessKey,
        *,
        max_age_seconds: float | None,
        allow_stale: bool,
    ) -> PublishedJsonRead[Payload] | None:
        record = self.codec.read(record_path)
        if record is None:
            return None
        payload = self.validator.payload(record, signature)
        if payload is None:
            return None
        manifest = self.codec.read(self.manifest_path(record_path, signature))
        state = self.freshness.evaluate(
            record,
            manifest,
            payload,
            freshness_key,
            now=self.clock(),
            max_age_seconds=max_age_seconds,
        )
        if state is None or (not state.fresh and not allow_stale):
            return None
        return PublishedJsonRead(payload, state)

    def write(
        self,
        record_path: Path,
        signature: str,
        payload: Payload,
        freshness_key: FreshnessKey,
    ) -> PublishedJsonWrite:
        payload_signature = self.record_manifest.payload_signature(payload)
        existing = self.codec.read(record_path)
        payload_changed = self.record_manifest.payload_changed(
            existing, signature, payload_signature, freshness_key,
        )
        stored_at = self.clock()
        record = self.record_manifest.record(
            signature, payload, payload_signature, freshness_key, stored_at,
        )
        manifest = self.record_manifest.manifest(
            signature, payload_signature, freshness_key, stored_at, payload_changed,
        )
        record_text = self.codec.encode(record)
        manifest_text = self.codec.encode(manifest)
        manifest_path = self.manifest_path(record_path, signature)
        if payload_changed:
            self.writer(record_path, record_text, mode=0o600)
        self.writer(manifest_path, manifest_text, mode=0o600)
        result = PublishedJsonWrite(
            record_path,
            manifest_path,
            payload_changed,
            len(record_text.encode("utf-8")) if payload_changed else 0,
            len(manifest_text.encode("utf-8")),
            stored_at,
        )
        self.post_write.after_write(result, signature, freshness_key)
        return result
