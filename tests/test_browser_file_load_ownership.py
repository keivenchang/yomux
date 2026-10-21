from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys


def test_restored_read_cannot_replace_newer_codemirror_edits(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    ready = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      (async () => {
        const path = '/home/test/yolomux.dev/restore-race.js';
        const item = registerFileEditorLayoutItem(path);
        const originalRead = window.fetchFileReadPayload;
        let release;
        window.fetchFileReadPayload = () => new Promise(resolve => { release = resolve; });
        const pending = loadFileEditorState(path, null, item);
        const state = fileState.get(path);
        state.kind = 'text';
        state.loading = false;
        state.original = state.content = 'newer content';
        setFileEditorViewMode(path, 'edit', item);
        const panel = createFileEditorPanel(item);
        panelNodes.set(item, panel);
        panel.classList.add('active-pane');
        panel.style.cssText = 'position:fixed;inset:20px;display:flex;width:900px;height:600px;z-index:9999;';
        document.body.append(panel);
        renderFileEditorPanel(panel, item);
        await window.__yomuxTestWaitFor(() => panel._cmView, {timeoutMs: 5000, description: 'editable restored file'});
        window.__guiLoadProbe = {path, item, panel, pending, release, originalRead};
        done(true);
      })().catch(error => done({error: String(error.stack)}));
    """)
    assert ready is True, ready
    content = browser.find_element(By.CSS_SELECTOR, '.file-editor-codemirror-panel .cm-content')
    content.click()
    content.send_keys(Keys.END, ' newer edit')
    metrics = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      (async () => {
        const probe = window.__guiLoadProbe;
        try {
          flushCodeMirrorSource(probe.panel, probe.path);
          const before = fileState.get(probe.path).content;
          probe.release({content: 'obsolete disk', mtime: 1});
          await probe.pending;
          const state = fileState.get(probe.path);
          done({before, content: state.content, dirty: state.dirty,
            document: probe.panel._cmView.state.doc.toString()});
        } finally {
          window.fetchFileReadPayload = probe.originalRead;
          const current = fileState.get(probe.path);
          if (current) { current.original = current.content; current.dirty = false; }
          await closeFileTab(probe.path);
          delete window.__guiLoadProbe;
        }
      })().catch(error => done({error: String(error.stack)}));
    """)
    assert metrics['before'] == 'newer content newer edit', metrics
    assert metrics['content'] == metrics['before'] == metrics['document'], metrics
    assert metrics['dirty'] is True, metrics
