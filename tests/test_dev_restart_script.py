from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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

    command = result.stdout
    assert "yolomux.py" in command
    assert "--host 127.0.0.1" in command
    assert "--port 7777" in command
    assert "--dang --self-signed" in command
    assert "--dev" not in command


def test_boot_print_command_launches_dev_ports_in_dev_mode():
    result = subprocess.run(
        [str(ROOT / "boot.sh"), "--print-command", "8123", "8124", "8125"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    command = result.stdout
    assert "--port 8123" in command
    assert "--port 8124" in command
    assert "--port 8125" in command
    assert command.count("--dang --self-signed --dev") == 3
