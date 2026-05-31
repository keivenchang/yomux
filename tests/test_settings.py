from yolomux_lib.settings import default_settings
from yolomux_lib.settings import read_settings_file
from yolomux_lib.settings import save_settings
from yolomux_lib.settings import sanitize_settings
from yolomux_lib.settings import settings_payload


def test_sanitize_settings_clamps_numbers_and_choices():
    settings = sanitize_settings(
        {
            "appearance": {"ui_font_size": 1, "terminal_font_size": 100, "editor_font_size": 100, "editor_color_scheme": "bogus", "editor_dark_color_scheme": "github-light", "editor_light_color_scheme": "vscode-dark-plus", "file_explorer_font_size": 1, "tab_width": 20},
            "file_explorer": {"root_mode": "bad", "image_open_mode": "bad", "image_preview_max_px": 5000, "refresh_ms": 3000},
            "editor": {"engine": "bad"},
            "notifications": {"notify_transitions": ["needs-input", "bogus", "done"]},
            "performance": {"metadata_refresh_ms": 15000, "pane_state_refresh_ms": 1200},
            "terminal_editor": {"word_wrap": "yes", "line_numbers": "no"},
        }
    )

    assert settings["appearance"]["ui_font_size"] == 8
    assert settings["appearance"]["terminal_font_size"] == 28
    assert settings["appearance"]["editor_font_size"] == 28
    assert settings["appearance"]["editor_color_scheme"] == "vscode-dark-plus"
    assert settings["appearance"]["editor_dark_color_scheme"] == "vscode-dark-plus"
    assert settings["appearance"]["editor_light_color_scheme"] == "github-light"
    assert settings["appearance"]["file_explorer_font_size"] == 8
    assert settings["appearance"]["tab_width"] == 120
    assert settings["file_explorer"]["root_mode"] == "fixed"
    assert settings["file_explorer"]["image_open_mode"] == "same-tab"
    assert settings["file_explorer"]["image_preview_max_px"] == 1200
    assert settings["editor"]["engine"] == "codemirror"
    assert settings["file_explorer"]["refresh_ms"] == 3000
    assert settings["notifications"]["notify_transitions"] == ["needs-input", "done"]
    assert settings["performance"]["metadata_refresh_ms"] == 15000
    assert settings["performance"]["pane_state_refresh_ms"] == 1200
    assert settings["terminal_editor"]["word_wrap"] is True
    assert settings["terminal_editor"]["line_numbers"] is False


def test_settings_round_trip_with_atomic_template(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload(path)

    assert payload["settings"] == default_settings()
    assert payload["settings"]["general"]["auto_focus"] is False
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
            "file_explorer": {"quick_access_paths": ["/tmp"]},
        },
        path,
    )

    reset = save_settings(default_settings(), path)

    assert reset["settings"] == default_settings()
