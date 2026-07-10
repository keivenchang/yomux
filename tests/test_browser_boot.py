# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import json
from urllib.parse import urlencode

import pytest

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401
pytestmark = [pytest.mark.browser, pytest.mark.socket, pytest.mark.boot]


def saved_layout_state(session):
    return {
        "v": 1,
        "finder": {
            "root": str(REPO_ROOT),
            "rootMode": "sync",
            "mode": "files",
            "session": session,
            "showHidden": False,
            "expanded": [str(REPO_ROOT)],
        },
        "preferences": {
            "searchText": "",
            "collapsedSections": ["Appearance", "File Explorer"],
            "resetConfirmVisible": False,
        },
        "scroll": [
            {"target": "preferences", "kind": "preferences", "top": 0, "left": 0},
            {"target": "finder:files", "kind": "finder", "top": 0, "left": 0, "mode": "files"},
        ],
    }


def saved_layout_search(session):
    return "?" + urlencode({
        "bootCase": "saved-layout",
        "sessions": f"files,{session},debug,prefs",
        "layout": "row@20(slot1,row@50(left,slot2))",
        "tabs": f"slot1:files;left:{session};slot2:debug,prefs",
        "finder": "files",
        "state": json.dumps(saved_layout_state(session), separators=(",", ":")),
    })


def test_full_bundle_boot_smoke_matrix_never_renders_a_blank_page(browser, monkeypatch, tmp_path):
    runtime = start_isolated_browser_share_app(monkeypatch, tmp_path)
    session = runtime.sessions[0]
    server, thread = start_browser_share_server(monkeypatch, tmp_path, runtime.app, auth_bypass=True)
    base_url = f"http://127.0.0.1:{server.server_address[1]}/"
    install_live_runtime_boot_error_tracker(browser)
    cases = {
        "fresh-default": "?" + urlencode({"bootCase": "fresh-default", "sessions": session}),
        "saved-layout": saved_layout_search(session),
        "malformed-state": "?" + urlencode({"bootCase": "malformed-state", "sessions": session, "state": "{not-json"}),
        "invalid-layout": "?" + urlencode({"bootCase": "invalid-layout", "sessions": session, "layout": "not-a-layout", "tabs": "broken"}),
    }
    try:
        for case_name, search in cases.items():
            browser.get(base_url + search)
            metrics = assert_live_runtime_boot_healthy(browser, case_name, timeout=12)
            if case_name == "saved-layout":
                assert "appearance" in metrics["collapsedPreferenceSectionIds"], metrics
                assert "file_explorer" in metrics["collapsedPreferenceSectionIds"], metrics
    finally:
        stop_browser_share_server(server, thread)
        stop_isolated_browser_share_app(runtime)


def test_real_xterm_renders_tmux_output_and_survives_pane_resize(browser, monkeypatch, tmp_path):
    """One isolated HTTP/WS smoke covers the real xterm path that fixture FakeTerminal cannot."""
    runtime = start_isolated_browser_share_app(monkeypatch, tmp_path)
    session = runtime.sessions[0]
    server, thread = start_browser_share_server(monkeypatch, tmp_path, runtime.app, auth_bypass=True)
    marker = "real-xterm-browser-smoke"
    try:
        browser.get(f"http://127.0.0.1:{server.server_address[1]}/?" + urlencode({"sessions": session}))
        assert_live_runtime_boot_healthy(browser, "real-xterm", timeout=12)
        mounted = WebDriverWait(browser, 12).until(
            lambda driver: driver.execute_script(
                """
                const term = terminals.get(arguments[0]);
                const node = document.querySelector(`#term-${arguments[0]} .xterm`);
                return Boolean(node && term?.socket?.readyState === WebSocket.OPEN);
                """,
                session,
            )
        )
        assert mounted is True
        result = run_isolated_tmux(runtime.tmux, "send-keys", "-t", f"{session}:", f"printf '{marker}\\n'", "Enter")
        assert result.returncode == 0, result.stderr or result.stdout
        glyphs = WebDriverWait(browser, 12).until(
            lambda driver: (
                metrics
                if (metrics := driver.execute_script(
                    """
                    const terminal = document.querySelector(`#term-${arguments[0]} .xterm`);
                    const item = terminals.get(arguments[0]);
                    return {
                      text: terminal?.textContent || '',
                      rows: terminal?.querySelectorAll('.xterm-rows > div').length || 0,
                      rect: terminal?.getBoundingClientRect().toJSON?.() || null,
                      cols: item?.term?.cols || 0,
                      terminalRows: item?.term?.rows || 0,
                      connected: item?.socket?.readyState === WebSocket.OPEN,
                      viewport: window.innerWidth,
                    };
                    """,
                    session,
                )) and marker in metrics["text"]
                else False
            ),
            message=f"real xterm never rendered {marker!r}",
        )
        browser.set_window_size(1320, 820)
        after = WebDriverWait(browser, 8).until(
            lambda driver: (
                metrics
                if (metrics := driver.execute_script(
                    """
                    const terminal = document.querySelector(`#term-${arguments[0]} .xterm`);
                    const rect = terminal?.getBoundingClientRect();
                    const item = terminals.get(arguments[0]);
                    return {
                      text: terminal?.textContent || '', rect: rect?.toJSON?.() || null,
                      cols: item?.term?.cols || 0, terminalRows: item?.term?.rows || 0,
                      connected: item?.socket?.readyState === WebSocket.OPEN, viewport: window.innerWidth,
                    };
                    """,
                    session,
                )) and marker in metrics["text"] and metrics["rect"] and metrics["rect"]["width"] > 0
                else False
            ),
        )
        assert glyphs["rows"] > 0 and glyphs["cols"] > 0 and glyphs["terminalRows"] > 0, glyphs
        # The live terminal stays connected and keeps its actual xterm glyphs after a real viewport
        # resize.  Its pane can legitimately be width-capped by the current saved layout.
        assert after["connected"] is True and after["viewport"] > glyphs["viewport"], {"before": glyphs, "after": after}
    finally:
        stop_browser_share_server(server, thread)
        stop_isolated_browser_share_app(runtime)
