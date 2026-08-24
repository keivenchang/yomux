#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Drive authenticated YO!stats browser workloads and capture bounded CPU evidence.

This is an operator-only measurement tool. It creates a local authenticated
browser session from the configured account's existing server-side cookie
material, never reads or emits a plaintext password, and writes the resulting
browser and CPU evidence only beneath /tmp unless the caller deliberately
chooses another transient output path.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
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

from tools.instance_isolation import apply_early_instance_environment


# Authentication, state, and local-service paths are resolved at product-module
# import time. Match the server entrypoint so a managed capture reads the exact
# row selected by --port instead of silently authenticating against the default.
apply_early_instance_environment(sys.argv[1:])

from yolomux_lib.auth import AUTH_CONFIG_PATH
from yolomux_lib.auth import AUTH_COOKIE_NAME
from yolomux_lib.auth import AuthUser
from yolomux_lib.auth import auth_cookie_value
from yolomux_lib.auth import read_auth_users
from yolomux_lib.common import RUNTIME_DIR
from yolomux_lib.common import STATE_DIR
from yolomux_lib.filesystem.io_ops import read_json_file
from yolomux_lib.infra.listener_census import unique_listener_pid
from yolomux_lib.local_services.registry import bounded_process_table
from yolomux_lib.local_services.registry import tracked_local_service_groups
from yolomux_lib.local_services.watchdog import GroupOverloadWatchdog
from tests.browser_helpers.browser_console import assert_browser_local_error_free
from tests.browser_helpers.browser_console import read_browser_console_log
from tests.browser_helpers.webdriver_lease import WebDriverLease
from tests.browser_helpers.webdriver_lease import process_start_key
from tools.instance_isolation import is_managed_instance_port
from tools.yostats_capture_common import positive_int, process_cpu_seconds


DETERMINISTIC_REPETITIONS = 10
DETERMINISTIC_PROFILE_RATE_HZ = 99
DETERMINISTIC_PROFILE_SAMPLE_ERROR_CEILING = 0
DETERMINISTIC_OWNER_COUNTER_NAMES = (
    "session_discovery",
    "transcript_tail_scan",
    "session_files_materialization",
    "jobd_work_graph_rebuild",
    "provider_metadata_rebuild",
    "statsd_unchanged_cell_materialization",
    "statusd_unchanged_pane_capture",
)
DETERMINISTIC_OWNER_COUNTER_SOURCES = {
    "watchd_refresh": (
        "session_discovery",
        "transcript_tail_scan",
        "session_files_materialization",
        "jobd_work_graph_rebuild",
    ),
    "jobd": (
        "jobd_work_graph_rebuild",
        "provider_metadata_rebuild",
    ),
    "statsd": ("statsd_unchanged_cell_materialization",),
    "statusd": ("statusd_unchanged_pane_capture",),
}
DEMAND_DRIVEN_SNAPSHOT_PENDING_CODES = {
    "system_status_snapshot_stale",
    "system_status_snapshot_unavailable",
}


class MeasurementSnapshotPending(RuntimeError):
    """A demanded status snapshot has not published its first current body yet."""


def find_chrome() -> str:
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), "")


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
    for record_path in (RUNTIME_DIR / "services").glob("*.service.json"):
        record = read_json_file(record_path, None, exceptions=(OSError, json.JSONDecodeError))
        if record is None:
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
    parser.add_argument("--duration", type=positive_int, default=60)
    parser.add_argument(
        "--workload",
        choices=("active", "idle-yostats", "deterministic-fanout"),
        default="active",
        help="active runs edit/reload/drag; idle-yostats holds YO!stats; deterministic-fanout runs the frozen 0.7.8 acceptance workload",
    )
    parser.add_argument("--session", action="append", default=[], help="exact tmux session for deterministic-fanout; repeat twice")
    parser.add_argument("--output", type=Path, required=True, help="browser evidence JSON path, normally under /tmp")
    args = parser.parse_args(argv)
    if args.workload == "deterministic-fanout" and len(args.session) != 2:
        parser.error("deterministic-fanout requires exactly two --session values")
    return args


def deterministic_fanout_workload_contract(duration_seconds: int) -> dict[str, object]:
    """Freeze the workload denominator before any browser or profiler process starts."""
    return {
        "schema_version": 1,
        "steps": {
            "authenticated_cold_load": 1,
            "identical_watch_root_renewals": DETERMINISTIC_REPETITIONS,
            "operation_add_remove_cycles": DETERMINISTIC_REPETITIONS,
            "unchanged_watchd_revisions": DETERMINISTIC_REPETITIONS,
            "client_event_source_reconnects": 1,
            "producer_restarts": 1,
        },
        "source_generation_owners": {
            "watch_roots": "deterministic-watch-roots",
            "operation_cycles": "deterministic-operation-cycle",
            "watchd_revisions": "filesystem-watch-diff",
            "client_events": "client-event-transport",
            "producer_restart": "watchd",
        },
        "owner_counter_names": list(DETERMINISTIC_OWNER_COUNTER_NAMES),
        "profiler": {
            "tool": "py-spy",
            "rate_hz": DETERMINISTIC_PROFILE_RATE_HZ,
            "duration_seconds": int(duration_seconds),
            "threads": True,
            "gil_only": True,
            "sample_error_ceiling": DETERMINISTIC_PROFILE_SAMPLE_ERROR_CEILING,
        },
    }


def output_path_is_under_tmp(path: Path) -> bool:
    """Authorize evidence output from its resolved path, not a lexical prefix."""
    return Path(path).expanduser().resolve(strict=False).is_relative_to(Path("/tmp").resolve())


def wait_for_app(driver: webdriver.Chrome, tmux_sessions: list[str], timeout: int) -> None:
    wait = WebDriverWait(driver, timeout)
    wait.until(lambda current: current.execute_script("return typeof setDebugGraphRange === 'function' && typeof selectSession === 'function' && document.getElementById('grid') !== null"))
    observed = driver.execute_script("return Array.isArray(sessions) ? sessions.filter(isTmuxSession) : []")
    missing = [session for session in tmux_sessions if session not in observed]
    if missing:
        raise RuntimeError(
            f"canonical workload lost its tmux sessions: missing {missing} from {observed}"
        )


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


def compose_deterministic_owner_counters(
    performance_diagnostics: dict[str, object],
    system_status: dict[str, object],
    system_status_advanced: dict[str, object],
) -> dict[str, dict[str, int]]:
    """Compose every lane's absolute counter without hiding its diagnostics owner."""

    refresh = system_status_advanced.get("refresh")
    watchd = refresh.get("owner_invocations") if isinstance(refresh, dict) else None
    observation = performance_diagnostics.get("browser_observation_status")
    statsd = observation.get("owner_counters") if isinstance(observation, dict) else None
    local_services = system_status.get("local_services")
    service_rows = local_services.get("services") if isinstance(local_services, dict) else None
    services = {
        str(row.get("service") or row.get("id") or ""): row
        for row in service_rows if isinstance(row, dict)
    } if isinstance(service_rows, list) else {}
    jobd_row = services.get("jobd")
    statusd_row = services.get("statusd")
    source_payloads = {
        "watchd_refresh": watchd,
        "jobd": jobd_row.get("owner_invocations") if isinstance(jobd_row, dict) else None,
        "statsd": statsd,
        "statusd": statusd_row.get("owner_invocations") if isinstance(statusd_row, dict) else None,
    }
    sources: dict[str, dict[str, int]] = {}
    missing: list[str] = []
    for source, names in DETERMINISTIC_OWNER_COUNTER_SOURCES.items():
        payload = source_payloads[source]
        source_values: dict[str, int] = {}
        for name in names:
            value = payload.get(name) if isinstance(payload, dict) else None
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                missing.append(f"{source}.{name}")
                continue
            source_values[name] = value
        sources[source] = source_values
    if missing:
        raise RuntimeError(
            "deterministic owner diagnostics omit required counter(s): " + ", ".join(missing)
        )
    totals = {
        name: sum(source.get(name, 0) for source in sources.values())
        for name in DETERMINISTIC_OWNER_COUNTER_NAMES
    }
    return {"totals": totals, "sources": sources}


def capture_measurement_metrics(
    driver: webdriver.Chrome,
    *,
    require_owner_counters: bool = False,
) -> dict[str, object]:
    """Read capture-scoped metrics through the authenticated workload browser."""
    response = driver.execute_async_script(
        """
        const requireOwnerCounters = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const measurementState = window.__yolomuxMeasurementFetch;
            const fetchImpl = measurementState?.originalFetch || window.fetch.bind(window);
            const requests = [fetchImpl('/api/diagnostics/performance?measurement_scope=capture', {
              credentials: 'same-origin',
              cache: 'no-store',
            })];
            if (requireOwnerCounters) {
              requests.push(fetchImpl('/api/diagnostics/performance', {
                credentials: 'same-origin',
                cache: 'no-store',
              }));
              requests.push(fetchImpl('/api/system-status', {
                credentials: 'same-origin',
                cache: 'no-store',
              }));
              requests.push(fetchImpl('/api/system-status/advanced', {
                credentials: 'same-origin',
                cache: 'no-store',
              }));
            }
            const [response, diagnosticsResponse, systemStatusResponse, systemStatusAdvancedResponse] = await Promise.all(requests);
            const payload = await response.json();
            if (!response.ok) {
              done({ok: false, error: String(payload?.error || `HTTP ${response.status}`)});
              return;
            }
            if (!requireOwnerCounters) {
              done({ok: true, perf: payload?.perf});
              return;
            }
            const diagnostics = await diagnosticsResponse.json();
            const systemStatus = await systemStatusResponse.json();
            const systemStatusAdvanced = await systemStatusAdvancedResponse.json();
            if (!diagnosticsResponse.ok) {
              done({ok: false, error: String(diagnostics?.error || `HTTP ${diagnosticsResponse.status}`)});
              return;
            }
            if (!systemStatusResponse.ok) {
              done({ok: false, error: String(systemStatus?.error || `HTTP ${systemStatusResponse.status}`)});
              return;
            }
            if (!systemStatusAdvancedResponse.ok) {
              done({ok: false, error: String(systemStatusAdvanced?.error || `HTTP ${systemStatusAdvancedResponse.status}`)});
              return;
            }
            done({
              ok: true,
              perf: payload?.perf,
              performanceDiagnostics: diagnostics,
              systemStatus,
              systemStatusAdvanced,
            });
          } catch (error) {
            done({ok: false, error: String(error?.message || error)});
          }
        })();
        """,
        require_owner_counters,
    )
    performance = response.get("perf") if isinstance(response, dict) and response.get("ok") else None
    if not isinstance(performance, dict):
        error = response.get("error") if isinstance(response, dict) else "invalid response"
        raise RuntimeError(f"capture measurement metrics unavailable: {error or 'invalid response'}")
    if not require_owner_counters:
        return dict(performance)
    performance_diagnostics = response.get("performanceDiagnostics")
    system_status = response.get("systemStatus")
    system_status_advanced = response.get("systemStatusAdvanced")
    if not all(isinstance(payload, dict) for payload in (
        performance_diagnostics,
        system_status,
        system_status_advanced,
    )):
        raise RuntimeError("capture measurement owner diagnostics are invalid")
    pending_snapshots = []
    for name, payload in (
        ("system-status", system_status),
        ("system-status-advanced", system_status_advanced),
    ):
        snapshot = payload.get("snapshot")
        reason_code = str(snapshot.get("reason_code") or "") if isinstance(snapshot, dict) else ""
        if payload.get("ok") is False and reason_code in DEMAND_DRIVEN_SNAPSHOT_PENDING_CODES:
            pending_snapshots.append(f"{name}:{reason_code}")
    if pending_snapshots:
        raise MeasurementSnapshotPending(
            "measurement owner snapshot(s) are publishing: " + ", ".join(pending_snapshots)
        )
    owner_counter_sample = compose_deterministic_owner_counters(
        performance_diagnostics,
        system_status,
        system_status_advanced,
    )
    result = dict(performance)
    result["owner_counters"] = dict(owner_counter_sample["totals"])
    result["owner_counter_sources"] = dict(owner_counter_sample["sources"])
    return result


def wait_for_deterministic_measurement_baseline(
    driver: webdriver.Chrome,
    timeout: int = 20,
) -> dict[str, object]:
    """Demand both retained diagnostic bodies, then return their first current counter sample."""
    try:
        result = WebDriverWait(
            driver,
            timeout,
            ignored_exceptions=(MeasurementSnapshotPending,),
        ).until(
            lambda current: capture_measurement_metrics(current, require_owner_counters=True)
        )
    except TimeoutException as error:
        raise RuntimeError(
            "deterministic measurement owner snapshots did not become current"
        ) from error
    if not isinstance(result, dict):
        raise RuntimeError("deterministic measurement baseline is invalid")
    return result


def measurement_marker_digest(marker: str) -> str:
    return hashlib.sha256(marker.encode("ascii")).hexdigest()[:16]


def validate_capture_request_join(
    marker: str,
    issued: list[dict[str, object]] | dict[str, object],
    performance: dict[str, object],
) -> dict[str, object]:
    ledger = issued if isinstance(issued, dict) else {"entries": issued, "dropped": 0}
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        raise RuntimeError("capture request ledger is invalid")
    dropped = int(ledger.get("dropped") or 0)
    if dropped:
        raise RuntimeError(f"capture request ledger dropped {dropped} request(s)")
    issued_ids = [str(entry.get("request_id") or "") for entry in entries if isinstance(entry, dict)]
    if len(issued_ids) != len(entries) or not all(issued_ids):
        raise RuntimeError("capture request ledger contains a missing request ID")
    issued_duplicates = sorted(request_id for request_id, count in Counter(issued_ids).items() if count > 1)
    if issued_duplicates:
        raise RuntimeError(f"capture request ledger contains duplicate request IDs: {issued_duplicates}")

    recent = performance.get("recent")
    capture_store = performance.get("capture")
    if not isinstance(recent, list) or not isinstance(capture_store, dict):
        raise RuntimeError("capture metrics omit the bounded capture store")
    digest = measurement_marker_digest(marker)
    records = [
        record
        for record in recent
        if isinstance(record, dict)
        and isinstance(record.get("details"), dict)
        and record["details"].get("measurement_request_id") == digest
    ]
    server_ids = [str(record["details"].get("transport_request_id") or "") for record in records]
    server_counts = Counter(server_ids)
    issued_set = set(issued_ids)
    server_set = set(server_ids)
    missing = sorted(issued_set - server_set)
    duplicate = sorted(request_id for request_id, count in server_counts.items() if not request_id or count > 1)
    unexpected = sorted(server_set - issued_set)
    if missing or duplicate or unexpected:
        raise RuntimeError(
            f"capture request join failed: missing={missing} duplicate={duplicate} unexpected={unexpected}"
        )
    issued_by_id = {str(entry["request_id"]): entry for entry in entries}
    records_by_id = {str(record["details"]["transport_request_id"]): record for record in records}
    browser_transport_failures = sorted(
        request_id
        for request_id, entry in issued_by_id.items()
        if not isinstance(entry.get("status"), int) or int(entry["status"]) <= 0
    )
    if browser_transport_failures:
        raise RuntimeError(f"capture request join has browser status 0: {browser_transport_failures}")
    mismatches = []
    for request_id in issued_ids:
        entry = issued_by_id[request_id]
        details = records_by_id[request_id]["details"]
        browser_fields = (
            str(entry.get("method") or "").upper(),
            str(entry.get("path") or ""),
            int(entry.get("status") or 0),
        )
        server_fields = (
            str(details.get("method") or "").upper(),
            str(details.get("path") or ""),
            int(details.get("status") or 0),
        )
        if browser_fields != server_fields:
            mismatches.append(request_id)
    if mismatches:
        raise RuntimeError(f"capture request join field mismatch: {sorted(mismatches)}")
    fs_batch_requests = [
        {
            "request_id": str(entry.get("request_id") or ""),
            "item_count": entry.get("fs_batch_item_count"),
        }
        for entry in entries
        if isinstance(entry, dict) and entry.get("path") == "/api/fs/batch"
    ]
    return {
        "join": {
            "issued": len(issued_ids),
            "server_records": len(records),
            "missing": missing,
            "duplicate": duplicate,
            "unexpected": unexpected,
        },
        "capture_store": dict(capture_store),
        "issued": entries,
        "records": records,
        "browser_ledger": {
            "max_concurrent_api_fetches": ledger.get("max_concurrent_api_fetches"),
            "peak_api_fetches": ledger.get("peak_api_fetches"),
            "fs_batch_requests": fs_batch_requests,
            "event_sources": ledger.get("event_sources"),
        },
    }


MEASUREMENT_FETCH_INSTALL_SCRIPT = """
        const marker = __YOLOMUX_MEASUREMENT_MARKER__;
        const originalFetch = window.fetch.bind(window);
        let sequence = 0;
        let dropped = 0;
        let activeApiFetches = 0;
        let maxConcurrentApiFetches = 0;
        let peakApiFetches = [];
        const activeApiRequests = new Map();
        const entries = [];
        const pending = new Set();
        const snapshot = () => ({
          entries: entries.map(entry => ({...entry})),
          pending: [...pending],
          dropped,
          max_concurrent_api_fetches: maxConcurrentApiFetches,
          peak_api_fetches: peakApiFetches.map(entry => ({...entry})),
          event_sources: window.__yolomuxMeasurementEventSources?.snapshot?.() || null,
          active: window.fetch === measurementFetch,
        });
        const measurementFetch = async (resource, init) => {
          const rawBody = !(resource instanceof Request) && typeof init?.body === 'string' ? init.body : '';
          const request = resource instanceof Request ? resource : new Request(resource, init);
          const parsed = new URL(request.url, window.location.href);
          if (!parsed.pathname.startsWith('/api/')) return originalFetch(request);
          const headers = new Headers(request.headers);
          headers.set('X-YOLOmux-Measurement', marker);
          let requestId = String(headers.get('X-YOLOmux-Request-ID') || '');
          if (!/^r-[A-Za-z0-9._-]{1,120}$/.test(requestId)) {
            requestId = `r-capture-${Date.now().toString(36)}-${(++sequence).toString(36)}`;
            headers.set('X-YOLOmux-Request-ID', requestId);
          }
          const entry = {
            request_id: requestId,
            method: String(request.method || 'GET').toUpperCase(),
            path: parsed.pathname,
            status: null,
          };
          if (parsed.pathname === '/api/fs/batch') {
            try {
              const parsedBody = JSON.parse(rawBody);
              entry.fs_batch_item_count = Array.isArray(parsedBody?.requests) ? parsedBody.requests.length : null;
            } catch (_error) {
              entry.fs_batch_item_count = null;
            }
          }
          if (entries.length < 4096) entries.push(entry);
          else dropped += 1;
          pending.add(requestId);
          activeApiFetches += 1;
          activeApiRequests.set(requestId, {
            request_id: requestId,
            method: entry.method,
            path: entry.path,
          });
          if (activeApiFetches > maxConcurrentApiFetches) {
            maxConcurrentApiFetches = activeApiFetches;
            peakApiFetches = [...activeApiRequests.values()].map(active => ({...active}));
          }
          try {
            const response = await originalFetch(new Request(request, {headers}));
            entry.status = Number(response.status) || 0;
            return response;
          } catch (error) {
            entry.status = 0;
            throw error;
          } finally {
            pending.delete(requestId);
            activeApiFetches -= 1;
            activeApiRequests.delete(requestId);
          }
        };
        window.fetch = measurementFetch;
        window.__yolomuxMeasurementFetch = {
          originalFetch,
          snapshot,
          stop: () => {
            if (window.fetch === measurementFetch) window.fetch = originalFetch;
            return snapshot();
          },
        };
"""

DETERMINISTIC_EVENT_SOURCE_INSTALL_SCRIPT = r"""
        (() => {
          const NativeEventSource = window.EventSource;
          if (typeof NativeEventSource !== 'function') return;
          let sequence = 0;
          let maxLive = 0;
          const created = [];
          const closed = [];
          const live = new Map();
          const InstrumentedEventSource = new Proxy(NativeEventSource, {
            construct(target, args) {
              const source = Reflect.construct(target, args, target);
              let tracked = false;
              try {
                tracked = new URL(String(args[0] || ''), window.location.href).pathname === '/api/client-events';
              } catch (_error) {
                tracked = false;
              }
              if (!tracked) return source;
              const identity = `client-event-source-${++sequence}`;
              created.push(identity);
              live.set(identity, source);
              maxLive = Math.max(maxLive, live.size);
              const nativeClose = source.close.bind(source);
              source.close = () => {
                if (live.delete(identity)) closed.push(identity);
                return nativeClose();
              };
              return source;
            },
          });
          window.EventSource = InstrumentedEventSource;
          window.__yolomuxMeasurementEventSources = {
            snapshot: () => ({
              created: [...created],
              closed: [...closed],
              live: [...live.keys()],
              max_live: maxLive,
              replacements: Math.max(0, created.length - 1),
            }),
          };
        })();
"""


def measurement_fetch_install_script(marker_expression: str) -> str:
    return MEASUREMENT_FETCH_INSTALL_SCRIPT.replace("__YOLOMUX_MEASUREMENT_MARKER__", marker_expression)


def deterministic_measurement_preload_script(marker: str) -> str:
    """Install body-free fetch and client-event identity evidence before the cold document."""
    return measurement_fetch_install_script(json.dumps(marker)) + DETERMINISTIC_EVENT_SOURCE_INSTALL_SCRIPT


def install_measurement_fetch_header(driver: webdriver.Chrome, marker: str) -> None:
    """Tag and ledger bounded in-page API fetches without retaining request bodies."""
    driver.execute_script(
        measurement_fetch_install_script("arguments[0]"),
        marker,
    )


def install_measurement_fetch_on_new_document(driver: webdriver.Chrome, marker: str) -> str:
    """Install the same bounded fetch owner before the deterministic app document starts."""
    result = driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": deterministic_measurement_preload_script(marker)},
    )
    identifier = str(result.get("identifier") or "") if isinstance(result, dict) else ""
    if not identifier:
        raise RuntimeError("Chrome did not retain the deterministic measurement preload")
    return identifier


def stop_and_collect_measurement_fetches(driver: webdriver.Chrome, timeout: int = 30) -> dict[str, object]:
    stopped = driver.execute_script(
        "return window.__yolomuxMeasurementFetch ? window.__yolomuxMeasurementFetch.stop() : null"
    )
    if not isinstance(stopped, dict):
        raise RuntimeError("measurement fetch ledger is unavailable")
    settled = WebDriverWait(driver, timeout).until(
        lambda current: current.execute_script(
            "const state = window.__yolomuxMeasurementFetch; const snapshot = state?.snapshot(); return snapshot && !snapshot.active && snapshot.pending.length === 0 ? snapshot : null"
        )
    )
    if not isinstance(settled, dict):
        raise RuntimeError("measurement fetch ledger did not settle")
    return settled


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


def prepare_deterministic_cold_navigation(
    driver: webdriver.Chrome,
    base_url: str,
    port: int,
    username: str | None,
    tmux_sessions: list[str],
    marker: str,
) -> tuple[list[str], str, str]:
    """Install auth and the preload without starting the measured app document."""
    if len(tmux_sessions) != 2 or any(not str(session).strip() for session in tmux_sessions):
        raise RuntimeError("deterministic workload requires two nonempty tmux sessions")
    install_local_auth_cookie(driver, base_url, port, capture_auth_user(username))
    preload_identifier = install_measurement_fetch_on_new_document(driver, marker)
    query = urlencode({
        "sessions": ",".join(tmux_sessions),
        "layout": "row@34(left,row@50(center,right))",
        "tabs": f"left:debug*;center:finder;right:tabber,{tmux_sessions[0]}",
    })
    return tmux_sessions, f"{base_url}/?{query}", preload_identifier


def open_deterministic_cold_navigation(
    driver: webdriver.Chrome,
    app_url: str,
    preload_identifier: str,
    tmux_sessions: list[str],
    timeout: int,
) -> None:
    """Start the one cold app navigation after every measurement owner is armed."""
    try:
        driver.get(app_url)
    finally:
        driver.execute_cdp_cmd("Page.removeScriptToEvaluateOnNewDocument", {"identifier": preload_identifier})
    wait = WebDriverWait(driver, timeout)
    wait.until(lambda current: "/login" not in current.current_url)
    wait_for_app(driver, tmux_sessions, timeout)


def perform_deterministic_fanout_browser_workload(driver: webdriver.Chrome, marker: str, session: str = "") -> dict[str, object]:
    """Run the browser-owned portion of the frozen 0.7.8 fan-out workload."""
    result = driver.execute_async_script(
        r"""
        const marker = arguments[0];
        const repetitions = arguments[1];
        const session = arguments[2];
        const done = arguments[arguments.length - 1];
        (async () => {
          const required = {
            syncServerWatchRootsNow,
            registerApiOperationReceipt,
            apiFetchJson,
            isApiPendingResponse,
            waitForApiOperationResult,
            flushOperationTerminalAcks,
            ensureFileExplorerFilesystemWatchBaseline,
            fetchFilesystemWatchDiff,
            closeClientEventStream,
            scheduleClientEventDisconnectEpisode,
            installClientEventStream,
          };
          for (const [name, value] of Object.entries(required)) {
            if (typeof value !== 'function') throw new Error(`deterministic workload owner is unavailable: ${name}`);
          }

          const rootsGeneration = `${marker}:roots:1`;
          const rootsOptions = {
            force: true,
            immediate: true,
            forceSourceOwner: 'deterministic-watch-roots',
            forceSourceGeneration: rootsGeneration,
          };
          const rootCalls = Array.from({length: repetitions}, () => syncServerWatchRootsNow(rootsOptions));
          await Promise.all(rootCalls.map(request => Promise.resolve(request)));

          if (!session) throw new Error('deterministic session-files operation owner requires an explicit session');
          const awaitPreexistingOperationQuiescence = async () => {
            for (let frame = 0; frame < 600; frame += 1) {
              if (operationTerminalAckState.timer !== null) {
                clearTimeout(operationTerminalAckState.timer);
                operationTerminalAckState.timer = null;
              }
              if (operationTerminalAckState.request) {
                await new Promise(requestAnimationFrame);
                continue;
              }
              if (operationTerminalAckState.pending.size) {
                const capturedAckIds = [...operationTerminalAckState.pending.keys()];
                await flushOperationTerminalAcks();
                const retainedAckIds = capturedAckIds.filter(id => operationTerminalAckState.pending.has(id));
                if (retainedAckIds.length) {
                  throw new Error(`pre-existing operation acknowledgment flush did not retire ${retainedAckIds.join(',')}`);
                }
                continue;
              }
              if (!apiOperationState.pending.size) return;
              await new Promise(requestAnimationFrame);
            }
            throw new Error(`pre-existing operations did not quiesce: operations=${apiOperationState.pending.size} acknowledgments=${operationTerminalAckState.pending.size} request=${Boolean(operationTerminalAckState.request)}`);
          };
          await awaitPreexistingOperationQuiescence();

          // Hold the production 25 ms batch timer while all real terminal cursors arrive. The
          // workload then flushes one exact batch and can prove every durable acknowledgment.
          operationTerminalAckState.timer = -1;
          const sessionFilesUrl = `/api/session-files?session=${encodeURIComponent(session)}&hours=24&force=1`;
          const operationRows = [];
          let ackResponse = null;
          let pendingAckIds = [];
          let unrelatedAckIds = [];
          try {
            for (let index = 0; index < repetitions; index += 1) {
              let pending;
              try {
                await apiFetchJson(sessionFilesUrl, {cache: 'no-store'});
                throw new Error(`session-files operation ${index + 1} did not return an accepted receipt`);
              } catch (error) {
                if (!isApiPendingResponse(error) || error.operation?.kind !== 'session_files' || !error.operationId) throw error;
                pending = error;
              }
              const record = apiOperationState.records.get(pending.operationId);
              const acceptedCursor = {...(pending.operation?.cursor || {})};
              const acceptedPending = record?.phase === 'accepted' && apiOperationState.pending.has(pending.operationId);
              const alreadyTerminal = record?.phase === 'terminal'
                && !apiOperationState.pending.has(pending.operationId)
                && apiOperationState.terminal.has(pending.operationId);
              if (!acceptedPending && !alreadyTerminal) {
                throw new Error(`session-files operation ${pending.operationId} was not accepted`);
              }
              const data = await waitForApiOperationResult(pending, {
                kind: 'session_files',
                url: sessionFilesUrl,
                method: 'GET',
              });
              const terminal = apiOperationState.terminal.get(pending.operationId);
              const terminalCursor = {...(terminal?.operation?.cursor || {})};
              if (!data || typeof data !== 'object' || !terminal || apiOperationState.pending.has(pending.operationId)) {
                throw new Error(`session-files operation ${pending.operationId} did not reach one terminal result`);
              }
              if (!acceptedCursor.epoch || acceptedCursor.epoch !== terminalCursor.epoch
                  || Number(acceptedCursor.seq) !== 0 || Number(terminalCursor.seq) <= Number(acceptedCursor.seq)) {
                throw new Error(`session-files operation ${pending.operationId} changed source generation`);
              }
              operationRows.push({
                id: pending.operationId,
                request_id: String(pending.request?.id || ''),
                accepted_cursor: acceptedCursor,
                terminal_cursor: terminalCursor,
              });
            }
            const operationIds = operationRows.map(row => row.id);
            const requestIds = operationRows.map(row => row.request_id);
            if (new Set(operationIds).size !== repetitions || new Set(requestIds).size !== repetitions || requestIds.some(id => !id)) {
              throw new Error('session-files accepted receipt identities were not one-to-one');
            }
            pendingAckIds = [...operationTerminalAckState.pending.keys()];
            const expectedIds = new Set(operationIds);
            const missingIds = operationIds.filter(id => !operationTerminalAckState.pending.has(id));
            unrelatedAckIds = pendingAckIds.filter(id => !expectedIds.has(id));
            if (missingIds.length) {
              throw new Error(`session-files terminal cursors did not join the pending acknowledgment batch: expected=${operationIds.length} pending=${pendingAckIds.length} missing=${missingIds.join(',')} unrelated=${unrelatedAckIds.join(',')}`);
            }
            operationTerminalAckState.timer = null;
            const originalApiFetchJsonQuiet = apiFetchJsonQuiet;
            apiFetchJsonQuiet = async (...args) => {
              const response = await originalApiFetchJsonQuiet(...args);
              if (String(args[0] || '') === '/api/operations/ack') ackResponse = response;
              return response;
            };
            try {
              await flushOperationTerminalAcks();
            } finally {
              apiFetchJsonQuiet = originalApiFetchJsonQuiet;
            }
            const acknowledged = Array.isArray(ackResponse?.acknowledged) ? ackResponse.acknowledged.map(String) : [];
            const ignored = Array.isArray(ackResponse?.ignored) ? ackResponse.ignored.map(String) : [];
            if (ignored.length || acknowledged.length !== pendingAckIds.length
                || pendingAckIds.some(id => !acknowledged.includes(id) || operationTerminalAckState.pending.has(id))) {
              throw new Error('session-files operation acknowledgment join was not exact');
            }
          } finally {
            if (operationTerminalAckState.timer === -1) operationTerminalAckState.timer = null;
            if (operationTerminalAckState.pending.size) scheduleOperationTerminalAckFlush();
          }
          const operationIds = operationRows.map(row => row.id);

          await Promise.resolve(ensureFileExplorerFilesystemWatchBaseline());
          const watchTokenBefore = String(fileExplorerFilesystemWatchToken || '');
          if (!watchTokenBefore) throw new Error('filesystem watch baseline is unavailable');
          const watchTokens = [];
          for (let index = 0; index < repetitions; index += 1) {
            let payload;
            try {
              payload = await fetchFilesystemWatchDiff({since: watchTokenBefore});
            } catch (error) {
              if (!isApiPendingResponse(error) || !error.operationId) throw error;
              payload = await waitForApiOperationResult(error, {kind: 'fs_watch_diff', url: '/api/fs/watch-diff', method: 'GET'});
            }
            const changed = (Array.isArray(payload?.directories) ? payload.directories.length : 0)
              + (Array.isArray(payload?.removed_roots) ? payload.removed_roots.length : 0);
            if (changed !== 0) throw new Error(`watchd revision ${index + 1} was not unchanged`);
            watchTokens.push(String(payload?.token || watchTokenBefore));
          }

          const priorSource = clientEventTransportState.source;
          const clientEventEpochBefore = String(clientEventTransportState.resourceEpoch || '');
          if (!priorSource || !clientEventTransportState.connected) throw new Error('client EventSource is not connected');
          closeClientEventStream();
          clientEventTransportState.reconnectPending = true;
          const reconnectEpisodeId = Number(clientEventTransportState.nextDisconnectEpisode || 0);
          scheduleClientEventDisconnectEpisode(null);
          clientEventTransportState.demandSignature = '';
          installClientEventStream();
          for (let frame = 0; frame < 600; frame += 1) {
            if (clientEventTransportState.connected && clientEventTransportState.source && clientEventTransportState.source !== priorSource) break;
            await new Promise(requestAnimationFrame);
          }
          if (!clientEventTransportState.connected || !clientEventTransportState.source || clientEventTransportState.source === priorSource) {
            throw new Error('client EventSource reconnect did not complete');
          }

          done({
            ok: true,
            steps: {
              identical_watch_root_renewals: repetitions,
              operation_add_remove_cycles: operationIds.length,
              unchanged_watchd_revisions: watchTokens.length,
              client_event_source_reconnects: 1,
            },
            source_generation_keys: {
              watch_roots: rootsGeneration,
              operation_cycles: operationRows.map(row => ({
                id: row.id,
                epoch: row.accepted_cursor.epoch,
                accepted_seq: row.accepted_cursor.seq,
                terminal_seq: row.terminal_cursor.seq,
              })),
              watchd_revisions: watchTokens,
              client_events: {
                before: clientEventEpochBefore,
                after: String(clientEventTransportState.resourceEpoch || ''),
                recovery_episode_id: reconnectEpisodeId,
              },
            },
            owner_invocations: {
              deterministic_watch_roots: repetitions,
              deterministic_operation_cycle: operationIds.length,
              filesystem_watch_diff: watchTokens.length,
              client_event_transport: 1,
            },
            operation_cycles: {
              route: '/api/session-files',
              receipts: operationRows,
              acknowledgments: ackResponse,
              acknowledgment_batch_ids: pendingAckIds,
              unrelated_acknowledgment_ids: unrelatedAckIds,
            },
          });
        })().catch(error => done({ok: false, error: String(error?.stack || error)}));
        """,
        marker,
        DETERMINISTIC_REPETITIONS,
        session,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        error = result.get("error") if isinstance(result, dict) else "invalid browser result"
        raise RuntimeError(f"deterministic browser workload failed: {error}")
    return result


def capture_deterministic_final_ui_convergence(driver: webdriver.Chrome) -> dict[str, object]:
    """Wait until every browser owner and the rendered metadata model share the final generation."""
    result = driver.execute_async_script(
        r"""
        const done = arguments[arguments.length - 1];
        const snapshot = () => {
          const renderedGeneration = Number(transcriptMetadataState.generation || 0);
          const pendingGeneration = Number(transcriptMetadataState.pendingGeneration || 0);
          const epoch = String(transcriptMetadataState.epoch || '');
          const sourceGeneration = Math.max(renderedGeneration, pendingGeneration);
          const owners = {
            client_event_connected: Boolean(clientEventTransportState.connected && clientEventTransportState.source),
            client_event_candidate: Boolean(clientEventTransportState.replacementSource),
            client_event_reconnect_pending: Boolean(clientEventTransportState.reconnectPending),
            startup_active: Number(startupRefreshApiCoordinator.active || 0),
            startup_queued: Number(startupRefreshApiCoordinator.queue?.length || 0),
            watch_roots_in_flight: Boolean(serverWatchRootsState.inFlight || serverWatchRootsState.request),
            operations_pending: Number(apiOperationState.pending.size),
            operation_waiters: Number(apiOperationState.waiters.size),
            acknowledgments_pending: Number(operationTerminalAckState.pending.size),
            acknowledgment_in_flight: Boolean(operationTerminalAckState.request),
          };
          const source = {epoch, generation: sourceGeneration};
          const rendered = {epoch, generation: renderedGeneration};
          const dom = {grid_connected: Boolean(document.querySelector('#grid')?.isConnected)};
          const settled = Boolean(
            source.epoch
            && source.epoch === String(clientEventTransportState.resourceEpoch || '')
            && source.generation === rendered.generation
            && owners.client_event_connected
            && !owners.client_event_candidate
            && !owners.client_event_reconnect_pending
            && owners.startup_active === 0
            && owners.startup_queued === 0
            && !owners.watch_roots_in_flight
            && owners.operations_pending === 0
            && owners.operation_waiters === 0
            && owners.acknowledgments_pending === 0
            && !owners.acknowledgment_in_flight
            && dom.grid_connected
          );
          return {
            settled,
            source_generation: source,
            rendered_generation: rendered,
            owners,
            dom,
          };
        };
        (async () => {
          let current = snapshot();
          for (let frame = 0; frame < 600 && !current.settled; frame += 1) {
            await new Promise(requestAnimationFrame);
            current = snapshot();
          }
          done(current);
        })().catch(error => done({settled: false, error: String(error?.stack || error)}));
        """
    )
    if not isinstance(result, dict):
        raise RuntimeError("deterministic final UI convergence evidence is malformed")
    return result


def capture_deterministic_browser_diagnostics(driver: webdriver.Chrome) -> dict[str, object]:
    """Project the retained browser diagnostics needed by the release criterion."""
    evidence = assert_browser_local_error_free(driver)
    local_failures = list(evidence.get("browserLocalFailures") or ())
    log_failures = list(evidence.get("browserLogFailures") or ())
    receipt = evidence.get("browserReceiptBarrier")
    return {
        "js_debug_store_reachable": evidence.get("jsDebugStoreReachable") is True,
        "js_debug_event_count": int(evidence.get("jsDebugEventCount") or 0),
        "browser_local_failures": local_failures,
        "browser_log_failures": log_failures,
        "warning_or_error_count": len(local_failures) + len(log_failures),
        "receipt_quiescent": isinstance(receipt, dict) and receipt.get("quiescent") is True,
    }


def validate_deterministic_final_acceptance(
    final_ui: dict[str, object],
    diagnostics: dict[str, object],
) -> dict[str, object]:
    """Fail closed unless the final rendered generation and browser diagnostics are clean."""
    if final_ui.get("settled") is not True:
        raise RuntimeError(f"deterministic final UI did not settle: {final_ui}")
    source_generation = final_ui.get("source_generation")
    rendered_generation = final_ui.get("rendered_generation")
    if not isinstance(source_generation, dict) or source_generation != rendered_generation:
        raise RuntimeError(
            "deterministic final UI does not match the last source generation: "
            f"source={source_generation} rendered={rendered_generation}"
        )
    owners = final_ui.get("owners")
    required_owners = {
        "client_event_connected": True,
        "client_event_candidate": False,
        "client_event_reconnect_pending": False,
        "startup_active": 0,
        "startup_queued": 0,
        "watch_roots_in_flight": False,
        "operations_pending": 0,
        "operation_waiters": 0,
        "acknowledgments_pending": 0,
        "acknowledgment_in_flight": False,
    }
    if not isinstance(owners, dict) or any(owners.get(key) != value for key, value in required_owners.items()):
        raise RuntimeError(f"deterministic final UI owners did not quiesce: {owners}")
    dom = final_ui.get("dom")
    if not isinstance(dom, dict) or dom.get("grid_connected") is not True:
        raise RuntimeError(f"deterministic final UI DOM is unavailable: {dom}")
    if (
        diagnostics.get("js_debug_store_reachable") is not True
        or diagnostics.get("receipt_quiescent") is not True
    ):
        raise RuntimeError(f"deterministic browser diagnostics are not observable or quiescent: {diagnostics}")
    warning_or_error_count = diagnostics.get("warning_or_error_count")
    if (
        warning_or_error_count != 0
        or diagnostics.get("browser_local_failures")
        or diagnostics.get("browser_log_failures")
    ):
        raise RuntimeError(f"deterministic browser emitted unallowlisted Warning/Error records: {diagnostics}")
    return {
        "ui_convergence": dict(final_ui),
        "browser_diagnostics": dict(diagnostics),
    }


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
          await selectSession(yocostItemId, {userInitiated: true});
          done({sessions: [arguments[0], arguments[1]], file: file.dataset.path});
        })().catch(error => done({error: String(error?.stack || error)}));
        """,
        tmux_sessions[0],
        tmux_sessions[1],
    )
    if result.get("error"):
        raise RuntimeError(str(result["error"]))
    # Each range/resolution pair is an async history request. Wait for it before
    # flipping again so a slower earlier request cannot overwrite the final 5m/1s state.
    driver.execute_script("setDebugGraphRange(300); setDebugGraphResolutionOverride(1)")
    wait_for_exact_history(driver, 300, 1)
    driver.execute_script("setDebugGraphRange(1800); setDebugGraphResolutionOverride(10)")
    wait_for_exact_history(driver, 1800, 10)
    driver.execute_script("setDebugGraphRange(300); setDebugGraphResolutionOverride(1)")
    wait_for_exact_history(driver, 300, 1)
    return result


def prepare_idle_yostats_workload(driver: webdriver.Chrome) -> dict[str, object]:
    """Hold the exact live YO!stats state without edits, refreshes, or pane drags."""
    driver.execute_script(
        "setDebugGraphRange(300); setDebugGraphResolutionOverride(1); return selectSession(debugPaneItemId, {userInitiated: true});"
    )
    wait_for_exact_history(driver, 300, 1)
    return WebDriverWait(driver, 20).until(
        lambda current: current.execute_script(
            "return jsDebugStatsPanelVisible() && itemIsActivePaneTab(debugPaneItemId) && document.querySelectorAll('[data-js-debug-graph]').length > 0"
        )
        and {
            "surface": "YO!stats",
            "range_seconds": 300,
            "resolution_seconds": 1,
            "idle": True,
        }
    )


def install_ticker_callback_counter(driver: webdriver.Chrome) -> None:
    """Count only the live-graph scheduler callbacks, never unrelated browser timers."""
    driver.execute_script(
        """
        const prior = window.__yolomuxTickerMeasurement;
        if (prior) prior.restore();
        const names = new Set(['debugGraphLiveFrameTick', 'debugGraphLiveTimerTick']);
        const counter = {requestAnimationFrame: 0, timeout: 0};
        const originalRaf = window.requestAnimationFrame.bind(window);
        const originalTimeout = window.setTimeout.bind(window);
        const named = callback => typeof callback === 'function' && names.has(callback.name);
        window.requestAnimationFrame = callback => originalRaf(timestamp => {
          if (named(callback)) counter.requestAnimationFrame += 1;
          return callback(timestamp);
        });
        window.setTimeout = (callback, delay, ...args) => {
          if (typeof callback !== 'function') return originalTimeout(callback, delay, ...args);
          return originalTimeout((...callbackArgs) => {
            if (named(callback)) counter.timeout += 1;
            return callback(...callbackArgs);
          }, delay, ...args);
        };
        window.__yolomuxTickerMeasurement = {
          snapshot: () => ({...counter, total: counter.requestAnimationFrame + counter.timeout}),
          restore: () => { window.requestAnimationFrame = originalRaf; window.setTimeout = originalTimeout; },
        };
        """
    )


def ticker_callback_counter(driver: webdriver.Chrome) -> dict[str, int] | None:
    return driver.execute_script(
        "return window.__yolomuxTickerMeasurement ? window.__yolomuxTickerMeasurement.snapshot() : null"
    )


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
    return {group["service"]: group["pid"] for group in tracked_local_service_groups(RUNTIME_DIR / "services", table)}


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


def chrome_renderer_cpu_snapshot(chromedriver_pid: int) -> dict[str, object]:
    """Capture only renderer descendants of this tool's temporary Chrome tree."""
    renderer_pids: list[int] = []
    cpu_seconds = 0.0
    for pid in descendants_of(chromedriver_pid):
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", errors="replace")
        except OSError:
            try:
                completed = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "command="],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2.0,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if completed.returncode != 0:
                continue
            command = completed.stdout
        if "--type=renderer" not in command:
            continue
        elapsed = process_cpu_seconds(pid)
        if elapsed is None:
            continue
        renderer_pids.append(pid)
        cpu_seconds += elapsed
    return {"pids": renderer_pids, "cpu_seconds": cpu_seconds}


def restart_managed_watchd_producer(driver: webdriver.Chrome, port: int, timeout: int = 20) -> dict[str, object]:
    """Restart exactly the managed row's proven watchd PID and drive one repair read."""
    if not is_managed_instance_port(port):
        raise RuntimeError("deterministic producer restart requires a managed isolated instance")
    before_pid = int(runtime_service_pids().get("watchd") or 0)
    before_key = process_start_key(before_pid) if before_pid else None
    if not before_pid or before_key is None:
        raise RuntimeError("managed watchd producer identity is unavailable")
    if process_start_key(before_pid) != before_key:
        raise RuntimeError("managed watchd producer identity changed before restart")
    os.kill(before_pid, signal.SIGTERM)
    WebDriverWait(driver, timeout).until(lambda _current: not process_is_alive(before_pid))
    repair = driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        Promise.resolve(refreshFileExplorerFromWatchDiff({full: true}, {full: true}))
          .then(() => done({ok: true}), error => done({ok: false, error: String(error?.stack || error)}));
        """
    )
    if not isinstance(repair, dict) or not repair.get("ok"):
        error = repair.get("error") if isinstance(repair, dict) else "invalid repair result"
        raise RuntimeError(f"watchd producer repair failed: {error}")
    after_pid = int(WebDriverWait(driver, timeout).until(
        lambda _current: (candidate if (candidate := int(runtime_service_pids().get("watchd") or 0)) and candidate != before_pid else False)
    ))
    after_key = process_start_key(after_pid)
    if after_key is None:
        raise RuntimeError("restarted watchd producer identity is unavailable")
    return {
        "service": "watchd",
        "before_pid": before_pid,
        "before_start_key": list(before_key) if isinstance(before_key, tuple) else before_key,
        "after_pid": after_pid,
        "after_start_key": list(after_key) if isinstance(after_key, tuple) else after_key,
        "restarts": 1,
        "source_generation_key": f"watchd:{after_pid}:{after_key}",
    }


def deterministic_profile_command(web_pid: int, duration_seconds: int, output_path: Path) -> list[str]:
    profiler = shutil.which("py-spy")
    if not profiler:
        raise RuntimeError("deterministic workload requires py-spy")
    return [
        profiler,
        "record",
        "--pid",
        str(web_pid),
        "--format",
        "raw",
        "--output",
        str(output_path),
        "--rate",
        str(DETERMINISTIC_PROFILE_RATE_HZ),
        "--duration",
        str(duration_seconds),
        "--threads",
        "--gil",
    ]


def validate_deterministic_profile(
    output_path: Path,
    stderr: str,
    returncode: int,
    command: list[str],
) -> dict[str, object]:
    """Count raw samples and reject the capture above the predeclared error ceiling."""
    try:
        lines = output_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RuntimeError(f"deterministic profiler output is unavailable: {error}") from error
    sample_count = 0
    for line in lines:
        _stack, separator, count_text = line.rpartition(" ")
        if separator and count_text.isdigit():
            sample_count += int(count_text)
    error_lines = [
        line.strip()
        for line in str(stderr or "").splitlines()
        if any(token in line.casefold() for token in ("error", "failed", "permission denied"))
    ]
    error_count = len(error_lines)
    if returncode or error_count > DETERMINISTIC_PROFILE_SAMPLE_ERROR_CEILING or sample_count <= 0:
        raise RuntimeError(
            "deterministic profiler capture is inadmissible: "
            f"returncode={returncode} samples={sample_count} errors={error_count} "
            f"ceiling={DETERMINISTIC_PROFILE_SAMPLE_ERROR_CEILING}"
        )
    return {
        "command": list(command),
        "rate_hz": DETERMINISTIC_PROFILE_RATE_HZ,
        "duration_seconds": int(command[command.index("--duration") + 1]),
        "threads": "--threads" in command,
        "gil_only": "--gil" in command,
        "sample_count": sample_count,
        "sample_error_count": error_count,
        "sample_error_ceiling": DETERMINISTIC_PROFILE_SAMPLE_ERROR_CEILING,
        "admissible": True,
        "raw_output": str(output_path),
    }


def deterministic_owner_counter_snapshot(
    performance: dict[str, object],
    baseline: dict[str, object] | None = None,
) -> dict[str, object]:
    """Retain complete before/after samples and reject absent or reset counters."""

    current_owner_counters = performance.get("owner_counters")
    current_owner_counters = current_owner_counters if isinstance(current_owner_counters, dict) else {}
    baseline_owner_counters = None if baseline is None else baseline.get("owner_counters")
    baseline_owner_counters = baseline_owner_counters if isinstance(baseline_owner_counters, dict) else {}
    missing_before = [
        owner for owner in DETERMINISTIC_OWNER_COUNTER_NAMES
        if not isinstance(baseline_owner_counters.get(owner), int)
        or isinstance(baseline_owner_counters.get(owner), bool)
        or baseline_owner_counters[owner] < 0
    ]
    if missing_before:
        raise RuntimeError(f"deterministic owner counters missing before sample: {missing_before}")
    missing_after = [
        owner for owner in DETERMINISTIC_OWNER_COUNTER_NAMES
        if not isinstance(current_owner_counters.get(owner), int)
        or isinstance(current_owner_counters.get(owner), bool)
        or current_owner_counters[owner] < 0
    ]
    if missing_after:
        raise RuntimeError(f"deterministic owner counters missing after sample: {missing_after}")
    before = {owner: int(baseline_owner_counters[owner]) for owner in DETERMINISTIC_OWNER_COUNTER_NAMES}
    after = {owner: int(current_owner_counters[owner]) for owner in DETERMINISTIC_OWNER_COUNTER_NAMES}
    moved_backwards = [owner for owner in DETERMINISTIC_OWNER_COUNTER_NAMES if after[owner] < before[owner]]
    if moved_backwards:
        raise RuntimeError(f"deterministic owner counters moved backwards: {moved_backwards}")
    raw_sources_before = baseline.get("owner_counter_sources") if baseline is not None else None
    raw_sources_after = performance.get("owner_counter_sources")
    source_samples: dict[str, dict[str, dict[str, int]]] = {"before": {}, "after": {}}
    for boundary, raw_sources in (
        ("before", raw_sources_before),
        ("after", raw_sources_after),
    ):
        for source, names in DETERMINISTIC_OWNER_COUNTER_SOURCES.items():
            payload = raw_sources.get(source) if isinstance(raw_sources, dict) else None
            values: dict[str, int] = {}
            for owner in names:
                value = payload.get(owner) if isinstance(payload, dict) else None
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise RuntimeError(
                        f"deterministic owner counter source missing {boundary} sample: {source}.{owner}"
                    )
                values[owner] = value
            source_samples[boundary][source] = values
    for source, names in DETERMINISTIC_OWNER_COUNTER_SOURCES.items():
        for owner in names:
            before_value = source_samples["before"][source][owner]
            after_value = source_samples["after"][source][owner]
            if after_value < before_value:
                raise RuntimeError(
                    f"deterministic owner counter source moved backwards: {source}.{owner}"
                )
    for boundary, totals in (("before", before), ("after", after)):
        composed = {
            owner: sum(source.get(owner, 0) for source in source_samples[boundary].values())
            for owner in DETERMINISTIC_OWNER_COUNTER_NAMES
        }
        if composed != totals:
            raise RuntimeError(
                f"deterministic owner counter {boundary} totals do not match source components"
            )
    counters = {owner: after[owner] - before[owner] for owner in DETERMINISTIC_OWNER_COUNTER_NAMES}
    return {
        "before": before,
        "after": after,
        "delta": counters,
        "sources": source_samples,
    }


def validate_deterministic_operation_cycle_join(
    browser_workload: dict[str, object],
    repetitions: int = DETERMINISTIC_REPETITIONS,
) -> dict[str, object]:
    """Require one server receipt, terminal cursor, and durable ack for every cycle."""
    cycles = browser_workload.get("operation_cycles")
    if not isinstance(cycles, dict) or cycles.get("route") != "/api/session-files":
        raise RuntimeError("deterministic operation cycles did not use the session-files owner")
    receipts = cycles.get("receipts")
    rows = receipts if isinstance(receipts, list) else []
    if len(rows) != repetitions or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("deterministic operation receipt denominator changed")
    operation_ids = [str(row.get("id") or "") for row in rows]
    request_ids = [str(row.get("request_id") or "") for row in rows]
    if not all(operation_ids) or len(set(operation_ids)) != repetitions:
        raise RuntimeError("deterministic operation ids were not one-to-one")
    if not all(request_ids) or len(set(request_ids)) != repetitions:
        raise RuntimeError("deterministic operation request ids were not one-to-one")
    generation_rows: list[dict[str, object]] = []
    for row in rows:
        accepted = row.get("accepted_cursor")
        terminal = row.get("terminal_cursor")
        if not isinstance(accepted, dict) or not isinstance(terminal, dict):
            raise RuntimeError("deterministic operation cursor evidence is incomplete")
        epoch = str(accepted.get("epoch") or "")
        accepted_seq = accepted.get("seq")
        terminal_seq = terminal.get("seq")
        if (
            not epoch
            or str(terminal.get("epoch") or "") != epoch
            or accepted_seq != 0
            or not isinstance(terminal_seq, int)
            or isinstance(terminal_seq, bool)
            or terminal_seq <= accepted_seq
        ):
            raise RuntimeError("deterministic operation source generation changed before terminal delivery")
        generation_rows.append({
            "id": str(row["id"]),
            "epoch": epoch,
            "accepted_seq": accepted_seq,
            "terminal_seq": terminal_seq,
        })
    acknowledgments = cycles.get("acknowledgments")
    acknowledged = acknowledgments.get("acknowledged") if isinstance(acknowledgments, dict) else None
    ignored = acknowledgments.get("ignored") if isinstance(acknowledgments, dict) else None
    acknowledged_ids = [str(value) for value in acknowledged] if isinstance(acknowledged, list) else []
    batch = cycles.get("acknowledgment_batch_ids")
    batch_ids = [str(value) for value in batch] if isinstance(batch, list) else []
    unrelated = cycles.get("unrelated_acknowledgment_ids")
    unrelated_ids = [str(value) for value in unrelated] if isinstance(unrelated, list) else []
    expected_unrelated = [operation_id for operation_id in batch_ids if operation_id not in set(operation_ids)]
    if (
        ignored != []
        or len(batch_ids) != len(set(batch_ids))
        or set(operation_ids) - set(batch_ids)
        or acknowledged_ids != batch_ids
        or unrelated_ids != expected_unrelated
    ):
        raise RuntimeError("deterministic operation acknowledgment join was not exact")
    source_keys = browser_workload.get("source_generation_keys")
    operation_generations = source_keys.get("operation_cycles") if isinstance(source_keys, dict) else None
    if operation_generations != generation_rows:
        raise RuntimeError("deterministic operation source-generation evidence did not join the receipts")
    return {
        "accepted": repetitions,
        "terminal": repetitions,
        "acknowledged": repetitions,
        "batch_acknowledged": len(batch_ids),
        "unrelated_acknowledged": len(unrelated_ids),
        "ignored": 0,
        "operation_ids": operation_ids,
        "request_ids": request_ids,
        "source_generations": generation_rows,
    }


def validate_deterministic_browser_fanout(request_join: dict[str, object]) -> dict[str, object]:
    """Fail closed unless startup/workload fan-out and client-event identity stay bounded."""
    ledger = request_join.get("browser_ledger")
    if not isinstance(ledger, dict):
        raise RuntimeError("deterministic browser fan-out evidence is missing")
    max_concurrent = ledger.get("max_concurrent_api_fetches")
    if (
        not isinstance(max_concurrent, int)
        or isinstance(max_concurrent, bool)
        or max_concurrent < 1
        or max_concurrent > 8
    ):
        raise RuntimeError(
            "deterministic API concurrency exceeded eight or was unmeasured: "
            f"{max_concurrent}; peak={ledger.get('peak_api_fetches')}"
        )
    fs_batch_requests = ledger.get("fs_batch_requests")
    batches = fs_batch_requests if isinstance(fs_batch_requests, list) else []
    if not batches or any(not isinstance(item, dict) for item in batches):
        raise RuntimeError("deterministic filesystem batch cardinality evidence is missing")
    item_counts = [item.get("item_count") for item in batches]
    if any(
        not isinstance(count, int) or isinstance(count, bool) or count < 1 or count > 64
        for count in item_counts
    ):
        raise RuntimeError(f"deterministic filesystem batch cardinality exceeded 64 or was unmeasured: {item_counts}")
    event_sources = ledger.get("event_sources")
    if not isinstance(event_sources, dict):
        raise RuntimeError("deterministic client EventSource identity evidence is missing")
    created = event_sources.get("created")
    closed = event_sources.get("closed")
    live = event_sources.get("live")
    if not all(isinstance(items, list) for items in (created, closed, live)):
        raise RuntimeError("deterministic client EventSource identity evidence is invalid")
    created_ids = [str(value) for value in created]
    closed_ids = [str(value) for value in closed]
    live_ids = [str(value) for value in live]
    if (
        len(created_ids) != 2
        or len(set(created_ids)) != 2
        or closed_ids != [created_ids[0]]
        or live_ids != [created_ids[1]]
        or event_sources.get("replacements") != 1
        or event_sources.get("max_live") != 1
    ):
        raise RuntimeError(
            "deterministic client EventSource was not one live identity with one deliberate replacement: "
            f"created={created_ids} closed={closed_ids} live={live_ids} "
            f"replacements={event_sources.get('replacements')} max_live={event_sources.get('max_live')}"
        )
    return {
        "max_concurrent_api_fetches": max_concurrent,
        "api_concurrency_limit": 8,
        "fs_batch_request_count": len(batches),
        "fs_batch_max_item_count": max(item_counts),
        "fs_batch_item_limit": 64,
        "event_sources": {
            "created": created_ids,
            "closed": closed_ids,
            "live": live_ids,
            "max_live": 1,
            "replacements": 1,
        },
    }


def deterministic_measurement_schema(
    contract: dict[str, object],
    browser_workload: dict[str, object],
    producer_restart: dict[str, object],
    request_join: dict[str, object],
    performance: dict[str, object],
    profiler: dict[str, object],
    elapsed_seconds: float,
    *,
    performance_before: dict[str, object] | None = None,
) -> dict[str, object]:
    expected_steps = dict(contract["steps"])
    observed_steps = {
        "authenticated_cold_load": 1,
        **dict(browser_workload.get("steps") or {}),
        "producer_restarts": int(producer_restart.get("restarts") or 0),
    }
    if observed_steps != expected_steps:
        raise RuntimeError(f"deterministic workload denominator changed: expected={expected_steps} observed={observed_steps}")
    join = request_join.get("join")
    if not isinstance(join, dict) or any(join.get(field) for field in ("missing", "duplicate", "unexpected")):
        raise RuntimeError("deterministic workload request join is incomplete")
    operation_cycle_join = validate_deterministic_operation_cycle_join(browser_workload)
    browser_fanout = validate_deterministic_browser_fanout(request_join)
    issued = request_join.get("issued")
    entries = issued if isinstance(issued, list) else []
    request_ids_by_route: dict[str, list[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        route = f"{str(entry.get('method') or '').upper()} {str(entry.get('path') or '')}"
        request_ids_by_route.setdefault(route, []).append(str(entry.get("request_id") or ""))
    owner_counters = deterministic_owner_counter_snapshot(performance, performance_before)
    return {
        "contract": contract,
        "observed_steps": observed_steps,
        "elapsed_seconds": round(max(0.0, elapsed_seconds), 3),
        "exact_request_ids_by_route": request_ids_by_route,
        "operation_cycle_join": operation_cycle_join,
        "browser_fanout": browser_fanout,
        "source_generation_keys": {
            **dict(browser_workload.get("source_generation_keys") or {}),
            "producer_restart": producer_restart.get("source_generation_key"),
        },
        "owner_invocations": {
            **dict(browser_workload.get("owner_invocations") or {}),
            "watchd": int(producer_restart.get("restarts") or 0),
            **dict(owner_counters["delta"]),
        },
        "owner_counter_samples": owner_counters,
        "profiler": dict(profiler),
    }


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


def bounded_driver_quit(
    driver: webdriver.Chrome,
    quit_timeout: float = 15.0,
    *,
    identity_fn=process_start_key,
    signal_fn=None,
) -> None:
    """Quit the browser through the one shared lease, then proof-sweep any orphan renderer subtree.

    The chromedriver process is retired by the single WebDriverLease owner - bounded quit -> TERM ->
    KILL -> reap -> final proof - so a wedged chromedriver never leaves this tool the next unbounded
    orphan owner, and the lease never signals a PID it cannot prove is still its own. Killing
    chromedriver normally reaps its Chrome children, but a wedged chromedriver can orphan the renderer
    subtree; we capture each descendant's immutable start-key proof BEFORE the retirement and SIGKILL
    only those we can still prove are the exact processes we owned. A reused or reparented PID reads a
    different key (or none) and is left untouched - the same reuse/reparent guard the lease applies to
    the chromedriver PID.
    """
    kill = os.kill if signal_fn is None else signal_fn
    service_process = getattr(getattr(driver, "service", None), "process", None)
    chromedriver_pid = int(service_process.pid) if service_process is not None else 0
    # Capture the renderer subtree's proofs up front, before retirement can reparent or exit them.
    descendant_proofs = {pid: identity_fn(pid) for pid in descendants_of(chromedriver_pid)} if chromedriver_pid else {}
    WebDriverLease.from_driver(driver, quit_timeout=quit_timeout, identity_fn=identity_fn, signal_fn=kill).retire()
    for pid, captured in descendant_proofs.items():
        if captured is None:
            continue
        current = identity_fn(pid)
        if current is None or current != captured:
            continue  # exited, or the PID was reused: never signal an unproved process
        try:
            kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            continue


def main() -> int:
    args = parse_args()
    chrome = find_chrome()
    if not chrome:
        print("error: Chrome/Chromium is not installed", file=sys.stderr)
        return 2
    if not output_path_is_under_tmp(args.output):
        print("error: output must be under /tmp", file=sys.stderr)
        return 2
    base_url = f"https://localhost:{args.port}"
    web_pid = unique_listener_pid(args.port, timeout_seconds=2.0)
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
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    driver = webdriver.Chrome(options=options)
    driver_process = getattr(getattr(driver, "service", None), "process", None)
    chromedriver_pid = int(driver_process.pid) if driver_process is not None else 0
    if not chromedriver_pid:
        raise RuntimeError("Chrome renderer capture requires a chromedriver PID")
    measurement_marker = f"capture-{uuid.uuid4().hex}"
    # Client-side Selenium timeouts: a wedged server or chromedriver must
    # surface as an exception that reaches cleanup, never an infinite block.
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(60)
    process: subprocess.Popen[str] | None = None
    profiler_process: subprocess.Popen[str] | None = None
    allowed_service_changes: set[str] = set()
    cleaned = threading.Event()

    def cleanup() -> None:
        if cleaned.is_set():
            return
        cleaned.set()
        stop_benchmark_group(process)
        stop_benchmark_group(profiler_process)
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
    watchdog = GroupOverloadWatchdog(port=args.port, state_dir=RUNTIME_DIR, service_dir=RUNTIME_DIR / "services")
    watchdog_thread = threading.Thread(target=watchdog.run, args=(float(args.duration) + 120.0,), daemon=True)
    watchdog_thread.start()
    try:
        deterministic_contract = deterministic_fanout_workload_contract(args.duration) if args.workload == "deterministic-fanout" else None
        deterministic_navigation: tuple[str, str] | None = None
        if args.workload == "deterministic-fanout":
            tmux_sessions, app_url, preload_identifier = prepare_deterministic_cold_navigation(
                driver,
                base_url,
                args.port,
                args.username,
                list(args.session),
                measurement_marker,
            )
            deterministic_navigation = (app_url, preload_identifier)
        else:
            tmux_sessions = authenticate_and_open(driver, base_url, args.port, args.username, timeout=20)
        if args.workload == "idle-yostats":
            workload = prepare_idle_yostats_workload(driver)
            install_ticker_callback_counter(driver)
        else:
            workload = None
        if args.workload != "deterministic-fanout":
            install_measurement_fetch_header(driver, measurement_marker)
            driver.execute_script("clearClientPerfCounters(); performance.clearResourceTimings()")
        measurement_before = (
            wait_for_deterministic_measurement_baseline(driver)
            if args.workload == "deterministic-fanout"
            else capture_measurement_metrics(driver)
        )
        renderer_before = chrome_renderer_cpu_snapshot(chromedriver_pid)
        benchmark_output = args.output.with_name(f"{args.output.stem}-contention.json")
        command = [sys.executable, str(REPO_ROOT / "tools" / "yostats_contention_benchmark.py"), "--web-pid", str(web_pid), "--duration", str(args.duration), "--output", str(benchmark_output)]
        if indexd_pid:
            command.extend(["--indexer-pid", str(indexd_pid)])
        if statsd_pid:
            command.extend(["--statsd-pid", str(statsd_pid)])
        # Own process group so a driver exception or signal can stop the whole
        # benchmark subtree without touching the dev stack's services.
        process = subprocess.Popen(command, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        profiler_command = None
        profiler_output = args.output.with_name(f"{args.output.stem}-py-spy.raw")
        if args.workload == "deterministic-fanout":
            profiler_command = deterministic_profile_command(web_pid, args.duration, profiler_output)
            profiler_process = subprocess.Popen(
                profiler_command,
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        workload_started = time.monotonic()
        if args.workload == "active":
            workload = perform_workload(driver, tmux_sessions)
            workload["drag"] = drag_yocost_pane(driver)
        elif args.workload == "deterministic-fanout":
            if deterministic_navigation is None:
                raise RuntimeError("deterministic cold navigation was not prepared")
            app_url, preload_identifier = deterministic_navigation
            # Authentication/setup navigation is outside the frozen cold-load denominator. Drain
            # its console records so the final gate covers exactly the cold load and workload.
            read_browser_console_log(driver)
            open_deterministic_cold_navigation(
                driver,
                app_url,
                preload_identifier,
                tmux_sessions,
                timeout=20,
            )
            workload = perform_deterministic_fanout_browser_workload(driver, measurement_marker, tmux_sessions[0])
            workload["sessions"] = list(tmux_sessions)
            workload["producer_restart"] = restart_managed_watchd_producer(driver, args.port)
            allowed_service_changes.add("watchd")
        workload_elapsed = time.monotonic() - workload_started
        stdout, stderr = process.communicate(timeout=args.duration + 20)
        if process.returncode:
            raise RuntimeError(f"contention benchmark failed: {stderr.strip() or stdout.strip()}")
        profiler = None
        if profiler_process is not None and profiler_command is not None:
            _profiler_stdout, profiler_stderr = profiler_process.communicate(timeout=args.duration + 20)
            profiler = validate_deterministic_profile(
                profiler_output,
                profiler_stderr,
                int(profiler_process.returncode or 0),
                profiler_command,
            )
        issued_requests = stop_and_collect_measurement_fetches(driver)
        browser = driver.execute_script("return {longTasks: clientPerfLongTaskSummary(), perf: clientPerfSummary(), resources: performance.getEntriesByType('resource').map(entry => ({name: entry.name, duration: entry.duration, transferSize: entry.transferSize}))}")
        browser["resources"] = bounded_api_resources(browser.get("resources", []))
        measurement_after = (
            wait_for_deterministic_measurement_baseline(driver)
            if args.workload == "deterministic-fanout"
            else capture_measurement_metrics(driver)
        )
        measurement_run = validate_capture_request_join(measurement_marker, issued_requests, measurement_after)
        renderer_after = chrome_renderer_cpu_snapshot(chromedriver_pid)
        renderer_delta = max(0.0, float(renderer_after["cpu_seconds"]) - float(renderer_before["cpu_seconds"]))
        renderer = {
            "before": renderer_before,
            "after": renderer_after,
            "cpu_seconds_delta": renderer_delta,
            "cpu_percent_of_one_core": renderer_delta / args.duration * 100.0,
        }
        ticker_callbacks = ticker_callback_counter(driver) if args.workload == "idle-yostats" else None
        deterministic = None
        if deterministic_contract is not None and profiler is not None and isinstance(workload, dict):
            final_acceptance = validate_deterministic_final_acceptance(
                capture_deterministic_final_ui_convergence(driver),
                capture_deterministic_browser_diagnostics(driver),
            )
            deterministic = deterministic_measurement_schema(
                deterministic_contract,
                workload,
                dict(workload.get("producer_restart") or {}),
                measurement_run,
                measurement_after,
                profiler,
                workload_elapsed,
                performance_before=measurement_before,
            )
            deterministic["final_acceptance"] = final_acceptance
        args.output.write_text(json.dumps({"version": 6, "base_url": base_url, "duration_seconds": args.duration, "workload_mode": args.workload, "workload": workload, "ticker_callbacks": ticker_callbacks, "browser": browser, "renderer": renderer, "measurement": {"before": measurement_before, "after": measurement_after, "run": measurement_run}, "deterministic": deterministic, "contention": str(benchmark_output)}, sort_keys=True) + "\n", encoding="utf-8")
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
        changed = {
            name: (ledger_before.get(name), pid)
            for name, pid in ledger_after.items()
            if ledger_before.get(name) != pid and name not in allowed_service_changes
        }
        if changed:
            print(f"error: capture changed the service ledger: {changed}", file=sys.stderr)
        elif vanished:
            print(f"note: service(s) {vanished} exited via their own idle lifecycle during the capture (not capture-caused)", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
