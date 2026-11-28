# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Native Chromium input for the source-preserving HTML editor."""

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_console import read_browser_console_log
from tests.browser_helpers.browser_layout import browser, browser_auth_yaml, new_chrome_driver  # noqa: F401
from tests.browser_helpers.native_keyboard import native_editor_modifier, native_logical_key
from tests.browser_helpers.native_pointer import _cdp_click_point, cdp_drag_hold, cdp_release, settle_native_frames
from tests.browser_helpers.editor_mount import _open_relocation_editor, mount_html_split
from tests.browser_helpers.share_workspace import _share_json, _workspace_mode
from tests.browser_helpers.webdriver_lease import WebDriverLease
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401


pytestmark = pytest.mark.browser


def _long_html_fixture():
    return (Path(__file__).resolve().parent / 'fixtures' / 'odd_orbit.html').read_text(encoding='utf-8')


def _assert_preview_scripts_blocked(browser):
    severe = [entry for entry in read_browser_console_log(browser) if entry.get('level') == 'SEVERE']
    assert severe and all(
        entry.get('source') == 'security' and entry['message'] ==
        "about:srcdoc - Blocked script execution in 'about:srcdoc' because the document's frame is sandboxed and the 'allow-scripts' permission is not set."
        for entry in severe
    ), severe


def _frame_point(browser, item, selector, offset=None, edge=None, text_index=0, *, source_ready=True, inset=0.2):
    return browser.execute_script("""
      const [item, selector, offset, edge, textIndex, sourceReady, inset] = arguments;
      const panel = panelNodes.get(item);
      const frame = panel?.querySelector('.file-editor-html-preview');
      const doc = frame?._htmlPreviewDocument;
      const element = doc?.querySelector(selector);
      if ((sourceReady && !panel?._cmView) || !element) return null;
      const bounds = element.getBoundingClientRect();
      let x = bounds.left + Math.min(5, bounds.width / 2);
      let y = bounds.top + bounds.height / 2;
      if (offset !== null) {
        const node = [...element.childNodes].map(node =>
          node.matches?.('[data-html-viewedit]') ? node.firstChild : node
        ).filter(node => node?.nodeType === Node.TEXT_NODE)[textIndex];
        if (!node || offset > node.length) return null;
        const range = doc.createRange();
        range.setStart(node, offset);
        range.collapse(true);
        const caret = range.getBoundingClientRect();
        x = caret.left + (offset === node.length ? -inset : inset);
        y = caret.top + caret.height / 2;
      } else if (edge) {
        x = bounds.left + bounds.width * (edge === 'right' ? 0.98 : 0.5);
        y = bounds.top + bounds.height * (edge === 'above' ? 0.25 : edge === 'below' ? 0.75 : 0.5);
      }
      const outer = frame.getBoundingClientRect();
      if (document.elementFromPoint(outer.left + x, outer.top + y) !== frame) return null;
      if (!element.contains(doc.elementFromPoint(x, y))) return null;
      return {x: outer.left + x, y: outer.top + y};
    """, item, selector, offset, edge, text_index, source_ready, inset)


def _click_frame(browser, item, selector, offset=None, *, edge=None, text_index=0, source_ready=True, inset=0.2):
    point = WebDriverWait(browser, 7).until(
        lambda driver: _frame_point(driver, item, selector, offset, edge, text_index, source_ready=source_ready, inset=inset),
        f'No interactable preview point for {selector}',
    )
    browser.execute_cdp_cmd('Input.dispatchMouseEvent', {'type': 'mouseMoved', **point, 'button': 'none'})
    settle_native_frames(browser)
    point = _frame_point(browser, item, selector, offset, edge, text_index, source_ready=source_ready, inset=inset)
    assert point, (selector, offset, edge)
    _cdp_click_point(browser, point)


def _tap_frame(browser, item, selector, offset, *, source_ready=True):
    point = WebDriverWait(browser, 7).until(
        lambda driver: _frame_point(driver, item, selector, offset, source_ready=source_ready, inset=2),
        f'No interactable preview point for {selector}',
    )
    browser.execute_cdp_cmd('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [point]})
    browser.execute_cdp_cmd('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})


def _editor_state(browser, item, selector):
    return browser.execute_script("""
      const [item, selector] = arguments;
      const panel = panelNodes.get(item);
      const frame = panel?.querySelector('.file-editor-html-preview');
      const doc = frame?._htmlPreviewDocument;
      const node = doc?.querySelector(selector);
      const host = doc?.querySelector('[data-html-viewedit]');
      const input = panel?.querySelector('.html-viewedit-input');
      const selection = doc?.getSelection();
      const focus = selection?.focusNode;
      return {
        source: panel?._cmView?.state.doc.toString(),
        canonical: panel && fileEditorPanelState(panel).content,
        focus: Boolean(host) && (input ? document.activeElement === input :
          document.activeElement === frame && doc.activeElement === host),
        offset: focus && host?.contains(focus) ? selection.focusOffset : null,
        text: node?.textContent,
        breaks: node?.querySelectorAll('br').length,
        sandbox: frame?.getAttribute('sandbox'),
      };
    """, item, selector)


def _viewedit_diagnostics(driver, item):
    return driver.execute_script("""
      const panel = panelNodes.get(arguments[0]), path = fileEditorPanelPath(panel);
      const state = fileEditorPanelState(panel), frames = [...panel.querySelectorAll('.file-editor-html-preview')];
      const frame = htmlEditorPreviewFrame(panel), first = frames[0];
      return {
        mode: fileEditorPanelMode(panel), writable: state.writable, shareActive: state._shareActive,
        connection: !!panel._shareConnection, cmPath: panel._cmPath, path, readOnly: panel._cmView?.state.readOnly,
        restriction: htmlViewEditRestriction(panel, path, state),
        sourceEditor: !!htmlViewEditSourceEditor(panel, path, state),
        frames: frames.length, current: !!frame, firstLoaded: !!first?._htmlPreviewDocument,
        firstDocMatches: first?._htmlPreviewDocument === first?.contentDocument,
        guide: !!frame?._htmlSplitViewGuide, installed: !!first?._htmlViewEditInputDocument || !!first?._disposeHtmlViewEdit,
        map: !!(frame && htmlSplitSourceMap(panel, frame)),
        snapshot: !!first?._htmlSourceSnapshot, snapshotCurrent: first?._htmlSourceSnapshot?.doc === panel._cmView?.state.doc,
        sourceMatches: first?._htmlPreviewSource === panel._cmView?.state.doc.toString(),
        staged: !!panel.querySelector('.html-preview-staged-frame'),
        status: panel.querySelector('[data-html-viewedit-status]')?.dataset.htmlVieweditStatus,
        restore: !!panel._htmlViewEditRestore, active: document.activeElement?.className,
      };
    """, item)


def _wait_source(browser, item, selector, expected, expected_text=None, *, expected_breaks=None, focused=False):
    state = None

    def read(driver):
        nonlocal state
        state = _editor_state(driver, item, selector)
        if (state['source'] != expected
                or (expected_text is not None and state['text'] != expected_text)
                or (expected_breaks is not None and state['breaks'] != expected_breaks)
                or (focused and not state['focus'])):
            return False
        return state

    try:
        return WebDriverWait(browser, 8).until(read)
    except TimeoutException as error:
        raise AssertionError(f'expected exact source {expected!r}, last state {state!r}') from error


def _wait_caret(wait, item, selector, offset):
    # A click inside an active run moves the frame selection before ViewEdit owns focus.
    # Keys must wait for both, or they reach the script-disabled frame and are dropped.
    state = None

    def read(driver):
        nonlocal state
        state = _editor_state(driver, item, selector)
        return state['focus'] and state['offset'] == offset

    try:
        wait.until(read)
    except TimeoutException as error:
        raise AssertionError(f'expected focused caret at {offset}, last state {state!r}') from error


def _send_frame_keys(browser, *keys):
    browser.switch_to.frame(browser.find_element(By.CSS_SELECTOR, '.file-editor-html-preview'))
    try:
        browser.switch_to.active_element.send_keys(*keys)
    finally:
        browser.switch_to.default_content()


def _enter_frame(browser, *, shift=False):
    _send_frame_keys(browser, *((Keys.SHIFT, Keys.ENTER) if shift else (Keys.ENTER,)))


def _wait_textedit_guide(browser, item, position):
    WebDriverWait(browser, 4).until(lambda driver: driver.execute_script("""
      const [item, position] = arguments;
      const panel = panelNodes.get(item), view = panel._cmView;
      const guides = [...view.scrollDOM.querySelectorAll('.split-codemirror-caret')];
      if (position === null) return guides.every(guide => !guide.getClientRects().length);
      const guide = guides[0], coords = view.coordsAtPos(position);
      const bounds = guide?.getBoundingClientRect(), viewport = view.scrollDOM.getBoundingClientRect();
      return guides.length === 1 && guide.getClientRects().length && coords && !view.hasFocus &&
        !view.contentDOM.contains(guide) && view.state.selection.main.head === position &&
        Math.abs(bounds.left - coords.left) < 3 && Math.abs(bounds.top - coords.top) < 3 &&
        bounds.bottom > viewport.top && bounds.top < viewport.bottom;
    """, item, position), f'TextEdit caret guide did not reach {position!r}')


@pytest.mark.required_browser
@pytest.mark.parametrize('mode', ['split', 'preview'])
def test_html_example_can_be_edited_in_viewedit(browser, isolated_dev_server_factory, request, mode):
    source = (Path(__file__).resolve().parents[1] / 'examples' / '11-html.html').read_text(encoding='utf-8')
    server = isolated_dev_server_factory('html-example-viewedit')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / '11-html.html'
    path.write_bytes(source.encode())
    item = _open_relocation_editor(browser, server, path, view_mode=mode)
    wait = WebDriverWait(browser, 10)
    try:
        _click_frame(browser, item, '#page-title', 2)
        assert _editor_state(browser, item, '#page-title')['focus']
        native_logical_key(browser, 'x')
        expected = source.replace('>YOmux HTML Example</h1>', '>YOxmux HTML Example</h1>')
        _wait_source(browser, item, '#page-title', expected, 'YOxmux HTML Example', focused=True)
        wait.until(lambda driver: driver.find_element(By.CSS_SELECTOR, '.file-editor-save-panel:not([hidden]):enabled')).click()
        wait.until(lambda _: path.read_bytes() == expected.encode())
    finally:
        _assert_preview_scripts_blocked(browser)


def test_html_example_places_caret_when_clicking_the_heading_box(browser, tmp_path):
    source = (Path(__file__).resolve().parents[1] / 'examples' / '11-html.html').read_text(encoding='utf-8')
    item = mount_html_split(browser, tmp_path, source)
    try:
        _click_frame(browser, item, '#page-title', edge='right')
        state = _editor_state(browser, item, '#page-title')
        assert state['focus'], state
    finally:
        _assert_preview_scripts_blocked(browser)


@pytest.mark.parametrize(('selector', 'text'), [('.hero', 'YOmux HTML Example'), ('.card', '&gt;_')])
def test_html_example_places_caret_when_clicking_block_padding(browser, tmp_path, selector, text):
    source = (Path(__file__).resolve().parents[1] / 'examples' / '11-html.html').read_text(encoding='utf-8')
    item = mount_html_split(browser, tmp_path, source)
    try:
        _click_frame(browser, item, '#page-title', 2)
        wait = WebDriverWait(browser, 5)
        point = wait.until(lambda driver: driver.execute_script("""
          const frame = htmlSplitPreviewFrame(panelNodes.get(arguments[0])), doc = frame?.contentDocument;
          const block = doc?.querySelector(arguments[1]);
          if (!block) return null;
          block.scrollIntoView({block: 'nearest'});
          const inner = block.getBoundingClientRect(), outer = frame.getBoundingClientRect();
          const x = inner.left + 11, y = inner.top + 12;
          return doc.elementFromPoint(x, y) === block && {x: outer.left + x, y: outer.top + y};
        """, item, selector))
        _cdp_click_point(browser, point)
        offset = browser.execute_script("""
          return htmlSplitPreviewFrame(panelNodes.get(arguments[0]))._htmlSplitViewGuide()?.head ?? -1;
        """, item)
        start = source.index(text, source.index('<body>'))
        assert start <= offset <= start + len(text), (selector, start, offset)
        native_logical_key(browser, 'x')
        _wait_source(browser, item, selector, source[:offset] + 'x' + source[offset:], focused=True)
    finally:
        _assert_preview_scripts_blocked(browser)


@pytest.mark.required_browser
@pytest.mark.parametrize(('mode', 'sandbox_events', 'width'), [
    ('split', False, 1000),
    ('preview', True, 412),
    ('split', True, 412),
])
def test_touch_html_example_places_caret_in_rendered_text_without_the_keyboard_popup(
    browser, tmp_path, isolated_dev_server_factory, mode, sandbox_events, width,
):
    source = (Path(__file__).resolve().parents[1] / 'examples' / '11-html.html').read_text(encoding='utf-8')
    browser.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {
        'width': width, 'height': 800, 'deviceScaleFactor': 1, 'mobile': sandbox_events,
    })
    browser.execute_cdp_cmd('Emulation.setTouchEmulationEnabled', {'enabled': True, 'maxTouchPoints': 1})
    try:
        if sandbox_events:
            server = isolated_dev_server_factory('html-example-touch')
            path = server.paths.home_dir / '11-html.html'
            path.write_bytes(source.encode())
            item = _open_relocation_editor(browser, server, path, view_mode=mode)
        else:
            item = mount_html_split(browser, tmp_path, source, sandbox_events=False, mode=mode)
        wait = WebDriverWait(browser, 5)
        wait.until(lambda driver: driver.execute_script("""
          const panel = panelNodes.get(arguments[0]), pane = fileEditorPanelPreviewPane(panel);
          const bar = pane.querySelector('.file-editor-html-extra-info');
          return fileEditorPanelMode(panel) === arguments[1] && bar?.querySelector('[data-html-viewedit-status="ready"]') &&
            bar.querySelector('[data-html-preview-auth]') && pane.querySelector('[data-html-viewedit-info]');
        """, item, mode))
        assert not browser.find_element(By.CSS_SELECTOR, '.file-editor-html-status-popover').is_displayed()
        for selector, text in [
            ('#page-title', 'YOmux HTML Example'),
            ('.hero p', 'Open this file'),
            ('#page-title', 'YOmux HTML Example'),
        ]:
            _tap_frame(browser, item, selector, 2)
            wait.until(lambda driver: driver.execute_script("""
              const frame = panelNodes.get(arguments[0]).querySelector('iframe'), doc = frame.contentDocument;
              const element = doc.querySelector(arguments[1]), selection = doc.getSelection();
              return (arguments[2] || element.getAttribute('tabindex') === '-1') && element.contains(selection.focusNode) &&
                !doc.querySelector('.topbar a').hasAttribute('tabindex') && (frame._htmlSplitViewGuide()?.head ?? -1) === arguments[3];
            """, item, selector, sandbox_events, source.index(text, source.index('<body>')) + 2), text)
        offset = browser.execute_script("""
          return panelNodes.get(arguments[0]).querySelector('iframe')._htmlSplitViewGuide()?.head ?? -1;
        """, item)
        native_logical_key(browser, 'x')
        expected = source[:offset] + 'x' + source[offset:]
        _wait_source(browser, item, '#page-title', expected, 'YOxmux HTML Example', focused=True)
        wait.until(lambda driver: _editor_state(driver, item, '#page-title')['canonical'] == expected)
    finally:
        try:
            _assert_preview_scripts_blocked(browser)
            browser.get('about:blank')
        finally:
            browser.execute_cdp_cmd('Emulation.setTouchEmulationEnabled', {'enabled': False})
            browser.execute_cdp_cmd('Emulation.clearDeviceMetricsOverride', {})


@pytest.mark.required_browser
@pytest.mark.parametrize(('mode', 'sandbox_events'), [('preview', True), ('split', True), ('preview', False)])
def test_html_viewedit_shows_the_page_while_editing_loads(browser, tmp_path, mode, sandbox_events, request):
    source = (Path(__file__).resolve().parents[1] / 'examples' / '11-html.html').read_text(encoding='utf-8')
    def hold_source_editor(driver):
        driver.execute_script("""
          const original = loadCodeMirrorApi;
          const held = new Promise(resolve => {
            window.__releaseHtmlSourceEditor = () => {
              loadCodeMirrorApi = original;
              resolve(original());
            };
          });
          loadCodeMirrorApi = () => held;
        """)

    wait = WebDriverWait(browser, 8)
    request.addfinalizer(lambda: browser.execute_cdp_cmd(
        'Emulation.setTouchEmulationEnabled', {'enabled': False},
    ))
    try:
        browser.execute_cdp_cmd('Emulation.setTouchEmulationEnabled', {'enabled': True, 'maxTouchPoints': 1})
        item = mount_html_split(browser, tmp_path, source, sandbox_events=sandbox_events,
                                before_mount=hold_source_editor, mode=mode)
        request.addfinalizer(lambda: _assert_preview_scripts_blocked(browser))
        wait.until(lambda driver: driver.execute_script("""
          const panel = panelNodes.get(arguments[0]), pane = fileEditorPanelPreviewPane(panel);
          return !panel._cmView && pane.querySelector('[data-html-viewedit-status]')?.dataset.htmlVieweditStatus === 'loading' &&
            !!pane.querySelector('iframe')?._htmlSplitViewGuide;
        """, item), 'The rendered page and preparing status must both be visible while the editor loads')
        if mode == 'split':
            browser.execute_script('requestFileEditorPanelFocus(arguments[0], {explicit: true})', item)
        if sandbox_events:
            _tap_frame(browser, item, '#page-title', 2, source_ready=False)
        else:
            _click_frame(browser, item, '#page-title', 2, source_ready=False)
        assert not _editor_state(browser, item, '#page-title')['focus']
        assert browser.execute_script("""
          window.__tappedHtmlFrame = panelNodes.get(arguments[0]).querySelector('iframe');
          return document.activeElement === window.__tappedHtmlFrame;
        """, item)
    finally:
        browser.execute_script("window.__releaseHtmlSourceEditor?.(); delete window.__releaseHtmlSourceEditor")
    wait.until(lambda driver: driver.find_elements(By.CSS_SELECTOR, '[data-html-viewedit-status="ready"]'))
    expected = source.replace('>YOmux HTML Example</h1>', '>YOxmux HTML Example</h1>')
    try:
        wait.until(lambda driver: _editor_state(driver, item, '#page-title')['focus'])
    except TimeoutException as error:
        focus = browser.execute_script("""
          const active = document.activeElement, frame = panelNodes.get(arguments[0]).querySelector('iframe');
          return {active: active?.className, frameFocus: frame.contentDocument.hasFocus(),
            caret: frame._htmlSplitViewGuide()?.head ?? -1, offset: frame.contentDocument.getSelection()?.focusOffset,
            sameFrame: frame === window.__tappedHtmlFrame, frames: panelNodes.get(arguments[0]).querySelectorAll('iframe').length,
            problems: htmlViewEditProblemLines(panelNodes.get(arguments[0])),
            logged: jsDebugEvents.filter(event => event.category === 'viewedit').map(event => event.message)};
        """, item)
        raise AssertionError(f'The first tap did not survive readiness in {mode}: {focus}') from error
    native_logical_key(browser, 'x')
    _wait_source(browser, item, '#page-title', expected, 'YOxmux HTML Example', focused=True)


def _forget_preloaded_codemirror(driver):
    # The page preloads the editor while idle. Wait for that, then return to the state of a page
    # whose preload failed, so the editor's own download is the one the test breaks.
    error = driver.execute_async_script("""
      const done = arguments[arguments.length - 1];
      window.__yomuxTestWaitFor(() => editorBundlePreloadPromise !== null, {timeoutMs: 15000})
        .then(() => editorBundlePreloadPromise)
        .then(() => {
          delete window.YOmuxCodeMirror;
          codeMirrorApiPromise = null;
          codeMirrorBundlePromise = null;
          done();
        }, error => done(String(error)));
    """)
    assert error is None, error


def test_html_preview_keeps_its_page_when_the_same_text_arrives_with_crlf_endings(browser, tmp_path):
    item = mount_html_split(browser, tmp_path, '<h1 id=heading>abcd</h1>\n<p>two</p>\n')
    WebDriverWait(browser, 10).until(lambda driver: driver.find_elements(By.CSS_SELECTOR, '[data-html-viewedit-status="ready"]'))
    result = browser.execute_script("""
      const pane = fileEditorPanelPreviewPane(panelNodes.get(arguments[0]));
      const frame = pane.querySelector('.file-editor-html-preview');
      const rendered = renderHtmlPreviewStrategy({container: pane, path: frame._htmlViewEditPath,
        text: frame._htmlPreviewSource.replace(/\\n/g, '\\r\\n'), context: frame._htmlViewEditContext, signature: 'crlf'});
      return {rendered, same: pane.querySelector('.file-editor-html-preview') === frame, staged: Boolean(pane._htmlStagedFrame)};
    """, item)
    # A saved file with \r\n endings is the same page; it must not reload on every render.
    assert result == {'rendered': False, 'same': True, 'staged': False}, result


def test_html_viewedit_recovers_from_a_failed_editor_load_without_reload(browser, tmp_path):
    original = '<h1 id=heading>abcd</h1>'

    def fail_first_bundle_then_retry_quickly(driver):
        _forget_preloaded_codemirror(driver)
        driver.execute_script("""
          htmlViewEditLoadRetryDelaysMs.splice(0, htmlViewEditLoadRetryDelaysMs.length, 200);
          const append = document.head.appendChild;
          document.head.appendChild = function(node) {
            // Fail the first download, as a server too busy to deliver the bundle in time would.
            if (node.tagName === 'SCRIPT' && new URL(node.dataset.bundleUrl || node.src, location.href).pathname.endsWith('/codemirror.js')) {
              document.head.appendChild = append;
              queueMicrotask(() => node.onerror());
              return node;
            }
            return append.call(this, node);
          };
        """)

    item = mount_html_split(browser, tmp_path, original, before_mount=fail_first_bundle_then_retry_quickly)
    wait = WebDriverWait(browser, 10)
    wait.until(lambda driver: driver.find_elements(By.CSS_SELECTOR, '[data-html-viewedit-status="unavailable"]'))
    # No click on Retry and no reload: the automatic retry restores editing.
    wait.until(lambda driver: driver.find_elements(By.CSS_SELECTOR, '[data-html-viewedit-status="ready"]'))
    _click_frame(browser, item, '#heading', 2)
    native_logical_key(browser, 'x')
    _wait_source(browser, item, '#heading', original.replace('abcd', 'abxcd'), 'abxcd', focused=True)


def test_html_viewedit_keeps_preview_on_bundle_failure_and_can_retry(browser, tmp_path):
    original = '<h1 id=heading>abcd</h1>'
    def fail_first_bundle(driver):
        _forget_preloaded_codemirror(driver)
        driver.execute_script("""
          const append = document.head.appendChild;
          document.head.appendChild = function(node) {
            if (node.tagName === 'SCRIPT' && new URL(node.dataset.bundleUrl || node.src, location.href).pathname.endsWith('/codemirror.js')) {
              document.head.appendChild = append;
              queueMicrotask(() => node.onerror());
              return node;
            }
            return append.call(this, node);
          };
        """)

    def fail_first_bundle_without_automatic_retry(driver):
        # This test owns the manual Retry button, so automatic retries stay off.
        driver.execute_script('htmlViewEditLoadRetryDelaysMs.length = 0')
        fail_first_bundle(driver)

    item = mount_html_split(browser, tmp_path, original, before_mount=fail_first_bundle_without_automatic_retry)
    wait = WebDriverWait(browser, 8)
    wait.until(lambda driver: driver.find_elements(By.CSS_SELECTOR, '[data-html-viewedit-status="unavailable"]'))
    wait.until(lambda driver: _editor_state(driver, item, '#heading')['text'] == 'abcd')
    _click_frame(browser, item, '#heading', 2, source_ready=False)
    assert not _editor_state(browser, item, '#heading')['focus']
    trigger = browser.execute_script("""
      const pane = fileEditorPanelPreviewPane(panelNodes.get(arguments[0]));
      const button = pane.querySelector('[data-html-viewedit-info]'), box = button.getBoundingClientRect();
      const x = box.left + box.width / 2, y = box.top + box.height / 2;
      const hit = document.elementFromPoint(x,y);
      return {x,y,hit:hit === button, target:hit?.className, box:box.toJSON(), viewport:[innerWidth,innerHeight],
        pane:pane.getBoundingClientRect().toJSON(), preview:pane.querySelector('iframe').getBoundingClientRect().toJSON()};
    """, item)
    assert trigger['hit'], str(trigger)
    _cdp_click_point(browser, {axis: trigger[axis] for axis in ('x', 'y')})
    geometry = browser.execute_script("""
      const panel = panelNodes.get(arguments[0]), pane = fileEditorPanelPreviewPane(panel);
      const retry = pane.querySelector('[data-html-viewedit-retry]');
      const box = retry.getBoundingClientRect(), x = box.left + box.width / 2, y = box.top + box.height / 2;
      const hit = document.elementFromPoint(x,y);
      return {x,y,ok:hit===retry, box: box.toJSON(), hit:hit?.className, viewport:[innerWidth,innerHeight],
        panel:panel.getBoundingClientRect().toJSON(), pane:pane.getBoundingClientRect().toJSON(), html:retry.outerHTML};
    """, item)
    assert geometry['ok'], geometry
    _cdp_click_point(browser, {axis: geometry[axis] for axis in ('x', 'y')})
    wait.until(lambda driver: driver.find_elements(By.CSS_SELECTOR, '[data-html-viewedit-status="ready"]'))
    assert not browser.find_element(By.CSS_SELECTOR, '[data-html-viewedit-retry]').is_displayed()
    _click_frame(browser, item, '#heading', 2)
    native_logical_key(browser, 'x')
    edited = original.replace('abcd', 'abxcd')
    _wait_source(browser, item, '#heading', edited, 'abxcd', focused=True)
    browser.execute_script("""
      const panel = panelNodes.get(arguments[0]), state = fileEditorPanelState(panel);
      state.writable = false;
      refreshFileEditorWriteAccess(panel, state);
    """, item)
    assert browser.find_elements(By.CSS_SELECTOR, '[data-html-viewedit-status="readonly"]')
    native_logical_key(browser, 'y')
    _wait_source(browser, item, '#heading', edited, 'abxcd')


def test_html_viewedit_status_hover_and_dismiss_preserve_preview_size(browser, tmp_path):
    item = mount_html_split(browser, tmp_path, '<h1 id="heading">Editable</h1><script>window.example = 1</script>')
    wait = WebDriverWait(browser, 5)
    wait.until(lambda driver: driver.find_elements(By.CSS_SELECTOR, '[data-html-viewedit-status="ready"]'))
    trigger = browser.find_element(By.CSS_SELECTOR, '[data-html-viewedit-info]')
    popover = browser.find_element(By.CSS_SELECTOR, '.file-editor-html-status-popover')
    geometry = """
      const frame = htmlSplitPreviewFrame(panelNodes.get(arguments[0]));
      const {top, height} = frame.getBoundingClientRect();
      return {top, height};
    """
    before = browser.execute_script(geometry, item)
    assert not popover.is_displayed()
    x, y = browser.execute_script("""
      const rect = arguments[0].getBoundingClientRect();
      return [rect.left + rect.width / 2, rect.top + rect.height / 2];
    """, trigger)
    browser.execute_cdp_cmd('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': x, 'y': y, 'button': 'none'})
    wait.until(lambda _driver: popover.is_displayed())
    assert 'ViewEdit (Ready to edit). JavaScript is disabled in this sandboxed preview. Open with JavaScript' in popover.text
    assert browser.execute_script(geometry, item) == before
    browser.find_element(By.CSS_SELECTOR, '[data-html-viewedit-info-close]').click()
    assert not popover.is_displayed()
    assert trigger.get_attribute('aria-expanded') == 'false'
    assert browser.execute_script(geometry, item) == before
    _click_frame(browser, item, '#heading', 2)
    native_logical_key(browser, 'x')
    _wait_source(browser, item, '#heading', '<h1 id="heading">Edxitable</h1><script>window.example = 1</script>', 'Edxitable', focused=True)
    _assert_preview_scripts_blocked(browser)


def test_html_viewedit_uses_parent_input_when_the_sandbox_blocks_events(browser, tmp_path):
    original = '<h1 id=heading>abcd</h1><p id=other>Second line</p><!-- keep -->'
    item = mount_html_split(browser, tmp_path, original)
    wait = WebDriverWait(browser, 6)
    wait.until(lambda driver: _editor_state(driver, item, '#heading')['text'] == 'abcd')
    browser.execute_script("""
      window.__htmlEventProbe = htmlPreviewReceivesEvents;
      htmlPreviewReceivesEvents = () => false;
      // Safari can leave an edited page unpainted. Record which code asked for a repaint.
      window.__htmlRepaint = repaintWebKitHtmlPreview;
      window.__htmlEditRepaints = 0;
      repaintWebKitHtmlPreview = frame => {
        if (/ at apply /.test(new Error().stack)) window.__htmlEditRepaints++;
        return window.__htmlRepaint(frame);
      };
    """)
    try:
        browser.find_element(By.CSS_SELECTOR, '[data-editor-mode="preview"]').click()
        _click_frame(browser, item, '#heading', 2)
        wait.until(lambda driver: _editor_state(driver, item, '#heading')['focus'])
        wait.until(lambda driver: driver.find_element(By.CSS_SELECTOR, '.html-viewedit-caret').is_displayed())
        assert browser.execute_script("""
          const caret = document.querySelector('.html-viewedit-caret');
          const animation = caret.getAnimations()[0];
          if (!animation) return false;
          animation.pause();
          try {
            animation.currentTime = 0;
            const shown = getComputedStyle(caret).visibility === 'visible';
            animation.currentTime = animation.effect.getTiming().duration * 0.75;
            return shown && getComputedStyle(caret).visibility === 'hidden';
          } finally {
            animation.play();
          }
        """), 'The primary fallback caret should blink'
        native_logical_key(browser, 'ArrowLeft', ['shift'])
        native_logical_key(browser, 'x')
        edited = original.replace('abcd', 'axcd')
        state = _wait_source(browser, item, '#heading', edited, 'axcd', focused=True)
        assert state['sandbox'] == 'allow-same-origin'
        # The in-place edit repaints the frame, and the one-pixel resize is undone.
        wait.until(lambda driver: driver.execute_script(
            "return window.__htmlEditRepaints > 0 && !document.querySelector('.file-editor-html-preview').style.width"))
        _click_frame(browser, item, '#other', 2)
        wait.until(lambda driver: _editor_state(driver, item, '#other')['offset'] == 2)
        native_logical_key(browser, 'Backspace')
        edited = edited.replace('Second', 'Scond')
        _wait_source(browser, item, '#other', edited, 'Scond line', focused=True)
        browser.switch_to.active_element.send_keys(Keys.ESCAPE)
        wait.until(lambda driver: not _editor_state(driver, item, '#other')['focus'])
        assert not browser.find_element(By.CSS_SELECTOR, '.html-viewedit-caret').is_displayed()
        start = _frame_point(browser, item, '#other', 1)
        end = _frame_point(browser, item, '#other', 4)
        cdp_drag_hold(browser, start, end)
        try:
            assert not _editor_state(browser, item, '#other')['focus'], 'A held drag must retain native selection'
        finally:
            cdp_release(browser, end)
        wait.until(lambda driver: driver.execute_script("return document.getSelection().toString() === 'con'"))
        native_logical_key(browser, 'z')
        _wait_source(browser, item, '#other', edited.replace('Scond', 'Szd'), 'Szd line', focused=True)
    finally:
        browser.execute_script("""
          htmlPreviewReceivesEvents = window.__htmlEventProbe; delete window.__htmlEventProbe;
          repaintWebKitHtmlPreview = window.__htmlRepaint; delete window.__htmlRepaint;
        """)


@pytest.mark.required_browser
@pytest.mark.parametrize(('sandbox_events', 'entity'), [(True, '&amp;'), (False, '&amp')])
def test_html_viewedit_and_textedit_mirror_selections(browser, tmp_path, sandbox_events, entity):
    source = f'<p id="sample">alpha {entity} <em style="display:contents">beta</em> gamma</p>'
    item = mount_html_split(browser, tmp_path, source, sandbox_events=sandbox_events)
    wait = WebDriverWait(browser, 5)
    _click_frame(browser, item, '#sample', 1)
    _wait_textedit_guide(browser, item, source.index('alpha') + 1)
    for _ in range(6):
        native_logical_key(browser, 'ArrowRight', ['shift'], commands=['moveRightAndModifySelection'])
    wait.until(lambda driver: driver.execute_script("""
      const view = panelNodes.get(arguments[0])._cmView;
      const paint = view.scrollDOM.querySelector('.split-editor-selection')?.getBoundingClientRect();
      const first = view.coordsAtPos(arguments[1]), last = view.coordsAtPos(arguments[2]);
      return !view.hasFocus && paint && Math.abs(paint.left - first.left) < 2 &&
        Math.abs(paint.right - last.left) < 2;
    """, item, source.index('alpha') + 1, source.index(entity) + len(entity)), 'ViewEdit selection did not mark its exact HTML source')
    browser.execute_script("""
      panelNodes.get(arguments[0]).querySelector('[data-editor-mode="split"]').focus();
    """, item)
    wait.until(lambda driver: driver.execute_script("""
      const panel = panelNodes.get(arguments[0]), view = panel._cmView;
      return view.state.selection.main.empty && !view.scrollDOM.querySelector('.split-editor-selection');
    """, item), 'Leaving ViewEdit must clear its mirrored selection')

    browser.execute_script("""
      const view = panelNodes.get(arguments[0])._cmView;
      view.focus();
      view.dispatch({selection: {anchor: arguments[1]}});
    """, item, source.index('beta') + 1)
    native_logical_key(browser, 'ArrowRight', ['shift'], commands=['moveRightAndModifySelection'])
    wait.until(lambda driver: driver.execute_script("""
      const panel = panelNodes.get(arguments[0]), frame = htmlSplitPreviewFrame(panel);
      const doc = frame.contentDocument, node = doc.querySelector('em').firstChild, range = doc.createRange();
      range.setStart(node, 1);
      range.setEnd(node, 2);
      const expected = range.getBoundingClientRect(), origin = frame.getBoundingClientRect();
      const rectangles = panel.querySelectorAll('.html-split-selection-layer .split-editor-selection');
      const paint = rectangles[0]?.getBoundingClientRect();
      return panel._cmView.hasFocus && rectangles.length === 1 && paint?.width > 0 &&
        Math.abs(paint.left - origin.left - expected.left) < 2 && Math.abs(paint.width - expected.width) < 2;
    """, item), 'TextEdit selection did not highlight the same character in ViewEdit')
    native_logical_key(browser, 'ArrowRight', commands=['moveRight'])
    wait.until(lambda driver: driver.execute_script("""
      return !panelNodes.get(arguments[0]).querySelector('.split-editor-selection');
    """, item))
    assert _editor_state(browser, item, '#sample')['source'] == source


@pytest.mark.required_browser
def test_html_viewedit_and_textedit_mirror_across_inline_markup(browser, tmp_path):
    source = '<p id="sample">alpha <em>beta</em> <a href="#sample">gamma</a></p>'
    item = mount_html_split(browser, tmp_path, source)
    wait = WebDriverWait(browser, 5)
    begin = wait.until(lambda driver: _frame_point(driver, item, '#sample', 2, inset=1.7))
    end = wait.until(lambda driver: _frame_point(driver, item, '#sample a', 2, inset=1.7))
    cdp_drag_hold(browser, begin, end)
    cdp_release(browser, end)
    selected = browser.execute_script("""
      return htmlSplitPreviewFrame(panelNodes.get(arguments[0])).contentDocument.getSelection().toString();
    """, item)
    assert selected == 'pha beta ga'
    first, last = source.index('alpha') + 2, source.index('gamma') + 2
    wait.until(lambda driver: driver.execute_script("""
      const panel = panelNodes.get(arguments[0]), view = panel._cmView;
      const frame = htmlSplitPreviewFrame(panel), range = frame.contentDocument.getSelection();
      const boxes = [...view.scrollDOM.querySelectorAll('.split-editor-selection')].map(node => node.getBoundingClientRect());
      const start = view.coordsAtPos(arguments[1]), end = view.coordsAtPos(arguments[2]);
      return document.activeElement === frame && range.toString() === 'pha beta ga' &&
        boxes.some(box => Math.abs(box.left - start.left) < 2 && box.top <= start.top + 2 && box.bottom > start.top) &&
        boxes.some(box => Math.abs(box.right - end.left) < 2 && box.top <= end.top + 2 && box.bottom > end.top);
    """, item, first, last))
    browser.execute_script("""
      const view = panelNodes.get(arguments[0])._cmView;
      view.focus();
      view.dispatch({selection: {anchor: arguments[2], head: arguments[1]}});
    """, item, first, last)
    wait.until(lambda driver: driver.execute_script("""
      const panel = panelNodes.get(arguments[0]), frame = htmlSplitPreviewFrame(panel);
      const rects = [...panel.querySelectorAll('.html-split-selection-layer .split-editor-selection')];
      const text = frame.contentDocument.querySelector('a').firstChild;
      const range = frame.contentDocument.createRange();
      range.setStart(text, 0);
      range.setEnd(text, 2);
      const native = range.getBoundingClientRect(), viewport = frame.getBoundingClientRect();
      return panel._cmView.hasFocus && rects.some(rect => {
        const box = rect.getBoundingClientRect();
        return Math.abs(box.left - viewport.left - native.left) < 2 && Math.abs(box.width - native.width) < 2;
      });
    """, item))


@pytest.mark.required_browser
@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
def test_html_textedit_caret_and_selection_reveal_viewedit_at_matching_height(browser, tmp_path, sandbox_events):
    title = 'Odd Orbit Field Notes'
    source = (
        '<html><body><div style="height: 65vh"></div>\n'
        + '<!-- Source-only line -->\n' * 100
        + f'<h1 id="brand">{title}</h1>\n<div style="height: 150vh"></div>\n'
        + '<!-- More source-only lines -->\n' * 100
        + '</body></html>'
    )
    item = mount_html_split(browser, tmp_path, source, sandbox_events=sandbox_events)
    start = source.index(title)
    wait = WebDriverWait(browser, 6)
    wait.until(lambda driver: _frame_point(driver, item, '#brand', 0))
    browser.execute_script("""
      const panel = panelNodes.get(arguments[0]), view = panel._cmView;
      setFileEditorScrollSyncGuardForSource('preview', panel);
      view.scrollDOM.scrollTop = view.lineBlockAt(arguments[1]).top - view.scrollDOM.clientHeight * .2;
    """, item, start)
    settle_native_frames(browser)
    point = browser.execute_script("""
      const view = panelNodes.get(arguments[0])._cmView;
      const rect = view.coordsAtPos(arguments[1], 1);
      return {x: rect.left + .5, y: (rect.top + rect.bottom) / 2};
    """, item, start)
    _cdp_click_point(browser, point)

    def assert_aligned(position, selector, empty):
        def geometry(driver):
            return driver.execute_script("""
              const [item, position, selector, empty] = arguments;
              const panel = panelNodes.get(item), view = panel._cmView, selection = view.state.selection.main;
              const marker = panel.querySelector(selector);
              if (!view.hasFocus || selection.head !== position || selection.empty !== empty ||
                  !marker?.getClientRects().length) return null;
              const text = view.coordsAtPos(position, empty ? 1 : -1);
              return {source: text.top, preview: marker.getBoundingClientRect().top};
            """, item, position, selector, empty)

        result = wait.until(geometry)
        assert abs(result['preview'] - result['source']) < 20, result

    assert_aligned(start, '.html-split-caret', True)
    browser.execute_script("""
      const panel = panelNodes.get(arguments[0]), frame = htmlSplitPreviewFrame(panel);
      setFileEditorScrollSyncGuardForSource('editor', panel);
      frame.contentDocument.scrollingElement.scrollTop = 0;
      rememberFileEditorReflectedScroll(panel, 'preview', 'editor', Number(panel._splitScrollGeneration || 0));
    """, item)
    settle_native_frames(browser)
    for _ in 'Odd Orbit':
        native_logical_key(browser, 'ArrowRight', ['shift'], commands=['moveRightAndModifySelection'])
    assert_aligned(start + len('Odd Orbit'), '.html-split-selection-layer .split-editor-selection', False)


@pytest.mark.required_browser
@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
def test_html_viewedit_caret_reveals_source_at_matching_height(browser, tmp_path, sandbox_events):
    title = 'Odd Orbit Field Notes'
    source = (
        '<html><head><style>header { margin-top: 8vh; font-size: 24px }</style>\n'
        + '<!-- Source-only line -->\n' * 100
        + f'</head><body><header><span id="brand">{title}</span></header>\n'
        + '<!-- More source-only lines -->\n' * 100
        + '</body></html>'
    )
    item = mount_html_split(browser, tmp_path, source, sandbox_events=sandbox_events)
    start = source.index(title)
    _click_frame(browser, item, '#brand', 0)

    def assert_aligned(position):
        _wait_textedit_guide(browser, item, position)
        geometry = browser.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          const frame = htmlSplitPreviewFrame(panel);
          const selection = frame.contentDocument.getSelection();
          const caret = selection.getRangeAt(0).getBoundingClientRect();
          const guide = panel._cmView.scrollDOM.querySelector('.split-codemirror-caret').getBoundingClientRect();
          return {preview: frame.getBoundingClientRect().top + caret.top, source: guide.top,
            collapsed: selection.isCollapsed, scroll: panel._cmView.scrollDOM.scrollTop};
        """, item)
        assert geometry['collapsed'] and abs(geometry['source'] - geometry['preview']) < 20, geometry

    assert_aligned(start)
    native_logical_key(browser, 'x')
    _wait_source(browser, item, '#brand', source[:start] + 'x' + source[start:], 'x' + title, focused=True)
    assert_aligned(start + 1)


@pytest.mark.required_browser
def test_html_viewedit_selection_reveals_source_at_matching_height(browser, tmp_path):
    source = (
        '<html><head><style>h1 { margin-top: 40vh; font-size: 32px }</style></head><body>\n'
        + '<!-- Source-only line -->\n' * 100
        + '<h1 id="sample">The Moss Moon Field Guide</h1>\n'
        + '<!-- More source-only lines -->\n' * 100
        + '</body></html>'
    )
    item = mount_html_split(browser, tmp_path, source)
    wait = WebDriverWait(browser, 5)
    _click_frame(browser, item, '#sample', 0)
    _wait_textedit_guide(browser, item, source.index('The Moss Moon'))
    for _ in 'The Moss Moon':
        native_logical_key(browser, 'ArrowRight', ['shift'], commands=['moveRightAndModifySelection'])
    wait.until(lambda driver: driver.execute_script("""
      const panel = panelNodes.get(arguments[0]);
      const frame = htmlSplitPreviewFrame(panel), doc = frame.contentDocument;
      const selection = doc.getSelection();
      return selection.toString() === 'The Moss Moon' && !!panel._cmView.scrollDOM.querySelector('.split-editor-selection');
    """, item))
    position = browser.execute_script("""
      const panel = panelNodes.get(arguments[0]);
      const frame = htmlSplitPreviewFrame(panel), doc = frame.contentDocument;
      const selected = doc.getSelection().getRangeAt(0).getBoundingClientRect();
      const view = panel._cmView, box = view.scrollDOM.getBoundingClientRect();
      const source = view.scrollDOM.querySelector('.split-editor-selection').getBoundingClientRect();
      return {preview: frame.getBoundingClientRect().top + selected.top, source: source.top,
        editorTop: box.top, editorHeight: box.height, focus: document.activeElement === frame};
    """, item)
    assert position['focus']
    assert position['editorTop'] + position['editorHeight'] * .25 < position['preview'] < position['editorTop'] + position['editorHeight'] * .75
    assert abs(position['source'] - position['preview']) < 20, position


@pytest.mark.required_browser
def test_html_viewedit_caret_does_not_reflow_textedit_wrap(browser, tmp_path):
    sample = (Path(__file__).resolve().parents[1] / 'examples' / '11-html.html').read_text(encoding='utf-8')
    heading = next(line for line in sample.splitlines() if '<h2 id="workspace-title">' in line)
    item = mount_html_split(browser, tmp_path, heading)
    selector = '#workspace-title'
    WebDriverWait(browser, 5).until(lambda driver: _frame_point(driver, item, selector, 8))
    browser.execute_script("""
      const view = panelNodes.get(arguments[0])._cmView;
      const width = (arguments[1] + 5) * view.defaultCharacterWidth;
      Object.assign(view.contentDOM.style, {width: width + 'px', flex: '0 0 auto'});
    """, item, heading.index('workspace</h2>'))
    settle_native_frames(browser)

    def source_rows():
        return browser.execute_script("""
          const view = panelNodes.get(arguments[0])._cmView;
          const start = view.coordsForChar(0).top;
          return Array.from({length: view.state.doc.length}, (_, index) =>
            Math.round(view.coordsForChar(index).top - start));
        """, item)

    rows = source_rows()
    assert len(set(rows)) > 1, 'The exact source line must wrap in the fixture'
    _click_frame(browser, item, selector, 8)
    first = heading.index('In the workspace') + 8
    observed = []
    for position in range(first, first + 4):
        _wait_textedit_guide(browser, item, position)
        observed.append(source_rows())
        if position < first + 3:
            _send_frame_keys(browser, Keys.ARROW_RIGHT)
    assert observed == [rows] * 4, 'Moving the ViewEdit caret changed the TextEdit wrap boundary'


def test_html_viewedit_textedit_guide_tracks_native_focus_navigation_and_rebuild(browser, tmp_path):
    source = ('<h1 id=start>abcd</h1><!-- unchanged --><p id=empty style=min-height:2em></p>'
              '<table><tr><td id=cell>a&amp;bcd</td></tr></table>'
              '<button id=control>Control</button><p id=end>uvwxyz</p>')
    item = mount_html_split(browser, tmp_path, source)
    start = source.index('abcd')
    _click_frame(browser, item, '#start', 2)
    _wait_textedit_guide(browser, item, start + 2)
    _send_frame_keys(browser, Keys.ARROW_RIGHT)
    _wait_textedit_guide(browser, item, start + 3)
    _send_frame_keys(browser, Keys.SHIFT, Keys.ARROW_LEFT)
    WebDriverWait(browser, 4).until(lambda driver: driver.execute_script("""
      const view = panelNodes.get(arguments[0])._cmView;
      const mark = view.scrollDOM.querySelector('.split-editor-selection')?.getBoundingClientRect();
      return mark && Math.abs(mark.left - view.coordsAtPos(arguments[1]).left) < 2 &&
        Math.abs(mark.right - view.coordsAtPos(arguments[1] + 1).left) < 2;
    """, item, start + 2))
    native_logical_key(browser, 'x')
    source = source.replace('abcd', 'abxd')
    _wait_source(browser, item, '#start', source, 'abxd', focused=True)
    _wait_textedit_guide(browser, item, start + 3)
    _enter_frame(browser)
    source = source.replace('abxd', 'abx<br>d')
    _wait_source(browser, item, '#start', source, 'abxd', focused=True)
    _wait_textedit_guide(browser, item, start + len('abx<br>'))

    _click_frame(browser, item, '#empty')
    _wait_textedit_guide(browser, item, source.index('</p>'))
    _click_frame(browser, item, '#cell', 2)
    _wait_textedit_guide(browser, item, source.index('a&amp;bcd') + len('a&amp;'))
    _click_frame(browser, item, '#control')
    _wait_textedit_guide(browser, item, None)
    _click_frame(browser, item, '#end', 1)
    end = source.index('uvwxyz')
    _wait_textedit_guide(browser, item, end + 1)
    _send_frame_keys(browser, Keys.ESCAPE)
    _wait_textedit_guide(browser, item, None)
    # Reactivation at the same source offset must also repaint the guide.
    _click_frame(browser, item, '#end', 1)
    _wait_textedit_guide(browser, item, end + 1)
    browser.find_element(By.CSS_SELECTOR, '.cm-content').click()
    _wait_textedit_guide(browser, item, None)
    assert _editor_state(browser, item, '#end')['source'] == source


def test_html_viewedit_textedit_guide_reveals_source_below_hidden_markup(browser, tmp_path):
    source = '<p id=first>Alpha</p>\n<!--\n' + 'Hidden source line\n' * 140 + '-->\n<p id=last>Omega</p>'
    item = mount_html_split(browser, tmp_path, source)
    _click_frame(browser, item, '#last', 2)
    _wait_textedit_guide(browser, item, source.index('Omega') + 2)
    _click_frame(browser, item, '#first', 2)
    _wait_textedit_guide(browser, item, source.index('Alpha') + 2)
    assert _editor_state(browser, item, '#first')['source'] == source


@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
def test_html_viewedit_clicking_images_shows_their_exact_source_selection(browser, tmp_path, sandbox_events):
    png = ('<img alt="Pixel" width=40 height=30 src="data:image/png;base64,'
           'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=">')
    vector = '<img alt="Vector" width=40 height=30 src="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\'/%3E">'
    picture = ('<picture><source media="(min-width: 400px)" '
               'srcset="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7">'
               + png + '</picture>')
    svg = '<svg width="60" height="36"><rect width="60" height="36" fill="green"/></svg>'
    graphics = '\n'.join(f'<div>{markup}</div>' for markup in (png, png, vector, picture, svg))
    source = '<!--\n' + 'Hidden source line\n' * 90 + '-->\n<button id=before-graphics>Button</button>' + graphics
    item = mount_html_split(browser, tmp_path, source, sandbox_events=sandbox_events)
    WebDriverWait(browser, 6).until(lambda driver: driver.execute_script("""
      const frame = htmlSplitPreviewFrame(panelNodes.get(arguments[0]));
      const images = [...frame?.contentDocument.images || []];
      return images.length === 4 && images.every(image => image.complete && image.naturalWidth > 0);
    """, item))
    _click_frame(browser, item, '#before-graphics')
    for selector, markup, offset in [
        ('div:nth-of-type(2) > img', png, source.index(png) + len(png)),
        ('img[alt=Vector]', vector, 0),
        ('picture img', picture, 0),
        ('svg rect', svg, 0),
    ]:
        _click_frame(browser, item, selector)
        start = source.index(markup, offset)
        WebDriverWait(browser, 4).until(lambda driver: driver.execute_script("""
          const [item, start, markup, source] = arguments;
          const panel = panelNodes.get(item), cm = panel._cmView;
          const doc = cm.state.doc, selection = cm.state.selection.main;
          const viewport = cm.scrollDOM.getBoundingClientRect();
          const mark = [...cm.dom.querySelectorAll('.cm-selectionBackground')].find(node => {
            const bounds = node.getBoundingClientRect();
            return bounds.width > 0 && bounds.top < viewport.bottom && bounds.bottom > viewport.top &&
              getComputedStyle(node).backgroundColor !== 'rgba(0, 0, 0, 0)';
          });
          return !!mark && !cm.hasFocus && selection.from === start &&
            doc.sliceString(selection.from, selection.to) === markup && doc.toString() === source;
        """, item, start, markup, source), f'No visible matching source highlight for {selector}')


def test_html_viewedit_maps_repeated_text_after_comment_to_its_own_source(browser, tmp_path):
    source = '<p id=sample>&copy foo<!-- keep -->copy foo</p>'
    item = mount_html_split(browser, tmp_path, source)
    _click_frame(browser, item, '#sample', 2, text_index=1)
    assert _editor_state(browser, item, '#sample')['focus']
    native_logical_key(browser, 'x')
    expected = '<p id=sample>&copy foo<!-- keep -->coxpy foo</p>'
    _wait_source(browser, item, '#sample', expected, '© foocoxpy foo', focused=True)
    _wait_textedit_guide(browser, item, expected.index('coxpy') + 3)


@pytest.mark.parametrize('start,end,expected', [(0, 3, 'x'), (3, 0, 'x'), (1, 2, 'axa')])
def test_html_viewedit_typing_replaces_native_heading_selection(browser, tmp_path, start, end, expected):
    template = '<h1 id=heading title="keep">{}</h1><!-- untouched --><p>Next</p>'
    item = mount_html_split(browser, tmp_path, template.format('aaa'))
    wait = WebDriverWait(browser, 5)
    first, last = [wait.until(lambda driver: _frame_point(driver, item, '#heading', offset))
                   for offset in (start, end)]
    cdp_drag_hold(browser, first, last, steps=4)
    cdp_release(browser, last)
    wait.until(lambda driver: driver.execute_script("""
      const frame = panelNodes.get(arguments[0]).querySelector('.file-editor-html-preview');
      return frame.contentDocument.getSelection().toString() === arguments[1];
    """, item, 'a' * abs(end - start)))
    native_logical_key(browser, 'x')
    _wait_source(browser, item, '#heading', template.format(expected), expected)


def test_html_viewedit_select_all_replaces_active_heading_text(browser, tmp_path):
    template = '<h1 id=heading>{}</h1><p>Keep this paragraph</p>'
    item = mount_html_split(browser, tmp_path, template.format('aaa'))
    _click_frame(browser, item, '#heading', 1)
    native_logical_key(browser, 'a', [native_editor_modifier(browser)])
    native_logical_key(browser, 'x')
    _wait_source(browser, item, '#heading', template.format('x'), 'x')


def test_html_footer_refresh_keeps_the_visible_page_until_replacement_loads(browser, tmp_path):
    source = ('<!doctype html><style>main {height:1800px} footer {height:100px}</style>'
              '<main>Top of the page</main><footer><strong>Odd Orbit</strong></footer>')
    item = mount_html_split(browser, tmp_path, source)
    wait = WebDriverWait(browser, 8)
    wait.until(lambda driver: driver.execute_script("""
      const frame = htmlSplitPreviewFrame(panelNodes.get(arguments[0]));
      return frame?.contentDocument.querySelector('footer strong')?.textContent === 'Odd Orbit';
    """, item))
    browser.execute_script("""
      const panel = panelNodes.get(arguments[0]), view = panel._cmView;
      const frame = htmlSplitPreviewFrame(panel), root = frame.contentDocument.scrollingElement;
      root.scrollTop = root.scrollHeight;
      window.__visibleFooterFrame = frame;
      const add = HTMLIFrameElement.prototype.addEventListener;
      HTMLIFrameElement.prototype.addEventListener = function(type, listener, options) {
        if (type !== 'load' || !this.classList.contains('file-editor-html-preview')) {
          return add.call(this, type, listener, options);
        }
        return add.call(this, type, event => {
          window.__publishFooterFrame = () => listener.call(this, event);
        }, options);
      };
      window.__restoreFooterEvents = () => { HTMLIFrameElement.prototype.addEventListener = add; };
      const from = view.state.doc.toString().indexOf('Odd Orbit');
      view.dispatch({changes: {from, to: from + 'Odd'.length, insert: 'New'}});
    """, item)
    try:
        wait.until(lambda driver: driver.execute_script('return !!window.__publishFooterFrame'))
        assert browser.execute_script("""
          const frame = htmlSplitPreviewFrame(panelNodes.get(arguments[0]));
          const footer = frame?.contentDocument.querySelector('footer strong');
          return frame === window.__visibleFooterFrame && footer?.textContent === 'Odd Orbit' &&
            footer.getBoundingClientRect().top < frame.clientHeight && footer.getBoundingClientRect().bottom > 0;
        """, item)
    finally:
        browser.execute_script('window.__restoreFooterEvents(); window.__publishFooterFrame?.()')
    wait.until(lambda driver: driver.execute_script("""
      const frame = htmlSplitPreviewFrame(panelNodes.get(arguments[0]));
      const footer = frame?.contentDocument.querySelector('footer strong');
      return frame !== window.__visibleFooterFrame && footer?.textContent === 'New Orbit' &&
        footer.getBoundingClientRect().top < frame.clientHeight && footer.getBoundingClientRect().bottom > 0;
    """, item))
    _click_frame(browser, item, 'footer strong', 4)
    wait.until(lambda driver: _editor_state(driver, item, 'footer strong')['offset'] == 4)
    native_logical_key(browser, 'X')
    _wait_source(browser, item, 'footer strong', source.replace('Odd Orbit', 'New XOrbit'), 'New XOrbit', focused=True)


@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
def test_html_viewedit_edits_the_visible_page_while_its_replacement_loads(browser, tmp_path, sandbox_events):
    source = '<!doctype html><p id=intro>Top of the page</p><footer><strong>Odd Orbit</strong></footer>'
    item = mount_html_split(browser, tmp_path, source, sandbox_events=sandbox_events)
    wait = WebDriverWait(browser, 8)
    wait.until(lambda driver: driver.execute_script("""
      const panel = panelNodes.get(arguments[0]);
      return !!panel?._cmView && !!htmlSplitPreviewFrame(panel)?.contentDocument.querySelector('#intro');
    """, item))
    browser.execute_script("""
      const panel = panelNodes.get(arguments[0]), view = panel._cmView;
      window.__visibleIntroFrame = htmlSplitPreviewFrame(panel);
      const add = HTMLIFrameElement.prototype.addEventListener;
      HTMLIFrameElement.prototype.addEventListener = function(type, listener, options) {
        if (type !== 'load' || !this.classList.contains('file-editor-html-preview')) {
          return add.call(this, type, listener, options);
        }
        return add.call(this, type, event => {
          window.__publishIntroFrame = () => listener.call(this, event);
        }, options);
      };
      window.__restoreIntroEvents = () => { HTMLIFrameElement.prototype.addEventListener = add; };
      // A TextEdit change elsewhere leaves the visible page one revision behind.
      const from = view.state.doc.toString().indexOf('Odd Orbit');
      view.dispatch({changes: {from, to: from + 'Odd'.length, insert: 'New'}});
    """, item)
    edited = source.replace('Odd Orbit', 'New Orbit').replace('Top of', 'TopX of')
    try:
        wait.until(lambda driver: driver.execute_script('return !!window.__publishIntroFrame'))
        _click_frame(browser, item, '#intro', 3)
        wait.until(lambda driver: _editor_state(driver, item, '#intro')['offset'] == 3)
        native_logical_key(browser, 'X')
        _wait_source(browser, item, '#intro', edited, 'TopX of the page', focused=True)
        assert browser.execute_script("""
          return htmlSplitPreviewFrame(panelNodes.get(arguments[0])) === window.__visibleIntroFrame;
        """, item)
    finally:
        browser.execute_script('window.__restoreIntroEvents(); window.__publishIntroFrame?.()')
    wait.until(lambda driver: driver.execute_script("""
      const doc = htmlSplitPreviewFrame(panelNodes.get(arguments[0]))?.contentDocument;
      return doc?.querySelector('footer strong')?.textContent === 'New Orbit' &&
        doc.querySelector('#intro').textContent === 'TopX of the page';
    """, item))
    assert _editor_state(browser, item, '#intro')['source'] == edited


@pytest.mark.required_browser
@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
def test_html_viewedit_relocates_caret_and_selects_partial_words_across_wrapped_lines(browser, tmp_path, sandbox_events):
    text = ('A pocket-sized report from a tiny green moon where the mushrooms glow, '
            'the rivers hum, and one very small frog runs the weather station.')
    source = '<p id="report" style="margin-top:110px;width:285px;font:17px/1.65 sans-serif">' + text + '</p>'
    item = mount_html_split(browser, tmp_path, source, sandbox_events=sandbox_events)
    wait = WebDriverWait(browser, 5)
    start, middle, end = (text.index(word) + 2 for word in ('report', 'tiny', 'rivers'))
    for offset in (start, middle, start):
        _click_frame(browser, item, '#report', offset)
        wait.until(lambda driver: _editor_state(driver, item, '#report')['offset'] == offset)
    if not sandbox_events:
        assert browser.execute_async_script("""
          const [item, done] = arguments;
          const panel = panelNodes.get(item), frame = htmlSplitPreviewFrame(panel);
          const input = panel.querySelector('.html-viewedit-input'), host = frame._htmlViewEditHost;
          input.blur();
          // WebKit runs microtasks after focusout and before it finishes focusing the iframe.
          queueMicrotask(() => {
            const kept = host?.isConnected && frame._htmlViewEditHost === host;
            if (kept) input.focus();
            done(kept);
          });
        """, item)
    for anchor, head in ((start, end), (end, start)):
        if anchor == end:
            # Collapse the previous range before starting the reverse gesture.
            native_logical_key(browser, 'ArrowLeft', commands=['moveLeft'])
        _click_frame(browser, item, '#report', anchor, inset=1.5)
        try:
            wait.until(lambda driver: _editor_state(driver, item, '#report')['offset'] == anchor)
        except TimeoutException as error:
            raise AssertionError(f'Click before drag {anchor} to {head}: {_editor_state(browser, item, "#report")}') from error
        first, last = (wait.until(lambda driver: _frame_point(driver, item, '#report', offset, inset=1.5))
                       for offset in (anchor, head))
        assert abs(first['y'] - last['y']) > 17
        cdp_drag_hold(browser, first, last, steps=12)
        cdp_release(browser, last)
        wait.until(lambda driver: driver.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          const frame = htmlSplitPreviewFrame(panel), input = panel.querySelector('.html-viewedit-input');
          const selection = frame.contentDocument.getSelection();
          const hasFocus = input ? document.activeElement === input : document.activeElement === frame;
          return hasFocus && selection.anchorOffset === arguments[1] && selection.focusOffset === arguments[2] &&
            selection.toString() === arguments[3];
        """, item, anchor, head, text[start:end]), f'Drag {anchor} to {head} did not activate the selection')
    assert _editor_state(browser, item, '#report')['source'] == source
    native_logical_key(browser, 'X')
    replacement = text[:start] + 'X' + text[end:]
    _wait_source(browser, item, '#report', source.replace(text, replacement), replacement, focused=True)


@pytest.mark.parametrize('whitespace', ['normal', 'pre-wrap'])
def test_html_viewedit_preserves_authored_whitespace_layout(browser, tmp_path, whitespace):
    text = '111\n  222\n  333'
    template = '<!doctype html><div id=example style="white-space:{}">{{}}</div>'.format(whitespace)
    item = mount_html_split(browser, tmp_path, template.format(text))

    def rows(driver):
        return driver.execute_script("""
          const panel = panelNodes.get(arguments[0]), frame = htmlSplitPreviewFrame(panel);
          const doc = frame?._htmlPreviewDocument, element = doc?.getElementById('example');
          if (!element) return null;
          const node = (element.querySelector('[data-html-viewedit]') || element).firstChild;
          const range = doc.createRange();
          return ['111', '22x', '333'].map((word, index) => {
            const offset = node.data.indexOf(index === 1 && !node.data.includes(word) ? '222' : word);
            range.setStart(node, offset);
            range.collapse(true);
            return Math.round(range.getBoundingClientRect().top);
          });
        """, item)

    initial = WebDriverWait(browser, 5).until(rows)
    assert len(set(initial)) == (1 if whitespace == 'normal' else 3)
    _click_frame(browser, item, '#example', text.index('222') + 2)
    assert rows(browser) == initial
    native_logical_key(browser, 'x')
    updated = text.replace('222', '22x2')
    _wait_source(browser, item, '#example', template.format(updated), updated, focused=True)
    assert rows(browser) == initial


@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
def test_html_viewedit_left_right_crosses_inline_markup(browser, tmp_path, sandbox_events):
    source = '<p id=example>one <b>two</b><!-- keep --> three</p>'
    item = mount_html_split(browser, tmp_path, source, sandbox_events=sandbox_events)
    _click_frame(browser, item, '#example', 2)
    _wait_textedit_guide(browser, item, source.index('one') + 2)
    native_logical_key(browser, 'ArrowRight')
    native_logical_key(browser, 'ArrowRight')
    _wait_textedit_guide(browser, item, source.index('<b>'))
    native_logical_key(browser, 'ArrowRight')
    _wait_textedit_guide(browser, item, source.index('two'))
    native_logical_key(browser, 'x')
    edited = source.replace('two', 'xtwo')
    _wait_source(browser, item, '#example', edited, 'one xtwo three', focused=True)
    native_logical_key(browser, 'ArrowLeft')
    native_logical_key(browser, 'ArrowLeft')
    _wait_textedit_guide(browser, item, edited.index('<b>'))
    native_logical_key(browser, 'y')
    _wait_source(browser, item, '#example', edited.replace(' <b>', ' y<b>'), 'one yxtwo three', focused=True)


def test_html_viewedit_up_down_moves_between_rendered_text_runs(browser, tmp_path):
    source = (
        '<style>body{font:16px monospace}table{border-spacing:0}td{padding:0}</style>'
        '<p id=first>abcdef<br>gh</p><hr><!-- hidden -->'
        '<p style="visibility:hidden">Unreachable</p><p id=last>mnopqr</p>'
        '<table><tr><td>stuvwx</td></tr><tr><td>yzabcd</td></tr></table>'
    )
    item = mount_html_split(browser, tmp_path, source)
    _click_frame(browser, item, '#first', 4)
    down = [('gh', 2), ('mnopqr', 4), ('stuvwx', 4), ('yzabcd', 4)]
    moves = [(Keys.ARROW_DOWN, row) for row in down] + [(Keys.ARROW_UP, row) for row in reversed([('abcdef', 4), *down[:-1]])]
    for key, (text, offset) in moves:
        _send_frame_keys(browser, key)
        WebDriverWait(browser, 4).until(lambda driver: driver.execute_script("""
          const panel = panelNodes.get(arguments[0]), frame = htmlSplitPreviewFrame(panel);
          const selection = frame.contentDocument.getSelection();
          return selection.focusNode?.data === arguments[1] && selection.focusOffset === arguments[2] &&
            frame.contentDocument.activeElement.hasAttribute('data-html-viewedit') &&
            panel._cmView.state.doc.toString() === arguments[3];
        """, item, text, offset, source), f'Caret did not move to {text} at offset {offset}')
    native_logical_key(browser, 'x')
    _wait_source(browser, item, '#first', source.replace('abcdef', 'abcdxef'), 'abcdxefgh')


def test_html_viewedit_up_down_moves_across_wrapped_lines(browser, tmp_path):
    source = '<p id=wrap style="font:16px monospace;width:4ch;word-break:break-all">abcdefghijkl</p>'
    item = mount_html_split(browser, tmp_path, source)
    _click_frame(browser, item, '#wrap', 2)
    for key, offset in [(Keys.ARROW_DOWN, 6), (Keys.ARROW_DOWN, 10), (Keys.ARROW_UP, 6), (Keys.ARROW_UP, 2)]:
        _send_frame_keys(browser, key)
        WebDriverWait(browser, 4).until(
            lambda driver: (state := _editor_state(driver, item, '#wrap'))['offset'] == offset
            and state['focus'] and state['source'] == source,
            f'Caret did not move to wrapped offset {offset}',
        )


def test_html_viewedit_up_down_reveals_offscreen_rendered_lines(browser, tmp_path):
    source = '<p id=top>abcdef</p><p id=bottom style="margin-top:150vh">abcdef</p>'
    item = mount_html_split(browser, tmp_path, source)
    _click_frame(browser, item, '#top', 2)
    for key, target in [(Keys.ARROW_DOWN, '#bottom'), (Keys.ARROW_UP, '#top')]:
        _send_frame_keys(browser, key)
        WebDriverWait(browser, 4).until(lambda driver: driver.execute_script("""
          const panel = panelNodes.get(arguments[0]), frame = htmlSplitPreviewFrame(panel);
          const doc = frame.contentDocument, node = doc.querySelector(arguments[1]);
          const selection = doc.getSelection(), rect = node.getBoundingClientRect();
          return node.contains(selection.focusNode) && selection.focusOffset === 2 &&
            rect.top < frame.clientHeight && rect.bottom > 0;
        """, item, target))


def test_html_viewedit_native_text_at_start_middle_and_end(browser, tmp_path):
    template = "<!DOCTYPE html>\n<HTML LANG=en><body><!-- keep -->\n<p id=example title='untouched'>%s</p>\n</body></HTML>\n"
    original = template % 'abcd'
    item = mount_html_split(browser, tmp_path, original)
    _click_frame(browser, item, '#example', 2)
    initial = WebDriverWait(browser, 5).until(
        lambda driver: state if (state := _editor_state(driver, item, '#example'))['focus'] else False
    )
    assert initial['offset'] == 2, initial
    cases = [
        ('x', 'abxcd'),
        ('Backspace', 'abcd'),
        ('Delete', 'abd'),
        ('Home', 'abd'),
        ('Backspace', 'abd'),
        ('y', 'yabd'),
        ('End', 'yabd'),
        ('Delete', 'yabd'),
        ('z', 'yabdz'),
    ]
    for key, expected in cases:
        native_logical_key(browser, key)
        state = _wait_source(browser, item, '#example', template % expected, expected)
        assert state['focus'] and state['sandbox'] == 'allow-same-origin', (key, state)


@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
def test_touch_html_editor_keyboard_targets_the_focused_surface(browser, tmp_path, sandbox_events):
    source = '<p id=example>abcd</p>'
    browser.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {
        'width': 1000, 'height': 740, 'deviceScaleFactor': 1, 'mobile': False,
    })
    browser.execute_cdp_cmd('Emulation.setTouchEmulationEnabled', {'enabled': True, 'maxTouchPoints': 1})
    try:
        item = mount_html_split(browser, tmp_path, source, sandbox_events=sandbox_events)
        _click_frame(browser, item, '#example', 2)
        _wait_textedit_guide(browser, item, source.index('abcd') + 2)
        wait = WebDriverWait(browser, 4)
        launcher = '.file-editor-panel .mobile-terminal-key-launcher'
        def tap(selector):
            snapshot = None
            def point_for(driver):
                nonlocal snapshot
                snapshot = driver.execute_script("""
                  const panel = panelNodes.get(arguments[0]), button = panel.querySelector(arguments[1]);
                  const box = button?.getBoundingClientRect();
                  if (!box?.width || button.hidden) return {button: button?.outerHTML};
                  const x = box.left + box.width / 2, y = box.top + box.height / 2;
                  const hit = document.elementFromPoint(x, y);
                  return {x, y, hit: hit?.className, ok: hit === button, viewport: [innerWidth, innerHeight]};
                """, item, selector)
                return snapshot.get('ok') and {axis: snapshot[axis] for axis in ('x', 'y')}
            try:
                point = wait.until(point_for)
            except TimeoutException as error:
                raise AssertionError(f'No touch target for {selector}: {snapshot}') from error
            browser.execute_cdp_cmd('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [point]})
            browser.execute_cdp_cmd('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
        tap(launcher)
        bar = browser.find_element(By.CSS_SELECTOR, '.file-editor-panel .mobile-terminal-keybar')
        assert bar.is_displayed()
        for action in ('shift', 'arrow-left', 'backspace', 'enter'):
            tap(f'.file-editor-panel [data-terminal-mobile-key="{action}"]')
        edited = source.replace('abcd', 'a<br>cd')
        _wait_source(browser, item, '#example', edited, 'acd', expected_breaks=1, focused=True)
        tap('.file-editor-panel [data-terminal-mobile-close]')
        assert browser.find_element(By.CSS_SELECTOR, launcher).is_displayed()
        browser.find_element(By.CSS_SELECTOR, '[data-editor-mode="preview"]').click()
        wait.until(lambda driver: driver.execute_script("""
          const panel = panelNodes.get(arguments[0]), frame = panel.querySelector('.file-editor-html-preview');
          return fileEditorPanelMode(panel) === 'preview' && Boolean(frame?._focusHtmlViewEditMobile);
        """, item))
        assert not _editor_state(browser, item, '#example')['focus']
        tap(launcher)
        _wait_source(browser, item, '#example', edited, 'acd', focused=True)
        tap('.file-editor-panel [data-terminal-mobile-close]')
        browser.find_element(By.CSS_SELECTOR, '[data-editor-mode="edit"]').click()
        browser.execute_script("""
          const view = panelNodes.get(arguments[0])._cmView;
          view.focus();
          view.dispatch({selection: {anchor: view.state.doc.toString().indexOf('cd') + 1}});
        """, item)
        tap(launcher)
        tap('.file-editor-panel [data-terminal-mobile-key="backspace"]')
        assert browser.execute_script("return panelNodes.get(arguments[0])._cmView.state.doc.toString()", item) == edited.replace('cd', 'd')
        assert browser.execute_script("""
          const panel = panelNodes.get(arguments[0]), state = fileEditorPanelState(panel);
          state.writable = false;
          refreshFileEditorWriteAccess(panel, state);
          return !panel.querySelector('.html-editor-mobile-keyboard');
        """, item)
    finally:
        browser.execute_cdp_cmd('Emulation.setTouchEmulationEnabled', {'enabled': False})
        browser.execute_cdp_cmd('Emulation.clearDeviceMetricsOverride', {})


@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
@pytest.mark.parametrize(('offset', 'key', 'command'), [
    (7, 'Backspace', 'deleteWordBackward'),
    (4, 'Delete', 'deleteWordForward'),
])
def test_html_viewedit_deletes_a_word(browser, tmp_path, sandbox_events, offset, key, command):
    original = '<p id=example>one two</p><!-- preserve -->'
    item = mount_html_split(browser, tmp_path, original, sandbox_events=sandbox_events)
    wait = WebDriverWait(browser, 5)
    modifier = 'alt' if native_editor_modifier(browser) == 'meta' else 'ctrl'
    _click_frame(browser, item, '#example', offset)
    wait.until(lambda driver: (state := _editor_state(driver, item, '#example'))['focus'] and
               state['offset'] == offset)
    native_logical_key(browser, key, [modifier], commands=[command])
    _wait_source(browser, item, '#example', original.replace('one two', 'one '), 'one ', focused=True)


@pytest.mark.parametrize(
    ('tag', 'shift', 'expected'),
    [
        ('p', False, 'ab<br>cd'),
        ('p', True, 'ab<br>cd'),
        ('pre', False, 'ab\ncd'),
        ('td', False, 'ab<br>cd'),
    ],
)
def test_html_viewedit_native_break_preserves_element_and_siblings(browser, tmp_path, tag, shift, expected):
    fragment = f'<{tag} id=example>{{}}</{tag}>'
    if tag == 'td':
        fragment = '<table class=authored><tr>' + fragment + '<td>unchanged</td></tr></table>'
    template = '<!doctype html><p>Before</p><!-- keep -->' + fragment + '<p>After</p>'
    item = mount_html_split(browser, tmp_path, template.format('abcd'))
    _click_frame(browser, item, '#example', 2)
    assert _editor_state(browser, item, '#example')['focus']
    _enter_frame(browser, shift=shift)
    _wait_source(
        browser, item, '#example', template.format(expected), 'ab\ncd' if tag == 'pre' else 'abcd',
        expected_breaks=0 if tag == 'pre' else 1, focused=True,
    )
    native_logical_key(browser, 'x')
    _wait_source(
        browser, item, '#example', template.format(expected[:-2] + 'xcd'), 'ab\nxcd' if tag == 'pre' else 'abxcd',
    )


@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
@pytest.mark.parametrize('offset', [0, 2, 4])
def test_html_viewedit_keeps_input_focused_through_consecutive_line_breaks(browser, tmp_path, sandbox_events, offset):
    source = '<h1 id=example>abcd</h1><!-- keep --><p id=other>Next</p>'
    item = mount_html_split(browser, tmp_path, source, sandbox_events=sandbox_events)
    _click_frame(browser, item, '#example', offset)
    WebDriverWait(browser, 5).until(lambda driver: (state := _editor_state(driver, item, '#example'))['focus'] and
                                  state['offset'] == offset)
    frame = browser.find_element(By.CSS_SELECTOR, '.file-editor-html-preview')
    for key in ('Enter', 'Enter', 'x', 'y', 'z'):
        native_logical_key(browser, key)
    text = 'abcd'
    edited = text[:offset] + '<br><br>xyz' + text[offset:]
    expected = source.replace(text, edited)
    _wait_source(browser, item, '#example', expected, edited.replace('<br>', ''), expected_breaks=2, focused=True)
    assert browser.find_element(By.CSS_SELECTOR, '.file-editor-html-preview') == frame
    _click_frame(browser, item, '#other', 1)
    _wait_textedit_guide(browser, item, expected.index('Next') + 1)
    _wait_source(browser, item, '#example', expected, edited.replace('<br>', ''), expected_breaks=2)


def test_html_viewedit_uses_source_history_and_never_executes_preview_script(browser, tmp_path):
    template = (
        '<!doctype html><p id=example>{text}</p><hr class=preserved><p>After</p>'
        '<script>document.body.setAttribute("data-executed", "yes")</script>'
    )
    original = template.format(text='Entity &copy;')
    item = mount_html_split(browser, tmp_path, original)
    try:
        _click_frame(browser, item, '#example', 7)
        assert _editor_state(browser, item, '#example')['focus']
        assert browser.execute_script("""
          const view = panelNodes.get(arguments[0])._cmView;
          return view.state.selection.main.head === view.state.doc.toString().indexOf('&copy;');
        """, item)
        native_logical_key(browser, 'x')
        edited = template.format(text='Entity x&copy;')
        _wait_source(browser, item, '#example', edited, 'Entity x©')
        modifier = native_editor_modifier(browser)
        native_logical_key(browser, 'z', [modifier])
        _wait_source(browser, item, '#example', original, 'Entity ©', focused=True)
        native_logical_key(browser, 'z', [modifier, 'shift'])
        state = _wait_source(browser, item, '#example', edited, 'Entity x©')
        assert state['sandbox'] == 'allow-same-origin'
        assert browser.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          return !panel.querySelector('.file-editor-html-preview').contentDocument.body.hasAttribute('data-executed');
        """, item)
    finally:
        _assert_preview_scripts_blocked(browser)


def test_html_viewedit_native_ime_commits_once(browser, tmp_path):
    original = '<!doctype html><p id=example>abcd</p><!-- keep -->'
    item = mount_html_split(browser, tmp_path, original)
    _click_frame(browser, item, '#example', 2)
    assert _editor_state(browser, item, '#example')['focus']
    for provisional in ('n', 'ni', '你'):
        browser.execute_cdp_cmd('Input.imeSetComposition', {
            'text': provisional, 'selectionStart': len(provisional), 'selectionEnd': len(provisional),
        })
        state = _editor_state(browser, item, '#example')
        assert state['source'] == state['canonical'] == original, state
    browser.execute_cdp_cmd('Input.insertText', {'text': '你'})
    _wait_source(browser, item, '#example', original.replace('abcd', 'ab你cd'), 'ab你cd')
    native_logical_key(browser, 'z', [native_editor_modifier(browser)])
    _wait_source(browser, item, '#example', original, 'abcd')


@pytest.mark.parametrize('while_active', [False, True])
def test_html_viewedit_blocks_writes_when_editor_is_read_only(browser, tmp_path, while_active):
    original = '<!doctype html><p id=example>abcd</p>'
    item = mount_html_split(browser, tmp_path, original)
    if while_active:
        _click_frame(browser, item, '#example', 2)
        assert _editor_state(browser, item, '#example')['focus']
    browser.execute_script('fileEditorPanelState(panelNodes.get(arguments[0])).writable = false', item)
    if not while_active:
        _click_frame(browser, item, '#example', 2)
    native_logical_key(browser, 'x')
    state = _editor_state(browser, item, '#example')
    assert state['source'] == state['canonical'] == original and not state['focus'], state


def test_html_viewedit_adds_text_above_and_below_rule_and_inside_empty_block(browser, tmp_path):
    rule = "<hr id=rule style='min-height:12px'>"
    empty = '<p id=empty style=min-height:2em>{}</p>'
    original = '<!doctype html><!-- keep -->' + rule + empty.format('')
    item = mount_html_split(browser, tmp_path, original)
    _click_frame(browser, item, '#rule', edge='above')
    native_logical_key(browser, 'a')
    expected = original.replace(rule, '<p>a</p>' + rule)
    _wait_source(browser, item, 'p:first-of-type', expected, 'a')
    _click_frame(browser, item, '#rule', edge='below')
    native_logical_key(browser, 'b')
    expected = expected.replace(rule, rule + '<p>b</p>')
    _wait_source(browser, item, 'p:nth-of-type(2)', expected, 'b')
    _click_frame(browser, item, '#empty')
    native_logical_key(browser, 'c')
    expected = expected.replace(empty.format(''), empty.format('c'))
    _wait_source(browser, item, '#empty', expected, 'c')


@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
@pytest.mark.parametrize('placeholder', ['', '<br>'])
def test_html_viewedit_empty_paragraph_shows_a_caret_and_keeps_its_markup(browser, tmp_path, sandbox_events, placeholder):
    original = f'<p id=empty style="min-height:2em">{placeholder}</p><p id=other>Next</p>'
    item = mount_html_split(browser, tmp_path, original, sandbox_events=sandbox_events)
    _click_frame(browser, item, '#empty')
    wait = WebDriverWait(browser, 5)
    wait.until(lambda driver: _editor_state(driver, item, '#empty')['focus'])
    if not sandbox_events:
        assert browser.execute_script("""
          const caret = document.querySelector('.html-viewedit-caret');
          return !caret.hidden && caret.getBoundingClientRect().height > 0;
        """)
    native_logical_key(browser, 'x')
    expected = original.replace(f'>{placeholder}</p>', f'>x{placeholder}</p>')
    _wait_source(browser, item, '#empty', expected, 'x', focused=True)
    _click_frame(browser, item, '#other', 2)
    _wait_textedit_guide(browser, item, expected.index('Next') + 2)
    _wait_source(browser, item, '#empty', expected, 'x')


@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
def test_html_viewedit_view_only_edits_prose_and_leaves_authored_controls_alone(browser, tmp_path, sandbox_events):
    original = '<!doctype html><p id=example>abcd</p><button id=control>Keep button</button>'
    item = mount_html_split(browser, tmp_path, original, sandbox_events=sandbox_events)
    browser.find_element(By.CSS_SELECTOR, '[data-editor-mode="preview"]').click()
    _click_frame(browser, item, '#control', 3)
    native_logical_key(browser, 'x')
    state = _editor_state(browser, item, '#control')
    assert state['source'] == original and state['text'] == 'Keep button' and not state['focus'], state
    _click_frame(browser, item, '#example', 2)
    WebDriverWait(browser, 5).until(lambda driver: _editor_state(driver, item, '#example')['focus'])
    _wait_textedit_guide(browser, item, None)
    native_logical_key(browser, 'x')
    expected = original.replace('abcd', 'abxcd')
    _wait_source(browser, item, '#example', expected, 'abxcd', focused=True)
    _click_frame(browser, item, '#control', 3)
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("""
      const panel = panelNodes.get(arguments[0]), frame = panel.querySelector('.file-editor-html-preview');
      const fallbackCaret = panel.querySelector('.html-viewedit-caret');
      return document.activeElement === frame && frame.contentDocument.activeElement.id === 'control' &&
        (!fallbackCaret || fallbackCaret.hidden);
    """, item))
    _click_frame(browser, item, '#example', 2)
    WebDriverWait(browser, 5).until(lambda driver: _editor_state(driver, item, '#example')['offset'] == 2)
    assert browser.execute_script("""
      const panel = panelNodes.get(arguments[0]);
      return panel._cmView.contentDOM.getClientRects().length === 0;
    """, item)


@pytest.mark.parametrize('initial,next_mode', [('preview', 'split'), ('split', 'preview')])
def test_html_viewedit_mode_switch_keeps_pending_draft_editable(browser, tmp_path, initial, next_mode):
    source = '<h1 id=heading>YOmux HTML Example</h1><p>Open this file</p>'
    item = mount_html_split(browser, tmp_path, source)
    wait = WebDriverWait(browser, 6)
    if initial == 'preview':
        wait.until(lambda driver: driver.find_element(By.CSS_SELECTOR, '[data-editor-mode="preview"]')).click()
    browser.execute_script("""
      const owner = panelNodes.get(arguments[0]);
      window.__htmlOriginalScheduleSource = scheduleCodeMirrorSource;
      scheduleCodeMirrorSource = (panel, path) => {
        window.__htmlOriginalScheduleSource(panel, path);
        if (panel === owner) clearTimeout(panel._cmSerializeTimer);
      };
    """, item)
    try:
        _click_frame(browser, item, '#heading', 2)
        native_logical_key(browser, 'x')
        draft = source.replace('YOmux', 'YOxmux')
        assert _wait_source(browser, item, '#heading', draft, 'YOxmux HTML Example')['canonical'] == source
        browser.find_element(By.CSS_SELECTOR, f'[data-editor-mode="{next_mode}"]').click()
        _click_frame(browser, item, 'p', 3)
        assert _editor_state(browser, item, 'p')['focus'], 'The next ViewEdit must accept the click before serialization'
        native_logical_key(browser, 'x')
        expected = draft.replace('Open this file', 'Opexn this file')
        assert _wait_source(browser, item, 'p', expected, 'Opexn this file', focused=True)['canonical'] == source
    finally:
        browser.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          scheduleCodeMirrorSource = window.__htmlOriginalScheduleSource;
          delete window.__htmlOriginalScheduleSource;
          flushCodeMirrorSource(panel, panel._cmPath);
        """, item)
    assert _editor_state(browser, item, 'p')['canonical'] == expected


def test_html_viewedit_rebases_native_input_after_source_revision_changes(browser, tmp_path):
    original = '<!doctype html><p id=example>abcd</p><p id=other>Prior revision</p>'
    item = mount_html_split(browser, tmp_path, original)
    _click_frame(browser, item, '#example', 2)
    assert _editor_state(browser, item, '#example')['focus']
    _wait_textedit_guide(browser, item, original.index('abcd') + 2)
    expected = original.replace('Prior revision', 'New revision')
    try:
        browser.execute_script("""
          const panel = panelNodes.get(arguments[0]), view = panel._cmView;
          window.__htmlVieweditRender = renderFileEditorPreviewSurface;
          renderFileEditorPreviewSurface = (...args) => args[0] === panel ? false : window.__htmlVieweditRender(...args);
          const from = view.state.doc.toString().indexOf('Prior revision');
          view.dispatch({changes: {from, to: from + 'Prior revision'.length, insert: 'New revision'}});
        """, item)
        state = _editor_state(browser, item, '#example')
        assert state['source'] == expected and state['focus'], state
        # The live text run follows the outside edit, so typing lands at the same caret.
        _wait_textedit_guide(browser, item, original.index('abcd') + 2)
        native_logical_key(browser, 'x')
        expected = expected.replace('abcd', 'abxcd')
        _wait_source(browser, item, '#example', expected, 'abxcd', focused=True)
    finally:
        browser.execute_script("""
          if (!window.__htmlVieweditRender) return;
          const panel = panelNodes.get(arguments[0]);
          renderFileEditorPreviewSurface = window.__htmlVieweditRender;
          delete window.__htmlVieweditRender;
          renderFileEditorPreviewSurface(panel, fileEditorPanelPreviewPane(panel), panel._cmPath,
            panel._cmView.state.doc.toString(), {force: true, context: 'split', state: fileEditorPanelState(panel)});
        """, item)
    _wait_source(browser, item, '#other', expected, 'New revision')


def test_html_viewedit_paste_save_and_reopen_exact_disk_bytes(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('html-viewedit-reopen')
    request.addfinalizer(lambda: browser.get('about:blank'))
    path = server.paths.home_dir / 'viewedit.html'
    original = b"<!DOCTYPE html>\n<HTML lang=en><body><!-- kept -->\n<p id=example class='s'>abcd</p>\n<hr>\n</body></HTML>\n"
    path.write_bytes(original)
    item = _open_relocation_editor(browser, server, path)
    wait = WebDriverWait(browser, 10)
    wait.until(lambda driver: driver.find_element(By.CSS_SELECTOR, '[data-editor-mode="split"]')).click()
    clipboard_origin = urlsplit(browser.current_url)
    origin = f'{clipboard_origin.scheme}://{clipboard_origin.netloc}'
    browser.execute_cdp_cmd('Browser.grantPermissions', {
        'origin': origin, 'permissions': ['clipboardReadWrite', 'clipboardSanitizedWrite'],
    })
    previous = browser.execute_async_script('navigator.clipboard.readText().then(arguments[0])')
    try:
        browser.execute_async_script("navigator.clipboard.writeText('<b>&').then(arguments[0])")
        _click_frame(browser, item, '#example', 2)
        native_logical_key(browser, 'v', [native_editor_modifier(browser)], commands=['paste'])
        expected = original.replace(b'abcd', b'ab&lt;b&gt;&amp;cd')
        _wait_source(browser, item, '#example', expected.decode(), 'ab<b>&cd')
        assert path.read_bytes() == original
        wait.until(lambda driver: driver.find_element(By.CSS_SELECTOR, '.file-editor-save-panel:not([hidden]):enabled')).click()
        wait.until(lambda _: path.read_bytes() == expected)
        reopened = _open_relocation_editor(browser, server, path)
        wait.until(lambda driver: driver.find_element(By.CSS_SELECTOR, '[data-editor-mode="split"]')).click()
        _wait_source(browser, reopened, '#example', expected.decode(), 'ab<b>&cd')
        assert path.read_bytes() == expected
    finally:
        browser.execute_async_script('navigator.clipboard.writeText(arguments[0]).then(arguments[1])', previous)
        browser.execute_cdp_cmd('Browser.resetPermissions', {})


@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
def test_writable_guest_html_viewedit_merges_while_source_sync_is_held(
    browser, isolated_dev_server_factory, tmp_path, sandbox_events,
):
    server = isolated_dev_server_factory('html-viewedit-share', tls=True, auth_bypass=False)
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    path = server.paths.workspace_dir / 'joined.html'
    original = "<!DOCTYPE html><p>First line</p>\n<p id=example title='keep'>Middle &copy;</p><!-- keep -->\n"
    path.write_text(original, encoding='utf-8')
    browser.get(server.base_url + '/login')
    browser.find_element(By.NAME, 'username').send_keys('keivenc')
    browser.find_element(By.NAME, 'password').send_keys('random-password')
    browser.find_element(By.CSS_SELECTOR, 'form[action="/login"] button[type="submit"]').click()
    host_wait = WebDriverWait(browser, 15)
    host_wait.until(lambda driver: driver.execute_script(
        "return !!window.YOmuxShare && typeof openFileEditorPane === 'function'"
    ))
    host_item = browser.execute_async_script("""
      const [path, done] = arguments;
      (async () => {
        const item = await openFileInEditor(path, 'joined.html', {viewMode: 'edit', userInitiated: true});
        await openFileEditorPane(path, {item});
        return item;
      })().then(done, error => done({error: String(error)}));
    """, str(path))
    assert isinstance(host_item, str), host_item
    created = _share_json(browser, '/api/share', {
        'guest': 'HtmlWriter', 'minutes': 5, 'limit': 1,
        'resources': [{'kind': 'file', 'path': str(path), 'writable': True, 'saved_content': original}],
    })
    assert created['status'] == 201, created
    host_wait.until(lambda driver: driver.execute_script(
        'return !!panelNodes.get(arguments[0])?._shareConnection', host_item
    ))
    lease = WebDriverLease.acquire(lambda: new_chrome_driver(profile_dir=tmp_path / 'html-guest'))
    guest = lease.driver
    try:
        guest.get(created['body']['url'])
        guest_wait = WebDriverWait(guest, 15)
        if not sandbox_events:
            guest_wait.until(lambda driver: driver.execute_script("return typeof htmlPreviewReceivesEvents === 'function'"))
            guest.execute_script("htmlPreviewReceivesEvents = () => false")
        _workspace_mode(guest, 'split')
        _workspace_mode(browser, 'split')
        guest_item = guest_wait.until(lambda driver: driver.execute_script("""
          return [...panelNodes].find(([, panel]) => panel._shareConnection && panel._cmView &&
            panel.querySelector('.file-editor-html-preview')?._htmlPreviewDocument)?.[0] || null;
        """))
        _click_frame(guest, guest_item, '#example', len('Middle'))
        guest_wait.until(lambda driver: _editor_state(driver, guest_item, '#example')['focus'])
        _wait_textedit_guide(guest, guest_item, original.index('Middle') + len('Middle'))
        host_wait.until(lambda driver: driver.execute_script("""
          const panel = panelNodes.get(arguments[0]), view = panel._cmView;
          const frame = htmlSplitPreviewFrame(panel);
          const preview = panel.querySelector('.html-split-caret[data-share-caret-name="HtmlWriter"][data-share-caret-primary]');
          const source = panel.querySelector('.share-remote-caret[data-share-caret-name="HtmlWriter"][data-share-caret-follower]');
          const labels = [...document.querySelectorAll('.share-caret-label:not([hidden])')]
            .filter(label => label.textContent === 'HtmlWriter');
          if (!preview || preview.hidden || !source || labels.length !== 2) return false;
          const range = frame.contentDocument.createRange();
          range.setStart(frame.contentDocument.querySelector('#example').firstChild, 6);
          range.collapse(true);
          const target = range.getBoundingClientRect(), pane = frame.getBoundingClientRect();
          const marker = preview.getBoundingClientRect();
          return view.posAtDOM(source) === view.state.doc.toString().indexOf('Middle') + 6 &&
            Math.abs(marker.left - (pane.left + target.left)) < 2 &&
            Math.abs(marker.top - (pane.top + target.top)) < 2 &&
            labels.some(label => {
              const box = label.getBoundingClientRect();
              return box.left >= pane.left && box.left < pane.right && box.bottom <= marker.bottom;
            }) &&
            getComputedStyle(preview, '::before').animationName === 'none' &&
            getComputedStyle(source, '::before').animationName === 'none' &&
            getComputedStyle(source, '::before').opacity === '0.5' &&
            labels.every(label => getComputedStyle(label).backgroundColor === getComputedStyle(preview).color);
        """, host_item))
        browser.execute_script("""
          const view = panelNodes.get(arguments[0])._cmView;
          const start = view.state.doc.toString().indexOf('Middle');
          view.focus();
          view.dispatch({selection: {anchor: start, head: start + 3}});
        """, host_item)
        host_wait.until(lambda driver: driver.execute_script("""
          return !!panelNodes.get(arguments[0]).querySelector('.html-split-selection-layer .split-editor-selection');
        """, host_item))
        guest_wait.until(lambda driver: driver.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          return !!panel.querySelector('.html-split-selection-layer .share-remote-selection') &&
            !!panel.querySelector('.cm-content .share-remote-selection') &&
            !!panel.querySelector('.html-split-caret[data-share-caret-name="keivenc"][data-share-caret-follower]');
        """, guest_item))
        _click_frame(guest, guest_item, '#example', 0)
        guest_wait.until(lambda driver: _editor_state(driver, guest_item, '#example')['offset'] == 0)
        first, last = (_frame_point(guest, guest_item, '#example', offset, inset=1.5) for offset in (0, 3))
        cdp_drag_hold(guest, first, last, steps=8)
        cdp_release(guest, last)
        guest_wait.until(lambda driver: driver.execute_script("""
          const panel = panelNodes.get(arguments[0]), frame = htmlSplitPreviewFrame(panel);
          return frame.contentDocument.getSelection().toString() === 'Mid' &&
            !!panel._cmView.scrollDOM.querySelector('.split-editor-selection');
        """, guest_item))
        host_wait.until(lambda driver: driver.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          const marks = [...panel.querySelectorAll('.cm-content .share-remote-selection')];
          return marks.map(mark => mark.textContent).join('') === 'Mid' &&
            !!panel.querySelector('.html-split-selection-layer .share-remote-selection');
        """, host_item))
        _click_frame(guest, guest_item, '#example', len('Middle'))
        _wait_caret(guest_wait, guest_item, '#example', len('Middle'))
        hold_html_sync = """
          const originalFetch = window.fetch;
          window.fetch = (input, options) => {
            const target = new URL(String(input), location.href);
            if (!target.pathname.startsWith(shareWorkspace.base) || options?.method !== 'POST' ||
                !options.body || !JSON.parse(options.body).updates?.length) return originalFetch(input, options);
            return new Promise(resolve => {
              window.__releaseHtmlSync = () => {
                window.fetch = originalFetch;
                resolve(originalFetch(input, options));
              };
            });
          };
        """
        guest.execute_script(hold_html_sync)
        try:
            native_logical_key(guest, 'G')
            local = original.replace('Middle', 'MiddleG')
            _wait_source(guest, guest_item, '#example', local, 'MiddleG ©', focused=True)
            guest_wait.until(lambda driver: driver.execute_script("""
              const panel = panelNodes.get(arguments[0]), frame = htmlSplitPreviewFrame(panel);
              const marker = panel.querySelector('.html-split-caret[data-share-caret-name="keivenc"]');
              if (!frame?._htmlViewEditHost || !marker || marker.hidden) return false;
              const range = frame.contentDocument.createRange();
              range.setStart(frame._htmlViewEditHost.firstChild, 3);
              range.collapse(true);
              return Math.abs(marker.getBoundingClientRect().left -
                (frame.getBoundingClientRect().left + range.getBoundingClientRect().left)) < 2;
            """, guest_item))
            assert path.read_bytes() == original.encode()
            guest_wait.until(lambda driver: driver.execute_script('return !!window.__releaseHtmlSync'))
            browser.execute_script("""
              const view = panelNodes.get(arguments[0])._cmView;
              view.focus();
              view.dispatch({selection: {anchor: view.state.doc.toString().indexOf('Middle')}});
            """, host_item)
            native_logical_key(browser, 'H')
            host_wait.until(lambda _: path.read_bytes() == original.replace('Middle', 'HMiddle').encode())
        finally:
            guest.execute_script('window.__releaseHtmlSync?.()')
        merged = original.replace('Middle', 'HMiddleG')
        guest_wait.until(lambda _: path.read_bytes() == merged.encode())
        _wait_source(guest, guest_item, '#example', merged, 'HMiddleG ©')
        host_wait.until(lambda driver: driver.execute_script(
            'return panelNodes.get(arguments[0])._cmView.state.doc.toString() === arguments[1]', host_item, merged
        ))
        _click_frame(guest, guest_item, '#example', 2)
        _wait_caret(guest_wait, guest_item, '#example', 2)
        remote_comment = '<!-- owner revision -->'
        updated_source = merged.replace('HMiddleG', remote_comment + 'HMiddleG')
        guest.execute_script("""
          const panel = panelNodes.get(arguments[0]), render = renderFileEditorPreviewSurface;
          const current = htmlSplitPreviewFrame(panel);
          renderFileEditorPreviewSurface = (owner, ...args) => owner === panel ? false : render(owner, ...args);
          window.__releaseSharedHtmlPreview = () => {
            renderFileEditorPreviewSurface = render;
            render(panel, current.parentElement, fileEditorPanelPath(panel), panel._cmView.state.doc.toString(),
              {force: true, context: fileEditorPanelMode(panel), state: fileEditorPanelState(panel)});
          };
        """, guest_item)
        try:
            browser.execute_script("""
              const [item, comment] = arguments, view = panelNodes.get(item)._cmView;
              view.dispatch({changes: {from: view.state.doc.toString().indexOf('HMiddleG'), insert: comment}});
            """, host_item, remote_comment)
            guest_wait.until(lambda driver: driver.execute_script("""
              return panelNodes.get(arguments[0])._cmView.state.doc.toString() === arguments[1];
            """, guest_item, updated_source))
            native_logical_key(guest, 'Y')
            updated_source = updated_source.replace('HMiddleG', 'HMYiddleG')
            _wait_source(guest, guest_item, '#example', updated_source, 'HMYiddleG ©', focused=True)
            native_logical_key(guest, 'ArrowLeft', ['shift'], commands=['moveLeftAndModifySelection'])
            host_wait.until(lambda driver: driver.execute_script("""
              return panelNodes.get(arguments[0])._cmView.state.doc.toString() === arguments[1];
            """, host_item, updated_source))
            browser.execute_script("""
              const view = panelNodes.get(arguments[0])._cmView;
              view.dispatch({changes: {from: view.state.doc.toString().indexOf('HMYiddleG') + 2, insert: '&amp;'}});
            """, host_item)
            updated_source = updated_source.replace('HMYiddleG', 'HM&amp;YiddleG')
            _wait_source(guest, guest_item, '#example', updated_source, 'HM&YiddleG ©', focused=True)
        finally:
            guest.execute_script('window.__releaseSharedHtmlPreview()')
        _wait_source(guest, guest_item, '#example', updated_source, 'HM&YiddleG ©', focused=True)
        assert guest.execute_script("""
          const frame = htmlSplitPreviewFrame(panelNodes.get(arguments[0]));
          return frame.contentDocument.getSelection().toString();
        """, guest_item) == 'Y'
        native_logical_key(guest, 'ArrowRight', commands=['moveRight'])
        native_logical_key(guest, 'Z')
        updated_source = updated_source.replace('YiddleG', 'YZiddleG')
        guest_wait.until(lambda _: path.read_bytes() == updated_source.encode())
        _workspace_mode(guest, 'edit')
        guest.execute_script(hold_html_sync)
        try:
            guest.execute_script("""
              const view = panelNodes.get(arguments[0])._cmView;
              view.focus();
              view.dispatch({selection: {anchor: view.state.doc.toString().indexOf('YZiddleG')}});
            """, guest_item)
            native_logical_key(guest, 'T')
            draft = updated_source.replace('YZiddleG', 'TYZiddleG')
            snapshot = guest.execute_script("""
              const panel = panelNodes.get(arguments[0]);
              panel.querySelector('[data-editor-mode="preview"]').click();
              return panel.querySelector('.file-editor-html-preview')?._htmlPreviewSource;
            """, guest_item)
            assert snapshot == draft
            guest_wait.until(lambda driver: driver.execute_script('return !!window.__releaseHtmlSync'))
            for mode in ('preview', 'edit', 'split', 'edit', 'preview'):
                _workspace_mode(guest, mode)
                if mode == 'edit':
                    continue
                _wait_source(guest, guest_item, '#example', draft, 'HM&TYZiddleG ©')
                _click_frame(guest, guest_item, '#example', 4)
                _wait_caret(guest_wait, guest_item, '#example', 4)
            native_logical_key(guest, 'Q')
            draft = draft.replace('TYZiddleG', 'TQYZiddleG')
            _wait_source(guest, guest_item, '#example', draft, 'HM&TQYZiddleG ©', focused=True)
        finally:
            guest.execute_script('window.__releaseHtmlSync?.()')
        guest_wait.until(lambda _: path.read_bytes() == draft.encode())
        for mode in ('preview', 'edit', 'split', 'edit', 'preview', 'split'):
            _workspace_mode(browser, mode)
            host_wait.until(lambda driver: driver.execute_script("""
              return fileEditorPanelMode(panelNodes.get(arguments[0])) === arguments[1];
            """, host_item, mode), f'The owner could not switch the shared HTML to {mode}')
            if mode != 'edit':
                _click_frame(browser, host_item, '#example', 2)
                host_wait.until(lambda driver: _editor_state(driver, host_item, '#example')['offset'] == 2)
        grant = created['body']['share']
        updated = _share_json(browser, '/api/share?id=' + grant['id'], {'revision': grant['revision'],
            'resources': [{'id': grant['resources'][0]['id'], 'writable': False}]}, method='PATCH')
        assert updated['status'] == 200, updated
        guest_wait.until(lambda driver: driver.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          return panel?._cmView?.state.readOnly && !panel._shareConnection;
        """, guest_item))
        _workspace_mode(guest, 'preview')
        host_wait.until(lambda driver: driver.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          return !panel?._shareConnection && panel.querySelector('[data-html-viewedit-status="ready"]');
        """, host_item))
        _click_frame(browser, host_item, '#example', 2)
        guest_wait.until(lambda driver: driver.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          const marker = panel?.querySelector('.html-split-caret[data-share-caret-name="keivenc"][data-share-caret-primary]');
          return marker && !marker.hidden && [...document.querySelectorAll('.share-caret-label:not([hidden])')]
            .some(label => label.textContent === 'keivenc');
        """, guest_item))
    finally:
        try:
            guest.get('about:blank')
        finally:
            result = lease.retire()
            browser.get('about:blank')
            assert result.proven_gone, result


@pytest.mark.parametrize('mode', ['split', 'preview'])
def test_guest_html_viewedit_accepts_clicks_after_read_only_grant_becomes_writable(
    browser, isolated_dev_server_factory, tmp_path, mode,
):
    server = isolated_dev_server_factory('html-viewedit-upgrade', tls=True, auth_bypass=False)
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    path = server.paths.workspace_dir / 'upgrade.html'
    original = '<!DOCTYPE html><p id=example>Middle line</p>\n'
    path.write_text(original, encoding='utf-8')
    browser.get(server.base_url + '/login')
    browser.find_element(By.NAME, 'username').send_keys('keivenc')
    browser.find_element(By.NAME, 'password').send_keys('random-password')
    browser.find_element(By.CSS_SELECTOR, 'form[action="/login"] button[type="submit"]').click()
    host_wait = WebDriverWait(browser, 15)
    host_wait.until(lambda driver: driver.execute_script(
        "return !!window.YOmuxShare && typeof openFileEditorPane === 'function'"
    ))
    host_item = browser.execute_async_script("""
      const [path, done] = arguments;
      (async () => {
        const item = await openFileInEditor(path, 'upgrade.html', {viewMode: 'edit', userInitiated: true});
        await openFileEditorPane(path, {item});
        return item;
      })().then(done, error => done({error: String(error)}));
    """, str(path))
    assert isinstance(host_item, str), host_item
    created = _share_json(browser, '/api/share', {
        'guest': 'HtmlWriter', 'minutes': 5, 'limit': 1,
        'resources': [{'kind': 'file', 'path': str(path), 'writable': False}],
    })
    assert created['status'] == 201, created
    lease = WebDriverLease.acquire(lambda: new_chrome_driver(profile_dir=tmp_path / 'html-guest'))
    guest = lease.driver
    try:
        guest.get(created['body']['url'])
        guest_wait = WebDriverWait(guest, 15)
        _workspace_mode(guest, mode)
        guest_item = guest_wait.until(lambda driver: driver.execute_script("""
          return [...panelNodes].find(([, panel]) => panel._cmView &&
            panel.querySelector('.file-editor-html-preview')?._htmlPreviewDocument)?.[0] || null;
        """))
        grant = created['body']['share']
        updated = _share_json(browser, '/api/share?id=' + grant['id'], {'revision': grant['revision'],
            'resources': [{'id': grant['resources'][0]['id'], 'writable': True, 'saved_content': original}]},
            method='PATCH')
        assert updated['status'] == 200, updated
        guest_wait.until(lambda driver: driver.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          return !!panel?._shareConnection && !panel._cmView.state.readOnly;
        """, guest_item))
        _click_frame(guest, guest_item, '#example', 3)
        try:
            WebDriverWait(guest, 5).until(lambda driver: _editor_state(driver, guest_item, '#example')['offset'] == 3)
        except TimeoutException as error:
            diagnostics = _viewedit_diagnostics(guest, guest_item)
            raise AssertionError(f'Click ignored after read-only became writable: {diagnostics}') from error
        native_logical_key(guest, 'X')
        _wait_source(guest, guest_item, '#example', original.replace('Middle', 'MidXdle'), 'MidXdle line', focused=True)
    finally:
        try:
            guest.get('about:blank')
        finally:
            result = lease.retire()
            browser.get('about:blank')
            assert result.proven_gone, result


def _alternate_textedit_and_viewedit(driver, item, source, text, rounds):
    """Type through TextEdit, ViewEdit, and Split in turn, checking each click lands."""
    def textedit_type(char, mode):
        nonlocal source, text
        _workspace_mode(driver, mode)
        driver.execute_script("""
          const [item, needle] = arguments, view = panelNodes.get(item)._cmView;
          view.focus();
          view.dispatch({selection: {anchor: view.state.doc.toString().indexOf(needle) + needle.length}});
        """, item, text)
        native_logical_key(driver, char)
        source, text = source.replace(text, text + char), text + char
        _wait_source(driver, item, '#example', source)

    def viewedit_type(char, mode, offset, step):
        nonlocal source, text
        _workspace_mode(driver, mode)
        _click_frame(driver, item, '#example', offset)
        try:
            _wait_caret(WebDriverWait(driver, 5), item, '#example', offset)
        except TimeoutException as error:
            raise AssertionError(f'{step}: ViewEdit ignored the click: {_viewedit_diagnostics(driver, item)}') from error
        native_logical_key(driver, char)
        updated = text[:offset] + char + text[offset:]
        source, text = source.replace(text, updated), updated
        _wait_source(driver, item, '#example', source, text, focused=True)

    for index in range(rounds):
        textedit_type('a', 'edit')
        viewedit_type('b', 'preview', 1 + index % 3, f'round {index} ViewEdit')
        viewedit_type('c', 'split', 2 + index % 3, f'round {index} Split')
        textedit_type('d', 'split')
    return source


def test_html_viewedit_keeps_accepting_clicks_across_repeated_mode_switches(browser, tmp_path):
    source = '<!DOCTYPE html><p>First line</p>\n<p id=example>Middle line</p>\n'
    item = mount_html_split(browser, tmp_path, source)
    _alternate_textedit_and_viewedit(browser, item, source, 'Middle line', 12)


@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
def test_shared_html_viewedit_keeps_accepting_clicks_across_repeated_mode_switches(
    browser, isolated_dev_server_factory, tmp_path, sandbox_events,
):
    server = isolated_dev_server_factory('html-viewedit-switches', tls=True, auth_bypass=False)
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    path = server.paths.workspace_dir / 'switches.html'
    original = '<!DOCTYPE html><p>First line</p>\n<p id=example>Middle line</p>\n'
    path.write_text(original, encoding='utf-8')
    browser.get(server.base_url + '/login')
    browser.find_element(By.NAME, 'username').send_keys('keivenc')
    browser.find_element(By.NAME, 'password').send_keys('random-password')
    browser.find_element(By.CSS_SELECTOR, 'form[action="/login"] button[type="submit"]').click()
    WebDriverWait(browser, 15).until(lambda driver: driver.execute_script(
        "return !!window.YOmuxShare && typeof openFileEditorPane === 'function'"
    ))
    host_item = browser.execute_async_script("""
      const [path, done] = arguments;
      (async () => {
        const item = await openFileInEditor(path, 'switches.html', {viewMode: 'edit', userInitiated: true});
        await openFileEditorPane(path, {item});
        return item;
      })().then(done, error => done({error: String(error)}));
    """, str(path))
    assert isinstance(host_item, str), host_item
    created = _share_json(browser, '/api/share', {
        'guest': 'HtmlWriter', 'minutes': 5, 'limit': 1,
        'resources': [{'kind': 'file', 'path': str(path), 'writable': True, 'saved_content': original}],
    })
    assert created['status'] == 201, created
    lease = WebDriverLease.acquire(lambda: new_chrome_driver(profile_dir=tmp_path / 'html-guest'))
    guest = lease.driver
    try:
        guest.get(created['body']['url'])
        guest_wait = WebDriverWait(guest, 15)
        if not sandbox_events:
            guest_wait.until(lambda driver: driver.execute_script("return typeof htmlPreviewReceivesEvents === 'function'"))
            guest.execute_script("htmlPreviewReceivesEvents = () => false")
        _workspace_mode(guest, 'split')
        guest_item = guest_wait.until(lambda driver: driver.execute_script("""
          return [...panelNodes].find(([, panel]) => panel._shareConnection && panel._cmView &&
            panel.querySelector('.file-editor-html-preview')?._htmlPreviewDocument)?.[0] || null;
        """))
        source = _alternate_textedit_and_viewedit(guest, guest_item, original, 'Middle line', 8)
        WebDriverWait(browser, 10).until(lambda driver: driver.execute_script(
            'return panelNodes.get(arguments[0])._cmView.state.doc.toString() === arguments[1]', host_item, source))
        text = source.split('<p id=example>')[1].split('</p>')[0]
        source = _alternate_textedit_and_viewedit(browser, host_item, source, text, 8)
        guest_wait.until(lambda _: path.read_bytes() == source.encode())
    finally:
        try:
            guest.get('about:blank')
        finally:
            result = lease.retire()
            browser.get('about:blank')
            assert result.proven_gone, result


@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
def test_html_viewedit_accepts_clicks_after_textedit_edits_the_footer(browser, tmp_path, sandbox_events):
    source = _long_html_fixture()
    item = mount_html_split(browser, tmp_path, source, sandbox_events=sandbox_events)
    wait = WebDriverWait(browser, 8)
    selector = '.footer strong'
    wait.until(lambda driver: driver.execute_script("""
      return !!htmlSplitPreviewFrame(panelNodes.get(arguments[0]))?.contentDocument.querySelector(arguments[1]);
    """, item, selector))
    browser.execute_script("""
      const view = panelNodes.get(arguments[0])._cmView, at = view.state.doc.toString().indexOf('Oddd Orbit') + 2;
      view.scrollDOM.scrollTop = view.scrollDOM.scrollHeight;
      view.focus();
      view.dispatch({selection: {anchor: at}});
    """, item)
    native_logical_key(browser, 'q')
    edited = source.replace('Oddd Orbit', 'Odqdd Orbit')
    wait.until(lambda driver: driver.execute_script("""
      const frame = htmlSplitPreviewFrame(panelNodes.get(arguments[0]));
      return frame?.contentDocument.querySelector(arguments[1])?.textContent.startsWith('Odqdd');
    """, item, selector))
    assert _editor_state(browser, item, selector)['source'] == edited
    for mode, target, offset in (('split', selector, 3), ('split', '.footer span + span', 4),
                                 ('preview', selector, 5), ('preview', '.footer span + span', 2)):
        if browser.execute_script('return fileEditorPanelMode(panelNodes.get(arguments[0]))', item) != mode:
            # The fixture page is taller than this window, and caret reveal can scroll it.
            browser.execute_script('document.scrollingElement.scrollTop = 0')
            _workspace_mode(browser, mode)
        browser.execute_script("""
          const frame = htmlSplitPreviewFrame(panelNodes.get(arguments[0])) ||
            panelNodes.get(arguments[0]).querySelector('.file-editor-html-preview');
          const doc = frame?.contentDocument, element = doc?.querySelector(arguments[1]);
          if (element) doc.scrollingElement.scrollTop += element.getBoundingClientRect().top - frame.clientHeight / 2;
        """, item, target)
        _click_frame(browser, item, target, offset)
        try:
            wait.until(lambda driver: _editor_state(driver, item, target)['offset'] == offset)
        except TimeoutException as error:
            raise AssertionError(f'{mode} {target}: ViewEdit ignored the click: {_viewedit_diagnostics(browser, item)}') from error


def test_html_textedit_typing_through_autosave_keeps_the_caret(browser, isolated_dev_server_factory, tmp_path, request):
    server = isolated_dev_server_factory('html-autosave-bottom', tls=True, auth_bypass=False)
    request.addfinalizer(lambda: browser.get('about:blank'))
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    path = server.paths.workspace_dir / 'test.html'
    source = _long_html_fixture()
    path.write_text(source, encoding='utf-8')
    browser.get(server.base_url + '/login')
    browser.find_element(By.NAME, 'username').send_keys('keivenc')
    browser.find_element(By.NAME, 'password').send_keys('random-password')
    browser.find_element(By.CSS_SELECTOR, 'form[action="/login"] button[type="submit"]').click()
    wait = WebDriverWait(browser, 15)
    wait.until(lambda driver: driver.execute_script("return typeof openFileEditorPane === 'function'"))
    item = browser.execute_async_script("""
      const [path, done] = arguments;
      (async () => {
        const item = await openFileInEditor(path, 'test.html', {viewMode: 'split', userInitiated: true});
        await openFileEditorPane(path, {item});
        return item;
      })().then(done, error => done({error: String(error)}));
    """, str(path))
    assert isinstance(item, str), item
    wait.until(lambda driver: driver.execute_script("""
      const panel = panelNodes.get(arguments[0]);
      return !!panel?._cmView && !!htmlSplitPreviewFrame(panel)?._htmlPreviewDocument?.querySelector('.footer strong');
    """, item))
    browser.execute_script("""
      fileEditorAutosaveDelaySeconds = 0.5;
      const view = panelNodes.get(arguments[0])._cmView, at = view.state.doc.toString().indexOf('Oddd Orbit') + 4;
      view.focus();
      view.dispatch({selection: {anchor: at}, scrollIntoView: true});
      window.__caretLog = [];
      const panel = panelNodes.get(arguments[0]);
      const sample = () => {
        const v = panel._cmView;
        window.__caretLog.push([v.state.selection.main.head, Math.round(v.scrollDOM.scrollTop), v.state.doc.length]);
        window.__caretTimer = requestAnimationFrame(sample);
      };
      sample();
    """, item)
    expected, start = source, source.index('Oddd Orbit') + 4
    for index, char in enumerate('qwertyuiop'):
        native_logical_key(browser, char)
        at = start + index
        expected = expected[:at] + char + expected[at:]
        wait.until(lambda _: path.read_text(encoding='utf-8') == expected)
    log = browser.execute_script('cancelAnimationFrame(window.__caretTimer); return window.__caretLog')
    lowest = min(head for head, _, _ in log)
    top = min(scroll for _, scroll, _ in log[5:])
    assert lowest > len(source) // 2 and top > 0, f'caret or scroll jumped: lowest head {lowest}, scroll {top}'


def test_html_viewedit_recovers_after_textedit_replaces_the_whole_text(browser, tmp_path):
    source = '<!DOCTYPE html><p id=example>Middle line</p>\n'
    item = mount_html_split(browser, tmp_path, source)
    _click_frame(browser, item, '#example', 2)
    WebDriverWait(browser, 5).until(lambda driver: _editor_state(driver, item, '#example')['offset'] == 2)
    browser.execute_script("""
      const view = panelNodes.get(arguments[0])._cmView, text = view.state.doc.toString();
      // A whole-document replacement with identical text, as a reload from the saved copy does.
      view.dispatch({changes: {from: 0, to: text.length, insert: text}});
    """, item)
    _click_frame(browser, item, '#example', 4)
    try:
        WebDriverWait(browser, 5).until(lambda driver: _editor_state(driver, item, '#example')['offset'] == 4)
    except TimeoutException as error:
        raise AssertionError(f'ViewEdit ignored the click: {_viewedit_diagnostics(browser, item)}') from error
    native_logical_key(browser, 'X')
    _wait_source(browser, item, '#example', source.replace('Middle', 'MiddXle'), 'MiddXle line', focused=True)


def test_html_viewedit_flags_problems_for_copying(browser, tmp_path):
    item = mount_html_split(browser, tmp_path, '<!DOCTYPE html><p id=example>Middle line</p>\n')
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("""
      return panelNodes.get(arguments[0]).querySelector('[data-html-viewedit-status]')?.dataset.htmlVieweditStatus === 'ready';
    """, item))
    flagged = """
      const panel = panelNodes.get(arguments[0]);
      const trigger = panel.querySelector('[data-html-viewedit-info]');
      const list = panel.querySelector('[data-html-viewedit-problems]');
      return {red: trigger.hasAttribute('data-html-viewedit-problem'), hidden: list.hidden,
        text: list.querySelector('pre').textContent, copy: !!list.querySelector('button'),
        log: debugLogsTextForClipboard()};
    """
    clean = browser.execute_script(flagged, item)
    assert not clean['red'] and clean['hidden'], clean
    browser.execute_script("""
      const panel = panelNodes.get(arguments[0]);
      const frame = htmlSplitPreviewFrame(panel);
      // Break the link the way an unobserved document swap would.
      frame._htmlSourceSnapshot = null;
      panel._cmView.setState(panel._cmApi.EditorState.create({doc: panel._cmView.state.doc.toString() + ' ',
        extensions: []}));
      htmlSplitSourceMap(panel, frame);
    """, item)
    state = browser.execute_script(flagged, item)
    assert state['red'] and not state['hidden'] and state['copy'], state
    assert 'lost the link between the page and the source' in state['text'], state
    browser.execute_script("jsDebugLogsState.levels.add('warning')")
    assert 'lost the link between the page and the source' in browser.execute_script(flagged, item)['log']


def test_clean_editor_in_a_background_window_reloads_a_disk_change(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('editor-deferred-reload', tls=True, auth_bypass=False)
    request.addfinalizer(lambda: browser.get('about:blank'))
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    path = server.paths.workspace_dir / 'shared.html'
    path.write_text('<p id=example>first</p>\n', encoding='utf-8')
    browser.get(server.base_url + '/login')
    browser.find_element(By.NAME, 'username').send_keys('keivenc')
    browser.find_element(By.NAME, 'password').send_keys('random-password')
    browser.find_element(By.CSS_SELECTOR, 'form[action="/login"] button[type="submit"]').click()
    wait = WebDriverWait(browser, 15)
    wait.until(lambda driver: driver.execute_script("return typeof openFileEditorPane === 'function'"))
    item = browser.execute_async_script("""
      const [path, done] = arguments;
      (async () => {
        const item = await openFileInEditor(path, 'shared.html', {viewMode: 'edit', userInitiated: true});
        await openFileEditorPane(path, {item});
        return item;
      })().then(done, error => done({error: String(error)}));
    """, str(path))
    assert isinstance(item, str), item
    wait.until(lambda driver: driver.execute_script('return !!panelNodes.get(arguments[0])?._cmView', item))
    # File change events only arrive for files the page has registered with the server.
    browser.execute_script('syncServerWatchRoots({immediate: true})')
    wait.until(lambda driver: driver.execute_script(
        'return clientEventTransportState.connected && serverWatchRootsState.registered'))
    # The editor keeps focus inside a browser window that is in the background, as when another
    # browser window is in front. A background window is not being edited, so the change loads.
    browser.execute_script('panelNodes.get(arguments[0])._cmView.focus(); document.hasFocus = () => false;', item)
    path.write_text('<p id=example>second</p>\n', encoding='utf-8')
    WebDriverWait(browser, 10).until(lambda driver: driver.execute_script(
        'return panelNodes.get(arguments[0])._cmView.state.doc.toString()', item) == '<p id=example>second</p>\n')


def _open_owner_editor(driver, server, path, *, sandbox_events=True):
    driver.get(server.base_url + '/login')
    driver.find_element(By.NAME, 'username').send_keys('keivenc')
    driver.find_element(By.NAME, 'password').send_keys('random-password')
    driver.find_element(By.CSS_SELECTOR, 'form[action="/login"] button[type="submit"]').click()
    WebDriverWait(driver, 15).until(lambda d: d.execute_script("return typeof openFileEditorPane === 'function'"))
    item = driver.execute_async_script("""
      const [path, name, sandboxEvents, done] = arguments;
      // WebKit does not pass iframe events through. The parent input bridge handles that path.
      if (!sandboxEvents) htmlPreviewReceivesEvents = () => false;
      (async () => {
        const item = await openFileInEditor(path, name, {viewMode: 'split', userInitiated: true});
        await openFileEditorPane(path, {item});
        return item;
      })().then(done, error => done({error: String(error)}));
    """, str(path), path.name, sandbox_events)
    assert isinstance(item, str), item
    return item


def _wait_live_sync(driver, item):
    WebDriverWait(driver, 15).until(lambda d: d.execute_script(
        'return !!panelNodes.get(arguments[0])?._shareConnection', item), 'owner tabs did not join live sync')


def test_owner_tabs_in_two_browsers_edit_the_same_file_live(browser, isolated_dev_server_factory, tmp_path):
    server = isolated_dev_server_factory('owner-live-sync', tls=True, auth_bypass=False)
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    path = server.paths.workspace_dir / 'live.html'
    path.write_text('<p id=example>Middle line</p>\n', encoding='utf-8')

    def text(driver, item):
        return driver.execute_script('return panelNodes.get(arguments[0])._cmView.state.doc.toString()', item)

    def type_at(driver, item, needle, char):
        driver.execute_script("""
          const view = panelNodes.get(arguments[0])._cmView;
          view.focus();
          view.dispatch({selection: {anchor: view.state.doc.toString().indexOf(arguments[1]) + arguments[1].length}});
        """, item, needle)
        native_logical_key(driver, char)

    lease = WebDriverLease.acquire(lambda: new_chrome_driver(profile_dir=tmp_path / 'second-owner-browser'))
    other = lease.driver
    try:
        first = _open_owner_editor(browser, server, path)
        second = _open_owner_editor(other, server, path)
        for driver, item in ((browser, first), (other, second)):
            _wait_live_sync(driver, item)
        type_at(browser, first, 'Middle', 'X')
        WebDriverWait(other, 10).until(lambda d: text(d, second) == '<p id=example>MiddleX line</p>\n')
        type_at(other, second, 'line', 'Y')
        WebDriverWait(browser, 10).until(lambda d: text(d, first) == '<p id=example>MiddleX lineY</p>\n')
        WebDriverWait(browser, 10).until(lambda _: path.read_text(encoding='utf-8') == '<p id=example>MiddleX lineY</p>\n')
        # The edit also reaches the other browser's rendered page.
        _wait_source(other, second, '#example', '<p id=example>MiddleX lineY</p>\n', 'MiddleX lineY')
        # Like a share, each browser shows the other browser's caret, even for the same user.
        for driver, item in ((browser, first), (other, second)):
            WebDriverWait(driver, 10).until(lambda d: d.execute_script("""
              return [...panelNodes.get(arguments[0]).querySelectorAll('[data-share-caret-name]')]
                .some(caret => caret.dataset.shareCaretName === 'keivenc');
            """, item), 'the other browser caret is not shown')
        # ViewEdit stays editable in both live-synced tabs. Each tab waits for the other's edit first.
        for driver, item, offset, char, before, expected in (
            (other, second, 2, 'Z', 'MiddleX lineY', 'MiZddleX lineY'),
            (browser, first, 4, 'W', 'MiZddleX lineY', 'MiZdWdleX lineY')):
            _wait_source(driver, item, '#example', f'<p id=example>{before}</p>\n', before)
            _click_frame(driver, item, '#example', offset)
            try:
                _wait_caret(WebDriverWait(driver, 5), item, '#example', offset)
            except TimeoutException as error:
                raise AssertionError(f'ViewEdit ignored the click: {_viewedit_diagnostics(driver, item)}') from error
            native_logical_key(driver, char)
            _wait_source(driver, item, '#example', f'<p id=example>{expected}</p>\n', expected, focused=True)
    finally:
        try:
            other.get('about:blank')
        finally:
            result = lease.retire()
            browser.get('about:blank')
            assert result.proven_gone, result


@pytest.mark.parametrize('sandbox_events', [True, False], ids=['iframe-events', 'parent-input'])
def test_owner_viewedit_survives_other_browser_textedit_at_the_bottom(browser, isolated_dev_server_factory, tmp_path, sandbox_events):
    # Reported: A has a ViewEdit caret at the top. B edits the footer in TextEdit. A then ignored
    # clicks and drew B's caret at the top-left, although B stayed at the bottom.
    server = isolated_dev_server_factory('owner-live-sync-bottom', tls=True, auth_bypass=False)
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    path = server.paths.workspace_dir / 'test.html'
    source = _long_html_fixture()
    path.write_text(source, encoding='utf-8')
    lease = WebDriverLease.acquire(lambda: new_chrome_driver(profile_dir=tmp_path / 'second-owner-browser'))
    other = lease.driver
    try:
        first = _open_owner_editor(browser, server, path, sandbox_events=sandbox_events)
        second = _open_owner_editor(other, server, path)
        for driver, item in ((browser, first), (other, second)):
            _wait_live_sync(driver, item)
        _click_frame(browser, first, '#page-title', 4)
        _wait_caret(WebDriverWait(browser, 5), first, '#page-title', 4)
        # B has not placed a caret, so its offset 0 is before <body> and has no place on A's page.
        assert not browser.execute_script("""
          return [...panelNodes.get(arguments[0]).querySelectorAll('.html-split-caret[data-share-caret-name]')]
            .some(marker => !marker.hidden);
        """, first), 'a caret outside <body> was drawn on the page'
        other.execute_script("""
          const view = panelNodes.get(arguments[0])._cmView, at = view.state.doc.toString().indexOf('Oddd Orbit') + 2;
          view.focus();
          view.dispatch({selection: {anchor: at}, scrollIntoView: true});
        """, second)
        native_logical_key(other, 'q')
        edited = source.replace('Oddd Orbit', 'Odqdd Orbit')
        WebDriverWait(browser, 10).until(lambda d: _editor_state(d, first, '.footer strong')['source'] == edited,
                                         'the footer edit did not reach the first browser')
        WebDriverWait(browser, 10).until(lambda d: _editor_state(d, first, '.footer strong')['text'].startswith('Odqdd'))
        # B's caret is in the footer. A shows it there, or hides it while the footer is out of view.
        caret = WebDriverWait(browser, 10).until(lambda d: d.execute_script("""
          const panel = panelNodes.get(arguments[0]), frame = htmlSplitPreviewFrame(panel);
          const marker = [...panel.querySelectorAll('.html-split-caret[data-share-caret-name]')].find(node => !node.hidden);
          const footer = frame.contentDocument.querySelector('.footer strong').getBoundingClientRect();
          const outer = frame.getBoundingClientRect(), pane = frame.parentElement.getBoundingClientRect();
          const visible = footer.bottom > 0 && footer.top < frame.clientHeight;
          if (!marker) return visible ? null : {hidden: true};
          const box = marker.getBoundingClientRect();
          return {hidden: false, top: box.top - outer.top, footerTop: footer.top, footerBottom: footer.bottom, visible};
        """, first), 'the other browser caret is missing')
        assert caret['hidden'] or caret['footerTop'] - 2 <= caret['top'] <= caret['footerBottom'], caret
        # A keeps editing in ViewEdit: its caret survives, and a new click still lands.
        native_logical_key(browser, 'x')
        typed = edited.replace('page-title">The Moss', 'page-title">The xMoss')
        _wait_source(browser, first, '#page-title', typed, 'The xMoss Moon Field Guide', focused=True)
        _click_frame(browser, first, '#page-title', 9)
        try:
            _wait_caret(WebDriverWait(browser, 5), first, '#page-title', 9)
        except TimeoutException as error:
            raise AssertionError(f'ViewEdit ignored the click: {_viewedit_diagnostics(browser, first)}') from error
        native_logical_key(browser, 'y')
        _wait_source(browser, first, '#page-title', typed.replace('page-title">The xMoss', 'page-title">The xMossy'), 'The xMossy Moon Field Guide', focused=True)
    finally:
        try:
            other.get('about:blank')
        finally:
            result = lease.retire()
            browser.get('about:blank')
            assert result.proven_gone, result


def test_html_viewedit_copy_details_records_clicks_and_map_health(browser, tmp_path):
    original = '<h1 id=heading>abcd</h1>'
    item = mount_html_split(browser, tmp_path, original)
    _click_frame(browser, item, '#heading', 2)
    _wait_caret(WebDriverWait(browser, 5), item, '#heading', 2)
    # Read while editing: opening the popover moves focus out of the page.
    details = browser.execute_script('return htmlViewEditDiagnostics(panelNodes.get(arguments[0]))', item)
    facts = json.loads(details.split('\n', 1)[1].split('\nProblems:', 1)[0])
    assert facts['map']['records'] > 0 and facts['page']['sameText'] and facts['editing']['hostConnected'], facts
    history = details.split('\nHistory:\n', 1)[1]
    assert 'page loaded' in history and 'click on text' in history and 'editing started' in history, history
    trigger = browser.find_element(By.CSS_SELECTOR, '[data-html-viewedit-info]')
    _cdp_click_point(browser, browser.execute_script("""
      const box = arguments[0].getBoundingClientRect(); return {x: box.left + box.width / 2, y: box.top + box.height / 2};
    """, trigger))
    assert browser.find_element(By.CSS_SELECTOR, '[data-html-viewedit-details]').is_displayed()


def test_html_viewedit_maps_a_page_that_an_extension_changed(browser, tmp_path):
    # Extensions can add, remove, or wrap elements on a same-origin page. Only the touched elements
    # may stop mapping; the rest of the page stays editable.
    original = ('<!doctype html><html><head><title>t</title></head><body><h1 id=heading>abcd</h1>'
                '<p id=gone>zz</p><p id=marked>one two</p><p id=after>efgh</p></body></html>')

    def change_like_an_extension(driver):
        driver.execute_script("""
          const descriptor = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'srcdoc');
          Object.defineProperty(HTMLIFrameElement.prototype, 'srcdoc', {configurable: true, get: descriptor.get, set(value) {
            descriptor.set.call(this, String(value).replace('<head>', '<head><style data-extension></style>')
              .replace('<p id=gone>zz</p>', '').replace('one two', 'one <mark>two</mark>')
              .replace('</body>', '<div data-extension>widget</div></body>'));
          }});
        """)

    item = mount_html_split(browser, tmp_path, original, before_mount=change_like_an_extension)
    for selector, offset, char, edited in (('#after', 2, 'x', 'efxgh'), ('#heading', 1, 'y', 'aybcd')):
        _click_frame(browser, item, selector, offset)
        try:
            _wait_caret(WebDriverWait(browser, 5), item, selector, offset)
        except (TimeoutException, AssertionError) as error:
            raise AssertionError(browser.execute_script('return htmlViewEditDiagnostics(panelNodes.get(arguments[0]))', item)) from error
        native_logical_key(browser, char)
        original = original.replace(edited.replace(char, '', 1), edited)
        _wait_source(browser, item, selector, original, edited, focused=True)
    details = browser.execute_script('return htmlViewEditDiagnostics(panelNodes.get(arguments[0]))', item)
    facts = json.loads(details.split('\n', 1)[1].split('\nProblems:', 1)[0])
    # Added: the style, the mark, the widget, and #marked whose own text the mark split. Missing: #gone, #marked.
    assert facts['map']['foreign'] == 4 and facts['map']['missing'] == 2, facts['map']
    assert 'page differs from the source' in details.split('\nHistory:\n', 1)[1], details
