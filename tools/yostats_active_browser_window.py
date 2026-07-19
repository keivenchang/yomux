#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Drive the canonical authenticated YO!stats browser workload and CPU capture.

This is an operator-only measurement tool. It creates a local authenticated
browser session from the configured account's existing server-side cookie
material, never reads or emits a plaintext password, and writes the resulting
browser and CPU evidence only beneath /tmp unless the caller deliberately
chooses another transient output path.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from yolomux_lib.auth import AUTH_CONFIG_PATH
from yolomux_lib.auth import AUTH_COOKIE_NAME
from yolomux_lib.auth import AuthUser
from yolomux_lib.auth import auth_cookie_value
from yolomux_lib.auth import read_auth_users
from yolomux_lib.common import STATE_DIR
from yolomux_lib.background_owner import read_background_owner_debug_status
from yolomux_lib.control import send_yolomux_control_request
from yolomux_lib.local_services.registry import bounded_process_table
from yolomux_lib.local_services.registry import tracked_local_service_groups
from yolomux_lib.local_services.watchdog import GroupOverloadWatchdog


def positive_seconds(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def find_chrome() -> str:
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), "")


def listener_pid(port: int) -> int:
    result = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True, check=False, timeout=2.0)
    values = [line.strip() for line in result.stdout.splitlines() if line.strip().isdigit()]
    if len(values) != 1:
        raise RuntimeError(f"expected one listener on {port}, found {values or 'none'}")
    return int(values[0])


def service_pid_for_socket(socket_path: str) -> int:
    """Resolve a daemon only when its command line names the exact service socket."""
    try:
        result = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=False, timeout=2.0)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    marker = f"--socket {socket_path}"
    for line in result.stdout.splitlines():
        pid_text, _separator, command = line.strip().partition(" ")
        if pid_text.isdigit() and marker in command:
            return int(pid_text)
    return 0


def runtime_service_pids() -> dict[str, int]:
    """Read bounded local-service records without constructing a second app instance."""
    pids: dict[str, int] = {}
    for record_path in (STATE_DIR / "services").glob("*.service.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        service = str(record.get("service") or "")
        if not service:
            continue
        pid = int(record.get("pid") or 0)
        if pid and process_is_alive(pid):
            pids[service] = pid
            continue
        socket_path = str(record.get("socket") or "")
        if socket_path:
            resolved_pid = service_pid_for_socket(socket_path)
            if resolved_pid:
                pids[service] = resolved_pid
    return pids


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def bounded_api_resources(entries: list[dict[str, object]], limit: int = 500) -> list[dict[str, object]]:
    """Keep capture evidence bounded and exclude query-string values."""
    resources: list[dict[str, object]] = []
    for entry in entries:
        name = entry.get("name")
        if not isinstance(name, str) or "/api/" not in name:
            continue
        parsed = urlsplit(name)
        resources.append(
            {
                "path": parsed.path,
                "duration": entry.get("duration"),
                "transferSize": entry.get("transferSize"),
            }
        )
        if len(resources) == limit:
            break
    return resources


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8881)
    parser.add_argument("--username", help="configured YOLOmux account name; defaults to the first admin account")
    parser.add_argument("--duration", type=positive_seconds, default=60)
    parser.add_argument("--output", type=Path, required=True, help="browser evidence JSON path, normally under /tmp")
    return parser.parse_args(argv)


def wait_for_app(driver: webdriver.Chrome, tmux_sessions: list[str], timeout: int) -> None:
    wait = WebDriverWait(driver, timeout)
    wait.until(lambda current: current.execute_script("return typeof setDebugGraphRange === 'function' && typeof selectSession === 'function' && document.getElementById('grid') !== null"))
    observed = driver.execute_script("return Array.isArray(sessions) ? sessions.filter(isTmuxSession) : []")
    if observed[:2] != tmux_sessions:
        raise RuntimeError(f"canonical workload lost its tmux sessions: expected {tmux_sessions}, found {observed}")


def capture_auth_user(username: str | None) -> AuthUser:
    """Choose a configured local account without handling plaintext credentials."""
    users = read_auth_users(AUTH_CONFIG_PATH)
    if not users:
        raise RuntimeError("no configured YOLOmux account is available for local capture")
    if username:
        for user in users:
            if user.username == username:
                return user
        raise RuntimeError("requested YOLOmux capture account is not configured")
    return next((user for user in users if user.role == "admin"), users[0])


def install_local_auth_cookie(driver: webdriver.Chrome, base_url: str, port: int, user: AuthUser) -> None:
    """Install the server-validated session cookie only in Selenium's temporary profile."""
    driver.get(f"{base_url}/login")
    driver.add_cookie(
        {
            "name": f"{AUTH_COOKIE_NAME}_{port}",
            "value": auth_cookie_value(user.username, user.password),
            "path": "/",
            "secure": base_url.startswith("https://"),
            "httpOnly": True,
        }
    )


def capture_measurement_metrics() -> dict[str, object]:
    """Read only the generic capture-scoped metrics from the existing owner."""
    owner_debug = read_background_owner_debug_status()
    owner = owner_debug.get("current_owner") if isinstance(owner_debug, dict) else None
    response = send_yolomux_control_request(owner, {"action": "runtime_measurement_metrics", "scope": "capture"})
    performance = response.get("performance") if response.get("ok") else None
    if not isinstance(performance, dict):
        raise RuntimeError(f"capture measurement metrics unavailable: {response.get('error') or 'invalid response'}")
    return performance


def install_measurement_fetch_header(driver: webdriver.Chrome, marker: str) -> None:
    """Tag in-page fetches after navigation without retaining the marker in app state."""
    driver.execute_script(
        """
        const marker = arguments[0];
        const originalFetch = window.fetch.bind(window);
        window.fetch = (resource, init) => {
          const request = resource instanceof Request ? resource : new Request(resource, init);
          const headers = new Headers(request.headers);
          headers.set('X-YOLOmux-Measurement', marker);
          return originalFetch(new Request(request, {headers}));
        };
        """,
        marker,
    )


def authenticate_and_open(driver: webdriver.Chrome, base_url: str, port: int, username: str | None, timeout: int) -> list[str]:
    wait = WebDriverWait(driver, timeout)
    install_local_auth_cookie(driver, base_url, port, capture_auth_user(username))
    driver.get(f"{base_url}/")
    wait.until(lambda current: "/login" not in current.current_url)
    wait.until(lambda current: current.execute_script("return Array.isArray(sessions) && sessions.filter(isTmuxSession).length >= 2"))
    tmux_sessions = driver.execute_script("return sessions.filter(isTmuxSession).slice(0, 2)")
    query = urlencode({"sessions": ",".join(tmux_sessions), "layout": "row@34(left,row@50(center,right))", "tabs": f"left:debug*;center:finder;right:tabber,{tmux_sessions[0]}"})
    driver.get(f"{base_url}/?{query}")
    wait_for_app(driver, tmux_sessions, timeout)
    return tmux_sessions


def wait_for_exact_history(driver: webdriver.Chrome, range_seconds: int, resolution_seconds: int, timeout: int = 20) -> None:
    WebDriverWait(driver, timeout).until(
        lambda current: current.execute_script(
            "const state = jsDebugHistoryReadinessSnapshot(); return state.phase === 'ready' && state.requestedRangeSeconds === arguments[0] && debugGraphExactRequestResolutionSeconds() === arguments[1]",
            range_seconds,
            resolution_seconds,
        )
    )


def perform_workload(driver: webdriver.Chrome, tmux_sessions: list[str]) -> dict[str, object]:
    result = driver.execute_async_script(
        r"""
        const done = arguments[arguments.length - 1];
        (async () => {
          setDebugGraphRange(300);
          setDebugGraphResolutionOverride(1);
          const finder = document.querySelector('[data-file-explorer-session-surface="finder"] [data-session-files-session]');
          if (!finder) throw new Error('Finder session selector was not rendered');
          finder.value = arguments[0];
          finder.dispatchEvent(new Event('change', {bubbles: true}));
          if (finder.value !== arguments[0]) throw new Error('Finder did not select the requested tmux session');
          document.querySelector('[data-file-explorer-refresh]')?.click();
          const finderPanel = finder.closest('.file-explorer-panel');
          if (!finderPanel) throw new Error('Finder panel was not rendered');
          await refreshFileExplorerPanelTree(finderPanel, {force: true});
          const editableFile = () => [...finderPanel.querySelectorAll('.file-tree-row[data-path][data-kind="file"]')]
            .find(row => /\.(js|json|py|ts|tsx|css|yaml|yml|txt)$/i.test(row.dataset.name || row.dataset.path || ''));
          let file = editableFile();
          for (const directory of [...finderPanel.querySelectorAll('.file-tree-row[data-path][data-kind="dir"]')].slice(0, 6)) {
            if (file) break;
            await ensureDirectoryRowExpanded(directory, directory.dataset.path, {user: true});
            file = editableFile();
          }
          if (!file?.dataset.path) throw new Error('Finder has no editable file row');
          await openFileInEditor(file.dataset.path, file.dataset.name || file.dataset.path.split('/').pop());
          const panelSelector = '[data-file-path="' + CSS.escape(file.dataset.path) + '"]';
          let panel = null;
          for (let frame = 0; frame < 300; frame += 1) {
            panel = document.querySelector(panelSelector);
            if (panel?._cmView?.state && panel._cmView.dispatch) break;
            await new Promise(requestAnimationFrame);
          }
          const view = panel?._cmView;
          if (!view?.state || !view?.dispatch) throw new Error('opened file has no CodeMirror view');
          view.dispatch({changes: {from: view.state.doc.length, insert: ' '}});
          await selectSession(arguments[1], {userInitiated: true});
          setDebugGraphRange(1800);
          setDebugGraphResolutionOverride(10);
          setDebugGraphRange(300);
          setDebugGraphResolutionOverride(1);
          await selectSession(yocostItemId, {userInitiated: true});
          done({sessions: [arguments[0], arguments[1]], file: file.dataset.path});
        })().catch(error => done({error: String(error?.stack || error)}));
        """,
        tmux_sessions[0],
        tmux_sessions[1],
    )
    if result.get("error"):
        raise RuntimeError(str(result["error"]))
    wait_for_exact_history(driver, 300, 1)
    driver.execute_script("setDebugGraphRange(1800); setDebugGraphResolutionOverride(10)")
    wait_for_exact_history(driver, 1800, 10)
    driver.execute_script("setDebugGraphRange(300); setDebugGraphResolutionOverride(1)")
    wait_for_exact_history(driver, 300, 1)
    return result


def settle_browser_frames(driver: webdriver.Chrome, frames: int = 2) -> None:
    driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        let remaining = arguments[0];
        const next = () => {
          remaining -= 1;
          if (remaining <= 0) done();
          else requestAnimationFrame(next);
        };
        requestAnimationFrame(next);
        """,
        frames,
    )


def cdp_drag(driver: webdriver.Chrome, start: dict[str, int], end: dict[str, int], steps: int = 24) -> None:
    """Use the same frame-settled pointer path as the Dockview browser tests."""
    driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": start["x"], "y": start["y"], "button": "left", "buttons": 0, "clickCount": 1})
    driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": start["x"], "y": start["y"], "button": "none"})
    driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": start["x"], "y": start["y"], "button": "left", "buttons": 1, "clickCount": 1})
    settle_browser_frames(driver)
    for index in range(1, steps + 1):
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": round(start["x"] + (end["x"] - start["x"]) * index / steps), "y": round(start["y"] + (end["y"] - start["y"]) * index / steps), "button": "left", "buttons": 1})
        if index % 4 == 0:
            settle_browser_frames(driver, 1)
    settle_browser_frames(driver, 4)
    driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": end["x"], "y": end["y"], "button": "left", "buttons": 1})
    settle_browser_frames(driver)
    driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": end["x"], "y": end["y"], "button": "left", "buttons": 0, "clickCount": 1})


def drag_yocost_pane(driver: webdriver.Chrome) -> dict[str, object]:
    """Drag YO!cost and retain only the interaction's own paint evidence."""
    drag = WebDriverWait(driver, 10).until(lambda current: current.execute_script(
        """
        const signature = layoutSlotsSignature(layoutSlots);
        const sourceTab = [...document.querySelectorAll('.dockview-pane-tab')]
          .find(node => node.dataset.paneTab === yocostItemId && node.closest('.dv-tab')?.classList.contains('dv-active-tab'));
        const sourceGroup = sourceTab?.closest('.dv-groupview');
        const source = sourceGroup?.querySelector('.pane-drag-handle');
        const sourceSlotName = sourceGroup ? dockviewSlotForGroupElement(sourceGroup) : '';
        const target = [...document.querySelectorAll('.dv-groupview')]
          .filter(group => group !== sourceGroup && group.getBoundingClientRect().width > 0 && group.getBoundingClientRect().height > 0)
          .find(group => paneSwapAllowed(sourceSlotName, dockviewSlotForGroupElement(group)));
        const point = node => { const rect = node?.getBoundingClientRect(); return rect && rect.width > 0 && rect.height > 0 ? {x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2)} : null; };
        const result = {
          signature,
          source: point(source),
          target: point(target),
          sourceSlot: source?.dataset.paneDrag || '',
          targetSlot: target ? dockviewSlotForGroupElement(target) : '',
          canSwap: Boolean(sourceGroup && target && paneSwapAllowed(dockviewSlotForGroupElement(sourceGroup), dockviewSlotForGroupElement(target))),
        };
        return result.source && result.target ? result : null;
        """
    ))
    # The canonical workload deliberately performs a Finder edit/reload before
    # this drag. Reset client counters at the interaction boundary so a slow
    # filesystem render cannot be misreported as a pane-drag regression.
    driver.execute_script("clearClientPerfCounters(); performance.clearResourceTimings()")
    cdp_drag(driver, drag["source"], drag["target"])
    try:
        changed = WebDriverWait(driver, 10).until(lambda current: current.execute_script("return layoutSlotsSignature(layoutSlots) !== arguments[0]", drag["signature"]))
    except TimeoutException as error:
        final_signature = driver.execute_script("return layoutSlotsSignature(layoutSlots)")
        raise RuntimeError(f"YO!cost pane drag did not change layout: source={drag['sourceSlot']} target={drag['targetSlot']} allowed={drag['canSwap']} changed={final_signature != drag['signature']}") from error
    if not changed or driver.execute_script('return Boolean(document.querySelector(\'.drag-image, [data-pane-dragging="true"]\'))'):
        raise RuntimeError("YO!cost pane drag did not settle cleanly")
    return driver.execute_script("return {longTasks: clientPerfLongTaskSummary(), perf: clientPerfSummary()}")


def ledger_snapshot() -> dict[str, int]:
    """Identity-verified service PIDs before/after: the capture must not change them."""
    table = bounded_process_table()
    return {group["service"]: group["pid"] for group in tracked_local_service_groups(STATE_DIR / "services", table)}


def descendants_of(root_pid: int) -> list[int]:
    """Bounded descendant walk for the chromedriver/Chrome tree fallback kill."""
    table = bounded_process_table()
    children: dict[int, list[int]] = {}
    for pid, entry in table.items():
        children.setdefault(entry.ppid, []).append(pid)
    found: list[int] = []
    frontier = [root_pid]
    while frontier:
        pid = frontier.pop()
        for child in children.get(pid, []):
            found.append(child)
            frontier.append(child)
    return found


def stop_benchmark_group(process: subprocess.Popen | None) -> None:
    """Terminate the benchmark's own process group, then reap it."""
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def bounded_driver_quit(driver: webdriver.Chrome, quit_timeout: float = 15.0) -> None:
    """Quit the browser without becoming the next unbounded orphan owner.

    driver.quit() itself can hang against a wedged chromedriver; run it on a
    helper thread with a deadline and fall back to killing the chromedriver
    PID plus its live descendants (the Chrome renderer/GPU subtree).
    """
    service_process = getattr(getattr(driver, "service", None), "process", None)
    chromedriver_pid = int(service_process.pid) if service_process is not None else 0
    quitter = threading.Thread(target=lambda: driver.quit(), daemon=True)
    quitter.start()
    quitter.join(timeout=quit_timeout)
    if not quitter.is_alive():
        return
    for pid in ([chromedriver_pid] if chromedriver_pid else []) + (descendants_of(chromedriver_pid) if chromedriver_pid else []):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            continue


def main() -> int:
    args = parse_args()
    chrome = find_chrome()
    if not chrome:
        print("error: Chrome/Chromium is not installed", file=sys.stderr)
        return 2
    if not str(args.output).startswith("/tmp/"):
        print("error: output must be under /tmp", file=sys.stderr)
        return 2
    base_url = f"https://localhost:{args.port}"
    web_pid = listener_pid(args.port)
    service_pids = runtime_service_pids()
    statsd_pid = service_pids.get("statsd", 0)
    indexd_pid = service_pids.get("indexd", 0)
    ledger_before = ledger_snapshot()
    options = webdriver.ChromeOptions()
    options.binary_location = chrome
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--window-size=1600,1000")
    options.set_capability("acceptInsecureCerts", True)
    driver = webdriver.Chrome(options=options)
    measurement_marker = f"capture-{uuid.uuid4().hex}"
    driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": {"X-YOLOmux-Measurement": measurement_marker}})
    # Client-side Selenium timeouts: a wedged server or chromedriver must
    # surface as an exception that reaches cleanup, never an infinite block.
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(60)
    process: subprocess.Popen[str] | None = None
    cleaned = threading.Event()

    def cleanup() -> None:
        if cleaned.is_set():
            return
        cleaned.set()
        stop_benchmark_group(process)
        bounded_driver_quit(driver)

    def on_signal(signum: int, _frame: object) -> None:
        cleanup()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    # Overall wall-clock deadline: SIGALRM interrupts even a blocked Selenium
    # socket read, so a hang becomes a loud failure that still runs cleanup.
    signal.signal(signal.SIGALRM, on_signal)
    signal.alarm(int(args.duration) + 180)
    # Arm the tracked-group overload watchdog for the capture window: a
    # capture-induced runaway is contained instead of surviving the tool.
    watchdog = GroupOverloadWatchdog(port=args.port, state_dir=STATE_DIR, service_dir=STATE_DIR / "services")
    watchdog_thread = threading.Thread(target=watchdog.run, args=(float(args.duration) + 120.0,), daemon=True)
    watchdog_thread.start()
    try:
        tmux_sessions = authenticate_and_open(driver, base_url, args.port, args.username, timeout=20)
        install_measurement_fetch_header(driver, measurement_marker)
        driver.execute_script("clearClientPerfCounters(); performance.clearResourceTimings()")
        measurement_before = capture_measurement_metrics()
        benchmark_output = args.output.with_name(f"{args.output.stem}-contention.json")
        command = [sys.executable, str(REPO_ROOT / "tools" / "yostats_contention_benchmark.py"), "--web-pid", str(web_pid), "--duration", str(args.duration), "--output", str(benchmark_output)]
        if indexd_pid:
            command.extend(["--indexer-pid", str(indexd_pid)])
        if statsd_pid:
            command.extend(["--statsd-pid", str(statsd_pid)])
        # Own process group so a driver exception or signal can stop the whole
        # benchmark subtree without touching the dev stack's services.
        process = subprocess.Popen(command, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        workload = perform_workload(driver, tmux_sessions)
        workload["drag"] = drag_yocost_pane(driver)
        stdout, stderr = process.communicate(timeout=args.duration + 20)
        if process.returncode:
            raise RuntimeError(f"contention benchmark failed: {stderr.strip() or stdout.strip()}")
        browser = driver.execute_script("return {longTasks: clientPerfLongTaskSummary(), perf: clientPerfSummary(), resources: performance.getEntriesByType('resource').map(entry => ({name: entry.name, duration: entry.duration, transferSize: entry.transferSize}))}")
        browser["resources"] = bounded_api_resources(browser.get("resources", []))
        measurement_after = capture_measurement_metrics()
        args.output.write_text(json.dumps({"version": 1, "base_url": base_url, "duration_seconds": args.duration, "workload": workload, "browser": browser, "measurement": {"before": measurement_before, "after": measurement_after}, "contention": str(benchmark_output)}, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
        return 0
    finally:
        signal.alarm(0)
        cleanup()
        # Prove the pre-existing service ledger is unchanged: the capture may
        # only ever add/remove its OWN benchmark/browser processes. A service
        # ADDED or REPLACED during the capture is an error; a service that
        # VANISHED exited through its own idle lifecycle — this tool holds no
        # kill path to services (its kill scope is the benchmark process group
        # and the chromedriver descendant tree only).
        ledger_after = ledger_snapshot()
        vanished = sorted(set(ledger_before) - set(ledger_after))
        changed = {name: (ledger_before.get(name), pid) for name, pid in ledger_after.items() if ledger_before.get(name) != pid}
        if changed:
            print(f"error: capture changed the service ledger: {changed}", file=sys.stderr)
        elif vanished:
            print(f"note: service(s) {vanished} exited via their own idle lifecycle during the capture (not capture-caused)", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
