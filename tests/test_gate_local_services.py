# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Rendered Local Services table regression gates."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401
from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import wait_for_browser_boot
from tests.test_gate_editor import gate_browser_runtime
from tools import static_build
from yolomux_lib import web as web_module


pytestmark = [pytest.mark.browser, pytest.mark.socket]

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_SERVICES_GLOBALS = {
    "debugSystemLocalServicesCardHtml": "function",
    "updateDebugSystemLocalServicesCard": "function",
}


@pytest.fixture(autouse=True)
def local_services_source_bundle(monkeypatch, tmp_path):
    """Serve this gate from a fixture-owned bundle built from current source."""
    asset_dir = tmp_path / "local-services-static"
    asset_dir.mkdir()
    for name in ("brand.css", "codemirror.js", "yolomux.css"):
        shutil.copy2(REPO_ROOT / "static" / name, asset_dir / name)
    for name in ("fonts", "locales", "vendor"):
        shutil.copytree(REPO_ROOT / "static" / name, asset_dir / name)
    (asset_dir / "yolomux.js").write_text(static_build.build_asset("yolomux.js"), encoding="utf-8")
    monkeypatch.setattr(web_module, "STATIC_DIR", asset_dir)


def test_local_services_rows_are_populated_and_long_runtime_fits_its_cell(gate_browser_runtime):
    """Every field paints a value and a production-shaped Runtime value stays inside its cell."""
    browser = gate_browser_runtime.browser
    wait_for_browser_boot(browser, globals_required=LOCAL_SERVICES_GLOBALS, dom_anchors=("#grid",), timeout=12)
    metrics = browser.execute_script(
        """
        debugSystemLocalServicesState.records.clear();
        const host = document.createElement('div');
        host.style.cssText = 'position:fixed;inset:0 auto auto 0;width:640px;padding:8px;background:var(--bg);z-index:10000';
        host.innerHTML = debugSystemLocalServicesCardHtml();
        document.body.append(host);
        const now = Date.now() / 1000;
        const tasks = Object.fromEntries(Array.from({length: 18}, (_, index) => [
          `daemon.fs.production_long_lived_repair_task_${index}`,
          {avg_ms: 12 + index, max_ms: 80 + index, count: 1000 + index},
        ]));
        updateDebugSystemLocalServicesCard(host.querySelector('[data-js-debug-local-services-card]'), {
          local_services: {services: [{
            service: 'daemon', pid: 4321, healthy: true, started_at: now - 120, uptime_seconds: 120,
            resources: {cpu_percent: 17.5, rss_bytes: 268435456}, clients: 3, generation: 41,
            active_task: 'session_files_view', last_success: now - 2, last_failure: 'none',
            queues: {queued: 7, active: 1}, cache: {entries: 8929, stale: 0},
            product_counters: {session_files_view: {completed: 120, failed: 0}},
            product_runtime_ms: tasks,
            product_work_totals: {session_files_view: {visited: 8929, published: 8929}},
          }]},
        });
        const wrap = host.querySelector('.js-debug-system-local-services-wrap');
        const cells = [...host.querySelectorAll('[data-js-debug-service-cell]')];
        const runtime = host.querySelector('[data-js-debug-service-cell][data-field="runtime"]');
        const rows = [...host.querySelectorAll('[data-js-debug-service-row]')].map(row => ({
          field: row.dataset.jsDebugServiceRow,
          value: row.querySelector('[data-js-debug-service-cell]')?.textContent?.trim() || '',
        }));
        const result = {
          rowCount: rows.length,
          blankRows: rows.filter(row => !row.value),
          placeholderRows: rows.filter(row => row.value === '—'),
          runtimeText: runtime?.textContent?.trim() || '',
          runtimeClientWidth: runtime?.clientWidth || 0,
          runtimeScrollWidth: runtime?.scrollWidth || 0,
          overflowingFields: cells
            .filter(cell => cell.scrollWidth > cell.clientWidth + 1)
            .map(cell => cell.dataset.field),
          wrapClientWidth: wrap?.clientWidth || 0,
          wrapScrollWidth: wrap?.scrollWidth || 0,
        };
        host.remove();
        return result;
        """
    )
    assert metrics["rowCount"] == 16, metrics
    assert metrics["blankRows"] == [] and metrics["placeholderRows"] == [], metrics
    assert metrics["runtimeText"], metrics
    assert metrics["overflowingFields"] == [], metrics
    assert metrics["wrapScrollWidth"] <= metrics["wrapClientWidth"] + 1, metrics
