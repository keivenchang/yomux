# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""One identity-fenced claim ledger for helper processes YOLOmux spawns.

A YOLOmux server spawns helper processes it supervises in-band through a live
handle (``subprocess.Popen``, a thread, a process group).  When that server
dies hard, the handle dies with it and the helper survives.  Reaping the
survivor later is a destructive decision, and the only thing that makes it a
*decision* rather than a guess is a persisted claim written while the spawner
still had direct proof of what it created.

A claim binds five dimensions, not one:

- **host/boot/pid/start identity** -- the fence
  ``infra.host_identity.is_current_local_process`` already owns, so a recycled
  PID can never be mistaken for the claimed process;
- **kind** -- what sort of helper this is, so one owner's ledger can never
  authorize a signal against another owner's helper;
- **namespace** -- the directory root the claim belongs to, so two YOLOmux
  installations sharing a host never read each other's claims;
- **generation** -- the spawn epoch, so a survivor of generation N is
  distinguishable from the live process of generation N+1;
- **supervisor** -- the identity of the process that spawned it.

The supervisor field is what makes retention truthful.  A claimed helper whose
supervisor is still the current local process is *deliberately retained*, and
the row says so by name: ``surviving_supervisor`` carries that supervisor's
identity.  Retention without a named surviving owner is exactly the silence
this ledger exists to remove.

Rejected shortcuts, restated so they are not reintroduced: ``ps`` command-text
matching, PPID/PGID, hostname, or "it looks like one of ours" are never
sufficient authority.  Every ambiguity fails closed -- no signal, no unlink,
one typed row naming the reason.
"""

from __future__ import annotations

import json
import os
import signal
import time
import uuid
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .atomic_file import atomic_write_text
from .host_identity import HostIdentity
from .host_identity import LocalProcessDiagnostic
from .host_identity import LocalProcessReason
from .host_identity import current_host_identity
from .host_identity import is_current_local_process
from .host_identity import process_start_identity


CLAIM_FILE_SUFFIX = ".claim.json"

# What the reaper actually did to one claimed survivor.
CLAIM_ACTION_NONE = "none"
CLAIM_ACTION_TERMINATE = "terminate"
CLAIM_ACTION_UNLINK_CLAIM = "unlink_claim"

# What that action achieved.  `reported_only` is reserved for the fail-closed
# path and may never describe an attempt.
CLAIM_RESULT_REPORTED_ONLY = "reported_only"
CLAIM_RESULT_SIGNALLED = "signalled"
CLAIM_RESULT_SIGNAL_REFUSED = "signal_refused"
CLAIM_RESULT_ALREADY_EXITED = "already_exited"
CLAIM_RESULT_CLAIM_REMOVED = "claim_removed"
CLAIM_RESULT_CLAIM_REMOVE_FAILED = "claim_remove_failed"

# Why the reaper reached that decision.  Identity reasons are carried straight
# through from `LocalProcessReason` so no second vocabulary can drift.
CLAIM_REASON_SUPERVISOR_ALIVE = "supervisor_alive"
CLAIM_REASON_SUPERVISOR_GONE = "supervisor_gone"
CLAIM_REASON_UNREADABLE_CLAIM = "unreadable_claim"
CLAIM_REASON_MISSING_SUPERVISOR_RECORD = "missing_supervisor_record"
CLAIM_REASON_KIND_MISMATCH = "kind_mismatch"
CLAIM_REASON_NAMESPACE_MISMATCH = "namespace_mismatch"


class ProcessClaimError(RuntimeError):
    """A claim could not be published, so its target can never be reaped by claim."""

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ProcessClaim:
    """One persisted, identity-bound authority to signal exactly one process."""

    path: Path
    kind: str
    namespace: str
    generation: str
    claim_id: str
    pid: int
    record: dict[str, Any]
    supervisor: dict[str, Any]
    claimed_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "kind": self.kind,
            "namespace": self.namespace,
            "generation": self.generation,
            "claimed_at": self.claimed_at,
            "supervisor": dict(self.supervisor),
            **dict(self.record),
        }


class ProcessClaimLedger:
    """Publish, release, and fail-closed reap claims for one helper kind."""

    def __init__(
        self,
        root: Path,
        kind: str,
        *,
        host_identity: HostIdentity | None = None,
        clock: Callable[[], float] = time.time,
        start_identity_reader: Callable[[int], str | None] = process_start_identity,
    ) -> None:
        clean_kind = str(kind or "").strip()
        if not clean_kind:
            raise ProcessClaimError("claim kind is required", reason_code=CLAIM_REASON_KIND_MISMATCH)
        self.kind = clean_kind
        self.identity = host_identity or current_host_identity()
        # The host segment is part of the namespace, not a decoration: a shared
        # or NFS-mounted state root would otherwise let one host read another
        # host's claims and treat them as its own authority.
        self.directory = self.identity.namespaced_path(Path(root), self.kind)
        self.namespace = str(self.directory)
        self.clock = clock
        self.start_identity_reader = start_identity_reader

    def claim_path(self, claim_id: str) -> Path:
        return self.directory / f"{claim_id}{CLAIM_FILE_SUFFIX}"

    def publish(self, pid: int, *, generation: str = "", details: Mapping[str, Any] | None = None) -> ProcessClaim:
        """Persist authority over one live PID, or refuse and say why.

        Refusal is not an inconvenience to be defaulted away: an unclaimed
        helper is simply never reapable by this ledger, which is the correct
        fail-closed outcome.  The caller must record the refusal at its own
        supervisor boundary rather than continuing as if a claim existed.
        """

        target_pid = int(pid)
        if target_pid <= 1:
            raise ProcessClaimError(
                f"{self.kind} claim refused: pid {target_pid} names no supervisable process",
                reason_code=LocalProcessReason.INVALID_PID.value,
            )
        start_identity = self.start_identity_reader(target_pid)
        if not start_identity:
            raise ProcessClaimError(
                f"{self.kind} claim refused: pid {target_pid} has no readable process-start identity",
                reason_code=LocalProcessReason.MISSING_PROCESS_START_IDENTITY.value,
            )
        claim_id = uuid.uuid4().hex
        claimed_at = float(self.clock())
        record = self.identity.process_record_fields(pid=target_pid, start_identity=str(start_identity))
        supervisor = self.identity.process_record_fields()
        payload = {
            "claim_id": claim_id,
            "kind": self.kind,
            "namespace": self.namespace,
            "generation": str(generation or ""),
            "claimed_at": claimed_at,
            "supervisor": supervisor,
            "details": dict(details or {}),
            **record,
        }
        path = self.claim_path(claim_id)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", mode=0o600)
        except OSError as error:
            raise ProcessClaimError(
                f"{self.kind} claim refused: {type(error).__name__} writing {path}",
                reason_code="claim_write_failed",
            ) from error
        return ProcessClaim(
            path=path,
            kind=self.kind,
            namespace=self.namespace,
            generation=str(generation or ""),
            claim_id=claim_id,
            pid=target_pid,
            record=record,
            supervisor=supervisor,
            claimed_at=claimed_at,
        )

    def release(self, claim: ProcessClaim) -> bool:
        """Drop authority after the supervisor has stopped its own helper."""

        try:
            claim.path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    def _claim_paths(self) -> list[Path]:
        try:
            return sorted(self.directory.glob(f"*{CLAIM_FILE_SUFFIX}"))
        except OSError:
            return []

    def rows(self) -> Iterator[tuple[Path, dict[str, Any] | None]]:
        for path in self._claim_paths():
            record = _read_claim(path)
            yield path, record

    def reap_unsupervised(
        self,
        *,
        signal_process: Callable[[int, int], None] = os.kill,
        signal_number: int = signal.SIGTERM,
    ) -> list[dict[str, Any]]:
        """Signal only claimed helpers whose supervisor is provably gone.

        Every branch returns exactly one typed row.  A claim whose supervisor is
        still the current local process is deliberately retained and names that
        surviving supervisor; a claim that cannot be read, or whose target
        identity cannot be re-proved, is reported and never acted on.
        """

        reaped: list[dict[str, Any]] = []
        for path, record in self.rows():
            reaped.append(self._reap_one(path, record, signal_process=signal_process, signal_number=signal_number))
        return reaped

    def _reap_one(
        self,
        path: Path,
        record: dict[str, Any] | None,
        *,
        signal_process: Callable[[int, int], None],
        signal_number: int,
    ) -> dict[str, Any]:
        if record is None:
            return _row(path, 0, CLAIM_ACTION_NONE, CLAIM_RESULT_REPORTED_ONLY, CLAIM_REASON_UNREADABLE_CLAIM)
        pid = _record_pid(record)
        if str(record.get("kind") or "") != self.kind:
            return _row(path, pid, CLAIM_ACTION_NONE, CLAIM_RESULT_REPORTED_ONLY, CLAIM_REASON_KIND_MISMATCH)
        if str(record.get("namespace") or "") != self.namespace:
            return _row(path, pid, CLAIM_ACTION_NONE, CLAIM_RESULT_REPORTED_ONLY, CLAIM_REASON_NAMESPACE_MISMATCH)
        supervisor = record.get("supervisor")
        if not isinstance(supervisor, dict) or not supervisor:
            return _row(path, pid, CLAIM_ACTION_NONE, CLAIM_RESULT_REPORTED_ONLY, CLAIM_REASON_MISSING_SUPERVISOR_RECORD)
        supervisor_state = is_current_local_process(supervisor, host_identity=self.identity)
        if supervisor_state.current:
            row = _row(path, pid, CLAIM_ACTION_NONE, CLAIM_RESULT_REPORTED_ONLY, CLAIM_REASON_SUPERVISOR_ALIVE)
            row["surviving_supervisor"] = supervisor_state.as_dict()
            return row
        target_state = is_current_local_process(record, host_identity=self.identity)
        if target_state.current:
            return self._terminate(path, pid, target_state, signal_process=signal_process, signal_number=signal_number)
        if target_state.reason in _CLAIM_TARGET_GONE_REASONS:
            # The claimed process cannot exist any more, so the claim file is the
            # only leftover.  Removing it is record-only cleanup: no signal, no
            # adoption, and it is the only way a stale claim stops accumulating.
            return self._unlink_claim(path, pid, target_state)
        return _row(path, pid, CLAIM_ACTION_NONE, CLAIM_RESULT_REPORTED_ONLY, target_state.reason.value)

    def _terminate(
        self,
        path: Path,
        pid: int,
        target_state: LocalProcessDiagnostic,
        *,
        signal_process: Callable[[int, int], None],
        signal_number: int,
    ) -> dict[str, Any]:
        try:
            signal_process(pid, signal_number)
        except ProcessLookupError:
            return self._unlink_claim(path, pid, target_state, result=CLAIM_RESULT_ALREADY_EXITED)
        except (PermissionError, OSError) as error:
            row = _row(
                path,
                pid,
                CLAIM_ACTION_TERMINATE,
                CLAIM_RESULT_SIGNAL_REFUSED,
                CLAIM_REASON_SUPERVISOR_GONE,
            )
            row["error"] = type(error).__name__
            return row
        row = _row(path, pid, CLAIM_ACTION_TERMINATE, CLAIM_RESULT_SIGNALLED, CLAIM_REASON_SUPERVISOR_GONE)
        row["signal"] = int(signal_number)
        # The claim is spent the moment it is used: leaving it would let a later
        # pass signal a recycled PID on the strength of an already-cashed proof.
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            row["claim_remove_error"] = type(error).__name__
        return row

    def _unlink_claim(
        self,
        path: Path,
        pid: int,
        target_state: LocalProcessDiagnostic,
        *,
        result: str = CLAIM_RESULT_CLAIM_REMOVED,
    ) -> dict[str, Any]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            row = _row(path, pid, CLAIM_ACTION_UNLINK_CLAIM, CLAIM_RESULT_CLAIM_REMOVE_FAILED, target_state.reason.value)
            row["error"] = type(error).__name__
            return row
        return _row(path, pid, CLAIM_ACTION_UNLINK_CLAIM, result, target_state.reason.value)


_CLAIM_TARGET_GONE_REASONS = frozenset({
    LocalProcessReason.PROCESS_NOT_FOUND,
    LocalProcessReason.PROCESS_IDENTITY_REUSED,
    LocalProcessReason.PREVIOUS_BOOT,
})


def _read_claim(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _record_pid(record: Mapping[str, Any]) -> int:
    try:
        value = int(record.get("pid") or 0)
    except (TypeError, ValueError):
        return 0
    return value


def _row(path: Path, pid: int, action: str, result: str, reason: str) -> dict[str, Any]:
    return {
        "claim_path": str(path),
        "pid": int(pid),
        "attempted_action": action,
        "result": result,
        "reason": reason,
    }
