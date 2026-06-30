import pytest

from yolomux_lib import app as app_module
from yolomux_lib.agent_comms.stream_events import ClaudeStreamJsonNormalizer


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


def test_yoagent_stream_callback_ignores_later_claude_heartbeats_after_words():
    webapp = app_module.TmuxWebtermApp(["5"])
    events = []
    webapp.publish_client_event = lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {}))
    try:
        callback = webapp.yoagent_stream_callback("stream-claude-live-words", "claude")
        callback({"kind": "hidden_work_delta", "text": "thinking... (~3 tokens)"})
        callback({"kind": "hidden_work_delta", "text": "Reading context"})
        callback({"kind": "hidden_work_delta", "text": "thinking... (~23 tokens)"})
        callback({"kind": "hidden_work_delta", "text": " and checking files"})
    finally:
        webapp.control_server.stop()

    payloads = [payload for event_type, payload in events if event_type == "yoagent_stream_delta"]
    assert payloads[2]["auxiliary_lines"] == ["thinking: Reading context"]
    assert payloads[-1]["auxiliary_lines"] == ["thinking: Reading context and checking files"]
    assert payloads[-1]["stream_items"] == [{"kind": "thinking", "text": "thinking: Reading context and checking files"}]


def test_yoagent_stream_callback_replaces_plain_claude_thinking_heartbeat_at_done(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    events = []
    stored_messages = []
    webapp.publish_client_event = lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {}))
    monkeypatch.setattr(app_module.yoagent_conversation, "append_message", lambda message: stored_messages.append(message) or message)
    try:
        callback = webapp.yoagent_stream_callback("stream-plain-heartbeat", "claude")
        callback({"kind": "thinking"})
        callback({"kind": "hidden_work_delta", "text": "Reading context across files"})
        callback({"kind": "hidden_work_delta", "text": " and preparing answer"})
        callback({"kind": "assistant_delta", "text": "Final answer"})
        callback({"kind": "turn_done"})
        fields = webapp.yoagent_stream_auxiliary_message_fields("stream-plain-heartbeat")
        webapp.record_yoagent_message("assistant", "Final answer", **fields)
    finally:
        webapp.control_server.stop()

    payloads = [payload for event_type, payload in events if event_type == "yoagent_stream_delta"]
    assert payloads[-1]["auxiliary_done"] is True
    assert payloads[-1]["auxiliary_lines"] == ["thinking: Reading context across files and preparing answer"]
    assert payloads[-1]["stream_items"] == [
        {"kind": "thinking", "text": "thinking: Reading context across files and preparing answer"},
        {"kind": "assistant", "text": "Final answer"},
    ]
    assert stored_messages[-1]["auxiliaryText"] == "thinking: Reading context across files and preparing answer"
    assert stored_messages[-1]["streamItems"][0]["text"] == "thinking: Reading context across files and preparing answer"
    assert "thinking: thinking" not in stored_messages[-1]["auxiliaryText"]


def test_yoagent_stream_callback_keeps_claude_token_progress_after_empty_thinking_snapshot(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    events = []
    stored_messages = []
    normalizer = ClaudeStreamJsonNormalizer()
    webapp.publish_client_event = lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {}))
    monkeypatch.setattr(app_module.yoagent_conversation, "append_message", lambda message: stored_messages.append(message) or message)
    try:
        callback = webapp.yoagent_stream_callback("stream-claude-token-progress", "claude")
        for line in [
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 50, "estimated_tokens_delta": 50, "session_id": "claude-session"},
            {"type": "stream_event", "session_id": "claude-session", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 50}}},
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 200, "estimated_tokens_delta": 150, "session_id": "claude-session"},
            {"type": "stream_event", "session_id": "claude-session", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 150}}},
            {"type": "assistant", "session_id": "claude-session", "message": {"content": [{"type": "thinking", "thinking": "", "signature": "signed-redacted-thinking"}]}},
        ]:
            for event in normalizer.normalize_item(line):
                callback(event)
        callback({"kind": "assistant_delta", "text": "Final answer"})
        callback({"kind": "turn_done"})
        fields = webapp.yoagent_stream_auxiliary_message_fields("stream-claude-token-progress")
        webapp.record_yoagent_message("assistant", "Final answer", **fields)
    finally:
        webapp.control_server.stop()

    payloads = [payload for event_type, payload in events if event_type == "yoagent_stream_delta"]
    assert payloads[-1]["auxiliary_lines"] == ["thinking... (~200 tokens)"]
    assert payloads[-1]["stream_items"] == [
        {"kind": "thinking", "text": "thinking... (~200 tokens)"},
        {"kind": "assistant", "text": "Final answer"},
    ]
    assert stored_messages[-1]["auxiliaryText"] == "thinking... (~200 tokens)"
    assert "thinking: thinking" not in stored_messages[-1]["auxiliaryText"]


def test_yoagent_stream_callback_keeps_claude_usage_from_hiding_thinking(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    events = []
    stored_messages = []
    normalizer = ClaudeStreamJsonNormalizer()
    webapp.publish_client_event = lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {}))
    monkeypatch.setattr(app_module.yoagent_conversation, "append_message", lambda message: stored_messages.append(message) or message)
    try:
        callback = webapp.yoagent_stream_callback("stream-claude-real-shape", "claude")
        for line in [
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 50, "estimated_tokens_delta": 50, "session_id": "claude-session"},
            {"type": "stream_event", "session_id": "claude-session", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 50}}},
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 86, "estimated_tokens_delta": 36, "session_id": "claude-session"},
            {"type": "stream_event", "session_id": "claude-session", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "signed-redacted-thinking"}}},
            {"type": "assistant", "session_id": "claude-session", "message": {"content": [{"type": "thinking", "thinking": "", "signature": "signed-redacted-thinking"}]}},
            {"type": "stream_event", "session_id": "claude-session", "event": {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Final answer"}}},
            {"type": "result", "is_error": False, "usage": {"input_tokens": 1, "output_tokens": 2}},
        ]:
            for event in normalizer.normalize_item(line):
                callback(event)
        callback({"kind": "turn_done"})
        fields = webapp.yoagent_stream_auxiliary_message_fields("stream-claude-real-shape")
        webapp.record_yoagent_message("assistant", "Final answer", **fields)
    finally:
        webapp.control_server.stop()

    payloads = [payload for event_type, payload in events if event_type == "yoagent_stream_delta"]
    assert payloads[-1]["auxiliary_preview"] == "thinking... (~86 tokens)"
    assert payloads[-1]["auxiliary_lines"] == ["thinking... (~86 tokens)"]
    assert payloads[-1]["stream_items"] == [
        {"kind": "thinking", "text": "thinking... (~86 tokens)"},
        {"kind": "assistant", "text": "Final answer"},
    ]
    assert stored_messages[-1]["auxiliaryPreview"] == "thinking... (~86 tokens)"
    assert stored_messages[-1]["auxiliaryText"] == "thinking... (~86 tokens)"
    assert "usage:" not in stored_messages[-1]["auxiliaryText"]


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


def test_yoagent_conversation_caps_oversized_auxiliary_fields(tmp_path):
    path = tmp_path / "conversation.jsonl"
    long_auxiliary = "x" * (app_module.yoagent_conversation.YOAGENT_AUXILIARY_LINE_LIMIT + 1000)
    long_stream_item = "y" * (app_module.yoagent_conversation.YOAGENT_STREAM_ITEM_TEXT_LIMIT + 1000)

    written = app_module.yoagent_conversation.append_message(
        {
            "role": "assistant",
            "content": "Visible answer",
            "auxiliaryLines": [long_auxiliary],
            "auxiliaryPreview": long_auxiliary,
            "streamItems": [{"kind": "tool", "text": long_stream_item}],
        },
        path=path,
    )
    loaded = app_module.yoagent_conversation.load_messages(path=path)

    assert written is not None
    assert written["auxiliaryTruncated"] is True
    assert written["auxiliaryLines"][0].endswith("[truncated]")
    assert written["auxiliaryPreview"].endswith("[truncated]")
    assert written["streamItems"][0]["text"].endswith("[truncated]")
    assert len(written["auxiliaryText"]) < len(long_auxiliary)
    assert len(written["streamItems"][0]["text"]) < len(long_stream_item)
    assert len(path.read_text(encoding="utf-8")) < 40_000
    assert loaded == [written]


def test_yoagent_stream_callback_truncates_oversized_auxiliary_history(no_control_socket):
    webapp = app_module.TmuxWebtermApp(["5"])
    last_payload = {}

    def publish(event_type, payload=None, **_kwargs):
        if event_type == "yoagent_stream_delta":
            last_payload.clear()
            last_payload.update(payload or {})

    webapp.yoagent_streams.publish_client_event = publish
    try:
        callback = webapp.yoagent_stream_callback("stream-long", "codex")
        long_line = "x" * 1200
        for index in range(5005):
            callback({"kind": "tool_call_delta", "tool_name": "command", "text": f"line {index} {long_line if index == 0 else ''}".rstrip()})
        callback({"kind": "turn_done"})
        fields = webapp.yoagent_stream_auxiliary_message_fields("stream-long")
    finally:
        webapp.control_server.stop()

    assert last_payload["auxiliary_truncated"] is True
    assert fields["auxiliary_truncated"] is True
    assert len(last_payload["auxiliary_lines"]) < 5005
    assert len(fields["auxiliary_lines"]) < 5005
    assert fields["auxiliary_lines"][-1] == "tool output: command: line 5004"
    assert "tool output: command: line 0 " not in "\n".join(fields["auxiliary_lines"])
    assert len("\n".join(fields["auxiliary_lines"])) <= app_module.yoagent_conversation.YOAGENT_AUXILIARY_TOTAL_LIMIT
    assert len("\n".join(item["text"] for item in fields["stream_items"])) <= app_module.yoagent_conversation.YOAGENT_STREAM_ITEMS_TOTAL_LIMIT
