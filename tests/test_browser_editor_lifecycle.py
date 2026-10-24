# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Late save and watcher callbacks must respect reopened editor ownership."""

import pytest
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401


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
    editor.click()
    editor.send_keys(Keys.CONTROL, 'a')
    for character in 'first edit':
        editor.send_keys(character)
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
