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
    assert active["borderTopLeftRadius"] == "6px"
    assert active["borderTopRightRadius"] == "6px"
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
    assert fetches == ["POST /api/tmux-window"]
    assert query == "session=1&window=2"


def test_dockview_window_bar_working_agent_glyph_uses_shared_pulse(browser, tmp_path):
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
        const working = document.querySelector('.agent-window-agent-icon--working');
        const idleButton = Array.from(document.querySelectorAll('.tmux-window-button')).find(button => button.textContent.includes('2:claude'));
        const idleIcon = idleButton?.querySelector('.agent-icon.claude');
        const idleDot = idleButton?.querySelector('.agent-window-status-dot');
        const workingStyle = getComputedStyle(working);
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
          workingGlowRgb: workingStyle.getPropertyValue('--agent-working-glow-rgb').trim(),
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
    assert metrics["workingAnimationName"] == "agent-symbol-glow-cadence", metrics
    # The blink dips to ~0.15 at its off frame, so accept the full animated opacity range.
    assert 0.0 <= float(metrics["workingOpacity"]) <= 1, metrics
    assert metrics["workingGlowRgb"] == "102 126 248", metrics


def test_dockview_window_bar_active_agent_glyph_pulses_without_dot(browser, tmp_path):
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
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return !!document.querySelector('.tmux-window-button.active .agent-window-agent-icon--active')")
    )
    metrics = browser.execute_script(
        """
        const button = Array.from(document.querySelectorAll('.tmux-window-button')).find(item => item.textContent.includes('1:codex'));
        const icon = button?.querySelector('.agent-window-agent-icon--active');
        const dot = button?.querySelector('.agent-window-status-dot');
        const style = getComputedStyle(icon);
        return {
          buttonActive: button?.classList.contains('active') || false,
          iconHasSvg: !!icon?.querySelector('svg'),
          iconAnimationName: style.animationName,
          iconGlowRgb: style.getPropertyValue('--agent-working-glow-rgb').trim(),
          dotCount: button?.querySelectorAll('.agent-window-status-dot').length || 0,
          dotText: dot?.textContent || '',
        };
        """
    )
    assert metrics["buttonActive"] is True, metrics
    assert metrics["iconHasSvg"] is True, metrics
    assert metrics["iconAnimationName"] == "agent-symbol-glow-cadence", metrics
    assert metrics["iconGlowRgb"] == "102 126 248", metrics
    assert metrics["dotCount"] == 0, metrics
    assert metrics["dotText"] == "", metrics


def test_dockview_working_glyph_animation_advances_over_wall_clock_time(browser, tmp_path):
    # Regression for "the working glyph does not visibly pulse". The earlier fixture test only froze
    # the animation at two fractions and pixel-diffed them, which proves the keyframes differ but NOT
    # that the live app actually animates over time. Here we boot the real app and assert the
    # live-computed opacity sweeps across the cycle on its own over wall-clock time.
    #
    # We read getComputedStyle().opacity rather than diffing screenshots on purpose: the pulse
    # animates opacity+transform, which Chrome runs on the compositor; headless captureScreenshot
    # serves a static composited frame, so an over-time screenshot diff reads ~0 even while the
    # animation is genuinely running. The main-thread computed opacity is the reliable live signal.
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
    base = browser.execute_script(
        """
        const el = document.querySelector('.agent-window-agent-icon--working');
        el.setAttribute('data-pulse-probe', '1');
        const cs = getComputedStyle(el);
        return {
          reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
          animationName: cs.animationName,
          animationDuration: cs.animationDuration,
          animationIterationCount: cs.animationIterationCount,
          animationPlayState: cs.animationPlayState,
          animationCount: el.getAnimations({subtree: false}).length,
          opacity: cs.opacity,
        };
        """
    )
    if base["reducedMotion"]:
        pytest.skip("browser prefers reduced motion")
    assert base["animationName"] == "agent-symbol-glow-cadence", base
    assert base["animationDuration"] == "1.55s", base
    assert base["animationIterationCount"] == "infinite", base
    assert base["animationPlayState"] == "running", base
    assert base["animationCount"] == 1, base

    # Sample the live-computed opacity across ~2 full 1.55s cycles. Do NOT set currentTime.
    opacities = [float(base["opacity"])]
    stamps = [True]
    for _ in range(12):
        time.sleep(0.25)
        sample = browser.execute_script(
            """
            const el = document.querySelector('.agent-window-agent-icon--working');
            return {opacity: getComputedStyle(el).opacity, stamped: el.getAttribute('data-pulse-probe') === '1'};
            """
        )
        opacities.append(float(sample["opacity"]))
        stamps.append(bool(sample["stamped"]))
    low = min(opacities)
    high = max(opacities)
    # The rest frame is opacity 0.46 and the peak frame is opacity 1; over two cycles the live value
    # must reach both ends, proving the animation is actually advancing rather than stuck on a frame.
    assert low <= 0.6, (low, high, opacities)
    assert high >= 0.95, (low, high, opacities)
    assert (high - low) >= 0.3, (low, high, opacities)
    # The animated node must persist (not be replaced/restarted every activity poll); the phase
    # anchor only helps if the same element keeps animating.
    assert all(stamps), stamps


def test_dockview_working_glyph_stays_distinct_under_reduced_motion(browser, tmp_path):
    # Regression: with prefers-reduced-motion the pulse animation is correctly disabled, but the
    # working/active glyph must still be visually distinct from an idle glyph (a steady glow ring),
    # otherwise a user with reduced motion sees a static icon identical to idle and cannot tell an
    # agent is working. Before the fix, working and idle were byte-identical under reduced motion.
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
            const idle = document.querySelector('.agent-window-agent-icon:not(.agent-window-agent-icon--working):not(.agent-window-agent-icon--active)');
            const ws = getComputedStyle(working);
            const is = idle ? getComputedStyle(idle) : null;
            return {
              reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
              workingAnimationName: ws.animationName,
              workingBoxShadow: ws.boxShadow,
              workingFilter: ws.filter,
              workingGlowRgb: ws.getPropertyValue('--agent-working-glow-rgb').trim(),
              idleFound: !!idle,
              idleBoxShadow: is ? is.boxShadow : null,
              idleFilter: is ? is.filter : null,
            };
            """
        )
        assert data["reducedMotion"] is True, data
        # Animation is disabled under reduced motion (no motion), as intended.
        assert data["workingAnimationName"] == "none", data
        # ...but a steady glow ring keeps the working glyph distinct from idle.
        assert data["idleFound"] is True, data
        assert data["workingBoxShadow"] != "none", data
        assert data["idleBoxShadow"] == "none", data
        assert data["workingBoxShadow"] != data["idleBoxShadow"], data
        # The glow color comes from the per-agent token (codex blue), not a hard-coded value.
        assert data["workingGlowRgb"] == "102 126 248", data
        assert "102, 126, 248" in data["workingBoxShadow"], data
    finally:
        browser.execute_cdp_cmd("Emulation.setEmulatedMedia", {"features": []})


def test_dockview_tab_symbol_pulses_when_session_works_via_screen_proxy(browser, tmp_path):
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
        const sym = tab && tab.querySelector('.session-agent-activity-marker .agent-window-agent-icon--working');
        return {
          tabFound: !!tab,
          hasMarker: !!marker,
          hasWorkingSymbol: !!sym,
          symbolAnimationName: sym ? getComputedStyle(sym).animationName : null,
          symbolIsClaude: sym ? sym.classList.contains('claude') : null,
        };
        """
    )
    assert data["tabFound"] is True, data
    assert data["hasMarker"] is True, data
    assert data["hasWorkingSymbol"] is True, data
    assert data["symbolAnimationName"] == "agent-symbol-glow-cadence", data
    assert data["symbolIsClaude"] is True, data


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
          close: rectFor(close),
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
    assert 0 <= before["close"]["left"] - before["bar"]["right"] <= 8
    assert before["row"]["right"] - before["close"]["right"] <= 8
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
          const infoSlot = slotForItem(infoItemId);
          activatePaneTab('slot1', '2', {userInitiated: true});
          setFocusedPanelItem('2', {userInitiated: true});
          const filePath = '/home/test/yolomux.dev/NEWTAB.md';
          const fileItem = await openFileInEditor(filePath, {name: 'NEWTAB.md'}, {userInitiated: true});
          done({
            prefsSlot,
            infoSlot,
            fileSlot: slotForItem(fileItem),
            slot1Tabs: paneTabs('slot1'),
            leftTabs: paneTabs('left'),
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    assert metrics.get("error") is None, metrics
    assert metrics["prefsSlot"] == "slot1", metrics
    assert metrics["infoSlot"] == "slot1", metrics
    assert metrics["fileSlot"] == "slot1", metrics
    assert "1" in metrics["leftTabs"], metrics
    assert "2" in metrics["slot1Tabs"], metrics


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
          tabsOverlapToolbar: tabs.filter(rect => rect.bottom > toolbar.top + 1),
          activeTab: rectFor(activeRect),
          activeTabClickable: Boolean(hit?.closest?.('.dockview-pane-tab') === activeTab),
        };
        """
    )
    assert metrics["tabRows"] >= 2, metrics
    assert metrics["tabsOverflowX"] == "visible", metrics
    assert metrics["tabsScrollWidth"] <= metrics["tabsClientWidth"] + 1, metrics
    assert metrics["firstRowRight"] <= metrics["actionLeft"] - 1, metrics
    assert metrics["laterRowRight"] >= metrics["actionLeft"] + 20, metrics
    assert metrics["tabsOverlapToolbar"] == [], metrics
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
    assert preview["label"] == "full left"
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
    assert preview["label"] == "full right"
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
    assert metrics["preview"]["label"] == "full bottom", metrics
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
    assert preview["label"] == "full top", preview
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
        assert item["preview"]["label"] == f"full {zone}", metrics
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
    assert result["preview"]["label"] == "take over", result
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
