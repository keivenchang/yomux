"""Browser-visible recovery reporting for damaged current-state stores."""

from __future__ import annotations

import pytest

from tests.browser_helpers.browser_layout import browser
from tests.browser_helpers.browser_layout import load_static_html_fixture
from tests.helpers.browser_stats_coverage import _current_stats_fixture_html
from tests.helpers.gate_stats import corrupt_in_place as _corrupt_in_place
from tests.helpers.gate_stats import valid_current_database as _valid_current_database
from yolomux_lib.stats_current import http as stats_http
from yolomux_lib.stats_current import service as stats_service
from yolomux_lib.stats_current import storage


pytest_plugins = ("tests.gate_harness",)


class _RecoveredStatusClient:
    """Expose one real service status to the narrow capabilities-forwarding seam."""

    def __init__(self, status: dict[str, object]):
        self._status = status

    def status(self) -> dict[str, object]:
        return self._status


def _recovered_capabilities(gate_runtime_paths) -> dict[str, object]:
    """Recover a physically damaged store and return the browser-facing payload."""

    state_dir = gate_runtime_paths.state_dir / "corruption-ui"
    database = _valid_current_database(state_dir)
    _corrupt_in_place(database)
    service = stats_service.StatsCurrentService(state_dir / "statsd.sock", database)
    try:
        service._start()
        forwarder = stats_http.StatsHttpForwarder(
            _RecoveredStatusClient(service._status()),
            client_binding_secret=b"corruption-ui-client-binding-secret",
        )
        return dict(forwarder.capabilities())
    finally:
        service._close()


def test_recovered_store_outcome_reaches_the_browser_payload(gate_runtime_paths):
    """A real malformed current database becomes a narrow, browser-safe recovery outcome."""
    payload = _recovered_capabilities(gate_runtime_paths)

    assert payload["migration"] == {
        "state": "ready",
        "result": "recovered",
        "issue_kinds": ["unreadable_current_database"],
    }, payload
    assert str(gate_runtime_paths.state_dir) not in repr(payload), payload
    assert storage.DATABASE_FILENAME not in repr(payload), payload


@pytest.mark.browser
def test_recovered_store_renders_a_persistent_banner_but_first_run_does_not(browser, tmp_path, gate_runtime_paths):
    """The rendered stats DOM makes a reset unmistakable without alarming a genuine first run."""
    recovered = _recovered_capabilities(gate_runtime_paths)
    load_static_html_fixture(browser, tmp_path, "corruption-ui.html", _current_stats_fixture_html())
    result = browser.execute_async_script(
        """
        const recovered = arguments[0];
        const done = arguments[arguments.length - 1];
        const originalFetch = window.__statsFixture.fetch;
        const eventSource = class { addEventListener() {} close() {} };
        const mount = async migration => {
          window.__statsFixture.fetch = async input => {
            const url = new URL(String(input), location.href);
            if (url.pathname === '/api/stats-capabilities') {
              return {status: 200, json: async () => ({...recovered, migration})};
            }
            return originalFetch(input);
          };
          const root = document.getElementById('stats-root');
          root.replaceChildren();
          const mounted = YOLOmuxStatsCurrent.mount(root, {
            view: 'stats', clientId: `corruption-${migration.result}`,
            savedRange: 300, savedResolution: 1, fetch: window.__statsFixture.fetch,
            EventSource: eventSource, controllerOptions: {clock: window.__statsFixture.clock},
          });
          await mounted.start();
          await window.__statsFixture.clock.advance(0);
          const banner = root.querySelector('[data-stats-current-recovery-banner]');
          const value = banner ? {
            text: banner.textContent, role: banner.getAttribute('role'), persistent: !banner.hidden,
          } : null;
          mounted.destroy();
          return value;
        };
        (async () => {
          const recovery = await mount(recovered.migration);
          const activation = await mount({state: 'ready', result: 'activated', issue_kinds: []});
          return {recovery, activation};
        })().then(done)
          .catch(error => done({error: String(error?.stack || error)}));
        """,
        recovered,
    )

    assert result.get("error") is None, result
    assert result["recovery"] == {
        "text": "Stats history was reset after storage damage. The damaged database was kept in your YOLOmux state directory. Check the preserved file before removing it, then let new history accumulate.",
        "role": "alert",
        "persistent": True,
    }, result
    assert result["activation"] is None, result
