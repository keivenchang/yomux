# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import json
import math

import pytest

from yomux_lib.workspace import session_files


@pytest.mark.parametrize("record", [
    [{"type": "assistant", "usage": {}}],
    {"type": "assistant", "message": {"usage": {}, "content": 1}},
    {"type": "assistant", "message": {"usage": {}, "content": [{"type": "tool_use", "name": []}]}},
])
def test_claude_change_scanner_skips_malformed_record_shapes(record):
    state = session_files.new_claude_transcript_scan_state()
    session_files.update_claude_transcript_scan_state(state, json.dumps(record))
    assert state["raw_changes"] == {}
    session_files.update_claude_transcript_scan_state(state, json.dumps({
        "type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/valid.py"}},
        ]},
    }))
    assert state["raw_changes"] == {"/valid.py": {"M"}}


@pytest.mark.parametrize("value", [math.inf, math.nan, 10**1000], ids=["inf", "nan", "overflow"])
def test_transcript_timestamp_rejects_unrepresentable_numbers(value):
    assert session_files.transcript_record_timestamp({"timestamp": value}) is None


@pytest.mark.parametrize("value", [10**1000, True], ids=["overflow", "boolean"])
def test_usage_counter_skips_invalid_values_and_retains_reported_zero(value):
    assert session_files.usage_number({"input_tokens": value}, "input_tokens") is None
    assert session_files.usage_number({"input_tokens": 0}, "input_tokens") == 0


@pytest.mark.parametrize("payload", [
    {"offset": math.inf},
    {"size": math.inf},
    {"usage_parser_state": {"kind": "claude", "sequence": math.inf}},
    {"usage_parser_state": {"kind": "claude", "sequence": "invalid"}},
    {"usage_parser_state": {"kind": "claude", "messages": [{"id": "message", "occurrences": math.inf}]}},
])
def test_invalid_durable_scan_state_restarts_instead_of_crashing(payload):
    assert session_files.transcript_scan_state_from_payload(
        ("stats-current-claude",), payload,
        lambda: session_files.new_stats_current_transcript_scan_state("claude"),
    ) is None


@pytest.mark.parametrize("contents", [b'\xff', b'{"identity":["stats-current-claude"],"state":{"offset":Infinity,"size":1}}'])
def test_cursor_pruner_skips_malformed_progress_records(tmp_path, contents):
    path = tmp_path / "cursor.json"
    path.write_bytes(contents)
    assert not session_files._is_incomplete_stats_current_cursor(path)
