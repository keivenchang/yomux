# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Trusted native Chromium checks shared by the isolated Go gate and live smoke."""

import json
import math
import os
from pathlib import Path
import subprocess
import time
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from tests.browser_helpers.native_pointer import cdp_drag_hold, cdp_release, click_visible_selector


def normal_journey_resource_snapshot(driver, tmux):
    """Retain namespace and scheduling evidence without exposing ambient environment values."""
    paths = ("/proc/pressure/cpu", "/proc/loadavg", "/sys/fs/cgroup/cpu.max", "/sys/fs/cgroup/cpu.stat")
    scheduling = {path: Path(path).read_text().strip() for path in paths if Path(path).is_file()}
    browser_pid = driver.capabilities.get("goog:processID")
    service_pid = driver.service.process.pid
    browser_args = []
    if browser_pid and Path(f"/proc/{browser_pid}/cmdline").is_file():
        browser_args = [arg for arg in Path(f"/proc/{browser_pid}/cmdline").read_bytes().decode().split("\0")
                        if arg.startswith(("--headless", "--user-data-dir=", "--remote-debugging-port="))]
    return {
        "runner_pid": os.getpid(), "chromedriver_pid": service_pid, "browser_pid": browser_pid,
        "browser_args": browser_args, "profile": driver.capabilities.get("chrome", {}).get("userDataDir"),
        "devtools": driver.capabilities.get("goog:chromeOptions", {}).get("debuggerAddress"),
        "tmux": list(tmux), "namespaces": {name: os.readlink(f"/proc/self/ns/{name}") for name in ("pid", "net", "mnt") if Path(f"/proc/self/ns/{name}").exists()},
        "page": driver.execute_script("""
          return {origin: location.origin, visibility: document.visibilityState, client: browserClientId,
            renderers: activeSessions.filter(isTmuxSession).map(session => {
              const container = terminals.get(session)?.container;
              const screen = container?.querySelector('.xterm-screen');
              const rect = screen?.getBoundingClientRect();
              const style = screen ? getComputedStyle(screen) : null;
              return {session, connected: screen?.isConnected === true,
                modelVisible: terminalIsVisible(session, container, {measure: false}),
                domRows: screen?.querySelectorAll('.xterm-rows > div').length || 0,
                canvases: screen?.querySelectorAll('canvas').length || 0,
                rect: rect ? {x: rect.x, y: rect.y, width: rect.width, height: rect.height} : null,
                display: style?.display, visibility: style?.visibility};
            })};
        """),
        "scheduling": scheduling,
        "chrome_metrics": {item["name"]: item["value"] for item in driver.execute_cdp_cmd("Performance.getMetrics", {})["metrics"]
                           if item["name"] in {"TaskDuration", "ScriptDuration", "LayoutDuration", "RecalcStyleDuration", "ThreadTime", "Frames", "Nodes"}},
    }


def normal_tabber_journey(driver, tmux, sessions, file_path=None):
    """Exercise only caller-owned sessions; the caller owns authentication and cleanup."""
    driver.set_script_timeout(30)
    driver.execute_script("""
      window.__tabberJourneySaved = {mode: tabberMode, selections: [...tabberNormalSelections], layout: cloneLayoutSlots(layoutSlots)};
      setTabberMode('normal'); storeTabberNormalSelections([]);
      void applyTabberNormalSelections();
    """)
    wait = WebDriverWait(driver, 15)
    wait.until(lambda browser: browser.execute_script("return typeof window.Terminal === 'function' && document.querySelector('#panel-__tabber__ .file-tree-row[data-tabber-type=normal]') !== null"))

    def select_row(session):
        click_visible_selector(driver, f'#panel-__tabber__ .file-tree-row[data-tabber-type="normal"][data-tabber-session="{session}"]')

    def row(session):
        return wait.until(EC.element_to_be_clickable(('css selector', f'#panel-__tabber__ .file-tree-row[data-tabber-type="normal"][data-tabber-session="{session}"]')))

    def selected(expected):
        wait.until(lambda browser: browser.execute_script("return JSON.stringify(tabberNormalSelections) === JSON.stringify(arguments[0])", expected))

    empty = driver.execute_script('''
      const titles = [...document.querySelectorAll('.empty-pane-panel strong')];
      return {titlesHidden: titles.every(title => title.hidden || getComputedStyle(title).display === 'none'),
        dropPromptVisible: titles.some(title => title.getClientRects().length && title.textContent.trim() === 'Drop a tab here')};
    ''')
    assert empty['titlesHidden'] and not empty['dropPromptVisible'], empty
    cold_open_ms = []
    for session in sessions[:2]:
        cold_start = time.monotonic()
        select_row(session)
        wait.until(lambda browser: browser.execute_script("return tabberNormalSelections.length === 1 && tabberNormalSelections[0] === arguments[0] && terminals.get(arguments[0])?.socket?.readyState === 1 && document.querySelector('#panel-' + arguments[0] + ' .xterm') !== null", session))
        cold_open_ms.append((time.monotonic() - cold_start) * 1000)
    selected([sessions[1]])
    assert driver.execute_script('return paneItems(layoutSlots).filter(isTmuxSession).join(",")') == sessions[1]
    select_row(sessions[1])
    selected([])
    assert driver.execute_script('return !paneItems(layoutSlots).some(isTmuxSession)')
    select_row(sessions[1])
    selected([sessions[1]])
    select_row(sessions[0])
    selected([sessions[0]])
    retained = sessions[1]
    select_row(retained)
    selected([retained])
    wait.until(lambda browser: browser.execute_script("return document.activeElement === terminals.get(arguments[0])?.term?.textarea", retained))
    driver.execute_script("setFileExplorerTreeDateMode('relative', 'tabber');")
    wait.until(lambda browser: browser.execute_script("return document.querySelector('#panel-__tabber__ .file-tree-row[data-tabber-session=\"' + CSS.escape(arguments[0]) + '\"] .file-tree-date')?.textContent.trim().endsWith('ago')", retained))
    driver.execute_script("window.__retainedTerminal = terminals.get(arguments[0]);", retained)
    draft = "printf 'retained-draft-ok\\n'"
    driver.find_element('css selector', f'#panel-{retained} .xterm-helper-textarea').send_keys(draft)
    select_row(sessions[2])
    selected([sessions[2]])
    assert driver.execute_script("return terminals.get(arguments[0]) === window.__retainedTerminal", retained)
    select_row(sessions[2])
    selected([])
    select_row(sessions[2])
    selected([sessions[2]])
    wait.until(lambda browser: browser.execute_script("return terminals.get(arguments[0])?.socket?.readyState === 1", sessions[2]))
    select_row(retained)
    selected([retained])
    wait.until(lambda browser: browser.execute_script("return document.activeElement === terminals.get(arguments[0])?.term?.textarea", retained))
    try:
        wait.until(lambda browser: any(draft in line for line in subprocess.check_output((*tmux, 'capture-pane', '-p', '-J', '-t', '=' + retained + ':')).decode().splitlines()))
    except TimeoutException:
        capture = subprocess.check_output((*tmux, 'capture-pane', '-p', '-J', '-t', '=' + retained + ':')).decode()
        focus = driver.execute_script('return {focusedTerminal, focusedPanelItem, activeElement: document.activeElement?.className || ""};')
        print('Go Normal retained draft diagnostic: ' + json.dumps({'focus': focus, 'capture': capture}, sort_keys=True))
        raise
    driver.find_element('css selector', f'#panel-{retained} .xterm-helper-textarea').send_keys(Keys.ENTER)
    wait.until(lambda browser: any(line.strip() == 'retained-draft-ok' for line in subprocess.check_output((*tmux, 'capture-pane', '-p', '-J', '-t', '=' + retained + ':')).decode().splitlines()))
    driver.execute_cdp_cmd("Performance.enable", {})
    print('Go Normal resources before: ' + json.dumps(normal_journey_resource_snapshot(driver, tmux), sort_keys=True))
    # Measure in the browser from trusted pointer input, not from the WebDriver round trip.
    driver.execute_script("""
      clearClientPerfCounters();
      window.__normalRefreshOwners = {};
      window.__normalOriginalRefresh = refreshTabberPanels;
      refreshTabberPanels = function(...args) {
        const owner = new Error().stack.split('\\n').slice(2, 4).join('\\n');
        window.__normalRefreshOwners[owner] = (window.__normalRefreshOwners[owner] || 0) + 1;
        return window.__normalOriginalRefresh(...args);
      };
      window.__normalTimings = [];
      window.__normalTimingActive = true;
      window.__normalTimingCleanups = [];
      const originalHighlight = syncTabberNormalSelectionHighlight;
      syncTabberNormalSelectionHighlight = function(...args) {
        const result = originalHighlight(...args);
        const sample = window.__normalTimings.at(-1);
        if (sample && sample.highlightDom === null
            && window.__normalTimingActiveRow?.classList.contains('tabber-active-normal') === !sample.before) {
          sample.highlightDom = performance.now() - sample.start;
        }
        return result;
      };
      window.__normalTimingCleanups.push(() => { syncTabberNormalSelectionHighlight = originalHighlight; });
      const onFocus = event => {
        const sample = window.__normalTimings.at(-1);
        if (sample && !sample.before && sample.focusEvent === null && event.target === terminals.get(sample.item)?.term?.textarea) {
          sample.focusEvent = performance.now() - sample.start;
        }
      };
      document.addEventListener('focusin', onFocus, true);
      window.__normalTimingCleanups.push(() => document.removeEventListener('focusin', onFocus, true));
      window.__normalTimingListener = event => {
        const row = event.target.closest('.file-tree-row[data-tabber-type="normal"]');
        if (!row || !event.isTrusted) return;
        const item = row.dataset.tabberPaneItem || row.dataset.tabberSession;
        const before = tabberNormalSelections.includes(item);
        const sample = {item, before, start: performance.now(), highlight: null, focus: null, paint: null,
          highlightDom: null, focusEvent: null, retainedRendererAttached: null, rendererKind: null, rendererEvent: null, frameGaps: []};
        window.__normalTimingActiveRow = row;
        window.__normalTimings.push(sample);
        let previousFrame = sample.start;
        const poll = () => {
          if (!window.__normalTimingActive) return;
          const now = performance.now();
          if (sample.frameGaps.length < 32) sample.frameGaps.push(now - previousFrame);
          previousFrame = now;
          const container = terminals.get(item)?.container;
          const screen = container?.querySelector('.xterm-screen');
          if (!before && sample.retainedRendererAttached === null && screen?.isConnected
              && terminalIsVisible(item, container, {measure: false})) {
            sample.retainedRendererAttached = now - sample.start;
            sample.rendererKind = screen.querySelector('.xterm-rows') ? 'dom' : screen.querySelector('canvas') ? 'canvas' : 'unknown';
          }
          if (tabberNormalSelections.includes(item) === !before) {
            if (sample.highlight === null && row.classList.contains('tabber-active-normal') === !before) sample.highlight = performance.now() - sample.start;
            if (!before && document.activeElement === terminals.get(item)?.term?.textarea && sample.focus === null) sample.focus = performance.now() - sample.start;
          }
          if (sample.highlight === null || (!before && (sample.focus === null || sample.paint === null))) requestAnimationFrame(poll);
        };
        if (!before) {
          const terminal = terminals.get(item);
          const subscription = terminal?.term?.onRender(() => {
            if (sample.rendererEvent === null) sample.rendererEvent = performance.now() - sample.start;
            requestAnimationFrame(() => { if (sample.paint === null) sample.paint = performance.now() - sample.start; });
            subscription?.dispose();
          });
          window.__normalTimingCleanups.push(() => subscription?.dispose());
        }
        requestAnimationFrame(poll);
      };
      document.addEventListener('pointerdown', window.__normalTimingListener, true);
    """)
    for index in range(20):
        target = sessions[index % 2]
        select_row(target)
        selected([target])
        wait.until(lambda browser: browser.execute_script("const sample = window.__normalTimings.at(-1); return sample?.highlight !== null && sample?.focus !== null && sample?.paint !== null"))
    timings = driver.execute_script("""
      window.__normalTimingActive = false;
      document.removeEventListener('pointerdown', window.__normalTimingListener, true);
      window.__normalTimingCleanups.forEach(dispose => dispose());
      return window.__normalTimings;
    """)
    print('Go Normal endpoint samples: ' + json.dumps(timings, sort_keys=True))
    print('Go Normal resources after: ' + json.dumps(normal_journey_resource_snapshot(driver, tmux), sort_keys=True))
    metrics = {"cold_open_wall_ms": cold_open_ms}
    for field in ('highlight', 'focus', 'paint'):
        values = sorted(item[field] for item in timings if item[field] is not None)
        metrics[field + '_p95_ms'] = values[max(0, math.ceil(len(values) * .95) - 1)]
    # The selected shell receives native key input through real xterm transport.
    driver.execute_script("""
      window.__echoTimings = [];
      const term = terminals.get(arguments[0]).term;
      window.__normalEchoSubscription = term.onData(data => {
        if (data !== 'z') return;
        const start = performance.now();
        const subscription = term.onRender(() => {
          requestAnimationFrame(() => window.__echoTimings.push(performance.now() - start));
          subscription.dispose();
        });
      });
    """, retained)
    textarea = driver.find_element('css selector', f'#panel-{retained} .xterm-helper-textarea')
    for index in range(20):
        textarea.send_keys('z')
        wait.until(lambda browser: browser.execute_script('return window.__echoTimings.length >= arguments[0]', index + 1))
    echo = sorted(driver.execute_script('window.__normalEchoSubscription.dispose(); return window.__echoTimings'))
    metrics['echo_p95_ms'] = echo[math.ceil(len(echo) * .95) - 1]
    captured = subprocess.check_output((*tmux, 'capture-pane', '-p', '-J', '-t', '=' + retained + ':')).decode()
    assert 'z' * 20 in captured, captured
    textarea.send_keys(Keys.CONTROL, 'u')
    # Close fixture startup tips through their normal UI before admitting a drag.
    for close in driver.find_elements("css selector", "[data-toast-close]"):
        if close.is_displayed():
            close.click()
    # Conversation chrome belongs to the sidebar; each terminal has a headerless singleton.
    geometry = driver.execute_script('''
      const groups = dockviewLayoutState.api.groups;
      const row = document.querySelector('#panel-__tabber__ .file-tree-row[data-tabber-session="' + arguments[1] + '"]');
      const title = row.querySelector('.tabber-normal-primary').getBoundingClientRect();
      const description = row.querySelector('.tabber-normal-description').getBoundingClientRect();
      const dateElement = row.querySelector('.file-tree-date');
      const date = dateElement.getBoundingClientRect();
      const contentGroups = groups.filter(group => group.panels.some(panel => panel.id !== '__tabber__'));
      return {singleton: groups.every(group => group.panels.length === 1),
        hidden: contentGroups.length > 0 && contentGroups.every(group => group.header.hidden),
        twoLineClass: row.classList.contains('tabber-normal-row'),
        secondLine: description.top >= title.bottom, dateVisible: date.width > 0 && dateElement.textContent.trim().endsWith('ago'),
        dateAligned: !date.width || Math.abs(title.top-date.top) < 4,
        rects: {title: title.toJSON(), description: description.toJSON(), date: date.toJSON()},
        columns: getComputedStyle(row).gridTemplateColumns};
    ''', sessions, retained)
    assert all(geometry.values()), geometry
    # Native held sidebar drag may not create a preview or change layout in Normal mode.
    tab = row(retained)
    before_selections = driver.execute_script('return [...tabberNormalSelections]')
    # A pointerdown still toggles its row; drag admission must remain blocked after that toggle.
    ActionChains(driver).move_to_element(tab).click_and_hold().perform()
    wait.until(lambda browser: browser.execute_script('return !tabberNormalSelections.includes(arguments[0])', retained))
    before = driver.execute_script('return layoutSlotsSignature(layoutSlots)')
    ActionChains(driver).move_by_offset(100, 100).perform()
    assert driver.execute_script("return !dockviewLayoutState.tabPointerDrag && !document.querySelector('.dv-drop-target')")
    ActionChains(driver).release().perform()
    assert driver.execute_script('return layoutSlotsSignature(layoutSlots)') == before
    select_row(retained)
    wait.until(lambda browser: browser.execute_script('return tabberNormalSelections.includes(arguments[0])', retained))
    driver.execute_script('storeTabberNormalSelections(arguments[0]);', before_selections)
    selected(before_selections)
    print('Go Normal native timings: ' + json.dumps(metrics, sort_keys=True))
    print('Go Normal warm costs: ' + json.dumps(driver.execute_script('return clientPerfSummary().map(({name,count,avgMs,maxMs})=>({name,count,avgMs,maxMs}))'), sort_keys=True))
    print('Go Normal refresh owners: ' + json.dumps(driver.execute_script('refreshTabberPanels = window.__normalOriginalRefresh; return window.__normalRefreshOwners'), sort_keys=True))
    # Rapid switching leaves only the last selected terminal visible.
    rapid_row = row(sessions[2])
    ActionChains(driver, duration=0).click(rapid_row).click(rapid_row).perform()
    selected([])
    select_row(retained)
    selected([retained])
    assert driver.execute_script("return terminals.get(arguments[0]) === window.__retainedTerminal", retained)
    retained_field = driver.find_element('css selector', f'#panel-{retained} .xterm-helper-textarea')
    retained_field.send_keys(Keys.CONTROL, 'u')
    retained_field.send_keys("seq 1 150", Keys.ENTER)
    wait.until(lambda browser: int(subprocess.check_output((*tmux, 'display-message', '-p', '-t', '=' + retained + ':', '#{history_size}')).decode().strip() or '0') > 100)
    subprocess.run((*tmux, 'copy-mode', '-t', '=' + retained + ':'), check=True)
    subprocess.run((*tmux, 'send-keys', '-X', '-t', '=' + retained + ':', 'page-up'), check=True)
    def scroll_position():
        return subprocess.check_output((*tmux, 'display-message', '-p', '-t', '=' + retained + ':', '#{scroll_position}')).decode().strip()
    wait.until(lambda browser: int(scroll_position() or '0') > 0)
    wait.until(lambda browser: browser.execute_script("const term=terminals.get(arguments[0]).term; return Array.from({length:term.rows},(_,i)=>term.buffer.active.getLine(term.buffer.active.viewportY+i)?.translateToString(true)||'').some(line=>line.includes('['))", retained))
    viewport = driver.execute_script("""
      const term = terminals.get(arguments[0]).term;
      const lines = Array.from({length: term.rows}, (_, index) => term.buffer.active.getLine(term.buffer.active.viewportY + index)?.translateToString(true) || '');
      return {cols: term.cols, rows: term.rows, anchor: lines.find(line => /^\\d+$/.test(line.trim()))};
    """, retained)
    assert viewport['anchor'], viewport
    # Hiding and reopening a terminal preserves the visible history and identity.
    select_row(sessions[2])
    selected([sessions[2]])
    select_row(retained)
    selected([retained])
    wait.until(lambda browser: browser.execute_script("const term=terminals.get(arguments[0]).term; return Array.from({length:term.rows},(_,i)=>term.buffer.active.getLine(term.buffer.active.viewportY+i)?.translateToString(true)||'').includes(arguments[1]);", retained, viewport['anchor']))
    assert driver.execute_script("""
      const term = terminals.get(arguments[0]).term;
      const lines = Array.from({length: term.rows}, (_, index) => term.buffer.active.getLine(term.buffer.active.viewportY + index)?.translateToString(true) || '');
      return terminals.get(arguments[0]) === window.__retainedTerminal && lines.includes(arguments[1]);
    """, retained, viewport['anchor'])
    assert int(scroll_position() or '0') > 0
    subprocess.run((*tmux, 'send-keys', '-X', '-t', '=' + retained + ':', 'cancel'), check=True)
    if file_path:
        result = driver.execute_async_script('''
          const done = arguments[arguments.length-1];
          openFileInEditor(arguments[0], {name:'normal-editor.js'}, {userInitiated:true, viewMode:'edit'}).then(done, error => done({error:String(error)}));
        ''', str(file_path))
        assert isinstance(result, str), result
        wait.until(lambda browser: browser.execute_script('return panelNodes.get(arguments[0])?._cmView && itemInLayout(arguments[0]);', result))
        field = driver.execute_script('return panelNodes.get(arguments[0])._cmView.contentDOM;', result)
        field.click()
        field.send_keys(Keys.CONTROL, Keys.END)
        for character in '\n// normal retained draft':
            field.send_keys(character)
        state = driver.execute_script('''
          const panel=panelNodes.get(arguments[0]); window.__normalFilePanel=panel; window.__normalFileView=panel._cmView;
          return {text:panel._cmView.state.doc.toString(), path:arguments[1]};
        ''', result, str(file_path))
        assert state['text'].endswith('// normal retained draft'), state
        selector = f'#panel-__tabber__ .file-tree-row[data-tabber-item="{result}"]'
        click_visible_selector(driver, selector)
        wait.until(lambda browser: browser.execute_script('return !itemInLayout(arguments[0]);', result))
        click_visible_selector(driver, selector)
        wait.until(lambda browser: browser.execute_script('return itemInLayout(arguments[0]);', result))
        assert driver.execute_script('''
          return panelNodes.get(arguments[0]) === window.__normalFilePanel
            && panelNodes.get(arguments[0])._cmView === window.__normalFileView
            && window.__normalFileView.state.doc.toString() === arguments[1]
            && fileState.get(arguments[2]).content === arguments[1]
          && dockviewLayoutState.api.groups.every(group => group.panels.length === 1)
          && dockviewLayoutState.api.groups.filter(group => group.panels.some(panel => panel.id !== '__tabber__'))
            .every(group => group.header.hidden);
        ''', result, state['text'], str(file_path))
        selected([result])
    # Advanced re-enables the same drag owner and native Dockview admission.
    driver.find_element('css selector', '[data-tabber-advanced]').click()
    assert driver.execute_script('return tabberIsAdvanced() && tabDragAllowed()')
    driver.execute_async_script('''
      const done=arguments[arguments.length-1];
      Promise.resolve(applyLayoutSlots(layoutSlotsForItems([arguments[0], arguments[1]], 'split'))).then(()=>done(true),error=>done(String(error)));
    ''', sessions[2], retained)
    source_tab = driver.find_element('css selector', f'.dockview-pane-tab[data-pane-tab="{sessions[2]}"]')
    target_body = driver.find_element('css selector', f'#panel-{retained} .xterm')
    points = driver.execute_script('''
      const point = node => {const rect = node.getBoundingClientRect(); return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};};
      return {start: point(arguments[0]), end: point(arguments[1])};
    ''', source_tab, target_body)
    assert driver.execute_script("return document.elementFromPoint(arguments[0],arguments[1])?.closest('.dockview-pane-tab')===arguments[2]", points['start']['x'], points['start']['y'], source_tab)
    cdp_drag_hold(driver, points['start'], points['end'])
    wait.until(lambda browser: browser.execute_script("return Array.from(document.querySelectorAll('.dv-drop-target-selection, .dv-drop-target-anchor')).some(node => node.getBoundingClientRect().width > 0 && node.getBoundingClientRect().height > 0)"))
    cdp_release(driver, points['end'])
    wait.until(lambda browser: browser.execute_script("const slot=slotForItem(arguments[0]); return slot && layoutSlots[slot].tabs.includes(arguments[1])", retained, sessions[2]))
    driver.execute_async_script("""
      const done = arguments[arguments.length - 1];
      setTabberMode(window.__tabberJourneySaved.mode);
      storeTabberNormalSelections(window.__tabberJourneySaved.selections);
      Promise.resolve(saveTabberModePreference(window.__tabberJourneySaved.mode))
        .then(() => applyLayoutSlots(window.__tabberJourneySaved.layout))
        .then(() => done(true), error => done(String(error)));
    """)
    print('Go Normal native costs: ' + json.dumps(driver.execute_script('return clientPerfSummary().map(({name,count,avgMs,maxMs})=>({name,count,avgMs,maxMs}))'), sort_keys=True))
    # Fixed latency ceilings belong to the exclusive certification phase; this correctness
    # journey runs beside other gate lanes and records timings without measuring host contention.
    return metrics


def normal_multiwindow_journey(driver, tmux, session):
    """Exercise real window-view creation and per-window descriptions in Normal mode."""
    driver.execute_script("""
      window.__tabberMultiRequests = [];
      window.__tabberMultiOriginalFetchJson = apiFetchJson;
      apiFetchJson = async (...args) => {
        try {
          const result = await window.__tabberMultiOriginalFetchJson(...args);
          if (String(args[0]).includes('/api/tabber-')) window.__tabberMultiRequests.push({url:String(args[0]), ok:true, result});
          return result;
        } catch (error) {
          if (String(args[0]).includes('/api/tabber-')) window.__tabberMultiRequests.push({url:String(args[0]), ok:false, error:String(error?.stack || error)});
          throw error;
        }
      };
    """)
    driver.execute_async_script("""
      const done = arguments[arguments.length - 1];
      setTabberMode('normal');
      storeTabberNormalSelections([]);
      Promise.resolve(applyTabberNormalSelections()).then(() => done(true), error => done(String(error)));
    """)
    wait = WebDriverWait(driver, 15)
    selector = f'#panel-__tabber__ .file-tree-row[data-tabber-type="normal"][data-tabber-session="{session}"]'
    wait.until(lambda browser: browser.execute_script("return [...document.querySelectorAll(arguments[0])].some(row => row.dataset.tabberWindow === '0') && [...document.querySelectorAll(arguments[0])].some(row => row.dataset.tabberWindow === '1')", selector))
    window_labels = driver.execute_script("""
      return [...document.querySelectorAll(arguments[0])].map(row => ({
        index: row.dataset.tabberWindow,
        windowId: row.dataset.tabberWindowId,
        label: row.querySelector('.tabber-normal-window-identity')?.textContent.trim() || '',
      }));
    """, selector)
    assert any(row['index'] == '0' and '0:codex' in row['label'] for row in window_labels), window_labels
    assert any(row['index'] == '1' and '1:bash' in row['label'] for row in window_labels), window_labels
    first_window_id = next(row['windowId'] for row in window_labels if row['index'] == '0')
    second_window_id = next(row['windowId'] for row in window_labels if row['index'] == '1')
    wait.until(lambda browser: browser.execute_script("return activePaneItems().join(',') === '__tabber__' && !dockviewTabContentInteractionSuppressed()"))

    def press_numeric_session_shortcut():
        driver.execute_script("""
          const panel = document.querySelector('#panel-__tabber__ .file-explorer-changes-panel');
          window.__tabberNumericTrace = {session:String(arguments[0]), direct:tabberSessionForNumericKey(String(arguments[0]))};
          const originalRow = tabberNormalRowForSession;
          tabberNormalRowForSession = (...args) => {
            const row = originalRow(...args);
            window.__tabberNumericTrace.row = row ? {type:row.dataset.tabberType,window:row.dataset.tabberWindow,
              windowView:row.dataset.tabberNormalWindowView,paneItem:row.dataset.tabberPaneItem} : null;
            return row;
          };
          const event = new KeyboardEvent('keydown', {key:String(arguments[0]), bubbles:true, cancelable:true});
          panel.dispatchEvent(event);
          window.__tabberNumericTrace.prevented = event.defaultPrevented;
          tabberNormalRowForSession = originalRow;
        """, session)

    press_numeric_session_shortcut()
    try:
        wait.until(lambda browser: browser.execute_script("return tabberNormalSelections.length === 1 && String(tabberNormalSelections[0]).startsWith('__yomux_view_') && terminals.get(tabberNormalSelections[0])?.socket?.readyState === 1"))
    except TimeoutException as error:
        state = driver.execute_script("""
          const panel = document.querySelector('#panel-__tabber__ .file-explorer-changes-panel');
          return {numeric_session:arguments[0], sessions:[...sessions], ordered:tabberOrderedSessions(),
            selections:[...tabberNormalSelections], mode:tabberMode, panelFound:Boolean(panel),
            rows:[...document.querySelectorAll(arguments[1])].map(row=>({window:row.dataset.tabberWindow,
              paneItem:row.dataset.tabberPaneItem,windowId:row.dataset.tabberWindowId,
              selected:row.classList.contains('tabber-active-normal')})),
            views:[...tabberViewSessions],requests:window.__tabberMultiRequests,
            numericTrace:window.__tabberNumericTrace,
            activeElement:document.activeElement?.className || '',focusedPanelItem,
            surface:eventTargetIsFileExplorerSurface(panel), status:document.getElementById('status')?.textContent || ''};
        """, session, selector)
        raise AssertionError(f'Normal numeric selection failed: {json.dumps(state, sort_keys=True)}') from error
    numeric_window = driver.execute_script("return tabberViewDescriptorForSession(tabberNormalSelections[0])?.windowIndex")
    assert numeric_window == 0, {'numeric_session': session, 'selected_window': numeric_window}
    press_numeric_session_shortcut()
    wait.until(lambda browser: browser.execute_script('return tabberNormalSelections.length === 0'))

    def window_row(index):
        return wait.until(EC.element_to_be_clickable(('css selector', f'{selector}[data-tabber-window="{index}"]')))

    def select_window(index):
        click_visible_selector(driver, f'{selector}[data-tabber-window="{index}"]')

    select_window(0)
    try:
        wait.until(lambda browser: browser.execute_script("return tabberNormalSelections.length === 1 && String(tabberNormalSelections[0]).startsWith('__yomux_view_') && tabberViewDescriptorForSession(tabberNormalSelections[0])?.windowIndex === 0 && terminals.get(tabberNormalSelections[0])?.socket?.readyState === 1"))
    except TimeoutException as error:
        state = driver.execute_script("""
          return {selections:[...tabberNormalSelections], mode:tabberMode, rows:[...document.querySelectorAll(arguments[0])].map(row=>({window:row.dataset.tabberWindow,paneItem:row.dataset.tabberPaneItem,windowView:row.dataset.tabberNormalWindowView,classes:row.className,description:row.querySelector('.tabber-normal-description')?.textContent})), status:document.getElementById('status')?.textContent, errors:[...document.querySelectorAll('[role=alert]')].map(node=>node.textContent), activeItems:activePaneItems(), layout:layoutSlotsSignature(layoutSlots), requests:window.__tabberMultiRequests, lastError:window.lastError || ''};
        """, selector)
        raise AssertionError(f'Normal window 0 selection failed: {json.dumps(state, sort_keys=True)}') from error
    first_item = driver.execute_script('return tabberNormalSelections[0]')
    first_window = driver.execute_script('return tabberViewDescriptorForSession(arguments[0])?.windowIndex', first_item)
    assert first_window == 0, {'selected_item': first_item, 'window_index': first_window}
    select_window(0)
    wait.until(lambda browser: browser.execute_script('return tabberNormalSelections.length === 0'))
    select_window(1)
    wait.until(lambda browser: browser.execute_script("return tabberNormalSelections.length === 1 && String(tabberNormalSelections[0]).startsWith('__yomux_view_') && terminals.get(tabberNormalSelections[0])?.socket?.readyState === 1"))
    second_item = driver.execute_script('return tabberNormalSelections[0]')
    second_window = driver.execute_script('return tabberViewDescriptorForSession(arguments[0])?.windowIndex', second_item)
    assert second_window == 1, {'selected_item': second_item, 'window_index': second_window}
    assert first_item != second_item, {'first_window_view': first_item, 'second_window_view': second_item}
    select_window(0)
    wait.until(lambda browser: browser.execute_script("return tabberNormalSelections[0] === arguments[0] && tabberViewDescriptorForSession(arguments[0])?.windowIndex === 0 && terminals.get(arguments[0])?.socket?.readyState === 1", first_item))
    wait.until(lambda browser: browser.execute_script('return document.activeElement === terminals.get(arguments[0])?.term?.textarea', first_item))
    driver.find_element('css selector', f'#panel-{first_item} .xterm-helper-textarea').send_keys("printf 'window-zero-input-ok\\n'", Keys.ENTER)
    wait.until(lambda browser: any('window-zero-input-ok' in line for line in subprocess.check_output((*tmux, 'capture-pane', '-p', '-J', '-t', f'={session}:0')).decode().splitlines()))
    # A cached per-window alias can be left on another linked window. Re-selecting its
    # Tabber row must restore the stable window ID before xterm input is routed.
    subprocess.run((*tmux, 'select-window', '-t', f'={second_item}:0'), check=True)
    stale_window_id = subprocess.check_output((*tmux, 'display-message', '-p', '-t', f'={second_item}:', '#{window_id}')).decode().strip()
    assert stale_window_id == first_window_id, {'stale_alias_window_id': stale_window_id, 'expected_window_zero_id': first_window_id}
    select_window(1)
    wait.until(lambda browser: browser.execute_script("return tabberNormalSelections[0] === arguments[0] && tabberViewDescriptorForSession(arguments[0])?.windowIndex === 1 && terminals.get(arguments[0])?.socket?.readyState === 1", second_item))
    wait.until(lambda browser: browser.execute_script("return document.activeElement === terminals.get(arguments[0])?.term?.textarea", second_item))
    selected_window_id = subprocess.check_output((*tmux, 'display-message', '-p', '-t', f'={second_item}:', '#{window_id}')).decode().strip()
    assert selected_window_id == second_window_id, {'selected_alias_window_id': selected_window_id, 'expected_window_one_id': second_window_id}
    driver.find_element('css selector', f'#panel-{second_item} .xterm-helper-textarea').send_keys("printf 'window-one-bash-input-ok\\n'", Keys.ENTER)
    wait.until(lambda browser: any('window-one-bash-input-ok' in line for line in subprocess.check_output((*tmux, 'capture-pane', '-p', '-J', '-t', f'={session}:1')).decode().splitlines()))
    driver.execute_script("document.querySelector('#panel-__tabber__ .file-explorer-changes-panel').dispatchEvent(new KeyboardEvent('keydown', {key: arguments[0], bubbles: true, cancelable: true}));", session)
    wait.until(lambda browser: browser.execute_script('return tabberNormalSelections.length === 0'))
    select_window(1)
    wait.until(lambda browser: browser.execute_script('return tabberNormalSelections[0] === arguments[0]', second_item))
    wait.until(lambda browser: browser.execute_script("return document.activeElement === terminals.get(arguments[0])?.term?.textarea", second_item))
    row = window_row(1)
    ActionChains(driver).context_click(row).perform()
    menu_button = wait.until(EC.element_to_be_clickable(('xpath', "//*[@role='menu']//*[self::button or @role='menuitem'][normalize-space()='Rename Description']")))
    menu_button.click()
    dialog = wait.until(EC.visibility_of_element_located(('css selector', '.session-rename-dialog')))
    value = dialog.find_element('css selector', '.session-rename-input')
    value.clear()
    value.send_keys('Window one retained description')
    dialog.find_element('css selector', '.session-rename-submit').click()
    wait.until(lambda browser: not browser.find_elements('css selector', '.session-rename-dialog'))
    wait.until(lambda browser: browser.execute_script("return [...document.querySelectorAll(arguments[0])].find(row => row.dataset.tabberWindow === '1')?.querySelector('.tabber-normal-description')?.textContent.trim() === 'Window one retained description'", selector))
    driver.refresh()
    wait.until(lambda browser: browser.execute_script("return typeof apiFetchJson === 'function' && [...document.querySelectorAll(arguments[0])].some(row => row.dataset.tabberWindow === '1' && row.querySelector('.tabber-normal-description')?.textContent.trim() === 'Window one retained description')", selector))
    print('Go Normal multi-window: ' + json.dumps({'session': session, 'window_zero_view': first_item, 'window_one_view': second_item, 'window_labels': window_labels, 'description_persisted': True, 'codex_window_zero_input': True, 'bash_window_one_input': True}, sort_keys=True))


def isolated_attention_journey(driver, tmux, sessions):
    """Synthetic classifier contract on the caller's isolated Go runtime only."""
    retained, other, replacement = sessions
    identity = subprocess.run((*tmux, 'display-message', '-p', '-t', '=' + retained + ':',
                               '#{window_index}\t#{pane_id}\t#{pane_pid}'),
                              check=True, capture_output=True, text=True).stdout.strip().split('\t')
    wait = WebDriverWait(driver, 15)

    def click_row(session):
        click_visible_selector(driver, f'#panel-__tabber__ .file-tree-row[data-tabber-type="normal"][data-tabber-session="{session}"]')

    def assert_unacknowledged(key):
        try:
            wait.until(lambda browser: browser.execute_script("""
          const [session,index,key] = arguments;
          const target = agentWindowActivityAcknowledgementTarget(session,index);
          const row = document.querySelector(`#panel-__tabber__ [data-tabber-session="${CSS.escape(session)}"]`);
          return target?.ackKey === key && !attentionAcknowledgementKeyIsRecorded(key)
            && row?.querySelector('.status-indicator--attention') !== null
            && terminals.get(session) === window.__attentionFixture.terminal;
            """, retained, identity[0], key))
        except TimeoutException as error:
            state = driver.execute_script("""
              const [session,index,key] = arguments;
              return {target:agentWindowActivityAcknowledgementTarget(session,index),
                model:windowViewModel(session,index),recorded:attentionAcknowledgementKeyIsRecorded(key),
                rows:[...document.querySelectorAll(`#panel-__tabber__ [data-tabber-session="${CSS.escape(session)}"]`)].map(row=>row.innerHTML),
                terminalRetained:terminals.get(session)===window.__attentionFixture.terminal};
            """, retained, identity[0], key)
            raise AssertionError(state) from error

    try:
        driver.execute_async_script("""
          const done = arguments[arguments.length-1];
          window.__attentionFixture = {mode:tabberMode, selections:[...tabberNormalSelections], layout:cloneLayoutSlots(layoutSlots)};
          setTabberMode('normal'); storeTabberNormalSelections(arguments[0]);
          Promise.resolve(applyTabberNormalSelections()).then(()=>done(true), error=>done(String(error)));
        """, [retained])
        wait.until(lambda browser: browser.execute_script("return terminals.get(arguments[0])?.socket?.readyState===1", retained))
        driver.execute_script("window.__attentionFixture.terminal=terminals.get(arguments[0]);", retained)
        for generation in (1, 2):
            key = driver.execute_script("""
              const [session,index,pane,pid,generation] = arguments;
              const signature = `fixture-g${generation}`;
              const key = attentionAcknowledgementKey(['agent-window',session,index,pane,'codex',STATE_KEY.needsInput,signature]);
              const previous = windowViewModel(session,index);
              const agent = {kind:'codex', state:STATE_KEY.needsInput,session,window_index:index,window:index,
                pane,pane_target:pane,pid:Number(pid),process_instance_id:previous?.process_instance_id || '',
                current:true,window_active:true,attention_signature:signature,attention_key:key,attention_acknowledged:false};
              const revision = Math.max(agentWindowSnapshotRevision(tabberActivityPayload),
                ...Array.from(autoApproveStates.values(),agentWindowSnapshotRevision))+1;
              const topology = Math.max(tmuxTopologyGeneration,transcriptMetadataState.rosterGeneration);
              const common = {topology_generation:topology,session_order:sessions.slice(),
                agent_window_status_fresh:true,agent_window_snapshot_revision:revision};
              const activity = {...tabberActivityPayload,...common,sessions:transcriptMetadataState.payload.sessions,
                agent_windows:{...tabberActivityPayload.agent_windows,[session]:[agent]}};
              const auto = {...common,sessions:{...Object.fromEntries(autoApproveStates),
                [session]:{...autoApproveStates.get(session),target:session,agent_windows:[agent],
                  agent_window_status_fresh:true,agent_window_snapshot_revision:revision}}};
              if (!applyTabberActivityPayload(activity) || applyAutoApprovePayload(auto,{deferForMetadata:false}).applied !== true)
                throw new Error('fixture snapshots rejected');
              refreshAgentWindowActivityDisplays([session]);
              return key;
            """, retained, *identity, generation)
            assert_unacknowledged(key)
            click_row(replacement)
            wait.until(lambda browser: browser.execute_script('return tabberNormalSelections.length === 1 && tabberNormalSelections[0] === arguments[0];', replacement))
            assert_unacknowledged(key)
            if generation == 1:
                click_row(retained)
                wait.until(lambda browser: browser.execute_script('return tabberNormalSelections.length === 1 && tabberNormalSelections[0] === arguments[0];', retained))
                click_visible_selector(driver, f'#panel-{retained} .xterm-screen')
                wait.until(lambda browser: browser.execute_script('return attentionAcknowledgementKeyIsRecorded(arguments[0]) && agentWindowActivityAcknowledgementTarget(arguments[1],arguments[2]) === null;', key, retained, identity[0]))
                click_row(other)
                wait.until(lambda browser: browser.execute_script('return tabberNormalSelections.length === 1 && tabberNormalSelections[0] === arguments[0];', other))
        print('Go Normal attention: retained g1 badge; native focus acknowledged exact key; new g2 badge retained')
    finally:
        # Navigation retires fixture timers, requests and synthetic revisions together;
        # no monotonic request clock is rewound in the existing document.
        driver.execute_async_script("""
          const done=arguments[arguments.length-1], saved=window.__attentionFixture;
          if (!saved) {done(true);return;}
          setTabberMode(saved.mode);storeTabberNormalSelections(saved.selections);
          Promise.resolve(applyLayoutSlots(saved.layout)).then(()=>done(true),()=>done(false));
        """)
        driver.refresh()
