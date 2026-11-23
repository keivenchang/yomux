# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import base64
import json
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import load_dockview_runtime_boot_fixture
from tests.browser_helpers.browser_layout import register_browser_new_document_script
from tests.browser_helpers.browser_layout import remove_browser_new_document_script
from tests.browser_helpers.native_pointer import click_visible_selector
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tests.helpers.pdf_fixtures import preview_pdf


def wheel_on(browser, element, delta):
    point = browser.execute_script("""
      const rect = arguments[0].getBoundingClientRect();
      return {x: Math.floor(rect.left + rect.width * 0.6), y: Math.floor(rect.top + rect.height * 0.6)};
    """, element)
    browser.execute_cdp_cmd('Input.dispatchMouseEvent', {'type': 'mouseWheel', **point, 'deltaX': 0, 'deltaY': delta})


@pytest.mark.browser
def test_tex_preview_install_consent_failure_recovery_and_unsaved_source(browser, tmp_path, request):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?layout=left&tabs=left:file%3A%2Fhome%2Ftest%2Fpaper.tex*", sessions=["1"])
    wait = WebDriverWait(browser, 15)
    wait.until(lambda driver: driver.execute_script("const p=panelNodes.get(fileEditorItemFor('/home/test/paper.tex'));return Boolean(p && !fileStateFor('/home/test/paper.tex').loading);"))
    assert browser.execute_script("return editorViewModeFor('/home/test/paper.tex',fileEditorItemFor('/home/test/paper.tex'))") == 'preview'
    browser.find_element(By.CSS_SELECTOR, '[data-editor-mode="edit"]').click()
    wait.until(lambda driver: driver.execute_script("const p=panelNodes.get(fileEditorItemFor('/home/test/paper.tex'));return Boolean(p?._cmView && !fileStateFor('/home/test/paper.tex').loading);"))
    pdf = preview_pdf()
    browser.execute_script(
        r"""
        const originalFetch = window.fetch;
        const pdf = Uint8Array.from(atob(arguments[0]), ch => ch.charCodeAt(0));
        window.__texAvailable = false;
        window.__texRequests = [];
        window.__texInstalls = 0;
        window.fetch = async (input, options = {}) => {
          const route = new URL(String(input), location.href).pathname;
          if (route === '/api/tex/compiler') return Response.json({available:window.__texAvailable,install_supported:true,instructions:'Manual install: brew install tectonic'});
          if (route === '/api/tex/compiler/install') {
            window.__texRequests.push({route, body:JSON.parse(options.body)});
            window.__texInstalls++;
            await new Promise(resolve => {window.__texFinishInstall=resolve;});
            window.__texAvailable = window.__texInstalls > 1;
            return Response.json({available:window.__texAvailable,code:'install_failed',error:'Download refused by test network',instructions:'Manual install: brew install tectonic'});
          }
          if (route === '/api/tex/preview') {
            window.__texRequests.push({route, body:JSON.parse(options.body)});
            return new Response(pdf, {headers:{'Content-Type':'application/pdf'}});
          }
          return originalFetch(input, options);
        };
        const path = '/home/test/paper.tex';
        const item = fileEditorItemFor(path);
        setFileState(path, {...fileStateFor(path),kind:'text',content:'Unsaved equation',original:'Saved source',dirty:true,language:'stex'});
        setFileEditorViewMode(path,'edit',item);
        const panel = panelNodes.get(item);
        panel.id = 'tex-preview-test';
        renderFileEditorPanel(panel,item);
        """, base64.b64encode(pdf).decode(),
    )
    request.addfinalizer(lambda: browser.execute_script("const p=document.getElementById('tex-preview-test');if(p){fileStateFor('/home/test/paper.tex').dirty=false;destroyFileEditorPanel(p);panelNodes.delete(fileEditorItemFor('/home/test/paper.tex'));p.remove();}"))
    assert browser.execute_script("return previewRendererForPath('/home/test/paper.tex').id") == 'tex'
    preview = wait.until(lambda driver: next((node for node in driver.find_elements(By.CSS_SELECTOR, '#tex-preview-test [data-editor-mode="preview"]') if node.is_displayed()), None))
    assert preview.get_attribute('aria-label') == 'Preview'
    preview.click()
    assert not browser.find_element(By.CSS_SELECTOR, '#tex-preview-test .file-editor-preview-font-panel').is_displayed()
    first = browser.execute_async_script("const done=arguments[arguments.length-1];const p=document.getElementById('tex-preview-test'),pane=p.querySelector('.file-editor-preview-pane-panel');Promise.resolve(pane._previewAsync).then(()=>done({mode:editorViewModeFor('/home/test/paper.tex',fileEditorItemFor('/home/test/paper.tex')),text:p.innerText,host:!!pane.querySelector('[data-tex-preview]'),requests:window.__texRequests}));")
    assert 'TeX Compiler Required' in first['text'], first
    assert browser.execute_script('return window.__texInstalls') == 0
    assert 'Or install manually: Manual install: brew install tectonic' in first['text'], first

    def install_button(driver):
        return next((node for node in driver.find_elements(By.CSS_SELECTOR, '#tex-preview-test .tex-preview-install-button') if node.is_displayed()), None)

    install = wait.until(install_button)
    assert install.text == 'Install TeX Compiler'
    install.click()
    wait.until(lambda driver: driver.execute_script("return window.__texInstalls === 1"))
    assert 'Let me try… Installing' in browser.execute_script("return document.getElementById('tex-preview-test').innerText")
    assert browser.execute_script('return window.__texRequests[0].body.consent') is True
    browser.execute_script('window.__texFinishInstall()')
    wait.until(lambda driver: 'Download refused by test network' in driver.find_element(By.ID, 'tex-preview-test').text)
    assert 'brew install tectonic' in browser.find_element(By.ID, 'tex-preview-test').text
    style = browser.execute_script("""
      const card=document.querySelector('#tex-preview-test [data-tex-preview] .file-editor-empty-state');
      const detail=card.querySelector('.file-editor-empty-detail'), instructions=card.querySelector('pre');
      return {align:getComputedStyle(card).textAlign,font:getComputedStyle(card).fontFamily,
        detailFont:getComputedStyle(detail).fontFamily,wrap:getComputedStyle(instructions).whiteSpace,
        actionsWrap:getComputedStyle(card.querySelector('.tex-preview-actions') || card).flexWrap};
    """)
    assert style['align'] == 'left', style
    assert style['font'] == style['detailFont'], style
    assert style['wrap'] == 'pre-wrap' and style['actionsWrap'] == 'wrap', style

    browser.find_element(By.CSS_SELECTOR, '#tex-preview-test .tex-preview-actions button').click()
    wait.until(install_button).click()
    wait.until(lambda driver: driver.execute_script('return window.__texInstalls === 2'))
    browser.execute_script('window.__texFinishInstall()')
    try:
        wait.until(lambda driver: driver.execute_script("const panel=document.getElementById('tex-preview-test'),frame=panel.querySelector('.file-editor-pdf-preview');return frame?.dataset.pdfReady==='true' && panel.querySelector('.file-editor-preview-fallback')?.hidden===true"))
    except TimeoutException as error:
        details = browser.execute_script("""
          const panel=document.getElementById('tex-preview-test');
          const frame=panel.querySelector('.file-editor-pdf-preview');
          const app=frame?.contentWindow?.PDFViewerApplication;
          return {text:panel.innerText,ready:frame?.dataset.pdfReady,initialized:app?.initialized,pages:app?.pagesCount,
            width:frame?.offsetWidth,height:frame?.offsetHeight};
        """)
        raise AssertionError(details) from error
    received_pdf = browser.execute_async_script("const done=arguments[0],xhr=new XMLHttpRequest();xhr.open('GET',document.querySelector('#tex-preview-test .file-editor-pdf-preview')._rawFileObjectUrl);xhr.responseType='arraybuffer';xhr.onload=()=>done(btoa(String.fromCharCode(...new Uint8Array(xhr.response))));xhr.onerror=()=>done({error:'PDF Blob read failed'});xhr.send();")
    assert base64.b64decode(received_pdf) == pdf, 'all three PDF pages reach the viewer unchanged'
    metrics = browser.execute_script(r"""
      const path='/home/test/paper.tex', panel=panelNodes.get(fileEditorItemFor(path));
      const frame=panel.querySelector('.file-editor-pdf-preview');
      const retry=panel.querySelector('[data-tex-preview-ready="true"] > .file-editor-preview-fallback');
      const metrics={source:window.__texRequests.at(-1).body.content,dirty:fileStateFor(path).dirty,
        blob:frame._rawFileObjectUrl.startsWith('blob:'),sandbox:frame.getAttribute('sandbox'),
        width:frame.getBoundingClientRect().width,height:frame.getBoundingClientRect().height,
        fallbackHidden:retry.hidden, fallbackButtons:retry.querySelectorAll('button').length,
        paneReloadVisible:!panel.querySelector('.file-editor-reload-panel').hidden};
      fileStateFor(path).dirty=false;
      destroyFileEditorPanel(panel);
      metrics.released=frame._rawFileObjectUrl==='';
      metrics.aborted=panel.querySelector('.file-editor-preview-pane-panel')._texPreviewController===null;
      panelNodes.delete(fileEditorItemFor(path));panel.remove();
      return metrics;
    """)
    assert metrics['source'] == 'Unsaved equation' and metrics['dirty']
    assert metrics['blob'] and metrics['sandbox'] is None
    assert metrics['width'] > 600 and metrics['height'] > 300
    assert metrics['fallbackHidden'] and metrics['fallbackButtons'] == 0, metrics
    assert metrics['paneReloadVisible'], metrics
    assert metrics['released'] and metrics['aborted'], metrics


@pytest.mark.browser
@pytest.mark.parametrize('extension', ['tex', 'yaml'])
def test_text_file_editor_popout_keeps_live_mode_controls(browser, isolated_dev_server_factory, extension, request):
    server = isolated_dev_server_factory('editor-popout')
    source = server.paths.home_dir / f'popout.{extension}'
    source.write_text('title: pop-out editor\n')
    path = str(source)
    item = 'file:' + path
    search = '?' + urlencode({'sessions': item, 'layout': 'main', 'tabs': 'main:' + item})
    request.addfinalizer(lambda: browser.get('about:blank'))
    browser.get(server.base_url + '/' + search)
    wait = WebDriverWait(browser, 15)
    wait.until(lambda driver: driver.execute_script("return typeof setTabberMode === 'function' && dockviewLayoutActive()"))
    placement = browser.execute_async_script("""
      const [item, path, session, done] = arguments;
      (async () => {
        setTabberMode('advanced');
        const targetSlot = slotForItem(session);
        if (!targetSlot || slotIsSidePane(targetSlot)) throw new Error('Expected a main pane for the fixture session');
        await openFileEditorPane(path, {item, targetSlot, rehomeExisting: true});
        return true;
      })().then(done, error => done(String(error)));
    """, item, path, server.tmux.sessions[0])
    assert placement is True, placement
    wait.until(lambda driver: driver.execute_script(
        """const [item, path] = arguments, panel = panelNodes.get(item);
        return panel?.isConnected && !fileStateFor(path).loading && !panel.closest('.side-pane-role');""",
        item, path,
    ))
    editor_popout = wait.until(lambda driver: next((node for node in driver.find_elements(
        By.CSS_SELECTOR, '.dockview-pane-header-actions .pane-popout[data-pane-popout]'
    ) if node.is_displayed() and node.get_attribute('data-pane-popout') == item), None))
    assert editor_popout.get_attribute('aria-label') == 'Pop out'
    main = browser.current_window_handle
    editor_popout.click()
    wait.until(lambda driver: len(driver.window_handles) == 2)
    popup = next(handle for handle in browser.window_handles if handle != main)
    browser.switch_to.window(popup)
    wait.until(lambda driver: driver.execute_script(
        "const item='file:'+arguments[0], panel=panelNodes.get(item);return !!panel && !fileStateFor(arguments[0]).loading",
        path,
    ))
    for mode in ['edit', 'preview', 'split']:
        button = wait.until(lambda driver: next((node for node in driver.find_elements(
            By.CSS_SELECTOR, f'[data-action="editor-mode"][data-editor-mode="{mode}"]'
        ) if node.is_displayed()), None))
        button.click()
        wait.until(lambda driver: driver.execute_script(
            "return editorViewModeFor(arguments[0],'file:'+arguments[0])===arguments[1]",
            path, mode,
        ))
        if mode in ('edit', 'split'):
            wait.until(lambda driver: driver.execute_script(
                "return !!panelNodes.get('file:'+arguments[0])?._cmView?.contentDOM?.isContentEditable",
                path,
            ))
    browser.close()
    browser.switch_to.window(main)
    wait.until(lambda driver: driver.execute_script(
        "return !!panelNodes.get(arguments[0]) && itemInLayout(arguments[0])",
        item,
    ))


@pytest.mark.browser
def test_pdf_tab_popout_detaches_and_uses_available_height(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('pdf-live-popout')
    source = server.paths.home_dir / 'popout.pdf'
    sample = Path(__file__).resolve().parents[1] / 'examples' / '13-pdf.pdf'
    source.write_bytes(sample.read_bytes())
    path = str(source)
    item = 'file:' + path
    search = '?' + urlencode({'sessions': item, 'layout': 'left', 'tabs': 'left:' + item})
    request.addfinalizer(lambda: browser.get('about:blank'))
    browser.get(server.base_url + '/' + search)
    wait = WebDriverWait(browser, 20)
    wait.until(lambda driver: driver.execute_script(
        "return !!panelNodes.get(arguments[0]) && !fileStateFor(arguments[1]).loading",
        item, path,
    ))
    main = browser.current_window_handle
    browser.execute_script('showTabContextMenu(arguments[0], 20, 20)', item)
    popout = wait.until(lambda driver: next((node for node in driver.find_elements(
        By.CSS_SELECTOR, '.terminal-context-menu [aria-label="Pop out"]'
    ) if node.is_displayed()), None))
    popout.click()
    wait.until(lambda driver: len(driver.window_handles) == 2)
    popup = next(handle for handle in browser.window_handles if handle != main)
    browser.switch_to.window(popup)
    metrics = wait.until(lambda driver: driver.execute_script(
        """
        const frame = document.querySelector('.file-editor-pdf-preview[data-pdf-ready="true"]');
        if (!frame) return false;
        const rect = frame.getBoundingClientRect();
        const fallback = frame.parentElement.querySelector('.file-editor-preview-fallback');
        return {itemInLayout:itemInLayout(arguments[0]),height:rect.height,viewport:innerHeight,
          fallbackHidden:fallback.hidden,fallbackDisplay:getComputedStyle(fallback).display};
        """,
        item,
    ))
    assert metrics['itemInLayout'] is True, metrics
    assert metrics['height'] > metrics['viewport'] * 0.8, metrics
    assert metrics['fallbackHidden'] and metrics['fallbackDisplay'] == 'none', metrics
    browser.close()
    browser.switch_to.window(main)
    wait.until(lambda driver: driver.execute_script(
        "return !!panelNodes.get(arguments[0]) && itemInLayout(arguments[0])",
        item,
    ))


@pytest.mark.browser
def test_tex_view_mode_survives_immediate_browser_reload(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('tex-mode-reload')
    source = server.paths.home_dir / 'multipage.tex'
    source.write_text('TeX mode reload fixture')
    path = str(source)
    item = 'file:' + path
    state = {'v': 1, 'editor': {'modes': [{'path': path, 'item': item, 'mode': 'preview'}]}}
    search = '?' + urlencode({'sessions': item, 'layout': 'left', 'tabs': 'left:' + item, 'state': json.dumps(state)})
    request.addfinalizer(lambda: browser.get('about:blank'))
    browser.get(server.base_url + '/' + search)
    wait = WebDriverWait(browser, 15)
    for mode in ['split', 'edit', 'preview']:
        button = wait.until(lambda driver: next((node for node in driver.find_elements(By.CSS_SELECTOR, f'[data-action="editor-mode"][data-editor-mode="{mode}"]') if node.is_displayed()), None))
        button.click()
        saved = browser.execute_script("return JSON.parse(new URLSearchParams(location.search).get('state')).editor.modes", path)
        assert next(entry['mode'] for entry in saved if entry['item'] == item) == mode
        browser.refresh()
        wait.until(lambda driver: driver.execute_script("return typeof editorViewModeFor==='function' && !!panelNodes.get(arguments[1]) && !fileStateFor(arguments[0]).loading", path, item))
        assert browser.execute_script('return editorViewModeFor(arguments[0],arguments[1])', path, item) == mode


@pytest.mark.browser
@pytest.mark.parametrize('extension', ['pdf', 'tex'])
def test_pdf_page_survives_browser_reload(browser, isolated_dev_server_factory, extension, request):
    server = isolated_dev_server_factory('pdf-page-reload')
    (server.paths.config_dir / 'settings.yaml').write_bytes(b'general:\n  startup_tips: false\n')
    path = server.paths.home_dir / ('multipage.' + extension)
    if extension == 'pdf':
        sample = Path(__file__).resolve().parents[1] / 'examples' / '13-pdf.pdf'
        path.write_bytes(sample.read_bytes())
    else:
        path.write_bytes(b'Unsaved TeX fixture')
    main = browser.current_window_handle
    script = register_browser_new_document_script(browser, """
      const nativeFetch = window.fetch;
      window.fetch = (input, options) => {
        const route = new URL(String(input), location.href).pathname;
        if (route === '/api/tex/compiler') return Promise.resolve(Response.json({available:true}));
        if (route === '/api/tex/preview') return Promise.resolve(new Response(
          Uint8Array.from(atob('%s'), ch => ch.charCodeAt(0)), {headers:{'Content-Type':'application/pdf'}}));
        return nativeFetch(input, options);
      };
    """ % base64.b64encode(preview_pdf(outline=True)).decode())

    def retire_preview_windows():
        for handle in set(browser.window_handles) - {main}:
            browser.switch_to.window(handle)
            browser.get('about:blank')
            browser.close()
        browser.switch_to.window(main)
        remove_browser_new_document_script(browser, script, window_handle=main)
        browser.get('about:blank')

    request.addfinalizer(retire_preview_windows)
    browser.get(server.base_url + '/?layout=left&tabs=left:' + quote('file:' + str(path), safe='') + '*')
    wait = WebDriverWait(browser, 25)

    def enter_viewer(pages=3, item=None):
        try:
            frame = wait.until(lambda driver: next((node for node in driver.find_elements(By.CSS_SELECTOR, '.file-editor-pdf-preview[data-pdf-ready="true"]') if node.is_displayed() and (item is None or node.get_attribute('data-layout-item') == item)), None))
        except TimeoutException as error:
            details = browser.execute_script("""
              return {path:location.pathname, frames:[...document.querySelectorAll('.file-editor-pdf-preview')].map(f=>{
                const app = f.contentWindow?.PDFViewerApplication;
                return {item:f.dataset.layoutItem, ready:f.dataset.pdfReady, width:f.offsetWidth, height:f.offsetHeight,
                  initialized:app?.initialized, pages:app?.pagesCount, page:app?.page};
              })};
            """)
            raise AssertionError(details) from error
        browser.switch_to.frame(frame)
        wait.until(lambda driver: driver.execute_script('return PDFViewerApplication.pagesCount === arguments[0]', pages))

    enter_viewer()
    browser.find_element(By.ID, 'viewsManagerToggleButton').click()
    wait.until(lambda driver: driver.execute_script('return PDFViewerApplication.viewsManager.isOpen'))
    page_input = browser.find_element(By.ID, 'pageNumber')
    page_input.send_keys(Keys.CONTROL, 'a')
    page_input.send_keys('3', Keys.ENTER)
    wait.until(lambda driver: driver.execute_script('return PDFViewerApplication.pdfViewer.currentPageNumber === 3 && !!document.querySelector(\'.page[data-page-number="3"] canvas\')'))
    Select(browser.find_element(By.ID, 'scaleSelect')).select_by_value('1.5')
    browser.execute_script('PDFViewerApplication.pdfViewer.container.scrollTop += 160')
    offset = browser.execute_script("""
      const view = PDFViewerApplication.pdfViewer;
      return view.container.scrollTop - view.getPageView(2).div.offsetTop;
    """)
    assert offset > 100
    browser.switch_to.default_content()
    saved = wait.until(lambda driver: driver.execute_script("""
      const modes = JSON.parse(new URLSearchParams(location.search).get('state')).editor.modes;
      return modes.some(entry => entry.pdfViewport?.zoom === '1.5' && entry.pdfViewport.page === 3
        && entry.pdfViewport.top < 730) ? modes : null;
    """))
    assert next(entry['pdfPage'] for entry in saved if entry['path'] == str(path)) == 3
    assert next(entry['pdfSidebarView'] for entry in saved if entry['path'] == str(path)) == 1
    if extension == 'pdf':
        frame = browser.find_element(By.CSS_SELECTOR, '[data-pdf-ready=true]')
        browser.execute_script("""
          const app = arguments[0].contentWindow.PDFViewerApplication;
          const view = app.pdfViewer, previousUrl = location.href;
          const onViewArea = () => {
            const offset = view.container.scrollTop - view.getPageView(2).div.offsetTop;
            if (offset <= arguments[1] + 50) return;
            app.eventBus.off('updateviewarea', onViewArea);
            sessionStorage.setItem('pdf-reload-fixture', JSON.stringify({offset, urlStillPending: location.href === previousUrl}));
            location.reload();
          };
          app.eventBus.on('updateviewarea', onViewArea);
        """, frame, offset)
        wheel_on(browser, frame, 80)
        reloading = wait.until(lambda driver: driver.execute_script("""
          if (performance.getEntriesByType('navigation')[0]?.type !== 'reload') return null;
          const data = sessionStorage.getItem('pdf-reload-fixture');
          sessionStorage.removeItem('pdf-reload-fixture');
          return JSON.parse(data);
        """))
    else:
        reloading = browser.execute_script("""
          const view = document.querySelector('[data-pdf-ready=true]').contentWindow.PDFViewerApplication.pdfViewer;
          const previousUrl = location.href;
          const scroller = view.container, page = view.getPageView(2).div;
          scroller.scrollTop += 80;
          const result = {offset: scroller.scrollTop - page.offsetTop, urlStillPending: location.href === previousUrl};
          location.reload();
          return result;
        """)
    assert reloading['urlStillPending'] and reloading['offset'] > offset + 50, reloading
    offset = reloading['offset']
    enter_viewer()
    assert browser.execute_script('return PDFViewerApplication.viewsManager.isOpen'), 'Manage pages must remain open after reload'
    assert browser.find_element(By.ID, 'viewsManager').is_displayed()
    wait.until(lambda driver: driver.execute_script('return PDFViewerApplication.pdfViewer.currentPageNumber === 3 && !!document.querySelector(\'.page[data-page-number="3"] canvas\')'))
    assert browser.find_element(By.ID, 'pageNumber').get_attribute('value') == '3'
    assert browser.execute_script("return PDFViewerApplication.pdfViewer.currentScaleValue") == '1.5'
    assert abs(browser.execute_script("""
      const view = PDFViewerApplication.pdfViewer;
      return view.container.scrollTop - view.getPageView(2).div.offsetTop;
    """) - offset) <= 2
    assert browser.execute_script("return PDFViewerApplicationOptions.get('enableScripting')") is False
    assert browser.execute_script("return PDFViewerApplicationOptions.get('enableXfa')") is False
    wait.until(lambda driver: driver.execute_script('return !document.getElementById("outlinesViewMenu").disabled'))
    browser.find_element(By.ID, 'viewsManagerSelectorButton').click()
    browser.find_element(By.ID, 'outlinesViewMenu').click()
    wait.until(lambda driver: driver.execute_script('return PDFViewerApplication.viewsManager.active === 2'))
    browser.find_element(By.ID, 'viewsManagerToggleButton').click()
    wait.until(lambda driver: driver.execute_script('return !PDFViewerApplication.viewsManager.isOpen'))
    browser.switch_to.default_content()
    closed = browser.execute_script("return JSON.parse(new URLSearchParams(location.search).get('state')).editor.modes")
    assert next(entry['pdfSidebarView'] for entry in closed if entry['path'] == str(path)) == 2
    assert next(entry['pdfSidebarOpen'] for entry in closed if entry['path'] == str(path)) is False
    browser.refresh()
    enter_viewer()
    assert browser.execute_script('return PDFViewerApplication.viewsManager.visibleView') == 0
    assert not browser.find_element(By.ID, 'viewsManager').is_displayed()
    browser.find_element(By.ID, 'viewsManagerToggleButton').click()
    assert browser.execute_script('return PDFViewerApplication.viewsManager.active') == 2
    blocked = browser.execute_script("""
      let replacements=0;
      PDFViewerApplication.eventBus.on('fileinputchange',()=>replacements++);
      const transfer=new DataTransfer();transfer.items.add(new File(['test'],'replacement.pdf',{type:'application/pdf'}));
      document.getElementById('mainContainer').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));
      return replacements;
    """)
    assert blocked == 0, 'Finder owns document replacement; viewer drops cannot change the bound PDF'
    browser.find_element(By.ID, 'secondaryToolbarToggle').click()
    assert not browser.find_element(By.ID, 'secondaryOpenFile').is_displayed()
    browser.find_element(By.ID, 'secondaryToolbarToggle').click()
    browser.switch_to.default_content()
    if extension == 'tex':
        for mode in ['split', 'edit', 'preview']:
            browser.find_element(By.CSS_SELECTOR, f'[data-editor-mode="{mode}"]').click()
            if mode != 'edit':
                enter_viewer()
                assert browser.find_element(By.ID, 'pageNumber').get_attribute('value') == '3'
                assert browser.execute_script('return PDFViewerApplication.viewsManager.isOpen')
                assert browser.execute_script('return PDFViewerApplication.viewsManager.active') == 2
                browser.switch_to.default_content()
    else:
        main = browser.current_window_handle
        shell = browser.execute_async_script("""
          const done = arguments[0];
          fetch('/preview-popout').then(async response => done({
            status: response.status, blankShell: !(await response.text()).includes('<script')
          }), error => done({error: String(error)}));
        """)
        assert shell == {'status': 200, 'blankShell': True}, shell
        browser.execute_script('openFilePreviewPopout(arguments[0])', str(path))
        wait.until(lambda driver: len(driver.window_handles) == 2)
        browser.switch_to.window(next(handle for handle in browser.window_handles if handle != main))
        enter_viewer()
        assert browser.execute_script('return PDFViewerApplication.viewsManager.isOpen')
        assert browser.execute_script("return PDFViewerApplicationOptions.get('enableScripting')") is False
        field = browser.find_element(By.ID, 'pageNumber')
        field.send_keys(Keys.CONTROL, 'a')
        field.send_keys('2', Keys.ENTER)
        wait.until(lambda driver: driver.execute_script('return PDFViewerApplication.pdfViewer.currentPageNumber === 2'))
        browser.switch_to.default_content()
        browser.close()
        browser.switch_to.window(main)
        enter_viewer()
        browser.find_element(By.ID, 'viewsManagerToggleButton').click()
        assert not browser.execute_script('return PDFViewerApplication.viewsManager.isOpen')
        browser.switch_to.default_content()
        browser.refresh()
        enter_viewer()
        assert browser.find_element(By.ID, 'pageNumber').get_attribute('value') == '2'
        assert not browser.execute_script('return PDFViewerApplication.viewsManager.isOpen')
        browser.find_element(By.ID, 'viewsManagerToggleButton').click()
        browser.switch_to.default_content()
        other_path = path.with_name('other.pdf')
        other_path.write_bytes(preview_pdf())
        other = browser.execute_async_script("const done=arguments[arguments.length-1];openFileInEditor(arguments[0],'other.pdf').then(done,error=>done({error:String(error)}));", str(other_path))
        assert other == 'file:' + str(other_path)
        for tab_item, page in [('file:' + str(path), '2'), (other, '1')]:
            click_visible_selector(browser, f'[data-pane-tab="{tab_item}"]')
            enter_viewer(item=tab_item)
            wait.until(lambda driver: driver.find_element(By.ID, 'pageNumber').get_attribute('value') == page,
                       message=f'{tab_item} should restore page {page}')
            assert browser.execute_script('return PDFViewerApplication.viewsManager.isOpen') == (tab_item == 'file:' + str(path))
            browser.switch_to.default_content()
        assert browser.execute_script("""
          const originalOpen = window.open;
          window.open = (...args) => {
            const popup = originalOpen.apply(window, args);
            const originalListen = popup.addEventListener;
            // A navigation can discard the listener registered on the initial popup.
            popup.addEventListener = (type, ...options) => {
              if (type !== 'load') originalListen.call(popup, type, ...options);
            };
            return popup;
          };
          try {
            return openFilePreviewPopout(arguments[0], panelNodes.get(arguments[1]));
          } finally {
            window.open = originalOpen;
          }
        """, str(other_path), other)
        wait.until(lambda driver: len(driver.window_handles) == 2)
        popup = next(handle for handle in browser.window_handles if handle != main)
        browser.switch_to.window(popup)
        enter_viewer()
        assert browser.find_element(By.ID, 'pageNumber').get_attribute('value') == '1'
        field = browser.find_element(By.ID, 'pageNumber')
        field.send_keys(Keys.CONTROL, 'a')
        field.send_keys('3', Keys.ENTER)
        wait.until(lambda driver: driver.execute_script('return PDFViewerApplication.pdfViewer.currentPageNumber === 3'))
        browser.switch_to.default_content()
        browser.switch_to.window(main)
        saved = browser.execute_script("return JSON.parse(new URLSearchParams(location.search).get('state')).editor.modes")
        assert next(entry['pdfPage'] for entry in saved if entry['item'] == other) == 3
        assert next(entry['pdfPage'] for entry in saved if entry['item'] == 'file:' + str(path)) == 2
        browser.execute_script('closeFilePreviewPopout(arguments[0])', str(other_path))
        browser.execute_script('openFilePreviewPopout(arguments[0],panelNodes.get(fileEditorItemFor(arguments[0])))', str(path))
        wait.until(lambda driver: len(driver.window_handles) == 2)
        popup = next(handle for handle in browser.window_handles if handle != main)
        browser.switch_to.window(popup)
        enter_viewer(item='file:' + str(path))
        wait.until(lambda driver: driver.find_element(By.ID, 'pageNumber').get_attribute('value') == '2')
        browser.switch_to.default_content()
        assert not browser.find_elements(By.CSS_SELECTOR, '.file-editor-preview-font-panel')
        browser.execute_script("window.readingFrame = document.querySelector('[data-pdf-ready=true]')")
        browser.switch_to.window(main)
        assert browser.execute_script("return updateFilePreviewPopout(arguments[0], '')", str(path))
        browser.switch_to.window(popup)
        assert browser.execute_async_script("""
          const done = arguments[arguments.length - 1];
          requestAnimationFrame(() => requestAnimationFrame(() => {
            const frame = document.querySelector('[data-pdf-ready=true]');
            done(frame === window.readingFrame && frame.contentWindow.PDFViewerApplication.page === 2);
          }));
        """)
        browser.switch_to.window(main)
        browser.execute_script('closeFilePreviewPopout(arguments[0])', str(path))
        assert browser.execute_script("""
          const originalOpen = window.open;
          window.open = (_url, name, features) => originalOpen.call(window, '/login', name, features);
          try { return openFilePreviewPopout(arguments[0]); }
          finally { window.open = originalOpen; }
        """, str(path))
        assert wait.until(lambda driver: driver.execute_script("""
          const record = filePreviewPopouts.get(arguments[0]);
          const doc = record?.window.document;
          if (!doc || doc.readyState !== 'complete' || record.navigationWrite) return null;
          return {path: new URL(doc.URL).pathname, previewWritten: Boolean(doc.querySelector('[data-preview-root]'))};
        """, str(path))) == {'path': '/login', 'previewWritten': False}
        browser.execute_script('closeFilePreviewPopout(arguments[0])', str(path))
        path.write_bytes(preview_pdf(1))
        click_visible_selector(browser, f'[data-pane-tab="file:{path}"]')
        browser.refresh()
        enter_viewer(pages=1)
        assert browser.find_element(By.ID, 'pageNumber').get_attribute('value') == '1'
        browser.find_element(By.ID, 'secondaryToolbarToggle').click()
        browser.find_element(By.ID, 'pageRotateCw').click()
        browser.find_element(By.ID, 'scrollHorizontal').click()
        browser.switch_to.default_content()
        browser.refresh()
        enter_viewer(pages=1)
        assert browser.execute_script("""
          const view = PDFViewerApplication.pdfViewer;
          return {rotation: view.pagesRotation, scroll: view.scrollMode};
        """) == {'rotation': 90, 'scroll': 1}
        browser.switch_to.default_content()


@pytest.mark.browser
def test_tex_split_scroll_keeps_the_current_pane_in_charge(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('tex-split-scroll')
    source = server.paths.home_dir / 'multipage.tex'
    example = Path(__file__).resolve().parents[1] / 'examples' / '30-tex' / source.name
    source.write_bytes(example.read_bytes())
    path = str(source)
    item = 'file:' + path
    page_two, page_three = [index for index, line in enumerate(source.read_text().splitlines(), 1) if line == r'\newpage']
    anchors = [{'line': 9, 'page': 1}, {'line': page_two + 1, 'page': 2}, {'line': page_three + 1, 'page': 3}]
    script = browser.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': """
      const original = window.fetch;
      const anchors = %s;
      window.fetch = async (input, options) => {
        const url = new URL(String(input), location.href);
        if (url.pathname === '/api/tex/compiler') {
          const line = Number(url.searchParams.get('line'));
          const closest = anchors.reduce((a, b) => Math.abs(a.line - line) <= Math.abs(b.line - line) ? a : b);
          return Response.json({available:true, sync_page:closest.page, sync_pages:anchors});
        }
        if (url.pathname === '/api/tex/preview') {
          if (window.holdTexRefresh) await new Promise(resolve => {window.resolveTexRefresh = resolve;});
          return new Response(Uint8Array.from(atob('%s'), ch => ch.charCodeAt(0)), {headers:{'Content-Type':'application/pdf'}});
        }
        return original(input, options);
      };
    """ % (json.dumps(anchors), base64.b64encode(preview_pdf()).decode())})
    request.addfinalizer(lambda: browser.execute_cdp_cmd('Page.removeScriptToEvaluateOnNewDocument', {'identifier': script['identifier']}))
    request.addfinalizer(lambda: browser.get('about:blank'))
    state = {'v': 1, 'editor': {'modes': [{'path': path, 'item': item, 'mode': 'split'}]}}
    search = urlencode({'sessions': item, 'layout': 'left', 'tabs': 'left:' + item, 'state': json.dumps(state)})
    browser.get(server.base_url + '/?' + search)
    wait = WebDriverWait(browser, 20)
    wait.until(lambda driver: driver.execute_script("""
      const p = typeof panelNodes === 'undefined' ? null : panelNodes.get(arguments[0]);
      return !!p?._cmView && !!p.querySelector('.file-editor-preview-pane-panel')._texPreviewSyncCleanup;
    """, item))
    browser.execute_script("""
      const panel = panelNodes.get(arguments[0]), cm = panel._cmView;
      const frame = panel.querySelector('[data-pdf-ready="true"]');
      window.__texSplit = {panel, cm, frame, app:frame.contentWindow.PDFViewerApplication};
      window.__texSplit.app.pdfViewer.currentPageNumber = 2;
    """, item)
    toast = browser.execute_script("""
      const {frame} = window.__texSplit;
      const tip = showStartupHelperTip({manual: true});
      if (!tip) return null;
      const toolbar = frame.contentDocument.getElementById('toolbarViewer');
      const button = frame.contentDocument.getElementById('zoomInButton');
      const box = frame.getBoundingClientRect(), controls = toolbar.getBoundingClientRect(), target = button.getBoundingClientRect();
      return {belowToolbar: tip.getBoundingClientRect().top >= box.top + controls.bottom,
        zoomClickable: document.elementFromPoint(box.left + target.left + target.width / 2,
          box.top + target.top + target.height / 2) === frame};
    """)
    assert toast == {'belowToolbar': True, 'zoomClickable': True}, toast
    browser.execute_script("closeStartupHelperToast(document.querySelector('.startup-helper-toast'))")
    before = wait.until(lambda driver: driver.execute_script("""
      const {cm, app} = window.__texSplit;
      return app.pdfViewer.currentPageNumber === 2 ? {top:cm.scrollDOM.scrollTop} : null;
    """))
    retained = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      const {panel, frame, app, cm} = window.__texSplit;
      const item = arguments[0], path = arguments[1];
      ensureCodeMirrorPanel(panel, item, path, fileStateFor(path)).then(() => {
        setTimeout(() => done({page:app.pdfViewer.currentPageNumber, top:cm.scrollDOM.scrollTop,
          sameFrame:frame === panel.querySelector('[data-pdf-ready="true"]')}), 600);
      }, error => done({error:String(error)}));
    """, item, path)
    assert retained['sameFrame'] and retained['page'] == 2, retained
    assert before['top'] > 0, 'PDF navigation should scroll the source with the caret left alone'
    assert retained['top'] == before['top'], retained
    def wheel(pane, delta):
        element = browser.execute_script("""
          const {cm, frame} = window.__texSplit;
          return arguments[0] === 'source' ? cm.scrollDOM : frame;
        """, pane)
        wheel_on(browser, element, delta)

    wheel('source', -1800)
    wait.until(lambda driver: driver.execute_script('return window.__texSplit.app.pdfViewer.currentPageNumber === 1'))
    wheel('source', 1800)
    wait.until(lambda driver: driver.execute_script('return window.__texSplit.app.pdfViewer.currentPageNumber === 3'))
    pdf_offset = wait.until(lambda driver: driver.execute_script('return window.__texSplit.app.pdfViewer.container.scrollTop'))
    wheel('pdf', -pdf_offset - 100)
    try:
        wait.until(lambda driver: driver.execute_script("""
          const {app, cm} = window.__texSplit;
          return app.pdfViewer.currentPageNumber === 1 && cm.scrollDOM.scrollTop < arguments[0];
        """, retained['top']))
    except TimeoutException as error:
        metrics = browser.execute_script("""
          const {app, cm} = window.__texSplit;
          return {page: app.pdfViewer.currentPageNumber, pdfTop: app.pdfViewer.container.scrollTop, sourceTop: cm.scrollDOM.scrollTop};
        """)
        raise AssertionError(metrics) from error
    assert browser.execute_script('return window.__texSplit.cm.state.selection.main.head') == 0

    browser.execute_script("""
      const {cm, app} = window.__texSplit;
      app.pdfViewer.currentPageNumber = 2;
      app.pdfViewer.currentScaleValue = '1.5';
      window.holdTexRefresh = true;
      cm.dispatch({selection:{anchor:cm.state.doc.line(arguments[0] + 2).to}});
      cm.focus();
    """, page_two)
    wheel('pdf', 160)
    before = wait.until(lambda driver: driver.execute_script("""
      const {app} = window.__texSplit;
      const view = app.pdfViewer;
      const offset = view.container.scrollTop - view.getPageView(1).div.offsetTop;
      return offset > 100 ? {offset, zoom: view.currentScaleValue, page: view.currentPageNumber} : null;
    """))
    browser.find_element(By.CSS_SELECTOR, '.cm-content').send_keys(' ')
    wait.until(lambda driver: driver.execute_script('return typeof window.resolveTexRefresh === "function"'))
    assert browser.execute_script("""
      const {panel, frame} = window.__texSplit;
      return panel.querySelector('[data-pdf-ready="true"]') === frame && !!panel.querySelector('.pdf-preview-update-status');
    """)
    assert browser.execute_script("return window.__texSplit.app.pdfViewer.currentScaleValue") == before['zoom']
    browser.execute_script('window.resolveTexRefresh()')
    after = wait.until(lambda driver: driver.execute_script("""
      const {panel, frame} = window.__texSplit;
      const next = panel.querySelector('[data-pdf-ready="true"]');
      if (!next || next === frame) return null;
      const view = next.contentWindow.PDFViewerApplication.pdfViewer;
      return {offset: view.container.scrollTop - view.getPageView(1).div.offsetTop,
        zoom: view.currentScaleValue, page: view.currentPageNumber, oldReleased: frame._rawFileObjectUrl === ''};
    """))
    assert after['page'] == before['page'] == 2 and after['zoom'] == before['zoom'] == '1.5', after
    assert abs(after['offset'] - before['offset']) <= 2 and after['oldReleased'], after

    browser.execute_script("""
      const {panel} = window.__texSplit;
      const frame = panel.querySelector('[data-pdf-ready="true"]');
      window.retainedTexFrame = frame;
    """)
    for mode in ['preview', 'split']:
        browser.find_element(By.CSS_SELECTOR, f'[data-editor-mode="{mode}"]').click()
        assert browser.execute_script("""
          const {panel} = window.__texSplit;
          return panel.querySelector('[data-pdf-ready="true"]') === window.retainedTexFrame
            && !panel.querySelector('.file-editor-preview-font-panel').getClientRects().length;
        """)
