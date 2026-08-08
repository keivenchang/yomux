import json
import multiprocessing
import os
from pathlib import Path

import yaml

from yolomux_lib.infra.host_identity import HostIdentity
from yolomux_lib.infra import shared_config_lock


def _hold_lock(path_text, entered, release):
    with shared_config_lock.shared_config_lock(Path(path_text)):
        entered.set()
        release.wait(5)


def _crash_while_locked(path_text, entered):
    with shared_config_lock.shared_config_lock(Path(path_text)):
        entered.set()
        os._exit(0)


def _concurrent_update(path_text, key, value, start, outcomes):
    start.wait(5)
    result = shared_config_lock.update_shared_yaml(Path(path_text), {key: value})
    outcomes.put((key, result.base_revision, result.revision))


def test_posix_record_lock_excludes_two_local_processes(tmp_path):
    context = multiprocessing.get_context("spawn")
    path = tmp_path / "settings.yaml"
    first_entered = context.Event()
    second_entered = context.Event()
    release = context.Event()
    first = context.Process(target=_hold_lock, args=(str(path), first_entered, release))
    second = context.Process(target=_hold_lock, args=(str(path), second_entered, release))
    first.start()
    assert first_entered.wait(5)
    second.start()
    assert second_entered.wait(0.2) is False
    release.set()
    assert second_entered.wait(5)
    first.join(5)
    second.join(5)
    assert first.exitcode == 0
    assert second.exitcode == 0


def test_posix_record_lock_releases_when_owner_crashes(tmp_path):
    context = multiprocessing.get_context("spawn")
    path = tmp_path / "auth.yaml"
    entered = context.Event()
    crashed = context.Process(target=_crash_while_locked, args=(str(path), entered))
    crashed.start()
    assert entered.wait(5)
    crashed.join(5)
    assert crashed.exitcode == 0

    with shared_config_lock.shared_config_lock(path):
        pass


def test_stale_different_key_updates_merge_and_yaml_is_never_torn(tmp_path):
    path = tmp_path / "settings.yaml"
    initial = shared_config_lock.update_shared_yaml(path, {"theme": "light", "locale": "en"})
    first = shared_config_lock.update_shared_yaml(path, {"theme": "dark"}, expected_revision=initial.revision)
    second = shared_config_lock.update_shared_yaml(path, {"locale": "fr"}, expected_revision=initial.revision)

    assert first.revision_conflict is False
    assert second.revision_conflict is True
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {"locale": "fr", "theme": "dark"}


def test_concurrent_different_key_updates_survive_and_remain_valid_yaml(tmp_path):
    context = multiprocessing.get_context("spawn")
    path = tmp_path / "yolo-rules.yaml"
    start = context.Event()
    outcomes = context.Queue()
    first = context.Process(target=_concurrent_update, args=(str(path), "allow", True, start, outcomes))
    second = context.Process(target=_concurrent_update, args=(str(path), "mode", "strict", start, outcomes))
    first.start()
    second.start()
    start.set()
    first.join(5)
    second.join(5)

    assert first.exitcode == 0
    assert second.exitcode == 0
    assert sorted(outcomes.get(timeout=2)[0] for _ in range(2)) == ["allow", "mode"]
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {"allow": True, "mode": "strict"}


def test_same_key_update_serializes_and_reports_host_identity_owner(tmp_path, monkeypatch):
    identity = HostIdentity("host-a", "display-a", "boot-a", 123, "proc:456", 456, "nonce-a", "YOLOMUX_HOST_ID")
    monkeypatch.setattr(shared_config_lock, "current_host_identity", lambda: identity)
    path = tmp_path / "state.json"
    initial = shared_config_lock.update_shared_yaml(path, {"theme": "light"})
    first = shared_config_lock.update_shared_yaml(path, {"theme": "dark"}, expected_revision=initial.revision)
    second = shared_config_lock.update_shared_yaml(path, {"theme": "black"}, expected_revision=initial.revision)

    assert first.revision_conflict is False
    assert second.revision_conflict is True
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {"theme": "black"}
    assert second.owner_record == identity.diagnostics()


def test_json_state_updates_merge_keys_without_torn_content(tmp_path):
    path = tmp_path / "state.json"
    initial = shared_config_lock.update_shared_json(path, {"layout": "grid"})
    updated = shared_config_lock.update_shared_json(path, {"theme": "dark"}, expected_revision=initial.revision)

    assert updated.revision_conflict is False
    assert json.loads(path.read_text(encoding="utf-8")) == {"layout": "grid", "theme": "dark"}


def test_cross_host_acceptance_contract_names_exporter_and_nfs_client():
    assert "exporter-local" in shared_config_lock.CROSS_HOST_ACCEPTANCE_CONTRACT
    assert "NFS client" in shared_config_lock.CROSS_HOST_ACCEPTANCE_CONTRACT
    assert "lin1" in shared_config_lock.CROSS_HOST_ACCEPTANCE_CONTRACT
    assert "lin2" in shared_config_lock.CROSS_HOST_ACCEPTANCE_CONTRACT
