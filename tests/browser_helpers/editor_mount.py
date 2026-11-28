# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared ways to open a file editor in a browser test, on a fixture page or a live isolated server."""

import yaml
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import BROWSER_WAIT_HELPER_SOURCE, load_live_runtime_boot_fixture


def mount_html_split(browser, tmp_path, source, *, sandbox_events=True, before_mount=None, mode='split'):
    return mount_split_file(browser, tmp_path, source, '/home/test/split.html', 'xml',
                            sandbox_events=sandbox_events, before_mount=before_mount, mode=mode)


def mount_split_file(browser, tmp_path, source, path, language, *, sandbox_events=True, before_mount=None, mode='split'):
    load_live_runtime_boot_fixture(browser, tmp_path, '?sessions=1', sessions=['1'])
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script(
        "return typeof registerFileEditorLayoutItem === 'function' && !!document.querySelector('#grid')"))
    if before_mount:
        before_mount(browser)
    return browser.execute_script("""
      const [source, sandboxEvents, mode, path, language] = arguments;
      // Exercise the fallback with Chromium's native wheel input.
      if (!sandboxEvents) htmlPreviewReceivesEvents = () => false;
      const item = fileEditorItemFor(path);
      setFileState(path, {kind: 'text', content: source, original: source, dirty: false, language});
      setFileEditorViewMode(path, mode, item);
      addFileEditorTabItem(path, item);
      registerFileEditorLayoutItem(path, {item});
      const slots = emptyLayoutSlots();
      slots[layoutTreeKey] = leafNode('left');
      slots.left = paneStateWithTabs([item], item);
      applyLayoutSlots(slots, {focusSession: item, forceFull: true});
      return item;
    """, source, sandbox_events, mode, path, language)


def _open_relocation_editor(browser, server, path, *, view_mode='edit'):
    settings_path = server.paths.config_dir / 'settings.yaml'
    settings = yaml.safe_load(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else {}
    settings.setdefault('general', {})['startup_tips'] = False
    settings_path.write_text(yaml.safe_dump(settings), encoding='utf-8')
    session = server.tmux.sessions[0]
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    browser.execute_script(BROWSER_WAIT_HELPER_SOURCE)
    browser.execute_script('window.__relocationServerBinary=arguments[0]', server.process.args[0])
    result = browser.execute_async_script("""
      const [path, viewMode, done] = arguments;
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof openFileInEditor === 'function', {timeoutMs:10000});
        if (boolSetting('general.startup_tips') !== false) throw new Error('relocation fixture startup tips must be disabled');
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
        const item = await openFileInEditor(path, basenameOf(path), {viewMode,userInitiated:true});
        await openFileEditorPane(path, {item});
        await window.__yomuxTestWaitFor(() => {
          const panel = panelNodes.get(item), view = panel?._cmView;
          return view && fileEditorPanelMode(panel) === viewMode &&
            (viewMode === 'preview' || view.contentDOM.getBoundingClientRect().height > 0);
        }, {timeoutMs:10000});
        await syncServerWatchRoots({immediate:true});
        await window.__yomuxTestWaitFor(() => clientEventTransportState.connected && serverWatchRootsState.registered,
          {timeoutMs:10000});
        return {item};
      })().then(done, error => done({error:String(error.stack || error),
        registrations:window.__relocationRegistrations,events:window.__relocationEvents}));
    """, str(path), view_mode)
    assert 'error' not in result, result
    return result['item']
