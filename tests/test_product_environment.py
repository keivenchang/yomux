# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

from tools import generate_product_environment
from tools import product_environment as environment
from tests.tmux_runtime import clear_isolated_product_environment
from tests.tmux_runtime import patch_isolated_product_environment

ROOT = Path(__file__).resolve().parents[1]


def test_isolated_product_fixture_assignments_preserve_canonical_precedence(monkeypatch):
    clear_isolated_product_environment(monkeypatch, "FIXTURE")
    patch_isolated_product_environment(monkeypatch, {
        "YOLOMUX_FIXTURE": "legacy",
        "YOMUX_FIXTURE": "",
    })

    assert os.environ["YOMUX_FIXTURE"] == ""
    assert os.environ["YOLOMUX_FIXTURE"] == ""


@pytest.mark.parametrize("values,expected,present", [
    ({}, "default", False),
    ({"YOLOMUX_FIXTURE": "legacy"}, "legacy", True),
    ({"YOMUX_FIXTURE": "canonical"}, "canonical", True),
    ({"YOMUX_FIXTURE": "same", "YOLOMUX_FIXTURE": "same"}, "same", True),
    ({"YOMUX_FIXTURE": "canonical", "YOLOMUX_FIXTURE": "legacy"}, "canonical", True),
    ({"YOMUX_FIXTURE": "", "YOLOMUX_FIXTURE": "legacy"}, "", True),
    ({"YOMUX_FIXTURE": "0"}, "0", True),
])
def test_suffix_readers_preserve_presence_without_mutation(values, expected, present, capsys):
    before = dict(values)
    assert environment.product_env("FIXTURE", "default", environ=values) == expected
    assert environment.product_env_present("FIXTURE", environ=values) == present
    if present:
        assert environment.require_product_env("FIXTURE", environ=values) == expected
    else:
        with pytest.raises(KeyError):
            environment.require_product_env("FIXTURE", environ=values)
    assert values == before
    assert capsys.readouterr().err == ""


def test_suffix_reads_observe_late_updates_and_assignments(capsys):
    values = {"YOLOMUX_FIXTURE": "original"}
    values["YOLOMUX_FIXTURE"] = "changed"
    assert environment.product_env("FIXTURE", environ=values) == "changed"
    environment.apply_product_environment_assignments(values, {"FIXTURE": ""})
    assert environment.product_env("FIXTURE", environ=values) == ""
    values = environment.normalize_product_environment(values, warn=True)
    assert "deprecated" in capsys.readouterr().err
    environment.clear_product_environment_assignments(values, "FIXTURE")
    assert not environment.product_env_present("FIXTURE", environ=values)
    assert not environment.normalize_product_environment(values, warn=True)["YOMUX_ENVIRONMENT_LEGACY_INPUT"]
    assert capsys.readouterr().err == ""


def test_namespace_rename_changes_all_python_operations(monkeypatch):
    monkeypatch.setattr(environment, "CANONICAL_PREFIX", "NEXT_")
    monkeypatch.setattr(environment, "LEGACY_PREFIXES", ("YOMUX_", "YOLOMUX_"))
    monkeypatch.setattr(environment, "PRODUCT_PREFIXES", ("NEXT_", "YOMUX_", "YOLOMUX_"))
    values = {"NEXT_FIXTURE": "", "YOMUX_FIXTURE": "stale"}
    assert environment.product_env("FIXTURE", "default", environ=values) == ""
    environment.apply_product_environment_assignments(values, {"FIXTURE": "new"})
    assert values["NEXT_FIXTURE"] == values["YOMUX_FIXTURE"] == values["YOLOMUX_FIXTURE"] == "new"
    environment.clear_product_environment_assignments(values, "FIXTURE")
    assert not environment.product_env_present("FIXTURE", environ=values)


def test_selected_checkout_loads_its_own_namespace(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "product_environment.py").write_bytes((ROOT / "tools/product_environment.py").read_bytes())
    (tools / "product_namespace.json").write_text(json.dumps({"canonical": "NEXT_", "legacy": ["OLD_"]}))
    spec = importlib.util.spec_from_file_location("_selected_product_environment", tools / "product_environment.py")
    assert spec is not None and spec.loader is not None
    selected = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selected)
    assert selected.normalize_product_environment({"NEXT_INSTANCE": "private"})["OLD_INSTANCE"] == "private"
    assert selected.product_env_name("ROOT") == "NEXT_ROOT"


@pytest.mark.parametrize("values,expected,present", [
    ({}, "", False),
    ({"YOLOMUX_FIXTURE": "legacy"}, "legacy", True),
    ({"YOMUX_FIXTURE": "canonical\n\n"}, "canonical\n\n", True),
    ({"YOMUX_FIXTURE": "", "YOLOMUX_FIXTURE": "stale"}, "", True),
])
def test_shell_suffix_lookup_preserves_bytes_and_presence(values, expected, present):
    script = 'source "$1"; if product_env FIXTURE result; then printf present; else printf absent; fi; printf "\\0%s" "$result"'
    result = subprocess.run(["bash", "-c", script, "probe", str(ROOT / "tools/product_environment.sh")],
                            env={"PATH": "/usr/bin:/bin", **values}, capture_output=True, check=True)
    assert result.stdout == (b"present" if present else b"absent") + b"\0" + expected.encode()
    assert not result.stderr


def test_generated_namespaces_and_installed_shell_owner_match():
    namespace = json.loads((ROOT / "tools/product_namespace.json").read_text())
    assert namespace == json.loads((ROOT / "internal/productenv/namespace.json").read_text())
    source = (ROOT / "tools/product_environment.sh").read_text()
    assert generate_product_environment.render_namespace(namespace) in source
    installed = Path.home() / "dev/ai-config/agents/skills/yomux-dev-start/scripts/environment.sh"
    if installed.exists():
        assert installed.read_text() == source


def test_shell_rename_needs_no_caller_change(tmp_path):
    source = (ROOT / "tools/product_environment.sh").read_text()
    start = source.index(generate_product_environment.START)
    end = source.index(generate_product_environment.END) + len(generate_product_environment.END)
    alternate = tmp_path / "environment.sh"
    alternate.write_text(source[:start] + generate_product_environment.render_namespace(
        {"canonical": "NEXT_", "legacy": ["YOMUX_", "YOLOMUX_"]}) + source[end:])
    script = 'source "$1"; product_env FIXTURE result; [[ "$result" == "" ]]; product_env_assign FIXTURE=new; product_env FIXTURE result; [[ "$result" == new ]]; product_env_unset FIXTURE; ! product_env_present FIXTURE'
    result = subprocess.run(["bash", "-c", script, "probe", str(alternate)],
                            env={"PATH": "/usr/bin:/bin", "NEXT_FIXTURE": "", "YOMUX_FIXTURE": "stale"}, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_bazel_environment_scrub_preserves_renamed_namespace_provenance(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    source = (ROOT / "tools/product_environment.sh").read_text()
    start = source.index(generate_product_environment.START)
    end = source.index(generate_product_environment.END) + len(generate_product_environment.END)
    namespace = {"canonical": "NEXT_", "legacy": ["YOMUX_", "YOLOMUX_"]}
    (tools / "product_environment.sh").write_text(
        source[:start] + generate_product_environment.render_namespace(namespace) + source[end:]
    )
    (tools / "bazel_test_common.sh").write_bytes((ROOT / "tools/bazel_test_common.sh").read_bytes())
    script = 'source "$1"; bazel_test_prepare_environment; product_env GO_BINARY selected; [[ "$selected" == fixture-canonical ]]'
    result = subprocess.run(
        ["bash", "-c", script, "probe", str(tools / "bazel_test_common.sh")],
        env={"PATH": "/usr/bin:/bin", "TEST_TMPDIR": str(tmp_path / "test-tmp"),
             "NEXT_GO_BINARY": "fixture-canonical", "NEXT_BAZEL_EXECUTION": "native"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_python_readers_use_the_owner():
    failures = []
    for directory in ("tools", "yomux_lib"):
        for path in (ROOT / directory).rglob("*.py"):
            if path.name == "product_environment.py":
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"get", "getenv"}:
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        if node.args[0].value.startswith(("YOMUX_", "YOLOMUX_")):
                            failures.append(f"{path.relative_to(ROOT)}:{node.lineno}")
                if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
                    if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                        if node.slice.value.startswith(("YOMUX_", "YOLOMUX_")):
                            failures.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not failures, failures
