# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Characterization tests for the shared local-service runtime-row projection."""

from __future__ import annotations

import inspect
from collections import OrderedDict

from yolomux_lib import app
from yolomux_lib import local_service_projection
from yolomux_lib import statusd_client
from yolomux_lib import watchd_client
from yolomux_lib.approval import approvald
from yolomux_lib.infra import jobd
from yolomux_lib.search import search_indexer


class RegistryStub:
    def __init__(self) -> None:
        self.resource_calls: list[tuple[int, tuple[int, ...]]] = []

    def resources(self, pid: int) -> dict[str, object]:
        self.resource_calls.append((pid, ()))
        return {"pid": pid, "scope": "leader"}

    def resources_for_pids(self, pid: int, worker_pids: list[int]) -> dict[str, object]:
        self.resource_calls.append((pid, tuple(worker_pids)))
        return {"pid": pid, "workers": list(worker_pids), "scope": "group"}


def test_local_service_runtime_row_preserves_the_common_wire_values_and_extras():
    resources = {"rss_bytes": 12}
    row = local_service_projection.local_service_runtime_row(
        "sampled",
        pid=17,
        started_at=12.5,
        version=4,
        healthy=True,
        last_failure="",
        resources=resources,
        fields_before_failure={"clients": 2, "queues": {"depth": 1}},
    )

    assert row == {
        "service": "sampled",
        "pid": 17,
        "started_at": 12.5,
        "version": 4,
        "healthy": True,
        "clients": 2,
        "queues": {"depth": 1},
        "last_failure": "",
        "resources": {"rss_bytes": 12},
    }
    resources["rss_bytes"] = 99
    assert row["resources"] == {"rss_bytes": 12}


def test_local_service_runtime_row_preserves_fields_around_failure_and_resources():
    row = local_service_projection.local_service_runtime_row(
        "ordered",
        pid=1,
        started_at=2.0,
        version=None,
        healthy=False,
        last_failure="failed",
        resources={"rss": 3},
        fields_before_failure=OrderedDict((("before", 1),)),
        fields_after_failure=OrderedDict((("after_failure", 2),)),
        fields_after_resources=OrderedDict((("after_resources", 3),)),
    )

    assert list(row) == [
        "service",
        "pid",
        "started_at",
        "healthy",
        "before",
        "last_failure",
        "after_failure",
        "resources",
        "after_resources",
    ]


def test_registry_runtime_row_reads_only_the_supplied_status_and_samples_the_leader(monkeypatch):
    registry = RegistryStub()
    failure_calls: list[tuple[dict[str, object], dict[str, object]]] = []

    def failure_text(status, payload):
        failure_calls.append((status, payload))
        return "latched failure"

    monkeypatch.setattr(local_service_projection, "local_service_failure_text", failure_text)
    status = {"healthy": False, "failure_reason": "latched failure"}
    payload = {"pid": 23, "started_at": 8, "version": 3, "clients": 7}

    row = local_service_projection.registry_runtime_row(
        "daemon",
        registry,
        status,
        payload,
        fields_before_failure={"clients": 7},
    )

    assert row == {
        "service": "daemon",
        "pid": 23,
        "started_at": 8.0,
        "version": 3,
        "healthy": False,
        "clients": 7,
        "last_failure": "latched failure",
        "resources": {"pid": 23, "scope": "leader"},
    }
    assert registry.resource_calls == [(23, ())]
    assert failure_calls == [(status, payload)]


def test_registry_runtime_row_preserves_group_resource_sampling():
    registry = RegistryStub()
    row = local_service_projection.registry_runtime_row(
        "jobd",
        registry,
        {"healthy": True},
        {"pid": 31, "started_at": 9.5},
        resource_pids=(32, 33),
    )

    assert row["resources"] == {"pid": 31, "workers": [32, 33], "scope": "group"}
    assert registry.resource_calls == [(31, (32, 33))]


def test_every_registry_row_producer_delegates_to_the_shared_parent():
    producers = (
        statusd_client.StatusClient.runtime_status,
        watchd_client.WatchClient.runtime_status,
        approvald.ApprovalClient.runtime_status,
        search_indexer.SearchIndexerClient.runtime_status,
        jobd.JobClient.runtime_status,
    )
    for producer in producers:
        source = inspect.getsource(producer)
        assert "registry_runtime_row(" in source, producer.__qualname__


def test_shared_projection_and_watchd_bridge_have_no_lifecycle_entrypoint():
    source = inspect.getsource(local_service_projection.registry_runtime_row)
    assert "ensure_started" not in source
    assert "acquire_lease" not in source

    watchd_bridge = inspect.getsource(app.WatchBridge.watchd_runtime_status)
    assert "local_service_runtime_row(" in watchd_bridge
    assert "registry_process_identity(" in watchd_bridge
    assert ".runtime_status(" not in watchd_bridge
    assert "ensure_started" not in watchd_bridge
    assert "acquire_lease" not in watchd_bridge
