# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Failure ownership regressions across the browser, client, and stats runtime."""

import threading

from yolomux_lib.stats_current import collectors, runtime
from yolomux_lib.local_services import client as local_service_client


class _FutureServiceClient:
    def __init__(self):
        self.releases = []

    def acquire_lease(self):
        return {"ok": True, "lease_id": "lease-1"}

    def renew_lease(self, _lease_id):
        return {"ok": False, "status": "upgrade_required", "required_protocol_version": 25}

    def release_lease(self, lease_id, *, bypass_cached_upgrade=False):
        self.releases.append((lease_id, bypass_cached_upgrade))
        return {"ok": False, "status": "upgrade_required", "required_protocol_version": 25}

    def append(self, **_groups):
        return {"ok": True}

    def status(self):
        return {"ok": True}


def test_terminal_renewal_failure_survives_release_cleanup():
    client = _FutureServiceClient()
    current = runtime.StatsCurrentRuntime(
        client,
        {family: lambda _attempt: collectors.CollectorFacts() for family in runtime.WEB_COLLECTED_FAMILIES},
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
        retry_initial_seconds=0.01,
        retry_max_seconds=0.02,
        owner_check_seconds=0.01,
    )
    try:
        assert current.start() is True
        supervisor = current._supervisor
        assert supervisor is not None
        supervisor.join(1)
        assert supervisor.is_alive() is False
        assert current.status()["supervisor"]["phase"] == "blocked"
        assert current.status()["supervisor"]["last_failure"] == "UpgradeRequired"
        assert client.releases == [("lease-1", True)]
    finally:
        current.stop()


def test_stats_release_timeout_keeps_retry_ownership_until_statsd_acknowledges(monkeypatch):
    releases = []
    settled = threading.Event()

    class Client:
        def release_lease(self, lease_id, *, bypass_cached_upgrade=False):
            releases.append((lease_id, bypass_cached_upgrade))
            if len(releases) == 1:
                return {"ok": False, "_transport_error": "timeout"}
            settled.set()
            return {"ok": True}

        def status(self):
            return {"ok": True}

    monkeypatch.setattr(local_service_client, "LOCAL_SERVICE_LEASE_RELEASE_RETRY_SECONDS", 0.01)
    current = runtime.StatsCurrentRuntime(
        Client(),
        {family: lambda _attempt: collectors.CollectorFacts() for family in runtime.WEB_COLLECTED_FAMILIES},
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
    )

    current._release("lease-1")

    assert settled.wait(1.0), "the retry owner forgot a timed-out statsd lease release"
    assert releases == [("lease-1", False), ("lease-1", False)]
    assert current.status()["supervisor"]["last_failure"] == "LeaseReleaseFailed"
