# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import json
from urllib.parse import urlencode

import pytest
from selenium.common.exceptions import TimeoutException

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401
pytestmark = [pytest.mark.browser, pytest.mark.socket, pytest.mark.boot]

TOUCH_LONG_PRESS_TEST_TIMEOUT_SECONDS = 8


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


def test_real_xterm_trusted_touch_long_press_selects_extends_and_offers_copy(browser, monkeypatch, tmp_path):
    """CDP touch input must traverse the bridge, actual xterm selection, and copy menu."""
    runtime = start_isolated_browser_share_app(monkeypatch, tmp_path)
    session = runtime.sessions[0]
    server, thread = start_browser_share_server(monkeypatch, tmp_path, runtime.app, auth_bypass=True)
    marker = "real-xterm-touch-copy-marker"
    original_user_agent = browser.execute_script("return navigator.userAgent;")
    try:
        browser.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 Version/18.5 Mobile/15E148 Safari/604.1"})
        browser.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
        browser.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
        browser.get(f"http://127.0.0.1:{server.server_address[1]}/?" + urlencode({"sessions": session}))
        assert_live_runtime_boot_healthy(browser, "real-xterm-trusted-touch", timeout=12)
        assert WebDriverWait(browser, 12).until(lambda driver: driver.execute_script("return Boolean(document.querySelector(`#term-${arguments[0]} .xterm`) && terminals.get(arguments[0])?.socket?.readyState === WebSocket.OPEN);", session))
        result = run_isolated_tmux(runtime.tmux, "send-keys", "-t", f"{session}:", f"printf '{marker} extension\\n'", "Enter")
        assert result.returncode == 0, result.stderr or result.stdout
        point = WebDriverWait(browser, 12).until(
            lambda driver: (
                geometry if (geometry := driver.execute_script(
                    """
                    const session = arguments[0], marker = arguments[1], item = terminals.get(session);
                    const container = document.querySelector(`#term-${session}`), screen = container?.querySelector('.xterm-screen'), term = item?.term, buffer = term?.buffer?.active;
                        const lineIndex = buffer ? Array.from({length: buffer.length}, (_, index) => index).filter(index => buffer.getLine(index)?.translateToString(true).trimStart().startsWith(`${marker} extension`)).at(-1) : -1;
                    const line = lineIndex >= 0 ? buffer.getLine(lineIndex).translateToString(true) : '', markerColumn = line.indexOf(marker), cell = terminalCellDimensions(term, container), rect = screen?.getBoundingClientRect(), viewportY = buffer?.viewportY || 0;
                    if (!rect || markerColumn < 0 || lineIndex < viewportY || !(cell.width > 0) || !(cell.height > 0)) return null;
                    const x = rect.left + (markerColumn + 0.5) * cell.width, y = rect.top + (lineIndex - viewportY + 0.5) * cell.height, events = [];
                    const observe = event => events.push({type: event.type, trusted: event.isTrusted, pointerType: event.pointerType || '', syntheticContext: touchContextMenuSyntheticEvents.has(event)});
                    document.addEventListener('pointerdown', observe, true); document.addEventListener('contextmenu', observe, true); window.__realXtermTouchLongPressProbe = {events, observe, copied: []}; term.clearSelection();
                    return {x, y, extendX: x + cell.width * (marker.length + 2)};
                    """, session, marker)) else False),
            message=f"real xterm never rendered {marker!r}",
        )
        browser.execute_cdp_cmd("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": point["x"], "y": point["y"], "id": 1}]})
        try:
            selected = WebDriverWait(browser, TOUCH_LONG_PRESS_TEST_TIMEOUT_SECONDS).until(
                lambda driver: (
                    state if (state := driver.execute_script(
                        """
                        const term = terminals.get(arguments[0])?.term, menu = document.querySelector('.terminal-context-menu'), probe = window.__realXtermTouchLongPressProbe;
                        return menu && term?.getSelection?.() === arguments[1] ? {events: probe?.events || [], selection: term.getSelection(), copy: Array.from(menu.querySelectorAll('button')).map(button => ({label: button.textContent || '', disabled: button.disabled}))} : null;
                        """, session, marker)) else False),
                message="trusted CDP touch did not reach the terminal long-press bridge",
            )
        except TimeoutException as exc:
            state = browser.execute_script(
                """
                const term = terminals.get(arguments[0])?.term, menu = document.querySelector('.terminal-context-menu'), probe = window.__realXtermTouchLongPressProbe;
                return {events: probe?.events || [], selection: term?.getSelection?.() || '', menuOpen: Boolean(menu), copy: Array.from(menu?.querySelectorAll('button') || []).map(button => ({label: button.textContent || '', disabled: button.disabled}))};
                """,
                session,
            )
            raise AssertionError(f"trusted CDP touch long-press state: {state}") from exc
        browser.execute_script(
            """
            const probe = window.__realXtermTouchLongPressProbe;
            probe.originalExecCommand = document.execCommand;
            document.execCommand = command => {
              if (command !== 'copy') return false;
              const clipboardData = new DataTransfer();
              const event = new Event('copy', {bubbles: true, cancelable: true});
              Object.defineProperty(event, 'clipboardData', {value: clipboardData});
              document.dispatchEvent(event);
              probe.copied.push(clipboardData.getData('text/plain'));
              return true;
            };
            const menu = document.querySelector('.terminal-context-menu');
            const copy = Array.from(menu?.querySelectorAll('button') || []).find(button => button.textContent.trim() === 'Copy');
            copy?.click();
            """
        )
        copied = WebDriverWait(browser, TOUCH_LONG_PRESS_TEST_TIMEOUT_SECONDS).until(
            lambda driver: (
                values if (values := driver.execute_script("return window.__realXtermTouchLongPressProbe?.copied || [];")) and marker in values else False
            ),
            message="touch-selected terminal menu Copy did not write the captured word",
        )
        browser.execute_cdp_cmd("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": point["extendX"], "y": point["y"], "id": 1}]})
        extended = WebDriverWait(browser, TOUCH_LONG_PRESS_TEST_TIMEOUT_SECONDS).until(lambda driver: (selection if (selection := driver.execute_script("return terminals.get(arguments[0])?.term?.getSelection?.() || '';", session)).startswith(marker) and len(selection) > len(marker) else False), message="touch move after a real long press did not extend xterm selection")
        browser.execute_cdp_cmd("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        assert any(event["type"] == "pointerdown" and event["trusted"] and event["pointerType"] == "touch" for event in selected["events"]), selected
        assert any(event["type"] == "contextmenu" and event["syntheticContext"] for event in selected["events"]), selected
        assert any(action["label"] == "Copy" and action["disabled"] is False for action in selected["copy"]), selected
        assert marker in copied, copied
        assert extended.startswith(marker) and len(extended) > len(marker), extended
    finally:
        browser.execute_script("""const probe = window.__realXtermTouchLongPressProbe; if (probe?.observe) { document.removeEventListener('pointerdown', probe.observe, true); document.removeEventListener('contextmenu', probe.observe, true); if (probe.originalExecCommand) document.execCommand = probe.originalExecCommand; }""")
        browser.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {"enabled": False})
        browser.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})
        browser.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": original_user_agent})
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
                    const buffer = item?.term?.buffer?.active;
                    const text = buffer
                      ? Array.from({length: buffer.length}, (_, index) => buffer.getLine(index)?.translateToString(true) || '').join('\\n')
                      : '';
                    const screen = terminal?.querySelector('.xterm-screen');
                    return {
                      text,
                      rows: buffer?.length || 0,
                      rect: terminal?.getBoundingClientRect().toJSON?.() || null,
                      screenRect: screen?.getBoundingClientRect().toJSON?.() || null,
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
                    const buffer = item?.term?.buffer?.active;
                    const text = buffer
                      ? Array.from({length: buffer.length}, (_, index) => buffer.getLine(index)?.translateToString(true) || '').join('\\n')
                      : '';
                    const screen = terminal?.querySelector('.xterm-screen');
                    return {
                      text, rect: rect?.toJSON?.() || null,
                      screenRect: screen?.getBoundingClientRect().toJSON?.() || null,
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
        assert glyphs["screenRect"] and glyphs["screenRect"]["width"] > 0 and glyphs["screenRect"]["height"] > 0, glyphs
        touch_trace = WebDriverWait(browser, 8).until(
            lambda driver: (
                trace
                if (trace := driver.execute_script(
                    """
                    const session = arguments[0];
                    const marker = arguments[1];
                    const item = terminals.get(session);
                    const container = document.querySelector(`#term-${session}`);
                    const screen = container?.querySelector('.xterm-screen');
                    const term = item?.term;
                    const buffer = term?.buffer?.active;
                    const lineIndex = buffer
                      ? Array.from({length: buffer.length}, (_, index) => index).filter(index => buffer.getLine(index)?.translateToString(true).includes(marker)).at(-1)
                      : -1;
                    const line = lineIndex >= 0 ? buffer.getLine(lineIndex).translateToString(true) : '';
                    const markerColumn = line.indexOf(marker);
                    const cell = terminalCellDimensions(term, container);
                    const rect = screen?.getBoundingClientRect();
                    const viewportY = buffer?.viewportY || 0;
                    const x = rect ? rect.left + (markerColumn + 0.5) * cell.width : 0;
                    const y = rect ? rect.top + (lineIndex - viewportY + 0.5) * cell.height : 0;
                    const trace = [];
                    const selected = [];
                    const originalSelect = term?.select?.bind(term);
                    if (originalSelect) term.select = (...args) => { selected.push(args); return originalSelect(...args); };
                    const observe = event => trace.push({
                      phase: event.eventPhase,
                      defaultPrevented: event.defaultPrevented,
                      cancelBubble: event.cancelBubble,
                      touchSelection: event.yolomuxTerminalTouchSelection?.text || '',
                      synthetic: touchContextMenuSyntheticEvents.has(event),
                    });
                    container?.addEventListener('contextmenu', observe, true);
                    container?.addEventListener('contextmenu', observe);
                    const direct = terminalTouchWordSelectionAtClientPoint(term, container, x, y);
                    term?.clearSelection?.();
                    const handled = dispatchTouchContextMenu(screen, x, y);
                    const menu = document.querySelector('.terminal-context-menu');
                    const result = {
                      lineIndex, markerColumn, x, y, direct, handled, selected,
                      selection: term?.getSelection?.() || '',
                      menuText: menu?.textContent || '',
                      menuActions: Array.from(menu?.querySelectorAll('button') || []).map(button => ({
                        label: button.textContent || '', disabled: button.disabled,
                      })),
                      menuOpen: Boolean(menu), trace,
                    };
                    if (originalSelect) term.select = originalSelect;
                    container?.removeEventListener('contextmenu', observe, true);
                    container?.removeEventListener('contextmenu', observe);
                    return result;
                    """,
                    session,
                    marker,
                )) and trace["direct"] and trace["handled"]
                else False
            ),
            message="real xterm long-press trace did not reach its point-to-word selection helper",
        )
        assert touch_trace["selected"], touch_trace
        assert touch_trace["selection"] == marker, touch_trace
        assert any(entry["synthetic"] and entry["touchSelection"] == marker for entry in touch_trace["trace"]), touch_trace
        assert touch_trace["menuOpen"], touch_trace
        assert any(action["label"] == "Copy" and action["disabled"] is False for action in touch_trace["menuActions"]), touch_trace
        assert "Copy" in touch_trace["menuText"], touch_trace
        # The live terminal stays connected and keeps its actual xterm glyphs after a real viewport
        # resize.  Its pane can legitimately be width-capped by the current saved layout.
        assert after["connected"] is True and after["viewport"] > glyphs["viewport"], {"before": glyphs, "after": after}
        assert after["screenRect"] and after["screenRect"]["width"] > 0 and after["screenRect"]["height"] > 0, after
    finally:
        stop_browser_share_server(server, thread)
        stop_isolated_browser_share_app(runtime)
