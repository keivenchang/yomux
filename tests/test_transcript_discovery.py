# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import json
import os

import pytest

from yomux_lib.tmux import sessions


@pytest.mark.parametrize("payload", [b"null", b"[]", b"1", b'"text"', b"\xff"])
def test_claude_metadata_errors_stay_local_to_the_agent(tmp_path, payload):
    (tmp_path / "123.json").write_bytes(payload)
    pane = sessions.TmuxPaneInfo("fixture", "0", "0", "%1", "%1", "/fixture", "claude", True, True, "", 123)
    process = sessions.ProcessInfo(123, 1, "claude")
    agent = sessions.read_claude_agent("fixture", pane, process, sessions_root=tmp_path, enrich_paths=False)
    assert agent.kind == "claude"
    assert agent.error.startswith("invalid session metadata:")
    assert agent.transcript is None


@pytest.mark.parametrize("prefix", [None, [], 1, "text"])
def test_codex_metadata_reader_skips_non_record_json_before_header(tmp_path, prefix):
    path = tmp_path / "rollout-root.jsonl"
    path.write_text(json.dumps(prefix) + '\n' + json.dumps({"type": "session_meta", "payload": {"id": "root"}}) + '\n')
    assert sessions.codex_transcript_meta(path) == ("root", "")


def test_missing_root_identity_does_not_adopt_unrelated_codex_sessions(tmp_path):
    root = tmp_path / "rollout-root.jsonl"
    other = tmp_path / "rollout-other.jsonl"
    root.write_text("")
    other.write_text(json.dumps({"type": "session_meta", "payload": {"id": "other"}}) + '\n')
    assert sessions.codex_transcript_family_paths(root, candidates=[other]) == [root]


@pytest.mark.parametrize("replacement", [False, True], ids=["initial-header", "atomic-replacement"])
def test_codex_header_cache_cannot_attribute_replaced_or_initializing_files_to_old_cwd(tmp_path, replacement):
    path = tmp_path / "rollout-root.jsonl"
    sessions.clear_transcript_cwd_memos()
    path.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": "/before"}}) + '\n' if replacement else "")
    assert sessions.memoized_codex_transcript_header_cwd(path) == ("/before" if replacement else None)
    target = path.with_suffix(".new") if replacement else path
    target.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": "/after"}}) + '\n')
    if replacement:
        os.replace(target, path)
        assert sessions.find_codex_transcript_in_candidates([path], "/before") is None
    assert sessions.find_codex_transcript_in_candidates([path], "/after") == path
    assert sessions.memoized_codex_transcript_header_cwd(path) == "/after"
