# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Real-browser evidence for the System service table and recovery alert."""

import json
from typing import Any

import pytest


pytest_plugins = ("tests.e2e_browser_harness",)
pytestmark = [pytest.mark.browser, pytest.mark.socket, pytest.mark.e2e]


def test_corrupt_stats_database_recovery_is_unmissable_in_real_chrome(e2e_browser: Any) -> None:
    """The real statsd recovery channel reaches a visible alert on the real page."""
    client = e2e_browser.runtime.app.stats_current_client
    database = client.database_path
    assert e2e_browser.runtime.paths.state_dir in database.parents, database
    assert not database.exists(), "the fixture must corrupt a new database, never replace live state"
    database.parent.mkdir(parents=True, exist_ok=True)
    database.write_bytes(b"not a sqlite database")

    try:
        started = client.ensure_started()
        assert started is True, json.dumps(client._transport.registry.status(), sort_keys=True)
        status = client.status()
        migration = status.get("migration") if isinstance(status.get("migration"), dict) else {}
        issues = migration.get("issue_records") if isinstance(migration.get("issue_records"), (list, tuple)) else ()
        recovery = next(
            issue for issue in issues
            if isinstance(issue, dict) and issue.get("kind") == "unreadable_current_database"
        )
        quarantined = database.parent / str(recovery["source"])
        assert quarantined.is_file() and database.is_file()

        e2e_browser.load(tabs=("files", "__debug__"))
        banner = e2e_browser.driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const inspect = () => {
              const debugTab = document.querySelector('.dockview-pane-tab[data-pane-tab="__debug__"]');
              if (!debugTab) return null;
              debugTab.click();
              const systemTab = document.querySelector('[data-js-debug-subtab="system"]');
              if (!systemTab) return null;
              systemTab.click();
              // The recovery event is now one line of the ONE compact alert slot above the service
              // roster, not a 80px-tall banner card of its own. "Unmissable" is therefore measured
              // as: the slot is visible, it carries role="alert", it sits ABOVE the roster, and it
              // names every fact -- not as a minimum pixel height.
              const element = document.querySelector('[data-system-recovery-banner]');
              const slot = document.querySelector('[data-js-debug-system-alert]');
              const roster = document.querySelector('[data-js-debug-roster]');
              if (!element || !slot || !roster) return null;
              const rect = slot.getBoundingClientRect();
              const style = getComputedStyle(slot);
              if (rect.width <= 0 || rect.height <= 0 || style.display === 'none') return null;
              return {
                text: element.textContent.replace(/\\s+/g, ' ').trim(),
                role: slot.getAttribute('role') || '',
                slots: document.querySelectorAll('[data-js-debug-system-alert]').length,
                aboveRoster: rect.top < roster.getBoundingClientRect().top,
                height: rect.height,
                fontSize: Number.parseFloat(style.fontSize),
              };
            };
            window.__yomuxTestWaitFor(inspect, {
              timeoutMs: 12000,
              description: 'visible statsd corruption recovery banner',
            }).then(done, error => done({__e2eError: String(error?.stack || error)}));
            """,
        )
        assert not banner.get("__e2eError"), banner
        assert banner["role"] == "alert" and banner["fontSize"] >= 11, banner
        assert banner["slots"] == 1 and banner["aboveRoster"] is True, banner
        assert "statsd" in banner["text"] and "unreadable_current_database" in banner["text"], banner
        assert str(quarantined) in banner["text"] and str(database) in banner["text"], banner
        evidence = e2e_browser.capture_failure("corruption-banner")
        assert evidence.screenshot is not None and evidence.screenshot.is_file()
        assert evidence.dom.is_file()
        print(evidence.message())
    finally:
        client._transport.registry._request("shutdown", timeout=0.5)
