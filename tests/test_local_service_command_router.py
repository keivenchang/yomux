# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Characterization of approvald/statusd command routing."""

from __future__ import annotations

import os
import threading

import pytest

from yolomux_lib.approval import approvald
from yolomux_lib import statusd
from yolomux_lib import watchd
from yolomux_lib.infra import batchd
from yolomux_lib.search import search_indexer
from yolomux_lib.stats_current import service as stats_service
from yolomux_lib.local_services.command_router import LocalServiceCommandRouter


def test_router_has_fixed_vocabulary_and_unknown_is_not_dispatched():
    router = LocalServiceCommandRouter({"one": "handle_one"})

    class Owner:
        def handle_one(self, request, body):
            return {"request": request}, body

    owner = Owner()
    assert router.actions == frozenset({"one"})
    assert router.dispatch(owner, "one", {"action": "one"}, b"body") == ({"request": {"action": "one"}}, b"body")
    assert router.dispatch(owner, "missing", {}, b"") is None
    with pytest.raises(ValueError, match="non-empty"):
        LocalServiceCommandRouter({})


@pytest.mark.parametrize(("router", "actions"), (
    (approvald.APPROVALD_COMMAND_ROUTER, {"ping", "status", "profile", "drain", "lease", "release", "start_worker", "status_target", "status_session", "has_pending_prompt", "alive", "stop_target", "stop_session", "shutdown", "shutdown_if_idle"}),
    (statusd.STATUSD_COMMAND_ROUTER, {"ping", "status", "profile", "snapshot", "inventory", "activity_summary", "wait_generation", "invalidate", "lease", "release", "shutdown", "shutdown_if_idle", "orphan_diagnostics"}),
    (batchd.BATCHD_COMMAND_ROUTER, set(batchd.BATCHD_REQUEST_ACTIONS) - set(batchd.BATCHD_ARTIFACT_ACTION_METHODS)),
    (batchd.BATCHD_ARTIFACT_COMMAND_ROUTER, set(batchd.BATCHD_ARTIFACT_ACTION_METHODS)),
    (search_indexer.INDEXER_COMMAND_ROUTER, {"ping", "status", "profile", "drain", "lease", "release", "shutdown_if_idle", "enqueue", "search", "drain_search_progress", "unindex", "promote", "shutdown"}),
    (watchd.WATCHD_COMMAND_ROUTER, {"ping", "status", "snapshot", "snapshot_product", "lease", "release", "upsert", "remove", "wait_revision", "shutdown", "shutdown_if_idle"}),
    (stats_service.STATS_COMMAND_ROUTER, set(stats_service.STATS_COMMAND_ACTIONS)),
))
def test_daemon_router_action_vocabulary_is_exact(router, actions):
    assert router.actions == frozenset(actions)


def test_approvald_common_action_matrix_and_idle_transitions(tmp_path):
    service = approvald.PersistentApprovalService(tmp_path / "approvald.sock")
    matrix = {
        "ping": {"ok": True, "service": "approvald", "pid": os.getpid(), "version": approvald.APPROVALD_PROTOCOL_VERSION},
        "profile": {"ok": True, "profile": service.status()},
        "drain": {"ok": True, "drained": True, "targets": 0},
        "status_target": {"ok": True, "status": {"target": "missing", "enabled": False, "approved": 0, "blocked": 0}},
        "status_session": {"ok": True, "session": "6", "statuses": []},
        "has_pending_prompt": {"ok": True, "pending": False},
        "alive": {"ok": True, "alive": False},
        "stop_target": {"ok": True, "stopped": True, "target": "missing"},
        "stop_session": {"ok": True, "session": "6", "stopped": True, "targets": [], "statuses": []},
    }
    fields = {"status_target": {"target": "missing"}, "has_pending_prompt": {"target": "missing"}, "alive": {"target": "missing"}, "stop_target": {"target": "missing"}, "status_session": {"session": "6"}, "stop_session": {"session": "6"}}
    for action, expected in matrix.items():
        response, body = service.handle({"action": action, **fields.get(action, {})})
        assert response == expected, action
        assert body == b"", action

    unknown, body = service.handle({"action": "new-command"})
    assert unknown == {"ok": False, "error": "unknown action: new-command"}
    assert body == b""
    service.leases["held"] = {"pid": os.getpid()}
    assert service.handle({"action": "shutdown_if_idle"})[0] == {"ok": True, "shutdown": False, "leases": 1, "targets": 0}
    assert not service.stop_event.is_set()
    service.leases.clear()
    assert service.handle({"action": "shutdown_if_idle"})[0] == {"ok": True, "shutdown": True}
    assert service.stop_event.is_set()


def test_statusd_validation_precedes_router_and_shutdown_notifies(tmp_path):
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
    invalid, body = service.handle({"action": "ping", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION + 1})
    assert invalid == {"ok": False, "error": "upgrade_required", "required_protocol_version": statusd.STATUSD_PROTOCOL_VERSION}
    assert body == b""
    unknown, body = service.handle({"action": "new-command", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION})
    assert unknown == {"ok": False, "error": "unknown status action", "required_protocol_version": statusd.STATUSD_PROTOCOL_VERSION}
    assert body == b""

    ping, body = service.handle({"action": "ping", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION})
    assert ping == {"ok": True, "service": "statusd", "pid": os.getpid(), "version": statusd.STATUSD_PROTOCOL_VERSION, "code_revision": statusd.STATUSD_CODE_REVISION, "build_revision": 1}
    assert body == b""

    waiter_ready = threading.Event()
    waiter_woke = threading.Event()
    def waiter():
        with service.lock:
            waiter_ready.set()
            service.lock.wait(timeout=1)
            waiter_woke.set()
    thread = threading.Thread(target=waiter)
    thread.start()
    assert waiter_ready.wait(timeout=1)
    assert service.handle({"action": "shutdown", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION})[0] == {"ok": True, "shutdown": True}
    assert waiter_woke.wait(timeout=1)
    thread.join(timeout=1)
