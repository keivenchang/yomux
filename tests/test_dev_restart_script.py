from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dev1_restart_print_command_launches_dev_mode():
    env = {
        **os.environ,
        "YOLOMUX_DEV1_PORT": "8123",
        "YOLOMUX_HOST": "127.0.0.1",
        "YOLOMUX_DEV1_LOG": "/tmp/yolomux-test-dev1.log",
    }
    result = subprocess.run(
        [str(ROOT / "tools" / "yolomux-restart-dev1.sh"), "--print-command"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    command = result.stdout
    assert "yolomux.py" in command
    assert "--host 127.0.0.1" in command
    assert "--port 8123" in command
    assert "--dang --self-signed --dev" in command

