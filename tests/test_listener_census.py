# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from pathlib import Path
import os
import re
import subprocess

import pytest

from tools import system_status_latency_probe
from tools import yostats_active_browser_window
from yolomux_lib import live_browser_soak
from yolomux_lib.infra import listener_census


SS_LISTENER = (
    "State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
    "LISTEN 0 64 0.0.0.0:19771 0.0.0.0:* users:((\"python3\",pid=3364478,fd=6))\n"
)


def completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_linux_listener_census_prefers_ss_and_dedupes_fds(tmp_path):
    calls = []
    dual_stack = SS_LISTENER + (
        "LISTEN 0 64 [::]:19771 [::]:* users:((\"python3\",pid=3364478,fd=7),"
        "(\"python3\",pid=3364478,fd=8))\n"
    )

    def run(command, **kwargs):
        calls.append((command, kwargs["timeout"]))
        return completed(command, stdout=dual_stack)

    assert listener_census.listener_pids(
        19771,
        platform_name="Linux",
        runner=run,
        which=lambda name: "/usr/bin/ss" if name == "ss" else None,
        proc_root=tmp_path,
    ) == [3364478]
    assert calls == [
        (["ss", "-ltnp", "sport = :19771"], listener_census.LISTENER_PROBE_TIMEOUT_SECONDS)
    ]


def test_linux_ss_parser_preserves_zero_and_multiple_owners():
    assert listener_census.parse_ss_listener_pids("State Recv-Q Send-Q Local Address:Port\n") == []
    output = SS_LISTENER + (
        "LISTEN 0 64 [::]:19771 [::]:* users:((\"python3\",pid=4242424,fd=6))\n"
    )
    assert listener_census.parse_ss_listener_pids(output) == [3364478, 4242424]


@pytest.mark.parametrize(
    ("pids", "parents", "commands", "expected"),
    (
        ([101, 202], {101: 1, 202: 101}, {101: "server", 202: "server"}, [101]),
        ([101, 202], {101: 1, 202: 101}, {101: "server", 202: "worker"}, [101, 202]),
        ([101, 202], {101: 1, 202: 1}, {101: "server", 202: "server"}, [101, 202]),
        ([101, 202], {101: 1, 202: 101}, {101: "", 202: ""}, [101, 202]),
        ([101, 303], {101: 1, 202: 101, 303: 202}, {101: "server", 303: "server"}, [101]),
    ),
)
def test_canonical_listener_pids_collapses_only_identified_fork_before_exec_clones(
    pids, parents, commands, expected
):
    assert listener_census.canonical_listener_pids(
        pids, parent_reader=parents.__getitem__, command_reader=commands.__getitem__
    ) == expected


@pytest.mark.parametrize(("pids", "expected"), (([], "none"), ([7, 8], "[7, 8]")))
def test_require_unique_listener_pid_gates_one_captured_snapshot(pids, expected):
    with pytest.raises(
        RuntimeError,
        match=re.escape(f"port 41771 must have exactly one listener; found {expected}"),
    ):
        listener_census.require_unique_listener_pid(41771, pids)


def test_unique_listener_pid_applies_the_pure_gate_to_raw_census(monkeypatch):
    monkeypatch.setattr(listener_census, "listener_pids", lambda *_args, **_kwargs: [7])

    assert listener_census.unique_listener_pid(41771) == 7


def test_linux_ss_rejects_listener_without_owner_pid():
    with pytest.raises(listener_census.ListenerCensusError, match="without an identifiable owner"):
        listener_census.parse_ss_listener_pids(
            "LISTEN 0 64 0.0.0.0:19771 0.0.0.0:*\n"
        )


@pytest.mark.parametrize(
    "runner, match",
    (
        (lambda command, **_kwargs: completed(command, returncode=2, stderr="denied"), "exit 2"),
        (lambda command, **_kwargs: completed(command, stderr="warning"), "exit 0"),
        (lambda _command, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("ss")), "execution failed"),
    ),
)
def test_linux_ss_scanner_failure_is_not_empty_ownership(runner, match):
    with pytest.raises(listener_census.ListenerCensusError, match=match):
        listener_census.listener_pids(
            19771,
            platform_name="Linux",
            runner=runner,
            which=lambda name: "/usr/bin/ss" if name == "ss" else None,
        )


def test_linux_listener_census_falls_back_to_lsof_before_proc(tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs["timeout"]))
        return completed(command, stdout="p3364478\nR1\ncpython3.12\n")

    assert listener_census.listener_pids(
        19771,
        platform_name="Linux",
        runner=run,
        which=lambda name: "/usr/bin/lsof" if name == "lsof" else None,
        proc_root=tmp_path,
        timeout_seconds=2.0,
    ) == [3364478]
    assert calls == [
        (["lsof", "-nP", "-iTCP:19771", "-sTCP:LISTEN", "-F", "pcR"], 2.0)
    ]


def write_proc_listener(
    proc_root: Path,
    *,
    pid: int = 123,
    inode: str = "4242",
    uid: int | None = None,
) -> Path:
    (proc_root / "net").mkdir(parents=True)
    owner_uid = os.getuid() if uid is None else uid
    row = f"0: 0100007F:C350 00000000:0000 0A 00000000:00000000 00:00000000 00000000 {owner_uid} 0 {inode} 1\n"
    (proc_root / "net" / "tcp").write_text("header\n" + row, encoding="utf-8")
    (proc_root / "net" / "tcp6").write_text("header\n", encoding="utf-8")
    fd_dir = proc_root / str(pid) / "fd"
    fd_dir.mkdir(parents=True)
    (fd_dir / "10").touch()
    listener = fd_dir / "11"
    listener.touch()
    return listener


def test_proc_fallback_skips_one_vanished_fd_and_keeps_listener_owner(tmp_path):
    listener = write_proc_listener(tmp_path)

    def readlink(entry):
        if entry.name == "10":
            raise FileNotFoundError(entry)
        assert entry == listener
        return "socket:[4242]"

    assert listener_census.listener_pids(
        50000,
        platform_name="Linux",
        which=lambda _name: None,
        proc_root=tmp_path,
        readlink=readlink,
    ) == [123]


def test_proc_fallback_skips_one_vanished_process_and_keeps_mapped_owner(tmp_path, monkeypatch):
    write_proc_listener(tmp_path)
    surviving_fd_dir = tmp_path / "456" / "fd"
    surviving_fd_dir.mkdir(parents=True)
    surviving_listener = surviving_fd_dir / "12"
    surviving_listener.touch()
    vanished_fd_dir = tmp_path / "123" / "fd"
    original_iterdir = Path.iterdir

    def iterdir(path):
        if path == vanished_fd_dir:
            raise FileNotFoundError(path)
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)

    assert listener_census.listener_pids(
        50000,
        platform_name="Linux",
        which=lambda _name: None,
        proc_root=tmp_path,
        readlink=lambda entry: "socket:[4242]" if entry == surviving_listener else "",
    ) == [456]


def test_proc_fallback_skips_vanished_process_owner_uid_and_keeps_mapped_owner(tmp_path):
    write_proc_listener(tmp_path)
    surviving_fd_dir = tmp_path / "456" / "fd"
    surviving_fd_dir.mkdir(parents=True)
    surviving_listener = surviving_fd_dir / "12"
    surviving_listener.touch()

    def process_uid(process_dir):
        if process_dir.name == "123":
            raise FileNotFoundError(process_dir)
        return os.getuid()

    assert listener_census.proc_listener_pids(
        50000,
        proc_root=tmp_path,
        readlink=lambda entry: "socket:[4242]" if entry == surviving_listener else "",
        process_uid_reader=process_uid,
    ) == [456]


def test_proc_fallback_rejects_stable_process_owner_uid_read_error(tmp_path):
    write_proc_listener(tmp_path)

    with pytest.raises(listener_census.ListenerCensusError, match="cannot identify owner UID"):
        listener_census.proc_listener_pids(
            50000,
            proc_root=tmp_path,
            process_uid_reader=lambda process_dir: (_ for _ in ()).throw(
                PermissionError(process_dir)
            ),
        )


@pytest.mark.parametrize("classifier", ("stat", "fdinfo"))
def test_proc_fallback_classifies_unreadable_noncandidate_descriptor_as_nonowner(
    tmp_path,
    classifier,
):
    listener = write_proc_listener(tmp_path)
    blocked_fd_dir = tmp_path / "456" / "fd"
    blocked_fd_dir.mkdir(parents=True)
    blocked_fd = blocked_fd_dir / "0"
    blocked_fd.touch()

    def readlink(entry):
        if entry == blocked_fd:
            raise PermissionError(entry)
        return "socket:[4242]" if entry == listener else ""

    def stat_inode(entry):
        if classifier == "fdinfo":
            raise PermissionError(entry)
        return listener_census._proc_fd_stat_inode(entry)

    if classifier == "fdinfo":
        fdinfo_dir = tmp_path / "456" / "fdinfo"
        fdinfo_dir.mkdir()
        (fdinfo_dir / "0").write_text("pos:\t0\nino:\t9999\n", encoding="utf-8")

    assert listener_census.proc_listener_pids(
        50000,
        proc_root=tmp_path,
        readlink=readlink,
        process_uid_reader=lambda process_dir: (
            os.getuid() + 1 if process_dir.name == "456" else os.getuid()
        ),
        fd_stat_inode_reader=stat_inode,
    ) == [123]


def test_proc_fallback_rejects_unreadable_fd_directory_owned_by_another_uid(
    tmp_path,
    monkeypatch,
):
    listener = write_proc_listener(tmp_path)
    blocked_fd_dir = tmp_path / "456" / "fd"
    blocked_fd_dir.mkdir(parents=True)
    original_iterdir = Path.iterdir

    def iterdir(path):
        if path == blocked_fd_dir:
            raise PermissionError(path)
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)

    with pytest.raises(listener_census.ListenerCensusError, match="cannot enumerate file descriptors"):
        listener_census.proc_listener_pids(
            50000,
            proc_root=tmp_path,
            readlink=lambda entry: "socket:[4242]" if entry == listener else "",
            process_uid_reader=lambda process_dir: (
                os.getuid() + 1 if process_dir.name == "456" else os.getuid()
            ),
        )


def test_proc_fallback_rejects_unreadable_process_fd_directory(tmp_path, monkeypatch):
    write_proc_listener(tmp_path)
    blocked_fd_dir = tmp_path / "123" / "fd"
    original_iterdir = Path.iterdir

    def iterdir(path):
        if path == blocked_fd_dir:
            raise PermissionError(path)
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)

    with pytest.raises(listener_census.ListenerCensusError, match="cannot enumerate file descriptors"):
        listener_census.listener_pids(
            50000,
            platform_name="Linux",
            which=lambda _name: None,
            proc_root=tmp_path,
        )


def test_proc_fallback_rejects_stable_fd_read_error(tmp_path):
    write_proc_listener(tmp_path)

    with pytest.raises(listener_census.ListenerCensusError, match="cannot inspect Linux file descriptor"):
        listener_census.listener_pids(
            50000,
            platform_name="Linux",
            which=lambda _name: None,
            proc_root=tmp_path,
            readlink=lambda _entry: (_ for _ in ()).throw(OSError("stable I/O failure")),
        )


def test_proc_fallback_rejects_unmapped_listener_inode(tmp_path):
    write_proc_listener(tmp_path)

    with pytest.raises(listener_census.ListenerCensusError, match="cannot identify owner PID"):
        listener_census.listener_pids(
            50000,
            platform_name="Linux",
            which=lambda _name: None,
            proc_root=tmp_path,
            readlink=lambda _entry: "socket:[9999]",
        )


def test_proc_fallback_preserves_multiple_same_uid_listener_owners(tmp_path):
    first_listener = write_proc_listener(tmp_path)
    second_fd_dir = tmp_path / "456" / "fd"
    second_fd_dir.mkdir(parents=True)
    second_listener = second_fd_dir / "12"
    second_listener.touch()

    pids = listener_census.proc_listener_pids(
        50000,
        proc_root=tmp_path,
        readlink=lambda entry: (
            "socket:[4242]" if entry in {first_listener, second_listener} else ""
        ),
    )

    assert pids == [123, 456]
    with pytest.raises(RuntimeError, match=r"exactly one listener; found \[123, 456\]"):
        listener_census.require_unique_listener_pid(50000, pids)


def test_proc_fallback_classifies_unreadable_cross_uid_listener_owner(tmp_path):
    first_listener = write_proc_listener(tmp_path)
    second_fd_dir = tmp_path / "456" / "fd"
    second_fd_dir.mkdir(parents=True)
    second_listener = second_fd_dir / "12"
    second_listener.touch()

    def readlink(entry):
        if entry == second_listener:
            raise PermissionError(entry)
        return "socket:[4242]" if entry == first_listener else ""

    pids = listener_census.proc_listener_pids(
        50000,
        proc_root=tmp_path,
        readlink=readlink,
        process_uid_reader=lambda process_dir: (
            os.getuid() + 1 if process_dir.name == "456" else os.getuid()
        ),
        fd_stat_inode_reader=lambda _entry: 4242,
    )

    assert pids == [123, 456]
    with pytest.raises(RuntimeError, match=r"exactly one listener; found \[123, 456\]"):
        listener_census.require_unique_listener_pid(50000, pids)


def test_proc_fallback_rejects_noncandidate_descriptor_when_all_classifiers_fail(tmp_path):
    listener = write_proc_listener(tmp_path)
    blocked_fd_dir = tmp_path / "456" / "fd"
    blocked_fd_dir.mkdir(parents=True)
    blocked_fd = blocked_fd_dir / "0"
    blocked_fd.touch()

    def readlink(entry):
        if entry == blocked_fd:
            raise PermissionError(entry)
        return "socket:[4242]" if entry == listener else ""

    with pytest.raises(listener_census.ListenerCensusError, match="cannot inspect Linux file descriptor"):
        listener_census.proc_listener_pids(
            50000,
            proc_root=tmp_path,
            readlink=readlink,
            process_uid_reader=lambda process_dir: (
                os.getuid() + 1 if process_dir.name == "456" else os.getuid()
            ),
            fd_stat_inode_reader=lambda entry: (_ for _ in ()).throw(
                PermissionError(entry)
            ),
            fdinfo_inode_reader=lambda entry: (_ for _ in ()).throw(
                PermissionError(entry)
            ),
        )


@pytest.mark.parametrize("readable_tables", ((), ("tcp",), ("tcp6",)))
def test_proc_fallback_rejects_partial_or_absent_listener_tables(tmp_path, readable_tables):
    (tmp_path / "net").mkdir()
    for table in readable_tables:
        (tmp_path / "net" / table).write_text("header\n", encoding="utf-8")

    with pytest.raises(listener_census.ListenerCensusError, match="cannot read Linux TCP"):
        listener_census.listener_pids(
            50000,
            platform_name="Linux",
            which=lambda _name: None,
            proc_root=tmp_path,
        )


def test_darwin_lsof_snapshot_preserves_raw_parent_and_child_owners():
    calls = []
    snapshot = "p101\nR1\ncpython3.12\np202\nR101\ncpython3.12\n"

    def run(command, **kwargs):
        calls.append((command, kwargs["timeout"]))
        return completed(command, stdout=snapshot)

    assert listener_census.listener_pids(
        49152,
        platform_name="Darwin",
        runner=run,
        which=lambda name: "/usr/bin/lsof" if name == "lsof" else None,
    ) == [101, 202]
    assert calls == [
        (
            ["lsof", "-nP", "-iTCP:49152", "-sTCP:LISTEN", "-F", "pcR"],
            listener_census.LISTENER_PROBE_TIMEOUT_SECONDS,
        )
    ]


def test_darwin_lsof_no_match_is_empty_but_scanner_error_is_not():
    which = lambda name: "/usr/bin/lsof" if name == "lsof" else None
    assert listener_census.listener_pids(
        49152,
        platform_name="Darwin",
        runner=lambda command, **_kwargs: completed(command, returncode=1),
        which=which,
    ) == []
    with pytest.raises(listener_census.ListenerCensusError, match="exit 1"):
        listener_census.listener_pids(
            49152,
            platform_name="Darwin",
            runner=lambda command, **_kwargs: completed(command, returncode=1, stderr="denied"),
            which=which,
        )
    with pytest.raises(listener_census.ListenerCensusError, match="partial output"):
        listener_census.listener_pids(
            49152,
            platform_name="Darwin",
            runner=lambda command, **_kwargs: completed(command, returncode=1, stdout="p123\n"),
            which=which,
        )
    with pytest.raises(listener_census.ListenerCensusError, match="exit 0"):
        listener_census.listener_pids(
            49152,
            platform_name="Darwin",
            runner=lambda command, **_kwargs: completed(command, stderr="warning"),
            which=which,
        )


@pytest.mark.parametrize(
    "output, match",
    (
        ("", "no ownership records"),
        ("cignored\n", "invalid command field"),
        ("pbad\n", "invalid PID field"),
        ("p123\nR1\n", "incomplete owner record"),
        ("p123\nR1\ncpython\nXunknown\n", "unknown ownership field"),
        ("p123\nR1\ncpython\np123\n", "repeated PID"),
    ),
)
def test_lsof_exit_zero_rejects_malformed_ownership_records(output, match):
    with pytest.raises(listener_census.ListenerCensusError, match=match):
        listener_census.listener_pids(
            49152,
            platform_name="Darwin",
            runner=lambda command, **_kwargs: completed(command, stdout=output),
            which=lambda name: "/usr/bin/lsof" if name == "lsof" else None,
        )


def test_all_python_listener_census_consumers_import_the_shared_owner():
    assert system_status_latency_probe.listener_pids is listener_census.listener_pids
    assert system_status_latency_probe.canonical_listener_pids is listener_census.canonical_listener_pids
    assert system_status_latency_probe.require_unique_listener_pid is listener_census.require_unique_listener_pid
    assert live_browser_soak.unique_listener_pid is listener_census.unique_listener_pid
    assert yostats_active_browser_window.unique_listener_pid is listener_census.unique_listener_pid
