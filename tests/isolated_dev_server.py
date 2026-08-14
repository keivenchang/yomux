# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""One isolated YOLOmux dev server: a real `yolomux.py` process a test owns end to end.

WHY THIS EXISTS SEPARATELY FROM `gate_harness.gate_live_server`. `gate_live_server` builds the
app and the HTTP server INSIDE the pytest process. That is the right tool for almost everything,
and it is not the right tool for a restart sequence, for a poisoned service that has to be
self-recovered by a real supervisor, or for "port A's teardown left port B's files untouched" --
all three need a separate OS process with its own lifetime, its own state root, and its own
signal handling.

WHAT ISOLATION MEANS HERE, and every clause is load-bearing:

  * EPHEMERAL PORT. `HttpPortLease.reserve()` with no candidate range binds port 0, so the kernel
    picks it. `7770`-`7773` (and `8880`-`8883` on macOS) are the operator's live servers and an
    automated test must never reach them; `assert_isolated_dev_server_port` refuses them outright
    rather than trusting the kernel to be tactful.
  * PRIVATE TMUX SOCKET. `start_isolated_tmux_runtime` owns a `/tmp/yts-<pid>-<uuid>` socket dir
    and fixture-created `yt-<pid>-<uuid>-N` sessions, so the server can never see, drive, or kill
    a session belonging to the person running the tests.
  * ITS OWN CONFIG AND STATE ROOT, AND ITS OWN `config/auth.yaml`. This is the detail that has
    cost real time twice. A dev server on a non-default port derives a private root under `/tmp`
    (`tools/instance_isolation.resolve_instance_environment`), and this harness derives one
    itself and passes it explicitly. Either way the instance reads the auth config INSIDE that
    root -- NOT `~/.config/yolomux` -- so a browser cookie or session minted against the
    operator's instance authenticates against nothing here. Use `auth_bypass=True` (the default,
    matching the coexistence gate) or write `paths.config_dir / "auth.yaml"` yourself and log in
    through the real form; there is no third option and no cookie to borrow.
  * TORN DOWN CLEANLY. SIGINT, then SIGTERM, then SIGKILL, each signal delivered only to a
    process whose start identity still matches the one captured at spawn, so a recycled PID can
    never be signalled by this fixture. The tmux runtime and the root directory go with it.
"""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from http import HTTPStatus
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest

from tests.gate_harness import FixtureMemberExitBarrier
from tests.gate_harness import HttpPortLease
from tests.gate_harness import run_fixture_cleanup_phases
from tests.tmux_runtime import start_isolated_tmux_runtime
from tests.tmux_runtime import stop_isolated_tmux_runtime
from yolomux_lib.host_identity import process_start_identity


REPO_ROOT = Path(__file__).resolve().parents[1]

SERVER_READY_TIMEOUT_SECONDS = 20.0
SERVER_STOP_TIMEOUT_SECONDS = 10.0

# The operator's live servers, by platform. Never automated, on any lane, for any reason.
FORBIDDEN_LIVE_PORTS = frozenset(range(8880, 8884) if sys.platform == "darwin" else range(7770, 7774))


@dataclass(frozen=True)
class BuildPaths:
    """Every writable root one isolated instance owns. Nothing it writes may escape `root`."""

    root: Path
    runtime_dir: Path
    home_dir: Path
    config_dir: Path
    state_dir: Path
    cache_dir: Path
    codex_home: Path
    tmp_dir: Path
    start_lock_path: Path
    tool_lock_path: Path
    ca_dir: Path
    log_dir: Path
    workspace_dir: Path

    @property
    def auth_config_path(self) -> Path:
        """This instance's OWN auth config. A cookie from `~/.config/yolomux` is meaningless here."""

        return self.config_dir / "auth.yaml"


def build_paths(root: Path, *, state_dir: Path | None = None) -> BuildPaths:
    paths = BuildPaths(
        root=root,
        runtime_dir=root / "runtime",
        home_dir=root / "home",
        config_dir=root / "config",
        state_dir=state_dir or root / "state",
        cache_dir=root / "cache",
        codex_home=root / "codex-home",
        tmp_dir=root / "tmp",
        start_lock_path=root / "locks" / "start.lock",
        tool_lock_path=root / "locks" / "expensive-tools.lock",
        ca_dir=root / "ca",
        log_dir=root / "logs",
        workspace_dir=root / "workspaces",
    )
    for directory in (
        paths.home_dir,
        paths.runtime_dir,
        paths.config_dir,
        paths.state_dir,
        paths.cache_dir,
        paths.codex_home,
        paths.tmp_dir,
        paths.start_lock_path.parent,
        paths.ca_dir,
        paths.log_dir,
        paths.workspace_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def build_environment(
    source_root: Path,
    paths: BuildPaths,
    tmux_runtime: Any,
    port: int,
    *,
    auth_bypass: bool = True,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """The complete environment one isolated instance runs under.

    Every writable location the product consults is named here explicitly. That is deliberate:
    `tools/instance_isolation.resolve_instance_environment` NO-OPS when `YOLOMUX_ROOT` or any of
    the four directory variables is already set, so a partially specified environment would leave
    the instance deriving some paths itself and inheriting others from the operator's session.
    """

    env = dict(os.environ)
    env.update(
        {
            "HOME": str(paths.home_dir),
            "TMPDIR": str(paths.tmp_dir),
            "XDG_CONFIG_HOME": str(paths.root / "xdg-config"),
            "XDG_STATE_HOME": str(paths.root / "xdg-state"),
            "XDG_CACHE_HOME": str(paths.root / "xdg-cache"),
            "YOLOMUX_CONFIG_DIR": str(paths.config_dir),
            "YOLOMUX_STATE_DIR": str(paths.state_dir),
            "YOLOMUX_RUNTIME_DIR": str(paths.runtime_dir),
            "YOLOMUX_CACHE_DIR": str(paths.cache_dir),
            "YOLOMUX_CODEX_HOME": str(paths.codex_home),
            "CODEX_HOME": str(paths.codex_home),
            "YOLOMUX_START_LOCK_DIR": str(paths.start_lock_path),
            "YOLOMUX_TOOL_LOCK_PATH": str(paths.tool_lock_path),
            "YOLOMUX_CA_DIR": str(paths.ca_dir),
            "YOLOMUX_LOG_DIR": str(paths.log_dir),
            "YOLOMUX_WORKSPACE_BASE": str(paths.workspace_dir),
            "YOLOMUX_TMUX_SOCKET": str(tmux_runtime.socket_path),
            "YOLOMUX_TEST_AUTH_BYPASS": "1" if auth_bypass else "0",
            "YOLOMUX_LOCAL_SERVICE_IDLE_SECONDS": "0.2",
            "YOLOMUX_STARTUP_WATCHDOG_SECONDS": "0",
            "YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT": str(port),
            "PYTHONPATH": str(source_root),
            "PYTHONUNBUFFERED": "1",
        }
    )
    if not auth_bypass:
        env.pop("YOLOMUX_TEST_AUTH_BYPASS", None)
    # Applied last so a caller can adjust otherwise-fixed isolation knobs -- e.g. a longer service
    # idle so shared daemons survive a web restart, the way they do at the production 60 s idle.
    if env_overrides:
        env.update(env_overrides)
    return env


def assert_isolated_dev_server_port(port: int) -> int:
    """Refuse the operator's live ports before anything binds or connects."""

    assert port not in FORBIDDEN_LIVE_PORTS, (
        f"isolated dev server refused port {port}: {sorted(FORBIDDEN_LIVE_PORTS)} are the live "
        "operator servers and are never automated"
    )
    assert port > 0, port
    return port


def wait_until_serving(
    process: subprocess.Popen[str],
    port: int,
    output: list[str],
    *,
    label: str = "isolated dev server",
    timeout_seconds: float = SERVER_READY_TIMEOUT_SECONDS,
) -> None:
    """Block until the server prints its own serving line, or fail with what it printed instead.

    Readiness is the product's own statement that it is bound and serving. Polling the port would
    accept a socket the kernel holds while startup is still failing, which is exactly the
    "alive is not serving" case this harness exists to avoid.
    """

    stdout = process.stdout
    assert stdout is not None
    expected = f"Serving YOLOmux on http://127.0.0.1:{port}/"
    deadline = time.monotonic() + timeout_seconds
    with selectors.DefaultSelector() as selector:
        selector.register(stdout, selectors.EVENT_READ)
        while True:
            exit_code = process.poll()
            if exit_code is not None:
                remainder = stdout.read()
                if remainder:
                    output.extend(remainder.splitlines())
                raise AssertionError(
                    f"{label} exited before serving with {exit_code}: " + "\n".join(output[-20:])
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"{label} did not serve within {timeout_seconds}s: " + "\n".join(output[-20:])
                )
            if not selector.select(remaining):
                continue
            line = stdout.readline()
            if not line:
                continue
            output.append(line.rstrip("\n"))
            if expected in line:
                return


def signal_server_exactly(
    process: subprocess.Popen[str],
    server_start_identity: str,
    signal_number: int,
    *,
    label: str = "isolated dev server",
) -> None:
    """Deliver one signal to this exact process, never to whatever now holds its PID.

    The PID alone is not an identity. `FixtureMemberExitBarrier.signal_exact` re-reads the start
    identity under the barrier and refuses to signal anything whose identity moved, and the
    `Popen.send_signal` fallback is safe for the same reason in the other direction: Popen owns
    this unreaped direct child, so its internal waitpid fence cannot adopt a PID reuse.
    """

    if process.poll() is not None:
        return
    if not server_start_identity:
        process.send_signal(signal_number)
        return
    identity = (process.pid, server_start_identity)
    with FixtureMemberExitBarrier((identity,)) as barrier:
        sent = barrier.signal_exact(
            signal_number,
            lambda pid, start_identity: (
                pid == process.pid
                and start_identity == server_start_identity
                and process_start_identity(pid) == start_identity
                and os.getpgid(pid) == pid
                and os.getsid(pid) == pid
            ),
        )
        unanchored = barrier.unanchored_identities
    if not sent and unanchored == (identity,) and process.poll() is None:
        if process_start_identity(process.pid) != server_start_identity:
            raise AssertionError(f"{label} server {process.pid} identity changed before child signal")
        process.send_signal(signal_number)
        sent = (process.pid,)
    if not sent and process.poll() is None:
        raise AssertionError(
            f"{label} server {process.pid} could not be signaled through its exact process identity"
        )


@dataclass
class IsolatedDevServer:
    """A running `yolomux.py` process plus everything a test needs to talk to it and end it."""

    label: str
    source_root: Path
    paths: BuildPaths
    tmux: Any
    port: int
    process: subprocess.Popen[str]
    server_start_identity: str
    auth_bypass: bool = True
    env_overrides: dict[str, str] | None = None
    output: list[str] = field(default_factory=list)
    stopped: bool = False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def request(self, path: str, *, timeout: float = 5.0) -> tuple[int, dict[str, str], bytes]:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return int(response.status), dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def assert_serving(self) -> None:
        status, _headers, body = self.request("/api/ping")
        assert status == HTTPStatus.OK, (self.label, status, body, self.output[-20:])
        assert self.process.poll() is None, (self.label, self.output[-20:])

    def stop(self) -> None:
        """SIGINT, then SIGTERM, then SIGKILL -- each one identity-checked, none of them optional."""

        if self.stopped:
            return

        def stop_server() -> None:
            if self.process.poll() is None:
                signal_server_exactly(self.process, self.server_start_identity, signal.SIGINT, label=self.label)
            for escalation in (signal.SIGTERM, signal.SIGKILL):
                try:
                    remaining, _stderr = self.process.communicate(timeout=SERVER_STOP_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    signal_server_exactly(self.process, self.server_start_identity, escalation, label=self.label)
                    continue
                if remaining:
                    self.output.extend(remaining.splitlines())
                return
            remaining, _stderr = self.process.communicate(timeout=SERVER_STOP_TIMEOUT_SECONDS)
            if remaining:
                self.output.extend(remaining.splitlines())

        run_fixture_cleanup_phases(f"{self.label} isolated dev server", (("stop-server", stop_server),))
        self.stopped = True

    def restart(self, *, sessions: tuple[str, ...] = ()) -> None:
        """Stop this process and start a fresh one on the SAME port, root, and tmux runtime.

        This is the web restart the restart-sequence acceptance work drives: a new OS process
        binds the exact port the old one just freed and re-reads the retained state under the same
        root, so `<root>/state/backend-health/<port>.json` continuity (same observer epoch, a
        higher revision, a new writer pid) is a claim a test can actually make. Nothing about the
        instance's isolation changes -- same private tmux socket, same config/state/HOME root.
        """

        self.stop()
        replacement = start_isolated_dev_server(
            self.label,
            self.source_root,
            self.paths,
            self.tmux,
            auth_bypass=self.auth_bypass,
            sessions=sessions,
            port=self.port,
            env_overrides=self.env_overrides,
        )
        # Adopt the replacement process; the fixture finalizer stops `self`, so the live process
        # must be the one it signals.
        self.process = replacement.process
        self.server_start_identity = replacement.server_start_identity
        self.output.extend(replacement.output)
        self.stopped = False


def start_isolated_dev_server(
    label: str,
    source_root: Path,
    paths: BuildPaths,
    tmux_runtime: Any,
    *,
    auth_bypass: bool = True,
    sessions: tuple[str, ...] = (),
    port: int | None = None,
    env_overrides: dict[str, str] | None = None,
    exec_plan_json: str | None = None,
) -> IsolatedDevServer:
    if port is None:
        lease = HttpPortLease.reserve()
        port = assert_isolated_dev_server_port(lease.port)
        # The lease holds the port against every other worker until the instant before the server
        # binds it, and only then is it handed over.
        release_lease: Callable[[], None] = lease.release
    else:
        # Restart-on-the-same-port: the caller owns a port a just-stopped instance freed. There is
        # no lease to release, only the same isolation guard so a restart can never target a live
        # operator server either.
        port = assert_isolated_dev_server_port(port)
        release_lease = lambda: None
    session_names = sessions or tuple(tmux_runtime.sessions)
    command = [
        sys.executable,
        "-u",
        str(source_root / "yolomux.py"),
        "--http",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--sessions",
        session_names[0],
    ]
    if exec_plan_json is not None:
        # Launch through the real per-row exec mode the supported launcher uses:
        # `instance_isolation.py exec --plan-file <p> -- <server cmd>` applies the
        # captured RowPlan to the environment, then runs the server under it. The
        # plan lives inside this instance's own root so it cannot escape isolation.
        plan_path = paths.root / "row-plan.json"
        plan_path.write_text(exec_plan_json, encoding="utf-8")
        command = [
            sys.executable,
            str(source_root / "tools" / "instance_isolation.py"),
            "exec",
            "--plan-file",
            str(plan_path),
            "--",
            *command,
        ]
    release_lease()
    process = subprocess.Popen(
        command,
        cwd=source_root,
        env=build_environment(
            source_root, paths, tmux_runtime, port, auth_bypass=auth_bypass, env_overrides=env_overrides
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    server_start_identity = process_start_identity(process.pid)
    assert server_start_identity, f"{label} server process has no stable start identity"
    server = IsolatedDevServer(
        label=label,
        source_root=source_root,
        paths=paths,
        tmux=tmux_runtime,
        port=port,
        process=process,
        server_start_identity=server_start_identity,
        auth_bypass=auth_bypass,
        env_overrides=env_overrides,
    )
    wait_until_serving(process, port, server.output, label=label)
    return server


def pid_is_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _proc_ppid(pid: int) -> int:
    """Parent pid from /proc/<pid>/stat, parsed past a comm that may contain spaces or parens."""

    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError):
        return 0
    tail = stat.rpartition(")")[2].split()
    return int(tail[1]) if len(tail) >= 2 and tail[1].lstrip("-").isdigit() else 0


def process_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, OSError):
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()


def process_descendants(root_pid: int) -> list[tuple[int, str, str]]:
    """Every live descendant of `root_pid`, as (pid, start_identity, cmdline), captured from /proc.

    Capture this WHILE the web server is still alive: the service daemons reparent to init the moment
    the server exits, so their ppid link to this instance is only readable before it stops. The start
    identity is recorded here so a later reap can refuse a recycled pid.
    """

    children: dict[int, list[int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        parent = _proc_ppid(pid)
        if parent:
            children.setdefault(parent, []).append(pid)

    descendants: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    stack = list(children.get(root_pid, ()))
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        identity = process_start_identity(pid) or ""
        if identity:
            descendants.append((pid, identity, process_cmdline(pid)))
        stack.extend(children.get(pid, ()))
    return descendants


def reap_descendants(descendants: Iterable[tuple[int, str, str]]) -> list[int]:
    """Signal each captured descendant through its EXACT (pid, start identity), then wait it out.

    A recycled pid whose start identity no longer matches the one captured is left untouched.
    """

    captured = list(descendants)
    reaped: list[int] = []
    for escalation in (signal.SIGTERM, signal.SIGKILL):
        pending = [
            (pid, identity)
            for pid, identity, _cmdline in captured
            if pid_is_alive(pid) and process_start_identity(pid) == identity
        ]
        if not pending:
            break
        for pid, _identity in pending:
            try:
                os.kill(pid, escalation)
            except (ProcessLookupError, PermissionError):
                continue
            reaped.append(pid)
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and any(
            pid_is_alive(pid) and process_start_identity(pid) == identity for pid, identity in pending
        ):
            time.sleep(0.2)
    return sorted(set(reaped))


def stop_and_reap_daemons(server: IsolatedDevServer) -> list[int]:
    """Stop the web server AND reap every per-instance service daemon it spawned.

    statsd and its siblings are shared daemons that OUTLIVE the web process by design -- which is why
    backend-health history survives a web restart. So a full instance teardown is not "the web process
    exited": the daemons have to be reaped too, or a still-running statsd keeps writing its database
    (and checkpoints its WAL on exit) and the state directory is never quiescent. Descendants are
    captured while the server is still alive, then signalled through their exact identities.
    """

    descendants = process_descendants(server.process.pid) if Path("/proc").is_dir() else []
    server.stop()
    return reap_descendants(descendants)


@pytest.fixture
def isolated_dev_server_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterable[Callable[..., IsolatedDevServer]]:
    """Start one or more isolated dev servers; every one is stopped before the fixture returns.

    More than one is the point for the teardown-isolation work: two instances started from this
    factory share nothing -- not a port, not a tmux socket, not a directory -- so "stopping A left
    B's files untouched" is a claim a test can actually make about them.
    """

    servers: list[IsolatedDevServer] = []
    runtimes: list[Any] = []

    def start(
        label: str = "isolated",
        *,
        source_root: Path = REPO_ROOT,
        state_dir: Path | None = None,
        auth_bypass: bool = True,
        env_overrides: dict[str, str] | None = None,
    ) -> IsolatedDevServer:
        root = tmp_path / f"runtime-{len(servers) + 1}-{label}"
        paths = build_paths(root, state_dir=state_dir)
        tmux_runtime = start_isolated_tmux_runtime(monkeypatch, root, session_count=1)
        runtimes.append(tmux_runtime)
        server = start_isolated_dev_server(
            label,
            source_root,
            paths,
            tmux_runtime,
            auth_bypass=auth_bypass,
            env_overrides=env_overrides,
        )
        servers.append(server)
        return server

    try:
        yield start
    finally:
        ordered = tuple(reversed(servers))
        run_fixture_cleanup_phases(
            "isolated dev servers",
            tuple((f"{server.label}-stop", server.stop) for server in ordered)
            + tuple((f"{server.label}-retry", server.stop) for server in ordered)
            + tuple(
                (f"tmux-{index}", lambda runtime=runtime: stop_isolated_tmux_runtime(runtime))
                for index, runtime in enumerate(reversed(runtimes))
            ),
        )


@pytest.fixture
def isolated_dev_server(
    isolated_dev_server_factory: Callable[..., IsolatedDevServer],
) -> IsolatedDevServer:
    return isolated_dev_server_factory("isolated")
