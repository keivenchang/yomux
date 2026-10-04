# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared real-browser journey for terminal navigation acknowledgement."""

from pathlib import Path
from typing import Any

from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_console import consume_only_expected_js_debug_api_errors
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture
from tests.gate_harness import assert_fixture_client_event_demand_claimed
from tests.gate_harness import claim_fixture_client_event_demand


TERMINAL_NAVIGATION_ACK_CEILING_MS = 50.0


def terminal_navigation_ack_metrics(browser: Any, tmp_path: Path) -> dict[str, object]:
    """Drive the one navigation journey used by parallel semantics and exclusive timing."""

    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=slot1&tabs=slot1:1",
        sessions=["1", "2"],
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof selectSession === 'function' && typeof tmuxWindow === 'function' && document.querySelector('#panel-1 .terminal .xterm')"
        )
    )
    ownership = claim_fixture_client_event_demand(browser)
    assert ownership["bound"]["sourceOrigin"] == ownership["bound"]["pageOrigin"]
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        (async () => {
          const originalEnsureSession = ensureSession;
          const originalEnsureTerminalRunning = ensureTerminalRunning;
          const originalUpdatePanelSlot = updatePanelSlot;
          const originalRenderAutoApproveButtons = renderAutoApproveButtons;
          const originalUpdatePanelInactiveOverlays = updatePanelInactiveOverlays;
          const originalApiFetchJson = apiFetchJson;
          const originalFetch = window.fetch;
          try {
            const topologyCounts = {
              slotUpdates: {},
              autoApproveRenders: 0,
              inactiveOverlayReconciliations: 0,
              terminalStarts: {},
            };
            updatePanelSlot = (panel, session, slot) => {
              topologyCounts.slotUpdates[session] = (topologyCounts.slotUpdates[session] || 0) + 1;
              return originalUpdatePanelSlot(panel, session, slot);
            };
            renderAutoApproveButtons = () => {
              topologyCounts.autoApproveRenders += 1;
              return originalRenderAutoApproveButtons();
            };
            updatePanelInactiveOverlays = () => {
              topologyCounts.inactiveOverlayReconciliations += 1;
              return originalUpdatePanelInactiveOverlays();
            };
            ensureTerminalRunning = session => {
              topologyCounts.terminalStarts[session] = (topologyCounts.terminalStarts[session] || 0) + 1;
              return originalEnsureTerminalRunning(session);
            };
            let resolveEnsure;
            ensureSession = () => new Promise(resolve => { resolveEnsure = resolve; });
            transcriptMetadataState.loaded = false;
            if (transcriptMetadataState.payload?.sessions) delete transcriptMetadataState.payload.sessions['2'];
            const tabStarted = performance.now();
            void selectSession('2', {userInitiated: true});
            const tabState = document.querySelector('#term-2 [data-terminal-connection-state]');
            const tabAck = {
              elapsedMs: performance.now() - tabStarted,
              visible: activeSessions.includes('2') && document.querySelector('#panel-2')?.isConnected === true,
              state: tabState?.dataset.terminalConnectionState || '',
            };
            await new Promise(resolve => requestAnimationFrame(resolve));
            const tabFrameState = document.querySelector('#term-2 [data-terminal-connection-state]');
            const tabFrameAck = {
              visible: activeSessions.includes('2') && document.querySelector('#panel-2')?.isConnected === true,
              state: tabFrameState?.dataset.terminalConnectionState || '',
            };
            const topologyTransactionCounts = structuredClone(topologyCounts);
            resolveEnsure(false);
            await window.__yomuxTestWaitFor(
              () => document.querySelector('#term-2 [data-terminal-connection-state="unavailable"]'),
              {timeoutMs: 2000, intervalMs: 20, description: 'cold tmux unavailable state'}
            );
            const unavailable = document.querySelector('#term-2 [data-terminal-connection-state="unavailable"]');
            const failedTab = {
              stillVisible: activeSessions.includes('2'),
              retry: Boolean(unavailable?.querySelector('[data-terminal-connection-retry]')),
            };

            activatePaneTab(slotForItem('1'), '1', {userInitiated: true});
            let rejectWindow;
            apiFetchJson = (url, options) => String(url).startsWith('/api/tmux-window?')
              ? new Promise((_resolve, reject) => { rejectWindow = reject; })
              : originalApiFetchJson(url, options);
            const windowStarted = performance.now();
            tmuxWindow('1', {windowIndex: 2}, 'window 2');
            const switching = document.querySelector('#term-1 [data-terminal-connection-state="switching"]');
            const windowAck = {
              elapsedMs: performance.now() - windowStarted,
              state: switching?.dataset.terminalConnectionState || '',
              dimmed: document.querySelector('#term-1')?.classList.contains('terminal-connection-pending') || false,
            };
            await new Promise(resolve => requestAnimationFrame(resolve));
            const windowFrameState = document.querySelector('#term-1 [data-terminal-connection-state="switching"]');
            const windowFrameAck = {
              state: windowFrameState?.dataset.terminalConnectionState || '',
              dimmed: document.querySelector('#term-1')?.classList.contains('terminal-connection-pending') || false,
            };
            rejectWindow(new Error('switch failed'));
            await window.__yomuxTestWaitFor(
              () => !document.querySelector('#term-1 [data-terminal-connection-state="switching"]'),
              {timeoutMs: 2000, intervalMs: 20, description: 'failed tmux switch rollback'}
            );
            applyBackendHealthPayload({epoch: 'terminal-navigation-fixture', revision: 1, overall_state: 'ready', degraded_resources: []});
            window.fetch = () => Promise.reject(new TypeError('server unavailable'));
            await Promise.allSettled([apiFetch('/api/test-a'), apiFetch('/api/test-b'), apiFetch('/api/test-c')]);
            const degraded = document.querySelector('[data-backend-health="unresponsive"]');
            window.fetch = () => Promise.resolve(new Response('{}', {status: 200, headers: {'Content-Type': 'application/json'}}));
            await apiFetch('/api/test-recovery');
            return {
              tabAck, tabFrameAck, failedTab, windowAck, windowFrameAck, topologyTransactionCounts,
              windowCleared: !terminalConnectionStateNode('1'),
              backendHealth: {shown: Boolean(degraded), recovered: document.querySelector('[data-backend-health]')?.dataset.backendHealth === 'ready'},
            };
          } finally {
            ensureSession = originalEnsureSession;
            ensureTerminalRunning = originalEnsureTerminalRunning;
            updatePanelSlot = originalUpdatePanelSlot;
            renderAutoApproveButtons = originalRenderAutoApproveButtons;
            updatePanelInactiveOverlays = originalUpdatePanelInactiveOverlays;
            apiFetchJson = originalApiFetchJson;
            window.fetch = originalFetch;
          }
        })().then(done).catch(error => done({error: String(error?.stack || error)}));
        """
    )
    expected_api_errors = consume_only_expected_js_debug_api_errors(
        browser,
        tuple(
            {"path": f"/api/test-{suffix}", "method": "GET", "query": {}, "error": "server unavailable"}
            for suffix in ("a", "b", "c")
        ),
    )
    metrics["expectedApiErrorCount"] = len(expected_api_errors)
    assert_fixture_client_event_demand_claimed(browser)
    return metrics


def assert_terminal_navigation_ack_semantics(metrics: dict[str, object]) -> None:
    """Assert identities and state transitions that remain valid under parallel scheduling."""

    assert "error" not in metrics, metrics
    assert metrics["expectedApiErrorCount"] == 3, metrics
    assert metrics["tabAck"]["visible"] is True, metrics
    assert metrics["tabAck"]["state"] == "connecting", metrics
    assert metrics["tabFrameAck"] == {"visible": True, "state": "connecting"}, metrics
    assert metrics["topologyTransactionCounts"] == {
        "slotUpdates": {"2": 1},
        "autoApproveRenders": 1,
        "inactiveOverlayReconciliations": 1,
        "terminalStarts": {"2": 1},
    }, metrics
    assert metrics["failedTab"] == {"stillVisible": True, "retry": True}, metrics
    assert metrics["windowAck"]["state"] == "switching", metrics
    assert metrics["windowAck"]["dimmed"] is True, metrics
    assert metrics["windowFrameAck"] == {"state": "switching", "dimmed": True}, metrics
    assert metrics["windowCleared"] is True, metrics
    assert metrics["backendHealth"] == {"shown": True, "recovered": True}, metrics
