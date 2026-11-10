# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import json
import math

import pytest

from yomux_lib.stats_current import collectors, families, host_collectors, materializer, opencode, process_memory, protocol, storage, usage


@pytest.mark.parametrize("value", [10**1000, -(10**1000), math.inf, -math.inf, math.nan, True, "1"], ids=["huge", "negative_huge", "inf", "negative_inf", "nan", "boolean", "text"])
@pytest.mark.parametrize("validate,error_type", [
    (lambda value: families.validate_payload("cpu", {"process_percent": value, "system_percent": 0}), families.FamilyValidationError),
    (lambda value: protocol._number(value, "value"), protocol.ProtocolValidationError),
    (lambda value: materializer._number({"value": value}, "value"), materializer.MaterializationError),
    (lambda value: materializer._validate_generation_inputs(1, 1, value, 1), materializer.MaterializationError),
    (lambda value: storage._validate_timestamp(value, "value"), storage.StorageValidationError),
    (lambda value: collectors._number(value, "value"), ValueError),
    (lambda value: usage._number(value, "value"), usage.UsageValidationError),
])
def test_numeric_admission_keeps_typed_rejections(validate, error_type, value):
    with pytest.raises(error_type):
        validate(value)


@pytest.mark.parametrize("aggregate", [process_memory.aggregate_process_cpu_by_binary, process_memory.aggregate_process_memory_by_binary])
def test_process_census_skips_unrepresentable_samples(aggregate):
    assert aggregate([("node", 10**1000), ("node", True), ("python", 5)]) == {"python": 5}


@pytest.mark.parametrize("value", [0, 1, 2**64, 1.25, math.nextafter(math.inf, 0)])
def test_representable_numbers_keep_values(value):
    assert protocol._number(value, "value") == value
    assert usage._number(value, "value") == float(value)
    assert storage._validate_timestamp(value, "value") == float(value)
    assert materializer._number({"value": value}, "value") == float(value)


@pytest.mark.parametrize("fields,expected", [
    ("100 0 50 850 0 0 0 0 20 10", (1000, 150)),
    ("100 0 50 850", (1000, 150)),
    ("100 0 50", None),
])
def test_host_cpu_census_counts_guest_once(monkeypatch, fields, expected):
    monkeypatch.setattr(host_collectors.Path, "read_text", lambda self, **kwargs: "cpu " + fields)
    assert host_collectors._linux_system_times() == expected


@pytest.mark.parametrize("field", ["family", "source_id", "epoch"])
@pytest.mark.parametrize("value", ["\ud800", "a\x00b", " "])
def test_no_data_reuses_strict_wire_identity(field, value):
    span = {"family": "cpu", "source_id": "host", "epoch": "epoch", "reason": "outage", "start": 0, "end": 1, "source_cadence_seconds": 1}
    span[field] = value
    with pytest.raises(protocol.ProtocolValidationError):
        protocol._no_data([span])


def test_no_data_preserves_valid_unicode_identity():
    span = {"family": "cpu", "source_id": "host-\u03bb", "epoch": "epoch", "reason": "outage", "start": 0, "end": 1, "source_cadence_seconds": 1}
    assert protocol._no_data([span]) == [span]


@pytest.mark.parametrize("second_reason,expected_count", [("driver offline", 1), ("permission denied", 2)])
def test_adjacent_no_data_preserves_failure_reasons(second_reason, expected_count):
    gaps = [materializer.NoData("gpu", "host", "epoch", 0, 1, 1, "driver offline")]
    materializer._append_gap(gaps, materializer.NoData("gpu", "host", "epoch", 1, 2, 1, second_reason))
    assert len(gaps) == expected_count
    assert gaps[0].reason == "driver offline"
    assert gaps[-1].reason == second_reason


def test_ring_bucket_text_keeps_typed_utf8_rejection():
    bucket = storage.RingBucketWrite(10, 0, '{"note":"\ud800"}', True)
    with pytest.raises(storage.StorageValidationError):
        storage._ring_bucket_values(bucket)


@pytest.mark.parametrize("filename,key,reader", [
    (storage.PRUNE_STATE_FILENAME, "last_pruned_at", storage.Store.last_pruned_at),
    (storage.VACUUM_BASELINE_FILENAME, "last_vacuumed_at", storage.Store.reclaimable_ratio_at_last_vacuum),
    (storage.VACUUM_BASELINE_FILENAME, "reclaimable_ratio", storage.Store.reclaimable_ratio_at_last_vacuum),
])
def test_maintenance_metadata_overflow_keeps_unknown_fallback(tmp_path, filename, key, reader):
    value = {"last_vacuumed_at": 0, "reclaimable_ratio": 0, key: 10**1000}
    (tmp_path / filename).write_text(json.dumps(value), encoding="utf-8")
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        assert reader(store) == 0


def test_coverage_epoch_continues_after_retention(tmp_path):
    with storage.Store.open(tmp_path / storage.DATABASE_FILENAME) as store:
        end = storage.RETENTION_SECONDS + 200
        store.append_coverage_epoch(storage.CoverageEpoch("cpu", "host", "epoch", 0, end, 1, 1))
        for cutoff in (100, 110, 120):
            result = store.prune(now=storage.RETENTION_SECONDS + cutoff)
            assert result.coverage_epochs_clipped == 0
            end += 1
            assert store.append_coverage_epoch(storage.CoverageEpoch("cpu", "host", "epoch", 0, end, 1, 1))
        epoch, = store.read_snapshot().coverage_epochs
        assert epoch.started_at == 0
        assert epoch.ended_at == end


@pytest.mark.parametrize("value", [2**53 + 1, 2**64 - 1])
def test_opencode_token_integers_preserve_exact_source_values(value):
    assert opencode._token_number(value) == value


def test_opencode_invalid_numeric_sources_keep_typed_outcomes():
    with pytest.raises(ValueError, match="token-count"):
        opencode._token_number(10**1000)
    assert opencode._cost_number(10**1000) is None
    assert opencode._milliseconds(10**1000) is None


@pytest.mark.parametrize("value", [10**1000, 1e308, math.nan, math.inf, True, -1])
@pytest.mark.parametrize("reader", [opencode.read_tool_inputs, opencode._read_tool_inputs_uncached])
def test_opencode_tool_time_admission_precedes_cache_conversion(reader, value):
    result = reader(session_id="fixture-session", after=value)
    assert result == opencode.OpenCodeUnavailable("invalid-tool-start-time")


@pytest.mark.parametrize("value", [10**1000, 1e308, math.nan, math.inf, True])
def test_opencode_sqlite_clock_bounds_are_typed(value):
    with pytest.raises(ValueError, match="invalid-time"):
        opencode._sqlite_milliseconds(value)


@pytest.mark.parametrize("value,expected", [(0, 0), (1.25, 1250), (-1.25, -1250)])
def test_opencode_sqlite_clock_preserves_valid_milliseconds(value, expected):
    assert opencode._sqlite_milliseconds(value) == expected
