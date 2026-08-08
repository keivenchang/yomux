# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The one owner of inotify capacity measurement and gate admission.

``inotify_init`` fails with ``EMFILE`` — rendered by the Rust ``notify`` crate as
``Too many open files (os error 24)`` — once the uid-wide
``fs.inotify.max_user_instances`` ceiling is reached, even while the process fd
table is nearly empty.  A gate that only inspects ``RLIMIT_NOFILE`` therefore
reports enormous headroom for the limit that actually refused the watcher, and
the refusal surfaces far from its cause: some later test silently degrades to
polling and fails on a missing changed-path record.

The ceiling is shared by every process this uid runs, not just the gate, so
capacity has to be admitted *before* the heavy lanes create browsers, watch
daemons, local services and xdist workers — after those lanes retire, the
evidence is gone.  Raising the ceiling is operator-owned mitigation and is never
performed here; a higher ceiling must also never be allowed to turn a fixture
leak green, which is why fixture teardown separately proves its instances return
to the measured pre-test baseline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

INOTIFY_MAX_USER_INSTANCES_PATH = Path("/proc/sys/fs/inotify/max_user_instances")
INOTIFY_MAX_USER_WATCHES_PATH = Path("/proc/sys/fs/inotify/max_user_watches")
INOTIFY_MAX_QUEUED_EVENTS_PATH = Path("/proc/sys/fs/inotify/max_queued_events")
INOTIFY_FD_TARGET = "anon_inode:inotify"
UNMEASURED_LIMIT = -1

# Declared minimums for this box's parallel gate profile. The default Linux
# ceiling of 128 instances is shared with every long-lived agent this uid runs,
# and the gate alone creates roughly one watch daemon plus two browser instances
# per xdist worker across three concurrent lanes.
REQUIRED_MAX_USER_INSTANCES = 1024
REQUIRED_MAX_USER_WATCHES = 1_048_576
REQUIRED_MAX_QUEUED_EVENTS = 65_536
# Headroom the gate must still have after the ambient, non-gate holders are
# counted. A ceiling that is high enough on paper is not capacity if other
# processes have already consumed it.
REQUIRED_FREE_INSTANCES = 256

ADMITTED_CODE = "inotify_capacity_admitted"
CODE_UNMEASURABLE = "inotify_capacity_unmeasurable"
CODE_INSTANCES_BELOW = "inotify_max_user_instances_below_requirement"
CODE_WATCHES_BELOW = "inotify_max_user_watches_below_requirement"
CODE_QUEUED_BELOW = "inotify_max_queued_events_below_requirement"
CODE_HEADROOM_BELOW = "inotify_free_instances_below_requirement"

REMEDIATION_COMMAND = (
    "sudo sysctl -w "
    f"fs.inotify.max_user_instances={REQUIRED_MAX_USER_INSTANCES} "
    f"fs.inotify.max_user_watches={REQUIRED_MAX_USER_WATCHES} "
    f"fs.inotify.max_queued_events={REQUIRED_MAX_QUEUED_EVENTS}"
)
REMEDIATION_PERSIST = (
    "printf 'fs.inotify.max_user_instances=%d\\nfs.inotify.max_user_watches=%d\\n"
    "fs.inotify.max_queued_events=%d\\n' "
    f"{REQUIRED_MAX_USER_INSTANCES} {REQUIRED_MAX_USER_WATCHES} {REQUIRED_MAX_QUEUED_EVENTS} "
    "| sudo tee /etc/sysctl.d/60-yolomux-inotify.conf && sudo sysctl --system"
)


def read_kernel_limit(path: Path) -> int:
    """Return one published kernel limit, or ``UNMEASURED_LIMIT`` when absent.

    A platform that does not publish the file is recorded as unmeasured rather
    than defaulted, because a substituted number would let a report claim
    headroom nobody measured.
    """

    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return UNMEASURED_LIMIT


def process_fd_owners(pid: int) -> dict[str, int]:
    """Resolve one process's descriptors to their targets, not just a count.

    A bare count cannot name a leaking owner; the resolved target is what
    separates an inotify instance from a socket, a pipe or a held file.
    """

    owners: dict[str, int] = {}
    fd_dir = Path(f"/proc/{pid}/fd")
    try:
        names = os.listdir(fd_dir)
    except OSError:
        return owners
    for name in names:
        try:
            target = os.readlink(fd_dir / name)
        except OSError:
            continue
        owners[target] = owners.get(target, 0) + 1
    return owners


def inotify_instance_census() -> tuple[int, dict[int, int]]:
    """Count this uid's inotify instances per pid and in total.

    The kernel accounts instances per uid across every namespace, but ``/proc``
    inside a container shows only that namespace's processes.  A census taken
    inside the test container therefore undercounts the ceiling's real consumers
    and must not be read as the uid-wide total.
    """

    per_pid: dict[int, int] = {}
    total = 0
    try:
        entries = os.listdir("/proc")
    except OSError:
        return 0, per_pid
    uid = os.getuid()
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid:
                continue
        except OSError:
            continue
        count = sum(
            occurrences
            for target, occurrences in process_fd_owners(pid).items()
            if target == INOTIFY_FD_TARGET
        )
        if count:
            per_pid[pid] = count
            total += count
    return total, per_pid


@dataclass(frozen=True)
class InotifyCapacityVerdict:
    """Whether this host may start the heavy gate lanes, and why not."""

    admitted: bool
    reason_code: str
    measured: dict[str, int]
    required: dict[str, int]
    in_use_instances: int
    free_instances: int
    top_holders: tuple[tuple[int, int], ...] = field(default=())

    def as_reason(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "reason_code": self.reason_code,
            "measured": dict(self.measured),
            "required": dict(self.required),
            "in_use_instances": self.in_use_instances,
            "free_instances": self.free_instances,
            "top_holders": [list(holder) for holder in self.top_holders],
            "remediation": REMEDIATION_COMMAND,
            "remediation_persist": REMEDIATION_PERSIST,
        }

    def refusal_text(self) -> str:
        """One operator-readable verdict naming current, required and remedy.

        The header follows the verdict.  An admitted capacity rendered under a
        "REFUSED" banner would be a false report of the very state this module
        exists to measure.
        """

        header = (
            f"INOTIFY CAPACITY ADMITTED: {self.reason_code}"
            if self.admitted
            else f"INOTIFY CAPACITY REFUSED: {self.reason_code}"
        )
        lines = [
            header,
            f"  fs.inotify.max_user_instances = {self.measured['max_user_instances']} "
            f"(required >= {self.required['max_user_instances']})",
            f"  fs.inotify.max_user_watches   = {self.measured['max_user_watches']} "
            f"(required >= {self.required['max_user_watches']})",
            f"  fs.inotify.max_queued_events  = {self.measured['max_queued_events']} "
            f"(required >= {self.required['max_queued_events']})",
            f"  instances in use by this uid  = {self.in_use_instances}",
            f"  free instances                = {self.free_instances} "
            f"(required >= {self.required['free_instances']})",
            "  This is an operator-owned host change; the gate never applies it.",
            f"  Remediate now:     {REMEDIATION_COMMAND}",
            f"  Persist on reboot: {REMEDIATION_PERSIST}",
        ]
        if self.top_holders:
            holders = ", ".join(f"pid {pid}={count}" for pid, count in self.top_holders)
            lines.append(f"  largest current holders: {holders}")
        return "\n".join(lines)


def inotify_capacity_verdict(
    *,
    required_instances: int = REQUIRED_MAX_USER_INSTANCES,
    required_watches: int = REQUIRED_MAX_USER_WATCHES,
    required_queued_events: int = REQUIRED_MAX_QUEUED_EVENTS,
    required_free_instances: int = REQUIRED_FREE_INSTANCES,
) -> InotifyCapacityVerdict:
    """Admit or refuse this host's inotify capacity before heavy lanes start."""

    max_instances = read_kernel_limit(INOTIFY_MAX_USER_INSTANCES_PATH)
    max_watches = read_kernel_limit(INOTIFY_MAX_USER_WATCHES_PATH)
    max_queued = read_kernel_limit(INOTIFY_MAX_QUEUED_EVENTS_PATH)
    measured = {
        "max_user_instances": max_instances,
        "max_user_watches": max_watches,
        "max_queued_events": max_queued,
    }
    required = {
        "max_user_instances": required_instances,
        "max_user_watches": required_watches,
        "max_queued_events": required_queued_events,
        "free_instances": required_free_instances,
    }
    in_use, per_pid = inotify_instance_census()
    top_holders = tuple(sorted(per_pid.items(), key=lambda item: -item[1])[:5])
    free_instances = max_instances - in_use if max_instances != UNMEASURED_LIMIT else UNMEASURED_LIMIT

    def refuse(code: str) -> InotifyCapacityVerdict:
        return InotifyCapacityVerdict(
            admitted=False,
            reason_code=code,
            measured=measured,
            required=required,
            in_use_instances=in_use,
            free_instances=free_instances,
            top_holders=top_holders,
        )

    if UNMEASURED_LIMIT in (max_instances, max_watches, max_queued):
        return refuse(CODE_UNMEASURABLE)
    if max_instances < required_instances:
        return refuse(CODE_INSTANCES_BELOW)
    if max_watches < required_watches:
        return refuse(CODE_WATCHES_BELOW)
    if max_queued < required_queued_events:
        return refuse(CODE_QUEUED_BELOW)
    if free_instances < required_free_instances:
        return refuse(CODE_HEADROOM_BELOW)
    return InotifyCapacityVerdict(
        admitted=True,
        reason_code=ADMITTED_CODE,
        measured=measured,
        required=required,
        in_use_instances=in_use,
        free_instances=free_instances,
        top_holders=top_holders,
    )
