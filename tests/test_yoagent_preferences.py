from http import HTTPStatus

import pytest

from yolomux_lib import app as app_module
from yolomux_lib.yoagent import controller as yoagent_controller_module
from yolomux_lib.settings import default_settings
from yolomux_lib.settings import save_settings
from yolomux_lib.settings import settings_payload
from yolomux_lib.yoagent.preferences import catalog_from_payload
from yolomux_lib.yoagent.preferences import product_capability_registry
from yolomux_lib.yoagent.preferences import product_capability_locale_key
from yolomux_lib.yoagent.preferences import yoagent_operator_response


pytestmark = pytest.mark.usefixtures("isolated_yoagent_conversation_state")


@pytest.fixture(autouse=True)
def no_control_socket(monkeypatch):
    monkeypatch.setattr(app_module.YolomuxControlServer, "start", lambda self: None)
    monkeypatch.setattr(app_module.YolomuxControlServer, "stop", lambda self: None)


def settings_payload_for_path(path):
    save_settings(default_settings(), path)
    return settings_payload(path)


def test_operator_answers_current_setting_without_model(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)
    calls = []

    response = yoagent_operator_response(
        "what is my tab width?",
        payload,
        {},
        "readonly",
        lambda patch: calls.append(patch) or payload,
    )

    assert response is not None
    assert response["backend_used"] == "yolomux"
    assert "`appearance.tab_width`" in response["answer"]
    assert "current `172`" in response["answer"]
    assert calls == []


def test_operator_writes_safe_admin_setting_through_callback(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)
    patches = []

    def callback(patch):
        patches.append(patch)
        return save_settings(patch, path)

    response = yoagent_operator_response("set theme to light", payload, {}, "admin", callback)

    assert response is not None
    assert patches == [{"appearance": {"theme": "light"}}]
    assert "`appearance.theme`" in response["answer"]
    assert "| `appearance.theme` | `dark` | `light` | Preferences -> Appearance | `live` |" in response["answer"]
    assert settings_payload(path)["settings"]["appearance"]["theme"] == "light"


def test_operator_writes_background_white_as_light_theme(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)
    patches = []

    def callback(patch):
        patches.append(patch)
        return save_settings(patch, path)

    response = yoagent_operator_response("change background to white", payload, {}, "admin", callback)

    assert response is not None
    assert patches == [{"appearance": {"theme": "light"}}]
    assert "`appearance.theme`" in response["answer"]
    assert "| `appearance.theme` | `dark` | `light` | Preferences -> Appearance | `live` |" in response["answer"]
    assert settings_payload(path)["settings"]["appearance"]["theme"] == "light"


def test_operator_treats_bare_color_change_as_active_color(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)
    patches = []

    def callback(patch):
        patches.append(patch)
        return save_settings(patch, path)

    response = yoagent_operator_response("change color from green to orange", payload, {}, "admin", callback)

    assert response is not None
    assert patches == [{"appearance": {"active_color": "orange"}}]
    assert "`appearance.active_color`" in response["answer"]
    assert "| `appearance.active_color` | `green` | `orange` | Preferences -> Appearance | `live` |" in response["answer"]
    assert settings_payload(path)["settings"]["appearance"]["active_color"] == "orange"


def test_operator_named_color_setting_keeps_explicit_target(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)

    response = yoagent_operator_response(
        "change cursor color from yellow to orange",
        payload,
        {},
        "admin",
        lambda patch: save_settings(patch, path),
    )

    updated = settings_payload(path)["settings"]["appearance"]
    assert response is not None
    assert updated["editor_cursor_color"] == "orange"
    assert updated["active_color"] == "green"
    assert "`appearance.editor_cursor_color`" in response["answer"]


def test_operator_writes_language_name_alias(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)

    response = yoagent_operator_response(
        "change language to Japanese",
        payload,
        {},
        "admin",
        lambda patch: save_settings(patch, path),
    )

    assert response is not None
    assert settings_payload(path)["settings"]["general"]["language"] == "ja"
    assert "| `general.language` |" in response["answer"]


def test_operator_catalog_hides_reserved_and_legacy_settings(tmp_path):
    payload = settings_payload_for_path(tmp_path / "settings.yaml")
    catalog = catalog_from_payload(payload)

    assert "general.default_sessions" not in catalog
    assert "appearance.editor_color_scheme" not in catalog
    assert catalog["yoagent.invocation"]["choices"] == ["cli"]


def test_operator_catalog_exposes_shared_locale_keys(tmp_path):
    payload = settings_payload_for_path(tmp_path / "settings.yaml")
    catalog = catalog_from_payload(payload)
    theme = catalog["appearance.theme"]

    assert theme["locale_keys"] == {
        "description": "pref.appearance.theme.help",
        "label": "pref.appearance.theme.label",
    }
    assert theme["gui"]["section_locale_key"] == "pref.section.appearance"
    assert catalog["file_explorer.root_mode"]["gui"]["section_locale_key"] == "finder.label.finder"
    for path in [
        "general.default_sessions",
        "appearance.editor_color_scheme",
        "updates.check_interval_minutes",
        "share.view_fit",
        "summary.backend",
        "summary.codex_model",
        "summary.codex_effort",
        "summary.codex_service_tier",
        "summary.lookback_seconds",
        "summary.timeout_seconds",
    ]:
        assert payload["catalog"][path]["gui"]["visible"] is False


def test_operator_localizes_setting_section_and_help(tmp_path):
    payload = settings_payload_for_path(tmp_path / "settings.yaml")

    response = yoagent_operator_response(
        "what is my theme?",
        payload,
        {},
        "readonly",
        lambda patch: payload,
        "zh-Hans",
    )

    assert response is not None
    assert "偏好设置 -> 外观" in response["answer"]
    assert "菜单、窗格、文件浏览器" in response["answer"]
    assert "Preferences -> Appearance" not in response["answer"]
    assert "Theme for menus" not in response["answer"]


def test_operator_does_not_write_legacy_editor_scheme(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)
    calls = []

    response = yoagent_operator_response(
        "set appearance.editor_color_scheme to github-light",
        payload,
        {},
        "admin",
        lambda patch: calls.append(patch) or save_settings(patch, path),
    )

    assert response is not None
    assert "legacy compatibility" in response["answer"]
    assert "appearance.editor_light_color_scheme" in response["answer"]
    assert calls == []
    assert settings_payload(path)["settings"]["appearance"]["editor_color_scheme"] == "dark"


def test_operator_clamps_numeric_write_and_reports_range(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)

    response = yoagent_operator_response(
        "set tab width to 9999",
        payload,
        {},
        "admin",
        lambda patch: save_settings(patch, path),
    )

    assert response is not None
    assert settings_payload(path)["settings"]["appearance"]["tab_width"] == 420
    assert "allowed range `120` to `420` pixels" in response["answer"]


def test_operator_preserves_concurrent_settings_edits(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)
    save_settings({"appearance": {"editor_font_size": 19}}, path)

    response = yoagent_operator_response(
        "set theme to light",
        payload,
        {},
        "admin",
        lambda patch: save_settings(patch, path),
    )

    updated = settings_payload(path)["settings"]
    assert response is not None
    assert updated["appearance"]["theme"] == "light"
    assert updated["appearance"]["editor_font_size"] == 19


def test_operator_writes_notify_level_and_boolean(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)

    notify = yoagent_operator_response(
        "change notify level to none",
        payload,
        {},
        "admin",
        lambda patch: save_settings(patch, path),
    )
    next_payload = settings_payload(path)
    focus = yoagent_operator_response(
        "turn auto focus on",
        next_payload,
        {},
        "admin",
        lambda patch: save_settings(patch, path),
    )

    assert notify is not None and focus is not None
    updated = settings_payload(path)["settings"]
    assert updated["updates"]["notify_level"] == "none"
    assert updated["general"]["auto_focus"] is True


def test_operator_denies_readonly_setting_write(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)
    calls = []

    response = yoagent_operator_response(
        "set theme to light",
        payload,
        {},
        "readonly",
        lambda patch: calls.append(patch) or payload,
    )

    assert response is not None
    assert "requires an admin login" in response["answer"]
    assert calls == []


def test_operator_upload_retention_write_uses_save_path(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)

    response = yoagent_operator_response(
        "set upload retention to 14 days",
        payload,
        {},
        "admin",
        lambda patch: save_settings(patch, path),
    )

    assert response is not None
    assert settings_payload(path)["settings"]["uploads"]["retention_days"] == 14
    assert "Updated this Preference" in response["answer"]


def test_operator_adds_watched_pr_without_replacing_list(tmp_path):
    path = tmp_path / "settings.yaml"
    save_settings({"github": {"watched_prs": ["keivenchang/yolomux#1"]}}, path)
    payload = settings_payload(path)

    response = yoagent_operator_response(
        "add watched PR keivenchang/yolomux#72",
        payload,
        {},
        "admin",
        lambda patch: save_settings(patch, path),
    )

    assert response is not None
    assert settings_payload(path)["settings"]["github"]["watched_prs"] == [
        "keivenchang/yolomux#1",
        "keivenchang/yolomux#72",
    ]


def test_operator_reports_duplicate_watched_pr_noop(tmp_path):
    path = tmp_path / "settings.yaml"
    save_settings({"github": {"watched_prs": ["keivenchang/yolomux#72"]}}, path)
    payload = settings_payload(path)

    response = yoagent_operator_response(
        "add watched PR https://github.com/keivenchang/yolomux/pull/72",
        payload,
        {},
        "admin",
        lambda patch: save_settings(patch, path),
    )

    assert response is not None
    assert settings_payload(path)["settings"]["github"]["watched_prs"] == ["keivenchang/yolomux#72"]
    assert "already in `github.watched_prs`" in response["answer"]


def test_operator_adds_and_removes_non_path_list_items(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)

    add_transition = yoagent_operator_response(
        "add notification transition pr merged",
        payload,
        {},
        "admin",
        lambda patch: save_settings(patch, path),
    )
    after_add = settings_payload(path)
    remove_transition = yoagent_operator_response(
        "remove notification transition pr-merged",
        after_add,
        {},
        "admin",
        lambda patch: save_settings(patch, path),
    )
    add_image = yoagent_operator_response(
        "add image action describe",
        settings_payload(path),
        {},
        "admin",
        lambda patch: save_settings(patch, path),
    )

    assert add_transition is not None and remove_transition is not None and add_image is not None
    updated = settings_payload(path)["settings"]
    assert "pr-merged" not in updated["notifications"]["notify_transitions"]
    assert "Describe the image: ; describe what is shown in this image." in updated["uploads"]["image_action_order"]


def test_operator_rejects_sensitive_path_values(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)

    response = yoagent_operator_response(
        "confirm set yolo rule file to ~/.ssh/id_rsa",
        payload,
        {},
        "admin",
        lambda patch: save_settings(patch, path),
    )

    assert response is not None
    assert "credential-sensitive" in response["answer"]
    assert settings_payload(path)["settings"]["yolo"]["rule_file_path"] == "~/.config/yolomux/yolo-rules.yaml"


def test_operator_rejects_unsafe_absolute_config_path(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)

    response = yoagent_operator_response(
        "confirm set yolo rule file to /etc/yolomux-rules.yaml",
        payload,
        {},
        "admin",
        lambda patch: save_settings(patch, path),
    )

    assert response is not None
    assert "config-like setting" in response["answer"]
    assert settings_payload(path)["settings"]["yolo"]["rule_file_path"] == "~/.config/yolomux/yolo-rules.yaml"


def test_operator_clarifies_invalid_pr_and_broad_reset(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)

    invalid_pr = yoagent_operator_response("add watched PR nope", payload, {}, "admin", lambda patch: save_settings(patch, path))
    reset_all = yoagent_operator_response("reset all settings", payload, {}, "admin", lambda patch: save_settings(patch, path))

    assert invalid_pr is not None and "pull request" in invalid_pr["answer"]
    assert reset_all is not None and "Resetting all Preferences is broad" in reset_all["answer"]


def test_operator_answers_product_state_from_activity_cache(tmp_path):
    payload = settings_payload_for_path(tmp_path / "settings.yaml")
    activity = {
        "sessions": {
            "6": {
                "session": "6",
                "last_activity_ts": 200,
                "last_activity_text": "5 minutes ago",
                "repos": ["/repo/yolomux"],
                "work": "YO!agent settings operator",
                "status_text": "PR #72",
                "pr_number": 72,
                "file_lines": ["M yolomux_lib/settings.py (+10/-1)"],
            }
        },
        "agents": [],
    }

    response = yoagent_operator_response("what did I last work on?", payload, activity, "readonly", lambda patch: payload)

    assert response is not None
    assert "tmux session `6`" in response["answer"]
    assert "YO!agent settings operator" in response["answer"]
    assert "/repo/yolomux" in response["answer"]


def test_operator_answers_changed_files_pr_capabilities_and_session_reason(tmp_path):
    payload = settings_payload_for_path(tmp_path / "settings.yaml")
    activity = {
        "sessions": {
            "2": {
                "session": "2",
                "last_activity_ts": 300,
                "repos": ["/repo/yolomux"],
                "work": "fixing Preferences",
                "activity_label": "blocked",
                "status_text": "PR #72",
                "pr_number": 72,
                "file_lines": ["M README.md (+3/-1)", "M yolomux_lib/settings.py (+20/-2)"],
                "blockers": ["waiting for tests"],
            }
        },
        "agents": [],
    }

    changed = yoagent_operator_response("what changed in this repo?", payload, activity, "readonly", lambda patch: payload)
    pr = yoagent_operator_response("what PR was that?", payload, activity, "readonly", lambda patch: payload)
    why = yoagent_operator_response("why is session 2 behaving this way?", payload, activity, "readonly", lambda patch: payload)
    capabilities = yoagent_operator_response("what can I do from here?", payload, activity, "readonly", lambda patch: payload)

    assert changed is not None and "M README.md" in changed["answer"]
    assert pr is not None and "PR #72" in pr["answer"]
    assert why is not None and "blocked" in why["answer"] and "waiting for tests" in why["answer"]
    assert capabilities is not None and "settings_catalog" in capabilities["answer"]


def test_product_capability_registry_mentions_preferences_and_orchestration():
    registry = product_capability_registry()
    names = {item["name"] for item in registry}

    assert "Preferences" in names
    assert "Agent orchestration" in names
    preferences = next(item for item in registry if item["name"] == "Preferences")
    assert preferences["backing"] == "settings_catalog + TmuxWebtermApp.save_settings"
    assert "appearance.tab_width" in preferences["setting_keys"]

    by_key = {item["key"]: item for item in registry}
    for key in ["panesTabs", "finderDifferTabber", "uploads"]:
        assert product_capability_locale_key(by_key[key], "auth") == "yoagent.capability.auth.adminForWrites"
    assert product_capability_locale_key(preferences, "name") == "common.preferences"
    assert product_capability_locale_key(by_key["share"], "name") == "brand.share"
    assert product_capability_locale_key(by_key["recentWork"], "write") == "common.readOnly"
    assert product_capability_locale_key(by_key["orchestration"], "name") == "yoagent.capability.orchestration.name"


def test_app_yoagent_settings_question_skips_cli_backend(monkeypatch, tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(app_module, "settings_payload", lambda: payload)
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("settings questions must not build the activity summary")))
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_cli_backend", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model backend should not run")))
    try:
        response, status = webapp.yoagent_controller.yoagent_chat({"message": "what is my theme?"}, access_role="readonly")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert response["backend_used"] == "yolomux"
    assert "`appearance.theme`" in response["answer"]
    assert "ttfr_ms" in response["timing"]


def test_app_yoagent_missing_message_keeps_diagnostic_descriptor():
    webapp = app_module.TmuxWebtermApp([])
    try:
        response, status = webapp.yoagent_controller.yoagent_chat({"locale": "zh-Hans"}, access_role="readonly")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.BAD_REQUEST
    assert response["error"] == "missing YO!agent message"
    assert response["user_message"] == {
        "key": "yoagent.error.chatMessageRequired",
        "params": {},
        "fallback": "missing YO!agent message",
    }


def test_app_yoagent_action_details_use_locale_catalog(monkeypatch):
    calls = []

    def localized(locale, key, **_params):
        calls.append((locale, key))
        return f"{locale}:{key}"

    monkeypatch.setattr(yoagent_controller_module, "yoagent_text", localized)
    webapp = app_module.TmuxWebtermApp([])
    try:
        details = webapp.yoagent_controller.yoagent_action_preview_details(
            {
                "session": "1",
                "screen": {"key": "input-draft", "detected_text": "old command"},
                "acceptance_text": "target is busy",
            },
            "zh-Hans",
        )
    finally:
        webapp.control_server.stop()

    assert "zh-Hans:common.sessionLabel" in details
    assert "zh-Hans:yoagent.action.detail.screenState" in details
    assert "zh-Hans:yoagent.action.detail.composerText" in details
    assert "zh-Hans:yoagent.action.detail.sendBlocker" in details
    assert all(locale == "zh-Hans" for locale, _key in calls)


def test_app_yoagent_static_capability_question_skips_activity_summary(monkeypatch, tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(app_module, "settings_payload", lambda: payload)
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("static capability questions must not build the activity summary")))
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_cli_backend", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model backend should not run")))
    try:
        response, status = webapp.yoagent_controller.yoagent_chat({"message": "what can I do from here?"}, access_role="readonly")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert response["backend_used"] == "yolomux"
    assert "Preferences" in response["answer"]
    assert "Agent orchestration" in response["answer"]


def test_app_yoagent_product_state_question_skips_cli_backend(monkeypatch, tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload_for_path(path)
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(app_module, "settings_payload", lambda: payload)
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {
        "generated_at": "now",
        "sessions": {"6": {"session": "6", "last_activity_ts": 1, "repos": ["/repo"], "work": "tests", "file_lines": ["M app.py"]}},
        "global": {"headline": "No work."},
    })
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_cli_backend", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model backend should not run")))
    try:
        response, status = webapp.yoagent_controller.yoagent_chat({"message": "what changed?"}, access_role="readonly")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert response["backend_used"] == "yolomux"
    assert "M app.py" in response["answer"]


def test_app_yoagent_model_answer_includes_timing(monkeypatch, tmp_path):
    path = tmp_path / "settings.yaml"
    save_settings({"yoagent": {"backend": "claude"}}, path)
    payload = settings_payload(path)
    webapp = app_module.TmuxWebtermApp([])
    activity_force_calls = []
    backend_calls = []

    def fake_activity_payload(*_args, **kwargs):
        activity_force_calls.append(kwargs.get("force"))
        return {"generated_at": "now", "sessions": {}, "global": {"headline": "No work."}}

    def fake_cli_backend(*args, **kwargs):
        backend_calls.append({"args": args, "kwargs": kwargs})
        return "model answer", "", {"session_id": "abc"}

    monkeypatch.setattr(app_module, "settings_payload", lambda: payload)
    monkeypatch.setattr(app_module, "resolve_yoagent_backend", lambda backend: "claude")
    monkeypatch.setattr(webapp.yoagent_controller, "activity_summary_payload", fake_activity_payload)
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_cli_backend", fake_cli_backend)
    try:
        response, status = webapp.yoagent_controller.yoagent_chat({"message": "summarize this project"}, access_role="admin")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert response["backend_used"] == "claude"
    assert response["answer"] == "model answer"
    assert "ttfr_ms" in response["timing"]
    detail_rows = {row["key"]: row for row in response["detail_rows"]}
    assert detail_rows["yoagent.details.backend"]["params"] == {"backend": "claude"}
    assert detail_rows["yoagent.details.responseTime"]["params"] == {
        "seconds": f'{response["timing"]["ttfr_ms"] / 1000:.3f}',
        "milliseconds": f'{response["timing"]["ttfr_ms"]:.1f}',
    }
    assert response["conversation"]["messages"][-1]["detailRows"] == response["detail_rows"]
    assert app_module.server_string(
        "zh-Hans",
        detail_rows["yoagent.details.responseTime"]["key"],
        **detail_rows["yoagent.details.responseTime"]["params"],
    ).startswith("响应时间")
    assert response["details"] == ""
    assert activity_force_calls == [True]
    assert backend_calls[0]["kwargs"]["include_activity_context"] is True


def test_app_yoagent_hides_raw_think_blocks_and_exposes_safe_details(monkeypatch, tmp_path):
    path = tmp_path / "settings.yaml"
    save_settings({"yoagent": {"backend": "claude"}}, path)
    payload = settings_payload(path)
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(app_module, "settings_payload", lambda: payload)
    monkeypatch.setattr(app_module, "resolve_yoagent_backend", lambda backend: "claude")
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {"generated_at": "now", "sessions": {}, "global": {"headline": "No work."}})
    monkeypatch.setattr(webapp.yoagent_controller, "run_yoagent_cli_backend", lambda *_args, **_kwargs: ("<think>hidden chain</think>\nfinal answer", "", {"elapsed_ms": 1234}))
    try:
        response, status = webapp.yoagent_controller.yoagent_chat({"message": "use the model"}, access_role="admin")
        conversation = webapp.yoagent_conversation_payload()
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert response["answer"] == "final answer"
    assert "hidden chain" not in response["answer"]
    detail_rows = {row["key"]: row for row in response["detail_rows"]}
    assert detail_rows["yoagent.details.hiddenThinking"]["fallback"].startswith("raw model thinking was hidden")
    assert detail_rows["yoagent.details.modelCliTime"]["params"] == {"seconds": "1.234"}
    assert conversation["messages"][-1]["detailRows"] == response["detail_rows"]
    localized_hidden_thinking = app_module.server_string(
        "zh-Hans",
        detail_rows["yoagent.details.hiddenThinking"]["key"],
        **detail_rows["yoagent.details.hiddenThinking"]["params"],
    )
    assert "原始模型思考已隐藏" in localized_hidden_thinking
    assert "raw model thinking was hidden" not in localized_hidden_thinking
    assert conversation["messages"][-1].get("details", "") == response["details"] == ""


def test_app_yoagent_readonly_cannot_send_to_session(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["6"])
    monkeypatch.setattr(webapp, "activity_summary_payload", lambda *args, **kwargs: {"generated_at": "now", "sessions": {}, "global": {"headline": "No work."}})
    try:
        response, status = webapp.yoagent_controller.yoagent_chat({"message": "tell session 6 to run date"}, access_role="readonly")
    finally:
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert "requires an admin login" in response["answer"]
