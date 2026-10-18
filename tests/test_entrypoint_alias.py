"""Installed canonical and legacy command names share the native entrypoint."""

import os
from pathlib import Path
import subprocess

from tests.isolated_dev_server import go_test_server_binary
from tools.instance_isolation import synthetic_test_environment


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_installed_entrypoint_aliases_forward_to_native_cli(tmp_path):
    binary = go_test_server_binary(REPO_ROOT)
    environment = synthetic_test_environment({
        "PATH": os.environ["PATH"], "HOME": str(tmp_path / "home"),
        "YOMUX_GO_BINARY": str(binary),
    })
    prefix = tmp_path / "installed"
    installed = subprocess.run([str(REPO_ROOT / "tools/install.sh"), "--prefix", str(prefix)],
                               env=environment, capture_output=True, text=True, timeout=30)
    assert installed.returncode == 0, installed.stderr
    outputs = []
    for command in ("yomux", "yolomux"):
        result = subprocess.run([str(prefix / "bin" / command), "--version"],
                                env=environment, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("yomux-go ")
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]
