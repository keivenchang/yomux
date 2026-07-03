#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Headless screenshot of a RUNNING YOLOmux instance — agent self-verification of UI changes.

Boots headless Chrome against a dev server (default https://localhost:8001), logs in via the login
form, optionally sets the locale cookie, loads a path/URL (e.g. a layout query string), waits for the
UI to settle, and writes a PNG. This closes the diagnose -> fix -> re-screenshot loop: the agent can
verify its OWN UI change instead of waiting on a human screenshot.

Examples:
  tools/snapshot.py --out /tmp/app.png
  tools/snapshot.py --path '/login' --no-auth --locale zh-Hant --out /tmp/login-zh.png
  tools/snapshot.py --path '/?<layout-query>' --wait-for '.file-editor-panel' --out /tmp/editor.png
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
import time
from urllib.parse import urljoin

SELENIUM_AVAILABLE = importlib.util.find_spec("selenium") is not None
if SELENIUM_AVAILABLE:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait


def find_chrome() -> str | None:
    return shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="https://localhost:8001", help="server base URL (default the dev instance)")
    parser.add_argument("--path", default="/", help="path (+query) to screenshot, joined onto --base")
    parser.add_argument("--url", default="", help="full URL to screenshot (overrides --base/--path)")
    parser.add_argument("--out", default="/tmp/yolomux-snapshot.png", help="output PNG path")
    parser.add_argument("--user", default="guest", help="login username (default guest)")
    parser.add_argument("--password", default="guest", help="login password (default guest)")
    parser.add_argument("--no-auth", action="store_true", help="skip the login step (for pre-auth pages like /login)")
    parser.add_argument("--locale", default="", help="set the yolomux_locale cookie (affects pre-auth screens)")
    parser.add_argument("--width", type=int, default=1400, help="viewport width")
    parser.add_argument("--height", type=int, default=900, help="viewport height")
    parser.add_argument("--wait-for", default="", help="CSS selector to wait for before the shot")
    parser.add_argument("--settle", type=int, default=900, help="extra settle time in ms after load (SPA render)")
    parser.add_argument("--timeout", type=int, default=15, help="seconds to wait for login / wait-for")
    args = parser.parse_args(argv)

    chrome = find_chrome()
    if not chrome:
        print("error: Chrome/Chromium not found (install google-chrome or chromium)", file=sys.stderr)
        return 2
    if not SELENIUM_AVAILABLE:
        print("error: selenium is not installed (pip install selenium)", file=sys.stderr)
        return 2

    options = Options()
    options.binary_location = chrome
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--window-size={args.width},{args.height}")
    options.add_argument("--ignore-certificate-errors")  # the dev server uses --self-signed
    options.set_capability("acceptInsecureCerts", True)

    target = args.url or urljoin(args.base + "/", args.path.lstrip("/"))
    driver = webdriver.Chrome(options=options)
    try:
        wait = WebDriverWait(driver, args.timeout)
        # Land on the base first so we can set a cookie on the right domain.
        driver.get(args.base + "/login")
        if args.locale:
            try:
                driver.add_cookie({"name": "yolomux_locale", "value": args.locale, "path": "/"})
            except Exception as exc:  # noqa: BLE001 - best-effort; cookie set can fail pre-navigation
                print(f"warning: could not set locale cookie: {exc}", file=sys.stderr)
        if not args.no_auth:
            driver.get(args.base + "/login")
            try:
                user_field = wait.until(EC.presence_of_element_located((By.NAME, "username")))
                user_field.clear()
                user_field.send_keys(args.user)
                pw = driver.find_element(By.NAME, "password")
                pw.clear()
                pw.send_keys(args.password)
                driver.find_element(By.CSS_SELECTOR, "form.login-form").submit()
                wait.until(lambda d: "/login" not in d.current_url)
            except Exception as exc:  # noqa: BLE001 - surface a clear message instead of a stack trace
                print(f"error: login failed ({exc}); is the server at {args.base} running?", file=sys.stderr)
                return 1
        driver.get(target)
        if args.wait_for:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, args.wait_for)))
        if args.settle > 0:
            time.sleep(args.settle / 1000.0)
        if not driver.save_screenshot(args.out):
            print(f"error: failed to write {args.out}", file=sys.stderr)
            return 1
        print(f"wrote {args.out}  ({target})")
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
