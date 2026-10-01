# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from pathlib import Path

import pytest

from tools.go_binary import find_go_backend_binary
from tools.go_binary import go_backend_binary_candidates


@pytest.mark.parametrize("relative", [
    "bazel-bin/cmd/yomux-go/yomux-go_/yomux-go",
    "bazel-bin/yomux-go",
    "yomux-go",
    "bazel-bin/cmd/yolomux-go/yolomux_go_/yolomux_go",
    "bazel-bin/cmd/yomux-go/yolomux_go_/yolomux_go",
    "yolomux-go",
    "bazel-bin/yolomux-go",
])
def test_go_binary_discovers_each_supported_output(tmp_path, relative):
    binary = tmp_path / relative
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"fixture")
    binary.chmod(0o700)
    assert find_go_backend_binary(go_backend_binary_candidates(tmp_path, {})) == binary


def test_go_binary_prefers_canonical_bazel_output_over_flat_and_legacy(tmp_path):
    candidates = go_backend_binary_candidates(tmp_path, {})
    for binary in candidates:
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"fixture")
        binary.chmod(0o700)
    assert find_go_backend_binary(candidates) == tmp_path / "bazel-bin/cmd/yomux-go/yomux-go_/yomux-go"


@pytest.mark.parametrize(("environment", "configured"), [
    ({"YOMUX_GO_BINARY": " /canonical ", "YOLOMUX_GO_BINARY": "/legacy"}, "/canonical"),
    ({"YOLOMUX_GO_BINARY": "/legacy"}, "/legacy"),
    ({"YOMUX_GO_BINARY": "", "YOLOMUX_GO_BINARY": "/legacy"}, None),
    ({"YOMUX_GO_BINARY": "   ", "YOLOMUX_GO_BINARY": "/legacy"}, None),
])
def test_go_binary_environment_precedence_and_empty_values(tmp_path, environment, configured):
    candidates = go_backend_binary_candidates(tmp_path, environment)
    if configured:
        assert candidates == [Path(configured)]
    else:
        assert candidates == go_backend_binary_candidates(tmp_path, {})


def test_go_binary_missing_override_never_selects_default(tmp_path):
    default = tmp_path / "yomux-go"
    default.write_bytes(b"fixture")
    default.chmod(0o700)
    candidates = go_backend_binary_candidates(tmp_path, {"YOMUX_GO_BINARY": str(tmp_path / "missing")})
    assert find_go_backend_binary(candidates) is None
    assert candidates == [tmp_path / "missing"]


def test_go_binary_requires_an_executable_file(tmp_path):
    directory = tmp_path / "directory"
    directory.mkdir()
    non_executable = tmp_path / "non-executable"
    non_executable.write_bytes(b"fixture")
    non_executable.chmod(0o600)
    assert find_go_backend_binary([directory, non_executable]) is None
