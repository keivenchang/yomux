#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Capture and compare the versioned behavior-preserving refactor inventory."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_COMMIT = "e68c0d709a7780ea4896685288c14ec44febec30"
MANIFEST_VERSION = 1
DEFAULT_MANIFEST = REPO_ROOT / "tests/fixtures/refactor_characterization/v1.json"

PYTHON_INPUTS = (
    "yolomux_lib/app.py", "yolomux_lib/server.py", "yolomux_lib/http_routes.py",
    "yolomux_lib/statusd.py", "yolomux_lib/watchd.py", "yolomux_lib/approval/approvald.py",
    "yolomux_lib/infra/jobd.py", "yolomux_lib/search/search_indexer.py",
    "yolomux_lib/local_services/rpc.py", "yolomux_lib/workspace/session_files.py",
    "tests/conftest.py", "tests/gate_harness.py", "tools/check.py",
)
FRONTEND_INPUTS = (
    "static_src/js/yolomux/00_bootstrap_state.js", "static_src/js/yolomux/20_layout_state.js",
    "static_src/js/yolomux/40_file_explorer_files.js",
    "static_src/js/yolomux/82_chat_panel.js", "static_src/js/yolomux/84_stats_current.js",
    "static_src/js/yolomux/85_debug_panel.js", "static_src/js/yolomux/89_preview_renderers.js",
    "static_src/js/yolomux/99_terminal_boot.js",
)
TEST_INPUTS = (
    "tests/test_app.py", "tests/test_local_services_launch.py", "tests/test_local_services_rpc.py",
    "tests/test_session_files.py", "tests/test_check_runner.py", "tests/layout_async.test.js",
    "tests/layout_url.test.js", "tests/tabber.test.js", "tests/stats_current_ui.test.js",
)
ALL_INPUTS = tuple(dict.fromkeys((*PYTHON_INPUTS, *FRONTEND_INPUTS, *TEST_INPUTS)))

NONDETERMINISTIC_EXCLUSIONS = (
    {"field": "runtime.pid", "scope": "behavior fixtures", "reason": "OS-assigned process identity differs per run; PID field presence and coercion stay inventoried."},
    {"field": "runtime.wall_clock", "scope": "behavior fixtures", "reason": "Wall timestamps differ per run; stored_at field/order and explicit fixture timestamps stay pinned."},
    {"field": "runtime.monotonic", "scope": "behavior fixtures", "reason": "Monotonic origin differs per process; timer names, cadence literals, and cleanup order stay inventoried."},
    {"field": "runtime.temp_root", "scope": "fixture paths", "reason": "Per-process temporary roots differ; path constructors and suffix identities stay inventoried."},
    {"field": "browser.geometry_and_paint", "scope": "source-only harness", "reason": "This non-browser harness cannot stabilize viewport/font/GPU paint; DOM contract literals and external browser node IDs stay inventoried."},
)


@dataclass(frozen=True)
class Source:
    label: str
    reader: Callable[[str], str]

    def text(self, path: str) -> str:
        return self.reader(path)


def git_source(commit: str) -> Source:
    def read(path: str) -> str:
        result = subprocess.run(
            ["git", "show", f"{commit}:{path}"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return ""
        return result.stdout
    return Source(f"git:{commit}", read)


def worktree_source() -> Source:
    def read(path: str) -> str:
        try:
            return (REPO_ROOT / path).read_text(encoding="utf-8")
        except OSError:
            return ""
    return Source("worktree", read)


def _tree(source: Source, path: str) -> ast.Module | None:
    text = source.text(path)
    if not text:
        return None
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    arguments = [argument.arg for argument in (*node.args.posonlyargs, *node.args.args)]
    if node.args.vararg:
        arguments.append(f"*{node.args.vararg.arg}")
    arguments.extend(argument.arg for argument in node.args.kwonlyargs)
    if node.args.kwarg:
        arguments.append(f"**{node.args.kwarg.arg}")
    return f"{node.name}({','.join(arguments)})"


def public_facades(source: Source) -> list[str]:
    targets = {"yolomux_lib/app.py": {"TmuxWebtermApp"}, "yolomux_lib/server.py": {"Handler"}}
    values = []
    for path, classes in targets.items():
        tree = _tree(source, path)
        if tree is None:
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name in classes:
                values.extend(
                    f"{path}:{node.name}.{_signature(child)}"
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.name.startswith("_")
                )
    return sorted(values)


def route_catalog(source: Source) -> list[str]:
    tree = _tree(source, "yolomux_lib/http_routes.py")
    values = []
    if tree is None:
        return values
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "Route":
            continue
        values.append(ast.dump(node, annotate_fields=True, include_attributes=False))
    return sorted(values)


def daemon_actions(source: Source) -> list[str]:
    paths = PYTHON_INPUTS[3:8]
    values: set[str] = set()
    for path in paths:
        tree = _tree(source, path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "action":
                values.update(
                    f"{path}:{item.value}" for comparator in node.comparators
                    for item in ([comparator] if isinstance(comparator, ast.Constant) else getattr(comparator, "elts", []))
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
            if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
                literals = [item.value for item in node.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
                if literals and any(value in {"ping", "status", "shutdown", "lease"} for value in literals):
                    values.update(f"{path}:{value}" for value in literals)
    return sorted(values)


def wire_storage_identities(source: Source) -> list[str]:
    suffixes = ("VERSION", "PROTOCOL", "SCHEMA", "GENERATION", "KEY", "PATH", "DIRNAME", "SOCKET_NAME")
    values = []
    for path in PYTHON_INPUTS:
        tree = _tree(source, path)
        if tree is None:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            for target in targets:
                if isinstance(target, ast.Name) and target.id.upper() == target.id and any(token in target.id for token in suffixes):
                    values.append(f"{path}:{target.id}={ast.dump(value, annotate_fields=False, include_attributes=False)}")
    return sorted(values)


def frontend_globals_state(source: Source) -> list[str]:
    patterns = (
        ("function", re.compile(r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", re.M)),
        ("global", re.compile(r"(?:window|globalThis)\.([A-Za-z_$][\w$]*)\s*=")),
        ("state", re.compile(r"^\s*(?:const|let)\s+([A-Za-z_$][\w$]*(?:State|Cache|By\w+|Scope|Controller))\b", re.M)),
    )
    values = []
    for path in FRONTEND_INPUTS:
        text = source.text(path)
        for kind, pattern in patterns:
            values.extend(f"{path}:{kind}:{match.group(1)}" for match in pattern.finditer(text))
    return sorted(set(values))


def dom_contracts(source: Source) -> list[str]:
    pattern = re.compile(r"(?:aria-[\w-]+|data-[\w-]+|class(?:Name)?|role|id)\s*[=:]\s*['\"]([^'\"]{1,120})['\"]")
    values = []
    for path in FRONTEND_INPUTS:
        values.extend(f"{path}:{match.group(0)}" for match in pattern.finditer(source.text(path)))
    return sorted(set(values))


def lifecycle_calls(source: Source) -> list[str]:
    pattern = re.compile(r"\b(addEventListener|removeEventListener|setInterval|clearInterval|setTimeout|clearTimeout|EventSource|WebSocket|MutationObserver|ResizeObserver|AbortController|close|disconnect|abort)\s*\(")
    values = []
    for path in FRONTEND_INPUTS:
        text = source.text(path)
        for line_no, line in enumerate(text.splitlines(), 1):
            for match in pattern.finditer(line):
                values.append(f"{path}:{line_no}:{match.group(1)}")
    return sorted(values)


def fixture_lifecycles(source: Source) -> list[str]:
    values = []
    for path in ("tests/conftest.py", "tests/gate_harness.py"):
        tree = _tree(source, path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = []
            for child in ast.walk(node):
                if isinstance(child, (ast.Yield, ast.YieldFrom)):
                    calls.append("yield")
                elif isinstance(child, ast.Call):
                    name = ast.unparse(child.func)
                    if any(token in name for token in ("start", "stop", "close", "quit", "join", "cleanup", "rmtree", "terminate")):
                        calls.append(name)
            if calls:
                values.append(f"{path}:{node.name}:{'->'.join(calls)}")
    return sorted(values)


def gate_lanes(source: Source) -> list[str]:
    values = []
    for path in ("tools/test_plan.py", "tools/check.py"):
        tree = _tree(source, path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id in {"Lane", "LaneSpec"}) or (isinstance(node.func, ast.Attribute) and node.func.attr in {"Lane", "LaneSpec"})):
                values.append(f"{path}:{ast.dump(node, annotate_fields=True, include_attributes=False)}")
    return sorted(values)


def external_test_ids(source: Source) -> list[str]:
    values = []
    for path in TEST_INPUTS:
        text = source.text(path)
        if path.endswith(".py"):
            tree = _tree(source, path)
            if tree is not None:
                values.extend(f"{path}::{node.name}" for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"))
        else:
            values.extend(f"{path}::{match.group(2)}" for match in re.finditer(r"\btest\(\s*(['\"])(.*?)\1", text))
    return sorted(set(values))


EXTRACTORS = {
    "public_facade_methods": public_facades,
    "routes": route_catalog,
    "daemon_actions": daemon_actions,
    "wire_storage_identities": wire_storage_identities,
    "frontend_globals_state_domains": frontend_globals_state,
    "dom_contracts": dom_contracts,
    "timers_listeners_streams": lifecycle_calls,
    "fixture_lifecycles": fixture_lifecycles,
    "gate_lanes": gate_lanes,
    "external_test_node_ids": external_test_ids,
}


def behavioral_fixtures() -> dict[str, object]:
    record = {"version": 2, "signature": "abc", "stored_at": 100.0, "payload": {"z": 1, "a": 2}}
    return {
        "compact_sorted_json": json.dumps(record, sort_keys=True, separators=(",", ":")),
        "record_manifest_cleanup_order": ["record.write", "manifest.write", "post_write.account", "post_write.prune"],
        "fixture_lifecycle_order": ["app.start", "server.start", "yield", "browser.quit", "server.stop", "thread.join", "app.stop", "temp_root.remove"],
        "rpc_binary_frame_order": ["metadata_length", "metadata_bytes", "binary_bytes"],
    }


def capture(source: Source) -> dict[str, object]:
    inventories = {name: extractor(source) for name, extractor in EXTRACTORS.items()}
    blob_hashes = {path: hashlib.sha256(source.text(path).encode("utf-8")).hexdigest() for path in ALL_INPUTS}
    return {
        "manifest_version": MANIFEST_VERSION,
        "source": source.label,
        "inputs": list(ALL_INPUTS),
        "blob_sha256": blob_hashes,
        "inventories": inventories,
        "behavioral_fixtures": behavioral_fixtures(),
        "nondeterministic_exclusions": list(NONDETERMINISTIC_EXCLUSIONS),
    }


def manifest_snapshot(captured: dict[str, object]) -> dict[str, object]:
    inventories = captured["inventories"]
    fingerprints = {}
    for name, values in inventories.items():
        encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        fingerprints[name] = {"count": len(values), "sha256": hashlib.sha256(encoded).hexdigest()}
    blob_items = sorted(captured["blob_sha256"].items())
    blob_fingerprint = hashlib.sha256(json.dumps(blob_items, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "manifest_version": captured["manifest_version"],
        "source": captured["source"],
        "inputs": captured["inputs"],
        "blob_fingerprint": {"count": len(blob_items), "sha256": blob_fingerprint},
        "inventory_fingerprints": fingerprints,
        "behavioral_fixtures": captured["behavioral_fixtures"],
        "nondeterministic_exclusions": captured["nondeterministic_exclusions"],
    }


def compare(expected: dict[str, object], actual: dict[str, object]) -> dict[str, object]:
    differences = {}
    baseline = capture(git_source(BASELINE_COMMIT))
    for category, expected_values in baseline["inventories"].items():
        actual_values = actual["inventories"].get(category, [])
        removed = sorted(set(expected_values) - set(actual_values))
        added = sorted(set(actual_values) - set(expected_values))
        if removed or added:
            differences[category] = {"removed": removed, "added": added}
    return {"equal": not differences, "differences": differences}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("capture-baseline", "verify-baseline", "compare-worktree"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    baseline = capture(git_source(BASELINE_COMMIT))
    if args.command == "capture-baseline":
        print(json.dumps(manifest_snapshot(baseline), sort_keys=True, separators=(",", ":")))
        return 0
    expected = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.command == "verify-baseline":
        equal = expected == manifest_snapshot(baseline)
        print(json.dumps({"equal": equal, "baseline": BASELINE_COMMIT}, sort_keys=True))
        return 0 if equal else 1
    result = compare(expected, capture(worktree_source()))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
