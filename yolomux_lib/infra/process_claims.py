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

A claimed helper whose supervisor died is NOT automatically reapable.  Some
helpers exist precisely so a peer can keep using them after the process that
spawned them is gone, and killing those is the exact failure adoption exists to
prevent.  Which outcome applies is a property of the ROOT the claim lives in,
not of the helper:

- a **caller-shared** root (the per-user runtime directory several YOLOmux
  servers share) may hand a surviving helper to a successor, because another
  live caller can legitimately still be using it;
- a **managed-private** root (one isolated ``YOLOMUX_ROOT``) may not.  Its
  helpers belong to exactly one launcher, so there is no successor to elect and
  no cross-root reuse, signal, unlink, reclaim, or adoption is ever performed.

Adoption is a transaction, not a flag.  Two successors racing the same dead
launcher must not both believe they own the helper, so the transfer is fenced
by an exclusively created adoption marker (``O_CREAT|O_EXCL``) carrying the
successor's own identity: exactly one successor can create it, the loser is
told so by name, and a successor that dies mid-transfer leaves a marker whose
recorded holder a later pass can fence and clear rather than a claim two owners
both think they hold.

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
CLAIM_ADOPTION_MARKER_SUFFIX = ".adopt.json"

# Whether the root this ledger lives in may ever hand a helper to a successor.
CLAIM_ROOT_CALLER_SHARED = "caller-shared"
CLAIM_ROOT_MANAGED_PRIVATE = "managed-private"
CLAIM_ROOT_SHARING_MODES = frozenset({CLAIM_ROOT_CALLER_SHARED, CLAIM_ROOT_MANAGED_PRIVATE})

# What the reaper actually did to one claimed survivor.
CLAIM_ACTION_NONE = "none"
CLAIM_ACTION_TERMINATE = "terminate"
CLAIM_ACTION_UNLINK_CLAIM = "unlink_claim"
CLAIM_ACTION_ADOPT = "adopt"

# What that action achieved.  `reported_only` is reserved for the fail-closed
# path and may never describe an attempt.
CLAIM_RESULT_REPORTED_ONLY = "reported_only"
CLAIM_RESULT_SIGNALLED = "signalled"
CLAIM_RESULT_SIGNAL_REFUSED = "signal_refused"
CLAIM_RESULT_ALREADY_EXITED = "already_exited"
CLAIM_RESULT_CLAIM_REMOVED = "claim_removed"
CLAIM_RESULT_CLAIM_REMOVE_FAILED = "claim_remove_failed"
CLAIM_RESULT_ADOPTED = "adopted"
CLAIM_RESULT_ADOPTION_REFUSED = "adoption_refused"
CLAIM_RESULT_ADOPTION_CONTENDED = "adoption_contended"
CLAIM_RESULT_ADOPTION_FAILED = "adoption_failed"

# Why the reaper reached that decision.  Identity reasons are carried straight
# through from `LocalProcessReason` so no second vocabulary can drift.
CLAIM_REASON_SUPERVISOR_ALIVE = "supervisor_alive"
CLAIM_REASON_SUPERVISOR_GONE = "supervisor_gone"
CLAIM_REASON_UNREADABLE_CLAIM = "unreadable_claim"
CLAIM_REASON_MISSING_SUPERVISOR_RECORD = "missing_supervisor_record"
CLAIM_REASON_KIND_MISMATCH = "kind_mismatch"
CLAIM_REASON_NAMESPACE_MISMATCH = "namespace_mismatch"
CLAIM_REASON_MANAGED_PRIVATE_ROOT = "managed_private_root"
CLAIM_REASON_ADOPTION_IN_PROGRESS = "adoption_in_progress"
CLAIM_REASON_STALE_ADOPTION_MARKER_CLEARED = "stale_adoption_marker_cleared"
CLAIM_REASON_ADOPTION_MARKER_UNREADABLE = "adoption_marker_unreadable"
CLAIM_REASON_CLAIM_CHANGED_DURING_ADOPTION = "claim_changed_during_adoption"
CLAIM_REASON_GENERATION_MISMATCH = "generation_mismatch"


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
        root_sharing: str = CLAIM_ROOT_CALLER_SHARED,
    ) -> None:
        clean_kind = str(kind or "").strip()
        if not clean_kind:
            raise ProcessClaimError("claim kind is required", reason_code=CLAIM_REASON_KIND_MISMATCH)
        clean_sharing = str(root_sharing or "").strip()
        if clean_sharing not in CLAIM_ROOT_SHARING_MODES:
            raise ProcessClaimError(
                f"unknown claim root sharing mode {root_sharing!r}; "
                f"use one of {sorted(CLAIM_ROOT_SHARING_MODES)}",
                reason_code=CLAIM_REASON_NAMESPACE_MISMATCH,
            )
        self.kind = clean_kind
        self.root_sharing = clean_sharing
        self.identity = host_identity or current_host_identity()
        # The host segment is part of the namespace, not a decoration: a shared
        # or NFS-mounted state root would otherwise let one host read another
        # host's claims and treat them as its own authority.
        self.directory = self.identity.namespaced_path(Path(root), self.kind)
        self.namespace = str(self.directory)
        self.clock = clock
        self.start_identity_reader = start_identity_reader
        # Set only inside one adoption transaction's cleanup and consumed by that
        # same transaction's row, so a marker that could not be removed is
        # reported instead of silently blocking the next adoption.
        self._last_marker_cleanup_error = ""

    @property
    def adoption_permitted(self) -> bool:
        """Whether this root's matrix allows a successor to inherit a helper at all."""

        return self.root_sharing == CLAIM_ROOT_CALLER_SHARED

    def claim_path(self, claim_id: str) -> Path:
        return self.directory / f"{claim_id}{CLAIM_FILE_SUFFIX}"

    def adoption_marker_path(self, claim_id: str) -> Path:
        return self.directory / f"{claim_id}{CLAIM_ADOPTION_MARKER_SUFFIX}"

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
            # Persisted, not derived: a reader deciding whether this helper may
            # ever be handed to a successor must not have to re-guess the root's
            # sharing mode from the path it happens to be reading.
            "root_sharing": self.root_sharing,
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

    def _unsupervised_state(
        self,
        path: Path,
        record: dict[str, Any] | None,
        *,
        action: str,
    ) -> tuple[dict[str, Any] | None, int, LocalProcessDiagnostic | None]:
        """Fence one claim's kind, namespace, and supervisor, shared by reap and adopt.

        Returns ``(refusal_row, pid, supervisor_state)``.  A non-``None`` refusal
        row is the whole outcome and the caller must not act further; a ``None``
        refusal with a supervisor state means the supervisor is provably gone and
        the caller owns the next decision.  ``action`` is the action the caller
        would have attempted, so a refusal names what was refused instead of
        flattening every gap into ``none``.
        """

        if record is None:
            return _row(path, 0, action, CLAIM_RESULT_REPORTED_ONLY, CLAIM_REASON_UNREADABLE_CLAIM), 0, None
        pid = _record_pid(record)
        if str(record.get("kind") or "") != self.kind:
            return _row(path, pid, action, CLAIM_RESULT_REPORTED_ONLY, CLAIM_REASON_KIND_MISMATCH), pid, None
        if str(record.get("namespace") or "") != self.namespace:
            return _row(path, pid, action, CLAIM_RESULT_REPORTED_ONLY, CLAIM_REASON_NAMESPACE_MISMATCH), pid, None
        supervisor = record.get("supervisor")
        if not isinstance(supervisor, dict) or not supervisor:
            return (
                _row(path, pid, action, CLAIM_RESULT_REPORTED_ONLY, CLAIM_REASON_MISSING_SUPERVISOR_RECORD),
                pid,
                None,
            )
        supervisor_state = is_current_local_process(supervisor, host_identity=self.identity)
        if supervisor_state.current:
            row = _row(path, pid, action, CLAIM_RESULT_REPORTED_ONLY, CLAIM_REASON_SUPERVISOR_ALIVE)
            row["surviving_supervisor"] = supervisor_state.as_dict()
            return row, pid, supervisor_state
        return None, pid, supervisor_state

    def adopt_unsupervised(self, *, generation: str = "") -> list[dict[str, Any]]:
        """Transfer, to this process, every claim whose supervisor is provably gone.

        This is the only path by which a helper outlives its launcher.  It is
        refused outright on a managed-private root, and every transfer that does
        happen is a single-winner transaction: the successor is named in the
        rewritten claim under ``surviving_supervisor``, so "who is keeping this
        alive" is answerable from the claim file alone rather than inferred.

        ``generation`` optionally restricts adoption to claims published for one
        spawn epoch, so a successor cannot inherit a generation it never asked
        for.  An empty value adopts regardless of generation and records the
        claim's own.
        """

        adopted: list[dict[str, Any]] = []
        for path, record in self.rows():
            adopted.append(self._adopt_one(path, record, generation=str(generation or "")))
        return adopted

    def _adopt_one(self, path: Path, record: dict[str, Any] | None, *, generation: str) -> dict[str, Any]:
        if not self.adoption_permitted:
            # A managed-private root has exactly one launcher, so there is no
            # successor to elect. Refusing here -- rather than in each caller --
            # is what keeps the "zero cross-root adoption" rule structural.
            return _row(
                path,
                _record_pid(record or {}),
                CLAIM_ACTION_ADOPT,
                CLAIM_RESULT_ADOPTION_REFUSED,
                CLAIM_REASON_MANAGED_PRIVATE_ROOT,
            )
        refusal, pid, _supervisor_state = self._unsupervised_state(path, record, action=CLAIM_ACTION_ADOPT)
        if refusal is not None:
            return refusal
        assert record is not None
        if generation and str(record.get("generation") or "") != generation:
            return _row(path, pid, CLAIM_ACTION_ADOPT, CLAIM_RESULT_ADOPTION_REFUSED, CLAIM_REASON_GENERATION_MISMATCH)
        target_state = is_current_local_process(record, host_identity=self.identity)
        if not target_state.current:
            # Nothing survives to adopt. Say so with the fence's own reason and
            # leave the claim for the reaping path rather than deleting it here:
            # adoption never removes a claim it did not take.
            return _row(path, pid, CLAIM_ACTION_ADOPT, CLAIM_RESULT_ADOPTION_REFUSED, target_state.reason.value)
        claim_id = str(record.get("claim_id") or "")
        if not claim_id or self.claim_path(claim_id) != path:
            return _row(path, pid, CLAIM_ACTION_ADOPT, CLAIM_RESULT_ADOPTION_REFUSED, CLAIM_REASON_UNREADABLE_CLAIM)
        return self._adopt_under_marker(path, record, pid, claim_id, target_state)

    def _adopt_under_marker(
        self,
        path: Path,
        record: dict[str, Any],
        pid: int,
        claim_id: str,
        target_state: LocalProcessDiagnostic,
    ) -> dict[str, Any]:
        marker_path = self.adoption_marker_path(claim_id)
        successor = self.identity.process_record_fields()
        try:
            descriptor = os.open(marker_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return self._resolve_contended_marker(path, marker_path, pid)
        except OSError as error:
            row = _row(path, pid, CLAIM_ACTION_ADOPT, CLAIM_RESULT_ADOPTION_FAILED, CLAIM_REASON_SUPERVISOR_GONE)
            row["error"] = type(error).__name__
            return row
        try:
            marker_payload = {"claim_id": claim_id, "successor": successor, "marked_at": float(self.clock())}
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(marker_payload, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            # The claim must still be the exact one that was fenced. Re-reading
            # under the marker is what makes the transfer a transaction rather
            # than two independent reads with a hole between them.
            current = _read_claim(path)
            if current is None or str(current.get("claim_id") or "") != claim_id:
                return _row(
                    path,
                    pid,
                    CLAIM_ACTION_ADOPT,
                    CLAIM_RESULT_ADOPTION_FAILED,
                    CLAIM_REASON_CLAIM_CHANGED_DURING_ADOPTION,
                )
            payload = dict(current)
            payload["supervisor"] = successor
            payload["adopted_from"] = record.get("supervisor")
            payload["adopted_at"] = float(self.clock())
            payload["adoption_count"] = int(current.get("adoption_count") or 0) + 1
            try:
                atomic_write_text(
                    path,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                    mode=0o600,
                )
            except OSError as error:
                row = _row(path, pid, CLAIM_ACTION_ADOPT, CLAIM_RESULT_ADOPTION_FAILED, CLAIM_REASON_SUPERVISOR_GONE)
                row["error"] = type(error).__name__
                return row
        finally:
            try:
                marker_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                # The marker outliving its transaction blocks the NEXT adoption of
                # this claim, so the failure has to reach the caller rather than
                # being dropped into a bare `pass`.
                self._last_marker_cleanup_error = type(error).__name__
        row = _row(path, pid, CLAIM_ACTION_ADOPT, CLAIM_RESULT_ADOPTED, CLAIM_REASON_SUPERVISOR_GONE)
        row["surviving_supervisor"] = dict(successor)
        row["previous_supervisor"] = record.get("supervisor")
        row["generation"] = str(record.get("generation") or "")
        row["target_identity"] = target_state.as_dict()
        if self._last_marker_cleanup_error:
            row["marker_remove_error"] = self._last_marker_cleanup_error
            self._last_marker_cleanup_error = ""
        return row

    def _resolve_contended_marker(self, path: Path, marker_path: Path, pid: int) -> dict[str, Any]:
        """Report a live transfer, or clear one whose successor is provably gone.

        A crashed successor must not lock the claim out of adoption forever, but
        clearing its marker on any weaker proof than the same identity fence used
        everywhere else would let two successors overlap.  A cleared marker is
        reported, never immediately retried: the next pass re-proves everything.
        """

        marker = _read_claim(marker_path)
        holder = marker.get("successor") if isinstance(marker, dict) else None
        if not isinstance(holder, dict) or not holder:
            return _row(
                path,
                pid,
                CLAIM_ACTION_ADOPT,
                CLAIM_RESULT_ADOPTION_CONTENDED,
                CLAIM_REASON_ADOPTION_MARKER_UNREADABLE,
            )
        holder_state = is_current_local_process(holder, host_identity=self.identity)
        if holder_state.current or not holder_state.may_remove_stale_record:
            row = _row(
                path,
                pid,
                CLAIM_ACTION_ADOPT,
                CLAIM_RESULT_ADOPTION_CONTENDED,
                CLAIM_REASON_ADOPTION_IN_PROGRESS,
            )
            row["adoption_holder"] = holder_state.as_dict()
            return row
        try:
            marker_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            row = _row(
                path,
                pid,
                CLAIM_ACTION_ADOPT,
                CLAIM_RESULT_ADOPTION_CONTENDED,
                CLAIM_REASON_ADOPTION_IN_PROGRESS,
            )
            row["error"] = type(error).__name__
            return row
        return _row(
            path,
            pid,
            CLAIM_ACTION_ADOPT,
            CLAIM_RESULT_ADOPTION_CONTENDED,
            CLAIM_REASON_STALE_ADOPTION_MARKER_CLEARED,
        )

    def _reap_one(
        self,
        path: Path,
        record: dict[str, Any] | None,
        *,
        signal_process: Callable[[int, int], None],
        signal_number: int,
    ) -> dict[str, Any]:
        refusal, pid, _supervisor_state = self._unsupervised_state(path, record, action=CLAIM_ACTION_NONE)
        if refusal is not None:
            return refusal
        assert record is not None
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
