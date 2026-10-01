# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Regression tests for the source/text-shape assertion detector."""

from __future__ import annotations

from pathlib import Path

from tools import textshape_assertion_guard


REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_test(root: Path, source: str) -> tuple[Path, Path]:
    tests = root / "tests"
    tests.mkdir()
    path = tests / "test_fixture.py"
    path.write_text(source, encoding="utf-8")
    return root, tests


def test_detector_flags_each_retired_textshape_pattern(tmp_path: Path, capsys):
    root, tests = _write_test(
        tmp_path,
        """
import inspect
import json
from pathlib import Path

def test_serialized_diagnostic():
    diagnostic_text = json.dumps({"fs_batch_body_read_ms": 1.0})
    assert "body" not in diagnostic_text

def test_finder_reload_source_contract():
    source = inspect.getsource(test_finder_reload_source_contract)
    assert "reload" in source

def test_guarded_fetch_source_contract():
    source = Path(__file__).read_text(encoding="utf-8")
    assert source != ""

def test_tmux_args_source_contract():
    source = inspect.getsource(test_tmux_args_source_contract)
    assert "tmux_args" in source and "argv" in source
""",
    )

    findings = textshape_assertion_guard.find_textshape_assertions(tests, repo_root=root)

    assert {finding.function for finding in findings} == {
        "test_serialized_diagnostic",
        "test_finder_reload_source_contract",
        "test_guarded_fetch_source_contract",
        "test_tmux_args_source_contract",
    }
    assert textshape_assertion_guard.main(["--root", str(root)]) == 1
    output = capsys.readouterr().out
    assert "4 candidates, 0 allowlisted, 4 unallowlisted" in output


def test_detector_ignores_behavioral_assertions_and_assertion_messages(tmp_path: Path):
    root, tests = _write_test(
        tmp_path,
        """
import json

def test_response_behaviour():
    diagnostic_text = json.dumps({"status": 200})
    response = {"status": 200, "body": "ok"}
    assert response["status"] == 200, diagnostic_text
    assert response["body"] == "ok"
""",
    )

    assert textshape_assertion_guard.find_textshape_assertions(tests, repo_root=root) == []


def test_allowlist_requires_a_reason_and_rejects_stale_entries(monkeypatch, tmp_path: Path):
    root, tests = _write_test(
        tmp_path,
        """
import inspect

def test_source_contract():
    source = inspect.getsource(test_source_contract)
    assert "source_contract" in source
""",
    )
    finding = textshape_assertion_guard.find_textshape_assertions(tests, repo_root=root)[0]
    monkeypatch.setattr(textshape_assertion_guard, "TEXT_SHAPE_ASSERTION_ALLOWLIST", {finding.allowlist_key: ""})

    assert textshape_assertion_guard.validate_allowlist([finding]) == [
        f"{finding.allowlist_key}: allowlist reason must be non-empty"
    ]
    monkeypatch.setattr(textshape_assertion_guard, "TEXT_SHAPE_ASSERTION_ALLOWLIST", {"tests/test_fixture.py:test_other": "no longer applies"})
    assert textshape_assertion_guard.validate_allowlist([finding]) == [
        "tests/test_fixture.py:test_other: stale text-shape allowlist entry"
    ]


def test_inventory_digest_rejects_a_new_assertion_in_an_allowlisted_function(monkeypatch, tmp_path: Path):
    root, tests = _write_test(
        tmp_path,
        """
import inspect

def test_source_contract():
    source = inspect.getsource(test_source_contract)
    assert "source_contract" in source
    assert "inspect" in source
""",
    )
    findings = textshape_assertion_guard.find_textshape_assertions(tests, repo_root=root)

    assert len(findings) == 2
    monkeypatch.setattr(textshape_assertion_guard, "TEXT_SHAPE_ASSERTION_ALLOWLIST", {findings[0].allowlist_key: "audited structural contract"})
    assert textshape_assertion_guard.validate_allowlist(
        findings,
        expected_inventory_sha256=textshape_assertion_guard.assertion_inventory_sha256(findings[:1]),
    ) == [
        "text-shape assertion inventory changed; review each added or removed candidate and update the allowlist inventory hash"
    ]


def test_inventory_digest_ignores_unrelated_line_movement(tmp_path: Path):
    root, tests = _write_test(
        tmp_path,
        """
import inspect

def test_source_contract():
    source = inspect.getsource(test_source_contract)
    assert "source_contract" in source
""",
    )
    before = textshape_assertion_guard.find_textshape_assertions(tests, repo_root=root)

    (tests / "test_fixture.py").write_text(
        """
import inspect

def test_source_contract():
    source = inspect.getsource(test_source_contract)
    unrelated_setup = "moves the assertion without changing it"
    assert "source_contract" in source
""",
        encoding="utf-8",
    )
    after = textshape_assertion_guard.find_textshape_assertions(tests, repo_root=root)

    assert before[0].line != after[0].line
    assert textshape_assertion_guard.assertion_inventory_sha256(before) == textshape_assertion_guard.assertion_inventory_sha256(after)


def test_real_repository_guard_is_silent_and_passes(capsys):
    assert textshape_assertion_guard.main(["--root", str(REPO_ROOT)]) == 0
    assert capsys.readouterr().out == ""
