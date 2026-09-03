# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Strict, shared listener ownership census for local YOLOmux tools."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
import argparse
import errno
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import time


LISTENER_PROBE_TIMEOUT_SECONDS = 3
_SOCKET_TARGET_RE = re.compile(r"^socket:\[(\d+)\]$")
# How many concrete degradation records a rendered summary may name. A shared host produced 561
# records and a 26,677-character message that no operator reads and no log wants; the counts below
# it stay exact so nothing is silently dropped.
LISTENER_DEGRADATION_RENDER_LIMIT = 5

# The two kinds of thing a census can fail to see, and the reason default identity is possible.
# TARGET: something about THIS port's listening inodes is unproven.
# GLOBAL: a live process could not be read, so it cannot be attributed to this target. On a shared
# Linux host this is normal - 557 of 866 live processes were unreadable in one measurement - so
# treating every such record as fatal makes operational identity impossible rather than safe.
SCOPE_TARGET = "target"
SCOPE_GLOBAL = "global visibility"

# A process that disappears mid-scan cannot own a live listening socket.
_LISTENER_SCAN_RACE = (FileNotFoundError, ProcessLookupError)
# The kernel refusing us another user's process is a fact about our access, not about ownership.
_LISTENER_DENIED_ERRNOS = frozenset({errno.EACCES, errno.EPERM})


class ListenerCensusError(RuntimeError):
    """The platform listener scanner could not prove the complete owner set."""


class ListenerCensusTimeout(ListenerCensusError):
    """The /proc walk exceeded the caller's published `timeout_seconds` budget."""


class ListenerCensusDegraded(ListenerCensusError):
    """An identity or exclusivity decision was asked of a census that saw less than the host.

    Separate from `ListenerCensusError` so a caller can tell "the scan broke" from "the scan ran
    but could not see everything". Both refuse the decision; only this one carries the records.
    """

    def __init__(self, port: int, census: "ListenerCensus", message: str | None = None) -> None:
        super().__init__(
            message
            if message is not None
            else f"port {port} listener ownership is unprovable: {census.degradation_summary()}"
        )
        self.census = census


@dataclass(frozen=True)
class ListenerDegradation:
    """One LIVE process the census could not read, and exactly why.

    A process that vanished mid-scan is not recorded here: it cannot own a live listening socket,
    so its absence costs the census nothing. A process that is present and unreadable is different
    - it may hold the socket, and nothing in /proc will say whether it does.

    `uid` is diagnostic only. It is recorded because it helps a human, and it is deliberately not
    consulted by any decision: `/proc/<pid>/fd` of a same-UID process is reassigned to root and
    mode 500 once that process clears its dumpable flag, so UID equality proves neither
    inspectability nor non-ownership.

    `pid` is None for exactly one case: a backend that cannot enumerate holders at all, where the
    limit belongs to the tool rather than to any one process.
    """

    pid: int | None
    stage: str
    errno_value: int | None
    detail: str
    uid: int | None = None
    scope: str = SCOPE_GLOBAL

    @property
    def error_code(self) -> str:
        return "UNKNOWN" if self.errno_value is None else errno.errorcode.get(self.errno_value, "UNKNOWN")

    def describe(self) -> str:
        if self.pid is None:
            return f"{self.stage}: {self.detail}"
        owner = "" if self.uid is None else f" uid {self.uid}"
        return f"pid {self.pid}{owner} {self.stage} (errno {self.errno_value} {self.error_code})"


@dataclass(frozen=True)
class ListenerCensus:
    """The ONE result shape every listener backend returns.

    `pids` is what the scan could see. `degradations` is what it could not, and the two travel
    together so no caller can read the first without the second being available. Every identity or
    exclusivity decision goes through the one shared selector `require_unique_listener_pid`: its default
    target-scoped mode refuses target degradation, and its explicit strict whole-host mode also refuses global.
    """

    pids: tuple[int, ...] = ()
    degradations: tuple[ListenerDegradation, ...] = field(default=())

    @property
    def target_degradations(self) -> tuple[ListenerDegradation, ...]:
        """Records that leave THIS port's ownership unproven."""

        return tuple(item for item in self.degradations if item.scope == SCOPE_TARGET)

    @property
    def global_degradations(self) -> tuple[ListenerDegradation, ...]:
        """Unreadable host processes that the scan could not attribute to this target."""

        return tuple(item for item in self.degradations if item.scope != SCOPE_TARGET)

    @property
    def degraded(self) -> bool:
        """Whole-host visibility is incomplete. Only STRICT identity refuses on this alone."""

        return bool(self.degradations)

    @property
    def target_degraded(self) -> bool:
        """This port's ownership is unproven. Every mode refuses on this."""

        return bool(self.target_degradations)

    def degradation_summary(self, *, limit: int = LISTENER_DEGRADATION_RENDER_LIMIT) -> str:
        """Bounded rendering: a few concrete records, then exact counts for everything else."""

        if not self.degradations:
            return "no degradation recorded"
        shown = self.degradations[:max(0, limit)]
        omitted = len(self.degradations) - len(shown)
        rendered = "; ".join(item.describe() for item in shown)
        # Count PROCESSES, not records. One process refusing readlink, stat and fdinfo produces
        # three records and is still one process an operator has to go look at.
        processes = len({item.pid for item in self.degradations if item.pid is not None})
        backend = sum(
            1
            for item in self.degradations
            if item.pid is None and item.scope != SCOPE_TARGET
        )
        targets = len(self.target_degradations)
        parts = []
        if targets:
            parts.append(f"{targets} unproven target record(s)")
        if processes:
            parts.append(f"{processes} unreadable live process(es)")
        if backend:
            parts.append(f"{backend} backend visibility limit(s)")
        head = " and ".join(parts) if parts else "degradation"
        tail = f"; {omitted} further record(s) omitted" if omitted else ""
        return f"{head}: {rendered}{tail}"

    def with_pids(self, pids: list[int] | tuple[int, ...]) -> "ListenerCensus":
        """Replace the visible set while carrying every degradation record forward."""

        return ListenerCensus(pids=tuple(pids), degradations=self.degradations)


def canonical_listener_pids(
    pids: list[int],
    *,
    parent_reader: Callable[[int], int],
    command_reader: Callable[[int], str],
) -> list[int]:
    """Collapse only a fork-before-exec clone of an identified listener owner."""

    candidates = sorted(set(pids))
    candidate_set = set(candidates)
    commands = {pid: command_reader(pid) for pid in candidates}
    canonical: list[int] = []
    for pid in candidates:
        command = commands[pid]
        ancestor = parent_reader(pid)
        seen = {pid}
        inherited = False
        while ancestor > 1 and ancestor not in seen:
            if ancestor in candidate_set:
                inherited = bool(command and command == commands[ancestor])
                break
            seen.add(ancestor)
            ancestor = parent_reader(ancestor)
        if not inherited:
            canonical.append(pid)
    return canonical


def parse_lsof_listener_snapshot(output: str) -> tuple[list[int], dict[int, int], dict[int, str]]:
    """Parse PID, parent, and command fields from one atomic lsof snapshot."""

    if not output.strip():
        raise ListenerCensusError("lsof listener census returned no ownership records with exit 0")
    pids: set[int] = set()
    parents: dict[int, int] = {}
    commands: dict[int, str] = {}
    current_pid = 0
    for field in output.splitlines():
        prefix, value = field[:1], field[1:]
        if not field:
            raise ListenerCensusError("lsof listener census returned an empty ownership field")
        if prefix == "p":
            if not value.isdigit() or int(value) <= 1:
                raise ListenerCensusError(f"lsof listener census returned an invalid PID field: {field!r}")
            current_pid = int(value)
            if current_pid in pids:
                raise ListenerCensusError(f"lsof listener census repeated PID {current_pid}")
            pids.add(current_pid)
        elif prefix == "R":
            if not current_pid or not value.isdigit() or int(value) < 0 or current_pid in parents:
                raise ListenerCensusError(f"lsof listener census returned an invalid parent field: {field!r}")
            parents[current_pid] = int(value)
        elif prefix == "c":
            if not current_pid or not value or current_pid in commands:
                raise ListenerCensusError(f"lsof listener census returned an invalid command field: {field!r}")
            commands[current_pid] = value
        elif prefix == "f":
            # Apple lsof emits the selected file descriptor even when -F requests only p/c/R.
            # It still belongs to the current process record, but ownership does not depend on it.
            if not current_pid or not value:
                raise ListenerCensusError(f"lsof listener census returned an invalid file descriptor field: {field!r}")
        else:
            raise ListenerCensusError(f"lsof listener census returned an unknown ownership field: {field!r}")
    incomplete = sorted(pid for pid in pids if pid not in parents or pid not in commands)
    if incomplete:
        raise ListenerCensusError(f"lsof listener census returned incomplete owner record(s) {incomplete}")
    return sorted(pids), parents, commands


def _proc_listener_inode_uids(port: int, proc_root: Path) -> dict[str, int]:
    inode_uids: dict[str, int] = {}
    for table in (proc_root / "net" / "tcp", proc_root / "net" / "tcp6"):
        try:
            rows = table.read_text(encoding="utf-8").splitlines()[1:]
        except OSError as error:
            raise ListenerCensusError(f"cannot read Linux TCP listener table {table}") from error
        for row in rows:
            fields = row.split()
            try:
                row_port = int(fields[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError) as error:
                raise ListenerCensusError(f"cannot parse listener table {table}") from error
            if len(fields) <= 9 or row_port != port or fields[3] != "0A":
                continue
            try:
                owner_uid = int(fields[7])
            except ValueError as error:
                raise ListenerCensusError(f"cannot parse listener table {table}") from error
            inode_uids[fields[9]] = owner_uid
    return inode_uids


def _proc_process_uid(process_dir: Path) -> int:
    return process_dir.stat().st_uid


def _proc_fd_stat_inode(entry: Path) -> int | None:
    metadata = entry.stat()
    return metadata.st_ino if stat.S_ISSOCK(metadata.st_mode) else None


def _proc_fdinfo_inode(entry: Path) -> int:
    fdinfo = entry.parent.parent / "fdinfo" / entry.name
    rows = fdinfo.read_text(encoding="utf-8").splitlines()
    values = [row.split(":", 1)[1].strip() for row in rows if row.startswith("ino:")]
    if len(values) != 1 or not values[0].isdigit():
        raise ListenerCensusError(f"cannot parse Linux file descriptor metadata {fdinfo}")
    return int(values[0])


def _listener_scan_denial(error: OSError) -> bool:
    """Whether one refused /proc read is a recordable DEGRADATION rather than a broken scan.

    The ONE rule for every per-process read in the /proc backend. A denied `fd` directory and a
    denied `fd/<n>` symlink differ only in which syscall the kernel rejected first, so they cannot
    mean different things about who owns a socket.

    UID is NOT consulted. An earlier revision skipped a denial only when the process UID differed
    from the socket creator's, which is wrong twice over: `/proc/<pid>/fd` becomes root-owned and
    mode 500 once a same-UID process clears its dumpable flag, and a process of any UID can inherit
    or be handed the descriptor. Worse, that skip was silent, so an accessible process and a denied
    process sharing one inode returned a single PID and satisfied the exact-one gate.

    Only EACCES/EPERM is degradation. Every other errno stays fatal, because EIO or ENOMEM
    describes a broken host rather than an unreadable neighbour, and an OSError carrying no errno
    stays fatal because inaccessibility is then unprovable.
    """

    return error.errno in _LISTENER_DENIED_ERRNOS


def _backend_visibility_degradation(system: str) -> ListenerDegradation:
    """Record why strict whole-host uniqueness cannot rely on this backend.

    `lsof` reports the sockets it is permitted to see. A snapshot naming one PID proves that
    holder exists; it never proves another holder was absent, and no tested authority condition
    here establishes otherwise. Default operational identity may still name the visible PID without
    claiming whole-host exclusivity; strict mode refuses this global degradation.
    """

    return ListenerDegradation(
        pid=None,
        stage="backend visibility",
        errno_value=None,
        detail=(
            f"lsof is the only listener backend on {system} and cannot prove it enumerated "
            "every holder of the socket"
        ),
        scope=SCOPE_GLOBAL,
    )


def _listener_degradation(process_dir: Path, stage: str, error: OSError, uid: int | None) -> ListenerDegradation:
    """Build one degradation record; `errno` is required, never Optional, at this boundary."""

    return ListenerDegradation(
        pid=int(process_dir.name),
        stage=stage,
        errno_value=int(error.errno),
        detail=str(error),
        uid=uid,
    )


def proc_listener_census(
    port: int,
    *,
    proc_root: Path = Path("/proc"),
    readlink: Callable[[os.PathLike[str]], str] = os.readlink,
    process_uid_reader: Callable[[Path], int] = _proc_process_uid,
    fd_stat_inode_reader: Callable[[Path], int | None] = _proc_fd_stat_inode,
    fdinfo_inode_reader: Callable[[Path], int] = _proc_fdinfo_inode,
    timeout_seconds: float = LISTENER_PROBE_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> ListenerCensus:
    """Map every listening socket inode to a PID, recording every process it could not read.

    Completeness has two halves. Every listening inode from /proc/net/tcp{,6} must be attributed,
    or the scan fails closed here. Separately, any live process this scan could not read is
    recorded as a degradation, because an unreadable process may hold the same inode an
    accessible one does - and then the visible PID set is real but incomplete.
    """

    deadline = clock() + max(0.0, float(timeout_seconds))

    def require_budget() -> None:
        if clock() >= deadline:
            raise ListenerCensusTimeout(
                f"listener census for port {port} exceeded {timeout_seconds}s while walking "
                f"{proc_root}"
            )

    inode_uids = _proc_listener_inode_uids(port, proc_root)
    require_budget()
    if not inode_uids:
        return ListenerCensus()
    try:
        process_dirs = tuple(proc_root.iterdir())
    except OSError as error:
        raise ListenerCensusError("cannot enumerate Linux processes for listener ownership") from error
    require_budget()
    pids: set[int] = set()
    owned_inodes: set[str] = set()
    degradations: list[ListenerDegradation] = []

    def budgeted_paths(entries: tuple[Path, ...]) -> Iterator[Path]:
        for entry in entries:
            require_budget()
            yield entry
            # The final item gets a post-operation check too. Checking only before each item lets
            # the last read, fallback, denial, or race cross the deadline and still return success.
            require_budget()

    for process_dir in budgeted_paths(process_dirs):
        if not process_dir.name.isdigit():
            continue
        # The published bound is the caller's, not this walk's private business. A shared host
        # carries hundreds of processes and this loop used to run unbounded while
        # `timeout_seconds` was accepted and silently ignored.
        try:
            process_uid = process_uid_reader(process_dir)
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as error:
            if _listener_scan_denial(error):
                degradations.append(_listener_degradation(process_dir, "process uid", error, None))
                continue
            raise ListenerCensusError(
                f"cannot identify owner UID for Linux process {process_dir.name}"
            ) from error
        # Recorded for the human reading a degradation, never consulted by a decision.
        try:
            entries = tuple((process_dir / "fd").iterdir())
        except _LISTENER_SCAN_RACE:
            continue
        except OSError as error:
            if _listener_scan_denial(error):
                degradations.append(
                    _listener_degradation(process_dir, "fd directory", error, process_uid)
                )
                continue
            raise ListenerCensusError(
                f"cannot enumerate file descriptors for Linux process {process_dir.name}"
            ) from error
        require_budget()
        for entry in budgeted_paths(entries):
            try:
                target = readlink(entry)
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError as error:
                # The inode classifiers exist to survive a DENIED symlink. Any other failure -
                # a non-permission errno, or none at all - is not an access boundary and stays
                # fatal here rather than being lost behind a classifier that happens to answer.
                if not _listener_scan_denial(error):
                    raise ListenerCensusError(
                        f"cannot inspect Linux file descriptor {entry}"
                    ) from error
                try:
                    fallback_inode = fd_stat_inode_reader(entry)
                except (FileNotFoundError, ProcessLookupError):
                    continue
                except OSError as stat_error:
                    if not _listener_scan_denial(stat_error):
                        raise ListenerCensusError(
                            f"cannot inspect Linux file descriptor {entry}"
                        ) from stat_error
                    try:
                        fallback_inode = fdinfo_inode_reader(entry)
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    except OSError as fallback_error:
                        # Both fallbacks failed. Keep BOTH causes: discarding the stat failure
                        # once hid which of the two classifiers the kernel actually refused.
                        if _listener_scan_denial(fallback_error) and _listener_scan_denial(stat_error):
                            degradations.append(
                                _listener_degradation(process_dir, f"fd {entry.name} readlink", error, process_uid)
                            )
                            degradations.append(
                                _listener_degradation(process_dir, f"fd {entry.name} stat", stat_error, process_uid)
                            )
                            degradations.append(
                                _listener_degradation(process_dir, f"fd {entry.name} fdinfo", fallback_error, process_uid)
                            )
                            continue
                        raise ListenerCensusError(
                            f"cannot inspect Linux file descriptor {entry}"
                        ) from fallback_error
                matched_inode = None if fallback_inode is None else str(fallback_inode)
            else:
                match = _SOCKET_TARGET_RE.fullmatch(target)
                matched_inode = None if match is None else match.group(1)
            if matched_inode not in inode_uids:
                continue
            pids.add(int(process_dir.name))
            owned_inodes.add(matched_inode)
    missing = sorted(set(inode_uids) - owned_inodes, key=int)
    for inode in missing:
        degradations.append(ListenerDegradation(
            pid=None,
            stage=f"unattributed listening inode {inode}",
            errno_value=None,
            detail=f"no visible process holds listening inode {inode} on port {port}",
            scope=SCOPE_TARGET,
        ))
    census = ListenerCensus(pids=tuple(sorted(pids)), degradations=tuple(degradations))
    if missing:
        # Fail closed in EVERY mode: this is the target itself, not an unrelated neighbour.
        raise ListenerCensusDegraded(
            port,
            census,
            f"cannot identify owner PID for listening socket inode(s) {missing}; "
            f"{census.degradation_summary()}",
        )
    return census


def _run_listener_command(
    command: list[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ListenerCensusError(f"listener scanner execution failed: {command[0]}") from error


def _lsof_listener_pids(
    port: int,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    timeout_seconds: float,
) -> list[int]:
    command = ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-F", "pcR"]
    completed = _run_listener_command(command, runner=runner, timeout_seconds=timeout_seconds)
    if completed.stderr.strip() or completed.returncode not in {0, 1}:
        raise ListenerCensusError(f"lsof listener census failed with exit {completed.returncode}: {completed.stderr.strip()}")
    if completed.returncode == 1:
        if completed.stdout.strip():
            raise ListenerCensusError("lsof listener census returned partial output with exit 1")
        return []
    pids, _parents, _commands = parse_lsof_listener_snapshot(completed.stdout)
    return pids


def listener_census(
    port: int,
    *,
    platform_name: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    proc_root: Path = Path("/proc"),
    readlink: Callable[[os.PathLike[str]], str] = os.readlink,
    timeout_seconds: float = LISTENER_PROBE_TIMEOUT_SECONDS,
) -> ListenerCensus:
    """The ONE listener backend entry point. Every platform returns the same typed result.

    On Linux this always walks /proc, even where `ss` or `lsof` is installed. Those tools were
    previously trusted to answer completely whenever they answered at all, which is not something
    either can promise: `ss -ltnp` prints an owner only for sockets the caller may inspect, so a
    snapshot containing one PID is evidence that one holder exists, never that no other holder was
    hidden. Accepting it as complete reintroduced the exact false-uniqueness the /proc owner exists
    to prevent, and it did so on the developer hosts that DO have `ss` while the gate container,
    which has neither tool, was the only place the safe path ran.

    A non-Linux host has no /proc to walk, so it uses `lsof` and carries an explicit global
    visibility degradation. Default mode may name one visible operational PID; strict whole-host
    uniqueness refuses because the backend cannot prove another holder was absent.
    """

    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError(f"invalid TCP port: {port!r}")
    system = platform_name or platform.system()
    if system == "Linux":
        return proc_listener_census(
            port, proc_root=proc_root, readlink=readlink, timeout_seconds=timeout_seconds
        )
    if not which("lsof"):
        raise ListenerCensusError(f"lsof is required for listener census on {system}")
    pids = _lsof_listener_pids(port, runner=runner, timeout_seconds=timeout_seconds)
    return ListenerCensus(pids=tuple(pids), degradations=(_backend_visibility_degradation(system),))


def unique_listener_pid(
    port: int,
    *,
    strict: bool = False,
    platform_name: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    proc_root: Path = Path("/proc"),
    readlink: Callable[[os.PathLike[str]], str] = os.readlink,
    timeout_seconds: float = LISTENER_PROBE_TIMEOUT_SECONDS,
) -> int:
    """Require exactly one raw owner from the shared listener census."""

    census = listener_census(
        port,
        platform_name=platform_name,
        runner=runner,
        which=which,
        proc_root=proc_root,
        readlink=readlink,
        timeout_seconds=timeout_seconds,
    )
    return require_unique_listener_pid(port, census, strict=strict)


def require_unique_listener_pid(port: int, census: ListenerCensus, *, strict: bool = False) -> int:
    """The ONE exact-one decision owner. It accepts a census, never a bare list.

    Taking `ListenerCensus` rather than `list[int]` is the point: a caller cannot reach this gate
    without carrying the degradation records, so an incomplete scan cannot be laundered into a
    uniqueness claim by passing its visible PIDs alone.

    Two modes, and the caller picks one.

    DEFAULT is target-scoped operational identity: exactly one visible process owns every listening
    inode for this port. It tolerates GLOBAL records that cannot be attributed to this target,
    because a shared Linux host always has them - one measurement found 557 unreadable of 866 live
    processes - and refusing on them makes identity impossible. It refuses on any TARGET record.

    This mode does NOT prove that an unreadable co-holder cannot share an already mapped inode. It
    is not a whole-host assertion and must not be described as one.

    STRICT is the whole-host assertion: it refuses on any degradation at all, including global
    visibility it could not attribute. Use it where the question is "could anything else hold this",
    and accept that it cannot succeed on a shared host.
    """

    if not isinstance(census, ListenerCensus):
        raise TypeError(
            "require_unique_listener_pid needs a ListenerCensus so degradation cannot be dropped; "
            f"got {type(census).__name__}"
        )
    if census.target_degraded:
        raise ListenerCensusDegraded(port, census)
    if strict and census.degraded:
        raise ListenerCensusDegraded(port, census)
    pids = list(census.pids)
    if len(pids) != 1:
        raise RuntimeError(f"port {port} must have exactly one listener; found {pids or 'none'}")
    return pids[0]


def main(argv: list[str] | None = None) -> int:
    """Print the listener census for the startup shell boundary.

    Default mode, matching every other production caller: this boundary needs to name the process
    serving a port on a shared host, not to assert whole-host visibility. `--strict` is available
    for a caller that genuinely wants the stronger question and accepts that it cannot be answered
    where unrelated processes are unreadable.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", type=int)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="refuse when ANY host process is unreadable, not only when this port is unproven",
    )
    args = parser.parse_args(argv)
    try:
        census = listener_census(args.port)
    except (ListenerCensusError, ValueError) as error:
        parser.exit(2, f"listener census failed: {error}\n")
    if census.target_degraded or (args.strict and census.degraded):
        parser.exit(2, f"listener census is degraded: {census.degradation_summary()}\n")
    for pid in census.pids:
        print(pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
