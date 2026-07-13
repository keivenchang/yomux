import json
from pathlib import Path

import pytest

from tools import rebuild_stats_tokens as rebuild_cli
from yolomux_lib import stats_rebuild
from yolomux_lib.local_services import stats_store


class _OfflineCatalog:
    def status(self):
        return {"state": "offline", "catalog_revision": 0}

    def resolve_rate(self, **_kwargs):
        return None

    def estimate_rate_band(self, **_kwargs):
        return None


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def _codex_transcript(path: Path, *, thread_id: str, parent_thread_id: str = "", timestamp: float = 1000.5) -> Path:
    source = {"subagent": {"thread_spawn": {"parent_thread_id": parent_thread_id}}} if parent_thread_id else "cli"
    return _write_jsonl(path, [
        {"type": "session_meta", "payload": {"id": thread_id, "source": source}},
        {"type": "turn_context", "payload": {"model": "gpt-test", "effort": "high"}},
        {"timestamp": timestamp, "payload": {"info": {"total_token_usage": {
            "input_tokens": 40, "cached_input_tokens": 10, "output_tokens": 7,
        }}}},
    ])


def _database_fixture(path: Path) -> None:
    store = stats_store.StatsStore(path)
    first = stats_store.empty_bucket(1000, 1)
    first.update({"sequence": 1, "server_sequence": 1, "cpu_total_percent": 12, "cpu_count": 1})
    first["clients"] = {"browser-a": {**stats_store.empty_client_bucket(), "sequence": 1, "api_count": 3}}
    first["agent_token_rates"] = {
        "agent-a": {"label": "Agent A", "tokens": 7, "total": 7, "samples": 1, "seconds": 1, "model_rates": {"gpt-test": {"tokens": 7}}}
    }
    first["cost_summary"] = {"components": [{
        "event_id": "stale", "timestamp": 999, "provider": "openai", "model": "old",
        "direction": "output", "modality": "text", "cache_role": "none", "unit": "tokens",
        "quantity": 999, "micro_usd": 0, "priced": False,
    }]}
    second = stats_store.empty_bucket(1100, 1)
    second.update({"sequence": 2, "server_sequence": 2, "tokens_per_agent_total": 5, "agent_token_samples": 1})
    store.replace_buckets([first, second])
    store.close()


def test_rebuild_cli_help_documents_offline_safety_backup_and_live_metric_loss():
    help_text = rebuild_cli.parser().format_help()

    assert "offline-only" in help_text
    assert "timestamped SQLite backup" in help_text
    assert "--stop-services" in help_text
    assert "--include-live" in help_text
    assert "cannot restore" in help_text


def test_rebuild_safety_reports_server_owner_socket_and_database_openers(tmp_path, monkeypatch):
    database = tmp_path / "stats-history.sqlite3"
    database.touch()
    socket_path = tmp_path / "services" / "statsd.sock"
    socket_path.parent.mkdir()
    socket_path.touch()
    monkeypatch.setattr(stats_rebuild, "live_server_processes", lambda *_args, **_kwargs: [stats_rebuild.ProcessRecord(11, "yolomux-server", "test")])
    monkeypatch.setattr(stats_rebuild, "live_statsd_processes", lambda *_args, **_kwargs: [stats_rebuild.ProcessRecord(12, "statsd", "test")])
    monkeypatch.setattr(stats_rebuild, "fresh_owner_processes", lambda *_args, **_kwargs: [stats_rebuild.ProcessRecord(11, "background-owner", "test")])
    monkeypatch.setattr(stats_rebuild, "database_openers", lambda *_args, **_kwargs: {12, 13})

    blockers = stats_rebuild.safety_blockers(database, socket_path)

    assert any("live YOLOmux" in blocker and "11" in blocker for blocker in blockers)
    assert any("live statsd" in blocker and "12" in blocker for blocker in blockers)
    assert any("fresh background owner" in blocker for blocker in blockers)
    assert any("database opener" in blocker and "13" in blocker for blocker in blockers)
    assert any("socket still exists" in blocker for blocker in blockers)
    with pytest.raises(stats_rebuild.StatsRebuildSafetyError, match="offline rebuild refused"):
        stats_rebuild.rebuild_stats_tokens(database, socket_path=socket_path, dry_run=True)


def test_macos_stop_disables_each_recorded_per_port_launch_job(tmp_path, monkeypatch):
    owner = tmp_path / "owner.json"
    owner.write_text(json.dumps({"port": 8881}), encoding="utf-8")
    calls = []

    def fake_run(args, **kwargs):
        if args and args[0] == "launchctl":
            calls.append((args, kwargs))
        return stats_rebuild.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(stats_rebuild.sys, "platform", "darwin")
    monkeypatch.setattr(stats_rebuild.shutil, "which", lambda command: "/bin/launchctl" if command == "launchctl" else None)
    monkeypatch.setattr(stats_rebuild.subprocess, "run", fake_run)

    stats_rebuild._disable_macos_launch_jobs([
        stats_rebuild.ProcessRecord(10, "yolomux-server", str(owner)),
        stats_rebuild.ProcessRecord(11, "yolomux-server", str(owner)),
        stats_rebuild.ProcessRecord(12, "statsd", "ignored"),
    ])

    assert [call[0] for call in calls] == [["launchctl", "bootout", f"gui/{stats_rebuild.os.getuid()}/local.yolomux.8881"]]


def test_discover_transcript_families_includes_claude_subagents_and_codex_descendants(tmp_path):
    claude_root = tmp_path / ".claude"
    claude = _write_jsonl(claude_root / "projects" / "repo" / "root.jsonl", [])
    claude_child = _write_jsonl(claude.with_suffix("") / "subagents" / "child.jsonl", [])
    codex_root = tmp_path / ".codex" / "sessions"
    codex = _codex_transcript(codex_root / "2026" / "07" / "12" / "rollout-root.jsonl", thread_id="root")
    codex_child = _codex_transcript(
        codex_root / "2026" / "07" / "12" / "rollout-child.jsonl", thread_id="child", parent_thread_id="root",
    )

    families = stats_rebuild.discover_transcript_families(claude_root=claude_root, codex_root=codex_root)

    assert [(family.kind, family.root, family.paths) for family in families] == [
        ("claude", claude.resolve(), (claude.resolve(), claude_child.resolve())),
        ("codex", codex.resolve(), (codex.resolve(), codex_child.resolve())),
    ]


def test_offline_rebuild_backs_up_replaces_components_and_preserves_output_and_live_metrics(tmp_path):
    database = tmp_path / "state" / "stats-history.sqlite3"
    database.parent.mkdir()
    _database_fixture(database)
    codex_root = tmp_path / ".codex" / "sessions"
    _codex_transcript(codex_root / "2026" / "07" / "12" / "rollout-root.jsonl", thread_id="root")
    claude_root = tmp_path / ".claude"

    dry_run = stats_rebuild.rebuild_stats_tokens(
        database, claude_root=claude_root, codex_root=codex_root, now=1200, dry_run=True,
    )
    report = stats_rebuild.rebuild_stats_tokens(
        database, claude_root=claude_root, codex_root=codex_root, now=1200, dry_run=False, catalog=_OfflineCatalog(),
    )

    assert dry_run.dry_run is True and dry_run.backup == ""
    assert report.dry_run is False
    assert report.families == 1 and report.transcript_files == 1
    assert report.atoms == 3 and report.duplicate_atoms == 0
    assert report.billable_lt_output_buckets == 0
    assert report.output_only_buckets == 1
    assert report.live_metric_buckets_preserved == 1
    backup = Path(report.backup)
    assert backup.is_file() and backup.stat().st_size > 0

    store = stats_store.StatsStore(database)
    rebuilt = store.bucket(1000, 1)
    missing = store.bucket(1100, 1)
    assert rebuilt["cpu_total_percent"] == 12
    assert rebuilt["clients"]["browser-a"]["api_count"] == 3
    assert rebuilt["agent_token_rates"]["agent-a"]["tokens"] == 7
    components = rebuilt["cost_summary"]["components"]
    assert "stale" not in {item["event_id"] for item in components}
    assert {(item["direction"], item["cache_role"], item["quantity"]) for item in components} == {
        ("input", "none", 30.0), ("input", "read", 10.0), ("output", "none", 7.0),
    }
    assert missing["tokens_per_agent_total"] == 5
    assert missing["cost_summary"]["components"] == []
    assert missing["cost_summary"]["lower_bound"] is True
    first_components = json.dumps(components, sort_keys=True)
    store.close()

    rerun = stats_rebuild.rebuild_stats_tokens(
        database, claude_root=claude_root, codex_root=codex_root, now=1200, dry_run=False, catalog=_OfflineCatalog(),
    )
    store = stats_store.StatsStore(database)
    assert json.dumps(store.bucket(1000, 1)["cost_summary"]["components"], sort_keys=True) == first_components
    assert Path(rerun.backup).is_file() and rerun.backup != report.backup
    store.close()


def test_include_live_erases_unreconstructible_metrics_but_keeps_output_scalar(tmp_path):
    database = tmp_path / "state" / "stats-history.sqlite3"
    database.parent.mkdir()
    _database_fixture(database)

    report = stats_rebuild.rebuild_stats_tokens(
        database,
        claude_root=tmp_path / "empty-claude",
        codex_root=tmp_path / "empty-codex",
        now=1200,
        dry_run=False,
        include_live=True,
        catalog=_OfflineCatalog(),
    )

    store = stats_store.StatsStore(database)
    bucket = store.bucket(1000, 1)
    assert report.live_metric_buckets_preserved == 0
    assert bucket["cpu_total_percent"] == 0
    assert bucket["clients"] == {}
    assert bucket["servers"] == {}
    assert bucket["agent_token_rates"]["agent-a"]["tokens"] == 7
    store.close()
