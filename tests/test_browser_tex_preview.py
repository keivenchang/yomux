# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import base64
import json
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import load_dockview_runtime_boot_fixture
from tests.browser_helpers.native_pointer import click_visible_selector
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tests.helpers.pdf_fixtures import preview_pdf


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
    first = browser.execute_async_script("const done=arguments[arguments.length-1];const p=document.getElementById('tex-preview-test'),pane=p.querySelector('.file-editor-preview-pane-panel');Promise.resolve(pane._previewAsync).then(()=>done({mode:editorViewModeFor('/home/test/paper.tex',fileEditorItemFor('/home/test/paper.tex')),text:p.innerText,host:!!pane.querySelector('[data-tex-preview]'),requests:window.__texRequests}));")
    assert 'No TeX compiler installed' in first['text'], first
    assert browser.execute_script('return window.__texInstalls') == 0
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

    def install_button(driver):
        return next((node for node in driver.find_elements(By.CSS_SELECTOR, '#tex-preview-test [data-tex-preview] button') if 'Install?' in node.text), None)

    assert install_button(browser) is not None, first
    install_button(browser).click()
    wait.until(lambda driver: driver.execute_script("return window.__texInstalls === 1"))
    assert 'Let me try… Installing' in browser.execute_script("return document.getElementById('tex-preview-test').innerText")
    assert browser.execute_script('return window.__texRequests[0].body.consent') is True
    browser.execute_script('window.__texFinishInstall()')
    wait.until(lambda driver: 'Download refused by test network' in driver.find_element(By.ID, 'tex-preview-test').text)
    assert 'brew install tectonic' in browser.find_element(By.ID, 'tex-preview-test').text
    browser.find_element(By.CSS_SELECTOR, '#tex-preview-test [data-tex-preview] button').click()
    wait.until(install_button).click()
    wait.until(lambda driver: driver.execute_script('return window.__texInstalls === 2'))
    browser.execute_script('window.__texFinishInstall()')
    wait.until(lambda driver: driver.execute_script("const panel=document.getElementById('tex-preview-test'),frame=panel.querySelector('.file-editor-pdf-preview');return frame?.dataset.pdfReady==='true' && panel.querySelector('.file-editor-preview-fallback')?.hidden===true"))
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
    search = '?' + urlencode({'sessions': item, 'layout': 'left', 'tabs': 'left:' + item})
    request.addfinalizer(lambda: browser.get('about:blank'))
    browser.get(server.base_url + '/' + search)
    wait = WebDriverWait(browser, 15)
    wait.until(lambda driver: driver.execute_script(
        "return !!panelNodes.get(arguments[0]) && !fileStateFor(arguments[1]).loading",
        item, path,
    ))
    editor_popout = wait.until(lambda driver: next((node for node in driver.find_elements(
        By.CSS_SELECTOR, '.file-editor-popout-editor-panel'
    ) if node.is_displayed()), None))
    assert editor_popout.get_attribute('aria-label') == 'Pop out editor'
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
    sample = Path(__file__).resolve().parents[1] / 'examples' / 'preview' / '13-pdf.pdf'
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
    path = server.paths.home_dir / ('multipage.' + extension)
    if extension == 'pdf':
        sample = Path(__file__).resolve().parents[1] / 'examples' / 'preview' / '13-pdf.pdf'
        path.write_bytes(sample.read_bytes())
    else:
        path.write_bytes(b'Unsaved TeX fixture')
    script = browser.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': """
      const nativeFetch = window.fetch;
      window.fetch = (input, options) => {
        const route = new URL(String(input), location.href).pathname;
        if (route === '/api/tex/compiler') return Promise.resolve(Response.json({available:true}));
        if (route === '/api/tex/preview') return Promise.resolve(new Response(
          Uint8Array.from(atob('%s'), ch => ch.charCodeAt(0)), {headers:{'Content-Type':'application/pdf'}}));
        return nativeFetch(input, options);
      };
    """ % base64.b64encode(preview_pdf(outline=True)).decode()})
    request.addfinalizer(lambda: browser.execute_cdp_cmd('Page.removeScriptToEvaluateOnNewDocument', {'identifier': script['identifier']}))
    request.addfinalizer(lambda: browser.get('about:blank'))
    browser.get(server.base_url + '/?layout=left&tabs=left:' + quote('file:' + str(path), safe='') + '*')
    wait = WebDriverWait(browser, 25)

    def enter_viewer(pages=3, item=None):
        frame = wait.until(lambda driver: next((node for node in driver.find_elements(By.CSS_SELECTOR, '.file-editor-pdf-preview[data-pdf-ready="true"]') if node.is_displayed() and (item is None or node.get_attribute('data-layout-item') == item)), None))
        browser.switch_to.frame(frame)
        wait.until(lambda driver: driver.execute_script('return PDFViewerApplication.pagesCount === arguments[0]', pages))

    enter_viewer()
    browser.find_element(By.ID, 'viewsManagerToggleButton').click()
    wait.until(lambda driver: driver.execute_script('return PDFViewerApplication.viewsManager.isOpen'))
    page_input = browser.find_element(By.ID, 'pageNumber')
    page_input.send_keys(Keys.CONTROL, 'a')
    page_input.send_keys('3', Keys.ENTER)
    wait.until(lambda driver: driver.execute_script('return PDFViewerApplication.pdfViewer.currentPageNumber === 3 && !!document.querySelector(\'.page[data-page-number="3"] canvas\')'))
    browser.switch_to.default_content()
    saved = browser.execute_script("return JSON.parse(new URLSearchParams(location.search).get('state')).editor.modes")
    assert next(entry['pdfPage'] for entry in saved if entry['path'] == str(path)) == 3
    assert next(entry['pdfSidebarView'] for entry in saved if entry['path'] == str(path)) == 1
    browser.refresh()
    enter_viewer()
    assert browser.execute_script('return PDFViewerApplication.viewsManager.isOpen'), 'Manage pages must remain open after reload'
    assert browser.find_element(By.ID, 'viewsManager').is_displayed()
    wait.until(lambda driver: driver.execute_script('return PDFViewerApplication.pdfViewer.currentPageNumber === 3 && !!document.querySelector(\'.page[data-page-number="3"] canvas\')'))
    assert browser.find_element(By.ID, 'pageNumber').get_attribute('value') == '3'
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
        browser.refresh()
        enter_viewer()
        assert browser.find_element(By.ID, 'pageNumber').get_attribute('value') == '2'
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
        browser.execute_script('openFilePreviewPopout(arguments[0],panelNodes.get(arguments[1]))', str(other_path), other)
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
        browser.switch_to.window(main)
        browser.execute_script('closeFilePreviewPopout(arguments[0])', str(path))
        path.write_bytes(preview_pdf(1))
        click_visible_selector(browser, f'[data-pane-tab="file:{path}"]')
        browser.refresh()
        enter_viewer(pages=1)
        assert browser.find_element(By.ID, 'pageNumber').get_attribute('value') == '1'
        browser.switch_to.default_content()
