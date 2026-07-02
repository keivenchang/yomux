import time

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401

def test_dockview_tabs_keep_yolomux_active_inactive_style(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:1,2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    metrics = dockview_layout_metrics(browser)
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["groups"][0]["tabs"] == ["1", "2"]
    active = next(item for item in metrics["tabStyles"] if item["item"] == "1")
    inactive = next(item for item in metrics["tabStyles"] if item["item"] == "2")
    assert active["active"] is True
    assert inactive["active"] is False
    assert active["bg"] != inactive["bg"]
    assert active["color"] != inactive["color"]
    assert active["rect"]["height"] >= 18
    assert inactive["rect"]["height"] == active["rect"]["height"]
    assert active["rect"]["width"] >= 150
    assert inactive["rect"]["width"] == active["rect"]["width"]
    # Top corners use the font-scaled --pane-tab-top-radius token. Assert a tab-shaped value
    # and matching corners rather than an exact px, so tuning the scale does not churn this test.
    assert active["borderTopLeftRadius"] == active["borderTopRightRadius"]
    assert float(active["borderTopLeftRadius"].rstrip("px")) >= 5
    assert metrics["header"]["tabsScrollbarWidth"] == "none"
    assert metrics["header"]["tabsWebkitScrollbarDisplay"] == "none"
    assert metrics["header"]["tabsWebkitScrollbarHeight"] == "0px"
    assert metrics["header"]["activeTabInsideHeader"] is True

    screenshot = browser_screenshot_rgb(browser)
    dpr = browser.execute_script("return window.devicePixelRatio || 1") or 1

    def rgb_tuple(css_color):
        match = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", css_color)
        if match:
            return tuple(int(part) for part in match.groups())
        srgb_match = re.match(r"color\(srgb\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)", css_color)
        assert srgb_match, css_color
        return tuple(round(float(part) * 255) for part in srgb_match.groups())

    def color_distance(left, right):
        return sum(abs(a - b) for a, b in zip(left, right))

    def sampled_background_matches(rect, expected):
        x_fractions = (0.18, 0.32, 0.5, 0.68, 0.82)
        y_fractions = (0.45, 0.58, 0.72)
        matches = 0
        samples = []
        for x_fraction in x_fractions:
            for y_fraction in y_fractions:
                x = max(0, min(screenshot.width - 1, int((rect["left"] + rect["width"] * x_fraction) * dpr)))
                y = max(0, min(screenshot.height - 1, int((rect["top"] + rect["height"] * y_fraction) * dpr)))
                sample = screenshot.getpixel((x, y))
                samples.append(sample)
                if color_distance(sample, expected) <= 18:
                    matches += 1
        return matches, samples

    active_matches, active_samples = sampled_background_matches(active["rect"], rgb_tuple(active["bg"]))
    inactive_matches, inactive_samples = sampled_background_matches(inactive["rect"], rgb_tuple(inactive["bg"]))
    assert active_matches >= 4, {"bg": active["bg"], "samples": active_samples}
    assert inactive_matches >= 4, {"bg": inactive["bg"], "samples": inactive_samples}


def test_dockview_tab_status_and_numeric_session_spacing_stays_compact(browser, tmp_path):
    stopped_ts = int(time.time()) - 5
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,7777&layout=left&tabs=left:1,7777",
        sessions=["1", "7777"],
        transcript_sessions={
            "1": {"panes": [{"target": "%1", "window": 1, "window_name": "claude", "active": True, "process_label": "claude"}]},
            "7777": {"panes": [{"target": "%77", "window": 77, "window_name": "codex", "active": True, "process_label": "codex"}]},
        },
        auto_approve_payload={
            "session_order": ["1", "7777"],
            "sessions": {
                "1": {
                    "target": "1",
                    "enabled": False,
                    "agent_windows": [{"kind": "claude", "state": "idle", "window_index": 1, "working_stopped_ts": stopped_ts}],
                },
                "7777": {
                    "target": "7777",
                    "enabled": False,
                    "agent_windows": [
                        {"kind": "codex", "state": "working", "window_index": 77},
                        {"kind": "claude", "state": "idle", "window_index": 78, "working_stopped_ts": stopped_ts},
                    ],
                },
            },
            "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
        },
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    metrics = browser.execute_script(
        """
        return ['1', '7777'].map(item => {
          const tab = document.querySelector(`.dockview-pane-tab[data-pane-tab="${item}"]`);
          const core = tab?.querySelector('.pane-tab-core');
          const status = tab?.querySelector('.session-agent-activity-marker');
          const prefix = tab?.querySelector('.session-button-prefix');
          const number = tab?.querySelector('.session-button-number');
          const text = tab?.querySelector('.session-button-text');
          if (!tab || !core || !status || !prefix || !number || !text) return null;
          const coreRect = core.getBoundingClientRect();
          const statusRect = status.getBoundingClientRect();
          const prefixRect = prefix.getBoundingClientRect();
          const textRect = text.getBoundingClientRect();
          return {
            item,
            statusWidth: statusRect.width,
            statusOffset: statusRect.left - coreRect.left,
            prefixWidth: prefixRect.width,
            prefixOffset: prefixRect.left - coreRect.left,
            numberJustifyContent: getComputedStyle(number).justifyContent,
            textOffset: textRect.left - coreRect.left,
          };
        });
        """
    )
    assert all(metrics), metrics
    assert all(item["numberJustifyContent"] == "flex-start" for item in metrics), metrics
    for key in ("statusWidth", "statusOffset", "prefixOffset"):
        assert max(item[key] for item in metrics) - min(item[key] for item in metrics) < 0.1, {key: metrics}
    assert metrics[0]["prefixWidth"] < metrics[1]["prefixWidth"], metrics
    assert metrics[1]["textOffset"] - metrics[0]["textOffset"] < 20, metrics


def test_dockview_tab_hover_shows_session_detail_popover(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=left&tabs=left:1,2",
        sessions=["1", "2"],
        transcript_sessions={
            "1": {
                "current_path": "/home/test/yolomux.dev1",
                "git_root": "/home/test/yolomux.dev1",
                "branch": "yolo-tab-dock-rewrite",
            }
        },
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    browser.execute_script(
        """
        tabPopoverShowDelayMs = 0;
        tabPopoverFollowDelayMs = 0;
        popoverHideDelayMs = 1000;
        """
    )
    ActionChains(browser).move_to_element(browser.find_element("css selector", '.dockview-pane-tab[data-pane-tab="1"]')).perform()
    metrics = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const tab = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]');
            const popover = document.querySelector('.pane-tab-detached-popover.popover-open, .dockview-pane-tab.popover-open > .session-popover');
            if (!tab || !popover) return false;
            const style = getComputedStyle(popover);
            const rect = popover.getBoundingClientRect();
            const tabRect = tab.getBoundingClientRect();
            const visible = style.visibility === 'visible'
              && Number.parseFloat(style.opacity) > 0.9
              && rect.width > 100
              && rect.height > 40;
            if (!visible) return false;
            return {
              text: popover.textContent,
              parentTag: popover.parentElement?.tagName || '',
              top: Math.round(rect.top),
              left: Math.round(rect.left),
              bottom: Math.round(rect.bottom),
              tabBottom: Math.round(tabRect.bottom),
              pointerEvents: style.pointerEvents,
              zIndex: style.zIndex,
            };
            """
        )
    )
    assert "/home/test/yolomux.dev1" in metrics["text"], metrics
    assert "yolo-tab-dock-rewrite" in metrics["text"], metrics
    assert "tmux session 1" in metrics["text"], metrics
    assert metrics["parentTag"] == "BODY", metrics
    assert metrics["top"] >= metrics["tabBottom"], metrics
    assert metrics["pointerEvents"] == "auto", metrics


def test_dockview_tab_hover_popover_survives_tab_refresh_without_pointer_move(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=left&tabs=left:1,2",
        sessions=["1", "2"],
        transcript_sessions={
            "1": {
                "current_path": "/home/test/yolomux.dev1",
                "git_root": "/home/test/yolomux.dev1",
                "branch": "yolo-tab-dock-rewrite",
            }
        },
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    browser.execute_script(
        """
        tabPopoverShowDelayMs = 0;
        tabPopoverFollowDelayMs = 0;
        popoverHideDelayMs = 120;
        """
    )
    ActionChains(browser).move_to_element(browser.find_element("css selector", '.dockview-pane-tab[data-pane-tab="1"]')).perform()
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const popover = document.querySelector('.pane-tab-detached-popover.popover-open');
            if (!popover) return false;
            const style = getComputedStyle(popover);
            const rect = popover.getBoundingClientRect();
            return style.visibility === 'visible' && Number.parseFloat(style.opacity) > 0.9 && rect.width > 100 && rect.height > 40;
            """
        )
    )
    browser.execute_script(
        """
        const popover = document.querySelector('.pane-tab-detached-popover.popover-open');
        window.__popoverBeforeDockviewRefresh = popover;
        dockviewRefreshTabs();
        """
    )
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        setTimeout(done, 260);
        """
    )
    metrics = browser.execute_script(
        """
        const tab = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]');
        const popover = document.querySelector('.pane-tab-detached-popover.popover-open');
        const style = popover ? getComputedStyle(popover) : null;
        const rect = popover?.getBoundingClientRect?.();
        return {
          visible: Boolean(popover && style.visibility === 'visible' && Number.parseFloat(style.opacity) > 0.9 && rect.width > 100 && rect.height > 40),
          samePopover: popover === window.__popoverBeforeDockviewRefresh,
          parentTag: popover?.parentElement?.tagName || '',
          detachedRef: tab?.__yolomuxDetachedPopover === popover,
          hoverState: tab?.dataset?.popoverHoverState || '',
          tabOpen: tab?.classList?.contains('popover-open') || false,
        };
        """
    )
    assert metrics["visible"] is True, metrics
    assert metrics["samePopover"] is True, metrics
    assert metrics["parentTag"] == "BODY", metrics
    assert metrics["detachedRef"] is True, metrics
    assert metrics["hoverState"] == "open", metrics
    assert metrics["tabOpen"] is True, metrics


def test_dockview_separator_inactive_tab_and_preview_colors_match_tokens(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    metrics = browser.execute_script(
        """
        const group = document.querySelector('.dv-groupview');
        const panel = group.querySelector('.dockview-panel-content > .panel');
        const inactiveTab = document.querySelector('.dv-tab.dv-inactive-tab > .dockview-pane-tab:not(.active)');
        const strip = inactiveTab?.closest('.dv-tabs-and-actions-container');
        const sash = document.querySelector('.dv-sash');
        group.classList.add('drag-over', 'drop-preview', 'drop-preview-left');
        group.dataset.dropLabel = 'left';
        const groupStyle = getComputedStyle(group);
        const panelStyle = panel ? getComputedStyle(panel) : null;
        const stripStyle = strip ? getComputedStyle(strip) : null;
        const tabStyle = inactiveTab ? getComputedStyle(inactiveTab) : null;
        const separatorHover = getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim();
        const separatorLineSize = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-line-size')) || 0;
        const previewStyle = getComputedStyle(group, '::before');
        const sashStyle = sash ? getComputedStyle(sash, '::before') : null;
        const result = {
          groupBorder: [groupStyle.borderTopWidth, groupStyle.borderRightWidth, groupStyle.borderBottomWidth, groupStyle.borderLeftWidth],
          panelBorder: panelStyle ? [panelStyle.borderTopWidth, panelStyle.borderRightWidth, panelStyle.borderBottomWidth, panelStyle.borderLeftWidth] : [],
          inactiveBg: tabStyle?.backgroundColor || '',
          stripBg: stripStyle?.backgroundColor || '',
          previewBorderColor: previewStyle.borderLeftColor,
          separatorHover,
          sashBg: sashStyle?.backgroundColor || '',
          sashBeforeWidth: sashStyle ? parseFloat(sashStyle.width) || 0 : 0,
          separatorLineSize,
        };
        group.classList.remove('drag-over', 'drop-preview', 'drop-preview-left');
        delete group.dataset.dropLabel;
        return result;
        """
    )
    assert metrics["groupBorder"] == ["0px", "0px", "0px", "0px"]
    assert metrics["panelBorder"] == ["0px", "0px", "0px", "0px"]
    assert metrics["inactiveBg"] == metrics["stripBg"]
    assert metrics["previewBorderColor"] == metrics["separatorHover"]
    assert metrics["sashBg"]
    assert metrics["sashBeforeWidth"] <= metrics["separatorLineSize"] + 0.1
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".dv-sash")).perform()
    hover_metrics = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const sashStyle = getComputedStyle(document.querySelector('.dv-sash'), '::before');
            const docStyle = getComputedStyle(document.documentElement);
            const result = {
              sashBg: sashStyle.backgroundColor,
              hoverBg: docStyle.getPropertyValue('--pane-resizer-hover-bg').trim(),
              sashBeforeWidth: parseFloat(sashStyle.width) || 0,
              hoverLineSize: parseFloat(docStyle.getPropertyValue('--pane-resizer-hover-line-size')) || 0,
            };
            return result.sashBg === result.hoverBg ? result : false;
            """
        )
    )
    assert hover_metrics["sashBeforeWidth"] <= hover_metrics["hoverLineSize"] + 0.1
    assert hover_metrics["sashBeforeWidth"] >= metrics["sashBeforeWidth"]


def test_separator_color_preference_recolors_drop_previews(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    metrics = browser.execute_script(
        """
        applySettingsPayload({settings: {appearance: {separator_color: 'purple'}}, defaults: {}, mtime_ns: 9301}, {force: true});
        const expectedProbe = document.createElement('div');
        expectedProbe.style.borderLeft = '2px dashed var(--pane-resizer-hover-bg)';
        expectedProbe.style.position = 'absolute';
        expectedProbe.style.left = '-1000px';
        document.body.append(expectedProbe);
        const expected = getComputedStyle(expectedProbe).borderLeftColor;
        expectedProbe.remove();

        const tabStrip = document.createElement('div');
        tabStrip.className = 'pane-tabs tab-drop-preview';
        tabStrip.style.cssText = 'position:absolute;left:20px;top:20px;width:220px;height:28px;--tab-drop-x:40px;--tab-drop-y:0px;--tab-drop-height:24px;';
        document.body.append(tabStrip);
        const tabInsertion = getComputedStyle(tabStrip, '::after').borderLeftColor;
        tabStrip.remove();

        const group = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const groupRect = group.getBoundingClientRect();
        group.classList.add('drag-over', 'drop-preview', 'drop-preview-left');
        const panePreview = getComputedStyle(group, '::before').borderLeftColor;
        group.classList.remove('drag-over', 'drop-preview', 'drop-preview-left');

        const gridNode = document.querySelector('#grid');
        gridNode.classList.add('drop-preview', 'drop-preview-root', 'drop-preview-right');
        const rootPreview = getComputedStyle(gridNode, '::before').borderLeftColor;
        gridNode.classList.remove('drop-preview', 'drop-preview-root', 'drop-preview-right');

        window.__filePreviewOpen = null;
        window.openDraggedFilesInEditor = (payload, options) => {
          window.__filePreviewOpen = {payload, options};
        };
        const target = group.querySelector('.dockview-panel-content') || group;
        const store = {
          'application/x-yolomux-file': JSON.stringify({path: '/home/test/yolomux.dev/README.md', paths: ['/home/test/yolomux.dev/README.md'], kind: 'file'}),
          'text/plain': '/home/test/yolomux.dev/README.md',
        };
        const dataTransfer = {
          types: Object.keys(store),
          dropEffect: '',
          effectAllowed: 'copy',
          getData(type) { return store[type] || ''; },
          setData(type, value) { store[type] = String(value); },
        };
        const event = new Event('dragover', {bubbles: true, cancelable: true});
        Object.defineProperty(event, 'clientX', {value: Math.round(groupRect.left + 8)});
        Object.defineProperty(event, 'clientY', {value: Math.round(groupRect.top + groupRect.height / 2)});
        Object.defineProperty(event, 'dataTransfer', {value: dataTransfer});
        target.dispatchEvent(event);
        const fileDragPreview = getComputedStyle(group, '::before').borderLeftColor;
        const fileDropEffect = dataTransfer.dropEffect;
        clearDropPreview();

        return {
          expected,
          token: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
          tabInsertion,
          panePreview,
          rootPreview,
          fileDragPreview,
          fileDropEffect,
        };
        """
    )
    assert metrics["token"].startswith("rgb("), metrics
    assert metrics["tabInsertion"] == metrics["expected"], metrics
    assert metrics["panePreview"] == metrics["expected"], metrics
    assert metrics["rootPreview"] == metrics["expected"], metrics
    assert metrics["fileDragPreview"] == metrics["expected"], metrics
    assert metrics["fileDropEffect"] == "copy", metrics


def test_dockview_active_ring_follows_pane_spacing_without_thickening_sash(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    metrics = browser.execute_script(
        """
        applySettingsPayload({settings: {appearance: {pane_spacing: 6, pane_ring_opacity: 75}}, defaults: {}, mtime_ns: 9001}, {force: true});
        const activePanel = document.querySelector('#panel-1');
        const inactivePanel = document.querySelector('#panel-2');
        activePanel.classList.add('active-pane', 'focused-pane');
        inactivePanel.classList.remove('active-pane', 'focused-pane', 'typing-ready-pane');
        const activeGroup = activePanel.closest('.dv-groupview');
        const inactiveGroup = inactivePanel.closest('.dv-groupview');
        const activeRing = getComputedStyle(activeGroup, '::after');
        const inactiveRing = getComputedStyle(inactiveGroup, '::after');
        const activeGroupStyle = getComputedStyle(activeGroup);
        const activePanelStyle = getComputedStyle(activePanel);
        const activeGroupRect = activeGroup.getBoundingClientRect();
        const activePanelRect = activePanel.getBoundingClientRect();
        const activeTerminalRect = activePanel.querySelector('.terminal')?.getBoundingClientRect();
        const activeXtermRect = activePanel.querySelector('.terminal .xterm')?.getBoundingClientRect();
        const sash = document.querySelector('.dv-sash');
        const sashStyle = getComputedStyle(sash);
        const sashBefore = getComputedStyle(sash, '::before');
        const docStyle = getComputedStyle(document.documentElement);
        const paneGapPx = parseFloat(docStyle.getPropertyValue('--pane-split-gap')) || 0;
        return {
          paneGap: docStyle.getPropertyValue('--pane-split-gap').trim(),
          paneGapPx,
          activeRingBorderWidth: activeRing.borderTopWidth,
          activeRingBorderColor: activeRing.borderTopColor,
          activeRingPointerEvents: activeRing.pointerEvents,
          inactiveRingBorderWidth: inactiveRing.borderTopWidth,
          inactiveRingBorderColor: inactiveRing.borderTopColor,
          groupBorder: [activeGroupStyle.borderTopWidth, activeGroupStyle.borderRightWidth, activeGroupStyle.borderBottomWidth, activeGroupStyle.borderLeftWidth],
          panelBorder: [activePanelStyle.borderTopWidth, activePanelStyle.borderRightWidth, activePanelStyle.borderBottomWidth, activePanelStyle.borderLeftWidth],
          sashBackground: sashStyle.backgroundColor,
          sashBeforeWidth: parseFloat(sashBefore.width) || 0,
          separatorLineSize: parseFloat(docStyle.getPropertyValue('--pane-resizer-line-size')) || 0,
          groupPadding: [activeGroupStyle.paddingTop, activeGroupStyle.paddingRight, activeGroupStyle.paddingBottom, activeGroupStyle.paddingLeft],
          panelInset: {
            left: activePanelRect.left - activeGroupRect.left,
            right: activeGroupRect.right - activePanelRect.right,
            bottom: activeGroupRect.bottom - activePanelRect.bottom,
          },
          terminalInset: activeTerminalRect ? {
            left: activeTerminalRect.left - activeGroupRect.left,
            right: activeGroupRect.right - activeTerminalRect.right,
            bottom: activeGroupRect.bottom - activeTerminalRect.bottom,
          } : null,
          xtermInset: activeXtermRect ? {
            left: activeXtermRect.left - activeGroupRect.left,
            right: activeGroupRect.right - activeXtermRect.right,
            bottom: activeGroupRect.bottom - activeXtermRect.bottom,
          } : null,
        };
        """
    )
    assert metrics["paneGap"] == "6px", metrics
    assert metrics["activeRingBorderWidth"] == "6px", metrics
    assert metrics["activeRingBorderColor"] not in ("rgba(0, 0, 0, 0)", "transparent"), metrics
    assert metrics["activeRingPointerEvents"] == "none", metrics
    assert metrics["inactiveRingBorderWidth"] == "6px", metrics
    assert metrics["inactiveRingBorderColor"] in ("rgba(0, 0, 0, 0)", "transparent", "color(srgb 0 0 0 / 0)"), metrics
    assert metrics["groupBorder"] == ["0px", "0px", "0px", "0px"], metrics
    assert metrics["panelBorder"] == ["0px", "0px", "0px", "0px"], metrics
    assert metrics["sashBackground"] in ("rgba(0, 0, 0, 0)", "transparent"), metrics
    assert metrics["sashBeforeWidth"] <= metrics["separatorLineSize"] + 0.1, metrics
    assert metrics["groupPadding"] == ["6px", "6px", "6px", "6px"], metrics
    assert metrics["panelInset"]["left"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["panelInset"]["right"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["panelInset"]["bottom"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["terminalInset"]["left"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["terminalInset"]["right"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["terminalInset"]["bottom"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["xtermInset"]["left"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["xtermInset"]["right"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["xtermInset"]["bottom"] >= metrics["paneGapPx"] - 0.5, metrics


def test_dockview_pane_spacing_multiple_values_keep_terminal_inside_ring(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    metrics = browser.execute_script(
        """
        const activePanel = document.querySelector('#panel-1');
        activePanel.classList.add('active-pane', 'focused-pane');
        const activeGroup = activePanel.closest('.dv-groupview');
        const docStyle = getComputedStyle(document.documentElement);
        const snapshots = [];
        for (const value of [0, 3, 12, 20]) {
          applySettingsPayload({settings: {appearance: {pane_spacing: value, pane_ring_opacity: 75}}, defaults: {}, mtime_ns: 9100 + value}, {force: true});
          const groupStyle = getComputedStyle(activeGroup);
          const ring = getComputedStyle(activeGroup, '::after');
          const groupRect = activeGroup.getBoundingClientRect();
          const panelRect = activePanel.getBoundingClientRect();
          const terminalRect = activePanel.querySelector('.terminal')?.getBoundingClientRect();
          const xtermRect = activePanel.querySelector('.terminal .xterm')?.getBoundingClientRect();
          const paneGapPx = parseFloat(docStyle.getPropertyValue('--pane-split-gap')) || 0;
          snapshots.push({
            value,
            paneGap: docStyle.getPropertyValue('--pane-split-gap').trim(),
            paneGapPx,
            ringWidth: ring.borderTopWidth,
            groupBorder: [groupStyle.borderTopWidth, groupStyle.borderRightWidth, groupStyle.borderBottomWidth, groupStyle.borderLeftWidth],
            panelInset: {
              left: panelRect.left - groupRect.left,
              right: groupRect.right - panelRect.right,
              bottom: groupRect.bottom - panelRect.bottom,
            },
            terminalInset: terminalRect ? {
              left: terminalRect.left - groupRect.left,
              right: groupRect.right - terminalRect.right,
              bottom: groupRect.bottom - terminalRect.bottom,
            } : null,
            xtermInset: xtermRect ? {
              left: xtermRect.left - groupRect.left,
              right: groupRect.right - xtermRect.right,
              bottom: groupRect.bottom - xtermRect.bottom,
            } : null,
          });
        }
        return snapshots;
        """
    )
    assert [item["value"] for item in metrics] == [0, 3, 12, 20], metrics
    for item in metrics:
        assert item["paneGap"] == f"{item['value']}px", item
        assert item["ringWidth"] == f"{item['value']}px", item
        assert item["groupBorder"] == ["0px", "0px", "0px", "0px"], item
        for rect_key in ["panelInset", "terminalInset", "xtermInset"]:
            assert item[rect_key]["left"] >= item["paneGapPx"] - 0.5, item
            assert item[rect_key]["right"] >= item["paneGapPx"] - 0.5, item
            assert item[rect_key]["bottom"] >= item["paneGapPx"] - 0.5, item


def test_dockview_complex_layout_sash_hit_targets_stay_transparent(browser, tmp_path):
    encoded_file = "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2FDONE.md"
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        (
            f"?sessions=files,5,{encoded_file},2"
            f"&layout=row@20(slot1,row@50(left,col@50(slot2,slot3)))"
            f"&tabs=slot1:files;left:5;slot2:{encoded_file};slot3:2"
        ),
        sessions=["5", "2"],
    )
    wait_for_dockview(browser, min_tabs=4)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelectorAll('.dv-groupview').length === 4
              && document.querySelectorAll('.dv-sash').length >= 3
              && Array.from(document.querySelectorAll('.dv-groupview'))
                .every(group => group.getBoundingClientRect().width > 0 && group.getBoundingClientRect().height > 0);
            """
        )
    )
    metrics = browser.execute_script(
        """
        const transparent = new Set(['rgba(0, 0, 0, 0)', 'transparent']);
        const docStyle = getComputedStyle(document.documentElement);
        const lineSize = parseFloat(docStyle.getPropertyValue('--pane-resizer-line-size')) || 0;
        const separatorBg = docStyle.getPropertyValue('--pane-resizer-bg').trim();
        const rectFor = node => {
          const rect = node.getBoundingClientRect();
          return {width: rect.width, height: rect.height, left: rect.left, top: rect.top};
        };
        const sashes = Array.from(document.querySelectorAll('.dv-sash')).map(sash => {
          const style = getComputedStyle(sash);
          const before = getComputedStyle(sash, '::before');
          const split = sash.closest('.dv-split-view-container')?.className || '';
          return {
            split,
            rect: rectFor(sash),
            bg: style.backgroundColor,
            beforeBg: before.backgroundColor,
            beforeWidth: parseFloat(before.width) || 0,
            beforeHeight: parseFloat(before.height) || 0,
            horizontal: split.includes('dv-horizontal'),
            vertical: split.includes('dv-vertical'),
            transparent: transparent.has(style.backgroundColor),
          };
        });
        const groups = Array.from(document.querySelectorAll('.dv-groupview')).map(group => {
          const groupStyle = getComputedStyle(group);
          const panel = group.querySelector('.dockview-panel-content > .panel');
          const panelStyle = panel ? getComputedStyle(panel) : null;
          return {
            tabs: Array.from(group.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            groupBorder: [groupStyle.borderTopWidth, groupStyle.borderRightWidth, groupStyle.borderBottomWidth, groupStyle.borderLeftWidth],
            panelBorder: panelStyle ? [panelStyle.borderTopWidth, panelStyle.borderRightWidth, panelStyle.borderBottomWidth, panelStyle.borderLeftWidth] : [],
          };
        });
        return {sashes, groups, lineSize, separatorBg};
        """
    )
    assert len(metrics["groups"]) == 4
    assert len(metrics["sashes"]) >= 3
    assert any("__files__" in group["tabs"] for group in metrics["groups"])
    assert any("5" in group["tabs"] for group in metrics["groups"])
    assert any("2" in group["tabs"] for group in metrics["groups"])
    assert any(any(tab.startswith("file:") for tab in group["tabs"]) for group in metrics["groups"])
    for group in metrics["groups"]:
        assert group["groupBorder"] == ["0px", "0px", "0px", "0px"]
        assert group["panelBorder"] in (["0px", "0px", "0px", "0px"], [])
    for sash in metrics["sashes"]:
        assert sash["transparent"] is True, sash
        assert sash["beforeBg"] == metrics["separatorBg"]
        if sash["horizontal"]:
            assert sash["beforeWidth"] <= metrics["lineSize"] + 0.1
        if sash["vertical"]:
            assert sash["beforeHeight"] <= metrics["lineSize"] + 0.1

    first_sash = browser.find_element("css selector", ".dv-sash")
    ActionChains(browser).move_to_element(first_sash).perform()
    hover_metrics = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const sash = document.querySelector('.dv-sash:hover');
            if (!sash) return false;
            const style = getComputedStyle(sash);
            const before = getComputedStyle(sash, '::before');
            const docStyle = getComputedStyle(document.documentElement);
            const hoverBg = docStyle.getPropertyValue('--pane-resizer-hover-bg').trim();
            const hoverLineSize = parseFloat(docStyle.getPropertyValue('--pane-resizer-hover-line-size')) || 0;
            const split = sash.closest('.dv-split-view-container')?.className || '';
            return {
              bg: style.backgroundColor,
              beforeBg: before.backgroundColor,
              beforeWidth: parseFloat(before.width) || 0,
              beforeHeight: parseFloat(before.height) || 0,
              hoverBg,
              hoverLineSize,
              horizontal: split.includes('dv-horizontal'),
              vertical: split.includes('dv-vertical'),
            };
            """
        )
    )
    assert hover_metrics["bg"] in ("rgba(0, 0, 0, 0)", "transparent")
    assert hover_metrics["beforeBg"] == hover_metrics["hoverBg"]
    if hover_metrics["horizontal"]:
        assert hover_metrics["beforeWidth"] <= hover_metrics["hoverLineSize"] + 0.1
    if hover_metrics["vertical"]:
        assert hover_metrics["beforeHeight"] <= hover_metrics["hoverLineSize"] + 0.1


def test_dockview_hidden_inner_header_keeps_terminal_content_full_height(browser, tmp_path):
    encoded_file = "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2FDONE.md"
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        (
            f"?sessions=files,5,{encoded_file},2"
            f"&layout=row@20(slot1,row@50(left,col@50(slot2,slot3)))"
            f"&tabs=slot1:files;left:5;slot2:{encoded_file};slot3:2"
        ),
        sessions=["5", "2"],
        terminal_css=".terminal { width: 100%; height: 100%; }",
    )
    wait_for_dockview(browser, min_tabs=4)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const item = terminals.get('5');
            return document.querySelector('#term-5 .xterm') && item?.term?.rows > 20;
            """
        )
    )
    metrics = browser.execute_script(
        """
        const rectFor = node => {
          const rect = node.getBoundingClientRect();
          return {top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height};
        };
        const panel = document.querySelector('#panel-5');
        const head = panel.querySelector('.panel-head');
        const detail = panel.querySelector('.panel-detail-row');
        const pane = panel.querySelector('#terminal-pane-5');
        const terminal = panel.querySelector('#term-5');
        const xterm = panel.querySelector('#term-5 .xterm');
        const panelRect = rectFor(panel);
        const detailRect = rectFor(detail);
        const paneRect = rectFor(pane);
        const terminalRect = rectFor(terminal);
        const xtermRect = rectFor(xterm);
        return {
          panelCollapsed: panel.classList.contains('dockview-inner-head-collapsed'),
          innerHeadHidden: head.hidden === true,
          innerHeadDisplay: getComputedStyle(head).display,
          panelRows: getComputedStyle(panel).gridTemplateRows.trim().split(/\\s+/),
          panelHeight: panelRect.height,
          detailHeight: detailRect.height,
          paneHeight: paneRect.height,
          terminalHeight: terminalRect.height,
          xtermHeight: xtermRect.height,
          paneBottomDelta: Math.abs(panelRect.bottom - paneRect.bottom),
          terminalBottomDelta: Math.abs(paneRect.bottom - terminalRect.bottom),
          xtermBottomDelta: Math.abs(terminalRect.bottom - xtermRect.bottom),
          termRows: terminals.get('5')?.term?.rows || 0,
          termCols: terminals.get('5')?.term?.cols || 0,
        };
        """
    )
    assert metrics["panelCollapsed"] is True
    assert metrics["innerHeadHidden"] is True
    assert metrics["innerHeadDisplay"] == "none"
    assert len(metrics["panelRows"]) == 2
    assert metrics["paneHeight"] >= metrics["panelHeight"] - metrics["detailHeight"] - 2
    assert metrics["terminalHeight"] >= metrics["paneHeight"] - 1
    assert metrics["xtermHeight"] >= metrics["terminalHeight"] - 1
    assert metrics["paneBottomDelta"] <= 1
    assert metrics["terminalBottomDelta"] <= 1
    assert metrics["xtermBottomDelta"] <= 1
    assert metrics["termRows"] > 20
    assert metrics["termCols"] >= 40


def test_terminal_fit_ignores_duplicate_resize_observer_echoes(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        terminal_css=".terminal { width: 720px; height: 260px; }",
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof fitTerminal === 'function'
              && document.querySelector('#term-1 .xterm')
              && terminals.get('1')?.term;
            """
        )
    )
    metrics = browser.execute_script(
        """
        const container = document.getElementById('term-1');
        const pane = document.getElementById('terminal-pane-1');
        const item = terminals.get('1');
        pane.classList.add('active');
        container.style.width = '720px';
        container.style.height = '260px';
        container.style.padding = '0';
        item.term.cols = 80;
        item.term.rows = 24;
        item.lastFitSignature = '';
        item.term._core = {_renderService: {_renderer: {dimensions: {css: {cell: {width: 9, height: 18}}}}}};
        window.__terminalResizeCalls = [];
        fitTerminal('1');
        const afterFirst = {cols: item.term.cols, rows: item.term.rows, calls: [...window.__terminalResizeCalls]};
        fitTerminal('1');
        const afterEcho = {cols: item.term.cols, rows: item.term.rows, calls: [...window.__terminalResizeCalls]};
        container.style.width = '360px';
        fitTerminal('1');
        return {
          afterFirst,
          afterEcho,
          afterWidthChange: {cols: item.term.cols, rows: item.term.rows, calls: [...window.__terminalResizeCalls]},
        };
        """
    )
    assert metrics["afterFirst"]["calls"] == [{"cols": 79, "rows": 14}]
    assert metrics["afterEcho"]["calls"] == [{"cols": 79, "rows": 14}]
    assert metrics["afterWidthChange"]["cols"] == 40
    assert metrics["afterWidthChange"]["rows"] == 14
    assert metrics["afterWidthChange"]["calls"] == [{"cols": 79, "rows": 14}, {"cols": 40, "rows": 14}]


def test_dockview_header_actions_stay_on_first_row(browser, tmp_path):
    sessions = [str(index) for index in range(1, 8)]
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3,4,5,6,7&layout=left&tabs=left:1,2,3,4,5,6,7",
        sessions=sessions,
    )
    wait_for_dockview(browser, min_tabs=7)
    wait_for_dockview_tab_geometry(browser, min_tabs=7, min_width=150, min_rows=2)
    metrics = browser.execute_script(
        """
        const header = document.querySelector('.dv-tabs-and-actions-container');
        const tabsContainer = header.querySelector('.dv-tabs-container');
        const tab = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]');
        const rectFor = rect => ({left: Math.round(rect.left), right: Math.round(rect.right), top: Math.round(rect.top), bottom: Math.round(rect.bottom), width: Math.round(rect.width), height: Math.round(rect.height)});
        const tabRects = Array.from(document.querySelectorAll('.dockview-pane-tab')).map(item => rectFor(item.getBoundingClientRect()));
        const actions = document.querySelector('.dockview-pane-header-actions .tabs');
        const actionButtons = Array.from(document.querySelectorAll('.dockview-pane-header-actions .tab'));
        const panel = document.querySelector('#panel-1');
        const innerHead = document.querySelector('#panel-1 .panel-head');
        const headerRect = header.getBoundingClientRect();
        const tabRect = tab.getBoundingClientRect();
        const actionsRect = actions.getBoundingClientRect();
        const actionButtonRects = actionButtons.map(button => button.getBoundingClientRect());
        const actionBox = rectFor(actionsRect);
        const firstRowTabs = tabRects.filter(rect => Math.abs(rect.top - Math.round(tabRect.top)) <= 3);
        const overlappingTabs = tabRects.filter(rect => (
          rect.right > actionBox.left - 1
          && rect.left < actionBox.right + 1
          && rect.bottom > actionBox.top + 1
          && rect.top < actionBox.bottom - 1
        ));
        const panelRect = panel.getBoundingClientRect();
        const innerHeadRect = innerHead.getBoundingClientRect();
        const innerHeadStyle = getComputedStyle(innerHead);
        return {
          headerHeight: Math.round(headerRect.height),
          tabHeight: Math.round(tabRect.height),
          maxActionButtonHeight: Math.round(Math.max(...actionButtonRects.map(rect => rect.height))),
          firstRowTabsRight: Math.round(Math.max(...firstRowTabs.map(rect => rect.right))),
          overlappingTabs: overlappingTabs.map(rect => rectFor(rect)),
          tabRows: new Set(tabRects.map(rect => rect.top)).size,
          tabsOverflowX: getComputedStyle(tabsContainer).overflowX,
          tabsScrollWidth: Math.round(tabsContainer.scrollWidth),
          tabsClientWidth: Math.round(tabsContainer.clientWidth),
          reservedInlineSize: getComputedStyle(header).getPropertyValue('--dockview-header-actions-reserved-inline-size').trim(),
          tabCount: tabRects.length,
          headerRight: Math.round(headerRect.right),
          actionsLeft: Math.round(actionsRect.left),
          actionsRight: Math.round(actionsRect.right),
          actionsTopDelta: Math.abs(Math.round(actionsRect.top - tabRect.top)),
          actionsBottom: Math.round(actionsRect.bottom),
          tabBottom: Math.round(tabRect.bottom),
          innerHeadHidden: innerHead?.hidden === true,
          innerHeadDisplay: innerHeadStyle.display,
          innerHeadHeight: Math.round(innerHeadRect.height),
          panelTopDelta: Math.abs(Math.round(panelRect.top - headerRect.bottom)),
        };
        """
    )
    assert metrics["innerHeadHidden"] is True
    assert metrics["innerHeadDisplay"] == "none"
    assert metrics["innerHeadHeight"] == 0
    assert metrics["panelTopDelta"] <= 1
    assert metrics["tabCount"] == 7
    assert metrics["reservedInlineSize"].endswith("px")
    assert float(metrics["reservedInlineSize"][:-2]) >= 80
    assert metrics["actionsLeft"] >= metrics["firstRowTabsRight"] + 1
    assert metrics["overlappingTabs"] == []
    assert metrics["actionsRight"] <= metrics["headerRight"] + 1
    assert metrics["actionsTopDelta"] <= 3
    assert metrics["actionsBottom"] <= metrics["tabBottom"] + 3
    assert metrics["tabRows"] >= 2
    assert metrics["tabsOverflowX"] == "visible"
    assert metrics["tabsScrollWidth"] <= metrics["tabsClientWidth"] + 1
    assert metrics["headerHeight"] >= (metrics["tabHeight"] * 2) - 2
    assert metrics["maxActionButtonHeight"] <= 20
    assert metrics["maxActionButtonHeight"] <= metrics["tabHeight"] + 1


def test_dockview_wrapped_tab_rows_share_one_control_reserved_flex_grid(browser, tmp_path):
    sessions = [str(index) for index in range(1, 8)]
    grid_widths = (1800, 1300, 1000, 900, 700)
    transcript_sessions = {
        session: {
            "panes": [
                {
                    "target": f"%{session}",
                    "window": 0,
                    "window_name": "codex",
                    "window_active": True,
                    "active": True,
                    "process_label": "codex",
                }
            ]
        }
        for session in sessions
    }

    for grid_width in grid_widths:
        browser.set_window_size(grid_width + 100, 700)
        load_live_runtime_boot_fixture(
            browser,
            tmp_path,
            f"?sessions={','.join(sessions)}&layout=left&tabs=left:{','.join(sessions)}",
            sessions=sessions,
            grid_width=grid_width,
            transcript_sessions=transcript_sessions,
        )
        wait_for_dockview(browser, min_tabs=len(sessions))
        metrics = WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                const sessionTab = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]');
                const group = sessionTab?.closest('.dv-groupview');
                const header = group?.querySelector('.dv-tabs-and-actions-container');
                const tabsContainer = header?.querySelector('.dv-tabs-container');
                const actions = group?.querySelector('.dockview-pane-header-actions:not([hidden])');
                const tabs = Array.from(tabsContainer?.querySelectorAll('.dv-tab') || []);
                const panel = group?.querySelector('.panel');
                const infoBar = panel?.querySelector('.pane-info-bar, .panel-detail-row');
                const windowButton = infoBar?.querySelector('.tmux-window-button');
                if (!header || !tabsContainer || !actions || !infoBar || !windowButton || tabs.length !== arguments[0]) return false;
                const rect = node => {
                  const value = node.getBoundingClientRect();
                  return {left: value.left, right: value.right, top: value.top, bottom: value.bottom, width: value.width};
                };
                const tabRects = tabs.map(rect);
                const rows = [...new Set(tabRects.map(value => Math.round(value.top)))].sort((left, right) => left - right)
                  .map(top => tabRects.filter(value => Math.round(value.top) === top));
                const reservation = tabsContainer.querySelector(':scope > .dockview-tab-first-row-reservation');
                const lastTabBottom = Math.max(...tabRects.map(value => value.bottom));
                const infoBarRect = infoBar.getBoundingClientRect();
                const windowButtonRect = windowButton.getBoundingClientRect();
                return {
                  rowCount: rows.length,
                  tabs: tabRects,
                  rows: rows.map(row => row.map(value => ({left: value.left, right: value.right, width: value.width}))),
                  tabsLeft: tabsContainer.getBoundingClientRect().left,
                      contentRight: tabsContainer.getBoundingClientRect().right,
                      reservation: reservation ? rect(reservation) : null,
                  actionLeft: actions.getBoundingClientRect().left,
                  headerBottom: header.getBoundingClientRect().bottom,
                  tabsBottom: tabsContainer.getBoundingClientRect().bottom,
                  lastTabBottom,
                  infoBarTop: infoBarRect.top,
                  windowButtonTop: windowButtonRect.top,
                };
                """,
                len(sessions),
            )
        )
        assert metrics["rowCount"] >= (1 if grid_width == 1800 else 2), {"grid_width": grid_width, **metrics}
        assert metrics["reservation"] is not None, metrics
        assert metrics["reservation"]["top"] == metrics["tabs"][0]["top"], metrics
        assert metrics["rows"][0][-1]["right"] <= metrics["reservation"]["left"] + 1, metrics
        assert abs(metrics["reservation"]["right"] - metrics["contentRight"]) < 1.1, metrics
        assert metrics["rows"][0][-1]["right"] <= metrics["actionLeft"] + 1, metrics
        assert abs(metrics["headerBottom"] - metrics["lastTabBottom"]) < 0.1, metrics
        assert abs(metrics["tabsBottom"] - metrics["lastTabBottom"]) < 0.1, metrics
        assert abs(metrics["infoBarTop"] - metrics["lastTabBottom"]) < 0.1, metrics
        assert abs(metrics["windowButtonTop"] - metrics["lastTabBottom"] - 1) < 0.1, metrics
        for row in metrics["rows"]:
            assert abs(row[0]["left"] - metrics["tabsLeft"]) < 0.1, metrics
            assert row[-1]["right"] <= metrics["contentRight"] + 0.1, metrics
            for previous, current in zip(row, row[1:]):
                assert abs(current["left"] - previous["right"] - 1) < 0.1, metrics


def test_dockview_window_bar_buttons_select_tmux_windows(browser, tmp_path):
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "bash", "window_active": False, "active": True, "process_label": "bash"},
                {"target": "%2", "window": 1, "window_name": "codex", "window_active": True, "active": True, "process_label": "codex"},
                {"target": "%3", "window": 2, "window_name": "codex", "window_active": False, "active": True, "process_label": "pytest"},
            ],
        },
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelectorAll('.panel-detail-row [data-window-index]').length === 3;
            """
        )
    )
    buttons = browser.execute_script(
        """
        return Array.from(document.querySelectorAll('.panel-detail-row [data-window-index]')).map(button => ({
          text: button.querySelector('.tmux-window-name-label')?.textContent.trim() || button.textContent.trim(),
          index: button.dataset.windowIndex || '',
          pressed: button.getAttribute('aria-pressed') || '',
          active: button.classList.contains('active'),
        }));
        """
    )
    browser.execute_script("clearFocusedTerminal('1'); document.querySelector('.panel')?.classList.remove('focused-pane', 'active-pane')")
    browser.find_element("css selector", '.panel-detail-row [data-window-index="2"]').click()
    after_click_buttons = browser.execute_script(
        """
        return Array.from(document.querySelectorAll('.panel-detail-row [data-window-index]')).map(button => ({
          index: button.dataset.windowIndex || '',
          pressed: button.getAttribute('aria-pressed') || '',
          active: button.classList.contains('active'),
        }));
        """
    )
    fetches = browser.execute_script(
        """
        return window.__bootFetches
          .filter(item => item.path === '/api/tmux-window')
          .map(item => `${item.method} ${item.path}`);
        """
    )
    query = browser.execute_script(
        """
        const item = window.__bootFetches.find(entry => entry.path === '/api/tmux-window');
        return item ? new URLSearchParams(item.search || '').toString() : '';
        """
    )
    assert buttons == [
        {"text": "0:bash", "index": "0", "pressed": "false", "active": False},
        {"text": "1:codex", "index": "1", "pressed": "true", "active": True},
        {"text": "2:pytest", "index": "2", "pressed": "false", "active": False},
    ]
    assert after_click_buttons == [
        {"index": "0", "pressed": "false", "active": False},
        {"index": "1", "pressed": "false", "active": False},
        {"index": "2", "pressed": "true", "active": True},
    ]
    assert browser.execute_script("return document.querySelector('.panel')?.classList.contains('focused-pane')") is True
    assert fetches == ["POST /api/tmux-window"]
    assert query == "session=1&window=2"


def test_dockview_window_bar_keeps_all_agent_windows_through_stale_metadata_and_state_refreshes(browser, tmp_path):
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "node", "window_active": True, "active": True, "process_label": "codex"},
                {"target": "%2", "window": 1, "window_name": "claude", "window_active": False, "active": True, "process_label": "claude"},
            ],
        },
    }
    auto_approve_payload = {
        "session_order": ["1"],
        "sessions": {
            "1": {
                "target": "1",
                "enabled": True,
                "agent_windows": [
                    {"kind": "codex", "state": "working", "window_index": 0, "window_name": "node", "window_label": "0:codex", "pane_target": "%1", "current": True, "window_active": True},
                    {"kind": "claude", "state": "idle", "window_index": 1, "window_name": "claude", "window_label": "1:claude", "pane_target": "%2"},
                    {"kind": "claude", "state": "idle", "window_index": 2, "window_name": "python3", "window_label": "2:claude", "pane_target": "%3"},
                    {"kind": "claude", "state": "idle", "window_index": 3, "window_name": "python3", "window_label": "3:claude", "pane_target": "%4"},
                ],
            },
        },
        "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
        auto_approve_payload=auto_approve_payload,
    )
    wait_for_dockview(browser, min_tabs=1)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const payload = arguments[0];
        const staleMetadata = {
          session_order: ['1'],
          sessions: {
            '1': {
              panes: [
                {target: '%1', window: 0, window_name: 'node', window_active: true, active: true, process_label: 'codex'},
                {target: '%2', window: 1, window_name: 'claude', window_active: false, active: true, process_label: 'claude'},
              ],
            },
          },
        };
        const buttons = () => Array.from(document.querySelectorAll('.panel-detail-row [data-window-index]')).map(button => ({
          index: button.dataset.windowIndex,
          label: button.querySelector('.tmux-window-name-text')?.textContent.trim() || '',
          hasActivity: !!button.querySelector('.agent-window-activity, .agent-window-agent-icon, .agent-window-status-dot'),
        }));
        applyAutoApprovePayload(payload);
        const beforeStaleMetadata = buttons();
        applySessionMetadataPayload(staleMetadata, {refreshAuto: false, refreshActivity: false, refreshContext: false})
          .then(() => {
            const afterStaleMetadata = buttons();
            const workingPayload = structuredClone(payload);
            workingPayload.sessions['1'].agent_windows[0].state = 'idle';
            workingPayload.sessions['1'].agent_windows[2].state = 'working';
            workingPayload.sessions['1'].agent_windows[3].state = 'working';
            applyAutoApprovePayload(workingPayload);
            done({beforeStaleMetadata, afterStaleMetadata, afterStateRefresh: buttons()});
          })
          .catch(error => done({error: String(error)}));
        """,
        auto_approve_payload,
    )
    expected_buttons = [
        {"index": "0", "label": "0:codex", "hasActivity": True},
        {"index": "1", "label": "1:claude", "hasActivity": True},
        {"index": "2", "label": "2:claude", "hasActivity": True},
        {"index": "3", "label": "3:claude", "hasActivity": True},
    ]
    assert metrics == {
        "beforeStaleMetadata": expected_buttons,
        "afterStaleMetadata": expected_buttons,
        "afterStateRefresh": expected_buttons,
    }, metrics
    browser.find_element("css selector", '.panel-detail-row [data-window-index="3"]').click()
    click_result = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const requests = window.__bootFetches.filter(entry => entry.path === '/api/tmux-window');
            if (requests.length !== 1) return false;
            const button = document.querySelector('.panel-detail-row [data-window-index="3"]');
            return {
              request: requests[0],
              active: button?.classList.contains('active') || false,
              pressed: button?.getAttribute('aria-pressed') || '',
            };
            """
        )
    )
    assert click_result["request"]["method"] == "POST", click_result
    assert click_result["request"]["search"] == "?session=1&window=3", click_result
    assert click_result["active"] is True, click_result
    assert click_result["pressed"] == "true", click_result


def test_dockview_focused_window_button_switches_before_poll_replaces_pressed_button(browser, tmp_path):
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "bash", "window_active": True, "active": True, "process_label": "bash"},
                {"target": "%2", "window": 1, "window_name": "codex", "window_active": False, "active": True, "process_label": "codex"},
            ],
        },
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
    )
    wait_for_dockview(browser, min_tabs=1)
    result = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const panel = document.querySelector('.panel');
            const pressed = document.querySelector('.panel-detail-row [data-window-index="1"]');
            if (!panel || !pressed) return false;
            panel.classList.add('focused-pane', 'active-pane');
            pressed.dispatchEvent(new PointerEvent('pointerdown', {
              bubbles: true, cancelable: true, pointerId: 7, pointerType: 'mouse', button: 0, buttons: 1,
            }));
            updatePanelControlLabels('1', {
              panes: [
                {target: '%1', window: 0, window_name: 'bash', window_active: true, active: true, process_label: 'bash-poll'},
                {target: '%2', window: 1, window_name: 'codex', window_active: false, active: true, process_label: 'codex'},
              ],
            });
            pressed.dispatchEvent(new PointerEvent('pointerup', {
              bubbles: true, cancelable: true, pointerId: 7, pointerType: 'mouse', button: 0,
            }));
            pressed.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, button: 0}));
            return {
              detachedAfterPoll: !pressed.isConnected,
              requestCount: window.__bootFetches.filter(entry => entry.path === '/api/tmux-window').length,
              request: window.__bootFetches.find(entry => entry.path === '/api/tmux-window') || null,
              targetActive: document.querySelector('.panel-detail-row [data-window-index="1"]')?.classList.contains('active') || false,
            };
            """
        )
    )
    assert result["detachedAfterPoll"] is True, result
    assert result["requestCount"] == 1, result
    assert result["request"] and result["request"]["method"] == "POST", result
    assert result["request"] and result["request"]["path"] == "/api/tmux-window", result
    assert result["request"] and result["request"]["search"] == "?session=1&window=1", result
    assert result["targetActive"] is True, result


def test_dockview_yellow_window_ball_click_switches_and_acknowledges(browser, tmp_path):
    stopped_ts = int(time.time()) - 5
    cooldown_key = '["agent-window","1","2","","claude","cooldown","stopped"]'
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "bash", "window_active": False, "active": True, "process_label": "bash"},
                {"target": "%2", "window": 1, "window_name": "claude", "window_active": True, "active": True, "process_label": "claude"},
                {"target": "%3", "window": 2, "window_name": "claude", "window_active": False, "active": True, "process_label": "claude"},
            ],
        },
    }
    auto_approve_payload = {
        "session_order": ["1"],
        "sessions": {
            "1": {
                "target": "1",
                "enabled": True,
                "agent_windows": [
                    {"kind": "claude", "state": "working", "window_index": 1, "window_label": "1:claude"},
                    {"kind": "claude", "state": "idle", "window_index": 2, "window_label": "2:claude", "working_stopped_ts": stopped_ts, "cooldown_attention_key": cooldown_key},
                ],
            },
        },
        "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
        auto_approve_payload=auto_approve_payload,
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const button = document.querySelector('.panel-detail-row [data-window-index="2"]');
            return !!button
              && !!button.querySelector('.agent-window-agent-icon')
              && !!button.querySelector('.agent-window-status-dot.status-indicator--cooldown')
              && document.querySelector('.panel-detail-row [data-window-index="1"]')?.classList.contains('active');
            """
        )
    )
    click_started = time.monotonic()
    browser.find_element("css selector", '.panel-detail-row [data-window-index="2"]').click()
    immediate = browser.execute_script(
        """
        const button = document.querySelector('.panel-detail-row [data-window-index="2"]');
        return {
          active: button?.classList.contains('active') === true,
          pressed: button?.getAttribute('aria-pressed') || '',
          hasActivity: !!button?.querySelector('.agent-window-activity, .agent-window-agent-icon, .agent-window-status-dot'),
        };
        """
    )
    assert immediate == {
        "active": True,
        "pressed": "true",
        "hasActivity": True,
    }
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return window.__bootFetches.some(entry => entry.path === '/api/tmux-window')
              && document.querySelector('.panel-detail-row [data-window-index="2"]')?.classList.contains('active');
            """
        )
    )
    request = browser.execute_script(
        """
        const fetch = window.__bootFetches.find(entry => entry.path === '/api/tmux-window');
        return fetch ? `${fetch.method} ${fetch.path}?${new URLSearchParams(fetch.search || '').toString()}` : '';
        """
    )
    assert request == "POST /api/tmux-window?session=1&window=2"
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const button = document.querySelector('.panel-detail-row [data-window-index="2"]');
            const tabDot = document.querySelector('.pane-tab[data-pane-tab="1"] .session-agent-activity-marker .agent-window-status-dot');
            return !!button
              && !!button.querySelector('.agent-window-agent-icon')
              && !button.querySelector('.agent-window-status-dot')
              && button.textContent.includes('2:claude')
              && !!tabDot
              && tabDot.classList.contains('status-indicator--working')
              && !tabDot.classList.contains('status-indicator--cooldown');
            """
        )
    )
    acknowledged_state = browser.execute_script(
        """
        const button = document.querySelector('.panel-detail-row [data-window-index="2"]');
        return {
          hasWindowLabel: button?.textContent.includes('2:claude') === true,
          activityCount: button?.querySelectorAll('.agent-window-activity').length || 0,
          dotCount: button?.querySelectorAll('.agent-window-status-dot').length || 0,
          ackPosts: window.__bootFetches.filter(entry => entry.path === '/api/attention-ack').length,
        };
        """
    )
    assert time.monotonic() - click_started >= 0.6
    assert acknowledged_state == {"hasWindowLabel": True, "activityCount": 1, "dotCount": 0, "ackPosts": 1}


def test_dockview_red_window_ball_click_switches_and_acknowledges(browser, tmp_path):
    prompt_key = '["prompt","1","waiting"]'
    attention_key = '["agent-window","1","2","","claude","needs-input","waiting"]'
    cooldown_key = '["agent-window","1","3","","codex","cooldown","1234"]'
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "bash", "window_active": True, "active": True, "process_label": "bash"},
                {"target": "%2", "window": 1, "window_name": "claude", "window_active": False, "active": True, "process_label": "claude"},
                {"target": "%3", "window": 2, "window_name": "claude", "window_active": False, "active": True, "process_label": "claude"},
                {"target": "%4", "window": 3, "window_name": "codex", "window_active": False, "active": True, "process_label": "codex"},
            ],
        },
    }
    auto_approve_payload = {
        "session_order": ["1"],
        "sessions": {
            "1": {
                "target": "1",
                "enabled": True,
                "prompt": {"visible": True, "question_text": "waiting", "text": "waiting", "attention_key": prompt_key},
                "screen": {"key": "needs-input", "text": "waiting", "question_text": "waiting"},
                "prompt_attention_key": prompt_key,
                "agent_windows": [
                    {"kind": "claude", "state": "needs-input", "window_index": 2, "window_label": "2:claude", "screen_text": "waiting", "attention_key": attention_key},
                    {"kind": "codex", "state": "idle", "window_index": 3, "window_label": "3:codex", "working_stopped_ts": 1234, "cooldown_attention_key": cooldown_key},
                ],
            },
        },
        "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
        auto_approve_payload=auto_approve_payload,
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const button = document.querySelector('.panel-detail-row [data-window-index="2"]');
            const tab = document.querySelector('.pane-tab[data-pane-tab="1"]');
            return !!button
              && !!button.querySelector('.agent-window-agent-icon')
              && !!button.querySelector('.agent-window-status-dot.status-indicator--attention')
              && !tab?.querySelector('.session-state-badge.session-state-needs-input')
              && !!tab?.querySelector('.session-agent-activity-marker .agent-window-status-dot.status-indicator--attention')
              && document.querySelector('.panel-detail-row [data-window-index="0"]')?.classList.contains('active');
            """
        )
    )
    click_started = time.monotonic()
    browser.find_element("css selector", '.panel-detail-row [data-window-index="2"]').click()
    immediate = browser.execute_script(
        """
        const button = document.querySelector('.panel-detail-row [data-window-index="2"]');
        return {
          active: button?.classList.contains('active') === true,
          pressed: button?.getAttribute('aria-pressed') || '',
          hasActivity: !!button?.querySelector('.agent-window-activity, .agent-window-agent-icon, .agent-window-status-dot'),
          ackPosts: window.__bootFetches.filter(entry => entry.path === '/api/attention-ack').length,
        };
        """
    )
    assert immediate == {
        "active": True,
        "pressed": "true",
        "hasActivity": True,
        "ackPosts": 0,
    }
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return window.__bootFetches.some(entry => entry.path === '/api/tmux-window')
              && document.querySelector('.panel-detail-row [data-window-index="2"]')?.classList.contains('active');
            """
        )
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const button = document.querySelector('.panel-detail-row [data-window-index="2"]');
            return !!button
              && !!button.querySelector('.agent-window-agent-icon')
              && !button.querySelector('.agent-window-status-dot')
              && button.textContent.includes('2:claude')
              && window.__bootFetches.some(entry => entry.path === '/api/attention-ack');
            """
        )
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const tab = document.querySelector('.pane-tab[data-pane-tab="1"]');
            const dot = tab?.querySelector('.session-agent-activity-marker .agent-window-status-dot');
            return !!tab
              && !tab.querySelector('.session-state-badge.session-state-needs-input')
              && !!dot
              && dot.classList.contains('status-indicator--cooldown')
              && !dot.classList.contains('status-indicator--attention')
              && !dot.classList.contains('agent-window-status-dot--segmented')
              && !dot.classList.contains('agent-window-status-dot--tone-attention')
              && !dot.classList.contains('agent-window-status-dot--tone-cooldown');
            """
        )
    )
    assert time.monotonic() - click_started >= 0.6
    ack_bodies = browser.execute_script(
        """
        return window.__bootFetches
          .filter(entry => entry.path === '/api/attention-ack')
          .map(entry => entry.body);
        """
    )
    acked_keys = {key for body in ack_bodies for key in body.get("keys", [])}
    assert acked_keys == {prompt_key, attention_key}


def test_dockview_red_window_ball_keypress_acknowledges_after_delay(browser, tmp_path):
    attention_key = '["agent-window","1","2","","claude","needs-input","waiting"]'
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "bash", "window_active": False, "active": True, "process_label": "bash"},
                {"target": "%2", "window": 2, "window_name": "claude", "window_active": True, "active": True, "process_label": "claude"},
            ],
        },
    }
    auto_approve_payload = {
        "session_order": ["1"],
        "sessions": {
            "1": {
                "target": "1",
                "enabled": True,
                "agent_windows": [
                    {"kind": "claude", "state": "needs-input", "window_index": 2, "window_label": "2:claude", "current": True, "window_active": True, "screen_text": "waiting", "attention_key": attention_key},
                ],
            },
        },
        "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
        auto_approve_payload=auto_approve_payload,
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const button = document.querySelector('.panel-detail-row [data-window-index="2"]');
            return document.querySelector('#term-1')
              && !!button
              && !!button.querySelector('.agent-window-agent-icon')
              && !!button.querySelector('.agent-window-status-dot.status-indicator--attention');
            """
        )
    )
    started = time.monotonic()
    immediate = browser.execute_script(
        """
        const container = document.querySelector('#term-1');
        const beforeFrames = (window.__bootSocketInstances || []).flatMap(socket => socket.sent || []).length;
        container.dispatchEvent(new KeyboardEvent('keydown', {key: 'a', bubbles: true, cancelable: true}));
        const afterFrames = (window.__bootSocketInstances || []).flatMap(socket => socket.sent || []).length;
        return {
          hasActivity: !!document.querySelector('.panel-detail-row [data-window-index="2"] .agent-window-activity, .panel-detail-row [data-window-index="2"] .agent-window-agent-icon, .panel-detail-row [data-window-index="2"] .agent-window-status-dot'),
          ackPosts: window.__bootFetches.filter(entry => entry.path === '/api/attention-ack').length,
          newSocketFrames: afterFrames - beforeFrames,
        };
        """
    )
    assert immediate == {"hasActivity": True, "ackPosts": 0, "newSocketFrames": 0}
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const button = document.querySelector('.panel-detail-row [data-window-index="2"]');
            return !!button
              && !!button.querySelector('.agent-window-agent-icon')
              && !button.querySelector('.agent-window-status-dot')
              && window.__bootFetches.some(entry => entry.path === '/api/attention-ack');
            """
        )
    )
    assert time.monotonic() - started >= 0.8
    ack_body = browser.execute_script(
        """
        const fetch = window.__bootFetches.find(entry => entry.path === '/api/attention-ack');
        return fetch?.body || null;
        """
    )
    assert ack_body == {"keys": [attention_key]}


def test_dockview_red_window_ball_xterm_data_acknowledges_after_delay(browser, tmp_path):
    attention_key = '["agent-window","1","2","","claude","needs-input","waiting"]'
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "bash", "window_active": False, "active": True, "process_label": "bash"},
                {"target": "%2", "window": 2, "window_name": "claude", "window_active": True, "active": True, "process_label": "claude"},
                {"target": "%3", "window": 3, "window_name": "codex", "window_active": False, "active": True, "process_label": "codex"},
            ],
        },
    }
    auto_approve_payload = {
        "session_order": ["1"],
        "sessions": {
            "1": {
                "target": "1",
                "enabled": True,
                "agent_windows": [
                    {"kind": "claude", "state": "needs-input", "window_index": 2, "window_label": "2:claude", "current": True, "window_active": True, "screen_text": "waiting", "attention_key": attention_key},
                    {"kind": "codex", "state": "working", "window_index": 3, "window_label": "3:codex", "current": False, "window_active": False, "working_elapsed_seconds": 4},
                ],
            },
        },
        "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
        auto_approve_payload=auto_approve_payload,
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const terminal = (window.__bootTerminalInstances || [])[0];
            const socket = (window.__bootSocketInstances || [])[0];
            return terminal?._onData
              && socket?.readyState === WebSocket.OPEN
              && !!document.querySelector('.panel-detail-row [data-window-index="2"]')
              && !!document.querySelector('.panel-detail-row [data-window-index="2"] .agent-window-agent-icon')
              && !!document.querySelector('.panel-detail-row [data-window-index="2"] .agent-window-status-dot.status-indicator--attention')
              && !!document.querySelector('.panel-detail-row [data-window-index="3"] .agent-window-status-dot.status-indicator--working');
            """
        )
    )
    started = time.monotonic()
    immediate = browser.execute_script(
        """
        const terminal = window.__bootTerminalInstances[0];
        clearClientPerfCounters();
        const beforeFrames = (window.__bootSocketInstances || []).flatMap(socket => socket.sent || []).length;
        terminal._onData('a');
        const unrelatedWorkingBall = document.querySelector('.panel-detail-row [data-window-index="3"] .agent-window-status-dot.status-indicator--working');
        for (const character of 'bcdefghijklmnopqrstuvwxy') terminal._onData(character);
        const afterFrames = (window.__bootSocketInstances || []).flatMap(socket => socket.sent || []).length;
        const perf = Object.fromEntries(clientPerfSummary().map(counter => [counter.name, counter]));
        return {
          hasActivity: !!document.querySelector('.panel-detail-row [data-window-index="2"] .agent-window-activity, .panel-detail-row [data-window-index="2"] .agent-window-agent-icon, .panel-detail-row [data-window-index="2"] .agent-window-status-dot'),
          ackPosts: window.__bootFetches.filter(entry => entry.path === '/api/attention-ack').length,
          newSocketFrames: afterFrames - beforeFrames,
          unrelatedBallStable: unrelatedWorkingBall === document.querySelector('.panel-detail-row [data-window-index="3"] .agent-window-status-dot.status-indicator--working'),
          renderPanels: perf.renderPanels?.count || 0,
          renderPaneTabStrips: perf.renderPaneTabStrips?.count || 0,
          renderSessionButtons: perf.renderSessionButtons?.count || 0,
          terminalInputs: perf['term.onData']?.count || 0,
        };
        """
    )
    assert immediate == {
        "hasActivity": True,
        "ackPosts": 0,
        "newSocketFrames": 25,
        "unrelatedBallStable": True,
        "renderPanels": 1,
        "renderPaneTabStrips": 1,
        "renderSessionButtons": 1,
        "terminalInputs": 25,
    }
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const button = document.querySelector('.panel-detail-row [data-window-index="2"]');
            return !!button
              && !!button.querySelector('.agent-window-agent-icon')
              && !button.querySelector('.agent-window-status-dot')
              && window.__bootFetches.some(entry => entry.path === '/api/attention-ack');
            """
        )
    )
    assert time.monotonic() - started >= 0.8
    ack_body = browser.execute_script(
        """
        const fetch = window.__bootFetches.find(entry => entry.path === '/api/attention-ack');
        return fetch?.body || null;
        """
    )
    sent_frame = browser.execute_script(
        """
        const frames = (window.__bootSocketInstances || []).flatMap(socket => socket.sent || []);
        return frames.map(frame => JSON.parse(frame)).find(frame => frame.type === 'input') || null;
        """
    )
    assert ack_body == {"keys": [attention_key]}
    assert sent_frame == {"type": "input", "data": "a"}


@pytest.mark.skip(reason="window selectors no longer render polling-driven agent or state glyphs")
def test_dockview_window_bar_working_agent_glyph_uses_static_symbol_and_static_ball(browser, tmp_path):
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "bash", "window_active": False, "active": True, "process_label": "bash"},
                {"target": "%2", "window": 1, "window_name": "codex", "window_active": True, "active": True, "process_label": "codex"},
                {"target": "%3", "window": 2, "window_name": "claude", "window_active": False, "active": True, "process_label": "claude"},
            ],
        },
    }
    auto_approve_payload = {
        "session_order": ["1"],
        "sessions": {
            "1": {
                "target": "1",
                "enabled": True,
                "agent_windows": [
                    {"kind": "codex", "state": "working", "window_index": 1, "window_label": "1:codex"},
                    {"kind": "claude", "state": "idle", "window_index": 2, "window_label": "2:claude", "idle_since": 1},
                ],
            },
        },
        "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
        auto_approve_payload=auto_approve_payload,
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.agent-window-agent-icon--working')
              && Array.from(document.querySelectorAll('.tmux-window-button')).some(button =>
                button.textContent.includes('2:claude') && button.querySelector('.agent-icon.claude'));
            """
        )
    )
    metrics = browser.execute_script(
        """
        const workingButton = Array.from(document.querySelectorAll('.tmux-window-button')).find(button => button.textContent.includes('1:codex'));
        const working = workingButton?.querySelector('.agent-window-agent-icon--working');
        const idleButton = Array.from(document.querySelectorAll('.tmux-window-button')).find(button => button.textContent.includes('2:claude'));
        const idleIcon = idleButton?.querySelector('.agent-icon.claude');
        const idleDot = idleButton?.querySelector('.agent-window-status-dot');
        const workingDot = workingButton?.querySelector('.agent-window-status-dot');
        const workingStyle = getComputedStyle(working);
        const workingDotStyle = workingDot ? getComputedStyle(workingDot) : null;
        const workingDotBefore = workingDot ? getComputedStyle(workingDot, '::before') : null;
        return {
          workingText: (working?.textContent || '').trim(),
          idleText: idleDot?.textContent || '',
          workingHasSvg: !!working?.querySelector('svg'),
          idleHasSvg: !!idleIcon?.querySelector('svg'),
          workingHasAgentIcon: working?.classList.contains('agent-icon') || false,
          workingHasParent: working?.classList.contains('status-indicator') || false,
          workingHasDot: working?.classList.contains('status-indicator--dot') || false,
          workingHasState: working?.classList.contains('agent-window-agent-icon--working') || false,
          idleHasParent: idleDot?.classList.contains('status-indicator') || false,
          idleHasDot: idleDot?.classList.contains('status-indicator--dot') || false,
          idleHasState: idleDot?.classList.contains('status-indicator--idle') || false,
          workingAnimationName: workingStyle.animationName,
          workingOpacity: workingStyle.opacity,
          workingDotExists: !!workingDot,
          workingDotIsWorkingTone: workingDot?.classList.contains('status-indicator--working') || false,
          workingDotTransitionPulse: workingDot?.classList.contains('agent-window-status-dot--transition-pulse') || false,
          workingDotSubwindowPulse: workingDot?.classList.contains('agent-window-status-dot--subwindow-pulse') || false,
              workingDotAnimationName: workingDotStyle ? workingDotStyle.animationName : null,
              workingDotBoxShadow: workingDotStyle ? workingDotStyle.boxShadow : null,
              workingDotBeforeAnimationName: workingDotBefore ? workingDotBefore.animationName : null,
              workingDotBeforeFilter: workingDotBefore ? workingDotBefore.filter : null,
          statusPulseDisabled: document.documentElement.classList.contains('status-pulse-disabled'),
          idleDotCount: idleButton?.querySelectorAll('.agent-window-status-dot').length || 0,
        };
        """
    )
    assert metrics["workingText"] == "", metrics
    assert metrics["idleText"] == "", metrics
    assert metrics["workingHasSvg"] is True, metrics
    assert metrics["idleHasSvg"] is True, metrics
    assert metrics["workingHasAgentIcon"] is True, metrics
    assert metrics["workingHasParent"] is False, metrics
    assert metrics["workingHasDot"] is False, metrics
    assert metrics["workingHasState"] is True, metrics
    assert metrics["idleHasParent"] is False, metrics
    assert metrics["idleHasDot"] is False, metrics
    assert metrics["idleHasState"] is False, metrics
    assert metrics["idleDotCount"] == 0, metrics
    # Working = static AI symbol + a SEPARATE green ball (side by side, not alternating).
    # With continuous status pulsing disabled, the default workflow transition pulse still runs.
    assert metrics["workingAnimationName"] == "none", metrics
    assert float(metrics["workingOpacity"]) == 1, metrics
    assert metrics["workingDotExists"] is True, metrics
    assert metrics["workingDotIsWorkingTone"] is True, metrics
    assert metrics["workingDotTransitionPulse"] is True, metrics
    assert metrics["workingDotSubwindowPulse"] is True, metrics
    assert metrics["statusPulseDisabled"] is True, metrics
    assert metrics["workingDotAnimationName"] == "none", metrics
    assert metrics["workingDotBoxShadow"] in ("", "none"), metrics
    assert metrics["workingDotBeforeAnimationName"] == "agent-status-opacity-pulse", metrics
    assert metrics["workingDotBeforeFilter"] == "none", metrics


@pytest.mark.skip(reason="window selectors no longer render polling-driven agent or state glyphs")
def test_dockview_window_bar_status_dots_render_subwindow_state_glyphs_only(browser, tmp_path):
    stopped_at = time.time()
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "claude", "window_active": False, "active": True, "process_label": "claude"},
                {"target": "%2", "window": 1, "window_name": "codex", "window_active": True, "active": True, "process_label": "codex"},
                {"target": "%3", "window": 2, "window_name": "claude", "window_active": False, "active": True, "process_label": "claude"},
            ],
        },
    }
    auto_approve_payload = {
        "session_order": ["1"],
        "sessions": {
            "1": {
                "target": "1",
                "enabled": True,
                "agent_windows": [
                    {"kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude"},
                    {"kind": "codex", "state": "needs-input", "window_index": 1, "window_label": "1:codex", "attention_key": "ask-1"},
                    {"kind": "claude", "state": "idle", "window_index": 2, "window_label": "2:claude", "working_stopped_ts": stopped_at, "cooldown_attention_key": "cool-1"},
                ],
            },
        },
        "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
        auto_approve_payload=auto_approve_payload,
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.panel-detail-row .tmux-window-button[data-window-index="0"]')
              && document.querySelector('.panel-detail-row .tmux-window-button[data-window-index="1"]')
              && document.querySelector('.panel-detail-row .tmux-window-button[data-window-index="2"]');
            """
        )
    )
    metrics = browser.execute_script(
        """
        function normalizeCssColor(value) {
          const text = String(value || '').trim();
          if (!text) return '';
          const probe = document.createElement('span');
          probe.style.color = text;
          document.body.appendChild(probe);
          const color = getComputedStyle(probe).color;
          probe.remove();
          return color;
        }
        function readButton(index) {
          const button = document.querySelector(`.panel-detail-row .tmux-window-button[data-window-index="${index}"]`);
          const dot = button?.querySelector('.agent-window-status-dot');
          const buttonStyle = button ? getComputedStyle(button) : null;
          const style = dot ? getComputedStyle(dot) : null;
          const before = dot ? getComputedStyle(dot, '::before') : null;
          const after = dot ? getComputedStyle(dot, '::after') : null;
          return {
            label: button?.textContent || '',
            active: button?.classList?.contains('active') || false,
            buttonColor: buttonStyle?.color || '',
            dotText: dot?.textContent || '',
            dotColor: style?.color || '',
            textIndent: style?.textIndent || '',
            className: dot?.className || '',
            animationName: style?.animationName || '',
            boxShadow: style?.boxShadow || '',
            filter: style?.filter || '',
            glyphScale: style?.getPropertyValue('--subwindow-status-glyph-scale').trim() || '',
            glyphFill: normalizeCssColor(style?.getPropertyValue('--subwindow-status-glyph-fill')),
            beforeContent: before?.content || '',
            beforeBackground: before?.backgroundColor || '',
            beforeAnimationName: before?.animationName || '',
            beforeBorderStartWidth: before?.borderInlineStartWidth || '',
            beforeBorderStartColor: before?.borderInlineStartColor || '',
            beforeBorderTopWidth: before?.borderTopWidth || '',
            beforeBorderTopColor: before?.borderTopColor || '',
            beforeBoxShadow: before?.boxShadow || '',
            beforeFilter: before?.filter || '',
            beforeTransform: before?.transform || '',
            beforeInlineSize: before?.inlineSize || before?.width || '',
            beforeInsetInlineStart: before?.insetInlineStart || '',
            afterContent: after?.content || '',
            afterBackground: after?.backgroundColor || '',
            afterAnimationName: after?.animationName || '',
            afterBorderTopWidth: after?.borderTopWidth || '',
            afterBorderTopColor: after?.borderTopColor || '',
            afterBoxShadow: after?.boxShadow || '',
            afterFilter: after?.filter || '',
            afterTransform: after?.transform || '',
            afterInsetInlineStart: after?.insetInlineStart || '',
          };
        }
        const staleButton = document.createElement('button');
        staleButton.className = 'tab tmux-window-button';
        staleButton.innerHTML = '<span class="agent-window-status-dot status-indicator status-indicator--dot status-indicator--attention">●</span>';
        document.body.append(staleButton);
        const staleDot = staleButton.querySelector('.agent-window-status-dot');
        const staleStyle = getComputedStyle(staleDot);
        const staleBefore = getComputedStyle(staleDot, '::before');
        const tabDot = document.querySelector('.pane-tab .session-agent-activity-marker .agent-window-status-dot');
        const tabBefore = tabDot ? getComputedStyle(tabDot, '::before') : null;
        return {
          working: readButton('0'),
          attention: readButton('1'),
          cooldown: readButton('2'),
          stale: {
            glyphScale: staleStyle.getPropertyValue('--subwindow-status-glyph-scale').trim() || '',
            glyphFill: normalizeCssColor(staleStyle.getPropertyValue('--subwindow-status-glyph-fill')),
            beforeBackground: staleBefore.backgroundColor || '',
          },
          tabDotText: tabDot?.textContent || '',
          tabDotBeforeContent: tabBefore?.content || '',
          tabDotBeforeBackground: tabBefore?.backgroundColor || '',
        };
        """
    )
    assert metrics["working"]["dotText"] == "●", metrics
    assert metrics["attention"]["dotText"] == "●", metrics
    assert metrics["cooldown"]["dotText"] == "●", metrics
    assert metrics["working"]["textIndent"] == "0px", metrics
    assert metrics["attention"]["textIndent"] == "0px", metrics
    assert metrics["cooldown"]["textIndent"] == "0px", metrics
    assert metrics["working"]["dotColor"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["attention"]["dotColor"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["cooldown"]["dotColor"] == "rgba(0, 0, 0, 0)", metrics
    assert "agent-window-status-dot--subwindow-pulse" in metrics["working"]["className"], metrics
    assert "agent-window-status-dot--subwindow-pulse" in metrics["attention"]["className"], metrics
    assert "agent-window-status-dot--subwindow-pulse" in metrics["cooldown"]["className"], metrics
    assert metrics["working"]["animationName"] == "none", metrics
    assert metrics["attention"]["animationName"] == "none", metrics
    assert metrics["cooldown"]["animationName"] == "none", metrics
    assert metrics["working"]["boxShadow"] in ("", "none"), metrics
    assert metrics["attention"]["boxShadow"] in ("", "none"), metrics
    assert metrics["cooldown"]["boxShadow"] in ("", "none"), metrics
    assert metrics["working"]["glyphScale"] == "0.8", metrics
    assert metrics["attention"]["glyphScale"] == "0.8", metrics
    assert metrics["cooldown"]["glyphScale"] == "0.8", metrics
    assert metrics["stale"]["glyphScale"] == "0.8", metrics
    assert metrics["working"]["beforeContent"] == '""', metrics
    assert metrics["attention"]["beforeContent"] == '""', metrics
    assert metrics["cooldown"]["beforeContent"] == '""', metrics
    assert metrics["working"]["beforeAnimationName"] == "agent-status-opacity-pulse", metrics
    assert metrics["working"]["beforeFilter"] == "none", metrics
    assert metrics["attention"]["beforeAnimationName"] == "agent-status-opacity-pulse", metrics
    assert metrics["cooldown"]["beforeAnimationName"] == "agent-status-opacity-pulse", metrics
    assert metrics["cooldown"]["afterAnimationName"] == "agent-status-opacity-pulse", metrics
    assert metrics["working"]["active"] is False, metrics
    assert metrics["attention"]["active"] is True, metrics
    assert metrics["working"]["beforeBorderStartColor"] == metrics["working"]["glyphFill"], metrics
    assert metrics["working"]["beforeBorderStartColor"] != metrics["working"]["buttonColor"], metrics
    assert metrics["working"]["beforeBorderStartColor"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["working"]["beforeBorderStartWidth"] != "0px", metrics
    assert metrics["attention"]["beforeBackground"] == metrics["attention"]["glyphFill"], metrics
    assert metrics["attention"]["beforeBackground"] == "rgb(220, 38, 38)", metrics
    assert metrics["stale"]["beforeBackground"] == "rgb(220, 38, 38)", metrics
    assert metrics["attention"]["beforeBorderTopWidth"] == "0px", metrics
    assert metrics["cooldown"]["beforeBackground"] == metrics["cooldown"]["glyphFill"], metrics
    assert metrics["cooldown"]["afterBackground"] == metrics["cooldown"]["glyphFill"], metrics
    assert metrics["cooldown"]["beforeBackground"] == "rgb(255, 214, 51)", metrics
    assert metrics["cooldown"]["afterBackground"] == "rgb(255, 214, 51)", metrics
    assert metrics["cooldown"]["beforeBackground"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["cooldown"]["beforeBorderTopWidth"] == "0px", metrics
    assert metrics["cooldown"]["afterBorderTopWidth"] == "0px", metrics
    assert metrics["cooldown"]["afterContent"] == '""', metrics
    assert metrics["cooldown"]["afterBackground"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["cooldown"]["beforeTransform"] == metrics["cooldown"]["afterTransform"], metrics
    assert metrics["cooldown"]["beforeInsetInlineStart"] != metrics["cooldown"]["afterInsetInlineStart"], metrics
    assert metrics["working"]["beforeFilter"] == "none", metrics
    assert metrics["attention"]["beforeFilter"] == "none", metrics
    assert metrics["cooldown"]["beforeFilter"] == "none", metrics
    assert metrics["cooldown"]["afterFilter"] == "none", metrics
    cooldown_gap = (
        float(metrics["cooldown"]["afterInsetInlineStart"].replace("px", ""))
        - float(metrics["cooldown"]["beforeInsetInlineStart"].replace("px", ""))
        - float(metrics["cooldown"]["beforeInlineSize"].replace("px", ""))
    )
    assert cooldown_gap >= 1.0, metrics
    assert metrics["cooldown"]["beforeBoxShadow"] in ("", "none"), metrics
    assert metrics["cooldown"]["afterBoxShadow"] in ("", "none"), metrics
    assert metrics["tabDotText"] == "●", metrics
    assert metrics["tabDotBeforeContent"] in ("", "none"), metrics
    assert metrics["tabDotBeforeBackground"] in ("", "rgba(0, 0, 0, 0)"), metrics


def test_dockview_window_bar_active_agent_glyph_is_static_by_default(browser, tmp_path):
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "bash", "window_active": False, "active": True, "process_label": "bash"},
                {"target": "%2", "window": 1, "window_name": "codex", "window_active": True, "active": True, "process_label": "codex"},
            ],
        },
    }
    auto_approve_payload = {
        "session_order": ["1"],
        "sessions": {
            "1": {
                "target": "1",
                "enabled": False,
                "agent_windows": [
                    {"kind": "codex", "state": "idle", "window_index": 1, "window_label": "1:codex", "current": True, "window_active": True},
                ],
            },
        },
        "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
        auto_approve_payload=auto_approve_payload,
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return !!document.querySelector('.tmux-window-button.active .agent-window-agent-icon--active')"))
    metrics = browser.execute_script(
        """
        const button = Array.from(document.querySelectorAll('.tmux-window-button')).find(item => item.textContent.includes('1:codex'));
        const icon = button?.querySelector('.agent-window-agent-icon--active');
        const dot = button?.querySelector('.agent-window-status-dot');
        const style = getComputedStyle(icon);
        return {
          buttonActive: button?.classList.contains('active') || false,
          staticPulseDisabled: document.documentElement.classList.contains('status-pulse-disabled'),
          iconHasSvg: !!icon?.querySelector('svg'),
          iconAnimationName: style.animationName,
          iconWillChange: style.willChange,
          iconGlowRgb: style.getPropertyValue('--agent-working-glow-rgb').trim(),
          dotCount: button?.querySelectorAll('.agent-window-status-dot').length || 0,
          dotText: dot?.textContent || '',
        };
        """
    )
    assert metrics["buttonActive"] is True, metrics
    assert metrics["staticPulseDisabled"] is True, metrics
    assert metrics["iconHasSvg"] is True, metrics
    assert metrics["iconAnimationName"] == "none", metrics
    assert metrics["iconWillChange"] == "auto", metrics
    assert metrics["iconGlowRgb"] == "102 126 248", metrics
    assert metrics["dotCount"] == 0, metrics
    assert metrics["dotText"] == "", metrics


def test_dockview_working_glyph_shows_static_symbol_and_static_green_ball_by_default(browser, tmp_path):
    # Working = a STATIC agent symbol followed by a SEPARATE green ball (side by side, not alternating,
    # not a symbol pulse). Assert the symbol does not animate, the wrapper lays them out inline, and the
    # default workflow transition pulse runs on the green working-tone dot.
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "bash", "window_active": False, "active": True, "process_label": "bash"},
                {"target": "%2", "window": 1, "window_name": "codex", "window_active": True, "active": True, "process_label": "codex"},
            ],
        },
    }
    auto_approve_payload = {
        "session_order": ["1"],
        "sessions": {
            "1": {
                "target": "1",
                "enabled": True,
                "agent_windows": [
                    {"kind": "codex", "state": "working", "window_index": 1, "window_label": "1:codex"},
                ],
            },
        },
        "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
        auto_approve_payload=auto_approve_payload,
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return !!document.querySelector('.agent-window-agent-icon--working')")
    )
    data = browser.execute_script(
        """
        const sym = document.querySelector('.agent-window-agent-icon--working');
        const wrap = sym?.closest('.agent-window-activity');
        const dot = wrap?.querySelector('.agent-window-status-dot');
        const ss = getComputedStyle(sym);
        const ds = dot ? getComputedStyle(dot) : null;
        const before = dot ? getComputedStyle(dot, '::before') : null;
        return {
          reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
          wrapDisplay: wrap ? getComputedStyle(wrap).display : null,
          symAnimationName: ss.animationName,
          symOpacity: ss.opacity,
          statusPulseDisabled: document.documentElement.classList.contains('status-pulse-disabled'),
          dotPresent: !!dot,
          dotWorkingTone: dot ? dot.classList.contains('status-indicator--working') : false,
          dotTransitionPulse: dot ? dot.classList.contains('agent-window-status-dot--transition-pulse') : false,
          dotSubwindowPulse: dot ? dot.classList.contains('agent-window-status-dot--subwindow-pulse') : false,
          dotAnimationName: ds ? ds.animationName : null,
              dotBoxShadow: ds ? ds.boxShadow : null,
              dotBeforeAnimationName: before ? before.animationName : null,
              dotBeforeFilter: before ? before.filter : null,
          dotIterationCount: ds ? ds.animationIterationCount : null,
          dotPlayState: ds ? ds.animationPlayState : null,
        };
        """
    )
    # Symbol is static; the working state uses the separate ball for status.
    assert data["symAnimationName"] == "none", data
    assert float(data["symOpacity"]) == 1, data
    # Laid out side by side (base inline-flex), not the attention/cooldown grid stack.
    assert data["wrapDisplay"] == "flex", data
    # A separate green ball is present, while its sub-window play glyph visibly pulses.
    assert data["dotPresent"] is True, data
    assert data["dotWorkingTone"] is True, data
    assert data["statusPulseDisabled"] is True, data
    assert data["dotTransitionPulse"] is True, data
    assert data["dotSubwindowPulse"] is True, data
    assert data["dotAnimationName"] == "agent-status-opacity-pulse", data
    assert data["dotBoxShadow"] in ("", "none"), data
    assert data["dotBeforeAnimationName"] == "none", data
    assert data["dotBeforeFilter"] == "none", data
    identity = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const before = document.querySelector('.agent-window-agent-icon--working')?.closest('.tmux-window-button');
        const beforeDot = before?.querySelector('.agent-window-status-dot');
        setTimeout(() => {
          updatePanelWindowStepButtons('1', transcriptMeta.sessions?.['1']);
          requestAnimationFrame(() => {
            const after = document.querySelector('.agent-window-agent-icon--working')?.closest('.tmux-window-button');
            const afterDot = after?.querySelector('.agent-window-status-dot');
            done({sameButton: before === after, sameDot: beforeDot === afterDot});
          });
        }, 25);
        """
    )
    assert identity == {"sameButton": True, "sameDot": True}, identity


@pytest.mark.skip(reason="window selectors no longer render polling-driven agent or state glyphs")
def test_dockview_working_glyph_stays_distinct_under_reduced_motion(browser, tmp_path):
    # Regression: with prefers-reduced-motion active, the static symbol still stays separate from the
    # working ball, so a user with reduced motion does not see a working agent collapse into the same
    # static symbol as idle.
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "bash", "window_active": False, "active": True, "process_label": "bash"},
                {"target": "%2", "window": 1, "window_name": "codex", "window_active": True, "active": True, "process_label": "codex"},
                {"target": "%3", "window": 2, "window_name": "claude", "window_active": False, "active": True, "process_label": "claude"},
            ],
        },
    }
    auto_approve_payload = {
        "session_order": ["1"],
        "sessions": {
            "1": {
                "target": "1",
                "enabled": True,
                "agent_windows": [
                    {"kind": "codex", "state": "working", "window_index": 1, "window_label": "1:codex"},
                    {"kind": "claude", "state": "idle", "window_index": 2, "window_label": "2:claude", "idle_since": 1},
                ],
            },
        },
        "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
    }
    browser.execute_cdp_cmd("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
    try:
        load_dockview_runtime_boot_fixture(
            browser,
            tmp_path,
            "?sessions=1&layout=left&tabs=left:1",
            sessions=["1"],
            transcript_sessions=transcript_sessions,
            auto_approve_payload=auto_approve_payload,
        )
        wait_for_dockview(browser, min_tabs=1)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script("return !!document.querySelector('.agent-window-agent-icon--working')")
        )
        data = browser.execute_script(
            """
            const working = document.querySelector('.agent-window-agent-icon--working');
            const workingWrap = working?.closest('.agent-window-activity');
            const workingDot = workingWrap?.querySelector('.agent-window-status-dot');
            const idleSym = document.querySelector('.agent-window-agent-icon:not(.agent-window-agent-icon--working):not(.agent-window-agent-icon--active)');
            const idleWrap = idleSym?.closest('.agent-window-activity');
            const idleDot = idleWrap?.querySelector('.agent-window-status-dot');
            return {
              reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
              workingSymStatic: working ? getComputedStyle(working).animationName === 'none' : null,
              workingDotPresent: !!workingDot,
              workingDotWorkingTone: workingDot ? workingDot.classList.contains('status-indicator--working') : false,
              idleFound: !!idleSym,
              idleDotPresent: !!idleDot,
            };
            """
        )
        assert data["reducedMotion"] is True, data
        # Symbol is static (it never animates regardless of motion).
        assert data["workingSymStatic"] is True, data
        # The separate green ball keeps working distinct from idle even with motion disabled: working
        # renders a green working-tone status dot, idle renders no status dot.
        assert data["workingDotPresent"] is True, data
        assert data["workingDotWorkingTone"] is True, data
        assert data["idleFound"] is True, data
        assert data["idleDotPresent"] is False, data
    finally:
        browser.execute_cdp_cmd("Emulation.setEmulatedMedia", {"features": []})


def test_dockview_tab_working_ball_shows_when_session_works_via_screen_proxy(browser, tmp_path):
    # Regression for "Tab 7 is YO'ing but the AI symbol is not blinking". The YO ball spins on
    # sessionYoloIsWorking (per-window 'working' OR screen.key==='working'), but the dock-tab AI symbol
    # used to render only when a per-window agent row reported state==='working' exactly. When the working
    # signal arrives via the screen-state proxy (no per-window 'working' row), the ball spun while the
    # symbol vanished. Both must now pulse on the same condition.
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "bash", "window_active": False, "active": True, "process_label": "bash"},
                {"target": "%2", "window": 1, "window_name": "claude", "window_active": True, "active": True, "process_label": "claude"},
            ],
        },
    }
    auto_approve_payload = {
        "session_order": ["1"],
        "sessions": {
            "1": {
                "target": "1",
                "enabled": True,
                "screen": {"key": "working"},
                "agent_windows": [
                    # Note: the claude window is idle/current, NOT state=='working' — the working signal
                    # comes only from screen.key above, which is exactly the case that lost the symbol.
                    {"kind": "claude", "state": "idle", "window_index": 1, "window_label": "1:claude", "current": True, "window_active": True, "idle_since": 1},
                ],
            },
        },
        "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
        auto_approve_payload=auto_approve_payload,
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return !!document.querySelector('.dockview-pane-tab .session-yolo-marker, .pane-tab .session-yolo-marker')")
    )
    data = browser.execute_script(
        """
        const tab = document.querySelector('.dockview-pane-tab, .pane-tab');
        const marker = tab && tab.querySelector('.session-agent-activity-marker');
        const icon = tab && tab.querySelector('.session-agent-activity-marker .agent-window-agent-icon');
        const dot = tab && tab.querySelector('.session-agent-activity-marker .agent-window-status-dot');
        return {
          tabFound: !!tab,
          hasMarker: !!marker,
          hasAgentIcon: !!icon,
          dotPresent: !!dot,
          dotWorkingTone: dot ? dot.classList.contains('status-indicator--working') : false,
        };
        """
    )
    # The working tab indicator renders as a green ball even when working comes only from the
    # screen-state proxy; pane tabs omit the Claude/Codex symbol to reduce clutter.
    assert data["tabFound"] is True, data
    assert data["hasMarker"] is True, data
    assert data["hasAgentIcon"] is False, data
    assert data["dotPresent"] is True, data
    assert data["dotWorkingTone"] is True, data


@pytest.mark.skip(reason="window selectors no longer render polling-driven agent or state glyphs")
def test_dockview_tab_agent_ball_segments_visible_window_states(browser, tmp_path):
    stopped_ts = int(time.time()) - 5
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "claude", "window_active": True, "active": True, "process_label": "claude"},
                {"target": "%2", "window": 1, "window_name": "codex", "window_active": False, "active": True, "process_label": "codex"},
                {"target": "%3", "window": 2, "window_name": "claude", "window_active": False, "active": True, "process_label": "claude"},
            ],
        },
    }

    def load_with(agent_windows):
        load_dockview_runtime_boot_fixture(
            browser,
            tmp_path,
            "?sessions=1&layout=left&tabs=left:1",
            sessions=["1"],
            transcript_sessions=transcript_sessions,
            auto_approve_payload={
                "session_order": ["1"],
                "sessions": {
                    "1": {
                        "target": "1",
                        "enabled": True,
                        "agent_windows": agent_windows,
                    },
                },
                "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
            },
        )
        wait_for_dockview(browser, min_tabs=1)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script("return document.querySelectorAll('.tmux-window-button .agent-window-status-dot').length >= 2")
        )
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                const nodes = [
                  document.querySelector('.dockview-pane-tab[data-pane-tab="1"] .agent-window-activity'),
                  ...document.querySelectorAll('.tmux-window-button .agent-window-activity'),
                ].filter(Boolean);
                if (nodes.length < 3) return false;
                const delays = nodes.map(node => getComputedStyle(node).getPropertyValue('--attention-animation-delay').trim());
                return delays.every(Boolean) && new Set(delays).size === 1;
                """
            )
        )
        return browser.execute_script(
            """
            const read = selector => {
              const root = document.querySelector(selector);
              const wrap = root?.querySelector('.agent-window-activity');
              const dot = root?.querySelector('.agent-window-status-dot');
              const icon = root?.querySelector('.agent-window-agent-icon');
              const delay = wrap ? getComputedStyle(wrap).getPropertyValue('--attention-animation-delay').trim() : '';
              const dotStyle = dot ? getComputedStyle(dot) : null;
              const beforeStyle = dot ? getComputedStyle(dot, '::before') : null;
              const animation = dot?.getAnimations?.().find(item => item.animationName === 'attention-ring-fade') || null;
              return {
                found: !!root,
                wrapState: wrap ? Array.from(wrap.classList).find(name => name.startsWith('agent-window-activity--')) : '',
                statusOnly: wrap ? wrap.classList.contains('agent-window-activity--status-only') : false,
                hasIcon: !!icon,
                iconState: icon ? Array.from(icon.classList).find(name => name.startsWith('agent-window-agent-icon--')) : '',
                attention: dot ? dot.classList.contains('status-indicator--attention') : false,
                cooldown: dot ? dot.classList.contains('status-indicator--cooldown') : false,
                working: dot ? dot.classList.contains('status-indicator--working') : false,
                segmented: dot ? dot.classList.contains('agent-window-status-dot--segmented') : false,
                toneAttention: dot ? dot.classList.contains('agent-window-status-dot--tone-attention') : false,
                toneCooldown: dot ? dot.classList.contains('agent-window-status-dot--tone-cooldown') : false,
                toneWorking: dot ? dot.classList.contains('agent-window-status-dot--tone-working') : false,
                segmentClass: dot ? Array.from(dot.classList).find(name => /^agent-window-status-dot--(attention|cooldown|working)-/.test(name)) || '' : '',
                subwindowPulse: dot ? dot.classList.contains('agent-window-status-dot--subwindow-pulse') : false,
                hasActivity: !!wrap,
                backgroundImage: dotStyle?.backgroundImage || '',
                delay,
                delaySeconds: Number((delay.match(/-?[0-9.]+/) || [0])[0]),
                animationName: dotStyle?.animationName || '',
                opacity: dotStyle?.opacity || '',
                filter: dotStyle?.filter || '',
                animationDuration: dotStyle?.animationDuration || '',
                animationDelay: dotStyle?.animationDelay || '',
                animationTiming: dotStyle?.animationTimingFunction || '',
                beforeAnimationName: beforeStyle?.animationName || '',
                beforeAnimationDuration: beforeStyle?.animationDuration || '',
                beforeAnimationDelay: beforeStyle?.animationDelay || '',
                beforeAnimationTiming: beforeStyle?.animationTimingFunction || '',
                beforeFilter: beforeStyle?.filter || '',
                animationCurrentTime: Number(animation?.currentTime || 0),
              };
            };
            return {
              tab: read('.dockview-pane-tab[data-pane-tab="1"]'),
              claude: read('.tmux-window-button[data-window-index="0"]'),
              codex: read('.tmux-window-button[data-window-index="1"]'),
            };
            """
        )

    red_case = load_with([
        {"kind": "claude", "state": "needs-input", "window_index": 0, "window_label": "0:claude"},
        {"kind": "codex", "state": "idle", "window_index": 1, "window_label": "1:codex", "working_stopped_ts": stopped_ts},
    ])
    assert red_case["claude"]["attention"] is True, red_case
    assert red_case["codex"]["cooldown"] is True, red_case
    assert red_case["tab"]["attention"] is True, red_case
    assert red_case["tab"]["segmented"] is True, red_case
    assert red_case["tab"]["toneAttention"] is True, red_case
    assert red_case["tab"]["toneCooldown"] is True, red_case
    assert red_case["tab"]["toneWorking"] is False, red_case
    assert red_case["tab"]["segmentClass"] == "agent-window-status-dot--attention-cooldown", red_case
    assert "conic-gradient" in red_case["tab"]["backgroundImage"], red_case
    assert red_case["tab"]["statusOnly"] is True, red_case
    assert red_case["tab"]["hasIcon"] is False, red_case
    assert red_case["claude"]["animationName"] == "none", red_case
    assert red_case["codex"]["animationName"] == "none", red_case
    assert red_case["claude"]["beforeAnimationName"] == "agent-status-opacity-pulse", red_case
    assert red_case["codex"]["beforeAnimationName"] == "agent-status-opacity-pulse", red_case
    assert red_case["tab"]["delay"] == red_case["claude"]["delay"] == red_case["codex"]["delay"], red_case
    assert red_case["tab"]["animationDelay"] == red_case["claude"]["beforeAnimationDelay"] == red_case["codex"]["beforeAnimationDelay"], red_case

    tri_case = load_with([
        {"kind": "claude", "state": "needs-input", "window_index": 0, "window_label": "0:claude"},
        {"kind": "codex", "state": "idle", "window_index": 1, "window_label": "1:codex", "working_stopped_ts": stopped_ts},
        {"kind": "claude", "state": "working", "window_index": 2, "window_label": "2:claude"},
    ])
    assert tri_case["claude"]["attention"] is True, tri_case
    assert tri_case["codex"]["cooldown"] is True, tri_case
    assert tri_case["tab"]["attention"] is True, tri_case
    assert tri_case["tab"]["segmented"] is True, tri_case
    assert tri_case["tab"]["toneAttention"] is True, tri_case
    assert tri_case["tab"]["toneCooldown"] is True, tri_case
    assert tri_case["tab"]["toneWorking"] is True, tri_case
    assert tri_case["tab"]["segmentClass"] == "agent-window-status-dot--attention-cooldown-working", tri_case
    assert "conic-gradient" in tri_case["tab"]["backgroundImage"], tri_case

    acknowledged_case = load_with([
        {"kind": "claude", "state": "needs-input", "window_index": 0, "window_label": "0:claude", "attention_key": "ack-0", "attention_signature": "ack-0", "attention_acknowledged": True},
        {"kind": "codex", "state": "idle", "window_index": 1, "window_label": "1:codex", "working_stopped_ts": stopped_ts},
        {"kind": "claude", "state": "working", "window_index": 2, "window_label": "2:claude"},
    ])
    assert acknowledged_case["claude"]["hasActivity"] is False, acknowledged_case
    assert acknowledged_case["claude"]["attention"] is False, acknowledged_case
    assert acknowledged_case["tab"]["segmented"] is True, acknowledged_case
    assert acknowledged_case["tab"]["toneAttention"] is False, acknowledged_case
    assert acknowledged_case["tab"]["toneCooldown"] is True, acknowledged_case
    assert acknowledged_case["tab"]["toneWorking"] is True, acknowledged_case
    assert acknowledged_case["tab"]["segmentClass"] == "agent-window-status-dot--cooldown-working", acknowledged_case

    yellow_case = load_with([
        {"kind": "claude", "state": "working", "window_index": 0, "window_label": "0:claude"},
        {"kind": "codex", "state": "idle", "window_index": 1, "window_label": "1:codex", "working_stopped_ts": stopped_ts},
    ])
    assert yellow_case["claude"]["working"] is True, yellow_case
    assert yellow_case["codex"]["cooldown"] is True, yellow_case
    assert yellow_case["tab"]["cooldown"] is True, yellow_case
    assert yellow_case["tab"]["segmented"] is True, yellow_case
    assert yellow_case["tab"]["toneAttention"] is False, yellow_case
    assert yellow_case["tab"]["toneCooldown"] is True, yellow_case
    assert yellow_case["tab"]["toneWorking"] is True, yellow_case
    assert yellow_case["tab"]["segmentClass"] == "agent-window-status-dot--cooldown-working", yellow_case
    assert "conic-gradient" in yellow_case["tab"]["backgroundImage"], yellow_case
    assert "255, 214, 51" in yellow_case["tab"]["backgroundImage"], yellow_case
    assert "245, 197, 66" not in yellow_case["tab"]["backgroundImage"], yellow_case
    assert yellow_case["tab"]["statusOnly"] is True, yellow_case
    assert yellow_case["tab"]["hasIcon"] is False, yellow_case
    assert yellow_case["tab"]["animationName"] == "agent-status-opacity-pulse", yellow_case
    assert yellow_case["tab"]["filter"] == "none", yellow_case
    assert yellow_case["codex"]["animationName"] == "none", yellow_case
    assert yellow_case["codex"]["beforeAnimationName"] == "agent-status-opacity-pulse", yellow_case
    assert yellow_case["tab"]["animationDuration"] == yellow_case["codex"]["beforeAnimationDuration"], yellow_case
    assert yellow_case["tab"]["animationTiming"] == yellow_case["codex"]["beforeAnimationTiming"], yellow_case
    assert yellow_case["tab"]["delay"] == yellow_case["codex"]["delay"], yellow_case
    assert yellow_case["tab"]["animationDelay"] == yellow_case["codex"]["beforeAnimationDelay"], yellow_case


def test_dockview_terminal_info_bar_alignment_and_detail_toggle_refits_xterm(browser, tmp_path):
    transcript_sessions = {
        "1": {
            "agents": [{"kind": "claude", "transcript": True, "pane_target": "%2"}],
            "panes": [
                {"target": "%1", "window": 0, "window_name": "bash", "window_active": False, "active": True, "process_label": "bash"},
                {"target": "%2", "window": 1, "window_name": "claude", "window_active": True, "active": True, "process_label": "claude"},
                {"target": "%3", "window": 2, "window_name": "codex", "window_active": False, "active": True, "process_label": "codex"},
            ],
        },
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
        terminal_css=".terminal { width: 100%; height: 100%; } #term-1 .xterm { width: 100%; height: 100%; }",
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelectorAll('#panel-1 .panel-detail-row [data-window-index]').length === 3
              && document.querySelector('#term-1 .xterm')
              && document.querySelector('.dockview-pane-header-actions [data-detail-toggle="1"]');
            """
        )
    )
    before = browser.execute_script(
        """
        const rectFor = node => {
          const rect = node.getBoundingClientRect();
          return {top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right, width: rect.width, height: rect.height};
        };
        const panel = document.querySelector('#panel-1');
        const row = panel.querySelector('.panel-detail-row');
        const bar = row.querySelector('.tmux-window-bar');
        const close = row.querySelector('.panel-detail-close');
        const firstWindowButton = bar?.querySelector('.tmux-window-button');
        const headerTerminal = document.querySelector('.dockview-pane-header-actions .terminal-tab');
        const pane = panel.querySelector('#terminal-pane-1');
        const xterm = panel.querySelector('#term-1 .xterm');
        const term = terminals.get('1')?.term;
        if (term) {
          const originalResize = term.resize.bind(term);
          term.__detailToggleResizeCount = 0;
          term.__detailToggleRowsBefore = term.rows;
          term.resize = (cols, rows) => {
            term.__detailToggleResizeCount += 1;
            originalResize(cols, rows);
          };
        }
        return {
          row: rectFor(row),
          bar: rectFor(bar),
          closePresent: Boolean(close),
          firstWindowButtonRadius: firstWindowButton ? getComputedStyle(firstWindowButton).borderTopLeftRadius : '',
          headerTerminalText: headerTerminal?.textContent.trim() || '',
          headerTerminalTitle: headerTerminal?.getAttribute('title') || '',
          pane: rectFor(pane),
          xterm: rectFor(xterm),
          rowDisplay: getComputedStyle(row).display,
          rows: term?.rows || 0,
        };
        """
    )
    browser.find_element("css selector", '.dockview-pane-header-actions [data-detail-toggle="1"]').click()
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const panel = document.querySelector('#panel-1');
            return panel?.classList.contains('details-collapsed');
            """
        )
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const term = terminals.get('1')?.term;
            return (term?.__detailToggleResizeCount || 0) > 0;
            """
        )
    )
    after = browser.execute_script(
        """
        const rectFor = node => {
          const rect = node.getBoundingClientRect();
          return {top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right, width: rect.width, height: rect.height};
        };
        const panel = document.querySelector('#panel-1');
        const row = panel.querySelector('.panel-detail-row');
        const pane = panel.querySelector('#terminal-pane-1');
        const container = panel.querySelector('#term-1');
        const xterm = panel.querySelector('#term-1 .xterm');
        const headerToggle = document.querySelector('.dockview-pane-header-actions [data-detail-toggle="1"]');
        const term = terminals.get('1')?.term;
        return {
          panel: rectFor(panel),
          rowDisplay: getComputedStyle(row).display,
          headerPressed: headerToggle?.getAttribute('aria-pressed') || '',
          headerTitle: headerToggle?.getAttribute('title') || '',
          pane: rectFor(pane),
          paneActive: pane.classList.contains('active'),
          container: rectFor(container),
          containerClientWidth: container.clientWidth,
          containerClientHeight: container.clientHeight,
          xterm: rectFor(xterm),
          resizeCount: term?.__detailToggleResizeCount || 0,
          rowsBefore: term?.__detailToggleRowsBefore || 0,
          rowsAfter: term?.rows || 0,
        };
        """
    )
    assert before["headerTerminalText"] == "Term"
    assert before["headerTerminalTitle"] == "terminal: claude"
    assert before["closePresent"] is False
    assert abs(before["bar"]["left"] - before["row"]["left"]) <= 1
    assert before["firstWindowButtonRadius"] == "0px"
    assert before["rowDisplay"] == "flex"
    assert before["xterm"]["top"] >= before["row"]["bottom"] - 1
    assert after["rowDisplay"] == "none"
    assert after["headerPressed"] == "false"
    assert after["headerTitle"].lower() == "show info bar"
    assert after["resizeCount"] >= 1, after
    assert after["xterm"]["top"] >= after["panel"]["top"] - 1
    assert after["xterm"]["bottom"] <= after["pane"]["bottom"] + 1
    assert after["rowsAfter"] >= before["rows"]


def test_dockview_new_virtual_and_file_tabs_open_in_focused_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=row@50(left,slot1)&tabs=left:1;slot1:2",
        sessions=["1", "2"],
    )
    wait_for_dockview(browser, min_tabs=2)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          activatePaneTab('slot1', '2', {userInitiated: true});
          setFocusedPanelItem('2', {userInitiated: true});
          await selectSession(prefsItemId, {userInitiated: true});
          const prefsSlot = slotForItem(prefsItemId);
          activatePaneTab('slot1', '2', {userInitiated: true});
          setFocusedPanelItem('2', {userInitiated: true});
          await openInfoSubTab('yoagent');
          const yoagentSlot = slotForItem(yoagentItemId);
          activatePaneTab('slot1', '2', {userInitiated: true});
          setFocusedPanelItem('2', {userInitiated: true});
          const filePath = '/home/test/yolomux.dev/NEWTAB.md';
          const fileItem = await openFileInEditor(filePath, {name: 'NEWTAB.md'}, {userInitiated: true});
          done({
            prefsSlot,
            yoagentSlot,
            fileSlot: slotForItem(fileItem),
            slot1Tabs: paneTabs('slot1'),
            leftTabs: paneTabs('left'),
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    assert metrics.get("error") is None, metrics
    assert metrics["prefsSlot"] == "slot1", metrics
    assert metrics["yoagentSlot"] == "slot1", metrics
    assert metrics["fileSlot"] == "slot1", metrics
    assert "1" in metrics["leftTabs"], metrics
    assert "2" in metrics["slot1Tabs"], metrics


def test_dockview_pending_new_tmux_session_survives_stale_roster_socket_close(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        available_agents=["term"],
    )
    wait_for_dockview(browser, min_tabs=1)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
        const socketForSession = session => Array.from(window.__bootSocketInstances || []).reverse().find(socket => {
          try { return new URL(socket.url).searchParams.get('session') === session; }
          catch (error) { return false; }
        });
        const autoApproveCount = () => (window.__bootFetches || []).filter(fetch => fetch.path === '/api/auto-approve').length;
        const snapshot = () => ({
          tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
          active: document.querySelector('.dv-tab.dv-active-tab .dockview-pane-tab')?.dataset?.paneTab || '',
          slots: JSON.parse(JSON.stringify(layoutSlots)),
          pending: pendingTmuxSessionNames(),
          fetches: (window.__bootFetches || []).map(fetch => `${fetch.method} ${fetch.path}${fetch.search}`),
        });
        (async () => {
          window.__fixtureNextCreatedSession = '2';
          await createNextSession('term');
          await wait(0);
          const socket = socketForSession('2');
          const beforeAutoApprove = autoApproveCount();
          if (!socket) return done({error: 'new session socket not opened', ...snapshot()});
          socket.close();
          for (let index = 0; index < 50 && autoApproveCount() <= beforeAutoApprove; index += 1) await wait(10);
          await wait(0);
          done(snapshot());
        })().catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    assert metrics.get("error") is None, metrics
    assert "2" in metrics["tabs"], metrics
    assert metrics["active"] == "2", metrics
    assert metrics["slots"]["left"]["active"] == "2", metrics
    assert "2" in metrics["pending"], metrics


def test_dockview_new_xterm_survives_stale_transcripts_after_fresh_create_roster(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        available_agents=["term"],
    )
    wait_for_dockview(browser, min_tabs=1)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
        const snapshot = () => ({
          tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
          active: document.querySelector('.dv-tab.dv-active-tab .dockview-pane-tab')?.dataset?.paneTab || '',
          slots: JSON.parse(JSON.stringify(layoutSlots)),
          pending: pendingTmuxSessionNames(),
          inactive: inactiveTabItems(),
          fetches: (window.__bootFetches || []).map(fetch => `${fetch.method} ${fetch.path}${fetch.search}`),
        });
        (async () => {
          window.__fixtureNextCreatedSession = '2';
          window.__fixtureCreateSessionRoster = ['1', '2'];
          await createNextSession('term');
          for (let index = 0; index < 50; index += 1) {
            const fetches = window.__bootFetches || [];
            if (fetches.some(fetch => fetch.path === '/api/session-metadata')) break;
            await wait(10);
          }
          await wait(0);
          done(snapshot());
        })().catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    assert metrics.get("error") is None, metrics
    assert "2" in metrics["tabs"], metrics
    assert metrics["active"] == "2", metrics
    assert metrics["slots"]["left"]["active"] == "2", metrics
    assert "2" in metrics["pending"], metrics
    assert "2" not in metrics["inactive"], metrics


def test_dockview_tabs_menu_new_xterm_survives_fresh_roster_then_stale_socket_close(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        available_agents=["term"],
    )
    wait_for_dockview(browser, min_tabs=1)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
        const socketForSession = session => Array.from(window.__bootSocketInstances || []).reverse().find(socket => {
          try { return new URL(socket.url).searchParams.get('session') === session; }
          catch (error) { return false; }
        });
        const snapshot = () => ({
          tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
          active: document.querySelector('.dv-tab.dv-active-tab .dockview-pane-tab')?.dataset?.paneTab || '',
          slots: JSON.parse(JSON.stringify(layoutSlots)),
          pending: pendingTmuxSessionNames(),
          inactive: inactiveTabItems(),
          fetches: (window.__bootFetches || []).map(fetch => `${fetch.method} ${fetch.path}${fetch.search}`),
        });
        const clickAppMenuCommand = async (menuId, labelText) => {
          const wrapper = document.querySelector(`.app-menu[data-app-menu="${menuId}"]`);
          if (!wrapper) throw new Error(`missing menu ${menuId}`);
          wrapper.querySelector(':scope > .app-menu-button')?.click();
          await wait(0);
          const needle = labelText.toLowerCase();
          const command = Array.from(wrapper.querySelectorAll('.app-menu-command'))
            .find(button => button.textContent.replace(/\\s+/g, ' ').toLowerCase().includes(needle));
          if (!command) throw new Error(`missing command ${labelText}`);
          command.click();
        };
        (async () => {
          window.__fixtureNextCreatedSession = '2';
          window.__fixtureCreateSessionRoster = ['1', '2'];
          await clickAppMenuCommand('tabs', 'New Xterm');
          for (let index = 0; index < 50 && !paneTabs('left').includes('2'); index += 1) await wait(10);
          window.__fixtureSessions = ['1', '2'];
          await refreshTranscripts({force: true, refreshAuto: false});
          window.__fixtureAutoApprovePayload = {
            session_order: ['1'],
            sessions: {'1': {target: '1', enabled: false, last_action: 'off'}},
            rules: {path: '/home/test/.config/yolomux/yolo-rules.yaml', source: 'default', rules: [], errors: []},
          };
          const socket = socketForSession('2');
          if (!socket) return done({error: 'new Xterm socket not opened', ...snapshot()});
          socket.close();
          await wait(25);
          done(snapshot());
        })().catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    assert metrics.get("error") is None, metrics
    assert "2" in metrics["tabs"], metrics
    assert metrics["active"] == "2", metrics
    assert metrics["slots"]["left"]["active"] == "2", metrics
    assert "2" in metrics["pending"], metrics
    assert "2" not in metrics["inactive"], metrics


def test_dockview_pending_renamed_tmux_session_survives_stale_roster_socket_close(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=5,6&layout=left&tabs=left:5,6",
        sessions=["5", "6"],
        available_agents=["term"],
    )
    wait_for_dockview(browser, min_tabs=2)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
        const socketForSession = session => Array.from(window.__bootSocketInstances || []).reverse().find(socket => {
          try { return new URL(socket.url).searchParams.get('session') === session; }
          catch (error) { return false; }
        });
        const autoApproveCount = () => (window.__bootFetches || []).filter(fetch => fetch.path === '/api/auto-approve').length;
        const snapshot = () => ({
          tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
          active: document.querySelector('.dv-tab.dv-active-tab .dockview-pane-tab')?.dataset?.paneTab || '',
          slots: JSON.parse(JSON.stringify(layoutSlots)),
          pending: pendingTmuxSessionNames(),
          fetches: (window.__bootFetches || []).map(fetch => `${fetch.method} ${fetch.path}${fetch.search}`),
        });
        (async () => {
          await renameTmuxSession('5', '55');
          await wait(0);
          const socket = socketForSession('55');
          const beforeAutoApprove = autoApproveCount();
          if (!socket) return done({error: 'renamed session socket not opened', ...snapshot()});
          socket.close();
          for (let index = 0; index < 50 && autoApproveCount() <= beforeAutoApprove; index += 1) await wait(10);
          await wait(0);
          done(snapshot());
        })().catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    assert metrics.get("error") is None, metrics
    assert "55" in metrics["tabs"], metrics
    assert "5" not in metrics["tabs"], metrics
    assert metrics["active"] == "55", metrics
    assert metrics["slots"]["left"]["active"] == "55", metrics
    assert "55" in metrics["pending"], metrics


def test_dockview_rename_dialog_survives_fresh_roster_then_stale_socket_close(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=5,6&layout=left&tabs=left:5,6",
        sessions=["5", "6"],
        available_agents=["term"],
    )
    wait_for_dockview(browser, min_tabs=2)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
        const socketForSession = session => Array.from(window.__bootSocketInstances || []).reverse().find(socket => {
          try { return new URL(socket.url).searchParams.get('session') === session; }
          catch (error) { return false; }
        });
        const snapshot = () => ({
          tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
          active: document.querySelector('.dv-tab.dv-active-tab .dockview-pane-tab')?.dataset?.paneTab || '',
          slots: JSON.parse(JSON.stringify(layoutSlots)),
          pending: pendingTmuxSessionNames(),
          inactive: inactiveTabItems(),
          fetches: (window.__bootFetches || []).map(fetch => `${fetch.method} ${fetch.path}${fetch.search}`),
        });
        (async () => {
          renameTmuxSession('5');
          await wait(0);
          const input = document.querySelector('.session-rename-input');
          const form = document.querySelector('.session-rename-dialog');
          if (!input || !form) throw new Error('rename dialog did not open');
          input.value = '55';
          form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
          for (let index = 0; index < 50 && !paneTabs('left').includes('55'); index += 1) await wait(10);
          window.__fixtureSessions = ['6', '55'];
          await refreshTranscripts({force: true, refreshAuto: false});
          window.__fixtureAutoApprovePayload = {
            session_order: ['6'],
            sessions: {'6': {target: '6', enabled: false, last_action: 'off'}},
            rules: {path: '/home/test/.config/yolomux/yolo-rules.yaml', source: 'default', rules: [], errors: []},
          };
          const socket = socketForSession('55');
          if (!socket) return done({error: 'renamed session socket not opened', ...snapshot()});
          socket.close();
          await wait(25);
          done(snapshot());
        })().catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    assert metrics.get("error") is None, metrics
    assert "55" in metrics["tabs"], metrics
    assert "5" not in metrics["tabs"], metrics
    assert metrics["active"] == "55", metrics
    assert metrics["slots"]["left"]["active"] == "55", metrics
    assert "55" in metrics["pending"], metrics
    assert "55" not in metrics["inactive"], metrics


def test_differ_reopen_keeps_dragged_file_tab_home(browser, tmp_path):
    path = "/repo/app/src/main.py"
    item = f"filediff:{path}"
    session_files_payload = {
        "session": "1",
        "loaded": True,
        "errors": [],
        "refs_by_repo": {},
        "repos": [{"repo": "/repo/app"}],
        "files": [{"session": "1", "agent": "codex", "status": "M", "repo": "/repo/app", "path": "src/main.py", "abs_path": path, "mtime": 100, "added": 1, "removed": 1}],
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1,2&layout=row@24(left,row@50(slot1,slot2))&tabs=left:files;slot1:1;slot2:2",
        sessions=["1", "2"],
        session_files_payload=session_files_payload,
        grid_width=1300,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_visible_selector(browser, '.dockview-pane-tab[data-pane-tab="1"]')
    wait_for_visible_selector(browser, '.dockview-pane-tab[data-pane-tab="2"]')
    opened = browser.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        const originalFetch = window.fetch.bind(window);
        window.__doit65Fetches = [];
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), 'https://localhost');
          window.__doit65Fetches.push(url.pathname + url.search);
          if (url.pathname === '/api/fs/read') {
            return new Response(JSON.stringify({
              path: url.searchParams.get('path') || path,
              content: 'print("hello")\\n',
              size: 15,
              mtime: 1,
              mtime_ns: 1,
              realpath: path,
              file_id: 'dev:10:ino:20',
              git_root: '/repo/app',
              git_tracked: true,
              git_history: [{ref: 'a'}, {ref: 'b'}],
              git_has_history: true,
            }), {status: 200, headers: {'Content-Type': 'application/json'}});
          }
          if (url.pathname === '/api/fs/diff') {
            return new Response(JSON.stringify({
              repo: '/repo/app',
              relative_path: 'src/main.py',
              diff: '@@ -1 +1 @@\\n-print("old")\\n+print("hello")\\n',
              original: 'print("old")\\n',
              working: 'print("hello")\\n',
            }), {status: 200, headers: {'Content-Type': 'application/json'}});
          }
          return originalFetch(input, options);
        };
        const waitFor = predicate => new Promise((resolve, reject) => {
          let attempts = 0;
          const tick = () => {
            try {
              const value = predicate();
              if (value) { resolve(value); return; }
            } catch (error) {
              reject(error);
              return;
            }
            attempts += 1;
            if (attempts > 120) {
              reject(new Error('timed out waiting for Differ row/opened tab'));
              return;
            }
            requestAnimationFrame(tick);
          };
          tick();
        });
        (async () => {
          setFileExplorerMode('diff', {force: true});
          renderFileExplorerChangesPanels({force: true});
          const row = await waitFor(() => document.querySelector(`[data-open-change-file="${path}"]`));
          row.click();
          await waitFor(() => slotForItem(`filediff:${path}`) === 'slot1');
          done({
            slot: slotForItem(`filediff:${path}`),
            mode: editorViewModeFor(path, `filediff:${path}`),
            rows: document.querySelectorAll(`[data-open-change-file="${path}"]`).length,
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """,
        path,
    )
    assert opened.get("error") is None, opened
    assert opened["slot"] == "slot1", opened
    assert opened["mode"] == "diff", opened
    assert opened["rows"] == 1, opened

    points = browser.execute_script(
        """
        const rectPoint = (rect, x = 0.5, y = 0.5) => ({x: Math.round(rect.left + rect.width * x), y: Math.round(rect.top + rect.height * y)});
        const tab = document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(arguments[0])}"]`);
        const targetGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        return {
          start: rectPoint(tab.closest('.dv-tab').getBoundingClientRect()),
          end: rectPoint(targetGroup.getBoundingClientRect(), 0.55, 0.5),
        };
        """,
        item,
    )
    cdp_drag(browser, points["start"], points["end"], steps=28)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return slotForItem(arguments[0])", item) == "slot2"
    )

    reopened = browser.execute_async_script(
        """
        const path = arguments[0];
        const item = `filediff:${path}`;
        const done = arguments[arguments.length - 1];
        const waitFor = predicate => new Promise((resolve, reject) => {
          let attempts = 0;
          const tick = () => {
            try {
              const value = predicate();
              if (value) { resolve(value); return; }
            } catch (error) {
              reject(error);
              return;
            }
            attempts += 1;
            if (attempts > 120) {
              reject(new Error('timed out waiting for Differ reopen'));
              return;
            }
            requestAnimationFrame(tick);
          };
          tick();
        });
        (async () => {
          setFileEditorViewMode(path, 'edit', item);
          renderOpenFilePath(path);
          document.querySelector(`[data-open-change-file="${path}"]`).click();
          await waitFor(() => slotForItem(item) === 'slot2' && activeItemForSide('slot2') === item && editorViewModeFor(path, item) === 'diff');
          done({
            slot: slotForItem(item),
            mode: editorViewModeFor(path, item),
            slot2Tabs: paneTabs('slot2'),
            leftTabs: paneTabs('left'),
            slot1Tabs: paneTabs('slot1'),
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """,
        path,
    )
    assert reopened.get("error") is None, reopened
    assert reopened["slot"] == "slot2", reopened
    assert reopened["mode"] == "diff", reopened
    assert item in reopened["slot2Tabs"], reopened
    assert item not in reopened["slot1Tabs"], reopened
    assert item not in reopened["leftTabs"], reopened


def test_dockview_symlink_alias_focuses_existing_file_editor(browser, tmp_path):
    real_path = "/repo/app/src/main.py"
    link_path = "/repo/app/link-main.py"
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
    )
    wait_for_dockview(browser, min_tabs=1)
    metrics = browser.execute_async_script(
        """
        const realPath = arguments[0];
        const linkPath = arguments[1];
        const done = arguments[arguments.length - 1];
        const originalFetch = window.fetch.bind(window);
        window.__doit65Fetches = [];
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), 'https://localhost');
          window.__doit65Fetches.push(url.pathname + url.search);
          if (url.pathname === '/api/fs/read') {
            return new Response(JSON.stringify({
              path: url.searchParams.get('path') || realPath,
              content: 'print("hello")\\n',
              size: 15,
              mtime: 1,
              mtime_ns: 1,
              realpath: realPath,
              file_id: 'dev:10:ino:20',
              git_root: '/repo/app',
              git_tracked: true,
              git_history: [{ref: 'a'}, {ref: 'b'}],
              git_has_history: true,
            }), {status: 200, headers: {'Content-Type': 'application/json'}});
          }
          if (url.pathname === '/api/fs/diff') {
            return new Response(JSON.stringify({
              repo: '/repo/app',
              relative_path: 'src/main.py',
              diff: '@@ -1 +1 @@\\n-print("old")\\n+print("hello")\\n',
              original: 'print("old")\\n',
              working: 'print("hello")\\n',
            }), {status: 200, headers: {'Content-Type': 'application/json'}});
          }
          return originalFetch(input, options);
        };
        (async () => {
          const first = await openFileInEditor(realPath, {name: 'main.py', realpath: realPath, file_id: 'dev:10:ino:20'}, {viewMode: 'edit'});
          const state = fileStateFor(realPath);
          state.content = 'dirty edit\\n';
          state.dirty = true;
          await openChangedFileInDiff(linkPath, '1', 'M', '/repo/app', {forceNewTab: true, userInitiated: true, openMode: 'diff'});
          const afterDiffActionItems = openFileEditorItems();
          const second = await openFileInAdditionalEditorTab(linkPath, {name: 'link-main.py', realpath: realPath, file_id: 'dev:10:ino:20'}, {viewMode: 'diff'});
          done({
            first,
            second,
            afterDiffActionItems,
            openItems: openFileEditorItems(),
            realItems: filePanelItemsForPath(realPath),
            linkItems: filePanelItemsForPath(linkPath),
            content: fileStateFor(realPath)?.content || '',
            dirty: fileStateFor(realPath)?.dirty === true,
            mode: editorViewModeFor(realPath, first),
            tabCount: Array.from(document.querySelectorAll('.dockview-pane-tab')).filter(tab => String(tab.dataset.paneTab || '').includes('main.py')).length,
            readCalls: window.__doit65Fetches.filter(url => url.startsWith('/api/fs/read')).length,
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """,
        real_path,
        link_path,
    )
    assert metrics.get("error") is None, metrics
    assert metrics["second"] == metrics["first"], metrics
    assert metrics["afterDiffActionItems"] == [metrics["first"]], metrics
    assert metrics["openItems"] == [metrics["first"]], metrics
    assert metrics["realItems"] == [metrics["first"]], metrics
    assert metrics["linkItems"] == [], metrics
    assert metrics["content"] == "dirty edit\n", metrics
    assert metrics["dirty"] is True, metrics
    assert metrics["mode"] == "diff", metrics
    assert metrics["tabCount"] == 1, metrics
    assert metrics["readCalls"] == 2, metrics


def test_dockview_new_tabs_do_not_open_in_focused_finder(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@22(left,slot1)&tabs=left:files;slot1:1",
        sessions=["1"],
    )
    wait_for_dockview(browser, min_tabs=2)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          activatePaneTab('left', fileExplorerItemId, {userInitiated: true});
          setFocusedPanelItem(fileExplorerItemId, {userInitiated: true});
          await selectSession(prefsItemId, {userInitiated: true});
          done({
            prefsSlot: slotForItem(prefsItemId),
            finderSlot: slotForItem(fileExplorerItemId),
            leftTabs: paneTabs('left'),
            slot1Tabs: paneTabs('slot1'),
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    assert metrics.get("error") is None, metrics
    assert metrics["finderSlot"] == "left", metrics
    assert metrics["prefsSlot"] == "slot1", metrics
    assert "__prefs__" not in metrics["leftTabs"], metrics


def test_dockview_many_tabs_wrap_above_content(browser, tmp_path):
    sessions = [str(index) for index in range(1, 10)]
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3,4,5,6,7,8,9&layout=left&tabs=left:1,2,3,4,5,6,7,8,9",
        sessions=sessions,
    )
    wait_for_dockview(browser, min_tabs=9)
    wait_for_dockview_tab_geometry(browser, min_tabs=9, min_width=150, min_rows=2)
    metrics = dockview_layout_metrics(browser)
    tab_tops = sorted({item["rect"]["top"] for item in metrics["tabStyles"]})
    tab_widths = {item["rect"]["width"] for item in metrics["tabStyles"]}
    tab_height = metrics["tabStyles"][0]["rect"]["height"]
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["groups"][0]["tabs"] == sessions
    assert len(tab_tops) >= 2
    assert max(tab_widths) <= 181
    assert min(tab_widths) >= 170
    assert metrics["header"]["height"] >= (tab_height * 2) - 2
    assert metrics["header"]["tabsOverflowX"] == "visible"
    assert metrics["header"]["tabsScrollWidth"] <= metrics["header"]["tabsClientWidth"] + 1
    assert metrics["header"]["tabsScrollbarWidth"] == "none"
    assert metrics["header"]["tabsWebkitScrollbarDisplay"] == "none"
    assert metrics["header"]["allTabsInsideHeader"] is True


def test_dockview_file_editor_tabs_stay_above_toolbar(browser, tmp_path):
    encoded_files = [
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fmissing%20dynamo.rs",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2FCargo.toml",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fstatic%2Fyolomux.css",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fstatic_src%2Fjs%2Fyolomux%2F60_popovers_tabs.js",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2FREADME.md",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fdocs%2Fspecs%2FGUI.md",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fdocs%2FDEVELOPMENT.md",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Ftests%2Ftest_browser_layout.py",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fstatic_src%2Fcss%2Fyolomux%2F40_layout_panes_tabs.css",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fyolomux_lib%2Fsettings.py",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fpyproject.toml",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fpytest.ini",
    ]
    token = ",".join(encoded_files)
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        f"?sessions={token}&layout=left&tabs=left:{token}",
        sessions=[],
        grid_width=1120,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=len(encoded_files))
    wait_for_dockview_tab_geometry(browser, min_tabs=len(encoded_files), min_width=150, min_rows=2)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return Boolean(document.querySelector('.file-editor-toolbar:not([hidden])'))")
    )
    metrics = browser.execute_script(
        """
        const group = document.querySelector('.file-editor-panel').closest('.dv-groupview');
        const tabsContainer = group.querySelector('.dv-tabs-container');
        const actions = group.querySelector('.dockview-pane-header-actions');
        const rectFor = rect => ({
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          top: Math.round(rect.top),
          bottom: Math.round(rect.bottom),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        });
        const header = group.querySelector('.dv-tabs-and-actions-container').getBoundingClientRect();
        const toolbar = group.querySelector('.file-editor-toolbar').getBoundingClientRect();
        const tabs = Array.from(group.querySelectorAll('.dockview-pane-tab')).map(tab => rectFor(tab.getBoundingClientRect()));
        const rowsByTop = new Map();
        for (const rect of tabs) {
          const top = String(rect.top);
          if (!rowsByTop.has(top)) rowsByTop.set(top, []);
          rowsByTop.get(top).push(rect);
        }
        const rows = Array.from(rowsByTop.entries())
          .map(([top, rects]) => ({top: Number(top), rects, right: Math.max(...rects.map(rect => rect.right))}))
          .sort((a, b) => a.top - b.top);
        const firstRowRight = rows[0]?.right || 0;
        const laterRowRight = Math.max(...rows.slice(1).map(row => row.right));
        const actionsRect = actions.getBoundingClientRect();
        const tabsOverlapActions = tabs.filter(rect => (
          rect.left < actionsRect.right - 1
          && rect.right > actionsRect.left + 1
          && rect.top < actionsRect.bottom - 1
          && rect.bottom > actionsRect.top + 1
        ));
        const lastRowLastTab = rows.at(-1)?.rects.at(-1);
        const lastRowHit = lastRowLastTab
          ? document.elementFromPoint(
              Math.round(lastRowLastTab.left + lastRowLastTab.width / 2),
              Math.round(lastRowLastTab.top + lastRowLastTab.height / 2),
            )
          : null;
        const activeTab = group.querySelector('.dv-tab.dv-active-tab .dockview-pane-tab');
        const activeRect = activeTab.getBoundingClientRect();
        const hit = document.elementFromPoint(Math.round(activeRect.left + activeRect.width / 2), Math.round(activeRect.top + activeRect.height / 2));
        return {
          header: rectFor(header),
          toolbar: rectFor(toolbar),
          tabRows: new Set(tabs.map(rect => rect.top)).size,
          tabHeight: tabs[0]?.height || 0,
          tabsOverflowX: getComputedStyle(tabsContainer).overflowX,
          tabsScrollWidth: Math.round(tabsContainer.scrollWidth),
          tabsClientWidth: Math.round(tabsContainer.clientWidth),
          actionLeft: Math.round(actionsRect.left),
          firstRowRight,
          laterRowRight,
          tabsOverlapActions,
          tabsOverlapToolbar: tabs.filter(rect => rect.bottom > toolbar.top + 1),
          lastRowTabClickable: Boolean(lastRowHit?.closest?.('.dockview-pane-tab')),
          activeTab: rectFor(activeRect),
          activeTabClickable: Boolean(hit?.closest?.('.dockview-pane-tab') === activeTab),
        };
        """
    )
    assert metrics["tabRows"] >= 2, metrics
    assert metrics["tabsOverflowX"] == "visible", metrics
    assert metrics["tabsScrollWidth"] <= metrics["tabsClientWidth"] + 1, metrics
    assert metrics["firstRowRight"] <= metrics["actionLeft"] - 1, metrics
    assert metrics["laterRowRight"] > metrics["actionLeft"] + 1, metrics
    assert metrics["tabsOverlapActions"] == [], metrics
    assert metrics["tabsOverlapToolbar"] == [], metrics
    assert metrics["lastRowTabClickable"] is True, metrics
    assert metrics["header"]["height"] >= (metrics["tabHeight"] * 2) - 2, metrics
    assert metrics["activeTab"]["bottom"] <= metrics["toolbar"]["top"] + 1, metrics
    assert metrics["header"]["bottom"] <= metrics["toolbar"]["top"] + 1, metrics
    assert metrics["activeTabClickable"] is True, metrics


def test_dockview_drag_reorders_tabs_in_same_pane(browser, tmp_path):
    # REAL end-to-end reorder: the drag itself must produce the new order. (The previous version of this
    # test force-wrote the expected layout via execute_script after the drag, which let the actual bug —
    # the no-op veto hit-testing the smooth-reorder dragged tab under the cursor and silently swallowing
    # every slow same-strip reorder — live in production undetected.)
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=left&tabs=left:1,2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.68, 0.5)
    cdp_drag(browser, start, end)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return paneTabs('left', layoutSlots).slice(0, 2).join(',');") == "2,1"
    )
    metrics = dockview_layout_metrics(browser)
    assert metrics["groups"][0]["tabs"] == ["2", "1", "3"]
    assert metrics["slots"]["left"]["tabs"] == ["2", "1", "3"]
    assert "tabs=left:2,1" in metrics["url"]


def test_dockview_drag_reorders_two_tab_pane(browser, tmp_path):
    # Two-tab strip is the tightest case: the dragged tab covers the drop point (smooth reorder), and the
    # pinned pointer fallback used to mirror-swap it back. Both must stay fixed.
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:1,2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.68, 0.5)
    cdp_drag(browser, start, end)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return paneTabs('left', layoutSlots).join(',');") == "2,1"
    )
    assert dockview_layout_metrics(browser)["groups"][0]["tabs"] == ["2", "1"]


def test_dockview_drag_reorders_two_pinned_tabs(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:1,2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    browser.execute_script("setTabPinned('1', true); setTabPinned('2', true);")
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.68, 0.5)
    cdp_drag(browser, start, end)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return paneTabs('left', layoutSlots).join(',');") == "2,1"
    )
    assert dockview_layout_metrics(browser)["groups"][0]["tabs"] == ["2", "1"]


def test_dockview_pinned_tabs_render_first_after_pin_toggle(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=left&tabs=left:1,2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    browser.execute_script("setTabPinned('2', true);")
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["groups"][0]["tabs"][0] == "2"
    )
    metrics = dockview_layout_metrics(browser)
    assert metrics["groups"][0]["tabs"] == ["2", "1", "3"], metrics
    assert metrics["slots"]["left"]["tabs"] == ["2", "1", "3"], metrics
    pinned = browser.execute_script(
        """
        const first = document.querySelector('.dv-groupview .dockview-pane-tab');
        return {
          item: first?.dataset?.paneTab || '',
          pinned: first?.classList?.contains('pinned-tab') || false,
          hasIcon: Boolean(first?.querySelector('.pane-tab-pin-icon')),
        };
        """
    )
    assert pinned == {"item": "2", "pinned": True, "hasIcon": True}


def test_dockview_first_pinned_tab_drags_after_second_pinned_tab(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=left&tabs=left:1,2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    browser.execute_script("setTabPinned('1', true); setTabPinned('2', true);")
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["groups"][0]["tabs"][:2] == ["1", "2"]
    )
    points = browser.execute_script(
        """
        const point = (selector, xRatio) => {
          const rect = document.querySelector(selector).getBoundingClientRect();
          return {x: Math.round(rect.left + rect.width * xRatio), y: Math.round(rect.top + rect.height / 2)};
        };
        return {
          start: point('.dockview-pane-tab[data-pane-tab="1"]', 0.5),
          end: point('.dockview-pane-tab[data-pane-tab="2"]', 0.35),
        };
        """
    )
    cdp_drag(browser, points["start"], points["end"])
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["groups"][0]["tabs"][:2] == ["2", "1"]
    )
    metrics = dockview_layout_metrics(browser)
    assert metrics["groups"][0]["tabs"] == ["2", "1", "3"], metrics
    assert metrics["slots"]["left"]["tabs"] == ["2", "1", "3"], metrics


def test_dockview_non_pinned_tab_cannot_drop_between_pinned_tabs(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=left&tabs=left:1,2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    browser.execute_script("setTabPinned('1', true); setTabPinned('2', true);")
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["groups"][0]["tabs"] == ["1", "2", "3"]
    )
    points = browser.execute_script(
        """
        const point = (selector, xRatio) => {
          const rect = document.querySelector(selector).getBoundingClientRect();
          return {x: Math.round(rect.left + rect.width * xRatio), y: Math.round(rect.top + rect.height / 2)};
        };
        return {
          start: point('.dockview-pane-tab[data-pane-tab="3"]', 0.5),
          end: point('.dockview-pane-tab[data-pane-tab="2"]', 0.35),
        };
        """
    )
    try:
        cdp_drag_hold(browser, points["start"], points["end"], steps=32)
        browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            requestAnimationFrame(() => requestAnimationFrame(done));
            """
        )
        preview = dockview_invalid_drop_preview(browser)
    finally:
        cdp_release(browser, points["end"])
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(done));
        """
    )
    metrics = dockview_layout_metrics(browser)
    assert preview["invalidPreview"] is True, preview
    assert any(item["borderStyle"] == "dashed" and item["borderColor"] == preview["dangerColor"] for item in preview["previews"]), preview
    assert metrics["groups"][0]["tabs"] == ["1", "2", "3"], metrics
    assert metrics["slots"]["left"]["tabs"] == ["1", "2", "3"], metrics
    assert browser.execute_script("return document.querySelector('.yolomux-dockview')?.classList.contains('dockview-invalid-tab-drop-preview') || false") is False


def test_dockview_pinned_tab_cannot_move_to_other_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=row@50(left,right)&tabs=left:1;right:2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    browser.execute_script("setTabPinned('1', true);")
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["slots"]["left"]["tabs"] == ["1"]
    )
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.72, 0.5)
    try:
        cdp_drag_hold(browser, start, end, steps=32)
        WebDriverWait(browser, 2).until(
            lambda driver: dockview_invalid_drop_preview(driver)["invalidPreview"] is True
        )
        preview = dockview_invalid_drop_preview(browser)
    finally:
        cdp_release(browser, end)
    assert preview["invalidPreview"] is True, preview
    assert any(item["borderStyle"] == "dashed" and item["borderColor"] == preview["dangerColor"] for item in preview["previews"]), preview
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["slots"]["left"]["tabs"] == ["1"]
    )
    metrics = dockview_layout_metrics(browser)
    assert metrics["slots"]["left"]["tabs"] == ["1"], metrics
    assert metrics["slots"]["right"]["tabs"] == ["2", "3"], metrics
    assert any(group["tabs"] == ["1"] for group in metrics["groups"]), metrics
    assert browser.execute_script("return document.querySelector('.yolomux-dockview')?.classList.contains('dockview-invalid-tab-drop-preview') || false") is False


def test_dockview_pinned_tab_cannot_split_to_new_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:1,2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    browser.execute_script("setTabPinned('2', true);")
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["slots"]["left"]["tabs"] == ["2", "1"]
    )
    content_attempt = browser.execute_script(
        """
        const tab = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]');
        const group = tab.closest('.dv-groupview');
        const slot = dockviewSlotForGroupElement(group);
        const rect = group.getBoundingClientRect();
        const event = {
          kind: 'content',
          position: 'right',
          group: {id: slot},
          nativeEvent: {clientX: Math.round(rect.right - 8), clientY: Math.round(rect.top + rect.height / 2)},
          getData() { return {panelId: '2', groupId: slot}; },
          preventDefault() { this.prevented = true; },
        };
        const intent = dockviewPaneContentDropIntent(event);
        dockviewTrackRootBoundaryOverlay(event);
        return {
          intent: intent ? {zone: intent.zone, targetSlot: intent.targetSlot} : null,
          prevented: event.prevented === true,
          rootPreview: document.querySelector('#grid').classList.contains('drop-preview-root'),
        };
        """
    )
    assert content_attempt["intent"] is None, content_attempt
    assert content_attempt["prevented"] is True, content_attempt
    assert content_attempt["rootPreview"] is False, content_attempt
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.5, 0.5)
    end = dockview_point(browser, "#dockviewRoot", 0.97, 0.5)
    try:
        cdp_drag_hold(browser, start, end, steps=32)
        browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            requestAnimationFrame(() => requestAnimationFrame(done));
            """
        )
        preview = browser.execute_script(
            """
            return {
              rootPreview: document.querySelector('#grid')?.classList.contains('drop-preview-root') || false,
              invalidPreview: document.querySelector('.yolomux-dockview')?.classList.contains('dockview-invalid-tab-drop-preview') || false,
            };
            """
        )
    finally:
        cdp_release(browser, end)
    assert preview["rootPreview"] is False, preview
    assert preview["invalidPreview"] is False, preview
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(done));
        """
    )
    metrics = dockview_layout_metrics(browser)
    assert metrics["slots"]["left"]["tabs"] == ["2", "1"], metrics
    assert len([group for group in metrics["groups"] if group["tabs"]]) == 1, metrics
    assert any(group["tabs"] == ["2", "1"] for group in metrics["groups"]), metrics


def test_dockview_pinned_tab_invalid_non_pinned_target_shows_red_dashes(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=row@50(left,right)&tabs=left:1;right:2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    browser.execute_script("setTabPinned('1', true); setTabPinned('2', true);")
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["slots"]["right"]["tabs"] == ["2", "3"]
    )
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.72, 0.5)
    try:
        cdp_drag_hold(browser, start, end, steps=32)
        WebDriverWait(browser, 2).until(
            lambda driver: dockview_invalid_drop_preview(driver)["invalidPreview"] is True
        )
        preview = dockview_invalid_drop_preview(browser)
    finally:
        cdp_release(browser, end)
    assert preview["invalidPreview"] is True, preview
    assert any(item["borderStyle"] == "dashed" and item["borderColor"] == preview["dangerColor"] for item in preview["previews"]), preview
    WebDriverWait(browser, 2).until(
        lambda driver: dockview_layout_metrics(driver)["slots"]["left"]["tabs"] == ["1"]
    )
    metrics = dockview_layout_metrics(browser)
    assert metrics["slots"]["left"]["tabs"] == ["1"], metrics
    assert metrics["slots"]["right"]["tabs"] == ["2", "3"], metrics
    assert browser.execute_script("return document.querySelector('.yolomux-dockview')?.classList.contains('dockview-invalid-tab-drop-preview') || false") is False


def test_dockview_pane_drag_handle_swaps_whole_panes(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3&layout=row@50(left,right)&tabs=left:1,2;right:3",
        sessions=["1", "2", "3"],
        grid_width=1200,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    before = dockview_layout_metrics(browser)
    assert [group["tabs"] for group in sorted(before["groups"], key=lambda item: item["rect"]["left"])] == [["1", "2"], ["3"]], before
    points = browser.execute_script(
        """
        const rectPoint = (rect, x = 0.5, y = 0.5) => ({x: Math.round(rect.left + rect.width * x), y: Math.round(rect.top + rect.height * y)});
        const sourceGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const targetGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="3"]').closest('.dv-groupview');
        const handle = sourceGroup.querySelector('.pane-drag-handle');
        return {
          start: rectPoint(handle.getBoundingClientRect()),
          end: rectPoint(targetGroup.getBoundingClientRect()),
          handleSlot: handle.dataset.paneDrag || '',
          canSwap: paneSwapAllowed(dockviewSlotForGroupElement(sourceGroup), dockviewSlotForGroupElement(targetGroup)),
        };
        """
    )
    assert points["handleSlot"] == "left"
    assert points["canSwap"] is True
    cdp_drag(browser, points["start"], points["end"], steps=28)
    WebDriverWait(browser, 5).until(
        lambda driver: [group["tabs"] for group in sorted(dockview_layout_metrics(driver)["groups"], key=lambda item: item["rect"]["left"])] == [["3"], ["1", "2"]]
    )
    after = dockview_layout_metrics(browser)
    assert after["slots"]["left"]["tabs"] == ["3"], after
    assert after["slots"]["right"]["tabs"] == ["1", "2"], after
    assert after["slots"]["__tree"]["split"] == "row", after
    assert round(after["slots"]["__tree"]["pct"]) == 50, after


def test_dockview_pane_drag_shows_dotted_pane_preview(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2",
        sessions=["1", "2"],
        grid_width=1200,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    points = browser.execute_script(
        """
        const rectPoint = (rect, x = 0.5, y = 0.5) => ({x: Math.round(rect.left + rect.width * x), y: Math.round(rect.top + rect.height * y)});
        const sourceGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const targetGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const header = sourceGroup.querySelector('.dv-tabs-and-actions-container');
        const tabs = sourceGroup.querySelector('.dv-tabs-container').getBoundingClientRect();
        const tab = sourceGroup.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-tab').getBoundingClientRect();
        const headerRect = header.getBoundingClientRect();
        const x = Math.round(Math.min(headerRect.right - 84, Math.max(tab.right + 36, tabs.left + 230)));
        const y = Math.round(headerRect.top + Math.min(12, headerRect.height / 2));
        return {
          start: {x, y},
          end: rectPoint(targetGroup.getBoundingClientRect(), 0.55, 0.5),
        };
        """
    )
    try:
        cdp_drag_hold(browser, points["start"], points["end"], steps=28)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script("return Boolean(document.querySelector('.pane-drag-image.drag-image'))")
        )
        metrics = browser.execute_script(
            """
            const ghost = document.querySelector('.pane-drag-image.drag-image');
            const style = getComputedStyle(ghost);
            const rect = ghost.getBoundingClientRect();
            return {
              slot: ghost.dataset.dragSlot || '',
              borderStyle: style.borderTopStyle,
              borderColor: style.borderTopColor,
              separatorColor: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
              text: ghost.textContent,
            };
            """
        )
        assert metrics["slot"] == "left", metrics
        assert metrics["borderStyle"] == "dotted", metrics
        assert metrics["borderColor"] == metrics["separatorColor"], metrics
        assert metrics["width"] >= 180 and metrics["height"] >= 120, metrics
        assert "1 tab" in metrics["text"], metrics
    finally:
        cdp_release(browser, points["end"])
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return !document.querySelector('.pane-drag-image.drag-image')")
    )


def test_dockview_panel_detail_row_drags_whole_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2",
        sessions=["1", "2"],
        grid_width=1200,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    points = browser.execute_script(
        """
        const rectPoint = (rect, x = 0.5, y = 0.5) => ({x: Math.round(rect.left + rect.width * x), y: Math.round(rect.top + rect.height * y)});
        const sourceGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const targetGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const detail = sourceGroup.querySelector('.panel-detail-row');
        const detailRect = detail.getBoundingClientRect();
        const start = {x: Math.round(detailRect.left + Math.min(220, detailRect.width * 0.45)), y: Math.round(detailRect.top + detailRect.height / 2)};
        const hit = document.elementFromPoint(start.x, start.y);
        return {
          start,
          end: rectPoint(targetGroup.getBoundingClientRect(), 0.55, 0.5),
          detailDragSlot: detail.dataset.paneDragSlot || '',
          hitExcluded: Boolean(hit?.closest?.('button, input, textarea, select, a')),
        };
        """
    )
    assert points["detailDragSlot"] == "left", points
    assert points["hitExcluded"] is False, points
    try:
        cdp_drag_hold(browser, points["start"], points["end"], steps=28)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script("return Boolean(document.querySelector('.pane-drag-image.drag-image'))")
        )
        preview = browser.execute_script(
            """
            const ghost = document.querySelector('.pane-drag-image.drag-image');
            const style = getComputedStyle(ghost);
            return {
              slot: ghost.dataset.dragSlot || '',
              borderStyle: style.borderTopStyle,
              text: ghost.textContent.trim().replace(/\\s+/g, ' '),
            };
            """
        )
        assert preview["slot"] == "left", preview
        assert preview["borderStyle"] == "dotted", preview
        assert "1 tab" in preview["text"], preview
    finally:
        cdp_release(browser, points["end"])
    WebDriverWait(browser, 5).until(
        lambda driver: [group["tabs"] for group in sorted(dockview_layout_metrics(driver)["groups"], key=lambda item: item["rect"]["left"])] == [["2"], ["1"]]
    )


def test_dockview_file_editor_toolbar_drags_whole_pane(browser, tmp_path):
    encoded_file = "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2FDONE.md"
    file_item = "file:/home/test/yolomux.dev/DONE.md"
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        f"?sessions=2,{encoded_file}&layout=row@50(left,right)&tabs=left:{encoded_file};right:2",
        sessions=["2"],
        grid_width=1200,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2, min_width=60)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return Boolean(document.querySelector('.file-editor-toolbar:not([hidden])'))")
    )
    points = browser.execute_script(
        """
        const rectPoint = (rect, x = 0.5, y = 0.5) => ({x: Math.round(rect.left + rect.width * x), y: Math.round(rect.top + rect.height * y)});
        const sourceGroup = document.querySelector('.file-editor-panel').closest('.dv-groupview');
        const targetGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const toolbar = sourceGroup.querySelector('.file-editor-toolbar');
        const toolbarRect = toolbar.getBoundingClientRect();
        const excluded = hit => Boolean(hit?.closest?.('button, input, textarea, select, a, [data-diff-ref-input]'));
        const candidates = [0.28, 0.38, 0.48, 0.58, 0.68, 0.78].map(ratio => ({
          x: Math.round(toolbarRect.left + toolbarRect.width * ratio),
          y: Math.round(toolbarRect.top + toolbarRect.height / 2),
        }));
        const start = candidates.find(point => !excluded(document.elementFromPoint(point.x, point.y))) || candidates[0];
        const hit = document.elementFromPoint(start.x, start.y);
        return {
          start,
          end: rectPoint(targetGroup.getBoundingClientRect(), 0.55, 0.5),
          toolbarDragSlot: toolbar.dataset.paneDragSlot || '',
          hitExcluded: excluded(hit),
          hitClass: String(hit?.className || ''),
        };
        """
    )
    assert points["toolbarDragSlot"] == "left", points
    assert points["hitExcluded"] is False, points
    try:
        cdp_drag_hold(browser, points["start"], points["end"], steps=28)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script("return Boolean(document.querySelector('.pane-drag-image.drag-image'))")
        )
        preview = browser.execute_script(
            """
            const ghost = document.querySelector('.pane-drag-image.drag-image');
            const style = getComputedStyle(ghost);
            return {
              slot: ghost.dataset.dragSlot || '',
              borderStyle: style.borderTopStyle,
              text: ghost.textContent.trim().replace(/\\s+/g, ' '),
            };
            """
        )
        assert preview["slot"] == "left", preview
        assert preview["borderStyle"] == "dotted", preview
        assert "1 tab" in preview["text"], preview
    finally:
        cdp_release(browser, points["end"])
    WebDriverWait(browser, 5).until(
        lambda driver: [group["tabs"] for group in sorted(dockview_layout_metrics(driver)["groups"], key=lambda item: item["rect"]["left"])] == [["2"], [file_item]]
    )


def test_dockview_tab_container_background_swaps_whole_panes(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2",
        sessions=["1", "2"],
        grid_width=1200,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    points = browser.execute_script(
        """
        const rectPoint = (rect, x = 0.5, y = 0.5) => ({x: Math.round(rect.left + rect.width * x), y: Math.round(rect.top + rect.height * y)});
        const sourceGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const targetGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const header = sourceGroup.querySelector('.dv-tabs-and-actions-container');
        const tabs = sourceGroup.querySelector('.dv-tabs-container').getBoundingClientRect();
        const tab = sourceGroup.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-tab').getBoundingClientRect();
        const headerRect = header.getBoundingClientRect();
        const x = Math.round(Math.min(headerRect.right - 84, Math.max(tab.right + 36, tabs.left + 230)));
        const y = Math.round(headerRect.top + Math.min(12, headerRect.height / 2));
        const hit = document.elementFromPoint(x, y);
        return {
          start: {x, y},
          end: rectPoint(targetGroup.getBoundingClientRect()),
          hitClass: hit?.className || '',
          hitTab: Boolean(hit?.closest?.('.dv-tab, .dockview-pane-tab, button, [data-pane-drag]')),
          headerDragSlot: header.dataset.paneDragSlot || '',
          headerDraggable: header.draggable === true,
          headerDragSource: header.classList.contains('pane-drag-source'),
        };
        """
    )
    assert points["headerDragSlot"] == "left", points
    assert points["headerDraggable"] is False, points
    assert points["headerDragSource"] is True, points
    assert points["hitTab"] is False, points
    cdp_drag(browser, points["start"], points["end"], steps=28)
    WebDriverWait(browser, 5).until(
        lambda driver: [group["tabs"] for group in sorted(dockview_layout_metrics(driver)["groups"], key=lambda item: item["rect"]["left"])] == [["2"], ["1"]]
    )
    after = dockview_layout_metrics(browser)
    assert after["slots"]["left"]["tabs"] == ["2"], after
    assert after["slots"]["right"]["tabs"] == ["1"], after


def test_dockview_pane_swap_rejects_too_small_target(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,3&layout=row@82(left,right)&tabs=left:prefs,1;right:3",
        sessions=["1", "3"],
        grid_width=1000,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3, min_width=60)
    result = browser.execute_script(
        """
        const sourceGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__prefs__"]').closest('.dv-groupview');
        const targetGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="3"]').closest('.dv-groupview');
        const sourceSlot = dockviewSlotForGroupElement(sourceGroup);
        const targetSlot = dockviewSlotForGroupElement(targetGroup);
        const targetRect = targetGroup.getBoundingClientRect();
        return {
          sourceSlot,
          targetSlot,
          targetWidth: Math.round(targetRect.width),
          canSwap: paneSwapAllowed(sourceSlot, targetSlot),
        };
        """
    )
    assert result["sourceSlot"] == "left", result
    assert result["targetSlot"] == "right", result
    assert result["targetWidth"] < 420, result
    assert result["canSwap"] is False, result


def test_dockview_tab_drag_preview_is_between_tabs(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=left&tabs=left:1,2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.68, 0.5)
    metrics = {}
    try:
        cdp_drag_hold(browser, start, end, steps=32)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                "return document.querySelector('.dv-tab.dv-drop-target .dv-drop-target-selection')?.getBoundingClientRect().width >= 22"
            )
        )
        metrics = browser.execute_script(
            """
            const rectFor = node => {
              const rect = node.getBoundingClientRect();
              return {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height};
            };
            const tab2 = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]');
            const tab3 = document.querySelector('.dockview-pane-tab[data-pane-tab="3"]');
            const selection = document.querySelector('.dv-tab.dv-drop-target .dv-drop-target-selection');
            const selectionStyle = getComputedStyle(selection);
            const tab2Rect = rectFor(tab2);
            const tab3Rect = rectFor(tab3);
            const selectionRect = rectFor(selection);
            return {
              tab2: tab2Rect,
              tab3: tab3Rect,
              selection: selectionRect,
              selectionClass: selection.className,
              backgroundColor: selectionStyle.backgroundColor,
              borderLeftWidth: selectionStyle.borderLeftWidth,
            };
            """
        )
    finally:
        cdp_release(browser, end)
    selection_center = (metrics["selection"]["left"] + metrics["selection"]["right"]) / 2
    assert 22 <= metrics["selection"]["width"] <= 26, metrics
    assert abs(selection_center - metrics["tab2"]["right"]) <= 3, metrics
    assert metrics["selection"]["left"] <= metrics["tab2"]["right"] - 10, metrics
    assert metrics["selection"]["right"] >= metrics["tab2"]["right"] + 10, metrics
    assert metrics["backgroundColor"] not in ("rgba(0, 0, 0, 0)", "transparent"), metrics
    assert metrics["borderLeftWidth"] == "2px", metrics


def test_dockview_adjacent_same_tab_drag_hides_noop_preview(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=left&tabs=left:1,2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)

    def drag_middle_to_adjacent_gap(target_selector, x_ratio):
        start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.5, 0.5)
        end = dockview_point(browser, target_selector, x_ratio, 0.5)
        try:
            cdp_drag_hold(browser, start, end, steps=32)
            browser.execute_async_script(
                """
                const done = arguments[arguments.length - 1];
                requestAnimationFrame(() => requestAnimationFrame(done));
                """
            )
            return browser.execute_script(
                """
                const visible = node => {
                  const rect = node.getBoundingClientRect();
                  const style = getComputedStyle(node);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && style.opacity !== '0';
                };
                const overlays = Array.from(document.querySelectorAll('.dv-drop-target, .dv-drop-target-selection, .dv-drop-target-anchor'))
                  .filter(visible)
                  .map(node => {
                    const rect = node.getBoundingClientRect();
                    return {className: node.className, width: Math.round(rect.width), height: Math.round(rect.height)};
                  });
                const grid = document.querySelector('.grid');
                return {
                  overlays,
                  rootPreview: grid?.classList.contains('drop-preview-root') || false,
                  url: location.search,
                };
                """
            )
        finally:
            cdp_release(browser, end)

    left_gap = drag_middle_to_adjacent_gap('.dockview-pane-tab[data-pane-tab="1"]', 0.86)
    right_gap = drag_middle_to_adjacent_gap('.dockview-pane-tab[data-pane-tab="3"]', 0.14)
    assert left_gap["overlays"] == [], left_gap
    assert left_gap["rootPreview"] is False, left_gap
    assert right_gap["overlays"] == [], right_gap
    assert right_gap["rootPreview"] is False, right_gap


def test_dockview_drag_moves_tab_to_other_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.68, 0.5)
    cdp_drag(browser, start, end)
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(done));
        """
    )
    browser.execute_script(
        """
        const next = cloneLayoutSlots(layoutSlots);
        next.left = emptyPaneState();
        next.right = paneStateWithTabs(['2', '1'], '1');
        dockviewLayoutState.syncQueued = false;
        layoutSlots = normalizeLayoutSlots(next);
        activeSessions = sessionsFromLayout();
        updateActiveSessionParam();
        dockviewLoadLayout(layoutSlots);
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: any(group["tabs"] == ["2", "1"] for group in dockview_layout_metrics(driver)["groups"])
    )
    metrics = dockview_layout_metrics(browser)
    assert any(group["tabs"] == ["2", "1"] for group in metrics["groups"])
    assert any(state.get("tabs") == ["2", "1"] for key, state in metrics["slots"].items() if key != "__tree")


def test_dockview_drag_splits_tab_to_right_pane_and_measures_geometry(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:1,2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    for edge_offset in (2, 8, 14):
        group = dockview_layout_metrics(browser)["groups"][0]["rect"]
        start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.5, 0.5)
        end = {"x": group["right"] - edge_offset, "y": round((group["top"] + group["bottom"]) / 2)}
        cdp_drag(browser, start, end)
        if len([group for group in dockview_layout_metrics(browser)["groups"] if group["tabs"]]) == 2:
            break
    WebDriverWait(browser, 5).until(
        lambda driver: len([group for group in dockview_layout_metrics(driver)["groups"] if group["tabs"]]) == 2
    )
    metrics = dockview_layout_metrics(browser)
    groups = sorted([group for group in metrics["groups"] if group["tabs"]], key=lambda item: item["rect"]["left"])
    assert [group["tabs"] for group in groups] == [["1"], ["2"]]
    assert groups[1]["rect"]["left"] >= groups[0]["rect"]["right"] - 2
    assert groups[0]["rect"]["width"] >= 250
    assert groups[1]["rect"]["width"] >= 250
    assert metrics["slots"]["__tree"]["split"] == "row"


def test_dockview_same_axis_second_split_preserves_target_half(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=row@50(left,right)&tabs=left:1,3;right:2", sessions=["1", "2", "3"], grid_width=1600)
    browser.set_window_size(1700, 800)
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    end = browser.execute_script(
        """
        const target = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const rect = target.getBoundingClientRect();
        return {
          x: Math.round(rect.right - 8),
          y: Math.round(rect.top + rect.height * 0.5),
        };
        """
    )
    cdp_drag(browser, dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.5, 0.5), end, steps=32)
    WebDriverWait(browser, 5).until(
        lambda driver: (
            len([group for group in dockview_layout_metrics(driver)["groups"] if group["tabs"]]) == 3
            and dockview_layout_metrics(driver)["slots"]["__tree"].get("split") == "row"
            and dockview_layout_metrics(driver)["slots"]["__tree"]["children"][1].get("split") == "row"
        )
    )
    metrics = dockview_layout_metrics(browser)
    groups = sorted([group for group in metrics["groups"] if group["tabs"]], key=lambda group: group["rect"]["left"])
    widths = [group["rect"]["width"] for group in groups]
    root = metrics["slots"]["__tree"]
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert [group["tabs"] for group in groups] == [["1"], ["2"], ["3"]], metrics
    assert root["split"] == "row", metrics
    assert root["children"][1]["split"] == "row", metrics
    assert 45 <= root["pct"] <= 55, metrics
    assert 45 <= root["children"][1]["pct"] <= 55, metrics
    assert widths[0] >= widths[1] * 1.75, metrics
    assert widths[0] >= widths[2] * 1.75, metrics
    assert abs(widths[1] - widths[2]) <= 40, metrics


def test_dockview_drag_to_root_left_of_stacked_panes_creates_full_height_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3&layout=col@50(top,bottom)&tabs=top:1,3;bottom:2",
        sessions=["1", "2", "3"],
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.5, 0.5)
    end = dockview_point(browser, "#dockviewRoot", 0.03, 0.5)
    cdp_drag(browser, start, end, steps=32)
    WebDriverWait(browser, 5).until(
        lambda driver: (
            dockview_layout_metrics(driver)["slots"]["__tree"].get("split") == "row"
            and len([group for group in dockview_layout_metrics(driver)["groups"] if group["tabs"]]) == 3
            and any(group["tabs"] == ["3"] for group in dockview_layout_metrics(driver)["groups"])
        )
    )
    metrics = dockview_layout_metrics(browser)
    groups = [group for group in metrics["groups"] if group["tabs"]]
    left = min(groups, key=lambda group: group["rect"]["left"])
    right = sorted([group for group in groups if group is not left], key=lambda group: group["rect"]["top"])
    right_top = min(group["rect"]["top"] for group in right)
    right_bottom = max(group["rect"]["bottom"] for group in right)
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert left["tabs"] == ["3"]
    assert [group["tabs"] for group in right] == [["1"], ["2"]]
    assert metrics["slots"]["__tree"]["split"] == "row"
    assert left["rect"]["right"] <= min(group["rect"]["left"] for group in right) + 2
    assert left["rect"]["top"] <= right_top + 2
    assert left["rect"]["bottom"] >= right_bottom - 2


def test_dockview_drag_to_root_right_of_stacked_panes_creates_full_height_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3&layout=col@50(top,bottom)&tabs=top:1,3;bottom:2",
        sessions=["1", "2", "3"],
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.5, 0.5)
    end = dockview_point(browser, "#dockviewRoot", 0.97, 0.5)
    cdp_drag(browser, start, end, steps=32)
    WebDriverWait(browser, 5).until(
        lambda driver: (
            dockview_layout_metrics(driver)["slots"]["__tree"].get("split") == "row"
            and len([group for group in dockview_layout_metrics(driver)["groups"] if group["tabs"]]) == 3
            and any(group["tabs"] == ["3"] for group in dockview_layout_metrics(driver)["groups"])
        )
    )
    metrics = dockview_layout_metrics(browser)
    groups = [group for group in metrics["groups"] if group["tabs"]]
    right = max(groups, key=lambda group: group["rect"]["left"])
    left = sorted([group for group in groups if group is not right], key=lambda group: group["rect"]["top"])
    left_top = min(group["rect"]["top"] for group in left)
    left_bottom = max(group["rect"]["bottom"] for group in left)
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert right["tabs"] == ["3"]
    assert [group["tabs"] for group in left] == [["1"], ["2"]]
    assert metrics["slots"]["__tree"]["split"] == "row"
    assert right["rect"]["left"] >= max(group["rect"]["right"] for group in left) - 2
    assert right["rect"]["top"] <= left_top + 2
    assert right["rect"]["bottom"] >= left_bottom - 2


def test_dockview_drag_to_pane_edge_splits_only_that_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3&layout=col@50(top,bottom)&tabs=top:1,3;bottom:2",
        sessions=["1", "2", "3"],
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.5, 0.5)
    end = browser.execute_script(
        """
        const topGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const rect = topGroup.getBoundingClientRect();
        return {
          x: Math.round(rect.left + 8),
          y: Math.round(rect.top + rect.height * 0.55),
        };
        """
    )
    cdp_drag(browser, start, end, steps=32)
    WebDriverWait(browser, 5).until(
        lambda driver: (
            dockview_layout_metrics(driver)["slots"]["__tree"].get("split") == "column"
            and dockview_layout_metrics(driver)["slots"]["__tree"]["children"][0].get("split") == "row"
            and len([group for group in dockview_layout_metrics(driver)["groups"] if group["tabs"]]) == 3
        )
    )
    metrics = dockview_layout_metrics(browser)
    root = metrics["slots"]["__tree"]
    groups = [group for group in metrics["groups"] if group["tabs"]]
    group_3 = next(group for group in groups if group["tabs"] == ["3"])
    group_1 = next(group for group in groups if group["tabs"] == ["1"])
    group_2 = next(group for group in groups if group["tabs"] == ["2"])
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert root["split"] == "column", metrics
    assert root["children"][0]["split"] == "row", metrics
    assert root["children"][1].get("slot"), metrics
    assert group_3["rect"]["right"] <= group_1["rect"]["left"] + 2, metrics
    assert group_3["rect"]["top"] <= group_1["rect"]["top"] + 2, metrics
    assert group_3["rect"]["bottom"] <= group_2["rect"]["top"] + 2, metrics
    assert group_2["rect"]["left"] <= group_3["rect"]["left"] + 2, metrics
    assert group_2["rect"]["right"] >= group_1["rect"]["right"] - 2, metrics


def test_dockview_root_left_drag_shows_full_span_preview_before_drop(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3&layout=col@50(top,bottom)&tabs=top:1,3;bottom:2",
        sessions=["1", "2", "3"],
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.5, 0.5)
    end = dockview_point(browser, "#dockviewRoot", 0.03, 0.5)
    preview = {}
    try:
        cdp_drag_hold(browser, start, end, steps=32)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                "return document.querySelector('.grid')?.classList.contains('drop-preview-root') === true"
            )
        )
        preview = browser.execute_script(
            """
            const grid = document.querySelector('.grid');
            const style = getComputedStyle(grid, '::before');
            return {
              root: grid.classList.contains('drop-preview-root'),
              left: grid.classList.contains('drop-preview-left'),
              label: grid.dataset.dropLabel || '',
              borderColor: style.borderLeftColor,
              separatorHover: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
            };
            """
        )
    finally:
        cdp_release(browser, end)
    assert preview["root"] is True
    assert preview["left"] is True
    assert preview["label"] == "Full left"
    assert preview["borderColor"] == preview["separatorHover"]


def test_dockview_root_right_drag_shows_full_span_preview_before_drop(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3&layout=col@50(top,bottom)&tabs=top:1,3;bottom:2",
        sessions=["1", "2", "3"],
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.5, 0.5)
    end = dockview_point(browser, "#dockviewRoot", 0.97, 0.5)
    preview = {}
    try:
        cdp_drag_hold(browser, start, end, steps=32)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                const grid = document.querySelector('.grid');
                return grid?.classList.contains('drop-preview-root') === true
                  && grid.classList.contains('drop-preview-right') === true;
                """
            )
        )
        preview = browser.execute_script(
            """
            const grid = document.querySelector('.grid');
            const style = getComputedStyle(grid, '::before');
            return {
              root: grid.classList.contains('drop-preview-root'),
              right: grid.classList.contains('drop-preview-right'),
              label: grid.dataset.dropLabel || '',
              borderColor: style.borderLeftColor,
              separatorHover: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
            };
            """
        )
    finally:
        cdp_release(browser, end)
    assert preview["root"] is True
    assert preview["right"] is True
    assert preview["label"] == "Full right"
    assert preview["borderColor"] == preview["separatorHover"]


def test_dockview_too_small_pane_edge_rejects_tab_preview(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=left&tabs=left:1,2",
        sessions=["1", "2"],
        grid_width=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2, min_width=80)
    result = browser.execute_script(
        """
        const group = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const slot = dockviewSlotForGroupElement(group);
        const rect = group.getBoundingClientRect();
        const event = {
          kind: 'content',
          position: 'right',
          group: {id: slot},
          nativeEvent: {clientX: Math.round(rect.right - 3), clientY: Math.round(rect.top + rect.height / 2)},
          getData() { return {panelId: '2', groupId: slot}; },
          preventDefault() { this.prevented = true; },
        };
        const intent = dockviewPaneContentDropIntent(event);
        dockviewTrackRootBoundaryOverlay(event);
        return {
          width: Math.round(rect.width),
          intent: intent ? {zone: intent.zone} : null,
          prevented: event.prevented === true,
          rootPreview: document.querySelector('#grid').classList.contains('drop-preview-root'),
        };
        """
    )
    assert result["width"] < 640, result
    assert result["intent"] is None, result
    assert result["prevented"] is True, result
    assert result["rootPreview"] is False, result


def test_dockview_finder_drop_previews_are_bottom_only_and_size_gated(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@40(left,slot1)&tabs=left:files;slot1:1",
        sessions=["1"],
        grid_width=1000,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    result = browser.execute_script(
        """
        const gridNode = document.querySelector('#grid');
        const finderGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]').closest('.dv-groupview');
        const contentGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const finderSlot = dockviewSlotForGroupElement(finderGroup);
        const contentSlot = dockviewSlotForGroupElement(contentGroup);
        const finderRect = finderGroup.getBoundingClientRect();
        const pointFor = position => ({
          clientX: Math.round(finderRect.left + finderRect.width / 2),
          clientY: position === 'bottom'
            ? Math.round(finderRect.bottom - 3)
            : position === 'top'
              ? Math.round(finderRect.top + 3)
              : Math.round(finderRect.top + finderRect.height / 2),
        });
        const tabProbe = position => {
          const nativeEvent = pointFor(position);
          const event = {
            kind: 'content',
            position,
            group: {id: finderSlot},
            nativeEvent,
            getData() { return {panelId: '1', groupId: contentSlot}; },
            preventDefault() { this.prevented = true; },
          };
          const intent = dockviewPaneContentDropIntent(event);
          dockviewTrackRootBoundaryOverlay(event);
          const result = {
            intent: intent ? {zone: intent.zone, targetSlot: intent.targetSlot} : null,
            prevented: event.prevented === true,
            rootPreview: gridNode.classList.contains('drop-preview-root'),
          };
          clearDropPreview();
          return result;
        };
        return {
          center: tabProbe('center'),
          left: tabProbe('left'),
          right: tabProbe('right'),
          top: tabProbe('top'),
          bottom: tabProbe('bottom'),
          finderRect: {width: Math.round(finderRect.width), height: Math.round(finderRect.height)},
        };
        """
    )
    assert result["finderRect"]["width"] >= 320, result
    assert result["finderRect"]["height"] >= 440, result
    for key in ["center", "left", "right", "top"]:
        assert result[key]["intent"] is None, result
        assert result[key]["prevented"] is True, result
        assert result[key]["rootPreview"] is False, result
    assert result["bottom"]["intent"] == {"zone": "bottom", "targetSlot": "left"}, result
    assert result["bottom"]["prevented"] is False, result

    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@40(left,slot1)&tabs=left:files;slot1:1",
        sessions=["1"],
        grid_width=1000,
        grid_height=420,
    )
    wait_for_dockview(browser, min_tabs=2)
    too_small = browser.execute_script(
        """
        const finderGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]').closest('.dv-groupview');
        const contentGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const finderSlot = dockviewSlotForGroupElement(finderGroup);
        const contentSlot = dockviewSlotForGroupElement(contentGroup);
        const rect = finderGroup.getBoundingClientRect();
        const event = {
          kind: 'content',
          position: 'bottom',
          group: {id: finderSlot},
          nativeEvent: {clientX: Math.round(rect.left + rect.width / 2), clientY: Math.round(rect.bottom - 3)},
          getData() { return {panelId: '1', groupId: contentSlot}; },
          preventDefault() { this.prevented = true; },
        };
        const intent = dockviewPaneContentDropIntent(event);
        dockviewTrackRootBoundaryOverlay(event);
        return {
          height: Math.round(rect.height),
          intent: intent ? {zone: intent.zone} : null,
          prevented: event.prevented === true,
          rootPreview: document.querySelector('#grid').classList.contains('drop-preview-root'),
        };
        """
    )
    assert too_small["height"] < 440, too_small
    assert too_small["intent"] is None, too_small
    assert too_small["prevented"] is True, too_small
    assert too_small["rootPreview"] is False, too_small


def test_dockview_language_switch_remounts_finder_content(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@28(left,slot1)&tabs=left:files;slot1:1",
        sessions=["1"],
        grid_width=1000,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    he_catalog = json.loads(Path("static/locales/he.json").read_text(encoding="utf-8"))
    result = browser.execute_async_script(
        """
        const heCatalog = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const before = panelNodes.get(fileExplorerItemId);
          i18nSetCatalogForTest('he', heCatalog);
          await applyLocale('he');
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const after = panelNodes.get(fileExplorerItemId);
          const group = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]')?.closest('.dv-groupview');
          const mounted = group?.querySelector('.dockview-panel-content > .file-explorer-panel');
          done({
            replaced: Boolean(before && after && before !== after),
            mapped: after === mounted,
            connected: after?.isConnected === true,
            toolbar: Boolean(mounted?.querySelector('.file-explorer-toolbar')),
            tree: Boolean(mounted?.querySelector('.file-explorer-tree-panel')),
            childCount: mounted?.childElementCount || 0,
            refreshTitle: mounted?.querySelector('[data-file-explorer-refresh]')?.title || '',
            dir: document.documentElement.dir,
          });
        })().catch(error => done({error: String(error)}));
        """,
        he_catalog,
    )
    assert result == {
        "replaced": True,
        "mapped": True,
        "connected": True,
        "toolbar": True,
        "tree": True,
        "childCount": 2,
        "refreshTitle": "רענן",
        "dir": "rtl",
    }


def test_dockview_finder_survives_hidden_host_adoption_and_reshow(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@28(left,slot1)&tabs=left:files;slot1:1",
        sessions=["1"],
        grid_width=1000,
        grid_height=620,
        file_explorer_open_intent="1",
    )
    wait_for_dockview(browser, min_tabs=2)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0;
            """
        )
    )
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 120; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const finderGroup = () => document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]')?.closest('.dv-groupview') || null;
          const widthOf = group => Math.round(group?.getBoundingClientRect?.().width || 0);
          const host = dockviewLayoutState.host;
          const api = dockviewLayoutState.api;
          if (!host || !api) return {error: 'dockview host or api missing'};
          const beforeWidth = widthOf(finderGroup());
          const beforeSlot = slotForSession(fileExplorerItemId);
          const originalToJSON = api.toJSON.bind(api);
          const finderless = emptyLayoutSlots();
          finderless[layoutTreeKey] = leafNode('left');
          finderless.left = paneStateWithTabs(['1'], '1');
          const finderlessJson = dockviewJsonFromLayoutSlots(finderless);
          api.toJSON = () => finderlessJson;
          host.style.display = 'none';
          adoptDockviewLayout();
          const hiddenHasFinder = itemInLayout(fileExplorerItemId);
          const hiddenSlot = slotForSession(fileExplorerItemId);
          host.style.display = '';
          dockviewLayoutToHost();
          adoptDockviewLayout();
          const restored = await waitFor(() => itemInLayout(fileExplorerItemId) && widthOf(finderGroup()) > 0);
          const afterWidth = widthOf(finderGroup());
          const afterSlot = slotForSession(fileExplorerItemId);
          api.toJSON = originalToJSON;
          return {
            beforeWidth,
            beforeSlot,
            hiddenHasFinder,
            hiddenSlot,
            restored,
            afterWidth,
            afterSlot,
            tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            url: location.search,
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in result, result
    assert result["beforeWidth"] > 0, result
    assert result["beforeSlot"], result
    assert result["hiddenHasFinder"] is True, result
    assert result["hiddenSlot"] == result["beforeSlot"], result
    assert result["restored"] is True, result
    assert result["afterWidth"] > 0, result
    assert result["afterSlot"], result
    assert "__files__" in result["tabs"], result
    assert result["errors"] == []
    assert result["rejections"] == []


def test_dockview_root_bottom_preview_preserves_docked_finder_column(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1,2&layout=row@24(left,col@50(slot1,slot2))&tabs=left:files;slot1:1;slot2:2",
        sessions=["1", "2"],
    )
    wait_for_dockview(browser, min_tabs=3)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0
              && document.querySelector('.dockview-pane-tab[data-pane-tab="2"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0;
            """
        )
    )
    metrics = browser.execute_script(
        """
        const host = document.querySelector('#dockviewRoot');
        const gridNode = document.querySelector('#grid');
        const finderGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]').closest('.dv-groupview');
        const contentGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const hostRect = host.getBoundingClientRect();
        const gridRect = gridNode.getBoundingClientRect();
        const finderRect = finderGroup.getBoundingClientRect();
        const contentRect = contentGroup.getBoundingClientRect();
        const eventAt = (rect, xRatio, y) => ({
          kind: 'content',
          position: 'bottom',
          getData() { return {panelId: '1', groupId: 'slot1'}; },
          nativeEvent: {
            clientX: Math.round(rect.left + rect.width * xRatio),
            clientY: Math.round(y),
          },
        });
        const contentIntent = dockviewRootBoundaryDropIntent(eventAt(contentRect, 0.5, hostRect.bottom - 2));
        dockviewShowRootBoundaryPreview(contentIntent);
        const previewStyle = getComputedStyle(gridNode, '::before');
        const preview = {
          root: gridNode.classList.contains('drop-preview-root'),
          bottom: gridNode.classList.contains('drop-preview-bottom'),
          label: gridNode.dataset.dropLabel || '',
          left: parseFloat(previewStyle.left) || 0,
          width: parseFloat(previewStyle.width) || 0,
          top: parseFloat(previewStyle.top) || 0,
          height: parseFloat(previewStyle.height) || 0,
          borderColor: previewStyle.borderLeftColor,
          separatorHover: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
        };
        clearDropPreview();
        const finderIntent = dockviewRootBoundaryDropIntent(eventAt(finderRect, 0.5, hostRect.bottom - 2));
        return {
      contentIntent: contentIntent ? {zone: contentIntent.zone} : null,
      finderIntent: finderIntent ? {zone: finderIntent.zone} : null,
          preview,
          gridRect: {left: gridRect.left, width: gridRect.width},
          finderRect: {left: finderRect.left, right: finderRect.right, width: finderRect.width},
          contentRect: {left: contentRect.left, right: contentRect.right, width: contentRect.width},
        };
        """
    )
    assert metrics["contentIntent"] == {"zone": "bottom"}, metrics
    assert metrics["finderIntent"] is None, metrics
    assert metrics["preview"]["root"] is True, metrics
    assert metrics["preview"]["bottom"] is True, metrics
    assert metrics["preview"]["label"] == "Full bottom", metrics
    assert metrics["preview"]["borderColor"] == metrics["preview"]["separatorHover"], metrics
    expected_left = metrics["contentRect"]["left"] - metrics["gridRect"]["left"] + 6
    expected_width = metrics["contentRect"]["width"] - 12
    assert abs(metrics["preview"]["left"] - expected_left) <= 2, metrics
    assert abs(metrics["preview"]["width"] - expected_width) <= 2, metrics
    assert metrics["preview"]["left"] >= metrics["finderRect"]["right"] - metrics["gridRect"]["left"] + 4, metrics


def test_dockview_root_top_drag_preview_preserves_docked_finder_column(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1,2&layout=row@24(left,col@50(slot1,slot2))&tabs=left:files;slot1:1;slot2:2",
        sessions=["1", "2"],
    )
    wait_for_dockview(browser, min_tabs=3)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0
              && document.querySelector('.dockview-pane-tab[data-pane-tab="1"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0
              && document.querySelector('.dockview-pane-tab[data-pane-tab="2"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0;
            """
        )
    )
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.5, 0.5)
    end = browser.execute_script(
        """
        const host = document.querySelector('#dockviewRoot');
        const contentGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const hostRect = host.getBoundingClientRect();
        const contentRect = contentGroup.getBoundingClientRect();
        return {
          x: Math.round(contentRect.left + contentRect.width * 0.5),
          y: Math.round(hostRect.top + 3),
        };
        """
    )
    preview = {}
    try:
        cdp_drag_hold(browser, start, end, steps=32)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                const grid = document.querySelector('.grid');
                return grid?.classList.contains('drop-preview-root') === true
                  && grid.classList.contains('drop-preview-top') === true;
                """
            )
        )
        preview = browser.execute_script(
            """
            const grid = document.querySelector('#grid');
            const finderGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]').closest('.dv-groupview');
            const contentGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
            const gridRect = grid.getBoundingClientRect();
            const finderRect = finderGroup.getBoundingClientRect();
            const contentRect = contentGroup.getBoundingClientRect();
            const style = getComputedStyle(grid, '::before');
            const nativeOverlays = Array.from(document.querySelectorAll('.dv-drop-target, .dv-drop-target-selection, .dv-drop-target-anchor'))
              .map(node => {
                const rect = node.getBoundingClientRect();
                const cs = getComputedStyle(node);
                return {
                  className: node.className,
                  display: cs.display,
                  visibility: cs.visibility,
                  left: rect.left,
                  right: rect.right,
                  top: rect.top,
                  bottom: rect.bottom,
                  width: rect.width,
                  height: rect.height,
                };
              })
              .filter(rect => rect.display !== 'none' && rect.visibility !== 'hidden' && rect.width > 0 && rect.height > 0);
            const coversFinder = nativeOverlays.some(rect => (
              rect.left < finderRect.right - 2
                && rect.right > finderRect.left + 2
                && rect.top < contentRect.top + contentRect.height * 0.5
                && rect.bottom > contentRect.top
            ));
            return {
              root: grid.classList.contains('drop-preview-root'),
              top: grid.classList.contains('drop-preview-top'),
              label: grid.dataset.dropLabel || '',
              left: parseFloat(style.left) || 0,
              width: parseFloat(style.width) || 0,
              borderColor: style.borderLeftColor,
              separatorHover: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
              nativeOverlays,
              coversFinder,
              gridRect: {left: gridRect.left, width: gridRect.width},
              finderRect: {left: finderRect.left, right: finderRect.right, width: finderRect.width},
              contentRect: {left: contentRect.left, right: contentRect.right, width: contentRect.width},
            };
            """
        )
    finally:
        cdp_release(browser, end)
    assert preview["root"] is True, preview
    assert preview["top"] is True, preview
    assert preview["label"] == "Full top", preview
    assert preview["borderColor"] == preview["separatorHover"], preview
    expected_left = preview["contentRect"]["left"] - preview["gridRect"]["left"] + 6
    expected_width = preview["contentRect"]["width"] - 12
    assert abs(preview["left"] - expected_left) <= 2, preview
    assert abs(preview["width"] - expected_width) <= 2, preview
    assert preview["left"] >= preview["finderRect"]["right"] - preview["gridRect"]["left"] + 4, preview
    assert preview["coversFinder"] is False, preview


def test_dockview_root_top_bottom_preview_normalizes_right_finder_and_avoids_reserved_column(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1,2&layout=row@76(col@50(slot1,slot2),right)&tabs=slot1:1;slot2:2;right:files",
        sessions=["1", "2"],
    )
    wait_for_dockview(browser, min_tabs=3)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0
              && document.querySelector('.dockview-pane-tab[data-pane-tab="1"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0;
            """
        )
    )
    metrics = browser.execute_script(
        """
        const host = document.querySelector('#dockviewRoot');
        const gridNode = document.querySelector('#grid');
        const finderGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]').closest('.dv-groupview');
        const contentGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const hostRect = host.getBoundingClientRect();
        const gridRect = gridNode.getBoundingClientRect();
        const finderRect = finderGroup.getBoundingClientRect();
        const contentRect = contentGroup.getBoundingClientRect();
        const eventAt = (rect, zone, x) => ({
          kind: 'content',
          position: zone,
          getData() { return {panelId: '2', groupId: dockviewSlotForGroupElement(contentGroup)}; },
          nativeEvent: {
            clientX: Math.round(x),
            clientY: zone === 'top' ? Math.round(hostRect.top + 2) : Math.round(hostRect.bottom - 2),
          },
        });
        const finderOnLeft = finderRect.left < contentRect.left;
        const previewFor = zone => {
          const nearFinderX = finderOnLeft ? finderRect.right + 3 : finderRect.left - 3;
          const contentIntent = dockviewRootBoundaryDropIntent(eventAt(contentRect, zone, nearFinderX));
          dockviewShowRootBoundaryPreview(contentIntent);
          const style = getComputedStyle(gridNode, '::before');
          const preview = {
            root: gridNode.classList.contains('drop-preview-root'),
            zone: gridNode.classList.contains(`drop-preview-${zone}`),
            label: gridNode.dataset.dropLabel || '',
            left: parseFloat(style.left) || 0,
            width: parseFloat(style.width) || 0,
            borderColor: style.borderLeftColor,
            separatorHover: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
          };
          clearDropPreview();
          const finderIntent = dockviewRootBoundaryDropIntent(eventAt(finderRect, zone, finderRect.left + finderRect.width / 2));
          return {
            contentIntent: contentIntent ? {zone: contentIntent.zone} : null,
            finderIntent: finderIntent ? {zone: finderIntent.zone} : null,
            preview,
          };
        };
        return {
          top: previewFor('top'),
          bottom: previewFor('bottom'),
          finderOnLeft,
          gridRect: {left: gridRect.left, width: gridRect.width},
          finderRect: {left: finderRect.left, right: finderRect.right, width: finderRect.width},
          contentRect: {left: contentRect.left, right: contentRect.right, width: contentRect.width},
        };
        """
    )
    for zone in ["top", "bottom"]:
        item = metrics[zone]
        assert metrics["finderOnLeft"] is True, metrics
        assert item["contentIntent"] == {"zone": zone}, metrics
        assert item["finderIntent"] is None, metrics
        assert item["preview"]["root"] is True, metrics
        assert item["preview"]["zone"] is True, metrics
        assert item["preview"]["label"] == f"Full {zone}", metrics
        assert item["preview"]["borderColor"] == item["preview"]["separatorHover"], metrics
        expected_left = metrics["contentRect"]["left"] - metrics["gridRect"]["left"] + 6
        expected_width = metrics["contentRect"]["width"] - 12
        assert abs(item["preview"]["left"] - expected_left) <= 2, metrics
        assert abs(item["preview"]["width"] - expected_width) <= 2, metrics
        assert item["preview"]["left"] >= metrics["finderRect"]["right"] - metrics["gridRect"]["left"] + 4, metrics


def test_dockview_drag_between_content_panes_preserves_docked_finder_width(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=row@24(left,row@50(slot1,slot2))&tabs=left:files;slot1:1;slot2:2",
        sessions=["1", "2"],
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_visible_selector(browser, '.dockview-pane-tab[data-pane-tab="1"]')
    wait_for_visible_selector(browser, '.dockview-pane-tab[data-pane-tab="2"]')
    before = dockview_layout_metrics(browser)
    finder_before = next(group for group in before["groups"] if "__files__" in group["tabs"])
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.68, 0.5)
    cdp_drag(browser, start, end, steps=28)
    WebDriverWait(browser, 5).until(
        lambda driver: any("1" in group["tabs"] and "2" in group["tabs"] for group in dockview_layout_metrics(driver)["groups"])
    )
    after = dockview_layout_metrics(browser)
    finder_after = next(group for group in after["groups"] if "__files__" in group["tabs"])
    assert abs(finder_after["rect"]["width"] - finder_before["rect"]["width"]) <= 3
    assert round(after["slots"]["__tree"]["pct"]) == round(before["slots"]["__tree"]["pct"])


def test_dockview_docked_finder_sash_resize_updates_root_pct(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=row@24(left,row@50(slot1,slot2))&tabs=left:files;slot1:1;slot2:2",
        sessions=["1", "2"],
    )
    wait_for_dockview(browser, min_tabs=2)
    WebDriverWait(browser, 5).until(
        lambda driver: len(dockview_layout_metrics(driver)["groups"]) >= 3
    )
    before = dockview_layout_metrics(browser)
    finder_before = next(group for group in before["groups"] if "__files__" in group["tabs"])
    content_before = sorted([group for group in before["groups"] if group["tabs"] in (["1"], ["2"])], key=lambda group: group["rect"]["left"])
    start = browser.execute_script(
        """
        const finderRight = arguments[0];
        const sashes = Array.from(document.querySelectorAll('.dv-sash'))
          .map(sash => {
            const rect = sash.getBoundingClientRect();
            return {left: rect.left, top: rect.top, width: rect.width, height: rect.height};
          })
          .filter(rect => rect.width > 0 && rect.height > rect.width);
        const sash = sashes.reduce((best, item) => (
          !best || Math.abs((item.left + item.width / 2) - finderRight) < Math.abs((best.left + best.width / 2) - finderRight)
            ? item
            : best
        ), null);
        return sash ? {
          x: Math.round(sash.left + sash.width / 2),
          y: Math.round(sash.top + sash.height / 2),
          left: sash.left,
          top: sash.top,
          width: sash.width,
          height: sash.height,
        } : null;
        """,
        finder_before["rect"]["right"],
    )
    end = {"x": start["x"] + 90, "y": start["y"]}
    cdp_drag(browser, start, end, steps=24)
    WebDriverWait(browser, 5).until(
        lambda driver: abs(
            next(group for group in dockview_layout_metrics(driver)["groups"] if "__files__" in group["tabs"])["rect"]["width"]
            - finder_before["rect"]["width"]
        ) > 35
    )
    after = dockview_layout_metrics(browser)
    finder_after = next(group for group in after["groups"] if "__files__" in group["tabs"])
    content_after = sorted([group for group in after["groups"] if group["tabs"] in (["1"], ["2"])], key=lambda group: group["rect"]["left"])
    assert finder_after["rect"]["width"] > finder_before["rect"]["width"] + 35
    assert after["slots"]["__tree"]["pct"] > before["slots"]["__tree"]["pct"] + 3
    assert abs(content_before[0]["rect"]["width"] - content_before[1]["rect"]["width"]) <= 4
    assert abs(content_after[0]["rect"]["width"] - content_after[1]["rect"]["width"]) <= 8, after
    assert content_after[0]["rect"]["width"] < content_before[0]["rect"]["width"] - 15
    assert content_after[1]["rect"]["width"] < content_before[1]["rect"]["width"] - 15


def test_dockview_file_drag_from_finder_opens_in_target_pane_with_preview(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:1,2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    result = browser.execute_script(
        """
        window.__dockviewFileOpen = null;
        window.openDraggedFilesInEditor = (payload, options) => {
          window.__dockviewFileOpen = {payload, options};
        };
        const group = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const target = group.querySelector('.dockview-panel-content') || group;
        const rect = group.getBoundingClientRect();
        const store = {
          'application/x-yolomux-file': JSON.stringify({path: '/home/test/yolomux.dev/README.md', paths: ['/home/test/yolomux.dev/README.md'], kind: 'file'}),
          'text/plain': '/home/test/yolomux.dev/README.md',
        };
        const dataTransfer = {
          types: Object.keys(store),
          dropEffect: '',
          effectAllowed: 'copy',
          getData(type) { return store[type] || ''; },
          setData(type, value) { store[type] = String(value); },
        };
        function fire(type, x, y) {
          const event = new Event(type, {bubbles: true, cancelable: true});
          Object.defineProperty(event, 'clientX', {value: x});
          Object.defineProperty(event, 'clientY', {value: y});
          Object.defineProperty(event, 'dataTransfer', {value: dataTransfer});
          target.dispatchEvent(event);
          return {defaultPrevented: event.defaultPrevented, dropEffect: dataTransfer.dropEffect};
        }
        const over = fire('dragover', Math.round(rect.left + 8), Math.round(rect.top + rect.height / 2));
        const preview = {
          dragOver: group.classList.contains('drag-over'),
          dropPreview: group.classList.contains('drop-preview'),
          left: group.classList.contains('drop-preview-left'),
          label: group.dataset.dropLabel || '',
          borderColor: getComputedStyle(group, '::before').borderLeftColor,
          separatorHover: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
        };
        const drop = fire('drop', Math.round(rect.left + 8), Math.round(rect.top + rect.height / 2));
        return {over, preview, drop, opened: window.__dockviewFileOpen};
        """
    )
    assert result["over"]["defaultPrevented"] is True
    assert result["over"]["dropEffect"] == "copy"
    assert result["preview"]["dragOver"] is True
    assert result["preview"]["dropPreview"] is True
    assert result["preview"]["left"] is True
    assert result["preview"]["label"] == "left"
    assert result["preview"]["borderColor"] == result["preview"]["separatorHover"]
    assert result["drop"]["defaultPrevented"] is True
    assert result["opened"]["payload"]["path"] == "/home/test/yolomux.dev/README.md"
    assert result["opened"]["options"]["targetSlot"] == "left"
    assert result["opened"]["options"]["targetZone"] == "left"


def test_dockview_file_drag_to_finder_previews_only_roomy_bottom(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@40(left,slot1)&tabs=left:files;slot1:1",
        sessions=["1"],
        grid_width=1000,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    result = browser.execute_script(
        """
        window.__dockviewFileOpen = null;
        window.openDraggedFilesInEditor = (payload, options) => {
          window.__dockviewFileOpen = {payload, options};
        };
        const finderGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]').closest('.dv-groupview');
        const target = finderGroup.querySelector('.dockview-panel-content') || finderGroup;
        const rect = finderGroup.getBoundingClientRect();
        const store = {
          'application/x-yolomux-file': JSON.stringify({path: '/home/test/yolomux.dev/README.md', paths: ['/home/test/yolomux.dev/README.md'], kind: 'file'}),
          'text/plain': '/home/test/yolomux.dev/README.md',
        };
        const dataTransfer = {
          types: Object.keys(store),
          dropEffect: '',
          effectAllowed: 'copy',
          getData(type) { return store[type] || ''; },
          setData(type, value) { store[type] = String(value); },
        };
        function fire(type, x, y) {
          const event = new Event(type, {bubbles: true, cancelable: true});
          Object.defineProperty(event, 'clientX', {value: x});
          Object.defineProperty(event, 'clientY', {value: y});
          Object.defineProperty(event, 'dataTransfer', {value: dataTransfer});
          target.dispatchEvent(event);
          return {
            defaultPrevented: event.defaultPrevented,
            dropEffect: dataTransfer.dropEffect,
            preview: {
              dragOver: finderGroup.classList.contains('drag-over'),
              dropPreview: finderGroup.classList.contains('drop-preview'),
              bottom: finderGroup.classList.contains('drop-preview-bottom'),
              left: finderGroup.classList.contains('drop-preview-left'),
              label: finderGroup.dataset.dropLabel || '',
            },
          };
        }
        const center = fire('dragover', Math.round(rect.left + rect.width / 2), Math.round(rect.top + rect.height / 2));
        clearDropPreview();
        const left = fire('dragover', Math.round(rect.left + 8), Math.round(rect.top + rect.height / 2));
        clearDropPreview();
        const bottom = fire('dragover', Math.round(rect.left + rect.width / 2), Math.round(rect.bottom - 8));
        const drop = fire('drop', Math.round(rect.left + rect.width / 2), Math.round(rect.bottom - 8));
        return {center, left, bottom, drop, opened: window.__dockviewFileOpen};
        """
    )
    assert result["center"]["defaultPrevented"] is True, result
    assert result["center"]["dropEffect"] == "none", result
    assert result["center"]["preview"]["dropPreview"] is False, result
    assert result["left"]["dropEffect"] == "none", result
    assert result["left"]["preview"]["dropPreview"] is False, result
    assert result["bottom"]["dropEffect"] == "copy", result
    assert result["bottom"]["preview"]["dropPreview"] is True, result
    assert result["bottom"]["preview"]["bottom"] is True, result
    assert result["bottom"]["preview"]["label"] == "bottom", result
    assert result["drop"]["defaultPrevented"] is True, result
    assert result["opened"]["payload"]["path"] == "/home/test/yolomux.dev/README.md", result
    assert result["opened"]["options"]["targetSlot"] == "left", result
    assert result["opened"]["options"]["targetZone"] == "bottom", result


def test_dockview_multi_file_drag_preserves_order_dedupes_and_uses_one_target(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:1,2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          window.__multiFileOpened = [];
          fetchFilePathInfo = async path => ({path, name: path.split('/').pop(), kind: 'file'});
          openFileInEditor = async (path, info, options) => {
            window.__multiFileOpened.push({path, info, options});
          };
          refreshOpenFileDiff = async () => {};
          openFileDiffAvailable = () => false;
          const group = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
          const target = group.querySelector('.dockview-panel-content') || group;
          const rect = group.getBoundingClientRect();
          const paths = [
            '/home/test/yolomux.dev/a.md',
            '/home/test/yolomux.dev/b.md',
            '/home/test/yolomux.dev/a.md',
            '/home/test/yolomux.dev/c.md',
          ];
          const store = {
            'application/x-yolomux-file': JSON.stringify({path: paths[0], paths, kind: 'file'}),
            'text/plain': paths.join('\\n'),
          };
          const dataTransfer = {
            types: Object.keys(store),
            dropEffect: '',
            effectAllowed: 'copy',
            getData(type) { return store[type] || ''; },
            setData(type, value) { store[type] = String(value); },
          };
          function fire(type, x, y) {
            const event = new Event(type, {bubbles: true, cancelable: true});
            Object.defineProperty(event, 'clientX', {value: x});
            Object.defineProperty(event, 'clientY', {value: y});
            Object.defineProperty(event, 'dataTransfer', {value: dataTransfer});
            target.dispatchEvent(event);
            return {defaultPrevented: event.defaultPrevented, dropEffect: dataTransfer.dropEffect};
          }
          const x = Math.round(rect.left + rect.width / 2);
          const y = Math.round(rect.top + rect.height / 2);
          const over = fire('dragover', x, y);
          const preview = {
            previewCount: document.querySelectorAll('.drop-preview').length,
            groupPreview: group.classList.contains('drop-preview'),
            label: group.dataset.dropLabel || '',
            targetSlot: dockviewSlotForGroupElement(group),
          };
          const drop = fire('drop', x, y);
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          done({over, preview, drop, opened: window.__multiFileOpened});
        })().catch(error => done({error: String(error), stack: error?.stack || ''}));
        """
    )
    assert "error" not in result, result
    assert result["over"]["defaultPrevented"] is True, result
    assert result["over"]["dropEffect"] == "copy", result
    assert result["preview"]["previewCount"] == 1, result
    assert result["preview"]["groupPreview"] is True, result
    assert result["preview"]["label"] == "Take over", result
    assert result["drop"]["defaultPrevented"] is True, result
    assert [item["path"] for item in result["opened"]] == [
        "/home/test/yolomux.dev/a.md",
        "/home/test/yolomux.dev/b.md",
        "/home/test/yolomux.dev/c.md",
    ], result
    assert {item["options"]["targetSlot"] for item in result["opened"]} == {result["preview"]["targetSlot"]}, result
    assert [item["options"]["targetIndex"] for item in result["opened"]] == [None, None, None], result
    assert {item["options"]["targetZone"] for item in result["opened"]} == {"middle"}, result


def test_dockview_directory_drag_over_finder_is_reserved_but_terminal_path_target_stays_allowed(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@30(left,slot1)&tabs=left:files;slot1:1",
        sessions=["1"],
        grid_width=1200,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    result = browser.execute_script(
        """
        const finderGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]').closest('.dv-groupview');
        const contentGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const target = finderGroup.querySelector('.dockview-panel-content') || finderGroup;
        const finderRect = finderGroup.getBoundingClientRect();
        const contentRect = contentGroup.getBoundingClientRect();
        const finderSlot = dockviewSlotForGroupElement(finderGroup);
        const contentSlot = dockviewSlotForGroupElement(contentGroup);
        const payload = {path: '/home/test/yolomux.dev/src', paths: ['/home/test/yolomux.dev/src'], kind: 'dir'};
        const store = {
          'application/x-yolomux-file': JSON.stringify(payload),
          'text/plain': payload.path,
        };
        const dataTransfer = {
          types: Object.keys(store),
          dropEffect: '',
          effectAllowed: 'copy',
          getData(type) { return store[type] || ''; },
          setData(type, value) { store[type] = String(value); },
        };
        function fireFinder(type, x, y) {
          const event = new Event(type, {bubbles: true, cancelable: true});
          Object.defineProperty(event, 'clientX', {value: x});
          Object.defineProperty(event, 'clientY', {value: y});
          Object.defineProperty(event, 'dataTransfer', {value: dataTransfer});
          target.dispatchEvent(event);
          return {
            defaultPrevented: event.defaultPrevented,
            dropEffect: dataTransfer.dropEffect,
            preview: finderGroup.classList.contains('drop-preview'),
            label: finderGroup.dataset.dropLabel || '',
          };
        }
        const center = fireFinder('dragover', Math.round(finderRect.left + finderRect.width / 2), Math.round(finderRect.top + finderRect.height / 2));
        clearDropPreview();
        const bottom = fireFinder('dragover', Math.round(finderRect.left + finderRect.width / 2), Math.round(finderRect.bottom - 8));
        clearDropPreview();
        return {
          center,
          bottom,
          sharedFileGateFinder: fileDropIntentAllowsPayload(payload, {targetSlot: finderSlot, zone: 'bottom', targetRect: finderRect}),
          sharedPathGateFinderMiddle: pathDropIntentAllowsPayload(payload, {targetSlot: finderSlot, zone: 'middle', targetRect: finderRect}),
          sharedPathGateTerminalEdge: pathDropIntentAllowsPayload(payload, {targetSlot: contentSlot, zone: 'left', targetRect: contentRect}),
        };
        """
    )
    assert result["center"]["defaultPrevented"] is True, result
    assert result["center"]["dropEffect"] == "none", result
    assert result["center"]["preview"] is False, result
    assert result["bottom"]["dropEffect"] == "none", result
    assert result["bottom"]["preview"] is False, result
    assert result["sharedFileGateFinder"] is False, result
    assert result["sharedPathGateFinderMiddle"] is False, result
    assert result["sharedPathGateTerminalEdge"] is True, result
