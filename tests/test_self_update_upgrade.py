"""Platform-independent validation for the native upgrade verifier."""

import ast
from pathlib import Path

import pytest

from tools import verify_self_update_upgrade as upgrade


def test_newest_base_is_numeric_reachable_release_strictly_below_candidate():
    assert upgrade.newest_base_tag(["v0.9.9", "v0.11.0", "v0.11.1", "v0.11.2", "v0.11.1-rc1", "other"], (0, 11, 1)) == "v0.11.0"
    assert upgrade.newest_base_tag(["v0.9.9", "v0.10.0"], (0, 11, 0)) == "v0.10.0"
    with pytest.raises(RuntimeError, match="select --base-tag"):
        upgrade.newest_base_tag(["v0.11.1"], (0, 11, 1))


def test_upgrade_tool_errors_name_the_missing_tool(monkeypatch):
    monkeypatch.setattr(upgrade.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="unavailable: bazel"):
        upgrade.required_tool("bazel")


def test_upgrade_validation_survives_optimized_python():
    tree = ast.parse(Path(upgrade.__file__).read_text())
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))
    with pytest.raises(RuntimeError, match="invalid identity"):
        upgrade.require(False, "invalid identity")


@pytest.mark.parametrize("source, expected", [('version: "0.11.1"\n', (0, 11, 1)), ("version: 1.2.3", (1, 2, 3))])
def test_upgrade_release_version(source, expected):
    assert upgrade.release_version(source) == expected
