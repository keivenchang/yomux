import re
from pathlib import Path
import tomllib

from tools import check_python


ROOT = Path(__file__).resolve().parents[1]


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
