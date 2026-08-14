"""Characterization contracts for shared fixture parents."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.helpers.fixture_http_server import FixtureHttpServer
from tests.helpers.fixture_http_server import header_values
from tests.helpers.prompt_corpus import PromptCorpus
from tests.helpers.prompt_corpus import PromptCorpusPreset


def test_fixture_http_server_binds_ephemeral_port_preserves_headers_and_stops_thread(monkeypatch):
    app = SimpleNamespace(sessions=[], dangerously_yolo=False)
    monkeypatch.setenv("YOLOMUX_TEST_AUTH_BYPASS", "1")
    runtime = FixtureHttpServer.start(app, label="fixture-parent characterization")
    try:
        assert runtime.port > 0
        response = runtime.request("GET", "/api/ping")
        assert response.status == 200
        assert json.loads(response.body)["state"] == "ready"
        assert header_values(response.headers, "Content-Type") == ("application/json; charset=utf-8",)
    finally:
        runtime.close()
    assert not runtime.thread.is_alive()


def test_fixture_http_server_cleanup_keeps_order_and_first_error(monkeypatch):
    events = []

    class Server:
        def shutdown(self):
            events.append("shutdown")
            raise RuntimeError("shutdown failed")

        def server_close(self):
            events.append("close")
            raise RuntimeError("close failed")

    class Thread:
        def join(self, timeout):
            events.append(("join", timeout))

        def is_alive(self):
            return False

    runtime = FixtureHttpServer(Server(), Thread(), "fixture-parent characterization")
    with pytest.raises(RuntimeError, match="shutdown failed"):
        runtime.close()
    assert events == ["shutdown", "close", ("join", 2)]


def test_prompt_corpus_presets_preserve_format_empty_path_and_raw_text_policies(tmp_path):
    root = tmp_path / "prompt_corpus"
    captures = root / "captures"
    captures.mkdir(parents=True)
    (root / "case.json").write_text('{"raw_capture":"json text"}', encoding="utf-8")
    (root / "empty.yaml").write_text("", encoding="utf-8")
    (root / "raw.txt").write_text("plain text", encoding="utf-8")

    agent_tui = PromptCorpus(root, PromptCorpusPreset.AGENT_TUI)
    auto_approve = PromptCorpus(root, PromptCorpusPreset.AUTO_APPROVE)
    mock_agents = PromptCorpus(root, PromptCorpusPreset.MOCK_AGENTS)

    assert agent_tui.load(root / "case.json") == {"raw_capture": "json text"}
    assert agent_tui.load(root / "empty.yaml") is None
    assert mock_agents.load(root / "empty.yaml") == {}
    assert auto_approve.visible_text(root / "raw.txt") == "plain text"
    assert mock_agents.resolve(captures / "inventory.yaml", "case.yaml") == captures / "case.yaml"
    assert agent_tui.resolve(captures / "inventory.yaml", "case.yaml") == root / "case.yaml"
