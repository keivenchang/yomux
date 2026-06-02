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
    assert settings["yoagent"]["backend"] == "deterministic"
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
    assert payload["settings"]["yoagent"]["backend"] == "deterministic"
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
