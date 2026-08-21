# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Derived lifecycle and adapter coverage for the current stats stack."""

from __future__ import annotations

from pathlib import Path

from tests.cross_layer_matrix import build_observed_cost_report
from tests.cross_layer_matrix import classify_stats_snapshot_use
from tests.cross_layer_matrix import discover_javascript_endpoint_uses
from tests.cross_layer_matrix import discover_queued_http_producers
from tests.cross_layer_matrix import observed_fixture_atoms
from tests.cross_layer_matrix import observed_pricing_rows
from tests.cross_layer_matrix import render_cost_model_table
from tests.terminal_state_guard import TERMINAL_STATE_CONTRACTS


REPO_ROOT = Path(__file__).resolve().parents[1]
STATS_SNAPSHOT_ENDPOINT = "/api/stats-stream"


def _shipped_javascript_sources() -> dict[Path, str]:
    return {
        path.relative_to(REPO_ROOT): path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "static_src" / "js" / "yolomux").glob("*.js"))
    }


def _production_python_sources() -> dict[Path, str]:
    return {
        path.relative_to(REPO_ROOT): path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "yolomux_lib").rglob("*.py"))
    }


def test_every_stats_snapshot_endpoint_use_routes_through_the_current_owner():
    uses = discover_javascript_endpoint_uses(
        _shipped_javascript_sources(),
        STATS_SNAPSHOT_ENDPOINT,
    )
    classifications = [classify_stats_snapshot_use(use) for use in uses]

    assert uses, "the shipped current stats client must own the snapshot-and-live endpoint"
    assert classifications.count("current-owner") == 1
    assert [
        f"{use.path}:{use.line}:{use.function}"
        for use, classification in zip(uses, classifications, strict=True)
        if classification == "bypass"
    ] == []


def test_stats_snapshot_owner_guard_rejects_a_new_direct_caller():
    sources = _shipped_javascript_sources()
    sources[Path("synthetic/direct_stats_caller.js")] = """
async function refreshStatsDirectly() {
  return fetch('/api/stats-stream?range_seconds=300');
}
"""

    bypasses = [
        use
        for use in discover_javascript_endpoint_uses(sources, STATS_SNAPSHOT_ENDPOINT)
        if classify_stats_snapshot_use(use) == "bypass"
    ]

    assert [(use.path, use.function) for use in bypasses] == [
        (Path("synthetic/direct_stats_caller.js"), "refreshStatsDirectly")
    ]


def test_stats_snapshot_owner_guard_rejects_an_arrow_caller_between_named_functions():
    sources = {
        Path("static_src/js/yolomux/84_stats_current.js"): """
function fetchSnapshot() {
  return fetch('/api/stats-stream?owner=1');
}
const directArrow = () => fetch('/api/stats-stream?bypass=1');
function closeStream() {}
"""
    }

    uses = discover_javascript_endpoint_uses(sources, STATS_SNAPSHOT_ENDPOINT)

    assert [classify_stats_snapshot_use(use) for use in uses] == [
        "current-owner",
        "bypass",
    ]


def test_every_observed_model_and_pricing_dimension_reaches_report_and_renderer(tmp_path):
    atoms = observed_fixture_atoms()
    rows = observed_pricing_rows(atoms)
    report = build_observed_cost_report(atoms, tmp_path)
    rendered = render_cost_model_table(report)
    report_models = {
        (row["provider"], row["model"]): row
        for row in report["models"]
    }

    assert rows, "the real transcript fixture corpus must produce pricing rows"
    assert report["unpriced"] == {"atoms": 0, "tokens": 0}
    for row in rows:
        model = report_models[(row.provider, row.model)]
        assert model["dimensions"][row.dimension]["tokens"] >= row.tokens
        assert model["dimensions"][row.dimension]["micro_usd"] > 0
        assert row.model in rendered


def test_every_queued_http_producer_has_a_registered_terminal_proof():
    discovered = {
        (producer.path, producer.function)
        for producer in discover_queued_http_producers(_production_python_sources())
    }
    registered_rows = [
        (contract.owner_path, contract.queued_producer_function)
        for contract in TERMINAL_STATE_CONTRACTS
        if contract.queued_producer_function is not None
    ]

    assert discovered
    assert len(registered_rows) == len(set(registered_rows))
    assert discovered == set(registered_rows)


def test_queued_http_producer_discovery_rejects_an_unregistered_acknowledgement():
    sources = _production_python_sources()
    sources[Path("yolomux_lib/synthetic_queue.py")] = """
from http import HTTPStatus

def accept_without_terminal_proof():
    return {"status": "QUEUED"}, HTTPStatus.ACCEPTED
"""
    registered = {
        (contract.owner_path, contract.queued_producer_function)
        for contract in TERMINAL_STATE_CONTRACTS
        if contract.queued_producer_function is not None
    }
    unregistered = {
        (producer.path, producer.function)
        for producer in discover_queued_http_producers(sources)
    } - registered

    assert unregistered == {
        (Path("yolomux_lib/synthetic_queue.py"), "accept_without_terminal_proof")
    }
