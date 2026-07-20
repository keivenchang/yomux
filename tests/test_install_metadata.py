from pathlib import Path

from tools import check_python


ROOT = Path(__file__).resolve().parents[1]


def test_python_preflight_rejects_unsupported_interpreter():
    error = check_python.python_requirement_error((3, 9, 19), "/usr/bin/python3")

    assert "requires Python 3.10 or newer" in error
    assert "/usr/bin/python3 is Python 3.9.19" in error
    assert check_python.python_requirement_error((3, 10, 0), "/usr/bin/python3") == ""


def test_install_metadata_owns_python_floor_and_watchfiles_dependency():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert 'requires-python = ">=3.10"' in pyproject
    assert '"watchfiles>=1.2.0"' in pyproject
    assert not any(ROOT.glob("requirements*.txt"))
    assert "setup: check-python" in makefile
    assert "dev: check-python" in makefile
