# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Contracts for fixture-owned mock transcript generation and YO!stats delivery."""

from __future__ import annotations

import json

from tools.mockers.transcript import MOCK_TRANSCRIPTS_ENV
from tools.mockers.transcript import MockTranscriptSpec
from tools.mockers.transcript import generate_mock_transcripts
from tools.mockers.transcript import generate_mock_transcripts_from_env


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
