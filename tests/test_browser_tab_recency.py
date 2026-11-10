# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import pytest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tests.tmux_runtime import run_isolated_tmux


def click_navigation(browser, control):
    # Layout publication can replace topbar nodes between WebDriver commands. A
    # native viewport click hits the current control rather than a retired DOM handle.
    x, y = browser.execute_script("""
      const button=document.getElementById(arguments[0]), rect=button.getBoundingClientRect();
      if (button.disabled || !rect.width || !rect.height) throw new Error('navigation control unavailable');
      return [Math.round(rect.x+rect.width/2),Math.round(rect.y+rect.height/2)];
    """, control)
    actions = ActionChains(browser)
    actions.w3c_actions.pointer_action.move_to_location(x, y)
    actions.w3c_actions.pointer_action.click()
    actions.perform()


@pytest.mark.parametrize('automatic', [False, True])
def test_global_tab_recency_native_navigation_close_and_tmux_exit(browser, isolated_dev_server_factory, request, automatic):
    server = isolated_dev_server_factory('tab-recency', session_count=3)
    request.addfinalizer(lambda: browser.get('about:blank'))
    sessions = server.tmux.sessions
    a, b, c = sessions
    path = server.paths.home_dir / 'recency.js'
    path.write_text('const answer = 42;\n', encoding='utf-8')
    browser.get(server.base_url + f'/?sessions={a}&layout=left&tabs=left:{a},{b},{c}')
    setup = browser.execute_async_script("""
      const [sessions, path, automatic, done] = arguments;
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof applyLayoutSlots === 'function'
          && typeof openFileInEditor === 'function' && initialAppShellPainted, {timeoutMs:15000});
        setTabberMode('advanced');
        autoFocusEnabled = automatic;
        const item = await openFileInEditor(path, basenameOf(path), {viewMode:'edit', userInitiated:true});
        const slots = emptyLayoutSlots();
        slots[layoutTreeKey] = splitNode('row', leafNode('left'), leafNode('right'), 60);
        slots.left = paneStateWithTabs(sessions, sessions[0]);
        slots.right = paneStateWithTabs([item], item);
        applyLayoutSlots(slots, {prune:false});
        await window.__yomuxTestWaitFor(() => panelNodes.get(item)?._cmView
          && terminals.get(sessions[0])?.container?.querySelector('textarea'), {timeoutMs:15000});
        setFocusedPanelItem(sessions[0]);
        editorNav.stack = []; editorNav.index = -1; recordEditorNav(sessions[0]);
        return item;
      })().then(done, error => done({error:String(error.stack || error)}));
    """, sessions, str(path), automatic)
    assert isinstance(setup, str), setup
    editor = setup

    def click_tab(item):
        selector = '[data-pane-tab="' + item.replace('"', '\\"') + '"]'
        tab = next(tab for tab in browser.find_elements(By.CSS_SELECTOR, selector) if tab.is_displayed())
        tab.click()

    def close_tab(item, keyboard=False):
        selector = '[data-pane-tab="' + item.replace('"', '\\"') + '"]'
        tab = next(tab for tab in browser.find_elements(By.CSS_SELECTOR, selector) if tab.is_displayed())
        ActionChains(browser).move_to_element(tab).perform()
        close = tab.find_element(By.CSS_SELECTOR, '[data-pane-tab-close]')
        if keyboard:
            browser.execute_script('arguments[0].focus()', close)
            close.send_keys(Keys.ENTER)
        else:
            close.click()

    def assert_focus(item, terminal=True):
        try:
            WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("""
              const item=arguments[0], terminal=arguments[1], panel=panelNodes.get(item);
              const content=terminal ? terminals.get(item)?.container?.querySelector('textarea') : panel?._cmView?.contentDOM;
              return focusedPanelItem===item && activeItemForSide(slotForItem(item))===item
                && content && (content===document.activeElement || content.contains(document.activeElement));
            """, item, terminal), message=str(browser.execute_script("return {focus:focusedPanelItem,active:activePaneItems(),dom:document.activeElement?.tagName+' '+document.activeElement?.className,nav:editorNav}")))
        except TimeoutException:
            raise AssertionError(browser.execute_script("""
              const item=arguments[0], panel=panelNodes.get(item), container=terminals.get(item)?.container;
              return {focus:focusedPanelItem,nav:editorNav,dom:document.activeElement?.tagName+' '+document.activeElement?.className,
                panel:panel?.className,rect:panel?.getBoundingClientRect().toJSON(),container:container?.getBoundingClientRect().toJSON(),
                terminalPane:document.getElementById('terminal-pane-'+item)?.className};
            """, item)) from None
        assert browser.execute_script('return focusedPanelItem') == item

    for item in [a, b, c]:
        click_tab(item)
        assert_focus(item)
    expected = [a, b, c]
    assert browser.execute_script('return editorNav.stack') == expected
    click_navigation(browser, 'topbarNavBack')
    assert_focus(b)
    click_navigation(browser, 'topbarNavBack')
    assert_focus(a)
    click_navigation(browser, 'topbarNavForward')
    assert_focus(b)
    assert browser.execute_script('return editorNav.stack') == expected
    # The visited order, rather than the Back cursor, determines closure focus.
    close_tab(b)
    assert_focus(c)
    assert browser.execute_script('return editorNav.stack') == [a, c]
    click_tab(editor)
    assert_focus(editor, False)
    close_tab(a, keyboard=automatic)
    assert_focus(editor, False)
    assert browser.execute_script('return editorNav.stack') == [c, editor]
    # Dirty-close cancellation must leave history and both focus owners alone.
    browser.execute_script("fileEditorAutosaveEnabled=false; const panel=panelNodes.get(arguments[1]); panel._cmView.dispatch({changes:{from:0,insert:'// local edit\\n'}}); window.__recencyClose=closeFileTab(arguments[0],{item:arguments[1]})", str(path), editor)
    WebDriverWait(browser, 5).until(lambda driver: driver.find_element(By.CSS_SELECTOR, '.file-editor-close-dialog [data-dialog-action="cancel"]').is_displayed())
    browser.find_element(By.CSS_SELECTOR, '.file-editor-close-dialog [data-dialog-action="cancel"]').click()
    cancelled = browser.execute_async_script("window.__recencyClose.then(arguments[arguments.length-1])")
    assert cancelled is False
    assert_focus(editor, False)
    assert browser.execute_script('return editorNav.stack') == [c, editor]
    click_tab(c)
    assert_focus(c)
    close_tab(editor)
    WebDriverWait(browser, 5).until(lambda driver: driver.find_element(By.CSS_SELECTOR, '.file-editor-close-dialog [data-dialog-action="discard"]').is_displayed())
    browser.find_element(By.CSS_SELECTOR, '.file-editor-close-dialog [data-dialog-action="discard"]').click()
    assert_focus(c)
    assert browser.execute_script('return editorNav.stack') == [c]
    browser.execute_async_script("openFileInEditor(arguments[0],basenameOf(arguments[0]),{viewMode:'edit',userInitiated:true}).then(arguments[arguments.length-1])", str(path))
    assert_focus(editor, False)
    click_tab(c)
    assert_focus(c)
    run_isolated_tmux(server.tmux, 'kill-session', '-t', f'={c}:')
    assert_focus(editor, False)
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script('return !editorNav.stack.includes(arguments[0])', c))
    assert browser.execute_script('return editorNav.stack') == [editor]


def test_normal_tab_recency_reveals_hidden_tabs(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('normal-tab-recency', session_count=2)
    request.addfinalizer(lambda: browser.get('about:blank'))
    first, second = server.tmux.sessions
    browser.get(server.base_url)
    setup = browser.execute_async_script("""
      const [first, done] = arguments;
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof applyTabberNormalSelections === 'function'
          && initialAppShellPainted, {timeoutMs:15000});
        setTabberMode('normal');
        storeTabberNormalSelections([first]);
        await applyTabberNormalSelections(first);
        editorNav.stack = []; editorNav.index = -1; recordEditorNav(first);
        return true;
      })().then(done, error => done({error:String(error.stack || error)}));
    """, first)
    assert setup is True, setup
    row = WebDriverWait(browser, 10).until(lambda driver: next((row for row in driver.find_elements(
        By.CSS_SELECTOR, f'.file-tree-row[data-tabber-pane-item="{second}"]') if row.is_displayed()), False))
    row.click()

    def assert_selection(item):
        WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("""
          const item=arguments[0], content=terminals.get(item)?.container?.querySelector('textarea');
          return tabberNormalSelections.length===1 && tabberNormalSelections[0]===item
            && itemIsActivePaneTab(item) && focusedPanelItem===item && content===document.activeElement;
        """, item))

    assert_selection(second)
    assert browser.execute_script('return editorNav.stack') == [first, second]
    click_navigation(browser, 'topbarNavBack')
    assert_selection(first)
    click_navigation(browser, 'topbarNavForward')
    assert_selection(second)
    assert browser.execute_script('return editorNav.stack') == [first, second]


def test_delayed_file_read_preserves_newer_native_tab_visit(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('tab-recency-read', session_count=2)
    request.addfinalizer(lambda: browser.get('about:blank'))
    a, b = server.tmux.sessions
    path = server.paths.home_dir / 'delayed.js'
    path.write_text('const loaded = true;\n', encoding='utf-8')
    browser.get(server.base_url + f'/?sessions={a}&layout=left&tabs=left:{a},{b}')
    started = browser.execute_async_script("""
      const [path, done] = arguments;
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof openFileInEditor === 'function' && initialAppShellPainted, {timeoutMs:10000});
        setTabberMode('advanced');
        const fetch=window.fetch;
        window.fetch=(url,options)=> {
          if (String(url).startsWith('/api/fs/read') && String(url).includes(encodeURIComponent(path))) {
            return new Promise((resolve,reject)=>{window.__releaseRead=()=>fetch(url,options).then(resolve,reject);});
          }
          return fetch(url,options);
        };
        window.__readOpen=openFileInEditor(path,basenameOf(path),{viewMode:'edit',userInitiated:true});
        await window.__yomuxTestWaitFor(() => window.__releaseRead && itemInLayout(fileEditorItemFor(path)), {timeoutMs:10000});
        return true;
      })().then(done,error=>done({error:String(error.stack || error)}));
    """, str(path))
    assert started is True, started
    tab = next(tab for tab in browser.find_elements(By.CSS_SELECTOR, f'[data-pane-tab="{b}"]') if tab.is_displayed())
    tab.click()
    before = browser.execute_script('return {focus:editorNav.focusedItem,stack:[...editorNav.stack],index:editorNav.index}')
    generation = browser.execute_script('return openFileContentGeneration.get(arguments[0]) || 0', str(path))
    result = browser.execute_async_script("""
      const done=arguments[arguments.length-1];
      window.__releaseRead();
      window.__readOpen.then(()=>done({focus:editorNav.focusedItem,stack:[...editorNav.stack],index:editorNav.index}),error=>done({error:String(error)}));
    """)
    assert result == before
    assert result['focus'] == b
    assert browser.execute_script('return openFileContentGeneration.get(arguments[0])', str(path)) > generation


def test_preview_and_finder_history_focus_without_automatic_following(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('tab-recency-preview')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'preview.md'
    path.write_text('# Preview focus\n', encoding='utf-8')
    browser.get(server.base_url)
    setup = browser.execute_async_script("""
      const [path,done]=arguments;
      (async()=>{
        await window.__yomuxTestWaitFor(()=>typeof openFileInEditor==='function' && initialAppShellPainted,{timeoutMs:10000});
        setTabberMode('advanced'); autoFocusEnabled=false;
        const item=await openFileInEditor(path,basenameOf(path),{viewMode:'preview',userInitiated:true});
        await window.__yomuxTestWaitFor(()=>panelNodes.get(item)?._pmView?.hasFocus(),{timeoutMs:10000});
        await openFileExplorerPane();
        return {item,finder:fileExplorerItemId};
      })().then(done,error=>done({error:String(error.stack || error)}));
    """, str(path))
    assert 'error' not in setup, setup
    finder = next(tab for tab in browser.find_elements(By.CSS_SELECTOR, f'[data-pane-tab="{setup["finder"]}"]') if tab.is_displayed())
    finder.click()
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script('return focusedPanelItem===arguments[0] && panelNodes.get(arguments[0]).contains(document.activeElement)', setup['finder']))
    click_navigation(browser, 'topbarNavBack')
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("""
      const panel=panelNodes.get(arguments[0]);
      return focusedPanelItem===arguments[0] && panel.querySelector('.file-editor-preview-pane-panel').contains(document.activeElement);
    """, setup['item']))
    click_navigation(browser, 'topbarNavForward')
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script('return focusedPanelItem===arguments[0] && panelNodes.get(arguments[0]).contains(document.activeElement)', setup['finder']))


def test_closing_finder_opened_editor_returns_to_previously_selected_tab(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('finder-close-return', session_count=2)
    request.addfinalizer(lambda: browser.get('about:blank'))
    first, selected = server.tmux.sessions
    path = server.paths.home_dir / 'finder-close.md'
    path.write_text('# Finder close return\n', encoding='utf-8')
    browser.get(server.base_url + f'/?sessions={first},{selected}&layout=left&tabs=left:{first},{selected}')
    setup = browser.execute_async_script("""
      const [path,done]=arguments;
      (async()=>{
        await window.__yomuxTestWaitFor(()=>typeof openFileExplorerPane==='function' && initialAppShellPainted,{timeoutMs:10000});
        await openFileExplorerPane();
        await window.__yomuxTestWaitFor(()=>panelNodes.get(fileExplorerItemId)?.querySelector('.file-tree-row[data-path="'+CSS.escape(path)+'"]'),{timeoutMs:10000});
        return {finder:fileExplorerItemId};
      })().then(done,error=>done({error:String(error.stack || error)}));
    """, str(path))
    assert 'error' not in setup, setup

    selected_tab = next(tab for tab in browser.find_elements(By.CSS_SELECTOR, f'[data-pane-tab="{selected}"]') if tab.is_displayed())
    selected_tab.click()
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script('return focusedPanelItem===arguments[0]', selected))
    browser.execute_script("""
      const row=panelNodes.get(fileExplorerItemId)?.querySelector('.file-tree-row[data-path="'+CSS.escape(arguments[0])+'"]');
      row.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:32,clientY:32}));
    """, str(path))
    browser.execute_script("""
      const button=[...document.querySelectorAll('.file-context-menu button')].find(node=>node.textContent.trim().endsWith('Preview in new tab'));
      if (!button) throw new Error('Finder Preview in new tab action missing');
      button.click();
    """)
    item = f'file:{path}'
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script('return itemInLayout(arguments[0]) && focusedPanelItem===arguments[0] && fileStateFor(arguments[1])?.loading!==true', item, str(path)))
    assert browser.execute_async_script("""
      closeFileTab(fileItemPath(arguments[0]),{item:arguments[0]}).then(arguments[arguments.length-1]);
    """, item) is True
    try:
        WebDriverWait(browser, 10).until(lambda driver: browser.execute_script("""
          const item=arguments[0], panel=panelNodes.get(item), terminal=terminals.get(item)?.container?.querySelector('textarea');
          return focusedPanelItem===item && activeItemForSide(slotForItem(item))===item && terminal
            && (terminal===document.activeElement || terminal.contains(document.activeElement));
        """, selected))
    except TimeoutException:
        state = browser.execute_script("""
          return {focused:focusedPanelItem,stack:[...editorNav.stack],index:editorNav.index,live:[...editorNav.liveItems],layoutItems:[...layoutItems],
            active:activePaneItems(),normal:tabberNormalSelections,layout:layoutSlots};
        """)
        raise AssertionError(state) from None
    assert browser.execute_script('return editorNav.stack.includes(arguments[0]) && !editorNav.stack.includes(arguments[1])', selected, item)
