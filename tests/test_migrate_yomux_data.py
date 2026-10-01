# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from tools import migrate_yomux_data as migration


def _roots(tmp_path: Path, *, count: int = 2) -> list[dict[str, str]]:
    roots = []
    for index in range(count):
        source = tmp_path / f"old-{index}"
        target = tmp_path / f"new-{index}"
        roots.append({"label": f"root-{index}", "source": str(source), "target": str(target)})
    return roots


@pytest.mark.parametrize(
    "command",
    (
        "/opt/yomux/bin/yomux-go --port 7220",
        "/usr/bin/python3 /opt/yomux/yomux.py --port 7220",
        "/usr/bin/python3 -m yomux_lib.batchd",
        "/opt/yomux/bin/yolomux-go --port 7220",
        "/opt/yomux/bin/yomux_go --port 7220",
        "/opt/yomux/bin/yolomux_go --port 7220",
        "/usr/bin/python3 -m yolomux_lib.batchd",
    ),
)
def test_writer_detection_matches_server_entry_points(command: str) -> None:
    assert migration.is_writer_command(command)


@pytest.mark.parametrize(
    "command",
    (
        "tmux -S /tmp/yolomux-go-e2e.sock new-session -d",
        "/usr/bin/python3 /tmp/work/yolomux/tests/mockers/claude.py --mock",
        "/usr/bin/python3 -c 'print(\"yomux_lib.batchd\")'",
    ),
)
def test_writer_detection_ignores_mentions_in_unrelated_commands(command: str) -> None:
    assert not migration.is_writer_command(command)


def test_missing_process_census_fails_closed(tmp_path: Path) -> None:
    assert migration.active_writer_processes(tmp_path / "no-proc") == [
        {"pid": None, "command": f"process census unavailable at {tmp_path / 'no-proc'}; refusing migration"}
    ]


def test_macos_process_census_blocks_writers_without_exposing_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(migration.sys, "platform", "darwin")
    monkeypatch.setattr(
        migration.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["ps"],
            returncode=0,
            stdout=(
                "  123 /opt/yomux/bin/yomux-go --port 7220 --secret hidden\n"
                "  124 /usr/bin/python3 -m yomux_lib.batchd --serve\n"
                "  125 /usr/bin/python3 -m unrelated.worker\n"
            ),
            stderr="",
        ),
    )

    matches = migration.active_writer_processes(tmp_path / "no-proc", own_pid=999)

    assert matches == [
        {"pid": 123, "command": "yomux-go (YOmux writer; root scope unavailable on macOS)"},
        {"pid": 124, "command": "python3 (YOmux writer; root scope unavailable on macOS)"},
    ]
    assert "hidden" not in str(matches)


def test_writer_census_only_blocks_processes_that_can_touch_selected_roots(tmp_path: Path) -> None:
    home = tmp_path / "home"
    roots = [{
        "label": "config",
        "source": str(home / ".config" / "yolomux"),
        "target": str(home / ".config" / "yomux"),
    }]

    assert migration.process_uses_migrated_roots({"HOME": str(home)}, roots, home)
    assert migration.process_uses_migrated_roots(
        {"HOME": "/other-user", "YOMUX_CONFIG_DIR": str(home / ".config" / "yomux")},
        roots,
        home,
    )
    assert not migration.process_uses_migrated_roots(
        {"HOME": "/other-user", "YOMUX_CONFIG_DIR": "/private/config"},
        roots,
        home,
    )


def test_writer_census_redacts_process_arguments(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    process = proc_root / "123"
    process.mkdir(parents=True)
    (process / "cmdline").write_bytes(b"/usr/bin/python3\0/tmp/yomux.py\0--password=hidden\0")
    (process / "environ").write_bytes(f"HOME={tmp_path}\0".encode())
    roots = [{"label": "config", "source": str(tmp_path / ".config" / "yolomux"), "target": str(tmp_path / ".config" / "yomux")}]

    matches = migration.active_writer_processes(proc_root, own_pid=999, roots=roots, home=tmp_path)

    assert matches == [{"pid": 123, "command": "python3 (YOmux writer)"}]


def test_writer_census_fails_closed_when_own_process_command_line_is_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc_root = tmp_path / "proc"
    process = proc_root / "123"
    process.mkdir(parents=True)
    original_read_bytes = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path == process / "cmdline":
            raise PermissionError("hidden")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    matches = migration.active_writer_processes(proc_root, own_pid=999)

    assert matches == [
        {"pid": 123, "command": "YOmux process command line unavailable; refusing migration (PermissionError)"}
    ]


def test_writer_census_matches_configured_custom_go_binary(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    process = proc_root / "123"
    process.mkdir(parents=True)
    (process / "cmdline").write_bytes(b"/opt/custom/yomux-server\0--port\0" + b"7220\0")
    (process / "environ").write_bytes(
        f"HOME={tmp_path}\0YOMUX_GO_BINARY=/opt/custom/yomux-server\0YOMUX_CONFIG_DIR={tmp_path / 'old'}\0".encode()
    )
    roots = [{"label": "config", "source": str(tmp_path / "old"), "target": str(tmp_path / "new")}]

    matches = migration.active_writer_processes(proc_root, own_pid=999, roots=roots, home=tmp_path)

    assert matches == [{"pid": 123, "command": "yomux-server (YOmux writer)"}]


def test_dry_run_inventory_preserves_wal_and_journal_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "YOMUX_ROOT", "YOLOMUX_ROOT", "YOMUX_CONFIG_DIR", "YOLOMUX_CONFIG_DIR",
        "YOMUX_STATE_DIR", "YOLOMUX_STATE_DIR", "YOMUX_CACHE_DIR", "YOLOMUX_CACHE_DIR",
        "YOMUX_CA_DIR", "YOLOMUX_CA_DIR", "XDG_CACHE_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    source = tmp_path / ".config" / "yolomux"
    target = tmp_path / ".config" / "yomux"
    source.mkdir(parents=True)
    for name, payload in (("stats.sqlite3", b"db"), ("stats.sqlite3-wal", b"wal"), ("settings.yaml-journal", b"journal")):
        (source / name).write_bytes(payload)
    mapping_path = tmp_path / "recovery" / "migration.json"

    result = migration.main(["dry-run", "--home", str(tmp_path), "--mapping", str(mapping_path)])

    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report["roots"] == [
        migration.root_inventory(source, target, "config"),
        migration.root_inventory(tmp_path / ".local/state/yolomux", tmp_path / ".local/state/yomux", "state"),
        migration.root_inventory(tmp_path / ".cache/yolomux", tmp_path / ".cache/yomux", "cache"),
        migration.root_inventory(tmp_path / ".local/share/yolomux-ca", tmp_path / ".local/share/yomux-ca", "ca"),
    ]
    assert not mapping_path.exists()
    assert {path.name for path in source.iterdir()} == {"stats.sqlite3", "stats.sqlite3-wal", "settings.yaml-journal"}


def test_dry_run_blocks_symlinked_source_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "YOMUX_ROOT", "YOLOMUX_ROOT", "YOMUX_CONFIG_DIR", "YOLOMUX_CONFIG_DIR",
        "YOMUX_STATE_DIR", "YOLOMUX_STATE_DIR", "YOMUX_CACHE_DIR", "YOLOMUX_CACHE_DIR",
        "YOMUX_CA_DIR", "YOLOMUX_CA_DIR", "XDG_CACHE_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    actual_source = tmp_path / "external-config"
    actual_source.mkdir()
    (actual_source / "auth.yaml").write_text("users: []\n", encoding="utf-8")
    source = tmp_path / ".config" / "yolomux"
    source.parent.mkdir()
    source.symlink_to(actual_source, target_is_directory=True)
    target = tmp_path / ".config" / "yomux"
    mapping_path = tmp_path / "recovery" / "migration.json"

    result = migration.main(["dry-run", "--home", str(tmp_path), "--mapping", str(mapping_path)])

    report = json.loads(capsys.readouterr().out)
    config = next(item for item in report["roots"] if item["label"] == "config")
    assert result == 2
    assert config["state"] == "source_symlink"
    assert config["source_is_symlink"] is True
    assert config["source_exists"] is True
    assert config["source_has_data"] is True
    assert config["target"] == str(target)
    assert not mapping_path.exists()


def test_default_roots_leave_explicit_operator_roots_untouched(tmp_path: Path) -> None:
    roots = migration.default_roots(tmp_path, {
        "YOMUX_CONFIG_DIR": str(tmp_path / "custom-config"),
        "YOLOMUX_STATE_DIR": str(tmp_path / "custom-state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache-home"),
    })

    assert [entry["label"] for entry in roots] == ["cache", "ca"]
    assert roots[0]["source"] == str(tmp_path / "cache-home" / "yolomux")
    assert roots[0]["target"] == str(tmp_path / "cache-home" / "yomux")


def test_default_roots_honor_empty_new_root_over_legacy_root(tmp_path: Path) -> None:
    roots = migration.default_roots(tmp_path, {
        "YOMUX_ROOT": "",
        "YOLOMUX_ROOT": str(tmp_path / "legacy-root"),
        "YOMUX_CONFIG_DIR": "",
        "YOLOMUX_CONFIG_DIR": str(tmp_path / "legacy-config"),
    })

    assert [entry["label"] for entry in roots] == ["config", "state", "cache", "ca"]
    assert roots[0]["source"] == str(tmp_path / ".config" / "yolomux")


def test_apply_backups_then_rollback_restores_roots_without_losing_sidecars(tmp_path: Path) -> None:
    roots = _roots(tmp_path, count=1)
    source = Path(roots[0]["source"])
    source.mkdir()
    (source / "stats.db").write_bytes(b"database")
    (source / "stats.db-wal").write_bytes(b"committed wal")
    (source / "stats.db-shm").write_bytes(b"shared memory")
    (source / "auth.yaml").write_text("secret: preserved\n", encoding="utf-8")
    mapping_path = tmp_path / "recovery" / "migration.json"
    mapping = migration.prepare_mapping(roots, mapping_path)

    assert migration.apply_mapping(mapping, mapping_path) == 0
    target = Path(roots[0]["target"])
    saved = json.loads(mapping_path.read_text(encoding="utf-8"))
    assert saved["state"] == "applied"
    assert saved["cutover_at_utc"]
    backup = Path(saved["entries"][0]["backup"])
    assert migration.tree_signature(target) == migration.tree_signature(backup)
    assert {path.name for path in target.iterdir()} == {"stats.db", "stats.db-wal", "stats.db-shm", "auth.yaml"}

    assert migration.rollback_mapping(saved, mapping_path) == 0
    assert source.joinpath("stats.db-wal").read_bytes() == b"committed wal"
    assert source.joinpath("auth.yaml").read_text(encoding="utf-8") == "secret: preserved\n"
    assert not target.exists()
    assert json.loads(mapping_path.read_text(encoding="utf-8"))["state"] == "rolled_back"


def test_apply_refuses_conflicting_data_roots_without_moving_either(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    roots = _roots(tmp_path, count=1)
    source = Path(roots[0]["source"])
    target = Path(roots[0]["target"])
    source.mkdir()
    target.mkdir()
    (source / "old.yaml").write_text("old\n", encoding="utf-8")
    (target / "new.yaml").write_text("new\n", encoding="utf-8")
    mapping_path = tmp_path / "recovery" / "migration.json"
    mapping = migration.prepare_mapping(roots, mapping_path)

    assert migration.apply_mapping(mapping, mapping_path) == 2
    assert "conflicts require manual resolution" in capsys.readouterr().err
    assert (source / "old.yaml").exists()
    assert (target / "new.yaml").exists()
    assert not mapping_path.exists()


def test_dangling_destination_symlink_is_a_conflict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "old"
    target = tmp_path / "new"
    target.symlink_to(tmp_path / "missing-target")
    roots = [{"label": "config", "source": str(source), "target": str(target)}]
    mapping_path = tmp_path / "recovery" / "migration.json"
    mapping = migration.prepare_mapping(roots, mapping_path)

    assert migration.apply_mapping(mapping, mapping_path) == 2
    assert "conflicts require manual resolution" in capsys.readouterr().err

    assert target.is_symlink()
    assert not source.exists()
    assert not mapping_path.exists()


def test_partial_apply_keeps_recovery_mapping_and_can_roll_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path, count=2)
    for root in roots:
        source = Path(root["source"])
        source.mkdir()
        (source / "settings.yaml").write_text(root["label"], encoding="utf-8")
    mapping_path = tmp_path / "recovery" / "migration.json"
    mapping = migration.prepare_mapping(roots, mapping_path)
    original_rename = migration.os.rename
    failed = False

    def fail_second_move(source: str | Path, target: str | Path) -> None:
        nonlocal failed
        if Path(source) == Path(roots[1]["source"]) and not failed:
            failed = True
            raise OSError("simulated second-root move failure")
        original_rename(source, target)

    monkeypatch.setattr(migration.os, "rename", fail_second_move)
    assert migration.apply_mapping(mapping, mapping_path) == 1
    monkeypatch.setattr(migration.os, "rename", original_rename)
    saved = json.loads(mapping_path.read_text(encoding="utf-8"))
    assert saved["state"] == "partial_failure"
    assert Path(roots[0]["target"]).exists()
    assert Path(roots[1]["source"]).exists()

    assert migration.rollback_mapping(saved, mapping_path) == 0
    assert (Path(roots[0]["source"]) / "settings.yaml").read_text(encoding="utf-8") == "root-0"
    assert not Path(roots[0]["target"]).exists()


def test_repeat_apply_is_idempotent_after_completed_mapping(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    roots = _roots(tmp_path, count=1)
    source = Path(roots[0]["source"])
    source.mkdir()
    (source / "settings.yaml").write_text("kept\n", encoding="utf-8")
    mapping_path = tmp_path / "recovery" / "migration.json"
    mapping = migration.prepare_mapping(roots, mapping_path)
    assert migration.apply_mapping(mapping, mapping_path) == 0
    capsys.readouterr()

    result = migration.main(["apply", "--writers-stopped", "--home", str(tmp_path), "--mapping", str(mapping_path)])

    assert result == 0
    assert "already applied" in capsys.readouterr().out
    assert (Path(roots[0]["target"]) / "settings.yaml").read_text(encoding="utf-8") == "kept\n"


def test_rollback_refuses_destination_modified_after_apply(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    roots = _roots(tmp_path, count=1)
    source = Path(roots[0]["source"])
    source.mkdir()
    (source / "settings.yaml").write_text("original\n", encoding="utf-8")
    mapping_path = tmp_path / "recovery" / "migration.json"
    mapping = migration.prepare_mapping(roots, mapping_path)
    assert migration.apply_mapping(mapping, mapping_path) == 0
    destination = Path(roots[0]["target"])
    (destination / "settings.yaml").write_text("changed after cutover\n", encoding="utf-8")
    saved = json.loads(mapping_path.read_text(encoding="utf-8"))

    assert migration.rollback_mapping(saved, mapping_path) == 1
    assert "destination changed since migration" in capsys.readouterr().err
    assert not source.exists()
    assert (destination / "settings.yaml").read_text(encoding="utf-8") == "changed after cutover\n"


def test_rollback_writer_census_uses_recovery_mapping_roots_despite_environment_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / ".local" / "state" / "yolomux"
    target = tmp_path / ".local" / "state" / "yomux"
    source.mkdir(parents=True)
    mapping_path = tmp_path / "recovery" / "migration.json"
    mapping_path.parent.mkdir()
    mapping_path.write_text(
        json.dumps({"migration_id": "fixture", "state": "applied", "entries": [{
            "label": "state", "source": str(source), "target": str(target), "state": "moved",
        }]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("YOMUX_STATE_DIR", str(target))
    monkeypatch.setenv("YOMUX_ROOT", str(target))
    captured: dict[str, object] = {}

    def census(*, roots, home):
        captured.update(roots=roots, home=home)
        return [{"pid": 123, "command": "yomux-go (YOmux writer)"}]

    monkeypatch.setattr(migration, "active_writer_processes", census)

    result = migration.main([
        "rollback", "--writers-stopped", "--home", str(tmp_path), "--mapping", str(mapping_path),
    ])

    assert result == 3
    assert captured["roots"] == [{"label": "state", "source": str(source), "target": str(target)}]
    assert "writers are still running" in capsys.readouterr().err


def test_rollback_rejects_mapping_inside_a_root_hidden_by_current_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "old-state"
    target = tmp_path / "new-state"
    target.mkdir()
    mapping_path = target / "migration.json"
    mapping_path.write_text(json.dumps({"migration_id": "fixture", "state": "applied", "entries": [{
        "label": "state", "source": str(source), "target": str(target), "state": "moved",
    }]}), encoding="utf-8")
    monkeypatch.setenv("YOMUX_ROOT", str(target))
    monkeypatch.setattr(
        migration,
        "active_writer_processes",
        lambda **_kwargs: pytest.fail("external recovery mapping must be checked before the writer census"),
    )

    result = migration.main([
        "rollback", "--writers-stopped", "--home", str(tmp_path), "--mapping", str(mapping_path),
    ])

    assert result == 2
    assert "recovery data must be outside migrated roots" in capsys.readouterr().err


def test_symlinked_source_root_is_refused_before_apply(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    real_source = tmp_path / "real-source"
    real_source.mkdir()
    (real_source / "settings.yaml").write_text("preserved\n", encoding="utf-8")
    source = tmp_path / "old-state"
    source.symlink_to(real_source, target_is_directory=True)
    roots = [{"label": "state", "source": str(source), "target": str(tmp_path / "new-state")}]
    mapping_path = tmp_path / "recovery" / "migration.json"
    mapping = migration.prepare_mapping(roots, mapping_path)

    result = migration.apply_mapping(mapping, mapping_path)
    error = capsys.readouterr().err

    assert result == 2
    assert "unsafe source roots" in error
    assert '"state": "source_symlink"' in error
    assert not mapping_path.exists()
    assert source.is_symlink()
    assert (real_source / "settings.yaml").read_text(encoding="utf-8") == "preserved\n"
    assert not Path(roots[0]["target"]).exists()
