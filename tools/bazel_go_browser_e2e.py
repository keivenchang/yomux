#!/usr/bin/env python3
"""Real Chromium smoke for the Go terminal page and PTY resize contract."""

import http.cookiejar
import json
import os
import shutil
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait


def resolve_source_root() -> Path:
    configured = os.environ.get("YOLOMUX_E2E_SOURCE_ROOT", "").strip()
    source_root = Path(configured) if configured else Path.cwd()
    required = (source_root / "static/yolomux.js", source_root / "tools", source_root / "yolomux_lib")
    if not all(path.exists() for path in required):
        missing = ", ".join(str(path) for path in required if not path.exists())
        raise RuntimeError(f"E2E source root is missing required assets: {missing}")
    return source_root.resolve()


def main(binary: str) -> None:
    with tempfile.TemporaryDirectory(prefix="yolomux-go-e2e-") as tmp:
        root = Path(tmp)
        config = root / "config"
        config.mkdir()
        (config / "auth.yaml").write_text("users:\n  - username: e2e\n    password: test-password\n    role: admin\n", encoding="utf-8")
        (config / "auth-cookie-secret").write_text("11" * 32, encoding="ascii")
        port = 0
        tmux_socket = str(root / "tmux.sock")
        tmux = ("tmux", "-S", tmux_socket)
        sessions = [f"yo-go-e2e-{os.getpid()}-{index}" for index in range(3)]
        session = sessions[0]
        source_root = resolve_source_root()
        private_source_root = root / "server-root"
        shutil.copytree(source_root / "static", private_source_root / "static")
        shutil.copytree(source_root / "tools", private_source_root / "tools")
        shutil.copytree(source_root / "yolomux_lib", private_source_root / "yolomux_lib")
        finder_root = private_source_root / "finder-fixture"
        (finder_root / "nested").mkdir(parents=True)
        (finder_root / "note.md").write_text("hello from finder\n", encoding="utf-8")
        (finder_root / "ui-remove-me.txt").write_text("delete through Finder\n", encoding="utf-8")
        (finder_root / "index.html").write_text("<script>document.body.textContent = 'preview'</script>\n", encoding="utf-8")
        (finder_root / "nested" / "needle.txt").write_text("nested search hit\n", encoding="utf-8")
        source_file = finder_root / "editor.go"
        source_original = "package main\n\nfunc main() {}\n"
        source_file.write_text(source_original, encoding="utf-8")
        markdown_file = finder_root / "editor.md"
        markdown_original = "# Editor\n\n<!-- hidden metadata -->\n\nA paragraph before the upload.\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n<u>under</u> and <mark>marked</mark>.\n<span>literal HTML</span>\n"
        markdown_file.write_text(markdown_original, encoding="utf-8")
        git_root = finder_root / "git-fixture"
        git_root.mkdir()
        git_note = git_root / "note.txt"
        git_note.write_text("base\n", encoding="utf-8")
        subprocess.run(("git", "init", "-q", str(git_root)), check=True)
        subprocess.run(("git", "-C", str(git_root), "config", "user.email", "e2e@example.invalid"), check=True)
        subprocess.run(("git", "-C", str(git_root), "config", "user.name", "YOLOmux E2E"), check=True)
        subprocess.run(("git", "-C", str(git_root), "add", "note.txt"), check=True)
        subprocess.run(("git", "-C", str(git_root), "commit", "-qm", "base"), check=True)
        git_note.write_text("changed\n", encoding="utf-8")
        subprocess.run(("git", "-C", str(git_root), "commit", "-qam", "changed"), check=True)
        git_head = subprocess.check_output(("git", "-C", str(git_root), "rev-parse", "HEAD"), text=True).strip()
        git_note.write_text("working\n", encoding="utf-8")
        created_sessions = []
        for session_name in sessions:
            if subprocess.run((*tmux, "has-session", "-t", session_name), check=False, stderr=subprocess.DEVNULL).returncode != 0:
                subprocess.run((*tmux, "new-session", "-d", "-s", session_name), check=True)
                created_sessions.append(session_name)

        def start_server():
            nonlocal port
            generation = len(list(root.glob("server-*.txt"))) + 1
            log_path = root / f"server-{generation}.txt"
            log_file = log_path.open("w", encoding="utf-8")
            environment = os.environ.copy()
            environment["YOLOMUX_TMUX_SOCKET"] = tmux_socket
            # Keep this deliberate backpressure fixture private to the uncached browser contract:
            # the normal queue remains 256, while Chromium proves that an evicted resource reaches
            # the browser's repair path instead of silently disappearing.
            environment["YOLOMUX_GO_EVENT_QUEUE_SIZE"] = "1"
            environment["YOLOMUX_GO_EVENT_STREAM_DELAY_MS"] = "500"
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("0.0.0.0", port))
                probe.listen(128)
                port = int(probe.getsockname()[1])
                process = subprocess.Popen([binary, "--host", "0.0.0.0", "--port", str(port), "--listen-fd", str(probe.fileno()), "--root", str(private_source_root), "--config", str(config)], env=environment, stdout=log_file, stderr=subprocess.STDOUT, pass_fds=(probe.fileno(),))
            log_file.close()
            for _ in range(80):
                try:
                    with urllib.request.urlopen(f"https://127.0.0.1:{port}/login", context=ssl._create_unverified_context(), timeout=1):
                        pass
                    return process
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for session_name in created_sessions:
                subprocess.run((*tmux, "kill-session", "-t", session_name), check=False)
            raise SystemExit("Go server did not become ready")

        proc = start_server()
        driver = None

        def stop_server(force=False):
            nonlocal proc
            if proc is None or proc.poll() is not None:
                proc = None
                return
            if force:
                proc.kill()
            else:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            proc = None

        def assert_server_identity(driver, process):
            identity = driver.execute_async_script("""
                const done = arguments[arguments.length - 1];
                fetch('/api/system-status', {cache: 'no-store', credentials: 'same-origin'})
                  .then(response => response.json().then(body => done({status: response.status, body})))
                  .catch(error => done({error: String(error)}));
            """)
            if identity.get("status") != 200 or identity.get("body", {}).get("server", {}).get("pid") != process.pid:
                raise SystemExit(f"Go Phase 3 listener identity failed: expected pid {process.pid}, got {identity}")

        try:
            options = Options()
            options.binary_location = os.environ.get("CHROME_BIN", "/usr/bin/google-chrome")
            for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--ignore-certificate-errors", "--window-size=1200,800"):
                options.add_argument(arg)
            driver_path = os.environ.get("CHROMEDRIVER_BIN")
            if driver_path:
                driver = webdriver.Chrome(service=Service(executable_path=driver_path), options=options)
            else:
                driver = webdriver.Chrome(options=options)
            try:
                driver.get(f"https://127.0.0.1:{port}/login")
                driver.find_element("name", "username").send_keys("e2e")
                driver.find_element("name", "password").send_keys("test-password")
                driver.find_element("css selector", "button[type=submit]").click()
                time.sleep(0.5)
                driver.execute_script("localStorage.clear(); sessionStorage.clear();")
                driver.get(f"https://127.0.0.1:{port}/?sessions={session}&layout=left&tabs=left:{session}")
                time.sleep(1)
                deadline = time.time() + 15
                while time.time() < deadline and not driver.find_elements("css selector", f"#term-{session} .xterm"):
                    time.sleep(0.1)
                result = driver.execute_script("""
                    if (typeof terminals === 'undefined') return {error: 'terminal runtime did not load', url: location.href, title: document.title};
                    const item = terminals.get(arguments[0]);
                    if (!item?.term || item.socket?.readyState !== WebSocket.OPEN) return {
                        error: 'terminal did not connect',
                        url: location.href,
                        body: document.body.innerText.slice(0, 300),
                        bootstrap: document.getElementById('yolomux-bootstrap')?.textContent?.slice(0, 2000) || null,
                        activeSessions: typeof activeSessions !== 'undefined' ? activeSessions : null,
                        layoutSlots: typeof layoutSlots !== 'undefined' ? layoutSlots : null,
                        terminal: Boolean(item?.term),
                        socket: item?.socket?.readyState || null,
                    };
                    setBadConnectionCursorState(false);
                    const before = {cols: item.term.cols, rows: item.term.rows, cursorBlink: item.term.options.cursorBlink, body: document.body.className};
                    item.container.style.width = '500px';
                    item.container.style.height = '200px';
                    fitTerminal(arguments[0], {force: true, claim: true});
                    return {before, after: {cols: item.term.cols, rows: item.term.rows, cursorBlink: item.term.options.cursorBlink}};
                """, session)
                if result.get("error") or result["after"]["cols"] >= result["before"]["cols"] or result["after"]["rows"] >= result["before"]["rows"] or result["after"]["cursorBlink"] is not True:
                    diagnostics = driver.execute_async_script("""
                        const done = arguments[arguments.length - 1];
                        const session = arguments[0];
                        Promise.all(['/api/tmux-session-exists?session=' + encodeURIComponent(session), '/api/ensure-session?session=' + encodeURIComponent(session)]
                          .map(url => fetch(url, {method: url.includes('ensure-session') ? 'POST' : 'GET', cache: 'no-store', credentials: 'same-origin'})
                            .then(response => response.text().then(body => ({url, status: response.status, body})))
                            .catch(error => ({url, error: String(error)}))))
                          .then(api => done({api, performance: performance.getEntriesByType('resource').map(entry => entry.name).filter(name => name.includes('/api/')).slice(-30), status: document.getElementById('status')?.innerText || ''}))
                          .catch(error => done({error: String(error)}));
                    """, session)
                    result["diagnostics"] = diagnostics
                    result["browser_log"] = driver.get_log("browser")
                    raise SystemExit(f"Go Chromium terminal E2E failed: {result}")
                assert_server_identity(driver, proc)
                phase3 = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const events = new EventSource('/api/client-events?channels=core,events&client_id=phase3-e2e');
                    const seen = [];
                    events.addEventListener('ready', event => { seen.push('ready'); fetch('/api/event', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session: '', type: 'note', message: 'phase3 browser event', details: {source: 'phase3-e2e'}})}); if (seen.includes('published')) { events.close(); done(seen); } });
                    events.addEventListener('event_log_changed', event => { seen.push('published'); if (seen.includes('ready')) { events.close(); done(seen); } });
                    setTimeout(() => { events.close(); done({error: 'client-events timeout', seen, response: window.__phase3EventResponse || ''}); }, 5000);
                """)
                if not isinstance(phase3, list) or 'ready' not in phase3 or 'published' not in phase3:
                    raise SystemExit(f"Go Phase 3 Chromium E2E failed: {phase3}")
                editor = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const sourcePath = arguments[0];
                    const markdownPath = arguments[1];
                    const sourceOriginal = arguments[2];
                    const markdownOriginal = arguments[3];
                    const waitFor = (predicate, description, timeoutMs = 12000) => new Promise((resolve, reject) => {
                      const deadline = performance.now() + timeoutMs;
                      const poll = () => {
                        try {
                          const value = predicate();
                          if (value) { resolve(value); return; }
                        } catch (error) { reject(error); return; }
                        if (performance.now() >= deadline) { reject(new Error(`${description} timed out`)); return; }
                        setTimeout(poll, 40);
                      };
                      poll();
                    });
                    const json = async (url, options = {}) => {
                      const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin', ...options});
                      const body = await response.json();
                      return {status: response.status, body};
                    };
                    const operationPayload = async (url, label, options = {}) => {
                      const accepted = await json(url, options);
                      if (accepted.status === 200) return accepted.body?.state === 'ready' ? accepted.body.data : accepted.body;
                      if (accepted.status !== 202 || !accepted.body.operation?.id) throw new Error(`${label} was not accepted: ${JSON.stringify(accepted)}`);
                      for (let attempt = 0; attempt < 160; attempt += 1) {
                        const terminal = await json('/api/operations/' + encodeURIComponent(accepted.body.operation.id));
                        if (terminal.status === 202) {
                          await new Promise(resolve => setTimeout(resolve, 25));
                          continue;
                        }
                        if (terminal.status !== 200 || terminal.body.state !== 'ready') throw new Error(`${label} terminal: ${JSON.stringify(terminal)}`);
                        return terminal.body.data;
                      }
                      throw new Error(`${label} timed out`);
                    };
                    const panelFor = path => fileEditorPanelsForPath(path)[0];
                    const editorText = path => panelFor(path)?._cmView?.state?.doc?.toString?.() || '';
                    (async () => {
                      try {
                        const sourceItem = await openFileInEditor(sourcePath, {name: 'editor.go'}, {userInitiated: true, viewMode: 'edit'});
                        await waitFor(() => panelFor(sourcePath)?._cmView && editorText(sourcePath) === sourceOriginal, 'TextEdit open');
                        const sourcePanel = panelFor(sourcePath);
                        if (!sourcePanel?.querySelector('.cm-content')) throw new Error('TextEdit CodeMirror content is missing');
                        const sourceInfo = await json('/api/fs/info?path=' + encodeURIComponent(sourcePath));
                        if (sourceInfo.status !== 200 || sourceInfo.body.data?.kind !== 'file') throw new Error(`TextEdit info mismatch: ${JSON.stringify(sourceInfo)}`);
                        done({
                          sourceItem,
                          sourceText: editorText(sourcePath),
                          sourceMtime: sourceInfo.body.data.mtime_ns,
                          sourcePanel: true,
                          markdownOriginal,
                        });
                      } catch (error) {
                        done({error: String(error?.stack || error)});
                      }
                    })();
                """, str(source_file), str(markdown_file), source_original, markdown_original)
                if editor.get('error') or editor.get('sourceText') != source_original or editor.get('sourcePanel') is not True:
                    raise SystemExit(f"Go Phase 6 TextEdit open failed: {editor}")
                source_element = driver.find_element("css selector", f'.file-editor-panel[data-file-path="{source_file}"] .cm-content')
                source_element.click()
                driver.execute_script("fileEditorAutosaveEnabled = false;")
                native_suffix = "// native TextEdit input"
                for character in native_suffix:
                    source_element.send_keys(character)
                try:
                    WebDriverWait(driver, 12).until(
                        lambda current: current.execute_script(
                            """
                            const path = arguments[0];
                            const suffix = arguments[1];
                            const panel = fileEditorPanelsForPath(path)[0];
                            const text = panel?._cmView?.state?.doc?.toString?.() || '';
                            const state = fileState.get(path);
                            return text.includes(suffix) && state?.content === text && state?.dirty === true;
                            """,
                            str(source_file),
                            native_suffix,
                        )
                    )
                except TimeoutException as error:
                    diagnostics = driver.execute_script(
                        """
                        const path = arguments[0];
                        const panel = fileEditorPanelsForPath(path)[0];
                        const text = panel?._cmView?.state?.doc?.toString?.() || '';
                        const state = fileState.get(path);
                        return {text, stateContent: state?.content || '', dirty: state?.dirty === true, activeTag: document.activeElement?.tagName || '', activeClass: document.activeElement?.className || ''};
                        """,
                        str(source_file),
                    )
                    raise SystemExit(f"Go Phase 6 native TextEdit state propagation failed: {diagnostics}") from error
                text_after_input = driver.execute_script("return fileEditorPanelsForPath(arguments[0])[0]?._cmView?.state?.doc?.toString?.() || '';", str(source_file))
                save_button = driver.find_element("css selector", f'.file-editor-panel[data-file-path="{source_file}"] .file-editor-save-panel')
                for close_button in driver.find_elements("css selector", "[data-toast-close]"):
                    if close_button.is_displayed():
                        close_button.click()
                save_selector = f'.file-editor-panel[data-file-path="{source_file}"] .file-editor-save-panel'
                try:
                    WebDriverWait(driver, 12).until(
                        lambda current: current.execute_script(
                            """
                            const button = document.querySelector(arguments[1]);
                            if (fileState.get(arguments[0])?.dirty !== true || button?.disabled) return false;
                            const rect = button?.getBoundingClientRect();
                            if (!rect || rect.width <= 0 || rect.height <= 0) return false;
                            const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
                            return top === button || button.contains(top);
                            """,
                            str(source_file),
                            save_selector,
                        )
                    )
                except TimeoutException as error:
                    diagnostics = driver.execute_script(
                        """
                        const button = document.querySelector(arguments[1]);
                        const rect = button?.getBoundingClientRect();
                        return {
                          dirty: fileState.get(arguments[0])?.dirty === true,
                          disabled: button?.disabled,
                          rect: rect?.toJSON?.() || null,
                          top: rect ? document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)?.outerHTML?.slice(0, 240) || '' : '',
                          toasts: [...document.querySelectorAll('.toast-header')].map(node => node.textContent?.trim() || ''),
                        };
                        """,
                        str(source_file),
                        save_selector,
                    )
                    raise SystemExit(f"Go Phase 6 save control never became interactable: {diagnostics}") from error
                save_button.click()
                save_result = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const path = arguments[0];
                    const expected = arguments[1];
                    const waitFor = () => {
                      const state = fileState.get(path);
                      const panel = fileEditorPanelsForPath(path)[0];
                      return state?.dirty === false && panel?._cmView?.state?.doc?.toString?.() === expected;
                    };
                    const poll = () => waitFor() ? done({saved: true, text: fileEditorPanelsForPath(path)[0]._cmView.state.doc.toString()}) : setTimeout(poll, 40);
                    poll();
                    setTimeout(() => done({error: 'TextEdit save timeout'}), 12000);
                """, str(source_file), text_after_input)
                if save_result.get('error'):
                    raise SystemExit(f"Go Phase 6 TextEdit save failed: {save_result}")
                if source_file.read_text(encoding="utf-8") != text_after_input:
                    raise SystemExit("Go Phase 6 TextEdit disk content did not match the native editor input")
                local_conflict_suffix = "\n// local unsaved conflict"
                for character in local_conflict_suffix:
                    source_element.send_keys(character)
                try:
                    WebDriverWait(driver, 12).until(
                        lambda current: current.execute_script(
                            """
                            const path = arguments[0];
                            const expected = arguments[1];
                            const panel = fileEditorPanelsForPath(path)[0];
                            const text = panel?._cmView?.state?.doc?.toString?.() || '';
                            const state = fileState.get(path);
                            return text.endsWith(expected) && state?.content === text && state?.dirty === true;
                            """,
                            str(source_file),
                            local_conflict_suffix,
                        )
                    )
                except TimeoutException as error:
                    raise SystemExit("Go Phase 6 local unsaved conflict input did not reach file state") from error
                source_file.write_text("external replacement\n", encoding="utf-8")
                conflict = driver.execute_async_script(
                    """
                    const done = arguments[arguments.length - 1];
                    const path = arguments[0];
                    const panel = fileEditorPanelsForPath(path)[0];
                    let finished = false;
                    const finish = value => {
                      if (finished) return;
                      finished = true;
                      done(value);
                    };
                    const deadline = performance.now() + 12000;
                    const observe = () => {
                      const dialog = document.querySelector('.file-editor-conflict-dialog');
                      if (dialog) {
                        const buttons = [...dialog.querySelectorAll('button')];
                        const cancel = buttons.find(button => /^cancel$/i.test(button.textContent?.trim() || ''));
                        if (!cancel) {
                          finish({error: 'conflict dialog has no Cancel action', text: dialog.textContent || ''});
                          return;
                        }
                        const dialogText = dialog.textContent || '';
                        cancel.click();
                        savePromise.then(result => finish({dialog: true, cancelled: result === false, result, text: dialogText, editor: panel?._cmView?.state?.doc?.toString?.() || '', dirty: fileState.get(path)?.dirty === true}), error => finish({error: String(error?.stack || error)}));
                        return;
                      }
                      if (performance.now() >= deadline) {
                        finish({error: 'TextEdit conflict dialog timeout', editor: panel?._cmView?.state?.doc?.toString?.() || '', dirty: fileState.get(path)?.dirty === true});
                        return;
                      }
                      setTimeout(observe, 40);
                    };
                    const savePromise = saveFileEditor(path, panel);
                    observe();
                    """,
                    str(source_file),
                )
                if conflict.get('error') or conflict.get('dialog') is not True or conflict.get('cancelled') is not True or conflict.get('dirty') is not True or local_conflict_suffix not in conflict.get('editor', ''):
                    raise SystemExit(f"Go Phase 6 expected-mtime conflict failed: {conflict}")
                markdown = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const path = arguments[0];
                    const expected = arguments[1];
                    (async () => {
                      try {
                        const item = await openFileInEditor(path, {name: 'editor.md'}, {userInitiated: true, viewMode: 'split'});
                        const panel = () => fileEditorPanelsForPath(path)[0];
                        const waitFor = (predicate, description, timeoutMs = 12000) => new Promise((resolve, reject) => {
                          const deadline = performance.now() + timeoutMs;
                          const poll = () => {
                            try {
                              const value = predicate();
                              if (value) { resolve(value); return; }
                            } catch (error) { reject(error); return; }
                            if (performance.now() >= deadline) { reject(new Error(`${description} timed out`)); return; }
                            setTimeout(poll, 40);
                          };
                          poll();
                        });
                        await waitFor(() => panel()?._cmView && panel()?._pmView && panel()._cmView.state.doc.toString() === expected, 'ViewEdit open');
                        const view = panel()._pmView;
                        let paragraphStart = 1;
                        let paragraph = null;
                        view.state.doc.forEach((node, offset) => {
                          if (!paragraph && node.type.name === 'paragraph' && node.textContent.includes('A paragraph before')) {
                            paragraph = node;
                            paragraphStart = offset + 1;
                          }
                        });
                        if (!paragraph) throw new Error('ViewEdit paragraph selection target is missing');
                        view.dispatch(view.state.tr.setSelection(YOLOmuxProseMirror.TextSelection.create(view.state.doc, paragraphStart + paragraph.content.size)));
                        view.focus();
                        const png = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='), byte => byte.charCodeAt(0));
                        const transfer = new DataTransfer();
                        transfer.items.add(new File([png], 'screen.png', {type: 'image/png'}));
                        const event = new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: transfer});
                        document.dispatchEvent(event);
                        const imageFor = () => panel()._pmView?.dom?.querySelector('img.markdown-preview-image');
                        await waitFor(() => imageFor(), 'ViewEdit upload image');
                        try {
                          await waitFor(() => imageFor()?.naturalWidth > 0, 'ViewEdit uploaded image decode');
                        } catch (error) {
                          const rawRequests = [...performance.getEntriesByType('resource')]
                            .map(entry => entry.name)
                            .filter(name => name.includes('/api/fs/raw'));
                          const images = [...panel()._pmView.dom.querySelectorAll('img')].map(node => ({outerHTML: node.outerHTML, src: node.src, resolvedPath: node.dataset.resolvedPath || '', naturalWidth: node.naturalWidth, naturalHeight: node.naturalHeight, complete: node.complete}));
                          const image = imageFor();
                          throw new Error(`${error.message}; image=${JSON.stringify({src: image?.src || '', resolvedPath: image?.dataset.resolvedPath || '', naturalWidth: image?.naturalWidth || 0, naturalHeight: image?.naturalHeight || 0, complete: image?.complete === true})}; images=${JSON.stringify(images)}; rawRequests=${JSON.stringify(rawRequests)}; source=${JSON.stringify(fileEditorPanelState(panel()).content)}`);
                        }
                        const image = imageFor();
                        const imageReferenceFor = value => String(value || '').split(String.fromCharCode(10)).find(line => line.includes('![') && line.includes('](editor/')) || '';
                        try {
                          await waitFor(() => imageReferenceFor(fileEditorPanelState(panel()).content), 'ViewEdit source upload synchronization');
                        } catch (error) {
                          const currentPanel = panel();
                          const currentState = fileEditorPanelState(currentPanel);
                          done({
                            error: String(error?.stack || error),
                            afterImage: {
                              source: currentState?.content || '',
                              dirty: currentState?.dirty === true,
                              pmText: currentPanel?._pmView?.state?.doc?.textContent || '',
                              pmJson: currentPanel?._pmView?.state?.doc?.toJSON?.() || null,
                              serialized: serializeProseMirrorSource(currentPanel),
                              pmSource: currentPanel?._pmSource || null,
                              pmSerializeTimer: Boolean(currentPanel?._pmSerializeTimer),
                              selection: currentPanel?._pmView?.state?.selection ? {
                                from: currentPanel._pmView.state.selection.from,
                                to: currentPanel._pmView.state.selection.to,
                              } : null,
                              cm: currentPanel?._cmView?.state?.doc?.toString?.() || '',
                            },
                          });
                          return;
                        }
                        const source = fileEditorPanelState(panel()).content;
                        const sourceImageReference = imageReferenceFor(source);
                        await waitFor(() => panel()._cmView.state.doc.toString() === source, 'ViewEdit source synchronization');
                        done({item, prevented: event.defaultPrevented, source, sourceImageReference, serialized: serializeProseMirrorSource(panel()), imageSource: image.dataset.resolvedPath || '', imageReady: image.naturalWidth > 0, cm: panel()._cmView.state.doc.toString()});
                      } catch (error) {
                        done({error: String(error?.stack || error)});
                      }
                    })();
                """, str(markdown_file), markdown_original)
                if markdown.get('error') or markdown.get('prevented') is not True or markdown.get('imageReady') is not True or not markdown.get('sourceImageReference') or not markdown.get('imageSource') or markdown.get('cm') != markdown.get('source') or markdown.get('serialized') != markdown.get('source'):
                    raise SystemExit(f"Go Phase 7 ViewEdit upload/round-trip failed: {markdown}")
                markdown_save = driver.find_element("css selector", f'.file-editor-panel[data-file-path="{markdown_file}"] .file-editor-save-panel')
                try:
                    WebDriverWait(driver, 12).until(
                        lambda current: current.execute_script(
                            """
                            const button = document.querySelector(arguments[0]);
                            const path = arguments[1];
                            return button && !button.disabled && fileState.get(path)?.dirty === true;
                            """,
                            f'.file-editor-panel[data-file-path="{markdown_file}"] .file-editor-save-panel',
                            str(markdown_file),
                        )
                    )
                except TimeoutException as error:
                    raise SystemExit("Go Phase 7 save control never became enabled") from error
                markdown_save.click()
                markdown_save_result = driver.execute_async_script(
                    """
                    const done = arguments[arguments.length - 1];
                    const path = arguments[0];
                    const expected = arguments[1];
                    const deadline = performance.now() + 12000;
                    const poll = () => {
                      const panel = fileEditorPanelsForPath(path)[0];
                      const state = fileState.get(path);
                      const text = panel?._cmView?.state?.doc?.toString?.() || '';
                      if (state?.dirty === false && text === expected) {
                        done({saved: true, text});
                        return;
                      }
                      if (performance.now() >= deadline) {
                        done({error: 'ViewEdit save timeout', text, dirty: state?.dirty === true});
                        return;
                      }
                      setTimeout(poll, 40);
                    };
                    poll();
                    """,
                    str(markdown_file),
                    markdown['source'],
                )
                if markdown_save_result.get('error') or markdown_save_result.get('saved') is not True:
                    raise SystemExit(f"Go Phase 7 Markdown save failed: {markdown_save_result}")
                if markdown_file.read_text(encoding="utf-8") != markdown['source']:
                    raise SystemExit("Go Phase 7 Markdown disk content did not match serialized ViewEdit source")
                markdown_reopened = driver.execute_async_script(
                    """
                    const done = arguments[arguments.length - 1];
                    const path = arguments[0];
                    const expected = arguments[1];
                    (async () => {
                      try {
                        await reloadOpenFileFromDisk(path, {force: true});
                        const deadline = performance.now() + 12000;
                        const poll = () => {
                          const panel = fileEditorPanelsForPath(path)[0];
                          const cm = panel?._cmView?.state?.doc?.toString?.() || '';
                          const pm = panel?._pmView;
                          if (cm === expected && pm?.dom?.isConnected && serializeProseMirrorSource(panel) === expected) {
                            done({reopened: true, cm, serialized: serializeProseMirrorSource(panel), pmReady: true});
                            return;
                          }
                          if (performance.now() >= deadline) {
                            done({error: 'ViewEdit reopen timeout', cm, serialized: pm ? serializeProseMirrorSource(panel) : '', pmReady: Boolean(pm?.dom?.isConnected)});
                            return;
                          }
                          setTimeout(poll, 40);
                        };
                        poll();
                      } catch (error) {
                        done({error: String(error?.stack || error)});
                      }
                    })();
                    """,
                    str(markdown_file),
                    markdown['source'],
                )
                if markdown_reopened.get('error') or markdown_reopened.get('reopened') is not True or markdown_reopened.get('serialized') != markdown['source']:
                    raise SystemExit(f"Go Phase 7 Markdown reopen failed: {markdown_reopened}")
                finder = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const root = arguments[0];
                    const note = root + '/note.md';
                    const json = async (path, options = {}) => {
                      const response = await fetch(path, {cache: 'no-store', credentials: 'same-origin', ...options});
                      const body = await response.json();
                      return {status: response.status, body};
                    };
                    const requireStatus = (result, status, label) => {
                      if (result.status !== status) throw new Error(`${label}: ${result.status} ${JSON.stringify(result.body)}`);
                      if (result.body?.state === 'ready') return result.body.data;
                      return result.body;
                    };
                    const operationPayload = async (url, label, options = {}) => {
                      const accepted = await json(url, options);
                      if (accepted.status === 200) return accepted.body?.state === 'ready' ? accepted.body.data : accepted.body;
                      if (accepted.status !== 202 || !accepted.body.operation?.id) throw new Error(`${label} was not accepted: ${JSON.stringify(accepted)}`);
                      for (let attempt = 0; attempt < 160; attempt += 1) {
                        const terminal = await json('/api/operations/' + encodeURIComponent(accepted.body.operation.id));
                        if (terminal.status === 202) {
                          await new Promise(resolve => setTimeout(resolve, 25));
                          continue;
                        }
                        if (terminal.status !== 200 || terminal.body.state !== 'ready') throw new Error(`${label} terminal: ${JSON.stringify(terminal)}`);
                        return terminal.body.data;
                      }
                      throw new Error(`${label} timed out`);
                    };
                    (async () => {
                      const listing = await operationPayload('/api/fs/list?path=' + encodeURIComponent(root), 'list');
                      if (!listing.entries.some(entry => entry.name === 'nested' && entry.kind === 'dir')) throw new Error('list missed nested directory');
                      const read = requireStatus(await json('/api/fs/read?path=' + encodeURIComponent(note)), 200, 'read');
                      if (read.content !== 'hello from finder\\n') throw new Error('read content mismatch');
                      const info = requireStatus(await json('/api/fs/info?path=' + encodeURIComponent(note)), 200, 'info');
                      if (info.kind !== 'file') throw new Error('info kind mismatch');
                      const rawResponse = await fetch('/api/fs/raw?path=' + encodeURIComponent(note), {cache: 'no-store', credentials: 'same-origin', headers: {Range: 'bytes=0-4'}});
                      if (rawResponse.status !== 206 || await rawResponse.text() !== 'hello') throw new Error('raw range mismatch');
                      const batch = await operationPayload('/api/fs/batch', 'batch', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({requests: [{id: 'list', type: 'list', path: root}, {id: 'info', type: 'info', path: note}]})});
                      if (batch.responses.length !== 2 || !batch.responses.every(response => response.ok)) throw new Error('batch response mismatch');
                      const candidate = requireStatus(await json('/api/fs/resolve-file-candidates', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({paths: [root + '/missing.txt', note]})}), 200, 'candidates');
                      if (candidate.path !== note || candidate.misses.length !== 1) throw new Error('candidate resolution mismatch');
                      const created = root + '/created.txt';
                      await operationPayload('/api/fs/write', 'write', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: created, content: 'created'})});
                      await operationPayload('/api/fs/rename', 'rename', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: created, new_name: 'renamed.txt'})});
                      await operationPayload('/api/fs/delete', 'delete', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: root + '/renamed.txt'})});
                      const emptyDir = root + '/empty-dir';
                      await operationPayload('/api/fs/mkdir', 'mkdir', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: emptyDir})});
                      const deletedDir = await operationPayload('/api/fs/delete', 'delete directory', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: emptyDir})});
                      if (deletedDir.deleted !== true || deletedDir.kind !== 'dir') throw new Error('directory delete mismatch');
                      const count = await operationPayload('/api/batch/count?path=' + encodeURIComponent(root), 'count');
                      if (count.files < 3 || count.recursive !== true) throw new Error('count mismatch: ' + JSON.stringify(count));
                      const zipResponse = await fetch('/api/batch/zip?path=' + encodeURIComponent(root), {cache: 'no-store', credentials: 'same-origin'});
                      const zipBytes = await zipResponse.arrayBuffer();
                      if (zipResponse.status !== 200 || zipBytes.byteLength < 22 || !zipResponse.headers.get('Content-Disposition')?.includes('.zip')) throw new Error('zip mismatch: ' + zipResponse.status + ' ' + zipBytes.byteLength);
                      const htmlResponse = await fetch('/api/batch/html-preview?path=' + encodeURIComponent(root + '/index.html'), {cache: 'no-store', credentials: 'same-origin'});
                      const htmlBody = await htmlResponse.text();
                      if (htmlResponse.status !== 200 || !htmlBody.includes('sandbox="allow-scripts allow-forms allow-popups"') || !htmlBody.includes('&lt;script&gt;')) throw new Error('html preview mismatch: ' + htmlResponse.status);
                      const streamResponse = await fetch('/api/fs/search-stream?root=' + encodeURIComponent(root) + '&query=needle&limit=10', {cache: 'no-store', credentials: 'same-origin', headers: {Accept: 'text/event-stream'}});
                      const streamBody = await streamResponse.text();
                      if (streamResponse.status !== 200 || !streamBody.includes('event: chunk') || !streamBody.includes('needle.txt')) throw new Error('search stream mismatch');
                      const accepted = await json('/api/fs/search?root=' + encodeURIComponent(root) + '&query=needle&recursive=1&limit=10');
                      if (accepted.status !== 202 || !accepted.body.operation?.id) throw new Error('recursive search was not accepted');
                      let terminal = null;
                      for (let attempt = 0; attempt < 80; attempt += 1) {
                        const operation = await json('/api/operations/' + encodeURIComponent(accepted.body.operation.id));
                        if (operation.status !== 202) { terminal = operation; break; }
                        await new Promise(resolve => setTimeout(resolve, 25));
                      }
                      if (!terminal || terminal.status !== 200 || terminal.body.state !== 'ready' || terminal.body.data.count !== 1) throw new Error('recursive search terminal mismatch: ' + JSON.stringify(terminal));
                      const uploadForm = new FormData();
                      uploadForm.append('files', new Blob(['uploaded'], {type: 'text/plain'}), 'asset.txt');
                      const upload = await operationPayload('/api/upload?editor_path=' + encodeURIComponent(note), 'upload', {method: 'POST', body: uploadForm});
                      if (upload.target_source !== 'editor_file_directory' || upload.files?.length !== 1) throw new Error('upload mismatch: ' + JSON.stringify(upload));
                      const forbidden = await json('/api/fs/read?path=%2Fetc%2Fpasswd');
                      if (forbidden.status !== 403) throw new Error('permission failure mismatch: ' + JSON.stringify(forbidden));
                      const watch = requireStatus(await json('/api/watch/roots', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({client_id: 'finder-e2e', roots: [root]})}), 200, 'watch roots');
                      done({listing: listing.entries.length, read: read.content, infoKind: info.kind, batch: batch.responses.length, candidate: candidate.path, count: count.files, zipBytes: zipBytes.byteLength, htmlPreview: true, stream: true, recursiveSearch: terminal.body.data.count, upload: upload.files[0].relative_path, watchToken: watch.watch_token});
                    })().catch(error => done({error: String(error)}));
                """, str(finder_root))
                if finder.get('error'):
                    raise SystemExit(f"Go Phase 4/5 Finder Chromium E2E failed: {finder}")
                driver.get(f"https://127.0.0.1:{port}/?sessions=files,{session}&layout=left&tabs=left:files")
                ui_deadline = time.time() + 15
                while time.time() < ui_deadline and not driver.find_elements("css selector", "#panel-__finder__ .file-explorer-tree-panel"):
                    time.sleep(0.1)
                finder_ui = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const root = arguments[0];
                    const waitFor = (predicate, timeoutMs = 12000) => new Promise((resolve, reject) => {
                      const deadline = performance.now() + timeoutMs;
                      const poll = () => {
                        try {
                          const value = predicate();
                          if (value) { resolve(value); return; }
                        } catch (error) { reject(error); return; }
                        if (performance.now() >= deadline) { reject(new Error('Finder UI condition timed out')); return; }
                        setTimeout(poll, 50);
                      };
                      poll();
                    });
                    const row = path => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${CSS.escape(path)}"]`);
                    (async () => {
                      if (typeof openFileExplorerAt !== 'function') throw new Error('Finder UI opener is unavailable');
                      const opened = await openFileExplorerAt(root, {user: true, manualSelection: true});
                      if (opened !== true) throw new Error(`Finder UI did not open: ${opened}`);
                      await waitFor(() => row(root + '/nested') && row(root + '/note.md'));
                      const initial = {
                        panel: Boolean(document.querySelector('#panel-__finder__')),
                        nested: Boolean(row(root + '/nested')),
                        note: Boolean(row(root + '/note.md')),
                        expanded: row(root + '/nested')?.getAttribute('aria-expanded') || null,
                      };
                      row(root + '/nested').click();
                      await waitFor(() => row(root + '/nested')?.getAttribute('aria-expanded') === 'true' && row(root + '/nested/needle.txt'));
                      const expanded = {
                        expanded: row(root + '/nested')?.getAttribute('aria-expanded') || null,
                        needle: Boolean(row(root + '/nested/needle.txt')),
                      };
                      row(root + '/nested').click();
                      await waitFor(() => row(root + '/nested')?.getAttribute('aria-expanded') === 'false' && !row(root + '/nested/needle.txt'));
                      row(root + '/nested').click();
                      await waitFor(() => row(root + '/nested')?.getAttribute('aria-expanded') === 'true' && row(root + '/nested/needle.txt'));
                      const reexpanded = {
                        expanded: row(root + '/nested')?.getAttribute('aria-expanded') || null,
                        needle: Boolean(row(root + '/nested/needle.txt')),
                      };
                      const removable = row(root + '/ui-remove-me.txt');
                      if (!removable) throw new Error('Finder delete fixture row is missing');
                      const originalConfirm = window.confirm;
                      window.confirm = () => true;
                      removable.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32}));
                      await waitFor(() => Array.from(document.querySelectorAll('.file-context-menu button')).find(button => /^delete$/i.test(button.textContent.trim())));
                      const deleteButton = Array.from(document.querySelectorAll('.file-context-menu button')).find(button => /^delete$/i.test(button.textContent.trim()));
                      if (!deleteButton) throw new Error('Finder delete context-menu action is missing');
                      deleteButton.click();
                      await waitFor(() => !row(root + '/ui-remove-me.txt'));
                      window.confirm = originalConfirm;
                      const deletedInfo = await fetch('/api/fs/info?path=' + encodeURIComponent(root + '/ui-remove-me.txt'), {cache: 'no-store', credentials: 'same-origin'});
                      if (deletedInfo.status !== 404) throw new Error(`Finder delete did not remove the file: ${deletedInfo.status}`);
                      done({initial, expanded, reexpanded, deletedViaFinder: true});
                    })().catch(error => done({error: String(error?.stack || error)}));
                """, str(finder_root))
                if finder_ui.get('error') or finder_ui.get('initial', {}).get('panel') is not True or finder_ui.get('expanded', {}).get('needle') is not True or finder_ui.get('reexpanded', {}).get('needle') is not True or finder_ui.get('deletedViaFinder') is not True:
                    raise SystemExit(f"Go Phase 4/5 Finder UI Chromium E2E failed: {finder_ui}")
                git_views = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const json = async url => {
                      const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin'});
                      return {status: response.status, body: await response.json()};
                    };
                    (async () => {
                      const routes = await Promise.all([
                        json('/api/fs/diff?path=' + encodeURIComponent(arguments[0])),
                        json('/api/fs/git-history?path=' + encodeURIComponent(arguments[1])),
                        json('/api/batch/blame?path=' + encodeURIComponent(arguments[0])),
                        json('/api/events?session=phase3-later-route'),
                      ]);
                      if (!routes.every(result => result.status === 501 && result.body.error === 'feature_unavailable')) throw new Error('later Git routes were not unavailable: ' + JSON.stringify(routes));
                      done({unavailable: routes.length});
                    })().catch(error => done({error: String(error)}));
                """, str(git_note), str(git_root), git_head)
                if git_views.get('error'):
                    raise SystemExit(f"Go later-route Chromium E2E failed: {git_views}")
                (finder_root / "external.txt").write_text("external edit\n", encoding="utf-8")
                watch = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const root = arguments[0];
                    const token = arguments[1];
                    const check = async () => {
                      const response = await fetch('/api/fs/watch-diff?since=' + encodeURIComponent(token), {cache: 'no-store', credentials: 'same-origin'});
                      const body = await response.json();
                      if (response.status === 200 && body.directories?.some(item => item.path === root && item.data.entries?.some(entry => entry.name === 'external.txt'))) {
                        done({changed: true, mode: body.mode, paths: body.change_summary?.paths_changed});
                        return;
                      }
                      setTimeout(check, 100);
                    };
                    check().catch(error => done({error: String(error)}));
                    setTimeout(() => done({error: 'watch diff timeout'}), 5000);
                """, str(finder_root), finder['watchToken'])
                if watch.get('error') or watch.get('changed') is not True:
                    raise SystemExit(f"Go Phase 4 Finder watch Chromium E2E failed: {watch}")
                repair = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const sessions = arguments[0];
                    const stream = new EventSource('/api/client-events?channels=events&client_id=repair-e2e');
                    const seen = [];
                    let settled = false;
                    const finish = value => { if (settled) return; settled = true; stream.close(); done(value); };
                    stream.addEventListener('ready', () => {
                        Promise.all(sessions.map((session, index) => fetch('/api/event', {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({session, type: 'repair-' + index, message: 'phase3 repair event'})
                        }).then(response => response.json().then(body => ({status: response.status, body})))))
                          .then(results => seen.push({published: results}));
                    });
                    stream.addEventListener('event_log_changed', event => {
                        const payload = JSON.parse(event.data);
                        seen.push(payload);
                        if ((payload.repair_resources || []).length >= 2) finish({seen});
                    });
                    setTimeout(() => finish({error: 'repair timeout', seen}), 7000);
                """, sessions)
                if repair.get('error') or not any((item.get('repair_resources') or []) for item in repair.get('seen', []) if isinstance(item, dict)):
                    raise SystemExit(f"Go Phase 3 Chromium event repair failed: {repair}")
                operation = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const id = 'op-chrome-replay';
                    fetch('/api/operations/accept', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id, request_id:'chrome-request', route:'POST /api/operations/accept', kind:'e2e'})})
                      .then(response => response.json().then(body => ({status: response.status, body})))
                      .then(accepted => fetch('/api/operations/terminalize', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id, result:{state:'ready', data:{source:'chrome'}}, status:200})}).then(response => response.json().then(body => ({accepted, status: response.status, body}))))
                      .then(done);
                """)
                if operation['accepted']['status'] != 202 or operation['status'] != 200:
                    raise SystemExit(f"Go Phase 3 operation setup failed: {operation}")
                first_pid = proc.pid
                stop_server(force=True)
                proc = start_server()
                if proc.pid == first_pid:
                    raise SystemExit(f"Go Phase 3 restart reused server pid {first_pid}")
                assert_server_identity(driver, proc)
                replay = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const seen = [];
                    const stream = new EventSource('/api/client-events?operation_id=op-chrome-replay');
                    stream.addEventListener('ready', event => seen.push('ready'));
                    stream.addEventListener('operation_terminal', event => {
                        const payload = JSON.parse(event.data).payload;
                        seen.push({type:'terminal', payload});
                        stream.close();
                        fetch('/api/operations/ack', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({acks:[{id:'op-chrome-replay', cursor:payload.operation.cursor}]})}).then(response => response.json().then(body => done({seen, ack:body})));
                    });
                    setTimeout(() => { stream.close(); done({error:'replay timeout', seen}); }, 5000);
                """)
                if replay.get('error') or 'ready' not in replay['seen'] or not any(item.get('type') == 'terminal' for item in replay['seen'] if isinstance(item, dict)) or 'op-chrome-replay' not in replay['ack'].get('acknowledged', []):
                    raise SystemExit(f"Go Phase 3 restart replay/ack failed: {replay}")
                second_pid = proc.pid
                stop_server(force=True)
                proc = start_server()
                if proc.pid == second_pid:
                    raise SystemExit(f"Go Phase 3 second restart reused server pid {second_pid}")
                assert_server_identity(driver, proc)
                no_replay = driver.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    const stream = new EventSource('/api/client-events?operation_id=op-chrome-replay');
                    const seen = [];
                    stream.addEventListener('ready', () => seen.push('ready'));
                    stream.addEventListener('operation_terminal', () => { stream.close(); done({error:'acknowledged operation replayed', seen}); });
                    setTimeout(() => { stream.close(); done({seen}); }, 1500);
                """)
                if no_replay.get('error') or no_replay.get('seen') != ['ready']:
                    raise SystemExit(f"Go Phase 3 acknowledged replay failed: {no_replay}")
                stop_server(force=True)
                journal_path = config / "go-operations.jsonl"
                journal_lines = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                if not any(line.get("type") == "snapshot" and any(item.get("id") == "op-chrome-replay" and item.get("delivery_acknowledged") is True for item in line.get("operations", [])) for line in journal_lines):
                    raise SystemExit(f"Go Phase 3 journal ack state missing: {journal_lines}")
                print({'terminal': result, 'editor': editor, 'textedit_conflict': conflict, 'viewedit': markdown, 'viewedit_reopen': markdown_reopened, 'finder': finder, 'finder_ui': finder_ui, 'git_views': git_views, 'finder_watch': watch, 'event_repair': repair, 'operation_replay': replay, 'after_ack_restart': no_replay, 'pids': [first_pid, second_pid, proc.pid if proc else None], 'journal_records': len(journal_lines)})
            finally:
                if driver is not None:
                    driver.quit()
                    driver = None
        finally:
            if driver is not None:
                driver.quit()
            stop_server()
            for session_name in created_sessions:
                subprocess.run((*tmux, "kill-session", "-t", session_name), check=False)


if __name__ == "__main__":
    main(os.environ["Yolomux_GO_BINARY"] if "Yolomux_GO_BINARY" in os.environ else os.sys.argv[1])
