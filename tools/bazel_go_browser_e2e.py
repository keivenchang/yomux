#!/usr/bin/env python3
"""Real Chromium smoke for the Go terminal page and PTY resize contract."""

import http.cookiejar
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


def main(binary: str) -> None:
    with tempfile.TemporaryDirectory(prefix="yolomux-go-e2e-") as tmp:
        root = Path(tmp)
        config = root / "config"
        config.mkdir()
        (config / "auth.yaml").write_text("users:\n  - username: e2e\n    password: test-password\n    role: admin\n", encoding="utf-8")
        (config / "auth-cookie-secret").write_text("11" * 32, encoding="ascii")
        port = 18721
        session = "yo-go-e2e"
        source_root = Path("/nfs/keivenc/dev/yolomux/yo7221")
        if not (source_root / "static/yolomux.js").is_file(): source_root = Path(os.getcwd())
        proc = subprocess.Popen([binary, "--host", "127.0.0.1", "--port", str(port), "--root", str(source_root), "--config", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        created_session = subprocess.run(("tmux", "has-session", "-t", session), check=False).returncode != 0
        if created_session:
            subprocess.run(("tmux", "new-session", "-d", "-s", session), check=True)
        try:
            for _ in range(80):
                try:
                    urllib.request.urlopen(f"https://127.0.0.1:{port}/login", context=__import__("ssl")._create_unverified_context(), timeout=1)
                    break
                except Exception:
                    time.sleep(0.1)
            options = Options()
            options.binary_location = "/usr/bin/google-chrome"
            for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--ignore-certificate-errors", "--window-size=1200,800"):
                options.add_argument(arg)
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
                while time.time() < deadline and not driver.find_elements("css selector", "#term-yo7220 .xterm"):
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
                print(result)
            finally:
                driver.quit()
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            if created_session:
                subprocess.run(("tmux", "kill-session", "-t", session), check=False)


if __name__ == "__main__":
    main(os.environ["Yolomux_GO_BINARY"] if "Yolomux_GO_BINARY" in os.environ else os.sys.argv[1])
