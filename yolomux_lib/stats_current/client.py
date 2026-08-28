# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Current-only statsd client with one append, snapshot, and exact-delta action."""

from __future__ import annotations

import json
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
from yolomux_lib.observability.failure_severity import BROWSER_UPLOAD_OUTCOME_OWNER
from yolomux_lib.observability.failure_severity import CALLER_OUTCOME_OWNER_FIELD
from yolomux_lib.stats_current import protocol, revision, storage

SERVICE_NAME = "statsd"
SERVICE_MODULE = "yolomux_lib.stats_current.service"
SOCKET_FILENAME = storage.SOCKET_FILENAME
LEASE_TIMEOUT_SECONDS = 3.0
STATUS_TIMEOUT_SECONDS = LEASE_TIMEOUT_SECONDS
BROWSER_PROFILES_TIMEOUT_SECONDS = 0.5
# statsd runs its full migration audit -- opening the retained on-disk database
# and reading its whole snapshot -- inside on_start, before it opens its socket
# to answer a status ping or publish its identity record. That cost scales with
# the retained database, not with a constant: a 400MB+ stats database measured
# ~6.5s to first healthy status, well past the shared 5.0s spawn budget that
# suits lightweight daemons. Under the default the spawn always timed out, so
# statsd never confirmed startup and crash-looped (empty stderr, unreaped
# zombie child, "service_absent"). This budget keeps a genuinely hung migration
# bounded while giving a real large-database open room to finish.
STATS_START_TIMEOUT_SECONDS = 30.0


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


def _encoded_record_size(record: object) -> int:
    """Exact encoded length one record contributes to the shared metadata frame.

    ``encode_metadata`` serializes the whole envelope with ``sort_keys`` and the
    compact separators, and a JSON array encodes each element independently.
    Sizing one record with the same options therefore yields the exact byte count
    that record adds inside its list, which is what lets a batch be sized by
    arithmetic instead of by re-encoding the whole candidate batch.
    """

    return len(
        json.dumps(
            _record_payload(record),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _list_size_delta(sizes: Sequence[int]) -> int:
    """Bytes a populated record list adds over the empty ``[]`` baseline."""

    if not sizes:
        return 0
    return sum(sizes) + len(sizes) - 1


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
    # Size every record exactly once. The previous binary search re-encoded the
    # whole candidate batch on each probe, so a single append serialized its
    # payload O(log n) times before sending it; on the live collector that
    # sizing search was 40% of the thread's wall time.
    baseline = append_metadata_size()
    fixed_sizes = tuple(
        tuple(_encoded_record_size(record) for record in group)
        for group in fixed_groups
    )
    variable_sizes = tuple(_encoded_record_size(item) for _kind, item in variable)
    variable_offset = 0
    first_batch = True
    while first_batch or variable_offset < len(variable):
        batch_observations = fixed_groups[0] if first_batch else ()
        batch_coverage = fixed_groups[1] if first_batch else ()
        batch_unavailable = fixed_groups[2] if first_batch else ()
        capacity = protocol.MAX_APPEND_RECORDS - sum(map(len, (
            batch_observations, batch_coverage, batch_unavailable,
        )))
        fixed_delta = sum(
            _list_size_delta(sizes if first_batch else ())
            for sizes in fixed_sizes
        )
        budget = LOCAL_RPC_MAX_METADATA_BYTES - baseline - fixed_delta
        # Encoded size grows monotonically with the prefix length, so the longest
        # prefix that fits is found by accumulation and equals the old bisection.
        limit = min(len(variable) - variable_offset, capacity)
        used = {"atom": 0, "tombstone": 0}
        variable_delta = 0
        atom_count = 0
        while atom_count < limit:
            kind, _item = variable[variable_offset + atom_count]
            added = variable_sizes[variable_offset + atom_count] + (
                1 if used[kind] else 0
            )
            if variable_delta + added > budget:
                break
            variable_delta += added
            used[kind] += 1
            atom_count += 1
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
    return storage.default_database_path(state_dir)


def default_socket_path(state_dir: Path | None = None) -> Path:
    return storage.default_socket_path(state_dir)


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
    request_binary: bytes = b"",
) -> tuple[dict[str, Any], bytes]:
    stamped = _stamp(action, payload)
    envelope = new_envelope(service, action, stamped, timeout_seconds=timeout)
    if request_binary:
        response, binary = local_service_request(
            socket_path,
            envelope,
            binary=request_binary,
            timeout_seconds=timeout,
        )
    else:
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
            start_timeout_seconds=STATS_START_TIMEOUT_SECONDS,
            extra_args=("--database", str(database_path)),
            code_revision=revision.CURRENT_CODE_REVISION,
            build_revision=storage.MIN_WRITER_BUILD,
        )
        service_dir = self.registry.service_dir
        self.registry = _CurrentRegistry(
            service_dir,
            self.registry.spec,
            socket_path=self.socket_path,
            service_dir=service_dir,
        )

    def dispatch(
        self,
        action: str,
        payload: Mapping[str, object] | None = None,
        *,
        timeout: float = 0.5,
        request_binary: bytes = b"",
    ) -> tuple[dict[str, Any], bytes]:
        try:
            response, binary = _wire_rpc(
                self.socket_path,
                self.service,
                action,
                payload,
                timeout,
                request_binary,
            )
        except (OSError, LocalRpcError) as error:
            self.registry.note_rpc_failure()
            return {
                "ok": False,
                "error": redact_local_service_text(error),
                "_transport_error": self._transport_error(error),
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
        bypass_cached_upgrade: bool = False,
    ) -> dict[str, Any]:
        if self._upgrade_required is not None and not bypass_cached_upgrade:
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
        self._transport.registry.prune_stale_runtime_locks_once()
        if self._transport.registry.recently_healthy() or self._transport.registry.healthy():
            return True
        upgrade = self._transport.registry._upgrade_required
        if isinstance(upgrade, dict) and upgrade:
            self._upgrade_required = {
                "ok": False,
                "error_code": "upgrade_required",
                **upgrade,
            }
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
        except storage.SchemaMismatchError:
            # An unreadable current file must reach statsd's migration owner so
            # it can quarantine the artifact and report a structured recovery.
            # A newer writer remains fenced by the distinct branch above.
            pass
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

    def retry(self) -> bool:
        # A retry is the one recovery boundary for a read-side RPC fence.  The
        # version-scoped socket keeps mixed builds apart, so clearing a stale
        # cached reply cannot start a cross-version respawn war.
        self._upgrade_required = None
        self._transport.registry.retry()
        return self.ensure_started()

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

    def release_lease(
        self,
        lease_id: str,
        *,
        bypass_cached_upgrade: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(lease_id, str) or not lease_id:
            raise ValueError("lease_id must be a non-empty string")
        return self._call(
            "release",
            {"lease_id": lease_id},
            timeout=LEASE_TIMEOUT_SECONDS,
            # A terminal renewal still leaves a real lease in statsd. Cleanup may dispatch once,
            # but the cached fence remains installed for every subsequent normal operation.
            bypass_cached_upgrade=bypass_cached_upgrade,
        )

    def register_collector_context(
        self,
        *,
        pid: int,
        port: int,
        owner_generation: int,
        control_socket: str,
    ) -> dict[str, Any]:
        """Register the elected web identity and where to reach it."""

        return self._call(
            "collector_context",
            {
                "pid": pid,
                "port": port,
                "owner_generation": owner_generation,
                "control_socket": control_socket,
            },
            timeout=STATUS_TIMEOUT_SECONDS,
        )

    def status(self) -> dict[str, Any]:
        response = self._call("status", timeout=STATUS_TIMEOUT_SECONDS)
        if response.get("_transport_error"):
            return self._transport.registry.failure_response()
        return response

    def resource_state(self) -> dict[str, Any]:
        """Lock-free control state for `/readyz`. Deliberately NOT `status()`.

        `status()` reaches `_status()`, which opens with `work_lock` -- the lock the materializer
        worker holds across a build. A readiness probe that waits behind the daemon it is checking
        reports nothing about it. This action's handler takes no lock at all.

        A public method rather than callers reaching through `_call`, so the owner boundary stays
        one method wide and a future transport change has one place to happen. "Public" here is
        Python visibility, NOT an HTTP tier: this payload carries pending cell counts, migration
        state and failure strings, and its only route is authenticated `/readyz`. Do not wire it
        to an unauthenticated endpoint.
        """
        response = self._call("resource_state", timeout=STATUS_TIMEOUT_SECONDS)
        if response.get("_transport_error"):
            return {}
        return response

    def browser_diagnostics(self) -> dict[str, Any]:
        return self._call("browser_profiles", timeout=BROWSER_PROFILES_TIMEOUT_SECONDS)

    def set_usage_atom_backfill_status(self, status: Mapping[str, object]) -> dict[str, Any]:
        return self._call("usage_atom_backfill", dict(status), timeout=STATUS_TIMEOUT_SECONDS)

    def runtime_status(self, status: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Project statsd's own status through its existing process sampler.

        statsd declares NEITHER `demand_started` NOR `absence_expected_reason`, and that is the
        decision, not an omission. It is lazily created like the other five, but a background
        loop keeps it hot: `StatsCurrentRuntime._supervise` holds a statsd lease for as long as
        this process is the elected background owner (`stats_current/runtime.py:365-368`) and
        the scheduler then appends over RPC at the `cpu` family's 1s cadence
        (`stats_current/families.py:130-134`), browser or no browser. A service a loop exercises
        every second is not demand-scoped, and flagging it `demand_started` would turn a real
        statsd outage into silence.

        There is also no switched-off path to excuse. statsd has no user-facing disable, and its
        row is truthful from any port: identity comes from a live `status` RPC
        (`StatsCurrentRuntime._service_status`), not from this process's lease, so a port that is
        not the background owner still sees the statsd the owner is keeping up.
        """
        service = dict(status) if isinstance(status, Mapping) else self.status()
        pid = int(service.get("pid") or 0)
        return {
            **service,
            "service": "statsd",
            "resources": self._transport.registry.resources(pid),
        }

    def append(
        self,
        *,
        observations: Sequence[storage.Observation] = (),
        usage_atoms: Sequence[storage.UsageAtom] = (),
        usage_tombstones: Sequence[storage.UsageAtomTombstone] = (),
        coverage_epochs: Sequence[storage.CoverageEpoch] = (),
        unavailable_spans: Sequence[storage.UnavailableSpan] = (),
        browser_upload: bytes | None = None,
        authenticated_username: str = "",
    ) -> dict[str, Any]:
        groups = (
            (observations, storage.Observation),
            (usage_atoms, storage.UsageAtom),
            (usage_tombstones, storage.UsageAtomTombstone),
            (coverage_epochs, storage.CoverageEpoch),
            (unavailable_spans, storage.UnavailableSpan),
        )
        total = sum(len(records) for records, _kind in groups)
        if browser_upload is not None:
            if total:
                raise ValueError("browser upload cannot be mixed with typed append records")
            if not isinstance(browser_upload, bytes) or not 1 <= len(browser_upload) <= 128 * 1024:
                raise ValueError("browser upload must contain 1..131072 bytes")
            if not isinstance(authenticated_username, str) or not authenticated_username:
                raise ValueError("authenticated_username must be non-empty text")
            response, _binary = self._transport.dispatch(
                "browser_upload",
                {"authenticated_username": authenticated_username},
                timeout=3.0,
                request_binary=browser_upload,
            )
            # Only statsd's browser-payload validator can fence one stale tab without poisoning
            # this shared client, which every lease and typed append also goes through. An
            # unmarked upgrade response is a real daemon protocol fence and must still be
            # remembered so it stops those callers too.
            if response.get(CALLER_OUTCOME_OWNER_FIELD) == BROWSER_UPLOAD_OUTCOME_OWNER:
                return response
            return self._remember(response)
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
            if request.chunk_index is not None:
                params["chunk_index"] = str(request.chunk_index)
                params["chunk_generation"] = str(request.chunk_generation)
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
        if validated.chunk_index is not None:
            payload["chunk_index"] = validated.chunk_index
            payload["chunk_generation"] = validated.chunk_generation
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
