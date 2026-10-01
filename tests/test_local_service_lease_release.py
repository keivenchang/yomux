# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import threading
import time

from tests.helpers.external_lease_client import assert_daemon_refuses_a_self_lease
from tests.helpers.external_lease_client import external_lease_client
from yomux_lib import app as app_module
from yomux_lib import batchd
from yomux_lib.local_services import client as local_service_client
from yomux_lib.local_services import registry as local_service_registry
from yomux_lib.local_services import rpc
from yomux_lib.local_services.rpc import LocalRpcError
from yomux_lib.local_services.registry import LocalServiceRegistry
from yomux_lib.local_services.registry import LocalServiceSpec


def test_registry_lease_release_timeout_retries_the_typed_transport_failure(tmp_path, monkeypatch):
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("fixture", "tests.fixture", "fixture.sock", 7),
    )
    calls = []
    settled = threading.Event()

    def request(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        settled.set()
        return {"ok": True, "leases": 0}, b""

    monkeypatch.setattr(local_service_registry, "request", request)

    owner = local_service_client.release_local_service_lease_eventually(
        registry.release_lease,
        "lease-timeout",
        retry_seconds=0.01,
    )

    assert settled.wait(timeout=1.0) is True
    assert len(calls) == 2
    assert owner.completed.is_set() is True
    assert owner.terminal_response is None
    assert owner._thread is not None


def test_registry_lease_release_protocol_failure_stops_without_retry(tmp_path, monkeypatch):
    registry = LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("fixture", "tests.fixture", "fixture.sock", 7),
    )
    calls = []
    emitted = []

    def request(*_args, **_kwargs):
        calls.append(True)
        raise LocalRpcError("unsupported RPC version")

    monkeypatch.setattr(local_service_registry, "request", request)
    monkeypatch.setattr(
        local_service_client,
        "emit_server_log",
        lambda *args, **kwargs: emitted.append((args, kwargs)),
    )

    owner = local_service_client.release_local_service_lease_eventually(
        registry.release_lease,
        "lease-protocol",
        retry_seconds=0.01,
    )

    assert calls == [True]
    assert owner.completed.is_set() is True
    assert owner._thread is None
    assert owner.terminal_response is not None
    assert owner.terminal_response["ok"] is False
    assert owner.terminal_response["error"] == "unsupported RPC version"
    # Was the duplicate classifier's bare 'rpc' fallthrough, which collapsed every unnamed
    # OSError and LocalRpcError into one string. Routed through the single owner in rpc.py,
    # "unsupported RPC version" is the case that owner names exactly: a stale peer that needs
    # an upgrade, which a caller must be able to tell apart from a generic transport failure.
    assert owner.terminal_response["_transport_error"] == rpc.LOCAL_SERVICE_REASON_REVISION_MISMATCH
    assert owner.terminal_response["exception_type"] == "LocalRpcError"
    assert owner.terminal_response["cause"]["exception"] == {
        "type": "LocalRpcError",
        "message": "unsupported RPC version",
    }
    assert emitted[0][0][2] == "lease release stopped: unsupported rpc version"
    assert emitted[0][1]["delivery"] == "terminal"


def test_status_generation_release_timeout_retries_until_statusd_acknowledges(monkeypatch):
    webapp = app_module.TmuxWebtermApp(["1"])
    record = webapp.client_watch_service.event_watcher_record
    wait_entered = threading.Event()
    settled = threading.Event()
    releases = []
    monkeypatch.setattr(local_service_client, "LOCAL_SERVICE_LEASE_RELEASE_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(webapp.status_client, "acquire_generation_lease", lambda: {"ok": True, "lease_id": "lease-1"})
    monkeypatch.setattr(webapp.status_client, "snapshot", lambda _sessions, timeout: ({"ok": True, "status": 200, "generation": 7}, b"{}"))

    def probe_generation(_generation):
        wait_entered.set()
        return {"ok": True, "changed": False, "generation": 7}

    def release_generation_lease(lease_id):
        releases.append(lease_id)
        if len(releases) == 1:
            return {"ok": False, "_transport_error": "timeout"}
        settled.set()
        return {"ok": True}

    monkeypatch.setattr(webapp.status_client, "probe_generation", probe_generation)
    monkeypatch.setattr(webapp.status_client, "release_generation_lease", release_generation_lease)
    try:
        assert webapp.start_status_generation_watcher(record) is True
        assert wait_entered.wait(timeout=1.0)
        webapp.stop_status_generation_watcher(record)
        assert settled.wait(1.0), "the retry owner forgot a timed-out statusd lease release"
    finally:
        webapp.control_server.stop()

    assert releases == ["lease-1", "lease-1"]
    assert record.status_generation_lease_id == ""
    assert record.status_generation_worker is None


def test_batchd_interaction_release_timeout_retries_until_the_broker_unpins(tmp_path, monkeypatch):
    """A timed-out interaction-lease release retries until the broker is actually unpinned.

    The client has to be a REAL separate process. A harness naming ``os.getpid()``
    IS the daemon, and the one shared lease fence in ``runtime.acquire_client_lease``
    correctly refuses a daemon the lease that keeps itself alive. Production is not
    shaped like that: the web server holds this interaction lease on a separate batchd
    process, so the fence sees a different pid whose start identity it can verify.
    """
    broker = batchd.PersistentJobBroker(tmp_path / "batchd.sock", idle_seconds=5.0, workers=1)
    release_calls = []

    with external_lease_client() as client_pid:
        class BrokerLeaseRegistry:
            def acquire_lease(self, existing_lease_id=""):
                return broker.handle({
                    "action": "lease",
                    "client_pid": client_pid,
                    "lease_id": existing_lease_id,
                })[0]

            def release_lease(self, lease_id):
                release_calls.append(lease_id)
                if len(release_calls) == 1:
                    return {"ok": False, "_transport_error": "timeout"}
                return broker.handle({"action": "release", "lease_id": lease_id})[0]

        monkeypatch.setattr(local_service_client, "LOCAL_SERVICE_LEASE_RELEASE_RETRY_SECONDS", 0.01)
        lease = app_module.BatchedInteractionLease(type("batchd.BatchClient", (), {"registry": BrokerLeaseRegistry()})())

        # NEGATIVE CONTROL, asserted first: the external stand-in is not a way
        # around the fence. A true self-lease stays refused and never reaches the
        # lease table, so the pin proved on the next line is the real client's and
        # cannot have been bought by the daemon leasing itself.
        assert_daemon_refuses_a_self_lease(broker)
        assert broker.common_status()["clients"] == 0, "the refused self-lease pinned the broker anyway"

        assert lease.acquire() is True
        assert broker.common_status()["clients"] == 1
        lease.release()
        assert lease.held is False

        deadline = time.monotonic() + 1.0
        while broker.common_status()["clients"] and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        assert release_calls == [release_calls[0], release_calls[0]]
        assert broker.common_status()["clients"] == 0
