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
from yomux_lib.stats_current import materializer
from yomux_lib.stats_current import storage
from yomux_lib.stats_current.transcripts import StatsCurrentTranscriptUsageScanner
from yomux_lib.stats_current.usage import usage_atom_from_source




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




def test_realistic_volume_and_span_are_explicit(tmp_path):
    corpus = generate_mock_transcripts(
        tmp_path / "realistic",
        MockTranscriptSpec(seed="realistic-8929", usage_records=8_929, span_seconds=86_400),
    )

    assert corpus.usage_records == 8_929
    first_timestamp = json.loads(corpus.claude_path.read_text(encoding="utf-8").splitlines()[0])["timestamp"]
    last_timestamp = json.loads(corpus.codex_path.read_text(encoding="utf-8").splitlines()[-1])["timestamp"]
    assert last_timestamp - first_timestamp == 86_400




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
