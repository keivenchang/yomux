#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Measure real Finder filesystem traffic with two isolated browsers against a local ephemeral server."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
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

from tests.browser_helpers.webdriver_lease import WebDriverLease
from tests.browser_helpers.webdriver_lease import retire_all
from tests.browser_helpers.browser_layout import (
    WebDriverWait,
    new_chrome_driver,
    register_browser_new_document_script,
    start_browser_server,
    start_isolated_browser_app,
    stop_browser_server,
    stop_isolated_browser_app,
)

FETCH_PROBE_SOURCE = """
(() => {
  window.__finderFsReproMarker = %s;
  if (window.__finderFsReproInstalled) return;
  window.__finderFsReproInstalled = true;
  window.__finderFsReproLog = [];
  window.__finderFsReproFailNextWatchDiff = 0;
  const originalFetch = window.fetch.bind(window);
  window.fetch = (resource, init) => {
    const request = resource instanceof Request ? resource : new Request(resource, init);
    const headers = new Headers(request.headers);
    headers.set('X-YOmux-Measurement', window.__finderFsReproMarker);
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
            "expanded": [root],
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


def capture_server_measurements(app, request_id: str) -> dict[str, Any]:
    payload = app.performance_metrics_payload(measurement_scope="capture")
    recent = [
        row for row in payload.get("recent", [])
        if isinstance(row, dict)
        and isinstance(row.get("details"), dict)
        and row["details"].get("measurement_request_id") == request_id
        and str(row.get("surface") or "") in {"GET /api/fs/watch-diff", "POST /api/fs/batch"}
    ]
    summaries: dict[tuple[str, str], dict[str, Any]] = {}
    for row in recent:
        key = (str(row.get("role") or ""), str(row.get("surface") or ""))
        summary = summaries.setdefault(key, {
            "role": key[0],
            "surface": key[1],
            "count": 0,
            "compute_ms_total": 0.0,
            "compute_ms_max": 0.0,
            "payload_bytes_total": 0,
            "cache": {},
            "request_total_ms_total": 0.0,
            "request_total_ms_max": 0.0,
            "accept_to_route_ms_total": 0.0,
            "accept_to_route_ms_max": 0.0,
        })
        compute_ms = max(0.0, float(row.get("compute_ms") or 0.0))
        summary["count"] += 1
        summary["compute_ms_total"] += compute_ms
        summary["compute_ms_max"] = max(summary["compute_ms_max"], compute_ms)
        summary["payload_bytes_total"] += max(0, int(row.get("payload_bytes") or 0))
        cache_status = str(row.get("cache_status") or "")
        if cache_status:
            summary["cache"][cache_status] = int(summary["cache"].get(cache_status, 0)) + 1
        details = row["details"]
        for field in ("request_total_ms", "accept_to_route_ms"):
            value = max(0.0, float(details.get(field) or 0.0))
            summary[f"{field}_total"] += value
            summary[f"{field}_max"] = max(summary[f"{field}_max"], value)
    summary = []
    for row in summaries.values():
        count = max(1, int(row["count"]))
        row["compute_ms_total"] = round(float(row["compute_ms_total"]), 3)
        row["compute_ms_avg"] = round(float(row["compute_ms_total"]) / count, 3)
        row["compute_ms_max"] = round(float(row["compute_ms_max"]), 3)
        row["request_total_ms_avg"] = round(float(row.pop("request_total_ms_total")) / count, 3)
        row["request_total_ms_max"] = round(float(row["request_total_ms_max"]), 3)
        row["accept_to_route_ms_avg"] = round(float(row.pop("accept_to_route_ms_total")) / count, 3)
        row["accept_to_route_ms_max"] = round(float(row["accept_to_route_ms_max"]), 3)
        summary.append(row)
    summary.sort(key=lambda row: str(row.get("surface") or ""))
    return {"summary": summary, "recent": recent}


def wait_for_server_measurements_quiet(app, request_id: str, timeout: float, quiet_seconds: float = 0.5, drivers: dict[str, Any] | None = None) -> None:
    deadline = time.monotonic() + timeout
    stable_since = time.monotonic()
    previous = None
    while time.monotonic() < deadline:
        current = capture_server_measurements(app, request_id)["summary"]
        signature = tuple((row.get("surface"), row.get("count"), row.get("compute_ms_total")) for row in current)
        if signature != previous:
            previous = signature
            stable_since = time.monotonic()
        elif time.monotonic() - stable_since >= quiet_seconds:
            return
        time.sleep(0.05)
    with app.client_watch_service.lock:
        watch_record = app.client_watch_service.event_watcher_record
        watch_state = {
            "watchd_epoch": watch_record.watchd_epoch,
            "watchd_revision": watch_record.watchd_revision,
            "watchd_generation": tuple(watch_record.filesystem_roots),
            "descriptors": {
                key: {
                    "generation": descriptor.descriptor_generation,
                    "roots": descriptor.roots,
                }
                for key, descriptor in app.client_watch_service.descriptors.items()
            },
            "history": [
                {"token": record.get("token"), "signature": record.get("signature")}
                for record in app.client_watch_service.filesystem_history[-4:]
            ],
        }
    watchd_snapshot, watchd_body = app.watch_client.snapshot(timeout=1.0)
    watchd_product = json.loads(watchd_body) if watchd_body else {}
    raise RuntimeError(
        f"Timed out after {timeout:.1f}s waiting for Finder server measurements to settle: "
        f"roots={app.client_watch_roots_snapshot()!r} watch_state={watch_state!r} "
        f"watchd_snapshot={watchd_snapshot!r} watchd_product={watchd_product!r} "
        f"browser_state={{{', '.join(f'{name!r}: {finder_work_state(driver)!r}' for name, driver in (drivers or {}).items())}}}"
    )


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
            && apiOperationState.pending.size === 0
            && fileExplorerFsBatchQueue.length === 0
            && fileExplorerFsBatchPending.size === 0
            && fileExplorerFsBatchOperations.size === 0
            && fileExplorerFsBatchTimer === null;
        } catch (_error) {
          return false;
        }
        """
    ))


def finder_work_state(driver: Any) -> dict[str, Any]:
    return driver.execute_script(
        """
        try {
          return {
            connected: clientEventTransportState.connected === true,
            api_pending: Array.from(apiOperationState.pending.keys()),
            batch_queue: fileExplorerFsBatchQueue.map(item => ({id: item.id, type: item.type, path: item.path, sent: item.sent === true})),
            batch_pending: Array.from(fileExplorerFsBatchPending.entries()).map(([key, pending]) => ({
              key,
              id: pending?.item?.id || 0,
              path: pending?.item?.path || '',
              sent: pending?.item?.sent === true,
              queued_product: pending?.item?.queuedProduct || null,
              repairing: pending?.item?.repairing === true,
            })),
            batch_operations: Array.from(fileExplorerFsBatchOperations.entries()).map(([id, items]) => ({
              id,
              items: Array.isArray(items) ? items.map(item => ({id: item.id, type: item.type, path: item.path})) : [],
            })),
            batch_timer: fileExplorerFsBatchTimer !== null,
            watch_token: String(fileExplorerFilesystemWatchToken || ''),
            push_token: String(fileExplorerFilesystemPushToken || ''),
            finder_root: currentFileExplorerRoot(),
            expanded: Array.from(fileExplorerExpanded),
            watched_directories: watchedFileExplorerDirectories(),
            server_watch_roots: clientServerWatchRoots(),
            differ_visible: fileExplorerSessionFilesPaneIsVisible(),
          };
        } catch (error) {
          return {error: String(error && (error.stack || error.message || error))};
        }
        """
    )


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


def wait_for_finder_settled(
    drivers: dict[str, Any],
    timeout: float,
    *,
    quiet_seconds: float = 0.35,
    clock=None,
    wait=None,
) -> None:
    clock = time.monotonic if clock is None else clock
    wait = threading.Event().wait if wait is None else wait
    deadline = clock() + timeout
    stable_since = None
    while True:
        now = clock()
        if all(finder_is_settled(driver) for driver in drivers.values()):
            if stable_since is None:
                stable_since = now
            if now - stable_since >= quiet_seconds:
                return
        else:
            stable_since = None
        remaining = deadline - now
        if remaining <= 0:
            states = {name: finder_work_state(driver) for name, driver in drivers.items()}
            raise RuntimeError(
                f"Timed out after {timeout:.1f}s waiting for both Finder clients to remain settled "
                f"for {quiet_seconds:.2f}s: {states}"
            )
        wait(min(0.05, remaining))


def append_line(path: str, text: str) -> None:
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(text)


def stop_watchd_revision_bridge(app: Any, timeout: float) -> None:
    with app.client_watch_service.lock:
        record = app.client_watch_service.event_watcher_record
        worker = record.watchd_worker
        record.watchd_stop_event.set()
    if worker is not None:
        worker.join(timeout=timeout)
        if worker.is_alive():
            raise RuntimeError(f"Timed out after {timeout:.1f}s stopping the fixture watchd revision bridge")


def capture_phase(app, drivers: dict[str, Any], process_cpu_started: float, request_id: str) -> dict[str, Any]:
    server = capture_server_measurements(app, request_id)
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


def measurement_request_id(marker: str) -> str:
    return hashlib.sha256(marker.encode("ascii")).hexdigest()[:16]


def start_measurement_phase(drivers: dict[str, Any]) -> str:
    marker = measurement_marker()
    for driver in drivers.values():
        # Re-registering also makes this phase marker the last new-document script for reloads.
        # The installed fetch wrapper reads the marker dynamically for every request.
        install_fetch_probe(driver, marker)
    return measurement_request_id(marker)


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


def run_measurement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, idle_seconds: float = 2.0, event_timeout: float = 8.0) -> dict[str, Any]:
    fixture = create_fixture_tree(tmp_path)
    runtime = start_isolated_browser_app(monkeypatch, tmp_path, session_count=1, session_cwd=fixture["root"])
    server = thread = None
    drivers: dict[str, Any] = {}
    try:
        server, thread = start_browser_server(monkeypatch, tmp_path, runtime.app, auth_bypass=True)
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
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
        wait_for_condition(
            lambda: runtime.app.client_watch_roots_snapshot() == [fixture["root"]],
            event_timeout,
            "both fixture clients to register only the fixture-owned watch root",
        )
        assert runtime.app.client_watch_roots_snapshot() == [fixture["root"]]
        setup_request_id = measurement_request_id(marker)
        wait_for_server_measurements_quiet(runtime.app, setup_request_id, event_timeout, drivers=drivers)

        phases: dict[str, Any] = {}

        idle_deadline = time.monotonic() + event_timeout
        while True:
            phase_request_id = start_measurement_phase(drivers)
            for driver in drivers.values():
                clear_browser_log(driver)
            phase_cpu_started = time.process_time()
            time.sleep(max(0.1, idle_seconds))
            idle_phase = capture_phase(runtime.app, drivers, phase_cpu_started, phase_request_id)
            no_client_finder_traffic = all(
                not client["request_counts"]
                for client in idle_phase["clients"].values()
            )
            if no_client_finder_traffic and not idle_phase["server"]["summary"]:
                phases["idle"] = idle_phase
                break
            if time.monotonic() >= idle_deadline:
                raise RuntimeError(f"Timed out after {event_timeout:.1f}s waiting for a clean Finder idle measurement window")

        for driver in drivers.values():
            clear_browser_log(driver)
        phase_request_id = start_measurement_phase(drivers)
        phase_cpu_started = time.process_time()
        file_change_before = {name: finder_change_state(driver) for name, driver in drivers.items()}
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
        phases["file_change"] = capture_phase(runtime.app, drivers, phase_cpu_started, phase_request_id)

        phase_request_id = start_measurement_phase(drivers)
        for driver in drivers.values():
            clear_browser_log(driver)
            set_fail_next_watch_diff(driver, 1)
        phase_cpu_started = time.process_time()
        for driver in drivers.values():
            trigger_forced_watch_diff_refresh(driver)
        time.sleep(0.25)
        phases["forced_watch_diff_failure"] = capture_phase(runtime.app, drivers, phase_cpu_started, phase_request_id)

        phase_request_id = start_measurement_phase(drivers)
        for driver in drivers.values():
            clear_browser_log(driver)
        phase_cpu_started = time.process_time()
        for driver in drivers.values():
            driver.get(f"{base_url}/{search}")
            wait_for_app(driver, event_timeout)
            open_root(driver, fixture["root"], fixture["expected_row"], event_timeout)
        wait_for_finder_settled(drivers, event_timeout)
        wait_for_server_measurements_quiet(runtime.app, phase_request_id, event_timeout, drivers=drivers)
        phases["reload"] = capture_phase(runtime.app, drivers, phase_cpu_started, phase_request_id)

        phase_request_id = start_measurement_phase(drivers)
        for driver in drivers.values():
            clear_browser_log(driver)
        phase_cpu_started = time.process_time()
        for driver in drivers.values():
            open_root(driver, fixture["nested_root"], fixture["nested_row"], event_timeout)
        phases["navigation"] = capture_phase(runtime.app, drivers, phase_cpu_started, phase_request_id)

        return {
            "version": 2,
            "base_url": base_url,
            "fixture": fixture,
            "file_change_delivery": "native-watcher",
            "phases": phases,
        }
    finally:
        # One shared owner retires both drivers: bounded quit -> TERM -> KILL -> reap -> final proof,
        # never a bare best-effort quit that leaves a chromedriver behind when quit() hangs.
        retire_all([WebDriverLease.from_driver(driver) for driver in drivers.values()])
        if server is not None and thread is not None:
            stop_browser_server(server, thread)
        stop_isolated_browser_app(runtime)


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
