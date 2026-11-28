# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Live editor settings and late callbacks preserve editor ownership."""

import json
import os
import sqlite3
import subprocess
import time
from urllib.parse import quote

import pytest
import yaml
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import BROWSER_WAIT_HELPER_SOURCE, browser_screenshot_rgb, select_current_value
from tests.browser_helpers.browser_console import assert_only_expected_browser_network_error
from tests.browser_helpers.browser_console import consume_only_expected_js_debug_api_error
from tests.browser_helpers.native_pointer import cdp_drag_hold, cdp_release, click_visible_selector
from tests.browser_helpers.native_file_actions import rename_finder_file_with_native_input
from tests.browser_helpers.native_keyboard import native_document_start, native_editor_modifier, native_logical_key
from tests.browser_helpers.editor_mount import _open_relocation_editor
from tests.gate_harness import _retire_fixture_background_producers
from tests.gate_harness import wait_for_fixture_api_quiescence
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tests.tmux_runtime import codex_exec_record, respawn_pane_with_codex_agent, write_codex_rollout


def _type_relocation_text(browser, item, text, surface='text-editor'):
    editor = browser.execute_script("""
      const panel=panelNodes.get(arguments[0]);
      return arguments[1] === 'text-editor' ? panel._cmView.contentDOM : panel._pmView.dom;
    """, item, surface)
    try:
        editor.click()
    except ElementClickInterceptedException as error:
        error.add_note(browser.execute_script("return JSON.stringify([...document.querySelectorAll('.toast')].map(node => ({text:node.textContent,html:node.outerHTML})));"))
        raise
    native_logical_key(browser, 'a', [native_editor_modifier(browser)])
    for index, character in enumerate(text, 1):
        editor.send_keys(character)
        actual = browser.execute_script("""
          const panel=panelNodes.get(arguments[0]);
          return arguments[1] === 'text-editor' ? panel._cmView.state.doc.toString() : panel._pmView.state.doc.textContent;
        """, item, surface)
        assert actual == text[:index]


def _assert_expected_relocation_absence(browser, server, request, old_path):
    assert not old_path.exists()
    _retire_fixture_background_producers(browser)
    wait_for_fixture_api_quiescence(browser)
    urls = tuple(server.base_url + endpoint + '?path=' + quote(str(old_path), safe='')
                 for endpoint in ['/api/fs/read', '/api/fs/info'])
    consumed = assert_only_expected_browser_network_error(browser, url=urls, reason='status of 404', count=None)
    request.node.user_properties.append(('expected_old_path_missing', consumed))


def _wait_relocation_state(browser, old_path, new_path):
    result = browser.execute_async_script("""
      const oldPath = arguments[0], path = arguments[1], done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => !fileState.has(oldPath) && fileState.has(path), {timeoutMs:15000});
        const state = fileState.get(path), items = fileEditorTabItemsForPath(path);
        return {content:state.content, original:state.original, dirty:state.dirty,activityTouches:window.__relocationActivityTouches,
          items:items.map(item => ({path:fileItemPath(item), slot:slotForItem(item),
            index:slotForItem(item) ? paneTabs(slotForItem(item)).indexOf(item) : -1,
            mode:editorViewModeFor(path,item),document:panelNodes.get(item)?._cmView?.state.doc.toString()}))};
      })().then(done, error => done({error:String(error.stack || error),
        serverBinary:window.__relocationServerBinary,
        registrations:window.__relocationRegistrations,events:window.__relocationEvents,
        states:[oldPath,path].map(key => ({path:key,content:fileState.get(key)?.content,
          fileId:fileState.get(key)?.fileId,trackingId:fileState.get(key)?.trackingId,
          trackingRevision:fileState.get(key)?.trackingRevision,missing:fileState.get(key)?.externalMissing}))}));
    """, str(old_path), str(new_path))
    assert 'error' not in result, result
    return result


@pytest.mark.parametrize('succeeds', [False, True], ids=['denied', 'deleted'])
def test_finder_delete_preserves_native_unsaved_edits(browser, isolated_dev_server_factory, request, succeeds):
    server = isolated_dev_server_factory('delete-buffer')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'delete-buffer.js'
    path.write_text('disk bytes\n', encoding='utf-8')
    item = _open_relocation_editor(browser, server, path)
    _type_relocation_text(browser, item, 'unsaved edit')
    browser.execute_script("""
      const [path,succeeds]=arguments, fetch=window.fetch;
      window.__deleteBufferOwner=fileState.get(path);
      window.fetch=(url,options)=>String(url)==='/api/fs/delete'
        ? new Promise((resolve,reject)=>{window.__finishDelete=()=>succeeds
          ? fetch(url,options).then(resolve,reject)
          : resolve(new Response(JSON.stringify({error:'permission denied'}),{status:403,headers:{'Content-Type':'application/json'}}));})
        : fetch(url,options);
      window.__deleteBufferPromise=deleteFileTreePath(path,{kind:'file'},[path]);
    """, str(path), succeeds)
    WebDriverWait(browser, 5).until(lambda driver: driver.switch_to.alert).accept()
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script('return typeof window.__finishDelete === "function"'))
    editor = browser.execute_script('return panelNodes.get(arguments[0])._cmView.contentDOM', item)
    editor.click()
    editor.send_keys(Keys.END)
    for character in ' while pending':
        editor.send_keys(character)
    outcome = browser.execute_async_script("""
      const [path,item,done]=arguments;
      (async()=>{
        const before=panelNodes.get(item)._cmView.state.doc.toString();
        window.__finishDelete(); await window.__deleteBufferPromise;
        const state=fileState.get(path);
        return {before,content:state?.content,dirty:state?.dirty,missing:state?.externalMissing===true,
          document:panelNodes.get(item)?._cmView?.state.doc.toString(),inLayout:itemInLayout(item),
          sameOwner:state===window.__deleteBufferOwner};
      })().then(done,error=>done({error:String(error.stack||error)}));
    """, str(path), item)
    assert 'error' not in outcome, outcome
    assert outcome['before'] == 'unsaved edit while pending', outcome
    assert outcome['document'] == outcome['before'], outcome
    assert outcome['inLayout'] and outcome['sameOwner'], outcome
    assert outcome['missing'] is succeeds, outcome
    expected_content = outcome['before'] + ('\n' if succeeds else '')
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script(
        'const state=fileState.get(arguments[0]);return state?.dirty && state.content===arguments[1]', str(path), expected_content))
    assert path.exists() is not succeeds
    if succeeds:
        _assert_expected_relocation_absence(browser, server, request, path)
    else:
        assert path.read_text(encoding='utf-8') == 'disk bytes\n'
        consume_only_expected_js_debug_api_error(browser, path='/api/fs/delete', status=403, method='POST', query={})


@pytest.mark.parametrize('trigger', ['watch', 'held-reload', 'atomic-replacement', 'hidden-tab'])
def test_markdown_disk_update_keeps_xterm_focus(browser, isolated_dev_server_factory, request, trigger):
    server = isolated_dev_server_factory('markdown-disk-focus', session_count=2 if trigger == 'hidden-tab' else 1)
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'external.md'
    path.write_text('# Original\n', encoding='utf-8')
    _open_relocation_editor(browser, server, path)
    session = server.tmux.sessions[0]
    peer = server.tmux.sessions[1] if trigger == 'hidden-tab' else ''
    setup = browser.execute_async_script("""
      const [path, session, peer, trigger, done] = arguments;
      (async () => {
        const item = fileEditorTabItemsForPath(path)[0];
        setFileEditorViewMode(path, 'preview', item);
        applyLayoutSlots({[layoutTreeKey]: splitNode('row', leafNode('left'), leafNode('right'), 50),
          left: paneStateWithTabs([session], session), right: paneStateWithTabs([item], item)});
        const terminal = () => terminals.get(session)?.term?.textarea;
        const view = () => panelNodes.get(item)?._pmView;
        await window.__yomuxTestWaitFor(() => view()?.dom.isConnected && terminal()?.isConnected,
          {timeoutMs: 10000});
        if (trigger === 'watch' || trigger === 'held-reload') {
          view().focus();
          if (document.activeElement !== view().dom) throw new Error('Markdown did not receive focus');
        } else {
          if (trigger === 'hidden-tab') {
            applyLayoutSlots({[layoutTreeKey]:splitNode('row',leafNode('left'),leafNode('right'),50),
              left:paneStateWithTabs([session],session),right:paneStateWithTabs([peer,item],peer)});
            if (visibleFileEditorWatchFiles().includes(path)) throw new Error('Markdown tab was not hidden');
          }
          terminals.get(session).term.focus();
        }
        await syncServerWatchRoots({immediate:true,force:true});
        await window.__yomuxTestWaitFor(() => serverWatchRootsState.registered && !serverWatchRootsState.inFlight,
          {timeoutMs:10000});
        window.__markdownDiskFocusEvents = [];
        document.addEventListener('focusin', e => window.__markdownDiskFocusEvents.push(e.target === view()?.dom ? 'markdown' : 'other'));
        if (trigger === 'held-reload') {
          const fetchOriginal = window.fetch;
          window.fetch = (url, options) => {
            const response = fetchOriginal(url, options);
            if (!String(url).startsWith('/api/fs/read?') || new URL(url, location.href).searchParams.get('path') !== path) return response;
            window.fetch = fetchOriginal;
            return new Promise(resolve => { window.__releaseMarkdownRead = () => resolve(response); });
          };
        }
        return {item};
      })().then(done, error => done({error:String(error.stack || error)}));
    """, str(path), session, peer, trigger)
    assert 'error' not in setup, setup
    if trigger == 'held-reload':
        browser.execute_script("""
          const original=handleClientPushEvent;
          handleClientPushEvent=(type,payload,envelope) =>
            ['fs_changed','files_changed'].includes(type) ? undefined : original(type,payload,envelope);
        """)
    expected = '# Edited externally\n'
    if trigger == 'atomic-replacement':
        expected = '# Replaced\n'
        old_info = path.stat()
        replacement = path.with_suffix('.tmp')
        replacement.write_text(expected, encoding='utf-8')
        os.utime(replacement, ns=(old_info.st_atime_ns, old_info.st_mtime_ns))
        replacement.replace(path)
        assert (path.stat().st_size, path.stat().st_mtime_ns) == (old_info.st_size, old_info.st_mtime_ns)
    else:
        path.write_text(expected, encoding='utf-8')
    if trigger == 'watch':
        WebDriverWait(browser, 10).until(lambda driver: driver.execute_script(
            'return !!fileState.get(arguments[0])?.externalReloadDeferred', str(path)))
    elif trigger == 'held-reload':
        browser.execute_script('window.__markdownReload = reloadOpenFileFromDisk(arguments[0]);', str(path))
        WebDriverWait(browser, 10).until(lambda driver: driver.execute_script('return !!window.__releaseMarkdownRead'))
    clicked = browser.execute_script("""
      const terminal=terminals.get(arguments[0]).term;
      terminal.focus();
      return document.activeElement === terminal.textarea;
    """, session)
    assert clicked
    if trigger == 'held-reload':
        browser.execute_script('window.__releaseMarkdownRead();')
    if trigger == 'hidden-tab':
        browser.execute_script("""
          const [session,peer,item]=arguments;
          applyLayoutSlots({[layoutTreeKey]:splitNode('row',leafNode('left'),leafNode('right'),50),
            left:paneStateWithTabs([session],session),right:paneStateWithTabs([peer,item],item)},
            {activeOnlyActivation:true});
        """, session, peer, setup['item'])
    WebDriverWait(browser, 6).until(lambda driver: driver.execute_script("""
      return panelNodes.get(arguments[0])?._pmView?.state.doc.textContent === arguments[1];
    """, setup['item'], expected.removeprefix('# ').strip()))
    result = browser.execute_async_script("""
      const [session,path,done]=arguments;
      requestAnimationFrame(() => requestAnimationFrame(() => done({
        terminalFocused:document.activeElement === terminals.get(session).term.textarea,
        pane:focusedPanelItem,
        markdownFocused:window.__markdownDiskFocusEvents.includes('markdown'),
        content:fileState.get(path).original
      })));
    """, session, str(path))
    assert result == {'terminalFocused': True, 'pane': session, 'markdownFocused': False,
                      'content': expected}, result


@pytest.mark.parametrize('format_name', ['graphviz', 'invalid-graphviz', 'sqlite'])
def test_file_preview_graphviz_and_sqlite(browser, isolated_dev_server_factory, request, format_name):
    server = isolated_dev_server_factory('graph-and-database-preview')
    request.addfinalizer(lambda: browser.get('about:blank'))
    home = server.paths.home_dir
    if format_name == 'sqlite':
        path = home / 'fixture.sqlite'
        with sqlite3.connect(path) as database:
            database.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, name TEXT)')
            database.executemany('INSERT INTO records VALUES (?, ?)', [(i, f'row {i}') for i in range(1, 56)])
            database.execute('CREATE TABLE sample (value TEXT)')
            database.execute("INSERT INTO sample VALUES ('<b>literal</b>')")
        database.close()
    else:
        path = home / ('diagram.dot' if format_name == 'graphviz' else 'bad.gv')
        path.write_text('digraph G { Start -> Finish }' if format_name == 'graphviz' else 'digraph { Start -> }',
                        encoding='utf-8')
    session = server.tmux.sessions[0]
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    browser.execute_script(BROWSER_WAIT_HELPER_SOURCE)
    opened = browser.execute_async_script("""
      const [path, done] = arguments;
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof openFileInEditor === 'function' && initialAppShellPainted);
        return await openFileInEditor(path, basenameOf(path), {userInitiated:true});
      })().then(item => done({item}), error => done({error:String(error.stack || error)}));
    """, str(path))
    assert opened.get('item'), opened
    item = opened['item']
    if format_name == 'graphviz':
        WebDriverWait(browser, 15).until(lambda driver: driver.execute_script("""
          const host = panelNodes.get(arguments[0])?.querySelector('.graphviz-preview-result');
          return host?.querySelector('img')?.naturalWidth > 0 && host.querySelector('[data-preview-zoom-action="fit"]');
        """, item))
        assert browser.execute_async_script("""
          const [path, done] = arguments;
          (async () => {
            const snapshot = await renderedPreviewSnapshotAsync(path, fileState.get(path).content);
            const root = document.createElement('div');
            applyPreviewSnapshotRoot(root, snapshot);
            const image = root.querySelector('.graphviz-preview-result img');
            await image.decode();
            return image.naturalWidth > 0;
          })().then(done, error => done(String(error)));
        """, str(path)) is True
        return
    if format_name == 'invalid-graphviz':
        WebDriverWait(browser, 15).until(lambda driver: driver.execute_script("""
          return panelNodes.get(arguments[0])?.querySelector('.graphviz-preview-result')?.textContent.includes('Graphviz parse error');
        """, item))
        return
    WebDriverWait(browser, 15).until(lambda driver: driver.execute_script("""
      const host = panelNodes.get(arguments[0])?.querySelector('.sqlite-preview-result');
      return host?.querySelectorAll('tbody tr').length === 50 && host.querySelector('details pre').textContent.includes('CREATE TABLE');
    """, item))
    host_selector = '.sqlite-preview-result'
    browser.find_element(By.CSS_SELECTOR, f'{host_selector} .sqlite-preview-pages button:last-child').click()
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("""
      return document.querySelector('.sqlite-preview-result tbody td')?.textContent === '51';
    """))
    reload_button = browser.find_element(By.CSS_SELECTOR, f'{host_selector} > button')

    def refresh_from_disk():
        refreshed = browser.execute_async_script("""
          const [path, done] = arguments;
          refreshOpenFilesIfChanged({paths:[path], fresh:true}).then(() => done(true), error => done(String(error)));
        """, str(path))
        assert refreshed is True, refreshed

    browser.execute_script("""
      window.__sqliteFetch = window.fetch;
      window.fetch = async (...args) => {
        const url = new URL(String(args[0]), location.href);
        const response = await window.__sqliteFetch(...args);
        if (url.pathname !== '/api/fs/sqlite-preview' || url.searchParams.get('table') !== 'sample') return response;
        window.fetch = window.__sqliteFetch;
        return new Promise(resolve => { window.__releaseSQLiteSelection = () => resolve(response); });
      };
    """)
    try:
        select_current_value(browser, f'{host_selector} select', 'sample')
        WebDriverWait(browser, 10).until(lambda driver: driver.execute_script('return !!window.__releaseSQLiteSelection'))
        browser.execute_script('document.activeElement.blur()')
        with sqlite3.connect(path) as database:
            database.execute("INSERT INTO sample VALUES ('new row')")
        database.close()
        refresh_from_disk()
        WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("""
          return document.querySelector('.sqlite-preview-result tbody tr:last-child td')?.textContent === 'new row';
        """))
    finally:
        browser.execute_script('window.fetch = window.__sqliteFetch; window.__releaseSQLiteSelection?.()')
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("""
      const cell = document.querySelector('.sqlite-preview-result tbody td');
      return cell?.textContent === '<b>literal</b>' && cell.children.length === 0;
    """))
    with sqlite3.connect(path) as database:
        database.execute('DROP TABLE sample')
    database.close()
    refresh_from_disk()
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("""
      const preview = document.querySelector('.sqlite-preview-result');
      return preview?.querySelector('select').value === 'records' &&
        preview.querySelectorAll('tbody tr').length === 50;
    """))
    assert browser.find_element(By.CSS_SELECTOR, f'{host_selector} > button') == reload_button
    reload_button.click()
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("""
      return document.querySelector('.sqlite-preview-result tbody tr:last-child td')?.textContent === '50';
    """))


def test_delayed_watch_snapshot_keeps_identity_from_native_save(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('watch-snapshot-save')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'saving.md'
    path.write_text('base\n', encoding='utf-8')
    item = _open_relocation_editor(browser, server, path)
    held = browser.execute_async_script("""
      const path=arguments[0],done=arguments[arguments.length-1];
      (async()=>{
        await window.__yomuxTestWaitFor(()=>!serverWatchRootsState.inFlight,{timeoutMs:10000});
        const originalFetch=window.fetch;window.__watchSaveWrites=[];let hold=true;
        window.fetch=(url,options)=>{
          const response=originalFetch(url,options);
          if(String(url)==='/api/fs/write') window.__watchSaveWrites.push(JSON.parse(options.body));
          if(String(url)!=='/api/watch/roots'||!hold) return response;
          hold=false;
          return response.then(async result=>{
            window.__heldWatchSnapshot=await result.clone().json();
            return new Promise(resolve=>{window.__releaseSaveWatch=()=>resolve(result);});
          });
        };
        const id=fileState.get(path).fileId;
        window.__saveWatchRegistration=syncServerWatchRootsNow({force:true});
        if(!window.__saveWatchRegistration?.then) throw new Error('watch registration was not dispatched');
        await window.__yomuxTestWaitFor(()=>window.__releaseSaveWatch,{timeoutMs:10000});
        return {id,snapshot:window.__heldWatchSnapshot.open_files.find(file=>file.old_path===path)};
      })().then(done,error=>done({error:String(error.stack||error)}));
    """, str(path))
    assert 'error' not in held and held['snapshot']['file_id'] == held['id'], held
    _type_relocation_text(browser, item, 'first')
    native_logical_key(browser, 's', [native_editor_modifier(browser)])
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script(
        "const s=fileState.get(arguments[0]);return !s.dirty&&s.original.trim()==='first'&&s.fileId!==arguments[1]",
        str(path), held['id']))
    saved_id = browser.execute_script('return fileState.get(arguments[0]).fileId', str(path))
    released = browser.execute_async_script("""
      const path=arguments[0],done=arguments[arguments.length-1];
      window.__releaseSaveWatch();window.__saveWatchRegistration.then(()=>done(fileState.get(path).fileId),
        error=>done({error:String(error.stack||error)}));
    """, str(path))
    assert released == saved_id, {'saved': saved_id, 'after_snapshot': released}
    _type_relocation_text(browser, item, 'second')
    native_logical_key(browser, 's', [native_editor_modifier(browser)])
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script(
        "const s=fileState.get(arguments[0]);return !s.dirty&&s.original.trim()==='second'", str(path)))
    assert browser.execute_script('return window.__watchSaveWrites[1].expected_file_id') == saved_id
    assert path.read_text(encoding='utf-8').rstrip('\n') == 'second'


@pytest.mark.parametrize('rename_kind', ['same-directory', 'watched-directory', 'parent-directory'])
def test_external_rename_preserves_two_clients_native_dirty_text(browser, isolated_dev_server_factory, request, rename_kind):
    server = isolated_dev_server_factory('external-rename-two-clients')
    original_window = browser.current_window_handle
    windows = []
    extra_windows = []

    def restore_windows():
        for window in extra_windows:
            if window in browser.window_handles:
                browser.switch_to.window(window)
                browser.close()
        browser.switch_to.window(original_window)
        browser.get('about:blank')

    request.addfinalizer(restore_windows)
    source_dir = server.paths.home_dir / 'source'
    source_dir.mkdir()
    target_dir = server.paths.home_dir / 'target'
    target_dir.mkdir()
    path = source_dir / 'note.md'
    path.write_text('base\n', encoding='utf-8')
    for text in ['client alpha', 'client beta']:
        preview_view_state = None
        if windows:
            browser.switch_to.new_window('tab')
            extra_windows.append(browser.current_window_handle)
        item = _open_relocation_editor(browser, server, path)
        if not windows:
            copied = browser.execute_async_script("""
              const path=arguments[0], item=arguments[1], done=arguments[arguments.length - 1];
              (async () => {
                const copy = await openFileInAdditionalEditorTab(path,basenameOf(path),{viewMode:'preview'});
                await openFileEditorPane(path,{item});
                return {copy,count:fileEditorTabItemsForPath(path).length};
              })().then(done,error => done({error:String(error.stack || error)}));
            """, str(path), item)
            assert 'error' not in copied and copied['count'] == 2, copied
            preview_item = copied['copy']
            preview_view_state = browser.execute_async_script("""
              const [path,item,restoreItem,done]=arguments;
              (async()=>{
                await openFileEditorPane(path,{item});
                await window.__yomuxTestWaitFor(()=>panelNodes.get(item)?._pmView?.dom?.getBoundingClientRect().height>0,
                  {timeoutMs:10000});
                const panel=panelNodes.get(item), view=panel._pmView;
                const target=Math.min(24,view.state.doc.content.size-1);
                view.dispatch(view.state.tr.setSelection(window.YOmuxProseMirror.TextSelection.create(view.state.doc,target)));
                captureFileEditorPanelViewState(item,panel);
                const saved=fileEditorViewState.get(item);
                await openFileEditorPane(path,{item:restoreItem});
                return {item,head:view.state.selection.head,savedHead:saved.pmHead};
              })().then(done,error=>done({error:String(error.stack||error)}));
            """, str(path), preview_item, item)
            assert 'error' not in preview_view_state, preview_view_state
            assert preview_view_state['head'] == preview_view_state['savedHead'], preview_view_state
        # Register both parent scopes before moving between them.
        registered = browser.execute_async_script("""
          const root = arguments[0], path=arguments[1], item=arguments[2], done = arguments[arguments.length - 1];
          (async () => {
            const registrationStart=window.__relocationRegistrations.length;
            await openFileExplorerPane();
            await openFileExplorerManualRoot(root);
            applyLayoutSlots({[layoutTreeKey]:splitNode('row',leafNode('left'),leafNode('right'),30),
              left:paneStateWithTabs([finderItemId],finderItemId),
              right:paneStateWithTabs(fileEditorTabItemsForPath(path),item)});
            await window.__yomuxTestWaitFor(() => fileExplorerTreePaneIsVisible()
              && panelNodes.get(item)?._cmView?.contentDOM.getBoundingClientRect().height > 0,{timeoutMs:5000});
            await syncServerWatchRoots({immediate:true,force:true});
            await window.__yomuxTestWaitFor(() => !serverWatchRootsState.inFlight && serverWatchRootsState.registered
              && window.__relocationRegistrations.slice(registrationStart).some(entry =>
                entry.status === 200 && entry.request.roots.includes(root)),{timeoutMs:10000});
            return {roots:clientServerWatchState().roots};
          })().then(done,error => done({error:String(error)}));
        """, str(server.paths.home_dir), str(path), item)
        assert 'error' not in registered and str(server.paths.home_dir) in registered['roots'], registered
        _type_relocation_text(browser, item, text)
        before = browser.execute_script("""
          const path=arguments[0];
          return fileEditorTabItemsForPath(path).map(item => ({slot:slotForItem(item),mode:editorViewModeFor(path,item),
            index:slotForItem(item) ? paneTabs(slotForItem(item)).indexOf(item) : -1}));
        """, str(path))
        assert any(entry['slot'] for entry in before), before
        windows.append((browser.current_window_handle, text, before, preview_view_state))
    if rename_kind == 'same-directory':
        destination = source_dir / 'renamed.md'
        path.rename(destination)
    elif rename_kind == 'watched-directory':
        destination = target_dir / path.name
        path.rename(destination)
    else:
        renamed_dir = server.paths.home_dir / 'renamed-parent'
        source_dir.rename(renamed_dir)
        destination = renamed_dir / path.name
    for window, text, before, preview_view_state in windows:
        browser.switch_to.window(window)
        try:
            result = _wait_relocation_state(browser, path, destination)
        except AssertionError as error:
            peers = []
            for peer_window, _peer_text, _peer_before, _preview_state in windows:
                browser.switch_to.window(peer_window)
                peers.append(browser.execute_script("""
                  return {client_id:browserClientId,registrations:window.__relocationRegistrations,
                    events:window.__relocationEvents};
                """))
            raise AssertionError({'failure':error.args[0],'clients':peers}) from error
        assert result['dirty'] is True and result['content'].rstrip('\n') == text, result
        assert result['original'] == 'base\n', result
        assert result['items'] and all(item['path'] == str(destination) for item in result['items']), result
        assert [{key:item[key] for key in ['slot','mode','index']} for item in result['items']] == before, {'before':before,'after':result}
        assert any((item.get('document') or '').rstrip('\n') == text for item in result['items']), result
        if window == original_window:
            assert len(result['items']) == 2 and {item['mode'] for item in result['items']} == {'edit', 'preview'}, result
            if preview_view_state:
                preview = browser.execute_script("""
                  const path=arguments[0], expected=arguments[1];
                  const item=fileEditorTabItemsForPath(path).find(candidate=>editorViewModeFor(path,candidate)==='preview');
                  const view=panelNodes.get(item)?._pmView, saved=fileEditorViewState.get(item);
                  return {item,head:view?.state.selection.head,savedHead:saved?.pmHead,
                    text:view?.state.doc.textContent};
                """, str(destination), preview_view_state)
                assert preview['head'] == preview_view_state['head'] == preview['savedHead'], preview
                assert preview['text'], preview
        _assert_expected_relocation_absence(browser, server, request, path)
    assert not path.exists() and destination.read_text(encoding='utf-8') == 'base\n'
    browser.close()
    browser.switch_to.window(original_window)


@pytest.mark.parametrize('copied', [False, True], ids=['original-editor', 'copied-editor'])
@pytest.mark.parametrize('disappearance', ['delete', 'outside-scope'])
@pytest.mark.parametrize('surface', ['text-editor', 'view-editor'])
def test_external_disappearance_keeps_pending_native_text(browser, isolated_dev_server_factory, request, copied, disappearance, surface):
    server = isolated_dev_server_factory('external-disappearance-native-input')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'pending.md'
    path.write_text('base\n', encoding='utf-8')
    item = _open_relocation_editor(browser, server, path)
    if copied:
        item = browser.execute_async_script("""
          const path=arguments[0], done=arguments[arguments.length-1];
          (async () => {
            const item=await openFileInAdditionalEditorTab(path,basenameOf(path),{viewMode:arguments[1]});
            await window.__yomuxTestWaitFor(()=>panelNodes.get(item)?._cmView?.contentDOM.getBoundingClientRect().height > 0,
              {timeoutMs:5000});
            return item;
          })().then(done,error=>done({error:String(error.stack || error)}));
        """, str(path), 'split' if surface == 'view-editor' else 'edit')
        assert isinstance(item, str), item
    if surface == 'view-editor':
        ready = browser.execute_async_script("""
          const path=arguments[0], item=arguments[1], done=arguments[arguments.length-1];
          (async () => {
            setFileEditorViewMode(path,'split',item); renderFileEditorPanel(panelNodes.get(item),item);
            await window.__yomuxTestWaitFor(()=>panelNodes.get(item)?._pmView?.dom.getBoundingClientRect().height > 0,
              {timeoutMs:5000});
            return true;
          })().then(done,error=>done({error:String(error.stack || error)}));
        """, str(path), item)
        assert ready is True, ready
    text = 'unsaved just before disappearance'
    _type_relocation_text(browser, item, text, surface)
    assert browser.execute_script("""
      const panel=panelNodes.get(arguments[0]);
      return Boolean(arguments[1] === 'text-editor' ? panel._cmSerializeTimer : panel._pmSerializeTimer);
    """, item, surface), 'native input must still be pending when the file disappears'
    if disappearance == 'delete':
        path.unlink()
    else:
        path.rename(server.paths.root / 'outside.md')
    result = browser.execute_async_script("""
      const path=arguments[0], item=arguments[1], done=arguments[arguments.length-1];
      (async () => {
        const diff=await fetchFilesystemWatchDiff({full:true});
        await refreshOpenFilesFromPush(diff);
        const state=fileState.get(path);
        const panel=panelNodes.get(item);
        return {content:state.content,original:state.original,dirty:state.dirty,missing:state.externalMissing,
          document:arguments[2] === 'text-editor' ? panel?._cmView?.state.doc.toString() : panel?._pmView?.state.doc.textContent};
      })().then(done,error=>done({error:String(error.stack || error)}));
    """, str(path), item, surface)
    assert result.get('missing') is True and result.get('dirty') is True, result
    assert result['content'].rstrip('\n') == text and result['document'].rstrip('\n') == text, result
    assert result['original'] == 'base\n', result
    _assert_expected_relocation_absence(browser, server, request, path)
    browser.get('about:blank')


def test_external_rename_historical_and_live_close_release_registration(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('historical-relocation-close')
    request.addfinalizer(lambda: browser.get('about:blank'))
    root = server.paths.home_dir
    path = root / 'history.txt'
    destination = root / 'relocated-history.txt'
    path.write_text('first\n', encoding='utf-8')
    commands = [['init', '--initial-branch=main'], ['config', 'user.name', 'Fixture'],
                ['config', 'user.email', 'fixture@example.invalid'], ['add', path.name], ['commit', '-m', 'first']]
    for args in commands:
        subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
    path.write_text('second\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(root), 'commit', '-am', 'second'], check=True, capture_output=True)
    commits = [subprocess.run(['git', '-C', str(root), 'rev-parse', ref], check=True, capture_output=True,
                              text=True).stdout.strip() for ref in ['HEAD~1', 'HEAD']]
    _open_relocation_editor(browser, server, path)
    history = browser.execute_async_script("""
      const path=arguments[0],done=arguments[arguments.length-1];
      openHistoricalFileInEditor(path,arguments[1],arguments[2]).then(done,error=>done({error:String(error)}));
    """, str(path), *commits)
    assert isinstance(history, str), history
    path.rename(destination)
    WebDriverWait(browser, 15).until(lambda driver: driver.execute_script(
        'return fileState.has(arguments[0]) && historicalFileState.has(arguments[1])', str(destination), history))
    assert browser.execute_script('return historicalFileState.get(arguments[0]).content', history) == 'second\n'
    for item in [history, 'file:' + str(destination)]:
        selector = browser.execute_script(
            'return \'[data-pane-tab="\'+CSS.escape(arguments[0])+\'"] [data-pane-tab-close]\'', item)
        # Model a chrome replacement during pointer approach while preserving the tab's real
        # delegated dismissal handler. A retained WebElement must become stale in this fixture.
        browser.execute_script("""
          const selector=arguments[0];window.__historicalCloseProbe={moved:false,replaced:false};
          window.addEventListener('pointermove',()=>{
            const retained=document.querySelector(selector);
            retained?.replaceWith(retained.cloneNode(true));
            window.__historicalCloseProbe={moved:true,replaced:Boolean(retained&&!retained.isConnected)};
          },{capture:true,once:true});
        """, selector)
        click_visible_selector(browser, selector)
        assert browser.execute_script('return window.__historicalCloseProbe') == {'moved': True, 'replaced': True}
    result = browser.execute_async_script("""
      const path=arguments[0],done=arguments[arguments.length-1];
      (async()=>{
        await window.__yomuxTestWaitFor(()=>!serverWatchRootsState.inFlight,{timeoutMs:10000});
        await syncServerWatchRootsNow({force:true});
        return {owners:filePanelItemsForPath(path),open:clientServerWatchState().open_files,
          retained:fileState.has(path)};
      })().then(done,error=>done({error:String(error)}));
    """, str(destination))
    assert result == {'owners': [], 'open': [], 'retained': False}, result
    _assert_expected_relocation_absence(browser, server, request, path)
    browser.get('about:blank')


def test_external_rename_fences_delayed_write_and_preserves_native_input(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('external-rename-delayed-write')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'queued.md'
    destination = path.with_name('relocated.md')
    path.write_text('base\n', encoding='utf-8')
    item = _open_relocation_editor(browser, server, path)
    _type_relocation_text(browser, item, 'first')
    held = browser.execute_async_script("""
      const path=arguments[0], item=arguments[1], done=arguments[arguments.length - 1];
      (async () => {
        window.__relocationFetch = window.fetch;
        window.fetch = (url, options) => String(url) === '/api/fs/write'
          ? new Promise((resolve,reject) => {
            window.__relocationWriteBody = JSON.parse(options.body);
            window.__releaseRelocationWrite = () => window.__relocationFetch(url,options).then(resolve,reject);
          }) : window.__relocationFetch(url,options);
        window.__relocationSave = saveFileEditor(path,panelNodes.get(item));
        await window.__yomuxTestWaitFor(() => Boolean(window.__releaseRelocationWrite), {timeoutMs:5000});
        return window.__relocationWriteBody;
      })().then(done,error => done({error:String(error.stack || error)}));
    """, str(path), item)
    assert held.get('expected_file_id'), held
    _type_relocation_text(browser, item, 'new native text')
    path.rename(destination)
    result = _wait_relocation_state(browser, path, destination)
    assert result['content'].rstrip('\n') == 'new native text' and result['dirty'] is True, result
    finished = browser.execute_async_script("""
      const path=arguments[0], done=arguments[arguments.length - 1];
      (async () => {
        window.fetch = window.__relocationFetch;
        window.__releaseRelocationWrite();
        const saved = await window.__relocationSave;
        return {saved,content:fileState.get(path).content,dirty:fileState.get(path).dirty};
      })().then(done,error => done({error:String(error.stack || error)}));
    """, str(destination))
    assert 'error' not in finished and finished['saved'] is False, finished
    assert finished['dirty'] is True and finished['content'].rstrip('\n') == 'new native text', finished
    assert not path.exists() and destination.read_text(encoding='utf-8') == 'base\n'
    consume_only_expected_js_debug_api_error(browser, path='/api/fs/write', status=409, method='POST', query={})
    _assert_expected_relocation_absence(browser, server, request, path)


def test_external_rename_registration_snapshot_recovers_missed_event_after_atomic_save(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('external-rename-snapshot')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'snapshot.md'
    destination = path.with_name('snapshot-renamed.md')
    path.write_text('base\n', encoding='utf-8')
    item = _open_relocation_editor(browser, server, path)
    _type_relocation_text(browser, item, 'saved before rename')
    saved = browser.execute_async_script("""
      const path=arguments[0], item=arguments[1], done=arguments[arguments.length - 1];
      (async () => {
        await syncServerWatchRoots({immediate:true,force:true});
        const trackingId = fileState.get(path).trackingId, oldId = fileState.get(path).fileId;
        const saved = await saveFileEditor(path,panelNodes.get(item));
        window.__relocationOriginalHandle = handleClientPushEvent;
        window.__relocationMissed = [];
        handleClientPushEvent = (type,payload,envelope) => {
          if (type === 'file_relocated') { window.__relocationMissed.push(payload); return true; }
          return window.__relocationOriginalHandle(type,payload,envelope);
        };
        return {saved,trackingId,afterTrackingId:fileState.get(path).trackingId,
          oldId,fileId:fileState.get(path).fileId};
      })().then(done,error => done({error:String(error.stack || error)}));
    """, str(path), item)
    assert 'error' not in saved and saved['saved'] is True and saved['trackingId'], saved
    assert saved['afterTrackingId'] == saved['trackingId'] and saved['oldId'] != saved['fileId'], saved
    path.rename(destination)
    snapshot = browser.execute_async_script("""
      const oldPath=arguments[0], path=arguments[1], done=arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => window.__relocationMissed.length > 0, {timeoutMs:15000});
        const missed=window.__relocationMissed[0];
        handleClientPushEvent = window.__relocationOriginalHandle;
        await syncServerWatchRoots({immediate:true,force:true});
        await window.__yomuxTestWaitFor(() => !fileState.has(oldPath) && fileState.has(path), {timeoutMs:10000});
        return {missed,trackingId:fileState.get(path).trackingId,content:fileState.get(path).content,
          dirty:fileState.get(path).dirty,original:fileState.get(path).original};
      })().then(done,error => done({error:String(error.stack || error)}));
    """, str(path), str(destination))
    assert 'error' not in snapshot and snapshot['trackingId'] == saved['trackingId'], snapshot
    assert snapshot['missed']['tracking_id'] == saved['trackingId'], snapshot
    assert snapshot['content'].rstrip('\n') == 'saved before rename' and snapshot['dirty'] is False, snapshot
    assert snapshot['original'] == snapshot['content'], snapshot
    assert not path.exists() and destination.read_text(encoding='utf-8') == snapshot['content']
    _assert_expected_relocation_absence(browser, server, request, path)


def test_external_rename_keeps_both_conflicting_dirty_buffers(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('external-rename-dirty-destination')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'source.md'
    destination = path.with_name('destination.md')
    path.write_text('source base\n', encoding='utf-8')
    destination.write_text('destination base\n', encoding='utf-8')
    source_item = _open_relocation_editor(browser, server, path)
    _type_relocation_text(browser, source_item, 'source unsaved')
    opened = browser.execute_async_script("""
      const path=arguments[0], done=arguments[arguments.length - 1];
      (async () => {
        const item=await openFileInEditor(path,basenameOf(path),{viewMode:'edit',userInitiated:true});
        await openFileEditorPane(path,{item});
        await window.__yomuxTestWaitFor(() => panelNodes.get(item)?._cmView?.contentDOM.getBoundingClientRect().height > 0,
          {timeoutMs:10000});
        await syncServerWatchRoots({immediate:true,force:true});
        window.__relocationConflictHandle=handleClientPushEvent;
        window.__relocationConflictEvents=[];
        handleClientPushEvent=(type,payload,envelope) => {
          if (type === 'file_relocated') window.__relocationConflictEvents.push(payload);
          return window.__relocationConflictHandle(type,payload,envelope);
        };
        return {item};
      })().then(done,error => done({error:String(error.stack || error)}));
    """, str(destination))
    assert 'error' not in opened, opened
    _type_relocation_text(browser, opened['item'], 'destination unsaved')
    path.replace(destination)
    preserved = browser.execute_async_script("""
      const oldPath=arguments[0], path=arguments[1], done=arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => window.__relocationConflictEvents.some(event => event.old_path === oldPath),
          {timeoutMs:15000});
        flushQueuedClientPushEvents();
        await window.__yomuxTestWaitFor(() => Boolean(fileState.get(oldPath)?.externalError), {timeoutMs:5000});
        handleClientPushEvent=window.__relocationConflictHandle;
        return [oldPath,path].map(key => ({path:key,content:fileState.get(key)?.content,dirty:fileState.get(key)?.dirty,
          document:panelNodes.get(primaryEditorItemForPath(key))?._cmView?.state.doc.toString()}));
      })().then(done,error => done({error:String(error.stack || error)}));
    """, str(path), str(destination))
    assert isinstance(preserved, list) and len(preserved) == 2, preserved
    for result, text in zip(preserved, ['source unsaved', 'destination unsaved'], strict=True):
        assert result['dirty'] is True and result['content'].rstrip('\n') == text, preserved
        assert result['document'].rstrip('\n') == text, preserved
    assert not path.exists() and destination.read_text(encoding='utf-8') == 'source base\n'
    _assert_expected_relocation_absence(browser, server, request, path)


def test_native_settings_save_preserves_later_finder_file_open(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('editor-settings-navigation')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'navigation.md'
    path.write_text('base\n', encoding='utf-8')
    browser.get(server.base_url + '/?sessions=__finder__&layout=left&tabs=left:__finder__')
    setup = browser.execute_async_script("""
      const path = arguments[0], done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => initialAppShellPainted && clientEventTransportState.connected, {timeoutMs:10000});
        await openFileExplorerPane();
        await openFileExplorerManualRoot(dirnameOf(path));
        await saveSettingsPatch(settingPatch('file_explorer.root_mode', 'sync'));
        setFileExplorerRootMode('sync', {sync:false});
        await window.__yomuxTestWaitFor(() => [...document.querySelectorAll('.file-tree-row')]
          .some(row => row.dataset.path === path && row.getBoundingClientRect().height > 0), {timeoutMs:5000});
        window.__settingsNavigationFetch = window.fetch;
        window.fetch = (url, options) => String(url) === '/api/settings' && options?.method === 'POST'
          ? new Promise((resolve, reject) => {
            window.__settingsNavigationRelease = () => window.__settingsNavigationFetch(url, options).then(resolve, reject);
          }) : window.__settingsNavigationFetch(url, options);
        window.__settingsNavigationSave = saveSettingsPatch;
        saveSettingsPatch = (...args) => {
          const saving = window.__settingsNavigationSave(...args);
          saving.then(() => { window.__settingsNavigationDone = true; });
          return saving;
        };
        return {painted:initialAppShellPainted};
      })().then(done, error => done({error:String(error.stack || error)}));
    """, str(path))
    assert setup == {'painted': True}, setup
    button = browser.find_element(By.CSS_SELECTOR, '.file-explorer-root-mode-toggle-panel')
    assert button.is_displayed()
    button.click()
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script('return Boolean(window.__settingsNavigationRelease)'))
    row = next(row for row in browser.find_elements(By.CSS_SELECTOR, f'.file-explorer-panel[data-file-explorer-view="finder"] .file-tree-row[data-path="{path}"]') if row.is_displayed())
    assert row.is_displayed()
    ActionChains(browser).double_click(row).perform()
    item = 'file:' + str(path)
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("""
      const item=arguments[0], panel=panelNodes.get(item);
      return itemIsActivePaneTab(item) && editorViewModeFor(fileItemPath(item),item)==='preview'
        && fileStateFor(fileItemPath(item))?.content===arguments[1]
        && panel?._pmView?.state.doc.textContent===arguments[1].trimEnd();
    """, item, path.read_text(encoding='utf-8')))
    before = browser.execute_script("""
      const item=arguments[0], panel=panelNodes.get(item), rect=panel.getBoundingClientRect();
      return {slot:slotForItem(item),width:rect.width,height:rect.height,selected:activeItemForSide(slotForItem(item)),
        content:fileStateFor(fileItemPath(item)).content,document:panel._pmView.state.doc.textContent};
    """, item)
    assert before['slot'] and before['width'] > 0 and before['height'] > 0 and before['selected'] == item, before
    browser.execute_script('window.__settingsNavigationRelease()')
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script('return window.__settingsNavigationDone === true'))
    after = browser.execute_script("""
      const item=arguments[0], panel=panelNodes.get(item), rect=panel.getBoundingClientRect();
      return {slot:slotForItem(item),width:rect.width,height:rect.height,selected:activeItemForSide(slotForItem(item)),
        content:fileStateFor(fileItemPath(item)).content,document:panel._pmView.state.doc.textContent};
    """, item)
    assert after == before, {'before':before, 'after':after}
    browser.execute_script('window.fetch = window.__settingsNavigationFetch; saveSettingsPatch = window.__settingsNavigationSave;')


@pytest.mark.parametrize("new_edit", [False, True], ids=["clean-reopen", "dirty-reopen"])
def test_native_save_close_discard_reopen(browser, isolated_dev_server_factory, request, new_edit):
    server = isolated_dev_server_factory('editor-save-reopen-audit')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'save-race.md'
    path.write_text('base\n', encoding='utf-8')
    session = server.tmux.sessions[0]
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    setup = browser.execute_async_script("""
      const path = arguments[0], done = arguments[arguments.length-1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof openFileInEditor === 'function', {timeoutMs:10000});
        fileEditorAutosaveEnabled = false;
        await openFileExplorerManualRoot(dirnameOf(path));
        const item = await openFileInEditor(path, 'save-race.md', {viewMode:'edit',userInitiated:true});
        await window.__yomuxTestWaitFor(() => panelNodes.get(item)?._cmView, {timeoutMs:10000});
        await openFileEditorPane(path,{item});
        await window.__yomuxTestWaitFor(() => panelNodes.get(item)?._cmView?.dom.getBoundingClientRect().height > 0, {timeoutMs:5000});
        await syncServerWatchRoots({immediate:true});
        await window.__yomuxTestWaitFor(() => clientEventTransportState.connected && serverWatchRootsState.registered, {timeoutMs:10000});
        window.__saveAuditEvents = [];
        window.__saveAuditHeldEvents = [];
        window.__saveAuditOriginalHandle = handleClientPushEvent;
        handleClientPushEvent = (type, payload, envelope) => {
          if (type === 'files_changed' || type === 'fs_changed') {
            window.__saveAuditEvents.push({type,payload,envelope});
            window.__saveAuditHeldEvents.push({type,payload,envelope});
            return true;
          }
          return window.__saveAuditOriginalHandle(type,payload,envelope);
        };
        const panel = panelNodes.get(item), view = panel._cmView;
        view.dispatch({changes:{from:0,to:view.state.doc.length,insert:'saved edit\\n'}});
        flushCodeMirrorSource(panel,path);
        window.__saveAuditOriginalFetch = window.fetch;
        window.fetch = (url, options) => String(url) === '/api/fs/write'
          ? new Promise((resolve,reject) => {window.__releaseAuditWrite = () => window.__saveAuditOriginalFetch(url,options).then(resolve,reject);})
          : window.__saveAuditOriginalFetch(url,options);
        window.__saveAuditPromise = saveFileEditor(path,panel);
        await window.__yomuxTestWaitFor(() => Boolean(window.__releaseAuditWrite), {timeoutMs:5000});
        fileEditorAutosaveEnabled = false;
        window.__saveAuditClose = closeFileTab(path);
        return {item,content:fileState.get(path).content,dirty:fileState.get(path).dirty,
          signature:serverWatchRootsState.signature};
      })().then(done,error => done({error:String(error.stack || error)}));
    """, str(path))
    assert 'error' not in setup, setup
    WebDriverWait(browser, 5).until(lambda driver: driver.find_element(By.CSS_SELECTOR, '[data-dialog-action="discard"]').is_displayed())
    browser.find_element(By.CSS_SELECTOR, '[data-dialog-action="discard"]').click()
    reopened = browser.execute_async_script("""
      const path = arguments[0], newEdit = arguments[1], done = arguments[arguments.length-1];
      (async () => {
        const closed = await window.__saveAuditClose;
        const item = await openFileInEditor(path,'save-race.md',{viewMode:'edit',userInitiated:true});
        await window.__yomuxTestWaitFor(() => panelNodes.get(item)?._cmView, {timeoutMs:10000});
        await openFileEditorPane(path,{item});
        await window.__yomuxTestWaitFor(() => panelNodes.get(item)?._cmView?.dom.getBoundingClientRect().height > 0, {timeoutMs:5000});
        await syncServerWatchRoots({immediate:true});
        if (serverWatchRootsState.watchDiffPromise) await serverWatchRootsState.watchDiffPromise;
        fileEditorAutosaveEnabled = false;
        if (newEdit) {
          const panel = panelNodes.get(item), view = panel._cmView;
          view.dispatch({changes:{from:0,to:view.state.doc.length,insert:'new unsaved edit\\n'}});
          flushCodeMirrorSource(panel,path);
        }
        return {closed,item,content:fileState.get(path).content,dirty:fileState.get(path).dirty};
      })().then(done,error => done({error:String(error.stack || error)}));
    """, str(path), new_edit)
    assert 'error' not in reopened, reopened
    immediate = browser.execute_async_script("""
      const path = arguments[0], item = arguments[1], done = arguments[arguments.length-1];
      (async () => {
        window.__releaseAuditWrite();
        const saved = await window.__saveAuditPromise;
        return {saved,content:fileState.get(path)?.content,original:fileState.get(path)?.original,
          dirty:fileState.get(path)?.dirty,mtime:fileState.get(path)?.mtime,
          document:panelNodes.get(item)?._cmView?.state.doc.toString(),
          ack:{present:fileEditorSelfWriteAcks.has(path),ownsCurrent:fileEditorSelfWriteAcks.get(path)?.state === fileState.get(path)},events:window.__saveAuditEvents};
      })().then(done,error => done({error:String(error.stack || error)}));
    """, str(path), reopened['item'])
    disk = path.read_text(encoding='utf-8')
    watched = browser.execute_async_script("""
      const path = arguments[0], item = arguments[1], done = arguments[arguments.length-1];
      (async () => {
        await window.__yomuxTestWaitFor(() => window.__saveAuditEvents.some(event =>
          (event.payload.paths || []).includes(path) || (event.payload.files || []).some(file => file.path === path)), {timeoutMs:10000});
        for (const event of window.__saveAuditHeldEvents.splice(0)) window.__saveAuditOriginalHandle(event.type,event.payload,event.envelope);
        flushQueuedClientPushEvents();
        if (serverWatchRootsState.watchDiffPromise) await serverWatchRootsState.watchDiffPromise;
        await new Promise(requestAnimationFrame);
        return {content:fileState.get(path)?.content,original:fileState.get(path)?.original,dirty:fileState.get(path)?.dirty,
          mtime:fileState.get(path)?.mtime,externalChanged:fileState.get(path)?.externalChanged,
          document:panelNodes.get(item)?._cmView?.state.doc.toString(),events:window.__saveAuditEvents,
          ack:{present:fileEditorSelfWriteAcks.has(path),ownsCurrent:fileEditorSelfWriteAcks.get(path)?.state === fileState.get(path)}};
      })().then(done,error => done({error:String(error.stack || error)}));
    """, str(path), reopened['item'])
    browser.execute_script('window.fetch = window.__saveAuditOriginalFetch; handleClientPushEvent = window.__saveAuditOriginalHandle;')
    evidence = {'setup':setup,'reopened':reopened,'immediate':immediate,'disk':disk,'watched':watched}
    assert immediate['saved'] is True and disk == 'saved edit\n', evidence
    assert immediate['ack']['present'] is False, evidence
    if new_edit:
        assert watched['document'] == 'new unsaved edit\n', evidence
        assert watched['content'] == 'new unsaved edit\n', evidence
        assert watched['dirty'] is True and watched['externalChanged'], evidence
        assert watched['original'] == 'base\n', evidence
    else:
        assert watched['document'] == disk and watched['content'] == disk, evidence
        assert watched['original'] == disk and watched['dirty'] is False, evidence


@pytest.mark.parametrize("action", ["merge-draft", "merge-disk"])
def test_native_retired_conflict_dialog_preserves_reopened_dirty_editor(browser, isolated_dev_server_factory, request, action):
    server = isolated_dev_server_factory('editor-conflict-lifetime')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'conflict-race.md'
    path.write_text('base\n', encoding='utf-8')
    session = server.tmux.sessions[0]
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    setup = browser.execute_async_script("""
      const path = arguments[0], done = arguments[arguments.length-1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof openFileInEditor === 'function', {timeoutMs:10000});
        await openFileExplorerManualRoot(dirnameOf(path));
        const item = await openFileInEditor(path,'conflict-race.md',{viewMode:'edit',userInitiated:true});
        await window.__yomuxTestWaitFor(() => panelNodes.get(item)?._cmView, {timeoutMs:10000});
        await openFileEditorPane(path,{item});
        await window.__yomuxTestWaitFor(() => panelNodes.get(item)?._cmView?.dom.getBoundingClientRect().height > 0, {timeoutMs:5000});
        fileEditorAutosaveEnabled = false;
        const panel = panelNodes.get(item), view = panel._cmView;
        view.dispatch({changes:{from:0,to:view.state.doc.length,insert:'old edit\\n'}});
        flushCodeMirrorSource(panel,path);
        return {item};
      })().then(done,error => done({error:String(error.stack || error)}));
    """, str(path))
    assert 'error' not in setup, setup
    path.write_text('disk replacement\n', encoding='utf-8')
    browser.execute_script("""
      const path = arguments[0];
      delete fileState.get(path).externalChanged;
      window.__conflictPriorOperations = new Set(apiOperationState.records.keys());
      window.__oldConflictSave = saveFileEditor(path,panelNodes.get(arguments[1]));
    """, str(path), setup['item'])
    WebDriverWait(browser, 5).until(lambda driver: driver.find_element(By.CSS_SELECTOR, '.file-editor-merge-dialog').is_displayed())
    receipts = browser.execute_script("""
      const path = arguments[0];
      return [...apiOperationState.records.values()].filter(record =>
        !window.__conflictPriorOperations.has(record.id) && record.kind === 'filesystem_operation'
        && record.context.operation === 'write' && record.context.path === path).map(record => {
          const terminal = apiOperationState.terminal.get(record.id);
          return {id:record.id,phase:record.phase,status:terminal?.status,
            terminalId:terminal?.operation?.id,state:terminal?.result?.state,
            errorCode:terminal?.result?.error?.code,messageKey:terminal?.result?.user_message?.key};
        });
    """, str(path))
    assert len(receipts) == 1, receipts
    receipt = receipts[0]
    assert receipt['phase'] == 'terminal' and receipt['terminalId'] == receipt['id'], receipt
    assert receipt['status'] == 409 and receipt['state'] == 'failed', receipt
    assert receipt['errorCode'] == 'changed_on_disk' and receipt['messageKey'] == 'fs.error.changedOnDisk', receipt
    consume_only_expected_js_debug_api_error(browser, path='/api/fs/write', status=409, method='POST', query={})
    reopened = browser.execute_async_script("""
      const path = arguments[0], done = arguments[arguments.length-1];
      (async () => {
        const closed = await closeFileTab(path,{confirmDirty:false});
        const item = await openFileInEditor(path,'conflict-race.md',{viewMode:'edit',userInitiated:true});
        await window.__yomuxTestWaitFor(() => panelNodes.get(item)?._cmView, {timeoutMs:10000});
        await openFileEditorPane(path,{item});
        await window.__yomuxTestWaitFor(() => panelNodes.get(item)?._cmView?.dom.getBoundingClientRect().height > 0, {timeoutMs:5000});
        fileEditorAutosaveEnabled = false;
        const panel = panelNodes.get(item), view = panel._cmView;
        view.dispatch({changes:{from:0,to:view.state.doc.length,insert:'fresh unsaved edit\\n'}});
        flushCodeMirrorSource(panel,path);
        return {closed,item,content:fileState.get(path).content,dirty:fileState.get(path).dirty};
      })().then(done,error => done({error:String(error.stack || error)}));
    """, str(path))
    assert 'error' not in reopened and reopened['closed'] is True, reopened
    browser.find_element(By.CSS_SELECTOR, f'.file-editor-merge-dialog [data-dialog-action="{action}"]').click()
    settled = browser.execute_async_script("""
      const path = arguments[0], item = arguments[1], done = arguments[arguments.length-1];
      (async () => ({saved:await window.__oldConflictSave,content:fileState.get(path)?.content,
        original:fileState.get(path)?.original,dirty:fileState.get(path)?.dirty,
        document:panelNodes.get(item)?._cmView?.state.doc.toString(),
        compareVisible:Boolean(document.querySelector('.file-editor-compare-dialog'))}))()
        .then(done,error => done({error:String(error.stack || error)}));
    """, str(path), reopened['item'])
    assert 'error' not in settled, settled
    assert settled['saved'] is False and settled['compareVisible'] is False, settled
    assert settled['content'] == 'fresh unsaved edit\n' and settled['document'] == settled['content'], settled
    assert settled['original'] == 'disk replacement\n' and settled['dirty'] is True, settled
    assert path.read_text(encoding='utf-8') == 'disk replacement\n'


@pytest.mark.parametrize("autosave", [False, True], ids=["manual-save", "autosave"])
def test_native_save_close_keeps_native_input_typed_during_write(browser, isolated_dev_server_factory, request, autosave):
    server = isolated_dev_server_factory('editor-close-typing')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'close-typing.md'
    path.write_text('base\n', encoding='utf-8')
    session = server.tmux.sessions[0]
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    setup = browser.execute_async_script("""
      const path = arguments[0], done = arguments[arguments.length-1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof openFileInEditor === 'function', {timeoutMs:10000});
        await openFileExplorerManualRoot(dirnameOf(path));
        const item = await openFileInEditor(path,'close-typing.md',{viewMode:'edit',userInitiated:true});
        await window.__yomuxTestWaitFor(() => panelNodes.get(item)?._cmView, {timeoutMs:10000});
        await openFileEditorPane(path,{item});
        await window.__yomuxTestWaitFor(() => panelNodes.get(item)?._cmView?.dom.getBoundingClientRect().height > 0, {timeoutMs:5000});
        fileEditorAutosaveEnabled = false;
        window.__closeTypingOriginalFetch = window.fetch;
        window.__closeTypingReleases = [];
        window.fetch = (url,options) => String(url) === '/api/fs/write'
          ? new Promise((resolve,reject) => window.__closeTypingReleases.push(() =>
            window.__closeTypingOriginalFetch(url,options).then(resolve,reject)))
          : window.__closeTypingOriginalFetch(url,options);
        return {item};
      })().then(done,error => done({error:String(error.stack || error)}));
    """, str(path))
    assert 'error' not in setup, setup
    editor = browser.execute_script('return panelNodes.get(arguments[0])._cmView.contentDOM', setup['item'])
    visibility = browser.execute_script("""
      const item = arguments[0], panel = panelNodes.get(item), ancestors = [];
      for (let node = panel._cmView.contentDOM; node && ancestors.length < 9; node = node.parentElement) {
        const rect = node.getBoundingClientRect(), style = getComputedStyle(node);
        ancestors.push({tag:node.tagName,classes:node.className,hidden:node.hidden,display:style.display,
          visibility:style.visibility,width:rect.width,height:rect.height});
      }
      return {mode:editorViewModeFor(arguments[1],item),slot:slotForItem(item),
        selected:activeItemForSide(slotForItem(item)),ancestors};
    """, setup['item'], str(path))
    WebDriverWait(browser, 5).until(lambda _driver: editor.is_displayed(), message=str(visibility))
    _type_relocation_text(browser, setup['item'], 'first edit')
    browser.execute_script('fileEditorAutosaveEnabled = arguments[1]; window.__closeTypingPromise = closeFileTab(arguments[0]);', str(path), autosave)
    if not autosave:
        WebDriverWait(browser, 5).until(lambda driver: driver.find_element(By.CSS_SELECTOR, '.file-editor-close-dialog').is_displayed())
        browser.find_element(By.CSS_SELECTOR, '.file-editor-close-dialog [data-dialog-action="save"]').click()
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script('return window.__closeTypingReleases.length === 1'))
    editor = browser.execute_script('return panelNodes.get(arguments[0])._cmView.contentDOM', setup['item'])
    WebDriverWait(browser, 5).until(lambda _driver: editor.is_displayed())
    editor.click()
    editor.send_keys(Keys.END)
    for character in ' second edit':
        editor.send_keys(character)
    result = browser.execute_async_script("""
      const path = arguments[0], item = arguments[1], done = arguments[arguments.length-1];
      (async () => {
        const before = panelNodes.get(item)?._cmView?.state.doc.toString();
        window.__closeTypingReleases.shift()();
        const closed = await window.__closeTypingPromise;
        return {closed,before,content:fileState.get(path)?.content,dirty:fileState.get(path)?.dirty,
          document:panelNodes.get(item)?._cmView?.state.doc.toString()};
      })().then(done,error => done({error:String(error.stack || error)}));
    """, str(path), setup['item'])
    assert 'error' not in result, result
    assert result['closed'] is False and result['dirty'] is True, result
    assert 'first edit second edit' in result['before'], result
    assert result['document'] == result['before'] and result['content'].rstrip('\n') == result['before'].rstrip('\n'), result
    assert path.read_text(encoding='utf-8').rstrip('\n') == 'first edit'
    browser.execute_async_script("""
      const path = arguments[0], done = arguments[arguments.length-1];
      (async () => {
        window.fetch = window.__closeTypingOriginalFetch;
        await Promise.all(window.__closeTypingReleases.splice(0).map(release => release()));
        await window.__yomuxTestWaitFor(() => !fileEditorSaveOwners.has(path), {timeoutMs:10000});
      })().then(done,error => done({error:String(error)}));
    """, str(path))


def _click_editor_preference(browser, path, value):
    # Preferences can reconcile metadata between WebDriver's lookup and click commands.
    # A native coordinate click targets the current DOM, as an actual pointer does.
    click_visible_selector(browser, f'[data-setting-path="appearance.{path}"][value="{value}"]')


def _set_editor_cursor_blink_delay(browser, delay):
    select_current_value(browser, '[data-setting-path="editor.cursor_blink_delay_ms"]', delay)
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return initialSetting('editor.cursor_blink_delay_ms')===arguments[0] && document.body.classList.contains('editor-cursor-static')===(arguments[0]===0)", delay))


def _open_cursor_editors(browser, isolated_dev_server_factory, request, theme, mode):
    server = isolated_dev_server_factory('vieweditor-cursor')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'cursor.md'
    path.write_text('# Cursor color\n\nKeep this text.\n', encoding='utf-8')
    browser.get(server.base_url + '/?sessions=' + quote(server.tmux.sessions[0]))
    setup = browser.execute_async_script("""
      const [path,theme,mode,session,done]=arguments;
      (async()=>{
        await window.__yomuxTestWaitFor(()=>initialAppShellPainted && typeof openFileInEditor==='function',{timeoutMs:10000});
        setTabberMode('advanced');
        await setGlobalThemeMode(theme);
        const item=await openFileInEditor(path,basenameOf(path),{viewMode:mode,userInitiated:true});
        window.__cursorTerminal=session;
        await selectSession(window.__cursorTerminal,{userInitiated:true});
        await selectSession(prefsItemId,{userInitiated:true});
        const slots=emptyLayoutSlots();
        slots[layoutTreeKey]=splitNode('row',leafNode('left'),splitNode('col',leafNode('rightTop'),leafNode('rightBottom'),70),50);
        slots.left=paneStateWithTabs([item],item);slots.rightTop=paneStateWithTabs([prefsItemId],prefsItemId);
        slots.rightBottom=paneStateWithTabs([window.__cursorTerminal],window.__cursorTerminal);
        applyLayoutSlots(slots,{prune:false});
        await window.__yomuxTestWaitFor(()=>panelNodes.get(item)?._pmView && document.querySelector('[data-setting-path="appearance.editor_cursor_color"]'),{timeoutMs:10000});
        window.__cursorView=panelNodes.get(item)._pmView;
        return item;
      })().then(done,error=>done({error:String(error.stack||error)}));
    """, str(path), theme, mode, server.tmux.sessions[0])
    assert isinstance(setup, str), setup
    for path in ['appearance.editor_cursor_color', 'appearance.editor_cursor_style']:
        toggle = browser.execute_script("return document.querySelector('[data-setting-path=\"'+arguments[0]+'\"]').closest('.preferences-section').querySelector('.preferences-section-toggle')", path)
        if toggle.get_attribute('aria-expanded') == 'false':
            toggle.click()
    return setup


def _place_cursor_on_first_character(browser, item, surface):
    deadline = time.monotonic() + 5
    last = {}

    def selection_converged(driver, expected):
        last.update(driver.execute_script("""
          const panel=panelNodes.get(arguments[0]),pm=arguments[1]==='pm',view=pm?panel._pmView:panel._cmView;
          const selection=window.getSelection(),inside=selection?.focusNode && selection?.anchorNode
            && view.dom.contains(selection.focusNode) && view.dom.contains(selection.anchorNode);
          return {position:pm?view.state.selection.head:view.state.selection.main.head,
            anchor:pm?view.state.selection.anchor:view.state.selection.main.anchor,
            domPosition:inside?view.posAtDOM(selection.focusNode,selection.focusOffset):null,
            domAnchor:inside?view.posAtDOM(selection.anchorNode,selection.anchorOffset):null,
            focused:pm?view.hasFocus():view.hasFocus,
            length:pm?view.state.doc.content.size:view.state.doc.length,
            text:pm?view.state.doc.textContent:view.state.doc.toString()};
        """, item, surface))
        return last['focused'] and last['position'] == last['domPosition'] and last['anchor'] == last['domAnchor'] \
            and (expected is None or last['position'] == expected)

    def wait_for_selection(phase, expected=None):
        try:
            WebDriverWait(browser, max(0, deadline - time.monotonic())).until(
                lambda driver: selection_converged(driver, expected), message=f'Native selection {phase}: {surface}')
        except TimeoutException as error:
            error.add_note(json.dumps(last, sort_keys=True))
            raise

    # Native clicks and navigation publish DOM selection before the editor's selectionchange
    # observer runs. Each next key must consume the completed preceding selection transaction.
    wait_for_selection('after click')
    native_document_start(browser)
    wait_for_selection('after document start', 1 if surface == 'pm' else 0)
    native_logical_key(browser, 'ArrowRight')
    wait_for_selection('after arrow right', 2 if surface == 'pm' else 1)
    assert last['position'] < last['length'], last


@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_editor_cursor_blink_delay_and_static_apply_live(browser, isolated_dev_server_factory, request, theme):
    item = _open_cursor_editors(browser, isolated_dev_server_factory, request, theme, 'split')
    _set_editor_cursor_blink_delay(browser, 300)
    state = browser.execute_script("""
      const panel=panelNodes.get(arguments[0]);window.__blinkCM=panel._cmView;window.__blinkPM=panel._pmView;
      const style=document.querySelector('[data-setting-path="appearance.editor_cursor_style"]');
      const delay=document.querySelector('[data-setting-path="editor.cursor_blink_delay_ms"]');
      return {section:style.closest('.preferences-section').dataset.preferenceSection,
        sameSection:style.closest('.preferences-section')===delay.closest('.preferences-section'),
        cycle:getComputedStyle(document.documentElement).getPropertyValue('--editor-cursor-blink-cycle').trim(),
        examples:[...document.querySelectorAll('.preferences-cursor-example')].map(node=>getComputedStyle(node).animationDuration)};
    """, item)
    assert state == {'section': 'editor_options', 'sameSection': True, 'cycle': '600ms', 'examples': ['0.6s', '0.6s']}, state
    for surface in ['cm', 'pm']:
        editor = browser.execute_script("return arguments[0]==='pm'?window.__blinkPM.dom:window.__blinkCM.contentDOM", surface)
        editor.click()
        _place_cursor_on_first_character(browser, item, surface)
        phases = browser.execute_async_script("""
          const [surface,done]=arguments,started=performance.now(),seen=new Set();
          const node=surface==='pm'?window.__blinkPM.dom:window.__blinkCM.dom.querySelector('.cm-cursorLayer');
          function sample(){
            const style=getComputedStyle(node);seen.add(surface==='pm'?style.caretColor:style.opacity);
            if(seen.size>1){done({values:[...seen],duration:style.animationDuration});return;}
            if(performance.now()-started>4000){done({error:'caret never blinked',values:[...seen]});return;}
            requestAnimationFrame(sample);
          }
          sample();
        """, surface)
        assert 'error' not in phases and phases['duration'] == '0.6s', phases
        if surface == 'pm':
            assert 'rgba(0, 0, 0, 0)' in phases['values'], phases
        else:
            assert set(phases['values']) == {'1', '0'}, phases
    # Native typing resets the custom native caret animation synchronously.
    browser.execute_script("""
      const view=window.__blinkPM;window.__blinkDispatch=view.dispatch;window.__blinkResets=[];
      view.dispatch=function(...args){const result=window.__blinkDispatch.apply(this,args);
        window.__blinkResets.push(this.dom.getAnimations().find(a=>a.animationName==='editor-native-cursor-blink')?.currentTime);return result;};
    """)
    try:
        editor.send_keys('x')
        resets = browser.execute_script('return window.__blinkResets')
        assert resets and all(value is not None and value <= 2 for value in resets), resets
    finally:
        browser.execute_script('window.__blinkPM.dispatch=window.__blinkDispatch')
    composition = browser.execute_script("""
      const view=window.__blinkPM;
      view.dom.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
      const animation=view.dom.getAnimations().find(a=>a.animationName==='editor-native-cursor-blink');
      const paused=animation.playState==='paused' && animation.currentTime===0;
      view.dom.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}));
      return {paused,resumed:animation.playState==='running'};
    """)
    assert composition == {'paused': True, 'resumed': True}, composition
    _set_editor_cursor_blink_delay(browser, 0)
    for surface in ['cm', 'pm']:
        editor = browser.execute_script("return arguments[0]==='pm'?window.__blinkPM.dom:window.__blinkCM.contentDOM", surface)
        editor.click()
        stable = browser.execute_async_script("""
          const [surface,done]=arguments,start=performance.now(),states=new Set();
          const node=surface==='pm'?window.__blinkPM.dom:window.__blinkCM.dom.querySelector('.cm-cursorLayer');
          function sample(){
            const style=getComputedStyle(node);states.add(surface==='pm'?style.caretColor:style.opacity);
            if(performance.now()-start>=700){done({values:[...states],animation:style.animationName,
              examples:[...document.querySelectorAll('.preferences-cursor-example')].map(n=>({animation:getComputedStyle(n).animationName,opacity:getComputedStyle(n).opacity}))});return;}
            requestAnimationFrame(sample);
          }
          sample();
        """, surface)
        assert stable['animation'] == 'none' and len(stable['values']) == 1, stable
        assert stable['values'][0] not in ['hidden', 'rgba(0, 0, 0, 0)'], stable
        assert stable['examples'] == [{'animation': 'none', 'opacity': '1'}] * 2, stable
    assert browser.execute_script('return panelNodes.get(arguments[0])._cmView===window.__blinkCM && panelNodes.get(arguments[0])._pmView===window.__blinkPM', item)
    saved = browser.execute_async_script("saveSettingsPatch({editor:{cursor_blink_delay_ms:450}}).then(()=>arguments[0](initialSetting('editor.cursor_blink_delay_ms')),error=>arguments[0]({error:String(error)}))")
    assert saved == 450, saved
    browser.refresh()
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("return typeof initialAppShellPainted!=='undefined' && initialAppShellPainted && typeof selectSession==='function'"))
    browser.execute_async_script("selectSession(prefsItemId,{userInitiated:true}).then(arguments[0])")
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("return document.querySelector('[data-setting-path=\"editor.cursor_blink_delay_ms\"]')?.value==='450'"))


@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_editor_cursor_style_examples_blink_in_selected_color(browser, isolated_dev_server_factory, request, tmp_path, theme):
    item = _open_cursor_editors(browser, isolated_dev_server_factory, request, theme, 'split')
    for value, active in [('neon-cyan', None), ('neon-green', None), ('theme', None), ('theme', 'purple'), ('theme', 'yellow')]:
        _click_editor_preference(browser, 'editor_cursor_color', value)
        if active:
            _click_editor_preference(browser, 'active_color', active)
            WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return initialSetting('appearance.active_color')===arguments[0]", active))
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script('return fileEditorCursorColor===arguments[0]', value))
        browser.execute_script("document.querySelector('.preferences-cursor-example').scrollIntoView({block:'center'})")
        metrics = browser.execute_script("""
          const panel=panelNodes.get(arguments[0]),expected=getComputedStyle(panel._pmView.dom).caretColor;
          const samples=[...document.querySelectorAll('.preferences-cursor-example')];
          return {expected,colors:samples.map(node=>getComputedStyle(node).backgroundColor),
            durations:samples.map(node=>getComputedStyle(node).animationDuration),
            reference:getComputedStyle(panel._cmView.dom.querySelector('.cm-cursorLayer')).animationDuration};
        """, item)
        assert metrics['colors'] == [metrics['expected']] * 2, metrics
        assert metrics['durations'] == [metrics['reference']] * 2 and metrics['reference'] == '1.2s', metrics
        if value != 'neon-cyan':
            continue
        expected_rgb = tuple(int(channel) for channel in metrics['expected'].replace('rgb(', '').replace(')', '').split(','))
        for visible in [True, False]:
            phase = browser.execute_async_script("""
              const [visible,done]=arguments,started=performance.now();
              function sample(){
                const nodes=[...document.querySelectorAll('.preferences-cursor-example')];
                const time=nodes[0].getAnimations()[0].currentTime%1200;
                if(time>=(visible?100:700) && time<=(visible?300:900)){
                  nodes.forEach(node=>node.getAnimations()[0].pause());
                  done({ratio:devicePixelRatio,nodes:nodes.map(node=>{
                    const r=node.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2,opacity:getComputedStyle(node).opacity};
                  })});return;
                }
                if(performance.now()-started>5000){done({error:'cursor examples did not reach expected blink phase'});return;}
                requestAnimationFrame(sample);
              }
              requestAnimationFrame(sample);
            """, visible)
            try:
                assert 'error' not in phase, phase
                assert [node['opacity'] for node in phase['nodes']] == ['1' if visible else '0'] * 2, phase
                image = browser_screenshot_rgb(browser)
                image.save(tmp_path / f'cursor-examples-{theme}-{visible}.png')
                for node in phase['nodes']:
                    pixel = image.getpixel((int(node['x'] * phase['ratio']), int(node['y'] * phase['ratio'])))
                    assert (pixel == expected_rgb) == visible, (visible, phase, metrics, pixel)
            finally:
                browser.execute_script("document.querySelectorAll('.preferences-cursor-example').forEach(node=>node.getAnimations().forEach(animation=>animation.play()))")


@pytest.mark.parametrize('theme', ['dark', 'light'])
@pytest.mark.parametrize('mode', ['preview', 'split'])
def test_editor_cursor_styles_paint_blocks_and_lines(browser, isolated_dev_server_factory, request, tmp_path, theme, mode):
    setup = _open_cursor_editors(browser, isolated_dev_server_factory, request, theme, mode)
    _set_editor_cursor_blink_delay(browser, 0)
    def wait_for_vieweditor_selection():
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("""
          const view=window.__cursorView, selection=window.getSelection();
          return view.hasFocus() && selection.focusNode && view.dom.contains(selection.focusNode)
            && view.state.selection.head===view.posAtDOM(selection.focusNode,selection.focusOffset)
            && view.state.selection.anchor===view.posAtDOM(selection.anchorNode,selection.anchorOffset);
        """))

    # Assert painted carets on both real editors; radio state or caret color alone cannot prove shape.
    browser.execute_script("""
      const style=document.createElement('style');style.textContent='.cm-cursor {opacity:1 !important}';
      document.head.append(style);window.__cursorView.dom.style.caretAnimation='manual';
    """)
    for style in ['block', 'line']:
        _click_editor_preference(browser, 'editor_cursor_style', style)
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script('return fileEditorCursorStyle===arguments[0]', style))
        for surface in ['pm', 'cm'] if mode == 'split' else ['pm']:
            editor = browser.execute_script("const panel=panelNodes.get(arguments[0]);return arguments[1]==='pm'?panel._pmView.dom:panel._cmView.contentDOM", setup, surface)
            editor.click()
            _place_cursor_on_first_character(browser, setup, surface)
            if surface == 'pm':
                wait_for_vieweditor_selection()
            metrics = browser.execute_script("""
              const panel=panelNodes.get(arguments[0]),pm=arguments[1]==='pm',view=pm?panel._pmView:panel._cmView;
              const position=pm?view.state.selection.head:view.state.selection.main.head;
              const box=view.coordsAtPos(position),next=view.coordsAtPos(position+1);
              let glyph;
              if(pm){
                const from=view.domAtPos(position),to=view.domAtPos(position+1),range=document.createRange();
                range.setStart(from.node,from.offset);range.setEnd(to.node,to.offset);glyph=range.getBoundingClientRect();
              }
              const paint=getComputedStyle(pm?view.dom:view.dom.querySelector('.cm-cursor'));
              return {x:pm?glyph.left:Math.min(box.left,next.left),y:box.top,height:box.bottom-box.top,
                cell:pm?glyph.width:Math.abs(next.left-box.left),color:pm?paint.caretColor:paint[arguments[2]==='block'?'backgroundColor':'borderLeftColor'],
                shape:pm?paint.caretShape:null,ratio:devicePixelRatio};
            """, setup, surface, style)
            screenshot = tmp_path / f'caret-{surface}-{style}-{theme}.png'
            color = tuple(int(value) for value in metrics['color'].replace('rgb(', '').replace(')', '').split(','))
            ratio = metrics['ratio']
            left = round(metrics['x'] * ratio)
            top = round(metrics['y'] * ratio)
            right = round((metrics['x'] + max(metrics['cell'], 4) + 2) * ratio)
            bottom = round((metrics['y'] + metrics['height']) * ratio)

            def caret_painted_width(_driver):
                image = browser_screenshot_rgb(browser)
                image.save(screenshot)
                columns = [x for x in range(max(0, left - 1), min(image.width, right))
                           if sum(all(abs(a-b) <= 4 for a, b in zip(image.getpixel((x, y)), color))
                                  for y in range(max(0, top), min(image.height, bottom))) >= 2 * ratio]
                return len(columns) / ratio

            # The native caret paints on a later frame than the selection change, which a loaded host can delay.
            painted_width = WebDriverWait(browser, 5, poll_frequency=0.1).until(
                caret_painted_width, f'{surface} {style} caret never painted: {metrics} screenshot={screenshot}')
            if style == 'block':
                assert painted_width >= metrics['cell'] * 0.65, f'{surface} {style} {metrics} painted={painted_width} screenshot={screenshot}'
            else:
                assert 0 < painted_width <= 3, f'{surface} {style} {metrics} painted={painted_width} screenshot={screenshot}'


@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_saved_cursor_palette_selection_survives_reload(browser, isolated_dev_server_factory, request, theme):
    server = isolated_dev_server_factory('cursor-palette')
    request.addfinalizer(lambda: browser.get('about:blank'))
    browser.get(server.base_url + '/?sessions=__prefs__&layout=left&tabs=left:__prefs__')
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("return typeof setGlobalThemeMode==='function' && typeof initialAppShellPainted!=='undefined' && initialAppShellPainted"))
    browser.execute_async_script("setGlobalThemeMode(arguments[0]).then(arguments[arguments.length-1])", theme)
    for legacy, canonical in [('green', 'neon-green'), ('blue', 'neon-cyan'), ('orange', 'neon-orange'), ('purple', 'neon-magenta')]:
        browser.execute_async_script("saveSettingsPatch({appearance:{editor_cursor_color:arguments[0]}}).then(arguments[arguments.length-1])", legacy)
        browser.refresh()
        WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("""
          return typeof fileEditorCursorColor!=='undefined' && fileEditorCursorColor===arguments[0]
            && document.querySelector(`[data-setting-path="appearance.editor_cursor_color"][value="${arguments[0]}"]`)?.checked;
        """, canonical))
        label = browser.execute_script("return document.querySelector('[data-setting-path=\"appearance.editor_cursor_color\"]:checked').closest('label').textContent.trim()")
        assert label.startswith('Neon ' if theme == 'dark' else 'Dark '), label


@pytest.mark.parametrize('theme', ['dark', 'light'])
@pytest.mark.parametrize('mode', ['preview', 'split'])
def test_vieweditor_cursor_color_updates_live_without_recreating_editor(browser, isolated_dev_server_factory, request, theme, mode):
    setup = _open_cursor_editors(browser, isolated_dev_server_factory, request, theme, mode)
    _set_editor_cursor_blink_delay(browser, 0)
    browser.find_element(By.CSS_SELECTOR, '.ProseMirror p').click()
    migrated = browser.execute_async_script("""
      const done=arguments[arguments.length-1];
      saveSettingsPatch({appearance:{editor_cursor_color:'laser-lime'}}).then(async()=>{
        await window.__yomuxTestWaitFor(()=>document.querySelector('[data-setting-path="appearance.editor_cursor_color"][value="neon-green"]')?.checked,{timeoutMs:5000});
        done({
        saved:initialSetting('appearance.editor_cursor_color'),runtime:fileEditorCursorColor,
        selected:document.querySelector('[data-setting-path="appearance.editor_cursor_color"][value="neon-green"]').checked,
        legacy:document.querySelector('[data-setting-path="appearance.editor_cursor_color"][value="laser-lime"]')!==null
      });},error=>done({error:String(error)})).catch(error=>done({error:String(error)}));
    """)
    assert migrated == {'saved': 'neon-green', 'runtime': 'neon-green', 'selected': True, 'legacy': False}, migrated
    cursor_style = 'line' if theme == 'light' else 'block'
    _click_editor_preference(browser, 'editor_cursor_style', cursor_style)
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script('return fileEditorCursorStyle===arguments[0]', cursor_style))
    paragraph = browser.find_element(By.CSS_SELECTOR, '.ProseMirror p')
    paragraph.click()
    paragraph.send_keys(Keys.ARROW_RIGHT)
    def wait_for_vieweditor_selection():
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("""
          const view=window.__cursorView, selection=window.getSelection();
          return view.hasFocus() && selection.focusNode && view.dom.contains(selection.focusNode)
            && view.state.selection.head===view.posAtDOM(selection.focusNode,selection.focusOffset)
            && view.state.selection.anchor===view.posAtDOM(selection.anchorNode,selection.anchorOffset);
        """))

    wait_for_vieweditor_selection()
    before = browser.execute_script('return {doc:window.__cursorView.state.doc.toJSON(),selection:window.__cursorView.state.selection.toJSON()}')
    for color, expected in [('neon-cyan', 'rgb(14, 116, 144)' if theme == 'light' else 'rgb(102, 255, 255)'), ('yellow', 'rgb(154, 103, 0)' if theme == 'light' else 'rgb(255, 234, 0)')]:
        _click_editor_preference(browser, 'editor_cursor_color', color)
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("""
          const panel=panelNodes.get(arguments[0]);
          return fileEditorCursorColor===arguments[1] && getComputedStyle(panel._pmView.dom).caretColor===arguments[2];
        """, setup, color, expected))
        after = browser.execute_script("""
          const view=panelNodes.get(arguments[0])._pmView;
          return {same:view===window.__cursorView,doc:view.state.doc.toJSON(),selection:view.state.selection.toJSON()};
        """, setup)
        assert after == {'same': True, **before}
    _click_editor_preference(browser, 'editor_cursor_color', 'theme')
    colors = {'green': '#ffea00', 'blue': '#ffea00', 'orange': '#66ffff', 'yellow': '#66ffff', 'purple': '#ffffff', 'white': '#ff66ff'}
    if theme == 'light':
        colors = {'green': '#9a6700', 'blue': '#9a6700', 'orange': '#0e7490', 'yellow': '#0e7490', 'purple': '#6b7280', 'white': '#a21caf'}
    for color, expected in colors.items():
        _click_editor_preference(browser, 'active_color', color)
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return fileEditorCursorColor==='theme' && initialSetting('appearance.active_color')===arguments[0]", color))
        metrics = browser.execute_script("""
          const panel=panelNodes.get(arguments[0]);
          const probe=document.createElement('span');probe.style.color=arguments[1];document.body.append(probe);
          const expected=getComputedStyle(probe).color;probe.remove();
          const cursor=panel._cmView?.dom.querySelector('.cm-cursor');
          return {expected, pm:getComputedStyle(panel._pmView.dom).caretColor, hasCM:Boolean(panel._cmView),
            cm:cursor?getComputedStyle(cursor)[fileEditorCursorStyle==='block'?'backgroundColor':'borderLeftColor']:null};
        """, setup, expected)
        assert metrics['pm'] == metrics['expected'], metrics
        assert not metrics['hasCM'] or metrics['cm'] == metrics['expected'], metrics
        browser.execute_script('return terminals.get(window.__cursorTerminal).container').click()
        WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("""
          const terminal=terminals.get(window.__cursorTerminal);
          const rgb=[1,3,5].map(start=>parseInt(arguments[0].slice(start,start+2),16));
          return focusedPanelItem===window.__cursorTerminal && document.activeElement===terminal.term.textarea
            && terminal.term._core._themeService.colors.cursor.css===arguments[0]
            && getComputedStyle(terminal.container.querySelector('.xterm-viewport'),'::-webkit-scrollbar-thumb').backgroundColor===`rgba(${rgb.join(', ')}, 0.88)`;
        """, expected))
        after = browser.execute_script("""
          const view=panelNodes.get(arguments[0])._pmView;
          return {same:view===window.__cursorView,doc:view.state.doc.toJSON(),selection:view.state.selection.toJSON()};
        """, setup)
        assert after == {'same': True, **before}
    for setting, value in [('editor_cursor_color', 'neon-cyan'), ('active_color', 'yellow')]:
        _click_editor_preference(browser, setting, value)
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("""
      return initialSetting('appearance.active_color')==='yellow'
        && getComputedStyle(window.__cursorView.dom).caretColor===arguments[0];
    """, 'rgb(14, 116, 144)' if theme == 'light' else 'rgb(102, 255, 255)'))
    opposite = 'dark' if theme == 'light' else 'light'
    for setting, value in [('terminal_theme', opposite), ('editor_cursor_color', 'theme')]:
        _click_editor_preference(browser, setting, value)
    browser.execute_script('return terminals.get(window.__cursorTerminal).container').click()
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("""
      return terminals.get(window.__cursorTerminal).term._core._themeService.colors.cursor.css===arguments[0]
        && getComputedStyle(window.__cursorView.dom).caretColor===arguments[1];
    """, '#66ffff' if opposite == 'dark' else '#0e7490',
       'rgb(14, 116, 144)' if theme == 'light' else 'rgb(102, 255, 255)'))


@pytest.mark.browser
def test_finder_native_rename_preserves_input_during_metadata_refresh(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('finder-native-rename')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'note.txt'
    content = 'preserve these bytes\n'
    path.write_text(content, encoding='utf-8')
    _open_relocation_editor(browser, server, path)
    browser.execute_async_script("""
      const done=arguments[arguments.length-1];
      openFileExplorerPane().then(()=>done(true), error=>done({error:String(error)}));
    """)
    rename_finder_file_with_native_input(browser, str(path), 'renamed.txt')
    renamed = path.with_name('renamed.txt')
    WebDriverWait(browser, 2).until(lambda driver: renamed.exists() and driver.execute_script(
        "return Boolean(document.querySelector('#panel-__finder__ .file-tree-row[data-path=\"'+CSS.escape(arguments[0])+'\"]'))", str(renamed)))
    assert not path.exists()
    assert renamed.read_text(encoding='utf-8') == content
    browser.get('about:blank')


@pytest.mark.browser
@pytest.mark.parametrize('copied', [False, True], ids=['primary', 'copied'])
def test_deleted_differ_row_preserves_pending_native_editor_buffer(browser, isolated_dev_server_factory, request, copied):
    server = isolated_dev_server_factory('deleted-differ-buffer')
    request.addfinalizer(lambda: browser.get('about:blank'))
    repo = server.paths.home_dir / 'deleted-repo'
    repo.mkdir()
    path = repo / 'pending.py'
    path.write_text('base\n', encoding='utf-8')
    for args in (['init', '-q', '--initial-branch=main'], ['config', 'user.name', 'Fixture'],
                 ['config', 'user.email', 'fixture@example.invalid'], ['add', path.name], ['commit', '-qm', 'baseline']):
        subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
    session = server.tmux.sessions[0]
    # Differ shows repositories the session's agent touched, never the pane directory.
    transcript = write_codex_rollout(server.paths.home_dir / '.codex' / 'sessions', 'deleted-differ-buffer', server.paths.home_dir,
                                     [codex_exec_record(f'git add {path.name}', repo)])
    respawn_pane_with_codex_agent(server.tmux, f'={session}:', repo, transcript)
    original = _open_relocation_editor(browser, server, path)
    item = browser.execute_async_script("""
      const [path,original,copied,session,done]=arguments;
      (async()=>{
        const item=copied ? await openFileInAdditionalEditorTab(path,basenameOf(path),{viewMode:'edit'}) : original;
        const slots=emptyLayoutSlots();
        slots[layoutTreeKey]=splitNode('row',leafNode('left'),leafNode('right'));
        slots.left=paneStateWithTabs([differItemId],differItemId);
        slots.right=paneStateWithTabs(copied?[original,item]:[item],item);
        applyLayoutSlots(slots);
        fileExplorerChangesSelectedSession=session;
        diffRefFrom='HEAD';diffRefTo='current';diffRefsByRepo={};
        await window.__yomuxTestWaitFor(()=>panelNodes.get(item)?._cmView?.contentDOM.getBoundingClientRect().height>0,{timeoutMs:10000});
        return item;
      })().then(done,error=>done({error:String(error.stack||error)}));
    """, str(path), original, copied, session)
    assert isinstance(item, str), item
    path.unlink()
    ready = browser.execute_async_script("""
      const [session,path,done]=arguments;
      (async()=>{
        await fetchSessionFiles({destination:'differ',session,force:true,freshGit:true,silent:true});
        await window.__yomuxTestWaitFor(()=>[...document.querySelectorAll('[data-open-change-file]')]
          .some(row=>row.dataset.openChangeFile===path&&row.dataset.openChangeStatus==='D'),{timeoutMs:15000});
        // A delayed filesystem absence receipt must not retire a valid historical Differ row.
        await refreshFileExplorerFromPush({removed_paths:[path]}, {fromWatchDiff:true});
        return Boolean([...document.querySelectorAll('[data-open-change-file]')]
          .some(row=>row.dataset.openChangeFile===path&&row.dataset.openChangeStatus==='D'));
      })().then(done,error=>done({error:String(error.stack||error),payload:fileExplorerSessionFilesState.payload,
        visible:fileExplorerSessionFilesPaneIsVisible(),sessions,selected:fileExplorerChangesSelectedSession,
        streamRepos:fileExplorerSessionFilesState.streamRepos?.size,streamSession:fileExplorerSessionFilesState.streamSession,
        collapsedRepos:[...changesRepoCollapsed],collapsedFolders:[...changesFolderCollapsed],
        activeControl:activeChangesControl(panelNodes.get(differItemId))?.outerHTML,
        panel:panelNodes.get(differItemId)?.innerHTML,
        rows:[...document.querySelectorAll('[data-open-change-file]')].map(row=>({...row.dataset}))}));
    """, session, str(path))
    assert ready is True, json.dumps(ready, ensure_ascii=False)
    text = 'pending native buffer'
    _type_relocation_text(browser, item, text)
    row_state = browser.execute_script("""
      const [path,item]=arguments,panel=panelNodes.get(differItemId),groups=panel?.querySelector('.changes-groups');
      return {row:[...document.querySelectorAll('[data-open-change-file]')].find(row=>row.dataset.openChangeFile===path)||null,
        pendingTimer:Boolean(panelNodes.get(item)._cmSerializeTimer),selected:fileExplorerChangesSelectedSession,
        visible:fileExplorerSessionFilesPaneIsVisible(),payload:fileExplorerSessionFilesState.payload,
        streamSession:fileExplorerSessionFilesState.streamSession,
        rendering:groups?{connected:groups.isConnected,rows:groups.querySelectorAll('[data-open-change-file]').length,
          owner:fileTreeRenderOwners.get(groups)}:null,
        rows:[...document.querySelectorAll('[data-open-change-file]')].map(row=>({...row.dataset}))};
    """, str(path), item)
    assert row_state['pendingTimer'], json.dumps(row_state, default=str)
    row = row_state['row']
    assert row is not None, json.dumps(row_state, default=str)
    row.click()
    activated = browser.execute_async_script("""
      const [path,item,done]=arguments;
      window.__yomuxTestWaitFor(()=>fileState.get(path)?.dirty && itemIsActivePaneTab(item),{timeoutMs:10000})
        .then(()=>done(true),error=>done({error:String(error),state:fileState.get(path),active:activePaneItems()}));
    """, str(path), item)
    assert activated is True, activated
    result = browser.execute_script("""
      const [path,item]=arguments,state=fileState.get(path),panel=panelNodes.get(item);
      return {content:state.content,original:state.original,dirty:state.dirty,missing:state.externalMissing,
        document:panel?._cmView?.state.doc.toString()};
    """, str(path), item)
    assert result['content'].rstrip('\n') == result['document'].rstrip('\n') == text, result
    assert result['original'] == 'base\n' and result['dirty'] and result['missing'], result
    assert not path.exists()
    _assert_expected_relocation_absence(browser, server, request, path)
    browser.get('about:blank')


@pytest.mark.browser
@pytest.mark.parametrize('control', ['wrap', 'gutter'])
def test_editor_display_toggle_preserves_pending_native_buffer(browser, isolated_dev_server_factory, request, control):
    server = isolated_dev_server_factory('pending-display-toggle')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'pending.py'
    path.write_text('base\n', encoding='utf-8')
    item = _open_relocation_editor(browser, server, path)
    browser.execute_script('window.__pendingDisplayView=panelNodes.get(arguments[0])._cmView', item)
    _type_relocation_text(browser, item, 'new unsaved text')
    assert browser.execute_script('return Boolean(panelNodes.get(arguments[0])._cmSerializeTimer)', item)
    button = browser.execute_script('return panelNodes.get(arguments[0]).querySelector(arguments[1])', item, f'.file-editor-{control}-panel')
    button.click()
    result = browser.execute_script("""
      const [path,item]=arguments,state=fileState.get(path),view=panelNodes.get(item)._cmView;
      return {content:state.content,dirty:state.dirty,document:view.state.doc.toString(),sameView:view===window.__pendingDisplayView};
    """, str(path), item)
    assert result['content'].rstrip('\n') == result['document'].rstrip('\n') == 'new unsaved text', result
    assert result['dirty'] and result['sameView'], result
    assert path.read_text(encoding='utf-8') == 'base\n'


@pytest.mark.browser
def test_new_file_action_preserves_existing_bytes(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('new-file-exclusive')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'existing.py'
    path.write_text('keep these bytes\n', encoding='utf-8')
    _open_relocation_editor(browser, server, path)
    browser.execute_script("""
      window.__newFileResults=[];window.__newFileCompletions=0;
      const originalCreate=createFileExplorerFile;
      createFileExplorerFile=async()=>{try {return await originalCreate();} finally {window.__newFileCompletions++;}};
      const originalFetch=window.fetch;
      window.fetch=(url,options)=>originalFetch(url,options).then(response=>{
        if(String(url)==='/api/fs/write') window.__newFileResults.push({body:JSON.parse(options.body),status:response.status});
        return response;
      });
    """)
    for count, name in enumerate(['existing.py', 'created.py'], 1):
        buttons = browser.find_elements(By.CSS_SELECTOR, '[data-file-explorer-new-file]')
        next(button for button in buttons if button.is_displayed()).click()
        alert = WebDriverWait(browser, 5).until(lambda driver: driver.switch_to.alert)
        alert.send_keys(name)
        alert.accept()
        WebDriverWait(browser, 10).until(lambda driver: driver.execute_script(
            'return window.__newFileCompletions===arguments[0]', count))
        result = browser.execute_script('return window.__newFileResults.at(-1)')
        assert result['status'] == 202 and result['body']['path'] == str(path.parent / name), result
        assert path.read_text(encoding='utf-8') == 'keep these bytes\n'
    assert (path.parent / 'created.py').read_bytes() == b''
    consume_only_expected_js_debug_api_error(browser, path='/api/fs/write', status=409, method='POST', query={})


def test_native_file_editor_merges_compatible_edits_and_asks_for_overlap(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('editor-three-way-merge')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'merge.txt'
    path.write_text('start\ncontext\nmiddle\ncontext two\nfinish\n', encoding='utf-8')
    item = _open_relocation_editor(browser, server, path)
    wait = WebDriverWait(browser, 15)
    local = 'mine\ncontext\nmiddle\ncontext two\nfinish'
    _type_relocation_text(browser, item, local)
    external = 'start\ncontext\nmiddle\ncontext two\ntheirs\n'
    path.write_text(external, encoding='utf-8')
    combined = 'mine\ncontext\nmiddle\ncontext two\ntheirs\n'
    wait.until(lambda d: d.execute_script("""
      const [path, item, text] = arguments, state = fileState.get(path);
      return state.dirty && !state.externalChanged && state.content === text
        && panelNodes.get(item)._cmView.state.doc.toString() === text;
    """, str(path), item, combined))
    assert path.read_text(encoding='utf-8') == external
    assert not browser.find_elements(By.CSS_SELECTOR, '.file-editor-dialog')
    local_overlap = 'mine next\ncontext\nlocal middle\ncontext two\ntheirs'
    _type_relocation_text(browser, item, local_overlap)
    wait.until(lambda d: d.execute_script("""
      const [path, item, draft] = arguments;
      return fileState.get(path).content === draft && !panelNodes.get(item)._cmSerializeTimer;
    """, str(path), item, local_overlap))
    browser.execute_script('document.activeElement.blur()')
    external = 'start\ncontext\ndisk middle\ncontext two\ntheirs\n'
    path.write_text(external, encoding='utf-8')
    wait.until(lambda d: d.execute_script('return Boolean(fileState.get(arguments[0]).externalChanged)', str(path)))
    browser.find_element(By.CSS_SELECTOR, '.file-editor-save-panel:not([hidden])').click()
    choose = wait.until(lambda d: d.find_element(By.CSS_SELECTOR, '.file-editor-merge-dialog [data-dialog-action="merge-disk"]'))
    assert path.read_text(encoding='utf-8') == external
    choose.click()
    expected = 'mine next\ncontext\ndisk middle\ncontext two\ntheirs\n'
    wait.until(lambda d: d.execute_script("""
      const [path] = arguments, owner = fileEditorSaveOwners.get(path);
      return !owner?.active && !owner?.conflict && !document.querySelector('.file-editor-merge-dialog');
    """, str(path)))
    assert path.read_text(encoding='utf-8') == expected, browser.execute_script("""
      const state = fileState.get(arguments[0]);
      return {draft: state.content, original: state.original, dirty: state.dirty, outside: state.externalChanged};
    """, str(path))
    wait.until(lambda d: d.execute_script("""
      const [path, item, text] = arguments, state = fileState.get(path);
      return !state.dirty && state.original === text && panelNodes.get(item)._cmView.state.doc.toString() === text;
    """, str(path), item, expected))


@pytest.mark.parametrize('extension', ['md', 'txt'])
def test_recent_changes_keeps_source_editor_and_unsaved_draft(browser, isolated_dev_server_factory, request, extension):
    server = isolated_dev_server_factory('recent-changes')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / f'review.{extension}'
    original = '# Original\n\n' + '\n'.join(f'Context {i}' for i in range(18)) + '\nOld ending\n'
    if extension == 'md':
        original = original.replace('Context 0', '- Entry 04 — reviewed and approved')
    updated = original.replace('# Original', '# Updated').replace('Old ending', 'New ending')
    if extension == 'md':
        updated = updated.replace('reviewed and approved', 'reviewed and approved222')
    path.write_text(original, encoding='utf-8')
    item = _open_relocation_editor(browser, server, path)
    button = browser.find_element(By.CSS_SELECTOR, '.file-editor-review-panel:not([hidden])')
    gutter = browser.find_element(By.CSS_SELECTOR, '.file-editor-gutter-panel:not([hidden])')
    for control in (gutter, button):
        appearance = browser.execute_script('''const style=getComputedStyle(arguments[0]);
          return {border:style.borderTopStyle, background:style.backgroundColor, width:style.width}''', control)
        assert appearance['border'] == 'solid' and appearance['background'] != 'rgba(0, 0, 0, 0)', appearance
        assert float(appearance['width'].removesuffix('px')) <= 30, appearance
        assert control.get_attribute('title')
    assert button.tag_name == 'button'
    assert button.text == 'Δ'
    assert button.get_attribute('aria-label') == 'Recent changes'
    review_toolbar = browser.find_element(By.CSS_SELECTOR, '.file-editor-review-toolbar')
    assert not review_toolbar.is_displayed()
    offset = browser.execute_script('''
      const button = arguments[0], box = button.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(button);
      const text = range.getBoundingClientRect();
      return Math.abs((box.left + box.right - text.left - text.right) / 2);
    ''', button)
    assert offset <= 1, offset
    button.click()
    WebDriverWait(browser, 15).until(lambda d: d.execute_script(
        'return Boolean(fileState.get(arguments[0])?.recentReview?.id || fileState.get(arguments[0])?.recentReview?.error)', str(path)))
    assert browser.execute_script('return fileState.get(arguments[0]).recentReview.error || ""', str(path)) == ''
    baseline = browser.execute_script('return fileState.get(arguments[0]).recentReview.original', str(path))
    assert baseline == original
    geometry = browser.execute_script('''
      const row = arguments[0], main = arguments[1].closest('.file-editor-toolbar');
      const controls = [...row.querySelectorAll('button')];
      return {
        below: row.getBoundingClientRect().top >= main.getBoundingClientRect().bottom,
        aligned: controls.every(control => control.getBoundingClientRect().top === controls[0].getBoundingClientRect().top),
        titles: controls.map(control => control.title),
      };
    ''', review_toolbar, button)
    assert geometry == {'below': True, 'aligned': True, 'titles': ['Previous change', 'Next change', 'Start new diff']}
    assert review_toolbar.find_element(By.CSS_SELECTOR, '.file-editor-review-reset').text == 'New diff'
    status = browser.find_element(By.CSS_SELECTOR, '.file-editor-review-status')
    assert status.text == 'Watching saves'
    assert browser.execute_script('return getComputedStyle(arguments[0]).whiteSpace', status) == 'nowrap'
    browser.execute_script('window.__reviewView = panelNodes.get(arguments[0])._cmView; window.__reviewView.focus()', item)
    subprocess.run(['python3', '-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2], encoding="utf-8")', str(path), updated], check=True)
    WebDriverWait(browser, 15).until(lambda d: d.execute_script(
        'return panelNodes.get(arguments[0])?._cmView?.state.doc.toString() === arguments[1]', item, updated))
    metrics = browser.execute_script('''
      const panel=panelNodes.get(arguments[0]), view=panel._cmView;
      return {same:view===window.__reviewView, focused:view.hasFocus,
        green:panel.querySelectorAll('.cm-insertedLine').length,
        red:panel.querySelectorAll('.cm-deletedChunk').length,dirty:fileState.get(arguments[1]).dirty};
    ''', item, str(path))
    assert metrics['same'] and metrics['focused'] and not metrics['dirty'], metrics
    assert metrics['green'] >= 2 and metrics['red'] >= 2, metrics
    if extension == 'md':
        clipboard_permissions = {
            'origin': server.base_url, 'permissions': ['clipboardReadWrite', 'clipboardSanitizedWrite'],
        }
        browser.execute_cdp_cmd('Browser.grantPermissions', clipboard_permissions)
        request.addfinalizer(lambda: browser.execute_cdp_cmd('Browser.resetPermissions', {}))
        browser.execute_script("""
          const clipboard = navigator.clipboard, writeText = clipboard.writeText;
          window.__reviewCopyWrites = [];
          window.__deferReviewCopy = false;
          clipboard.writeText = function(text) {
            window.__reviewCopyWrites.push(text);
            if (window.__deferReviewCopy) return new Promise(() => {});
            return writeText.call(this, text);
          };
        """)
        paint = {}
        word = 'reviewed and approved'
        for side, selector in (('red', '.cm-deletedChunk .cm-deletedLine'), ('green', '.cm-insertedLine')):
            target = browser.execute_script("""
              const [selector, word] = arguments;
              const line = [...window.__reviewView.dom.querySelectorAll(selector)].find(candidate => candidate.textContent.includes(word));
              const walker = document.createTreeWalker(line, NodeFilter.SHOW_TEXT);
              for (let node; (node = walker.nextNode());) {
                const index = node.textContent.indexOf(word);
                if (index < 0) continue;
                const range = document.createRange();
                range.setStart(node, index);
                range.setEnd(node, index + word.length);
                const rect = range.getBoundingClientRect();
                const y = Math.round(rect.top + rect.height / 2);
                return {start:{x:Math.ceil(rect.left),y}, end:{x:Math.floor(rect.right),y},
                  rect:rect.toJSON(), dpr:devicePixelRatio};
              }
              throw new Error('Missing diff text for selection');
            """, selector, word)
            unselected = browser_screenshot_rgb(browser)
            cdp_drag_hold(browser, target['start'], target['end'], steps=8)
            selected = browser_screenshot_rgb(browser)
            cdp_release(browser, target['end'])
            rect, scale = target['rect'], target['dpr']
            pixels = [
                (x, y)
                for x in range(round((rect['left'] + 2) * scale), round((rect['right'] - 2) * scale))
                for y in range(round((rect['top'] + 2) * scale), round((rect['bottom'] - 2) * scale))
            ]
            changed = sum(
                sum(abs(a - b) for a, b in zip(unselected.getpixel(point), selected.getpixel(point))) > 45
                for point in pixels
            )
            if side == 'red':
                browser.execute_script('window.__reviewView.contentDOM.blur()')
                assert browser.execute_script('return getSelection().toString()') == word
            browser.execute_async_script("navigator.clipboard.writeText('before copy').then(arguments[0])")
            browser.execute_script('window.__reviewCopyWrites.length = 0; window.__deferReviewCopy = arguments[0]', side == 'red')
            browser.execute_cdp_cmd('Browser.resetPermissions', {})
            native_logical_key(browser, 'c', [native_editor_modifier(browser)], commands=('copy',))
            browser.execute_cdp_cmd('Browser.grantPermissions', clipboard_permissions)
            copied = browser.execute_async_script('navigator.clipboard.readText().then(arguments[0])')
            browser.execute_script('window.__deferReviewCopy = false')
            paint[side] = {
                'changed': changed, 'samples': len(pixels), 'copied': copied,
                'writes': browser.execute_script('return window.__reviewCopyWrites'),
            }
        for side in ('red', 'green'):
            assert paint[side]['copied'] == word, paint
            assert paint[side]['changed'] > paint[side]['samples'] * 0.55, paint
        assert paint['red']['writes'] == [], paint
        page_copy = browser.execute_script("""
          const line = [...window.__reviewView.dom.querySelectorAll('.cm-deletedChunk del')]
            .find(candidate => candidate.textContent.includes('Entry 04'));
          const selection = document.getSelection();
          selection.selectAllChildren(line);
          const clipboardData = new DataTransfer();
          const event = new ClipboardEvent('copy', {clipboardData, bubbles: true, cancelable: true});
          document.body.dispatchEvent(event);
          const result = {expected: selection.toString(), copied: clipboardData.getData('text/plain'), handled: event.defaultPrevented};
          selection.removeAllRanges();
          return result;
        """)
        assert page_copy['handled'] and page_copy['copied'] == page_copy['expected'], page_copy
        assert 'Entry 04' in page_copy['copied'] and word in page_copy['copied'], page_copy
        assert browser.execute_script('return window.__reviewView.state.doc.toString()') == updated
    browser.execute_script('window.__reviewView.focus(); window.__reviewView.dispatch({selection:{anchor:5,head:8}})')
    WebDriverWait(browser, 5).until(lambda d: d.execute_script("""
      const view = window.__reviewView;
      const layer = view.dom.querySelector('.cm-selectionLayer');
      const highlight = layer?.querySelector('.cm-selectionBackground');
      const line = view.dom.querySelector('.cm-insertedLine').getBoundingClientRect();
      const box = highlight?.getBoundingClientRect();
      return getComputedStyle(layer).zIndex === '1' && box?.width > 0 && box.top < line.bottom && box.bottom > line.top;
    """))
    selection = browser.execute_script('return window.__reviewView.state.selection.toJSON()')
    range_select = browser.find_element(By.CSS_SELECTOR, '.file-editor-review-range')
    assert [option.get_attribute('value') for option in range_select.find_elements(By.TAG_NAME, 'option')] == ['0', '1', '5', '15', '30']
    select_current_value(browser, '.file-editor-review-range', '1')
    WebDriverWait(browser, 10).until(lambda d: d.execute_script(
        'return fileState.get(arguments[0]).recentReview.minutes === 1', str(path)))
    assert browser.execute_script('return window.__reviewView.state.selection.toJSON()') == selection
    assert path.read_text(encoding='utf-8') == updated
    select_current_value(browser, '.file-editor-review-range', '0')
    browser.find_element(By.CSS_SELECTOR, '.file-editor-review-next:not([hidden])').click()
    assert browser.execute_script('return panelNodes.get(arguments[0])._cmView === window.__reviewView', item)
    if extension == 'md':
        browser.execute_script('setFileEditorViewMode(arguments[1],"split",arguments[0]);renderFileEditorPanel(panelNodes.get(arguments[0]),arguments[0])', item, str(path))
        WebDriverWait(browser, 10).until(lambda d: d.execute_script(
            'return Boolean(panelNodes.get(arguments[0])?.querySelector(".cm-deletedChunk"))', item))
        preview = browser.execute_script('''const panel=panelNodes.get(arguments[0]), preview=panel.querySelector('.file-editor-preview-pane-panel');
          return {text:preview.textContent, deletions:preview.querySelectorAll('.cm-deletedChunk').length}''', item)
        assert 'Updated' in preview['text'] and 'Original' not in preview['text'] and preview['deletions'] == 0, preview
    if extension == 'md':
        review_id = browser.execute_script('return fileState.get(arguments[0]).recentReview.id', str(path))
        browser.refresh()
        WebDriverWait(browser, 15).until(lambda d: d.execute_script('return typeof openFileInEditor === "function" && typeof openFileEditorPane === "function"'))
        reopened = browser.execute_async_script("""
          const path=arguments[0], done=arguments[arguments.length-1];
          (async()=>{const item=await openFileInEditor(path,basenameOf(path),{viewMode:'edit',userInitiated:true});
            await openFileEditorPane(path,{item});return item})().then(done,error=>done({error:String(error)}));
        """, str(path))
        assert isinstance(reopened, str), reopened
        item = reopened
        WebDriverWait(browser, 15).until(lambda d: d.execute_script(
            'return Boolean(fileState.get(arguments[0])?.recentReview?.original || fileState.get(arguments[0])?.recentReview?.error)', str(path)))
        restored = browser.execute_script('const r=fileState.get(arguments[0]).recentReview;return {id:r.id,original:r.original,error:r.error}', str(path))
        assert restored['id'] == review_id and restored['original'] == original, restored
    browser.execute_script('setFileEditorViewMode(arguments[1],"edit",arguments[0]);renderFileEditorPanel(panelNodes.get(arguments[0]),arguments[0])', item, str(path))
    _type_relocation_text(browser, item, 'unsaved draft')
    WebDriverWait(browser, 10).until(lambda d: d.execute_script(
        'return fileState.get(arguments[0]).content === "unsaved draft" && fileState.get(arguments[0]).dirty && !panelNodes.get(arguments[1])._cmSerializeTimer', str(path), item))
    browser.execute_script('document.activeElement.blur()')
    subprocess.run(['python3', '-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("external conflict\\n", encoding="utf-8")', str(path)], check=True)
    WebDriverWait(browser, 15).until(lambda d: d.execute_script(
        'return Boolean(fileState.get(arguments[0]).externalChanged)', str(path)))
    assert browser.execute_script('return panelNodes.get(arguments[0])._cmView.state.doc.toString()', item) == 'unsaved draft'
    assert path.read_text(encoding='utf-8') == 'external conflict\n'
    browser.find_element(By.CSS_SELECTOR, '.file-editor-review-reset:not([hidden])').click()
    WebDriverWait(browser, 10).until(lambda d: d.execute_script(
        'return fileState.get(arguments[0]).recentReview.original === "external conflict\\n"', str(path)))
    assert browser.execute_script('return panelNodes.get(arguments[0])._cmView.state.doc.toString()', item) == 'unsaved draft'
    assert path.read_text(encoding='utf-8') == 'external conflict\n'
