#!/usr/bin/env python3
"""Fail early when an installer uses an unsupported Python interpreter."""

from __future__ import annotations

import sys
from collections.abc import Sequence


MIN_PYTHON = (3, 10)


def python_requirement_error(
    version_info: Sequence[int] | None = None,
    executable: str | None = None,
) -> str:
    current = tuple((version_info or sys.version_info)[:3])
    if current[:2] >= MIN_PYTHON:
        return ""
    interpreter = executable or sys.executable or "python"
    required = ".".join(str(part) for part in MIN_PYTHON)
    detected = ".".join(str(part) for part in current)
    return (
        f"YOLOmux requires Python {required} or newer; "
        f"{interpreter} is Python {detected}. Set PYTHON to a newer interpreter "
        f"(for example: make setup PYTHON=python3.12)."
    )


def main() -> int:
    error = python_requirement_error()
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
