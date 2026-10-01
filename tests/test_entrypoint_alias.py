import os
from pathlib import Path
import subprocess
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_python_entrypoint_warns_and_forwards_to_canonical_cli() -> None:
    with tempfile.TemporaryDirectory(prefix="yomux-legacy-cli-") as temp_root:
        temporary_root = Path(temp_root)
        home = temporary_root / "home"
        runtime_tmp = temporary_root / "tmp"
        home.mkdir()
        runtime_tmp.mkdir()
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("YOLOMUX_", "YOMUX_", "XDG_"))
        }
        environment["HOME"] = str(home)
        environment["TMPDIR"] = str(runtime_tmp)
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "yolomux.py"), "--help"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )

    assert result.returncode == 0, result.stderr
    assert "WARNING: the yolomux command and entrypoint are deprecated; use yomux." in result.stderr
    assert "usage:" in result.stdout
