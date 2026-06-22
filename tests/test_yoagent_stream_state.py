import pytest

from yolomux_lib import app as app_module


pytestmark = pytest.mark.usefixtures("no_control_socket", "isolated_yoagent_conversation_state")


def test_yoagent_stream_hidden_thinking_is_not_exposed():
    visible, hidden = app_module.strip_yoagent_stream_hidden_thinking("<think>private reasoning")
    assert visible == ""
    assert hidden is True

    visible, hidden = app_module.strip_yoagent_stream_hidden_thinking("<think>private</think>Final answer")
    assert visible == "Final answer"
    assert hidden is True


def test_yoagent_stream_callback_uses_extracted_stream_owner(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    stream_events = []
    monkeypatch.setattr(webapp, "publish_yoagent_stream_delta", lambda *args, **kwargs: stream_events.append((args, kwargs)))
    try:
        callback = webapp.yoagent_stream_callback("stream-owner", "codex")
        callback({"kind": "hidden_work_delta", "text": "Checking stream owner"})
        fields = webapp.yoagent_stream_auxiliary_message_fields("stream-owner")
    finally:
        webapp.control_server.stop()

    assert webapp.yoagent_stream_states is webapp.yoagent_streams.store.states
    assert webapp.yoagent_stream_lock is webapp.yoagent_streams.store.lock
    assert stream_events[-1][0] == ("stream-owner", "")
    assert stream_events[-1][1]["phase"] == "thinking"
    assert fields["auxiliary_lines"] == ["thinking: Checking stream owner"]


def test_yoagent_stream_callback_separates_answer_from_auxiliary_events(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    events = []
    stored_messages = []
    webapp.publish_client_event = lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {}))
    monkeypatch.setattr(app_module.yoagent_conversation, "append_message", lambda message: stored_messages.append(message) or message)
    try:
        callback = webapp.yoagent_stream_callback("stream-1", "codex")
        callback({"kind": "hidden_work_delta", "text": "Checking repo state"})
        callback({"kind": "hidden_work_delta", "text": " and reading files"})
        callback({"kind": "tool_call_started", "tool_name": "command", "command": "python3 tools/check.py"})
        callback({"kind": "tool_call_delta", "tool_name": "command", "text": "line 1\nline 2"})
        callback({"kind": "assistant_delta", "text": "Visible "})
        callback({"kind": "assistant_delta", "text": "answer"})
        callback({"kind": "tool_call_finished", "tool_name": "command", "text": "passed"})
        callback({"kind": "turn_done"})
        webapp.record_yoagent_message("assistant", "Visible answer", **webapp.yoagent_stream_auxiliary_message_fields("stream-1"))
    finally:
        webapp.control_server.stop()

    payloads = [payload for event_type, payload in events if event_type == "yoagent_stream_delta"]
    assert payloads[-1]["content"] == "Visible answer"
    assert payloads[-1]["auxiliary_done"] is True
    assert payloads[-1]["auxiliary_preview"] == "tool done: command: passed"
    assert payloads[-1]["auxiliary_lines"] == [
        "thinking: Checking repo state and reading files",
        "tool start: command: python3 tools/check.py",
        "tool output: command: line 1\nline 2",
        "tool done: command: passed",
    ]
    assert stored_messages[-1]["auxiliaryPreview"] == "tool done: command: passed"
    assert "thinking: Checking repo state and reading files" in stored_messages[-1]["auxiliaryText"]
    assert "tool output: command: line 1\nline 2" in stored_messages[-1]["auxiliaryText"]
    assert stored_messages[-1]["auxiliaryDone"] is True
    assert stored_messages[-1]["streamItems"] == [
        {"kind": "thinking", "text": "thinking: Checking repo state and reading files"},
        {"kind": "tool", "text": "tool start: command: python3 tools/check.py"},
        {"kind": "tool", "text": "tool output: command: line 1\nline 2"},
        {"kind": "assistant", "text": "Visible answer"},
        {"kind": "tool", "text": "tool done: command: passed"},
    ]


def test_yoagent_stream_callback_preserves_interleaved_order():
    webapp = app_module.TmuxWebtermApp(["5"])
    last_payload = {}
    webapp.publish_client_event = lambda event_type, payload=None, **_kwargs: last_payload.update(payload or {}) if event_type == "yoagent_stream_delta" else None
    try:
        callback = webapp.yoagent_stream_callback("stream-order", "claude")
        callback({"kind": "hidden_work_delta", "text": "Reading context"})
        callback({"kind": "assistant_delta", "text": "First visible sentence. "})
        callback({"kind": "tool_call_started", "tool_name": "command", "command": "python3 tools/check.py"})
        callback({"kind": "tool_call_finished", "tool_name": "command", "text": "passed"})
        callback({"kind": "hidden_work_delta", "text": "Preparing final answer"})
        callback({"kind": "assistant_delta", "text": "Second visible sentence."})
        fields = webapp.yoagent_stream_auxiliary_message_fields("stream-order")
    finally:
        webapp.control_server.stop()

    assert last_payload["stream_items"] == [
        {"kind": "thinking", "text": "thinking: Reading context"},
        {"kind": "assistant", "text": "First visible sentence. "},
        {"kind": "tool", "text": "tool start: command: python3 tools/check.py"},
        {"kind": "tool", "text": "tool done: command: passed"},
        {"kind": "thinking", "text": "thinking: Preparing final answer"},
        {"kind": "assistant", "text": "Second visible sentence."},
    ]
    assert fields["stream_items"] == last_payload["stream_items"]


def test_yoagent_stream_callback_preserves_raw_thinking_detail_text():
    webapp = app_module.TmuxWebtermApp(["5"])
    last_payload = {}
    webapp.publish_client_event = lambda event_type, payload=None, **_kwargs: last_payload.update(payload or {}) if event_type == "yoagent_stream_delta" else None
    try:
        callback = webapp.yoagent_stream_callback("stream-raw-thinking", "claude")
        callback({"kind": "hidden_work_delta", "text": "First line\n"})
        callback({"kind": "hidden_work_delta", "text": "  second line"})
        fields = webapp.yoagent_stream_auxiliary_message_fields("stream-raw-thinking")
    finally:
        webapp.control_server.stop()

    assert last_payload["auxiliary_lines"] == ["thinking: First line second line"]
    assert last_payload["stream_items"] == [{"kind": "thinking", "text": "thinking: First line\n  second line"}]
    assert fields["stream_items"] == last_payload["stream_items"]


def test_yoagent_stream_callback_replaces_claude_thinking_heartbeat():
    webapp = app_module.TmuxWebtermApp(["5"])
    events = []
    webapp.publish_client_event = lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {}))
    try:
        callback = webapp.yoagent_stream_callback("stream-claude", "claude")
        callback({"kind": "hidden_work_delta", "text": "thinking... (~50 tokens)"})
        callback({"kind": "hidden_work_delta", "text": "Reading context"})
        callback({"kind": "hidden_work_delta", "text": " and checking files"})
    finally:
        webapp.control_server.stop()

    payloads = [payload for event_type, payload in events if event_type == "yoagent_stream_delta"]
    assert payloads[-1]["auxiliary_preview"] == "thinking: Reading context and checking files"
    assert payloads[-1]["auxiliary_lines"] == ["thinking: Reading context and checking files"]


def test_yoagent_conversation_persists_auxiliary_stream_fields(tmp_path):
    path = tmp_path / "conversation.jsonl"

    written = app_module.yoagent_conversation.append_message(
        {
            "role": "assistant",
            "content": "Visible answer",
            "auxiliaryLines": ["thinking: Checking repo state", "tool done: command: passed"],
            "auxiliaryPreview": "tool done: command: passed",
            "auxiliaryDone": True,
            "auxiliaryTruncated": True,
            "streamItems": [
                {"kind": "thinking", "text": "thinking: Checking repo state"},
                {"kind": "assistant", "text": "Visible answer"},
                {"kind": "tool", "text": "tool done: command: passed"},
            ],
        },
        path=path,
    )
    loaded = app_module.yoagent_conversation.load_messages(path=path)

    assert written is not None
    assert written["auxiliaryLines"] == ["thinking: Checking repo state", "tool done: command: passed"]
    assert written["auxiliaryText"] == "thinking: Checking repo state\ntool done: command: passed"
    assert written["auxiliaryPreview"] == "tool done: command: passed"
    assert written["auxiliaryDone"] is True
    assert written["auxiliaryTruncated"] is True
    assert written["streamItems"] == [
        {"kind": "thinking", "text": "thinking: Checking repo state"},
        {"kind": "assistant", "text": "Visible answer"},
        {"kind": "tool", "text": "tool done: command: passed"},
    ]
    assert loaded == [written]


def test_yoagent_conversation_persists_stream_items_without_auxiliary_lines(tmp_path):
    path = tmp_path / "conversation.jsonl"

    written = app_module.yoagent_conversation.append_message(
        {
            "role": "assistant",
            "content": "Visible answer",
            "streamItems": [
                {"kind": "assistant", "text": "Visible answer "},
                {"kind": "thinking", "text": "thinking: raw\n  detail"},
            ],
        },
        path=path,
    )
    loaded = app_module.yoagent_conversation.load_messages(path=path)

    assert written is not None
    assert "auxiliaryLines" not in written
    assert written["streamItems"] == [
        {"kind": "assistant", "text": "Visible answer "},
        {"kind": "thinking", "text": "thinking: raw\n  detail"},
    ]
    assert loaded == [written]


def test_yoagent_stream_callback_preserves_full_auxiliary_history():
    webapp = app_module.TmuxWebtermApp(["5"])
    last_payload = {}

    def publish(event_type, payload=None, **_kwargs):
        if event_type == "yoagent_stream_delta":
            last_payload.clear()
            last_payload.update(payload or {})

    webapp.publish_client_event = publish
    try:
        callback = webapp.yoagent_stream_callback("stream-long", "codex")
        long_line = "x" * 1200
        for index in range(5005):
            callback({"kind": "tool_call_delta", "tool_name": "command", "text": f"line {index} {long_line if index == 0 else ''}".rstrip()})
        callback({"kind": "turn_done"})
        fields = webapp.yoagent_stream_auxiliary_message_fields("stream-long")
    finally:
        webapp.control_server.stop()

    assert "auxiliary_truncated" not in last_payload
    assert len(last_payload["auxiliary_lines"]) == 5005
    assert len(fields["auxiliary_lines"]) == 5005
    assert fields["auxiliary_lines"][0] == f"tool output: command: line 0 {long_line}"
    assert "[truncated]" not in "\n".join(fields["auxiliary_lines"])
    assert fields["stream_items"][0]["text"] == f"tool output: command: line 0 {long_line}"
