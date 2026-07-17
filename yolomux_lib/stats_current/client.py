# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Current-only statsd client with one append, snapshot, and exact-delta action."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import fields
from pathlib import Path
from typing import Any

from yolomux_lib import common
from yolomux_lib.local_services.client import LocalServiceClient
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.rpc import LocalRpcError, new_envelope, request as local_service_request
from yolomux_lib.local_services.rpc import LOCAL_RPC_MAX_METADATA_BYTES, encode_metadata
from yolomux_lib.local_services.runtime import redact_local_service_text
from yolomux_lib.stats_current import protocol, revision, storage

SERVICE_NAME = "statsd"
SERVICE_MODULE = "yolomux_lib.stats_current.service"
SOCKET_FILENAME = "statsd.sock"
LEASE_TIMEOUT_SECONDS = 3.0
STATUS_TIMEOUT_SECONDS = LEASE_TIMEOUT_SECONDS


def _plain_json_value(value: object) -> object:
    """Copy immutable validated fact containers without ``asdict`` deep-copying them."""

    if isinstance(value, Mapping):
        return {key: _plain_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json_value(item) for item in value]
    if isinstance(value, list):
        return [_plain_json_value(item) for item in value]
    return value


def _record_payload(record: object) -> dict[str, object]:
    return {
        field.name: _plain_json_value(getattr(record, field.name))
        for field in fields(record)
    }


def _append_payload(
    observations: Sequence[storage.Observation],
    usage_atoms: Sequence[storage.UsageAtom],
    coverage_epochs: Sequence[storage.CoverageEpoch],
    unavailable_spans: Sequence[storage.UnavailableSpan],
    usage_tombstones: Sequence[storage.UsageAtomTombstone] = (),
) -> dict[str, object]:
    return {
        "observations": [_record_payload(record) for record in observations],
        "usage_atoms": [_record_payload(record) for record in usage_atoms],
        "usage_tombstones": [_record_payload(record) for record in usage_tombstones],
        "coverage_epochs": [_record_payload(record) for record in coverage_epochs],
        "unavailable_spans": [_record_payload(record) for record in unavailable_spans],
    }


def _append_metadata_size(payload: Mapping[str, object]) -> int:
    stamped = _stamp("append", payload)
    envelope = new_envelope(
        SERVICE_NAME,
        "append",
        stamped,
        timeout_seconds=3.0,
    )
    return len(encode_metadata(envelope))


def append_metadata_size(
    *,
    observations: Sequence[storage.Observation] = (),
    usage_atoms: Sequence[storage.UsageAtom] = (),
    usage_tombstones: Sequence[storage.UsageAtomTombstone] = (),
    coverage_epochs: Sequence[storage.CoverageEpoch] = (),
    unavailable_spans: Sequence[storage.UnavailableSpan] = (),
) -> int:
    return _append_metadata_size(
        _append_payload(
            observations, usage_atoms, coverage_epochs, unavailable_spans,
            usage_tombstones,
        )
    )


AppendBatch = tuple[
    tuple[storage.Observation, ...],
    tuple[storage.UsageAtom, ...],
    tuple[storage.UsageAtomTombstone, ...],
    tuple[storage.CoverageEpoch, ...],
    tuple[storage.UnavailableSpan, ...],
]


def iter_append_batches(
    *,
    observations: Sequence[storage.Observation] = (),
    usage_atoms: Sequence[storage.UsageAtom] = (),
    usage_tombstones: Sequence[storage.UsageAtomTombstone] = (),
    coverage_epochs: Sequence[storage.CoverageEpoch] = (),
    unavailable_spans: Sequence[storage.UnavailableSpan] = (),
) -> Iterator[AppendBatch]:
    """Partition facts by both the record limit and their exact encoded RPC size."""

    fixed_groups = (
        tuple(observations), tuple(coverage_epochs), tuple(unavailable_spans),
    )
    variable = (
        *(("atom", item) for item in usage_atoms),
        *(("tombstone", item) for item in usage_tombstones),
    )
    fixed_count = sum(len(group) for group in fixed_groups)
    if fixed_count > protocol.MAX_APPEND_RECORDS:
        raise ValueError(
            f"append requires at most {protocol.MAX_APPEND_RECORDS} fixed records"
        )
    if fixed_count == 0 and not variable:
        return
    variable_offset = 0
    first_batch = True
    while first_batch or variable_offset < len(variable):
        batch_observations = fixed_groups[0] if first_batch else ()
        batch_coverage = fixed_groups[1] if first_batch else ()
        batch_unavailable = fixed_groups[2] if first_batch else ()
        capacity = protocol.MAX_APPEND_RECORDS - sum(map(len, (
            batch_observations, batch_coverage, batch_unavailable,
        )))
        low, high = 0, min(len(variable) - variable_offset, capacity)
        while low < high:
            candidate = (low + high + 1) // 2
            candidate_rows = variable[variable_offset:variable_offset + candidate]
            candidate_atoms = tuple(item for kind, item in candidate_rows if kind == "atom")
            candidate_tombstones = tuple(
                item for kind, item in candidate_rows if kind == "tombstone"
            )
            size = append_metadata_size(
                observations=batch_observations,
                usage_atoms=candidate_atoms,
                usage_tombstones=candidate_tombstones,
                coverage_epochs=batch_coverage,
                unavailable_spans=batch_unavailable,
            )
            if size <= LOCAL_RPC_MAX_METADATA_BYTES:
                low = candidate
            else:
                high = candidate - 1
        atom_count = low
        batch_rows = variable[variable_offset:variable_offset + atom_count]
        batch_atoms = tuple(item for kind, item in batch_rows if kind == "atom")
        batch_tombstones = tuple(
            item for kind, item in batch_rows if kind == "tombstone"
        )
        if append_metadata_size(
            observations=batch_observations,
            usage_atoms=batch_atoms,
            usage_tombstones=batch_tombstones,
            coverage_epochs=batch_coverage,
            unavailable_spans=batch_unavailable,
        ) > LOCAL_RPC_MAX_METADATA_BYTES:
            raise ValueError("fixed append facts exceed the local RPC limit")
        if (
            atom_count == 0
            and variable_offset < len(variable)
            and not batch_observations
            and not batch_coverage
            and not batch_unavailable
        ):
            raise ValueError("usage record exceeds the local RPC limit")
        yield (
            batch_observations, batch_atoms, batch_tombstones, batch_coverage,
            batch_unavailable,
        )
        first_batch = False
        variable_offset += atom_count


def default_database_path(state_dir: Path | None = None) -> Path:
    return Path(state_dir or common.STATE_DIR) / storage.DATABASE_FILENAME


def default_socket_path(state_dir: Path | None = None) -> Path:
    return Path(state_dir or common.STATE_DIR) / "services" / SOCKET_FILENAME


def _stamp(action: str, payload: Mapping[str, object] | None = None) -> dict[str, Any]:
    return {
        **dict(payload or {}),
        "action": action,
        "protocol_version": storage.MIN_WRITER_PROTOCOL,
        "schema_generation": storage.SCHEMA_VERSION,
    }


def _wire_rpc(
    socket_path: Path,
    service: str,
    action: str,
    payload: Mapping[str, object] | None,
    timeout: float,
) -> tuple[dict[str, Any], bytes]:
    stamped = _stamp(action, payload)
    envelope = new_envelope(service, action, stamped, timeout_seconds=timeout)
    response, binary = local_service_request(
        socket_path,
        envelope,
        timeout_seconds=timeout,
    )
    if not isinstance(response, dict):
        raise LocalRpcError("invalid local service response")
    return response, binary


class _CurrentRegistry(LocalServiceRegistry):
    def _request(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 0.2,
    ) -> dict[str, Any]:
        try:
            response, _binary = _wire_rpc(
                self.socket_path,
                self.spec.name,
                method,
                payload,
                timeout,
            )
        except (OSError, LocalRpcError):
            return {}
        if response.get("error_code") == "upgrade_required" or response.get("status") == "upgrade_required":
            required = int(
                response.get("required_protocol_version") or response.get("version") or 0
            )
            if required > self.spec.protocol_version:
                self._upgrade_required = dict(response)
        return response if isinstance(response, dict) else {}


class _CurrentTransport(LocalServiceClient):
    def __init__(self, socket_path: Path, database_path: Path):
        super().__init__(
            SERVICE_NAME,
            SERVICE_MODULE,
            socket_path,
            storage.MIN_WRITER_PROTOCOL,
            extra_args=("--database", str(database_path)),
            code_revision=revision.CURRENT_CODE_REVISION,
        )
        self.registry = _CurrentRegistry(
            self.socket_path.parent,
            self.registry.spec,
            socket_path=self.socket_path,
        )

    def dispatch(
        self,
        action: str,
        payload: Mapping[str, object] | None = None,
        *,
        timeout: float = 0.5,
    ) -> tuple[dict[str, Any], bytes]:
        try:
            response, binary = _wire_rpc(
                self.socket_path,
                self.service,
                action,
                payload,
                timeout,
            )
        except (OSError, LocalRpcError) as error:
            self.registry.note_rpc_failure()
            return {
                "ok": False,
                "error": redact_local_service_text(error),
                "_transport_error": "rpc",
            }, b""
        self.registry.note_rpc_success()
        return response, binary


class StatsCurrentClient:
    """One write plus exact snapshot/delta reads; terminal upgrade state stays private."""

    def __init__(
        self,
        socket_path: Path | None = None,
        database_path: Path | None = None,
    ):
        self.database_path = Path(database_path or default_database_path())
        self._transport = _CurrentTransport(
            Path(socket_path or default_socket_path()),
            self.database_path,
        )
        self._upgrade_required: dict[str, Any] | None = None

    def _remember(self, response: dict[str, Any]) -> dict[str, Any]:
        if response.get("error_code") == "upgrade_required" or response.get("status") == "upgrade_required":
            self._upgrade_required = dict(response)
        return response

    def _call(
        self,
        action: str,
        payload: Mapping[str, object] | None = None,
        *,
        timeout: float = 0.5,
    ) -> dict[str, Any]:
        if self._upgrade_required is not None:
            return dict(self._upgrade_required)
        response, _binary = self._transport.dispatch(
            action,
            payload,
            timeout=timeout,
        )
        return self._remember(response)

    def ensure_started(self) -> bool:
        if self._upgrade_required is not None:
            return False
        try:
            storage.require_compatible_writer(self.database_path)
        except storage.SchemaTooNewError as error:
            self._upgrade_required = {
                "ok": False,
                "status": "upgrade_required",
                "error_code": "upgrade_required",
                "required_protocol_version": error.minimum_writer_protocol,
                "required_schema_generation": error.found_schema,
                "required_build": str(error.minimum_writer_build),
            }
            return False
        started = self._transport.registry.ensure_started()
        if not started:
            upgrade = self._transport.registry._upgrade_required
            if isinstance(upgrade, dict) and upgrade:
                self._upgrade_required = {
                    "ok": False,
                    "error_code": "upgrade_required",
                    **upgrade,
                }
        return started

    def acquire_lease(self) -> dict[str, Any]:
        if not self.ensure_started():
            return dict(self._upgrade_required or {"ok": False, "error": "statsd unavailable"})
        return self._call(
            "lease",
            {"client_pid": os.getpid(), "lease_id": ""},
            timeout=LEASE_TIMEOUT_SECONDS,
        )

    def renew_lease(self, lease_id: str) -> dict[str, Any]:
        if not isinstance(lease_id, str) or not lease_id:
            raise ValueError("lease_id must be a non-empty string")
        if not self.ensure_started():
            return dict(self._upgrade_required or {"ok": False, "error": "statsd unavailable"})
        return self._call(
            "lease",
            {"client_pid": os.getpid(), "lease_id": lease_id},
            timeout=LEASE_TIMEOUT_SECONDS,
        )

    def release_lease(self, lease_id: str) -> dict[str, Any]:
        if not isinstance(lease_id, str) or not lease_id:
            raise ValueError("lease_id must be a non-empty string")
        return self._call(
            "release",
            {"lease_id": lease_id},
            timeout=LEASE_TIMEOUT_SECONDS,
        )

    def status(self) -> dict[str, Any]:
        return self._call("status", timeout=STATUS_TIMEOUT_SECONDS)

    def append(
        self,
        *,
        observations: Sequence[storage.Observation] = (),
        usage_atoms: Sequence[storage.UsageAtom] = (),
        usage_tombstones: Sequence[storage.UsageAtomTombstone] = (),
        coverage_epochs: Sequence[storage.CoverageEpoch] = (),
        unavailable_spans: Sequence[storage.UnavailableSpan] = (),
    ) -> dict[str, Any]:
        groups = (
            (observations, storage.Observation),
            (usage_atoms, storage.UsageAtom),
            (usage_tombstones, storage.UsageAtomTombstone),
            (coverage_epochs, storage.CoverageEpoch),
            (unavailable_spans, storage.UnavailableSpan),
        )
        total = sum(len(records) for records, _kind in groups)
        if total < 1 or total > protocol.MAX_APPEND_RECORDS:
            raise ValueError(f"append requires 1..{protocol.MAX_APPEND_RECORDS} records")
        for records, kind in groups:
            if any(not isinstance(record, kind) for record in records):
                raise TypeError(f"append requires {kind.__name__} values")
        payload = _append_payload(
            observations, usage_atoms, coverage_epochs, unavailable_spans,
            usage_tombstones,
        )
        if _append_metadata_size(payload) > LOCAL_RPC_MAX_METADATA_BYTES:
            raise ValueError("append metadata exceeds the local RPC limit")
        return self._call(
            "append",
            payload,
            timeout=3.0,
        )

    def snapshot(
        self,
        request: protocol.SnapshotRequest | Mapping[str, object],
    ) -> tuple[dict[str, Any], bytes]:
        params: dict[str, object]
        if isinstance(request, protocol.SnapshotRequest):
            params = {
                "range_seconds": str(request.range_seconds),
                "resolution": str(request.resolution),
                "client_id": request.client_id,
            }
            if request.since_generation is not None:
                params["since_generation"] = str(request.since_generation)
        else:
            params = dict(request)
        validated = protocol.parse_snapshot_request(params)
        if self._upgrade_required is not None:
            return dict(self._upgrade_required), b""
        payload: dict[str, object] = {
            "range_seconds": validated.range_seconds,
            "resolution": validated.resolution,
            "client_id": validated.client_id,
        }
        if validated.since_generation is not None:
            payload["since_generation"] = validated.since_generation
        response, binary = self._transport.dispatch(
            "snapshot",
            payload,
            timeout=3.0,
        )
        remembered = self._remember(response)
        return (remembered, b"") if self._upgrade_required is not None else (remembered, binary)

    def delta(
        self,
        request: protocol.DeltaRequest | Mapping[str, object],
    ) -> tuple[dict[str, Any], bytes]:
        params: dict[str, object]
        if isinstance(request, protocol.DeltaRequest):
            params = {
                "range_seconds": str(request.range_seconds),
                "resolution_seconds": str(request.resolution_seconds),
                "client_id": request.client_id,
                "after_cache_generation": str(request.after_cache_generation),
                "after_revision": str(request.after_revision),
            }
        else:
            params = dict(request)
        validated = protocol.parse_delta_request(params)
        if self._upgrade_required is not None:
            return dict(self._upgrade_required), b""
        response, binary = self._transport.dispatch(
            "delta",
            {
                "range_seconds": validated.range_seconds,
                "resolution_seconds": validated.resolution_seconds,
                "client_id": validated.client_id,
                "after_cache_generation": validated.after_cache_generation,
                "after_revision": validated.after_revision,
            },
            timeout=3.0,
        )
        remembered = self._remember(response)
        return (remembered, b"") if self._upgrade_required is not None else (remembered, binary)
