# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared Go executable discovery and cached fixture builds."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from collections.abc import Mapping
import os
from pathlib import Path
import sys
import subprocess
import tempfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.product_environment import product_env
from tools.generate_go_version import generate_go_version_overlay


GO_TEST_SERVER_BUILD_FLAGS = ("-buildvcs=false", "-tags=sqlite_fts5")


def go_backend_binary_candidates(repo_root: Path, environment: Mapping[str, str]) -> list[Path]:
    configured = product_env('GO_BINARY', "", environ=environment).strip()
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


def go_test_server_binary(source_root: Path) -> Path:
    """Return the Go server for process-level tests, building it hermetically if needed."""

    configured = product_env("GO_BINARY", "").strip()
    if configured:
        binary = Path(configured)
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise FileNotFoundError(f"configured Go test server is not executable: {binary}")
        return binary

    environment = dict(os.environ)
    environment.update({
        "GOMODCACHE": os.environ.get("GOMODCACHE", "/opt/yomux-go-mod-cache"),
        "GOPROXY": "off",
        "GOSUMDB": "off",
        "GOWORK": "off",
    })
    build_identity = subprocess.check_output(
        ["go", "env", "-json", "GOVERSION", "GOOS", "GOARCH", "GOAMD64", "GOARM64", "GOEXPERIMENT", "GOFLAGS", "CGO_ENABLED", "CC", "CXX", "CGO_CFLAGS", "CGO_CPPFLAGS", "CGO_CXXFLAGS", "CGO_LDFLAGS"],
        cwd=source_root, env=environment, text=True,
    )
    source_inputs = sorted(
        path
        for directory in (source_root / "cmd", source_root / "internal")
        if directory.is_dir()
        for path in directory.rglob("*")
        if path.is_file()
    )
    digest = hashlib.sha256()
    digest.update(json.dumps(GO_TEST_SERVER_BUILD_FLAGS).encode())
    digest.update(build_identity.encode())
    digest.update(Path(__file__).read_bytes())
    for path in [source_root / "go.mod", source_root / "go.sum", source_root / "version.yaml", source_root / "tools/generate_go_version.py", *source_inputs]:
        if path.is_file():
            digest.update(str(path.relative_to(source_root)).encode())
            digest.update(path.read_bytes())
    cache = Path(tempfile.gettempdir()) / "yomux-go-test-binaries" / digest.hexdigest()
    binary = cache / "yomux-go"
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "build.lock").open("a+b") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        if binary.is_file() and os.access(binary, os.X_OK):
            return binary
        generated = cache / "product_version_generated.go"
        overlay = cache / "go-overlay.json"
        generate_go_version_overlay(
            source_root / "version.yaml",
            generated,
            overlay,
            source_root / "cmd/yomux-go/product_version_generated.go",
        )
        temporary_binary = cache / "yomux-go.building"
        completed = subprocess.run(
            ["go", "build", *GO_TEST_SERVER_BUILD_FLAGS, "-overlay", str(overlay), "-o", str(temporary_binary), "./cmd/yomux-go"],
            cwd=source_root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"offline Go test-server build failed: {completed.stderr.strip() or completed.stdout.strip()}")
        os.replace(temporary_binary, binary)
        binary.chmod(0o700)
    return binary


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
