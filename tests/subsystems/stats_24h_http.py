# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared 24-hour stats HTTP semantics and latency measurement engine."""

from __future__ import annotations

import json
import threading
import time
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request
from urllib.request import urlopen

import pytest

from tests.browser_helpers.browser_layout import start_browser_server
from tests.browser_helpers.browser_layout import stop_browser_server
from tests.gate_harness import run_fixture_cleanup_phases
from tests.helpers.gate_stats import NOW
from tests.helpers.gate_stats import _seed_realistic_stats
from tests.helpers.gate_stats import record_current_database_migration
from tools.mockers.transcript import MockTranscriptSpec
from tools.mockers.transcript import generate_mock_transcripts
from yomux_lib.stats_current import pricing
from yomux_lib.stats_current import resolution as stats_resolution
from yomux_lib.stats_current import storage
from yomux_lib.stats_current.transcripts import StatsCurrentTranscriptUsageScanner
from yomux_lib.stats_current.usage import usage_atom_from_source


# Cold 24h run: snapshot 11.0 ms (496,037 B), the other HTTP routes 0.6-2.5
# ms. Fifty milliseconds is a 4.5x margin over the slowest measured route.
MAX_24H_ENDPOINT_MS = 50.0
EXPECTED_EXPLICIT_RESOLUTIONS = {
    300: (1, 10),
    900: (10, 60),
    1800: (10, 60),
    3600: (60, 300),
    7200: (60, 300),
    14400: (60, 300),
    28800: (60, 300),
    57600: (300,),
    86400: (300,),
}
TRANSCRIPT_CORPUS_SPECS = (
    ("quiet", MockTranscriptSpec("stats-24h-quiet", usage_records=288, span_seconds=86_399, start_timestamp=int(NOW))),
    ("early", MockTranscriptSpec("stats-24h-early", usage_records=96, span_seconds=6 * 3600, start_timestamp=int(NOW))),
    ("mid", MockTranscriptSpec("stats-24h-mid", usage_records=144, span_seconds=4 * 3600, start_timestamp=int(NOW + 8 * 3600), unknown_model=True)),
    ("late", MockTranscriptSpec("stats-24h-late", usage_records=120, span_seconds=6 * 3600, start_timestamp=int(NOW + 16 * 3600))),
)


def assert_query_policy() -> None:
    assert stats_resolution.explicit_resolutions(86_400) == (300,)
    assert stats_resolution.auto_resolution(86_400) == 300
    assert 86_400 // 300 == 288


def assert_bunched_transcript_window_rejected(tmp_path: Path) -> None:
    corpus = generate_mock_transcripts(
        tmp_path / "bunched",
        MockTranscriptSpec("stats-24h-bunched", usage_records=288, span_seconds=3600, start_timestamp=int(NOW)),
    )
    with pytest.raises(AssertionError, match="1700003600"):
        _assert_transcript_corpus_window("quiet", corpus, int(NOW), int(NOW + 86_399))








def _series_values(snapshot: dict[str, object], name: str) -> list[int]:
    buckets = snapshot["buckets"]
    assert isinstance(buckets, list)
    return [int(bucket["series"][name]["value"]) for bucket in buckets if name in bucket["series"]]


def _bucket_series_values(snapshot: dict[str, object], name: str) -> dict[int, int]:
    buckets = snapshot["buckets"]
    assert isinstance(buckets, list)
    return {
        int(bucket["start"]): int(bucket["series"].get(name, {"value": 0})["value"])
        for bucket in buckets
    }


def _assert_resolution_aggregation(
    responses: dict[tuple[int, int], dict[str, object]],
    range_seconds: int,
    resolutions: tuple[int, ...],
) -> None:
    finest_resolution = resolutions[0]
    finest = responses[range_seconds, finest_resolution]
    finest_values = _bucket_series_values(finest, "usage_tokens")
    finest_total = sum(finest_values.values())
    finest_cost = int(finest["cost_report"]["total_micro_usd"])
    for resolution_value in resolutions:
        response = responses[range_seconds, resolution_value]
        values = _bucket_series_values(response, "usage_tokens")
        assert sum(values.values()) == finest_total
        assert int(response["cost_report"]["total_micro_usd"]) == finest_cost
        compared_bucket_count = 0
        for bucket_start, bucket_value in values.items():
            fine_starts = range(bucket_start, bucket_start + resolution_value, finest_resolution)
            if not all(fine_start in finest_values for fine_start in fine_starts):
                continue
            assert bucket_value == sum(finest_values[fine_start] for fine_start in fine_starts)
            compared_bucket_count += 1
        assert compared_bucket_count


def _generate_24h_transcript_corpora(root: Path) -> dict[str, object]:
    return {name: generate_mock_transcripts(root / name, spec) for name, spec in TRANSCRIPT_CORPUS_SPECS}


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


def _scan_transcript_atoms(corpora: dict[str, object]) -> tuple[object, tuple[storage.UsageAtom, ...]]:
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=2_000)
    scan = scanner.scan([row for corpus in corpora.values() for row in corpus.scanner_rows])
    scanner.commit(scan.receipt_id)
    return scan, tuple(
        usage_atom_from_source({**vars(item.atom), "tmux_key": item.tmux_key, "agent_kind": item.agent_kind})
        for item in scan.items
    )


def _assert_complete_bucket_window(
    buckets: list[object], window_start: int, window_end: int, resolution_seconds: int,
) -> None:
    assert [bucket["start"] for bucket in buckets] == list(range(window_start, window_end, resolution_seconds))


def _assert_24h_endpoint_latency(endpoint: str, elapsed_ms: float) -> None:
    assert elapsed_ms < MAX_24H_ENDPOINT_MS, (
        f"24h API latency guard: {endpoint} took {elapsed_ms:.1f} ms; limit is {MAX_24H_ENDPOINT_MS:.1f} ms"
    )



def read_stream_frame_header(stream) -> bytes:
    """Consume one SSE frame's header lines and return them, tolerating a leading `id:`.

    The stats stream carries a monotonic emit id on EVERY frame -- `server.py:868`
    `sse_id_line`, supplied at `server.py:2496`, `:2500`, `:2504`, `:2519` -- and the writer
    emits it BEFORE the event name (`server.py:929`, `:946`). A reader that takes one line and
    looks for the event name therefore reads the id line and fails on a perfectly healthy
    stream. Observed directly on this endpoint: status 200, then

        b'id: 2129409927\n'
        b'event: ack\n'
        b'data: {"cache_generation": 1700086400000, "chunk_count": 1, ...}\n'

    Returning the CONSUMED bytes rather than only the event line keeps the caller's
    `len(...)` first-frame byte measurement meaningful: it is the frame header actually
    received, which is what the latency sample is describing.

    Streams that pass no id are unaffected, because `sse_id_line` returns `b""` for an empty
    id -- see `test_only_the_stats_stream_emits_an_sse_id_line`, which is the negative search
    proving no other single-line reader in the suite can be reached by this change.
    """

    consumed = stream.readline()
    if consumed.startswith(b"id:"):
        consumed += stream.readline()
    return consumed

def _http_json(base_url: str, path: str, *, payload: object | None = None) -> tuple[int, object, int, float]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{base_url}{path}",
        body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read()
            status = response.status
    except HTTPError as error:
        raw = error.read()
        status = error.code
    elapsed_ms = (time.perf_counter() - started) * 1000
    try:
        decoded: object = json.loads(raw)
    except json.JSONDecodeError:
        decoded = raw.decode("utf-8")
    return status, decoded, len(raw), elapsed_ms
