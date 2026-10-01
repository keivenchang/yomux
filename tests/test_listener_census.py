# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from pathlib import Path
import errno
import os
import re
import subprocess

import pytest

from tools import system_status_latency_probe
from tools import yostats_active_browser_window
from yomux_lib import live_browser_soak
from yomux_lib.infra import listener_census


SS_LISTENER = (
    "State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
    "LISTEN 0 64 0.0.0.0:19771 0.0.0.0:* users:((\"python3\",pid=3364478,fd=6))\n"
)


def visible_pids(*args, **kwargs) -> list[int]:
    """Test-local: the visible PID set from the typed census.

    The product exposes no raw-list wrapper: every production caller needs the degradation
    records, so reading `.pids` alone is a test-only convenience and lives here.
    """

    return list(listener_census.listener_census(*args, **kwargs).pids)


def proc_visible_pids(*args, **kwargs) -> list[int]:
    """Test-local: the /proc backend's visible PID set from the typed census."""

    return list(listener_census.proc_listener_census(*args, **kwargs).pids)


def completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


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
        listener_census.require_unique_listener_pid(41771, listener_census.ListenerCensus(pids=tuple(pids)))


def test_unique_listener_pid_applies_the_pure_gate_to_raw_census(monkeypatch):
    monkeypatch.setattr(
        listener_census, "listener_census",
        lambda *_args, **_kwargs: listener_census.ListenerCensus(pids=(7,)),
    )

    assert listener_census.unique_listener_pid(41771) == 7


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

    assert visible_pids(
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

    assert visible_pids(
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

    assert proc_visible_pids(
        50000,
        proc_root=tmp_path,
        readlink=lambda entry: "socket:[4242]" if entry == surviving_listener else "",
        process_uid_reader=process_uid,
    ) == [456]


def test_proc_fallback_records_a_denied_process_uid_read_as_degradation(tmp_path):
    """A process we cannot even stat is unreadable, so it degrades rather than aborting."""

    write_proc_listener(tmp_path)
    with pytest.raises(listener_census.ListenerCensusError, match="cannot identify owner PID") as raised:
        listener_census.proc_listener_census(
            50000,
            proc_root=tmp_path,
            process_uid_reader=lambda process_dir: (_ for _ in ()).throw(
                PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(process_dir))
            ),
        )
    assert "process uid" in str(raised.value) and "errno 13 EACCES" in str(raised.value)


def test_proc_fallback_records_a_denied_uid_read_as_degradation_when_another_pid_owns_the_inode(tmp_path):
    """A second, readable owner attributes the inode, so the scan completes - but degraded."""

    listener = write_proc_listener(tmp_path)
    (tmp_path / "456" / "fd").mkdir(parents=True)

    def uid_reader(process_dir):
        if process_dir.name == "456":
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(process_dir))
        return os.getuid()

    census = listener_census.proc_listener_census(
        50000,
        proc_root=tmp_path,
        readlink=lambda entry: "socket:[4242]" if entry == listener else "",
        process_uid_reader=uid_reader,
    )
    assert census.pids == (123,)
    assert census.degraded
    assert [item.stage for item in census.degradations] == ["process uid"]
    # An unrelated process we could not stat is GLOBAL visibility: strict refuses, default does not.
    with pytest.raises(listener_census.ListenerCensusDegraded):
        listener_census.require_unique_listener_pid(50000, census, strict=True)
    assert listener_census.require_unique_listener_pid(50000, census) == 123


def test_proc_fallback_rejects_a_non_denial_process_uid_read_error(tmp_path):
    """A non-permission errno on the UID read is a broken host and stays fatal."""

    write_proc_listener(tmp_path)
    with pytest.raises(listener_census.ListenerCensusError, match="cannot identify owner UID"):
        listener_census.proc_listener_census(
            50000,
            proc_root=tmp_path,
            process_uid_reader=lambda process_dir: (_ for _ in ()).throw(
                OSError(errno.EIO, "I/O error", str(process_dir))
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
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(entry))
        return "socket:[4242]" if entry == listener else ""

    def stat_inode(entry):
        if classifier == "fdinfo":
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(entry))
        return listener_census._proc_fd_stat_inode(entry)

    if classifier == "fdinfo":
        fdinfo_dir = tmp_path / "456" / "fdinfo"
        fdinfo_dir.mkdir()
        (fdinfo_dir / "0").write_text("pos:\t0\nino:\t9999\n", encoding="utf-8")

    assert proc_visible_pids(
        50000,
        proc_root=tmp_path,
        readlink=readlink,
        process_uid_reader=lambda process_dir: (
            os.getuid() + 1 if process_dir.name == "456" else os.getuid()
        ),
        fd_stat_inode_reader=stat_inode,
    ) == [123]


def test_proc_fallback_rejects_a_denied_fd_directory_that_carries_no_errno(
    tmp_path,
    monkeypatch,
):
    """An OSError with no errno is not a PROVEN permission boundary, so it stays fatal.

    The skip rule keys on EACCES/EPERM. A refusal that never reported an errno could equally be a
    broken /proc, and ownership must fail closed whenever inaccessibility is unprovable.
    """

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
        proc_visible_pids(
            50000,
            proc_root=tmp_path,
            readlink=lambda entry: "socket:[4242]" if entry == listener else "",
            process_uid_reader=lambda process_dir: (
                os.getuid() + 1 if process_dir.name == "456" else os.getuid()
            ),
        )


def test_proc_fallback_fails_closed_when_the_only_owner_is_unreadable(tmp_path, monkeypatch):
    """The listening inode has no other claimant, so inode coverage still fails the scan closed."""

    write_proc_listener(tmp_path)
    blocked_fd_dir = tmp_path / "123" / "fd"
    original_iterdir = Path.iterdir

    def iterdir(path):
        if path == blocked_fd_dir:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)

    with pytest.raises(listener_census.ListenerCensusError, match="cannot identify owner PID") as raised:
        listener_census.listener_census(
            50000,
            platform_name="Linux",
            which=lambda _name: None,
            proc_root=tmp_path,
        )
    assert "pid 123" in str(raised.value) and "errno 13 EACCES" in str(raised.value)


def test_proc_fallback_rejects_stable_fd_read_error(tmp_path):
    write_proc_listener(tmp_path)

    with pytest.raises(listener_census.ListenerCensusError, match="cannot inspect Linux file descriptor"):
        visible_pids(
            50000,
            platform_name="Linux",
            which=lambda _name: None,
            proc_root=tmp_path,
            readlink=lambda _entry: (_ for _ in ()).throw(OSError("stable I/O failure")),
        )


def test_proc_fallback_rejects_unmapped_listener_inode(tmp_path):
    write_proc_listener(tmp_path)

    with pytest.raises(listener_census.ListenerCensusError, match="cannot identify owner PID"):
        visible_pids(
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

    pids = proc_visible_pids(
        50000,
        proc_root=tmp_path,
        readlink=lambda entry: (
            "socket:[4242]" if entry in {first_listener, second_listener} else ""
        ),
    )

    assert pids == [123, 456]
    with pytest.raises(RuntimeError, match=r"exactly one listener; found \[123, 456\]"):
        listener_census.require_unique_listener_pid(50000, listener_census.ListenerCensus(pids=tuple(pids)))


def test_proc_fallback_classifies_unreadable_cross_uid_listener_owner(tmp_path):
    first_listener = write_proc_listener(tmp_path)
    second_fd_dir = tmp_path / "456" / "fd"
    second_fd_dir.mkdir(parents=True)
    second_listener = second_fd_dir / "12"
    second_listener.touch()

    def readlink(entry):
        if entry == second_listener:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(entry))
        return "socket:[4242]" if entry == first_listener else ""

    pids = proc_visible_pids(
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
        listener_census.require_unique_listener_pid(50000, listener_census.ListenerCensus(pids=tuple(pids)))


def test_proc_fallback_rejects_noncandidate_descriptor_when_all_classifiers_fail(tmp_path):
    listener = write_proc_listener(tmp_path)
    blocked_fd_dir = tmp_path / "456" / "fd"
    blocked_fd_dir.mkdir(parents=True)
    blocked_fd = blocked_fd_dir / "0"
    blocked_fd.touch()

    def readlink(entry):
        if entry == blocked_fd:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(entry))
        return "socket:[4242]" if entry == listener else ""

    census = listener_census.proc_listener_census(
        50000,
        proc_root=tmp_path,
        readlink=readlink,
        process_uid_reader=lambda process_dir: (
            os.getuid() + 1 if process_dir.name == "456" else os.getuid()
        ),
        fd_stat_inode_reader=lambda entry: (_ for _ in ()).throw(
            PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(entry))
        ),
        fdinfo_inode_reader=lambda entry: (_ for _ in ()).throw(
            PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(entry))
        ),
    )

    # All three classifiers were refused. Every one is retained, so a reader can tell which
    # of readlink / stat / fdinfo the kernel actually rejected instead of only the last.
    assert census.pids == (123,)
    assert [item.stage for item in census.degradations] == [
        "fd 0 readlink", "fd 0 stat", "fd 0 fdinfo",
    ]
    assert {item.errno_value for item in census.degradations} == {errno.EACCES}
    assert all(item.scope == listener_census.SCOPE_GLOBAL for item in census.degradations)
    with pytest.raises(listener_census.ListenerCensusDegraded):
        listener_census.require_unique_listener_pid(50000, census, strict=True)


@pytest.mark.parametrize("readable_tables", ((), ("tcp",), ("tcp6",)))
def test_proc_fallback_rejects_partial_or_absent_listener_tables(tmp_path, readable_tables):
    (tmp_path / "net").mkdir()
    for table in readable_tables:
        (tmp_path / "net" / table).write_text("header\n", encoding="utf-8")

    with pytest.raises(listener_census.ListenerCensusError, match="cannot read Linux TCP"):
        visible_pids(
            50000,
            platform_name="Linux",
            which=lambda _name: None,
            proc_root=tmp_path,
        )


def test_darwin_lsof_snapshot_preserves_raw_parent_and_child_owners():
    calls = []
    snapshot = "p101\nR1\ncpython3.12\nf9\np202\nR101\ncpython3.12\nf10\n"

    def run(command, **kwargs):
        calls.append((command, kwargs["timeout"]))
        return completed(command, stdout=snapshot)

    assert visible_pids(
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
    assert visible_pids(
        49152,
        platform_name="Darwin",
        runner=lambda command, **_kwargs: completed(command, returncode=1),
        which=which,
    ) == []
    with pytest.raises(listener_census.ListenerCensusError, match="exit 1"):
        visible_pids(
            49152,
            platform_name="Darwin",
            runner=lambda command, **_kwargs: completed(command, returncode=1, stderr="denied"),
            which=which,
        )
    with pytest.raises(listener_census.ListenerCensusError, match="partial output"):
        visible_pids(
            49152,
            platform_name="Darwin",
            runner=lambda command, **_kwargs: completed(command, returncode=1, stdout="p123\n"),
            which=which,
        )
    with pytest.raises(listener_census.ListenerCensusError, match="exit 0"):
        visible_pids(
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
        visible_pids(
            49152,
            platform_name="Darwin",
            runner=lambda command, **_kwargs: completed(command, stdout=output),
            which=lambda name: "/usr/bin/lsof" if name == "lsof" else None,
        )


def test_all_python_listener_census_consumers_import_the_shared_owner():
    assert system_status_latency_probe.listener_census is listener_census.listener_census
    assert system_status_latency_probe.canonical_listener_pids is listener_census.canonical_listener_pids
    assert system_status_latency_probe.require_unique_listener_pid is listener_census.require_unique_listener_pid
    assert live_browser_soak.unique_listener_pid is listener_census.unique_listener_pid
    assert yostats_active_browser_window.unique_listener_pid is listener_census.unique_listener_pid


def denied(path, code=errno.EACCES):
    """One PermissionError carrying a REAL errno, as the kernel always reports it."""

    return PermissionError(code, os.strerror(code), str(path))


def blocked_fd_directory(monkeypatch, blocked, error):
    original_iterdir = Path.iterdir

    def iterdir(path):
        if path == blocked:
            raise error
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)


def shared_inode_tree(tmp_path, monkeypatch, *, denied_uid):
    """pid 123 readable and pid 456 denied, BOTH genuinely holding listening inode 4242.

    This is the shape that made the previous correction unsafe: the inode is attributed by the
    readable owner, so inode coverage is satisfied and the visible set is a true SUBSET that
    happens to look unique.
    """

    listener = write_proc_listener(tmp_path)
    blocked = tmp_path / "456" / "fd"
    blocked.mkdir(parents=True)
    (blocked / "12").touch()
    original_iterdir = Path.iterdir

    def iterdir(path):
        if path == blocked:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    return listener_census.proc_listener_census(
        50000,
        proc_root=tmp_path,
        readlink=lambda entry: "socket:[4242]" if entry == listener else "",
        process_uid_reader=lambda process_dir: (
            denied_uid if process_dir.name == "456" else os.getuid()
        ),
    )


@pytest.mark.parametrize(
    "denied_uid, label",
    ((os.getuid(), "same uid, non-dumpable"), (os.getuid() + 1, "different uid")),
)
def test_a_denied_process_sharing_the_inode_never_yields_false_uniqueness(
    tmp_path, monkeypatch, denied_uid, label,
):
    """The headline invariant: a subset that looks unique must refuse every uniqueness API.

    UID does not change the answer. `/proc/<pid>/fd` becomes root-owned and mode 500 once a
    same-UID process clears its dumpable flag, so same-UID inaccessibility is real and a UID
    match proves neither inspectability nor non-ownership.
    """

    census = shared_inode_tree(tmp_path, monkeypatch, denied_uid=denied_uid)

    # Raw observability still sees the real, visible owner and the reason it may be incomplete.
    assert census.pids == (123,), label
    assert census.degraded, label
    assert [item.pid for item in census.degradations] == [456], label
    assert census.degradations[0].stage == "fd directory"
    assert census.degradations[0].errno_value == errno.EACCES
    assert census.degradations[0].error_code == "EACCES"
    assert census.degradations[0].uid == denied_uid, "uid is diagnostic data"

    # STRICT is the whole-host assertion, and it refuses: pid 456 may hold the same inode.
    with pytest.raises(listener_census.ListenerCensusDegraded) as raised:
        listener_census.require_unique_listener_pid(50000, census, strict=True)
    assert "pid 456" in str(raised.value) and "errno 13 EACCES" in str(raised.value)
    assert raised.value.census is census

    # DEFAULT is target-scoped operational identity: inode 4242 has exactly one visible owner,
    # so it answers 123. This is NOT a claim that pid 456 cannot also hold that inode - it
    # cannot be, and the record stays on the census for any caller that needs the stronger
    # question answered.
    assert listener_census.require_unique_listener_pid(50000, census) == 123
    assert census.global_degradations and not census.target_degradations


def test_the_exact_one_gate_refuses_a_bare_list_so_degradation_cannot_be_dropped():
    """Passing `[123]` was how an incomplete scan used to become a uniqueness claim."""

    with pytest.raises(TypeError, match="needs a ListenerCensus"):
        listener_census.require_unique_listener_pid(50000, [123])


def test_unique_listener_pid_refuses_a_degraded_census(tmp_path, monkeypatch):
    """The public convenience wrapper inherits the refusal; it has no separate path."""

    census = shared_inode_tree(tmp_path, monkeypatch, denied_uid=os.getuid())
    monkeypatch.setattr(listener_census, "listener_census", lambda *_a, **_k: census)
    with pytest.raises(listener_census.ListenerCensusDegraded):
        listener_census.unique_listener_pid(50000, strict=True)
    assert listener_census.unique_listener_pid(50000) == 123


def test_no_public_raw_pid_wrapper_survives(tmp_path, monkeypatch):
    """The product exposes no list-returning listener API for a caller to reach for.

    Both raw wrappers were deleted once a repo-wide trace found no production consumer. Keeping
    them would have left a degradation-discarding path alive for the next caller to find.
    """

    for retired in ("listener_pids", "proc_listener_pids", "parse_ss_listener_pids"):
        assert not hasattr(listener_census, retired), retired


def test_a_vanished_process_is_a_race_and_never_degradation(tmp_path, monkeypatch):
    """A process that disappears mid-scan cannot own a LIVE listening socket."""

    listener = write_proc_listener(tmp_path)
    gone = tmp_path / "456" / "fd"
    gone.mkdir(parents=True)
    original_iterdir = Path.iterdir

    def iterdir(path):
        if path == gone:
            raise ProcessLookupError(errno.ESRCH, "No such process", str(path))
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    census = listener_census.proc_listener_census(
        50000,
        proc_root=tmp_path,
        readlink=lambda entry: "socket:[4242]" if entry == listener else "",
    )
    assert census.pids == (123,)
    assert not census.degraded
    assert listener_census.require_unique_listener_pid(50000, census) == 123




def test_linux_identity_walks_proc_even_where_ss_and_lsof_are_installed(tmp_path, monkeypatch):
    """Mixed visibility: the tools exist, and the /proc degradation still decides.

    `ss -ltnp` prints an owner only for sockets the caller may inspect, so a snapshot naming one
    PID proves that holder exists and never proves another was hidden. Trusting it returned the
    visible PID on exactly the developer hosts that have `ss`, while the gate container - which
    has neither tool - was the only place the safe path ran.
    """

    census = shared_inode_tree(tmp_path, monkeypatch, denied_uid=os.getuid())
    monkeypatch.setattr(listener_census, "proc_listener_census", lambda *_a, **_k: census)

    resolved = listener_census.listener_census(
        50000,
        platform_name="Linux",
        which=lambda name: f"/usr/bin/{name}",
        runner=lambda *_a, **_k: completed(["ss"], stdout=SS_LISTENER),
        proc_root=tmp_path,
    )

    assert resolved is census
    assert resolved.pids == (123,)
    assert resolved.degraded
    with pytest.raises(listener_census.ListenerCensusDegraded):
        listener_census.require_unique_listener_pid(50000, resolved, strict=True)


def test_non_linux_lsof_carries_an_explicit_backend_visibility_degradation():
    """lsof cannot prove it enumerated every holder, so uniqueness fails closed off Linux."""

    census = listener_census.listener_census(
        19771,
        platform_name="Darwin",
        which=lambda name: "/usr/bin/lsof" if name == "lsof" else None,
        runner=lambda *_a, **_k: completed(["lsof"], stdout="p3364478\nR1\ncpython3\n"),
    )

    assert census.pids == (3364478,)
    assert census.degraded
    record = census.degradations[0]
    assert record.pid is None and record.stage == "backend visibility"
    assert record.errno_value is None and "cannot prove it enumerated" in record.detail
    assert "backend visibility limit(s)" in census.degradation_summary()
    assert record.scope == listener_census.SCOPE_GLOBAL
    # Strict refuses the incomplete backend; default still provides operational identity, so a
    # supported non-Linux host is usable rather than degraded by construction.
    with pytest.raises(listener_census.ListenerCensusDegraded):
        listener_census.require_unique_listener_pid(19771, census, strict=True)
    assert listener_census.require_unique_listener_pid(19771, census) == 3364478


def test_degradation_summary_counts_processes_not_records(tmp_path):
    """One process refusing all three classifiers is ONE process for the operator, not three."""

    listener = write_proc_listener(tmp_path)
    blocked_fd_dir = tmp_path / "456" / "fd"
    blocked_fd_dir.mkdir(parents=True)
    blocked_fd = blocked_fd_dir / "0"
    blocked_fd.touch()

    def readlink(entry):
        if entry == blocked_fd:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(entry))
        return "socket:[4242]" if entry == listener else ""

    census = listener_census.proc_listener_census(
        50000,
        proc_root=tmp_path,
        readlink=readlink,
        fd_stat_inode_reader=lambda entry: (_ for _ in ()).throw(
            PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(entry))
        ),
        fdinfo_inode_reader=lambda entry: (_ for _ in ()).throw(
            PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(entry))
        ),
    )

    # Every record is retained in the detail...
    assert len(census.degradations) == 3
    assert [item.stage for item in census.degradations] == [
        "fd 0 readlink", "fd 0 stat", "fd 0 fdinfo",
    ]
    # ...while the operator-facing count is the number of processes to go look at.
    summary = census.degradation_summary()
    assert summary.startswith("1 unreadable live process(es)"), summary
    assert "3 unreadable" not in summary


def build_host_scale_tree(tmp_path, monkeypatch, *, denied_count=300):
    """A /proc tree shaped like a shared host: one readable owner, hundreds denied neighbours.

    The measured host carried 866 live processes with 557 EACCES/EPERM reads. Treating that as
    fatal identified the owner correctly and then refused it, which is what this shape pins.
    """

    listener = write_proc_listener(tmp_path)
    denied_dirs = set()
    for index in range(denied_count):
        fd_dir = tmp_path / str(1000 + index) / "fd"
        fd_dir.mkdir(parents=True)
        denied_dirs.add(fd_dir)
    original_iterdir = Path.iterdir

    def iterdir(path):
        if path in denied_dirs:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    return listener_census.proc_listener_census(
        50000,
        proc_root=tmp_path,
        readlink=lambda entry: "socket:[4242]" if entry == listener else "",
        process_uid_reader=lambda process_dir: os.getuid(),
    )


def test_host_scale_denials_do_not_block_default_identity(tmp_path, monkeypatch):
    """Hundreds of unrelated denied processes must not stop the known owner being returned."""

    census = build_host_scale_tree(tmp_path, monkeypatch)

    assert census.pids == (123,)
    assert len(census.global_degradations) == 300
    assert not census.target_degradations
    assert listener_census.require_unique_listener_pid(50000, census) == 123
    with pytest.raises(listener_census.ListenerCensusDegraded):
        listener_census.require_unique_listener_pid(50000, census, strict=True)


def test_host_scale_degradation_rendering_stays_bounded(tmp_path, monkeypatch):
    """300 records must not become a 26 KB message, and nothing may be silently dropped."""

    census = build_host_scale_tree(tmp_path, monkeypatch)
    summary = census.degradation_summary()

    assert summary.count("errno 13 EACCES") == listener_census.LISTENER_DEGRADATION_RENDER_LIMIT
    assert "300 unreadable live process(es)" in summary
    assert f"{300 - listener_census.LISTENER_DEGRADATION_RENDER_LIMIT} further record(s) omitted" in summary
    assert len(summary) < 700, len(summary)


def test_an_unattributed_target_inode_is_a_target_record_and_refuses_in_every_mode(tmp_path, monkeypatch):
    """The target itself is unproven, so no mode may answer - not even default."""

    write_proc_listener(tmp_path)
    blocked = tmp_path / "123" / "fd"
    original_iterdir = Path.iterdir

    def iterdir(path):
        if path == blocked:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)

    with pytest.raises(listener_census.ListenerCensusDegraded) as raised:
        listener_census.proc_listener_census(50000, proc_root=tmp_path)

    message = str(raised.value)
    assert "cannot identify owner PID for listening socket inode(s) ['4242']" in message
    assert "unproven target record(s)" in message
    assert "backend visibility limit(s)" not in message
    target = [item for item in raised.value.census.degradations if item.scope == listener_census.SCOPE_TARGET]
    assert len(target) == 1 and "unattributed listening inode 4242" in target[0].stage


def test_proc_walk_honours_the_published_timeout_with_an_injected_clock(tmp_path, monkeypatch):
    """`timeout_seconds` was accepted and silently ignored on the Linux path."""

    write_proc_listener(tmp_path)
    for index in range(5):
        (tmp_path / str(2000 + index) / "fd").mkdir(parents=True)

    ticks = iter([0.0] + [10.0] * 40)

    with pytest.raises(listener_census.ListenerCensusTimeout, match="exceeded 2.0s"):
        listener_census.proc_listener_census(
            50000,
            proc_root=tmp_path,
            timeout_seconds=2.0,
            clock=lambda: next(ticks),
        )


def test_proc_walk_checks_timeout_inside_one_process_fd_walk(tmp_path):
    """One high-FD process cannot consume the whole caller budget after the process check."""

    write_proc_listener(tmp_path)
    ticks = iter((0.0, 0.0, 0.0, 10.0))

    with pytest.raises(listener_census.ListenerCensusTimeout, match="exceeded 2.0s"):
        listener_census.proc_listener_census(
            50000,
            proc_root=tmp_path,
            timeout_seconds=2.0,
            clock=lambda: next(ticks),
        )


def test_proc_walk_timeout_includes_listener_table_read(tmp_path):
    """The published bound starts before the TCP listener tables are read."""

    write_proc_listener(tmp_path)
    ticks = iter((0.0, 10.0))

    with pytest.raises(listener_census.ListenerCensusTimeout, match="exceeded 2.0s"):
        listener_census.proc_listener_census(
            50000,
            proc_root=tmp_path,
            timeout_seconds=2.0,
            clock=lambda: next(ticks),
        )


def test_proc_walk_checks_timeout_after_final_denied_process(tmp_path):
    """A final denied UID read cannot cross the deadline through its continue path."""

    write_proc_listener(tmp_path)
    elapsed = [0.0]

    def denied_uid(process_dir):
        elapsed[0] = 10.0
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(process_dir))

    with pytest.raises(listener_census.ListenerCensusTimeout, match="exceeded 2.0s"):
        listener_census.proc_listener_census(
            50000,
            proc_root=tmp_path,
            process_uid_reader=denied_uid,
            timeout_seconds=2.0,
            clock=lambda: elapsed[0],
        )


def test_proc_walk_checks_timeout_after_the_final_fd_operation(tmp_path):
    """The last descriptor cannot cross the deadline and then return a false green."""

    listener = write_proc_listener(tmp_path)
    (listener.parent / "10").unlink()
    elapsed = [0.0]

    def readlink(_entry):
        elapsed[0] = 10.0
        return "socket:[4242]"

    with pytest.raises(listener_census.ListenerCensusTimeout, match="exceeded 2.0s"):
        listener_census.proc_listener_census(
            50000,
            proc_root=tmp_path,
            readlink=readlink,
            timeout_seconds=2.0,
            clock=lambda: elapsed[0],
        )


def test_proc_fallback_keeps_non_permission_stat_errors_fatal(tmp_path):
    """A successful fdinfo fallback must not launder a fatal stat error."""

    listener = write_proc_listener(tmp_path)

    def readlink(entry):
        if entry == listener:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(entry))
        raise FileNotFoundError(entry)

    def stat_inode(_entry):
        raise OSError(errno.EIO, os.strerror(errno.EIO))

    with pytest.raises(listener_census.ListenerCensusError, match="cannot inspect Linux file descriptor") as raised:
        listener_census.proc_listener_census(
            50000,
            proc_root=tmp_path,
            readlink=readlink,
            fd_stat_inode_reader=stat_inode,
            fdinfo_inode_reader=lambda _entry: 4242,
            clock=lambda: 0.0,
        )

    assert isinstance(raised.value.__cause__, OSError)
    assert raised.value.__cause__.errno == errno.EIO


def test_proc_walk_completes_inside_its_timeout(tmp_path, monkeypatch):
    """A clock that never advances must not trip the bound."""

    listener = write_proc_listener(tmp_path)
    census = listener_census.proc_listener_census(
        50000,
        proc_root=tmp_path,
        readlink=lambda entry: "socket:[4242]" if entry == listener else "",
        timeout_seconds=2.0,
        clock=lambda: 0.0,
    )
    assert census.pids == (123,)
