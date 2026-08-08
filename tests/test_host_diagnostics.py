from pathlib import Path

from yolomux_lib.infra.filesystem_preflight import FilesystemClassification
from yolomux_lib.infra.host_identity import HostIdentity
from yolomux_lib.infra.host_identity import current_host_identity
from yolomux_lib.infra.host_diagnostics import DatabasePartition
from yolomux_lib.infra.host_diagnostics import RejectedMutablePath
from yolomux_lib.infra.host_diagnostics import RootLayout
from yolomux_lib.infra.host_diagnostics import collect_host_diagnostics
from yolomux_lib.stats_current.storage import DATABASE_FILENAME


def test_non_admin_host_diagnostics_redacts_paths_and_keeps_safety_state_visible(tmp_path):
    roots = RootLayout(
        config=tmp_path / "operator" / "config",
        state=tmp_path / "operator" / "state",
        cache=tmp_path / "operator" / "cache",
        runtime=None,
    )
    report = collect_host_diagnostics(
        roots=roots,
        classifier=lambda path: FilesystemClassification(Path(path), "nfs4", True, True),
        database_partitions=(DatabasePartition("chat", roots.state / "yochat.sqlite3"),),
        rejected_paths=(RejectedMutablePath(roots.state / "services" / "stats.sock", "Unix socket is on nfs4"),),
        environ={"YOLOMUX_ALLOW_NETWORK_FILESYSTEM_MUTABLE_ROOTS": "1"},
    )

    payload = report.payload(admin=False)

    assert payload["roots"] == [
        {"kind": "config", "filesystem_type": "nfs4", "network_filesystem": True, "determined": True},
        {"kind": "state", "filesystem_type": "nfs4", "network_filesystem": True, "determined": True},
        {"kind": "cache", "filesystem_type": "nfs4", "network_filesystem": True, "determined": True},
        {"kind": "runtime", "filesystem_type": "undetermined", "network_filesystem": None, "determined": False,
         "reason": "No host-local runtime root has been configured; legacy state root still owns runtime files"},
    ]
    assert payload["database_partitions"] == [{"name": "chat", "status": "not_created", "path_name": "yochat.sqlite3"}]
    assert payload["rejected_mutable_paths"] == [{"path_name": "stats.sock", "reason": "Unix socket is on nfs4"}]
    assert payload["network_filesystem_escape_hatch"] is True
    assert str(tmp_path) not in str(payload)


def test_admin_host_diagnostics_exposes_resolved_paths_and_explicit_identity_snapshot(tmp_path):
    roots = RootLayout(config=tmp_path / "config", state=tmp_path / "state", cache=tmp_path / "cache", runtime=tmp_path / "run")
    database = roots.state / "stats-v6.sqlite3"
    database.parent.mkdir()
    database.touch()
    report = collect_host_diagnostics(
        roots=roots,
        identity=HostIdentity(
            stable_host_id="machine-123",
            display_hostname="display-host",
            boot_id="boot-456",
            pid=123,
            process_start_identity="proc:100",
            process_start_ticks=100,
            instance_nonce="nonce-abc",
            stable_host_id_source="YOLOMUX_HOST_ID",
        ),
        classifier=lambda path: FilesystemClassification(Path(path), "ext4", False, True),
        database_partitions=(DatabasePartition("stats", database),),
        environ={},
    )

    payload = report.payload(admin=True)

    assert payload["identity"] == {
        "stable_host_id": {"value": "machine-123"},
        "display_hostname": {"value": "display-host"},
        "boot_id": {"value": "boot-456"},
        "process_start_identity": {"value": "proc:100"},
        "stable_host_id_source": {"value": "YOLOMUX_HOST_ID"},
    }
    assert payload["roots"][3]["path"] == str(roots.runtime.resolve())
    assert payload["database_partitions"] == [{"name": "stats", "status": "active", "path": str(database.resolve())}]
    assert payload["rejected_mutable_paths"] == []
    assert payload["network_filesystem_escape_hatch"] is False


def test_unknown_filesystem_is_reported_as_undetermined_not_local(tmp_path):
    roots = RootLayout(config=tmp_path / "config", state=tmp_path / "state", cache=tmp_path / "cache", runtime=tmp_path / "run")
    report = collect_host_diagnostics(
        roots=roots,
        classifier=lambda path: FilesystemClassification(Path(path), "unknown", False, False),
        database_partitions=(),
        environ={},
    )

    assert report.payload(admin=False)["roots"][0] == {
        "kind": "config", "filesystem_type": "unknown", "network_filesystem": False, "determined": False,
    }


def test_unreadable_database_partition_is_undetermined(tmp_path, monkeypatch):
    roots = RootLayout(config=tmp_path / "config", state=tmp_path / "state", cache=tmp_path / "cache", runtime=tmp_path / "run")
    database = roots.state / "stats-v6.sqlite3"
    database_path = str(database.resolve())
    original_stat = Path.stat

    def denied_stat(path, *args, **kwargs):
        if str(path) == database_path:
            raise OSError("permission denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied_stat)
    report = collect_host_diagnostics(
        roots=roots,
        classifier=lambda path: FilesystemClassification(Path(path), "ext4", False, True),
        database_partitions=(DatabasePartition("stats", database),),
        environ={},
    )

    assert report.payload(admin=False)["database_partitions"] == [{
        "name": "stats", "status": "undetermined", "path_name": "stats-v6.sqlite3", "reason": "OSError",
    }]


def test_collect_uses_current_host_identity_with_distinct_display_name_and_source(tmp_path, monkeypatch):
    roots = RootLayout(config=tmp_path / "config", state=tmp_path / "state", cache=tmp_path / "cache", runtime=tmp_path / "run")
    identity = HostIdentity(
        stable_host_id="machine-123",
        display_hostname="display-host",
        boot_id="boot-456",
        pid=123,
        process_start_identity="proc:789",
        process_start_ticks=789,
        instance_nonce="nonce-abc",
        stable_host_id_source="YOLOMUX_HOST_ID",
    )
    monkeypatch.setattr("yolomux_lib.infra.host_diagnostics.current_host_identity", lambda: identity)

    payload = collect_host_diagnostics(
        roots=roots,
        classifier=lambda path: FilesystemClassification(Path(path), "ext4", False, True),
        database_partitions=(),
        environ={},
    ).payload(admin=False)

    assert payload["identity"] == {
        "stable_host_id": {"value": "machine-123"},
        "display_hostname": {"value": "display-host"},
        "boot_id": {"value": "boot-456"},
        "process_start_identity": {"value": "proc:789"},
        "stable_host_id_source": {"value": "YOLOMUX_HOST_ID"},
    }


def test_cached_identity_reports_its_actual_source_after_a_late_override(tmp_path, monkeypatch):
    roots = RootLayout(config=tmp_path / "config", state=tmp_path / "state", cache=tmp_path / "cache", runtime=tmp_path / "run")
    monkeypatch.delenv("YOLOMUX_HOST_ID", raising=False)
    current_host_identity.cache_clear()
    resolved = current_host_identity()
    monkeypatch.setenv("YOLOMUX_HOST_ID", "diagnostics-override")

    payload = collect_host_diagnostics(
        roots=roots,
        classifier=lambda path: FilesystemClassification(Path(path), "ext4", False, True),
        database_partitions=(),
        environ={},
    ).payload(admin=False)

    assert payload["identity"]["stable_host_id"] == {"value": resolved.stable_host_id}
    assert payload["identity"]["stable_host_id_source"] == {"value": resolved.stable_host_id_source}
    assert payload["identity_reason_code"] == "late_host_id_override_rejected"
    current_host_identity.cache_clear()


def test_no_argument_diagnostics_reports_late_override_without_raising(monkeypatch):
    monkeypatch.delenv("YOLOMUX_HOST_ID", raising=False)
    current_host_identity.cache_clear()
    resolved = current_host_identity()
    monkeypatch.setenv("YOLOMUX_HOST_ID", "diagnostics-override")

    payload = collect_host_diagnostics().payload(admin=False)

    assert payload["identity"]["stable_host_id"] == {"value": resolved.stable_host_id}
    assert payload["identity_reason_code"] == "late_host_id_override_rejected"
    partitions = {item["name"]: item for item in payload["database_partitions"]}
    assert {"chat", "login-throttle", "stats-current", "model-pricing", "search-index"} <= partitions.keys()
    assert all(item["partition_key"] == resolved.stable_host_id for item in partitions.values())
    current_host_identity.cache_clear()


def test_current_database_partitions_reports_host_partitioned_stats_path(tmp_path):
    roots = RootLayout(config=tmp_path / "config", state=tmp_path / "state", cache=tmp_path / "cache", runtime=tmp_path / "run")
    stats_path = roots.state / "hosts" / "machine-123" / DATABASE_FILENAME
    stats_path.parent.mkdir(parents=True)
    stats_path.touch()
    identity = HostIdentity("machine-123", "display-host", "boot-456", 123, "proc:789", 789, "nonce-abc", "YOLOMUX_HOST_ID")

    payload = collect_host_diagnostics(
        roots=roots,
        identity=identity,
        classifier=lambda path: FilesystemClassification(Path(path), "ext4", False, True),
        environ={},
    ).payload(admin=True)

    assert {item["name"]: item for item in payload["database_partitions"]}["stats-current"] == {
        "name": "stats-current", "status": "active", "partition_key": "machine-123", "path": str(stats_path.resolve()),
    }


def test_current_database_partitions_reports_a_host_key_for_all_five_stores(tmp_path):
    roots = RootLayout(config=tmp_path / "config", state=tmp_path / "state", cache=tmp_path / "cache", runtime=tmp_path / "run")
    host_root = roots.state / "hosts" / "machine-123"
    cache_root = roots.cache / "hosts" / "machine-123"
    stats_path = host_root / DATABASE_FILENAME
    search_path = host_root / "search_index" / "index.sqlite3"
    for path in (stats_path, search_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    identity = HostIdentity("machine-123", "display-host", "boot-456", 123, "proc:789", 789, "nonce-abc", "YOLOMUX_HOST_ID")
    payload = collect_host_diagnostics(
        roots=roots,
        identity=identity,
        classifier=lambda path: FilesystemClassification(Path(path), "ext4", False, True),
        environ={},
    ).payload(admin=False)

    partitions = {item["name"]: item for item in payload["database_partitions"]}
    assert {"chat", "login-throttle", "stats-current", "model-pricing", "search-index", "search-index:index"} <= partitions.keys()
    assert all(partitions[name]["partition_key"] == "machine-123" for name in partitions)
