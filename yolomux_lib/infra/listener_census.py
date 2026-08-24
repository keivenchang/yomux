# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Strict, shared listener ownership census for local YOLOmux tools."""

from __future__ import annotations

from collections.abc import Callable
import argparse
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess


LISTENER_PROBE_TIMEOUT_SECONDS = 3
_SOCKET_TARGET_RE = re.compile(r"^socket:\[(\d+)\]$")
_SS_PID_RE = re.compile(r"\bpid=(\d+)")


class ListenerCensusError(RuntimeError):
    """The platform listener scanner could not prove the complete owner set."""


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


def parse_ss_listener_pids(output: str) -> list[int]:
    """Parse one port-filtered ss snapshot and reject unidentified listener rows."""

    pids: set[int] = set()
    for line in output.splitlines():
        if not line.lstrip().startswith("LISTEN"):
            continue
        matches = _SS_PID_RE.findall(line)
        if not matches:
            raise ListenerCensusError("ss reported a listener without an identifiable owner PID")
        pids.update(int(match) for match in matches)
    return sorted(pids)


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


def proc_listener_pids(
    port: int,
    *,
    proc_root: Path = Path("/proc"),
    readlink: Callable[[os.PathLike[str]], str] = os.readlink,
    process_uid_reader: Callable[[Path], int] = _proc_process_uid,
    fd_stat_inode_reader: Callable[[Path], int | None] = _proc_fd_stat_inode,
    fdinfo_inode_reader: Callable[[Path], int] = _proc_fdinfo_inode,
) -> list[int]:
    """Map every listening socket inode to a PID without aborting on one vanished FD."""

    inode_uids = _proc_listener_inode_uids(port, proc_root)
    if not inode_uids:
        return []
    candidate_uids = set(inode_uids.values())
    try:
        process_dirs = tuple(proc_root.iterdir())
    except OSError as error:
        raise ListenerCensusError("cannot enumerate Linux processes for listener ownership") from error
    pids: set[int] = set()
    owned_inodes: set[str] = set()
    for process_dir in process_dirs:
        if not process_dir.name.isdigit():
            continue
        try:
            process_uid = process_uid_reader(process_dir)
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as error:
            raise ListenerCensusError(
                f"cannot identify owner UID for Linux process {process_dir.name}"
            ) from error
        # The TCP-table UID is the socket creator, but another UID can inherit or receive the FD.
        # Scan every readable process; the UID only decides whether a denied symlink may use the
        # inode classifiers or must remain fail-closed at the first error.
        candidate_uid = process_uid in candidate_uids
        try:
            entries = tuple((process_dir / "fd").iterdir())
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as error:
            raise ListenerCensusError(
                f"cannot enumerate file descriptors for Linux process {process_dir.name}"
            ) from error
        for entry in entries:
            try:
                target = readlink(entry)
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError as error:
                if candidate_uid:
                    raise ListenerCensusError(
                        f"cannot inspect Linux file descriptor {entry}"
                    ) from error
                try:
                    fallback_inode = fd_stat_inode_reader(entry)
                except (FileNotFoundError, ProcessLookupError):
                    continue
                except OSError:
                    try:
                        fallback_inode = fdinfo_inode_reader(entry)
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    except OSError as fallback_error:
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
    if missing:
        raise ListenerCensusError(f"cannot identify owner PID for listening socket inode(s) {missing}")
    return sorted(pids)


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


def listener_pids(
    port: int,
    *,
    platform_name: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    proc_root: Path = Path("/proc"),
    readlink: Callable[[os.PathLike[str]], str] = os.readlink,
    timeout_seconds: float = LISTENER_PROBE_TIMEOUT_SECONDS,
) -> list[int]:
    """Return the complete raw listener-owner set or raise when it is unprovable."""

    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError(f"invalid TCP port: {port!r}")
    system = platform_name or platform.system()
    if system == "Linux":
        if which("ss"):
            command = ["ss", "-ltnp", f"sport = :{port}"]
            completed = _run_listener_command(command, runner=runner, timeout_seconds=timeout_seconds)
            if completed.returncode != 0 or completed.stderr.strip():
                raise ListenerCensusError(f"ss listener census failed with exit {completed.returncode}: {completed.stderr.strip()}")
            return parse_ss_listener_pids(completed.stdout)
        if which("lsof"):
            return _lsof_listener_pids(port, runner=runner, timeout_seconds=timeout_seconds)
        return proc_listener_pids(port, proc_root=proc_root, readlink=readlink)
    if not which("lsof"):
        raise ListenerCensusError(f"lsof is required for listener census on {system}")
    return _lsof_listener_pids(port, runner=runner, timeout_seconds=timeout_seconds)


def unique_listener_pid(
    port: int,
    *,
    platform_name: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    proc_root: Path = Path("/proc"),
    readlink: Callable[[os.PathLike[str]], str] = os.readlink,
    timeout_seconds: float = LISTENER_PROBE_TIMEOUT_SECONDS,
) -> int:
    """Require exactly one raw owner from the shared listener census."""

    pids = listener_pids(
        port,
        platform_name=platform_name,
        runner=runner,
        which=which,
        proc_root=proc_root,
        readlink=readlink,
        timeout_seconds=timeout_seconds,
    )
    return require_unique_listener_pid(port, pids)


def require_unique_listener_pid(port: int, pids: list[int]) -> int:
    """Apply the exact-one gate to one already captured listener snapshot."""

    if len(pids) != 1:
        raise RuntimeError(f"port {port} must have exactly one listener; found {pids or 'none'}")
    return pids[0]


def main(argv: list[str] | None = None) -> int:
    """Print the strict raw listener census for the startup shell boundary."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", type=int)
    args = parser.parse_args(argv)
    try:
        pids = listener_pids(args.port)
    except (ListenerCensusError, ValueError) as error:
        parser.exit(2, f"listener census failed: {error}\n")
    for pid in pids:
        print(pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
