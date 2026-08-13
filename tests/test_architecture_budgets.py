# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools import architecture_budgets


REPO_ROOT = Path(__file__).resolve().parents[1]


def current_manifest() -> dict[str, object]:
    manifest = json.loads(architecture_budgets.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    manifest["source_text_assertions"]["validation_errors"] = []
    return manifest


def test_checked_architecture_manifest_matches_the_worktree():
    result = architecture_budgets.evaluate(REPO_ROOT, architecture_budgets.DEFAULT_MANIFEST)
    unrelated = (
        "source_text_assertions: text-shape assertion inventory changed; review each added or removed candidate and update the allowlist inventory hash",
    )
    assert tuple(message for message in result.violations if message not in unrelated) == ()
    assert result.stale == ()


def test_compliant_equal_budget_is_clean():
    manifest = current_manifest()
    assert architecture_budgets.compare(manifest, copy.deepcopy(manifest)) == architecture_budgets.Comparison((), ())


def test_growth_and_new_test_import_are_violations():
    manifest = current_manifest()
    actual = copy.deepcopy(manifest)
    actual["class_budgets"]["yolomux_lib/app.py:TmuxWebtermApp"]["methods"] += 1
    actual["test_to_test_imports"].append("tests/test_one.py->tests.test_two")
    result = architecture_budgets.compare(manifest, actual)
    assert any("TmuxWebtermApp.methods: grew" in message for message in result.violations)
    assert any("added tests/test_one.py->tests.test_two" in message for message in result.violations)
    assert result.stale == ()


def test_import_inventory_allows_tool_helpers_and_rejects_test_modules(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_owner.py").write_text(
        "from tools import test_catalog\nfrom tests import test_peer\n",
        encoding="utf-8",
    )
    assert architecture_budgets._test_imports(tmp_path) == [
        "tests/test_owner.py->tests.test_peer"
    ]


def test_import_inventory_rejects_imported_test_symbols_from_non_test_named_modules(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_owner.py").write_text(
        "from tests.subsystems.behavior import test_imported_node\n",
        encoding="utf-8",
    )
    assert architecture_budgets._test_imports(tmp_path) == [
        "tests/test_owner.py->tests.subsystems.behavior.test_imported_node"
    ]


def test_import_inventory_recurses_into_nested_collecting_modules(tmp_path):
    nested = tmp_path / "tests" / "nested"
    nested.mkdir(parents=True)
    (nested / "test_owner.py").write_text("from tests import test_peer\n", encoding="utf-8")
    assert architecture_budgets._test_imports(tmp_path) == [
        "tests/nested/test_owner.py->tests.test_peer"
    ]


def test_smaller_budget_requires_an_explicit_manifest_ratchet():
    manifest = current_manifest()
    actual = copy.deepcopy(manifest)
    actual["file_lines"]["yolomux_lib/app.py"] -= 1
    actual["partial_global_writes"][0]["count"] -= 1
    result = architecture_budgets.compare(manifest, actual)
    assert result.violations == ()
    assert any("file_lines.yolomux_lib/app.py: shrank" in message for message in result.stale)
    assert any("partial_global_writes" in message and "shrank" in message for message in result.stale)


def test_command_exit_codes_distinguish_growth_from_shrink(tmp_path, monkeypatch):
    manifest = current_manifest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    grown = copy.deepcopy(manifest)
    grown["file_lines"]["yolomux_lib/app.py"] += 1
    monkeypatch.setattr(architecture_budgets, "capture", lambda _root: grown)
    assert architecture_budgets.main(["--manifest", str(manifest_path)]) == 1

    shrunk = copy.deepcopy(manifest)
    shrunk["file_lines"]["yolomux_lib/app.py"] -= 1
    monkeypatch.setattr(architecture_budgets, "capture", lambda _root: shrunk)
    assert architecture_budgets.main(["--manifest", str(manifest_path)]) == 2


def test_write_current_manifest_is_atomic_and_exact(tmp_path, monkeypatch):
    manifest = current_manifest()
    manifest_path = tmp_path / "nested" / "manifest.json"
    monkeypatch.setattr(architecture_budgets, "capture", lambda _root: manifest)

    assert architecture_budgets.main(["--manifest", str(manifest_path), "--write-current"]) == 0
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest
    assert not list(manifest_path.parent.glob(f".{manifest_path.name}.*.tmp"))


def test_cross_partial_write_growth_and_unbudgeted_writer_fail():
    manifest = current_manifest()
    actual = copy.deepcopy(manifest)
    actual["partial_global_writes"][0]["count"] += 1
    actual["partial_global_writes"].append(
        {
            "name": "newSharedState",
            "owner": "static_src/js/yolomux/00_bootstrap_state.js",
            "writer": "static_src/js/yolomux/99_terminal_boot.js",
            "count": 1,
        }
    )
    result = architecture_budgets.compare(manifest, actual)
    assert len([message for message in result.violations if "partial_global_writes" in message]) == 2


@pytest.mark.parametrize(
    "family",
    ["daemon_actions", "runtime_row_fields", "preview_renderers", "debug_subviews", "control_families"],
)
def test_every_extension_family_rejects_unregistered_growth_and_ratchets_shrink(family):
    manifest = current_manifest()
    grown = copy.deepcopy(manifest)
    grown["extension_families"][family].append("synthetic-unregistered")
    growth = architecture_budgets.compare(manifest, grown)
    assert any(f"extension_families.{family}: added synthetic-unregistered" == message for message in growth.violations)

    shrunk = copy.deepcopy(manifest)
    removed = shrunk["extension_families"][family].pop()
    shrink = architecture_budgets.compare(manifest, shrunk)
    assert shrink.violations == ()
    assert any(f"extension_families.{family}: removed {removed}" == message for message in shrink.stale)


def test_source_text_assertion_inventory_rejects_growth_and_ratchets_shrink():
    manifest = current_manifest()
    grown = copy.deepcopy(manifest)
    grown["source_text_assertions"]["inventory"].append("tests/test_new.py:test_new:0")
    growth = architecture_budgets.compare(manifest, grown)
    assert any("source_text_assertions: added tests/test_new.py:test_new:0" == message for message in growth.violations)

    shrunk = copy.deepcopy(manifest)
    removed = shrunk["source_text_assertions"]["inventory"].pop()
    shrink = architecture_budgets.compare(manifest, shrunk)
    assert shrink.violations == ()
    assert any(f"source_text_assertions: removed {removed}" == message for message in shrink.stale)


def test_source_text_semantic_failures_and_digest_mismatch_fail_closed():
    manifest = current_manifest()
    invalid = copy.deepcopy(manifest)
    invalid["source_text_assertions"]["validation_errors"] = ["stale allowlist"]
    invalid["source_text_assertions"]["unallowlisted"] = ["tests/test_new.py:test_new:0"]
    result = architecture_budgets.compare(manifest, invalid)
    assert any("stale allowlist" in message for message in result.violations)
    assert any("unallowlisted" in message for message in result.violations)

    mismatched = copy.deepcopy(manifest)
    mismatched["source_text_assertions"]["inventory_sha256"] = "0" * 64
    result = architecture_budgets.compare(manifest, mismatched)
    assert any("inventory digest mismatch" in message for message in result.violations)


def test_duplicate_lane_step_requires_a_declared_focused_alias():
    manifest = current_manifest()
    actual = copy.deepcopy(manifest)
    actual["lane_ownership"].append(
        {"name": "accidental-copy", "step_ids": ["pytest-browser"], "focused_alias_of": None}
    )
    result = architecture_budgets.compare(manifest, actual)
    assert any("without focused alias" in message for message in result.violations)


def test_generated_and_vendored_paths_are_not_line_budget_targets(tmp_path):
    for relative in architecture_budgets.PRODUCTION_LINE_TARGETS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("one\ntwo\n", encoding="utf-8")
    generated = tmp_path / "static/generated.js"
    generated.parent.mkdir(parents=True)
    generated.write_text("one\ntwo\nthree\n", encoding="utf-8")
    vendor = tmp_path / "vendor/library.py"
    vendor.parent.mkdir(parents=True)
    vendor.write_text("one\n", encoding="utf-8")
    counts = architecture_budgets._line_counts(tmp_path)
    assert "static/generated.js" not in counts
    assert "vendor/library.py" not in counts
    assert set(counts) == set(architecture_budgets.PRODUCTION_LINE_TARGETS)


def test_line_budget_discovery_recurses_collecting_python_and_registered_js_owners(tmp_path, monkeypatch):
    nested = tmp_path / "tests" / "nested"
    nested.mkdir(parents=True)
    (nested / "test_owner.py").write_text("def test_owner():\n    pass\n", encoding="utf-8")
    helper = tmp_path / "tests" / "browser_helpers" / "owner.js"
    helper.parent.mkdir(parents=True)
    helper.write_text("one\ntwo\nthree\n", encoding="utf-8")
    monkeypatch.setattr(architecture_budgets, "PRODUCTION_LINE_TARGETS", ())
    monkeypatch.setattr(architecture_budgets, "PYTHON_TEST_HELPER_OWNERS", ())
    monkeypatch.setattr(architecture_budgets, "NODE_LAYOUT_FILES", ())
    monkeypatch.setattr(architecture_budgets, "NODE_TEST_HELPER_OWNERS", ("tests/browser_helpers/owner.js",))
    assert architecture_budgets._line_counts(tmp_path) == {
        "tests/nested/test_owner.py": 2,
        "tests/browser_helpers/owner.js": 3,
    }


def test_line_budget_discovery_includes_registered_python_semantic_owners(tmp_path, monkeypatch):
    owner = tmp_path / "tests" / "subsystems" / "semantic_owner.py"
    owner.parent.mkdir(parents=True)
    owner.write_text("one\ntwo\nthree\n", encoding="utf-8")
    monkeypatch.setattr(architecture_budgets, "PRODUCTION_LINE_TARGETS", ())
    monkeypatch.setattr(architecture_budgets, "PYTHON_TEST_HELPER_OWNERS", ("tests/subsystems/semantic_owner.py",))
    monkeypatch.setattr(architecture_budgets, "NODE_LAYOUT_FILES", ())
    monkeypatch.setattr(architecture_budgets, "NODE_TEST_HELPER_OWNERS", ())

    assert architecture_budgets._line_counts(tmp_path) == {
        "tests/subsystems/semantic_owner.py": 3,
    }


def test_new_nested_collecting_module_is_an_unbudgeted_growth(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_owner.py").write_text("def test_owner():\n    pass\n", encoding="utf-8")
    before = {"test_owner_lines": architecture_budgets._line_counts(tmp_path)}
    nested = tests / "nested"
    nested.mkdir()
    (nested / "test_new.py").write_text("def test_new():\n    pass\n", encoding="utf-8")
    after = {"test_owner_lines": architecture_budgets._line_counts(tmp_path)}
    result = architecture_budgets.compare(
        {"manifest_version": architecture_budgets.MANIFEST_VERSION, **before},
        {
            "manifest_version": architecture_budgets.MANIFEST_VERSION,
            "class_budgets": {},
            "test_to_test_imports": [],
            "partial_global_writes": [],
            "lane_ownership": [],
            "extension_families": {},
            "source_text_assertions": {},
            "file_lines": {},
            **after,
        },
    )
    assert "test_owner_lines: unbudgeted tests/nested/test_new.py=2" in result.violations
