# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Versioned refactor characterization manifest and exact comparator behavior."""

from __future__ import annotations

import json

from tools import refactor_characterization as characterization


def test_checked_in_manifest_reproduces_from_frozen_git_blobs():
    expected = json.loads(characterization.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    actual = characterization.manifest_snapshot(
        characterization.capture(characterization.git_source(characterization.BASELINE_COMMIT))
    )

    assert actual == expected


def test_manifest_covers_every_required_characterization_domain():
    expected = json.loads(characterization.DEFAULT_MANIFEST.read_text(encoding="utf-8"))

    assert set(expected["inventory_fingerprints"]) == {
        "public_facade_methods", "routes", "daemon_actions", "wire_storage_identities",
        "frontend_globals_state_domains", "dom_contracts", "timers_listeners_streams",
        "fixture_lifecycles", "gate_lanes", "external_test_node_ids",
    }
    assert all(item["count"] > 0 and len(item["sha256"]) == 64 for item in expected["inventory_fingerprints"].values())
    assert len(expected["nondeterministic_exclusions"]) == 5
    assert all(set(item) == {"field", "scope", "reason"} for item in expected["nondeterministic_exclusions"])


def test_comparator_reports_exact_category_additions_and_removals():
    baseline = characterization.capture(characterization.git_source(characterization.BASELINE_COMMIT))
    texts = {path: characterization.git_source(characterization.BASELINE_COMMIT).text(path) for path in characterization.ALL_INPUTS}
    texts["yolomux_lib/http_routes.py"] = texts["yolomux_lib/http_routes.py"].replace('Route("GET",', 'Route("PATCH",', 1)
    changed = characterization.capture(characterization.Source("synthetic", lambda path: texts.get(path, "")))

    result = characterization.compare(characterization.manifest_snapshot(baseline), changed)

    assert result["equal"] is False
    assert set(result["differences"]) == {"routes"}
    assert len(result["differences"]["routes"]["removed"]) == 1
    assert len(result["differences"]["routes"]["added"]) == 1
    assert "GET" in result["differences"]["routes"]["removed"][0]
    assert "PATCH" in result["differences"]["routes"]["added"][0]


def test_behavior_fixtures_pin_normalized_outputs_and_cleanup_order():
    fixtures = characterization.behavioral_fixtures()

    assert fixtures["compact_sorted_json"] == '{"payload":{"a":2,"z":1},"signature":"abc","stored_at":100.0,"version":2}'
    assert fixtures["record_manifest_cleanup_order"] == ["record.write", "manifest.write", "post_write.account", "post_write.prune"]
    assert fixtures["fixture_lifecycle_order"][-5:] == ["browser.quit", "server.stop", "thread.join", "app.stop", "temp_root.remove"]
