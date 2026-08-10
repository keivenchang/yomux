# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""A7: the top-right backend-health indicator, in a real browser, against the real bundle.

WHAT WAS MISSING. The indicator's substance was a Node shard driving a constructed DOM stub that
hands the renderer its own `.topbar-right-tools` host, because the stub never builds a real
`.topbar`. That shard is thorough about the state machine and proves nothing about the page: it
cannot fail if `backendHealthIndicatorHost()` resolves nothing, if `.topbar-right-tools` never
exists in the real topbar, if the CSS renders the node invisible, or if the details button is
covered. Two clauses in particular had no assertion at ANY layer:

  * "delivered while the diagnostics panels are hidden" existed only as a comment in a file
    header. The whole reason this indicator is push-fed is that `/api/system-status` polling
    STOPS when the panel is hidden (`85_debug_panel.js` syncDebugSystemPolling), so the hidden
    state is the only state where the indicator is load-bearing -- and it was the untested one.
  * `backendHealthIndicatorHost()`'s `.topbar-right-tools` -> `topbar` fallback was untested
    everywhere. It is not decoration: `syncTopbarActivityPlacement()` re-parents topbar children,
    and a narrow viewport is exactly when a health warning matters most.

HOW THESE TESTS DIFFER FROM THE NODE SHARD. Every clause below goes in through
`handleClientPushEventNow('backend_health_changed', ...)` -- the real dispatcher branch in
`99_terminal_boot.js`, not `applyBackendHealthPayload` directly -- and every assertion reads the
rendered page: real host resolution, real geometry, real visibility, real click.

Each test reloads the boot fixture, so the bundle's module-scope `backendHealthState` starts clean
and no test can inherit a severity, an epoch or a debug sub-tab from the one before it.
"""

import pytest
from selenium.webdriver.common.by import By

from tests.browser_helpers.browser_layout import browser  # noqa: F401  -- fixture
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture


# `performance.getEntriesByType('resource')` is NOT used to count requests here: its buffer is
# capped (250 entries by default) and a full buffer silently stops recording, which would make a
# "no new requests" assertion incapable of failing. A real wrapper around `window.fetch` counts
# every call and is removed again in the same test.
_COUNT_SYSTEM_STATUS_FETCHES = """
    window.__a7SystemStatusFetches = 0;
    window.__a7NativeFetch = window.fetch;
    window.fetch = function(...args) {
      const target = String(args[0] && args[0].url ? args[0].url : args[0] || '');
      if (target.includes('/api/system-status')) window.__a7SystemStatusFetches += 1;
      return window.__a7NativeFetch.apply(this, args);
    };
    return true;
"""

_RESTORE_FETCH = """
    if (window.__a7NativeFetch) window.fetch = window.__a7NativeFetch;
    const counted = window.__a7SystemStatusFetches || 0;
    delete window.__a7NativeFetch;
    delete window.__a7SystemStatusFetches;
    return counted;
"""

INDICATOR = ".backend-health-indicator"
INDICATOR_SELECTOR = "[data-backend-health]"
HOST_SELECTOR = ".topbar-right-tools"

# The payload shape `BackendHealthObserver.event_payload` publishes: exactly four keys, and
# `degraded_resources` rows of exactly `id`/`label`/`state`/`reason_code`.
_PUSH_SCRIPT = """
    const payload = arguments[0];
    handleClientPushEventNow('backend_health_changed', payload);
    const indicator = document.querySelector('.topbar [data-backend-health]');
    if (!indicator) return null;
    const message = indicator.querySelector('.backend-health-indicator-text');
    const details = indicator.querySelector('.backend-health-indicator-details');
    const rect = indicator.getBoundingClientRect();
    const host = indicator.parentElement;
    return {
      severity: indicator.dataset.backendHealth || '',
      reason: indicator.dataset.backendHealthReason || '',
      role: indicator.getAttribute('role') || '',
      text: (message?.textContent || '').trim(),
      hostClass: host?.className || '',
      hostIsRightTools: Boolean(host?.classList?.contains('topbar-right-tools')),
      hostIsTopbar: Boolean(host?.classList?.contains('topbar')),
      firstChild: host?.firstElementChild === indicator,
      duplicates: document.querySelectorAll('[data-backend-health]').length,
      width: rect.width,
      height: rect.height,
      visible: rect.width > 0 && rect.height > 0 && getComputedStyle(indicator).visibility !== 'hidden',
      detailsTag: details ? details.tagName.toLowerCase() : '',
      detailsType: details ? details.getAttribute('type') : '',
      detailsAria: details ? details.getAttribute('aria-label') : '',
      detailsText: details ? details.textContent.trim() : '',
    };
"""


def _event(revision, overall_state, resources, epoch="epoch-a7"):
    return {
        "epoch": epoch,
        "revision": revision,
        "overall_state": overall_state,
        "degraded_resources": list(resources),
    }


def _resource(identifier, label, state, reason_code):
    return {"id": identifier, "label": label, "state": state, "reason_code": reason_code}


def _push(browser, payload):
    return browser.execute_script(_PUSH_SCRIPT, payload)


def _watchd_down(revision=1):
    return _event(
        revision,
        "down",
        [_resource("watchd", "File watching", "down", "service_absent")],
    )


@pytest.mark.browser
def test_a7_1_a_pushed_down_service_renders_one_visible_indicator_in_the_topbar_right_tools(browser, tmp_path):
    """Sub-clause 1: ONE node, in the real topbar's right-tools host, actually visible on screen.

    The Node shard hands the renderer a host it built itself, so it can pass while the real page
    has no `.topbar-right-tools` at all. This resolves the host the way the product does.
    """

    load_live_runtime_boot_fixture(browser, tmp_path)
    assert browser.find_elements(By.CSS_SELECTOR, INDICATOR_SELECTOR) == [], "the indicator must not exist while healthy"
    assert browser.find_elements(By.CSS_SELECTOR, f".topbar {HOST_SELECTOR}"), "the real topbar has no right-tools host"

    rendered = _push(browser, _watchd_down())

    assert rendered is not None, "a pushed down service rendered no indicator in the real topbar"
    assert rendered["duplicates"] == 1, rendered
    assert rendered["hostIsRightTools"] is True, rendered
    assert rendered["firstChild"] is True, rendered
    assert rendered["visible"] is True, rendered
    assert rendered["width"] > 0 and rendered["height"] > 0, rendered
    assert rendered["severity"] == "down", rendered
    assert rendered["role"] == "status", rendered


@pytest.mark.browser
def test_a7_2_the_indicator_names_the_service_by_its_server_label_and_never_by_its_id(browser, tmp_path):
    """Sub-clause 2: the rendered sentence carries the server's label, not the raw service id."""

    load_live_runtime_boot_fixture(browser, tmp_path)
    rendered = _push(browser, _watchd_down())

    assert "File watching" in rendered["text"], rendered
    assert "watchd" not in rendered["text"], rendered
    assert "service_absent" not in rendered["text"], rendered
    # The machine-readable half is retained on the node, where it cannot be read as prose.
    assert rendered["reason"] == "service_absent", rendered

    many = _push(
        browser,
        _event(
            2,
            "down",
            [
                _resource("watchd", "File watching", "down", "service_absent"),
                _resource("statsd", "Statistics", "down", "exited"),
                _resource("indexd", "Quick Open index", "down", "exited"),
            ],
        ),
    )
    assert "File watching" in many["text"] and "2" in many["text"], many
    assert "Statistics" not in many["text"] and "statsd" not in many["text"], many


@pytest.mark.browser
def test_a7_3_the_indicator_is_delivered_by_push_with_no_system_status_request(browser, tmp_path):
    """Sub-clause 3: pushed, and it adds no polling. Counted through a real `window.fetch` wrapper."""

    load_live_runtime_boot_fixture(browser, tmp_path)
    assert browser.execute_script(_COUNT_SYSTEM_STATUS_FETCHES) is True
    try:
        for revision in range(1, 6):
            rendered = _push(browser, _watchd_down(revision))
            assert rendered is not None, revision
        pushed_total = browser.execute_script("return window.__a7SystemStatusFetches;")
        # Positive control, in the same test: a counter that cannot count is not evidence of zero.
        # One deliberate request must move it, or the zero above means only that the wrapper is dead.
        browser.execute_script(
            "return window.fetch('/api/system-status').catch(() => null);"
        )
        control_total = browser.execute_script("return window.__a7SystemStatusFetches;")
    finally:
        browser.execute_script(_RESTORE_FETCH)
    assert pushed_total == 0, f"pushed backend health caused {pushed_total} /api/system-status requests"
    assert control_total == pushed_total + 1, (pushed_total, control_total)


@pytest.mark.browser
def test_a7_4_the_indicator_is_delivered_while_every_diagnostics_panel_is_hidden(browser, tmp_path):
    """Sub-clause 5, the one with NO assertion at any layer before this test.

    The System sub-view is where a person could otherwise read this fact, and its poll is switched
    off whenever the panel is hidden. So the state asserted here -- no diagnostics panel rendered,
    no System sub-view visible, no `/api/system-status` request -- is exactly the state in which
    the indicator is the ONLY way a dead backend service reaches the user. It was previously
    described in a file-header comment and asserted nowhere.
    """

    load_live_runtime_boot_fixture(browser, tmp_path)

    hidden = browser.execute_script(
        """
        const panels = Array.from(document.querySelectorAll('.js-debug-panel'));
        const views = Array.from(document.querySelectorAll('[data-js-debug-subview]'));
        const shown = element => {
          const rect = element.getBoundingClientRect();
          return !element.hidden && rect.width > 0 && rect.height > 0;
        };
        return {
          panels: panels.length,
          visiblePanels: panels.filter(shown).length,
          visibleSystemViews: views.filter(view => view.dataset.jsDebugSubview === 'system' && shown(view)).length,
        };
        """
    )
    assert hidden["visiblePanels"] == 0, hidden
    assert hidden["visibleSystemViews"] == 0, hidden

    assert browser.execute_script(_COUNT_SYSTEM_STATUS_FETCHES) is True
    try:
        rendered = _push(browser, _watchd_down())

        assert rendered is not None, "no indicator was delivered while the diagnostics panels were hidden"
        assert rendered["visible"] is True, rendered
        assert "File watching" in rendered["text"], rendered

        after = browser.execute_script(
            """
            const panels = Array.from(document.querySelectorAll('.js-debug-panel'));
            const shown = element => {
              const rect = element.getBoundingClientRect();
              return !element.hidden && rect.width > 0 && rect.height > 0;
            };
            return {visiblePanels: panels.filter(shown).length};
            """
        )
        # Still hidden AFTER delivery: the indicator must not open a panel to say what it has to say.
        assert after["visiblePanels"] == 0, after
    finally:
        counted = browser.execute_script(_RESTORE_FETCH)
    assert counted == 0, f"delivery while panels were hidden issued {counted} /api/system-status requests"


@pytest.mark.browser
def test_a7_5_the_indicator_host_falls_back_to_the_topbar_when_right_tools_is_absent(browser, tmp_path):
    """Sub-clause 6: `backendHealthIndicatorHost()`'s fallback, untested at every layer until now.

    `.topbar-right-tools` is built by `createTopbarRightTools()` and its children are re-parented
    at runtime by `syncTopbarActivityPlacement()`, so "the host container is not there yet" is a
    real page state and not a hypothetical. The warning must still render, in the topbar.

    The removed host is restored before the test ends, because the browser fixture is shared.
    """

    load_live_runtime_boot_fixture(browser, tmp_path)
    removed = browser.execute_script(
        """
        const host = document.querySelector('.topbar .topbar-right-tools');
        if (!host) return false;
        window.__a7RemovedHost = host;
        window.__a7RemovedParent = host.parentElement;
        window.__a7RemovedNext = host.nextSibling;
        host.remove();
        return document.querySelector('.topbar .topbar-right-tools') === null;
        """
    )
    assert removed is True, "the right-tools host could not be removed for the fallback case"

    try:
        rendered = _push(browser, _watchd_down())
        assert rendered is not None, "the indicator vanished when .topbar-right-tools was absent"
        assert rendered["hostIsRightTools"] is False, rendered
        assert rendered["hostIsTopbar"] is True, rendered
        assert rendered["visible"] is True, rendered
        assert rendered["duplicates"] == 1, rendered
        assert "File watching" in rendered["text"], rendered

        # ...and when the host reappears, the renderer must not build a SECOND indicator: the
        # existing node is looked up across the whole topbar, not just the current host.
        restored = browser.execute_script(
            """
            const host = window.__a7RemovedHost;
            window.__a7RemovedParent.insertBefore(host, window.__a7RemovedNext);
            syncBackendHealthIndicator();
            return document.querySelectorAll('[data-backend-health]').length;
            """
        )
        assert restored == 1, restored
    finally:
        browser.execute_script(
            """
            const host = window.__a7RemovedHost;
            if (host && !host.isConnected && window.__a7RemovedParent) {
              window.__a7RemovedParent.insertBefore(host, window.__a7RemovedNext);
            }
            delete window.__a7RemovedHost;
            delete window.__a7RemovedParent;
            delete window.__a7RemovedNext;
            """
        )
        assert browser.find_elements(By.CSS_SELECTOR, f".topbar {HOST_SELECTOR}"), "the right-tools host was not restored"


@pytest.mark.browser
def test_a7_6_severity_is_carried_by_the_words_and_a_worse_signal_outranks_a_better_one(browser, tmp_path):
    """Sub-clause 4 and 7: the ranking, and the rule that the sentence -- not the colour -- warns.

    Colour is asserted as a data token rather than a pixel on purpose: the contract is that a
    monochrome or high-contrast theme still tells the user what is wrong, so the two states must
    read differently in TEXT before anything is said about their styling.
    """

    load_live_runtime_boot_fixture(browser, tmp_path)

    degraded = _push(
        browser,
        _event(1, "degraded", [_resource("statsd", "Statistics", "degraded", "transport_failed")]),
    )
    assert degraded["severity"] == "degraded", degraded
    assert "Statistics" in degraded["text"], degraded

    down = _push(
        browser,
        _event(
            2,
            "down",
            [
                _resource("statsd", "Statistics", "degraded", "transport_failed"),
                _resource("watchd", "File watching", "down", "service_absent"),
            ],
        ),
    )
    assert down["severity"] == "down", down
    assert down["text"] != degraded["text"], (down, degraded)

    # `starting` and `ready` are not warnings, and a healthy overall state must not raise the node.
    healthy = _push(browser, _event(3, "ready", []))
    still_warning = _push(browser, _event(4, "starting", []))
    assert healthy is not None and still_warning is None, (healthy, still_warning)


@pytest.mark.browser
def test_a7_7_the_details_button_opens_the_system_view_from_a_real_click(browser, tmp_path):
    """Sub-clause 8: the affordance does what it says, driven by a real click on a real button."""

    load_live_runtime_boot_fixture(browser, tmp_path)
    rendered = _push(browser, _watchd_down())
    assert rendered["detailsTag"] == "button", rendered
    assert rendered["detailsType"] == "button", rendered
    assert rendered["detailsAria"], rendered
    assert rendered["detailsText"], rendered

    button = browser.find_element(By.CSS_SELECTOR, f"{INDICATOR} .backend-health-indicator-details")
    assert button.is_displayed(), "the details button is in the DOM but not visible"
    button.click()

    opened = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__yolomuxTestWaitFor(
          () => jsDebugSubTab === 'system'
            && Array.from(document.querySelectorAll('[data-js-debug-subview="system"]')).some(view => !view.hidden),
          {timeoutMs: 10000, description: 'backend health details opens the System view'},
        ).then(() => done({
          subTab: jsDebugSubTab,
          visibleSystemViews: Array.from(document.querySelectorAll('[data-js-debug-subview="system"]'))
            .filter(view => !view.hidden).length,
        })).catch(error => done({error: String(error)}));
        """
    )
    assert opened.get("error") is None, opened
    assert opened["subTab"] == "system", opened
    assert opened["visibleSystemViews"] >= 1, opened


@pytest.mark.browser
def test_a7_8_the_warning_clears_only_after_the_recovery_debounce_and_ignores_stale_revisions(browser, tmp_path):
    """Sub-clause 9: one good sample does not clear a warning, and an old revision cannot reopen it."""

    load_live_runtime_boot_fixture(browser, tmp_path)
    assert _push(browser, _watchd_down(revision=5))["severity"] == "down"

    # A replayed and an older revision are both ignored: the node stays exactly as it was.
    assert _push(browser, _watchd_down(revision=5))["severity"] == "down"
    assert _push(browser, _watchd_down(revision=4))["severity"] == "down"

    first_healthy = _push(browser, _event(6, "ready", []))
    assert first_healthy is not None, "one healthy revision must not clear a backend warning"
    assert first_healthy["severity"] == "down", first_healthy

    second_healthy = _push(browser, _event(7, "ready", []))
    assert second_healthy is None, second_healthy
    assert browser.find_elements(By.CSS_SELECTOR, INDICATOR_SELECTOR) == [], "the recovered indicator was not removed"
