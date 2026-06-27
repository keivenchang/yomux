import re

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401


_CLAUDE_WORKING_ICON_SVG = """<svg viewBox="0 0 24 24" aria-hidden="true">
  <rect width="24" height="24" rx="5.5" fill="#cf7554"/>
  <g fill="#fff7f1">
    <path d="M11.1 2.4h1.8l1.1 7.9-2 .6-2-.6 1.1-7.9z"/>
    <path d="m17.8 4.3 1.4 1.1-4.3 6.7-2.1-1.3 5-6.5z"/>
    <path d="m21.5 10.2.3 1.8-8.2 2-1-2.3 8.9-1.5z"/>
    <path d="m20.2 16.8-1.1 1.4-6.7-4.3 1.3-2.1 6.5 5z"/>
    <path d="m13.8 21.5-1.8.3-2-8.2 2.3-1 1.5 8.9z"/>
    <path d="m6.2 19.7-1.4-1.1 4.3-6.7 2.1 1.3-5 6.5z"/>
    <path d="m2.5 13.8-.3-1.8 8.2-2 1 2.3-8.9 1.5z"/>
    <path d="m3.8 7.2 1.1-1.4 6.7 4.3-1.3 2.1-6.5-5z"/>
    <circle cx="12" cy="12" r="2.2"/>
  </g>
</svg>"""

_CODEX_WORKING_ICON_SVG = """<svg viewBox="0 0 24 24" aria-hidden="true">
  <path fill="#667ef8" d="M7.3 20.8c-3.1 0-5.7-2.4-5.9-5.5-.2-2.4 1.1-4.6 3.1-5.7C4.8 5.9 7.9 3 11.8 3c3.3 0 6.2 2.2 7 5.4 2.4.7 4 2.8 4 5.4 0 3.2-2.6 5.8-5.8 5.8-.9 1.1-2.2 1.8-3.8 1.8-1.2 0-2.3-.4-3.1-1.1-.8.3-1.8.5-2.8.5z"/>
  <path fill="#fff" d="M6.4 8.2c.5-.5 1.2-.5 1.7 0l2.8 2.8c.5.5.5 1.2 0 1.7l-2.8 2.8c-.5.5-1.2.5-1.7 0s-.5-1.2 0-1.7l1.9-1.9-1.9-1.9c-.5-.5-.5-1.3 0-1.8zM13 13.2h5.1c.7 0 1.2.5 1.2 1.2s-.5 1.2-1.2 1.2H13c-.7 0-1.2-.5-1.2-1.2s.5-1.2 1.2-1.2z"/>
</svg>"""


def _agent_status_glyph_html(kind, state, element_id):
    svg = _CLAUDE_WORKING_ICON_SVG if kind == "claude" else _CODEX_WORKING_ICON_SVG
    label = f"{'Claude' if kind == 'claude' else 'Codex'} {state}"
    dot_classes = [
        "status-indicator",
        "status-indicator--dot",
        f"status-indicator--{state}",
        "heartbeat-pulse",
        "agent-window-activity-icon",
        "agent-window-status-dot",
        f"agent-window-activity-icon--{state}",
    ]
    if state in ("attention", "cooldown"):
        dot_classes.append("attention-pulse")
    return f"""
      <span class="agent-window-activity agent-window-activity--{state}" title="{label}" aria-label="{label}" style="--attention-animation-delay:0s">
        <span id="{element_id}" class="agent-icon {kind} agent-window-activity-icon agent-window-agent-icon agent-window-activity-icon--{state} agent-window-agent-icon--{state}" aria-label="{label}" title="{label}">
          {svg}
        </span>
        <span id="{element_id}-dot" class="{' '.join(dot_classes)}" aria-hidden="true">●</span>
      </span>
    """


def _working_agent_glyph_html(kind, element_id):
    return _agent_status_glyph_html(kind, "working", element_id)


def test_debug_agent_status_y_axis_guides_align_with_labels(browser, tmp_path):
    page = tmp_path / "debug-agent-status-axis-guides.html"
    page.write_text(page_html("""
      <section class="js-debug-chart debug-chart-fixture" data-js-debug-chart="activity">
        <div class="js-debug-chart-head">
          <span class="js-debug-chart-title">Agent status</span>
        </div>
        <div class="js-debug-chart-body">
          <div class="js-debug-y-axis js-debug-y-axis--integer" data-js-debug-axis="activity">
            <span data-js-debug-axis-tick="activity" data-js-debug-axis-value="3" data-js-debug-axis-max="activity" style="--js-debug-axis-y: 6.667%;">3</span>
            <span data-js-debug-axis-tick="activity" data-js-debug-axis-value="2" style="--js-debug-axis-y: 35.556%;">2</span>
            <span data-js-debug-axis-tick="activity" data-js-debug-axis-value="1" style="--js-debug-axis-y: 64.444%;">1</span>
            <span data-js-debug-axis-tick="activity" data-js-debug-axis-value="0" data-js-debug-axis-zero="activity" style="--js-debug-axis-y: 93.333%;">0</span>
          </div>
          <div class="js-debug-plot">
            <svg class="js-debug-line-chart" viewBox="0 0 600 120" role="img" preserveAspectRatio="none">
              <line class="js-debug-grid-line js-debug-grid-line--integer" data-js-debug-grid-line="activity" data-js-debug-grid-value="3" x1="0" y1="8.0" x2="600" y2="8.0" vector-effect="non-scaling-stroke"></line>
              <line class="js-debug-grid-line js-debug-grid-line--integer" data-js-debug-grid-line="activity" data-js-debug-grid-value="2" x1="0" y1="42.7" x2="600" y2="42.7" vector-effect="non-scaling-stroke"></line>
              <line class="js-debug-grid-line js-debug-grid-line--integer" data-js-debug-grid-line="activity" data-js-debug-grid-value="1" x1="0" y1="77.3" x2="600" y2="77.3" vector-effect="non-scaling-stroke"></line>
              <line class="js-debug-grid-line js-debug-grid-line--integer" data-js-debug-grid-line="activity" data-js-debug-grid-value="0" x1="0" y1="112.0" x2="600" y2="112.0" vector-effect="non-scaling-stroke"></line>
            </svg>
          </div>
          <div class="js-debug-x-axis" data-js-debug-x-axis>
            <span data-js-debug-x-tick="start">start</span>
            <span data-js-debug-x-tick="mid">mid</span>
            <span data-js-debug-x-tick="end">end</span>
          </div>
        </div>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 24px; background: #111827; color: #e5e7eb; }
      .debug-chart-fixture { width: 560px; height: 260px; }
    """), encoding="utf-8")
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const svg = document.querySelector('.js-debug-line-chart');
        const svgRect = svg.getBoundingClientRect();
        return ['3', '2', '1', '0'].map(value => {
          const tick = document.querySelector(`[data-js-debug-axis-value="${value}"]`);
          const line = document.querySelector(`[data-js-debug-grid-value="${value}"]`);
          const tickRect = tick.getBoundingClientRect();
          const tickCenterY = tickRect.top + tickRect.height / 2;
          const lineY = svgRect.top + (Number(line.getAttribute('y1')) / 120) * svgRect.height;
          return {
            value,
            deltaY: Math.abs(tickCenterY - lineY),
            strokeWidth: Number.parseFloat(getComputedStyle(line).strokeWidth),
          };
        });
        """
    )
    assert max(item["deltaY"] for item in metrics) <= 0.75, metrics
    assert all(0 < item["strokeWidth"] <= 0.5 for item in metrics), metrics


def test_debug_graph_series_colors_are_distinct_and_theme_aware(browser, tmp_path):
    page = tmp_path / "debug-graph-series-colors.html"
    page.write_text(page_html("""
      <section class="js-debug-graph-view" id="debug-graph">
        <svg class="js-debug-line-chart" viewBox="0 0 20 20" role="img" preserveAspectRatio="none">
          <path class="js-debug-line js-debug-line--api" d="M0 1L20 1"></path>
          <path class="js-debug-line js-debug-line--sse" d="M0 3L20 3"></path>
          <path class="js-debug-line js-debug-line--cpu" d="M0 5L20 5"></path>
          <path class="js-debug-line js-debug-line--systemCpu" d="M0 7L20 7"></path>
        </svg>
        <span class="js-debug-legend-swatch js-debug-legend-swatch--api"></span>
        <span class="js-debug-legend-swatch js-debug-legend-swatch--sse"></span>
        <span class="js-debug-legend-swatch js-debug-legend-swatch--cpu"></span>
        <span class="js-debug-legend-swatch js-debug-legend-swatch--systemCpu"></span>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 24px; background: var(--bg); color: var(--text); }
      #debug-graph { width: 260px; }
    """), encoding="utf-8")
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const graph = document.getElementById('debug-graph');
        const line = name => getComputedStyle(document.querySelector(`.js-debug-line--${name}`)).stroke;
        const swatch = name => getComputedStyle(document.querySelector(`.js-debug-legend-swatch--${name}`)).color;
        const colorFor = value => {
          const probe = document.createElement('span');
          probe.style.color = value;
          graph.appendChild(probe);
          const color = getComputedStyle(probe).color;
          probe.remove();
          return color;
        };
        const colorDistance = (left, right) => {
          const rgb = color => (String(color).match(/[0-9.]+/g) || []).slice(0, 3).map(Number);
          const a = rgb(left);
          const b = rgb(right);
          return Math.sqrt(((a[0] - b[0]) ** 2) + ((a[1] - b[1]) ** 2) + ((a[2] - b[2]) ** 2));
        };
        const read = () => {
          const values = {
            line: {api: line('api'), sse: line('sse'), cpu: line('cpu'), systemCpu: line('systemCpu')},
            legend: {api: swatch('api'), sse: swatch('sse'), cpu: swatch('cpu'), systemCpu: swatch('systemCpu')},
            expected: {
              api: colorFor('var(--js-debug-api-series)'),
              sse: colorFor('var(--js-debug-sse-series)'),
              cpu: colorFor('var(--active-accent-bright)'),
              systemCpu: colorFor('var(--bad)'),
            },
          };
          values.apiSseDistance = colorDistance(values.line.api, values.line.sse);
          return values;
        };
        document.body.className = 'theme-dark';
        const dark = read();
        document.body.className = 'theme-light';
        const light = read();
        return {dark, light};
        """
    )
    for theme in ("dark", "light"):
        item = metrics[theme]
        assert item["line"]["api"] == item["legend"]["api"] == item["expected"]["api"], (theme, item)
        assert item["line"]["sse"] == item["legend"]["sse"] == item["expected"]["sse"], (theme, item)
        assert item["line"]["cpu"] == item["legend"]["cpu"] == item["expected"]["cpu"], (theme, item)
        assert item["line"]["systemCpu"] == item["legend"]["systemCpu"] == item["expected"]["systemCpu"], (theme, item)
        assert item["line"]["api"] != item["line"]["sse"], (theme, item)
        assert item["line"]["cpu"] != item["line"]["api"], (theme, item)
        assert item["line"]["cpu"] != item["line"]["systemCpu"], (theme, item)
        assert item["apiSseDistance"] >= 120, (theme, item)


def test_debug_graph_range_slider_hover_and_drag_zoom(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof debugGraphApplyServerHistory === 'function'
              && typeof setDebugGraphRange === 'function'
              && typeof renderDebugPanels === 'function'
              && document.querySelector('[data-js-debug-graph]') !== null;
            """
        )
    )
    metrics = browser.execute_script(
        """
        const now = Date.now();
        const records = [];
        for (let index = 0; index < 6; index += 1) {
          records.push({
            start: Math.floor((now - ((240 - (index * 30)) * 1000)) / 1000),
            duration: 1,
            sequence: 200 + index,
            api_count: index + 1,
            sse_count: index,
            latency_total_ms: 12 + index,
            latency_count: 1,
            bandwidth_bytes: 1024 * (index + 1),
            cpu_total_percent: 8 + index,
            cpu_count: 1,
            system_cpu_total_percent: 24 + index,
            system_cpu_count: 1,
          });
        }
        debugGraphApplyServerHistory({sequence: 300, records});
        setDebugGraphRange(300);
        renderDebugPanels({force: true});

        let panel = document.querySelector('.js-debug-panel');
        let graph = panel?.querySelector('[data-js-debug-graph]');
        let slider = graph?.querySelector('[data-js-debug-range-slider]');
        const stops = Array.from(graph?.querySelectorAll('datalist option[data-js-debug-range]') || [])
          .map(option => Number(option.dataset.jsDebugRange));
        let svgs = Array.from(graph?.querySelectorAll('.js-debug-line-chart') || []);
        if (!panel || !graph || !slider || svgs.length < 2) {
          return {error: 'missing graph fixture', stops, svgCount: svgs.length};
        }

        const sliderRect = slider.getBoundingClientRect();
        const sliderPointerDefaultAllowed = slider.dispatchEvent(new PointerEvent('pointerdown', {
          bubbles: true,
          cancelable: true,
          pointerId: 2,
          pointerType: 'mouse',
          button: 0,
          buttons: 1,
          clientX: sliderRect.left + (sliderRect.width / 2),
          clientY: sliderRect.top + (sliderRect.height / 2),
        }));
        const sliderSurvivedPointerDown = graph.querySelector('[data-js-debug-range-slider]') === slider;
        slider.value = '7.4';
        slider.dispatchEvent(new Event('input', {bubbles: true, cancelable: true}));
        const sliderValueDuringInput = slider.value;
        const sliderSurvivedInputDrag = graph.querySelector('[data-js-debug-range-slider]') === slider;
        refreshDebugPanelsFromEvents();
        const sliderSurvivedPassiveRefreshDuringDrag = graph.querySelector('[data-js-debug-range-slider]') === slider;
        slider.dispatchEvent(new Event('change', {bubbles: true, cancelable: true}));
        const sliderValueAfterSnap = slider.value;
        const sliderInputGrid = document.querySelector('[data-js-debug-chart-grid]');
        const sliderInputSeconds = (Number(sliderInputGrid?.dataset.jsDebugDomainEnd) - Number(sliderInputGrid?.dataset.jsDebugDomainStart)) / 1000;
        setDebugGraphRange(300);
        renderDebugPanels({force: true});

        panel = document.querySelector('.js-debug-panel');
        graph = panel?.querySelector('[data-js-debug-graph]');
        slider = graph?.querySelector('[data-js-debug-range-slider]');
        svgs = Array.from(graph?.querySelectorAll('.js-debug-line-chart') || []);
        if (!panel || !graph || !slider || svgs.length < 2) {
          return {error: 'missing graph after slider reset', stops, svgCount: svgs.length};
        }

        const first = svgs[0];
        const second = svgs[1];
        const rect = first.getBoundingClientRect();
        const y = rect.top + (rect.height / 2);
        const startX = rect.left + (rect.width * 0.25);
        const endX = rect.left + (rect.width * 0.65);
        const eventInit = clientX => ({
          bubbles: true,
          cancelable: true,
          pointerId: 1,
          pointerType: 'mouse',
          button: 0,
          buttons: 1,
          clientX,
          clientY: y,
        });
        first.dispatchEvent(new PointerEvent('pointermove', eventInit(startX)));
        const hoverFirst = first.querySelector('[data-js-debug-hover-line]');
        const hoverSecond = second.querySelector('[data-js-debug-hover-line]');
        const hoverFirstX = hoverFirst.getAttribute('x1');
        const hoverSecondX = hoverSecond.getAttribute('x1');
        const hoverOpacity = getComputedStyle(hoverFirst).opacity;

        first.dispatchEvent(new PointerEvent('pointerdown', eventInit(startX)));
        first.dispatchEvent(new PointerEvent('pointermove', eventInit(endX)));
        const selection = first.querySelector('[data-js-debug-selection-rect]');
        const selecting = graph.classList.contains('js-debug-graph--selecting');
        const selectionOpacity = getComputedStyle(selection).opacity;
        const selectionWidth = Number(selection.getAttribute('width'));
        first.dispatchEvent(new PointerEvent('pointerup', eventInit(endX)));

        const zoomGrid = document.querySelector('[data-js-debug-chart-grid]');
        const reset = document.querySelector('[data-js-debug-zoom-reset]');
        const zoomed = zoomGrid?.dataset.jsDebugZoomed === 'true';
        const zoomStart = Number(zoomGrid?.dataset.jsDebugDomainStart);
        const zoomEnd = Number(zoomGrid?.dataset.jsDebugDomainEnd);
        const resetControl = reset?.closest('[data-js-debug-range-control]');
        const label = resetControl?.querySelector('[data-js-debug-range-label]');
        const sliderAfterZoom = resetControl?.querySelector('[data-js-debug-range-slider]');
        const resetRect = reset?.getBoundingClientRect();
        const resetControlRect = resetControl?.getBoundingClientRect();
        const labelRect = label?.getBoundingClientRect();
        const sliderAfterZoomRect = sliderAfterZoom?.getBoundingClientRect();
        const resetRightGap = resetRect && resetControlRect ? resetControlRect.right - resetRect.right : NaN;
        const sliderLeftSpacer = labelRect && resetControlRect ? labelRect.left - resetControlRect.left : NaN;
        reset?.dispatchEvent(new PointerEvent('pointerdown', eventInit(endX)));
        const afterResetGrid = document.querySelector('[data-js-debug-chart-grid]');

        return {
          stops,
          sliderMin: slider.min,
          sliderMax: slider.max,
          sliderStep: slider.step,
          sliderValue: slider.value,
          sliderPointerDefaultAllowed,
          sliderSurvivedPointerDown,
          sliderSurvivedInputDrag,
          sliderSurvivedPassiveRefreshDuringDrag,
          sliderValueDuringInput,
          sliderValueAfterSnap,
          sliderInputSeconds,
          hoverFirstX,
          hoverSecondX,
          hoverOpacity,
          selecting,
          selectionOpacity,
          selectionWidth,
          zoomed,
          zoomSeconds: (zoomEnd - zoomStart) / 1000,
          resetText: reset?.textContent || '',
          resetRightGap,
          sliderLeftSpacer,
          resetZoomed: afterResetGrid?.dataset.jsDebugZoomed === 'true',
        };
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["stops"] == [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 57600, 86400], metrics
    assert metrics["sliderMin"] == "0"
    assert metrics["sliderMax"] == "9"
    assert metrics["sliderStep"] == "any", metrics
    assert metrics["sliderValue"] == "1"
    assert metrics["sliderPointerDefaultAllowed"] is True, metrics
    assert metrics["sliderSurvivedPointerDown"] is True, metrics
    assert metrics["sliderSurvivedInputDrag"] is True, metrics
    assert metrics["sliderSurvivedPassiveRefreshDuringDrag"] is True, metrics
    assert metrics["sliderValueDuringInput"] == "7.4", metrics
    assert metrics["sliderValueAfterSnap"] == "7", metrics
    assert 28790 <= metrics["sliderInputSeconds"] <= 28810, metrics
    assert metrics["hoverFirstX"] == metrics["hoverSecondX"] == "150.0", metrics
    assert float(metrics["hoverOpacity"]) > 0.0, metrics
    assert metrics["selecting"] is True, metrics
    assert float(metrics["selectionOpacity"]) > 0.0, metrics
    assert 235 <= metrics["selectionWidth"] <= 245, metrics
    assert metrics["zoomed"] is True, metrics
    assert 118 <= metrics["zoomSeconds"] <= 122, metrics
    assert metrics["resetText"] == "Reset", metrics
    assert 0 <= metrics["resetRightGap"] <= 1.5, metrics
    assert metrics["sliderLeftSpacer"] >= 40, metrics
    assert metrics["resetZoomed"] is False, metrics

    slider = browser.find_element("css selector", "[data-js-debug-range-slider]")
    slider_rect = slider.rect
    ActionChains(browser).move_to_element(slider).click_and_hold().move_by_offset(
        max(60, int(slider_rect["width"] * 0.35)),
        0,
    ).release().perform()
    drag_metrics = browser.execute_script(
        """
        const slider = document.querySelector('[data-js-debug-range-slider]');
        const grid = document.querySelector('[data-js-debug-chart-grid]');
        return {
          value: Number(slider?.value),
          rangeSeconds: (Number(grid?.dataset.jsDebugDomainEnd) - Number(grid?.dataset.jsDebugDomainStart)) / 1000,
          sliderExists: Boolean(slider),
        };
        """
    )
    assert drag_metrics["sliderExists"] is True, drag_metrics
    assert drag_metrics["value"] > 1, drag_metrics
    assert drag_metrics["value"] == round(drag_metrics["value"]), drag_metrics
    assert drag_metrics["rangeSeconds"] > 300, drag_metrics


def _status_ball_tone_score(image, dpr, rest_rect, peak_rect, tone):
    padding = 24
    left = int((min(rest_rect["left"], peak_rect["left"]) - padding) * dpr)
    top = int((min(rest_rect["top"], peak_rect["top"]) - padding) * dpr)
    right = int((max(rest_rect["right"], peak_rect["right"]) + padding) * dpr)
    bottom = int((max(rest_rect["bottom"], peak_rect["bottom"]) + padding) * dpr)
    left = max(0, min(image.width - 1, left))
    right = max(left + 1, min(image.width, right))
    top = max(0, min(image.height - 1, top))
    bottom = max(top + 1, min(image.height, bottom))

    def pixel_weight(pixel):
        r, g, b = pixel[:3]
        if tone == "green":
            return max(0, g - max(r, b)) * (g / 255)
        if tone == "red":
            return max(0, r - max(g, b)) * (r / 255)
        if tone == "yellow":
            return max(0, min(r, g) - b) * ((r + g) / 510)
        raise AssertionError(f"unknown tone {tone}")

    count = 0
    energy = 0.0
    for y in range(top, bottom):
        for x in range(left, right):
            weight = pixel_weight(image.getpixel((x, y)))
            if weight > 0:
                count += 1
                energy += weight
    return {"count": count, "energy": round(energy, 2), "bounds": (left, top, right, bottom)}


def test_working_agent_glyphs_show_static_symbol_and_glowing_ball_in_tabs_windows_and_tabber(browser, tmp_path):
    page = tmp_path / "working-agent-visible-pulse.html"
    page.write_text(page_html(f"""
      <section class="agent-pulse-fixture">
        <button id="dock-tab" class="pane-tab active">
          <span class="pane-tab-core">
            <span class="session-yolo-marker">YO</span>
            <span class="session-agent-activity-marker">{_working_agent_glyph_html("claude", "dock-claude")}</span>
            <span class="session-button-prefix">8002b fix: visible Claude</span>
          </span>
        </button>
        <button id="window-button" class="tab tmux-window-button active">
          <span class="tmux-window-name-label">
            {_working_agent_glyph_html("claude", "window-claude")}
            <span class="tmux-window-name-text">0:claude</span>
          </span>
        </button>
        <div id="tabber-claude-row" class="file-tree-row tabber-row selected" data-tabber-type="window" style="--file-explorer-font-size: 18px;">
          <span class="file-tree-name">
            <span class="tabber-window-label">
              {_working_agent_glyph_html("claude", "tabber-claude")}
              <span class="tabber-window-text">0:claude</span>
            </span>
          </span>
        </div>
        <div id="tabber-codex-row" class="file-tree-row tabber-row" data-tabber-type="window" style="--file-explorer-font-size: 18px;">
          <span class="file-tree-name">
            <span class="tabber-window-label">
              {_working_agent_glyph_html("codex", "tabber-codex")}
              <span class="tabber-window-text">1:codex</span>
            </span>
          </span>
        </div>
      </section>
    """, extra_css="""
      body {
        margin: 0;
        padding: 28px;
        background: #17270e;
        color: #e8eef8;
        font: 18px sans-serif;
      }
      .agent-pulse-fixture {
        display: grid;
        justify-items: start;
        gap: 20px;
      }
      #dock-tab {
        width: 360px;
        height: 30px;
      }
      .tmux-window-button {
        width: max-content;
      }
      .file-tree-row.tabber-row {
        width: 520px;
        padding: 4px 8px;
        background: #2c3340;
      }
    """), encoding="utf-8")
    browser.get(page.as_uri())
    reduced = browser.execute_script("return matchMedia('(prefers-reduced-motion: reduce)').matches")
    targets = {
        "dock-tab Claude": "#dock-claude",
        "window-bar Claude": "#window-claude",
        "Tabber Claude": "#tabber-claude",
        "Tabber Codex": "#tabber-codex",
    }
    results = {}
    for label, selector in targets.items():
        info = browser.execute_script(
            """
            const sym = document.querySelector(arguments[0]);
            const dot = document.querySelector(arguments[0] + '-dot');
            return {
              symAnim: getComputedStyle(sym).animationName,
              symOpacity: getComputedStyle(sym).opacity,
              dotPresent: !!dot,
              dotWorkingTone: dot ? dot.classList.contains('status-indicator--working') : false,
              dotAnim: dot ? getComputedStyle(dot).animationName : null,
            };
            """,
            selector,
        )
        results[label] = info
        # On every surface the agent symbol is STATIC (no pulse) ...
        assert info["symAnim"] == "none", results
        assert float(info["symOpacity"]) == 1, results
        # ... and a separate green ball sits beside it and glows (green via attention-ring-fade).
        assert info["dotPresent"] is True, results
        assert info["dotWorkingTone"] is True, results
        if not reduced:
            assert "attention-ring-fade" in info["dotAnim"], results
            assert "working-ball-hard-flash" in info["dotAnim"], results


def test_working_status_ball_has_visible_green_glow_pixels(browser, tmp_path):
    page = tmp_path / "working-agent-glow-pixels.html"
    page.write_text(page_html(f"""
      <section class="glow-pixel-fixture">
        <div id="tabber-glow-row" class="file-tree-row tabber-row" data-tabber-type="window" style="--file-explorer-font-size: 18px;">
          <span class="file-tree-name">
            <span class="tabber-window-label">
              {_working_agent_glyph_html("codex", "tabber-glow")}
              <span class="tabber-window-text">0:codex</span>
            </span>
          </span>
        </div>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 80px; background: #101820; color: #e8eef8; font: 18px sans-serif; }
      .glow-pixel-fixture { display: grid; justify-items: start; gap: 24px; }
      #tabber-glow-row { width: 320px; padding: 14px 18px; background: #101820; overflow: visible; }
      #tabber-glow-row .file-tree-name,
      #tabber-glow-row .tabber-window-label,
      #tabber-glow-row .agent-window-activity { overflow: visible; }
    """), encoding="utf-8")
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const dot = document.getElementById('tabber-glow-dot');
        for (const animation of dot.getAnimations()) {
          const timing = animation.effect?.getTiming?.() || {};
          const duration = Number(timing.duration) || 0;
          if (duration > 0) {
            animation.pause();
            animation.currentTime = duration * 0.5;
          }
        }
        const rect = dot.getBoundingClientRect();
        const style = getComputedStyle(dot);
        return {
          rect: {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height},
          animationName: style.animationName,
          boxShadow: style.boxShadow,
          filter: style.filter,
          background: style.backgroundColor,
          color: style.color,
          reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
        };
        """
    )
    assert "attention-ring-fade" in metrics["animationName"] or metrics["reducedMotion"] is True, metrics
    assert "working-ball-hard-flash" in metrics["animationName"] or metrics["reducedMotion"] is True, metrics
    assert metrics["boxShadow"] != "none", metrics
    screenshot = browser_screenshot_rgb(browser)
    dpr = browser.execute_script("return window.devicePixelRatio || 1") or 1
    rect = metrics["rect"]
    left = max(0, min(screenshot.width - 1, int((rect["right"] + 1) * dpr)))
    right = max(left + 1, min(screenshot.width, int((rect["right"] + 18) * dpr)))
    top = max(0, min(screenshot.height - 1, int((rect["top"] - 8) * dpr)))
    bottom = max(top + 1, min(screenshot.height, int((rect["bottom"] + 8) * dpr)))
    samples = []
    green_pixels = 0
    for y in range(top, bottom):
        for x in range(left, right):
            pixel = screenshot.getpixel((x, y))
            if len(samples) < 20:
                samples.append(pixel)
            if pixel[1] >= 42 and pixel[1] - pixel[0] >= 10 and pixel[1] - pixel[2] >= 4:
                green_pixels += 1
    assert green_pixels >= 6, {"greenPixels": green_pixels, "samples": samples, "rect": rect, "metrics": metrics}


def test_agent_status_glyphs_split_on_tabs_tabber_and_info_buttons(browser, tmp_path):
    page = tmp_path / "agent-status-split-surfaces.html"
    page.write_text(page_html(f"""
      <section class="agent-status-split-fixture">
        <button id="dock-tab" class="pane-tab active">
          <span class="pane-tab-core">
            <span class="session-yolo-marker">YO</span>
            <span class="session-agent-activity-marker">{_agent_status_glyph_html("claude", "attention", "dock-attention")}</span>
            <span class="session-button-prefix">8002b ASK?</span>
          </span>
        </button>
        <div id="tabber-session-row" class="file-tree-row tabber-row selected" data-tabber-type="session" style="--file-explorer-font-size: 18px;">
          <span class="file-tree-name">
            <span class="tabber-session-tab active" data-tabber-session-chrome="shared">
              <span class="pane-tab-core">
                <span class="session-yolo-marker">YO</span>
                <span class="session-agent-activity-marker">{_agent_status_glyph_html("codex", "working", "tabber-session-working")}</span>
                <span class="session-button-prefix">8001</span>
              </span>
            </span>
          </span>
        </div>
        <div id="tabber-window-row" class="file-tree-row tabber-row" data-tabber-type="window" style="--file-explorer-font-size: 18px;">
          <span class="file-tree-name">
            <span class="tabber-window-label">
              {_agent_status_glyph_html("claude", "cooldown", "tabber-window-cooldown")}
              <span class="tabber-window-text">0:claude</span>
            </span>
          </span>
        </div>
        <div id="info-pane" class="pane-info-bar">
          <button id="info-button" class="tab tmux-window-button active">
            <span class="tmux-window-name-label">
              {_agent_status_glyph_html("codex", "attention", "info-attention")}
              <span class="tmux-window-name-text">1:codex</span>
            </span>
          </button>
        </div>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 28px; background: #17270e; color: #e8eef8; font: 18px sans-serif; }
      .agent-status-split-fixture { display: grid; justify-items: start; gap: 20px; }
      #dock-tab { width: 360px; height: 30px; }
      .file-tree-row.tabber-row { width: 520px; padding: 4px 8px; background: #2c3340; }
      .pane-info-bar { display: flex; width: 520px; padding: 4px; background: #202633; }
      .tmux-window-button { width: max-content; }
    """), encoding="utf-8")
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const read = id => {
          const icon = document.getElementById(id);
          const dot = document.getElementById(id + '-dot');
          const dotLiveStyle = getComputedStyle(dot);
          const dotAnimationName = dotLiveStyle.animationName;
          const dotAnimationPlayState = dotLiveStyle.animationPlayState;
          const dotAnimationIterationCount = dotLiveStyle.animationIterationCount;
          for (const animation of dot.getAnimations()) {
            const timing = animation.effect?.getTiming?.() || {};
            const duration = Number(timing.duration) || 0;
            if (duration > 0) {
              animation.pause();
              animation.currentTime = duration * 0.5;
            }
          }
          const wrap = icon?.closest('.agent-window-activity');
          const iconRect = icon.getBoundingClientRect();
          const dotRect = dot.getBoundingClientRect();
          const wrapStyle = getComputedStyle(wrap);
          const iconStyle = getComputedStyle(icon);
          const dotStyle = getComputedStyle(dot);
          return {
            wrapDisplay: wrapStyle.display,
            agentStatusBallSize: wrapStyle.getPropertyValue('--agent-status-ball-size').trim(),
            dotFontSize: dotStyle.fontSize,
            iconAnimation: iconStyle.animationName,
            iconOpacity: iconStyle.opacity,
            dotAnimation: dotAnimationName,
            dotPlayState: dotAnimationPlayState,
            dotIterationCount: dotAnimationIterationCount,
            dotTransform: dotStyle.transform,
            dotColor: dotStyle.color,
            dotBackground: dotStyle.backgroundColor,
            dotBoxShadow: dotStyle.boxShadow,
            dotWidth: dotRect.width,
            dotHeight: dotRect.height,
            dotToneWorking: dot.classList.contains('status-indicator--working'),
            dotToneAttention: dot.classList.contains('status-indicator--attention'),
            dotToneCooldown: dot.classList.contains('status-indicator--cooldown'),
            greenGlowAlpha: dotStyle.getPropertyValue('--attention-ring-peak-glow-alpha').trim(),
            greenGlowSize: dotStyle.getPropertyValue('--attention-ring-peak-glow-size').trim(),
            iconLeft: iconRect.left,
            iconRight: iconRect.right,
            dotLeft: dotRect.left,
            dotRight: dotRect.right,
            centerDy: Math.abs((iconRect.top + iconRect.height / 2) - (dotRect.top + dotRect.height / 2)),
          };
        };
        return {
          dockAttention: read('dock-attention'),
          tabberSessionWorking: read('tabber-session-working'),
          tabberWindowCooldown: read('tabber-window-cooldown'),
          infoAttention: read('info-attention'),
          reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
        };
        """
    )
    for name in ("dockAttention", "tabberSessionWorking", "tabberWindowCooldown", "infoAttention"):
        item = metrics[name]
        assert item["wrapDisplay"] == "flex", (name, metrics)
        assert item["iconAnimation"] == "none", (name, metrics)
        assert float(item["iconOpacity"]) == 1, (name, metrics)
        assert item["dotLeft"] >= item["iconRight"] - 0.5, (name, metrics)
        assert item["centerDy"] <= 1, (name, metrics)
        if not metrics["reducedMotion"]:
            assert "attention-ring-fade" in item["dotAnimation"], (name, metrics)
            assert item["dotPlayState"] == "running", (name, metrics)
            assert item["dotIterationCount"] == "infinite", (name, metrics)
            assert item["dotBoxShadow"] != "none", (name, metrics)
        assert item["dotWidth"] > 0 and item["dotHeight"] > 0, (name, metrics)
    assert metrics["dockAttention"]["dotToneAttention"] is True, metrics
    assert metrics["infoAttention"]["dotToneAttention"] is True, metrics
    assert metrics["tabberSessionWorking"]["dotToneWorking"] is True, metrics
    assert metrics["tabberWindowCooldown"]["dotToneCooldown"] is True, metrics
    agent_ball_sizes = {metrics[name]["agentStatusBallSize"] for name in ("dockAttention", "tabberSessionWorking", "tabberWindowCooldown", "infoAttention")}
    dot_font_sizes = {metrics[name]["dotFontSize"] for name in ("dockAttention", "tabberSessionWorking", "tabberWindowCooldown", "infoAttention")}
    peak_widths = [metrics[name]["dotWidth"] for name in ("dockAttention", "tabberSessionWorking", "tabberWindowCooldown", "infoAttention")]
    peak_heights = [metrics[name]["dotHeight"] for name in ("dockAttention", "tabberSessionWorking", "tabberWindowCooldown", "infoAttention")]
    transforms = {metrics[name]["dotTransform"] for name in ("dockAttention", "tabberSessionWorking", "tabberWindowCooldown", "infoAttention")}
    assert agent_ball_sizes == {"14px"}, metrics
    assert dot_font_sizes == {"14px"}, metrics
    assert max(peak_widths) - min(peak_widths) <= 0.5, metrics
    assert max(peak_heights) - min(peak_heights) <= 0.5, metrics
    assert len(transforms) == 1, metrics


def test_tabber_parent_child_status_balls_share_parent_size_and_phase(browser, tmp_path):
    page = tmp_path / "tabber-parent-child-status-ball-parity.html"
    page.write_text(page_html(f"""
      <section class="tabber-ball-parity-fixture">
        <div id="tabber-session-row" class="file-tree-row tabber-row selected" data-tabber-type="session" style="--file-explorer-font-size: 16px;">
          <span class="file-tree-name">
            <span class="tabber-session-tab active" data-tabber-session-chrome="shared">
              <span class="pane-tab-core">
                <span class="session-yolo-marker">YO</span>
                <span class="session-agent-activity-marker">{_working_agent_glyph_html("codex", "tabber-session-working")}</span>
                <span class="session-button-prefix">8001</span>
              </span>
            </span>
          </span>
        </div>
        <div id="tabber-window-row" class="file-tree-row tabber-row" data-tabber-type="window" style="--file-explorer-font-size: 22px;">
          <span class="file-tree-name">
            <span class="tabber-window-label">
              {_working_agent_glyph_html("codex", "tabber-window-working")}
              <span class="tabber-window-text">0:codex</span>
            </span>
          </span>
        </div>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 32px; background: #202633; color: #e8eef8; font: 18px sans-serif; }
      .tabber-ball-parity-fixture { display: grid; justify-items: start; gap: 16px; }
      .file-tree-row.tabber-row { width: 620px; padding: 5px 8px; background: #2c3340; overflow: visible; }
      .file-tree-name,
      .tabber-session-tab,
      .tabber-window-label,
      .agent-window-activity { overflow: visible; }
    """), encoding="utf-8")
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const read = id => {
          const icon = document.getElementById(id);
          const dot = document.getElementById(id + '-dot');
          const wrap = dot.closest('.agent-window-activity');
          for (const animation of dot.getAnimations()) {
            const timing = animation.effect?.getTiming?.() || {};
            const duration = Number(timing.duration) || 0;
            if (duration > 0) {
              animation.pause();
              animation.currentTime = duration * 0.5;
            }
          }
          void dot.offsetWidth;
          const iconStyle = getComputedStyle(icon);
          const wrapStyle = getComputedStyle(wrap);
          const dotStyle = getComputedStyle(dot);
          const dotRect = dot.getBoundingClientRect();
          return {
            iconSize: iconStyle.width,
            agentWindowIconSize: wrapStyle.getPropertyValue('--agent-window-icon-size').trim(),
            agentStatusBallSize: wrapStyle.getPropertyValue('--agent-status-ball-size').trim(),
            dotFontSize: dotStyle.fontSize,
            dotFontStretch: dotStyle.fontStretch,
            animationName: dotStyle.animationName,
            animationDuration: dotStyle.animationDuration,
            animationDelay: dotStyle.animationDelay,
            animationTimingFunction: dotStyle.animationTimingFunction,
            transform: dotStyle.transform,
            width: dotRect.width,
            height: dotRect.height,
          };
        };
        return {
          parent: read('tabber-session-working'),
          child: read('tabber-window-working'),
          reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
        };
        """
    )
    assert metrics["parent"]["iconSize"] != metrics["child"]["iconSize"], metrics
    for side in ("parent", "child"):
        assert metrics[side]["agentStatusBallSize"] == "14px", metrics
        assert metrics[side]["dotFontSize"] == "14px", metrics
        assert metrics[side]["dotFontStretch"] in {"normal", "100%"}, metrics
        assert "attention-ring-fade" in metrics[side]["animationName"], metrics
        assert "working-ball-hard-flash" in metrics[side]["animationName"], metrics
    assert metrics["parent"]["animationDuration"] == metrics["child"]["animationDuration"], metrics
    assert metrics["parent"]["animationDelay"] == metrics["child"]["animationDelay"], metrics
    assert metrics["parent"]["animationTimingFunction"] == metrics["child"]["animationTimingFunction"], metrics
    assert metrics["parent"]["transform"] == metrics["child"]["transform"], metrics
    assert abs(metrics["parent"]["width"] - metrics["child"]["width"]) <= 0.5, metrics
    assert abs(metrics["parent"]["height"] - metrics["child"]["height"]) <= 0.5, metrics


def test_status_balls_share_ask_badge_pulse_cadence_and_actually_pulsate(browser, tmp_path):
    page = tmp_path / "attention-dot-pulse.html"
    page.write_text(page_html("""
      <span id="working-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-activity-icon--working status-indicator--working heartbeat-pulse" style="--attention-animation-delay:-0.42s">●</span>
      <span id="window-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-activity-icon--attention status-indicator--attention heartbeat-pulse attention-pulse" style="--attention-animation-delay:-0.42s">●</span>
      <span id="popover-dot" class="status-indicator session-agent-dot status-indicator--dot status-indicator--attention heartbeat-pulse attention-pulse" style="--attention-animation-delay:-0.42s">●</span>
      <span id="tabber-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-activity-icon--attention status-indicator--attention heartbeat-pulse attention-pulse" style="--attention-animation-delay:-0.42s">●</span>
      <span id="cooldown-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-activity-icon--cooldown status-indicator--cooldown heartbeat-pulse attention-pulse" style="--attention-animation-delay:-0.42s">●</span>
      <span id="ask-badge" class="status-indicator tabber-agent-status status-indicator--label agent-status-attention status-indicator--attention heartbeat-pulse attention-pulse" style="--attention-animation-delay:-0.42s">ASK?</span>
    """, extra_css="""
      :root { --pulse-duration: 1.8s; --pulse-easing: ease-in-out; --bad: #ff3347; --danger-text: #ff3347; --text: #dbe2ef; --muted: #8590a6; }
      body { display: grid; justify-items: start; gap: 34px; background: #111; color: #ddd; font: 16px sans-serif; padding: 32px; }
    """), encoding="utf-8")
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const ids = ['working-dot', 'window-dot', 'popover-dot', 'tabber-dot', 'cooldown-dot', 'ask-badge'];
        const firstAnimationValue = value => String(value || '').split(',').map(item => item.trim())[0] || '';
        const pauseAt = (node, fraction) => {
          const animations = node.getAnimations();
          for (const animation of animations) {
            const timing = animation.effect?.getTiming?.() || {};
            const duration = Number(timing.duration) || 0;
            const delay = Number(timing.delay) || 0;
            if (duration > 0) {
              animation.pause();
              animation.currentTime = delay + duration * fraction;
            }
          }
          void node.offsetWidth;
          const style = getComputedStyle(node);
          const rect = node.getBoundingClientRect();
          return {
            boxShadow: style.boxShadow,
            filter: style.filter,
            color: style.color,
            backgroundColor: style.backgroundColor,
            borderTopColor: style.borderTopColor,
            opacity: style.opacity,
            transform: style.transform,
            rect: {width: rect.width, height: rect.height},
          };
        };
        const read = id => {
          const node = document.getElementById(id);
          const style = getComputedStyle(node);
          const rest = pauseAt(node, 1);
          const peak = pauseAt(node, 0.5);
          return {
            animationName: style.animationName,
            primaryAnimationName: firstAnimationValue(style.animationName),
            animationPlayState: style.animationPlayState,
            primaryAnimationPlayState: firstAnimationValue(style.animationPlayState),
            animationIterationCount: style.animationIterationCount,
            primaryAnimationIterationCount: firstAnimationValue(style.animationIterationCount),
            animationDuration: style.animationDuration,
            primaryAnimationDuration: firstAnimationValue(style.animationDuration),
            animationDelay: style.animationDelay,
            primaryAnimationDelay: firstAnimationValue(style.animationDelay),
            animationTimingFunction: style.animationTimingFunction,
            primaryAnimationTimingFunction: firstAnimationValue(style.animationTimingFunction),
            borderTopStyle: style.borderTopStyle,
            borderTopWidth: style.borderTopWidth,
            delayVar: style.getPropertyValue('--attention-animation-delay').trim(),
            peakGlowSize: style.getPropertyValue('--attention-ring-peak-glow-size').trim(),
            rest,
            peak,
            reduced: matchMedia('(prefers-reduced-motion: reduce)').matches,
          };
        };
        return Object.fromEntries(ids.map(id => [id, read(id)]));
        """
    )
    if metrics["ask-badge"]["reduced"]:
        pytest.skip("browser prefers reduced motion")
    badge = metrics["ask-badge"]
    assert "attention-ring-fade" in badge["animationName"], badge
    assert badge["rest"]["boxShadow"] != badge["peak"]["boxShadow"], badge
    for dot_id in ("working-dot", "window-dot", "popover-dot", "tabber-dot", "cooldown-dot"):
        dot = metrics[dot_id]
        assert dot["primaryAnimationName"] == "attention-ring-fade", {dot_id: dot}
        assert dot["primaryAnimationPlayState"] == "running", {dot_id: dot}
        assert dot["primaryAnimationIterationCount"] == "infinite", {dot_id: dot}
        assert dot["primaryAnimationDuration"] == badge["primaryAnimationDuration"], {dot_id: dot, "badge": badge}
        assert dot["primaryAnimationDelay"] == badge["primaryAnimationDelay"], {dot_id: dot, "badge": badge}
        assert dot["primaryAnimationTimingFunction"] == badge["primaryAnimationTimingFunction"], {dot_id: dot, "badge": badge}
        assert dot["delayVar"] == badge["delayVar"] == "-0.42s", {dot_id: dot, "badge": badge}
        assert dot["borderTopStyle"] == "solid", {dot_id: dot}
        assert dot["borderTopWidth"] != "0px", {dot_id: dot}
        assert dot["rest"]["boxShadow"] != dot["peak"]["boxShadow"], {dot_id: dot}
        assert dot["rest"]["filter"] != dot["peak"]["filter"], {dot_id: dot}
        assert abs(dot["rest"]["rect"]["width"] - dot["peak"]["rect"]["width"]) <= 0.5, {dot_id: dot}
        assert abs(dot["rest"]["rect"]["height"] - dot["peak"]["rect"]["height"]) <= 0.5, {dot_id: dot}
    assert "working-ball-hard-flash" in metrics["working-dot"]["animationName"], metrics["working-dot"]
    assert metrics["working-dot"]["rest"]["color"] != metrics["working-dot"]["peak"]["color"], metrics["working-dot"]
    peak_rgb = [int(float(item)) for item in re.findall(r"\d+(?:\.\d+)?", metrics["working-dot"]["peak"]["color"])[:3]]
    assert peak_rgb[1] - peak_rgb[0] >= 90 and peak_rgb[1] - peak_rgb[2] >= 70, metrics["working-dot"]
    working_peak_glow = float(metrics["working-dot"]["peakGlowSize"].replace("px", ""))
    default_peak_glow = float(metrics["window-dot"]["peakGlowSize"].replace("px", ""))
    assert working_peak_glow < default_peak_glow, metrics
    for dot_id in ("window-dot", "popover-dot", "tabber-dot", "cooldown-dot"):
        assert abs(metrics["working-dot"]["rest"]["rect"]["width"] - metrics[dot_id]["rest"]["rect"]["width"]) <= 0.5, {dot_id: metrics[dot_id], "working": metrics["working-dot"]}
        assert abs(metrics["working-dot"]["rest"]["rect"]["height"] - metrics[dot_id]["rest"]["rect"]["height"]) <= 0.5, {dot_id: metrics[dot_id], "working": metrics["working-dot"]}
        assert abs(metrics["working-dot"]["peak"]["rect"]["width"] - metrics[dot_id]["peak"]["rect"]["width"]) <= 0.5, {dot_id: metrics[dot_id], "working": metrics["working-dot"]}
        assert abs(metrics["working-dot"]["peak"]["rect"]["height"] - metrics[dot_id]["peak"]["rect"]["height"]) <= 0.5, {dot_id: metrics[dot_id], "working": metrics["working-dot"]}

    visual_ids = {"working-dot": "green", "window-dot": "red", "cooldown-dot": "yellow"}
    all_animation_ids = ["working-dot", "window-dot", "popover-dot", "tabber-dot", "cooldown-dot", "ask-badge"]
    sample_rects = """
      const ids = arguments[0];
      const fraction = arguments[1];
      for (const id of ids) {
        const node = document.getElementById(id);
        for (const animation of node.getAnimations()) {
          const timing = animation.effect?.getTiming?.() || {};
          const duration = Number(timing.duration) || 0;
          const delay = Number(timing.delay) || 0;
          if (duration > 0) {
            animation.pause();
            animation.currentTime = delay + duration * fraction;
          }
        }
      }
      void document.body.offsetWidth;
      return Object.fromEntries(ids.map(id => {
        const rect = document.getElementById(id).getBoundingClientRect();
        return [id, {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height}];
      }));
    """
    dpr = browser.execute_script("return window.devicePixelRatio || 1") or 1
    browser.execute_script(sample_rects, all_animation_ids, 1)
    rest_rects = browser.execute_script(sample_rects, list(visual_ids), 1)
    rest_screenshot = browser_screenshot_rgb(browser)
    browser.execute_script(sample_rects, all_animation_ids, 0.5)
    peak_rects = browser.execute_script(sample_rects, list(visual_ids), 0.5)
    peak_screenshot = browser_screenshot_rgb(browser)
    visual_scores = {}
    for dot_id, tone in visual_ids.items():
        rest_score = _status_ball_tone_score(rest_screenshot, dpr, rest_rects[dot_id], peak_rects[dot_id], tone)
        peak_score = _status_ball_tone_score(peak_screenshot, dpr, rest_rects[dot_id], peak_rects[dot_id], tone)
        visual_scores[dot_id] = {"rest": rest_score, "peak": peak_score}
        energy_ratio = 1.08 if dot_id == "working-dot" else 1.25
        count_delta = 6 if dot_id == "working-dot" else 10
        assert peak_score["energy"] > rest_score["energy"] * energy_ratio, visual_scores
        assert peak_score["count"] > rest_score["count"] + count_delta, visual_scores
    working_energy_delta = visual_scores["working-dot"]["peak"]["energy"] - visual_scores["working-dot"]["rest"]["energy"]
    red_energy_delta = visual_scores["window-dot"]["peak"]["energy"] - visual_scores["window-dot"]["rest"]["energy"]
    yellow_energy_delta = visual_scores["cooldown-dot"]["peak"]["energy"] - visual_scores["cooldown-dot"]["rest"]["energy"]
    assert working_energy_delta < red_energy_delta, visual_scores
    assert working_energy_delta < yellow_energy_delta, visual_scores


def test_status_balls_keep_ask_pill_pulse_cadence_under_reduced_motion(browser, tmp_path):
    page = tmp_path / "attention-dot-reduced-motion.html"
    page.write_text(page_html("""
      <span id="working-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-activity-icon--working status-indicator--working heartbeat-pulse" style="--attention-animation-delay:-0.42s">●</span>
      <span id="attention-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-activity-icon--attention status-indicator--attention heartbeat-pulse attention-pulse" style="--attention-animation-delay:-0.42s">●</span>
      <span id="ask-pill" class="status-indicator session-state-badge status-indicator--text tab-symbol session-state-needs-input session-state-reminder status-indicator--attention heartbeat-pulse attention-pulse" style="--attention-animation-delay:-0.42s">ASK?</span>
    """, extra_css="""
      :root { --pulse-duration: 1.55s; --pulse-easing: ease-in-out; --bad: #ff3347; --danger-text: #ff3347; --text: #dbe2ef; --muted: #8590a6; }
      body { display: grid; justify-items: start; gap: 34px; background: #111; color: #ddd; font: 16px sans-serif; padding: 32px; }
    """), encoding="utf-8")
    browser.execute_cdp_cmd("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
    try:
        browser.get(page.as_uri())
        metrics = browser.execute_script(
            """
            const firstAnimationValue = value => String(value || '').split(',').map(item => item.trim())[0] || '';
            const read = id => {
              const node = document.getElementById(id);
              const style = getComputedStyle(node);
              const firstAnimation = node.getAnimations()[0];
              const timing = firstAnimation?.effect?.getTiming?.() || {};
              return {
                animationName: style.animationName,
                primaryAnimationName: firstAnimationValue(style.animationName),
                animationDuration: style.animationDuration,
                primaryAnimationDuration: firstAnimationValue(style.animationDuration),
                animationDelay: style.animationDelay,
                primaryAnimationDelay: firstAnimationValue(style.animationDelay),
                animationTimingFunction: style.animationTimingFunction,
                primaryAnimationTimingFunction: firstAnimationValue(style.animationTimingFunction),
                primaryEffectDuration: Number(timing.duration) || 0,
                primaryPlayState: firstAnimation?.playState || '',
              };
            };
            return {
              reduced: matchMedia('(prefers-reduced-motion: reduce)').matches,
              working: read('working-dot'),
              attention: read('attention-dot'),
              ask: read('ask-pill'),
            };
            """
        )
        assert metrics["reduced"] is True, metrics
        ask = metrics["ask"]
        assert ask["primaryAnimationName"] == "attention-ring-fade", metrics
        assert ask["primaryAnimationDuration"] == "1.55s", metrics
        assert ask["primaryAnimationDelay"] == "-0.42s", metrics
        assert ask["primaryEffectDuration"] > 0, metrics
        for key in ("working", "attention"):
            dot = metrics[key]
            assert dot["primaryAnimationName"] == "attention-ring-fade", metrics
            assert dot["primaryAnimationDuration"] == ask["primaryAnimationDuration"], metrics
            assert dot["primaryAnimationDelay"] == ask["primaryAnimationDelay"], metrics
            assert dot["primaryAnimationTimingFunction"] == ask["primaryAnimationTimingFunction"], metrics
            assert dot["primaryEffectDuration"] == ask["primaryEffectDuration"], metrics
            assert dot["primaryPlayState"] in {"pending", "running"}, metrics
        assert "working-ball-hard-flash" in metrics["working"]["animationName"], metrics
    finally:
        browser.execute_cdp_cmd("Emulation.setEmulatedMedia", {"features": []})


def test_agent_attention_and_cooldown_status_balls_sit_beside_static_ai_icon(browser, tmp_path):
    page = tmp_path / "agent-status-split.html"
    page.write_text(page_html("""
      <div id="base" class="agent-window-activity agent-window-activity--attention" style="--attention-animation-delay:-0.42s">
        <span id="base-icon" class="agent-icon claude agent-window-activity-icon agent-window-agent-icon agent-window-activity-icon--attention agent-window-agent-icon--attention">
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="5.5" fill="#cf7554"/></svg>
        </span>
        <span id="base-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-status-dot agent-window-activity-icon--attention status-indicator--attention heartbeat-pulse attention-pulse">●</span>
      </div>
      <button id="info-button" class="tab tmux-window-button">
        <span class="tmux-window-name-label">
          <span id="info" class="agent-window-activity agent-window-activity--attention" style="--attention-animation-delay:-0.37s">
            <span id="info-icon" class="agent-icon claude agent-window-activity-icon agent-window-agent-icon agent-window-activity-icon--attention agent-window-agent-icon--attention">
              <svg viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="5.5" fill="#cf7554"/></svg>
            </span>
            <span id="info-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-status-dot agent-window-activity-icon--attention status-indicator--attention heartbeat-pulse attention-pulse">●</span>
          </span>
          <span class="tmux-window-name-text">0:claude</span>
        </span>
      </button>
      <div class="file-tree-row tabber-row" style="--file-explorer-font-size: 14px;">
        <span class="tabber-window-label">
          <span id="tabber" class="agent-window-activity agent-window-activity--cooldown" style="--attention-animation-delay:-0.91s">
            <span id="tabber-icon" class="agent-icon codex agent-window-activity-icon agent-window-agent-icon agent-window-activity-icon--cooldown agent-window-agent-icon--cooldown">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#667ef8" d="M3 12a9 9 0 1 0 18 0A9 9 0 0 0 3 12z"/></svg>
            </span>
            <span id="tabber-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-status-dot agent-window-activity-icon--cooldown status-indicator--cooldown heartbeat-pulse attention-pulse">●</span>
          </span>
          <span class="tabber-window-text">1:codex</span>
        </span>
      </div>
    """, extra_css="""
      body { background: #111; color: #ddd; font: 16px sans-serif; padding: 24px; display: grid; gap: 16px; }
    """), encoding="utf-8")
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const rect = id => {
          const r = document.getElementById(id).getBoundingClientRect();
          return {left: r.left, top: r.top, width: r.width, height: r.height, cx: r.left + r.width / 2, cy: r.top + r.height / 2, right: r.right};
        };
        const read = (rootId, iconId, dotId) => {
          const root = document.getElementById(rootId);
          const rootStyle = getComputedStyle(root);
          const iconStyle = getComputedStyle(document.getElementById(iconId));
          const dotStyle = getComputedStyle(document.getElementById(dotId));
          const rootRect = rect(rootId);
          const iconRect = rect(iconId);
          const dotRect = rect(dotId);
          return {
            rootDisplay: rootStyle.display,
            rootWidth: rootRect.width,
            iconAnimation: iconStyle.animationName,
            dotAnimation: dotStyle.animationName,
            dotDelay: dotStyle.animationDelay,
            rootDelayVar: rootStyle.getPropertyValue('--attention-animation-delay').trim(),
            dotDelayVar: dotStyle.getPropertyValue('--attention-animation-delay').trim(),
            leftGap: dotRect.left - iconRect.right,
            centerDy: Math.abs(iconRect.cy - dotRect.cy),
            dotWithinRoot: dotRect.left >= rootRect.left - 1 && dotRect.left + dotRect.width <= rootRect.left + rootRect.width + 1,
          };
        };
        return {
          base: read('base', 'base-icon', 'base-dot'),
          info: read('info', 'info-icon', 'info-dot'),
          tabber: read('tabber', 'tabber-icon', 'tabber-dot'),
        };
        """
    )
    for name, item in metrics.items():
        assert item["rootDisplay"] == "flex", (name, item)
        assert item["iconAnimation"] == "none", (name, item)
        assert "attention-ring-fade" in item["dotAnimation"], (name, item)
        assert item["leftGap"] >= -0.5, (name, item)
        assert item["centerDy"] <= 1, (name, item)
        assert item["dotWithinRoot"] is True, (name, item)
        assert item["dotDelay"] == item["rootDelayVar"], (name, item)
        assert item["dotDelayVar"] == item["rootDelayVar"], (name, item)
    assert metrics["info"]["rootWidth"] >= 24
    assert metrics["tabber"]["rootWidth"] >= 28


def test_pane_info_bar_scrolls_metadata_without_shrinking_window_buttons(browser, tmp_path):
    page = tmp_path / "pane-info-bar-scroll.html"
    long_text = "#76 DRAFT · keivenchang/DIS-2239__parity-commit-link-frontend-crates · ~/dynamo/frontend-crates3 · 5 dirty · DIS-2239 In Review · fix(performance): repair v1 PARITY commit + case-doc links after"
    body = """
      <article class="panel active-pane" style="width: 520px;">
        <div id="info-bar" class="pane-info-bar panel-detail-row">
          <div class="pane-info-bar-popover-zone panel-popover-zone">
            <div class="panel-session-label"><span class="session-button-dir">8001</span></div>
            <div id="meta" class="pane-info-bar-meta meta pane-info-bar-meta-overflow" style="--pane-info-bar-scroll-distance: 240px; --pane-info-bar-scroll-offset: -240px; --pane-info-bar-scroll-duration: 23s; --pane-info-bar-scroll-timing: linear(0 0%, 0 13.04%, 1 91.30%, 1 100%);">
              <span id="controls" class="pane-info-bar-controls"><span class="meta-repo-switch"><button type="button" class="btn-base meta-repo-cycle">&lt;</button><button type="button" class="btn-base meta-repo-chip">2/3</button><button type="button" class="btn-base meta-repo-cycle">&gt;</button></span></span>
              <span class="meta-sep pane-info-bar-fixed-sep"> · </span>
              <span id="viewport" class="pane-info-bar-scroll-viewport"><span id="scroll-text" class="pane-info-bar-scroll-text"><span class="meta-branch">__LONG_TEXT__</span></span></span>
            </div>
          </div>
          <div id="window-bar" class="tmux-window-bar" data-tmux-window-bar-context="info-bar" data-tmux-window-label-mode="names">
            <button type="button" class="tab tmux-window-button"><span class="tmux-window-name-label"><span class="tmux-window-name-text">0:codex</span></span><span class="tmux-window-number-label">0</span></button>
            <button type="button" class="tab tmux-window-button active"><span class="tmux-window-name-label"><span class="tmux-window-name-text">1:claude</span></span><span class="tmux-window-number-label">1</span></button>
            <button type="button" class="tab tmux-window-button"><span class="tmux-window-name-label"><span class="tmux-window-name-text">2:bash</span></span><span class="tmux-window-number-label">2</span></button>
          </div>
          <button type="button" class="panel-detail-close"></button>
        </div>
      </article>
    """.replace("__LONG_TEXT__", long_text)
    page.write_text(page_html(body, extra_css="""
      body { margin: 0; padding: 24px; background: var(--bg); color: var(--text); }
      .panel { height: auto; }
    """), encoding="utf-8")
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const rect = id => {
          const r = document.getElementById(id).getBoundingClientRect();
          return {left: r.left, right: r.right, width: r.width};
        };
        const textStyle = getComputedStyle(document.getElementById('scroll-text'));
        const metaStyle = getComputedStyle(document.getElementById('meta'));
        const movedText = document.getElementById('scroll-text').cloneNode(true);
        movedText.id = 'scroll-text-moved';
        movedText.style.animationDelay = '-4s';
        document.getElementById('viewport').appendChild(movedText);
        const movedTransform = getComputedStyle(movedText).transform;
        const movedX = movedTransform && movedTransform !== 'none' ? new DOMMatrixReadOnly(movedTransform).m41 : 0;
        const firstButton = document.querySelector('.tmux-window-button');
        const buttonStyle = getComputedStyle(firstButton);
        const borderDistance = (left, right) => {
          const nums = value => (String(value).match(/[0-9.]+/g) || []).slice(0, 3).map(Number);
          const a = nums(left);
          const b = nums(right);
          if (a.length < 3 || b.length < 3) return 0;
          return Math.sqrt(((a[0] - b[0]) ** 2) + ((a[1] - b[1]) ** 2) + ((a[2] - b[2]) ** 2));
        };
        return {
          controls: rect('controls'),
          viewport: rect('viewport'),
          bar: rect('window-bar'),
          metaText: document.getElementById('scroll-text').textContent,
          controlsInsideViewport: Boolean(document.getElementById('viewport').querySelector('.meta-repo-switch')),
          scrollAnimation: textStyle.animationName,
          scrollDelay: textStyle.animationDelay,
          scrollDirection: textStyle.animationDirection,
          scrollDuration: textStyle.animationDuration,
          scrollOffset: metaStyle.getPropertyValue('--pane-info-bar-scroll-offset').trim(),
          scrollTiming: metaStyle.getPropertyValue('--pane-info-bar-scroll-timing').trim(),
          movedX,
          reduced: matchMedia('(prefers-reduced-motion: reduce)').matches,
          buttonFlexShrink: buttonStyle.flexShrink,
          buttonMaxWidth: buttonStyle.maxWidth,
          buttonOverflow: buttonStyle.overflow,
          buttonWidth: firstButton.getBoundingClientRect().width,
          buttonBorderStyle: buttonStyle.borderTopStyle,
          buttonBorderWidth: buttonStyle.borderTopWidth,
          buttonBorderBackgroundDistance: borderDistance(buttonStyle.borderTopColor, buttonStyle.backgroundColor),
          labelMode: document.getElementById('window-bar').dataset.tmuxWindowLabelMode,
          visibleNameDisplays: Array.from(document.querySelectorAll('.tmux-window-name-label')).map(node => getComputedStyle(node).display),
          visibleNumberDisplays: Array.from(document.querySelectorAll('.tmux-window-number-label')).map(node => getComputedStyle(node).display),
        };
        """
    )
    assert "keivenchang/DIS-2239__parity-commit-link-frontend-crates" in metrics["metaText"]
    assert "DIS-2239 In Review" in metrics["metaText"]
    assert "fix(performance): repair v1 PARITY commit + case-doc links after" in metrics["metaText"]
    assert metrics["controlsInsideViewport"] is False
    assert metrics["controls"]["right"] <= metrics["viewport"]["left"] + 2
    if not metrics["reduced"]:
        assert metrics["scrollAnimation"] == "pane-info-bar-scroll"
        assert metrics["scrollDelay"] == "0s"
        assert metrics["scrollDirection"] == "normal"
        assert metrics["scrollDuration"] == "23s"
        assert metrics["scrollOffset"] == "-240px"
        assert metrics["scrollTiming"] == "linear(0 0%, 0 13.04%, 1 91.30%, 1 100%)"
        assert metrics["movedX"] < -1
    assert metrics["buttonFlexShrink"] == "0"
    assert metrics["buttonMaxWidth"] == "none"
    assert metrics["buttonOverflow"] == "visible"
    assert metrics["buttonWidth"] > 40
    assert metrics["buttonBorderStyle"] == "solid"
    assert metrics["buttonBorderWidth"] == "1px"
    assert metrics["buttonBorderBackgroundDistance"] > 32
    assert metrics["labelMode"] == "names"
    assert set(metrics["visibleNameDisplays"]).issubset({"flex", "inline-flex"})
    assert "none" not in set(metrics["visibleNameDisplays"])
    assert set(metrics["visibleNumberDisplays"]) == {"none"}


@pytest.mark.parametrize(
    "agent,user_input",
    [
        ("codex", "touch /tmp/yolomux-mock-approval"),
        ("claude", "sleep 10"),
    ],
)
def test_mock_agent_prompt_payload_renders_ask_attention_in_live_browser(browser, monkeypatch, tmp_path, agent, user_input):
    tmux_binary = shutil.which("tmux")
    if not tmux_binary:
        pytest.skip("tmux is not installed")

    paths = isolate_browser_runtime_paths(monkeypatch, tmp_path)
    sock_base = Path("/tmp") / f"yoask-ui-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    sock_base.mkdir(mode=0o700)
    socket_path = sock_base / "s"
    session = f"yb-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))

    def tmux_cmd(*args, timeout=8):
        return subprocess.run(
            [tmux_binary, "-S", str(socket_path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def capture():
        return tmux_cmd("capture-pane", "-p", "-t", f"{session}:").stdout or ""

    def wait_until(predicate, timeout=20):
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            last = capture()
            if predicate(last):
                return True, last
            time.sleep(0.4)
        return False, last

    app = None
    try:
        created = tmux_cmd(
            "new-session", "-d", "-s", session, "-x", "120", "-y", "40",
            f"cd {REPO_ROOT} && exec python3 tools/{agent}.py --mock",
        )
        assert created.returncode == 0, f"tmux new-session failed: {created.stderr or created.stdout}"
        booted, pane = wait_until(lambda text: "❯" in text or "›" in text)
        assert booted, f"{agent}.py --mock did not boot to an input prompt:\n{pane}"
        tmux_cmd("send-keys", "-t", f"{session}:", user_input, "Enter")
        prompted, pane = wait_until(lambda text: user_input in text and ("Would you like to run the following command?" in text or "Do you want to proceed?" in text))
        assert prompted, f"{agent}.py --mock did not render an approval prompt after `{user_input}`:\n{pane}"

        app = TmuxWebtermApp([session], dangerously_yolo=False)
        payload = app.auto_approve_session_status(session, capture_bare_session_when_roster=True)
        assert payload["prompt"]["visible"] is True
        assert payload["screen"]["key"] == "approval"
        assert payload["prompt"]["agent"] == agent
        if agent == "codex":
            assert payload["prompt"]["command"] == user_input
        assert payload["prompt"]["signature"]

        auto_approve_payload = {
            "session_order": [session],
            "sessions": {session: payload},
            "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
        }
        load_live_runtime_boot_fixture(
            browser,
            tmp_path,
            sessions=[session],
            transcript_sessions={session: {"agents": [{"kind": agent}], "panes": []}},
            auto_approve_payload=auto_approve_payload,
        )
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                const session = arguments[0];
                return document.getElementById(`panel-${session}`)
                  && document.getElementById('topbarActivity')?.textContent?.includes('ASK?');
                """,
                session,
            )
        )
        metrics = browser.execute_script(
            """
            const session = arguments[0];
            const panel = document.getElementById(`panel-${session}`);
            const tab = document.getElementById(`panel-tab-${session}`);
            const topbar = document.getElementById('topbarActivity');
            const beforeSocketFrames = (window.__bootSocketInstances || []).flatMap(socket => socket.sent || []);
            const badge = tab?.querySelector('[data-prompt-attention-clear]');
            const before = {
              badgeText: badge?.textContent || '',
              badgeHasSharedParent: badge?.classList.contains('status-indicator') || false,
              badgeHasTextModifier: badge?.classList.contains('status-indicator--text') || false,
              badgeHasAttentionModifier: badge?.classList.contains('status-indicator--attention') || false,
              badgeHasPulse: badge?.classList.contains('attention-pulse') || false,
              tabAttention: tab?.classList.contains('needs-attention') || false,
              panelNeedsApproval: panel?.classList.contains('needs-exec-pane') || false,
              topbarText: topbar?.textContent || '',
              topbarAskHasSharedParent: topbar?.querySelector('.topbar-activity-ask')?.classList.contains('status-indicator') || false,
              topbarAskHasAttentionModifier: topbar?.querySelector('.topbar-activity-ask')?.classList.contains('status-indicator--attention') || false,
              topbarAskHasPulse: topbar?.querySelector('.topbar-activity-ask')?.classList.contains('attention-pulse') || false,
            };
            badge?.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            const afterSocketFrames = (window.__bootSocketInstances || []).flatMap(socket => socket.sent || []);
            const after = {
              badgeText: tab?.querySelector('[data-prompt-attention-clear]')?.textContent || '',
              tabAttention: tab?.classList.contains('needs-attention') || false,
              panelNeedsApproval: panel?.classList.contains('needs-exec-pane') || false,
              topbarText: topbar?.textContent || '',
              newInputFrames: afterSocketFrames.slice(beforeSocketFrames.length).filter(frame => String(frame).includes('"type":"input"')).length,
            };
            return {before, after};
            """,
            session,
        )
        assert metrics["before"]["badgeText"] == "ASK?"
        assert metrics["before"]["badgeHasSharedParent"] is True
        assert metrics["before"]["badgeHasTextModifier"] is True
        assert metrics["before"]["badgeHasAttentionModifier"] is True
        assert metrics["before"]["badgeHasPulse"] is True
        assert metrics["before"]["tabAttention"] is True
        assert metrics["before"]["panelNeedsApproval"] is True
        assert "1 ASK?" in metrics["before"]["topbarText"]
        assert metrics["before"]["topbarAskHasSharedParent"] is True
        assert metrics["before"]["topbarAskHasAttentionModifier"] is True
        assert metrics["before"]["topbarAskHasPulse"] is True
        assert metrics["after"]["badgeText"] == ""
        assert metrics["after"]["tabAttention"] is False
        assert metrics["after"]["panelNeedsApproval"] is False
        assert "0 ASK?" in metrics["after"]["topbarText"]
        assert metrics["after"]["newInputFrames"] == 0
    finally:
        if app is not None:
            stop = getattr(getattr(app, "control_server", None), "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
        subprocess.run(
            [tmux_binary, "-S", str(socket_path), "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        shutil.rmtree(sock_base, ignore_errors=True)
        cleanup_isolated_browser_runtime_paths(paths)


@pytest.mark.e2e
def test_real_agent_prompts_render_ask_attention_in_live_server(browser, monkeypatch, tmp_path):
    if os.environ.get("YOLOMUX_REAL_AGENT_SMOKE") != "1":
        pytest.skip("set YOLOMUX_REAL_AGENT_SMOKE=1 to run real Claude/Codex prompt smoke")
    tmux_binary = shutil.which("tmux")
    codex_binary = shutil.which("codex")
    claude_binary = shutil.which("claude")
    if not tmux_binary:
        pytest.skip("tmux is not installed")
    if not codex_binary:
        pytest.skip("codex is not installed")
    if not claude_binary:
        pytest.skip("claude is not installed")

    paths = isolate_browser_runtime_paths(monkeypatch, tmp_path)
    sock_base = Path("/tmp") / f"yoask-real-ui-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    sock_base.mkdir(mode=0o700)
    socket_path = sock_base / "s"
    sessions = {
        "codex": f"yr-codex-{os.getpid()}-{uuid.uuid4().hex[:6]}",
        "claude": f"yr-claude-{os.getpid()}-{uuid.uuid4().hex[:6]}",
    }
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))

    def tmux_cmd(*args, timeout=8):
        return subprocess.run(
            [tmux_binary, "-S", str(socket_path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def capture(session):
        return tmux_cmd("capture-pane", "-p", "-t", f"{session}:").stdout or ""

    def wait_until(session, predicate, timeout=120):
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            last = capture(session)
            if predicate(last):
                return True, last
            time.sleep(0.4)
        return False, last

    def wait_for_codex_sleep_prompt(session, timeout=120):
        deadline = time.time() + timeout
        last = ""
        extra_submit_sent = False
        while time.time() < deadline:
            last = capture(session)
            if "Would you like to run the following" in last and "sleep 10" in last:
                return True, last
            if not extra_submit_sent and "› Run sleep 10" in last:
                tmux_cmd("send-keys", "-t", f"{session}:", "C-m")
                extra_submit_sent = True
            time.sleep(0.4)
        return False, last

    def wait_for_claude_plan_prompt(session, timeout=120):
        deadline = time.time() + timeout
        last = ""
        extra_submit_sent = False
        while time.time() < deadline:
            last = capture(session)
            if "Claude has written up a plan" in last and "Would you like to proceed?" in last:
                return True, last
            if not extra_submit_sent and "Add a temporary line to README.md" in last:
                tmux_cmd("send-keys", "-t", f"{session}:", "C-m")
                extra_submit_sent = True
            time.sleep(0.4)
        return False, last

    app = None
    server = None
    thread = None
    try:
        codex_command = (
            f"cd {REPO_ROOT} && exec codex --no-alt-screen "
            f"--ask-for-approval untrusted --sandbox read-only -C {REPO_ROOT}"
        )
        claude_command = f"cd {REPO_ROOT} && exec claude --permission-mode plan --safe-mode"
        for session, command in ((sessions["codex"], codex_command), (sessions["claude"], claude_command)):
            created = tmux_cmd("new-session", "-d", "-s", session, "-x", "120", "-y", "40", command, timeout=10)
            assert created.returncode == 0, f"tmux new-session failed for {session}: {created.stderr or created.stdout}"

        codex_ready, codex_pane = wait_until(sessions["codex"], lambda text: "›" in text or "Codex" in text, timeout=45)
        assert codex_ready, f"Codex did not reach an input prompt:\n{codex_pane}"
        claude_ready, claude_pane = wait_until(sessions["claude"], lambda text: "❯" in text or "Claude Code" in text, timeout=45)
        assert claude_ready, f"Claude did not reach an input prompt:\n{claude_pane}"

        app = TmuxWebtermApp(list(sessions.values()), dangerously_yolo=False)
        initial_payloads = {
            agent: app.auto_approve_session_status(session, capture_bare_session_when_roster=True)
            for agent, session in sessions.items()
        }
        assert initial_payloads["codex"]["prompt"]["visible"] is False, initial_payloads["codex"]
        assert initial_payloads["claude"]["prompt"]["visible"] is False, initial_payloads["claude"]

        server, thread = start_browser_share_server(monkeypatch, tmp_path, app, auth_bypass=True)
        base_url = f"http://127.0.0.1:{server.server_address[1]}/"
        browser.get(base_url + "?" + urlencode({"sessions": ",".join(sessions.values())}))
        WebDriverWait(browser, 10).until(
            lambda driver: driver.execute_script(
                """
                const sessions = arguments[0];
                return sessions.every(session => document.getElementById(`panel-tab-${session}`))
                  && typeof globalActivityCounts === 'function'
                  && globalActivityCounts().ask === 0;
                """,
                list(sessions.values()),
            )
        )
        initial_ui = browser.execute_script(
            """
            const sessions = arguments[0];
            return {
              ask: globalActivityCounts().ask,
              badges: sessions.map(session => document.getElementById(`panel-tab-${session}`)?.querySelector('[data-prompt-attention-clear]')?.textContent || ''),
              topbar: document.getElementById('topbarActivity')?.textContent || '',
            };
            """,
            list(sessions.values()),
        )
        assert initial_ui["ask"] == 0, initial_ui
        assert initial_ui["badges"] == ["", ""], initial_ui

        tmux_cmd("send-keys", "-t", f"{sessions['codex']}:", "Run sleep 10", "Enter")
        codex_prompted, codex_pane = wait_for_codex_sleep_prompt(sessions["codex"])
        assert codex_prompted, f"Codex did not render the real sleep approval prompt:\n{codex_pane}"

        tmux_cmd("send-keys", "-t", f"{sessions['claude']}:", "Add a temporary line to README.md, then wait for approval before editing", "Enter")
        claude_prompted, claude_pane = wait_for_claude_plan_prompt(sessions["claude"])
        assert claude_prompted, f"Claude did not render the real plan approval prompt:\n{claude_pane}"

        prompted_status_payload, prompted_status = app.auto_approve_status()
        assert prompted_status == HTTPStatus.OK, prompted_status_payload
        prompted_payloads = {
            agent: prompted_status_payload["sessions"][session]
            for agent, session in sessions.items()
        }
        assert prompted_payloads["codex"]["prompt"]["visible"] is True, prompted_payloads["codex"]
        assert prompted_payloads["codex"]["screen"]["key"] == "approval", prompted_payloads["codex"]
        assert prompted_payloads["codex"]["prompt"]["agent"] == "codex", prompted_payloads["codex"]
        assert prompted_payloads["codex"]["prompt"]["prompt_kind"] == "shell-command", prompted_payloads["codex"]
        assert prompted_payloads["claude"]["prompt"]["visible"] is True, prompted_payloads["claude"]
        assert prompted_payloads["claude"]["screen"]["key"] == "approval", prompted_payloads["claude"]
        assert prompted_payloads["claude"]["prompt"]["agent"] == "claude", prompted_payloads["claude"]

        ui_deadline = time.time() + 15
        prompted_ui = {}
        while time.time() < ui_deadline:
            prompted_ui = browser.execute_async_script(
                """
                const sessions = arguments[0];
                const done = arguments[arguments.length - 1];
                Promise.resolve(refreshAutoStatuses()).then(() => {
                  refreshActivePanelHeaders();
                  updateTopbarActivityStatus();
                  const counts = globalActivityCounts();
                  done({
                    ok: counts.ask === sessions.length,
                    counts,
                    topbar: document.getElementById('topbarActivity')?.textContent || '',
                    states: sessions.map(session => {
                      const payload = autoApproveStates.get(session) || {};
                      const state = sessionState(session);
                      const tab = document.getElementById(`panel-tab-${session}`);
                      const panel = document.getElementById(`panel-${session}`);
                      return {
                        session,
                        promptVisible: payload.prompt?.visible === true,
                        promptAgent: payload.prompt?.agent || '',
                        screenKey: payload.screen?.key || '',
                        stateKey: state?.key || '',
                        stateAttention: state?.attention === true,
                        badge: tab?.querySelector('[data-prompt-attention-clear]')?.textContent || '',
                        tabAttention: tab?.classList.contains('needs-attention') || false,
                        panelNeedsApproval: panel?.classList.contains('needs-exec-pane') || false,
                      };
                    }),
                    errors: window.__bootErrors || [],
                    rejections: window.__bootRejections || [],
                  });
                }).catch(error => done({ok: false, error: String(error && error.stack || error)}));
                """,
                list(sessions.values()),
            )
            if prompted_ui.get("ok") is True:
                break
            time.sleep(0.5)
        assert prompted_ui.get("ok") is True, prompted_ui
        browser.execute_script(
            """
            if (!WebSocket.prototype.__askClearTracked) {
              const nativeSend = WebSocket.prototype.send;
              window.__askClearWsFrames = [];
              WebSocket.prototype.send = function(data) {
                window.__askClearWsFrames.push({url: String(this.url || ''), data: String(data || '')});
                return nativeSend.call(this, data);
              };
              WebSocket.prototype.__askClearTracked = true;
            } else {
              window.__askClearWsFrames = [];
            }
            """
        )
        metrics = browser.execute_script(
            """
            const sessions = arguments[0];
            function snapshot() {
              return {
                ask: globalActivityCounts().ask,
                topbar: document.getElementById('topbarActivity')?.textContent || '',
                sessions: sessions.map(session => {
                  const tab = document.getElementById(`panel-tab-${session}`);
                  const panel = document.getElementById(`panel-${session}`);
                  return {
                    session,
                    badge: tab?.querySelector('[data-prompt-attention-clear]')?.textContent || '',
                    tabAttention: tab?.classList.contains('needs-attention') || false,
                    panelNeedsApproval: panel?.classList.contains('needs-exec-pane') || false,
                  };
                }),
              };
            }
            const before = snapshot();
            for (const session of sessions) {
              document.getElementById(`panel-tab-${session}`)?.querySelector('[data-prompt-attention-clear]')?.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            }
            const after = snapshot();
            const inputFrames = (window.__askClearWsFrames || []).filter(frame => frame.data.includes('"type":"input"'));
            return {before, after, inputFrames};
            """,
            list(sessions.values()),
        )
        assert metrics["before"]["ask"] == 2, metrics
        assert "2 ASK?" in metrics["before"]["topbar"], metrics
        assert [item["badge"] for item in metrics["before"]["sessions"]] == ["ASK?", "ASK?"], metrics
        assert [item["tabAttention"] for item in metrics["before"]["sessions"]] == [True, True], metrics
        assert [item["panelNeedsApproval"] for item in metrics["before"]["sessions"]] == [True, True], metrics
        assert metrics["after"]["ask"] == 0, metrics
        assert "0 ASK?" in metrics["after"]["topbar"], metrics
        assert [item["badge"] for item in metrics["after"]["sessions"]] == ["", ""], metrics
        assert [item["tabAttention"] for item in metrics["after"]["sessions"]] == [False, False], metrics
        assert [item["panelNeedsApproval"] for item in metrics["after"]["sessions"]] == [False, False], metrics
        assert metrics["inputFrames"] == [], metrics
    finally:
        if server is not None and thread is not None:
            stop_browser_share_server(server, thread)
        elif app is not None:
            stop = getattr(getattr(app, "control_server", None), "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
        subprocess.run(
            [tmux_binary, "-S", str(socket_path), "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        shutil.rmtree(sock_base, ignore_errors=True)
        cleanup_isolated_browser_runtime_paths(paths)


def test_yoagent_settings_operator_updates_live_gui_and_denies_readonly(browser, tmp_path):
    base_settings = {
        "appearance": {
            "theme": "dark",
            "active_color": "green",
            "tab_width": 180,
            "terminal_font_size": 13,
        },
        "updates": {"notify_level": "patch"},
        "yoagent": {"backend": "auto"},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        sessions=["1"],
        settings=base_settings,
        yoagent_chat_mode="settings",
        available_agents=["term", "codex"],
        agent_auth={"codex": {"installed": True, "logged_in": True}},
    )
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return typeof openInfoSubTab === 'function' && typeof sendYoagentChatMessage === 'function'"))
    admin = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          await openInfoSubTab('yoagent');
          for (const prompt of [
            'set theme to light',
            'set active color to blue',
            'set tab width to 220',
            'set terminal font size to 18',
            'change notification level to none',
            'maybe theme',
          ]) {
            await sendYoagentChatMessage(prompt);
          }
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const rootStyle = getComputedStyle(document.documentElement);
          const chat = document.querySelector('.yoagent-chat');
          const history = document.querySelector('.yoagent-chat-history')?.getBoundingClientRect();
          const form = document.querySelector('[data-yoagent-chat-form]')?.getBoundingClientRect();
          done({
            bodyClass: document.body.className,
            theme: clientSettings.appearance?.theme,
            activeColor: clientSettings.appearance?.active_color,
            activeAccent: rootStyle.getPropertyValue('--active-accent').trim(),
            tabWidth: rootStyle.getPropertyValue('--pane-tab-width').trim(),
            terminalFontSize: rootStyle.getPropertyValue('--terminal-font-size').trim(),
            notifyLevel: clientSettings.updates?.notify_level,
            text: document.querySelector('#yoagent-content')?.innerText || '',
            assistantCount: document.querySelectorAll('.yoagent-message.assistant').length,
            formEnabled: document.querySelector('[data-yoagent-chat-input]')?.disabled === false,
            noOverlap: Boolean(chat && history && form && history.bottom <= form.top + 1),
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    assert admin.get("error") is None, admin
    assert "theme-light" in admin["bodyClass"]
    assert admin["theme"] == "light"
    assert admin["activeColor"] == "blue"
    assert admin["activeAccent"] == "#2563eb"
    assert admin["tabWidth"] == "220px"
    assert admin["terminalFontSize"] == "18px"
    assert admin["notifyLevel"] == "none"
    assert "Updated this Preference" in admin["text"]
    assert "| `appearance.theme` | `dark` | `light` | Preferences -> Appearance | `live` |" in admin["text"]
    assert "Which setting do you mean" in admin["text"]
    assert admin["assistantCount"] >= 6
    assert admin["formEnabled"] is True
    assert admin["noOverlap"] is True

    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        sessions=["1"],
        settings=base_settings,
        access_role="readonly",
        yoagent_chat_mode="settings",
        available_agents=["term", "codex"],
        agent_auth={"codex": {"installed": True, "logged_in": True}},
    )
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return typeof openInfoSubTab === 'function' && typeof sendYoagentChatMessage === 'function'"))
    readonly = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          await openInfoSubTab('yoagent');
          await sendYoagentChatMessage('set theme to light');
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          done({
            bodyClass: document.body.className,
            theme: clientSettings.appearance?.theme,
            text: document.querySelector('#yoagent-content')?.innerText || '',
            inputDisabled: document.querySelector('[data-yoagent-chat-input]')?.disabled === true,
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    assert readonly.get("error") is None, readonly
    assert "theme-light" not in readonly["bodyClass"]
    assert readonly["theme"] == "dark"
    assert "requires an admin login" in readonly["text"]
    assert readonly["inputDisabled"] is False


def test_yoagent_busy_chat_uses_one_vertical_scroll_owner(browser, tmp_path):
    for label, grid_width, window_width in (("desktop", 1000, 1120), ("narrow", 520, 640)):
        browser.set_window_size(window_width, 720)
        load_live_runtime_boot_fixture(
            browser,
            tmp_path,
            sessions=["1"],
            settings={"yoagent": {"backend": "auto"}},
            grid_width=grid_width,
            grid_height=460,
            available_agents=["term", "codex"],
            agent_auth={"codex": {"installed": True, "logged_in": True}},
        )
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return typeof openInfoSubTab === 'function' && typeof sendYoagentChatMessage === 'function'"))
        metrics = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const raf = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const responsePayload = {
              answer: 'done',
              backend: 'yolomux',
              backend_used: 'yolomux',
              deterministic: true,
              conversation: {
                messages: [
                  {role: 'user', content: 'summarize activity', createdAt: '2026-06-19T08:00:00Z'},
                  {role: 'assistant', content: 'Done.', createdAt: '2026-06-19T08:00:01Z'},
                ],
                pending_waits: [],
              },
            };
            const rectFor = element => {
              if (!element) return null;
              const rect = element.getBoundingClientRect();
              return {
                left: rect.left,
                top: rect.top,
                right: rect.right,
                bottom: rect.bottom,
                width: rect.width,
                height: rect.height,
              };
            };
            const boxFor = element => {
              if (!element) return null;
              const style = getComputedStyle(element);
              return {
                overflowX: style.overflowX,
                overflowY: style.overflowY,
                overscrollBehaviorX: style.overscrollBehaviorX,
                overscrollBehaviorY: style.overscrollBehaviorY,
                scrollHeight: element.scrollHeight,
                clientHeight: element.clientHeight,
                scrollWidth: element.scrollWidth,
                clientWidth: element.clientWidth,
                scrollTop: element.scrollTop,
                rect: rectFor(element),
              };
            };
            const styleFor = element => {
              if (!element) return null;
              const style = getComputedStyle(element);
              return {
                overflowX: style.overflowX,
                overflowY: style.overflowY,
                overscrollBehaviorX: style.overscrollBehaviorX,
                overscrollBehaviorY: style.overscrollBehaviorY,
                pointerEvents: style.pointerEvents,
              };
            };
            const collect = () => {
              const infoPane = document.querySelector('.info-pane');
              const outer = document.querySelector('#yoagent-content.info-list.yoagent-list');
              const chat = document.querySelector('.yoagent-chat');
              const history = document.querySelector('.yoagent-chat-history');
              const form = document.querySelector('[data-yoagent-chat-form]');
              const status = document.querySelector('.yoagent-chat-status');
              const streaming = document.querySelector('.yoagent-message.streaming');
              const pre = document.querySelector('.yoagent-chat .markdown-body pre');
              const details = document.querySelector('.yoagent-message-details:not(.yoagent-toolcall-details)');
              const toolDetails = document.querySelector('.yoagent-toolcall-details');
              const auxPreview = details?.querySelector('.yoagent-details-preview');
              const auxStream = details?.querySelector('.yoagent-auxiliary-stream');
              const toolPreview = toolDetails?.querySelector('.yoagent-details-preview');
              const toolStream = toolDetails?.querySelector('.yoagent-toolcall-stream');
              const assistantMessage = document.querySelector('.yoagent-message.assistant');
              const userMessage = document.querySelector('.yoagent-message.user');
              const messageBody = document.querySelector('.yoagent-message-body');
              const markdownBody = document.querySelector('.yoagent-message-body.markdown-body');
              const streamingBody = streaming?.querySelector?.('.yoagent-message-body');
              const actionText = document.querySelector('.yoagent-action-text');
              const input = document.querySelector('[data-yoagent-chat-input]');
              const stopButton = document.querySelector('[data-yoagent-chat-cancel]');
              const queue = document.querySelector('.yoagent-chat-queue');
              const queueCancel = document.querySelector('[data-yoagent-queued-cancel]');
              const boxes = {
                infoPane: boxFor(infoPane),
                outer: boxFor(outer),
                chat: boxFor(chat),
                history: boxFor(history),
                pre: boxFor(pre),
                details: boxFor(details),
                actionText: boxFor(actionText),
              };
              const targetStyles = {
                assistantMessage: styleFor(assistantMessage),
                userMessage: styleFor(userMessage),
                messageBody: styleFor(messageBody),
                markdownBody: styleFor(markdownBody),
                auxPreview: styleFor(auxPreview),
                auxStream: styleFor(auxStream),
              };
              return {
                ...boxes,
                targetStyles,
                verticalOverflowKeys: Object.entries(boxes)
                  .filter(([, box]) => box && box.scrollHeight > box.clientHeight + 1)
                  .map(([key]) => key),
                hasStatus: Boolean(status),
                hasStreaming: Boolean(streaming),
                noHistoryFormOverlap: Boolean(history && form && history.getBoundingClientRect().bottom <= form.getBoundingClientRect().top + 1),
                statusInsideHistory: Boolean(status && history && history.contains(status)),
                streamingInsideHistory: Boolean(streaming && history && history.contains(streaming)),
                inputDisabled: input?.disabled === true,
                hasStopButton: Boolean(stopButton),
                hasQueue: Boolean(queue),
                queueText: queue?.textContent || '',
                hasQueuedCancel: Boolean(queueCancel),
                thinkingDetailsOpen: details?.open === true,
                toolDetailsOpen: toolDetails?.open === true,
                auxPreviewText: auxPreview?.textContent || '',
                auxStreamText: auxStream?.textContent || '',
                toolPreviewText: toolPreview?.textContent || '',
                toolStreamText: toolStream?.textContent || '',
                streamingBodyText: streamingBody?.textContent || '',
                errors: window.__bootErrors || [],
                rejections: window.__bootRejections || [],
              };
            };
            (async () => {
              await openInfoSubTab('yoagent');
              applyYoagentConversationPayload({
                transcript_path: '/home/test/.local/state/yolomux/yoagent/conversation.jsonl',
                transcript_display_path: '~/.local/state/yolomux/yoagent/conversation.jsonl',
                messages: Array.from({length: 80}, (_, index) => ({
                  role: index % 2 ? 'assistant' : 'user',
                  content: index === 1
                    ? 'message 2 with a wide code block\\n\\n```js\\nconst wide = "' + Array.from({length: 36}, () => 'wide_token').join('_') + '";\\n```'
                    : `message ${index + 1} ` + Array.from({length: 10}, (_line, lineIndex) => `detail-${lineIndex + 1}-for-message-${index + 1}`).join(' '),
                  createdAt: `2026-06-19T08:${String(index).padStart(2, '0')}:00Z`,
                })),
                pending_waits: [],
              });
              renderYoagentPanel({scrollBottom: true});
              await raf();
              const originalFetch = window.fetch;
              let releaseChat = null;
              const fetchCalls = [];
              window.fetch = (input, options = {}) => {
                const url = new URL(String(input), window.location.href);
                fetchCalls.push({path: url.pathname, method: options.method || 'GET', body: String(options.body || ''), hasSignal: Boolean(options.signal)});
                if (url.pathname === '/api/yoagent/chat') {
                  return new Promise((resolve, reject) => {
                    releaseChat = () => resolve(new Response(JSON.stringify(responsePayload), {status: 200, headers: {'Content-Type': 'application/json'}}));
                    options.signal?.addEventListener?.('abort', () => {
                      const error = new Error('aborted');
                      error.name = 'AbortError';
                      reject(error);
                    });
                  });
                }
                if (/^\/api\/yoagent\/chat\/.+\/cancel$/.test(url.pathname)) {
                  return Promise.resolve(new Response(JSON.stringify({ok: true, cancelled: true}), {status: 200, headers: {'Content-Type': 'application/json'}}));
                }
                return originalFetch(input, options);
              };
              const sendPromise = sendYoagentChatMessage('summarize activity');
              await raf();
              const active = yoagentActiveChatRequest ? {id: yoagentActiveChatRequest.id, streamId: yoagentActiveChatRequest.streamId} : null;
              const immediate = collect();
              const input = document.querySelector('[data-yoagent-chat-input]');
              const form = document.querySelector('[data-yoagent-chat-form]');
              if (input && form) {
                input.value = 'queued followup';
                input.dispatchEvent(new Event('input', {bubbles: true}));
                form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
              }
              await raf();
              const queueBeforeCancel = collect();
              document.querySelector('[data-yoagent-queued-cancel]')?.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
              await raf();
              const queueAfterCancel = collect();
              applyYoagentStreamPayload({
                stream_id: active?.streamId || active?.id || '',
                backend: 'claude',
                phase: 'delta',
                content: Array.from({length: 24}, (_, index) => `streamed line ${index + 1}: working through the activity context`).join('\\n'),
                auxiliary_lines: ['thinking: scanning recent events', 'thinking: reading activity context', 'thinking: final synthesis', 'tool output: command: collected files'],
                auxiliary_preview: 'thinking: reading activity context\\nthinking: final synthesis\\ntool output: command: collected files',
                hidden_work_active: true,
                tool_active: true,
              });
              renderYoagentPanel({preserveDraft: true, scrollBottom: 'auto', allowBusyRebuild: true});
              await raf();
              await new Promise(resolve => setTimeout(resolve, 25));
              const history = document.querySelector('.yoagent-chat-history');
              if (history) history.scrollTop = 1;
              const manualScrollTop = history?.scrollTop || 0;
              renderYoagentPanel({preserveDraft: true, scrollBottom: 'auto'});
              await raf();
              const measured = collect();
              const thinkingDetails = document.querySelector('.yoagent-message-details:not(.yoagent-toolcall-details)');
              const toolDetails = document.querySelector('.yoagent-toolcall-details');
              const collapsedThinkingHeight = thinkingDetails?.getBoundingClientRect?.().height || 0;
              const collapsedToolHeight = toolDetails?.getBoundingClientRect?.().height || 0;
              if (thinkingDetails) thinkingDetails.open = true;
              if (toolDetails) toolDetails.open = true;
              await raf();
              measured.expandedThinkingStreamText = thinkingDetails?.querySelector('.yoagent-auxiliary-stream')?.textContent || '';
              measured.expandedToolStreamText = toolDetails?.querySelector('.yoagent-toolcall-stream')?.textContent || '';
              measured.expandedThinkingHeight = thinkingDetails?.getBoundingClientRect?.().height || 0;
              measured.expandedToolHeight = toolDetails?.getBoundingClientRect?.().height || 0;
              measured.collapsedThinkingHeight = collapsedThinkingHeight;
              measured.collapsedToolHeight = collapsedToolHeight;
              measured.manualScrollTop = manualScrollTop;
              const refreshedHistory = document.querySelector('.yoagent-chat-history');
              measured.afterRefreshScrollTop = refreshedHistory?.scrollTop || 0;
              measured.afterRefreshBottomGap = refreshedHistory ? refreshedHistory.scrollHeight - refreshedHistory.clientHeight - refreshedHistory.scrollTop : 0;
              if (refreshedHistory) refreshedHistory.scrollTop = 0;
              const formWheelTarget = document.querySelector('[data-yoagent-chat-form]');
              const formWheelEvent = new WheelEvent('wheel', {deltaY: 240, bubbles: true, cancelable: true});
              const formWheelResult = formWheelTarget ? formWheelTarget.dispatchEvent(formWheelEvent) : true;
              await raf();
              measured.formWheelScrollTop = refreshedHistory?.scrollTop || 0;
              measured.formWheelPrevented = formWheelEvent.defaultPrevented === true || formWheelResult === false;
              measured.releaseAvailable = typeof releaseChat === 'function';
              measured.activeRequest = active;
              measured.immediate = immediate;
              measured.queueBeforeCancel = queueBeforeCancel;
              measured.queueAfterCancel = queueAfterCancel;
              document.querySelector('[data-yoagent-chat-cancel]')?.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
              await sendPromise;
              await raf();
              measured.afterCancel = {
                activeRequest: yoagentActiveChatRequest ? {id: yoagentActiveChatRequest.id, streamId: yoagentActiveChatRequest.streamId} : null,
                busy: yoagentBusy === true,
                inputDisabled: document.querySelector('[data-yoagent-chat-input]')?.disabled === true,
                stoppedText: document.querySelector('.yoagent-message.stopped')?.textContent || '',
                cancelCallCount: fetchCalls.filter(call => /^\\/api\\/yoagent\\/chat\\/.+\\/cancel$/.test(call.path)).length,
              };
              measured.chatRequestBodies = fetchCalls.filter(call => call.path === '/api/yoagent/chat').map(call => JSON.parse(call.body || '{}'));
              window.fetch = originalFetch;
              done(measured);
            })().catch(error => done({error: String(error && error.stack || error), errors: window.__bootErrors || [], rejections: window.__bootRejections || []}));
            """
        )
        assert metrics.get("error") is None, (label, metrics)
        assert metrics["errors"] == [], (label, metrics)
        assert metrics["rejections"] == [], (label, metrics)
        assert metrics["releaseAvailable"] is True, (label, metrics)
        assert metrics["activeRequest"]["id"] and metrics["activeRequest"]["streamId"], (label, metrics)
        assert metrics["chatRequestBodies"][0]["request_id"] == metrics["activeRequest"]["id"], (label, metrics)
        assert metrics["chatRequestBodies"][0]["stream_id"] == metrics["activeRequest"]["streamId"], (label, metrics)
        assert metrics["immediate"]["inputDisabled"] is False, (label, metrics)
        assert metrics["immediate"]["hasStopButton"] is True, (label, metrics)
        assert "thinking" in metrics["immediate"]["auxPreviewText"], (label, metrics)
        assert metrics["queueBeforeCancel"]["hasQueue"] is True, (label, metrics)
        assert metrics["queueBeforeCancel"]["hasQueuedCancel"] is True, (label, metrics)
        assert "queued followup" in metrics["queueBeforeCancel"]["queueText"], (label, metrics)
        assert metrics["queueAfterCancel"]["hasQueue"] is False, (label, metrics)
        assert metrics["hasStatus"] is True, (label, metrics)
        assert metrics["hasStreaming"] is True, (label, metrics)
        assert metrics["statusInsideHistory"] is True, (label, metrics)
        assert metrics["streamingInsideHistory"] is True, (label, metrics)
        assert metrics["hasStopButton"] is True, (label, metrics)
        assert metrics["inputDisabled"] is False, (label, metrics)
        assert metrics["thinkingDetailsOpen"] is False, (label, metrics)
        assert metrics["toolDetailsOpen"] is False, (label, metrics)
        assert metrics["auxPreviewText"] == "thinking: reading activity context thinking: final synthesis", (label, metrics)
        assert metrics["toolPreviewText"] == "tool output: command: collected files", (label, metrics)
        assert "thinking: reading activity context" in metrics["auxStreamText"], (label, metrics)
        assert "tool output: command: collected files" not in metrics["auxStreamText"], (label, metrics)
        assert "tool output: command: collected files" in metrics["toolStreamText"], (label, metrics)
        assert "thinking: scanning recent events" in metrics["expandedThinkingStreamText"], (label, metrics)
        assert "tool output: command: collected files" in metrics["expandedToolStreamText"], (label, metrics)
        assert metrics["expandedThinkingHeight"] > metrics["collapsedThinkingHeight"], (label, metrics)
        assert metrics["expandedToolHeight"] > metrics["collapsedToolHeight"], (label, metrics)
        assert "thinking: reading activity context" not in metrics["streamingBodyText"], (label, metrics)
        assert metrics["afterCancel"]["activeRequest"] is None, (label, metrics)
        assert metrics["afterCancel"]["busy"] is False, (label, metrics)
        assert metrics["afterCancel"]["inputDisabled"] is False, (label, metrics)
        assert metrics["afterCancel"]["cancelCallCount"] == 1, (label, metrics)
        assert "Stopped." in metrics["afterCancel"]["stoppedText"], (label, metrics)
        assert metrics["outer"]["overflowY"] == "hidden", (label, metrics)
        assert metrics["outer"]["scrollHeight"] <= metrics["outer"]["clientHeight"] + 1, (label, metrics)
        assert metrics["history"]["overflowY"] == "auto", (label, metrics)
        assert metrics["history"]["overscrollBehaviorY"] == "auto", (label, metrics)
        assert metrics["history"]["scrollHeight"] > metrics["history"]["clientHeight"], (label, metrics)
        assert metrics["verticalOverflowKeys"] == ["history"], (label, metrics)
        for target in ("assistantMessage", "userMessage", "messageBody", "markdownBody"):
            assert metrics["targetStyles"][target]["overflowY"] == "visible", (label, target, metrics)
            assert metrics["targetStyles"][target]["overscrollBehaviorY"] == "auto", (label, target, metrics)
        assert metrics["targetStyles"]["auxPreview"]["overflowY"] == "clip", (label, metrics)
        assert metrics["targetStyles"]["auxStream"]["overscrollBehaviorY"] == "auto", (label, metrics)
        assert metrics["chat"]["rect"]["bottom"] <= metrics["outer"]["rect"]["bottom"] + 1, (label, metrics)
        assert metrics["noHistoryFormOverlap"] is True, (label, metrics)
        assert metrics["manualScrollTop"] > 0, (label, metrics)
        assert metrics["afterRefreshScrollTop"] > 0, (label, metrics)
        assert metrics["afterRefreshBottomGap"] > 48, (label, metrics)
        assert metrics["formWheelScrollTop"] > 0, (label, metrics)
        assert metrics["formWheelPrevented"] is True, (label, metrics)
        screenshot = browser_screenshot_rgb(browser)
        assert screenshot.size[0] >= window_width - 20, (label, screenshot.size)
        assert screenshot.getbbox() is not None, label


def test_yoagent_auxiliary_details_are_subdued_in_dark_and_light(browser, tmp_path):
    for theme_class in ("theme-dark", "theme-light"):
        page = tmp_path / f"yoagent-auxiliary-{theme_class}.html"
        page.write_text(
            page_html(
                f"""
                <script>document.body.className = {json.dumps(theme_class)};</script>
                <section class="yoagent-chat">
                  <div class="yoagent-message assistant">
                    <div class="yoagent-message-role"><span>YO!agent</span></div>
                    <div class="yoagent-message-body markdown-body">Normal assistant answer</div>
                    <details class="yoagent-message-details has-auxiliary" open>
                      <summary><span>Details</span><span class="yoagent-details-preview">thinking: preview</span></summary>
                      <pre class="yoagent-auxiliary-stream">thinking: preview\ntool output: done</pre>
                    </details>
                  </div>
                </section>
                """,
                extra_css="body { margin: 0; padding: 20px; background: var(--bg); color: var(--text); } .yoagent-chat { width: 420px; }",
            ),
            encoding="utf-8",
        )
        browser.get(page.as_uri())
        metrics = browser.execute_script(
            """
            const body = document.querySelector('.yoagent-message-body');
            const aux = document.querySelector('.yoagent-auxiliary-stream');
            const preview = document.querySelector('.yoagent-details-preview');
            return {
              bodyColor: getComputedStyle(body).color,
              auxColor: getComputedStyle(aux).color,
              previewColor: getComputedStyle(preview).color,
              auxText: aux.textContent,
            };
            """
        )
        assert metrics["auxText"] == "thinking: preview\ntool output: done", (theme_class, metrics)
        assert metrics["auxColor"] != metrics["bodyColor"], (theme_class, metrics)
        assert metrics["previewColor"] != metrics["bodyColor"], (theme_class, metrics)


def test_tabber_session_rows_use_pane_tab_shape_and_keep_columns(browser, tmp_path):
    for label, theme_class, pane_width, window_width in (
        ("dark-narrow", "theme-dark", 300, 700),
        ("light-wide", "theme-light", 520, 900),
    ):
        browser.set_window_size(window_width, 720)
        page = tmp_path / f"tabber-session-row-{label}.html"
        page.write_text(
            page_html(
                f"""
                <script>document.body.className = {json.dumps(theme_class)};</script>
                <section class="fixture-tabber-panel file-explorer-changes-panel">
                  <div class="file-tree" role="tree">
                    <div class="file-tree-row tabber-row kind-dir expanded tabber-active-session" data-tabber-type="session" data-tabber-session="1" role="treeitem" aria-expanded="true" aria-selected="false" aria-current="true" style="padding-left: 8px;">
                      <span class="file-tree-icon tabber-icon">▾</span>
                      <span class="file-tree-name"><span class="tabber-session-tab active" data-tabber-session-chrome="shared"><span class="pane-tab-core"><span class="session-yolo-marker inactive">YO</span><span class="session-button-prefix"><span class="session-button-number">8801</span></span><span class="session-button-text"><span class="session-button-dir tab-inline-detail">tabber session tab styling keeps the date column visible for a deliberately long work description</span></span></span></span></span>
                      <span class="file-tree-agent" hidden></span>
                      <span class="file-tree-diff" hidden></span>
                      <span class="file-tree-dir-count" hidden></span>
                      <span class="file-tree-git-status" hidden></span>
                      <span class="file-tree-date">2m ago</span>
                    </div>
                    <div class="file-tree-row tabber-row kind-file" data-tabber-type="window" data-tabber-session="1" role="treeitem" aria-selected="false" style="padding-left: 27px;">
                      <span class="file-tree-icon tabber-icon"></span>
                      <span class="file-tree-name"><span class="tabber-window-label"><span class="tabber-window-text">0:bash</span></span></span>
                      <span class="file-tree-agent" hidden></span>
                      <span class="file-tree-diff" hidden></span>
                      <span class="file-tree-dir-count" hidden></span>
                      <span class="file-tree-git-status" hidden></span>
                      <span class="file-tree-date">2m ago</span>
                    </div>
                    <div class="file-tree-row tabber-row kind-dir expanded" data-tabber-type="session" data-tabber-session="2" role="treeitem" aria-expanded="true" aria-selected="false" style="padding-left: 8px;">
                      <span class="file-tree-icon tabber-icon">▾</span>
                      <span class="file-tree-name"><span class="tabber-session-tab" data-tabber-session-chrome="shared"><span class="pane-tab-core"><span class="session-yolo-marker inactive">YO</span><span class="session-button-prefix"><span class="session-button-number">2</span></span><span class="session-button-text"><span class="session-button-dir tab-inline-detail">main</span></span></span></span></span>
                      <span class="file-tree-agent" hidden></span>
                      <span class="file-tree-diff" hidden></span>
                      <span class="file-tree-dir-count" hidden></span>
                      <span class="file-tree-git-status" hidden></span>
                      <span class="file-tree-date">15m ago</span>
                    </div>
                    <div class="file-tree-row tabber-row kind-file" data-tabber-type="window" data-tabber-session="2" role="treeitem" aria-selected="false" style="padding-left: 27px;">
                      <span class="file-tree-icon tabber-icon"></span>
                      <span class="file-tree-name"><span class="tabber-window-label"><span class="tabber-window-text">0:bash</span></span></span>
                      <span class="file-tree-agent" hidden></span>
                      <span class="file-tree-diff" hidden></span>
                      <span class="file-tree-dir-count" hidden></span>
                      <span class="file-tree-git-status" hidden></span>
                      <span class="file-tree-date">15m ago</span>
                    </div>
                  </div>
                </section>
                """,
                extra_css=f"""
                  body {{ margin: 0; padding: 16px; background: var(--bg); color: var(--text); }}
                  .fixture-tabber-panel {{ width: {pane_width}px; border: 1px solid var(--border); }}
                """,
            ),
            encoding="utf-8",
        )
        browser.get(page.as_uri())
        metrics = browser.execute_script(
            """
            const rectFor = element => {
              if (!element) return null;
              const rect = element.getBoundingClientRect();
              return {
                left: rect.left,
                top: rect.top,
                right: rect.right,
                bottom: rect.bottom,
                width: rect.width,
                height: rect.height,
              };
            };
            const resolvedColor = (scope, value) => {
              const probe = document.createElement('span');
              probe.style.position = 'absolute';
              probe.style.pointerEvents = 'none';
              probe.style.background = value;
              (scope || document.body).appendChild(probe);
              const color = getComputedStyle(probe).backgroundColor;
              probe.remove();
              return color;
            };
            const rowMetrics = row => {
              const tab = row?.querySelector('.tabber-session-tab');
              const name = row?.querySelector('.session-button-prefix');
              const description = row?.querySelector('.tab-inline-detail');
              const icon = row?.querySelector(':scope > .file-tree-icon');
              const date = row?.querySelector(':scope > .file-tree-date');
              const style = tab ? getComputedStyle(tab) : null;
              const descriptionStyle = description ? getComputedStyle(description) : null;
              return {
                row: rectFor(row),
                tab: rectFor(tab),
                name: rectFor(name),
                description: rectFor(description),
                icon: rectFor(icon),
                date: rectFor(date),
                tabClass: tab?.className || '',
                rowClass: row?.className || '',
                ariaCurrent: row?.getAttribute('aria-current') || '',
                ariaExpanded: row?.getAttribute('aria-expanded') || '',
                iconText: icon?.textContent || '',
                dateText: (date?.textContent || '').trim(),
                dateDisplay: date ? getComputedStyle(date).display : '',
                dateWidth: date ? date.getBoundingClientRect().width : 0,
                tabBg: style?.backgroundColor || '',
                tabColor: style?.color || '',
                descriptionColor: descriptionStyle?.color || '',
                tabHeight: style?.height || '',
                tabRadius: style?.borderTopLeftRadius || '',
                tabBorderTop: style?.borderTopColor || '',
                expectedActiveBg: tab ? resolvedColor(tab.parentElement, 'var(--pane-tab-active-bg)') : '',
                expectedInactiveBg: tab ? resolvedColor(tab.parentElement, 'var(--pane-bar-bg, var(--panel2))') : '',
                descriptionScrollWidth: description?.scrollWidth || 0,
                descriptionClientWidth: description?.clientWidth || 0,
              };
            };
            const sessionRows = Array.from(document.querySelectorAll('.file-tree-row[data-tabber-type="session"]'));
            const windowRows = Array.from(document.querySelectorAll('.file-tree-row[data-tabber-type="window"]'));
            const activeRow = sessionRows.find(row => row.dataset.tabberSession === '1');
            const inactiveRow = sessionRows.find(row => row.dataset.tabberSession === '2');
            const activeWindowRow = windowRows.find(row => row.dataset.tabberSession === '1');
            const activeWindowText = activeWindowRow?.querySelector('.tabber-window-text');
            const windowIcons = windowRows.map(row => (row.querySelector('.file-tree-icon')?.textContent || '').trim());
            const nonSessionWithSessionTab = Array.from(document.querySelectorAll('.file-tree-row:not([data-tabber-type="session"]) .tabber-session-tab')).length;
            return {
              active: rowMetrics(activeRow),
              inactive: rowMetrics(inactiveRow),
              activeWindow: rectFor(activeWindowRow),
              activeWindowTextColor: activeWindowText ? getComputedStyle(activeWindowText).color : '',
              expectedText: resolvedColor(document.body, 'var(--text)'),
              expectedActiveText: resolvedColor(document.body, 'var(--pane-tab-active-text)'),
              expectedMutedText: resolvedColor(document.body, 'var(--muted)'),
              windowIcons,
              nonSessionWithSessionTab,
              sessionCount: sessionRows.length,
              windowCount: windowRows.length,
              bodyClass: document.body.className,
            };
            """
        )
        assert metrics["sessionCount"] >= 2, (label, metrics)
        assert metrics["windowCount"] >= 2, (label, metrics)
        assert metrics["windowIcons"] == ["", ""], (label, metrics)
        assert metrics["nonSessionWithSessionTab"] == 0, (label, metrics)
        assert "tabber-active-session" in metrics["active"]["rowClass"], (label, metrics)
        assert "active" in metrics["active"]["tabClass"], (label, metrics)
        assert metrics["active"]["ariaCurrent"] == "true", (label, metrics)
        assert metrics["active"]["ariaExpanded"] == "true", (label, metrics)
        assert metrics["active"]["iconText"] == "▾", (label, metrics)
        assert "tabber-active-session" not in metrics["inactive"]["rowClass"], (label, metrics)
        assert "active" not in metrics["inactive"]["tabClass"], (label, metrics)
        assert metrics["inactive"]["ariaCurrent"] == "", (label, metrics)
        assert metrics["active"]["tabBg"] == metrics["active"]["expectedActiveBg"], (label, metrics)
        assert metrics["inactive"]["tabBg"] == metrics["inactive"]["expectedInactiveBg"], (label, metrics)
        assert metrics["active"]["tabBg"] != metrics["active"]["tabColor"], (label, metrics)
        assert metrics["inactive"]["tabBg"] != metrics["inactive"]["tabColor"], (label, metrics)
        if theme_class == "theme-light":
            assert metrics["active"]["tabColor"] == metrics["expectedActiveText"], (label, metrics)
            assert metrics["active"]["descriptionColor"] == metrics["active"]["tabColor"], (label, metrics)
            assert metrics["inactive"]["tabColor"] == metrics["expectedMutedText"], (label, metrics)
            assert metrics["inactive"]["descriptionColor"] == metrics["inactive"]["tabColor"], (label, metrics)
            assert metrics["activeWindowTextColor"] == metrics["expectedText"], (label, metrics)
        assert metrics["active"]["tab"]["height"] >= 16, (label, metrics)
        assert metrics["active"]["tabRadius"] == "6px", (label, metrics)
        assert metrics["active"]["dateDisplay"] != "none", (label, metrics)
        assert metrics["active"]["dateWidth"] > 0, (label, metrics)
        assert metrics["active"]["dateText"], (label, metrics)
        assert abs(metrics["active"]["tab"]["width"] - metrics["inactive"]["tab"]["width"]) <= 1, (label, metrics)
        assert metrics["active"]["icon"]["right"] <= metrics["active"]["tab"]["left"] + 1, (label, metrics)
        assert metrics["active"]["tab"]["right"] <= metrics["active"]["date"]["left"] + 1, (label, metrics)
        assert metrics["active"]["name"]["left"] >= metrics["active"]["tab"]["left"] - 1, (label, metrics)
        assert metrics["active"]["description"]["right"] <= metrics["active"]["tab"]["right"] + 1, (label, metrics)
        assert metrics["active"]["descriptionScrollWidth"] >= metrics["active"]["descriptionClientWidth"], (label, metrics)
        assert metrics["activeWindow"]["top"] >= metrics["active"]["row"]["bottom"] - 1, (label, metrics)
        screenshot = browser_screenshot_rgb(browser)
        assert screenshot.size[0] >= window_width - 20, (label, screenshot.size)
        assert screenshot.getbbox() is not None, label


def test_tabber_session_tab_popover_uses_normal_tab_surface(browser, tmp_path):
    page = tmp_path / "tabber-session-popover-surface.html"
    page.write_text(
        page_html(
            """
            <script>document.body.className = 'theme-dark';</script>
            <section class="fixture-tabs">
              <button id="normal-tab" type="button" class="pane-tab session-popover-host popover-open" style="--pane-tab-popover-left: 24px; --pane-tab-popover-top: 80px;">
                <span class="pane-tab-core"><span class="session-button-name">8001</span></span>
                <div id="normal-popover" class="session-popover" role="tooltip"><div class="popover-title">Normal</div></div>
              </button>
            </section>
            <section class="fixture-tabber-panel file-explorer-changes-panel">
              <div class="file-tree" role="tree">
                <div class="file-tree-row tabber-row kind-dir expanded" data-tabber-type="session" data-tabber-session="8001" role="treeitem" aria-expanded="true" style="padding-left: 8px;">
                  <span class="file-tree-icon tabber-icon">▾</span>
                  <span class="file-tree-name">
                    <span id="tabber-tab" class="tabber-session-tab session-popover-host popover-open" data-tabber-session-chrome="shared" style="--pane-tab-popover-left: 24px; --pane-tab-popover-top: 180px;">
                      <span class="pane-tab-core"><span class="session-button-name">8001</span></span>
                      <div id="tabber-popover" class="session-popover" role="tooltip"><div class="popover-title">Tabber</div></div>
                    </span>
                  </span>
                  <span class="file-tree-date">now</span>
                </div>
              </div>
            </section>
            """,
            extra_css="""
              body { margin: 0; padding: 24px; display: block; min-height: 480px; background: var(--bg); color: var(--text); }
              .fixture-tabs { margin-bottom: 56px; }
              .fixture-tabber-panel { width: 420px; }
            """,
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const read = id => {
          const popover = document.getElementById(id);
          const style = getComputedStyle(popover);
          const rect = popover.getBoundingClientRect();
          return {
            position: style.position,
            visibility: style.visibility,
            opacity: style.opacity,
            pointerEvents: style.pointerEvents,
            zIndex: style.zIndex,
            width: Math.round(rect.width),
            maxWidth: style.maxWidth,
            maxHeight: style.maxHeight,
            boxShadow: style.boxShadow,
            borderRadius: style.borderTopLeftRadius,
            transform: style.transform,
          };
        };
        return {
          normal: read('normal-popover'),
          tabber: read('tabber-popover'),
          tabberOpen: document.getElementById('tabber-tab').classList.contains('popover-open'),
          normalOpen: document.getElementById('normal-tab').classList.contains('popover-open'),
        };
        """
    )
    assert metrics["normalOpen"] is True
    assert metrics["tabberOpen"] is True
    assert metrics["normal"]["position"] == "fixed", metrics
    assert metrics["tabber"]["position"] == metrics["normal"]["position"], metrics
    assert metrics["tabber"]["visibility"] == metrics["normal"]["visibility"] == "visible", metrics
    assert metrics["tabber"]["opacity"] == metrics["normal"]["opacity"] == "1", metrics
    assert metrics["tabber"]["pointerEvents"] == metrics["normal"]["pointerEvents"] == "auto", metrics
    assert metrics["tabber"]["zIndex"] == metrics["normal"]["zIndex"], metrics
    assert metrics["tabber"]["width"] == metrics["normal"]["width"], metrics
    assert metrics["tabber"]["maxWidth"] == metrics["normal"]["maxWidth"], metrics
    assert metrics["tabber"]["maxHeight"] == metrics["normal"]["maxHeight"], metrics
    assert metrics["tabber"]["boxShadow"] == metrics["normal"]["boxShadow"], metrics
    assert metrics["tabber"]["borderRadius"] == metrics["normal"]["borderRadius"], metrics
    assert metrics["tabber"]["transform"] == metrics["normal"]["transform"], metrics


def test_tabber_live_rows_use_custom_hover_without_native_titles(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=__files__,1&layout=row@34(left,right)&tabs=left:__files__;right:1",
        sessions=["1"],
        file_explorer_open_intent="1",
        transcript_sessions={
            "1": {
                "current_path": "/home/test/yolomux.dev8001",
                "git_root": "/home/test/yolomux.dev8001",
                "branch": "hover-fix",
                "panes": [
                    {
                        "window": "0",
                        "pane": "0",
                        "window_name": "claude",
                        "process_label": "claude",
                        "command": "claude",
                        "window_active": True,
                        "active": True,
                        "pid": 12345,
                    },
                ],
            },
        },
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof setFileExplorerMode === 'function'
              && document.querySelector('#panel-__files__ .file-explorer-mode-toggle') !== null;
            """
        )
    )
    browser.execute_script("setFileExplorerMode('tabber', {force: true});")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelectorAll('.file-tree-row[data-tabber-type]').length >= 2"
        )
    )
    metrics = browser.execute_script(
        """
        const rows = Array.from(document.querySelectorAll('.file-tree-row[data-tabber-type]'));
        const visibleChromeTitles = rows.flatMap(row => Array.from(row.querySelectorAll('[title]'))
          .filter(node => !node.closest('.session-popover'))
          .map(node => node.outerHTML.slice(0, 220)));
        const sessionTab = document.querySelector('.file-tree-row[data-tabber-type="session"] .tabber-session-tab');
        const customPopover = sessionTab?.querySelector(':scope > .session-popover');
        return {
          rowCount: rows.length,
          rowTitles: rows.map(row => row.getAttribute('title') || ''),
          dataTitles: rows.map(row => row.dataset.tabberTitle || ''),
          visibleChromeTitles,
          sessionTabHasTitle: sessionTab?.hasAttribute('title') || false,
          customPopoverPresent: !!customPopover,
          customPopoverRole: customPopover?.getAttribute('role') || '',
        };
        """
    )
    assert metrics["rowCount"] >= 2, metrics
    assert all(title == "" for title in metrics["rowTitles"]), metrics
    assert any(title for title in metrics["dataTitles"]), metrics
    assert metrics["visibleChromeTitles"] == [], metrics
    assert metrics["sessionTabHasTitle"] is False, metrics
    assert metrics["customPopoverPresent"] is True, metrics
    assert metrics["customPopoverRole"] == "tooltip", metrics
    browser.execute_script("renderAutoApproveButtons();")
    live_sync_metrics = browser.execute_script(
        """
        const visibleChromeTitles = Array.from(document.querySelectorAll('.file-tree-row[data-tabber-type] [title]'))
          .filter(node => !node.closest('.session-popover'))
          .map(node => node.outerHTML.slice(0, 220));
        const tabberYoloControls = Array.from(document.querySelectorAll('.tabber-session-tab [data-yolo-session]')).map(node => ({
          title: node.getAttribute('title') || '',
          ariaLabel: node.getAttribute('aria-label') || '',
          pressed: node.getAttribute('aria-pressed') || '',
        }));
        return {visibleChromeTitles, tabberYoloControls};
        """
    )
    assert live_sync_metrics["visibleChromeTitles"] == [], live_sync_metrics
    assert live_sync_metrics["tabberYoloControls"], live_sync_metrics
    assert all(item["title"] == "" for item in live_sync_metrics["tabberYoloControls"]), live_sync_metrics
    assert all(item["ariaLabel"] for item in live_sync_metrics["tabberYoloControls"]), live_sync_metrics
    browser.execute_script(
        """
        popoverHideDelayMs = 120;
        window.__tabberSessionTabBeforeRefresh = document.querySelector('.file-tree-row[data-tabber-type="session"] .tabber-session-tab');
        window.__tabberPopoverBeforeRefresh = window.__tabberSessionTabBeforeRefresh?.querySelector(':scope > .session-popover');
        window.__tabberSessionTabBeforeRefresh.classList.add('popover-open');
        window.__tabberSessionTabBeforeRefresh.dataset.popoverHoverState = 'open';
        window.__tabberPopoverBeforeRefresh.classList.add('popover-open');
        refreshTabberPanels();
        """
    )
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        setTimeout(done, 260);
        """
    )
    hover_metrics = browser.execute_script(
        """
        const tab = document.querySelector('.file-tree-row[data-tabber-type="session"] .tabber-session-tab');
        const visiblePopovers = Array.from(document.querySelectorAll('.tabber-session-tab.popover-open > .session-popover')).filter(popover => {
          const style = getComputedStyle(popover);
          const rect = popover.getBoundingClientRect();
          return style.visibility === 'visible' && Number.parseFloat(style.opacity) > 0.9 && rect.width > 100 && rect.height > 40;
        });
        const visibleChromeTitles = Array.from(document.querySelectorAll('.file-tree-row[data-tabber-type] [title]'))
          .filter(node => !node.closest('.session-popover'))
          .map(node => node.outerHTML.slice(0, 220));
        return {
          sameTab: tab === window.__tabberSessionTabBeforeRefresh,
          samePopover: visiblePopovers[0] === window.__tabberPopoverBeforeRefresh,
          visiblePopoverCount: visiblePopovers.length,
          hoverState: tab?.dataset?.popoverHoverState || '',
          tabOpen: tab?.classList?.contains('popover-open') || false,
          visibleChromeTitles,
        };
        """
    )
    assert hover_metrics["sameTab"] is True, hover_metrics
    assert hover_metrics["samePopover"] is True, hover_metrics
    assert hover_metrics["visiblePopoverCount"] == 1, hover_metrics
    assert hover_metrics["hoverState"] == "open", hover_metrics
    assert hover_metrics["tabOpen"] is True, hover_metrics
    assert hover_metrics["visibleChromeTitles"] == [], hover_metrics


def test_generated_app_boots_live_runtime_without_browser_errors(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return window.__terminalOpened >= 1 && document.querySelector('#panel-1 .terminal .xterm') !== null"
        )
    )
    metrics = browser.execute_script(
        """
        return {
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
          fetchPaths: window.__bootFetches.map(item => `${item.method} ${item.path}`),
          sockets: window.__bootSockets,
          menuButtons: Array.from(document.querySelectorAll('.app-menu')).map(menu => {
            const button = menu.querySelector(':scope > .app-menu-button');
            const badge = button?.querySelector('.app-menu-button-badge');
            const label = Array.from(button?.childNodes || [])
              .filter(node => node.nodeType === Node.TEXT_NODE)
              .map(node => node.textContent)
              .join('')
              .trim();
            return {label, badge: badge?.textContent.trim() || ''};
          }),
          panelCount: document.querySelectorAll('.panel').length,
          paneTabCount: document.querySelectorAll('.pane-tab').length,
          panelVisible: document.querySelector('#panel-1')?.isConnected === true,
          notifyActive: document.getElementById('notifyToggle')?.classList.contains('active') === true,
          status: document.getElementById('status').textContent,
          terminalText: document.querySelector('#panel-1 .terminal .xterm')?.textContent || '',
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert "GET /api/notify" in metrics["fetchPaths"]
    assert "GET /api/auto-approve" in metrics["fetchPaths"]
    assert metrics["fetchPaths"].count("POST /api/ensure-session") <= 1
    assert "GET /api/session-metadata" in metrics["fetchPaths"]
    assert "GET /api/ping" in metrics["fetchPaths"]
    assert any("/ws?session=1" in url for url in metrics["sockets"])
    assert {"File", "View", "tmux", "Tabs", "Help"}.issubset(
        {button["label"] for button in metrics["menuButtons"]}
    )
    assert any(
        button["label"] == "Tabs" and button["badge"] == "0"
        for button in metrics["menuButtons"]
    )
    assert metrics["panelCount"] >= 1
    assert metrics["paneTabCount"] >= 1
    assert metrics["panelVisible"]
    assert metrics["notifyActive"] is False
    assert metrics["terminalText"] == "fake terminal"


def test_terminal_visible_selection_cleanup_clears_browser_and_xterm_state(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof clearTerminalVisibleSelection === 'function'
              && typeof terminalVisibleSelectionState === 'function'
              && terminals.get('1')?.term
              && document.querySelector('#term-1 .xterm');
            """
        )
    )
    metrics = browser.execute_script(
        """
        const container = document.getElementById('term-1');
        const xterm = container.querySelector('.xterm');
        xterm.textContent = 'browser selected terminal text';
        const range = document.createRange();
        range.selectNodeContents(xterm);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        const item = terminals.get('1');
        window.__xtermClearCount = 0;
        item.term.getSelection = () => 'xterm selected terminal text';
        item.term.clearSelection = () => { window.__xtermClearCount += 1; };
        rememberTerminalAppClipboardText('1', 'osc52 terminal text');
        const before = terminalVisibleSelectionState('1', item.term, container);
        const result = clearTerminalVisibleSelection('1', item.term, container, 'selenium-test');
        const after = terminalVisibleSelectionState('1', item.term, container);
        return {
          before,
          result,
          after,
          browserSelection: window.getSelection().toString(),
          xtermClearCount: window.__xtermClearCount,
        };
        """
    )
    assert metrics["before"]["browserChars"] == len("browser selected terminal text")
    assert metrics["before"]["xtermChars"] == len("xterm selected terminal text")
    assert metrics["before"]["recentOsc52Chars"] == len("osc52 terminal text")
    assert metrics["result"]["browserCleared"] is True
    assert metrics["browserSelection"] == ""
    assert metrics["xtermClearCount"] == 1
    assert metrics["after"]["browserChars"] == 0


def test_terminal_file_reference_underlines_are_visible_and_hover_subtle(browser, tmp_path):
    page = tmp_path / "terminal-file-reference-underlines.html"
    page.write_text(page_html("""
      <section id="dark-terminal" class="terminal" data-terminal-theme="dark">
        <div class="xterm">
          <div class="xterm-rows">
            <span id="dark-link" style="color: rgb(103, 232, 249); text-decoration: underline;">tests/editor_preview.test.js</span>
          </div>
        </div>
        <div class="terminal-file-link-underlines" aria-hidden="true">
          <div id="dark-rest" class="terminal-file-link-underline" style="left: 64px; top: 30px; width: 264px;"></div>
          <div id="dark-hover" class="terminal-file-link-underline terminal-file-link-underline--hover" style="left: 64px; top: 54px; width: 264px;"></div>
        </div>
      </section>
      <section id="light-terminal" class="terminal" data-terminal-theme="light">
        <div class="xterm">
          <div class="xterm-rows">
            <span id="light-link" style="color: rgb(2, 132, 199); text-decoration-line: underline;">tests/editor_preview.test.js</span>
          </div>
        </div>
        <div class="terminal-file-link-underlines" aria-hidden="true">
          <div id="light-rest" class="terminal-file-link-underline" style="left: 64px; top: 30px; width: 264px;"></div>
          <div id="light-hover" class="terminal-file-link-underline terminal-file-link-underline--hover" style="left: 64px; top: 54px; width: 264px;"></div>
        </div>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 24px; background: #111827; }
      .terminal {
        width: 420px;
        height: 72px;
        margin: 0 0 20px;
        font: 20px/24px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      }
      #dark-terminal { background: #111827; color: #d1d5db; }
      #light-terminal { background: #ffffff; color: #111827; }
      .xterm-rows { position: absolute; inset: 8px 12px; }
    """), encoding="utf-8")
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const line = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {
            borderBottomColor: style.borderBottomColor,
            borderBottomWidth: style.borderBottomWidth,
          };
        };
        const link = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {
            color: style.color,
            textDecorationColor: style.textDecorationColor,
            textDecorationThickness: style.textDecorationThickness,
          };
        };
        const layer = getComputedStyle(document.querySelector('.terminal-file-link-underlines'));
        return {
          darkRest: line('dark-rest'),
          darkHover: line('dark-hover'),
          lightRest: line('light-rest'),
          lightHover: line('light-hover'),
          darkLink: link('dark-link'),
          lightLink: link('light-link'),
          layerPointerEvents: layer.pointerEvents,
          layerZIndex: layer.zIndex,
        };
        """
    )
    assert metrics["darkRest"]["borderBottomWidth"] == "1px", metrics
    assert metrics["darkHover"]["borderBottomWidth"] == "1px", metrics
    assert metrics["lightRest"]["borderBottomWidth"] == "1px", metrics
    assert metrics["lightHover"]["borderBottomWidth"] == "1px", metrics
    assert metrics["darkRest"]["borderBottomColor"] == "rgba(125, 211, 252, 0.5)", metrics
    assert metrics["darkHover"]["borderBottomColor"] == "rgba(125, 211, 252, 0.6)", metrics
    assert metrics["lightRest"]["borderBottomColor"] == "rgba(3, 105, 161, 0.48)", metrics
    assert metrics["lightHover"]["borderBottomColor"] == "rgba(3, 105, 161, 0.58)", metrics
    assert metrics["darkLink"]["textDecorationThickness"] == "1px", metrics
    assert metrics["lightLink"]["textDecorationThickness"] == "1px", metrics
    assert metrics["darkLink"]["textDecorationColor"] == metrics["darkLink"]["color"], metrics
    assert metrics["lightLink"]["textDecorationColor"] == metrics["lightLink"]["color"], metrics
    assert metrics["layerPointerEvents"] == "none", metrics
    assert int(metrics["layerZIndex"]) > 0, metrics


def test_live_app_menu_dropdowns_open_switch_and_expose_hover_state(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return window.__terminalOpened >= 1 && document.querySelectorAll('.app-menu-button').length >= 5"
        )
    )

    def menu_metrics(menu_id):
        return browser.execute_script(
            """
            const menuId = arguments[0];
            const wrapper = document.querySelector(`.app-menu[data-app-menu="${menuId}"]`);
            const button = wrapper?.querySelector?.(':scope > .app-menu-button');
            const popover = wrapper?.querySelector?.(':scope > .app-menu-popover');
            const rect = popover?.getBoundingClientRect?.();
            const style = popover ? getComputedStyle(popover) : null;
            const commands = Array.from(popover?.querySelectorAll?.('.app-menu-command') || []);
            const activeCommands = commands.filter(command => command.classList.contains('share-mirror-active'));
            const openIds = Array.from(document.querySelectorAll('.app-menu.open')).map(menu => menu.dataset.appMenu || '');
            return {
              exists: Boolean(wrapper && button && popover),
              open: wrapper?.classList?.contains('open') || false,
              openIds,
              expanded: button?.getAttribute?.('aria-expanded') || '',
              visible: Boolean(popover && wrapper?.classList?.contains('open') && style.display !== 'none' && style.visibility !== 'hidden' && Number.parseFloat(style.opacity || '1') > 0.9 && rect.width > 20 && rect.height > 20),
              rect: rect ? {left: Math.round(rect.left), top: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height)} : null,
              commandCount: commands.length,
              activeCommandCount: activeCommands.length,
              firstCommand: commands[0]?.textContent?.replace(/\\s+/g, ' ').trim() || '',
              errors: window.__bootErrors || [],
              rejections: window.__bootRejections || [],
            };
            """,
            menu_id,
        )

    for menu_id in ["file", "view", "tmux", "tabs", "help"]:
        browser.find_element("css selector", f'.app-menu[data-app-menu="{menu_id}"] > .app-menu-button').click()
        metrics = WebDriverWait(browser, 5).until(
            lambda _driver: (state if (state := menu_metrics(menu_id))["visible"] and state["commandCount"] > 0 else False)
        )
        assert metrics["exists"] is True, metrics
        assert metrics["open"] is True, metrics
        assert metrics["openIds"] == [menu_id], metrics
        assert metrics["expanded"] == "true", metrics
        assert metrics["rect"]["width"] >= 80, metrics
        assert metrics["rect"]["height"] >= 24, metrics
        assert metrics["firstCommand"], metrics
        assert metrics["errors"] == [], metrics
        assert metrics["rejections"] == [], metrics

        first_command = browser.find_element("css selector", f'.app-menu[data-app-menu="{menu_id}"] > .app-menu-popover .app-menu-command:not([disabled])')
        ActionChains(browser).move_to_element(first_command).perform()
        hover = WebDriverWait(browser, 5).until(
            lambda _driver: (state if (state := menu_metrics(menu_id))["activeCommandCount"] >= 1 else False)
        )
        assert hover["activeCommandCount"] >= 1, hover

    browser.find_element("css selector", '.app-menu[data-app-menu="file"] > .app-menu-button').click()
    ActionChains(browser).move_to_element(browser.find_element("css selector", '.app-menu[data-app-menu="view"] > .app-menu-button')).perform()
    switched = WebDriverWait(browser, 5).until(
        lambda _driver: (state if (state := menu_metrics("view"))["visible"] else False)
    )
    assert switched["openIds"] == ["view"], switched

    browser.find_element("css selector", "#panel-1").click()
    closed = WebDriverWait(browser, 5).until(
        lambda _driver: (state if not (state := menu_metrics("view"))["open"] else False)
    )
    assert closed["openIds"] == [], closed


def test_client_events_ready_refetches_yolo_marker_after_reconnect(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        sessions=["1"],
        auto_approve_payload={
            "session_order": ["1"],
            "sessions": {"1": {"target": "1", "enabled": False, "last_action": "off", "screen": {"key": "idle"}}},
            "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
        },
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return window.__terminalOpened >= 1 && document.querySelector('[data-yolo-session=\"1\"]') !== null"
        )
    )
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const markerBefore = document.querySelector('[data-yolo-session="1"]');
          const source = (window.__eventSources || []).find(item => item.url === '/api/client-events');
          if (!markerBefore || !source) return {error: 'missing marker or client-events source'};
          const beforeWorking = markerBefore.classList.contains('working');
          window.__fixtureAutoApprovePayload = {
            session_order: ['1'],
            sessions: {'1': {target: '1', enabled: false, last_action: 'off', screen: {key: 'working'}}},
            rules: {path: '/home/test/.config/yolomux/yolo-rules.yaml', source: 'default', rules: [], errors: []},
          };
          clientEventsConnected = false;
          source.emit('ready');
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 90; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => document.querySelector('[data-yolo-session="1"]')?.classList.contains('working'));
          const markerAfter = document.querySelector('[data-yolo-session="1"]');
          return {
            beforeWorking,
            ready,
            connected: clientEventsConnected,
            className: markerAfter?.className || '',
            autoApproveFetches: window.__bootFetches.filter(item => item.path === '/api/auto-approve').length,
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in result, result
    assert result["beforeWorking"] is False, result
    assert result["ready"] is True, result
    assert result["connected"] is True, result
    assert result["autoApproveFetches"] >= 2, result
    assert result["errors"] == []
    assert result["rejections"] == []


def test_preferences_scroll_defers_passive_rerender(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof selectSession === 'function' && window.__terminalOpened >= 1")
    )
    opened = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        selectSession('__prefs__').then(
          () => requestAnimationFrame(() => done({ok: true})),
          error => done({ok: false, error: String(error)})
        );
        """
    )
    assert opened["ok"], opened
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('.preferences-scroll') !== null")
    )
    metrics = browser.execute_script(
        """
        const scroller = document.querySelector('.preferences-scroll');
        scroller.scrollTop = 60;
        scroller.dispatchEvent(new WheelEvent('wheel', {deltaY: 120, bubbles: true}));
        renderPreferencesPanels();
        const afterPassive = document.querySelector('.preferences-scroll');
        renderPreferencesPanels({force: true});
        const afterForced = document.querySelector('.preferences-scroll');
        return {
          passiveKeptScroller: afterPassive === scroller,
          forcedReplacedScroller: afterForced !== afterPassive,
          scrollTop: afterPassive.scrollTop,
          bodyHtml: document.querySelector('.preferences-body')?.innerHTML || '',
        };
        """
    )
    assert metrics["passiveKeptScroller"], metrics
    assert metrics["forcedReplacedScroller"], metrics
    assert "preferences-sections" in metrics["bodyHtml"]


def test_active_pane_ring_opacity_follows_preference(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof applySettingsPayload === 'function' && document.querySelector('#panel-1') !== null"
        )
    )
    metrics = browser.execute_script(
        """
        const applyOpacity = value => {
          applySettingsPayload({settings: {appearance: {pane_ring_opacity: value}}, defaults: {}, mtime_ns: value}, {force: true});
          const panel = document.querySelector('#panel-1');
          panel.classList.add('active-pane');
          const rootStyle = getComputedStyle(document.documentElement);
          const panelStyle = getComputedStyle(panel);
          const ringOwner = panel.closest('.dv-groupview');
          const ringStyle = ringOwner ? getComputedStyle(ringOwner, '::after') : panelStyle;
          return {
            activeOpacity: rootStyle.getPropertyValue('--pane-active-ring-opacity').trim(),
            normalOpacity: rootStyle.getPropertyValue('--pane-ring-opacity').trim(),
            borderColor: ringStyle.borderLeftColor,
          };
        };
        return {low: applyOpacity(5), defaultish: applyOpacity(75)};
        """
    )
    assert metrics["low"]["activeOpacity"] == "5%", metrics
    assert metrics["low"]["normalOpacity"] == "5%", metrics
    assert metrics["defaultish"]["activeOpacity"] == "75%", metrics
    assert metrics["low"]["borderColor"] != metrics["defaultish"]["borderColor"], metrics


def test_meta_arrow_walks_visible_pane_tabs_in_live_runtime(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1,2,__prefs__&layout=row@50(left,right)&tabs=left:files,1;right:__prefs__,2",
        sessions=["1", "2"],
    )
    wait_for_dockview(browser, min_tabs=4)
    wait_for_dockview_tab_geometry(browser, min_tabs=4, min_width=45)
    result = browser.execute_script(
        """
        const fireMetaArrow = key => {
          const event = new KeyboardEvent('keydown', {
            key,
            code: key,
            metaKey: true,
            ctrlKey: false,
            altKey: false,
            shiftKey: false,
            bubbles: true,
            cancelable: true,
          });
          (document.activeElement || window).dispatchEvent(event);
          return event.defaultPrevented;
        };
        activatePaneTab('left', fileExplorerItemId, {userInitiated: true});
        setFocusedPanelItem(fileExplorerItemId, {userInitiated: true});
        const firstPrevented = fireMetaArrow('ArrowRight');
        const afterFinderRight = {
          left: activeItemForSide('left'),
          right: activeItemForSide('right'),
          focus: visualActivePaneItem(),
        };
        const secondPrevented = fireMetaArrow('ArrowRight');
        const afterPaneSpill = {
          left: activeItemForSide('left'),
          right: activeItemForSide('right'),
          focus: visualActivePaneItem(),
        };
        const thirdPrevented = fireMetaArrow('ArrowLeft');
        const afterBack = {
          left: activeItemForSide('left'),
          right: activeItemForSide('right'),
          focus: visualActivePaneItem(),
        };
        const editor = document.createElement('div');
        editor.className = 'cm-editor';
        editor.tabIndex = 0;
        document.body.appendChild(editor);
        editor.focus();
        const blockedPrevented = fireMetaArrow('ArrowRight');
        return {
          firstPrevented,
          secondPrevented,
          thirdPrevented,
          blockedPrevented,
          afterFinderRight,
          afterPaneSpill,
          afterBack,
          finalLeft: activeItemForSide('left'),
          finalRight: activeItemForSide('right'),
        };
        """
    )
    assert result["firstPrevented"] is True, result
    assert result["afterFinderRight"] == {"left": "1", "right": "__prefs__", "focus": "1"}, result
    assert result["secondPrevented"] is True, result
    assert result["afterPaneSpill"] == {"left": "1", "right": "__prefs__", "focus": "__prefs__"}, result
    assert result["thirdPrevented"] is True, result
    assert result["afterBack"] == {"left": "1", "right": "__prefs__", "focus": "1"}, result
    assert result["blockedPrevented"] is False, result
    assert result["finalLeft"] == "1" and result["finalRight"] == "__prefs__", result


def test_active_color_radios_recolor_live_pane_chrome(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=__files__,1,__prefs__&layout=row@32(slot1,row@56(left,right))&tabs=slot1:__files__;left:1;right:__prefs__")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('input[type="radio"][data-setting-path="appearance.active_color"][value="blue"]') !== null
              && document.querySelector('.dockview-pane-tab[data-pane-tab="1"].active') !== null
              && document.querySelector('#panel-__files__ .file-explorer-mode-toggle[aria-pressed="true"]') !== null
              && document.querySelector('input[data-setting-path="appearance.inactive_pane_opacity"]') !== null
              && document.querySelector('input[data-setting-path="appearance.pane_ring_opacity"]') !== null
            """
        )
    )
    browser.execute_script(
        """
        const panel = document.querySelector('#panel-1');
        panel.classList.add('active-pane', 'focused-pane', 'typing-ready-pane', 'yolo-ready-pane');
        document.getElementById('tabMetaToggle')?.classList.add('active');
        const notify = document.getElementById('notifyToggle');
        notify?.classList.add('notify-toggle', 'active');
        const radio = document.querySelector('input[type="radio"][data-setting-path="appearance.active_color"][value="blue"]');
        radio.checked = true;
        radio.dispatchEvent(new Event('change', {bubbles: true}));
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return window.__settingsPayload?.settings?.appearance?.active_color === 'blue'
              && getComputedStyle(document.querySelector('.dockview-pane-tab[data-pane-tab="1"].active')).backgroundColor === 'rgb(59, 130, 246)';
            """
        )
    )
    metrics = browser.execute_script(
        """
        const rootStyle = getComputedStyle(document.documentElement);
        const bodyStyle = getComputedStyle(document.body);
        const tabStyle = getComputedStyle(document.querySelector('.dockview-pane-tab[data-pane-tab="1"].active'));
        const panelStyle = getComputedStyle(document.querySelector('#panel-1'));
        const prefsRange = document.querySelector('input[data-setting-path="appearance.inactive_pane_opacity"]');
        const ringRange = document.querySelector('input[data-setting-path="appearance.pane_ring_opacity"]');
        const radio = document.querySelector('input[data-setting-path="appearance.date_time_hour_cycle"]');
        const prefsScroll = document.querySelector('.preferences-scroll');
        const finderMode = document.querySelector('#panel-__files__ .file-explorer-mode-toggle[aria-pressed="true"]');
        const tabMeta = document.getElementById('tabMetaToggle');
        const notify = document.getElementById('notifyToggle');
        const brandYo = document.querySelector('.brand-title .brand-yolo');
        const mdProbe = document.createElement('div');
        mdProbe.className = 'markdown-body';
        mdProbe.innerHTML = '<h1>Probe</h1>';
        document.body.appendChild(mdProbe);
        const cmProbe = document.createElement('div');
        cmProbe.className = 'cm-content';
        cmProbe.innerHTML = '<span class="md-heading"># Probe</span>';
        document.body.appendChild(cmProbe);
        const yoloProbe = document.createElement('span');
        yoloProbe.className = 'session-yolo-marker active';
        yoloProbe.textContent = 'YO';
        document.body.appendChild(yoloProbe);
        const shortcutProbe = document.createElement('section');
        shortcutProbe.className = 'keyboard-shortcuts-section';
        shortcutProbe.innerHTML = '<h3>APP</h3>';
        document.body.appendChild(shortcutProbe);
        const activeSwatch = document.querySelector('input[type="radio"][data-setting-path="appearance.active_color"][value="blue"]').closest('.preferences-radio').querySelector('.preferences-radio-swatch');
        const activeSwatchLabel = activeSwatch.closest('.preferences-radio');
        const scrollProbe = document.createElement('div');
        scrollProbe.style.background = 'var(--pane-scrollbar-thumb-active)';
        document.body.appendChild(scrollProbe);
        const expectedScrollThumb = getComputedStyle(scrollProbe).backgroundColor;
        scrollProbe.style.background = 'var(--pane-scrollbar-thumb)';
        const expectedNeutralScrollThumb = getComputedStyle(scrollProbe).backgroundColor;
        scrollProbe.remove();
        const metrics = {
          markdownHeadingColor: getComputedStyle(mdProbe.querySelector('h1')).color,
          cmHeadingColor: getComputedStyle(cmProbe.querySelector('.md-heading')).color,
          yoloBg: getComputedStyle(yoloProbe).backgroundColor,
          yoloBorder: getComputedStyle(yoloProbe).borderTopColor,
          shortcutHeadingColor: getComputedStyle(shortcutProbe.querySelector('h3')).color,
          swatchDisplay: getComputedStyle(activeSwatchLabel).display,
          swatchRadius: getComputedStyle(activeSwatch).borderRadius,
        };
        mdProbe.remove();
        cmProbe.remove();
        yoloProbe.remove();
        shortcutProbe.remove();
        return {
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
          rootAccent: rootStyle.getPropertyValue('--active-accent').trim(),
          bodyAccent: bodyStyle.getPropertyValue('--active-accent').trim(),
          rootRgb: rootStyle.getPropertyValue('--active-accent-rgb').trim(),
          tabBg: tabStyle.backgroundColor,
          tabBorder: tabStyle.borderTopColor,
          panelBorder: panelStyle.borderTopColor,
          prefsRangeAccent: getComputedStyle(prefsRange).accentColor,
          ringRangeAccent: getComputedStyle(ringRange).accentColor,
          radioAccent: getComputedStyle(radio).accentColor,
          prefsScrollColor: getComputedStyle(prefsScroll).scrollbarColor,
          prefsScrollThumb: getComputedStyle(prefsScroll, '::-webkit-scrollbar-thumb').backgroundColor,
          expectedScrollThumb,
          expectedNeutralScrollThumb,
          finderModeBg: getComputedStyle(finderMode).backgroundColor,
          finderModeBorder: getComputedStyle(finderMode).borderTopColor,
          tabMetaBg: getComputedStyle(tabMeta).backgroundColor,
          tabMetaBorder: getComputedStyle(tabMeta).borderTopColor,
          notifyBg: getComputedStyle(notify).backgroundColor,
          brandYoBg: getComputedStyle(brandYo).backgroundColor,
          brandYoBorder: getComputedStyle(brandYo).borderTopColor,
          markdownHeadingColor: metrics.markdownHeadingColor,
          cmHeadingColor: metrics.cmHeadingColor,
          yoloBg: metrics.yoloBg,
          yoloBorder: metrics.yoloBorder,
          shortcutHeadingColor: metrics.shortcutHeadingColor,
          swatchDisplay: metrics.swatchDisplay,
          swatchRadius: metrics.swatchRadius,
          settingsPosts: window.__bootFetches.filter(item => item.method === 'POST' && item.path === '/api/settings').length,
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["rootAccent"] == "#3b82f6", metrics
    assert metrics["bodyAccent"] == "#3b82f6", metrics
    assert metrics["rootRgb"] == "59 130 246", metrics
    assert metrics["tabBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["tabBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["panelBorder"].startswith("color(srgb 0.231373 0.509804 0.964706 / 0.75)"), metrics
    assert metrics["prefsRangeAccent"] == "rgb(59, 130, 246)", metrics
    assert metrics["ringRangeAccent"] == "rgb(59, 130, 246)", metrics
    assert metrics["radioAccent"] == "rgb(59, 130, 246)", metrics
    assert metrics["expectedScrollThumb"] == "rgba(255, 234, 0, 0.88)", metrics
    assert metrics["prefsScrollColor"].startswith(metrics["expectedNeutralScrollThumb"]), metrics
    assert metrics["prefsScrollThumb"] == metrics["expectedNeutralScrollThumb"], metrics
    assert metrics["finderModeBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["finderModeBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["tabMetaBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["tabMetaBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["notifyBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["brandYoBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["brandYoBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["markdownHeadingColor"] == "rgb(59, 130, 246)", metrics
    assert metrics["cmHeadingColor"] == "rgb(59, 130, 246)", metrics
    assert metrics["yoloBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["yoloBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["shortcutHeadingColor"] == "rgb(59, 130, 246)", metrics
    assert metrics["swatchDisplay"] == "grid", metrics
    assert metrics["swatchRadius"] == "2px 0px 0px 2px", metrics
    assert metrics["settingsPosts"] >= 1, metrics
    browser.execute_script(
        """
        const panel = document.querySelector('#panel-__prefs__');
        panel?.classList.add('active-pane', 'focused-pane');
        panel?.style.setProperty('--pane-scrollbar-current-thumb', 'var(--pane-scrollbar-thumb-active)');
        """
    )
    WebDriverWait(browser, 2).until(
        lambda driver: driver.execute_script(
            "return getComputedStyle(document.querySelector('.preferences-scroll'), '::-webkit-scrollbar-thumb').backgroundColor"
        ) == metrics["expectedScrollThumb"]
    )
    browser.execute_script(
        """
        const panel = document.querySelector('#panel-__prefs__');
        panel?.classList.remove('active-pane', 'focused-pane');
        panel?.style.removeProperty('--pane-scrollbar-current-thumb');
        """
    )
    WebDriverWait(browser, 2).until(
        lambda driver: driver.execute_script(
            "return getComputedStyle(document.querySelector('.preferences-scroll'), '::-webkit-scrollbar-thumb').backgroundColor"
        ) == metrics["expectedNeutralScrollThumb"]
    )
    move_to_visible_panel(browser, "panel-1")
    WebDriverWait(browser, 2).until(
        lambda driver: driver.execute_script(
            "return getComputedStyle(document.querySelector('.preferences-scroll'), '::-webkit-scrollbar-thumb').backgroundColor"
        ) == metrics["expectedNeutralScrollThumb"]
    )
    browser.execute_script(
        """
        setFocusedPanelItem('1', {userInitiated: true});
        const radio = document.querySelector('input[type="radio"][data-setting-path="appearance.editor_cursor_color"][value="laser-lime"]');
        radio.checked = true;
        radio.dispatchEvent(new Event('change', {bubbles: true}));
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return window.__settingsPayload?.settings?.appearance?.editor_cursor_color === 'laser-lime'
              && getComputedStyle(document.documentElement).getPropertyValue('--active-terminal-cursor-rgb').trim() === '204 255 0'
              && terminals.get('1')?.term?.options?.theme?.cursor === '#ccff00';
            """
        )
    )
    cursor_metrics = browser.execute_script(
        """
        const probe = document.createElement('div');
        probe.style.background = 'var(--pane-scrollbar-thumb-active)';
        document.body.appendChild(probe);
        const activeThumb = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return {
          rootCursorRgb: getComputedStyle(document.documentElement).getPropertyValue('--active-terminal-cursor-rgb').trim(),
          terminalCursor: terminals.get('1')?.term?.options?.theme?.cursor || '',
          activeScrollbarThumb: activeThumb,
        };
        """
    )
    assert cursor_metrics["rootCursorRgb"] == "204 255 0", cursor_metrics
    assert cursor_metrics["terminalCursor"] == "#ccff00", cursor_metrics
    assert cursor_metrics["activeScrollbarThumb"] == "rgba(204, 255, 0, 0.88)", cursor_metrics
    browser.execute_script(
        """
        const radio = document.querySelector('input[type="radio"][data-setting-path="appearance.active_color"][value="yellow"]');
        radio.checked = true;
        radio.dispatchEvent(new Event('change', {bubbles: true}));
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const brandYo = document.querySelector('.brand-title .brand-yolo');
            return window.__settingsPayload?.settings?.appearance?.active_color === 'yellow'
              && getComputedStyle(brandYo).backgroundColor === 'rgb(234, 179, 8)'
              && getComputedStyle(brandYo).borderTopColor === 'rgb(234, 179, 8)';
            """
        )
    )


def test_info_and_preferences_scrollbars_inherit_shared_hover_state(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=__info__,__prefs__,1&layout=row@34(left,row@50(mid,right))&tabs=left:__info__;mid:__prefs__;right:1",
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.info-list') !== null && document.querySelector('.preferences-scroll') !== null"
        )
    )
    metrics = browser.execute_script(
        """
        const info = document.querySelector('.info-list');
        const prefs = document.querySelector('.preferences-scroll');
        info.insertAdjacentHTML('beforeend', '<div style="height: 900px"></div>');
        prefs.insertAdjacentHTML('beforeend', '<div style="height: 900px"></div>');
        const probe = document.createElement('div');
        probe.style.background = 'var(--pane-scrollbar-thumb)';
        document.body.appendChild(probe);
        const neutral = getComputedStyle(probe).backgroundColor;
        probe.style.background = 'var(--pane-scrollbar-thumb-active)';
        const accent = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return {
          neutral,
          accent,
          infoOverflow: info.scrollHeight > info.clientHeight,
          prefsOverflow: prefs.scrollHeight > prefs.clientHeight,
          infoThumb: getComputedStyle(info, '::-webkit-scrollbar-thumb').backgroundColor,
          prefsThumb: getComputedStyle(prefs, '::-webkit-scrollbar-thumb').backgroundColor,
        };
        """
    )
    assert metrics["infoOverflow"], metrics
    assert metrics["prefsOverflow"], metrics
    assert metrics["infoThumb"] == metrics["neutral"], metrics
    assert metrics["prefsThumb"] == metrics["neutral"], metrics

    def thumb(selector):
        return browser.execute_script(
            "return getComputedStyle(document.querySelector(arguments[0]), '::-webkit-scrollbar-thumb').backgroundColor",
            selector,
        )

    def wait_thumb(selector, expected):
        WebDriverWait(browser, 2).until(lambda _driver: thumb(selector) == expected)

    browser.execute_script("document.querySelector('.info-list')?.closest('.panel')?.classList.add('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".info-list")).perform()
    wait_thumb(".info-list", metrics["accent"])
    wait_thumb(".preferences-scroll", metrics["neutral"])

    browser.execute_script(
        """
        document.querySelector('.info-list')?.closest('.panel')?.classList.remove('active-pane', 'focused-pane');
        document.querySelector('.preferences-scroll')?.closest('.panel')?.classList.add('active-pane', 'focused-pane');
        """
    )
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".preferences-scroll")).perform()
    wait_thumb(".preferences-scroll", metrics["accent"])
    wait_thumb(".info-list", metrics["neutral"])

    browser.execute_script("document.querySelector('.preferences-scroll')?.closest('.panel')?.classList.remove('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".preferences-scroll")).perform()
    wait_thumb(".preferences-scroll", metrics["neutral"])

    ActionChains(browser).move_to_element(browser.find_element("css selector", ".topbar")).perform()
    wait_thumb(".info-list", metrics["neutral"])
    wait_thumb(".preferences-scroll", metrics["neutral"])


def test_info_scroll_preserves_immediate_parent_header(browser, tmp_path):
    page = tmp_path / "info-tree-sticky-parent.html"
    records = "\n".join(
        f"""
        <div class="info-tree-record info-tree-item{' info-tree-item-last' if index == 23 else ''}">
          <div class="info-tree-record-main">
            <div class="info-tree-field info-tree-field-tab"><span class="info-tree-field-label">Tab(tmux session):</span><span class="info-tree-field-value"><button type="button" class="info-tree-action-link">tab-{index}</button></span></div>
            <div class="info-tree-field info-tree-field-pr"><span class="info-tree-field-label">GitHub PR:</span><span class="info-tree-field-value"><a href="#">#1</a> PR description {index}</span></div>
            <div class="info-tree-field info-tree-field-updated"><span class="info-tree-field-label">updated:</span><span class="info-tree-field-value"><span class="info-tree-meta-updated">{index} days ago</span></span></div>
          </div>
        </div>
        """
        for index in range(24)
    )
    page.write_text(page_html(f"""
      <div class="info-tree-panel" style="width: 680px; height: 260px; display: grid; grid-template-rows: auto minmax(0, 1fr);">
        <div id="info-tree-actions" class="info-actions-bar info-tree-actions-bar">YO!info controls</div>
        <div class="info-pane">
          <div id="info-tree-scroller" class="info-list info-tree-list">
            <div class="info-tree">
              <details class="info-tree-group info-tree-item" data-info-dimension="path" data-info-depth="0" open>
                <summary id="path-summary">
                  <span class="info-tree-group-dimension">Path</span>
                  <span class="info-tree-group-label-line"><span class="info-tree-group-label">/repo/app</span><span class="info-tree-group-child-count">(2 branches)</span></span>
                </summary>
                <div class="info-tree-group-children">
                  <details class="info-tree-group info-tree-item info-tree-item-last" data-info-dimension="branch" data-info-depth="1" open>
                    <summary id="branch-summary">
                      <span class="info-tree-group-dimension">Git BRANCH:</span>
                      <span class="info-tree-group-label-line"><span class="info-tree-group-label">feature/context</span></span>
                    </summary>
                    <div class="info-tree-group-children">
                      <details class="info-tree-group info-tree-item info-tree-item-last" data-info-dimension="pr" data-info-depth="2" open>
                        <summary id="pr-summary">
                          <span class="info-tree-group-dimension">GitHub PR:</span>
                          <span class="info-tree-group-label-line"><span class="info-tree-group-label">#42 sticky parent</span></span>
                        </summary>
                        <div class="info-tree-group-children">{records}</div>
                      </details>
                    </div>
                  </details>
                </div>
              </details>
            </div>
          </div>
        </div>
      </div>
    """), encoding="utf-8")
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const scroller = document.getElementById('info-tree-scroller');
        const actions = document.getElementById('info-tree-actions');
        const infoPane = document.querySelector('.info-pane');
        const rootSummary = document.getElementById('path-summary');
        const branchSummary = document.getElementById('branch-summary');
        const prSummary = document.getElementById('pr-summary');
        const initialBranchTop = branchSummary.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
        scroller.scrollTop = scroller.scrollHeight;
        scroller.scrollTop = initialBranchTop + 90;
        return new Promise(resolve => requestAnimationFrame(() => {
          const scrollerRect = scroller.getBoundingClientRect();
          const actionsRect = actions.getBoundingClientRect();
          const rootRect = rootSummary.getBoundingClientRect();
          const branchRect = branchSummary.getBoundingClientRect();
          const prRect = prSummary.getBoundingClientRect();
          const summaryValueX = scrollerRect.left + Math.min(scrollerRect.width - 24, 210);
          const topElement = document.elementFromPoint(summaryValueX, scrollerRect.top + Math.min(12, rootRect.height / 2));
          const branchElement = document.elementFromPoint(summaryValueX, scrollerRect.top + rootRect.height + Math.min(12, branchRect.height / 2));
          const prElement = document.elementFromPoint(summaryValueX, scrollerRect.top + rootRect.height + branchRect.height + Math.min(12, prRect.height / 2));
          const rootStyle = getComputedStyle(rootSummary);
          const branchStyle = getComputedStyle(branchSummary);
          const prStyle = getComputedStyle(prSummary);
          const actionsStyle = getComputedStyle(actions);
          const maskStyle = getComputedStyle(infoPane, '::before');
          const actionElement = document.elementFromPoint(actionsRect.left + 110, actionsRect.bottom - Math.min(4, actionsRect.height / 2));
          const branchLabel = branchSummary.querySelector('.info-tree-group-label');
          const branchLabelRange = document.createRange();
          branchLabelRange.selectNodeContents(branchLabel);
          const branchLabelTextRect = branchLabelRange.getBoundingClientRect();
          const treeRect = document.querySelector('.info-tree').getBoundingClientRect();
          const records = [...document.querySelectorAll('.info-tree-record')];
          const recordStyle = getComputedStyle(records[0]);
          const recordLineStyle = getComputedStyle(records[0], '::after');
          const firstRecordRect = records[0].getBoundingClientRect();
          const secondRecordRect = records[1].getBoundingClientRect();
          const rootConnector = getComputedStyle(rootSummary, '::after');
          const branchConnector = getComputedStyle(branchSummary, '::after');
          const branchRowConnector = getComputedStyle(branchSummary.parentElement, '::before');
          resolve({
            overflow: scroller.scrollHeight > scroller.clientHeight,
            scrollTop: scroller.scrollTop,
            scrollerBelowActions: scrollerRect.top >= actionsRect.bottom - 1,
            rootTopDelta: rootRect.top - scrollerRect.top,
            rootHeight: rootRect.height,
            branchTopDelta: branchRect.top - scrollerRect.top,
            branchHeight: branchRect.height,
            branchBottom: branchRect.bottom,
            prTopDelta: prRect.top - scrollerRect.top,
            prHeight: prRect.height,
            prBottom: prRect.bottom,
            topText: topElement ? topElement.textContent : '',
            branchText: branchElement ? branchElement.textContent : '',
            prText: prElement ? prElement.textContent : '',
            rootPosition: rootStyle.position,
            branchPosition: branchStyle.position,
            prPosition: prStyle.position,
            branchAlignItems: branchStyle.alignItems,
            branchAlignContent: branchStyle.alignContent,
            rootBorder: rootStyle.borderTopWidth,
            branchBorder: branchStyle.borderTopWidth,
            prBorder: prStyle.borderTopWidth,
            branchTextTopGap: Math.round(branchLabelTextRect.top - branchRect.top),
            branchTextBottomGap: Math.round(branchRect.bottom - branchLabelTextRect.bottom),
            maskContent: maskStyle.content,
            maskPosition: maskStyle.position,
            maskHeight: maskStyle.height,
            maskBg: maskStyle.backgroundColor,
            maskPointerEvents: maskStyle.pointerEvents,
            actionsPosition: actionsStyle.position,
            actionsZ: Number.parseInt(actionsStyle.zIndex, 10),
            actionElementId: actionElement ? actionElement.id : '',
            actionText: actionElement ? actionElement.textContent : '',
            rootZ: Number.parseInt(rootStyle.zIndex, 10),
            branchZ: Number.parseInt(branchStyle.zIndex, 10),
            prZ: Number.parseInt(prStyle.zIndex, 10),
            treeTopDelta: treeRect.top - scrollerRect.top + scroller.scrollTop,
            recordBorderWidth: recordStyle.borderTopWidth,
            recordBorderColor: recordStyle.borderTopColor,
            recordShadow: recordStyle.boxShadow,
            recordLineTop: recordLineStyle.insetBlockStart,
            recordLineBottom: recordLineStyle.insetBlockEnd,
            recordGap: Math.round(secondRecordRect.top - firstRecordRect.bottom),
            rootConnectorContent: rootConnector.content,
            branchConnectorContent: branchConnector.content,
            branchConnectorBg: branchConnector.backgroundImage,
            branchConnectorColor: branchConnector.backgroundColor,
            branchConnectorWidth: branchConnector.width,
            branchConnectorHeight: branchConnector.height,
            branchRowConnectorContent: branchRowConnector.content,
          });
        }));
        """
    )
    assert metrics["overflow"], metrics
    assert metrics["scrollTop"] > 0, metrics
    assert metrics["scrollerBelowActions"], metrics
    assert metrics["rootPosition"] == "sticky", metrics
    assert metrics["branchPosition"] == "sticky", metrics
    assert metrics["prPosition"] == "sticky", metrics
    assert metrics["actionsPosition"] == "relative", metrics
    assert metrics["branchAlignItems"] == "center", metrics
    assert metrics["branchAlignContent"] == "center", metrics
    assert 0 <= metrics["rootTopDelta"] <= 6, metrics
    assert metrics["rootHeight"] <= 32, metrics
    assert abs(metrics["branchTopDelta"] - metrics["rootHeight"]) <= 4, metrics
    assert metrics["branchHeight"] <= 32, metrics
    assert metrics["branchBottom"] > metrics["branchTopDelta"], metrics
    assert abs(metrics["prTopDelta"] - metrics["rootHeight"] - metrics["branchHeight"]) <= 4, metrics
    assert metrics["prHeight"] <= 32, metrics
    assert metrics["prBottom"] > metrics["prTopDelta"], metrics
    assert "/repo/app" in metrics["topText"], metrics
    assert "feature/context" in metrics["branchText"], metrics
    assert "#42 sticky parent" in metrics["prText"], metrics
    sticky_text = "\n".join([metrics["topText"], metrics["branchText"], metrics["prText"]])
    assert "tab-" not in sticky_text, metrics
    assert "days ago" not in sticky_text, metrics
    assert metrics["rootBorder"] == "0px", metrics
    assert metrics["branchBorder"] == "0px", metrics
    assert metrics["prBorder"] == "0px", metrics
    assert abs(metrics["branchTextTopGap"] - metrics["branchTextBottomGap"]) <= 2, metrics
    assert metrics["maskContent"] in ('""', "''"), metrics
    assert metrics["maskPosition"] == "absolute", metrics
    assert metrics["maskHeight"] == "27px", metrics
    assert metrics["maskBg"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["maskPointerEvents"] == "none", metrics
    assert metrics["actionsZ"] > metrics["prZ"], metrics
    assert metrics["actionElementId"] == "info-tree-actions" or "YO!info controls" in metrics["actionText"], metrics
    assert metrics["branchZ"] > metrics["rootZ"], metrics
    assert metrics["prZ"] > metrics["branchZ"], metrics
    assert 0 <= metrics["treeTopDelta"] <= 6, metrics
    assert metrics["recordBorderWidth"] == "1px", metrics
    assert metrics["recordBorderColor"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["recordShadow"] == "none", metrics
    assert metrics["recordLineTop"] == "-1px", metrics
    assert metrics["recordLineBottom"] == "-1px", metrics
    assert metrics["recordGap"] == 0, metrics
    assert metrics["rootConnectorContent"] == "none", metrics
    assert metrics["branchConnectorContent"] in ('""', "''"), metrics
    assert metrics["branchConnectorBg"] == "none", metrics
    assert metrics["branchConnectorColor"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["recordBorderColor"] == metrics["branchConnectorColor"], metrics
    assert metrics["branchConnectorWidth"] == "11px", metrics
    assert metrics["branchConnectorHeight"] == "1px", metrics
    assert metrics["branchRowConnectorContent"] == "none", metrics


def test_info_scroll_top_mask_hides_clipped_leaf_text(browser, tmp_path):
    page = tmp_path / "info-tree-top-mask.html"
    page.write_text(page_html("""
      <div class="info-tree-panel" style="width: 760px; height: 190px; display: grid; grid-template-rows: auto minmax(0, 1fr);">
        <div class="info-actions-bar info-tree-actions-bar">YO!info controls</div>
        <div class="info-pane">
          <div id="info-tree-scroller" class="info-list info-tree-list">
            <div class="info-tree">
              <details class="info-tree-group info-tree-item" data-info-dimension="pr" data-info-depth="0" open>
                <summary id="previous-pr">
                  <span class="info-tree-group-dimension">GitHub PR:</span>
                  <span class="info-tree-group-label-line"><span class="info-tree-group-label">#81 previous group</span></span>
                </summary>
                <div class="info-tree-group-children">
                  <div id="previous-record" class="info-tree-record info-tree-item info-tree-item-last">
                    <div class="info-tree-record-main">
                      <div class="info-tree-field info-tree-field-path"><span class="info-tree-field-label">path:</span><span class="info-tree-field-value">/repo/previous</span></div>
                      <div class="info-tree-field info-tree-field-tab"><span class="info-tree-field-label">Tab(tmux session):</span><span class="info-tree-field-value"><span id="leak-sentinel" style="color: #80ff00; font: 900 18px/1 var(--mono-font);">LEAKGREENLEAKGREENLEAKGREEN</span></span></div>
                    </div>
                  </div>
                </div>
              </details>
              <details class="info-tree-group info-tree-item info-tree-item-last" data-info-dimension="pr" data-info-depth="0" open>
                <summary id="next-pr">
                  <span class="info-tree-group-dimension">GitHub PR:</span>
                  <span class="info-tree-group-label-line"><span class="info-tree-group-label">#80 next group</span></span>
                </summary>
                <div class="info-tree-group-children">
                  <div class="info-tree-record info-tree-item info-tree-item-last">
                    <div class="info-tree-record-main">
                      <div class="info-tree-field info-tree-field-path"><span class="info-tree-field-label">path:</span><span class="info-tree-field-value">/repo/next</span></div>
                    </div>
                  </div>
                  <div class="info-tree-record info-tree-item info-tree-item-last" style="min-height: 220px;">
                    <div class="info-tree-record-main">
                      <div class="info-tree-field info-tree-field-path"><span class="info-tree-field-label">path:</span><span class="info-tree-field-value">/repo/filler</span></div>
                    </div>
                  </div>
                </div>
              </details>
            </div>
          </div>
        </div>
      </div>
    """), encoding="utf-8")
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const scroller = document.getElementById('info-tree-scroller');
        const next = document.getElementById('next-pr');
        const sentinel = document.getElementById('leak-sentinel');
        const scrollerRectAtZero = scroller.getBoundingClientRect();
        const nextTopAtZero = next.getBoundingClientRect().top - scrollerRectAtZero.top + scroller.scrollTop;
        scroller.scrollTop = Math.max(0, nextTopAtZero - 30);
        return new Promise(resolve => requestAnimationFrame(() => {
          const firstScrollerRect = scroller.getBoundingClientRect();
          const firstSentinelRect = sentinel.getBoundingClientRect();
          scroller.scrollTop += firstSentinelRect.top - firstScrollerRect.top - 8;
          requestAnimationFrame(() => {
          const scrollerRect = scroller.getBoundingClientRect();
          const nextRect = next.getBoundingClientRect();
          const sentinelRect = sentinel.getBoundingClientRect();
          const maskStyle = getComputedStyle(document.querySelector('.info-pane'), '::before');
          resolve({
            dpr: window.devicePixelRatio || 1,
            scrollerRect: {
              left: scrollerRect.left,
              top: scrollerRect.top,
              right: scrollerRect.right,
              bottom: scrollerRect.bottom,
            },
            nextTopDelta: nextRect.top - scrollerRect.top,
            sentinelTopDelta: sentinelRect.top - scrollerRect.top,
            sentinelBottomDelta: sentinelRect.bottom - scrollerRect.top,
            maskHeight: Number.parseFloat(maskStyle.height),
            maskBg: maskStyle.backgroundColor,
          });
          });
        }));
        """
    )
    assert 0 <= metrics["sentinelTopDelta"] <= metrics["maskHeight"], metrics
    assert metrics["sentinelBottomDelta"] > 0, metrics
    assert metrics["nextTopDelta"] >= metrics["maskHeight"] - 2, metrics
    assert metrics["maskBg"] != "rgba(0, 0, 0, 0)", metrics

    image = browser_screenshot_rgb(browser)
    dpr = metrics["dpr"]
    rect = metrics["scrollerRect"]
    x0 = max(0, round((rect["left"] + 18) * dpr))
    x1 = min(image.width - 1, round((rect["right"] - 18) * dpr))
    y0 = max(0, round((rect["top"] + 2) * dpr))
    y1 = min(image.height - 1, round((rect["top"] + min(metrics["maskHeight"] - 3, metrics["nextTopDelta"] - 3)) * dpr))
    green_pixels = 0
    for y in range(y0, max(y0 + 1, y1), 2):
        for x in range(x0, max(x0 + 1, x1), 2):
            r, g, b = image.getpixel((x, y))[:3]
            if g >= 130 and g - r >= 50 and g - b >= 50:
                green_pixels += 1
    assert green_pixels == 0, {"greenPixels": green_pixels, **metrics}


@pytest.mark.parametrize("width, expected_rows", [(860, [3, 3]), (493, [1, 2, 2, 1])])
def test_pane_tabs_stay_within_panel(browser, tmp_path, width, expected_rows):
    # Tabs wrap to fit the panel at any width: the toolbar never overflows the panel, the rows wrap to the
    # expected counts, every tab stays within the panel's right edge, and the toolbar stays centered with no
    # gap below the tab head. (Was two near-identical width tests, at 860 and 493.)
    metrics = load_fixture(browser, tmp_path, width)
    assert metrics["toolbar"]["right"] <= metrics["panel"]["right"]
    assert [row["count"] for row in metrics["rows"]] == expected_rows
    assert all(tab["right"] <= metrics["panel"]["right"] for tab in metrics["tabs"])
    assert metrics["toolbarCenterDelta"] <= 2
    assert metrics["tabHeadBottomGap"] <= 2


def test_pane_tab_wide_layout_shows_compact_info_bar(browser, tmp_path):
    # At a comfortable width the first tab row shares the toolbar's row (sits left of it), lower rows stay
    # within the panel, and the Info Bar is a single compact strip (text shown, symbol hidden, tinted bg).
    metrics = load_fixture(browser, tmp_path, 860)
    first_row = metrics["rows"][0]
    assert max(first_row["rights"]) < metrics["toolbar"]["left"]
    lower_row_rights = [right for row in metrics["rows"][1:] for right in row["rights"]]
    assert max(lower_row_rights) <= metrics["panel"]["right"]
    assert metrics["detailRow"]["height"] <= 20
    assert metrics["hiddenTextDisplay"] != "none"
    assert metrics["hiddenSymbolDisplay"] == "none"
    assert metrics["detailBg"] != "rgb(18, 24, 35)"
    assert metrics["detailCloseRightGap"] <= 3


def test_pane_tab_active_accent_theming(browser, tmp_path):
    # The active pane tab + the pressed control tab share one --active-accent source (asserted as
    # relationships, not pinned greens, so the appearance.active_color picker can't break it); unpressed
    # controls share one neutral bg; theme-specific surfaces repaint on a theme switch while everything else
    # stays token-equal; and inactive-tab dir text always contrasts with its bg (no white-on-white).
    load_fixture(browser, tmp_path, 860)
    theme_metrics = browser.execute_script(
        """
        const originalPanel = document.querySelector('.panel.active-pane');
        const inactivePanel = originalPanel.cloneNode(true);
        inactivePanel.classList.remove('active-pane');
        inactivePanel.style.marginTop = '12px';
        document.body.appendChild(inactivePanel);
        const readMetrics = () => {
          const panel = document.querySelector('.panel.active-pane');
          const activeTab = panel.querySelector('.pane-tab.active');
          const inactiveActiveTab = inactivePanel.querySelector('.pane-tab.active');
          const inactiveTab = panel.querySelector('.pane-tab:not(.active)');
          const panelHead = panel.querySelector('.panel-head');
          const toolbarActive = panel.querySelector('.panel-head .tab.active:not(.auto-toggle)');
          const activeWindow = panel.querySelector('.tmux-window-button.active[data-window-agent]');
          const inactiveWindow = panel.querySelector('.tmux-window-button[data-window-agent]:not(.active)');
          const paneControl = panel.querySelector('.tabs .pane-minimize');
          const zoomControl = panel.querySelector('.tabs .pc-zoom');
          return {
            panelBorder: getComputedStyle(panel).borderTopColor,
            panelHeadBg: getComputedStyle(panelHead).backgroundColor,
            activeTabBg: getComputedStyle(activeTab).backgroundColor,
            activeTabColor: getComputedStyle(activeTab).color,
            activeTabShadow: getComputedStyle(activeTab).boxShadow,
            inactiveActiveTabBg: getComputedStyle(inactiveActiveTab).backgroundColor,
            inactiveActiveTabColor: getComputedStyle(inactiveActiveTab).color,
            inactiveActiveTabShadow: getComputedStyle(inactiveActiveTab).boxShadow,
            inactiveTabBg: getComputedStyle(inactiveTab).backgroundColor,
            inactiveTabBorder: getComputedStyle(inactiveTab).borderTopColor,
            inactiveDirColor: getComputedStyle(inactiveTab.querySelector('.session-button-dir') || inactiveTab).color,
            toolbarActiveBg: getComputedStyle(toolbarActive).backgroundColor,
            toolbarActiveBorder: getComputedStyle(toolbarActive).borderTopColor,
            activeWindowBg: getComputedStyle(activeWindow).backgroundColor,
            activeWindowBorder: getComputedStyle(activeWindow).borderTopColor,
            activeWindowColor: getComputedStyle(activeWindow).color,
            inactiveWindowBg: getComputedStyle(inactiveWindow).backgroundColor,
            paneControlBg: getComputedStyle(paneControl).backgroundColor,
            paneControlBorder: getComputedStyle(paneControl).borderTopColor,
            zoomControlBg: getComputedStyle(zoomControl).backgroundColor,
          };
        };
        const dark = readMetrics();
        document.body.classList.add('theme-light');
        return {dark, light: readMetrics()};
        """
    )
    assert theme_metrics["dark"]["panelHeadBg"].startswith("color(srgb")
    # The light chrome strip is a tinted (active-accent-derived) bar, NOT the neutral control bg — assert
    # the relationship, not a pinned green, so it survives the appearance.active_color picker.
    assert theme_metrics["light"]["panelHeadBg"] != theme_metrics["light"]["paneControlBg"]
    # Shared pane-chrome buttons (image 009): every UNPRESSED control is white (light) / near-black (dark)
    # via --pane-ctl-bg — including the expand "+" (formerly always-green). Only PRESSED/ACTIVE buttons go
    # green (asserted via toolbarActiveBg below). No per-button one-off colors.
    assert theme_metrics["dark"]["paneControlBg"] == "rgb(27, 36, 50)"
    assert theme_metrics["light"]["paneControlBg"] == "rgb(247, 249, 252)"
    assert theme_metrics["dark"]["zoomControlBg"] == "rgb(27, 36, 50)"      # "+" is NOT green when unpressed
    assert theme_metrics["light"]["zoomControlBg"] == "rgb(247, 249, 252)"
    assert theme_metrics["dark"]["zoomControlBg"] == theme_metrics["dark"]["paneControlBg"]  # all unpressed controls share one bg
    # The active control tab (the agent/"claude" pill) is PRESSED -> green, in both themes (shared rule).
    # The pressed/active control tab is the active accent (NOT a pinned green) — distinct from the
    # unpressed control bg in both themes, so the picker (Green/Blue/...) doesn't break the test.
    assert theme_metrics["dark"]["toolbarActiveBg"] != theme_metrics["dark"]["paneControlBg"]
    assert theme_metrics["light"]["toolbarActiveBg"] != theme_metrics["light"]["paneControlBg"]
    # the active-tab greens are tuned PER THEME so a theme switch visibly repaints the active
    # pane tab; the frame controls are also theme-specific now (image 043). Every OTHER surface stays
    # token-equal across themes.
    # inactiveTabBg is theme-specific now (images 003/004): light gets a very-light-green #e6f1dd while
    # dark keeps #285a2f, so it must NOT be required equal across themes.
    # toolbarActiveBg/Border are the PRESSED control tab's green, which is theme-specific (light #4f9e3a /
    # dark #86d600); Info Bar bg now follows --pane-bar-bg so it is theme-specific too.
    theme_specific = {"panelHeadBg", "activeTabBg", "activeTabColor", "inactiveActiveTabBg", "inactiveActiveTabColor", "inactiveTabBg", "inactiveTabBorder", "inactiveDirColor", "paneControlBg", "paneControlBorder", "zoomControlBg", "toolbarActiveBg", "toolbarActiveBorder", "activeWindowBg", "activeWindowBorder", "activeWindowColor", "inactiveWindowBg"}
    for key, value in theme_metrics["dark"].items():
        if key not in theme_specific:
            assert theme_metrics["light"][key] == value
    # The active pane tab shares the active accent with the pressed control tab (one --active-accent
    # source) and stands out from the unpressed control bg — true for any active-color preset.
    assert theme_metrics["dark"]["activeTabBg"] == theme_metrics["dark"]["toolbarActiveBg"]
    assert theme_metrics["light"]["activeTabBg"] == theme_metrics["light"]["toolbarActiveBg"]
    assert theme_metrics["dark"]["activeWindowBg"] == theme_metrics["dark"]["activeTabBg"]
    assert theme_metrics["light"]["activeWindowBg"] == theme_metrics["light"]["activeTabBg"]
    assert theme_metrics["light"]["activeWindowBg"] != theme_metrics["light"]["inactiveWindowBg"]
    assert theme_metrics["light"]["activeWindowColor"] != theme_metrics["light"]["activeWindowBg"]
    assert theme_metrics["dark"]["activeTabBg"] != theme_metrics["dark"]["paneControlBg"]
    assert theme_metrics["light"]["activeTabBg"] != theme_metrics["dark"]["activeTabBg"]
    assert theme_metrics["light"]["inactiveActiveTabBg"] != theme_metrics["dark"]["inactiveActiveTabBg"]
    # Active-tab text stays legible against its (theme-specific) accent in BOTH modes.
    assert theme_metrics["light"]["activeTabColor"] != theme_metrics["light"]["activeTabBg"]
    assert theme_metrics["dark"]["activeTabColor"] != theme_metrics["dark"]["activeTabBg"]
    assert theme_metrics["dark"]["activeTabShadow"] == "none"
    # images 003/004: an unfocused pane's active tab now uses the SAME full green as the focused pane's
    # active tab (no lightening) — the unfocused-active tokens are aliased to the focused ones.
    assert theme_metrics["dark"]["inactiveActiveTabBg"] == theme_metrics["dark"]["activeTabBg"]
    assert theme_metrics["dark"]["inactiveActiveTabShadow"] == "none"
    # REGRESSION GUARD (image 008): the inactive-tab branch/dir TEXT must contrast with the tab bg in BOTH
    # themes — i.e. NOT white-on-white. This is the check that was missing before: the prior browser test
    # measured tab BACKGROUNDS but never the nested .session-button-* TEXT color, so a near-white dir text
    # on a near-white light tab went uncaught. Compare relative luminance of text vs bg.
    def _lum(css_rgb):
        nums = [int(n) for n in re.findall(r"\d+", css_rgb)[:3]]
        return 0.2126 * nums[0] + 0.7152 * nums[1] + 0.0722 * nums[2]
    for th in ("light", "dark"):
        text_lum = _lum(theme_metrics[th]["inactiveDirColor"])
        bg_lum = _lum(theme_metrics[th]["inactiveTabBg"])
        assert abs(text_lum - bg_lum) > 80, (
            f"{th}: inactive-tab dir text ({theme_metrics[th]['inactiveDirColor']}) must contrast with the "
            f"tab bg ({theme_metrics[th]['inactiveTabBg']}) — not white-on-white"
        )


def test_split_pane_seam_is_a_compact_tile_divider(browser, tmp_path):
    load_split_seam_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const topPanel = document.getElementById('top-panel');
        const bottomPanel = document.getElementById('bottom-panel');
        const resizer = document.getElementById('split-resizer');
        const topRect = topPanel.getBoundingClientRect();
        const bottomRect = bottomPanel.getBoundingClientRect();
        const resizerRect = resizer.getBoundingClientRect();
        const topStyle = getComputedStyle(topPanel);
        const bottomStyle = getComputedStyle(bottomPanel);
        return {
          seamGap: bottomRect.top - topRect.bottom,
          resizerHeight: resizerRect.height,
          topBottomBorder: topStyle.borderBottomWidth,
          bottomTopBorder: bottomStyle.borderTopWidth,
          topBottomRadius: topStyle.borderBottomLeftRadius,
          bottomTopRadius: bottomStyle.borderTopLeftRadius,
        };
        """
    )
    assert metrics["resizerHeight"] <= 2
    assert metrics["seamGap"] <= 2.5
    assert metrics["topBottomBorder"] == "0px"
    assert metrics["bottomTopBorder"] == "0px"
    assert metrics["topBottomRadius"] == "0px"
    assert metrics["bottomTopRadius"] == "0px"


def test_tab_menu_rows_are_compact_for_many_tabs(browser, tmp_path):
    metrics = load_menu_fixture(browser, tmp_path)
    assert metrics["count"] == 30
    assert metrics["maxHeight"] <= 23
    assert metrics["maxStep"] <= 24
    assert metrics["firstTwentyFiveSpan"] <= 575
    assert metrics["width"] > 0
    assert metrics["width"] <= metrics["maxInlineSize"] + metrics["devicePixelRatio"]
    assert metrics["secondRowBorderTopColor"] != "rgba(0, 0, 0, 0)"
    assert metrics["scrollHeight"] <= 700


def test_topbar_uses_ui_font_size_and_compact_actions(browser, tmp_path):
    load_topbar_font_fixture(browser, tmp_path)
    topbar_metrics = browser.execute_script(
        """
        const menu = document.getElementById('menu-file');
        const action = document.getElementById('tabMetaToggle');
        const paneTab = document.querySelector('.pane-tab');
        const menuRect = menu.getBoundingClientRect();
        const actionRect = action.getBoundingClientRect();
        const paneTabRect = paneTab.getBoundingClientRect();
        return {
          menuFontSize: Number.parseFloat(getComputedStyle(menu).fontSize),
          menuHeight: menuRect.height,
          actionWidth: actionRect.width,
          actionHeight: actionRect.height,
          paneTabHeight: paneTabRect.height,
        };
        """
    )
    assert topbar_metrics["menuFontSize"] >= 17.5
    assert 23 <= topbar_metrics["menuHeight"] <= 25
    assert 22 <= topbar_metrics["paneTabHeight"] <= 24
    assert topbar_metrics["actionWidth"] <= 31
    assert topbar_metrics["actionHeight"] <= 31
    compact_metrics = browser.execute_script(
        """
        document.documentElement.style.setProperty('--ui-font-size', '13px');
        document.documentElement.style.setProperty('--tab-label-size', '13px');
        const action = document.getElementById('tabMetaToggle').getBoundingClientRect();
        const paneTab = document.querySelector('.pane-tab').getBoundingClientRect();
        return {actionWidth: action.width, actionHeight: action.height, paneTabHeight: paneTab.height};
        """
    )
    assert compact_metrics["actionWidth"] <= 21
    assert compact_metrics["actionHeight"] <= 21
    assert compact_metrics["paneTabHeight"] <= 21
    tiny_metrics = browser.execute_script(
        """
        document.documentElement.style.setProperty('--ui-font-size', '8px');
        document.documentElement.style.setProperty('--tab-label-size', '8px');
        const action = document.getElementById('tabMetaToggle').getBoundingClientRect();
        const paneTab = document.querySelector('.pane-tab').getBoundingClientRect();
        return {actionHeight: action.height, paneTabHeight: paneTab.height};
        """
    )
    assert tiny_metrics["actionHeight"] <= 18
    assert tiny_metrics["paneTabHeight"] <= 18


def test_active_pane_tab_container_lightens_in_dark_only(browser, tmp_path):
    load_fixture(browser, tmp_path, 860)
    metrics = browser.execute_script(
        """
        function colorFor(styleValue) {
          const probe = document.createElement('div');
          probe.style.position = 'absolute';
          probe.style.left = '-1000px';
          probe.style.top = '-1000px';
          probe.style.background = styleValue;
          document.body.appendChild(probe);
          const color = getComputedStyle(probe).backgroundColor;
          probe.remove();
          return color;
        }
        function brightness(color) {
          const nums = (color.match(/\\d+(?:\\.\\d+)?/g) || []).slice(0, 3).map(Number);
          if (color.startsWith('color(srgb')) return nums.reduce((sum, value) => sum + value * 255, 0);
          return nums[0] + nums[1] + nums[2];
        }
        document.body.classList.add('theme-dark');
        const head = document.querySelector('.panel-head');
        const darkStrip = colorFor('var(--pane-tab-strip-bg)');
        const darkHead = getComputedStyle(head).backgroundColor;
        document.body.classList.remove('theme-dark');
        document.body.classList.add('theme-light');
        const lightStrip = colorFor('var(--pane-tab-strip-bg)');
        const lightHead = getComputedStyle(head).backgroundColor;
        return {
          darkStrip,
          darkHead,
          darkStripBrightness: brightness(darkStrip),
          lightStrip,
          lightHead,
        };
        """
    )
    assert metrics["darkHead"] == metrics["darkStrip"], metrics
    assert metrics["darkStripBrightness"] > 0, metrics
    assert metrics["lightHead"] == metrics["lightStrip"], metrics


def test_pane_tab_strip_hover_token_is_removed():
    # The dark-only --pane-tab-strip-hover-bg was removed when the tab container + info bar were unified
    # onto one token. Cheap string guard against its reintroduction — no browser needed (P3 demotion).
    css = app_css()
    assert "--pane-tab-strip-hover-bg" not in css


def test_share_host_editor_snapshot_tracks_codemirror_cursor_after_typing(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof fileEditorItemFor === 'function'
              && typeof applyLayoutSlots === 'function'
              && typeof shareUiStateSnapshot === 'function'
              && document.querySelector('#grid') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          activeShares = [{
            token: 'share-token',
            shortId: 'share-token',
            mode: 'ro',
            scheme: 'http',
            session: '1',
            sessions: ['1'],
            viewers: 1,
            maxViewers: 5,
            expiresAt: Math.floor(Date.now() / 1000) + 600,
          }];
          const path = '/home/test/yolomux.dev/docs/DONE.md';
          const item = fileEditorItemFor(path);
          const content = [
            '# DONE',
            '',
            'First paragraph stays visible.',
            'Second paragraph receives the typed text.',
            'Third paragraph is only here to keep normal editor structure.',
          ].join('\\n');
          setFileState(path, {
            kind: 'text',
            content,
            original: content,
            dirty: false,
            language: 'markdown',
            gitRoot: '/home/test/yolomux.dev',
            gitTracked: true,
            gitHasHistory: true,
            gitHistory: [{ref: 'HEAD'}],
          });
          setFileEditorViewMode(path, 'edit', item);
          registerFileEditorLayoutItem(path, {item});
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([item], item);
          applyLayoutSlots(next, {focusSession: item, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 220; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => panelNodes.get(item)?._cmView?.scrollDOM);
          if (!ready) return {error: 'CodeMirror editor did not initialize', bootErrors: window.__bootErrors || [], bootRejections: window.__bootRejections || []};
          const panel = panelNodes.get(item);
          const view = panel._cmView;
          fileEditorViewState.set(item, {scrollTop: 0, scrollLeft: 0, anchor: 0, head: 0, scrollSnapshot: null});
          const insertAt = content.indexOf('receives');
          const insert = 'typed ';
          view.focus();
          view.dispatch({
            changes: {from: insertAt, to: insertAt, insert},
            selection: {anchor: insertAt + insert.length, head: insertAt + insert.length},
          });
          await frame();
          await frame();
          const cached = fileEditorViewState.get(item) || {};
          const snapshot = shareUiStateSnapshot();
          const modeEntry = (snapshot.editor?.modes || []).find(entry => entry.item === item || entry.path === path) || {};
          return {
            item,
            expectedAnchor: insertAt + insert.length,
            cachedAnchor: cached.anchor,
            cachedHead: cached.head,
            snapshotAnchor: modeEntry.viewState?.anchor,
            snapshotHead: modeEntry.viewState?.head,
            dirty: openFiles.get(path)?.dirty === true,
            sentSockets: window.__bootSockets || [],
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["dirty"] is True, metrics
    assert metrics["cachedAnchor"] == metrics["expectedAnchor"], metrics
    assert metrics["cachedHead"] == metrics["expectedAnchor"], metrics
    assert metrics["snapshotAnchor"] == metrics["expectedAnchor"], metrics
    assert metrics["snapshotHead"] == metrics["expectedAnchor"], metrics


def test_long_markdown_editor_scroll_survives_preferences_tab_roundtrip(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof fileEditorItemFor === 'function'
              && typeof applyLayoutSlots === 'function'
              && typeof createFileEditorPanel === 'function'
              && document.querySelector('#grid') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          const path = '/home/test/repo/2026.md';
          const item = fileEditorItemFor(path);
          const content = Array.from({length: 1400}, (_value, index) => `# Entry ${index + 1}\\n\\n- Work item ${index + 1} with enough text to produce normal Markdown editor rows.`).join('\\n');
          setFileState(path, {
            kind: 'text',
            content,
            original: content,
            dirty: false,
            language: 'markdown',
            gitRoot: '/home/test/repo',
            gitTracked: true,
            gitHasHistory: true,
            gitHistory: [{ref: 'HEAD'}],
          });
          setFileEditorViewMode(path, 'edit', item);
          registerFileEditorLayoutItem(path);
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([item, prefsItemId], item);
          applyLayoutSlots(next, {focusSession: item, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 220; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => {
            const panel = panelNodes.get(item);
            const scroller = panel?._cmView?.scrollDOM;
            return activeItemForSide('left') === item
              && panel?.isConnected
              && scroller
              && scroller.scrollHeight > scroller.clientHeight * 3;
          });
          if (!ready) {
            const panel = panelNodes.get(item);
            const scroller = panel?._cmView?.scrollDOM;
            const rect = panel?.getBoundingClientRect?.();
            return {
              error: 'file editor did not become scrollable',
              active: activeItemForSide('left'),
              item,
              panelExists: Boolean(panel),
              connected: Boolean(panel?.isConnected),
              panelHeight: rect?.height || 0,
              hasView: Boolean(panel?._cmView),
              scrollHeight: scroller?.scrollHeight || 0,
              clientHeight: scroller?.clientHeight || 0,
              cmText: panel?.querySelector?.('.file-editor-codemirror-panel')?.textContent?.slice(0, 80) || '',
              bootErrors: window.__bootErrors || [],
              bootRejections: window.__bootRejections || [],
            };
          }
          const panel = panelNodes.get(item);
          const scroller = panel._cmView.scrollDOM;
          scroller.scrollTop = Math.min(9000, scroller.scrollHeight - scroller.clientHeight - 10);
          await frame();
          await frame();
          const savedTop = scroller.scrollTop;
          activatePaneTab('left', prefsItemId, {userInitiated: true});
          const prefsReady = await waitFor(() => activeItemForSide('left') === prefsItemId && panelNodes.get(prefsItemId)?.isConnected);
          const captured = fileEditorViewState.get(item);
          const capturedTop = captured?.scrollTop || 0;
          const capturedSnapshot = Boolean(captured?.scrollSnapshot);
          if (!prefsReady) return {error: 'preferences tab did not activate', savedTop, capturedTop, capturedSnapshot};
          activatePaneTab('left', item, {userInitiated: true});
          const fileReady = await waitFor(() => activeItemForSide('left') === item && panelNodes.get(item)?.isConnected && panelNodes.get(item)?._cmView?.scrollDOM);
          if (!fileReady) return {error: 'file tab did not reactivate', savedTop, capturedTop, capturedSnapshot};
          await frame();
          await frame();
          await new Promise(resolve => setTimeout(resolve, 140));
          await frame();
          const restoredPanel = panelNodes.get(item);
          const restoredScroller = restoredPanel._cmView.scrollDOM;
          return {
            savedTop,
            capturedTop,
            capturedSnapshot,
            restoredTop: restoredScroller.scrollTop,
            scrollHeight: restoredScroller.scrollHeight,
            clientHeight: restoredScroller.clientHeight,
            active: activeItemForSide('left'),
            focusedPanelItem,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["capturedSnapshot"] is True, metrics
    assert abs(metrics["capturedTop"] - metrics["savedTop"]) < 32, metrics
    assert abs(metrics["restoredTop"] - metrics["savedTop"]) < 32, metrics


def test_long_markdown_editor_scroll_survives_dockview_tab_click_roundtrip(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof applyLayoutSlots === 'function' && typeof registerFileEditorLayoutItem === 'function';"
        )
    )
    setup = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          const path = '/home/test/repo/2026.md';
          const item = fileEditorItemFor(path);
          const content = Array.from({length: 1400}, (_value, index) => `# Entry ${index + 1}\\n\\n- Work item ${index + 1} with enough text to produce normal Markdown editor rows.`).join('\\n');
          setFileState(path, {
            kind: 'text',
            content,
            original: content,
            dirty: false,
            language: 'markdown',
            gitRoot: '/home/test/repo',
            gitTracked: true,
            gitHasHistory: true,
            gitHistory: [{ref: 'HEAD'}],
          });
          setFileEditorViewMode(path, 'edit', item);
          registerFileEditorLayoutItem(path);
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([item, prefsItemId], item);
          applyLayoutSlots(next, {focusSession: item, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 260; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => {
            const panel = panelNodes.get(item);
            const scroller = panel?._cmView?.scrollDOM;
            return dockviewLayoutActive()
              && activeItemForSide('left') === item
              && panel?.isConnected
              && scroller
              && scroller.scrollHeight > scroller.clientHeight * 3
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === item)
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === prefsItemId);
          });
          if (!ready) {
            const panel = panelNodes.get(item);
            const scroller = panel?._cmView?.scrollDOM;
            return {
              error: 'dockview editor did not become ready',
              dockview: typeof dockviewLayoutActive === 'function' ? dockviewLayoutActive() : null,
              active: activeItemForSide('left'),
              panelExists: Boolean(panel),
              connected: Boolean(panel?.isConnected),
              hasView: Boolean(panel?._cmView),
              scrollHeight: scroller?.scrollHeight || 0,
              clientHeight: scroller?.clientHeight || 0,
              tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            };
          }
          const panel = panelNodes.get(item);
          const scroller = panel._cmView.scrollDOM;
          scroller.scrollTop = Math.min(9000, scroller.scrollHeight - scroller.clientHeight - 10);
          await frame();
          await frame();
          return {
            item,
            savedTop: scroller.scrollTop,
            preSwitchCapturedTop: fileEditorViewState.get(item)?.scrollTop || 0,
            clientHeight: scroller.clientHeight,
            scrollHeight: scroller.scrollHeight,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in setup, setup
    assert setup["savedTop"] > setup["clientHeight"], setup

    def dockview_tab(item):
        return WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                return Array.from(document.querySelectorAll('.dockview-pane-tab'))
                  .find(tab => tab.dataset.paneTab === arguments[0]) || null;
                """,
                item,
            )
        )

    ActionChains(browser).move_to_element(dockview_tab("__prefs__")).click().perform()
    after_prefs = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const item = arguments[0];
            if (activeItemForSide('left') !== prefsItemId) return null;
            const state = fileEditorViewState.get(item);
            return {
              active: activeItemForSide('left'),
              capturedTop: state?.scrollTop || 0,
              capturedSnapshot: Boolean(state?.scrollSnapshot),
              panelConnected: Boolean(panelNodes.get(item)?.isConnected),
            };
            """,
            setup["item"],
        )
    )
    assert after_prefs["capturedSnapshot"] is True, after_prefs
    assert abs(after_prefs["capturedTop"] - setup["savedTop"]) < 32, {**setup, **after_prefs}

    ActionChains(browser).move_to_element(dockview_tab(setup["item"])).click().perform()
    restored = browser.execute_async_script(
        """
        const item = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 220; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => activeItemForSide('left') === item && panelNodes.get(item)?.isConnected && panelNodes.get(item)?._cmView?.scrollDOM);
          await frame();
          await frame();
          await new Promise(resolve => setTimeout(resolve, 140));
          await frame();
          const panel = panelNodes.get(item);
          const scroller = panel?._cmView?.scrollDOM;
          return {
            ready,
            active: activeItemForSide('left'),
            restoredTop: scroller?.scrollTop || 0,
            scrollHeight: scroller?.scrollHeight || 0,
            clientHeight: scroller?.clientHeight || 0,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        setup["item"],
    )
    assert restored["ready"] is True, restored
    assert abs(restored["restoredTop"] - setup["savedTop"]) < 32, {**setup, **after_prefs, **restored}


def test_preferences_scroll_survives_dockview_tab_click_roundtrip(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof applyLayoutSlots === 'function' && typeof paneViewState !== 'undefined';"
        )
    )
    setup = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([prefsItemId, infoItemId], prefsItemId);
          applyLayoutSlots(next, {focusSession: prefsItemId, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 240; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => {
            const scroller = panelNodes.get(prefsItemId)?.querySelector('.preferences-scroll');
            return dockviewLayoutActive()
              && activeItemForSide('left') === prefsItemId
              && scroller
              && scroller.scrollHeight > scroller.clientHeight * 2
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === prefsItemId)
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === infoItemId);
          });
          if (!ready) {
            const scroller = panelNodes.get(prefsItemId)?.querySelector('.preferences-scroll');
            return {
              error: 'preferences pane did not become scrollable',
              active: activeItemForSide('left'),
              scrollHeight: scroller?.scrollHeight || 0,
              clientHeight: scroller?.clientHeight || 0,
              tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            };
          }
          const scroller = panelNodes.get(prefsItemId).querySelector('.preferences-scroll');
          scroller.scrollTop = Math.min(9000, scroller.scrollHeight - scroller.clientHeight - 10);
          await frame();
          await frame();
          return {
            item: prefsItemId,
            other: infoItemId,
            savedTop: scroller.scrollTop,
            preSwitchCapturedTop: paneViewState.get(prefsItemId)?.scrollContainers?.find(entry => entry.scrollTop > 0)?.scrollTop || 0,
            clientHeight: scroller.clientHeight,
            scrollHeight: scroller.scrollHeight,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in setup, setup
    assert setup["savedTop"] > setup["clientHeight"], setup
    assert abs(setup["preSwitchCapturedTop"] - setup["savedTop"]) < 32, setup

    def dockview_tab(item):
        return WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                return Array.from(document.querySelectorAll('.dockview-pane-tab'))
                  .find(tab => tab.dataset.paneTab === arguments[0]) || null;
                """,
                item,
            )
        )

    ActionChains(browser).move_to_element(dockview_tab(setup["other"])).click().perform()
    after_other = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            if (activeItemForSide('left') !== arguments[0]) return null;
            const state = paneViewState.get(arguments[1]);
            return {
              active: activeItemForSide('left'),
              capturedTop: state?.scrollContainers?.find(entry => entry.scrollTop > 0)?.scrollTop || 0,
            };
            """,
            setup["other"],
            setup["item"],
        )
    )
    assert abs(after_other["capturedTop"] - setup["savedTop"]) < 32, {**setup, **after_other}

    ActionChains(browser).move_to_element(dockview_tab(setup["item"])).click().perform()
    restored = browser.execute_async_script(
        """
        const item = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          for (let attempt = 0; attempt < 80; attempt += 1) {
            if (activeItemForSide('left') === item && panelNodes.get(item)?.querySelector('.preferences-scroll')) break;
            await frame();
          }
          await frame();
          await frame();
          await new Promise(resolve => setTimeout(resolve, 120));
          const scroller = panelNodes.get(item)?.querySelector('.preferences-scroll');
          return {
            active: activeItemForSide('left'),
            restoredTop: scroller?.scrollTop || 0,
            clientHeight: scroller?.clientHeight || 0,
            scrollHeight: scroller?.scrollHeight || 0,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        setup["item"],
    )
    assert restored["active"] == setup["item"], restored
    assert abs(restored["restoredTop"] - setup["savedTop"]) < 32, {**setup, **after_other, **restored}


def test_info_scroll_survives_dockview_tab_click_roundtrip(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof applyLayoutSlots === 'function' && typeof renderInfoPanel === 'function';"
        )
    )
    setup = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          infoPanelSubTab = 'info';
          transcriptMetaLoaded = true;
          transcriptMetaLoading = false;
          transcriptMetaLoadError = '';
          const branches = Array.from({length: 180}, (_value, index) => ({
            name: `feature/long-info-row-${index + 1}`,
            subject: `Long YO!info tree row ${index + 1} that makes the relationship tree scroll.`,
            updated: `2026-06-${String((index % 28) + 1).padStart(2, '0')}`,
            updated_ts: 1800000000 - index,
            current: index === 0,
            linear_ids: [`YOLO-${index + 1}`],
          }));
          transcriptMeta = {
            session_order: ['1'],
            sessions: {
              '1': {
                session: '1',
                project: {
                  git: {
                    root: '/home/test/repo',
                    cwd: '/home/test/repo',
                    branch: 'feature/long-info-row-1',
                    other_branches: {branches},
                  },
                  linear: [],
                },
              },
            },
          };
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([infoItemId, prefsItemId], infoItemId);
          applyLayoutSlots(next, {focusSession: infoItemId, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 260; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => {
            const scroller = document.getElementById('info-content');
            return dockviewLayoutActive()
              && activeItemForSide('left') === infoItemId
              && scroller
              && scroller.scrollHeight > scroller.clientHeight * 2
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === infoItemId)
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === prefsItemId);
          });
          if (!ready) {
            const scroller = document.getElementById('info-content');
            return {
              error: 'info pane did not become scrollable',
              active: activeItemForSide('left'),
              scrollHeight: scroller?.scrollHeight || 0,
              clientHeight: scroller?.clientHeight || 0,
              rows: document.querySelectorAll('#info-content .info-tree-record').length,
              tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            };
          }
          const scroller = document.getElementById('info-content');
          scroller.scrollTop = Math.min(9000, scroller.scrollHeight - scroller.clientHeight - 10);
          await frame();
          await frame();
          return {
            item: infoItemId,
            other: prefsItemId,
            savedTop: scroller.scrollTop,
            preSwitchCapturedTop: paneViewState.get(infoItemId)?.scrollContainers?.find(entry => entry.scrollTop > 0)?.scrollTop || 0,
            clientHeight: scroller.clientHeight,
            scrollHeight: scroller.scrollHeight,
            rowCount: document.querySelectorAll('#info-content .info-tree-record').length,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in setup, setup
    assert setup["rowCount"] > 100, setup
    assert setup["savedTop"] > setup["clientHeight"], setup
    assert abs(setup["preSwitchCapturedTop"] - setup["savedTop"]) < 32, setup

    def dockview_tab(item):
        return WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                return Array.from(document.querySelectorAll('.dockview-pane-tab'))
                  .find(tab => tab.dataset.paneTab === arguments[0]) || null;
                """,
                item,
            )
        )

    ActionChains(browser).move_to_element(dockview_tab(setup["other"])).click().perform()
    after_other = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            if (activeItemForSide('left') !== arguments[0]) return null;
            const state = paneViewState.get(arguments[1]);
            return {
              active: activeItemForSide('left'),
              capturedTop: state?.scrollContainers?.find(entry => entry.scrollTop > 0)?.scrollTop || 0,
            };
            """,
            setup["other"],
            setup["item"],
        )
    )
    assert abs(after_other["capturedTop"] - setup["savedTop"]) < 32, {**setup, **after_other}

    ActionChains(browser).move_to_element(dockview_tab(setup["item"])).click().perform()
    restored = browser.execute_async_script(
        """
        const item = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          for (let attempt = 0; attempt < 100; attempt += 1) {
            if (activeItemForSide('left') === item && document.getElementById('info-content')) break;
            await frame();
          }
          await frame();
          await frame();
          await new Promise(resolve => setTimeout(resolve, 120));
          const scroller = document.getElementById('info-content');
          return {
            active: activeItemForSide('left'),
            restoredTop: scroller?.scrollTop || 0,
            clientHeight: scroller?.clientHeight || 0,
            scrollHeight: scroller?.scrollHeight || 0,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        setup["item"],
    )
    assert restored["active"] == setup["item"], restored
    assert abs(restored["restoredTop"] - setup["savedTop"]) < 32, {**setup, **after_other, **restored}


def test_topbar_finder_and_modified_files_headers_hover_accent_in_light_mode(browser, tmp_path):
    def theme_tokens():
        return browser.execute_script(
            """
            document.body.classList.add('theme-light');
            function tokenColor(name) {
              const probe = document.createElement('div');
              probe.style.background = `var(${name})`;
              probe.style.position = 'absolute';
              probe.style.left = '-1000px';
              probe.style.top = '-1000px';
              document.body.appendChild(probe);
              const color = getComputedStyle(probe).backgroundColor;
              probe.remove();
              return color;
            }
            return {
              panel: tokenColor('--panel'),
              neutral: tokenColor('--panel2'),
              accent: tokenColor('--pane-tab-strip-bg'),
            };
            """
        )

    def background(selector):
        return browser.execute_script("return getComputedStyle(document.querySelector(arguments[0])).backgroundColor", selector)

    def wait_background(selector, expected):
        WebDriverWait(browser, 2).until(lambda _driver: background(selector) == expected)

    load_topbar_font_fixture(browser, tmp_path)
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".pane-tab")).perform()
    tokens = theme_tokens()
    wait_background("#topbar-fixture", tokens["neutral"])
    ActionChains(browser).move_to_element(browser.find_element("id", "topbar-fixture")).perform()
    wait_background("#topbar-fixture", tokens["accent"])
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".pane-tab")).perform()
    wait_background("#topbar-fixture", tokens["neutral"])

    load_finder_click_toolbar_fixture(browser, tmp_path)
    tokens = theme_tokens()
    wait_background("#finder-panel .file-explorer-head", tokens["neutral"])
    ActionChains(browser).move_to_element(browser.find_element("css selector", "#finder-panel .file-explorer-head")).perform()
    wait_background("#finder-panel .file-explorer-head", tokens["accent"])
    ActionChains(browser).move_to_element(browser.find_element("id", "terminal-panel")).perform()
    wait_background("#finder-panel .file-explorer-head", tokens["neutral"])

    activate_finder_diff_fixture(browser)
    wait_background("#modified-files-panel .changes-toolbar", tokens["panel"])
    wait_background("#modified-files-repo-head", tokens["neutral"])
    repo_caret_metrics = browser.execute_script(
        """
        const caret = document.querySelector('#modified-files-repo-head .changes-repo-caret');
        const title = document.querySelector('#modified-files-repo-head .changes-repo-title');
        const caretStyle = getComputedStyle(caret);
        const titleStyle = getComputedStyle(title);
        return {
          caretFontSize: parseFloat(caretStyle.fontSize),
          titleFontSize: parseFloat(titleStyle.fontSize),
          caretWidth: caret.getBoundingClientRect().width,
          titleHeight: title.getBoundingClientRect().height,
        };
        """
    )
    assert repo_caret_metrics["caretFontSize"] > repo_caret_metrics["titleFontSize"], repo_caret_metrics
    assert repo_caret_metrics["caretWidth"] >= 16, repo_caret_metrics
    assert repo_caret_metrics["titleHeight"] > 0, repo_caret_metrics
    ActionChains(browser).move_to_element(browser.find_element("id", "modified-files-panel")).perform()
    wait_background("#finder-panel .file-explorer-head", tokens["neutral"])
    wait_background("#modified-files-panel .changes-toolbar", tokens["accent"])
    wait_background("#modified-files-repo-head", tokens["accent"])


def test_finder_and_embedded_differ_scrollbars_hover_independently(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    browser.execute_script(
        """
        const tree = document.querySelector('.file-explorer-tree-panel');
        tree.innerHTML = '<div style="height: 520px"></div>';
        """
    )

    def thumb(selector):
        return browser.execute_script(
            "return getComputedStyle(document.querySelector(arguments[0]), '::-webkit-scrollbar-thumb').backgroundColor",
            selector,
        )

    def wait_thumb(selector, expected):
        WebDriverWait(browser, 2).until(lambda _driver: thumb(selector) == expected)

    neutral = "rgba(190, 205, 218, 0.56)"
    accent = browser.execute_script(
        """
        const probe = document.createElement('div');
        probe.style.background = 'var(--pane-scrollbar-thumb-active)';
        document.body.appendChild(probe);
        const color = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return color;
        """
    )
    overflow = browser.execute_script(
        """
        const tree = document.querySelector('.file-explorer-tree-panel');
        return {
          tree: tree.scrollHeight > tree.clientHeight,
        };
        """
    )
    assert overflow["tree"]

    wait_thumb(".file-explorer-tree-panel", neutral)
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".file-explorer-tree-panel")).perform()
    wait_thumb(".file-explorer-tree-panel", accent)
    browser.execute_script("document.getElementById('finder-panel')?.classList.remove('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".file-explorer-tree-panel")).perform()
    wait_thumb(".file-explorer-tree-panel", neutral)
    ActionChains(browser).move_to_element(browser.find_element("id", "terminal-panel")).perform()
    wait_thumb(".file-explorer-tree-panel", neutral)

    activate_finder_diff_fixture(browser)
    browser.execute_script(
        """
        const differ = document.getElementById('modified-files-panel');
        differ.insertAdjacentHTML('beforeend', '<div style="height: 520px"></div>');
        """
    )
    overflow = browser.execute_script(
        """
        const differ = document.getElementById('modified-files-panel');
        return {differ: differ.scrollHeight > differ.clientHeight};
        """
    )
    assert overflow["differ"]
    wait_thumb("#modified-files-panel", neutral)
    browser.execute_script("document.getElementById('finder-panel')?.classList.add('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("id", "modified-files-panel")).perform()
    wait_thumb("#modified-files-panel", accent)
    browser.execute_script("document.getElementById('finder-panel')?.classList.remove('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("id", "modified-files-panel")).perform()
    wait_thumb("#modified-files-panel", neutral)
    ActionChains(browser).move_to_element(browser.find_element("id", "terminal-panel")).perform()
    wait_thumb("#modified-files-panel", neutral)


def test_finder_differ_row_hover_and_embedded_refresh_are_visible_in_light_mode(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    activate_finder_diff_fixture(browser)
    refresh_metrics = browser.execute_script(
        """
        document.body.classList.add('theme-light');
        const button = document.querySelector('#modified-files-panel .changes-refresh');
        const style = getComputedStyle(button);
        const before = getComputedStyle(button, '::before');
        const rect = button.getBoundingClientRect();
        return {
          background: style.backgroundColor,
          borderColor: style.borderTopColor,
          color: style.color,
          beforeContent: before.content,
          beforeDisplay: before.display,
          beforeFontSize: Number.parseFloat(before.fontSize),
          height: rect.height,
          width: rect.width,
        };
        """
    )
    assert refresh_metrics["background"] != "rgb(255, 255, 255)"
    assert refresh_metrics["color"] != "rgb(255, 255, 255)"
    assert refresh_metrics["borderColor"] != "rgb(255, 255, 255)"
    assert refresh_metrics["beforeContent"] == '"↻"'
    assert refresh_metrics["beforeDisplay"] != "none"
    assert refresh_metrics["beforeFontSize"] >= 12
    assert refresh_metrics["height"] >= 18
    assert refresh_metrics["width"] >= 20

    load_pc_controls_fixture(browser, tmp_path)
    hover_tokens = browser.execute_script(
        """
        document.body.classList.add('theme-light');
        const probe = document.createElement('div');
        probe.style.position = 'absolute';
        probe.style.left = '-1000px';
        probe.style.top = '-1000px';
        probe.style.background = 'var(--file-hover-bg)';
        document.body.appendChild(probe);
        const hoverBg = getComputedStyle(probe).backgroundColor;
        probe.style.background = 'var(--file-hover-border)';
        const hoverBorder = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return {hoverBg, hoverBorder};
        """
    )
    ActionChains(browser).move_to_element(browser.find_element("id", "collapsed-dir")).perform()
    row_metrics = browser.execute_script(
        """
        const row = document.getElementById('collapsed-dir');
        const style = getComputedStyle(row);
        return {
          background: style.backgroundColor,
          boxShadow: style.boxShadow,
        };
        """
    )
    assert hover_tokens["hoverBg"] == "rgb(255, 242, 168)"
    assert row_metrics["background"] == hover_tokens["hoverBg"]
    assert hover_tokens["hoverBorder"] in row_metrics["boxShadow"]


def test_finder_sync_current_file_reuses_selected_row_colors(browser, tmp_path):
    load_pc_controls_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const read = () => {
          const selected = getComputedStyle(document.getElementById('selected-file-row'));
          const current = getComputedStyle(document.getElementById('current-file-row'));
          const selectedName = getComputedStyle(document.querySelector('#selected-file-row .file-tree-name'));
          const currentName = getComputedStyle(document.querySelector('#current-file-row .file-tree-name'));
          return {
            selectedColor: selected.color,
            currentColor: current.color,
            selectedNameColor: selectedName.color,
            currentNameColor: currentName.color,
            selectedBg: selected.backgroundColor,
            currentBg: current.backgroundColor,
            selectedShadow: selected.boxShadow,
            currentShadow: current.boxShadow,
          };
        };
        document.body.classList.remove('theme-light');
        document.body.classList.add('theme-dark');
        const dark = read();
        document.body.classList.remove('theme-dark');
        document.body.classList.add('theme-light');
        const light = read();
        return {dark, light};
        """
    )
    for theme in ("dark", "light"):
        assert metrics[theme]["currentColor"] == metrics[theme]["selectedColor"], metrics
        assert metrics[theme]["currentNameColor"] == metrics[theme]["selectedNameColor"], metrics
        assert metrics[theme]["currentBg"] == metrics[theme]["selectedBg"], metrics
        assert metrics[theme]["currentShadow"] == metrics[theme]["selectedShadow"], metrics


def test_finder_differ_status_badges_share_one_column(browser, tmp_path):
    load_file_tree_status_alignment_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const rowIds = ['status-row-m', 'status-row-t', 'status-row-q'];
        const rows = rowIds.map(id => {
          const row = document.getElementById(id);
          const status = row.querySelector('.file-tree-git-status');
          const date = row.querySelector('.file-tree-date');
          const rowRect = row.getBoundingClientRect();
          const statusRect = status.getBoundingClientRect();
          const dateRect = date.getBoundingClientRect();
          return {
            statusCenterX: statusRect.left + statusRect.width / 2,
            statusCenterY: statusRect.top + statusRect.height / 2,
            rowCenterY: rowRect.top + rowRect.height / 2,
            statusRight: statusRect.right,
            dateLeft: dateRect.left,
            dateRight: dateRect.right,
          };
        });
        const xs = rows.map(row => row.statusCenterX);
        const centerYs = rows.map(row => Math.abs(row.statusCenterY - row.rowCenterY));
        const dateRights = rows.map(row => row.dateRight);
        return {
          statusCenterDelta: Math.max(...xs) - Math.min(...xs),
          maxVerticalDelta: Math.max(...centerYs),
          dateRightDelta: Math.max(...dateRights) - Math.min(...dateRights),
          statusBeforeDate: rows.every(row => row.statusRight <= row.dateLeft + 0.5),
        };
        """
    )
    assert metrics["statusCenterDelta"] <= 0.75
    assert metrics["dateRightDelta"] <= 0.75
    assert metrics["maxVerticalDelta"] <= 1.0
    assert metrics["statusBeforeDate"]
    hidden_date_metrics = browser.execute_script(
        """
        const row = document.getElementById('status-row-m');
        const status = row.querySelector('.file-tree-git-status');
        const diff = row.querySelector('.file-tree-diff');
        const date = row.querySelector('.file-tree-date');
        const beforeStatusRight = status.getBoundingClientRect().right;
        const beforeDiffRight = diff.getBoundingClientRect().right;
        date.hidden = true;
        const rowRect = row.getBoundingClientRect();
        const statusRect = status.getBoundingClientRect();
        const diffRect = diff.getBoundingClientRect();
        return {
          dateDisplay: getComputedStyle(date).display,
          statusGain: statusRect.right - beforeStatusRight,
          diffGain: diffRect.right - beforeDiffRight,
          statusRightGap: rowRect.right - statusRect.right,
        };
        """
    )
    assert hidden_date_metrics["dateDisplay"] == "none"
    assert hidden_date_metrics["statusGain"] >= 80
    assert hidden_date_metrics["diffGain"] >= 80
    assert hidden_date_metrics["statusRightGap"] <= 10


def test_differ_long_filename_ellipsizes_before_date_column(browser, tmp_path):
    load_file_tree_status_alignment_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const row = document.getElementById('status-row-long');
        const shortRow = document.getElementById('status-row-m');
        const tree = row.parentElement;
        const name = row.querySelector('.file-tree-name');
        const agent = row.querySelector('.file-tree-agent');
        const diff = row.querySelector('.file-tree-diff');
        const status = row.querySelector('.file-tree-git-status');
        const date = row.querySelector('.file-tree-date');
        const shortName = shortRow.querySelector('.file-tree-name');
        const shortAgent = shortRow.querySelector('.file-tree-agent');
        const rowRect = row.getBoundingClientRect();
        const treeRect = tree.getBoundingClientRect();
        const nameRect = name.getBoundingClientRect();
        const agentRect = agent.getBoundingClientRect();
        const diffRect = diff.getBoundingClientRect();
        const statusRect = status.getBoundingClientRect();
        const dateRect = date.getBoundingClientRect();
        const shortNameRect = shortName.getBoundingClientRect();
        const shortAgentRect = shortAgent.getBoundingClientRect();
        return {
          treeRight: treeRect.right,
          rowRight: rowRect.right,
          nameRight: nameRect.right,
          agentLeft: agentRect.left,
          diffLeft: diffRect.left,
          statusLeft: statusRect.left,
          dateLeft: dateRect.left,
          dateRight: dateRect.right,
          dateClientWidth: date.clientWidth,
          dateScrollWidth: date.scrollWidth,
          nameClientWidth: name.clientWidth,
          nameScrollWidth: name.scrollWidth,
          nameFlex: getComputedStyle(name).flex,
          agentMarginInlineEnd: getComputedStyle(agent).marginInlineEnd,
          shortNameRight: shortNameRect.right,
          shortAgentLeft: shortAgentRect.left,
          shortNameFlex: getComputedStyle(shortName).flex,
        };
        """
    )
    assert metrics["dateRight"] <= metrics["treeRight"] + 0.5, metrics
    assert metrics["dateScrollWidth"] <= metrics["dateClientWidth"] + 1, metrics
    assert metrics["nameScrollWidth"] > metrics["nameClientWidth"] + 1, metrics
    assert metrics["nameFlex"].startswith("0 1"), metrics
    assert metrics["shortNameFlex"].startswith("0 1"), metrics
    assert metrics["agentMarginInlineEnd"] == "0px", metrics
    assert metrics["nameRight"] <= metrics["agentLeft"] + 0.5, metrics
    assert metrics["shortAgentLeft"] - metrics["shortNameRight"] <= 8, metrics
    assert metrics["agentLeft"] <= metrics["diffLeft"] <= metrics["statusLeft"] <= metrics["dateLeft"], metrics


def test_diff_overview_does_not_cover_editor_scrollbar(browser, tmp_path):
    load_codemirror_scrollbar_overview_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const hostRect = document.getElementById('host').getBoundingClientRect();
        const overviewRect = document.getElementById('overview').getBoundingClientRect();
        const overviewStyle = getComputedStyle(document.getElementById('overview'));
        const scroller = document.getElementById('scroller');
        const scrollerRect = scroller.getBoundingClientRect();
        const scrollbarStyle = getComputedStyle(scroller, '::-webkit-scrollbar');
        const cornerStyle = getComputedStyle(scroller, '::-webkit-scrollbar-corner');
        document.getElementById('overview').style.top = '0px';
        document.getElementById('overview').style.bottom = 'auto';
        document.getElementById('overview').style.height = `${scroller.clientHeight}px`;
        const adjustedOverviewRect = document.getElementById('overview').getBoundingClientRect();
        const verticalTrackBottom = scrollerRect.top + scroller.clientHeight;
        return {
          overviewRightGap: hostRect.right - adjustedOverviewRect.right,
          overviewTopDelta: Math.abs(adjustedOverviewRect.top - scrollerRect.top),
          overviewBottomDelta: Math.abs(adjustedOverviewRect.bottom - verticalTrackBottom),
          overviewWidth: adjustedOverviewRect.width,
          overviewBackground: overviewStyle.backgroundImage,
          overviewPointerEvents: overviewStyle.pointerEvents,
          tickCount: document.querySelectorAll('.cm-diff-overview-tick').length,
          scrollbarWidth: Number.parseFloat(scrollbarStyle.width || '0'),
          cornerBackground: cornerStyle.backgroundColor,
        };
        """
    )
    assert metrics["overviewRightGap"] >= 12
    assert metrics["overviewTopDelta"] <= 1
    assert metrics["overviewBottomDelta"] <= 1
    assert 3 <= metrics["overviewWidth"] <= 5
    assert "linear-gradient" in metrics["overviewBackground"]
    assert metrics["overviewPointerEvents"] == "none"
    assert metrics["tickCount"] == 0
    assert 11 <= metrics["scrollbarWidth"] <= 13
    assert metrics["cornerBackground"] in {
        "rgba(255, 255, 255, 0.04)",
        "rgba(255, 255, 255, 0.05)",
        "rgba(15, 23, 42, 0.1)",
    }


def test_diff_overview_matches_actual_todo_codemirror_rows(browser, tmp_path):
    load_codemirror_todo_diff_overview_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return window.__todoDiffOverviewMetrics != null")
    )
    metrics = browser.execute_script("return window.__todoDiffOverviewMetrics")
    original_text, current_text = codemirror_todo_diff_overview_texts()
    original_lines = original_text.splitlines(keepends=True)
    current_lines = current_text.splitlines(keepends=True)
    common_prefix_lines = 0
    while (
        common_prefix_lines < min(len(original_lines), len(current_lines))
        and original_lines[common_prefix_lines] == current_lines[common_prefix_lines]
    ):
        common_prefix_lines += 1
    common_suffix_lines = 0
    while (
        common_suffix_lines < len(original_lines) - common_prefix_lines
        and common_suffix_lines < len(current_lines) - common_prefix_lines
        and original_lines[len(original_lines) - common_suffix_lines - 1] == current_lines[len(current_lines) - common_suffix_lines - 1]
    ):
        common_suffix_lines += 1
    expected_from = sum(len(line) for line in original_lines[:common_prefix_lines])
    expected_to_a = len(original_text) - sum(len(line) for line in original_lines[len(original_lines) - common_suffix_lines:])
    expected_to_b = len(current_text) - sum(len(line) for line in current_lines[len(current_lines) - common_suffix_lines:])
    # Both fixture sides are frozen (codemirror_todo_diff_overview_texts) as one contiguous block
    # replacement, so the merge view is a single chunk with stable byte offsets derived from the
    # actual common prefix/suffix instead of the moving docs/TODO.md.
    assert len(metrics["chunks"]) == 1
    chunk = metrics["chunks"][0]
    assert chunk["fromA"] == expected_from
    assert chunk["toA"] in {expected_to_a, expected_to_a + 1}
    assert chunk["endA"] == chunk["toA"] - 1
    assert chunk["fromB"] == expected_from
    assert chunk["toB"] in {expected_to_b, expected_to_b + 1}
    assert chunk["endB"] == chunk["toB"] - 1
    bands = metrics["rows"]["bands"]
    assert len(bands) == len(metrics["chunks"]) * 2, bands
    for index in range(0, len(bands), 2):
        assert bands[index]["kind"] == "remove", bands
        assert bands[index + 1]["kind"] == "add", bands
        assert bands[index + 1]["start"] == bands[index]["end"], bands
    deleted_rows = metrics["rows"]["deletedRows"]
    current_line_count = metrics["rows"]["currentLineCount"]
    inserted_rows = metrics["insertedRangeRows"]
    todo_line_count = current_text.count("\n") + 1
    assert current_line_count == todo_line_count, (current_line_count, todo_line_count)
    assert metrics["rows"]["totalRows"] == deleted_rows + current_line_count
    assert deleted_rows > 0 and inserted_rows > 0
    assert metrics["removedRangeRows"] == deleted_rows
    assert 0 < metrics["deletedDomRows"] <= metrics["removedRangeRows"]
    assert "linear-gradient" in metrics["overviewBackground"]
    assert metrics["overviewStops"] == metrics["expectedStops"], metrics["overviewBackground"]
    assert metrics["tickCount"] == 0
    assert metrics["overviewTopDelta"] <= 1
    assert metrics["overviewBottomDelta"] <= 1


def test_diff_wrapped_inserted_line_continuation_rows_show_text(browser, tmp_path):
    # Regression for the screenshot bug: a long INSERTED line that soft-wraps in the unified merge diff
    # rendered only its first visual row; continuation rows were blank green (gutter numbers present,
    # text buried). Root cause was the full-bleed box-shadow/clip-path being applied to the INLINE
    # cm-insertedLine/cm-deletedLine marks, letting the parent .cm-changedLine block band paint over the
    # wrapped rows. Assert each wrapped continuation row has VISIBLE inserted text (bounding-box height
    # > 0 AND non-empty caret text AND the inserted mark is the topmost painted element), with word-wrap
    # on and the collapsed-unchanged state active.
    load_codemirror_diff_wrapped_inserted_line_fixture(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        window.__diffWrapMetrics().then(done);
        """
    )
    assert metrics["insertedFound"] is True, metrics
    assert metrics["hasTailText"] is True, metrics
    # The inserted bullet is long enough to occupy more than one visual row in a 460px pane.
    assert metrics["wraps"] is True, metrics
    assert metrics["boundingHeight"] > metrics["lineHeight"] * 1.5, metrics
    assert metrics["rowCount"] >= 2, metrics
    # Every continuation visual row must paint visible inserted text (height > 0 and non-empty content),
    # not a blank band. This is the exact assertion the box demands.
    continuation_rows = metrics["rows"][1:]
    assert continuation_rows, metrics
    for row in continuation_rows:
        assert row["topElInsideInserted"] is True, (row, metrics)
        assert row["caretTextLen"] > 0, (row, metrics)
    assert metrics["continuationRowsAllVisible"] is True, metrics
    # W5: the collapsed-unchanged widget stays in normal flow and does not overlap the inserted block;
    # it only LOOKED like it floated over the green block because the continuation rows were blank.
    assert metrics["collapsePresent"] is True, metrics
    assert metrics["collapsePosition"] == "static", metrics
    assert metrics["collapseOverlapPx"] == 0, metrics


def test_diff_overview_matches_actual_file_explorer_visible_rows_after_scroll(browser, tmp_path):
    load_codemirror_file_explorer_diff_overview_fixture(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        window.__fileExplorerDiffOverviewMetrics().then(done);
        """
    )
    assert metrics["chunks"] == [
        {
            "fromA": 56602,
            "toA": 138459,
            "endA": 138458,
            "fromB": 56602,
            "toB": 144134,
            "endB": 144133,
        }
    ]
    assert metrics["tickCount"] == 0
    assert metrics["initialBackground"] != metrics["finalBackground"]
    assert metrics["initialBackground"] == ""
    assert metrics["initialOverviewPresent"] is False
    assert metrics["initialDeletedDomRows"] == 0
    assert metrics["initialChangedStops"] == []
    assert metrics["fullRows"]["deletedRows"] == 1986
    assert metrics["finalOverviewPresent"] is True
    assert metrics["finalChangedStops"] == metrics["expectedFullChangedStops"], metrics["finalBackground"]
    assert any(stop["color"] == '#ff5d6c' for stop in metrics["finalChangedStops"])
    assert any(stop["color"] == '#38d878' for stop in metrics["finalChangedStops"])
    cases = {case["name"]: case for case in metrics["cases"]}
    assert cases["top-normal"]["deletedDomRows"] == 0
    assert cases["red-middle-previous-regression"]["deletedDomRows"] == 1986
    checked_cases = [
        cases["red-middle-previous-regression"],
        cases["red-late-previous-regression"],
        cases["green-middle"],
    ]
    for case in checked_cases:
        assert case["mismatches"] == [], f"{case['name']} mismatched visible rows: {case['mismatches']}"
    for case in cases.values():
        if not case["railPresent"]:
            assert case["background"] == ""
            assert not any(sample["rail"] == "remove" for sample in case["samples"]), case
    assert any(sample["visible"] == "normal" for sample in cases["top-normal"]["samples"])
    assert any(sample["visible"] == "remove" for sample in cases["red-middle-previous-regression"]["samples"])
    assert any(sample["visible"] == "remove" for sample in cases["red-late-previous-regression"]["samples"])
    assert any(sample["visible"] == "add" for sample in cases["green-middle"]["samples"])


def test_diff_left_gutter_stays_neutral(browser, tmp_path):
    load_codemirror_scrollbar_overview_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const changed = document.getElementById('changed-gutter');
        const deleted = document.getElementById('deleted-gutter');
        const mergeRevert = document.getElementById('merge-revert');
        const changedStyle = getComputedStyle(changed);
        const deletedStyle = getComputedStyle(deleted);
        const mergeRevertStyle = getComputedStyle(mergeRevert);
        return {
          changedBg: changedStyle.backgroundColor,
          deletedBg: deletedStyle.backgroundColor,
          changedColor: changedStyle.color,
          deletedColor: deletedStyle.color,
          mergeRevertDisplay: mergeRevertStyle.display,
        };
        """
    )
    assert metrics["changedBg"] == "rgba(0, 0, 0, 0)"
    assert metrics["deletedBg"] == "rgba(0, 0, 0, 0)"
    assert metrics["changedColor"] == metrics["deletedColor"]
    assert metrics["mergeRevertDisplay"] == "none"


def test_finder_path_is_first_and_readable_in_wrapped_toolbar(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const toolbar = document.querySelector('#finder-panel .file-explorer-toolbar');
        const primaryRow = toolbar.querySelector('.file-explorer-primary-row');
        const pathRow = toolbar.querySelector('.file-explorer-path-row');
        const actionsRow = toolbar.querySelector('.file-explorer-actions-row');
        const scopeRow = toolbar.querySelector('.file-explorer-scope-row');
        const collapse = primaryRow.querySelector('[data-session-files-collapse-toggle]');
        const newFile = actionsRow.querySelector('[data-file-explorer-new-file]');
        const newFolder = actionsRow.querySelector('[data-file-explorer-new-folder]');
        const actionsSpacer = actionsRow.querySelector('.file-explorer-toolbar-spacer');
        const sync = pathRow.querySelector('.file-explorer-root-mode-toggle-panel');
        const hidden = actionsRow.querySelector('.file-explorer-hidden-toggle-panel');
        const sort = actionsRow.querySelector('.file-explorer-sort-select');
        const quick = toolbar.querySelector('.file-explorer-quick-access-panel');
        const path = pathRow.querySelector('.file-explorer-path-inline');
        const copy = pathRow.querySelector('.file-explorer-path-copy-panel');
        const mode = primaryRow.querySelector('.file-explorer-mode-switcher');
        const diffSession = primaryRow.querySelector('.file-explorer-diff-session-control');
        const primarySpacer = primaryRow.querySelector('.file-explorer-toolbar-spacer');
        const modeButtons = Array.from(mode.querySelectorAll('[data-file-explorer-mode-set]'));
        const modeLabels = Array.from(mode.querySelectorAll('.file-explorer-mode-label'));
        const cluster = toolbar.querySelector('.file-explorer-date-reload-cluster');
        const date = cluster.querySelector('.file-explorer-date-toggle');
        const refresh = cluster.querySelector('.changes-refresh');
        const close = primaryRow.querySelector('.file-explorer-panel-close');
        const toolbarRect = toolbar.getBoundingClientRect();
        const primaryRowRect = primaryRow.getBoundingClientRect();
        const pathRowRect = pathRow.getBoundingClientRect();
        const actionsRowRect = actionsRow.getBoundingClientRect();
        const newFileRect = newFile.getBoundingClientRect();
        const newFolderRect = newFolder.getBoundingClientRect();
        const syncRect = sync.getBoundingClientRect();
        const hiddenRect = hidden.getBoundingClientRect();
        const sortRect = sort.getBoundingClientRect();
        const pathRect = path.getBoundingClientRect();
        const copyRect = copy.getBoundingClientRect();
        const modeRect = mode.getBoundingClientRect();
        const diffSessionRect = diffSession.getBoundingClientRect();
        const modeButtonRects = modeButtons.map(button => button.getBoundingClientRect());
        const modeButtonStyles = modeButtons.map(button => getComputedStyle(button));
        const clusterRect = cluster.getBoundingClientRect();
        const dateRect = date.getBoundingClientRect();
        const refreshRect = refresh.getBoundingClientRect();
        const closeRect = close.getBoundingClientRect();
        const textProbe = document.createElement('span');
        textProbe.style.color = 'var(--text)';
        document.body.appendChild(textProbe);
        const textColor = getComputedStyle(textProbe).color;
        textProbe.remove();
        const colorFor = value => {
          const probe = document.createElement('span');
          probe.style.color = value;
          document.body.appendChild(probe);
          const color = getComputedStyle(probe).color;
          probe.remove();
          return color;
        };
        const tabFont = getComputedStyle(document.documentElement).getPropertyValue('--tab-font').trim();
        return {
          firstRowIsPrimary: toolbar.firstElementChild === primaryRow,
          secondRowIsPath: primaryRow.nextElementSibling === pathRow,
          thirdRowIsActions: pathRow.nextElementSibling === actionsRow,
          noScopeRow: scopeRow === null,
          noQuickAccessPanel: quick === null,
          modeFirstInPrimaryRow: primaryRow.firstElementChild === mode,
          noPanelTitle: primaryRow.querySelector('.file-explorer-panel-title') === null,
          actionsOrder: actionsRow.firstElementChild === newFile && newFile.nextElementSibling === newFolder,
          folderIconPresent: newFolder.querySelector('.file-explorer-folder-icon') !== null,
          pathRowOrder: pathRow.firstElementChild === sync && sync.nextElementSibling === path && path.nextElementSibling === copy,
          hiddenBeforeSort: newFolder.nextElementSibling === actionsSpacer && actionsSpacer.nextElementSibling === hidden && hidden.nextElementSibling === sort,
          syncText: sync.textContent.trim(),
          syncPressed: sync.getAttribute('aria-pressed'),
          rootPressedCount: [sync].filter(button => button.getAttribute('aria-pressed') === 'true').length,
          diffSessionImmediatelyAfterMode: mode.nextElementSibling === diffSession,
          spacerAfterDiffSession: diffSession.nextElementSibling === primarySpacer,
          diffSessionVisibleInFilesMode: getComputedStyle(diffSession).display !== 'none',
          noTopCollapseButton: collapse === null,
          newFileLeft: newFileRect.left,
          newFileRight: newFileRect.right,
          newFolderLeft: newFolderRect.left,
          syncLeft: syncRect.left,
          syncRight: syncRect.right,
          hiddenLeft: hiddenRect.left,
          hiddenRight: hiddenRect.right,
          sortLeft: sortRect.left,
          pathRowTop: pathRowRect.top,
          pathRowBottom: pathRowRect.bottom,
          pathRowLeft: pathRowRect.left,
          pathRowRight: pathRowRect.right,
          pathRowWidth: pathRowRect.width,
          pathLeft: pathRect.left,
          pathRight: pathRect.right,
          primaryRowLeft: primaryRowRect.left,
          primaryRowRight: primaryRowRect.right,
          primaryRowWidth: primaryRowRect.width,
          diffSessionLeft: diffSessionRect.left,
          diffSessionRight: diffSessionRect.right,
          copyLeft: copyRect.left,
          copyRight: copyRect.right,
          copyWidth: copyRect.width,
          modeLeft: modeRect.left,
          modeRight: modeRect.right,
          modeWidth: modeRect.width,
          modeMaxButtonWidth: Math.max(...modeButtonRects.map(rect => rect.width)),
          modeButtonPaddingInline: Array.from(new Set(modeButtonStyles.map(style => `${style.paddingLeft}/${style.paddingRight}`))).sort(),
          modeButtonHorizontal: modeButtonRects.every(rect => rect.width > rect.height),
          modeLabelsHorizontal: modeLabels.every(label => getComputedStyle(label).writingMode === 'horizontal-tb'),
          modeUsesTabFont: modeButtonStyles.every(style => style.fontFamily === tabFont || style.fontFamily.toLowerCase().includes('narrow')),
          modeButtonTopRounded: modeButtonStyles.every(style => style.borderTopLeftRadius !== '0px' && style.borderTopRightRadius !== '0px' && style.borderBottomLeftRadius === '0px'),
          activeModeUsesPaneTabColor: getComputedStyle(mode.querySelector('[aria-pressed="true"]')).backgroundColor === colorFor('var(--pane-tab-active-bg)'),
          modeTexts: Array.from(mode.querySelectorAll('[data-file-explorer-mode-set]')).map(button => button.textContent.trim()),
          pathConsumesRemaining: pathRect.width >= pathRowRect.width - syncRect.width - copyRect.width - 36,
          actionsRowTop: actionsRowRect.top,
          primaryRowBottom: primaryRowRect.bottom,
          actionsRowRight: actionsRowRect.right,
          clusterRight: clusterRect.right,
          clusterLeft: clusterRect.left,
          dateRight: dateRect.right,
          refreshLeft: refreshRect.left,
          refreshRight: refreshRect.right,
          closeLeft: closeRect.left,
          closeRight: closeRect.right,
          pathWidth: pathRect.width,
          toolbarWidth: toolbarRect.width,
          pathColor: getComputedStyle(path).color,
          textColor,
        };
        """
    )
    assert metrics["firstRowIsPrimary"]
    assert metrics["secondRowIsPath"]
    assert metrics["thirdRowIsActions"]
    assert metrics["noScopeRow"]
    assert metrics["noQuickAccessPanel"]
    assert metrics["modeFirstInPrimaryRow"]
    assert metrics["noPanelTitle"]
    assert metrics["actionsOrder"]
    assert metrics["folderIconPresent"]
    assert metrics["pathRowOrder"]
    assert metrics["hiddenBeforeSort"]
    assert metrics["syncText"] == "Sync"
    assert metrics["syncPressed"] == "true"
    assert metrics["rootPressedCount"] == 1
    assert metrics["diffSessionImmediatelyAfterMode"]
    assert metrics["spacerAfterDiffSession"]
    assert metrics["diffSessionVisibleInFilesMode"]
    assert metrics["noTopCollapseButton"]
    assert metrics["newFileRight"] <= metrics["newFolderLeft"]
    assert metrics["hiddenRight"] <= metrics["sortLeft"]
    assert metrics["syncRight"] <= metrics["pathLeft"]
    assert metrics["pathLeft"] > metrics["pathRowLeft"]
    assert metrics["pathWidth"] >= min(90, metrics["toolbarWidth"] / 4)
    assert metrics["pathRight"] <= metrics["copyLeft"]
    assert metrics["copyRight"] <= metrics["pathRowRight"] + 1
    assert metrics["modeRight"] <= metrics["diffSessionLeft"]
    assert metrics["diffSessionRight"] <= metrics["closeLeft"]
    assert metrics["modeButtonHorizontal"]
    assert metrics["modeLabelsHorizontal"]
    assert metrics["modeUsesTabFont"]
    assert metrics["modeButtonTopRounded"]
    assert metrics["activeModeUsesPaneTabColor"]
    assert metrics["modeButtonPaddingInline"] == ["3px/3px"]
    assert metrics["modeMaxButtonWidth"] <= 60
    assert metrics["pathConsumesRemaining"]
    assert metrics["modeTexts"] == ["Finder", "Differ", "Tabber"]
    assert abs(metrics["closeRight"] - metrics["primaryRowRight"]) <= 1
    assert metrics["pathColor"] == metrics["textColor"]
    assert metrics["pathRowTop"] >= metrics["primaryRowBottom"]
    assert metrics["actionsRowTop"] >= metrics["pathRowBottom"]
    assert metrics["dateRight"] <= metrics["refreshLeft"]
    assert metrics["refreshRight"] <= metrics["actionsRowRight"] + 1
    assert metrics["clusterLeft"] > metrics["pathLeft"]


def test_finder_diff_mode_toggle_fills_pane(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    before = browser.execute_script(
        """
        const filesButton = document.querySelector('[data-file-explorer-mode-set="files"]');
        const diffButton = document.querySelector('[data-file-explorer-mode-set="diff"]');
        const newFile = document.getElementById('new-file');
        const tree = document.querySelector('.file-explorer-tree-panel');
        const changes = document.querySelector('.file-explorer-changes-panel');
        return {
          bodyFiles: document.body.classList.contains('file-explorer-mode-files'),
          bodyDiff: document.body.classList.contains('file-explorer-mode-diff'),
          filesPressed: filesButton.getAttribute('aria-pressed'),
          diffPressed: diffButton.getAttribute('aria-pressed'),
          texts: Array.from(document.querySelectorAll('[data-file-explorer-mode-set]')).map(button => button.textContent.trim().replace(/\\s+/g, ' ')),
          diffButtonBg: getComputedStyle(diffButton).backgroundColor,
          newFileDisplay: getComputedStyle(newFile).display,
          treeDisplay: getComputedStyle(tree).display,
          changesDisplay: getComputedStyle(changes).display,
          titleCount: document.querySelectorAll('.file-explorer-panel-title').length,
        };
        """
    )
    assert before["bodyFiles"]
    assert not before["bodyDiff"]
    assert before["filesPressed"] == "true"
    assert before["diffPressed"] == "false"
    assert before["texts"] == ["Finder", "Differ", "Tabber"]
    assert before["newFileDisplay"] != "none"
    assert before["treeDisplay"] != "none"
    assert before["changesDisplay"] == "none"
    assert before["titleCount"] == 0

    browser.find_element("css selector", "[data-file-explorer-mode-set='diff']").click()
    after = browser.execute_script(
        """
        const filesButton = document.querySelector('[data-file-explorer-mode-set="files"]');
        const diffButton = document.querySelector('[data-file-explorer-mode-set="diff"]');
        const newFile = document.getElementById('new-file');
        const pane = document.querySelector('.file-explorer-pane');
        const tree = document.querySelector('.file-explorer-tree-panel');
        const changes = document.querySelector('.file-explorer-changes-panel');
        const visible = selector => Array.from(document.querySelectorAll(selector)).filter(node => node.getClientRects().length > 0);
        const changesStyle = getComputedStyle(changes);
        const paneRect = pane.getBoundingClientRect();
        const changesRect = changes.getBoundingClientRect();
        return {
          bodyFiles: document.body.classList.contains('file-explorer-mode-files'),
          bodyDiff: document.body.classList.contains('file-explorer-mode-diff'),
          panelMode: document.getElementById('finder-panel').dataset.fileExplorerMode,
          filesPressed: filesButton.getAttribute('aria-pressed'),
          diffPressed: diffButton.getAttribute('aria-pressed'),
          texts: Array.from(document.querySelectorAll('[data-file-explorer-mode-set]')).map(button => button.textContent.trim().replace(/\\s+/g, ' ')),
          diffButtonBg: getComputedStyle(diffButton).backgroundColor,
          newFileDisplay: getComputedStyle(newFile).display,
          treeDisplay: getComputedStyle(tree).display,
          changesDisplay: changesStyle.display,
          changesFlexGrow: changesStyle.flexGrow,
          changesMaxBlockSize: changesStyle.maxBlockSize,
          paneHeight: paneRect.height,
          changesHeight: changesRect.height,
          titleCount: document.querySelectorAll('.file-explorer-panel-title').length,
          visibleRootControls: visible('.file-explorer-root-mode-toggle-panel').length,
          visibleSessionSelects: visible('[data-session-files-session]').length,
          visibleSortSelects: visible('[data-session-files-sort]').length,
          visibleDateButtons: visible('[data-file-explorer-tree-dates]').length,
          visibleReloadButtons: visible('[data-session-files-refresh], [data-file-explorer-refresh]').length,
        };
        """
    )
    assert not after["bodyFiles"]
    assert after["bodyDiff"]
    assert after["panelMode"] == "diff"
    assert after["filesPressed"] == "false"
    assert after["diffPressed"] == "true"
    assert after["texts"] == ["Finder", "Differ", "Tabber"]
    assert after["diffButtonBg"] != before["diffButtonBg"]
    assert after["newFileDisplay"] == "none"
    assert after["treeDisplay"] == "none"
    assert after["changesDisplay"] != "none"
    assert after["changesFlexGrow"] == "1"
    assert after["changesMaxBlockSize"] == "none"
    assert abs(after["changesHeight"] - after["paneHeight"]) <= 1
    assert after["titleCount"] == 0
    assert after["visibleRootControls"] == 0
    assert after["visibleSessionSelects"] == 1
    assert after["visibleSortSelects"] == 1
    assert after["visibleDateButtons"] == 1
    assert after["visibleReloadButtons"] == 1

    browser.find_element("css selector", "[data-file-explorer-mode-set='files']").click()
    restored = browser.execute_script(
        """
        const filesButton = document.querySelector('[data-file-explorer-mode-set="files"]');
        const diffButton = document.querySelector('[data-file-explorer-mode-set="diff"]');
        return {
          bodyFiles: document.body.classList.contains('file-explorer-mode-files'),
          bodyDiff: document.body.classList.contains('file-explorer-mode-diff'),
          filesPressed: filesButton.getAttribute('aria-pressed'),
          diffPressed: diffButton.getAttribute('aria-pressed'),
          treeDisplay: getComputedStyle(document.querySelector('.file-explorer-tree-panel')).display,
          changesDisplay: getComputedStyle(document.querySelector('.file-explorer-changes-panel')).display,
        };
        """
    )
    assert restored["bodyFiles"]
    assert not restored["bodyDiff"]
    assert restored["filesPressed"] == "true"
    assert restored["diffPressed"] == "false"
    assert restored["treeDisplay"] != "none"
    assert restored["changesDisplay"] == "none"


def test_platform_controls_use_pc_glyphs(browser, tmp_path):
    load_pc_controls_fixture(browser, tmp_path)
    assert browser.execute_script("return getComputedStyle(document.getElementById('hidden-pane-zoom')).display") == "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('tab-minimize'), '::after').display") == "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('finder-close'), '::after').display") != "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('editor-close'), '::after').display") != "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('pane-zoom'), '::after').display") != "none"
    assert browser.execute_script("return document.getElementById('editor-close').getBoundingClientRect().width") <= 24
    assert browser.execute_script("return document.getElementById('tab-minimize').getBoundingClientRect().width") >= 18
    assert browser.execute_script("return getComputedStyle(document.getElementById('collapsed-preferences')).display") == "none"
    # The working YO marker no longer rotates — the glowing green ball beside the agent symbol is the
    # working indicator now.
    assert browser.execute_script("return getComputedStyle(document.getElementById('working-yolo')).animationName") == "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('working-yolo'), '::after').content") == "none"
    # An idle (auto-on, NON-working) marker must be STATIC — no ambient rotation.
    assert browser.execute_script("return getComputedStyle(document.getElementById('idle-yolo')).animationName") == "none"
    triangle_sizes = browser.execute_script(
        """
        const root = document.documentElement;
        const collapsed = getComputedStyle(document.querySelector('#collapsed-dir > .file-tree-icon'));
        const expanded = getComputedStyle(document.querySelector('#expanded-dir > .file-tree-icon'));
        const defaultWidth = document.querySelector('#collapsed-dir > .file-tree-icon').getBoundingClientRect().width;
        const defaultFontSize = Number.parseFloat(collapsed.fontSize);
        root.style.setProperty('--file-explorer-font-size', '8px');
        const smallIcon = document.querySelector('#collapsed-dir > .file-tree-icon');
        const smallStyle = getComputedStyle(smallIcon);
        const smallWidth = smallIcon.getBoundingClientRect().width;
        const smallFontSize = Number.parseFloat(smallStyle.fontSize);
        root.style.removeProperty('--file-explorer-font-size');
        return {
          collapsedSize: Number.parseFloat(collapsed.fontSize),
          expandedSize: Number.parseFloat(expanded.fontSize),
          collapsedWidth: defaultWidth,
          defaultFontSize,
          smallWidth,
          smallFontSize,
          expandedColor: expanded.color,
          collapsedColor: collapsed.color,
        };
        """
    )
    assert triangle_sizes["collapsedSize"] > 0
    assert triangle_sizes["expandedSize"] > 0
    assert triangle_sizes["smallWidth"] < triangle_sizes["collapsedWidth"]
    assert triangle_sizes["smallFontSize"] < triangle_sizes["defaultFontSize"]
    assert triangle_sizes["expandedColor"] != triangle_sizes["collapsedColor"]
    dots_center_delta = browser.execute_script(
        """
        const button = document.getElementById('pane-actions').getBoundingClientRect();
        const dots = document.getElementById('pane-actions-dots').getBoundingClientRect();
        const actionsStyle = getComputedStyle(document.getElementById('pane-actions'));
        const dotsStyle = getComputedStyle(document.getElementById('pane-actions-dots'));
        const hashStyle = getComputedStyle(document.getElementById('hash-tab'));
        return {
          x: Math.abs((button.left + button.width / 2) - (dots.left + dots.width / 2)),
          y: Math.abs((button.top + button.height / 2) - (dots.top + dots.height / 2)),
          background: actionsStyle.backgroundColor,
          borderColor: actionsStyle.borderTopColor,
          dotsColor: dotsStyle.color,
          hashColor: hashStyle.color,
        };
        """
    )
    assert dots_center_delta["x"] <= 1
    assert dots_center_delta["y"] <= 1
    assert dots_center_delta["background"] != "rgba(0, 0, 0, 0)"
    assert dots_center_delta["borderColor"] != "rgba(0, 0, 0, 0)"
    # Shared pane-chrome treatment: the "..." actions dots and the "#" control share ONE foreground color
    # (--pane-ctl-fg) now — consistent, not per-button (image 009).
    assert dots_center_delta["dotsColor"] == dots_center_delta["hashColor"]
    light_control = browser.execute_script(
        """
        document.body.classList.add('theme-light');
        const actionsStyle = getComputedStyle(document.getElementById('pane-actions'));
        const closeStyle = getComputedStyle(document.getElementById('finder-close'));
        return {
          actionsColor: actionsStyle.color,
          actionsBg: actionsStyle.backgroundColor,
          closeColor: closeStyle.color,
          closeBg: closeStyle.backgroundColor,
          infoLabelColor: getComputedStyle(document.querySelector('#info-tab .pane-tab-info-label')).color,
          infoTabBg: getComputedStyle(document.getElementById('info-tab')).backgroundColor,
        };
        """
    )
    assert light_control["actionsColor"] == "rgb(31, 41, 55)"
    assert light_control["actionsColor"] != light_control["actionsBg"]
    assert light_control["closeColor"] == "rgb(31, 41, 55)"
    assert light_control["closeColor"] != light_control["closeBg"]
    # the YO!info tab label is legible in light mode (color contrasts with the tab bg,
    # not white-on-white) now that it uses the themed .session-button-dir treatment.
    assert light_control["infoLabelColor"] != light_control["infoTabBg"]
    z_indexes = browser.execute_script(
        """
        return {
          contextMenu: Number.parseInt(getComputedStyle(document.getElementById('test-context-menu')).zIndex, 10),
          imagePreview: Number.parseInt(getComputedStyle(document.getElementById('test-image-preview')).zIndex, 10),
          tabPopover: Number.parseInt(getComputedStyle(document.getElementById('test-tab-popover')).zIndex, 10),
        };
        """
    )
    assert z_indexes["contextMenu"] > z_indexes["imagePreview"]
    assert z_indexes["contextMenu"] > z_indexes["tabPopover"]

    ActionChains(browser).move_to_element(browser.find_element("id", "tab-minimize")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('tab-minimize')).opacity") == "1"

    ActionChains(browser).move_to_element(browser.find_element("id", "pane-zoom")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('pane-zoom')).backgroundColor") != "rgba(0, 0, 0, 0)"

    ActionChains(browser).move_to_element(browser.find_element("id", "finder-close")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('finder-close')).opacity") == "1"

    ActionChains(browser).move_to_element(browser.find_element("id", "editor-close")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('editor-close')).opacity") == "1"

    tree_metrics = browser.execute_script(
        """
        const collapsedIcon = document.querySelector('#collapsed-dir .file-tree-icon');
        const expandedIcon = document.querySelector('#expanded-dir .file-tree-icon');
        const collapsedName = document.querySelector('#collapsed-dir .file-tree-name');
        return {
          collapsedColor: getComputedStyle(collapsedIcon).color,
          expandedColor: getComputedStyle(expandedIcon).color,
          iconSize: Number.parseFloat(getComputedStyle(collapsedIcon).fontSize),
          nameSize: Number.parseFloat(getComputedStyle(collapsedName).fontSize),
        };
        """
    )
    assert tree_metrics["collapsedColor"] != tree_metrics["expandedColor"]
    assert tree_metrics["iconSize"] > tree_metrics["nameSize"]
    repo_row_metrics = browser.execute_script(
        """
        const name = document.querySelector('#repo-dir .file-tree-name');
        const branch = document.querySelector('#repo-dir .file-tree-repo-branch');
        const diff = document.querySelector('#repo-dir .file-tree-diff');
        const add = document.querySelector('#repo-dir .changes-diff-add');
        const remove = document.querySelector('#repo-dir .changes-diff-remove');
        const nameRect = name.getBoundingClientRect();
        const diffRect = diff.getBoundingClientRect();
        const addRect = add.getBoundingClientRect();
        const removeRect = remove.getBoundingClientRect();
        return {
          text: name.textContent,
          hasRetiredDelta: Boolean(document.querySelector('#repo-dir .file-tree-repo-delta')),
          nameWeight: getComputedStyle(name).fontWeight,
          branchWeight: getComputedStyle(branch).fontWeight,
          diffWeight: getComputedStyle(diff).fontWeight,
          branchFont: getComputedStyle(branch).fontFamily,
          diffDisplay: getComputedStyle(diff).display,
          diffJustify: getComputedStyle(diff).justifyContent,
          addText: add.textContent,
          removeText: remove.textContent,
          addColor: getComputedStyle(add).color,
          removeColor: getComputedStyle(remove).color,
          diffRight: diffRect.right,
          addLeft: addRect.left,
          removeRight: removeRect.right,
          diffAfterName: diffRect.left >= nameRect.right,
          nameColor: getComputedStyle(name).color,
        };
        """
    )
    assert repo_row_metrics["text"] == "yolomux [feature/repo-row]"
    assert not repo_row_metrics["hasRetiredDelta"]
    assert repo_row_metrics["nameWeight"] in ("400", "normal")
    assert repo_row_metrics["branchWeight"] in ("400", "normal")
    assert repo_row_metrics["diffWeight"] == "800"
    assert "mono" in repo_row_metrics["branchFont"].lower()
    assert repo_row_metrics["diffDisplay"] == "flex"
    assert repo_row_metrics["diffJustify"] == "flex-end"
    assert repo_row_metrics["addText"] == "+5"
    assert repo_row_metrics["removeText"] == "-3"
    assert repo_row_metrics["addColor"] != repo_row_metrics["removeColor"]
    assert abs(repo_row_metrics["removeRight"] - repo_row_metrics["diffRight"]) <= 1
    assert repo_row_metrics["diffAfterName"]
    assert repo_row_metrics["nameColor"] != tree_metrics["collapsedColor"]


def test_editor_pane_does_not_shift_grid_when_legacy_body_class_is_present(browser, tmp_path):
    load_editor_pane_legacy_body_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const grid = document.getElementById('grid');
        const gridStyle = getComputedStyle(grid);
        const panel = document.querySelector('.file-editor-panel').getBoundingClientRect();
        return {
          paddingLeft: Number.parseFloat(gridStyle.paddingLeft),
          panelLeft: panel.left,
        };
        """
    )
    assert metrics["paddingLeft"] <= 10
    assert metrics["panelLeft"] <= 16


def test_codemirror_editor_controls_are_sized_and_aligned(browser, tmp_path):
    load_codemirror_editor_controls_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const firstTab = document.querySelector('.pane-tab').getBoundingClientRect();
        const actions = document.getElementById('editor-actions').getBoundingClientRect();
        const search = document.getElementById('search-field').getBoundingClientRect();
        const replace = document.getElementById('replace-field').getBoundingClientRect();
        const nextButton = document.querySelector('.cm-button[name="next"]').getBoundingClientRect();
        const previousButton = document.querySelector('.cm-button[name="prev"]').getBoundingClientRect();
        const allButton = document.querySelector('.cm-button[name="select"]').getBoundingClientRect();
        const replaceButton = document.querySelector('.cm-button[name="replace"]').getBoundingClientRect();
        const replaceAllButton = document.querySelector('.cm-button[name="replaceAll"]').getBoundingClientRect();
        const count = document.getElementById('search-count').getBoundingClientRect();
        const label = document.getElementById('match-label').getBoundingClientRect();
        const regexpLabel = document.querySelectorAll('.cm-search label')[1].getBoundingClientRect();
        const wordLabel = document.querySelectorAll('.cm-search label')[2].getBoundingClientRect();
        const labelStyle = getComputedStyle(document.getElementById('match-label'));
        const checkbox = document.getElementById('match-case').getBoundingClientRect();
        const markerContent = getComputedStyle(document.getElementById('wrapped-line'), '::before').content;
        const marker = document.getElementById('wrap-marker').getBoundingClientRect();
        const markerStyle = getComputedStyle(document.getElementById('wrap-marker'));
        const panelRing = getComputedStyle(document.querySelector('.file-editor-panel'));
        const searchLabel = getComputedStyle(document.querySelector('.cm-search'), '::before').content;
        const editorStyle = getComputedStyle(document.getElementById('cm-editor'));
        const themeStyle = getComputedStyle(document.querySelector('.file-editor-theme-panel'));
        const wrapStyle = getComputedStyle(document.querySelector('.file-editor-wrap-panel'));
        const findStyle = getComputedStyle(document.querySelector('.file-editor-find-panel'));
        const previewStyle = getComputedStyle(document.querySelector('[data-editor-mode="preview"]'));
        const closeStyle = getComputedStyle(document.querySelector('.file-editor-panel-close'));
        const searchCloseStyle = getComputedStyle(document.querySelector('.cm-dialog-close'));
        const syntaxProbe = Array.from(document.querySelectorAll('#light-syntax-probe span')).map(node => {
          const style = getComputedStyle(node);
          return {color: style.color, background: style.backgroundColor, border: style.borderTopColor};
        });
        const filePopoverStyle = getComputedStyle(document.getElementById('file-popover'));
        const filePopoverCopyStyle = getComputedStyle(document.getElementById('file-popover-copy'));
        const findControl = document.querySelector('.file-editor-find-panel').getBoundingClientRect();
        const wrapControl = document.querySelector('.file-editor-wrap-panel').getBoundingClientRect();
        const modeControl = document.querySelector('[data-editor-mode="preview"]').getBoundingClientRect();
        const modeButtonRects = Array.from(document.querySelectorAll('.file-editor-mode-control button')).map(button => button.getBoundingClientRect());
        const toolbarButtons = Array.from(document.querySelectorAll([
          '.file-editor-gutter-panel',
          '.file-editor-wrap-panel',
          '.file-editor-find-panel',
          '.file-editor-blame-panel',
          '.file-editor-diff-panel',
          '.file-editor-diff-expand-panel',
          '.file-editor-theme-panel',
          '.file-editor-reload-panel',
          '.file-editor-save-panel',
        ].join(',')));
        const modeIconDeltas = Array.from(document.querySelectorAll('.file-editor-mode-control button')).map(button => {
          const buttonRect = button.getBoundingClientRect();
          const iconRect = button.querySelector('.file-editor-icon').getBoundingClientRect();
          return Math.abs((buttonRect.top + buttonRect.height / 2) - (iconRect.top + iconRect.height / 2));
        });
        const toolbarIconDeltas = toolbarButtons
          .filter(button => button.querySelector('.file-editor-icon'))
          .map(button => {
            const buttonRect = button.getBoundingClientRect();
            const iconRect = button.querySelector('.file-editor-icon').getBoundingClientRect();
            return {
              cls: button.className,
              dx: Math.abs((buttonRect.left + buttonRect.width / 2) - (iconRect.left + iconRect.width / 2)),
              dy: Math.abs((buttonRect.top + buttonRect.height / 2) - (iconRect.top + iconRect.height / 2)),
            };
          });
        const toolbarButtonRects = toolbarButtons.map(button => button.getBoundingClientRect());
        const elementAtCenter = rect => document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        const tabRows = [];
        for (const tab of Array.from(document.querySelectorAll('.pane-tab'))) {
          const rect = tab.getBoundingClientRect();
          let row = tabRows.find(item => Math.abs(item.top - rect.top) <= 1);
          if (!row) {
            row = {top: rect.top, rights: []};
            tabRows.push(row);
          }
          row.rights.push(rect.right);
        }
        return {
          actionsTopDelta: Math.abs(actions.top - firstTab.top),
          searchWidth: search.width,
          replaceWidth: replace.width,
          nextWidth: nextButton.width,
          previousWidth: previousButton.width,
          allWidth: allButton.width,
          countText: document.getElementById('search-count').textContent,
          countColor: getComputedStyle(document.getElementById('search-count')).color,
          nextTitle: document.querySelector('.cm-button[name="next"]').title,
          previousTitle: document.querySelector('.cm-button[name="prev"]').title,
          searchFirstToggleGap: label.left - search.right,
          toggleCountGap: count.left - regexpLabel.right,
          previousNextGap: nextButton.left - previousButton.right,
          nextAllGap: allButton.left - nextButton.right,
          replaceReplaceAllGap: replaceAllButton.left - replaceButton.right,
          labelRegexpGap: wordLabel.left - label.right,
          regexpWordGap: regexpLabel.left - wordLabel.right,
          replaceLeftDelta: Math.abs(search.left - replace.left),
          replaceWidthDelta: Math.abs(search.width - replace.width),
          checkboxCenterDelta: Math.abs((checkbox.top + checkbox.height / 2) - (label.top + label.height / 2)),
          labelFontFamily: labelStyle.fontFamily,
          labelFontSize: Number.parseFloat(labelStyle.fontSize),
          markerContent,
          markerHeight: marker.height,
          markerColor: markerStyle.color,
          // the focus ring is the translucent gutter border (color-mix of --panel-ring-color).
          panelRingBorderColor: getComputedStyle(document.querySelector('.file-editor-panel')).borderTopColor,
          searchLabel,
          editorBg: editorStyle.backgroundColor,
          editorColor: editorStyle.color,
          themeBg: themeStyle.backgroundColor,
          themeBorderColor: themeStyle.borderTopColor,
          themeColor: themeStyle.color,
          wrapBg: wrapStyle.backgroundColor,
          wrapBorderColor: wrapStyle.borderTopColor,
          findBg: findStyle.backgroundColor,
          previewBg: previewStyle.backgroundColor,
          closeBg: closeStyle.backgroundColor,
          searchCloseColor: searchCloseStyle.color,
          searchCloseBg: searchCloseStyle.backgroundColor,
          syntaxColorCount: new Set(syntaxProbe.map(item => item.color)).size,
          keywordColor: syntaxProbe[0].color,
          stringColor: syntaxProbe[1].color,
          functionColor: syntaxProbe[3].color,
          commentColor: syntaxProbe[4].color,
          headingColor: syntaxProbe[5].color,
          inlineCodeColor: syntaxProbe[6].color,
          inlineCodeBg: syntaxProbe[6].background,
          inlineCodeBorder: syntaxProbe[6].border,
          listMarkerColor: syntaxProbe[7].color,
          linkColor: syntaxProbe[8].color,
          filePopoverPointerEvents: filePopoverStyle.pointerEvents,
          filePopoverCopyPointerEvents: filePopoverCopyStyle.pointerEvents,
          findControlClickable: Boolean(elementAtCenter(findControl)?.closest?.('.file-editor-find-panel')),
          wrapControlClickable: Boolean(elementAtCenter(wrapControl)?.closest?.('.file-editor-wrap-panel')),
          previewControlClickable: Boolean(elementAtCenter(modeControl)?.closest?.('[data-editor-mode="preview"]')),
          modeButtonTopSpread: Math.max(...modeButtonRects.map(rect => rect.top)) - Math.min(...modeButtonRects.map(rect => rect.top)),
          modeButtonHeightSpread: Math.max(...modeButtonRects.map(rect => rect.height)) - Math.min(...modeButtonRects.map(rect => rect.height)),
          modeIconCenterMaxDelta: Math.max(...modeIconDeltas),
          toolbarButtonTopSpread: Math.max(...toolbarButtonRects.map(rect => rect.top)) - Math.min(...toolbarButtonRects.map(rect => rect.top)),
          toolbarButtonHeightSpread: Math.max(...toolbarButtonRects.map(rect => rect.height)) - Math.min(...toolbarButtonRects.map(rect => rect.height)),
          toolbarIconCenterMaxDx: Math.max(...toolbarIconDeltas.map(item => item.dx)),
          toolbarIconCenterMaxDy: Math.max(...toolbarIconDeltas.map(item => item.dy)),
          toolbarIconDeltas,
          tabRowCount: tabRows.length,
          lowerTabRowsUseFullWidth: tabRows.slice(1).some(row => Math.max(...row.rights) > actions.left + 20),
        };
        """
    )
    assert metrics["actionsTopDelta"] <= 2
    assert metrics["searchWidth"] >= 120
    assert metrics["searchWidth"] <= 210
    assert metrics["replaceWidth"] >= 120
    assert metrics["nextWidth"] <= 45
    assert metrics["previousWidth"] <= 75
    assert metrics["allWidth"] <= 38
    assert metrics["countText"] == "3/102"
    assert metrics["countColor"] != "rgb(0, 0, 0)"
    assert metrics["nextTitle"] == "Next match (Enter)"
    assert metrics["previousTitle"] == "Previous match (Shift+Enter)"
    assert 0 <= metrics["searchFirstToggleGap"] <= 8
    assert 0 <= metrics["toggleCountGap"] <= 10
    assert metrics["previousNextGap"] <= 6
    assert metrics["nextAllGap"] <= 6
    assert metrics["replaceReplaceAllGap"] <= 4
    assert metrics["labelRegexpGap"] <= 4
    assert metrics["regexpWordGap"] <= 4
    assert metrics["replaceLeftDelta"] <= 1.5
    assert metrics["replaceWidthDelta"] <= 2
    assert metrics["checkboxCenterDelta"] <= 1.5
    assert (
        "Arial Narrow" in metrics["labelFontFamily"]
        or "Roboto Condensed" in metrics["labelFontFamily"]
        or metrics["labelFontSize"] <= 11
    )
    assert metrics["markerContent"] in ("none", '""')
    assert metrics["markerHeight"] > 0
    assert metrics["markerColor"] != "rgb(0, 0, 0)"
    # the active pane's focus ring is the translucent gutter border; assert it shows a
    # colored (non-transparent) ring color (color-mix of --panel-ring-color at --pane-ring-opacity).
    assert metrics["panelRingBorderColor"] not in ("rgba(0, 0, 0, 0)", "transparent")
    assert metrics["searchLabel"] in ("none", '""')
    assert metrics["editorBg"] != "rgb(15, 17, 21)"
    assert metrics["editorColor"] != "rgb(228, 232, 238)"
    assert metrics["themeBorderColor"] != "rgba(0, 0, 0, 0)"
    assert metrics["themeColor"] != metrics["editorColor"]
    assert metrics["wrapBorderColor"] != "rgba(0, 0, 0, 0)"
    assert metrics["themeBg"] != "rgba(0, 0, 0, 0)"
    assert metrics["wrapBg"] not in ("rgb(255, 255, 255)", "rgb(221, 244, 255)")
    assert metrics["findBg"] != "rgba(0, 0, 0, 0)"
    assert metrics["previewBg"] != "rgba(0, 0, 0, 0)"
    assert metrics["wrapBg"] != metrics["findBg"]
    assert metrics["closeBg"] != metrics["editorBg"]
    assert metrics["closeBg"] != "rgb(255, 235, 233)"
    assert metrics["searchCloseColor"] != "rgb(255, 255, 255)"
    assert metrics["searchCloseColor"] != metrics["searchCloseBg"]
    assert metrics["syntaxColorCount"] >= 6
    assert metrics["keywordColor"] != metrics["stringColor"]
    assert metrics["functionColor"] != metrics["keywordColor"]
    assert metrics["inlineCodeColor"] != metrics["headingColor"]
    assert metrics["inlineCodeColor"] != metrics["linkColor"]
    assert metrics["inlineCodeColor"] != metrics["listMarkerColor"]
    assert metrics["headingColor"] != metrics["linkColor"]
    assert metrics["commentColor"] == metrics["listMarkerColor"]
    assert metrics["inlineCodeBg"] != "rgba(0, 0, 0, 0)"
    assert metrics["inlineCodeBorder"] != "rgba(0, 0, 0, 0)"
    assert metrics["filePopoverPointerEvents"] == "auto"  # popover-open tab: interactive when visible
    assert metrics["filePopoverCopyPointerEvents"] == "auto"
    assert metrics["findControlClickable"]
    assert metrics["wrapControlClickable"]
    assert metrics["previewControlClickable"]
    assert metrics["modeButtonTopSpread"] <= 1
    assert metrics["modeButtonHeightSpread"] <= 1
    assert metrics["modeIconCenterMaxDelta"] <= 1.5
    assert metrics["toolbarButtonTopSpread"] <= 1
    assert metrics["toolbarButtonHeightSpread"] <= 1
    assert metrics["toolbarIconCenterMaxDx"] <= 1.5, metrics["toolbarIconDeltas"]
    assert metrics["toolbarIconCenterMaxDy"] <= 1.5, metrics["toolbarIconDeltas"]


def test_editor_diff_ref_reset_is_visible_and_hittable(browser, tmp_path):
    load_editor_diff_ref_toolbar_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const toolbar = document.querySelector('.file-editor-toolbar').getBoundingClientRect();
        const leftZone = document.querySelector('.file-editor-toolbar-left').getBoundingClientRect();
        const centerZone = document.querySelector('.file-editor-toolbar-center').getBoundingClientRect();
        const rightZone = document.querySelector('.file-editor-toolbar-right').getBoundingClientRect();
        const gutter = document.getElementById('gutter-button').getBoundingClientRect();
        const wrap = document.getElementById('wrap-button').getBoundingClientRect();
        const wrapIcon = document.querySelector('#wrap-button .file-editor-icon-wrap');
        const diff = document.getElementById('diff-button').getBoundingClientRect();
        const expand = document.getElementById('diff-expand-button').getBoundingClientRect();
        const font = document.getElementById('font-panel').getBoundingClientRect();
        const mode = document.getElementById('mode-control').getBoundingClientRect();
        const diffStyle = getComputedStyle(document.getElementById('diff-button'));
        const panel = document.getElementById('diff-ref-panel').getBoundingClientRect();
        const controls = document.querySelector('[data-diff-ref-controls]').getBoundingClientRect();
        const from = document.getElementById('from-ref').getBoundingClientRect();
        const fromInput = document.getElementById('from-ref');
        const to = document.getElementById('to-ref').getBoundingClientRect();
        const reset = document.getElementById('reset-ref').getBoundingClientRect();
        const resetStyle = getComputedStyle(document.getElementById('reset-ref'));
        const panelStyle = getComputedStyle(document.getElementById('diff-ref-panel'));
        const hit = document.elementFromPoint(reset.left + reset.width / 2, reset.top + reset.height / 2);
        return {
          toolbarLeft: toolbar.left,
          toolbarRight: toolbar.right,
          toolbarCenter: toolbar.left + toolbar.width / 2,
          leftZoneLeft: leftZone.left,
          leftZoneRight: leftZone.right,
          centerZoneCenter: centerZone.left + centerZone.width / 2,
          rightZoneLeft: rightZone.left,
          rightZoneRight: rightZone.right,
          gutterLeft: gutter.left,
          gutterRight: gutter.right,
          wrapLeft: wrap.left,
          wrapRight: wrap.right,
          wrapHasIcon: Boolean(wrapIcon),
          diffLeft: diff.left,
          diffRight: diff.right,
          diffText: document.getElementById('diff-button').textContent.trim(),
          expandLeft: expand.left,
          expandRight: expand.right,
          fontCenter: font.left + font.width / 2,
          modeLeft: mode.left,
          modeRight: mode.right,
          diffBg: diffStyle.backgroundColor,
          diffBorder: diffStyle.borderTopColor,
          panelRight: panel.right,
          panelLeft: panel.left,
          controlsRight: controls.right,
          fromValue: fromInput.value,
          fromClientWidth: fromInput.clientWidth,
          fromScrollWidth: fromInput.scrollWidth,
          fromWidth: from.width,
          toRight: to.right,
          resetLeft: reset.left,
          resetRight: reset.right,
          resetWidth: reset.width,
          resetDisplay: resetStyle.display,
          panelOverflow: panelStyle.overflow,
          hitReset: Boolean(hit?.closest?.('#reset-ref')),
          hitId: hit?.id || '',
          hitClass: String(hit?.className || ''),
          hitText: hit?.textContent || '',
        };
        """
    )
    assert metrics["leftZoneLeft"] <= metrics["toolbarLeft"] + 8, metrics
    assert abs(metrics["centerZoneCenter"] - metrics["toolbarCenter"]) <= 2, metrics
    assert abs(metrics["fontCenter"] - metrics["toolbarCenter"]) <= 2, metrics
    assert metrics["rightZoneRight"] >= metrics["toolbarRight"] - 8, metrics
    assert metrics["modeLeft"] >= metrics["centerZoneCenter"] + 20, metrics
    assert metrics["modeLeft"] >= metrics["leftZoneRight"], metrics
    assert metrics["gutterLeft"] <= metrics["toolbarLeft"] + 8, metrics
    assert 0 <= metrics["wrapLeft"] - metrics["gutterRight"] <= 6, metrics
    assert metrics["wrapHasIcon"], metrics
    assert 0 <= metrics["diffLeft"] - metrics["wrapRight"] <= 6, metrics
    assert metrics["diffText"] == "Differ", metrics
    assert 0 <= metrics["expandLeft"] - metrics["diffRight"] <= 6, metrics
    assert 0 <= metrics["panelLeft"] - metrics["expandRight"] <= 6, metrics
    assert metrics["diffBg"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["diffBorder"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["fromValue"] == "2eb21b3339/HEAD", metrics
    assert metrics["fromScrollWidth"] <= metrics["fromClientWidth"] + 2, metrics
    assert metrics["fromWidth"] >= 100, metrics
    assert metrics["resetDisplay"] != "none"
    assert metrics["resetWidth"] >= 18
    assert metrics["panelOverflow"] == "visible"
    assert 0 <= metrics["resetLeft"] - metrics["toRight"] <= 5
    assert metrics["resetRight"] <= metrics["panelRight"] + 1
    assert metrics["controlsRight"] <= metrics["panelRight"] + 1
    assert metrics["panelRight"] <= metrics["toolbarRight"] + 1
    assert metrics["hitReset"], metrics


def test_codemirror_word_wrap_toggle_keeps_existing_content_visible(browser, tmp_path):
    load_codemirror_wrap_toggle_fixture(browser, tmp_path)
    ready = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__wrapRegressionReady.then(
          () => done({ok: true}),
          error => done({ok: false, message: String(error)})
        );
        """
    )
    assert ready["ok"], ready
    before = browser.execute_script(
        """
        const panel = document.getElementById('wrap-regression-panel');
        const content = panel.querySelector('.cm-content');
        return {
          sameView: panel._cmView === window.__wrapRegressionInitialView,
          renderCalls: window.__wrapRegressionRenderCalls,
          doc: panel._cmView.state.doc.toString(),
          visibleText: content.textContent,
          lineWrapping: content.classList.contains('cm-lineWrapping'),
          buttonActive: panel.querySelector('.file-editor-wrap-panel').classList.contains('active'),
          contentHeight: content.getBoundingClientRect().height,
        };
        """
    )
    assert before["sameView"]
    assert before["renderCalls"] == 0
    assert "This line must stay visible" in before["doc"]
    assert "This line must stay visible" in before["visibleText"]
    assert before["lineWrapping"] is False
    assert before["buttonActive"] is False
    assert before["contentHeight"] > 0

    after = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const panel = document.getElementById('wrap-regression-panel');
        panel.querySelector('.file-editor-wrap-panel').click();
        let attempts = 0;
        const finish = () => {
          const content = panel.querySelector('.cm-content');
          const metrics = {
            sameView: panel._cmView === window.__wrapRegressionInitialView,
            renderCalls: window.__wrapRegressionRenderCalls,
            doc: panel._cmView.state.doc.toString(),
            visibleText: content.textContent,
            lineWrapping: Boolean(panel.querySelector('.cm-lineWrapping')),
            panelWrap: panel.classList.contains('editor-wrap'),
            buttonActive: panel.querySelector('.file-editor-wrap-panel').classList.contains('active'),
            contentHeight: content.getBoundingClientRect().height,
            editorClass: panel.querySelector('.cm-editor')?.className || '',
            scrollerClass: panel.querySelector('.cm-scroller')?.className || '',
            contentClass: content.className,
            contentWhiteSpace: getComputedStyle(content).whiteSpace,
            reconfigCalls: window.__wrapRegressionReconfigCalls,
            errors: window.__wrapRegressionErrors,
            optionViews: panel._cmEditorOptionViews?.length || 0,
            loadingText: panel.querySelector('.file-editor-codemirror-panel').textContent,
          };
          if (metrics.lineWrapping || attempts > 20) done(metrics);
          else {
            attempts += 1;
            requestAnimationFrame(finish);
          }
        };
        requestAnimationFrame(finish);
        """
    )
    assert after["sameView"], after
    assert after["renderCalls"] == 0, after
    assert "This line must stay visible" in after["doc"]
    assert "This line must stay visible" in after["visibleText"]
    assert after["lineWrapping"] is True, (
        f"contentClass={after['contentClass']} "
        f"contentWhiteSpace={after['contentWhiteSpace']} "
        f"reconfigCalls={after['reconfigCalls']} "
        f"errors={after['errors']} "
        f"optionViews={after['optionViews']}"
    )
    assert after["panelWrap"] is True
    assert after["buttonActive"] is True
    assert after["contentHeight"] > 0
    assert after["reconfigCalls"], after
    assert after["reconfigCalls"][-1]["result"] is True, after
    assert "cm-lineWrapping" in after["reconfigCalls"][-1]["classes"]
    assert after["errors"] == []
    assert "loading CodeMirror" not in after["loadingText"]


def test_codemirror_bundle_exports_decoration_for_html_semantic_marks(browser, tmp_path):
    load_codemirror_bundle_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const cm = window.YOLOmuxCodeMirror || {};
            return typeof cm.Decoration?.mark === 'function'
              && typeof cm.Decoration?.set === 'function'
              && typeof cm.MergeView === 'function'
              && typeof cm.unifiedMergeView === 'function';
            """
        )
    )
    metrics = browser.execute_script(
        """
        const cm = window.YOLOmuxCodeMirror || {};
        const mark = cm.Decoration?.mark?.({attributes: {style: 'font-weight:700'}});
        return {
          hasDecoration: typeof cm.Decoration?.mark === 'function',
          hasDecorationSet: typeof cm.Decoration?.set === 'function',
          hasMergeView: typeof cm.MergeView === 'function',
          hasUnifiedMergeView: typeof cm.unifiedMergeView === 'function',
          markWorks: Boolean(mark && typeof mark.range === 'function'),
        };
        """
    )
    assert metrics["hasDecoration"]
    assert metrics["hasDecorationSet"]
    assert metrics["hasMergeView"]
    assert metrics["hasUnifiedMergeView"]
    assert metrics["markWorks"]


def test_clicking_finder_does_not_change_terminal_pane_toolbar(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    light_metrics = browser.execute_script(
        """
        document.body.classList.add('theme-light');
        const detail = document.querySelector('#terminal-panel .panel-detail-row');
        const meta = detail.querySelector('.meta');
        const action = document.querySelector('#terminal-panel .pane-actions');
        return {
          detailBg: getComputedStyle(detail).backgroundColor,
          metaColor: getComputedStyle(meta).color,
          actionColor: getComputedStyle(action).color,
          actionBg: getComputedStyle(action).backgroundColor,
        };
        """
    )
    # The Info Bar is the tinted (active-accent-derived) chrome strip with readable dark meta text —
    # assert the readability relationship, not a pinned green, so the active_color picker doesn't break it.
    assert light_metrics["detailBg"] != light_metrics["metaColor"]
    assert light_metrics["metaColor"] == "rgb(31, 41, 55)"
    assert light_metrics["actionColor"] == "rgb(31, 41, 55)"
    assert light_metrics["actionColor"] != light_metrics["actionBg"]
    before = browser.execute_script(
        """
        const toolbar = document.getElementById('terminal-toolbar');
        const rect = toolbar.getBoundingClientRect();
        return {
          html: toolbar.innerHTML,
          display: getComputedStyle(toolbar).display,
          buttonCount: toolbar.querySelectorAll('.tab').length,
          left: rect.left,
          width: rect.width,
        };
        """
    )
    browser.find_element("id", "finder-panel").click()
    after = browser.execute_script(
        """
        const toolbar = document.getElementById('terminal-toolbar');
        const rect = toolbar.getBoundingClientRect();
        return {
          html: toolbar.innerHTML,
          display: getComputedStyle(toolbar).display,
          buttonCount: toolbar.querySelectorAll('.tab').length,
          left: rect.left,
          width: rect.width,
        };
        """
    )
    assert after == before


# — light-mode surface regression guard. The recurring light-mode bug class is a
# component rule that hardcodes a DARK color literal with no body.theme-light / body.editor-theme-light
# counterpart, so it renders as a dark box (or invisible pale text) on the white surface. The earlier
# white-on-white miss slipped through because the test measured BACKGROUNDS but never the nested TEXT
# color. This builds each fixed surface in light mode and asserts (a) container backgrounds are LIGHT
# and (b) text vs its surface meets a real contrast ratio — the same thing a human reading it needs.
LIGHT_MODE_SURFACES = """
<div class="command-palette-dialog" id="cp-dlg">
  <input class="command-palette-input" id="cp-inp" value="x">
  <button class="command-palette-row active" id="cp-row">
    <span class="command-palette-group" id="cp-grp">FILES</span>
    <span class="command-palette-detail" id="cp-det">detail</span>
    <span class="command-palette-keybinding" id="cp-kb">^P</span>
  </button>
</div>
<div class="keyboard-shortcuts-dialog" id="ks-dlg">
  <div class="keyboard-shortcut-row"><span>act</span><kbd id="ks-kbd">Ctrl</kbd></div>
</div>
<div class="preferences-global-reset" id="gr">
  <div class="preferences-global-reset-title" id="gr-title">Reset</div>
  <div class="preferences-global-reset-warning" id="gr-warn">warn</div>
</div>
<span class="agent-icon codex" id="agent-ico">A</span>
<span class="session-state-badge" id="badge-neutral">run</span>
<span class="session-state-badge session-state-working" id="badge-working">working</span>
<span class="session-state-badge session-state-done" id="badge-done">done</span>
<span class="session-yolo-marker inactive" id="ym-inactive">YO</span>
<button class="pane-tab file-missing" id="fm-tab">
  <span class="session-button-dir" id="fm-dir">gone</span>
  <span class="file-tab-missing-badge" id="fm-badge">!</span>
</button>
<div class="server-update-banner" id="sub">
  update <button class="server-update-banner-dismiss" id="sub-dismiss">x</button>
</div>
<div class="file-tree-row repo-non-main"><span class="file-tree-name" id="rnm-name">repo</span></div>
<div class="file-tree-row indexed-directory">
  <span class="file-tree-name" id="idx-name">dir</span>
  <span class="file-tree-git-status" id="idx-status">INDEXED</span>
</div>
<input class="file-tree-rename-input" id="rename-inp" value="name">
<div class="session-rename-dialog">
  <input class="session-rename-input" id="session-rename-inp" value="session">
  <div class="session-rename-actions"><button id="session-rename-cancel">Cancel</button></div>
</div>
<div class="yoagent-message-body markdown-body"><pre id="md-pre"><code>code</code></pre></div>
<div class="info-pane" id="info-pane">
  <div class="info-row header"><div class="info-cell" id="info-hdr">Session</div></div>
  <div class="info-row"><div class="info-cell" id="info-row-text">main</div>
    <div class="info-cell"><a id="info-link" href="#">branch</a></div></div>
  <div class="info-row current"><div class="info-cell" id="info-cur">current</div></div>
  <div class="info-list info-tree-list" id="info-content">
    <div class="info-tree">
      <details class="info-tree-group" open>
        <summary id="info-tree-summary"><span class="info-tree-group-dimension">GitHub PR:</span><span class="info-tree-group-label-line"><span class="info-tree-group-label">#1 full title</span><span class="info-tree-group-child-count" id="info-tree-child-count">(2 branches)</span></span></summary>
        <div class="info-tree-group-children">
          <div class="info-tree-record" id="info-tree-record"><div class="info-tree-record-main"><div class="info-tree-field info-tree-field-pr" id="info-tree-desc"><span class="info-tree-field-label" id="info-tree-label">GitHub PR:</span><span class="info-tree-field-value"><a href="#">#1</a> description</span></div><div class="info-tree-field info-tree-field-tab"><span class="info-tree-field-label">Tab(tmux session):</span><span class="info-tree-field-value"><button type="button" class="info-tree-action-link" id="info-tree-session">tab</button></span></div></div></div>
        </div>
      </details>
    </div>
  </div>
</div>
"""


def light_mode_surfaces_fixture_html(body_class):
    css = app_css()
    return f"""
    <!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>
    <body class="{body_class}" style="background:#fff">{LIGHT_MODE_SURFACES}</body></html>
    """


def _contrast_ratio(rgb_a, rgb_b):
    def rel_lum(css_rgb):
        nums = [int(n) for n in re.findall(r"\d+", css_rgb)[:3]]

        def chan(c):
            c = c / 255.0
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * chan(nums[0]) + 0.7152 * chan(nums[1]) + 0.0722 * chan(nums[2])

    la, lb = rel_lum(rgb_a), rel_lum(rgb_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_light_mode_surfaces_are_readable_not_dark_boxes(browser, tmp_path):
    page = tmp_path / "light-surfaces.html"
    page.write_text(light_mode_surfaces_fixture_html("theme-light"), encoding="utf-8")
    browser.get(page.as_uri())
    style = browser.execute_script(
        """
        const out = {};
        for (const el of document.querySelectorAll('[id]')) {
          const s = getComputedStyle(el);
          out[el.id] = {color: s.color, bg: s.backgroundColor};
        }
        const bodyStyle = getComputedStyle(document.body);
        out.bodyVars = {
          infoTreeBorder: bodyStyle.getPropertyValue('--info-tree-border').trim(),
          infoTreeLine: bodyStyle.getPropertyValue('--info-tree-line').trim(),
          infoRecordBorder: bodyStyle.getPropertyValue('--info-tree-record-border').trim(),
        };
        return out;
        """
    )

    # (a) Surfaces that were dark boxes must now have LIGHT backgrounds (luminance high).
    def _lum(css_rgb):
        nums = [int(n) for n in re.findall(r"\d+", css_rgb)[:3]]
        return 0.2126 * nums[0] + 0.7152 * nums[1] + 0.0722 * nums[2]

    for box in ("cp-dlg", "ks-dlg", "sub", "rename-inp", "session-rename-inp", "session-rename-cancel", "md-pre", "info-pane", "info-content", "info-tree-summary"):
        assert _lum(style[box]["bg"]) > 180, f"{box} background must be light in light mode, got {style[box]['bg']}"
    assert style["bodyVars"]["infoTreeBorder"] == "#8793a3", style["bodyVars"]
    assert style["bodyVars"]["infoTreeLine"] == "rgb(100 116 139 / 0.16)", style["bodyVars"]
    assert style["bodyVars"]["infoRecordBorder"] == style["bodyVars"]["infoTreeLine"], style["bodyVars"]
    assert style["bodyVars"]["infoTreeLine"] != style["bodyVars"]["infoTreeBorder"], style["bodyVars"]

    # (b) Text must contrast with its surface. Where the element bg is transparent, it sits on the white page.
    page_white = "rgb(255, 255, 255)"
    text_checks = {
        "cp-row": "cp-dlg", "cp-grp": "cp-dlg", "cp-det": "cp-dlg", "cp-kb": "cp-dlg",
        "ks-kbd": "ks-kbd", "gr-title": "gr", "gr-warn": "gr", "agent-ico": None,
        "badge-neutral": "badge-neutral", "badge-working": "badge-working", "badge-done": "badge-done", "ym-inactive": "ym-inactive",
        "fm-dir": "fm-tab", "fm-badge": "fm-tab", "sub": "sub", "sub-dismiss": "sub",
        "rnm-name": None, "idx-name": None, "idx-status": None, "rename-inp": "rename-inp", "session-rename-inp": "session-rename-inp", "session-rename-cancel": "session-rename-cancel", "md-pre": "md-pre",
        # the YO!info table — rows/header/current/links must read on the light pane.
        "info-hdr": None, "info-row-text": None, "info-link": None, "info-cur": None,
        # YO!info leaf rows use a transparent fill and must stay readable on the light pane surface.
        "info-tree-summary": "info-tree-summary", "info-tree-child-count": "info-tree-summary", "info-tree-record": "info-content", "info-tree-label": "info-content", "info-tree-session": "info-content", "info-tree-desc": "info-content",
    }
    for eid, bg_id in text_checks.items():
        bg = style[bg_id]["bg"] if bg_id else page_white
        if "rgba(0, 0, 0, 0)" in bg or bg == "transparent":
            bg = page_white
        ratio = _contrast_ratio(style[eid]["color"], bg)
        assert ratio >= 3.0, f"{eid}: text {style[eid]['color']} on {bg} contrast {ratio:.1f} < 3.0 (dark-box/invisible)"


def test_light_editor_image_backdrop_is_light(browser, tmp_path):
    page = tmp_path / "light-editor-image.html"
    page.write_text(
        light_mode_surfaces_fixture_html("editor-theme-light").replace(
            LIGHT_MODE_SURFACES,
            '<div class="file-editor-image-panel" id="imgp"><img class="file-editor-image" id="img" src="#"></div>',
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri())
    style = browser.execute_script(
        "return {panel: getComputedStyle(document.getElementById('imgp')).backgroundColor,"
        " img: getComputedStyle(document.getElementById('img')).backgroundColor};"
    )

    def _lum(css_rgb):
        nums = [int(n) for n in re.findall(r"\d+", css_rgb)[:3]]
        return 0.2126 * nums[0] + 0.7152 * nums[1] + 0.0722 * nums[2]

    assert _lum(style["panel"]) > 180, f"editor-light image panel must be light, got {style['panel']}"
    assert _lum(style["img"]) > 180, f"editor-light image backdrop must be light, got {style['img']}"


def codemirror_search_panel_fixture_html():
    css = app_css()
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <script src="{bundle_uri}"></script>
        <style>.file-editor-codemirror {{ width: 680px; height: 220px; }}</style>
      </head>
      <body class="editor-theme-light">
        <div class="panel file-editor-panel active-pane">
          <div class="file-editor-content file-editor-codemirror" id="cm-host"></div>
        </div>
        <script>
          (function() {{
            const CM = window.YOLOmuxCodeMirror;
            const exts = CM.search ? [CM.search()] : [];
            const view = new CM.EditorView({{
              state: CM.EditorState.create({{doc: "hello world\\nfind me\\n", extensions: exts}}),
              parent: document.getElementById('cm-host'),
            }});
            CM.openSearchPanel(view);
          }})();
        </script>
      </body>
    </html>
    """


def load_codemirror_search_panel_fixture(browser, tmp_path):
    page = tmp_path / "cm-search-panel.html"
    page.write_text(codemirror_search_panel_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def test_codemirror_search_toggle_labels_collapse_to_glyph_not_overflow(browser, tmp_path):
    # CodeMirror's baseTheme injects `.cm-panel.cm-search label { font-size: 80% }` at RUNTIME, a
    # specificity TIE with our label rule that wins on source order — un-hiding the native toggle
    # text ("match case"/"regexp"/"by word") so it overflows the 24px box and collides with our
    # compact ::after glyph (images 019/021). The +1-class override must keep the label font-size 0.
    load_codemirror_search_panel_fixture(browser, tmp_path)
    labels = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const panel = document.querySelector('.cm-search');
            if (!panel) return false;
            const labels = [...panel.querySelectorAll('label')].map(l => ({
              fontSize: getComputedStyle(l).fontSize,
              boxWidth: Math.round(l.getBoundingClientRect().width),
              scrollWidth: l.scrollWidth,
            }));
            return labels.length ? labels : false;
            """
        )
    )
    assert labels, "search panel did not open (CodeMirror bundle missing search export?)"
    assert len(labels) == 3
    for lb in labels:
        assert lb["fontSize"] == "0px", f"toggle label native text must be hidden (font-size 0), got {lb['fontSize']}"
        assert lb["scrollWidth"] <= lb["boxWidth"] + 1, f"toggle label overflows its 24px box: {lb}"


def test_needs_attention_pane_stays_red_when_focused_and_yolo_ready(browser, tmp_path):
    # image 20260603-028: focusing/hovering a needs-attention (red) pane on a --dangerously-yolo server
    # made it `typing-ready-pane yolo-ready-pane needs-input-pane`; the yolo-ready green --panel-ring-color
    # (0,3,0) out-specified the needs red (0,2,0), so the alert went GREEN. The red must always win.
    css = app_css()
    combos = [
        "needs-input-pane",                                       # unfocused alert -> red (ring)
        "active-pane needs-input-pane",                           # focused alert -> red
        "typing-ready-pane yolo-ready-pane needs-input-pane",     # the bug: hovered + yolo + alert -> red
        "active-pane yolo-ready-pane needs-blocked-pane",
    ]
    panels = "".join(f'<div class="panel {c}" id="p{i}" style="width:160px;height:60px"></div>' for i, c in enumerate(combos))
    page = tmp_path / "needs-ring.html"
    page.write_text(f"<!doctype html><html><head><meta charset=utf-8><style>{css}</style></head>"
                    f'<body class="theme-dark">{panels}</body></html>', encoding="utf-8")
    browser.get(page.as_uri())
    rings = browser.execute_script(
        """
        const out = {};
        document.querySelectorAll('.panel').forEach(p => {
          out[p.id] = getComputedStyle(p).getPropertyValue('--panel-ring-color').trim();
        });
        return out;
        """
    )
    # Every needs-attention pane resolves the red ring color, regardless of focus/yolo-ready state.
    for pid, ring in rings.items():
        assert ring.lower() == '#ff3347', f"{pid}: needs-attention pane must keep the red ring (#ff3347), got {ring}"
