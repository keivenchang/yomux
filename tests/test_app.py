from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
import io
import json
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs
from urllib.parse import urlparse

import pytest

from yolomux_lib import app as app_module
from yolomux_lib.common import AgentInfo
from yolomux_lib.common import PaneInfo
from yolomux_lib.common import SessionInfo
from yolomux_lib.yoagent import transports as transport_module


PROMPT_STATE_KEYS = set(app_module.blank_prompt_state())


@pytest.fixture(autouse=True)
def no_control_socket(monkeypatch):
    monkeypatch.setattr(app_module.YolomuxControlServer, "start", lambda self: None)
    monkeypatch.setattr(app_module.YolomuxControlServer, "stop", lambda self: None)


@pytest.fixture(autouse=True)
def isolated_yoagent_conversation_state(monkeypatch, tmp_path):
    state_dir = tmp_path / "yoagent-state"
    monkeypatch.setattr(app_module.yoagent_conversation, "YOAGENT_CONVERSATION_PATH", state_dir / "conversation.jsonl")
    monkeypatch.setattr(app_module.yoagent_conversation, "YOAGENT_CLI_STATE_PATH", state_dir / "cli-sessions.json")
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", tmp_path / "session-files-cache")


class FakeCodexAppServerStdin:
    def __init__(self):
        self.messages = []

    def write(self, text):
        self.messages.append(json.loads(text))
        return len(text)

    def flush(self):
        return None


class FakeCodexAppServerProcess:
    def __init__(self, messages):
        self.stdin = FakeCodexAppServerStdin()
        self.stdout = io.StringIO("\n".join(json.dumps(message) for message in messages) + "\n")
        self.stderr = io.StringIO("")
        self._returncode = None
        self.terminated = False

    def poll(self):
        return self._returncode

    def terminate(self):
        self.terminated = True
        self._returncode = 0

    def wait(self, timeout=None):
        self._returncode = 0
        return 0

    def kill(self):
        self._returncode = -9


def test_auto_approve_status_refreshes_session_order(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["old"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["new"], None))
    monkeypatch.setattr(webapp, "auto_approve_session_status", lambda session, **_kwargs: {"target": session})
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    try:
        payload, status = webapp.auto_approve_status()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["session_order"] == ["new"]
    assert payload["sessions"] == {"new": {"target": "new"}}


def test_share_token_url_seeds_whole_layout_sessions_and_layout():
    webapp = app_module.TmuxWebtermApp(["6", "7"])
    try:
        payload, status = webapp.create_share_token(
            "6",
            900,
            sessions=["6", "7"],
            base_url="https://yolo.example.test:8002",
            created_by="keivenc",
            layout="row@50(left,slot1)",
            tabs="left:6;slot1:7",
            finder={"root": "/home/keivenc/yolomux.dev1", "rootMode": "fixed", "mode": "tabber", "session": "7"},
            scheme="https",
            request_is_https=True,
            tls_available=True,
        )
        parsed = urlparse(payload["url"])
        params = parse_qs(parsed.query)

        assert status == HTTPStatus.OK
        assert payload["ok"] is True
        assert payload["session"] == "6"
        assert parsed.scheme == "https"
        assert parsed.netloc == "yolo.example.test:8002"
        assert parsed.path == f"/share/{payload['short_id']}"
        assert parsed.fragment == f"t={payload['token']}"
        assert "token" not in params
        assert "sessions" not in params
        assert "layout" not in params
        assert "tabs" not in params
        assert payload["sessions"] == ["6", "7"]
        assert payload["layout"] == "row@50(left,slot1)"
        assert payload["tabs"] == "left:6;slot1:7"
        assert payload["finder"] == {"root": "/home/keivenc/yolomux.dev1", "rootMode": "fixed", "mode": "tabber", "session": "7"}
        record = webapp.verify_share_token(payload["token"])
        assert record["session"] == "6"
        assert record["sessions"] == ["6", "7"]
        assert record["finder"] == {"root": "/home/keivenc/yolomux.dev1", "rootMode": "fixed", "mode": "tabber", "session": "7"}
        assert record["mode"] == "ro"
        assert record["scheme"] == "https"
        assert record["max_viewers"] == app_module.SHARE_MAX_VIEWERS_DEFAULT
        assert webapp.share_record_for_short_id(payload["short_id"])["token"] == payload["token"]
    finally:
        webapp.control_server.stop()


def test_share_debug_profile_is_opt_in_and_redacted(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SHARE_DEBUG_PROFILE_LOG_DIR", tmp_path)
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        disabled, disabled_status = webapp.create_share_token("6", 900)
        enabled, enabled_status = webapp.create_share_token("6", 900, debug_profile=True)

        assert disabled_status == HTTPStatus.OK
        assert enabled_status == HTTPStatus.OK
        assert disabled["debug_profile"] is False
        assert disabled["debugProfile"] is False
        assert enabled["debug_profile"] is True
        assert enabled["debugProfile"] is True
        assert webapp.verify_share_token(disabled["token"])["debug_profile"] is False
        assert webapp.verify_share_token(enabled["token"])["debug_profile"] is True

        denied, denied_status = webapp.record_share_debug_profile(disabled["token"], {"kind": "share-replay-health"})
        assert denied_status == HTTPStatus.FORBIDDEN
        assert denied["error"] == "debug/profiling upload is not enabled for this share"

        payload, status = webapp.record_share_debug_profile(
            enabled["token"],
            {
                "kind": "share-geometry-drift",
                "viewerId": "viewer-a",
                "url": f"https://host.example/share/{enabled['short_id']}#t={enabled['token']}",
                "nested": {"shareToken": enabled["token"], "text": f"token={enabled['token']}"},
            },
            ip="203.0.113.9",
            user_agent="Mozilla/5.0 Version/26.0 Safari/605.1.15",
        )

        assert status == HTTPStatus.OK
        assert payload["ok"] is True
        assert payload["logged"] is True
        record = webapp.verify_share_token(enabled["token"])
        stored_text = json.dumps(record["debug_profile_events"][-1], sort_keys=True)
        assert record["debug_profile_events"][-1]["browser"] == "Safari 26.0"
        assert "share-geometry-drift" in stored_text
        assert enabled["token"] not in stored_text
        assert f"/share/{enabled['short_id']}" not in stored_text
        assert "[redacted-share-token]" in stored_text
        log_text = (tmp_path / f"{enabled['short_id']}.jsonl").read_text(encoding="utf-8")
        assert enabled["token"] not in log_text
        assert f"/share/{enabled['short_id']}" not in log_text
    finally:
        webapp.control_server.stop()


def test_share_token_clamps_mode_scheme_viewers_and_allows_concurrent_shares():
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        payload, status = webapp.create_share_token(
            "6",
            900,
            base_url="https://yolo.example.test:8002",
            mode="rw",
            scheme="http",
            request_is_https=True,
            tls_available=True,
        )
        assert status == HTTPStatus.BAD_REQUEST
        assert payload["error"] == "write shares require https"

        payload, status = webapp.create_share_token(
            "6",
            900,
            base_url="https://yolo.example.test:8002",
            mode="rw",
            scheme="https",
            max_viewers=999,
            request_is_https=True,
            tls_available=True,
        )
        assert status == HTTPStatus.OK
        assert payload["mode"] == "rw"
        assert payload["scheme"] == "https"
        assert payload["max_viewers"] == app_module.SHARE_MAX_VIEWERS_HARD_LIMIT

        second, second_status = webapp.create_share_token(
            "6",
            120,
            mode="ro",
            scheme="http",
            request_is_https=False,
            tls_available=True,
        )
        assert second_status == HTTPStatus.OK
        assert second["mode"] == "ro"
        assert second["scheme"] == "http"
        active = webapp.active_share_payload()[0]
        assert {share["token"] for share in active["shares"]} == {payload["token"], second["token"]}
        assert {share["mode"] for share in active["shares"]} == {"rw", "ro"}
    finally:
        webapp.control_server.stop()


def test_share_token_forces_readonly_without_tls():
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        payload, status = webapp.create_share_token(
            "6",
            900,
            base_url="https://yolo.example.test:8002",
            mode="rw",
            scheme="https",
            request_is_https=False,
            tls_available=False,
        )

        assert status == HTTPStatus.OK
        assert payload["mode"] == "ro"
        assert payload["scheme"] == "http"
        assert urlparse(payload["url"]).scheme == "http"
    finally:
        webapp.control_server.stop()


def test_active_share_payload_and_stop_active_share():
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        empty, empty_status = webapp.active_share_payload(base_url="https://yolo.example.test:8002")
        assert empty_status == HTTPStatus.OK
        assert empty == {"ok": True, "active": False, "shares": []}

        payload, status = webapp.create_share_token(
            "6",
            900,
            base_url="https://yolo.example.test:8002",
            scheme="https",
            request_is_https=True,
            tls_available=True,
            layout="left",
            tabs="left:6",
            ui_state={"viewport": {"width": 1440, "height": 900}, "editor": {"modes": [{"path": "/tmp/a.md", "mode": "split"}]}},
        )
        assert status == HTTPStatus.OK

        active, active_status = webapp.active_share_payload(base_url="https://new-host.example.test:9443")
        assert active_status == HTTPStatus.OK
        assert active["active"] is True
        assert active["token"] == payload["token"]
        assert active["url"].startswith("https://new-host.example.test:9443/share/")
        assert active["url"].endswith(f"#t={payload['token']}")
        assert active["shares"][0]["token"] == payload["token"]

        scoped_status, scoped_status_code = webapp.share_status_payload(payload["token"], base_url="https://viewer.example.test:9443")
        assert scoped_status_code == HTTPStatus.OK
        assert scoped_status["token"] == payload["token"]
        assert scoped_status["url"].startswith("https://viewer.example.test:9443/share/")
        assert scoped_status["shares"] == []
        assert scoped_status["layout"] == "left"
        assert scoped_status["tabs"] == "left:6"
        assert scoped_status["viewport"] == {"width": 1440, "height": 900}
        assert scoped_status["uiState"]["editor"] == {"modes": [{"path": "/tmp/a.md", "mode": "split"}]}

        missing_status, missing_status_code = webapp.share_status_payload("wrong-token")
        assert missing_status_code == HTTPStatus.UNAUTHORIZED
        assert missing_status["active"] is False

        second, second_status = webapp.create_share_token(
            "6",
            120,
            base_url="http://new-host.example.test:9443",
            scheme="http",
            request_is_https=False,
            tls_available=True,
        )
        assert second_status == HTTPStatus.OK

        scoped, scoped_status = webapp.stop_active_share(payload["short_id"])
        assert scoped_status == HTTPStatus.OK
        assert scoped["stopped"] == 1
        assert scoped["active"] is True
        assert webapp.verify_share_token(payload["token"]) is None
        assert webapp.verify_share_token(second["token"]) is not None

        stopped, stopped_status = webapp.stop_active_share()
        assert stopped_status == HTTPStatus.OK
        assert stopped["stopped"] == 1
        assert webapp.verify_share_token(second["token"]) is None
        assert webapp.active_share_payload()[0] == {"ok": True, "active": False, "shares": []}
    finally:
        webapp.control_server.stop()


def test_share_viewer_registration_enforces_cap_and_decrements():
    webapp = app_module.TmuxWebtermApp(["6", "7"])
    try:
        payload, status = webapp.create_share_token("6", 900, sessions=["6", "7"], max_viewers=1)
        assert status == HTTPStatus.OK

        user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
        viewer, viewer_status = webapp.register_share_viewer(payload["token"], "6", "viewer-a", "203.0.113.4", user_agent)
        assert viewer_status == HTTPStatus.OK
        assert viewer["viewers"] == 1
        active = webapp.active_share_payload()[0]
        assert active["viewers"] == 1
        assert active["viewer_details"][0]["ip"] == "203.0.113.4"
        assert active["viewer_details"][0]["browser"] == "Chrome 125.0.0.0"
        assert active["viewer_details"][0]["connected_seconds"] >= 0

        same_viewer, same_viewer_status = webapp.register_share_viewer(payload["token"], "7", "viewer-a")
        assert same_viewer_status == HTTPStatus.OK
        assert same_viewer["viewers"] == 1
        assert webapp.active_share_payload()[0]["viewers"] == 1

        wrong_session, wrong_status = webapp.register_share_viewer(payload["token"], "8")
        assert wrong_status == HTTPStatus.FORBIDDEN
        assert wrong_session["error"] == "share token is scoped to a different session"

        rejected, rejected_status = webapp.register_share_viewer(payload["token"], "6", "viewer-b")
        assert rejected_status == HTTPStatus.FORBIDDEN
        assert rejected["error"] == "share viewer limit reached"
        status_frame = webapp.share_status_frame_payload(payload["token"])
        assert status_frame["viewer_details"][0]["ip"] == "203.0.113.4"
        assert status_frame["viewer_details"][0]["browser"] == "Chrome 125.0.0.0"

        assert webapp.unregister_share_viewer(payload["token"], "viewer-a") == 1
        assert webapp.unregister_share_viewer(payload["token"], "viewer-a") == 0
        assert webapp.active_share_payload()[0]["viewers"] == 0
    finally:
        webapp.control_server.stop()


def test_share_token_revokes_when_session_disappears():
    webapp = app_module.TmuxWebtermApp(["6", "7"])
    try:
        payload, status = webapp.create_share_token("6", 900, sessions=["6", "7"])
        assert status == HTTPStatus.OK
        assert webapp.verify_share_token(payload["token"])["session"] == "6"
        assert webapp.verify_share_token(payload["token"])["sessions"] == ["6", "7"]

        assert webapp.revoke_share_tokens_for_missing_sessions({"6"}) == 1
        assert webapp.verify_share_token(payload["token"]) is None
    finally:
        webapp.control_server.stop()


def test_share_extend_updates_expiry_and_status_frame_is_secret_free():
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        payload, status = webapp.create_share_token(
            "6",
            900,
            base_url="https://yolo.example.test:8002",
            scheme="https",
            request_is_https=True,
            tls_available=True,
            max_viewers=5,
        )
        assert status == HTTPStatus.OK
        before = payload["expires_at"]

        extended, extend_status = webapp.extend_share_token(payload["short_id"], 600)

        assert extend_status == HTTPStatus.OK
        assert extended["extended"] is True
        assert extended["expires_at"] > before
        status_frame = webapp.share_status_frame_payload(payload["token"])
        assert status_frame["active"] is True
        assert status_frame["short_id"] == payload["short_id"]
        assert status_frame["viewers"] == 0
        assert status_frame["max_viewers"] == 5
        assert "token" not in status_frame
        assert "url" not in status_frame
    finally:
        webapp.control_server.stop()


def test_share_record_ui_state_updates_late_viewer_layout_and_files():
    webapp = app_module.TmuxWebtermApp(["6", "7"])
    try:
        payload, status = webapp.create_share_token("6", 900, sessions=["6", "7"], tabs="left:6")
        assert status == HTTPStatus.OK
        token = payload["token"]

        webapp.update_share_record_ui_state(token, {
            "layout": "row@60(left,slot1)",
            "tabs": "left:6;slot1:file:/tmp/a.md,filediff:/tmp/c.py,filecopy:copy-1:/tmp/d.py,image:/tmp/screen.png",
            "finder": {"root": "/tmp", "rootMode": "fixed", "mode": "tabber", "session": "7"},
            "uiState": {"editor": {"modes": [{"path": "/tmp/b.md", "mode": "split"}]}},
        })

        record = webapp.verify_share_token(token)
        assert record["layout"] == "row@60(left,slot1)"
        assert record["tabs"] == "left:6;slot1:file:/tmp/a.md,filediff:/tmp/c.py,filecopy:copy-1:/tmp/d.py,image:/tmp/screen.png"
        assert record["finder"] == {"root": "/tmp", "rootMode": "fixed", "mode": "tabber", "session": "7"}
        assert record["ui_state"] == {"editor": {"modes": [{"path": "/tmp/b.md", "mode": "split"}]}}
        assert webapp.share_record_allows_file_path(record, "/tmp/a.md")
        assert webapp.share_record_allows_file_path(record, "/tmp/b.md")
        assert webapp.share_record_allows_file_path(record, "/tmp/c.py")
        assert webapp.share_record_allows_file_path(record, "/tmp/d.py")
        assert webapp.share_record_allows_file_path(record, "/tmp/screen.png")
        assert not webapp.share_record_allows_file_path(record, "/tmp/private.md")
    finally:
        webapp.control_server.stop()


def test_share_record_ui_state_updates_late_viewer_sessions_from_layout_tabs():
    webapp = app_module.TmuxWebtermApp(["6", "7"])
    try:
        payload, status = webapp.create_share_token("6", 900, sessions=["6"], tabs="left:6")
        assert status == HTTPStatus.OK
        token = payload["token"]

        webapp.update_share_record_ui_state(token, {
            "layout": "row@50(left,right)",
            "tabs": "left:6;right:7,ghost,file:/tmp/a.md",
            "finder": {"root": "/tmp", "rootMode": "fixed", "mode": "diff", "session": "7"},
        })

        record = webapp.verify_share_token(token)
        assert record["session"] == "6"
        assert record["sessions"] == ["6", "7"]
        assert record["finder"]["session"] == "7"
    finally:
        webapp.control_server.stop()


def test_share_ui_state_normalizes_viewport_and_appearance():
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        normalized = webapp.normalize_share_ui_state({
            "viewport": {"w": "1440.4", "h": "900.2"},
            "appearance": {
                "locale": "ja",
                "languagePref": "system",
                "uiFontSize": 999,
                "terminalFontSize": 15,
                "terminalLineHeight": 1,
                "editorFontSize": 14,
                "previewFontSize": 16,
                "fileExplorerFontSize": 13,
                "tabWidth": 240,
                "paneSpacing": -4,
                "theme": "dark",
                "resolvedTheme": "dark",
                "terminalTheme": "follow-app",
                "activeColor": "green",
                "separatorColor": "theme",
                "unknown": "ignored",
            },
            "chrome": {"tabMetaVisible": False, "infoSubTab": "yoagent"},
            "editor": {"modes": [{"path": "/tmp/a.py", "item": "file:/tmp/a.py", "mode": "diff", "diffExpandUnchanged": True}]},
            "scroll": [
                {"target": "preferences", "kind": "preferences", "top": "444.6", "left": 12, "ignored": "drop"},
                {"target": "editor:file:/tmp/a.py:editor", "kind": "editor", "path": "/tmp/a.py", "item": "file:/tmp/a.py", "source": "editor", "top": 80, "left": 2, "anchor": 5, "head": 7},
            ],
        })
        assert normalized["viewport"] == {"width": 1440, "height": 900}
        assert normalized["appearance"]["uiFontSize"] == 20
        assert normalized["appearance"]["paneSpacing"] == 0
        assert normalized["appearance"]["locale"] == "ja"
        assert normalized["appearance"]["languagePref"] == "system"
        assert normalized["appearance"]["terminalTheme"] == "follow-app"
        assert normalized["chrome"] == {"tabMetaVisible": False, "infoSubTab": "yoagent"}
        assert normalized["editor"]["modes"][0]["diffExpandUnchanged"] is True
        assert normalized["scroll"] == [
            {"target": "preferences", "kind": "preferences", "top": 445, "left": 12},
            {"target": "editor:file:/tmp/a.py:editor", "kind": "editor", "top": 80, "left": 2, "path": "/tmp/a.py", "item": "file:/tmp/a.py", "source": "editor", "anchor": 5, "head": 7},
        ]
        assert "unknown" not in normalized["appearance"]
    finally:
        webapp.control_server.stop()


def test_share_ui_state_patch_merges_geometry_without_losing_editor_state():
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        payload, status = webapp.create_share_token(
            "6",
            900,
            tabs="left:file:/tmp/a.md",
            ui_state={"editor": {"modes": [{"path": "/tmp/a.md", "mode": "split"}]}},
        )
        assert status == HTTPStatus.OK
        webapp.update_share_record_ui_state(payload["token"], {"uiStatePatch": {"viewport": {"width": 1280, "height": 720}}})
        webapp.update_share_record_ui_state(payload["token"], {"uiStateScroll": {"target": "preferences", "kind": "preferences", "top": 444, "left": 12}})
        webapp.update_share_record_ui_state(payload["token"], {"uiStateScroll": {"target": "info", "kind": "info", "top": 20, "left": 30}})
        record = webapp.verify_share_token(payload["token"])
        assert record["ui_state"]["editor"] == {"modes": [{"path": "/tmp/a.md", "mode": "split"}]}
        assert record["ui_state"]["viewport"] == {"width": 1280, "height": 720}
        assert record["ui_state"]["scroll"] == [
            {"target": "preferences", "kind": "preferences", "top": 444, "left": 12},
            {"target": "info", "kind": "info", "top": 20, "left": 30},
        ]
        assert webapp.share_record_allows_file_path(record, "/tmp/a.md")
    finally:
        webapp.control_server.stop()


def test_share_file_read_allowlist_tracks_current_editor_state():
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        payload, status = webapp.create_share_token(
            "6",
            900,
            tabs="left:6",
            ui_state={"editor": {"modes": [{"path": "/tmp/a.md", "mode": "edit"}]}},
        )
        assert status == HTTPStatus.OK
        record = webapp.verify_share_token(payload["token"])
        assert webapp.share_record_allows_file_path(record, "/tmp/a.md")

        webapp.update_share_record_ui_state(payload["token"], {"uiState": {"editor": {"modes": [{"path": "/tmp/b.md", "mode": "preview"}]}}})
        record = webapp.verify_share_token(payload["token"])
        assert not webapp.share_record_allows_file_path(record, "/tmp/a.md")
        assert webapp.share_record_allows_file_path(record, "/tmp/b.md")
    finally:
        webapp.control_server.stop()


def test_server_event_poll_seconds_accepts_fast_server_side_interval(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(
            app_module,
            "settings_payload",
            lambda: {"settings": {"performance": {"server_event_poll_ms": 100}}},
        )
        assert webapp.server_event_poll_seconds() == 0.25
        monkeypatch.setattr(
            app_module,
            "settings_payload",
            lambda: {"settings": {"performance": {"server_event_poll_ms": 850}}},
        )
        assert webapp.server_event_poll_seconds() == 0.85
    finally:
        webapp.control_server.stop()


def test_server_directory_event_poll_seconds_uses_own_interval(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(
            app_module,
            "settings_payload",
            lambda: {"settings": {"performance": {
                "server_event_poll_ms": 250,
                "server_background_file_event_poll_ms": 5000,
                "server_directory_event_poll_ms": 1250,
            }}},
        )
        assert webapp.server_event_poll_seconds() == 0.25
        assert webapp.server_background_file_event_poll_seconds() == 5.0
        assert webapp.server_directory_event_poll_seconds() == 1.25
    finally:
        webapp.control_server.stop()


def test_client_event_watch_sleep_uses_next_due_preference(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(
            app_module,
            "settings_payload",
            lambda: {"settings": {"performance": {"server_event_poll_ms": 250}}},
        )
        webapp.client_event_next_file_poll_at = 100.5
        webapp.client_event_next_background_file_poll_at = 100.75
        webapp.client_event_next_signature_poll_at = 100.25
        webapp.client_event_next_auto_poll_at = 101.0
        webapp.client_event_next_watched_pr_poll_at = 200.0
        assert webapp.client_event_watch_sleep_seconds(100.0) == pytest.approx(0.25)
        webapp.client_event_next_signature_poll_at = 0.0
        assert webapp.client_event_watch_sleep_seconds(100.0) == pytest.approx(0.25)
    finally:
        webapp.control_server.stop()


@pytest.mark.parametrize("method_name", ["events_payload", "search_payload", "auto_approve_status"])
def test_session_scoped_endpoints_refresh_before_unknown_session_guard(monkeypatch, method_name):
    webapp = app_module.TmuxWebtermApp(["old"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["new"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    monkeypatch.setattr(webapp, "auto_approve_session_status", lambda session, **_kwargs: {"target": session})
    try:
        if method_name == "search_payload":
            payload, status = webapp.search_payload("", session="new")
        else:
            payload, status = getattr(webapp, method_name)("new")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["session" if method_name != "auto_approve_status" else "target"] == "new"


def test_auto_approve_roster_uses_live_pane_working_signal(monkeypatch):
    # #28: the roster's working/idle signal comes from the LIVE pane (a cheap visible-only capture),
    # not transcript recency, while still discovering once and skipping the expensive hybrid prompt fan-out.
    info5 = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    info6 = SessionInfo(
        session="6",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="6",
                kind="codex",
                pid=123,
                pane_target="6:1.0",
                command="codex",
                cwd=None,
                status=None,
                session_id=None,
                transcript=None,
                error=None,
            )
        ],
    )
    discover_calls = []
    capture_calls = []
    pane_text = {"5": "working pane", "6": "idle pane", "6:1.0": "approval pane"}
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["5", "6"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: (discover_calls.append(tuple(sessions)) or {"5": info5, "6": info6}, []))

    def fake_capture(session, *_args, **kwargs):
        capture_calls.append((session, kwargs.get("visible_only")))
        return pane_text.get(session, "")

    monkeypatch.setattr(app_module, "tmux_capture_pane", fake_capture)
    monkeypatch.setattr(app_module, "agent_screen_state", lambda text: {"key": "approval" if text == "approval pane" else "working" if text == "working pane" else "idle", "text": text})
    monkeypatch.setattr(
        app_module,
        "approval_prompt_state",
        lambda text: {"visible": text == "approval pane", "type": "bash" if text == "approval pane" else "", "text": "Do you want to proceed?" if text == "approval pane" else "", "yes_selected": text == "approval pane", "action": ""},
    )
    monkeypatch.setattr(app_module, "hybrid_approval_prompt_state", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("roster must not run the prompt-detection fan-out")))
    monkeypatch.setattr(app_module, "auto_approve_lock_owner", lambda _session: None)
    webapp = app_module.TmuxWebtermApp(["5", "6"])
    discover_calls.clear()
    try:
        payload, status = webapp.auto_approve_status()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert discover_calls == [("5", "6")]  # discovered once for the whole roster, not per session
    assert {session for session, _visible in capture_calls} == {"5", "6:1.0"}
    assert all(visible_only is True for _session, visible_only in capture_calls)  # cheap visible-only capture only
    assert payload["sessions"]["5"]["screen"]["key"] == "working"  # live working pane spins
    assert payload["sessions"]["6"]["screen"]["key"] == "approval"  # pending approval lights the roster
    assert payload["sessions"]["5"]["prompt"]["visible"] is False  # no live prompt fan-out in the roster
    assert payload["sessions"]["6"]["prompt"]["visible"] is True


def test_transcripts_payload_exposes_server_version(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(webapp, "refresh_sessions", lambda: [])
    monkeypatch.setattr(webapp, "warm_metadata_cache_async", lambda sessions: None)
    try:
        payload = webapp.transcripts_payload()
    finally:
        webapp.control_server.stop()

    assert payload["server_version"] == app_module.YOLOMUX_VERSION
    assert payload["server_started_at"] == app_module.SERVER_STARTED_AT
    assert payload["server_uptime_seconds"] >= 0


def test_transcripts_payload_returns_stale_cache_and_refreshes(monkeypatch):
    calls = []
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])

    def fake_discover(sessions):
        calls.append(len(calls) + 1)
        return {"5": info}, []

    monkeypatch.setattr(app_module, "discover_sessions", fake_discover)
    monkeypatch.setattr(app_module, "session_to_json", lambda info, cache, allow_network=False: {"session": info.session, "call": calls[-1]})
    monkeypatch.setattr(app_module, "agent_auth_status", lambda: {})
    webapp = app_module.TmuxWebtermApp(["5"])
    calls.clear()
    monkeypatch.setattr(webapp, "refresh_sessions", lambda: [])
    monkeypatch.setattr(webapp, "warm_metadata_cache_async", lambda sessions: None)
    webapp.start_transcripts_payload_refresh = lambda: (webapp.refresh_transcripts_payload_cache() or True)
    try:
        first = webapp.transcripts_payload()
        with webapp.transcripts_payload_cache_lock:
            stored_at, value = webapp.transcripts_payload_cache
            webapp.transcripts_payload_cache = (stored_at - app_module.TRANSCRIPTS_PAYLOAD_CACHE_SECONDS - 1.0, value)
        second = webapp.transcripts_payload()
        third = webapp.transcripts_payload()
    finally:
        webapp.control_server.stop()

    assert first["sessions"]["5"]["call"] == 1
    assert second["sessions"]["5"]["call"] == 1
    assert second["cache"]["hit"] is True
    assert second["cache"]["stale"] is True
    assert third["sessions"]["5"]["call"] == 2
    assert calls == [1, 2]


def test_metadata_badge_pulse_expiry_does_not_persist(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    signature = {"main": "", "pr": "123", "status": "open", "ci": "pending"}
    persist_calls = []
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(webapp, "metadata_badge_signatures_for_session", lambda _payload: signature)
    monkeypatch.setattr(webapp, "persist_metadata_badge_state_locked", lambda: persist_calls.append("persist"))
    webapp.metadata_badge_signatures = {"6": dict(signature)}
    webapp.metadata_badge_pulse_until = {"6": {"ci": 99.0}}
    try:
        payloads = {"6": {}}
        webapp.apply_metadata_badge_pulses(payloads)
    finally:
        webapp.control_server.stop()

    assert persist_calls == []
    assert webapp.metadata_badge_pulse_until == {}
    assert "metadata_badge_pulse_remaining_ms" not in payloads["6"]


def test_metadata_badge_signature_change_persists(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    next_signature = {"main": "", "pr": "123", "status": "merged", "ci": "passing"}
    persist_calls = []
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(webapp, "metadata_badge_signatures_for_session", lambda _payload: next_signature)
    monkeypatch.setattr(webapp, "persist_metadata_badge_state_locked", lambda: persist_calls.append("persist"))
    webapp.metadata_badge_signatures = {"6": {"main": "", "pr": "123", "status": "open", "ci": "pending"}}
    try:
        webapp.apply_metadata_badge_pulses({"6": {}})
    finally:
        webapp.control_server.stop()

    assert persist_calls == ["persist"]
    assert webapp.metadata_badge_signatures == {"6": next_signature}


def test_prompt_and_screen_status_uses_transcript_activity_when_visible_pane_is_idle(monkeypatch, tmp_path):
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "make test"}}],
            },
        }) + "\n",
        encoding="utf-8",
    )
    info = SessionInfo(
        session="6",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=123,
                pane_target="6:0.0",
                command="claude",
                cwd=None,
                status=None,
                session_id="session-6",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda session, visible_only=False: "❯ ")
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"6": info}, []))
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        prompt, screen = webapp.prompt_and_screen_status("6")
    finally:
        webapp.control_server.stop()

    assert prompt["visible"] is False
    assert screen["key"] == "working"
    assert "Bash" in screen["text"]


def test_prompt_and_screen_status_captures_discovered_agent_pane(monkeypatch):
    info = SessionInfo(
        session="6",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="6",
                kind="codex",
                pid=123,
                pane_target="6:1.0",
                command="codex",
                cwd=None,
                status=None,
                session_id=None,
                transcript=None,
                error=None,
            )
        ],
    )
    capture_calls = []
    hybrid_targets = []

    def fake_capture(target, visible_only=False):
        capture_calls.append((target, visible_only))
        return "Do you want to proceed?\n❯ 1. Yes\n  2. No"

    def fake_hybrid(target, _visible_text, pane_text=None, **_kwargs):
        hybrid_targets.append((target, pane_text is not None))
        return {"visible": True, "type": "bash", "text": "Do you want to proceed?", "yes_selected": True, "action": "approve"}

    monkeypatch.setattr(app_module, "tmux_capture_pane", fake_capture)
    monkeypatch.setattr(app_module, "hybrid_approval_prompt_state", fake_hybrid)
    monkeypatch.setattr(app_module, "agent_screen_state", lambda _text: {"key": "approval", "text": "Do you want to proceed?"})
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        prompt, screen = webapp.prompt_and_screen_status("6", discovered_sessions={"6": info})
    finally:
        webapp.control_server.stop()

    assert prompt["visible"] is True
    assert set(prompt) == PROMPT_STATE_KEYS
    assert screen["key"] == "approval"
    assert capture_calls == [("6:1.0", True), ("6:1.0", False)]
    assert hybrid_targets == [("6", False), ("6", True)]


def test_prompt_and_screen_status_reports_os_errors(monkeypatch):
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("tmux failed")))
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        prompt, screen = webapp.prompt_and_screen_status("6")
    finally:
        webapp.control_server.stop()

    assert prompt["error"] == "tmux failed"
    assert set(prompt) == PROMPT_STATE_KEYS | {"error"}
    assert screen == {"key": "error", "text": "tmux failed"}


def test_prompt_and_screen_status_does_not_hide_programmer_errors(monkeypatch):
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda *_args, **_kwargs: "visible")
    monkeypatch.setattr(app_module, "hybrid_approval_prompt_state", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bug")))
    webapp = app_module.TmuxWebtermApp(["6"])
    try:
        with pytest.raises(RuntimeError, match="bug"):
            webapp.prompt_and_screen_status("6")
    finally:
        webapp.control_server.stop()


def test_activity_summary_payload_reuses_cached_session_summary(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "Fix tabs"}}) + "\n", encoding="utf-8")
    info = SessionInfo(
        session="5",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="5",
                kind="codex",
                pid=123,
                pane_target="5:0.0",
                command="codex",
                cwd=str(tmp_path),
                status="running",
                session_id="session-5",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    files_payload = {"files": [{"status": "M", "repo": str(tmp_path), "path": "README.md", "abs_path": str(tmp_path / "README.md"), "added": 1, "removed": 0, "mtime": 10}], "repos": [{"repo": str(tmp_path), "count": 1}], "errors": []}
    calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))
    monkeypatch.setattr(app_module, "session_project_metadata", lambda info, cache, allow_network=False: {"git": {"root": str(tmp_path), "branch": "main", "dirty_count": 1}, "pull_request": None, "linear": []})
    monkeypatch.setattr(app_module.session_files, "session_files_payload_for_info", lambda info, hours=24.0, **_kwargs: files_payload)

    def fake_build(info, project, files):
        calls.append(info.session)
        return {"session": info.session, "agent": "codex", "active": False, "repos": [str(tmp_path)], "files": {"count": 1, "added": 1, "removed": 0}, "lines": ["cached test"], "local": "cached test"}

    monkeypatch.setattr(app_module, "build_session_activity_summary", fake_build)
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    try:
        first = webapp.activity_summary_payload()
        second = webapp.activity_summary_payload()
        third = webapp.activity_summary_payload(force=True)
        localized = webapp.activity_summary_payload(locale="zh-Hant")
    finally:
        webapp.control_server.stop()

    assert calls == ["5", "5"]
    assert first["global"]["files"] == {"count": 1, "added": 1, "removed": 0}
    assert first["agents"][0]["label"] == "session '5' 0:codex"
    assert first["agents"][0]["recent_paths"][0]["path"] == str(tmp_path)
    assert second["sessions"]["5"]["local"] == "cached test"
    assert third["sessions"]["5"]["local"] == "cached test"
    assert localized["locale"] == "zh-Hant"


def test_activity_payload_returns_indefinite_stale_cache_and_refreshes(monkeypatch):
    snapshots = [
        {"5": {"last_user_input_ts": 100}},
        {"5": {"last_user_input_ts": 200}},
    ]
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        calls = []

        def fake_snapshot():
            calls.append("snapshot")
            return snapshots[min(len(calls) - 1, len(snapshots) - 1)]

        webapp.activity_ledger.snapshot = fake_snapshot
        webapp.refresh_tabber_activity_cache()
        first, status = webapp.activity_payload()
        second, _status = webapp.activity_payload()

        assert status == HTTPStatus.OK
        assert first["activity"]["5"]["last_user_input_ts"] == 100
        assert first["agents"] == []
        assert second["activity"]["5"]["last_user_input_ts"] == 100
        assert second["cache"]["hit"] is True
        assert second["cache"]["stale"] is False
        assert calls == ["snapshot"]

        stored_at, payload = webapp.tabber_activity_cache
        webapp.tabber_activity_cache = (
            stored_at - webapp.tabber_activity_refresh_seconds() - 1,
            payload,
        )
        monkeypatch.setattr(webapp, "start_tabber_activity_cache_refresh", lambda: "queued")
        stale, _status = webapp.activity_payload()

        assert stale["activity"]["5"]["last_user_input_ts"] == 100
        assert stale["cache"]["stale"] is True
        assert stale["cache"]["refreshing"] == "queued"
        assert calls == ["snapshot"]
    finally:
        webapp.control_server.stop()


def test_tabber_activity_refresh_seconds_uses_performance_setting(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"performance": {"tabber_activity_refresh_ms": 2500}}})
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        assert webapp.tabber_activity_refresh_seconds() == 2.5
    finally:
        webapp.control_server.stop()


def test_tabber_activity_cache_warmer_refreshes_snapshot(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    refreshes = []
    events = []

    def stop_after_sleep(seconds):
        raise RuntimeError(f"stop after sleeping {seconds}")

    try:
        webapp.tabber_activity_cache_warmer_running = True
        monkeypatch.setattr(webapp, "refresh_tabber_activity_cache", lambda: refreshes.append("refresh") or {})
        monkeypatch.setattr(webapp, "publish_activity_summary_ready_events", lambda trigger: events.append(trigger) or [])
        monkeypatch.setattr(webapp, "tabber_activity_refresh_seconds", lambda: 15.0)
        monkeypatch.setattr(app_module.time, "sleep", stop_after_sleep)

        with pytest.raises(RuntimeError, match="stop after sleeping"):
            webapp.tabber_activity_cache_warmer_loop()
    finally:
        webapp.control_server.stop()

    assert refreshes == ["refresh"]
    assert events == ["tabber_activity"]
    assert webapp.tabber_activity_cache_warmer_running is False


def test_activity_summary_agents_come_from_tabber_activity_cache(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        cached_agent = {
            "label": "session '5' 0:codex",
            "session": "5",
            "window_label": "0:codex",
            "agent_kind": "codex",
            "recent_paths": [{"path": "/repo/yolomux"}],
        }
        webapp.set_tabber_activity_cache({"activity": {}, "agents": [cached_agent], "errors": []})
        payload = webapp.activity_summary_payload()
        assert payload["agents"] == [cached_agent]
    finally:
        webapp.control_server.stop()


def test_refresh_sessions_rotates_activity_heartbeats_hourly(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["5"], None))
    try:
        calls = []
        monkeypatch.setattr(webapp.activity_ledger, "rotate_heartbeats", lambda: calls.append("rotate") or 1)

        assert webapp.refresh_sessions() == []
        assert webapp.refresh_sessions() == []
        webapp.activity_heartbeat_next_rotate_at = 0
        assert webapp.refresh_sessions() == []

        assert calls == ["rotate", "rotate"]
    finally:
        webapp.control_server.stop()


def test_corrupt_activity_ledger_does_not_break_app_start(monkeypatch, tmp_path):
    activity_path = tmp_path / "activity.json"
    heartbeat_path = tmp_path / "activity-heartbeats.jsonl"
    activity_path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(app_module, "ACTIVITY_PATH", activity_path)
    monkeypatch.setattr(app_module, "ACTIVITY_HEARTBEATS_PATH", heartbeat_path)
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))

    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        payload, status = webapp.activity_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["activity"] == {}


def test_session_files_payload_reuses_short_cache(monkeypatch):
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def fake_session_files_payload(session, infos, hours, from_ref=None, to_ref=None, repo_refs=None, **_kwargs):
        calls.append((session, tuple(infos), hours, from_ref, to_ref, repo_refs))
        return {"session": session, "files": [], "repos": [], "errors": []}, HTTPStatus.OK

    monkeypatch.setattr(app_module.session_files, "session_files_payload", fake_session_files_payload)
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.refresh_sessions = lambda: []
    try:
        first, first_status = webapp.session_files_payload("5")
        second, second_status = webapp.session_files_payload("5")
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert calls == [("5", ("5",), 24.0, None, None, None)]
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert first["files"] == second["files"] == []
    assert first["repos"] == second["repos"] == []
    assert first["errors"] == second["errors"] == []


def test_session_files_payload_reuses_shared_disk_cache_between_apps(monkeypatch):
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def fake_session_files_payload(session, infos, hours, from_ref=None, to_ref=None, repo_refs=None, **_kwargs):
        calls.append((session, tuple(infos), hours, from_ref, to_ref, repo_refs))
        return {"session": session, "files": [{"path": "/repo/one.txt"}], "repos": [{"path": "/repo"}], "errors": []}, HTTPStatus.OK

    monkeypatch.setattr(app_module.session_files, "session_files_payload", fake_session_files_payload)
    first_app = app_module.TmuxWebtermApp(["5"])
    second_app = app_module.TmuxWebtermApp(["5"])
    first_app.refresh_sessions = lambda: []
    second_app.refresh_sessions = lambda: []
    try:
        first, first_status = first_app.session_files_payload("5")
        second, second_status = second_app.session_files_payload("5")
    finally:
        first_app.control_server.stop()
        second_app.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert calls == [("5", ("5",), 24.0, None, None, None)]
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert second["files"] == [{"path": "/repo/one.txt"}]
    assert second["repos"] == [{"path": "/repo"}]
    assert second["errors"] == []


def test_session_files_payload_returns_stale_cache_and_refreshes(monkeypatch):
    info = SessionInfo(session="5", panes=[], selected_pane=None, agents=[])
    calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def fake_session_files_payload(session, infos, hours, from_ref=None, to_ref=None, repo_refs=None, **_kwargs):
        calls.append(len(calls) + 1)
        return {"session": session, "files": [{"path": f"file-{calls[-1]}.txt"}], "repos": [], "errors": []}, HTTPStatus.OK

    monkeypatch.setattr(app_module.session_files, "session_files_payload", fake_session_files_payload)
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.refresh_sessions = lambda: []
    webapp.start_session_files_cache_refresh = lambda cache_key, target, *args: (target(cache_key, *args) or True)
    try:
        first, first_status = webapp.session_files_payload("5")
        key = next(iter(webapp.session_files_cache))
        with webapp.session_files_cache_lock:
            stored_at, value = webapp.session_files_cache[key]
            webapp.session_files_cache[key] = (stored_at - app_module.SESSION_FILES_CACHE_SECONDS - 1.0, value)
        path, _signature = webapp.session_files_disk_cache_path(key)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["stored_at"] = float(record["stored_at"]) - app_module.SESSION_FILES_CACHE_SECONDS - 1.0
        path.write_text(json.dumps(record), encoding="utf-8")
        second, second_status = webapp.session_files_payload("5")
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert first["files"] == [{"path": "file-1.txt"}]
    assert second["files"] == [{"path": "file-1.txt"}]
    assert second["cache"]["hit"] is True
    assert second["cache"]["stale"] is True
    assert calls == [1, 2]


def test_update_client_watch_roots_filters_and_expires(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(app_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    try:
        payload = webapp.update_client_watch_roots({
            "roots": ["/repo", "relative", "", "/repo"],
            "files": ["/repo/DOIT.51.md", "relative"],
            "background_files": ["/repo/README.md", "/repo/DOIT.51.md", "relative"],
        })
        assert payload["roots"] == ["/repo"]
        assert payload["files"] == ["/repo/DOIT.51.md"]
        assert payload["background_files"] == ["/repo/README.md"]
        assert webapp.client_watch_roots_snapshot() == ["/repo"]
        assert webapp.client_watch_files_snapshot() == ["/repo/DOIT.51.md"]
        assert webapp.client_watch_background_files_snapshot() == ["/repo/README.md"]
        monkeypatch.setattr(app_module.time, "monotonic", lambda: 1000.0)
        monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
        assert webapp.client_watch_roots_snapshot() == []
        assert webapp.client_watch_files_snapshot() == []
        assert webapp.client_watch_background_files_snapshot() == []
    finally:
        webapp.control_server.stop()


def test_client_watch_roots_are_shared_across_app_instances(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "WATCH_INDEX_PATH", tmp_path / "watch-index.json")
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    app1 = app_module.TmuxWebtermApp([])
    app2 = app_module.TmuxWebtermApp([])
    try:
        app1.update_client_watch_roots({"roots": ["/repo/one"]})
        app2.update_client_watch_roots({"roots": ["/repo/two"]})

        assert app1.client_watch_roots_snapshot() == ["/repo/one", "/repo/two"]
        assert app2.client_watch_roots_snapshot() == ["/repo/one", "/repo/two"]
        payload = json.loads((tmp_path / "watch-index.json").read_text(encoding="utf-8"))
        assert sorted(payload["owners"]) == sorted([app1.watch_root_owner_id, app2.watch_root_owner_id])
    finally:
        app1.control_server.stop()
        app2.control_server.stop()


def test_client_watch_roots_concurrent_writes_do_not_clobber(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "WATCH_INDEX_PATH", tmp_path / "watch-index.json")
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    app1 = app_module.TmuxWebtermApp([])
    app2 = app_module.TmuxWebtermApp([])
    barrier = threading.Barrier(2)

    def update(app, root):
        barrier.wait(timeout=5)
        app.update_client_watch_roots({"roots": [root]})

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(update, app1, "/repo/one"),
                executor.submit(update, app2, "/repo/two"),
            ]
            for future in futures:
                future.result(timeout=5)
        assert app1.client_watch_roots_snapshot() == ["/repo/one", "/repo/two"]
        assert app2.client_watch_roots_snapshot() == ["/repo/one", "/repo/two"]
    finally:
        app1.control_server.stop()
        app2.control_server.stop()


def test_client_watch_roots_lock_free_read_during_write(monkeypatch, tmp_path):
    index_path = tmp_path / "watch-index.json"
    monkeypatch.setattr(app_module, "WATCH_INDEX_PATH", index_path)
    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    writer = app_module.TmuxWebtermApp([])
    reader = app_module.TmuxWebtermApp([])
    try:
        writer.update_client_watch_roots({"roots": ["/repo/old"]})
        observed: list[list[str]] = []

        with app_module.file_lock(index_path):
            thread = threading.Thread(target=lambda: observed.append(reader.client_watch_roots_snapshot()))
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive()
            assert observed == [["/repo/old"]]
            replacement = {
                "version": 1,
                "owners": {
                    "other-server": {
                        "entries": {
                            "client:/repo/new": {
                                "path": "/repo/new",
                                "source": "client",
                                "expires_at": 200.0,
                                "updated_at": 100.0,
                            }
                        }
                    }
                },
            }
            app_module.atomic_write_text(index_path, json.dumps(replacement, separators=(",", ":")), mode=0o600)

        assert reader.client_watch_roots_snapshot() == ["/repo/new"]
        index_path.write_text("{not-json", encoding="utf-8")
        assert reader.client_watch_roots_snapshot() == []
    finally:
        writer.control_server.stop()
        reader.control_server.stop()


def test_client_watch_roots_limit_keeps_multiple_owners_visible(tmp_path, caplog):
    index_path = tmp_path / "watch-index.json"
    clock = lambda: 100.0
    owner_a = app_module.SharedWatchRootIndex(index_path, "owner-a", limit=2, clock=clock)
    owner_b = app_module.SharedWatchRootIndex(index_path, "owner-b", limit=2, clock=clock)

    owner_a.update_client_roots(["/repo/a1", "/repo/a2"])
    owner_b.update_client_roots(["/repo/b1", "/repo/b2"])

    with caplog.at_level("WARNING"):
        assert owner_a.snapshot() == ["/repo/a1", "/repo/b1"]
    assert "shared watch-root index truncated from 4 live roots across 2 owners to 2" in caplog.text


def test_filesystem_change_summary_counts_entry_changes():
    previous = (
        (
            "/repo",
            (
                "/repo",
                "dir",
                100,
                0,
                (
                    ("old.txt", "file", 100, 10),
                    ("same.txt", "file", 100, 10),
                    ("old-dir", "dir", 100, 0),
                    ("mod.txt", "file", 100, 10),
                ),
            ),
        ),
    )
    current = (
        (
            "/repo",
            (
                "/repo",
                "dir",
                200,
                0,
                (
                    ("new.txt", "file", 100, 10),
                    ("same.txt", "file", 100, 10),
                    ("new-dir", "dir", 100, 0),
                    ("mod.txt", "file", 200, 10),
                ),
            ),
        ),
        ("/new-root", ("/new-root", "missing")),
    )

    summary = app_module.filesystem_change_summary(previous, current)

    assert summary["roots_changed"] == 2
    assert summary["roots_added"] == 1
    assert summary["roots_removed"] == 0
    assert summary["entries_added"] == 2
    assert summary["entries_removed"] == 2
    assert summary["entries_modified"] == 1
    assert summary["files_added"] == 1
    assert summary["files_removed"] == 1
    assert summary["files_modified"] == 1
    assert summary["dirs_added"] == 1
    assert summary["dirs_removed"] == 1
    assert summary["dirs_modified"] == 0


def test_poll_client_events_once_publishes_changed_signatures(monkeypatch):
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp([])
    events = []
    settings_signatures = [("settings", 1), ("settings", 2)]
    transcript_signatures = [("transcripts", 1), ("transcripts", 2)]
    filesystem_signatures = [
        (("/repo", ("/repo", "dir", 100, 0, (("old.txt", "file", 100, 10),))),),
        (("/repo", ("/repo", "dir", 200, 0, (("new.txt", "file", 100, 10),))),),
    ]
    monkeypatch.setattr(webapp, "settings_watch_signature", lambda: settings_signatures.pop(0))
    monkeypatch.setattr(webapp, "transcripts_watch_signature", lambda sessions: transcript_signatures.pop(0))
    monkeypatch.setattr(webapp, "filesystem_roots_watch_signature", lambda sessions: filesystem_signatures.pop(0))
    monkeypatch.setattr(webapp, "filesystem_roots_for_watch", lambda sessions: ["/repo"])
    monkeypatch.setattr(
        webapp,
        "filesystem_push_payload",
        lambda roots: {
            "roots": roots,
            "directories": [{"path": "/repo", "status": 200, "ok": True, "data": {"entries": []}}],
            "listing_summary": {"roots_requested": 1, "roots_listed": 1, "roots_error": 0, "entries_listed": 0, "files_listed": 0, "dirs_listed": 0},
            "compute_ms": 1.0,
        },
    )
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        webapp.set_session_files_cache(("k",), {"files": []}, HTTPStatus.OK)
        webapp.transcripts_payload_cache = (1.0, {"sessions": {}})
        assert webapp.poll_client_events_once() == []
        assert webapp.poll_client_events_once() == ["settings_changed", "transcripts_changed", "fs_changed"]
    finally:
        webapp.control_server.stop()

    assert [event_type for event_type, _payload in events] == ["settings_changed", "transcripts_changed", "fs_changed"]
    fs_payload = events[-1][1]
    assert fs_payload["change_summary"]["roots_changed"] == 1
    assert fs_payload["change_summary"]["entries_added"] == 1
    assert fs_payload["change_summary"]["entries_removed"] == 1
    assert fs_payload["listing_summary"]["roots_listed"] == 1
    assert webapp.session_files_cache != {}
    assert webapp.transcripts_payload_cache is not None


def test_poll_client_file_events_once_publishes_active_file_changes(monkeypatch):
    webapp = app_module.TmuxWebtermApp([])
    events = []
    signatures = [
        (("/repo/DOIT.51.md", ("/repo/DOIT.51.md", "file", 100, 10)),),
        (("/repo/DOIT.51.md", ("/repo/DOIT.51.md", "file", 200, 12)),),
    ]
    monkeypatch.setattr(webapp, "files_watch_signature", lambda: signatures.pop(0))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.poll_client_file_events_once() == []
        assert webapp.poll_client_file_events_once() == ["files_changed"]
    finally:
        webapp.control_server.stop()

    assert events == [("files_changed", {"files": [{"path": "/repo/DOIT.51.md", "signature": ("/repo/DOIT.51.md", "file", 200, 12)}], "count": 1})]


def test_poll_client_background_file_events_once_uses_own_signature(monkeypatch):
    events = []
    signatures = [
        (("/repo/README.md", ("/repo/README.md", "file", 100, 10)),),
        (("/repo/README.md", ("/repo/README.md", "file", 200, 12)),),
    ]
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(webapp, "background_files_watch_signature", lambda: signatures.pop(0))
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    try:
        assert webapp.poll_client_background_file_events_once() == []
        assert webapp.poll_client_background_file_events_once() == ["files_changed"]
    finally:
        webapp.control_server.stop()

    assert events == [("files_changed", {"files": [{"path": "/repo/README.md", "signature": ("/repo/README.md", "file", 200, 12)}], "count": 1})]


def test_filesystem_roots_for_watch_auto_indexes_active_directory(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    src = repo / "src"
    watched = tmp_path / "watched"
    transcript = tmp_path / "transcripts" / "codex.jsonl"
    src.mkdir(parents=True)
    info = SessionInfo(
        session="5",
        panes=[
            PaneInfo(
                session="5",
                window="0",
                pane="0",
                pane_id="%5",
                target="5:0.0",
                current_path=str(src),
                command="codex",
                active=True,
                window_active=True,
                title="codex",
                pid=123,
            )
        ],
        selected_pane=PaneInfo(
            session="5",
            window="0",
            pane="0",
            pane_id="%5",
            target="5:0.0",
            current_path=str(src),
            command="codex",
            active=True,
            window_active=True,
            title="codex",
            pid=123,
        ),
        agents=[
            AgentInfo(
                session="5",
                kind="codex",
                pid=123,
                pane_target="5:1.0",
                command="codex",
                cwd=str(repo),
                status=None,
                session_id="session-5",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    monkeypatch.setattr(app_module, "WATCH_INDEX_PATH", tmp_path / "watch-index.json")
    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"file_explorer": {"companion_dirs": []}}})
    monkeypatch.setattr(app_module.filesystem, "git_root_for_path", lambda path: str(repo) if str(path).startswith(str(repo)) else "")
    webapp = app_module.TmuxWebtermApp([])
    try:
        webapp.update_client_watch_roots({"roots": [str(watched)]})
        roots = webapp.filesystem_roots_for_watch({"5": info})
    finally:
        webapp.control_server.stop()

    assert str(watched) in roots
    assert str(repo) in roots
    assert str(src) not in roots
    assert str(transcript.parent) not in roots


def test_context_items_reuses_transcript_tail_cache(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "user_message", "message": "Check latency"}}) + "\n", encoding="utf-8")
    info = SessionInfo(
        session="5",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="5",
                kind="codex",
                pid=123,
                pane_target="5:0.0",
                command="codex",
                cwd=str(tmp_path),
                status="running",
                session_id="session-5",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    calls = []

    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"5": info}, []))

    def fake_tail_file_lines(path, lines):
        calls.append((path, lines))
        return transcript.read_text(encoding="utf-8")

    monkeypatch.setattr(app_module, "tail_file_lines", fake_tail_file_lines)
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        first, first_status = webapp.context_items("5", 20)
        second, second_status = webapp.context_items("5", 20)
    finally:
        webapp.control_server.stop()

    assert first_status == HTTPStatus.OK
    assert second_status == HTTPStatus.OK
    assert calls == [(transcript, app_module.MAX_TRANSCRIPT_TAIL_LINES)]
    assert first["items"] == second["items"]


def test_yoagent_session_summary_updates_from_transcript_delta(monkeypatch, tmp_path):
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text(
        "\n".join([
            json.dumps({"timestamp": "2026-06-07T10:00:00Z", "payload": {"type": "user_message", "message": "Fix the YO!agent summary table"}}),
            json.dumps({"timestamp": "2026-06-07T10:00:01Z", "payload": {"type": "agent_message", "message": "Added clickable session links."}}),
        ]) + "\n",
        encoding="utf-8",
    )
    info = SessionInfo(
        session="5",
        panes=[],
        selected_pane=None,
        agents=[
            AgentInfo(
                session="5",
                kind="codex",
                pid=123,
                pane_target="5:0.0",
                command="codex",
                cwd=str(tmp_path),
                status="running",
                session_id="session-5",
                transcript=str(transcript),
                error=None,
            )
        ],
    )
    prompts = []

    def fake_direct_backend(backend, prompt, **_kwargs):
        prompts.append(prompt)
        summary = "state: working\nsummary: Updating YO!agent session summaries from transcript deltas." if len(prompts) == 1 else "state: done\nsummary: Verified the rolling summary update path."
        return summary, "", {"backend": backend, "prompt_chars": len(prompt)}

    monkeypatch.setattr(app_module, "transcript_activity_is_recent", lambda *_args, **_kwargs: False)
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "run_yoagent_direct_prompt_backend", fake_direct_backend)
    settings = {"backend": "codex", "invocation": "cli", "refresh_interval_seconds": 120}
    try:
        first = webapp.update_yoagent_session_summary("5", info, settings)
        unchanged = webapp.update_yoagent_session_summary("5", info, settings)
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": "2026-06-07T10:05:00Z", "payload": {"type": "agent_message", "message": "Tests now pass."}}) + "\n")
        second = webapp.update_yoagent_session_summary("5", info, settings)
        state = app_module.read_yolomux_state().get(app_module.YOAGENT_SESSION_SUMMARIES_STATE_KEY, {})
    finally:
        webapp.control_server.stop()

    assert first["updated"] is True
    assert unchanged["updated"] is False
    assert unchanged["reason"] == "no new transcript lines"
    assert second["updated"] is True
    assert second["state"] == "done"
    assert "Fix the YO!agent summary table" in prompts[0]
    assert "Tests now pass." not in prompts[0]
    assert "Prior summary:\nUpdating YO!agent session summaries from transcript deltas." in prompts[1]
    assert "Tests now pass." in prompts[1]
    assert "Fix the YO!agent summary table" not in prompts[1]
    assert state["5"]["rolling_summary"] == "Verified the rolling summary update path."


def test_yoagent_session_summary_refresh_interval_zero_is_disabled(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp, "update_yoagent_session_summary", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("disabled summaries must not call the model")))
    try:
        result = webapp.tick_yoagent_session_summaries({"refresh_interval_seconds": 0})
    finally:
        webapp.control_server.stop()

    assert result == {"enabled": False, "updated": [], "skipped": []}


def test_yoagent_chat_uses_deterministic_fallback(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "sessions": {
            "5": {
                "session": "5",
                "agent": "codex",
                "agent_label": "Codex",
                "active": True,
                "repos": ["/repo/yolomux"],
                "files": {"count": 1, "added": 2, "removed": 0},
                "work": "editor fixes",
                "local": "Codex session 5 is active in yolomux.",
            }
        },
        "errors": [],
    })
    try:
        payload, status = webapp.yoagent_chat({"message": "what is session 5 doing?"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "deterministic"
    assert payload["fallback"] is False
    assert "editor fixes" in payload["answer"]
    assert "tmux session `5`" in payload["context_lines"][1]


def test_yoagent_chat_sends_to_accepting_agent_pane_without_extra_confirmation(monkeypatch):
    pane = PaneInfo(
        session="6",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%6",
        target="%6",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="6",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=123,
                pane_target="%6",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-6",
                transcript="/tmp/claude-session-6.jsonl",
                error=None,
                model="opus",
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["6"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"6": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-06-13T17:40:00+00:00",
        "session_order": ["6"],
        "global": {"headline": "Session 6 is idle."},
        "sessions": {"6": {"local": "Claude session 6 is idle in /repo/app."}},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    pastes = []

    def fake_tmux_paste_text(target, text, submit=False):
        pastes.append((target, text, submit))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(app_module, "tmux_paste_text", fake_tmux_paste_text)
    monkeypatch.setattr(webapp, "run_yoagent_claude_cli", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("visible sends must not use native resume")))

    try:
        payload, status = webapp.yoagent_chat({"message": "wait for session 6 to be done, then ask for date"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "yolomux"
    assert "accepting an AI prompt" in payload["answer"]
    assert "I am sending this exact prompt" in payload["answer"]
    assert "```text\ntell me the date\n```" in payload["answer"]
    assert payload["actions"] == []
    assert pastes == [("%6", "tell me the date", True)]


def test_yoagent_chat_sends_to_agent_waiting_for_input(monkeypatch):
    pane = PaneInfo(
        session="1",
        window="1",
        window_name="claude",
        pane="0",
        pane_id="%1",
        target="%1",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="1",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="1",
                kind="claude",
                pid=123,
                pane_target="%1",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-1",
                transcript="/tmp/claude-session-1.jsonl",
                error=None,
                model="opus",
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "needs-input", "text": "Want me to keep using system PT?"}))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-06-13T17:40:00+00:00",
        "session_order": ["1"],
        "global": {"headline": "Session 1 is waiting for input."},
        "sessions": {"1": {"local": "Claude session 1 is waiting in /repo/app."}},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    pastes = []

    def fake_tmux_paste_text(target, text, submit=False):
        pastes.append((target, text, submit))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(app_module, "tmux_paste_text", fake_tmux_paste_text)

    try:
        payload, status = webapp.yoagent_chat({"message": "ask session 1 what it has done today"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "yolomux"
    assert "accepting an AI prompt" in payload["answer"]
    assert "I am sending this exact prompt" in payload["answer"]
    assert "```text\nwhat have you done today?\n```" in payload["answer"]
    assert "ask session 1 what it has done today" not in payload["answer"]
    assert payload["actions"] == []
    assert pastes == [("%1", "what have you done today?", True)]


def test_yoagent_chat_sends_and_starts_background_result_watch(monkeypatch):
    pane = PaneInfo(
        session="6",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%6",
        target="%6",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="6",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=123,
                pane_target="%6",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-6",
                transcript="/tmp/claude-session-6.jsonl",
                error=None,
                model="opus",
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["6"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"6": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-06-13T17:40:00+00:00",
        "session_order": ["6"],
        "global": {"headline": "Session 6 is idle."},
        "sessions": {"6": {"local": "Claude session 6 is idle in /repo/app."}},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda target, text, submit=False: SimpleNamespace(returncode=0, stdout="", stderr=""))
    watchers = []
    monkeypatch.setattr(webapp, "start_yoagent_action_result_watcher", lambda preview, marker: watchers.append((preview, marker)) or {"started": True})

    try:
        payload, status = webapp.yoagent_chat({"message": "send `date` to tmux session 6 and show the result here"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert "show the result here" in payload["answer"]
    assert "I am awaiting the response" in payload["answer"]
    assert "```text\ndate\n```" in payload["answer"]
    assert watchers
    preview, marker = watchers[0]
    assert preview["return_result"] is True
    assert preview["target"]["transport"] == "tmux-legacy"
    assert preview["target"]["transport_label"] == "legacy tmux pane paste + Return"
    assert preview["target"]["agent_transcript"] == "/tmp/claude-session-6.jsonl"
    assert marker["transcript"] == "/tmp/claude-session-6.jsonl"


def test_yoagent_managed_transport_result_is_recorded_without_tmux_watcher(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["7"])
    target = {
        "session": "7",
        "pane_target": "%7",
        "agent_kind": "codex",
        "agent_session_id": "thread-7",
        "agent_model": "gpt-5",
        "agent_transcript": "",
        "transport": "codex-sdk",
        "transport_label": "Codex SDK",
        "transport_kind": "managed-session",
        "transport_capabilities": ["sdk"],
        "prompt": {},
        "screen": {"key": "idle", "text": ""},
    }

    class FakeManagedTransport:
        id = "codex-sdk"
        label = "Codex SDK"
        kind = "managed-session"
        capabilities = ("sdk",)

        def send(self, _target, text, **_kwargs):
            assert text == "summarize the diff"
            return transport_module.TransportSendResult(
                ok=True,
                sent=True,
                transport=self.id,
                transport_label=self.label,
                result_source="codex-sdk",
                text="Final managed SDK answer.",
            )

    class FakeRegistry:
        def get(self, _transport):
            return FakeManagedTransport()

    preview = {
        "id": "preview-1",
        "status": "ready",
        "session": "7",
        "text": "summarize the diff",
        "submit": True,
        "return_result": True,
        "target": target,
        "created_ts": app_module.time.time(),
    }
    webapp.yoagent_action_previews["preview-1"] = preview
    webapp.yoagent_transports = FakeRegistry()
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "yoagent_action_acceptance", lambda current: (True, "target agent is accepting an AI prompt"))
    monkeypatch.setattr(webapp, "start_yoagent_action_result_watcher", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("managed transport result should not start tmux watcher")))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})

    try:
        result, status = webapp.execute_yoagent_send_action({"preview_id": "preview-1"}, persist_result=True, start_result_watch=True)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert result["result_recorded"] is True
    assert result["result_source"] == "codex-sdk"
    assert "```text\nsummarize the diff\n```" in result["answer"]
    assert "I am awaiting the response" in result["answer"]
    assert conversation["messages"][0]["content"] == result["answer"]
    assert "Final managed SDK answer." in conversation["messages"][-1]["content"]
    assert "Result from Codex SDK target `7`" in conversation["messages"][-1]["content"]


def test_yoagent_handoff_uses_structured_transport_for_managed_target(monkeypatch):
    pane = PaneInfo(
        session="2",
        window="0",
        window_name="codex",
        pane="0",
        pane_id="%2",
        target="%2",
        current_path="/repo/app",
        command="codex",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="2",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="2",
                kind="codex",
                pid=123,
                pane_target="%2",
                command="codex",
                cwd="/repo/app",
                status=None,
                session_id="codex-session-2",
                transcript="",
                error=None,
                model="gpt-5",
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    webapp.yoagent_managed_targets["codex-session-2"] = {"managed": True, "transport": "codex-sdk"}
    sends = []

    class FakeManagedTransport:
        id = "codex-sdk"
        label = "Codex SDK"
        kind = "managed-session"
        capabilities = ("sdk",)

        def send(self, target, text, **_kwargs):
            sends.append((target, text))
            return transport_module.TransportSendResult(
                ok=True,
                sent=True,
                transport=self.id,
                transport_label=self.label,
                result_source="codex-sdk",
                text="Structured handoff answer.",
            )

    class FakeRegistry:
        managed = FakeManagedTransport()
        tmux = transport_module.TmuxLegacyTransport()

        def get(self, transport):
            return self.managed if transport == "codex-sdk" else self.tmux

        def first_available(self, target):
            return self.managed if target.get("transport") == "codex-sdk" else self.tmux

    source_preview = {
        "session": "1",
        "text": "what changed?",
        "target": {"session": "1", "pane_target": "%1", "agent_kind": "claude", "transport": "tmux-legacy", "transport_label": "legacy tmux pane paste + Return"},
        "handoff": {"source_session": "1", "session": "2", "instruction": "summarize that"},
    }
    webapp.yoagent_transports = FakeRegistry()
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1", "2"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"2": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})

    try:
        result = webapp.continue_yoagent_handoff(source_preview, "Session 1 found three changed files.")
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert result["ok"] is True
    assert sends
    target, text = sends[0]
    assert target["transport"] == "codex-sdk"
    assert "Use this context: Session 1 found three changed files." in text
    assert "Structured handoff answer." in conversation["messages"][-1]["content"]
    assert "Codex SDK target `2`" in conversation["messages"][-1]["content"]


def test_yoagent_action_target_prefers_managed_codex_transport(monkeypatch):
    pane = PaneInfo(
        session="7",
        window="0",
        window_name="codex",
        pane="0",
        pane_id="%7",
        target="%7",
        current_path="/repo/app",
        command="codex",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="7",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="7",
                kind="codex",
                pid=123,
                pane_target="%7",
                command="codex",
                cwd="/repo/app",
                status=None,
                session_id="codex-session-7",
                transcript="/tmp/codex-session-7.jsonl",
                error=None,
                model="gpt-5",
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["7"])
    webapp.yoagent_managed_targets["codex-session-7"] = {"managed": True}
    monkeypatch.setattr(transport_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["7"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"7": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))

    try:
        target, status = webapp.yoagent_action_target("7")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert target["managed"] is True
    assert target["transport"] == "codex-exec"
    assert target["transport_label"] == "Codex exec JSONL"
    assert target["transport_kind"] == "managed-one-shot"
    assert "structured-jsonl" in target["transport_capabilities"]


def test_yoagent_action_result_watcher_appends_transcript_result(monkeypatch, tmp_path):
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text("", encoding="utf-8")
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "claude",
        "agent_transcript": str(transcript),
        "transport": "pane-paste",
    }
    preview = {"session": "6", "text": "tell me the date", "return_result": True, "target": target}
    marker = webapp.yoagent_action_result_marker(target)
    transcript.write_text(
        json.dumps({"timestamp": "2026-06-13T17:41:00Z", "payload": {"type": "agent_message", "message": "The date is June 13, 2026."}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    events = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        result = webapp.run_yoagent_action_result_watcher(preview, marker, wait_seconds=1, poll_seconds=0.01)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert result["ok"] is True
    assert result["source"] == "transcript"
    assert "Result from tmux session `6`" in conversation["messages"][-1]["content"]
    assert "June 13, 2026" in conversation["messages"][-1]["content"]
    assert conversation["messages"][-1]["kind"] == "agent_result"
    assert conversation["messages"][-1]["session"] == "6"
    assert events == [("yoagent_conversation_changed", {"reason": "yoagent_result"})]


def test_yoagent_action_result_watcher_waits_for_claude_final_after_tool_use(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["2"])
    target = {
        "session": "2",
        "pane_target": "%2",
        "agent_kind": "claude",
        "agent_transcript": "/tmp/claude-session-2.jsonl",
        "transport": "pane-paste",
    }
    preview = {"session": "2", "text": "check the time", "return_result": True, "target": target}
    initial_text = json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "18:17:46 + 6 minutes = 18:23:46 PDT. Checking the clock now:"}],
            "stop_reason": "tool_use",
        },
    })
    tool_use = json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "date"}}],
            "stop_reason": "tool_use",
        },
    })
    tool_result = json.dumps({
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "18:17:57"}],
        },
    })
    final_text = json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Final answer: the projected time is 349 seconds ahead of now."}],
            "stop_reason": "end_turn",
        },
    })
    deltas = [
        initial_text,
        "\n".join([initial_text, tool_use]),
        "\n".join([initial_text, tool_use, tool_result]),
        "\n".join([initial_text, tool_use, tool_result, final_text]),
    ]
    calls = {"count": 0}

    def fake_delta(_marker):
        index = min(calls["count"], len(deltas) - 1)
        calls["count"] += 1
        return deltas[index]

    monkeypatch.setattr(webapp, "yoagent_transcript_delta_text", fake_delta)
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        result = webapp.run_yoagent_action_result_watcher(preview, {"transcript": target["agent_transcript"]}, wait_seconds=1, poll_seconds=0.01)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert result["ok"] is True
    assert calls["count"] >= 4
    assert "Final answer" in conversation["messages"][-1]["content"]
    assert "Checking the clock now" not in conversation["messages"][-1]["content"]


def test_yoagent_action_result_watcher_waits_for_codex_task_complete(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["3"])
    target = {
        "session": "3",
        "pane_target": "%3",
        "agent_kind": "codex",
        "agent_transcript": "/tmp/codex-session-3.jsonl",
        "transport": "pane-paste",
    }
    preview = {"session": "3", "text": "check the time", "return_result": True, "target": target}
    started = json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}})
    initial_delta = json.dumps({"type": "event_msg", "payload": {"type": "agent_message_delta", "delta": "I will check the clock now."}})
    tool_call = json.dumps({"type": "event_msg", "payload": {"type": "function_call", "call_id": "call-1", "name": "shell", "arguments": "{\"cmd\":\"date\"}"}})
    tool_output = json.dumps({"type": "event_msg", "payload": {"type": "function_call_output", "call_id": "call-1", "output": "18:17:57"}})
    final_text = json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "Final answer: the projected time is 349 seconds ahead of now."}})
    deltas = [
        "\n".join([started, initial_delta]),
        "\n".join([started, initial_delta, tool_call]),
        "\n".join([started, initial_delta, tool_call, tool_output]),
        "\n".join([started, initial_delta, tool_call, tool_output, final_text]),
    ]
    calls = {"count": 0}

    def fake_delta(_marker):
        index = min(calls["count"], len(deltas) - 1)
        calls["count"] += 1
        return deltas[index]

    monkeypatch.setattr(webapp, "yoagent_transcript_delta_text", fake_delta)
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda *_args, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        result = webapp.run_yoagent_action_result_watcher(preview, {"transcript": target["agent_transcript"]}, wait_seconds=1, poll_seconds=0.01)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert result["ok"] is True
    assert calls["count"] >= 4
    assert "Final answer" in conversation["messages"][-1]["content"]
    assert "I will check the clock now" not in conversation["messages"][-1]["content"]


def test_yoagent_pending_waits_show_and_clear(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "claude",
        "agent_transcript": "/tmp/claude-session-6.jsonl",
        "transport": "pane-paste",
    }
    preview = {"session": "6", "text": "tell me the date", "return_result": True, "target": target}
    marker = {"transcript": "/tmp/claude-session-6.jsonl"}
    events = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})))

    try:
        webapp.register_yoagent_action_wait("wait-1", preview, marker)
        waiting = webapp.yoagent_conversation_payload()["pending_waits"]
        webapp.finish_yoagent_action_wait("wait-1", "yoagent_wait_finished")
        cleared = webapp.yoagent_conversation_payload()["pending_waits"]
    finally:
        webapp.control_server.stop()

    assert waiting == [
        {
            "id": "wait-1",
            "session": "6",
            "label": "Waiting for tmux session `6` to reply",
            "started_ts": waiting[0]["started_ts"],
            "wait_seconds": app_module.YOAGENT_ACTION_RESULT_WAIT_SECONDS,
            "transcript": "/tmp/claude-session-6.jsonl",
        }
    ]
    assert cleared == []
    assert events == [
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_started"}),
        ("yoagent_conversation_changed", {"reason": "yoagent_wait_finished"}),
    ]


def test_yoagent_handoff_pending_wait_label_includes_regarding(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    preview = {
        "session": "1",
        "text": "what time is it?",
        "return_result": True,
        "target": {
            "session": "1",
            "pane_target": "%1",
            "agent_kind": "claude",
            "agent_transcript": "/tmp/claude-session-1.jsonl",
            "transport": "pane-paste",
        },
        "handoff": {
            "source_session": "1",
            "session": "2",
            "instruction": "add 6 minutes and say how far off that is",
        },
    }
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})

    try:
        webapp.register_yoagent_action_wait("wait-1", preview, {"transcript": "/tmp/claude-session-1.jsonl"})
        waiting = webapp.yoagent_conversation_payload()["pending_waits"]
    finally:
        webapp.control_server.stop()

    assert waiting[0]["label"] == (
        "Waiting for tmux session `1` to respond (regarding what time is it?), before handing off "
        "the next request to tmux session `2` (regarding add 6 minutes and say how far off that is)"
    )
    assert waiting[0]["handoff"] == {
        "source_session": "1",
        "session": "2",
        "source_regarding": "what time is it?",
        "target_regarding": "add 6 minutes and say how far off that is",
    }


def test_yoagent_handoff_sends_to_second_session_and_watches_result(monkeypatch):
    pane1 = PaneInfo(
        session="1",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%1",
        target="%1",
        current_path="/repo/one",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=101,
    )
    pane2 = PaneInfo(
        session="2",
        window="0",
        window_name="codex",
        pane="0",
        pane_id="%2",
        target="%2",
        current_path="/repo/two",
        command="codex",
        active=True,
        window_active=True,
        title="",
        pid=202,
    )
    info1 = SessionInfo(
        session="1",
        panes=[pane1],
        selected_pane=pane1,
        agents=[
            AgentInfo(
                session="1",
                kind="claude",
                pid=101,
                pane_target="%1",
                command="claude",
                cwd="/repo/one",
                status=None,
                session_id="claude-session-1",
                transcript="/tmp/claude-session-1.jsonl",
                error=None,
            )
        ],
    )
    info2 = SessionInfo(
        session="2",
        panes=[pane2],
        selected_pane=pane2,
        agents=[
            AgentInfo(
                session="2",
                kind="codex",
                pid=202,
                pane_target="%2",
                command="codex",
                cwd="/repo/two",
                status=None,
                session_id="codex-session-2",
                transcript="/tmp/codex-session-2.jsonl",
                error=None,
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    preview = {
        "session": "1",
        "text": "what time is it?",
        "return_result": True,
        "target": {
            "session": "1",
            "pane_target": "%1",
            "agent_kind": "claude",
            "agent_transcript": "/tmp/claude-session-1.jsonl",
            "transport": "pane-paste",
        },
        "handoff": {
            "source_session": "1",
            "session": "2",
            "instruction": "take that result, add 35 minutes, and ask session 2 if that is correct",
        },
    }
    sent = []
    watchers = []
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1", "2"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": info1, "2": info2}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda target, text, submit=False: sent.append((target, text, submit)) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(webapp, "start_yoagent_action_result_watcher", lambda action, marker: watchers.append((action, marker)) or {"started": True})

    try:
        result = webapp.continue_yoagent_handoff(preview, "The time is **2026-06-13 Sat 17:35:43 PDT** (Pacific Time).")
    finally:
        webapp.control_server.stop()

    assert result["ok"] is True
    assert sent and sent[0][0] == "%2"
    assert sent[0][2] is True
    assert sent[0][1] == "Is 6:10 PM the correct time now?"
    assert "tmux session `1` replied" not in sent[0][1]
    assert "ask session 2" not in sent[0][1].lower()
    assert watchers
    assert watchers[0][0]["session"] == "2"
    assert watchers[0][0]["return_result"] is True
    assert watchers[0][0]["target"]["agent_transcript"] == "/tmp/codex-session-2.jsonl"


def test_yoagent_handoff_right_time_now_sends_clean_single_question():
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    preview = {
        "session": "2",
        "target": {"session": "2"},
        "handoff": {
            "source_session": "2",
            "session": "1",
            "instruction": "add 10 minutes to it, and ask session 1 if that is the right time now",
        },
    }
    response = "\n".join([
        "It's **11:17 PM PDT** (2026-06-13 Sat, 23:17).",
        "",
        "Worth flagging: my clock jumped from ~6:16 PM to 11:17 PM.",
    ])

    try:
        prompt = webapp.yoagent_handoff_prompt(preview, response)
    finally:
        webapp.control_server.stop()

    assert prompt == "Is 11:27 PM the correct time now?"
    assert "\n" not in prompt
    assert "session 1" not in prompt.lower()


def test_yoagent_generic_handoff_prompt_hides_source_and_target_identity():
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    preview = {
        "session": "1",
        "target": {"session": "1"},
        "handoff": {
            "source_session": "1",
            "session": "2",
            "instruction": "summarize that result and ask session 2 if the risk is real",
        },
    }

    try:
        prompt = webapp.yoagent_handoff_prompt(preview, "The cache invalidation path can drop dirty files.")
    finally:
        webapp.control_server.stop()

    assert prompt == "Use this context: The cache invalidation path can drop dirty files. Task: summarize the context and say if the risk is real."
    assert "\n" not in prompt
    assert "tmux session" not in prompt
    assert "session 1" not in prompt.lower()
    assert "session 2" not in prompt.lower()


def test_yoagent_send_does_not_claim_success_when_text_remains_in_composer(monkeypatch):
    pane = PaneInfo(
        session="1",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%1",
        target="%1",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="1",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="1",
                kind="claude",
                pid=123,
                pane_target="%1",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-1",
                transcript="/tmp/claude-session-1.jsonl",
                error=None,
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda target, text, submit=False: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(webapp, "yoagent_text_still_in_composer", lambda target, text: True)
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})

    try:
        preview, preview_status = webapp.create_yoagent_action_preview({"type": "send_prompt", "session": "1", "text": "Use this context: hello Task: answer."})
        result, result_status = webapp.execute_yoagent_send_action({"preview_id": preview["id"]}, persist_result=True, start_result_watch=True)
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert preview_status == HTTPStatus.OK
    assert result_status == HTTPStatus.CONFLICT
    assert result["sent"] is False
    assert result["pasted"] is True
    assert "still in the target input box" in result["error"]
    assert conversation["messages"] == []


def test_yoagent_action_preview_allows_existing_target_composer_text_with_clear(monkeypatch):
    pane = PaneInfo(
        session="1",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%1",
        target="%1",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="1",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="1",
                kind="claude",
                pid=123,
                pane_target="%1",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-1",
                transcript="/tmp/claude-session-1.jsonl",
                error=None,
            )
        ],
    )
    visible_text = "\n".join([
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ Use this context:",
        "",
        "  It's 11:17 PM PDT.",
        "",
        "  Task: add 10 minutes and say if that is right.",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": info}, []))
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: visible_text)

    try:
        preview, status = webapp.create_yoagent_action_preview({"type": "send_prompt", "session": "1", "text": "what time is it?"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert preview["status"] == "ready"
    assert preview["screen"]["key"] == "input-draft"
    assert preview["screen"]["detected_text"] == "Use this context: It's 11:17 PM PDT. Task: add 10 minutes and say if that is right."
    assert preview["screen"]["detected_text_preview"] == "Use this context: It's 11:17 PM PDT. Task: add 10 minutes and say if that is right."
    assert "will clear it before sending" in preview["acceptance_text"]


def test_yoagent_chat_clears_existing_draft_before_send(monkeypatch):
    pane = PaneInfo(
        session="1",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%1",
        target="%1",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="1",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="1",
                kind="claude",
                pid=123,
                pane_target="%1",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-1",
                transcript="/tmp/claude-session-1.jsonl",
                error=None,
            )
        ],
    )
    draft_text = "\n".join([
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ token=secret-value run the release",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    empty_text = "\n".join([
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ ",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    cleared = {"value": False}
    webapp = app_module.TmuxWebtermApp(["1"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["1"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"1": info}, []))
    monkeypatch.setattr(app_module, "tmux_capture_pane", lambda target, visible_only=False: empty_text if cleared["value"] else draft_text)
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-06-13T17:40:00+00:00",
        "session_order": ["1"],
        "global": {"headline": "Session 1 is idle."},
        "sessions": {"1": {"local": "Claude session 1 is idle in /repo/app."}},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    operations = []

    def fake_clear(target):
        operations.append(("clear", target))
        cleared["value"] = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_paste(target, text, submit=False):
        operations.append(("paste", target, text, submit))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(app_module, "tmux_clear_input", fake_clear)
    monkeypatch.setattr(app_module, "tmux_paste_text", fake_paste)

    try:
        payload, status = webapp.yoagent_chat({"message": "ask session 1 what time it is"})
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert "cleared existing target input" in payload["answer"]
    assert "```text\nwhat time is it?\n```" in payload["answer"]
    assert operations == [
        ("clear", "%1"),
        ("paste", "%1", "what time is it?", True),
    ]
    assert "secret-value" not in payload["answer"]
    assert "secret-value" not in payload["details"]
    assert "secret-value" not in json.dumps(conversation)


def test_yoagent_send_refuses_when_existing_draft_does_not_clear(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    target = {
        "session": "1",
        "pane_target": "%1",
        "agent_kind": "claude",
        "agent_model": "opus",
        "agent_transcript": "/tmp/claude-session-1.jsonl",
        "transport": "tmux-legacy",
        "transport_label": "legacy tmux pane paste + Return",
        "transport_kind": "terminal",
        "prompt": {},
        "screen": {"key": "input-draft", "text": "target input box already contains unsent text", "detected_text": "old draft"},
    }
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "yoagent_clear_target_composer", lambda *_args, **_kwargs: {
        "ok": False,
        "cleared": False,
        "detected_text": "old draft",
        "remaining_text": "old draft",
        "error": "target input box did not clear",
    })
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("uncleared draft must not receive paste")))

    try:
        preview, preview_status = webapp.create_yoagent_action_preview({"type": "send_prompt", "session": "1", "text": "what time is it?"})
        result, status = webapp.execute_yoagent_send_action({"preview_id": preview["id"]})
    finally:
        webapp.control_server.stop()

    assert preview_status == HTTPStatus.OK
    assert preview["status"] == "ready"
    assert status == HTTPStatus.CONFLICT
    assert result["sent"] is False
    assert result["cleared_input"] is False
    assert result["cleared_text_preview"] == "old draft"
    assert "did not clear" in result["error"]


def test_yoagent_composer_text_ignores_completed_prompt_history():
    visible_text = "\n".join([
        "❯ what time it is",
        "",
        "  Ran 1 shell command",
        "",
        "● It's 11:17 PM PDT (2026-06-13 Sat, 23:17).",
        "",
        "✻ Sautéed for 12s",
        "                                                             new task? /clear to save 967.7k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ ",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    webapp = app_module.TmuxWebtermApp(["2"])
    try:
        assert webapp.yoagent_visible_composer_text(visible_text) == ""
    finally:
        webapp.control_server.stop()


def test_yoagent_composer_text_ignores_submitted_queue_above_blank_prompt():
    visible_text = "\n".join([
        "❯ Queue: change background to white and document agent handoffs",
        "",
        "● Please run /login · API Error: 401 Invalid authentication credentials",
        "",
        "✻ Crunched for 4s · 1 shell still running",
        "                                          new task? /clear to save 328.2k tokens · ◎ /goal active (1d)",
        "────────────────────────────────────────────────────────────────",
        "❯ ",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ accept edits on · 1 shell · ← for agents · ↓ to manage",
    ])
    webapp = app_module.TmuxWebtermApp(["1"])
    try:
        assert webapp.yoagent_visible_composer_text(visible_text) == ""
    finally:
        webapp.control_server.stop()


def test_yoagent_composer_text_ignores_submitted_prompt_waiting_for_output():
    visible_text = "\n".join([
        "Earlier assistant output.",
        "",
        "❯ what time it is",
        "",
    ])
    webapp = app_module.TmuxWebtermApp(["2"])
    try:
        assert webapp.yoagent_visible_composer_text(visible_text) == ""
    finally:
        webapp.control_server.stop()


def test_yoagent_composer_text_keeps_real_multiline_draft():
    visible_text = "\n".join([
        "new task? /clear to save 193.6k tokens",
        "────────────────────────────────────────────────────────────────",
        "❯ Use this context:",
        "",
        "  It's 11:17 PM PDT.",
        "",
        "  Task: add 10 minutes and say if that is right.",
        "────────────────────────────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])
    webapp = app_module.TmuxWebtermApp(["1"])
    try:
        assert webapp.yoagent_visible_composer_text(visible_text) == "Use this context: It's 11:17 PM PDT. Task: add 10 minutes and say if that is right."
    finally:
        webapp.control_server.stop()


def test_yoagent_composer_text_keeps_codex_bottom_draft():
    visible_text = "\n".join([
        "• Wrote /tmp/hangman.py and verified it.",
        "",
        "› Write tests for @filename",
        "",
        "  gpt-5.5 medium · ~",
    ])
    webapp = app_module.TmuxWebtermApp(["9"])
    try:
        assert webapp.yoagent_visible_composer_text(visible_text) == "Write tests for @filename"
    finally:
        webapp.control_server.stop()


def test_yoagent_chat_preview_only_when_confirmation_requested(monkeypatch):
    pane = PaneInfo(
        session="6",
        window="0",
        window_name="codex",
        pane="0",
        pane_id="%6",
        target="%6",
        current_path="/repo/app",
        command="codex",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="6",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="6",
                kind="codex",
                pid=123,
                pane_target="%6",
                command="codex",
                cwd="/repo/app",
                status=None,
                session_id="codex-session-6",
                transcript="/tmp/codex-session-6.jsonl",
                error=None,
                model="gpt-5",
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["6"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"6": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "idle", "text": ""}))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-06-13T17:40:00+00:00",
        "session_order": ["6"],
        "global": {"headline": "Session 6 is idle."},
        "sessions": {"6": {"local": "Codex session 6 is idle in /repo/app."}},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("confirmation request must not auto-send")))

    try:
        payload, status = webapp.yoagent_chat({"message": "send `date` to tmux session 6, ask me before"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "yolomux"
    assert "confirmed send action" in payload["answer"]
    assert len(payload["actions"]) == 1
    assert payload["actions"][0]["requires_confirmation"] is True
    assert payload["actions"][0]["status"] == "ready"
    assert payload["actions"][0]["target"]["agent_kind"] == "codex"


def test_yoagent_chat_does_not_send_when_target_agent_is_working(monkeypatch):
    pane = PaneInfo(
        session="6",
        window="0",
        window_name="claude",
        pane="0",
        pane_id="%6",
        target="%6",
        current_path="/repo/app",
        command="claude",
        active=True,
        window_active=True,
        title="",
        pid=123,
    )
    info = SessionInfo(
        session="6",
        panes=[pane],
        selected_pane=pane,
        agents=[
            AgentInfo(
                session="6",
                kind="claude",
                pid=123,
                pane_target="%6",
                command="claude",
                cwd="/repo/app",
                status=None,
                session_id="claude-session-6",
                transcript="/tmp/claude-session-6.jsonl",
                error=None,
            )
        ],
    )
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(app_module, "list_tmux_session_names", lambda: (["6"], None))
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({"6": info}, []))
    monkeypatch.setattr(webapp, "yoagent_action_pane_status", lambda session, target, **_kwargs: ({}, {"key": "working", "text": "agent is working"}))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-06-13T17:40:00+00:00",
        "session_order": ["6"],
        "global": {"headline": "Session 6 is working."},
        "sessions": {"6": {"local": "Claude session 6 is working in /repo/app."}},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("working target must not receive paste")))

    try:
        payload, status = webapp.yoagent_chat({"message": "tell session 6 to run date"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "yolomux"
    assert "still working" in payload["answer"]
    assert payload["actions"] == []


def install_fake_yolomux_state(monkeypatch):
    state = {}
    monkeypatch.setattr(app_module, "read_yolomux_state", lambda: dict(state))
    monkeypatch.setattr(app_module, "update_yolomux_state", lambda updates: state.update(updates))
    return state


def test_yoagent_notify_job_create_dedupe_and_cancel(monkeypatch):
    state = install_fake_yolomux_state(monkeypatch)
    events = []
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        payload, status = webapp.create_yoagent_job({"type": "notify_session_idle", "session": "6", "quiet_seconds": 0})
        duplicate, duplicate_status = webapp.create_yoagent_job({"type": "notify_session_idle", "session": "6", "quiet_seconds": 0})
        jobs, jobs_status = webapp.yoagent_jobs_payload()
        cancelled, cancel_status = webapp.cancel_yoagent_job(payload["job"]["id"])
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert duplicate_status == HTTPStatus.CONFLICT
    assert duplicate["duplicate"] is True
    assert jobs_status == HTTPStatus.OK
    assert len(jobs["jobs"]) == 1
    assert cancel_status == HTTPStatus.OK
    assert cancelled["job"]["status"] == "cancelled"
    assert state[app_module.YOAGENT_JOBS_STATE_KEY][payload["job"]["id"]]["status"] == "cancelled"
    assert any(item[0] == "yoagent_jobs_changed" for item in events)


def test_yoagent_wait_then_send_job_fires_when_target_accepts(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    events = []
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "codex",
        "agent_model": "gpt-5",
        "agent_transcript": "/tmp/codex-session-6.jsonl",
        "transport": "pane-paste",
        "prompt": {},
        "screen": {"key": "idle", "text": ""},
    }
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "execute_yoagent_send_action", lambda payload, **_kwargs: ({
        "ok": True,
        "preview_id": payload["preview_id"],
        "transport": "tmux-legacy",
        "result_source": "transcript-or-screen",
        "result_marker": {"transcript": "/tmp/codex-session-6.jsonl", "size": 10},
    }, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        payload, status = webapp.create_yoagent_job({"type": "wait_then_send", "session": "6", "text": "date", "quiet_seconds": 0})
        fired = webapp.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["job"]["job_id"] == payload["job"]["id"]
    assert payload["job"]["prompt"] == "date"
    assert payload["job"]["prompt_preview"] == "date"
    assert payload["job"]["public_text"] == "date"
    assert payload["job"]["transport"] == ""
    assert payload["job"]["result_marker"] == {}
    assert payload["job"]["result_source"] == ""
    assert fired == [payload["job"]["id"]]
    assert jobs["jobs"][0]["status"] == "fired"
    assert jobs["jobs"][0]["started_at"]
    assert jobs["jobs"][0]["transport"] == "tmux-legacy"
    assert jobs["jobs"][0]["result_source"] == "transcript-or-screen"
    assert jobs["jobs"][0]["result_marker"] == {"transcript": "/tmp/codex-session-6.jsonl", "size": 10}
    assert jobs["jobs"][0]["result"]["send"]["ok"] is True
    assert any(item[0] == "yoagent_jobs_changed" and item[1].get("reason") == "yoagent_job_fired" for item in events)


def test_yoagent_risky_chat_send_requires_preview_confirmation_and_redacts_secret(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "codex",
        "agent_model": "gpt-5",
        "agent_transcript": "/tmp/codex-session-6.jsonl",
        "transport": "pane-paste",
        "prompt": {},
        "screen": {"key": "idle", "text": ""},
    }
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {"generated_at": "now", "session_order": ["6"], "global": {"headline": "Session 6 is idle."}, "sessions": {}, "errors": []})
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("risky target text must wait for confirmation")))

    try:
        payload, status = webapp.yoagent_chat({"message": "tell session 6 to run token=super-secret-value"})
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["actions"]
    assert payload["actions"][0]["requires_confirmation"] is True
    assert payload["actions"][0]["risk_labels"] == ["secret-like-text"]
    assert payload["actions"][0]["text"] == "token=<redacted>"
    assert "super-secret-value" not in payload["answer"]
    assert "super-secret-value" not in json.dumps(conversation)


def test_yoagent_risky_wait_then_send_job_starts_pending_confirmation_and_redacts(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})

    try:
        payload, status = webapp.create_yoagent_job({"type": "wait_then_send", "session": "6", "text": "api_key=super-secret-value", "quiet_seconds": 0})
        fired = webapp.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["job"]["status"] == "pending_confirmation"
    assert payload["job"]["confirm_required"] is True
    assert payload["job"]["action"]["risk_labels"] == ["secret-like-text"]
    assert payload["job"]["action"]["text"] == "api_key=<redacted>"
    assert payload["job"]["prompt"] == "api_key=<redacted>"
    assert payload["job"]["public_text"] == "api_key=<redacted>"
    assert fired == []
    assert jobs["jobs"][0]["status"] == "pending_confirmation"
    assert "super-secret-value" not in json.dumps(jobs)


def test_yoagent_notify_all_idle_job_tracks_blockers_then_fires(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    events = []
    webapp = app_module.TmuxWebtermApp(["1", "2"])
    states = {"1": "idle", "2": "working"}

    def target(session):
        return {
            "session": session,
            "pane_target": f"%{session}",
            "agent_kind": "codex",
            "transport": "pane-paste",
            "prompt": {},
            "screen": {"key": states[session], "text": states[session]},
        }, HTTPStatus.OK

    monkeypatch.setattr(webapp, "yoagent_action_target", target)
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        payload, status = webapp.create_yoagent_job({"type": "notify_all_idle", "quiet_seconds": 0})
        first = webapp.poll_yoagent_jobs_once()
        waiting, _waiting_status = webapp.yoagent_jobs_payload()
        states["2"] = "idle"
        second = webapp.poll_yoagent_jobs_once()
        fired, _fired_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["job"]["target"]["roster"] == ["1", "2"]
    assert first == []
    assert waiting["jobs"][0]["last_observed_state"]["blockers"] == ["2"]
    assert waiting["jobs"][0]["last_observed_state"]["states"] == {"1": "idle", "2": "working"}
    assert second == [payload["job"]["id"]]
    assert fired["jobs"][0]["status"] == "fired"
    assert any(item[0] == "yoagent_jobs_changed" and item[1].get("notification", {}).get("body") == "all watched tmux sessions are idle" for item in events)


def test_yoagent_jobs_reload_from_persisted_state(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    first_app = app_module.TmuxWebtermApp(["6"])
    second_app = None
    monkeypatch.setattr(first_app, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(first_app, "publish_client_event", lambda *args, **kwargs: {})

    try:
        payload, status = first_app.create_yoagent_job({"type": "notify_session_idle", "session": "6"})
        second_app = app_module.TmuxWebtermApp(["6"])
        jobs, jobs_status = second_app.yoagent_jobs_payload()
    finally:
        first_app.control_server.stop()
        if second_app is not None:
            second_app.control_server.stop()

    assert status == HTTPStatus.OK
    assert jobs_status == HTTPStatus.OK
    assert jobs["jobs"][0]["id"] == payload["job"]["id"]
    assert jobs["jobs"][0]["status"] == "queued"


def test_yoagent_job_fails_and_notifies_when_target_disappears(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    events = []
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": f"event-{len(events)}"})
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type})

    try:
        payload, status = webapp.create_yoagent_job({"type": "wait_then_send", "session": "6", "text": "date", "quiet_seconds": 0})
        monkeypatch.setattr(webapp, "yoagent_action_target", lambda session: ({"error": "unknown session: 6"}, HTTPStatus.NOT_FOUND))
        fired = webapp.poll_yoagent_jobs_once()
        jobs, _jobs_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert fired == []
    assert jobs["jobs"][0]["id"] == payload["job"]["id"]
    assert jobs["jobs"][0]["status"] == "failed"
    assert jobs["jobs"][0]["result"]["error"] == "unknown session: 6"
    assert any(item[0] == "yoagent_jobs_changed" and item[1].get("reason") == "yoagent_job_failed" and item[1].get("notification") for item in events)


def test_yoagent_action_preview_blocks_approval_prompt(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "claude",
        "transport": "pane-paste",
        "prompt": {"visible": True, "type": "bash"},
        "screen": {"key": "idle", "text": ""},
    }
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})

    try:
        preview, status = webapp.create_yoagent_action_preview({"type": "send_prompt", "session": "6", "text": "date"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert preview["status"] == "waiting"
    assert preview["acceptance_text"] == "target agent is at an approval prompt, not a fresh AI prompt"


def test_yoagent_chat_wait_then_send_queues_job_when_target_is_working(monkeypatch):
    install_fake_yolomux_state(monkeypatch)
    webapp = app_module.TmuxWebtermApp(["6"])
    target = {
        "session": "6",
        "pane_target": "%6",
        "agent_kind": "codex",
        "agent_model": "gpt-5",
        "agent_transcript": "/tmp/codex-session-6.jsonl",
        "transport": "pane-paste",
        "prompt": {},
        "screen": {"key": "working", "text": "working"},
    }
    monkeypatch.setattr(webapp, "yoagent_action_target", lambda session: (target, HTTPStatus.OK))
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {"generated_at": "now", "session_order": ["6"], "global": {"headline": "Session 6 is working."}, "sessions": {}, "errors": []})
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {"time": "event"})
    monkeypatch.setattr(app_module, "tmux_paste_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("queued job must not paste now")))

    try:
        payload, status = webapp.yoagent_chat({"message": "wait for session 6 to finish, then tell it to run date"})
        jobs, _jobs_status = webapp.yoagent_jobs_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend_used"] == "yolomux"
    assert "created yo!agent job" in payload["answer"].lower()
    assert len(jobs["jobs"]) == 1
    assert jobs["jobs"][0]["type"] == "wait_then_send"
    assert jobs["jobs"][0]["action"]["text"] == "date"


def test_yoagent_capability_question_is_grounded_and_readonly(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "capabilities": app_module.yoagent_capabilities_payload(),
        "sessions": {},
        "errors": [],
    })
    try:
        payload, status = webapp.yoagent_chat({"message": "Can YO!agent read, poll, monitor, notify, and send commands to tmux panes?"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert "can read tmux panes" in payload["answer"]
    assert "poll live session state" in payload["answer"]
    assert "notify when configured transitions" in payload["answer"]
    assert "send explicit target-session requests" in payload["answer"]
    assert "must not ask one target session to contact another directly" in payload["answer"]
    assert "~/.config/yolomux/skills.d/" in payload["answer"]
    assert "verified against a live Claude/Codex prompt" in payload["answer"]
    assert any("capability: YOLOmux can read tmux panes" in line for line in payload["context_lines"])
    assert any("YO!agent can execute explicit target-session sends" in line for line in payload["context_lines"])
    assert any("preserves perspectives" in line and "ask agent 1 to <do ...>" in line for line in payload["context_lines"])
    assert any("background-watches the target transcript" in line for line in payload["context_lines"])
    assert any("manage_user_skills" not in line and "~/.config/yolomux/skills.d/" in line for line in payload["context_lines"])


def test_yoagent_chat_can_update_user_skill_files(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "capabilities": app_module.yoagent_capabilities_payload(),
        "sessions": {},
        "errors": [],
    })
    writes = []

    def fake_write_user_skill_file(kind, name, text):
        writes.append((kind, name, text))
        return {
            "kind": kind,
            "name": name,
            "path": f"/tmp/yolomux/{kind}s.d/{name}.yaml",
            "text": text,
            "valid": True,
        }

    monkeypatch.setattr(app_module, "write_user_skill_file", fake_write_user_skill_file)
    monkeypatch.setattr(webapp, "yoagent_skills_payload", lambda: {"skills": []})
    monkeypatch.setattr(webapp, "publish_client_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(webapp, "log_event", lambda *args, **kwargs: {})
    try:
        payload, status = webapp.yoagent_chat({"message": "create skill local-checks description: Ask idle agents to run focused tests."})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert writes == [("skill", "local-checks", "name: local-checks\nkind: workflow\ndescription: Ask idle agents to run focused tests.\nconfirmation: none")]
    assert "Updated user-local `skill` `local-checks`" in payload["answer"]
    assert "/tmp/yolomux/skills.d/local-checks.yaml" in payload["answer"]


def test_yoagent_cli_auth_failure_is_actionable(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "claude", "invocation": "cli"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "sessions": {},
        "errors": [],
    })
    monkeypatch.setattr(webapp, "run_yoagent_claude_cli", lambda prompt, session_id="", resume=False, **_kwargs: ("", "Error: not logged in. Run claude login."))
    try:
        payload, status = webapp.yoagent_chat({"message": "status?"})
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["backend"] == "claude"
    assert payload["backend_used"] == "deterministic"
    assert payload["fallback"] is True
    assert "Claude CLI is not logged in" in payload["fallback_reason"]
    assert "claude auth login" in payload["fallback_reason"]


def test_yoagent_cli_fallback_keeps_non_auth_error():
    reason = app_module.yoagent_cli_fallback_reason("codex", "model overloaded")
    assert reason == "model overloaded"


def test_resolve_yoagent_backend_auto_prefers_codex_then_claude(monkeypatch):
    # #41: auto resolves to codex first, then claude, then deterministic — only for installed AND
    # logged-in agents. Explicit choices pass through untouched.
    def status(claude_in, codex_in):
        return lambda *a, **k: {
            "claude": {"installed": True, "logged_in": claude_in},
            "codex": {"installed": True, "logged_in": codex_in},
        }

    monkeypatch.setattr(app_module, "agent_auth_status", status(True, True))
    assert app_module.resolve_yoagent_backend("auto") == "codex"
    monkeypatch.setattr(app_module, "agent_auth_status", status(True, False))
    assert app_module.resolve_yoagent_backend("auto") == "claude"
    monkeypatch.setattr(app_module, "agent_auth_status", status(False, False))
    assert app_module.resolve_yoagent_backend("auto") == "deterministic"
    # an installed-but-logged-out codex is skipped in favor of a logged-in claude
    monkeypatch.setattr(app_module, "agent_auth_status", status(True, False))
    assert app_module.resolve_yoagent_backend("auto") == "claude"
    # explicit selections are never auto-resolved
    monkeypatch.setattr(app_module, "agent_auth_status", status(False, False))
    assert app_module.resolve_yoagent_backend("claude") == "claude"
    assert app_module.resolve_yoagent_backend("deterministic") == "deterministic"


def test_yoagent_language_directive_only_for_non_english_locales():
    # Phase 1: a non-English UI locale asks the LLM to answer in that language.
    assert app_module.yoagent_language_directive("zh-Hant") == "\n\n請用繁體中文回答。"
    assert app_module.yoagent_language_directive("zh-Hans") == "\n\n请用简体中文回答。"
    assert app_module.yoagent_language_directive("es") == "\n\nResponde en español."
    assert app_module.yoagent_language_directive("en") == ""
    assert app_module.yoagent_language_directive("en-XA") == ""
    assert app_module.yoagent_language_directive("system") == ""
    assert app_module.yoagent_language_directive("") == ""


def test_yoagent_chat_appends_language_directive_to_the_llm_prompt(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(app_module, "agent_auth_status", lambda *a, **k: {
        "claude": {"installed": True, "logged_in": False},
        "codex": {"installed": True, "logged_in": True},
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "auto", "invocation": "cli"})
    captured = {}

    def fake_codex(prompt, session_id="", resume=False, settings=None, stream_callback=None):
        captured["prompt"] = prompt
        return ("respuesta", "", "s1", {"transport": "codex-app-server", "persistent": True})
    monkeypatch.setattr(webapp, "run_yoagent_codex_app_server", fake_codex)
    try:
        payload, status = webapp.yoagent_chat({"message": "estado?", "locale": "zh-Hant"})
    finally:
        webapp.control_server.stop()
    assert status == HTTPStatus.OK
    assert "你是優!助手" in captured["prompt"]
    assert "優樂mux" in captured["prompt"]
    assert "You are YO!agent" not in captured["prompt"]
    assert "請用繁體中文回答。" in captured["prompt"]


def test_yoagent_chat_auto_runs_logged_in_agent(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(app_module, "agent_auth_status", lambda *a, **k: {
        "claude": {"installed": True, "logged_in": False},
        "codex": {"installed": True, "logged_in": True},
    })
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "auto", "invocation": "cli"})
    monkeypatch.setattr(webapp, "run_yoagent_codex_app_server", lambda prompt, session_id="", resume=False, settings=None, stream_callback=None: ("codex answer", "", "codex-session-1", {"transport": "codex-app-server", "persistent": True}))
    try:
        payload, status = webapp.yoagent_chat({"message": "status?"})
    finally:
        webapp.control_server.stop()
    assert payload["backend"] == "auto"
    assert payload["backend_used"] == "codex"
    assert payload["answer"] == "codex answer"


def test_yoagent_codex_backend_reuses_persistent_app_server(monkeypatch):
    messages = [
        {"jsonrpc": "2.0", "id": "initialize-1", "result": {}},
        {"jsonrpc": "2.0", "id": "thread-1", "result": {"thread": {"id": "thread-1"}}},
        {"jsonrpc": "2.0", "id": "turn-1", "result": {"turn": {"id": "turn-1", "items": [], "status": "inProgress"}}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "items": [{"type": "agentMessage", "id": "item-1", "text": "first answer"}], "status": "completed"}}},
        {"jsonrpc": "2.0", "id": "turn-2", "result": {"turn": {"id": "turn-2", "items": [], "status": "inProgress"}}},
        {"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": "thread-1", "turn": {"id": "turn-2", "items": [{"type": "agentMessage", "id": "item-2", "text": "second answer"}], "status": "completed"}}},
    ]
    fake_process = FakeCodexAppServerProcess(messages)
    calls = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return fake_process

    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Session 5 is editing YO!agent."},
        "sessions": {},
        "errors": [],
    }
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(app_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    monkeypatch.setattr(transport_module.subprocess, "Popen", fake_popen)
    try:
        settings = {"codex_model": "gpt-5.3-codex-spark", "codex_effort": "low"}
        first, first_reason, first_status = webapp.run_yoagent_cli_backend("codex", "first?", activity, settings, [])
        second, second_reason, second_status = webapp.run_yoagent_cli_backend("codex", "second?", activity, settings, [{"role": "user", "content": "first?"}])
        terminated_before_shutdown = fake_process.terminated
    finally:
        webapp.stop_auto_approve_all()

    assert first == "first answer"
    assert second == "second answer"
    assert first_reason == ""
    assert second_reason == ""
    assert len(calls) == 1
    assert calls[0][0][:4] == ["codex", "app-server", "--listen", "stdio://"]
    assert 'model_reasoning_effort="low"' in calls[0][0]
    assert 'service_tier="fast"' in calls[0][0]
    assert first_status["transport"] == "codex-app-server"
    assert first_status["persistent"] is True
    assert first_status["process_started"] is True
    assert first_status["thread_started"] is True
    assert second_status["process_reused"] is True
    assert second_status["thread_started"] is False
    assert first_status["session_id"] == "thread-1"
    assert second_status["session_id"] == "thread-1"
    assert webapp.yoagent_cli_sessions["codex"]["session_id"] == "thread-1"
    methods = [message["method"] for message in fake_process.stdin.messages]
    assert methods == ["initialize", "initialized", "thread/start", "turn/start", "turn/start"]
    assert fake_process.stdin.messages[2]["params"]["model"] == "gpt-5.3-codex-spark"
    assert "first?" in fake_process.stdin.messages[3]["params"]["input"][0]["text"]
    assert "second?" in fake_process.stdin.messages[4]["params"]["input"][0]["text"]
    assert terminated_before_shutdown is False
    assert fake_process.terminated is True


def test_yoagent_codex_backend_falls_back_to_exec_when_app_server_fails(monkeypatch):
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Session 5 is editing YO!agent."},
        "sessions": {},
        "errors": [],
    }
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(app_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    monkeypatch.setattr(transport_module.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("app-server failed")))
    monkeypatch.setattr(webapp, "run_yoagent_codex_cli", lambda prompt, session_id="", resume=False, **_kwargs: ("exec fallback answer", "", "exec-thread"))
    try:
        answer, reason, status = webapp.run_yoagent_cli_backend("codex", "status?", activity, {"codex_model": "gpt-5.3-codex-spark", "codex_effort": "low"}, [])
    finally:
        webapp.stop_auto_approve_all()

    assert answer == "exec fallback answer"
    assert reason == ""
    assert status["transport"] == "codex-exec"
    assert status["persistent"] is False
    assert status["fallback_transport"] == "codex-exec"
    assert "app-server failed" in status["fast_backend_error"]
    assert status["session_id"] == "exec-thread"


def test_yoagent_permission_block_answer_is_preserved(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(webapp, "run_yoagent_claude_cli", lambda prompt, session_id="", resume=False, **_kwargs: ("I'm blocked — the harness denied access to ~/.claude/projects/**/*.jsonl.", ""))
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Your most recent work is about editor fixes."},
        "sessions": {},
        "errors": [],
    }
    try:
        answer, reason, status = webapp.run_yoagent_cli_backend("claude", "status?", activity, {}, [])
    finally:
        webapp.control_server.stop()

    assert answer == "I'm blocked — the harness denied access to ~/.claude/projects/**/*.jsonl."
    assert reason == ""
    assert status["backend"] == "claude"


def test_reset_yoagent_chat_clears_cli_sessions():
    webapp = app_module.TmuxWebtermApp(["5"])
    try:
        webapp.yoagent_cli_sessions["claude"] = {"session_id": "old"}
        assert webapp.reset_yoagent_chat()["ok"] is True
        assert webapp.yoagent_cli_sessions == {}
    finally:
        webapp.control_server.stop()


def test_yoagent_chat_persists_conversation_until_reset(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "deterministic", "invocation": "cli"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Session 5 is editing YO!agent."},
        "sessions": {"5": {"local": "Codex session 5 is editing YO!agent."}},
        "errors": [],
    })
    try:
        payload, status = webapp.yoagent_chat({"message": "what changed?"})
        persisted = webapp.yoagent_conversation_payload()
        reset = webapp.reset_yoagent_chat()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert [item["role"] for item in payload["conversation"]["messages"]] == ["user", "assistant"]
    assert payload["conversation"]["messages"][0]["content"] == "what changed?"
    assert persisted["messages"] == payload["conversation"]["messages"]
    assert persisted["transcript_path"].endswith("conversation.jsonl")
    assert reset["conversation"]["messages"] == []


def test_yoagent_visible_prewarm_persists_startup_response(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    webapp.warm_metadata_cache_async = lambda sessions: None
    events = []
    monkeypatch.setattr(webapp, "publish_client_event", lambda event_type, payload=None, **_kwargs: events.append((event_type, payload or {})) or {"type": event_type, "payload": payload or {}})
    monkeypatch.setattr(webapp, "yoagent_settings", lambda: {"backend": "codex", "invocation": "cli", "codex_model": "gpt-5.3-codex-spark"})
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda: {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Session 5 is editing YO!agent."},
        "sessions": {"5": {"local": "Codex session 5 is editing YO!agent."}},
        "errors": [],
    })
    calls = []

    def fake_backend(backend, question, activity_payload, settings, history, locale="en", stream_id=""):
        calls.append((backend, question, stream_id, activity_payload, settings, history, locale))
        return "Start with the YO!agent streaming fix.", "", {"transport": "codex-app-server", "persistent": True, "elapsed_ms": 12, "prompt_chars": 345}

    monkeypatch.setattr(webapp, "run_yoagent_cli_backend", fake_backend)
    try:
        payload, status = webapp.yoagent_prewarm({"visible": True, "locale": "en"})
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["visible"] is True
    assert payload["answer"] == "Start with the YO!agent streaming fix."
    assert payload["stream_id"].startswith("startup-")
    assert calls and calls[0][0] == "codex"
    assert calls[0][1] == app_module.YOAGENT_STARTUP_QUESTION
    assert calls[0][2] == payload["stream_id"]
    assert [message["role"] for message in conversation["messages"]] == ["assistant"]
    assert conversation["messages"][0]["content"] == "Start with the YO!agent streaming fix."
    assert "model CLI time" in conversation["messages"][0]["details"]
    assert any(event_type == "yoagent_stream_delta" for event_type, _payload in events)
    assert any(event_type == "yoagent_conversation_changed" for event_type, _payload in events)


def test_yoagent_stream_hidden_thinking_is_not_exposed():
    visible, hidden = app_module.strip_yoagent_stream_hidden_thinking("<think>private reasoning")
    assert visible == ""
    assert hidden is True

    visible, hidden = app_module.strip_yoagent_stream_hidden_thinking("<think>private</think>Final answer")
    assert visible == "Final answer"
    assert hidden is True


def test_yoagent_cli_sessions_persist_across_restart(monkeypatch):
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "Session 5 is editing YO!agent."},
        "sessions": {},
        "errors": [],
    }
    first_app = app_module.TmuxWebtermApp(["5"])
    monkeypatch.setattr(first_app, "run_yoagent_claude_cli", lambda prompt, session_id="", resume=False, **_kwargs: ("answer", ""))
    try:
        answer, reason, status = first_app.run_yoagent_cli_backend("claude", "status?", activity, {}, [])
        session_id = status["session_id"]
    finally:
        first_app.control_server.stop()

    second_app = app_module.TmuxWebtermApp(["5"])
    try:
        loaded = second_app.yoagent_cli_sessions.get("claude", {})
    finally:
        second_app.control_server.stop()

    assert answer == "answer"
    assert reason == ""
    assert session_id
    assert loaded["session_id"] == session_id


def test_yoagent_cli_backend_resumes_and_trims_context(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    calls = []

    def fake_claude(prompt, session_id="", resume=False, **kwargs):
        calls.append({"prompt": prompt, "session_id": session_id, "resume": resume, **kwargs})
        return ("seeded" if not resume else "resumed", "")

    monkeypatch.setattr(webapp, "run_yoagent_claude_cli", fake_claude)
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "sessions": {
            "5": {
                "agent": "codex",
                "agent_label": "Codex",
                "active": True,
                "repos": ["/repo/yolomux"],
                "files": {"count": 1, "added": 2, "removed": 0},
                "work": "editor fixes",
                "file_lines": ["M static/yolomux.js (+2/-0)"],
            }
        },
        "errors": [],
    }
    try:
        settings = {"claude_model": "claude-haiku-4-5", "claude_effort": "low"}
        first, first_reason, first_status = webapp.run_yoagent_cli_backend("claude", "first?", activity, settings, [])
        second, second_reason, second_status = webapp.run_yoagent_cli_backend("claude", "second?", activity, settings, [{"role": "user", "content": "first?"}])
    finally:
        webapp.control_server.stop()

    assert first == "seeded"
    assert first_reason == ""
    assert second == "resumed"
    assert second_reason == ""
    assert calls[0]["resume"] is False
    assert calls[1]["resume"] is True
    assert calls[0]["session_id"] == calls[1]["session_id"]
    assert calls[0]["model"] == "claude-haiku-4-5"
    assert calls[0]["effort"] == "low"
    assert calls[1]["effort"] == "low"
    assert first_status["seeded"] is True
    assert second_status["resumed"] is True
    assert second_status["prompt_chars"] < first_status["prompt_chars"]
    assert "Activity summary is unchanged" in calls[1]["prompt"]
    assert "M static/yolomux.js" not in calls[1]["prompt"]


def test_yoagent_cli_backend_does_not_hold_state_lock_during_cli(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    observed = []

    def fake_claude(_prompt, session_id="", resume=False, **_kwargs):
        def probe_lock():
            acquired = webapp.yoagent_cli_lock.acquire(timeout=0.1)
            observed.append(acquired)
            if acquired:
                webapp.yoagent_cli_lock.release()

        thread = threading.Thread(target=probe_lock)
        thread.start()
        thread.join()
        return ("answer", "")

    monkeypatch.setattr(webapp, "run_yoagent_claude_cli", fake_claude)
    activity = {
        "generated_at": "2026-05-31T00:00:00+00:00",
        "session_order": ["5"],
        "global": {"headline": "You have 1 AI agent working on editor fixes across yolomux."},
        "sessions": {},
        "errors": [],
    }
    try:
        answer, reason, status = webapp.run_yoagent_cli_backend("claude", "status?", activity, {}, [])
    finally:
        webapp.control_server.stop()

    assert answer == "answer"
    assert reason == ""
    assert observed == [True]
    assert status["backend"] == "claude"


def test_codex_event_session_id_extracts_common_shapes():
    assert app_module.codex_event_session_id({"type": "thread.started", "thread_id": "abc"}) == "abc"
    assert app_module.codex_event_session_id({"thread": {"id": "nested"}}) == "nested"


def test_yoagent_codex_cli_persists_then_resumes(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["5"])
    calls = []

    def fake_run(args, input, cwd, env, text, capture_output, timeout, check):
        calls.append(args)
        stdout = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "codex-session"}),
            json.dumps({"type": "agent_message", "text": "answer"}),
        ])
        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    monkeypatch.setattr(app_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    try:
        settings = {"codex_model": "gpt-5.4-mini", "codex_effort": "low"}
        first_answer, first_error, first_session = webapp.run_yoagent_codex_cli("first", resume=False, settings=settings)
        second_answer, second_error, second_session = webapp.run_yoagent_codex_cli("second", session_id=first_session, resume=True, settings=settings)
    finally:
        webapp.control_server.stop()

    assert first_answer == "answer"
    assert first_error == ""
    assert first_session == "codex-session"
    assert second_answer == "answer"
    assert second_error == ""
    assert second_session == "codex-session"
    assert calls[0][:3] == ["codex", "exec", "--json"]
    assert calls[0][calls[0].index("-m") + 1] == "gpt-5.4-mini"
    assert 'model_reasoning_effort="low"' in calls[0]
    assert 'service_tier="fast"' in calls[0]
    assert "--ephemeral" not in calls[0]
    assert "--sandbox" in calls[0]
    assert calls[1][:4] == ["codex", "exec", "resume", "--json"]
    assert "codex-session" in calls[1]
    assert calls[0][calls[0].index("--sandbox") + 1] == "read-only"
    # `codex exec resume` rejects --sandbox/--cd (it restores the original session's cwd + sandbox), so
    # the resume call must NOT pass them — passing them raised "unexpected argument '--sandbox'".
    assert "--sandbox" not in calls[1]
    assert "--cd" not in calls[1]


def test_watched_prs_payload_shapes_result_and_logs_truncation_once(monkeypatch):
    # watched_prs_payload returns {watched_prs, truncated, invalid}.
    # the cap is logged only when the capped state CHANGES — not on every poll.
    monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ({}, []))
    webapp = app_module.TmuxWebtermApp([])
    truncated_box = {"n": 3}
    monkeypatch.setattr(
        app_module,
        "watched_pr_metadata",
        lambda refs, cache, allow_network=True: {
            "watched_prs": [{"ref": "o/r#1", "url": "u", "number": 1, "status_label": "open"}],
            "truncated": truncated_box["n"],
            "invalid": ["bad"],
        },
    )
    events = []
    monkeypatch.setattr(webapp, "log_event", lambda *a, **k: events.append(a))

    payload = webapp.watched_prs_payload(allow_network=False)
    assert payload["watched_prs"][0]["ref"] == "o/r#1"
    assert payload["truncated"] == 3
    assert payload["invalid"] == ["bad"]
    assert "refresh_ms" not in payload
    truncation_events = lambda: [a for a in events if "watched_pr_truncated" in str(a)]
    assert len(truncation_events()) == 1, "logs the truncation on first cap"

    # A second poll with the SAME capped state does NOT log again.
    webapp.watched_prs_payload(allow_network=False)
    assert len(truncation_events()) == 1, "does not re-log an unchanged capped state every poll"

    # A changed truncation count logs a new event.
    truncated_box["n"] = 5
    webapp.watched_prs_payload(allow_network=False)
    assert len(truncation_events()) == 2, "a changed capped state logs again"


def test_apply_upload_subdir_defaults_to_dot_uploads(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"subdir": ".uploads"}}})
        target = webapp._apply_upload_subdir(tmp_path)
        assert target == tmp_path / ".uploads"
        assert target.is_dir()
    finally:
        webapp.control_server.stop()


def test_apply_upload_subdir_empty_writes_into_cwd(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"subdir": ""}}})
        assert webapp._apply_upload_subdir(tmp_path) == tmp_path
    finally:
        webapp.control_server.stop()


def test_apply_upload_subdir_rejects_escaping_value(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"subdir": "../escape"}}})
        assert webapp._apply_upload_subdir(tmp_path) == tmp_path
        assert not (tmp_path.parent / "escape").exists()
    finally:
        webapp.control_server.stop()


def test_apply_upload_subdir_falls_back_when_uncreatable(monkeypatch, tmp_path):
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"uploads": {"subdir": ".uploads"}}})
        base = tmp_path / "afile"
        base.write_text("not a dir", encoding="utf-8")
        assert webapp._apply_upload_subdir(base) == base
    finally:
        webapp.control_server.stop()


def test_self_update_dryrun_is_noop_with_plan():
    webapp = app_module.TmuxWebtermApp(["1"])
    result = webapp.perform_self_update(dryrun=True)
    assert result["ok"] is True
    assert result["dryrun"] is True
    assert result["restarting"] is False
    assert any("git pull" in step for step in result["plan"])


def _fake_update_git(remote_version="0.3.25", remote_sha="remoteabcdef1"):
    def fake_git(args, cwd, timeout=3.0):
        assert cwd == "/repo"
        if args == ["fetch", "--quiet", "origin", "main"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args == ["rev-parse", "--short=12", "origin/main"]:
            return SimpleNamespace(returncode=0, stdout=f"{remote_sha}\n", stderr="")
        if args == ["show", "origin/main:yolomux_lib/common.py"]:
            return SimpleNamespace(returncode=0, stdout=f'YOLOMUX_VERSION = "{remote_version}"\n', stderr="")
        raise AssertionError(f"unexpected git args: {args}")
    return fake_git


def test_update_check_status_ignores_sha_only_changes(monkeypatch):
    monkeypatch.setattr(app_module.common, "YOLOMUX_VERSION", "0.3.25")
    monkeypatch.setattr(app_module.common, "yolomux_commit_sha", lambda: "localabcdef1")
    monkeypatch.setattr(app_module.common, "git_ahead_behind_counts", lambda cwd, left: (0, 1))
    monkeypatch.setattr(app_module.common, "git", _fake_update_git(remote_version="0.3.25"))

    status = app_module.common.update_check_status("/repo")

    assert status["available"] is False
    assert status["current"] == "0.3.25"
    assert status["target"] == "0.3.25"
    assert status["current_sha"] == "localabcdef1"
    assert status["target_sha"] == "remoteabcdef1"
    assert status["behind"] == 1


def test_update_check_status_reports_newer_version(monkeypatch):
    monkeypatch.setattr(app_module.common, "YOLOMUX_VERSION", "0.3.25")
    monkeypatch.setattr(app_module.common, "yolomux_commit_sha", lambda: "localabcdef1")
    monkeypatch.setattr(app_module.common, "git_ahead_behind_counts", lambda cwd, left: (0, 1))
    monkeypatch.setattr(app_module.common, "git", _fake_update_git(remote_version="0.3.26"))

    status = app_module.common.update_check_status("/repo")

    assert status["available"] is True
    assert status["current"] == "0.3.25"
    assert status["target"] == "0.3.26"
    assert status["target_version"] == "0.3.26"
    assert status["target_sha"] == "remoteabcdef1"


def test_update_status_dryrun_reports_available():
    webapp = app_module.TmuxWebtermApp(["1"])
    status = webapp.update_status_payload(dryrun=True)
    assert status["available"] is True
    assert status["target"] == "dryrun"
    assert status["dryrun"] is True
    assert status["enabled"] is False
    assert status["notify"] is True
    assert status["notify_level"] == "patch"
    assert status["version_change_level"] == "patch"


def test_update_status_notify_level_respects_semver_threshold(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    status_payload = {
        "available": True,
        "target": "abc123",
        "dryrun": False,
        "version_change_level": "patch",
    }
    monkeypatch.setattr(app_module.common, "update_check_status", lambda *_args, **_kwargs: dict(status_payload))

    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"updates": {"check_enabled": True, "notify_level": "minor"}}})
    assert webapp.update_status_payload()["notify"] is False

    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"updates": {"check_enabled": True, "notify_level": "patch"}}})
    assert webapp.update_status_payload()["notify"] is True

    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"updates": {"check_enabled": True, "notify_level": "none"}}})
    assert webapp.update_status_payload()["notify"] is False

    monkeypatch.setattr(app_module, "settings_payload", lambda: {"settings": {"updates": {"check_enabled": False, "notify_level": "patch"}}})
    assert webapp.update_status_payload()["notify"] is False


def test_version_change_level_classifies_semver_bumps():
    assert app_module.common.version_change_level("0.3.25", "0.3.26") == "patch"
    assert app_module.common.version_change_level("0.3.25", "0.4.0") == "minor"
    assert app_module.common.version_change_level("0.3.25", "1.0.0") == "major"
    assert app_module.common.version_change_level("0.3.25", "0.3.25") == "none"
    assert app_module.common.version_change_level("0.3.25", "not-a-version") == "none"
