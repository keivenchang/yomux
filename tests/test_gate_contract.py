# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Regression-gate contracts for transient HTTP and browser status handling."""

from http import HTTPStatus
import json
import os
import re

import pytest

from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401
from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_console import acknowledge_and_consume_only_expected_js_debug_failures
from tests.browser_helpers.browser_console import validate_server_log_ring_payload
from tests.browser_helpers.browser_console import validate_server_log_ring_transition
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_http_request
from tests.gate_harness import gate_live_server
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.gate_harness import assert_fixture_client_event_demand_claimed
from tests.gate_harness import claim_fixture_client_event_demand
from tests.gate_harness import load_gate_browser
from tests.gate_harness import release_fixture_client_event_demand
from tests.terminal_state_guard import assert_terminal_transition
from yolomux_lib.server_logs import SERVER_LOGS


DISCOVERED_MUTATING_ACTIONS = {
    "tmux-status-cycle": "pending",
    "terminal-upload": "pending",
    "editor-upload": "pending",
    "tmux-window-select": "optimistic",
    "auto-approve-toggle": "optimistic",
    "event-log-post": "background",
    "self-update": "pending",
    "js-debug-observation-flush": "background",
    "pricing-catalog-refresh": "pending",
    "debug-service-control": "pending",
    "yolo-rule-open": "pending",
    "yolo-rule-reload": "pending",
    "ensure-session": "pending",
    "create-session": "pending",
    "rename-session": "optimistic",
    "kill-session": "pending",
    "settings-save": "pending",
    "yoagent-cancel": "pending",
    "yoagent-reset": "pending",
    "yoagent-job-update": "optimistic",
    "yoagent-wait-clear": "optimistic",
    "yoagent-chat-start": "optimistic",
    "yoagent-action-send": "optimistic",
    "yoagent-prewarm": "background",
    "fs-batch-repair": "background",
    "fs-batch-flush": "background",
    "finder-unindex": "pending",
    "settings-unindex": "background",
    "recovery-preflight": "pending",
    "recovery-preflight-confirm": "pending",
    "recovery-attach-existing": "pending",
    "recovery-repair-pane": "pending",
    "recovery-recover": "pending",
    "recovery-dismiss": "optimistic",
    "recovery-recover-all-start": "pending",
    "recovery-recover-all-next": "pending",
    "recovery-recover-all-action": "pending",
    "tmux-copy-selection": "pending",
    "recovery-adopt": "pending",
    "chat-api-post": "optimistic",
    "drop-action-run": "pending",
    "attention-ack": "background",
    "editor-save": "pending",
    "stats-read-fence-retry": "background",
    "stats-manual-retry": "pending",
    "finder-file-create": "pending",
    "finder-folder-create": "pending",
    "finder-delete": "optimistic",
    "finder-rename": "optimistic",
    "watch-roots-sync": "background",
}

_USER_COMMAND_CONTRACT_CLASSES = frozenset({"pending", "optimistic"})


@pytest.mark.browser
@pytest.mark.socket
def test_k0_browser_command_registry_exactly_matches_the_declared_inventory(browser, gate_live_server):
    """Every discovered mutation is declared once in the browser registry and assigned one lifecycle class."""
    assert len(DISCOVERED_MUTATING_ACTIONS) == 50, DISCOVERED_MUTATING_ACTIONS
    load_gate_browser(browser, gate_live_server)
    inventory = browser.execute_script(
        """
        if (typeof COMMAND_ROUTES !== 'object' || COMMAND_ROUTES === null) return null;
        return Object.fromEntries(Object.entries(COMMAND_ROUTES).map(([name, route]) => [name, route?.contractClass || '']));
        """
    )
    assert inventory is not None, "COMMAND_ROUTES is absent"
    assert inventory == DISCOVERED_MUTATING_ACTIONS


@pytest.mark.browser
@pytest.mark.socket
def test_k0_unregistered_mutating_request_fails_closed_before_network(browser, gate_live_server):
    """A newly added mutation cannot bypass acknowledgement while waiting for inventory registration."""
    load_gate_browser(browser, gate_live_server)
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const originalFetch = window.fetch;
        let calls = 0;
        window.fetch = async () => {
          calls += 1;
          return new Response(JSON.stringify({ok: true}), {status: 200, headers: {'Content-Type': 'application/json'}});
        };
        apiFetch('/api/gate-unregistered-mutation', {method: 'POST'})
          .then(() => done({calls, resolved: true}))
          .catch(error => done({
            calls,
            resolved: false,
            code: String(error?.code || error?.reason_code || ''),
          }))
          .finally(() => { window.fetch = originalFetch; });
        """
    )
    assert result == {
        "calls": 0,
        "resolved": False,
        "code": "unregistered_mutating_command",
    }


@pytest.mark.browser
@pytest.mark.socket
@pytest.mark.parametrize("contract_class", sorted(_USER_COMMAND_CONTRACT_CLASSES))
def test_k0_each_user_command_class_executes_k1_through_k5(
    browser,
    gate_live_server,
    contract_class,
):
    """The shared dispatcher proves visible acknowledgement, disable/finally, de-duplication, rollback, and live semantics per route class."""
    load_gate_browser(browser, gate_live_server)
    result = browser.execute_async_script(
        """
        const contractClass = arguments[0];
        const done = arguments[arguments.length - 1];
        if (typeof commandRoute !== 'function' || typeof dispatchCommand !== 'function') {
          done({missing: ['commandRoute', 'dispatchCommand'].filter(name => typeof window[name] !== 'function')});
          return;
        }

        const host = document.getElementById('grid') || document.body;
        const control = document.createElement('button');
        control.type = 'button';
        control.textContent = 'Gate command';
        host.appendChild(control);
        const beforeStatuses = new Map(Array.from(document.querySelectorAll('[role="status"], [aria-live]'))
          .map(node => [node, node.textContent || '']));
        let fetchCalls = 0;
        let rejectRequest = null;
        let optimisticState = false;
        let rolledBack = false;
        const originalFetch = window.fetch;
        const finish = result => {
          window.fetch = originalFetch;
          control.remove();
          done(result);
        };
        const heldRequest = (url, options = {}) => {
          const parsed = new URL(String(url), location.href);
          if (parsed.pathname !== '/api/gate-command-contract') return originalFetch(url, options);
          fetchCalls += 1;
          return new Promise((_resolve, reject) => { rejectRequest = reject; });
        };
        window.fetch = heldRequest;

        const descriptor = commandRoute({
          id: `gate-${contractClass}`,
          method: 'POST',
          path: '/api/gate-command-contract',
          contractClass,
          pendingLabel: 'Gate command pending',
          optimistic: contractClass === 'optimistic' ? () => {
            optimisticState = true;
            return {previous: false};
          } : undefined,
          rollback: contractClass === 'optimistic' ? undo => {
            optimisticState = undo?.previous === true;
            rolledBack = true;
          } : undefined,
        });
        const first = dispatchCommand(descriptor, {}, control);
        const second = dispatchCommand(descriptor, {}, control);

        requestAnimationFrame(() => {
          (async () => {
            try {
            const changedStatuses = Array.from(document.querySelectorAll('[role="status"], [aria-live]'))
              .filter(node => (node.textContent || '').trim() && (node.textContent || '') !== beforeStatuses.get(node));
            const visibleStatus = changedStatuses.find(node => {
              const style = getComputedStyle(node);
              const rect = node.getBoundingClientRect();
              return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) > 0
                && rect.width > 1 && rect.height > 1;
            });
            const pending = {
              fetchCalls,
              disabled: control.disabled === true,
              busy: control.getAttribute('aria-busy') === 'true',
              visibleAcknowledgement: Boolean(visibleStatus),
              role: visibleStatus?.getAttribute('role') || '',
              live: visibleStatus?.getAttribute('aria-live') || '',
              optimisticState,
            };
            if (typeof rejectRequest !== 'function') {
              finish({
                pending,
                diagnostic: {code: 'command_request_not_started'},
              });
              return;
            }
            rejectRequest(new Error('fixture command failure'));
            let callerResults = null;
            Promise.allSettled([first, second]).then(results => {
              callerResults = results;
            });
            try {
              await window.__yolomuxTestWaitFor(
                () => callerResults || null,
                {timeoutMs: 2000, description: 'deduplicated gate command callers to settle'},
              );
            } catch (error) {
              finish({
                pending,
                diagnostic: {
                  code: 'command_callers_never_settled',
                  error: String(error?.message || error),
                  disabled: control.disabled === true,
                  busy: control.getAttribute('aria-busy') === 'true',
                  pendingPhaseCount: document.querySelectorAll(
                    '[data-command-status-owned="true"][data-command-status-phase="pending"]',
                  ).length,
                },
              });
              return;
            }
            {
              const pendingPhaseCount = document.querySelectorAll(
                '[data-command-status-owned="true"][data-command-status-phase="pending"]',
              ).length;
              const terminalStatus = document.querySelector(
                '[data-command-status-owned="true"][data-command-status-phase="error"]',
              );
              const terminal = {
                phase: terminalStatus?.dataset.commandStatusPhase || '',
                role: terminalStatus?.getAttribute('role') || '',
                live: terminalStatus?.getAttribute('aria-live') || '',
              };
              const settled = {
                callerStates: callerResults.map(result => result.status),
                enabled: control.disabled === false,
                notBusy: control.getAttribute('aria-busy') !== 'true',
                pendingPhaseCount,
                terminal,
                optimisticState,
                rolledBack,
              };
              const diagnosticEvent = jsDebugFailureEvents().find(event => {
                if (event?.type !== 'api') return false;
                return new URL(String(event.endpoint || ''), location.href).pathname === '/api/gate-command-contract';
              }) || null;
              finish({pending, settled, diagnosticEvent});
            }
            } catch (error) {
              finish({error: String(error?.message || error)});
            }
          })();
        });
        """,
        contract_class,
    )
    diagnostic_event = result.pop("diagnosticEvent", None)
    assert diagnostic_event is not None, result
    retired = acknowledge_and_consume_only_expected_js_debug_failures(browser, (diagnostic_event,))
    assert retired == (diagnostic_event,)
    assert result.get("error") is None, result
    assert "missing" not in result, result
    pending = result.get("pending", {})
    settled = result.get("settled", {})
    assert_terminal_transition(
        contract_id="command-dispatch-pending",
        pending_observed=(
            pending.get("fetchCalls") == 1
            and pending.get("disabled") is True
            and pending.get("busy") is True
            and pending.get("visibleAcknowledgement") is True
        ),
        terminal_observed=(
            result.get("diagnostic") is None
            and settled.get("callerStates") == ["rejected", "rejected"]
            and settled.get("enabled") is True
            and settled.get("notBusy") is True
            and settled.get("pendingPhaseCount") == 0
            and settled.get("terminal", {}).get("phase") == "error"
        ),
        evidence=result,
    )
    assert result.get("diagnostic") is None, result
    assert result["pending"]["fetchCalls"] == 1, result
    assert result["pending"]["disabled"] is True, result
    assert result["pending"]["busy"] is True, result
    assert result["pending"]["visibleAcknowledgement"] is True, result
    assert result["pending"]["role"] == "status", result
    assert result["pending"]["live"] in {"polite", "assertive"}, result
    assert result["settled"]["enabled"] is True, result
    assert result["settled"]["notBusy"] is True, result
    assert result["settled"]["callerStates"] == ["rejected", "rejected"], result
    assert result["settled"]["pendingPhaseCount"] == 0, result
    assert result["settled"]["terminal"] == {
        "phase": "error",
        "role": "alert",
        "live": "assertive",
    }, result
    if contract_class == "optimistic":
        assert result["pending"]["optimisticState"] is True, result
        assert result["settled"]["rolledBack"] is True, result
        assert result["settled"]["optimisticState"] is False, result


@pytest.mark.browser
@pytest.mark.socket
def test_m1_first_delivery_is_not_retried_and_one_replacement_stream_repairs(browser, gate_live_server):
    load_gate_browser(browser, gate_live_server)
    ownership = claim_fixture_client_event_demand(browser)
    assert ownership["bound"]["sourceOrigin"] == gate_live_server.base_url
    assert ownership["claimed"] == {
        "scheduled": False,
        "enabled": False,
        "timerPending": False,
        "sourcePresent": False,
    }
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const session = arguments[0];
        const originalFetch = window.fetch;
        const originalEventSource = window.EventSource;
        const variants = [
          ['queued-202', () => new Response(JSON.stringify({ok: true, status: 'queued', ticket: 'ticket-1', key: 'events'}), {
            status: 202,
            headers: {'Content-Type': 'application/json'},
          })],
          ['unavailable-503', () => new Response(JSON.stringify({status: 'unavailable', reason: 'warming'}), {
            status: 503,
            headers: {'Content-Type': 'application/json'},
          })],
          ['wrong-body', () => new Response('not-json', {
            status: 200,
            headers: {'Content-Type': 'text/plain'},
          })],
        ];
        const results = [];
        const pane = document.getElementById(`events-pane-${session}`);
        const paneWasActive = pane.classList.contains('active');
        pane.classList.remove('active');

        class ReplacementEventSource {
          constructor(url) {
            this.url = String(url);
            this.listeners = new Map();
            this.readyState = 1;
            ReplacementEventSource.instances.push(this);
          }
          addEventListener(type, listener) {
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(listener);
          }
          close() { this.readyState = 2; }
        }
        ReplacementEventSource.instances = [];
        window.EventSource = ReplacementEventSource;

        (async () => {
          const omittedOperationsSignature = clientEventDemandSignature({channels: ['events']});
          const emptyOperationsSignature = clientEventDemandSignature({channels: ['events'], operations: []});
          const canonicalSignature = clientEventDemandSignature({
            channels: ['status', 'events', 'status', '', null],
            operations: ['op-b', 'op-a', 'op-b', '', null],
          });
          const omittedOperationsSource = openClientEventStream({channels: ['events']});
          const omittedOperationsUrl = new URL(omittedOperationsSource.url, location.href);
          closeClientEventStream();
          const canonicalSource = openClientEventStream({
            channels: ['status', 'events', 'status', '', null],
            operations: ['op-b', 'op-a', 'op-b', '', null],
          });
          const canonicalUrl = new URL(canonicalSource.url, location.href);
          closeClientEventStream();
          const streamsBeforeEmptyDemand = ReplacementEventSource.instances.length;
          const emptyDemandSource = openClientEventStream({operations: ['op-a']});
          const normalization = {
            omittedMatchesEmpty: omittedOperationsSignature === emptyOperationsSignature,
            omittedChannels: omittedOperationsUrl.searchParams.get('channels'),
            omittedOperations: omittedOperationsUrl.searchParams.get('operations'),
            canonicalSignature,
            canonicalChannels: canonicalUrl.searchParams.get('channels'),
            canonicalOperations: canonicalUrl.searchParams.get('operations'),
            emptyDemandSource: emptyDemandSource === null,
            emptyDemandOpenedStream: ReplacementEventSource.instances.length !== streamsBeforeEmptyDemand,
          };

          for (const [variantIndex, [name, firstResponse]] of variants.entries()) {
            closeClientEventStream();
            ReplacementEventSource.instances.length = 0;
            const marker = `m1-${variantIndex}-${name}`;
            let controlledCalls = 0;
            let unrelatedCalls = 0;
            let recoveryCalls = 0;
            let phase = 'controlled';
            pane.classList.remove('active');
            window.fetch = async (url, options = {}) => {
              const parsed = new URL(String(url), location.href);
              if (parsed.pathname !== '/api/events' || parsed.searchParams.get('session') !== session) {
                return originalFetch(url, options);
              }
              if (parsed.searchParams.get('gate_m1_probe') === marker) {
                controlledCalls += 1;
                return firstResponse();
              }
              if (parsed.searchParams.has('gate_m1_probe')) return originalFetch(url, options);
              if (phase === 'unrelated') unrelatedCalls += 1;
              else if (phase === 'ready') recoveryCalls += 1;
              else return originalFetch(url, options);
              return new Response(JSON.stringify({events: []}), {
                status: 200,
                headers: {'Content-Type': 'application/json'},
              });
            };

            const servingSource = openClientEventStream({channels: ['events']});
            servingSource.closeCalls = 0;
            const servingClose = servingSource.close.bind(servingSource);
            servingSource.close = () => { servingSource.closeCalls += 1; servingClose(); };
            clientEventTransportState.connected = true;
            await apiFetchJsonQuiet(`/api/events?session=${encodeURIComponent(session)}&limit=120&gate_m1_probe=${encodeURIComponent(marker)}`).catch(() => null);
            phase = 'unrelated';
            await refreshEventLog(session);
            const controlledCallsAfterUnrelated = controlledCalls;
            const source = openClientEventStream({channels: ['events']}, {replace: true});
            pane.classList.add('active');
            phase = 'ready';
            source.listeners.get('ready')[0]({
              data: JSON.stringify({epoch: `replacement-${name}`, resource_revisions: {}}),
              type: 'ready',
              lastEventId: '',
            });
            while (eventLogRefreshRecord(session).request) await eventLogRefreshRecord(session).request;
            await Promise.resolve();
            results.push({
              name,
              controlledCalls,
              controlledCallsAfterUnrelated,
              unrelatedCalls,
              replacementStreams: ReplacementEventSource.instances.length,
              recoveryCalls,
              distinctSources: servingSource !== source,
              readyHandoff: clientEventTransportState.source === source && clientEventTransportState.replacementSource === null,
              oldCloseCalls: servingSource.closeCalls,
            });
            pane.classList.remove('active');
          }
          done({normalization, recoveries: results});
        })().catch(error => done({error: String(error?.stack || error)})).finally(() => {
          closeClientEventStream();
          pane.classList.toggle('active', paneWasActive);
          window.fetch = originalFetch;
          window.EventSource = originalEventSource;
        });
        """,
        gate_live_server.tmux.sessions[0],
    )
    assert_fixture_client_event_demand_claimed(browser)
    release_fixture_client_event_demand(browser)
    assert result == {
        "normalization": {
            "omittedMatchesEmpty": True,
            "omittedChannels": "events",
            "omittedOperations": None,
            "canonicalSignature": '{"channels":["events","status"],"operations":["op-a","op-b"]}',
            "canonicalChannels": "events,status",
            "canonicalOperations": "op-a,op-b",
            "emptyDemandSource": True,
            "emptyDemandOpenedStream": False,
        },
        "recoveries": [
            {"name": "queued-202", "controlledCalls": 1, "controlledCallsAfterUnrelated": 1, "unrelatedCalls": 1, "replacementStreams": 2, "recoveryCalls": 1, "distinctSources": True, "readyHandoff": True, "oldCloseCalls": 1},
            {"name": "unavailable-503", "controlledCalls": 1, "controlledCallsAfterUnrelated": 1, "unrelatedCalls": 1, "replacementStreams": 2, "recoveryCalls": 1, "distinctSources": True, "readyHandoff": True, "oldCloseCalls": 1},
            {"name": "wrong-body", "controlledCalls": 1, "controlledCallsAfterUnrelated": 1, "unrelatedCalls": 1, "replacementStreams": 2, "recoveryCalls": 1, "distinctSources": True, "readyHandoff": True, "oldCloseCalls": 1},
        ],
    }


@pytest.mark.browser
@pytest.mark.socket
def test_m2_api_fetch_applies_timeout_and_abort_signal(browser, gate_live_server):
    load_gate_browser(browser, gate_live_server)
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const originalFetch = window.fetch;
        let capturedSignal = null;
        window.fetch = (url, options = {}) => {
          if (String(url) !== '/api/gate-timeout') return originalFetch(url, options);
          return new Promise((_resolve, reject) => {
            capturedSignal = options.signal || null;
            capturedSignal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), {once: true});
          });
        };
        const request = apiFetch('/api/gate-timeout', {timeoutMs: 25}, {provenance: 'controlled_probe'})
          .then(() => ({outcome: 'resolved'}))
          .catch(error => ({outcome: error?.name || 'rejected'}));
        Promise.race([
          request,
          new Promise(resolve => setTimeout(() => resolve({outcome: 'watchdog'}), 250)),
        ]).then(outcome => {
          const event = [...jsDebugEvents].reverse().find(item => (
            item?.type === 'api' && item?.endpoint === '/api/gate-timeout'
          ));
          done({
            ...outcome,
            hasSignal: capturedSignal instanceof AbortSignal,
            aborted: capturedSignal?.aborted === true,
            event,
          });
        }).finally(() => { window.fetch = originalFetch; });
        """
    )
    event = result["event"]
    retired = acknowledge_and_consume_only_expected_js_debug_failures(browser, (event,))
    assert retired == (event,)
    assert result["outcome"] == "AbortError", result
    assert result["hasSignal"] is True and result["aborted"] is True, result
    assert event["type"] == "api", event
    assert event["endpoint"] == "/api/gate-timeout", event
    assert event["error"] == "The operation was aborted.", event
    assert event["provenance"] == "controlled_probe", event

    blocking = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const originalFetch = window.fetch;
        (async () => {
          window.fetch = async (url, options) => {
            const target = String(url);
            if (target === '/api/gate-unmarked-abort') {
              throw new DOMException('unmarked abort', 'AbortError');
            }
            if (target === '/api/gate-500') {
              return new Response(JSON.stringify({error: 'forced failure'}), {
                status: 500,
                headers: {'Content-Type': 'application/json'},
              });
            }
            // Every OTHER request must pass through to the real server. A broad stub that returned
            // 500 to every URL turned the ambient 3s client-health /api/ping (measureClientHealth)
            // into a spurious extra blocking event, so the deliberate three-event sequence could not
            // be asserted exactly under load.
            return originalFetch(target, options);
          };
          await apiFetch('/api/gate-unmarked-abort').catch(() => null);
          const response = await apiFetch('/api/gate-500');
          await response.text();
          // Deterministic ambient client-health ping in the same stub window: with the narrow stub it
          // passes through to the live server and records nothing; with the old broad stub it would
          // 500 and add a fourth blocking event. This is the ambient producer the oracle must tolerate.
          await apiFetch('/api/ping').catch(() => null);
          recordJsDebugEvent('unhandledrejection', {
            ...jsDebugFailureDetails('unhandledrejection', new Error('rejected upload')),
            endpoint: '/api/upload',
          });
          const events = jsDebugFailureEvents();
          window.fetch = originalFetch;
          done(events);
        })().catch(error => {
          window.fetch = originalFetch;
          done({error: String(error?.stack || error)});
        });
        """
    )
    assert [item["type"] for item in blocking] == ["api", "api", "unhandledrejection"], blocking
    assert [item.get("endpoint") for item in blocking] == [
        "/api/gate-unmarked-abort",
        "/api/gate-500",
        "/api/upload",
    ], blocking
    assert all("provenance" not in item for item in blocking), blocking
    retired_blocking = acknowledge_and_consume_only_expected_js_debug_failures(browser, blocking)
    assert [item["id"] for item in retired_blocking] == [item["id"] for item in blocking]


@pytest.mark.browser
@pytest.mark.socket
def test_m3_live_daemon_transport_failure_is_not_reported_as_process_down(browser, gate_live_server):
    """A running daemon whose transport failed is `issue` + `transport_failed`, never down.

    This used to call `debugSystemServiceState` in the browser -- a JavaScript re-derivation of a
    rule `yolomux_lib/app.py:system_status_service` already owned, from `pid`/`healthy`/
    `transport_reason`. Two classifiers for one rule, and the panel-side copy was the one the
    contract watched. The classifier is retired, so the contract now runs the WHOLE path it cares
    about: the live server classifies a transport-failed row, and the live browser renders that
    exact published row through the roster.

    The distinction being protected is a real operator decision. A process that is not running is
    something to start; a process that IS running and cannot be reached is something to
    investigate, and reporting the second as the first sends the reader to the wrong place.
    """
    load_gate_browser(browser, gate_live_server)

    # PRODUCER. The live server's own classifier, on a row whose process is up and whose transport
    # is refusing -- the exact shape the retired JavaScript branch existed for.
    published = gate_live_server.app.system_status_service({
        "service": "statsd",
        "pid": os.getpid(),
        "launcher_pid": os.getpid(),
        "healthy": False,
        "transport_reason": "rpc_refused",
        "last_failure": "status transport refused",
    })
    assert published["state"] == "issue", published
    assert published["reason_code"] == "transport_failed", published
    assert published["reason"] == "rpc_refused", published
    # The two readings this contract exists to prevent, at the producer.
    assert published["state"] != "unavailable", published
    assert published["state"] != "not_running", published

    # CONSUMER. That published row, rendered by the live bundle's one roster renderer. Nothing is
    # re-derived here: the row goes in as the backend emitted it.
    rendered = browser.execute_script(
        """
        const published = arguments[0];
        const html = debugSystemRosterHtml({
          local_services: {schema_version: 3, inventory: ['statsd'], services: [published]},
        }, {nowSeconds: 0});
        const host = document.createElement('div');
        host.innerHTML = html;
        const row = host.querySelector('[data-subsystem-row][data-subsystem-id="statsd"]');
        return {
          state: row.dataset.subsystemState,
          tone: row.querySelector('[data-subsystem-tone]').dataset.subsystemTone,
          label: row.querySelector('[data-subsystem-state-label]').textContent.trim(),
          reason: row.querySelector('[data-subsystem-reason]').textContent.trim(),
        };
        """,
        published,
    )
    assert rendered["state"] == "issue", rendered
    assert rendered["tone"] == "bad", rendered
    assert rendered["reason"] == "rpc_refused", rendered
    # The label a reader sees says the service has a problem; it must not say the process is gone
    # or that it is idle, which are the two wrong destinations.
    assert "not running" not in rendered["label"].lower(), rendered
    assert "idle" not in rendered["label"].lower(), rendered
    assert rendered["tone"] not in {"muted", "good"}, rendered


def test_m4_ready_queued_and_typed_errors_keep_distinct_http_schemas(gate_live_server, monkeypatch):
    forwarder = gate_live_server.app.stats_current_http
    monkeypatch.setattr(forwarder.client, "ensure_started", lambda: True)
    server_log_start = validate_server_log_ring_payload(gate_live_server.server_log_boundary)
    path = (
        "/api/stats-delta?range_seconds=300&resolution_seconds=1&client_id=gate"
        "&after_cache_generation=0&after_revision=0"
    )
    cases = (
        (
            "ready",
            {
                "ok": True,
                "status": "ready",
                "content_type": "application/json",
                "generation": 11,
                "freshness": "fresh",
            },
            json.dumps(
                {"ok": True, "status": "ready", "data": {"points": []}, "generation": 11, "freshness": "fresh"}
            ).encode("utf-8"),
        ),
        (
            "queued",
            {"ok": True, "status": "queued", "ticket": "stats-ticket-12", "key": "stats:300:1"},
            b"",
        ),
        (
            "unavailable",
            {"ok": False, "status": "unavailable", "reason": "stats storage is unavailable"},
            b"",
        ),
        (
            "upgrade_required",
            {"ok": False, "status": "upgrade_required", "reason": "reader protocol is too old"},
            b"",
        ),
    )
    observed = {}
    for name, metadata, body in cases:
        monkeypatch.setattr(forwarder.client, "delta", lambda _request, result=(metadata, body): result)
        response = gate_http_request(gate_live_server, path)
        observed[name] = {"status": response.status, "payload": response.json()}

    request_ids = {name: str(result["payload"]["request"]["id"]) for name, result in observed.items()}
    assert len(set(request_ids.values())) == len(request_ids), request_ids
    assert all(re.fullmatch(r"r-[A-Za-z0-9._-]{1,120}", request_id) for request_id in request_ids.values())

    server_log_current = validate_server_log_ring_payload(SERVER_LOGS.payload())
    transition = validate_server_log_ring_transition(server_log_start, server_log_current)
    failures = [
        entry
        for entry in transition["newLogs"]
        if str(entry.get("level") or "").lower() in {"warning", "error"}
    ]
    assert transition["droppedCount"] == 0 and len(failures) == 1, transition
    failure = failures[0]
    failure_payload = json.loads(str(failure["message"]))
    assert {
        "level": failure["level"],
        "source": failure["source"],
        "category": failure["category"],
        "code": failure_payload["code"],
        "request_id": failure_payload["request"]["id"],
    } == {
        "level": "error",
        "source": "api-response",
        "category": "api",
        "code": "request_failed",
        "request_id": request_ids["upgrade_required"],
    }
    gate_live_server.server_log_boundary = server_log_current

    assert observed["ready"] == {
        "status": HTTPStatus.OK,
        "payload": {
            "state": "ready",
            "request": {"id": request_ids["ready"]},
            "data": {"points": []},
            "ok": True,
            "terminal": True,
            "status": "ready",
            "generation": 11,
            "freshness": "fresh",
        },
    }
    assert observed["queued"] == {
        "status": HTTPStatus.ACCEPTED,
        "payload": {
            "state": "queued",
            "request": {"id": request_ids["queued"]},
            "operation": {
                "id": "stats:300:1",
                "progress": {
                    "phase": "accepted",
                    "legacy": {
                        "ok": True,
                        "status": "queued",
                        "ticket": "stats-ticket-12",
                        "key": "stats:300:1",
                    },
                },
            },
            "ok": True,
            "terminal": False,
            "status": "queued",
            "ticket": "stats-ticket-12",
            "key": "stats:300:1",
        },
    }
    assert observed["unavailable"] == {
        "status": HTTPStatus.ACCEPTED,
        "payload": {
            "state": "queued",
            "request": {"id": request_ids["unavailable"]},
            "ok": True,
            "terminal": False,
            "status": "pending",
            "retry_after_seconds": 1,
            "reason": "upstream service is refreshing",
        },
    }
    assert observed["upgrade_required"] == {
        "status": HTTPStatus.UPGRADE_REQUIRED,
        "payload": {
            "state": "failed",
            "request": {"id": request_ids["upgrade_required"]},
            "error": {
                "code": "request_failed",
                "message": {
                    "key": "common.requestFailed",
                    "params": {},
                    "fallback": "reader protocol is too old",
                },
                "origin": "server.http",
                "retryable": False,
                "details": {"ok": False, "reason": "reader protocol is too old"},
                "stack": [{
                    "component": "server.http",
                    "operation": "GET /api/stats-delta",
                    "code": "request_failed",
                }],
                "reason": "reader protocol is too old",
            },
            "user_message": {
                "key": "common.requestFailed",
                "params": {},
                "fallback": "reader protocol is too old",
            },
            "legacy_error": "reader protocol is too old",
            "status": HTTPStatus.UPGRADE_REQUIRED,
            "ok": False,
            "terminal": True,
            "reason": "reader protocol is too old",
        },
    }
