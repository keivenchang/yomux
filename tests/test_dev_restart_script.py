from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tools.instance_isolation import synthetic_test_environment
from tools.go_binary import go_backend_binary_candidates
from tools.product_environment import product_env_names


ROOT = Path(__file__).resolve().parents[1]
STARTUP_COMMON = ROOT / "tools" / "startup_common.sh"


def _copy_product_environment_owner(tools_dir: Path) -> None:
    for name in ("product_environment.py", "product_namespace.json"):
        shutil.copy2(ROOT / "tools" / name, tools_dir / name)


def printable_command_text(output: str) -> str:
    return output.replace("\\ ", " ")


def test_startup_load_wait_compatibility_helper_is_nonblocking():
    env = {**os.environ, "YOMUX_START_LOAD_WAIT_SECONDS": "0"}
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; yomux_wait_for_system_capacity false', "bash", str(STARTUP_COMMON)],
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


def test_boot_print_command_defaults_to_go_for_any_configured_primary_port():
    env = {
        **os.environ,
        "YOLOMUX_HOST": "127.0.0.1",
        "YOLOMUX_LOG_DIR": "/tmp",
        "YOLOMUX_PORT": "48123",
        "YOLOMUX_GO_BINARY": "/tmp/yolomux-go-test",
    }
    result = subprocess.run(
        [str(ROOT / "boot.sh"), "--print-command"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    command = printable_command_text(result.stdout)
    assert "yomux.py" in command
    assert "--backend go" in command
    assert "YOMUX_GO_BINARY=/tmp/yolomux-go-test" in command
    assert "--host 127.0.0.1" in command
    assert "--port 48123" in command
    assert "--dang --self-signed" in command
    assert "--dev" not in command
    assert "MALLOC_ARENA_MAX=2" in command
    if platform.system() != "Darwin":
        assert "nohup setsid bash -c" in command
    assert "YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT" not in command
    clears_tmux_inline = "TMUX= TMUX_PANE=" in command
    clears_tmux_in_detacher = (
        'env.pop("TMUX", None)' in command
        and 'env.pop("TMUX_PANE", None)' in command
    )
    clears_tmux_in_macos_launcher = (
        "unset TMUX TMUX_PANE" in command
        and "TMUX_SOCKET TMUX_ALLOW_DEFAULT_SERVER" in command
    )
    assert clears_tmux_inline or clears_tmux_in_detacher or clears_tmux_in_macos_launcher
    if platform.system() == "Darwin":
        assert "/bin/bash -c" in command
        assert "env -u TMUX -u TMUX_PANE tmux new-session" in command
        assert "yolomux-48123" in command
        assert "launchctl submit" not in command
        assert "cd" in command
        assert str(ROOT) in command
        assert "yolomux-48123.log" in command


def test_boot_print_command_uses_canonical_go_binary_environment_name():
    env = {
        **os.environ,
        "YOLOMUX_PORT": "48124",
        "YOMUX_GO_BINARY": "/tmp/yomux-go-canonical",
    }
    env.pop("YOLOMUX_GO_BINARY", None)
    result = subprocess.run(
        [str(ROOT / "boot.sh"), "--print-command"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "YOMUX_GO_BINARY=/tmp/yomux-go-canonical" in printable_command_text(result.stdout)


def test_boot_print_command_canonical_go_binary_wins_conflicting_legacy_value():
    env = {
        **os.environ,
        "YOLOMUX_PORT": "48125",
        "YOLOMUX_GO_BINARY": "/tmp/yolomux-go-legacy",
        "YOMUX_GO_BINARY": "/tmp/yomux-go-canonical",
    }
    result = subprocess.run(
        [str(ROOT / "boot.sh"), "--print-command"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    command = printable_command_text(result.stdout)
    assert "YOMUX_GO_BINARY=/tmp/yomux-go-canonical" in command
    assert "YOMUX_GO_BINARY=/tmp/yolomux-go-legacy" not in command


def test_boot_print_command_empty_canonical_go_binary_clears_legacy_override():
    env = {
        **{key: value for key, value in os.environ.items() if key not in {"YOMUX_GO_BINARY", "YOLOMUX_GO_BINARY"}},
        "YOLOMUX_PORT": "48126",
        "YOLOMUX_GO_BINARY": "/tmp/yolomux-go-legacy",
        "YOMUX_GO_BINARY": "",
    }
    result = subprocess.run(
        [str(ROOT / "boot.sh"), "--print-command"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    command = printable_command_text(result.stdout)
    assert "YOMUX_GO_BINARY=/tmp/yolomux-go-legacy" not in command


@pytest.mark.parametrize("relative_binary", [
    "bazel-bin/cmd/yomux-go/yomux-go_/yomux-go",
    "bazel-bin/cmd/yolomux-go/yolomux_go_/yolomux_go",
    "bazel-bin/cmd/yomux-go/yolomux_go_/yolomux_go",
])
def test_boot_print_command_selects_the_executable_bazel_wrapper(tmp_path, relative_binary):
    checkout = tmp_path / "checkout"
    tools_dir = checkout / "tools"
    tools_dir.mkdir(parents=True)
    for source in (ROOT / "boot.sh", STARTUP_COMMON, ROOT / "tools" / "product_environment.sh", ROOT / "tools" / "instance_isolation.py", ROOT / "tools" / "go_binary.py"):
        shutil.copy2(source, checkout / "boot.sh" if source.name == "boot.sh" else tools_dir / source.name)
    _copy_product_environment_owner(tools_dir)
    binary = checkout / relative_binary
    binary.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, binary)
    with tempfile.TemporaryDirectory(prefix="yomux-launch-", dir="/tmp") as instance_root:
        environment = synthetic_test_environment({"PYTHON": sys.executable, "HOME": str(tmp_path / "home"), "YOMUX_ROOT": instance_root})
        result = subprocess.run(
            [str(checkout / "boot.sh"), "--print-command", "48127"],
            cwd=checkout,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
    assert f"YOMUX_GO_BINARY={binary}" in printable_command_text(result.stdout)


def test_boot_print_command_launches_dev_ports_in_dev_mode():
    env = dict(os.environ)
    env.pop("YOLOMUX_PORT", None)
    result = subprocess.run(
        [str(ROOT / "boot.sh"), "--print-command", "8123", "8124", "8125"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    command = printable_command_text(result.stdout)
    assert "--port 8123" in command
    assert "--port 8124" in command
    assert "--port 8125" in command
    assert command.count("--backend go") == 3
    assert "--dev" not in command
    assert command.count("--self-signed") == 3


def test_boot_print_command_selects_go_backend_and_defaults_to_all_interfaces():
    env = dict(os.environ)
    env.pop("YOLOMUX_HOST", None)
    env.pop("YOLOMUX_PORT", None)
    env["YOLOMUX_GO_BINARY"] = "/tmp/yolomux-go-test"
    result = subprocess.run(
        [str(ROOT / "boot.sh"), "--print-command", "--golang", "7221"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    command = printable_command_text(result.stdout)
    assert "yomux.py" in command
    assert "--backend go" in command
    assert "--host 0.0.0.0" in command
    assert "YOMUX_GO_BINARY=/tmp/yolomux-go-test" in command
    assert "YOMUX_CLAUDE_TRANSCRIPT_ROOT=" in command
    assert "YOMUX_CODEX_TRANSCRIPT_ROOT=" in command
    assert "YOMUX_OPENCODE_DATABASE=" in command


def test_boot_print_command_accepts_backend_go_alias():
    env = dict(os.environ)
    env.pop("YOLOMUX_HOST", None)
    env.pop("YOLOMUX_PORT", None)
    env["YOLOMUX_GO_BINARY"] = "/tmp/yolomux-go-test"
    result = subprocess.run(
        [str(ROOT / "boot.sh"), "--print-command", "--backend", "go", "7221"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--backend go" in printable_command_text(result.stdout)


def test_boot_print_command_selects_python_only_with_deprecated_flag():
    result = subprocess.run(
        [str(ROOT / "boot.sh"), "--print-command", "--deprecated-python", "7221"],
        cwd=ROOT,
        env={**os.environ, "YOLOMUX_BACKEND": "go"},
        check=True,
        text=True,
        capture_output=True,
    )

    command = printable_command_text(result.stdout)
    assert "--backend python" in command
    assert "--backend go" not in command


def test_boot_print_command_rejects_backend_python_without_deprecated_flag():
    result = subprocess.run(
        [str(ROOT / "boot.sh"), "--print-command", "--backend", "python", "7221"],
        cwd=ROOT,
        env=dict(os.environ),
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "--deprecated-python" in result.stderr


def test_boot_go_resolution_includes_current_bazel_rule_output():
    candidates = go_backend_binary_candidates(ROOT, {})
    assert candidates[0] == ROOT / "bazel-bin/cmd/yomux-go/yomux-go_/yomux-go"
    assert candidates.index(ROOT / "bazel-bin/cmd/yolomux-go/yolomux_go_/yolomux_go") > candidates.index(ROOT / "yomux-go")
    assert candidates.index(ROOT / "bazel-bin/yolomux-go") > candidates.index(ROOT / "yomux-go")


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


def test_boot_refuses_unsafe_tmpdir_before_restart_lock_write(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    for value in ("relative-tmp", "/", str(home)):
        cwd = tmp_path / f"cwd-{len(value)}"
        cwd.mkdir()
        result = subprocess.run(
            [str(ROOT / "boot.sh"), "--print-command", "17775"],
            cwd=cwd,
            env={**os.environ, "HOME": str(home), "TMPDIR": value},
            text=True,
            capture_output=True,
        )

        assert result.returncode != 0
        assert "TMPDIR" in result.stderr
        assert list(cwd.iterdir()) == []


def test_boot_asset_check_uses_only_tracked_vendor_files_without_package_tools(tmp_path):
    checkout = tmp_path / "checkout"
    vendor_dir = checkout / "static" / "vendor"
    tools_dir = checkout / "tools"
    vendor_dir.mkdir(parents=True)
    tools_dir.mkdir()
    shutil.copy2(ROOT / "boot.sh", checkout / "boot.sh")
    shutil.copy2(STARTUP_COMMON, tools_dir / "startup_common.sh")
    shutil.copy2(ROOT / "tools" / "product_environment.sh", tools_dir / "product_environment.sh")
    shutil.copy2(ROOT / "tools" / "go_binary.py", tools_dir / "go_binary.py")
    _copy_product_environment_owner(tools_dir)
    asset_names = ("xterm.js", "xterm.css", "xterm-addon-unicode11.js")
    for name in asset_names:
        shutil.copy2(ROOT / "static" / "vendor" / name, vendor_dir / name)

    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    for name, target in (("bash", "/bin/bash"), ("dirname", "/usr/bin/dirname"), ("uname", "/usr/bin/uname")):
        (command_dir / name).symlink_to(target)
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": str(command_dir),
        "PYTHON": sys.executable,
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


def test_boot_restart_waits_for_stable_listener_after_ready():
    source = (ROOT / "boot.sh").read_text(encoding="utf-8")

    assert "listener_pid_is_forbidden()" in source
    assert 'previous_listener_pids="$(port_listener_pids "$port")"' in source
    assert "verify_port_stable()" in source
    assert "became unstable after readiness" in source
    assert 'wait_for_port "$port" "$previous_listener_pids"' in source
    assert 'verify_port_stable "$port" "$previous_listener_pids"' in source
    assert 'local stable_pid=""' in source
    assert 'stable_pid="$pids"' in source


def test_boot_restart_refuses_duplicate_without_blind_listener_kill():
    source = (ROOT / "boot.sh").read_text(encoding="utf-8")
    startup_common = STARTUP_COMMON.read_text(encoding="utf-8")

    assert "stop_port_listener" not in source
    assert "wait_for_port_free" not in source
    assert "--force" in source
    assert "yomux_lib.server_lease" not in source
    assert "acquire_instance_and_port_leases" not in source
    assert "exec --plan-json" not in source
    assert "boot.sh launching port" in source
    assert " >> %q 2>&1 < /dev/null" in source
    assert "TMUX= TMUX_PANE=" in source
    assert "acquire_port_restart_lock \"$port\"" in source
    assert "a YOmux restart for port $port is already in progress" in source
    assert "another YOmux stack start is already in progress" in startup_common
    assert 'source "$repo_root/tools/startup_common.sh"' in source
    assert "yomux_acquire_start_lock" in source
    assert "trap yomux_release_start_lock EXIT" in source
    assert 'yomux_wait_for_system_capacity "$python_bin"' not in source
    assert 'if [[ "$force_start" -eq 1 ]]; then' in source
    assert "already has listener(s)" in source
    assert 'wait_for_port "$port" "$previous_listener_pids" "$launched_pid" "$log_path"' in source
    assert "launch process" in source
    assert "launch_process_is_alive" in source
    assert 'mkdir -p "$restart_lock_base"' in source
    assert 'launch_server "$log_path"' in source
    assert "yomux_submit_macos_server" in source
    assert "yomux_macos_server_launcher" in source
    assert "yomux_macos_server_tmux()" in startup_common
    assert 'env -u TMUX -u TMUX_PANE tmux "$@"' in startup_common
    assert "launchctl submit" not in startup_common
    assert 'cd "$repo"' in startup_common


def test_boot_refuses_an_existing_listener_before_detached_launch():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        with tempfile.TemporaryDirectory(prefix="yolo-boot-", dir="/tmp") as workspace:
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
    with tempfile.TemporaryDirectory(prefix="yolo-boot-root-", dir="/tmp") as workspace:
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


def test_boot_load_flag_is_compatibility_only_and_preserves_startup_guards():
    source = (ROOT / "boot.sh").read_text(encoding="utf-8")

    assert "--ignore-load (legacy no-op)" in source
    assert 'yomux_acquire_start_lock || die "startup lock unavailable"' in source
    assert 'restart_port "$port"' in source
    assert 'yomux_wait_for_system_capacity "$python_bin"' not in source


def test_boot_never_waits_for_resource_pressure():
    source = (ROOT / "boot.sh").read_text(encoding="utf-8")
    launch_loop = source[source.rindex('for port in "${ports[@]}"; do'):]
    script = """
ports=(7220 7221)
restart_port() { printf 'restart %s\\n' "$1"; }
""" + launch_loop
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    lines = result.stdout.splitlines()
    assert [line for line in lines if line.startswith("restart ")] == ["restart 7220", "restart 7221"]


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
            ["/bin/bash", "-c", f'source "$1"; yomux_port_listener_pids {port}',
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
            ["/bin/bash", "-c", f'source "$1"; yomux_unique_listener_pid {port}',
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
        ["/bin/bash", "-c", f'source "$1"; yomux_unique_listener_pid {free_port}',
         "listener-probe", str(STARTUP_COMMON)],
        env={**os.environ, "PYTHON": sys.executable},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr[:400])
    assert "must have exactly one listener" in result.stderr
    assert len(result.stderr) < 2000, len(result.stderr)


@pytest.mark.parametrize(
    "scanner_body, expected_returncode, expected_stdout",
    (
        ("printf 'LISTEN 0 64 0.0.0.0:48124 0.0.0.0:* users:((\"python\",pid=202,fd=6))\\n'\n", 0, ""),
        ("printf 'scanner failed\\n' >&2\nexit 7\n", 0, ""),
    ),
)
def test_startup_listener_boundary_uses_selected_interpreter_and_checkout_module(
    tmp_path, scanner_body, expected_returncode, expected_stdout
):
    """The interpreter, cwd and module invocation are still the boundary's contract.

    Only the scanner's influence is gone: both a succeeding and a failing planted `ss` now produce
    the same result, because neither is consulted.
    """

    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    scanner = command_dir / "ss"
    scanner.write_text("#!/bin/sh\n" + scanner_body, encoding="utf-8")
    scanner.chmod(0o755)
    listener_census = command_dir / "lsof"
    listener_census.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    listener_census.chmod(0o755)
    (command_dir / "dirname").symlink_to("/usr/bin/dirname")
    record = tmp_path / "interpreter-record"
    wrapper = tmp_path / "selected-python"
    wrapper.write_text(
        "#!/bin/sh\n"
        "printf 'cwd=%s\\n' \"$PWD\" > \"$WRAPPER_RECORD\"\n"
        "for arg in \"$@\"; do printf 'arg=%s\\n' \"$arg\" >> \"$WRAPPER_RECORD\"; done\n"
        "exec \"$REAL_PYTHON\" \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$1"; yomux_port_listener_pids 48124', "probe", str(STARTUP_COMMON)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(command_dir),
            "YOMUX_PYTHON": str(wrapper),
            "REAL_PYTHON": sys.executable,
            "WRAPPER_RECORD": str(record),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == expected_returncode, result.stderr
    assert result.stdout == expected_stdout
    assert record.read_text(encoding="utf-8").splitlines() == [
        f"cwd={ROOT}",
        "arg=-m",
        "arg=yomux_lib.infra.listener_census",
        "arg=48124",
    ]


def test_macos_submit_accepts_legacy_six_fields_and_server_options(tmp_path):
    checkout = tmp_path / "checkout"
    tools_dir = checkout / "tools"
    tools_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "tools" / "instance_isolation.py", tools_dir / "instance_isolation.py")
    shutil.copy2(STARTUP_COMMON, tools_dir / "startup_common.sh")
    shutil.copy2(ROOT / "tools" / "product_environment.sh", tools_dir / "product_environment.sh")
    _copy_product_environment_owner(tools_dir)
    result_path = tmp_path / "result.json"
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({"unset": ["YOMUX_ROOT", "YOLOMUX_ROOT", "YOMUX_ROW_PLAN_FILE", "YOLOMUX_ROW_PLAN_FILE"], "assign": {"YOMUX_ROOT": str(tmp_path / "fresh-root"), "YOLOMUX_ROOT": str(tmp_path / "fresh-root")}}), encoding="utf-8")
    (checkout / "yomux.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "keys = ('GO_BINARY', 'GO_START_REASON', 'CODEX_TRANSCRIPT_ROOT', 'TMUX_SOCKET', 'TMUX_ALLOW_DEFAULT_SERVER', 'TEST_AUTH_BYPASS')\n"
        "values = {prefix + key: os.environ.get(prefix + key) for prefix in ('YOMUX_', 'YOLOMUX_') for key in keys}\n"
        "Path(os.environ['TEST_RESULT']).write_text(json.dumps({'args': sys.argv[1:], 'environment': values}))\n",
        encoding="utf-8",
    )
    tmux_stub = tmp_path / "tmux_stub.py"
    tmux_stub.write_text(
        "import os, subprocess, sys\n"
        "environment = dict(os.environ)\n"
        "for prefix in ('YOMUX_', 'YOLOMUX_'):\n"
        "    for key in ('GO_BINARY', 'GO_START_REASON', 'CODEX_TRANSCRIPT_ROOT', 'TMUX_SOCKET', 'TMUX_ALLOW_DEFAULT_SERVER', 'TEST_AUTH_BYPASS'):\n"
        "        environment[prefix + key] = 'stale-daemon-value'\n"
        "command = sys.argv[sys.argv.index('/bin/bash'):]\n"
        "raise SystemExit(subprocess.run(command, env=environment).returncode)\n",
        encoding="utf-8",
    )
    environment = {key: value for key, value in os.environ.items() if not key.startswith(('YOMUX_', 'YOLOMUX_'))}
    environment.update({
        "YOMUX_GO_BINARY": "/tmp/fresh-caller-yomux-go",
        "YOLOMUX_GO_BINARY": "/tmp/conflicting-legacy-go",
        "YOMUX_CODEX_TRANSCRIPT_ROOT": str(tmp_path / "codex sessions"),
        "YOMUX_GO_START_REASON": "operator-demand",
        "YOMUX_TEST_AUTH_BYPASS": "",
        "YOLOMUX_TEST_AUTH_BYPASS": "1",
        "YOLOMUX_ROW_PLAN_FILE": str(plan_path),
        "TEST_RESULT": str(result_path),
        "TEST_INTERPRETER": sys.executable,
        "TEST_TMUX_STUB": str(tmux_stub),
    })
    result = subprocess.run([
        "bash", "-c",
        'source "$1"; yomux_macos_server_tmux() { "$TEST_INTERPRETER" "$TEST_TMUX_STUB" "$@"; }; '
        'yomux_submit_macos_server "$2" "$3" /bin/bash "$PATH" 48125 "$4" --host 127.0.0.1 --port 48125 --backend go',
        "legacy-submit", str(STARTUP_COMMON), str(checkout), sys.executable, str(tmp_path / "server.log"),
    ], env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    captured = json.loads(result_path.read_text(encoding="utf-8"))
    assert captured["args"] == ["--host", "127.0.0.1", "--port", "48125", "--backend", "go"]
    for prefix in ("YOMUX_", "YOLOMUX_"):
        assert captured["environment"][prefix + "GO_BINARY"] == "/tmp/fresh-caller-yomux-go"
        assert captured["environment"][prefix + "GO_START_REASON"] == "operator-demand"
        assert captured["environment"][prefix + "CODEX_TRANSCRIPT_ROOT"] == str(tmp_path / "codex sessions")
        assert captured["environment"][prefix + "TMUX_SOCKET"] == ""
        assert captured["environment"][prefix + "TMUX_ALLOW_DEFAULT_SERVER"] == "1"
        assert captured["environment"][prefix + "TEST_AUTH_BYPASS"] is None


@pytest.mark.parametrize("handoff_prefix", ("YOMUX", "YOLOMUX"))
def test_macos_submit_uses_callers_row_plan_not_tmux_daemon_environment(tmp_path, handoff_prefix):
    tmux = shutil.which("tmux")
    if tmux is None:
        return

    checkout = tmp_path / "checkout"
    tools_dir = checkout / "tools"
    tools_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "tools" / "instance_isolation.py", tools_dir / "instance_isolation.py")
    shutil.copy2(STARTUP_COMMON, tools_dir / "startup_common.sh")
    shutil.copy2(ROOT / "tools" / "product_environment.sh", tools_dir / "product_environment.sh")
    _copy_product_environment_owner(tools_dir)
    result_path = tmp_path / "result.json"
    server_script = checkout / "yomux.py"
    server_script.write_text(
        """from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

values = {
    "YOLOMUX_ROOT": os.environ.get("YOLOMUX_ROOT"),
    "PYTHONPYCACHEPREFIX": os.environ.get("PYTHONPYCACHEPREFIX"),
    "YOLOMUX_ROW_PLAN_FILE": os.environ.get("YOLOMUX_ROW_PLAN_FILE"),
    "YOLOMUX_GO_BINARY": os.environ.get("YOLOMUX_GO_BINARY"),
    "YOLOMUX_GO_START_REASON": os.environ.get("YOLOMUX_GO_START_REASON"),
    "YOLOMUX_CLAUDE_TRANSCRIPT_ROOT": os.environ.get("YOLOMUX_CLAUDE_TRANSCRIPT_ROOT"),
    "YOLOMUX_CODEX_TRANSCRIPT_ROOT": os.environ.get("YOLOMUX_CODEX_TRANSCRIPT_ROOT"),
    "YOLOMUX_OPENCODE_DATABASE": os.environ.get("YOLOMUX_OPENCODE_DATABASE"),
    "YOLOMUX_TEST_AUTH_BYPASS": os.environ.get("YOLOMUX_TEST_AUTH_BYPASS"),
    "YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER": os.environ.get("YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER"),
    "YOLOMUX_TMUX_SOCKET": os.environ.get("YOLOMUX_TMUX_SOCKET"),
}
values.update({"YOMUX_" + key.removeprefix("YOLOMUX_"): os.environ.get("YOMUX_" + key.removeprefix("YOLOMUX_")) for key in tuple(values) if key.startswith("YOLOMUX_")})
Path(sys.argv[1]).write_text(json.dumps(values), encoding="utf-8")
subprocess.run(["tmux", "-L", sys.argv[2], "wait-for", "-S", "server-ready"], check=True)
""",
        encoding="utf-8",
    )

    socket_name = f"yomux-test-{os.getpid()}-{tmp_path.name}"
    stale_plan_path = tmp_path / "stale-plan.json"
    fresh_plan_path = tmp_path / "fresh-plan.json"
    stale_plan_path.write_text(
        json.dumps(
            {
                "unset": ["YOLOMUX_ROOT", "PYTHONPYCACHEPREFIX"],
                "assign": {
                    "YOLOMUX_ROOT": str(tmp_path / "stale-root"),
                    "PYTHONPYCACHEPREFIX": str(tmp_path / "stale-root" / "pycache"),
                },
            }
        ),
        encoding="utf-8",
    )
    fresh_plan_path.write_text(
        json.dumps(
            {
                "unset": ["YOMUX_ROOT", "YOLOMUX_ROOT", "PYTHONPYCACHEPREFIX", "YOMUX_ROW_PLAN_FILE", "YOLOMUX_ROW_PLAN_FILE"],
                "assign": {"YOMUX_ROOT": str(tmp_path / "fresh-root"), "YOLOMUX_ROOT": str(tmp_path / "fresh-root")},
            }
        ),
        encoding="utf-8",
    )
    daemon_env = {
        **os.environ,
        "YOLOMUX_ROW_PLAN_FILE": str(stale_plan_path),
        "YOLOMUX_ROOT": str(tmp_path / "stale-daemon-root"),
        "PYTHONPYCACHEPREFIX": str(tmp_path / "stale-daemon-pycache"),
        "YOLOMUX_GO_BINARY": "/tmp/stale-other-checkout-go",
        "YOLOMUX_GO_START_REASON": "stale-reason",
        "YOLOMUX_CLAUDE_TRANSCRIPT_ROOT": "/tmp/stale-claude",
        "YOLOMUX_CODEX_TRANSCRIPT_ROOT": "/tmp/stale-codex",
        "YOLOMUX_OPENCODE_DATABASE": "/tmp/stale-opencode.db",
        "YOLOMUX_TEST_AUTH_BYPASS": "1",
        "YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER": "0",
        "YOLOMUX_TMUX_SOCKET": "/tmp/stale-tmux.sock",
    }
    daemon_env.update({"YOMUX_" + key.removeprefix("YOLOMUX_"): value for key, value in tuple(daemon_env.items()) if key.startswith("YOLOMUX_")})
    subprocess.run(
        [tmux, "-L", socket_name, "new-session", "-d", "-s", "keeper", "tail", "-f", "/dev/null"],
        env=daemon_env,
        check=True,
    )
    try:
        launch = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; test_socket="$2"; yomux_macos_server_tmux() { env -u TMUX -u TMUX_PANE tmux -L "$test_socket" "$@"; }; '
                'export YOLOMUX_ROW_PLAN_FILE="$3"; '
                'yomux_submit_macos_server "$4" "$5" "$6" "$7" 48125 "$8" 8 '
                '"YOMUX_GO_BINARY=/tmp/yomux-go-test" "YOMUX_GO_START_REASON=operator-demand" '
                '"YOMUX_CLAUDE_TRANSCRIPT_ROOT=/tmp/claude projects" "YOMUX_CODEX_TRANSCRIPT_ROOT=/tmp/codex" '
                '"YOMUX_OPENCODE_DATABASE=/tmp/opencode.db" "YOMUX_TEST_AUTH_BYPASS=1" '
                '"YOMUX_TMUX_ALLOW_DEFAULT_SERVER=1" "YOMUX_TMUX_SOCKET=" "$9" "$2"'.replace("YOMUX_", f"{handoff_prefix}_"),
                "submit-clean-plan",
                str(STARTUP_COMMON),
                socket_name,
                str(fresh_plan_path),
                str(checkout),
                sys.executable,
                os.environ.get("SHELL", "/bin/bash"),
                os.environ["PATH"],
                str(tmp_path / "server.log"),
                str(result_path),
            ],
            text=True,
            capture_output=True,
        )
        assert launch.returncode == 0, launch.stderr
        subprocess.run([tmux, "-L", socket_name, "wait-for", "server-ready"], check=True, timeout=10)
        child_environment = json.loads(result_path.read_text(encoding="utf-8"))
        expected_environment = {
            "YOLOMUX_ROOT": str(tmp_path / "fresh-root"),
            "PYTHONPYCACHEPREFIX": None,
            "YOLOMUX_ROW_PLAN_FILE": None,
            "YOLOMUX_GO_BINARY": "/tmp/yomux-go-test",
            "YOLOMUX_GO_START_REASON": "operator-demand",
            "YOLOMUX_CLAUDE_TRANSCRIPT_ROOT": "/tmp/claude projects",
            "YOLOMUX_CODEX_TRANSCRIPT_ROOT": "/tmp/codex",
            "YOLOMUX_OPENCODE_DATABASE": "/tmp/opencode.db",
            "YOLOMUX_TEST_AUTH_BYPASS": "1",
            "YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER": "1",
            "YOLOMUX_TMUX_SOCKET": "",
        }
        expected_environment.update({"YOMUX_" + key.removeprefix("YOLOMUX_"): value for key, value in tuple(expected_environment.items()) if key.startswith("YOLOMUX_")})
        assert child_environment == expected_environment
        result_path.unlink()
        launch_without_go = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; test_socket="$2"; yomux_macos_server_tmux() { env -u TMUX -u TMUX_PANE tmux -L "$test_socket" "$@"; }; '
                'export YOLOMUX_ROW_PLAN_FILE="$3"; '
                'yomux_submit_macos_server "$4" "$5" "$6" "$7" 48125 "$8" 0 "$9" "$2"',
                "submit-empty-go-handoff",
                str(STARTUP_COMMON),
                socket_name,
                str(fresh_plan_path),
                str(checkout),
                sys.executable,
                os.environ.get("SHELL", "/bin/bash"),
                os.environ["PATH"],
                str(tmp_path / "server.log"),
                str(result_path),
            ],
            text=True,
            capture_output=True,
        )
        assert launch_without_go.returncode == 0, launch_without_go.stderr
        subprocess.run([tmux, "-L", socket_name, "wait-for", "server-ready"], check=True, timeout=10)
        child_environment = json.loads(result_path.read_text(encoding="utf-8"))
        expected_environment = {
            "YOLOMUX_ROOT": str(tmp_path / "fresh-root"),
            "PYTHONPYCACHEPREFIX": None,
            "YOLOMUX_ROW_PLAN_FILE": None,
            "YOLOMUX_GO_BINARY": None,
            "YOLOMUX_GO_START_REASON": None,
            "YOLOMUX_CLAUDE_TRANSCRIPT_ROOT": None,
            "YOLOMUX_CODEX_TRANSCRIPT_ROOT": None,
            "YOLOMUX_OPENCODE_DATABASE": None,
            "YOLOMUX_TEST_AUTH_BYPASS": None,
            "YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER": "1",
            "YOLOMUX_TMUX_SOCKET": None,
        }
        expected_environment.update({"YOMUX_" + key.removeprefix("YOLOMUX_"): value for key, value in tuple(expected_environment.items()) if key.startswith("YOLOMUX_")})
        assert child_environment == expected_environment
    finally:
        subprocess.run([tmux, "-L", socket_name, "kill-server"], check=False, capture_output=True)


def test_instance_preflight_uses_the_direct_plan_not_ambient_tmpdir():
    env = {
        **os.environ,
        "TMPDIR": "/private/var/folders/retained-service-daemon-tempdir-that-is-too-deep-for-sockets",
        "PYTHONPYCACHEPREFIX": "/private/tmp/stale-pycache",
    }
    for key in ("YOLOMUX_ROOT", "YOLOMUX_ROW_PLAN_FILE"):
        env.pop(key, None)
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; yomux_validate_instance_isolation "$2" "$3" 8881',
            "instance-preflight",
            str(STARTUP_COMMON),
            str(ROOT),
            sys.executable,
        ],
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr


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
            "bash",
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
    assert "invalid row plan" in result.stderr, result.stderr


def test_a_valid_row_plan_file_is_still_accepted_so_the_refusal_is_not_blanket(tmp_path):
    """Negative control for the row above: refusing every plan would pass that test and be a defect."""
    plan = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "instance_isolation.py"), "plan-direct", "--port", "8881"],
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
            "bash",
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
            "bash",
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
            "bash",
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
        ["bash", "-c", probe, "startup-lock-reentry", str(STARTUP_COMMON), str(lock_dir)],
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "another YOmux stack start is already in progress" in result.stderr
    assert not lock_dir.exists()


def test_legacy_startup_admission_helpers_are_nonblocking():
    startup_common = STARTUP_COMMON.read_text(encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
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
        ["bash", "-c", 'source "$1"; TMPDIR="$2" yomux_start_lock_path', "startup-lock-path", str(STARTUP_COMMON), str(tmp_path)],
        text=True,
        capture_output=True,
        check=True,
        env={**os.environ, "YOLOMUX_ROOT": str(rooted)},
    )

    assert result.stdout == str(rooted / "cache" / "start.lock")
