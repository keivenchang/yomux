# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Guest grants use the ordinary workspace and keep live scope transitions visible."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import browser_auth_yaml, new_chrome_driver, register_browser_new_document_script, remove_browser_test_new_document_scripts
from tests.browser_helpers.browser_console import assert_only_expected_browser_network_error
from tests.browser_helpers.native_keyboard import native_document_end, native_editor_modifier, native_logical_key
from tests.browser_helpers.webdriver_lease import WebDriverLease, retire_all
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tests.browser_helpers.share_workspace import login_owner, _button, _share_json, _workspace_mode

pytestmark = pytest.mark.browser


def _workspace_editor(driver, writable=None):
    return driver.execute_script("""
      const root=[...document.querySelectorAll('.file-editor-panel .cm-editor')].find(node=>node.getClientRects().length);
      if(!root || !window.YOmuxCodeMirror)return false;
      const view=YOmuxCodeMirror.EditorView.findFromDOM(root);
      if(arguments[0]!==null && view.state.readOnly===arguments[0])return false;
      return view.contentDOM;
    """, writable)


def test_shared_markdown_viewedit_merges_with_textedit(browser, isolated_dev_server_factory, tmp_path):
    server = isolated_dev_server_factory('share-rich-edit', tls=True, auth_bypass=False)
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    path = server.paths.workspace_dir / 'rich.md'
    bullets = '### 2026-11-25 Wed\n- h\n- \n- \n- next item'
    mixed = '* hello\n\n* \n- next group'
    details = '<details><summary>Details</summary>\n\ndetail-body\n\n</details>'
    table = '| A | B |\n| :--- | ---: |\n| C | D |'
    remainder = f'\n\n*untouched*\n\n{table}\n\n{bullets}\n\nA &amp; B\n\n{mixed}\n\n{details}\n\nThis has __bold__ and ordinary text\n'
    original = 'Middle' + remainder
    path.write_text(original, encoding='utf-8')
    login_owner(browser, server)
    WebDriverWait(browser, 15).until(lambda d: d.execute_script(
        "return !!window.YOmuxShare && typeof openFileEditorPane === 'function'"))
    item = browser.execute_async_script("""
      const path = arguments[0], done = arguments[1];
      openFileInEditor(path, 'rich.md', {viewMode: 'edit', userInitiated: true}).then(async item => {
        await openFileEditorPane(path, {item});
        done(item);
      }, error => done({error: String(error)}));
    """, str(path))
    assert isinstance(item, str), item
    created = _share_json(browser, '/api/share', {'guest': 'RichWriter', 'minutes': 5, 'limit': 1,
        'resources': [{'kind': 'file', 'path': str(path), 'writable': True, 'saved_content': original}]})
    assert created['status'] == 201, created
    WebDriverWait(browser, 10).until(lambda d: d.execute_script(
        'return !!panelNodes.get(arguments[0])?._shareConnection', item))
    guest = new_chrome_driver(profile_dir=tmp_path / 'rich-guest')
    lease = WebDriverLease.from_driver(guest)
    try:
        guest.get(created['body']['url'])
        rich = WebDriverWait(guest, 15).until(lambda d: d.execute_script("""
          const panel = [...document.querySelectorAll('.file-editor-panel')].find(p => p._pmView?.editable);
          if (!panel?._shareConnection || !panel._cmView?.dom.closest('.file-editor-codemirror-panel').hidden) return false;
          const view = panel._pmView;
          view.focus();
          view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc, 7)));
          return view.dom;
        """))
        hold_guest_edits = """
          const original = window.fetch;
          let held = true;
          window.__releaseRichSync = null;
          window.fetch = (input, options) => {
            if (!held || options?.method !== 'POST' || !options.body ||
                !JSON.parse(options.body).updates?.length) return original(input, options);
            return new Promise(resolve => {
              window.__releaseRichSync = () => {
                held = false;
                resolve(original(input, options));
              };
            });
          };
        """
        guest.execute_script(hold_guest_edits)
        try:
            native_logical_key(guest, 'G')
            assert rich.find_element(By.CSS_SELECTOR, 'p').text == 'MiddleG'
            assert path.read_text(encoding='utf-8') == original
            WebDriverWait(guest, 5).until(lambda d: d.execute_script('return !!window.__releaseRichSync'))
            browser.execute_script("""
              const view = panelNodes.get(arguments[0])._cmView;
              view.focus();
              view.dispatch({selection: {anchor: 2}});
            """, item)
            assert browser.execute_script("""
              return panelNodes.get(arguments[0]).querySelector('.file-editor-cursor-status').textContent;
            """, item) == "1:3"
            browser.execute_script("""
              const view = panelNodes.get(arguments[0])._cmView;
              view.dispatch({selection: {anchor: 0}});
            """, item)
            native_logical_key(browser, 'H')
            WebDriverWait(browser, 10).until(lambda _: path.read_text(encoding='utf-8') == 'H' + original)
        finally:
            guest.execute_script('window.__releaseRichSync?.()')
        WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == 'HMiddleG' + remainder)
        WebDriverWait(guest, 5).until(lambda _: rich.find_element(By.CSS_SELECTOR, 'p').text == 'HMiddleG')
        WebDriverWait(guest, 8).until(lambda d: d.find_elements(By.CSS_SELECTOR, '.share-rich-caret[data-share-caret-name="keivenc"]'))
        WebDriverWait(browser, 8).until(lambda d: d.find_elements(By.CSS_SELECTOR, '.share-remote-caret[data-share-caret-name="RichWriter"]'))
        _workspace_mode(browser, 'split')
        _workspace_mode(guest, 'split')
        guest.execute_script("""
          const view = document.querySelector('.file-editor-panel')._pmView;
          view.focus();
          view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc, 3)));
        """)
        def host_carets(driver, primary, offset):
            return driver.execute_script("""
              const rendered = selector => [...document.querySelectorAll(selector)]
                .filter(node => node.getClientRects().length);
              const labels = rendered('.share-caret-label:not([hidden])')
                .filter(node => node.textContent === 'RichWriter')
                .map(node => getComputedStyle(node).opacity).sort();
              const carets = rendered('[data-share-caret-name="RichWriter"]');
              const primary = arguments[0];
              return labels.join() === '0.85,0.85' && carets.length === 2 && carets.every(node => {
                const pane = node.classList.contains('share-rich-caret') ? 'view' : 'text';
                const ink = getComputedStyle(node, '::before');
                const panel = node.closest('.file-editor-panel');
                const actual = pane === 'view' ? panel._pmView.posAtDOM(node, 0) - 1 : panel._cmView.posAtDOM(node);
                if (actual !== arguments[1]) return false;
                return ink.animationName === 'none' && (pane === primary
                  ? node.hasAttribute('data-share-caret-primary')
                  : node.hasAttribute('data-share-caret-follower') && ink.opacity === '0.5');
              });
            """, primary, offset)
        WebDriverWait(browser, 8).until(lambda d: host_carets(d, 'view', 2))
        guest.execute_script("""
          const view = document.querySelector('.file-editor-panel')._cmView;
          view.focus();
          view.dispatch({selection: {anchor: 4}});
        """)
        WebDriverWait(browser, 8).until(lambda d: host_carets(d, 'text', 4))
        _workspace_mode(guest, 'preview')
        guest.execute_script("document.querySelector('.file-editor-panel')._pmView.focus()")
        native_logical_key(guest, 'z', [native_editor_modifier(guest)])
        WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == 'H' + original)
        guest.execute_script("""
          const view = [...document.querySelectorAll('.file-editor-panel')].find(p => p._pmView?.editable)._pmView;
          view.focus();
          view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc, 2, 8)));
        """)
        native_logical_key(guest, 'b', [native_editor_modifier(guest)])
        expected = 'H**Middle**' + remainder
        WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == expected)
        assert rich.find_element(By.TAG_NAME, 'strong').text == 'Middle'
        _workspace_mode(browser, 'preview')
        WebDriverWait(browser, 10).until(lambda d: d.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          return panel?._shareConnection && panel._pmView?.editable && panel._pmView.dom.textContent.includes('ordinary');
        """, item))
        guest.execute_script("""
          const view = document.querySelector('.file-editor-panel')._pmView;
          let target;
          view.state.doc.descendants((node, position) => {
            if (node.isText && node.text === 'bold') target = position + 2;
          });
          view.focus();
          view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc, target)));
        """)
        WebDriverWait(browser, 8).until(lambda d: d.execute_script("""
          const view = panelNodes.get(arguments[0])._pmView;
          const caret = view.dom.querySelector('strong [data-share-caret-name="RichWriter"]:not([data-share-caret-approximate])');
          return !!caret?.getClientRects().length;
        """, item))
        guest.execute_script("""
          const view = document.querySelector('.file-editor-panel')._pmView;
          let target;
          view.state.doc.descendants((node, position) => {
            if (node.isText && node.text === 'A & B') target = position + 3;
          });
          view.focus();
          view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc, target)));
        """)
        WebDriverWait(browser, 8).until(lambda d: d.execute_script("""
          const view = panelNodes.get(arguments[0])._pmView;
          const caret = view.dom.querySelector('.share-rich-caret[data-share-caret-name="RichWriter"]:not([data-share-caret-approximate])');
          if (!caret) return false;
          let expected;
          view.state.doc.descendants((node, position) => {
            if (node.isText && node.text === 'A & B') expected = position + 3;
          });
          return view.posAtDOM(caret, 0) === expected;
        """, item))
        for bullet_index in (1, 2):
            guest.execute_script("""
              const view = document.querySelector('.file-editor-panel')._pmView;
              let target;
              view.state.doc.descendants((node, position, parent, index) => {
                if (parent?.type.name === 'bullet_list' && parent.attrs.bullet === '-' && index === arguments[0] &&
                    !node.firstChild.content.size) target = position + 2;
              });
              view.focus();
              view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc, target)));
            """, bullet_index)
            WebDriverWait(browser, 8).until(lambda d: d.execute_script("""
              const [item, index] = arguments;
              const dom = panelNodes.get(item)._pmView.dom;
              const li = dom.querySelectorAll('ul > li')[index];
              const caret = li.querySelector('p .share-rich-caret[data-share-caret-name="RichWriter"]:not([data-share-caret-approximate])');
              if (!caret) return false;
              const row = li.getBoundingClientRect(), point = caret.getBoundingClientRect();
              return row.height > 0 && point.top >= row.top && point.top <= row.bottom &&
                !dom.querySelector('ul > .share-rich-caret');
            """, item, bullet_index))
        guest.execute_script("""
          const view = [...document.querySelectorAll('.file-editor-panel')].find(p => p._pmView?.editable)._pmView;
          view.focus();
          view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc, view.state.doc.content.size - 1)));
        """)
        guest.execute_script(hold_guest_edits)
        try:
            native_logical_key(guest, 'Q')
            assert rich.find_elements(By.CSS_SELECTOR, 'p')[-1].text.endswith('textQ')
            WebDriverWait(guest, 5).until(lambda d: d.execute_script('return !!window.__releaseRichSync'))
            browser.execute_script("""
              const view = panelNodes.get(arguments[0])._pmView;
              let offset;
              view.state.doc.descendants((node, position) => {
                if (node.isText && node.text.includes('ordinary')) offset = position + node.text.indexOf('ordinary');
              });
              view.focus();
              view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc, offset)));
            """, item)
            native_logical_key(browser, 'R')
            expected = expected.replace('ordinary', 'Rordinary')
            WebDriverWait(browser, 10).until(lambda _: path.read_text(encoding='utf-8') == expected)
        finally:
            guest.execute_script('window.__releaseRichSync?.()')
        expected = expected.rstrip('\n') + 'Q\n'
        WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == expected)
        WebDriverWait(guest, 5).until(lambda _: rich.find_elements(By.CSS_SELECTOR, 'p')[-1].text.endswith('Rordinary textQ'))
        native_logical_key(guest, 'Enter')
        native_logical_key(guest, 'B')
        expected += '\nB\n'
        WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == expected)
        for select_letter in (False, True):
            guest.execute_script("""
              const view = document.querySelector('.file-editor-panel')._pmView;
              let end;
              view.state.doc.descendants((node, pos) => {
                if (node.isText && node.text === 'h') end = pos + 1;
              });
              view.focus();
              view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc, end)));
            """)
            if select_letter:
                native_logical_key(guest, 'ArrowLeft', ('shift',), commands=['moveLeftAndModifySelection'])
                WebDriverWait(guest, 5).until(lambda d: d.execute_script("""
                  const {doc, selection} = document.querySelector('.file-editor-panel')._pmView.state;
                  return selection.anchor > selection.head && doc.textBetween(selection.from, selection.to) === 'h';
                """))
            native_logical_key(guest, 'Backspace', commands=['deleteBackward'])
            assert rich.find_element(By.CSS_SELECTOR, 'ul > li:first-child').text == ''
            assert rich.find_element(By.CSS_SELECTOR, 'ul > li:last-child').text == 'next item'
            expected = expected.replace('\n- h\n', '\n- \n')
            WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == expected)
            WebDriverWait(browser, 8).until(lambda d: d.execute_script("""
              const panel = panelNodes.get(arguments[0]);
              return panel?._pmView?.dom.querySelector('ul > li')?.textContent === '' &&
                panel._cmView.state.doc.toString() === arguments[1];
            """, item, expected))
            if not select_letter:
                native_logical_key(guest, 'h')
                assert rich.find_element(By.CSS_SELECTOR, 'ul > li:first-child').text == 'h'
                expected = expected.replace('\n- \n', '\n- h\n', 1)
                WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == expected)
        for key, command, remaining in (('Backspace', 'deleteBackward', 3), ('Delete', 'deleteForward', 2)):
            guest.execute_script("""
              const view = document.querySelector('.file-editor-panel')._pmView;
              let caret;
              view.state.doc.descendants((node, pos, parent, index) => {
                if (parent?.type.name === 'bullet_list' && parent.attrs.bullet === '-' && index === 1) caret = pos + 2;
              });
              view.focus();
              view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc, caret)));
            """)
            native_logical_key(guest, key, commands=[command])
            assert len(rich.find_elements(By.CSS_SELECTOR, ':scope > ul:first-of-type > li')) == remaining
            assert rich.find_element(By.CSS_SELECTOR, 'ul > li:last-child').text == 'next item'
            expected = expected.replace('\n- \n', '\n', 1)
            WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == expected)
        guest.execute_script("""
          const view = document.querySelector('.file-editor-panel')._pmView;
          let target;
          view.state.doc.descendants((node, position, parent, index) => {
            if (parent?.type.name === 'bullet_list' && parent.attrs.bullet === '*' && index === 1) target = position + 2;
          });
          view.focus();
          view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc, target)));
        """)
        native_logical_key(guest, 'Enter')
        assert guest.execute_script("""
          const view = document.querySelector('.file-editor-panel')._pmView;
          const caret = view.state.selection.$head, list = caret.node(1);
          return list.attrs.bullet === '*' && list.childCount === 3 &&
            list.child(0).textContent === 'hello' && list.child(1).textContent === '' &&
            list.child(2).textContent === '' && caret.index(1) === 2 && caret.parentOffset === 0;
        """)
        expected = expected.replace(mixed, '* hello\n\n* \n\n* \n- next group')
        WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == expected)
        WebDriverWait(browser, 8).until(lambda d: d.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          const list = [...panel._pmView.dom.querySelectorAll(':scope > ul')]
            .find(node => node.firstElementChild?.textContent === 'hello');
          return panel._cmView.state.doc.toString() === arguments[1] &&
            list?.querySelectorAll(':scope > li').length === 3 && list.lastElementChild.textContent === '';
        """, item, expected))
        before_enter = expected.replace('* hello\n\n* \n\n* \n- next group', mixed)
        modifier = native_editor_modifier(guest)
        native_logical_key(guest, 'z', [modifier])
        WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == before_enter)
        native_logical_key(guest, 'z', [modifier, 'shift'])
        WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == expected)
        guest.execute_script("""
          const view = document.querySelector('.file-editor-panel')._pmView;
          let target;
          view.state.doc.descendants((node, position) => {
            if (node.isText && node.text === 'detail-body') target = position + node.text.length;
          });
          view.focus();
          view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc, target)));
        """)
        native_logical_key(guest, 'Z')
        assert rich.find_element(By.CSS_SELECTOR, 'details p').text == 'detail-bodyZ'
        expected = expected.replace('detail-body', 'detail-bodyZ')
        WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == expected)
        guest.execute_script("""
          const editor = document.querySelector('.file-editor-panel')._pmView.dom;
          window.__richComposition = [];
          for (const type of ['compositionstart', 'compositionend']) {
            editor.addEventListener(type, event => window.__richComposition.push([type, event.isTrusted]), {once: true});
          }
        """)
        guest.execute_cdp_cmd('Input.imeSetComposition', {'text': 'に', 'selectionStart': 1, 'selectionEnd': 1})
        assert guest.execute_script("return document.querySelector('.file-editor-panel')._pmView.composing")
        assert path.read_text(encoding='utf-8') == expected
        guest.execute_cdp_cmd('Input.insertText', {'text': '日本'})
        composition = guest.execute_script('return window.__richComposition')
        assert composition[0] == ['compositionstart', True]
        assert [event[0] for event in composition] == ['compositionstart', 'compositionend']
        expected = expected.replace('detail-bodyZ', 'detail-bodyZ日本')
        WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == expected)
        WebDriverWait(browser, 8).until(lambda d: d.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          return panel._pmView?.dom.querySelector('details p')?.textContent === 'detail-bodyZ日本';
        """, item))
        ActionChains(guest).context_click(rich.find_element(By.CSS_SELECTOR, 'td:last-child')).perform()
        _button(guest, '.markdown-table-context-menu', 'Insert column right').click()
        assert len(rich.find_elements(By.CSS_SELECTOR, 'th')) == 3
        table_edit = expected.replace(table, '| A | B |  |\n| :--- | ---: | --- |\n| C | D |  |')
        WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == table_edit)
        WebDriverWait(browser, 8).until(lambda d: d.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          return panel._pmView?.dom.querySelectorAll('th').length === 3 &&
            panel._cmView.state.doc.toString() === arguments[1];
        """, item, table_edit))
        native_logical_key(guest, 'z', [native_editor_modifier(guest)])
        WebDriverWait(guest, 10).until(lambda _: path.read_text(encoding='utf-8') == expected)
        guest.execute_script("""
          const original = window.fetch;
          window.fetch = (input, options) => {
            if (options?.method === 'POST' && options.body && JSON.parse(options.body).updates?.length) {
              return new Promise(() => {});
            }
            return original(input, options);
          };
          const panel = document.querySelector('.file-editor-panel');
          const view = panel._pmView;
          view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc, 1)));
          view.focus();
        """)
        native_logical_key(guest, 'X')
        assert rich.find_element(By.CSS_SELECTOR, 'p').text.startswith('X')
        grant = created['body']['share']
        updated = _share_json(browser, '/api/share?id=' + grant['id'], {'revision': grant['revision'],
            'resources': [{'id': grant['resources'][0]['id'], 'writable': False}]}, method='PATCH')
        assert updated['status'] == 200, updated
        WebDriverWait(guest, 10).until(lambda d: d.execute_script("""
          return document.querySelector('#share-retained-drafts textarea')?.value.startsWith('X') &&
            !document.querySelector('.file-editor-preview-pane-panel [contenteditable="true"]');
        """))
        assert path.read_text(encoding='utf-8') == expected
    finally:
        try:
            guest.get('about:blank')
        finally:
            retired = retire_all([lease])
            assert retired['proven_gone'], retired
            browser.get('about:blank')


def _native_text_selection(driver, node):
    points = driver.execute_script("""
      const range=document.createRange();range.selectNodeContents(arguments[0]);const rect=range.getBoundingClientRect();
      return {left:rect.left+1,right:rect.right-1,y:rect.top+rect.height/2};
    """, node)
    driver.execute_cdp_cmd('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': points['left'], 'y': points['y'], 'button': 'left', 'clickCount': 1})
    driver.execute_cdp_cmd('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': points['right'], 'y': points['y'], 'button': 'left', 'buttons': 1})
    driver.execute_cdp_cmd('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': points['right'], 'y': points['y'], 'button': 'left', 'clickCount': 1})
    return driver.execute_script("""
      const native=String(getSelection());if(native)return native;
      const root=arguments[0].closest('.cm-editor');if(!root)return '';
      const view=YOmuxCodeMirror.EditorView.findFromDOM(root);
      return view.state.sliceDoc(view.state.selection.main.from,view.state.selection.main.to);
    """, node)


def _workspace_snapshot(driver):
    return driver.execute_script("""
      const panel=[...document.querySelectorAll('.file-editor-panel')].find(node=>node.getClientRects().length);
      const box=node=>{const r=node.getBoundingClientRect();return {left:r.left,top:r.top,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
      const toolbar=panel.querySelector('.file-editor-toolbar');
      const source=panel.querySelector('.file-editor-codemirror-panel'), preview=panel.querySelector('.file-editor-preview-pane-panel');
      return {viewport:[innerWidth,innerHeight], toolbar:box(toolbar),source:box(source),preview:box(preview),
        theme:document.body.classList.contains('theme-light')?'light':'dark',
        controls:[...toolbar.querySelectorAll('[data-editor-mode],.file-editor-find-panel,.file-editor-wrap-panel,.file-editor-gutter-panel')]
          .filter(node=>node.getClientRects().length).map(node=>({label:node.getAttribute('aria-label'),width:box(node).width,height:box(node).height,icons:node.querySelectorAll('.file-editor-icon').length})),
        find:box(panel.querySelector('.file-editor-find-panel'))};
    """)


@pytest.mark.parametrize('writable', [False, True])
def test_guest_editor_follows_external_atomic_save(browser, isolated_dev_server_factory, tmp_path, writable):
    server = isolated_dev_server_factory('share-atomic-save', tls=True, auth_bypass=False)
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    source = server.paths.workspace_dir / '2026.md'
    original = '# 2026\n\nOriginal\n'
    updated = '# 2026\n\nUpdated by AI\n'
    source.write_text(original, encoding='utf-8')
    login_owner(browser, server)
    WebDriverWait(browser, 15).until(lambda d: d.execute_script(
        "return !!window.YOmuxShare && typeof openFileEditorPane === 'function'"))
    if not writable:
        opened = browser.execute_async_script("""
          const path = arguments[0], done = arguments[1];
          (async () => {
            const item = await openFileInEditor(path, '2026.md', {viewMode: 'edit', userInitiated: true});
            await openFileEditorPane(path, {item});
            done({item});
          })().catch(error => done({error: String(error.stack)}));
        """, str(source))
        assert 'item' in opened, opened
        WebDriverWait(browser, 10).until(lambda d: _workspace_editor(d, writable=True))
        browser.execute_script("""
          const view = panelNodes.get(arguments[0])._cmView;
          view.focus();
          view.dispatch({selection: {anchor: 3}});
        """, opened['item'])
    created = _share_json(browser, '/api/share', {
        'guest': 'AtomicSaveGuest', 'minutes': 5, 'limit': 1,
        'resources': [{'kind': 'file', 'path': str(source), 'writable': writable, 'saved_content': original}],
    })
    assert created['status'] == 201, created

    guest = new_chrome_driver(profile_dir=tmp_path / 'atomic-save-guest')
    lease = WebDriverLease.from_driver(guest)
    try:
        guest.get(created['body']['url'])
        WebDriverWait(guest, 15).until(lambda d: d.execute_script("""
          return typeof shareWorkspace !== 'undefined' && shareWorkspace.active &&
            !!document.querySelector('.file-editor-panel [data-editor-mode="edit"]');
        """))
        _workspace_mode(guest, 'preview')

        def preview_text(driver, text):
            return driver.execute_script("""
              return [...document.querySelectorAll('.file-editor-preview-pane-panel')]
                .some(panel => panel.getClientRects().length && panel.innerText.includes(arguments[0]));
            """, text)

        def editor_text(driver):
            return driver.execute_script("""
              const panel = [...document.querySelectorAll('.file-editor-panel')]
                .find(node => node.getClientRects().length);
              if (arguments[0] && !panel?._shareConnection) return null;
              return panel._cmView?.state.doc.toString();
            """, writable)

        WebDriverWait(guest, 10).until(lambda d: preview_text(d, 'Original'))
        if not writable:
            _workspace_mode(guest, 'edit')
            WebDriverWait(guest, 10).until(lambda d: d.execute_script("""
              const caret = document.querySelector('.cm-content .share-remote-caret');
              if (!caret) return false;
              const view = YOmuxCodeMirror.EditorView.findFromDOM(caret.closest('.cm-editor'));
              return view.state.readOnly && view.posAtDOM(caret) === 3 &&
                [...document.querySelectorAll('.share-caret-label')].some(label =>
                  !label.hidden && label.textContent === 'keivenc');
            """))
            _workspace_mode(guest, 'preview')
        previous_inode = source.stat().st_ino
        temporary = source.with_suffix('.next')
        temporary.write_text(updated, encoding='utf-8')
        temporary.replace(source)
        assert source.stat().st_ino != previous_inode
        WebDriverWait(guest, 10).until(lambda d: preview_text(d, 'Updated by AI'))
        _workspace_mode(guest, 'edit')
        WebDriverWait(guest, 10).until(lambda d: _workspace_editor(d, writable=writable))
        WebDriverWait(guest, 10).until(lambda d: editor_text(d) == updated)

        if not writable:
            return
        _workspace_mode(guest, 'split')
        editor = _workspace_editor(guest, writable=True)
        editor.click()
        native_document_end(guest)
        assert guest.execute_script("""
          const view = YOmuxCodeMirror.EditorView.findFromDOM(arguments[0].closest('.cm-editor'));
          return view.hasFocus && view.state.selection.main.head === view.state.doc.length;
        """, editor)
        guest.execute_script("""
          const id = arguments[0], original = window.fetch;
          window.__releaseTypingSync = () => { window.fetch = original; };
          window.fetch = (input, options) => {
            if (options?.method !== 'POST' || !new URL(String(input), location.href).pathname.endsWith('/' + id) ||
                !JSON.parse(options.body).updates?.length) return original(input, options);
            return new Promise(resolve => {
              window.__typingSyncPending = true;
              window.__releaseTypingSync = () => {
                window.fetch = original;
                resolve(original(input, options));
              };
            });
          };
        """, created['body']['share']['resources'][0]['id'])
        try:
            native_logical_key(guest, 'x')
            assert guest.execute_script("return arguments[0].innerText.endsWith('x')", editor)
            native_logical_key(guest, 'ArrowLeft')
            assert guest.execute_script("""
              const view = YOmuxCodeMirror.EditorView.findFromDOM(arguments[0].closest('.cm-editor'));
              return view.state.selection.main.head === view.state.doc.length - 1;
            """, editor)
            WebDriverWait(guest, 5).until(lambda d: preview_text(d, 'x'))
            WebDriverWait(guest, 5).until(lambda d: d.execute_script("return window.__typingSyncPending === true"))
            assert source.read_text(encoding='utf-8') == updated
        finally:
            guest.execute_script("window.__releaseTypingSync()")
        WebDriverWait(guest, 10).until(lambda d: source.read_text(encoding='utf-8') == updated + 'x')
        assert editor_text(guest) == updated + 'x'
    finally:
        try:
            guest.get('about:blank')
        finally:
            retired = retire_all([lease])
            assert retired['proven_gone'], retired
            browser.get('about:blank')


def test_guest_normal_workspace_scope_theme_find_and_retained_draft(browser, isolated_dev_server_factory, tmp_path):
    server = isolated_dev_server_factory('share-workspace', tls=True, auth_bypass=False, session_count=1)
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    (server.paths.config_dir / 'settings.yaml').write_bytes(b'general:\n  startup_tips: false\nupdates:\n  notify_level: none\n')
    source = server.paths.workspace_dir / 'workspace.md'
    other = server.paths.workspace_dir / 'added.md'
    original = '# Same Markdown\n\nSelect this text and find this word.\n'
    source.write_text(original, encoding='utf-8')
    other.write_text(''.join(f'## Section {index}\n\nSecond granted tab, paragraph {index}.\n\n' for index in range(160)), encoding='utf-8')
    guest = None
    lease = None
    try:
        login_owner(browser, server)
        WebDriverWait(browser, 15).until(lambda d: d.execute_script("return typeof openFileInEditor==='function' && !!window.YOmuxShare"))
        opened = browser.execute_async_script("""
          const [path,done]=arguments;
          openFileInEditor(path,'workspace.md',{viewMode:'split',userInitiated:true}).then(item=>done({item}),error=>done({error:String(error.stack)}));
        """, str(source))
        assert 'error' not in opened, opened
        styled = _share_json(browser, '/api/settings', {'settings': {'appearance': {'theme': 'dark', 'active_color': 'purple'}}})
        assert styled['status'] == 200, styled
        browser.execute_script("applySettingsPayload(arguments[0],{force:true})", styled['body'])
        created = _share_json(browser, '/api/share', {
            'guest': 'WorkspaceGuest', 'minutes': 5, 'limit': 1,
            'appearance': {'theme': 'dark', 'accent': 'purple'},
            'resources': [{'kind': 'file', 'path': str(source), 'writable': False}],
        })
        assert created['status'] == 201, created
        invite = urlsplit(created['body']['url'])
        assert len(invite.path.rsplit('/', 1)[-1]) == len(invite.fragment) == 20
        grant = created['body']['share']
        resource = grant['resources'][0]
        base = created['body']['url'].split('#')[0] + '/'
        guest = new_chrome_driver(profile_dir=tmp_path / 'workspace-guest')
        lease = WebDriverLease.from_driver(guest)
        register_browser_new_document_script(guest, """
          if (location.protocol === 'https:') localStorage.setItem('yomux.tabber.sidebarVisible.v1', 'false');
        """)
        guest.get(created['body']['url'])
        WebDriverWait(guest, 15).until(lambda d: d.execute_script("""
          if (typeof shareWorkspace === 'undefined' || !shareWorkspace.active) return false;
          const resource = shareWorkspace.resources.get(arguments[0]);
          return Boolean(resource && panelNodes.has(fileEditorItemFor(shareWorkspacePath(resource))));
        """, resource['id']))
        assert guest.find_elements(By.CSS_SELECTOR, '#grid .file-editor-panel')
        guest_title = 'YOmux - keivenc - WorkspaceGuest'
        assert guest.title == guest_title
        tabs_menu = '.app-menu[data-app-menu="tabs"]'
        guest.find_element(By.CSS_SELECTOR, tabs_menu + ' > .app-menu-button').click()
        tabber_command = WebDriverWait(guest, 5).until(lambda d: d.execute_script("""
          const command = document.querySelector(arguments[0] + '.open [data-menu-target-item="__tabber__"]');
          return command?.getClientRects().length ? {command, label: command.innerText.trim()} : false;
        """, tabs_menu))
        assert tabber_command['label'] == 'Tabber'
        tabber_command['command'].send_keys(Keys.ESCAPE)
        WebDriverWait(guest, 5).until(lambda d: d.execute_script("""
          const item = fileEditorItemFor(shareWorkspacePath(shareWorkspace.resources.get(arguments[0])));
          const row = [...document.querySelectorAll('.file-explorer-tabber [data-tabber-item]')]
            .find(row => row.dataset.tabberItem === item);
          return row?.getAttribute('aria-current') === 'true' && getComputedStyle(row).boxShadow !== 'none';
        """, resource['id']))
        assert not guest.find_elements(By.CSS_SELECTOR, '.share-workspace,.share-topbar,.share-resource')
        assert not guest.find_elements(By.CSS_SELECTOR, '[data-tabber-advanced]')
        assert not guest.find_elements(By.CSS_SELECTOR, '[data-tabber-new-chat], .tabber-new-chat-popover')
        assert not guest.find_elements(By.CSS_SELECTOR, '#topbarTabberToggle, [data-pane-minimize="__tabber__"]')
        chat_summary = guest.find_element(By.CSS_SELECTOR, '.share-sidebar-chat > summary')
        chat_summary.click()
        assert guest.execute_script("return document.querySelector('.share-sidebar-chat').open && !toggleTabberSidebar()")
        assert guest.execute_script("setTabberMode('advanced'); return !tabberIsAdvanced();")
        assert guest.execute_script('return location.hash') == ''
        assert _share_json(guest, '/api/fs/read?path=' + str(source))['status'] == 401
        assert _share_json(guest, '/api/create-session-plan')['status'] == 401
        assert _share_json(browser, '/api/share')['body']['shares'][0]['claimed'] == 1
        guest.refresh()
        WebDriverWait(guest, 15).until(lambda d: d.execute_script("return typeof shareWorkspace!=='undefined' && shareWorkspace.active && !!shareWorkspace.chat"))
        assert _share_json(browser, '/api/share')['body']['shares'][0]['claimed'] == 1
        browser.set_window_size(1280, 850)
        guest.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
        guest.execute_cdp_cmd('Emulation.setTouchEmulationEnabled', {'enabled': True})
        try:
            WebDriverWait(guest, 5).until(lambda d: d.execute_script("""
              const sidebar = document.querySelector('#grid .file-explorer-tabber')?.getBoundingClientRect();
              const file = document.querySelector('#grid .file-editor-panel')?.getBoundingClientRect();
              return innerWidth === 390 && sidebar?.width > 0 && file?.width > 0 &&
                sidebar.right <= file.left + 1 && file.right <= innerWidth + 1 &&
                !!document.querySelector('#grid .share-sidebar-chat textarea')?.getClientRects().length;
            """))
        finally:
            guest.execute_cdp_cmd('Emulation.setTouchEmulationEnabled', {'enabled': False})
            guest.execute_cdp_cmd('Emulation.clearDeviceMetricsOverride', {})
        guest.set_window_size(1280, 850)
        assert browser.find_elements(By.CSS_SELECTOR, '[data-tabber-advanced]')
        assert not guest.find_elements(By.CSS_SELECTOR, '[data-tabber-advanced]')
        assert browser.find_element(By.CSS_SELECTOR, '#grid [data-tabber-new-chat]').is_displayed()
        about = []
        for driver in [browser, guest]:
            help_menu = '.app-menu[data-app-menu="help"]'
            WebDriverWait(driver, 10).until(lambda d: d.find_element(By.CSS_SELECTOR, help_menu + ' > .app-menu-button')).click()
            WebDriverWait(driver, 10).until(lambda d: _button(d, help_menu, 'About')).click()
            dialog = driver.find_element(By.CSS_SELECTOR, '#modal.open.about-open')
            about.append(dialog.find_element(By.CSS_SELECTOR, '.about-details').text)
            assert len(dialog.find_elements(By.CSS_SELECTOR, '.about-author')) == 3
            dialog.find_element(By.ID, 'closeModal').click()
        assert about[0] == about[1], about
        browser.execute_script("setGlobalThemeMode('dark');selectSession(arguments[0],{userInitiated:true});", opened['item'])
        _workspace_mode(browser, 'split')
        _workspace_mode(guest, 'split')
        WebDriverWait(guest, 10).until(lambda d: _workspace_editor(d, False))
        for driver in [browser, guest]:
            WebDriverWait(driver, 10).until(lambda d: d.execute_script("return [...document.querySelectorAll('.file-editor-panel .file-editor-preview-pane-panel')].some(node=>node.getClientRects().length && node.textContent.includes('Same Markdown') && node.textContent.includes('Select this text'))"))
        host_geometry = _workspace_snapshot(browser)
        guest_geometry = _workspace_snapshot(guest)
        assert browser.execute_script("return getComputedStyle(document.body).getPropertyValue('--active-accent')") == guest.execute_script("return getComputedStyle(document.body).getPropertyValue('--active-accent')")
        assert host_geometry['viewport'] == guest_geometry['viewport'], (host_geometry, guest_geometry)
        assert host_geometry['controls'] == guest_geometry['controls'], (host_geometry, guest_geometry)
        for geometry in [host_geometry, guest_geometry]:
            assert geometry['source']['width'] > 100 and geometry['preview']['width'] > 100, geometry
            assert abs(geometry['source']['top'] - geometry['preview']['top']) < 2, geometry
            assert abs(geometry['source']['bottom'] - geometry['preview']['bottom']) < 2, geometry
        browser.save_screenshot(str(tmp_path / 'host-workspace-split-dark.png'))
        guest.save_screenshot(str(tmp_path / 'guest-workspace-split-dark.png'))
        for theme in ['dark', 'light']:
            guest.execute_script('setGlobalThemeMode(arguments[0])', theme)
            WebDriverWait(guest, 10).until(lambda d: d.execute_script("return resolvedGlobalThemeMode()===arguments[0]", theme))
            _workspace_mode(guest, 'edit')
            editor = WebDriverWait(guest, 10).until(lambda d: _workspace_editor(d, False))
            selected = _native_text_selection(guest, editor.find_element(By.CSS_SELECTOR, '.cm-line'))
            assert len(selected.strip()) >= 5 and selected.strip() in original, selected
            assert source.read_text(encoding='utf-8') == original
            native_logical_key(guest, 'f', modifiers=(native_editor_modifier(guest),))
            WebDriverWait(guest, 10).until(lambda d: d.find_element(By.CSS_SELECTOR, '.file-editor-panel .cm-search input').is_displayed())
            find = guest.execute_script("const panel=document.querySelector('.file-editor-panel');const input=panel.querySelector('.cm-search');const editor=panel.querySelector('.cm-editor');const a=input.getBoundingClientRect(),b=editor.getBoundingClientRect();return {top:a.top,bottom:a.bottom,left:a.left,right:a.right,editorTop:b.top,editorBottom:b.bottom,editorLeft:b.left,editorRight:b.right}")
            assert find['top'] >= find['editorTop'] and find['bottom'] <= find['editorBottom'], find
            assert find['left'] >= find['editorLeft'] and find['right'] <= find['editorRight'], find
            guest.save_screenshot(str(tmp_path / ('guest-find-' + theme + '.png')))
            guest.execute_script("YOmuxCodeMirror.closeSearchPanel(YOmuxCodeMirror.EditorView.findFromDOM(document.querySelector('.file-editor-panel .cm-editor')))")
            _workspace_mode(guest, 'preview')
            paragraph = WebDriverWait(guest, 10).until(lambda d: d.execute_script("return [...document.querySelectorAll('.file-editor-preview-pane-panel p')].find(node=>node.getClientRects().length && node.textContent.includes('Select this text')) || false"))
            selected = _native_text_selection(guest, paragraph)
            assert len(selected.strip()) >= 5 and selected.strip() in original, selected
            guest.save_screenshot(str(tmp_path / ('guest-markdown-selection-' + theme + '.png')))
        assert browser.execute_script('return resolvedGlobalThemeMode()') == 'dark'
        guest.refresh()
        WebDriverWait(guest, 15).until(lambda d: d.execute_script("return typeof shareWorkspace!=='undefined' && shareWorkspace.active && resolvedGlobalThemeMode()==='light' && !!shareWorkspace.chat"))
        assert guest.title == guest_title
        guest.execute_script("updateDocumentTitle()")
        assert guest.title == guest_title
        assert browser.execute_script('return resolvedGlobalThemeMode()') == 'dark'
        changed = _share_json(browser, '/api/share?id=' + grant['id'], {
            'revision': grant['revision'],
            'resources': [{'id': resource['id'], 'writable': True, 'saved_content': original},
                          {'kind': 'file', 'path': str(other), 'writable': False}],
        }, 'PATCH')
        assert changed['status'] == 200, changed
        updated = _share_json(browser, '/api/share')['body']['shares'][0]
        WebDriverWait(guest, 10).until(lambda d: d.execute_script("return shareWorkspace.resources.size===2 && [...shareWorkspace.resources.values()].some(resource=>resource.id===arguments[0] && resource.writable)", resource['id']))
        added = next(item for item in updated['resources'] if item['id'] != resource['id'])
        def tab_row(resource_id):
            return WebDriverWait(guest, 10).until(lambda d: d.execute_script("""
              const resource = shareWorkspace.resources.get(arguments[0]);
              const item = resource && fileEditorItemFor(shareWorkspacePath(resource));
              return [...document.querySelectorAll('.file-explorer-tabber [data-tabber-item]')]
                .find(row => row.dataset.tabberItem === item) || false;
            """, resource_id))

        tab_row(added['id']).click()
        for mode, surface in [('preview', '.file-editor-preview-pane-panel'), ('edit', '.cm-scroller'), ('split', '.cm-scroller')]:
            _workspace_mode(guest, mode)
            def visible_scroller(driver):
                return driver.execute_script("""
                  const panel = [...document.querySelectorAll('.file-editor-panel')].find(panel => panel.getClientRects().length);
                  const surface = panel?.querySelector(arguments[0]);
                  return panel?.dataset.filePath.endsWith('/added.md') && surface?.getClientRects().length &&
                    surface.scrollHeight > surface.clientHeight + 600 ? surface : false;
                """, surface)
            scroller = WebDriverWait(guest, 10).until(visible_scroller)
            guest.execute_script('arguments[0].scrollTop = 600', scroller)
            positions = """
              const panel = arguments[0].closest('.file-editor-panel');
              return Object.fromEntries([['edit', '.cm-scroller'], ['preview', '.file-editor-preview-pane-panel']]
                .map(([key, selector]) => [key, panel.querySelector(selector)])
                .filter(([, surface]) => surface?.getClientRects().length)
                .map(([key, surface]) => [key, Math.round(surface.scrollTop)]));
            """
            def saved_position(driver):
                query = parse_qs(urlsplit(driver.current_url).query)
                offsets = driver.execute_script(positions, scroller)
                if query.get('tab') != [added['id']] or query.get('view') != [mode] or 600 not in offsets.values():
                    return False
                return offsets if all(query.get(key, [''])[0].split(',')[0] == str(top) for key, top in offsets.items()) else False

            offsets = WebDriverWait(guest, 10).until(saved_position)
            assert not urlsplit(guest.current_url).fragment and 'added.md' not in guest.current_url
            guest.refresh()
            scroller = WebDriverWait(guest, 10).until(visible_scroller)
            observed = {}
            def restored_position(driver):
                nonlocal observed
                observed = driver.execute_script(positions, scroller)
                return observed == offsets
            try:
                WebDriverWait(guest, 10).until(restored_position)
            except Exception as error:
                raise AssertionError(f'{mode} reload expected {offsets}, got {observed}') from error
            assert tab_row(added['id']).get_dom_attribute('aria-current') == 'true'
        _workspace_mode(guest, 'preview')
        preview_scroller = WebDriverWait(guest, 10).until(lambda d: d.execute_script("""
          const preview = document.querySelector('.file-editor-preview-pane-panel:not([hidden])');
          return preview?.getClientRects().length && preview.scrollHeight > preview.clientHeight + 850 &&
            !shareWorkspace.restoreView && new URLSearchParams(location.search).get('view') === 'preview' ? preview : false;
        """))
        guest.execute_script("""
          const preview = arguments[0];
          window.__beforeImmediateGuestReload = true;
          preview.scrollTop = 850;
          if (Math.round(preview.scrollTop) !== 850) throw new Error('Fixture preview did not scroll to 850');
          location.reload();
        """, preview_scroller)
        restored = """
          const preview = document.querySelector('.file-editor-preview-pane-panel:not([hidden])');
          return {reloaded: !window.__beforeImmediateGuestReload,
            top: preview?.getClientRects().length ? Math.round(preview.scrollTop) : null,
            saved: new URLSearchParams(location.search).get('preview')};
        """
        expected = {'reloaded': True, 'top': 850, 'saved': '850,0'}
        try:
            WebDriverWait(guest, 10).until(lambda d: d.execute_script(restored) == expected)
        except Exception as error:
            raise AssertionError(f'immediate reload expected {expected}, got {guest.execute_script(restored)}') from error
        tab_row(resource['id']).click()
        _workspace_mode(guest, 'edit')
        editor = WebDriverWait(guest, 10).until(lambda d: _workspace_editor(d, True))
        WebDriverWait(guest, 10).until(lambda d: d.execute_script("return [...panelNodes.values()].some(panel=>panel._shareConnection)"))
        guest.execute_script("""
          window.__shareOriginalFetch=fetch;window.__shareHeld=[];window.__shareHold=true;
          window.fetch=(url,options)=>window.__shareHold && options?.method==='POST' && String(url).includes(arguments[0])
            ? new Promise((resolve,reject)=>window.__shareHeld.push(()=>window.__shareOriginalFetch(url,options).then(resolve,reject)))
            : window.__shareOriginalFetch(url,options);
        """, base + resource['id'])
        editor.click()
        for character in 'UNSENT':
            native_logical_key(guest, character)
        WebDriverWait(guest, 10).until(lambda d: d.execute_script("return [...panelNodes.values()].some(panel=>panel._shareConnection?.pending()>0)"))
        pending_text = guest.execute_script("return YOmuxCodeMirror.EditorView.findFromDOM(arguments[0]).state.doc.toString()", editor)
        kept = next(item for item in updated['resources'] if item['id'] != resource['id'])
        removed = _share_json(browser, '/api/share?id=' + grant['id'], {
            'revision': updated['revision'], 'resources': [{'id': kept['id'], 'writable': False}],
        }, 'PATCH')
        assert removed['status'] == 200, removed
        WebDriverWait(guest, 10).until(lambda d: d.execute_script("return shareWorkspace.resources.size===1 && !shareWorkspace.resources.has(arguments[0]) && [...fileState.keys()].every(path=>!path.includes(arguments[0]))", resource['id']))
        draft = guest.find_element(By.CSS_SELECTOR, '#share-retained-drafts textarea')
        assert draft.get_attribute('value') == pending_text and 'UNSENT' in pending_text
        assert draft.get_attribute('readonly')
        assert draft.is_displayed()
        assert guest.execute_script("return tabberNormalSelections[0]===fileEditorItemFor(shareWorkspacePath([...shareWorkspace.resources.values()][0]))")
        assert _share_json(guest, base + resource['id'])['status'] == 404
        stale = _share_json(browser, '/api/share?id=' + grant['id'], {
            'revision': updated['revision'], 'resources': [{'id': kept['id'], 'writable': True}],
        }, 'PATCH')
        assert stale['status'] == 409, stale
        assert_only_expected_browser_network_error(browser, url=server.base_url + '/api/share?id=' + grant['id'], reason='status of 409', count=1)
        guest.execute_script("window.__shareHold=false;for(const release of window.__shareHeld.splice(0))release();")
        chat_input = guest.find_element(By.CSS_SELECTOR, '.share-sidebar-chat textarea')
        chat_input.send_keys('conversation')
        native_logical_key(guest, 'Enter', modifiers=('shift',), commands=('insertNewline',))
        for key in 'survives':
            native_logical_key(guest, key)
        assert chat_input.get_property('value') == 'conversation\nsurvives'
        before = guest.execute_script("""
          const tabber = panelNodes.get(tabberItemId), row = tabberTreeInteractionController.leadRow(tabber);
          return {item: focusedPanelItem, selection: [...tabberNormalSelections], lead: row?.dataset.path};
        """)
        assert before['lead'], before
        guest.execute_script("""
          const post = YOmuxShare.post;
          window.chatPosts = 0;
          YOmuxShare.post = (url, data) => {
            if (url !== shareWorkspace.base + 'chat') return post(url, data);
            window.chatPosts++;
            return new Promise((resolve, reject) => {
              window.releaseChatPost = () => {
                YOmuxShare.post = post;
                post(url, data).then(resolve, reject);
              };
            });
          };
        """)
        native_logical_key(guest, 'Enter')
        native_logical_key(guest, 'Enter')
        assert guest.execute_script("""
          return window.chatPosts === 1 && document.activeElement === arguments[0] && focusedPanelItem === arguments[1].item &&
            JSON.stringify(tabberNormalSelections) === JSON.stringify(arguments[1].selection);
        """, chat_input, before), before
        guest.execute_script('window.releaseChatPost()')
        WebDriverWait(guest, 10).until(lambda d: 'conversation\nsurvives' in d.find_element(By.CSS_SELECTOR, '.share-sidebar-chat [role=log]').text)
        geometry = guest.execute_script("const chat=document.querySelector('.share-sidebar-chat').getBoundingClientRect(),tree=document.querySelector('.file-explorer-tabber .file-explorer-changes-panel').getBoundingClientRect();return {chat:chat.top,tree:tree.bottom}")
        assert geometry['chat'] >= geometry['tree'] - 1, geometry
        assert guest.execute_script("""
          showTabContextMenu(tabberNormalSelections[0], 300, 150);
          return !!document.querySelector('.session-context-menu');
        """)
        assert _share_json(browser, '/api/share?id=' + grant['id'], method='DELETE')['status'] == 200
        WebDriverWait(guest, 10).until(lambda d: d.execute_script("""
          return !shareWorkspace.active && shareWorkspace.resources.size === 0 && fileState.size === 0 &&
            !tabberNormalSelections.length && paneItems(layoutSlots).every(item => item === tabberItemId) &&
            !document.querySelector('.file-explorer-tabber [data-tabber-item], #grid .file-editor-panel, .session-context-menu') &&
            !document.querySelector('.share-sidebar-chat');
        """))
        assert draft.get_property('value') == pending_text and draft.is_displayed()
        assert not guest.find_elements(By.CSS_SELECTOR, '[data-tabber-new-chat], .tabber-new-chat-popover')
        assert guest.execute_script("""
          const dialog = document.querySelector('dialog:modal');
          const notice = document.getElementById('shareAccessNotice');
          const brand = dialog?.querySelector('.brand-title')?.getBoundingClientRect();
          const message = 'This share link has expired or ended. Ask the owner to open File → YO!Share → New Share, select the tabs, then click Create New Share and Copy URL and send you the new link.';
          return dialog?.contains(notice) && notice.textContent === message && !notice.isContentEditable &&
            brand?.width > 0 && brand.left < 48 && brand.top < 48 &&
            dialog.contains(document.querySelector('#share-retained-drafts')) &&
            !dialog.querySelector('.share-sidebar-chat');
        """)
        guest.find_element(By.ID, 'shareAccessNotice').send_keys(Keys.ESCAPE)
        guest.execute_cdp_cmd('Input.dispatchMouseEvent', {'type': 'mousePressed', 'button': 'left', 'clickCount': 1, 'x': 5, 'y': 90})
        guest.execute_cdp_cmd('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'button': 'left', 'clickCount': 1, 'x': 5, 'y': 90})
        assert guest.execute_script("return !!document.querySelector('dialog:modal') && !document.querySelector('#grid').getClientRects().length")
        assert guest.execute_script("""
          const leave = new Event('beforeunload', {cancelable: true});
          window.__yomuxDirtyFileNavigationGuard(leave);
          return leave.defaultPrevented;
        """)
        assert not guest.find_elements(By.CSS_SELECTOR, '.share-sidebar-chat')
        assert _share_json(guest, base + 'chat', {'text': 'after end'})['status'] == 410
        assert source.read_text(encoding='utf-8') == original
        appearance_script = """
          const dialog = document.querySelector('dialog.share-access-dialog');
          if (!dialog?.open) return null;
          return {
            theme: resolvedGlobalThemeMode(),
            accent: getComputedStyle(document.body).getPropertyValue('--active-accent').trim(),
            mark: getComputedStyle(dialog.querySelector('.brand-mark')).backgroundColor,
            second: getComputedStyle(dialog.querySelector('.brand-second')).color,
            text: getComputedStyle(dialog.querySelector('#shareAccessNotice')).color,
            card: getComputedStyle(dialog.querySelector('.share-access-card')).backgroundColor,
          };
        """
        appearance = guest.execute_script(appearance_script)
        assert appearance['theme'] == 'light'
        assert appearance['accent'] == guest.execute_script("return ACTIVE_COLOR_PRESETS.purple.light.accent")
        assert appearance['mark'] == appearance['second']
        guest.execute_script("shareWorkspace.drafts.clear()")
        guest.refresh()
        assert WebDriverWait(guest, 10).until(lambda d: d.execute_script(appearance_script)) == appearance
        assert 'WorkspaceGuest' not in guest.title and 'keivenc' not in guest.title
        guest.execute_script("""
          storageRemove('yomux.share.theme.' + shareWorkspace.base);
          storageRemove('yomux.share.appearance.' + shareWorkspace.base);
        """)
        guest.execute_cdp_cmd('Emulation.setEmulatedMedia', {'features': [{'name': 'prefers-color-scheme', 'value': 'light'}]})
        guest.refresh()
        first_visit = WebDriverWait(guest, 10).until(lambda d: d.execute_script(appearance_script))
        assert first_visit['theme'] == 'light' and first_visit['accent'] != appearance['accent']
    except Exception:
        if guest is not None:
            details = guest.execute_script("return {resources:[...shareWorkspace.resources.values()],states:[...fileState].map(([path,state])=>({path,kind:state.kind,content:state.content,preview:previewKindForPath(path,state),error:state.error})),panels:[...panelNodes].map(([item,panel])=>({item,html:panel.outerHTML})),layout:layoutSlots,selection:tabberNormalSelections}")
            (tmp_path / 'share-workspace-debug.json').write_text(json.dumps({'body': guest.find_element(By.TAG_NAME, 'body').text, 'console': guest.get_log('browser'), 'details': details, 'selection': guest.execute_script("return {text:String(getSelection()),active:document.activeElement.tagName+':'+document.activeElement.className}")}, indent=2), encoding='utf-8')
            guest.save_screenshot(str(tmp_path / 'share-workspace-failure.png'))
        raise
    finally:
        if guest is not None:
            remove_browser_test_new_document_scripts(guest)
            guest.execute_script("if (typeof shareWorkspace !== 'undefined') shareWorkspace.drafts.clear()")
            guest.get('about:blank')
        if lease is not None:
            retired = retire_all([lease])
            assert retired['proven_gone'], retired
        browser.get('about:blank')


@pytest.mark.parametrize('transition', ['remove', 'permission'])
def test_guest_startup_reconciles_membership_changed_during_first_read(browser, isolated_dev_server_factory, tmp_path, transition):
    server = isolated_dev_server_factory('share-startup-' + transition, tls=True, auth_bypass=False, session_count=1)
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding='utf-8')
    (server.paths.config_dir / 'settings.yaml').write_bytes(b'general:\n  startup_tips: false\nupdates:\n  notify_level: none\n')
    changing = server.paths.workspace_dir / 'changing.md'
    remaining = server.paths.workspace_dir / 'remaining.txt'
    source = '# Startup transition\n'
    changing.write_text(source, encoding='utf-8')
    remaining.write_text('This grant remains available.\n', encoding='utf-8')
    guest = None
    lease = None
    try:
        login_owner(browser, server)
        WebDriverWait(browser, 15).until(lambda d: d.execute_script("return typeof openFileInEditor==='function' && !!window.YOmuxShare"))
        created = _share_json(browser, '/api/share', {
            'guest': 'StartupGuest', 'minutes': 5, 'limit': 1,
            'resources': [{'kind': 'file', 'path': str(changing), 'writable': False},
                          {'kind': 'file', 'path': str(remaining), 'writable': False}],
        })
        assert created['status'] == 201, created
        grant = created['body']['share']
        first, second = grant['resources']
        base = created['body']['url'].split('#')[0] + '/'
        guest = new_chrome_driver(profile_dir=tmp_path / 'startup-guest')
        lease = WebDriverLease.from_driver(guest)
        barrier = """
          window.__startupDocumentID=crypto.randomUUID();
          window.__heldResourceReads=[];window.__holdResourceRead=true;
          const initialFetch=window.fetch;
          window.fetch=(url,options)=>{
            const target=new URL(String(url),location.href);
            if(window.__holdResourceRead && (!options?.method || options.method==='GET') && target.pathname===__RESOURCE_PATH__)
              return new Promise((resolve,reject)=>window.__heldResourceReads.push(()=>initialFetch(url,options).then(resolve,reject)));
            return initialFetch(url,options);
          };
        """.replace('__RESOURCE_PATH__', json.dumps('/share/' + grant['id'] + '/' + first['id']))
        register_browser_new_document_script(guest, barrier)
        guest.get(created['body']['url'])
        WebDriverWait(guest, 10).until(lambda d: d.execute_script("return window.__heldResourceReads?.length>0 && typeof shareWorkspace!=='undefined' && shareWorkspace.active"))
        document_id = guest.execute_script('return window.__startupDocumentID')
        selections = [{'id': second['id'], 'writable': False}]
        if transition == 'permission':
            selections.insert(0, {'id': first['id'], 'writable': True, 'saved_content': source})
        changed = _share_json(browser, '/api/share?id=' + grant['id'], {
            'revision': grant['revision'], 'resources': selections,
        }, 'PATCH')
        assert changed['status'] == 200, changed
        guest.execute_script('window.__holdResourceRead=false;for(const release of window.__heldResourceReads.splice(0))release();')
        WebDriverWait(guest, 10).until(lambda d: d.execute_script("""
          if(!shareWorkspace.active || !shareWorkspace.chat?.root.isConnected)return false;
          const resource=shareWorkspace.resources.get(arguments[0]);
          if(!resource)return false;
          const state=fileState.get(shareWorkspacePath(resource));
          if(!state || state.kind!=='text' || !state.content.includes('This grant remains available.'))return false;
          if(arguments[2]==='remove')return shareWorkspace.resources.size===1 && !shareWorkspace.resources.has(arguments[1]) && [...fileState.keys()].every(path=>!path.includes(arguments[1]));
          const first=shareWorkspace.resources.get(arguments[1]);
          return shareWorkspace.resources.size===2 && first?.writable && fileState.get(shareWorkspacePath(first))?.kind==='text';
        """, second['id'], first['id'], transition))
        assert guest.execute_script('return window.__startupDocumentID') == document_id
        assert _share_json(browser, '/api/share')['body']['shares'][0]['claimed'] == 1
        guest.find_element(By.CSS_SELECTOR, '.share-sidebar-chat textarea').send_keys('startup converged')
        guest.find_element(By.CSS_SELECTOR, '.share-sidebar-chat button').click()
        WebDriverWait(guest, 10).until(lambda d: 'startup converged' in d.find_element(By.CSS_SELECTOR, '.share-sidebar-chat [role=log]').text)
        assert _share_json(browser, '/api/share?id=' + grant['id'], method='DELETE')['status'] == 200
        WebDriverWait(browser, 10).until(lambda d: d.execute_script("""
          return !hostShareConversations.has(arguments[0]) && !document.querySelector('.share-sidebar-conversations');
        """, grant['id']))
        assert_only_expected_browser_network_error(
            browser, url=server.base_url + '/api/share/chat?id=' + grant['id'],
            reason='status of 410', count=None,
        )
    except Exception:
        if guest is not None:
            details = guest.execute_script("return {active:typeof shareWorkspace!=='undefined' && shareWorkspace.active,chat:typeof shareWorkspace!=='undefined' && !!shareWorkspace.chat,body:document.body.innerText,console:[]} ")
            details['console'] = guest.get_log('browser')
            (tmp_path / 'share-startup-debug.json').write_text(json.dumps(details, indent=2), encoding='utf-8')
            guest.save_screenshot(str(tmp_path / 'share-startup-failure.png'))
        raise
    finally:
        if guest is not None:
            remove_browser_test_new_document_scripts(guest)
            guest.get('about:blank')
        if lease is not None:
            retired = retire_all([lease])
            assert retired['proven_gone'], retired
        browser.get('about:blank')
