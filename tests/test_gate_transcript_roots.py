# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared read-only transcript-root contracts for unreliable remote filesystems."""

from __future__ import annotations

import errno
import importlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tools.mockers.transcript import MockTranscriptSpec
from tools.mockers.transcript import append_partial_record
from tools.mockers.transcript import append_record
from tools.mockers.transcript import claude_usage
from tools.mockers.transcript import codex_usage
from tools.mockers.transcript import finish_partial_record
from tools.mockers.transcript import generate_mock_transcripts
from tools.mockers.transcript import replace_records
from yolomux_lib.infra.filesystem_preflight import FilesystemClassification
from yolomux_lib.tmux import sessions
from yolomux_lib.workspace import session_files


TRANSCRIPT_ROOTS_MODULE = "yolomux_lib.workspace.session_files"


@pytest.fixture(autouse=True)
def _fixture_owned_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(session_files.common, "STATE_DIR", tmp_path / "state")
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    sessions._TRANSCRIPT_DIR_CATALOG.clear()
    yield
    session_files._TRANSCRIPT_SCAN_CACHE.clear()
    sessions._TRANSCRIPT_DIR_CATALOG.clear()


def _tree_snapshot(root: Path) -> dict[str, tuple[Any, ...]]:
    snapshot: dict[str, tuple[Any, ...]] = {}
    for path in [root, *sorted(root.rglob("*"))]:
        relative = "." if path == root else path.relative_to(root).as_posix()
        mode = path.stat().st_mode & 0o7777
        snapshot[relative] = ("dir", mode) if path.is_dir() else ("file", mode, path.read_bytes())
    return snapshot


def _codex_append_record(corpus: Any, timestamp: int) -> dict[str, Any]:
    return codex_usage(
        timestamp,
        corpus.codex_input_total + 101,
        corpus.codex_cached_total + 17,
        corpus.codex_output_total + 23,
    )


class _AppendAfterFirstReadHandle:
    def __init__(self, handle: Any, path: Path, record: dict[str, Any]) -> None:
        self.handle = handle
        self.path = path
        self.record = record
        self.appended = False

    def __enter__(self) -> _AppendAfterFirstReadHandle:
        self.handle.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        return self.handle.__exit__(*args)

    def readline(self, *args: Any) -> bytes:
        line = self.handle.readline(*args)
        if line and not self.appended:
            append_record(self.path, self.record)
            self.appended = True
        return line


class _AppendDuringReadPath:
    def __init__(self, path: Path, record: dict[str, Any]) -> None:
        self.path = path
        self.record = record

    def open(self, *args: Any, **kwargs: Any) -> _AppendAfterFirstReadHandle:
        return _AppendAfterFirstReadHandle(self.path.open(*args, **kwargs), self.path, self.record)


def test_partial_final_jsonl_record_preserves_complete_prefix(tmp_path: Path) -> None:
    corpus = generate_mock_transcripts(
        tmp_path / "shared-transcripts",
        MockTranscriptSpec(seed="gate-partial", usage_records=4),
    )
    before = list(session_files.transcript_json_records(corpus.codex_path))
    partial_record = _codex_append_record(corpus, corpus.next_timestamp)
    remaining = append_partial_record(corpus.codex_path, partial_record)

    during_partial = list(session_files.transcript_json_records(corpus.codex_path))

    assert during_partial == before
    assert partial_record not in during_partial
    finish_partial_record(corpus.codex_path, remaining)
    after_completion = list(session_files.transcript_json_records(corpus.codex_path))
    assert after_completion == [*before, partial_record]


def test_append_during_read_has_no_duplicate_or_dropped_records(tmp_path: Path) -> None:
    corpus = generate_mock_transcripts(
        tmp_path / "shared-transcripts",
        MockTranscriptSpec(seed="gate-append-during-read", usage_records=4),
    )
    before = list(session_files.transcript_json_records(corpus.codex_path))
    appended_record = _codex_append_record(corpus, corpus.next_timestamp)
    growing_path = _AppendDuringReadPath(corpus.codex_path, appended_record)

    observed = list(session_files.transcript_json_records(growing_path))

    assert observed == [*before, appended_record]
    assert sum(record == appended_record for record in observed) == 1


def test_replaced_transcript_inode_resets_incremental_reader(tmp_path: Path) -> None:
    corpus = generate_mock_transcripts(
        tmp_path / "shared-transcripts",
        MockTranscriptSpec(seed="gate-replacement", usage_records=4),
    )
    first = session_files.scan_claude_transcript_details(corpus.claude_path)
    replacement_record = claude_usage(
        corpus.next_timestamp,
        "replacement-message",
        "claude-sonnet-synthetic",
        101,
        37,
    )

    before_identity, after_identity = replace_records(corpus.claude_path, [replacement_record])
    replaced = session_files.scan_claude_transcript_details(corpus.claude_path)

    assert before_identity != after_identity
    assert first["usage"]["generated_tokens"] != 37
    assert replaced["usage"]["generated_tokens"] == 37


@pytest.mark.parametrize(
    ("error_number", "reason_code"),
    (
        (errno.ENOENT, "not_found"),
        (getattr(errno, "ESTALE", 116), "stale_handle"),
        (errno.EIO, "io_error"),
    ),
)
def test_remote_read_failure_preserves_last_known_good_with_typed_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error_number: int,
    reason_code: str,
) -> None:
    corpus = generate_mock_transcripts(
        tmp_path / "shared-transcripts",
        MockTranscriptSpec(seed=f"gate-unavailable-{reason_code}", usage_records=4),
    )
    first = session_files.scan_codex_transcript_details(corpus.codex_path)
    real_open = Path.open
    failures: list[int] = []

    def failed_remote_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == corpus.codex_path:
            failures.append(error_number)
            raise OSError(error_number, os.strerror(error_number))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failed_remote_open)
    unavailable = session_files.scan_codex_transcript_details(corpus.codex_path)

    assert failures
    assert unavailable["changes"] == first["changes"]
    assert unavailable["usage"] == first["usage"]
    assert unavailable["read_status"] == {
        "available": False,
        "deleted": False,
        "reason_code": reason_code,
    }


def test_genuine_transcript_deletion_is_distinct_from_unavailable_read(tmp_path: Path) -> None:
    corpus = generate_mock_transcripts(
        tmp_path / "shared-transcripts",
        MockTranscriptSpec(seed="gate-genuine-deletion", usage_records=4),
    )
    first = session_files.scan_codex_transcript_details(corpus.codex_path)
    assert first["usage"]["generated_tokens"] > 0

    corpus.codex_path.unlink()
    deleted = session_files.scan_codex_transcript_details(corpus.codex_path)

    assert deleted["changes"] == {}
    assert deleted["usage"] == {"generated_tokens": 0.0, "generated_tokens_by_model": {}}
    assert deleted["read_status"] == {
        "available": False,
        "deleted": True,
        "reason_code": "deleted",
    }


def test_full_scan_never_mutates_shared_transcript_root(tmp_path: Path) -> None:
    transcript_root = tmp_path / "shared-transcripts"
    corpus = generate_mock_transcripts(
        transcript_root,
        MockTranscriptSpec(seed="gate-read-only-root", usage_records=6),
    )
    before = _tree_snapshot(transcript_root)

    assert list(session_files.transcript_json_records(corpus.claude_path))
    assert list(session_files.transcript_json_records(corpus.codex_path))
    assert session_files.scan_claude_transcript_details(corpus.claude_path)["usage"]["generated_tokens"] > 0
    assert session_files.scan_codex_transcript_details(corpus.codex_path)["usage"]["generated_tokens"] > 0
    assert corpus.codex_path in sessions.recent_codex_transcript_candidates(transcript_root / "codex" / "sessions")
    assert corpus.claude_path in sessions.recent_claude_transcript_candidates(corpus.claude_path.parent)

    after = _tree_snapshot(transcript_root)
    assert after == before
    forbidden = {
        path.relative_to(transcript_root).as_posix()
        for path in transcript_root.rglob("*")
        if path.name.endswith((".lock", ".pyc", ".repair", ".index"))
        or path.name in {"__pycache__", "index", "locks"}
    }
    assert forbidden == set()


def test_remote_root_poll_discovers_append_without_native_event(tmp_path: Path) -> None:
    module = importlib.import_module(TRANSCRIPT_ROOTS_MODULE)
    transcript_root = tmp_path / "shared-transcripts"
    corpus = generate_mock_transcripts(
        transcript_root,
        MockTranscriptSpec(seed="gate-remote-poll", usage_records=4),
    )
    clock = [100.0]
    root = module.SharedTranscriptRoot(
        shared_root_id="team-transcripts",
        mount_path=transcript_root,
        source_host_id="fixture-source-host",
        source_hostname="lin1",
    )
    address = root.transcript(corpus.codex_path.relative_to(transcript_root).as_posix())
    reader = module.SharedTranscriptReader(
        roots=[root],
        state_dir=tmp_path / "state",
        poll_interval_seconds=2.0,
        clock=lambda: clock[0],
        filesystem_classifier=lambda path: FilesystemClassification(path, "nfs4", True, True),
    )
    first = reader.read_jsonl(address)
    appended_record = _codex_append_record(corpus, corpus.next_timestamp)
    append_record(corpus.codex_path, appended_record)

    clock[0] += 2.1
    refreshed = reader.read_jsonl(address)

    assert list(refreshed.records) == [*first.records, appended_record]
    assert refreshed.source_host_id == "fixture-source-host"
    assert refreshed.source_hostname == "lin1"


def test_local_transcript_root_waits_for_native_invalidation_instead_of_polling(tmp_path: Path) -> None:
    module = importlib.import_module(TRANSCRIPT_ROOTS_MODULE)
    transcript_root = tmp_path / "local-transcripts"
    corpus = generate_mock_transcripts(
        transcript_root,
        MockTranscriptSpec(seed="gate-local-native-watch", usage_records=4),
    )
    clock = [100.0]
    root = module.SharedTranscriptRoot(
        shared_root_id="local-transcripts",
        mount_path=transcript_root,
        source_host_id="fixture-local-host",
        source_hostname="lin1",
    )
    address = root.transcript(corpus.codex_path.relative_to(transcript_root).as_posix())
    reader = module.SharedTranscriptReader(
        roots=[root],
        state_dir=tmp_path / "state",
        poll_interval_seconds=2.0,
        clock=lambda: clock[0],
        filesystem_classifier=lambda path: FilesystemClassification(path, "ext4", False, True),
    )
    first = reader.read_jsonl(address)
    appended_record = _codex_append_record(corpus, corpus.next_timestamp)
    append_record(corpus.codex_path, appended_record)

    clock[0] += 100.0
    assert reader.read_jsonl(address) == first
    reader.invalidate(address)
    refreshed = reader.read_jsonl(address)
    assert list(refreshed.records) == [*first.records, appended_record]


def test_logical_transcript_identity_deduplicates_mount_aliases_without_merging_roots(tmp_path: Path) -> None:
    module = importlib.import_module(TRANSCRIPT_ROOTS_MODULE)
    relative_path = "codex/sessions/2026/01/01/rollout-fixture.jsonl"
    root_a = module.SharedTranscriptRoot(
        shared_root_id="team-transcripts",
        mount_path=tmp_path / "lin1-mount",
        source_host_id="fixture-host-a",
        source_hostname="lin1",
    )
    root_b = module.SharedTranscriptRoot(
        shared_root_id="team-transcripts",
        mount_path=tmp_path / "different" / "lin2-mount",
        source_host_id="fixture-host-b",
        source_hostname="lin2",
    )
    alias_a = root_a.transcript(relative_path)
    alias_b = root_b.transcript(relative_path)

    assert alias_a.absolute_path != alias_b.absolute_path
    assert alias_a.identity_key == alias_b.identity_key
    assert alias_a.source_host_id != alias_b.source_host_id
    assert alias_a.source_hostname != alias_b.source_hostname
    assert str(alias_a.absolute_path) not in alias_a.identity_key
    assert str(alias_b.absolute_path) not in alias_b.identity_key

    coincident_mount = tmp_path / "same-absolute-path"
    first_file = module.SharedTranscriptRoot(
        shared_root_id="first-logical-root",
        mount_path=coincident_mount,
        source_host_id="fixture-host-a",
        source_hostname="lin1",
    ).transcript(relative_path)
    second_file = module.SharedTranscriptRoot(
        shared_root_id="second-logical-root",
        mount_path=coincident_mount,
        source_host_id="fixture-host-b",
        source_hostname="lin2",
    ).transcript(relative_path)
    assert first_file.absolute_path == second_file.absolute_path
    assert first_file.identity_key != second_file.identity_key
