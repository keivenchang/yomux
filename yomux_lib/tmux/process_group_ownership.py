# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Identity-verified ownership for process-group signals."""

from __future__ import annotations

from dataclasses import dataclass
import os
import signal
import subprocess
from typing import Any
from typing import Callable
import uuid

from ..infra.host_identity import process_start_identity


PROCESS_GROUP_DEPLOYMENT_ID = uuid.uuid4().hex
PROCESS_GROUP_IDENTITY_ATTRIBUTE = "_yolomux_process_group_identity"


@dataclass(frozen=True)
class ProcessGroupIdentity:
    """One deployment's recorded identity for a spawned process-group leader."""

    deployment_id: str
    leader_pid: int
    pgid: int
    leader_start_identity: str


def record_owned_process_group(
    process: subprocess.Popen[Any],
    *,
    deployment_id: str = PROCESS_GROUP_DEPLOYMENT_ID,
    pgid_reader: Callable[[int], int] | None = None,
    start_identity_reader: Callable[[int], str | None] | None = None,
) -> ProcessGroupIdentity | None:
    """Record the identity immediately after spawning a new-session process."""

    leader_pid = int(process.pid)
    if process.poll() is not None:
        return None
    read_pgid = pgid_reader or os.getpgid
    read_start_identity = start_identity_reader or process_start_identity
    try:
        pgid = int(read_pgid(leader_pid))
    except (OSError, ValueError):
        return None
    start_identity = read_start_identity(leader_pid)
    if process.poll() is not None or leader_pid <= 1 or pgid != leader_pid or not start_identity:
        return None
    identity = ProcessGroupIdentity(
        deployment_id=str(deployment_id),
        leader_pid=leader_pid,
        pgid=pgid,
        leader_start_identity=str(start_identity),
    )
    setattr(process, PROCESS_GROUP_IDENTITY_ATTRIBUTE, identity)
    return identity


def owned_process_group_identity(process: subprocess.Popen[Any]) -> ProcessGroupIdentity | None:
    identity = process.__dict__.get(PROCESS_GROUP_IDENTITY_ATTRIBUTE)
    return identity if isinstance(identity, ProcessGroupIdentity) else None


def signal_owned_process_group(
    identity: ProcessGroupIdentity | None,
    signum: int,
    *,
    deployment_id: str = PROCESS_GROUP_DEPLOYMENT_ID,
    pgid_reader: Callable[[int], int] | None = None,
    start_identity_reader: Callable[[int], str | None] | None = None,
    killpg: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Signal only a live group whose deployment, PGID, and leader birth still match."""

    if identity is None:
        return {"signalled": False, "reason": "process_group_ownership_missing"}
    if identity.deployment_id != str(deployment_id):
        return {"signalled": False, "reason": "process_group_owned_by_another_deployment"}

    read_pgid = pgid_reader or os.getpgid
    read_start_identity = start_identity_reader or process_start_identity
    send_group_signal = killpg or os.killpg
    try:
        live_pgid = int(read_pgid(identity.leader_pid))
    except ProcessLookupError:
        return {"signalled": False, "reason": "nothing_to_kill"}
    except (OSError, ValueError):
        return {"signalled": False, "reason": "process_group_live_identity_unavailable"}
    live_start_identity = read_start_identity(identity.leader_pid)
    if live_start_identity is None:
        return {"signalled": False, "reason": "process_group_live_identity_unavailable"}
    if live_pgid != identity.pgid or str(live_start_identity) != identity.leader_start_identity:
        return {"signalled": False, "reason": "process_group_identity_recycled"}
    try:
        send_group_signal(identity.pgid, int(signum))
    except ProcessLookupError:
        return {"signalled": False, "reason": "nothing_to_kill"}
    except PermissionError:
        return {"signalled": False, "reason": "process_group_signal_permission_denied"}
    return {"signalled": True, "reason": "process_group_signalled"}


def signal_recorded_process_group(process: subprocess.Popen[Any], signum: int) -> dict[str, Any]:
    """Verify and signal the identity recorded on one owned Popen instance."""

    if process.poll() is not None:
        return {"signalled": False, "reason": "nothing_to_kill"}
    return signal_owned_process_group(owned_process_group_identity(process), signum)


def refuse_unowned_process_group_signals_for_test(
    *,
    deployment_id: str,
    attempts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Exercise refusal outcomes with simulated ownership and live identities."""

    outcomes: list[dict[str, Any]] = []
    for attempt in attempts:
        case = str(attempt.get("case") or "")
        pgid = int(attempt.get("pgid") or 0)
        recorded_start = str(attempt.get("recorded_leader_start_ticks", 100))
        live_start = str(attempt.get("live_leader_start_ticks", recorded_start))
        identity = None
        if case != "no_record":
            identity = ProcessGroupIdentity(
                deployment_id=str(attempt.get("owner_deployment_id") or deployment_id),
                leader_pid=pgid,
                pgid=pgid,
                leader_start_identity=recorded_start,
            )
        simulated_signals: list[tuple[int, int]] = []
        outcome = signal_owned_process_group(
            identity,
            signal.SIGTERM,
            deployment_id=deployment_id,
            pgid_reader=lambda _pid, live_pgid=pgid: live_pgid,
            start_identity_reader=lambda _pid, value=live_start: value,
            killpg=lambda target_pgid, target_signum: simulated_signals.append((target_pgid, target_signum)),
        )
        if simulated_signals:
            raise AssertionError(f"refusal case signalled process group: {simulated_signals}")
        outcomes.append({"case": case, **outcome})
    return outcomes
