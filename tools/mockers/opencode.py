#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""OpenCode-shaped text client mock for YOLOmux parser and browser tests."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
MOCKER_ROOT = Path(__file__).resolve().parent
for path in (REPO_ROOT, MOCKER_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import common as mock_agent_common


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the OpenCode-shaped YOLOmux mock client.")
    parser.add_argument("prompt", nargs="*", help="Optional prompt or mock command.")
    parser.add_argument("-C", "--cd", dest="cwd", default=os.getcwd(), metavar="DIR")
    parser.add_argument("-m", "--model", default="switchyard/openai/gpt-5.6-luna", metavar="MODEL")
    parser.add_argument("--effort", default="", metavar="LEVEL")
    parser.add_argument("-s", "--session", default="", metavar="SESSION_ID")
    parser.add_argument("--mock", action="store_true", help="Run the OpenCode TUI mock.")
    parser.add_argument("--dump-fixtures", action="store_true", help="Dump OpenCode fixtures and exit.")
    parser.add_argument("-V", "--version", action="version", version="opencode-text-client 1.18.26")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.cwd = str(Path(args.cwd).expanduser().resolve())
    mock_agent_common.configure_opencode_mock(display_cwd_override=args.cwd, model=args.model, effort=args.effort)
    if args.dump_fixtures:
        mock_agent_common.print_mock_fixture_dump()
        return 0
    if args.prompt:
        mock_agent_common.setup_history()
        mock_agent_common.print_startup({})
        mock_agent_common.handle_command(" ".join(args.prompt), {})
        return 0
    mock_agent_common.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
