# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import json
import shlex
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from selenium.common.exceptions import TimeoutException

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401
pytestmark = [pytest.mark.browser, pytest.mark.socket, pytest.mark.boot]

TOUCH_LONG_PRESS_TEST_TIMEOUT_SECONDS = 8


def _suspend_synthetic_terminal_output(browser, session):
    browser.execute_script(
        """
        const socket = terminals.get(arguments[0])?.socket;
        if (!socket) throw new Error('synthetic terminal socket is missing');
        // Live PTY redraws can replace a test's injected history or switch buffers.
        window.__fixtureSyntheticTerminalSocketOwner = {socket, onmessage: socket.onmessage, send: socket.send};
        socket.onmessage = null;
        """,
        session,
    )


def _restore_synthetic_terminal_output(browser):
    browser.execute_script(
        """
        const owner = window.__fixtureSyntheticTerminalSocketOwner;
        if (owner) {
          owner.socket.onmessage = owner.onmessage;
          owner.socket.send = owner.send;
        }
        delete window.__fixtureSyntheticTerminalSocketOwner;
        """
    )


@pytest.mark.parametrize("action", ["release", "replace", "dispose", "late"])
def test_lifecycle_scope_cancels_native_animation_frames_without_illegal_receiver(browser, action):
    source = (Path(__file__).resolve().parents[1] / "static_src/js/yomux/10_core_utils.js").read_text()
    start = source.index("function createLifecycleScope(")
    scope_source = source[start:source.index("\nfunction createLatestResource(", start)]
    original_handle = browser.current_window_handle
    browser.switch_to.new_window("tab")
    try:
        browser.get("about:blank")
        receipt = browser.execute_async_script(scope_source + """
            const action = arguments[0];
            const done = arguments[arguments.length - 1];
            const scope = createLifecycleScope();
            let ran = false;
            const frame = requestAnimationFrame(() => { ran = true; });
            let error = null;
            try {
              if (action === 'late') scope.dispose();
              scope.replace('dispatch-frame', frame, cancelAnimationFrame);
              if (action === 'release') scope.release('dispatch-frame', frame);
              if (action === 'replace') {
                scope.replace('dispatch-frame', requestAnimationFrame(() => {}), cancelAnimationFrame);
              }
              scope.dispose();
            } catch (caught) { error = String(caught); }
            requestAnimationFrame(() => done({error, ran}));
        """, action)
        assert receipt == {"error": None, "ran": False}, receipt
    finally:
        browser.close()
        browser.switch_to.window(original_handle)




def xterm_only_search(session):
    return "?" + urlencode({
        "sessions": session,
        "layout": "slot1",
        "tabs": f"slot1:{session}",
    })


def saved_layout_state(session, finder_root):
    return {
        "v": 1,
        "finder": {
            "root": str(finder_root),
            "rootMode": "sync",
            "mode": "files",
            "session": session,
            "showHidden": False,
            "expanded": [str(finder_root)],
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


def saved_layout_search(session, finder_root):
    return "?" + urlencode({
        "bootCase": "saved-layout",
        "sessions": f"files,{session},debug,prefs",
        "layout": "row@20(slot1,row@50(left,slot2))",
        "tabs": f"slot1:files;left:{session};slot2:debug,prefs",
        "finder": "files",
        "state": json.dumps(saved_layout_state(session, finder_root), separators=(",", ":")),
    })
