# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import pytest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.native_pointer import click_visible_selector
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


@pytest.fixture
def terminal_focus_trace(browser):
    def install():
        browser.execute_script("""
          const originalClear=clearFocusedTerminal,originalReconcile=clearFocusForInactiveLayout;
          const trace=window.__recencyFocusClears=[];
          const snapshot=()=>({focused:focusedPanelItem,terminal:focusedTerminal,activeSessions:[...activeSessions],
            domOwner:[...terminals].find(([,terminal])=>terminal.container?.contains(document.activeElement))?.[0],
            socket:terminals.get(focusedPanelItem)?.socket?.readyState,
            paneActive:focusedPanelItem?terminalPaneIsActive(focusedPanelItem):false});
          const observed=(owner,original)=>function(...args){
            const before=snapshot(),value=original(...args);
            if(before.focused && !focusedPanelItem){
              trace.push({owner,before,after:snapshot(),stack:new Error().stack});
              if(trace.length>32) trace.shift();
            }
            return value;
          };
          clearFocusedTerminal=observed('clearFocusedTerminal',originalClear);
          clearFocusForInactiveLayout=observed('clearFocusForInactiveLayout',originalReconcile);
          window.__recencyRestoreFocusTrace=()=>{
            clearFocusedTerminal=originalClear;clearFocusForInactiveLayout=originalReconcile;
            delete window.__recencyRestoreFocusTrace;
          };
        """)
    try:
        yield install
    finally:
        browser.execute_script('window.__recencyRestoreFocusTrace?.()')


@pytest.mark.parametrize('automatic', [False, True])
def test_global_tab_recency_native_navigation_close_and_tmux_exit(browser, isolated_dev_server_factory, request, automatic, terminal_focus_trace):
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
    terminal_focus_trace()

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
                terminalPane:document.getElementById('terminal-pane-'+item)?.className,
                activeSessions:[...activeSessions],socket:terminals.get(item)?.socket?.readyState,
                domOwner:[...terminals].find(([,terminal])=>terminal.container?.contains(document.activeElement))?.[0],
                clears:window.__recencyFocusClears};
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


def test_terminal_refocus_during_native_focusout_preserves_logical_focus(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('terminal-refocus')
    request.addfinalizer(lambda: browser.get('about:blank'))
    session = server.tmux.sessions[0]
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    wait = WebDriverWait(browser, 10)
    wait.until(lambda driver: driver.execute_script("""
      return initialAppShellPainted && terminals.get(arguments[0])?.container?.querySelector('textarea');
    """, session))
    browser.execute_script("setTabberMode('advanced');autoFocusEnabled=false")
    click_visible_selector(browser, f'[data-pane-tab="{session}"]')
    wait.until(lambda driver: driver.execute_script("""
      return focusedPanelItem===arguments[0]
        && terminals.get(arguments[0]).container.contains(document.activeElement);
    """, session))
    result = browser.execute_script("""
      const session=arguments[0],input=terminals.get(session).container.querySelector('textarea');
      const transitions=[];
      input.addEventListener('focusout',()=>{
        transitions.push({phase:'focusout',focused:document.activeElement===input});
        // A focus owner may restore the native input before the old event finishes bubbling.
        input.focus({preventScroll:true});
        transitions.push({phase:'refocus',focused:document.activeElement===input});
      },{capture:true,once:true});
      input.blur();
      return {transitions,domFocused:document.activeElement===input,focused:focusedPanelItem,
        active:activeItemForSide(slotForItem(session)),navigation:editorNav.focusedItem};
    """, session)
    assert result == {
        'transitions': [{'phase': 'focusout', 'focused': False}, {'phase': 'refocus', 'focused': True}],
        'domFocused': True, 'focused': session, 'active': session, 'navigation': session,
    }, result
    lifecycle = browser.execute_script("""
      const session=arguments[0],item=terminals.get(session),input=item.container.querySelector('textarea');
      const snapshot=()=>({domFocused:document.activeElement===input,focused:focusedPanelItem});
      input.blur();
      const genuineBlur=snapshot();
      input.focus({preventScroll:true});
      clearFocusedTerminal(session);
      const explicitLifecycle=snapshot();
      input.blur();input.focus({preventScroll:true});
      const pane=document.getElementById('terminal-pane-'+session);
      const priorStyle=pane.getAttribute('style');
      pane.style.setProperty('display','block','important');
      pane.classList.remove('active');
      let inactivePane;
      try {
        input.addEventListener('focusout',()=>input.focus({preventScroll:true}),{capture:true,once:true});
        input.blur();
        inactivePane=snapshot();
      } finally {
        pane.classList.add('active');
        if(priorStyle===null) pane.removeAttribute('style');else pane.setAttribute('style',priorStyle);
        input.blur();input.focus({preventScroll:true});
      }
      const replacement=document.createElement('div'),replacementInput=document.createElement('textarea');
      replacement.append(replacementInput);item.container.after(replacement);
      let retiredGeneration;
      try {
        terminals.set(session,{...item,container:replacement});
        replacementInput.focus({preventScroll:true});
        retiredGeneration={domFocused:document.activeElement===replacementInput,focused:focusedPanelItem};
      } finally {
        terminals.set(session,item);replacement.remove();input.focus({preventScroll:true});
      }
      let removedGeneration;
      try {
        terminals.delete(session);input.blur();removedGeneration=snapshot();
      } finally {
        terminals.set(session,item);input.focus({preventScroll:true});
      }
      return {genuineBlur,explicitLifecycle,inactivePane,retiredGeneration,removedGeneration};
    """, session)
    assert lifecycle == {
        'genuineBlur': {'domFocused': False, 'focused': None},
        'explicitLifecycle': {'domFocused': True, 'focused': None},
        'inactivePane': {'domFocused': True, 'focused': None},
        'retiredGeneration': {'domFocused': True, 'focused': session},
        'removedGeneration': {'domFocused': False, 'focused': None},
    }, lifecycle


@pytest.mark.parametrize('focus_state', ['retained', 'other-pane', 'panel-intent', 'navigation-intent', 'pending-intent'])
def test_terminal_reconnect_reconciles_retained_native_focus_without_stealing_new_intent(browser, isolated_dev_server_factory, request, focus_state):
    server = isolated_dev_server_factory('terminal-reconnect-focus', session_count=2)
    request.addfinalizer(lambda: browser.get('about:blank'))
    session, other = server.tmux.sessions
    browser.get(server.base_url + f'/?sessions={session},{other}&layout=left&tabs=left:{session},{other}')
    setup = browser.execute_async_script("""
      const [sessions,done]=arguments;
      (async()=>{
        await window.__yomuxTestWaitFor(()=>initialAppShellPainted,{timeoutMs:10000});
        setTabberMode('advanced');autoFocusEnabled=false;
        const slots=emptyLayoutSlots();
        slots[layoutTreeKey]=splitNode('row',leafNode('left'),leafNode('right'),50);
        slots.left=paneStateWithTabs([sessions[0]],sessions[0]);
        slots.right=paneStateWithTabs([sessions[1]],sessions[1]);
        applyLayoutSlots(slots,{prune:false});
        await window.__yomuxTestWaitFor(()=>sessions.every(session=>terminals.get(session)?.socket?.readyState===WebSocket.OPEN),{timeoutMs:10000});
        return true;
      })().then(done,error=>done({error:String(error.stack||error)}));
    """, [session, other])
    assert setup is True, setup
    click_visible_selector(browser, f'[data-pane-tab="{session}"]')
    wait = WebDriverWait(browser, 10)
    wait.until(lambda driver: driver.execute_script("""
      return focusedPanelItem===arguments[0] && terminals.get(arguments[0]).container.contains(document.activeElement);
    """, session))
    try:
        browser.execute_script("""
          const [session,other,focusState]=arguments,originalFetch=window.fetch,originalWebSocket=window.WebSocket;
          const state=window.__recencyReconnect={originalFetch,originalWebSocket,socket:terminals.get(session).socket,release:null};
          const snapshot=()=>({focused:focusedPanelItem,
            native:[...terminals].find(([,item])=>item.container.contains(document.activeElement))?.[0],
            navigation:editorNav.focusedItem,pending:editorNav.pendingFocus?.item || null,
            stack:[...editorNav.stack],index:editorNav.index});
          window.WebSocket=new Proxy(originalWebSocket,{construct(target,args){
            const socket=Reflect.construct(target,args);
            if(String(args[0])===wsUrl(session)){
              socket.addEventListener('open',()=>{
                if(terminals.get(session)?.socket!==socket) return;
                if(focusState!=='other-pane'){
                  clearFocusedTerminal(session);
                  if(focusState==='panel-intent'){setFocusedPanelItem(other);editorNav.focusedItem=session;}
                  if(focusState==='navigation-intent') editorNav.focusedItem=other;
                  if(focusState==='pending-intent') editorNav.pendingFocus={item:other,generation:tabSwitchHighlightGeneration};
                }
                state.beforeOpen=snapshot();
              },{capture:true,once:true});
              // Register after connectTerminalSocket assigns its production open handler.
              queueMicrotask(()=>socket.addEventListener('open',()=>{
                if(terminals.get(session)?.socket!==socket) return;
                state.afterOpen=snapshot();state.openedSocket=socket;
              },{once:true}));
            }
            return socket;
          }});
          let held=false;
          window.fetch=(input,options)=>{
            const url=new URL(String(input),location.href);
            if(!held && url.pathname==='/api/tmux-session-exists' && url.searchParams.get('session')===session){
              held=true;
              return new Promise((resolve,reject)=>state.release=()=>{
                state.release=null;originalFetch(input,options).then(resolve,reject);
              });
            }
            return originalFetch(input,options);
          };
          state.socket.close();
        """, session, other, focus_state)
        wait.until(lambda driver: driver.execute_script('return !!window.__recencyReconnect.release'))
        assert browser.execute_script("""
          return {closed:window.__recencyReconnect.socket.readyState===WebSocket.CLOSED,
            native:terminals.get(arguments[0]).container.contains(document.activeElement)};
        """, session) == {'closed': True, 'native': True}
        if focus_state == 'other-pane':
            click_visible_selector(browser, f'[data-pane-tab="{other}"]')
        before = browser.execute_script('return {stack:[...editorNav.stack],index:editorNav.index}')
        browser.execute_script('window.__recencyReconnect.release()')
        wait.until(lambda driver: driver.execute_script("""
          const socket=terminals.get(arguments[0])?.socket;
          return socket && socket===window.__recencyReconnect.openedSocket && socket.readyState===WebSocket.OPEN
            && terminals.get(arguments[0]).reconnectAttempt===0;
        """, session))
        result = browser.execute_script("""
          return {before:window.__recencyReconnect.beforeOpen,after:window.__recencyReconnect.afterOpen};
        """)
        expected_before = {
            'focused': other if focus_state in {'other-pane', 'panel-intent'} else None,
            'native': other if focus_state == 'other-pane' else session,
            'navigation': other if focus_state in {'other-pane', 'navigation-intent'} else session,
            'pending': other if focus_state == 'pending-intent' else None,
            **before,
        }
        expected_after = dict(expected_before)
        if focus_state == 'retained':
            expected_after['focused'] = session
        assert result == {'before': expected_before, 'after': expected_after}, result
    finally:
        browser.execute_script("""
          const state=window.__recencyReconnect;
          if(state){
            window.fetch=state.originalFetch;window.WebSocket=state.originalWebSocket;
            state.release?.();delete window.__recencyReconnect;
          }
          editorNav.pendingFocus=null;
        """)


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
    click_visible_selector(browser, f'.file-tree-row[data-tabber-pane-item="{second}"]')

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
