# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Transcript corpus window guard shared by the stats range tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers.gate_stats import NOW
from tools.mockers.transcript import MockTranscriptSpec
from tools.mockers.transcript import generate_mock_transcripts


def assert_bunched_transcript_window_rejected(tmp_path: Path) -> None:
    corpus = generate_mock_transcripts(
        tmp_path / "bunched",
        MockTranscriptSpec("stats-24h-bunched", usage_records=288, span_seconds=3600, start_timestamp=int(NOW)),
    )
    with pytest.raises(AssertionError, match="1700003600"):
        _assert_transcript_corpus_window("quiet", corpus, int(NOW), int(NOW + 86_399))


def _transcript_usage_timestamps(corpus: object) -> list[int]:
    timestamps = []
    for path in (corpus.claude_path, corpus.codex_path):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("type") == "assistant" and "usage" in record.get("message", {}):
                timestamps.append(int(record["timestamp"]))
            elif "total_token_usage" in record.get("payload", {}).get("info", {}):
                timestamps.append(int(record["timestamp"]))
    return timestamps


def _assert_transcript_corpus_window(name: str, corpus: object, start: int, end: int) -> None:
    timestamps = _transcript_usage_timestamps(corpus)
    assert timestamps, name
    assert min(timestamps) == start, (name, min(timestamps), start)
    assert max(timestamps) == end, (name, max(timestamps), end)
