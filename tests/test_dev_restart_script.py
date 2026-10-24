from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path

import pytest

from tests.helpers.fixture_tools import clear_fixture_settings, fixture_executable
from tests.isolated_dev_server import go_test_server_binary
from tools.instance_isolation import synthetic_test_environment
from tools.product_environment import product_env_names
from tools.tmux_paths import short_temp_base, short_temp_prefix


ROOT = Path(__file__).resolve().parents[1]
STARTUP_COMMON = ROOT / "tools" / "startup_common.sh"


@pytest.fixture(scope="module")
def native_binary():
    return go_test_server_binary(ROOT)


@pytest.fixture(autouse=True)
def private_launcher_settings(monkeypatch, native_binary):
    clear_fixture_settings(monkeypatch, "GO_BINARY")
    monkeypatch.setenv("YOMUX_GO_BINARY", str(native_binary))


def printable_command_text(output: str) -> str:
    return output.replace("\\ ", " ")


def test_startup_load_wait_compatibility_helper_is_nonblocking():
    env = {**os.environ, "YOMUX_START_LOAD_WAIT_SECONDS": "0"}
    result = subprocess.run(
        [fixture_executable("bash"), "-c", 'source "$1"; yomux_wait_for_system_capacity false', "bash", str(STARTUP_COMMON)],
        capture_output=True,
        text=True,
        env=env,
        timeout=2,
        check=False,
    )
    assert result.returncode == 0
    assert "startup CPU/memory admission disabled" in result.stdout


def boot_function_source(name: str) -> str:
    lines = (ROOT / "boot.sh").read_text(encoding="utf-8").splitlines()
    start = lines.index(f"{name}() {{")
    end = next(index for index in range(start + 1, len(lines)) if lines[index] == "}")
    return "\n".join(lines[start : end + 1])


@pytest.mark.parametrize("http_mode,scheme", [(0, "https"), (1, "http")])
@pytest.mark.parametrize("host,probe_host", [
    ("0.0.0.0", "localhost"), ("::", "localhost"),
    ("127.0.0.2", "127.0.0.2"), ("192.0.2.9", "192.0.2.9"), ("::1", "[::1]"),
])
def test_boot_readiness_and_stability_probe_the_selected_protocol(tmp_path, http_mode, scheme, host, probe_host):
    probes = tmp_path / "probes.log"
    functions = "\n".join(boot_function_source(name) for name in (
        "probe_healthz", "listener_pid_is_forbidden", "wait_for_port", "verify_port_stable",
    ))
    script = f"set -eu\nhttp_mode={http_mode}\nhost={host}\n" + functions + r'''
port_listener_pids() { printf '12345\n'; }
sleep() { :; }
curl() {
  local url="${@: -1}"
  printf '%s\n' "$url" >> "$PROBE_LOG"
  if [[ "$url" == "$EXPECTED_PROBE_URL" ]]; then printf 200; else printf 000; fi
}
wait_for_port 48123
verify_port_stable 48123
'''
    expected_url = f"{scheme}://{probe_host}:48123/healthz"
    result = subprocess.run(
        [fixture_executable("bash"), "-c", script],
        env={**os.environ, "PROBE_LOG": str(probes), "EXPECTED_PROBE_URL": expected_url},
        capture_output=True, text=True, timeout=5, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert probes.read_text().splitlines() == [expected_url] * 5


def test_boot_health_probe_bounds_stalled_http_peer():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    release_peer = threading.Event()

    def hold_connection():
        connection, _ = listener.accept()
        with connection:
            release_peer.wait(timeout=5)

    peer = threading.Thread(target=hold_connection, daemon=True)
    peer.start()
    functions = boot_function_source("probe_healthz")
    script = f"set -eu\nhttp_mode=1\nhost=127.0.0.1\n{functions}\nprobe_healthz {port}\n"
    started = time.monotonic()
    try:
        result = subprocess.run(
            [fixture_executable("bash"), "-c", script],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    finally:
        release_peer.set()
        listener.close()
        peer.join(timeout=1)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "000"
    assert time.monotonic() - started < 2.75


def test_boot_print_command_preserves_explicit_certificate_pair(tmp_path):
    cert = tmp_path / "certificate.pem"
    key = tmp_path / "key.pem"
    cert.write_text("certificate fixture", encoding="utf-8")
    key.write_text("key fixture", encoding="utf-8")
    result = subprocess.run(
        [str(ROOT / "boot.sh"), "--print-command", "--cert", cert.name, "--key", key.name, "48123"],
        cwd=tmp_path,
        env=os.environ,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    command = printable_command_text(result.stdout)
    assert f"--cert {cert}" in command
    assert f"--key {key}" in command
    assert "--self-signed" not in command


@pytest.mark.parametrize("case", ["missing-cert", "missing-key", "unreadable-pair"])
def test_boot_rejects_invalid_certificate_pair_before_log_or_listener_mutation(tmp_path, case):
    cert = tmp_path / "certificate.pem"
    key = tmp_path / "key.pem"
    log_dir = tmp_path / "logs"
    options = ["--cert", str(cert), "--key", str(key)]
    if case == "missing-cert":
        options = ["--key", str(key)]
    elif case == "missing-key":
        options = ["--cert", str(cert)]
    result = subprocess.run(
        [str(ROOT / "boot.sh"), *options, "--log-dir", str(log_dir), "48123"],
        cwd=tmp_path,
        env=os.environ,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "certificate" in result.stderr or "--cert and --key" in result.stderr
    assert not log_dir.exists()


def test_boot_print_command_refuses_relative_log_dir_without_writing(tmp_path):
    result = subprocess.run(
        [str(ROOT / "boot.sh"), "--print-command", "--log-dir", "relative-logs", "17775"],
        cwd=tmp_path,
        env=os.environ,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "YOMUX_LOG_DIR" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_boot_refuses_an_existing_listener_before_detached_launch():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        with tempfile.TemporaryDirectory(prefix=short_temp_prefix("yolo-boot-"), dir=short_temp_base()) as workspace:
            workspace_path = Path(workspace)
            root = workspace_path / "root"
            temp_dir = workspace_path / "tmp"
            log_dir = workspace_path / "logs"
            temp_dir.mkdir()
            env = dict(os.environ)
            env.update(
                {
                    "YOMUX_ROOT": str(root),
                    "YOMUX_LOG_DIR": str(log_dir),
                    "TMPDIR": str(temp_dir),
                    "PYTHON": sys.executable,
                }
            )
            for suffix in (
                "CONFIG_DIR",
                "STATE_DIR",
                "RUNTIME_DIR",
                "CACHE_DIR",
                "CODEX_HOME",
                "START_LOCK_DIR",
                "TOOL_LOCK_PATH",
                "CA_DIR",
                "WORKSPACE_BASE",
            ):
                for key in product_env_names(suffix):
                    env.pop(key, None)
            env.pop("CODEX_HOME", None)
            env.pop("PYTHONPYCACHEPREFIX", None)
            result = subprocess.run(
                [
                    str(ROOT / "boot.sh"),
                    "--ignore-load",
                    "--no-dev",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=ROOT,
                env=env,
                check=False,
                text=True,
                capture_output=True,
                timeout=5,
            )
    finally:
        listener.close()

    assert result.returncode == 2
    assert f"port {port} already has listener(s)" in result.stderr
    assert "--force" in result.stderr


def test_boot_surfaces_same_root_duplicate_without_waiting_for_port_timeout():
    with tempfile.TemporaryDirectory(prefix=short_temp_prefix("yolo-boot-root-"), dir=short_temp_base()) as workspace:
        workspace_path = Path(workspace)
        root = workspace_path / "root"
        ready = workspace_path / "ready"
        temp_dir = root / "tmp"
        port_socket = socket.socket()
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
        port_socket.close()
        owner_code = (
            "import fcntl,sys,time; "
            "from pathlib import Path; "
            "root=Path(sys.argv[1]); "
            "paths=[root/name for name in ('config','state','cache','runtime')]; "
            "[path.mkdir(parents=True) for path in paths]; "
            "leases=[(path/'instance.lock').open('wb') for path in paths]; "
            "[fcntl.flock(lease,fcntl.LOCK_EX) for lease in leases]; "
            "Path(sys.argv[2]).write_text('ready'); "
            "time.sleep(60)"
        )
        owner = subprocess.Popen(
            [sys.executable, "-c", owner_code, str(root), str(ready)],
            cwd=ROOT,
            env={**os.environ, "PYTHONPYCACHEPREFIX": ""},
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                assert owner.poll() is None
                time.sleep(0.02)
            assert ready.exists()
            owned_before = {
                str(path.relative_to(root)): (path.stat().st_mode, path.read_bytes() if path.is_file() else None)
                for directory in (root / "config", root / "state", root / "cache", root / "runtime")
                for path in (directory, *directory.rglob("*"))
            }
            imported_app = workspace_path / "imported-app"
            (workspace_path / "sitecustomize.py").write_text(
                "import sys\n"
                "from pathlib import Path\n"
                "class AppImportSentinel:\n"
                "    def find_spec(self, fullname, path=None, target=None):\n"
                "        if fullname == 'yomux_lib.app':\n"
                f"            Path({str(imported_app)!r}).write_text('imported')\n"
                "            raise RuntimeError('duplicate root reached expensive app import')\n"
                "sys.meta_path.insert(0, AppImportSentinel())\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env.update(
                {
                    "YOMUX_ROOT": str(root),
                    "YOMUX_LOG_DIR": str(root / "logs"),
                    "TMPDIR": str(temp_dir),
                    "PYTHONPATH": os.pathsep.join((str(workspace_path), str(ROOT))),
                }
            )
            for suffix in (
                "CONFIG_DIR",
                "STATE_DIR",
                "RUNTIME_DIR",
                "CACHE_DIR",
                "CODEX_HOME",
                "START_LOCK_DIR",
                "TOOL_LOCK_PATH",
                "CA_DIR",
                "WORKSPACE_BASE",
            ):
                for key in product_env_names(suffix):
                    env.pop(key, None)
            env.pop("CODEX_HOME", None)
            env.pop("PYTHONPYCACHEPREFIX", None)
            result = subprocess.run(
                [
                    str(ROOT / "boot.sh"),
                    "--ignore-load",
                    "--no-dev",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=ROOT,
                env=env,
                check=False,
                text=True,
                capture_output=True,
                timeout=5,
            )
            assert not imported_app.exists(), "duplicate root was checked after importing the application"
            owned_after = {
                str(path.relative_to(root)): (path.stat().st_mode, path.read_bytes() if path.is_file() else None)
                for directory in (root / "config", root / "state", root / "cache", root / "runtime")
                for path in (directory, *directory.rglob("*"))
            }
            assert owned_after.keys() == owned_before.keys(), "refused startup changed an owned product tree"
            assert all(owned_after[path] == before for path, before in owned_before.items()), "refused startup changed owned file bytes or modes"
        finally:
            if owner.poll() is None:
                owner.terminate()
            owner.wait(timeout=5)

    assert result.returncode != 0
    assert "product root is already owned" in result.stderr
    assert "restarted port" not in result.stdout


def test_startup_listener_boundary_returns_the_exact_owned_listener_pid(tmp_path):
    """The boundary must print the real owner of a real listener, exactly.

    An earlier revision accepted `returncode in {0, 2}`, which passes whether the boundary works
    or refuses everything - and it did refuse everything on a shared host while staying green.
    This binds one fixture-owned loopback listener on a kernel-assigned port to its exact PID.
    """

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    assert not 7110 <= port <= 7113, port
    try:
        result = subprocess.run(
            [fixture_executable("bash"), "-c", f'source "$1"; yomux_port_listener_pids {port}',
             "listener-probe", str(STARTUP_COMMON)],
            env={**{key: value for key, value in os.environ.items() if not key.startswith("YOLOMUX_") and key != "YOMUX_ENVIRONMENT_LEGACY_INPUT"}, "PYTHON": sys.executable},
            text=True,
            capture_output=True,
        )
    finally:
        listener.close()

    assert result.returncode == 0, (result.returncode, result.stderr[:400])
    assert result.stdout == f"{os.getpid()}\n", (result.stdout, result.stderr[:400])
    assert result.stderr == "", result.stderr[:400]


def test_startup_unique_listener_gate_propagates_the_exact_owner(tmp_path):
    """`yomux_unique_listener_pid` is the shell-side exact-one gate; prove it passes the PID."""

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    assert not 7110 <= port <= 7113, port
    try:
        result = subprocess.run(
            [fixture_executable("bash"), "-c", f'source "$1"; yomux_unique_listener_pid {port}',
             "listener-probe", str(STARTUP_COMMON)],
            env={**os.environ, "PYTHON": sys.executable},
            text=True,
            capture_output=True,
        )
    finally:
        listener.close()

    assert result.returncode == 0, (result.returncode, result.stderr[:400])
    assert result.stdout.strip() == str(os.getpid()), (result.stdout, result.stderr[:400])


def test_startup_listener_boundary_propagates_an_absent_listener_exactly(tmp_path):
    """A port with no listener is a typed refusal with a nonzero exit, not an empty success."""

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()
    assert not 7110 <= free_port <= 7113, free_port

    result = subprocess.run(
        [fixture_executable("bash"), "-c", f'source "$1"; yomux_unique_listener_pid {free_port}',
         "listener-probe", str(STARTUP_COMMON)],
        env={**os.environ, "PYTHON": sys.executable},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr[:400])
    assert "expected one listener" in result.stderr
    assert len(result.stderr) < 2000, len(result.stderr)


def _run_instance_preflight(row_plan_file, port=8881):
    """Drive the real production preflight exactly as `boot.sh` does.

    `yomux_validate_instance_isolation` is called unconditionally at `boot.sh:483`, outside the
    `uname -s == Darwin` gate, so this is the one half of the retained-tmux-environment fix that
    executes on every platform. Setting `YOLOMUX_ROW_PLAN_FILE` here reproduces exactly what a
    long-lived tmux server leaks into a session created from a clean shell.
    """
    env = {**os.environ}
    for key in ("YOLOMUX_ROOT", "YOLOMUX_ROW_PLAN_FILE"):
        env.pop(key, None)
    if row_plan_file is not None:
        env["YOLOMUX_ROW_PLAN_FILE"] = str(row_plan_file)
    return subprocess.run(
        [
            fixture_executable("bash"),
            "-c",
            f'source "$1"; yomux_validate_instance_isolation "$2" "$3" {port}',
            "instance-preflight",
            str(STARTUP_COMMON),
            str(ROOT),
            sys.executable,
        ],
        env=env,
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    ("name", "write"),
    [
        ("missing", None),
        ("not-json", lambda path: path.write_text("this is not a row plan", encoding="utf-8")),
        ("wrong-shape", lambda path: path.write_text(json.dumps({"assign": {}}), encoding="utf-8")),
        ("unset-not-strings", lambda path: path.write_text(json.dumps({"unset": [1], "assign": {}}), encoding="utf-8")),
    ],
)
def test_a_stale_row_plan_file_is_refused_by_the_preflight_instead_of_silently_accepted(tmp_path, name, write):
    """The Linux-live half of the retained-tmux-environment fix, which had no regression at all.

    Before the fix `yomux_validate_instance_isolation` never read `YOLOMUX_ROW_PLAN_FILE`, so a
    server born from a poisoned tmux server started with a stale root and rc=0. Each row here is a
    plan a retained environment can actually point at: a path that no longer exists, and three that
    exist but cannot be a plan. All four must fail CLOSED with the typed message, not be ignored.
    """
    plan_file = tmp_path / f"{name}-row-plan.json"
    if write is not None:
        write(plan_file)

    result = _run_instance_preflight(plan_file)

    assert result.returncode == 2, f"stale row plan was ACCEPTED\nstdout={result.stdout}\nstderr={result.stderr}"
    assert result.stderr.strip(), "invalid plan lacked an actionable error"


def test_a_valid_row_plan_file_is_still_accepted_so_the_refusal_is_not_blanket(tmp_path):
    """Negative control for the row above: refusing every plan would pass that test and be a defect."""
    plan = subprocess.run(
        [os.environ["YOMUX_GO_BINARY"], "--operator", "plan-direct", "--root", str(ROOT), "--port", "8881"],
        env={key: value for key, value in os.environ.items() if key not in {"YOLOMUX_ROOT", "YOLOMUX_ROW_PLAN_FILE"}},
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert json.loads(plan).keys() == {"unset", "assign"}
    plan_file = tmp_path / "valid-row-plan.json"
    plan_file.write_text(plan, encoding="utf-8")

    result = _run_instance_preflight(plan_file)

    assert result.returncode == 0, result.stderr
    assert "invalid row plan" not in result.stderr


def test_shared_start_lock_rejects_concurrent_launcher_and_releases_cleanly(tmp_path):
    lock_dir = tmp_path / "start.lock"
    holder = subprocess.Popen(
        [
            fixture_executable("bash"),
            "-c",
            'source "$1"; export YOMUX_START_LOCK_DIR="$2"; trap yomux_release_start_lock EXIT; yomux_acquire_start_lock; echo ready; read -r _',
            "startup-lock-holder",
            str(STARTUP_COMMON),
            str(lock_dir),
        ],
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ready"

    contender = subprocess.run(
        [
            fixture_executable("bash"),
            "-c",
            'source "$1"; export YOMUX_START_LOCK_DIR="$2"; yomux_acquire_start_lock',
            "startup-lock-contender",
            str(STARTUP_COMMON),
            str(lock_dir),
        ],
        text=True,
        capture_output=True,
    )
    assert contender.returncode != 0
    assert "another YOmux stack start is already in progress" in contender.stderr

    assert holder.stdin is not None
    holder.stdin.close()
    assert holder.wait(timeout=5) in {0, 1}
    assert lock_dir.exists() is False


def test_shared_start_lock_recovers_dead_owner(tmp_path):
    lock_dir = tmp_path / "start.lock"
    lock_dir.mkdir()
    (lock_dir / "pid").write_text("99999999\n", encoding="utf-8")

    result = subprocess.run(
        [
            fixture_executable("bash"),
            "-c",
            'source "$1"; export YOMUX_START_LOCK_DIR="$2"; yomux_acquire_start_lock; yomux_release_start_lock',
            "startup-lock-stale",
            str(STARTUP_COMMON),
            str(lock_dir),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert lock_dir.exists() is False


def test_shared_start_lock_reentry_requires_same_shell_owner(tmp_path):
    lock_dir = tmp_path / "start.lock"
    probe = r'''
set -e
source "$1"
export YOMUX_START_LOCK_DIR="$2"
trap yomux_release_start_lock EXIT
yomux_acquire_start_lock
export YOLOMUX_START_LOCK_ACQUIRED
yomux_acquire_start_lock
if (yomux_acquire_start_lock); then
  exit 8
fi
(yomux_release_start_lock)
test "$(cat "$2/pid")" = "$BASHPID"
if bash -c 'source "$1"; yomux_acquire_start_lock' contender "$1"; then
  exit 9
fi
test "$(cat "$2/pid")" = "$$"
'''
    result = subprocess.run(
        [fixture_executable("bash"), "-c", probe, "startup-lock-reentry", str(STARTUP_COMMON), str(lock_dir)],
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "another YOmux stack start is already in progress" in result.stderr
    assert not lock_dir.exists()


def test_legacy_startup_admission_helpers_are_nonblocking():
    startup_common = STARTUP_COMMON.read_text(encoding="utf-8")
    result = subprocess.run(
        [
            fixture_executable("bash"),
            "-c",
            'source "$1"; yomux_wait_for_system_capacity "$2"; yomux_system_load_snapshot "$2"',
            "startup-capacity",
            str(STARTUP_COMMON),
            sys.executable,
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout.count("startup CPU/memory admission disabled") == 2
    assert "os.getloadavg" not in startup_common
    assert "cpu_idle_fraction" not in startup_common
    assert "memory_used_fraction" not in startup_common


def test_default_start_lock_is_shared_outside_process_specific_tmpdir(tmp_path):
    rooted = tmp_path / "root"
    result = subprocess.run(
        [fixture_executable("bash"), "-c", 'source "$1"; TMPDIR="$2" yomux_start_lock_path', "startup-lock-path", str(STARTUP_COMMON), str(tmp_path)],
        text=True,
        capture_output=True,
        check=True,
        env={**os.environ, "YOLOMUX_ROOT": str(rooted)},
    )

    assert result.stdout == str(rooted / "cache" / "start.lock")


@pytest.mark.parametrize("selector", [[], ["--backend", "go"], ["--backend", "auto"], ["--golang"]])
def test_native_boot_aliases_preserve_launch_and_environment_contract(tmp_path, native_binary, selector):
    environment = synthetic_test_environment({
        "HOME": str(tmp_path / "home"), "YOMUX_GO_BINARY": str(native_binary),
        "YOMUX_PORT": "48123", "YOMUX_HOST": "127.0.0.1", "YOMUX_LOG_DIR": "/tmp",
    })
    result = subprocess.run([str(ROOT / "boot.sh"), "--print-command", *selector], cwd=tmp_path, env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    command = printable_command_text(result.stdout)
    assert str(native_binary) in command
    assert "--launch --root" in command
    assert "--port 48123" in command
    assert "--host 127.0.0.1" in command
    assert "--backend go" in command
    assert "--dang" in command
    assert "TMUX= TMUX_PANE=" in command or "unset TMUX TMUX_PANE" in command
    assert "yomux.py" not in command
    assert "--dev" not in command
    assert not (tmp_path / "home").exists()


@pytest.mark.parametrize("values", [
    {"YOMUX_BACKEND": "python"}, {"YOLOMUX_BACKEND": "python"},
    {"YOMUX_BACKEND": "go", "YOLOMUX_BACKEND": "python"},
    {"YOMUX_BACKEND": "auto", "YOLOMUX_BACKEND": "invalid"},
])
def test_native_boot_rejects_either_raw_backend_before_mutation(tmp_path, native_binary, values):
    environment = synthetic_test_environment({"HOME": str(tmp_path / "home"), "YOMUX_GO_BINARY": str(native_binary), **values})
    result = subprocess.run([str(ROOT / "boot.sh"), "--force", "--port", "48123"], env=environment, text=True, capture_output=True)
    assert result.returncode == 2
    assert "unsupported" in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("selector", [["--deprecated-python"], ["--backend", "python"], ["--backend", "invalid"], ["--dev"]])
def test_retired_or_invalid_launch_modes_are_rejected_before_mutation(tmp_path, selector):
    result = subprocess.run([str(ROOT / "boot.sh"), *selector, "--log-dir", str(tmp_path / "logs"), "48123"], env=os.environ, text=True, capture_output=True)
    assert result.returncode == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("prefix", ["YOMUX", "YOLOMUX"])
def test_native_binary_override_requires_an_executable_without_fallback(tmp_path, prefix):
    environment = synthetic_test_environment({"HOME": str(tmp_path / "home"), f"{prefix}_GO_BINARY": str(tmp_path / "missing")})
    result = subprocess.run([str(ROOT / "boot.sh"), "--print-command", "48123"], env=environment, text=True, capture_output=True)
    assert result.returncode == 2
    assert "missing or unexecutable" in result.stderr
    assert not (tmp_path / "home").exists()


def test_canonical_executable_override_wins_and_empty_clears_legacy(tmp_path, native_binary):
    environment = synthetic_test_environment({"HOME": str(tmp_path / "home"), "YOMUX_GO_BINARY": str(native_binary), "YOLOMUX_GO_BINARY": str(tmp_path / "legacy-missing")})
    selected = subprocess.run([str(ROOT / "boot.sh"), "--print-command", "48123"], env=environment, text=True, capture_output=True)
    assert selected.returncode == 0, selected.stderr
    assert f"YOMUX_GO_BINARY={native_binary}" in printable_command_text(selected.stdout)
    environment["YOMUX_GO_BINARY"] = ""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    fallback = checkout / "yomux-go"
    shutil.copy2(native_binary, fallback)
    cleared = subprocess.run([fixture_executable("bash"), "-c", 'source "$1"; yomux_go_binary "$2"', "bash", str(STARTUP_COMMON), str(checkout)], env=environment, text=True, capture_output=True)
    assert cleared.returncode == 0, cleared.stderr
    assert cleared.stdout == str(fallback)


@pytest.mark.parametrize("relative_binary", ["bazel-bin/cmd/yomux-go/yomux-go_/yomux-go", "bazel-bin/yomux-go", "yomux-go"])
def test_native_executable_discovery_uses_supported_wrappers(tmp_path, native_binary, relative_binary):
    checkout = tmp_path / "checkout"
    tools = checkout / "tools"
    tools.mkdir(parents=True)
    for source in (ROOT / "boot.sh", ROOT / "version.yaml"):
        shutil.copy2(source, checkout / source.name)
    for name in ("startup_common.sh", "product_environment.sh"):
        shutil.copy2(ROOT / "tools" / name, tools / name)
    binary = checkout / relative_binary
    binary.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(native_binary, binary)
    environment = synthetic_test_environment({"HOME": str(tmp_path / "home")})
    result = subprocess.run([str(checkout / "boot.sh"), "--print-command", "48123"], env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert f"YOMUX_GO_BINARY={binary}" in printable_command_text(result.stdout)


def test_multiple_ports_generate_only_native_launches():
    result = subprocess.run([str(ROOT / "boot.sh"), "--print-command", "48121", "48122", "48123"], env=os.environ, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    command = printable_command_text(result.stdout)
    for port in (48121, 48122, 48123):
        assert f"--port {port}" in command
    assert command.count("--backend go") == 3
    assert "--dev" not in command


@pytest.mark.parametrize("prefix", ["YOMUX", "YOLOMUX"])
def test_captured_row_plan_survives_poisoned_macos_tmux_environment(tmp_path, native_binary, prefix):
    checkout = tmp_path / "checkout"
    tools = checkout / "tools"
    tools.mkdir(parents=True)
    for name in ("startup_common.sh", "product_environment.sh"):
        shutil.copy2(ROOT / "tools" / name, tools / name)
    result_path = tmp_path / "environment.bin"
    wrapper = tmp_path / "native-wrapper"
    wrapper.write_text('#!/bin/bash\nif [[ "${1:-}" == --operator ]]; then exec "$REAL_NATIVE_BINARY" "$@"; fi\n/usr/bin/env -0 > "$ENVIRONMENT_CAPTURE"\n')
    wrapper.chmod(0o755)
    with tempfile.TemporaryDirectory(prefix="yomux-row-", dir=short_temp_base()) as instance:
        plan_path = tmp_path / "row.json"
        plan_path.write_text(json.dumps({"unset": ["YOMUX_ROOT", "YOLOMUX_ROOT", "YOMUX_ROW_PLAN_FILE", "YOLOMUX_ROW_PLAN_FILE"], "assign": {"YOMUX_ROOT": instance}}))
        environment = synthetic_test_environment({
            "HOME": str(tmp_path / "home"), "REAL_NATIVE_BINARY": str(native_binary), "ENVIRONMENT_CAPTURE": str(result_path),
            f"{prefix}_ROW_PLAN_FILE": str(plan_path), "YOMUX_GO_BINARY": str(wrapper),
            "YOMUX_CODEX_TRANSCRIPT_ROOT": str(tmp_path / "codex sessions"), "YOMUX_GO_START_REASON": "operator-demand",
        })
        script = r'''set -euo pipefail
source "$1"
yomux_macos_server_tmux() {
  while [[ "$1" != /bin/bash ]]; do shift; done
  env YOMUX_ROOT=/stale/root YOLOMUX_ROOT=/stale/root YOMUX_ROW_PLAN_FILE=/stale/plan YOLOMUX_ROW_PLAN_FILE=/stale/plan YOMUX_GO_BINARY=/stale/bin YOLOMUX_GO_BINARY=/stale/bin "$@"
}
yomux_submit_macos_server "$2" "$3" /bin/bash "$PATH" 48123 "$4" --port 48123 --backend go
'''
        result = subprocess.run([fixture_executable("bash"), "-c", script, "submit", str(STARTUP_COMMON), str(checkout), str(wrapper), str(tmp_path / "server.log")], env=environment, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        values = dict(entry.split(b"=", 1) for entry in result_path.read_bytes().split(b"\0") if b"=" in entry)
        assert values[b"YOMUX_ROOT"].decode() == instance
        assert b"YOMUX_ROW_PLAN_FILE" not in values
        assert values[b"YOMUX_GO_BINARY"].decode() == str(wrapper)
        assert values[b"YOMUX_CODEX_TRANSCRIPT_ROOT"].decode() == str(tmp_path / "codex sessions")
        assert values[b"YOMUX_TMUX_SOCKET"] == b""
        assert values[b"YOMUX_TMUX_ALLOW_DEFAULT_SERVER"] == b"1"


def test_boot_asset_check_uses_only_tracked_vendor_files_without_package_tools(tmp_path, native_binary):
    checkout = tmp_path / "checkout"
    vendor_dir = checkout / "static" / "vendor"
    tools_dir = checkout / "tools"
    vendor_dir.mkdir(parents=True)
    tools_dir.mkdir()
    shutil.copy2(ROOT / "boot.sh", checkout / "boot.sh")
    shutil.copy2(STARTUP_COMMON, tools_dir / "startup_common.sh")
    shutil.copy2(ROOT / "tools" / "product_environment.sh", tools_dir / "product_environment.sh")
    shutil.copy2(ROOT / "tools" / "go_binary.py", tools_dir / "go_binary.py")
    shutil.copy2(ROOT / "version.yaml", checkout / "version.yaml")
    asset_names = ("xterm.js", "xterm.css", "xterm-addon-unicode11.js")
    for name in asset_names:
        shutil.copy2(ROOT / "static" / "vendor" / name, vendor_dir / name)

    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    for name in ("bash", "dirname", "uname", "sha256sum"):
        (command_dir / name).symlink_to(fixture_executable(name))
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": str(command_dir),
        "YOMUX_GO_BINARY": str(native_binary),
        "SHELL": str(command_dir / "bash"),
    }
    assert shutil.which("npm", path=env["PATH"]) is None
    assert shutil.which("curl", path=env["PATH"]) is None

    first = subprocess.run(
        [str(checkout / "boot.sh"), "--check-assets"],
        cwd=checkout,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )
    assert first.returncode == 0, first.stderr
    assert all(not (checkout / "static" / name).exists() for name in asset_names)

    vendor_bytes = {name: (vendor_dir / name).read_bytes() for name in asset_names}
    for name in asset_names:
        (checkout / "static" / name).write_bytes(f"contaminated-{name}".encode("ascii"))
    second = subprocess.run(
        [str(checkout / "boot.sh"), "--check-assets"],
        cwd=checkout,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )
    assert second.returncode == 0, second.stderr
    assert {name: (vendor_dir / name).read_bytes() for name in asset_names} == vendor_bytes
    assert {(checkout / "static" / name).read_text(encoding="ascii") for name in asset_names} == {
        f"contaminated-{name}" for name in asset_names
    }
