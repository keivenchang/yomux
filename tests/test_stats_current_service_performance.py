# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Sampling-path performance contracts for current YO!stats."""

from yolomux_lib import local_service_projection


def test_service_sampling_skips_system_only_diagnostics():
    diagnostic_calls = []
    producers = {
        service: (lambda name=service: {
            "service": name,
            "pid": 1,
            "resources": {"cpu_percent": 2.0, "rss_bytes": 3},
        })
        for service in local_service_projection.LOCAL_SERVICE_INVENTORY
    }
    collector = local_service_projection.LocalServicesCollector(
        lambda: producers,
        ledger=lambda: diagnostic_calls.append("ledger") or {"expensive": True},
        recovery_events=lambda _rows: diagnostic_calls.append("recovery") or ({"kind": "x"},),
    )

    snapshot = collector.collect(include_diagnostics=False)

    assert tuple(row.service for row in snapshot.rows) == local_service_projection.LOCAL_SERVICE_INVENTORY
    assert snapshot.ledger == {}
    assert snapshot.recovery_events == ()
    assert diagnostic_calls == []
