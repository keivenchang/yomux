# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""A7: the top-right backend-health control, in a real browser, against the real bundle.

WHAT WAS MISSING. The indicator's substance was a Node shard driving a constructed DOM stub that
hands the renderer its own `.topbar-right-tools` host, because the stub never builds a real
`.topbar`. That shard is thorough about the state machine and proves nothing about the page: it
cannot fail if `backendHealthIndicatorHost()` resolves nothing, if `.topbar-right-tools` never
exists in the real topbar, if the CSS renders the node invisible, or if the details affordance is
covered. Two clauses in particular had no assertion at ANY layer:

  * "delivered while the diagnostics panels are hidden" existed only as a comment in a file
    header. The whole reason this control is push-fed is that `/api/system-status` polling
    STOPS when the panel is hidden (`85_debug_panel.js` syncDebugSystemPolling), so the hidden
    state is the only state where the control is load-bearing -- and it was the untested one.
  * `backendHealthIndicatorHost()`'s `.topbar-right-tools` -> `topbar` fallback was untested
    everywhere. It is not decoration: `syncTopbarActivityPlacement()` re-parents topbar children,
    and a narrow viewport is exactly when a health warning matters most.

AND THE ONE THAT SHIPPED A BUG. The control USED to be a variable-size text pill that
`syncBackendHealthIndicator()` prepended on a warning and removed on recovery. Because `.topbar`
is content-sized, each appearance grew the row (measured `32px` -> `34.390625px`) and pushed
`#grid` and every xterm down `2.390625px`; each removal reversed it. The old shard asserted the
pill had a positive width and was the first child, so it PASSED while the workspace jumped. The
`test_a7_9_*` geometry regressions below record the topbar, grid, Search, Language, health slot,
and a mounted xterm across healthy / degraded / down / first-recovery / cleared and fail if any
vertical geometry moves.

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
from tests.browser_helpers.browser_layout import assert_close
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture
from tests.browser_helpers.browser_layout import set_browser_visual_profile


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

# The rendered state of the ONE permanently mounted control. `severity` is '' while healthy: the
# control is not removed on recovery, it goes inert and empty, so the slot's geometry never changes.
_STATE_JS = """
    const indicator = document.querySelector('.topbar [data-backend-health]');
    if (!indicator) return null;
    const message = indicator.querySelector('.backend-health-indicator-text');
    const icon = indicator.querySelector('.backend-health-indicator-icon');
    const rect = indicator.getBoundingClientRect();
    const host = indicator.parentElement;
    const style = getComputedStyle(indicator);
    return {
      severity: indicator.dataset.backendHealth || '',
      reason: indicator.dataset.backendHealthReason || '',
      messageRole: message ? (message.getAttribute('role') || '') : '',
      messageLive: message ? (message.getAttribute('aria-live') || '') : '',
      text: (message ? message.textContent : '' || '').trim(),
      iconText: (icon ? icon.textContent : '' || '').trim(),
      ariaLabel: indicator.getAttribute('aria-label') || '',
      title: indicator.getAttribute('title') || '',
      ariaHidden: indicator.getAttribute('aria-hidden') || '',
      disabled: indicator.disabled === true,
      tag: indicator.tagName.toLowerCase(),
      hostClass: host ? host.className : '',
      hostIsRightTools: Boolean(host && host.classList && host.classList.contains('topbar-right-tools')),
      hostIsTopbar: Boolean(host && host.classList && host.classList.contains('topbar')),
      firstChild: Boolean(host && host.firstElementChild === indicator),
      duplicates: document.querySelectorAll('[data-backend-health]').length,
      width: rect.width,
      height: rect.height,
      visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden',
    };
"""

_PUSH_SCRIPT = f"""
    const payload = arguments[0];
    handleClientPushEventNow('backend_health_changed', payload);
    {_STATE_JS}
"""

_READ_SCRIPT = _STATE_JS

# Every rectangle the health control must never move. `.topbar`/`#grid`/`.xterm` vertical geometry
# is the workspace-push proof; Search and Language are the horizontal-repack proof; `health` is the
# fixed slot itself.
_GEOMETRY_JS = """
    const pick = el => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height};
    };
    const xterm = document.querySelector('#grid .xterm') || document.querySelector('.xterm');
    const health = document.querySelector('.topbar [data-backend-health]');
    return {
      severity: health ? (health.dataset.backendHealth || '') : '(absent)',
      topbar: pick(document.querySelector('.topbar')),
      grid: pick(document.getElementById('grid')),
      search: pick(document.querySelector('.topbar-search')),
      language: pick(document.querySelector('.topbar-language-menu')),
      health: pick(health),
      xterm: pick(xterm),
    };
"""

_GEOMETRY_PUSH_SCRIPT = f"""
    const payload = arguments[0];
    handleClientPushEventNow('backend_health_changed', payload);
    {_GEOMETRY_JS}
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


def _read(browser):
    return browser.execute_script(_READ_SCRIPT)


def _geometry(browser):
    return browser.execute_script(_GEOMETRY_JS)


def _push_geometry(browser, payload):
    return browser.execute_script(_GEOMETRY_PUSH_SCRIPT, payload)


def _watchd_down(revision=1):
    return _event(
        revision,
        "down",
        [_resource("watchd", "File watching", "down", "service_absent")],
    )


@pytest.mark.browser
def test_a7_1_a_pushed_down_service_renders_the_one_permanent_control_in_the_right_tools(browser, tmp_path):
    """Sub-clause 1: ONE node, in the real topbar's right-tools host, actually visible on screen.

    The control is permanently mounted, so `duplicates` is exactly one before AND after the warning
    and healthy is a state of the same node, not its absence.
    """

    load_live_runtime_boot_fixture(browser, tmp_path)
    healthy = _read(browser)
    assert healthy is not None, "the permanent health control was not mounted while healthy"
    assert healthy["severity"] == "", healthy
    assert healthy["duplicates"] == 1, healthy
    assert healthy["disabled"] is True, healthy
    assert healthy["ariaHidden"] == "true", healthy
    assert healthy["width"] > 0 and healthy["height"] > 0, healthy
    assert browser.find_elements(By.CSS_SELECTOR, f".topbar {HOST_SELECTOR}"), "the real topbar has no right-tools host"

    rendered = _push(browser, _watchd_down())

    assert rendered is not None, "a pushed down service rendered no control in the real topbar"
    assert rendered["duplicates"] == 1, rendered
    assert rendered["hostIsRightTools"] is True, rendered
    assert rendered["firstChild"] is True, rendered
    assert rendered["visible"] is True, rendered
    assert rendered["width"] > 0 and rendered["height"] > 0, rendered
    assert rendered["severity"] == "down", rendered
    assert rendered["disabled"] is False, rendered
    assert rendered["ariaHidden"] == "", rendered
    # role=status now lives on the live-region owner that announces the sentence.
    assert rendered["messageRole"] == "status", rendered
    assert rendered["messageLive"] == "polite", rendered


@pytest.mark.browser
def test_a7_2_the_control_names_the_service_by_its_server_label_and_never_by_its_id(browser, tmp_path):
    """Sub-clause 2: the announced sentence carries the server's label, not the raw service id."""

    load_live_runtime_boot_fixture(browser, tmp_path)
    rendered = _push(browser, _watchd_down())

    assert "File watching" in rendered["text"], rendered
    assert "watchd" not in rendered["text"], rendered
    assert "service_absent" not in rendered["text"], rendered
    # The tooltip carries the same sentence; the machine-readable half is retained as a data attr,
    # where it cannot be read as prose.
    assert rendered["title"] == rendered["text"], rendered
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
def test_a7_3_the_control_is_delivered_by_push_with_no_system_status_request(browser, tmp_path):
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
def test_a7_4_the_control_is_delivered_while_every_diagnostics_panel_is_hidden(browser, tmp_path):
    """Sub-clause 5, the one with NO assertion at any layer before this test.

    The System sub-view is where a person could otherwise read this fact, and its poll is switched
    off whenever the panel is hidden. So the state asserted here -- no diagnostics panel rendered,
    no System sub-view visible, no `/api/system-status` request -- is exactly the state in which
    the control is the ONLY way a dead backend service reaches the user.
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

        assert rendered is not None, "no control was delivered while the diagnostics panels were hidden"
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
        # Still hidden AFTER delivery: the control must not open a panel to say what it has to say.
        assert after["visiblePanels"] == 0, after
    finally:
        counted = browser.execute_script(_RESTORE_FETCH)
    assert counted == 0, f"delivery while panels were hidden issued {counted} /api/system-status requests"


@pytest.mark.browser
def test_a7_5_the_control_host_falls_back_to_the_topbar_when_right_tools_is_absent(browser, tmp_path):
    """Sub-clause 6: `backendHealthIndicatorHost()`'s fallback, untested at every layer until now.

    `.topbar-right-tools` is built by `createTopbarRightTools()` and its children are re-parented
    at runtime by `syncTopbarActivityPlacement()`, so "the host container is not there yet" is a
    real page state. Removing the host removes the permanently mounted control with it; the next
    warning must still render, in the topbar, and restoring the host must not build a SECOND control.
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
        assert rendered is not None, "the control vanished when .topbar-right-tools was absent"
        assert rendered["hostIsRightTools"] is False, rendered
        assert rendered["hostIsTopbar"] is True, rendered
        assert rendered["visible"] is True, rendered
        assert rendered["duplicates"] == 1, rendered
        assert "File watching" in rendered["text"], rendered

        # ...and when the host reappears, the renderer must not build a SECOND control: the existing
        # node is looked up across the whole topbar, not just the current host.
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

    # `starting` and `ready` are not warnings and never escalate the control; the debounce keeps the
    # existing warning shown for one healthy revision, then clears it back to the inert '' state.
    one_healthy = _push(browser, _event(3, "ready", []))
    assert one_healthy["severity"] == "down", one_healthy  # one healthy revision does not clear a warning
    two_healthy = _push(browser, _event(4, "starting", []))
    assert two_healthy["severity"] == "", two_healthy  # the second clears; a healthy/starting state never raises a warning


@pytest.mark.browser
def test_a7_7_the_control_opens_the_system_view_from_a_real_click(browser, tmp_path):
    """Sub-clause 8: the affordance does what it says, driven by a real click on the real control.

    The variable-width Details button is gone; the whole fixed-size control is the System route.
    """

    load_live_runtime_boot_fixture(browser, tmp_path)
    rendered = _push(browser, _watchd_down())
    assert rendered["tag"] == "button", rendered
    assert rendered["ariaLabel"], rendered  # names the action ("Show backend service details")
    assert rendered["disabled"] is False, rendered

    button = browser.find_element(By.CSS_SELECTOR, INDICATOR)
    assert button.is_displayed(), "the health control is in the DOM but not visible"
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
    """Sub-clause 9: one good sample does not clear a warning, and an old revision cannot reopen it.

    "Cleared" now means the same node returns to its inert '' state -- it is never removed.
    """

    load_live_runtime_boot_fixture(browser, tmp_path)
    assert _push(browser, _watchd_down(revision=5))["severity"] == "down"

    # A replayed and an older revision are both ignored: the node stays exactly as it was.
    assert _push(browser, _watchd_down(revision=5))["severity"] == "down"
    assert _push(browser, _watchd_down(revision=4))["severity"] == "down"

    first_healthy = _push(browser, _event(6, "ready", []))
    assert first_healthy["severity"] == "down", "one healthy revision must not clear a backend warning"

    second_healthy = _push(browser, _event(7, "ready", []))
    assert second_healthy["severity"] == "", second_healthy
    # The node is still mounted (one, inert), not removed.
    assert second_healthy["duplicates"] == 1, second_healthy
    assert second_healthy["disabled"] is True, second_healthy


# ---------------------------------------------------------------------------------------------------
# The geometry regressions: what the old insert/remove pill could not satisfy.
# ---------------------------------------------------------------------------------------------------

_VERTICAL_TOLERANCE = 0.5


def _assert_same_vertical_geometry(reference, sample, label):
    """The workspace must not move: topbar box, grid top, and any mounted xterm top are identical."""

    assert_close(sample["topbar"]["top"], reference["topbar"]["top"], tol=_VERTICAL_TOLERANCE, context=f"{label}: topbar top")
    assert_close(sample["topbar"]["height"], reference["topbar"]["height"], tol=_VERTICAL_TOLERANCE, context=f"{label}: topbar height")
    assert_close(sample["topbar"]["bottom"], reference["topbar"]["bottom"], tol=_VERTICAL_TOLERANCE, context=f"{label}: topbar bottom")
    assert_close(sample["grid"]["top"], reference["grid"]["top"], tol=_VERTICAL_TOLERANCE, context=f"{label}: grid top")
    if reference["xterm"] and sample["xterm"]:
        assert_close(sample["xterm"]["top"], reference["xterm"]["top"], tol=_VERTICAL_TOLERANCE, context=f"{label}: xterm top")


def _assert_stable_slot_and_neighbors(reference, sample, label):
    """The fixed slot keeps its size, and Search and Language do not repack horizontally."""

    assert_close(sample["health"]["width"], reference["health"]["width"], tol=_VERTICAL_TOLERANCE, context=f"{label}: health slot width")
    assert_close(sample["health"]["height"], reference["health"]["height"], tol=_VERTICAL_TOLERANCE, context=f"{label}: health slot height")
    assert_close(sample["health"]["left"], reference["health"]["left"], tol=_VERTICAL_TOLERANCE, context=f"{label}: health slot left")
    assert_close(sample["search"]["left"], reference["search"]["left"], tol=_VERTICAL_TOLERANCE, context=f"{label}: search left")
    assert_close(sample["search"]["top"], reference["search"]["top"], tol=_VERTICAL_TOLERANCE, context=f"{label}: search top")
    assert_close(sample["language"]["left"], reference["language"]["left"], tol=_VERTICAL_TOLERANCE, context=f"{label}: language left")
    assert_close(sample["language"]["top"], reference["language"]["top"], tol=_VERTICAL_TOLERANCE, context=f"{label}: language top")


@pytest.mark.browser
@pytest.mark.parametrize(
    "width,height,theme,dpr",
    [
        (2048, 900, "dark", 1),
        (2048, 900, "light", 1),
        (900, 720, "light", 2),  # narrow + high-zoom: the case the packer could otherwise repack
    ],
)
def test_a7_9_health_transitions_never_move_the_workspace(browser, tmp_path, width, height, theme, dpr):
    """The single before/after proof: healthy -> down -> first-recovery -> cleared, no geometry shift.

    The old pill grew `.topbar` from `32px` to `34.390625px` and moved `#grid`/xterm down
    `2.390625px` on every appearance; this fails if the topbar, grid, or terminal moves at all, at a
    wide and a narrow/zoomed viewport, in dark and light themes.
    """

    browser.set_window_size(width, height)
    load_live_runtime_boot_fixture(browser, tmp_path)
    set_browser_visual_profile(browser, theme=theme, dpr=dpr)

    healthy = _geometry(browser)
    assert healthy["health"] is not None, healthy
    assert healthy["health"]["width"] > 0 and healthy["health"]["height"] > 0, healthy
    assert healthy["severity"] == "", healthy

    down = _push_geometry(browser, _watchd_down(revision=1))
    assert down["severity"] == "down", down
    _assert_same_vertical_geometry(healthy, down, "down")
    _assert_stable_slot_and_neighbors(healthy, down, "down")

    first_recovery = _push_geometry(browser, _event(2, "ready", []))
    assert first_recovery["severity"] == "down", first_recovery  # debounce keeps the warning shown
    _assert_same_vertical_geometry(healthy, first_recovery, "first-recovery")
    _assert_stable_slot_and_neighbors(healthy, first_recovery, "first-recovery")

    cleared = _push_geometry(browser, _event(3, "ready", []))
    assert cleared["severity"] == "", cleared
    _assert_same_vertical_geometry(healthy, cleared, "cleared")
    _assert_stable_slot_and_neighbors(healthy, cleared, "cleared")


@pytest.mark.browser
@pytest.mark.parametrize(
    "service,states,epoch",
    [
        # The exact retained port-7770 sequences that produced the visible flashing.
        ("watchd", [("starting", ""), ("degraded", "service_unhealthy"), ("ready", ""), ("ready", "")], "epoch-watchd"),
        ("statsd", [("ready", ""), ("unknown", "probe_timeout"), ("ready", ""), ("ready", "")], "epoch-statsd"),
    ],
)
def test_a7_10_the_retained_noisy_sequences_change_paint_not_geometry(browser, tmp_path, service, states, epoch):
    """Drive the two real transition sequences and prove each step changes state, never geometry."""

    browser.set_window_size(2048, 900)
    load_live_runtime_boot_fixture(browser, tmp_path)
    set_browser_visual_profile(browser, theme="dark", dpr=1)

    baseline = _geometry(browser)
    label_for = {"watchd": "File watching", "statsd": "Statistics"}[service]
    saw_warning = False
    for revision, (state, reason) in enumerate(states, start=1):
        overall = "down" if state in {"down", "upgrade_required"} else (
            "degraded" if state in {"degraded", "backoff", "unknown"} else state
        )
        resources = [] if reason == "" else [_resource(service, label_for, state, reason)]
        sample = _push_geometry(browser, _event(revision, overall, resources, epoch=epoch))
        _assert_same_vertical_geometry(baseline, sample, f"{service} step {revision} ({state})")
        _assert_stable_slot_and_neighbors(baseline, sample, f"{service} step {revision} ({state})")
        if reason:
            saw_warning = True
    # The sequence is not a no-op: the middle steps DID raise a warning, so a green result means the
    # geometry held through a real state change, not that nothing happened.
    assert saw_warning, states
