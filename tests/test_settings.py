import os
from pathlib import Path
import threading


from yolomux_lib.common import DEFAULT_UPLOAD_FILENAME_TEMPLATE
from yolomux_lib.common import UPLOAD_MAX_BYTES
from yolomux_lib.settings import LEGACY_YOAGENT_DEFAULTS
from yolomux_lib.settings import default_settings
from yolomux_lib.settings import read_settings_file
from yolomux_lib.settings import save_settings
from yolomux_lib.settings import sanitize_settings
from yolomux_lib.settings import settings_catalog
from yolomux_lib.settings import settings_payload


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_pane_spacing_default_is_3px():
    # the default inter-pane gap is 3px (the JS runtime fallback in 50_editor_settings_runtime.js
    # must match this so a fresh profile and a reset-to-defaults both render a 3px gap + 3px ring).
    assert default_settings()["appearance"]["pane_spacing"] == 3
    assert default_settings()["appearance"]["pane_ring_opacity"] == 75
    assert default_settings()["appearance"]["inactive_pane_opacity"] == 60


def test_legacy_yoagent_default_prompts_migrate_without_overwriting_custom_text():
    settings = sanitize_settings({"yoagent": LEGACY_YOAGENT_DEFAULTS.copy()})
    defaults = default_settings()["yoagent"]

    assert "background-watch the target transcript or visible pane" in defaults["system_prompt"]
    assert "Native resume channels are not a substitute for sending to that pane" in defaults["system_prompt"]
    assert "YO!agent is the orchestrator" in defaults["system_prompt"]
    assert "do not reveal target-session identities to each other" in defaults["system_prompt"]
    assert "clean task/question rather than a routing transcript" in defaults["system_prompt"]
    assert "Address that target directly as `you`" in defaults["system_prompt"]
    assert "~/.config/yolomux/skills.d/" in defaults["system_prompt"]
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
            "general": {"default_layout": "bad", "reload_on_update": "bad"},
            "appearance": {"theme": "neon", "terminal_theme": "neon", "date_time_hour_cycle": "bogus", "ui_font_size": 1, "terminal_font_size": 100, "editor_font_size": 100, "editor_color_scheme": "bogus", "editor_dark_color_scheme": "github-light", "editor_light_color_scheme": "popular-ide-dark-plus", "editor_cursor_style": "beam", "editor_cursor_color": "bogus-cursor", "separator_color": "bogus-separator", "file_explorer_font_size": 1, "tab_width": 20, "pane_spacing": 50, "pane_ring_opacity": 1, "inactive_pane_opacity": 500},
            "file_explorer": {"root_mode": "bad", "image_open_mode": "bad", "image_preview_max_px": 5000, "refresh_seconds": 99},
            "notifications": {"notify_transitions": ["needs-input", "bogus", "done"]},
            "updates": {"notify_level": "bogus"},
            "performance": {"latency_refresh_ms": 100, "event_log_refresh_ms": 100000},
            "terminal_editor": {"word_wrap": "yes", "line_numbers": "no"},
            "editor": {"autosave": "yes", "autosave_delay_seconds": 100},
            "uploads": {"max_bytes": 999999999},
            "share": {"ttl_seconds": 1_000_000, "max_viewers": 999, "scheme": "ftp", "read_only": "no"},
            "yoagent": {"backend": "wat", "invocation": "bad", "system_prompt": "Use facts", "intro": "Be terse", "format": "One line"},
            "yolo": {"prompt_source": "bad"},
        }
    )

    assert settings["general"]["default_layout"] == "split"
    assert settings["general"]["reload_on_update"] is True
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
    assert sanitize_settings({"appearance": {"editor_cursor_color": "laser-lime"}})["appearance"]["editor_cursor_color"] == "laser-lime"
    assert settings["appearance"]["separator_color"] == "theme"
    assert sanitize_settings({"appearance": {"separator_color": "purple"}})["appearance"]["separator_color"] == "purple"
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
    assert settings["uploads"]["filename_template"] == DEFAULT_UPLOAD_FILENAME_TEMPLATE
    assert settings["uploads"]["max_bytes"] == 512 * 1024 * 1024
    assert settings["share"]["ttl_seconds"] == 28800
    assert settings["share"]["max_viewers"] == 300
    assert settings["share"]["read_only"] is False
    assert settings["share"]["scheme"] == "http"
    assert sanitize_settings({"share": {"scheme": "https"}})["share"]["scheme"] == "https"
    assert settings["notifications"]["notify_transitions"] == ["needs-input", "done"]
    assert settings["updates"]["notify_level"] == "patch"
    assert sanitize_settings({"updates": {"notify_level": "minor"}})["updates"]["notify_level"] == "minor"
    assert settings["performance"]["latency_refresh_ms"] == 1000
    assert settings["performance"]["event_log_refresh_ms"] == 60000
    assert settings["terminal_editor"]["word_wrap"] is True
    assert settings["terminal_editor"]["line_numbers"] is False
    assert settings["yoagent"]["backend"] == "codex"
    assert settings["yoagent"]["invocation"] == "cli"
    assert "auto_refresh" not in settings["yoagent"]
    assert settings["yoagent"]["refresh_interval_seconds"] == 0
    assert settings["yoagent"]["system_prompt"] == "Use facts"
    assert settings["yoagent"]["intro"] == "Be terse"
    assert settings["yoagent"]["format"] == "One line"
    assert settings["yolo"]["prompt_source"] == "hybrid"


def test_legacy_yoagent_auto_refresh_migrates_to_interval():
    disabled = sanitize_settings({"yoagent": {"auto_refresh": False, "refresh_interval_seconds": 120}})
    enabled = sanitize_settings({"yoagent": {"auto_refresh": True, "refresh_interval_seconds": 45}})
    enabled_without_interval = sanitize_settings({"yoagent": {"auto_refresh": True}})

    assert "auto_refresh" not in disabled["yoagent"]
    assert disabled["yoagent"]["refresh_interval_seconds"] == 0
    assert enabled["yoagent"]["refresh_interval_seconds"] == 45
    assert enabled_without_interval["yoagent"]["refresh_interval_seconds"] == 120


def test_legacy_editor_scheme_ids_migrate_to_popular_ide_names():
    legacy_prefix = "".join(("vs", "code"))
    settings = sanitize_settings({"appearance": {
        "editor_dark_color_scheme": f"{legacy_prefix}-dark-plus",
        "editor_light_color_scheme": f"{legacy_prefix}-light-plus",
    }})

    assert settings["appearance"]["editor_dark_color_scheme"] == "popular-ide-dark-plus"
    assert settings["appearance"]["editor_light_color_scheme"] == "popular-ide-light-plus"


def test_settings_round_trip_with_atomic_template(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload(path)

    assert payload["settings"] == default_settings()
    assert payload["settings"]["general"]["auto_focus"] is False
    assert payload["settings"]["appearance"]["terminal_theme"] == "follow-app"
    assert payload["settings"]["appearance"]["date_time_hour_cycle"] == "24"
    assert payload["settings"]["appearance"]["tab_width"] == 180
    assert payload["settings"]["general"]["default_layout"] == "split"
    assert payload["choices"]["general.default_layout"] == ["single", "split", "grid"]
    assert payload["catalog"]["general.default_layout"]["choices"] == ["single", "split", "grid"]
    assert "wall" not in payload["catalog"]["general.default_layout"]["choices"]
    assert payload["choices"]["appearance.active_color"] == ["green", "blue", "orange", "yellow", "purple", "white"]
    assert payload["choices"]["appearance.separator_color"] == ["theme", "green", "blue", "orange", "yellow", "purple", "white"]
    assert payload["choices"]["appearance.editor_cursor_color"] == ["green", "blue", "orange", "yellow", "purple", "white", "laser-lime", "neon-green", "neon-cyan", "neon-magenta", "neon-orange", "theme"]
    assert payload["choices"]["share.view_fit"] == ["cover", "contain"]
    assert payload["choices"]["updates.notify_level"] == ["major", "minor", "patch", "none"]
    assert payload["settings"]["general"]["startup_tips"] is True
    assert payload["catalog"]["general.default_sessions"]["gui"]["visible"] is False
    assert payload["settings"]["uploads"]["max_bytes"] == UPLOAD_MAX_BYTES
    assert payload["settings"]["share"] == {"ttl_seconds": 600, "max_viewers": 2, "read_only": True, "scheme": "http", "view_fit": "cover"}
    assert payload["settings"]["yoagent"]["backend"] == "codex"
    assert "auto_refresh" not in payload["settings"]["yoagent"]
    assert payload["settings"]["yoagent"]["refresh_interval_seconds"] == 0
    assert "normal status-update style" in payload["settings"]["yoagent"]["system_prompt"]
    assert "server-verified sends" in payload["settings"]["yoagent"]["system_prompt"]
    assert "live pane receives the text" in payload["settings"]["yoagent"]["system_prompt"]
    assert "without an extra confirmation unless the user asks" in payload["settings"]["yoagent"]["system_prompt"]
    assert "Maintain perspectives" in payload["settings"]["yoagent"]["system_prompt"]
    assert "ask agent 1 to" in payload["settings"]["yoagent"]["system_prompt"]
    assert "ask agent 1 to <do ...>" in payload["settings"]["yoagent"]["system_prompt"]
    assert "send only the task/question meant for that target" in payload["settings"]["yoagent"]["system_prompt"]
    assert "YO!agent is the orchestrator" in payload["settings"]["yoagent"]["system_prompt"]
    assert "do not ask one target session to contact another target session directly" in payload["settings"]["yoagent"]["system_prompt"]
    assert "do not reveal target-session identities to each other" in payload["settings"]["yoagent"]["system_prompt"]
    assert "Direct agent-to-agent relay or chaining is rare" in payload["settings"]["yoagent"]["system_prompt"]
    assert "pass explicit instructions" in payload["settings"]["yoagent"]["system_prompt"]
    assert "source-neutral handoff prompt" in payload["settings"]["yoagent"]["system_prompt"]
    assert "what have you done today?" in payload["settings"]["yoagent"]["system_prompt"]
    assert "~/.config/yolomux/context.d/" in payload["settings"]["yoagent"]["system_prompt"]
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


def test_settings_catalog_covers_defaults_and_gui_metadata():
    defaults = default_settings()
    catalog = settings_catalog(defaults)
    expected_paths = {f"{section}.{key}" for section, values in defaults.items() for key in values}

    assert set(catalog) == expected_paths
    for path, item in catalog.items():
        section, key = path.split(".", 1)
        assert item["path"] == path
        assert item["section"] == section
        assert item["key"] == key
        assert item["type"] in {"boolean", "integer", "number", "list", "string"}
        assert isinstance(item["description"], str) and item["description"], path
        assert item["read_role"] == "readonly"
        assert item["write_role"] == "admin"

    assert catalog["appearance.tab_width"]["limits"] == {"min": 120, "max": 420}
    assert catalog["appearance.tab_width"]["units"] == "pixels"
    assert catalog["updates.notify_level"]["choices"] == ["major", "minor", "patch", "none"]
    assert catalog["uploads.subdir"]["empty_allowed"] is True
    assert catalog["uploads.image_action_order"]["list_limit"] == 9
    assert catalog["general.language"]["aliases"]["japanese"] == "ja"
    assert catalog["yoagent.backend"]["choices"] == ["auto", "claude", "codex"]
    assert catalog["yoagent.backend"]["accepted_choices"] == ["auto", "claude", "codex", "deterministic"]
    assert catalog["yoagent.backend"]["hidden_choices"] == ["deterministic"]
    assert catalog["yoagent.invocation"]["choices"] == ["cli"]
    assert catalog["yoagent.invocation"]["accepted_choices"] == ["api-key", "cli"]
    assert catalog["yoagent.invocation"]["hidden_choices"] == ["api-key"]
    assert catalog["yoagent.codex_model"]["default"] == "gpt-5.4-mini"
    assert catalog["yoagent.codex_model"]["choices"] == ["gpt-5.4-mini", "gpt-5.4", "gpt-5.5"]
    assert "gpt-5.3-codex-spark" not in catalog["yoagent.codex_model"]["accepted_choices"]
    assert catalog["yoagent.claude_model"]["choices"] == ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"]
    assert catalog["yoagent.system_prompt"]["requires_confirmation"] is True
    assert catalog["yoagent.system_prompt"]["sensitivity"] == "prompt"
    assert catalog["appearance.theme"]["gui"] == {"section": "Appearance", "visible": True}
    assert catalog["share.view_fit"]["gui"] == {"section": "", "visible": False}


def test_stale_yoagent_model_settings_revert_to_valid_defaults():
    sanitized = sanitize_settings({
        "yoagent": {
            "codex_model": "gpt-5.3-codex-spark",
            "claude_model": "claude-fable-5",
        },
    })

    assert sanitized["yoagent"]["codex_model"] == "gpt-5.4-mini"
    assert sanitized["yoagent"]["claude_model"] == "claude-haiku-4-5"


def test_preferences_source_paths_are_in_backend_catalog():
    source = (REPO_ROOT / "static_src/js/yolomux/80_panes_preferences.js").read_text(encoding="utf-8")
    paths = set()
    for line in source.splitlines():
        if "{path:" not in line:
            continue
        for part in line.split("{path:")[1:]:
            quote = "'" if "'" in part[:2] else '"'
            if quote not in part:
                continue
            value = part.split(quote, 2)[1]
            if "." in value:
                paths.add(value)

    catalog = settings_catalog(default_settings())
    assert paths, "Preferences source should declare setting paths"
    assert paths <= set(catalog), sorted(paths - set(catalog))
    assert "general.default_layout" in paths
    assert "wall" not in catalog["general.default_layout"]["choices"]


def test_startup_helpers_setting_migrates_to_startup_tips():
    migrated = sanitize_settings({"general": {"startup_helpers": False}})
    assert migrated["general"]["startup_tips"] is False


def test_language_setting_accepts_names_and_endonyms():
    assert sanitize_settings({"general": {"language": "Japanese"}})["general"]["language"] == "ja"
    assert sanitize_settings({"general": {"language": "Traditional Chinese"}})["general"]["language"] == "zh-Hant"
    assert sanitize_settings({"general": {"language": "日本語"}})["general"]["language"] == "ja"


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
    # a clamped/reverted patch value is reported in payload["coerced"], not changed silently.
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
    off = save_settings({"yoagent": {"refresh_interval_seconds": 0}}, path)
    assert off["coerced"] == []
    assert off["settings"]["yoagent"]["refresh_interval_seconds"] == 0
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
    # Phase 1: the login-screen language picker (entry point #1) renders endonym options and a
    # successful pick is persisted to general.language (the same setting topbar/Preferences write).
    from yolomux_lib.web import LOGIN_LOCALE_CHOICES
    from yolomux_lib.web import current_language_pref
    from yolomux_lib.web import login_html
    from yolomux_lib.web import save_login_locale

    assert [value for value, _ in LOGIN_LOCALE_CHOICES] == ["system", "en", "zh-Hant", "zh-Hans", "ja", "ko", "es", "de", "fr", "it", "pt-BR", "pl", "nl", "he", "ar", "ru", "hi", "vi", "th", "tr"]
    page = login_html()
    assert 'name="locale"' in page
    assert '<select name="locale"' not in page
    assert 'data-locale-picker' in page
    assert "简体中文" in page and "繁體中文" in page  # endonym-labeled, product-priority order
    assert page.index("繁體中文") < page.index("简体中文")
    for label in ["Tiếng Việt", "ไทย", "Türkçe", "Nederlands", "Polski", "Italiano"]:
        assert label in page
    # Phase 2: the <html> shell carries lang + dir; Arabic is rendered right-to-left.
    from yolomux_lib.web import html_lang_dir_attrs
    assert html_lang_dir_attrs("en") == 'lang="en" dir="ltr"'
    assert html_lang_dir_attrs("ar") == 'lang="ar" dir="rtl"'
    assert 'dir="rtl"' in login_html(current_locale="ar")
    assert 'dir="ltr"' in login_html(current_locale="de")
    # The login chrome localizes to the saved locale (server-side, pre-auth).
    english_login = login_html(current_locale="en")
    assert "Sign in" in english_login
    assert "<h1>" not in english_login
    assert "登入" in login_html(current_locale="zh-Hant")  # Sign in (Traditional)
    assert "Iniciar sesión" in login_html(current_locale="es")
    assert "ログイン" in login_html(current_locale="ja")
    assert "Đăng nhập" in login_html(current_locale="vi")
    try:
        save_login_locale("vi")
        assert current_language_pref() == "vi"
        page = login_html(current_locale=current_language_pref())
        assert 'name="locale" value="vi"' in page
        assert 'data-locale-value="vi" aria-selected="true"' in page
        save_login_locale("zh-Hant")
        assert current_language_pref() == "zh-Hant"
        page = login_html(current_locale=current_language_pref())
        assert 'name="locale" value="zh-Hant"' in page
        assert 'data-locale-value="zh-Hant" aria-selected="true"' in page
        assert "使用者名稱" in page and "密碼" in page  # Username / Password localized
        save_login_locale("bogus-locale")  # invalid -> ignored, no change
        assert current_language_pref() == "zh-Hant"
    finally:
        save_login_locale("system")  # reset so the shared test config dir isn't left in Chinese


def test_deterministic_yoagent_reply_localizes_framing():
    # Phase 3: the no-agent fallback localizes its fixed framing (prefix + no-activity headline)
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


def test_watched_prs_and_server_event_defaults():
    # watched PRs ship with a safe empty default; refresh cadence is server-internal.
    d = default_settings()
    assert d["github"]["watched_prs"] == []
    assert d["performance"]["server_event_poll_ms"] == 850
    assert d["performance"]["server_background_file_event_poll_ms"] == 5000
    assert d["performance"]["server_directory_event_poll_ms"] == 3000


def test_removed_client_poll_settings_are_not_saved():
    d = default_settings()
    assert "refresh_seconds" not in d["file_explorer"]
    assert "session_files_refresh_seconds" not in d["file_explorer"]
    for key in [
        "metadata_refresh_ms",
        "pane_state_refresh_ms",
        "settings_refresh_ms",
        "activity_summary_refresh_ms",
        "watched_pr_refresh_ms",
    ]:
        assert key not in d["performance"]
    migrated = sanitize_settings({
        "file_explorer": {"refresh_ms": 42000, "refresh_seconds": 7, "session_files_refresh_seconds": 12},
        "performance": {
            "metadata_refresh_ms": 15002,
            "pane_state_refresh_ms": 1254,
            "settings_refresh_ms": 5010,
            "activity_summary_refresh_ms": 61000,
            "watched_pr_refresh_ms": 60002,
        },
    })
    assert "refresh_seconds" not in migrated["file_explorer"]
    for key in [
        "metadata_refresh_ms",
        "pane_state_refresh_ms",
        "settings_refresh_ms",
        "activity_summary_refresh_ms",
        "watched_pr_refresh_ms",
    ]:
        assert key not in migrated["performance"]


def test_stale_saved_poll_defaults_migrate_to_current_defaults():
    migrated = sanitize_settings({
        "performance": {
            "latency_refresh_ms": 3_001,
            "event_log_refresh_ms": 5_003,
            "server_event_poll_ms": 5_009,
            "server_background_file_event_poll_ms": 5_009,
            "server_directory_event_poll_ms": 5_009,
        },
    })
    defaults = default_settings()
    assert migrated["performance"]["latency_refresh_ms"] == defaults["performance"]["latency_refresh_ms"]
    assert migrated["performance"]["event_log_refresh_ms"] == defaults["performance"]["event_log_refresh_ms"]
    assert migrated["performance"]["server_event_poll_ms"] == defaults["performance"]["server_event_poll_ms"]
    assert migrated["performance"]["server_background_file_event_poll_ms"] == defaults["performance"]["server_background_file_event_poll_ms"]
    assert migrated["performance"]["server_directory_event_poll_ms"] == defaults["performance"]["server_directory_event_poll_ms"]
    rounded_legacy = sanitize_settings({"performance": {"server_event_poll_ms": 5000}})
    assert rounded_legacy["performance"]["server_event_poll_ms"] == defaults["performance"]["server_event_poll_ms"]
    rounded_background_legacy = sanitize_settings({"performance": {"server_background_file_event_poll_ms": 5000}})
    assert rounded_background_legacy["performance"]["server_background_file_event_poll_ms"] == defaults["performance"]["server_background_file_event_poll_ms"]
    rounded_directory_legacy = sanitize_settings({"performance": {"server_directory_event_poll_ms": 5000}})
    assert rounded_directory_legacy["performance"]["server_directory_event_poll_ms"] == defaults["performance"]["server_directory_event_poll_ms"]
    stale_share = sanitize_settings({"share": {"max_viewers": 5}})
    assert stale_share["share"]["max_viewers"] == defaults["share"]["max_viewers"]

    custom = sanitize_settings({
        "performance": {
            "latency_refresh_ms": 3002,
            "event_log_refresh_ms": 5004,
            "server_event_poll_ms": 251,
            "server_background_file_event_poll_ms": 253,
            "server_directory_event_poll_ms": 252,
        },
        "share": {"max_viewers": 7},
    })
    assert custom["performance"]["latency_refresh_ms"] == 3002
    assert custom["performance"]["event_log_refresh_ms"] == 5004
    assert custom["performance"]["server_event_poll_ms"] == 251
    assert custom["performance"]["server_background_file_event_poll_ms"] == 253
    assert custom["performance"]["server_directory_event_poll_ms"] == 252
    assert custom["share"]["max_viewers"] == 7


def test_notify_transitions_accepts_pr_keys_and_drops_unknown():
    # the notify_transitions allowlist now accepts the watched-PR transition keys alongside
    # session-state keys; unknown keys are still dropped.
    settings = sanitize_settings(
        {"notifications": {"notify_transitions": ["needs-input", "pr-merged", "pr-ci-failing", "pr-review", "bogus"]}}
    )
    assert settings["notifications"]["notify_transitions"] == ["needs-input", "pr-merged", "pr-ci-failing", "pr-review"]


def test_new_poll_intervals_are_clamped_to_range():
    low = sanitize_settings({"performance": {"server_event_poll_ms": 100, "server_background_file_event_poll_ms": 100, "server_directory_event_poll_ms": 100}})
    high = sanitize_settings({"performance": {"server_event_poll_ms": 10_000_000, "server_background_file_event_poll_ms": 10_000_000, "server_directory_event_poll_ms": 10_000_000}})
    assert low["performance"]["server_event_poll_ms"] == 250
    assert high["performance"]["server_event_poll_ms"] == 60000
    assert low["performance"]["server_background_file_event_poll_ms"] == 250
    assert high["performance"]["server_background_file_event_poll_ms"] == 60000
    assert low["performance"]["server_directory_event_poll_ms"] == 250
    assert high["performance"]["server_directory_event_poll_ms"] == 60000


def test_watched_prs_setting_round_trips_in_template(tmp_path):
    # a watchlist persists through the YAML template round-trip.
    path = tmp_path / "settings.yaml"
    save_settings(default_settings(), path)
    updated = save_settings({"github": {"watched_prs": ["ai-dynamo/frontend-crates#18", "owner/repo#7"]}}, path)
    assert updated["settings"]["github"]["watched_prs"] == ["ai-dynamo/frontend-crates#18", "owner/repo#7"]
    loaded, error = read_settings_file(path)
    assert error == ""
    assert loaded["github"]["watched_prs"] == ["ai-dynamo/frontend-crates#18", "owner/repo#7"]


def test_blame_all_lines_default_and_coercion():
    # follow-up: the all-lines blame toggle defaults off and coerces to bool.
    assert default_settings()["editor"]["blame_all_lines"] is False
    assert sanitize_settings({"editor": {"blame_all_lines": "yes"}})["editor"]["blame_all_lines"] is True
    assert sanitize_settings({"editor": {"blame_all_lines": "no"}})["editor"]["blame_all_lines"] is False


def test_uploads_subdir_defaults_to_dot_uploads():
    assert default_settings()["uploads"]["subdir"] == ".uploads"


def test_uploads_subdir_allows_empty_for_cwd_opt_out():
    assert sanitize_settings({"uploads": {"subdir": ""}})["uploads"]["subdir"] == ""


def test_uploads_blank_filename_template_still_reverts_to_default():
    assert sanitize_settings({"uploads": {"filename_template": ""}})["uploads"]["filename_template"] == DEFAULT_UPLOAD_FILENAME_TEMPLATE


def test_upload_drop_action_settings_defaults_and_round_trip(tmp_path):
    defaults = default_settings()["uploads"]
    assert defaults["suggestion_autorun"] is False
    assert defaults["image_action_order"] == [
        "Extract the text (OCR): ; do OCR on this image and extract all of the text.",
        "Diagnose the error: ; diagnose the error/problem shown in this screenshot & suggest a fix.",
        "Describe the image: ; describe what is shown in this image.",
        "info",
    ]
    assert defaults["custom_actions"] == []

    settings = sanitize_settings({"uploads": {"suggestion_autorun": "yes", "image_action_order": ["img-describe", "", 7, "Info: info", "Insert path", "Show file type", "Extract the text: ; do OCR on this image and extract all of the text.", "Diagnose the error in this screenshot: ; diagnose the error or problem shown in this screenshot and suggest a fix.", "server ocr"], "custom_actions": ["Ask owner | explain {name} | code", "", 7]}})
    assert settings["uploads"]["suggestion_autorun"] is True
    assert settings["uploads"]["image_action_order"] == [
        "Describe the image: ; describe what is shown in this image.",
        "info",
        "Extract the text (OCR): ; do OCR on this image and extract all of the text.",
        "Diagnose the error: ; diagnose the error/problem shown in this screenshot & suggest a fix.",
    ]
    assert settings["uploads"]["custom_actions"] == ["Ask owner | explain {name} | code"]

    path = tmp_path / "settings.yaml"
    too_many_actions = [f"action {index}" for index in range(12)]
    updated = save_settings({"uploads": {"suggestion_autorun": True, "image_action_order": too_many_actions, "custom_actions": ["Peek | shell:head -40 {qpath} | log"]}}, path)
    assert updated["settings"]["uploads"]["suggestion_autorun"] is True
    assert updated["settings"]["uploads"]["image_action_order"] == too_many_actions[:9]
    assert updated["settings"]["uploads"]["custom_actions"] == ["Peek | shell:head -40 {qpath} | log"]


def test_updates_check_defaults_to_patch_threshold():
    # origin/main update checks are controlled by the notification threshold; "none" means off.
    general = default_settings()["general"]
    updates = default_settings()["updates"]
    assert general["reload_on_update"] is True
    assert general["reload_on_update_auto"] is False
    assert updates["check_interval_minutes"] == 60
    assert updates["notify_level"] == "patch"
