# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import base64
import shutil
import struct
import sys
from zipfile import ZipFile
from pathlib import Path
from urllib.parse import urlencode

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import load_dockview_runtime_boot_fixture
from tests.browser_helpers.browser_layout import settle_browser_frames
from tests.browser_helpers.browser_console import consume_only_expected_js_debug_api_errors
from tests.helpers.pdf_fixtures import preview_pdf
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401


@pytest.mark.browser
@pytest.mark.parametrize('name,pages,text', [
    ('25-document.docx', 3, 'Office document preview'),
    ('26-slides.pptx', 2, 'Office slides preview'),
    ('24-small.pdf', 1, 'Small PDF preview'),
])
def test_real_office_and_small_pdf_preview(browser, isolated_dev_server_factory, request, tmp_path, name, pages, text):
    if name.endswith(('.docx', '.pptx')) and (sys.platform != 'linux' or not shutil.which('libreoffice') or not shutil.which('bwrap')):
        pytest.skip('real Office containment qualification requires Linux LibreOffice and bubblewrap')
    server = isolated_dev_server_factory('office-preview')
    source = server.paths.home_dir / name
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'examples' / name, source)
    item = 'file:' + str(source)
    request.addfinalizer(lambda: browser.get('about:blank'))
    browser.get(server.base_url + '/?' + urlencode({'sessions': item, 'layout': 'left', 'tabs': 'left:' + item}))
    wait = WebDriverWait(browser, 45)
    readiness = wait.until(lambda driver: driver.execute_script(
        "const p=typeof panelNodes!=='undefined' && panelNodes.get(arguments[0]); if(!p)return null;const ready=p.querySelector('.file-editor-pdf-preview')?.dataset.pdfReady==='true';return ready || p.innerText.includes('Office preview unavailable') ? {ready,text:p.innerText}:null", item))
    assert readiness['ready'], readiness['text']
    result = browser.execute_async_script(r"""
      const done=arguments[arguments.length-1];
      const panel=panelNodes.get(arguments[0]), frame=panel.querySelector('.file-editor-pdf-preview');
      const app=frame.contentWindow.PDFViewerApplication;
      Promise.all(Array.from({length:app.pagesCount},(_,index)=>app.pdfDocument.getPage(index+1)
        .then(page=>page.getTextContent())
        .then(content=>content.items.map(x=>x.str).join(' ')))).then(pageText=>done({
        pages:app.pagesCount,text:pageText.join(' '),
        editControls:[...panel.querySelectorAll('[data-editor-mode="edit"], [data-editor-mode="split"], .file-editor-save-panel')].filter(x=>x.getClientRects().length && !x.hidden).length,
        pdfEditing:app.pdfViewer.annotationEditorMode,
        fallbackVisible:[...panel.querySelectorAll('iframe + .file-editor-preview-fallback')].some(x=>x.getClientRects().length>0),
      })).catch(e=>done({error:String(e)}));
    """, item)
    assert result.get('pages') == pages, result
    assert text in result['text'], result
    assert not result['fallbackVisible'], result
    if name == '25-document.docx':
        assert 'Expected preview' in result['text'] and 'Two slides' in result['text'], result
        with ZipFile(source) as document:
            images = [name for name in document.namelist() if name.startswith('word/media/') and name.endswith('.png')]
            assert len(images) == 2, images
            dimensions = [struct.unpack('>II', document.read(name)[16:24]) for name in images]
            assert all(width <= 320 and height <= 120 for width, height in dimensions), dimensions
    if name.endswith(('.docx', '.pptx')):
        assert result['editControls'] == 0 and result['pdfEditing'] == -1, result
        panel = browser.execute_script("return panelNodes.get(arguments[0])", item)
        download = panel.find_element(By.LINK_TEXT, 'Download original')
        assert '/api/fs/raw?' in download.get_attribute('href')
        browser.execute_cdp_cmd('Browser.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': str(tmp_path)})
        try:
            download.click()
            original = tmp_path / name
            wait.until(lambda _: original.is_file())
            assert original.read_bytes() == source.read_bytes()
        finally:
            browser.execute_cdp_cmd('Browser.setDownloadBehavior', {'behavior': 'deny'})
    if pages > 1:
        frame = browser.execute_script("return panelNodes.get(arguments[0]).querySelector('.file-editor-pdf-preview')", item)
        browser.switch_to.frame(frame)
        try:
            for page_number in range(2, pages + 1):
                browser.find_element(By.ID, 'next').click()
                wait.until(lambda driver: driver.execute_script(
                    "return PDFViewerApplication.page === arguments[0]", page_number))
                expected_page_text = (
                    'Read the second slide' if name == '26-slides.pptx'
                    else 'Expected preview' if page_number == 2
                    else 'End of the Office preview sample.'
                )
                wait.until(lambda driver: driver.execute_script("""
                  return document.querySelector('.page[data-page-number="' + arguments[0] + '"] .textLayer')
                    ?.textContent.includes(arguments[1]);
                """, page_number, expected_page_text))
        finally:
            browser.switch_to.default_content()


@pytest.mark.browser
@pytest.mark.parametrize('scenario', ['width', 'restore'])
def test_office_viewer_in_retained_tabs(browser, isolated_dev_server_factory, request, scenario):
    if sys.platform != 'linux' or not shutil.which('libreoffice') or not shutil.which('bwrap'):
        pytest.skip('real Office containment qualification requires Linux LibreOffice and bubblewrap')
    server = isolated_dev_server_factory('office-layout')
    items = {}
    for name in ('25-document.docx', '26-slides.pptx'):
        path = server.paths.home_dir / name
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'examples' / name, path)
        items[name] = 'file:' + str(path)
    docx, slides = items.values()
    bounds = browser.get_window_rect()
    request.addfinalizer(lambda: browser.set_window_rect(**bounds))
    browser.set_window_size(1400, 900)
    request.addfinalizer(lambda: browser.get('about:blank'))
    browser.get(server.base_url + '/?' + urlencode({
        'sessions': slides, 'layout': 'left', 'tabs': 'left:' + slides,
    }))
    wait = WebDriverWait(browser, 45)

    def viewer_state(item):
        return browser.execute_script("""
          const panel = typeof panelNodes !== 'undefined' && panelNodes.get(arguments[0]);
          const frame = panel?.querySelector('.file-editor-pdf-preview');
          const app = frame?.contentWindow?.PDFViewerApplication;
          if (!frame?.dataset.pdfReady || !frame.getBoundingClientRect().width || !app?.pdfDocument) return null;
          const host = panel.querySelector('[data-office-preview]');
          const box = frame.getBoundingClientRect(), pane = host.parentElement.getBoundingClientRect();
          return {
            width: box.width, top: box.top - pane.top, bottom: pane.bottom - box.bottom,
            sidebar: app.viewsManager.isOpen, pages: app.pagesCount,
            painted: !!frame.contentDocument.querySelector('.page[data-page-number="1"] canvas'),
            readOnly: app.pdfViewer.annotationEditorMode === -1,
          };
        """, item)

    wide = wait.until(lambda _: viewer_state(slides))
    if scenario == 'width':
        assert wide['width'] > 840 and wide['sidebar'], wide
        assert wide['top'] <= 34 and abs(wide['bottom']) <= 1, wide
        # Constrain the pane while keeping the browser viewport unchanged.
        browser.execute_script("panelNodes.get(arguments[0]).style.maxWidth = '780px'", slides)
        settle_browser_frames(browser)
        narrow = wait.until(lambda _: (state := viewer_state(slides)) and state['width'] <= 780 and state)
        assert not narrow['sidebar'], narrow
        assert narrow['top'] <= 34 and abs(narrow['bottom']) <= 1, narrow
        browser.execute_script("panelNodes.get(arguments[0]).style.maxWidth = ''", slides)
        wait.until(lambda _: (state := viewer_state(slides)) and state['width'] > 840 and state['sidebar'])
        return

    assert browser.execute_async_script("""
      const [path, done] = arguments;
      openFileInEditor(path, '25-document.docx').then(done, error => done(String(error)));
    """, docx.removeprefix('file:')) == docx
    wait.until(lambda _: (state := viewer_state(docx)) and state['painted'])
    for item in (slides, docx):
        tab = wait.until(lambda driver: driver.execute_script("""
          return [...document.querySelectorAll('[data-pane-tab]')].find(tab =>
            tab.dataset.paneTab === arguments[0] && tab.getClientRects().length);
        """, item))
        tab.click()
        wait.until(lambda _: (state := viewer_state(item)) and state['painted'])
    browser.execute_script("""
      const frame = panelNodes.get(arguments[0]).querySelector('.file-editor-pdf-preview');
      frame._testPreviousDocument = frame.contentDocument;
      window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}));
      window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
      frame.contentWindow.location.reload();
    """, docx)
    wait.until(lambda driver: driver.execute_script("""
      const frame = panelNodes.get(arguments[0]).querySelector('.file-editor-pdf-preview');
      return frame.contentDocument !== frame._testPreviousDocument;
    """, docx))
    recovered = WebDriverWait(browser, 12).until(lambda _: (state := viewer_state(docx)) and state['painted'] and state)
    assert recovered['pages'] == 3 and recovered['readOnly'], recovered


@pytest.mark.browser
def test_office_preview_retry_retention_refresh_and_retirement(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    WebDriverWait(browser, 15).until(lambda driver: driver.execute_script("return typeof renderOfficePreviewStrategy === 'function' && !!document.getElementById('grid')"))
    result = browser.execute_async_script(r"""
      const done=arguments[arguments.length-1];
      (async()=>{
        const original=window.fetch, requests=[];
        let arrival=null;
        const waitRequest=index=>requests[index] ? Promise.resolve(requests[index]) : new Promise(resolve=>{arrival=resolve;});
        const container=document.createElement('div');container.style.height='500px';document.body.append(container);
        const pdf=Uint8Array.from(atob(arguments[0]),ch=>ch.charCodeAt(0));
        window.fetch=(url,options={})=>{
          if(String(url)!=='/api/fs/office-preview') return original(url,options);
          return new Promise(resolve=>{const request={resolve,signal:options.signal};requests.push(request);if(arrival){arrival(request);arrival=null;}});
        };
        try {
          renderOfficePreviewStrategy({container,path:'/sample.docx',signature:'first'});
          const pending=container._previewAsync;
          const downloadDuring=container.querySelector('a').textContent;
          (await waitRequest(0)).resolve(Response.json({error:'Sandbox refused',instructions:'Install bubblewrap'},{status:503}));
          await pending;
          const failure=container.innerText;
          [...container.querySelectorAll('button')].find(x=>x.textContent==='Retry').click();
          const retry=container._previewAsync;
          (await waitRequest(1)).resolve(new Response(pdf,{headers:{'Content-Type':'application/pdf'}}));await retry;
          const frame=container.querySelector('iframe');
          await window.__yomuxTestWaitFor(()=>frame.dataset.pdfReady==='true');
          const retained=renderOfficePreviewStrategy({container,path:'/sample.docx',signature:'first'})===false && frame===container.querySelector('iframe');
          [...container.querySelectorAll('button')].find(x=>x.textContent==='Reload').click();
          const refresh=container._previewAsync;
          const held=frame===container.querySelector('iframe') && frame._rawFileObjectUrl.startsWith('blob:')
            && Boolean(container.querySelector('.pdf-preview-update-status'));
          (await waitRequest(2)).resolve(Response.json({error:'Refresh refused'},{status:503}));await refresh;
          const keptOnFailure=frame===container.querySelector('iframe') && container.innerText.includes('Refresh refused');
          [...container.querySelectorAll('button')].find(x=>x.textContent==='Retry').click();
          const recovered=container._previewAsync;
          frame.contentDocument.getElementById('pageNumber').focus();
          if (document.activeElement !== frame) throw new Error('fixture failed to focus the old PDF');
          (await waitRequest(3)).resolve(new Response(pdf,{headers:{'Content-Type':'application/pdf'}}));await recovered;
          await window.__yomuxTestWaitFor(()=>frame._rawFileObjectUrl==='' && frame!==container.querySelector('iframe'));
          const released=frame._rawFileObjectUrl==='';
          const nextFrame=container.querySelector('iframe');
          const focusKept=document.activeElement===nextFrame && nextFrame.contentDocument.activeElement.id==='pageNumber';
          const painted=!!nextFrame.contentDocument.querySelector('.page[data-page-number="1"] canvas');
          renderOfficePreviewStrategy({container,path:'/sample.docx',signature:'new-revision'});
          const late=container._previewAsync;
          releasePreviewSurfaceResources(container);container.replaceChildren();
          const aborted=requests[4].signal.aborted;
          (await waitRequest(4)).resolve(new Response(pdf,{headers:{'Content-Type':'application/pdf'}}));await late;
          done({downloadDuring,failure,retained,held,keptOnFailure,released,focusKept,painted,aborted,lateChildren:container.children.length,requests:requests.length});
        } finally {releasePreviewSurfaceResources(container);container.remove();window.fetch=original;}
      })().catch(error=>done({error:String(error),stack:error.stack}));
    """, base64.b64encode(preview_pdf()).decode())
    consume_only_expected_js_debug_api_errors(browser, [{"method": "POST", "path": "/api/fs/office-preview", "status": 503, "ok": False}] * 2)
    assert result.get('downloadDuring') == 'Download original', result
    assert 'Sandbox refused' in result['failure'] and 'Install bubblewrap' in result['failure'], result
    assert result['retained'] and result['held'] and result['keptOnFailure'] and result['released'] and result['focusKept'] and result['painted'] and result['aborted'], result
    assert result['lateChildren'] == 0 and result['requests'] == 5, result
