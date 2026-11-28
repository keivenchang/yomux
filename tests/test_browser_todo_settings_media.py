# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from PIL import Image
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import BROWSER_WAIT_HELPER_SOURCE, load_dockview_runtime_boot_fixture, select_current_value
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401


def test_description_preferences_use_independent_model_and_supported_effort(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('description-preferences')
    request.addfinalizer(lambda: browser.get('about:blank'))
    browser.get(server.base_url + '/?layout=left&tabs=left:__prefs__')
    wait = WebDriverWait(browser, 15)
    wait.until(lambda driver: driver.execute_script("return !!document.querySelector('[data-setting-path=\"tabber.codex_model\"]') && !!settingChoiceMetadata('tabber.codex_model')['gpt-6-luna']"))
    initial = browser.execute_script("""
      window.__descriptionOtherModel=initialSetting('yoagent.codex_model');
      window.__descriptionOtherEffort=initialSetting('yoagent.codex_effort');
      return {enabled:document.querySelector('[data-setting-path="tabber.automatic_descriptions"]').checked,
        model:document.querySelector('[data-setting-path="tabber.codex_model"]').value,
        effort:document.querySelector('[data-setting-path="tabber.codex_effort"]:checked').value,
        interval:document.querySelector('[data-setting-path="tabber.description_interval_minutes"]').value};
    """)
    assert initial == {'enabled': True, 'model': 'gpt-6-luna', 'effort': 'low', 'interval': '10'}, initial
    models = browser.execute_script("""
      const select = document.querySelector('[data-setting-path="tabber.codex_model"]');
      const claude = select.querySelector('option[value="claude-sonnet-5-5"]');
      applyAgentAvailabilityPayload({availableAgents: ['codex', 'claude'],
        agentAuth: {codex: {available: true}, claude: {available: false}}});
      const unavailable = claude.disabled;
      applyAgentAvailabilityPayload({agentAuth: {codex: {available: true}, claude: {available: true}}});
      return {unavailable, enabled: !claude.disabled, retired: !!select.querySelector('option[value="gpt-5.6-sol"]')};
    """)
    assert models == {'unavailable': True, 'enabled': True, 'retired': False}, models
    for model in ['gpt-6-astra', 'claude-sonnet-5-5', 'gpt-6-luna']:
        select_current_value(browser, '[data-setting-path="tabber.codex_model"]', model)
        wait.until(lambda driver: driver.execute_script("return initialSetting('tabber.codex_model')===arguments[0]", model))
        state = browser.execute_script("""
          const model=initialSetting('tabber.codex_model'), claude=model.startsWith('claude-');
          return {options:[...document.querySelectorAll('[data-setting-path="tabber.codex_effort"]')].map(node=>node.value),
            expected:claude ? ['low', 'medium', 'high'] : settingChoiceMetadata('tabber.codex_model')[model].effort_options,
            selected:document.querySelector('[data-setting-path="tabber.codex_effort"]:checked')?.value,
            expectedSelected:claude ? 'low' : settingChoiceMetadata('tabber.codex_model')[model].default_effort,
            independent:initialSetting('yoagent.codex_model')===window.__descriptionOtherModel
              && initialSetting('yoagent.codex_effort')===window.__descriptionOtherEffort};
        """)
        assert state['options'] == state['expected'] and state['selected'] == state['expectedSelected'], state
        assert state['independent'], state


def test_unchanged_markdown_reload_refreshes_retained_images(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('reload-markdown-media')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'images.md'
    image = path.with_name('figure.png')
    Image.new('RGB', (48, 24), 'red').save(image)
    path.write_text('Before ![figure](figure.png "Authored image") after.\n', encoding='utf-8')
    browser.get(server.base_url + '/?layout=left&tabs=left:__finder__')
    opened = browser.execute_async_script("""
      const path=arguments[0], done=arguments[arguments.length-1];
      (async()=>{
        await window.__yomuxTestWaitFor(()=>initialAppShellPainted,{timeoutMs:10000});
        const item=await openFileInEditor(path);setFileEditorViewMode(path,'split',item);
        await window.__yomuxTestWaitFor(()=>panelNodes.get(item)?._pmView?.dom.querySelector('img')?.naturalWidth===48,{timeoutMs:10000});
        const panel=panelNodes.get(item);panel.id='reload-media-test';
        window.__retainedMediaView=panel._pmView;window.__oldMediaURL=panel._pmView.dom.querySelector('img').src;
        return item;
      })().then(done,error=>done({error:String(error.stack||error)}));
    """, str(path))
    assert isinstance(opened, str), opened
    Image.new('RGB', (64, 32), 'blue').save(image)
    browser.find_element(By.CSS_SELECTOR, '#reload-media-test .file-editor-reload-panel').click()
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("""
      const panel=document.getElementById('reload-media-test'), image=panel?._pmView?.dom.querySelector('img');
      return image?.naturalWidth===64 && image.src!==window.__oldMediaURL;
    """))
    assert browser.execute_script("return document.getElementById('reload-media-test')._pmView===window.__retainedMediaView")
    assert browser.execute_script("return window.__retainedMediaView.dom.querySelector('img').title") == 'Authored image'
    browser.execute_script("""
      const image=window.__retainedMediaView.dom.querySelector('img');
      window.__mediaScrollOwner=image.closest('.file-editor-preview-pane-panel') || image;
      window.__previousMediaScrollTrace=previewScrollTraceState.active;
      previewScrollTraceState.active={element:window.__mediaScrollOwner,traceElement:window.__mediaScrollOwner,
        claimed:true,startedAt:Date.now(),ownerDocument:document};
      window.__obsoleteImageFailure=false;
      scheduleMarkdownImageFallbackAfterUserScroll(window.__retainedMediaView.dom,image,()=>{
        window.__obsoleteImageFailure=true;return document.createElement('span');
      });
      const originalFetch=window.fetch;let held=false;
      window.__mediaAborted=false;
      window.fetch=(url,options)=>{
        if(!held && new URL(String(url),location.href).pathname==='/api/fs/raw'){
          held=true;
          options.signal.addEventListener('abort',()=>window.__mediaAborted=true);
          return new Promise(resolve=>window.__releaseOldMedia=()=>resolve(new Response(new Blob([]))));
        }
        return originalFetch(url,options);
      };
    """)
    browser.find_element(By.CSS_SELECTOR, '#reload-media-test .file-editor-reload-panel').click()
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("return !!window.__releaseOldMedia"))
    browser.find_element(By.CSS_SELECTOR, '#reload-media-test .file-editor-reload-panel').click()
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("return window.__mediaAborted && window.__retainedMediaView.dom.querySelector('img')?.naturalWidth===64"))
    browser.execute_script("window.__releaseOldMedia();previewScrollTraceState.active=window.__previousMediaScrollTrace")
    assert browser.execute_script("return !window.__obsoleteImageFailure && !window.__retainedMediaView.dom.querySelector('img')._previewDeferredWorkByKey?.has('markdown-image-failure')")


def test_failed_markdown_image_reload_preserves_authored_title_and_alt(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('reload-failed-markdown-media')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'failed-images.md'
    image = path.with_name('figure.png')
    image.write_bytes(b'not a decodable image')
    path.write_text('![Authored alternative](figure.png "Authored image")\n', encoding='utf-8')
    browser.get(server.base_url + '/?layout=left&tabs=left:__finder__')
    try:
        failed = browser.execute_async_script("""
          const path=arguments[0],done=arguments[arguments.length-1];
          (async()=>{
            await window.__yomuxTestWaitFor(()=>initialAppShellPainted,{timeoutMs:10000});
            const item=await openFileInEditor(path,null,{viewMode:'edit'}),panel=panelNodes.get(item);
            const preview=panel.querySelector('.file-editor-preview-pane-panel');
            renderMarkdownPreviewInto(preview,fileState.get(path).content,path,{readOnly:true});
            await preview._previewAsync;
            await window.__yomuxTestWaitFor(()=>preview.querySelector('.markdown-image-error'),{timeoutMs:10000});
            openFilePreviewPopout(path,panel);
            const record=filePreviewPopouts.get(path);
            if(record.previewAsync) await record.previewAsync.promise;
            await window.__yomuxTestWaitFor(()=>record.window.document.querySelector('[data-preview-root]'),{timeoutMs:10000});
            const popoutRoot=record.window.document.querySelector('[data-preview-root]');
            renderMarkdownPreviewInto(popoutRoot,fileState.get(path).content,path,{readOnly:true});
            await popoutRoot._previewAsync;
            await window.__yomuxTestWaitFor(()=>popoutRoot.querySelector('.markdown-image-error'),{timeoutMs:10000});
            window.__failedMediaPreview=preview;
            window.__failedMediaPopoutRoot=popoutRoot;
            return {preview:preview.querySelectorAll('.markdown-image-error').length,
              popout:window.__failedMediaPopoutRoot.querySelectorAll('.markdown-image-error').length};
          })().then(done,error=>done({error:String(error.stack||error)}));
        """, str(path))
        assert failed == {'preview': 1, 'popout': 1}, failed
        Image.new('RGB', (64, 32), 'blue').save(image)
        recovered = browser.execute_async_script("""
          const path=arguments[0],done=arguments[arguments.length-1];
          (async()=>{
            // Reload's retained-media owner must recover without reparsing the source snapshot.
            await refreshOpenMarkdownImages(path);
            return [window.__failedMediaPreview,window.__failedMediaPopoutRoot].map(root=>{
              const image=root.querySelector('img.markdown-preview-image');
              return {width:image?.naturalWidth,height:image?.naturalHeight,title:image?.getAttribute('title'),
                alt:image?.getAttribute('alt'),failures:root.querySelectorAll('.markdown-image-error').length};
            });
          })().then(done,error=>done({error:String(error.stack||error)}));
        """, str(path))
        assert recovered == [
            {'width': 64, 'height': 32, 'title': 'Authored image', 'alt': 'Authored alternative', 'failures': 0},
            {'width': 64, 'height': 32, 'title': 'Authored image', 'alt': 'Authored alternative', 'failures': 0},
        ], recovered
    finally:
        browser.execute_script('closeFilePreviewPopout(arguments[0])', str(path))


def test_image_reload_retires_requests_when_popout_closes(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('reload-popout-close')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'popout.md'
    Image.new('RGB', (48, 24), 'red').save(path.with_name('figure.png'))
    path.write_text('![figure](figure.png)\n', encoding='utf-8')
    browser.get(server.base_url + '/?layout=left&tabs=left:__finder__')
    result = browser.execute_async_script("""
      const path=arguments[0],done=arguments[arguments.length-1];
      (async()=>{
        await window.__yomuxTestWaitFor(()=>initialAppShellPainted,{timeoutMs:10000});
        const item=await openFileInEditor(path);setFileEditorViewMode(path,'split',item);
        await window.__yomuxTestWaitFor(()=>panelNodes.get(item)?._pmView?.dom.querySelector('img')?.naturalWidth===48,{timeoutMs:10000});
        openFilePreviewPopout(path,panelNodes.get(item));
        const popout=filePreviewPopouts.get(path).window;
        await window.__yomuxTestWaitFor(()=>popout.document.querySelector('[data-preview-root] img')?.naturalWidth===48,{timeoutMs:10000});
        const image=popout.document.querySelector('[data-preview-root] img');
        // Retained-editor refresh is covered separately; hold only the popout's local request.
        const retainedImages=[...panelNodes.get(item).querySelectorAll('img.markdown-preview-image')]
          .map(node=>({node,src:node.dataset.originalSrc}));
        for(const {node} of retainedImages) node.dataset.originalSrc='https://example.test/figure.png';
        const oldFetch=window.fetch,oldCreate=URL.createObjectURL;let created=0,aborted=false;
        let release,heldResolve;const held=new Promise(resolve=>heldResolve=resolve);
        const blob=await (await oldFetch('/api/fs/raw?path='+encodeURIComponent(image.dataset.resolvedPath || path.replace('popout.md','figure.png')))).blob();
        URL.createObjectURL=(...args)=>{created++;return oldCreate(...args)};
        window.fetch=(url,options)=>{
          if(new URL(String(url),location.href).pathname==='/api/fs/raw'){
            options.signal.addEventListener('abort',()=>aborted=true);
            heldResolve();return new Promise(resolve=>release=()=>resolve(new Response(blob)));
          }
          return oldFetch(url,options);
        };
        try {
          // The real Reload transaction includes the popout through the shared media owner.
          const refresh=refreshOpenMarkdownImages(path);await held;
          popout.close();
          await window.__yomuxTestWaitFor(()=>popout.closed,{timeoutMs:10000});
          release();
          await window.__yomuxTestWaitFor(()=>aborted || created>0,{timeoutMs:10000});
          return {aborted,created};
        } finally {
          window.fetch=oldFetch;URL.createObjectURL=oldCreate;
          for(const {node,src} of retainedImages) node.dataset.originalSrc=src;
        }
      })().then(done,error=>done({error:String(error.stack||error)}));
    """, str(path))
    assert result == {'aborted': True, 'created': 0}, result


def test_tabber_pins_share_primary_row_and_preserve_hover_order(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path,
        '?sessions=tabber,zulu,alpha&layout=row@22(side,main)&tabs=side:@side-left,tabber;main:zulu,alpha',
        sessions=['zulu', 'alpha'])
    result = browser.execute_async_script("""
      const done=arguments[0];
      (async()=>{
        await window.__yomuxTestWaitFor(()=>panelNodes.get(tabberItemId)?.querySelector('.file-tree-row'),{timeoutMs:10000});
        setTabberMode('normal');
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        await openFileSurface(tabberItemId);
        await window.__yomuxTestWaitFor(()=>panelNodes.get(tabberItemId)?.querySelector('.changes-groups .file-tree-row'),{timeoutMs:10000});
        setFileExplorerViewSetting('tabber','treeSortMode','az',{refresh:true,publish:false});
        setTabPinned('zulu',true);setTabPinned(prefsItemId,true);
        const panel=panelNodes.get(tabberItemId),groups=panel.querySelector('.changes-groups');
        const rows=()=>[...groups.querySelectorAll('.file-tree-row')].filter(row=>row.dataset.tabberPaneItem).map(row=>row.dataset.tabberPaneItem);
        const initial=rows();renderTabberTree(groups,{preserveDisplayedOrder:true});
        const retained=rows();
        const pins=[...groups.querySelectorAll('.pane-tab-pin-icon')].map(pin=>{
          const primary=pin.closest('.tabber-normal-primary'),row=pin.closest('.file-tree-row');
          return {inside:!!primary,top:pin.getBoundingClientRect().top,primaryTop:primary?.getBoundingClientRect().top,rowHeight:row.getBoundingClientRect().height};
        });
        panel.__tabberPointerInside=true;
        transcriptMetadataState.payload.sessions.third={panes:[],agents:[]};
        transcriptMetadataState.payload.session_order.push('third');
        refreshTabberPanels();
        const hovered=rows();
        setTabPinned('zulu',false);const unpinned=rows();
        return {initial,retained,pins,hovered,unpinned};
      })().then(done,error=>done({error:String(error.stack||error)}));
    """)
    assert 'error' not in result, result
    assert result['initial'] == result['retained'], result
    assert result['initial'][:2] == ['__prefs__', 'zulu'], result
    assert all(pin['inside'] and abs(pin['top'] - pin['primaryTop']) < 8 for pin in result['pins']), result
    assert [item for item in result['hovered'] if item in result['initial']] == result['initial'], result
    assert result['unpinned'][0] == '__prefs__', result


def test_settings_recover_between_browsers_without_rebuilding_editors(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('two-browser-settings')
    request.addfinalizer(lambda: browser.get('about:blank'))
    url = server.base_url + '/?layout=left&tabs=left:__prefs__'
    path = server.paths.home_dir / 'settings-editor.md'
    path.write_text('Paragraph.\n\n' * 100, encoding='utf-8')
    browser.get(url)
    wait = WebDriverWait(browser, 15)
    wait.until(lambda driver: driver.execute_script("return typeof clientEventTransportState!=='undefined' && clientEventTransportState.connected"))
    first = browser.current_window_handle
    browser.switch_to.new_window('tab')
    second = browser.current_window_handle
    browser.get(url)
    wait.until(lambda driver: driver.execute_script("return typeof clientEventTransportState!=='undefined' && clientEventTransportState.connected"))
    browser.execute_script(BROWSER_WAIT_HELPER_SOURCE)
    try:
        opened = browser.execute_async_script("""
          const path=arguments[0],done=arguments[arguments.length-1];
          (async()=>{
            const item=await openFileInEditor(path);setFileEditorViewMode(path,'split',item);
            await window.__yomuxTestWaitFor(()=>panelNodes.get(item)?._pmView && panelNodes.get(item)?._cmView,{timeoutMs:10000});
            const panel=panelNodes.get(item);window.__settingsEditor=panel;
            window.__settingsCM=panel._cmView;window.__settingsPM=panel._pmView;
            panel._cmView.focus();panel._cmView.dispatch({selection:{anchor:4}});
            await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
            panel._cmView.scrollDOM.scrollTop=60;
            await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
            window.__settingsScroll=panel._cmView.scrollDOM.scrollTop;
            window.__settingsLayout=JSON.stringify(layoutSlots);
            window.__settingsText=panel._cmView.state.doc.toString();
            return true;
          })().then(done,error=>done(String(error.stack||error)));
        """, str(path))
        assert opened is True, opened
        browser.execute_script("disposeClientEventTransportLifecycle('settings-test-disconnect')")
        browser.switch_to.window(first)
        result = browser.execute_async_script("saveSettingsPatch({appearance:{active_color:'blue',editor_cursor_style:'block'}}).then(()=>arguments[0](true),error=>arguments[0](String(error)))")
        assert result is True, result
        browser.switch_to.window(second)
        browser.execute_script("syncClientEventDemand({immediate:true})")
        wait.until(lambda driver: driver.execute_script("return initialSetting('appearance.active_color')==='blue' && fileEditorCursorStyle==='block'"))
        for handle in [first, second]:
            browser.switch_to.window(handle)
            browser.execute_script("window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}))")
            browser.execute_cdp_cmd('Page.setWebLifecycleState', {'state': 'frozen'})
        server.restart()
        for handle in [first, second]:
            browser.switch_to.window(handle)
            browser.execute_cdp_cmd('Page.setWebLifecycleState', {'state': 'active'})
            browser.execute_script("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
        browser.switch_to.window(first)
        wait.until(lambda driver: driver.execute_script("return clientEventTransportState.connected"))
        result = browser.execute_async_script("saveSettingsPatch({appearance:{active_color:'orange'}}).then(()=>arguments[0](true),error=>arguments[0](String(error)))")
        assert result is True, result
        browser.switch_to.window(second)
        wait.until(lambda driver: driver.execute_script("return initialSetting('appearance.active_color')==='orange'"))
        preserved = browser.execute_script("""
          const panel=window.__settingsEditor;
          return {cm:panel._cmView===window.__settingsCM,pm:panel._pmView===window.__settingsPM,
            selection:panel._cmView.state.selection.main.anchor,
            scroll:panel._cmView.scrollDOM.scrollTop,expectedScroll:window.__settingsScroll,
            content:panel._cmView.state.doc.toString()===window.__settingsText,
            layout:JSON.stringify(layoutSlots)===window.__settingsLayout};
        """)
        assert preserved['cm'] and preserved['pm'] and preserved['content'] and preserved['layout'], preserved
        assert preserved['selection'] == 4 and preserved['scroll'] == preserved['expectedScroll'], preserved
    finally:
        browser.switch_to.window(second)
        browser.get('about:blank')
        browser.close()
        browser.switch_to.window(first)
