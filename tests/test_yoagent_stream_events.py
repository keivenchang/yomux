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
        {"jsonrpc": "2.0", "id": "approval-1", "method": "item/commandExecution/requestApproval", "params": {"threadId": "t1", "turnId": "u1", "command": ["python3", "tools/check.py"], "reason": "run tests", "cwd": "/repo"}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "t1", "turn": {"items": []}}},
    ]

    events = [event for message in messages for event in normalize_codex_app_server_message(message)]

    assert [event["kind"] for event in events] == [ASSISTANT_DELTA, HIDDEN_WORK_DELTA, TOOL_CALL_STARTED, TOOL_CALL_DELTA, APPROVAL_REQUESTED, TURN_DONE]
    assert events[0]["text"] == "Visible answer"
    assert events[1]["summary"] is True
    assert events[2]["tool_name"] == "command"
    assert events[4]["command"] == "python3 tools/check.py"
    assert "approval requested: python3 tools/check.py" == yoagent_stream_event_auxiliary_line(events[4])


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
