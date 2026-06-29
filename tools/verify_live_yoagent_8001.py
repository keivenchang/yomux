#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Verify live 8001 YO!agent auxiliary streaming in the real browser UI."""

from __future__ import annotations

import argparse
import json
import shutil
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from yolomux_lib.common import auth_cookie_value
from yolomux_lib.common import current_auth_users
from yolomux_lib.settings import SETTINGS_PATH
from yolomux_lib.settings import save_settings
from yolomux_lib.yoagent.conversation import YOAGENT_CLI_STATE_PATH
from yolomux_lib.yoagent.conversation import YOAGENT_CONVERSATION_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://localhost:8001")
    parser.add_argument("--backends", default="claude,codex", help="comma-separated backend list")
    parser.add_argument("--screenshot-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--sleep-seconds", type=int, default=8)
    parser.add_argument("--window-size", default="1280,900")
    return parser.parse_args()


def admin_auth_cookie(base_url: str) -> dict[str, object]:
    parsed = urlsplit(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    users = [user for user in current_auth_users() if user.role == "admin"] or list(current_auth_users())
    if not users:
        raise RuntimeError("no YOLOmux auth users configured")
    user = users[0]
    return {
        "name": f"yolomux_auth_{port}",
        "value": auth_cookie_value(user.username, user.password),
        "path": "/",
        "secure": parsed.scheme == "https",
        "httpOnly": True,
        "sameSite": "Lax",
    }


def snapshot_paths() -> tuple[Path, dict[Path, Path], set[Path]]:
    paths = [SETTINGS_PATH, YOAGENT_CONVERSATION_PATH, YOAGENT_CLI_STATE_PATH]
    backup_dir = Path(tempfile.mkdtemp(prefix="yolomux-live-yoagent-backup-"))
    backups: dict[Path, Path] = {}
    missing: set[Path] = set()
    for path in map(Path, paths):
        target = backup_dir / path.name
        if path.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            backups[path] = target
        else:
            missing.add(path)
    return backup_dir, backups, missing


def restore_paths(backups: dict[Path, Path], missing: set[Path]) -> None:
    for path in [SETTINGS_PATH, YOAGENT_CONVERSATION_PATH, YOAGENT_CLI_STATE_PATH]:
        path = Path(path)
        if path in backups:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backups[path], path)
        elif path in missing and path.exists():
            path.unlink()


def make_driver(window_size: str) -> webdriver.Chrome:
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not chrome:
        raise RuntimeError("Chrome/Chromium is not installed")
    options = Options()
    options.binary_location = chrome
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument(f"--window-size={window_size}")
    return webdriver.Chrome(options=options)


def http_ping(base_url: str) -> str:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/ping", context=ssl._create_unverified_context(), timeout=5) as response:
            return str(response.status)
    except urllib.error.HTTPError as exc:
        return str(exc.code)


def wait_for_app(driver: webdriver.Chrome) -> None:
    WebDriverWait(driver, 20).until(
        lambda browser: browser.execute_script(
            """
            return typeof openInfoSubTab === 'function'
              && typeof sendYoagentChatMessage === 'function'
              && typeof renderYoagentPanel === 'function'
              && document.getElementById('grid');
            """
        )
    )


def reset_chat(driver: webdriver.Chrome) -> None:
    result = driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        fetch('/api/yoagent/reset', {method: 'POST'})
          .then(response => response.json().then(payload => done({status: response.status, payload})))
          .catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    if result.get("error") or int(result.get("status") or 0) >= 400:
        raise AssertionError(f"YO!agent reset failed: {result}")


def install_chat_response_delay(driver: webdriver.Chrome, delay_ms: int = 2000) -> None:
    driver.execute_script(
        """
        const delayMs = arguments[0];
        if (!window.__liveYoagentVerifyNativeFetch) {
          window.__liveYoagentVerifyNativeFetch = window.fetch.bind(window);
        }
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), window.location.href);
          const response = await window.__liveYoagentVerifyNativeFetch(input, options);
          if (url.pathname !== '/api/yoagent/chat') return response;
          const body = await response.text();
          await new Promise(resolve => setTimeout(resolve, delayMs));
          const headers = new Headers(response.headers);
          return new Response(body, {status: response.status, statusText: response.statusText, headers});
        };
        """,
        delay_ms,
    )


def start_chat(driver: webdriver.Chrome, message: str, marker: str) -> None:
    result = driver.execute_script(
        """
        const message = arguments[0];
        const marker = arguments[1];
        window.__liveYoagentVerify = {done: false, error: null, marker};
        openInfoSubTab('yoagent');
        renderYoagentPanel({scrollBottom: true});
        sendYoagentChatMessage(message)
          .then(() => { window.__liveYoagentVerify.done = true; })
          .catch(error => {
            window.__liveYoagentVerify.done = true;
            window.__liveYoagentVerify.error = String(error && error.stack || error);
          });
        return true;
        """,
        message,
        marker,
    )
    if result is not True:
        raise AssertionError("failed to start YO!agent chat")


def collect_dom(driver: webdriver.Chrome) -> dict[str, object]:
    return driver.execute_script(
        """
        const marker = window.__liveYoagentVerify?.marker || '';
        const messages = Array.from(document.querySelectorAll('.yoagent-message.assistant'));
        const records = messages.map(node => ({
          node,
          streaming: node.classList.contains('streaming'),
          body: node.querySelector('.yoagent-message-body')?.textContent || '',
        }));
        const selected = records.find(record => marker && record.body.includes(marker))
          || records.find(record => record.streaming)
          || records[records.length - 1]
          || null;
        const assistant = selected?.node || null;
        const details = assistant?.querySelector?.('.yoagent-message-details.has-auxiliary') || null;
        const preview = details?.querySelector?.('.yoagent-details-preview') || null;
        const stream = details?.querySelector?.('.yoagent-auxiliary-stream') || null;
        const body = assistant?.querySelector?.('.yoagent-message-body') || null;
        const status = document.querySelector('.yoagent-chat-status');
        const style = node => node ? getComputedStyle(node).color : '';
        return {
          done: Boolean(window.__liveYoagentVerify?.done),
          error: window.__liveYoagentVerify?.error || '',
          streaming: Boolean(selected?.streaming),
          statusText: status?.textContent || '',
          detailsFound: Boolean(details),
          detailsOpen: Boolean(details?.open),
          detailsText: details?.textContent || '',
          previewText: preview?.textContent || '',
          streamText: stream?.textContent || '',
          bodyText: body?.textContent || '',
          auxiliaryColor: style(stream || preview),
          bodyColor: style(body),
          bootErrors: window.__bootErrors || [],
          bootRejections: window.__bootRejections || [],
        };
        """
    )


def has_tool_line(text: object) -> bool:
    return any(line.startswith(("tool start:", "tool output:", "tool done:")) for line in str(text or "").splitlines())


def has_auxiliary_line(text: object) -> bool:
    return any(line.startswith(("thinking:", "thinking done", "tool start:", "tool output:", "tool done:", "usage:", "error:")) for line in str(text or "").splitlines())


def wait_for_running_auxiliary(driver: webdriver.Chrome, timeout: float = 90.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        last = collect_dom(driver)
        preview = str(last.get("previewText") or "")
        stream = str(last.get("streamText") or "")
        if last.get("detailsFound") and (has_auxiliary_line(preview) or has_auxiliary_line(stream)) and (last.get("streaming") or last.get("done")):
            return last
        time.sleep(0.2)
    raise AssertionError(f"did not observe running auxiliary preview: {last}")


def wait_for_done(driver: webdriver.Chrome, timeout: float = 120.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        last = collect_dom(driver)
        if last.get("done"):
            if last.get("error"):
                raise AssertionError(last["error"])
            return last
        time.sleep(0.25)
    raise AssertionError(f"YO!agent chat did not finish: {last}")


def verify_backend(driver: webdriver.Chrome, base_url: str, backend: str, screenshot_dir: Path, sleep_seconds: int) -> dict[str, object]:
    marker = f"YOAGENT_{backend.upper()}_LIVE_UI_OK"
    save_settings({"yoagent": {"backend": backend, "invocation": "cli"}})
    reset_chat(driver)
    driver.get(base_url.rstrip("/") + "/")
    wait_for_app(driver)
    driver.execute_script("openInfoSubTab('yoagent'); renderYoagentPanel({scrollBottom: true});")
    message = f"Please use a shell tool for `sleep {sleep_seconds}; pwd`, then include {marker} in one short final sentence."
    install_chat_response_delay(driver)
    start_chat(driver, message, marker)
    running = wait_for_running_auxiliary(driver)
    driver.execute_script(
        """
        const details = document.querySelector('.yoagent-message.streaming .yoagent-message-details.has-auxiliary');
        if (details) details.open = true;
        """
    )
    expanded_running = collect_dom(driver)
    done = wait_for_done(driver)
    driver.execute_script(
        """
        const details = Array.from(document.querySelectorAll('.yoagent-message.assistant .yoagent-message-details.has-auxiliary')).pop();
        if (details) details.open = false;
        """
    )
    collapsed_done = collect_dom(driver)
    screenshot = screenshot_dir / f"yolomux-8001-yoagent-live-{backend}.png"
    driver.save_screenshot(str(screenshot))

    preview_lines = [line for line in str(collapsed_done.get("previewText") or "").splitlines() if line.strip()]
    stream_text = str(done.get("streamText") or collapsed_done.get("streamText") or "")
    body_text = str(done.get("bodyText") or collapsed_done.get("bodyText") or "")
    details_text = str(collapsed_done.get("detailsText") or "")
    assert str(running.get("previewText") or "").count("\n") <= 1, running
    assert has_auxiliary_line(expanded_running.get("streamText") or running.get("streamText") or running.get("previewText")), {"expanded": expanded_running, "running": running}
    assert marker in body_text, collapsed_done
    assert has_auxiliary_line(stream_text), collapsed_done
    assert len(preview_lines) == 1, collapsed_done
    assert str(collapsed_done.get("auxiliaryColor") or "") != str(collapsed_done.get("bodyColor") or ""), collapsed_done
    assert "tool start:" not in body_text, collapsed_done
    assert collapsed_done.get("bootErrors") == [], collapsed_done
    assert collapsed_done.get("bootRejections") == [], collapsed_done
    return {
        "backend": backend,
        "marker": marker,
        "running_preview": running.get("previewText"),
        "expanded_running_stream": expanded_running.get("streamText"),
        "done_preview": collapsed_done.get("previewText"),
        "details_text": details_text,
        "final_stream": stream_text,
        "auxiliary_color": collapsed_done.get("auxiliaryColor"),
        "body_color": collapsed_done.get("bodyColor"),
        "screenshot": str(screenshot),
    }


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    args.screenshot_dir.mkdir(parents=True, exist_ok=True)
    ping_status = http_ping(base_url)
    if ping_status != "401":
        raise AssertionError(f"expected unauthenticated /api/ping to return 401, got {ping_status}")
    _backup_dir, backups, missing = snapshot_paths()
    driver = make_driver(args.window_size)
    results: list[dict[str, object]] = []
    try:
        driver.get(base_url + "/")
        driver.add_cookie(admin_auth_cookie(base_url))
        driver.get(base_url + "/")
        wait_for_app(driver)
        for backend in [item.strip() for item in args.backends.split(",") if item.strip()]:
            if backend not in {"claude", "codex"}:
                raise AssertionError(f"unsupported backend: {backend}")
            results.append(verify_backend(driver, base_url, backend, args.screenshot_dir, max(1, args.sleep_seconds)))
    finally:
        driver.quit()
        restore_paths(backups, missing)
    print(json.dumps({"ok": True, "ping": ping_status, "results": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
