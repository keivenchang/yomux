import os
import threading


from yolomux_lib.common import DEFAULT_UPLOAD_FILENAME_TEMPLATE
from yolomux_lib.common import UPLOAD_MAX_BYTES
from yolomux_lib.settings import LEGACY_YOAGENT_DEFAULTS
from yolomux_lib.settings import default_settings
from yolomux_lib.settings import read_settings_file
from yolomux_lib.settings import save_settings
from yolomux_lib.settings import sanitize_settings
from yolomux_lib.settings import settings_payload


def test_pane_spacing_default_is_4px():
    # DOIT.20: the default inter-pane gap is 4px (the JS runtime fallback in 50_editor_settings_runtime.js
    # must match this so a fresh profile and a reset-to-defaults both render a 4px gap + 4px ring).
    assert default_settings()["appearance"]["pane_spacing"] == 4
    assert default_settings()["appearance"]["pane_ring_opacity"] == 75
    assert default_settings()["appearance"]["inactive_pane_opacity"] == 60


def test_legacy_yoagent_default_prompts_migrate_without_overwriting_custom_text():
    settings = sanitize_settings({"yoagent": LEGACY_YOAGENT_DEFAULTS.copy()})
    defaults = default_settings()["yoagent"]

    assert settings["yoagent"]["system_prompt"] == defaults["system_prompt"]
    assert settings["yoagent"]["intro"] == defaults["intro"]
    assert settings["yoagent"]["format"] == defaults["format"]

    custom = sanitize_settings({"yoagent": {**LEGACY_YOAGENT_DEFAULTS, "intro": "Custom intro"}})
    assert custom["yoagent"]["system_prompt"] == defaults["system_prompt"]
    assert custom["yoagent"]["intro"] == "Custom intro"
    assert custom["yoagent"]["format"] == defaults["format"]

    stale_reset = sanitize_settings({"yoagent": {
        "system_prompt": "You are YO!agent, a concise assistant for YOLOmux. Help users operate YOLOmux using the supplied concepts and report only from the supplied agent activity context. Write like a normal human status update, not a metadata list. Answer the user's question directly and helpfully. Prioritize the most recent and important work, blockers, PRs, CI, dirty repos, and likely next actions. Do not mention session ids, per-session details, or a session-by-session inventory unless the user explicitly asks about a session, asks to list/enumerate sessions, or asks for all sessions. Do not run tools or inspect ~/.claude, ~/.codex, transcript directories, or any filesystem path. Do not invent missing facts.",
        "intro": "Use the live AI agent activity only as much as the user asked for. If the user is unsure what to do, recommend what to work on next based on freshness, importance, blockers, PR/CI state, dirty repos, and changed files. Keep stale or old work out of the default answer unless it changes the recommendation or the user explicitly asks for sessions.",
        "format": "Reply in Markdown. Default shape: a short direct answer, then optional bullets for the top relevant topics or next actions. Do not include session ids, per-session headings, or one item per session unless the user asks about a specific session or asks to list/enumerate/show all sessions. When the user explicitly asks for sessions, use one numbered item per session/topic, with a bold title and one or two short factual sub-bullets.",
    }})
    assert stale_reset["yoagent"]["system_prompt"] == defaults["system_prompt"]
    assert stale_reset["yoagent"]["intro"] == defaults["intro"]
    assert stale_reset["yoagent"]["format"] == defaults["format"]


def test_sanitize_settings_clamps_numbers_and_choices():
    settings = sanitize_settings(
        {
            "appearance": {"theme": "neon", "terminal_theme": "neon", "date_time_hour_cycle": "bogus", "ui_font_size": 1, "terminal_font_size": 100, "editor_font_size": 100, "editor_color_scheme": "bogus", "editor_dark_color_scheme": "github-light", "editor_light_color_scheme": "vscode-dark-plus", "editor_cursor_style": "beam", "editor_cursor_color": "purple", "file_explorer_font_size": 1, "tab_width": 20, "pane_spacing": 50, "pane_ring_opacity": 1, "inactive_pane_opacity": 500},
            "file_explorer": {"root_mode": "bad", "image_open_mode": "bad", "image_preview_max_px": 5000, "refresh_seconds": 99},
            "notifications": {"notify_transitions": ["needs-input", "bogus", "done"]},
            "performance": {"metadata_refresh_ms": 15000, "pane_state_refresh_ms": 1200},
            "terminal_editor": {"word_wrap": "yes", "line_numbers": "no"},
            "editor": {"autosave": "yes", "autosave_delay_seconds": 100},
            "uploads": {"max_bytes": 999999999},
            "yoagent": {"backend": "wat", "invocation": "bad", "system_prompt": "Use facts", "intro": "Be terse", "format": "One line"},
            "yolo": {"prompt_source": "bad"},
        }
    )

    assert settings["appearance"]["theme"] == "dark"
    assert settings["appearance"]["terminal_theme"] == "follow-app"
    assert settings["appearance"]["date_time_hour_cycle"] == "24"
    assert settings["appearance"]["ui_font_size"] == 6
    assert settings["appearance"]["terminal_font_size"] == 28
    assert settings["appearance"]["editor_font_size"] == 28
    assert settings["appearance"]["editor_color_scheme"] == "dark"
    assert settings["appearance"]["editor_dark_color_scheme"] == "dark"
    assert settings["appearance"]["editor_light_color_scheme"] == "yolomux-light"
    assert settings["appearance"]["editor_cursor_style"] == "block"  # C3: invalid choice clamps to the new block default
    assert settings["appearance"]["editor_cursor_color"] == "yellow"  # invalid choice clamps to the default
    assert settings["appearance"]["file_explorer_font_size"] == 6
    assert settings["appearance"]["tab_width"] == 120
    assert settings["appearance"]["pane_spacing"] == 20
    assert settings["appearance"]["pane_ring_opacity"] == 5
    assert settings["appearance"]["inactive_pane_opacity"] == 100
    assert settings["file_explorer"]["root_mode"] == "sync"
    assert settings["file_explorer"]["image_open_mode"] == "same-tab"
    assert settings["file_explorer"]["image_preview_max_px"] == 1200
    assert settings["editor"]["autosave"] is True
    assert settings["editor"]["autosave_delay_seconds"] == 60
    assert settings["file_explorer"]["refresh_seconds"] == 60
    assert settings["uploads"]["filename_template"] == DEFAULT_UPLOAD_FILENAME_TEMPLATE
    assert settings["uploads"]["max_bytes"] == 512 * 1024 * 1024
    assert settings["notifications"]["notify_transitions"] == ["needs-input", "done"]
    assert settings["performance"]["metadata_refresh_ms"] == 15000
    assert settings["performance"]["pane_state_refresh_ms"] == 1200
    assert settings["terminal_editor"]["word_wrap"] is True
    assert settings["terminal_editor"]["line_numbers"] is False
    assert settings["yoagent"]["backend"] == "auto"
    assert settings["yoagent"]["invocation"] == "cli"
    assert settings["yoagent"]["auto_refresh"] is False
    assert settings["yoagent"]["refresh_interval_seconds"] == 120
    assert settings["yoagent"]["system_prompt"] == "Use facts"
    assert settings["yoagent"]["intro"] == "Be terse"
    assert settings["yoagent"]["format"] == "One line"
    assert settings["yolo"]["prompt_source"] == "hybrid"


def test_settings_round_trip_with_atomic_template(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload(path)

    assert payload["settings"] == default_settings()
    assert payload["settings"]["general"]["auto_focus"] is False
    assert payload["settings"]["appearance"]["terminal_theme"] == "follow-app"
    assert payload["settings"]["appearance"]["date_time_hour_cycle"] == "24"
    assert payload["settings"]["appearance"]["tab_width"] == 180
    assert payload["settings"]["general"]["startup_helpers"] is True
    assert payload["settings"]["uploads"]["max_bytes"] == UPLOAD_MAX_BYTES
    assert payload["settings"]["yoagent"]["backend"] == "auto"
    assert payload["settings"]["yoagent"]["auto_refresh"] is False
    assert payload["settings"]["yoagent"]["refresh_interval_seconds"] == 120
    assert "normal status-update style" in payload["settings"]["yoagent"]["system_prompt"]
    assert "explicit admin UI paths" in payload["settings"]["yoagent"]["system_prompt"]
    assert "autonomous command-sending tools" in payload["settings"]["yoagent"]["system_prompt"]
    assert "If needed facts are missing" in payload["settings"]["yoagent"]["intro"]
    assert "recommend what to work on next" in payload["settings"]["yoagent"]["intro"]
    assert "refer to it as tmux session `<session-name>`" in payload["settings"]["yoagent"]["system_prompt"]
    assert "use one Markdown table with columns" in payload["settings"]["yoagent"]["format"]
    assert "show only the session name as a Markdown link" in payload["settings"]["yoagent"]["format"]
    assert "[`2`](?yoagent-session=2)" in payload["settings"]["yoagent"]["format"]
    assert "9 hrs ago" in payload["settings"]["yoagent"]["format"]
    assert "If there are 6 sessions, emit 6 table rows." in payload["settings"]["yoagent"]["format"]
    assert "Open / pending:" in payload["settings"]["yoagent"]["format"]
    assert path.exists()
    assert "YOLOmux user preferences" in path.read_text()

    updated = save_settings({"appearance": {"terminal_font_size": 17, "date_time_hour_cycle": "12"}, "file_explorer": {"quick_access_paths": ["/tmp", ""]}}, path)
    assert updated["settings"]["appearance"]["terminal_font_size"] == 17
    assert updated["settings"]["appearance"]["date_time_hour_cycle"] == "12"
    assert updated["settings"]["file_explorer"]["quick_access_paths"] == ["/tmp"]

    loaded, error = read_settings_file(path)
    assert error == ""
    assert loaded == updated["settings"]


def test_save_settings_can_reset_all_values_to_defaults(tmp_path):
    path = tmp_path / "settings.yaml"
    save_settings(
        {
            "appearance": {"terminal_font_size": 17, "editor_color_scheme": "github-light"},
            "terminal_editor": {"word_wrap": True, "line_numbers": True},
            "editor": {"autosave": False, "autosave_delay_seconds": 7},
            "file_explorer": {"quick_access_paths": ["/tmp"]},
        },
        path,
    )

    reset = save_settings(default_settings(), path)

    assert reset["settings"] == default_settings()


def test_save_settings_preserves_concurrent_section_updates(tmp_path):
    path = tmp_path / "settings.yaml"
    save_settings(default_settings(), path)

    patches = [
        {"appearance": {"terminal_font_size": 18}},
        {"appearance": {"editor_font_size": 19}},
        {"file_explorer": {"quick_access_paths": ["/tmp", "/var"]}},
        {"terminal_editor": {"word_wrap": True}},
        {"notifications": {"throttle_seconds": 17}},
    ]
    threads = [threading.Thread(target=save_settings, args=(patch, path)) for patch in patches]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    settings, error = read_settings_file(path)
    assert error == ""
    assert settings["appearance"]["terminal_font_size"] == 18
    assert settings["appearance"]["editor_font_size"] == 19
    assert settings["file_explorer"]["quick_access_paths"] == ["/tmp", "/var"]
    assert settings["terminal_editor"]["word_wrap"] is True
    assert settings["notifications"]["throttle_seconds"] == 17
    assert not (tmp_path / "settings.yaml.tmp").exists()
    assert list(tmp_path.glob(".settings.yaml.*.tmp")) == []


def test_save_settings_reports_coerced_keys(tmp_path):
    # DOIT.6 #84: a clamped/reverted patch value is reported in payload["coerced"], not changed silently.
    path = tmp_path / "settings.yaml"
    save_settings(default_settings(), path)
    result = save_settings({"appearance": {"ui_font_size": 999}}, path)
    assert "appearance.ui_font_size" in result["coerced"]
    assert result["settings"]["appearance"]["ui_font_size"] == 20  # clamped to the max
    ok = save_settings({"appearance": {"ui_font_size": 14}}, path)
    assert ok["coerced"] == []
    ring = save_settings({"appearance": {"pane_ring_opacity": 20}}, path)
    assert ring["coerced"] == []
    assert ring["settings"]["appearance"]["pane_ring_opacity"] == 20
    interval = save_settings({"yoagent": {"refresh_interval_seconds": 1}}, path)
    assert "yoagent.refresh_interval_seconds" in interval["coerced"]
    assert interval["settings"]["yoagent"]["refresh_interval_seconds"] == 30
    clamped_ring = save_settings({"appearance": {"pane_ring_opacity": 1}}, path)
    assert "appearance.pane_ring_opacity" in clamped_ring["coerced"]
    assert clamped_ring["settings"]["appearance"]["pane_ring_opacity"] == 5
    opacity = save_settings({"appearance": {"inactive_pane_opacity": 0}}, path)
    assert opacity["coerced"] == []
    assert opacity["settings"]["appearance"]["inactive_pane_opacity"] == 0
    clamped_opacity = save_settings({"appearance": {"inactive_pane_opacity": 500}}, path)
    assert "appearance.inactive_pane_opacity" in clamped_opacity["coerced"]
    assert clamped_opacity["settings"]["appearance"]["inactive_pane_opacity"] == 100


def test_login_locale_picker_writes_general_language():
    # DOIT.8 Phase 1: the login-screen language picker (entry point #1) renders endonym options and a
    # successful pick is persisted to general.language (the same setting topbar/Preferences write).
    from yolomux_lib.web import LOGIN_LOCALE_CHOICES
    from yolomux_lib.web import current_language_pref
    from yolomux_lib.web import login_html
    from yolomux_lib.web import save_login_locale

    assert [value for value, _ in LOGIN_LOCALE_CHOICES] == ["system", "en", "zh-Hant", "zh-Hans", "es", "ja", "de", "fr", "pt-BR", "ru", "ko", "hi", "ar", "he"]
    page = login_html()
    assert 'name="locale"' in page
    assert "繁體中文" in page and "简体中文" in page  # endonym-labeled, Traditional before Simplified
    assert page.index("繁體中文") < page.index("简体中文")
    # DOIT.8 Phase 2: the <html> shell carries lang + dir; Arabic is rendered right-to-left.
    from yolomux_lib.web import html_lang_dir_attrs
    assert html_lang_dir_attrs("en") == 'lang="en" dir="ltr"'
    assert html_lang_dir_attrs("ar") == 'lang="ar" dir="rtl"'
    assert 'dir="rtl"' in login_html(current_locale="ar")
    assert 'dir="ltr"' in login_html(current_locale="de")
    # The login chrome localizes to the saved locale (server-side, pre-auth).
    assert "Sign in" in login_html(current_locale="en")
    assert "登入" in login_html(current_locale="zh-Hant")  # Sign in (Traditional)
    assert "Iniciar sesión" in login_html(current_locale="es")
    assert "ログイン" in login_html(current_locale="ja")
    try:
        save_login_locale("zh-Hant")
        assert current_language_pref() == "zh-Hant"
        page = login_html(current_locale=current_language_pref())
        assert ' value="zh-Hant" selected>' in page
        assert "使用者名稱" in page and "密碼" in page  # Username / Password localized
        save_login_locale("bogus-locale")  # invalid -> ignored, no change
        assert current_language_pref() == "zh-Hant"
    finally:
        save_login_locale("system")  # reset so the shared test config dir isn't left in Chinese


def test_deterministic_yoagent_reply_localizes_framing():
    # DOIT.8 Phase 3: the no-agent fallback localizes its fixed framing (prefix + no-activity headline)
    # via the saved/active locale. The generated per-session prose stays English (LLM backends localize).
    from yolomux_lib.activity_summary import deterministic_yoagent_reply

    en_reply = deterministic_yoagent_reply("status?", {}, {}, "en")
    assert "No AI backend is answering" in en_reply
    assert "No AI agent activity is available yet." in en_reply

    es_reply = deterministic_yoagent_reply("status?", {}, {}, "es")
    assert "Ningún backend de IA está respondiendo" in es_reply
    assert "Aún no hay actividad del agente de IA disponible." in es_reply

    ja_reply = deterministic_yoagent_reply("status?", {}, {}, "ja")
    assert "応答する AI バックエンドがありません" in ja_reply


def test_watched_prs_and_watched_pr_refresh_defaults():
    # DOIT.29: the watched-PR settings ship with safe empty/longer-interval defaults.
    d = default_settings()
    assert d["github"]["watched_prs"] == []
    assert d["performance"]["watched_pr_refresh_ms"] == 60000


def test_file_explorer_refresh_uses_seconds_and_migrates_legacy_ms():
    d = default_settings()
    assert d["file_explorer"]["refresh_seconds"] == 15
    migrated = sanitize_settings({"file_explorer": {"refresh_ms": 42000}})
    assert migrated["file_explorer"]["refresh_seconds"] == 42
    default_legacy = sanitize_settings({"file_explorer": {"refresh_ms": 3000}})
    assert default_legacy["file_explorer"]["refresh_seconds"] == 15


def test_notify_transitions_accepts_pr_keys_and_drops_unknown():
    # DOIT.29: the notify_transitions allowlist now accepts the watched-PR transition keys alongside
    # session-state keys; unknown keys are still dropped.
    settings = sanitize_settings(
        {"notifications": {"notify_transitions": ["needs-input", "pr-merged", "pr-ci-failing", "pr-review", "bogus"]}}
    )
    assert settings["notifications"]["notify_transitions"] == ["needs-input", "pr-merged", "pr-ci-failing", "pr-review"]


def test_watched_pr_refresh_ms_is_clamped_to_range():
    # DOIT.29: below the 15000ms floor / above the 600000ms ceiling clamp to the bounds.
    low = sanitize_settings({"performance": {"watched_pr_refresh_ms": 100}})
    high = sanitize_settings({"performance": {"watched_pr_refresh_ms": 10_000_000}})
    assert low["performance"]["watched_pr_refresh_ms"] == 15000
    assert high["performance"]["watched_pr_refresh_ms"] == 600000


def test_watched_prs_setting_round_trips_in_template(tmp_path):
    # DOIT.29: a watchlist persists through the YAML template round-trip.
    path = tmp_path / "settings.yaml"
    save_settings(default_settings(), path)
    updated = save_settings({"github": {"watched_prs": ["ai-dynamo/frontend-crates#18", "owner/repo#7"]}}, path)
    assert updated["settings"]["github"]["watched_prs"] == ["ai-dynamo/frontend-crates#18", "owner/repo#7"]
    loaded, error = read_settings_file(path)
    assert error == ""
    assert loaded["github"]["watched_prs"] == ["ai-dynamo/frontend-crates#18", "owner/repo#7"]


def test_blame_all_lines_default_and_coercion():
    # DOIT.26 follow-up: the all-lines blame toggle defaults off and coerces to bool.
    assert default_settings()["editor"]["blame_all_lines"] is False
    assert sanitize_settings({"editor": {"blame_all_lines": "yes"}})["editor"]["blame_all_lines"] is True
    assert sanitize_settings({"editor": {"blame_all_lines": "no"}})["editor"]["blame_all_lines"] is False
