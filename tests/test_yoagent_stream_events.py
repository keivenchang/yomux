import json

from yolomux_lib.yoagent.stream_events import APPROVAL_REQUESTED
from yolomux_lib.yoagent.stream_events import ASSISTANT_DELTA
from yolomux_lib.yoagent.stream_events import HIDDEN_WORK_DELTA
from yolomux_lib.yoagent.stream_events import TOOL_CALL_DELTA
from yolomux_lib.yoagent.stream_events import TOOL_CALL_FINISHED
from yolomux_lib.yoagent.stream_events import TOOL_CALL_STARTED
from yolomux_lib.yoagent.stream_events import TURN_DONE
from yolomux_lib.yoagent.stream_events import ClaudeStreamJsonNormalizer
from yolomux_lib.yoagent.stream_events import normalize_codex_app_server_message
from yolomux_lib.yoagent.stream_events import yoagent_stream_event_auxiliary_line


def test_codex_app_server_stream_events_cover_answer_reasoning_tool_and_approval():
    messages = [
        {"jsonrpc": "2.0", "method": "item/agentMessage/delta", "params": {"threadId": "t1", "turnId": "u1", "itemId": "a1", "delta": "Visible answer"}},
        {"jsonrpc": "2.0", "method": "item/reasoning/summaryTextDelta", "params": {"threadId": "t1", "turnId": "u1", "itemId": "r1", "delta": "Checking repo state"}},
        {"jsonrpc": "2.0", "method": "item/started", "params": {"threadId": "t1", "turnId": "u1", "item": {"type": "commandExecution", "id": "cmd1", "command": "python3 tools/check.py"}}},
        {"jsonrpc": "2.0", "method": "item/commandExecution/outputDelta", "params": {"threadId": "t1", "turnId": "u1", "itemId": "cmd1", "delta": "tests passed"}},
        {"jsonrpc": "2.0", "method": "item/completed", "params": {"threadId": "t1", "turnId": "u1", "item": {"type": "commandExecution", "id": "cmd1", "aggregatedOutput": "tests passed"}}},
        {"jsonrpc": "2.0", "id": "approval-1", "method": "item/commandExecution/requestApproval", "params": {"threadId": "t1", "turnId": "u1", "command": ["python3", "tools/check.py"], "reason": "run tests", "cwd": "/repo"}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "t1", "turn": {"items": []}}},
    ]

    events = [event for message in messages for event in normalize_codex_app_server_message(message)]

    assert [event["kind"] for event in events] == [ASSISTANT_DELTA, HIDDEN_WORK_DELTA, TOOL_CALL_STARTED, TOOL_CALL_DELTA, TOOL_CALL_FINISHED, APPROVAL_REQUESTED, TURN_DONE]
    assert events[0]["text"] == "Visible answer"
    assert events[1]["summary"] is True
    assert events[2]["tool_name"] == "command"
    assert events[4]["text"] == "tests passed"
    assert events[5]["command"] == "python3 tools/check.py"
    assert "approval requested: python3 tools/check.py" == yoagent_stream_event_auxiliary_line(events[5])


def test_textless_reasoning_delta_uses_truthful_minimal_state():
    codex_events = normalize_codex_app_server_message({
        "jsonrpc": "2.0",
        "method": "item/reasoning/delta",
        "params": {"threadId": "t1", "turnId": "u1", "itemId": "r1", "delta": ""},
    })
    claude_events = ClaudeStreamJsonNormalizer().normalize_line(json.dumps({
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 50},
    }))

    assert codex_events[0]["text"] == "reasoning..."
    assert claude_events[0]["text"] == "thinking... (~50 tokens)"
    assert claude_events[0]["metadata"] == {"estimated_tokens": 50}
    assert yoagent_stream_event_auxiliary_line(codex_events[0]) == "thinking: reasoning..."
    assert yoagent_stream_event_auxiliary_line(claude_events[0]) == "thinking... (~50 tokens)"


def test_stream_events_preserve_long_text_without_truncation_marker():
    long_text = "x" * 12_000
    events = ClaudeStreamJsonNormalizer().normalize_line(json.dumps({
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "thinking_delta", "thinking": long_text},
    }))

    assert events[0]["text"] == long_text
    assert "[truncated]" not in events[0]["text"]


def test_claude_stream_json_events_cover_answer_thinking_tool_and_usage():
    normalizer = ClaudeStreamJsonNormalizer()
    lines = [
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Visible answer"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "thinking_delta", "thinking": "Inspecting tool output"}},
        {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Bash"}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"command":"ls"}'}},
        {"type": "content_block_stop", "index": 2},
        {"type": "user", "tool_use_result": {"stdout": "file.txt\n", "stderr": ""}},
        {"type": "result", "is_error": False, "usage": {"input_tokens": 10}},
    ]

    events = [event for line in lines for event in normalizer.normalize_line(json.dumps(line))]

    assert [event["kind"] for event in events] == [ASSISTANT_DELTA, HIDDEN_WORK_DELTA, TOOL_CALL_STARTED, TOOL_CALL_DELTA, TOOL_CALL_FINISHED, TOOL_CALL_FINISHED, "usage", TURN_DONE]
    assert events[0]["text"] == "Visible answer"
    assert events[1]["raw_thinking"] is True
    assert events[2]["tool_name"] == "Bash"
    assert events[4]["text"] == "ls"
    assert events[5]["text"] == "file.txt"


def test_claude_partial_assistant_thinking_blocks_feed_auxiliary_stream():
    events = ClaudeStreamJsonNormalizer().normalize_line(json.dumps({
        "type": "assistant",
        "message": {
            "content": [
                {"type": "thinking", "thinking": "Reading context"},
                {"type": "text", "text": "Visible answer"},
            ],
        },
    }))

    assert [event["kind"] for event in events] == [HIDDEN_WORK_DELTA]
    assert events[0]["text"] == "Reading context"
    assert events[0]["snapshot"] is True
    assert yoagent_stream_event_auxiliary_line(events[0]) == "thinking: Reading context"


def test_claude_stream_event_wrapper_feeds_current_cli_partials():
    normalizer = ClaudeStreamJsonNormalizer()
    lines = [
        {"type": "stream_event", "session_id": "claude-session", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Visible"}}},
        {"type": "stream_event", "session_id": "claude-session", "event": {"type": "content_block_delta", "index": 1, "delta": {"type": "thinking_delta", "thinking": "Reading files"}}},
        {"type": "stream_event", "session_id": "claude-session", "event": {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Bash"}}},
        {"type": "stream_event", "session_id": "claude-session", "event": {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"command":"printf ok"}'}}},
        {"type": "assistant", "session_id": "claude-session", "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "printf ok"}}]}},
        {"type": "stream_event", "session_id": "claude-session", "event": {"type": "content_block_stop", "index": 2}},
        {"type": "stream_event", "session_id": "claude-session", "event": {"type": "message_stop"}},
    ]

    events = [event for line in lines for event in normalizer.normalize_line(json.dumps(line))]

    assert [event["kind"] for event in events] == [ASSISTANT_DELTA, HIDDEN_WORK_DELTA, TOOL_CALL_STARTED, TOOL_CALL_DELTA, TOOL_CALL_FINISHED]
    assert events[0]["text"] == "Visible"
    assert events[0]["thread_id"] == "claude-session"
    assert events[1]["text"] == "Reading files"
    assert events[2]["tool_name"] == "Bash"
    assert events[4]["text"] == "printf ok"


def test_claude_thinking_token_events_keep_cumulative_progress_without_empty_snapshot():
    normalizer = ClaudeStreamJsonNormalizer()
    lines = [
        {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 50, "estimated_tokens_delta": 50, "session_id": "claude-session"},
        {"type": "stream_event", "session_id": "claude-session", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 50}}},
        {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 200, "estimated_tokens_delta": 150, "session_id": "claude-session"},
        {"type": "stream_event", "session_id": "claude-session", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 150}}},
        {"type": "assistant", "session_id": "claude-session", "message": {"content": [{"type": "thinking", "thinking": "", "signature": "signed-redacted-thinking"}]}},
    ]

    events = [event for line in lines for event in normalizer.normalize_line(json.dumps(line))]

    assert [event["kind"] for event in events] == [HIDDEN_WORK_DELTA, HIDDEN_WORK_DELTA]
    assert [event["text"] for event in events] == ["thinking... (~50 tokens)", "thinking... (~200 tokens)"]
    assert events[-1]["metadata"] == {"estimated_tokens": 200, "estimated_tokens_delta": 150}


def test_tool_auxiliary_lines_preserve_multiline_output():
    line = yoagent_stream_event_auxiliary_line({
        "kind": TOOL_CALL_FINISHED,
        "tool_name": "command",
        "text": "line 1\nline 2",
    })

    assert line == "tool done: command: line 1\nline 2"
