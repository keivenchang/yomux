# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared executable discovery for the CLI and managed Go launcher."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import os
from pathlib import Path


def go_backend_binary_candidates(repo_root: Path, environment: Mapping[str, str]) -> list[Path]:
    configured = environment.get("YOMUX_GO_BINARY", environment.get("YOLOMUX_GO_BINARY", "")).strip()
    if configured:
        return [Path(configured)]
    return [repo_root / relative for relative in (
        "bazel-bin/cmd/yomux-go/yomux-go_/yomux-go",
        "bazel-bin/yomux-go",
        "yomux-go",
        "bazel-bin/cmd/yolomux-go/yolomux_go_/yolomux_go",
        "bazel-bin/cmd/yomux-go/yolomux_go_/yolomux_go",
        "yolomux-go",
        "bazel-bin/yolomux-go",
    )]


def find_go_backend_binary(candidates: list[Path]) -> Path | None:
    return next((path for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root", type=Path)
    args = parser.parse_args()
    binary = find_go_backend_binary(go_backend_binary_candidates(args.repo_root, os.environ))
    if binary is not None:
        print(binary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
