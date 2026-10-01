# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Session-files and Tabber strategies for the shared published JSON cache."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any
from typing import Callable

from ..infra.published_json_cache import DeterministicJsonCodec
from ..infra.published_json_cache import PublishedJsonCache
from ..infra.published_json_cache import PublishedJsonFreshness
from ..infra.published_json_cache import PublishedJsonRead
from ..infra.published_json_cache import PublishedJsonWrite
from . import session_files


PayloadSignature = Callable[[dict[str, Any]], str]
OwnerGeneration = Callable[[], dict[str, Any]]


@dataclass
class SessionFilesCachedPayload:
    payload: dict[str, Any]
    status: HTTPStatus
    request_descriptor: str = ""


@dataclass(frozen=True)
class SessionFilesFreshnessKey:
    source_generation: str
    status: HTTPStatus = HTTPStatus.OK


class SessionFilesValidator:
    def __init__(self, version: int) -> None:
        self.version = version

    def payload(self, record: dict[str, Any], signature: str) -> SessionFilesCachedPayload | None:
        if record.get("version") != self.version or record.get("signature") != signature:
            return None
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return None
        try:
            status = HTTPStatus(int(record.get("status", int(HTTPStatus.OK))))
            float(record.get("stored_at", 0.0))
        except (TypeError, ValueError):
            return None
        return SessionFilesCachedPayload(payload, status, str(record.get("request_descriptor") or ""))


class SessionFilesFreshness:
    def __init__(self, version: int, payload_signature: PayloadSignature) -> None:
        self.version = version
        self.payload_signature = payload_signature

    def evaluate(
        self,
        record: dict[str, Any],
        manifest: dict[str, Any] | None,
        payload: SessionFilesCachedPayload,
        freshness_key: SessionFilesFreshnessKey,
        *,
        now: float,
        max_age_seconds: float | None,
    ) -> PublishedJsonFreshness | None:
        source_matches = str(record.get("source_generation") or "") == freshness_key.source_generation
        try:
            stored_at = float(record.get("stored_at", 0.0))
        except (TypeError, ValueError):
            return None
        if source_matches:
            signature = str(record.get("signature") or "")
            payload_signature = str(record.get("payload_signature") or self.payload_signature(payload.payload))
            if (
                isinstance(manifest, dict)
                and manifest.get("version") == self.version
                and manifest.get("signature") == signature
                and manifest.get("payload_signature") == payload_signature
            ):
                try:
                    payload.status = HTTPStatus(int(manifest.get("status", int(payload.status))))
                    stored_at = float(manifest.get("stored_at", stored_at))
                except (TypeError, ValueError):
                    pass
        age_seconds = max(0.0, now - stored_at)
        fresh = source_matches and (max_age_seconds is None or age_seconds <= max_age_seconds)
        return PublishedJsonFreshness(source_matches, fresh, age_seconds, stored_at)


class RecordManifestOwner:
    def __init__(self, version: int, payload_signature: PayloadSignature, owner_generation: OwnerGeneration) -> None:
        self.version = version
        self._payload_signature = payload_signature
        self.owner_generation = owner_generation


class SessionFilesRecordManifest(RecordManifestOwner):

    def payload_signature(self, payload: SessionFilesCachedPayload) -> str:
        return self._payload_signature({"payload": payload.payload, "request_descriptor": payload.request_descriptor})

    def payload_changed(
        self,
        existing: dict[str, Any] | None,
        signature: str,
        payload_signature: str,
        freshness_key: SessionFilesFreshnessKey,
    ) -> bool:
        if not isinstance(existing, dict) or existing.get("version") != self.version or existing.get("signature") != signature:
            return True
        existing_payload = existing.get("payload")
        existing_signature = str(existing.get("payload_signature") or "")
        if not existing_signature and isinstance(existing_payload, dict):
            existing_signature = self._payload_signature(existing_payload)
        try:
            existing_status = int(existing.get("status", int(freshness_key.status)))
        except (TypeError, ValueError):
            existing_status = -1
        return (
            existing_signature != payload_signature
            or existing_status != int(freshness_key.status)
            or str(existing.get("source_generation") or "") != freshness_key.source_generation
        )

    def record(
        self,
        signature: str,
        payload: SessionFilesCachedPayload,
        payload_signature: str,
        freshness_key: SessionFilesFreshnessKey,
        stored_at: float,
    ) -> dict[str, Any]:
        return {
            "version": self.version,
            "signature": signature,
            "source_generation": freshness_key.source_generation,
            "stored_at": stored_at,
            "status": int(freshness_key.status),
            "payload_signature": payload_signature,
            "request_descriptor": payload.request_descriptor,
            "payload": payload.payload,
        }

    def manifest(
        self,
        signature: str,
        payload_signature: str,
        freshness_key: SessionFilesFreshnessKey,
        stored_at: float,
        payload_changed: bool,
    ) -> dict[str, Any]:
        return {
            "version": self.version,
            "signature": signature,
            "stored_at": stored_at,
            "status": int(freshness_key.status),
            "payload_signature": payload_signature,
            "payload_changed": payload_changed,
            "owner": self.owner_generation(),
            "refresh_status": "ready",
            "last_error": "",
        }


class SessionFilesPostWrite:
    def __init__(
        self,
        cache_dir: Path,
        record_phase: Callable[[str, float, dict[str, Any]], None],
        request_prune: Callable[[str], bool],
    ) -> None:
        self.cache_dir = cache_dir
        self.record_phase = record_phase
        self.request_prune = request_prune

    def after_write(
        self,
        result: PublishedJsonWrite,
        signature: str,
        freshness_key: SessionFilesFreshnessKey,
    ) -> None:
        del freshness_key
        try:
            payload_size = result.record_path.stat().st_size
        except OSError:
            payload_size = 0
        try:
            manifest_stat = result.manifest_path.stat()
            payload_size += manifest_stat.st_size
            mtime = max(result.stored_at, float(manifest_stat.st_mtime))
        except OSError:
            mtime = result.stored_at
        session_files.update_disk_cache_index(self.cache_dir, signature, size=payload_size, mtime=mtime)
        try:
            index_bytes = session_files.disk_cache_index_path(self.cache_dir).stat().st_size
        except OSError:
            index_bytes = 0
        self.record_phase(
            "durable-cache-write",
            0.0,
            {
                "payload_and_manifest_bytes": int(payload_size),
                "index_bytes_rewritten": int(index_bytes),
            },
        )
        self.request_prune("write")


@dataclass(frozen=True)
class TabberFreshnessKey:
    source_signature: str
    hours: float
    allow_source_mismatch: bool


class TabberValidator:
    def __init__(self, version: int) -> None:
        self.version = version

    def payload(self, record: dict[str, Any], signature: str) -> dict[str, Any] | None:
        if record.get("version") != self.version or record.get("signature") != signature:
            return None
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return None
        try:
            float(record.get("stored_at", 0.0))
        except (TypeError, ValueError):
            return None
        return payload


class TabberFreshness:
    def __init__(
        self,
        version: int,
        payload_signature: PayloadSignature,
        bounded_hours: Callable[[Any], float],
    ) -> None:
        self.version = version
        self.payload_signature = payload_signature
        self.bounded_hours = bounded_hours

    def evaluate(
        self,
        record: dict[str, Any],
        manifest: dict[str, Any] | None,
        payload: dict[str, Any],
        freshness_key: TabberFreshnessKey,
        *,
        now: float,
        max_age_seconds: float | None,
    ) -> PublishedJsonFreshness | None:
        source_matches = str(record.get("source_signature") or "") == freshness_key.source_signature
        if not source_matches and not freshness_key.allow_source_mismatch:
            return None
        try:
            stored_at = float(record.get("stored_at", 0.0))
        except (TypeError, ValueError):
            return None
        payload_signature = str(record.get("payload_signature") or self.payload_signature(payload))
        if (
            isinstance(manifest, dict)
            and manifest.get("version") == self.version
            and manifest.get("signature") == record.get("signature")
            and manifest.get("payload_signature") == payload_signature
            and (freshness_key.allow_source_mismatch or str(manifest.get("source_signature") or "") == freshness_key.source_signature)
        ):
            try:
                stored_at = float(manifest.get("stored_at", stored_at))
            except (TypeError, ValueError):
                pass
        cached_hours = self.bounded_hours(payload.get("session_file_hours"))
        if cached_hours != session_files.bounded_session_files_hours(freshness_key.hours):
            return None
        age_seconds = max(0.0, now - stored_at)
        fresh = source_matches and (max_age_seconds is None or age_seconds <= max_age_seconds)
        return PublishedJsonFreshness(source_matches, fresh, age_seconds, stored_at)


class TabberRecordManifest(RecordManifestOwner):
    def payload_signature(self, payload: dict[str, Any]) -> str:
        return self._payload_signature(payload)

    def payload_changed(
        self,
        existing: dict[str, Any] | None,
        signature: str,
        payload_signature: str,
        freshness_key: TabberFreshnessKey,
    ) -> bool:
        del freshness_key
        if not isinstance(existing, dict) or existing.get("version") != self.version or existing.get("signature") != signature:
            return True
        existing_payload = existing.get("payload")
        existing_signature = str(existing.get("payload_signature") or "")
        if not existing_signature and isinstance(existing_payload, dict):
            existing_signature = self._payload_signature(existing_payload)
        return existing_signature != payload_signature

    def record(
        self,
        signature: str,
        payload: dict[str, Any],
        payload_signature: str,
        freshness_key: TabberFreshnessKey,
        stored_at: float,
    ) -> dict[str, Any]:
        return {
            "version": self.version,
            "signature": signature,
            "source_signature": freshness_key.source_signature,
            "stored_at": stored_at,
            "payload_signature": payload_signature,
            "payload": payload,
        }

    def manifest(
        self,
        signature: str,
        payload_signature: str,
        freshness_key: TabberFreshnessKey,
        stored_at: float,
        payload_changed: bool,
    ) -> dict[str, Any]:
        return {
            "version": self.version,
            "signature": signature,
            "source_signature": freshness_key.source_signature,
            "stored_at": stored_at,
            "payload_signature": payload_signature,
            "payload_changed": payload_changed,
            "owner": self.owner_generation(),
            "refresh_status": "ready",
            "last_error": "",
        }


class TabberPostWrite:
    def after_write(self, result: PublishedJsonWrite, signature: str, freshness_key: TabberFreshnessKey) -> None:
        del result, signature, freshness_key


def session_files_cache(
    *,
    version: int,
    cache_dir: Path,
    payload_signature: PayloadSignature,
    owner_generation: OwnerGeneration,
    record_phase: Callable[[str, float, dict[str, Any]], None],
    request_prune: Callable[[str], bool],
    clock: Callable[[], float],
    writer: Callable[..., None],
) -> PublishedJsonCache[SessionFilesCachedPayload, SessionFilesFreshnessKey]:
    return PublishedJsonCache(
        codec=DeterministicJsonCodec(),
        validator=SessionFilesValidator(version),
        freshness=SessionFilesFreshness(version, payload_signature),
        record_manifest=SessionFilesRecordManifest(version, payload_signature, owner_generation),
        post_write=SessionFilesPostWrite(cache_dir, record_phase, request_prune),
        clock=clock,
        writer=writer,
    )


def tabber_cache(
    *,
    version: int,
    payload_signature: PayloadSignature,
    owner_generation: OwnerGeneration,
    bounded_hours: Callable[[Any], float],
    clock: Callable[[], float],
    writer: Callable[..., None],
) -> PublishedJsonCache[dict[str, Any], TabberFreshnessKey]:
    return PublishedJsonCache(
        codec=DeterministicJsonCodec(),
        validator=TabberValidator(version),
        freshness=TabberFreshness(version, payload_signature, bounded_hours),
        record_manifest=TabberRecordManifest(version, payload_signature, owner_generation),
        post_write=TabberPostWrite(),
        clock=clock,
        writer=writer,
    )
