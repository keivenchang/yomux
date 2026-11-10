# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401
from tests.browser_helpers.browser_layout import load_static_html_fixture
from tests.browser_helpers.browser_layout import register_browser_new_document_script
from tests.browser_helpers.browser_console import assert_only_expected_browser_http_error
pytestmark = [pytest.mark.browser, pytest.mark.socket, pytest.mark.boot]

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


@pytest.mark.parametrize("opening", ["webdriver", "script"])
@pytest.mark.parametrize("late_error", [False, True])
def test_browser_close_snapshots_the_live_realm_in_the_close_task(browser, tmp_path, opening, late_error):
    original = browser.current_window_handle
    if opening == "script":
        browser.execute_script("window.__diagnosticPopup = window.open('about:blank');")
        browser.switch_to.window(next(handle for handle in browser.window_handles if handle != original))
    else:
        browser.switch_to.new_window("tab")
    handle = browser.current_window_handle
    for name in ['close-first.html', 'close-second.html']:
        load_static_html_fixture(browser, tmp_path, name, '<html><head></head><body>Close qualification</body></html>')
    assert browser.execute_script('return history.length;') >= 2
    browser.execute_script("""
      window.jsDebugEvents = [];
      window.jsDebugFailureEvents = () => jsDebugEvents.filter(event => event.type === 'error');
      window.jsDebugCurrentObservationReceiptBarrier = () => ({epoch:'all',accepted:0,pending:0,
        retrying:0,rejected:0,dropped:0,quiescent:true,blocking:[]});
    """)
    raw_close = browser._driver.close
    injected = []

    def execute_close():
        if late_error:
            count = browser.execute_script("""
              jsDebugEvents.push({type:'error',message:'LATE_CLOSE_TASK_CANARY'});
              return jsDebugFailureEvents().length;
            """)
            injected.append(count)
        return raw_close()

    browser._driver.close = execute_close
    try:
        if late_error:
            with pytest.raises(AssertionError, match="LATE_CLOSE_TASK_CANARY"):
                browser.close()
            assert injected == [1]
        else:
            browser.close()
        assert handle not in browser.window_handles
    finally:
        browser._driver.close = raw_close
        browser.switch_to.window(original)
    if opening == "script":
        assert browser.execute_script("return window.__diagnosticPopup.closed;") is True
        browser.execute_script("delete window.__diagnosticPopup;")


def test_browser_navigation_preserves_fragment_realms_and_history(browser, tmp_path):
    load_static_html_fixture(browser, tmp_path, 'navigation-first.html', '<html><body>First</body></html>')
    first = browser.current_url
    browser.execute_script("window.__fragmentCanary = {message:'retained diagnostics'};")
    for url in [first + '#one', first + '#one', first + '#two']:
        browser.get(url)
        assert browser.execute_script("return window.__fragmentCanary?.message;") == 'retained diagnostics', (url, browser.current_url)
    browser.get(first)
    assert browser.execute_script('return window.__fragmentCanary === undefined;') is True
    before = browser.execute_script('return history.length;')
    load_static_html_fixture(browser, tmp_path, 'navigation-second.html', '<html><body>Second</body></html>')
    assert browser.execute_script('return history.length;') == before + 1


def test_browser_navigation_leaves_destination_errors_for_the_exact_consumer(browser, tmp_path):
    load_static_html_fixture(browser, tmp_path, 'navigation-error.html', '<html><head></head><body>First</body></html>')
    register_browser_new_document_script(browser, """
      document.addEventListener('DOMContentLoaded', () => {
        const icon = document.createElement('link');
        icon.rel = 'icon'; icon.href = 'data:,'; document.head.appendChild(icon);
      }, {once:true});
    """)
    missing = browser.current_url + '-missing'
    browser.get(missing)
    assert_only_expected_browser_http_error(browser, path=urlsplit(missing).path, status=404, query={})
