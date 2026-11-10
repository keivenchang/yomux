# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Gate O: old and rebuilt YOmux processes must coexist without state loss."""

from __future__ import annotations

import ast
import io
import os
import re
import shlex
import signal
import subprocess
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.client import HTTPConnection
from pathlib import Path
from threading import Barrier
from threading import Event
from threading import Lock
from threading import Thread
from typing import Any

import pytest
from tools.product_environment import product_env

import tests.gate_harness as gate_harness_module
from tests.gate_harness import HttpPortLease
from tests.gate_harness import FixtureMemberExitBarrier
from tests.gate_harness import run_fixture_cleanup_phases
from tests.gate_harness import assert_writable_paths_beneath
from tests.gate_harness import bootstrap_writable_paths
from tests.isolated_dev_server import BuildPaths
from tests.isolated_dev_server import SERVER_STOP_TIMEOUT_SECONDS
from tests.isolated_dev_server import build_environment
from tests.isolated_dev_server import build_paths as _build_paths
from tests.isolated_dev_server import finish_server_output
from tests.isolated_dev_server import signal_server_exactly
from tests.isolated_dev_server import server_entrypoint_arguments
from tests.isolated_dev_server import wait_until_serving
from tests.tmux_runtime import start_isolated_tmux_runtime
from tests.tmux_runtime import stop_isolated_tmux_runtime
from yomux_lib.host_identity import process_start_identity
from yomux_lib.local_services import registry as local_services_registry
from yomux_lib.local_services.registry import bounded_process_table
from yomux_lib.local_services.registry import ProcessTableEntry
from yomux_lib.local_services.registry import process_spawn_generation
from yomux_lib.local_services.registry import SpawnOwnershipProof
from yomux_lib.local_services.registry import SpawnProcessOwnership


pytestmark = pytest.mark.socket

REPO_ROOT = Path(__file__).resolve().parents[1]
CONCURRENT_CAPTURE_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class CapturedFixtureService:
    launcher_pid: int
    launcher_start_identity: str
    ownership: SpawnProcessOwnership
    proof: SpawnOwnershipProof
    member_argv: tuple[tuple[int, tuple[str, ...]], ...]


@dataclass
class RunningBuild:
    label: str
    source_root: Path
    paths: BuildPaths
    tmux: Any
    port: int
    process: subprocess.Popen[str]
    server_start_identity: str
    baseline_paths: frozenset[Path]
    allow_legacy_service_capture: bool = False
    output: list[str] = field(default_factory=list)
    captured_services: tuple[CapturedFixtureService, ...] = ()
    stopped: bool = False
    output_reader: Thread | None = None
    service_capture_lock: Lock = field(default_factory=Lock, repr=False)

    def request(self, path: str) -> tuple[int, dict[str, str], bytes]:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return int(response.status), dict(response.getheaders()), response.read()
        finally:
            connection.close()
            self.refresh_service_ownership()

    def refresh_service_ownership(self) -> None:
        # Concurrent health requests prove that the servers can serve together. Their
        # fixture-only process-table snapshots are not part of that request contract,
        # so one RunningBuild owns this mutable capture ledger at a time.
        with self.service_capture_lock:
            if self.stopped or not self.server_start_identity:
                return
            current_identity = process_start_identity(self.process.pid)
            if current_identity is None:
                return
            assert current_identity == self.server_start_identity, (
                f"{self.label} launcher {self.process.pid} identity changed during local-service capture"
            )
            captured = _capture_fixture_services(
                self.process.pid,
                self.server_start_identity,
                allow_legacy=self.allow_legacy_service_capture,
            )
            self.captured_services = _merge_captured_fixture_services(self.captured_services, captured)

    def signal_server(self, signal_number: int) -> None:
        signal_server_exactly(
            self.process,
            self.server_start_identity,
            signal_number,
            label=self.label,
        )

    def stop(self) -> None:
        if self.stopped:
            return

        def stop_server() -> None:
            if self.process.poll() is None:
                self.signal_server(signal.SIGINT)
            try:
                finish_server_output(self.process, self.output, self.output_reader)
            except subprocess.TimeoutExpired:
                self.signal_server(signal.SIGTERM)
                try:
                    finish_server_output(self.process, self.output, self.output_reader)
                except subprocess.TimeoutExpired:
                    self.signal_server(signal.SIGKILL)
                    finish_server_output(self.process, self.output, self.output_reader)

        run_fixture_cleanup_phases(
            f"{self.label} build",
            (
                ("capture-services", self.refresh_service_ownership),
                ("stop-server", stop_server),
                (
                    "stop-services",
                    lambda: _stop_fixture_services(
                        self.paths,
                        captured_services=self.captured_services,
                    ),
                ),
            ),
        )
        self.stopped = True


def _process_argv(pid: int, command: str) -> tuple[str, ...]:
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        try:
            return tuple(shlex.split(command))
        except ValueError:
            return ()
    return tuple(os.fsdecode(value) for value in raw.split(b"\0") if value)


def _is_local_service_process(pid: int, command: str, *, allow_legacy: bool = False) -> bool:
    argv = _process_argv(pid, command)
    try:
        module_index = argv.index("-m") + 1
    except ValueError:
        return False
    module_prefixes = ("yomux_lib.", "yolomux_lib.") if allow_legacy else ("yomux_lib.",)
    return (
        module_index < len(argv)
        and argv[module_index].startswith(module_prefixes)
        and "--serve" in argv
        and "--socket" in argv
    )


def test_o6_legacy_service_capture_accepts_only_the_explicit_old_package_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        f"{__name__}._process_argv",
        lambda _pid, _command: (
            "python",
            "-m",
            "yolomux_lib.stats_current.service",
            "--serve",
            "--socket",
            "/tmp/exact.sock",
        ),
    )

    assert not _is_local_service_process(43201, "legacy sidecar")
    assert _is_local_service_process(43201, "legacy sidecar", allow_legacy=True)


def _member_matches_capture(
    pid: int,
    identity: str,
    process_group: int,
    session_id: int,
    expected_argv: tuple[str, ...],
    command: str,
) -> bool:
    if process_start_identity(pid) != identity:
        return False
    try:
        current_group = os.getpgid(pid)
        current_session = os.getsid(pid)
    except ProcessLookupError:
        return False
    if current_group != process_group or current_session != session_id:
        return False
    return _process_argv(pid, command) == expected_argv and process_start_identity(pid) == identity


def _capture_fixture_services(
    launcher_pid: int,
    launcher_start_identity: str,
    *,
    allow_legacy: bool = False,
) -> tuple[CapturedFixtureService, ...]:
    assert process_start_identity(launcher_pid) == launcher_start_identity, (
        f"fixture launcher {launcher_pid} identity changed before local-service capture"
    )
    table = bounded_process_table(require_complete=True)
    assert process_start_identity(launcher_pid) == launcher_start_identity, (
        f"fixture launcher {launcher_pid} identity changed during local-service capture"
    )
    captured: list[CapturedFixtureService] = []
    for service_pid, entry in table.items():
        if entry.ppid != launcher_pid or not _is_local_service_process(
            service_pid,
            entry.command,
            allow_legacy=allow_legacy,
        ):
            continue
        assert entry.pgid == service_pid and entry.session_id == service_pid, (
            f"fixture local-service child {service_pid} lacks a dedicated process group/session"
        )
        member_identities: list[tuple[int, str]] = []
        member_argv: list[tuple[int, tuple[str, ...]]] = []
        for pid, member in table.items():
            if member.pgid != service_pid or member.session_id != service_pid:
                continue
            identity = member.start_identity or process_start_identity(pid)
            if not identity:
                continue
            argv = _process_argv(pid, member.command)
            if not argv or not _member_matches_capture(
                pid,
                identity,
                service_pid,
                service_pid,
                argv,
                member.command,
            ):
                continue
            member_identities.append((pid, identity))
            member_argv.append((pid, argv))
        leader_identity = dict(member_identities).get(service_pid)
        if not leader_identity:
            continue
        generation_marker = process_spawn_generation(service_pid)
        if generation_marker is None and allow_legacy:
            generation_marker = f"legacy:{launcher_start_identity}"
        else:
            assert generation_marker is not None and re.fullmatch(r"[0-9a-f]{32}", generation_marker), (
                f"fixture local-service child {service_pid} has no valid spawn generation"
            )
        ownership = SpawnProcessOwnership(
            leader_pid=service_pid,
            process_group=service_pid,
            session_id=service_pid,
            generation_marker=generation_marker,
            member_identities=tuple(sorted(member_identities)),
        )
        proof = SpawnOwnershipProof(ownership, True, ownership.member_identities)
        captured.append(CapturedFixtureService(
            launcher_pid,
            launcher_start_identity,
            ownership,
            proof,
            tuple(sorted(member_argv)),
        ))
    assert process_start_identity(launcher_pid) == launcher_start_identity, (
        f"fixture launcher {launcher_pid} identity changed after local-service capture"
    )
    return tuple(captured)


def _merge_captured_fixture_services(
    before: Iterable[CapturedFixtureService],
    after: Iterable[CapturedFixtureService],
) -> tuple[CapturedFixtureService, ...]:
    merged = {
        (capture.ownership.leader_pid, dict(capture.ownership.member_identities)[capture.ownership.leader_pid]): capture
        for capture in before
    }
    for capture in after:
        key = (capture.ownership.leader_pid, dict(capture.ownership.member_identities)[capture.ownership.leader_pid])
        merged[key] = capture
    return tuple(merged[key] for key in sorted(merged))


def _refresh_fixture_service_capture(capture: CapturedFixtureService) -> CapturedFixtureService:
    ownership = capture.ownership
    table = bounded_process_table(require_complete=True)
    launcher_identity = process_start_identity(capture.launcher_pid)
    assert launcher_identity in {None, capture.launcher_start_identity}, (
        f"fixture launcher {capture.launcher_pid} identity was reused during local-service teardown"
    )
    leader = table.get(ownership.leader_pid)
    retained_commands = dict(capture.member_argv)
    retained_identities = dict(ownership.member_identities)
    leader_identity = (
        leader.start_identity or process_start_identity(ownership.leader_pid)
        if leader is not None
        else None
    )
    leader_is_current = leader is not None and (
        leader_identity == retained_identities.get(ownership.leader_pid)
        and leader.pgid == ownership.process_group
        and leader.session_id == ownership.session_id
        and (launcher_identity is None or leader.ppid == capture.launcher_pid)
        and (launcher_identity is not None or leader.ppid in {1, capture.launcher_pid})
    )
    generation_is_portable = re.fullmatch(r"[0-9a-f]{32}", ownership.generation_marker) is not None
    legacy_capture = ownership.generation_marker.startswith("legacy:")
    members: list[tuple[int, str]] = []
    commands: list[tuple[int, tuple[str, ...]]] = []
    for pid, entry in table.items():
        if entry.pgid != ownership.process_group or entry.session_id != ownership.session_id:
            continue
        identity = entry.start_identity or process_start_identity(pid)
        if not identity:
            continue
        expected_identity = retained_identities.get(pid)
        if pid == ownership.leader_pid and not leader_is_current:
            continue
        if legacy_capture and not leader_is_current:
            continue
        if legacy_capture and expected_identity is None:
            continue
        if expected_identity is None and not leader_is_current and not (
            generation_is_portable and process_spawn_generation(pid) == ownership.generation_marker
        ):
            continue
        if expected_identity is not None and expected_identity != identity:
            continue
        argv = _process_argv(pid, entry.command)
        expected_argv = retained_commands.get(pid, argv)
        if not expected_argv or not _member_matches_capture(
            pid,
            identity,
            ownership.process_group,
            ownership.session_id,
            expected_argv,
            entry.command,
        ):
            continue
        members.append((pid, identity))
        commands.append((pid, expected_argv))
    refreshed_ownership = SpawnProcessOwnership(
        leader_pid=ownership.leader_pid,
        process_group=ownership.process_group,
        session_id=ownership.session_id,
        generation_marker=ownership.generation_marker,
        member_identities=tuple(sorted(members)),
    )
    proof = SpawnOwnershipProof(
        refreshed_ownership,
        any(entry.pgid == ownership.process_group for entry in table.values()),
        refreshed_ownership.member_identities,
    )
    return CapturedFixtureService(
        capture.launcher_pid,
        capture.launcher_start_identity,
        refreshed_ownership,
        proof,
        tuple(sorted(commands)),
    )


def _capture_authorizes_member(capture: CapturedFixtureService, pid: int, identity: str) -> bool:
    if (pid, identity) not in set(capture.proof.owned_member_identities):
        return False
    launcher_identity = process_start_identity(capture.launcher_pid)
    if launcher_identity not in {None, capture.launcher_start_identity}:
        return False
    expected_argv = dict(capture.member_argv).get(pid)
    if expected_argv is None:
        return False
    generation = capture.ownership.generation_marker
    if re.fullmatch(r"[0-9a-f]{32}", generation) is not None and process_spawn_generation(pid) != generation:
        return False
    table = bounded_process_table(require_complete=True)
    if capture.ownership.generation_marker.startswith("legacy:"):
        leader = table.get(capture.ownership.leader_pid)
        leader_identity = dict(capture.ownership.member_identities).get(capture.ownership.leader_pid)
        leader_argv = dict(capture.member_argv).get(capture.ownership.leader_pid)
        if (
            leader is None
            or leader_identity is None
            or leader_argv is None
            or (launcher_identity is not None and leader.ppid != capture.launcher_pid)
            or (launcher_identity is None and leader.ppid not in {1, capture.launcher_pid})
            or not _member_matches_capture(
                capture.ownership.leader_pid,
                leader_identity,
                capture.ownership.process_group,
                capture.ownership.session_id,
                leader_argv,
                leader.command,
            )
        ):
            return False
    entry = table.get(pid)
    if (
        entry is None
        or (entry.start_identity or process_start_identity(pid)) != identity
        or entry.pgid != capture.ownership.process_group
        or entry.session_id != capture.ownership.session_id
    ):
        return False
    if pid == capture.ownership.leader_pid:
        if launcher_identity is not None and entry.ppid != capture.launcher_pid:
            return False
        if launcher_identity is None and entry.ppid not in {1, capture.launcher_pid}:
            return False
    return _member_matches_capture(
        pid,
        identity,
        capture.ownership.process_group,
        capture.ownership.session_id,
        expected_argv,
        entry.command,
    )


def _signal_fixture_service_members(
    captures: Iterable[CapturedFixtureService],
    signal_number: int,
    timeout: float,
) -> tuple[CapturedFixtureService, ...]:
    deadline = time.monotonic() + timeout
    refreshed = tuple(_refresh_fixture_service_capture(capture) for capture in captures)
    for capture in refreshed:
        identities = capture.proof.owned_member_identities
        if not identities:
            continue
        with FixtureMemberExitBarrier(identities) as barrier:
            sent = barrier.signal_exact(
                signal_number,
                lambda pid, identity: _capture_authorizes_member(capture, pid, identity),
            )
            if sent or barrier.can_wait_exact:
                barrier.wait(max(0.0, deadline - time.monotonic()))
    return refreshed


def _stop_fixture_services(
    paths: BuildPaths,
    *,
    captured_services: Iterable[CapturedFixtureService] = (),
) -> None:
    """Stop only exact service identities captured from the live fixture launcher."""
    captured = tuple(captured_services)
    remaining = _signal_fixture_service_members(captured, signal.SIGTERM, 2.0)
    remaining = _signal_fixture_service_members(remaining, signal.SIGKILL, 1.0)
    retained = [
        refreshed
        for capture in remaining
        if (
            (refreshed := _refresh_fixture_service_capture(capture)).proof.owned_member_identities
            or refreshed.proof.group_exists
        )
    ]
    assert not retained, f"{paths.root}: fixture services survived teardown: {retained}"


def _runtime_relative_paths(root: Path) -> frozenset[Path]:
    return frozenset(path.relative_to(root) for path in root.rglob("*"))


def test_o1_service_capture_is_serialized_while_health_requests_converge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Capture bookkeeping cannot race the requests used to prove concurrent health."""

    first_capture = Event()
    second_attempted = Event()
    release_capture = Event()
    overlap = Event()
    active = 0
    active_lock = Lock()

    class Process:
        pid = 43210

    def capture(*_args: Any, **_kwargs: Any) -> tuple[CapturedFixtureService, ...]:
        nonlocal active
        with active_lock:
            active += 1
            if active > 1:
                overlap.set()
        first_capture.set()
        assert second_attempted.wait(timeout=CONCURRENT_CAPTURE_TIMEOUT_SECONDS)
        assert release_capture.wait(timeout=CONCURRENT_CAPTURE_TIMEOUT_SECONDS)
        with active_lock:
            active -= 1
        return ()

    monkeypatch.setattr(f"{__name__}.process_start_identity", lambda _pid: "proc:stable")
    monkeypatch.setattr(f"{__name__}._capture_fixture_services", capture)
    build = RunningBuild("capture", REPO_ROOT, None, None, 0, Process(), "proc:stable", frozenset())
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(build.refresh_service_ownership)
        assert first_capture.wait(timeout=CONCURRENT_CAPTURE_TIMEOUT_SECONDS)
        second = executor.submit(lambda: (second_attempted.set(), build.refresh_service_ownership()))
        assert second_attempted.wait(timeout=CONCURRENT_CAPTURE_TIMEOUT_SECONDS)
        release_capture.set()
        first.result(timeout=CONCURRENT_CAPTURE_TIMEOUT_SECONDS)
        second.result(timeout=CONCURRENT_CAPTURE_TIMEOUT_SECONDS)

    assert not overlap.is_set()


def _wait_until_serving(build: RunningBuild) -> None:
    """Adapter only: the readiness rule itself belongs to `isolated_dev_server`."""

    build.output_reader = wait_until_serving(
        build.process,
        build.port,
        build.output,
        label=build.label,
    )


def _spawn_build(
    label: str,
    source_root: Path,
    paths: BuildPaths,
    tmux_runtime: Any,
    register: Callable[[RunningBuild], None],
    *,
    allow_legacy_service_capture: bool = False,
) -> RunningBuild:
    baseline_paths = _runtime_relative_paths(paths.root)
    lease = HttpPortLease.reserve()
    port = lease.port
    command = [
        *server_entrypoint_arguments(source_root),
        "--http",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--root",
        str(source_root),
        "--config",
        str(paths.config_dir),
        "--stats",
        str(paths.state_dir / "stats-v9.sqlite3"),
    ]
    lease.release()
    process = subprocess.Popen(
        command,
        cwd=source_root,
        env=build_environment(source_root, paths, tmux_runtime, port),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    build = RunningBuild(
        label,
        source_root,
        paths,
        tmux_runtime,
        port,
        process,
        "",
        baseline_paths,
        allow_legacy_service_capture=allow_legacy_service_capture,
    )
    register(build)
    server_start_identity = process_start_identity(process.pid)
    assert server_start_identity, f"{label} server process has no stable start identity"
    build.server_start_identity = server_start_identity
    build.refresh_service_ownership()
    _wait_until_serving(build)
    build.refresh_service_ownership()
    return build


@pytest.fixture
def private_tmux_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterable[Callable[[], Any]]:
    runtimes: list[Any] = []

    def start() -> Any:
        runtime = start_isolated_tmux_runtime(
            monkeypatch,
            tmp_path,
            session_count=1,
        )
        runtimes.append(runtime)
        return runtime

    try:
        yield start
    finally:
        run_fixture_cleanup_phases("coexistence tmux runtimes", [
            (f"runtime-{index}", lambda runtime=runtime: stop_isolated_tmux_runtime(runtime))
            for index, runtime in enumerate(reversed(runtimes))
        ])


@pytest.fixture
def running_build_factory(
    private_tmux_factory: Callable[[], Any],
    tmp_path: Path,
) -> Iterable[Callable[..., RunningBuild]]:
    builds: list[RunningBuild] = []

    def start(
        label: str,
        source_root: Path,
        *,
        state_dir: Path | None = None,
        legacy_service_capture: bool = False,
    ) -> RunningBuild:
        runtime_root = tmp_path / f"runtime-{len(builds) + 1}-{label}"
        paths = _build_paths(runtime_root, state_dir=state_dir)
        tmux_runtime = private_tmux_factory()
        # Historical Python snapshots remain migration fixtures only. Every live
        # process in this gate is the current Go server.
        build = _spawn_build(
            label,
            REPO_ROOT,
            paths,
            tmux_runtime,
            builds.append,
            allow_legacy_service_capture=legacy_service_capture,
        )
        return build

    try:
        yield start
    finally:
        ordered = tuple(reversed(builds))
        run_fixture_cleanup_phases(
            "coexistence builds",
            tuple((f"{build.label}-stop", build.stop) for build in ordered)
            + tuple((f"{build.label}-retry", build.stop) for build in ordered),
        )


def test_private_tmux_factory_retires_every_runtime_after_one_stop_failure(monkeypatch, tmp_path):
    stopped = []
    runtimes = iter(("first", "second"))
    failure = RuntimeError("private tmux retirement sentinel")

    def stop(runtime):
        stopped.append(runtime)
        if runtime == "second":
            raise failure

    monkeypatch.setattr(f"{__name__}.start_isolated_tmux_runtime", lambda *_args, **_kwargs: next(runtimes))
    monkeypatch.setattr(f"{__name__}.stop_isolated_tmux_runtime", stop)
    fixture = private_tmux_factory.__wrapped__(monkeypatch, tmp_path)
    start = next(fixture)
    assert (start(), start()) == ("first", "second")
    with pytest.raises(RuntimeError) as raised:
        next(fixture)
    assert raised.value is failure
    assert stopped == ["second", "first"]


def test_o6_exact_member_barrier_signals_pidfd_not_reused_numeric_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(gate_harness_module.os, "pidfd_open", lambda pid: pid + 1000)
    monkeypatch.setattr(gate_harness_module.os, "close", lambda _descriptor: None)
    monkeypatch.setattr(gate_harness_module, "process_start_identity", lambda pid: f"proc:{pid}")
    monkeypatch.setattr(
        gate_harness_module.signal,
        "pidfd_send_signal",
        lambda descriptor, signum: signals.append((descriptor, signum)),
    )
    monkeypatch.setattr(
        gate_harness_module.os,
        "kill",
        lambda _pid, _signum: (_ for _ in ()).throw(AssertionError("numeric PID signal is unsafe")),
    )

    with FixtureMemberExitBarrier(((43210, "proc:43210"),)) as barrier:
        sent = barrier.signal_exact(signal.SIGTERM, lambda pid, identity: (pid, identity) == (43210, "proc:43210"))

    assert sent == (43210,)
    assert signals == [(44210, signal.SIGTERM)]


def test_o6_exact_member_exit_after_pidfd_resolution_is_already_retired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate_harness_module.os, "pidfd_open", lambda pid: pid + 1000)
    monkeypatch.setattr(gate_harness_module.os, "close", lambda _descriptor: None)
    monkeypatch.setattr(gate_harness_module, "process_start_identity", lambda _pid: "proc:43210")
    monkeypatch.setattr(
        gate_harness_module.signal,
        "pidfd_send_signal",
        lambda _descriptor, _signum: (_ for _ in ()).throw(ProcessLookupError()),
    )

    with FixtureMemberExitBarrier(((43210, "proc:43210"),)) as barrier:
        sent = barrier.signal_exact(signal.SIGTERM, lambda _pid, _identity: True)

    assert sent == ()


def test_o6_exact_member_barrier_fails_closed_on_darwin_kqueue_without_numeric_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []

    class Queue:
        def control(self, changes: object, _max_events: int, _timeout: object) -> list[object]:
            events.append(("kqueue", changes))
            return []

        def close(self) -> None:
            events.append(("closed", None))

    monkeypatch.delattr(gate_harness_module.os, "pidfd_open")
    monkeypatch.setattr(gate_harness_module.select, "kqueue", Queue, raising=False)
    monkeypatch.setattr(gate_harness_module.select, "kevent", lambda pid, **_kwargs: ("event", pid), raising=False)
    monkeypatch.setattr(gate_harness_module.select, "KQ_FILTER_PROC", 1, raising=False)
    monkeypatch.setattr(gate_harness_module.select, "KQ_EV_ADD", 2, raising=False)
    monkeypatch.setattr(gate_harness_module.select, "KQ_EV_ENABLE", 4, raising=False)
    monkeypatch.setattr(gate_harness_module.select, "KQ_EV_ONESHOT", 8, raising=False)
    monkeypatch.setattr(gate_harness_module.select, "KQ_NOTE_EXIT", 16, raising=False)
    monkeypatch.setattr(gate_harness_module, "process_start_identity", lambda _pid: "ps:stable")
    monkeypatch.setattr(
        gate_harness_module.os,
        "kill",
        lambda pid, signum: events.append(("signal", (pid, signum))),
    )

    with FixtureMemberExitBarrier(((43210, "ps:stable"),)) as barrier:
        sent = barrier.signal_exact(signal.SIGTERM, lambda _pid, _identity: True)
        unanchored = barrier.unanchored_identities

    assert sent == ()
    assert unanchored == ((43210, "ps:stable"),)
    assert events[0][0] == "kqueue"
    assert events[1] == ("closed", None)
    assert all(event[0] != "signal" for event in events)


def test_o6_exact_member_barrier_reports_partial_pidfd_open_failure_without_numeric_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[tuple[int, int]] = []

    def pidfd_open(pid: int) -> int:
        if pid == 43211:
            raise PermissionError(pid)
        return pid + 1000

    monkeypatch.setattr(gate_harness_module.os, "pidfd_open", pidfd_open)
    monkeypatch.setattr(gate_harness_module.os, "close", lambda _descriptor: None)
    monkeypatch.setattr(gate_harness_module, "process_start_identity", lambda pid: f"proc:{pid}")
    monkeypatch.setattr(
        gate_harness_module.signal,
        "pidfd_send_signal",
        lambda descriptor, signum: signals.append((descriptor, signum)),
    )
    monkeypatch.setattr(
        gate_harness_module.os,
        "kill",
        lambda _pid, _signum: (_ for _ in ()).throw(AssertionError("numeric PID fallback is unsafe")),
    )

    identities = ((43210, "proc:43210"), (43211, "proc:43211"))
    with FixtureMemberExitBarrier(identities) as barrier:
        sent = barrier.signal_exact(signal.SIGTERM, lambda _pid, _identity: True)
        unanchored = barrier.unanchored_identities

    assert sent == (43210,)
    assert unanchored == ((43211, "proc:43211"),)
    assert signals == [(44210, signal.SIGTERM)]


def test_o6_process_table_uses_portable_session_column_for_darwin_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = "43201 43200 43201 43201 0:00.01 python -m yomux_lib.watchd --serve --socket /tmp/exact.sock\n"

    def run(command: list[str], **_kwargs: object) -> Completed:
        commands.append(command)
        return Completed()

    monkeypatch.setattr(local_services_registry.subprocess, "run", run)
    monkeypatch.setattr(local_services_registry, "process_state", lambda _pid: "")
    monkeypatch.setattr(local_services_registry, "process_start_time", lambda _pid: 0)

    table = local_services_registry.bounded_process_table(require_complete=True)

    assert commands == [["ps", "-axww", "-o", "pid=,ppid=,pgid=,sess=,time=,command="]]
    assert table[43201].session_id == 43201
    assert table[43201].command.endswith("--socket /tmp/exact.sock")


def test_o6_fixture_service_capture_uses_exact_live_launcher_children_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher_pid = 43200
    service_pid = 43201
    worker_pid = 43202
    foreign_pid = 43203
    launcher_identity = "proc:1200"
    table = {
        launcher_pid: ProcessTableEntry(1, launcher_pid, 0.0, "python yolomux.py", 1200, launcher_pid, launcher_identity),
        service_pid: ProcessTableEntry(
            launcher_pid,
            service_pid,
            0.0,
            "python -m yomux_lib.stats_current.service --serve --socket /tmp/exact.sock",
            1201,
            service_pid,
            "proc:1201",
        ),
        worker_pid: ProcessTableEntry(service_pid, service_pid, 0.0, "worker", 1202, service_pid, "proc:1202"),
        foreign_pid: ProcessTableEntry(
            1,
            foreign_pid,
            0.0,
            "python -m yomux_lib.stats_current.service --serve --socket /tmp/foreign.sock",
            1203,
            foreign_pid,
            "proc:1203",
        ),
    }
    identities = {pid: entry.start_identity for pid, entry in table.items()}
    monkeypatch.setattr(f"{__name__}.process_start_identity", identities.get)
    monkeypatch.setattr(f"{__name__}.bounded_process_table", lambda **_kwargs: table)
    monkeypatch.setattr(os, "getpgid", lambda pid: table[pid].pgid)
    monkeypatch.setattr(os, "getsid", lambda pid: table[pid].session_id)
    monkeypatch.setattr(f"{__name__}.process_spawn_generation", lambda pid: "a" * 32 if pid == service_pid else None)
    monkeypatch.setattr(
        f"{__name__}._process_argv",
        lambda _pid, command: tuple(command.split()),
    )

    captures = _capture_fixture_services(launcher_pid, launcher_identity)

    assert len(captures) == 1
    assert captures[0].ownership.leader_pid == service_pid
    assert captures[0].proof.owned_member_identities == (
        (service_pid, "proc:1201"),
        (worker_pid, "proc:1202"),
    )


@pytest.mark.parametrize("generation", (None, "not-a-generation"))
def test_o6_fixture_service_capture_fails_closed_without_valid_spawn_generation(
    monkeypatch: pytest.MonkeyPatch,
    generation: str | None,
) -> None:
    launcher_pid = 43200
    service_pid = 43201
    launcher_identity = "proc:1200"
    command = "python -m yomux_lib.watchd --serve --socket /tmp/exact.sock"
    table = {
        launcher_pid: ProcessTableEntry(1, launcher_pid, 0.0, "python yolomux.py", 1200, launcher_pid, launcher_identity),
        service_pid: ProcessTableEntry(launcher_pid, service_pid, 0.0, command, 1201, service_pid, "proc:1201"),
    }
    monkeypatch.setattr(f"{__name__}.bounded_process_table", lambda **_kwargs: table)
    monkeypatch.setattr(
        f"{__name__}.process_start_identity",
        lambda pid: launcher_identity if pid == launcher_pid else "proc:1201",
    )
    monkeypatch.setattr(os, "getpgid", lambda pid: table[pid].pgid)
    monkeypatch.setattr(os, "getsid", lambda pid: table[pid].session_id)
    monkeypatch.setattr(f"{__name__}.process_spawn_generation", lambda _pid: generation)
    monkeypatch.setattr(f"{__name__}._process_argv", lambda _pid, text: tuple(text.split()))

    with pytest.raises(AssertionError, match="has no valid spawn generation"):
        _capture_fixture_services(launcher_pid, launcher_identity)


def test_o6_legacy_peer_capture_accepts_missing_generation_with_exact_leader_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher_pid = 43200
    service_pid = 43201
    launcher_identity = "proc:1200"
    command = "python -m yomux_lib.watchd --serve --socket /tmp/legacy.sock"
    table = {
        launcher_pid: ProcessTableEntry(1, launcher_pid, 0.0, "python yolomux.py", 1200, launcher_pid, launcher_identity),
        service_pid: ProcessTableEntry(launcher_pid, service_pid, 0.0, command, 1201, service_pid, "proc:1201"),
    }
    monkeypatch.setattr(f"{__name__}.bounded_process_table", lambda **_kwargs: table)
    monkeypatch.setattr(
        f"{__name__}.process_start_identity",
        lambda pid: launcher_identity if pid == launcher_pid else "proc:1201",
    )
    monkeypatch.setattr(os, "getpgid", lambda pid: table[pid].pgid)
    monkeypatch.setattr(os, "getsid", lambda pid: table[pid].session_id)
    monkeypatch.setattr(f"{__name__}.process_spawn_generation", lambda _pid: None)
    monkeypatch.setattr(f"{__name__}._process_argv", lambda _pid, text: tuple(text.split()))

    captures = _capture_fixture_services(launcher_pid, launcher_identity, allow_legacy=True)

    assert len(captures) == 1
    assert captures[0].ownership.generation_marker == f"legacy:{launcher_identity}"
    assert captures[0].proof.owned_member_identities == ((service_pid, "proc:1201"),)


def test_o6_fixture_service_capture_revalidates_command_group_session_and_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher_pid = 43200
    service_pid = 43201
    launcher_identity = "proc:1200"
    command = "python -m yomux_lib.watchd --serve --socket /tmp/exact.sock"
    table = {
        launcher_pid: ProcessTableEntry(1, launcher_pid, 0.0, "python yolomux.py", 1200, launcher_pid, launcher_identity),
        service_pid: ProcessTableEntry(launcher_pid, service_pid, 0.0, command, 1201, service_pid, "proc:1201"),
    }
    identities = {launcher_pid: launcher_identity, service_pid: "proc:1201"}
    monkeypatch.setattr(f"{__name__}.bounded_process_table", lambda **_kwargs: table)
    monkeypatch.setattr(f"{__name__}.process_start_identity", identities.get)
    monkeypatch.setattr(os, "getpgid", lambda pid: table[pid].pgid)
    monkeypatch.setattr(os, "getsid", lambda pid: table[pid].session_id)
    monkeypatch.setattr(f"{__name__}.process_spawn_generation", lambda pid: "a" * 32 if pid == service_pid else None)
    monkeypatch.setattr(f"{__name__}._process_argv", lambda _pid, text: tuple(text.split()))
    capture = _capture_fixture_services(launcher_pid, launcher_identity)[0]
    table[service_pid] = ProcessTableEntry(launcher_pid, service_pid, 0.0, command + " --changed", 1201, service_pid, "proc:1201")

    refreshed = _refresh_fixture_service_capture(capture)

    assert refreshed.proof.owned_member_identities == ()


def test_o6_fixture_service_member_is_revalidated_immediately_before_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher_pid = 43200
    service_pid = 43201
    ownership = SpawnProcessOwnership(service_pid, service_pid, service_pid, "a" * 32, ((service_pid, "proc:1201"),))
    capture = CapturedFixtureService(
        launcher_pid,
        "proc:1200",
        ownership,
        SpawnOwnershipProof(ownership, True, ownership.member_identities),
        ((service_pid, ("python", "-m", "yomux_lib.watchd", "--serve", "--socket", "/tmp/exact.sock")),),
    )
    changed_command = "python -m foreign.module --serve --socket /tmp/exact.sock"
    table = {
        service_pid: ProcessTableEntry(launcher_pid, service_pid, 0.0, changed_command, 1201, service_pid, "proc:1201"),
    }
    monkeypatch.setattr(f"{__name__}.bounded_process_table", lambda **_kwargs: table)
    monkeypatch.setattr(
        f"{__name__}.process_start_identity",
        lambda pid: "proc:1200" if pid == launcher_pid else "proc:1201",
    )
    monkeypatch.setattr(os, "getpgid", lambda _pid: service_pid)
    monkeypatch.setattr(os, "getsid", lambda _pid: service_pid)
    monkeypatch.setattr(f"{__name__}.process_spawn_generation", lambda _pid: "a" * 32)
    monkeypatch.setattr(f"{__name__}._process_argv", lambda _pid, text: tuple(text.split()))

    assert _capture_authorizes_member(capture, service_pid, "proc:1201") is False


def test_o6_fixture_service_capture_survives_launcher_exit_without_adopting_reused_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher_pid = 43200
    service_pid = 43201
    ownership = SpawnProcessOwnership(service_pid, service_pid, service_pid, "a" * 32, ((service_pid, "proc:1201"),))
    capture = CapturedFixtureService(
        launcher_pid,
        "proc:1200",
        ownership,
        SpawnOwnershipProof(ownership, True, ownership.member_identities),
        ((service_pid, ("python", "-m", "yomux_lib.watchd", "--serve", "--socket", "/tmp/exact.sock")),),
    )
    command = "python -m yomux_lib.watchd --serve --socket /tmp/exact.sock"
    table = {service_pid: ProcessTableEntry(1, service_pid, 0.0, command, 1201, service_pid, "proc:1201")}
    identities: dict[int, str | None] = {launcher_pid: None, service_pid: "proc:1201"}
    monkeypatch.setattr(f"{__name__}.bounded_process_table", lambda **_kwargs: table)
    monkeypatch.setattr(f"{__name__}.process_start_identity", identities.get)
    monkeypatch.setattr(os, "getpgid", lambda pid: table[pid].pgid)
    monkeypatch.setattr(os, "getsid", lambda pid: table[pid].session_id)
    monkeypatch.setattr(f"{__name__}._process_argv", lambda _pid, text: tuple(text.split()))

    after_launcher_exit = _refresh_fixture_service_capture(capture)
    identities[service_pid] = "proc:reused"
    after_pid_reuse = _refresh_fixture_service_capture(capture)

    assert after_launcher_exit.proof.owned_member_identities == ((service_pid, "proc:1201"),)
    assert after_pid_reuse.proof.owned_member_identities == ()


def test_o6_fixture_service_capture_retains_exact_descendant_after_service_leader_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher_pid = 43200
    service_pid = 43201
    worker_pid = 43202
    ownership = SpawnProcessOwnership(service_pid, service_pid, service_pid, "a" * 32, (
        (service_pid, "proc:1201"),
        (worker_pid, "proc:1202"),
    ))
    capture = CapturedFixtureService(
        launcher_pid,
        "proc:1200",
        ownership,
        SpawnOwnershipProof(ownership, True, ownership.member_identities),
        ((service_pid, ("service",)), (worker_pid, ("worker",))),
    )
    table = {worker_pid: ProcessTableEntry(1, service_pid, 0.0, "worker", 1202, service_pid, "proc:1202")}
    monkeypatch.setattr(f"{__name__}.bounded_process_table", lambda **_kwargs: table)
    monkeypatch.setattr(
        f"{__name__}.process_start_identity",
        lambda pid: {launcher_pid: None, service_pid: None, worker_pid: "proc:1202"}.get(pid),
    )
    monkeypatch.setattr(os, "getpgid", lambda _pid: service_pid)
    monkeypatch.setattr(os, "getsid", lambda _pid: service_pid)
    monkeypatch.setattr(f"{__name__}._process_argv", lambda _pid, text: tuple(text.split()))

    refreshed = _refresh_fixture_service_capture(capture)

    assert refreshed.proof.owned_member_identities == ((worker_pid, "proc:1202"),)


def test_o6_legacy_capture_fails_closed_on_group_after_exact_leader_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher_pid = 43200
    service_pid = 43201
    worker_pid = 43202
    ownership = SpawnProcessOwnership(service_pid, service_pid, service_pid, "legacy:proc:1200", (
        (service_pid, "proc:1201"),
        (worker_pid, "proc:1202"),
    ))
    capture = CapturedFixtureService(
        launcher_pid,
        "proc:1200",
        ownership,
        SpawnOwnershipProof(ownership, True, ownership.member_identities),
        ((service_pid, ("service",)), (worker_pid, ("worker",))),
    )
    table = {worker_pid: ProcessTableEntry(1, service_pid, 0.0, "worker", 1202, service_pid, "proc:1202")}
    monkeypatch.setattr(f"{__name__}.bounded_process_table", lambda **_kwargs: table)
    monkeypatch.setattr(
        f"{__name__}.process_start_identity",
        lambda pid: {launcher_pid: None, service_pid: None, worker_pid: "proc:1202"}.get(pid),
    )
    monkeypatch.setattr(os, "getpgid", lambda _pid: service_pid)
    monkeypatch.setattr(os, "getsid", lambda _pid: service_pid)
    monkeypatch.setattr(f"{__name__}._process_argv", lambda _pid, text: tuple(text.split()))

    refreshed = _refresh_fixture_service_capture(capture)

    assert refreshed.proof.owned_member_identities == ()
    assert refreshed.proof.group_exists is True


def test_o6_legacy_member_signal_requires_exact_leader_to_remain_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher_pid = 43200
    service_pid = 43201
    worker_pid = 43202
    ownership = SpawnProcessOwnership(service_pid, service_pid, service_pid, "legacy:proc:1200", (
        (service_pid, "proc:1201"),
        (worker_pid, "proc:1202"),
    ))
    capture = CapturedFixtureService(
        launcher_pid,
        "proc:1200",
        ownership,
        SpawnOwnershipProof(ownership, True, ownership.member_identities),
        ((service_pid, ("service",)), (worker_pid, ("worker",))),
    )
    table = {
        service_pid: ProcessTableEntry(launcher_pid, service_pid, 0.0, "service", 1201, service_pid, "proc:1201"),
        worker_pid: ProcessTableEntry(service_pid, service_pid, 0.0, "worker", 1202, service_pid, "proc:1202"),
    }
    identities = {launcher_pid: "proc:1200", service_pid: "proc:1201", worker_pid: "proc:1202"}
    monkeypatch.setattr(f"{__name__}.bounded_process_table", lambda **_kwargs: table)
    monkeypatch.setattr(f"{__name__}.process_start_identity", identities.get)
    monkeypatch.setattr(os, "getpgid", lambda _pid: service_pid)
    monkeypatch.setattr(os, "getsid", lambda _pid: service_pid)
    monkeypatch.setattr(f"{__name__}._process_argv", lambda _pid, text: tuple(text.split()))

    assert _capture_authorizes_member(capture, worker_pid, "proc:1202") is True
    del table[service_pid]
    identities[service_pid] = None
    assert _capture_authorizes_member(capture, worker_pid, "proc:1202") is False


def test_o6_legacy_capture_does_not_adopt_new_descendant_while_leader_is_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher_pid = 43200
    service_pid = 43201
    new_worker_pid = 43202
    ownership = SpawnProcessOwnership(
        service_pid,
        service_pid,
        service_pid,
        "legacy:proc:1200",
        ((service_pid, "proc:1201"),),
    )
    capture = CapturedFixtureService(
        launcher_pid,
        "proc:1200",
        ownership,
        SpawnOwnershipProof(ownership, True, ownership.member_identities),
        ((service_pid, ("service",)),),
    )
    table = {
        service_pid: ProcessTableEntry(launcher_pid, service_pid, 0.0, "service", 1201, service_pid, "proc:1201"),
        new_worker_pid: ProcessTableEntry(service_pid, service_pid, 0.0, "new-worker", 1202, service_pid, "proc:1202"),
    }
    identities = {launcher_pid: "proc:1200", service_pid: "proc:1201", new_worker_pid: "proc:1202"}
    monkeypatch.setattr(f"{__name__}.bounded_process_table", lambda **_kwargs: table)
    monkeypatch.setattr(f"{__name__}.process_start_identity", identities.get)
    monkeypatch.setattr(os, "getpgid", lambda _pid: service_pid)
    monkeypatch.setattr(os, "getsid", lambda _pid: service_pid)
    monkeypatch.setattr(f"{__name__}._process_argv", lambda _pid, text: tuple(text.split()))

    refreshed = _refresh_fixture_service_capture(capture)

    assert refreshed.proof.owned_member_identities == ((service_pid, "proc:1201"),)


def test_o6_fixture_service_capture_adopts_post_term_descendant_by_spawn_generation_after_leader_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher_pid = 43200
    service_pid = 43201
    late_worker_pid = 43203
    generation = "a" * 32
    ownership = SpawnProcessOwnership(
        service_pid,
        service_pid,
        service_pid,
        generation,
        ((service_pid, "proc:1201"),),
    )
    capture = CapturedFixtureService(
        launcher_pid,
        "proc:1200",
        ownership,
        SpawnOwnershipProof(ownership, True, ownership.member_identities),
        ((service_pid, ("service",)),),
    )
    table = {
        late_worker_pid: ProcessTableEntry(1, service_pid, 0.0, "late-worker", 1203, service_pid, "proc:1203"),
    }
    monkeypatch.setattr(f"{__name__}.bounded_process_table", lambda **_kwargs: table)
    monkeypatch.setattr(
        f"{__name__}.process_start_identity",
        lambda pid: {launcher_pid: None, service_pid: None, late_worker_pid: "proc:1203"}.get(pid),
    )
    monkeypatch.setattr(f"{__name__}.process_spawn_generation", lambda pid: generation if pid == late_worker_pid else None)
    monkeypatch.setattr(os, "getpgid", lambda _pid: service_pid)
    monkeypatch.setattr(os, "getsid", lambda _pid: service_pid)
    monkeypatch.setattr(f"{__name__}._process_argv", lambda _pid, text: tuple(text.split()))

    refreshed = _refresh_fixture_service_capture(capture)

    assert refreshed.proof.owned_member_identities == ((late_worker_pid, "proc:1203"),)
    assert refreshed.member_argv == ((late_worker_pid, ("late-worker",)),)


def test_o6_fixture_service_teardown_kills_generation_owned_descendant_spawned_after_term(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launcher_pid = 43200
    service_pid = 43201
    late_worker_pid = 43203
    generation = "a" * 32
    ownership = SpawnProcessOwnership(
        service_pid,
        service_pid,
        service_pid,
        generation,
        ((service_pid, "proc:1201"),),
    )
    capture = CapturedFixtureService(
        launcher_pid,
        "proc:1200",
        ownership,
        SpawnOwnershipProof(ownership, True, ownership.member_identities),
        ((service_pid, ("service",)),),
    )
    phase = {"value": "leader"}
    events: list[tuple[int, int]] = []

    def table(**_kwargs: object) -> dict[int, ProcessTableEntry]:
        if phase["value"] == "leader":
            return {service_pid: ProcessTableEntry(1, service_pid, 0.0, "service", 1201, service_pid, "proc:1201")}
        if phase["value"] == "late":
            return {late_worker_pid: ProcessTableEntry(1, service_pid, 0.0, "late-worker", 1203, service_pid, "proc:1203")}
        return {}

    def identity(pid: int) -> str | None:
        if pid == launcher_pid:
            return None
        if phase["value"] == "leader" and pid == service_pid:
            return "proc:1201"
        if phase["value"] == "late" and pid == late_worker_pid:
            return "proc:1203"
        return None

    class Barrier:
        def __init__(self, identities: Iterable[tuple[int, str]]):
            self.identities = tuple(identities)

        def signal_exact(self, signum: int, authorize: Callable[[int, str], bool]) -> tuple[int, ...]:
            sent = tuple(pid for pid, member_identity in self.identities if authorize(pid, member_identity))
            events.extend((pid, signum) for pid in sent)
            if signum == signal.SIGTERM:
                phase["value"] = "late"
            elif signum == signal.SIGKILL:
                phase["value"] = "retired"
            return sent

        def wait(self, _timeout: float) -> bool:
            return True

        def __enter__(self):
            return self

        def __exit__(self, _error_type, _error, _traceback):
            return None

    monkeypatch.setattr(f"{__name__}.bounded_process_table", table)
    monkeypatch.setattr(f"{__name__}.process_start_identity", identity)
    monkeypatch.setattr(
        f"{__name__}.process_spawn_generation",
        lambda pid: generation if pid in {service_pid, late_worker_pid} else None,
    )
    monkeypatch.setattr(os, "getpgid", lambda _pid: service_pid)
    monkeypatch.setattr(os, "getsid", lambda _pid: service_pid)
    monkeypatch.setattr(f"{__name__}._process_argv", lambda _pid, text: tuple(text.split()))
    monkeypatch.setattr(f"{__name__}.FixtureMemberExitBarrier", Barrier)

    _stop_fixture_services(_build_paths(tmp_path / "post-term-descendant"), captured_services=(capture,))

    assert events == [
        (service_pid, signal.SIGTERM),
        (late_worker_pid, signal.SIGKILL),
    ]


def test_o6_fixture_service_teardown_waits_for_kqueue_owned_natural_exit_without_numeric_signal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service_pid = 43201
    ownership = SpawnProcessOwnership(
        service_pid,
        service_pid,
        service_pid,
        "a" * 32,
        ((service_pid, "ps:stable"),),
    )
    capture = CapturedFixtureService(
        43200,
        "ps:launcher",
        ownership,
        SpawnOwnershipProof(ownership, True, ownership.member_identities),
        ((service_pid, ("service",)),),
    )
    active = {"value": True}
    events: list[tuple[str, object]] = []
    retired_ownership = SpawnProcessOwnership(service_pid, service_pid, service_pid, "a" * 32, ())
    retired = CapturedFixtureService(
        43200,
        "ps:launcher",
        retired_ownership,
        SpawnOwnershipProof(retired_ownership, False, ()),
        (),
    )

    class Barrier:
        def __init__(self, identities: Iterable[tuple[int, str]]):
            events.append(("armed", tuple(identities)))

        @property
        def can_wait_exact(self) -> bool:
            return True

        def signal_exact(self, _signum: int, _authorize: Callable[[int, str], bool]) -> tuple[int, ...]:
            events.append(("failed-closed", ()))
            return ()

        def wait(self, timeout: float) -> bool:
            events.append(("waited", timeout))
            active["value"] = False
            return True

        def __enter__(self):
            return self

        def __exit__(self, _error_type, _error, _traceback):
            return None

    monkeypatch.setattr(
        f"{__name__}._refresh_fixture_service_capture",
        lambda _capture: capture if active["value"] else retired,
    )
    monkeypatch.setattr(f"{__name__}._capture_authorizes_member", lambda _capture, _pid, _identity: True)
    monkeypatch.setattr(f"{__name__}.FixtureMemberExitBarrier", Barrier)

    _stop_fixture_services(_build_paths(tmp_path / "kqueue-natural-exit"), captured_services=(capture,))

    assert events[0] == ("armed", ((service_pid, "ps:stable"),))
    assert events[1] == ("failed-closed", ())
    assert events[2][0] == "waited"


def test_o6_fixture_service_teardown_fails_closed_when_group_survives_without_proven_member(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service_pid = 43201
    ownership = SpawnProcessOwnership(service_pid, service_pid, service_pid, "a" * 32, ())
    uncertain = CapturedFixtureService(
        43200,
        "proc:1200",
        ownership,
        SpawnOwnershipProof(ownership, True, ()),
        (),
    )
    monkeypatch.setattr(
        f"{__name__}._signal_fixture_service_members",
        lambda captures, _signal_number, _timeout: tuple(captures),
    )
    monkeypatch.setattr(f"{__name__}._refresh_fixture_service_capture", lambda _capture: uncertain)

    with pytest.raises(AssertionError, match="uncertain-group"):
        _stop_fixture_services(_build_paths(tmp_path / "uncertain-group"), captured_services=(uncertain,))


def test_o6_spawn_registers_cleanup_and_captures_ownership_before_readiness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class Process:
        pid = 43200
        stdout = io.StringIO()

        def poll(self) -> int | None:
            return None

    class Tmux:
        sessions = ("fixture",)
        socket_path = tmp_path / "tmux.sock"

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: Process())
    monkeypatch.setattr(f"{__name__}.server_entrypoint_arguments", lambda _root: ["/fixture/yomux-go"])
    monkeypatch.setattr(f"{__name__}.process_start_identity", lambda _pid: "proc:1200")
    monkeypatch.setattr(
        f"{__name__}._capture_fixture_services",
        lambda _pid, _identity, *, allow_legacy: (
            events.append("capture") or ()
            if allow_legacy is False
            else pytest.fail("current build unexpectedly enabled legacy capture")
        ),
    )

    def fail_readiness(build: RunningBuild) -> None:
        assert registered == [build]
        assert events == ["registered", "capture"]
        raise AssertionError("readiness failed")

    monkeypatch.setattr(f"{__name__}._wait_until_serving", fail_readiness)
    registered: list[RunningBuild] = []

    with pytest.raises(AssertionError, match="readiness failed"):
        _spawn_build(
            "readiness-failure",
            REPO_ROOT,
            _build_paths(tmp_path / "readiness-failure"),
            Tmux(),
            lambda build: (registered.append(build), events.append("registered")),
        )

    assert len(registered) == 1


def test_o6_running_build_factory_cleans_registered_build_after_readiness_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class Build:
        label = "readiness-failure"

        def stop(self) -> None:
            events.append("stopped")

    def fail_after_register(
        _label: str,
        _source_root: Path,
        _paths: BuildPaths,
        _tmux_runtime: object,
        register: Callable[[RunningBuild], None],
        *,
        allow_legacy_service_capture: bool = False,
    ) -> RunningBuild:
        assert allow_legacy_service_capture is False
        register(Build())  # type: ignore[arg-type]
        events.append("registered")
        raise AssertionError("readiness failed")

    monkeypatch.setattr(f"{__name__}._spawn_build", fail_after_register)
    fixture = running_build_factory.__wrapped__(lambda: object(), tmp_path)
    start = next(fixture)

    with pytest.raises(AssertionError, match="readiness failed"):
        start("readiness-failure", REPO_ROOT)
    with pytest.raises(StopIteration):
        next(fixture)

    assert events == ["registered", "stopped", "stopped"]


def test_o6_running_build_factory_never_infers_legacy_capability_from_alternate_source_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    capabilities: list[tuple[str, bool]] = []

    class Build:
        def __init__(self, label: str):
            self.label = label

        def stop(self) -> None:
            pass

    def spawn(
        label: str,
        _source_root: Path,
        _paths: BuildPaths,
        _tmux_runtime: object,
        register: Callable[[RunningBuild], None],
        *,
        allow_legacy_service_capture: bool = False,
    ) -> RunningBuild:
        capabilities.append((label, allow_legacy_service_capture))
        build = Build(label)
        register(build)  # type: ignore[arg-type]
        return build  # type: ignore[return-value]

    monkeypatch.setattr(f"{__name__}._spawn_build", spawn)
    fixture = running_build_factory.__wrapped__(lambda: object(), tmp_path)
    start = next(fixture)
    start("arbitrary-alternate", tmp_path / "alternate-source")
    start("explicit-v0610", tmp_path / "v0610-source", legacy_service_capture=True)
    with pytest.raises(StopIteration):
        next(fixture)

    assert capabilities == [
        ("arbitrary-alternate", False),
        ("explicit-v0610", True),
    ]


def test_o6_running_build_factory_cleans_process_when_start_identity_acquisition_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[tuple[str, object]] = []

    class Process:
        pid = 43200
        stdout = io.StringIO()
        alive = True

        def poll(self) -> int | None:
            return None if self.alive else -signal.SIGINT

        def send_signal(self, signum: int) -> None:
            events.append(("signal", signum))
            self.alive = False

        def communicate(self, timeout: float) -> tuple[str, str]:
            events.append(("communicate", timeout))
            return "", ""

    class Tmux:
        sessions = ("fixture",)
        socket_path = tmp_path / "tmux.sock"

    process = Process()
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(f"{__name__}.server_entrypoint_arguments", lambda _root: ["/fixture/yomux-go"])
    monkeypatch.setattr(f"{__name__}.process_start_identity", lambda _pid: None)
    fixture = running_build_factory.__wrapped__(lambda: Tmux(), tmp_path)
    start = next(fixture)

    with pytest.raises(AssertionError, match="has no stable start identity"):
        start("identity-failure", REPO_ROOT)
    with pytest.raises(StopIteration):
        next(fixture)

    assert events == [
        ("signal", signal.SIGINT),
        ("communicate", SERVER_STOP_TIMEOUT_SECONDS),
    ]


def test_o6_running_build_finalizer_retries_and_cleans_every_build_after_one_stop_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class Build:
        def __init__(self, label: str):
            self.label = label
            self.attempts = 0

        def stop(self) -> None:
            self.attempts += 1
            events.append(f"{self.label}-{self.attempts}")
            if self.label == "second" and self.attempts == 1:
                raise AssertionError("first stop failed")

    def spawn(
        label: str,
        _source_root: Path,
        _paths: BuildPaths,
        _tmux_runtime: object,
        register: Callable[[RunningBuild], None],
        *,
        allow_legacy_service_capture: bool = False,
    ) -> RunningBuild:
        assert allow_legacy_service_capture is False
        build = Build(label)
        register(build)  # type: ignore[arg-type]
        return build  # type: ignore[return-value]

    monkeypatch.setattr(f"{__name__}._spawn_build", spawn)
    fixture = running_build_factory.__wrapped__(lambda: object(), tmp_path)
    start = next(fixture)
    start("first", REPO_ROOT)
    start("second", REPO_ROOT)

    with pytest.raises(AssertionError, match="first stop failed"):
        next(fixture)

    assert events == ["second-1", "first-1", "second-2", "first-2"]


def test_o6_running_build_cleanup_failure_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attempts = 0

    class Process:
        pid = 43200
        stdout = io.StringIO()

        def poll(self) -> int:
            return 0

        def communicate(self, timeout: float) -> tuple[str, str]:
            assert timeout == SERVER_STOP_TIMEOUT_SECONDS
            return "", ""

    class Tmux:
        socket_path = tmp_path / "tmux.sock"

    build = RunningBuild(
        "retry",
        REPO_ROOT,
        _build_paths(tmp_path / "retry"),
        Tmux(),
        43210,
        Process(),
        "proc:1200",
        frozenset(),
    )

    def stop_services(_paths: BuildPaths, *, captured_services: Iterable[CapturedFixtureService]) -> None:
        nonlocal attempts
        assert tuple(captured_services) == ()
        attempts += 1
        if attempts == 1:
            raise AssertionError("late service write")

    monkeypatch.setattr(f"{__name__}.process_start_identity", lambda _pid: None)
    monkeypatch.setattr(f"{__name__}._stop_fixture_services", stop_services)

    with pytest.raises(AssertionError, match="late service write"):
        build.stop()
    assert build.stopped is False
    build.stop()

    assert build.stopped is True
    assert attempts == 2


def test_o6_running_build_stops_registered_server_before_reporting_missing_service_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launcher_pid = 43200
    service_pid = 43201
    launcher_identity = "proc:1200"
    events: list[tuple[str, object]] = []

    class Process:
        pid = launcher_pid
        stdout = io.StringIO()
        alive = True

        def poll(self) -> int | None:
            return None if self.alive else -signal.SIGINT

        def send_signal(self, signum: int) -> None:
            events.append(("signal", signum))
            self.alive = False

        def communicate(self, timeout: float) -> tuple[str, str]:
            events.append(("communicate", timeout))
            return "", ""

    class Tmux:
        socket_path = tmp_path / "tmux.sock"

    command = "python -m yomux_lib.watchd --serve --socket /tmp/exact.sock"
    table = {
        launcher_pid: ProcessTableEntry(1, launcher_pid, 0.0, "python yolomux.py", 1200, launcher_pid, launcher_identity),
        service_pid: ProcessTableEntry(launcher_pid, service_pid, 0.0, command, 1201, service_pid, "proc:1201"),
    }
    build = RunningBuild(
        "missing-generation",
        REPO_ROOT,
        _build_paths(tmp_path / "missing-generation"),
        Tmux(),
        43210,
        Process(),
        launcher_identity,
        frozenset(),
    )

    def start_identity(pid: int) -> str:
        return launcher_identity if pid == launcher_pid else "proc:1201"

    monkeypatch.setattr(f"{__name__}.bounded_process_table", lambda **_kwargs: table)
    # One fixture identity, installed at both readers: capture reads this module's
    # `process_start_identity`, while the signal path reads the owner module's copy.
    monkeypatch.setattr(f"{__name__}.process_start_identity", start_identity)
    monkeypatch.setattr("tests.isolated_dev_server.process_start_identity", start_identity)
    monkeypatch.setattr(f"{__name__}.process_spawn_generation", lambda _pid: None)
    monkeypatch.setattr(f"{__name__}._process_argv", lambda _pid, text: tuple(text.split()))
    monkeypatch.setattr(os, "getpgid", lambda pid: table[pid].pgid)
    monkeypatch.setattr(os, "getsid", lambda pid: table[pid].session_id)

    with pytest.raises(AssertionError, match="has no valid spawn generation"):
        build.stop()

    assert events == [
        ("signal", signal.SIGINT),
        ("communicate", SERVER_STOP_TIMEOUT_SECONDS),
    ]
    assert build.process.poll() == -signal.SIGINT
    assert build.stopped is False


def test_o6_running_build_refuses_server_signal_after_identity_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Process:
        pid = 43200

        def poll(self) -> None:
            return None

    class Tmux:
        socket_path = tmp_path / "tmux.sock"

    class Barrier:
        def __init__(self, identities: Iterable[tuple[int, str]]):
            assert tuple(identities) == ((43200, "proc:1200"),)

        def signal_exact(self, _signal_number: int, authorize: Callable[[int, str], bool]) -> tuple[int, ...]:
            assert authorize(43200, "proc:1200") is False
            return ()

        @property
        def unanchored_identities(self) -> tuple[tuple[int, str], ...]:
            return ((43200, "proc:1200"),)

        def __enter__(self):
            return self

        def __exit__(self, _error_type, _error, _traceback):
            return None

    build = RunningBuild(
        "reused-server",
        REPO_ROOT,
        _build_paths(tmp_path / "reused-server"),
        Tmux(),
        43210,
        Process(),
        "proc:1200",
        frozenset(),
    )
    monkeypatch.setattr("tests.isolated_dev_server.FixtureMemberExitBarrier", Barrier)
    monkeypatch.setattr("tests.isolated_dev_server.process_start_identity", lambda _pid: "proc:reused")

    with pytest.raises(AssertionError, match="identity changed before child signal"):
        build.signal_server(signal.SIGTERM)


def test_o6_running_build_uses_owned_popen_child_when_kernel_signal_handle_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    signals: list[int] = []

    class Process:
        pid = 43200

        def poll(self) -> None:
            return None

        def send_signal(self, signum: int) -> None:
            signals.append(signum)

    class Tmux:
        socket_path = tmp_path / "tmux.sock"

    class Barrier:
        def __init__(self, _identities: Iterable[tuple[int, str]]):
            pass

        def signal_exact(self, _signal_number: int, authorize: Callable[[int, str], bool]) -> tuple[int, ...]:
            assert authorize(43200, "proc:1200") is True
            return ()

        @property
        def unanchored_identities(self) -> tuple[tuple[int, str], ...]:
            return ((43200, "proc:1200"),)

        def __enter__(self):
            return self

        def __exit__(self, _error_type, _error, _traceback):
            return None

    build = RunningBuild(
        "owned-server",
        REPO_ROOT,
        _build_paths(tmp_path / "owned-server"),
        Tmux(),
        43210,
        Process(),
        "proc:1200",
        frozenset(),
    )
    monkeypatch.setattr("tests.isolated_dev_server.FixtureMemberExitBarrier", Barrier)
    monkeypatch.setattr("tests.isolated_dev_server.process_start_identity", lambda _pid: "proc:1200")
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "getsid", lambda pid: pid)
    monkeypatch.setattr(
        os,
        "kill",
        lambda _pid, _signum: (_ for _ in ()).throw(AssertionError("numeric os.kill fallback is unsafe")),
    )

    build.signal_server(signal.SIGTERM)

    assert signals == [signal.SIGTERM]


def test_o6_fixture_service_teardown_arms_exact_members_before_signal_and_waits_for_delayed_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _build_paths(tmp_path / "service-exit-barrier")
    events: list[tuple[str, object]] = []
    ownership = SpawnProcessOwnership(12345, 12345, 12345, "a" * 32, (
        (12345, "proc:111"),
        (12346, "proc:112"),
    ))
    capture = CapturedFixtureService(
        12200,
        "proc:launcher",
        ownership,
        SpawnOwnershipProof(ownership, True, ownership.member_identities),
        ((12345, ("service",)), (12346, ("worker",))),
    )
    retired_ownership = SpawnProcessOwnership(12345, 12345, 12345, "a" * 32, ())
    retired_capture = CapturedFixtureService(
        12200,
        "proc:launcher",
        retired_ownership,
        SpawnOwnershipProof(retired_ownership, False, ()),
        (),
    )
    state = {"active": True}

    class ExitBarrier:
        def __init__(self, identities: Iterable[tuple[int, str]]):
            events.append(("armed", tuple(identities)))

        def wait(self, timeout: float) -> bool:
            events.append(("waited", timeout))
            state["active"] = False
            return True

        def signal_exact(self, signal_number: int, authorize: Callable[[int, str], bool]) -> tuple[int, ...]:
            identities = ((12345, "proc:111"), (12346, "proc:112"))
            assert all(authorize(*identity) for identity in identities)
            events.append(("signal", signal_number))
            return (12345, 12346)

        def close(self) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, _error_type, _error, _traceback):
            self.close()

    monkeypatch.setattr(f"{__name__}.FixtureMemberExitBarrier", ExitBarrier)
    monkeypatch.setattr(
        f"{__name__}._refresh_fixture_service_capture",
        lambda _capture: capture if state["active"] else retired_capture,
    )
    monkeypatch.setattr(f"{__name__}._capture_authorizes_member", lambda _capture, _pid, _identity: True)
    _stop_fixture_services(paths, captured_services=(capture,))

    assert events[0] == ("armed", ((12345, "proc:111"), (12346, "proc:112")))
    assert events[1] == ("signal", signal.SIGTERM)
    assert events[2][0] == "waited"


def test_o6_fixture_service_teardown_never_signals_argv_only_legacy_process_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _build_paths(tmp_path / "legacy-stats-stranger")
    legacy_pid = 43210
    legacy_entry = tmp_path / str(legacy_pid)
    legacy_entry.mkdir()
    legacy_database = paths.state_dir / "stats-v6.sqlite3"
    (legacy_entry / "cmdline").write_bytes(
        b"python3\0-m\0yomux_lib.stats_current.service\0--database\0"
        + os.fsencode(legacy_database)
        + b"\0"
    )
    member_signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        gate_harness_module.os,
        "kill",
        lambda pid, signum: member_signals.append((pid, signum)),
    )

    _stop_fixture_services(paths)

    assert member_signals == []


def test_o6_fixture_service_teardown_never_discovers_records_outside_fixture_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _build_paths(tmp_path / ("long-fixture-root-" + "x" * 120))
    monkeypatch.setattr(
        Path,
        "rglob",
        lambda _path, _pattern: (_ for _ in ()).throw(AssertionError("teardown must not discover service records")),
    )

    _stop_fixture_services(paths)


def test_o7_gate_tests_are_fixture_scoped_and_bootstrap_guard_rejects_escapes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gate_files = sorted((REPO_ROOT / "tests").glob("test_gate_*.py"))
    violations: list[str] = []
    home_method = "home"
    expansion_method = "expand" + "user"
    home_prefix = "~" + "/"
    live_state_suffix = "/" + ".local" + "/state/yolomux"
    for path in gate_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = node.func
                if isinstance(function, ast.Attribute) and function.attr in {home_method, expansion_method}:
                    violations.append(f"{path.name}:{node.lineno}:{function.attr}")
                elif isinstance(function, ast.Name) and function.id == expansion_method:
                    violations.append(f"{path.name}:{node.lineno}:{function.id}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.startswith(home_prefix) or live_state_suffix in node.value:
                    violations.append(f"{path.name}:{node.lineno}:live-state-literal")

    root, bootstrap_paths = bootstrap_writable_paths()
    assert gate_files
    assert not violations, f"gate tests reference operator-owned paths: {violations}"
    assert_writable_paths_beneath(root, bootstrap_paths)

    fixture_state_dir = product_env("STATE_DIR")
    assert fixture_state_dir is not None
    operator_root = Path(os.environ["HOME"]).resolve(strict=False)
    operator_state_dir = (operator_root / ".local" / "state" / "yolomux").resolve(strict=False)
    # Pass the operator path only to the isolation guard so the guard fires
    # before product code or a writable filesystem operation can receive it.
    monkeypatch.setenv("YOMUX_STATE_DIR", str(operator_state_dir))
    escaped_root, escaped_paths = bootstrap_writable_paths()
    with pytest.raises(AssertionError, match="writable paths escape fixture root") as escaped:
        assert_writable_paths_beneath(escaped_root, escaped_paths)
    assert f"YOMUX_STATE_DIR={operator_state_dir}" in str(escaped.value)

    monkeypatch.setenv("YOMUX_STATE_DIR", fixture_state_dir)
    restored_root, restored_paths = bootstrap_writable_paths()
    assert restored_root == root
    assert restored_paths == bootstrap_paths
    assert_writable_paths_beneath(restored_root, restored_paths)

    with pytest.raises(AssertionError, match="writable paths escape fixture root"):
        assert_writable_paths_beneath(tmp_path / "fixture-root", {"state": tmp_path / "outside"})
