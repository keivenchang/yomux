# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused tests for the small current-only YO!stats protocol."""

import copy

import pytest

from yolomux_lib.stats_current import protocol, resolution as resolution_policy


def request(range_seconds: str = "300", resolution: str = "AUTO", **extra: object) -> protocol.SnapshotRequest:
    return protocol.parse_snapshot_request({"range_seconds": range_seconds, "resolution": resolution, "client_id": "browser-a", **extra})


def bucket(start: int = 0, duration: int = 1, open_: bool = False) -> dict[str, object]:
    observed_at = start + min(0.5, duration / 2)
    return {
        "start": start,
        "duration": duration,
        "series": {"cpu": {
            "value": 0.0,
            "source_count": 1,
            "first_timestamp": observed_at,
            "last_timestamp": observed_at,
        }},
        "source": {"first_timestamp": observed_at, "last_timestamp": observed_at, "count": 1},
        "open": open_,
    }


def no_data(start: int = 10, end: int = 20) -> dict[str, object]:
    return {"family": "gpu", "source_id": "gpu:0", "start": start, "end": end, "epoch": "gpu:3", "reason": "late", "source_cadence_seconds": 10}


def cost_report(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": protocol.COST_REPORT_SCHEMA_VERSION,
        "total_micro_usd": 0,
        "total_api_list_micro_usd": 0,
        "total_tokens": 0,
        "dimensions": {
            dimension: {"tokens": 0, "micro_usd": 0, "api_list_micro_usd": 0}
            for dimension in protocol.COST_REPORT_DIMENSIONS
        },
        "priced": {"atoms": 0, "tokens": 0},
        "unpriced": {"atoms": 0, "tokens": 0},
        "models": [],
        "agents": [],
        "evidence": [],
        "catalog_revision": 0,
        "omissions": {"models": 0, "agents": 0, "evidence": 0},
        "reasoning_available": False,
    }
    value.update(changes)
    return value


def populated_cost_report() -> dict[str, object]:
    dimensions = {
        dimension: {"tokens": 0, "micro_usd": 0, "api_list_micro_usd": 0}
        for dimension in protocol.COST_REPORT_DIMENSIONS
    }
    dimensions["input"] = {
        "tokens": 10, "micro_usd": 20, "api_list_micro_usd": 30,
    }
    attribution = {
        "total_tokens": 10,
        "total_micro_usd": 20,
        "total_api_list_micro_usd": 30,
        "dimensions": copy.deepcopy(dimensions),
        "priced": {"atoms": 1, "tokens": 10},
        "unpriced": {"atoms": 0, "tokens": 0},
    }
    return cost_report(
        total_micro_usd=20,
        total_api_list_micro_usd=30,
        total_tokens=10,
        dimensions=dimensions,
        priced={"atoms": 1, "tokens": 10},
        models=[{
            "key": "a" * 24, "provider": "openai", "model": "gpt", **attribution,
        }],
        agents=[{
            "key": "b" * 24, "source": "codex", "label": "yo8881|0|codex",
            **copy.deepcopy(attribution),
        }],
        evidence=[{
            "key": "c" * 24,
            "provider": "openai",
            "model": "gpt",
            "dimension": "input",
            "direction": "input",
            "modality": "text",
            "cache_role": "none",
            "unit": "tokens",
            "pricing_profile": "default",
            "service_tier": "default",
            "catalog_model": "gpt",
            "rate_usd": "2.00",
            "rate_scale": 1_000_000,
            "effective_from": "2026-07-09T00:00:00Z",
            "source_kind": "seed",
            "source_url": "https://example.com/pricing",
            "catalog_revision": 3,
            "tokens": 10,
            "micro_usd": 20,
            "api_list_micro_usd": 30,
            "priced_atoms": 1,
        }],
        catalog_revision=3,
    )


def snapshot(**changes: object) -> protocol.SnapshotWire:
    value: protocol.SnapshotWire = {"protocol_version": protocol.WIRE_PROTOCOL_VERSION, "range_seconds": 300, "requested_resolution": "AUTO", "resolution_seconds": 1, "window_start": 0, "window_end": 300, "generated_at": 300, "source_generation": 8, "cache_generation": 7, "rightmost_open": False, "buckets": [], "no_data": [], "cost_report": cost_report()}
    value.update(changes)
    return value


def delta(**changes: object) -> protocol.DeltaWire:
    value: protocol.DeltaWire = {"protocol_version": protocol.WIRE_PROTOCOL_VERSION, "range_seconds": 300, "resolution_seconds": 1, "source_generation": 9, "base_cache_generation": 7, "cache_generation": 8, "revision": 41, "buckets": [bucket()], "no_data": [], "tombstones": [], "cost_report": cost_report()}
    value.update(changes)
    return value


@pytest.mark.parametrize(
    ("range_seconds", "selected", "concrete"),
    [(r, selected, resolution_policy.resolve_requested(r, selected)) for r in resolution_policy.RANGE_SECONDS for selected in ("AUTO", *resolution_policy.explicit_resolutions(r))],
)
def test_every_current_request_resolves_exactly(range_seconds: int, selected: int | str, concrete: int):
    parsed = request(str(range_seconds), str(selected))
    assert (parsed.range_seconds, parsed.resolution, parsed.resolution_seconds) == (range_seconds, selected, concrete)


@pytest.mark.parametrize(("range_seconds", "resolution"), [("900", "1"), ("7200", "10"), ("14400", "120"), ("57600", "1"), ("86400", "600")])
def test_bad_pairs_are_unsupported_without_substitution(range_seconds: str, resolution: str):
    with pytest.raises(protocol.UnsupportedRequest) as caught:
        request(range_seconds, resolution)
    response = caught.value.response
    assert response["status"] == "unsupported"
    assert int(resolution) not in response["valid_resolutions"]


@pytest.mark.parametrize("params", [{"range_seconds": "0300"}, {"range_seconds": "300 "}, {"resolution": "01"}, {"resolution": "auto"}, {"client_id": ""}, {"client_id": "a\n"}, {"since_generation": "-1"}])
def test_query_values_are_strict(params: dict[str, object]):
    defaults = {"range_seconds": "300", "resolution": "1", "client_id": "a"}
    with pytest.raises(protocol.UnsupportedRequest):
        protocol.parse_snapshot_request(defaults | params)


@pytest.mark.parametrize("field", [*sorted(protocol.RETIRED_REQUEST_FIELDS), "mystery"])
def test_retired_and_unknown_fields_are_rejected(field: str):
    with pytest.raises(protocol.UnsupportedRequest):
        request(**{field: "1"})


def test_snapshot_exact_shape_and_valid_content():
    value = snapshot(
        buckets=[bucket(299, open_=True)],
        no_data=[{"family": "gpu", "source_id": "gpu:0", "start": 10, "end": 20, "epoch": "gpu:3", "reason": "late", "source_cadence_seconds": 10}],
        rightmost_open=True,
    )
    assert protocol.validate_snapshot(value) is value


def test_cost_report_is_strict_complete_json_safe_and_never_fabricates_reasoning():
    report = populated_cost_report()
    assert protocol.validate_cost_report(report) is report
    assert protocol.validate_snapshot(snapshot(cost_report=report))["cost_report"] is report
    assert protocol.validate_delta(delta(cost_report=report))["cost_report"] is report

    invalid = copy.deepcopy(report)
    invalid["reasoning_available"] = True
    with pytest.raises(protocol.ProtocolValidationError, match="reasoning"):
        protocol.validate_cost_report(invalid)
    invalid = copy.deepcopy(report)
    invalid["dimensions"]["reasoning"] = {
        "tokens": 1, "micro_usd": 0, "api_list_micro_usd": 0,
    }
    with pytest.raises(protocol.ProtocolValidationError, match="unknown"):
        protocol.validate_cost_report(invalid)
    invalid = copy.deepcopy(report)
    invalid["total_tokens"] = 11
    with pytest.raises(protocol.ProtocolValidationError, match="totals"):
        protocol.validate_cost_report(invalid)
    invalid = copy.deepcopy(report)
    invalid["evidence"][0]["source_url"] = "javascript:alert(1)"
    with pytest.raises(protocol.ProtocolValidationError, match="HTTP"):
        protocol.validate_cost_report(invalid)
    invalid = copy.deepcopy(report)
    invalid["extra"] = 1
    with pytest.raises(protocol.ProtocolValidationError, match="unknown"):
        protocol.validate_cost_report(invalid)
    invalid = copy.deepcopy(report)
    second = copy.deepcopy(invalid["models"][0])
    second["key"] = "0" * 24
    invalid["models"].append(second)
    with pytest.raises(protocol.ProtocolValidationError, match="rank order"):
        protocol.validate_cost_report(invalid)
    invalid = copy.deepcopy(report)
    template = invalid["models"][0]
    invalid["models"] = [
        {**copy.deepcopy(template), "key": f"{index:024x}"}
        for index in range(protocol.MAX_COST_DETAIL_MODELS + 1)
    ]
    with pytest.raises(protocol.ProtocolValidationError, match="at most"):
        protocol.validate_cost_report(invalid)
    invalid = copy.deepcopy(report)
    invalid["total_tokens"] = protocol.MAX_SAFE_INTEGER + 1
    with pytest.raises(protocol.ProtocolValidationError, match="JSON"):
        protocol.validate_cost_report(invalid)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"legacy_samples": []}, "unknown"),
        ({"range_seconds": 900, "requested_resolution": 1, "resolution_seconds": 1, "window_end": 900}, "unsupported"),
        ({"resolution_seconds": 10}, "concrete"),
        ({"buckets": [bucket(0, 10)]}, "duration/alignment"),
        ({"range_seconds": 900, "requested_resolution": 10, "resolution_seconds": 10, "window_end": 900, "buckets": [bucket(1, 10)]}, "duration/alignment"),
        ({"buckets": [bucket(2), bucket(1)]}, "increasing"),
        ({"buckets": [bucket(300)]}, "outside"),
        ({"buckets": [bucket()] * 601}, "at most"),
        ({"window_end": 299}, "window length"),
        ({"cache_generation": -1}, "cache_generation"),
        ({"buckets": [bucket(299, open_=True)]}, "rightmost_open"),
        ({"buckets": [bucket(298, open_=True)], "rightmost_open": True}, "window_end"),
    ],
)
def test_snapshot_rejects_wrong_shape_key_bucket_window_and_generation(changes: dict[str, object], message: str):
    with pytest.raises(protocol.ProtocolValidationError, match=message):
        protocol.validate_snapshot(snapshot(**changes))


def test_snapshot_rejects_invalid_source_and_no_data():
    bad_source = bucket()
    bad_source["source"] = {"first_timestamp": None, "last_timestamp": None, "count": 1}
    with pytest.raises(protocol.ProtocolValidationError, match="source timestamps"):
        protocol.validate_snapshot(snapshot(buckets=[bad_source]))
    span = {"family": "gpu", "source_id": "gpu:0", "start": 299, "end": 301, "epoch": "gpu:1", "reason": "late", "source_cadence_seconds": 10}
    with pytest.raises(protocol.ProtocolValidationError, match="outside"):
        protocol.validate_snapshot(snapshot(no_data=[span]))
    for invalid_epoch in (1, ""):
        span = {"family": "gpu", "source_id": "gpu:0", "start": 10, "end": 20, "epoch": invalid_epoch, "reason": "late", "source_cadence_seconds": 10}
        with pytest.raises(protocol.ProtocolValidationError, match="epoch"):
            protocol.validate_snapshot(snapshot(no_data=[span]))
    span = {"family": "gpu", "source_id": "x" * 257, "start": 10, "end": 20, "epoch": "gpu:1", "reason": "late", "source_cadence_seconds": 10}
    with pytest.raises(protocol.ProtocolValidationError, match="source_id is too long"):
        protocol.validate_snapshot(snapshot(no_data=[span]))


@pytest.mark.parametrize(
    ("series", "message"),
    [
        ({"cpu": 1}, "object"),
        ({"cpu": {"value": 1, "source_count": 1, "first_timestamp": 0.5}}, "fields"),
        ({"cpu": {"value": 1, "source_count": 0, "first_timestamp": 0.5, "last_timestamp": 0.5}}, "source_count"),
        ({"cpu": {"value": 1, "source_count": 1, "first_timestamp": 0.5, "last_timestamp": 1}}, "outside"),
    ],
)
def test_snapshot_requires_one_exact_plot_ready_series_shape(series, message):
    value = bucket()
    value["series"] = series
    with pytest.raises(protocol.ProtocolValidationError, match=message):
        protocol.validate_snapshot(snapshot(buckets=[value]))


def test_dense_empty_bucket_requires_zero_null_source_facts():
    empty = bucket()
    empty["series"] = {}
    empty["source"] = {"first_timestamp": None, "last_timestamp": None, "count": 0}
    assert protocol.validate_snapshot(snapshot(buckets=[empty]))["buckets"] == [empty]
    empty["source"] = {"first_timestamp": 1, "last_timestamp": 1, "count": 1}
    with pytest.raises(protocol.ProtocolValidationError, match="empty series"):
        protocol.validate_snapshot(snapshot(buckets=[empty]))


def test_no_data_overlap_is_scoped_by_family_and_source():
    spans = [
        {"family": "gpu", "source_id": "gpu:0", "start": 10.5, "end": 20.5, "epoch": "gpu:0:1", "reason": "late", "source_cadence_seconds": 0.5},
        {"family": "gpu", "source_id": "gpu:1", "start": 10.5, "end": 20.5, "epoch": "gpu:1:1", "reason": "late", "source_cadence_seconds": 10},
    ]
    assert protocol.validate_snapshot(snapshot(no_data=spans))["no_data"] == spans


def test_delta_shape_uniformity_and_generation_are_strict():
    active = snapshot()
    update = delta()
    assert protocol.validate_delta(update) is update
    protocol.validate_delta_for_snapshot(active, update)
    with pytest.raises(protocol.ProtocolValidationError, match="replacement"):
        protocol.validate_delta(delta(buckets=[]))
    with pytest.raises(protocol.ProtocolValidationError, match="duration/alignment"):
        protocol.validate_delta(delta(buckets=[bucket(0, 10)]))
    with pytest.raises(protocol.ProtocolValidationError, match="unsupported"):
        protocol.validate_delta(delta(range_seconds=900, resolution_seconds=1))
    with pytest.raises(protocol.ProtocolValidationError, match="advance"):
        protocol.validate_delta(delta(base_cache_generation=8, cache_generation=8))
    with pytest.raises(protocol.ProtocolValidationError, match="base"):
        protocol.validate_delta_for_snapshot(active, delta(base_cache_generation=6))
    with pytest.raises(protocol.ProtocolValidationError, match="regressed"):
        protocol.validate_delta_for_snapshot(active, delta(source_generation=7, cache_generation=8))


def test_delta_identities_cover_bucket_no_data_and_exact_tombstones():
    span = no_data()
    assert protocol.bucket_identity(bucket()) == ("bucket", 0, 1)
    assert protocol.no_data_identity(span) == ("no_data", "gpu", "gpu:0", "gpu:3", 10, 20)
    changed_bucket = bucket()
    changed_bucket["series"] = {"cpu": {"average": 99}}
    changed_span = dict(span, reason="known_outage", source_cadence_seconds=60)
    assert protocol.bucket_identity(changed_bucket) == protocol.bucket_identity(bucket())
    assert protocol.no_data_identity(changed_span) == protocol.no_data_identity(span)

    update = delta(
        buckets=[],
        no_data=[span],
        tombstones=[
            {"kind": "bucket", "start": 0, "duration": 1},
            {"kind": "no_data", "family": "gpu", "source_id": "gpu:1", "epoch": "gpu:4", "start": 20, "end": 30},
        ],
    )
    assert protocol.validate_delta(update) is update
    assert protocol.tombstone_identity(update["tombstones"][0], 1) == ("bucket", 0, 1)
    assert protocol.tombstone_identity(update["tombstones"][1], 1) == ("no_data", "gpu", "gpu:1", "gpu:4", 20, 30)


def test_delta_rejects_duplicate_unsorted_or_replaced_tombstone_identity():
    duplicate = {"kind": "bucket", "start": 0, "duration": 1}
    with pytest.raises(protocol.ProtocolValidationError, match="both replaced and removed"):
        protocol.validate_delta(delta(tombstones=[duplicate]))
    with pytest.raises(protocol.ProtocolValidationError, match="ordered"):
        protocol.validate_delta(delta(buckets=[], tombstones=[
            {"kind": "no_data", "family": "gpu", "source_id": "gpu:1", "epoch": "gpu:4", "start": 20, "end": 30},
            {"kind": "bucket", "start": 0, "duration": 1},
        ]))
    with pytest.raises(protocol.ProtocolValidationError, match="duration/alignment"):
        protocol.validate_delta(delta(buckets=[], tombstones=[{"kind": "bucket", "start": 0, "duration": 10}]))
    with pytest.raises(protocol.ProtocolValidationError, match="kind"):
        protocol.validate_delta(delta(buckets=[], tombstones=[{"kind": "series", "start": 0, "duration": 1}]))
    with pytest.raises(protocol.ProtocolValidationError, match="both replaced and removed"):
        protocol.validate_delta(delta(
            buckets=[],
            no_data=[no_data()],
            tombstones=[{"kind": "no_data", "family": "gpu", "source_id": "gpu:0", "epoch": "gpu:3", "start": 10, "end": 20}],
        ))
    tombstone_only = delta(
        buckets=[], no_data=[],
        tombstones=[{"kind": "bucket", "start": 0, "duration": 1}],
    )
    assert protocol.validate_delta(tombstone_only) is tombstone_only


def test_delta_chain_requires_exact_base_and_consecutive_revision():
    first = delta(base_cache_generation=7, cache_generation=11, revision=41)
    second = delta(base_cache_generation=11, cache_generation=20, revision=42)
    protocol.validate_delta_after_delta(first, second)
    with pytest.raises(protocol.ProtocolValidationError, match="base"):
        protocol.validate_delta_after_delta(first, delta(base_cache_generation=10, cache_generation=20, revision=42))
    with pytest.raises(protocol.ProtocolValidationError, match="revision"):
        protocol.validate_delta_after_delta(first, delta(base_cache_generation=11, cache_generation=20, revision=43))
    with pytest.raises(protocol.ProtocolValidationError, match="regressed"):
        protocol.validate_delta_after_delta(first, delta(base_cache_generation=11, cache_generation=20, revision=42, source_generation=8))


@pytest.mark.parametrize("resolution", [2, 5, 30, 120, 600])
def test_delta_rejects_every_non_current_resolution(resolution: int):
    with pytest.raises(protocol.ProtocolValidationError, match="unsupported"):
        protocol.validate_delta(delta(resolution_seconds=resolution))


@pytest.mark.parametrize(
    ("range_seconds", "resolution_seconds"),
    [
        (range_seconds, resolution_seconds)
        for range_seconds in resolution_policy.RANGE_SECONDS
        for resolution_seconds in resolution_policy.explicit_resolutions(range_seconds)
    ],
)
def test_delta_request_and_wire_accept_every_exact_numeric_matrix_key(
    range_seconds: int, resolution_seconds: int
):
    request_value = protocol.parse_delta_request({
        "range_seconds": str(range_seconds),
        "resolution_seconds": str(resolution_seconds),
        "client_id": "browser-a",
        "after_cache_generation": "7",
        "after_revision": "41",
    })
    assert (request_value.range_seconds, request_value.resolution_seconds) == (
        range_seconds, resolution_seconds
    )
    value = delta(
        range_seconds=range_seconds,
        resolution_seconds=resolution_seconds,
        buckets=[bucket(0, resolution_seconds)],
    )
    assert protocol.validate_delta(value) is value


def test_delta_request_is_exact_and_requires_a_generation_revision_cursor():
    parsed = protocol.parse_delta_request({
        "range_seconds": "300", "resolution_seconds": "1", "client_id": "browser-a",
        "after_cache_generation": "7", "after_revision": "41",
    })
    assert parsed == protocol.DeltaRequest(300, 1, "browser-a", 7, 41)
    for changes in (
        {"resolution_seconds": "AUTO"},
        {"resolution_seconds": "600"},
        {"after_cache_generation": "-1"},
        {"after_revision": "01"},
        {"history": "1"},
    ):
        with pytest.raises(protocol.UnsupportedRequest):
            protocol.parse_delta_request({
                "range_seconds": "300", "resolution_seconds": "1", "client_id": "browser-a",
                "after_cache_generation": "7", "after_revision": "41", **changes,
            })


@pytest.mark.parametrize(
    "changes",
    (
        {"source_generation": protocol.MAX_SAFE_INTEGER + 1},
        {"base_cache_generation": protocol.MAX_SAFE_INTEGER + 1},
        {"cache_generation": protocol.MAX_SAFE_INTEGER + 1},
        {"revision": protocol.MAX_SAFE_INTEGER + 1},
        {"revision": 0},
    ),
)
def test_delta_generations_and_revision_are_exact_json_integers(changes: dict[str, object]):
    with pytest.raises(protocol.ProtocolValidationError, match="revision|generation|JSON"):
        protocol.validate_delta(delta(**changes))


def test_snapshot_and_request_generations_reject_non_exact_json_integers():
    with pytest.raises(protocol.ProtocolValidationError, match="JSON"):
        protocol.validate_snapshot(snapshot(cache_generation=protocol.MAX_SAFE_INTEGER + 1))
    with pytest.raises(protocol.UnsupportedRequest, match="JSON"):
        request(since_generation=str(protocol.MAX_SAFE_INTEGER + 1))


def test_snapshot_must_match_request_and_advance_generation():
    active = request(since_generation="7")
    protocol.validate_snapshot_for_request(active, snapshot(cache_generation=8))
    with pytest.raises(protocol.ProtocolValidationError, match="stale"):
        protocol.validate_snapshot_for_request(active, snapshot(cache_generation=7))
    with pytest.raises(protocol.ProtocolValidationError, match="key"):
        protocol.validate_snapshot_for_request(active, snapshot(range_seconds=900, requested_resolution="AUTO", resolution_seconds=10, window_end=900))


def test_pending_unsupported_and_upgrade_wire_shapes():
    assert protocol.pending_response(request(), 3) == {"status": "pending", "protocol_version": protocol.WIRE_PROTOCOL_VERSION, "range_seconds": 300, "requested_resolution": "AUTO", "resolution_seconds": 1, "retry_after_seconds": 3, "reason": "materialization is not ready"}
    assert protocol.unsupported_response("bad", 300)["valid_resolutions"] == ["AUTO", 1, 10]
    assert protocol.upgrade_required_response(2, 4, "0.7.0") == {"status": "upgrade_required", "protocol_version": protocol.WIRE_PROTOCOL_VERSION, "required_protocol_version": 2, "required_schema_generation": 4, "required_build": "0.7.0", "reason": "client or writer is too old"}
    with pytest.raises(protocol.ProtocolValidationError, match="bounded"):
        protocol.pending_response(request(), 61)


@pytest.mark.parametrize(("resolution_seconds", "cadence"), [(1, 1), (10, 10), (60, 60), (300, 60)])
def test_cadence_uses_only_echoed_concrete_resolution(resolution_seconds: int, cadence: int):
    assert protocol.live_cadence_seconds(resolution_seconds) == cadence


@pytest.mark.parametrize("resolution_seconds", [2, 5, 30, 120, 600])
def test_cadence_rejects_retired_values(resolution_seconds: int):
    with pytest.raises(ValueError, match="unsupported concrete resolution"):
        protocol.live_cadence_seconds(resolution_seconds)
