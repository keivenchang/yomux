#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""the single local-check entry point — runs the full documented check list as one command.

Replaces "remember to run these six things" with `python3 tools/check.py`. Each step prints PASS/FAIL and
the whole run exits non-zero if any step fails, so it is usable as the one CPS / pre-push gate (E1 folded
the lint set into `static_build.py --check`; E3 made pytest gate the node suite + locale staleness; this
ties the remaining steps — py_compile, both `node --check`s, git whitespace — into one invocation).

Usage: python3 tools/check.py            # run everything
       python3 tools/check.py --fast      # skip the (slow) full pytest; run the "not socket" lane
"""

from __future__ import annotations

import argparse
import glob
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def run(label: str, args: list[str]) -> bool:
    print(f"\n=== {label} ===", flush=True)
    result = subprocess.run(args, cwd=REPO_ROOT)
    ok = result.returncode == 0
    print(f"{'PASS' if ok else 'FAIL'}: {label}", flush=True)
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true", help="run the 'not socket' pytest lane instead of the full suite")
    args = parser.parse_args(argv)

    py_files = ["yolomux.py", "tmux_wall.py", "auto_approve_tmux.py", *sorted(glob.glob("yolomux_lib/*.py", root_dir=REPO_ROOT))]
    pytest_args = ["python3", "-m", "pytest", "tests", "-q"]
    if args.fast:
        pytest_args += ["-m", "not socket"]

    steps: list[tuple[str, list[str]]] = [
        ("py_compile", ["python3", "-m", "py_compile", *py_files]),
        ("static_build --check (assets + full lint set)", ["python3", "tools/static_build.py", "--check"]),
        ("node --check static/yolomux.js", ["node", "--check", "static/yolomux.js"]),
        ("node --check static/tmux-wall.js", ["node", "--check", "static/tmux-wall.js"]),
        ("node tests/layout_url.test.js", ["node", "tests/layout_url.test.js"]),
        ("pytest" + (" (fast: not socket)" if args.fast else ""), pytest_args),
        ("git diff --check (whitespace)", ["git", "diff", "--check"]),
    ]

    failed = [label for label, cmd in steps if not run(label, cmd)]
    print("\n" + ("=" * 40))
    if failed:
        print("CHECK FAILED: " + ", ".join(failed))
        return 1
    print("CHECK PASSED: all steps green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
