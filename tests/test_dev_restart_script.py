from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STARTUP_COMMON = ROOT / "tools" / "startup_common.sh"


def printable_command_text(output: str) -> str:
    return output.replace("\\ ", " ")


def test_boot_print_command_uses_any_configured_primary_port():
    env = {
        **os.environ,
        "YOLOMUX_HOST": "127.0.0.1",
        "YOLOMUX_LOG_DIR": "/tmp",
        "YOLOMUX_PORT": "48123",
    }
    env.pop("YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT", None)
    result = subprocess.run(
        [str(ROOT / "boot.sh"), "--print-command"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    command = printable_command_text(result.stdout)
    assert "yolomux.py" in command
    assert "--host 127.0.0.1" in command
    assert "--port 48123" in command
    assert "--dang --self-signed" in command
    assert "--dev" not in command
    assert "MALLOC_ARENA_MAX=2" in command
    if platform.system() == "Darwin":
        assert "YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT" in command
        assert "48123 /tmp/yolomux-48123.log --host 127.0.0.1" in command
    else:
        assert "YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT=48123" in command
    clears_tmux_inline = "TMUX= TMUX_PANE=" in command
    clears_tmux_in_detacher = (
        'env.pop("TMUX", None)' in command
        and 'env.pop("TMUX_PANE", None)' in command
    )
    clears_tmux_in_macos_launcher = "unset TMUX TMUX_PANE" in command
    assert clears_tmux_inline or clears_tmux_in_detacher or clears_tmux_in_macos_launcher
    if platform.system() == "Darwin":
        assert "/bin/bash -c" in command
        assert "tmux -L yolomux-services new-session" in command
        assert "yolomux-48123" in command
        assert "launchctl submit" not in command
        assert "cd" in command
        assert str(ROOT) in command
        assert "yolomux-48123.log" in command


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
    assert command.count("--dang --self-signed --dev") == 3


def test_boot_restart_waits_for_stable_listener_after_ready():
    source = (ROOT / "boot.sh").read_text(encoding="utf-8")

    assert "verify_port_stable()" in source
    assert "became unstable after readiness" in source
    assert "wait_for_port \"$port\"\n  verify_port_stable \"$port\"" in source


def test_boot_restart_requires_old_listener_to_stop_before_launch():
    source = (ROOT / "boot.sh").read_text(encoding="utf-8")
    startup_common = STARTUP_COMMON.read_text(encoding="utf-8")

    assert "wait_for_port_free()" in source
    assert "listener still alive after SIGTERM; sending SIGKILL" in source
    assert "stop_port_listener \"$port\"\n\n  printf" in source
    assert "boot.sh launching port" in source
    assert " >> %q 2>&1 < /dev/null" in source
    assert 'env.pop("TMUX", None)' in source
    assert 'env.pop("TMUX_PANE", None)' in source
    assert "acquire_port_restart_lock \"$port\"" in source
    assert "a YOLOmux restart for port $port is already in progress" in source
    assert "another YOLOmux stack start is already in progress" in startup_common
    assert 'source "$repo_root/tools/startup_common.sh"' in source
    assert "yolomux_acquire_start_lock" in source
    assert "trap yolomux_release_start_lock EXIT" in source
    assert 'trap yolomux_release_start_lock EXIT\nyolomux_wait_for_system_capacity "$python_bin"\nensure_xterm_assets' in source
    assert 'yolomux_wait_for_system_capacity "$python_bin"' in source
    assert 'yolomux_bootout_macos_server "$port"\n  fi\n  stop_port_listener "$port"' in source
    assert "yolomux_submit_macos_server" in source
    assert "yolomux_macos_server_launcher" in source
    assert "yolomux_macos_server_tmux_socket" in startup_common
    assert 'tmux -L "$socket_name" new-session' in startup_common
    assert "launchctl submit" not in startup_common
    assert 'cd "$repo"' in startup_common


def test_shared_start_lock_rejects_concurrent_launcher_and_releases_cleanly(tmp_path):
    lock_dir = tmp_path / "start.lock"
    holder = subprocess.Popen(
        [
            "bash",
            "-c",
            'source "$1"; export YOLOMUX_START_LOCK_DIR="$2"; trap yolomux_release_start_lock EXIT; yolomux_acquire_start_lock; echo ready; read -r _',
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
            'source "$1"; export YOLOMUX_START_LOCK_DIR="$2"; yolomux_acquire_start_lock',
            "startup-lock-contender",
            str(STARTUP_COMMON),
            str(lock_dir),
        ],
        text=True,
        capture_output=True,
    )
    assert contender.returncode != 0
    assert "another YOLOmux stack start is already in progress" in contender.stderr

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
            'source "$1"; export YOLOMUX_START_LOCK_DIR="$2"; yolomux_acquire_start_lock; yolomux_release_start_lock',
            "startup-lock-stale",
            str(STARTUP_COMMON),
            str(lock_dir),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert lock_dir.exists() is False


def test_startup_capacity_uses_portable_eight_cpu_macos_ceiling():
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; yolomux_system_load_snapshot "$2"',
            "startup-capacity",
            str(STARTUP_COMMON),
            sys.executable,
        ],
        text=True,
        capture_output=True,
    )
    expected = min(os.cpu_count() or 1, 8) if platform.system() == "Darwin" else max(1, os.cpu_count() or 1)

    assert result.returncode in {0, 1}
    assert f"cpu_budget={expected}" in result.stdout


def test_startup_capacity_accepts_bounded_operator_load_discount():
    expected = min(os.cpu_count() or 1, 8) if platform.system() == "Darwin" else max(1, os.cpu_count() or 1)
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; YOLOMUX_START_LOAD_DISCOUNT_CORES="$3" yolomux_system_load_snapshot "$2"',
            "startup-capacity-discount",
            str(STARTUP_COMMON),
            sys.executable,
            str(expected + 100),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode in {0, 1}, result.stdout + result.stderr
    assert f"discount={expected:.2f}" in result.stdout
    fields = [part.split("=", 1) for part in result.stdout.split() if "=" in part]
    raw_loads = [float(value) for key, value in fields if key in {"load1", "load5"}]
    effective_loads = [float(value.split("/", 1)[0]) for key, value in fields if key == "effective"]
    assert len(raw_loads) == len(effective_loads) == 2
    assert all(
        abs(effective - max(0.0, raw - expected)) <= 0.02
        for raw, effective in zip(raw_loads, effective_loads)
    )


def test_startup_capacity_rejects_invalid_operator_load_discount():
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; YOLOMUX_START_LOAD_DISCOUNT_CORES=invalid yolomux_system_load_snapshot "$2"',
            "startup-capacity-invalid-discount",
            str(STARTUP_COMMON),
            sys.executable,
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "invalid YOLOMUX_START_LOAD_DISCOUNT_CORES" in result.stdout


def test_default_start_lock_is_shared_outside_process_specific_tmpdir(tmp_path):
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; TMPDIR="$2" yolomux_start_lock_path', "startup-lock-path", str(STARTUP_COMMON), str(tmp_path)],
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout == str(Path.home() / ".cache" / "yolomux" / "start.lock")
