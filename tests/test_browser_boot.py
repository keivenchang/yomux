# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import json
import shlex
import subprocess
from urllib.parse import urlencode

import pytest
from selenium.common.exceptions import TimeoutException

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401
pytestmark = [pytest.mark.browser, pytest.mark.socket, pytest.mark.boot]

TOUCH_LONG_PRESS_TEST_TIMEOUT_SECONDS = 8


def xterm_only_search(session):
    return "?" + urlencode({
        "sessions": session,
        "layout": "slot1",
        "tabs": f"slot1:{session}",
    })


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
    runtime = start_isolated_browser_app(monkeypatch, tmp_path)
    session = runtime.sessions[0]
    server, thread = start_browser_server(monkeypatch, tmp_path, runtime.app, auth_bypass=True)
    base_url = f"http://127.0.0.1:{server.server_address[1]}/"
    install_live_runtime_boot_error_tracker(browser)
    browser._yolomux_server_log_boundary = server._fixture_server_log_boundary
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
        try:
            stop_browser_server(server, thread, browser=browser)
        finally:
            stop_isolated_browser_app(runtime)

    successor_path = tmp_path / "xterm-successor"
    successor_path.mkdir()
    successor_runtime = start_isolated_browser_app(monkeypatch, successor_path)
    successor_session = successor_runtime.sessions[0]
    successor_server, successor_thread = start_browser_server(
        monkeypatch,
        successor_path,
        successor_runtime.app,
        auth_bypass=True,
    )
    try:
        browser._yolomux_server_log_boundary = successor_server._fixture_server_log_boundary
        browser.get(
            f"http://127.0.0.1:{successor_server.server_address[1]}/"
            f"{xterm_only_search(successor_session)}"
        )
        assert_live_runtime_boot_healthy(browser, "full-bundle-xterm-successor", timeout=12)
        mounted = WebDriverWait(browser, 12).until(
            lambda driver: driver.execute_script(
                """
                const item = terminals.get(arguments[0]);
                return Boolean(
                  document.querySelector(`#term-${arguments[0]} .xterm`)
                  && item?.socket?.readyState === WebSocket.OPEN
                );
                """,
                successor_session,
            )
        )
        assert mounted is True
        assert_browser_journey_error_free(browser, observation_seconds=2.0)
    finally:
        try:
            stop_browser_server(
                successor_server,
                successor_thread,
                browser=browser,
            )
        finally:
            stop_isolated_browser_app(successor_runtime)


def test_hard_loaded_yoagent_keeps_activity_summary_disabled_without_requests_or_spinner(browser, monkeypatch, tmp_path):
    runtime = start_isolated_browser_app(monkeypatch, tmp_path)
    monkeypatch.setattr(
        runtime.app.yoagent_controller,
        "run_yoagent_cli_backend",
        lambda *_args, **_kwargs: ("fixture prewarm", "", {"elapsed_ms": 1}),
    )
    session = runtime.sessions[0]
    server, thread = start_browser_server(monkeypatch, tmp_path, runtime.app, auth_bypass=True)
    base_url = f"http://127.0.0.1:{server.server_address[1]}/"
    search = "?" + urlencode({
        "sessions": f"{session},yoagent",
        "layout": "slot1",
        "tabs": f"slot1:{session},yoagent",
    })
    try:
        browser._yolomux_server_log_boundary = server._fixture_server_log_boundary
        browser.get(base_url + search)
        assert_live_runtime_boot_healthy(browser, "activity-summary-disabled", timeout=12)
        selected = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            Promise.resolve(selectSession(yoagentItemId, {userInitiated: true}))
              .then(() => { activateYoagentPanel({scrollBottom: false}); done(true); })
              .catch(error => done({error: String(error)}));
            """
        )
        assert selected is True, selected
        state = WebDriverWait(browser, 12).until(
            lambda driver: driver.execute_script(
                """
                const disabled = document.querySelector('[data-activity-summary-disabled="true"]');
                const refresh = document.querySelector('[data-yoagent-refresh]');
                if (!disabled || !refresh) return null;
                return {
                  text: disabled.textContent.trim(),
                  refreshDisabled: refresh.disabled,
                  spinner: Boolean(document.querySelector('.yoagent-refresh-progress')),
                  activityRequests: performance.getEntriesByType('resource')
                    .map(entry => new URL(entry.name, location.href).pathname)
                    .filter(path => path === '/api/activity-summary'),
                };
                """
            )
        )
        assert state["text"]
        assert state["refreshDisabled"] is True
        assert state["spinner"] is False
        assert state["activityRequests"] == []
        assert_browser_journey_error_free(browser)
    finally:
        stop_browser_server(server, thread, browser=browser)
        stop_isolated_browser_app(runtime)


def test_real_xterm_trusted_touch_long_press_selects_extends_and_offers_copy(browser, monkeypatch, tmp_path):
    """CDP touch input must traverse the bridge, actual xterm selection, and copy menu."""
    runtime = start_isolated_browser_app(monkeypatch, tmp_path)
    session = runtime.sessions[0]
    server, thread = start_browser_server(monkeypatch, tmp_path, runtime.app, auth_bypass=True)
    marker = "real-xterm-touch-copy-marker"
    original_user_agent = browser.execute_script("return navigator.userAgent;")
    try:
        browser.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 Version/18.5 Mobile/15E148 Safari/604.1"})
        browser.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
        browser.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
        browser.get(f"http://127.0.0.1:{server.server_address[1]}/{xterm_only_search(session)}")
        assert_live_runtime_boot_healthy(browser, "real-xterm-trusted-touch", timeout=12)
        assert WebDriverWait(browser, 12).until(lambda driver: driver.execute_script("return Boolean(document.querySelector(`#term-${arguments[0]} .xterm`) && terminals.get(arguments[0])?.socket?.readyState === WebSocket.OPEN);", session))
        result = run_isolated_tmux(runtime.tmux, "send-keys", "-t", f"{session}:", f"printf '{marker} extension\\n'", "Enter")
        assert result.returncode == 0, result.stderr or result.stdout
        point = WebDriverWait(browser, 12).until(
            lambda driver: (
                geometry if (geometry := driver.execute_script(
                    """
                    const session = arguments[0], marker = arguments[1], item = terminals.get(session);
                    const container = document.querySelector(`#term-${session}`), screen = terminalScreenElement(container), term = item?.term, buffer = term?.buffer?.active;
                        const lineIndex = buffer ? Array.from({length: buffer.length}, (_, index) => index).filter(index => buffer.getLine(index)?.translateToString(true).trimStart().startsWith(`${marker} extension`)).at(-1) : -1;
                    const line = lineIndex >= 0 ? buffer.getLine(lineIndex).translateToString(true) : '', markerColumn = line.indexOf(marker), cell = terminalCellDimensions(term, container), rect = screen?.getBoundingClientRect(), viewportY = buffer?.viewportY || 0, cursorLine = (buffer?.baseY || 0) + (buffer?.cursorY || 0);
                    if (!rect || markerColumn < 0 || cursorLine <= lineIndex || lineIndex < viewportY || !(cell.width > 0) || !(cell.height > 0)) return null;
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
        stop_browser_server(server, thread, browser=browser)
        stop_isolated_browser_app(runtime)


@pytest.mark.parametrize(
    "mobile_viewports",
    [
        pytest.param(
            {
                "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 Version/18.5 Mobile/15E148 Safari/604.1",
                "portrait": {"width": 390, "height": 844},
                "keyboard": {"width": 390, "height": 520},
                "landscape": {"width": 844, "height": 390},
            },
            id="phone",
        ),
        pytest.param(
            {
                "user_agent": "Mozilla/5.0 (iPad; CPU OS 18_5 like Mac OS X) AppleWebKit/605.1.15 Version/18.5 Mobile/15E148 Safari/604.1",
                "portrait": {"width": 768, "height": 1024},
                "keyboard": {"width": 768, "height": 640},
                "landscape": {"width": 1024, "height": 768},
            },
            id="tablet",
        ),
    ],
)
def test_real_xterm_mobile_input_survives_pan_preview_pane_accessory_keyboard_and_rotation(browser, monkeypatch, tmp_path, mobile_viewports):
    preview_path = tmp_path / "mobile-preview.md"
    terminal_upload_path = tmp_path / "mobile-terminal-upload.txt"
    editor_upload_path = tmp_path / "mobile-editor-upload.png"
    preview_base = "# Mobile Preview\n\n" + "Preview row\n\n" * 80
    preview_path.write_text(preview_base, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", preview_path.name], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c", "core.hooksPath=/dev/null",
            "-c", "user.name=YOLOmux Tests",
            "-c", "user.email=yolomux-tests@example.invalid",
            "commit", "-qm", "mobile preview base",
        ],
        cwd=tmp_path,
        check=True,
    )
    preview_committed = preview_base + "Committed on mobile\n"
    preview_path.write_text(preview_committed, encoding="utf-8")
    subprocess.run(["git", "add", preview_path.name], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c", "core.hooksPath=/dev/null",
            "-c", "user.name=YOLOmux Tests",
            "-c", "user.email=yolomux-tests@example.invalid",
            "commit", "-qm", "update mobile preview",
        ],
        cwd=tmp_path,
        check=True,
    )
    preview_path.write_text(preview_committed + "Changed on mobile\n", encoding="utf-8")
    terminal_upload_path.write_text("mobile terminal upload\n", encoding="utf-8")
    editor_upload_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\xda"
        b"cd\xf8\x0f\x00\x01\x05\x01\x01'\x18\xe3f\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    runtime = start_isolated_browser_app(monkeypatch, tmp_path, session_count=2, session_cwd=tmp_path)
    session, other_session = runtime.sessions
    server, thread = start_browser_server(monkeypatch, tmp_path, runtime.app, auth_bypass=True)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    marker = "mobile-native-input"
    original_user_agent = browser.execute_script("return navigator.userAgent;")
    pane_heights = []

    def terminal_point(target_session):
        return WebDriverWait(browser, 12).until(
            lambda driver: (
                geometry if (geometry := driver.execute_script(
                    """
                    const session = arguments[0], item = terminals.get(session);
                    const terminal = document.querySelector(`#term-${session} .xterm`);
                    const buffer = item?.term?.buffer?.active;
                    const rendered = buffer
                      ? Array.from({length: buffer.length}, (_, index) => buffer.getLine(index)?.translateToString(true) || '').some(line => line.length > 0)
                      : false;
                    const rect = terminal?.getBoundingClientRect();
                    if (!rect || !(rect.width > 0) || !(rect.height > 0)
                        || item?.socket?.readyState !== WebSocket.OPEN || !item?.term?.textarea || !rendered
                        || (item.fitFrame || 0) !== 0 || (item.fitTimer || 0) !== 0) return null;
                    const points = [0.2, 0.5, 0.8].flatMap(xFraction =>
                      [0.25, 0.5, 0.75].map(yFraction => ({
                        x: rect.left + rect.width * xFraction,
                        y: rect.top + rect.height * yFraction,
                      }))
                    );
                    return points.find(point => terminal.contains(document.elementFromPoint(point.x, point.y))) || null;
                    """,
                    target_session,
                )) else False
            ),
            message=f"real xterm mobile input surface {target_session!r} did not mount",
        )

    def touch_tap(point, touch_id):
        browser.execute_cdp_cmd("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": point["x"], "y": point["y"], "id": touch_id}]})
        browser.execute_cdp_cmd("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

    def assert_app_geometry(stage):
        geometry = browser.execute_script(
            """
            const box = node => {
              const rect = node?.getBoundingClientRect();
              return rect ? {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height} : null;
            };
            const root = document.getElementById('appRoot');
            const grid = document.getElementById('grid');
            const topbar = document.querySelector('.topbar');
            return {
              root: box(root),
              grid: box(grid),
              topbar: box(topbar),
              viewport: {width: visualViewport.width, height: visualViewport.height},
              documentWidth: document.documentElement.scrollWidth,
            };
            """
        )
        assert geometry["root"] and geometry["grid"] and geometry["topbar"], {stage: geometry}
        assert geometry["root"]["left"] >= -1 and geometry["root"]["right"] <= geometry["viewport"]["width"] + 1, {stage: geometry}
        assert geometry["topbar"]["left"] >= -1 and geometry["topbar"]["right"] <= geometry["viewport"]["width"] + 1, {stage: geometry}
        assert geometry["grid"]["left"] >= -1 and geometry["grid"]["right"] <= geometry["viewport"]["width"] + 1, {stage: geometry}
        assert geometry["documentWidth"] <= geometry["viewport"]["width"] + 1, {stage: geometry}
        return geometry

    def assert_editor_surface_geometry(editor_item, mode):
        geometry = browser.execute_script(
            """
            const panel = panelNodes.get(arguments[0]);
            const editor = panel?.querySelector('.cm-editor');
            const content = panel?.querySelector('.cm-content');
            const rect = node => {
              const value = node?.getBoundingClientRect();
              return value ? {left: value.left, top: value.top, right: value.right, bottom: value.bottom, width: value.width, height: value.height} : null;
            };
            return {
              panel: rect(panel),
              editor: rect(editor),
              content: rect(content),
              text: content?.textContent || '',
              viewport: {width: visualViewport.width, height: visualViewport.height},
              mode: panel?._cmMode || '',
            };
            """,
            editor_item,
        )
        assert geometry["panel"] and geometry["editor"] and geometry["content"], {mode: geometry}
        assert geometry["mode"] == mode, geometry
        assert geometry["panel"]["left"] >= -1 and geometry["panel"]["right"] <= geometry["viewport"]["width"] + 1, geometry
        assert geometry["editor"]["width"] >= min(240, geometry["viewport"]["width"] - 24), geometry
        assert geometry["editor"]["height"] >= 80 and geometry["content"]["height"] > 0, geometry
        expected_text = "Changed on mobile" if mode == "diff" else "Mobile Preview"
        assert expected_text in geometry["text"], geometry
        return geometry

    def assert_upload_event(event_type, filename, session_name=None):
        def matching_event(_driver):
            events = runtime.app.event_log.tail(session=session_name, limit=30)
            return next((event for event in reversed(events) if event.get("type") == event_type and any(filename in path for path in event.get("details", {}).get("files", []))), False)

        return WebDriverWait(browser, 12).until(matching_event, message=f"{event_type} chooser upload did not reach the shared server upload owner")

    def touch_mobile_menu(touch_id):
        geometry = WebDriverWait(browser, 8).until(
            lambda driver: (
                value if (value := driver.execute_script(
                    """
                    const root = document.querySelector('.app-menu--nested-root') || document.querySelector('.app-menu');
                    const button = root?.querySelector(':scope > .app-menu-button');
                    const rect = button?.getBoundingClientRect();
                    return rect && rect.width > 0 && rect.height > 0
                      ? {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, width: rect.width, height: rect.height, menuId: root.dataset.appMenu}
                      : null;
                    """
                )) else False
            ),
            message="mobile topbar did not expose a usable menu owner",
        )
        assert geometry["height"] >= 36, geometry
        touch_tap(geometry, touch_id)
        opened = WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                const root = document.querySelector(`.app-menu[data-app-menu="${arguments[0]}"].open`);
                const sheet = root?.querySelector(':scope > .app-menu-popover');
                const rect = sheet?.getBoundingClientRect();
                return rect ? {
                  left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom,
                  width: rect.width, height: rect.height,
                  viewport: {width: visualViewport.width, height: visualViewport.height},
                  commands: sheet.querySelectorAll('.app-menu-command').length,
                } : null;
                """,
                geometry["menuId"],
            ),
            message="trusted touch did not open the mobile menu sheet",
        )
        assert opened["commands"] >= 5, opened
        assert opened["left"] >= -1 and opened["right"] <= opened["viewport"]["width"] + 1, opened
        assert opened["top"] >= -1 and opened["bottom"] <= opened["viewport"]["height"] + 1, opened
        touch_tap(geometry, touch_id + 1)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                "return !document.querySelector(`.app-menu[data-app-menu=\"${arguments[0]}\"].open`);",
                geometry["menuId"],
            )
        )

    def wait_for_terminal_acknowledgment(acknowledgment_path, expected, stage):
        try:
            return WebDriverWait(browser, 12).until(
                lambda _driver: (
                    text if acknowledgment_path.is_file()
                    and (text := acknowledgment_path.read_text(encoding="utf-8")) == expected
                    else False
                ),
                message=f"focused real xterm did not deliver native input after {stage}",
            )
        except TimeoutException as exc:
            state = browser.execute_script(
                """
                const item = terminals.get(arguments[0]), buffer = item?.term?.buffer?.active;
                return {
                  buffer: buffer ? Array.from({length: buffer.length}, (_, index) => buffer.getLine(index)?.translateToString(true) || '').join('\\n') : '',
                  phases: jsDebugEvents.filter(event => event.type === 'terminal_mobile_input_trace').map(event => event.phase),
                  socketState: item?.socket?.readyState ?? -1,
                  textareaValue: item?.term?.textarea?.value || '',
                };
                """,
                session,
            )
            state["acknowledgmentPath"] = str(acknowledgment_path)
            state["acknowledgmentExists"] = acknowledgment_path.is_file()
            state["acknowledgmentValue"] = (
                acknowledgment_path.read_text(encoding="utf-8", errors="replace")
                if acknowledgment_path.is_file()
                else ""
            )
            pane_capture = run_isolated_tmux(runtime.tmux, "capture-pane", "-p", "-S", "-100", "-t", f"{session}:")
            state["tmuxPane"] = pane_capture.stdout
            raise AssertionError(f"focused real xterm input state after {stage}: {state}") from exc

    def dispatch_terminal_enter():
        browser.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "text": "\r", "unmodifiedText": "\r"})
        browser.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})

    def arm_terminal_line_acknowledgment(name, prefix=""):
        ready_path = tmp_path / f"{name}.ready"
        acknowledgment_path = tmp_path / f"{name}.ack"
        command = (
            f"printf ready > {shlex.quote(ready_path.name)}; "
            f"IFS= read -r terminal_value; "
            f"printf '%s%s\\n' {shlex.quote(prefix)} \"$terminal_value\" > {shlex.quote(acknowledgment_path.name)}"
        )
        browser.execute_cdp_cmd("Input.insertText", {"text": command})
        dispatch_terminal_enter()
        assert wait_for_terminal_acknowledgment(
            ready_path,
            "ready",
            f"{name} line receiver readiness",
        ) == "ready"
        return acknowledgment_path

    def tap_terminal_and_send(stage, touch_id):
        point = terminal_point(session)
        browser.execute_script("document.body.tabIndex = -1; document.body.focus();")
        touch_tap(point, touch_id)
        focused = WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                "return document.activeElement === terminals.get(arguments[0])?.term?.textarea;",
                session,
            ),
            message=f"terminal tap after {stage} did not focus xterm's native textarea",
        )
        assert focused is True
        pane_geometry = browser.execute_script(
            """
            const panel = document.getElementById(`panel-${arguments[0]}`);
            const rect = panel?.getBoundingClientRect();
            return {height: rect?.height || 0, viewportHeight: visualViewport.height};
            """,
            session,
        )
        assert 120 <= pane_geometry["height"] <= pane_geometry["viewportHeight"] + 1, pane_geometry
        pane_heights.append({"stage": stage, **pane_geometry})
        stage_marker = f"{marker}-{stage}"
        acknowledgment_path = tmp_path / f"input-{touch_id}.ack"
        command = f"printf '%s\\n' {shlex.quote(stage_marker)} > {shlex.quote(acknowledgment_path.name)}"
        browser.execute_cdp_cmd("Input.insertText", {"text": command})
        dispatch_terminal_enter()
        return wait_for_terminal_acknowledgment(acknowledgment_path, f"{stage_marker}\n", stage)

    def touch_tab(target_session, touch_id):
        point = WebDriverWait(browser, 8).until(
            lambda driver: (
                geometry if (geometry := driver.execute_script(
                    """
                        const tab = document.querySelector(`.dockview-pane-tab[data-pane-tab="${arguments[0]}"]`);
                        tab?.scrollIntoView({block: 'nearest', inline: 'center'});
                        const rect = tab?.getBoundingClientRect();
                        if (!rect || !(rect.width > 0) || !(rect.height > 0)) return null;
                        const y = rect.top + rect.height / 2;
                        const hits = [0.2, 0.35, 0.5, 0.65, 0.8].map(fraction => {
                          const x = rect.left + rect.width * fraction;
                          const target = document.elementFromPoint(x, y);
                          return {x, target, label: target ? `${target.tagName}.${target.className}` : 'none'};
                        });
                        const hit = hits.find(candidate => tab.contains(candidate.target) && !candidate.target.closest('[data-pane-tab-close], [data-auto-session]'));
                        const stack = hits[0]?.target?.closest('.panel-toast-stack, .attention-alerts');
                        const stackRect = stack?.getBoundingClientRect();
                        return hit ? {x: hit.x, y} : {
                          blocked: hits.map(candidate => candidate.label),
                          bodyClass: document.body.className,
                          rect: {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width},
                          stack: stackRect ? {top: stackRect.top, bottom: stackRect.bottom, computedTop: getComputedStyle(stack).top} : null,
                        };
                    """,
                    target_session,
                )) else False
            ),
            message=f"mobile tab {target_session!r} did not become tappable",
        )
        assert "x" in point, point
        touch_tap(point, touch_id)
        WebDriverWait(browser, 8).until(
            lambda driver: driver.execute_script(
                "return activeItemForSide(slotForItem(arguments[0])) === arguments[0];",
                target_session,
            ),
            message=f"mobile tab tap did not activate {target_session!r}",
        )

    def touch_editor_mode(editor_item, mode, touch_id):
        point = WebDriverWait(browser, 8).until(
            lambda driver: (
                geometry if (geometry := driver.execute_script(
                    """
                    const panel = panelNodes.get(arguments[0]);
                    const button = panel?.querySelector(`[data-action="editor-mode"][data-editor-mode="${arguments[1]}"]`);
                    const rect = button?.getBoundingClientRect();
                    return rect && rect.width > 0 && rect.height > 0
                      ? {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, width: rect.width, height: rect.height}
                      : null;
                    """,
                    editor_item,
                    mode,
                )) else False
            ),
            message=f"mobile editor mode {mode!r} did not become tappable",
        )
        assert abs(point["width"] - 20) <= 1 and abs(point["height"] - 20) <= 1, point
        touch_tap(point, touch_id)
        WebDriverWait(browser, 8).until(
            lambda driver: driver.execute_script(
                "return editorViewModeFor(arguments[0], arguments[1]) === arguments[2];",
                str(preview_path),
                editor_item,
                mode,
            ),
            message=f"mobile editor mode touch did not activate {mode!r}",
        )

    def touch_editor_diff(editor_item, touch_id):
        point = WebDriverWait(browser, 8).until(
            lambda driver: (
                geometry if (geometry := driver.execute_script(
                    """
                    const panel = panelNodes.get(arguments[0]);
                    const button = panel?.querySelector('.file-editor-diff-panel:not([hidden])');
                    const rect = button?.getBoundingClientRect();
                    return rect && rect.width > 0 && rect.height > 0 && button.disabled !== true
                      ? {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, width: rect.width, height: rect.height}
                      : null;
                    """,
                    editor_item,
                )) else False
            ),
            message="mobile Diff action did not become tappable",
        )
        assert point["width"] >= 44 and abs(point["height"] - 20) <= 1, point
        touch_tap(point, touch_id)
        return WebDriverWait(browser, 12).until(
            lambda driver: driver.execute_script(
                """
                const panel = panelNodes.get(arguments[0]);
                return editorViewModeFor(arguments[1], arguments[0]) === 'diff' && panel?._cmMode === 'diff';
                """,
                editor_item,
                str(preview_path),
            ),
            message="mobile Diff action did not activate the shared diff surface",
        )

    try:
        browser.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": mobile_viewports["user_agent"]})
        browser.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {**mobile_viewports["portrait"], "deviceScaleFactor": 1, "mobile": True})
        browser.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
        search = "?" + urlencode({
            "debug": "1",
            "sessions": f"{session},{other_session}",
            "layout": "slot1",
            "tabs": f"slot1:{session},{other_session}",
        })
        browser.get(f"{origin}/{search}")
        assert_live_runtime_boot_healthy(browser, "real-xterm-mobile-input", timeout=12)
        assert_app_geometry("initial-mobile")
        touch_mobile_menu(21)
        touch_tab(session, 1)
        assert marker in tap_terminal_and_send("initial", 2)

        point = terminal_point(session)
        browser.execute_cdp_cmd("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": point["x"], "y": point["y"] + 80, "id": 3}]})
        browser.execute_cdp_cmd("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": point["x"], "y": point["y"] - 80, "id": 3}]})
        browser.execute_cdp_cmd("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        pan_trace = WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                "return jsDebugEvents.filter(event => event.type === 'terminal_mobile_input_trace' && event.phase === 'touch-decision').at(-1) || null;"
            ),
            message="terminal finger pan never reached the shared touch classifier",
        )
        assert pan_trace["decision"] == "vertical", pan_trace
        assert marker in tap_terminal_and_send("after-pan", 4)

        preview_item = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            openFileInEditor(arguments[0], {name: 'mobile-preview.md'}, {viewMode: 'preview', userInitiated: true})
              .then(item => done({item, active: activeItemForSide(slotForItem(item))}))
              .catch(error => done({error: String(error?.stack || error)}));
            """,
            str(preview_path),
        )
        assert preview_item.get("error") is None and preview_item["active"] == preview_item["item"], preview_item
        touch_tab(session, 5)
        assert marker in tap_terminal_and_send("after-preview", 6)

        launcher_point = WebDriverWait(browser, 8).until(
            lambda driver: (
                geometry if (geometry := driver.execute_script(
                    """
                    const launcher = document.querySelector(`[data-terminal-mobile-toggle="${arguments[0]}"]`);
                    const rect = launcher?.getBoundingClientRect();
                    return rect && rect.width > 0 && rect.height > 0
                      ? {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}
                      : null;
                    """,
                    session,
                )) else False
            ),
            message="mobile terminal accessory launcher did not render",
        )
        touch_tap(launcher_point, 10)
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return terminalMobileAccessoryState(arguments[0])?.open === true;", session))
        upload_point = browser.execute_script(
            """
            const upload = document.querySelector(`[data-terminal-mobile-key="upload"][data-terminal-mobile-session="${arguments[0]}"]`);
            const rect = upload?.getBoundingClientRect();
            return rect && rect.width > 0 && rect.height > 0
              ? {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, width: rect.width, height: rect.height}
              : null;
            """,
            session,
        )
        assert upload_point and upload_point["height"] >= 44, upload_point
        touch_tap(upload_point, 23)
        terminal_chooser = WebDriverWait(browser, 5).until(
            lambda driver: driver.find_element("css selector", '.file-upload-chooser[type="file"]')
        )
        assert terminal_chooser.get_attribute("accept") == ""
        terminal_chooser.send_keys(str(terminal_upload_path))
        terminal_upload_event = assert_upload_event("upload", terminal_upload_path.name, session)
        assert terminal_upload_event["session"] == session, terminal_upload_event
        # The terminal upload parent inserts the saved path at the prompt. Clear only this fixture's
        # pending readline buffer before the next native-input stage so the two journeys do not merge.
        cleared = run_isolated_tmux(runtime.tmux, "send-keys", "-t", f"{session}:", "C-u")
        assert cleared.returncode == 0, cleared.stderr or cleared.stdout
        close_point = browser.execute_script(
            """
            const close = document.querySelector(`[data-terminal-mobile-close="${arguments[0]}"]`);
            const rect = close.getBoundingClientRect();
            return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
            """,
            session,
        )
        touch_tap(close_point, 11)
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return terminalMobileAccessoryState(arguments[0])?.open === false;", session))
        assert marker in tap_terminal_and_send("after-accessory", 12)

        before_keyboard_viewport = browser.execute_script("return {width: visualViewport.width, height: visualViewport.height};")
        browser.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {**mobile_viewports["keyboard"], "deviceScaleFactor": 1, "mobile": True})
        after_keyboard_viewport = WebDriverWait(browser, 8).until(
            lambda driver: (
                viewport if (viewport := driver.execute_script("return {width: visualViewport.width, height: visualViewport.height};"))["height"] < before_keyboard_viewport["height"] else False
            ),
            message="software-keyboard viewport shrink did not reach visualViewport",
        )
        assert after_keyboard_viewport["width"] == before_keyboard_viewport["width"], after_keyboard_viewport
        assert marker in tap_terminal_and_send("after-keyboard-resize", 13)

        browser.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {**mobile_viewports["landscape"], "deviceScaleFactor": 1, "mobile": True})
        rotated_viewport = WebDriverWait(browser, 8).until(
            lambda driver: (
                viewport if (viewport := driver.execute_script("return {width: visualViewport.width, height: visualViewport.height};"))["width"] > viewport["height"] else False
            ),
            message="mobile rotation did not reach landscape visualViewport geometry",
        )
        assert rotated_viewport["width"] > before_keyboard_viewport["width"], rotated_viewport
        WebDriverWait(browser, 8).until(
            lambda driver: driver.execute_script(
                """
                const item = terminals.get(arguments[0]);
                return (item?.fitFrame || 0) === 0 && (item?.fitTimer || 0) === 0 && item?.remoteResizePending !== true;
                """,
                session,
            ),
            message="terminal rotation refit did not settle",
        )
        assert marker in tap_terminal_and_send("after-rotation", 14)

        native_input_acknowledgment = arm_terminal_line_acknowledgment("backspace")
        browser.execute_cdp_cmd("Input.insertText", {"text": "mobile-native-input-xx"})
        for _ in range(2):
            browser.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8})
            browser.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8})
        browser.execute_cdp_cmd("Input.insertText", {"text": "ok"})
        dispatch_terminal_enter()
        assert wait_for_terminal_acknowledgment(
            native_input_acknowledgment,
            "mobile-native-input-ok\n",
            "mobile-style text, Backspace, and Return",
        ) == "mobile-native-input-ok\n"

        native_ime_acknowledgment = arm_terminal_line_acknowledgment("ime", "mobile-native-ime-")
        composition_trace_start = browser.execute_script("return jsDebugEvents.length;")
        browser.execute_cdp_cmd("Input.imeSetComposition", {"text": "漢", "selectionStart": 1, "selectionEnd": 1})
        browser.execute_cdp_cmd("Input.insertText", {"text": "漢"})
        committed_composition = WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                const events = jsDebugEvents.slice(arguments[0])
                  .filter(event => event.type === 'terminal_mobile_input_trace');
                const compositionEnd = events.findIndex(event => event.phase === 'compositionend');
                if (compositionEnd < 0) return null;
                const committed = events.slice(compositionEnd + 1)
                  .find(event => event.phase === 'on-data' && event.bytes === arguments[1]);
                return committed || null;
                """,
                composition_trace_start,
                len("漢".encode("utf-8")),
            ),
            message="xterm did not emit the committed IME bytes after compositionend",
        )
        assert committed_composition["bytes"] == len("漢".encode("utf-8")), committed_composition
        dispatch_terminal_enter()
        assert wait_for_terminal_acknowledgment(
            native_ime_acknowledgment,
            "mobile-native-ime-漢\n",
            "one committed IME composition",
        ) == "mobile-native-ime-漢\n"

        paste_marker = "mobile-native-paste"
        native_paste_acknowledgment = arm_terminal_line_acknowledgment("paste")
        paste_dispatched = browser.execute_script(
            """
            const textarea = terminals.get(arguments[0])?.term?.textarea;
            const transfer = new DataTransfer();
            transfer.setData('text/plain', arguments[1]);
            return textarea?.dispatchEvent(new ClipboardEvent('paste', {clipboardData: transfer, bubbles: true, cancelable: true})) ?? false;
            """,
            session,
            paste_marker,
        )
        assert isinstance(paste_dispatched, bool)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                "return jsDebugEvents.some(event => event.type === 'terminal_mobile_input_trace' && event.phase === 'paste');"
            ),
            message="clipboard payload did not reach xterm's native paste event",
        )
        dispatch_terminal_enter()
        assert wait_for_terminal_acknowledgment(
            native_paste_acknowledgment,
            f"{paste_marker}\n",
            "one paste through xterm's native event path",
        ) == f"{paste_marker}\n"
        trace_phases = browser.execute_script(
            """
            return jsDebugEvents
              .filter(event => event.type === 'terminal_mobile_input_trace')
              .map(event => event.phase);
            """
        )
        assert "compositionstart" in trace_phases and "compositionend" in trace_phases and "paste" in trace_phases, trace_phases

        touch_tab(preview_item["item"], 15)
        touch_editor_mode(preview_item["item"], "edit", 16)
        editor_ready = WebDriverWait(browser, 8).until(
            lambda driver: driver.execute_script(
                "return Boolean(panelNodes.get(arguments[0])?._cmView?.contentDOM);",
                preview_item["item"],
            ),
            message="mobile Markdown editor did not expose the shared CodeMirror surface",
        )
        assert editor_ready is True
        assert_editor_surface_geometry(preview_item["item"], "edit")
        git_metadata_ready = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            refreshOpenFileGitMetadata(arguments[0]).then(ok => {
              const panel = panelNodes.get(arguments[1]);
              renderFileEditorPanel(panel, arguments[1]);
              const state = fileState.get(arguments[0]);
              const button = panel?.querySelector('.file-editor-diff-panel');
              done({
                ok,
                gitRoot: state?.gitRoot || '',
                gitTracked: state?.gitTracked === true,
                gitHasHistory: state?.gitHasHistory === true,
                gitHistory: state?.gitHistory || [],
                diffAvailable: openFileDiffAvailable(state),
                hidden: button?.hidden === true,
                disabled: button?.disabled === true,
              });
            }, error => done({error: String(error)}));
            """,
            str(preview_path),
            preview_item["item"],
        )
        assert git_metadata_ready.get("ok") is True, git_metadata_ready
        assert git_metadata_ready.get("hidden") is False and git_metadata_ready.get("disabled") is False, git_metadata_ready
        assert touch_editor_diff(preview_item["item"], 17) is True
        assert_editor_surface_geometry(preview_item["item"], "diff")
        touch_editor_mode(preview_item["item"], "edit", 24)
        WebDriverWait(browser, 8).until(
            lambda driver: driver.execute_script(
                "return Boolean(panelNodes.get(arguments[0])?._cmView?.contentDOM);",
                preview_item["item"],
            ),
            message="mobile Markdown editor did not return from Diff",
        )
        editor_upload_point = browser.execute_script(
            """
            const panel = panelNodes.get(arguments[0]);
            const upload = panel?.querySelector('.file-editor-upload-panel:not([hidden])');
            const rect = upload?.getBoundingClientRect();
            return rect && rect.width > 0 && rect.height > 0
              ? {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, width: rect.width, height: rect.height}
              : null;
            """,
            preview_item["item"],
        )
        assert editor_upload_point and abs(editor_upload_point["width"] - 20) <= 1 and abs(editor_upload_point["height"] - 20) <= 1, editor_upload_point
        touch_tap(editor_upload_point, 25)
        editor_chooser = WebDriverWait(browser, 5).until(
            lambda driver: driver.find_element("css selector", '.file-upload-chooser[type="file"]')
        )
        assert editor_chooser.get_attribute("accept") == "image/*"
        editor_chooser.send_keys(str(editor_upload_path))
        assert_upload_event("editor_upload", editor_upload_path.name)
        chooser_reference = WebDriverWait(browser, 12).until(
            lambda driver: (
                value if "![image](" in (value := driver.execute_script(
                    "return panelNodes.get(arguments[0])?._cmView?.state?.doc?.toString?.() || '';",
                    preview_item["item"],
                )) else False
            ),
            message="mobile editor native chooser did not insert one Markdown image reference",
        )
        assert chooser_reference.count("![image](") == 1, chooser_reference
        image_paste_claimed = browser.execute_script(
            """
            const panel = panelNodes.get(arguments[0]);
            const content = panel?._cmView?.contentDOM;
            if (!content) return false;
            content.focus();
            const png = Uint8Array.from(
              atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='),
              byte => byte.charCodeAt(0),
            );
            const clipboardData = new DataTransfer();
            clipboardData.items.add(new File([png], 'mobile-checkin.png', {type: 'image/png'}));
            const event = new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData});
            content.dispatchEvent(event);
            return event.defaultPrevented;
            """,
            preview_item["item"],
        )
        assert image_paste_claimed is True
        inserted_reference = WebDriverWait(browser, 12).until(
            lambda driver: (
                value if (value := driver.execute_script(
                    "return panelNodes.get(arguments[0])?._cmView?.state?.doc?.toString?.() || '';",
                    preview_item["item"],
                )).count("![image](") == 2 else False
            ),
            message="mobile editor image paste did not upload and insert one Markdown reference",
        )
        assert inserted_reference.count("![image](") == 2, inserted_reference
        touch_editor_mode(preview_item["item"], "preview", 18)
        preview_after_upload = WebDriverWait(browser, 8).until(
            lambda driver: driver.execute_script(
                """
                const panel = panelNodes.get(arguments[0]);
                return Boolean(panel?.querySelector('.file-editor-preview-pane-panel:not([hidden])'));
                """,
                preview_item["item"],
            ),
            message="mobile editor did not return to Preview through its shared mode action",
        )
        assert preview_after_upload is True
        assert_app_geometry("mobile-after-editor-and-uploads")
        touch_tab(session, 19)
        assert marker in tap_terminal_and_send("after-editor-upload", 20)
        assert len(pane_heights) == 7 and all(item["height"] >= 120 for item in pane_heights), pane_heights
        mobile_capture = browser.execute_script("return debugMobileCaptureSnapshot();")
        assert mobile_capture["debugArmed"] is True and "debug=1" in mobile_capture["url"], mobile_capture
        assert {item["item"] for item in mobile_capture["layout"]["activeItems"]} >= {session}, mobile_capture
        assert mobile_capture["layout"]["focusedItem"] == session and mobile_capture["layout"]["visualItem"] == session, mobile_capture
        assert any(item["item"] == preview_item["item"] and "markdown-body" in item["classes"] for item in mobile_capture["preview"]), mobile_capture
        assert {event["type"] for event in mobile_capture["events"]} >= {"terminal_mobile_input_trace"}, mobile_capture

        browser.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {"enabled": False})
        browser.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        browser.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": original_user_agent})
        desktop_geometry = WebDriverWait(browser, 8).until(
            lambda driver: (
                value if (value := driver.execute_script(
                    """
                    const menuIds = Array.from(document.querySelectorAll('.app-menu:not(.app-menu--nested-root)')).map(node => node.dataset.appMenu || '');
                    return !document.body.classList.contains('app-topbar-coarse-pointer') && menuIds.length >= 5
                      ? {menuIds, compactRoots: document.querySelectorAll('.app-menu--nested-root').length}
                      : null;
                    """
                )) else False
            ),
            message="wide desktop viewport did not restore full desktop menus and pointer geometry",
        )
        assert set(("file", "view", "tmux", "tabs", "help")).issubset(desktop_geometry["menuIds"]), desktop_geometry
        assert desktop_geometry["compactRoots"] == 0, desktop_geometry
        assert_app_geometry("wide-desktop-parity")
        assert_browser_journey_error_free(browser)
    finally:
        browser.execute_cdp_cmd("Browser.resetPermissions", {})
        browser.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {"enabled": False})
        browser.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})
        browser.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": original_user_agent})
        stop_browser_server(server, thread, browser=browser)
        stop_isolated_browser_app(runtime)


def test_real_xterm_renders_tmux_output_and_survives_pane_resize(browser, monkeypatch, tmp_path):
    """One isolated HTTP/WS smoke covers the real xterm path that fixture FakeTerminal cannot."""
    runtime = start_isolated_browser_app(monkeypatch, tmp_path)
    session = runtime.sessions[0]
    server, thread = start_browser_server(monkeypatch, tmp_path, runtime.app, auth_bypass=True)
    marker = "real-xterm-browser-smoke"
    try:
        browser.get(f"http://127.0.0.1:{server.server_address[1]}/{xterm_only_search(session)}")
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
        browser.set_window_size(1292, 1800)
        tall_fit = WebDriverWait(browser, 8).until(
            lambda driver: (
                metrics
                if (metrics := driver.execute_script(
                    """
                    const session = arguments[0];
                    const item = terminals.get(session);
                    const pane = document.querySelector(`#terminal-pane-${session}`);
                    const screen = pane?.querySelector('.xterm-screen');
                    const rowNodes = Array.from(screen?.querySelectorAll('.xterm-rows > div') || []);
                    const paneRect = pane?.getBoundingClientRect();
                    const screenRect = screen?.getBoundingClientRect();
                    const lastRowRect = rowNodes.at(-1)?.getBoundingClientRect();
                    const estimate = pane && item?.term ? estimateTerminalSize(pane, item.term) : null;
                    if (!paneRect || !screenRect || !lastRowRect || !estimate) return null;
                    return {
                      connected: item.socket?.readyState === WebSocket.OPEN,
                      paneHeight: paneRect.height,
                      paneBottom: paneRect.bottom,
                      screenHeight: screenRect.height,
                      screenBottom: screenRect.bottom,
                      lastRowBottom: lastRowRect.bottom,
                      lastRowOverflow: lastRowRect.bottom - paneRect.bottom,
                      renderedRows: rowNodes.length,
                      terminalRows: item.term.rows,
                      estimatedRows: estimate.rows,
                      measuredCell: estimate.measuredCell,
                      cellHeight: estimate.cellHeight,
                    };
                    """,
                    session,
                )) and metrics["connected"] and metrics["paneHeight"] > 1000 and metrics["renderedRows"] == metrics["terminalRows"] == metrics["estimatedRows"]
                else False
            ),
            message="real xterm did not converge to its measured tall-pane row count",
        )
        assert tall_fit["measuredCell"] == "renderer", tall_fit
        assert tall_fit["cellHeight"] > 0 and tall_fit["paneHeight"] > 1000, tall_fit
        assert tall_fit["screenHeight"] <= tall_fit["paneHeight"] + 1, tall_fit
        assert tall_fit["screenBottom"] <= tall_fit["paneBottom"] + 1, tall_fit
        assert tall_fit["lastRowOverflow"] <= 1, tall_fit
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
        repeated = run_isolated_tmux(runtime.tmux, "send-keys", "-t", f"{session}:", f"printf '{marker}\\n'", "Enter")
        assert repeated.returncode == 0, repeated.stderr or repeated.stdout
        touch_trace = WebDriverWait(browser, 8).until(
            lambda driver: (
                trace
                if (trace := driver.execute_script(
                    """
                    const session = arguments[0];
                    const marker = arguments[1];
                    const item = terminals.get(session);
                    const container = document.querySelector(`#term-${session}`);
                    const screen = terminalScreenElement(container);
                    const term = item?.term;
                    const buffer = term?.buffer?.active;
                    const lineIndex = buffer
                      ? (Array.from({length: buffer.length}, (_, index) => index).filter(index => buffer.getLine(index)?.translateToString(true).trim() === marker).at(-1) ?? -1)
                      : -1;
                    const line = lineIndex >= 0 ? buffer.getLine(lineIndex).translateToString(true) : '';
                    const markerColumn = line.indexOf(marker);
                    const cell = terminalCellDimensions(term, container);
                    const rect = screen?.getBoundingClientRect();
                    if (lineIndex < 0 || markerColumn < 0 || !rect || !(cell.width > 0) || !(cell.height > 0)) return null;
                    const viewportY = buffer?.viewportY || 0;
                    const x = rect.left + (markerColumn + 0.5) * cell.width;
                    const y = rect.top + (lineIndex - viewportY + 0.5) * cell.height;
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
        stop_browser_server(server, thread, browser=browser)
        stop_isolated_browser_app(runtime)
