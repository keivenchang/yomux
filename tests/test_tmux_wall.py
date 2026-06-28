from yolomux_lib.agent_tui import AgentTuiCapture

from tmux_wall import PaneInfo
from tmux_wall import TmuxWallApp
from tmux_wall import capture_pane_state
from tmux_wall import container_helper_path
from tmux_wall import is_loopback_bind_host
from tmux_wall import remote_bind_error


class FakeAgentPaneState:
    def as_dict(self):
        return {
            "target": "project1:0.0",
            "screen": {"key": "working", "text": "agent is working"},
            "display": {"screen_key": "working", "attention_kind": "working", "attention_label": "Working"},
            "approval": {"approval_visible": False},
            "attention_kind": "working",
            "attention_label": "Working",
            "agent_kind": "claude",
            "reason_code": "busy",
        }


def test_tmux_wall_loopback_bind_detection():
    assert is_loopback_bind_host("127.0.0.1")
    assert is_loopback_bind_host("localhost")
    assert is_loopback_bind_host("::1")
    assert not is_loopback_bind_host("0.0.0.0")
    assert not is_loopback_bind_host("::")


def test_tmux_wall_rejects_remote_bind_without_explicit_flag():
    assert "no authentication" in remote_bind_error("0.0.0.0", False)
    assert remote_bind_error("0.0.0.0", True) == ""
    assert remote_bind_error("127.0.0.1", False) == ""


def test_container_helper_path_uses_env_override(monkeypatch, tmp_path):
    helper = tmp_path / "show_project_containers.py"
    monkeypatch.setenv("YOLOMUX_CONTAINER_HELPER", str(helper))

    assert container_helper_path() == helper


def test_capture_pane_state_uses_agent_tui_shared_classification(monkeypatch):
    calls = []

    def fake_tmux_capture(_target, lines=80, visible_only=False, timeout=3.0):
        assert lines == 90
        assert timeout == 3.0
        return "visible\n" if visible_only else "raw\n"

    def fake_tmux_capture_styled(_target, lines=80, visible_only=False, timeout=3.0):
        assert lines == 90
        assert timeout == 3.0
        return "styled\n"

    def fake_capture_agent_pane(target, **kwargs):
        calls.append(("capture", target, kwargs["visible_only"], kwargs["styled"], kwargs["include_cursor"]))
        assert kwargs["capture_func"]("%1", visible_only=False) == "raw\n"
        assert kwargs["capture_styled_func"]("%1", visible_only=True) == "styled\n"
        return AgentTuiCapture(target=target, visible_text="raw\n", pane_text="raw\n")

    def fake_classify_agent_pane(target, **kwargs):
        calls.append(("classify", target, kwargs["prompt_source"], kwargs["include_cursor"], kwargs["include_transcript_activity"]))
        assert kwargs["capture_func"]("%1", visible_only=True) == "visible\n"
        return FakeAgentPaneState()

    monkeypatch.setattr("tmux_wall.tmux_capture_pane", fake_tmux_capture)
    monkeypatch.setattr("tmux_wall.tmux_capture_pane_styled", fake_tmux_capture_styled)
    monkeypatch.setattr("tmux_wall.capture_agent_pane", fake_capture_agent_pane)
    monkeypatch.setattr("tmux_wall.classify_agent_pane", fake_classify_agent_pane)

    text, error, state = capture_pane_state("project1:0.0", 90)

    assert text == "raw"
    assert error is None
    assert state["screen"]["key"] == "working"
    assert state["display"]["attention_label"] == "Working"
    assert state["reason_code"] == "busy"
    assert calls == [
        ("capture", "project1:0.0", False, False, False),
        ("classify", "project1:0.0", "pane", False, False),
    ]


def test_snapshot_includes_shared_agent_state(monkeypatch):
    pane = PaneInfo(
        target="project1:0.0",
        session="project1",
        window="0",
        pane="0",
        current_path="/home/keivenc/yolomux.dev8001",
        command="claude",
        active=True,
        title="claude",
    )
    state = {
        "screen": {"key": "needs-input", "text": "Which backend?"},
        "display": {"screen_key": "needs-input", "attention_kind": "question", "attention_label": "Question"},
        "approval": {"approval_visible": False},
        "attention_kind": "question",
        "attention_label": "Question",
        "agent_kind": "claude",
        "reason_code": "needs-input",
    }

    def fake_discover(_app):
        return {
            "panes": [pane],
            "targets": [pane.target],
            "pane_by_target": {pane.target: pane},
            "tmux_error": None,
            "containers": [],
            "container_error": None,
        }

    monkeypatch.setattr(TmuxWallApp, "discover", fake_discover)
    monkeypatch.setattr("tmux_wall.capture_pane_state", lambda _target, _lines: ("raw text", None, state))

    payload = TmuxWallApp([pane.target], slots=1, lines=25, interval=1.0).snapshot()
    slot = payload["slots"][0]

    assert slot["text"] == "raw text"
    assert slot["screen"]["key"] == "needs-input"
    assert slot["display"]["attention_label"] == "Question"
    assert slot["attention_kind"] == "question"
    assert slot["reason_code"] == "needs-input"
