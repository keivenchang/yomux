"""Browser harness lifecycle contracts retained under tests.test_browser_layout node IDs."""

import subprocess

import pytest

from tests.browser_helpers import browser_layout as browser_layout_module
from tests.browser_helpers.browser_layout import _reset_browser_state
from tests.browser_helpers.browser_layout import browser_wait_timeout
from tests.browser_helpers.browser_layout import DEFAULT_BROWSER_WINDOW_SIZE
from tests.browser_helpers.browser_layout import load_static_html_fixture
from tests.browser_helpers.browser_layout import page_html
from tests.browser_helpers.browser_layout import SESSION_SCOPED_BROWSER_REUSE_ENV
from tests.browser_helpers.browser_layout import XDIST_BROWSER_WAIT_FLOOR_SECONDS


def assert_browser_wait_timeout_has_one_xdist_only_floor():
    assert browser_wait_timeout(5, worker="gw0") == XDIST_BROWSER_WAIT_FLOOR_SECONDS
    assert browser_wait_timeout(15, worker="gw0") == 15
    assert browser_wait_timeout(5, worker="") == 5


def assert_session_scoped_browser_reuse_is_the_default(monkeypatch):
    monkeypatch.delenv(SESSION_SCOPED_BROWSER_REUSE_ENV, raising=False)
    assert browser_layout_module._browser_fixture_scope(fixture_name="browser", config=None) == "session"


def assert_browser_bundle_guard_passes_through_static_build_failure(monkeypatch):
    """The browser gate must expose --check's specific failure, not invent one."""
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="stale static assets: yolomux.js\n",
    )
    monkeypatch.setattr(browser_layout_module.subprocess, "run", lambda *_args, **_kwargs: completed)
    monkeypatch.setattr(browser_layout_module, "_BUNDLE_FRESHNESS_CHECKED", False)

    with pytest.raises(AssertionError) as raised:
        browser_layout_module._require_current_generated_bundle()
    assert str(raised.value) == "stale static assets: yolomux.js\n"


def assert_reused_browser_reset_closes_popouts_and_clears_profile_state(monkeypatch):
    calls = []

    class SwitchTo:
        def window(self, handle):
            calls.append(("switch", handle))

    class Driver:
        window_handles = ["primary", "popout"]
        current_window_handle = "primary"
        _yolomux_primary_window_handle = "primary"
        switch_to = SwitchTo()

        def close(self):
            calls.append(("close",))

        def execute_cdp_cmd(self, command, params):
            calls.append(("cdp", command, params))

        def execute_script(self, source):
            calls.append(("script", source))
            return "http://current.test" if source == "return location.origin;" else None

        def delete_all_cookies(self):
            calls.append(("cookies",))

        def get_log(self, kind):
            calls.append(("log", kind))
            return []

        def get(self, url):
            calls.append(("get", url))

        def set_window_size(self, width, height):
            calls.append(("size", width, height))

    monkeypatch.setattr(browser_layout_module, "_FIXTURE_HTTP_BASE", "http://fixture.test")
    monkeypatch.setattr(browser_layout_module, "remove_browser_test_new_document_scripts", lambda driver: calls.append(("scripts",)))
    browser_layout_module._reset_reused_browser_state(Driver())

    assert ("close",) in calls
    assert ("get", "about:blank") in calls
    assert calls.index(("get", "about:blank")) < next(
        index for index, call in enumerate(calls) if call[0] == "script" and "document.body.focus" in call[1]
    )
    assert ("log", "browser") in calls
    assert ("size", *DEFAULT_BROWSER_WINDOW_SIZE) in calls
    cdp = [(call[1], call[2]) for call in calls if call[0] == "cdp"]
    assert ("Browser.resetPermissions", {}) in cdp
    assert ("Browser.setDownloadBehavior", {"behavior": "deny"}) in cdp
    assert ("Emulation.setCPUThrottlingRate", {"rate": 1}) in cdp
    assert ("Emulation.clearDeviceMetricsOverride", {}) in cdp
    assert ("Emulation.setEmulatedMedia", {"features": []}) in cdp
    assert ("Storage.clearDataForOrigin", {"origin": "http://current.test", "storageTypes": "all"}) in cdp
    assert ("Storage.clearDataForOrigin", {"origin": "http://fixture.test", "storageTypes": "all"}) in cdp


def assert_generic_browser_teardown_gates_local_diagnostics_without_requesting_server_logs(monkeypatch):
    class Driver:
        current_url = "about:blank"
        ring_requests = 0

        def execute_script(self, source):
            if source == "return location.origin;":
                return "http://fixture.test"
            if "const isArray = Array.isArray(jsDebugEvents)" in source:
                warning = {
                    "id": 17,
                    "level": "warning",
                    "message": "mock retained JS warning",
                    "source": "browser",
                }
                js_debug = {
                    "reachable": True,
                    "isArray": True,
                    "events": [warning],
                    "errors": [warning],
                    "receiptBarrier": {
                        "epoch": "all",
                        "accepted": 0,
                        "pending": 0,
                        "retrying": 0,
                        "rejected": 0,
                        "dropped": 0,
                        "quiescent": True,
                        "blocking": [],
                    },
                }
                if "window.location.replace('about:blank')" in source:
                    return {
                        "jsDebug": js_debug,
                        "journey": {"reachable": False, "visitedSurfaces": []},
                    }
                return js_debug
            if "const state = window.__yolomuxBrowserJourneyGate" in source:
                return {"reachable": False, "visitedSurfaces": []}
            raise AssertionError(f"unexpected browser script: {source}")

        def execute_async_script(self, source):
            if "/api/logs" in source:
                self.ring_requests += 1
                raise AssertionError("generic browser teardown requested /api/logs")
            raise AssertionError(f"unexpected async browser script: {source}")

        def get_log(self, kind):
            assert kind == "browser"
            return [{"level": "SEVERE", "message": "mock retained Chrome failure"}]

    class Node:
        funcargs = {"browser": Driver()}

    class Request:
        node = Node()

    monkeypatch.setattr(browser_layout_module, "_reset_reused_browser_state", lambda *_args, **_kwargs: None)
    teardown = _reset_browser_state.__wrapped__(Request())
    next(teardown)
    with pytest.raises(AssertionError) as raised:
        next(teardown)

    assert "mock retained JS warning" in str(raised.value)
    assert "mock retained Chrome failure" in str(raised.value)
    assert Node.funcargs["browser"].ring_requests == 0


def assert_browser_document_wait_helper_reports_values_and_timeout_context(browser, tmp_path):
    page = tmp_path / "browser-wait-helper.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html('<main id="ready">ready</main>'),
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const immediate = await window.__yolomuxTestWaitFor(
            () => document.getElementById('ready')?.textContent,
            {description: 'immediate fixture value'}
          );
          let delayedValue = '';
          setTimeout(() => { delayedValue = 'delayed-value'; }, 20);
          const delayed = await window.__yolomuxTestWaitFor(
            () => delayedValue,
            {timeoutMs: 500, intervalMs: 5, description: 'delayed fixture value'}
          );
          let asyncAttempts = 0;
          const asyncValue = await window.__yolomuxTestWaitFor(
            async () => {
              await Promise.resolve();
              asyncAttempts += 1;
              return asyncAttempts >= 2 ? {ready: true} : false;
            },
            {timeoutMs: 500, intervalMs: 5, description: 'async fixture value'}
          );
          let timeout = '';
          try {
            await window.__yolomuxTestWaitFor(
              () => false,
              {timeoutMs: 15, intervalMs: 4, description: 'missing fixture state'}
            );
          } catch (error) {
            timeout = String(error?.message || error);
          }
          done({immediate, delayed, asyncValue, asyncAttempts, timeout});
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert metrics.get("error") is None, metrics
    assert metrics["immediate"] == "ready", metrics
    assert metrics["delayed"] == "delayed-value", metrics
    assert metrics["asyncValue"] == {"ready": True}, metrics
    assert metrics["asyncAttempts"] == 2, metrics
    assert metrics["timeout"] == "Timed out after 15ms waiting for missing fixture state", metrics
