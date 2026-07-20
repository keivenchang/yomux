import json
import subprocess
from pathlib import Path

from yolomux_lib.tmux_theme import apply_tmux_theme_color_to_existing
from yolomux_lib.tmux_theme import normalized_tmux_theme_color
from yolomux_lib.tmux_theme import tmux_theme_commands_for_existing
from yolomux_lib.tmux_theme import tmux_theme_commands_for_new_session
from yolomux_lib.tmux_theme import tmux_theme_color_from_settings


UI_PINS = json.loads((Path(__file__).parent / "fixtures" / "ui_pins.json").read_text(encoding="utf-8"))
TMUX_BLUE = UI_PINS["textSelectionBg"]


def completed(args, stdout=""):
    return subprocess.CompletedProcess(args, 0, stdout, "")


def test_tmux_theme_commands_apply_globals_sessions_and_windows():
    commands = tmux_theme_commands_for_existing("blue", ["1", "8003"], ["1:0", "8003:2"])

    assert ["set-option", "-g", "status-style", f"bg={TMUX_BLUE},fg=#ffffff"] in commands
    assert ["set-option", "-t", "1:", "status-style", f"bg={TMUX_BLUE},fg=#ffffff"] in commands
    assert ["set-window-option", "-t", "8003:2", "window-status-current-style", "bg=#3b82f6,fg=#ffffff,bold"] in commands
    assert ["set-window-option", "-t", "1:0", "pane-active-border-style", f"fg={TMUX_BLUE}"] in commands
    assert commands[-1] == ["refresh-client", "-S"]


def test_tmux_theme_green_uses_tmux_native_green():
    commands = tmux_theme_commands_for_existing("green", ["1"], ["1:0"])

    assert ["set-option", "-g", "status-style", "bg=green,fg=black"] in commands
    assert ["set-window-option", "-g", "window-status-current-style", "bg=green,fg=black,bold"] in commands
    assert ["set-option", "-t", "1:", "status-left-style", "bg=green,fg=black"] in commands
    assert ["set-window-option", "-t", "1:0", "pane-active-border-style", "fg=green"] in commands


def test_tmux_theme_new_session_commands_set_global_defaults_and_session():
    commands = tmux_theme_commands_for_new_session("purple", "9")

    assert ["set-option", "-g", "status-style", "bg=#7c3aed,fg=#ffffff"] in commands
    assert ["set-option", "-t", "9:", "status-style", "bg=#7c3aed,fg=#ffffff"] in commands
    assert ["set-window-option", "-t", "9:", "pane-border-style", "fg=#6d28d9"] in commands
    assert commands[-1] == ["refresh-client", "-S"]


def test_tmux_theme_color_normalizes_from_settings():
    assert tmux_theme_color_from_settings({"appearance": {"active_color": "orange"}}) == "orange"
    assert tmux_theme_color_from_settings({"appearance": {"active_color": "not-real"}}) == "green"
    assert normalized_tmux_theme_color(None) == "green"


def test_apply_tmux_theme_color_to_existing_lists_then_updates_every_target():
    calls = []

    def fake_tmux(args, timeout=5.0):
        calls.append((args, timeout))
        if args[:2] == ["list-sessions", "-F"]:
            return completed(args, "1\n2\n")
        if args[:3] == ["list-windows", "-a", "-F"]:
            return completed(args, "1:0\n2:1\n")
        return completed(args)

    result = apply_tmux_theme_color_to_existing("blue", runner=fake_tmux)

    assert result["applied"] is True
    assert calls[0][0] == ["list-sessions", "-F", "#{session_name}"]
    assert calls[1][0] == ["list-windows", "-a", "-F", "#{session_name}:#{window_index}"]
    assert (["set-option", "-t", "2:", "status-right-style", f"bg={TMUX_BLUE},fg=#ffffff"], 3.0) in calls
    assert (["set-window-option", "-t", "2:1", "pane-active-border-style", f"fg={TMUX_BLUE}"], 3.0) in calls
    assert calls[-1][0] == ["refresh-client", "-S"]


def test_apply_tmux_theme_color_treats_detached_refresh_as_success():
    def fake_tmux(args, timeout=5.0):
        if args[:2] == ["list-sessions", "-F"]:
            return completed(args, "1\n")
        if args[:3] == ["list-windows", "-a", "-F"]:
            return completed(args, "1:0\n")
        if args == ["refresh-client", "-S"]:
            return subprocess.CompletedProcess(args, 1, "", "no current client")
        return completed(args)

    result = apply_tmux_theme_color_to_existing("blue", runner=fake_tmux)

    assert result["applied"] is True
    assert result["errors"] == []
