# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Private SQLite fixture tests for the bounded OpenCode YO!stats source."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from yomux_lib.stats_current import opencode


def _database(path: Path, sessions: list[tuple[object, ...]], messages: list[tuple[object, ...]], parts: list[tuple[object, ...]]) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE session (
                id TEXT PRIMARY KEY, directory TEXT NOT NULL, agent TEXT, model TEXT,
                time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, cost REAL NOT NULL DEFAULT 0,
                title TEXT NOT NULL DEFAULT '',
                tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER,
                tokens_cache_read INTEGER, tokens_cache_write INTEGER
            );
            CREATE TABLE message (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, time_created INTEGER NOT NULL,
                time_updated INTEGER NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE part (
                id TEXT PRIMARY KEY, message_id TEXT NOT NULL, session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE session_input (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, prompt TEXT NOT NULL,
                delivery TEXT NOT NULL, admitted_seq INTEGER NOT NULL, promoted_seq INTEGER,
                time_created INTEGER NOT NULL
            );
            CREATE TABLE credential (id TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        connection.executemany(
            "INSERT INTO session (id, directory, agent, model, time_created, time_updated, cost, "
            "tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            sessions,
        )
        connection.executemany("INSERT INTO message VALUES (?, ?, ?, ?, ?)", messages)
        connection.executemany("INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)", parts)


def _session(session_id: str, directory: str, updated: int = 2_000, *, tokens: tuple[int, ...] = (100, 20, 3, 10, 7)) -> tuple[object, ...]:
    return (
        session_id, directory, "build",
        json.dumps({"id": "model-a", "providerID": "provider-a"}), 1_000, updated,
        0.0,
        *tokens,
    )


def _message(
    message_id: str,
    session_id: str,
    *,
    model: str = "model-a",
    finish: str | None = None,
    completed: int | None = None,
    error: object = None,
) -> tuple[object, ...]:
    data: dict[str, object] = {
        "role": "assistant", "modelID": model, "providerID": "provider-a",
        "time": {"created": 1_500},
    }
    if finish is not None:
        data["finish"] = finish
    if completed is not None:
        data["time"] = {"created": 1_500, "completed": completed}
    if error is not None:
        data["error"] = error
    return (
        message_id, session_id, 1_500, 1_500,
        json.dumps(data),
    )


def _part(part_id: str, message_id: str, session_id: str, tokens: dict[str, object] | None) -> tuple[object, ...]:
    data: dict[str, object] = {"type": "step-finish"}
    if tokens is not None:
        data["tokens"] = tokens
    return (part_id, message_id, session_id, 1_600, 1_600, json.dumps(data))


def _tool_part(part_id: str, message_id: str, session_id: str, tool: str, input_data: object, *, status: str = "completed") -> tuple[object, ...]:
    return (
        part_id,
        message_id,
        session_id,
        1_600,
        1_600,
        json.dumps({"type": "tool", "tool": tool, "state": {"status": status, "input": input_data}}),
    )


def _state_database(path: Path, *, parts=(), messages=(), inputs=(), tokens=(0, 0, 0, 0, 0)) -> None:
    _database(path, [_session("ses-a", "/repo/a", tokens=tokens)], list(messages), list(parts))
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO session_input VALUES (?, ?, ?, ?, ?, ?, ?)",
            inputs,
        )


def test_read_tool_inputs_returns_only_completed_tool_inputs(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a")],
        [_message("msg-a", "ses-a")],
        [
            (
                "patch",
                "msg-a",
                "ses-a",
                1_600,
                1_600,
                json.dumps({
                    "type": "tool",
                    "tool": "apply_patch",
                    "state": {
                        "status": "completed",
                        "input": {"patchText": "*** Begin Patch\n*** Update File: app.py\n"},
                        "metadata": {"files": [{"filePath": "/repo/a/app.py", "type": "update", "patch": "large output omitted"}]},
                    },
                }),
            ),
            _tool_part("read", "msg-a", "ses-a", "read", {"filePath": "/repo/a/app.py"}),
            _tool_part("running", "msg-a", "ses-a", "bash", {"command": "git add app.py"}, status="running"),
        ],
    )

    result = opencode.read_tool_inputs(database, session_id="ses-a")

    assert isinstance(result, tuple)
    assert [(item.part_id, item.tool, item.patch_text, item.changed_files) for item in result] == [
        ("patch", "apply_patch", "*** Begin Patch\n*** Update File: app.py\n", (("/repo/a/app.py", "update"),)),
    ]


def test_incremental_usage_reads_newest_step_finish_parts_before_bound(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    session_id = "ses-large"
    parts = []
    messages = []
    monkeypatch.setattr(opencode, "MAX_INCREMENTAL_PARTS", 2)
    count = 2
    for index in range(count):
        message_id = f"msg-{index}"
        part_id = f"part-{index}"
        messages.append(_message(message_id, session_id, completed=2_000 + index))
        parts.append(_part(part_id, message_id, session_id, {"input": index + 1, "output": 2, "reasoning": 0, "cache": {"read": 0, "write": 0}}))
    _database(database, [_session(session_id, "/repo/large", updated=2_000 + count, tokens=(None, None, None, None, None))], messages, parts)

    result = opencode.read_usage(database, session_id=session_id, incremental=True, max_parts=1, known_event_revisions={})

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert result.components
    assert {component.part_id for component in result.components} == {"part-0", "part-1"}


def test_read_tool_inputs_keeps_newest_bounded_slice_and_write_metadata(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a")],
        [_message("msg-a", "ses-a")],
        [
            ("old", "msg-a", "ses-a", 1_000, 1_000, json.dumps({"type": "tool", "tool": "bash", "state": {"status": "completed", "input": {"command": "git add old.py"}}})),
            ("new", "msg-a", "ses-a", 2_000, 2_000, json.dumps({"type": "tool", "tool": "write", "state": {"status": "completed", "input": {"filePath": "/repo/a/new.py"}, "metadata": {"exists": False}}})),
        ],
    )

    result = opencode.read_tool_inputs(database, session_id="ses-a", max_parts=1)

    assert isinstance(result, tuple)
    assert [(item.tool, item.file_path, item.file_exists) for item in result] == [("write", "/repo/a/new.py", False)]


def test_read_tool_inputs_applies_start_time_and_reports_move_paths(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a")],
        [_message("msg-a", "ses-a")],
        [
            ("old", "msg-a", "ses-a", 1_000, 1_000, json.dumps({"type": "tool", "tool": "apply_patch", "state": {"status": "completed", "metadata": {"files": [{"filePath": "/repo/a/old.py", "type": "update"}]}}})),
            ("move", "msg-a", "ses-a", 2_000, 2_000, json.dumps({"type": "tool", "tool": "apply_patch", "state": {"status": "completed", "metadata": {"files": [{"filePath": "/repo/a/old.py", "movePath": "/repo/a/new.py", "type": "move"}]}}})),
        ],
    )

    result = opencode.read_tool_inputs(database, session_id="ses-a", after=1.5)

    assert isinstance(result, tuple)
    assert result[0].changed_files == (("/repo/a/old.py", "delete"), ("/repo/a/new.py", "add"))


def test_read_tool_inputs_bounds_by_updated_time(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a")],
        [_message("msg-a", "ses-a")],
        [
            ("older-created", "msg-a", "ses-a", 1_000, 5_000, json.dumps({"type": "tool", "tool": "write", "state": {"status": "completed", "input": {"filePath": "/repo/a/recent.py"}}})),
            ("newer-created", "msg-a", "ses-a", 4_000, 4_000, json.dumps({"type": "tool", "tool": "write", "state": {"status": "completed", "input": {"filePath": "/repo/a/old.py"}}})),
        ],
    )

    result = opencode.read_tool_inputs(database, session_id="ses-a", max_parts=1)

    assert isinstance(result, tuple)
    assert [item.file_path for item in result] == ["/repo/a/recent.py"]


def test_read_usage_emits_exact_step_finish_dimensions_without_message_double_count(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
            [_session("ses-a", "/repo/a")],
        [_message("msg-a", "ses-a")],
        [_part("part-a", "msg-a", "ses-a", {"input": 100, "output": 20, "reasoning": 3, "cache": {"read": 10, "write": 7}})],
    )

    result = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert [(item.dimension, item.tokens) for item in result.components] == [
        ("input", 100), ("cache_read", 10), ("cache_write", 7), ("output", 20), ("reasoning", 3),
    ]
    assert all(item.provider == "provider-a" and item.model == "model-a" for item in result.components)
    assert all(item.model_evidence == "message.providerID+message.modelID" for item in result.components)
    assert all(item.telemetry_complete is True for item in result.components)
    assert all(item.event_id.startswith("opencode:ses-a:part-a:") for item in result.components)


def test_session_id_for_terminal_title_unquotes_visible_title(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(database, [_session("ses-a", "/repo/a", updated=2_000)], [], [])
    with sqlite3.connect(database) as connection:
        connection.execute('UPDATE session SET title = ? WHERE id = ?', ("build", "ses-a"))

    result = opencode.session_id_for_terminal_title(
        database,
        directory="/repo/a",
        title='OC | "build"',
        now=2.0,
    )

    assert result == "ses-a"


def test_session_id_for_terminal_title_handles_quoted_ellipsis(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(database, [_session("ses-a", "/repo/a", updated=2_000)], [], [])
    with sqlite3.connect(database) as connection:
        connection.execute('UPDATE session SET title = ? WHERE id = ?', ('"build: follow-up work"', "ses-a"))

    result = opencode.session_id_for_terminal_title(
        database,
        directory="/repo/a",
        title='OC | "build: ..."',
        now=2.0,
    )

    assert result == "ses-a"


def test_read_state_reports_running_tool_without_token_atoms(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _state_database(
        database,
        messages=[_message("msg-a", "ses-a")],
        parts=[("tool-a", "msg-a", "ses-a", 1_700, 1_700, json.dumps({
            "type": "tool", "tool": "read", "state": {"status": "running"},
        }))],
    )

    result = opencode.read_state(database, session_id="ses-a", now=2.0)

    assert isinstance(result, opencode.OpenCodeStateSuccess)
    assert result.state == "working"
    assert result.evidence == ("running-tool", "recent-assistant")


def test_read_state_does_not_treat_completed_or_aborted_assistant_as_working(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _state_database(
        database,
        messages=[
            _message("completed", "ses-a", finish="stop", completed=1_800),
            _message("aborted", "ses-a", error={"name": "AbortError"}, completed=1_900),
        ],
    )

    result = opencode.read_state(database, session_id="ses-a", now=2.0)

    assert isinstance(result, opencode.OpenCodeStateSuccess)
    assert result.state == "idle"
    assert result.evidence == ()


def test_read_state_detects_active_assistant_from_unfinished_message_fields(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _state_database(database, messages=[_message("active", "ses-a")])

    result = opencode.read_state(database, session_id="ses-a", now=2.0)

    assert isinstance(result, opencode.OpenCodeStateSuccess)
    assert result.state == "working"
    assert result.evidence == ("recent-assistant",)


def test_read_state_reports_queued_input_as_paused(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _state_database(
        database,
        inputs=[("input-a", "ses-a", "follow up", "queue", 1, None, 1_700)],
    )

    result = opencode.read_state(database, session_id="ses-a", now=2.0)

    assert isinstance(result, opencode.OpenCodeStateSuccess)
    assert result.state == "paused"
    assert result.evidence == ("queued-input",)


def test_read_state_running_tool_wins_over_queued_input(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _state_database(
        database,
        inputs=[("input-a", "ses-a", "follow up", "queue", 1, None, 1_700)],
        parts=[("tool-a", "msg-a", "ses-a", 1_700, 1_700, json.dumps({
            "type": "tool", "tool": "read", "state": {"status": "running"},
        }))],
    )

    result = opencode.read_state(database, session_id="ses-a", now=2.0)

    assert isinstance(result, opencode.OpenCodeStateSuccess)
    assert result.state == "working"
    assert result.evidence == ("queued-input", "running-tool")


def test_read_state_ignores_stale_queued_input(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _state_database(
        database,
        inputs=[("input-a", "ses-a", "old", "queue", 1, None, 1_000)],
    )

    result = opencode.read_state(database, session_id="ses-a", now=400.0)

    assert isinstance(result, opencode.OpenCodeStateSuccess)
    assert result.state == "idle"
    assert result.evidence == ()


def test_read_state_rejects_invalid_recent_queue_lifecycle_fields(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _state_database(
        database,
        inputs=[("input-a", "ses-a", "follow up", "invalid", 1, None, 1_700)],
    )

    result = opencode.read_state(database, session_id="ses-a", now=2.0)

    assert isinstance(result, opencode.OpenCodeStateUnavailable)
    assert result.reason == "invalid-queued-input-delivery"


def test_read_state_does_not_report_promoted_input_as_queued(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _state_database(
        database,
        inputs=[("input-a", "ses-a", "already sent", "queue", 1, 2, 1_700)],
    )

    result = opencode.read_state(database, session_id="ses-a", now=2.0)

    assert isinstance(result, opencode.OpenCodeStateSuccess)
    assert result.state == "idle"
    assert result.evidence == ()


def test_read_state_rejects_promoted_input_with_an_invalid_admission_order(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _state_database(
        database,
        inputs=[("input-a", "ses-a", "already sent", "queue", 3, 2, 1_700)],
    )

    result = opencode.read_state(database, session_id="ses-a", now=2.0)

    assert isinstance(result, opencode.OpenCodeStateUnavailable)
    assert result.reason == "invalid-queued-input-promotion"


def test_read_state_queued_input_does_not_override_latest_completed_turn(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _state_database(
        database,
        messages=[
            _message("old", "ses-a", finish=None),
            _message("new", "ses-a", finish="stop", completed=1_900),
        ],
        inputs=[("input-a", "ses-a", "follow up", "queue", 1, None, 1_700)],
        parts=[],
    )
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE message SET time_updated = ? WHERE id = ?", (1_900, "new"))

    result = opencode.read_state(database, session_id="ses-a", now=2.0)

    assert isinstance(result, opencode.OpenCodeStateSuccess)
    assert result.state == "paused"
    assert result.evidence == ("queued-input",)


def test_read_state_rejects_malformed_promoted_input_metadata(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _state_database(
        database,
        inputs=[("input-a", "ses-a", "already sent", "queue", 1, "bad", 1_700)],
    )

    result = opencode.read_state(database, session_id="ses-a", now=2.0)

    assert isinstance(result, opencode.OpenCodeStateUnavailable)
    assert result.reason == "invalid-queued-input-sequence"


def test_read_state_reports_completed_session_as_idle(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _state_database(
        database,
        messages=[_message("msg-a", "ses-a")],
        parts=[_part("step-a", "msg-a", "ses-a", {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}})],
    )

    result = opencode.read_state(database, session_id="ses-a", now=2.0)

    assert isinstance(result, opencode.OpenCodeStateSuccess)
    assert result.state == "idle"
    assert result.evidence == ()


def test_read_state_requires_real_session_input_schema_and_missing_database_is_unavailable(tmp_path: Path) -> None:
    missing = opencode.read_state(tmp_path / "missing.db", session_id="ses-a")
    assert isinstance(missing, opencode.OpenCodeStateUnavailable)
    assert missing.reason == "database-unavailable"

    database = tmp_path / "schema.db"
    _database(database, [_session("ses-a", "/repo/a", tokens=(0, 0, 0, 0, 0))], [], [])
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE session_input")
    result = opencode.read_state(database, session_id="ses-a")
    assert isinstance(result, opencode.OpenCodeStateSchemaMismatch)
    assert result.reason == "missing-table:session_input"


def test_read_usage_rejects_authoritative_counter_mismatch(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a", tokens=(11, 7, 2, 5, 0))],
        [_message("msg-a", "ses-a")],
        [_part("part-a", "msg-a", "ses-a", {"input": 1, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}})],
    )

    result = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(result, opencode.OpenCodeUnavailable)
    assert result.reason == "cumulative-anchor-mismatch"


def test_read_usage_rejects_complete_history_mismatch(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a", tokens=(11, 7, 2, 5, 0))],
        [_message("msg-a", "ses-a")],
        [_part(
            "part-a", "msg-a", "ses-a",
            {"input": 1, "output": 2, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        )],
    )

    result = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(result, opencode.OpenCodeUnavailable)
    assert result.reason == "cumulative-anchor-mismatch"


def test_read_usage_rejects_truncated_or_mismatched_step_finish_anchor(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a", tokens=(11, 7, 2, 5, 0))],
        [_message("msg-a", "ses-a"), _message("msg-b", "ses-a")],
        [_part("part-a", "msg-a", "ses-a", {"input": 1, "output": 1, "reasoning": 1, "cache": {"read": 1, "write": 0}})],
    )

    result = opencode.read_usage(database, session_id="ses-a", max_parts=1)

    assert isinstance(result, opencode.OpenCodeUnavailable)
    assert result.reason == "cumulative-anchor-mismatch"


def test_read_usage_accepts_real_cost_column_and_excludes_zero_dimensions(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a", tokens=(1, 2, 0, 0, 0))],
        [_message("msg-a", "ses-a")],
        [_part("part-a", "msg-a", "ses-a", {"input": 1, "output": 2, "reasoning": 0, "cache": {"read": 0, "write": 0}})],
    )

    result = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert result.session.cost == 0.0
    assert [item.dimension for item in result.components] == [
        "input", "cache_read", "cache_write", "output", "reasoning",
    ]
    assert [item.tokens for item in result.components] == [1, 0, 0, 2, 0]


def test_read_usage_accepts_schema_without_optional_cumulative_columns(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(database, [_session("ses-a", "/repo/a", tokens=(0, 0, 0, 0, 0))], [], [])
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE session DROP COLUMN tokens_input")

    result = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert result.components == ()


def test_read_usage_accepts_missing_unsupported_session_counters(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a", tokens=(1, 2, 3, 4, 5))],
        [_message("msg-a", "ses-a")],
        [_part("part-a", "msg-a", "ses-a", {"input": 1, "output": 2, "cache": {"read": 4}})],
    )
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE session DROP COLUMN tokens_reasoning")
        connection.execute("ALTER TABLE session DROP COLUMN tokens_cache_write")

    result = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert [(item.dimension, item.tokens) for item in result.components] == [
        ("input", 1), ("cache_read", 4), ("output", 2),
    ]


def test_read_usage_uses_authoritative_totals_when_optional_dimensions_are_absent(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a", tokens=(4, 6, 3, 3, 0))],
        [_message("msg-a", "ses-a"), _message("msg-b", "ses-a")],
        [
            _part("part-a", "msg-a", "ses-a", {"input": 1, "output": 2, "cache": {"read": 1}}),
            _part("part-b", "msg-b", "ses-a", {"input": 3, "output": 4, "cache": {"read": 2}}),
        ],
    )
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE session DROP COLUMN tokens_reasoning")
        connection.execute("ALTER TABLE session DROP COLUMN tokens_cache_write")

    result = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert [(item.part_id, item.dimension, item.tokens) for item in result.components] == [
        ("part-a", "input", 1), ("part-a", "cache_read", 1), ("part-a", "output", 2),
        ("part-b", "input", 3), ("part-b", "cache_read", 2), ("part-b", "output", 4),
    ]
    assert all(item.telemetry_complete is False for item in result.components)


def test_read_usage_preserves_supported_dimensions_when_other_snapshot_dimensions_are_omitted(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a", tokens=(1, 2, 0, 10, 0))],
        [_message("msg-a", "ses-a")],
        [_part("part-a", "msg-a", "ses-a", {"input": 1, "output": 2, "cache": {"read": 10}})],
    )

    result = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert [(item.dimension, item.tokens) for item in result.components] == [
        ("input", 1), ("cache_read", 10), ("output", 2),
    ]
    assert all(item.telemetry_complete is False for item in result.components)


def test_read_usage_marks_only_dimensions_absent_from_both_sources_as_unsupported(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a", tokens=(1, 2, 0, 10, 0))],
        [_message("msg-a", "ses-a")],
        [_part("part-a", "msg-a", "ses-a", {"input": 1, "output": 2, "cache": {"read": 10}})],
    )

    result = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert result.components[0].unsupported_dimensions == ()


def test_explicit_session_directory_match_uses_canonical_equality(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(database, [_session("ses-a", str(tmp_path / "repo"), tokens=(0, 0, 0, 0, 0))], [], [])
    (tmp_path / "repo").mkdir()

    result = opencode.read_usage(database, session_id="ses-a", directory=str(tmp_path / "repo" / ".." / "repo"))

    assert isinstance(result, opencode.OpenCodeReadSuccess)


def test_explicit_session_id_remains_authoritative_over_a_stale_directory_hint(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(database, [_session("ses-a", "/repo/a", tokens=(1, 2, 0, 0, 0))], [_message("msg-a", "ses-a")], [_part("part-a", "msg-a", "ses-a", {"input": 1, "output": 2, "reasoning": 0, "cache": {"read": 0, "write": 0}})])

    result = opencode.read_usage(database, session_id="ses-a", directory="/repo/b")

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert result.session.session_id == "ses-a"


def test_unqualified_same_directory_match_fails_closed_as_ambiguous(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(database, [_session("ses-a", "/repo/a"), _session("ses-b", "/repo/a", 1_900)], [], [])

    result = opencode.read_usage(database, directory="/repo/a", now=2.0)

    assert isinstance(result, opencode.OpenCodeAmbiguousSession)
    assert result.session_ids == ("ses-a", "ses-b")


def test_explicit_session_remains_authoritative_when_a_newer_session_exists(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-current", "/repo/a", updated=4_500, tokens=(0, 0, 0, 0, 0)), _session("ses-new", "/repo/a", updated=4_000)],
        [],
        [],
    )

    result = opencode.read_usage(
        database,
        session_id="ses-current",
        directory="/repo/a",
        started_at=4.0,
        now=5.0,
    )

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert result.session.session_id == "ses-current"


def test_resumed_explicit_session_remains_authoritative_when_newer_work_exists(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [
            _session("ses-stale", "/repo/a", updated=1_500, tokens=(0, 0, 0, 0, 0)),
            _session("ses-new", "/repo/a", updated=3_000, tokens=(0, 0, 0, 0, 0)),
        ],
        [],
        [],
    )

    result = opencode.read_usage(
        database,
        session_id="ses-stale",
        directory="/repo/a",
        started_at=2.0,
        now=4.0,
        start_skew_seconds=0,
    )

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert result.session.session_id == "ses-stale"


def test_explicit_session_is_unavailable_when_missing(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(database, [_session("ses-new", "/repo/a", updated=3_000, tokens=(0, 0, 0, 0, 0))], [], [])

    result = opencode.read_usage(
        database,
        session_id="ses-stale",
        directory="/repo/a",
        started_at=2.0,
        now=4.0,
        start_skew_seconds=0,
    )

    assert isinstance(result, opencode.OpenCodeUnavailable)
    assert result.reason == "session-not-found"


def test_cwd_only_selection_accepts_one_fresh_eligible_session(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(database, [_session("ses-only", "/repo/a", updated=3_000, tokens=(0, 0, 0, 0, 0))], [], [])

    result = opencode.read_usage(database, directory="/repo/a", started_at=2.0, now=4.0)

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert result.session.session_id == "ses-only"


def test_cwd_only_selection_matches_historical_yomux_directory(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    legacy_directory = str(tmp_path / "yolomux.dev8123")
    canonical_directory = str(tmp_path / "yomux.dev8123")
    _database(database, [_session("ses-moved", legacy_directory, updated=3_000, tokens=(0, 0, 0, 0, 0))], [], [])

    result = opencode.read_usage(database, directory=canonical_directory, started_at=2.0, now=4.0)

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert result.session.session_id == "ses-moved"


def test_read_is_read_only_and_never_reads_credential_table(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(database, [_session("ses-a", "/repo/a", tokens=(0, 0, 0, 0, 0))], [_message("msg-a", "ses-a")], [_part("part-a", "msg-a", "ses-a", {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}})])
    before = database.read_bytes()

    result = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert [item.tokens for item in result.components] == [0, 0, 0, 0, 0]
    assert database.read_bytes() == before


def test_missing_database_and_schema_are_typed_failures(tmp_path: Path) -> None:
    missing = opencode.read_usage(tmp_path / "missing.db", session_id="ses-a")
    assert isinstance(missing, opencode.OpenCodeUnavailable)
    assert missing.reason == "database-unavailable"

    malformed = tmp_path / "malformed.db"
    with sqlite3.connect(malformed) as connection:
        connection.execute("CREATE TABLE session (id TEXT PRIMARY KEY)")
    result = opencode.read_usage(malformed, session_id="ses-a")
    assert isinstance(result, opencode.OpenCodeSchemaMismatch)
    assert result.reason.startswith("missing-columns:session:")


def test_database_lock_is_a_typed_unavailable_result(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "locked.db"
    error = sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(opencode.sqlite3, "connect", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))

    result = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(result, opencode.OpenCodeUnavailable)
    assert result.reason == "database-locked"


def test_malformed_database_is_a_typed_unavailable_result(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "malformed.db"
    error = sqlite3.DatabaseError("file is not a database")
    monkeypatch.setattr(opencode.sqlite3, "connect", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))

    result = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(result, opencode.OpenCodeUnavailable)
    assert result.reason == "database-malformed"


def test_part_bound_and_malformed_token_snapshot_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a", tokens=(0, 1, 0, 0, 0))],
        [_message("msg-a", "ses-a"), _message("msg-b", "ses-a")],
        [
            _part("part-a", "msg-a", "ses-a", {"input": 1, "output": 2, "reasoning": 0, "cache": {"read": 0, "write": 0}}),
            _part("part-b", "msg-b", "ses-a", {"input": 2, "cache": {"read": "bad"}}),
        ],
    )

    bounded = opencode.read_usage(database, session_id="ses-a", max_parts=1)
    malformed = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(bounded, opencode.OpenCodeUnavailable)
    assert bounded.reason == "part-bound-exceeded"
    assert isinstance(malformed, opencode.OpenCodeUnavailable)
    assert malformed.reason == "malformed-token-snapshot"


def test_part_bound_counts_only_step_finish_rows(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a", tokens=(0, 3, 0, 0, 0))],
        [_message("msg-a", "ses-a"), _message("msg-b", "ses-a")],
        [
            ("tool-a", "msg-a", "ses-a", 1_620, 1_620, json.dumps({"type": "tool", "tool": "read"})),
            ("text-a", "msg-a", "ses-a", 1_650, 1_650, json.dumps({"type": "text", "text": "long output"})),
            _part("step-a", "msg-a", "ses-a", {"input": 0, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}}),
            _part("step-b", "msg-b", "ses-a", {"input": 0, "output": 2, "reasoning": 0, "cache": {"read": 0, "write": 0}}),
        ],
    )

    result = opencode.read_usage(database, session_id="ses-a", max_parts=3)

    assert isinstance(result, opencode.OpenCodeReadSuccess)
    assert [(item.part_id, item.tokens) for item in result.components if item.dimension == "output"] == [
        ("step-a", 1), ("step-b", 2),
    ]


def test_incremental_read_handles_a_session_beyond_the_initial_part_bound(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    count = opencode.DEFAULT_MAX_PARTS + 1
    messages = [_message(f"msg-{index}", "ses-a") for index in range(count)]
    parts = [
        _part(
            f"step-{index}", f"msg-{index}", "ses-a",
            {"input": 0, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        )
        for index in range(count)
    ]
    _database(database, [_session("ses-a", "/repo/a", tokens=(0, count, 0, 0, 0))], messages, parts)

    initial = opencode.read_usage(database, session_id="ses-a", max_parts=count)
    assert isinstance(initial, opencode.OpenCodeReadSuccess)
    revisions = {item.event_id: item.source_revision for item in initial.components if item.source_revision}

    incremental = opencode.read_usage(database, session_id="ses-a", known_event_revisions=revisions, incremental=True)

    assert isinstance(incremental, opencode.OpenCodeReadSuccess)
    assert incremental.components == ()


def test_incremental_read_emits_only_a_new_part_from_a_long_session(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    count = opencode.DEFAULT_MAX_PARTS + 1
    messages = [_message(f"msg-{index}", "ses-a") for index in range(count)]
    parts = [
        _part(
            f"step-{index}", f"msg-{index}", "ses-a",
            {"input": 0, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        )
        for index in range(count)
    ]
    _database(database, [_session("ses-a", "/repo/a", tokens=(0, count, 0, 0, 0))], messages, parts)
    initial = opencode.read_usage(database, session_id="ses-a", max_parts=count)
    assert isinstance(initial, opencode.OpenCodeReadSuccess)
    revisions = {item.event_id: item.source_revision for item in initial.components if item.source_revision}
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE part SET data = ?, time_updated = ? WHERE id = ?",
            (json.dumps({"type": "step-finish", "tokens": {"input": 0, "output": 2, "reasoning": 0, "cache": {"read": 0, "write": 0}}}), 1_700, "step-0"),
        )
        connection.execute("UPDATE session SET tokens_output = ? WHERE id = ?", (count + 1, "ses-a"))
    incremental = opencode.read_usage(database, session_id="ses-a", known_event_revisions=revisions, incremental=True)

    assert isinstance(incremental, opencode.OpenCodeReadSuccess)
    assert [(item.part_id, item.dimension, item.tokens) for item in incremental.components if item.tokens] == [("step-0", "output", 2)]


def test_incremental_read_skips_unchanged_long_identifier_revisions(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    session_id = "s" * 100
    part_id = "p" * 300
    message_id = "m" * 100
    _database(
        database,
        [_session(session_id, "/repo/a", tokens=(0, 1, 0, 0, 0))],
        [_message(message_id, session_id)],
        [_part(part_id, message_id, session_id, {"input": 0, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}})],
    )
    initial = opencode.read_usage(database, session_id=session_id)
    assert isinstance(initial, opencode.OpenCodeReadSuccess)
    revisions = {item.event_id: item.source_revision for item in initial.components if item.source_revision}

    incremental = opencode.read_usage(database, session_id=session_id, known_event_revisions=revisions, incremental=True)

    assert isinstance(incremental, opencode.OpenCodeReadSuccess)
    assert incremental.components == ()


def test_cursor_accepts_the_hashed_event_id_for_long_part_identifiers(tmp_path: Path) -> None:
    path = tmp_path / "cursors.json"
    store = opencode.OpenCodeCursorStore(path)
    event_id = opencode._event_id("s" * 100, "p" * 300, "output")

    store.prepare({}, event_revisions={event_id: "revision"})
    store.commit()

    state = store.state()
    assert isinstance(state, opencode.OpenCodeCursorState)
    assert state.event_revisions == {event_id: "revision"}


def test_first_read_with_another_session_cursor_still_validates_the_cumulative_anchor(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a", tokens=(0, 2, 0, 0, 0))],
        [_message("msg-a", "ses-a")],
        [_part("step-a", "msg-a", "ses-a", {"input": 0, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}})],
    )

    result = opencode.read_usage(
        database,
        session_id="ses-a",
        known_event_revisions={"opencode:other-session:step-a:output": "revision"},
    )

    assert isinstance(result, opencode.OpenCodeUnavailable)
    assert result.reason == "cumulative-anchor-mismatch"


def test_revised_step_finish_snapshot_keeps_stable_source_identity(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(
        database,
        [_session("ses-a", "/repo/a", tokens=(0, 1, 0, 0, 0))],
        [_message("msg-a", "ses-a")],
        [_part("step-a", "msg-a", "ses-a", {"input": 0, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}})],
    )
    first = opencode.read_usage(database, session_id="ses-a")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE part SET data = ?, time_updated = ? WHERE id = ?",
            (json.dumps({"type": "step-finish", "tokens": {"input": 0, "output": 2, "reasoning": 0, "cache": {"read": 0, "write": 0}}}), 1_700, "step-a"),
        )
        connection.execute(
            "UPDATE session SET tokens_output = ? WHERE id = ?",
            (2, "ses-a"),
        )
    second = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(first, opencode.OpenCodeReadSuccess)
    assert isinstance(second, opencode.OpenCodeReadSuccess)
    assert first.components[0].event_id == second.components[0].event_id


def test_missing_model_or_provider_is_unavailable_instead_of_unknown(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    session = list(_session("ses-a", "/repo/a"))
    session[3] = json.dumps({})
    _database(database, [tuple(session)], [_message("msg-a", "ses-a", model="")], [_part("part-a", "msg-a", "ses-a", {"output": 1})])

    result = opencode.read_usage(database, session_id="ses-a")

    assert isinstance(result, opencode.OpenCodeUnavailable)
    assert result.reason == "model-metadata-missing"


def test_missing_session_identity_is_ambiguous_instead_of_reading_a_newest_session(tmp_path: Path) -> None:
    database = tmp_path / "opencode.db"
    _database(database, [_session("ses-a", "/repo/a")], [], [])

    result = opencode.read_usage(database)

    assert isinstance(result, opencode.OpenCodeAmbiguousSession)
    assert result.reason == "session-selector-requires-id-or-directory"


def test_machinewide_inventory_resolves_explicit_ids_and_deduplicates_processes() -> None:
    observations = [
        opencode.OpenCodeProcessObservation(11, "opencode -s ses-a", "/repo/a"),
        opencode.OpenCodeProcessObservation(12, "opencode --session=ses-a", "/repo/a"),
        opencode.OpenCodeProcessObservation(13, "opencode", "/repo/b"),
    ]

    inventory = opencode.inventory_processes(observations)

    assert [item.session_id for item in inventory.sessions] == ["ses-a"]
    assert len(inventory.unavailable) == 1
    assert inventory.unavailable[0].reason == "session-identity-unavailable"


def test_agent_token_key_prefers_tmux_session_and_falls_back_to_process() -> None:
    assert opencode.agent_token_key_for_process(pid=12345, tmux_session="yo7111") == "yo7111"
    assert opencode.agent_token_key_for_process(pid=12345) == "opencode-process:12345"
    assert opencode.agent_token_key_for_process(pid=12345, tmux_session="  ") == "opencode-process:12345"


def test_machinewide_inventory_preserves_ambiguous_shared_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        opencode,
        "_database_session_candidates",
        lambda *_args, **_kwargs: ("ses-a", "ses-b"),
    )

    inventory = opencode.inventory_processes([
        opencode.OpenCodeProcessObservation(11, "opencode", str(tmp_path)),
    ])

    assert inventory.sessions == ()
    assert inventory.unavailable[0].reason == "ambiguous-session-identity"
    assert inventory.unavailable[0].candidates == ("ses-a", "ses-b")


def test_reader_queries_are_explicit_and_credential_free() -> None:
    source = Path(opencode.__file__).read_text(encoding="utf-8")

    assert "access_token" not in source
    assert "refresh_token" not in source
    assert "SELECT *" not in source
    assert opencode._SESSION_COLUMNS == (
        "id", "directory", "agent", "model", "time_created", "time_updated", "cost",
    )
    assert opencode._MESSAGE_COLUMNS == ("id", "session_id", "time_created", "time_updated", "data")
    assert opencode._PART_COLUMNS == ("id", "message_id", "session_id", "time_created", "time_updated", "data")


def test_cursor_store_repairs_pending_state_and_preserves_committed_values(tmp_path: Path) -> None:
    path = tmp_path / "cursors.json"
    first = opencode.OpenCodeCursorStore(path)
    first.prepare({"session:ses-a:output": 10}, {"session:ses-a:output": 0}, {"session:ses-a:output": 1})
    second = opencode.OpenCodeCursorStore(path)

    locked = second.state()
    assert isinstance(locked, opencode.OpenCodeUnavailable)
    assert locked.reason == "cursor-state-unavailable"
    first.rollback()
    first.prepare({"session:ses-a:output": 10}, {"session:ses-a:output": 0}, {"session:ses-a:output": 1})
    first.commit()
    restarted = opencode.OpenCodeCursorStore(path)

    state = restarted.state()
    assert isinstance(state, opencode.OpenCodeCursorState)
    assert state.values == {"session:ses-a:output": 10}
    assert state.sequences == {"session:ses-a:output": 1}


def test_cursor_store_is_durable_across_collector_restart_and_reset_resume(tmp_path: Path) -> None:
    path = tmp_path / "cursors.json"

    first = opencode.OpenCodeCursorStore(path)
    first.prepare({"session:ses-a:output": 10}, {"session:ses-a:output": 0}, {"session:ses-a:output": 1})
    first.commit()

    restarted = opencode.OpenCodeCursorStore(path)
    state = restarted.state()
    assert isinstance(state, opencode.OpenCodeCursorState)
    assert state.values["session:ses-a:output"] == 10

    restarted.prepare({"session:ses-a:output": 5}, {"session:ses-a:output": 1}, {"session:ses-a:output": 0}, state.values, state.epochs, state.sequences)
    restarted.commit()

    resumed = opencode.OpenCodeCursorStore(path)
    resumed_state = resumed.state()
    assert isinstance(resumed_state, opencode.OpenCodeCursorState)
    assert resumed_state.values["session:ses-a:output"] == 5
    assert resumed_state.epochs["session:ses-a:output"] == 1

    resumed.prepare({"session:ses-a:output": 6}, {"session:ses-a:output": 1}, {"session:ses-a:output": 1}, resumed_state.values, resumed_state.epochs, resumed_state.sequences)
    resumed.commit()
    final = opencode.OpenCodeCursorStore(path).state()
    assert isinstance(final, opencode.OpenCodeCursorState)
    assert final.values["session:ses-a:output"] == 6
    assert final.sequences["session:ses-a:output"] == 1


def test_cursor_presence_prevents_stale_delta_when_a_dimension_reappears(tmp_path: Path) -> None:
    path = tmp_path / "cursors.json"
    store = opencode.OpenCodeCursorStore(path)
    key = "session:ses-a:output"
    store.prepare({key: 10}, {key: 0}, {key: 1}, presence={key: True})
    store.commit()
    state = store.state()
    assert isinstance(state, opencode.OpenCodeCursorState)

    store.prepare(
        {key: 10}, {key: 0}, {key: 1}, state.values, state.epochs, state.sequences,
        presence={key: False}, expected_presence=state.presence,
    )
    store.commit()
    missing = store.state()
    assert isinstance(missing, opencode.OpenCodeCursorState)
    assert missing.presence == {key: False}

    store.prepare(
        {key: 12}, {key: 0}, {key: 1}, missing.values, missing.epochs, missing.sequences,
        presence={key: True}, expected_presence=missing.presence,
    )
    store.commit()
    resumed = store.state()
    assert isinstance(resumed, opencode.OpenCodeCursorState)
    assert resumed.values[key] == 12
    assert resumed.presence[key] is True


def test_cursor_state_is_reset_when_the_stats_database_is_replaced(tmp_path: Path) -> None:
    cursor_path = tmp_path / "cursors.json"
    database = tmp_path / "stats-v9.sqlite3"
    database.write_bytes(b"first")
    store = opencode.OpenCodeCursorStore(cursor_path)
    assert store.reset_for_database(database) is None
    store.prepare({"part:output": 10}, event_revisions={"part:output": "rev-1"})
    store.commit()

    replacement = tmp_path / "stats-v9.sqlite3.new"
    replacement.write_bytes(b"second")
    replacement.replace(database)

    assert store.reset_for_database(database) is None
    state = store.state()
    assert isinstance(state, opencode.OpenCodeCursorState)
    assert state.values == {}
    assert state.event_revisions == {}
    assert state.database_identity


def test_cursor_state_is_preserved_for_normal_stats_database_writes(tmp_path: Path) -> None:
    cursor_path = tmp_path / "cursors.json"
    database = tmp_path / "stats-v9.sqlite3"
    database.write_bytes(b"stable")
    store = opencode.OpenCodeCursorStore(cursor_path)
    store.reset_for_database(database)
    store.prepare({"part:output": 10}, event_revisions={"part:output": "rev-1"})
    store.commit()
    database.write_bytes(b"stable-with-new-content")

    assert store.reset_for_database(database) is None
    state = store.state()
    assert isinstance(state, opencode.OpenCodeCursorState)
    assert state.values == {"part:output": 10}
    assert state.event_revisions == {"part:output": "rev-1"}


def test_cursor_event_revisions_are_compacted_on_restart(tmp_path: Path) -> None:
    path = tmp_path / "cursors.json"
    payload = {
        "version": 1,
        "committed": {},
        "epochs": {},
        "sequences": {},
        "presence": {},
        "event_revisions": {
            f"event-{index:05d}": f"revision-{index:05d}"
            for index in range(opencode.MAX_CURSOR_ENTRIES + 17)
        },
        "database_identity": "",
        "pending": None,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    state = opencode.OpenCodeCursorStore(path).state()

    assert isinstance(state, opencode.OpenCodeCursorState)
    assert len(state.event_revisions) == opencode.MAX_CURSOR_ENTRIES
    assert "event-00000" not in state.event_revisions
    assert f"event-{opencode.MAX_CURSOR_ENTRIES - 1:05d}" in state.event_revisions
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert len(persisted["event_revisions"]) == opencode.MAX_CURSOR_ENTRIES


def test_cursor_event_revisions_are_bounded_when_prepared_and_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "cursors.json"
    store = opencode.OpenCodeCursorStore(path)
    revisions = {
        f"event-{index:05d}": f"revision-{index:05d}"
        for index in range(opencode.MAX_CURSOR_ENTRIES + 1)
    }

    store.prepare({}, event_revisions=revisions)
    store.commit()
    restarted = opencode.OpenCodeCursorStore(path).state()

    assert isinstance(restarted, opencode.OpenCodeCursorState)
    assert len(restarted.event_revisions) == opencode.MAX_CURSOR_ENTRIES
    assert "event-00000" not in restarted.event_revisions
    assert f"event-{opencode.MAX_CURSOR_ENTRIES:05d}" in restarted.event_revisions
