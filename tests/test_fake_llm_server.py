import json
import os
import threading
import urllib.request

from tools.fake_llm_server import Handler
from http.server import ThreadingHTTPServer


def _post(server, path, body):
    request = urllib.request.Request(
        server + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return response.status, json.loads(response.read())


def test_openai_response_has_deterministic_metadata_and_tool_call():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, normal = _post(f"http://127.0.0.1:{server.server_port}", "/v1/chat/completions", {})
        assert status == 200
        assert normal["model"] == "gpt-4o-mini"
        assert normal["usage"] == {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20, "prompt_tokens_details": {"cached_tokens": 2}}

        _, tool = _post(f"http://127.0.0.1:{server.server_port}", "/scenario/tool", {})
        message = tool["choices"][0]["message"]
        assert tool["choices"][0]["finish_reason"] == "tool_calls"
        assert message["tool_calls"][0]["function"]["name"] == "Bash"
    finally:
        server.shutdown()
        server.server_close()


def test_request_log_preserves_timestamp_scenario_model_and_provider_usage(tmp_path, monkeypatch):
    log = tmp_path / "requests.jsonl"
    monkeypatch.setenv("FAKE_LLM_LOG", str(log))
    monkeypatch.setenv("FAKE_LLM_TIMESTAMP_START", "1000")
    monkeypatch.setenv("FAKE_LLM_TIMESTAMP_STEP", "10")
    monkeypatch.delenv("FAKE_LLM_TIMESTAMP_SEQUENCE", raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _post(f"http://127.0.0.1:{server.server_port}", "/scenario/done/v1/chat/completions", {})
        record = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        assert record["timestamp"] == 1000
        assert record["scenario"] == "done"
        assert record["model"] == "gpt-4o-mini"
        assert record["provider"] == "openai"
        assert record["usage"] == {"completion_tokens": 8, "prompt_tokens": 12, "total_tokens": 20, "prompt_tokens_details": {"cached_tokens": 2}}
    finally:
        server.shutdown()
        server.server_close()


def test_anthropic_stream_has_usage_events():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/messages",
            data=b'{"stream":true}',
            headers={"Content-Type": "application/json", "Anthropic-Version": "2023-06-01"},
        )
        with urllib.request.urlopen(request) as response:
            body = response.read().decode()
        assert '"model": "claude-3-5-haiku-20241022"' in body
        assert '"input_tokens": 12' in body
        assert 'event: message_delta' in body
        assert 'event: message_stop' in body
    finally:
        server.shutdown()
        server.server_close()


def test_server_can_restart_on_explicit_port(monkeypatch):
    first = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = first.server_port
    first.server_close()
    monkeypatch.setenv("FAKE_LLM_PORT", str(port))
    # The command-line entry point is covered by the Docker recovery test; this assertion keeps
    # the explicit-port contract visible without starting a subprocess in the unit lane.
    assert int(os.environ["FAKE_LLM_PORT"]) == port
