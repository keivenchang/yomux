#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Measure real Finder filesystem traffic with two isolated browsers against a local ephemeral server."""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.browser_helpers.browser_layout import (
    WebDriverWait,
    new_chrome_driver,
    register_browser_new_document_script,
    start_browser_share_server,
    start_isolated_browser_share_app,
    stop_browser_share_server,
    stop_isolated_browser_share_app,
)

FETCH_PROBE_SOURCE = """
(() => {
  if (window.__finderFsReproInstalled) return;
  window.__finderFsReproInstalled = true;
  window.__finderFsReproMarker = %s;
  window.__finderFsReproLog = [];
  window.__finderFsReproFailNextWatchDiff = 0;
  const originalFetch = window.fetch.bind(window);
  window.fetch = (resource, init) => {
    const request = resource instanceof Request ? resource : new Request(resource, init);
    const headers = new Headers(request.headers);
    headers.set('X-YOLOmux-Measurement', window.__finderFsReproMarker);
    const trackedRequest = new Request(request, {headers});
    const url = new URL(trackedRequest.url, location.href);
    const record = {
      path: url.pathname,
      method: String(trackedRequest.method || 'GET').toUpperCase(),
      started_at_ms: Number(performance.now().toFixed(3)),
      result: 'pending',
    };
    window.__finderFsReproLog.push(record);
    if (url.pathname === '/api/fs/watch-diff' && Number(window.__finderFsReproFailNextWatchDiff || 0) > 0) {
      window.__finderFsReproFailNextWatchDiff -= 1;
      record.result = 'rejected';
      record.error = 'forced-watch-diff-failure';
      return Promise.reject(new Error('forced watch-diff failure'));
    }
    return originalFetch(trackedRequest).then(
      response => {
        record.result = 'fulfilled';
        record.status = Number(response.status || 0);
        return response;
      },
      error => {
        record.result = 'rejected';
        record.error = String(error && (error.stack || error.message || error));
        throw error;
      },
    );
  };
})();
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="JSON report path")
    parser.add_argument("--idle-seconds", type=float, default=2.0)
    parser.add_argument("--event-timeout", type=float, default=8.0)
    return parser.parse_args(argv)


def saved_layout_search(session: str, root: str) -> str:
    state = {
        "v": 1,
        "finder": {
            "root": root,
            "rootMode": "files",
            "mode": "files",
            "session": session,
            "showHidden": False,
            "expanded": [str(REPO_ROOT)],
        },
        "scroll": [{"target": "finder:files", "kind": "finder", "top": 0, "left": 0, "mode": "files"}],
    }
    return "?" + urlencode({
        "bootCase": "finder-fs-repro",
        "sessions": f"files,{session}",
        "layout": "slot1",
        "tabs": "slot1:files",
        "finder": "files",
        "state": json.dumps(state, separators=(",", ":")),
    })


def create_fixture_tree(root: Path) -> dict[str, str]:
    project = root / "finder-repro"
    nested = project / "nested"
    deep = nested / "deeper"
    deep.mkdir(parents=True, exist_ok=True)
    watched = project / "watched.txt"
    watched.write_text("v1\n", encoding="utf-8")
    (nested / "nested.txt").write_text("nested\n", encoding="utf-8")
    (deep / "deep.txt").write_text("deep\n", encoding="utf-8")
    return {
        "root": str(project),
        "watched_file": str(watched),
        "nested_root": str(nested),
        "expected_row": str(project / "watched.txt"),
        "nested_row": str(nested / "nested.txt"),
    }


def install_fetch_probe(driver, marker: str) -> None:
    source = FETCH_PROBE_SOURCE % json.dumps(marker)
    register_browser_new_document_script(driver, source, reset_after_test=False)
    driver.execute_script(source)


def wait_for_app(driver, timeout: float) -> None:
    wait = WebDriverWait(driver, timeout)
    wait.until(lambda current: current.execute_script("return typeof openFileExplorerAt === 'function' && typeof refreshFileExplorerPanelTree === 'function' && document.getElementById('grid') !== null"))


def open_root(driver, root: str, expected_row: str, timeout: float) -> None:
    result = driver.execute_async_script(
        """
        const root = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
                await openFileExplorerAt(root, {syncSelection: true});
                const panel = document.querySelector('.file-explorer-panel');
                if (!panel) throw new Error('Finder panel is missing');
                await refreshFileExplorerPanelTree(panel, {force: true});
                const waitFor = window.__yolomuxTestWaitFor;
                await waitFor(() => document.querySelectorAll('.file-explorer-panel .file-tree-row').length > 0, {
                  timeoutMs: 6000,
                  description: 'Finder tree render',
                });
            done({
              ok: true,
              root: document.querySelector('.file-explorer-path-inline')?.value || '',
              rowCount: document.querySelectorAll('.file-explorer-panel .file-tree-row').length,
            });
          } catch (error) {
            done({ok: false, error: String(error && (error.stack || error.message || error))});
          }
        })();
        """,
        root,
    )
    if result != {"ok": True, "root": result.get("root", "")} and not result.get("ok"):
        raise RuntimeError(result.get("error") or "Finder root did not open")
    wait_for_app(driver, timeout)


def clear_browser_log(driver) -> None:
    driver.execute_script("if (Array.isArray(window.__finderFsReproLog)) window.__finderFsReproLog.length = 0;")


def browser_log(driver) -> list[dict[str, Any]]:
    return driver.execute_script("return JSON.parse(JSON.stringify(window.__finderFsReproLog || []));")


def set_fail_next_watch_diff(driver, count: int) -> None:
    driver.execute_script("window.__finderFsReproFailNextWatchDiff = arguments[0];", int(count))


def summarize_fetch_log(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter()
    by_path = Counter()
    rejected = Counter()
    for record in records:
        path = str(record.get("path") or "")
        result = str(record.get("result") or "")
        method = str(record.get("method") or "").upper()
        if path in {"/api/fs/watch-diff", "/api/fs/batch"}:
            by_path[path] += 1
            counts[f"{method} {path}"] += 1
            if result == "rejected":
                rejected[path] += 1
    return {
        "request_counts": {key: int(by_path[key]) for key in sorted(by_path)},
        "request_counts_by_method": {key: int(counts[key]) for key in sorted(counts)},
        "rejected_counts": {key: int(rejected[key]) for key in sorted(rejected)},
        "records": records,
    }


def clear_server_measurements(app) -> None:
    with app.performance_record_lock:
        app.performance_records.clear()


def capture_server_measurements(app) -> dict[str, Any]:
    payload = app.performance_metrics_payload(measurement_scope="capture")
    summary = [
        row for row in payload.get("summary", [])
        if str(row.get("surface") or "") in {"GET /api/fs/watch-diff", "POST /api/fs/batch"}
    ]
    summary.sort(key=lambda row: str(row.get("surface") or ""))
    return {"summary": summary}


def wait_for_condition(predicate, timeout: float, description: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise RuntimeError(f"Timed out after {timeout:.1f}s waiting for {description}")


def finder_is_settled(driver: Any) -> bool:
    return bool(driver.execute_script(
        """
        try {
          return clientEventTransportState.connected === true
            && fileExplorerFsBatchQueue.length === 0
            && fileExplorerFsBatchPending.size === 0
            && fileExplorerFsBatchTimer === null;
        } catch (_error) {
          return false;
        }
        """
    ))


def finder_change_state(driver: Any) -> dict[str, str]:
    """Return browser-visible Finder state that changes for compact and full SSE frames."""
    return driver.execute_script(
        """
        try {
          const root = currentFileExplorerRoot();
          const record = fileExplorerDirectoryRecord(root);
          return {
            watch_token: String(fileExplorerFilesystemWatchToken || ''),
            root_signature: String(record?.signature || ''),
          };
        } catch (_error) {
          return {watch_token: '', root_signature: ''};
        }
        """,
    )


def wait_for_finder_settled(drivers: dict[str, Any], timeout: float) -> None:
    wait_for_condition(
        lambda: all(finder_is_settled(driver) for driver in drivers.values()),
        timeout,
        "both Finder clients to drain their bootstrap batch work",
    )
    time.sleep(0.35)
    if not all(finder_is_settled(driver) for driver in drivers.values()):
        raise RuntimeError("Finder batch work restarted during the quiet window")


def append_line(path: str, text: str) -> None:
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(text)


def capture_phase(app, drivers: dict[str, Any], process_cpu_started: float) -> dict[str, Any]:
    server = capture_server_measurements(app)
    server["process_cpu_seconds"] = round(max(0.0, time.process_time() - process_cpu_started), 6)
    return {
        "clients": {
            name: summarize_fetch_log(browser_log(driver))
            for name, driver in drivers.items()
        },
        "finder_state": {name: finder_change_state(driver) for name, driver in drivers.items()},
        "server": server,
    }


def measurement_marker() -> str:
    return f"capture-{uuid.uuid4().hex}"


def open_clients(drivers: dict[str, Any], base_url: str, search: str, fixture: dict[str, str], timeout: float) -> None:
    for driver in drivers.values():
        driver.get(f"{base_url}/{search}")
        wait_for_app(driver, timeout)
        open_root(driver, fixture["root"], fixture["expected_row"], timeout)


def trigger_forced_watch_diff_refresh(driver) -> None:
    result = driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            await refreshFileExplorerFromWatchDiff({mode: 'diff', roots: [currentFileExplorerRoot()]}, {full: false});
            done({ok: true});
          } catch (error) {
            done({ok: false, error: String(error && (error.stack || error.message || error))});
          }
        })();
        """,
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "forced watch-diff refresh failed")


def run_measurement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, idle_seconds: float = 2.0, event_timeout: float = 8.0, force_full_filesystem_event: bool = False) -> dict[str, Any]:
    runtime = start_isolated_browser_share_app(monkeypatch, tmp_path, session_count=1)
    server = thread = None
    drivers: dict[str, Any] = {}
    try:
        server, thread = start_browser_share_server(monkeypatch, tmp_path, runtime.app, auth_bypass=True)
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        fixture = create_fixture_tree(tmp_path)
        marker = measurement_marker()
        drivers = {
            "client-a": new_chrome_driver(window_size=(1280, 900)),
            "client-b": new_chrome_driver(window_size=(1280, 900)),
        }
        for driver in drivers.values():
            install_fetch_probe(driver, marker)
        search = saved_layout_search(runtime.sessions[0], fixture["root"])
        open_clients(drivers, base_url, search, fixture, event_timeout)
        wait_for_finder_settled(drivers, event_timeout)

        phases: dict[str, Any] = {}

        for driver in drivers.values():
            clear_browser_log(driver)
        clear_server_measurements(runtime.app)
        phase_cpu_started = time.process_time()
        time.sleep(max(0.1, idle_seconds))
        phases["idle"] = capture_phase(runtime.app, drivers, phase_cpu_started)

        for driver in drivers.values():
            clear_browser_log(driver)
        clear_server_measurements(runtime.app)
        phase_cpu_started = time.process_time()
        file_change_before = {name: finder_change_state(driver) for name, driver in drivers.items()}
        if force_full_filesystem_event:
            with runtime.app.client_watch_service.lock:
                runtime.app.client_watch_service.filesystem_last_full_at = 0.0
        append_line(fixture["watched_file"], "file-change\n")
        wait_for_condition(
            lambda: all(
                finder_change_state(driver) != file_change_before[name]
                for name, driver in drivers.items()
            ),
            event_timeout,
            "both Finder clients to apply a real file change",
        )
        time.sleep(0.25)
        phases["file_change"] = capture_phase(runtime.app, drivers, phase_cpu_started)

        for driver in drivers.values():
            clear_browser_log(driver)
            set_fail_next_watch_diff(driver, 1)
        clear_server_measurements(runtime.app)
        phase_cpu_started = time.process_time()
        for driver in drivers.values():
            trigger_forced_watch_diff_refresh(driver)
        time.sleep(0.25)
        phases["forced_watch_diff_failure"] = capture_phase(runtime.app, drivers, phase_cpu_started)

        for driver in drivers.values():
            clear_browser_log(driver)
        clear_server_measurements(runtime.app)
        phase_cpu_started = time.process_time()
        for driver in drivers.values():
            driver.get(f"{base_url}/{search}")
            wait_for_app(driver, event_timeout)
            open_root(driver, fixture["root"], fixture["expected_row"], event_timeout)
        phases["reload"] = capture_phase(runtime.app, drivers, phase_cpu_started)

        for driver in drivers.values():
            clear_browser_log(driver)
        clear_server_measurements(runtime.app)
        phase_cpu_started = time.process_time()
        for driver in drivers.values():
            open_root(driver, fixture["nested_root"], fixture["nested_row"], event_timeout)
        phases["navigation"] = capture_phase(runtime.app, drivers, phase_cpu_started)

        return {
            "version": 2,
            "base_url": base_url,
            "fixture": fixture,
            "file_change_delivery": "full-sse" if force_full_filesystem_event else "native-watcher",
            "phases": phases,
        }
    finally:
        for driver in drivers.values():
            try:
                driver.quit()
            except Exception:
                pass
        if server is not None and thread is not None:
            stop_browser_share_server(server, thread)
        stop_isolated_browser_share_app(runtime)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    work_root = Path(args.output).parent / f"finder-fs-repro-{uuid.uuid4().hex}"
    work_root.mkdir(parents=True, exist_ok=True)
    with pytest.MonkeyPatch.context() as monkeypatch:
        report = run_measurement(
            monkeypatch,
            work_root,
            idle_seconds=float(args.idle_seconds),
            event_timeout=float(args.event_timeout),
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
