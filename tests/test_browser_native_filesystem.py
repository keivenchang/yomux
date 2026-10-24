# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Native filesystem event delivery through the rendered Finder."""

import json
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tools.product_environment import product_env


def test_native_mixed_notices_scroll_below_editor_controls_on_short_viewports(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('short-notice-stack')
    request.addfinalizer(lambda: browser.get('about:blank'))
    # The test owns explicit tips; automatic startup delivery must not replace its native wheel target.
    (server.paths.config_dir / 'settings.yaml').write_text('general:\n  startup_tips: false\n', encoding='utf-8')
    path = server.paths.home_dir / 'notices.json'
    path.write_text('{"notice":true}\n', encoding='utf-8')
    session = server.tmux.sessions[0]
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    setup = browser.execute_async_script("""
      const path=arguments[0],done=arguments[arguments.length-1];
      (async()=>{
        await window.__yomuxTestWaitFor(()=>initialAppShellPainted && clientEventTransportState.connected,{timeoutMs:10000});
        const item=await openFileInEditor(path,'notices.json',{viewMode:'edit',userInitiated:true});
        await window.__yomuxTestWaitFor(()=>panelNodes.get(item)?._cmView,{timeoutMs:10000});
        return {item,automaticTips:startupHelpersEnabled};
      })().then(done,error=>done({error:String(error.stack||error)}));
    """, str(path))
    assert 'error' not in setup, setup
    assert setup['automaticTips'] is False, setup
    original_compact = browser.execute_script("return document.body.classList.contains('app-topbar-touch-compact')")
    try:
        for width, height, usable_height in ((1000, 235, None), (400, 235, None), (1000, 480, 235)):
            browser.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {'width':width,'height':height,'deviceScaleFactor':1,'mobile':False})
            browser.execute_script('setAppViewportOverride(arguments[0])', {'width':width,'height':usable_height} if usable_height else None)
            for compact in (False, True):
                geometry = browser.execute_async_script("""
                  const item=arguments[0],compact=arguments[1],done=arguments[arguments.length-1];
                  (async()=>{
                    document.body.classList.toggle('app-topbar-touch-compact',compact);
                    await new Promise(requestAnimationFrame);
                    await new Promise(requestAnimationFrame);
                    const enabled=startupHelpersEnabled;startupHelpersEnabled=true;
                    const tip=showStartupHelperTip();startupHelpersEnabled=enabled;
                    const command=showTimedStatusNotice('The command could not complete. Please review the request and try again.');
                    window.__shortNoticeIds=[tip,command].map(node=>Number(node.dataset.alertId));
                    const stack=tip.parentElement,bounds=stack.getBoundingClientRect();
                    const reload=panelNodes.get(item).querySelector('.file-editor-reload-panel'),button=reload.getBoundingClientRect();
                    const body=tip.querySelector('.toast-body').getBoundingClientRect();
                    const bodyHit=document.elementFromPoint(body.left+4,Math.max(body.top,bounds.top)+2);
                    return {top:bounds.top,bottom:bounds.bottom,height:Math.min(innerHeight,appViewport().height),reloadBottom:button.bottom,
                      hit:document.elementFromPoint(button.x+button.width/2,button.y+button.height/2)===reload,
                      passthrough:!bodyHit?.closest('.attention-alerts'),
                      scrollHeight:stack.scrollHeight,clientHeight:stack.clientHeight,
                      horizontalOverflow:stack.scrollWidth > stack.clientWidth+1,
                      clipped:[tip,command].some(node=>node.scrollHeight > node.clientHeight+1),
                      remaining:Number(command.dataset.toastRemainingMs),
                      tipId:tip.dataset.alertId,commandId:command.dataset.alertId};
                  })().then(done,error=>done({error:String(error.stack||error)}));
                """, setup['item'], compact)
                assert 'error' not in geometry, geometry
                assert geometry['hit'] and geometry['top'] >= geometry['reloadBottom'], geometry
                assert abs(geometry['bottom'] - (geometry['height'] - 12)) <= 1, geometry
                assert geometry['scrollHeight'] > geometry['clientHeight'] and geometry['remaining'] > 0 and not geometry['clipped'] and not geometry['horizontalOverflow'], geometry
                if compact:
                    assert geometry['passthrough'], geometry
                command_keep = browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["commandId"]}"] [data-toast-keep]')
                tip_keep = browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["tipId"]}"] [data-toast-keep]')
                ActionChains(browser).scroll_from_origin(ScrollOrigin.from_element(tip_keep), 0, geometry['scrollHeight']).perform()
                WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return document.querySelector('.attention-alerts').scrollTop > 0"))
                command_keep.click()
                assert 'kept' in browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["commandId"]}"]').get_attribute('class')
                command_close = browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["commandId"]}"] [data-toast-close]')
                command_close.click()
                WebDriverWait(browser, 5).until(lambda driver: not driver.find_elements(By.CSS_SELECTOR, f'[data-alert-id="{geometry["commandId"]}"]'))
                browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["tipId"]}"] [data-toast-keep]').click()
                assert 'kept' in browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["tipId"]}"]').get_attribute('class')
                browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["tipId"]}"] [data-toast-close]').click()
    finally:
        browser.execute_script("""
          for(const id of window.__shortNoticeIds||[]) removeAttentionAlert(id);
          document.body.classList.toggle('app-topbar-touch-compact',arguments[0]);
          setAppViewportOverride(null);
        """, original_compact)
        browser.execute_cdp_cmd('Emulation.clearDeviceMetricsOverride', {})


def test_native_filesystem_root_lists_through_http_and_rendered_finder(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory("native-filesystem-root")
    request.addfinalizer(lambda: browser.get("about:blank"))
    alias = server.paths.workspace_dir / "filesystem-root"
    alias.symlink_to("/", target_is_directory=True)

    def get_terminal(path):
        response = server.request(path)
        if response[0] != 202:
            return response
        receipt = json.loads(response[2])

        def terminal(_server):
            result = _server.request(receipt["operation"]["status_url"])
            return result if result[0] != 202 else False

        return WebDriverWait(server, 5, poll_frequency=0.05).until(terminal)

    for root in ("/", "/./", "/tmp/..", str(alias)):
        for route in ("/api/fs/fast/list", "/api/fs/list", "/api/fs/info"):
            status, _, body = get_terminal(route + "?" + urlencode({"path": root}))
            payload = json.loads(body)
            assert status == 200, (root, route, payload)
            if route.endswith("/info"):
                assert payload["kind"] == "dir" and payload["realpath"] == "/"
            else:
                assert payload["entries"], (root, route, payload)
        status, _, body = get_terminal("/api/fs/read?" + urlencode({"path": root}))
        assert status == 400 and json.loads(body)["error_code"] == "invalid_request", body

    connection = HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        connection.request("POST", "/api/fs/batch", headers={"Content-Type": "application/json"}, body=json.dumps({
            "requests": [{"type": kind, "path": "/", "include_repo_info": False} for kind in ("list", "info")],
        }))
        response = connection.getresponse()
        status, body = response.status, response.read()
    finally:
        connection.close()
    if status == 202:
        receipt = json.loads(body)
        status, _, body = get_terminal(receipt["operation"]["status_url"])
    assert status == 200, body
    assert all(item["ok"] and item["status"] == 200 for item in json.loads(body)["responses"]), body

    browser.get(server.base_url + "/?sessions=__finder__&layout=left&tabs=left:__finder__")
    try:
        result = browser.execute_async_script("""
          const done = arguments[arguments.length - 1];
          (async () => {
            await window.__yomuxTestWaitFor(() => typeof openFileExplorerManualRoot === 'function'
              && typeof openFileExplorerPane === 'function', {timeoutMs:10000});
            await openFileExplorerPane();
            await openFileExplorerManualRoot('/');
            await window.__yomuxTestWaitFor(() => currentFileExplorerRoot() === '/'
              && [...document.querySelectorAll('.file-tree-row')].some(row => row.dataset.path === '/usr'),
              {timeoutMs:5000});
            return {root:currentFileExplorerRoot(), visible:[...document.querySelectorAll('.file-tree-row')]
              .some(row => row.dataset.path === '/usr')};
          })().then(done, error => done({error:String(error.stack || error)}));
        """)
        assert result == {"root": "/", "visible": True}, result
    finally:
        browser.get("about:blank")


def test_native_filesystem_events_refresh_visible_finder_and_hidden_finder_editor(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory("native-finder")
    request.addfinalizer(lambda: browser.get("about:blank"))
    directory = server.paths.home_dir / "finder-watch"
    directory.mkdir()
    existing = directory / "existing.json"
    existing.write_text('{"baseline":true}\n', encoding="utf-8")
    created = directory / "created.json"
    browser.get(server.base_url + "/?sessions=__finder__&layout=left&tabs=left:__finder__")
    setup = browser.execute_async_script("""
      const root = arguments[0], existing = arguments[1], done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof openFileExplorerAt === 'function'
          && typeof clientEventTransportState !== 'undefined', {timeoutMs:10000});
        await window.__yomuxTestWaitFor(() => clientEventTransportState.connected, {timeoutMs:10000});
        await openFileExplorerPane();
        await window.__yomuxTestWaitFor(() => fileExplorerTreePaneIsVisible(), {timeoutMs:5000});
        await openFileExplorerManualRoot(root);
        await syncServerWatchRoots({immediate:true});
        await window.__yomuxTestWaitFor(() => serverWatchRootsState.registered
          && serverWatchRootsState.signature.includes(root) && fileExplorerFilesystemWatchToken
          && clientEventTransportState.connected && clientEventTransportState.source
          && [...document.querySelectorAll('.file-tree-row')].some(row => row.dataset.path === existing), {timeoutMs:10000});
        window.__nativeFinderEvents = [];
        window.__nativeFinderObservedSources = new WeakSet();
        window.__observeNativeFinderSource = source => {
          if (window.__nativeFinderObservedSources.has(source)) return;
          window.__nativeFinderObservedSources.add(source);
          source.addEventListener('fs_changed', event => window.__nativeFinderEvents.push(JSON.parse(event.data)));
        };
        window.__observeNativeFinderSource(clientEventTransportState.source);
        return {root:currentFileExplorerRoot(), token:fileExplorerFilesystemWatchToken,
          registered:serverWatchRootsState.registered, connected:clientEventTransportState.connected};
      })().then(done, error => done({error:String(error.stack || error),
        root:typeof currentFileExplorerRoot === 'function' ? currentFileExplorerRoot() : '',
        token:typeof fileExplorerFilesystemWatchToken === 'string' ? fileExplorerFilesystemWatchToken : '',
        registered:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.registered : null,
        rootMode:typeof fileExplorerRootMode === 'string' ? fileExplorerRootMode : '',
        finderVisible:typeof fileExplorerTreePaneIsVisible === 'function' ? fileExplorerTreePaneIsVisible() : null,
        registrationPending:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.registrationPending : null,
        inFlight:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.inFlight : null,
        baselinePromise:typeof serverWatchRootsState === 'object' ? Boolean(serverWatchRootsState.watchBaselinePromise) : null,
        rejectedSignature:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.rejectedSignature : '',
        signature:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.signature : '',
        connected:typeof clientEventTransportState === 'object' ? clientEventTransportState.connected : null,
        apiFailures:typeof jsDebugEvents === 'object' ? jsDebugEvents.filter(event => event.type === 'api'
          && (event.error || event.ok === false)).slice(-5).map(event => ({path:event.endpoint,status:event.status,
            error:event.error,code:event.code})) : [],
        visible:[...document.querySelectorAll('.file-tree-row')].map(row => row.dataset.path)}));
    """, str(directory), str(existing))
    status, _, watch_body = server.request("/api/fs/watch-diff?full=1")
    setup["watchHttpStatus"] = status
    setup["watchResponse"] = json.loads(watch_body)
    evidence = Path(product_env("E2E_EVIDENCE_DIR", "") or server.paths.log_dir) / "native-finder-setup.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(setup, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(setup, sort_keys=True))
    assert "error" not in setup, setup
    observations = []
    try:
        for path, present in ((created, True), (existing, False)):
            before = browser.execute_script("return window.__nativeFinderEvents.length;")
            if present:
                path.write_text('{"created":true}\n', encoding="utf-8")
            else:
                path.unlink()
            result = browser.execute_async_script("""
              const root = arguments[0], path = arguments[1], present = arguments[2], before = arguments[3];
              const done = arguments[arguments.length - 1];
              (async () => {
                await window.__yomuxTestWaitFor(() => window.__nativeFinderEvents.slice(before).some(event =>
                  (event.payload?.paths || []).some(changed => changed === root || changed === path)), {timeoutMs:10000});
                let converged = true;
                try {
                  await window.__yomuxTestWaitFor(() => [...document.querySelectorAll('.file-tree-row')]
                    .some(row => row.dataset.path === path) === present, {timeoutMs:5000});
                } catch (_) { converged = false; }
                return {path, present, converged, token:fileExplorerFilesystemWatchToken,
                  events:window.__nativeFinderEvents.slice(before),
                  visible:[...document.querySelectorAll('.file-tree-row')].map(row => row.dataset.path)};
              })().then(done, error => done({error:String(error.stack || error)}));
            """, str(directory), str(path), present, before)
            assert "error" not in result, result
            observations.append(result)
            evidence.with_name("native-finder-transitions.json").write_text(
                json.dumps(observations, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        editor_setup = browser.execute_async_script("""
          const path = arguments[0], done = arguments[arguments.length - 1];
          (async () => {
            const item = await openFileInEditor(path, {name:'created.json'}, {userInitiated:true});
            removeSessionFromLayout(fileExplorerItemId);
            syncClientEventDemand({immediate:true});
            await window.__yomuxTestWaitFor(() => clientEventTransportState.connected
              && !clientEventTransportState.replacementSource, {timeoutMs:10000});
            await syncServerWatchRoots({immediate:true});
            await window.__yomuxTestWaitFor(() => !fileExplorerTreePaneIsVisible()
              && serverWatchRootsState.registered && JSON.parse(serverWatchRootsState.signature).files.includes(path)
              && fileState.get(path)?.content.includes('"created":true')
              && panelNodes.get(item)?.querySelector('.cm-content'), {timeoutMs:10000});
            window.__nativeFinderEvents = [];
            window.__observeNativeFinderSource(clientEventTransportState.source);
            return {item, content:fileState.get(path)?.content, finderVisible:fileExplorerTreePaneIsVisible(),
              signature:serverWatchRootsState.signature, token:fileExplorerFilesystemWatchToken};
          })().then(done, error => done({error:String(error.stack || error)}));
        """, str(created))
        evidence.with_name("native-editor-setup.json").write_text(
            json.dumps(editor_setup, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert "error" not in editor_setup, editor_setup
        browser.execute_script(
            "return panelNodes.get(arguments[0]).querySelector('.cm-content');", editor_setup["item"]
        ).click()
        assert browser.execute_script("return fileEditorPathHasFocus(arguments[0]);", str(created)) is True
        watch_read_start = browser.execute_script("return jsDebugEvents.length;")
        created.write_text('{"external":true,"revision":2}\n', encoding="utf-8")
        editor = browser.execute_async_script("""
          const path = arguments[0], item = arguments[1], before = arguments[2], done = arguments[arguments.length - 1];
          (async () => {
            await window.__yomuxTestWaitFor(() => window.__nativeFinderEvents.some(event =>
              (event.payload?.paths || []).includes(path)), {timeoutMs:10000});
            let converged = true;
            try {
              await window.__yomuxTestWaitFor(() => {
                const state = fileState.get(path), panel = panelNodes.get(item);
                const status = panel?.querySelector('.file-editor-status-message');
                return state?.externalChanged && state?.externalReloadDeferred && fileEditorPathHasFocus(path)
                  && state.content === '{"created":true}\\n'
                  && panel?.querySelector('.cm-content')?.textContent.includes('"created":true')
                  && status?.textContent === t('dialog.externalTitle') && status.getClientRects().length > 0;
              }, {timeoutMs:5000});
            } catch (_) { converged = false; }
            return {converged, finderVisible:fileExplorerTreePaneIsVisible(), content:fileState.get(path)?.content,
              externalChanged:fileState.get(path)?.externalChanged,
              deferred:Boolean(fileState.get(path)?.externalReloadDeferred), focused:fileEditorPathHasFocus(path),
              status:panelNodes.get(item)?.querySelector('.file-editor-status-message')?.textContent,
              watchReadbacks:jsDebugEvents.slice(before).filter(event => event.type === 'api'
                && event.endpoint === '/api/fs/watch-diff').map(event => ({url:event.url,status:event.status,requestId:event.requestId})),
              events:window.__nativeFinderEvents, token:fileExplorerFilesystemWatchToken};
          })().then(done, error => done({error:String(error.stack || error)}));
        """, str(created), editor_setup["item"], watch_read_start)
        evidence.with_name("native-editor-transition.json").write_text(
            json.dumps(editor, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert "error" not in editor, editor
        assert editor["watchReadbacks"] and all(row["status"] == 200 and row["requestId"] for row in editor["watchReadbacks"]), editor
        observations.append(editor)
        read_start = browser.execute_script("return jsDebugEvents.length;")
        toast_geometry = browser.execute_script("""
          const item=arguments[0], reload=panelNodes.get(item).querySelector('.file-editor-reload-panel');
          const enabled=startupHelpersEnabled, compact=document.body.classList.contains('app-topbar-touch-compact');
          startupHelpersEnabled=true;
          const tip=showStartupHelperTip();
          startupHelpersEnabled=enabled;
          const stack=tip.parentElement, samples=[];
          const sample=label => {
            const button=reload.getBoundingClientRect(), bounds=stack.getBoundingClientRect();
            samples.push({label,bottom:bounds.bottom,viewport:innerHeight,top:bounds.top,
              reloadBottom:button.bottom,
              hit:document.elementFromPoint(button.x+button.width/2,button.y+button.height/2) === reload});
          };
          try {
            sample('startup');
            const command=showTimedStatusNotice('A command could not complete.');
            sample('command-present');
            document.body.classList.add('app-topbar-touch-compact');
            sample('compact');
            removeAttentionAlert(Number(command.dataset.alertId));
            sample('command-removed');
          } finally {
            document.body.classList.toggle('app-topbar-touch-compact',compact);
          }
          sample('restored');
          return {kind:tip.dataset.toastKind,samples};
        """, editor_setup["item"])
        evidence.with_name("native-editor-toast-geometry.json").write_text(
            json.dumps(toast_geometry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert toast_geometry["kind"] == "startup-helper", toast_geometry
        assert all(sample["hit"] and sample["top"] >= sample["reloadBottom"]
                   and abs(sample["bottom"] - (sample["viewport"] - 12)) <= 1
                   for sample in toast_geometry["samples"]), toast_geometry
        browser.execute_script(
            "return panelNodes.get(arguments[0]).querySelector('.file-editor-reload-panel');", editor_setup["item"]
        ).click()
        reloaded = browser.execute_async_script("""
          const path = arguments[0], item = arguments[1], before = arguments[2], done = arguments[arguments.length - 1];
          (async () => {
            await window.__yomuxTestWaitFor(() => fileState.get(path)?.content === '{"external":true,"revision":2}\\n'
              && panelNodes.get(item)?.querySelector('.cm-content')?.textContent.includes('"external":true')
              && !fileState.get(path)?.externalChanged, {timeoutMs:5000});
            return {content:fileState.get(path)?.content, finderVisible:fileExplorerTreePaneIsVisible(),
              readbacks:jsDebugEvents.slice(before).filter(event => event.type === 'api' && event.endpoint === '/api/fs/read'
                && new URL(event.url, location.href).searchParams.get('path') === path)
                .map(event => ({url:event.url,status:event.status,requestId:event.requestId}))};
          })().then(done, error => done({error:String(error.stack || error)}));
        """, str(created), editor_setup["item"], read_start)
        evidence.with_name("native-editor-reload.json").write_text(
            json.dumps(reloaded, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert "error" not in reloaded, reloaded
        assert reloaded["finderVisible"] is False and reloaded["readbacks"], reloaded
        assert created.read_text(encoding="utf-8") == reloaded["content"]
    finally:
        browser.get("about:blank")
        server.stop()
    assert all(item["converged"] for item in observations), observations
