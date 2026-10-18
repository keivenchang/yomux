# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Section G regression gates for rendered stats, cost, and materializer health."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture
from tests.browser_helpers.browser_layout import load_static_html_fixture
from tests.browser_helpers.browser_layout import start_browser_server
from tests.browser_helpers.browser_layout import stop_browser_server
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import assert_browser_journey_error_free
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_http_request
from tests.native_gate_fixtures import gate_live_server  # noqa: F401
from tests.gate_harness import load_gate_browser
from tests.gate_harness import open_gate_stats_surface
from tests.gate_harness import run_fixture_cleanup_phases
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.helpers.browser_stats_coverage import _current_stats_fixture_html, _start_current_stats, _write_current_stats_fixture_assets
from tests.helpers.gate_stats import NOW, _seed_realistic_stats
from tools.mockers.transcript import append_record as _append_record
from tools.mockers.transcript import codex_meta as _codex_meta
from tools.mockers.transcript import codex_usage as _codex_usage
from tools.mockers.transcript import write_records as _write_records
from tests.helpers.gate_stats import commit_scan as _commit
from yomux_lib.client_events import ClientEventBroker
from yomux_lib.stats_current import pricing
from yomux_lib.stats_current import storage
from yomux_lib.stats_current.transcripts import StatsCurrentTranscriptUsageScanner
from yomux_lib.stats_current.usage import usage_atom_from_source
from yomux_lib.workspace import settings as workspace_settings


REALISTIC_ROW_COUNT = 8_929
REPAIR_BACKLOG_RECORDS = 240


def _stats_snapshot_ready(response) -> bool:
    if response.status == 200:
        return True
    assert response.status == 202, response.status
    payload = response.json()
    assert isinstance(payload, dict), payload
    assert payload.get("status") == "pending", payload
    retry_after_seconds = payload.get("retry_after_seconds")
    assert type(retry_after_seconds) is int, payload
    assert 1 <= retry_after_seconds <= 60, payload
    return False


@pytest.mark.browser
def test_g0_stats_snapshot_readiness_accepts_only_ready_or_bounded_pending():
    assert _stats_snapshot_ready(SimpleNamespace(status=200, json=lambda: None)) is True
    pending = SimpleNamespace(
        status=202,
        json=lambda: {"status": "pending", "retry_after_seconds": 1},
    )
    assert _stats_snapshot_ready(pending) is False
    invalid = (
        SimpleNamespace(status=409, json=lambda: {}),
        SimpleNamespace(status=202, json=lambda: {"status": "queued", "retry_after_seconds": 1}),
        SimpleNamespace(status=202, json=lambda: {"status": "pending", "retry_after_seconds": 0}),
        SimpleNamespace(status=202, json=lambda: {"status": "pending", "retry_after_seconds": 61}),
    )
    for response in invalid:
        with pytest.raises(AssertionError):
            _stats_snapshot_ready(response)








def _live_usage_while_repair_backlog_remains(root: Path) -> tuple[tuple[storage.UsageAtom, ...], int]:
    """Return current-tail atoms while a production-shaped orphan repair remains queued."""

    sessions = root / ".codex" / "sessions" / "2026" / "07" / "31"
    active = sessions / "rollout-active.jsonl"
    orphan = sessions / "rollout-orphan.jsonl"
    _write_records(active, [_codex_meta("active-thread"), _codex_usage(NOW - 3, 100, 20, 10)])
    rows = [{"key": "gate-stats|0|codex", "kind": "codex", "transcript": str(active)}]
    scanner = StatsCurrentTranscriptUsageScanner(max_records_per_scan=20)
    initial = scanner.scan(rows)
    _commit(scanner, initial)

    _write_records(orphan, [
        _codex_meta(
            "orphan-thread",
            "active-thread",
            forked_from_id="active-thread",
            thread_source="subagent",
        ),
        *(
            {"type": "response_item", "timestamp": NOW - 600 + index, "payload": {"text": "historical repair"}}
            for index in range(REPAIR_BACKLOG_RECORDS)
        ),
    ])
    scanner._max_records_per_scan = 1
    repair = scanner.scan(rows)
    _commit(scanner, repair)
    assert repair.backlog_files == 1

    _append_record(active, _codex_usage(NOW - 1, 140, 25, 17))
    live = scanner.scan(rows)
    assert live.backlog_files == 1
    atoms = tuple(
        usage_atom_from_source({**vars(item.atom), "tmux_key": item.tmux_key})
        for scan in (initial, live)
        for item in scan.items
    )
    assert atoms and any(atom.direction == "output" and atom.payload["quantity"] == 7 for atom in atoms)
    return atoms, live.backlog_files




def _start_current_stats_over_network(browser):
    """Mount the production stream transport over the fixture's real HTTP server.

    `_current_stats_fixture_html(network_fetch=True)` forwards its fetches to the
    server, but its generic helper intentionally substitutes a synchronous fake
    EventSource for renderer tests. G1 exercises the live append-to-paint
    transition, so it must use the browser's native EventSource too.
    """

    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const root = document.getElementById('stats-root');
        const mounted = YOmuxStatsCurrent.mount(root, {
          view: 'stats',
          clientId: 'gate-live-stats',
          savedRange: 300,
          savedResolution: 1,
          fetch: window.__statsFixture.fetch,
          EventSource: window.EventSource,
        });
        window.__statsFixture.mounted = mounted;
        mounted.start().then(async () => {
          await window.__yomuxTestWaitFor(
            () => root.querySelector('[data-stats-chart="cpu"]'),
            {description: 'live stats first exact generation'}
          );
          done({ok: true});
        }).catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert result.get("error") is None, result






@pytest.mark.browser
def test_g2_unpriced_live_attribution_never_renders_as_zero_cost(browser, tmp_path, gate_runtime_paths):
    """Unknown-model live attribution stays visibly unpriced instead of becoming $0.00."""

    load_static_html_fixture(browser, tmp_path, "gate-stats-unpriced.html", _current_stats_fixture_html())
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          let pricedReport = null;
          window.__statsFixture.snapshotTransform = input => {
            const snapshot = structuredClone(input);
            const report = snapshot.cost_report;
            pricedReport = structuredClone(report);
            const zeroCosts = value => {
              if (!value || typeof value !== 'object') return;
              for (const [key, item] of Object.entries(value)) {
                if (key === 'micro_usd' || key === 'api_list_micro_usd' || key === 'total_micro_usd' || key === 'total_api_list_micro_usd') value[key] = 0;
                else zeroCosts(item);
              }
            };
            zeroCosts(report);
            report.priced = {atoms: 0, tokens: 0};
            report.unpriced = {atoms: 3, tokens: report.total_tokens};
            for (const row of [...report.models, ...report.agents]) {
              row.priced = {atoms: 0, tokens: 0};
              row.unpriced = {atoms: 3, tokens: row.total_tokens};
            }
            for (const row of report.evidence) row.priced_atoms = 0;
            return snapshot;
          };
          await window.__statsFixture.start('cost');
          const root = document.getElementById('stats-root');
          root.querySelector('[data-stats-current-cost-more]').click();
          const unpriced = {
            summary: root.querySelector('[data-stats-current-cost-summary]').textContent,
            details: document.querySelector('[data-stats-current-cost-modal-scroll]').textContent,
          };
          window.__statsFixture.lastSnapshot = {
            ...structuredClone(window.__statsFixture.lastSnapshot),
            cost_report: pricedReport,
          };
          window.__statsFixture.emitCpuDelta(12);
          await window.__statsFixture.clock.advance(0);
          done({
            unpriced,
            liveSummary: root.querySelector('[data-stats-current-cost-summary]').textContent,
          });
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert metrics.get("error") is None, metrics
    assert "Unpriced: 1.1K tokens / 3 atoms" in metrics["unpriced"]["details"], metrics
    assert "Unpriced" in metrics["unpriced"]["summary"], metrics
    assert "$0.00" not in metrics["unpriced"]["summary"], metrics
    assert "$0.25" in metrics["liveSummary"], metrics


@pytest.mark.browser
def test_g6_unlisted_series_uses_declared_micro_usd_unit(browser, tmp_path, gate_runtime_paths):
    """An unlisted 150,000 micro-USD series paints as $0.15 from its declared unit."""

    load_static_html_fixture(browser, tmp_path, "gate-stats-unlisted-unit.html", _current_stats_fixture_html())
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__statsFixture.snapshotTransform = input => {
          const snapshot = structuredClone(input);
          const bucket = snapshot.buckets.find(item => Object.keys(item.series).length > 0);
          delete bucket.series.cost_micro_usd;
          bucket.series.unlisted_billing_probe = {
            value: 150000,
            unit: 'micro_usd',
            source_count: 1,
            first_timestamp: bucket.start,
            last_timestamp: bucket.start,
          };
          return snapshot;
        };
        (async () => {
          await window.__statsFixture.start('cost');
          const root = document.getElementById('stats-root');
          const series = root.querySelector('[data-series="unlisted_billing_probe"]');
          const chart = series?.closest('[data-stats-chart]');
          const svg = chart?.querySelector('[data-stats-current-svg]');
          const point = series?.querySelector('[data-series-point]');
          const bounds = svg?.getBoundingClientRect();
          svg?.dispatchEvent(new PointerEvent('pointermove', {
            bubbles: true,
            pointerType: 'mouse',
            clientX: bounds.left + Number(point?.getAttribute('cx')) / 600 * bounds.width,
          }));
          done({
            chart: chart?.dataset.statsChart || '',
            labels: [...chart?.querySelectorAll('text') || []].map(item => item.textContent),
            legend: chart?.querySelector('[data-series-legend="unlisted_billing_probe"]')?.textContent || '',
            tooltip: chart?.querySelector('[data-stats-current-tooltip]')?.textContent || '',
          });
        })().catch(error => done({error: String(error?.stack || error)}));
        """
    )
    assert result.get("error") is None, result
    assert result["legend"] == "unlisted billing probe", result
    assert "$0.15" in result["labels"], result
    assert "$0.15" in result["tooltip"], result


@pytest.mark.browser
def test_g7_monotonic_value_never_renders_as_wall_clock_age(browser, tmp_path, gate_runtime_paths):
    """`generated_at` is wall-clock data. A monotonic counter in that field must read as absent.

    Subtracting a small monotonic value from `Date.now()` yields a confident, false multi-year age
    instead of admitting no wall-clock time exists, so the guard lives in `debugSystemGeneratedAge`
    and the summary strip's `updated` fact is where its answer is rendered.

    The selector is a DATA HOOK, deliberately, and this test is why. It used to be
    `[role="status"]`, which was written when the summary strip carried that role. A later change
    moved the role off the strip and added a screen-reader announce region ahead of it in document
    order; `querySelector` takes the first match, so this test silently rebound to the announce
    region and started asserting against a counts string. The contract went unverified while the
    suite stayed green. A bare role is a rebindable selector -- any future sibling can take it --
    so this names the element that owns the rendering instead.
    """
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1&layout=left&tabs=left:1")
    rendered = browser.execute_script(
        """
        jsDebugSystemState.payload = {
          ok: true,
          generated_at: 12345,
          server: {}, owner: {}, refresh: {roles: {}}, search_index: {}, caches: {},
          client_events: {}, chat: {}, cpu_budget: {},
          local_services: {totals: {}, services: []},
          top_endpoints: [], top_background_work: [],
        };
        const host = document.createElement('div');
        host.innerHTML = debugSystemInnerHtml();
        const updated = host.querySelector('[data-js-debug-roster-summary-fact="updated"]');
        return {
          updated: updated ? updated.textContent : null,
          // Every element in the rendered panel that carries the age, so a future second copy
          // rendered somewhere else cannot pass by being invisible to one selector.
          matches: Array.from(host.querySelectorAll('*'))
            .filter(node => !node.children.length && /\b12345\b|1970/.test(node.textContent))
            .map(node => node.textContent.trim()),
        };
        """
    )
    assert rendered["updated"] is not None, rendered
    text = rendered["updated"]
    assert "year" not in text.lower(), text
    assert "not available" in text.lower() or "n/a" in text.lower(), text
    # NEGATIVE CONTROL: nothing anywhere in the panel renders the raw monotonic value or a 1970
    # date derived from it. The single-element assertion above cannot see a second copy.
    assert rendered["matches"] == [], rendered
