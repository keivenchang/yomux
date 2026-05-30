from yolomux_lib.settings import default_settings
from yolomux_lib.settings import read_settings_file
from yolomux_lib.settings import save_settings
from yolomux_lib.settings import sanitize_settings
from yolomux_lib.settings import settings_payload


def test_sanitize_settings_clamps_numbers_and_choices():
    settings = sanitize_settings(
        {
            "appearance": {"terminal_font_size": 100, "tab_width": 20},
            "file_explorer": {"root_mode": "bad"},
            "terminal_editor": {"word_wrap": "yes", "line_numbers": "no"},
        }
    )

    assert settings["appearance"]["terminal_font_size"] == 28
    assert settings["appearance"]["tab_width"] == 120
    assert settings["file_explorer"]["root_mode"] == "fixed"
    assert settings["terminal_editor"]["word_wrap"] is True
    assert settings["terminal_editor"]["line_numbers"] is False


def test_settings_round_trip_with_atomic_template(tmp_path):
    path = tmp_path / "settings.yaml"
    payload = settings_payload(path)

    assert payload["settings"] == default_settings()
    assert path.exists()
    assert "YOLOmux user preferences" in path.read_text()

    updated = save_settings({"appearance": {"terminal_font_size": 17}, "file_explorer": {"quick_access_paths": ["/tmp", ""]}}, path)
    assert updated["settings"]["appearance"]["terminal_font_size"] == 17
    assert updated["settings"]["file_explorer"]["quick_access_paths"] == ["/tmp"]

    loaded, error = read_settings_file(path)
    assert error == ""
    assert loaded == updated["settings"]
