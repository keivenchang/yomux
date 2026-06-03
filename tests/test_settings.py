import os
import threading

os.environ.setdefault("YOLOMUX_CONFIG_DIR", "/tmp/yolomux-test-config")

from yolomux_lib.common import DEFAULT_UPLOAD_FILENAME_TEMPLATE
from yolomux_lib.common import UPLOAD_MAX_BYTES
from yolomux_lib.settings import default_settings
from yolomux_lib.settings import read_settings_file
from yolomux_lib.settings import save_settings
from yolomux_lib.settings import sanitize_settings
from yolomux_lib.settings import settings_payload


def test_sanitize_settings_clamps_numbers_and_choices():
    settings = sanitize_settings(
        {
            "appearance": {"theme": "neon", "terminal_theme": "neon", "ui_font_size": 1, "terminal_font_size": 100, "editor_font_size": 100, "editor_color_scheme": "bogus", "editor_dark_color_scheme": "github-light", "editor_light_color_scheme": "vscode-dark-plus", "editor_cursor_style": "beam", "file_explorer_font_size": 1, "tab_width": 20},
            "file_explorer": {"root_mode": "bad", "image_open_mode": "bad", "image_preview_max_px": 5000, "refresh_ms": 3000},
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
    assert settings["appearance"]["ui_font_size"] == 8
    assert settings["appearance"]["terminal_font_size"] == 28
    assert settings["appearance"]["editor_font_size"] == 28
    assert settings["appearance"]["editor_color_scheme"] == "dark"
    assert settings["appearance"]["editor_dark_color_scheme"] == "dark"
    assert settings["appearance"]["editor_light_color_scheme"] == "yolomux-light"
    assert settings["appearance"]["editor_cursor_style"] == "line"
    assert settings["appearance"]["file_explorer_font_size"] == 8
    assert settings["appearance"]["tab_width"] == 120
    assert settings["file_explorer"]["root_mode"] == "fixed"
    assert settings["file_explorer"]["image_open_mode"] == "same-tab"
    assert settings["file_explorer"]["image_preview_max_px"] == 1200
    assert settings["editor"]["autosave"] is True
    assert settings["editor"]["autosave_delay_seconds"] == 60
    assert settings["file_explorer"]["refresh_ms"] == 3000
    assert settings["uploads"]["filename_template"] == DEFAULT_UPLOAD_FILENAME_TEMPLATE
    assert settings["uploads"]["max_bytes"] == 512 * 1024 * 1024
    assert settings["notifications"]["notify_transitions"] == ["needs-input", "done"]
    assert settings["performance"]["metadata_refresh_ms"] == 15000
    assert settings["performance"]["pane_state_refresh_ms"] == 1200
    assert settings["terminal_editor"]["word_wrap"] is True
    assert settings["terminal_editor"]["line_numbers"] is False
    assert settings["yoagent"]["backend"] == "auto"
    assert settings["yoagent"]["invocation"] == "cli"
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
    assert payload["settings"]["appearance"]["tab_width"] == 180
    assert payload["settings"]["uploads"]["max_bytes"] == UPLOAD_MAX_BYTES
    assert payload["settings"]["yoagent"]["backend"] == "auto"
    assert "normal human status update" in payload["settings"]["yoagent"]["system_prompt"]
    assert "structured status report" in payload["settings"]["yoagent"]["intro"]
    assert "numbered list with ONE item per session" in payload["settings"]["yoagent"]["format"]
    assert "Open / pending:" in payload["settings"]["yoagent"]["format"]
    assert path.exists()
    assert "YOLOmux user preferences" in path.read_text()

    updated = save_settings({"appearance": {"terminal_font_size": 17}, "file_explorer": {"quick_access_paths": ["/tmp", ""]}}, path)
    assert updated["settings"]["appearance"]["terminal_font_size"] == 17
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


def test_login_locale_picker_writes_general_language():
    # DOIT.8 Phase 1: the login-screen language picker (entry point #1) renders endonym options and a
    # successful pick is persisted to general.language (the same setting topbar/Preferences write).
    from yolomux_lib.web import LOGIN_LOCALE_CHOICES
    from yolomux_lib.web import current_language_pref
    from yolomux_lib.web import login_html
    from yolomux_lib.web import save_login_locale

    assert [value for value, _ in LOGIN_LOCALE_CHOICES] == ["system", "en", "zh-Hant", "zh-Hans", "es", "ja", "de", "fr", "pt-BR", "ru", "ko", "hi", "ar"]
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
