# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import pytest

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture


@pytest.mark.browser
def test_synchronous_preview_snapshot_releases_scratch_resize_observers(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    result = browser.execute_script(
        """
        const NativeObserver = window.ResizeObserver;
        const observers = new Set();
        window.ResizeObserver = class extends NativeObserver {
          observe(...args) { observers.add(this); return super.observe(...args); }
          disconnect() { observers.delete(this); return super.disconnect(); }
        };
        try {
          const path = '/home/test/repo/scratch.png';
          setFileState(path, {kind: 'image', size: 42, mtime: 1});
          const snapshot = renderedPreviewSnapshot(path, '');
          return {observers: observers.size, image: snapshot.html.includes('file-editor-preview-image')};
        } finally {
          for (const observer of [...observers]) observer.disconnect();
          window.ResizeObserver = NativeObserver;
        }
        """
    )
    assert result == {"observers": 0, "image": True}, result


@pytest.mark.browser
def test_index_metadata_refresh_preserves_finder_disclosure_and_differ_badges(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    result = browser.execute_script(
        """
        const root = document.createElement('div');
        const finder = document.createElement('div');
        const differ = document.createElement('div');
        differ.className = 'file-explorer-changes-panel';
        root.append(finder, differ);
        document.body.appendChild(root);
        try {
          updateFileTreeRow(finder.appendChild(document.createElement('div')), '/home/test/repo',
            {name: 'folder', kind: 'dir'}, 0, {view: 'finder'});
          updateFileTreeRow(differ.appendChild(document.createElement('div')), '/home/test/repo',
            {name: 'folder', kind: 'dir'}, 0, {view: 'differ', differMode: true});
          const differStatus = differ.querySelector('.file-tree-git-status');
          differStatus.textContent = 'kept';
          differStatus.hidden = false;
          updateFileExplorerIndexedDirectoryRows();
          return {disclosure: finder.querySelector('.file-tree-icon').classList.contains('ui-disclosure-triangle'),
                  differText: differStatus.textContent, differHidden: differStatus.hidden};
        } finally { root.remove(); }
        """
    )
    assert result == {"disclosure": True, "differText": "kept", "differHidden": False}, result


@pytest.mark.browser
@pytest.mark.parametrize("action", ["preview", "finder"])
def test_clipboard_image_source_converts_jpeg_to_native_png(browser, tmp_path, action):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    browser.execute_cdp_cmd("Browser.grantPermissions", {"permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"]})
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const canvas = document.createElement('canvas');
          canvas.width = 3;
          canvas.height = 2;
          canvas.getContext('2d').fillRect(0, 0, 3, 2);
          const jpeg = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg'));
          const originalFetch = window.fetch.bind(window);
          window.fetch = (input, options) => new URL(String(input), location.href).pathname === '/api/fs/raw'
            ? Promise.resolve(new Response(jpeg, {headers: {'Content-Type': 'image/jpeg'}}))
            : originalFetch(input, options);
          const image = new Image();
          image.dataset.resolvedPath = '/home/test/repo/copy.jpg';
          let writeError = '';
          let type = '';
          const write = navigator.clipboard.write.bind(navigator.clipboard);
          navigator.clipboard.write = async items => {
            type = items[0].types.find(type => type.startsWith('image/')) || '';
            try { await write(items); }
            catch (error) { writeError = String(error); throw error; }
          };
          if (arguments[0] === 'finder') await copyImageFileToClipboard(image.dataset.resolvedPath);
          else {
            const blob = await clipboardImageBlobFor(image);
            try { await navigator.clipboard.write([new ClipboardItem({'image/png': blob})]); }
            catch (_) {}
          }
          done({type, writeError, sourceType: jpeg.type});
        })().catch(error => done({error: String(error), stack: error.stack}));
        """,
        action,
    )
    assert "error" not in result, result
    assert result["sourceType"] == "image/jpeg", result
    assert result["writeError"] == "", result
    assert result["type"] == "image/png", result


@pytest.mark.browser
def test_raw_image_popout_snapshot_remains_decodable_after_scratch_cleanup(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const path = '/home/test/repo/popout-image.png';
          const originalFetch = window.fetch.bind(window);
          const fixturePng = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='), ch => ch.charCodeAt(0));
          window.fetch = (input, options) => {
            if (new URL(String(input), location.href).pathname === '/api/fs/raw') {
              return Promise.resolve(new Response(fixturePng, {headers: {'Content-Type': 'image/png'}}));
            }
            return originalFetch(input, options);
          };
          setFileState(path, {kind: 'image', size: 42, mtime: 1});
          const snapshot = await renderedPreviewSnapshotAsync(path, '');
          const frame = document.createElement('iframe');
          document.body.appendChild(frame);
          try {
            writeFilePreviewPopoutDocument(path, frame.contentWindow, snapshot);
            const image = frame.contentDocument.querySelector('img.file-editor-preview-image');
            if (!image) throw new Error(`missing snapshot image: ${snapshot.html}`);
            let decodeError = '';
            try { await image.decode(); } catch (error) { decodeError = String(error); }
            done({width: image.naturalWidth, height: image.naturalHeight, src: image.src, decodeError});
          } finally {
            frame.remove();
          }
        })().catch(error => done({error: String(error), stack: error.stack}));
        """
    )
    assert "error" not in result, result
    assert result["width"] > 0 and result["height"] > 0, result
    assert result["decodeError"] == "", result


@pytest.mark.browser
def test_view_editor_details_summary_entities_preserve_display_and_round_trip(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const path = '/home/test/repo/summary.md';
          const source = ['<details>', '<summary>A &amp; B &lt; C &gt;</summary>', '', 'Body', '', '</details>'].join(String.fromCharCode(10));
          const item = fileEditorItemFor(path);
          setFileState(path, {kind: 'text', content: source, original: source, dirty: false, language: 'markdown'});
          setFileEditorViewMode(path, 'preview', item);
          addFileEditorTabItem(path, item);
          const panel = createFileEditorPanel(item);
          panelNodes.set(item, panel);
          document.getElementById('grid').appendChild(panel);
          try {
            renderFileEditorPanel(panel, item);
            await window.__yomuxTestWaitFor(() => panel._pmView);
            const sourceAfterSave = serializeProseMirrorSource(panel);
            const reopened = panel._pmParser.parse(sourceAfterSave);
            done({summary: panel._pmView.dom.querySelector('summary').textContent,
                  sourceAfterSave, reopenedSummary: reopened.firstChild.attrs.summary});
          } finally {
            destroyProseMirrorPanel(panel);
            panelNodes.delete(item);
            panel.remove();
          }
        })().catch(error => done({error: String(error), stack: error.stack}));
        """
    )
    assert "error" not in result, result
    assert result["summary"] == "A & B < C >", result
    assert result["reopenedSummary"] == "A & B < C >", result
    assert "<summary>A &amp; B &lt; C &gt;</summary>" in result["sourceAfterSave"], result


@pytest.mark.browser
def test_existing_view_editor_rejects_new_unsupported_html_during_source_refresh(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const path = '/home/test/repo/refresh.md';
          const original = 'Original paragraph';
          const item = fileEditorItemFor(path);
          setFileState(path, {kind: 'text', content: original, original, dirty: false, language: 'markdown'});
          setFileEditorViewMode(path, 'preview', item);
          addFileEditorTabItem(path, item);
          const panel = createFileEditorPanel(item);
          panelNodes.set(item, panel);
          document.getElementById('grid').appendChild(panel);
          try {
            renderFileEditorPanel(panel, item);
            await window.__yomuxTestWaitFor(() => panel._pmView);
            const changed = '<div>Unsupported refresh</div>';
            setFileState(path, {...fileState.get(path), content: changed});
            syncProseMirrorPanelSource(panel, path, fileState.get(path), {sourceSurface: 'text-editor'});
            const pane = panel.querySelector('.file-editor-preview-pane-panel');
            done({watermark: Boolean(pane.querySelector('.file-editor-prosemirror-watermark')),
                  editableSurfaces: pane.querySelectorAll('[contenteditable="true"]').length,
                  message: panel._pmError || '', content: fileState.get(path).content});
          } finally {
            destroyProseMirrorPanel(panel);
            panelNodes.delete(item);
            panel.remove();
          }
        })().catch(error => done({error: String(error), stack: error.stack}));
        """
    )
    assert "error" not in result, result
    assert result["watermark"] is True, result
    assert result["editableSurfaces"] == 0, result
    assert "<div>" in result["message"], result
    assert result["content"] == "<div>Unsupported refresh</div>", result


@pytest.mark.browser
@pytest.mark.parametrize("control", ["&#10;", "&#9;", "&#13;"])
def test_markdown_sanitizer_rejects_control_characters_inside_script_protocol(browser, tmp_path, control):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    result = browser.execute_script(
        """
        const root = document.createElement('div');
        root.appendChild(sanitizeMarkdownPreviewHtml(`<a href="java${arguments[0]}script:window.__yomuxSanitizerProbe=1">open</a>`));
        document.body.appendChild(root);
        try {
          delete window.__yomuxSanitizerProbe;
          const link = root.querySelector('a');
          link.click();
          return {href: link.getAttribute('href'), normalizedHref: link.href,
                  executed: window.__yomuxSanitizerProbe === 1};
        } finally {
          root.remove();
          delete window.__yomuxSanitizerProbe;
        }
        """,
        control,
    )
    assert result["executed"] is False, result
    assert result["href"] is None, result
