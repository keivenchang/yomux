# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import io

import pytest

from yomux_lib import session_files
from yomux_lib.observability import transcripts
from yomux_lib.stats_current.transcripts import StatsCurrentTranscriptUsageScanner


@pytest.mark.parametrize("block_type", [[], {}])
def test_transcript_activity_skips_unknown_block_types(block_type):
    assert not transcripts.transcript_content_has_activity([{"type": block_type}], "user")


@pytest.mark.parametrize("captured", [False, True])
def test_unfinished_last_jsonl_record_waits_for_terminator(tmp_path, captured):
    path = tmp_path / "history.jsonl"
    first, second = b'{"id":1}\n', b'{"id":2}'
    path.write_bytes(first + second)
    options = {"max_bytes": path.stat().st_size} if captured else {}
    assert list(session_files.transcript_json_records(path, **options)) == [{"id": 1}]
    with path.open("ab") as handle:
        handle.write(b"\n")
    assert list(session_files.transcript_json_records(path)) == [{"id": 1}, {"id": 2}]
    if captured:
        assert list(session_files.transcript_json_records(path, **options)) == [{"id": 1}]


@pytest.mark.parametrize("reader", ["records", "shared", "append", "stats"])
def test_transcript_readers_reject_oversized_jsonl_record(tmp_path, reader):
    path = tmp_path / "history.jsonl"
    prefix, suffix = b'{"text":"', b'"}\n'
    size = session_files.MAX_TRANSCRIPT_RECORD_BYTES + 1
    path.write_bytes(prefix + b'x' * (size - len(prefix) - len(suffix)) + suffix)
    with pytest.raises(ValueError, match="transcript record exceeds"):
        if reader == "records":
            list(session_files.transcript_json_records(path))
        elif reader == "shared":
            session_files.SharedTranscriptReader._read_complete_records(path, 0)
        elif reader == "append":
            session_files.scan_transcript_append(path, 0, lambda line: None, {})
        else:
            StatsCurrentTranscriptUsageScanner().scan([{"kind": "claude", "transcript": str(path)}])


@pytest.mark.parametrize("size", [8 * 1024 * 1024 - 1, 8 * 1024 * 1024])
def test_transcript_line_bound_preserves_supported_boundary(size):
    line = b'x' * (size - 1) + b'\n'
    assert session_files.read_transcript_line(io.BytesIO(line)) == line
