from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def printable_command_text(output: str) -> str:
    return output.replace("\\ ", " ")


def test_boot_print_command_defaults_to_prod_port():
    env = {
        **os.environ,
        "YOLOMUX_HOST": "127.0.0.1",
        "YOLOMUX_LOG_DIR": "/tmp",
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
    assert "yolomux.py" in command
    assert "--host 127.0.0.1" in command
    assert "--port 7000" in command
    assert "--dang --self-signed" in command
    assert "--dev" not in command
    assert "MALLOC_ARENA_MAX=2" in command
    assert "TMUX= TMUX_PANE=" in command


def test_boot_print_command_launches_dev_ports_in_dev_mode():
    result = subprocess.run(
        [str(ROOT / "boot.sh"), "--print-command", "8123", "8124", "8125"],
        cwd=ROOT,
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

    assert "wait_for_port_free()" in source
    assert "listener still alive after SIGTERM; sending SIGKILL" in source
    assert "stop_port_listener \"$port\"\n\n  printf" in source
    assert "boot.sh launching port" in source
    assert " >> %q 2>&1 < /dev/null" in source
    assert 'env.pop("TMUX", None)' in source
    assert 'env.pop("TMUX_PANE", None)' in source
