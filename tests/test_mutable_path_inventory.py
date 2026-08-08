"""Guard direct persistent paths from bypassing the current root owners."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_HOME_ROOT_OWNERS = {"yolomux_lib/infra/common.py", "yolomux_lib/auth.py"}
MUTATORS = {"write_text", "write_bytes", "mkdir", "touch", "unlink", "replace", "rename", "chmod"}


def _has_path_home(node: ast.AST) -> bool:
    return any(
        isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and item.func.attr == "home"
        and isinstance(item.func.value, ast.Name)
        and item.func.value.id == "Path"
        for item in ast.walk(node)
    )


def test_direct_home_backed_mutable_paths_have_a_layout_owner():
    offenders = []
    for path in sorted((ROOT / "yolomux_lib").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MUTATORS and _has_path_home(node):
                offenders.append(f"{relative}:{node.lineno}")
            if isinstance(node, ast.Assign) and _has_path_home(node) and relative not in ALLOWED_HOME_ROOT_OWNERS:
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
                if any(name.endswith(("_DIR", "_PATH")) for name in names):
                    offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, "mutable path bypasses the layout owner: " + ", ".join(offenders)
