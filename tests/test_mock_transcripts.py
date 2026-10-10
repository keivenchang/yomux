# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Contracts for fixture-owned mock transcript generation and YO!stats delivery."""

from __future__ import annotations

import json

import pytest

from tools.mockers.transcript import MOCK_TRANSCRIPTS_ENV
from tools.mockers.transcript import MockTranscriptSpec
from tools.mockers.transcript import generate_mock_transcripts
from tools.mockers.transcript import generate_mock_transcripts_from_env
from yomux_lib import session_files
from yomux_lib.stats_current import materializer
from yomux_lib.stats_current import storage
from yomux_lib.stats_current.transcripts import StatsCurrentTranscriptUsageScanner
from yomux_lib.stats_current.usage import usage_atom_from_source


@pytest.fixture(autouse=True)
def _isolated_transcript_scan_store(tmp_path, monkeypatch, request):
    # Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
    if request.node.get_closest_marker("deprecated_python_server") is None:
        yield
        return
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "scanner-state")
    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()
        session_files._TRANSCRIPT_SCAN_CACHE_STATE_DIR = None
    yield
    with session_files._TRANSCRIPT_SCAN_CACHE_GUARD:
        session_files._TRANSCRIPT_SCAN_CACHE.clear()
        session_files._TRANSCRIPT_SCAN_CACHE_STATE_DIR = None


def _file_bytes(corpus):
    return corpus.claude_path.read_bytes(), corpus.codex_path.read_bytes()


def _scan_all(corpus):
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=20_000)
    result = scanner.scan(corpus.scanner_rows)
    scanner.commit(result.receipt_id)
    return scanner, result


def _generated_yostats_usage_value(tmp_path, seed: str, instance: str) -> tuple[int, int]:
    corpus = generate_mock_transcripts(
        tmp_path / f"corpus-{instance}",
        MockTranscriptSpec(seed=seed, usage_records=18, span_seconds=90),
    )
    _scanner, result = _scan_all(corpus)
    usage_atoms = tuple(
        usage_atom_from_source({**vars(item.atom), "tmux_key": item.tmux_key})
        for item in result.items
    )
    database = tmp_path / f"stats-{instance}" / storage.DATABASE_FILENAME
    with storage.Store.open(database) as store:
        appended = store.append_batch(usage_atoms=usage_atoms)
        assert appended.usage_atoms_accepted == len(usage_atoms)
        snapshot = store.read_snapshot()
    generation = materializer.build_generation(
        snapshot,
        source_generation=1,
        cache_generation=1,
        generated_at=corpus.next_timestamp,
        observed_until=corpus.next_timestamp,
    )
    painted_value = sum(
        int(series.value)
        for bucket in generation.layer(1).buckets
        for series in bucket.series
        if series.name == "usage_tokens"
    )
    return painted_value, corpus.expected_input_tokens + corpus.expected_output_tokens


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_seed_is_byte_reproducible_and_changes_generated_yostats_value(tmp_path):
    spec = MockTranscriptSpec(seed="repeatable-seed", usage_records=24, span_seconds=7_200)
    first = generate_mock_transcripts(tmp_path / "first", spec)
    second = generate_mock_transcripts(tmp_path / "second", spec)
    changed = generate_mock_transcripts(
        tmp_path / "changed",
        MockTranscriptSpec(seed="different-seed", usage_records=24, span_seconds=7_200),
    )

    assert _file_bytes(first) == _file_bytes(second)
    assert _file_bytes(first) != _file_bytes(changed)
    first_value, first_expected = _generated_yostats_usage_value(tmp_path, "repeatable-value", "first-value")
    replayed_value, replayed_expected = _generated_yostats_usage_value(tmp_path, "repeatable-value", "replayed-value")
    changed_value, changed_expected = _generated_yostats_usage_value(tmp_path, "different-value", "changed-value")
    assert (first_value, first_expected) == (replayed_value, replayed_expected)
    assert first_value == first_expected
    assert changed_value == changed_expected
    assert changed_value != first_value


def test_realistic_volume_and_span_are_explicit(tmp_path):
    corpus = generate_mock_transcripts(
        tmp_path / "realistic",
        MockTranscriptSpec(seed="realistic-8929", usage_records=8_929, span_seconds=86_400),
    )

    assert corpus.usage_records == 8_929
    first_timestamp = json.loads(corpus.claude_path.read_text(encoding="utf-8").splitlines()[0])["timestamp"]
    last_timestamp = json.loads(corpus.codex_path.read_text(encoding="utf-8").splitlines()[-1])["timestamp"]
    assert last_timestamp - first_timestamp == 86_400


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_append_exercises_offset_observed_size_and_prefix_digest(tmp_path):
    corpus = generate_mock_transcripts(
        tmp_path / "append",
        MockTranscriptSpec(seed="append-seed", usage_records=12, span_seconds=60),
    )
    scanner, initial = _scan_all(corpus)
    source = str(corpus.codex_path.resolve())
    before = scanner._files[source]
    before_state = (before.offset, before.observed_size, before.prefix_digest)
    expected_before = corpus.expected_output_tokens

    appended_bytes = corpus.append("codex", 3)
    delta = scanner.scan(corpus.scanner_rows)
    scanner.commit(delta.receipt_id)
    after = scanner._files[source]

    assert appended_bytes
    assert delta.resets == 0
    assert sum(item.atom.quantity for item in delta.items if item.atom.direction == "output") == (
        corpus.expected_output_tokens - expected_before
    )
    assert after.offset > before_state[0]
    assert after.observed_size > before_state[1]
    assert after.prefix_digest == before_state[2]
    assert scanner.status()["committed_appended_bytes"] >= len(appended_bytes)


def test_test_env_routes_only_to_generated_namespace(tmp_path, monkeypatch):
    mock_root = tmp_path / "env-owned-mocks"
    monkeypatch.setenv(MOCK_TRANSCRIPTS_ENV, json.dumps({
        "seed": "env-routed",
        "usage_records": 10,
        "span_seconds": 300,
    }))

    corpus = generate_mock_transcripts_from_env(mock_root)

    assert corpus.root == mock_root
    assert corpus.claude_path.is_relative_to(mock_root)
    assert corpus.codex_path.is_relative_to(mock_root)
    assert all(row["transcript"].startswith(str(mock_root)) for row in corpus.scanner_rows)


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_awkward_cases_include_unknown_model_companion_values_and_repair_backlog(tmp_path):
    corpus = generate_mock_transcripts(
        tmp_path / "awkward",
        MockTranscriptSpec(
            seed="awkward-cases",
            usage_records=14,
            span_seconds=600,
            unknown_model=True,
            repair_backlog_records=240,
        ),
    )
    _scanner, result = _scan_all(corpus)

    assert {item.atom.model for item in result.items} == {
        f"synthetic-unpriced-{corpus.claude_path.stem.removeprefix('session-')}",
    }
    assert corpus.unlisted_micro_usd_series["unit"] == "micro_usd"
    assert str(corpus.unlisted_micro_usd_series["name"]).startswith("synthetic_cost_probe_")
    assert corpus.monotonic_timestamp > 1_000_000_000_000
    assert corpus.repair_path is not None
    assert len(corpus.repair_path.read_text(encoding="utf-8").splitlines()) == 241
    assert result.backlog_files >= 1 or result.records_parsed >= 241
