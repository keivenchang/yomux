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
