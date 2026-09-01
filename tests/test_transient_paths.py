# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused tests for manifest-backed disposable roots."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from yolomux_lib.infra.host_identity import HostIdentity
from yolomux_lib.infra.transient_paths import TransientPathError
from yolomux_lib.infra.transient_paths import create_run_root
from yolomux_lib.infra.transient_paths import NAMESPACE_SERVER
from yolomux_lib.infra.transient_paths import NAMESPACE_TEST
from yolomux_lib.infra.transient_paths import remove_run_root
from yolomux_lib.infra.transient_paths import recover_stale_run_root
from yolomux_lib.infra.host_identity import process_start_identity


def fixture_identity(*, stable_host_id: str = "host-a", pid: int = 4242) -> HostIdentity:
    return HostIdentity(
        stable_host_id=stable_host_id,
        display_hostname="host-a.example",
        boot_id="boot-a",
        pid=pid,
        process_start_identity="proc:6262",
        process_start_ticks=6262,
        instance_nonce="instance-a",
        stable_host_id_source="fixture",
    )


def test_run_root_writes_identity_manifest_and_named_private_children(tmp_path: Path) -> None:
    identity = fixture_identity(pid=4242)
    run_root = create_run_root(namespace=NAMESPACE_TEST, owner_role="pytest", temporary_base=tmp_path, identity=identity, clock=12.5)

    assert run_root.path.parent == tmp_path
    assert run_root.path.name.startswith("yolomux-test-4242-")
    assert f"-{run_root.identity.pid}-{run_root.manifest['owner_uid']}-" in run_root.path.name
    assert run_root.path.stat().st_mode & 0o777 == 0o700
    assert json.loads((run_root.path / "manifest.json").read_text(encoding="utf-8")) == run_root.manifest
    assert run_root.child("browser").is_dir()
    with pytest.raises(TransientPathError):
        run_root.child("../outside")

    remove_run_root(run_root, identity=identity)
    assert not run_root.path.exists()


def test_cleanup_rejects_changed_root_identity(tmp_path: Path) -> None:
    identity = fixture_identity()
    run_root = create_run_root(owner_role="pytest", temporary_base=tmp_path, identity=identity)
    replacement = tmp_path / "replacement"
    run_root.path.rename(replacement)
    run_root.path.mkdir(mode=0o700)
    (run_root.path / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(TransientPathError, match="identity changed|malformed"):
        remove_run_root(run_root, identity=identity)
    assert replacement.exists()


def test_cleanup_rejects_a_replacement_directory_at_the_original_path(tmp_path: Path) -> None:
    identity = fixture_identity()
    run_root = create_run_root(owner_role="pytest", temporary_base=tmp_path, identity=identity)
    original = run_root.path
    original.rename(tmp_path / "original")
    original.mkdir(mode=0o700)
    (original / "important.txt").write_text("keep", encoding="utf-8")
    manifest = json.loads((tmp_path / "original" / "manifest.json").read_text(encoding="utf-8"))
    (original / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(TransientPathError, match="identity changed|malformed"):
        remove_run_root(run_root, identity=identity)
    assert (original / "important.txt").exists()


def test_recovery_rejects_retained_and_foreign_roots(tmp_path: Path) -> None:
    identity = fixture_identity()
    retained = create_run_root(
        owner_role="browser",
        retention_class="retain-on-failure",
        temporary_base=tmp_path,
        identity=identity,
    )
    with pytest.raises(TransientPathError, match="only disposable"):
        recover_stale_run_root(retained.path, identity=identity)

    foreign = fixture_identity(stable_host_id="host-b")
    foreign_root = create_run_root(owner_role="pytest", temporary_base=tmp_path, identity=foreign)
    with pytest.raises(TransientPathError, match="another host"):
        recover_stale_run_root(foreign_root.path, identity=identity)


def test_recovery_removes_a_root_only_after_proving_the_owner_is_gone(tmp_path: Path) -> None:
    identity = fixture_identity(pid=999_999_999)
    run_root = create_run_root(owner_role="pytest", temporary_base=tmp_path, identity=identity)

    recover_stale_run_root(run_root.path, identity=identity)

    assert not run_root.path.exists()


def test_recovery_keeps_a_root_with_incomplete_owner_identity(tmp_path: Path) -> None:
    identity = fixture_identity()
    run_root = create_run_root(owner_role="pytest", temporary_base=tmp_path, identity=identity)
    manifest_path = run_root.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["owner"]["process_start_identity"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(TransientPathError, match="transient owner is missing"):
        recover_stale_run_root(run_root.path, identity=identity)
    assert run_root.path.exists()


def test_server_namespace_is_distinct_from_test_namespace(tmp_path: Path) -> None:
    identity = fixture_identity()
    server_root = create_run_root(namespace=NAMESPACE_SERVER, owner_role="server", temporary_base=tmp_path, identity=identity)
    assert server_root.path.parent == tmp_path
    assert server_root.path.name.startswith("yolomux-server-4242-")
    with pytest.raises(TransientPathError, match="retained"):
        retained = create_run_root(namespace=NAMESPACE_SERVER, owner_role="server", retention_class="service-runtime", temporary_base=tmp_path, identity=identity)
        remove_run_root(retained, identity=identity)
    for child in retained.path.iterdir():
        child.unlink()
    retained.path.rmdir()


def test_child_claim_blocks_cleanup_until_the_child_retires(tmp_path: Path) -> None:
    identity = fixture_identity(pid=999_999_999)
    run_root = create_run_root(owner_role="pytest", temporary_base=tmp_path, identity=identity)
    with run_root.child_claim(kind="worker", pid=os.getpid(), start_identity=process_start_identity(os.getpid()) or ""):
        with pytest.raises(TransientPathError, match="active or ambiguous"):
            remove_run_root(run_root, identity=identity)
        assert run_root.path.exists()
