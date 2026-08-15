"""Gate I and K: rendered tab drag geometry and mutation acknowledgement."""

import pytest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

from tests import latency_calibration
from tests.browser_helpers.browser_console import consume_only_expected_js_debug_api_errors
from tests.browser_helpers.browser_layout import browser
from tests.browser_helpers.browser_layout import cdp_drag_hold
from tests.browser_helpers.browser_layout import cdp_release
from tests.browser_helpers.browser_layout import load_dockview_runtime_boot_fixture
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture
from tests.browser_helpers.browser_layout import wait_for_dockview
from tests.browser_helpers.browser_layout import wait_for_dockview_pointer_target
from tests.browser_helpers.browser_layout import wait_for_dockview_tab_geometry
from tests.gate_harness import assert_computed_style
from tests.gate_harness import run_when_browser_ready
from tests.helpers.terminal_navigation import TERMINAL_NAVIGATION_ACK_CEILING_MS
from tests.helpers.terminal_navigation import assert_terminal_navigation_ack_semantics
from tests.helpers.terminal_navigation import terminal_navigation_ack_metrics
from tests.latency_calibration import CALIBRATION_ADMISSION_MS
from tests.latency_calibration import CALIBRATION_REFERENCE_MS
from tests.latency_calibration import NOT_CERTIFIABLE
from tests.latency_calibration import NotCertifiableError
from tests.latency_calibration import assert_fixed_ceiling
from tests.latency_calibration import browser_calibration_qualification
from tests.latency_calibration import calibration_pressure_verdict
from tests.latency_calibration import certification_phase_fixture
from tests.latency_calibration import fixed_ceiling_verdict
from tests.latency_calibration import merged_qualification
from tests.latency_calibration import run_browser_latency_calibration
from tests.latency_calibration import start_independent_browser_pressure
from tests.latency_calibration import stop_independent_browser_pressure
from tests.latency_calibration import write_latency_evidence


pytestmark = pytest.mark.browser

# Fixed product budgets. A host measurement may decide WHETHER these are certifiable; it may
# never decide what they are. Both are asserted only in the exclusive certification phase.
I3A_DRAG_PREVIEW_CEILING_MS = 150.0
I3B_DOCKVIEW_LOAD_CEILING_MS = 100.0
I3_DRAG_SAMPLE_COUNT = 30
I3_FORCED_RED_INJECTED_DELAY_MS = 220
# Four interleaved quiet/busy pairs: the same 28 samples the quiet side already collected, now
# collected on BOTH sides and adjacent in time, so a host that changes during the unit changes both.
I3_PRESSURE_ROUNDS = 4


def _retire_expected_auto_approve_failure(browser, message):
    consume_only_expected_js_debug_api_errors(
        browser,
        (
            {
                "path": "/api/auto-approve",
                "method": "POST",
                "query": {"session": "1", "enabled": "1"},
                "error": message,
            },
        ),
    )


def _load_toggle_fixture(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    run_when_browser_ready(
        browser,
        "return document.querySelector('[data-yolo-session=\"1\"]') !== null;",
        globals_required={"toggleAutoApprove": "function"},
        dom_anchors=("[data-yolo-session=\"1\"]", "#status"),
    )
    browser.execute_script(
        """
        window.__fixtureInterceptAutoApproveFetch = handler => {
          const originalFetch = window.fetch;
          const scopedFetch = (url, options = {}) => {
            if (!String(url).startsWith('/api/auto-approve?')) return originalFetch(url, options);
            return handler(url, options);
          };
          window.fetch = scopedFetch;
          return () => {
            if (window.fetch === scopedFetch) window.fetch = originalFetch;
          };
        };
        """
    )


def _dockview_drag_preview(browser, *, x_ratio, y_ratio):
    start = wait_for_dockview_pointer_target(browser, '.dockview-pane-tab[data-pane-tab="2"]')
    target = browser.execute_script(
        """
        const group = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]')?.closest('.dv-groupview');
        const rect = group?.getBoundingClientRect();
        return rect && {x: Math.round(rect.left + rect.width * arguments[0]), y: Math.round(rect.top + rect.height * arguments[1])};
        """,
        x_ratio,
        y_ratio,
    )
    assert start and target
    before = browser.execute_script(
        "return {started: runtimeState.layoutMutationGeneration, completed: runtimeState.layoutMutationCompletedGeneration};"
    )
    preview = None
    try:
        cdp_drag_hold(browser, start, target, steps=20)
        preview = browser.execute_script(
            """
            const point = {x: arguments[0], y: arguments[1]};
            const visible = node => {
              const rect = node.getBoundingClientRect();
              const style = getComputedStyle(node);
              return rect.width > 0 && rect.height > 0 && style.display !== 'none'
                && style.visibility !== 'hidden' && style.opacity !== '0';
            };
            const previews = Array.from(document.querySelectorAll('.dv-drop-target-selection, .dv-drop-target-anchor'))
              .filter(visible)
              .map(node => {
                const rect = node.getBoundingClientRect();
                return {className: node.className, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom};
              });
            const grid = document.getElementById('grid');
            const hit = document.elementFromPoint(point.x, point.y);
            return {
              point,
              previews,
              rootPreview: grid?.classList.contains('drop-preview-root') || false,
              rootZone: ['left', 'right', 'top', 'bottom'].find(zone => grid?.classList.contains(`drop-preview-${zone}`)) || '',
              hitId: hit?.id || '',
              hitClass: hit?.className || '',
              hitInAttentionRail: Boolean(hit?.closest?.('#attentionAlerts')),
            };
            """,
            target["x"],
            target["y"],
        )
    finally:
        cdp_release(browser, target)
    try:
        completion = WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                const state = {started: runtimeState.layoutMutationGeneration, completed: runtimeState.layoutMutationCompletedGeneration};
                return state.started > arguments[0] && state.completed >= state.started ? state : false;
                """,
                before["started"],
            )
        )
    except TimeoutException as exc:
        diagnostic = browser.execute_script(
            """
            const counters = Object.fromEntries(clientPerfSummary().map(counter => [counter.name, counter]));
            return {
              started: runtimeState.layoutMutationGeneration,
              completed: runtimeState.layoutMutationCompletedGeneration,
              pending: runtimeState.pendingLayoutMutationGeneration,
              pendingRender: Boolean(pendingLayoutRender),
              pendingRenderFrame: Boolean(pendingLayoutRenderFrame),
              pendingLoadFrame: Boolean(dockviewLayoutState.pendingLoadFrame),
              renderPanels: counters.renderPanels?.count || 0,
              dockviewLoads: counters.dockviewLoadLayout?.count || 0,
            };
            """
        )
        raise AssertionError({"before": before, "preview": preview, "diagnostic": diagnostic}) from exc
    preview["layoutCompletion"] = completion
    return preview


def _install_i3a_drag_sampler(browser, *, injected_delay_ms=0):
    browser.execute_script(
        """
        window.__i3aSamples = [];
        window.__i3aAttemptId = 0;
        document.addEventListener('pointerdown', event => {
          if (!event.target.closest('.dockview-pane-tab[data-pane-tab="2"]')) return;
          const sample = {attemptId: window.__i3aAttemptId, startedAt: null, settledAt: null};
          window.__i3aSamples.push(sample);
          const firstMove = moveEvent => {
            if (!(moveEvent.buttons & 1)) return;
            sample.startedAt = performance.now();
            window.removeEventListener('pointermove', firstMove, true);
            const delayUntil = performance.now() + Number(arguments[0] || 0);
            while (performance.now() < delayUntil) window.__i3aInjectedDelayChecksum = Math.random();
          };
          window.addEventListener('pointermove', firstMove, true);
          const visible = node => {
            const rect = node.getBoundingClientRect();
            const style = getComputedStyle(node);
            return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
          };
          const poll = () => {
            const preview = Array.from(document.querySelectorAll('.dv-drop-target-selection, .dv-drop-target-anchor')).some(visible)
              || document.getElementById('grid')?.classList.contains('drop-preview-root');
            if (preview) sample.settledAt = performance.now();
            else requestAnimationFrame(poll);
          };
          requestAnimationFrame(poll);
        }, true);
        """,
        injected_delay_ms,
    )


def _measure_i3a_drag_preview(browser):
    start = wait_for_dockview_pointer_target(browser, '.dockview-pane-tab[data-pane-tab="2"]')
    target = browser.execute_script(
        """
        const group = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]')?.closest('.dv-groupview');
        const rect = group?.getBoundingClientRect();
        return rect && {x: Math.round(rect.left + rect.width * .02), y: Math.round(rect.top + rect.height * .5)};
        """
    )
    assert start and target
    attempt = browser.execute_script(
        """
        window.__i3aAttemptId += 1;
        return {attemptId: window.__i3aAttemptId, sampleCount: window.__i3aSamples.length};
        """
    )
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": start["x"], "y": start["y"], "button": "none"})
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": start["x"], "y": start["y"], "button": "left", "buttons": 1, "clickCount": 1})
    for ratio in (.25, .5, .75, 1):
        browser.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseMoved",
                "x": round(start["x"] + (target["x"] - start["x"]) * ratio),
                "y": round(start["y"] + (target["y"] - start["y"]) * ratio),
                "button": "left",
                "buttons": 1,
            },
        )
    settled = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const sample = window.__i3aSamples.find(candidate => candidate.attemptId === arguments[0]);
        if (!sample) return done({missingAttempt: arguments[0]});
        const timeoutAt = performance.now() + 1000;
        const poll = () => {
          if (sample.settledAt !== null) return done({elapsedMs: sample.settledAt - sample.startedAt});
          if (performance.now() > timeoutAt) return done({timedOutAttempt: arguments[0]});
          requestAnimationFrame(poll);
        };
        poll();
        """,
        attempt["attemptId"],
    )
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": target["x"], "y": target["y"], "button": "left", "buttons": 0, "clickCount": 1})
    after_sample_count = browser.execute_script("return window.__i3aSamples.length;")
    assert after_sample_count == attempt["sampleCount"] + 1, {
        "attempt": attempt,
        "afterSamples": after_sample_count,
        "start": start,
        "target": target,
        "settled": settled,
    }
    assert "elapsedMs" in settled, settled
    return settled["elapsedMs"]


def test_i1_tab_drag_reaches_every_drop_target_without_overlay_interception(browser, tmp_path):
    """A tab drag activates each edge drop target and the attention-toast rail never intercepts the pointer."""
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=left&tabs=left:1,2",
        sessions=["1", "2"],
        grid_width=1100,
        grid_height=500,
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    for x_ratio, y_ratio, zone in ((0.02, 0.5, "left"), (0.98, 0.5, "right"), (0.5, 0.14, "top"), (0.5, 0.95, "bottom")):
        preview = _dockview_drag_preview(browser, x_ratio=x_ratio, y_ratio=y_ratio)
        assert preview["hitInAttentionRail"] is False, {"zone": zone, "preview": preview}
        assert preview["previews"] or (preview["rootPreview"] and preview["rootZone"] == zone), {"zone": zone, "preview": preview}


def test_i2_active_drop_target_matches_pointer_geometry_at_top_left(browser, tmp_path):
    """At top-left, the active preview contains the pointer instead of indicating a different split target."""
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=left&tabs=left:1,2",
        sessions=["1", "2"],
        grid_width=1100,
        grid_height=500,
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    preview = _dockview_drag_preview(browser, x_ratio=0.02, y_ratio=0.02)
    contained = [
        target for target in preview["previews"]
        if target["left"] <= preview["point"]["x"] <= target["right"]
        and target["top"] <= preview["point"]["y"] <= target["bottom"]
    ]
    assert contained or (preview["rootPreview"] and preview["rootZone"] in {"left", "top"}), preview


# Admission and host fitness both come from the one owner in tests/latency_calibration.py: it keeps
# an exclusive-phase unit out of the shared parallel lane, and refuses an unqualified host, before
# any browser or server fixture is built.
certification_phase_only = certification_phase_fixture()


def _load_dockview_drag_fixture(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=left&tabs=left:1,2",
        sessions=["1", "2"],
        grid_width=1100,
        grid_height=500,
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)


def _i3b_drag_series(browser, tmp_path):
    """Run I3_DRAG_SAMPLE_COUNT real Dockview drags and report both the counters and the durations.

    One body, two callers. The parallel lane reads the counters, which are counts and identities an
    oversubscribed renderer cannot change; the exclusive phase additionally judges the durations.
    """

    _load_dockview_drag_fixture(browser, tmp_path)
    first_generation = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return runtimeState.layoutMutationGeneration === runtimeState.layoutMutationCompletedGeneration
              ? {completed: runtimeState.layoutMutationCompletedGeneration}
              : false;
            """
        )
    )["completed"]
    browser.execute_script(
        """
        clearClientPerfCounters();
        window.__i3bDockviewLoadSamples = [];
        window.__i3bDockviewTransitions = [];
        window.__i3bTransitionIndex = 0;
        window.__i3bOriginalClientPerfEnd = clientPerfEnd;
        clientPerfEnd = function(token, details = {}) {
          const counter = window.__i3bOriginalClientPerfEnd(token, details);
          const tracked = new Set([
            'dockviewFromJson',
            'dockviewRefreshTabs',
            'dockviewSyncMountedPanels',
            'dockviewLoadLayout',
            'renderPanels',
          ]);
          if (tracked.has(token?.name)) {
            const index = window.__i3bTransitionIndex;
            const transition = window.__i3bDockviewTransitions[index - 1] || {index};
            transition[token.name] = counter?.lastMs;
            window.__i3bDockviewTransitions[index - 1] = transition;
          }
          if (token?.name === 'dockviewLoadLayout') window.__i3bDockviewLoadSamples.push(counter?.lastMs);
          return counter;
        };
        """
    )
    transitions = []
    for index in range(I3_DRAG_SAMPLE_COUNT):
        browser.execute_script("window.__i3bTransitionIndex = arguments[0];", index + 1)
        _dockview_drag_preview(browser, x_ratio=0.02, y_ratio=0.5)
        sample = browser.execute_script(
            "return {duration: window.__i3bDockviewLoadSamples.at(-1), components: window.__i3bDockviewTransitions.at(-1)};"
        )
        transitions.append({"index": index + 1, "raw_measured_ms": sample["duration"], "component_ms": sample["components"]})
    counters = browser.execute_script(
        """
        return Object.fromEntries(clientPerfSummary().map(counter => [counter.name, counter]));
        """
    )
    return {
        "transitions": transitions,
        "counters": counters,
        "recorded_samples": browser.execute_script("return window.__i3bDockviewLoadSamples.slice();"),
        "first_generation": first_generation,
        "final_generation": browser.execute_script("return runtimeState.layoutMutationCompletedGeneration;"),
    }


def _assert_i3b_series_complete(observed):
    """Every drag ran exactly once and every recorded duration belongs to a real transition."""

    counters = observed["counters"]
    transitions = observed["transitions"]
    recorded_samples = observed["recorded_samples"]
    dockview_loads = counters.get("dockviewLoadLayout", {})
    assert counters.get("renderPanels", {}).get("count") == I3_DRAG_SAMPLE_COUNT, counters
    assert counters.get("dockviewFromJson", {}).get("count") == I3_DRAG_SAMPLE_COUNT, counters
    assert dockview_loads.get("count") == I3_DRAG_SAMPLE_COUNT, counters
    assert len(recorded_samples) == len(transitions) == I3_DRAG_SAMPLE_COUNT, {
        "recordedSamples": recorded_samples,
        "transitions": transitions,
    }
    assert recorded_samples == [transition["raw_measured_ms"] for transition in transitions], transitions
    assert dockview_loads.get("maxMs") == max(recorded_samples), {
        "counter": dockview_loads,
        "recordedSamples": recorded_samples,
    }
    assert observed["final_generation"] - observed["first_generation"] == I3_DRAG_SAMPLE_COUNT, {
        "firstGeneration": observed["first_generation"],
        "finalGeneration": observed["final_generation"],
        "counters": counters,
    }


def test_i3b_drag_render_panels_completes_every_layout_load_exactly_once(browser, tmp_path):
    """Thirty real Dockview drags each run one full render/load transition and advance one generation.

    Release-blocking correctness, safe in the parallel browser lane: every assertion is a count or an
    identity. The wall ceiling this test used to carry is certified separately, below.
    """

    _assert_i3b_series_complete(_i3b_drag_series(browser, tmp_path))


def test_i3b_certification_dockview_load_layout_holds_the_fixed_ceiling(certification_phase_only, browser, tmp_path, request):
    """Certify the slowest of thirty Dockview full loads against the fixed 100 ms ceiling.

    The ceiling is the product's budget and is identical on every host. The host qualifier (already
    consulted by `certification_phase_only`) and the pre-page renderer calibration decide only
    whether this box may certify at all; either refusing produces NOT CERTIFIABLE with its raw
    evidence, never a skip and never a widened budget.
    """

    calibration = run_browser_latency_calibration(browser)
    observed = _i3b_drag_series(browser, tmp_path)
    _assert_i3b_series_complete(observed)
    assert_fixed_ceiling(
        nodeid=request.node.nodeid,
        label="I3b dockview_load_layout",
        raw_measured_ms=max(observed["recorded_samples"]),
        ceiling_ms=I3B_DOCKVIEW_LOAD_CEILING_MS,
        qualification=merged_qualification(certification_phase_only, browser_calibration_qualification(calibration)),
        statistic="max",
        extra_evidence={"sample_count": I3_DRAG_SAMPLE_COUNT, "transitions": observed["transitions"]},
    )


def test_i3c_certification_terminal_navigation_ack_holds_the_fixed_ceiling(certification_phase_only, browser, tmp_path, request):
    """Certify immediate tab/window acknowledgement against the fixed 50 ms ceiling."""

    calibration = run_browser_latency_calibration(browser)
    metrics = terminal_navigation_ack_metrics(browser, tmp_path)
    assert_terminal_navigation_ack_semantics(metrics)
    samples = [float(metrics[owner]["elapsedMs"]) for owner in ("tabAck", "windowAck")]
    assert_fixed_ceiling(
        nodeid=request.node.nodeid,
        label="I3c terminal_navigation_ack",
        raw_measured_ms=max(samples),
        ceiling_ms=TERMINAL_NAVIGATION_ACK_CEILING_MS,
        qualification=merged_qualification(certification_phase_only, browser_calibration_qualification(calibration)),
        statistic="max",
        extra_evidence={"sample_count": len(samples), "samples_ms": samples, "metrics": metrics},
    )


def _i3a_drag_preview_series(browser, tmp_path, *, injected_delay_ms=0, sample_count=I3_DRAG_SAMPLE_COUNT):
    """Run sample_count native pointer drags and return every measured preview settle time."""

    _load_dockview_drag_fixture(browser, tmp_path)
    _install_i3a_drag_sampler(browser, injected_delay_ms=injected_delay_ms)
    return [_measure_i3a_drag_preview(browser) for _ in range(sample_count)]


def test_i3a_drag_preview_becomes_visible_for_every_drag(browser, tmp_path):
    """Every one of thirty native pointer drags produces a visible drop preview.

    Release-blocking correctness with no timing in it: `_measure_i3a_drag_preview` fails when a drag
    produces no sample or no preview within its own bound, so this reds on a lost preview while
    staying insensitive to renderer scheduling. The wall ceiling is certified separately, below.
    """

    samples = _i3a_drag_preview_series(browser, tmp_path)
    assert len(samples) == I3_DRAG_SAMPLE_COUNT, samples
    assert all(sample >= 0 for sample in samples), samples


def test_i3a_certification_drag_preview_holds_the_fixed_ceiling(certification_phase_only, browser, tmp_path, request):
    """Certify the slowest of thirty drag previews against the fixed 150 ms ceiling."""

    calibration = run_browser_latency_calibration(browser)
    samples = _i3a_drag_preview_series(browser, tmp_path)
    assert len(samples) == I3_DRAG_SAMPLE_COUNT, samples
    assert_fixed_ceiling(
        nodeid=request.node.nodeid,
        label="I3a drag_preview",
        raw_measured_ms=max(samples),
        ceiling_ms=I3A_DRAG_PREVIEW_CEILING_MS,
        qualification=browser_calibration_qualification(calibration),
        statistic="max",
        extra_evidence={"sample_count": I3_DRAG_SAMPLE_COUNT, "samples_ms": samples},
    )


def test_i3_calibration_qualifies_the_renderer_and_never_widens_the_ceiling():
    """Calibration decides WHETHER a verdict may be reached. The ceiling is the same either way.

    This pins the removal of the old 1x-6x calibration factor: a slower machine authorising a slower
    product was the exact antipattern the certification phase exists to delete.
    """

    fast = browser_calibration_qualification({"calibrationNowMs": CALIBRATION_REFERENCE_MS / 2, "samplesMs": [1.0], "statistic": "p75"})
    slow = browser_calibration_qualification({"calibrationNowMs": CALIBRATION_ADMISSION_MS * 6, "samplesMs": [100.0], "statistic": "p75"})
    assert fast["qualified"] is True and fast["reasons"] == [] and fast["at_reference"] is True
    assert slow["qualified"] is False and slow["reasons"][0]["signal"] == "browser_calibration_p75_ms"

    # Admission is the outer envelope and the reference is the stricter statistic selector. They
    # are two thresholds on one measurement, and they may never invert.
    assert CALIBRATION_REFERENCE_MS <= CALIBRATION_ADMISSION_MS
    between = browser_calibration_qualification({"calibrationNowMs": (CALIBRATION_REFERENCE_MS + CALIBRATION_ADMISSION_MS) / 2, "samplesMs": [20.0], "statistic": "p75"})
    assert between["qualified"] is True and between["at_reference"] is False

    # One measurement, two hosts, one verdict: the ceiling never moves and neither does the outcome.
    for label in ("on a qualified renderer", "on an unqualified renderer"):
        verdict = fixed_ceiling_verdict(label=label, raw_measured_ms=101.0, ceiling_ms=I3B_DOCKVIEW_LOAD_CEILING_MS)
        assert verdict["ceiling_ms"] == I3B_DOCKVIEW_LOAD_CEILING_MS and verdict["passed"] is False
    assert "factor" not in fixed_ceiling_verdict(label="fields", raw_measured_ms=1.0, ceiling_ms=100.0)
    assert not hasattr(latency_calibration, "MAX_LATENCY_FACTOR"), "the calibration multiplication must not come back"
    assert not hasattr(latency_calibration, "latency_budget_result"), "the multiplying budget helper must not come back"


def test_i3_negative_control_unqualified_host_refuses_instead_of_passing():
    """An unqualified host must produce NOT CERTIFIABLE, never a skip and never a pass.

    The measurement below is comfortably inside the ceiling; only the host decides the outcome, and
    the refusal reds whatever observes it.
    """

    inside_the_ceiling = I3A_DRAG_PREVIEW_CEILING_MS / 10
    unqualified = browser_calibration_qualification({"calibrationNowMs": CALIBRATION_ADMISSION_MS * 4, "samplesMs": [100.0], "statistic": "p75"})
    with pytest.raises(NotCertifiableError) as refusal:
        assert_fixed_ceiling(
            nodeid="negative-control",
            label="I3 unqualified host",
            raw_measured_ms=inside_the_ceiling,
            ceiling_ms=I3A_DRAG_PREVIEW_CEILING_MS,
            qualification=unqualified,
        )
    assert NOT_CERTIFIABLE in str(refusal.value)
    assert refusal.value.evidence["reasons"][0]["measured"] == CALIBRATION_ADMISSION_MS * 4
    assert refusal.value.evidence["verdicts"][0]["passed"] is True, "the refusal must carry the raw verdict it declined to certify"


def test_i3_calibration_probe_moves_under_independent_browser_pressure(browser, request):
    """The probe reads higher under independent renderer pressure, measured identically on both sides.

    Quiet and busy rounds are interleaved inside ONE warmed page context, so both sides see the same
    host conditions and the same renderer state. That removes the two asymmetries that used to decide
    this unit instead of the product:

    * The quiet side navigated to a fresh document before every round and paid its cold-context cost,
      while the busy side ran warm because it reused the page the pressure loop was already burning
      in. Across 576 recorded quiet rounds the FIRST sample of a round exceeded 29 ms 2.26% of the
      time and the LAST 0.00%, and the round maximum landed on one of the first two samples in 74% of
      rounds against a 29% chance share. The discarded warm-up cost was real and it was one-sided.
    * The quiet estimator was a maximum over four rounds of a per-round p75 -- effectively a high
      quantile of 28 samples -- against a single busy p75 over 7. Two preempted quiet samples out of
      28 were therefore enough to invert the ratio.

    Neither asymmetry is a product property: this probe runs on `about:blank` with no application
    code and no server, so what those tails record is the machine the gate is running on. Recorded
    2026-08-09 in the gate, quiet samples [14.2, 41.0, 112.4, 19.9, 18.6, 22.1, 15.1] produced a
    "quiet" reading of 41.0 ms against a busy 33.2 ms while the quiet MEDIAN was 19.9 ms.
    """

    quiet_samples_ms: list[float] = []
    busy_samples_ms: list[float] = []
    # One discarded round warms the context both sides then share; it is never measured.
    run_browser_latency_calibration(browser)
    for _ in range(I3_PRESSURE_ROUNDS):
        quiet_samples_ms.extend(run_browser_latency_calibration(browser, reset_page=False)["samplesMs"])
        start_independent_browser_pressure(browser)
        try:
            pressure_ticks = browser.execute_async_script(
                """
                const done = arguments[arguments.length - 1];
                const wait = () => {
                  if ((window.__yolomuxCalibrationPressureTicks || 0) >= 2) return done(window.__yolomuxCalibrationPressureTicks);
                  setTimeout(wait, 0);
                };
                wait();
                """
            )
            assert pressure_ticks >= 2
            busy_samples_ms.extend(run_browser_latency_calibration(browser, reset_page=False)["samplesMs"])
        finally:
            stop_independent_browser_pressure(browser)
    verdict = calibration_pressure_verdict(quiet_samples_ms=quiet_samples_ms, busy_samples_ms=busy_samples_ms)
    assert verdict["quiet_sample_count"] == verdict["busy_sample_count"], verdict
    artifact = write_latency_evidence(nodeid=request.node.nodeid, label="I3 calibration variation", payload=verdict)
    assert verdict["passed"], {**verdict, "artifact": str(artifact)}


def test_i3_negative_control_the_pressure_verdict_reds_on_a_probe_that_did_not_move():
    """The matched-median comparison must still red, or the rewrite above would be a deletion.

    The third case is the one the old maximum-vs-p75 comparison actually MISSED. Under pressure the
    renderer can stop waiting for vsync, so the probe reads BELOW its quiet value. Three recorded
    runs had busy medians of 7.8, 11.0 and 8.3 ms against quiet medians near 16.7 ms and still passed
    the old rule, with ratios of 1.43, 1.42 and 1.66, because one or two surviving vsync-aligned
    samples carried a seven-sample p75. Replayed against all 144 recorded runs, the matched-median
    rule turns the six host-contention reds green and those three false greens red.
    """

    quiet_ms = [16.6] * 28
    moved = calibration_pressure_verdict(quiet_samples_ms=quiet_ms, busy_samples_ms=[31.0] * 28)
    assert moved["passed"] is True and moved["statistic"] == "median", moved
    for unmoved_busy_ms in ([16.7] * 28, [17.5] * 28, [7.8] * 28):
        verdict = calibration_pressure_verdict(quiet_samples_ms=quiet_ms, busy_samples_ms=unmoved_busy_ms)
        assert verdict["passed"] is False, verdict

    # The regression this unit was rewritten for, replayed from the gate run of 2026-08-09 03:28:
    # two preempted samples out of 28 are the host, not the product, and may not decide the verdict.
    preempted_ms = [16.6] * 26 + [41.0, 112.4]
    assert calibration_pressure_verdict(quiet_samples_ms=preempted_ms, busy_samples_ms=[31.0] * 28)["passed"] is True

    with pytest.raises(ValueError):
        calibration_pressure_verdict(quiet_samples_ms=[], busy_samples_ms=[31.0])


def test_i3_negative_control_forced_red_breaches_the_fixed_ceiling_on_a_qualified_host(browser, tmp_path, request):
    """A deliberately slow interaction must still red on a host the qualifier accepts.

    Without this, a phase that can only ever refuse or pass would be indistinguishable from a phase
    that cannot fail. The qualification here is a declared negative-control constant, so the proof
    does not depend on whatever this machine happens to be doing; the machine's real calibration is
    recorded alongside it.
    """

    calibration = run_browser_latency_calibration(browser)
    observed = browser_calibration_qualification(calibration)
    raw_measured_ms = _i3a_drag_preview_series(browser, tmp_path, injected_delay_ms=I3_FORCED_RED_INJECTED_DELAY_MS, sample_count=1)[0]
    assert raw_measured_ms >= I3_FORCED_RED_INJECTED_DELAY_MS, raw_measured_ms
    forced_qualified = {"qualified": True, "reasons": [], "negative_control": True, "observed_browser_calibration": observed}
    verdict = fixed_ceiling_verdict(label="I3 forced-red drag_preview", raw_measured_ms=raw_measured_ms, ceiling_ms=I3A_DRAG_PREVIEW_CEILING_MS)
    assert verdict["ceiling_ms"] == I3A_DRAG_PREVIEW_CEILING_MS and verdict["passed"] is False, verdict
    with pytest.raises(AssertionError) as failure:
        assert_fixed_ceiling(
            nodeid=request.node.nodeid,
            label="I3 forced-red drag_preview",
            raw_measured_ms=raw_measured_ms,
            ceiling_ms=I3A_DRAG_PREVIEW_CEILING_MS,
            qualification=forced_qualified,
        )
    message = str(failure.value)
    assert not isinstance(failure.value, NotCertifiableError), message
    for field in ("raw_measured_ms", "ceiling_ms", "passed", "qualification", "artifact"):
        assert field in message, message


def test_k1_every_mutation_visibly_acknowledges_within_one_frame_and_settles(browser, tmp_path):
    """The YOLO toggle visibly acknowledges within one frame, remains visible while pending, and settles after its request completes."""
    _load_toggle_fixture(browser, tmp_path)
    pending = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const before = document.getElementById('status')?.textContent || '';
        window.__fixtureHoldAutoApprove = true;
        const request = toggleAutoApprove('1');
        requestAnimationFrame(() => {
          const status = document.getElementById('status');
          done({before, text: status?.textContent || ''});
        });
        """
    )
    settled = ""
    try:
        assert pending["text"].strip() and pending["text"] != pending["before"], pending
        assert_computed_style(
            browser,
            "#status",
            {
                "display": lambda value: value != "none",
                "visibility": lambda value: value == "visible",
                "opacity": lambda value: float(value) > 0,
            },
        )
    finally:
        settled = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            window.__fixtureReleaseAutoApprove();
            requestAnimationFrame(() => requestAnimationFrame(() => done(document.getElementById('status')?.textContent || '')));
            """
        )
    assert settled.strip(), settled


def test_k2_submitted_control_disables_before_request_and_reenables_in_finally(browser, tmp_path):
    """The YOLO toggle disables before its request resolves and re-enables after both success and failure."""
    _load_toggle_fixture(browser, tmp_path)
    states = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const button = document.querySelector('[data-yolo-session="1"]');
        window.__fixtureHoldAutoApprove = true;
        const success = toggleAutoApprove('1');
        requestAnimationFrame(async () => {
          const disabledWhilePending = button.disabled === true;
          window.__fixtureReleaseAutoApprove();
          await success;
          const enabledAfterSuccess = !button.disabled;
          const restoreFetch = window.__fixtureInterceptAutoApproveFetch(
            () => Promise.reject(new TypeError('fixture mutation failure')),
          );
          await toggleAutoApprove('1');
          const enabledAfterFailure = !button.disabled;
          restoreFetch();
          done({disabledWhilePending, enabledAfterSuccess, enabledAfterFailure});
        });
        """
    )
    _retire_expected_auto_approve_failure(browser, "fixture mutation failure")
    assert states == {
        "disabledWhilePending": True,
        "enabledAfterSuccess": True,
        "enabledAfterFailure": True,
    }, states


def test_k3_repeated_mutation_activation_issues_one_request(browser, tmp_path):
    """Repeated YOLO-toggle activation while pending emits exactly one POST request."""
    _load_toggle_fixture(browser, tmp_path)
    requests = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__fixtureHoldAutoApprove = true;
        const before = window.__bootFetches.length;
        void toggleAutoApprove('1');
        void toggleAutoApprove('1');
        requestAnimationFrame(() => {
          const pendingPosts = window.__bootFetches.slice(before).filter(item => item.path === '/api/auto-approve' && item.method === 'POST').length;
          window.__fixtureReleaseAutoApprove();
          done(pendingPosts);
        });
        """
    )
    assert requests == 1, requests


def test_k4_yolo_toggle_reverts_its_optimistic_state_on_failure(browser, tmp_path):
    """The YOLO toggle changes optimistically during a request and restores its prior visual state after failure."""
    _load_toggle_fixture(browser, tmp_path)
    states = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const button = document.querySelector('[data-yolo-session="1"]');
        const before = button.getAttribute('aria-pressed');
        let rejectRequest;
        const restoreFetch = window.__fixtureInterceptAutoApproveFetch(
          () => new Promise((_resolve, reject) => { rejectRequest = reject; }),
        );
        const request = toggleAutoApprove('1');
        requestAnimationFrame(async () => {
          const pending = button.getAttribute('aria-pressed');
          rejectRequest(new TypeError('fixture mutation failure'));
          await request;
          const after = button.getAttribute('aria-pressed');
          restoreFetch();
          done({before, pending, after});
        });
        """
    )
    _retire_expected_auto_approve_failure(browser, "fixture mutation failure")
    assert states["pending"] != states["before"], states
    assert states["after"] == states["before"], states


def test_k5_mutation_acknowledgement_preserves_live_status_semantics(browser, tmp_path):
    """The visible pending YOLO acknowledgement retains ``role=status`` and a non-off ``aria-live`` value."""
    _load_toggle_fixture(browser, tmp_path)
    semantics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__fixtureHoldAutoApprove = true;
        void toggleAutoApprove('1');
        requestAnimationFrame(() => {
          const status = document.getElementById('status');
          done({role: status?.getAttribute('role'), live: status?.getAttribute('aria-live')});
          window.__fixtureReleaseAutoApprove();
        });
        """
    )
    assert semantics["role"] == "status", semantics
    assert semantics["live"] in {"polite", "assertive"}, semantics


def test_k6_failed_mutation_renders_its_typed_reason_in_a_visible_control_associated_error(browser, tmp_path):
    """A rejected YOLO toggle clears pending state and shows its typed reason beside the initiating control."""
    _load_toggle_fixture(browser, tmp_path)
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const button = document.querySelector('[data-yolo-session="1"]');
        let rejectRequest;
        const restoreFetch = window.__fixtureInterceptAutoApproveFetch(
          () => new Promise((_resolve, reject) => { rejectRequest = reject; }),
        );
        const visible = node => {
          if (!node) return false;
          const style = getComputedStyle(node);
          const rect = node.getBoundingClientRect();
          return !node.closest('.a11y-only, .sr-only, [aria-hidden="true"]')
            && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) > 0
            && rect.width > 1 && rect.height > 1;
        };
        const request = toggleAutoApprove('1');
        const delegationRoot = '/fixture-k6-watch-root-delegation';
        const backgroundWatchRoots = window.fetch('/api/watch/roots', {
          method: 'POST',
          body: JSON.stringify({roots: [delegationRoot]}),
        });
        requestAnimationFrame(async () => {
          const error = new Error('fixture typed mutation reason');
          error.status = 503;
          error.payload = {reason: 'fixture typed mutation reason'};
          const backgroundResponse = await backgroundWatchRoots;
          rejectRequest(error);
          request.then(() => requestAnimationFrame(() => {
            const ids = (button.getAttribute('aria-describedby') || '').split(/\\s+/).filter(Boolean);
            const described = ids.map(id => document.getElementById(id)).filter(Boolean);
            const errorNode = described.find(node => visible(node) && (node.textContent || '').includes(error.payload.reason));
            const delegatedWatchRoots = window.__bootFetches.filter(item => (
              item.path === '/api/watch/roots' && item.body?.roots?.includes(delegationRoot)
            ));
            restoreFetch();
            done({
              pendingCleared: button.disabled === false && button.getAttribute('aria-busy') !== 'true',
              errorVisible: Boolean(errorNode),
              errorText: errorNode?.textContent || '',
              watchRootsDelegated: backgroundResponse.status === 200 && delegatedWatchRoots.length === 1,
            });
          }));
        });
        """
    )
    _retire_expected_auto_approve_failure(browser, "fixture typed mutation reason")
    assert result["pendingCleared"] is True, result
    assert result["errorVisible"] is True, result
    assert "fixture typed mutation reason" in result["errorText"], result
    assert result["watchRootsDelegated"] is True, result


@pytest.mark.parametrize("outcome", ("success", "failure"))
def test_k7_overdue_mutation_keeps_its_control_disabled_without_abort_and_converges_late(browser, tmp_path, outcome):
    """A held mutation shows an associated visible overdue notice, then converges after a late success or failure without aborting."""
    _load_toggle_fixture(browser, tmp_path)
    result = browser.execute_async_script(
        """
        const outcome = arguments[0];
        const done = arguments[arguments.length - 1];
        const button = document.querySelector('[data-yolo-session="1"]');
        const before = button.getAttribute('aria-pressed');
        let settleRequest;
        let signal = null;
        const restoreFetch = window.__fixtureInterceptAutoApproveFetch((_url, options = {}) => (
          new Promise((resolve, reject) => {
            signal = options.signal || null;
            settleRequest = {resolve, reject};
          })
        ));
        const visible = node => {
          if (!node) return false;
          const style = getComputedStyle(node);
          const rect = node.getBoundingClientRect();
          return !node.closest('.a11y-only, .sr-only, [aria-hidden="true"]')
            && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) > 0
            && rect.width > 1 && rect.height > 1;
        };
        const request = toggleAutoApprove('1');
        setTimeout(() => {
          const ids = (button.getAttribute('aria-describedby') || '').split(/\\s+/).filter(Boolean);
          const overdue = ids.map(id => document.getElementById(id)).find(node => visible(node) && /overdue|taking longer|still working/i.test(node.textContent || ''));
          const pending = {
            disabled: button.disabled === true,
            overdueVisible: Boolean(overdue),
            notAborted: signal?.aborted !== true,
          };
          if (outcome === 'success') {
            settleRequest.resolve(new Response(JSON.stringify({target: '1', enabled: true, last_action: 'on'}), {
              status: 200, headers: {'Content-Type': 'application/json'},
            }));
          } else {
            const error = new Error('fixture late mutation failure');
            error.status = 503;
            error.payload = {reason: 'fixture late mutation failure'};
            settleRequest.reject(error);
          }
          request.then(() => requestAnimationFrame(() => {
            const afterIds = (button.getAttribute('aria-describedby') || '').split(/\\s+/).filter(Boolean);
            const overdueAfter = afterIds.map(id => document.getElementById(id)).find(node => visible(node) && /overdue|taking longer|still working/i.test(node.textContent || ''));
            const errorVisible = Array.from(document.querySelectorAll('[aria-describedby]'))
              .some(control => (control.getAttribute('aria-describedby') || '').split(/\\s+/).some(id => {
                const node = document.getElementById(id);
                return visible(node) && (node.textContent || '').includes('fixture late mutation failure');
              }));
            restoreFetch();
            done({
              pending,
              enabled: button.disabled === false && button.getAttribute('aria-busy') !== 'true',
              overdueRevoked: !overdueAfter,
              committed: button.getAttribute('aria-pressed') !== before,
              rolledBack: button.getAttribute('aria-pressed') === before,
              errorVisible,
            });
          }));
        }, 350);
        """,
        outcome,
    )
    if outcome == "failure":
        _retire_expected_auto_approve_failure(browser, "fixture late mutation failure")
    assert result["pending"] == {"disabled": True, "overdueVisible": True, "notAborted": True}, result
    assert result["enabled"] is True and result["overdueRevoked"] is True, result
    if outcome == "success":
        assert result["committed"] is True, result
    else:
        assert result["rolledBack"] is True and result["errorVisible"] is True, result
