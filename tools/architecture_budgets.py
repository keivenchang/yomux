#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Ratchet structural complexity without freezing implementation text.

Growth is a gate failure. A smaller measured value is a stale baseline so a
reviewer must deliberately accept the improved architecture in the manifest.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools import textshape_assertion_guard  # noqa: E402 - direct script execution needs repository root
from tools.test_catalog import NODE_LAYOUT_FILES  # noqa: E402
from tools.test_catalog import NODE_TEST_HELPER_OWNERS  # noqa: E402
from tools.test_catalog import PYTHON_TEST_HELPER_OWNERS  # noqa: E402
from tools.test_plan import StepId  # noqa: E402


DEFAULT_MANIFEST = REPO_ROOT / "tests/fixtures/architecture_budgets/v1.json"
MANIFEST_VERSION = 1

CLASS_TARGETS: Final[tuple[tuple[str, str], ...]] = (
    ("yolomux_lib/app.py", "TmuxWebtermApp"),
    ("yolomux_lib/server.py", "Handler"),
    ("yolomux_lib/stats_current/service.py", "StatsCurrentService"),
    ("yolomux_lib/local_services/registry.py", "LocalServiceRegistry"),
    ("yolomux_lib/watchd.py", "PersistentWatchService"),
    ("yolomux_lib/infra/jobd.py", "PersistentJobBroker"),
    ("yolomux_lib/infra/jobd.py", "JobProductStore"),
    ("yolomux_lib/infra/background_owner.py", "BackgroundOwnerRegistry"),
)
PRODUCTION_LINE_TARGETS: Final[tuple[str, ...]] = (
    "yolomux_lib/app.py",
    "yolomux_lib/server.py",
    "yolomux_lib/search/file_index.py",
    "yolomux_lib/workspace/session_files.py",
    "yolomux_lib/stats_current/service.py",
    "static_src/js/yolomux/85_debug_panel.js",
    "static_src/js/yolomux/99_terminal_boot.js",
    "static_src/js/yolomux/20_layout_state.js",
    "static_src/js/yolomux/10_core_utils.js",
    "static_src/js/yolomux/40_file_explorer_files.js",
    "static_src/css/yolomux/50_terminal_file_tree.css",
    "static_src/css/yolomux/30_preferences_changes.css",
    "static_src/css/yolomux/60_editor_file_panels.css",
)
GENERATED_PARTS: Final[set[str]] = {"static", "node_modules", "vendor", "dist", "build", "__pycache__"}
DECLARATION_RE = re.compile(r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\b", re.MULTILINE)
WRITE_RE = re.compile(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*(?:\+\+|--|=(?!=)|\+=|-=|\*=|/=|\?\?=|&&=|\|\|=)")
CONTROL_FAMILY_NAMES: Final[tuple[str, ...]] = (
    "createToolbarButton",
    "createActionRow",
    "createSegmentedControl",
    "toolbarButtonHtml",
    "actionRowHtml",
    "segmentedControlHtml",
    "bindActionDispatcher",
)


@dataclass(frozen=True)
class Comparison:
    violations: tuple[str, ...]
    stale: tuple[str, ...]


def _python_tree(root: Path, relative: str) -> ast.Module:
    return ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)


def _class_budget(root: Path, relative: str, class_name: str) -> dict[str, int]:
    target = next(node for node in _python_tree(root, relative).body if isinstance(node, ast.ClassDef) and node.name == class_name)
    methods = sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in target.body)
    fields = {
        assigned.attr
        for method in target.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(method)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
        for assigned in (node.targets if isinstance(node, ast.Assign) else (node.target,))
        if isinstance(assigned, ast.Attribute) and isinstance(assigned.value, ast.Name) and assigned.value.id == "self"
    }
    return {"methods": methods, "self_fields": len(fields)}


def _collecting_python_tests(root: Path) -> tuple[Path, ...]:
    return tuple(sorted((root / "tests").rglob("test_*.py")))


def _registered_js_test_owners() -> tuple[str, ...]:
    return tuple(sorted({*NODE_LAYOUT_FILES, *NODE_TEST_HELPER_OWNERS}))


def _test_imports(root: Path) -> list[str]:
    imports: set[str] = set()
    for path in _collecting_python_tests(root):
        for node in ast.walk(_python_tree(root, path.relative_to(root).as_posix())):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ("tests" if node.level else "")
                if prefix == "tests":
                    modules = [f"tests.{alias.name}" for alias in node.names]
                elif prefix:
                    modules = [prefix]
                if prefix.startswith("tests"):
                    imports.update(
                        f"{path.relative_to(root).as_posix()}->{prefix}.{alias.name}"
                        for alias in node.names
                        if alias.name.startswith("test_")
                    )
            for module in modules:
                leaf = module.rsplit(".", 1)[-1]
                if module.startswith("tests.") and leaf.startswith("test_"):
                    imports.add(f"{path.relative_to(root).as_posix()}->{module}")
    return sorted(imports)


def _partial_global_writes(root: Path) -> list[dict[str, Any]]:
    directory = root / "static_src/js/yolomux"
    paths = sorted(directory.glob("*.js"))
    owners: dict[str, str] = {}
    texts: dict[str, str] = {}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        texts[relative] = text
        for name in DECLARATION_RE.findall(text):
            owners.setdefault(name, relative)
    rows = []
    for writer, text in texts.items():
        counts = Counter(WRITE_RE.findall(text))
        for name, count in sorted(counts.items()):
            owner = owners.get(name)
            if owner is not None and writer != owner:
                rows.append({"name": name, "owner": owner, "writer": writer, "count": count})
    return rows


def _lane_ownership(root: Path) -> list[dict[str, Any]]:
    def step_id_value(node: ast.AST) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "StepId":
            return StepId[node.attr].value
        raise ValueError(f"unsupported lane step identity: {ast.dump(node)}")

    rows = []
    for node in ast.walk(_python_tree(root, "tools/test_plan.py")):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "LaneSpec" or len(node.args) < 3:
            continue
        name = ast.literal_eval(node.args[0])
        if not isinstance(node.args[2], (ast.Tuple, ast.List)):
            raise ValueError("LaneSpec step IDs must be a literal sequence")
        steps = [step_id_value(item) for item in node.args[2].elts]
        alias = None
        for keyword in node.keywords:
            if keyword.arg == "focused_alias_of":
                alias = ast.literal_eval(keyword.value)
        rows.append({"name": name, "step_ids": steps, "focused_alias_of": alias})
    return sorted(rows, key=lambda row: row["name"])


def _line_targets(root: Path) -> tuple[str, ...]:
    collecting = tuple(path.relative_to(root).as_posix() for path in _collecting_python_tests(root))
    return tuple(
        dict.fromkeys(
            (
                *PRODUCTION_LINE_TARGETS,
                *collecting,
                *PYTHON_TEST_HELPER_OWNERS,
                *_registered_js_test_owners(),
            )
        )
    )


def _line_counts(root: Path) -> dict[str, int]:
    counts = {}
    for relative in _line_targets(root):
        path = root / relative
        if path.exists() and not GENERATED_PARTS.intersection(path.parts):
            counts[relative] = len(path.read_text(encoding="utf-8").splitlines())
    return counts


def _line_budget_categories(root: Path) -> tuple[dict[str, int], dict[str, int]]:
    all_counts = _line_counts(root)
    test_owners = {
        relative: count
        for relative, count in all_counts.items()
        if relative.startswith("tests/")
    }
    production = {
        relative: count
        for relative, count in all_counts.items()
        if not relative.startswith("tests/")
    }
    return production, test_owners


def _string_set(node: ast.AST, values: dict[str, set[str]]) -> set[str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        return values.get(node.id)
    if isinstance(node, ast.Starred):
        return _string_set(node.value, values)
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        result: set[str] = set()
        for item in node.elts:
            resolved = _string_set(item, values)
            if resolved is None:
                return None
            result.update(resolved)
        return result
    if isinstance(node, ast.Dict):
        result = set()
        for key in node.keys:
            if key is None:
                continue
            resolved = _string_set(key, values)
            if resolved is None:
                return None
            result.update(resolved)
        return result
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"frozenset", "set", "tuple", "list"} and node.args:
        return _string_set(node.args[0], values)
    if isinstance(node, ast.DictComp) and isinstance(node.key, ast.Name) and len(node.generators) == 1:
        generator = node.generators[0]
        if isinstance(generator.target, ast.Name) and node.key.id == generator.target.id:
            return _string_set(generator.iter, values)
    return None


def _daemon_actions(root: Path) -> list[str]:
    rows: list[str] = []
    for path in sorted((root / "yolomux_lib").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        tree = _python_tree(root, relative)
        values: dict[str, set[str]] = {}
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            resolved = _string_set(node.value, values)
            for target in targets:
                if isinstance(target, ast.Name) and resolved is not None:
                    values[target.id] = resolved
                if not isinstance(target, ast.Name) or not target.id.endswith("_COMMAND_ROUTER"):
                    continue
                if not isinstance(node.value, ast.Call) or not node.value.args:
                    continue
                actions = _string_set(node.value.args[0], values)
                if actions is not None:
                    rows.extend(f"{relative}:{target.id}:{action}" for action in actions)
    return sorted(rows)


def _runtime_row_fields(root: Path) -> list[str]:
    tree = _python_tree(root, "yolomux_lib/local_service_projection.py")
    target = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LocalServiceRuntimeRow")
    return sorted(
        node.target.id
        for node in target.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    )


def _registry_ids(root: Path, relative: str, registry: str, constructor: str) -> list[str]:
    text = (root / relative).read_text(encoding="utf-8")
    start = text.index(f"const {registry} = Object.freeze([")
    end = text.index("\n]);", start)
    body = text[start:end]
    return sorted(re.findall(rf"{re.escape(constructor)}\(\{{\s*id:\s*['\"]([^'\"]+)['\"]", body))


def _control_families(root: Path) -> list[str]:
    text = (root / "static_src/js/yolomux/76_panel_dom_actions.js").read_text(encoding="utf-8")
    return sorted(name for name in CONTROL_FAMILY_NAMES if re.search(rf"^function\s+{re.escape(name)}\s*\(", text, re.MULTILINE))


def _source_text_assertions(root: Path) -> dict[str, Any]:
    findings = textshape_assertion_guard.find_textshape_assertions(root / "tests", repo_root=root)
    return {
        "inventory": sorted(finding.inventory_key for finding in findings),
        "inventory_sha256": textshape_assertion_guard.assertion_inventory_sha256(findings),
        "validation_errors": textshape_assertion_guard.validate_allowlist(
            findings,
            expected_inventory_sha256=textshape_assertion_guard.TEXT_SHAPE_ASSERTION_INVENTORY_SHA256,
        ),
        "unallowlisted": sorted(
            finding.inventory_key
            for finding in findings
            if finding.allowlist_key not in textshape_assertion_guard.TEXT_SHAPE_ASSERTION_ALLOWLIST
        ),
    }


def capture(root: Path) -> dict[str, Any]:
    file_lines, test_owner_lines = _line_budget_categories(root)
    return {
        "manifest_version": MANIFEST_VERSION,
        "class_budgets": {
            f"{relative}:{class_name}": _class_budget(root, relative, class_name)
            for relative, class_name in CLASS_TARGETS
        },
        "test_to_test_imports": _test_imports(root),
        "partial_global_writes": _partial_global_writes(root),
        "lane_ownership": _lane_ownership(root),
        "extension_families": {
            "daemon_actions": _daemon_actions(root),
            "runtime_row_fields": _runtime_row_fields(root),
            "preview_renderers": _registry_ids(root, "static_src/js/yolomux/00_bootstrap_state.js", "PREVIEW_RENDERERS", "previewRendererStrategy"),
            "debug_subviews": _registry_ids(root, "static_src/js/yolomux/85_debug_panel.js", "DEBUG_SUBVIEWS", "debugSubviewDescriptor"),
            "control_families": _control_families(root),
        },
        "source_text_assertions": _source_text_assertions(root),
        "file_lines": file_lines,
        "test_owner_lines": test_owner_lines,
    }


def _compare_numeric_map(category: str, expected: dict[str, Any], actual: dict[str, Any], violations: list[str], stale: list[str]) -> None:
    for key in sorted(set(expected) | set(actual)):
        if key not in expected:
            violations.append(f"{category}: unbudgeted {key}={actual[key]}")
        elif key not in actual:
            stale.append(f"{category}: retired {key} (was {expected[key]})")
        elif isinstance(expected[key], dict):
            _compare_numeric_map(f"{category}.{key}", expected[key], actual[key], violations, stale)
        elif actual[key] > expected[key]:
            violations.append(f"{category}.{key}: grew {expected[key]} -> {actual[key]}")
        elif actual[key] < expected[key]:
            stale.append(f"{category}.{key}: shrank {expected[key]} -> {actual[key]}")


def _row_key(row: dict[str, Any], fields: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in fields)


def compare(expected: dict[str, Any], actual: dict[str, Any]) -> Comparison:
    violations: list[str] = []
    stale: list[str] = []
    if expected.get("manifest_version") != MANIFEST_VERSION:
        violations.append(f"manifest_version: expected {MANIFEST_VERSION}, got {expected.get('manifest_version')}")
    _compare_numeric_map("class_budgets", expected.get("class_budgets", {}), actual["class_budgets"], violations, stale)
    _compare_numeric_map("file_lines", expected.get("file_lines", {}), actual["file_lines"], violations, stale)
    _compare_numeric_map("test_owner_lines", expected.get("test_owner_lines", {}), actual["test_owner_lines"], violations, stale)
    expected_imports = set(expected.get("test_to_test_imports", []))
    actual_imports = set(actual["test_to_test_imports"])
    violations.extend(f"test_to_test_imports: added {value}" for value in sorted(actual_imports - expected_imports))
    stale.extend(f"test_to_test_imports: removed {value}" for value in sorted(expected_imports - actual_imports))
    for family, expected_rows in expected.get("extension_families", {}).items():
        actual_rows = set(actual.get("extension_families", {}).get(family, []))
        expected_set = set(expected_rows)
        violations.extend(f"extension_families.{family}: added {value}" for value in sorted(actual_rows - expected_set))
        stale.extend(f"extension_families.{family}: removed {value}" for value in sorted(expected_set - actual_rows))
    for family in sorted(set(actual.get("extension_families", {})) - set(expected.get("extension_families", {}))):
        violations.append(f"extension_families: unbudgeted family {family}")
    expected_text_state = expected.get("source_text_assertions", {})
    actual_text_state = actual.get("source_text_assertions", {})
    expected_text = set(expected_text_state.get("inventory", []))
    actual_text = set(actual_text_state.get("inventory", []))
    violations.extend(f"source_text_assertions: added {value}" for value in sorted(actual_text - expected_text))
    stale.extend(f"source_text_assertions: removed {value}" for value in sorted(expected_text - actual_text))
    if actual_text_state.get("inventory_sha256") != expected_text_state.get("inventory_sha256") and actual_text == expected_text:
        violations.append("source_text_assertions: inventory digest mismatch")
    violations.extend(f"source_text_assertions: {value}" for value in actual_text_state.get("validation_errors", []))
    violations.extend(f"source_text_assertions: unallowlisted {value}" for value in actual_text_state.get("unallowlisted", []))
    expected_writes = {_row_key(row, ("name", "owner", "writer")): row["count"] for row in expected.get("partial_global_writes", [])}
    actual_writes = {_row_key(row, ("name", "owner", "writer")): row["count"] for row in actual["partial_global_writes"]}
    _compare_numeric_map("partial_global_writes", expected_writes, actual_writes, violations, stale)
    expected_lanes = {_row_key(row, ("name",)): row for row in expected.get("lane_ownership", [])}
    actual_lanes = {_row_key(row, ("name",)): row for row in actual["lane_ownership"]}
    if expected_lanes != actual_lanes:
        added = sorted(set(actual_lanes) - set(expected_lanes))
        removed = sorted(set(expected_lanes) - set(actual_lanes))
        changed = sorted(key for key in set(expected_lanes) & set(actual_lanes) if expected_lanes[key] != actual_lanes[key])
        violations.extend(f"lane_ownership: added {key[0]}" for key in added)
        stale.extend(f"lane_ownership: removed {key[0]}" for key in removed)
        violations.extend(f"lane_ownership: changed {key[0]}" for key in changed)
    step_owners: dict[str, str] = {}
    lane_by_name = {row["name"]: row for row in actual["lane_ownership"]}
    for row in actual["lane_ownership"]:
        for step in row["step_ids"]:
            previous = step_owners.get(step)
            if previous and row["focused_alias_of"] != previous and lane_by_name.get(previous, {}).get("focused_alias_of") != row["name"]:
                violations.append(f"lane_ownership: step {step} owned by {previous} and {row['name']} without focused alias")
            step_owners.setdefault(step, row["name"])
    return Comparison(tuple(violations), tuple(stale))


def evaluate(root: Path, manifest: Path) -> Comparison:
    expected = json.loads(manifest.read_text(encoding="utf-8"))
    return compare(expected, capture(root))


def write_current_manifest(manifest: Path, current: dict[str, Any]) -> None:
    """Atomically accept one reviewed current structural inventory as the next ratchet."""

    manifest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=manifest.parent,
        prefix=f".{manifest.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(json.dumps(current, indent=2, sort_keys=True))
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(manifest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--print-current", action="store_true")
    parser.add_argument("--write-current", action="store_true")
    args = parser.parse_args(argv)
    current = capture(args.root.resolve())
    if args.print_current:
        print(json.dumps(current, indent=2, sort_keys=True))
        return 0
    if args.write_current:
        write_current_manifest(args.manifest, current)
        return 0
    result = compare(json.loads(args.manifest.read_text(encoding="utf-8")), current)
    for message in result.violations:
        print(f"architecture budget violation: {message}", file=sys.stderr)
    for message in result.stale:
        print(f"architecture budget stale: {message}", file=sys.stderr)
    return 1 if result.violations else 2 if result.stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
