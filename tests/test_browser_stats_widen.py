# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Retained YO!stats range-transition regressions."""

import copy
import json
import os
from pathlib import Path
import subprocess
import ssl
import shutil
import sqlite3
import sys
import time
import urllib.request
from http import HTTPStatus
from typing import Any

import pytest
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.common.exceptions import WebDriverException

from tests.browser_helpers.browser_console import acknowledge_and_consume_only_expected_js_debug_failures
from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.gate_harness import wait_for_fixture_api_quiescence
from tests.gate_harness import HttpPortLease
from yolomux_lib.stats_current import http as stats_current_http
from yolomux_lib.stats_current import storage
from tests.tmux_runtime import stop_isolated_tmux_runtime
from tests.tmux_runtime import start_isolated_tmux_runtime
from tools.verify_go_agent_status_live import copy_live_jsonl
from tools.verify_go_agent_status_live import make_opencode_database
from tools.instance_isolation import synthetic_test_environment


pytest_plugins = ("tests.e2e_browser_harness",)
pytestmark = [pytest.mark.browser, pytest.mark.socket, pytest.mark.e2e]

CPU_GRAPH_SAMPLE_COUNT = 8
CPU_GRAPH_PERCENT = 37.0
GO_STATS_SERIES = {
    "activity": [
        ("ask_agents", "askAgents"),
        ("run_agents", "workingAgents"),
        ("transition_agents", "transitionAgents"),
        ("idle_agents", "idleAgents"),
    ],
    "agentTokens": [("agent_tokens_per_minute:go-agent", "agentToken:go-agent")],
    "memory": [
        ("system_memory_used_bytes", "systemMemory"),
        ("process_memory_bytes:python", "memory:python"),
    ],
}
GO_STATS_SNAPSHOT_SERIES = tuple(
    snapshot_name
    for names in GO_STATS_SERIES.values()
    for snapshot_name, _dom_name in names
) + ("system_memory_capacity_bytes", "system_cpu_percent")

_PRODUCT_ROOT_ENV_KEYS = (
    "HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
    "TMPDIR",
    "YOLOMUX_ROOT",
    "YOLOMUX_RUNTIME_DIR",
    "YOLOMUX_CONFIG_DIR",
    "YOLOMUX_STATE_DIR",
    "YOLOMUX_CACHE_DIR",
    "YOLOMUX_CODEX_HOME",
    "YOLOMUX_HOST_ARTIFACT_DIR",
    "YOLOMUX_START_LOCK_DIR",
    "YOLOMUX_LOG_DIR",
    "YOLOMUX_CA_DIR",
    "YOLOMUX_TOOL_LOCK_PATH",
    "PYTHONPYCACHEPREFIX",
    "YOLOMUX_OPENCODE_DATABASE",
    "YOLOMUX_CLAUDE_TRANSCRIPT_ROOT",
    "YOLOMUX_CODEX_TRANSCRIPT_ROOT",
)


def _synthetic_server_environment() -> dict[str, str]:
    """Return an environment with no ambient product or client-data roots."""
    return synthetic_test_environment()


def _seed_go_stats_fixture(database: Path) -> None:
    """Seed non-browser facts through the Go writer's supported SQLite schema."""
    now = int(time.time())
    database.parent.mkdir(parents=True, exist_ok=True)
    observations = []
    for index in range(3):
        observed_at = now - 120 + index * 60
        observations.extend(
            (
                (
                    f"go-cpu-{index}",
                    "cpu",
                    "host",
                    observed_at,
                    "go-cpu-epoch",
                    1,
                    json.dumps({"process_percent": 18 + index, "system_percent": 37}, separators=(",", ":")),
                ),
                (
                    f"go-status-{index}",
                    "agent_status",
                    "host",
                    observed_at,
                    "go-agent-epoch",
                    1,
                    json.dumps(
                        {
                            "states": {
                                "go-ask": "ask",
                                "go-run": "run",
                                "go-transition": "transition",
                                "go-idle": "idle",
                            },
                            "session_states": {"go-session": "run"},
                            "snapshot_revision": index + 1,
                        },
                        separators=(",", ":"),
                    ),
                ),
                (
                    f"go-memory-{index}",
                    "system_memory",
                    "host",
                    observed_at,
                    "go-memory-epoch",
                    1,
                    json.dumps(
                        {
                            "used_bytes": 4_000_000_000 + index * 100_000_000,
                            "capacity_bytes": 8_000_000_000,
                            "process_memory_bytes": {"python": 700_000_000 + index * 10_000_000},
                        },
                        separators=(",", ":"),
                    ),
                ),
            )
        )
    atoms = [
        (
            f"go-input-{index}",
            "input",
            "text",
            "none",
            "tokens",
            now - 120 + index * 60,
            json.dumps(
                {
                    "quantity": 600 + index,
                    "agent_id": "go-agent",
                    "model": "go-model",
                    "provider": "openai",
                    "telemetry_complete": True,
                },
                separators=(",", ":"),
            ),
        )
        for index in range(3)
    ] + [
        (
            f"go-output-{index}",
            "output",
            "text",
            "none",
            "tokens",
            now - 120 + index * 60,
            json.dumps(
                {
                    "quantity": 300 + index,
                    "agent_id": "go-agent",
                    "model": "go-model",
                    "provider": "openai",
                    "telemetry_complete": True,
                },
                separators=(",", ":"),
            ),
        )
        for index in range(3)
    ]
    coverage = [
        ("cpu", "host", "go-cpu-epoch", now - 180, None, 1, 1),
        ("agent_status", "host", "go-agent-epoch", now - 180, None, 60, 1),
        ("system_memory", "host", "go-memory-epoch", now - 180, None, 60, 1),
    ]
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            PRAGMA application_id = 0x594F5354;
            PRAGMA user_version = 9;
            CREATE TABLE schema_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), minimum_writer_protocol INTEGER NOT NULL, minimum_writer_build INTEGER NOT NULL, source_generation INTEGER NOT NULL, last_vacuumed_at REAL NOT NULL DEFAULT 0);
            INSERT INTO schema_meta VALUES (1, 25, 8, 0, 0);
            CREATE TABLE observations(event_id TEXT NOT NULL,family TEXT NOT NULL,source_id TEXT NOT NULL,observed_at REAL NOT NULL,epoch_id TEXT NOT NULL,owner_generation INTEGER NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(family,source_id,event_id)) WITHOUT ROWID;
            CREATE INDEX observations_time ON observations(observed_at);
            CREATE TABLE coverage_epochs(family TEXT NOT NULL,source_id TEXT NOT NULL,epoch_id TEXT NOT NULL,started_at REAL NOT NULL,ended_at REAL,native_cadence_seconds REAL NOT NULL,owner_generation INTEGER NOT NULL,PRIMARY KEY(family,source_id,epoch_id)) WITHOUT ROWID;
            CREATE INDEX coverage_epochs_end ON coverage_epochs(ended_at);
            CREATE TABLE unavailable_spans(family TEXT NOT NULL,source_id TEXT NOT NULL,epoch_id TEXT NOT NULL,started_at REAL NOT NULL,ended_at REAL NOT NULL,native_cadence_seconds REAL NOT NULL,reason TEXT NOT NULL,owner_generation INTEGER NOT NULL,PRIMARY KEY(family,source_id,epoch_id,started_at,ended_at)) WITHOUT ROWID;
            CREATE INDEX unavailable_spans_end ON unavailable_spans(ended_at);
            CREATE TABLE usage_atoms(event_id TEXT NOT NULL,direction TEXT NOT NULL,modality TEXT NOT NULL,cache_role TEXT NOT NULL,unit TEXT NOT NULL,observed_at REAL NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(event_id,direction,modality,cache_role,unit)) WITHOUT ROWID;
            CREATE INDEX usage_atoms_time ON usage_atoms(observed_at);
            CREATE TABLE migration_reconciliation(migration_id TEXT PRIMARY KEY,completed_at REAL NOT NULL,source_digest TEXT NOT NULL,details_json TEXT NOT NULL) WITHOUT ROWID;
            CREATE TABLE aggregate_publication(singleton INTEGER PRIMARY KEY CHECK(singleton=1),ring_generation INTEGER NOT NULL,source_generation INTEGER NOT NULL,published_at REAL NOT NULL);
            CREATE TABLE aggregate_rings(resolution_seconds INTEGER PRIMARY KEY,slot_count INTEGER NOT NULL CHECK(slot_count>0),newest_bucket_start INTEGER,CHECK(resolution_seconds>0),CHECK(newest_bucket_start IS NULL OR newest_bucket_start>=0),CHECK(newest_bucket_start IS NULL OR newest_bucket_start % resolution_seconds=0)) WITHOUT ROWID;
            CREATE TABLE aggregate_ring_slots(resolution_seconds INTEGER NOT NULL,slot_index INTEGER NOT NULL CHECK(slot_index>=0),bucket_start INTEGER,bucket_json BLOB,complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0,1)),source_generation INTEGER NOT NULL DEFAULT 0 CHECK(source_generation>=0),ring_generation INTEGER NOT NULL DEFAULT 0 CHECK(ring_generation>=0),published_at REAL NOT NULL DEFAULT 0 CHECK(published_at>=0),payload_version INTEGER NOT NULL DEFAULT 0 CHECK(payload_version>=0),PRIMARY KEY(resolution_seconds,slot_index),FOREIGN KEY(resolution_seconds) REFERENCES aggregate_rings(resolution_seconds),CHECK(bucket_start IS NULL OR bucket_start>=0),CHECK(bucket_start IS NULL OR bucket_start % resolution_seconds=0),CHECK((bucket_start IS NULL AND bucket_json IS NULL AND complete=0 AND source_generation=0 AND ring_generation=0 AND published_at=0 AND payload_version=0) OR (bucket_start IS NOT NULL AND bucket_json IS NOT NULL AND payload_version>0))) WITHOUT ROWID;
            CREATE TABLE ring_replay_cursor(resolution_seconds INTEGER PRIMARY KEY,folded_through_observed_at REAL NOT NULL DEFAULT 0 CHECK(folded_through_observed_at>=0),folded_source_generation INTEGER NOT NULL DEFAULT 0 CHECK(folded_source_generation>=0),updated_at REAL NOT NULL DEFAULT 0 CHECK(updated_at>=0),FOREIGN KEY(resolution_seconds) REFERENCES aggregate_rings(resolution_seconds)) WITHOUT ROWID;
            CREATE TABLE ring_invalidations(resolution_seconds INTEGER NOT NULL,bucket_start INTEGER NOT NULL CHECK(bucket_start>=0),source_generation INTEGER NOT NULL CHECK(source_generation>=0),reason TEXT NOT NULL,created_at REAL NOT NULL CHECK(created_at>=0),applied_at REAL,PRIMARY KEY(resolution_seconds,bucket_start,source_generation),FOREIGN KEY(resolution_seconds) REFERENCES aggregate_rings(resolution_seconds),CHECK(bucket_start % resolution_seconds=0),CHECK(applied_at IS NULL OR applied_at>=0)) WITHOUT ROWID;
            INSERT INTO aggregate_publication VALUES (1, 0, 0, 0);
            """
        )
        for resolution, capacity in ((1, 300), (10, 180), (60, 480), (300, 288)):
            connection.execute("INSERT INTO aggregate_rings VALUES (?, ?, NULL)", (resolution, capacity))
            connection.execute("INSERT INTO ring_replay_cursor VALUES (?, 0, 0, 0)", (resolution,))
            connection.executemany(
                "INSERT INTO aggregate_ring_slots(resolution_seconds, slot_index) VALUES (?, ?)",
                ((resolution, slot) for slot in range(capacity)),
            )
        connection.executescript(
            """
            CREATE TRIGGER aggregate_publication_reject_insert BEFORE INSERT ON aggregate_publication BEGIN SELECT RAISE(ABORT, 'aggregate ring rows are fixed'); END;
            CREATE TRIGGER aggregate_publication_reject_delete BEFORE DELETE ON aggregate_publication BEGIN SELECT RAISE(ABORT, 'aggregate ring rows are fixed'); END;
            CREATE TRIGGER aggregate_rings_reject_insert BEFORE INSERT ON aggregate_rings BEGIN SELECT RAISE(ABORT, 'aggregate ring rows are fixed'); END;
            CREATE TRIGGER aggregate_rings_reject_delete BEFORE DELETE ON aggregate_rings BEGIN SELECT RAISE(ABORT, 'aggregate ring rows are fixed'); END;
            CREATE TRIGGER aggregate_ring_slots_reject_insert BEFORE INSERT ON aggregate_ring_slots BEGIN SELECT RAISE(ABORT, 'aggregate ring rows are fixed'); END;
            CREATE TRIGGER aggregate_ring_slots_reject_delete BEFORE DELETE ON aggregate_ring_slots BEGIN SELECT RAISE(ABORT, 'aggregate ring rows are fixed'); END;
            """
        )
        connection.executemany("INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?)", observations)
        connection.executemany("INSERT INTO usage_atoms VALUES (?, ?, ?, ?, ?, ?, ?)", atoms)
        connection.executemany("INSERT INTO coverage_epochs VALUES (?, ?, ?, ?, ?, ?, ?)", coverage)
        connection.execute("UPDATE schema_meta SET source_generation = 1 WHERE singleton = 1")
        reconciliation = json.dumps(
            {
                "format": 1,
                "counts": {
                    "observations": len(observations),
                    "coverage_epochs": len(coverage),
                    "usage_atoms": len(atoms),
                    "unavailable_spans": 0,
                },
                "issues": [],
                "issue_counts": {},
                "issues_truncated": 0,
                "sources": [],
            },
            separators=(",", ":"),
        )
        connection.execute(
            "INSERT INTO migration_reconciliation(migration_id, completed_at, source_digest, details_json) VALUES (?, ?, ?, ?)",
            ("stats-v5-current-only-v1", float(now), "0" * 64, reconciliation),
        )
        connection.commit()


def _prepare_go_collector_sources(root: Path) -> tuple[Path, Path, Path]:
    """Create current-window Claude, Codex, and OpenCode inputs for live collectors."""
    source_root = Path(__file__).resolve().parents[1]
    now = time.time()
    home = root / "home"
    (home / "claude" / "projects").mkdir(parents=True)
    (home / "codex" / "sessions").mkdir(parents=True)
    (home / ".GlobalProtect").mkdir()
    copy_live_jsonl(
        source_root / "tests/fixtures/current_model_usage/observed.claude.jsonl",
        home / "claude" / "projects" / "live.jsonl",
        now,
    )
    copy_live_jsonl(
        source_root / "tests/fixtures/current_model_usage/observed.codex.jsonl",
        home / "codex" / "sessions" / "live.jsonl",
        now,
    )
    database = root / "opencode.db"
    make_opencode_database(database, int(now * 1000))
    interpreter = root / "opencode"
    shutil.copy(Path(sys.executable), interpreter)
    interpreter.chmod(0o700)
    return home, database, interpreter

_STATS_ROUTE_INSTRUMENTATION = r"""
(() => {
  if (globalThis.__yolomuxStatsRouteCounts) return;
  const tracked = new Set([
    '/api/stats-capabilities', '/api/stats-snapshot', '/api/stats-retry', '/api/stats-stream',
  ]);
  const unrelatedTracked = new Set([
    '/api/settings', '/api/session-metadata', '/api/background/status', '/api/auto-approve',
    '/api/fs/index-status', '/api/watched-prs', '/api/update-status', '/api/chat/bootstrap',
    '/api/transcripts', '/api/activity',
  ]);
  const counts = Object.fromEntries([...tracked].map(path => [path, 0]));
  const unrelatedCounts = Object.fromEntries([...unrelatedTracked].map(path => [path, 0]));
  globalThis.__yolomuxStatsRouteCounts = counts;
  globalThis.__yolomuxStatsUnrelatedRouteCounts = unrelatedCounts;
  globalThis.__yolomuxStatsStreamEvents = [];
  const count = input => {
    try {
      const url = new URL(typeof input === 'string' ? input : input?.url || '', location.href);
      if (tracked.has(url.pathname)) counts[url.pathname] += 1;
      if (unrelatedTracked.has(url.pathname)) unrelatedCounts[url.pathname] += 1;
    } catch (_) {}
  };
  const nativeFetch = globalThis.fetch;
  if (typeof nativeFetch === 'function') {
    globalThis.fetch = function(input, ...rest) {
      count(input);
      return nativeFetch.call(this, input, ...rest);
    };
  }
  const NativeEventSource = globalThis.EventSource;
  if (typeof NativeEventSource === 'function') {
    globalThis.EventSource = new Proxy(NativeEventSource, {
      construct(target, args) {
        count(args[0]);
        const source = Reflect.construct(target, args, target);
        let path = '';
        try { path = new URL(String(args[0] || ''), location.href).pathname; } catch (_) {}
        if (path !== '/api/stats-stream') return source;
        for (const eventName of ['ack', 'snapshot', 'ready', 'delta', 'unavailable', 'error']) {
          source.addEventListener(eventName, event => {
            globalThis.__yolomuxStatsStreamEvents.push({
              event: eventName,
              readyState: source.readyState,
              data: String(event?.data || '').slice(0, 240),
            });
            if (globalThis.__yolomuxStatsStreamEvents.length > 64) globalThis.__yolomuxStatsStreamEvents.shift();
          });
        }
        return source;
      },
    });
  }
})();
"""

def test_stats_area_legend_key_is_a_thick_block(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug&layout=left&tabs=left:__debug__")
    WebDriverWait(browser, 8).until(
        lambda driver: driver.execute_script("return typeof debugGraphLegendSwatchHtml === 'function'")
    )
    result = browser.execute_script(
        """
        const probe = document.createElement('div');
        probe.style.cssText = 'display:flex;align-items:center;gap:8px';
        probe.innerHTML = debugGraphLegendSwatchHtml({key: 'memory', hostMetric: 'memory', hostProcessId: 'python'}, 'area')
          + debugGraphLegendSwatchHtml({key: 'latency', clientMetric: true}, 'line');
        document.body.append(probe);
        const area = probe.querySelector('.js-debug-legend-area');
        const lineSvg = probe.querySelector('.js-debug-legend-line');
        const line = lineSvg?.querySelector('line');
        const result = {
          areaTag: area?.tagName || '',
          areaWidth: area?.getBoundingClientRect().width || 0,
          areaHeight: area?.getBoundingClientRect().height || 0,
          lineTag: lineSvg?.tagName || '',
          lineHeight: lineSvg?.getBoundingClientRect().height || 0,
          lineStrokeWidth: Number.parseFloat(line ? getComputedStyle(line).strokeWidth : '0') || 0,
        };
        probe.remove();
        return result;
        """
    )
    assert result == {
        "areaTag": "SPAN",
        "areaWidth": 18,
        "areaHeight": 6,
        "lineTag": "svg",
        "lineHeight": 4,
        "lineStrokeWidth": 1.5,
    }, result


def test_go_stats_capabilities_validate_and_load_fifteen_minute_history(browser, tmp_path, monkeypatch, request):
    """Exercise the shipped JS validator against the live Go stats endpoint."""
    source_root = Path(__file__).resolve().parents[1]
    private_source_root = tmp_path / "source-root"
    # The Go server only needs these runtime roots. Copying the whole checkout makes this
    # non-root Docker test depend on unrelated developer-owned files (for example mode-600
    # locks), and it needlessly enlarges the private server fixture.
    for relative in ("static", "tools", "yolomux_lib"):
        shutil.copytree(source_root / relative, private_source_root / relative)
    configured_binary = os.environ.get("YOLOMUX_GO_BINARY", "").strip()
    binary = Path(configured_binary) if configured_binary else tmp_path / "yolomux-go"
    if not configured_binary:
        go_binary = shutil.which("go")
        if not go_binary:
            pytest.skip("Go browser regression requires the Go toolchain")
        go_environment = {
            **os.environ,
            "GOTOOLCHAIN": "local",
            "GOWORK": "off",
            "GO111MODULE": "on",
        }
        image_module_cache = Path("/opt/yolomux-go-mod-cache")
        if image_module_cache.is_dir():
            go_environment.update(
                {
                    "GOMODCACHE": str(image_module_cache),
                    "GOPROXY": "off",
                    "GOSUMDB": "off",
                    "GOCACHE": str(tmp_path / "go-cache"),
                    "GOPATH": str(tmp_path / "go-path"),
                }
            )
        subprocess.run(
            [go_binary, "build", "-buildvcs=false", "-o", str(binary), "./cmd/yolomux-go"],
            cwd=source_root,
            check=True,
            env=go_environment,
        )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "auth.yaml").write_text(
        "users:\n  - username: e2e-admin\n    password: fixture-only-password\n    role: admin\n",
        encoding="utf-8",
    )
    (config_dir / "auth-cookie-secret").write_text("11" * 32, encoding="ascii")
    tmux_runtime = start_isolated_tmux_runtime(monkeypatch, tmp_path, session_count=1)
    port_lease = HttpPortLease.reserve()
    process = None
    server_log_path = tmp_path / "go-server.log"
    server_log = server_log_path.open("w", encoding="utf-8")
    try:
        _seed_go_stats_fixture(tmp_path / "stats.sqlite3")
        fixture_home = tmp_path / "home"
        fixture_home.mkdir()
        seeded_environment = _synthetic_server_environment()
        seeded_environment.update({
            "HOME": str(fixture_home),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
            "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
            "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
            "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
            "XDG_RUNTIME_DIR": str(tmp_path / "xdg-runtime"),
            "CODEX_HOME": str(tmp_path / "codex-home"),
            "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config"),
            "TMPDIR": str(tmp_path / "tmp"),
            "YOLOMUX_STATS_DISABLE_COLLECTORS": "1",
            "YOLOMUX_TMUX_SOCKET": str(tmux_runtime.socket_path),
            "PYTHONPATH": str(private_source_root),
        })
        for private_path in (
            seeded_environment["XDG_CONFIG_HOME"], seeded_environment["XDG_DATA_HOME"],
            seeded_environment["XDG_STATE_HOME"], seeded_environment["XDG_CACHE_HOME"],
            seeded_environment["XDG_RUNTIME_DIR"], seeded_environment["TMPDIR"],
        ):
            Path(private_path).mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            [
                str(binary),
                "--host",
                "127.0.0.1",
                "--port",
                str(port_lease.port),
                "--root",
                str(private_source_root),
                "--config",
                str(config_dir),
                "--stats",
                str(tmp_path / "stats.sqlite3"),
            ],
            cwd=private_source_root,
            env=seeded_environment,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        server_log.close()
        server_log = None
        port_lease.release()
        WebDriverWait(browser, 30, poll_frequency=0.05).until(
            lambda _driver: _go_server_is_ready(port_lease.port)
        )
        base_url = f"https://127.0.0.1:{port_lease.port}"
        register_browser_new_document_script(browser, _STATS_ROUTE_INSTRUMENTATION)
        browser.get(f"{base_url}/login")
        login_form = WebDriverWait(browser, 15, poll_frequency=0.05).until(
            lambda driver: driver.find_elements(By.CSS_SELECTOR, 'form[action="/login"]')
            or driver.find_elements(By.CSS_SELECTOR, 'input[name="username"]')
            or (_raise_login_page_diagnostic(driver))
        )[0]
        login_form.find_element(By.NAME, "username").send_keys("e2e-admin")
        login_form.find_element(By.NAME, "password").send_keys("fixture-only-password")
        login_form.find_element(By.CSS_SELECTOR, "button[type=submit]").click()
        try:
            WebDriverWait(browser, 15, poll_frequency=0.05).until(
                lambda driver: driver.current_url.split("?", 1)[0].rstrip("/") == base_url
            )
        except TimeoutException as error:
            raise AssertionError(
                {
                    "login_url_after_submit": browser.current_url,
                    "login_title": browser.title,
                    "login_source": browser.page_source[:2000],
                    "go_server_log": server_log_path.read_text(encoding="utf-8")[-6000:],
                }
            ) from error
        browser.get(
            f"{base_url}/?sessions={tmux_runtime.sessions[0]},debug"
            f"&layout=left&tabs=left:{tmux_runtime.sessions[0]},__debug__*&debug=1&stats_only=1"
        )
        WebDriverWait(browser, 15, poll_frequency=0.05).until(
            lambda driver: driver.execute_script(
                "return document.readyState === 'complete' && document.querySelector('#grid') !== null"
            )
        )
        request.node._yolomux_skip_reused_browser_diagnostic = True
        WebDriverWait(browser, 15, poll_frequency=0.05).until(
            lambda driver: driver.execute_script(
                "return typeof setDebugGraphRange === 'function' "
                "&& document.querySelector('#grid') !== null"
            )
        )
        try:
            WebDriverWait(browser, 20, poll_frequency=0.05).until(
                lambda driver: driver.execute_script(
                    "return typeof jsDebugHistoryReadiness !== 'undefined' "
                    "&& jsDebugHistoryReadiness.phase === 'ready' "
                    "&& jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.() !== null"
                )
            )
        except TimeoutException as error:
            diagnostic = browser.execute_script(
                """
                const clientState = globalThis.jsDebugCurrentStatsClientState || {};
                const controller = clientState.client?.controller?.();
                const generation = controller?.generation?.() || null;
                const panel = document.querySelector('.js-debug-panel');
                const panelRect = panel?.getBoundingClientRect?.();
                const events = (globalThis.jsDebugEvents || []).filter(event =>
                  event?.type === 'stats_history' || event?.route === '/api/stats-stream');
                return {
                  url: location.href,
                  readiness: globalThis.jsDebugHistoryReadinessSnapshot?.() || null,
                  generation,
                  streamEvidence: globalThis.jsDebugCurrentStatsStreamEvidence?.() || null,
                  client: {
                    selectionKey: clientState.selectionKey || '',
                    failureLatched: clientState.failureLatched === true,
                    startPromise: Boolean(clientState.startPromise),
                  },
                  streamEvents: (globalThis.__yolomuxStatsStreamEvents || []).slice(-32),
                  routeCounts: globalThis.__yolomuxStatsRouteCounts || null,
                  graph: {
                    debugModeEnabled: globalThis.debugModeEnabled === true,
                    panelPresent: Boolean(panel),
                    panelVisible: Boolean(panelRect && panelRect.width > 0 && panelRect.height > 0),
                    panelText: panel?.innerText?.slice(0, 800) || '',
                    exactResolutionSeconds: typeof debugGraphRenderedResolutionSeconds === 'function'
                      ? debugGraphRenderedResolutionSeconds(Date.now()) : null,
                    resolutionOverrideSeconds: globalThis.debugRuntimeState?.graphResolutionOverrideSeconds ?? null,
                  },
                  events: events.slice(-20),
                  apiResources: performance.getEntriesByType('resource')
                    .map(entry => entry.name)
                    .filter(name => name.includes('/api/stats'))
                    .slice(-30),
                  bodyText: document.body?.innerText?.slice(0, 1200) || '',
                };
                """
            )
            server_output = server_log_path.read_text(encoding="utf-8")
            raise AssertionError({"browser": diagnostic, "server_log": server_output[-12000:]}) from error
        no_data_start = int(time.time()) - 20
        with sqlite3.connect(tmp_path / "stats.sqlite3") as connection:
            connection.executemany(
                "INSERT INTO unavailable_spans VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("gpu", "gpu:0", "go-gpu-later", no_data_start, no_data_start + 10, 10, "later reason", 1),
                    ("gpu", "gpu:0", "go-gpu-earlier", no_data_start - 1, no_data_start + 1, 10, "earlier reason", 1),
                ],
            )
            connection.execute("UPDATE schema_meta SET source_generation = 2 WHERE singleton = 1")
            connection.commit()
        browser.execute_script("window.__goNoDataTestStart = arguments[0]", no_data_start)
        result = browser.execute_async_script(
            r"""
            const done = arguments[arguments.length - 1];
            let streamEvents = [];
            let stream = null;
            (async () => {
              const response = await fetch('/api/stats-capabilities', {cache: 'no-store'});
              const capabilityEnvelope = await response.json();
              if (capabilityEnvelope?.state !== 'ready' || capabilityEnvelope?.ok !== true
                || capabilityEnvelope?.terminal !== true || !capabilityEnvelope?.data
                || typeof capabilityEnvelope.data !== 'object') {
                throw new Error(`invalid stats capabilities envelope: ${JSON.stringify(capabilityEnvelope)}`);
              }
              const capabilities = capabilityEnvelope.data;
              const exact = capabilities.ranges.map(row => ({
                range: row.range_seconds,
                resolutions: row.explicit_resolution_seconds,
                buckets: row.buckets,
                expected: Object.fromEntries(row.explicit_resolution_seconds.map(resolution => [
                  String(resolution), row.range_seconds / resolution,
                ])),
              }));
              const now = Math.floor(Date.now() / 1000);
              const syntheticObservations = [120, 60, 0].map((age, index) => ({
                event_id: `go-e2e-${now}-${index}`,
                family: 'browser',
                source_id: 'browser-go-e2e',
                observed_at: now - age,
                epoch_id: 'go-e2e-epoch',
                payload: {
                  kind: 'api', latency_ms: 37 + index, bytes: 512 + index,
                  endpoint: '/api/stats-snapshot', method: 'GET', status: 200,
                },
              }));
              const observationResponse = await fetch('/api/stats-observations', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                  protocol_version: 25,
                  schema_generation: 9,
                  client_id: 'browser-go-e2e',
                  observations: syntheticObservations,
                }),
              });
              const observation = await observationResponse.json();
              if (observationResponse.status !== 200 || observation.accepted !== syntheticObservations.length) {
                throw new Error(`synthetic observation rejected: ${observationResponse.status} ${JSON.stringify(observation)}`);
              }
              stream = new EventSource('/api/stats-stream?range_seconds=900&resolution=AUTO&client_id=browser-go-e2e');
              for (const eventName of ['ack', 'snapshot', 'delta', 'ready']) {
                stream.addEventListener(eventName, event => streamEvents.push({event: eventName, data: JSON.parse(event.data)}));
              }
              await window.__yolomuxTestWaitFor(
                () => {
                  const ack = streamEvents.find(item => item.event === 'ack')?.data;
                  const expectedSnapshots = Number.isInteger(ack?.chunk_count) ? ack.chunk_count : 1;
                  const snapshotCount = streamEvents.filter(item => item.event === 'snapshot').length;
                  const ready = streamEvents.some(item => item.event === 'ready');
                  return ack && snapshotCount >= expectedSnapshots && ready ? streamEvents : null;
                },
                {timeoutMs: 15000, description: 'Go stats stream ack/snapshot/ready'},
              );
                  const ack = streamEvents.find(item => item.event === 'ack')?.data;
               const snapshot = streamEvents.find(item => item.event === 'snapshot')?.data;
               const ready = streamEvents.find(item => item.event === 'ready')?.data;
              if (ack?.requested_resolution !== 'AUTO' || ack?.range_seconds !== 900
                || !Number.isInteger(ack?.resolution_seconds)) {
                throw new Error(`invalid stats stream ack: ${JSON.stringify(ack)}`);
              }
               if (!Array.isArray(snapshot?.buckets) || ready?.cache_generation === undefined) {
                 throw new Error(`invalid stats stream snapshot/ready: ${JSON.stringify({snapshot, ready})}`);
               }
               const initialCacheGeneration = Number(ready.cache_generation);
               const nextObservation = {
                 event_id: `go-e2e-delta-${Date.now()}`,
                 family: 'browser',
                 source_id: 'browser-go-e2e',
                 observed_at: Math.floor(Date.now() / 1000),
                 epoch_id: 'go-e2e-epoch',
                 payload: {
                   kind: 'api', latency_ms: 41, bytes: 768,
                   endpoint: '/api/stats-delta', method: 'GET', status: 200,
                 },
               };
               const appendResponse = await fetch('/api/stats-observations', {
                 method: 'POST',
                 headers: {'Content-Type': 'application/json'},
                 body: JSON.stringify({
                   protocol_version: 25,
                   schema_generation: 9,
                   client_id: 'browser-go-e2e',
                   observations: [nextObservation],
                 }),
               });
               const appendResult = await appendResponse.json();
               if (appendResponse.status !== 200 || appendResult.accepted !== 1
                 || Number(appendResult.source_generation) <= 0) {
                 throw new Error(`delta observation append failed: ${appendResponse.status} ${JSON.stringify(appendResult)}`);
               }
               await window.__yolomuxTestWaitFor(
                 () => streamEvents.find(item => item.event === 'delta' && Number(item.data?.cache_generation) > initialCacheGeneration),
                 {timeoutMs: 20000, intervalMs: 50, description: 'Go stats browser delta after append'},
               );
               const delta = streamEvents.find(item => item.event === 'delta' && Number(item.data?.cache_generation) > initialCacheGeneration)?.data;
               if (!delta || Number(delta.base_cache_generation) !== initialCacheGeneration
                 || Number(delta.cache_generation) <= initialCacheGeneration
                 || Number(delta.revision) < 1) {
                 throw new Error(`invalid Go stats browser delta: ${JSON.stringify({delta, streamEvents})}`);
               }
               stream.close();
               const reconnectEvents = [];
               const reconnect = new EventSource(
                 `/api/stats-stream?range_seconds=900&resolution=AUTO&client_id=browser-go-e2e&since_generation=${delta.cache_generation}`,
               );
               for (const eventName of ['ack', 'snapshot', 'ready', 'repair', 'unavailable', 'error']) {
                 reconnect.addEventListener(eventName, event => reconnectEvents.push({event: eventName, data: JSON.parse(event.data)}));
               }
               await window.__yolomuxTestWaitFor(
                 () => reconnectEvents.find(item => item.event === 'ready'),
                 {timeoutMs: 15000, intervalMs: 50, description: 'Go stats browser reconnect readiness'},
               );
               reconnect.close();
               const reconnectAck = reconnectEvents.find(item => item.event === 'ack')?.data;
               const reconnectReady = reconnectEvents.find(item => item.event === 'ready')?.data;
               if (!reconnectAck?.not_modified || Number(reconnectAck.cache_generation) !== Number(delta.cache_generation)
                 || reconnectEvents.some(item => item.event === 'snapshot')
                 || Number(reconnectReady?.cache_generation) !== Number(delta.cache_generation)
                 || reconnectEvents.some(item => ['repair', 'unavailable', 'error'].includes(item.event))) {
                 throw new Error(`Go stats browser reconnect did not resume the immutable generation: ${JSON.stringify({reconnectEvents, delta})}`);
               }
               const retryResponse = await fetch('/api/stats-retry', {method: 'POST'});
               if (retryResponse.status !== 200) {
                 throw new Error(`stats retry notification failed: ${retryResponse.status}`);
               }
               const rawSnapshot = await window.__yolomuxTestWaitFor(async () => {
                 const response = await fetch('/api/stats-snapshot?range_seconds=900&resolution=AUTO&client_id=browser-go-e2e', {cache: 'no-store'});
                 if (response.status !== 200) return null;
                 const body = await response.json();
                 return body.no_data?.some(span => span.family === 'gpu' && span.source_id === 'gpu:0') ? body : null;
               }, {timeoutMs: 15000, intervalMs: 50, description: 'Go stats no-data refresh'});
               const rawResponse = {status: 200};
                   const rawNoData = rawSnapshot.no_data;
               const normalizedNoData = rawNoData
                 .filter(span => span.family === 'gpu' && span.source_id === 'gpu:0')
                 .map(span => [span.family, span.source_id, span.start, span.end, span.reason]);
               const testT = Number(window.__goNoDataTestStart);
               const expectedNoData = [
                 ['gpu', 'gpu:0', testT - 1, testT + 1, 'earlier reason'],
                 ['gpu', 'gpu:0', testT + 1, testT + 10, 'later reason'],
               ];
               if (rawResponse.status !== 200 || !Array.isArray(rawNoData)
                 || JSON.stringify(normalizedNoData) !== JSON.stringify(expectedNoData)
                     ) {
                     throw new Error(`raw stats snapshot no-data normalization failed: ${JSON.stringify({status: rawResponse.status, normalizedNoData})}`);
                   }
                   const validator = YOLOmuxStatsCurrent.createController({
                     capabilities: capabilities,
                     savedRange: 900,
                     savedResolution: 'AUTO',
                     clientId: 'raw-no-data-validator',
                   });
                   if (validator.acceptSnapshot(rawSnapshot) !== true) {
                     throw new Error('raw stats snapshot validator did not accept a fresh generation');
                   }
               const agentSeries = new Set(['ask_agents', 'run_agents', 'transition_agents', 'idle_agents']);
               const publishedAgentSeries = new Set(snapshot.buckets.flatMap(bucket => Object.keys(bucket.series || {})).filter(name => agentSeries.has(name)));
               if (publishedAgentSeries.size !== agentSeries.size) {
                 throw new Error(`Agents chart data missing: ${JSON.stringify({published: [...publishedAgentSeries], snapshot})}`);
               }
               const defaultChartKeys = [...document.querySelectorAll('.js-debug-panel [data-js-debug-chart]')]
                 .map(chart => chart.dataset.jsDebugChart);
               const defaultDaemonsToggle = document.querySelector('[data-js-debug-chart-toggle="serversLoad"]');
               const defaultGraphs = Object.fromEntries(['activity', 'agentTokens', 'memory'].map(chart => {
                 const node = document.querySelector(`.js-debug-panel [data-js-debug-chart="${chart}"]`);
                 const plotted = [...(node?.querySelectorAll('[data-js-debug-series], [data-js-debug-area-series], [data-js-debug-bar-series]') || [])];
                 return [chart, {
                   present: Boolean(node),
                   visible: Boolean(node && node.getBoundingClientRect().width > 0 && node.getBoundingClientRect().height > 0),
                   geometry: plotted.filter(item => {
                     const path = item.getAttribute('d') || item.getAttribute('points') || '';
                     return path.length > 0 || Number(item.getAttribute('height') || 0) > 0;
                   }).length,
                   positiveData: plotted.filter(item => (
                     Number(item.dataset.jsDebugBarTotal || 0) > 0
                     || Number(item.getAttribute('height') || 0) > 0
                     || Boolean(item.getAttribute('d') || item.getAttribute('points'))
                   )).length,
                   waiting: node?.textContent?.includes('Waiting for server stats') || false,
                 }];
               }));
               if (jsDebugHistoryReadiness.phase !== 'ready'
                 || Object.values(defaultGraphs).some(chart => !chart.present || !chart.visible || chart.geometry <= 0 || chart.positiveData <= 0 || chart.waiting)
                 || !defaultChartKeys.includes('activity')
                 || !defaultChartKeys.includes('agentTokens')
                 || !defaultChartKeys.includes('memory')
                 || defaultChartKeys.includes('serversLoad')
                 || defaultDaemonsToggle !== null) {
                 throw new Error(`default Graphs state did not paint the required charts: ${JSON.stringify({readiness: jsDebugHistoryReadinessSnapshot?.(), defaultChartKeys, defaultDaemonsChecked: defaultDaemonsToggle?.checked, defaultGraphs})}`);
               }
               const warningsBeforeProbe = jsDebugEvents.filter(event => event?.type === 'stats_history'
                 && event?.level === 'warning').map(event => String(event.message || ''));
              if (typeof debugGraphSvgHtml !== 'function' || typeof debugGraphSeriesData !== 'function'
                || typeof debugGraphDisplayBuckets !== 'function') {
                throw new Error('missing graph render functions');
              }
                  if (typeof clearJsDebugGraphData !== 'function'
                    || typeof jsDebugCurrentBucketRecord !== 'function'
                    || typeof debugGraphApplyServerRecord !== 'function'
                    || typeof updateJsDebugCurrentSnapshotState !== 'function'
                    || typeof refreshDebugGraphSurfaces !== 'function') {
                    throw new Error('missing graph snapshot render functions');
                  }
                  clearJsDebugGraphData();
                  const currentClientId = jsDebugStatsClientIdForRequest();
                  snapshot.buckets.forEach(bucket => {
                    const record = jsDebugCurrentBucketRecord(bucket);
                    record.clients = {[currentClientId]: {
                      api_count: record.api_count || 0,
                      sse_count: record.sse_count || 0,
                      latency_total_ms: record.latency_total_ms || 0,
                      latency_count: record.latency_count || 0,
                      bandwidth_bytes: record.bandwidth_bytes || 0,
                      heartbeat_count: record.heartbeat_count || 0,
                      disconnected_ms: record.disconnected_ms || 0,
                    }};
                    debugGraphApplyServerRecord(record);
                  });
              updateJsDebugCurrentSnapshotState(snapshot, {forceGraphRefresh: true});
              const buckets = debugGraphDisplayBuckets(Date.now(), {rangeSeconds: 900});
              const seriesItems = debugGraphSeriesData(buckets);
              const requiredGroups = jsDebugGraphChartGroups.filter(group => ['activity', 'agentTokens', 'memory'].includes(group.key));
              const render = document.createElement('div');
              render.innerHTML = debugGraphSvgHtml(
                buckets,
                seriesItems,
                requiredGroups,
                Date.now(),
              );
              document.body.append(render);
              const generation = snapshot;
                  const warnings = jsDebugEvents.filter(event => event?.type === 'stats_history'
                    && event?.level === 'warning').map(event => String(event.message || ''));
              const charts = [...render.querySelectorAll('.js-debug-line-chart')].map(svg => ({
                    viewBox: svg.getAttribute('viewBox'),
                    width: svg.getBoundingClientRect().width,
                    height: svg.getBoundingClientRect().height,
                    paths: [...svg.querySelectorAll('path')].map(path => path.getAttribute('d') || '').filter(Boolean),
                    polylines: [...svg.querySelectorAll('polyline')].map(line => line.getAttribute('points') || '').filter(Boolean),
                  }));
              const requiredSeries = Object.fromEntries(Object.entries(__GO_STATS_SERIES__).map(([chart, names]) => [
                chart,
                names.map(([snapshotName, domName]) => ({snapshotName, domName, count: 0, geometry: 0})),
              ]));
              for (const [chartName, names] of Object.entries(requiredSeries)) {
                const chart = render.querySelector(`[data-js-debug-chart="${chartName}"]`);
                for (const item of names) {
                  const nodes = [...(chart?.querySelectorAll(`[data-js-debug-series="${item.domName}"], [data-js-debug-bar-series="${item.domName}"], [data-js-debug-area-series="${item.domName}"]`) || [])];
                  item.count = nodes.reduce((total, node) => total + Math.max(
                    Number(node.dataset.pointCount || node.getAttribute('data-point-count') || 0),
                    node.getAttribute('points')?.trim().split(/\s+/).filter(Boolean).length || 0,
                    node.getAttribute('d') ? 1 : 0,
                    Number(node.getAttribute('height') || 0) > 0 ? 1 : 0,
                  ), 0);
                  item.geometry = nodes.reduce((total, node) => total
                    + (node.getAttribute('points') || node.getAttribute('d') || '').length
                    + (Number(node.getAttribute('height') || 0) > 0 ? 1 : 0), 0);
                }
              }
              const nonEmptyGeometry = charts.some(chart => {
                const values = String(chart.viewBox || '').trim().split(/\s+/).map(Number);
                return values.length === 4 && values[2] > 0 && values[3] > 0
                  && chart.width > 0 && chart.height > 0
                      && (chart.paths.some(path => /[ML]\s*[-+\d.]+\s+[-+\d.]+/.test(path))
                        || chart.polylines.some(points => /[-+\d.]+\s*,?\s*[-+\d.]+/.test(points)));
              });
              const missingSeries = Object.entries(requiredSeries).flatMap(([chart, names]) => names
                .filter(item => item.count <= 0 || item.geometry <= 0)
                .map(item => `${chart}/${item.domName} snapshot=${item.snapshotName} count=${item.count} geometry=${item.geometry}`));
                  const snapshotSeries = snapshot.buckets.flatMap(bucket => Object.keys(bucket.series || {})).reduce((counts, name) => { counts[name] = (counts[name] || 0) + 1; return counts; }, {});
              const snapshotSeriesValues = Object.fromEntries(__GO_STATS_SNAPSHOT_SERIES__.map(name => [
                name,
                snapshot.buckets.flatMap(bucket => {
                  const value = bucket.series?.[name]?.value;
                  return Number(value) > 0 ? [Number(value)] : [];
                }),
              ]));
              const missingSnapshotSeries = Object.entries(snapshotSeriesValues)
                .filter(([_name, values]) => values.length <= 0)
                .map(([name, values]) => `${name} count=${values.length}`);
               const tokenRates = snapshot.buckets.flatMap(bucket => Object.entries(bucket.series || {})
                .filter(([name, item]) => name.startsWith('agent_tokens_per_minute:') && Number(item?.value) > 0)
                .map(([name, item]) => ({name, value: Number(item.value)})));
               const costReport = snapshot.cost_report;
               if (!costReport || Number(costReport.total_tokens) <= 0
                 || Number(costReport.unpriced?.tokens) <= 0
                 || Number(costReport.total_micro_usd) !== 0) {
                 throw new Error(`cost report stats gate failed: ${JSON.stringify(costReport)}`);
               }
               if (!nonEmptyGeometry || missingSeries.length || missingSnapshotSeries.length || tokenRates.length <= 0) {
                 throw new Error(`15m graph stats gate failed: missingRendered=${JSON.stringify(missingSeries)} missingSnapshot=${JSON.stringify(missingSnapshotSeries)} tokenRates=${JSON.stringify(tokenRates)} charts=${JSON.stringify(charts)} snapshotSeries=${JSON.stringify(snapshotSeries)}`);
               }
               setDebugGraphRange(300);
               await window.__yolomuxTestWaitFor(
                 () => jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.()?.range_seconds === 300
                   && jsDebugHistoryReadiness.phase === 'ready',
                 {timeoutMs: 15000, description: 'Go stats 5m generation'},
               );
               const fiveMinuteSnapshotResponse = await fetch('/api/stats-snapshot?range_seconds=300&resolution=AUTO&client_id=browser-go-e2e', {cache: 'no-store'});
               const fiveMinuteSnapshot = await fiveMinuteSnapshotResponse.json();
               const fiveMinuteMemoryValues = fiveMinuteSnapshot.buckets.flatMap(bucket => [
                 bucket.series?.system_memory_used_bytes?.value,
                 bucket.series?.['process_memory_bytes:python']?.value,
               ]).filter(value => Number(value) > 0);
               const fiveMinuteMemoryChart = document.querySelector('[data-js-debug-chart="memory"]');
               const fiveMinuteMemoryGeometry = [...(fiveMinuteMemoryChart?.querySelectorAll('[data-js-debug-series], [data-js-debug-area-series]') || [])]
                 .filter(node => Boolean(node.getAttribute('d') || node.getAttribute('points'))).length;
               if (fiveMinuteSnapshotResponse.status !== 200 || fiveMinuteSnapshot.resolution_seconds !== 1
                 || fiveMinuteMemoryValues.length < 2 || !fiveMinuteMemoryChart
                 || fiveMinuteMemoryChart.getBoundingClientRect().width <= 0
                 || fiveMinuteMemoryChart.getBoundingClientRect().height <= 0
                 || fiveMinuteMemoryGeometry <= 0) {
                 throw new Error(`5m memory graph stats gate failed: ${JSON.stringify({status: fiveMinuteSnapshotResponse.status, resolution: fiveMinuteSnapshot.resolution_seconds, values: fiveMinuteMemoryValues, geometry: fiveMinuteMemoryGeometry, chart: Boolean(fiveMinuteMemoryChart)})}`);
               }
                done({status: response.status, readiness: jsDebugHistoryReadinessSnapshot?.(), routeCounts: {...globalThis.__yolomuxStatsRouteCounts}, unrelatedRouteCounts: {...globalThis.__yolomuxStatsUnrelatedRouteCounts}, defaultChartKeys, defaultDaemonsChecked: defaultDaemonsToggle?.checked ?? false, defaultGraphs, exact, streamEvolution: {initialCacheGeneration, delta, reconnectAck, reconnectReady, reconnectEvents}, generation: {
                range: generation.range_seconds,
                resolution: generation.resolution_seconds,
                buckets: generation.buckets.length,
                }, stream: {events: streamEvents, ack, snapshotBuckets: snapshot.buckets.length, ready}, charts, requiredSeries, snapshotSeriesValues, tokenRates, costReport, warnings, warningsBeforeProbe, fiveMinute: {status: fiveMinuteSnapshotResponse.status, resolution: fiveMinuteSnapshot.resolution_seconds, memoryValues: fiveMinuteMemoryValues, geometry: fiveMinuteMemoryGeometry}});
                })().catch(error => done({error: String(error?.stack || error), streamEvents, streamReadyState: stream?.readyState ?? null, readiness: globalThis.jsDebugHistoryReadinessSnapshot?.(), routeCounts: globalThis.__yolomuxStatsRouteCounts || null, serverEvents: globalThis.__yolomuxStatsStreamEvents?.slice(-32) || []}));
            """.replace("__GO_STATS_SERIES__", json.dumps(GO_STATS_SERIES)).replace(
                "__GO_STATS_SNAPSHOT_SERIES__", json.dumps(GO_STATS_SNAPSHOT_SERIES)
            )
        )
        assert result.get("error") is None, result
        assert result["status"] == 200, result
        assert result["warnings"] == result["warningsBeforeProbe"], result
        for row in result["exact"]:
            assert row["buckets"] == row["expected"], row
        assert result["generation"]["range"] == 900, result
        assert result["generation"]["buckets"] > 0, result
        evolution = result["streamEvolution"]
        assert evolution["delta"]["base_cache_generation"] == evolution["initialCacheGeneration"], evolution
        assert evolution["delta"]["cache_generation"] > evolution["initialCacheGeneration"], evolution
        assert evolution["delta"]["revision"] >= 1, evolution
        assert evolution["reconnectAck"]["not_modified"] is True, evolution
        assert evolution["reconnectAck"]["cache_generation"] == evolution["delta"]["cache_generation"], evolution
        assert not any(item["event"] == "snapshot" for item in evolution["reconnectEvents"]), evolution
        assert result["defaultDaemonsChecked"] is False, result
        assert set(("activity", "agentTokens", "memory")).issubset(result["defaultChartKeys"]), result
        assert "serversLoad" not in result["defaultChartKeys"], result
        for chart in ("activity", "agentTokens", "memory"):
            assert result["defaultGraphs"][chart]["visible"] is True, result
            assert result["defaultGraphs"][chart]["geometry"] > 0, result
            assert result["defaultGraphs"][chart]["positiveData"] > 0, result
        assert all(values and all(value > 0 for value in values) for values in result["snapshotSeriesValues"].values()), result
        assert result["tokenRates"] and all(rate["value"] > 0 for rate in result["tokenRates"]), result
        for names in result["requiredSeries"].values():
            for series in names:
                assert series["count"] > 0 and series["geometry"] > 0, result
        assert result["fiveMinute"]["resolution"] == 1, result
        assert len(result["fiveMinute"]["memoryValues"]) >= 2, result
        assert result["fiveMinute"]["geometry"] > 0, result
        unrelated_routes = (
            "/api/settings", "/api/session-metadata", "/api/background/status", "/api/auto-approve",
            "/api/fs/index-status", "/api/watched-prs", "/api/update-status", "/api/chat/bootstrap",
            "/api/transcripts",
        )
        unexpected_routes = {route: result["unrelatedRouteCounts"].get(route, 0) for route in unrelated_routes if result["unrelatedRouteCounts"].get(route, 0)}
        assert not unexpected_routes, {"unexpected_routes": unexpected_routes, "route_counts": result["unrelatedRouteCounts"]}
        assert result["unrelatedRouteCounts"].get("/api/activity", 0) == 1, result
    finally:
        active_error = sys.exc_info()[1]
        browser_retirement_error = None
        try:
            # This test owns a Go listener, not the fixture HTTP server. Retire the shared browser
            # page while that listener is still alive so EventSource/fetch teardown cannot turn into
            # connection-refused noise in the autouse browser reset.
            browser.get("about:blank")
            WebDriverWait(browser, 3, poll_frequency=0.05).until(
                lambda driver: str(driver.current_url) == "about:blank"
            )
        except WebDriverException as error:
            browser_retirement_error = AssertionError(
                f"Go stats browser page did not retire before server shutdown: {error}"
            )
        port_lease.close()
        if server_log is not None:
            server_log.close()
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        stop_isolated_tmux_runtime(tmux_runtime)
        if browser_retirement_error is not None:
            if active_error is not None:
                print(f"Go stats browser cleanup also failed: {browser_retirement_error}", file=sys.stderr)
            else:
                raise browser_retirement_error


def test_go_collectors_drive_browser_stats_without_seeded_facts(browser, tmp_path, monkeypatch, request):
    """Prove collector -> SQLite -> lifecycle -> SSE -> unchanged UI in one fixture."""
    source_root = Path(__file__).resolve().parents[1]
    private_source_root = tmp_path / "source-root"
    for relative in ("static", "tools", "yolomux_lib"):
        shutil.copytree(source_root / relative, private_source_root / relative)
    home, opencode_database, opencode_interpreter = _prepare_go_collector_sources(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "auth.yaml").write_text(
        "users:\n  - username: collector-admin\n    password: fixture-only-password\n    role: admin\n",
        encoding="utf-8",
    )
    (config_dir / "auth-cookie-secret").write_text("11" * 32, encoding="ascii")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("YOLOMUX_OPENCODE_DATABASE", str(opencode_database))
    monkeypatch.setenv("YOLOMUX_CLAUDE_TRANSCRIPT_ROOT", str(home / "claude" / "projects"))
    monkeypatch.setenv("YOLOMUX_CODEX_TRANSCRIPT_ROOT", str(home / "codex" / "sessions"))
    monkeypatch.setenv("YOLOMUX_PRICING_CATALOG", str(private_source_root / "yolomux_lib" / "data" / "model_pricing.json"))
    prefix = f"yt-collector-{os.getpid()}"
    session_commands = {
        f"{prefix}-claude": f"exec -a claude {sys.executable} {source_root / 'tools/mockers/claude.py'} --mock",
        f"{prefix}-codex": f"exec -a codex {sys.executable} {source_root / 'tools/mockers/codex.py'} --mock",
        f"{prefix}-opencode": f"exec -a opencode {opencode_interpreter} {source_root / 'tools/mockers/opencode.py'} --mock --session ses-live",
    }
    tmux_runtime = start_isolated_tmux_runtime(
        monkeypatch,
        tmp_path,
        session_commands=session_commands,
        session_cwd=source_root,
    )
    configured_binary = os.environ.get("YOLOMUX_GO_BINARY", "").strip()
    binary = Path(configured_binary) if configured_binary else tmp_path / "yolomux-go"
    if not configured_binary:
        go_binary = shutil.which("go")
        if not go_binary:
            stop_isolated_tmux_runtime(tmux_runtime)
            pytest.skip("Go collector browser regression requires the Go toolchain")
        build_environment = {
            **os.environ,
            "GOTOOLCHAIN": "local",
            "GOWORK": "off",
            "GO111MODULE": "on",
        }
        image_module_cache = Path("/opt/yolomux-go-mod-cache")
        if image_module_cache.is_dir():
            build_environment.update(
                {
                    "GOMODCACHE": str(image_module_cache),
                    "GOPROXY": "off",
                    "GOSUMDB": "off",
                    "GOCACHE": str(tmp_path / "go-cache"),
                    "GOPATH": str(tmp_path / "go-path"),
                }
            )
        subprocess.run(
            [go_binary, "build", "-buildvcs=false", "-o", str(binary), "./cmd/yolomux-go"],
            cwd=source_root,
            check=True,
            env=build_environment,
        )
    port_lease = HttpPortLease.reserve()
    process = None
    server_log_path = tmp_path / "go-collector-server.log"
    server_log = server_log_path.open("w", encoding="utf-8")
    try:
        collector_environment = _synthetic_server_environment()
        collector_environment.update({
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
            "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
            "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
            "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
            "XDG_RUNTIME_DIR": str(tmp_path / "xdg-runtime"),
            "CODEX_HOME": str(home / "codex"),
            "CLAUDE_CONFIG_DIR": str(home / "claude"),
            "TMPDIR": str(tmp_path / "tmp"),
                "YOLOMUX_OPENCODE_DATABASE": str(opencode_database),
                "YOLOMUX_CLAUDE_TRANSCRIPT_ROOT": str(home / "claude" / "projects"),
                "YOLOMUX_CODEX_TRANSCRIPT_ROOT": str(home / "codex" / "sessions"),
                "YOLOMUX_PRICING_CATALOG": str(private_source_root / "yolomux_lib" / "data" / "model_pricing.json"),
                "YOLOMUX_TMUX_SOCKET": str(tmux_runtime.socket_path),
                "PYTHONPATH": str(private_source_root),
            })
        for private_path in (
            collector_environment["XDG_CONFIG_HOME"], collector_environment["XDG_DATA_HOME"],
            collector_environment["XDG_STATE_HOME"], collector_environment["XDG_CACHE_HOME"],
            collector_environment["XDG_RUNTIME_DIR"], collector_environment["TMPDIR"],
        ):
            Path(private_path).mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            [
                str(binary),
                "--http",
                "--host",
                "127.0.0.1",
                "--port",
                str(port_lease.port),
                "--root",
                str(private_source_root),
                "--config",
                str(config_dir),
                "--stats",
                str(tmp_path / "stats.sqlite3"),
            ],
            cwd=private_source_root,
            env=collector_environment,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        server_log.close()
        server_log = None
        port_lease.release()
        WebDriverWait(browser, 30, poll_frequency=0.05).until(
            lambda _driver: _go_server_http_is_ready(port_lease.port)
        )
        base_url = f"http://127.0.0.1:{port_lease.port}"
        browser.get(f"{base_url}/login")
        login_form = WebDriverWait(browser, 15, poll_frequency=0.05).until(
            lambda driver: driver.find_elements(By.CSS_SELECTOR, 'form[action="/login"]')
            or driver.find_elements(By.CSS_SELECTOR, 'input[name="username"]')
            or (_raise_login_page_diagnostic(driver))
        )[0]
        login_form.find_element(By.NAME, "username").send_keys("collector-admin")
        login_form.find_element(By.NAME, "password").send_keys("fixture-only-password")
        login_form.find_element(By.CSS_SELECTOR, "button[type=submit]").click()
        WebDriverWait(browser, 15, poll_frequency=0.05).until(
            lambda driver: driver.current_url.split("?", 1)[0].rstrip("/") == base_url
        )
        request.node._yolomux_skip_reused_browser_diagnostic = True
        sessions = ",".join(tmux_runtime.sessions)
        browser.get(f"{base_url}/?sessions={sessions},debug&layout=left&tabs=left:{tmux_runtime.sessions[0]},__debug__*&debug=1&stats_only=1")
        WebDriverWait(browser, 15, poll_frequency=0.05).until(
            lambda driver: driver.execute_script("return document.readyState === 'complete' && document.querySelector('#grid') !== null")
        )
        result = browser.execute_async_script(
            r"""
            const done = arguments[arguments.length - 1];
            const wait = async () => {
              const deadline = Date.now() + 30000;
              while (Date.now() < deadline) {
                try {
                  const [statusResponse, snapshotResponse] = await Promise.all([
                    fetch('/api/stats-agent-status', {cache: 'no-store'}),
                    fetch('/api/stats-snapshot?range_seconds=300&resolution=AUTO&client_id=collector-browser', {cache: 'no-store'}),
                  ]);
                  const status = await statusResponse.json();
                  const snapshot = await snapshotResponse.json();
                  const values = snapshot.buckets?.flatMap(bucket => Object.entries(bucket.series || {}).map(([name, item]) => ({name, value: Number(item?.value)}))) || [];
                  const tokens = values.filter(item => item.name.startsWith('agent_tokens_per_minute:') && item.value > 0);
                  const activity = values.filter(item => ['ask_agents', 'run_agents', 'transition_agents', 'idle_agents'].includes(item.name) && item.value > 0);
                  const memory = values.filter(item => item.name === 'system_memory_used_bytes' && item.value > 0);
                  const cpu = values.filter(item => item.name === 'system_cpu_percent' && item.value > 0);
                  const gpu = values.filter(item => item.name.startsWith('gpu_') && item.value > 0);
                  const gpuNoData = (snapshot.no_data || []).filter(span => span.family === 'gpu');
                  const cost = snapshot.cost_report || {};
                  if (statusResponse.status === 200 && snapshotResponse.status === 200
                    && Number(status.counts?.total) >= 3
                    && Number(status.token_rates?.total_per_second) > 0
                    && tokens.length > 0 && activity.length > 0 && memory.length > 0 && cpu.length > 0
                    && (gpu.length > 0 || gpuNoData.length > 0)
                    && Number(cost.total_tokens) > 0) {
                    done({status, snapshot, tokens, activity, memory, cpu, gpu, gpuNoData, cost});
                    return;
                  }
                } catch (_) {}
                await new Promise(resolve => setTimeout(resolve, 250));
              }
              done({error: 'collector facts did not reach snapshot before deadline', readiness: globalThis.jsDebugHistoryReadinessSnapshot?.(), body: document.body?.innerText?.slice(0, 1000)});
            };
            wait();
            """
        )
        assert result.get("error") is None, result
        assert result["gpu"] or result["gpuNoData"], result
        charts = browser.execute_script(
            """
            return Object.fromEntries(['activity', 'agentTokens', 'memory'].map(name => {
              const chart = document.querySelector(`[data-js-debug-chart="${name}"]`);
              const nodes = [...(chart?.querySelectorAll('[data-js-debug-series], [data-js-debug-area-series], [data-js-debug-bar-series]') || [])];
              return [name, {present: Boolean(chart), visible: Boolean(chart && chart.getBoundingClientRect().width > 0 && chart.getBoundingClientRect().height > 0), geometry: nodes.filter(node => node.getAttribute('d') || node.getAttribute('points') || Number(node.getAttribute('height') || 0) > 0).length, waiting: chart?.textContent?.includes('Waiting for server stats') || false}];
            }));
            """
        )
        assert browser.execute_script("return jsDebugHistoryReadiness.phase") == "ready", result
        assert all(item["present"] and item["visible"] and item["geometry"] > 0 and not item["waiting"] for item in charts.values()), {"result": result, "charts": charts}
    finally:
        active_error = sys.exc_info()[1]
        browser_retirement_error = None
        try:
            browser.get("about:blank")
            WebDriverWait(browser, 3, poll_frequency=0.05).until(lambda driver: str(driver.current_url) == "about:blank")
        except WebDriverException as error:
            browser_retirement_error = AssertionError(f"Go collector browser page did not retire: {error}")
        port_lease.close()
        if server_log is not None:
            server_log.close()
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        stop_isolated_tmux_runtime(tmux_runtime)
        if browser_retirement_error is not None and active_error is None:
            raise browser_retirement_error


def _go_server_is_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"https://127.0.0.1:{port}/healthz",
            context=ssl._create_unverified_context(),
            timeout=0.5,
        ) as response:
            return response.status == 200
    except OSError:
        return False


def _go_server_http_is_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.5) as response:
            return response.status == 200
    except OSError:
        return False


def _start_production_stats_server(
    *,
    kind: str,
    binary: Path,
    source_root: Path,
    root: Path,
    project_root: Path,
    database: Path,
    config_dir: Path,
    tmux_socket: Path,
    session: str,
    runtime_dir: Path,
    log_path: Path,
):
    """Start one production stats server with a private, already-seeded store."""

    if kind not in {"python", "go"}:
        raise ValueError(f"unknown production stats server kind: {kind!r}")
    port_lease = HttpPortLease.reserve()
    environment = _synthetic_server_environment()
    environment.update({
        "HOME": str(root / "home"),
        "YOLOMUX_ROOT": str(root),
        "YOLOMUX_CONFIG_DIR": str(config_dir),
        "YOLOMUX_STATE_DIR": str(root / "state"),
        "YOLOMUX_CACHE_DIR": str(root / "cache"),
        "YOLOMUX_RUNTIME_DIR": str(runtime_dir),
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "XDG_STATE_HOME": str(root / "xdg-state"),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "XDG_RUNTIME_DIR": str(root / "xdg-runtime"),
        "CODEX_HOME": str(root / "codex-home"),
        "CLAUDE_CONFIG_DIR": str(root / "claude-config"),
        "TMPDIR": str(root / "tmp"),
        "YOLOMUX_TMUX_SOCKET": str(tmux_socket),
        "YOLOMUX_STATS_DISABLE_COLLECTORS": "1",
        "PYTHONPATH": str(source_root),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    for private_path in (
        environment["HOME"], environment["XDG_CONFIG_HOME"], environment["XDG_DATA_HOME"],
        environment["XDG_STATE_HOME"], environment["XDG_CACHE_HOME"], environment["XDG_RUNTIME_DIR"],
        environment["CODEX_HOME"], environment["CLAUDE_CONFIG_DIR"], environment["TMPDIR"],
    ):
        Path(private_path).mkdir(parents=True, exist_ok=True)
    if kind == "python":
        command = [
            sys.executable,
            "yolomux.py",
            "--http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port_lease.port),
            "--sessions",
            session,
        ]
        cwd = source_root
        ready = _python_server_is_ready
    else:
        command = [
            str(binary),
            "--http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port_lease.port),
            "--root",
            str(project_root),
            "--config",
            str(config_dir),
            "--stats",
            str(database),
        ]
        cwd = root
        ready = _go_server_http_is_ready
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    log_file.close()
    port_lease.release()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"{kind} production server exited: {log_path.read_text(encoding='utf-8')[-6000:]}")
        if ready(port_lease.port):
            return process, port_lease, f"http://127.0.0.1:{port_lease.port}"
        time.sleep(0.1)
    process.terminate()
    process.wait(timeout=10)
    raise AssertionError(f"{kind} production server did not become healthy: {log_path.read_text(encoding='utf-8')[-6000:]}")


def _python_server_is_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.5) as response:
            return response.status == 200
    except OSError:
        return False


def _stop_production_stats_server(process, port_lease: HttpPortLease) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    port_lease.close()


def _production_stats_browser_pass(browser, base_url: str, session: str, request, *, stats_only: bool = False) -> dict[str, Any]:
    """Run one identical authenticated browser journey and return stable wire evidence."""

    register_browser_new_document_script(browser, _STATS_ROUTE_INSTRUMENTATION)
    browser.get(f"{base_url}/login")
    login_form = WebDriverWait(browser, 15, poll_frequency=0.05).until(
        lambda driver: driver.find_elements(By.CSS_SELECTOR, 'form[action="/login"]')
        or driver.find_elements(By.CSS_SELECTOR, 'input[name="username"]')
        or (_raise_login_page_diagnostic(driver))
    )[0]
    login_form.find_element(By.NAME, "username").send_keys("e2e-admin")
    login_form.find_element(By.NAME, "password").send_keys("fixture-only-password")
    login_form.find_element(By.CSS_SELECTOR, "button[type=submit]").click()
    WebDriverWait(browser, 15, poll_frequency=0.05).until(
        lambda driver: driver.current_url.split("?", 1)[0].rstrip("/") == base_url
    )
    request.node._yolomux_skip_reused_browser_diagnostic = True
    stats_only_query = "&stats_only=1" if stats_only else ""
    browser.get(
        f"{base_url}/?sessions={session},debug&layout=left&tabs=left:{session},__debug__*&debug=1{stats_only_query}"
    )
    WebDriverWait(browser, 15, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            "return document.readyState === 'complete' && document.querySelector('#grid') !== null"
        )
    )
    try:
        # The Go lifecycle repairs the cold ring in bounded 64-cell runs.  The
        # fixture intentionally starts with all four rings empty, so this wait
        # covers the measured repair queue rather than treating a still-pending
        # product as a browser failure.
        WebDriverWait(browser, 60, poll_frequency=0.05).until(
            lambda driver: driver.execute_script(
                "return typeof jsDebugHistoryReadiness !== 'undefined' "
                "&& jsDebugHistoryReadiness.phase === 'ready' "
                "&& jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.() !== null"
            )
        )
    except TimeoutException as error:
        diagnostic = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const inspect = async path => {
              const response = await fetch(path, {cache: 'no-store'});
              const text = await response.text();
              let body;
              try { body = JSON.parse(text); } catch (_) { body = text; }
              return {status: response.status, body};
            };
            Promise.all([
              inspect('/api/stats-agent-status'),
              inspect('/api/stats-capabilities'),
              inspect('/api/stats-snapshot?range_seconds=300&resolution=AUTO&client_id=production-browser'),
              inspect('/api/system-status'),
            ]).then(([status, capabilities, snapshot, system]) => done({
              url: location.href,
              readiness: globalThis.jsDebugHistoryReadinessSnapshot?.() || null,
              stream: globalThis.jsDebugCurrentStatsStreamEvidence?.() || null,
              body: document.body?.innerText?.slice(0, 1600) || '',
              failures: typeof jsDebugFailureEvents === 'function' ? jsDebugFailureEvents() : [],
              status,
              capabilities,
              snapshot,
              system,
            })).catch(error => done({error: String(error?.stack || error)}));
            """
        )
        raise AssertionError(f"{base_url} browser stats did not become ready: {diagnostic}") from error
    evidence = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const inspect = async path => {
          const response = await fetch(path, {cache: 'no-store'});
          const text = await response.text();
          let body;
          try { body = JSON.parse(text); } catch (_) { body = text; }
          return {status: response.status, body};
        };
        const snapshotPath = '/api/stats-snapshot?range_seconds=300&resolution=AUTO&client_id=production-browser';
        const waitForSnapshot = async () => {
          const deadline = Date.now() + 60000;
          let latest;
          while (Date.now() < deadline) {
            latest = await inspect(snapshotPath);
            if (latest.status === 200) return latest;
            await new Promise(resolve => setTimeout(resolve, 250));
          }
          return {error: 'snapshot did not converge before deadline', latest};
        };
        Promise.all([
          inspect('/api/stats-capabilities'),
          inspect('/api/stats-agent-status'),
          waitForSnapshot(),
        ]).then(([capabilities, status, snapshot]) => done({
          error: snapshot.error || null,
          capabilitiesStatus: capabilities.status,
          statusStatus: status.status,
          snapshotStatus: snapshot.status || 0,
          capabilities: capabilities.body,
          status: status.body,
          snapshot: snapshot.body,
          snapshotDiagnostic: snapshot.latest || null,
        })).catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert evidence.get("error") is None, evidence
    assert evidence["capabilitiesStatus"] == 200, evidence
    assert evidence["snapshotStatus"] == 200, evidence
    snapshot = evidence["snapshot"]
    values = [
        (name, float(item.get("value", 0)))
        for bucket in snapshot.get("buckets", [])
        for name, item in bucket.get("series", {}).items()
        if isinstance(item, dict)
    ]
    assert any(name == "system_cpu_percent" and value > 0 for name, value in values), evidence
    assert any(name == "system_memory_used_bytes" and value > 0 for name, value in values), evidence
    assert any(name.startswith("agent_tokens_per_minute:") and value > 0 for name, value in values), evidence
    charts = browser.execute_script(
        """
        return Object.fromEntries(['cpu', 'memory', 'activity', 'agentTokens'].map(name => {
          const chart = document.querySelector(`[data-js-debug-chart="${name}"]`);
          const geometry = [...(chart?.querySelectorAll('[data-js-debug-series], [data-js-debug-area-series], [data-js-debug-bar-series]') || [])]
            .filter(node => node.getAttribute('d') || node.getAttribute('points') || Number(node.getAttribute('height') || 0)).length;
          const rect = chart?.getBoundingClientRect();
          return [name, {present: Boolean(chart), visible: Boolean(rect && rect.width > 0 && rect.height > 0), geometry, waiting: chart?.textContent?.includes('Waiting for server stats') || false}];
        }));
        """
    )
    assert all(item["present"] and item["visible"] and item["geometry"] > 0 and not item["waiting"] for item in charts.values()), {"evidence": evidence, "charts": charts}
    stream_events = browser.execute_script(
        "return (globalThis.__yolomuxStatsStreamEvents || []).map(item => ({event: item.event, data: item.data}));"
    )
    # EventSource can report a connection error before its first successful open. The protocol
    # handshake is the ordered ack/snapshot/ready sequence; a transient pre-open error does not
    # invalidate a generation that subsequently became ready.
    handshake = [item["event"] for item in stream_events if item["event"] in {"ack", "snapshot", "ready"}][:3]
    if handshake != ["ack", "snapshot", "ready"]:
        raise AssertionError(f"initial stats stream did not settle to ack/snapshot/ready: {stream_events}")
    route_counts = browser.execute_script(
        "return {stats: {...(globalThis.__yolomuxStatsRouteCounts || {})}, unrelated: {...(globalThis.__yolomuxStatsUnrelatedRouteCounts || {})}};"
    )
    return {
        "capabilities": evidence["capabilities"],
        "status": evidence["status"],
        "snapshot": {
            "protocol_version": snapshot.get("protocol_version"),
            "range_seconds": snapshot.get("range_seconds"),
            "requested_resolution": snapshot.get("requested_resolution"),
            "resolution_seconds": snapshot.get("resolution_seconds"),
            "buckets": [
                {
                    "duration": bucket.get("duration"),
                    "series": {
                        name: item.get("value")
                        for name, item in bucket.get("series", {}).items()
                        if isinstance(item, dict)
                    },
                }
                for bucket in snapshot.get("buckets", [])
            ],
            "no_data": [
                {
                    key: span.get(key)
                    for key in ("family", "source_id", "reason", "source_cadence_seconds")
                }
                for span in snapshot.get("no_data", [])
            ],
            "cost_report": snapshot.get("cost_report"),
        },
        # Compare the initial browser journey only. Later ready/delta frames are
        # wall-clock dependent: Go may publish the next open bucket while the
        # browser is collecting chart evidence, whereas Python can still be on
        # the same generation. Raw initial SSE parity is covered independently
        # by cmd/yolomux-go/http_server_parity_test.go.
        "stream_events": stream_events[:3],
        "stream_events_observed": stream_events,
        "stream_handshake": handshake,
        "route_counts": route_counts,
    }


def _normalize_stats_capabilities_for_parity(payload: Any) -> Any:
    """Compare the route contract while ignoring each server's request UUID."""
    if not isinstance(payload, dict):
        return payload
    normalized = copy.deepcopy(payload)
    request_record = normalized.get("request")
    if isinstance(request_record, dict) and "id" in request_record:
        request_record["id"] = "<request-id>"
    return normalized


def _normalize_stats_snapshot_for_parity(payload: Any) -> Any:
    """Compare seeded facts while excluding backend-specific live browser telemetry."""
    if not isinstance(payload, dict):
        return payload
    normalized = copy.deepcopy(payload)
    buckets = normalized.get("buckets")
    if isinstance(buckets, list):
        comparable = []
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            series = bucket.get("series")
            if not isinstance(series, dict):
                continue
            bucket["series"] = {name: item for name, item in series.items() if not str(name).startswith("browser_")}
            if bucket["series"]:
                comparable.append(bucket)
        normalized["buckets"] = comparable
    return normalized


def test_production_python_and_go_stats_share_one_fixture_and_browser_journey(browser, tmp_path, monkeypatch, request):
    """The Docker parity gate must exercise production servers, not only a synthetic oracle."""

    configured_binary = os.environ.get("YOLOMUX_GO_BINARY", "").strip()
    if not configured_binary:
        pytest.skip("production Python/Go browser parity requires YOLOMUX_GO_BINARY")
    binary = Path(configured_binary)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise AssertionError(f"YOLOMUX_GO_BINARY is not executable: {binary}")
    source_root = Path(__file__).resolve().parents[1]
    fixture = tmp_path / "fixture.sqlite3"
    _seed_go_stats_fixture(fixture)
    tmux_runtime = start_isolated_tmux_runtime(monkeypatch, tmp_path, session_count=1)
    session = tmux_runtime.sessions[0]
    servers = []
    runtime_dirs = []
    state_roots = []
    try:
        for kind in ("python", "go"):
            root = Path("/tmp") / f"yostats-browser-state-{os.getpid()}-{kind}"
            if root.exists():
                raise AssertionError(f"state root already exists: {root}")
            root.mkdir(parents=True)
            state_roots.append(root)
            config_dir = root / "config"
            config_dir.mkdir(parents=True)
            (root / "home").mkdir()
            (config_dir / "auth.yaml").write_text(
                "users:\n  - username: e2e-admin\n    password: fixture-only-password\n    role: admin\n",
                encoding="utf-8",
            )
            (config_dir / "auth-cookie-secret").write_text("11" * 32, encoding="ascii")
            database = root / "stats-v9.sqlite3"
            runtime_dir = root / "runtime"
            runtime_dir.mkdir()
            runtime_dirs.append(runtime_dir)
            project_root = root / "project"
            for relative in ("static", "tools", "yolomux_lib"):
                shutil.copytree(source_root / relative, project_root / relative)
            monkeypatch.setenv("YOLOMUX_RUNTIME_DIR", str(runtime_dir))
            if kind == "python":
                seed_script = (
                    "import os, shutil\n"
                    "from yolomux_lib.stats_current.storage import default_database_path\n"
                    "destination = default_database_path()\n"
                    "destination.parent.mkdir(parents=True, exist_ok=True)\n"
                    "shutil.copyfile(os.environ['YOLOMUX_STATS_FIXTURE'], destination)\n"
                    "print(destination)\n"
                )
                seed_environment = _synthetic_server_environment()
                seed_environment.update({
                    "HOME": str(root / "home"),
                    "YOLOMUX_ROOT": str(root),
                    "YOLOMUX_CONFIG_DIR": str(config_dir),
                    "YOLOMUX_STATE_DIR": str(root / "state"),
                    "YOLOMUX_CACHE_DIR": str(root / "cache"),
                    "YOLOMUX_RUNTIME_DIR": str(runtime_dir),
                    "YOLOMUX_STATS_FIXTURE": str(fixture),
                    "PYTHONPATH": str(source_root),
                })
                seed_result = subprocess.run(
                    [sys.executable, "-c", seed_script],
                    cwd=source_root,
                    env=seed_environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if seed_result.returncode != 0:
                    raise AssertionError(
                        f"Python stats fixture seed failed ({seed_result.returncode}): "
                        f"stdout={seed_result.stdout!r} stderr={seed_result.stderr!r}"
                    )
                database = Path(
                    subprocess.check_output(
                        [sys.executable, "-c", "from yolomux_lib.stats_current.storage import default_database_path; print(default_database_path())"],
                        cwd=source_root,
                        env=seed_environment,
                        text=True,
                    ).strip()
                )
            else:
                shutil.copyfile(fixture, database)
            process, port_lease, base_url = _start_production_stats_server(
                kind=kind,
                    binary=binary,
                    source_root=source_root,
                    root=root,
                    project_root=project_root,
                database=database,
                config_dir=config_dir,
                tmux_socket=tmux_runtime.socket_path,
                session=session,
                runtime_dir=runtime_dir,
                log_path=tmp_path / f"{kind}-server.log",
            )
            servers.append((process, port_lease))
            browser.get("about:blank")
            servers[-1] = (process, port_lease, _production_stats_browser_pass(browser, base_url, session, request, stats_only=True))
        python_evidence = servers[0][2]
        go_evidence = servers[1][2]
        assert _normalize_stats_capabilities_for_parity(go_evidence["capabilities"]) == _normalize_stats_capabilities_for_parity(python_evidence["capabilities"]), {"python": python_evidence, "go": go_evidence}
        assert _normalize_stats_snapshot_for_parity(go_evidence["snapshot"]) == _normalize_stats_snapshot_for_parity(python_evidence["snapshot"]), {"python": python_evidence, "go": go_evidence}
        python_stream = python_evidence["stream_handshake"]
        go_stream = go_evidence["stream_handshake"]
        assert python_stream == go_stream, {"python_stream": python_evidence["stream_events_observed"], "go_stream": go_evidence["stream_events_observed"]}
        assert python_stream == ["ack", "snapshot", "ready"], python_evidence["stream_events_observed"]
        assert go_stream == ["ack", "snapshot", "ready"], go_evidence["stream_events_observed"]
    finally:
        browser.get("about:blank")
        for process, port_lease, *_rest in reversed(servers):
            _stop_production_stats_server(process, port_lease)
        stop_isolated_tmux_runtime(tmux_runtime)
        for runtime_dir in runtime_dirs:
            shutil.rmtree(runtime_dir, ignore_errors=True)
        for state_root in state_roots:
            shutil.rmtree(state_root, ignore_errors=True)


def _raise_login_page_diagnostic(driver):
    raise AssertionError(
        "Go login page missing form: "
        f"url={driver.current_url!r} title={driver.title!r} "
        f"source={driver.page_source[:1200]!r}"
    )


def _register_stats_route_instrumentation(harness: Any) -> None:
    register_browser_new_document_script(harness.driver, _STATS_ROUTE_INSTRUMENTATION)


def _stats_route_counts(harness: Any) -> dict[str, int]:
    return harness.driver.execute_script("return {...globalThis.__yolomuxStatsRouteCounts};")


def _browser_failure_fingerprint(client: Any, signature: str, minimum_count: int) -> dict[str, Any] | bool:
    response = client.browser_diagnostics()
    status = response.get("observation_status", {})
    for fingerprint in status.get("fingerprints", ()):
        if fingerprint.get("signature") == signature and int(fingerprint.get("count") or 0) >= minimum_count:
            return {"fingerprint": fingerprint, "status": status}
    return False


def test_stats_authentication_expiry_paints_signed_out_and_stops_real_browser_polling(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug&layout=left&tabs=left:__debug__")
    WebDriverWait(browser, 8).until(
        lambda driver: driver.execute_script(
            "return typeof claimTerminalAuthentication === 'function' "
            "&& typeof ensureJsDebugCurrentStatsClient === 'function' "
            "&& typeof apiFetch === 'function' "
            "&& document.querySelector('#status') !== null"
        )
    )
    browser.execute_async_script(
        r"""
        const done = arguments[arguments.length - 1];
        const nativeEventSource = globalThis.EventSource;
        const fixture = {streamRequests: 0, startedAt: Date.now(), nativeEventSource};
        globalThis.EventSource = class AuthenticationFixtureEventSource {
          constructor() {
            this.listeners = new Map();
            this.closed = false;
            fixture.streamRequests += 1;
            queueMicrotask(() => {
              if (!this.closed) this.emit('unavailable', {
                code: 'authentication_required',
                reason: 'authentication_required',
                status_code: 401,
                terminal: true,
              });
            });
          }
          addEventListener(name, callback) {
            const listeners = this.listeners.get(name) || [];
            listeners.push(callback);
            this.listeners.set(name, listeners);
          }
          emit(name, payload) {
            for (const callback of this.listeners.get(name) || []) callback({data: JSON.stringify(payload)});
          }
          close() { this.closed = true; }
        };
        jsDebugCurrentStatsClientState.client?.stop?.();
        jsDebugCurrentStatsClientState.client = null;
        jsDebugCurrentStatsClientState.startPromise = null;
        const client = ensureJsDebugCurrentStatsClient();
        fixture.client = client;
        globalThis.__statsAuthenticationFixture = fixture;
        client.setVisible(true);
        client.start().then(() => done(true), error => done({error: String(error?.message || error)}));
        """
    )
    result = WebDriverWait(browser, 8, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const fixture = globalThis.__statsAuthenticationFixture;
            if (!fixture || Date.now() - fixture.startedAt < 1200) return false;
            const status = document.querySelector('#status');
            return {
              streamRequests: fixture.streamRequests,
              applicationClient: jsDebugCurrentStatsClientState.client === fixture.client,
              authenticationState: document.body?.dataset?.authenticationState || '',
              statusText: status?.textContent || '',
              statusRole: status?.getAttribute('role') || '',
            };
            """
        )
    )
    browser.execute_script(
        "globalThis.__statsAuthenticationFixture.client.stop(); "
        "globalThis.EventSource = globalThis.__statsAuthenticationFixture.nativeEventSource;"
    )

    assert result["streamRequests"] == 1, result
    assert result["applicationClient"] is True, result
    assert result["authenticationState"] == "signed-out", result
    assert result["statusText"] == "Authentication required.", result
    assert result["statusRole"] == "alert", result
    assert browser.execute_script("return jsDebugFailureEvents();") == []


def test_retained_stats_widen_fetches_and_paints_the_full_exact_window(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?debug=1&sessions=debug")
    WebDriverWait(browser, 8).until(
        lambda driver: driver.execute_script(
            "return typeof setDebugGraphRange === 'function' "
            "&& typeof globalThis.YOLOmuxStatsCurrent?.createBrowserClient === 'function' "
            "&& document.querySelector('.js-debug-panel [data-js-debug-graph]') !== null"
        )
    )
    result = browser.execute_async_script(
        r"""
        const done = arguments[arguments.length - 1];
        (async () => {
          const matrix = [
            [300, 1, [1, 10]],
            [900, 10, [10, 60]],
            [3600, 300, [60, 300]],
            [14400, 300, [60, 300]],
            [86400, 300, [300]],
          ];
          const capabilities = {
            resolution_choices: [1, 10, 60, 300],
            max_buckets: 600,
            min_buckets: 12,
            max_live_cadence_seconds: 60,
            ranges: matrix.map(([rangeSeconds, autoResolution, explicitResolutions]) => ({
              range_seconds: rangeSeconds,
              auto_resolution_seconds: autoResolution,
              explicit_resolution_seconds: explicitResolutions,
              buckets: Object.fromEntries(explicitResolutions.map(resolution => [resolution, rangeSeconds / resolution])),
            })),
          };
          const emptyDimensions = () => Object.fromEntries(
            ['input', 'cache_read', 'cache_write_5m', 'cache_write_1h', 'output', 'other'].map(key => [
              key,
              {tokens: 0, micro_usd: 0, api_list_micro_usd: 0},
            ]),
          );
          const costReport = () => ({
            schema_version: 4,
            total_micro_usd: 0,
            total_api_list_micro_usd: 0,
            total_tokens: 0,
            dimensions: emptyDimensions(),
            priced: {atoms: 0, tokens: 0},
            unpriced: {atoms: 0, tokens: 0},
            models: [],
            agents: [],
            evidence: [],
            catalog_revision: 0,
            omissions: {models: 0, agents: 0, evidence: 0},
            reasoning_available: false,
          });
          let generation = 0;
          const snapshot = (rangeSeconds, requestedResolution, resolutionSeconds) => {
            const windowEnd = Math.floor(Date.now() / 1000 / resolutionSeconds) * resolutionSeconds;
            const windowStart = windowEnd - rangeSeconds;
            const bucketCount = rangeSeconds / resolutionSeconds;
            generation += 1;
            return {
              protocol_version: 2,
              range_seconds: rangeSeconds,
              requested_resolution: requestedResolution,
              resolution_seconds: resolutionSeconds,
              window_start: windowStart,
              window_end: windowEnd,
              generated_at: windowEnd,
              source_generation: generation,
              cache_generation: generation,
              rightmost_open: true,
              buckets: Array.from({length: bucketCount}, (_unused, index) => {
                const start = windowStart + index * resolutionSeconds;
                return {
                  start,
                  duration: resolutionSeconds,
                  series: {'cpu_percent:widen-fixture': {
                    value: 20 + index % 5,
                    source_count: 1,
                    first_timestamp: start,
                    last_timestamp: start,
                  }},
                  source: {first_timestamp: start, last_timestamp: start, count: 1},
                  open: index === bucketCount - 1,
                };
              }),
              no_data: [],
              cost_report: costReport(),
            };
          };
          const requests = [];
          const fixtureFetch = async input => {
            const url = new URL(String(input), location.href);
            if (url.pathname === '/api/stats-capabilities') {
              return {status: 200, json: async () => structuredClone(capabilities)};
            }
            return {status: 404, json: async () => ({})};
          };
          class FixtureEventSource {
            constructor(input) {
              this.url = String(input);
              this.listeners = new Map();
              this.closed = false;
              queueMicrotask(() => {
                if (this.closed) return;
                const url = new URL(this.url, location.href);
                const rangeSeconds = Number(url.searchParams.get('range_seconds'));
                const requestedText = url.searchParams.get('resolution');
                const requestedResolution = requestedText === 'AUTO' ? 'AUTO' : Number(requestedText);
                const capability = capabilities.ranges.find(row => row.range_seconds === rangeSeconds);
                const resolutionSeconds = requestedResolution === 'AUTO'
                  ? capability.auto_resolution_seconds
                  : requestedResolution;
                const accepted = snapshot(rangeSeconds, requestedResolution, resolutionSeconds);
                const retainedKeys = [...jsDebugGraphBuckets.keys()];
                const scrollOwner = document.querySelector('.js-debug-graph-view');
                const retainedScrollLeft = Number(scrollOwner?.scrollLeft) || 0;
                const requestEvidence = {
                  rangeSeconds,
                  requestedResolution,
                  resolutionSeconds,
                  sinceGeneration: Number(url.searchParams.get('since_generation')),
                  bucketCount: accepted.buckets.length,
                  windowStart: accepted.window_start,
                  windowEnd: accepted.window_end,
                };
                requests.push(requestEvidence);
                const splitIndex = Math.floor(accepted.buckets.length / 2);
                const chunks = [accepted.buckets.slice(0, splitIndex), accepted.buckets.slice(splitIndex)].map((buckets, chunkIndex) => ({
                  ...accepted,
                  chunk_index: chunkIndex,
                  chunk_count: 2,
                  chunk_start: buckets[0].start,
                  chunk_end: buckets.at(-1).start + buckets.at(-1).duration,
                  buckets,
                }));
                this.emit('ack', {
                  cache_generation: accepted.cache_generation,
                  chunk_count: 2,
                  not_modified: false,
                  range_seconds: rangeSeconds,
                  requested_resolution: requestedResolution,
                  resolution_seconds: resolutionSeconds,
                });
                this.emit('snapshot', chunks[0]);
                requestEvidence.retainedAfterFirstChunk = JSON.stringify([...jsDebugGraphBuckets.keys()]) === JSON.stringify(retainedKeys);
                requestEvidence.scrollAfterFirstChunk = Number(scrollOwner?.scrollLeft) || 0;
                requestEvidence.retainedScrollLeft = retainedScrollLeft;
                this.emit('snapshot', chunks[1]);
                this.emit('ready', {cache_generation: accepted.cache_generation, revision: 0});
              });
            }
            addEventListener(name, callback) {
              const listeners = this.listeners.get(name) || [];
              listeners.push(callback);
              this.listeners.set(name, listeners);
            }
            emit(name, payload) {
              for (const callback of this.listeners.get(name) || []) callback({data: JSON.stringify(payload)});
            }
            close() { this.closed = true; }
          }
          const waitForGeneration = async (rangeSeconds, resolutionSeconds) => {
            await window.__yolomuxTestWaitFor(
              () => {
                const accepted = jsDebugCurrentStatsClientState.client?.controller?.()?.generation?.();
                return accepted?.range_seconds === rangeSeconds
                  && accepted?.resolution_seconds === resolutionSeconds
                  && jsDebugHistoryReadiness.phase === 'ready';
              },
              {timeoutMs: 3000, intervalMs: 10, description: `retained ${rangeSeconds}/${resolutionSeconds} generation`},
            );
          };
          const warm = async (rangeSeconds, resolutionSeconds) => {
            setDebugGraphRange(rangeSeconds);
            if (Number(debugRuntimeState.graphResolutionOverrideSeconds) !== resolutionSeconds) {
              setDebugGraphResolutionOverride(resolutionSeconds);
            }
            await waitForGeneration(rangeSeconds, resolutionSeconds);
          };
          const widen = async (fromRange, fromResolution, toRange, toResolution) => {
            await warm(fromRange, fromResolution);
            const before = requests.length;
            const scrollOwner = document.querySelector('.js-debug-graph-view');
            if (scrollOwner) scrollOwner.scrollLeft = Math.max(0, scrollOwner.scrollWidth - scrollOwner.clientWidth) / 2;
            setDebugGraphRange(toRange);
            const loadingState = jsDebugHistoryReadinessStateName();
            await waitForGeneration(toRange, toResolution);
            refreshDebugGraphSurfaces({force: true, deferFocusedControl: false});
            const targetRequests = requests.slice(before).filter(request => request.rangeSeconds === toRange);
            const cpuBuckets = [...jsDebugGraphBuckets.values()]
              .filter(bucket => Number(bucket.cpuCount) > 0)
              .sort((left, right) => Number(left.startMs) - Number(right.startMs));
            const graph = document.querySelector('.js-debug-panel [data-js-debug-graph]');
            const overlay = graph?.querySelector('[data-js-debug-history-overlay]');
            return {
              fromRange,
              toRange,
              toResolution,
              loadingState,
              requests: targetRequests,
              paintedBucketCount: cpuBuckets.length,
              paintedStart: Number(cpuBuckets[0]?.startMs) / 1000,
              paintedEnd: (Number(cpuBuckets.at(-1)?.startMs) + Number(cpuBuckets.at(-1)?.durationMs)) / 1000,
              readiness: jsDebugHistoryReadinessStateName(),
              busy: graph?.getAttribute('aria-busy') || '',
              overlayVisible: Boolean(overlay && !overlay.hidden),
            };
          };

          jsDebugCurrentStatsClientState.client?.stop();
          stopJsDebugStatsPolling();
          clearJsDebugGraphData();
          resetJsDebugHistoryReadiness();
          debugRuntimeState.graphRangeSeconds = 900;
          debugRuntimeState.graphResolutionOverrideSeconds = 10;
          const client = YOLOmuxStatsCurrent.createBrowserClient({
            fetch: fixtureFetch,
            EventSource: FixtureEventSource,
            clientId: 'retained-widen-fixture',
            savedRange: 900,
            savedResolution: 10,
            controllerOptions: {
              onGeneration: accepted => applyJsDebugCurrentSnapshot(accepted, {forceGraphRefresh: true}),
            },
          });
          jsDebugCurrentStatsClientState.client = client;
          jsDebugCurrentStatsClientState.selectionKey = '900:10';
          jsDebugCurrentStatsClientState.startPromise = null;
          await client.start();
          await waitForGeneration(900, 10);

          const transitions = [];
          transitions.push(await widen(900, 10, 14400, 300));
          transitions.push(await widen(300, 1, 3600, 300));
          transitions.push(await widen(3600, 60, 86400, 300));
          await warm(900, 10);
          await warm(300, 1);
          refreshDebugGraphSurfaces({force: true, deferFocusedControl: false});
          const intervalOptionsAfterRoundTrip = [...document.querySelectorAll('[data-js-debug-resolution-override] option')]
            .map(option => option.textContent.trim());
          const idleRequest = client.controller().buildRequest();
          const legacyFetch = apiFetchJsonQuiet;
          const legacySnapshotRequests = [];
          apiFetchJsonQuiet = async (url, ...args) => {
            if (String(url).startsWith('/api/stats-snapshot?')) legacySnapshotRequests.push(String(url));
            return legacyFetch(url, ...args);
          };
          await Promise.all(Array.from({length: 8}, () => pollJsDebugStatsSample()));
          apiFetchJsonQuiet = legacyFetch;
          client.stop();
          done({transitions, intervalOptionsAfterRoundTrip, idleRequest, legacySnapshotRequests});
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert result.get("error") is None, result
    assert "1s" in result["intervalOptionsAfterRoundTrip"], result
    assert result["idleRequest"]["since_generation"] > 0, result
    assert result["legacySnapshotRequests"] == [], result
    assert len(result["transitions"]) == 3, result
    for transition in result["transitions"]:
        expected_buckets = transition["toRange"] // transition["toResolution"]
        assert transition["loadingState"] == "loading-older", transition
        assert len(transition["requests"]) == 1, transition
        request = transition["requests"][0]
        assert request["sinceGeneration"] == 0, transition
        assert request["resolutionSeconds"] == transition["toResolution"], transition
        assert request["bucketCount"] == expected_buckets, transition
        assert request["retainedAfterFirstChunk"] is True, transition
        assert request["scrollAfterFirstChunk"] == request["retainedScrollLeft"], transition
        assert transition["paintedBucketCount"] == expected_buckets, transition
        assert transition["paintedStart"] == request["windowStart"], transition
        assert transition["paintedEnd"] == request["windowEnd"], transition
        assert transition["readiness"] == "ready", transition
        assert transition["busy"] == "false", transition
        assert transition["overlayVisible"] is False, transition


def _open_real_stats_graph(harness: Any, *, include_finder: bool = True) -> None:
    session = str(harness.runtime.tmux.sessions[0])
    if include_finder:
        harness.load(tabs=("files", "__debug__", session))
    else:
        harness.driver.get(
            f"{harness.base_url}/?sessions={session}&layout=left&tabs=left:__debug__,{session}"
        )
        WebDriverWait(harness.driver, 12, poll_frequency=0.05).until(
            lambda driver: driver.execute_script(
                "return document.readyState === 'complete' "
                "&& typeof syncJsDebugCurrentStatsClient === 'function' "
                "&& document.querySelector('#grid') !== null;"
            )
        )
    harness.switch_session("__debug__")
    result = harness.driver.execute_async_script(
        r"""
        const done = arguments[arguments.length - 1];
        const graphTab = document.querySelector('[data-js-debug-subtab="graph"]');
        graphTab?.click();
        window.__yolomuxTestWaitFor(() => {
          const client = jsDebugCurrentStatsClientState?.client;
          const select = document.querySelector('.js-debug-panel [data-js-debug-resolution-override]');
          return client?.controller?.()?.generation?.() && select ? true : null;
        }, {timeoutMs: 12000, description: 'real YO!stats generation and interval picker'})
          .then(() => done({ok: true}), error => done({error: String(error?.stack || error)}));
        """
    )
    assert result == {"ok": True}, result


def _wait_for_real_stats_fixture_jobs(harness: Any) -> dict[str, Any]:
    def settled(_driver):
        status = harness.runtime.app.job_client.runtime_status()
        queues = status.get("queues") if isinstance(status.get("queues"), dict) else {}
        if status.get("active_records") or any(int(count or 0) for count in queues.values()):
            return False
        return status

    return WebDriverWait(harness.driver, 20, poll_frequency=0.1).until(settled)


def _flush_and_stop_real_stats_graph(harness: Any, *, require_client: bool = False) -> dict[str, Any]:
    try:
        receipt = harness.driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const client = jsDebugCurrentStatsClientState.client;
            const startPromise = jsDebugCurrentStatsClientState.startPromise;
            const lifecycle = {
              hadClient: Boolean(client),
              startPending: Boolean(startPromise),
              hadGeneration: Boolean(client?.controller?.()?.generation?.()),
            };
            const errors = [];
            (async () => {
              try {
                if (startPromise) await startPromise;
              } catch (error) {
                errors.push({phase: 'start', error: String(error?.stack || error)});
              } finally {
                try {
                  client?.stop?.();
                } catch (error) {
                  errors.push({phase: 'stop', error: String(error?.stack || error)});
                }
              }
              try {
                await flushJsDebugCurrentObservations();
              } catch (error) {
                errors.push({phase: 'flush', error: String(error?.stack || error)});
              }
              const barrier = jsDebugCurrentObservationReceiptBarrier();
              done({lifecycle, errors, barrier});
            })().catch(error => done({fatalError: String(error?.stack || error), lifecycle, errors}));
            """
        )
    finally:
        try:
            wait_for_fixture_api_quiescence(harness.driver)
        finally:
            _wait_for_real_stats_fixture_jobs(harness)
    if receipt.get("fatalError") is not None or receipt["errors"]:
        raise AssertionError(f"real stats cleanup failed: {json.dumps(receipt, sort_keys=True)}")
    assert receipt["barrier"]["quiescent"] is True, receipt
    if require_client:
        assert receipt["lifecycle"]["hadClient"] is True, receipt
    return receipt


def _exercise_real_stats_interval_round_trip(harness: Any) -> dict[str, Any]:
    return harness.driver.execute_async_script(
        r"""
        const done = arguments[arguments.length - 1];
        (async () => {
          const capabilitiesResponse = await fetch('/api/stats-capabilities', {cache: 'no-store'});
          const capabilities = await capabilitiesResponse.json();
          const capability = capabilities.ranges.find(row => row.range_seconds === 300);
          if (capabilitiesResponse.status !== 200 || !capability) {
            throw new Error(`5-minute stats capability missing: ${capabilitiesResponse.status}`);
          }
          const waitForSelection = async rangeSeconds => {
            await window.__yolomuxTestWaitFor(() => {
              const controller = jsDebugCurrentStatsClientState?.client?.controller?.();
              const selection = controller?.selection?.();
              const generation = controller?.generation?.();
              const select = document.querySelector('.js-debug-panel [data-js-debug-resolution-override]');
              return selection?.range_seconds === rangeSeconds
                && generation?.range_seconds === rangeSeconds
                && jsDebugHistoryReadiness?.phase === 'ready'
                && select ? true : null;
            }, {timeoutMs: 12000, description: `real YO!stats ${rangeSeconds}s selection`});
          };
          const setRange = async rangeSeconds => {
            const options = [...document.querySelectorAll('.js-debug-panel #js-debug-range-options option')];
            const index = options.findIndex(option => Number(option.dataset.jsDebugRange) === rangeSeconds);
            if (index < 0) throw new Error(`range ${rangeSeconds}s is not offered`);
            const slider = document.querySelector('.js-debug-panel [data-js-debug-range-slider]');
            slider.focus({preventScroll: true});
            slider.value = String(index);
            slider.dispatchEvent(new Event('input', {bubbles: true}));
            slider.dispatchEvent(new Event('change', {bubbles: true}));
            await waitForSelection(rangeSeconds);
          };
          const setResolution = async resolutionSeconds => {
            const select = document.querySelector('.js-debug-panel [data-js-debug-resolution-override]');
            if (![...select.options].some(option => Number(option.value) === resolutionSeconds)) {
              throw new Error(`resolution ${resolutionSeconds}s is not offered`);
            }
            select.focus({preventScroll: true});
            select.value = String(resolutionSeconds);
            select.dispatchEvent(new Event('change', {bubbles: true}));
            await window.__yolomuxTestWaitFor(() => {
              const controller = jsDebugCurrentStatsClientState?.client?.controller?.();
              const generation = controller?.generation?.();
              return controller?.selection?.()?.resolution === resolutionSeconds
                && generation?.requested_resolution === resolutionSeconds
                && jsDebugHistoryReadiness?.phase === 'ready' ? true : null;
            }, {timeoutMs: 12000, description: `real YO!stats ${resolutionSeconds}s resolution`});
          };
          const pickerState = label => {
            const controller = jsDebugCurrentStatsClientState.client.controller();
            const generation = controller.generation();
            const select = document.querySelector('.js-debug-panel [data-js-debug-resolution-override]');
            return {
              label,
              options: [...select.options].map(option => option.value),
              selected: select.value,
              selection: controller.selection(),
              generation: {
                range_seconds: generation.range_seconds,
                requested_resolution: generation.requested_resolution,
                resolution_seconds: generation.resolution_seconds,
                buckets: generation.buckets.length,
                source_generation: generation.source_generation,
              },
            };
          };

          await setRange(300);
          await setResolution(1);
          const before = pickerState('before');
          await setRange(3600);
          const widened = pickerState('widened');
          await setRange(300);
          const returned = pickerState('returned');
          done({
            expected: ['0', ...capability.explicit_resolution_seconds.map(String)],
            before,
            widened,
            returned,
          });
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )


def _real_stats_picker_state(harness: Any) -> dict[str, Any]:
    return harness.driver.execute_script(
        """
        const controller = jsDebugCurrentStatsClientState.client.controller();
        const generation = controller.generation();
        const select = document.querySelector('.js-debug-panel [data-js-debug-resolution-override]');
        return {
          options: [...select.options].map(option => option.value),
          selected: select.value,
          range_seconds: generation.range_seconds,
          resolution_seconds: generation.resolution_seconds,
          buckets: generation.buckets.length,
          source_generation: generation.source_generation,
        };
        """
    )


def _wait_for_ring_publication(harness: Any, source_generation: int) -> dict[str, Any]:
    client = harness.runtime.app.stats_current_client

    def published(_driver):
        status = client.status()
        ring = status.get("ring_writer") if isinstance(status, dict) else None
        if not isinstance(ring, dict):
            return False
        if int(ring.get("publications") or 0) < 1:
            return False
        if int(ring.get("last_source_generation") or 0) < source_generation:
            return False
        return status

    return WebDriverWait(harness.driver, 20, poll_frequency=0.1).until(published)


def _cpu_graph_state(harness: Any) -> dict[str, Any]:
    return harness.driver.execute_script(
        r"""
        const graph = document.querySelector('.js-debug-panel [data-js-debug-graph]');
        const cpuChart = document.querySelector('.js-debug-panel [data-js-debug-chart="cpu"]');
        const cpuAxisMax = Number(cpuChart?.dataset?.jsDebugChartAxisMax || 0);
        const lines = [...document.querySelectorAll('.js-debug-panel polyline[data-js-debug-series]')];
        const cpuLines = lines.filter(line => line.dataset.jsDebugSeries === 'systemCpu');
        const systemLine = cpuLines[0] || null;
        const systemStyle = systemLine ? getComputedStyle(systemLine) : null;
        const colorProbe = document.createElement('span');
        colorProbe.style.color = systemStyle?.getPropertyValue('--js-debug-agent-token-rose') || '';
        document.body.append(colorProbe);
        const expectedSystemStroke = getComputedStyle(colorProbe).color;
        colorProbe.remove();
        const points = cpuLines.flatMap(line => String(line.getAttribute('points') || '')
          .trim().split(/\s+/).filter(Boolean).map(point => {
            const [x, y] = point.split(',').map(Number);
            return {x, y, percent: (116 - y) / 108 * cpuAxisMax};
          }));
        const overlay = graph?.querySelector('[data-js-debug-history-overlay]');
        const warnings = (typeof jsDebugEvents !== 'undefined' ? jsDebugEvents : [])
          .filter(event => event?.type === 'stats_history' && event?.level === 'warning')
          .map(event => String(event.message || ''));
        return {
          lineCount: lines.length,
          seriesKeys: [...new Set(lines.map(line => line.dataset.jsDebugSeries))],
          cpuPointCount: points.length,
          cpuAxisMax,
          points,
          systemStroke: systemStyle?.stroke || '',
          expectedSystemStroke,
          systemDasharray: systemStyle?.strokeDasharray || '',
          systemLinePattern: systemLine?.dataset?.jsDebugLinePattern || '',
          historyState: graph?.dataset?.jsDebugHistoryState || '',
          busy: graph?.getAttribute('aria-busy') || '',
          overlayHidden: overlay?.hidden === true,
          warnings,
        };
        """
    )


def _assert_seeded_cpu_graph(state: dict[str, Any]) -> None:
    assert state["lineCount"] > 0, state
    assert "systemCpu" in state["seriesKeys"], state
    assert state["cpuPointCount"] >= CPU_GRAPH_SAMPLE_COUNT - 1, state
    assert state["cpuAxisMax"] == 50, state
    assert all(8 <= point["y"] <= 116 for point in state["points"]), state
    assert all(abs(point["percent"] - CPU_GRAPH_PERCENT) < 0.2 for point in state["points"]), state
    assert state["systemStroke"] == state["expectedSystemStroke"], state
    assert state["systemLinePattern"] == "dot", state
    assert state["systemDasharray"] not in {"", "none", "0px"}, state
    assert state["historyState"] == "ready", state
    assert state["busy"] == "false", state
    assert state["overlayHidden"] is True, state
    assert state["warnings"] == [], state


def _wait_for_cpu_points(harness: Any) -> dict[str, Any] | bool:
    state = _cpu_graph_state(harness)
    return state if state["cpuPointCount"] >= CPU_GRAPH_SAMPLE_COUNT - 1 else False


def _wait_for_stats_warning(harness: Any) -> dict[str, Any] | bool:
    state = _cpu_graph_state(harness)
    return state if state["warnings"] else False


def test_real_stats_cleanup_settles_start_and_stops_before_final_flush(authenticated_e2e_browser: Any) -> None:
    wait_for_fixture_api_quiescence(authenticated_e2e_browser.driver, timeout=20)
    _open_real_stats_graph(authenticated_e2e_browser, include_finder=False)
    wait_for_fixture_api_quiescence(authenticated_e2e_browser.driver, timeout=20)
    _flush_and_stop_real_stats_graph(authenticated_e2e_browser, require_client=True)
    authenticated_e2e_browser.driver.execute_script(
        """
        globalThis.__statsCleanupFixture = {
          client: jsDebugCurrentStatsClientState.client,
          startPromise: jsDebugCurrentStatsClientState.startPromise,
          flush: flushJsDebugCurrentObservations,
          markers: [],
        };
        jsDebugCurrentStatsClientState.client = {
          stop() { globalThis.__statsCleanupFixture.markers.push('stop'); },
          controller() { return null; },
        };
        jsDebugCurrentStatsClientState.startPromise = {
          then(_resolve, reject) {
            globalThis.__statsCleanupFixture.markers.push('start-rejected');
            reject(new Error('pending start rejection'));
          },
        };
        flushJsDebugCurrentObservations = () => {
          globalThis.__statsCleanupFixture.markers.push('flush-rejected');
          return Promise.reject(new Error('final flush rejection'));
        };
        """
    )
    try:
        with pytest.raises(AssertionError) as cleanup_error:
            _flush_and_stop_real_stats_graph(authenticated_e2e_browser, require_client=True)
        assert "pending start rejection" in str(cleanup_error.value), cleanup_error.value
        assert "final flush rejection" in str(cleanup_error.value), cleanup_error.value
        markers = authenticated_e2e_browser.driver.execute_script(
            "return [...globalThis.__statsCleanupFixture.markers];"
        )
        assert markers == ["start-rejected", "stop", "flush-rejected"], markers
    finally:
        authenticated_e2e_browser.driver.execute_script(
            """
            jsDebugCurrentStatsClientState.client = globalThis.__statsCleanupFixture.client;
            jsDebugCurrentStatsClientState.startPromise = globalThis.__statsCleanupFixture.startPromise;
            flushJsDebugCurrentObservations = globalThis.__statsCleanupFixture.flush;
            delete globalThis.__statsCleanupFixture;
            """
        )
    _flush_and_stop_real_stats_graph(authenticated_e2e_browser, require_client=True)


def test_hidden_stats_panel_receives_push_then_opens_from_cached_generation(
    authenticated_e2e_browser: Any,
    request: pytest.FixtureRequest,
) -> None:
    runtime = authenticated_e2e_browser.runtime
    client = runtime.app.stats_current_client
    assert client.ensure_started() is True
    _flush_and_stop_real_stats_graph(authenticated_e2e_browser, require_client=True)
    _wait_for_ring_publication(authenticated_e2e_browser, 0)
    request.addfinalizer(
        lambda: _flush_and_stop_real_stats_graph(authenticated_e2e_browser, require_client=True)
    )
    _register_stats_route_instrumentation(authenticated_e2e_browser)
    authenticated_e2e_browser.driver.execute_script(
        "localStorage.setItem('yolomux.stats.ui_preferences.v1', "
        "JSON.stringify({rangeSeconds: 300, resolutionOverrideSeconds: 1}));"
    )
    session = str(runtime.tmux.sessions[0])
    authenticated_e2e_browser.driver.get(
        f"{authenticated_e2e_browser.base_url}/?sessions={session}&layout=left&tabs=left:__debug__,{session}"
    )
    WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            "return document.readyState === 'complete' "
            "&& typeof syncJsDebugCurrentStatsClient === 'function' "
            "&& document.querySelector('#grid') !== null;"
        )
    )
    authenticated_e2e_browser.switch_session(session)
    hidden = WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const controller = jsDebugCurrentStatsClientState?.client?.controller?.();
            const generation = controller?.generation?.();
            if (!generation || jsDebugStatsPanelVisible()) return false;
            return {
              cacheGeneration: generation.cache_generation,
              sourceGeneration: generation.source_generation,
              rangeSeconds: generation.range_seconds,
              resolutionSeconds: generation.resolution_seconds,
              paintedGenerationKey: jsDebugCurrentStatsClientState.paintedGenerationKey,
              requestCounts: {...globalThis.__yolomuxStatsRouteCounts},
            };
            """
        )
    )
    assert hidden["rangeSeconds"] == 300 and hidden["resolutionSeconds"] == 1, hidden
    assert hidden["requestCounts"]["/api/stats-capabilities"] == 1, hidden
    assert hidden["requestCounts"]["/api/stats-snapshot"] == 0, hidden
    assert hidden["requestCounts"]["/api/stats-retry"] == 0, hidden
    assert hidden["requestCounts"]["/api/stats-stream"] == 1, hidden
    observed_at = int(time.time())
    cadence_started = time.monotonic()
    appended = client.append(observations=(storage.Observation(
        f"hidden-panel-push-{observed_at}",
        "cpu",
        "hidden-panel-push",
        observed_at,
        "hidden-panel-push",
        1,
        {"process_percent": 19.0, "system_percent": 29.0},
    ),))
    assert appended.get("ok") is True, appended
    pushed = WebDriverWait(authenticated_e2e_browser.driver, 6, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const generation = jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.();
            if (!generation || generation.source_generation < arguments[0]) return false;
            return {
              cacheGeneration: generation.cache_generation,
              sourceGeneration: generation.source_generation,
              paintedGenerationKey: jsDebugCurrentStatsClientState.paintedGenerationKey,
            };
            """,
            int(appended["source_generation"]),
        )
    )
    assert pushed["paintedGenerationKey"] == hidden["paintedGenerationKey"], pushed
    assert _stats_route_counts(authenticated_e2e_browser) == hidden["requestCounts"], pushed

    hidden_updates = [pushed]
    for offset in range(1, 4):
        append_deadline = cadence_started + (offset * 1.05)
        WebDriverWait(authenticated_e2e_browser.driver, 5, poll_frequency=0.05).until(
            lambda _driver, deadline=append_deadline: time.monotonic() >= deadline
        )
        appended = client.append(observations=(storage.Observation(
            f"hidden-panel-push-{observed_at + offset}",
            "cpu",
            "hidden-panel-push",
            observed_at + offset,
            "hidden-panel-push",
            1,
            {
                "process_percent": 19.0 + offset,
                "system_percent": 29.0 + offset,
            },
        ),))
        assert appended.get("ok") is True, appended
        previous = hidden_updates[-1]
        advanced = WebDriverWait(
            authenticated_e2e_browser.driver,
            6,
            poll_frequency=0.05,
        ).until(
            lambda driver: driver.execute_script(
                """
                const generation = jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.();
                if (!generation
                    || generation.source_generation < arguments[0]
                    || generation.cache_generation <= arguments[1]) return false;
                return {
                  cacheGeneration: generation.cache_generation,
                  sourceGeneration: generation.source_generation,
                  paintedGenerationKey: jsDebugCurrentStatsClientState.paintedGenerationKey,
                  panelVisible: jsDebugStatsPanelVisible(),
                };
                """,
                int(appended["source_generation"]),
                previous["cacheGeneration"],
            )
        )
        assert advanced["paintedGenerationKey"] == hidden["paintedGenerationKey"], advanced
        assert advanced["panelVisible"] is False, advanced
        assert _stats_route_counts(authenticated_e2e_browser) == hidden["requestCounts"], advanced
        hidden_updates.append(advanced)

    continued = authenticated_e2e_browser.driver.execute_script(
        """
        const generation = jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.();
        const stalls = jsDebugEvents.filter(event =>
          String(event?.route || '') === '/api/stats-stream'
          && /YO!stats (stream|data).*(stalled|missing|unavailable)/i.test(String(event?.message || '')));
        return {
          cacheGeneration: generation?.cache_generation || 0,
          paintedGenerationKey: jsDebugCurrentStatsClientState.paintedGenerationKey,
          panelVisible: jsDebugStatsPanelVisible(),
          stalls: stalls.map(event => String(event.message || '')),
          requestCounts: {...globalThis.__yolomuxStatsRouteCounts},
        };
        """
    )
    assert [update["cacheGeneration"] for update in hidden_updates] == sorted({
        update["cacheGeneration"] for update in hidden_updates
    }), hidden_updates
    assert continued["cacheGeneration"] == hidden_updates[-1]["cacheGeneration"], continued
    assert continued["paintedGenerationKey"] == hidden["paintedGenerationKey"], continued
    assert continued["panelVisible"] is False, continued
    assert continued["stalls"] == [], continued
    assert continued["requestCounts"] == hidden["requestCounts"], continued
    before_open_counts = _stats_route_counts(authenticated_e2e_browser)

    authenticated_e2e_browser.switch_session("__debug__")
    opened = WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const controller = jsDebugCurrentStatsClientState?.client?.controller?.();
            const generation = controller?.generation?.();
            const expected = jsDebugCurrentStatsGenerationKey(generation);
            if (!jsDebugStatsPanelVisible() || !expected || jsDebugCurrentStatsClientState.paintedGenerationKey !== expected) return false;
            return {
              cacheGeneration: generation.cache_generation,
              sourceGeneration: generation.source_generation,
              paintedGenerationKey: jsDebugCurrentStatsClientState.paintedGenerationKey,
              requestCounts: {...globalThis.__yolomuxStatsRouteCounts},
            };
            """
        )
    )
    assert opened["cacheGeneration"] == continued["cacheGeneration"], opened
    assert opened["sourceGeneration"] >= pushed["sourceGeneration"], opened
    assert opened["requestCounts"] == before_open_counts, opened
    _flush_and_stop_real_stats_graph(authenticated_e2e_browser, require_client=True)


def test_stats_stream_failure_is_durable_latched_and_recovers_only_after_a_real_push(
    authenticated_e2e_browser: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = authenticated_e2e_browser.runtime
    client = runtime.app.stats_current_client
    assert client.ensure_started() is True
    original_capabilities = runtime.app.stats_current_http.capabilities
    original_delta_stream = runtime.app.stats_current_http.delta_stream

    def invalid_capabilities() -> dict[str, object]:
        payload = dict(original_capabilities())
        payload["invalid_review_field"] = True
        return payload

    monkeypatch.setattr(runtime.app.stats_current_http, "capabilities", invalid_capabilities)
    _register_stats_route_instrumentation(authenticated_e2e_browser)
    session = str(runtime.tmux.sessions[0])
    authenticated_e2e_browser.driver.execute_script(
        "localStorage.setItem('yolomux.stats.ui_preferences.v1', "
        "JSON.stringify({rangeSeconds: 300, resolutionOverrideSeconds: 1}));"
    )
    install_live_runtime_boot_error_tracker(authenticated_e2e_browser.driver)
    authenticated_e2e_browser.driver.get(
        f"{authenticated_e2e_browser.base_url}/?sessions={session}&layout=left&tabs=left:files,__debug__,{session}"
    )
    WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            "return document.readyState === 'complete' "
            "&& typeof syncJsDebugCurrentStatsClient === 'function' "
            "&& document.querySelector('#grid') !== null;"
        )
    )

    failed = WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const failures = jsDebugEvents.filter(event => event?.type === 'stats_history'
              && event?.level === 'warning' && event?.route === '/api/stats-stream'
              && String(event?.message || '').includes('stats capabilities fields are not exact'));
            const capabilities = globalThis.__yolomuxStatsRouteCounts?.['/api/stats-capabilities'] || 0;
            if (failures.length !== 1 || capabilities < 2) return false;
            const event = failures[0];
            return {
              count: failures.length,
              signature: event.signature,
              source: event.route,
              message: event.message,
              marked: Object.prototype.hasOwnProperty.call(event, 'provenance'),
              capabilities,
              streamEvidence: event.streamEvidence ?? null,
              liveEvidenceKeys: Object.keys(
                jsDebugCurrentStatsClientState?.client?.streamEvidence?.() || {}
              ).sort(),
            };
            """
        )
    )
    assert failed["source"] == "/api/stats-stream", failed
    assert failed["marked"] is False, failed
    # A retained stream failure that records only its message string cannot be classified
    # afterwards. The transport samples its own first-transition observables at report time,
    # so the retained record must carry that whole snapshot, not a hand-picked subset.
    assert isinstance(failed["streamEvidence"], dict), failed
    assert sorted(failed["streamEvidence"]) == failed["liveEvidenceKeys"], failed
    assert failed["streamEvidence"]["running"] is True, failed
    assert isinstance(failed["streamEvidence"]["deliverySequence"], int), failed
    assert isinstance(failed["streamEvidence"]["streamEpoch"], int), failed

    receipt = WebDriverWait(authenticated_e2e_browser.driver, 18, poll_frequency=0.2).until(
        lambda driver: driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            Promise.resolve(typeof flushJsDebugCurrentObservations === 'function'
              ? flushJsDebugCurrentObservations() : undefined).then(() => {
                const barrier = jsDebugCurrentObservationReceiptBarrier();
                done(barrier.quiescent && barrier.accepted > 0 ? barrier : false);
              }, () => done(false));
            """
        )
    )
    assert receipt["quiescent"] is True and receipt["accepted"] > 0, receipt

    retained = WebDriverWait(authenticated_e2e_browser.driver, 18, poll_frequency=0.2).until(
        lambda _driver: _browser_failure_fingerprint(client, failed["signature"], 1)
    )
    assert retained["fingerprint"]["signature"] == failed["signature"], retained
    assert retained["fingerprint"]["kind"] == "warning", retained
    assert retained["fingerprint"]["provenance"] == "unknown", retained

    def ready_only_delta_stream(
        _raw_query: str,
        *,
        authenticated_username: str,
    ) -> stats_current_http.DeltaStreamResult:
        assert authenticated_username
        return stats_current_http.DeltaStreamResult(
            HTTPStatus.NOT_MODIFIED,
            {"ok": True, "not_modified": True, "cache_generation": 0},
        )

    # A prior test may have left a publishable generation in the fixture-owned
    # service. Hold this stream at a validated ready heartbeat so the assertion
    # below proves snapshot readiness cannot clear the latch; the test injects
    # its accepted push explicitly after taking the stream base.
    monkeypatch.setattr(
        runtime.app.stats_current_http,
        "delta_stream",
        ready_only_delta_stream,
    )
    monkeypatch.setattr(runtime.app.stats_current_http, "capabilities", original_capabilities)
    healthy = WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const generation = jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.();
            return generation ? {
              sourceGeneration: generation.source_generation,
              rangeSeconds: generation.range_seconds,
              resolutionSeconds: generation.resolution_seconds,
              failureLatched: jsDebugCurrentStatsClientState.failureLatched,
            } : false;
            """
        )
    )
    assert healthy["rangeSeconds"] == 300 and healthy["resolutionSeconds"] == 1, healthy
    assert healthy["failureLatched"] is True, healthy
    healthy_counts = _stats_route_counts(authenticated_e2e_browser)
    assert set(healthy_counts) == {
        "/api/stats-capabilities", "/api/stats-snapshot", "/api/stats-retry", "/api/stats-stream",
    }, healthy_counts
    assert healthy_counts["/api/stats-capabilities"] >= 2, healthy_counts
    assert healthy_counts["/api/stats-snapshot"] == 0, healthy_counts
    assert healthy_counts["/api/stats-retry"] == 0, healthy_counts
    assert healthy_counts["/api/stats-stream"] >= 1, healthy_counts
    stream_base = authenticated_e2e_browser.driver.execute_script(
        """
        const client = jsDebugCurrentStatsClientState?.client;
        const controller = client?.controller?.();
        const generation = controller?.generation?.();
        const presentation = controller?.presentation?.();
        const evidence = client?.streamEvidence?.();
        return generation && presentation ? {
          generation: structuredClone(generation),
          revision: presentation.delta_revision,
          streamEpoch: evidence?.streamEpoch,
        } : null;
        """
    )
    assert stream_base and stream_base["generation"]["buckets"], stream_base
    generation = stream_base["generation"]
    streamed_cache_generation = int(generation["cache_generation"]) + 1
    streamed_source_generation = int(generation["source_generation"]) + 1
    streamed_revision = int(stream_base["revision"]) + 1
    streamed_delta = json.dumps({
        "protocol_version": 2,
        "range_seconds": generation["range_seconds"],
        "resolution_seconds": generation["resolution_seconds"],
        "source_generation": streamed_source_generation,
        "base_cache_generation": generation["cache_generation"],
        "cache_generation": streamed_cache_generation,
        "revision": streamed_revision,
        "buckets": [generation["buckets"][-1]],
        "no_data": [],
        "tombstones": [],
        "cost_report": generation["cost_report"],
    }, separators=(",", ":")).encode("utf-8")
    streamed = False

    def accepted_delta_stream(
        _raw_query: str,
        *,
        authenticated_username: str,
    ) -> stats_current_http.DeltaStreamResult:
        nonlocal streamed
        assert authenticated_username
        if not streamed:
            streamed = True
            return stats_current_http.DeltaStreamResult(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "content_type": "application/json",
                    "base_cache_generation": generation["cache_generation"],
                    "cache_generation": streamed_cache_generation,
                    "revision": streamed_revision,
                },
                streamed_delta,
            )
        return stats_current_http.DeltaStreamResult(
            HTTPStatus.NOT_MODIFIED,
            {"ok": True, "not_modified": True, "cache_generation": streamed_cache_generation},
        )

    monkeypatch.setattr(runtime.app.stats_current_http, "delta_stream", accepted_delta_stream)
    accepted_push = WebDriverWait(authenticated_e2e_browser.driver, 8, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const client = jsDebugCurrentStatsClientState?.client;
            const controller = client?.controller?.();
            const generation = controller?.generation?.();
            const evidence = client?.streamEvidence?.();
            if (generation?.source_generation !== arguments[0] || evidence?.acceptedDeltaSequence < 1) return false;
            return {
              accepted: true,
              sourceGeneration: generation.source_generation,
              failureLatched: jsDebugCurrentStatsClientState.failureLatched,
              streamEpoch: evidence.streamEpoch,
              lastDeliveryEmitMs: evidence.lastDeliveryEmitMs,
              lastDeliveryAtMs: evidence.lastDeliveryAtMs,
            };
            """,
            streamed_source_generation,
        )
    )
    assert accepted_push["accepted"] is True, accepted_push
    assert accepted_push["sourceGeneration"] == streamed_source_generation, accepted_push
    assert accepted_push["failureLatched"] is False, accepted_push
    assert accepted_push["streamEpoch"] == stream_base["streamEpoch"], accepted_push
    # The server's emit clock rides the SSE `id:` line, so a real Chrome EventSource surfaces it
    # as `event.lastEventId`. Without it the browser knows only when a frame arrived, and a stall
    # artifact cannot separate "the server never sent it" from "it arrived late".
    assert isinstance(accepted_push["lastDeliveryEmitMs"], int), accepted_push
    assert accepted_push["lastDeliveryEmitMs"] > 0, accepted_push
    assert accepted_push["lastDeliveryAtMs"] > 0, accepted_push
    accepted_failures = authenticated_e2e_browser.driver.execute_script(
        """
            const generation = jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.();
            const failures = jsDebugEvents.filter(event => event?.type === 'stats_history'
              && event?.level === 'warning' && event?.route === '/api/stats-stream');
            return {
              sourceGeneration: generation.source_generation,
              failureLatched: jsDebugCurrentStatsClientState.failureLatched,
              failures: failures.map(event => ({
                signature: event.signature,
                message: event.message,
                source: event.route,
                marked: Object.prototype.hasOwnProperty.call(event, 'provenance'),
              })),
            };
        """
    )
    assert accepted_failures["failureLatched"] is False, accepted_failures

    def unavailable_delta_stream(
        _raw_query: str,
        *,
        authenticated_username: str,
    ) -> stats_current_http.DeltaStreamResult:
        assert authenticated_username
        return stats_current_http.DeltaStreamResult(
            HTTPStatus.FAILED_DEPENDENCY,
            {"ok": False, "status": "unavailable", "reason": "forced graph stream failure"},
        )

    monkeypatch.setattr(runtime.app.stats_current_http, "delta_stream", unavailable_delta_stream)
    authenticated_e2e_browser.driver.execute_script(
        "void jsDebugCurrentStatsClientState.client.start();"
    )
    recovered = WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            """
            const generation = jsDebugCurrentStatsClientState?.client?.controller?.()?.generation?.();
            const failures = jsDebugEvents.filter(event => event?.type === 'stats_history'
              && event?.level === 'warning' && event?.route === '/api/stats-stream');
            const later = [...failures].reverse().find(event => event.message !== arguments[0]);
            if (!generation || !later) return false;
            return {
              sourceGeneration: generation.source_generation,
              failureLatched: jsDebugCurrentStatsClientState.failureLatched,
              later: {
                signature: later.signature,
                message: later.message,
                source: later.route,
                marked: Object.prototype.hasOwnProperty.call(later, 'provenance'),
              },
            };
            """,
            failed["message"],
        )
    )
    monkeypatch.setattr(runtime.app.stats_current_http, "delta_stream", original_delta_stream)
    assert recovered["later"]["source"] == "/api/stats-stream", recovered
    assert recovered["later"]["marked"] is False, recovered
    assert recovered["failureLatched"] is True, recovered
    recovered_receipt = WebDriverWait(authenticated_e2e_browser.driver, 18, poll_frequency=0.2).until(
        lambda driver: driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            Promise.resolve(flushJsDebugCurrentObservations()).then(() => {
              const barrier = jsDebugCurrentObservationReceiptBarrier();
              done(barrier.quiescent ? barrier : false);
            }, () => done(false));
            """
        )
    )
    assert recovered_receipt["quiescent"] is True, recovered_receipt
    expected_failures = authenticated_e2e_browser.driver.execute_script(
        """
        return jsDebugEvents.filter(event => event?.type === 'stats_history'
          && event?.level === 'warning' && event?.route === '/api/stats-stream');
        """,
    )
    assert [
        {
            "signature": event["signature"],
            "source": event["route"],
            "message": event["message"],
            "marked": "provenance" in event,
        }
        for event in expected_failures
    ] == [
        {"signature": failed["signature"], "source": failed["source"], "message": failed["message"], "marked": False},
        {"signature": recovered["later"]["signature"], "source": recovered["later"]["source"], "message": recovered["later"]["message"], "marked": False},
    ]
    retired = acknowledge_and_consume_only_expected_js_debug_failures(
        authenticated_e2e_browser.driver,
        expected_failures,
    )
    assert len(retired) == 2
    chrome_entries = read_browser_console_log(authenticated_e2e_browser.driver)
    chrome_failures = [
        entry for entry in chrome_entries
        if str(entry.get("level") or "").upper() in {"WARNING", "SEVERE"}
    ]
    assert chrome_failures == [], chrome_failures


def _select_one_second_five_minute_graph(harness: Any) -> None:
    result = harness.driver.execute_async_script(
        r"""
        const done = arguments[arguments.length - 1];
        setDebugGraphRange(300);
        setDebugGraphResolutionOverride(1);
        window.__yolomuxTestWaitFor(() => {
          const controller = jsDebugCurrentStatsClientState?.client?.controller?.();
          const generation = controller?.generation?.();
          return generation?.range_seconds === 300
            && generation?.requested_resolution === 1
            && generation?.resolution_seconds === 1
            && jsDebugHistoryReadiness?.phase === 'ready' ? true : null;
        }, {timeoutMs: 12000, description: 'real YO!stats five-minute one-second generation'})
          .then(() => done({ok: true}), error => done({error: String(error?.stack || error)}));
        """
    )
    assert result == {"ok": True}, result


def test_real_stats_cpu_value_round_trips_through_rpc_and_rendered_svg(
    authenticated_e2e_browser: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = authenticated_e2e_browser.runtime
    _flush_and_stop_real_stats_graph(authenticated_e2e_browser, require_client=True)
    runtime.app.stats_current_runtime.stop()
    client = runtime.app.stats_current_client
    assert client.ensure_started() is True
    now = int(time.time())
    first_observed_at = now - CPU_GRAPH_SAMPLE_COUNT - 2
    source = "cpu:positive-graph-gate"
    epoch = "positive-graph-gate"
    appended = client.append(
        observations=tuple(
            storage.Observation(
                f"positive-graph-{index}",
                "cpu",
                source,
                first_observed_at + index,
                epoch,
                1,
                {"process_percent": 11.0, "system_percent": CPU_GRAPH_PERCENT},
            )
            for index in range(CPU_GRAPH_SAMPLE_COUNT)
        ),
        coverage_epochs=(storage.CoverageEpoch(
            "cpu",
            source,
            epoch,
            first_observed_at,
            None,
            1,
            1,
        ),),
    )
    assert appended.get("ok") is True, appended
    assert int(appended.get("accepted") or 0) == CPU_GRAPH_SAMPLE_COUNT + 1, appended
    _wait_for_ring_publication(authenticated_e2e_browser, int(appended["source_generation"]))

    _open_real_stats_graph(authenticated_e2e_browser)
    _select_one_second_five_minute_graph(authenticated_e2e_browser)
    positive = WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda _driver: _wait_for_cpu_points(authenticated_e2e_browser)
    )
    _assert_seeded_cpu_graph(positive)

    original_capabilities = runtime.app.stats_current_http.capabilities

    def capabilities_with_unknown_field() -> dict[str, object]:
        payload = dict(original_capabilities())
        payload["negative_control_unknown"] = True
        return payload

    monkeypatch.setattr(runtime.app.stats_current_http, "capabilities", capabilities_with_unknown_field)
    session = str(runtime.tmux.sessions[0])
    authenticated_e2e_browser.driver.get(
        f"{authenticated_e2e_browser.base_url}/?sessions={session}&layout=left&tabs=left:files,__debug__,{session}"
    )
    WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            "return document.readyState === 'complete' "
            "&& typeof syncJsDebugCurrentStatsClient === 'function' "
            "&& document.querySelector('#grid') !== null;"
        )
    )
    authenticated_e2e_browser.switch_session("__debug__")
    negative = WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda _driver: _wait_for_stats_warning(authenticated_e2e_browser)
    )
    assert negative["lineCount"] == 0, negative
    assert any("stats capabilities fields are not exact" in warning for warning in negative["warnings"]), negative
    monkeypatch.setattr(runtime.app.stats_current_http, "capabilities", original_capabilities)
    authenticated_e2e_browser.driver.get(
        f"{authenticated_e2e_browser.base_url}/?sessions={session}&layout=left&tabs=left:__debug__,{session}"
    )
    WebDriverWait(authenticated_e2e_browser.driver, 12, poll_frequency=0.05).until(
        lambda driver: driver.execute_script(
            "return document.readyState === 'complete' "
            "&& typeof syncJsDebugCurrentStatsClient === 'function' "
            "&& document.querySelector('#grid') !== null;"
        )
    )
    assert_live_runtime_boot_healthy(
        authenticated_e2e_browser.driver,
        authenticated_e2e_browser.test_id,
        timeout=12,
    )
    _flush_and_stop_real_stats_graph(authenticated_e2e_browser)


def test_real_stats_interval_options_return_after_range_round_trip_and_reload(authenticated_e2e_browser: Any) -> None:
    client = authenticated_e2e_browser.runtime.app.stats_current_client
    assert client.ensure_started() is True
    observed_at = int(time.time())
    appended = client.append(observations=(storage.Observation(
        "stats-interval-real-browser",
        "cpu",
        "stats-interval-fixture",
        observed_at,
        "stats-interval-fixture",
        1,
        {"process_percent": 17.0, "system_percent": 23.0},
    ),))
    assert appended.get("ok") is True, appended

    _open_real_stats_graph(authenticated_e2e_browser)
    round_trip = _exercise_real_stats_interval_round_trip(authenticated_e2e_browser)
    assert round_trip.get("error") is None, round_trip
    assert round_trip["before"]["options"] == round_trip["expected"], round_trip
    assert round_trip["before"]["selected"] == "1", round_trip
    assert "1" not in round_trip["widened"]["options"], round_trip
    assert round_trip["widened"]["selected"] == "0", round_trip
    assert round_trip["widened"]["generation"]["requested_resolution"] == "AUTO", round_trip
    assert round_trip["widened"]["generation"]["resolution_seconds"] == 300, round_trip
    assert round_trip["returned"]["options"] == round_trip["before"]["options"], round_trip
    assert round_trip["returned"]["selected"] == "0", round_trip
    assert round_trip["returned"]["generation"]["requested_resolution"] == "AUTO", round_trip
    assert round_trip["returned"]["generation"]["resolution_seconds"] == 1, round_trip
    assert round_trip["returned"]["generation"]["buckets"] == 300, round_trip

    _open_real_stats_graph(authenticated_e2e_browser, include_finder=False)
    persisted = _real_stats_picker_state(authenticated_e2e_browser)
    assert persisted["range_seconds"] == 300, persisted
    assert persisted["options"] == round_trip["expected"], {"round_trip": round_trip, "persisted": persisted}
    assert persisted["selected"] == "0", persisted
    assert persisted["resolution_seconds"] == 1 and persisted["buckets"] == 300, persisted
    _flush_and_stop_real_stats_graph(authenticated_e2e_browser)
