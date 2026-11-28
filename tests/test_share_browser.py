# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Scoped guest browsers co-edit through the production Go authority."""

import base64
import json
import re
import shlex
import shutil
import sys
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from PIL import Image
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import browser_auth_yaml, new_chrome_driver, remove_browser_test_new_document_scripts
from tests.browser_helpers.browser_console import assert_only_expected_browser_http_error, assert_only_expected_browser_network_error, read_browser_console_log
from tests.browser_helpers.share_workspace import login_owner, _button, _share_json
from tests.browser_helpers.native_keyboard import native_logical_key, native_editor_modifier
from tests.browser_helpers.native_pointer import cdp_drag_hold, cdp_release
from tests.browser_helpers.webdriver_lease import WebDriverLease, retire_all
from tests.helpers.pdf_fixtures import preview_pdf
from tests.tmux_runtime import run_isolated_tmux
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401

pytestmark = pytest.mark.browser
CREATE_SHARE_LABEL = 'Create New Share and Copy URL'


def _assert_chat_sidebar_resize(driver):
    def geometry(browser):
        return browser.execute_script("""
          const handle = [...document.querySelectorAll('.share-sidebar-resizer')]
            .find(node => node.getClientRects().length);
          if (!handle) return null;
          const sidebar = handle.closest('.file-explorer-pane');
          const chat = handle.parentElement;
          const tree = [...sidebar.querySelectorAll(':scope > .file-explorer-tree-panel, :scope > .file-explorer-changes-panel')]
            .find(node => node.getClientRects().length);
          const composer = chat.querySelector('.share-sidebar-chat:not([hidden]) textarea');
          const box = handle.getBoundingClientRect();
          return {x: Math.round(box.x + box.width / 2), y: Math.round(box.y + box.height / 2),
            chat: chat.getBoundingClientRect().height, tree: tree.getBoundingClientRect().height,
            composerVisible: composer.getBoundingClientRect().bottom <= chat.getBoundingClientRect().bottom};
        """)

    before = WebDriverWait(driver, 5).until(geometry)
    start = {'x': before['x'], 'y': before['y']}
    end = {'x': start['x'], 'y': start['y'] - 55}
    try:
        cdp_drag_hold(driver, start, end, steps=4)
        held = geometry(driver)
        assert abs(held['y'] - end['y']) <= 2, held
        assert held['chat'] > before['chat'] + 50 and held['tree'] < before['tree'] - 50, held
        assert held['composerVisible'], held
    finally:
        cdp_release(driver, end)
    assert geometry(driver) == held


def _share_editor(driver):
    return driver.execute_script("const root=[...document.querySelectorAll('.file-editor-panel .cm-editor')].find(node=>node.getClientRects().length);if(!root)return false;const view=YOmuxCodeMirror.EditorView.findFromDOM(root);try{return !view.state.readOnly && YOmuxCodeMirror.getSyncedVersion(view.state)>=0 ? view.contentDOM : false;}catch{return false;}")


def _share_type(driver, text):
    editor = WebDriverWait(driver, 10).until(_share_editor)
    editor.click()
    for char in text:
        native_logical_key(driver, char)

def _guest_ready(driver):
    return driver.execute_script("return typeof shareWorkspace!=='undefined' && shareWorkspace.active && !!shareWorkspace.chat && !location.hash")

def _open_share_manager(driver):
    driver.find_element(By.CSS_SELECTOR, '.app-menu[data-app-menu="file"] > .app-menu-button').click()
    assert not driver.find_elements(By.CSS_SELECTOR, '.app-menu[data-app-menu="share"]')
    WebDriverWait(driver, 5).until(lambda d: _button(d, '.app-menu[data-app-menu="file"]', 'YO!Share')).click()


def test_share_markdown_renders_referenced_images(browser, isolated_dev_server_factory, tmp_path):
    server = isolated_dev_server_factory('share-markdown-images', tls=True, auth_bypass=False)
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    (server.paths.config_dir / 'settings.yaml').write_bytes(b'general:\n  startup_tips: false\nupdates:\n  notify_level: none\n')
    source = server.paths.workspace_dir / '2026.md'
    source.write_text(
        '# Shared notes\n\n![Overview][diagram]\n\n<img src="images/detail.png" alt="Detail">\n\n'
        '[diagram]: images/overview%20chart.png\n', encoding='utf-8',
    )
    images = source.parent / 'images'
    images.mkdir()
    Image.new('RGB', (43, 29), '#004faa').save(images / 'overview chart.png')
    Image.new('RGB', (57, 38), '#a0003a').save(images / 'detail.png')
    login_owner(browser, server)
    wait = WebDriverWait(browser, 15)
    wait.until(lambda d: d.execute_script("return typeof openFileInEditor==='function' && !!window.YOmuxShare"))
    browser.execute_async_script("""
      openFileInEditor(arguments[0], '2026.md', {viewMode:'edit', userInitiated:true}).then(arguments[1]);
    """, str(source))
    _open_share_manager(browser)
    wait.until(lambda d: _button(d, '.share-admin', CREATE_SHARE_LABEL)).click()
    invite = wait.until(lambda d: d.execute_script("return document.querySelector('.share-invite input')?.value"))

    guest = new_chrome_driver(profile_dir=tmp_path / 'markdown-image-guest')
    lease = WebDriverLease.from_driver(guest)
    try:
        guest.get(invite)
        WebDriverWait(guest, 15).until(_guest_ready)
        sample = """
          const previews = [...document.querySelectorAll('.file-editor-preview-pane-panel')];
          return {assets: [...shareWorkspace.resources.values()].map(resource => Object.keys(resource.assets || {})),
            previews: previews.map(preview => ({
              text: preview.innerText,
              images: [...preview.querySelectorAll('img')].map(image => [image.alt, image.naturalWidth]),
            }))};
        """
        def decoded(driver):
            state = driver.execute_script(sample)
            return any(preview['images'] == [['Overview', 43], ['Detail', 57]] for preview in state['previews'])

        try:
            WebDriverWait(guest, 10).until(decoded)
        except TimeoutException as error:
            raise AssertionError(guest.execute_script(sample)) from error
        assert guest.execute_script("return [...shareWorkspace.resources.values()].every(resource => !resource.label.endsWith('.png'))")
    finally:
        try:
            guest.get('about:blank')
        finally:
            retired = retire_all([lease])
            assert retired['proven_gone'], retired
            browser.get('about:blank')


@pytest.mark.skipif(sys.platform != 'linux' or not shutil.which('bwrap'), reason='requires Linux TeX sandbox')
def test_share_tex_publishes_pdf_and_save_status_survives_redraw(browser, isolated_dev_server_factory, tmp_path, request):
    server = isolated_dev_server_factory('share-tex', tls=True, auth_bypass=False)
    request.addfinalizer(lambda: browser.get('about:blank'))
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    (server.paths.config_dir / 'settings.yaml').write_bytes(b'general:\n  startup_tips: false\nupdates:\n  notify_level: none\n')
    source = server.paths.workspace_dir / 'shared.tex'
    source.write_text('Shared TeX fixture\n', encoding='utf-8')
    login_owner(browser, server)
    wait = WebDriverWait(browser, 15)
    wait.until(lambda d: d.execute_script("return typeof openFileInEditor==='function' && !!window.YOmuxShare"))
    compiler = Path(_share_json(browser, '/api/tex/compiler')['body']['install_path'])
    assert compiler.is_relative_to(server.paths.config_dir)
    compiler.parent.mkdir(parents=True, exist_ok=True)
    encoded = base64.b64encode(preview_pdf()).decode('ascii')
    edited_pdf = base64.b64encode(preview_pdf(page_count=2)).decode('ascii')
    sibling = source.with_name('private-sibling.dat')
    sibling.write_bytes(b'Outside the collaborative TeX document')
    compiler.write_bytes(f"""#!/bin/sh
source=$(/bin/cat)
case "$source" in
  *Z*) test ! -r {shlex.quote(str(sibling))} || exit 31; pdf={edited_pdf} ;;
  *) pdf={encoded} ;;
esac
printf %s "$pdf" | /usr/bin/base64 -d > "$3/texput.pdf"
""".encode())
    compiler.chmod(0o700)
    item = browser.execute_async_script("""
      openFileInEditor(arguments[0], 'shared.tex', {viewMode:'edit', userInitiated:true})
        .then(arguments[1]);
    """, str(source))
    editor = wait.until(lambda d: d.execute_script("return panelNodes.get(arguments[0])?._cmView?.contentDOM", item))
    editor.click()
    native_logical_key(browser, 'x')
    wait.until(lambda d: d.execute_script("""
      const panel = panelNodes.get(arguments[0]);
      return !panel.querySelector('.file-editor-save-panel').disabled;
    """, item))
    browser.execute_script("return panelNodes.get(arguments[0]).querySelector('.file-editor-save-panel')", item).click()
    saved = wait.until(lambda d: d.execute_script("""
      const panel = panelNodes.get(arguments[0]);
      const status = panel.querySelector('.file-editor-status-message').textContent;
      return status.startsWith('saved (') && status;
    """, item))
    assert browser.execute_script("""
      const item = arguments[0], panel = panelNodes.get(item);
      renderFileEditorPanel(panel, item);
      return panel.querySelector('.file-editor-status-message').textContent;
    """, item) == saved
    browser.execute_script("""
      const originalFetch = window.fetch;
      window.fetch = async (input, options) => {
        if (input !== '/api/tex/preview') return originalFetch(input, options);
        const body = JSON.parse(options.body);
        if (!body.publish) return originalFetch(input, options);
        window.__shareTexRequest = body;
        await new Promise(resolve => { window.__releaseShareTex = resolve; });
        return originalFetch(input, options);
      };
      window.addEventListener('pagehide', () => { window.fetch = originalFetch; }, {once:true});
    """)
    _open_share_manager(browser)
    wait.until(lambda d: d.find_element(By.CSS_SELECTOR, '.share-admin input[data-share-resource$="shared.tex"]')).click()
    wait.until(lambda d: _button(d, '.share-admin', CREATE_SHARE_LABEL)).click()
    invite = wait.until(lambda d: d.execute_script("return document.querySelector('.share-invite input')?.value"))
    guest = new_chrome_driver(profile_dir=tmp_path / 'tex-guest')
    lease = WebDriverLease.from_driver(guest)
    try:
        guest.get(invite)
        WebDriverWait(guest, 20).until(lambda d: d.execute_script("""
          return document.querySelector('.file-editor-preview-pane-panel .file-editor-code-preview')
            ?.textContent.includes('TeX fixture');
        """))
        body = wait.until(lambda d: d.execute_script("return window.__shareTexRequest"))
        assert body == {'path': str(source), 'content': source.read_text(encoding='utf-8'), 'publish': True}
        browser.execute_script("window.__releaseShareTex()")
        def published_pdf(driver, pages):
            url = WebDriverWait(driver, 20).until(lambda d: d.execute_script("""
              const frame = [...document.querySelectorAll('.file-editor-preview-pane-panel iframe[data-pdf-ready="true"]')]
                .find(node => node.getClientRects().length && node.contentWindow.PDFViewerApplication.pagesCount === arguments[0]
                  && node.contentDocument.querySelector('.page canvas')?.width > 0);
              return frame && new URL(frame.src).searchParams.get('file');
            """, pages))
            return driver.execute_async_script("""
              const [url, done] = arguments;
              fetch(url).then(async response => done({status: response.status,
                pdf: btoa(String.fromCharCode(...new Uint8Array(await response.arrayBuffer())))}))
                .catch(error => done({error: String(error)}));
            """, url)

        assert published_pdf(guest, 3) == {'status': 200, 'pdf': encoded}
        browser.execute_async_script("""
          openFileInEditor(arguments[0], 'shared.tex', {viewMode:'split', userInitiated:true})
            .then(arguments[1]);
        """, str(source))
        assert published_pdf(browser, 3) == {'status': 200, 'pdf': encoded}
        _share_mode(guest, 'edit')
        _share_type(guest, 'Z')
        WebDriverWait(guest, 10).until(lambda _: 'Z' in source.read_text(encoding='utf-8'))
        assert published_pdf(browser, 2) == {'status': 200, 'pdf': edited_pdf}
        _share_mode(guest, 'preview')
        assert published_pdf(guest, 2) == {'status': 200, 'pdf': edited_pdf}
        _open_share_manager(browser)
        grant = _share_json(browser, '/api/share')['body']['shares'][0]
        detail = _expand_share(browser, grant['guest']).find_element(By.CSS_SELECTOR, '.share-manager-detail')
        revision = detail.get_dom_attribute('data-key')
        added = source.with_name('opened-after-share.md')
        added.write_text('# Opened later\n', encoding='utf-8')
        browser.execute_async_script("""
          openFileInEditor(arguments[0], 'opened-after-share.md', {viewMode:'edit', userInitiated:true})
            .then(arguments[1]);
        """, str(added))
        wait.until(lambda d: d.execute_script("""
          const row = [...arguments[0].querySelectorAll('.share-admin-picker label')]
            .find(label => label.firstElementChild.textContent.endsWith('opened-after-share.md'));
          return Boolean(row) && !row.control.checked;
        """, detail))
        assert detail.get_dom_attribute('data-key') == revision
        assert not _guest_tab_row(guest, added.name)
        _open_share_manager(browser)
        _set_share_tab(browser, grant['guest'], added.name, True)
        WebDriverWait(guest, 10).until(lambda d: _guest_tab_row(d, added.name))
        browser.execute_async_script("""
          removeOpenFile(arguments[0], {confirmDirty:false}).then(arguments[1]);
        """, str(added))
        assert _guest_tab_row(guest, added.name)
        _set_share_tab(browser, grant['guest'], added.name, False, expect_removed=True)
        WebDriverWait(guest, 10).until_not(lambda d: _guest_tab_row(d, added.name))
        wait.until(lambda d: d.execute_script("""
          const detail = document.querySelector('.share-manager-disclosure[open] .share-manager-detail');
          return !!detail && !detail.textContent.includes('opened-after-share.md');
        """))
    finally:
        retire_all([lease])


def test_share_picker_removes_deleted_background_file(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('share-deleted', tls=True, auth_bypass=False)
    request.addfinalizer(lambda: browser.get('about:blank'))
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    (server.paths.config_dir / 'settings.yaml').write_bytes(b'general:\n  startup_tips: false\nupdates:\n  notify_level: none\n')
    source = server.paths.workspace_dir / 'sample.text'
    source.write_bytes(b'The editor retains this text after deletion.\n')
    login_owner(browser, server)
    wait = WebDriverWait(browser, 15)
    wait.until(lambda d: d.execute_script("return typeof openFileInEditor==='function' && !!window.YOmuxShare"))
    item = browser.execute_async_script("""
      openFileInEditor(arguments[0], 'sample.text', {viewMode:'edit', userInitiated:true})
        .then(arguments[1]);
    """, str(source))
    assert item
    _open_share_manager(browser)
    wait.until(lambda d: d.execute_script("""
      const [path, item] = arguments;
      const picker = document.querySelector('.share-create-disclosure[open] .share-admin-picker');
      const watch = serverWatchRootsState;
      return picker?.textContent.includes('sample.text') && watch.registered &&
        JSON.parse(watch.signature).files.includes(path) &&
        clientEventTransportState.connected && clientEventDemandDescriptor().channels.includes('files') &&
        !itemIsActivePaneTab(item);
    """, str(source), item))
    source.unlink()
    wait.until(lambda d: d.execute_script("""
      const state = fileState.get(arguments[0]);
      const picker = document.querySelector('.share-create-disclosure[open] .share-admin-picker');
      return state?.externalMissing && state.content.includes('retains this text') &&
        !!picker && !picker.textContent.includes('sample.text');
    """, str(source)))
    assert_only_expected_browser_http_error(browser, path='/api/fs/read', status=404, query={'path': str(source)})


def test_share_picker_retains_owner_path_after_grant(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('share-owner-path', tls=True, auth_bypass=False)
    request.addfinalizer(lambda: browser.get('about:blank'))
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    source = server.paths.workspace_dir / 'sample.txt'
    source.write_bytes(b'sample\n')
    extra = server.paths.workspace_dir / 'extra.txt'
    extra.write_bytes(b'extra\n')
    login_owner(browser, server)
    wait = WebDriverWait(browser, 15)
    wait.until(lambda driver: driver.execute_script("return typeof openFileInEditor === 'function' && !!window.YOmuxShare"))
    browser.execute_async_script("""
      const [source, extra, done] = arguments;
      (async () => {
        await openFileInEditor(source, 'sample.txt', {viewMode: 'edit', userInitiated: true});
        await openFileInEditor(extra, 'extra.txt', {viewMode: 'edit', userInitiated: true});
      })().then(done);
    """, str(source), str(extra))
    _open_share_manager(browser)
    selector = '.share-admin-picker tbody label'
    wait.until(lambda driver: any(row.text == str(source) for row in driver.find_elements(By.CSS_SELECTOR, selector)))
    draft_picker = '.share-create-disclosure[open]'
    active_picker = '.share-manager-disclosure[open]'

    def file_color(picker):
        return browser.execute_script("""
          const write = [...document.querySelectorAll(arguments[0] + ' input[data-share-resource]')]
            .find(input => input.dataset.shareResource === arguments[1]);
          const label = write.closest('tr').querySelector('label');
          return {description: label.textContent, name: getComputedStyle(label).color,
            normal: getComputedStyle(label.parentElement).color};
        """, picker, str(source))

    def check_color(picker, writable, guest=None):
        colors = file_color(picker)
        assert colors['description'] == (f'{source} (writable to {guest})' if writable else str(source))
        if writable:
            red, green, blue = (int(channel) for channel in colors['name'][4:-1].split(','))
            assert colors['name'] != colors['normal'] and red > max(green, blue), colors
        else:
            assert colors['name'] == colors['normal'], colors

    headers = browser.find_elements(By.CSS_SELECTOR, f'{draft_picker} .share-permission-table thead th')
    assert [header.text for header in headers] == ['R', 'W', '']
    assert [header.get_dom_attribute('aria-label') for header in headers[:2]] == ['Read access', 'Writable']
    draft_write = browser.find_element(By.CSS_SELECTOR, f'{draft_picker} input[data-share-resource="{source}"]')
    draft_selection = draft_write.find_element(By.XPATH, './ancestor::tr/td[1]/input')
    geometry = browser.execute_script("""
      const row = arguments[0].closest('tr');
      const picker = row.closest('.share-admin-picker');
      const original = picker.style.cssText;
      picker.style.minInlineSize = '910px';
      try {
        return {alignment: getComputedStyle(row.cells[2]).textAlign,
          widths: [14, 20].map(fontSize => {
            picker.style.fontSize = fontSize + 'px';
            return [...row.cells].map(cell => cell.getBoundingClientRect().width / fontSize);
          })};
      } finally {
        picker.style.cssText = original;
      }
    """, draft_selection)
    assert geometry['alignment'] == 'start', geometry
    assert all(read == pytest.approx(1.5, abs=.1) and write == pytest.approx(1.5, abs=.1) and description > 40
               for read, write, description in geometry['widths']), geometry
    guest_name = browser.find_element(By.CSS_SELECTOR, f'{draft_picker} input[name=guest]')
    draft_guest = guest_name.get_property('value')
    for theme in ('dark', 'light'):
        browser.execute_script("globalThemeMode = arguments[0]; applyGlobalThemeMode();", theme)
        check_color(draft_picker, False)
        draft_write.click()
        check_color(draft_picker, True, draft_guest)
        if theme == 'dark':
            guest_name.clear()
            guest_name.send_keys('RenamedGuest')
            draft_guest = 'RenamedGuest'
            check_color(draft_picker, True, draft_guest)
        draft_selection.click()
        check_color(draft_picker, False)
        assert not draft_write.is_selected()
        draft_write.click()
        assert draft_selection.is_selected()
        check_color(draft_picker, True, draft_guest)
        draft_write.click()
        assert draft_selection.is_selected()
    created = _share_json(browser, '/api/share', {'guest': 'PathGuest', 'minutes': 5, 'limit': 1,
        'resources': [{'kind': 'file', 'path': str(source), 'writable': False}]})
    assert created['status'] == 201
    assert [resource['label'] for resource in created['body']['share']['resources']] == [source.name]
    _open_share_manager(browser)
    active = _expand_share(browser, 'PathGuest')
    rows = active.find_elements(By.CSS_SELECTOR, selector)
    selected = [row.text for row in rows if browser.execute_script('return arguments[0].control.checked', row)]
    assert selected == [str(source)]
    for theme in ('light', 'dark'):
        browser.execute_script("globalThemeMode = arguments[0]; applyGlobalThemeMode();", theme)
        check_color(active_picker, False)
        _toggle_host_permission(browser, str(source))
        check_color(active_picker, True, 'PathGuest')
        _toggle_host_permission(browser, str(source))
        check_color(active_picker, False)
    _toggle_host_permission(browser, str(extra))
    resources = _share_json(browser, '/api/share')['body']['shares'][0]['resources']
    assert {resource['label']: resource['writable'] for resource in resources} == {
        'sample.txt': False, 'extra.txt': True,
    }
    _toggle_host_permission(browser, str(extra))
    resources = _share_json(browser, '/api/share')['body']['shares'][0]['resources']
    assert {resource['label']: resource['writable'] for resource in resources} == {
        'sample.txt': False, 'extra.txt': False,
    }


def _guest_tab_row(driver, label=None, resource_id=None):
    return driver.execute_script("""
      const resource=[...shareWorkspace.resources.values()].find(resource=>arguments[1] ? resource.id===arguments[1] : resource.label===arguments[0]);
      if(!resource)return false;
      const item=fileEditorItemFor(shareWorkspacePath(resource));
      return [...document.querySelectorAll('.file-explorer-tabber .file-tree-row')].find(row=>
        (row.dataset.tabberItem || row.dataset.tabberPaneItem)===item) || false;
    """, label, resource_id)


def _select_guest_tab(driver, label=None, resource_id=None):
    current = driver.execute_script("""
      const resource=[...shareWorkspace.resources.values()].find(resource=>arguments[1] ? resource.id===arguments[1] : resource.label===arguments[0]);
      if(!resource)return false;
      const item=fileEditorItemFor(shareWorkspacePath(resource));
      if(itemInLayout(item) && itemIsActivePaneTab(item))return {active:true};
      return [...document.querySelectorAll('.file-explorer-tabber .file-tree-row')].find(row=>
        (row.dataset.tabberItem || row.dataset.tabberPaneItem)===item) || false;
    """, label, resource_id)
    assert current, (label, resource_id)
    if not isinstance(current, dict):
        current.click()


def _share_mode(driver, mode):
    WebDriverWait(driver, 10).until(lambda d: d.execute_script("""
      return [...document.querySelectorAll('.file-editor-panel [data-editor-mode]')].find(node=>
        node.dataset.editorMode===arguments[0] && node.getClientRects().length) || false;
    """, mode)).click()


def _expand_share(driver, label):
    row = WebDriverWait(driver, 10).until(lambda d: d.execute_script("""
      return [...document.querySelectorAll('.share-manager-disclosure')].find(row=>row.querySelector('[data-share-name]').textContent===arguments[0]) || false;
    """, label))
    if not row.get_attribute('open'):
        row.find_element(By.CSS_SELECTOR, '[data-share-name]').click()
    return row


def _decide_terminal_write(driver, action):
    selector = '.share-terminal-write-confirmation [role=dialog]'
    dialog = WebDriverWait(driver, 5).until(lambda d: d.find_element(By.CSS_SELECTOR, selector))
    assert dialog.get_attribute('aria-label') == 'Allow writable terminal access?'
    assert 'full permissions of the operating system account' in dialog.text
    assert 'read, change, or delete' in dialog.text
    assert driver.switch_to.active_element.get_attribute('data-dialog-action') == 'cancel'
    dialog.find_element(By.CSS_SELECTOR, f'[data-dialog-action="{action}"]').click()
    assert not driver.find_elements(By.CSS_SELECTOR, selector)


def _share_screenshot_masks(driver, region, selector):
    style = driver.execute_script("""
      const style = document.createElement('style');
      style.textContent = arguments[0] + '::before { animation: none !important; }';
      document.head.append(style);
      return style;
    """, selector)
    try:
        visible = Image.open(BytesIO(driver.get_screenshot_as_png())).convert('RGB').crop(region)
        driver.execute_script("""
          arguments[0].textContent += arguments[1] + ' { visibility: hidden !important; }';
        """, style, selector)
        hidden = Image.open(BytesIO(driver.get_screenshot_as_png())).convert('RGB').crop(region)
    finally:
        driver.execute_script("arguments[0].remove()", style)
    return visible, hidden


def _clipboard_has_url(driver, url):
    return driver.execute_async_script("""
      const [expected, done] = arguments;
      navigator.clipboard.readText().then(text => done(text === expected), () => done(false));
    """, url)


def _toggle_host_permission(driver, label):
    selector = f'.share-manager-disclosure[open] input[data-share-resource="{label}"]'
    transition = driver.execute_script("""
      const checkbox = document.querySelector(arguments[0]);
      const detail = checkbox.closest('.share-manager-detail');
      return {checkbox, previous:checkbox.checked, revision:JSON.parse(detail.dataset.key)[0]};
    """, selector)
    transition['checkbox'].click()
    assert not driver.find_elements(By.CSS_SELECTOR, '.share-terminal-write-confirmation')
    WebDriverWait(driver, 10).until(lambda d: d.execute_script("""
      const checkbox = document.querySelector(arguments[1]);
      const detail = checkbox?.closest('.share-manager-detail');
      return detail && Number(JSON.parse(detail.dataset.key)[0]) > arguments[0] &&
        checkbox.checked !== arguments[2];
    """, transition['revision'], selector, transition['previous']))


def test_share_creation_clipboard_and_terminal_confirmation(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('share-terminal-confirm', tls=True, auth_bypass=False, session_count=2)
    request.addfinalizer(lambda: browser.get('about:blank'))
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    (server.paths.config_dir / 'settings.yaml').write_bytes(b'general:\n  startup_tips: false\nupdates:\n  notify_level: none\n')
    login_owner(browser, server)
    wait = WebDriverWait(browser, 15)
    wait.until(lambda d: d.execute_script("return !!window.YOmuxShare"))
    _open_share_manager(browser)
    draft = '.share-create-disclosure[open] .share-admin'

    def terminal_rows(driver):
        rows = driver.find_elements(By.CSS_SELECTOR, f'{draft} tbody tr')
        return rows if len(rows) == 2 else False

    for row in wait.until(terminal_rows):
        row.find_element(By.CSS_SELECTOR, 'td:first-child input').click()
    name = browser.find_element(By.CSS_SELECTOR, f'{draft} input[name=guest]').get_property('value')
    browser.execute_script("""
      window.__shareTestExec = document.execCommand;
      document.execCommand = () => false;
      const refused = () => Promise.reject(new DOMException('Permission denied', 'NotAllowedError'));
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true, value: {write: refused, writeText: refused},
      });
    """)
    try:
        _button(browser, draft, CREATE_SHARE_LABEL).click()
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, '.share-manager-disclosure'))
        wait.until(lambda d: 'copy failed' in d.find_element(By.CSS_SELECTOR, '.share-manager-body > [role=status]').text)
        assert browser.find_element(By.CSS_SELECTOR, '.share-invite input').get_property('value').startswith('https://')
        assert not browser.find_elements(By.CSS_SELECTOR, draft)
    finally:
        browser.execute_script("delete navigator.clipboard; document.execCommand = window.__shareTestExec; delete window.__shareTestExec;")
    [grant] = _share_json(browser, '/api/share')['body']['shares']
    first, second = [resource['label'] for resource in grant['resources']]
    _expand_share(browser, name)
    selector = f'.share-manager-disclosure[open] input[data-share-resource="{first}"]'
    browser.find_element(By.CSS_SELECTOR, selector).click()
    _decide_terminal_write(browser, 'cancel')
    assert not browser.find_element(By.CSS_SELECTOR, selector).is_selected()
    browser.find_element(By.CSS_SELECTOR, selector).click()
    _decide_terminal_write(browser, 'allow')
    wait.until(lambda d: _share_json(d, '/api/share')['body']['shares'][0].get('terminal_write_confirmed') is True)
    browser.refresh()
    wait.until(lambda d: d.execute_script("return !!window.YOmuxShare"))
    _open_share_manager(browser)
    wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, '.share-manager-disclosure'))
    _expand_share(browser, name)
    assert browser.find_element(By.CSS_SELECTOR, selector).is_selected()
    _toggle_host_permission(browser, second)
    _toggle_host_permission(browser, first)
    _toggle_host_permission(browser, second)
    _toggle_host_permission(browser, first)
    browser.find_element(By.CSS_SELECTOR, '.share-create-disclosure > summary').click()
    new_terminal = wait.until(terminal_rows)[0]
    new_selection = new_terminal.find_element(By.CSS_SELECTOR, 'td:first-child input')
    new_write = new_terminal.find_element(By.CSS_SELECTOR, 'input[data-share-resource]')
    new_write.click()
    _decide_terminal_write(browser, 'cancel')
    assert not new_selection.is_selected() and not new_write.is_selected()
    new_write.click()
    _decide_terminal_write(browser, 'allow')
    assert new_selection.is_selected() and new_write.is_selected()
    terminal_label = new_terminal.find_element(By.TAG_NAME, 'label')
    draft_name = browser.find_element(By.CSS_SELECTOR, f'{draft} input[name=guest]').get_property('value')
    assert terminal_label.text == f'{new_write.get_dom_attribute("data-share-resource")} (executable by {draft_name})'
    assert terminal_label.value_of_css_property('color') != new_terminal.value_of_css_property('color')
    new_write.click()
    assert new_selection.is_selected() and not new_write.is_selected()
    assert terminal_label.text == new_write.get_dom_attribute('data-share-resource')
    new_name = 'ClipboardGuest'
    name_field = browser.find_element(By.CSS_SELECTOR, f'{draft} input[name=guest]')
    name_field.clear()
    name_field.send_keys(new_name)
    browser.execute_cdp_cmd('Browser.grantPermissions', {
        'origin': server.base_url, 'permissions': ['clipboardReadWrite', 'clipboardSanitizedWrite'],
    })
    try:
        _button(browser, draft, CREATE_SHARE_LABEL).click()
        invite = wait.until(lambda d: d.execute_script("""
          const row = [...document.querySelectorAll('.share-manager-disclosure')].find(
            candidate => candidate.querySelector('[data-share-name]').textContent === arguments[0]);
          return row && !row.open && row.querySelector('.share-invite input')?.value;
        """, new_name))
        wait.until(lambda d: _clipboard_has_url(d, invite))
    finally:
        browser.execute_cdp_cmd('Browser.resetPermissions', {})

def _set_share_tab(driver, share, filename, checked, *, expect_removed=False):
    row = _expand_share(driver, share)
    snapshot = """
      const detail = arguments[0].querySelector('.share-manager-detail');
      const label = [...detail.querySelectorAll('.share-admin-picker tbody label')]
        .find(label => label.firstElementChild.textContent.split('/').at(-1) === arguments[1]);
      const input = label?.control;
      return {input: input ?? null, checked: input?.checked, disabled: input?.disabled,
        revision: JSON.parse(detail.dataset.key)[0]};
    """
    before = driver.execute_script(snapshot, row, filename)
    assert before['input'] and before['checked'] != checked and not before['disabled'], before
    before['input'].click()

    def settled(browser):
        state = browser.execute_script(snapshot, row, filename)
        if state['revision'] <= before['revision']:
            return False
        if expect_removed:
            return state['input'] is None
        return state['input'] and not state['disabled'] and state['checked'] == checked

    WebDriverWait(driver, 10).until(settled, f'{filename} checkbox did not settle at {checked}')


def _markdown_preview_contrast(driver):
    return driver.execute_script("""
      const preview = document.querySelector('.file-editor-preview-pane-panel');
      const canvas = document.createElement('canvas');
      const sample = canvas.getContext('2d', {willReadFrequently: true});
      function luminance(color) {
        sample.fillStyle = color;
        sample.fillRect(0, 0, 1, 1);
        const [r,g,b] = [...sample.getImageData(0,0,1,1).data].map(value => {
          const x = value / 255;
          return x <= .04045 ? x / 12.92 : Math.pow((x + .055) / 1.055, 2.4);
        });
        return .2126*r + .7152*g + .0722*b;
      }
      const background = luminance(getComputedStyle(preview).backgroundColor);
      return [preview.querySelector('h1'), preview.querySelector('p')].map(node => {
        const text = luminance(getComputedStyle(node).color);
        return (Math.max(text, background) + .05) / (Math.min(text, background) + .05);
      });
    """)


def test_scoped_guests_coedit_chat_reconnect_switch_and_recover(browser, isolated_dev_server_factory, tmp_path):
    server = isolated_dev_server_factory('share-browser', tls=True, auth_bypass=False, session_count=2)
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    (server.paths.config_dir / 'settings.yaml').write_bytes(
        b'general:\n  startup_tips: false\nupdates:\n  notify_level: none\n'
        b'appearance:\n  theme: dark\n  active_color: purple\n')
    path = server.paths.workspace_dir / 'shared.md'
    other = server.paths.workspace_dir / 'other.dot'
    path.write_text('# shared\n', encoding='utf-8')
    other.write_text('digraph { other -> resource }\n', encoding='utf-8')
    guests, leases = [], []
    try:
        login_owner(browser, server)
        WebDriverWait(browser, 15).until(lambda d: d.execute_script("return typeof openFileInEditor==='function' && !!window.YOmuxShare"))
        opened = browser.execute_async_script("""
          const [path,other,done]=arguments;
          (async()=>{await openFileInEditor(other,'other.dot',{viewMode:'edit',userInitiated:true});const item=await openFileInEditor(path,'shared.md',{viewMode:'preview',userInitiated:true});await openFileEditorPane(path,{item});done({item});})().catch(error=>done({error:String(error.stack)}));
        """, str(path), str(other))
        assert 'error' not in opened, opened
        WebDriverWait(browser, 10).until(lambda d: d.execute_script("return !!panelNodes.get(arguments[0])?._pmView", opened['item']))
        WebDriverWait(browser, 10).until(lambda d: d.execute_script(r"""
          const records = transcriptMetadataState.payload.sessions;
          return arguments[0].every(session => /^%\d+$/.test(records?.[session]?.selected_pane?.pane_id));
        """, server.tmux.sessions))
        browser.execute_script("""
          window.__sharePickerCandidates = sharePickerCandidates;
          sharePickerCandidates = (...args) => window.__sharePickerCandidates(...args)
            .filter(choice => choice.resource.kind !== 'terminal');
        """)
        _open_share_manager(browser)
        WebDriverWait(browser, 10).until(lambda d: d.execute_script("return document.querySelector('.share-manager-active').hidden && document.querySelector('.share-manager-recovery').hidden"))
        new_share = browser.find_element(By.CSS_SELECTOR, '.share-create-disclosure > summary')
        assert new_share.text == 'New Share'
        WebDriverWait(browser, 5).until(lambda d: d.execute_script("""
          const form = document.querySelector('.share-create-disclosure[open] .share-admin');
          return !!form?.getClientRects().length;
        """))
        duration = browser.find_element(By.CSS_SELECTOR, '.share-admin input[name=minutes]')
        duration.clear()
        duration.send_keys('31')
        selection = browser.find_element(By.CSS_SELECTOR, '.share-admin tbody td:first-child input')
        selection.click()
        picker = r"""
          const rows = [...document.querySelectorAll('.share-admin tbody tr')];
          return rows.filter(row => / %\d+$/.test(row.textContent)).length;
        """
        assert browser.execute_script(picker) == 0
        browser.execute_script("sharePickerCandidates = window.__sharePickerCandidates")
        WebDriverWait(browser, 5).until(lambda d: d.execute_script(picker) == 2)
        assert duration.get_property('value') == '31' and not selection.is_selected()
        duration.clear()
        duration.send_keys('30')
        selection.click()
        # Hold host discovery so the first guest edit precedes host collaboration admission.
        browser.execute_script("""
          window.__shareAdminHeld=[];window.__shareHoldAdmin=true;window.__shareOriginalRequest=YOmuxShare.request;
          YOmuxShare.request=(url,options)=>String(url)==='/api/share' && !options && window.__shareHoldAdmin ? new Promise((resolve,reject)=>window.__shareAdminHeld.push(()=>window.__shareOriginalRequest(url,options).then(resolve,reject))) : window.__shareOriginalRequest(url,options);
        """)
        browser.execute_script("""
          window.__shareAPIRequestErrors=[];const shareRequest=YOmuxShare.request;
          YOmuxShare.request=async(url,options)=>{
            try{return await shareRequest(url,options);}
            catch(error){if(error.status>=400)window.__shareAPIRequestErrors.push({url:String(url),status:error.status,message:error.message,body:options?.body||''});throw error;}
          };
          window.__sharePatchResults=[];window.__shareManagerResults=[];window.__retiredShareDocuments=[];window.__shareFetch=window.fetch;
          window.fetch=async(url,options)=>{
            const parsed=new URL(url,location.href);
            const response=await window.__shareFetch(url,options);
            if(parsed.pathname==='/api/share' && parsed.searchParams.has('id')) {
              window.__shareManagerResults.push({method:options?.method||'GET',status:response.status,body:options?.body||'',result:await response.clone().text()});
            }
            if(options?.method==='PATCH' && parsed.pathname==='/api/share' && parsed.searchParams.has('id'))
              window.__sharePatchResults.push({status:response.status,body:options.body,result:await response.clone().text()});
            if (parsed.pathname === '/api/share/document' && parsed.searchParams.has('session') &&
                response.status === 409 && (await response.clone().json()).error === 'document_session_changed') {
              window.__retiredShareDocuments.push(parsed.href);
            }
            return response;
          };
        """)
        default_name = WebDriverWait(browser, 5).until(lambda d: d.find_element(By.CSS_SELECTOR, '.share-admin input[name="guest"]')).get_attribute('value')
        assert re.fullmatch(r'Guest\d{4}', default_name)
        geometry = browser.execute_script("""
          const dialog = document.querySelector('.share-admin');
          const button = dialog.querySelector('.file-editor-dialog-actions button');
          const box = node => node.getBoundingClientRect();
          const picker = dialog.querySelector('.share-admin-picker');
          const scrollOwners = [];
          for (let node = picker; node; node = node.parentElement) {
            if (['auto', 'scroll'].includes(getComputedStyle(node).overflowY)) scrollOwners.push(node.className);
            if (node.classList.contains('share-manager-body')) break;
          }
          const cells = [...dialog.querySelectorAll('tbody input[data-share-resource]')];
          const style = getComputedStyle(button);
          return {disclosure:dialog.parentElement.localName, action:button.textContent,
            actionBelow:box(button).top >= box(picker).bottom,
            styles:[style.borderTopStyle,style.backgroundColor,style.boxShadow,parseFloat(style.minHeight)], dropdowns:dialog.querySelectorAll('select').length,
            scrollOwners, pickerScrollHeight:picker.scrollHeight, pickerHeight:picker.clientHeight,
            rows:cells.map(node=>({label:node.dataset.shareResource, right:box(node).right,
              selectionRight:box(node.closest('tr').cells[0].querySelector('input')).right, checked:node.checked})),
            aligned:cells.length>=4 && cells.every(node=>Math.abs(box(node).right-box(cells[0]).right)<1 &&
              box(node).left>box(node.closest('tr').cells[0].querySelector('input')).right && !node.checked),
            defaultRead:dialog.querySelectorAll('tbody tr td:first-child input:checked').length>=2};
        """)
        assert geometry['disclosure'] == 'details' and geometry['action'] == CREATE_SHARE_LABEL and geometry['actionBelow'], geometry
        assert geometry['styles'][:3] == ['solid', 'rgba(0, 0, 0, 0)', 'none'] and geometry['styles'][3] >= 26, geometry
        assert len(geometry['scrollOwners']) == 1 and 'share-manager-body' in geometry['scrollOwners'][0], geometry
        assert geometry['pickerScrollHeight'] <= geometry['pickerHeight'] + 2, geometry
        assert geometry['aligned'] and geometry['dropdowns'] == 0 and geometry['defaultRead'], geometry
        selected = browser.find_elements(By.CSS_SELECTOR, '.share-admin tbody td:first-child input:checked')
        for checkbox in selected:
            checkbox.click()
        _button(browser, '.share-admin', CREATE_SHARE_LABEL).click()
        assert browser.find_element(By.CSS_SELECTOR, '.share-create-error').text == 'Select at least one tab to share.'
        for checkbox in selected:
            checkbox.click()
        scroll_target = browser.execute_script("""
          const pane = document.querySelector('.share-manager-body');
          pane.style.maxHeight = '12rem';
          const picker = pane.querySelector('.share-admin-picker');
          picker.scrollIntoView({block:'start'});
          const a = pane.getBoundingClientRect(), b = picker.getBoundingClientRect();
          return {x:Math.round(b.left + b.width / 2), y:Math.round(Math.max(a.top,b.top) + 20), top:pane.scrollTop};
        """)
        browser.execute_cdp_cmd('Input.dispatchMouseEvent', {
            'type': 'mouseWheel', 'x': scroll_target['x'], 'y': scroll_target['y'], 'deltaX': 0, 'deltaY': 1000,
        })
        WebDriverWait(browser, 5).until(lambda d: d.execute_script("""
          const pane = document.querySelector('.share-manager-body');
          const picker = pane.querySelector('.share-admin-picker');
          const last = picker.querySelector('tbody tr:last-child').getBoundingClientRect();
          return pane.scrollTop > arguments[0] && picker.scrollTop === 0 &&
            last.bottom <= pane.getBoundingClientRect().bottom;
        """, scroll_target['top']))
        browser.execute_script("const pane=document.querySelector('.share-manager-body');pane.style.removeProperty('max-height');pane.scrollTop=0;")
        browser.save_screenshot(str(tmp_path / 'host-share-dialog.png'))
        limit = browser.find_element(By.CSS_SELECTOR, '.share-admin input[name="limit"]')
        assert [cell.text for cell in browser.find_elements(By.CSS_SELECTOR, '.share-admin thead th')] == ['R', 'W', '']
        assert browser.find_element(By.CSS_SELECTOR, '.share-admin input[name=minutes]').get_dom_attribute('max') == '1445'
        limit.clear(); limit.send_keys('2')
        for selected_path, permission in ((path, 'W'), (other, 'RO')):
            row = next(row for row in browser.find_elements(By.CSS_SELECTOR, '.share-admin tbody tr') if row.find_element(By.TAG_NAME, 'span').text == str(selected_path))
            selected = row.find_element(By.CSS_SELECTOR, 'td:first-child input')
            if not selected.is_selected():
                selected.click()
            if permission == 'W':
                row.find_element(By.CSS_SELECTOR, 'input[data-share-resource]').click()
                assert not browser.find_elements(By.CSS_SELECTOR, '.share-terminal-write-confirmation')
        terminals=[row for row in browser.find_elements(By.CSS_SELECTOR,'.share-admin tbody tr') if row.find_element(By.TAG_NAME,'span').text not in {str(path),str(other)}]
        assert len(terminals)==2
        for row,permission in zip(terminals,('RO','W')):
            row.find_element(By.CSS_SELECTOR,'td:first-child input').click()
            if permission == 'W':
                checkbox = row.find_element(By.CSS_SELECTOR,'input[data-share-resource]')
                checkbox.click()
                assert not checkbox.is_selected()
                _decide_terminal_write(browser, 'cancel')
                assert not checkbox.is_selected()
                checkbox.click()
                _decide_terminal_write(browser, 'allow')
                assert checkbox.is_selected()
        other_terminal = terminals[0].find_element(By.CSS_SELECTOR, 'input[data-share-resource]')
        other_terminal.click()
        assert not browser.find_elements(By.CSS_SELECTOR, '.share-terminal-write-confirmation')
        assert other_terminal.is_selected()
        other_terminal.click()
        assert not other_terminal.is_selected()
        assert not browser.find_elements(By.NAME, 'terminal-write')
        _button(browser, '.share-admin', CREATE_SHARE_LABEL).click()
        first = WebDriverWait(browser, 10).until(lambda d: d.find_element(By.CSS_SELECTOR, '.share-manager-disclosure'))
        assert not first.get_dom_attribute('open')
        assert first.find_element(By.CSS_SELECTOR, '[data-share-name]').text == default_name
        assert re.fullmatch(r'\d+m \d+s', first.find_element(By.CSS_SELECTOR, '[data-expiry]').text)
        assert first.find_element(By.CSS_SELECTOR, '[data-share-count]').text == '0 / 2 guests'
        assert not first.find_element(By.CSS_SELECTOR, '.share-connected-indicator').is_displayed()
        assert not first.find_element(By.CSS_SELECTOR, '.share-manager-detail').is_displayed()
        url = first.find_element(By.CSS_SELECTOR, '.share-invite input[type=url]').get_attribute('value')
        assert not browser.find_elements(By.CSS_SELECTOR, '.share-admin, .share-create-disclosure[open]')
        copy = first.find_element(By.CSS_SELECTOR, '.share-invite button.share-invite-copy')
        assert copy.text == f'Copy URL to share w/ {default_name}'
        assert browser.execute_script("""
          const button = arguments[0], input = button.previousElementSibling;
          if (input?.type !== 'url' || button.classList.contains('file-editor-primary-action') ||
              !button.firstElementChild?.matches('svg[data-menu-icon="copy"]')) return false;
          const buttonBox = button.getBoundingClientRect(), inputBox = input.getBoundingClientRect();
          const style = getComputedStyle(input), text = document.createElement('canvas').getContext('2d');
          text.font = style.font;
          const available = input.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
          return getComputedStyle(button).backgroundColor === 'rgba(0, 0, 0, 0)'
            && style.textOverflow === 'ellipsis' && text.measureText(input.value).width > available
            && inputBox.right <= buttonBox.left && inputBox.width > 0
            && Math.abs((buttonBox.top + buttonBox.bottom) - (inputBox.top + inputBox.bottom)) < 2;
        """, copy)
        assert browser.execute_script(r"""
          const row = arguments[0], countdown = row.querySelector('[data-expiry]');
          const stop = countdown.nextElementSibling;
          if (stop?.textContent !== 'Stop Sharing') return false;
          const timer = countdown.getBoundingClientRect(), button = stop.getBoundingClientRect();
          const stopStyle = getComputedStyle(stop);
          const [red, green, blue] = stopStyle.borderTopColor.match(/\d+/g).map(Number);
          return stopStyle.backgroundColor === 'rgba(0, 0, 0, 0)' &&
            red > green && red > blue && parseFloat(stopStyle.borderRadius) <= 4 &&
            button.width > 0 && button.left >= timer.right && button.top <= timer.bottom && button.bottom >= timer.top;
        """, first)
        browser.execute_cdp_cmd('Browser.grantPermissions', {
            'origin': server.base_url, 'permissions': ['clipboardReadWrite', 'clipboardSanitizedWrite'],
        })
        try:
            browser.execute_async_script("navigator.clipboard.writeText('clipboard sentinel').then(arguments[0]);")
            copy.click()
            WebDriverWait(browser, 8).until(lambda d: _clipboard_has_url(d, url))
            assert not first.get_dom_attribute('open')
        finally:
            browser.execute_cdp_cmd('Browser.resetPermissions', {})
        for index in range(2):
            driver = new_chrome_driver(profile_dir=tmp_path / f'guest-{index}')
            guests.append(driver); leases.append(WebDriverLease.from_driver(driver))
            driver.execute_cdp_cmd('Emulation.setEmulatedMedia', {'features': [{'name': 'prefers-color-scheme', 'value': 'dark'}]})
            driver.get(url)
            WebDriverWait(driver, 10).until(_guest_ready)
            _select_guest_tab(driver, 'shared.md')
            _share_mode(driver, 'edit')
            WebDriverWait(driver, 10).until(_share_editor)
            assert not driver.find_elements(By.CSS_SELECTOR, '.file-editor-review-panel:not([hidden])')
        assert guests[0].title == 'YOmux - keivenc - ' + default_name
        assert guests[1].title == 'YOmux - keivenc - ' + default_name + ' 2'
        # A guest's writable Tabber rows end in a red "(writable)"; read-only rows add nothing.
        guest_rows = WebDriverWait(guests[0], 10).until(lambda d: d.execute_script("""
          const rows = [...document.querySelectorAll('.tabber-normal-row .tabber-normal-primary')]
            .map(node => [node.textContent.trim(), node.querySelector('.tabber-share-access') && getComputedStyle(node.querySelector('.tabber-share-access')).color]);
          return rows.length === 4 ? rows : null;
        """))
        rows = dict(guest_rows)
        assert rows['shared.md (writable)'] == 'rgb(220, 38, 38)' and rows['other.dot'] is None, guest_rows
        assert sum(name.endswith(' (writable)') for name in rows) == 2, guest_rows
        assert guests[1].execute_async_script("""
          const done = arguments[0];
          YOmuxShare.post(shareWorkspace.base + 'chat', {text:'second guest color'}).then(() => done(true), () => done(false));
        """)
        _share_type(guests[0], 'one')
        _share_type(guests[1], 'two')
        WebDriverWait(guests[0], 10).until(lambda _: 'one' in path.read_text() and 'two' in path.read_text())
        _expand_share(browser, default_name)
        browser.execute_async_script("refreshShareManager().then(arguments[arguments.length - 1]);")
        assert len(browser.find_elements(By.CSS_SELECTOR, '[data-share-peers] .share-peer-connected')) == 2
        assert [cell.text for cell in browser.find_elements(By.CSS_SELECTOR, '[data-share-peers] th')] == [
            'Guest', 'IP address', 'Browser', 'Version',
        ]
        assert [cell.text for cell in browser.find_elements(By.CSS_SELECTOR, '[data-share-peers] td:nth-child(3)')] == [
            'Chrome', 'Chrome',
        ]
        expected_versions = [
            re.search(r'Chrome/([\d.]+)', guest.execute_script('return navigator.userAgent')).group(1)
            for guest in guests
        ]
        assert [cell.text for cell in browser.find_elements(By.CSS_SELECTOR, '[data-share-peers] td:nth-child(4)')] == expected_versions
        remove_browser_test_new_document_scripts(guests[1])
        guests[1].quit()
        retired = retire_all([leases[1]])
        assert retired['proven_gone'], retired
        guests.pop(1); leases.pop(1)
        assert not browser.find_elements(By.CSS_SELECTOR, '.share-admin')
        browser.execute_script('selectSession(arguments[0], {userInitiated:true})', opened['item'])
        browser.execute_script("window.__shareHoldAdmin=false;for(const release of window.__shareAdminHeld.splice(0))release();")
        WebDriverWait(browser, 15).until(lambda d: d.execute_script("return !!panelNodes.get(arguments[0])?._shareConnection && fileState.get(arguments[1]).content.includes('one') && fileState.get(arguments[1]).content.includes('two')", opened['item'], str(path)))
        # The owner's own Tabber never marks shared rows.
        assert not browser.execute_script("return buildTabberNormalRows().some(row => row.tabber.normalNameHtml?.includes('tabber-share-access'))")
        _share_mode(browser, 'edit')
        assert browser.execute_async_script("""
          const id = new URL(arguments[0]).pathname.split('/').pop(), done = arguments[1];
          YOmuxShare.post('/api/share/chat?id=' + encodeURIComponent(id), {text:'owner color'}).then(() => done(true), () => done(false));
        """, url)
        assert guests[0].execute_async_script("""
          const done = arguments[0];
          YOmuxShare.post(shareWorkspace.base + 'chat', {text:'guest color'}).then(() => done(true), () => done(false));
        """)
        for driver in (browser, guests[0]):
            WebDriverWait(driver, 10).until(lambda d: d.execute_script("""
              const expected = new Map([[arguments[0] + ': ', '180'], [arguments[0] + ' 2: ', '60']]);
              const colors = new Set();
              let ownerMatches = false;
              for (const author of document.querySelectorAll('.share-sidebar-chat .share-chat-author')) {
                const style = getComputedStyle(author);
                if (author.textContent === 'keivenc: ') {
                  ownerMatches ||= style.color === 'rgb(168, 85, 247)';
                }
                if (style.getPropertyValue('--share-participant-offset').trim() === expected.get(author.textContent)) {
                  colors.add(style.color);
                  expected.delete(author.textContent);
                }
              }
              return ownerMatches && expected.size === 0 && colors.size === 2 && !colors.has('rgb(168, 85, 247)');
            """, default_name))
            assert driver.execute_script("""
              const authors = [...document.querySelectorAll('.share-sidebar-chat .share-chat-author')];
              const owner = authors.find(node => node.textContent === 'keivenc: ');
              const guest = authors.find(node => node.textContent === arguments[0] + ': ');
              const probe = document.createElement('span');
              probe.style.color = 'var(--active-accent)';
              document.body.append(probe);
              const before = getComputedStyle(guest).color;
              try {
                applyActiveColor('orange');
                const accent = getComputedStyle(probe).color, guestColor = getComputedStyle(guest).color;
                return getComputedStyle(owner).color === accent && guestColor !== accent && guestColor !== before;
              } finally {
                applyActiveColor('purple');
                probe.remove();
              }
            """, default_name)
        assert guests[0].execute_async_script("""
          const done = arguments[0];
          const resource = [...shareWorkspace.resources.values()].find(value => value.label === 'shared.md');
          const url = new URL(shareWorkspace.base + resource.id, location.origin);
          url.searchParams.set('client', 'another-editor-view-01');
          url.searchParams.set('permission', resource.permission_revision);
          const initial = new URL(url);
          initial.searchParams.set('initial', '1');
          YOmuxShare.request(initial).then(({version}) =>
            YOmuxShare.post(url, {version, anchor: 0, head: 0})).then(() => done(true), () => done(false));
        """)
        for driver, offset in [(browser, 2), (guests[0], -1)]:
            editor = WebDriverWait(driver, 10).until(_share_editor)
            driver.execute_script("""
              const view = YOmuxCodeMirror.EditorView.findFromDOM(arguments[0]);
              view.focus();
              const last = view.state.doc.line(view.state.doc.lines);
              view.dispatch({selection: {anchor: arguments[1] < 0 ? Math.min(last.to, last.from + 1) : arguments[1]}});
            """, editor, offset)
        for driver, name, offset in [(guests[0], 'keivenc', 2), (browser, default_name, -1)]:
            selector = f'.cm-editor .share-remote-caret[data-share-caret-name={json.dumps(name)}]'
            find_caret = """
              const selectCaret = selector => {
                const matches = [...document.querySelectorAll(selector)].filter(node => node.getClientRects().length);
                return matches.length === 1 ? matches[0] : null;
              };
            """
            caret_position = find_caret + """
              const caret = selectCaret(arguments[0]);
              if (!caret) return false;
              const view = YOmuxCodeMirror.EditorView.findFromDOM(caret.closest('.cm-editor'));
              const last = view.state.doc.line(view.state.doc.lines);
              if (view.posAtDOM(caret) !== (arguments[1] < 0 ? Math.min(last.to, last.from + 1) : arguments[1])) return false;
              const {left, bottom, width, height} = caret.getBoundingClientRect();
              const x = Math.round(left + 1), y = Math.round(bottom - .45 * parseFloat(getComputedStyle(caret).fontSize));
              return !caret.textContent && width === 0 && height === 0
                ? {x, y, scale: devicePixelRatio} : false;
            """
            caret_point = WebDriverWait(driver, 10).until(lambda d: d.execute_script(caret_position, selector, offset))
            x, y, scale = (caret_point[key] for key in ('x', 'y', 'scale'))
            px, py = round(x * scale), round(y * scale)
            region = (px - 1, py - 2, px + 2, py + 3)
            visible, hidden = _share_screenshot_masks(driver, region, selector)
            assert max(abs(a - b) for before, after in zip(visible.getdata(), hidden.getdata()) for a, b in zip(before, after)) >= 30, (name, x, y)
            scroll_geometry = find_caret + """
              const scroller = selectCaret(arguments[0])?.closest('.cm-scroller');
              return scroller && [scroller.scrollLeft, scroller.scrollTop, scroller.scrollWidth, scroller.scrollHeight];
            """
            driver.execute_cdp_cmd('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': 2, 'y': 2})
            before = WebDriverWait(driver, 10).until(lambda d: d.execute_script(scroll_geometry, selector))
            label = WebDriverWait(driver, 10).until(lambda d: d.execute_script(find_caret + """
              const [name, selector, last] = arguments;
              const caret = selectCaret(selector);
              const label = [...document.querySelectorAll('.share-caret-label:not([hidden])')].find(node => node.textContent === name);
              const author = [...document.querySelectorAll('.share-sidebar-chat .share-chat-author')].find(node => node.textContent === name + ': ');
              if (!label || !author || !caret || caret.matches(':hover')) return false;
              const rect = label.getBoundingClientRect(), anchor = caret.getBoundingClientRect();
              const color = getComputedStyle(author).color;
              const style = getComputedStyle(label), caretStyle = getComputedStyle(caret);
              const fontSize = parseFloat(caretStyle.fontSize);
              const halfLine = parseFloat(caretStyle.lineHeight) / 2;
              const center = anchor.top - .4 * fontSize + (last ? -halfLine : halfLine);
              const gap = rect.left >= anchor.left ? rect.left - anchor.left : anchor.left - rect.right;
              return style.opacity === '0.85'
                && color === getComputedStyle(caret).color && color === style.backgroundColor
                && style.color === 'rgb(255, 255, 255)'
                && style.textShadow.includes('rgb(0, 0, 0)')
                && [style.borderTopWidth, style.borderRightWidth, style.borderBottomWidth, style.borderLeftWidth]
                  .every(width => width === '1px')
                && style.borderLeftColor === style.color
                && style.paddingTop === '0px' && style.paddingLeft === '4px'
                && Math.abs(gap - 6) <= 1
                && rect.width > 0 && rect.height > 0 && rect.left >= 0
                && rect.right <= innerWidth && rect.top >= 0 && rect.bottom <= innerHeight
                && {bounds:[rect.left, rect.top, rect.right, rect.bottom].map(n=>Math.round(n * devicePixelRatio)),
                    center:(rect.top + rect.bottom) / 2, expectedCenter:center, anchor:anchor.top, last};
            """, name, selector, offset < 0))
            assert abs(label['center'] - label['expectedCenter']) <= 1, (name, label)
            assert driver.execute_script("""
              const label = [...document.querySelectorAll('.share-caret-label:not([hidden])')]
                .find(node => node.textContent === arguments[0]);
              const wasLight = document.body.classList.contains('theme-light');
              document.body.classList.add('theme-light');
              try {
                const style = getComputedStyle(label);
                return style.color === 'rgb(0, 0, 0)' && style.opacity === '0.85' &&
                  style.textShadow.includes('rgb(255, 255, 255)');
              }
              finally { document.body.classList.toggle('theme-light', wasLight); }
            """, name)
            visible, hidden = _share_screenshot_masks(driver, tuple(label['bounds']), '.share-caret-label')
            assert max(abs(a - b) for before_pixel, after_pixel in zip(visible.getdata(), hidden.getdata()) for a, b in zip(before_pixel, after_pixel)) >= 30
            assert driver.execute_script(scroll_geometry, selector) == before
            surface, previous, priority, participant_color = WebDriverWait(driver, 10).until(lambda d: d.execute_script(find_caret + """
              const caret = selectCaret(arguments[0]), selector = arguments[1];
              if (!caret) return false;
              const surface = caret.closest('.file-editor-panel').querySelector(selector);
              const previous = surface.style.getPropertyValue('background-color');
              const priority = surface.style.getPropertyPriority('background-color');
              const color = getComputedStyle(caret).color;
              surface.style.setProperty('background-color', color, 'important');
              window.dispatchEvent(new Event('resize'));
              return [surface, previous, priority, color];
            """, selector, '.cm-editor' if driver is browser else '.file-editor-preview-pane-panel'))
            try:
                WebDriverWait(driver, 5).until(lambda d: d.execute_script("""
                  const [name, surface, participant] = arguments;
                  const label = [...document.querySelectorAll('.share-caret-label:not([hidden])')].find(node => node.textContent === name);
                  if (!label) return false;
                  const style = getComputedStyle(label);
                  return participant === getComputedStyle(surface).backgroundColor
                    && style.backgroundColor !== participant
                    && style.color === 'rgb(255, 255, 255)';
                """, name, surface, participant_color))
            finally:
                driver.execute_script("""
                  arguments[0].style.setProperty('background-color', arguments[1], arguments[2]);
                  window.dispatchEvent(new Event('resize'));
                """, surface, previous, priority)
        guests[0].find_element(By.CSS_SELECTOR, '.file-editor-panel .cm-content').click()
        native_logical_key(guests[0], 'z', modifiers=(native_editor_modifier(guests[0]),))
        WebDriverWait(guests[0], 10).until(lambda _: 'one' not in path.read_text() and 'two' in path.read_text())
        browser.execute_script("setFileEditorViewMode(arguments[0],'split',arguments[1])",str(path),opened['item'])
        WebDriverWait(browser,10).until(lambda d:d.execute_script("const panel=panelNodes.get(arguments[0]);return panel._pmView?.editable && panel._pmView.dom.textContent.includes('shared')",opened['item']))
        browser.execute_script("""
          window.__hostPendingFetch=[];window.__hostHold=true;window.__hostFetch=window.fetch;
          window.fetch=(url,options)=>window.__hostHold && String(url).includes('/api/share/document') && options?.method==='POST' ? new Promise((resolve,reject)=>window.__hostPendingFetch.push(()=>window.__hostFetch(url,options).then(resolve,reject))) : window.__hostFetch(url,options);
          window.__sharedHostView=panelNodes.get(arguments[0])._cmView;
        """,opened['item'])
        editor=browser.execute_script('return panelNodes.get(arguments[0])._cmView.contentDOM',opened['item']);editor.click()
        for char in 'host':native_logical_key(browser,char)
        browser.execute_script("""
          window.__shareBlameApplied=false;
          const applyBlame=applyEditorBlamePreference;
          applyEditorBlamePreference=async()=>{try{return await applyBlame();}finally{window.__shareBlameApplied=true;}};
          setFileEditorBlameEnabled(!fileEditorBlameEnabled);
        """)
        assert browser.execute_script("const panel=panelNodes.get(arguments[0]);return panel._cmView===window.__sharedHostView && panel._shareConnection.pending()>0 && panel._cmView.state.doc.toString().includes('host')",opened['item'])
        WebDriverWait(browser,10).until(lambda d:d.execute_script('return window.__shareBlameApplied'))
        assert browser.execute_script("const panel=panelNodes.get(arguments[0]);return panel._cmView===window.__sharedHostView && panel._shareConnection.pending()>0 && panel._cmView.state.doc.toString().includes('host')",opened['item'])
        browser.execute_script("window.__hostHold=false;for(const release of window.__hostPendingFetch.splice(0))release();")
        WebDriverWait(browser,10).until(lambda _: 'host' in path.read_text())
        WebDriverWait(browser,10).until(lambda d:d.execute_script("return panelNodes.get(arguments[0]).querySelector('.file-editor-preview-pane-panel').textContent.includes('host')",opened['item']))
        _open_share_manager(browser)
        guest = guests[0]
        _expand_share(browser, default_name)
        WebDriverWait(browser, 10).until(lambda d: default_name + ' 2' in d.find_element(By.CSS_SELECTOR, '[data-share-peers]').text)
        assert '127.0.0.1' in browser.find_element(By.CSS_SELECTOR, '[data-share-peers]').text
        WebDriverWait(browser, 10).until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '[data-share-peers] .share-peer-connected')) == 1)
        assert browser.find_element(By.CSS_SELECTOR, '.share-manager-disclosure[open] [data-share-count]').text == '1 / 2 guests'
        summary = browser.find_element(By.CSS_SELECTOR, '.share-manager-disclosure[open] summary')
        assert not summary.find_element(By.CSS_SELECTOR, '.share-connected-indicator').is_displayed()
        summary.find_element(By.CSS_SELECTOR, '[data-share-name]').click()
        assert summary.find_element(By.CSS_SELECTOR, '[data-share-connected-label]').text == 'Connected'
        assert browser.execute_script(r"""
          const marker = document.querySelector('.share-manager-disclosure:not([open]) .share-connected-indicator:not([hidden])');
          const play = marker?.querySelector('.agent-window-status-dot--play');
          const animation = play?.getAnimations()[0];
          if (!animation || marker.hidden) return false;
          const [r, g, b] = getComputedStyle(play).backgroundColor.match(/\d+/g).map(Number);
          const shape = getComputedStyle(play).clipPath;
          const {delay, duration} = animation.effect.getTiming();
          const cycleStart = delay + Math.ceil(Math.max(0, -delay) / duration) * duration;
          animation.pause();
          animation.currentTime = cycleStart;
          const low = Number(getComputedStyle(play).opacity);
          animation.currentTime = cycleStart + duration / 2;
          const high = Number(getComputedStyle(play).opacity);
          animation.play();
          return play.getBoundingClientRect().width > 0 && shape.startsWith('polygon(') && g > r && g > b && high > low;
        """)
        _expand_share(browser, default_name)
        peer_rows = browser.find_elements(By.CSS_SELECTOR, '[data-share-peers] tbody tr')
        disconnected_peer = next(row for row in peer_rows if default_name + ' 2' in row.text)
        connected_peer = next(row for row in peer_rows if default_name in row.text and default_name + ' 2' not in row.text)
        assert '127.0.0.1' in disconnected_peer.text
        assert disconnected_peer.find_elements(By.TAG_NAME, 'td')[2].text == 'Chrome'
        assert not disconnected_peer.find_elements(By.CSS_SELECTOR, '.share-peer-connected')
        assert connected_peer.find_elements(By.CSS_SELECTOR, '.share-peer-connected')
        assert re.search(r'\d+m \d+s', browser.find_element(By.CSS_SELECTOR, '[data-expiry]').text)
        countdown = guest.find_element(By.CSS_SELECTOR, '.topbar [role=timer]')
        assert countdown.is_displayed()
        remaining = countdown.text
        assert re.fullmatch(r'\d+m \d+s left', remaining)
        WebDriverWait(guest, 3).until(lambda _: countdown.text != remaining)
        assert not browser.find_elements(By.CSS_SELECTOR, '.topbar [role=timer]')
        expiry_revision = json.loads(browser.find_element(By.CSS_SELECTOR, '.share-manager-disclosure[open] .share-manager-detail').get_attribute('data-key'))[0]
        expiry = browser.find_element(By.CSS_SELECTOR, '.share-manager-disclosure[open] .share-expiry-control input')
        assert browser.execute_script("""
          const detail = arguments[0].closest('.share-manager-detail');
          const top = detail.firstElementChild, picker = detail.querySelector('.share-admin-picker');
          const stop = top.querySelector('.share-stop-action');
          const summaryStop = detail.parentElement.querySelector('summary .share-summary-stop');
          return top.contains(arguments[0]) && top.querySelector('input[name=limit]') &&
            stop?.textContent === 'Stop Sharing' && !summaryStop.getClientRects().length &&
            getComputedStyle(stop).backgroundColor === getComputedStyle(summaryStop).backgroundColor &&
            top.getBoundingClientRect().bottom <= picker.getBoundingClientRect().top;
        """, expiry)
        assert expiry.get_dom_attribute('max') == '1445'
        expiry.clear(); expiry.send_keys('7', Keys.ENTER)
        WebDriverWait(browser, 10).until(lambda d: d.execute_script("""
          const detail=document.querySelector('.share-manager-disclosure[open] .share-manager-detail');
          const minutes=detail?.querySelector('.share-expiry-control input');
          return detail && minutes && Number(JSON.parse(detail.dataset.key)[0])>arguments[0] && minutes.value==='7';
        """, expiry_revision))
        WebDriverWait(guest, 5).until(lambda _: re.fullmatch(r'(?:7m 0s|6m [45]\ds) left', countdown.text))
        limit = browser.find_element(By.CSS_SELECTOR, '.share-manager-disclosure[open] input[name=limit]')
        limit.clear()
        limit.send_keys('3', Keys.ENTER)
        count = '.share-manager-disclosure[open] [data-share-count]'
        WebDriverWait(browser, 5).until(lambda d: d.find_element(By.CSS_SELECTOR, count).text == '1 / 3 guests')
        limit = browser.find_element(By.CSS_SELECTOR, '.share-manager-disclosure[open] input[name=limit]')
        limit.clear()
        limit.send_keys('2', Keys.ENTER)
        WebDriverWait(browser, 5).until(lambda d: d.find_element(By.CSS_SELECTOR, count).text == '1 / 2 guests')
        new_share.click()
        name = WebDriverWait(browser, 5).until(lambda d: d.find_element(By.CSS_SELECTOR, '.share-admin input[name=guest]'))
        assert name.is_displayed()
        name.clear()
        name.send_keys(' ' + default_name.lower() + ' ')
        _button(browser, '.share-admin', CREATE_SHARE_LABEL).click()
        WebDriverWait(browser, 10).until(lambda d: 'already in use' in d.find_element(By.CSS_SELECTOR, '.share-admin [role=status]').text)
        assert name.is_displayed()
        assert_only_expected_browser_network_error(browser, url=server.base_url + '/api/share', reason='status of 409', count=1)
        name.clear()
        name.send_keys('Second guest')
        next(row for row in browser.find_elements(By.CSS_SELECTOR, '.share-admin tbody tr') if row.text == str(path)).find_element(By.CSS_SELECTOR, 'td:first-child input').click()
        _button(browser, '.share-admin', CREATE_SHARE_LABEL).click()
        WebDriverWait(browser, 10).until(lambda d: not d.find_elements(By.CSS_SELECTOR, '.share-admin'))
        WebDriverWait(browser, 10).until(lambda d: d.execute_script("""
          const pane = document.querySelector('.share-manager-body');
          const active = pane.querySelector('.share-manager-active');
          const create = pane.querySelector('.share-create-disclosure');
          return !active.hidden && active.getBoundingClientRect().bottom <= create.getBoundingClientRect().top &&
            active.querySelectorAll('.share-manager-disclosure').length === 2;
        """))
        second = _expand_share(browser, 'Second guest')
        first = _expand_share(browser, default_name)
        assert first.find_element(By.CSS_SELECTOR, '.share-invite input').get_attribute('value') == url
        assert second.find_element(By.CSS_SELECTOR, '.share-invite input').get_attribute('value') != url
        assert second.find_element(By.CSS_SELECTOR, '.share-invite button').text == 'Copy URL to share w/ Second guest'
        assert len(browser.find_elements(By.CSS_SELECTOR, '.share-manager input[type=url]')) == 2
        browser.save_screenshot(str(tmp_path / 'host-active-shares.png'))
        second.find_element(By.CSS_SELECTOR, '[data-share-name]').click()
        assert not second.get_dom_attribute('open')
        browser.execute_script("""
          const row = arguments[0];
          window.__stoppedRowExpanded = false;
          row.addEventListener('toggle', event => {
            if (event.newState === 'open') window.__stoppedRowExpanded = true;
          });
        """, second)
        second.find_element(By.CSS_SELECTOR, 'summary .share-summary-stop').click()
        WebDriverWait(browser, 10).until(lambda d: d.execute_script("return ![...document.querySelectorAll('[data-share-name]')].some(node=>node.textContent==='Second guest')"))
        assert not browser.execute_script('return window.__stoppedRowExpanded')
        WebDriverWait(browser, 10).until(lambda d: d.execute_script("""
          const chats = [...document.querySelectorAll('.share-sidebar-chat')];
          const select = document.querySelector('.share-sidebar-conversations select');
          return chats.length === 1 && !chats[0].hidden && select?.hidden
            && select.selectedOptions[0]?.textContent === arguments[0];
        """, default_name))
        assert not browser.find_elements(By.CSS_SELECTOR, '.share-manager [role=log]')
        edit = guest.find_element(By.CSS_SELECTOR, '[data-editor-mode="edit"]')
        edit.click()
        editor = WebDriverWait(guest, 10).until(_share_editor)
        _share_mode(guest, 'preview')
        assert not editor.is_displayed()
        WebDriverWait(guest, 10).until(lambda d: d.execute_script("""
          const rendered = selector => [...document.querySelectorAll(selector)]
            .filter(node => node.getClientRects().length);
          return rendered('.share-rich-caret[data-share-caret-name="keivenc"]').length === 1 &&
            rendered('.share-remote-caret[data-share-caret-name="keivenc"]').length === 0 &&
            rendered('.share-caret-label:not([hidden])').filter(node => node.textContent === 'keivenc').length === 1;
        """))
        WebDriverWait(guest, 10).until(lambda d: 'host' in d.find_element(By.CSS_SELECTOR, '.file-editor-preview-pane-panel').text)
        _share_mode(guest, 'split')
        layout = guest.execute_script("""
          const holder = document.querySelector('.file-editor-codemirror-panel'), preview = document.querySelector('.file-editor-preview-pane-panel');
          const a = holder.getBoundingClientRect(), b = preview.getBoundingClientRect();
          const row = document.querySelector('.file-explorer-tabber .tabber-active-normal') || document.querySelector('.file-explorer-tabber [aria-selected=true]');
          return {editorWidth:a.width, previewWidth:b.width, split:a.right<=b.left && a.top===b.top,
                  selection:getComputedStyle(row).boxShadow};
        """)
        assert layout['editorWidth'] > 100 and layout['previewWidth'] > 100 and layout['split'], layout
        assert layout['selection'] != 'none', layout
        assert editor == WebDriverWait(guest, 10).until(_share_editor)
        guest.find_element(By.CSS_SELECTOR, '.file-editor-panel .file-editor-find-panel').click()
        assert guest.find_element(By.CSS_SELECTOR, '.file-editor-panel .cm-search').is_displayed()
        wrap = guest.find_element(By.CSS_SELECTOR, '.file-editor-panel .file-editor-wrap-panel')
        was_wrapped = wrap.get_attribute('aria-pressed') == 'true'
        wrap.click()
        assert bool(guest.find_elements(By.CSS_SELECTOR, '.file-editor-panel .cm-lineWrapping')) != was_wrapped
        lines = guest.find_element(By.CSS_SELECTOR, '.file-editor-panel .file-editor-gutter-panel')
        had_lines = lines.get_attribute('aria-pressed') == 'true'
        lines.click()
        assert bool(guest.find_elements(By.CSS_SELECTOR, '.file-editor-panel .cm-lineNumbers')) != had_lines
        original_size = guest.find_element(By.CSS_SELECTOR, '.file-editor-preview-pane-panel').value_of_css_property('font-size')
        guest.find_element(By.CSS_SELECTOR, '.file-editor-panel [data-editor-preview-font-step="1"]').click()
        assert guest.find_element(By.CSS_SELECTOR, '.file-editor-preview-pane-panel').value_of_css_property('font-size') != original_size
        assert min(_markdown_preview_contrast(guest)) >= 4.5
        guest.save_screenshot(str(tmp_path / 'guest-markdown-split-dark.png'))
        guest.execute_script("setGlobalThemeMode('light')")
        WebDriverWait(guest, 5).until(lambda d: d.execute_script("return document.body.classList.contains('theme-light')"))
        painted = _guest_tab_row(guest, 'shared.md').value_of_css_property('box-shadow')
        assert painted != 'none' and painted != layout['selection'], (painted, layout)
        assert min(_markdown_preview_contrast(guest)) >= 4.5
        guest.save_screenshot(str(tmp_path / 'guest-markdown-split-light.png'))
        guest.execute_cdp_cmd('Emulation.setEmulatedMedia', {'features': []})
        for driver in (browser, guest):
            _assert_chat_sidebar_resize(driver)
        chat_input = guest.find_element(By.CSS_SELECTOR, '.share-sidebar-chat textarea')
        chat_input.send_keys('hello')
        native_logical_key(guest, 'Enter', modifiers=('shift',), commands=('insertNewline',))
        chat_input.send_keys('host')
        assert chat_input.get_property('value') == 'hello\nhost'
        native_logical_key(guest, 'Enter')
        log_selector = '.share-sidebar-chat:not([hidden]) [role=log]'
        WebDriverWait(browser, 10).until(lambda d: 'hello\nhost' in d.find_element(By.CSS_SELECTOR, log_selector).text)
        assert 'hello\nhost' in guest.find_element(By.CSS_SELECTOR, log_selector).text
        host_chat = browser.find_element(By.CSS_SELECTOR, '.share-sidebar-chat:not([hidden]) textarea')
        host_chat.send_keys('host reply', Keys.ENTER)
        WebDriverWait(guest, 10).until(lambda d: 'host reply' in d.find_element(By.CSS_SELECTOR, '.share-sidebar-chat [role=log]').text)
        assert guest.execute_script("return new Set([...document.querySelectorAll('.share-sidebar-chat .share-chat-author')].map(node=>getComputedStyle(node).color)).size") >= 2
        chat_box = guest.find_element(By.CSS_SELECTOR, '.share-sidebar-chat').rect
        content_box = guest.find_element(By.CSS_SELECTOR, '.file-editor-panel').rect
        tabs_box = guest.find_element(By.CSS_SELECTOR, '.file-explorer-tabber .file-explorer-changes-panel').rect
        assert chat_box['x'] < content_box['x'] and chat_box['y'] >= tabs_box['y'] + tabs_box['height'] - 1
        # The transport barrier makes switching happen before the edit can be acknowledged.
        guest.execute_script("""
          window.__shareHeld=[];window.__shareFetch=window.fetch;window.__shareHold=true;
          window.fetch=(url,options)=>window.__shareHold && options?.method==='POST' &&
            new URL(String(url), location.href).pathname.startsWith(shareWorkspace.base)
              ? new Promise((resolve,reject)=>window.__shareHeld.push(()=>window.__shareFetch(url,options).then(resolve,reject)))
              : window.__shareFetch(url,options);
        """)
        _share_type(guest, 'retained')
        WebDriverWait(guest, 5).until(lambda d: d.execute_script("return window.__shareHeld.length > 0"))
        _select_guest_tab(guest, 'other.dot')
        assert guest.find_element(By.CSS_SELECTOR, '.share-sidebar-chat').is_displayed()
        guest.execute_script("window.__shareHold=false;for(const release of window.__shareHeld.splice(0))release();")
        WebDriverWait(guest, 10).until(lambda _: path.read_text().count('retained') == 1)
        _select_guest_tab(guest, 'shared.md')
        guest.execute_cdp_cmd('Network.enable', {})
        guest.execute_cdp_cmd('Network.emulateNetworkConditions', {'offline':True,'latency':0,'downloadThroughput':-1,'uploadThroughput':-1})
        _share_type(guest, 'offline')
        try:
            WebDriverWait(browser, 10).until(lambda d: d.find_element(By.CSS_SELECTOR, count).text == '0 / 2 guests')
            assert not browser.find_element(By.CSS_SELECTOR, '.share-manager-disclosure[open] .share-connected-indicator').is_displayed()
        finally:
            guest.execute_cdp_cmd('Network.emulateNetworkConditions', {'offline':False,'latency':0,'downloadThroughput':-1,'uploadThroughput':-1})
        WebDriverWait(browser, 10).until(lambda d: d.find_element(By.CSS_SELECTOR, count).text == '1 / 2 guests')
        WebDriverWait(guest, 10).until(lambda _: path.read_text().count('offline') == 1)
        for filename in ('other.dot', 'shared.md'):
            for checked in (False, True):
                _set_share_tab(browser, default_name, filename, checked)
                WebDriverWait(guest, 10).until(lambda d: d.execute_script("""
                  return [...shareWorkspace.resources.values()].some(item => item.label === arguments[0]) === arguments[1];
                """, filename, checked))
        _toggle_host_permission(browser, str(path))
        WebDriverWait(guest, 10).until(lambda d: d.execute_script("""
          return [...shareWorkspace.resources.values()].some(item => item.label === 'shared.md' && item.writable);
        """))
        resources=_share_json(browser,'/api/share')['body']['shares'][0]['resources']
        writable=next(resource for resource in resources if resource['kind']=='terminal' and resource['writable'])
        readonly=next(resource for resource in resources if resource['kind']=='terminal' and not resource['writable'])
        geometry=run_isolated_tmux(server.tmux,'display-message','-p','-t',writable['pane'],'#{pane_width}x#{pane_height}:#{window_id}:#{pane_active}')
        assert geometry.returncode==0,geometry.stderr
        _select_guest_tab(guest, resource_id=writable['id'])
        screen=guest.find_element(By.CSS_SELECTOR,'.share-screen')
        assert guest.execute_script("return document.activeElement===arguments[0]", screen)
        owner = guest.execute_script("""
          const screen=document.querySelector('.share-screen');
          const api=dockviewLayoutState.api, capture=captureDockviewPaneBeforeDetach, fromJSON=api.fromJSON;
          let insideFromJSON=false, lostWithoutCapture=false;
          try {
            api.fromJSON=function(...args){insideFromJSON=true;try{return fromJSON.apply(this,args);}finally{insideFromJSON=false;}};
            // Reproduce the prior ordering: only the late pool callbacks capture state.
            captureDockviewPaneBeforeDetach=function(...args){if(insideFromJSON)return capture(...args);};
            dockviewLoadLayout(layoutSlots);finishPanelLayoutRender([], {prune:false});
            lostWithoutCapture=document.activeElement!==screen;
          } finally {
            captureDockviewPaneBeforeDetach=capture;api.fromJSON=fromJSON;
          }
          screen.focus();
          const retained=[];
          for(let pass=0;pass<2;pass++){
            dockviewLoadLayout(layoutSlots);finishPanelLayoutRender([], {prune:false});
            retained.push(document.activeElement===screen && screen.isConnected);
          }
          return {lostWithoutCapture,retained,connected:screen.isConnected};
        """)
        assert owner == {'lostWithoutCapture': True, 'retained': [True, True], 'connected': True}, owner
        guest.set_window_size(740,540)
        screen=guest.find_element(By.CSS_SELECTOR,'.share-screen');screen.click()
        guest.execute_script("arguments[0].style.maxHeight='3lh'", screen)
        for char in 'sizeproof':
            native_logical_key(guest, char)
        native_logical_key(guest, 'a', modifiers=('ctrl',))
        for char in 'echo ':
            native_logical_key(guest, char)
        native_logical_key(guest, 'e', modifiers=('ctrl',))
        for char in ' discarded':
            native_logical_key(guest, char)
        native_logical_key(guest, 'a', modifiers=('ctrl',))
        for _ in 'echo sizeproof':
            native_logical_key(guest, 'f', modifiers=('ctrl',))
        native_logical_key(guest, 'k', modifiers=('ctrl',))
        native_logical_key(guest, 'Enter')
        WebDriverWait(guest,10).until(lambda _: 'sizeproof' in screen.get_property('textContent').splitlines())
        cursor_position = run_isolated_tmux(server.tmux, 'display-message', '-p', '-t', writable['pane'], '#{cursor_x}:#{cursor_y}')
        assert cursor_position.returncode == 0
        WebDriverWait(guest, 5).until(lambda d: d.execute_script("""
          const cursor = arguments[0].querySelector('.share-screen-cursor');
          if (!cursor || cursor.hidden) return false;
          const [x, y] = arguments[1].split(':');
          const rect = cursor.getBoundingClientRect();
          const viewport = arguments[0].getBoundingClientRect();
          return cursor.style.getPropertyValue('--share-cursor-column') === x &&
            cursor.style.getPropertyValue('--share-cursor-row') === y &&
            rect.width > 0 && rect.height > 0 &&
            rect.top >= viewport.top - 1 && rect.bottom <= viewport.bottom + 1 &&
            rect.left >= viewport.left - 1 && rect.right <= viewport.right + 1 &&
            getComputedStyle(cursor).backgroundColor !== 'rgba(0, 0, 0, 0)';
        """, screen, cursor_position.stdout.strip()))
        guest.execute_script("arguments[0].style.removeProperty('max-height')", screen)
        assert guest.execute_script("return document.activeElement===document.querySelector('.share-screen')")
        for char in 'echo discarded':
            native_logical_key(guest, char)
        native_logical_key(guest, 'w', modifiers=('ctrl',))
        for char in 'wordproof':
            native_logical_key(guest, char)
        native_logical_key(guest, 'Enter')
        WebDriverWait(guest, 10).until(lambda _: 'wordproof' in screen.get_property('textContent').splitlines())
        after=run_isolated_tmux(server.tmux,'display-message','-p','-t',writable['pane'],'#{pane_width}x#{pane_height}:#{window_id}:#{pane_active}')
        assert after.returncode==0 and after.stdout==geometry.stdout,(geometry,after)
        source = writable['label'].split(':', 1)[0]
        view = source + '-host-view'
        linked = run_isolated_tmux(server.tmux, 'new-session', '-d', '-t', source, '-s', view)
        assert linked.returncode == 0, linked.stderr
        resized = run_isolated_tmux(server.tmux, 'resize-window', '-t', view + ':', '-x', '65', '-y', '20')
        assert resized.returncode == 0, resized.stderr
        host_screen = run_isolated_tmux(server.tmux, 'capture-pane', '-p', '-t', writable['pane'])
        assert host_screen.returncode == 0 and len(host_screen.stdout.splitlines()) == 20, host_screen.stderr
        WebDriverWait(guest, 10).until(lambda d: screen.get_property('textContent') == host_screen.stdout)
        assert guest.execute_script("return document.activeElement===arguments[0]", screen)
        for char in 'echo resizedproof':native_logical_key(guest,char)
        native_logical_key(guest, 'Enter')
        WebDriverWait(guest, 10).until(lambda _: 'resizedproof' in screen.get_property('textContent').splitlines())
        base=url.split('#')[0]+'/'
        _expand_share(browser, default_name)
        assert not guest.execute_script("return shareWorkspace.resources.get(arguments[0])?.writable", readonly['id'])
        _toggle_host_permission(browser, readonly['label'])
        WebDriverWait(guest, 5).until(lambda d: d.execute_script("return shareWorkspace.resources.get(arguments[0])?.writable", readonly['id']))
        _toggle_host_permission(browser, readonly['label'])
        WebDriverWait(guest, 5).until(lambda d: d.execute_script("return shareWorkspace.resources.get(arguments[0])?.writable === false", readonly['id']))
        assert _share_json(guest,base+readonly['id'],{'text':'blocked'})['status']==403
        assert _share_json(guest, '/api/fs/raw?path=' + str(path))['status'] == 401
        third=_share_json(browser,url.split('#')[0]+'/join',{'secret':url.split('#')[1]})
        assert third['status']==409,third
        assert_only_expected_browser_network_error(browser,url=url.split('#')[0]+'/join',reason='status of 409',count=1)
        denied = new_chrome_driver(profile_dir=tmp_path / 'full-share')
        denied_lease = WebDriverLease.from_driver(denied)
        try:
            denied.execute_cdp_cmd('Emulation.setEmulatedMedia', {'features': [{'name': 'prefers-color-scheme', 'value': 'light'}]})
            for target, endpoint in ((base.rstrip('/'), 'state'), (url, 'join')):
                denied.get(target)
                notice = WebDriverWait(denied, 5).until(lambda d: d.find_element(By.CSS_SELECTOR, '.share-access-dialog[open] #shareAccessNotice'))
                assert notice.text == 'This share has reached its guest limit.'
                appearance = denied.execute_script("""
                  const dialog = document.querySelector('.share-access-dialog');
                  const style = getComputedStyle(document.body);
                  const mark = getComputedStyle(dialog.querySelector('.brand-mark')).backgroundColor;
                  const second = getComputedStyle(dialog.querySelector('.brand-second')).color;
                  const border = getComputedStyle(dialog.querySelector('.share-access-card')).borderTopColor;
                  return {theme: resolvedGlobalThemeMode(), accent: style.getPropertyValue('--active-accent').trim(),
                    mark, second, border};
                """)
                assert appearance['theme'] == 'dark' and appearance['accent'] == '#a855f7', appearance
                assert appearance['mark'] == appearance['second'] == 'rgb(168, 85, 247)', appearance
                assert appearance['border'] == browser.execute_script("""
                  const sample = document.createElement('div');
                  sample.style.borderColor = 'var(--app-modal-border-color)';
                  document.body.append(sample);
                  const color = getComputedStyle(sample).borderTopColor;
                  sample.remove();
                  return color;
                """), appearance
                assert_only_expected_browser_http_error(denied, path=urlsplit(base + endpoint).path, status=409, query={})
                denied.execute_script("storageRemove('yomux.share.appearance.' + shareWorkspace.base)")
                denied.get('about:blank')
        finally:
            remove_browser_test_new_document_scripts(denied)
            denied.quit()
            retired = retire_all([denied_lease])
            assert retired['proven_gone'], retired
        state = _share_json(browser, '/api/share')['body']
        assert state['shares'][0]['claimed'] == 2
        guest.refresh()
        WebDriverWait(guest, 10).until(_guest_ready)
        assert _share_json(browser, '/api/share')['body']['shares'][0]['claimed'] == 2
        _select_guest_tab(guest, 'shared.md')
        _share_mode(guest, 'edit')
        WebDriverWait(guest, 10).until(_share_editor)
        _toggle_host_permission(browser, str(path))
        WebDriverWait(guest, 10).until(lambda d: d.execute_script("return [...shareWorkspace.resources.values()].some(resource=>resource.label==='shared.md' && !resource.writable) && [...document.querySelectorAll('.file-editor-panel .cm-editor')].some(root=>root.getClientRects().length && YOmuxCodeMirror.EditorView.findFromDOM(root).state.readOnly)"))
        _share_mode(guest, 'split')
        read_only = guest.find_element(By.CSS_SELECTOR, '.file-editor-panel .cm-content')
        assert read_only.get_attribute('contenteditable') != 'true'
        markdown = next(resource for resource in resources if resource['label'] == 'shared.md')
        assert _share_json(guest, base + markdown['id'], {'updates': []})['status'] == 403
        assert _share_json(guest, base + markdown['id'] + '?permission=' + str(markdown['permission_revision']), {'updates': []})['status'] == 409
        assert guest.find_element(By.CSS_SELECTOR, '.share-sidebar-chat').is_displayed()
        _toggle_host_permission(browser, str(path))
        WebDriverWait(guest, 10).until(_share_editor)
        _share_type(guest, 'upgraded')
        WebDriverWait(guest, 10).until(lambda _: 'upgraded' in path.read_text())
        # Hold the actual draft POST and background readers so a clean poll
        # cannot import the external text before the conflicting draft arrives.
        for driver, endpoint, host_path in (
            (browser, '/api/share/document', str(path)),
            (guest, urlsplit(base + markdown['id']).path, None),
        ):
            driver.execute_script("""
              const [endpoint, hostPath] = arguments;
              const original = window.fetch;
              const gate = window.__shareConflict = {reads: [], writes: []};
              gate.restore = () => {
                window.fetch = original;
                for (const release of [...gate.reads.splice(0), ...gate.writes.splice(0)]) release();
              };
              window.fetch = (input, options) => {
                const url = new URL(String(input), location.href);
                if (url.pathname !== endpoint || (hostPath && url.searchParams.get('path') !== hostPath)) {
                  return original(input, options);
                }
                const write = options?.method === 'POST' && JSON.parse(options.body).updates?.length > 0;
                if (!hostPath && options?.method === 'POST' && !write) return original(input, options);
                return new Promise((resolve, reject) => {
                  (write ? gate.writes : gate.reads).push(() => original(input, options).then(resolve, reject));
                });
              };
            """, endpoint, host_path)
        try:
            _share_type(guest, 'draft')
            WebDriverWait(browser, 10).until(lambda d: d.execute_script("return window.__shareConflict.reads.length > 0"))
            WebDriverWait(guest, 10).until(lambda d: d.execute_script("return window.__shareConflict.writes.length > 0"))
            path.write_text('external editor\n', encoding='utf-8')
            guest.execute_script("window.__shareConflict.writes.shift()()")
            WebDriverWait(guest, 10).until(lambda d: 'paused' in d.find_element(By.CSS_SELECTOR, '.file-editor-status-message').text.lower())
        finally:
            for driver in (browser, guest):
                driver.execute_script("window.__shareConflict.restore()")
        assert path.read_bytes() == b'external editor\n'
        def journaled_draft(driver):
            drafts = _share_json(driver, '/api/share/recovery')['body']['drafts']
            return next((draft['text'].encode('utf-8') for draft in drafts
                         if draft['path'] == str(path) and 'draft' in draft['text']), False)

        retained = WebDriverWait(browser, 10).until(journaled_draft)
        restored = _share_json(browser, '/api/share/recovery', {'path':str(path),'action':'restore'})
        assert restored['status'] == 200, restored
        assert path.read_bytes() == retained
        grant_id = _share_json(browser, '/api/share')['body']['shares'][0]['id']
        stopped = _share_json(browser, '/api/share?id=' + grant_id, method='DELETE')
        assert stopped['status'] == 200, stopped
        WebDriverWait(browser, 10).until(lambda d: d.execute_script("""
          const active = document.querySelector('.share-manager-active');
          return active?.hidden && document.querySelector('.share-create-disclosure[open] .share-admin')?.getClientRects().length > 0;
        """))
        new_share.click()
        browser.execute_async_script("const done = arguments[0]; refreshShareManager().then(done);")
        assert not browser.find_elements(By.CSS_SELECTOR, '.share-create-disclosure[open]')
        for driver in guests:
            WebDriverWait(driver, 10).until(lambda d: d.execute_script("""
              return !shareWorkspace.active && shareWorkspace.resources.size === 0 && fileState.size === 0 &&
                !document.querySelector('.file-explorer-tabber [data-tabber-item], #grid .file-editor-panel, #grid .share-screen') &&
                !document.querySelector('.share-sidebar-chat');
            """))
        notice = guest.find_element(By.ID, 'shareAccessNotice')
        assert 'expired or ended' in notice.text and 'YO!Share → New Share' in notice.text
        assert not guest.find_element(By.CSS_SELECTOR, '.topbar').is_displayed()
        assert not guest.find_elements(By.CSS_SELECTOR, '#sessionButtons .app-menu')
        patch_failures = browser.execute_script("return window.__sharePatchResults.filter(result=>result.status!==200)")
        assert not patch_failures, patch_failures
        manager_failures = browser.execute_script("return window.__shareManagerResults.filter(result=>result.status>=400)")
        assert not manager_failures, manager_failures
        api_failures = browser.execute_script("return window.__shareAPIRequestErrors")
        assert not api_failures, api_failures
        WebDriverWait(browser, 10).until(lambda d: not d.find_elements(By.CSS_SELECTOR, '.share-sidebar-conversations, .share-sidebar-chat'))
        retired_documents = browser.execute_script("return window.__retiredShareDocuments")
        expected = [(server.base_url + '/api/share/chat?id=' + grant_id, 410)]
        expected.extend((url, 409) for url in retired_documents)
        unexpected = [
            entry for entry in read_browser_console_log(browser)
            if entry.get('level') in {'WARNING', 'SEVERE'}
            and not (entry.get('source') == 'network' and any(
                entry['message'].startswith(url + ' - Failed to load resource:')
                and f'status of {status}' in entry['message'] for url, status in expected
            ))
        ]
        assert not unexpected, unexpected
        fresh = _share_json(browser, '/api/share', {'guest':'RestartGuest','minutes':5,'limit':1,'resources':[{'kind':'file','path':str(path),'writable':False}]})
        assert fresh['status'] == 201, fresh
        fresh_url = fresh['body']['url']
        fresh_base = fresh_url.split('#')[0]+'/'
        assert _share_json(guest,fresh_base+'join',{'secret':fresh_url.split('#')[1]})['status'] == 200
        assert _share_json(guest,fresh_base+'state')['status'] == 200
        browser.get('about:blank')
        server.restart()
        assert _share_json(guest,fresh_base+'state')['status']==410
        guest.get(url)
        WebDriverWait(guest, 5).until(lambda d: 'expired or ended' in d.find_element(By.ID, 'shareAccessNotice').text)
        assert not guest.find_element(By.CSS_SELECTOR, '.topbar').is_displayed()
        assert not guest.find_elements(By.CSS_SELECTOR, '#sessionButtons .app-menu')
        assert path.read_bytes() == retained

    except Exception:
        diagnostics={'disk':path.read_text(),'guests':[]}
        for driver in guests:
            diagnostics['guests'].append({'status':driver.find_element(By.ID,'status').text,'body':driver.find_element(By.TAG_NAME,'body').text,'console':driver.get_log('browser'),'focus':driver.execute_script("return document.activeElement.tagName+':'+document.activeElement.className")})
        (tmp_path/'browser-debug.json').write_text(json.dumps(diagnostics,indent=2))
        raise
    finally:
        for driver in guests:
            remove_browser_test_new_document_scripts(driver)
            driver.get('about:blank')
        if leases:
            retirement = retire_all(leases)
            assert retirement['proven_gone'], retirement
        browser.get('about:blank')
