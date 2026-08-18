"""M2 and M3 of DOIT.p0.daemon-monitor: the watchd decision, and the extracted collector.

M2 -- watchd is observed WITHOUT calling its status RPC. `WatchClient.runtime_status`
exists and stays uncalled from production, because reaching it from a diagnostics path
demand-starts a demand-scoped service. Its identity and uptime come from the persisted
service record instead, which costs one file read and no traffic at all.

M3 -- `runtime_local_services()` is now a render of one immutable snapshot produced by
`LocalServicesCollector`. Every service, including statsd, is one named row producer;
statsd's row is no longer an inline dict literal inside the projection.

Every assertion here is a negative control as well as a positive one: the poisoned-input
tests below (`..._is_rejected`) are the permanent proof that the guard actually fires,
because a guard nobody has watched go red is a guard nobody has tested.
"""

from __future__ import annotations

import ast
import json
import os
import textwrap
import time
from pathlib import Path

import pytest

from yolomux_lib import app as app_module
from yolomux_lib import local_service_projection
from yolomux_lib.backend_health import store as store_module
from yolomux_lib.backend_health.store import BackendHealthStore
from yolomux_lib.backend_health.store import HealthSnapshot
from yolomux_lib.backend_health.store import ResourceObservation
from yolomux_lib.local_services import rpc
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.rpc import reset_local_service_traffic
from yolomux_lib.search.search_indexer import SearchIndexerClient
from yolomux_lib.stats_current.client import StatsCurrentClient
from yolomux_lib.watchd_client import WatchClient


pytestmark = pytest.mark.usefixtures("no_control_socket", "isolated_yoagent_conversation_state", "isolated_tmux_socket")


# The exact schema this milestone freezes. Rendered payload, top level.
SNAPSHOT_PAYLOAD_KEYS = frozenset({
    "schema_version",
    "inventory",
    "services",
    "totals",
    "ledger",
    "recovery_events",
    # M8. Revision, age, epoch, and persistence describe the whole retained document, so
    # they are published once here instead of six times, one per row.
    "health",
})
# The typed snapshot's own fields, before any HTTP projection touches it.
SNAPSHOT_DATACLASS_FIELDS = (
    "schema_version",
    "observed_at",
    "inventory",
    "rows",
    "processes",
    "cpu_percent",
    "rss_bytes",
    "ledger",
    "recovery_events",
)
ROW_DATACLASS_FIELDS = (
    "service",
    "pid",
    "started_at",
    "uptime_seconds",
    "cpu_percent",
    "rss_bytes",
    "fields",
)


def _stub_producers(**overrides):
    """Six cheap row producers, so a collector test never touches a socket."""
    rows = {
        "indexd": {"service": "indexd", "pid": 0, "resources": {}},
        "statsd": {"service": "statsd", "pid": 0, "resources": {}},
        "jobd": {"service": "jobd", "pid": 0, "resources": {}},
        "statusd": {"service": "statusd", "pid": 0, "resources": {}},
        "watchd": {"service": "watchd", "pid": 0, "resources": {}},
        "approvald": {"service": "approvald", "pid": 0, "resources": {}},
    }
    rows.update(overrides)
    return {name: (lambda row=row: row) for name, row in rows.items()}


def _quiet_app(monkeypatch):
    """An app whose five non-watchd rows are stubs, so only the watchd path is live."""
    webapp = app_module.TmuxWebtermApp([])
    monkeypatch.setattr(webapp.search_indexer, "runtime_status", lambda: {"service": "indexd", "pid": 0, "resources": {}})
    monkeypatch.setattr(webapp, "statsd_runtime_status", lambda: {"service": "statsd", "pid": 0, "resources": {}})
    monkeypatch.setattr(webapp.job_client, "runtime_status", lambda: {"service": "jobd", "pid": 0, "resources": {}})
    monkeypatch.setattr(webapp.status_client, "runtime_status", lambda: {"service": "statusd", "pid": 0, "resources": {}})
    monkeypatch.setattr(webapp.approval_client, "runtime_status", lambda: {"service": "approvald", "pid": 0, "resources": {}})
    monkeypatch.setattr(webapp, "runtime_process_ledger", lambda: {})
    return webapp


def _watchd_client_with_record(tmp_path: Path, *, started_at: float, pid: int, protocol_version: int = 7) -> WatchClient:
    """A WatchClient whose registry has a valid, identity-fenced service record on disk.

    The record is written the way the registry writes it -- through
    `host_identity.process_record_fields` -- so it carries this host's stable id, boot id
    and the live process-start identity, and therefore passes the same central fence every
    other identity consumer uses. Nothing is started and no socket is created.
    """
    service_dir = tmp_path / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    client = WatchClient(socket_path=service_dir / "watchd.sock")
    registry = client.registry
    record = {
        **registry.host_identity.process_record_fields(pid=pid),
        "service": "watchd",
        "module": "yolomux_lib.watchd",
        "protocol_version": protocol_version,
        "socket": str(registry.socket_path),
        "started_at": started_at,
        "updated_at": time.time(),
    }
    registry.record_path.parent.mkdir(parents=True, exist_ok=True)
    registry.record_path.write_text(json.dumps(record), encoding="utf-8")
    return client


def _forbid_rpc(monkeypatch, registry: LocalServiceRegistry) -> None:
    """Any RPC at all on this registry becomes an immediate, named failure."""

    def refuse(*args, **kwargs):
        raise AssertionError("watchd diagnostics path issued an RPC")

    monkeypatch.setattr(type(registry), "_request", refuse)
    monkeypatch.setattr(WatchClient, "runtime_status", refuse)


def _forbid_demand_starts(monkeypatch) -> None:
    """Every process-creating entrypoint in the tree becomes a named failure.

    `LocalServiceRegistry.ensure_started` is the one primitive (frozen by
    tests/test_backend_health_catalog.py), and `_spawn` is the only thing under it that
    reaches the operating system. `SearchIndexerClient.ensure_started` and
    `StatsCurrentClient.ensure_started` are the two wrappers that do not inherit from
    `LocalServiceClient`, so they are named explicitly rather than assumed.
    """

    def refuse(self, *args, **kwargs):
        raise AssertionError("the local-services projection started a demand-scoped service")

    monkeypatch.setattr(LocalServiceRegistry, "ensure_started", refuse)
    monkeypatch.setattr(LocalServiceRegistry, "_spawn", refuse)
    monkeypatch.setattr(LocalServiceRegistry, "acquire_lease", refuse)
    monkeypatch.setattr(SearchIndexerClient, "ensure_started", refuse)
    monkeypatch.setattr(StatsCurrentClient, "ensure_started", refuse)


# --------------------------------------------------------------------------------------
# M2 -- the watchd decision
# --------------------------------------------------------------------------------------


def test_watchd_identity_and_uptime_come_from_the_service_record_with_no_rpc(monkeypatch, tmp_path):
    """The M2 fix: registry-derived identity, real uptime, real resources, zero traffic."""
    started_at = time.time() - 900.0
    client = _watchd_client_with_record(tmp_path, started_at=started_at, pid=os.getpid(), protocol_version=7)
    webapp = _quiet_app(monkeypatch)
    try:
        monkeypatch.setattr(webapp, "watch_client", client)
        _forbid_rpc(monkeypatch, client.registry)
        with webapp.client_watch_service.lock:
            webapp.client_watch_service.event_watcher_record.watchd_state = "polling"
            webapp.client_watch_service.event_watcher_record.watchd_pid = os.getpid()
        row = webapp.watchd_runtime_status()
        projected = next(
            service for service in webapp.runtime_local_services()["services"] if service["service"] == "watchd"
        )
    finally:
        webapp.control_server.stop()

    assert row["pid"] == os.getpid()
    # started_at was hardcoded 0.0 before M2, which made uptime permanently None.
    assert row["started_at"] == pytest.approx(started_at)
    assert row["version"] == 7
    assert row["identity"] == {
        "pid": os.getpid(),
        "started_at": pytest.approx(started_at),
        "process_start_identity": client.registry.host_identity.process_start_identity,
        "verified": True,
        "reason_code": "",
        "source": "service_record",
        "bridge_pid": os.getpid(),
        "bridge_pid_unverified": False,
    }
    # resources was hardcoded {} before M2, so watchd was the one service with no CPU/memory.
    assert row["resources"]["rss_bytes"] > 0
    assert projected["uptime_seconds"] is not None
    assert 899.0 <= projected["uptime_seconds"] <= 960.0
    assert projected["metrics"]["uptime_seconds"]["state"] == "measured"
    assert projected["metrics"]["rss_bytes"]["state"] == "measured"
    assert projected["state"] == "running"


def test_watchd_without_a_service_record_stays_idle_and_names_why(monkeypatch, tmp_path):
    """Absence of a demand-scoped service is idle, not down -- and it says which absence."""
    service_dir = tmp_path / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    client = WatchClient(socket_path=service_dir / "watchd.sock")
    webapp = _quiet_app(monkeypatch)
    try:
        monkeypatch.setattr(webapp, "watch_client", client)
        _forbid_rpc(monkeypatch, client.registry)
        row = webapp.watchd_runtime_status()
        projected = next(
            service for service in webapp.runtime_local_services()["services"] if service["service"] == "watchd"
        )
    finally:
        webapp.control_server.stop()

    assert row["pid"] == 0
    assert row["started_at"] == 0.0
    assert row["identity"]["verified"] is False
    assert row["identity"]["reason_code"] == local_service_projection.IDENTITY_NO_RECORD
    assert row["resources"] == {"cpu_percent": None, "rss_bytes": None}
    assert projected["state"] == "idle"
    assert projected["alerting"] is False
    assert projected["uptime_seconds"] is None


def test_watchd_publishes_a_bridge_pid_the_record_cannot_verify(monkeypatch, tmp_path):
    """A lease PID the durable record does not confirm is reported, not silently preferred."""
    service_dir = tmp_path / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    client = WatchClient(socket_path=service_dir / "watchd.sock")
    registry = client.registry
    registry.record_path.write_text(
        json.dumps({
            **registry.host_identity.process_record_fields(pid=2),
            "service": "watchd",
            "socket": str(registry.socket_path),
            "started_at": time.time(),
        }),
        encoding="utf-8",
    )
    webapp = _quiet_app(monkeypatch)
    try:
        monkeypatch.setattr(webapp, "watch_client", client)
        _forbid_rpc(monkeypatch, registry)
        with webapp.client_watch_service.lock:
            webapp.client_watch_service.event_watcher_record.watchd_pid = 987654
        row = webapp.watchd_runtime_status()
    finally:
        webapp.control_server.stop()

    assert row["pid"] == 0
    assert row["identity"]["verified"] is False
    assert row["identity"]["reason_code"] == local_service_projection.IDENTITY_NOT_CURRENT
    assert row["identity"]["bridge_pid"] == 987654
    assert row["identity"]["bridge_pid_unverified"] is True


def test_watchd_client_runtime_status_still_has_zero_production_callers():
    """M2 must not be "fixed" by calling watchd's status RPC from the projection."""
    repo_root = Path(__file__).resolve().parent.parent
    callers = [
        str(path.relative_to(repo_root))
        for path in sorted((repo_root / "yolomux_lib").rglob("*.py"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if "watch_client.runtime_status" in line and not line.strip().startswith("#")
    ]

    assert callers == [], callers
    assert "without making a status route call watchd" in app_module.WatchBridge.watchd_runtime_status.__doc__


def test_watchd_now_has_a_display_label_and_is_essential():
    """The two remaining M2 consequences: no raw-id label, and no second absence rule."""
    projected = app_module.TmuxWebtermApp.system_status_service(
        app_module.TmuxWebtermApp,
        {"service": "watchd", "pid": 0, "demand_started": True, "resources": {}},
    )

    assert projected["label"] == "File watching"
    assert projected["label"] != projected["id"]
    assert "watchd" in app_module.ESSENTIAL_LOCAL_SERVICES
    assert app_module.ESSENTIAL_LOCAL_SERVICES == frozenset(local_service_projection.LOCAL_SERVICE_INVENTORY)
    # Being essential must not make a legitimately-absent demand-scoped service alarm:
    # `demand_started` is the one owner of that rule.
    assert projected["essential"] is True
    assert projected["state"] == "idle"
    assert projected["alerting"] is False


def test_a_full_projection_starts_zero_demand_scoped_services(monkeypatch, tmp_path):
    """Mandatory M2/M4 invariant: observing the six services creates none of them."""
    client = _watchd_client_with_record(tmp_path, started_at=time.time() - 10.0, pid=os.getpid())
    webapp = app_module.TmuxWebtermApp([])
    try:
        monkeypatch.setattr(webapp, "watch_client", client)
        _forbid_demand_starts(monkeypatch)
        payload = webapp.runtime_local_services()
        # The periodic sampler reads the same collector, so it inherits the same guarantee.
        snapshot = webapp.local_services_snapshot()
    finally:
        webapp.control_server.stop()

    assert [service["service"] for service in payload["services"]] == list(
        local_service_projection.LOCAL_SERVICE_INVENTORY
    )
    assert tuple(row.service for row in snapshot.rows) == local_service_projection.LOCAL_SERVICE_INVENTORY


def test_a_projection_that_starts_a_service_is_rejected(monkeypatch, tmp_path):
    """Permanent negative control for the no-demand-start rule.

    A row producer that calls the one process-creating primitive must make the guard above
    fail, not pass quietly. Without this, `_forbid_demand_starts` could be silently
    unreachable -- for instance if the projection stopped touching registries at all -- and
    the invariant would read green while proving nothing.
    """
    webapp = app_module.TmuxWebtermApp([])
    try:
        _forbid_demand_starts(monkeypatch)
        monkeypatch.setattr(
            webapp,
            "watchd_runtime_status",
            lambda: webapp.watch_client.registry.ensure_started() and {},
        )
        with pytest.raises(AssertionError, match="started a demand-scoped service"):
            webapp.runtime_local_services()
    finally:
        webapp.control_server.stop()


def test_registry_process_identity_refuses_an_unreadable_or_foreign_record(tmp_path):
    """Every identity-read failure is a distinct bounded reason code, never one string."""
    service_dir = tmp_path / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    client = WatchClient(socket_path=service_dir / "watchd.sock")
    registry = client.registry

    absent = local_service_projection.registry_process_identity(registry)
    registry.record_path.write_text("{not json", encoding="utf-8")
    unreadable = local_service_projection.registry_process_identity(registry)
    registry.record_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    wrong_shape = local_service_projection.registry_process_identity(registry)
    registry.record_path.write_text(
        json.dumps({
            "stable_host_id": "some-other-host",
            "boot_id": "some-other-boot",
            "pid": os.getpid(),
            "process_start_identity": "proc:1",
            "started_at": 5.0,
        }),
        encoding="utf-8",
    )
    foreign = local_service_projection.registry_process_identity(registry)

    assert absent.reason_code == local_service_projection.IDENTITY_NO_RECORD
    # A corrupt file is not JSON, so read_json_file returns the default: same as absent.
    assert unreadable.reason_code == local_service_projection.IDENTITY_NO_RECORD
    assert wrong_shape.reason_code == local_service_projection.IDENTITY_RECORD_UNREADABLE
    assert foreign.reason_code == local_service_projection.IDENTITY_NOT_CURRENT
    # An unverified identity must never hand a PID back: a caller would sample or signal it.
    assert [identity.pid for identity in (absent, unreadable, wrong_shape, foreign)] == [0, 0, 0, 0]
    assert [identity.verified for identity in (absent, unreadable, wrong_shape, foreign)] == [False] * 4


# --------------------------------------------------------------------------------------
# M3 -- the extracted collector
# --------------------------------------------------------------------------------------


def test_the_snapshot_schema_is_frozen_and_immutable():
    """The snapshot dataclass IS the schema: exact fields, and no consumer may mutate it."""
    collector = local_service_projection.LocalServicesCollector(
        lambda: _stub_producers(indexd={"service": "indexd", "pid": 11, "started_at": 100.0, "resources": {"cpu_percent": 2.5, "rss_bytes": 4096}}),
        clock=lambda: 160.0,
    )
    snapshot = collector.collect()

    assert tuple(snapshot.__dataclass_fields__) == SNAPSHOT_DATACLASS_FIELDS
    assert tuple(snapshot.rows[0].__dataclass_fields__) == ROW_DATACLASS_FIELDS
    assert snapshot.schema_version == 3
    assert snapshot.observed_at == 160.0
    assert snapshot.inventory == local_service_projection.LOCAL_SERVICE_INVENTORY
    assert snapshot.row("indexd").uptime_seconds == 60.0
    assert snapshot.totals == {"processes": 1, "cpu_percent": 2.5, "rss_bytes": 4096}
    with pytest.raises(Exception):
        snapshot.schema_version = 99
    with pytest.raises(TypeError):
        snapshot.row("indexd").fields["pid"] = 99


def test_the_rendered_payload_publishes_schema_three_and_the_frozen_inventory():
    """W13 removed the dead `alert` key, so the version moved with it. Both are pinned together.

    This is the negative control for "a schema change without a version bump": the key set
    and the version number are asserted in ONE statement, so removing (or adding) a field
    without moving the number fails here rather than in a browser that silently rendered the
    old shape. The key set no longer carries `alert`.
    """
    collector = local_service_projection.LocalServicesCollector(lambda: _stub_producers())
    payload = collector.collect().payload(lambda row: dict(row))

    assert (payload["schema_version"], frozenset(payload)) == (3, SNAPSHOT_PAYLOAD_KEYS)
    assert "alert" not in payload
    assert payload["inventory"] == ("indexd", "statsd", "jobd", "statusd", "watchd", "approvald")
    assert [service["service"] for service in payload["services"]] == list(payload["inventory"])


def test_a_service_dropped_from_the_snapshot_is_rejected():
    """Permanent negative control: an extraction that loses a service must not render."""
    producers = _stub_producers()
    producers.pop("watchd")
    collector = local_service_projection.LocalServicesCollector(lambda: producers)

    with pytest.raises(ValueError, match=r"missing=\['watchd'\]"):
        collector.collect()


def test_a_service_smuggled_into_the_snapshot_is_rejected():
    """The same guard in the other direction: the inventory is exactly six, not at least six."""
    producers = _stub_producers()
    producers["storaged"] = lambda: {"service": "storaged", "pid": 0}
    collector = local_service_projection.LocalServicesCollector(lambda: producers)

    with pytest.raises(ValueError, match=r"unexpected=\['storaged'\]"):
        collector.collect()


def test_a_row_producer_wired_to_the_wrong_service_is_rejected():
    """A producer returning another service's row would silently relabel a whole column."""
    collector = local_service_projection.LocalServicesCollector(
        lambda: _stub_producers(watchd={"service": "statusd", "pid": 0, "resources": {}})
    )

    with pytest.raises(ValueError, match="returned a row for 'statusd'"):
        collector.collect()


def test_no_producer_field_is_lost_in_the_extraction():
    """Permanent negative control for silent field loss.

    Every key a row producer emits survives into the rendered row, plus exactly one derived
    key. A field quietly dropped during the M3 move -- the classic extraction defect -- is
    caught here rather than in the browser, where the cell just renders an em dash.
    """
    produced = {
        "service": "jobd",
        "pid": 4242,
        "started_at": 500.0,
        "version": 3,
        "healthy": True,
        "clients": 2,
        "queues": {"depth": 1},
        "cache": {"records": 3},
        "product_counters": {"transcript_view": {"completed": 4}},
        "source_change_counters": {"initial": 1},
        "last_success": 1784386100.0,
        "last_failure": "",
        "resources": {"cpu_percent": 1.5, "rss_bytes": 2048},
    }
    collector = local_service_projection.LocalServicesCollector(
        lambda: _stub_producers(jobd=produced), clock=lambda: 560.0
    )
    payload = collector.collect().payload(lambda row: dict(row))
    rendered = next(service for service in payload["services"] if service["service"] == "jobd")

    assert frozenset(rendered) == frozenset(produced) | {"uptime_seconds"}
    for key, value in produced.items():
        assert rendered[key] == value, key
    assert rendered["uptime_seconds"] == 60.0


def test_statsd_is_a_named_row_producer_and_no_longer_an_inline_literal():
    """M3 closes the one row shape that lived in two places.

    Before this milestone `runtime_local_services()` built statsd's row as a dict literal
    in its own body while the other five came back whole from a client, so a change to
    statsd's shape had to be made twice. The projection now contains no dict literal at
    all, and every service -- statsd included -- is one attribute reference to a whole-row
    owner rather than an expression that assembles a row at the call site.
    """
    projection = _definition_source("yolomux_lib/app.py", ("SystemStatusProjector", "runtime_local_services"))
    inline_literals = [
        node for node in ast.walk(ast.parse(textwrap.dedent(projection))) if isinstance(node, ast.Dict)
    ]

    assert inline_literals == [], projection
    assert "statsd" not in projection
    producers = _producer_expressions()
    assert tuple(producers) == local_service_projection.LOCAL_SERVICE_INVENTORY
    assert producers["statsd"] == "app.statsd_runtime_status"
    for service, expression in producers.items():
        assert expression.startswith("app."), (service, expression)
        assert "(" not in expression, (service, expression)
    # The whole row really does come back from that owner, not from the projection.
    statsd_source = _definition_source("yolomux_lib/app.py", ("SystemStatusProjector", "statsd_runtime_status"))
    statsd_calls = [
        node for node in ast.walk(ast.parse(textwrap.dedent(statsd_source)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "local_service_runtime_row"
    ]
    assert len(statsd_calls) == 1
    assert ast.literal_eval(statsd_calls[0].args[0]) == "statsd"


def _producer_expressions() -> dict[str, str]:
    """The literal `{service: producer}` mapping local_services_row_producers returns."""
    source = textwrap.dedent(
        _definition_source("yolomux_lib/app.py", ("SystemStatusProjector", "local_services_row_producers"))
    )
    mapping = next(
        node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Dict)
    )
    return {
        ast.literal_eval(key): ast.unparse(value)
        for key, value in zip(mapping.keys, mapping.values)
    }


def test_the_collector_is_the_only_path_into_the_projection():
    """The stats `service_load` sampler and `/api/system-status` share one owner.

    A second sampling path over the same projection is the divergent-copy defect this
    codebase fails on most, and the DOIT names it explicitly. Both callers must reach
    `local_services_snapshot()`, and neither may build rows of its own.
    """
    snapshot_source = _definition_source("yolomux_lib/app.py", ("SystemStatusProjector", "local_services_snapshot"))
    projection_source = _definition_source("yolomux_lib/app.py", ("SystemStatusProjector", "runtime_local_services"))
    sampler_source = _definition_source("yolomux_lib/app.py", ("TmuxWebtermApp", "collect_current_stats_service_load"))
    app_text = (Path(__file__).resolve().parent.parent / "yolomux_lib" / "app.py").read_text(encoding="utf-8")

    assert "LocalServicesCollector(" in snapshot_source
    # Exactly one construction site for the collector in the whole app.
    assert app_text.count("local_service_projection.LocalServicesCollector(") == 1
    assert "app.local_services_snapshot()" in projection_source
    assert "self.local_services_snapshot()" in sampler_source
    # The sampler must no longer re-parse the rendered HTTP payload. Read the executable
    # source, not the text: the comment recording why it stopped doing that names the old
    # call, and a test that could not tell those apart would forbid explaining itself.
    assert "runtime_local_services" not in ast.unparse(ast.parse(textwrap.dedent(sampler_source)))
    # And there is exactly one production call site for the snapshot beyond those two.
    callers = frozenset(
        line.strip()
        for line in app_text.splitlines()
        if "local_services_snapshot()" in line and not line.strip().startswith("#") and "def " not in line
    )
    assert len(callers) == 2, sorted(callers)


def test_the_sampler_reads_the_typed_rows_not_a_reparsed_payload(monkeypatch, tmp_path):
    """The continuous-1-Hz `service_load` collector emits one sample per service.

    This is the sampler the DOIT names as the existing periodic observer. It now samples the
    collector's typed rows -- including a watchd row that is running and has real memory,
    which it could never report before M2 because `resources` was hardcoded empty.
    """
    client = _watchd_client_with_record(tmp_path, started_at=time.time() - 30.0, pid=os.getpid())
    captured: list = []
    webapp = _quiet_app(monkeypatch)
    try:
        monkeypatch.setattr(webapp, "watch_client", client)
        monkeypatch.setattr(
            webapp.job_client,
            "runtime_status",
            lambda: {"service": "jobd", "pid": 4242, "resources": {"cpu_percent": 3.5, "rss_bytes": 8192}},
        )
        monkeypatch.setattr(
            app_module.stats_current_collectors,
            "service_load_success",
            lambda services, **kwargs: captured.extend(services),
        )
        _forbid_demand_starts(monkeypatch)
        webapp.collect_current_stats_service_load(_SamplerAttempt())
    finally:
        webapp.control_server.stop()

    samples = {sample.source_id: sample for sample in captured}
    assert tuple(samples) == local_service_projection.LOCAL_SERVICE_INVENTORY
    assert samples["jobd"].running is True
    assert samples["jobd"].cpu_percent == 3.5
    assert samples["jobd"].rss_bytes == 8192.0
    assert samples["watchd"].running is True
    assert samples["watchd"].rss_bytes > 0
    assert samples["indexd"].running is False
    assert samples["indexd"].rss_bytes is None


class _SamplerAttempt:
    """The bounded attempt shape `collect_current_stats_service_load` reads."""

    epoch_id = "epoch-1"
    epoch_started_at = 1.0
    scheduled_at = 2.0
    cadence_seconds = 1.0
    owner_generation = 1


def _definition_source(relative_path: str, qualname: tuple[str, ...]) -> str:
    """Return the on-disk source of a nested definition, read from the file."""
    path = Path(__file__).resolve().parent.parent / relative_path
    text = path.read_text(encoding="utf-8")
    node: ast.AST = ast.parse(text)
    for part in qualname:
        matches = [
            child
            for child in getattr(node, "body", [])
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == part
        ]
        assert len(matches) == 1, f"{relative_path}:{'.'.join(qualname)} -> {part}: found {len(matches)}"
        node = matches[0]
    segment = ast.get_source_segment(text, node)
    assert segment
    return segment


# --------------------------------------------------------------------------------------
# M8 -- publishing the retained health in the System row
# --------------------------------------------------------------------------------------


@pytest.fixture
def quiet_traffic_ledger():
    """The RPC ledger is process-wide, so a test that reads it must own its own state."""
    reset_local_service_traffic()
    yield
    reset_local_service_traffic()


def _retained_store(tmp_path: Path, port: int = 7799) -> BackendHealthStore:
    return BackendHealthStore(port, state_dir=tmp_path)


def _observation(resource: str, state: str, *, pid: int, identity: str, reason: str = "none") -> ResourceObservation:
    """One observation shaped exactly the way `BackendHealthObserver` shapes it today.

    `counters_available` is left at its default False deliberately: that is what the observer
    publishes, and every retained-counter assertion below depends on reproducing it rather
    than on a friendlier fixture. A fixture that fed counters here would prove nothing about
    the payload the live server actually renders.
    """
    return ResourceObservation(
        resource=resource,
        state=state,
        reason_code=reason,
        pid=pid,
        process_start_identity=identity,
    )


def _work(service: str, *, completions: tuple[float, ...] = (), failures: tuple[str, ...] = (), epoch: str = "") -> None:
    ledger = rpc.local_service_traffic_ledger(service)
    if epoch:
        ledger.note_epoch(epoch)
    for elapsed in completions:
        ledger.record_completion(rpc.LOCAL_SERVICE_TRAFFIC_WORK, client_elapsed_ms=elapsed)
    for reason in failures:
        ledger.record_failure(rpc.LOCAL_SERVICE_TRAFFIC_WORK, reason)


def _health(store: BackendHealthStore, *, now: float, web_started_at: float = 0.0) -> local_service_projection.RetainedHealth:
    return local_service_projection.RetainedHealth(
        document=store.status(),
        traffic=rpc.local_service_traffic_snapshot(),
        now=now,
        web_process_started_at=web_started_at,
    )


def test_a_row_publishes_the_typed_state_its_age_and_its_bounded_history(tmp_path, quiet_traffic_ledger):
    """The numbers Keiven asked for, on one row: state, when it started, and the transitions."""
    store = _retained_store(tmp_path)
    store.record(HealthSnapshot(observed_at=100.0, resources=(_observation("jobd", "starting", pid=42, identity="proc:98"),)))
    store.record(HealthSnapshot(observed_at=102.0, resources=(_observation("jobd", "ready", pid=42, identity="proc:98"),)))

    row = _health(store, now=112.0).service("jobd")

    assert row["observed"] is True and row["unavailable_reason_code"] == ""
    assert (row["state"], row["reason_code"], row["recovery_outcome"]) == ("ready", "none", "none")
    assert row["process_epoch"] == "pid:42:start:98" and row["pid"] == 42
    assert (row["since_revision"], row["since_wall_time"]) == (2, 102.0)
    assert row["state_age_seconds"] == 10.0
    assert [(entry["previous_state"], entry["new_state"]) for entry in row["transitions"]] == [
        ("", "starting"),
        ("starting", "ready"),
    ]
    assert (row["transitions_total"], row["transitions_truncated"]) == (2, False)
    # A transition row still carries exactly the seven redaction-safe fields the store bounds
    # it to; the projection republishes them, it does not enrich them with anything free-text.
    assert frozenset(row["transitions"][0]) == frozenset(store_module.TRANSITION_ROW_FIELDS)


def test_the_snapshot_level_health_block_carries_revision_age_and_persistence(tmp_path, quiet_traffic_ledger):
    """Revision and age are published once for the document, never copied into six rows."""
    store = _retained_store(tmp_path, port=7801)
    store.record(HealthSnapshot(observed_at=100.0, resources=(_observation("jobd", "ready", pid=42, identity="proc:98"),)))
    document = store.status()

    health = local_service_projection.RetainedHealth(document=document, now=float(document["written_at"]) + 4.0)
    payload = health.payload()

    assert payload["available"] is True and payload["reason_code"] == ""
    assert (payload["port"], payload["revision"], payload["resources"]) == (7801, 1, 1)
    assert payload["schema_version"] == store_module.BACKEND_HEALTH_SCHEMA_VERSION
    assert payload["age_seconds"] == 4.0
    assert (payload["history_coverage"], payload["history_reset_reason"]) == ("full", "")
    assert (payload["persistence_state"], payload["persistence_reason_code"]) == ("ok", "")
    assert payload["observer_epoch"] == document["observer_epoch"]
    # No row repeats any of it: one revision, one age, one epoch, one place.
    row = health.service("jobd")
    assert frozenset(row) & frozenset(payload) == frozenset({"reason_code"}), sorted(frozenset(row) & frozenset(payload))


def test_counters_total_exactly_across_a_peer_restart(tmp_path, quiet_traffic_ledger):
    """THE reconciliation proof. Counters are published under the denominator that makes
    them exact, and a peer restart adds work rather than double counting or resetting it.

    The retained store re-baselines per verified process epoch and the observer feeds it
    `counters_available=False`, so its per-epoch aggregate is structurally empty. The web
    process's own RPC ledger is cumulative and continuous across a peer restart, which is
    exactly what makes it addable. The row therefore takes requests/errors/latency from the
    ledger with `counter_scope: "web_process"`, and restarts from the store, and neither
    number is republished from the other source.
    """
    store = _retained_store(tmp_path)
    store.record(HealthSnapshot(observed_at=100.0, resources=(_observation("jobd", "ready", pid=42, identity="proc:98"),)))
    _work("jobd", epoch="pid:42", completions=(4.0, 10.0), failures=("peer_absent",))

    before = _health(store, now=110.0).service("jobd")
    assert before["metrics"]["request_count"]["value"] == 3
    assert before["metrics"]["completed_count"]["value"] == 2
    assert before["metrics"]["error_count"]["value"] == 1
    assert before["metrics"]["latency_average_ms"]["value"] == 7.0
    assert before["metrics"]["latency_max_ms"]["value"] == 10.0
    assert before["metrics"]["restart_count"]["value"] == 0

    # The peer restarts: a new verified epoch in the store, and new work through the ledger.
    store.record(HealthSnapshot(observed_at=104.0, resources=(_observation("jobd", "ready", pid=77, identity="proc:120"),)))
    _work("jobd", epoch="pid:77", completions=(1.0,))

    after = _health(store, now=114.0).service("jobd")
    # Exact totals: 3 + 1 attempts, 2 + 1 completions, one error, mean (4+10+1)/3, max 10.
    assert after["metrics"]["request_count"]["value"] == 4
    assert after["metrics"]["completed_count"]["value"] == 3
    assert after["metrics"]["error_count"]["value"] == 1
    assert after["metrics"]["latency_average_ms"]["value"] == 5.0
    assert after["metrics"]["latency_max_ms"]["value"] == 10.0
    # The restart itself is counted once, by the one owner that verifies process identity.
    assert after["metrics"]["restart_count"]["value"] == 1
    assert after["coverage"]["counter_scope"] == "web_process"
    assert after["errors_by_reason"] == {"peer_absent": 1}


def test_a_retained_aggregate_with_no_counter_sample_never_renders_as_complete(tmp_path, quiet_traffic_ledger):
    """PERMANENT NEGATIVE CONTROL: a partial aggregate must not be shown as complete.

    The store's own `aggregate.coverage` reads `full` in this state -- `_accumulate` degrades
    coverage only when an epoch change loses a final sample, never for an observer that reads
    no counters at all -- while every retained count is a structural zero. Republishing that
    field verbatim would tell a reader that zero requests is a measured fact.
    """
    store = _retained_store(tmp_path)
    store.record(HealthSnapshot(observed_at=100.0, resources=(_observation("jobd", "ready", pid=42, identity="proc:98"),)))

    aggregate = store.document()["resources"]["jobd"]["aggregate"]
    assert aggregate["coverage"] == "full", aggregate
    assert aggregate["request_count"] == 0 and aggregate["last_sample"]["counters_available"] is False

    coverage = _health(store, now=110.0).service("jobd")["coverage"]
    assert coverage["retained_counters"] == "partial", coverage
    assert "counters_not_observed" in coverage["retained_counter_reasons"], coverage


def test_a_retained_counter_sample_keeps_its_own_coverage_verdict(tmp_path, quiet_traffic_ledger):
    """The downgrade above is a downgrade, not a hardcoded 'partial'.

    Without this, `retained_counters` would be a constant, and the negative control would
    pass for the wrong reason -- the field that cannot vary.
    """
    store = _retained_store(tmp_path)
    for observed_at, requests in ((100.0, 0), (102.0, 5)):
        store.record(HealthSnapshot(observed_at=observed_at, resources=(
            ResourceObservation(
                resource="jobd", state="ready", reason_code="none", pid=42,
                process_start_identity="proc:98", counters_available=True,
                request_count=requests, error_count=0, completed_count=requests,
                latency_total_ms=float(requests), latency_max_ms=1.0,
            ),
        )))

    row = _health(store, now=110.0).service("jobd")
    assert row["coverage"]["retained_counters"] == "full", row["coverage"]
    assert row["coverage"]["retained_counter_reasons"] == [], row["coverage"]


def test_an_untimed_service_publishes_no_average_response_time(tmp_path, quiet_traffic_ledger):
    """The ledger publishes `avg_ms: 0.0` with no completion. Zero is not a response time."""
    store = _retained_store(tmp_path)
    store.record(HealthSnapshot(observed_at=100.0, resources=(_observation("jobd", "ready", pid=42, identity="proc:98"),)))
    _work("jobd", failures=("peer_absent",))

    assert rpc.local_service_traffic_snapshot()["jobd"]["work"]["client_latency_ms"]["avg_ms"] == 0.0
    metrics = _health(store, now=110.0).service("jobd")["metrics"]
    for name in ("latency_average_ms", "latency_max_ms"):
        assert metrics[name] == {
            "state": "unavailable",
            "value": None,
            "reason_code": "no_completed_request",
            "reason": "No completed request has been timed in this web process",
        }, (name, metrics[name])
    # The counts around them are still exact: one attempt, one error, no completion.
    assert metrics["request_count"]["value"] == 1 and metrics["error_count"]["value"] == 1
    assert metrics["completed_count"]["value"] == 0


def test_counters_are_partial_when_the_retained_history_predates_this_web_process(tmp_path, quiet_traffic_ledger):
    """History survives a web restart; the ledger does not. The row says which window it covers."""
    store = _retained_store(tmp_path)
    store.record(HealthSnapshot(observed_at=100.0, resources=(_observation("jobd", "ready", pid=42, identity="proc:98"),)))
    epoch_started_at = float(store.document()["observer_epoch_started_at"])

    same_process = _health(store, now=110.0, web_started_at=epoch_started_at).service("jobd")["coverage"]
    assert same_process["counters"] == "full" and same_process["counter_reasons"] == []

    restarted_web = _health(store, now=110.0, web_started_at=epoch_started_at + 60.0).service("jobd")["coverage"]
    assert restarted_web["counters"] == "partial", restarted_web
    assert restarted_web["counter_reasons"] == ["web_process_scope"], restarted_web


def test_probe_traffic_never_enters_the_published_request_count(tmp_path, quiet_traffic_ledger):
    """The observer probes every service every two seconds. Those are not product requests."""
    store = _retained_store(tmp_path)
    store.record(HealthSnapshot(observed_at=100.0, resources=(_observation("jobd", "ready", pid=42, identity="proc:98"),)))
    ledger = rpc.local_service_traffic_ledger("jobd")
    for _ in range(50):
        ledger.record_completion(rpc.LOCAL_SERVICE_TRAFFIC_PROBE, client_elapsed_ms=900.0)
    ledger.record_failure(rpc.LOCAL_SERVICE_TRAFFIC_PROBE, "peer_absent")
    _work("jobd", completions=(4.0,))

    metrics = _health(store, now=110.0).service("jobd")["metrics"]
    assert metrics["request_count"]["value"] == 1, metrics
    assert metrics["error_count"]["value"] == 0, metrics
    assert metrics["latency_max_ms"]["value"] == 4.0, metrics


def test_transitions_are_bounded_and_say_when_older_rows_exist(tmp_path, quiet_traffic_ledger):
    """The store keeps 128 per resource; the HTTP body publishes the newest 16 and says so."""
    store = _retained_store(tmp_path)
    states = ("ready", "degraded")
    for index in range(40):
        store.record(HealthSnapshot(observed_at=100.0 + index, resources=(
            _observation("jobd", states[index % 2], pid=42, identity="proc:98"),
        )))

    row = _health(store, now=200.0).service("jobd")
    assert len(row["transitions"]) == local_service_projection.SYSTEM_STATUS_MAX_TRANSITIONS
    assert row["transitions_total"] == 40 and row["transitions_truncated"] is True
    # The NEWEST rows, not the oldest: a reader acting on this list acts on the recent past.
    assert row["transitions"][-1]["wall_time"] == 139.0
    assert row["transitions"][0]["wall_time"] == 124.0


@pytest.mark.parametrize("asserted", [True, False])
def test_the_projection_republishes_the_stores_exactness_answer_rather_than_deciding_again(
    tmp_path, quiet_traffic_ledger, asserted
):
    """FOURTH COPY GUARD, part one: this layer forwards the owner's answer, both ways.

    `backend_health.store._transition_totals` is the one owner of the presence-versus-validity
    rules for the two optional counter fields. This module used to re-answer the flag locally
    (`bool(x) if isinstance(x, bool) else False`), which is the store's answer for a CORRUPT flag
    and the OPPOSITE of its answer for an ABSENT one -- a copy that disagreed with its owner by
    construction.

    Measured, so the guard is not credited with more than it catches: this test does NOT go red
    against that original line, which forwards real booleans correctly and differs only on input
    the store cannot emit. It goes red against a copy that stops forwarding -- hardcoded `False`
    (1 failed), re-derived from the retained rows (10 failed), or published raw with no boolean
    guarantee (9 failed).
    """
    store = _retained_store(tmp_path)
    store.record(HealthSnapshot(observed_at=100.0, resources=(_observation("jobd", "ready", pid=42, identity="proc:98"),)))
    document = store.status()
    document["resources"]["jobd"]["transitions_total_exact"] = asserted

    row = local_service_projection.RetainedHealth(document=document, now=200.0).service("jobd")
    assert row["transitions_total_exact"] is asserted, row


@pytest.mark.parametrize(
    "flag",
    [True, False, "true", 1, 0, None, {}, [], "", "false"],
)
def test_the_projection_can_never_manufacture_an_exactness_claim_its_owner_did_not_make(
    quiet_traffic_ledger, flag
):
    """FOURTH COPY GUARD, part two: this layer may LOSE a claim, never INVENT one.

    The projection cannot import `_transition_totals` -- `backend_health/observer.py` imports THIS
    module, so the projection is the lower layer and the reverse import is a measured hard cycle.
    The owner's answer therefore arrives inside the document instead, which leaves one direction
    that has to be pinned by property rather than by parity: whatever a record contains, a
    published `True` must be a value the owner would also have resolved to `True`.

    That is the direction the whole subsystem keeps failing in -- `-1`, `True`, `'1'` and `'true'`
    were all laundered into claims of completeness by a branch that asked the wrong question.
    Measured: swapping this layer back to `bool(...)` turns `'true'`, `1` and `'false'` into
    exactness claims and fails this test 3 ways. The store's own truth table
    (`test_the_counter_owner_answers_presence_and_validity_separately`) pins the other side.

    The residual gap is stated rather than hidden: because the answer travels in the document and
    not through an import, this layer can still LOSE a claim on a raw record the store never
    normalized -- an absent flag beside a usable total reads as `False` here and `True` in the
    owner. Closing that needs the owner to move below both modules, or the observer's import of
    this module to go; neither is a counter fix.
    """
    record = {
        "current": {"state": "ready", "reason_code": "none", "recovery_outcome": "none",
                    "process_epoch": "pid:42:start:98", "pid": 42, "observed_at": 100.0,
                    "since_revision": 1, "since_wall_time": 100.0},
        "aggregate": {},
        "transitions": [],
        "transitions_total": 43,
        "transitions_total_exact": flag,
    }
    document = {"resources": {"jobd": record}, "revision": 1, "written_at": 100.0}

    row = local_service_projection.RetainedHealth(document=document, now=200.0).service("jobd")
    published = row["transitions_total_exact"]
    assert published is True or published is False, row
    owner_says = store_module._transition_totals(record, len(record["transitions"]))[1]
    assert not (published and not owner_says), (flag, published, owner_says)


def test_an_unobserved_service_claims_nothing_about_a_history_it_does_not_have(quiet_traffic_ledger):
    """The one counter question this layer owns: is there a record AT ALL?

    An unobserved service has no history, so its `0` is not an exact count of anything -- that is
    this module's own answer to its own question, not a copy of the store's rule, and it is why
    the published field stays a real boolean instead of becoming `None` for a row nobody observed.
    """
    row = local_service_projection.RetainedHealth().service("jobd")
    assert row["observed"] is False, row
    assert row["transitions_total"] == 0, row
    assert row["transitions_total_exact"] is False, row
    assert row["transitions_truncated"] is False, row


def test_an_unattached_observer_publishes_a_reason_and_never_zeros(quiet_traffic_ledger):
    """No observer is not the same fact as a healthy service with no restarts."""
    health = local_service_projection.RetainedHealth()
    payload = health.payload()
    row = health.service("jobd")

    assert payload["available"] is False and payload["reason_code"] == "observer_unattached"
    assert payload["age_seconds"] is None and payload["revision"] == 0
    assert row["observed"] is False and row["unavailable_reason_code"] == "observer_unattached"
    assert row["state"] == "" and row["state_age_seconds"] is None
    for name in ("restart_count", "observations"):
        assert row["metrics"][name]["value"] is None, (name, row["metrics"][name])
        assert row["metrics"][name]["reason_code"] == "observer_unattached", (name, row["metrics"][name])
    assert row["coverage"]["retained_counters"] == "unavailable"


def test_an_unobserved_resource_is_named_apart_from_an_unattached_observer(tmp_path, quiet_traffic_ledger):
    """Two different failures with two different fixes never collapse into one reason."""
    store = _retained_store(tmp_path)
    store.record(HealthSnapshot(observed_at=100.0, resources=(_observation("jobd", "ready", pid=42, identity="proc:98"),)))
    health = _health(store, now=110.0)

    assert health.payload()["available"] is True
    row = health.service("statusd")
    assert row["observed"] is False and row["unavailable_reason_code"] == "resource_unobserved"
    assert row["metrics"]["restart_count"]["reason_code"] == "resource_unobserved"
