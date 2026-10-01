# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Deterministic, fixture-owned Claude and Codex transcript generator."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any


MOCK_TRANSCRIPTS_ENV = "YOLOMUX_TEST_MOCK_TRANSCRIPTS"
DEFAULT_START_TIMESTAMP = 1_700_000_000


def record_line(record: dict[str, Any]) -> str:
    return json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"


def write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(record_line(record) for record in records),
        encoding="utf-8",
    )


def append_record(path: Path, record: dict[str, Any]) -> str:
    line = record_line(record)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    return line


def append_partial_record(path: Path, record: dict[str, Any], split_at: int | None = None) -> bytes:
    """Append an incomplete JSONL record and return the bytes still held by the writer."""

    encoded = record_line(record).encode("utf-8")
    boundary = len(encoded) // 2 if split_at is None else int(split_at)
    if boundary <= 0 or boundary >= len(encoded):
        raise ValueError("partial transcript split must leave bytes on both sides")
    with path.open("ab") as handle:
        handle.write(encoded[:boundary])
    return encoded[boundary:]


def finish_partial_record(path: Path, remaining: bytes) -> None:
    if not remaining:
        raise ValueError("partial transcript remainder must not be empty")
    with path.open("ab") as handle:
        handle.write(remaining)


def replace_records(path: Path, records: list[dict[str, Any]]) -> tuple[tuple[int, int], tuple[int, int]]:
    """Replace a transcript atomically and return its old and new device/inode identities."""

    before = path.stat()
    replacement = path.with_name(f".{path.name}.replacement")
    if replacement.exists():
        raise FileExistsError(replacement)
    write_records(replacement, records)
    os.replace(replacement, path)
    after = path.stat()
    return (int(before.st_dev), int(before.st_ino)), (int(after.st_dev), int(after.st_ino))


def codex_meta(thread_id: str, parent_thread_id: str = "", **context: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": thread_id}
    payload.update(context)
    if parent_thread_id:
        payload["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent_thread_id}}}
    return {"type": "session_meta", "timestamp": 1, "payload": payload}


def codex_usage(timestamp: int | float, input_tokens: int, cached_tokens: int, output_tokens: int) -> dict[str, Any]:
    return {
        "type": "event_msg",
        "timestamp": timestamp,
        "payload": {
            "info": {
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_tokens,
                    "output_tokens": output_tokens,
                },
            },
        },
    }


def claude_usage(
    timestamp: int | float,
    message_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    session_id: str = "synthetic-claude-session",
) -> dict[str, Any]:
    return {
        "cwd": "/synthetic/workspace",
        "gitBranch": "synthetic/test-branch",
        "sessionId": session_id,
        "timestamp": timestamp,
        "type": "assistant",
        "uuid": message_id,
        "version": "synthetic-1",
        "message": {
            "id": message_id,
            "model": model,
            "role": "assistant",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "service_tier": "default",
            },
        },
    }


@dataclass(frozen=True, slots=True)
class MockTranscriptSpec:
    seed: str
    usage_records: int = 20
    span_seconds: int = 3_600
    start_timestamp: int = DEFAULT_START_TIMESTAMP
    unknown_model: bool = False
    repair_backlog_records: int = 0

    def __post_init__(self) -> None:
        if not self.seed:
            raise ValueError("mock transcript seed must not be empty")
        if self.usage_records < 2:
            raise ValueError("usage_records must be at least two so both providers are represented")
        if self.span_seconds < 1:
            raise ValueError("span_seconds must be positive")
        if self.start_timestamp < 0:
            raise ValueError("start_timestamp must be non-negative")
        if self.repair_backlog_records < 0:
            raise ValueError("repair_backlog_records must not be negative")


@dataclass(slots=True)
class MockTranscriptCorpus:
    spec: MockTranscriptSpec
    root: Path
    claude_path: Path
    codex_path: Path
    repair_path: Path | None
    claude_session_id: str
    codex_thread_id: str
    expected_input_tokens: int
    expected_output_tokens: int
    claude_usage_records: int
    codex_usage_records: int
    codex_input_total: int
    codex_cached_total: int
    codex_output_total: int
    next_timestamp: int
    unlisted_micro_usd_series: dict[str, int | str]
    monotonic_timestamp: int

    @property
    def scanner_rows(self) -> list[dict[str, str]]:
        return [
            {"key": "synthetic|0|claude", "kind": "claude", "transcript": str(self.claude_path)},
            {"key": "synthetic|1|codex", "kind": "codex", "transcript": str(self.codex_path)},
        ]

    @property
    def usage_records(self) -> int:
        return self.claude_usage_records + self.codex_usage_records

    def append(self, provider: str, count: int = 1) -> bytes:
        """Append plausible new records without rewriting the existing prefix."""

        if provider not in {"claude", "codex"}:
            raise ValueError("provider must be claude or codex")
        if count < 1:
            raise ValueError("append count must be positive")
        appended = []
        for _unused in range(count):
            timestamp = self.next_timestamp
            self.next_timestamp += 1
            if provider == "claude":
                index = self.claude_usage_records
                input_tokens = _bounded(self.spec.seed, provider, index, "input", 20, 500)
                output_tokens = _bounded(self.spec.seed, provider, index, "output", 2, 100)
                record = claude_usage(
                    timestamp,
                    f"synthetic-claude-message-{index:06d}",
                    _model(self.spec, provider),
                    input_tokens,
                    output_tokens,
                    session_id=self.claude_session_id,
                )
                self.claude_usage_records += 1
            else:
                index = self.codex_usage_records
                input_tokens = _bounded(self.spec.seed, provider, index, "input", 20, 500)
                cached_tokens = _bounded(self.spec.seed, provider, index, "cached", 0, input_tokens)
                output_tokens = _bounded(self.spec.seed, provider, index, "output", 2, 100)
                self.codex_input_total += input_tokens
                self.codex_cached_total += cached_tokens
                self.codex_output_total += output_tokens
                record = codex_usage(
                    timestamp,
                    self.codex_input_total,
                    self.codex_cached_total,
                    self.codex_output_total,
                )
                self.codex_usage_records += 1
            self.expected_input_tokens += input_tokens
            self.expected_output_tokens += output_tokens
            appended.append(append_record(self.claude_path if provider == "claude" else self.codex_path, record))
        return "".join(appended).encode("utf-8")


def _bounded(seed: str, provider: str, index: int, field: str, minimum: int, maximum: int) -> int:
    digest = hashlib.sha256(f"{seed}\0{provider}\0{index}\0{field}".encode("utf-8")).digest()
    return minimum + int.from_bytes(digest[:8], "big") % (maximum - minimum + 1)


def _model(spec: MockTranscriptSpec, provider: str) -> str:
    if spec.unknown_model:
        suffix = hashlib.sha256(spec.seed.encode("utf-8")).hexdigest()[:12]
        return f"synthetic-unpriced-{suffix}"
    return "claude-sonnet-synthetic" if provider == "claude" else "gpt-synthetic"


def generate_mock_transcripts(root: Path, spec: MockTranscriptSpec) -> MockTranscriptCorpus:
    """Generate a new deterministic corpus beneath a caller-owned root."""

    suffix = hashlib.sha256(spec.seed.encode("utf-8")).hexdigest()[:12]
    claude_session_id = f"synthetic-claude-session-{suffix}"
    codex_thread_id = f"synthetic-codex-thread-{suffix}"
    claude_path = root / "claude" / "projects" / "-synthetic-workspace" / f"session-{suffix}.jsonl"
    codex_path = root / "codex" / "sessions" / "2026" / "01" / "01" / f"rollout-{suffix}.jsonl"
    claude_count = spec.usage_records // 2
    codex_count = spec.usage_records - claude_count
    timestamps = [
        spec.start_timestamp + (index * spec.span_seconds // max(1, spec.usage_records - 1))
        for index in range(spec.usage_records)
    ]
    claude_records = []
    codex_records = [
        codex_meta(codex_thread_id, model=_model(spec, "codex"), effort="medium"),
        {"type": "turn_context", "timestamp": timestamps[claude_count], "payload": {"model": _model(spec, "codex")}},
    ]
    expected_input = expected_output = 0
    codex_input = codex_cached = codex_output = 0
    for index in range(claude_count):
        input_tokens = _bounded(spec.seed, "claude", index, "input", 20, 500)
        output_tokens = _bounded(spec.seed, "claude", index, "output", 2, 100)
        expected_input += input_tokens
        expected_output += output_tokens
        claude_records.append(claude_usage(
            timestamps[index], f"synthetic-claude-message-{index:06d}",
            _model(spec, "claude"), input_tokens, output_tokens,
            session_id=claude_session_id,
        ))
    for index in range(codex_count):
        input_tokens = _bounded(spec.seed, "codex", index, "input", 20, 500)
        cached_tokens = _bounded(spec.seed, "codex", index, "cached", 0, input_tokens)
        output_tokens = _bounded(spec.seed, "codex", index, "output", 2, 100)
        codex_input += input_tokens
        codex_cached += cached_tokens
        codex_output += output_tokens
        expected_input += input_tokens
        expected_output += output_tokens
        codex_records.append(codex_usage(
            timestamps[claude_count + index], codex_input, codex_cached, codex_output,
        ))
    write_records(claude_path, claude_records)
    write_records(codex_path, codex_records)

    repair_path = None
    if spec.repair_backlog_records:
        repair_path = codex_path.with_name(f"rollout-repair-{suffix}.jsonl")
        repair_records = [
            codex_meta(
                f"synthetic-repair-thread-{suffix}", codex_thread_id,
                forked_from_id=codex_thread_id, thread_source="subagent",
            ),
            *(
                {
                    "type": "response_item",
                    "timestamp": spec.start_timestamp - spec.repair_backlog_records + index,
                    "payload": {"kind": "synthetic-repair-placeholder", "ordinal": index},
                }
                for index in range(spec.repair_backlog_records)
            ),
        ]
        write_records(repair_path, repair_records)

    companion_suffix = int(suffix[:8], 16)
    return MockTranscriptCorpus(
        spec=spec,
        root=root,
        claude_path=claude_path,
        codex_path=codex_path,
        repair_path=repair_path,
        claude_session_id=claude_session_id,
        codex_thread_id=codex_thread_id,
        expected_input_tokens=expected_input,
        expected_output_tokens=expected_output,
        claude_usage_records=claude_count,
        codex_usage_records=codex_count,
        codex_input_total=codex_input,
        codex_cached_total=codex_cached,
        codex_output_total=codex_output,
        next_timestamp=spec.start_timestamp + spec.span_seconds + 1,
        unlisted_micro_usd_series={
            "name": f"synthetic_cost_probe_{suffix}",
            "unit": "micro_usd",
            "value": 100_000 + companion_suffix % 900_000,
        },
        monotonic_timestamp=10_000_000_000_000 + companion_suffix,
    )


def generate_mock_transcripts_from_env(root: Path) -> MockTranscriptCorpus:
    """Generate from the explicit test-only environment contract."""

    raw = os.environ.get(MOCK_TRANSCRIPTS_ENV, "").strip()
    if not raw:
        raise RuntimeError(f"{MOCK_TRANSCRIPTS_ENV} must be set for mock transcript routing")
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{MOCK_TRANSCRIPTS_ENV} must contain a JSON object") from error
    if not isinstance(values, dict):
        raise ValueError(f"{MOCK_TRANSCRIPTS_ENV} must contain a JSON object")
    return generate_mock_transcripts(root, MockTranscriptSpec(**values))
