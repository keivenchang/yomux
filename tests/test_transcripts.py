import json
import os
import time
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from yolomux_lib.transcripts import transcript_activity_state
from yolomux_lib.transcripts import transcript_activity_state_from_text
from yolomux_lib.transcripts import transcript_pending_approval
from yolomux_lib.transcripts import transcript_pending_approval_from_text


def jsonl(*records):
    return "\n".join(json.dumps(record) for record in records)


def test_transcript_activity_state_detects_pending_claude_tool_use():
    text = jsonl(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I will inspect it."},
                    {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}},
                ],
            },
        }
    )

    state = transcript_activity_state_from_text(text, "claude")

    assert state["key"] == "working"
    assert "Bash" in state["text"]


def test_transcript_activity_state_clears_claude_tool_result():
    text = jsonl(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}],
            },
        },
    )

    assert transcript_activity_state_from_text(text, "claude")["key"] == "idle"


def test_transcript_activity_state_detects_codex_streaming_turn():
    text = jsonl(
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}},
        {"type": "event_msg", "payload": {"type": "agent_message_delta", "delta": "working"}},
    )

    state = transcript_activity_state_from_text(text, "codex")

    assert state["key"] == "working"
    assert "codex" in state["text"]


def test_transcript_activity_state_completed_claude_turn_is_idle():
    text = jsonl(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Done."}],
                "stop_reason": "end_turn",
            },
        }
    )

    assert transcript_activity_state_from_text(text, "claude")["key"] == "idle"


def test_transcript_activity_state_gates_stale_pending_tool_by_recency(tmp_path):
    old_time = datetime.now(timezone.utc) - timedelta(seconds=60)
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        jsonl(
            {
                "type": "assistant",
                "timestamp": old_time.isoformat(),
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}],
                },
            }
        ) + "\n",
        encoding="utf-8",
    )
    os.utime(transcript, (old_time.timestamp(), old_time.timestamp()))

    assert transcript_activity_state(transcript, "claude")["key"] == "idle"


def test_transcript_activity_state_allows_recent_pending_tool(tmp_path):
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        jsonl(
            {
                "type": "assistant",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}],
                },
            }
        ) + "\n",
        encoding="utf-8",
    )
    os.utime(transcript, (time.time(), time.time()))

    state = transcript_activity_state(transcript, "claude")

    assert state["key"] == "working"
    assert "Bash" in state["text"]


def test_transcript_pending_approval_extracts_claude_bash_command():
    text = jsonl(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "python3 -m pytest tests/test_app.py"}},
                ],
            },
        }
    )

    state = transcript_pending_approval_from_text(text, "claude")

    assert state["visible"] is True
    assert state["source"] == "transcript"
    assert state["type"] == "bash"
    assert state["command"] == "python3 -m pytest tests/test_app.py"
    assert state["hash"]


def test_transcript_pending_approval_extracts_file_tool_and_clears_result():
    pending = jsonl(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "Edit", "input": {"file_path": "/repo/README.md"}},
                ],
            },
        }
    )
    completed = jsonl(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "Edit", "input": {"file_path": "/repo/README.md"}},
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}],
            },
        },
    )

    assert transcript_pending_approval_from_text(pending, "claude")["type"] == "file"
    assert transcript_pending_approval_from_text(completed, "claude")["visible"] is False


def test_transcript_pending_approval_gates_stale_candidates(tmp_path):
    old_time = datetime.now(timezone.utc) - timedelta(seconds=60)
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        jsonl(
            {
                "type": "assistant",
                "timestamp": old_time.isoformat(),
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "make test"}}],
                },
            }
        ) + "\n",
        encoding="utf-8",
    )
    os.utime(transcript, (old_time.timestamp(), old_time.timestamp()))

    assert transcript_pending_approval(transcript, "claude")["visible"] is False
