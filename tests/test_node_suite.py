"""make the single documented entry point (pytest) actually gate the rest.

`python3 -m pytest tests` passing used to say NOTHING about the largest test file in the repo (the node
suite) or about stale generated locale outputs — both ran only when a human/agent followed the manual
command list. These bridge that gap.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from tools import static_build

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.node_bridge


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_layout_suite_passes():
    result = subprocess.run(
        ["node", "tests/layout_url.test.js"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"node tests/layout_url.test.js failed:\n{result.stderr[-4000:]}"


def test_generated_locale_outputs_are_current():
    stale = static_build.check_locales()
    assert stale == [], f"stale generated locale outputs (run tools/static_build.py): {stale}"
