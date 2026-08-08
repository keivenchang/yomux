# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Contracts for one declared writer and host-local worktree artifacts."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import pytest

from tools import check
from tools import static_build
from yolomux_lib import cli
from yolomux_lib.infra.host_identity import HostIdentity
from yolomux_lib.infra import worktree_writer


FIXTURE_PID = 4242
FIXTURE_TICKS = 5252


def _identity(name: str, hostname: str | None = None, *, pid: int = FIXTURE_PID) -> HostIdentity:
    return HostIdentity(
        stable_host_id=f"fixture-{name}",
        display_hostname=hostname or f"{name}.example",
        boot_id=f"boot-{name}",
        pid=pid,
        process_start_identity=f"proc:{FIXTURE_TICKS}",
        process_start_ticks=FIXTURE_TICKS,
        instance_nonce=f"instance-{name}",
        stable_host_id_source="gate fixture",
    )


def _record(identity: HostIdentity, *, token: str, heartbeat_at: float, purpose: str = "fixture") -> dict[str, Any]:
    return {
        **identity.process_record_fields(),
        "schema": 1,
        "token": token,
        "purpose": purpose,
        "hostname": identity.display_hostname,
        "declared_at": heartbeat_at,
        "heartbeat_at": heartbeat_at,
    }


def _write_slot(slot: Path, record: dict[str, Any]) -> bytes:
    slot.mkdir(parents=True)
    raw = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    (slot / "owner.json").write_bytes(raw)
    return raw


def test_reader_detects_foreign_writer_and_warns_without_mutating_declaration(tmp_path: Path) -> None:
    local = _identity("host-b", "lin2")
    foreign = _identity("host-a", "lin1")
    slot = tmp_path / "shared-git" / "writer"
    original = _write_slot(slot, _record(foreign, token="foreign-token", heartbeat_at=95.0))

    status = worktree_writer.inspect_worktree_writer(
        tmp_path / "worktree",
        host_identity=local,
        slot_dir=slot,
        now=100.0,
    )
    warning = worktree_writer.server_start_writer_warning(
        tmp_path / "worktree",
        host_identity=local,
        slot_dir=slot,
        now=100.0,
    )

    assert status.active is True
    assert status.foreign_host is True
    assert status.stable_host_id == foreign.stable_host_id
    assert status.hostname == "lin1"
    assert "lin1" in warning
    assert foreign.stable_host_id in warning
    assert "read-only" in warning
    assert (slot / "owner.json").read_bytes() == original


def test_writer_refuses_fresh_foreign_declaration_with_typed_status(tmp_path: Path) -> None:
    local = _identity("host-b", "lin2")
    foreign = _identity("host-a", "lin1")
    slot = tmp_path / "shared-git" / "writer"
    original = _write_slot(slot, _record(foreign, token="foreign-token", heartbeat_at=99.0))

    with pytest.raises(worktree_writer.WorktreeWriterBusy) as caught:
        worktree_writer.acquire_worktree_writer(
            tmp_path / "worktree",
            purpose="static-build",
            host_identity=local,
            slot_dir=slot,
            clock=lambda: 100.0,
            heartbeat_interval_seconds=0.0,
        )

    assert caught.value.status.state == "foreign_active"
    assert caught.value.status.hostname == "lin1"
    assert (slot / "owner.json").read_bytes() == original


def test_stale_foreign_declaration_is_reclaimed_and_owned_release_removes_it(tmp_path: Path) -> None:
    local = _identity("host-b", "lin2")
    foreign = _identity("host-a", "lin1")
    slot = tmp_path / "shared-git" / "writer"
    _write_slot(slot, _record(foreign, token="stale-token", heartbeat_at=10.0))
    env: dict[str, str] = {}

    lease = worktree_writer.acquire_worktree_writer(
        tmp_path / "worktree",
        purpose="pytest",
        host_identity=local,
        slot_dir=slot,
        clock=lambda: 100.0,
        stale_after_seconds=30.0,
        heartbeat_interval_seconds=0.0,
        environ=env,
    )
    active = json.loads((slot / "owner.json").read_text(encoding="utf-8"))
    assert active["stable_host_id"] == local.stable_host_id
    assert active["token"] == lease.token == env[worktree_writer.WRITER_TOKEN_ENV]

    lease.release()

    assert not slot.exists()
    assert worktree_writer.WRITER_TOKEN_ENV not in env


@pytest.mark.parametrize("observed_start", [None, "proc:9999"])
def test_same_host_dead_or_recycled_process_is_reclaimed_without_waiting_for_ttl(
    tmp_path: Path,
    observed_start: str | None,
) -> None:
    identity = _identity("host-a", "lin1")
    slot = tmp_path / "shared-git" / "writer"
    _write_slot(slot, _record(identity, token="old-token", heartbeat_at=99.9))

    lease = worktree_writer.acquire_worktree_writer(
        tmp_path / "worktree",
        purpose="pytest",
        host_identity=identity,
        slot_dir=slot,
        clock=lambda: 100.0,
        heartbeat_interval_seconds=0.0,
        start_identity_reader=lambda _pid: observed_start,
        environ={},
    )
    try:
        assert lease.token != "old-token"
        active = json.loads((slot / "owner.json").read_text(encoding="utf-8"))
        assert active["stable_host_id"] == identity.stable_host_id
    finally:
        lease.release()


def test_release_never_removes_a_successor_token(tmp_path: Path) -> None:
    identity = _identity("host-a", "lin1")
    slot = tmp_path / "shared-git" / "writer"
    lease = worktree_writer.acquire_worktree_writer(
        tmp_path / "worktree",
        purpose="pytest",
        host_identity=identity,
        slot_dir=slot,
        clock=lambda: 100.0,
        heartbeat_interval_seconds=0.0,
        environ={},
    )
    successor = _record(identity, token="successor-token", heartbeat_at=101.0)
    (slot / "owner.json").write_text(json.dumps(successor, sort_keys=True) + "\n", encoding="utf-8")

    lease.release()

    assert json.loads((slot / "owner.json").read_text(encoding="utf-8"))["token"] == "successor-token"


def test_reader_inspection_of_clear_worktree_creates_nothing(tmp_path: Path) -> None:
    identity = _identity("host-a", "lin1")
    slot = tmp_path / "shared-git" / "writer"

    status = worktree_writer.inspect_worktree_writer(
        tmp_path / "worktree",
        host_identity=identity,
        slot_dir=slot,
        now=100.0,
    )

    assert status.state == "clear"
    assert status.active is False
    assert not slot.exists()
    assert list(tmp_path.rglob("*")) == []


def test_host_artifact_paths_route_bytecode_pytest_package_and_logs_outside_worktree(tmp_path: Path) -> None:
    worktree = tmp_path / "shared" / "repo"
    worktree.mkdir(parents=True)
    runtime = tmp_path / "host-local-runtime"
    env = {"YOLOMUX_RUNTIME_DIR": str(runtime)}

    paths = worktree_writer.configure_host_local_artifacts(
        worktree,
        environ=env,
        temporary_dir=tmp_path / "fallback",
        uid=1000,
        apply_process=False,
    )

    for path in (paths.root, paths.python_cache, paths.pytest_cache, paths.package_cache, paths.logs):
        assert path.is_relative_to(runtime)
        assert not path.is_relative_to(worktree)
    assert Path(env["PYTHONPYCACHEPREFIX"]) == paths.python_cache
    assert env["GIT_OPTIONAL_LOCKS"] == "0"


def test_package_import_routes_bytecode_before_product_modules_load(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[1]
    copied_root = tmp_path / "shared-worktree"
    shutil.copytree(
        source_root / "yolomux_lib",
        copied_root / "yolomux_lib",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(
        source_root / "tools",
        copied_root / "tools",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy2(source_root / "yolomux.py", copied_root / "yolomux.py")
    runtime = tmp_path / "host-local-runtime"
    env = {
        **os.environ,
        "PYTHONPATH": str(copied_root),
        "YOLOMUX_RUNTIME_DIR": str(runtime),
    }
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, os, runpy, sys; runpy.run_path('yolomux.py', run_name='bootstrap_probe'); "
            "import yolomux_lib.locales; "
            "print(json.dumps({'prefix': sys.pycache_prefix, 'git_locks': os.environ.get('GIT_OPTIONAL_LOCKS')}))",
        ],
        cwd=copied_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10.0,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert Path(payload["prefix"]).is_relative_to(runtime)
    assert payload["git_locks"] == "0"
    assert list(copied_root.rglob("__pycache__")) == []
    assert list(copied_root.rglob("*.pyc")) == []
    assert list(runtime.rglob("locales*.pyc")), "product modules imported after bootstrap must use the host-local prefix"


def test_pytest_cache_owner_points_outside_the_worktree(pytestconfig: pytest.Config) -> None:
    cache_path = Path(pytestconfig.getini("cache_dir"))
    repo_root = Path(__file__).resolve().parents[1]

    assert cache_path.is_absolute()
    assert not cache_path.is_relative_to(repo_root)


def test_static_build_and_check_runner_acquire_the_shared_writer_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    purposes: list[str] = []

    @contextmanager
    def acquire(_root: Path, *, purpose: str, **_kwargs: Any) -> Any:
        purposes.append(purpose)
        yield object()

    monkeypatch.setattr(static_build.worktree_writer, "acquire_worktree_writer", acquire)
    monkeypatch.setattr(static_build, "lint_light_mode_pairs", lambda: [])
    assert static_build.main(["--lint-light"]) == 0

    monkeypatch.setattr(check.worktree_writer, "acquire_worktree_writer", acquire)
    monkeypatch.setattr(check, "run_serial", lambda _selected: [])
    report = tmp_path / "check-report.json"
    assert check.main(["--serial", "--lane", "whitespace", "--no-tool-guard", "--performance-report", str(report)]) == 0
    assert purposes == ["static-build", "test-gate"]


def test_server_start_reports_writer_but_keeps_read_only_start_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(cli, "server_start_writer_warning", lambda _root: "foreign writer on lin1; read-only")
    monkeypatch.setattr(cli, "emit_server_log", lambda _level, _source, message, **_kwargs: warnings.append(message))

    allowed = cli.report_worktree_writer_warning()

    assert allowed is True
    assert warnings == ["foreign writer on lin1; read-only"]


def test_docs_separate_relocated_artifacts_from_single_writer_generated_source() -> None:
    root = Path(__file__).resolve().parents[1]
    inventory = (root / "docs" / "MUTABLE-PATH-INVENTORY.md").read_text(encoding="utf-8")
    development = (root / "docs" / "DEVELOPMENT.md").read_text(encoding="utf-8")

    assert "detect and warn" in development
    assert "refuse writer tools" in development
    assert "PYTHONPYCACHEPREFIX" in inventory
    assert ".pytest_cache" in inventory
    assert "uploads" in inventory and "logs" in inventory
    assert "generated static" in inventory
    assert "package metadata" in inventory
    assert "GIT_OPTIONAL_LOCKS=0" in development
