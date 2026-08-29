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


def test_in_container_caller_without_inherited_token_refuses_instead_of_crashing(
    tmp_path: Path,
) -> None:
    # Regression: an in-container caller with no inherited YOLOMUX_WORKTREE_WRITER_TOKEN used to
    # fall through to the real acquire path and crash with a bare PermissionError deep in a
    # recursive mkdir, because the writer slot lives under a directory containers only ever get
    # read-only. Simulate that by pointing at a slot directory whose parent cannot be created
    # (read-only), matching the read-only git-common bind mount.
    readonly_root = tmp_path / "readonly-git-common"
    readonly_root.mkdir()
    slot = readonly_root / "worktrees" / "some-worktree" / "yolomux" / "worktree-writer"
    try:
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(worktree_writer, "_claim_slot_leaf", lambda _slot: (_ for _ in ()).throw(PermissionError("read-only bind mount")))
        with pytest.raises(worktree_writer.WorktreeWriterContainerRefusal) as caught:
            worktree_writer.acquire_worktree_writer(
                tmp_path / "worktree",
                purpose="pytest",
                slot_dir=slot,
                clock=lambda: 100.0,
                heartbeat_interval_seconds=0.0,
                environ={"YOLOMUX_CHECK_IN_CONTAINER": "1"},
            )
        assert not slot.exists()
    finally:
        monkeypatch.undo()

    assert caught.value.reason == worktree_writer.CONTAINER_REFUSAL_NO_TOKEN
    assert isinstance(caught.value.__cause__, PermissionError)
    assert worktree_writer.WRITER_TOKEN_ENV in str(caught.value)


def test_in_container_caller_refuses_even_when_parent_directory_already_exists(
    tmp_path: Path,
) -> None:
    # Regression: an earlier version of the guard checked only `slot.parent.exists()`, so
    # when the parent directory was already materialized (e.g. by a prior host run under
    # the same read-only mount) but the slot leaf itself was not, the guard was skipped and
    # `slot.mkdir()` still attempted a real write into the read-only tree.
    readonly_root = tmp_path / "readonly-git-common"
    readonly_root.mkdir()
    slot_parent = readonly_root / "worktrees" / "some-worktree" / "yolomux"
    slot_parent.mkdir(parents=True)
    slot = slot_parent / "worktree-writer"
    # A real read-only bind mount denies writes at every level, not just the top -- chmod
    # only the top ancestor and the OS permission check on `slot_parent` itself (still 0o755)
    # would let `slot.mkdir()` succeed, since Unix write permission is checked per-directory,
    # not inherited from an ancestor. Chmod the immediate parent to match the real scenario.
    try:
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(worktree_writer, "_claim_slot_leaf", lambda _slot: (_ for _ in ()).throw(PermissionError("read-only bind mount")))
        with pytest.raises(worktree_writer.WorktreeWriterContainerRefusal) as caught:
            worktree_writer.acquire_worktree_writer(
                tmp_path / "worktree",
                purpose="pytest",
                slot_dir=slot,
                clock=lambda: 100.0,
                heartbeat_interval_seconds=0.0,
                environ={"YOLOMUX_CHECK_IN_CONTAINER": "1"},
            )
        assert not slot.exists()
    finally:
        monkeypatch.undo()

    assert isinstance(caught.value.__cause__, PermissionError)


def test_in_container_caller_with_a_writable_caller_owned_slot_dir_succeeds(
    tmp_path: Path,
) -> None:
    # Negative control: the refusal must key off the real PermissionError, not off
    # YOLOMUX_CHECK_IN_CONTAINER alone. A caller that owns a fully writable `slot_dir`
    # (a test's own `tmp_path`) is never the shared read-only mount, so its mkdir simply
    # succeeds and the refusal branch is never entered. A real caller
    # (tests/test_gate_f8_harness.py's writer-lease test) does exactly this while
    # YOLOMUX_CHECK_IN_CONTAINER=1 is set in the real process environment.
    slot = tmp_path / "caller-owned" / "worktree-writer"

    lease = worktree_writer.acquire_worktree_writer(
        tmp_path / "worktree",
        purpose="pytest",
        slot_dir=slot,
        clock=lambda: 100.0,
        heartbeat_interval_seconds=0.0,
        environ={"YOLOMUX_CHECK_IN_CONTAINER": "1"},
    )

    assert lease.borrowed is False
    assert slot.exists()
    lease.release()


def test_in_container_caller_with_inherited_token_still_borrows_normally(tmp_path: Path) -> None:
    local = _identity("host-a", "lin1")
    slot = tmp_path / "shared-git" / "writer"
    record = _write_slot(slot, _record(local, token="host-token", heartbeat_at=100.0))

    lease = worktree_writer.acquire_worktree_writer(
        tmp_path / "worktree",
        purpose="pytest",
        host_identity=local,
        slot_dir=slot,
        clock=lambda: 100.0,
        heartbeat_interval_seconds=0.0,
        environ={"YOLOMUX_CHECK_IN_CONTAINER": "1", worktree_writer.WRITER_TOKEN_ENV: "host-token"},
    )

    assert lease.borrowed
    assert lease.token == "host-token"
    assert (slot / "owner.json").read_bytes() == record


def test_in_container_refusal_separates_missing_authority_from_stale_authority(
    tmp_path: Path,
) -> None:
    # Regression: the refusal used to be derived purely from YOLOMUX_CHECK_IN_CONTAINER, so a
    # container that DID inherit a token which no longer matched any live writer record was
    # told it had "no inherited token" -- false, and it sends the operator to re-export a token
    # that is already exported instead of re-acquiring the lease on the host.
    readonly_root = tmp_path / "readonly-git-common"
    readonly_root.mkdir()
    slot = readonly_root / "yolomux" / "worktree-writer"
    try:
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(worktree_writer, "_claim_slot_leaf", lambda _slot: (_ for _ in ()).throw(PermissionError("read-only bind mount")))
        with pytest.raises(worktree_writer.WorktreeWriterContainerRefusal) as missing:
            worktree_writer.acquire_worktree_writer(
                tmp_path / "worktree",
                purpose="pytest",
                slot_dir=slot,
                clock=lambda: 100.0,
                heartbeat_interval_seconds=0.0,
                environ={"YOLOMUX_CHECK_IN_CONTAINER": "1"},
            )
        with pytest.raises(worktree_writer.WorktreeWriterContainerRefusal) as stale:
            worktree_writer.acquire_worktree_writer(
                tmp_path / "worktree",
                purpose="pytest",
                slot_dir=slot,
                clock=lambda: 100.0,
                heartbeat_interval_seconds=0.0,
                environ={
                    "YOLOMUX_CHECK_IN_CONTAINER": "1",
                    worktree_writer.WRITER_TOKEN_ENV: "no-longer-live-token",
                },
            )
    finally:
        monkeypatch.undo()

    assert missing.value.reason == worktree_writer.CONTAINER_REFUSAL_NO_TOKEN
    assert stale.value.reason == worktree_writer.CONTAINER_REFUSAL_STALE_TOKEN
    assert str(missing.value) != str(stale.value)
    assert "stale or invalid" in str(stale.value)
    assert "stale or invalid" not in str(missing.value)
    assert isinstance(missing.value, worktree_writer.WorktreeWriterError)
    assert isinstance(stale.value, worktree_writer.WorktreeWriterError)
    assert isinstance(stale.value.__cause__, PermissionError)


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
    report = tmp_path / "check-report.json"

    def run_serial(_selected: Any, *, output_root: Path | None = None) -> list[Any]:
        assert output_root == check.lane_output_root(report)
        return []

    monkeypatch.setattr(check, "run_serial", run_serial)
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


def test_a_writer_killed_mid_heartbeat_does_not_strand_the_next_writer(tmp_path: Path) -> None:
    # `atomic_write_text` removes its temp sibling on every exit path, so one can only survive if the
    # writing process was killed between creating it and renaming it -- which is exactly what a
    # cancelled test run does. Reclaim used to classify that leftover as a foreign entry and abort,
    # so the run after a cancelled one died with an internal error instead of taking the slot over.
    identity = _identity("host-a", "lin1")
    slot = tmp_path / "shared-git" / "writer"
    _write_slot(slot, _record(identity, token="old-token", heartbeat_at=10.0))
    abandoned = slot / f".{worktree_writer.WRITER_RECORD_NAME}.401950.140674180654784.1787679736965115809.tmp"
    abandoned.write_text("{}", encoding="utf-8")

    lease = worktree_writer.acquire_worktree_writer(
        tmp_path / "worktree",
        purpose="pytest",
        host_identity=identity,
        slot_dir=slot,
        clock=lambda: 100.0,
        stale_after_seconds=30.0,
        heartbeat_interval_seconds=0.0,
        environ={},
    )

    assert not abandoned.exists(), "the abandoned heartbeat temp must not survive the reclaim"
    active = json.loads((slot / "owner.json").read_text(encoding="utf-8"))
    assert active["token"] == lease.token
    lease.release()
    assert not slot.exists()


def test_a_genuinely_foreign_entry_still_blocks_removing_a_writer_slot(tmp_path: Path) -> None:
    # The tolerance above is scoped to this module's own abandoned temporaries by name. Anything
    # else in the slot is still someone else's file and must stop the slot being deleted.
    slot = tmp_path / "shared-git" / "writer"
    slot.mkdir(parents=True)
    (slot / worktree_writer.WRITER_RECORD_NAME).write_text("{}", encoding="utf-8")
    (slot / "someone-elses-file").write_text("keep me", encoding="utf-8")

    with pytest.raises(worktree_writer.WorktreeWriterReleaseError) as caught:
        worktree_writer._remove_exact_slot(slot)

    assert "someone-elses-file" in str(caught.value)
    assert (slot / "someone-elses-file").exists()
