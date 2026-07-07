import json
import re
from pathlib import Path

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401
from tools.static_build import build_asset
from yolomux_lib.locales import SHIPPED_LOCALES


def test_browser_wait_timeout_has_one_xdist_only_floor():
    assert browser_wait_timeout(5, worker="gw0") == XDIST_BROWSER_WAIT_FLOOR_SECONDS
    assert browser_wait_timeout(15, worker="gw0") == 15
    assert browser_wait_timeout(5, worker="") == 5


def test_browser_document_wait_helper_reports_values_and_timeout_context(browser, tmp_path):
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


def test_live_runtime_bundle_readiness_has_one_ready_and_error_owner():
    class FixtureBrowser:
        def __init__(self, state):
            self.state = state

        def execute_script(self, _script):
            return self.state

    ready = {"sentinel": True, "grid": True, "appRoot": False, "lateFunction": True, "url": "file:///tmp/live-runtime-boot.html", "errors": [], "rejections": []}
    assert wait_for_live_runtime_bundle(FixtureBrowser(ready), timeout=0.01) == ready

    replay_ready = {"sentinel": True, "grid": False, "appRoot": True, "lateFunction": True, "url": "file:///tmp/live-runtime-boot.html", "errors": [], "rejections": []}
    assert wait_for_live_runtime_bundle(FixtureBrowser(replay_ready), timeout=0.01) == replay_ready

    redirected = {"sentinel": False, "grid": False, "appRoot": False, "lateFunction": False, "url": "file:///login", "errors": [], "rejections": []}
    assert wait_for_live_runtime_bundle(
        FixtureBrowser(redirected),
        timeout=0.01,
        expected_url="file:///tmp/live-runtime-boot.html",
        expected_redirect_paths=("/login",),
    ) == redirected

    unexpected_redirect = {**redirected, "url": "file:///broken"}
    assert live_runtime_bundle_ready_state(unexpected_redirect, "/tmp/live-runtime-boot.html", ("/login",)) is False
    chrome_error = {**redirected, "url": "chrome-error://chromewebdata/"}
    assert live_runtime_bundle_ready_state(chrome_error, "/tmp/live-runtime-boot.html") is False
    assert live_runtime_bundle_ready_state(chrome_error, "/tmp/live-runtime-boot.html", ("/login",)) == chrome_error

    failed = {"sentinel": False, "grid": True, "appRoot": False, "lateFunction": False, "url": "file:///tmp/live-runtime-boot.html", "errors": [{"message": "bundle boom"}], "rejections": []}
    with pytest.raises(AssertionError, match="bundle boom"):
        wait_for_live_runtime_bundle(FixtureBrowser(failed), timeout=0.01)


def test_live_runtime_raw_html_builder_has_no_external_callers():
    external_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "tests").glob("test_browser_*.py"))
    )
    assert "live_runtime_boot_fixture" + "_html(" not in external_sources


def test_static_browser_fixtures_have_one_write_and_navigation_owner():
    source = (REPO_ROOT / "tests" / "browser_helpers" / "browser_layout.py").read_text(encoding="utf-8")

    assert source.count("page.write_text(") == 2  # one static helper plus the distinct full-bundle loader
    assert source.count("browser.get(page.as_uri())") == 1
    assert len(re.findall(r"^\s+load_static_html_fixture\(browser, tmp_path,", source, re.MULTILINE)) == 16


def test_isolated_tmux_runtime_supports_named_commands_dimensions_and_cleanup(monkeypatch, tmp_path):
    session = f"yt-named-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    runtime = start_isolated_tmux_runtime(
        monkeypatch,
        tmp_path,
        session_commands={session: "printf 'named command ready\\n'; exec sh"},
        columns=93,
        rows=27,
    )
    socket_dir = runtime.socket_dir
    try:
        ready, panes = wait_for_isolated_tmux_panes(
            runtime,
            [session],
            lambda captures: "named command ready" in captures[session],
            timeout=5,
            poll_interval=0.05,
        )
        dimensions = run_isolated_tmux(
            runtime,
            "display-message",
            "-p",
            "-t",
            f"{session}:",
            "#{window_width}x#{window_height}",
            timeout=5,
        )
        assert ready, panes
        assert dimensions.returncode == 0, dimensions.stderr or dimensions.stdout
        assert dimensions.stdout.strip() == "93x27"
    finally:
        stop_isolated_tmux_runtime(runtime)
    assert not socket_dir.exists()


def test_agent_prompt_browser_cases_share_the_isolated_tmux_runtime_owner():
    source = Path(__file__).read_text(encoding="utf-8")
    function_sources = {}
    for test_name in (
        "test_mock_agent_prompt_payload_renders_ask_attention_in_live_browser",
        "test_real_agent_prompts_render_ask_attention_in_live_server",
    ):
        match = re.search(rf"^def {re.escape(test_name)}\(", source, re.MULTILINE)
        assert match is not None
        start = match.start()
        end = source.find("\ndef test_", start + 1)
        function_source = source[start:end if end >= 0 else len(source)]
        function_sources[test_name] = function_source
        assert "start_isolated_tmux_runtime(" in function_source
        assert "run_isolated_tmux(" in function_source
        assert "wait_for_isolated_tmux_panes(" in function_source
        assert "stop_isolated_tmux_runtime(" in function_source
        assert "subprocess.run(" not in function_source
        assert "def tmux_cmd(" not in function_source
    real_source = function_sources["test_real_agent_prompts_render_ask_attention_in_live_server"]
    assert "getattr(" not in real_source
    assert "except Exception:" not in real_source
    assert "window.__yolomuxTestWaitFor" in real_source
    assert "ui_deadline" not in real_source
    assert "time.sleep(0.5)" not in real_source


def test_branch_list_title_uses_separate_dark_and_light_theme_tokens(browser, tmp_path):
    page = tmp_path / "branch-list-title-theme.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html('<div class="branch-list-title">Branches</div>'),
    )
    colors = browser.execute_script(
        """
        const title = document.querySelector('.branch-list-title');
        const dark = getComputedStyle(title).color;
        document.body.classList.add('theme-light');
        const light = getComputedStyle(title).color;
        return {dark, light};
        """
    )
    assert colors == {"dark": "rgb(226, 232, 240)", "light": "rgb(71, 85, 105)"}


def test_tab_metadata_hidden_removes_symbols_from_regular_and_compact_tmux_tabs(browser, tmp_path):
    page = tmp_path / "tab-metadata-visibility.html"
    tab_body = '<span class="session-yolo-marker">YO</span><span class="session-button-name">1</span><span class="tab-symbol ci-indicator branch-indicator">MAIN</span><span class="session-button-detail">description</span>'
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(f"""
      <button id="regular-tab" class="pane-tab active">{tab_body}</button>
      <button id="compact-tab" class="tmux-pane-tab-token active">{tab_body}</button>
    """),
    )
    metrics = browser.execute_script(
        """
        const regular = document.getElementById('regular-tab');
        const compact = document.getElementById('compact-tab');
        const read = tab => ({
          symbol: getComputedStyle(tab.querySelector('.tab-symbol')).display,
          symbolText: tab.querySelector('.branch-indicator')?.textContent || '',
          symbolColor: getComputedStyle(tab.querySelector('.branch-indicator')).color,
          symbolBackground: getComputedStyle(tab.querySelector('.branch-indicator')).backgroundColor,
          yolo: getComputedStyle(tab.querySelector('.session-yolo-marker')).display,
          number: tab.querySelector('.session-button-name')?.textContent || '',
          detail: tab.querySelector('.session-button-detail')?.textContent || '',
        });
        document.body.classList.add('tab-meta-hidden');
        const hidden = {regular: read(regular), compact: read(compact)};
        document.body.classList.remove('tab-meta-hidden');
        const visible = {regular: read(regular), compact: read(compact)};
        return {hidden, visible};
        """
    )
    for tab in metrics["hidden"].values():
        assert tab["symbol"] == "none", metrics
        assert tab["yolo"] != "none", metrics
        assert tab["number"] == "1", metrics
        assert tab["detail"] == "description", metrics
    for tab in metrics["visible"].values():
        assert tab["symbol"] != "none", metrics
        assert tab["symbolText"] == "MAIN", metrics
        assert tab["symbolColor"] != tab["symbolBackground"], metrics


def test_session_popover_agent_row_wraps_text_after_compact_state_and_ai_controls(browser, tmp_path):
    page = tmp_path / "session-popover-agent-row-wrap.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <div id="row" class="session-agent-row attention">
        <span id="controls" class="session-agent-kind"><span class="agent-window-activity"><span class="agent-window-status-dot">■</span><span class="agent-icon">✳</span></span></span>12:claude a deliberately long tmux window name that should wrap in the popover <span id="separator" class="session-agent-sep">—</span> needs input
      </div>
    """, extra_css="""
      body { margin: 0; padding: 16px; background: var(--bg); }
      #row { box-sizing: border-box; width: 210px; padding: 3px 5px; border-radius: 5px; background: var(--pane-inactive-tab-bg); }
    """),
    )
    metrics = browser.execute_script(
        """
        const row = document.getElementById('row');
        const controls = document.getElementById('controls');
        const separator = document.getElementById('separator');
        const labelRange = document.createRange();
        labelRange.setStartAfter(controls);
        labelRange.setEndBefore(separator);
        const labelRects = [...labelRange.getClientRects()].map(rect => ({
          left: rect.left,
          top: rect.top,
          right: rect.right,
          width: rect.width,
        }));
        const rowRect = row.getBoundingClientRect();
        const controlsRect = controls.getBoundingClientRect();
        return {
          controlsLeft: controlsRect.left,
          controlsRight: controlsRect.right,
          rowLeft: rowRect.left,
          rowRight: rowRect.right,
          scrollWidth: row.scrollWidth,
          clientWidth: row.clientWidth,
          labelRects,
        };
        """
    )
    assert metrics["controlsLeft"] - metrics["rowLeft"] <= 6, metrics
    assert metrics["labelRects"][0]["left"] >= metrics["controlsRight"] - 1, metrics
    assert len(metrics["labelRects"]) >= 2, metrics
    assert metrics["labelRects"][1]["left"] <= metrics["labelRects"][0]["left"] + 1, metrics
    assert metrics["scrollWidth"] <= metrics["clientWidth"], metrics


def test_subwindow_attention_turns_gray_across_tmux_readback_before_removal(browser, tmp_path):
    cases = [
        {"tone": "attention", "pulsing": True, "expected_fill": "#dc2626", "window_index": 2},
        {"tone": "attention", "pulsing": False, "expected_fill": "#dc2626", "window_index": 3},
        {"tone": "cooldown", "pulsing": True, "expected_fill": "#ffd633", "window_index": 4},
        {"tone": "cooldown", "pulsing": False, "expected_fill": "#ffd633", "window_index": 5},
    ]
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    browser.execute_script(
        """
        const cases = arguments[0];
        const now = Date.now() / 1000;
        agentStatusPulsePeriodMs = 1200;
        document.documentElement.style.setProperty('--pulse-duration', '1.2s');
        document.documentElement.style.setProperty('--status-pulse-step-count', '10');
        setAttentionAnimationClockDelay();
        workflowTransitionGlowSeconds = 60;
        const acknowledgementKeyFor = (item, tone) => JSON.stringify([
          'agent-window',
          '1',
          String(item.window_index),
          '',
          'codex',
          tone === 'attention' ? 'approval' : 'cooldown',
          `click-gray-${item.window_index}`,
        ]);
        const agentFor = (item, tone = item.tone) => {
          const acknowledgementKey = acknowledgementKeyFor(item, tone);
          return tone === 'attention'
            ? {kind: 'codex', state: 'approval', window_index: item.window_index, window_label: `${item.window_index}:codex`, attention_key: acknowledgementKey, attention_acknowledged: false, screen_text: 'Approval required', observed_ts: now}
            : {kind: 'codex', state: 'idle', window_index: item.window_index, window_label: `${item.window_index}:codex`, working_stopped_ts: now - item.window_index, idle_since: now - item.window_index, cooldown_attention_key: acknowledgementKey, cooldown_acknowledged: false, observed_ts: now};
        };
        const payloadFor = agentWindows => ({
          session_order: ['1'],
          sessions: {'1': {target: '1', enabled: false, screen: {key: 'idle'}, agent_windows: agentWindows}},
          rules: {path: '/home/test/.config/yolomux/yolo-rules.yaml', source: 'default', rules: [], errors: []},
        });
        const staticPreseed = cases.filter(item => !item.pulsing).map(item => agentFor(item, item.tone === 'attention' ? 'cooldown' : 'attention'));
        applyAutoApprovePayload(payloadFor(staticPreseed));
        refreshAgentWindowActivityDisplays();
        const payload = payloadFor(cases.map(item => agentFor(item)));
        window.__fixtureAutoApprovePayload = payload;
        applyAutoApprovePayload(payload);
        refreshAgentWindowActivityDisplays();
        const findButton = index => [...document.querySelectorAll(`[data-window-session="1"][data-window-index="${index}"]`)].find(node => node.offsetParent !== null);
        const read = (index, label) => {
          const dot = findButton(index)?.querySelector('.agent-window-status-dot');
          const style = dot ? getComputedStyle(dot) : null;
          const stateRow = (autoApproveStates.get('1')?.agent_windows || []).find(row => Number(row?.window_index) === index);
          const fixtureRow = (window.__fixtureAutoApprovePayload?.sessions?.['1']?.agent_windows || []).find(row => Number(row?.window_index) === index);
          const key = String(stateRow?.attention_key || stateRow?.cooldown_attention_key || fixtureRow?.attention_key || fixtureRow?.cooldown_attention_key || '');
          const record = attentionAcknowledgementRecord(key);
          const pseudo = name => {
            const pseudoStyle = dot ? getComputedStyle(dot, name) : null;
            return {content: pseudoStyle?.content || '', width: pseudoStyle?.width || '', height: pseudoStyle?.height || '', background: pseudoStyle?.backgroundColor || ''};
          };
          return {label, gray: dot?.classList.contains('agent-window-status-dot--acknowledging') || false, pulsing: dot?.classList.contains('agent-window-status-dot--subwindow-pulse') || false, fill: style?.getPropertyValue('--subwindow-status-glyph-fill').trim() || '', opacity: Number(style?.opacity || 0), animationName: style?.animationName || '', animationDuration: style?.animationDuration || '', animationDelay: style?.animationDelay || '', animationIterationCount: style?.animationIterationCount || '', animationTimingFunction: style?.animationTimingFunction || '', acknowledgement: {pending: record?.pending === true, recorded: record?.recordedAt !== null && record?.recordedAt !== undefined, state: stateRow?.attention_acknowledged === true || stateRow?.cooldown_acknowledged === true, fixture: fixtureRow?.attention_acknowledged === true || fixtureRow?.cooldown_acknowledged === true}, before: pseudo('::before'), after: pseudo('::after')};
        };
        window.__subwindowGrayClickSamples = Object.fromEntries(cases.map(item => [String(item.window_index), [read(item.window_index, 'before')]]));
        window.__subwindowGrayClicked = new Set();
        document.addEventListener('pointerdown', event => {
          const button = event.target?.closest?.('[data-window-session="1"][data-window-index]');
          const index = Number(button?.dataset?.windowIndex);
            const samples = window.__subwindowGrayClickSamples[String(index)];
            if (!samples || window.__subwindowGrayClicked.has(index)) return;
                    window.__subwindowGrayClicked.add(index);
                    const clickedAt = performance.now();
                    const captureAt = (label, currentTime) => {
                      refreshAgentWindowActivityDisplays();
                      const dot = findButton(index)?.querySelector('.agent-window-status-dot');
                      dot?.style.setProperty('--agent-status-acknowledgement-delay', '0s');
                      const animation = dot?.getAnimations().find(item => item.animationName === 'agent-status-acknowledgement-fade');
                      if (animation) {
                        animation.pause();
                        animation.currentTime = currentTime;
                      }
                      samples.push(read(index, label));
                    };
                    const captureSettled = () => {
                      refreshAgentWindowActivityDisplays();
                      const sample = read(index, 'settled');
              if (!sample.gray || performance.now() - clickedAt >= 3000) samples.push(sample);
              else setTimeout(captureSettled, 100);
            };
                setTimeout(() => {
                  captureAt('100ms', 100);
                  captureAt('500ms', 500);
                  captureAt('1000ms', 1000);
                }, 0);
            setTimeout(captureSettled, 1400);
        }, {capture: true});
        return window.__subwindowGrayClickSamples;
        """,
        cases,
    )
    for case in cases:
        selector = f'[data-window-session="1"][data-window-index="{case["window_index"]}"]'
        click_visible_selector(browser, selector)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return Object.values(window.__subwindowGrayClickSamples || {}).every(samples => samples.length === 5)")
    )
    samples_by_window = browser.execute_script("return window.__subwindowGrayClickSamples")
    for case in cases:
        samples = samples_by_window[str(case["window_index"])]
        evidence = {"case": case, "samples": samples}
        assert samples[0]["gray"] is False and samples[0]["pulsing"] is case["pulsing"] and samples[0]["fill"] == case["expected_fill"], evidence
        for sample in samples[1:4]:
            assert sample["gray"] is True and sample["pulsing"] is False and sample["fill"] == "#9aa5b1", evidence
            assert sample["animationName"] == "agent-status-acknowledgement-fade", evidence
            assert sample["animationDuration"] == "1.2s", evidence
            assert -1.2 <= float(sample["animationDelay"].removesuffix("s")) <= 0, evidence
            assert sample["animationIterationCount"] == "1" and sample["animationTimingFunction"].startswith("steps(10"), evidence
            assert float(sample["before"]["width"].removesuffix("px")) > 0, evidence
            assert float(sample["before"]["height"].removesuffix("px")) > 0, evidence
            assert sample["before"]["background"] == "rgb(154, 165, 177)", evidence
            if case["tone"] == "cooldown":
                assert float(sample["after"]["width"].removesuffix("px")) > 0, evidence
                assert float(sample["after"]["height"].removesuffix("px")) > 0, evidence
                assert sample["after"]["background"] == "rgb(154, 165, 177)", evidence
        assert samples[1]["opacity"] > samples[2]["opacity"] > samples[3]["opacity"] > 0, evidence
        assert samples[1]["opacity"] >= 0.8 and samples[3]["opacity"] <= 0.6, evidence
        assert samples[1]["opacity"] - samples[3]["opacity"] >= 0.4, evidence
        assert samples[4]["gray"] is False and samples[4]["fill"] == "", {
            "case": case,
            "acknowledgement": samples[4]["acknowledgement"],
            "gray": samples[4]["gray"],
            "fill": samples[4]["fill"],
        }


def test_session_tabs_reserve_an_invisible_status_ball_without_number_padding(browser, tmp_path):
    page = tmp_path / "tab-reserves-invisible-status-ball.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <section class="tab-prefix-fixture">
        <button id="with-ball" class="pane-tab active"><span class="pane-tab-core"><span class="session-agent-activity-marker"><span class="agent-window-activity agent-window-activity--status-only"><span class="agent-window-status-dot"></span></span></span><span class="session-button-prefix"><span class="session-button-number">3</span></span><span class="session-button-text">#86 DRAFT feature title</span></span></button>
        <button id="without-ball" class="pane-tab active"><span class="pane-tab-core"><span class="session-agent-activity-marker session-agent-activity-marker--placeholder"><span class="agent-window-activity agent-window-activity--status-only"><span class="agent-window-status-dot"></span></span></span><span class="session-button-prefix"><span class="session-button-number">3</span></span><span class="session-button-text">#86 DRAFT feature title</span></span></button>
        <button id="long-name" class="pane-tab active"><span class="pane-tab-core"><span class="session-agent-activity-marker session-agent-activity-marker--placeholder"><span class="agent-window-activity agent-window-activity--status-only"><span class="agent-window-status-dot"></span></span></span><span class="session-button-prefix"><strong class="session-button-name session-button-identifier">[dynamo-utils.production]</strong></span><span class="session-button-text">keivenc</span></span></button>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 16px; background: #202633; }
      .tab-prefix-fixture { display: grid; justify-items: start; gap: 8px; }
      .pane-tab { width: 420px; }
    """),
    )
    metrics = browser.execute_script(
        """
        const read = id => {
          const tab = document.getElementById(id);
          const number = tab.querySelector('.session-button-number');
          const text = tab.querySelector('.session-button-text');
          const numberRect = number.getBoundingClientRect();
          const textRect = text.getBoundingClientRect();
          const marker = tab.querySelector('.session-agent-activity-marker');
          const markerRect = marker.getBoundingClientRect();
          return {markerWidth: markerRect.width, numberWidth: numberRect.width, textLeft: textRect.left};
        };
        return {withBall: read('with-ball'), withoutBall: read('without-ball')};
        """
    )
    assert abs(metrics["withoutBall"]["markerWidth"] - metrics["withBall"]["markerWidth"]) <= 0.5, metrics
    assert abs(metrics["withoutBall"]["textLeft"] - metrics["withBall"]["textLeft"]) <= 0.5, metrics
    assert metrics["withBall"]["numberWidth"] < 2 * metrics["withBall"]["markerWidth"], metrics
    long_name = browser.execute_script(
        """
        const name = document.querySelector('#long-name .session-button-name');
        return {clientWidth: name.clientWidth, scrollWidth: name.scrollWidth, text: name.textContent};
        """
    )
    assert long_name["text"] == "[dynamo-utils.production]", long_name
    assert long_name["scrollWidth"] <= long_name["clientWidth"], long_name


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


def _agent_status_glyph_html(kind, state, element_id, *, subwindow=False):
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
      <span class="agent-window-activity{' agent-window-activity--subwindow' if subwindow else ''} agent-window-activity--{state}" title="{label}" aria-label="{label}" style="--attention-animation-delay:0s">
        <span id="{element_id}" class="agent-icon {kind} agent-window-activity-icon agent-window-agent-icon agent-window-activity-icon--{state} agent-window-agent-icon--{state}" aria-label="{label}" title="{label}">
          {svg}
        </span>
        <span id="{element_id}-dot" class="{' '.join(dot_classes)}" aria-hidden="true">●</span>
      </span>
    """


def _working_agent_glyph_html(kind, element_id, *, subwindow=False):
    return _agent_status_glyph_html(kind, "working", element_id, subwindow=subwindow)


def _tabber_window_button_html(kind, label, glyph_html, active=False):
    active_class = " active" if active else ""
    return f"""
      <span class="tabber-window-token tmux-window-bar" data-tmux-window-label-mode="names" data-tmux-window-bar-context="info">
        <span class="tab tmux-window-button tabber-window-button{active_class}" data-tabber-window-button="shared">
          <span class="tmux-window-name-label">
            {glyph_html}
            <span class="tmux-window-name-text">{label}</span>
          </span>
        </span>
      </span>
    """


def test_debug_agent_status_y_axis_guides_align_with_labels(browser, tmp_path):
    page = tmp_path / "debug-agent-status-axis-guides.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <section class="js-debug-chart debug-chart-fixture" data-js-debug-chart="activity">
        <div class="js-debug-chart-head">
          <span class="js-debug-chart-title">Agent status</span>
        </div>
        <div class="js-debug-chart-body">
          <div class="js-debug-y-axis js-debug-y-axis--integer" data-js-debug-axis="activity">
            <span data-js-debug-axis-tick="activity" data-js-debug-axis-value="3" data-js-debug-axis-max="activity" style="--js-debug-axis-y: 6.667%;">3</span>
            <span data-js-debug-axis-tick="activity" data-js-debug-axis-value="2" style="--js-debug-axis-y: 37.778%;">2</span>
            <span data-js-debug-axis-tick="activity" data-js-debug-axis-value="1" style="--js-debug-axis-y: 68.889%;">1</span>
            <span data-js-debug-axis-tick="activity" data-js-debug-axis-value="0" data-js-debug-axis-zero="activity" style="--js-debug-axis-y: 100%;">0</span>
          </div>
          <div class="js-debug-plot">
            <svg class="js-debug-line-chart" viewBox="0 0 600 120" role="img" preserveAspectRatio="none">
              <line class="js-debug-grid-line js-debug-grid-line--integer" data-js-debug-grid-line="activity" data-js-debug-grid-value="3" x1="0" y1="8.0" x2="600" y2="8.0" vector-effect="non-scaling-stroke"></line>
              <line class="js-debug-grid-line js-debug-grid-line--integer" data-js-debug-grid-line="activity" data-js-debug-grid-value="2" x1="0" y1="45.3" x2="600" y2="45.3" vector-effect="non-scaling-stroke"></line>
              <line class="js-debug-grid-line js-debug-grid-line--integer" data-js-debug-grid-line="activity" data-js-debug-grid-value="1" x1="0" y1="82.7" x2="600" y2="82.7" vector-effect="non-scaling-stroke"></line>
              <line class="js-debug-grid-line js-debug-grid-line--integer" data-js-debug-grid-line="activity" data-js-debug-grid-value="0" x1="0" y1="120.0" x2="600" y2="120.0" vector-effect="non-scaling-stroke"></line>
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
    """),
    )
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
    assert all(0 < item["strokeWidth"] <= 0.8 for item in metrics), metrics


def test_debug_agent_status_hidden_integer_guides_stay_full_width_and_distinct(browser, tmp_path):
    axis_max = 12
    labeled_values = list(range(axis_max, -1, -2))
    grid_values = list(range(axis_max, -1, -1))
    labels_html = "".join(
        f'<span data-js-debug-axis-value="{value}">{value}</span>'
        for value in labeled_values
    )
    grid_html = "".join(
        f'<line class="js-debug-grid-line js-debug-grid-line--integer" data-js-debug-grid-value="{value}" x1="0" y1="{8 + (1 - value / axis_max) * 112:.1f}" x2="600" y2="{8 + (1 - value / axis_max) * 112:.1f}" vector-effect="non-scaling-stroke"></line>'
        for value in grid_values
    )
    page = tmp_path / "debug-agent-status-hidden-integer-guides.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(f"""
      <section class="js-debug-graph-view">
        <div class="js-debug-y-axis">{labels_html}</div>
        <svg class="js-debug-line-chart" viewBox="0 0 600 120" role="img" preserveAspectRatio="none">
          <rect class="js-debug-bar js-debug-bar--idleAgents" data-idle-fill x="0" y="8" width="600" height="104"></rect>
          {grid_html}
        </svg>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 24px; background: #111827; color: #e5e7eb; }
      .js-debug-graph-view { width: 560px; }
      .js-debug-line-chart { width: 520px; height: 220px; }
    """),
    )
    metrics = browser.execute_script(
        """
        const lines = [...document.querySelectorAll('[data-js-debug-grid-value]')];
        const labels = new Set([...document.querySelectorAll('[data-js-debug-axis-value]')].map(node => node.dataset.jsDebugAxisValue));
        const hidden = lines.filter(line => !labels.has(line.dataset.jsDebugGridValue));
        const rgb = value => {
          const numbers = (String(value).match(/[0-9.]+/g) || []).slice(0, 3).map(Number);
          return String(value).startsWith('color(srgb') ? numbers.map(number => number * 255) : numbers;
        };
        const lineColor = rgb(getComputedStyle(lines[0]).stroke);
        const idleColor = rgb(getComputedStyle(document.querySelector('[data-idle-fill]')).fill);
        const colorDistance = Math.sqrt(lineColor.reduce((total, value, index) => total + ((value - idleColor[index]) ** 2), 0));
        return {
          count: lines.length,
          values: lines.map(line => Number(line.dataset.jsDebugGridValue)),
          hiddenValues: hidden.map(line => Number(line.dataset.jsDebugGridValue)),
          fullWidth: lines.every(line => line.getAttribute('x1') === '0' && line.getAttribute('x2') === '600'),
          strokeWidths: lines.map(line => Number.parseFloat(getComputedStyle(line).strokeWidth)),
          colorDistance,
        };
        """
    )
    assert metrics["count"] == axis_max + 1
    assert metrics["values"] == grid_values
    assert metrics["hiddenValues"] == [11, 9, 7, 5, 3, 1]
    assert metrics["fullWidth"] is True
    assert all(0.25 <= width <= 0.35 for width in metrics["strokeWidths"]), metrics
    assert metrics["colorDistance"] >= 20, metrics


def test_debug_graph_series_colors_are_distinct_and_theme_aware(browser, tmp_path):
    page = tmp_path / "debug-graph-series-colors.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <section class="js-debug-graph-view" id="debug-graph">
        <svg class="js-debug-line-chart" viewBox="0 0 20 20" role="img" preserveAspectRatio="none">
          <path class="js-debug-line js-debug-line--api" d="M0 1L20 1"></path>
          <path class="js-debug-line js-debug-line--sse" d="M0 3L20 3"></path>
          <path data-cpu-server="current" class="js-debug-line js-debug-line--cpu js-debug-line--pattern js-debug-line--pattern-solid" d="M0 5L20 5"></path>
          <path data-cpu-server="peer" class="js-debug-line js-debug-line--cpu js-debug-line--pattern js-debug-line--pattern-dot" style="--js-debug-series-color: var(--accent-gold)" d="M0 6L20 6"></path>
          <path data-cpu-server="system" class="js-debug-line js-debug-line--systemCpu js-debug-line--pattern js-debug-line--pattern-solid" d="M0 7L20 7"></path>
          <path data-client-line="solid" class="js-debug-line js-debug-line--api js-debug-line--client js-debug-line--client-solid" d="M0 8L20 8"></path>
          <path data-client-line="peer" class="js-debug-line js-debug-line--api js-debug-line--client js-debug-line--client-dot" d="M0 10L20 10"></path>
          <rect class="js-debug-bar js-debug-bar--agentToken" data-agent-token="cyan" style="--js-debug-series-color: var(--js-debug-agent-token-cyan)" x="0" y="9" width="1" height="1"></rect>
          <rect class="js-debug-bar js-debug-bar--agentToken" data-agent-token="orange" style="--js-debug-series-color: var(--js-debug-agent-token-orange)" x="2" y="9" width="1" height="1"></rect>
          <rect class="js-debug-bar js-debug-bar--agentToken" data-agent-token="magenta" style="--js-debug-series-color: var(--js-debug-agent-token-magenta)" x="4" y="9" width="1" height="1"></rect>
          <rect class="js-debug-bar js-debug-bar--agentToken" data-agent-token="beige" style="--js-debug-series-color: var(--js-debug-agent-token-beige)" x="6" y="9" width="1" height="1"></rect>
          <rect class="js-debug-bar js-debug-bar--agentToken" data-agent-token="turquoise" style="--js-debug-series-color: var(--js-debug-agent-token-turquoise)" x="8" y="9" width="1" height="1"></rect>
          <rect class="js-debug-bar js-debug-bar--agentToken" data-agent-token="rose" style="--js-debug-series-color: var(--js-debug-agent-token-rose)" x="10" y="9" width="1" height="1"></rect>
          <rect class="js-debug-bar js-debug-bar--agentToken" data-agent-token="violet" style="--js-debug-series-color: var(--js-debug-agent-token-violet)" x="12" y="9" width="1" height="1"></rect>
        </svg>
        <span class="js-debug-legend-swatch js-debug-legend-swatch--api"></span>
        <span class="js-debug-legend-swatch js-debug-legend-swatch--sse"></span>
        <span class="js-debug-legend-swatch js-debug-legend-swatch--cpu"></span>
        <span class="js-debug-legend-swatch js-debug-legend-swatch--systemCpu"></span>
        <svg class="js-debug-legend-line" viewBox="0 0 18 4"><line data-client-legend="peer" class="js-debug-line js-debug-line--api js-debug-line--client js-debug-line--client-dot" x1="0" y1="2" x2="18" y2="2"></line></svg>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 24px; background: var(--bg); color: var(--text); }
      #debug-graph { width: 260px; }
    """),
    )
    metrics = browser.execute_script(
        """
        const graph = document.getElementById('debug-graph');
        const line = name => getComputedStyle(document.querySelector(`.js-debug-line--${name}`)).stroke;
        const swatch = name => getComputedStyle(document.querySelector(`.js-debug-legend-swatch--${name}`)).color;
        const agentToken = name => getComputedStyle(document.querySelector(`[data-agent-token="${name}"]`)).fill;
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
            line: {api: line('api'), sse: line('sse'), cpu: line('cpu'), peerCpu: getComputedStyle(document.querySelector('[data-cpu-server="peer"]')).stroke, systemCpu: line('systemCpu')},
            legend: {api: swatch('api'), sse: swatch('sse'), cpu: swatch('cpu'), systemCpu: swatch('systemCpu')},
            expected: {
              api: colorFor('var(--js-debug-api-series)'),
              sse: colorFor('var(--js-debug-sse-series)'),
              cpu: colorFor('var(--active-accent-bright)'),
              systemCpu: colorFor('var(--bad)'),
            },
            agentTokens: ['cyan', 'orange', 'magenta', 'beige', 'turquoise', 'rose', 'violet'].map(agentToken),
            clientLines: Object.fromEntries(['solid', 'peer'].map(pattern => [pattern, getComputedStyle(document.querySelector(`[data-client-line="${pattern}"]`)).strokeDasharray])),
            clientOpacity: Object.fromEntries(['solid', 'peer'].map(pattern => [pattern, Number(getComputedStyle(document.querySelector(`[data-client-line="${pattern}"]`)).opacity)])),
            clientLegend: {peer: getComputedStyle(document.querySelector('[data-client-legend="peer"]')).strokeDasharray},
            cpuLines: Object.fromEntries(['current', 'peer', 'system'].map(server => [server, getComputedStyle(document.querySelector(`[data-cpu-server="${server}"]`)).strokeDasharray])),
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
        assert item["line"]["peerCpu"] != item["line"]["cpu"], (theme, item)
        assert item["apiSseDistance"] >= 120, (theme, item)
        distances = [
            item["agentTokens"][left] != item["agentTokens"][right]
            for left in range(len(item["agentTokens"]))
            for right in range(left + 1, len(item["agentTokens"]))
        ]
        assert all(distances), (theme, item)
        assert item["clientLines"] == {
            "solid": "none",
            "peer": "1px, 3px",
        }, (theme, item)
        assert item["clientOpacity"]["solid"] == 1, (theme, item)
        assert 0 < item["clientOpacity"]["peer"] < item["clientOpacity"]["solid"], (theme, item)
        assert item["clientLegend"] == {"peer": item["clientLines"]["peer"]}, (theme, item)
        assert item["cpuLines"] == {"current": "none", "peer": "1px, 3px", "system": "none"}, (theme, item)


def test_debug_graph_chart_title_stays_full_above_long_client_legend(browser, tmp_path):
    page = tmp_path / "debug-graph-full-title.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <section class="js-debug-chart" style="width:420px">
        <div class="js-debug-chart-head">
          <div class="js-debug-chart-heading-row">
            <span id="latency-title" class="js-debug-chart-title">Client latency</span>
          </div>
          <div id="latency-legend" class="js-debug-legend">
            <div class="js-debug-legend-item"><svg class="js-debug-legend-line"></svg><span>Client latency across all retained clients</span></div>
          </div>
        </div>
        <div class="js-debug-chart-body"><div class="js-debug-plot"><svg class="js-debug-line-chart" viewBox="0 0 600 120"></svg></div></div>
      </section>
    """, extra_css="body { margin:0; padding:24px; background:var(--bg); color:var(--text); }"),
    )
    metrics = browser.execute_script(
        """
        const title = document.getElementById('latency-title');
        const heading = title.closest('.js-debug-chart-heading-row').getBoundingClientRect();
        const legend = document.getElementById('latency-legend').getBoundingClientRect();
        const titleRect = title.getBoundingClientRect();
        const range = document.createRange();
        range.selectNodeContents(title);
        return {
          text: title.textContent,
          textOverflow: getComputedStyle(title).textOverflow,
          textWidth: range.getBoundingClientRect().width,
          titleWidth: titleRect.width,
          headingBottom: heading.bottom,
          legendTop: legend.top,
          titleFontSize: Number.parseFloat(getComputedStyle(title).fontSize),
          legendFontSize: Number.parseFloat(getComputedStyle(document.querySelector('.js-debug-legend-item')).fontSize),
        };
        """
    )
    assert metrics["text"] == "Client latency", metrics
    assert metrics["textOverflow"] != "ellipsis", metrics
    assert metrics["textWidth"] <= metrics["titleWidth"] + 0.5, metrics
    assert metrics["legendTop"] >= metrics["headingBottom"] - 0.5, metrics
    assert abs(metrics["legendFontSize"] / metrics["titleFontSize"] - 0.85) <= 0.01, metrics


def test_debug_graph_header_controls_and_time_axis_stay_inside_their_rows(browser, tmp_path):
    load_static_html_fixture(browser, tmp_path, "debug-graph-header-geometry.html", page_html("""
      <div class="js-debug-graph-controls" style="width:720px">
        <div class="js-debug-range-slider-control" data-js-debug-range-control>
          <span id="range-prefix" class="js-debug-range-prefix">Range:</span><input id="range-slider" class="js-debug-range-slider" type="range"><span id="range-label" class="js-debug-range-label">15m</span>
        </div>
        <span id="resolution" class="js-debug-resolution-label">Resolution: 1s</span>
      </div>
      <section id="chart" class="js-debug-chart" style="width:420px">
        <div class="js-debug-chart-head"><div id="heading" class="js-debug-chart-heading-row"><span id="title" class="js-debug-chart-title">Client API&amp;SSE/sec</span><span id="summary" class="js-debug-chart-summary">(123.4k, Σ displayed reqs)</span><button id="close" class="js-debug-chart-close">×</button></div></div>
        <div class="js-debug-chart-body"><div id="y-axis" class="js-debug-y-axis"><span id="axis-max" data-js-debug-axis-max style="--js-debug-axis-y: 6.667%;">100%</span><span id="axis-zero" data-js-debug-axis-zero style="--js-debug-axis-y: 100%;">0%</span></div><div id="plot" class="js-debug-plot"><svg id="svg" class="js-debug-line-chart" viewBox="0 0 600 120"></svg></div><div id="axis" class="js-debug-x-axis"><span>23:09:28</span><span>23:16:58</span><span>23:24:28</span></div></div>
      </section>
    """, extra_css="body { margin:0; padding:24px; background:var(--bg); color:var(--text); }"))
    metrics = browser.execute_script(
        """
        const rect = id => { const value = document.getElementById(id).getBoundingClientRect(); return {left:value.left, right:value.right, top:value.top, bottom:value.bottom, width:value.width, height:value.height}; };
        const heading = rect('heading'); const close = rect('close'); const summary = rect('summary'); const axis = rect('axis'); const plot = rect('plot'); const svg = rect('svg');
        const chart = rect('chart'); const range = document.querySelector('[data-js-debug-range-control]').getBoundingClientRect();
        return {heading, close, summary, axis, plot, svg, chart, yAxis: rect('y-axis'), axisMax: rect('axis-max'), axisZero: rect('axis-zero'), range, resolution: rect('resolution'), rangePrefix: rect('range-prefix'), rangeSlider: rect('range-slider'), rangeLabel: rect('range-label'), axisItems: [...document.querySelectorAll('#axis span')].map(node => { const value=node.getBoundingClientRect(); return {left:value.left,right:value.right,top:value.top,bottom:value.bottom}; })};
        """
    )
    assert metrics["close"]["right"] <= metrics["heading"]["right"] + 0.5, metrics
    assert metrics["close"]["left"] >= metrics["summary"]["right"] + 4, metrics
    assert metrics["axis"]["top"] >= metrics["plot"]["bottom"] + 4, metrics
    assert all(item["left"] >= metrics["axis"]["left"] - 0.5 and item["right"] <= metrics["axis"]["right"] + 0.5 for item in metrics["axisItems"]), metrics
    assert all(item["top"] >= metrics["chart"]["top"] and item["bottom"] <= metrics["chart"]["bottom"] for item in metrics["axisItems"]), metrics
    assert abs(((metrics["axisMax"]["top"] + metrics["axisMax"]["bottom"]) / 2) - (metrics["svg"]["top"] + (metrics["svg"]["height"] * 8 / 120))) <= 0.75, metrics
    assert abs(((metrics["axisZero"]["top"] + metrics["axisZero"]["bottom"]) / 2) - metrics["svg"]["bottom"]) <= 0.75, metrics
    assert metrics["axisMax"]["top"] >= metrics["yAxis"]["top"] - 0.5, metrics
    assert metrics["rangePrefix"]["right"] <= metrics["rangeSlider"]["left"] + 0.5, metrics
    assert metrics["rangeLabel"]["left"] >= metrics["rangeSlider"]["right"] + 4, metrics
    assert metrics["rangeLabel"]["right"] <= metrics["range"]["right"] + 0.5, metrics
    assert metrics["resolution"]["left"] >= metrics["range"]["right"] + 4, metrics
    assert metrics["rangeSlider"]["left"] >= metrics["range"]["left"] - 0.5, metrics


def test_debug_graph_sparse_client_samples_aggregate_and_zero_meets_baseline(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof debugGraphApplyServerHistory === 'function'
              && typeof setDebugGraphRange === 'function'
              && document.querySelector('[data-js-debug-graph]') !== null;
            """
        )
    )
    metrics = browser.execute_script(
        """
        stopJsDebugStatsPolling();
        clearJsDebugGraphData();
        const nowSeconds = Math.floor(Date.now() / 1000);
        const currentClientId = jsDebugStatsClientIdForRequest();
        const records = Array.from({length: 300}, (_item, index) => ({
          start: nowSeconds - 300 + index,
          duration: 1,
          sequence: index + 1,
          cpu_total_percent: 1,
          cpu_count: 1,
          ...(index % 3 === 0 ? {
            clients: {
              [currentClientId]: {api_count: 1, latency_total_ms: 12, latency_count: 1, bandwidth_bytes: 256},
            },
          } : {}),
        }));
        setDebugGraphRange(5 * 60, {render: false});
        debugGraphApplyServerHistory({sequence: 300, records});
        renderDebugPanels({force: true});
        let chart = document.querySelector('[data-js-debug-chart="latency"]');
        const svg = chart.querySelector('.js-debug-line-chart');
        const zero = chart.querySelector('[data-js-debug-axis-zero="latency"]');
        const zeroRect = zero.getBoundingClientRect();
        const svgRect = svg.getBoundingClientRect();
        const fiveSecond = {
          resolution: Number(document.querySelector('[data-js-debug-resolution-seconds]')?.dataset.jsDebugResolutionSeconds),
          lineSegments: chart.querySelectorAll('[data-js-debug-series="latency"]').length,
          noDataRegions: chart.querySelectorAll('[data-js-debug-no-data-range]').length,
        };

        clearJsDebugGraphData();
        const tenSecondRecords = Array.from({length: 90}, (_item, index) => ({
          start: nowSeconds - (15 * 60) + (index * 10),
          duration: 10,
          sequence: index + 1,
          cpu_total_percent: 1,
          cpu_count: 1,
          ...(index % 2 === 0 ? {
            clients: {
              [currentClientId]: {api_count: 1, latency_total_ms: 12, latency_count: 1, bandwidth_bytes: 256},
            },
          } : {}),
        }));
        setDebugGraphRange(15 * 60, {render: false});
        debugGraphApplyServerHistory({sequence: 90, records: tenSecondRecords});
        renderDebugPanels({force: true});
        chart = document.querySelector('[data-js-debug-chart="bandwidth"]');
        const tenSecond = {
          resolution: Number(document.querySelector('[data-js-debug-resolution-seconds]')?.dataset.jsDebugResolutionSeconds),
          lineSegments: chart.querySelectorAll('[data-js-debug-series="bandwidth"]').length,
          noDataRegions: chart.querySelectorAll('[data-js-debug-no-data-range]').length,
        };

        clearJsDebugGraphData();
        setDebugGraphRange(60, {render: false});
        debugGraphApplyServerHistory({sequence: 3, records: [{
          start: nowSeconds - 3, duration: 1, sequence: 1, disconnected_ms: 1000,
          clients: {'client-stale': {disconnected_ms: 1000}},
        }, {
          start: nowSeconds - 2, duration: 1, sequence: 2,
          clients: {'client-live': {api_count: 1, latency_total_ms: 12, latency_count: 1, bandwidth_bytes: 256}},
        }, {
          start: nowSeconds - 1, duration: 1, sequence: 3, disconnected_ms: 1000,
          clients: {'client-stale': {disconnected_ms: 1000}, 'client-live': {disconnected_ms: 1000}},
        }]});
        renderDebugPanels({force: true});
        const thisClientOutageRegions = document.querySelectorAll('[data-js-debug-chart="bandwidth"] [data-js-debug-disconnected-range]').length;
        return {
          fiveSecond,
          tenSecond,
          thisClientOutageRegions,
          zeroBaselineDelta: Math.abs((zeroRect.top + zeroRect.height / 2) - svgRect.bottom),
          zeroStyle: zero.style.getPropertyValue('--js-debug-axis-y'),
        };
        """
    )
    assert metrics["fiveSecond"]["resolution"] == 5, metrics
    assert metrics["fiveSecond"]["lineSegments"] == 1, metrics
    assert metrics["fiveSecond"]["noDataRegions"] <= 2, metrics
    assert metrics["tenSecond"] == {"resolution": 10, "lineSegments": 1, "noDataRegions": 0}, metrics
    assert metrics["thisClientOutageRegions"] == 2, metrics
    assert metrics["zeroBaselineDelta"] <= 0.75, metrics
    assert metrics["zeroStyle"] == "100.000%", metrics


def test_debug_graph_cpu_chart_yields_plot_height_to_a_wrapped_legend(browser, tmp_path):
    load_static_html_fixture(browser, tmp_path, "debug-graph-cpu-compact-row.html", page_html("""
      <div class="js-debug-chart-grid" style="width:690px">
        <section id="cpu" class="js-debug-chart" data-js-debug-chart="cpu">
          <div class="js-debug-chart-head"><div class="js-debug-chart-heading-row"><span class="js-debug-chart-title">CPU</span></div><div class="js-debug-legend"><span>system avg CPU %</span><span>yolomux.py :8101 CPU %</span><span>yolomux.py :8102 CPU %</span><span>yolomux.py :8103 CPU %</span></div></div>
          <div id="cpu-body" class="js-debug-chart-body"><div id="cpu-axis" class="js-debug-y-axis"><span>100%</span></div><div class="js-debug-plot"><svg id="cpu-plot" class="js-debug-line-chart" viewBox="0 0 600 120"></svg></div><div class="js-debug-x-axis"><span>07:56</span><span>08:26</span><span>08:56</span></div></div>
        </section>
        <section class="js-debug-chart"><div class="js-debug-chart-head"><span class="js-debug-chart-title">System memory</span></div><div class="js-debug-chart-body"><div class="js-debug-y-axis"><span>125GB</span></div><div class="js-debug-plot"><svg class="js-debug-line-chart" viewBox="0 0 600 120"></svg></div></div></section>
        <section id="next-row" class="js-debug-chart"><div class="js-debug-chart-head"><span class="js-debug-chart-title">GPU memory</span></div><div class="js-debug-chart-body"><div class="js-debug-y-axis"><span>48GB</span></div><div class="js-debug-plot"><svg class="js-debug-line-chart" viewBox="0 0 600 120"></svg></div></div></section>
      </div>
    """, extra_css="body { margin:0; padding:24px; background:var(--bg); color:var(--text); }"))
    metrics = browser.execute_script(
        """
        const rect = id => { const value = document.getElementById(id).getBoundingClientRect(); return {top:value.top, bottom:value.bottom, height:value.height}; };
        return {cpu: rect('cpu'), body: rect('cpu-body'), axis: rect('cpu-axis'), plot: rect('cpu-plot'), nextRow: rect('next-row')};
        """
    )
    assert 72 <= metrics["body"]["height"] < 138, metrics
    assert metrics["axis"]["height"] >= 72 and metrics["plot"]["height"] >= 72, metrics
    assert metrics["cpu"]["bottom"] <= metrics["nextRow"]["top"] - 9, metrics


def test_debug_graph_scrolls_whole_cards_without_an_outer_frame_or_chart_overlap(browser, tmp_path):
    chart = """
      <section class="js-debug-chart"><div class="js-debug-chart-head"><span class="js-debug-chart-title">{title}</span></div><div class="js-debug-chart-body"><div class="js-debug-y-axis"><span style="--js-debug-axis-y:6.667%">100%</span><span style="--js-debug-axis-y:100%">0%</span></div><div class="js-debug-plot"><svg class="js-debug-line-chart" viewBox="0 0 600 120"></svg></div><div class="js-debug-x-axis"><span>08:11:16</span><span>09:11:16</span><span>10:11:16</span></div></div></section>
    """
    load_static_html_fixture(browser, tmp_path, "debug-graph-flow-layout.html", page_html(f"""
      <div id="graph-view" class="js-debug-subview js-debug-graph-view" style="width:720px;height:300px">
        <div id="graph" class="js-debug-graph"><div class="js-debug-chart-shell"><div id="chart-grid" class="js-debug-chart-grid">{chart.format(title='CPU')}{chart.format(title='System memory')}{chart.format(title='GPU utilization')}{chart.format(title='GPU memory')}</div></div></div>
      </div>
    """, extra_css="body { margin:0; padding:24px; background:var(--bg); color:var(--text); }"))
    metrics = browser.execute_script(
        """
        const rect = node => { const value = node.getBoundingClientRect(); return {top:value.top, bottom:value.bottom, left:value.left, right:value.right}; };
        const view = document.getElementById('graph-view');
        const graph = document.getElementById('graph');
        const cards = [...document.querySelectorAll('.js-debug-chart')].map(rect);
        const timeLabels = [...document.querySelectorAll('.js-debug-x-axis span')].map(node => ({...rect(node), card: rect(node.closest('.js-debug-chart'))}));
        return {view: {scrollHeight:view.scrollHeight, clientHeight:view.clientHeight, overflow:getComputedStyle(view).overflowY}, graph: {border:getComputedStyle(graph).borderTopWidth, padding:getComputedStyle(graph).paddingTop}, cards, timeLabels};
        """
    )
    assert metrics["view"]["overflow"] == "auto" and metrics["view"]["scrollHeight"] > metrics["view"]["clientHeight"], metrics
    assert metrics["graph"] == {"border": "0px", "padding": "0px"}, metrics
    assert metrics["cards"][0]["bottom"] <= metrics["cards"][2]["top"] - 9, metrics
    assert metrics["cards"][1]["bottom"] <= metrics["cards"][3]["top"] - 9, metrics
    assert all(label["top"] >= label["card"]["top"] and label["bottom"] <= label["card"]["bottom"] for label in metrics["timeLabels"]), metrics


def test_debug_graph_cards_fill_a_tall_pane_without_exceeding_their_maximum_height(browser, tmp_path):
    chart = """
      <section class="js-debug-chart"><div class="js-debug-chart-head"><span class="js-debug-chart-title">{title}</span></div><div class="js-debug-chart-body"><div class="js-debug-y-axis"><span>100%</span></div><div class="js-debug-plot"><svg class="js-debug-line-chart" viewBox="0 0 600 120"></svg></div><div class="js-debug-x-axis"><span>08:11:16</span><span>09:11:16</span><span>10:11:16</span></div></div></section>
    """
    load_static_html_fixture(browser, tmp_path, "debug-graph-tall-card-layout.html", page_html(f"""
      <div id="graph-view" class="js-debug-subview js-debug-graph-view" style="width:720px;height:1040px">
        <div id="graph" class="js-debug-graph"><div class="js-debug-chart-shell"><div id="chart-grid" class="js-debug-chart-grid">{chart.format(title='CPU')}{chart.format(title='System memory')}{chart.format(title='GPU utilization')}{chart.format(title='GPU memory')}{chart.format(title='Client latency')}{chart.format(title='Agent status')}</div></div></div>
      </div>
    """, extra_css="body { margin:0; padding:24px; background:var(--bg); color:var(--text); }"))
    metrics = browser.execute_script(
        """
        const rect = node => { const value = node.getBoundingClientRect(); return {top:value.top, bottom:value.bottom, height:value.height}; };
        const view = document.getElementById('graph-view');
        const grid = document.getElementById('chart-grid');
        return {
          view: {clientHeight:view.clientHeight, scrollHeight:view.scrollHeight},
          grid: rect(grid),
          cards: [...document.querySelectorAll('.js-debug-chart')].map(rect),
          minimum: Number.parseFloat(getComputedStyle(grid).getPropertyValue('--js-debug-chart-min-height')),
          maximum: Number.parseFloat(getComputedStyle(grid).getPropertyValue('--js-debug-chart-max-height')),
        };
        """
    )
    assert metrics["view"]["scrollHeight"] == metrics["view"]["clientHeight"], metrics
    assert all(metrics["minimum"] < card["height"] <= metrics["maximum"] for card in metrics["cards"]), metrics
    assert metrics["cards"][0]["bottom"] <= metrics["cards"][2]["top"] - 9, metrics
    assert metrics["cards"][2]["bottom"] <= metrics["cards"][4]["top"] - 9, metrics


def test_repo_chip_menu_uses_shared_left_aligned_branch_and_status_columns(browser, tmp_path):
    load_static_html_fixture(browser, tmp_path, "repo-chip-grid-columns.html", page_html("""
      <div class="terminal-context-menu repo-chip-menu" style="width:760px">
        <button type="button" class="repo-chip-row"><span class="repo-chip-path">~/yolomux.dev8001</span><span class="repo-chip-branch meta-branch">yolomux.dev8001</span><span class="repo-chip-status"><span class="meta-muted">51 dirty</span><span class="meta-muted">6 ahead</span></span></button>
        <button type="button" class="repo-chip-row"><span class="repo-chip-path">~/yolomux.dev8002</span><span class="repo-chip-branch meta-branch">yolomux.dev8002</span><span class="repo-chip-status"><span class="meta-muted">9 ahead</span></span></button>
      </div>
    """, extra_css="body { margin:0; padding:24px; background:var(--bg); color:var(--text); }"))
    metrics = browser.execute_script(
        """
        const column = selector => [...document.querySelectorAll(selector)].map(node => {
          const rect = node.getBoundingClientRect();
          return {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom};
        });
        return {branches: column('.repo-chip-branch'), statuses: column('.repo-chip-status'), rows: column('.repo-chip-row')};
        """
    )
    assert abs(metrics["branches"][0]["left"] - metrics["branches"][1]["left"]) <= 0.5, metrics
    assert abs(metrics["statuses"][0]["left"] - metrics["statuses"][1]["left"]) <= 0.5, metrics
    assert all(column["left"] >= row["left"] and column["right"] <= row["right"] for column, row in zip(metrics["branches"], metrics["rows"])), metrics


def test_debug_graph_client_work_does_not_steal_chart_height(browser, tmp_path):
    page = tmp_path / "debug-graph-client-work-layout.html"
    client_perf = """
      <div class="js-debug-client-perf" data-js-debug-client-perf>
        <div class="js-debug-client-perf-title">Client work | animations 1 | long tasks 0</div>
        <div class="js-debug-client-perf-grid">
          <div class="js-debug-client-perf-row">focusSet n=3 avg=0.2ms</div>
          <div class="js-debug-client-perf-row">wsSend n=8 avg=0.1ms</div>
          <div class="js-debug-client-perf-row">renderInfoPanel n=2 avg=3.5ms</div>
        </div>
      </div>
    """
    chart_shell = """
      <div class="js-debug-chart-shell">
        <div class="js-debug-chart-grid" data-js-debug-chart-grid>
          <section class="js-debug-chart">
            <div class="js-debug-chart-head"><span class="js-debug-chart-title">Client latency</span></div>
            <div class="js-debug-chart-body"><div class="js-debug-plot"><svg class="js-debug-line-chart" viewBox="0 0 600 120"></svg></div></div>
          </section>
        </div>
      </div>
    """
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(f"""
      <section class="js-debug-graph-view">
        <div id="graph-with-client-work" class="js-debug-graph" data-js-debug-graph>
          <div class="js-debug-graph-controls"><span class="js-debug-resolution-label">Resolution: 1s</span></div>
          <div class="js-debug-graph-meta">PID=123 | total 1/2 MB up/down</div>
          {client_perf}
          {chart_shell}
        </div>
        <div id="graph-without-client-work" class="js-debug-graph" data-js-debug-graph>
          <div class="js-debug-graph-controls"><span class="js-debug-resolution-label">Resolution: 1s</span></div>
          <div class="js-debug-graph-meta">PID=123 | total 1/2 MB up/down</div>
          {chart_shell}
        </div>
        <div id="graph-empty-with-client-work" class="js-debug-graph js-debug-graph--empty" data-js-debug-graph>
          <div class="js-debug-graph-controls"><span class="js-debug-resolution-label">Resolution: 1s</span></div>
          <div class="js-debug-graph-meta">waiting for server stats</div>
          {client_perf}
          <div class="js-debug-graph-empty">No data</div>
        </div>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 24px; background: var(--bg); color: var(--text); }
      .js-debug-graph-view { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; height: 380px; }
      .js-debug-graph { height: 340px; }
    """),
    )
    metrics = browser.execute_script(
        """
        const rect = selector => {
          const node = document.querySelector(selector);
          const item = node.getBoundingClientRect();
          return {top: item.top, bottom: item.bottom, height: item.height};
        };
        const rowGap = graphSelector => {
          const rows = Array.from(document.querySelectorAll(`${graphSelector} .js-debug-client-perf-row`)).map(node => node.getBoundingClientRect());
          return rows.length > 1 ? rows[1].top - rows[0].bottom : 0;
        };
        return {
          withClientWork: {
            graph: rect('#graph-with-client-work'),
            client: rect('#graph-with-client-work .js-debug-client-perf'),
            chart: rect('#graph-with-client-work .js-debug-chart-shell'),
            rowGap: rowGap('#graph-with-client-work'),
          },
          withoutClientWork: {
            graph: rect('#graph-without-client-work'),
            chart: rect('#graph-without-client-work .js-debug-chart-shell'),
          },
          emptyWithClientWork: {
            graph: rect('#graph-empty-with-client-work'),
            client: rect('#graph-empty-with-client-work .js-debug-client-perf'),
            empty: rect('#graph-empty-with-client-work .js-debug-graph-empty'),
            rowGap: rowGap('#graph-empty-with-client-work'),
          },
        };
        """
    )
    with_client = metrics["withClientWork"]
    assert with_client["client"]["height"] < with_client["graph"]["height"] * 0.4, metrics
    assert with_client["chart"]["height"] > with_client["graph"]["height"] * 0.45, metrics
    assert with_client["chart"]["top"] >= with_client["client"]["bottom"], metrics
    assert with_client["rowGap"] <= 6, metrics
    without_client = metrics["withoutClientWork"]
    assert without_client["chart"]["height"] > without_client["graph"]["height"] * 0.55, metrics
    assert without_client["chart"]["bottom"] <= without_client["graph"]["bottom"], metrics
    empty = metrics["emptyWithClientWork"]
    assert empty["client"]["height"] < empty["graph"]["height"] * 0.4, metrics
    assert empty["empty"]["height"] > empty["graph"]["height"] * 0.45, metrics
    assert empty["rowGap"] <= 6, metrics


def test_debug_graph_initial_history_overlay_uses_shared_animated_ellipsis(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof clearJsDebugServerHistory === 'function'
              && typeof debugGraphMetaHtml === 'function';
            """
        )
    )
    metrics = browser.execute_script(
        """
        clearJsDebugServerHistory();
        resetJsDebugHistoryReadiness();
        setJsDebugHistoryReadiness('loading-initial', {
          requestedRangeSeconds: 900,
          targetStartSeconds: Math.floor(Date.now() / 1000) - 900,
          targetEndSeconds: Math.floor(Date.now() / 1000),
          requestedResolutionSeconds: 5,
          generation: 1,
        });
        renderDebugPanels({force: true});
        const graph = document.querySelector('[data-js-debug-graph]');
        const meta = graph.querySelector('.js-debug-graph-meta');
        const overlay = graph.querySelector('[data-js-debug-history-overlay]');
        const dots = Array.from(overlay.querySelectorAll('.moving-ellipsis > span'));
        return {
          busy: graph.getAttribute('aria-busy'),
          phase: graph.dataset.jsDebugHistoryState,
          text: overlay.textContent,
          waitingMeta: meta.textContent,
          dotCount: dots.length,
          animationNames: dots.map(dot => getComputedStyle(dot).animationName),
          labelText: Array.from(overlay.querySelector('.js-debug-history-overlay-message > span').childNodes).filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join(''),
        };
        """
    )
    assert metrics["busy"] == "true", metrics
    assert metrics["phase"] == "loading-initial", metrics
    assert metrics["text"].startswith("Loading history"), metrics
    assert metrics["waitingMeta"] == "", metrics
    assert metrics["dotCount"] == 3, metrics
    assert all(name == "moving-ellipsis-dot" for name in metrics["animationNames"]), metrics
    assert metrics["labelText"] == "Loading history", metrics


@pytest.mark.boot
def test_language_switch_relocalizes_open_help_and_stats(browser, tmp_path):
    selected_path = "/home/test/project/state.txt"
    source_bundle = tmp_path / "yolomux-source.js"
    source_bundle.write_text(build_asset("yolomux.js"), encoding="utf-8")
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?debug=1&sessions=files,1,debug",
        sessions=["1"],
        runtime_script_uri=source_bundle.as_uri(),
        file_explorer_open_intent="1",
        fs_entries={
            "/home/test": [{"name": "project", "path": "/home/test/project", "kind": "dir"}],
            "/home/test/project": [{"name": "state.txt", "path": selected_path, "kind": "file", "size": 17}],
        },
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof applyLocale === 'function'
              && typeof openKeyboardShortcutsOverlay === 'function'
              && typeof debugGraphApplyServerHistory === 'function'
              && typeof fileEditorItemFor === 'function'
              && typeof applyLayoutSlots === 'function'
              && typeof jsDebugGraphChartGroups !== 'undefined'
              && window.__terminalOpened >= 1
              && window.__eventSources.length >= 1
              && document.querySelector('#grid') !== null;
            """
        )
    )
    locale_catalogs = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(Path("static_src/locales").glob("*.json"))
    }
    assert set(locale_catalogs) == set(SHIPPED_LOCALES)
    metrics = browser.execute_async_script(
        r"""
        const localeCatalogs = arguments[0];
        const selectedPath = arguments[1];
        const done = arguments[arguments.length - 1];
        (async () => {
          for (const [locale, catalog] of Object.entries(localeCatalogs)) {
            i18nSetCatalogForTest(locale, catalog);
          }
          const selectLocale = async locale => {
            // A real picker change persists general.language before applyLocale(). Keep the fixture's
            // settings response in lock-step so a concurrent settings refresh cannot race the matrix
            // back to the bootstrap English preference.
            clientSettings = mergeSettingObjects(clientSettings, {general: {language: locale}});
            window.__settingsPayload.settings = mergeSettings(window.__settingsPayload.settings || {}, {general: {language: locale}});
            for (let attempt = 0; attempt < 3; attempt += 1) {
              await applyLocale(locale);
              await frame();
              await frame();
              if (i18nActiveLocale === locale) return;
            }
            throw new Error(`locale switch did not settle: ${locale} -> ${i18nActiveLocale}`);
          };
          stopJsDebugStatsPolling();
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = window.__yolomuxTestWaitFor;

          // Keep all stateful pane families mounted while the global Help overlay and YO!stats
          // relocalize. This catches locale refreshes that accidentally clear Finder, CodeMirror,
          // or terminal state even when the translated labels themselves look correct.
          const editorPath = '/home/test/project/locale-state.md';
          const editorItem = fileEditorItemFor(editorPath);
          const editorText = Array.from({length: 48}, (_value, index) => `STATE_${String(index + 1).padStart(2, '0')}_${'X'.repeat(160)}`).join('\n');
          fileEditorWrapEnabled = false;
          setFileState(editorPath, {
            kind: 'text', content: editorText, original: editorText, dirty: false,
            language: 'markdown', gitRoot: '/home/test/project', gitTracked: true,
            gitHasHistory: true, gitHistory: [{ref: 'HEAD'}],
          });
          setFileEditorViewMode(editorPath, 'edit', editorItem);
          registerFileEditorLayoutItem(editorPath, {item: editorItem});
          fileExplorerRoot = '/home/test';
          fileExplorerRootMode = 'fixed';
          fileExplorerExpanded.clear();
          fileExplorerExpanded.add('/home/test/project');
          fileExplorerSelectedPaths.clear();
          fileExplorerSelectedPaths.add(selectedPath);
          fileExplorerSelectionAnchor = selectedPath;
          fileExplorerSelectionLead = selectedPath;
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = splitNode('row', leafNode('finder'), splitNode(
            'row',
            leafNode('editor'),
            splitNode('col', leafNode('terminal'), leafNode('stats'), 50),
            48,
          ), 24);
          next.finder = paneStateWithTabs([fileExplorerItemId], fileExplorerItemId);
          next.editor = paneStateWithTabs([editorItem], editorItem);
          next.terminal = paneStateWithTabs(['1'], '1');
          next.stats = paneStateWithTabs([debugPaneItemId], debugPaneItemId);
          applyLayoutSlots(next, {focusSession: editorItem, forceFull: true});
          const finderRootReady = await waitFor(() => panelNodes.get(fileExplorerItemId)
            ?.querySelector('.file-tree-row[data-path="/home/test/project"]'));
          if (finderRootReady) {
            const finder = panelNodes.get(fileExplorerItemId);
            const projectRow = finder.querySelector('.file-tree-row[data-path="/home/test/project"]');
            await ensureDirectoryRowExpanded(projectRow, '/home/test/project');
            updateFileExplorerCurrentFileHighlight();
          }
          const panesReady = await waitFor(() => {
            const finder = panelNodes.get(fileExplorerItemId);
            const editor = panelNodes.get(editorItem);
            const terminal = terminals.get('1');
            const stats = panelNodes.get(debugPaneItemId);
            return finder?.querySelector(`.file-tree-row[data-path="${selectedPath}"]`)
              && editor?._cmView?.scrollDOM
              && terminal?.term?.element?.isConnected
              && stats?.querySelector('[data-js-debug-graph]');
          });
          if (!panesReady) {
            const finder = panelNodes.get(fileExplorerItemId);
            const editor = panelNodes.get(editorItem);
            const terminal = terminals.get('1');
            const stats = panelNodes.get(debugPaneItemId);
            done({
              error: 'stateful locale-matrix panes did not initialize',
              readiness: {
                finder: Boolean(finder),
                finderRows: Array.from(finder?.querySelectorAll('.file-tree-row[data-path]') || []).map(row => row.dataset.path),
                editor: Boolean(editor),
                editorView: Boolean(editor?._cmView?.scrollDOM),
                terminal: Boolean(terminal),
                terminalConnected: terminal?.term?.element?.isConnected === true,
                stats: Boolean(stats),
                statsGraph: Boolean(stats?.querySelector('[data-js-debug-graph]')),
                layout: JSON.parse(JSON.stringify(layoutSlots)),
              },
              bootErrors: window.__bootErrors,
              bootRejections: window.__bootRejections,
            });
            return;
          }
          const editorView = panelNodes.get(editorItem)._cmView;
          const editorAnchor = editorText.indexOf('STATE_24') + 5;
          editorView.dispatch({selection: {anchor: editorAnchor, head: editorAnchor + 4}});
          editorView.scrollDOM.scrollTop = 81;
          editorView.scrollDOM.scrollLeft = 47;
          const codeMirrorState = () => ({
            viewPreserved: panelNodes.get(editorItem)?._cmView === editorView,
            anchor: editorView.state.selection.main.anchor,
            head: editorView.state.selection.main.head,
            scrollTop: Math.round(editorView.scrollDOM.scrollTop),
            scrollLeft: Math.round(editorView.scrollDOM.scrollLeft),
          });
          const codeMirrorLocaleState = {before: codeMirrorState()};
          await selectLocale('de');
          await frame();
          await frame();
          codeMirrorLocaleState.after = codeMirrorState();
          editorView.scrollDOM.scrollTop = 73;
          const terminalItem = terminals.get('1');
          terminalItem.term.element.textContent = 'TERM_STATE_137';

          const now = Date.now();
          const clientId = jsDebugStatsClientIdForRequest();
          debugGraphApplyServerHistory({
            sequence: 190,
            records: [{
              start: Math.floor((now - 500) / 1000),
              duration: 1,
              sequence: 190,
              api_count: 3,
              sse_count: 2,
              latency_total_ms: 12,
              latency_count: 1,
              bandwidth_bytes: 4096,
              cpu_total_percent: 10,
              cpu_count: 1,
              system_cpu_total_percent: 20,
              system_cpu_count: 1,
              clients: {
                [clientId]: {api_count: 3, sse_count: 2, latency_total_ms: 12, latency_count: 1, bandwidth_bytes: 4096},
                'client-peer': {api_count: 4, sse_count: 1, latency_total_ms: 24, latency_count: 1, bandwidth_bytes: 2048},
              },
            }],
          });
          await selectLocale('vi');
          openKeyboardShortcutsOverlay();
          const beforeHeading = keyboardShortcutsNode?.querySelector('.keyboard-shortcuts-head h2')?.textContent || '';
          const normalizedText = value => String(value || '').replace(/\s+/g, ' ').trim();
          const visibleSurfaceValues = roots => {
            const values = [];
            const visible = node => {
              const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
              if (!element) return false;
              for (let current = element; current; current = current.parentElement) {
                if (current.hasAttribute?.('hidden')) return false;
                const style = getComputedStyle(current);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
              }
              return true;
            };
            for (const root of roots.filter(Boolean)) {
                  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
                    const value = normalizedText(node.textContent);
                    if (value && visible(node)) values.push({
                      sink: 'text',
                      value,
                      element: node.parentElement?.outerHTML || '',
                      technical: Boolean(node.parentElement?.closest?.('kbd, code, pre')),
                    });
                  }
              for (const node of [root, ...root.querySelectorAll('*')]) {
                if (!visible(node)) continue;
                for (const attribute of ['title', 'aria-label', 'placeholder']) {
                      const value = normalizedText(node.getAttribute?.(attribute));
                      if (value) values.push({sink: attribute, value, element: node.outerHTML || ''});
                }
              }
            }
            return values;
          };
          const obviousSourceEnglishLeaks = (locale, catalog, values) => {
            if (locale === 'en') return [];
            const english = localeCatalogs.en;
            const keysBySourceValue = new Map();
            for (const [key, raw] of Object.entries(english)) {
              if (typeof raw !== 'string') continue;
              const source = normalizedText(raw);
              if (!keysBySourceValue.has(source)) keysBySourceValue.set(source, []);
              keysBySourceValue.get(source).push(key);
            }
            const candidates = [];
            for (const [source, keys] of keysBySourceValue) {
              // If any key intentionally retains this source value in the target catalog, it is a
              // technical/proper-name value, not evidence of an untranslated visible sink.
              if (keys.some(key => normalizedText(catalog[key]) === source)) continue;
              const matchInsideValue = source.length >= 10
                && !/[{}<>`\n]/.test(source)
                && /[A-Za-z]{3,}\s+[A-Za-z]{3,}/.test(source);
              candidates.push({source, keys, matchInsideValue});
            }
            const leaks = [];
            for (const entry of values) {
              if (entry.technical) continue;
              for (const candidate of candidates) {
                if (entry.value !== candidate.source
                    && (!candidate.matchInsideValue || !entry.value.includes(candidate.source))) continue;
                leaks.push({sink: entry.sink, value: entry.value, source: candidate.source, keys: candidate.keys.slice(0, 4), element: entry.element.slice(0, 600)});
                if (leaks.length >= 12) return leaks;
              }
            }
            return leaks;
          };
          const preservedState = () => {
            const finder = panelNodes.get(fileExplorerItemId);
            const editor = panelNodes.get(editorItem);
            const terminal = terminals.get('1');
            const selectedRow = finder?.querySelector(`.file-tree-row[data-path="${selectedPath}"]`);
            return {
              finderConnected: finder?.isConnected === true,
              finderRoot: fileExplorerRoot,
              finderExpanded: fileExplorerExpanded.has('/home/test/project'),
              finderSelected: fileExplorerSelectedPaths.has(selectedPath) && selectedRow?.classList.contains('selected') === true,
              finderAnchor: fileExplorerSelectionAnchor,
              editorConnected: editor?.isConnected === true,
              editorViewPreserved: editor?._cmView === editorView,
              editorText: editor?._cmView?.state.doc.toString() || '',
              editorAnchor: editor?._cmView?.state.selection.main.anchor ?? -1,
              editorHead: editor?._cmView?.state.selection.main.head ?? -1,
              editorScrollTop: Math.round(editor?._cmView?.scrollDOM.scrollTop || 0),
              terminalConnected: terminal?.term?.element?.isConnected === true,
              terminalPreserved: terminal === terminalItem,
              terminalText: terminal?.term?.element?.textContent || '',
              };
            };
          const baselineState = preservedState();
          const surfaceMatrix = {};
          for (const [locale, catalog] of Object.entries(localeCatalogs)) {
            await selectLocale(locale);
            await frame();
            await frame();
            const help = keyboardShortcutsNode;
            const stats = panelNodes.get(debugPaneItemId);
            const finder = panelNodes.get(fileExplorerItemId);
            const editor = panelNodes.get(editorItem);
            const terminal = panelNodes.get('1');
              const expectedChartTitles = jsDebugGraphChartGroups
                .filter(group => debugGraphChartVisible(group.key))
                .map(group => catalog[group.labelKey]);
            surfaceMatrix[locale] = {
              activeLocale: i18nActiveLocale,
              resolvedHelpHeading: t('common.keyboardShortcuts'),
              helpHeading: help?.querySelector('.keyboard-shortcuts-head h2')?.textContent || '',
              helpAria: help?.querySelector('.keyboard-shortcuts-dialog')?.getAttribute('aria-label') || '',
              statsTitle: stats?.querySelector('.panel-session-label')?.textContent || '',
              chartTitles: Array.from(stats?.querySelectorAll('.js-debug-chart-title') || []).map(node => node.textContent),
              languageTitle: document.querySelector('.topbar-language')?.title || '',
              lang: document.documentElement.lang,
              dir: document.documentElement.dir,
              helpConnected: Boolean(help?.isConnected),
              statsConnected: Boolean(stats?.isConnected),
              state: preservedState(),
              englishLeaks: obviousSourceEnglishLeaks(
                locale,
                catalog,
                visibleSurfaceValues([help, stats, finder, editor, terminal]),
              ),
              expected: {
                helpHeading: catalog['common.keyboardShortcuts'],
                statsTitle: catalog['tab.debug'],
                chartTitles: expectedChartTitles,
                languageTitle: catalog['common.language'],
              },
            };
          }
          await selectLocale('zh-Hant');
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const help = keyboardShortcutsNode;
          const helpText = help?.textContent || '';
          const stats = panelNodes.get(debugPaneItemId);
          const statsText = stats?.textContent || '';
          const zhHant = {
            heading: help?.querySelector('.keyboard-shortcuts-head h2')?.textContent || '',
            sections: Array.from(help?.querySelectorAll('.keyboard-shortcuts-section h3') || []).map(node => node.textContent),
            englishLeak: /Agent status glyphs|Color meanings|Icon meanings|YO button meanings|Menus, palettes, and pickers|Open selected file or folder/.test(helpText),
            markerTexts: Array.from(help?.querySelectorAll('.session-yolo-marker') || []).map(node => node.textContent),
            statsTitle: stats?.querySelector('.panel-session-label')?.textContent || '',
            chartTitles: Array.from(stats?.querySelectorAll('.js-debug-chart-title') || []).map(node => node.textContent),
            statsEnglishLeak: /Client latency|Client bandwidth|Agent status|Agent tokens\/min|other clients avg|this client|Graph bucket size|Graph time range/.test(statsText),
          };
          await selectLocale('he');
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          done({
            beforeHeading,
            codeMirrorLocaleState,
            baselineState,
            surfaceMatrix,
            zhHant,
            hebrew: {
              heading: help?.querySelector('.keyboard-shortcuts-head h2')?.textContent || '',
              statsTitle: stats?.querySelector('.panel-session-label')?.textContent || '',
              chartTitles: Array.from(stats?.querySelectorAll('.js-debug-chart-title') || []).map(node => node.textContent),
              dir: document.documentElement.dir,
            },
          });
        })().catch(error => done({error: String(error), stack: error?.stack || ''}));
        """,
        locale_catalogs,
        selected_path,
    )
    assert "error" not in metrics, metrics
    assert set(metrics["surfaceMatrix"]) == set(locale_catalogs), metrics
    expected_editor_text = "\n".join(f"STATE_{index:02d}_{'X' * 160}" for index in range(1, 49))
    expected_editor_anchor = expected_editor_text.index("STATE_24") + 5
    assert metrics["baselineState"] == {
        "finderConnected": True,
        "finderRoot": "/home/test",
        "finderExpanded": True,
        "finderSelected": True,
        "finderAnchor": selected_path,
        "editorConnected": True,
        "editorViewPreserved": True,
        "editorText": expected_editor_text,
        "editorAnchor": expected_editor_anchor,
        "editorHead": expected_editor_anchor + 4,
        "editorScrollTop": metrics["baselineState"]["editorScrollTop"],
        "terminalConnected": True,
        "terminalPreserved": True,
        "terminalText": "TERM_STATE_137",
    }, metrics["baselineState"]
    assert metrics["baselineState"]["editorScrollTop"] > 0
    assert metrics["codeMirrorLocaleState"]["before"]["scrollTop"] == 81, metrics["codeMirrorLocaleState"]
    assert metrics["codeMirrorLocaleState"]["before"]["scrollLeft"] > 0, metrics["codeMirrorLocaleState"]
    assert metrics["codeMirrorLocaleState"]["after"] == metrics["codeMirrorLocaleState"]["before"], metrics["codeMirrorLocaleState"]
    for locale, surface in metrics["surfaceMatrix"].items():
        assert surface["activeLocale"] == locale, (locale, surface)
        assert surface["helpHeading"] == surface["expected"]["helpHeading"], (locale, surface)
        assert surface["helpAria"] == surface["expected"]["helpHeading"], (locale, surface)
        assert surface["statsTitle"] == surface["expected"]["statsTitle"], (locale, surface)
        assert set(surface["expected"]["chartTitles"]).issubset(set(surface["chartTitles"])), (locale, surface)
        assert surface["languageTitle"] == surface["expected"]["languageTitle"], (locale, surface)
        assert surface["lang"] == locale, (locale, surface)
        assert surface["dir"] == ("rtl" if locale in {"ar", "he"} else "ltr"), (locale, surface)
        assert surface["helpConnected"] is True and surface["statsConnected"] is True, (locale, surface)
        assert surface["state"] == metrics["baselineState"], (locale, surface["state"], metrics["baselineState"])
        assert surface["englishLeaks"] == [], json.dumps({"locale": locale, "leaks": surface["englishLeaks"]}, indent=2)
    assert metrics["beforeHeading"] == "Phím tắt", metrics
    assert metrics["zhHant"]["heading"] == "鍵盤快速鍵", metrics
    assert {"代理狀態圖示", "顏色含義", "圖示含義", "優按鈕含義"}.issubset(set(metrics["zhHant"]["sections"])), metrics
    assert metrics["zhHant"]["englishLeak"] is False, metrics
    assert metrics["zhHant"]["markerTexts"] and set(metrics["zhHant"]["markerTexts"]) == {"優"}, metrics
    assert metrics["zhHant"]["statsTitle"] == "優!統計", metrics
    assert {"用戶端延遲", "用戶端 API 與 SSE/秒", "用戶端頻寬/秒", "代理狀態", "代理權杖/分鐘"}.issubset(set(metrics["zhHant"]["chartTitles"])), metrics
    assert metrics["zhHant"]["statsEnglishLeak"] is False, metrics
    assert metrics["hebrew"]["heading"] == "קיצורי מקלדת", metrics
    assert metrics["hebrew"]["statsTitle"] == "YO!stats", metrics
    assert "זמן אחזור לקוח" in metrics["hebrew"]["chartTitles"], metrics
    assert metrics["hebrew"]["dir"] == "rtl", metrics


def test_debug_graph_first_stats_sample_bypasses_steady_render_throttle(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof clearJsDebugEvents === 'function'
              && typeof pollJsDebugStatsSample === 'function'
              && typeof scheduleJsDebugPanelRefresh === 'function'
              && document.querySelector('[data-js-debug-graph]') !== null;
            """
        )
    )
    initial = browser.execute_script(
        """
        stopJsDebugStatsPolling();
        clearJsDebugEvents();
        const graph = document.querySelector('[data-js-debug-graph]');
        const originalFetch = window.fetch;
        const now = Date.now();
        window.__firstStatsSample = {
          startedAt: performance.now(),
          renderedAt: Number(graph.dataset.jsDebugGraphRenderedAt),
          pollRequests: 0,
        };
        window.fetch = (input, options = {}) => {
          const url = new URL(String(input), 'https://localhost');
          if (url.pathname !== '/api/stats-sample') return originalFetch(input, options);
          window.__firstStatsSample.pollRequests += 1;
          return jsonResponse({
            time: now / 1000,
            uptime_seconds: 60,
            pid: 4242,
            rss_bytes: 1048576,
            cpu_percent: 7.5,
            system_cpu_percent: 22.5,
            history: {
              sequence: 1,
              records: [{
                start: Math.floor(now / 1000),
                duration: 1,
                sequence: 1,
                api_count: 2,
                sse_count: 1,
                latency_total_ms: 15.5,
                latency_count: 2,
                bandwidth_bytes: 4096,
                cpu_total_percent: 7.5,
                cpu_count: 1,
                system_cpu_total_percent: 22.5,
                system_cpu_count: 1,
              }],
              coverage: {
                mode: 'live',
                requested_start: Number(url.searchParams.get('history_start')),
                requested_end: 0,
                covered_start: Number(url.searchParams.get('history_start')),
                covered_end: Math.floor(now / 1000),
                resolution_seconds: Number(url.searchParams.get('history_resolution')),
                complete: true,
                has_more_older: false,
                next_older_end: 0,
              },
            },
          });
        };
        scheduleJsDebugPanelRefresh();
        pollJsDebugStatsSample().then(() => {
          window.__firstStatsSample.pollSettledAt = performance.now();
        });
        return {
          waiting: graph.textContent.includes('Waiting for server stats'),
          loading: graph.textContent.includes('Loading history'),
          busy: graph.getAttribute('aria-busy'),
          chartCount: graph.querySelectorAll('[data-js-debug-chart]').length,
          renderedAt: window.__firstStatsSample.renderedAt,
        };
        """
    )
    assert initial["waiting"] is False, initial
    assert initial["loading"] is True, initial
    assert initial["busy"] == "true", initial
    assert initial["chartCount"] == 0, initial
    WebDriverWait(browser, 3).until(
        lambda driver: driver.execute_script(
            """
            const state = window.__firstStatsSample;
            const graph = document.querySelector('[data-js-debug-graph]');
            const renderedAt = Number(graph?.dataset.jsDebugGraphRenderedAt);
            if (!state?.pollSettledAt || !(renderedAt > state.renderedAt)) return false;
            state.finishedAt = performance.now();
            state.waiting = graph.textContent.includes('Waiting for server stats');
            state.chartCount = graph.querySelectorAll('[data-js-debug-chart]').length;
            state.renderedAtAfter = renderedAt;
                return !state.waiting && state.chartCount === 6;
            """
        )
    )
    result = browser.execute_script("return window.__firstStatsSample")
    assert result["pollRequests"] == 1, result
    assert result["renderedAtAfter"] > result["renderedAt"], result
    assert result["finishedAt"] - result["startedAt"] < 3000, result


def test_debug_graph_chrome_refocus_fetches_missed_history_and_redraws_immediately(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof pollJsDebugStatsSample === 'function'
              && typeof renderDebugPanels === 'function'
              && document.querySelector('[data-js-debug-graph]') !== null
              && jsDebugStatsPollState.inFlight === false;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        (async () => {
          stopJsDebugStatsPolling();
          clearJsDebugGraphData();
          jsDebugStatsPollState.firstSampleReceived = true;
          jsDebugStatsPollState.inFlight = false;
          jsDebugStatsPollState.pending = false;
          jsDebugStatsPollState.pendingForceGraphRefresh = false;
          jsDebugStatsServerSequence = 1;
          const nowSeconds = Math.floor(Date.now() / 1000);
          recordJsDebugStatsSample({history: {sequence: 1, records: [{
            start: nowSeconds - 10 * 60,
            duration: 1,
            sequence: 1,
            system_cpu_total_percent: 10,
            system_cpu_count: 1,
          }]}}, {forceGraphRefresh: true});
          setJsDebugHistoryReadiness('ready', {
            loadedStartSeconds: nowSeconds - 15 * 60,
            loadedEndSeconds: nowSeconds,
            resolutionSeconds: 5,
            coverageIntervals: [{startSeconds: 0, endSeconds: Infinity, resolutionSeconds: 5}],
          });
          renderDebugPanels({force: true});
          const graph = document.querySelector('[data-js-debug-graph]');
          const renderedBefore = Number(graph.dataset.jsDebugGraphRenderedAt);
          const originalFetch = window.fetch;
          let requests = 0;
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            if (url.pathname !== '/api/stats-sample') return originalFetch(input, options);
            requests += 1;
            return Promise.resolve(new Response(JSON.stringify({history: {
              sequence: 2,
              records: [{
                start: nowSeconds - 15,
                duration: 1,
                sequence: 2,
                system_cpu_total_percent: 30,
                system_cpu_count: 1,
              }],
              coverage: {
                mode: 'live',
                requested_start: Number(url.searchParams.get('history_start')),
                requested_end: Number(url.searchParams.get('history_end')),
                covered_start: Number(url.searchParams.get('history_start')),
                covered_end: nowSeconds,
                resolution_seconds: Number(url.searchParams.get('history_resolution')),
                complete: true,
                has_more_older: false,
                next_older_end: 0,
              },
            }}), {status: 200, headers: {'Content-Type': 'application/json'}}));
          };
          try {
            Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true});
            document.dispatchEvent(new Event('visibilitychange'));
            await new Promise(resolve => setTimeout(resolve, 20));
            const refocusedAt = performance.now();
            Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true});
            document.dispatchEvent(new Event('visibilitychange'));
            await window.__yolomuxTestWaitFor(() => {
              const currentGraph = document.querySelector('[data-js-debug-graph]');
              const currentRenderedAt = Number(currentGraph?.dataset.jsDebugGraphRenderedAt);
              const currentPointCount = Array.from(currentGraph?.querySelectorAll('[data-js-debug-series="systemCpu"]') || [])
                .flatMap(line => String(line.getAttribute('points') || '').trim().split(/\\s+/).filter(Boolean)).length;
              return !jsDebugStatsPollState.inFlight && requests >= 1 && currentRenderedAt > renderedBefore && currentPointCount >= 2;
            }, {timeoutMs: 2000, intervalMs: 10, description: 'YO!stats visibility-refocus redraw'});
            const refreshedGraph = document.querySelector('[data-js-debug-graph]');
            const renderedAfter = Number(refreshedGraph.dataset.jsDebugGraphRenderedAt);
            const points = Array.from(document.querySelectorAll('[data-js-debug-series="systemCpu"]'))
              .flatMap(line => String(line.getAttribute('points') || '').trim().split(/\\s+/).filter(Boolean));
            return {
              requests,
              renderedBefore,
              renderedAfter,
              redrawDelayMs: performance.now() - refocusedAt,
              pointCount: points.length,
            };
          } finally {
            window.fetch = originalFetch;
            stopJsDebugStatsPolling();
            delete document.visibilityState;
          }
        })().then(done).catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["requests"] == 1, metrics
    assert metrics["renderedAfter"] > metrics["renderedBefore"], metrics
    assert metrics["redrawDelayMs"] < 1000, metrics
    assert metrics["pointCount"] >= 2, metrics


def test_debug_graph_agent_status_uses_stacked_bounded_resolution_bars(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof recordJsDebugStatsSample === 'function'
              && typeof setDebugGraphRange === 'function'
              && document.querySelector('[data-js-debug-graph]') !== null;
            """
        )
    )
    metrics = browser.execute_script(
        """
        stopJsDebugStatsPolling();
        clearJsDebugGraphData();
        jsDebugStatsPollState.firstSampleReceived = true;
        setDebugGraphRange(60 * 60);
        const displayBucketSeconds = 30;
        const nowSeconds = Math.floor(Date.now() / (displayBucketSeconds * 1000)) * displayBucketSeconds;
        recordJsDebugStatsSample({history: {sequence: 2, records: [
          {
            start: nowSeconds - (displayBucketSeconds * 2),
            duration: 1,
            sequence: 1,
            ask_agent_total: 1,
            run_agent_total: 1,
            transition_agent_total: 1,
            idle_agent_total: 1,
            agent_activity_samples: 1,
          },
          {
            start: nowSeconds - displayBucketSeconds,
            duration: 1,
            sequence: 2,
            ask_agent_total: 1,
            run_agent_total: 1,
            transition_agent_total: 1,
            idle_agent_total: 1,
            agent_activity_samples: 1,
          },
        ]}}, {forceGraphRefresh: true});
        renderDebugPanels({force: true});
        const chart = document.querySelector('[data-js-debug-chart="activity"]');
        const bars = Array.from(chart?.querySelectorAll('[data-js-debug-bar-series]') || []);
        const barsByX = {};
        for (const bar of bars) {
          const x = bar.getAttribute('x');
          barsByX[x] = (barsByX[x] || 0) + 1;
        }
        const fillBySeries = Object.fromEntries(bars.map(bar => [
          bar.dataset.jsDebugBarSeries,
          getComputedStyle(bar).fill,
        ]));
        const opacityBySeries = Object.fromEntries(bars.map(bar => [
          bar.dataset.jsDebugBarSeries,
          getComputedStyle(bar).opacity,
        ]));
        const activitySvg = chart?.querySelector('.js-debug-line-chart');
        const activityGrid = chart?.closest('[data-js-debug-graph]')?.querySelector('[data-js-debug-chart-grid]');
        const activityRect = activitySvg?.getBoundingClientRect();
        const hoverTime = (nowSeconds - (displayBucketSeconds * 1.5)) * 1000;
        const hoverRatio = (hoverTime - Number(activityGrid?.dataset.jsDebugDomainStart)) / (Number(activityGrid?.dataset.jsDebugDomainEnd) - Number(activityGrid?.dataset.jsDebugDomainStart));
        activitySvg?.dispatchEvent(new PointerEvent('pointermove', {
          bubbles: true,
          clientX: activityRect.left + (activityRect.width * hoverRatio),
          clientY: activityRect.top + (activityRect.height / 2),
        }));
        const hoverMax = chart?.querySelector('[data-js-debug-hover-max]')?.textContent || '';
        setDebugGraphRange(24 * 60 * 60);
        renderDebugPanels({force: true});
        const datedTicks = Array.from(document.querySelectorAll('[data-js-debug-chart="activity"] [data-js-debug-x-tick]')).map(tick => ({
          name: tick.dataset.jsDebugXTick,
          date: tick.dataset.jsDebugXDate || '',
          text: tick.textContent || '',
        }));
        return {
          kind: chart?.dataset.jsDebugChartKind || '',
          bucketSeconds: Number(chart?.dataset.jsDebugChartBucketSeconds),
          stacked: chart?.dataset.jsDebugChartStacked || '',
          areaCount: chart?.querySelectorAll('[data-js-debug-area-series]').length || 0,
          barCount: bars.length,
          widths: bars.map(bar => Number(bar.getAttribute('width'))),
          gaps: bars.map(bar => Number(bar.dataset.jsDebugBarGap)),
          barsPerX: Object.values(barsByX),
          fillBySeries,
          opacityBySeries,
          hoverMax,
          datedTicks,
        };
        """
    )
    assert metrics["kind"] == "bar", metrics
    assert metrics["bucketSeconds"] == 10, metrics
    assert metrics["stacked"] == "true", metrics
    assert metrics["areaCount"] == 0, metrics
    assert metrics["barCount"] == 8, metrics
    assert all(4.9 <= width <= 5.1 for width in metrics["widths"]), metrics
    assert set(metrics["gaps"]) == {0}, metrics
    assert sorted(metrics["barsPerX"]) == [4, 4], metrics
    assert len(set(metrics["fillBySeries"].values())) == 4, metrics
    assert metrics["opacityBySeries"] == {
        "askAgents": "0.82",
        "workingAgents": "0.82",
        "transitionAgents": "0.82",
        "idleAgents": "0.3",
    }, metrics
    assert metrics["hoverMax"] == "3", metrics
    assert [tick["name"] for tick in metrics["datedTicks"]] == ["start", "mid", "end"], metrics
    assert all(tick["date"] and len(tick["text"]) > 8 for tick in metrics["datedTicks"]), metrics
    assert metrics["datedTicks"][0]["date"] != metrics["datedTicks"][-1]["date"], metrics


def test_debug_graph_agent_tokens_use_color_and_infill_patterns(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof recordJsDebugStatsSample === 'function' && document.querySelector('[data-js-debug-graph]') !== null;"
        )
    )
    metrics = browser.execute_script(
        """
        stopJsDebugStatsPolling();
        clearJsDebugGraphData();
        jsDebugStatsPollState.firstSampleReceived = true;
        const nowSeconds = Math.floor(Date.now() / 60000) * 60;
        recordJsDebugStatsSample({history: {sequence: 4, records: [{
          start: nowSeconds - 60,
          duration: 60,
          sequence: 4,
          tokens_per_agent_total: 60,
          agent_token_samples: 1,
          agent_token_rates: [
            {key: '1|0|claude', label: '1:0:claude', total: 10, samples: 1, tokens: 10, seconds: 60, source: 'transcript'},
            {key: '1|1|codex', label: '1:1:codex', total: 20, samples: 1, tokens: 20, seconds: 60, source: 'transcript'},
            {key: '2|0|codex', label: '2:0:codex', total: 30, samples: 1, tokens: 30, seconds: 60, source: 'transcript'},
            {key: '3|0|codex', label: '3:0:codex', total: 40, samples: 1, tokens: 40, seconds: 60, source: 'transcript'},
            {key: '4|0|codex', label: '4:0:codex', total: 50, samples: 1, tokens: 50, seconds: 60, source: 'transcript'},
            {key: '5|0|codex', label: '5:0:codex', total: 60, samples: 1, tokens: 60, seconds: 60, source: 'transcript'},
            {key: '6|0|codex', label: '6:0:codex', total: 70, samples: 1, tokens: 70, seconds: 60, source: 'transcript'},
          ],
        }]}}, {forceGraphRefresh: true});
        renderDebugPanels({force: true});
        const chart = document.querySelector('[data-js-debug-chart="agentTokens"]');
        const bars = [...chart.querySelectorAll('[data-js-debug-bar-series^="agentToken:"]')];
        const legends = [...chart.querySelectorAll('[data-js-debug-legend^="agentToken:"]')];
        return {
          patternDefs: [...chart.querySelectorAll('[data-js-debug-token-pattern-def]')].map(node => ({
            id: node.id,
            pattern: node.dataset.jsDebugTokenPatternDef,
            definition: node.innerHTML,
            commands: [...node.querySelectorAll('.js-debug-agent-token-pattern-ink path')]
              .flatMap(path => (path.getAttribute('d')?.match(/[A-Za-z]/g) || [])),
          })),
          bars: bars.map(bar => ({pattern: bar.dataset.jsDebugTokenPattern, gap: Number(bar.dataset.jsDebugBarGap), fill: getComputedStyle(bar).fill, style: bar.getAttribute('style') || ''})),
          legends: legends.map(item => {
            const swatch = item.querySelector('.js-debug-legend-token-swatch');
            const pattern = swatch?.querySelector('[data-js-debug-token-legend-pattern-def]');
            const rect = [...(swatch?.children || [])].find(node => node.tagName?.toLowerCase() === 'rect');
            return {pattern: item.dataset.jsDebugTokenPattern, fill: getComputedStyle(rect).fill, fillAttr: rect?.getAttribute('fill') || '', definition: pattern?.innerHTML || ''};
          }),
        };
        """
    )
    assert [item["pattern"] for item in metrics["patternDefs"]] == [str(index) for index in range(7)], metrics
    assert len({item["id"] for item in metrics["patternDefs"]}) == 7, metrics
    assert metrics["patternDefs"][0]["commands"] == [], metrics
    assert all(item["commands"] and set(item["commands"]) <= {"M", "H"} for item in metrics["patternDefs"][1:]), metrics
    assert [item["pattern"] for item in metrics["bars"]] == [str(index) for index in range(7)], metrics
    assert all(item["gap"] > 0 for item in metrics["bars"]), metrics
    assert all("url(" in item["fill"] or "fill: url(" in item["style"] for item in metrics["bars"]), metrics
    assert [item["pattern"] for item in metrics["legends"]] == [str(index) for index in range(7)], metrics
    assert all(item["fillAttr"].startswith("url(") for item in metrics["legends"]), metrics
    assert [item["definition"] for item in metrics["legends"]] == [item["definition"] for item in metrics["patternDefs"]], metrics


def test_debug_graph_bad_connection_overlay_covers_full_graph_area(browser, tmp_path):
    page = tmp_path / "debug-graph-bad-connection-overlay.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <section class="js-debug-graph-view" id="debug-graph">
        <svg class="js-debug-line-chart" viewBox="0 0 600 120" role="img" preserveAspectRatio="none">
          <line class="js-debug-grid-line" x1="0" y1="60" x2="600" y2="60" vector-effect="non-scaling-stroke"></line>
          <polyline class="js-debug-line js-debug-line--bandwidth" points="0,100 600,20" fill="none"></polyline>
          <rect class="js-debug-disconnected-range" data-js-debug-disconnected-range="0" x="120" y="8" width="180" height="112"></rect>
        </svg>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 24px; background: var(--bg); color: var(--text); }
      #debug-graph { width: 600px; height: 160px; }
      .js-debug-line-chart { height: 120px; }
    """),
    )
    metrics = browser.execute_script(
        """
        const svg = document.querySelector('.js-debug-line-chart');
        const overlay = document.querySelector('.js-debug-disconnected-range');
        const svgRect = svg.getBoundingClientRect();
        const overlayRect = overlay.getBoundingClientRect();
        const style = getComputedStyle(overlay);
        return {
          svgHeight: svgRect.height,
          overlayTopDelta: Math.abs(overlayRect.top - svgRect.top),
          overlayHeightDelta: Math.abs(overlayRect.height - svgRect.height),
          fill: style.fill,
          pointerEvents: style.pointerEvents,
        };
        """
    )
    assert metrics["overlayTopDelta"] > 0.5, metrics
    assert metrics["overlayHeightDelta"] > 1.1, metrics
    assert metrics["fill"] == "rgba(220, 38, 38, 0.28)", metrics
    assert metrics["pointerEvents"] == "none", metrics


def test_debug_graph_zero_baseline_is_shared_by_lines_grid_axis_bars_and_overlays(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof debugGraphApplyServerHistory === 'function' && document.querySelector('[data-js-debug-graph]') !== null;"
        )
    )
    metrics = browser.execute_script(
        """
        stopJsDebugStatsPolling();
        clearJsDebugGraphData();
        const now = Date.now();
        debugGraphApplyServerHistory({sequence: 1, records: [{
          start: Math.floor((now - 500) / 1000), duration: 1, sequence: 1,
          cpu_total_percent: 0, cpu_count: 1, system_cpu_total_percent: 0, system_cpu_count: 1,
        }]});
        renderDebugPanels({force: true});
        const chart = document.querySelector('[data-js-debug-chart="cpu"]');
        const line = chart.querySelector('[data-js-debug-series="cpu"]');
        const grid = [...chart.querySelectorAll('[data-js-debug-grid-line="cpu"]')].find(node => Number(node.getAttribute('y1')) === jsDebugGraphGeometry.plotBottom);
        const axis = chart.querySelector('[data-js-debug-axis-zero="cpu"]');
        const pointY = Number(line.getAttribute('points').split(/[, ]+/)[1]);
        const zeroBar = debugGraphBarVerticalGeometry(0, 0, 1, true);
        const overlay = debugGraphPlotOverlayRectHtml('probe', 'data-probe', 0, 0, 1, 'probe');
        return {
          plotBottom: jsDebugGraphGeometry.plotBottom,
          pointY,
          gridY: Number(grid.getAttribute('y1')),
          axisY: axis.style.getPropertyValue('--js-debug-axis-y'),
          zeroBar,
          overlay,
        };
        """
    )
    assert metrics["pointY"] == metrics["plotBottom"], metrics
    assert metrics["gridY"] == metrics["plotBottom"], metrics
    assert metrics["axisY"] == "100.000%", metrics
    assert metrics["zeroBar"]["y"] + metrics["zeroBar"]["height"] == metrics["plotBottom"], metrics
    assert 'y="8"' in metrics["overlay"] and 'height="112"' in metrics["overlay"], metrics


def test_debug_graph_peer_traffic_shows_red_averages_without_marking_short_self_quiet_period(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof debugGraphApplyServerHistory === 'function'
              && typeof clearJsDebugGraphData === 'function'
              && document.querySelector('[data-js-debug-graph]') !== null;
            """
        )
    )
    metrics = browser.execute_script(
        """
        stopJsDebugStatsPolling();
        clearJsDebugGraphData();
        const now = Date.now();
        const bucketStart = offsetMs => Math.floor(((now - offsetMs) / 1000) / 5) * 5;
        const peerBucketStart = bucketStart(25_000);
        setDebugGraphRange(60, {render: false});
        debugGraphApplyServerHistory({
          sequence: 202,
          records: [
            {
              start: bucketStart(45_000), duration: 5, sequence: 200,
              api_count: 5, sse_count: 2, latency_total_ms: 150, latency_count: 3, bandwidth_bytes: 2048,
            },
            {
              start: peerBucketStart, duration: 5, sequence: 201,
              clients: {'peer-client': {
                api_count: 8, sse_count: 3, latency_total_ms: 240, latency_count: 4, bandwidth_bytes: 4096,
              }},
            },
            {
              start: bucketStart(5_000), duration: 5, sequence: 202,
              api_count: 4, sse_count: 1, latency_total_ms: 80, latency_count: 2, bandwidth_bytes: 1024,
            },
          ],
        });
        renderDebugPanels({force: true});
        const grid = document.querySelector('[data-js-debug-chart-grid]');
        const domainStart = Number(grid?.dataset.jsDebugDomainStart);
        const domainEnd = Number(grid?.dataset.jsDebugDomainEnd);
        const peerMidpointX = (((peerBucketStart * 1000) + 2500 - domainStart) / (domainEnd - domainStart)) * 600;
        const charts = {};
        for (const key of ['latency', 'count', 'bandwidth']) {
          const chart = document.querySelector(`[data-js-debug-chart="${key}"]`);
          const ranges = [...chart.querySelectorAll('[data-js-debug-no-data-range]')].map(rect => ({
            start: Number(rect.getAttribute('x')),
            end: Number(rect.getAttribute('x')) + Number(rect.getAttribute('width')),
            fill: getComputedStyle(rect).fill,
          }));
          charts[key] = {
            ranges,
            peerCovered: ranges.some(range => range.start <= peerMidpointX && range.end >= peerMidpointX),
            peerLines: [...chart.querySelectorAll('polyline[data-js-debug-client-series="other-clients-average"]')].map(line => ({
              color: getComputedStyle(line).stroke,
              points: line.getAttribute('points'),
            })),
          };
        }
        const colorProbe = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        colorProbe.setAttribute('class', 'js-debug-line js-debug-line--systemCpu');
        document.querySelector('[data-js-debug-graph]').append(colorProbe);
        const systemAverageColor = getComputedStyle(colorProbe).stroke;
        colorProbe.remove();
        return {
          peerMidpointX,
          systemAverageColor,
          charts,
        };
        """
    )
    assert 0 < metrics["peerMidpointX"] < 600, metrics
    for key, chart in metrics["charts"].items():
        assert chart["peerCovered"] is False, (key, metrics)
        assert chart["ranges"], (key, metrics)
        assert all(item["fill"] == "rgba(220, 38, 38, 0.12)" for item in chart["ranges"]), (key, metrics)
        assert chart["peerLines"], (key, metrics)
        assert all(item["color"] == metrics["systemAverageColor"] and item["points"] for item in chart["peerLines"]), (key, metrics)


def test_debug_graph_chart_close_restore_persists_preferences(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof debugGraphApplyServerHistory === 'function'
              && typeof setDebugGraphRange === 'function'
              && document.querySelector('.js-debug-panel [data-js-debug-graph]') !== null;
            """
        )
    )
    metrics = browser.execute_script(
        """
        const preferencesKey = 'yolomux.stats.ui_preferences.v1';
        localStorage.removeItem(preferencesKey);
        stopJsDebugStatsPolling();
        clearJsDebugGraphData();
        const now = Date.now();
        debugGraphApplyServerHistory({
          sequence: 401,
          records: [{
            start: Math.floor(now / 1000), duration: 1, sequence: 401,
            api_count: 1, cpu_total_percent: 5, cpu_count: 1,
            system_cpu_total_percent: 20, system_cpu_count: 1,
          }],
        });
        renderDebugPanels({force: true});
        let panel = document.querySelector('.js-debug-panel');
        const paint = element => {
          const style = getComputedStyle(element);
          return {color: style.color, background: style.backgroundColor};
        };
        const activeSubtabPaint = paint(panel?.querySelector('.js-debug-subtab.active'));
        const resolutionLabel = panel?.querySelector('[data-js-debug-resolution]')?.textContent.trim();
        const cpuClose = panel?.querySelector('[data-js-debug-chart-close="cpu"]');
        cpuClose?.focus({focusVisible: true});
        const closeFocusPaint = paint(cpuClose);
        const closeUsesSharedParent = cpuClose?.classList.contains('control-active-hover') === true;
        cpuClose?.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, cancelable: true}));
        const closed = !panel?.querySelector('[data-js-debug-chart="cpu"]');
        const restore = panel?.querySelector('[data-js-debug-chart-restore="cpu"]');
        restore?.focus({focusVisible: true});
        const restoreFocusPaint = paint(restore);
        const restoreUsesSharedParent = restore?.classList.contains('control-active-hover') === true;
        restore?.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, cancelable: true}));
        const restored = Boolean(panel?.querySelector('[data-js-debug-chart="cpu"]'));
        panel?.querySelector('[data-js-debug-subtab="events"]')?.click();
        setDebugGraphRange(14400);
        panel?.querySelector('[data-js-debug-chart-close="gpuMemory"]')?.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, cancelable: true}));
        return {
          closed,
          restoreVisible: Boolean(restore),
          restored,
          activeSubtabPaint,
          resolutionLabel,
          closeFocusPaint,
          restoreFocusPaint,
          closeUsesSharedParent,
          restoreUsesSharedParent,
          saved: JSON.parse(localStorage.getItem(preferencesKey) || '{}'),
        };
        """
    )
    assert metrics["closed"] is True, metrics
    assert metrics["restoreVisible"] is True, metrics
    assert metrics["restored"] is True, metrics
    assert metrics["resolutionLabel"].startswith("Resolution: "), metrics
    assert metrics["closeFocusPaint"] == metrics["activeSubtabPaint"], metrics
    assert metrics["restoreFocusPaint"] == metrics["activeSubtabPaint"], metrics
    assert metrics["closeUsesSharedParent"] is True, metrics
    assert metrics["restoreUsesSharedParent"] is True, metrics
    assert metrics["saved"] == {
        "subTab": "events",
        "rangeSeconds": 14400,
        "resolutionOverrideSeconds": 0,
        "chartLayout": 0,
        "hiddenCharts": ["gpuMemory", "gpuUtil", "memory"],
        "visibleCharts": ["cpu"],
    }, metrics

    browser.refresh()
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const panel = document.querySelector('.js-debug-panel');
            return panel?.querySelector('[data-js-debug-subview="events"]')?.hidden === false
              && panel?.querySelector('[data-js-debug-subview="graph"]')?.hidden === true
              && Number(document.querySelector('[data-js-debug-resolution]')?.dataset.jsDebugResolutionSeconds || 0) >= 60
              && document.querySelector('[data-js-debug-range-label]')?.textContent.trim() === '4h'
              && document.querySelector('[data-js-debug-chart-restore="gpuMemory"]') !== null;
            """
        )
    )


def test_debug_graph_24_hour_range_change_avoids_multi_second_browser_task(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof debugGraphApplyServerHistory === 'function'
              && typeof setDebugGraphRange === 'function'
              && document.querySelector('[data-js-debug-graph]') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        stopJsDebugStatsPolling();
        clearJsDebugGraphData();
        const now = Math.ceil(Date.now() / 60_000) * 60_000;
        const gapStart = now - (6 * 60 * 60 * 1000);
        const gapEnd = gapStart + (5 * 60 * 1000);
        const disconnectedStart = gapStart + (2 * 60 * 1000);
        const records = [];
        const appendTier = (startMs, count, durationSeconds) => {
          for (let index = 0; index < count; index += 1) {
            const bucketStartMs = startMs + (index * durationSeconds * 1000);
            const hasClientData = bucketStartMs < gapStart || bucketStartMs >= gapEnd;
            const record = {
              start: bucketStartMs / 1000,
              duration: durationSeconds,
              sequence: records.length + 1,
              cpu_total_percent: 12,
              cpu_count: 1,
              system_cpu_total_percent: 24,
              system_cpu_count: 1,
            };
            if (hasClientData) Object.assign(record, {api_count: 2, sse_count: 1, latency_total_ms: 25, latency_count: 1, bandwidth_bytes: 2048});
            if (bucketStartMs === disconnectedStart) record.disconnected_ms = durationSeconds * 1000;
            records.push(record);
          }
        };
        appendTier(now - (24 * 60 * 60 * 1000), 1320, 60);
        appendTier(now - (2 * 60 * 60 * 1000), 360, 10);
        appendTier(now - (60 * 60 * 1000), 3600, 1);
        setDebugGraphRange(15 * 60, {render: false});
        debugGraphApplyServerHistory({sequence: records.length, records});
        setJsDebugHistoryReadiness('ready', {
          loadedStartSeconds: (now - (24 * 60 * 60 * 1000)) / 1000,
          loadedEndSeconds: now / 1000,
          resolutionSeconds: 1,
          coverageIntervals: [{
            startSeconds: 0,
            endSeconds: Infinity,
            resolutionSeconds: 1,
          }],
        });
        renderDebugPanels({force: true});
        const longTasks = [];
        const observer = typeof PerformanceObserver === 'function' && PerformanceObserver.supportedEntryTypes?.includes('longtask')
          ? new PerformanceObserver(list => longTasks.push(...list.getEntries().map(entry => entry.duration)))
          : null;
        observer?.observe({entryTypes: ['longtask']});
        requestAnimationFrame(() => {
          const started = performance.now();
          setDebugGraphRange(24 * 60 * 60);
          const syncElapsedMs = performance.now() - started;
          requestAnimationFrame(() => requestAnimationFrame(() => {
            observer?.disconnect();
            const noDataCounts = Object.fromEntries(['latency', 'count', 'bandwidth'].map(key => [
              key,
              document.querySelectorAll(`[data-js-debug-chart="${key}"] [data-js-debug-no-data-range]`).length,
            ]));
            done({syncElapsedMs, maxLongTaskMs: Math.max(0, ...longTasks), noDataCounts, records: records.length});
          }));
        });
        """
    )
    assert metrics["records"] == 5280, metrics
    assert all(count >= 2 for count in metrics["noDataCounts"].values()), metrics
    assert metrics["syncElapsedMs"] < 1000, metrics
    assert metrics["maxLongTaskMs"] < 1000, metrics


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
        setJsDebugHistoryReadiness('ready', {
          loadedStartSeconds: 1,
          loadedEndSeconds: Infinity,
          resolutionSeconds: 1,
          coverageIntervals: [{startSeconds: 0, endSeconds: Infinity, resolutionSeconds: 1}],
        });
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
        const hoverTooltip = first.closest('[data-js-debug-chart]')?.querySelector('[data-js-debug-hover-tooltip]');
        const hoverTooltipRect = hoverTooltip?.getBoundingClientRect();
        const firstChartRect = first.closest('[data-js-debug-chart]')?.getBoundingClientRect();
        const hoverTooltipMetrics = {
          hidden: hoverTooltip?.hidden,
          max: hoverTooltip?.querySelector('[data-js-debug-hover-max]')?.textContent || '',
          time: hoverTooltip?.querySelector('[data-js-debug-hover-time]')?.textContent || '',
          rightOfCursor: hoverTooltipRect && firstChartRect ? hoverTooltipRect.left >= startX + 3 || hoverTooltipRect.right <= firstChartRect.right - 4 : false,
          aboveCursor: hoverTooltipRect ? hoverTooltipRect.bottom <= y - 3 : false,
          contained: hoverTooltipRect && firstChartRect ? hoverTooltipRect.left >= firstChartRect.left + 3 && hoverTooltipRect.right <= firstChartRect.right - 3 && hoverTooltipRect.top >= firstChartRect.top + 3 && hoverTooltipRect.bottom <= firstChartRect.bottom - 3 : false,
        };

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
        const label = document.querySelector('[data-js-debug-range-label]');
        const sliderAfterZoom = resetControl?.querySelector('[data-js-debug-range-slider]');
        const resetRect = reset?.getBoundingClientRect();
        const resetControlRect = resetControl?.getBoundingClientRect();
        const labelRect = label?.getBoundingClientRect();
        const sliderAfterZoomRect = sliderAfterZoom?.getBoundingClientRect();
        const resetRightGap = resetRect && resetControlRect ? resetControlRect.right - resetRect.right : NaN;
        const sliderBeforeLabelGap = labelRect && sliderAfterZoomRect ? labelRect.left - sliderAfterZoomRect.right : NaN;
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
          hoverTooltip: hoverTooltipMetrics,
          selecting,
          selectionOpacity,
          selectionWidth,
          zoomed,
          zoomSeconds: (zoomEnd - zoomStart) / 1000,
          resetText: reset?.textContent || '',
          resetRightGap,
          sliderBeforeLabelGap,
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
    assert metrics["hoverTooltip"]["hidden"] is False, metrics
    assert metrics["hoverTooltip"]["max"] == "0.0%", metrics
    assert re.search(r"[12]\d{3}.*\d{1,2}:\d{2}:\d{2}", metrics["hoverTooltip"]["time"]), metrics
    assert metrics["hoverTooltip"]["rightOfCursor"] is True, metrics
    assert metrics["hoverTooltip"]["aboveCursor"] is True, metrics
    assert metrics["hoverTooltip"]["contained"] is True, metrics
    assert metrics["selecting"] is True, metrics
    assert float(metrics["selectionOpacity"]) > 0.0, metrics
    assert 235 <= metrics["selectionWidth"] <= 245, metrics
    assert metrics["zoomed"] is True, metrics
    assert 118 <= metrics["zoomSeconds"] <= 122, metrics
    assert metrics["resetText"] == "Reset", metrics
    assert 0 <= metrics["resetRightGap"] <= 1.5, metrics
    assert metrics["sliderBeforeLabelGap"] >= 4, metrics
    assert metrics["resetZoomed"] is False, metrics

    slider = WebDriverWait(browser, 5).until(lambda driver: driver.find_element("css selector", "[data-js-debug-range-slider]"))
    slider_rect = browser.execute_script(
        """
        const rect = arguments[0].getBoundingClientRect();
        return {width: rect.width, height: rect.height};
        """,
        slider,
    )
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


def test_debug_graph_short_range_refetches_token_rates_after_compact_token_history(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof setDebugGraphRange === 'function' && typeof debugGraphApplyServerAgentTokenHistory === 'function' && document.querySelector('[data-js-debug-graph]') !== null;"
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        (async () => {
          stopJsDebugStatsPolling();
          clearJsDebugGraphData();
          resetJsDebugHistoryReadiness();
          jsDebugStatsPollState.firstSampleReceived = false;
          jsDebugStatsPollState.inFlight = false;
          jsDebugStatsPollState.pending = false;
          jsDebugStatsPollState.pendingForceGraphRefresh = false;
          const nowSeconds = Math.floor(Date.now() / 1000);
          jsDebugGraphRangeSeconds = 8 * 60 * 60;
          jsDebugStatsAgentTokenResolutionSeconds = 120;
          debugGraphApplyServerAgentTokenHistory({
            sequence: 9,
            resolution_seconds: 120,
            records: [{
              start: nowSeconds - (60 * 60),
              duration: 120,
              sequence: 9,
              tokens_per_agent_total: 100,
              agent_token_samples: 1,
              agent_token_rates: [{key: 'old|0|codex', label: 'old:0:codex', total: 100, samples: 1, tokens: 100, seconds: 60}],
            }],
          });
          setJsDebugHistoryReadiness('ready', {
            loadedStartSeconds: nowSeconds - (8 * 60 * 60),
            loadedEndSeconds: nowSeconds,
            resolutionSeconds: 5,
            coverageIntervals: [{startSeconds: nowSeconds - (8 * 60 * 60), endSeconds: nowSeconds, resolutionSeconds: 5}],
          });
          const originalFetch = window.fetch;
          const requests = [];
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            if (url.pathname !== '/api/stats-sample') return originalFetch(input, options);
            requests.push(url.toString());
            const requestedStart = Number(url.searchParams.get('history_start'));
            return Promise.resolve(new Response(JSON.stringify({history: {
              sequence: 50,
              latest_sequence: 50,
              agent_token_schema_version: 2,
              records: [{
                start: nowSeconds - 60,
                duration: 60,
                sequence: 50,
                tokens_per_agent_total: 120,
                agent_token_samples: 1,
                agent_token_rates: [{key: '8002|1|codex', label: '8002:1:codex', total: 120, samples: 1, tokens: 120, seconds: 60}],
              }],
              coverage: {
                mode: 'live', requested_start: requestedStart, requested_end: 0,
                covered_start: requestedStart, covered_end: nowSeconds,
                resolution_seconds: Number(url.searchParams.get('history_resolution')),
                complete: true, has_more_older: false, next_older_end: 0,
              },
            }}), {status: 200, headers: {'Content-Type': 'application/json'}}));
          };
          try {
            setDebugGraphRange(2 * 60 * 60);
            await window.__yolomuxTestWaitFor(
              () => requests.length >= 1 && !jsDebugStatsPollState.inFlight,
              {timeoutMs: 3000, intervalMs: 20, description: 'short-range token history refetch'},
            );
            renderDebugPanels({force: true});
            const chart = document.querySelector('[data-js-debug-chart="agentTokens"]');
            const url = new URL(requests[0] || location.href);
            return {
              requestCount: requests.length,
              since: url.searchParams.get('since'),
              historyStart: Number(url.searchParams.get('history_start')),
              tokenResolution: url.searchParams.get('token_resolution'),
              separateTokenCount: jsDebugGraphAgentTokenBuckets.size,
              legendLabels: [...(chart?.querySelectorAll('[data-js-debug-legend] span') || [])].map(node => node.textContent),
              barCount: chart?.querySelectorAll('[data-js-debug-bar-series="agentToken:8002|1|codex"]').length || 0,
            };
          } finally {
            window.fetch = originalFetch;
            stopJsDebugStatsPolling();
          }
        })().then(done).catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["requestCount"] >= 1, metrics
    assert metrics["since"] == "0", metrics
    assert metrics["historyStart"] > 0, metrics
    assert metrics["tokenResolution"] is None, metrics
    assert metrics["separateTokenCount"] == 0, metrics
    assert "8002:1:codex" in metrics["legendLabels"], metrics
    assert metrics["barCount"] == 1, metrics


def test_debug_graph_compact_tokens_cover_the_full_domain_during_an_older_history_fetch(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof setDebugGraphRange === 'function' && document.querySelector('[data-js-debug-graph]') !== null;"
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        (async () => {
          stopJsDebugStatsPolling();
          clearJsDebugGraphData();
          resetJsDebugHistoryReadiness();
          jsDebugStatsPollState.firstSampleReceived = true;
          jsDebugStatsPollState.inFlight = false;
          jsDebugStatsPollState.pending = false;
          jsDebugStatsPollState.pendingForceGraphRefresh = false;
          const nowSeconds = Math.floor(Date.now() / 1000);
          const twoHoursAgo = nowSeconds - (2 * 60 * 60);
          jsDebugGraphRangeSeconds = 2 * 60 * 60;
          setJsDebugHistoryReadiness('ready', {
            loadedStartSeconds: twoHoursAgo,
            loadedEndSeconds: nowSeconds,
            resolutionSeconds: 5,
            coverageIntervals: [{startSeconds: twoHoursAgo, endSeconds: nowSeconds, resolutionSeconds: 5}],
          });
          const originalFetch = window.fetch;
          const requests = [];
          const waitForRequests = expectedCount => window.__yolomuxTestWaitFor(
            () => requests.length >= expectedCount && !jsDebugStatsPollState.inFlight,
            {timeoutMs: 3000, intervalMs: 20, description: `compact-token history request ${expectedCount}`},
          );
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            if (url.pathname !== '/api/stats-sample') return originalFetch(input, options);
            requests.push(url);
            const tokenStart = Number(url.searchParams.get('token_history_start'));
            const normalEnd = Number(url.searchParams.get('history_end'));
            const tokenResolution = Number(url.searchParams.get('token_resolution'));
            return Promise.resolve(new Response(JSON.stringify({history: {
              sequence: 50,
              latest_sequence: 50,
              agent_token_schema_version: 2,
              records: [{
                start: tokenStart,
                duration: 5,
                sequence: 50,
                active_agent_total: 1,
                agent_activity_samples: 1,
              }],
              coverage: {
                mode: 'older', requested_start: tokenStart, requested_end: normalEnd,
                covered_start: tokenStart, covered_end: normalEnd,
                resolution_seconds: 5, complete: true, has_more_older: false, next_older_end: 0,
              },
              agent_token_history: {
                sequence: 50,
                latest_sequence: 50,
                resolution_seconds: tokenResolution,
                snapshot: true,
                coverage: {
                  mode: 'live', requested_start: tokenStart, requested_end: 0,
                  covered_start: tokenStart, covered_end: nowSeconds,
                  resolution_seconds: 120, complete: true, has_more_older: false, next_older_end: 0,
                },
                records: [
                  {start: tokenStart, duration: tokenResolution, sequence: 40, tokens_per_agent_total: 100, agent_token_samples: 1, agent_token_rates: [{key: 'old|0|codex', label: 'old:0:codex', total: 100, samples: 1, tokens: 100, seconds: 60}]},
                  {start: nowSeconds - tokenResolution, duration: tokenResolution, sequence: 50, tokens_per_agent_total: 200, agent_token_samples: 1, agent_token_rates: [{key: 'recent|0|codex', label: 'recent:0:codex', total: 200, samples: 1, tokens: 200, seconds: 60}]},
                ],
              },
            }}), {status: 200, headers: {'Content-Type': 'application/json'}}));
          };
          try {
            setDebugGraphRange(4 * 60 * 60);
            await waitForRequests(1);
            setDebugGraphRange(8 * 60 * 60);
            await waitForRequests(2);
            setDebugGraphRange(16 * 60 * 60);
            await waitForRequests(3);
            renderDebugPanels({force: true});
            const chart = document.querySelector('[data-js-debug-chart="agentTokens"]');
            return {
              requestCount: requests.length,
              requests: requests.map(request => ({
                normalHistoryEnd: Number(request.searchParams.get('history_end')),
                tokenHistoryStart: Number(request.searchParams.get('token_history_start')),
                tokenHistoryEnd: request.searchParams.get('token_history_end'),
                tokenResolution: Number(request.searchParams.get('token_resolution')),
              })),
              tokenStarts: [...jsDebugGraphAgentTokenBuckets.values()].map(bucket => Math.floor(bucket.startMs / 1000)).sort((left, right) => left - right),
              oldBars: chart?.querySelectorAll('[data-js-debug-bar-series="agentToken:old|0|codex"]').length || 0,
              recentBars: chart?.querySelectorAll('[data-js-debug-bar-series="agentToken:recent|0|codex"]').length || 0,
            };
          } finally {
            window.fetch = originalFetch;
            stopJsDebugStatsPolling();
          }
        })().then(done).catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["requestCount"] >= 3, metrics
    assert [request["tokenResolution"] for request in metrics["requests"][:3]] == [120, 120, 300], metrics
    assert all(request["normalHistoryEnd"] > request["tokenHistoryStart"] for request in metrics["requests"][:3]), metrics
    assert all(request["tokenHistoryEnd"] == "0" for request in metrics["requests"][:3]), metrics
    assert metrics["requests"][0]["tokenHistoryStart"] > metrics["requests"][1]["tokenHistoryStart"] > metrics["requests"][2]["tokenHistoryStart"], metrics
    assert len(metrics["tokenStarts"]) == 2, metrics
    assert metrics["tokenStarts"][1] - metrics["tokenStarts"][0] > 3 * 60 * 60, metrics
    assert metrics["oldBars"] == 1 and metrics["recentBars"] == 1, metrics


def test_debug_graph_server_restart_refetches_complete_history_without_waiting_for_the_poll_interval(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof pollJsDebugStatsSample === 'function' && document.querySelector('[data-js-debug-graph]') !== null;"
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        (async () => {
          stopJsDebugStatsPolling();
          clearJsDebugGraphData();
          resetJsDebugHistoryReadiness();
          jsDebugStatsPollState.firstSampleReceived = true;
          jsDebugStatsPollState.inFlight = false;
          jsDebugStatsPollState.pending = false;
          jsDebugStatsPollState.pendingForceGraphRefresh = false;
          jsDebugStatsServerSequence = 0;
          jsDebugStatsServerPid = null;
          jsDebugStatsServerStartedAt = null;
          const nowSeconds = Math.floor(Date.now() / 1000);
          const originalFetch = window.fetch;
          const requests = [];
          const response = payload => Promise.resolve(new Response(JSON.stringify(payload), {
            status: 200,
            headers: {'Content-Type': 'application/json'},
          }));
          const history = (url, records, sequence) => ({
            sequence,
            latest_sequence: sequence,
            records,
            coverage: {
              mode: 'live',
              requested_start: Number(url.searchParams.get('history_start')),
              requested_end: 0,
              covered_start: Number(url.searchParams.get('history_start')),
              covered_end: nowSeconds,
              resolution_seconds: Number(url.searchParams.get('history_resolution')),
              complete: true,
              has_more_older: false,
              next_older_end: 0,
            },
          });
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            if (url.pathname !== '/api/stats-sample') return originalFetch(input, options);
            requests.push(url);
            if (requests.length === 1) {
              return response({
                pid: 1001,
                started_at: nowSeconds - 120,
                history: history(url, [{start: nowSeconds - 600, duration: 5, sequence: 100, system_cpu_total_percent: 20, system_cpu_count: 1}], 100),
              });
            }
            if (requests.length === 2) {
              return response({
                pid: 2002,
                started_at: nowSeconds,
                history: history(url, [], 101),
              });
            }
            return response({
              pid: 2002,
              started_at: nowSeconds,
              history: history(url, [
                {start: nowSeconds - 600, duration: 5, sequence: 150, system_cpu_total_percent: 20, system_cpu_count: 1},
                {start: nowSeconds - 60, duration: 5, sequence: 151, system_cpu_total_percent: 40, system_cpu_count: 1},
              ], 151),
            });
          };
          try {
            await pollJsDebugStatsSample();
            await pollJsDebugStatsSample();
            await window.__yolomuxTestWaitFor(
              () => requests.length >= 3 && !jsDebugStatsPollState.inFlight,
              {timeoutMs: 3000, intervalMs: 20, description: 'YO!stats server-restart history refetch'},
            );
            renderDebugPanels({force: true});
            const xValues = [...document.querySelectorAll('[data-js-debug-series="systemCpu"]')]
              .flatMap(line => String(line.getAttribute('points') || '')
                .split(/\\s+/)
                .filter(Boolean)
                .map(point => Number(point.split(',')[0]))
                .filter(Number.isFinite));
            return {
              requestCount: requests.length,
              restartRefetchSince: requests[2]?.searchParams.get('since'),
              graphBusy: document.querySelector('[data-js-debug-graph]')?.getAttribute('aria-busy'),
              xValues,
            };
          } finally {
            window.fetch = originalFetch;
            stopJsDebugStatsPolling();
          }
        })().then(done).catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["requestCount"] >= 3, metrics
    assert metrics["restartRefetchSince"] == "0", metrics
    assert metrics["graphBusy"] == "false", metrics
    assert any(100 < value < 300 for value in metrics["xValues"]), metrics
    assert any(value > 500 for value in metrics["xValues"]), metrics


def test_debug_graph_wider_range_fetches_and_paints_older_history_after_inflight_poll(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof pollJsDebugStatsSample === 'function'
              && typeof setDebugGraphRange === 'function'
              && document.querySelector('[data-js-debug-graph]') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        (async () => {
          stopJsDebugStatsPolling();
          clearJsDebugGraphData();
          jsDebugStatsPollState.firstSampleReceived = false;
          jsDebugStatsPollState.inFlight = false;
          jsDebugStatsPollState.pending = false;
          jsDebugStatsServerSequence = 0;
          resetJsDebugHistoryReadiness();
          jsDebugGraphRangeSeconds = 15 * 60;
          const originalFetch = window.fetch;
          const requests = [];
          let releaseIncremental;
          const response = payload => Promise.resolve(new Response(JSON.stringify(payload), {
            status: 200,
            headers: {'Content-Type': 'application/json'},
          }));
          const coverageFor = (url, overrides = {}) => {
            const requestedStart = Number(url.searchParams.get('history_start'));
            const requestedEnd = Number(url.searchParams.get('history_end'));
            return {
              mode: requestedEnd === 0 ? 'live' : 'older',
              requested_start: requestedStart,
              requested_end: requestedEnd,
              covered_start: requestedStart,
              covered_end: requestedEnd === 0 ? Math.floor(Date.now() / 1000) : requestedEnd,
              resolution_seconds: Number(url.searchParams.get('history_resolution')),
              complete: true,
              has_more_older: false,
              next_older_end: 0,
              ...overrides,
            };
          };
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            if (url.pathname !== '/api/stats-sample') return originalFetch(input, options);
            requests.push(url.toString());
            const nowSeconds = Math.floor(Date.now() / 1000);
            if (requests.length === 1) {
              return response({history: {sequence: 100, records: [{
                start: nowSeconds - 60,
                duration: 1,
                sequence: 100,
                system_cpu_total_percent: 20,
                system_cpu_count: 1,
              }], coverage: coverageFor(url)}});
            }
            if (requests.length === 2) {
              return new Promise(resolve => {
                releaseIncremental = () => response({history: {sequence: 101, records: []}}).then(resolve);
              });
            }
            return response({history: {sequence: 102, records: [{
              start: nowSeconds - (25 * 60),
              duration: 1,
              sequence: 101,
              system_cpu_total_percent: 40,
              system_cpu_count: 1,
            }], coverage: coverageFor(url)}});
          };
          try {
            await pollJsDebugStatsSample();
            stopJsDebugStatsPolling();
            renderDebugPanels({force: true});
            const retainedChartCount = document.querySelectorAll('[data-js-debug-chart]').length;
            const narrowPoll = pollJsDebugStatsSample();
            await Promise.resolve();
            setDebugGraphRange(30 * 60);
            const loadingGraph = document.querySelector('[data-js-debug-graph]');
            const beforeDelay = {
              chartCount: loadingGraph.querySelectorAll('[data-js-debug-chart]').length,
              busy: loadingGraph.getAttribute('aria-busy'),
              phase: loadingGraph.dataset.jsDebugHistoryState,
              overlayHidden: loadingGraph.querySelector('[data-js-debug-history-overlay]')?.hidden,
            };
            await new Promise(resolve => setTimeout(resolve, 160));
            const delayedOverlay = loadingGraph.querySelector('[data-js-debug-history-overlay]');
            const afterDelay = {
              overlayHidden: delayedOverlay?.hidden,
              overlayText: delayedOverlay?.textContent?.trim() || '',
            };
            releaseIncremental();
            await narrowPoll;
            await window.__yolomuxTestWaitFor(
              () => requests.length >= 3 && !jsDebugStatsPollState.inFlight && jsDebugHistoryReadiness.phase === 'ready',
              {timeoutMs: 3000, intervalMs: 20, description: 'wider YO!stats history readiness'}
            );
            const wideUrl = new URL(requests[2]);
            const lines = Array.from(document.querySelectorAll('[data-js-debug-series="systemCpu"]'));
            const xValues = lines.flatMap(line => String(line.getAttribute('points') || '')
              .split(/\\s+/)
              .filter(Boolean)
              .map(point => Number(point.split(',')[0]))
              .filter(Number.isFinite));
            const grid = document.querySelector('[data-js-debug-chart-grid]');
            const finalGraph = document.querySelector('[data-js-debug-graph]');
            const narrowUrl = new URL(requests[0]);
            return {
              requestCount: requests.length,
              since: wideUrl.searchParams.get('since'),
              historyStart: Number(wideUrl.searchParams.get('history_start')),
              historyEnd: Number(wideUrl.searchParams.get('history_end')),
              expectedHistoryEnd: Number(narrowUrl.searchParams.get('history_start')),
              historyResolution: Number(wideUrl.searchParams.get('history_resolution')),
              historyMaxPoints: Number(wideUrl.searchParams.get('history_max_points')),
              rangeSeconds: (Number(grid?.dataset.jsDebugDomainEnd) - Number(grid?.dataset.jsDebugDomainStart)) / 1000,
              minX: xValues.length ? Math.min(...xValues) : null,
              maxX: xValues.length ? Math.max(...xValues) : null,
              retainedChartCount,
              beforeDelay,
              afterDelay,
              finalBusy: finalGraph?.getAttribute('aria-busy'),
              finalPhase: finalGraph?.dataset.jsDebugHistoryState,
              finalOverlayHidden: finalGraph?.querySelector('[data-js-debug-history-overlay]')?.hidden,
            };
          } finally {
            window.fetch = originalFetch;
            stopJsDebugStatsPolling();
          }
        })().then(done).catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["requestCount"] == 3, metrics
    assert metrics["since"] == "0", metrics
    assert metrics["historyEnd"] == metrics["expectedHistoryEnd"], metrics
    assert metrics["historyResolution"] == 1, metrics
    assert metrics["historyMaxPoints"] == 6000, metrics
    assert 1790 <= metrics["rangeSeconds"] <= 1810, metrics
    assert metrics["minX"] is not None and metrics["minX"] < 200, metrics
    assert metrics["maxX"] is not None and metrics["maxX"] > 500, metrics
    assert metrics["retainedChartCount"] > 0, metrics
    assert metrics["beforeDelay"] == {
        "chartCount": metrics["retainedChartCount"],
        "busy": "true",
        "phase": "loading-older",
        "overlayHidden": True,
    }, metrics
    assert metrics["afterDelay"]["overlayHidden"] is False, metrics
    assert "Loading older data" in metrics["afterDelay"]["overlayText"], metrics
    assert metrics["finalBusy"] == "false", metrics
    assert metrics["finalPhase"] == "ready", metrics
    assert metrics["finalOverlayHidden"] is True, metrics


def test_debug_graph_history_error_retains_chart_and_retry_clears_overlay(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof pollJsDebugStatsSample === 'function'
              && typeof retryJsDebugHistory === 'function'
              && document.querySelector('[data-js-debug-graph]') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        (async () => {
          stopJsDebugStatsPolling();
          clearJsDebugGraphData();
          resetJsDebugHistoryReadiness();
          const nowSeconds = Math.floor(Date.now() / 1000);
          debugGraphApplyServerHistory({sequence: 1, records: [{
            start: nowSeconds - 60,
            duration: 1,
            sequence: 1,
            system_cpu_total_percent: 20,
            system_cpu_count: 1,
          }]});
          renderDebugPanels({force: true});
          const retainedChartCount = document.querySelectorAll('[data-js-debug-chart]').length;
          const originalFetch = window.fetch;
          let requestCount = 0;
          let rejectInitial;
          const response = payload => Promise.resolve(new Response(JSON.stringify(payload), {
            status: 200,
            headers: {'Content-Type': 'application/json'},
          }));
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            if (url.pathname !== '/api/stats-sample') return originalFetch(input, options);
            requestCount += 1;
            if (requestCount === 1) {
              return new Promise((_resolve, reject) => { rejectInitial = reject; });
            }
            const requestedStart = Number(url.searchParams.get('history_start'));
            const requestedEnd = Number(url.searchParams.get('history_end'));
            return response({history: {
              sequence: 1,
              latest_sequence: 1,
              records: [],
              coverage: {
                mode: requestedEnd === 0 ? 'live' : 'older',
                requested_start: requestedStart,
                requested_end: requestedEnd,
                covered_start: 0,
                covered_end: 0,
                resolution_seconds: Number(url.searchParams.get('history_resolution')),
                complete: false,
                has_more_older: false,
                next_older_end: 0,
              },
            }});
          };
              try {
                const initialPoll = pollJsDebugStatsSample();
                const requestDeadline = performance.now() + 3000;
                while (typeof rejectInitial !== 'function' && performance.now() < requestDeadline) {
                  await new Promise(resolve => setTimeout(resolve, 20));
                }
                if (typeof rejectInitial !== 'function') throw new Error('mocked stats request did not start');
                const loadingGraph = document.querySelector('[data-js-debug-graph]');
            const loading = {
              busy: loadingGraph?.getAttribute('aria-busy'),
              phase: loadingGraph?.dataset.jsDebugHistoryState,
              chartCount: loadingGraph?.querySelectorAll('[data-js-debug-chart]').length,
              overlayHidden: loadingGraph?.querySelector('[data-js-debug-history-overlay]')?.hidden,
                };
                rejectInitial(new Error('history unavailable'));
                await initialPoll;
                const errorDeadline = performance.now() + 3000;
                while (jsDebugHistoryReadiness.phase !== 'error' && performance.now() < errorDeadline) {
                  await new Promise(resolve => setTimeout(resolve, 20));
                }
                const errorGraph = document.querySelector('[data-js-debug-graph]');
            const error = {
              busy: errorGraph?.getAttribute('aria-busy'),
              phase: errorGraph?.dataset.jsDebugHistoryState,
              chartCount: errorGraph?.querySelectorAll('[data-js-debug-chart]').length,
              text: errorGraph?.querySelector('[data-js-debug-history-overlay]')?.textContent?.trim() || '',
              retry: Boolean(errorGraph?.querySelector('[data-js-debug-history-retry]')),
            };
            retryJsDebugHistory();
            await window.__yolomuxTestWaitFor(
              () => requestCount >= 2 && !jsDebugStatsPollState.inFlight && jsDebugHistoryReadiness.phase === 'ready',
              {timeoutMs: 3000, intervalMs: 20, description: 'YO!stats history retry readiness'}
            );
            const readyGraph = document.querySelector('[data-js-debug-graph]');
            return {
              retainedChartCount,
              requestCount,
              loading,
              failed: error,
              ready: {
                busy: readyGraph?.getAttribute('aria-busy'),
                phase: readyGraph?.dataset.jsDebugHistoryState,
                chartCount: readyGraph?.querySelectorAll('[data-js-debug-chart]').length,
                overlayHidden: readyGraph?.querySelector('[data-js-debug-history-overlay]')?.hidden,
                retry: Boolean(readyGraph?.querySelector('[data-js-debug-history-retry]')),
              },
            };
          } finally {
            window.fetch = originalFetch;
            stopJsDebugStatsPolling();
          }
        })().then(done).catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["retainedChartCount"] > 0, metrics
    assert metrics["loading"] == {
        "busy": "true",
        "phase": "loading-initial",
        "chartCount": metrics["retainedChartCount"],
        "overlayHidden": False,
    }, metrics
    assert metrics["failed"]["busy"] == "false", metrics
    assert metrics["failed"]["phase"] == "error", metrics
    assert metrics["failed"]["chartCount"] == metrics["retainedChartCount"], metrics
    assert metrics["failed"]["retry"] is True, metrics
    assert "history unavailable" in metrics["failed"]["text"], metrics
    assert metrics["requestCount"] == 2, metrics
    assert metrics["ready"] == {
        "busy": "false",
        "phase": "ready",
        "chartCount": metrics["retainedChartCount"],
        "overlayHidden": True,
        "retry": False,
    }, metrics


def test_debug_graph_history_upload_ack_does_not_leave_hidden_interval_blank(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof pollJsDebugStatsSample === 'function'
              && typeof flushJsDebugStatsHistory === 'function'
              && document.querySelector('[data-js-debug-graph]') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        (async () => {
          stopJsDebugStatsPolling();
          clearJsDebugGraphData();
          jsDebugStatsPollState.firstSampleReceived = false;
          jsDebugStatsPollState.inFlight = false;
          jsDebugStatsPollState.pending = false;
          jsDebugStatsServerSequence = 0;
          resetJsDebugHistoryReadiness();
          jsDebugGraphRangeSeconds = 15 * 60;
          const originalFetch = window.fetch;
          const requests = [];
          let getCount = 0;
          const nowSeconds = Math.floor(Date.now() / 1000);
          const response = payload => Promise.resolve(new Response(JSON.stringify(payload), {
            status: 200,
            headers: {'Content-Type': 'application/json'},
          }));
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            requests.push({url: url.toString(), method: String(options.method || 'GET')});
            if (url.pathname === '/api/stats-history') {
              return response({ok: true, history: {sequence: 200, records: []}});
            }
            if (url.pathname !== '/api/stats-sample') return originalFetch(input, options);
            getCount += 1;
            if (getCount === 1) {
              return response({history: {sequence: 100, records: [{
                start: nowSeconds - 600,
                duration: 1,
                sequence: 100,
                system_cpu_total_percent: 20,
                system_cpu_count: 1,
              }], coverage: {
                mode: 'live',
                requested_start: Number(url.searchParams.get('history_start')),
                requested_end: 0,
                covered_start: Number(url.searchParams.get('history_start')),
                covered_end: nowSeconds,
                resolution_seconds: Number(url.searchParams.get('history_resolution')),
                complete: true,
                has_more_older: false,
                next_older_end: 0,
              }}});
            }
            const since = Number(url.searchParams.get('since') || 0);
            const records = [{
              start: nowSeconds - 60,
              duration: 1,
              sequence: 201,
              system_cpu_total_percent: 30,
              system_cpu_count: 1,
            }];
            if (since <= 100) records.unshift({
              start: nowSeconds - 300,
              duration: 1,
              sequence: 150,
              system_cpu_total_percent: 25,
              system_cpu_count: 1,
            });
            return response({history: {sequence: 201, records}});
          };
          try {
            await pollJsDebugStatsSample();
            stopJsDebugStatsPolling();
            recordJsDebugEventForGraph({
              id: 999999,
              type: 'api',
              ts: new Date().toISOString(),
              durationMs: 1,
              requestBytes: 0,
              responseBytes: 0,
            });
            await flushJsDebugStatsHistory();
            await pollJsDebugStatsSample();
            stopJsDebugStatsPolling();
            renderDebugPanels({force: true});
            const sampleRequests = requests.filter(request => new URL(request.url).pathname === '/api/stats-sample');
            const catchUpUrl = new URL(sampleRequests[1].url);
            const xValues = Array.from(document.querySelectorAll('[data-js-debug-series="systemCpu"]'))
              .flatMap(line => String(line.getAttribute('points') || '')
                .split(/\\s+/)
                .filter(Boolean)
                .map(point => Number(point.split(',')[0]))
                .filter(Number.isFinite));
            return {
              since: catchUpUrl.searchParams.get('since'),
              requestCount: requests.length,
              xValues,
              hasMiddlePoint: xValues.some(value => value > 330 && value < 470),
            };
          } finally {
            window.fetch = originalFetch;
            stopJsDebugStatsPolling();
          }
        })().then(done).catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["since"] == "100", metrics
    assert metrics["requestCount"] == 3, metrics
    assert metrics["hasMiddlePoint"] is True, metrics


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


def test_working_agent_glyphs_show_static_symbol_and_opacity_pulse_in_tabs_windows_and_tabber(browser, tmp_path):
    page = tmp_path / "working-agent-visible-pulse.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(f"""
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
            {_working_agent_glyph_html("claude", "window-claude", subwindow=True)}
            <span class="tmux-window-name-text">0:claude</span>
          </span>
        </button>
        <div id="tabber-claude-row" class="file-tree-row tabber-row selected" data-tabber-type="window" style="--file-explorer-font-size: 18px;">
          <span class="file-tree-name">
            {_tabber_window_button_html("claude", "0:claude", _working_agent_glyph_html("claude", "tabber-claude", subwindow=True))}
          </span>
        </div>
        <div id="tabber-codex-row" class="file-tree-row tabber-row" data-tabber-type="window" style="--file-explorer-font-size: 18px;">
          <span class="file-tree-name">
            {_tabber_window_button_html("codex", "1:codex", _working_agent_glyph_html("codex", "tabber-codex", subwindow=True))}
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
    """),
    )
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
            if (arguments[0] !== '#dock-claude') dot.classList.add('agent-window-status-dot--subwindow-pulse');
            const before = dot ? getComputedStyle(dot, '::before') : null;
            return {
              symAnim: getComputedStyle(sym).animationName,
              symOpacity: getComputedStyle(sym).opacity,
              dotPresent: !!dot,
              dotWorkingTone: dot ? dot.classList.contains('status-indicator--working') : false,
                  dotAnim: dot ? getComputedStyle(dot).animationName : null,
                  dotBoxShadow: dot ? getComputedStyle(dot).boxShadow : null,
                  beforeAnim: before ? before.animationName : null,
                  beforeFilter: before ? before.filter : null,
            };
            """,
            selector,
        )
        results[label] = info
        # On every surface the agent symbol is STATIC (no pulse) ...
        assert info["symAnim"] == "none", results
        assert float(info["symOpacity"]) == 1, results
        # ... and a separate status marker sits beside it. Session/Tab balls and sub-window play
        # glyphs use the same opacity-only pulse, without a glow filter or shadow.
        assert info["dotPresent"] is True, results
        assert info["dotWorkingTone"] is True, results
        if not reduced:
            if label == "dock-tab Claude":
                assert info["dotAnim"] == "agent-status-opacity-pulse", results
                assert info["dotBoxShadow"] in ("", "none"), results
            else:
                assert info["dotAnim"] == "agent-status-opacity-pulse", results
                assert info["dotBoxShadow"] in ("", "none"), results
                assert info["beforeAnim"] == "none", results
                assert info["beforeFilter"] == "none", results


def test_working_status_ball_is_filled_green_with_a_border_and_no_glow(browser, tmp_path):
    page = tmp_path / "working-agent-glow-pixels.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(f"""
      <section class="glow-pixel-fixture">
        <div id="tabber-glow-row" class="file-tree-row tabber-row" data-tabber-type="session" style="--file-explorer-font-size: 18px;">
          <span class="file-tree-name">
            <span class="tmux-pane-tab-token tmux-pane-tab-token-action tabber-session-tab session-popover-host active" data-tabber-session-chrome="shared">
              <span class="pane-tab-core">
                <span class="session-agent-activity-marker">{_working_agent_glyph_html("codex", "tabber-glow")}</span>
                <span class="session-button-prefix">8001</span>
              </span>
            </span>
          </span>
        </div>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 80px; background: #101820; color: #e8eef8; font: 18px sans-serif; }
      .glow-pixel-fixture { display: grid; justify-items: start; gap: 24px; }
      #tabber-glow-row { width: 320px; padding: 14px 18px; background: #101820; overflow: visible; }
      #tabber-glow-row .file-tree-name,
      #tabber-glow-row .tabber-window-token,
      #tabber-glow-row .agent-window-activity { overflow: visible; }
    """),
    )
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
              border: style.borderTopColor,
              opacity: style.opacity,
              reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
        };
        """
    )
    assert metrics["animationName"] == "agent-status-opacity-pulse" or metrics["reducedMotion"] is True, metrics
    assert metrics["boxShadow"] in ("", "none"), metrics
    assert metrics["filter"] == "none", metrics
    assert metrics["background"] == "rgb(82, 210, 115)", metrics
    assert metrics["color"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["border"] not in ("rgba(0, 0, 0, 0)", "transparent"), metrics
    assert float(metrics["opacity"]) == 1, metrics


def test_mixed_parent_status_ball_uses_two_crisp_child_colors(browser, tmp_path):
    page = tmp_path / "mixed-parent-status-ball.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <section class="mixed-status-fixture">
        <span class="session-agent-activity-marker"><span class="agent-window-activity agent-window-activity--attention"><span id="red-green" class="status-indicator status-indicator--dot status-indicator--attention agent-window-status-dot agent-window-status-dot--segmented agent-window-status-dot--attention-working">●</span></span></span>
        <span class="session-agent-activity-marker"><span class="agent-window-activity agent-window-activity--attention"><span id="red-yellow" class="status-indicator status-indicator--dot status-indicator--attention agent-window-status-dot agent-window-status-dot--segmented agent-window-status-dot--attention-cooldown">●</span></span></span>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 64px; background: #111820; }
      .mixed-status-fixture { display: flex; gap: 24px; }
      .session-agent-activity-marker { --agent-status-ball-size: 28px; }
    """),
    )
    metrics = browser.execute_script(
        """
        const read = id => {
          const node = document.getElementById(id);
          const style = getComputedStyle(node);
          const rect = node.getBoundingClientRect();
          return {backgroundImage: style.backgroundImage, border: style.borderTopColor, width: rect.width, height: rect.height};
        };
        return {redGreen: read('red-green'), redYellow: read('red-yellow')};
        """
    )
    for name in ("redGreen", "redYellow"):
        item = metrics[name]
        assert "conic-gradient" in item["backgroundImage"], metrics
        assert item["border"] not in ("rgba(0, 0, 0, 0)", "transparent"), metrics
        assert abs(item["width"] - item["height"]) <= 0.5, metrics
    assert metrics["redGreen"]["backgroundImage"] != metrics["redYellow"]["backgroundImage"], metrics


def test_pane_tab_cooldown_ball_keeps_canonical_vibrant_yellow_at_rest(browser, tmp_path):
    page = tmp_path / "pane-tab-cooldown-vibrant-yellow.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <section class="status-ball-vibrancy-fixture">
        <button id="tab" class="pane-tab active">
          <span class="pane-tab-core">
            <span class="session-agent-activity-marker">
              <span class="agent-window-activity agent-window-activity--cooldown" style="--attention-animation-delay: 0s">
                <span id="tab-dot" class="status-indicator status-indicator--dot status-indicator--cooldown heartbeat-pulse attention-pulse agent-window-status-dot">●</span>
              </span>
            </span>
            <span class="session-button-prefix">8001</span>
          </span>
        </button>
        <span id="reference-dot" class="status-indicator status-indicator--dot status-indicator--cooldown agent-window-status-dot">●</span>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 64px; background: #111820; color: #e8eef8; font: 18px sans-serif; }
      .status-ball-vibrancy-fixture { display: flex; align-items: center; gap: 32px; }
      #tab { min-width: 160px; min-height: 34px; }
      #reference-dot { font-size: 14px; line-height: 1; }
    """),
    )
    metrics = browser.execute_script(
        """
        const dot = document.getElementById('tab-dot');
        for (const animation of dot.getAnimations()) {
          const duration = Number(animation.effect?.getTiming?.().duration) || 0;
          if (duration > 0) {
            animation.pause();
            animation.currentTime = 0;
          }
        }
        const rect = id => {
          const value = document.getElementById(id).getBoundingClientRect();
          return {left: value.left, top: value.top, width: value.width, height: value.height};
        };
        const style = getComputedStyle(dot);
        return {
          tab: rect('tab-dot'),
          reference: rect('reference-dot'),
              tabFilter: style.filter,
              tabAnimation: style.animationName,
              tabBackground: style.backgroundColor,
              tabBorder: style.borderTopColor,
              tabOpacity: style.opacity,
              canonicalCooldown: getComputedStyle(document.documentElement).getPropertyValue('--agent-status-cooldown').trim(),
        };
        """
    )
    assert metrics["tabFilter"] == "none", metrics
    assert metrics["tabAnimation"] == "agent-status-opacity-pulse", metrics
    assert metrics["tabBackground"] == "rgb(255, 214, 51)", metrics
    assert metrics["tabBorder"] not in ("rgba(0, 0, 0, 0)", "transparent"), metrics
    assert 0.14 <= float(metrics["tabOpacity"]) <= 0.18, metrics
    assert metrics["canonicalCooldown"] == "#ffd633", metrics


def test_attention_status_ball_is_red_and_uses_the_shared_opacity_pulse(browser, tmp_path):
    page = tmp_path / "pane-tab-attention-opacity.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <button class="pane-tab active">
        <span class="pane-tab-core">
          <span class="session-agent-activity-marker">
            <span class="agent-window-activity agent-window-activity--attention" style="--attention-animation-delay:0s">
              <span id="attention-dot" class="status-indicator status-indicator--dot status-indicator--attention heartbeat-pulse agent-window-status-dot agent-window-status-dot--transition-glow">●</span>
            </span>
          </span>
          <span class="session-button-prefix">needs input</span>
        </span>
      </button>
    """, extra_css="body { margin: 0; padding: 64px; background: var(--bg); }"),
    )
    metrics = browser.execute_script(
        """
        const dot = document.getElementById('attention-dot');
        const animation = dot.getAnimations().find(item => item.animationName === 'agent-status-opacity-pulse');
        const read = () => {
          const style = getComputedStyle(dot);
          return {background: style.backgroundColor, border: style.borderTopColor, opacity: style.opacity, filter: style.filter};
        };
        const duration = Number(animation?.effect?.getTiming?.().duration) || 0;
        if (animation && duration > 0) {
          animation.pause();
          animation.currentTime = duration;
        }
        const dim = read();
        if (animation && duration > 0) animation.currentTime = duration * 0.5;
        const bright = read();
        return {animation: getComputedStyle(dot).animationName, dim, bright};
        """
    )
    assert metrics["animation"] == "agent-status-opacity-pulse", metrics
    assert metrics["dim"]["background"] == "rgb(255, 102, 115)", metrics
    assert metrics["dim"]["border"] == "rgb(0, 0, 0)", metrics
    assert metrics["dim"]["filter"] == metrics["bright"]["filter"] == "none", metrics
    assert 0.14 <= float(metrics["dim"]["opacity"]) <= 0.18, metrics
    assert float(metrics["bright"]["opacity"]) == 1, metrics


def test_subwindow_status_glyphs_are_solid_unclipped_shapes_without_tab_dot_override(browser, tmp_path):
    page = tmp_path / "subwindow-status-solid-shapes.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(f"""
      <section class="subwindow-glyph-fixture">
        <div class="tmux-window-bar" data-tmux-window-label-mode="names">
          <span id="bar-button" class="tab tmux-window-button active">
            <span class="tmux-window-name-label">
              {_agent_status_glyph_html("codex", "working", "bar-working", subwindow=True)}
              <span class="tmux-window-name-text">0:codex</span>
            </span>
          </span>
        </div>
        <div class="tmux-window-bar" data-tmux-window-label-mode="names">
          <span id="stable-button" class="tab tmux-window-button">
            <span class="tmux-window-name-label">
                  <span class="agent-window-activity agent-window-activity--subwindow agent-window-activity--working">
                <span id="stable-working-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-status-dot agent-window-activity-icon--working status-indicator--working">●</span>
              </span>
              <span class="tmux-window-name-text">3:codex</span>
            </span>
          </span>
        </div>
        <div class="tmux-window-bar" data-tmux-window-label-mode="names">
          <span id="stale-cooldown-button" class="tab tmux-window-button">
            <span class="tmux-window-name-label">
                  <span class="agent-window-activity agent-window-activity--subwindow agent-window-activity--cooldown">
                <span id="stale-cooldown-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-status-dot agent-window-activity-icon--cooldown status-indicator--cooldown">●</span>
              </span>
              <span class="tmux-window-name-text">4:codex</span>
            </span>
          </span>
        </div>
        <div class="tmux-window-bar" data-tmux-window-label-mode="names">
          <span id="active-stale-cooldown-button" class="tab tmux-window-button active">
            <span class="tmux-window-name-label">
                  <span class="agent-window-activity agent-window-activity--subwindow agent-window-activity--cooldown">
                <span id="active-stale-cooldown-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-status-dot agent-window-activity-icon--cooldown status-indicator--cooldown">●</span>
              </span>
              <span class="tmux-window-name-text">5:codex</span>
            </span>
          </span>
        </div>
        <div class="session-agent-window-block">
          <div id="popover-row" class="session-agent-row current">
            {_agent_status_glyph_html("claude", "attention", "popover-attention", subwindow=True)}
          </div>
        </div>
        <div class="file-tree-row tabber-row" data-tabber-type="window">
          <span class="tabber-window-label">
            {_agent_status_glyph_html("codex", "cooldown", "tabber-cooldown", subwindow=True)}
            <span class="tabber-window-text">1:codex</span>
          </span>
        </div>
        <div class="file-tree-row tabber-row" data-tabber-type="session">
          <span class="tabber-window-label">
            {_agent_status_glyph_html("codex", "working", "session-aggregate")}
          </span>
        </div>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 32px; background: var(--bg); color: var(--text); font: 16px sans-serif; }
      .subwindow-glyph-fixture { display: grid; gap: 18px; justify-items: start; }
      .tmux-window-button,
      .session-agent-row,
      .file-tree-row.tabber-row { padding: 6px 10px; background: var(--panel2); }
      .tmux-window-button.active { background: var(--active-control-bg); }
      .agent-window-activity { overflow: visible; }
    """),
    )
    metrics = browser.execute_script(
        """
        document.documentElement.classList.add('status-pulse-disabled');
        document.getElementById('bar-working-dot').classList.add('agent-window-status-dot--subwindow-pulse');
        document.getElementById('popover-attention-dot').classList.add('agent-window-status-dot--subwindow-pulse');
        document.getElementById('tabber-cooldown-dot').classList.add('agent-window-status-dot--subwindow-pulse');
        const read = id => {
          const dot = document.getElementById(id + '-dot');
          const before = getComputedStyle(dot, '::before');
              const after = getComputedStyle(dot, '::after');
              const style = getComputedStyle(dot);
              const button = dot.closest('.tmux-window-button');
              const rect = dot.getBoundingClientRect();
              return {
                color: style.color,
                textIndent: style.textIndent,
                fontSize: style.fontSize,
                overflow: style.overflow,
                borderRadius: style.borderTopLeftRadius,
                className: dot.className,
                animationName: style.animationName,
                boxShadow: style.boxShadow,
                filter: style.filter,
                width: rect.width,
                height: rect.height,
                beforeContent: before.content,
                beforeBackground: before.backgroundColor,
                beforeAnimationName: before.animationName,
                beforeOpacity: before.opacity,
                beforeBorderStartColor: before.borderInlineStartColor,
                beforeBorderStartWidth: before.borderInlineStartWidth,
            beforeBorderTopColor: before.borderTopColor,
            beforeBorderTopWidth: before.borderTopWidth,
            beforeFilter: before.filter,
            beforeInlineSize: before.inlineSize || before.width || '',
            beforeInsetInlineStart: before.insetInlineStart || '',
            afterContent: after.content,
            afterBackground: after.backgroundColor,
            afterAnimationName: after.animationName,
            afterOpacity: after.opacity,
            afterBorderTopColor: after.borderTopColor,
            afterBorderTopWidth: after.borderTopWidth,
            afterFilter: after.filter,
            afterInsetInlineStart: after.insetInlineStart || '',
            buttonColor: button ? getComputedStyle(button).color : '',
          };
        };
        const dark = {
          bar: read('bar-working'),
          stable: read('stable-working'),
          staleCooldown: read('stale-cooldown'),
          activeStaleCooldown: read('active-stale-cooldown'),
          popover: read('popover-attention'),
          tabber: read('tabber-cooldown'),
          session: read('session-aggregate'),
        };
        const barActivity = document.getElementById('bar-working').closest('.agent-window-activity');
        const barIcon = barActivity.querySelector('.agent-icon').getBoundingClientRect();
        const barDot = barActivity.querySelector('.agent-window-status-dot').getBoundingClientRect();
        const barLabel = barActivity.closest('.tmux-window-name-label').querySelector('.tmux-window-name-text').getBoundingClientRect();
        const barInnerGap = Math.max(barIcon.left, barDot.left) - Math.min(barIcon.right, barDot.right);
        const barLabelGap = barLabel.left - barActivity.getBoundingClientRect().right;
        document.body.classList.add('theme-light');
        return {
          ...dark,
          barInnerGap,
          barLabelGap,
          lightStaleCooldown: read('stale-cooldown'),
          lightActiveStaleCooldown: read('active-stale-cooldown'),
        };
        """
    )
    assert metrics["bar"]["textIndent"] == "0px", metrics
    assert metrics["bar"]["fontSize"] == "14px", metrics
    assert metrics["bar"]["color"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["bar"]["overflow"] == "visible", metrics
    assert metrics["bar"]["borderRadius"] == "0px", metrics
    assert "agent-window-status-dot--subwindow-pulse" in metrics["bar"]["className"], metrics
    assert metrics["bar"]["animationName"] == "agent-status-opacity-pulse", metrics
    assert metrics["bar"]["boxShadow"] in ("", "none"), metrics
    assert metrics["bar"]["filter"] == "none", metrics
    assert 2.9 <= metrics["barInnerGap"] <= 3.1, metrics
    assert metrics["barLabelGap"] >= 1, metrics
    assert 8 <= metrics["bar"]["width"] <= 10 and 8 <= metrics["bar"]["height"] <= 10, metrics
    assert metrics["bar"]["beforeContent"] == '""', metrics
    assert metrics["bar"]["beforeAnimationName"] == "none", metrics
    assert 0.14 <= float(metrics["bar"]["beforeOpacity"]) <= 1, metrics
    assert metrics["bar"]["beforeBackground"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["bar"]["afterBackground"] == "rgb(82, 210, 115)", metrics
    assert metrics["bar"]["afterAnimationName"] == "none", metrics
    assert metrics["bar"]["beforeFilter"] == "none", metrics
    assert metrics["bar"]["afterFilter"] == "none", metrics
    stable_width = float(metrics["stable"]["beforeInlineSize"].replace("px", ""))
    pulsing_width = float(metrics["bar"]["beforeInlineSize"].replace("px", ""))
    stale_pause_width = float(metrics["staleCooldown"]["beforeInlineSize"].replace("px", ""))
    pulsing_pause_width = float(metrics["tabber"]["beforeInlineSize"].replace("px", ""))
    assert 0.95 <= stable_width / pulsing_width <= 1.05, metrics
    assert 0.95 <= stale_pause_width / pulsing_pause_width <= 1.05, metrics
    assert metrics["staleCooldown"]["beforeBackground"] == "rgb(255, 214, 51)", metrics
    assert metrics["staleCooldown"]["afterBackground"] == "rgb(255, 214, 51)", metrics
    assert metrics["activeStaleCooldown"]["beforeBackground"] == "rgb(255, 214, 51)", metrics
    assert metrics["activeStaleCooldown"]["afterBackground"] == "rgb(255, 214, 51)", metrics
    assert metrics["lightStaleCooldown"]["beforeBackground"] == "rgb(194, 138, 0)", metrics
    assert metrics["lightStaleCooldown"]["afterBackground"] == "rgb(194, 138, 0)", metrics
    assert metrics["lightActiveStaleCooldown"]["beforeBackground"] == "rgb(194, 138, 0)", metrics
    assert metrics["lightActiveStaleCooldown"]["afterBackground"] == "rgb(194, 138, 0)", metrics
    assert metrics["popover"]["beforeContent"] == '""', metrics
    assert metrics["popover"]["color"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["popover"]["beforeBackground"] == "rgb(220, 38, 38)", metrics
    assert metrics["popover"]["animationName"] == "agent-status-opacity-pulse", metrics
    assert metrics["popover"]["boxShadow"] in ("", "none"), metrics
    assert metrics["popover"]["beforeAnimationName"] == "none", metrics
    assert 0.14 <= float(metrics["popover"]["beforeOpacity"]) <= 1, metrics
    assert metrics["popover"]["beforeBorderTopWidth"] == "0px", metrics
    assert metrics["popover"]["beforeFilter"] == "none", metrics
    assert metrics["tabber"]["beforeContent"] == '""', metrics
    assert metrics["tabber"]["afterContent"] == '""', metrics
    assert metrics["tabber"]["color"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["tabber"]["beforeBackground"] == "rgb(255, 214, 51)", metrics
    assert metrics["tabber"]["afterBackground"] == "rgb(255, 214, 51)", metrics
    assert metrics["tabber"]["animationName"] == "agent-status-opacity-pulse", metrics
    assert metrics["tabber"]["boxShadow"] in ("", "none"), metrics
    assert metrics["tabber"]["beforeAnimationName"] == "none", metrics
    assert metrics["tabber"]["afterAnimationName"] == "none", metrics
    assert 0.14 <= float(metrics["tabber"]["beforeOpacity"]) <= 1, metrics
    assert 0.14 <= float(metrics["tabber"]["afterOpacity"]) <= 1, metrics
    assert metrics["tabber"]["beforeBorderTopWidth"] == "0px", metrics
    assert metrics["tabber"]["afterBorderTopWidth"] == "0px", metrics
    assert metrics["tabber"]["beforeFilter"] == "none", metrics
    assert metrics["tabber"]["afterFilter"] == "none", metrics
    for key in ("tabber", "staleCooldown", "activeStaleCooldown"):
        before_left = float(metrics[key]["beforeInsetInlineStart"].replace("px", ""))
        after_left = float(metrics[key]["afterInsetInlineStart"].replace("px", ""))
        bar_width = float(metrics[key]["beforeInlineSize"].replace("px", ""))
        assert after_left - before_left - bar_width >= 1.0, (key, metrics)
    assert metrics["session"]["beforeContent"] in ("", "none"), metrics
    assert metrics["session"]["beforeBackground"] in ("", "rgba(0, 0, 0, 0)"), metrics


def test_agent_status_glyphs_split_on_tabs_tabber_and_info_buttons(browser, tmp_path):
    page = tmp_path / "agent-status-split-surfaces.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(f"""
      <section class="agent-status-split-fixture">
        <button id="dock-tab" class="pane-tab active">
          <span class="pane-tab-core">
            <span class="session-yolo-marker">YO</span>
            <span class="session-agent-activity-marker">{_agent_status_glyph_html("claude", "attention", "dock-attention")}</span>
            <span class="session-button-prefix">8002b</span>
          </span>
        </button>
        <div id="tabber-session-row" class="file-tree-row tabber-row selected" data-tabber-type="session" style="--file-explorer-font-size: 18px;">
          <span class="file-tree-name">
            <span class="tmux-pane-tab-token tmux-pane-tab-token-action tabber-session-tab session-popover-host active" data-tabber-session-chrome="shared">
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
            {_tabber_window_button_html("claude", "0:claude", _agent_status_glyph_html("claude", "cooldown", "tabber-window-cooldown", subwindow=True))}
          </span>
        </div>
        <div id="info-pane" class="pane-info-bar">
          <button id="info-button" class="tab tmux-window-button active">
            <span class="tmux-window-name-label">
              {_agent_status_glyph_html("codex", "attention", "info-attention", subwindow=True)}
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
    """),
    )
    metrics = browser.execute_script(
        """
        document.getElementById('tabber-window-cooldown-dot').classList.add('agent-window-status-dot--subwindow-pulse');
        document.getElementById('info-attention-dot').classList.add('agent-window-status-dot--subwindow-pulse');
        const read = id => {
          const icon = document.getElementById(id);
          const dot = document.getElementById(id + '-dot');
          const dotLiveStyle = getComputedStyle(dot);
          const beforeLiveStyle = getComputedStyle(dot, '::before');
          const dotAnimationName = dotLiveStyle.animationName;
          const beforeAnimationName = beforeLiveStyle.animationName;
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
            beforeAnimation: beforeAnimationName,
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
            if name in ("tabberWindowCooldown", "infoAttention"):
                assert item["dotAnimation"] == "agent-status-opacity-pulse", (name, metrics)
                assert item["beforeAnimation"] == "none", (name, metrics)
                assert item["dotBoxShadow"] in ("", "none"), (name, metrics)
            elif name == "tabberSessionWorking":
                assert item["dotAnimation"] == "agent-status-opacity-pulse", (name, metrics)
                assert item["dotBoxShadow"] in ("", "none"), (name, metrics)
            else:
                assert item["dotAnimation"] == "agent-status-opacity-pulse", (name, metrics)
                assert item["dotPlayState"] == "running", (name, metrics)
                assert item["dotIterationCount"] == "infinite", (name, metrics)
                assert item["dotBoxShadow"] in ("", "none"), (name, metrics)
        assert item["dotWidth"] > 0 and item["dotHeight"] > 0, (name, metrics)
    assert metrics["dockAttention"]["dotToneAttention"] is True, metrics
    assert metrics["infoAttention"]["dotToneAttention"] is True, metrics
    assert metrics["tabberSessionWorking"]["dotToneWorking"] is True, metrics
    assert metrics["tabberWindowCooldown"]["dotToneCooldown"] is True, metrics
    aggregate_names = ("dockAttention", "tabberSessionWorking")
    subwindow_names = ("tabberWindowCooldown", "infoAttention")
    aggregate_ball_sizes = {metrics[name]["agentStatusBallSize"] for name in aggregate_names}
    aggregate_dot_font_sizes = {metrics[name]["dotFontSize"] for name in aggregate_names}
    aggregate_peak_widths = [metrics[name]["dotWidth"] for name in aggregate_names]
    aggregate_peak_heights = [metrics[name]["dotHeight"] for name in aggregate_names]
    subwindow_peak_widths = [metrics[name]["dotWidth"] for name in subwindow_names]
    subwindow_peak_heights = [metrics[name]["dotHeight"] for name in subwindow_names]
    transforms = {metrics[name]["dotTransform"] for name in ("dockAttention", "tabberSessionWorking", "tabberWindowCooldown", "infoAttention")}
    assert aggregate_ball_sizes == {"14px"}, metrics
    assert aggregate_dot_font_sizes == {"14px"}, metrics
    for name in subwindow_names:
        assert metrics[name]["agentStatusBallSize"] == "14px", (name, metrics)
        assert metrics[name]["dotFontSize"] == "14px", (name, metrics)
    assert max(aggregate_peak_widths) - min(aggregate_peak_widths) <= 0.5, metrics
    assert max(aggregate_peak_heights) - min(aggregate_peak_heights) <= 0.5, metrics
    assert max(subwindow_peak_widths) - min(subwindow_peak_widths) <= 0.5, metrics
    assert max(subwindow_peak_heights) - min(subwindow_peak_heights) <= 0.5, metrics
    assert all(abs(width - (aggregate_peak_widths[0] * 0.66)) <= 0.5 for width in subwindow_peak_widths), metrics
    assert all(abs(height - (aggregate_peak_heights[0] * 0.66)) <= 0.5 for height in subwindow_peak_heights), metrics
    assert len(transforms) == 1, metrics


def test_tabber_child_status_ball_uses_compact_subwindow_size_and_shared_phase(browser, tmp_path):
    page = tmp_path / "tabber-parent-child-status-ball-parity.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(f"""
      <section class="tabber-ball-parity-fixture">
        <div id="tabber-session-row" class="file-tree-row tabber-row selected" data-tabber-type="session" style="--file-explorer-font-size: 16px;">
          <span class="file-tree-name">
            <span class="tmux-pane-tab-token tmux-pane-tab-token-action tabber-session-tab session-popover-host active" data-tabber-session-chrome="shared">
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
            {_tabber_window_button_html("codex", "0:codex", _working_agent_glyph_html("codex", "tabber-window-working", subwindow=True))}
          </span>
        </div>
      </section>
    """, extra_css="""
      body { margin: 0; padding: 32px; background: #202633; color: #e8eef8; font: 18px sans-serif; }
      .tabber-ball-parity-fixture { display: grid; justify-items: start; gap: 16px; }
      .file-tree-row.tabber-row { width: 620px; padding: 5px 8px; background: #2c3340; overflow: visible; }
      .file-tree-name,
      .tabber-session-tab,
      .tabber-window-token,
      .agent-window-activity { overflow: visible; }
    """),
    )
    metrics = browser.execute_script(
        """
        const read = id => {
          const icon = document.getElementById(id);
          const dot = document.getElementById(id + '-dot');
          const wrap = dot.closest('.agent-window-activity');
          if (id === 'tabber-window-working') dot.classList.add('agent-window-status-dot--subwindow-pulse');
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
          const beforeStyle = getComputedStyle(dot, '::before');
          const dotRect = dot.getBoundingClientRect();
          return {
            iconSize: iconStyle.width,
            agentWindowIconSize: wrapStyle.getPropertyValue('--agent-window-icon-size').trim(),
            agentStatusBallSize: wrapStyle.getPropertyValue('--agent-status-ball-size').trim(),
            dotFontSize: dotStyle.fontSize,
            dotFontStretch: dotStyle.fontStretch,
            animationName: dotStyle.animationName,
            beforeAnimationName: beforeStyle.animationName,
            animationDuration: dotStyle.animationDuration,
            animationDelay: dotStyle.animationDelay,
            animationTimingFunction: dotStyle.animationTimingFunction,
            opacity: dotStyle.opacity,
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
    assert metrics["parent"]["agentStatusBallSize"] == "14px", metrics
    assert metrics["parent"]["dotFontSize"] == "14px", metrics
    assert metrics["child"]["agentStatusBallSize"] == "14px", metrics
    assert metrics["child"]["dotFontSize"] == "14px", metrics
    assert abs(metrics["child"]["width"] - (metrics["parent"]["width"] * 0.66)) <= 0.5, metrics
    assert abs(metrics["child"]["height"] - (metrics["parent"]["height"] * 0.66)) <= 0.5, metrics
    for side in ("parent", "child"):
        assert metrics[side]["dotFontStretch"] in {"normal", "100%"}, metrics
    assert metrics["parent"]["animationName"] == "agent-status-opacity-pulse", metrics
    assert metrics["child"]["animationName"] == "agent-status-opacity-pulse", metrics
    assert metrics["child"]["beforeAnimationName"] == "none", metrics
    assert metrics["parent"]["opacity"] == metrics["child"]["opacity"] == "1", metrics
    assert metrics["parent"]["transform"] == metrics["child"]["transform"], metrics


def test_status_balls_share_attention_label_pulse_cadence_and_actually_pulsate(browser, tmp_path):
    page = tmp_path / "attention-dot-pulse.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <span id="working-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-activity-icon--working status-indicator--working heartbeat-pulse" style="--attention-animation-delay:-0.42s">●</span>
      <span id="window-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-activity-icon--attention status-indicator--attention heartbeat-pulse attention-pulse" style="--attention-animation-delay:-0.42s">●</span>
      <span id="popover-dot" class="status-indicator session-agent-dot status-indicator--dot status-indicator--attention heartbeat-pulse attention-pulse" style="--attention-animation-delay:-0.42s">●</span>
      <span id="tabber-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-activity-icon--attention status-indicator--attention heartbeat-pulse attention-pulse" style="--attention-animation-delay:-0.42s">●</span>
      <span id="cooldown-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-activity-icon--cooldown status-indicator--cooldown heartbeat-pulse attention-pulse" style="--attention-animation-delay:-0.42s">●</span>
      <span id="attention-label" class="status-indicator tabber-agent-status status-indicator--label agent-status-attention status-indicator--attention heartbeat-pulse attention-pulse" style="--attention-animation-delay:-0.42s">&lt;15 sec ago</span>
    """, extra_css="""
      :root { --pulse-duration: 1.8s; --pulse-easing: ease-in-out; --bad: #ff3347; --danger-text: #ff3347; --text: #dbe2ef; --muted: #8590a6; }
      body { display: grid; justify-items: start; gap: 34px; background: #111; color: #ddd; font: 16px sans-serif; padding: 32px; }
    """),
    )
    metrics = browser.execute_script(
        """
        const ids = ['working-dot', 'window-dot', 'popover-dot', 'tabber-dot', 'cooldown-dot', 'attention-label'];
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
    if metrics["attention-label"]["reduced"]:
        pytest.skip("browser prefers reduced motion")
    badge = metrics["attention-label"]
    assert "attention-ring-fade" in badge["animationName"], badge
    assert badge["rest"]["boxShadow"] != badge["peak"]["boxShadow"], badge
    for dot_id in ("working-dot", "window-dot", "popover-dot", "tabber-dot", "cooldown-dot"):
        dot = metrics[dot_id]
        uses_opacity_pulse = dot_id in ("working-dot", "cooldown-dot")
        assert dot["primaryAnimationName"] == ("agent-status-opacity-pulse" if uses_opacity_pulse else "attention-ring-fade"), {dot_id: dot}
        assert dot["primaryAnimationPlayState"] == "running", {dot_id: dot}
        assert dot["primaryAnimationIterationCount"] == "infinite", {dot_id: dot}
        assert dot["primaryAnimationDuration"] == badge["primaryAnimationDuration"], {dot_id: dot, "badge": badge}
        assert dot["primaryAnimationDelay"] == badge["primaryAnimationDelay"], {dot_id: dot, "badge": badge}
        assert dot["primaryAnimationTimingFunction"] == badge["primaryAnimationTimingFunction"], {dot_id: dot, "badge": badge}
        assert dot["delayVar"] == badge["delayVar"] == "-0.42s", {dot_id: dot, "badge": badge}
        assert dot["borderTopStyle"] in {"none", "solid"}, {dot_id: dot}
        if uses_opacity_pulse:
            assert dot["rest"]["boxShadow"] == dot["peak"]["boxShadow"] == "none", {dot_id: dot}
            assert dot["rest"]["filter"] == dot["peak"]["filter"] == "none", {dot_id: dot}
            assert dot["rest"]["opacity"] < dot["peak"]["opacity"], {dot_id: dot}
        else:
            assert dot["rest"]["boxShadow"] != dot["peak"]["boxShadow"], {dot_id: dot}
            assert dot["rest"]["filter"] != dot["peak"]["filter"], {dot_id: dot}
        assert abs(dot["rest"]["rect"]["width"] - dot["peak"]["rect"]["width"]) <= 0.5, {dot_id: dot}
        assert abs(dot["rest"]["rect"]["height"] - dot["peak"]["rect"]["height"]) <= 0.5, {dot_id: dot}
    assert metrics["working-dot"]["animationName"] == "agent-status-opacity-pulse", metrics["working-dot"]
    assert metrics["working-dot"]["rest"]["opacity"] < metrics["working-dot"]["peak"]["opacity"], metrics["working-dot"]
    for dot_id in ("window-dot", "popover-dot", "tabber-dot", "cooldown-dot"):
        assert abs(metrics["working-dot"]["rest"]["rect"]["width"] - metrics[dot_id]["rest"]["rect"]["width"]) <= 2.1, {dot_id: metrics[dot_id], "working": metrics["working-dot"]}
        assert abs(metrics["working-dot"]["rest"]["rect"]["height"] - metrics[dot_id]["rest"]["rect"]["height"]) <= 2.1, {dot_id: metrics[dot_id], "working": metrics["working-dot"]}
        assert abs(metrics["working-dot"]["peak"]["rect"]["width"] - metrics[dot_id]["peak"]["rect"]["width"]) <= 2.1, {dot_id: metrics[dot_id], "working": metrics["working-dot"]}
        assert abs(metrics["working-dot"]["peak"]["rect"]["height"] - metrics[dot_id]["peak"]["rect"]["height"]) <= 2.1, {dot_id: metrics[dot_id], "working": metrics["working-dot"]}

    visual_ids = {"working-dot": "green", "window-dot": "red", "cooldown-dot": "yellow"}
    all_animation_ids = ["working-dot", "window-dot", "popover-dot", "tabber-dot", "cooldown-dot", "attention-label"]
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
        if dot_id in ("working-dot", "cooldown-dot"):
            assert peak_score["count"] == rest_score["count"], visual_scores
        else:
            assert peak_score["count"] > rest_score["count"] + count_delta, visual_scores
    working_energy_delta = visual_scores["working-dot"]["peak"]["energy"] - visual_scores["working-dot"]["rest"]["energy"]
    red_energy_delta = visual_scores["window-dot"]["peak"]["energy"] - visual_scores["window-dot"]["rest"]["energy"]
    yellow_energy_delta = visual_scores["cooldown-dot"]["peak"]["energy"] - visual_scores["cooldown-dot"]["rest"]["energy"]
    assert working_energy_delta < red_energy_delta, visual_scores
    assert working_energy_delta < yellow_energy_delta, visual_scores


def test_recreated_working_status_ball_keeps_wall_clock_pulse_phase(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof agentWindowActivityIconHtml === 'function' && typeof setAttentionAnimationClockDelay === 'function';"
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const duration = 1550;
          globalThis.yolomuxEnableBroadStatusPulse = false;
          agentStatusPulsePeriodMs = duration;
          document.documentElement.style.setProperty('--pulse-duration', `${duration / 1000}s`);
          document.documentElement.style.setProperty('--status-pulse-step-count', '12');
          setAttentionAnimationClockDelay();
          const host = document.createElement('div');
          document.body.appendChild(host);
          const item = {
            state: 'working',
            icon: '●',
            label: 'Codex working',
            pulseActive: true,
            transitionPulseActive: true,
            acknowledged: false,
          };
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
          const samples = [];
          let previousDot = null;
          for (let index = 0; index < 5; index += 1) {
            host.innerHTML = agentWindowActivityIconHtml('codex', 'working', 0, {
              session: '1',
              window_index: 0,
              current: true,
              statusOnly: true,
              item,
            });
            await frame();
            const dot = host.querySelector('.agent-window-status-dot');
            const animation = dot?.getAnimations?.().find(value => value.animationName === 'agent-status-opacity-pulse');
            const timing = animation?.effect?.getComputedTiming?.() || {};
            const sampledAt = Date.now();
            const expectedProgress = ((sampledAt % duration) + duration) % duration / duration;
            const progress = Number(timing.progress);
            const phaseDistance = Math.min(Math.abs(progress - expectedProgress), 1 - Math.abs(progress - expectedProgress));
            samples.push({
              replaced: previousDot !== null && previousDot !== dot,
              progress,
              expectedProgress,
              phaseDistance,
              opacity: Number(getComputedStyle(dot).opacity),
              delay: getComputedStyle(dot).animationDelay,
            });
            previousDot = dot;
            await pause(260);
          }
          host.remove();
          done({samples});
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert all(sample["replaced"] for sample in metrics["samples"][1:]), metrics
    assert all(sample["phaseDistance"] < 0.08 for sample in metrics["samples"]), metrics
    assert len({round(sample["progress"], 2) for sample in metrics["samples"]}) >= 4, metrics
    assert len({round(sample["opacity"], 2) for sample in metrics["samples"]}) >= 3, metrics


def test_status_balls_keep_pulse_cadence_under_reduced_motion(browser, tmp_path):
    page = tmp_path / "attention-dot-reduced-motion.html"
    page.write_text(page_html("""
      <span id="working-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-activity-icon--working status-indicator--working heartbeat-pulse" style="--attention-animation-delay:-0.42s">●</span>
      <span id="attention-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-activity-icon--attention status-indicator--attention heartbeat-pulse attention-pulse" style="--attention-animation-delay:-0.42s">●</span>
      <span id="cooldown-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-activity-icon--cooldown status-indicator--cooldown heartbeat-pulse attention-pulse" style="--attention-animation-delay:-0.42s">●</span>
    """, extra_css="""
      :root { --pulse-duration: 1.55s; --pulse-easing: ease-in-out; --status-pulse-step-count: 12; --status-pulse-timing: steps(var(--status-pulse-step-count), end); --bad: #ff3347; --danger-text: #ff3347; --text: #dbe2ef; --muted: #8590a6; }
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
                cooldown: read('cooldown-dot'),
              };
            """
        )
        assert metrics["reduced"] is True, metrics
        attention = metrics["attention"]
        assert attention["primaryAnimationName"] == "none", metrics
        assert attention["primaryAnimationDuration"] == "1.55s", metrics
        assert attention["primaryAnimationTimingFunction"].startswith("steps(12"), metrics
        assert attention["primaryAnimationDelay"] == "-0.42s", metrics
        assert attention["primaryEffectDuration"] == 0, metrics
        for key in ("working", "cooldown"):
            dot = metrics[key]
            assert dot["primaryAnimationName"] == "agent-status-opacity-pulse", metrics
            assert dot["primaryAnimationDuration"] == "1.55s", metrics
            assert dot["primaryAnimationDelay"] == "-0.42s", metrics
            assert dot["primaryAnimationTimingFunction"].startswith("steps(12"), metrics
            assert dot["primaryEffectDuration"] > 0, metrics
            assert dot["primaryPlayState"] in {"pending", "running"}, metrics
        assert metrics["working"]["animationName"] == "agent-status-opacity-pulse", metrics
    finally:
        browser.execute_cdp_cmd("Emulation.setEmulatedMedia", {"features": []})


def test_agent_attention_and_cooldown_status_balls_sit_beside_static_ai_icon(browser, tmp_path):
    page = tmp_path / "agent-status-split.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <div id="base" class="agent-window-activity agent-window-activity--attention" style="--attention-animation-delay:-0.42s">
        <span id="base-icon" class="agent-icon claude agent-window-activity-icon agent-window-agent-icon agent-window-activity-icon--attention agent-window-agent-icon--attention">
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="5.5" fill="#cf7554"/></svg>
        </span>
        <span id="base-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-status-dot agent-window-activity-icon--attention status-indicator--attention heartbeat-pulse attention-pulse">●</span>
      </div>
      <button id="info-button" class="tab tmux-window-button">
        <span class="tmux-window-name-label">
          <span id="info" class="agent-window-activity agent-window-activity--subwindow agent-window-activity--attention" style="--attention-animation-delay:-0.37s">
            <span id="info-icon" class="agent-icon claude agent-window-activity-icon agent-window-agent-icon agent-window-activity-icon--attention agent-window-agent-icon--attention">
              <svg viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="5.5" fill="#cf7554"/></svg>
            </span>
            <span id="info-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-status-dot agent-window-activity-icon--attention status-indicator--attention heartbeat-pulse attention-pulse">●</span>
          </span>
          <span class="tmux-window-name-text">0:claude</span>
        </span>
      </button>
      <div class="file-tree-row tabber-row" style="--file-explorer-font-size: 14px;">
        <span class="tabber-window-token tmux-window-bar" data-tmux-window-label-mode="names" data-tmux-window-bar-context="info">
          <span class="tab tmux-window-button tabber-window-button" data-tabber-window-button="shared">
            <span class="tmux-window-name-label">
              <span id="tabber" class="agent-window-activity agent-window-activity--subwindow agent-window-activity--cooldown" style="--attention-animation-delay:-0.91s">
                <span id="tabber-icon" class="agent-icon codex agent-window-activity-icon agent-window-agent-icon agent-window-activity-icon--cooldown agent-window-agent-icon--cooldown">
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#667ef8" d="M3 12a9 9 0 1 0 18 0A9 9 0 0 0 3 12z"/></svg>
                </span>
                <span id="tabber-dot" class="status-indicator agent-window-activity-icon status-indicator--dot agent-window-status-dot agent-window-activity-icon--cooldown status-indicator--cooldown heartbeat-pulse attention-pulse">●</span>
              </span>
              <span class="tmux-window-name-text">1:codex</span>
            </span>
          </span>
        </span>
      </div>
    """, extra_css="""
      body { background: #111; color: #ddd; font: 16px sans-serif; padding: 24px; display: grid; gap: 16px; }
    """),
    )
    metrics = browser.execute_script(
        """
        document.getElementById('info-dot').classList.add('agent-window-status-dot--subwindow-pulse');
        document.getElementById('tabber-dot').classList.add('agent-window-status-dot--subwindow-pulse');
        const rect = id => {
          const r = document.getElementById(id).getBoundingClientRect();
          return {left: r.left, top: r.top, width: r.width, height: r.height, cx: r.left + r.width / 2, cy: r.top + r.height / 2, right: r.right};
        };
        const read = (rootId, iconId, dotId) => {
          const root = document.getElementById(rootId);
          const rootStyle = getComputedStyle(root);
          const iconStyle = getComputedStyle(document.getElementById(iconId));
          const dotStyle = getComputedStyle(document.getElementById(dotId));
          const beforeStyle = getComputedStyle(document.getElementById(dotId), '::before');
          const rootRect = rect(rootId);
          const iconRect = rect(iconId);
          const dotRect = rect(dotId);
          return {
            rootDisplay: rootStyle.display,
            rootWidth: rootRect.width,
            iconAnimation: iconStyle.animationName,
            dotAnimation: dotStyle.animationName,
            beforeAnimation: beforeStyle.animationName,
            beforeDelay: beforeStyle.animationDelay,
            dotBoxShadow: dotStyle.boxShadow,
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
        if name == "base":
            assert "attention-ring-fade" in item["dotAnimation"], (name, item)
            assert item["dotDelay"] == item["rootDelayVar"], (name, item)
        else:
            assert item["dotAnimation"] == "agent-status-opacity-pulse", (name, item)
            assert item["beforeAnimation"] == "none", (name, item)
            assert item["dotDelay"] == item["rootDelayVar"], (name, item)
            assert item["dotBoxShadow"] in ("", "none"), (name, item)
        assert item["leftGap"] >= -0.5, (name, item)
        assert item["centerDy"] <= 1, (name, item)
        assert item["dotWithinRoot"] is True, (name, item)
        assert item["dotDelayVar"] == item["rootDelayVar"], (name, item)
    assert metrics["info"]["rootWidth"] >= 24
    assert 24 <= metrics["tabber"]["rootWidth"] <= 27


def test_pane_info_bar_scrolls_metadata_without_shrinking_window_buttons(browser, tmp_path):
    page = tmp_path / "pane-info-bar-scroll.html"
    long_text = "#76 DRAFT · keivenchang/DIS-2239__parity-commit-link-frontend-crates · ~/dynamo/frontend-crates3 · 5 dirty · DIS-2239 In Review · fix(performance): repair v1 PARITY commit + case-doc links after"
    body = """
      <article class="panel active-pane" style="width: 520px;">
        <div id="info-bar" class="pane-info-bar panel-detail-row">
          <div class="pane-info-bar-popover-zone panel-popover-zone">
            <div class="panel-session-label"><span class="session-button-dir">8001</span></div>
            <div id="meta" class="pane-info-bar-meta meta pane-info-bar-meta-overflow" style="--pane-info-bar-scroll-distance: 240px; --pane-info-bar-scroll-offset: -240px; --pane-info-bar-scroll-duration: 23s; --pane-info-bar-scroll-timing: linear(0 0%, 0 13.04%, 1 91.30%, 1 100%);">
              <span id="viewport" class="pane-info-bar-scroll-viewport"><span id="scroll-text" class="pane-info-bar-scroll-text"><span class="meta-branch">__LONG_TEXT__</span></span></span>
            </div>
          </div>
          <div id="window-bar" class="tmux-window-bar" data-tmux-window-bar-context="info-bar" data-tmux-window-label-mode="names">
            <button type="button" class="tab tmux-window-button"><span class="tmux-window-name-label"><span class="tmux-window-name-text">0:codex</span></span><span class="tmux-window-number-label">0</span></button>
            <button type="button" class="tab tmux-window-button active"><span class="tmux-window-name-label"><span class="tmux-window-name-text">1:claude</span></span><span class="tmux-window-number-label">1</span></button>
            <button type="button" class="tab tmux-window-button"><span class="tmux-window-name-label"><span class="tmux-window-name-text">2:bash</span></span><span class="tmux-window-number-label">2</span></button>
          </div>
          <div id="controls" class="pane-info-bar-controls"><span class="meta-repo-switch"><button type="button" class="btn-base meta-repo-cycle">&lt;</button><button type="button" class="btn-base meta-repo-chip">2/3</button><button type="button" class="btn-base meta-repo-cycle">&gt;</button></span></div>
          <button id="status-toggle" type="button" class="tab tmux-status-toggle tmux-status-toggle--none">·</button>
          <button type="button" class="panel-detail-close"></button>
        </div>
      </article>
    """.replace("__LONG_TEXT__", long_text)
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(body, extra_css="""
      body { margin: 0; padding: 24px; background: var(--bg); color: var(--text); }
      .panel { height: auto; }
    """),
    )
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
          statusToggle: rect('status-toggle'),
        };
        """
    )
    assert "keivenchang/DIS-2239__parity-commit-link-frontend-crates" in metrics["metaText"]
    assert "DIS-2239 In Review" in metrics["metaText"]
    assert "fix(performance): repair v1 PARITY commit + case-doc links after" in metrics["metaText"]
    assert metrics["controlsInsideViewport"] is False
    assert metrics["controls"]["left"] >= metrics["bar"]["right"] - 1
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
    assert metrics["statusToggle"]["left"] >= metrics["viewport"]["right"] - 1
    assert metrics["labelMode"] == "names"
    assert set(metrics["visibleNameDisplays"]).issubset({"flex", "inline-flex"})
    assert "none" not in set(metrics["visibleNameDisplays"])
    assert set(metrics["visibleNumberDisplays"]) == {"none"}
    short_meta = browser.execute_script(
        """
        const meta = document.getElementById('meta');
        meta.classList.remove('pane-info-bar-meta-overflow');
        document.getElementById('scroll-text').textContent = 'yolomux.dev8003 · ~/yolomux.dev8003 · 9 ahead · 10 dirty';
        const metaRect = meta.getBoundingClientRect();
        const zoneRect = document.querySelector('.pane-info-bar-popover-zone').getBoundingClientRect();
        return {metaRight: metaRect.right, zoneRight: zoneRect.right};
        """
    )
    assert abs(short_meta["metaRight"] - short_meta["zoneRight"]) <= 1


def test_pane_info_popover_uses_full_pane_width_not_metadata_anchor(browser, tmp_path):
    page = tmp_path / "pane-info-popover-full-width.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <article id="panel" class="panel active-pane" style="width: 900px; height: 300px;">
        <div id="info-bar" class="pane-info-bar panel-detail-row">
          <div id="zone" class="pane-info-bar-popover-zone panel-popover-zone popover-open">
            <div class="panel-session-label"><span class="session-button-dir">7001</span></div>
            <div class="pane-info-bar-meta meta">short metadata</div>
            <div id="popover" class="session-popover" role="tooltip"><div class="popover-title">Extra session information</div><div class="popover-desc">This card should use all available pane width.</div></div>
          </div>
          <div class="tmux-window-bar" data-tmux-window-bar-context="info-bar"><button type="button" class="tab tmux-window-button">0:codex</button><button type="button" class="tab tmux-window-button">1:claude</button></div>
          <div class="pane-info-bar-controls"><button type="button" class="btn-base">2/3</button></div>
          <button type="button" class="panel-detail-close"></button>
        </div>
      </article>
    """, extra_css="""
      body { margin: 0; padding: 24px; background: var(--bg); }
      .panel { overflow: hidden; }
      .pane-info-bar { box-sizing: border-box; }
    """),
    )
    metrics = browser.execute_script(
        """
        const rect = node => {
          const r = node.getBoundingClientRect();
          return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width};
        };
        const panel = document.getElementById('panel');
        const zone = document.getElementById('zone');
        const popover = document.getElementById('popover');
        const infoBar = document.getElementById('info-bar');
        const style = getComputedStyle(popover);
        return {panel: rect(panel), zone: rect(zone), popover: rect(popover), infoBar: rect(infoBar), position: style.position, visibility: style.visibility, overflow: getComputedStyle(infoBar).overflow};
        """
    )
    assert metrics["position"] == "absolute", metrics
    assert metrics["visibility"] == "visible", metrics
    assert metrics["overflow"] == "visible", metrics
    assert metrics["zone"]["width"] < metrics["panel"]["width"] * 0.85, metrics
    assert metrics["popover"]["width"] > metrics["zone"]["width"] * 1.15, metrics
    assert metrics["popover"]["left"] >= metrics["infoBar"]["left"] + 6, metrics
    assert metrics["popover"]["right"] <= metrics["infoBar"]["right"] - 6, metrics
    assert abs(metrics["popover"]["left"] - metrics["infoBar"]["left"] - 8) <= 1, metrics
    assert abs(metrics["infoBar"]["right"] - metrics["popover"]["right"] - 8) <= 1, metrics


def test_pane_control_families_share_paint_and_glyph_base(browser, tmp_path):
    page = tmp_path / "pane-control-shared-paint.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <article class="panel active-pane" style="height:auto;">
        <div class="pane-info-bar panel-detail-row">
          <button id="status-control" type="button" class="tab tmux-status-toggle">·</button>
          <div class="tmux-window-bar" data-tmux-window-bar-context="info" data-tmux-window-label-mode="names">
            <button id="window-control" type="button" class="tab tmux-window-button"><span class="tmux-window-name-label">0:bash</span></button>
          </div>
          <div class="tabs">
            <button id="frame-control" type="button" class="tab pane-minimize"></button>
            <button id="pane-close" type="button" class="tab pane-close"></button>
          </div>
          <button id="detail-close" type="button" class="panel-detail-close"></button>
          <button id="tab-close" type="button" class="pane-tab-close"></button>
        </div>
      </article>
      <article id="preferences-grid" class="preferences-panel" style="height:120px;border:0;"><div></div><div></div><div></div></article>
      <article id="debug-grid" class="js-debug-panel" style="height:120px;border:0;"><div></div><div></div><div></div></article>
      <article id="panel-grid" class="panel" style="height:120px;border:0;"><div></div><div></div><div></div></article>
      <article id="editor-grid" class="panel file-editor-panel" style="height:120px;border:0;"><div></div><div></div><div></div></article>
    """),
    )

    def paint(control_id):
        return browser.execute_script(
            """
            const style = getComputedStyle(document.getElementById(arguments[0]));
            return {color: style.color, background: style.backgroundColor, border: style.borderTopColor};
            """,
            control_id,
        )

    control_ids = ["status-control", "window-control", "frame-control"]
    rest = [paint(control_id) for control_id in control_ids]
    assert rest[0] == rest[1] == rest[2]

    focus_paints = []
    for control_id in control_ids:
        browser.execute_script("document.getElementById(arguments[0]).focus({focusVisible: true})", control_id)
        assert browser.execute_script("return document.getElementById(arguments[0]).matches(':focus-visible')", control_id) is True
        focus_paints.append(paint(control_id))
    assert focus_paints[0] == focus_paints[1] == focus_paints[2]
    assert focus_paints[0] != rest[0]

    hover_paints = []
    for control_id in control_ids:
        fast_pointer_actions(browser).move_to_element(browser.find_element("id", control_id)).perform()
        hover_paints.append(paint(control_id))
    assert hover_paints[0] == hover_paints[1] == hover_paints[2] == focus_paints[0]

    for theme in ("dark", "light"):
        browser.execute_script("document.body.classList.toggle('theme-light', arguments[0] === 'light')", theme)
        metrics = browser.execute_script(
            """
            const readGlyph = id => {
              const element = document.getElementById(id);
              const style = getComputedStyle(element, '::before');
              return {
                base: {position: style.position, width: style.width, height: style.height, radius: style.borderRadius},
                centerX: parseFloat(style.left) / element.clientWidth,
                centerY: parseFloat(style.top) / element.clientHeight,
              };
            };
            const gridIds = ['preferences-grid', 'debug-grid', 'panel-grid', 'editor-grid'];
            return {
              glyphs: ['detail-close', 'pane-close', 'tab-close'].map(readGlyph),
              grids: gridIds.map(id => getComputedStyle(document.getElementById(id)).gridTemplateRows),
            };
            """
        )
        glyphs = metrics["glyphs"]
        assert glyphs[0]["base"] == glyphs[1]["base"] == glyphs[2]["base"]
        assert glyphs[0]["base"]["width"] == "8px"
        assert abs(float(glyphs[0]["base"]["height"].removesuffix("px")) - 1.4) <= 0.02
        for glyph in glyphs:
            assert abs(glyph["centerX"] - 0.5) <= 0.01
            assert abs(glyph["centerY"] - 0.5) <= 0.01
        assert metrics["grids"][0] == metrics["grids"][1] == metrics["grids"][2] == metrics["grids"][3]


def test_persistent_active_controls_share_one_computed_paint(browser, tmp_path):
    page = tmp_path / "persistent-active-control-paint.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <button id="tab-meta" class="active"></button>
      <div class="topbar-language-menu open"><button id="language" class="topbar-language"></button></div>
      <button id="menu-icon" class="app-menu-ui-icon active"></button>
      <button id="notify" class="notify-toggle active"></button>
      <span id="session-state" class="session-state-badge session-state-yolo-approval"></span>
      <button id="preferences-search" class="preferences-search-button"></button>
      <div class="tmux-window-bar"><button id="tmux-window" class="tab tmux-window-button active"></button></div>
      <div class="session-rename-actions"><button id="rename" class="session-rename-submit"></button></div>
      <button id="info-preset" class="info-tree-preset active"></button>
      <button id="finder-toggle" class="file-explorer-hidden-toggle active"></button>
      <div class="file-editor-preview-zoom-toolbar"><button id="preview-zoom" aria-pressed="true"></button></div>
      <div class="file-editor-codemirror" style="position:static;"><div class="cm-search"><label id="search-toggle"><input type="checkbox" checked></label></div></div>
      <article id="pane-control-fixture" class="panel file-editor-panel" style="position:static;height:auto;display:block;padding:8px;">
        <div class="tabs"><button id="pane-tab-pressed" class="tab active"></button></div>
        <button id="attention-pressed" class="attention-toast-agent-button"></button>
        <div class="file-editor-mode-control"><button id="legacy-mode-pressed" class="active"></button></div>
        <button id="blame-pressed" class="file-editor-blame-panel" aria-pressed="true"></button>
        <button id="diff-expand-pressed" class="file-editor-diff-expand-panel" aria-pressed="true"></button>
        <div class="file-editor-mode-control-panel"><button id="mode-panel-pressed" class="active"></button></div>
        <button id="gutter-pressed" class="file-editor-gutter-panel active"></button>
        <button id="wrap-pressed" class="file-editor-wrap-panel" aria-pressed="true"></button>
        <button id="find-pressed" class="file-editor-find-panel" aria-pressed="true"></button>
        <button id="diff-pressed" class="file-editor-diff-panel" aria-pressed="true"></button>
        <button id="toolbar-hover" class="file-editor-gutter-panel"></button>
        <div class="file-editor-preview-find-panel" style="position:static;"><button id="preview-find-hover"></button></div>
        <span id="icon-blame" class="file-editor-icon file-editor-icon-blame"></span>
        <span id="icon-diff" class="file-editor-icon file-editor-icon-diff"></span>
        <span id="icon-eye" class="file-editor-icon file-editor-icon-eye"></span>
        <span id="icon-split" class="file-editor-icon file-editor-icon-split"></span>
        <span id="icon-theme" class="file-editor-icon file-editor-icon-theme"></span>
      </article>
    """),
    )
    metrics = browser.execute_script(
        """
        const ids = ['tab-meta', 'language', 'menu-icon', 'notify', 'session-state', 'preferences-search', 'tmux-window', 'rename', 'info-preset', 'finder-toggle', 'preview-zoom', 'search-toggle'];
        const paneIds = ['pane-tab-pressed', 'attention-pressed', 'legacy-mode-pressed', 'blame-pressed', 'diff-expand-pressed', 'mode-panel-pressed', 'gutter-pressed', 'wrap-pressed', 'find-pressed', 'diff-pressed'];
        document.getElementById('tab-meta').id = 'tabMetaToggle';
        const readPaint = element => {
          const style = getComputedStyle(element);
          return {color: style.color, background: style.backgroundColor, border: style.borderTopColor};
        };
        const readIcon = (id, pseudo) => {
          const element = document.getElementById(id);
          const style = getComputedStyle(element, pseudo);
          const transform = new DOMMatrixReadOnly(style.transform);
          const outerWidth = parseFloat(style.width) + parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth);
          const outerHeight = parseFloat(style.height) + parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
          return {
            content: style.content,
            position: style.position,
            centerX: parseFloat(style.left) / element.clientWidth,
            centerY: parseFloat(style.top) / element.clientHeight,
            translatedHalfWidth: transform.e + (outerWidth / 2),
            translatedHalfHeight: transform.f + (outerHeight / 2),
          };
        };
        const readTheme = light => {
          document.body.classList.toggle('theme-light', light);
          document.body.classList.toggle('theme-dark', !light);
          document.body.classList.toggle('editor-theme-light', light);
          const probe = document.createElement('div');
          probe.style.cssText = 'color:var(--active-control-text);background:var(--active-control-bg);border:1px solid var(--active-control-border)';
          document.body.appendChild(probe);
          const expected = readPaint(probe);
          probe.remove();
          const pane = document.getElementById('pane-control-fixture');
          const paneProbe = document.createElement('button');
          paneProbe.style.cssText = 'color:var(--pane-ctl-pressed-fg,var(--pane-tab-active-text));background:var(--pane-ctl-pressed-bg,var(--pane-tab-active-bg));border:1px solid var(--pane-ctl-pressed-border,var(--pane-tab-active-border))';
          pane.appendChild(paneProbe);
          const paneExpected = readPaint(paneProbe);
          paneProbe.remove();
          return {
            expected,
            paneExpected,
            controls: Object.fromEntries(ids.map(id => {
              const resolvedId = id === 'tab-meta' ? 'tabMetaToggle' : id;
              return [id, readPaint(document.getElementById(resolvedId))];
            })),
            paneControls: Object.fromEntries(paneIds.map(id => [id, readPaint(document.getElementById(id))])),
            icons: {
              blameBefore: readIcon('icon-blame', '::before'),
              blameAfter: readIcon('icon-blame', '::after'),
              diffBefore: readIcon('icon-diff', '::before'),
              eyeBefore: readIcon('icon-eye', '::before'),
              eyeAfter: readIcon('icon-eye', '::after'),
              splitBefore: readIcon('icon-split', '::before'),
              splitAfter: readIcon('icon-split', '::after'),
              themeBefore: readIcon('icon-theme', '::before'),
            },
          };
        };
        return {dark: readTheme(false), light: readTheme(true)};
        """
    )
    for theme in ("dark", "light"):
        expected = metrics[theme]["expected"]
        for control, paint in metrics[theme]["controls"].items():
            assert paint == expected, {"theme": theme, "control": control, "paint": paint, "expected": expected}
        for control, paint in metrics[theme]["paneControls"].items():
            assert paint == metrics[theme]["paneExpected"], {"theme": theme, "control": control, "paint": paint, "expected": metrics[theme]["paneExpected"]}
        for icon, geometry in metrics[theme]["icons"].items():
            assert geometry["content"] == '""', {"theme": theme, "icon": icon, "geometry": geometry}
            assert geometry["position"] == "absolute", {"theme": theme, "icon": icon, "geometry": geometry}
            assert abs(geometry["centerX"] - 0.5) <= 0.01, {"theme": theme, "icon": icon, "geometry": geometry}
            assert abs(geometry["centerY"] - 0.5) <= 0.01, {"theme": theme, "icon": icon, "geometry": geometry}
            assert abs(geometry["translatedHalfWidth"]) <= 0.01, {"theme": theme, "icon": icon, "geometry": geometry}
            assert abs(geometry["translatedHalfHeight"]) <= 0.01, {"theme": theme, "icon": icon, "geometry": geometry}

    for theme, light in (("dark", False), ("light", True)):
        browser.execute_script(
            "document.body.classList.toggle('theme-light', arguments[0]); document.body.classList.toggle('theme-dark', !arguments[0]); document.body.classList.toggle('editor-theme-light', arguments[0]);",
            light,
        )
        hover_paints = {}
        for control_id in ("toolbar-hover", "preview-find-hover"):
            fast_pointer_actions(browser).move_to_element(browser.find_element("id", control_id)).perform()
            hover_paints[control_id] = browser.execute_script(
                """
                const control = document.getElementById(arguments[0]);
                const pane = document.getElementById('pane-control-fixture');
                const read = element => {
                  const style = getComputedStyle(element);
                  return {color: style.color, background: style.backgroundColor, border: style.borderTopColor};
                };
                const probe = document.createElement('button');
                probe.style.cssText = 'color:var(--editor-toolbar-control-hover-fg);background:var(--editor-toolbar-control-hover-bg);border:1px solid var(--editor-toolbar-control-hover-border)';
                pane.appendChild(probe);
                const expected = read(probe);
                probe.remove();
                return {actual: read(control), expected};
                """,
                control_id,
            )
        for control, paint in hover_paints.items():
            assert paint["actual"] == paint["expected"], {"theme": theme, "control": control, **paint}


def test_branch_reload_scaffold_debug_and_search_states_share_computed_owners(browser, tmp_path):
    page = tmp_path / "shared-css-state-layout-owners.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <button id="hover-away" type="button">away</button>
      <button id="server-reload" type="button" class="server-update-banner-reload">Reload</button>
      <article class="panel" style="position:static;height:auto;width:320px;">
        <button class="pane-tab"><span id="branch-inactive" class="ci-indicator branch-indicator">MAIN</span></button>
      </article>
      <article class="panel active-pane" style="position:static;height:auto;width:320px;">
        <button class="pane-tab active"><span id="branch-active" class="ci-indicator branch-indicator">MAIN</span></button>
      </article>
      <div style="height:90px;"><section id="transcript-shell" class="transcript"><i></i><i></i><i></i></section></div>
      <div style="height:90px;"><section id="summary-shell" class="summary"><i></i><i></i><i></i></section></div>
      <section id="debug-scroll" class="js-debug-scroll" style="height:90px;"><i></i><i></i></section>
      <section id="debug-events" class="js-debug-events-view" style="height:90px;"><i></i><i></i></section>
      <label class="info-tree-search-control"><input id="info-search" value="info"></label>
      <input id="preferences-search" class="preferences-search" value="preferences">
      <input id="history-search" class="search-history-input" value="history">
    """),
    )

    metrics = browser.execute_script(
        """
        const paint = element => {
          const style = getComputedStyle(element);
          return {color: style.color, background: style.backgroundColor, border: style.borderTopColor};
        };
        const expectedBranchPaint = element => {
          const probe = document.createElement('span');
          probe.style.cssText = 'color:var(--branch-indicator-text);background:var(--branch-indicator-bg);border:1px solid var(--branch-indicator-border)';
          element.appendChild(probe);
          const result = paint(probe);
          probe.remove();
          return result;
        };
        const layout = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {display: style.display, rows: style.gridTemplateRows, gap: style.gap, height: style.height, minHeight: style.minHeight, background: style.backgroundColor};
        };
        const readTheme = light => {
          document.body.classList.toggle('theme-light', light);
          document.body.classList.toggle('theme-dark', !light);
          const inactive = document.getElementById('branch-inactive');
          const active = document.getElementById('branch-active');
          return {
            inactive: paint(inactive),
            active: paint(active),
            inactiveExpected: expectedBranchPaint(inactive),
            activeExpected: expectedBranchPaint(active),
          };
        };
        return {
          dark: readTheme(false),
          light: readTheme(true),
          transcript: layout('transcript-shell'),
          summary: layout('summary-shell'),
          debugScroll: layout('debug-scroll'),
          debugEvents: layout('debug-events'),
        };
        """
    )
    for theme in ("dark", "light"):
        assert metrics[theme]["inactive"] == metrics[theme]["inactiveExpected"], metrics[theme]
        assert metrics[theme]["active"] == metrics[theme]["activeExpected"], metrics[theme]
        assert metrics[theme]["active"] == metrics[theme]["inactive"], metrics[theme]
    assert metrics["dark"]["inactive"] != metrics["light"]["inactive"]
    assert metrics["transcript"] == metrics["summary"]
    assert metrics["debugScroll"] == metrics["debugEvents"]

    for theme, light in (("dark", False), ("light", True)):
        browser.execute_script(
            "document.body.classList.toggle('theme-light', arguments[0]); document.body.classList.toggle('theme-dark', !arguments[0]);",
            light,
        )
        fast_pointer_actions(browser).move_to_element(browser.find_element("id", "hover-away")).perform()
        rest = browser.execute_script(
            """
            const button = document.getElementById('server-reload');
            const style = getComputedStyle(button);
            const probe = document.createElement('button');
            probe.style.cssText = 'color:var(--paint-white);background:var(--danger-strong);border:1px solid var(--danger-strong-hover)';
            document.body.appendChild(probe);
            const expected = getComputedStyle(probe);
            const result = {
              actual: {color: style.color, background: style.backgroundColor, border: style.borderTopColor},
              expected: {color: expected.color, background: expected.backgroundColor, border: expected.borderTopColor},
            };
            probe.remove();
            return result;
            """
        )
        assert rest["actual"] == rest["expected"], {"theme": theme, **rest}
        fast_pointer_actions(browser).move_to_element(browser.find_element("id", "server-reload")).perform()
        hover = browser.execute_script(
            """
            const button = document.getElementById('server-reload');
            const style = getComputedStyle(button);
            const probe = document.createElement('button');
            probe.style.cssText = 'color:var(--paint-white);background:var(--danger-strong-hover);border:1px solid var(--danger-strong-border)';
            document.body.appendChild(probe);
            const expected = getComputedStyle(probe);
            const result = {
              actual: {color: style.color, background: style.backgroundColor, border: style.borderTopColor},
              expected: {color: expected.color, background: expected.backgroundColor, border: expected.borderTopColor},
              hovered: button.matches(':hover'),
            };
            probe.remove();
            return result;
            """
        )
        assert hover["hovered"] is True
        assert hover["actual"] == hover["expected"], {"theme": theme, **hover}

        focus_paints = []
        for control_id in ("info-search", "preferences-search", "history-search"):
            browser.execute_script("document.getElementById(arguments[0]).focus()", control_id)
            focus_paints.append(
                browser.execute_script(
                    """
                    const control = document.getElementById(arguments[0]);
                    const style = getComputedStyle(control);
                    return {border: style.borderTopColor, shadow: style.boxShadow, outline: style.outlineStyle};
                    """,
                    control_id,
                )
            )
        assert focus_paints[0] == focus_paints[1], {"theme": theme, "focus": focus_paints}


def test_ellipsis_and_disabled_control_families_share_computed_state(browser, tmp_path):
    page = tmp_path / "shared-ellipsis-disabled-state.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <span id="share-banner" class="share-viewer-banner-text"></span>
      <span id="menu-setting" class="app-menu-setting-label"></span>
      <span id="status-label" class="status-indicator--label"></span>
      <span id="diff-description" class="diff-ref-suggestion-description"></span>
      <span id="preferences-title" class="preferences-section-title"></span>
      <div id="client-perf" class="js-debug-client-perf-row"></div>
      <span id="chart-summary" class="js-debug-chart-summary"></span>
      <div class="js-debug-x-axis"><span id="x-axis"></span></div>
      <div id="search-title" class="search-history-row-title"></div>
      <div id="search-meta" class="search-history-row-meta"></div>
      <div id="recent-paths" class="yoagent-recent-agent-paths"></div>
      <div id="transcript-value" class="yoagent-transcript-value"></div>
      <div id="compact-label" class="yoagent-compact-label"></div>
      <div id="job-meta" class="yoagent-job-meta"></div>
      <div id="job-text" class="yoagent-job-text"></div>
      <div id="info-label-line" class="info-tree-group-label-line"></div>
      <div id="info-label" class="info-tree-group-label"></div>
      <button class="tmux-window-button"><span id="tmux-window-text" class="tmux-window-name-text"></span></button>
      <span id="editor-title" class="file-editor-title-name"></span>
      <div class="file-tree-row tabber-row"><span id="tabber-window-text" class="tabber-window-text"></span></div>
      <div class="share-entry-heading"><strong id="share-heading"></strong></div>
      <div class="share-url-primary-head"><span id="share-url"></span></div>
      <div class="share-users-row"><span id="share-user"></span></div>
      <div id="terminal-drop-row" class="terminal-drop-suggestion" style="width:130px;"><span class="terminal-drop-suggestion-combo">1</span><span id="terminal-drop-label" class="terminal-drop-suggestion-label">Insert this deliberately long terminal drop action label</span></div>
      <div class="terminal-context-menu"><button id="context-disabled" disabled></button></div>
      <div class="file-editor-preview-zoom-toolbar"><button id="zoom-disabled" disabled></button></div>
      <button id="find-disabled" class="file-editor-find" disabled></button>
      <div class="file-editor-preview-font-panel"><button id="font-disabled" disabled></button></div>
      <button id="find-panel-disabled" class="file-editor-find-panel" disabled></button>
      <button id="info-group-action" class="info-tree-group-label-action"></button>
      <button id="info-leaf-action" class="info-tree-action-link"></button>
      <span id="session-agent-cluster" class="agent-window-activity agent-window-activity--subwindow"><span class="agent-icon"><svg id="subwindow-agent-svg"></svg></span></span>
      <span id="changes-agent-cluster" class="changes-file-agent"><span class="agent-icon"><svg id="changes-agent-svg"></svg></span></span>
      <span id="file-agent-cluster" class="file-tree-agent"></span>
    """),
    )
    metrics = browser.execute_script(
        """
        const ellipsisIds = ['share-banner', 'menu-setting', 'status-label', 'diff-description', 'preferences-title', 'client-perf', 'chart-summary', 'x-axis', 'tmux-window-text', 'search-title', 'search-meta', 'recent-paths', 'transcript-value', 'compact-label', 'job-meta', 'job-text', 'info-label-line', 'info-label', 'editor-title', 'tabber-window-text', 'share-heading', 'share-url', 'share-user', 'terminal-drop-label'];
        const disabledIds = ['context-disabled', 'zoom-disabled', 'find-disabled', 'font-disabled', 'find-panel-disabled'];
        return {
          ellipsis: Object.fromEntries(ellipsisIds.map(id => {
            const style = getComputedStyle(document.getElementById(id));
            return [id, {minWidth: style.minWidth, overflow: style.overflow, textOverflow: style.textOverflow, whiteSpace: style.whiteSpace}];
          })),
          disabled: Object.fromEntries(disabledIds.map(id => {
            const style = getComputedStyle(document.getElementById(id));
            return [id, {opacity: style.opacity, cursor: style.cursor}];
          })),
          textActions: Object.fromEntries(['info-group-action', 'info-leaf-action'].map(id => {
            const style = getComputedStyle(document.getElementById(id));
            return [id, {padding: style.padding, borderWidth: style.borderTopWidth, borderStyle: style.borderTopStyle, background: style.backgroundColor, textAlign: style.textAlign, fontFamily: style.fontFamily, fontSize: style.fontSize, fontWeight: style.fontWeight, lineHeight: style.lineHeight, cursor: style.cursor}];
          })),
          agentClusters: Object.fromEntries(['session-agent-cluster', 'changes-agent-cluster', 'file-agent-cluster'].map(id => {
            const style = getComputedStyle(document.getElementById(id));
            return [id, {display: style.display, alignItems: style.alignItems, gap: style.gap, flexGrow: style.flexGrow, flexShrink: style.flexShrink, flexBasis: style.flexBasis, verticalAlign: style.verticalAlign}];
          })),
          agentSvgGeometry: Object.fromEntries(['subwindow-agent-svg', 'changes-agent-svg'].map(id => {
            const style = getComputedStyle(document.getElementById(id));
            return [id, {width: style.width, height: style.height, flexBasis: style.flexBasis}];
          })),
          terminalDropGeometry: (() => {
            const row = document.getElementById('terminal-drop-row').getBoundingClientRect();
            const label = document.getElementById('terminal-drop-label');
            const rect = label.getBoundingClientRect();
            return {rowRight: row.right, labelRight: rect.right, clientWidth: label.clientWidth, scrollWidth: label.scrollWidth};
          })(),
        };
        """
    )
    expected_ellipsis = {"minWidth": "0px", "overflow": "hidden", "textOverflow": "ellipsis", "whiteSpace": "nowrap"}
    expected_disabled = {"opacity": "0.42", "cursor": "default"}
    for control, state in metrics["ellipsis"].items():
        assert state == expected_ellipsis, {"control": control, "state": state}
    for control, state in metrics["disabled"].items():
        assert state == expected_disabled, {"control": control, "state": state}
    expected_text_action = {"padding": "0px", "borderWidth": "0px", "borderStyle": "none", "background": "rgba(0, 0, 0, 0)", "textAlign": "left", "fontFamily": metrics["textActions"]["info-group-action"]["fontFamily"], "fontSize": metrics["textActions"]["info-group-action"]["fontSize"], "fontWeight": metrics["textActions"]["info-group-action"]["fontWeight"], "lineHeight": metrics["textActions"]["info-group-action"]["lineHeight"], "cursor": "pointer"}
    for control, state in metrics["textActions"].items():
        assert state == expected_text_action, {"control": control, "state": state}
    expected_agent_cluster = {"display": "inline-flex", "alignItems": "center", "gap": "2px", "flexGrow": "0", "flexShrink": "0", "flexBasis": "auto", "verticalAlign": "middle"}
    for cluster, state in metrics["agentClusters"].items():
        assert state == expected_agent_cluster, {"cluster": cluster, "state": state}
    expected_agent_svg = {"width": "14px", "height": "14px", "flexBasis": "14px"}
    for icon, geometry in metrics["agentSvgGeometry"].items():
        assert geometry == expected_agent_svg, {"icon": icon, "geometry": geometry}
    assert metrics["terminalDropGeometry"]["labelRight"] <= metrics["terminalDropGeometry"]["rowRight"] + 1
    assert metrics["terminalDropGeometry"]["scrollWidth"] > metrics["terminalDropGeometry"]["clientWidth"]


def test_danger_status_and_light_panel_surface_paint_have_shared_computed_owners(browser, tmp_path):
    page = tmp_path / "danger-status-light-panel-paint.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <span id="approval" class="session-state-badge session-state-needs-approval"></span>
      <span id="input" class="session-state-badge session-state-needs-input"></span>
      <span id="failing" class="ci-indicator pr-status-failing"></span>
      <span id="changes" class="ci-indicator pr-review-changes"></span>
      <span id="pr-number" class="ci-indicator pr-number-chip">#86</span>
      <section id="changes-toolbar" class="changes-toolbar"></section>
      <section id="changes-repo" class="changes-repo-group"></section>
      <section id="comparison" class="changes-comparison-head"></section>
      <section id="debug-stat" class="js-debug-stat"></section>
      <section id="preferences-section" class="preferences-section"></section>
      <section id="search-result" class="search-history-result"></section>
      <section id="mermaid-preview" class="mermaid-preview"></section>
      <section id="zoom-viewport" class="file-editor-preview-zoom-viewport"></section>
      <button id="default-button">default</button>
      <button id="share-control" class="share-view-fit-toggle">fit</button>
      <label class="info-tree-search-control"><input id="info-search-control"></label>
      <section id="terminal-surface" class="terminal-drop-suggestions"></section>
      <section id="preview-surface" class="file-editor-preview-fallback"></section>
      <button id="standalone-refresh" class="changes-refresh">refresh</button>
      <section id="file-explorer" class="file-explorer"></section>
      <section id="file-tree-col" class="file-explorer-tree-col"></section>
      <section id="finder-pane" class="file-explorer-pane"></section>
      <section id="finder-tree" class="file-explorer-tree-panel"></section>
      <section id="yoagent-chat" class="yoagent-chat"></section>
      <article id="yoagent-message" class="conversation-message yoagent-message"></article>
      <article id="yoagent-assistant" class="conversation-message yoagent-message assistant"></article>
      <article id="yoagent-result" class="conversation-message yoagent-message assistant yoagent-agent-result"></article>
      <article id="yoagent-user" class="conversation-message yoagent-message user"></article>
      <div class="file-editor-codemirror"><div class="cm-search"><button id="cm-close" class="cm-dialog-close">×</button></div></div>
      <section class="file-explorer-changes-panel">
        <section id="embedded-comparison" class="changes-comparison-head"></section>
        <button id="embedded-refresh" class="changes-refresh">refresh</button>
      </section>
    """),
    )
    metrics = browser.execute_script(
        """
        const paint = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {color: style.color, background: style.backgroundColor, border: style.borderTopColor};
        };
        const frame = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {background: style.backgroundColor, border: style.borderTop, radius: style.borderRadius};
        };
        const controlShell = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {color: style.color, border: style.borderTop, radius: style.borderRadius};
        };
        const secondarySurface = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {color: style.color, background: style.backgroundColor, border: style.borderTop};
        };
        const sharedOwnerPaint = () => {
          const controlProbe = document.createElement('div');
          controlProbe.style.cssText = 'color:var(--text);border:1px solid var(--line);border-radius:var(--radius-control)';
          document.body.appendChild(controlProbe);
          controlProbe.id = 'control-shell-probe';
          const expectedControl = controlShell('control-shell-probe');
          controlProbe.remove();
          const surfaceProbe = document.createElement('div');
          surfaceProbe.style.cssText = 'color:var(--text);background:var(--panel2);border:1px solid var(--line)';
          document.body.appendChild(surfaceProbe);
          surfaceProbe.id = 'secondary-surface-probe';
          const expectedSurface = secondarySurface('secondary-surface-probe');
          surfaceProbe.remove();
          return {
            controls: Object.fromEntries(['share-control', 'info-search-control'].map(id => [id, controlShell(id)])),
            expectedControl,
            surfaces: Object.fromEntries(['default-button', 'terminal-surface', 'preview-surface'].map(id => [id, secondarySurface(id)])),
            expectedSurface,
          };
        };
        const probePaint = cssText => {
          const probe = document.createElement('div');
          probe.style.cssText = cssText;
          document.body.appendChild(probe);
          const result = {color: getComputedStyle(probe).color, background: getComputedStyle(probe).backgroundColor, border: getComputedStyle(probe).borderTopColor};
          probe.remove();
          return result;
        };
        const statusIds = ['approval', 'input', 'failing', 'changes'];
        document.documentElement.classList.add('status-pulse-disabled');
        document.body.classList.add('theme-dark');
        document.body.classList.remove('editor-theme-light');
        const darkStatuses = Object.fromEntries(statusIds.map(id => [id, paint(id)]));
        const expectedDangerDark = probePaint('color:var(--danger-text);background:var(--danger-bg);border:1px solid var(--danger-border)');
        const darkSharedOwners = sharedOwnerPaint();
        const frameIds = ['debug-stat', 'preferences-section', 'search-result', 'mermaid-preview', 'zoom-viewport'];
        const darkFrames = Object.fromEntries(frameIds.map(id => [id, frame(id)]));
        const darkFrameProbe = document.createElement('div');
        darkFrameProbe.style.cssText = 'background:var(--panel);border:1px solid var(--line);border-radius:var(--radius-md)';
        document.body.appendChild(darkFrameProbe);
        darkFrameProbe.id = 'dark-frame-probe';
        const expectedDarkFrame = frame('dark-frame-probe');
        darkFrameProbe.remove();
        const darkOwnedPaint = {
          finder: ['finder-pane', 'finder-tree'].map(paint),
          yoagent: ['yoagent-chat', 'yoagent-message', 'yoagent-assistant', 'yoagent-result', 'yoagent-user'].map(paint),
        };
        document.body.classList.remove('theme-dark');
        document.body.classList.add('theme-light');
        document.body.classList.add('editor-theme-light');
        const lightStatuses = Object.fromEntries(statusIds.map(id => [id, paint(id)]));
        const expectedDangerLight = probePaint('color:var(--danger-text);background:var(--danger-bg);border:1px solid var(--danger-border)');
        const lightSharedOwners = sharedOwnerPaint();
        const lightFrames = Object.fromEntries(frameIds.map(id => [id, frame(id)]));
        const lightFrameProbe = document.createElement('div');
        lightFrameProbe.style.cssText = 'background:var(--panel);border:1px solid var(--line);border-radius:var(--radius-md)';
        document.body.appendChild(lightFrameProbe);
        lightFrameProbe.id = 'light-frame-probe';
        const expectedLightFrame = frame('light-frame-probe');
        lightFrameProbe.remove();
        const surfaceIds = ['changes-toolbar', 'changes-repo', 'comparison', 'file-explorer', 'file-tree-col'];
        const lightSurfaces = Object.fromEntries(surfaceIds.map(id => [id, paint(id)]));
        const expectedSurface = probePaint('color:var(--text);background:var(--panel);border:1px solid var(--line)');
        const refreshes = {standalone: paint('standalone-refresh'), embedded: paint('embedded-refresh')};
        const expectedRefreshes = {
          standalone: probePaint('color:var(--text);background:var(--paint-white);border:1px solid var(--line)'),
          embedded: probePaint('color:var(--muted);background:transparent;border:1px solid var(--line)'),
        };
        const lightOwnedPaint = {
          prNumber: paint('pr-number'),
          expectedPrNumber: probePaint('color:var(--paint-white);background:var(--pr-number-chip-bg);border:1px solid var(--pr-number-chip-bg)'),
          finder: ['finder-pane', 'finder-tree'].map(paint),
          expectedFinder: probePaint('color:var(--text);background:var(--panel);border:1px solid var(--line)'),
          yoagent: ['yoagent-chat', 'yoagent-message'].map(paint),
          expectedYoagent: probePaint('color:var(--pc-control-hover-fg);background:var(--panel);border:1px solid var(--line)'),
          assistant: paint('yoagent-assistant'),
          expectedAssistant: probePaint('color:var(--pc-control-hover-fg);background:color-mix(in srgb,var(--active-control-soft-bg) 78%,var(--paint-white));border:1px solid var(--active-control-border)'),
          result: paint('yoagent-result'),
          expectedResult: probePaint('color:var(--pc-control-hover-fg);background:color-mix(in srgb,var(--accent-gold) 10%,var(--paint-white));border:1px solid var(--active-control-border)'),
          resultStartBorder: getComputedStyle(document.getElementById('yoagent-result')).borderInlineStartColor,
          expectedResultStartBorder: probePaint('border:1px solid var(--accent-gold)').border,
          user: paint('yoagent-user'),
          expectedUser: probePaint('color:var(--pc-control-hover-fg);background:var(--panel);border:1px solid var(--link-soft)'),
          userEndBorder: getComputedStyle(document.getElementById('yoagent-user')).borderInlineEndColor,
          expectedUserEndBorder: probePaint('border:1px solid var(--link-soft)').border,
          close: {color: getComputedStyle(document.getElementById('cm-close')).color, background: getComputedStyle(document.getElementById('cm-close')).backgroundColor},
          expectedClose: {color: probePaint('color:var(--lt-text)').color, background: 'rgba(0, 0, 0, 0)'},
        };
        return {
          darkStatuses,
          darkSharedOwners,
          darkFrames,
          expectedDarkFrame,
          darkOwnedPaint,
          lightStatuses,
          lightSharedOwners,
          lightFrames,
          expectedLightFrame,
          expectedDangerDark,
          expectedDangerLight,
          lightSurfaces,
          expectedSurface,
          embeddedComparison: {...paint('embedded-comparison'), borderWidth: getComputedStyle(document.getElementById('embedded-comparison')).borderTopWidth},
          refreshes,
          expectedRefreshes,
          lightOwnedPaint,
        };
        """
    )
    for status, paint in metrics["darkStatuses"].items():
        assert paint == metrics["expectedDangerDark"], {"theme": "dark", "status": status, "paint": paint}
    for theme in ("dark", "light"):
        owners = metrics[f"{theme}SharedOwners"]
        for control, shell in owners["controls"].items():
            assert shell == owners["expectedControl"], {"theme": theme, "control": control, "shell": shell, "expected": owners["expectedControl"]}
        for surface, paint in owners["surfaces"].items():
            assert paint == owners["expectedSurface"], {"theme": theme, "surface": surface, "paint": paint, "expected": owners["expectedSurface"]}
    for theme in ("dark", "light"):
        for surface, frame in metrics[f"{theme}Frames"].items():
            assert frame == metrics[f"expected{theme.title()}Frame"], {"theme": theme, "surface": surface, "frame": frame}
    for status in ("approval", "input"):
        assert metrics["lightStatuses"][status] == metrics["expectedDangerLight"], {"theme": "light", "status": status, "paint": metrics["lightStatuses"][status]}
    assert metrics["lightStatuses"]["failing"] == metrics["lightStatuses"]["changes"]
    assert metrics["lightStatuses"]["failing"] != metrics["expectedDangerLight"]
    for surface, paint in metrics["lightSurfaces"].items():
        assert paint == metrics["expectedSurface"], {"surface": surface, "paint": paint, "expected": metrics["expectedSurface"]}
    assert metrics["embeddedComparison"]["background"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["embeddedComparison"]["borderWidth"] == "0px", metrics
    assert metrics["refreshes"] == metrics["expectedRefreshes"], metrics
    assert all(paint["background"] != "rgba(0, 0, 0, 0)" for paint in metrics["darkOwnedPaint"]["yoagent"][:4]), metrics
    owned = metrics["lightOwnedPaint"]
    assert owned["prNumber"] == owned["expectedPrNumber"], owned
    assert all(paint["color"] == owned["expectedFinder"]["color"] and paint["background"] == owned["expectedFinder"]["background"] for paint in owned["finder"]), owned
    assert all(paint == owned["expectedYoagent"] for paint in owned["yoagent"]), owned
    assert owned["assistant"] == owned["expectedAssistant"], owned
    assert owned["result"] == owned["expectedResult"], owned
    assert owned["resultStartBorder"] == owned["expectedResultStartBorder"], owned
    assert owned["user"] == owned["expectedUser"], owned
    assert owned["userEndBorder"] == owned["expectedUserEndBorder"], owned
    assert owned["close"] == owned["expectedClose"], owned


def test_inactive_markers_and_vanilla_code_surfaces_share_computed_paint(browser, tmp_path):
    page = tmp_path / "inactive-marker-vanilla-code-paint.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <span id="session-marker" class="session-yolo-marker inactive"></span>
      <button class="pane-tab active"><span id="pane-marker" class="session-yolo-marker inactive"></span></button>
      <div class="file-editor-preview-pane vanilla-preview-body"><code id="pane-code"></code><pre id="pane-pre"></pre></div>
      <div class="file-editor-preview-pane-panel vanilla-preview-body"><code id="popout-code"></code><pre id="popout-pre"></pre></div>
      <div class="file-editor-content"><div class="markdown-body vanilla-preview-body"><code id="editor-code"></code><pre id="editor-pre"></pre></div></div>
    """),
    )
    metrics = browser.execute_script(
        """
        const paint = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {color: style.color, background: style.backgroundColor, border: style.borderTopColor};
        };
        const probePaint = cssText => {
          const probe = document.createElement('div');
          probe.style.cssText = cssText;
          document.body.appendChild(probe);
          const value = {color: getComputedStyle(probe).color, background: getComputedStyle(probe).backgroundColor, border: getComputedStyle(probe).borderTopColor};
          probe.remove();
          return value;
        };
        const markerTheme = light => {
          document.body.classList.toggle('theme-light', light);
          document.body.classList.toggle('theme-dark', !light);
          return {
            expected: probePaint('color:var(--agent-inactive-marker-text);background:var(--agent-inactive-marker-bg);border:1px solid var(--agent-inactive-marker-border)'),
            session: paint('session-marker'),
            pane: paint('pane-marker'),
          };
        };
        const markers = {dark: markerTheme(false), light: markerTheme(true)};
        document.body.classList.add('editor-theme-light');
        const vanilla = {
          expected: probePaint('color:var(--markdown-html-light-text);background:var(--lt-panel);border:1px solid var(--lt-line)'),
          surfaces: Object.fromEntries(['pane-code', 'pane-pre', 'popout-code', 'popout-pre', 'editor-code', 'editor-pre'].map(id => [id, paint(id)])),
        };
        return {markers, vanilla};
        """
    )
    for theme, state in metrics["markers"].items():
        assert state["session"] == state["expected"], {"theme": theme, **state}
        assert state["pane"] == state["expected"], {"theme": theme, **state}
    for surface, paint in metrics["vanilla"]["surfaces"].items():
        assert paint == metrics["vanilla"]["expected"], {"surface": surface, "paint": paint, "expected": metrics["vanilla"]["expected"]}


def test_code_surfaces_and_audited_css_families_share_computed_owners(browser, tmp_path):
    page = tmp_path / "code-header-tab-text-owners.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <div class="file-editor-content">
        <div class="markdown-body">
          <code id="markdown-inline">inline</code>
          <blockquote class="markdown-alert markdown-alert-warning"><code id="alert-inline">alert</code></blockquote>
          <table bgcolor="#ffffff"><tr><td><code id="table-inline">table</code></td></tr></table>
        </div>
        <div class="file-editor-raw-panel"><span id="source-inline" class="md-code">source</span></div>
      </div>
      <div class="file-editor-preview-pane vanilla-preview-body"><pre><code id="pane-nested-code"><span id="pane-nested-child">pane</span></code></pre></div>
      <div class="file-editor-content"><div class="markdown-body vanilla-preview-body"><pre><code id="editor-nested-code"><span id="editor-nested-child">editor</span></code></pre></div></div>
      <div id="action-code-block" class="yoagent-action-text">action</div>
      <div class="yoagent-chat"><div class="markdown-body"><pre id="chat-code-block">chat</pre></div></div>
      <div id="preview-overlay" class="file-editor-preview-pane" style="position:static;"></div>
      <div id="preview-panel" class="file-editor-preview-pane-panel"></div>
      <button id="chat-send" class="conversation-send conversation-send-primary yoagent-chat-send"></button>
      <button id="chat-stop" class="yoagent-chat-stop"></button>
      <div id="explorer-title" class="file-explorer-title">Finder</div>
      <div id="explorer-panel-title" class="file-explorer-panel-title">Differ</div>
      <div id="editor-title" class="file-editor-title">Editor</div>
      <span id="summary-totals" class="changes-summary-totals"></span>
      <span id="repo-totals" class="changes-repo-totals"></span>
      <div id="shortcut-row" class="keyboard-shortcut-row"></div>
      <div id="legend-row" class="keyboard-legend-row"></div>
      <div id="finder-head" class="file-explorer-head">Finder</div>
      <div class="file-explorer-changes-panel"><div id="changes-head" class="file-explorer-changes-head">Modified files</div></div>
      <button class="pane-tab file-editor-item"><span id="file-tab-text" class="session-button-dir">file.py</span></button>
      <button class="pane-tab"><span id="detail-tab-text" class="session-button-dir tab-inline-detail">detail</span></button>
    """),
    )
    metrics = browser.execute_script(
        """
        const codePaint = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {color: style.color, background: style.backgroundColor, border: style.borderTopColor, radius: style.borderRadius};
        };
        const headerPaint = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {color: style.color, background: style.backgroundColor, border: style.borderBottomColor};
        };
        const resetPaint = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {color: style.color, background: style.backgroundColor, border: style.borderTopColor};
        };
        const inlineTheme = light => {
          document.body.classList.toggle('theme-light', light);
          document.body.classList.toggle('theme-dark', !light);
          document.body.classList.toggle('editor-theme-light', light);
          return {
            markdown: codePaint('markdown-inline'),
            source: codePaint('source-inline'),
            alert: codePaint('alert-inline'),
            table: codePaint('table-inline'),
            finder: headerPaint('finder-head'),
            changes: headerPaint('changes-head'),
          };
        };
        const themes = {dark: inlineTheme(false), light: inlineTheme(true)};
        const nested = Object.fromEntries(['pane-nested-code', 'pane-nested-child', 'editor-nested-code', 'editor-nested-child'].map(id => [id, resetPaint(id)]));
        const codeBlocks = {action: resetPaint('action-code-block'), chat: resetPaint('chat-code-block')};
        const flex = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {grow: style.flexGrow, shrink: style.flexShrink, basis: style.flexBasis, minWidth: style.minWidth, maxWidth: style.maxWidth};
        };
        const family = (ids, properties) => Object.fromEntries(ids.map(id => {
          const style = getComputedStyle(document.getElementById(id));
          return [id, Object.fromEntries(properties.map(property => [property, style[property]]))];
        }));
        return {
          themes,
          nested,
          codeBlocks,
          flex: {file: flex('file-tab-text'), detail: flex('detail-tab-text')},
          sharedFamilies: {
            preview: family(['preview-overlay', 'preview-panel'], ['zIndex', 'overflow', 'padding', 'backgroundColor', 'color', 'fontFamily', 'fontSize', 'lineHeight']),
            chat: family(['chat-send', 'chat-stop'], ['display', 'alignItems', 'justifyContent', 'width', 'height', 'flexGrow', 'flexShrink', 'flexBasis', 'padding', 'borderRadius', 'cursor']),
            title: family(['explorer-title', 'explorer-panel-title', 'editor-title'], ['color', 'fontFamily', 'fontSize', 'fontWeight', 'whiteSpace']),
            totals: family(['summary-totals', 'repo-totals'], ['display', 'alignItems', 'gap', 'flexGrow', 'flexShrink', 'flexBasis', 'whiteSpace']),
            keyboard: family(['shortcut-row', 'legend-row'], ['display', 'gap', 'alignItems', 'padding', 'borderBottom', 'fontFamily', 'fontSize', 'lineHeight']),
          },
        };
        """
    )
    for theme, state in metrics["themes"].items():
        assert state["markdown"] == state["source"], {"theme": theme, **state}
        assert state["alert"] == state["table"], {"theme": theme, **state}
        assert state["finder"] == state["changes"], {"theme": theme, **state}
    assert metrics["themes"]["dark"]["markdown"] != metrics["themes"]["light"]["markdown"]
    assert metrics["codeBlocks"]["action"] == metrics["codeBlocks"]["chat"]
    nested_values = list(metrics["nested"].values())
    assert all(value == nested_values[0] for value in nested_values), metrics["nested"]
    assert nested_values[0]["background"] == "rgba(0, 0, 0, 0)"
    assert nested_values[0]["border"] == "rgba(0, 0, 0, 0)"
    assert metrics["flex"]["file"] == metrics["flex"]["detail"] == {"grow": "1", "shrink": "1", "basis": "auto", "minWidth": "0px", "maxWidth": "none"}
    for family, members in metrics["sharedFamilies"].items():
        values = list(members.values())
        assert all(value == values[0] for value in values), {"family": family, "members": members}


def test_syntax_token_colors_share_one_owner_across_renderers(browser, tmp_path):
    cases = [
        ("keyword", "keyword", "keyword"),
        ("string", "string", "string"),
        ("comment", "comment", "comment"),
        ("number", "number", "number"),
        ("atom", "meta", "constant"),
        ("function", "title", "function"),
        ("type", "type", "type"),
        ("variable", "variable", "variable"),
        ("property", "property", "property"),
        ("tag", "tag", "tag"),
        ("invalid", "deletion", "invalid"),
    ]
    hljs = "".join(f'<span id="hljs-{token}" class="hljs-{hljs_class}">{token}</span>' for token, hljs_class, _code_class in cases)
    markdown = "".join(f'<span id="markdown-{token}" class="code-{code_class}">{token}</span>' for token, _hljs_class, code_class in cases)
    codemirror = "".join(f'<span id="codemirror-{token}" class="code-{code_class}">{token}</span>' for token, _hljs_class, code_class in cases)
    page = tmp_path / "shared-syntax-token-colors.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(
            f'<div class="markdown-body"><pre><code>{hljs}{markdown}<span id="markdown-control" class="code-control">control</span></code></pre></div>'
            f'<div class="cm-content">{codemirror}<span id="codemirror-control" class="code-control">control</span></div>'
        ),
    )
    metrics = browser.execute_script(
        """
        const tokens = arguments[0];
        const read = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {color: style.color, weight: style.fontWeight};
        };
        return Object.fromEntries(tokens.map(token => [token, {
          hljs: token === 'control' ? null : read(`hljs-${token}`),
          markdown: read(`markdown-${token}`),
          codemirror: read(`codemirror-${token}`),
        }]));
        """,
        [token for token, _hljs_class, _code_class in cases] + ["control"],
    )
    for token, renderer_values in metrics.items():
        colors = {value["color"] for value in renderer_values.values() if value is not None}
        assert len(colors) == 1, {"token": token, **renderer_values}
    assert metrics["function"]["markdown"]["weight"] == metrics["function"]["codemirror"]["weight"] == "700"
    assert metrics["keyword"]["markdown"]["weight"] != metrics["keyword"]["codemirror"]["weight"]
    assert metrics["control"]["markdown"]["weight"] != metrics["control"]["codemirror"]["weight"]


def test_shared_link_drag_and_action_css_families_compute_from_one_owner(browser, tmp_path):
    page = tmp_path / "shared-link-drag-action-owners.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <button id="checked-menu" class="app-menu-command" data-checked="true">checked</button>
      <div id="changes-panel" class="file-explorer-changes-panel"><div id="changes-toolbar" class="changes-toolbar"><button id="changes-focus">focus</button><button id="changes-refresh" class="changes-refresh">refresh</button></div></div>
      <div class="file-editor-preview-pane vanilla-preview-body" style="position:static;"><h2 id="pane-heading">Pane heading</h2><strong id="pane-strong">Pane strong</strong></div>
      <div class="file-editor-content"><div class="markdown-body vanilla-preview-body"><h2 id="editor-heading">Editor heading</h2><strong id="editor-strong">Editor strong</strong></div></div>
      <div class="popover-value"><a id="popover-link" href="#">popover</a><a id="popover-merged" class="pr-status-merged" href="#">merged</a></div>
      <a id="branch-link" class="branch-link" href="#">branch</a>
      <div class="branch-meta"><a id="branch-merged" class="pr-status-merged" href="#">merged</a></div>
      <div class="meta"><a id="meta-link" href="#">meta</a><a id="meta-merged" class="pr-status-merged" href="#">merged</a></div>
      <div class="summary-context"><a id="summary-link" href="#">summary</a><a id="summary-merged" class="pr-status-merged" href="#">merged</a></div>
      <button id="info-link" class="info-tree-group-label-action">info</button>
      <div id="terminal-drag" class="terminal path-drag-over" style="position:static;width:40px;height:20px;"></div><div id="panel-drag" class="panel path-drag-over" style="position:static;width:40px;height:20px;"></div>
      <div class="file-explorer-panel" style="position:static;display:block;">
        <select id="sort-action" class="file-explorer-sort-select"><option>A-Z</option></select>
        <button id="hidden-action" class="file-explorer-hidden-toggle file-explorer-hidden-toggle-panel">hidden</button>
        <button id="header-action" class="file-explorer-header-action">header</button>
        <button id="quick-action" class="file-explorer-quick-access-button">quick</button>
      </div>
      <button id="compact-action" class="yoagent-compact-action">compact</button>
      <button id="confirm-action" class="yoagent-job-confirm">confirm</button>
      <button id="cancel-action" class="yoagent-job-cancel">cancel</button>
    """),
    )
    metrics = browser.execute_script(
        """
        const paint = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {color: style.color, background: style.backgroundColor, border: style.borderTopColor};
        };
        const outline = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {style: style.outlineStyle, width: style.outlineWidth, color: style.outlineColor, offset: style.outlineOffset};
        };
        const linkPaint = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {color: style.color, decoration: style.textDecorationLine};
        };
        const focusLinkPaint = id => {
          const node = document.getElementById(id);
          node.focus({focusVisible: true});
          return {...linkPaint(id), focusVisible: node.matches(':focus-visible')};
        };
        const themed = light => {
          document.body.classList.toggle('theme-light', light);
          document.body.classList.toggle('theme-dark', !light);
          document.body.classList.toggle('editor-theme-light', light);
          document.getElementById('changes-focus').focus({focusVisible: true});
          const genericLinkIds = ['popover-link', 'meta-link', 'summary-link'];
          const mergedLinkIds = ['popover-merged', 'branch-merged', 'meta-merged', 'summary-merged'];
          const value = {
            menu: paint('checked-menu'),
            toolbar: paint('changes-toolbar'),
            headings: [paint('pane-heading'), paint('editor-heading')],
            strong: [paint('pane-strong'), paint('editor-strong')],
            genericLinks: genericLinkIds.map(linkPaint),
            mergedLinks: mergedLinkIds.map(linkPaint),
          };
          value.focusedGenericLinks = genericLinkIds.map(focusLinkPaint);
          value.focusedMergedLinks = mergedLinkIds.map(focusLinkPaint);
          return value;
        };
        const themes = {dark: themed(false), light: themed(true)};
        const finderShell = id => {
          const style = getComputedStyle(document.getElementById(id));
          return {
            height: style.height,
            border: style.borderTop,
            radius: style.borderRadius,
            color: style.color,
            background: style.backgroundColor,
            family: style.fontFamily,
            size: style.fontSize,
            weight: style.fontWeight,
          };
        };
        const finderShellIds = ['sort-action', 'hidden-action', 'header-action', 'quick-action'];
        const finderShells = {};
        for (const [theme, light] of [['dark', false], ['light', true]]) {
          document.body.classList.toggle('theme-light', light);
          document.body.classList.toggle('theme-dark', !light);
          finderShells[theme] = finderShellIds.map(finderShell);
        }
        const focus = id => {
          const node = document.getElementById(id);
          node.focus({focusVisible: true});
          const style = getComputedStyle(node);
          return {...paint(id), outline: style.outlineStyle, decoration: style.textDecorationLine, focusVisible: node.matches(':focus-visible')};
        };
        return {
          themes,
          branchRest: linkPaint('branch-link'),
          infoLink: focus('info-link'),
          drag: [outline('terminal-drag'), outline('panel-drag')],
          finderShells,
          finderActions: ['sort-action', 'hidden-action', 'header-action', 'changes-refresh'].map(focus),
          yoagentActions: ['compact-action', 'confirm-action', 'cancel-action'].map(focus),
        };
        """
    )
    assert metrics["themes"]["light"]["headings"][0] == metrics["themes"]["light"]["headings"][1], metrics["themes"]["light"]
    assert metrics["themes"]["light"]["strong"][0] == metrics["themes"]["light"]["strong"][1], metrics["themes"]["light"]
    assert metrics["themes"]["dark"]["menu"] != metrics["themes"]["light"]["menu"]
    assert metrics["themes"]["dark"]["toolbar"] != metrics["themes"]["light"]["toolbar"]
    for theme, values in metrics["themes"].items():
        assert all(value == values["genericLinks"][0] for value in values["genericLinks"]), {theme: values["genericLinks"]}
        assert all(value["color"] == values["mergedLinks"][0]["color"] for value in values["mergedLinks"]), {theme: values["mergedLinks"]}
        assert values["mergedLinks"][0]["color"] != values["genericLinks"][0]["color"], {theme: values}
        assert all(value["focusVisible"] and value["color"] == values["focusedGenericLinks"][0]["color"] for value in values["focusedGenericLinks"]), {theme: values["focusedGenericLinks"]}
        assert all(value["focusVisible"] and value["color"] == values["focusedMergedLinks"][0]["color"] for value in values["focusedMergedLinks"]), {theme: values["focusedMergedLinks"]}
        assert values["focusedMergedLinks"][0]["color"] != values["focusedGenericLinks"][0]["color"], {theme: values}
    assert metrics["branchRest"]["color"] != metrics["themes"]["light"]["genericLinks"][0]["color"]
    assert metrics["drag"][0] == metrics["drag"][1]
    for theme, shells in metrics["finderShells"].items():
        assert all(shell == shells[0] for shell in shells), {theme: shells}
    assert all(value["focusVisible"] for value in metrics["finderActions"]), json.dumps(metrics["finderActions"], indent=2)
    assert all(value["color"] == metrics["finderActions"][0]["color"] and value["border"] == metrics["finderActions"][0]["border"] for value in metrics["finderActions"]), json.dumps(metrics["finderActions"], indent=2)
    assert all(value["focusVisible"] for value in metrics["yoagentActions"]), metrics["yoagentActions"]
    assert all(value["color"] == metrics["yoagentActions"][0]["color"] and value["border"] == metrics["yoagentActions"][0]["border"] and value["outline"] == metrics["yoagentActions"][0]["outline"] for value in metrics["yoagentActions"]), metrics["yoagentActions"]
    hover_paints = {}
    for theme, light in (("dark", False), ("light", True)):
        browser.execute_script(
            "document.body.classList.toggle('theme-light', arguments[0]); document.body.classList.toggle('theme-dark', !arguments[0]);",
            light,
        )
        hover_paints[theme] = {}
        for family, link_id in (("generic", "popover-link"), ("merged", "popover-merged")):
            fast_pointer_actions(browser).move_to_element(browser.find_element("id", link_id)).perform()
            hover_paints[theme][family] = browser.execute_script("const s=getComputedStyle(document.getElementById(arguments[0])); return {color:s.color, decoration:s.textDecorationLine};", link_id)
    info_hover = {"color": metrics["infoLink"]["color"], "decoration": metrics["infoLink"]["decoration"]}
    for theme, families in hover_paints.items():
        assert families["generic"]["color"] == metrics["themes"][theme]["focusedGenericLinks"][0]["color"], json.dumps({theme: families}, indent=2)
        assert families["merged"]["color"] == metrics["themes"][theme]["focusedMergedLinks"][0]["color"], json.dumps({theme: families}, indent=2)
        assert families["merged"]["color"] != families["generic"]["color"], json.dumps({theme: families}, indent=2)
    assert info_hover == hover_paints["light"]["generic"]


def test_regular_and_compact_tabs_share_interaction_and_active_child_paint(browser, tmp_path):
    page = tmp_path / "tab-interaction-shared-paint.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <article class="panel active-pane" style="position:static;height:auto;width:760px;">
        <button id="regular" type="button" class="pane-tab"><span class="session-button-name">regular</span></button>
        <button id="compact" type="button" class="tmux-pane-tab-token tmux-pane-tab-token-action"><span class="session-button-name">compact</span></button>
        <button id="regular-active" type="button" class="pane-tab active"><span class="session-button-name">regular active</span></button>
        <button id="compact-active" type="button" class="tmux-pane-tab-token tmux-pane-tab-token-action active"><span class="session-button-name">compact active</span></button>
        <button id="missing-active" type="button" class="pane-tab file-missing active"><span class="session-button-name">missing active</span></button>
      </article>
      <div class="yolomux-dockview">
        <div class="dv-tab dv-active-tab"><button id="dock-active" type="button" class="pane-tab dockview-pane-tab"><span class="session-button-name">dock active</span></button></div>
      </div>
      <article class="panel" style="position:static;height:auto;width:760px;"><button id="plain-inactive" type="button" class="pane-tab">plain</button></article>
      <article class="panel typing-ready-pane" style="position:static;height:auto;width:760px;"><button id="typing-inactive" type="button" class="pane-tab">typing</button></article>
    """),
    )

    def paint(control_id):
        return browser.execute_script(
            """
            const style = getComputedStyle(document.getElementById(arguments[0]));
            return {background: style.backgroundColor, border: style.borderTopColor};
            """,
            control_id,
        )

    def assert_interaction_parity():
        hover = []
        for control_id in ("regular", "compact"):
            fast_pointer_actions(browser).move_to_element(browser.find_element("id", control_id)).perform()
            hover.append(paint(control_id))
        assert hover[0] == hover[1]

        focus = []
        for control_id in ("regular", "compact"):
            browser.execute_script("document.getElementById(arguments[0]).focus({focusVisible: true})", control_id)
            assert browser.execute_script("return document.getElementById(arguments[0]).matches(':focus-visible')", control_id) is True
            focus.append(paint(control_id))
        assert focus[0] == focus[1] == hover[0]

    assert_interaction_parity()
    browser.execute_script("document.body.classList.add('theme-light')")
    assert_interaction_parity()

    active = browser.execute_script(
        """
        const read = id => {
          const tab = document.getElementById(id);
          return {tab: getComputedStyle(tab).color, child: getComputedStyle(tab.querySelector('.session-button-name')).color};
        };
        return ['regular-active', 'compact-active', 'dock-active', 'missing-active'].map(read);
        """
    )
    assert all(item["child"] == item["tab"] for item in active)
    assert active[0]["tab"] == active[1]["tab"] == active[2]["tab"]
    assert active[3]["tab"] != active[0]["tab"]

    inactive_backgrounds = browser.execute_script(
        """
        return ['regular', 'plain-inactive', 'typing-inactive'].map(id => {
          const tab = document.getElementById(id);
          const probe = document.createElement('span');
          probe.style.background = 'var(--pane-bar-bg)';
          tab.parentElement.appendChild(probe);
          const expected = getComputedStyle(probe).backgroundColor;
          probe.remove();
          return {actual: getComputedStyle(tab).backgroundColor, expected};
        });
        """
    )
    assert all(item["actual"] == item["expected"] for item in inactive_backgrounds)


def test_pane_info_bar_repository_selector_cycles_opens_and_selects(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,__prefs__&layout=row@50(left,right)&tabs=left:1;right:__prefs__",
        sessions=["1"],
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof updatePanelHeader === 'function'
              && typeof cycleSessionRepoDisplay === 'function'
              && document.getElementById('panel-1') !== null;
            """
        )
    )
    initial = browser.execute_script(
        """
        const repos = [1, 2, 3, 4].map(index => ({
          root: `/home/test/repo-${index}`,
          cwd: `/home/test/repo-${index}`,
          branch: `branch-${index}`,
          dirty_count: index,
          primary: index === 1,
        }));
        const info = {
          session: '1',
          selected_pane: {current_path: repos[0].root},
          panes: [],
          project: {git: {...repos[0]}, repos},
        };
        transcriptMetadataState.payload.sessions['1'] = info;
        cycleSessionRepoDisplay('1', info, 1);
        updatePanelHeader('1', info);
        return document.querySelector('[data-repo-chip="1"]')?.textContent.trim() || '';
        """
    )
    assert initial == "2/4"

    assert browser.execute_script(
        "setFocusedPanelItem('__prefs__', {userInitiated: true}); return document.getElementById('panel-1').classList.contains('focused-pane');"
    ) is False
    previous = browser.find_element("css selector", '[data-repo-cycle="1"][data-repo-cycle-dir="-1"]')
    fast_pointer_actions(browser).move_to_element(previous).click().perform()
    assert browser.find_element("css selector", '[data-repo-chip="1"]').text.strip() == "1/4"
    assert browser.execute_script("return document.getElementById('panel-1').classList.contains('focused-pane')") is True

    next_button = browser.find_element("css selector", '[data-repo-cycle="1"][data-repo-cycle-dir="1"]')
    fast_pointer_actions(browser).move_to_element(next_button).click().perform()
    assert browser.find_element("css selector", '[data-repo-chip="1"]').text.strip() == "2/4"

    chip = browser.find_element("css selector", '[data-repo-chip="1"]')
    fast_pointer_actions(browser).move_to_element(chip).click().perform()
    rows = WebDriverWait(browser, 5).until(
        lambda driver: driver.find_elements("css selector", ".repo-chip-menu [data-repo-chip-open]")
    )
    assert len(rows) == 4
    assert browser.execute_script(
        """
        const menu = document.querySelector('.repo-chip-menu');
        if (!menu) return false;
        const rect = menu.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        return rect.width > 0 && rect.height > 0 && menu.contains(hit);
        """
    ) is True
    repo_three = browser.find_element("css selector", '[data-repo-chip-open="/home/test/repo-3"]')
    fast_pointer_actions(browser).move_to_element(repo_three).click().perform()
    assert browser.find_element("css selector", '[data-repo-chip="1"]').text.strip() == "3/4"


def test_standalone_svg_blocked_tags_share_dom_and_string_policy(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    results = browser.execute_script(
        """
        const tags = ['script', 'foreignobject', 'iframe', 'object', 'embed', 'audio', 'video', 'canvas', 'link', 'meta'];
        const outputs = tags.map(tag => {
          const tagName = tag === 'foreignobject' ? 'foreignObject' : tag;
          const source = `<svg><a href="#local">ok</a><${tagName}>blocked-${tag}</${tagName}></svg>`;
          return {tag, dom: sanitizeStandaloneSvg(source), string: sanitizeStandaloneSvgString(source)};
        });
        const foreignObject = sanitizeStandaloneSvg('<svg><foreignObject x="0" y="0" width="100" height="20"><div>safe label</div></foreignObject></svg>');
        return {outputs, foreignObject};
        """
    )
    for output in results["outputs"]:
        tag = "foreignObject" if output["tag"] == "foreignobject" else output["tag"]
        blocked_tag = re.compile(rf"<\s*{re.escape(tag)}\b", re.IGNORECASE)
        assert blocked_tag.search(output["dom"]) is None, output
        assert blocked_tag.search(output["string"]) is None, output
        assert 'href="#local"' in output["dom"], output
        assert 'href="#local"' in output["string"], output
    assert re.search(r"<foreignObject\b", results["foreignObject"], re.IGNORECASE) is None
    assert "safe label" in results["foreignObject"]


def test_terminal_wheel_routes_alt_screen_lines_to_xterm_and_normal_lines_to_tmux(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const socket = window.__bootSocketInstances.find(item => item.url.includes('/ws?session=1'));
            return typeof sessionPaneIsAlternateScreen === 'function'
              && document.querySelector('#term-1 .xterm') !== null
              && socket?.readyState === WebSocket.OPEN;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const container = document.getElementById('term-1');
        const screen = container.querySelector('.xterm');
        const socket = window.__bootSocketInstances.find(item => item.url.includes('/ws?session=1'));
        const forwarded = [];
        screen.addEventListener('wheel', event => {
          if (event.deltaMode === WheelEvent.DOM_DELTA_LINE) {
            forwarded.push({deltaY: event.deltaY, deltaMode: event.deltaMode});
          }
        });
        const signalState = alternateOn => ({
          ok: true,
          sessions: {'1': {}},
          windows: [{
            key: '1:0', session: '1', window_index: '0', active: true,
            panes: [{
              window_key: '1:0', session: '1', window_index: '0', pane_index: '0',
              target: '%11', pane_id: '%11', current_command: alternateOn ? 'claude' : 'bash',
              active: true, alternate_on: alternateOn, pid: 1234, dead: false,
            }],
          }],
        });

        tmuxSignalState = signalState(true);
        screen.dispatchEvent(new WheelEvent('wheel', {deltaY: 105, deltaMode: 0, bubbles: true, cancelable: true}));
        const mouseForwarded = forwarded.slice();
        for (let index = 0; index < 5; index += 1) {
          screen.dispatchEvent(new WheelEvent('wheel', {deltaY: 7, deltaMode: 0, bubbles: true, cancelable: true}));
        }
        const touchpadForwarded = forwarded.slice(mouseForwarded.length);

        tmuxSignalState = signalState(false);
        const beforeNormal = forwarded.length;
        screen.dispatchEvent(new WheelEvent('wheel', {deltaY: 105, deltaMode: 0, bubbles: true, cancelable: true}));
        setTimeout(() => {
          const tmuxScrollFrames = socket.sent
            .map(message => {
              try { return JSON.parse(message); } catch (_error) { return null; }
            })
            .filter(message => message?.type === 'tmux-scroll');
          done({
            mouseForwarded,
            touchpadForwarded,
            normalForwarded: forwarded.slice(beforeNormal),
            tmuxScrollFrames,
            alternateAfterSwitch: sessionPaneIsAlternateScreen('1'),
            errors: window.__bootErrors || [],
            rejections: window.__bootRejections || [],
          });
        }, 60);
        """
    )
    assert metrics["mouseForwarded"] == [{"deltaY": 1, "deltaMode": 1}] * 3, metrics
    assert metrics["touchpadForwarded"] == [{"deltaY": 1, "deltaMode": 1}], metrics
    assert metrics["normalForwarded"] == [], metrics
    assert metrics["tmuxScrollFrames"] == [{"type": "tmux-scroll", "direction": "down", "lines": 3}], metrics
    assert metrics["alternateAfterSwitch"] is False, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics


def test_terminal_app_modified_arrows_route_to_tmux_scrollback_boundaries(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const socket = window.__bootSocketInstances.find(item => item.url.includes('/ws?session=1'));
            return document.querySelector('#term-1 .xterm') !== null
              && terminals.get('1')?.term?.rows > 0
              && socket?.readyState === WebSocket.OPEN;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const screen = document.querySelector('#term-1 .xterm');
        const socket = window.__bootSocketInstances.find(item => item.url.includes('/ws?session=1'));
        const pageLines = Math.max(1, Math.floor(terminals.get('1').term.rows * terminalWheelPageFraction));
        const dispatch = key => {
          const event = new KeyboardEvent('keydown', {key, code: key, ctrlKey: true, bubbles: true, cancelable: true});
          const accepted = screen.dispatchEvent(event);
          return {key, accepted, defaultPrevented: event.defaultPrevented};
        };
        const historyUp = dispatch('ArrowUp');
        setTimeout(() => {
          const historyDown = dispatch('ArrowDown');
          setTimeout(() => {
            const frames = socket.sent
              .map(message => {
                try { return JSON.parse(message); } catch (_error) { return null; }
              })
              .filter(message => message?.type === 'tmux-scroll' || message?.type === 'input');
            done({pageLines, historyUp, historyDown, frames, errors: window.__bootErrors || [], rejections: window.__bootRejections || []});
          }, 60);
        }, 60);
        """
    )
    assert metrics["historyUp"] == {"key": "ArrowUp", "accepted": False, "defaultPrevented": True}, metrics
    assert metrics["historyDown"] == {"key": "ArrowDown", "accepted": False, "defaultPrevented": True}, metrics
    assert metrics["frames"] == [
        {"type": "tmux-scroll", "direction": "up", "lines": metrics["pageLines"]},
        {"type": "tmux-scroll", "direction": "down", "lines": metrics["pageLines"]},
    ], metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics


def test_keyboard_shortcuts_overlay_fits_narrow_viewport(browser, tmp_path):
    browser.set_window_size(375, 680)
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    metrics = browser.execute_script(
        """
        openKeyboardShortcutsOverlay();
        const dialog = document.querySelector('.keyboard-shortcuts-dialog');
        const body = document.querySelector('.keyboard-shortcuts-body');
        const rect = dialog.getBoundingClientRect();
        return {
          rect: {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom},
          viewport: {width: innerWidth, height: innerHeight},
          bodyScrollsHorizontally: body.scrollWidth > body.clientWidth + 1,
          rowsOverflow: Array.from(dialog.querySelectorAll('.keyboard-shortcut-row')).some(row => row.scrollWidth > row.clientWidth + 1),
          pageBinding: Array.from(dialog.querySelectorAll('.keyboard-shortcut-row')).some(row => row.textContent.includes('Page tmux scrollback') && row.textContent.includes('Ctrl+↑ / Ctrl+↓')),
        };
        """
    )
    assert metrics["rect"]["left"] >= 0, metrics
    assert metrics["rect"]["right"] <= metrics["viewport"]["width"], metrics
    assert metrics["rect"]["top"] >= 0, metrics
    assert metrics["rect"]["bottom"] <= metrics["viewport"]["height"], metrics
    assert metrics["bodyScrollsHorizontally"] is False, metrics
    assert metrics["rowsOverflow"] is False, metrics
    assert metrics["pageBinding"] is True, metrics


def test_loading_and_thinking_surfaces_share_one_activity_cadence(browser, tmp_path):
    page = tmp_path / "shared-activity-cadence.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(
            f"""
            <button id="info-refresh" class="info-refresh loading">Refresh</button>
            <div class="actions"><button id="refreshMeta" class="loading">Refresh all</button></div>
            <span id="info-spinner" class="info-loading-spinner"></span>
            <div class="file-tree-row kind-dir loading-children"><span id="finder-spinner" class="file-tree-icon"></span></div>
            <div id="command-thinking" class="command-palette-status">Thinking</div>
            <div class="yoagent-message assistant streaming"><span id="agent-thinking" class="yoagent-message-role">Agent</span></div>
            """
        ),
    )
    metrics = browser.execute_script(
        """
        const animation = (selector, pseudo) => {
          const style = getComputedStyle(document.querySelector(selector), pseudo);
          return {name: style.animationName, duration: style.animationDuration};
        };
        return {
          token: getComputedStyle(document.documentElement).getPropertyValue('--motion-activity-duration').trim(),
          infoRefresh: animation('#info-refresh', '::before'),
          globalRefresh: animation('#refreshMeta', '::before'),
          infoSpinner: animation('#info-spinner'),
          finderSpinner: animation('#finder-spinner', '::before'),
          commandThinking: animation('#command-thinking', '::before'),
          agentThinking: animation('#agent-thinking', '::after'),
        };
        """
    )
    assert metrics["token"] == "900ms", metrics
    for key in ("infoRefresh", "globalRefresh", "infoSpinner", "finderSpinner"):
        assert metrics[key] == {"name": "metadata-refresh-spin", "duration": "0.9s"}, metrics
    for key in ("commandThinking", "agentThinking"):
        assert metrics[key] == {"name": "command-palette-thinking", "duration": "0.9s"}, metrics


def test_standard_component_text_follows_responsive_ui_type_scale(browser, tmp_path):
    page = tmp_path / "responsive-ui-type-scale.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(
            """
            <div id="axis" class="js-debug-y-axis">10</div>
            <textarea id="debug-log" class="js-debug-log">11</textarea>
            <div id="drag-title" class="pane-drag-image-title">12</div>
            <div id="agent-state" class="yoagent-message-state">11</div>
            <span id="popout-icon" class="file-editor-icon file-editor-icon-popout-preview"></span>
            <div class="drop-action-result"><pre id="drop-result">12</pre></div>
            """
        ),
    )
    metrics = browser.execute_script(
        """
        const read = () => ({
          axis: getComputedStyle(document.getElementById('axis')).fontSize,
          debugLog: getComputedStyle(document.getElementById('debug-log')).fontSize,
          dragTitle: getComputedStyle(document.getElementById('drag-title')).fontSize,
          agentState: getComputedStyle(document.getElementById('agent-state')).fontSize,
          popoutIcon: getComputedStyle(document.getElementById('popout-icon'), '::after').fontSize,
          dropResult: getComputedStyle(document.getElementById('drop-result')).fontSize,
        });
        const defaults = read();
        document.documentElement.style.setProperty('--ui-font-size', '18px');
        return {defaults, scaled: read()};
        """
    )
    assert metrics["defaults"] == {
        "axis": "10px",
        "debugLog": "11px",
        "dragTitle": "12px",
        "agentState": "11px",
        "popoutIcon": "10px",
        "dropResult": "12px",
    }, metrics
    assert metrics["scaled"] == {
        "axis": "15px",
        "debugLog": "16px",
        "dragTitle": "17px",
        "agentState": "16px",
        "popoutIcon": "15px",
        "dropResult": "17px",
    }, metrics


def test_event_rows_follow_pane_width_with_scaled_localized_metadata(browser, tmp_path):
    page = tmp_path / "responsive-event-rows.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(
            """
            <div id="event-host" style="height: 220px;">
              <div id="event-list" class="event-list">
                <div id="event-row" class="event-item">
                  <span id="event-time" class="event-time">2026年07月03日 12时34分56秒</span>
                  <span id="event-type" class="event-type">后台工作区同步完成事件</span>
                  <span id="event-message" class="event-message">A deliberately long translated event message must retain readable width and wrap inside the selected pane.</span>
                </div>
              </div>
            </div>
            """
        ),
    )
    metrics = browser.execute_script(
        """
        document.documentElement.style.setProperty('--ui-font-size', '18px');
        const host = document.getElementById('event-host');
        const list = document.getElementById('event-list');
        const row = document.getElementById('event-row');
        const time = document.getElementById('event-time');
        const type = document.getElementById('event-type');
        const message = document.getElementById('event-message');
        const measure = (width, light) => {
          host.style.width = `${width}px`;
          document.body.classList.toggle('theme-light', light);
          void list.offsetWidth;
          const rowRect = row.getBoundingClientRect();
          const timeRect = time.getBoundingClientRect();
          const typeRect = type.getBoundingClientRect();
          const messageRect = message.getBoundingClientRect();
          const style = getComputedStyle(list);
          return {
            containerType: style.containerType,
            containerName: style.containerName,
            listClientWidth: list.clientWidth,
            listScrollWidth: list.scrollWidth,
            rowClientWidth: row.clientWidth,
            rowScrollWidth: row.scrollWidth,
            rowTop: rowRect.top,
            timeTop: timeRect.top,
            typeTop: typeRect.top,
            metadataBottom: Math.max(timeRect.bottom, typeRect.bottom),
            messageTop: messageRect.top,
            messageWidth: messageRect.width,
            background: style.backgroundColor,
          };
        };
        return {
          wideDark: measure(900, false),
          narrowDark: measure(340, false),
          wideLight: measure(900, true),
          narrowLight: measure(340, true),
        };
        """
    )
    for theme in ("Dark", "Light"):
        wide = metrics[f"wide{theme}"]
        narrow = metrics[f"narrow{theme}"]
        assert wide["containerType"] == "inline-size", metrics
        assert wide["containerName"] == "event-list", metrics
        assert abs(wide["messageTop"] - wide["timeTop"]) <= 1, metrics
        assert wide["messageWidth"] >= 200, metrics
        assert narrow["messageTop"] >= narrow["metadataBottom"] + 4, metrics
        assert narrow["messageWidth"] >= narrow["rowClientWidth"] * 0.85, metrics
        for layout in (wide, narrow):
            assert layout["listScrollWidth"] <= layout["listClientWidth"] + 1, metrics
            assert layout["rowScrollWidth"] <= layout["rowClientWidth"] + 1, metrics
    assert metrics["wideDark"]["background"] != metrics["wideLight"]["background"], metrics


def test_mock_agent_prompt_payload_renders_ask_attention_in_live_browser(browser, monkeypatch, tmp_path):
    paths = isolate_browser_runtime_paths(monkeypatch, tmp_path)
    specs = {
        "codex": {
            "session": f"yb-codex-{os.getpid()}-{uuid.uuid4().hex[:8]}",
            "user_input": "touch /tmp/yolomux-mock-approval",
            "prompt_glyph": "›",
            "question": "Would you like to run the following command?",
            "expected_command": "touch /tmp/yolomux-mock-approval",
        },
        "claude": {
            "session": f"yb-claude-{os.getpid()}-{uuid.uuid4().hex[:8]}",
            "user_input": "sleep 10",
            "prompt_glyph": "❯",
            "question": "Do you want to proceed?",
            "expected_command": None,
        },
    }
    sessions = [spec["session"] for spec in specs.values()]
    tmux_runtime = None
    app = None
    try:
        tmux_runtime = start_isolated_tmux_runtime(
            monkeypatch,
            tmp_path,
            session_commands={
                spec["session"]: f"cd {REPO_ROOT} && exec python3 tools/{agent}.py --mock"
                for agent, spec in specs.items()
            },
            columns=120,
            rows=40,
        )
        booted, panes = wait_for_isolated_tmux_panes(
            tmux_runtime,
            sessions,
            lambda captures: all(spec["prompt_glyph"] in captures[spec["session"]] for spec in specs.values()),
        )
        assert booted, f"mock agents did not boot to their input prompts:\n{panes}"

        for spec in specs.values():
            run_isolated_tmux(tmux_runtime, "send-keys", "-t", f"{spec['session']}:", spec["user_input"], "Enter")
        prompted, panes = wait_for_isolated_tmux_panes(
            tmux_runtime,
            sessions,
            lambda captures: all(
                spec["user_input"] in captures[spec["session"]]
                and ("Would you like to run the following command?" in captures[spec["session"]] or "Do you want to proceed?" in captures[spec["session"]])
                for spec in specs.values()
            ),
        )
        assert prompted, f"mock agents did not render approval prompts:\n{panes}"

        app = TmuxWebtermApp(sessions, dangerously_yolo=False)
        payloads = {}
        for agent, spec in specs.items():
            payload = app.auto_approve_session_status(spec["session"], capture_bare_session_when_roster=True)
            payloads[spec["session"]] = payload
            assert payload["prompt"]["visible"] is True
            assert payload["screen"]["key"] == "approval"
            assert payload["prompt"]["agent"] == agent
            assert payload["prompt"]["text"] == spec["question"]
            assert payload["prompt"]["command"] == spec["expected_command"]
            assert payload["prompt"]["signature"]

        auto_approve_payload = {
            "session_order": sessions,
            "sessions": payloads,
            "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
        }
        load_live_runtime_boot_fixture(
            browser,
            tmp_path,
            sessions=sessions,
            transcript_sessions={spec["session"]: {"agents": [{"kind": agent}], "panes": []} for agent, spec in specs.items()},
            auto_approve_payload=auto_approve_payload,
        )
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                const sessions = arguments[0];
                return sessions.every(session => document.getElementById(`panel-${session}`))
                  && document.querySelector('#topbarActivity .topbar-activity-ask .topbar-activity-count-number')?.textContent === '2'
                  && document.querySelector('#topbarActivity .topbar-activity-ask .agent-window-status-dot')?.classList.contains('status-indicator--attention');
                """,
                sessions,
            )
        )
        metrics = browser.execute_script(
            """
            const sessions = arguments[0];
            const topbar = document.getElementById('topbarActivity');
            const sessionState = session => {
              const panel = document.getElementById(`panel-${session}`);
              const tab = document.getElementById(`panel-tab-${session}`);
              const badge = tab?.querySelector('[data-prompt-attention-clear]');
              return {
                badgeText: badge?.textContent || '',
                badgePresent: !!badge,
                tabAttention: tab?.classList.contains('needs-attention') || false,
                panelNeedsApproval: panel?.classList.contains('needs-exec-pane') || false,
              };
            };
            const beforeSocketFrames = (window.__bootSocketInstances || []).flatMap(socket => socket.sent || []);
            const before = {
              statusPulseDisabled: document.documentElement.classList.contains('status-pulse-disabled'),
              sessions: Object.fromEntries(sessions.map(session => [session, sessionState(session)])),
              topbarText: topbar?.textContent || '',
              topbarAskCount: topbar?.querySelector('.topbar-activity-ask .topbar-activity-count-number')?.textContent || '',
              topbarAskHasSharedParent: topbar?.querySelector('.topbar-activity-ask .agent-window-status-dot')?.classList.contains('status-indicator') || false,
              topbarAskHasAttentionModifier: topbar?.querySelector('.topbar-activity-ask .agent-window-status-dot')?.classList.contains('status-indicator--attention') || false,
              topbarAskHasPulse: topbar?.querySelector('.topbar-activity-ask .agent-window-status-dot')?.classList.contains('attention-pulse') || false,
            };
            sessions.forEach(session => acknowledgeTerminalAttentionFromUserAction(session, 0, {delayMs: agentWindowActivityAcknowledgeDelayMs, localOnly: true}));
            const afterSocketFrames = (window.__bootSocketInstances || []).flatMap(socket => socket.sent || []);
            const immediate = {
              sessions: Object.fromEntries(sessions.map(session => [session, sessionState(session)])),
              topbarText: topbar?.textContent || '',
              topbarAskCount: topbar?.querySelector('.topbar-activity-ask .topbar-activity-count-number')?.textContent || '',
              acknowledgedStatusCount: document.querySelectorAll('.agent-window-status-dot.agent-window-status-dot--acknowledging.status-indicator--acknowledged').length,
              acknowledgedStatusPulses: document.querySelectorAll('.agent-window-status-dot.agent-window-status-dot--acknowledging.attention-pulse').length,
              newInputFrames: afterSocketFrames.slice(beforeSocketFrames.length).filter(frame => String(frame).includes('"type":"input"')).length,
            };
            return {before, immediate};
            """,
            sessions,
        )
        assert metrics["before"]["statusPulseDisabled"] is True
        for session in sessions:
            assert metrics["before"]["sessions"][session] == {
                "badgeText": "",
                "badgePresent": False,
                "tabAttention": True,
                "panelNeedsApproval": True,
            }
        assert metrics["before"]["topbarAskCount"] == "2"
        assert metrics["before"]["topbarAskHasSharedParent"] is True
        assert metrics["before"]["topbarAskHasAttentionModifier"] is True
        assert metrics["before"]["topbarAskHasPulse"] is False
        for session in sessions:
            assert metrics["immediate"]["sessions"][session] == {
                "badgeText": "",
                "badgePresent": False,
                "tabAttention": False,
                "panelNeedsApproval": False,
            }
        assert metrics["immediate"]["topbarAskCount"] == "2"
        assert metrics["immediate"]["acknowledgedStatusCount"] >= 2
        assert metrics["immediate"]["acknowledgedStatusPulses"] == 0
        assert metrics["immediate"]["newInputFrames"] == 0
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                const sessions = arguments[0];
                return sessions.every(session => {
                  const tab = document.getElementById(`panel-tab-${session}`);
                  const panel = document.getElementById(`panel-${session}`);
                  return (tab?.querySelector('[data-prompt-attention-clear]')?.textContent || '') === ''
                    && !tab?.classList.contains('needs-attention')
                    && !panel?.classList.contains('needs-exec-pane');
                })
                  && document.querySelector('#topbarActivity .topbar-activity-ask .topbar-activity-count-number')?.textContent === '0';
                """,
                sessions,
            )
        )
    finally:
        if app is not None:
            app.control_server.stop()
        stop_isolated_tmux_runtime(tmux_runtime)
        cleanup_isolated_browser_runtime_paths(paths)


def test_topbar_status_actions_share_shell_and_pointer_keyboard_paint(browser, tmp_path):
    page = tmp_path / "topbar-status-actions.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <div id="token-topbar" class="topbar"></div>
      <button id="owner" class="topbar-owner-status topbar-status-surface">IDX: leader</button>
      <button id="activity" class="topbar-activity topbar-status-surface">1 running</button>
      <button id="attention" class="topbar-activity topbar-status-surface has-attention">1 attention</button>
      <div id="latency" class="latency-meter topbar-status-surface">10 ms</div>
      <span id="view-chip" class="command-palette-view-chip">preview</span>
      <button id="normal-action" class="preferences-inline-action">normal</button>
      <div class="preferences-setting-advisory"><button id="advisory-action" class="preferences-inline-action">advisory</button></div>
      <div id="neutral" style="width:20px;height:20px"></div>
    """, extra_css="#token-topbar { transition: none; }"),
    )

    def read(element_id):
        return browser.execute_script(
            """
            const node = document.getElementById(arguments[0]);
            const style = getComputedStyle(node);
            return {
              display: style.display,
              flex: style.flex,
              alignItems: style.alignItems,
              height: style.height,
              fontSize: style.fontSize,
              cursor: style.cursor,
              whiteSpace: style.whiteSpace,
              color: style.color,
              background: style.backgroundColor,
              border: style.border,
            };
            """,
            element_id,
        )

    def hover(element_id):
        browser.execute_script("document.activeElement?.blur()")
        fast_pointer_actions(browser).move_to_element(browser.find_element("id", element_id)).perform()
        return read(element_id)

    def focus(element_id):
        fast_pointer_actions(browser).move_to_element(browser.find_element("id", "neutral")).perform()
        browser.execute_script("document.getElementById(arguments[0]).focus({focusVisible: true})", element_id)
        assert browser.execute_script("return document.getElementById(arguments[0]).matches(':focus-visible')", element_id) is True
        return read(element_id)

    for theme in ("theme-dark", "theme-light"):
        browser.execute_script("document.body.className = arguments[0]", theme)
        fast_pointer_actions(browser).move_to_element(browser.find_element("id", "neutral")).perform()
        owner = read("owner")
        activity = read("activity")
        for property_name in ("display", "flex", "alignItems", "height", "fontSize", "cursor", "whiteSpace"):
            assert owner[property_name] == activity[property_name], (theme, property_name, owner, activity)
        assert owner["display"] == "inline-flex"
        latency = read("latency")
        assert latency["display"] == "inline-grid"
        assert latency["cursor"] == "auto"
        for property_name in ("background", "border"):
            assert len({owner[property_name], activity[property_name], latency[property_name]}) == 1, (theme, property_name, owner, activity, latency)
        token_metrics = browser.execute_script(
            """
            const paint = id => {
              const style = getComputedStyle(document.getElementById(id));
              return {color: style.color, background: style.backgroundColor, border: style.borderTopColor};
            };
            const probe = document.createElement('div');
            document.body.appendChild(probe);
            const value = (property, token) => {
              probe.style.cssText = '';
              probe.style.setProperty(property, `var(${token})`);
              const style = getComputedStyle(probe);
              return property === 'background' ? style.backgroundColor : property === 'border-color' ? style.borderTopColor : style.color;
            };
            const metrics = {
              topbar: paint('token-topbar'),
              chip: paint('view-chip'),
              normalAction: paint('normal-action'),
              advisoryAction: paint('advisory-action'),
              panel2: value('background', '--panel2'),
              strip: value('background', '--pane-tab-strip-bg'),
              text: value('color', '--text'),
              softBg: value('background', '--active-control-soft-bg'),
              softBorder: value('border-color', '--active-control-soft-border'),
              active: value('color', '--active-control-bg'),
              good: value('color', '--good'),
              panel: value('background', '--panel'),
            };
            probe.remove();
            return metrics;
            """
        )
        assert token_metrics["topbar"]["background"] == token_metrics["panel2"], (theme, token_metrics)
        assert token_metrics["chip"] == {"color": token_metrics["text"], "background": token_metrics["softBg"], "border": token_metrics["softBorder"]}, (theme, token_metrics)
        assert token_metrics["normalAction"]["color"] == token_metrics["active"], (theme, token_metrics)
        if theme == "theme-light":
            assert token_metrics["advisoryAction"]["color"] == token_metrics["good"], token_metrics
            assert token_metrics["advisoryAction"]["background"] == token_metrics["panel"], token_metrics
        fast_pointer_actions(browser).move_to_element(browser.find_element("id", "token-topbar")).perform()
        assert read("token-topbar")["background"] == token_metrics["strip"], (theme, token_metrics, read("token-topbar"))
        assert hover("owner") == focus("owner")
        assert hover("activity") == focus("activity")
        assert hover("attention") == focus("attention")

    hidden_display = browser.execute_script(
        "const node = document.getElementById('owner'); node.hidden = true; return getComputedStyle(node).display;"
    )
    assert hidden_display == "none"


def test_touch_compact_topbar_keeps_menu_and_status_groups_separate(browser, tmp_path):
    page = tmp_path / "touch-compact-topbar.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <header id="touch-topbar" class="topbar">
        <div class="brand-cell"><div class="brand title brand-title"><span class="brand-yolo">YO</span><span>LO</span><span>mux</span><span class="brand-version"> 0.5.24</span></div></div>
        <div id="sessionButtons" class="app-menu-area">
          <nav class="app-menu-bar"><div id="touch-menu" class="app-menu"><button class="app-menu-button">Menus</button></div></nav>
          <div id="touch-nav" class="topbar-nav"><button class="topbar-nav-button">←</button><button class="topbar-nav-button">→</button></div>
          <button id="touch-search" class="topbar-search"><span class="topbar-search-icon">⌕</span><span id="touch-search-label" class="topbar-search-label"><span class="topbar-search-label-long">Search files, commands</span><span class="topbar-search-label-short" aria-hidden="true">Search</span></span><kbd class="topbar-search-hint">Cmd-P</kbd></button>
          <button id="touch-language" class="topbar-language">English</button>
          <button id="touch-owner" class="topbar-owner-status topbar-status-surface">IDX: leader</button>
          <button id="touch-activity" class="topbar-activity topbar-status-surface"><span class="topbar-activity-count topbar-activity-working active"><span class="topbar-activity-count-number">2</span><span class="agent-window-activity agent-window-activity--status-only agent-window-activity--working topbar-activity-ball"><span class="status-indicator status-indicator--dot status-indicator--working">●</span></span></span><span class="topbar-activity-sep">·</span><span class="topbar-activity-count topbar-activity-ask active"><span class="topbar-activity-count-number">1</span><span class="agent-window-activity agent-window-activity--status-only agent-window-activity--attention topbar-activity-ball"><span class="status-indicator status-indicator--dot status-indicator--attention">●</span></span></span><span class="topbar-activity-sep">·</span><span class="topbar-activity-count topbar-activity-blocked active"><span class="topbar-activity-count-number">3</span><span class="agent-window-activity agent-window-activity--status-only agent-window-activity--cooldown topbar-activity-ball"><span class="status-indicator status-indicator--dot status-indicator--cooldown">●</span></span></span><span class="topbar-activity-idle">3 idle</span></button>
        </div>
        <div id="touch-actions" class="actions"><div id="latencyMeter" class="latency-meter">12 ms</div><button id="notifyToggle">Notify</button><button id="refreshMeta">Refresh</button><button id="logoutButton">Log out</button><span id="status">connected</span></div>
      </header>
    """, extra_css="""
      body { margin: 0; padding: 0; display: block; height: auto; min-height: 0; }
      #touch-topbar { width: 390px; }
    """),
    )
    metrics = browser.execute_script(
        """
        document.body.classList.add('app-topbar-touch-compact', 'app-topbar-menu-compact', 'app-topbar-coarse-pointer', 'app-vw-lte-600', 'app-vw-lte-760', 'app-vw-lte-980', 'app-vw-lte-1100');
        const actions = document.getElementById('touch-actions');
        const activityNode = document.getElementById('touch-activity');
        actions.insertBefore(activityNode, document.getElementById('refreshMeta'));
        const box = node => {
          const rect = node.getBoundingClientRect();
          const style = getComputedStyle(node);
          return {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height, display: style.display};
        };
        const overlaps = (left, right) => left.right > right.left + 0.5 && right.right > left.left + 0.5 && left.bottom > right.top + 0.5 && right.bottom > left.top + 0.5;
        const header = document.getElementById('touch-topbar');
        const menu = document.getElementById('touch-menu');
        const activity = document.getElementById('touch-activity');
        const sessionButtons = document.getElementById('sessionButtons');
        return {
          header: box(header),
          menu: box(menu),
          menuButton: box(menu.querySelector('.app-menu-button')),
          menuTouchAction: getComputedStyle(menu.querySelector('.app-menu-button')).touchAction,
          activity: box(activity),
          sessionButtons: box(sessionButtons),
          actions: box(actions),
          search: box(document.getElementById('touch-search')),
          searchLabel: box(document.getElementById('touch-search-label')),
          searchShortLabel: box(document.querySelector('.topbar-search-label-short')),
          searchLongLabel: box(document.querySelector('.topbar-search-label-long')),
          searchShortLabelOverflow: document.querySelector('.topbar-search-label-short').scrollWidth > document.querySelector('.topbar-search-label-short').clientWidth,
          language: box(document.getElementById('touch-language')),
          owner: box(document.getElementById('touch-owner')),
          nav: box(document.getElementById('touch-nav')),
          refresh: box(document.getElementById('refreshMeta')),
          notify: box(document.getElementById('notifyToggle')),
          logout: box(document.getElementById('logoutButton')),
          menuActivityOverlap: overlaps(box(menu), box(activity)),
          searchActivityOverlap: overlaps(box(document.getElementById('touch-search')), box(activity)),
          activityCounts: [...activity.querySelectorAll('.topbar-activity-count.active')].map(node => ({box: box(node), text: node.querySelector('.topbar-activity-count-number')?.textContent || '', numberDisplay: getComputedStyle(node.querySelector('.topbar-activity-count-number')).display})),
          idleDisplay: getComputedStyle(activity.querySelector('.topbar-activity-idle')).display,
        };
        """
    )
    assert metrics["actions"]["display"] == "flex", metrics
    # A flex child blockifies inline-grid to computed grid while retaining the shared centered grid shell.
    assert metrics["refresh"]["display"] == "grid", metrics
    assert metrics["notify"]["display"] == "none", metrics
    assert metrics["logout"]["display"] == "none", metrics
    assert metrics["search"]["display"] == "flex", metrics
    # A phone still exposes a short, useful Cmd-P label rather than an unexplained magnifying
    # glass or a mostly empty full-width search field.
    assert metrics["search"]["width"] > metrics["search"]["height"] + 1, metrics
    assert metrics["searchLabel"]["display"] == "flex", metrics
    assert metrics["searchLongLabel"]["display"] == "none", metrics
    # A flex child blockifies the inline short label while retaining the compact text content.
    assert metrics["searchShortLabel"]["display"] == "block", metrics
    assert metrics["searchShortLabelOverflow"] is False, metrics
    assert metrics["language"]["display"] == "none", metrics
    assert metrics["owner"]["display"] == "none", metrics
    assert metrics["nav"]["display"] == "none", metrics
    assert metrics["activity"]["display"] == "flex", metrics
    assert [item["text"] for item in metrics["activityCounts"]] == ["2", "1", "3"], metrics
    assert all(item["box"]["width"] >= 20 and item["numberDisplay"] == "grid" for item in metrics["activityCounts"]), metrics
    assert metrics["idleDisplay"] == "none", metrics
    assert metrics["menu"]["left"] >= metrics["sessionButtons"]["left"] - 0.5, metrics
    assert metrics["menuButton"]["height"] >= 36, metrics
    assert metrics["menuTouchAction"] == "manipulation", metrics
    assert metrics["menu"]["right"] <= metrics["search"]["left"] + 0.5, metrics
    assert metrics["search"]["right"] <= metrics["actions"]["left"] + 2, metrics
    assert metrics["activity"]["right"] <= metrics["refresh"]["left"] + 0.5, metrics
    assert metrics["refresh"]["left"] - metrics["activity"]["right"] <= 8, metrics
    assert metrics["header"]["height"] < 50, metrics


def test_touch_terminal_smart_key_accessory_is_a_movable_palette_with_large_targets(browser, tmp_path):
    page = tmp_path / "touch-terminal-smart-keys.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <div id="terminal-pane" class="tab-pane active panel-overlay-root">
        <div id="terminal" class="terminal"><div class="xterm"></div></div>
        <button id="smart-key-launcher" class="mobile-terminal-key-launcher">⌨</button>
          <div id="smart-keys" class="mobile-terminal-keybar" role="toolbar" hidden>
            <button class="mobile-terminal-key-drag">⠿</button>
            <div id="smart-key-shell" class="mobile-terminal-keyrow-shell"><div id="smart-key-row" class="mobile-terminal-keyrow mobile-terminal-keyrow--primary"><button class="mobile-terminal-key">Esc</button><button class="mobile-terminal-key active">Ctrl</button><button class="mobile-terminal-key mobile-terminal-key--interrupt">^C</button><button class="mobile-terminal-key">Tab</button><button class="mobile-terminal-key">^B</button></div><button id="smart-key-more" class="mobile-terminal-key mobile-terminal-key--more">⋯</button></div>
            <div id="smart-key-dpad" class="mobile-terminal-key-dpad"><button id="copy" class="mobile-terminal-key mobile-terminal-key--copy">Copy</button><button id="up" class="mobile-terminal-key mobile-terminal-key--arrow-up">↑</button><button id="pg-up" class="mobile-terminal-key mobile-terminal-key--tmux-scroll-up">Pg↑</button><button id="left" class="mobile-terminal-key mobile-terminal-key--arrow-left">←</button><button class="mobile-terminal-key mobile-terminal-key--enter">↵</button><button id="right" class="mobile-terminal-key mobile-terminal-key--arrow-right">→</button><button id="paste" class="mobile-terminal-key mobile-terminal-key--command-v">⌘V</button><button id="down" class="mobile-terminal-key mobile-terminal-key--arrow-down">↓</button><button id="pg-down" class="mobile-terminal-key mobile-terminal-key--tmux-scroll-down">Pg↓</button></div>
            <div id="smart-key-more-row" class="mobile-terminal-keyrow mobile-terminal-keyrow--more" hidden><button class="mobile-terminal-key">⌘P</button><button class="mobile-terminal-key">Home</button><button class="mobile-terminal-key">End</button><button class="mobile-terminal-key">Pg↑</button><button class="mobile-terminal-key">Pg↓</button><button class="mobile-terminal-key">Del</button><button class="mobile-terminal-key">⇧↹</button><button class="mobile-terminal-key">^D</button><button class="mobile-terminal-key">^Z</button><button class="mobile-terminal-key">^L</button><button class="mobile-terminal-key">^R</button><button id="smart-key-more-return" class="mobile-terminal-key mobile-terminal-key--more">⋯</button></div>
          </div>
      </div>
    """, extra_css="""
      body { margin: 0; padding: 0; display: block; height: auto; min-height: 0; }
      #terminal-pane { width: 320px; height: 240px; }
    """),
    )
    metrics = browser.execute_script(
        """
        const box = node => { const rect = node.getBoundingClientRect(); return {left: rect.left, right: rect.right, width: rect.width, height: rect.height, top: rect.top, bottom: rect.bottom}; };
        const pane = document.getElementById('terminal-pane');
        const terminal = document.getElementById('terminal');
        const bar = document.getElementById('smart-keys');
        const row = document.getElementById('smart-key-row');
        const key = row.querySelector('.mobile-terminal-key');
        const hidden = bar.hidden;
        bar.hidden = false;
        bar.style.insetInlineStart = '12px';
        bar.style.insetInlineEnd = 'auto';
        bar.style.insetBlockStart = '12px';
        bar.style.insetBlockEnd = 'auto';
        const dpad = document.getElementById('smart-key-dpad');
        const shell = document.getElementById('smart-key-shell');
        const moreRow = document.getElementById('smart-key-more-row');
        const normal = {bar: box(bar), key: box(key), more: box(document.getElementById('smart-key-more')), shell: box(shell), shellDisplay: getComputedStyle(shell).display, dpadDisplay: getComputedStyle(dpad).display, copy: box(document.getElementById('copy')), paste: box(document.getElementById('paste')), pgUp: box(document.getElementById('pg-up')), pgDown: box(document.getElementById('pg-down')), up: box(document.getElementById('up')), left: box(document.getElementById('left')), right: box(document.getElementById('right')), down: box(document.getElementById('down')), dpad: box(dpad)};
        bar.classList.add('mobile-terminal-keybar--more');
        moreRow.hidden = false;
        const overflow = {bar: box(bar), row: box(moreRow), more: box(document.getElementById('smart-key-more-return')), rowDisplay: getComputedStyle(moreRow).display, shellDisplay: getComputedStyle(shell).display, dpadDisplay: getComputedStyle(dpad).display};
        return {
          pane: box(pane), terminal: box(terminal), bar: normal.bar, key: normal.key, launcher: box(document.getElementById('smart-key-launcher')),
          paneDisplay: getComputedStyle(pane).display,
          overflowX: getComputedStyle(row).overflowX,
          activeBackground: getComputedStyle(row.querySelector('.active')).backgroundColor,
          interruptColor: getComputedStyle(row.querySelector('.mobile-terminal-key--interrupt')).color,
          primaryColumns: getComputedStyle(row).gridTemplateColumns,
          primaryLabels: [...row.querySelectorAll('.mobile-terminal-key')].map(node => node.textContent),
          more: normal.more,
          shell: normal.shell,
          movedInsetEnd: bar.style.insetBlockEnd,
          initiallyHidden: hidden,
              up: normal.up, left: normal.left, right: normal.right, down: normal.down, dpad: normal.dpad,
              copy: normal.copy, paste: normal.paste, pgUp: normal.pgUp, pgDown: normal.pgDown,
          normal, overflow,
        };
        """
    )
    assert metrics["paneDisplay"] == "block", metrics
    assert metrics["initiallyHidden"] is True, metrics
    assert abs(metrics["terminal"]["height"] - metrics["pane"]["height"]) <= 1, metrics
    assert metrics["launcher"]["width"] >= 40 and metrics["launcher"]["height"] >= 40, metrics
    assert metrics["key"]["height"] >= 36, metrics
    assert metrics["overflowX"] == "visible", metrics
    assert metrics["primaryColumns"].startswith("repeat(5,"), metrics
    assert all(label in metrics["primaryLabels"] for label in ["Tab", "^B"]), metrics
    assert metrics["more"]["right"] <= metrics["shell"]["right"] + 0.5, metrics
    assert metrics["more"]["top"] <= metrics["shell"]["top"] + 0.5, metrics
    assert metrics["more"]["left"] >= metrics["key"]["right"] - 0.5, metrics
    assert metrics["movedInsetEnd"] == "auto", metrics
    assert metrics["bar"]["height"] < metrics["pane"]["height"], metrics
    assert metrics["normal"]["shellDisplay"] == "grid" and metrics["normal"]["dpadDisplay"] == "grid", metrics
    assert metrics["overflow"]["rowDisplay"] == "grid", metrics
    assert metrics["overflow"]["shellDisplay"] == "none" and metrics["overflow"]["dpadDisplay"] == "none", metrics
    assert metrics["overflow"]["bar"]["height"] <= metrics["pane"]["height"], metrics
    assert metrics["overflow"]["more"]["right"] <= metrics["overflow"]["row"]["right"] + 0.5, metrics
    assert metrics["overflow"]["more"]["top"] <= metrics["overflow"]["row"]["top"] + 0.5, metrics
    assert metrics["activeBackground"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["interruptColor"] != "rgb(0, 0, 0)", metrics
    assert metrics["up"]["top"] < metrics["left"]["top"] and metrics["up"]["top"] < metrics["right"]["top"], metrics
    assert metrics["left"]["left"] < metrics["up"]["left"] < metrics["right"]["left"], metrics
    assert metrics["down"]["top"] > metrics["left"]["top"] and metrics["down"]["top"] > metrics["right"]["top"], metrics
    assert metrics["copy"]["left"] < metrics["up"]["left"] and metrics["paste"]["left"] < metrics["down"]["left"], metrics
    assert metrics["copy"]["top"] < metrics["left"]["top"] < metrics["paste"]["top"], metrics
    assert metrics["pgUp"]["left"] > metrics["up"]["left"] and metrics["pgDown"]["left"] > metrics["down"]["left"], metrics
    assert metrics["pgUp"]["top"] < metrics["right"]["top"] < metrics["pgDown"]["top"], metrics


def test_phone_single_pane_uses_one_pixel_active_ring_without_changing_tablets(browser, tmp_path):
    page = tmp_path / "phone-single-pane-ring.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <div class="yolomux-dockview" id="dockview">
        <div class="dv-groupview" id="group"><article class="panel active-pane" id="panel"></article></div>
      </div>
    """, extra_css="""
      body { margin: 0; padding: 0; display: block; height: auto; min-height: 0; }
      #dockview, #group { width: 320px; height: 240px; }
    """),
    )
    metrics = browser.execute_script(
        """
        document.documentElement.style.setProperty('--pane-split-gap', '7px');
        const group = document.getElementById('group');
        const panel = document.getElementById('panel');
        const values = () => ({
          groupGap: getComputedStyle(group).getPropertyValue('--pane-split-gap').trim(),
          panelGap: getComputedStyle(panel).getPropertyValue('--pane-split-gap').trim(),
          groupPadding: getComputedStyle(group).paddingTop,
          panelBorder: getComputedStyle(panel).borderTopWidth,
        });
        document.body.classList.add('app-phone-single-pane');
        const phone = values();
        document.body.classList.remove('app-phone-single-pane');
        return {phone, regular: values()};
        """
    )
    assert metrics["phone"] == {"groupGap": "1px", "panelGap": "1px", "groupPadding": "1px", "panelBorder": "1px"}, metrics
    assert metrics["regular"] == {"groupGap": "7px", "panelGap": "7px", "groupPadding": "7px", "panelBorder": "7px"}, metrics


def test_topbar_menu_search_action_priority_matrix(browser, tmp_path):
    """Keep Help visible: the launcher may never be an overlay on top of the menu row."""
    page = tmp_path / "topbar-priority-matrix.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <header id="matrix-topbar" class="topbar">
        <div class="brand-cell"><div class="brand title brand-title"><span class="brand-yolo">YO</span><span class="brand-lo">LO</span><span>mux</span><span class="brand-version"> 0.5.23</span></div></div>
        <div id="matrix-session-buttons" class="app-menu-area">
          <nav class="app-menu-bar">
            <div class="app-menu"><button id="matrix-file" class="app-menu-button">File</button></div>
            <div class="app-menu"><button id="matrix-view" class="app-menu-button">View</button></div>
            <div class="app-menu"><button id="matrix-tmux" class="app-menu-button">tmux</button></div>
            <div class="app-menu"><button id="matrix-tabs" class="app-menu-button">Tabs</button></div>
            <div class="app-menu"><button id="matrix-help" class="app-menu-button">Help</button></div>
          </nav>
          <div id="matrix-center" class="topbar-center-tools">
            <div class="topbar-nav"><button class="topbar-nav-button">←</button><button class="topbar-nav-button">→</button></div>
            <button id="matrix-search" class="topbar-search"><span class="topbar-search-icon">⌕</span><span class="topbar-search-label">Search files</span><kbd class="topbar-search-hint">Cmd-P</kbd></button>
          </div>
          <div id="matrix-right" class="topbar-right-tools"><button class="topbar-language">English</button><button class="topbar-owner-status">IDX: leader</button><button class="topbar-activity">1 running</button></div>
        </div>
        <div id="matrix-actions" class="actions"><button id="matrix-notify">Notify</button><button id="refreshMeta">Refresh</button><button id="matrix-logout">Log out</button></div>
      </header>
    """, extra_css="""
      body { margin: 0; padding: 0; display: block; height: auto; min-height: 0; }
      #matrix-topbar { width: var(--matrix-width); }
    """),
    )

    scenarios = [
        ("narrow-phone", 320, 14, True),
        ("phone", 390, 18, True),
        ("large-phone", 600, 22, True),
        ("small-tablet", 640, 14, False),
        ("portrait-tablet", 680, 18, False),
        ("iPad", 744, 18, False),
        ("wide-iPad", 960, 22, False),
        ("laptop", 1239, 18, False),
        ("desktop", 1440, 18, False),
    ]
    for label, width, font_size, compact in scenarios:
        metrics = browser.execute_script(
            """
            const [width, fontSize, compact] = arguments;
            const body = document.body;
            body.className = 'app-topbar-touch-compact';
            for (const breakpoint of [1280, 1100, 980, 760, 720, 600]) {
              if (width <= breakpoint) body.classList.add(`app-vw-lte-${breakpoint}`);
            }
            if (compact) body.classList.add('app-topbar-menu-compact');
            document.documentElement.style.setProperty('--matrix-width', `${width}px`);
            document.documentElement.style.setProperty('--ui-font-size', `${fontSize}px`);
            const topbar = document.getElementById('matrix-topbar');
            const menuBar = topbar.querySelector('.app-menu-bar');
            if (compact) {
              menuBar.innerHTML = '<div class="app-menu"><button id="matrix-menus" class="app-menu-button">Menus</button></div>';
            } else {
              menuBar.innerHTML = ['File', 'View', 'tmux', 'Tabs', 'Help'].map(name => `<div class="app-menu"><button id="matrix-${name.toLowerCase()}" class="app-menu-button">${name}</button></div>`).join('');
            }
            const rect = node => {
              const box = node.getBoundingClientRect();
              const style = getComputedStyle(node);
              return {left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: box.width, height: box.height, display: style.display};
            };
            const visible = node => node && getComputedStyle(node).display !== 'none';
            const menuNames = compact ? ['menus'] : ['file', 'view', 'tmux', 'tabs', 'help'];
            const menus = menuNames.map(name => rect(document.getElementById(`matrix-${name}`)));
            const search = rect(document.getElementById('matrix-search'));
            const actions = rect(document.getElementById('matrix-actions'));
            const center = rect(document.getElementById('matrix-center'));
            return {
              header: rect(topbar),
              menuArea: rect(document.getElementById('matrix-session-buttons')),
              menus,
              search,
              center,
              actions,
              refresh: rect(document.getElementById('refreshMeta')),
              notifyVisible: visible(document.getElementById('matrix-notify')),
              searchVisible: visible(document.getElementById('matrix-search')),
            };
            """,
            width,
            font_size,
            compact,
        )
        assert metrics["header"]["height"] < 60, (label, metrics)
        assert metrics["menus"][0]["left"] >= metrics["menuArea"]["left"] - 1, (label, metrics)
        assert metrics["menus"][-1]["right"] <= metrics["header"]["right"] + 1, (label, metrics)
        for left, right in zip(metrics["menus"], metrics["menus"][1:]):
            assert left["right"] <= right["left"] + 1, (label, metrics)
        if compact:
            assert metrics["searchVisible"] is True, (label, metrics)
            assert metrics["menus"][0]["right"] <= metrics["search"]["left"] + 1, (label, metrics)
            assert metrics["search"]["right"] <= metrics["actions"]["left"] + 1, (label, metrics)
        else:
            assert metrics["menus"][-1]["right"] <= metrics["center"]["left"] + 1, (label, metrics)
            assert metrics["search"]["right"] <= metrics["actions"]["left"] + 1, (label, metrics)
            assert metrics["refresh"]["display"] == "grid", (label, metrics)
        if width <= 980:
            assert metrics["notifyVisible"] is False, (label, metrics)

    # On a pointer desktop the center wrapper owns the actual space between Help and the right
    # chrome.  Search therefore grows beyond its label-sized shell and the nav/search pair is
    # centered in that free region without covering either neighbor.
    desktop = browser.execute_script(
        """
        document.body.className = '';
        document.documentElement.style.setProperty('--matrix-width', '1440px');
        document.documentElement.style.setProperty('--ui-font-size', '18px');
        document.querySelector('.app-menu-bar').innerHTML = ['File', 'View', 'tmux', 'Tabs', 'Help']
          .map(name => `<div class="app-menu"><button class="app-menu-button">${name}</button></div>`).join('');
        const rect = node => {
          const box = node.getBoundingClientRect();
          return {left: box.left, right: box.right, width: box.width, center: (box.left + box.right) / 2};
        };
        const menu = document.querySelector('.app-menu-bar');
        const center = document.getElementById('matrix-center');
        const nav = center.querySelector('.topbar-nav');
        const search = document.getElementById('matrix-search');
        const right = document.getElementById('matrix-right');
        const actions = document.getElementById('matrix-actions');
        return {menu: rect(menu), center: rect(center), nav: rect(nav), search: rect(search), right: rect(right), actions: rect(actions)};
        """
    )
    assert desktop["menu"]["right"] <= desktop["center"]["left"] + 1, desktop
    assert desktop["search"]["right"] <= desktop["right"]["left"] + 1, desktop
    assert desktop["right"]["right"] <= desktop["actions"]["left"] + 1, desktop
    assert desktop["search"]["width"] >= 360, desktop
    pair_center = (desktop["nav"]["left"] + desktop["search"]["right"]) / 2
    assert abs(pair_center - desktop["center"]["center"]) <= 1, desktop


def test_touch_pane_tab_close_uses_a_large_hit_target(browser, tmp_path):
    page = tmp_path / "touch-pane-tab-close.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <div class="pane-tab"><button id="touch-tab-close" class="pane-tab-close pc-window-control pc-minimize" type="button"></button></div>
    """, extra_css="body { margin: 0; padding: 12px; display: block; height: auto; min-height: 0; }"),
    )
    metrics = browser.execute_script(
        """
        document.body.classList.add('app-topbar-touch-compact');
        const control = document.getElementById('touch-tab-close');
        const style = getComputedStyle(control);
        let clicks = 0;
        control.addEventListener('click', () => { clicks += 1; });
        control.click();
        return {width: control.getBoundingClientRect().width, height: control.getBoundingClientRect().height, touchAction: style.touchAction, clicks};
        """
    )
    assert metrics["width"] >= 36 and metrics["height"] >= 36, metrics
    assert metrics["touchAction"] == "manipulation", metrics
    assert metrics["clicks"] == 1, metrics


def test_narrow_server_update_banner_stacks_message_and_actions(browser, tmp_path):
    page = tmp_path / "narrow-server-update-banner.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <div id="serverUpdateBanner" class="server-update-banner">
        <span class="server-update-banner-msg">The YOLOmux server version changed since this browser tab loaded. Do you want to reload the browser?</span>
        <div class="toast-control-row server-update-banner-actions"><button class="server-update-banner-reload">Reload</button><button class="server-update-banner-dismiss">Keep</button></div>
      </div>
    """),
    )
    metrics = browser.execute_script(
        """
        document.body.classList.add('app-vw-lte-760');
        const rect = selector => {
          const box = document.querySelector(selector).getBoundingClientRect();
          return {left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: box.width, height: box.height};
        };
        const message = rect('.server-update-banner-msg');
        const actions = rect('.server-update-banner-actions');
        const reload = rect('.server-update-banner-reload');
        const keep = rect('.server-update-banner-dismiss');
        return {message, actions, reload, keep, banner: rect('#serverUpdateBanner')};
        """
    )
    assert metrics["message"]["bottom"] <= metrics["actions"]["top"] + 0.5, metrics
    assert metrics["actions"]["top"] - metrics["message"]["bottom"] <= 16, metrics
    assert metrics["reload"]["right"] <= metrics["keep"]["left"] + 0.5, metrics
    assert metrics["actions"]["right"] <= metrics["banner"]["right"] + 0.5, metrics
    assert metrics["banner"]["height"] <= metrics["message"]["height"] + metrics["actions"]["height"] + 48, metrics


def test_topbar_owner_status_shows_index_and_stats_roles(browser, tmp_path):
    background_status = {
        "owner": False,
        "status": "follower",
        "generation": {"hostname": "devhost", "port": 8001, "project_root": "/home/test/yolomux.dev8001", "pid": 111},
        "current_owner": {"hostname": "devhost", "port": 8002, "project_root": "/home/test/yolomux.dev8002", "pid": 222},
        "roles": {
            "search-index": {"role": "search-index", "owner": True, "status": "owner"},
            "stats-sampler": {"role": "stats-sampler", "owner": False, "status": "follower"},
            "session-files": {"role": "session-files", "owner": False, "status": "follower"},
        },
        "search_index": {
            "role": "search-index",
            "owner": True,
            "mode": "indexing-server",
            "current_server": {"hostname": "devhost", "port": 8001, "project_root": "/home/test/yolomux.dev8001", "pid": 111},
            "owner_server": {"hostname": "devhost", "port": 8001, "project_root": "/home/test/yolomux.dev8001", "pid": 111},
            "status": "owner",
        },
    }
    auto_approve_payload = {
        "session_order": ["1"],
        "sessions": {"1": {"target": "1", "enabled": True, "screen": {"key": "idle"}, "agent_windows": [{"kind": "codex", "state": "idle", "window_index": 0, "window_label": "0:codex"}]}},
        "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
    }
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"], auto_approve_payload=auto_approve_payload, background_status_payload=background_status)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const owner = document.getElementById('topbarOwnerStatus');
            return owner && owner.textContent.includes('IDX|STATS|SESS');
            """
        )
    )
    metrics = browser.execute_script(
        """
        const language = document.querySelector('.topbar-language');
        const owner = document.getElementById('topbarOwnerStatus');
        const activity = document.getElementById('topbarActivity');
        const position = (a, b) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
        const rect = node => {
          const box = node.getBoundingClientRect();
          return {left: box.left, right: box.right, width: box.width};
        };
        return {
          text: owner.textContent.replace(/\\s+/g, ' ').trim(),
          title: owner.title,
          sharedRole: owner.querySelector('.topbar-owner-status-shared')?.dataset.ownerRole || '',
          languageBeforeOwner: position(language, owner),
          ownerBeforeActivity: position(owner, activity),
          ownerRect: rect(owner),
          activityRect: rect(activity),
        };
        """
    )
    assert "IDX|STATS|SESS: follower" in metrics["text"], metrics
    assert metrics["sharedRole"] == "follower"
    assert metrics["languageBeforeOwner"] is True
    assert metrics["ownerBeforeActivity"] is True
    assert metrics["ownerRect"]["right"] <= metrics["activityRect"]["left"] + 1
    assert "STATS leader: devhost:8002" in metrics["title"]
    assert "SESS leader: devhost:8002" in metrics["title"]

@pytest.mark.e2e
def test_real_agent_prompts_render_ask_attention_in_live_server(browser, monkeypatch, tmp_path):
    if os.environ.get("YOLOMUX_REAL_AGENT_SMOKE") != "1":
        pytest.skip("set YOLOMUX_REAL_AGENT_SMOKE=1 to run real Claude/Codex prompt smoke")
    codex_binary = shutil.which("codex")
    claude_binary = shutil.which("claude")
    if not codex_binary:
        pytest.skip("codex is not installed")
    if not claude_binary:
        pytest.skip("claude is not installed")

    paths = isolate_browser_runtime_paths(monkeypatch, tmp_path)
    sessions = {
        "codex": f"yr-codex-{os.getpid()}-{uuid.uuid4().hex[:6]}",
        "claude": f"yr-claude-{os.getpid()}-{uuid.uuid4().hex[:6]}",
    }
    tmux_runtime = None

    def wait_for_codex_sleep_prompt(session, timeout=120):
        extra_submit_sent = False
        def prompted(panes):
            nonlocal extra_submit_sent
            text = panes[session]
            if "Would you like to run the following" in text and "sleep 10" in text:
                return True
            if not extra_submit_sent and "› Run sleep 10" in text:
                run_isolated_tmux(tmux_runtime, "send-keys", "-t", f"{session}:", "C-m")
                extra_submit_sent = True
            return False
        ready, panes = wait_for_isolated_tmux_panes(tmux_runtime, [session], prompted, timeout=timeout)
        return ready, panes.get(session, "")

    def wait_for_claude_plan_prompt(session, timeout=120):
        extra_submit_sent = False
        def prompted(panes):
            nonlocal extra_submit_sent
            text = panes[session]
            if "Claude has written up a plan" in text and "Would you like to proceed?" in text:
                return True
            if not extra_submit_sent and "Add a temporary line to README.md" in text:
                run_isolated_tmux(tmux_runtime, "send-keys", "-t", f"{session}:", "C-m")
                extra_submit_sent = True
            return False
        ready, panes = wait_for_isolated_tmux_panes(tmux_runtime, [session], prompted, timeout=timeout)
        return ready, panes.get(session, "")

    app = None
    server = None
    thread = None
    try:
        codex_command = (
            f"cd {REPO_ROOT} && exec codex --no-alt-screen "
            f"--ask-for-approval untrusted --sandbox read-only -C {REPO_ROOT}"
        )
        claude_command = f"cd {REPO_ROOT} && exec claude --permission-mode plan --safe-mode"
        tmux_runtime = start_isolated_tmux_runtime(
            monkeypatch,
            tmp_path,
            session_commands={sessions["codex"]: codex_command, sessions["claude"]: claude_command},
            columns=120,
            rows=40,
        )
        codex_ready, codex_panes = wait_for_isolated_tmux_panes(
            tmux_runtime,
            [sessions["codex"]],
            lambda panes: "›" in panes[sessions["codex"]] or "Codex" in panes[sessions["codex"]],
            timeout=45,
        )
        codex_pane = codex_panes.get(sessions["codex"], "")
        assert codex_ready, f"Codex did not reach an input prompt:\n{codex_pane}"
        claude_ready, claude_panes = wait_for_isolated_tmux_panes(
            tmux_runtime,
            [sessions["claude"]],
            lambda panes: "❯" in panes[sessions["claude"]] or "Claude Code" in panes[sessions["claude"]],
            timeout=45,
        )
        claude_pane = claude_panes.get(sessions["claude"], "")
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
              topbarAskCount: document.querySelector('#topbarActivity .topbar-activity-ask .topbar-activity-count-number')?.textContent || '',
            };
            """,
            list(sessions.values()),
        )
        assert initial_ui["ask"] == 0, initial_ui
        assert initial_ui["badges"] == ["", ""], initial_ui

        run_isolated_tmux(tmux_runtime, "send-keys", "-t", f"{sessions['codex']}:", "Run sleep 10", "Enter")
        codex_prompted, codex_pane = wait_for_codex_sleep_prompt(sessions["codex"])
        assert codex_prompted, f"Codex did not render the real sleep approval prompt:\n{codex_pane}"

        run_isolated_tmux(tmux_runtime, "send-keys", "-t", f"{sessions['claude']}:", "Add a temporary line to README.md, then wait for approval before editing", "Enter")
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

        prompted_ui = browser.execute_async_script(
            """
            const sessions = arguments[0];
            const done = arguments[arguments.length - 1];
            (async () => {
              const prompted = await window.__yolomuxTestWaitFor(async () => {
                  await refreshAutoStatuses();
                  refreshActivePanelHeaders();
                  updateTopbarActivityStatus();
                  const counts = globalActivityCounts();
                  const snapshot = {
                    ok: counts.ask === sessions.length,
                    counts,
                    topbar: document.getElementById('topbarActivity')?.textContent || '',
                    topbarAskCount: document.querySelector('#topbarActivity .topbar-activity-ask .topbar-activity-count-number')?.textContent || '',
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
                  };
                  return snapshot.ok ? snapshot : false;
              }, {timeoutMs: 15000, intervalMs: 500, description: 'real agent prompt attention UI'});
              done(prompted);
            })().catch(error => done({ok: false, error: String(error && error.stack || error)}));
            """,
            list(sessions.values()),
        )
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
                topbarAskCount: document.querySelector('#topbarActivity .topbar-activity-ask .topbar-activity-count-number')?.textContent || '',
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
              clearPromptAttentionForSession(session, {delayMs: agentWindowActivityAcknowledgeDelayMs, localOnly: true});
            }
            const immediate = snapshot();
            const inputFrames = (window.__askClearWsFrames || []).filter(frame => frame.data.includes('"type":"input"'));
            return {before, immediate, inputFrames};
            """,
            list(sessions.values()),
        )
        assert metrics["before"]["ask"] == 2, metrics
        assert metrics["before"]["topbarAskCount"] == "2", metrics
        assert [item["badge"] for item in metrics["before"]["sessions"]] == ["", ""], metrics
        assert [item["tabAttention"] for item in metrics["before"]["sessions"]] == [True, True], metrics
        assert [item["panelNeedsApproval"] for item in metrics["before"]["sessions"]] == [True, True], metrics
        assert metrics["immediate"]["ask"] == 2, metrics
        assert metrics["immediate"]["topbarAskCount"] == "2", metrics
        assert [item["badge"] for item in metrics["immediate"]["sessions"]] == ["", ""], metrics
        assert [item["tabAttention"] for item in metrics["immediate"]["sessions"]] == [True, True], metrics
        assert [item["panelNeedsApproval"] for item in metrics["immediate"]["sessions"]] == [True, True], metrics
        assert metrics["inputFrames"] == [], metrics
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                const sessions = arguments[0];
                return globalActivityCounts().ask === 0
                  && document.querySelector('#topbarActivity .topbar-activity-ask .topbar-activity-count-number')?.textContent === '0'
                  && sessions.every(session => {
                    const tab = document.getElementById(`panel-tab-${session}`);
                    const panel = document.getElementById(`panel-${session}`);
                    return (tab?.querySelector('[data-prompt-attention-clear]')?.textContent || '') === ''
                      && !tab?.classList.contains('needs-attention')
                      && !panel?.classList.contains('needs-exec-pane');
                  });
                """,
                list(sessions.values()),
            )
        )
    finally:
        if server is not None and thread is not None:
            stop_browser_share_server(server, thread)
        elif app is not None:
            app.control_server.stop()
        stop_isolated_tmux_runtime(tmux_runtime)
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
                if (/^\\/api\\/yoagent\\/chat\\/.+\\/cancel$/.test(url.pathname)) {
                  return Promise.resolve(new Response(JSON.stringify({ok: true, cancelled: true}), {status: 200, headers: {'Content-Type': 'application/json'}}));
                }
                return originalFetch(input, options);
              };
              const sendPromise = sendYoagentChatMessage('summarize activity');
              await raf();
              const active = yoagentChatState.activeRequest ? {id: yoagentChatState.activeRequest.id, streamId: yoagentChatState.activeRequest.streamId} : null;
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
                stream_items: [
                  {
                    kind: 'thinking',
                    text: 'thinking: scanning recent events\\nthinking: reading activity context\\nthinking: final synthesis',
                    eventKind: 'hidden_work_delta',
                    labelKey: 'yoagent.stream.thinking',
                    labelParams: {},
                    fallback: 'thinking',
                    sourceIndex: 0,
                  },
                  {
                    kind: 'tool',
                    text: 'tool output: command: collected files',
                    eventKind: 'tool_output',
                    labelKey: 'yoagent.stream.toolOutput',
                    labelParams: {tool: 'command'},
                    fallback: 'tool output: command',
                    toolName: 'command',
                    sourceIndex: 1,
                  },
                ],
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
                activeRequest: yoagentChatState.activeRequest ? {id: yoagentChatState.activeRequest.id, streamId: yoagentChatState.activeRequest.streamId} : null,
                busy: yoagentChatState.busy === true,
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
        assert metrics["auxPreviewText"] == "thinking: scanning recent events thinking: reading activity context thinking: final synthesis", (label, metrics)
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
        load_static_html_fixture(
            browser,
            page.parent,
            page.name,
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
        )
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
        if theme_class == "theme-light":
            assert _contrast_ratio(metrics["bodyColor"], "rgb(255, 255, 255)") >= 12.0, metrics
            assert _contrast_ratio(metrics["auxColor"], "rgb(247, 248, 250)") >= 7.0, metrics
            assert _contrast_ratio(metrics["previewColor"], "rgb(255, 255, 255)") >= 7.0, metrics


def test_light_agent_status_chart_uses_vibrant_shared_status_tokens(browser, tmp_path):
    page = tmp_path / "light-agent-status-chart.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(
            """
            <script>document.body.className = 'theme-light';</script>
            <section class="js-debug-graph-view">
              <svg>
                <path id="ask-line" class="js-debug-line--askAgents"></path>
                <path id="working-line" class="js-debug-line--workingAgents"></path>
                <path id="transition-line" class="js-debug-line--transitionAgents"></path>
                <path id="idle-line" class="js-debug-line--idleAgents"></path>
                <rect id="ask-bar" class="js-debug-bar--askAgents"></rect>
              </svg>
              <span id="ask-legend" class="js-debug-legend-swatch--askAgents"></span>
              <span id="working-legend" class="js-debug-legend-swatch--workingAgents"></span>
              <span id="transition-legend" class="js-debug-legend-swatch--transitionAgents"></span>
            </section>
            """,
            extra_css="body { margin: 0; background: #fff; }",
        ),
    )
    metrics = browser.execute_script(
        """
        const style = id => getComputedStyle(document.getElementById(id));
        return {
          ask: style('ask-line').stroke,
          working: style('working-line').stroke,
          transition: style('transition-line').stroke,
          idle: style('idle-line').stroke,
          askLegend: style('ask-legend').color,
          workingLegend: style('working-legend').color,
          transitionLegend: style('transition-legend').color,
          askBarOpacity: style('ask-bar').opacity,
        };
        """
    )
    assert metrics["ask"] == metrics["askLegend"], metrics
    assert metrics["working"] == metrics["workingLegend"], metrics
    assert metrics["transition"] == metrics["transitionLegend"], metrics
    assert len({metrics["ask"], metrics["working"], metrics["transition"], metrics["idle"]}) == 4, metrics
    assert metrics["askBarOpacity"] == "0.82", metrics


def test_subwindow_pid_and_recency_use_shared_subtle_color_in_each_theme(browser, tmp_path):
    for theme_class in ("theme-dark", "theme-light"):
        page = tmp_path / f"subwindow-metadata-{theme_class}.html"
        load_static_html_fixture(
            browser,
            page.parent,
            page.name,
            page_html(
                f"""
                <script>document.body.className = {json.dumps(theme_class)};</script>
                <span id="info-pid" class="info-tree-ai-pid">(pid=2345)</span><span id="info-recency" class="info-tree-ai-recency info-tree-trailing-meta">3.1 hrs ago</span>
                <div class="file-tree-row tabber-row" data-tabber-type="window" data-recency="recent"><span class="tabber-window-pid" id="tabber-pid"> (pid=2345)</span><span class="file-tree-date" id="tabber-recency">3.1 hrs ago</span></div>
                """,
                extra_css="body { margin: 0; padding: 20px; background: var(--bg); }",
            ),
        )
        metrics = browser.execute_script(
            """
            const color = id => getComputedStyle(document.getElementById(id)).color;
            return {infoPid: color('info-pid'), infoRecency: color('info-recency'), tabberPid: color('tabber-pid'), tabberRecency: color('tabber-recency')};
            """
        )
        if theme_class == "theme-dark":
            assert set(metrics.values()) == {"rgb(102, 112, 133)"}, metrics
        else:
            assert set(metrics.values()) == {"rgb(158, 168, 183)"}, metrics


def test_in_page_notification_titles_omit_external_yolomux_context(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof showAttentionAlert === 'function' && typeof showTerminalConnectionToast === 'function' && typeof sendTestNotification === 'function';"
        )
    )
    metrics = browser.execute_script(
        """
        document.querySelectorAll('.toast').forEach(node => node.remove());
        const state = {label: 'Needs input', reason: 'Please answer the question'};
        const externalTitle = sessionNotificationTitle('1', state);
        const internalTitle = sessionNotificationTitle('1', state, {inApp: true});
        showAttentionAlert('1', state);
        const genericAttentionTitle = document.querySelector('.toast[data-toast-kind="attention"] .toast-title')?.textContent?.trim() || '';
        document.querySelectorAll('.toast[data-toast-kind="attention"]').forEach(node => node.remove());
        autoApproveStates.set('1', {agent_windows: [{kind: 'claude', state: 'approval', window_index: 0, window_label: '0:claude', screen_text: 'Do you want to proceed with this change? Explain the expected behavior, edge cases, and verification steps before continuing with the requested operation.'}]});
        const agentState = sessionState('1');
        showAttentionAlert('1', agentState);
        showTerminalConnectionToast('1', 'Disconnected', 5000);
        sendTestNotification();
        const title = selector => document.querySelector(selector)?.textContent?.trim() || '';
        const attentionControls = document.querySelector('.toast[data-toast-kind="attention"] .attention-toast-controls');
        const attentionTab = document.querySelector('.toast[data-toast-kind="attention"] .attention-toast-session-tab');
        const attentionPill = document.querySelector('.toast[data-toast-kind="attention"] .attention-toast-agent-button');
        const attentionReason = document.querySelector('.toast[data-toast-kind="attention"] .attention-toast-reason');
        const controlsRect = attentionControls?.getBoundingClientRect();
        const reasonRects = [...(attentionReason?.getClientRects?.() || [])];
        const untyped = [...document.querySelectorAll('.toast:not([data-toast-kind]) .toast-title')];
        return {
          externalTitle,
          internalTitle,
          genericAttentionTitle,
          attentionTitle: title('.toast[data-toast-kind="attention"] .toast-title'),
          attentionTabLabel: title('.toast[data-toast-kind="attention"] .attention-toast-session-tab .session-button-prefix'),
          attentionAgentLabel: title('.toast[data-toast-kind="attention"] .attention-toast-agent-button .tmux-window-name-text'),
          attentionReason: title('.toast[data-toast-kind="attention"] .attention-toast-reason'),
          attentionHasStop: Boolean(document.querySelector('.toast[data-toast-kind="attention"] .attention-toast-agent-button .status-indicator--attention')),
          attentionControlsVisible: Boolean(controlsRect && controlsRect.width > 0 && controlsRect.height > 0 && attentionTab && attentionPill),
          attentionReasonWrapsAroundControls: Boolean(
            controlsRect
            && reasonRects.length > 1
            && reasonRects[0].left >= controlsRect.right - 1
            && reasonRects.some(rect => rect.top >= controlsRect.bottom - 1 && rect.left <= controlsRect.left + 1)
          ),
          terminalTitle: title('.toast[data-toast-kind="terminal-connection"] .toast-title'),
          testTitle: untyped.at(-1)?.textContent?.trim() || '',
        };
        """
    )
    assert metrics == {
        "externalTitle": "YOLOmux[1 main] Needs input",
        "internalTitle": "Needs input",
        "genericAttentionTitle": "Needs input",
        "attentionTitle": "[1] 0:claude: Needs approval",
        "attentionTabLabel": "[1]",
        "attentionAgentLabel": "0:claude",
        "attentionReason": "Do you want to proceed with this change? Explain the expected behavior, edge cases, and verification steps before continuing with the requested operation.",
        "attentionHasStop": True,
        "attentionControlsVisible": True,
        "attentionReasonWrapsAroundControls": True,
        "terminalTitle": "Term",
        "testTitle": "notifications enabled",
    }, metrics
    navigation = browser.execute_async_script(
        """
        const done = arguments[0];
        const calls = [];
        const originalSelectSession = selectSession;
        const originalTmuxWindow = tmuxWindow;
        selectSession = async (...args) => { calls.push(['select', args[0], args[1]?.userInitiated === true]); };
        tmuxWindow = (...args) => { calls.push(['window', args[0], args[1]?.windowIndex, args[2]]); };
        document.querySelector('.toast[data-toast-kind="attention"]').click();
        setTimeout(() => {
          selectSession = originalSelectSession;
          tmuxWindow = originalTmuxWindow;
          done(calls);
        }, 0);
        """
    )
    assert navigation == [["select", "1", True], ["window", "1", "0", "tmux sub-window 0"]], navigation


def test_hovered_toast_pauses_countdown(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof showToast === 'function';")
    )
    result = browser.execute_async_script(
        """
        const done = arguments[0];
        const toast = showToast('Needs input', 'Do you want to proceed?', {countdownMs: 120});
        const countdown = toast.querySelector('.toast-line');
        toast.dispatchEvent(new Event('pointerenter'));
        toast.dispatchEvent(new FocusEvent('focusin'));
        setTimeout(() => {
          const held = toast.isConnected;
          const paused = toast.classList.contains('toast-countdown-paused')
            && getComputedStyle(countdown, '::after').animationPlayState === 'paused';
          toast.dispatchEvent(new Event('pointerleave'));
          setTimeout(() => {
            const heldAfterPointerLeave = toast.isConnected;
            toast.dispatchEvent(new FocusEvent('focusout', {relatedTarget: document.body}));
            setTimeout(() => done({held, paused, heldAfterPointerLeave, removed: !toast.isConnected}), 180);
          }, 180);
        }, 180);
        """
    )
    assert result == {"held": True, "paused": True, "heldAfterPointerLeave": True, "removed": True}, result


def test_browser_notifications_use_shared_badge_free_yolomux_icon(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof sendBrowserNotification === 'function' && typeof renderBrowserAppIconDataUrl === 'function';"
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const sent = [];
        Object.defineProperty(window, 'Notification', {
          configurable: true,
          value: function(title, options) {
            sent.push({title, options});
            return {};
          },
        });
        sendBrowserNotification('YOLOmux[test] Needs input', {body: 'Please answer'});
        sendBrowserNotification('custom', {icon: 'data:image/png;base64,custom'});
        const image = new Image();
        image.onload = () => done({
          first: sent[0],
          second: sent[1],
          width: image.naturalWidth,
          height: image.naturalHeight,
        });
        image.onerror = () => done({error: 'icon did not decode', first: sent[0]});
        image.src = sent[0]?.options?.icon || '';
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["first"]["title"] == "YOLOmux[test] Needs input", metrics
    assert metrics["first"]["options"]["body"] == "Please answer", metrics
    assert metrics["first"]["options"]["icon"].startswith("data:image/png;base64,"), metrics
    assert metrics["second"]["options"]["icon"] == "data:image/png;base64,custom", metrics
    assert (metrics["width"], metrics["height"]) == (192, 192), metrics


def test_rename_marks_index_building_and_refresh_done_requeries_open_search(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof renameFileTreePath === 'function' && typeof markFileIndexRootsRefreshing === 'function' && typeof handleClientPushEventNow === 'function';"
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const requests = [];
        const retries = [];
        apiFetchJson = async url => {
          requests.push(url);
          if (url === '/api/fs/rename') return {path: '/repo/home-manifest', reindex_roots: ['/repo']};
          if (url.startsWith('/api/fs/index-status')) return {state: 'building', ready: false};
          return {};
        };
        refreshFileExplorerTrees = async () => {};
        refreshBackgroundOwnerStatus = async () => {};
        fileExplorerIndexedDirs = new Set(['/repo']);
        fileExplorerIndexStatus.set('/repo', 'ready');
        renameFileTreePath('/repo/migration-tools', {name: 'migration-tools', kind: 'dir'}, 'home-manifest').then(async renamed => {
          const waitFor = window.__yolomuxTestWaitFor;
          await waitFor(
            () => fileExplorerIndexStatus.get('/repo') === 'building',
            {description: 'renamed root index building'}
          );
          commandPaletteState.node = document.createElement('div');
          commandPaletteState.node.hidden = false;
          commandPaletteMode = 'files';
          commandPaletteState.query = 'home-manifest';
          refreshFileQuickOpenCandidates = async query => { retries.push(query); };
          handleClientPushEventNow('background_refresh_done', {role: 'search-index', root: '/repo'});
          await Promise.resolve();
          done({
            renamed,
            indexStatus: fileExplorerIndexStatus.get('/repo'),
            indexStatusRequested: requests.some(url => url.startsWith('/api/fs/index-status')),
            retries,
          });
        }).catch(error => done({error: String(error)}));
        """
    )
    assert metrics == {
        "renamed": True,
        "indexStatus": "building",
        "indexStatusRequested": True,
        "retries": ["home-manifest"],
    }, metrics


def test_yoinfo_reuses_tab_badges_and_right_aligns_trailing_metadata(browser, tmp_path):
    page = tmp_path / "yoinfo-badges-and-trailing-metadata.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(
            f"""
            <script>document.body.className = 'theme-dark';</script>
            <div class="info-tree-record" style="width: 760px"><div class="info-tree-record-main">
              <div class="info-tree-field info-tree-field-tab"><span class="info-tree-field-label">Tab(tmux session):</span><span id="tab-value" class="info-tree-field-value">8001</span></div>
              <div class="info-tree-field info-tree-field-ai"><span class="info-tree-field-label">tmux sub-window:</span><span id="window-value" class="info-tree-field-value"><span class="info-tree-ai-value tmux-window-bar"><button id="window-button" class="tab tmux-window-button"><span class="tmux-window-name-label">{_working_agent_glyph_html("claude", "yoinfo-agent", subwindow=True)}<span class="tmux-window-name-text">0:claude</span></span></button><span id="window-pid" class="info-tree-ai-pid">(pid=1234)</span><span id="window-time" class="info-tree-ai-recency info-tree-trailing-meta">3.1 hrs ago</span></span></span></div>
              <div class="info-tree-field info-tree-field-branch"><span class="info-tree-field-label">Git branch:</span><span id="branch-value" class="info-tree-field-value"><span class="info-tree-value-text">master</span><span id="branch-time" class="info-tree-meta-updated">Git commit 3 hours ago</span></span></div>
              <div class="info-tree-field info-tree-field-pr"><span class="info-tree-field-label">GitHub PR:</span><span class="info-tree-field-value"><span>#80 parser work</span> <span id="draft-badge" class="ci-indicator tab-symbol pr-status-draft">DRAFT</span></span></div>
            </div></div>
            """,
            extra_css="body { margin: 0; padding: 20px; background: var(--bg); }",
        ),
    )
    metrics = browser.execute_script(
        """
        const rightInset = (containerId, itemId) => {
          const container = document.getElementById(containerId).getBoundingClientRect();
          const item = document.getElementById(itemId).getBoundingClientRect();
          return container.right - item.right;
        };
        const badge = document.getElementById('draft-badge');
        const style = getComputedStyle(badge);
        const agentIcon = document.getElementById('yoinfo-agent');
        const agentIconStyle = getComputedStyle(agentIcon);
        const agentWrapStyle = getComputedStyle(agentIcon.closest('.agent-window-activity'));
        return {
          tabTop: document.getElementById('tab-value').getBoundingClientRect().top,
          windowTop: document.getElementById('window-value').getBoundingClientRect().top,
          branchTop: document.getElementById('branch-value').getBoundingClientRect().top,
          branchRightInset: rightInset('branch-value', 'branch-time'),
          windowRightInset: rightInset('window-value', 'window-time'),
          windowPidOffset: document.getElementById('window-pid').getBoundingClientRect().left - document.getElementById('window-button').getBoundingClientRect().right,
          badgeClasses: badge.className,
          badgeBackground: style.backgroundColor,
          badgeBorder: style.borderTopColor,
          agentIconSizeToken: agentWrapStyle.getPropertyValue('--agent-window-icon-size').trim(),
          agentIconWidth: agentIconStyle.width,
          agentIconMinWidth: agentIconStyle.minWidth,
          agentIconHeight: agentIconStyle.height,
        };
        """
    )
    assert metrics["tabTop"] < metrics["windowTop"], metrics
    assert metrics["windowTop"] < metrics["branchTop"], metrics
    assert metrics["branchRightInset"] <= 1, metrics
    assert metrics["windowRightInset"] <= 1, metrics
    assert 0 <= metrics["windowPidOffset"] <= 8, metrics
    assert metrics["agentIconSizeToken"] == "14px", metrics
    assert metrics["agentIconWidth"] == metrics["agentIconMinWidth"] == metrics["agentIconHeight"] == "14px", metrics
    assert "ci-indicator" in metrics["badgeClasses"] and "tab-symbol" in metrics["badgeClasses"], metrics
    assert metrics["badgeBackground"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["badgeBorder"] != "rgba(0, 0, 0, 0)", metrics


def test_yoinfo_path_activity_is_dim_right_aligned_trailing_metadata(browser, tmp_path):
    page = tmp_path / "yoinfo-path-trailing-metadata.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(
            """
            <script>document.body.className = 'theme-dark';</script>
            <div class="info-tree" style="width: 900px">
              <details class="info-tree-group info-tree-item info-tree-item-last" data-info-dimension="path" data-info-depth="0" open>
                <summary>
                  <span class="info-tree-group-dimension">PATH:</span>
                  <span id="group-line" class="info-tree-group-label-line">
                    <span class="info-tree-group-label info-tree-group-label-path"><button id="group-path" class="info-tree-group-label-action">/home/keivenc/dynamo/frontend-crates</button></span>
                    <span id="group-count" class="info-tree-group-child-count">(6 branches)</span>
                    <span id="group-time" class="info-tree-meta-updated info-tree-meta-path-activity info-tree-trailing-meta">2 hours ago</span>
                  </span>
                </summary>
              </details>
            </div>
            <div class="info-tree-record" style="width: 900px"><div class="info-tree-record-main">
              <div class="info-tree-field info-tree-field-ai"><span class="info-tree-field-label">tmux sub-window:</span><span class="info-tree-field-value"><span class="info-tree-ai-value tmux-window-bar"><button class="tab tmux-window-button">0:claude</button><span id="window-time" class="info-tree-ai-recency info-tree-trailing-meta">40 min ago</span></span></span></div>
              <div class="info-tree-field info-tree-field-path"><span class="info-tree-field-label">path:</span><span id="path-value" class="info-tree-field-value"><button id="leaf-path" class="info-tree-action-link info-tree-action-link-path">/home/keivenc/dynamo/dynamo-utils.dev</button><span id="path-time" class="info-tree-meta-updated info-tree-meta-path-activity info-tree-trailing-meta">5 hours ago</span></span></div>
            </div></div>
            """,
            extra_css="body { margin: 0; padding: 20px; background: var(--bg); }",
        ),
    )
    metrics = browser.execute_script(
        """
        const rect = id => document.getElementById(id).getBoundingClientRect();
        const style = id => getComputedStyle(document.getElementById(id));
        const groupLine = rect('group-line');
        const groupPath = rect('group-path');
        const groupCount = rect('group-count');
        const groupTime = rect('group-time');
        const pathValue = rect('path-value');
        const pathTime = rect('path-time');
        return {
          groupTimeRightInset: groupLine.right - groupTime.right,
          groupCountGap: groupCount.left - groupPath.right,
          groupTrailingSpace: groupTime.left - groupCount.right,
          pathTimeRightInset: pathValue.right - pathTime.right,
          groupTimeColor: style('group-time').color,
          pathTimeColor: style('path-time').color,
          windowTimeColor: style('window-time').color,
          pathTextColor: style('leaf-path').color,
          groupTimeFontSize: style('group-time').fontSize,
          pathTimeFontSize: style('path-time').fontSize,
          windowTimeFontSize: style('window-time').fontSize,
          pathTimeWhiteSpace: style('path-time').whiteSpace,
        };
        """
    )
    assert abs(metrics["groupTimeRightInset"]) <= 1, metrics
    assert 4 <= metrics["groupCountGap"] <= 8, metrics
    assert metrics["groupTrailingSpace"] > 50, metrics
    assert abs(metrics["pathTimeRightInset"]) <= 1, metrics
    assert metrics["groupTimeColor"] == metrics["pathTimeColor"] == metrics["windowTimeColor"], metrics
    assert metrics["pathTimeColor"] != metrics["pathTextColor"], metrics
    assert metrics["groupTimeFontSize"] == metrics["pathTimeFontSize"] == metrics["windowTimeFontSize"], metrics
    assert metrics["pathTimeWhiteSpace"] == "nowrap", metrics


def test_yoinfo_tab_values_match_shared_tab_detail(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof infoTreeHtml === 'function' && typeof infoRecordHtml === 'function';"
        )
    )
    metrics = browser.execute_script(
        """
        transcriptMetadataState.payload = {sessions: {
          '8003': {
            project: {
              git: {branch: 'feature/shared-tab-detail'},
              pull_request: {number: 11112, draft: true, title: 'Refactor agent status ownership'},
            },
          },
        }};
        const record = {
          id: 'session-work-8003',
          tabKey: '8003',
          tabSession: '8003',
          tabLabel: '8003',
          tabTitle: '8003',
        };
        const fixture = document.createElement('div');
        fixture.id = 'yoinfo-session-work-fixture';
        fixture.style.width = '900px';
        fixture.innerHTML = `
          <section id="group-work">${infoTreeHtml([record], ['tab'])}</section>
          <section id="leaf-work">${infoRecordHtml(record)}</section>`;
        document.body.appendChild(fixture);
        const sharedFixture = document.createElement('div');
        sharedFixture.innerHTML = tmuxPaneTabTokenHtml('8003', {tag: 'span', action: false});
        const sharedDetail = sharedFixture.querySelector('.tab-inline-detail');
        const groupLine = fixture.querySelector('#group-work .info-tree-group-label-line');
        const groupToken = groupLine.querySelector('.info-tree-tab-token');
        const groupDetail = groupToken.querySelector('.tab-inline-detail');
        const leafValue = fixture.querySelector('#leaf-work .info-tree-field-tab .info-tree-field-value');
        const leafToken = leafValue.querySelector('.info-tree-tab-token');
        const leafDetail = leafToken.querySelector('.tab-inline-detail');
        const rect = element => element?.getBoundingClientRect();
        return {
          sharedText: sharedDetail?.textContent || '',
          groupText: groupDetail?.textContent || '',
          leafText: leafDetail?.textContent || '',
          groupSession: groupToken.querySelector('.session-button-number')?.textContent || '',
          leafSession: leafToken.querySelector('.session-button-number')?.textContent || '',
          groupWidthDelta: Math.abs(rect(groupLine).width - rect(groupToken).width),
          leafWidthDelta: Math.abs(rect(leafValue).width - rect(leafToken).width),
          groupDetailDisplay: groupDetail ? getComputedStyle(groupDetail).display : '',
          leafDetailDisplay: leafDetail ? getComputedStyle(leafDetail).display : '',
          groupPrCount: (groupToken.textContent.match(/#11112/g) || []).length,
          leafPrCount: (leafToken.textContent.match(/#11112/g) || []).length,
        };
        """
    )
    assert metrics["sharedText"] == "Refactor agent status ownership", metrics
    assert metrics["groupText"] == metrics["leafText"] == metrics["sharedText"], metrics
    assert metrics["groupSession"] == "[8003]" and metrics["leafSession"] == "[8003]", metrics
    assert metrics["groupWidthDelta"] <= 1 and metrics["leafWidthDelta"] <= 1, metrics
    assert metrics["groupDetailDisplay"] != "none" and metrics["leafDetailDisplay"] != "none", metrics
    assert metrics["groupPrCount"] == metrics["leafPrCount"] == 1, metrics


def test_yoinfo_leaf_fields_put_linear_then_pr_before_repository_metadata(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof infoRecordHtml === 'function';")
    )
    order = browser.execute_script(
        """
        const fixture = document.createElement('div');
        fixture.innerHTML = infoRecordHtml({
          id: 'ordered-fields',
          pathKey: '/home/test/repo',
          pathLabel: '/home/test/repo',
          pathTitle: '/home/test/repo',
          branchKey: 'feature/order',
          branchLabel: 'feature/order',
          branchTitle: 'feature/order',
          prKey: '#123',
          prLabel: '#123',
          prTitle: '#123 Reorder YO!info metadata',
          prUrl: 'https://example.test/pull/123',
          linearKey: 'DIS-123',
          linearLabel: 'DIS-123',
          linearTitle: 'DIS-123 Reorder YO!info metadata',
          linearItems: [{identifier: 'DIS-123', title: 'Reorder YO!info metadata', url: 'https://linear.test/DIS-123'}],
        });
        document.body.appendChild(fixture);
        return [...fixture.querySelectorAll('.info-tree-field')].map(field =>
          [...field.classList].find(className => className.startsWith('info-tree-field-') && className !== 'info-tree-field-value')?.replace('info-tree-field-', '')
        );
        """
    )
    assert order == ["linear", "pr", "path", "branch"], order


def test_tabber_session_rows_use_pane_tab_shape_and_keep_columns(browser, tmp_path):
    for label, theme_class, pane_width, window_width in (
        ("dark-narrow", "theme-dark", 300, 700),
        ("light-wide", "theme-light", 1200, 1400),
    ):
        browser.set_window_size(window_width, 720)
        page = tmp_path / f"tabber-session-row-{label}.html"
        load_static_html_fixture(
            browser,
            page.parent,
            page.name,
            page_html(
                f"""
                <script>document.body.className = {json.dumps(theme_class)};</script>
                <section class="fixture-tabber-panel file-explorer-changes-panel">
                  <div class="file-tree" role="tree">
                    <div class="file-tree-row tabber-row kind-dir expanded tabber-active-session" data-tabber-type="session" data-tabber-session="1" role="treeitem" aria-expanded="true" aria-selected="false" aria-current="true" style="padding-left: 8px;">
                      <span class="file-tree-icon tabber-icon ui-disclosure-triangle" data-disclosure-expanded="true">›</span>
                      <span class="file-tree-name"><span class="tmux-pane-tab-token tmux-pane-tab-token-action tabber-session-tab session-popover-host active" data-tabber-session-chrome="shared"><span class="pane-tab-core"><span class="session-yolo-marker inactive">YO</span><span class="session-button-prefix"><span class="session-button-number">8801</span></span><span class="session-button-text"><span class="session-button-dir tab-inline-detail">tabber session tab styling keeps the date column visible for a deliberately long work description</span></span></span></span></span>
                      <span class="file-tree-agent" hidden></span>
                      <span class="file-tree-diff" hidden></span>
                      <span class="file-tree-dir-count" hidden></span>
                      <span class="file-tree-git-status" hidden></span>
                      <span class="file-tree-date">2m ago</span>
                    </div>
                    <div class="file-tree-row tabber-row kind-file" data-tabber-type="window" data-tabber-session="1" role="treeitem" aria-selected="false" style="padding-left: 27px;">
                      <span class="file-tree-icon tabber-icon"></span>
                      <span class="file-tree-name"><span class="tabber-window-token tmux-window-bar" data-tmux-window-label-mode="names" data-tmux-window-bar-context="info"><span class="tab tmux-window-button tabber-window-button" data-tabber-window-button="shared"><span class="tmux-window-name-label"><span class="tmux-window-name-text">0:bash</span></span></span></span></span>
                      <span class="file-tree-agent" hidden></span>
                      <span class="file-tree-diff" hidden></span>
                      <span class="file-tree-dir-count" hidden></span>
                      <span class="file-tree-git-status" hidden></span>
                      <span class="file-tree-date">2m ago</span>
                    </div>
                    <div class="file-tree-row tabber-row kind-dir expanded tabber-active-session" data-tabber-type="session" data-tabber-session="2" role="treeitem" aria-expanded="true" aria-selected="false" style="padding-left: 8px;">
                      <span class="file-tree-icon tabber-icon ui-disclosure-triangle" data-disclosure-expanded="true">›</span>
                      <span class="file-tree-name"><span class="tmux-pane-tab-token tmux-pane-tab-token-action tabber-session-tab session-popover-host active" data-tabber-session-chrome="shared"><span class="pane-tab-core"><span class="session-yolo-marker inactive">YO</span><span class="session-button-prefix"><span class="session-button-number">2</span></span><span class="session-button-text"><span class="session-button-dir tab-inline-detail">main</span></span></span></span></span>
                      <span class="file-tree-agent" hidden></span>
                      <span class="file-tree-diff" hidden></span>
                      <span class="file-tree-dir-count" hidden></span>
                      <span class="file-tree-git-status" hidden></span>
                      <span class="file-tree-date">15m ago</span>
                    </div>
                    <div class="file-tree-row tabber-row kind-file" data-tabber-type="window" data-tabber-session="2" role="treeitem" aria-selected="false" style="padding-left: 27px;">
                      <span class="file-tree-icon tabber-icon"></span>
                      <span class="file-tree-name"><span class="tabber-window-token tmux-window-bar" data-tmux-window-label-mode="names" data-tmux-window-bar-context="info"><span class="tab tmux-window-button tabber-window-button" data-tabber-window-button="shared"><span class="tmux-window-name-label"><span class="tmux-window-name-text">0:bash</span></span></span></span></span>
                      <span class="file-tree-agent" hidden></span>
                      <span class="file-tree-diff" hidden></span>
                      <span class="file-tree-dir-count" hidden></span>
                      <span class="file-tree-git-status" hidden></span>
                      <span class="file-tree-date">15m ago</span>
                    </div>
                  </div>
                </section>
                <span id="info-window-token" class="info-tree-ai-value tmux-window-bar"><span class="tab tmux-window-button">0:bash</span></span>
                """,
                extra_css=f"""
                  body {{ margin: 0; padding: 16px; background: var(--bg); color: var(--text); }}
                  .fixture-tabber-panel {{ width: {pane_width}px; border: 1px solid var(--border); }}
                """,
            ),
        )
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
            const resolvedRadius = (scope, value) => {
              const probe = document.createElement('span');
              probe.style.position = 'absolute';
              probe.style.pointerEvents = 'none';
              probe.style.borderRadius = value;
              (scope || document.body).appendChild(probe);
              const radius = getComputedStyle(probe).borderTopLeftRadius;
              probe.remove();
              return radius;
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
                tabTopRadius: style?.borderTopLeftRadius || '',
                tabBottomRadius: style?.borderBottomLeftRadius || '',
                tabBorderTop: style?.borderTopColor || '',
                expectedActiveBg: tab ? resolvedColor(tab.parentElement, 'var(--pane-tab-active-bg)') : '',
                expectedInactiveBg: tab ? resolvedColor(tab.parentElement, 'var(--pane-inactive-tab-bg)') : '',
                descriptionScrollWidth: description?.scrollWidth || 0,
                descriptionClientWidth: description?.clientWidth || 0,
              };
            };
            const sessionRows = Array.from(document.querySelectorAll('.file-tree-row[data-tabber-type="session"]'));
            const windowRows = Array.from(document.querySelectorAll('.file-tree-row[data-tabber-type="window"]'));
            const activeRow = sessionRows.find(row => row.dataset.tabberSession === '1');
            const inactiveRow = sessionRows.find(row => row.dataset.tabberSession === '2');
            const activeWindowRow = windowRows.find(row => row.dataset.tabberSession === '1');
            const tabberWindowToken = activeWindowRow?.querySelector('.tabber-window-token');
            const infoWindowToken = document.getElementById('info-window-token');
            const tokenAlignment = node => {
              const style = node ? getComputedStyle(node) : null;
              return style ? {
                flex: style.flex,
                maxWidth: style.maxWidth,
                marginInlineStart: style.marginInlineStart,
                justifyContent: style.justifyContent,
                overflow: style.overflow,
                verticalAlign: style.verticalAlign,
              } : null;
            };
            const activeWindowText = activeWindowRow?.querySelector('.tmux-window-name-text');
            const windowIcons = windowRows.map(row => (row.querySelector('.file-tree-icon')?.textContent || '').trim());
            const nonSessionWithSessionTab = Array.from(document.querySelectorAll('.file-tree-row:not([data-tabber-type="session"]) .tabber-session-tab')).length;
            return {
              active: rowMetrics(activeRow),
              inactive: rowMetrics(inactiveRow),
              activeWindow: rectFor(activeWindowRow),
              tabberWindowAlignment: tokenAlignment(tabberWindowToken),
              infoWindowAlignment: tokenAlignment(infoWindowToken),
              activeWindowTextColor: activeWindowText ? getComputedStyle(activeWindowText).color : '',
              expectedText: resolvedColor(document.body, 'var(--text)'),
              expectedWindowButtonText: resolvedColor(document.body, 'var(--pane-ctl-fg, var(--pc-control-fg))'),
              expectedActiveText: resolvedColor(document.body, 'var(--pane-tab-active-text)'),
              expectedInactiveText: resolvedColor(document.body, 'var(--pane-tab-text)'),
              expectedInactiveBorder: resolvedColor(document.body, 'var(--pane-inactive-tab-border)'),
              expectedTopRadius: resolvedRadius(document.body, 'var(--pane-tab-top-radius)'),
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
        assert "tmux-pane-tab-token" in metrics["active"]["tabClass"], (label, metrics)
        assert "tmux-pane-tab-token-action" in metrics["active"]["tabClass"], (label, metrics)
        assert metrics["active"]["ariaCurrent"] == "true", (label, metrics)
        assert metrics["active"]["ariaExpanded"] == "true", (label, metrics)
        assert metrics["active"]["iconText"] == "›", (label, metrics)
        assert "tabber-active-session" in metrics["inactive"]["rowClass"], (label, metrics)
        assert "active" in metrics["inactive"]["tabClass"], (label, metrics)
        assert "tmux-pane-tab-token" in metrics["inactive"]["tabClass"], (label, metrics)
        assert "tmux-pane-tab-token-action" in metrics["inactive"]["tabClass"], (label, metrics)
        assert metrics["inactive"]["ariaCurrent"] == "", (label, metrics)
        assert metrics["active"]["tabBg"] == metrics["active"]["expectedActiveBg"], (label, metrics)
        assert metrics["inactive"]["tabBg"] == metrics["inactive"]["expectedActiveBg"], (label, metrics)
        assert metrics["active"]["tabBg"] != metrics["active"]["tabColor"], (label, metrics)
        assert metrics["inactive"]["tabBg"] != metrics["inactive"]["tabColor"], (label, metrics)
        if theme_class == "theme-light":
            assert metrics["active"]["tabColor"] == metrics["expectedActiveText"], (label, metrics)
            assert metrics["active"]["descriptionColor"] == metrics["active"]["tabColor"], (label, metrics)
            assert metrics["inactive"]["tabColor"] == metrics["expectedActiveText"], (label, metrics)
            assert metrics["inactive"]["descriptionColor"] == metrics["inactive"]["tabColor"], (label, metrics)
        assert metrics["activeWindowTextColor"] == metrics["expectedWindowButtonText"], (label, metrics)
        assert metrics["tabberWindowAlignment"] == metrics["infoWindowAlignment"] == {
            "flex": "0 1 auto",
            "maxWidth": "100%",
            "marginInlineStart": "0px",
            "justifyContent": "flex-start",
            "overflow": "visible",
            "verticalAlign": "middle",
        }, (label, metrics)
        assert metrics["active"]["tab"]["height"] >= 16, (label, metrics)
        assert metrics["active"]["tabTopRadius"] == metrics["expectedTopRadius"], (label, metrics)
        assert metrics["active"]["tabBottomRadius"] == "0px", (label, metrics)
        assert metrics["inactive"]["tabTopRadius"] == metrics["expectedTopRadius"], (label, metrics)
        assert metrics["inactive"]["tabBottomRadius"] == "0px", (label, metrics)
        assert metrics["active"]["dateDisplay"] != "none", (label, metrics)
        assert metrics["active"]["dateWidth"] > 0, (label, metrics)
        assert metrics["active"]["dateText"], (label, metrics)
        assert abs(metrics["active"]["tab"]["width"] - metrics["inactive"]["tab"]["width"]) <= 1, (label, metrics)
        assert metrics["active"]["icon"]["right"] <= metrics["active"]["tab"]["left"] + 1, (label, metrics)
        assert metrics["active"]["tab"]["right"] <= metrics["active"]["date"]["left"] + 1, (label, metrics)
        assert metrics["active"]["name"]["left"] >= metrics["active"]["tab"]["left"] - 1, (label, metrics)
        assert metrics["active"]["description"]["right"] <= metrics["active"]["tab"]["right"] + 1, (label, metrics)
        if label == "light-wide":
            assert metrics["active"]["descriptionScrollWidth"] <= metrics["active"]["descriptionClientWidth"] + 1, (label, metrics)
        else:
            assert metrics["active"]["descriptionScrollWidth"] > metrics["active"]["descriptionClientWidth"], (label, metrics)
        assert metrics["activeWindow"]["top"] >= metrics["active"]["row"]["bottom"] - 1, (label, metrics)
        screenshot = browser_screenshot_rgb(browser)
        assert screenshot.size[0] >= window_width - 20, (label, screenshot.size)
        assert screenshot.getbbox() is not None, label


def test_tabber_session_tab_popover_uses_normal_tab_surface(browser, tmp_path):
    page = tmp_path / "tabber-session-popover-surface.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
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
                  <span class="file-tree-icon tabber-icon ui-disclosure-triangle" data-disclosure-expanded="true">›</span>
                  <span class="file-tree-name">
                    <span id="tabber-tab" class="tmux-pane-tab-token tmux-pane-tab-token-action tabber-session-tab session-popover-host popover-open" data-tabber-session-chrome="shared" style="--pane-tab-popover-left: 24px; --pane-tab-popover-top: 180px;">
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
    )
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
        const customPopover = paneTabPopoverForAnchor(sessionTab);
        positionPaneTabPopover(sessionTab, customPopover);
        sessionTab.classList.add('popover-open');
        customPopover.classList.add('popover-open');
        const panelRect = sessionTab.closest('.panel').getBoundingClientRect();
        const popoverRect = customPopover.getBoundingClientRect();
        return {
          rowCount: rows.length,
          rowTitles: rows.map(row => row.getAttribute('title') || ''),
          dataTitles: rows.map(row => row.dataset.tabberTitle || ''),
          visibleChromeTitles,
          sessionTabHasTitle: sessionTab?.hasAttribute('title') || false,
          customPopoverPresent: !!customPopover,
          customPopoverRole: customPopover?.getAttribute('role') || '',
          customPopoverDetached: customPopover?.parentElement === appOverlayRootElement(),
          customPopoverCrossesPane: popoverRect.right > panelRect.right + 1,
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
    assert metrics["customPopoverDetached"] is True, metrics
    assert metrics["customPopoverCrossesPane"] is True, metrics
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
    assert live_sync_metrics["tabberYoloControls"] == [], live_sync_metrics
    browser.execute_script(
        """
        popoverHideDelayMs = 120;
        window.__tabberSessionTabBeforeRefresh = document.querySelector('.file-tree-row[data-tabber-type="session"] .tabber-session-tab');
        window.__tabberPopoverBeforeRefresh = paneTabPopoverForAnchor(window.__tabberSessionTabBeforeRefresh);
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
        const visiblePopovers = [tab?.__yolomuxDetachedPopover].filter(popover => {
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
    cleanup_metrics = browser.execute_script(
        """
        const oldTab = window.__tabberSessionTabBeforeRefresh;
        const oldPopover = window.__tabberPopoverBeforeRefresh;
        const detachedPopoverCountBefore = appOverlayRootElement().querySelectorAll('.pane-tab-detached-popover').length;
        oldTab.classList.remove('popover-open');
        oldPopover.classList.remove('popover-open');
        delete oldTab.dataset.popoverHoverState;
        refreshTabberPanels();
        const nextTab = document.querySelector('.file-tree-row[data-tabber-type="session"] .tabber-session-tab');
        const nextPopover = paneTabPopoverForAnchor(nextTab);
        return {
          oldPopoverConnected: oldPopover.isConnected,
          nextPopoverDetached: nextPopover?.parentElement === appOverlayRootElement(),
          detachedPopoverCountBefore,
          detachedPopoverCount: appOverlayRootElement().querySelectorAll('.pane-tab-detached-popover').length,
        };
        """
    )
    assert cleanup_metrics["oldPopoverConnected"] is False, cleanup_metrics
    assert cleanup_metrics["nextPopoverDetached"] is True, cleanup_metrics
    assert cleanup_metrics["detachedPopoverCount"] == cleanup_metrics["detachedPopoverCountBefore"], cleanup_metrics


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
    assert "GET /api/notify" not in metrics["fetchPaths"]
    assert "GET /api/auto-approve" in metrics["fetchPaths"]
    assert metrics["fetchPaths"].count("POST /api/ensure-session") <= 1
    assert "GET /api/session-metadata" in metrics["fetchPaths"]
    assert "GET /api/ping" in metrics["fetchPaths"]
    assert any("/ws?session=1" in url for url in metrics["sockets"])
    assert {"File", "View", "tmux", "Tabs", "Help"}.issubset(
        {button["label"] for button in metrics["menuButtons"]}
    )
    assert any(
        button["label"] == "Tabs" and button["badge"] == ""
        for button in metrics["menuButtons"]
    ), "Tabs has no right-side running-YOLO circle"
    assert metrics["panelCount"] >= 1
    assert metrics["paneTabCount"] >= 1
    assert metrics["panelVisible"]
    assert metrics["notifyActive"] is True
    assert metrics["terminalText"] == "fake terminal"


def test_yochat_live_panel_unicode_status_search_and_emoji_geometry(browser, tmp_path):
    try:
        load_live_runtime_boot_fixture(
            browser,
            tmp_path,
            "?sessions=chat&layout=slot1&tabs=slot1:chat",
            sessions=["1"],
            grid_width=760,
            grid_height=560,
        )
    except AssertionError as error:
        raise AssertionError(f"{error}; browser_log={browser.get_log('browser')}") from error
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__chat__ [data-chat-input]') && window.__eventSources.length > 0"
        )
    )
    initial = browser.execute_script(
        """
        const panel = document.getElementById('panel-__chat__');
        const grid = document.getElementById('grid');
        const originalGridHeight = grid.style.height;
        grid.style.height = '900px';
        const timeline = panel?.querySelector('[data-chat-timeline]');
        const introduction = panel?.querySelector('.yochat-introduction');
        const pane = panel?.querySelector('.chat-pane');
        const composer = panel?.querySelector('[data-chat-form]');
        const panelRect = panel?.getBoundingClientRect();
        const timelineRect = timeline?.getBoundingClientRect();
        const introductionRect = introduction?.getBoundingClientRect();
        const paneRect = pane?.getBoundingClientRect();
        const composerRect = composer?.getBoundingClientRect();
        const result = {
          panelConnected: panel?.isConnected === true,
          emojiCatalogRequested: window.__bootFetches.some(item => item.path === '/static/emoji-data.js'),
          oldBodies: panel?.querySelectorAll('[data-chat-message-id]').length || 0,
          searchHidden: panel?.querySelector('[data-chat-search-bar]')?.hidden === true,
          searchDisplay: getComputedStyle(panel?.querySelector('[data-chat-search-bar]')).display,
          introduction: panel?.querySelector('[data-chat-timeline]')?.textContent || '',
          introductionCode: introduction?.querySelector('code')?.textContent || '',
          introductionBottomGap: (timelineRect?.bottom || 0) - (introductionRect?.bottom || 0),
          introductionComposerGap: (composerRect?.top || 0) - (introductionRect?.bottom || 0),
          composerBottomGap: (paneRect?.bottom || 0) - (composerRect?.bottom || 0),
          panePanelBottomGap: (panelRect?.bottom || 0) - (paneRect?.bottom || 0),
          composerPanelBottomGap: (panelRect?.bottom || 0) - (composerRect?.bottom || 0),
          panelClasses: panel?.className || '',
          panelGridRows: getComputedStyle(panel).gridTemplateRows,
          olderButton: panel?.querySelector('[data-chat-load-older]') !== null,
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
        };
        grid.style.height = originalGridHeight;
        return result;
        """
    )
    assert initial["panelConnected"] is True and initial["emojiCatalogRequested"] is False and initial["oldBodies"] == 0
    assert initial["searchHidden"] is True and initial["searchDisplay"] == "none" and initial["olderButton"] is False
    assert "YO!agent" in initial["introduction"] and "ask me" in initial["introduction"] and "/yo <query>" in initial["introduction"]
    assert initial["introductionCode"] == "/yo <query>"
    assert 0 <= initial["introductionBottomGap"] <= 8, initial
    assert 0 <= initial["introductionComposerGap"] <= 16, initial
    assert 0 <= initial["composerBottomGap"] <= 10, initial
    assert 0 <= initial["panePanelBottomGap"] <= 2 and 0 <= initial["composerPanelBottomGap"] <= 12, initial
    assert initial["errors"] == [] and initial["rejections"] == []

    exact_body = "😀 👍🏽 👩‍💻 👨‍👩‍👧‍👦 🏳️‍🌈 🇺🇸 1️⃣ ☕️ مرحبا 😀"
    browser.execute_script(
        """
        setFocusedPanelItem(chatItemId, {userInitiated: true});
        document.querySelector('#panel-__chat__ [data-chat-input]').focus();
        window.__fixtureChatSendAs('bob', 'browser-b', arguments[0], true);
        """,
        exact_body,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__chat__ [data-chat-message-id]')?.textContent.includes(arguments[0])",
            exact_body,
        )
    )
    message = browser.execute_script(
        """
        const panel = document.getElementById('panel-__chat__');
        const row = panel.querySelector('[data-chat-message-id]');
        const time = row.querySelector('time');
        const marker = document.querySelector('.chat-status-marker');
        return {
          body: row.querySelector('.conversation-message-body').textContent,
          author: row.querySelector('.conversation-message-role span').textContent,
          ip: row.querySelector('.yochat-message-ip')?.textContent || '',
          timestamp: time.textContent,
          datetime: time.getAttribute('datetime'),
          red: marker?.querySelector('.state-attention, .attention, [data-state="attention"]') !== null || marker?.textContent.length > 0,
          toastCount: document.querySelectorAll('.toast').length,
          liveText: panel.querySelector('[data-chat-live]').textContent,
          timelineLive: panel.querySelector('[data-chat-timeline]').getAttribute('aria-live'),
        };
        """
    )
    assert message["body"] == exact_body
    assert message["author"] == "bob"
    assert message["ip"] == "10.1.123.12"
    assert "ago" in message["timestamp"]
    assert message["datetime"].endswith("Z")
    assert message["red"] is True
    assert message["toastCount"] == 0, "focused, visible YO!chat suppresses in-app notifications"
    assert exact_body in message["liveText"]
    assert message["timelineLive"] == "off", "the dedicated live region announces only new messages instead of replaying the whole timeline"
    assert browser.execute_script(
        "return document.querySelectorAll('#panel-__chat__ .yochat-introduction').length"
    ) == 1, "the non-persisted introduction remains the first timeline card when messages exist"
    parity = browser.execute_script(
        """
        const panel = document.getElementById('panel-__chat__');
        const host = document.createElement('div');
        host.innerHTML = conversationMessageShellHtml({
          className: 'yoagent-message fixture-agent-message',
          author: 'YO!agent',
          bodyHtml: '<div class="conversation-message-body yoagent-message-body">fixture</div>',
        });
        panel.querySelector('[data-chat-timeline]').appendChild(host.firstElementChild);
        const read = theme => {
          document.body.classList.toggle('theme-light', theme === 'light');
          document.body.classList.toggle('theme-dark', theme === 'dark');
          const chat = getComputedStyle(panel.querySelector('.yochat-message'));
          const agent = getComputedStyle(panel.querySelector('.fixture-agent-message'));
          const chatBody = getComputedStyle(panel.querySelector('.yochat-message .conversation-message-body'));
          const agentBody = getComputedStyle(panel.querySelector('.fixture-agent-message .conversation-message-body'));
          return {
            radius: [chat.borderRadius, agent.borderRadius],
            padding: [chat.padding, agent.padding],
            bodyFont: [chatBody.font, agentBody.font],
            bodyColor: [chatBody.color, agentBody.color],
          };
        };
        const result = {dark: read('dark'), light: read('light')};
        document.body.classList.remove('theme-light');
        document.body.classList.add('theme-dark');
        return result;
        """
    )
    for theme in ("dark", "light"):
        assert parity[theme]["radius"][0] == parity[theme]["radius"][1]
        assert parity[theme]["padding"][0] == parity[theme]["padding"][1]
        assert parity[theme]["bodyFont"][0] == parity[theme]["bodyFont"][1]
        assert parity[theme]["bodyColor"][0] == parity[theme]["bodyColor"][1]

    browser.execute_script(
        """
        setFocusedPanelItem('1', {userInitiated: true});
        window.__fixtureChatSendAs('carol', 'browser-c', 'unfocused pane notification', false);
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return [...document.querySelectorAll('.toast')].some(node => node.textContent.includes('unfocused pane notification'))"
        )
    )
    assert browser.execute_script(
        "return [...document.querySelectorAll('.toast')].filter(node => node.textContent.includes('unfocused pane notification')).length"
    ) == 1, "active-but-unfocused YO!chat delivers one deduplicated in-app notification"
    browser.execute_script("setFocusedPanelItem(chatItemId, {userInitiated: true})")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return ![...document.querySelectorAll('.toast')].some(node => node.textContent.includes('unfocused pane notification'))"
        )
    )
    browser.execute_script("window.__fixtureChatSendAs('dave', 'browser-d', 'already focused notification', false)")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return [...document.querySelectorAll('#panel-__chat__ .conversation-message-body')].some(node => node.textContent === 'already focused notification')"
        )
    )
    assert browser.execute_script(
        "return ![...document.querySelectorAll('.toast')].some(node => node.textContent.includes('already focused notification'))"
    ), "the exact focused target Tab suppresses new in-app notifications"

    author_colors = browser.execute_script(
        """
        const rows = [...document.querySelectorAll('#panel-__chat__ .yochat-message[data-chat-message-id]')];
        return Object.fromEntries(rows.map(row => [row.querySelector('.conversation-message-role > span')?.textContent || '', getComputedStyle(row).borderColor]));
        """
    )
    assert author_colors["bob"] != author_colors["carol"], "each visible person gets a distinct existing-token border scheme"

    browser.execute_script(
        """
        window.__fixtureChatTyping = [{username: 'bob', browser_instance_id: 'browser-b', expires_at_utc: Date.now() / 1000 + 5}];
        emitFixtureClientEvent('chat_typing_changed', {});
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: "bob" in driver.execute_script(
            "return document.querySelector('#panel-__chat__ [data-chat-typing]')?.textContent || ''"
        )
    )
    browser.execute_script("document.querySelector('#panel-__chat__ [data-chat-timeline]').click()")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.chat-status-marker .agent-window-status-dot--acknowledging') !== null"
        )
    )
    acknowledgement = browser.execute_script(
        """
        const marker = document.querySelector('.chat-status-marker');
        return {
          gray: marker.querySelector('.agent-window-status-dot--acknowledging') !== null,
          greenRemains: marker.querySelectorAll('.agent-window-status-dot').length >= 2,
          readPosts: window.__bootFetches.filter(item => item.path === '/api/chat/read').length,
        };
        """
    )
    assert acknowledgement == {"gray": True, "greenRemains": True, "readPosts": 1}

    browser.execute_script(
        """
        window.__fixtureChatTyping = [{username: 'stale-user', browser_instance_id: 'stale-browser', expires_at_utc: Date.now() / 1000 + 0.2}];
        emitFixtureClientEvent('chat_typing_changed', {});
        """
    )
    WebDriverWait(browser, 3).until(
        lambda driver: "stale-user" not in driver.execute_script(
            "return document.querySelector('#panel-__chat__ [data-chat-typing]')?.textContent || ''"
        )
    )

    typing_requests = browser.execute_script(
        """
        const input = document.querySelector('#panel-__chat__ [data-chat-input]');
        input.value = 'h';
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.value = 'he';
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new FocusEvent('focusout', {bubbles: true}));
        return window.__bootFetches
          .filter(item => item.path === '/api/chat/typing')
          .map(item => item.body.typing);
        """
    )
    assert typing_requests == [True, False], "typing sends one leading lease, not one request per keystroke, then explicitly stops on blur"

    search_shortcut = browser.execute_script(
        """
        const event = new KeyboardEvent('keydown', {
          key: 'f',
          code: 'KeyF',
          metaKey: isMacPlatform(),
          ctrlKey: !isMacPlatform(),
          bubbles: true,
          cancelable: true,
        });
        (document.activeElement || document.body).dispatchEvent(event);
        return event.defaultPrevented;
        """
    )
    assert search_shortcut is True, "Cmd/Ctrl-F is claimed by the focused YO!chat tab"
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__chat__ [data-chat-search-bar]')?.hidden === false && document.activeElement?.matches('[data-chat-search]')"
        )
    )
    browser.execute_script(
        """
        const firstWeatherId = window.__fixtureChatMessages.length + 1;
        for (let index = 0; index < 12; index += 1) {
          window.__fixtureChatMessages.push({
            id: firstWeatherId + index,
            created_at_utc: Date.now() / 1000,
            username: 'weather-fixture',
            sender_ip: '10.1.123.12',
            sender_instance_id: 'weather-browser',
            client_message_uuid: `weather-${index}`,
            body: `weather result ${index} for California`,
            is_question: false,
          });
        }
        const search = document.querySelector('#panel-__chat__ [data-chat-search]');
        search.value = 'wea';
        search.closest('form').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelectorAll('#panel-__chat__ [data-chat-search-result]').length === 12"
        )
    )
    search_split = browser.execute_script(
        """
        const panel = document.getElementById('panel-__chat__');
        const split = panel.querySelector('.chat-history-search-split');
        const results = panel.querySelector('[data-chat-search-results]');
        const timeline = panel.querySelector('[data-chat-timeline]');
        const first = results.querySelector('[data-chat-search-result]');
        const splitRect = split.getBoundingClientRect();
        const resultsRect = results.getBoundingClientRect();
        const timelineRect = timeline.getBoundingClientRect();
        const firstRect = first.getBoundingClientRect();
        return {
          matchText: first.textContent,
          resultsHeight: resultsRect.height,
          timelineHeight: timelineRect.height,
          splitHeight: splitRect.height,
          resultsScrollable: results.scrollHeight > results.clientHeight,
          timelineScrollable: timeline.scrollHeight > timeline.clientHeight,
          firstFullyVisible: firstRect.top >= resultsRect.top - 1 && firstRect.bottom <= resultsRect.bottom + 1,
          separated: resultsRect.bottom <= timelineRect.top + 1,
        };
        """
    )
    assert "weather" in search_split["matchText"].lower(), search_split
    assert search_split["resultsHeight"] <= search_split["splitHeight"] / 2 + 1, search_split
    assert search_split["timelineHeight"] >= search_split["splitHeight"] / 2 - 3, search_split
    assert search_split["resultsScrollable"] is True and search_split["timelineScrollable"] is True, search_split
    assert search_split["firstFullyVisible"] is True and search_split["separated"] is True, search_split
    browser.execute_script("document.querySelector('#panel-__chat__ [data-chat-search-close]').click()")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__chat__ [data-chat-search-bar]')?.hidden === true"
        )
    )
    assert browser.execute_script(
        "return document.querySelector('#panel-__chat__ [data-chat-search-results]')?.hidden === true"
    ), "X hides both the Cmd/Ctrl-F search chrome and its results"

    browser.set_window_size(430, 650)
    composer_size = browser.execute_script(
        """
        const panel = document.getElementById('panel-__chat__');
        const pane = panel.querySelector('.chat-pane');
        const input = panel.querySelector('[data-chat-input]');
        input.value = '';
        input.dispatchEvent(new Event('input', {bubbles: true}));
        const compact = panel.querySelector('[data-chat-form]').getBoundingClientRect().height;
        input.value = Array.from({length: 40}, (_, index) => `line ${index}`).join('\\n');
        input.dispatchEvent(new Event('input', {bubbles: true}));
        const grown = panel.querySelector('[data-chat-form]').getBoundingClientRect().height;
        const grownOverflow = getComputedStyle(input).overflowY;
        const paneHeight = pane.getBoundingClientRect().height;
        input.value = '';
        input.dispatchEvent(new Event('input', {bubbles: true}));
        const compactAfter = panel.querySelector('[data-chat-form]').getBoundingClientRect().height;
        return {compact, grown, grownOverflow, paneHeight, compactAfter};
        """
    )
    assert composer_size["grown"] > composer_size["compact"], composer_size
    assert composer_size["grown"] <= composer_size["paneHeight"] / 2 + 2, composer_size
    assert composer_size["grownOverflow"] == "auto", composer_size
    assert abs(composer_size["compactAfter"] - composer_size["compact"]) < 1, composer_size
    browser.execute_script(
        """
        globalThis.YOLOMUX_EMOJI_DATA = [
          {emoji: '😀', category: 'smileys-emotion', names: {en: 'grinning face'}, keywords: {en: ['smile']}},
          {emoji: '👨‍👩‍👧‍👦', category: 'people-body', names: {en: 'family'}, keywords: {en: ['family']}}
        ];
        const input = document.querySelector('#panel-__chat__ [data-chat-input]');
        input.value = 'ab';
        input.setSelectionRange(1, 1);
        document.querySelector('#panel-__chat__ [data-chat-emoji-button]').click();
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__chat__ [data-chat-emoji-picker]')?.hidden === false"
        )
    )
    browser.execute_script(
        """
        const panel = document.getElementById('panel-__chat__');
        const search = panel.querySelector('[data-chat-emoji-search]');
        search.value = 'family';
        search.dispatchEvent(new Event('input', {bubbles: true}));
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__chat__ [data-chat-emoji=" + '"👨‍👩‍👧‍👦"' + "]') !== null"
        )
    )
    picker = browser.execute_script(
        """
        const panel = document.getElementById('panel-__chat__');
        const glyph = panel.querySelector('[data-chat-emoji="👨‍👩‍👧‍👦"]');
        glyph.click();
        const input = panel.querySelector('[data-chat-input]');
        const picker = panel.querySelector('[data-chat-emoji-picker]');
        const panelRect = panel.getBoundingClientRect();
        const pickerRect = picker.getBoundingClientRect();
        return {
          value: input.value,
          pickerOpen: !picker.hidden,
          withinPanel: pickerRect.left >= panelRect.left - 1 && pickerRect.right <= panelRect.right + 1,
          ariaExpanded: panel.querySelector('[data-chat-emoji-button]').getAttribute('aria-expanded'),
          catalogNetworkFetches: window.__bootFetches.filter(item => item.path === '/static/emoji-data.js').length,
        };
        """
    )
    assert picker == {
        "value": "a👨‍👩‍👧‍👦b",
        "pickerOpen": True,
        "withinPanel": True,
        "ariaExpanded": "true",
        "catalogNetworkFetches": 0,
    }
    browser.set_window_size(1748, 1248)
    browser.execute_script("document.getElementById('panel-__chat__').classList.add('details-collapsed')")
    tall_picker_geometry = browser.execute_script(
        """
        const panel = document.getElementById('panel-__chat__');
        const pane = panel.querySelector('.chat-pane');
        const composer = panel.querySelector('[data-chat-form]');
        const timeline = panel.querySelector('[data-chat-timeline]');
        const panelRect = panel.getBoundingClientRect();
        const paneRect = pane.getBoundingClientRect();
        const composerRect = composer.getBoundingClientRect();
        return {
          panePanelBottomGap: panelRect.bottom - paneRect.bottom,
          composerPanelBottomGap: panelRect.bottom - composerRect.bottom,
          composerPaneBottomGap: paneRect.bottom - composerRect.bottom,
          timelineHeight: timeline.getBoundingClientRect().height,
          paneRows: getComputedStyle(pane).gridTemplateRows,
          panelRows: getComputedStyle(panel).gridTemplateRows,
          panelClasses: panel.className,
        };
        """
    )
    assert 0 <= tall_picker_geometry["composerPanelBottomGap"] <= 12, tall_picker_geometry

    retry_body = "retry once 👩‍💻"
    browser.execute_script(
        """
        closeChatEmojiPicker();
        window.__fixtureDropNextChatSendResponse = true;
        const panel = document.getElementById('panel-__chat__');
        const input = panel.querySelector('[data-chat-input]');
        input.value = arguments[0];
        input.dispatchEvent(new Event('input', {bubbles: true}));
        const form = panel.querySelector('[data-chat-form]');
        form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
        form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
        """,
        retry_body,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return window.__fixtureChatMessages.filter(message => message.body === arguments[0]).length === 1 && document.querySelector('#panel-__chat__ [data-chat-retry]') === null",
            retry_body,
        )
    )
    lost_response = browser.execute_script(
        """
        return {
          stored: window.__fixtureChatMessages.filter(message => message.body === arguments[0]).length,
          requests: window.__bootFetches.filter(item => item.path === '/api/chat/send' && item.body.body === arguments[0]).length,
          rendered: [...document.querySelectorAll('#panel-__chat__ .conversation-message-body')].filter(node => node.textContent === arguments[0]).length,
        };
        """,
        retry_body,
    )
    assert lost_response == {"stored": 1, "requests": 1, "rendered": 1}, "SSE reconciliation canonicalizes a lost response without a duplicate retry"

    retry_body = "retry after rejection 🏳️‍🌈"
    browser.execute_script(
        """
        window.__fixtureFailNextChatSendBeforeInsert = true;
        const panel = document.getElementById('panel-__chat__');
        const input = panel.querySelector('[data-chat-input]');
        input.value = arguments[0];
        input.dispatchEvent(new Event('input', {bubbles: true}));
        panel.querySelector('[data-chat-form]').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
        """,
        retry_body,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__chat__ [data-chat-retry]') !== null"
        )
    )
    browser.execute_script("document.querySelector('#panel-__chat__ [data-chat-retry]').click()")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__chat__ [data-chat-retry]') === null && window.__fixtureChatMessages.filter(message => message.body === arguments[0]).length === 1",
            retry_body,
        )
    )
    after_retry = browser.execute_script(
        """
        return {
          stored: window.__fixtureChatMessages.filter(message => message.body === arguments[0]).length,
          requests: window.__bootFetches.filter(item => item.path === '/api/chat/send' && item.body.body === arguments[0]).length,
          rendered: [...document.querySelectorAll('#panel-__chat__ .conversation-message-body')].filter(node => node.textContent === arguments[0]).length,
        };
        """,
        retry_body,
    )
    assert after_retry == {"stored": 1, "requests": 2, "rendered": 1}, "retry reuses the same client UUID and replaces the pending row"

    browser.execute_script(
        """
        window.__fixtureHoldChatYoagent = true;
        const panel = document.getElementById('panel-__chat__');
        const input = panel.querySelector('[data-chat-input]');
        input.value = '/yo summarize current tasks';
        input.dispatchEvent(new Event('input', {bubbles: true}));
        panel.querySelector('[data-chat-form]').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: "YO!agent is typing" in driver.execute_script(
            "return document.querySelector('#panel-__chat__ [data-chat-typing]')?.textContent || ''"
        )
    )
    optimistic = browser.execute_script(
        """
        const row = [...document.querySelectorAll('#panel-__chat__ .yochat-message.user')]
          .find(item => item.querySelector('.conversation-message-body')?.textContent === '/yo summarize current tasks');
        return {
          visible: row?.offsetParent !== null,
          author: row?.querySelector('.conversation-message-role > span')?.textContent || '',
          expectedAuthor: window.__fixtureAuthUsername,
          metadata: row?.querySelector('.yochat-message-metadata')?.textContent || '',
          atTail: (() => {
            const timeline = document.querySelector('#panel-__chat__ [data-chat-timeline]');
            return timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 32;
          })(),
        };
        """
    )
    assert optimistic["visible"] is True and optimistic["author"] == optimistic["expectedAuthor"] and optimistic["atTail"] is True, optimistic
    assert "myself" in optimistic["metadata"] and "10.1.123.12" in optimistic["metadata"], optimistic
    assert browser.execute_script(
        "return [...document.querySelectorAll('#panel-__chat__ .conversation-message-body')].some(node => node.textContent.includes('YO!agent is thinking'))"
    ) is False, "/yo reuses human typing presence instead of adding a fake thinking message"
    browser.execute_script(
        "window.__fixtureHoldChatRead = true; window.__fixtureHoldChatYoagent = false; window.__fixtureReleaseChatYoagent?.()"
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return [...document.querySelectorAll('#panel-__chat__ .yochat-agent-message .conversation-message-body')].some(node => node.textContent.includes('summarize current tasks'))"
        )
    )
    assert browser.execute_script(
        "return window.__fixtureHoldChatRead === true && window.__fixtureReleaseChatRead !== null"
    ), "the YO!agent reply paints before the read-cursor request is released"
    browser.execute_script("window.__fixtureHoldChatRead = false; window.__fixtureReleaseChatRead?.()")
    WebDriverWait(browser, 5).until(
        lambda driver: "YO!agent" not in driver.execute_script(
            "return document.querySelector('#panel-__chat__ [data-chat-typing]')?.textContent || ''"
        )
    )
    assert browser.execute_script(
        "return window.__bootFetches.filter(item => item.path === '/api/chat/yoagent').length"
    ) == 1, "/yo invokes the server bridge exactly once after the source chat message is durable"

    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return Math.max(0, ...Object.values(window.__fixtureChatReaders).map(Number)) === window.__fixtureChatMessages.at(-1).id"
        )
    )
    media_url = "https://encrypted-tbn0.gstatic.com/images?q=fixture&s=10"
    browser.execute_script(
        "window.__fixtureChatSendAs('guest-media', 'browser-media', `picture ${arguments[0]}`, false)",
        media_url,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__chat__ [data-chat-media-url]')?.dataset.chatMediaUrl === arguments[0]",
            media_url,
        )
    )
    fast_pointer_actions(browser).move_to_element(
        browser.find_element("css selector", "#panel-__chat__ [data-chat-media-url]")
    ).perform()
    thumbnail = browser.execute_script(
        """
        const node = document.querySelector('#panel-__chat__ [data-chat-media-url]');
        const rect = node.getBoundingClientRect();
        return {width: rect.width, height: rect.height, kind: node.dataset.chatMediaKind, link: node.closest('.conversation-message-body').querySelector('a')?.href || ''};
        """
    )
    assert thumbnail["kind"] == "image" and 40 <= thumbnail["width"] <= 48 and 40 <= thumbnail["height"] <= 48, thumbnail
    assert thumbnail["link"] == media_url, thumbnail
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.yochat-media-preview-popover img')?.src === arguments[0]",
            media_url,
        )
    )
    media_menu = browser.execute_script(
        """
        const node = document.querySelector('#panel-__chat__ [data-chat-media-url]');
        node.click();
        return [...document.querySelectorAll('.yochat-media-context-menu button')].map(button => button.textContent.trim());
        """
    )
    assert media_menu == ["Open in new tab", "Open URL in a new tab", "Copy URL", "Download"]
    browser.execute_script("document.querySelector('.yochat-media-context-menu button').click()")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.chat-media-panel .yochat-media-panel-stage img')?.src === arguments[0]",
            media_url,
        )
    )
    browser.execute_script("selectSession(chatItemId, {userInitiated: true})")
    reload_result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        clearChatLifecycle({destroy: true});
        chatState.messages.clear();
        chatState.pending.clear();
        chatState.unread.clear();
        chatState.loaded = false;
        chatState.loading = false;
        renderChatPanel();
        (async () => {
          const first = await loadChatBootstrap();
          const result = first || await loadChatBootstrap();
          done({
            result,
            transferredBodies: window.__fixtureLastChatBootstrapMessages?.length ?? -1,
            renderedBodies: document.querySelectorAll('#panel-__chat__ [data-chat-message-id]').length,
          });
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert reload_result["transferredBodies"] == 0 and reload_result["renderedBodies"] == 0, "reload transfers and paints no previously read message bodies"
    queued_older = browser.execute_script(
        """
        const firstQueuedId = window.__fixtureChatMessages.length + 1;
        for (let index = 0; index < 60; index += 1) {
          window.__fixtureChatMessages.push({
            id: firstQueuedId + index,
            created_at_utc: Date.now() / 1000,
            username: 'history-fixture',
            sender_ip: '10.1.123.12',
            sender_instance_id: 'history-browser',
            client_message_uuid: `queued-history-${index}`,
            body: `queued history ${index}`,
            is_question: false,
          });
        }
        const latest = window.__fixtureChatMessages.at(-1)?.id || 0;
        window.__fixtureChatReaders[chatReaderId] = Math.max(0, latest - 1);
        window.__fixtureHoldChatBootstrap = true;
        clearChatLifecycle({destroy: true});
        chatState.messages.clear();
        chatState.pending.clear();
        chatState.unread.clear();
        chatState.loaded = false;
        renderChatPanel();
        const beforeRequests = window.__bootFetches.filter(item => item.path === '/api/chat/page').length;
        window.__fixtureQueuedOlderBeforeRequests = beforeRequests;
        void loadChatBootstrap();
        const timeline = document.querySelector('#panel-__chat__ [data-chat-timeline]');
        timeline.scrollTop = 0;
        timeline.dispatchEvent(new WheelEvent('wheel', {deltaY: -100, bubbles: true}));
        return {
          bootstrapHeld: window.__fixtureReleaseChatBootstrap !== null,
          pageRequests: window.__bootFetches.filter(item => item.path === '/api/chat/page').length - beforeRequests,
          olderRequested: chatState.olderRequested,
        };
        """
    )
    assert queued_older == {"bootstrapHeld": True, "pageRequests": 0, "olderRequested": True}
    browser.execute_script("window.__fixtureHoldChatBootstrap = false; window.__fixtureReleaseChatBootstrap?.()")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelectorAll('#panel-__chat__ [data-chat-message-id]').length === 51"
        )
    )
    older_page = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(() => done({
          pageRequests: window.__bootFetches.filter(item => item.path === '/api/chat/page').length - window.__fixtureQueuedOlderBeforeRequests,
          olderRequested: chatState.olderRequested,
          hasMore: chatState.hasMore,
          renderedBodies: document.querySelectorAll('#panel-__chat__ [data-chat-message-id]').length,
        })));
        """
    )
    assert older_page["pageRequests"] == 1, "one upward gesture queues exactly one older-history request"
    assert older_page["olderRequested"] is False, "the queued gesture is consumed when paging starts"
    assert older_page["hasMore"] is True and older_page["renderedBodies"] == 51
    cleanup = browser.execute_script(
        """
        removePanelForItem(chatItemId);
        return {
          panelConnected: document.getElementById('panel-__chat__')?.isConnected === true,
          observer: chatState.olderObserver,
          controller: chatState.requestController,
          emojiOpen: chatEmojiOverlayController.isOpen(),
        };
        """
    )
    assert cleanup == {"panelConnected": False, "observer": None, "controller": None, "emojiOpen": False}


def test_yochat_follows_new_messages_only_from_the_tail_and_resets_initial_chrome(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=chat&layout=slot1&tabs=slot1:chat",
        sessions=["1"],
        grid_width=640,
        grid_height=520,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__chat__ [data-chat-timeline]') && window.__eventSources.length > 0"
        )
    )
    browser.execute_script(
        """
        window.marked.parse = text => String(text).includes('| City |')
          ? '<table><thead><tr><th>City</th><th>Temp</th></tr></thead><tbody><tr><td>San Jose</td><td>65°F</td></tr></tbody></table>'
          : String(text || '');
        window.__fixtureChatSendAs('YO!agent', 'yolomux-yoagent', '| City | Temp |\\n| --- | --- |\\n| San Jose | 65°F |', false, '');
        window.__fixtureChatSendAs(window.__fixtureAuthUsername, 'browser-self', 'own color', false);
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__chat__ .yochat-agent-message table td')?.textContent === 'San Jose'"
        )
    )
    composer_color = browser.execute_script(
        """
        const panel = document.getElementById('panel-__chat__');
        const selfRow = [...panel.querySelectorAll('.yochat-message.user')].find(row => row.querySelector('.conversation-message-role span')?.textContent === window.__fixtureAuthUsername);
        const probe = document.createElement('span');
        probe.style.border = '1px solid color-mix(in srgb, var(--accent-gold) 68%, var(--line))';
        probe.style.background = 'var(--accent-gold)';
        panel.appendChild(probe);
        const goldBorder = getComputedStyle(probe).borderColor;
        const goldBackground = getComputedStyle(probe).backgroundColor;
        probe.remove();
        const send = panel.querySelector('.conversation-send-primary');
        return {
          composer: getComputedStyle(panel.querySelector('[data-chat-form]')).borderColor,
          message: getComputedStyle(selfRow).borderColor,
          sendBackground: getComputedStyle(send).backgroundColor,
          sendBorder: getComputedStyle(send).borderColor,
          goldBorder,
          goldBackground,
        };
        """
    )
    assert composer_color["composer"] == composer_color["message"] == composer_color["goldBorder"], composer_color
    assert composer_color["sendBorder"] == composer_color["goldBorder"] and composer_color["sendBackground"] == composer_color["goldBackground"], composer_color
    browser.execute_script(
        """
        for (let index = 0; index < 24; index += 1) {
          window.__fixtureChatSendAs('guest', 'browser-guest', `history ${index}`, false);
        }
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelectorAll('#panel-__chat__ [data-chat-message-id]').length >= 26"
        )
    )

    bottom_before = browser.execute_script(
        """
        const timeline = document.querySelector('#panel-__chat__ [data-chat-timeline]');
        timeline.scrollTop = timeline.scrollHeight;
        timeline.dispatchEvent(new Event('scroll', {bubbles: true}));
        return timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight;
        """
    )
    assert bottom_before < 32
    browser.execute_script("window.__fixtureChatSendAs('guest', 'browser-guest', 'follow the tail', false)")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const panel = document.getElementById('panel-__chat__');
            const timeline = panel.querySelector('[data-chat-timeline]');
            return [...panel.querySelectorAll('.conversation-message-body')].some(node => node.textContent === 'follow the tail')
              && timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 32
              && panel.querySelector('[data-chat-new-messages]').hidden === true;
            """
        )
    )

    browser.execute_script(
        """
        const timeline = document.querySelector('#panel-__chat__ [data-chat-timeline]');
        timeline.scrollTop = timeline.scrollHeight;
        chatState.followTail = false;
        window.__fixtureChatSendAs('guest', 'browser-guest', 'measure the real tail', false);
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const panel = document.getElementById('panel-__chat__');
            const timeline = panel.querySelector('[data-chat-timeline]');
            return [...panel.querySelectorAll('.conversation-message-body')].some(node => node.textContent === 'measure the real tail')
              && timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight <= 32
              && panel.querySelector('[data-chat-new-messages]').hidden === true;
            """
        )
    )

    browser.execute_script(
        """
        const timeline = document.querySelector('#panel-__chat__ [data-chat-timeline]');
        timeline.scrollTop = 0;
        timeline.dispatchEvent(new Event('scroll', {bubbles: true}));
        window.__fixtureChatSendAs('guest', 'browser-guest', 'preserve scrollback', false);
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return [...document.querySelectorAll('#panel-__chat__ .conversation-message-body')].some(node => node.textContent === 'preserve scrollback')"
        )
    )
    scrollback = browser.execute_script(
        """
        const panel = document.getElementById('panel-__chat__');
        const timeline = panel.querySelector('[data-chat-timeline]');
        return {
          top: timeline.scrollTop,
          bottomGap: timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight,
          newMessagesVisible: panel.querySelector('[data-chat-new-messages]').hidden === false,
        };
        """
    )
    assert scrollback["top"] <= 2 and scrollback["bottomGap"] > 32 and scrollback["newMessagesVisible"] is True
    browser.execute_script("document.querySelector('#panel-__chat__ [data-chat-new-messages]').click()")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const panel = document.getElementById('panel-__chat__');
            const timeline = panel.querySelector('[data-chat-timeline]');
            return timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 32
              && panel.querySelector('[data-chat-new-messages]').hidden === true;
            """
        )
    )

    reset = browser.execute_script(
        """
        openChatSearch(document.getElementById('panel-__chat__'));
        clearChatLifecycle({destroy: true});
        renderChatPanel();
        const panel = document.getElementById('panel-__chat__');
        return {
          searchHidden: panel.querySelector('[data-chat-search-bar]').hidden,
          introductionCount: panel.querySelectorAll('.yochat-introduction').length,
          messageCount: panel.querySelectorAll('[data-chat-message-id]').length,
        };
        """
    )
    assert reset == {"searchHidden": True, "introductionCount": 1, "messageCount": 29}


def test_twelve_hour_setting_repaints_yostats_and_tab_navigation_dismisses_addressed_toasts(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=slot1&tabs=slot1:1",
        sessions=["1"],
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof debugGraphExactTimeLabel === 'function' && document.getElementById('panel-1')"
        )
    )
    result = browser.execute_script(
        """
        const timestamp = new Date(2026, 6, 4, 18, 5, 6).getTime();
        const before = debugGraphExactTimeLabel(timestamp);
        const next = {mtime_ns: Date.now(), defaults: clientSettingsDefaults, settings: JSON.parse(JSON.stringify(clientSettings))};
        next.settings.appearance = next.settings.appearance || {};
        next.settings.appearance.date_time_hour_cycle = '12';
        applySettingsPayload(next, {force: true});
        const after = debugGraphExactTimeLabel(timestamp);
        setFocusedPanelItem(prefsItemId);
        const node = showToast('Working AI needs your attention', 'fixture', {container: displayToastContainer('1'), targetItem: '1'});
        node.dataset.toastKind = 'working-agent-transition';
        const beforeNavigate = document.querySelectorAll('[data-toast-target-item="1"]').length;
        selectSession('1', {userInitiated: true});
        const afterNavigate = document.querySelectorAll('[data-toast-target-item="1"]').length;
        const suppressed = showToast('Working AI finished', 'fixture', {container: displayToastContainer('1'), targetItem: '1'});
        return {before, after, beforeNavigate, afterNavigate, suppressed: suppressed === null};
        """
    )
    assert not re.search(r"\b(?:AM|PM)\b", result["before"])
    assert re.search(r"\b(?:AM|PM)\b", result["after"]), result
    assert result["beforeNavigate"] == 1 and result["afterNavigate"] == 0
    assert result["suppressed"] is True


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
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
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
    """),
    )
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


def test_terminal_file_reference_underlines_clear_on_same_viewport_output(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof installTerminalFileReferenceUnderlines === 'function'")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const container = document.createElement('div');
          container.className = 'terminal';
          container.style.cssText = 'position:relative;width:1000px;height:60px';
          const rows = document.createElement('div');
          rows.className = 'xterm-rows';
          rows.style.cssText = 'width:1000px;height:60px';
          container.appendChild(rows);
          document.body.appendChild(container);
          let text = 'Open static_src/js/yolomux/00_bootstrap_state.js:283';
          const term = {
            cols: 100,
            rows: 3,
            buffer: {active: {viewportY: 0, getLine: index => index === 0 ? {isWrapped: false, translateToString: () => text} : null}},
            _core: {_renderService: {dimensions: {css: {cell: {width: 10, height: 20}}}}},
          };
          const controller = installTerminalFileReferenceUnderlines('1', term, container, {
            isActive: () => true,
            targetResolver: async (_session, reference) => ({path: `/repo/${reference.path}`}),
          });
          const initial = await controller.refresh();
          const before = container.querySelectorAll('.terminal-file-link-underline').length;
          text = 'No file references here';
          controller.schedule({reason: 'output'});
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const after = container.querySelectorAll('.terminal-file-link-underline').length;
          controller.dispose();
          container.remove();
          return {initial, before, after};
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["initial"] == 1 and metrics["before"] == 1, metrics
    assert metrics["after"] == 0, metrics


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

    def disclosure_metrics(menu_id):
        return browser.execute_script(
            """
            const wrapper = document.querySelector(`.app-menu[data-app-menu="${arguments[0]}"]`);
            const button = wrapper?.querySelector(':scope > .app-menu-button');
            const disclosure = button ? getComputedStyle(button, '::before') : null;
            const resolveColor = token => {
              const probe = document.createElement('i');
              probe.style.color = `var(${token})`;
              document.body.appendChild(probe);
              const color = getComputedStyle(probe).color;
              probe.remove();
              return color;
            };
            return {
              exists: Boolean(button && disclosure),
              beforeDisplay: disclosure?.display || '',
              beforeWidth: disclosure?.width || '',
              beforeBorderLeftWidth: disclosure?.borderLeftWidth || '',
              beforeBorderRightWidth: disclosure?.borderRightWidth || '',
              borderLeftColor: disclosure?.borderLeftColor || '',
              transform: disclosure?.transform || '',
              closedColor: resolveColor('--disclosure-triangle-collapsed-color'),
              expandedColor: resolveColor('--disclosure-triangle-expanded-color'),
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
        fast_pointer_actions(browser).move_to_element(first_command).perform()
        hover = WebDriverWait(browser, 5).until(
            lambda _driver: (state if (state := menu_metrics(menu_id))["activeCommandCount"] >= 1 else False)
        )
        assert hover["activeCommandCount"] >= 1, hover

    browser.find_element("css selector", '.app-menu[data-app-menu="file"] > .app-menu-button').click()
    fast_pointer_actions(browser).move_to_element(browser.find_element("css selector", '.app-menu[data-app-menu="view"] > .app-menu-button')).perform()
    switched = WebDriverWait(browser, 5).until(
        lambda _driver: (state if (state := menu_metrics("view"))["visible"] else False)
    )
    assert switched["openIds"] == ["view"], switched

    browser.find_element("css selector", "#panel-1").click()
    closed = WebDriverWait(browser, 5).until(
        lambda _driver: (state if not (state := menu_metrics("view"))["open"] else False)
    )
    assert closed["openIds"] == [], closed
    disclosure_closed = disclosure_metrics("file")
    assert disclosure_closed["exists"] is True, disclosure_closed
    assert disclosure_closed["beforeDisplay"] == "block", disclosure_closed
    assert disclosure_closed["beforeWidth"] == "0px", disclosure_closed
    assert disclosure_closed["beforeBorderLeftWidth"] == "5px", disclosure_closed
    assert disclosure_closed["beforeBorderRightWidth"] == "0px", disclosure_closed
    assert disclosure_closed["borderLeftColor"] == disclosure_closed["closedColor"], disclosure_closed
    assert disclosure_closed["transform"] == "none", disclosure_closed

    browser.find_element("css selector", '.app-menu[data-app-menu="file"] > .app-menu-button').click()
    assert WebDriverWait(browser, 5).until(lambda _driver: menu_metrics("file")["open"])
    disclosure_open = WebDriverWait(browser, 5).until(
        lambda _driver: (
            state
            if (state := disclosure_metrics("file"))["borderLeftColor"] == state["expandedColor"]
            and state["transform"] != "none"
            else False
        )
    )
    assert disclosure_open["borderLeftColor"] == disclosure_open["expandedColor"], disclosure_open
    assert disclosure_open["transform"] != "none", disclosure_open
    fast_pointer_actions(browser).move_to_element(browser.find_element("css selector", ".xterm")).click().perform()
    terminal_closed = WebDriverWait(browser, 5).until(
        lambda _driver: (state if not (state := menu_metrics("file"))["open"] else False)
    )
    assert terminal_closed["openIds"] == [], terminal_closed


def test_live_compact_menus_root_opens_on_touch_sized_topbar(browser, tmp_path):
    def touch_tap(element):
        browser.execute_script(
            """
            const tap = node => {
              node.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true, cancelable: true, button: 0, pointerType: 'touch', isPrimary: true,
              }));
              node.dispatchEvent(new PointerEvent('pointerup', {
                bubbles: true, cancelable: true, button: 0, pointerType: 'touch', isPrimary: true,
              }));
              node.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, button: 0}));
            };
            tap(arguments[0]);
            """,
            element,
        )

    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1", "2"], dangerously_yolo=True, available_agents=["claude", "codex", "term"])
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return window.__terminalOpened >= 1 && typeof renderSessionButtons === 'function'")
    )
    browser.execute_script(
        """
        compactTopbarForViewport = () => true;
        topbarActivityUsesPhoneActionsRail = () => true;
        mobileSinglePaneMode = () => true;
        terminalCommands.push('bash', 'csh', 'dash', 'rbash', 'zsh');
        document.body.classList.add('app-topbar-touch-compact', 'app-vw-lte-600');
        renderSessionButtons({force: true});
        """
    )
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(done));
        """
    )
    activity_metrics = browser.execute_script(
        """
        renderSessionButtons({force: true});
        renderSessionButtons({force: true});
        renderSessionButtons({force: true});
        const actions = document.querySelector('.actions');
        const activities = Array.from(document.querySelectorAll('#topbarActivity'));
            const activity = activities[0];
            const refresh = document.getElementById('refreshMeta');
            const rect = node => node?.getBoundingClientRect?.() || {left: 0, right: 0};
        return {
          count: activities.length,
          inActions: activity?.parentElement === actions,
          actionChildren: Array.from(actions?.children || []).filter(node => node.id === 'topbarActivity').length,
          activityRight: rect(activity).right,
          refreshLeft: rect(refresh).left,
        };
        """
    )
    assert activity_metrics["count"] == 1, activity_metrics
    assert activity_metrics["inActions"] is True, activity_metrics
    assert activity_metrics["actionChildren"] == 1, activity_metrics
    assert activity_metrics["activityRight"] <= activity_metrics["refreshLeft"] + 1, activity_metrics
    button = WebDriverWait(browser, 5).until(
        lambda driver: driver.find_element("css selector", ".app-menu--nested-root > .app-menu-button")
    )
    button.click()
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        setTimeout(() => {
          const root = document.querySelector('.app-menu--nested-root');
          const button = root?.querySelector(':scope > .app-menu-button');
          const popover = root?.querySelector(':scope > .app-menu-popover');
          const style = popover ? getComputedStyle(popover) : null;
          const rect = popover?.getBoundingClientRect?.();
          done({
            open: root?.classList.contains('open') || false,
            expanded: button?.getAttribute('aria-expanded') || '',
            visible: Boolean(popover && style.visibility !== 'hidden' && Number.parseFloat(style.opacity || '0') > 0.9 && rect.width > 20 && rect.height > 20),
            labels: Array.from(popover?.querySelectorAll(':scope > .app-menu-submenu-wrap > .app-menu-command') || []).map(node => node.textContent.replace(/\\s+/g, ' ').trim()),
            errors: window.__bootErrors || [],
            rejections: window.__bootRejections || [],
          });
        }, 300);
        """
    )
    assert metrics["open"] is True, metrics
    assert metrics["expanded"] == "true", metrics
    assert metrics["visible"] is True, metrics
    assert metrics["labels"] == ["File>", "View>", "tmux>", "Tabs>", "Help>"], metrics
    assert metrics["errors"] == []
    assert metrics["rejections"] == []

    # This is a real rendered touch sheet, not a source-only assertion. The compact agent pairs
    # must remain one row each and shell choices must remain chips inside the viewport.
    tabs_button = browser.execute_script(
        """
        const root = document.querySelector('.app-menu--nested-root');
        return Array.from(root?.querySelectorAll(':scope > .app-menu-popover > .app-menu-submenu-wrap > .app-menu-command') || [])
          .find(button => button.textContent.replace(/\\s+/g, ' ').trim().startsWith('Tabs')) || null;
        """
    )
    touch_tap(tabs_button)
    compact_tabs = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const root = document.querySelector('.app-menu--nested-root');
            const sheet = root?.querySelector(':scope > .app-menu-popover');
            const pairs = Array.from(root?.querySelectorAll('.app-menu-command-pair') || []);
            const shells = root?.querySelector('.app-menu-command-row--shells');
            if (!sheet || pairs.length !== 2 || !shells) return null;
            const rect = node => {
              const box = node.getBoundingClientRect();
              return {left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: box.width, height: box.height};
            };
            return {sheet: rect(sheet), pairs: pairs.map(rect), pairButtons: pairs.map(pair => Array.from(pair.querySelectorAll('.app-menu-command')).map(rect)), shells: rect(shells), shellButtons: Array.from(shells.querySelectorAll('.app-menu-command')).map(rect), viewport: {width: visualViewport.width, height: visualViewport.height}};
            """
        )
    )
    assert compact_tabs["sheet"]["left"] >= -1 and compact_tabs["sheet"]["right"] <= compact_tabs["viewport"]["width"] + 1, compact_tabs
    assert all(pair["height"] <= 46 for pair in compact_tabs["pairs"]), compact_tabs
    assert all(len(pair) == 2 and pair[0]["right"] <= pair[1]["left"] + 12 for pair in compact_tabs["pairButtons"]), compact_tabs
    assert all(button["height"] >= 40 for pair in compact_tabs["pairButtons"] for button in pair), compact_tabs
    assert compact_tabs["shells"]["right"] <= compact_tabs["sheet"]["right"] + 1, compact_tabs
    assert len(compact_tabs["shellButtons"]) == 5, compact_tabs
    assert all(button["height"] >= 36 for button in compact_tabs["shellButtons"]), compact_tabs

    # Use the real touch event path to launch an explicit Xterm shell. A launch must not merely
    # create tmux state in the background: it closes Menus and makes the new session the active pane.
    shell_button = browser.execute_script(
        "return document.querySelector('.app-menu-command-row--shells .app-menu-command')"
    )
    touch_tap(shell_button)
    launch_state = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
                const request = Array.from(window.__bootFetches || []).find(call => call.path === '/api/create-session');
                if (!request || layoutSlots.left?.active !== '3') return null;
                const panel = document.getElementById('panel-3');
                const panelRect = panel?.getBoundingClientRect?.();
                if (!panel || !panel.querySelector('.xterm') || !panelRect?.width || !panelRect?.height) return null;
                const root = document.querySelector('.app-menu--nested-root');
                return {request, active: activeSessions.slice(), selected: layoutSlots.left?.active || '', tabs: layoutSlots.left?.tabs || [], panel: {width: panelRect.width, height: panelRect.height}, rootOpen: root?.classList.contains('open') || false};
            """
        )
    )
    assert launch_state["request"]["method"] == "POST", launch_state
    assert "agent=term" in launch_state["request"]["search"] and "terminal=bash" in launch_state["request"]["search"], launch_state
    assert launch_state["selected"] == "3" and "3" in launch_state["tabs"] and launch_state["panel"]["height"] > 20 and launch_state["rootOpen"] is False, launch_state

    # A second phone tap is a disclosure toggle, not a grace-period reopen. This keeps the compact
    # root usable as a one-tap close affordance without changing full desktop menu hover behavior.
    button = browser.find_element("css selector", ".app-menu--nested-root > .app-menu-button")
    button.click()
    assert WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('.app-menu--nested-root')?.classList.contains('open') || false")
    )
    browser.execute_script("arguments[0].click()", button)
    closed = WebDriverWait(browser, 5).until(
        lambda driver: (state if not (state := driver.execute_script(
            """
            const root = document.querySelector('.app-menu--nested-root');
            const button = root?.querySelector(':scope > .app-menu-button');
            const popover = root?.querySelector(':scope > .app-menu-popover');
            const style = popover ? getComputedStyle(popover) : null;
            return {
              open: root?.classList.contains('open') || false,
              expanded: button?.getAttribute('aria-expanded') || '',
              visible: Boolean(popover && style.visibility !== 'hidden' && Number.parseFloat(style.opacity || '0') > 0.1),
            };
            """
        ))["open"] and state["visible"] is False else False)
    )
    assert closed == {"open": False, "expanded": "false", "visible": False}, closed

    # Checked navigation is still navigation: File -> Finder must close the compact root instead of
    # inheriting keep-open behavior merely because Finder is currently visible/checked.
    button = browser.find_element("css selector", ".app-menu--nested-root > .app-menu-button")
    button.click()
    file_button = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const root = document.querySelector('.app-menu--nested-root');
            return Array.from(root?.querySelectorAll(':scope > .app-menu-popover > .app-menu-submenu-wrap > .app-menu-command') || [])
              .find(button => button.textContent.replace(/\\s+/g, ' ').trim().startsWith('File')) || null;
            """
        )
    )
    # Use the same pointerdown + synthetic click sequence as a touch browser. A compact category
    # must be an accordion disclosure even while Safari keeps the tapped button focused/hovered.
    touch_tap(file_button)
    file_commands = browser.execute_script(
        """
        return Array.from(document.querySelectorAll('.app-menu--nested-root .app-submenu-popover .app-menu-command'))
          .map(button => button.textContent.replace(/\\s+/g, ' ').trim());
        """
    )
    assert len(file_commands) >= 2, file_commands
    assert file_commands[1].startswith(("Finder", "Explorer", "File Explorer")), file_commands
    touch_tap(file_button)
    file_collapsed = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const root = document.querySelector('.app-menu--nested-root');
            const file = Array.from(root?.querySelectorAll(':scope > .app-menu-popover > .app-menu-submenu-wrap') || [])
              .find(node => node.querySelector(':scope > .app-menu-command')?.textContent.replace(/\\s+/g, ' ').trim().startsWith('File'));
            const popover = file?.querySelector(':scope > .app-submenu-popover');
            return root?.classList.contains('open') && file && !file.classList.contains('open')
              && file.querySelector(':scope > .app-menu-command')?.getAttribute('aria-expanded') === 'false'
              && getComputedStyle(popover).display === 'none';
            """
        )
    )
    assert file_collapsed is True
    browser.execute_script("arguments[0].click()", file_button)
    finder_button = browser.execute_script(
        "return document.querySelectorAll('.app-menu--nested-root .app-submenu-popover .app-menu-command')[1]"
    )
    browser.execute_script("arguments[0].click()", finder_button)
    finder_closed = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const root = document.querySelector('.app-menu--nested-root');
            return root && !root.classList.contains('open') && root.querySelector(':scope > .app-menu-button')?.getAttribute('aria-expanded') === 'false';
            """
        )
    )
    assert finder_closed is True


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
          const source = (window.__eventSources || []).find(item => item.url.startsWith('/api/client-events?channels='));
          if (!markerBefore || !source) return {error: 'missing marker or client-events source'};
          const beforeWorking = markerBefore.classList.contains('working');
          window.__fixtureAutoApprovePayload = {
            session_order: ['1'],
            sessions: {'1': {target: '1', enabled: false, last_action: 'off', screen: {key: 'working'}}},
            rules: {path: '/home/test/.config/yolomux/yolo-rules.yaml', source: 'default', rules: [], errors: []},
          };
          clientEventTransportState.connected = false;
          source.emit('ready');
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = window.__yolomuxTestWaitFor;
          const ready = await waitFor(() => document.querySelector('[data-yolo-session="1"]')?.classList.contains('working'));
          const markerAfter = document.querySelector('[data-yolo-session="1"]');
          return {
            beforeWorking,
            ready,
            connected: clientEventTransportState.connected,
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


def test_auto_approve_refresh_rebuilds_pane_tab_to_show_restored_yolo(browser, tmp_path):
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
        lambda driver: driver.execute_script("return window.__terminalOpened >= 1 && document.querySelector('[data-pane-tab=\"1\"]') !== null")
    )
    result = browser.execute_script(
        """
        const tab = () => document.querySelector('[data-pane-tab="1"]');
        const before = !!tab()?.querySelector('.session-yolo-marker');
        applyAutoApprovePayload({
          session_order: ['1'],
          sessions: {
            '1': {
              target: '1', enabled: true, last_action: 'enabled', screen: {key: 'working'},
              agent_windows: [{kind: 'codex', state: 'working', window_index: 0, window_label: '0:codex', current: true}],
            },
          },
          rules: {path: '/home/test/.config/yolomux/yolo-rules.yaml', source: 'default', rules: [], errors: []},
        });
        const marker = tab()?.querySelector('.session-yolo-marker');
        const statusDotNode = tab()?.querySelector('.session-agent-activity-marker .agent-window-status-dot.status-indicator--working');
        const statusDotStyle = statusDotNode ? getComputedStyle(statusDotNode) : null;
        const statusDotAnimation = statusDotNode?.getAnimations?.().find(animation => animation.animationName === 'agent-status-opacity-pulse');
        return {
          before,
          after: !!marker,
          active: marker?.classList.contains('active') || false,
          session: marker?.dataset.yoloSession || '',
          statusDot: !!tab()?.querySelector('.session-agent-activity-marker .agent-window-status-dot.status-indicator--working'),
          statusDotPulseMin: statusDotStyle?.getPropertyValue('--agent-status-pulse-min-opacity').trim() || '',
          statusDotKeyframeOpacities: statusDotAnimation ? statusDotAnimation.effect.getKeyframes().map(frame => frame.opacity) : [],
          errors: window.__bootErrors || [],
          rejections: window.__bootRejections || [],
        };
        """
    )
    assert result["before"] is False, result
    assert result["after"] is True, result
    assert result["active"] is True, result
    assert result["session"] == "1", result
    assert result["statusDot"] is True, result
    assert result["statusDotPulseMin"] == "0.16", result
    assert result["statusDotKeyframeOpacities"] == ["0.16", "1", "0.16"], result
    assert result["errors"] == [], result
    assert result["rejections"] == [], result


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


def test_preferences_status_examples_share_pulse_period_phase_and_live_renderers(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof selectSession === 'function' && window.__terminalOpened >= 1")
    )
    opened = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        agentStatusPulsePeriodMs = 2000;
        document.documentElement.style.setProperty('--pulse-duration', '2s');
        document.documentElement.style.setProperty('--status-pulse-step-count', '16');
        setAttentionAnimationClockDelay();
        selectSession('__prefs__').then(
          () => requestAnimationFrame(() => done({ok: true})),
          error => done({ok: false, error: String(error)})
        );
        """
    )
    assert opened["ok"], opened
    browser.execute_script(
        """
        const host = document.createElement('div');
        host.className = 'preferences-status-pulse-browser-fixture';
        host.style.cssText = 'position:fixed;inset:24px auto auto 24px;padding:12px;background:var(--panel);z-index:9999';
        host.innerHTML = preferencesStatusPulseExampleHtml();
        document.body.appendChild(host);
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelectorAll('.preferences-status-pulse-browser-fixture .agent-window-status-dot').length === 9"
        )
    )
    metrics = browser.execute_script(
        """
        const example = document.querySelector('.preferences-status-pulse-browser-fixture .preferences-status-pulse-example');
        scheduleAgentWindowActivityAnimationSync(example);
        const first = value => String(value || '').split(',')[0].trim();
        const pseudo = (node, name) => {
          const style = getComputedStyle(node, name);
          return {width: parseFloat(style.width) || 0, height: parseFloat(style.height) || 0, background: style.backgroundColor};
        };
        const read = node => {
          const dot = node.querySelector('.agent-window-status-dot');
          const style = getComputedStyle(dot);
          const animationName = first(style.animationName);
          const animation = dot.getAnimations().find(item => item.animationName === animationName);
          const timing = animation?.effect?.getComputedTiming?.() || {};
          return {
            group: node.dataset.statusPulseExampleGroup,
            state: node.dataset.statusPulseExampleState,
            animationName,
            duration: first(style.animationDuration),
            delay: first(style.animationDelay),
            timingFunction: first(style.animationTimingFunction),
            iterations: first(style.animationIterationCount),
            playState: animation?.playState || '',
            progress: Number(timing.progress),
            opacity: Number(style.opacity),
            background: style.backgroundColor,
            borderRadius: style.borderRadius,
            before: pseudo(dot, '::before'),
            after: pseudo(dot, '::after'),
          };
        };
        const groups = Object.fromEntries(['tab', 'subwindow', 'acknowledgement'].map(group => {
          const groupNode = example.querySelector(`[data-status-pulse-example="${group}"]`);
          return [group, {
            left: groupNode.getBoundingClientRect().left,
            right: groupNode.getBoundingClientRect().right,
            markers: [...groupNode.querySelectorAll('[data-status-pulse-example-group]')].map(read),
          }];
        }));
        return {groups, rootDelay: getComputedStyle(document.documentElement).getPropertyValue('--attention-animation-delay').trim()};
        """
    )
    groups = metrics["groups"]
    assert groups["tab"]["right"] < groups["subwindow"]["left"], metrics
    assert groups["subwindow"]["right"] < groups["acknowledgement"]["left"], metrics
    expected_names = {"tab": "agent-status-opacity-pulse", "subwindow": "agent-status-opacity-pulse", "acknowledgement": "agent-status-acknowledgement-fade"}
    for group, expected_name in expected_names.items():
        markers = groups[group]["markers"]
        assert [marker["state"] for marker in markers] == ["working", "attention", "cooldown"], metrics
        assert all(marker["animationName"] == expected_name for marker in markers), metrics
        assert all(marker["duration"] == "2s" and marker["delay"] == metrics["rootDelay"] for marker in markers), metrics
        assert all(marker["timingFunction"].startswith("steps(16") and marker["iterations"] == "infinite" for marker in markers), metrics
        assert all(marker["playState"] in {"pending", "running"} for marker in markers), metrics
        assert max(marker["progress"] for marker in markers) - min(marker["progress"] for marker in markers) < 0.04, metrics
        assert max(marker["opacity"] for marker in markers) - min(marker["opacity"] for marker in markers) < 0.04, metrics
    all_progress = [marker["progress"] for group in groups.values() for marker in group["markers"]]
    assert max(all_progress) - min(all_progress) < 0.04, metrics
    expected_tab_fills = {"working": "rgb(82, 210, 115)", "cooldown": "rgb(255, 214, 51)", "attention": "rgb(255, 102, 115)"}
    expected_glyph_fills = {"working": "rgb(82, 210, 115)", "cooldown": "rgb(255, 214, 51)", "attention": "rgb(220, 38, 38)"}
    for marker in groups["tab"]["markers"]:
        assert marker["background"] == expected_tab_fills[marker["state"]] and marker["borderRadius"] == "50%", metrics
    for marker in groups["subwindow"]["markers"]:
        shape = marker["after"] if marker["state"] == "working" else marker["before"]
        assert shape["width"] > 0 and shape["height"] > 0 and shape["background"] == expected_glyph_fills[marker["state"]], metrics
    for marker in groups["acknowledgement"]["markers"]:
        shape = marker["after"] if marker["state"] == "working" else marker["before"]
        assert shape["width"] > 0 and shape["height"] > 0 and shape["background"] == "rgb(154, 165, 177)", metrics


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
          yoloRadius: getComputedStyle(yoloProbe).borderRadius,
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
          yoloRadius: metrics.yoloRadius,
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
    assert metrics["yoloRadius"] == "4px", metrics
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
    page = tmp_path / "info-preferences-scrollbars.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <article id="info-panel" class="panel fixture-panel"><div class="info-list fixture-scroll"><div class="fixture-tall"></div></div></article>
      <article id="preferences-panel" class="panel fixture-panel"><div class="preferences-scroll fixture-scroll"><div class="fixture-tall"></div></div></article>
      <div id="neutral" style="width:20px;height:20px"></div>
    """, extra_css="""
      .fixture-panel { display: block; width: 240px; height: 120px; }
      .fixture-scroll { height: 100%; overflow: auto; }
      .fixture-tall { height: 900px; }
    """),
    )
    metrics = browser.execute_script(
        """
        const info = document.querySelector('.info-list');
        const prefs = document.querySelector('.preferences-scroll');
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

    browser.execute_script("document.getElementById('info-panel').classList.add('active-pane', 'focused-pane')")
    fast_pointer_actions(browser).move_to_element(browser.find_element("css selector", ".info-list")).perform()
    wait_thumb(".info-list", metrics["accent"])
    wait_thumb(".preferences-scroll", metrics["neutral"])

    browser.execute_script(
        """
        document.getElementById('info-panel').classList.remove('active-pane', 'focused-pane');
        document.getElementById('preferences-panel').classList.add('active-pane', 'focused-pane');
        """
    )
    fast_pointer_actions(browser).move_to_element(browser.find_element("css selector", ".preferences-scroll")).perform()
    wait_thumb(".preferences-scroll", metrics["accent"])
    wait_thumb(".info-list", metrics["neutral"])

    browser.execute_script("document.getElementById('preferences-panel').classList.remove('active-pane', 'focused-pane')")
    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "neutral")).perform()
    wait_thumb(".preferences-scroll", metrics["neutral"])
    fast_pointer_actions(browser).move_to_element(browser.find_element("css selector", ".preferences-scroll")).perform()
    wait_thumb(".preferences-scroll", metrics["neutral"])

    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "neutral")).perform()
    wait_thumb(".info-list", metrics["neutral"])
    wait_thumb(".preferences-scroll", metrics["neutral"])


def test_info_toolbar_height_stays_stable_during_metadata_refresh(browser, tmp_path):
    page = tmp_path / "info-toolbar-refresh-height.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <article id="panel" class="panel info-panel info-tree-panel" style="height: 360px;">
        <div class="panel-head"></div>
        <div id="actions" class="info-actions-bar info-tree-actions-bar">
          <div class="info-tree-primary-controls">
            <div class="info-tree-presets">
              <button class="info-tree-preset">Tab &gt; tmux-window</button>
              <button class="info-tree-preset active">Tab &gt; Path &gt; tmux-window</button>
              <button class="info-tree-preset">Path &gt; Branch</button>
              <button class="info-tree-preset">Linear &gt; PR</button>
              <button class="info-tree-preset">PR &gt; Branch</button>
            </div>
            <label class="info-tree-search-control"><span>Search</span><input value="" placeholder="Search YO!info"></label>
          </div>
          <div class="info-tree-group-selects">
            <span class="info-tree-order-label">Order by:</span>
            <label class="info-tree-group-select"><select><option>Tab</option></select></label>
            <span class="info-tree-order-separator">&gt;</span>
            <label class="info-tree-group-select"><select><option>Path</option></select></label>
            <span class="info-tree-order-separator">&gt;</span>
            <label class="info-tree-group-select"><select><option>tmux sub-window</option></select></label>
            <span class="info-tree-order-separator">&gt;</span>
            <label class="info-tree-group-select"><select><option>None</option></select></label>
          </div>
          <div class="info-tree-sort-controls"><label class="info-tree-group-select"><span>Sort</span><select><option>recent</option></select></label></div>
          <div class="info-subtab-actions"><button id="refresh" class="info-refresh">Refresh</button></div>
        </div>
        <div id="body" class="info-pane"></div>
      </article>
    """),
    )
    metrics = browser.execute_script(
        """
        const panel = document.getElementById('panel');
        const actions = document.getElementById('actions');
        const refresh = document.getElementById('refresh');
        const body = document.getElementById('body');
        const read = () => ({height: actions.getBoundingClientRect().height, bodyTop: body.getBoundingClientRect().top});
        const changedWidths = [];
        for (let width = 620; width <= 1900; width += 2) {
          panel.style.width = `${width}px`;
          refresh.classList.remove('loading');
          refresh.textContent = 'Refresh';
          const idle = read();
          refresh.classList.add('loading');
          refresh.textContent = 'Refresh';
          const loading = read();
          if (Math.abs(idle.height - loading.height) > 0.5 || Math.abs(idle.bodyTop - loading.bodyTop) > 0.5) {
            changedWidths.push({width, idle, loading});
          }
        }
        return {changedWidths, idleWidth: (() => {
          refresh.classList.remove('loading');
          refresh.textContent = 'Refresh';
          return refresh.getBoundingClientRect().width;
        })(), loadingWidth: (() => {
          refresh.classList.add('loading');
          refresh.textContent = 'Refresh';
          return refresh.getBoundingClientRect().width;
        })()};
        """
    )
    assert metrics["changedWidths"] == [], metrics
    assert abs(metrics["idleWidth"] - metrics["loadingWidth"]) <= 0.5, metrics


def test_info_scroll_preserves_immediate_parent_header(browser, tmp_path):
    page = tmp_path / "info-tree-sticky-parent.html"
    records = "\n".join(
        f"""
        <div class="info-tree-record info-tree-item{' info-tree-item-first' if index == 0 else ''}{' info-tree-item-last' if index == 23 else ''}">
          <div class="info-tree-record-main">
            <div class="info-tree-field info-tree-field-path"><span class="info-tree-field-label">path:</span><span class="info-tree-field-value"><button type="button" class="info-tree-action-link info-tree-action-link-path">/repo/app/{index}</button></span></div>
            <div class="info-tree-field info-tree-field-branch"><span class="info-tree-field-label">Git branch:</span><span class="info-tree-field-value"><span class="info-tree-value-text">feature/context</span></span></div>
            <div class="info-tree-field info-tree-field-tab"><span class="info-tree-field-label">Tab(tmux session):</span><span class="info-tree-field-value"><button type="button" class="info-tree-action-link">tab-{index}</button></span></div>
            <div class="info-tree-field info-tree-field-ai"><span class="info-tree-field-label">tmux sub-window:</span><span class="info-tree-field-value"><button type="button" class="info-tree-action-link">0:codex</button></span></div>
            <div class="info-tree-field info-tree-field-pr"><span class="info-tree-field-label">GitHub PR:</span><span class="info-tree-field-value"><a href="#">#1</a> PR description {index}</span></div>
            <div class="info-tree-field info-tree-field-updated"><span class="info-tree-field-label">updated:</span><span class="info-tree-field-value"><span class="info-tree-meta-updated">{index} days ago</span></span></div>
          </div>
        </div>
        """
        for index in range(24)
    )
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(f"""
      <div class="info-tree-panel" style="width: 680px; height: 260px; display: grid; grid-template-rows: auto minmax(0, 1fr);">
        <div id="info-tree-actions" class="info-actions-bar info-tree-actions-bar">YO!info controls</div>
        <div class="info-pane info-tree-pane-scrolled">
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
                      <span class="info-tree-group-dimension">Git branch:</span>
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
    """),
    )
    metrics = browser.execute_script(
        """
        const scroller = document.getElementById('info-tree-scroller');
        const actions = document.getElementById('info-tree-actions');
        const infoPane = document.querySelector('.info-pane');
        const rootSummary = document.getElementById('path-summary');
        const branchSummary = document.getElementById('branch-summary');
        const prSummary = document.getElementById('pr-summary');
        const resolvedColor = value => {
          const probe = document.createElement('span');
          probe.style.color = value;
          document.body.appendChild(probe);
          const color = getComputedStyle(probe).color;
          probe.remove();
          return color;
        };
        const colorSnapshot = () => ({
          text: resolvedColor('var(--text)'),
          themeAccent: resolvedColor('var(--pane-tab-active-bg)'),
          branchBlue: resolvedColor('var(--link-soft)'),
          pathGroup: getComputedStyle(rootSummary.querySelector('.info-tree-group-label')).color,
          pathLeaf: getComputedStyle(document.querySelector('.info-tree-field-path .info-tree-action-link')).color,
          branchGroup: getComputedStyle(branchSummary.querySelector('.info-tree-group-label')).color,
          branchLeaf: getComputedStyle(document.querySelector('.info-tree-field-branch .info-tree-value-text')).color,
          aiLeafLabel: getComputedStyle(document.querySelector('.info-tree-field-ai .info-tree-field-label')).color,
          aiLeafValue: getComputedStyle(document.querySelector('.info-tree-field-ai .info-tree-action-link')).color,
        });
        const darkColors = colorSnapshot();
        document.body.classList.add('theme-light');
        const lightColors = colorSnapshot();
        document.body.classList.remove('theme-light');
        const initialBranchTop = branchSummary.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
        scroller.scrollTop = scroller.scrollHeight;
        scroller.scrollTop = initialBranchTop + 90;
        return new Promise(resolve => requestAnimationFrame(() => {
          const scrollerRect = scroller.getBoundingClientRect();
          const actionsRect = actions.getBoundingClientRect();
          const rootRect = rootSummary.getBoundingClientRect();
          const branchRect = branchSummary.getBoundingClientRect();
          const prRect = prSummary.getBoundingClientRect();
          const labelProbeX = summary => {
            const rect = summary.querySelector('.info-tree-group-label').getBoundingClientRect();
            return rect.left + Math.min(4, rect.width / 2);
          };
          const topElement = document.elementFromPoint(labelProbeX(rootSummary), scrollerRect.top + Math.min(12, rootRect.height / 2));
          const branchElement = document.elementFromPoint(labelProbeX(branchSummary), scrollerRect.top + rootRect.height + Math.min(12, branchRect.height / 2));
          const prElement = document.elementFromPoint(labelProbeX(prSummary), scrollerRect.top + rootRect.height + branchRect.height + Math.min(12, prRect.height / 2));
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
            maskZ: Number.parseInt(maskStyle.zIndex, 10),
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
            darkColors,
            lightColors,
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
    assert metrics["maskZ"] == 2, metrics
    assert metrics["actionsZ"] > metrics["prZ"], metrics
    assert metrics["actionElementId"] == "info-tree-actions" or "YO!info controls" in metrics["actionText"], metrics
    assert [metrics["rootZ"], metrics["branchZ"], metrics["prZ"]] == [4, 5, 6], metrics
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
    assert metrics["darkColors"]["pathGroup"] == metrics["darkColors"]["text"], metrics
    assert metrics["darkColors"]["pathLeaf"] == metrics["darkColors"]["text"], metrics
    assert metrics["lightColors"]["pathGroup"] == metrics["lightColors"]["text"], metrics
    assert metrics["lightColors"]["pathLeaf"] == metrics["lightColors"]["text"], metrics
    assert metrics["darkColors"]["branchGroup"] == metrics["darkColors"]["branchBlue"], metrics
    assert metrics["darkColors"]["branchLeaf"] == metrics["darkColors"]["branchBlue"], metrics
    assert metrics["lightColors"]["branchGroup"] == metrics["lightColors"]["branchBlue"], metrics
    assert metrics["lightColors"]["branchLeaf"] == metrics["lightColors"]["branchBlue"], metrics
    assert metrics["darkColors"]["aiLeafLabel"] == metrics["darkColors"]["themeAccent"], metrics
    assert metrics["darkColors"]["aiLeafValue"] == metrics["darkColors"]["themeAccent"], metrics
    assert metrics["lightColors"]["aiLeafLabel"] == metrics["lightColors"]["themeAccent"], metrics
    assert metrics["lightColors"]["aiLeafValue"] == metrics["lightColors"]["themeAccent"], metrics


def test_info_tree_sibling_records_share_one_rounded_outline(browser, tmp_path):
    page = tmp_path / "info-tree-shared-record-outline.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <div class="info-tree" style="width: 760px">
        <details class="info-tree-group info-tree-item info-tree-item-last" data-info-dimension="path" data-info-depth="0" open>
          <summary>
            <span class="info-tree-group-dimension">PATH:</span>
            <span class="info-tree-group-label-line"><span class="info-tree-group-label">/home/test/yolomux.dev8001</span></span>
          </summary>
          <div class="info-tree-group-children">
            <div id="first-record" class="info-tree-record info-tree-item info-tree-item-first">
              <div class="info-tree-record-main">
                <div class="info-tree-field info-tree-field-branch"><span class="info-tree-field-label">Git branch:</span><span class="info-tree-field-value">yolomux.dev8001</span></div>
                <div class="info-tree-field info-tree-field-ai"><span class="info-tree-field-label">tmux sub-window:</span><span class="info-tree-field-value">1:claude</span></div>
              </div>
            </div>
            <div id="last-record" class="info-tree-record info-tree-item info-tree-item-last">
              <div class="info-tree-record-main">
                <div class="info-tree-field info-tree-field-branch"><span class="info-tree-field-label">Git branch:</span><span class="info-tree-field-value">yolomux.dev8001</span></div>
                <div class="info-tree-field info-tree-field-ai"><span class="info-tree-field-label">tmux sub-window:</span><span class="info-tree-field-value">2:codex</span></div>
              </div>
            </div>
          </div>
        </details>
      </div>
    """),
    )
    metrics = browser.execute_script(
        """
        const first = document.getElementById('first-record');
        const last = document.getElementById('last-record');
        const firstRect = first.getBoundingClientRect();
        const lastRect = last.getBoundingClientRect();
        const firstStyle = getComputedStyle(first);
        const lastStyle = getComputedStyle(last);
        return {
          firstTopLeft: firstStyle.borderTopLeftRadius,
          firstTopRight: firstStyle.borderTopRightRadius,
          firstBottomLeft: firstStyle.borderBottomLeftRadius,
          firstBottomRight: firstStyle.borderBottomRightRadius,
          lastTopLeft: lastStyle.borderTopLeftRadius,
          lastTopRight: lastStyle.borderTopRightRadius,
          lastBottomLeft: lastStyle.borderBottomLeftRadius,
          lastBottomRight: lastStyle.borderBottomRightRadius,
          firstBottomBorder: firstStyle.borderBottomWidth,
          lastTopBorder: lastStyle.borderTopWidth,
          seamGap: lastRect.top - firstRect.bottom,
          leftDelta: Math.abs(lastRect.left - firstRect.left),
          rightDelta: Math.abs(lastRect.right - firstRect.right),
        };
        """
    )
    assert metrics["firstTopLeft"] == "8px" and metrics["firstTopRight"] == "8px", metrics
    assert metrics["firstBottomLeft"] == "0px" and metrics["firstBottomRight"] == "0px", metrics
    assert metrics["lastTopLeft"] == "0px" and metrics["lastTopRight"] == "0px", metrics
    assert metrics["lastBottomLeft"] == "8px" and metrics["lastBottomRight"] == "8px", metrics
    assert metrics["firstBottomBorder"] == "1px" and metrics["lastTopBorder"] == "0px", metrics
    assert abs(metrics["seamGap"]) <= 0.1, metrics
    assert metrics["leftDelta"] <= 0.1 and metrics["rightDelta"] <= 0.1, metrics


def test_info_tree_top_row_is_not_masked_at_scroll_origin(browser, tmp_path):
    page = tmp_path / "info-tree-top-visible.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <div class="info-tree-panel" style="width: 760px; height: 190px; display: grid; grid-template-rows: auto minmax(0, 1fr);">
        <div class="info-actions-bar info-tree-actions-bar">YO!info controls</div>
        <div class="info-pane">
          <div id="info-tree-scroller" class="info-list info-tree-list">
            <div class="info-tree">
              <details class="info-tree-group info-tree-item" data-info-dimension="path" data-info-depth="0" open>
                <summary id="path-summary">
                  <span class="info-tree-group-dimension">Path</span>
                  <span class="info-tree-group-label-line"><span class="info-tree-group-label">/repo/top-visible</span></span>
                </summary>
                <div class="info-tree-group-children">
                  <div class="info-tree-record info-tree-item info-tree-item-last">
                    <div class="info-tree-record-main">
                      <div class="info-tree-field info-tree-field-branch"><span class="info-tree-field-label">Git branch:</span><span class="info-tree-field-value"><span class="info-tree-value-text">main</span></span></div>
                    </div>
                  </div>
                </div>
              </details>
            </div>
          </div>
        </div>
      </div>
    """),
    )
    metrics = browser.execute_script(
        """
        const scroller = document.getElementById('info-tree-scroller');
        const summary = document.getElementById('path-summary');
        const pane = document.querySelector('.info-pane');
        const scrollerRect = scroller.getBoundingClientRect();
        const summaryRect = summary.getBoundingClientRect();
        const maskStyle = getComputedStyle(pane, '::before');
        const topElement = document.elementFromPoint(scrollerRect.left + 120, scrollerRect.top + Math.min(12, summaryRect.height / 2));
        return {
          scrollTop: scroller.scrollTop,
          paneScrolled: pane.classList.contains('info-tree-pane-scrolled'),
          maskContent: maskStyle.content,
          summaryTopDelta: summaryRect.top - scrollerRect.top,
          summaryText: topElement ? topElement.textContent : '',
        };
        """
    )
    assert metrics["scrollTop"] == 0, metrics
    assert metrics["paneScrolled"] is False, metrics
    assert metrics["maskContent"] == "none", metrics
    assert 0 <= metrics["summaryTopDelta"] <= 6, metrics
    assert "/repo/top-visible" in metrics["summaryText"], metrics


def test_info_scroll_top_mask_hides_clipped_leaf_text(browser, tmp_path):
    page = tmp_path / "info-tree-top-mask.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html("""
      <div class="info-tree-panel" style="width: 760px; height: 190px; display: grid; grid-template-rows: auto minmax(0, 1fr);">
        <div class="info-actions-bar info-tree-actions-bar">YO!info controls</div>
        <div class="info-pane info-tree-pane-scrolled">
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
                      <div class="info-tree-field info-tree-field-tab"><span class="info-tree-field-label">Tab(tmux session):</span><span class="info-tree-field-value"><span id="leak-sentinel" style="color: #ff00ff; font: 900 18px/1 var(--mono-font);">LEAKMAGENTALEAKMAGENTALEAKMAGENTA</span></span></div>
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
    """),
    )
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
    leak_pixels = 0
    for y in range(y0, max(y0 + 1, y1), 2):
        for x in range(x0, max(x0 + 1, x1), 2):
            r, g, b = image.getpixel((x, y))[:3]
            if r >= 130 and b >= 130 and r - g >= 50 and b - g >= 50:
                leak_pixels += 1
    assert leak_pixels == 0, {"leakPixels": leak_pixels, **metrics}


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
    for th in ("light", "dark"):
        text_lum = _css_luminance_255(theme_metrics[th]["inactiveDirColor"])
        bg_lum = _css_luminance_255(theme_metrics[th]["inactiveTabBg"])
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
          const waitFor = window.__yolomuxTestWaitFor;
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


def test_editor_right_click_preserves_existing_codemirror_diff_selection(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof fileEditorItemFor === 'function' && typeof applyLayoutSlots === 'function' && document.querySelector('#grid') !== null;"
        )
    )
    setup = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          const path = '/home/test/right-click-selection.md';
          const item = fileEditorItemFor(path);
          const original = Array.from({length: 24}, (_value, index) => `Original line ${index + 1} has enough text for right-click coverage.`).join('\\n');
          const content = original.replace('Original line 1', 'Changed line 1');
          setFileState(path, {
            kind: 'text',
            content,
            original: content,
            dirty: false,
            language: 'markdown',
            gitRoot: '/home/test',
            gitTracked: true,
            gitHasHistory: true,
            gitHistory: [{sha: 'HEAD'}],
            diffLoaded: true,
            diffUnavailable: false,
            diff: 'diff --git a/right-click-selection.md b/right-click-selection.md',
            diffOriginal: original,
            diffWorking: content,
          });
          setFileEditorViewMode(path, 'diff', item);
          registerFileEditorLayoutItem(path, {item});
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([item], item);
          applyLayoutSlots(next, {focusSession: item, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          for (let attempt = 0; attempt < 220; attempt += 1) {
            const panel = panelNodes.get(item);
            if (panel?._cmMode === 'diff' && panel._cmView?.contentDOM) {
              const view = panel._cmView;
              view.focus();
              view.dispatch({selection: {anchor: 0, head: content.length}});
              await frame();
              await frame();
              window.__editorContextMenuCount = 0;
              view.contentDOM.addEventListener('contextmenu', () => { window.__editorContextMenuCount += 1; });
              return {item, anchor: view.state.selection.main.anchor, head: view.state.selection.main.head};
            }
            await frame();
          }
          return {error: 'CodeMirror diff editor did not initialize'};
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in setup, setup
    content = browser.find_element("css selector", ".file-editor-panel .cm-content")
    fast_pointer_actions(browser).context_click(content).perform()
    after = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const panel = [...panelNodes.values()].find(node => node?._cmMode === 'diff' && node?._cmView?.contentDOM);
          const selection = panel._cmView.state.selection.main;
          done({
            anchor: selection.anchor,
            head: selection.head,
            selectedChars: Math.abs(selection.to - selection.from),
            contextMenus: window.__editorContextMenuCount,
            status: panel.querySelector('.file-editor-cursor-status')?.textContent || '',
          });
        }));
        """
    )
    assert after["anchor"] == setup["anchor"], after
    assert after["head"] == setup["head"], after
    assert after["selectedChars"] > 100, after
    assert after["contextMenus"] == 1, after
    assert f"{after['selectedChars']} selected chars" in after["status"], after


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
          const waitFor = window.__yolomuxTestWaitFor;
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
          const restoredScroller = await waitFor(() => {
            const scroller = panelNodes.get(item)?._cmView?.scrollDOM;
            return scroller && Math.abs(scroller.scrollTop - savedTop) < 32 ? scroller : null;
          }, {description: 'file editor scroll restoration'});
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
          const waitFor = window.__yolomuxTestWaitFor;
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

    fast_pointer_actions(browser).move_to_element(dockview_tab("__prefs__")).click().perform()
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

    fast_pointer_actions(browser).move_to_element(dockview_tab(setup["item"])).click().perform()
    restored = browser.execute_async_script(
        """
        const item = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const waitFor = window.__yolomuxTestWaitFor;
          const ready = await waitFor(() => Boolean(activeItemForSide('left') === item && panelNodes.get(item)?.isConnected && panelNodes.get(item)?._cmView?.scrollDOM));
          const scroller = await waitFor(() => {
            const candidate = panelNodes.get(item)?._cmView?.scrollDOM;
            return candidate && Math.abs(candidate.scrollTop - arguments[1]) < 32 ? candidate : null;
          }, {description: 'Dockview file editor scroll restoration'});
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
        setup["savedTop"],
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
          const waitFor = window.__yolomuxTestWaitFor;
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

    fast_pointer_actions(browser).move_to_element(dockview_tab(setup["other"])).click().perform()
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

    fast_pointer_actions(browser).move_to_element(dockview_tab(setup["item"])).click().perform()
    restored = browser.execute_async_script(
        """
        const item = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const waitFor = window.__yolomuxTestWaitFor;
          const scroller = await waitFor(() => {
            if (activeItemForSide('left') !== item) return null;
            const candidate = panelNodes.get(item)?.querySelector('.preferences-scroll');
            return candidate && Math.abs(candidate.scrollTop - arguments[1]) < 32 ? candidate : null;
          }, {description: 'Preferences scroll restoration'});
          return {
            active: activeItemForSide('left'),
            restoredTop: scroller?.scrollTop || 0,
            clientHeight: scroller?.clientHeight || 0,
            scrollHeight: scroller?.scrollHeight || 0,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        setup["item"],
        setup["savedTop"],
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
          transcriptMetadataState.loaded = true;
          transcriptMetadataState.loading = false;
          transcriptMetadataState.error = '';
          const branches = Array.from({length: 180}, (_value, index) => ({
            name: `feature/long-info-row-${index + 1}`,
            subject: `Long YO!info tree row ${index + 1} that makes the relationship tree scroll.`,
            updated: `2026-06-${String((index % 28) + 1).padStart(2, '0')}`,
            updated_ts: 1800000000 - index,
            current: index === 0,
            linear_ids: [`YOLO-${index + 1}`],
          }));
          transcriptMetadataState.payload = {
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
          const waitFor = window.__yolomuxTestWaitFor;
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

    fast_pointer_actions(browser).move_to_element(dockview_tab(setup["other"])).click().perform()
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

    fast_pointer_actions(browser).move_to_element(dockview_tab(setup["item"])).click().perform()
    restored = browser.execute_async_script(
        """
        const item = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const waitFor = window.__yolomuxTestWaitFor;
          const scroller = await waitFor(() => {
            if (activeItemForSide('left') !== item) return null;
            const candidate = document.getElementById('info-content');
            return candidate && Math.abs(candidate.scrollTop - arguments[1]) < 32 ? candidate : null;
          }, {description: 'YO!info scroll restoration'});
          return {
            active: activeItemForSide('left'),
            restoredTop: scroller?.scrollTop || 0,
            clientHeight: scroller?.clientHeight || 0,
            scrollHeight: scroller?.scrollHeight || 0,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        setup["item"],
        setup["savedTop"],
    )
    assert restored["active"] == setup["item"], restored
    assert abs(restored["restoredTop"] - setup["savedTop"]) < 32, {**setup, **after_other, **restored}


def test_yoinfo_external_links_survive_panel_focus_pointerdown(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof applyLayoutSlots === 'function' && typeof infoPanelRenderSignature === 'function';"
        )
    )
    browser.execute_script(
        """
        autoFocusEnabled = false;
        const next = emptyLayoutSlots();
        next[layoutTreeKey] = leafNode('left');
        next.left = paneStateWithTabs([infoItemId], infoItemId);
        applyLayoutSlots(next, {focusSession: infoItemId, forceFull: true});
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return dockviewLayoutActive()
              && activeItemForSide('left') === infoItemId
              && document.getElementById('info-content') !== null;
            """
        )
    )
    setup = browser.execute_script(
        """
        const node = document.getElementById('info-content');
        const html = `
          <div class="info-tree">
            <div class="info-tree-record info-tree-item info-tree-item-first info-tree-item-last">
              <div class="info-tree-record-main">
                <div class="info-tree-field info-tree-field-linear"><span class="info-tree-field-label">Linear:</span><span class="info-tree-field-value"><a id="linear-link" href="https://example.test/linear/DIS-2228" target="_blank" rel="noreferrer noopener">DIS-2228</a></span></div>
                <div class="info-tree-field info-tree-field-pr"><span class="info-tree-field-label">GitHub PR:</span><span class="info-tree-field-value"><a id="pr-link" href="https://example.test/pull/87" target="_blank" rel="noreferrer noopener">#87</a></span></div>
              </div>
            </div>
          </div>`;
        node.innerHTML = html;
        infoPanelRenderCache.signature = infoPanelRenderSignature();
        infoPanelRenderCache.html = html;
        window.__infoTrustedClicks = [];
        document.addEventListener('click', event => {
          if (event.target?.matches?.('#linear-link, #pr-link')) {
            window.__infoTrustedClicks.push({id: event.target.id, trusted: event.isTrusted});
          }
        }, {capture: true});
        return {
          dockview: dockviewLayoutActive(),
          linearConnected: document.getElementById('linear-link')?.isConnected === true,
          prConnected: document.getElementById('pr-link')?.isConnected === true,
        };
        """
    )
    assert setup == {"dockview": True, "linearConnected": True, "prConnected": True}, setup

    original_handle = browser.current_window_handle

    def click_external_link(selector, expected_path):
        handles_before = set(browser.window_handles)
        fast_pointer_actions(browser).move_to_element(browser.find_element("css selector", selector)).click().perform()
        WebDriverWait(browser, 3).until(lambda driver: len(set(driver.window_handles) - handles_before) == 1)
        new_handle = next(iter(set(browser.window_handles) - handles_before))
        browser.switch_to.window(new_handle)
        WebDriverWait(browser, 3).until(lambda driver: expected_path in driver.current_url)
        browser.close()
        browser.switch_to.window(original_handle)

    click_external_link("#linear-link", "/linear/DIS-2228")
    click_external_link("#pr-link", "/pull/87")
    clicks = browser.execute_script("return window.__infoTrustedClicks")
    assert clicks == [
        {"id": "linear-link", "trusted": True},
        {"id": "pr-link", "trusted": True},
    ], clicks


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
    fast_pointer_actions(browser).move_to_element(browser.find_element("css selector", ".pane-tab")).perform()
    tokens = theme_tokens()
    wait_background("#topbar-fixture", tokens["neutral"])
    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "topbar-fixture")).perform()
    wait_background("#topbar-fixture", tokens["accent"])
    fast_pointer_actions(browser).move_to_element(browser.find_element("css selector", ".pane-tab")).perform()
    wait_background("#topbar-fixture", tokens["neutral"])

    load_finder_click_toolbar_fixture(browser, tmp_path)
    tokens = theme_tokens()
    wait_background("#finder-panel .file-explorer-head", tokens["neutral"])
    fast_pointer_actions(browser).move_to_element(browser.find_element("css selector", "#finder-panel .file-explorer-head")).perform()
    wait_background("#finder-panel .file-explorer-head", tokens["accent"])
    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "terminal-panel")).perform()
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
    assert abs(repo_caret_metrics["caretFontSize"] - repo_caret_metrics["titleFontSize"]) <= 0.5, repo_caret_metrics
    assert repo_caret_metrics["caretWidth"] <= repo_caret_metrics["titleFontSize"] * 1.4, repo_caret_metrics
    assert repo_caret_metrics["titleHeight"] > 0, repo_caret_metrics
    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "modified-files-panel")).perform()
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
    fast_pointer_actions(browser).move_to_element(browser.find_element("css selector", ".file-explorer-tree-panel")).perform()
    wait_thumb(".file-explorer-tree-panel", accent)
    browser.execute_script("document.getElementById('finder-panel')?.classList.remove('active-pane', 'focused-pane')")
    fast_pointer_actions(browser).move_to_element(browser.find_element("css selector", ".file-explorer-tree-panel")).perform()
    wait_thumb(".file-explorer-tree-panel", neutral)
    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "terminal-panel")).perform()
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
    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "modified-files-panel")).perform()
    wait_thumb("#modified-files-panel", accent)
    browser.execute_script("document.getElementById('finder-panel')?.classList.remove('active-pane', 'focused-pane')")
    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "modified-files-panel")).perform()
    wait_thumb("#modified-files-panel", neutral)
    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "terminal-panel")).perform()
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
    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "collapsed-dir")).perform()
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

    browser.execute_script(
        """
        const row = document.createElement('div');
        row.id = 'tabber-hover-row';
        row.className = 'file-tree-row tabber-row kind-file';
        row.innerHTML = '<span class="file-tree-icon">T</span><span class="file-tree-name">Tabber row</span>';
        document.querySelector('.file-explorer-tree-panel').appendChild(row);
        """
    )
    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "tabber-hover-row")).perform()
    hover_metrics = browser.execute_script(
        """
        const read = theme => {
          document.body.classList.remove('theme-dark', 'theme-light');
          document.body.classList.add(theme);
          const row = getComputedStyle(document.getElementById('tabber-hover-row'));
          const probe = document.createElement('div');
          probe.style.background = 'var(--file-hover-bg)';
          probe.style.borderColor = 'var(--file-hover-border)';
          document.body.appendChild(probe);
          const expected = getComputedStyle(probe);
          const result = {
            background: row.backgroundColor,
            shadow: row.boxShadow,
            expectedBackground: expected.backgroundColor,
            expectedBorder: expected.borderColor,
          };
          probe.remove();
          return result;
        };
        return {dark: read('theme-dark'), light: read('theme-light')};
        """
    )
    for theme in ("dark", "light"):
        assert hover_metrics[theme]["background"] == hover_metrics[theme]["expectedBackground"], hover_metrics
        assert hover_metrics[theme]["expectedBorder"] in hover_metrics[theme]["shadow"], hover_metrics


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
        const expand = cluster.querySelector('[data-file-tree-expand-collapse-all="expand"]');
        const collapseAll = cluster.querySelector('[data-file-tree-expand-collapse-all="collapse"]');
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
        const expandRect = expand.getBoundingClientRect();
        const collapseAllRect = collapseAll.getBoundingClientRect();
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
        const hadLightTheme = document.body.classList.contains('theme-light');
        document.body.classList.add('theme-light');
        const lightExpandRect = expand.getBoundingClientRect();
        const lightCollapseAllRect = collapseAll.getBoundingClientRect();
        if (!hadLightTheme) document.body.classList.remove('theme-light');
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
          expandLeft: expandRect.left,
          expandRight: expandRect.right,
          expandWidth: expandRect.width,
          expandHeight: expandRect.height,
          collapseAllLeft: collapseAllRect.left,
          collapseAllRight: collapseAllRect.right,
          collapseAllWidth: collapseAllRect.width,
          collapseAllHeight: collapseAllRect.height,
          lightExpandWidth: lightExpandRect.width,
          lightCollapseAllWidth: lightCollapseAllRect.width,
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
    assert metrics["dateRight"] <= metrics["expandLeft"]
    assert metrics["expandRight"] <= metrics["collapseAllLeft"]
    assert metrics["collapseAllRight"] <= metrics["refreshLeft"]
    assert metrics["expandWidth"] == metrics["collapseAllWidth"] == 16
    assert metrics["expandHeight"] == metrics["collapseAllHeight"] == 20
    assert metrics["lightExpandWidth"] == metrics["lightCollapseAllWidth"] == 16
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

    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "tab-minimize")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('tab-minimize')).opacity") == "1"

    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "pane-zoom")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('pane-zoom')).backgroundColor") != "rgba(0, 0, 0, 0)"

    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "finder-close")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('finder-close')).opacity") == "1"

    fast_pointer_actions(browser).move_to_element(browser.find_element("id", "editor-close")).perform()
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
    assert abs(tree_metrics["iconSize"] - tree_metrics["nameSize"]) <= 0.5
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


def test_rendered_preview_find_highlights_navigates_and_cleans_up(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    metrics = browser.execute_script(
        """
        const host = document.createElement('section');
        host.className = 'file-editor-panel';
        host.style.cssText = 'width: 600px; height: 140px';
        host.innerHTML = `
          <div class="file-editor-content">
            <div class="file-editor-preview-pane-panel"><p>alpha first</p>${'<p>filler</p>'.repeat(40)}<p>beta alpha second</p></div>
            <div class="file-editor-find-overview" hidden></div>
            <form class="file-editor-preview-find-panel" hidden>
              <input type="search"><span class="file-editor-preview-find-count"></span>
              <button type="button" data-preview-find-move="-1"></button>
              <button type="button" data-preview-find-move="1"></button>
              <button type="button" data-preview-find-close></button>
            </form>
          </div>`;
        document.body.append(host);
        openPreviewFind(host);
        const input = host.querySelector('input');
        input.value = 'alpha';
        previewFindApplyQuery(host, input.value);
        const opened = {
          visible: !previewFindPanelForHost(host).hidden,
          matches: host.querySelectorAll('.file-editor-preview-find-match').length,
          overviewTicks: host.querySelectorAll('.file-editor-find-overview-tick').length,
          overviewVisible: !host.querySelector('.file-editor-find-overview').hidden,
          overviewTops: [...host.querySelectorAll('.file-editor-find-overview-tick')].map(node => Number.parseFloat(node.style.top)),
          active: host.querySelectorAll('.file-editor-preview-find-match.active').length,
          activeColor: getComputedStyle(host.querySelector('.file-editor-preview-find-match.active')).color,
          activeBackground: getComputedStyle(host.querySelector('.file-editor-preview-find-match.active')).backgroundColor,
          count: host.querySelector('.file-editor-preview-find-count').textContent,
          inputFocused: document.activeElement === input,
        };
        previewFindSelectMatch(host, 1);
        const moved = {
          activeIndex: [...host.querySelectorAll('.file-editor-preview-find-match')].findIndex(node => node.classList.contains('active')),
          overviewActiveIndex: [...host.querySelectorAll('.file-editor-find-overview-tick')].findIndex(node => node.classList.contains('active')),
          count: host.querySelector('.file-editor-preview-find-count').textContent,
        };
        closePreviewFind(host);
        const closed = {
          hidden: previewFindPanelForHost(host).hidden,
          matches: host.querySelectorAll('.file-editor-preview-find-match').length,
          overviewHidden: host.querySelector('.file-editor-find-overview').hidden,
          startsWith: host.querySelector('.file-editor-preview-pane-panel').textContent.trim().startsWith('alpha first'),
          endsWith: host.querySelector('.file-editor-preview-pane-panel').textContent.trim().endsWith('beta alpha second'),
        };
        host.remove();
        return {opened, moved, closed};
        """
    )
    opened = {**metrics["opened"], "overviewTops": None, "activeColor": None, "activeBackground": None}
    assert opened == {"visible": True, "matches": 2, "overviewTicks": 2, "overviewVisible": True, "overviewTops": None, "active": 1, "activeColor": None, "activeBackground": None, "count": "1/2", "inputFocused": True}
    assert metrics["opened"]["overviewTops"][0] < metrics["opened"]["overviewTops"][1], metrics
    assert metrics["opened"]["activeColor"] != metrics["opened"]["activeBackground"], metrics
    assert metrics["moved"] == {"activeIndex": 1, "overviewActiveIndex": 1, "count": "2/2"}
    assert metrics["closed"] == {"hidden": True, "matches": 0, "overviewHidden": True, "startsWith": True, "endsWith": True}


def test_file_editor_find_shortcut_claims_ctrl_f_before_browser_find(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    metrics = browser.execute_script(
        """
        const path = '/tmp/preview-find.md';
        const item = 'file:/tmp/preview-find.md';
        const host = document.createElement('section');
        host.className = 'file-editor-panel';
        host.dataset.filePath = path;
        host.dataset.layoutItem = item;
        host.innerHTML = `
          <div class="file-editor-content"><div class="file-editor-preview-pane-panel"><p>alpha</p></div><div class="file-editor-find-overview" hidden></div><form class="file-editor-preview-find-panel" hidden><input type="search"><span class="file-editor-preview-find-count"></span></form></div>
          <button class="file-editor-find-panel"></button>`;
        document.body.append(host);
        openFiles.set(path, {kind: 'text'});
        fileEditorViewModesForPath(path, true).set(item, 'preview');
        focusedPanelItem = item;
        const event = new KeyboardEvent('keydown', {key: 'f', ctrlKey: true, bubbles: true, cancelable: true});
        host.dispatchEvent(event);
        return new Promise(resolve => setTimeout(() => {
          const find = host.querySelector('.file-editor-preview-find-panel');
          const result = {prevented: event.defaultPrevented, visible: !find.hidden, focused: document.activeElement === find.querySelector('input')};
          host.remove();
          openFiles.delete(path);
          fileEditorViewModesForPath(path, true).delete(item);
          resolve(result);
        }, 0));
        """
    )
    assert metrics == {"prevented": True, "visible": True, "focused": True}


def test_focused_panel_search_shortcut_routes_to_each_registered_search(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    metrics = browser.execute_script(
        """
        const makePanel = (className, item, inputMarkup) => {
          const panel = document.createElement('section');
          panel.className = `panel ${className}`;
          panel.dataset.layoutItem = item;
          panel.innerHTML = inputMarkup;
          document.body.append(panel);
          return panel;
        };
        const trigger = (item, panel, selector) => {
          focusedPanelItem = item;
          const input = panel.querySelector(selector);
          input.value = 'existing query';
          const event = new KeyboardEvent('keydown', {key: 'f', ctrlKey: true, bubbles: true, cancelable: true});
          panel.dispatchEvent(event);
          return {
            prevented: event.defaultPrevented,
            focused: document.activeElement === input,
            selected: input.selectionStart === 0 && input.selectionEnd === input.value.length,
          };
        };
        const info = makePanel('info-tree-panel', infoItemId, '<input data-info-search>');
        const preferences = makePanel('preferences-panel', prefsItemId, '<input data-preferences-search>');
        const history = makePanel('search-history-panel', searchHistoryItemId, '<input data-search-history-query>');
        const results = {
          info: trigger(infoItemId, info, '[data-info-search]'),
          preferences: trigger(prefsItemId, preferences, '[data-preferences-search]'),
          history: trigger(searchHistoryItemId, history, '[data-search-history-query]'),
        };
        const unsupported = makePanel('debug-panel', debugPaneItemId, '<input>');
        focusedPanelItem = debugPaneItemId;
        const unsupportedEvent = new KeyboardEvent('keydown', {key: 'f', ctrlKey: true, bubbles: true, cancelable: true});
        unsupported.dispatchEvent(unsupportedEvent);
        results.unsupportedPrevented = unsupportedEvent.defaultPrevented;
        info.remove();
        preferences.remove();
        history.remove();
        unsupported.remove();
        return results;
        """
    )
    assert metrics == {
        "info": {"prevented": True, "focused": True, "selected": True},
        "preferences": {"prevented": True, "focused": True, "selected": True},
        "history": {"prevented": True, "focused": True, "selected": True},
        "unsupportedPrevented": False,
    }


def test_codemirror_find_uses_the_shared_scrollbar_overview(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    metrics = browser.execute_script(
        """
        const path = '/tmp/editor-find.py';
        const item = 'file:/tmp/editor-find.py';
        const newline = String.fromCharCode(10);
        const text = `alpha first${newline}${`filler${newline}`.repeat(40)}alpha last`;
        const lineAt = position => ({number: text.slice(0, position).split(newline).length});
        const host = document.createElement('section');
        host.className = 'file-editor-panel';
        host.dataset.filePath = path;
        host.dataset.layoutItem = item;
        host.innerHTML = `<div class="file-editor-content"><div class="file-editor-find-overview" hidden></div><div class="cm-search"><input name="search" value="alpha"></div></div>`;
        host._cmView = {state: {doc: {toString: () => text, lines: text.split(newline).length, lineAt}, selection: {main: {from: 0, to: 5, head: 0}}}};
        document.body.append(host);
        openFiles.set(path, {kind: 'text'});
        fileEditorViewModesForPath(path, true).set(item, 'edit');
        refreshCodeMirrorFindOverview(host);
        const rail = host.querySelector('.file-editor-find-overview');
        const opened = {
          visible: !rail.hidden,
          ticks: rail.querySelectorAll('.file-editor-find-overview-tick').length,
          active: rail.querySelectorAll('.file-editor-find-overview-tick.active').length,
          tops: [...rail.querySelectorAll('.file-editor-find-overview-tick')].map(node => Number.parseFloat(node.style.top)),
        };
        host.querySelector('input[name="search"]').value = '';
        refreshCodeMirrorFindOverview(host);
        const closed = {hidden: rail.hidden, ticks: rail.children.length};
        host.remove();
        openFiles.delete(path);
        fileEditorViewModesForPath(path, true).delete(item);
        return {opened, closed};
        """
    )
    assert metrics["opened"]["visible"] is True
    assert metrics["opened"]["ticks"] == 2
    assert metrics["opened"]["active"] == 1
    assert metrics["opened"]["tops"][0] < metrics["opened"]["tops"][1]
    assert metrics["closed"] == {"hidden": True, "ticks": 0}


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
    assert metrics["nextTitle"] == "下一项 (Enter)"
    assert metrics["previousTitle"] == "上一项 (Shift+Enter)"
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
            optionViews: panel._cmViews?.length || 0,
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
<div class="preferences-setting-advisory" id="pref-adv">
  <span id="pref-adv-text">Large browser uploads are buffered in memory.</span>
  <code id="pref-adv-code">rsync -avz &lt;local-path&gt; host:/path/</code>
  <button type="button" class="preferences-inline-action" id="pref-adv-copy">Copy rsync example</button>
</div>
<span class="agent-icon codex" id="agent-ico">A</span>
<span class="session-state-badge" id="badge-neutral">run</span>
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


def _css_rgb_triplet(css_color):
    nums = [float(n) for n in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", css_color)[:3]]
    assert len(nums) == 3, f"expected an RGB-ish CSS color, got {css_color!r}"
    if css_color.strip().startswith("color(srgb"):
        nums = [n * 255 for n in nums]
    return tuple(max(0.0, min(255.0, n)) for n in nums)


def _css_luminance_255(css_color):
    r, g, b = _css_rgb_triplet(css_color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(rgb_a, rgb_b):
    def rel_lum(css_color):
        r, g, b = _css_rgb_triplet(css_color)

        def chan(c):
            c = c / 255.0
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)

    la, lb = rel_lum(rgb_a), rel_lum(rgb_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_light_mode_surfaces_are_readable_not_dark_boxes(browser, tmp_path):
    page = tmp_path / "light-surfaces.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        light_mode_surfaces_fixture_html("theme-light"),
    )
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
    for box in ("cp-dlg", "ks-dlg", "pref-adv", "pref-adv-code", "sub", "rename-inp", "session-rename-inp", "session-rename-cancel", "md-pre", "info-pane", "info-content", "info-tree-summary"):
        assert _css_luminance_255(style[box]["bg"]) > 180, f"{box} background must be light in light mode, got {style[box]['bg']}"
    assert style["bodyVars"]["infoTreeBorder"] == "#8793a3", style["bodyVars"]
    assert style["bodyVars"]["infoTreeLine"] == "rgb(71 85 105 / 0.42)", style["bodyVars"]
    assert style["bodyVars"]["infoRecordBorder"] == style["bodyVars"]["infoTreeLine"], style["bodyVars"]
    assert style["bodyVars"]["infoTreeLine"] != style["bodyVars"]["infoTreeBorder"], style["bodyVars"]

    # (b) Text must contrast with its surface. Where the element bg is transparent, it sits on the white page.
    page_white = "rgb(255, 255, 255)"
    text_checks = {
        "cp-row": "cp-dlg", "cp-grp": "cp-dlg", "cp-det": "cp-dlg", "cp-kb": "cp-dlg",
        "ks-kbd": "ks-kbd", "gr-title": "gr", "gr-warn": "gr", "pref-adv-text": "pref-adv", "pref-adv-code": "pref-adv-code", "pref-adv-copy": "pref-adv-copy", "agent-ico": None,
        "badge-neutral": "badge-neutral", "ym-inactive": "ym-inactive",
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


def test_preferences_upload_advisory_matches_dark_theme_and_light_readability(browser, tmp_path):
    page = tmp_path / "preferences-advisory.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        light_mode_surfaces_fixture_html("theme-dark"),
    )
    dark = browser.execute_script(
        """
        const read = id => {
          const s = getComputedStyle(document.getElementById(id));
          return {color: s.color, bg: s.backgroundColor, border: s.borderTopColor};
        };
        return {
          advisory: read('pref-adv'),
          code: read('pref-adv-code'),
          copy: read('pref-adv-copy'),
          panel2: getComputedStyle(document.body).getPropertyValue('--panel2').trim(),
        };
        """
    )
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        light_mode_surfaces_fixture_html("theme-light"),
    )
    light = browser.execute_script(
        """
        const read = id => {
          const s = getComputedStyle(document.getElementById(id));
          return {color: s.color, bg: s.backgroundColor, border: s.borderTopColor};
        };
        return {advisory: read('pref-adv'), code: read('pref-adv-code'), copy: read('pref-adv-copy')};
        """
    )

    assert _css_luminance_255(dark["advisory"]["bg"]) < 85, dark
    assert _css_luminance_255(dark["code"]["bg"]) < 95, dark
    assert dark["advisory"]["bg"] != "rgb(255, 231, 163)", dark
    assert _contrast_ratio(dark["advisory"]["color"], dark["advisory"]["bg"]) >= 3.0, dark
    assert _contrast_ratio(dark["code"]["color"], dark["code"]["bg"]) >= 4.5, dark
    assert _contrast_ratio(dark["copy"]["color"], dark["copy"]["bg"]) >= 3.0, dark
    assert _css_luminance_255(light["advisory"]["bg"]) > 180, light
    assert _css_luminance_255(light["code"]["bg"]) > 225, light
    assert _contrast_ratio(light["advisory"]["color"], light["advisory"]["bg"]) >= 4.5, light
    assert _contrast_ratio(light["code"]["color"], light["code"]["bg"]) >= 7.0, light
    assert _contrast_ratio(light["copy"]["color"], light["copy"]["bg"]) >= 3.0, light


def test_light_editor_image_backdrop_is_light(browser, tmp_path):
    page = tmp_path / "light-editor-image.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        light_mode_surfaces_fixture_html("editor-theme-light").replace(
            LIGHT_MODE_SURFACES,
            '<div class="file-editor-image-panel" id="imgp"><img class="file-editor-image" id="img" src="#"></div>',
        ),
    )
    style = browser.execute_script(
        "return {panel: getComputedStyle(document.getElementById('imgp')).backgroundColor,"
        " img: getComputedStyle(document.getElementById('img')).backgroundColor};"
    )

    assert _css_luminance_255(style["panel"]) > 180, f"editor-light image panel must be light, got {style['panel']}"
    assert _css_luminance_255(style["img"]) > 180, f"editor-light image backdrop must be light, got {style['img']}"


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
            const phrases = {{
              Find: '查找', Replace: '替换', next: '下一项', previous: '上一项', all: '全部',
              'match case': '区分大小写', regexp: '正则表达式', 'by word': '全字匹配',
              replace: '替换', 'replace all': '全部替换', close: '关闭',
            }};
            const exts = [CM.EditorState.phrases.of(phrases), ...(CM.search ? [CM.search()] : [])];
            const view = new CM.EditorView({{
              state: CM.EditorState.create({{doc: "hello world\\nfind me\\n", extensions: exts}}),
              parent: document.getElementById('cm-host'),
            }});
            CM.openSearchPanel(view);
            const panel = document.querySelector('.cm-search');
            for (const button of panel.querySelectorAll('.cm-button[name="select"], .cm-button[name="replaceAll"]')) {{
              button.dataset.searchLabel = phrases.all;
            }}
            panel.querySelector('label:has(input[name="word"])').dataset.searchLabel = '全字';
          }})();
        </script>
      </body>
    </html>
    """


def load_codemirror_search_panel_fixture(browser, tmp_path):
    page = tmp_path / "cm-search-panel.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        codemirror_search_panel_fixture_html(),
    )


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


def test_codemirror_search_panel_uses_localized_phrases_and_generated_labels(browser, tmp_path):
    load_codemirror_search_panel_fixture(browser, tmp_path)
    labels = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const panel = document.querySelector('.cm-search');
            if (!panel) return false;
            const select = panel.querySelector('.cm-button[name="select"]');
            const replaceAll = panel.querySelector('.cm-button[name="replaceAll"]');
            const word = panel.querySelector('label:has(input[name="word"])');
            return {
              find: panel.querySelector('input[name="search"]')?.placeholder,
              replace: panel.querySelector('input[name="replace"]')?.placeholder,
              select: select?.textContent,
              replaceAll: replaceAll?.textContent,
              matchCase: panel.querySelector('label:has(input[name="case"])')?.textContent,
              regexp: panel.querySelector('label:has(input[name="re"])')?.textContent,
              word: word?.textContent,
              selectGenerated: getComputedStyle(select, '::before').content,
              wordGenerated: getComputedStyle(word, '::after').content,
            };
            """
        )
    )
    assert labels == {
        "find": "查找",
        "replace": "替换",
        "select": "全部",
        "replaceAll": "全部替换",
        "matchCase": "区分大小写",
        "regexp": "正则表达式",
        "word": "全字匹配",
        "selectGenerated": '"全部"',
        "wordGenerated": '"全字"',
    }


def test_preformatted_text_surfaces_share_wrapping_in_dark_and_light_modes(browser, tmp_path):
    page = tmp_path / "shared-preformatted-wrapping.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(
            """
            <pre id="snapshot" class="tmux-snapshot">alpha/beta</pre>
            <div id="transcript" class="transcript-text">alpha/beta</div>
            <div id="agent" class="yoagent-message-body">alpha/beta</div>
            <div id="details-preview" class="yoagent-details-preview">alpha/beta</div>
            <div class="yoagent-message-details"><pre id="details">alpha/beta</pre></div>
            <div class="modal"><pre id="modal-pre">alpha/beta</pre></div>
            <div class="drop-action-result"><pre id="drop-result">alpha/beta</pre></div>
            <div class="file-editor-conflict-compare"><pre id="conflict">alpha/beta</pre></div>
            """
        ),
    )
    for theme in ("theme-dark", "theme-light"):
        metrics = browser.execute_script(
            """
            document.body.className = arguments[0];
            return Object.fromEntries([
              'snapshot', 'transcript', 'agent', 'details-preview',
              'details', 'modal-pre', 'drop-result', 'conflict',
            ].map(id => {
              const style = getComputedStyle(document.getElementById(id));
              return [id, {whiteSpace: style.whiteSpace, overflowWrap: style.overflowWrap}];
            }));
            """,
            theme,
        )
        assert all(value == {"whiteSpace": "pre-wrap", "overflowWrap": "anywhere"} for value in metrics.values()), (theme, metrics)


def test_transient_surfaces_use_viewport_clamped_readable_capacities(browser, tmp_path):
    page = tmp_path / "responsive-transient-capacities.html"
    long_text = "0123456789abcdef" * 16
    page.write_text(
        page_html(
            f"""
            <div class="app-menu-area"><button id="search" class="topbar-search"><span class="topbar-search-label">{long_text}</span></button></div>
            <pre id="drag" class="drag-timing-overlay">{long_text}</pre>
            <div id="repo" class="file-tree-repo-popover" style="inset-inline-start:var(--popover-edge-gap);inset-block-start:90px"><div class="file-tree-repo-popover-title">{long_text}</div><div class="file-tree-repo-popover-path">/{long_text}</div></div>
            <div id="drop" class="terminal-drop-suggestions" style="inset-inline-start:var(--popover-edge-gap);inset-block-start:190px"><div class="terminal-drop-suggestions-head">{long_text}</div></div>
            """
        ),
        encoding="utf-8",
    )
    def geometry(width):
        browser.set_window_size(width, 720)
        browser.get(page.as_uri())
        return browser.execute_script(
            """
            return {
              viewport: innerWidth,
              surfaces: Object.fromEntries(['search', 'drag', 'repo', 'drop'].map(id => {
                const rect = document.getElementById(id).getBoundingClientRect();
                return [id, {left: rect.left, right: rect.right, width: rect.width}];
              })),
            };
            """
        )

    narrow = geometry(360)
    for name, rect in narrow["surfaces"].items():
        assert rect["left"] >= -1, (name, narrow)
        assert rect["right"] <= narrow["viewport"] + 1, (name, narrow)
        assert rect["width"] <= narrow["viewport"] + 1, (name, narrow)

    wide = geometry(1400)
    legacy_caps = {"search": 320, "drag": 460, "repo": 360, "drop": 380}
    for name, old_cap in legacy_caps.items():
        assert wide["surfaces"][name]["width"] > old_cap, (name, wide)


def test_dialog_capacity_tokens_keep_host_and_replay_geometry_in_sync(browser, tmp_path):
    page = tmp_path / "dialog-capacity-ownership.html"
    page.write_text(
        page_html(
            """
            <div id="host-about" class="modal app-modal-overlay open about-open"><div id="host-about-dialog" class="modal-dialog"></div></div>
            <div id="host-share" class="modal app-modal-overlay open share-open"><div id="host-share-dialog" class="modal-dialog"></div></div>
            <div id="replay" class="share-popup-mirror-item" style="inset:0;width:100%;height:100%">
              <div class="modal app-modal-overlay open about-open"><div id="replay-about-dialog" class="modal-dialog"></div></div>
              <div class="modal app-modal-overlay open share-open"><div id="replay-share-dialog" class="modal-dialog"></div></div>
            </div>
            <div class="command-palette app-modal-overlay"><div id="command-dialog" class="command-palette-dialog"></div></div>
            <div class="keyboard-shortcuts-overlay app-modal-overlay"><div id="keyboard-dialog" class="keyboard-shortcuts-dialog"></div></div>
            <div class="file-editor-dialog-backdrop app-modal-overlay"><div id="editor-dialog" class="file-editor-dialog"></div></div>
            """
        ),
        encoding="utf-8",
    )

    def geometry(width, theme):
        browser.set_window_size(width, 720)
        browser.get(page.as_uri())
        return browser.execute_script(
            """
            document.body.className = arguments[0];
            const width = id => document.getElementById(id).getBoundingClientRect().width;
            return {
              viewport: innerWidth,
              hostAbout: width('host-about-dialog'),
              replayAbout: width('replay-about-dialog'),
              hostShare: width('host-share-dialog'),
              replayShare: width('replay-share-dialog'),
              command: width('command-dialog'),
              keyboard: width('keyboard-dialog'),
              editor: width('editor-dialog'),
            };
            """,
            theme,
        )

    for theme in ("theme-dark", "theme-light"):
        narrow = geometry(360, theme)
        wide = geometry(1400, theme)
        for metrics in (narrow, wide):
            assert abs(metrics["hostAbout"] - metrics["replayAbout"]) <= 1, (theme, metrics)
            assert abs(metrics["hostShare"] - metrics["replayShare"]) <= 1, (theme, metrics)
            assert all(0 < metrics[key] <= metrics["viewport"] for key in ("hostAbout", "hostShare", "command", "keyboard", "editor")), (theme, metrics)
        assert abs(wide["command"] - wide["editor"]) <= 1, (theme, wide)
        assert wide["keyboard"] >= wide["command"], (theme, wide)
        assert wide["keyboard"] > narrow["keyboard"], (theme, narrow, wide)
        assert wide["hostAbout"] > narrow["hostAbout"], (theme, narrow, wide)
        assert wide["hostShare"] > narrow["hostShare"], (theme, narrow, wide)


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
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"<!doctype html><html><head><meta charset=utf-8><style>{css}</style></head>"
                    f'<body class="theme-dark">{panels}</body></html>',
    )
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
