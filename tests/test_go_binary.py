# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import ast
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.go_binary import find_go_backend_binary
from tools.go_binary import go_backend_binary_candidates
from tools.go_binary import go_test_server_binary
from tools.instance_isolation import synthetic_test_environment
from tools.product_environment import product_env_names


@pytest.mark.parametrize("prefix", ["YOMUX", "YOLOMUX"])
def test_go_fixture_binary_uses_shared_namespace_owner(tmp_path, monkeypatch, prefix):
    for name in product_env_names("GO_BINARY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(prefix + "_GO_BINARY", " " + sys.executable + " ")
    monkeypatch.setattr("tools.go_binary.subprocess.check_output", lambda *_args, **_kwargs: pytest.fail("configured binary was ignored"))
    assert go_test_server_binary(tmp_path) == Path(sys.executable)


def test_go_discovery_imports_with_only_declared_test_tool_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("YOMUX_GO_BINARY", sys.executable)
    source_root = Path(__file__).resolve().parents[1]
    declarations = ast.parse((source_root / "BUILD.bazel").read_text(encoding="utf-8"))
    group = next(
        node for node in ast.walk(declarations)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "filegroup"
        and any(keyword.arg == "name" and isinstance(keyword.value, ast.Constant)
                and keyword.value.value == "go_test_tool_inputs" for keyword in node.keywords)
    )
    inputs = next(keyword.value for keyword in group.keywords if keyword.arg == "srcs")
    for entry in inputs.elts:
        relative = Path(entry.value)
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / relative, destination)
    result = subprocess.run(
        [sys.executable, "-I", str(tmp_path / "tools/go_binary.py"), str(tmp_path)],
        cwd=tmp_path, env=synthetic_test_environment(), capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


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
