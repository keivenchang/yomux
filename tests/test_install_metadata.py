import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tomllib

import pytest

from tests.helpers.fixture_tools import fixture_executable, fixture_python_environment, fixture_script
from tools import check_python


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("corrupt", [False, True])
def test_xterm_verification_owns_its_npm_tree_and_preserves_prepared_inputs(tmp_path, corrupt):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for name in ("Makefile", "package.json", "package-lock.json"):
        shutil.copy2(ROOT / name, checkout / name)
    emoji = checkout / "node_modules/.yomux/emoji_data.js"
    emoji.parent.mkdir(parents=True)
    emoji.write_bytes(b"prepared emoji catalog\n")
    assets = {
        "xterm.js": "@xterm/xterm/lib/xterm.js",
        "xterm.css": "@xterm/xterm/css/xterm.css",
        "xterm-addon-unicode11.js": "@xterm/addon-unicode11/lib/addon-unicode11.js",
    }
    vendor = checkout / "static/vendor"
    vendor.mkdir(parents=True)
    for name in assets:
        (vendor / name).write_bytes(("upstream " + name).encode())
    if corrupt:
        (vendor / "xterm.js").write_bytes(b"unexpected bytes")
    tools = tmp_path / "bin"
    tools.mkdir()
    npm = tools / "npm"
    npm.write_text(fixture_script('''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import sys
args = sys.argv[1:]
prefix = Path(args[args.index("--prefix") + 1]) if "--prefix" in args else Path.cwd()
assert json.loads((prefix / "package.json").read_bytes())["dependencies"]
assert json.loads((prefix / "package-lock.json").read_bytes())["packages"]
modules = prefix / "node_modules"
if modules.exists():
    shutil.rmtree(modules)
for name, relative in json.loads(os.environ["NPM_FIXTURE_ASSETS"]).items():
    path = modules / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("upstream " + name).encode())
Path(os.environ["NPM_FIXTURE_PREFIX"]).write_text(str(prefix))
'''))
    npm.chmod(0o700)
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    record = tmp_path / "npm-prefix"
    environment = fixture_python_environment({
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "TMPDIR": str(temporary),
        "NPM_FIXTURE_ASSETS": json.dumps(assets),
        "NPM_FIXTURE_PREFIX": str(record),
    })
    recipe_lines = []
    in_target = False
    for line in (checkout / "Makefile").read_text().splitlines():
        if line.startswith("xterm:"):
            in_target = True
        elif in_target and line.startswith("\t"):
            recipe_lines.append(line[1:].removeprefix("@"))
        elif in_target and line.strip():
            break
    # Execute the production shell recipe with Make's dollar escaping resolved;
    # the offline test runtime intentionally does not contain GNU Make.
    recipe = "\n".join(recipe_lines).replace("$$", "$")
    result = subprocess.run([fixture_executable("sh"), "-c", recipe], cwd=checkout, env=environment,
                            capture_output=True, text=True, timeout=20)
    assert (result.returncode != 0) == corrupt, result.stdout + result.stderr
    assert emoji.read_bytes() == b"prepared emoji catalog\n"
    prefix = Path(record.read_text())
    assert prefix.parent == temporary
    assert not prefix.exists()
    assert not list(temporary.iterdir())


def test_static_commit_hook_builds_the_staged_tree_in_its_own_directory(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    hook = checkout / ".githooks/pre-commit"
    hook.parent.mkdir()
    shutil.copy2(ROOT / ".githooks/pre-commit", hook)
    tools = checkout / "tools"
    tools.mkdir()
    build = tools / "build_go.sh"
    build.write_text('''#!/bin/sh
set -eu
root="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
[ "$(pwd -P)" = "$root" ] || exit 81
[ "$(cat static_src/input.js)" = "staged source" ]
printf 'staged build' > "$HOOK_FIXTURE_RECORD"
''')
    build.chmod(0o700)
    (tools / "static_build.py").write_text("import pathlib\nassert pathlib.Path(__file__).resolve().parents[1].joinpath('static_src/input.js').read_text() == 'staged source'\n")
    source = checkout / "static_src/input.js"
    source.parent.mkdir()
    source.write_text("staged source")
    subprocess.run([fixture_executable("git"), "init", "--initial-branch=main"], cwd=checkout,
                   check=True, capture_output=True)
    subprocess.run([fixture_executable("git"), "add", "."], cwd=checkout, check=True, capture_output=True)
    source.write_text("unstaged source")
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    record = tmp_path / "build-record"
    environment = fixture_python_environment({"TMPDIR": str(temporary), "HOOK_FIXTURE_RECORD": str(record)})
    result = subprocess.run([fixture_executable("bash"), str(hook)], cwd=checkout, env=environment,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    effects = {
        "build_artifact": record.read_bytes(),
        "worktree_source": source.read_bytes(),
        "remaining_private_workspaces": list(temporary.iterdir()),
    }
    assert effects == {
        "build_artifact": b"staged build",
        "worktree_source": b"unstaged source",
        "remaining_private_workspaces": [],
    }


def test_python_preflight_rejects_unsupported_interpreter():
    error = check_python.python_requirement_error((3, 9, 19), "/usr/bin/python3")

    assert "requires Python 3.11 or newer" in error
    assert "/usr/bin/python3 is Python 3.9.19" in error
    assert check_python.python_requirement_error((3, 10, 15), "/usr/bin/python3")
    assert check_python.python_requirement_error((3, 11, 0), "/usr/bin/python3") == ""


def _make_prerequisites(makefile):
    prerequisites = {}
    for line in makefile.splitlines():
        if line.startswith("\t"):
            continue
        declaration = line.split("#", 1)[0]
        targets, separator, dependencies = declaration.partition(":")
        if not separator or "=" in targets or targets.startswith("."):
            continue
        for target in targets.split():
            prerequisites.setdefault(target, set()).update(dependencies.split())
    return prerequisites


def test_install_metadata_owns_python_floor_and_watchfiles_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    prerequisites = _make_prerequisites((ROOT / "Makefile").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()

    assert project["requires-python"] == ">=3.11"
    assert project["dependencies"] == []
    assert not project.get("scripts", {})
    extras = project["optional-dependencies"]
    assert set(extras) == {"dev"}
    developer_dependencies = {re.split(r"[<>=!~;\[]", value, maxsplit=1)[0].lower(): value for value in extras["dev"]}
    assert developer_dependencies["watchfiles"] == "watchfiles>=1.2.0"
    assert "pyyaml" in developer_dependencies
    for dependency in ("mypy", "pillow", "pytest", "pytest-timeout", "pytest-xdist", "selenium"):
        assert dependency in developer_dependencies
        assert dependency in readme
    assert not any(ROOT.glob("requirements*.txt"))
    assert "install" in prerequisites["setup"]
    assert "build" in prerequisites["install"]
    assert "check-python" in prerequisites["dev"]
    product_targets = set()
    pending = ["setup"]
    while pending:
        target = pending.pop()
        if target in product_targets:
            continue
        product_targets.add(target)
        pending.extend(prerequisites.get(target, ()))
    assert {"build", "install"} <= product_targets
    assert not {"dev", "check-python"} & product_targets
