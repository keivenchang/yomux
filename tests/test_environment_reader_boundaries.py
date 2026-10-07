# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Injected environment and parent-lock handoffs honor canonical presence."""

import os

import pytest

from tools.instance_isolation import normalize_yomux_environment
from tools.tool_guard import hold_host_tool_flock
from tools.tool_guard import parent_owns_tool_lock
from tools.tool_guard import tool_lock_owner_marker
from yomux_lib.infra import host_diagnostics
from yomux_lib.infra import worktree_writer
from yomux_lib.infra.filesystem_preflight import FilesystemClassification
from yomux_lib.infra.filesystem_preflight import FilesystemPreflightError
from yomux_lib.infra.filesystem_preflight import preflight_mutable_roots
from yomux_lib.infra.host_identity import HostIdentity
from yomux_lib.infra.host_identity import HostIdentityError
from yomux_lib.infra.host_identity import LateHostIdentityOverrideError
from yomux_lib.infra.host_identity import current_host_identity
from yomux_lib.tmux.tmux_utils import use_machine_tmux_for_operator_launch


@pytest.mark.parametrize("values", [
    {"YOLOMUX_HOST_ID": "chosen"},
    {"YOMUX_HOST_ID": "chosen"},
    {"YOMUX_HOST_ID": "chosen", "YOLOMUX_HOST_ID": "stale"},
])
def test_injected_host_identity_uses_canonical_override(tmp_path, values):
    boot = tmp_path / "boot"
    boot.write_bytes(b"boot-id")
    identity = HostIdentity.from_system(
        environ=values,
        machine_id_path=tmp_path / "absent-machine",
        boot_id_path=boot,
        hostname_reader=lambda: "fixture",
        pid_reader=lambda: 4242,
        start_identity_reader=lambda _pid: "proc:42",
        nonce_factory=lambda: "nonce",
    )
    assert identity.stable_host_id == "chosen"


def test_empty_canonical_host_identity_does_not_revive_legacy():
    with pytest.raises(HostIdentityError):
        HostIdentity.from_system(environ={"YOMUX_HOST_ID": "", "YOLOMUX_HOST_ID": "stale"})


def test_late_canonical_host_identity_change_is_fenced(monkeypatch):
    monkeypatch.setenv("YOMUX_HOST_ID", "first")
    monkeypatch.setenv("YOLOMUX_HOST_ID", "stale")
    current_host_identity.cache_clear()
    try:
        identity = current_host_identity()
        assert identity.stable_host_id == "first"
        monkeypatch.setenv("YOMUX_HOST_ID", "second")
        with pytest.raises(LateHostIdentityOverrideError) as caught:
            current_host_identity()
        assert caught.value.identity is identity
        assert caught.value.rejected_override == "second"
    finally:
        current_host_identity.cache_clear()


@pytest.mark.parametrize("canonical", ["1", ""])
def test_synthetic_diagnostics_resolve_presence_before_machine_reads(monkeypatch, canonical):
    def forbidden():
        raise RuntimeError("machine owner reached")

    monkeypatch.setattr(host_diagnostics.RootLayout, "current", forbidden)
    values = {"YOMUX_STATS_SYNTHETIC_HOST": canonical, "YOLOMUX_STATS_SYNTHETIC_HOST": "1"}
    if canonical:
        assert host_diagnostics.collect_host_diagnostics(environ=values).payload(admin=True)["identity_reason_code"] == "synthetic_host_diagnostics"
    else:
        with pytest.raises(RuntimeError, match="machine owner reached"):
            host_diagnostics.collect_host_diagnostics(environ=values)


@pytest.mark.parametrize("canonical", ["1", ""])
def test_network_filesystem_escape_hatch_resolves_canonical_presence(tmp_path, canonical):
    values = {"YOMUX_ALLOW_NETWORK_FILESYSTEM_MUTABLE_ROOTS": canonical, "YOLOMUX_ALLOW_NETWORK_FILESYSTEM_MUTABLE_ROOTS": "1"}
    arguments = {
        "wal_databases": [tmp_path / "database"],
        "classifier": lambda path: FilesystemClassification(path, "nfs4", True, True),
        "environ": values,
    }
    if canonical:
        with pytest.warns(RuntimeWarning):
            assert len(preflight_mutable_roots(**arguments)) == 1
    else:
        with pytest.raises(FilesystemPreflightError):
            preflight_mutable_roots(**arguments)


def test_canonical_container_marker_classifies_writer_refusal(monkeypatch, tmp_path):
    def readonly(_slot):
        raise PermissionError("read-only fixture")

    monkeypatch.setattr(worktree_writer, "_claim_slot_leaf", readonly)
    with pytest.raises(worktree_writer.WorktreeWriterContainerRefusal) as caught:
        worktree_writer.acquire_worktree_writer(
            tmp_path,
            slot_dir=tmp_path / "writer",
            purpose="fixture",
            environ={"YOMUX_CHECK_IN_CONTAINER": "1", "YOLOMUX_CHECK_IN_CONTAINER": "0"},
        )
    assert caught.value.reason == worktree_writer.CONTAINER_REFUSAL_NO_TOKEN


def test_parent_tool_lock_canonical_presence_and_alias_provenance(tmp_path, capsys):
    path = tmp_path / "tool.lock"
    marker = tool_lock_owner_marker(path)
    values = {"YOMUX_CHECK_TOOL_LOCK_OWNER": "previous", "YOLOMUX_CHECK_TOOL_LOCK_OWNER": "stale"}
    with hold_host_tool_flock(path, environ=values):
        assert values["YOMUX_CHECK_TOOL_LOCK_OWNER"] == marker
        assert values["YOLOMUX_CHECK_TOOL_LOCK_OWNER"] == marker
        assert parent_owns_tool_lock(path, environ={"YOMUX_CHECK_TOOL_LOCK_OWNER": marker}, parent_pid=os.getpid())
        assert not parent_owns_tool_lock(path, environ={"YOMUX_CHECK_TOOL_LOCK_OWNER": "", "YOLOMUX_CHECK_TOOL_LOCK_OWNER": marker}, parent_pid=os.getpid())
        values.update(normalize_yomux_environment(values, warn=True))
        warning = capsys.readouterr().err
        assert "deprecated" in warning and "stale" not in warning
        normalize_yomux_environment(values, warn=True)
        assert capsys.readouterr().err == ""
    assert values["YOMUX_CHECK_TOOL_LOCK_OWNER"] == "previous"
    assert values["YOLOMUX_CHECK_TOOL_LOCK_OWNER"] == "previous"


def test_operator_tmux_optin_replaces_canonical_denial_without_false_warning(monkeypatch, capsys):
    monkeypatch.setenv("YOMUX_TMUX_SOCKET", "")
    monkeypatch.setenv("YOLOMUX_TMUX_SOCKET", "")
    monkeypatch.setenv("YOMUX_TMUX_ALLOW_DEFAULT_SERVER", "0")
    monkeypatch.setenv("YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER", "0")
    for key, value in normalize_yomux_environment(os.environ, warn=True).items():
        if key.startswith(("YOMUX_", "YOLOMUX_")):
            monkeypatch.setenv(key, value)
    capsys.readouterr()
    use_machine_tmux_for_operator_launch()
    normalized = normalize_yomux_environment(os.environ, warn=True)
    assert normalized["YOMUX_TMUX_ALLOW_DEFAULT_SERVER"] == "1"
    assert normalized["YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER"] == "1"
    assert capsys.readouterr().err == ""
