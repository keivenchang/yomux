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


def main(binary: str) -> None:
    with tempfile.TemporaryDirectory(prefix="yolomux-go-e2e-") as tmp:
        root = Path(tmp)
        config = root / "config"
        config.mkdir()
        (config / "auth.yaml").write_text("users:\n  - username: e2e\n    password: test-password\n    role: admin\n", encoding="utf-8")
        (config / "auth-cookie-secret").write_text("11" * 32, encoding="ascii")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        tmux_socket = str(root / "tmux.sock")
        tmux = ("tmux", "-S", tmux_socket)
        sessions = [f"yo-go-e2e-{os.getpid()}-{index}" for index in range(3)]
        session = sessions[0]
        source_root = Path("/nfs/keivenc/dev/yolomux/yo7221")
        if not (source_root / "static/yolomux.js").is_file(): source_root = Path(os.getcwd())
        private_source_root = root / "server-root"
        shutil.copytree(source_root / "static", private_source_root / "static")
        shutil.copytree(source_root / "tools", private_source_root / "tools")
        shutil.copytree(source_root / "yolomux_lib", private_source_root / "yolomux_lib")
        created_sessions = []
        for session_name in sessions:
            if subprocess.run((*tmux, "has-session", "-t", session_name), check=False, stderr=subprocess.DEVNULL).returncode != 0:
                subprocess.run((*tmux, "new-session", "-d", "-s", session_name), check=True)
                created_sessions.append(session_name)

        def start_server():
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
            process = subprocess.Popen([binary, "--host", "127.0.0.1", "--port", str(port), "--root", str(private_source_root), "--config", str(config)], env=environment, stdout=log_file, stderr=subprocess.STDOUT)
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
                    if (!item?.term || item.socket?.readyState !== WebSocket.OPEN) return {error: 'terminal did not connect', url: location.href, body: document.body.innerText.slice(0, 300), sessions: window.bootstrap?.sessions || null, terminal: Boolean(item?.term), socket: item?.socket?.readyState || null};
                    setBadConnectionCursorState(false);
                    const before = {cols: item.term.cols, rows: item.term.rows, cursorBlink: item.term.options.cursorBlink, body: document.body.className};
                    item.container.style.width = '500px';
                    item.container.style.height = '200px';
                    fitTerminal(arguments[0], {force: true, claim: true});
                    return {before, after: {cols: item.term.cols, rows: item.term.rows, cursorBlink: item.term.options.cursorBlink}};
                """, session)
                if result.get("error") or result["after"]["cols"] >= result["before"]["cols"] or result["after"]["rows"] >= result["before"]["rows"] or result["after"]["cursorBlink"] is not True:
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
                print({'terminal': result, 'event_repair': repair, 'operation_replay': replay, 'after_ack_restart': no_replay, 'pids': [first_pid, second_pid, proc.pid if proc else None], 'journal_records': len(journal_lines)})
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
