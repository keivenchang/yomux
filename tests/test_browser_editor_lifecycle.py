# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Late save and watcher callbacks must respect reopened editor ownership."""

from urllib.parse import quote

import pytest
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import BROWSER_WAIT_HELPER_SOURCE
from tests.browser_helpers.browser_console import assert_only_expected_browser_network_error
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401


def _open_relocation_editor(browser, server, path):
    session = server.tmux.sessions[0]
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    browser.execute_script(BROWSER_WAIT_HELPER_SOURCE)
    browser.execute_script('window.__relocationServerBinary=arguments[0]', server.process.args[0])
    result = browser.execute_async_script("""
      const path = arguments[0], done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof openFileInEditor === 'function', {timeoutMs:10000});
        setTabberMode('advanced');
        await saveSettingsPatch(settingPatch('editor.autosave',false));
        await window.__yomuxTestWaitFor(() => boolSetting('editor.autosave',true) === false
          && fileEditorAutosaveEnabled === false,{timeoutMs:5000});
        window.__relocationActivityTouches=[];
        window.__relocationRegistrations=[];
        window.__relocationEvents=[];
        const originalFetch=window.fetch;
        window.fetch=(url,options) => {
          if (String(url) === '/api/activity/touch') window.__relocationActivityTouches.push(JSON.parse(options.body));
          const response=originalFetch(url,options);
          if (String(url) !== '/api/watch/roots') return response;
          return response.then(async result => {
            const sent=JSON.parse(options.body), body=await result.clone().json();
            window.__relocationRegistrations.push({request:{client_id:sent.client_id,roots:sent.roots,
              search_roots:sent.search_roots,root_surfaces:sent.root_surfaces,background_files:sent.background_files,
              open_files:sent.open_files,files:sent.files},status:result.status,
              response:{request_id:body.request?.id,roots:body.roots,open_files:body.open_files,
                revision:body.revision,error_path:body.error?.details?.path}});
            return result;
          });
        };
        const originalHandle=handleClientPushEvent;
        handleClientPushEvent=(type,payload,envelope) => {
          if (['file_relocated','fs_changed','files_changed'].includes(type)) window.__relocationEvents.push({type,payload});
          return originalHandle(type,payload,envelope);
        };
        await openFileExplorerPane();
        await openFileExplorerManualRoot(dirnameOf(path));
        const item = await openFileInEditor(path, basenameOf(path), {viewMode:'edit',userInitiated:true});
        await openFileEditorPane(path, {item});
        await window.__yomuxTestWaitFor(() => panelNodes.get(item)?._cmView?.contentDOM.getBoundingClientRect().height > 0,
          {timeoutMs:10000});
        await syncServerWatchRoots({immediate:true});
        await window.__yomuxTestWaitFor(() => clientEventTransportState.connected && serverWatchRootsState.registered,
          {timeoutMs:10000});
        return {item};
      })().then(done, error => done({error:String(error.stack || error),
        registrations:window.__relocationRegistrations,events:window.__relocationEvents}));
    """, str(path))
    assert 'error' not in result, result
    return result['item']


def _type_relocation_text(browser, item, text, surface='text-editor'):
    editor = browser.execute_script("""
      const panel=panelNodes.get(arguments[0]);
      return arguments[1] === 'text-editor' ? panel._cmView.contentDOM : panel._pmView.dom;
    """, item, surface)
    editor.click()
    modifier = Keys.COMMAND if browser.capabilities['platformName'].lower() in {'mac', 'macos', 'darwin'} else Keys.CONTROL
    editor.send_keys(modifier, 'a')
    for index, character in enumerate(text, 1):
        editor.send_keys(character)
        actual = browser.execute_script("""
          const panel=panelNodes.get(arguments[0]);
          return arguments[1] === 'text-editor' ? panel._cmView.state.doc.toString() : panel._pmView.state.doc.textContent;
        """, item, surface)
        assert actual == text[:index]


def _assert_expected_relocation_absence(browser, server, request, old_path):
    assert not old_path.exists()
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
        windows.append((browser.current_window_handle, text, before))
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
    for window, text, before in windows:
        browser.switch_to.window(window)
        try:
            result = _wait_relocation_state(browser, path, destination)
        except AssertionError as error:
            peers = []
            for peer_window, _peer_text, _peer_before in windows:
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
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script('return Boolean(panelNodes.get(arguments[0])?._cmView)', item))
    before = browser.execute_script("""
      const item=arguments[0], panel=panelNodes.get(item), rect=panel.getBoundingClientRect();
      return {slot:slotForItem(item),width:rect.width,height:rect.height,selected:activeItemForSide(slotForItem(item))};
    """, item)
    assert before['slot'] and before['width'] > 0 and before['height'] > 0 and before['selected'] == item, before
    browser.execute_script('window.__settingsNavigationRelease()')
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script('return window.__settingsNavigationDone === true'))
    after = browser.execute_script("""
      const item=arguments[0], panel=panelNodes.get(item), rect=panel.getBoundingClientRect();
      return {slot:slotForItem(item),width:rect.width,height:rect.height,selected:activeItemForSide(slotForItem(item))};
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


@pytest.mark.parametrize("action", ["reload", "compare"])
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
    WebDriverWait(browser, 5).until(lambda driver: driver.find_element(By.CSS_SELECTOR, '.file-editor-conflict-dialog').is_displayed())
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
    browser.find_element(By.CSS_SELECTOR, f'.file-editor-conflict-dialog [data-dialog-action="{action}"]').click()
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
