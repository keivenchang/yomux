"""DOIT.58 A5 — activity ledger coalescing semantics (these tests DEFINE the contract)."""

import json

from yolomux_lib.activity import ActivityLedger


def _ledger(tmp_path, idle_gap_seconds=120.0, retention_days=14.0):
    return ActivityLedger(
        tmp_path / "activity.json",
        heartbeat_path=tmp_path / "activity-heartbeats.jsonl",
        idle_gap_seconds=idle_gap_seconds,
        retention_days=retention_days,
    )


def test_two_close_heartbeats_count_the_real_gap(tmp_path):
    led = _ledger(tmp_path)
    led.heartbeat("6", "1", ts=1000.0)
    led.heartbeat("6", "1", ts=1005.0)  # 5s later
    assert led.snapshot()["6:1"]["total_user_input_ms"] == 5000


def test_gap_beyond_idle_caps_at_idle_gap(tmp_path):
    led = _ledger(tmp_path, idle_gap_seconds=120.0)
    led.heartbeat("6", "1", ts=1000.0)
    led.heartbeat("6", "1", ts=1000.0 + 600.0)  # 10 min later
    assert led.snapshot()["6:1"]["total_user_input_ms"] == 120000  # idle cap, not 600000


def test_single_heartbeat_is_zero_time_but_records_event(tmp_path):
    led = _ledger(tmp_path)
    led.heartbeat("6", "1", ts=1000.0, byte_count=3)
    rec = led.snapshot()["6:1"]
    assert rec["total_user_input_ms"] == 0
    assert rec["last_user_input_ts"] == 1000.0
    assert rec["input_events"] == 1
    assert rec["input_bytes"] == 3


def test_heartbeat_log_records_share_source_without_keystrokes(tmp_path):
    led = _ledger(tmp_path)
    led.heartbeat("6", "1", ts=1000.0, byte_count=3, source="share")
    line = json.loads((tmp_path / "activity-heartbeats.jsonl").read_text(encoding="utf-8").strip())
    assert line == {"ts": 1000.0, "s": "6", "w": "1", "b": 3, "src": "share"}


def test_per_window_attribution_rolls_up_to_session(tmp_path):
    led = _ledger(tmp_path)
    led.heartbeat("6", "1", ts=1000.0)
    led.heartbeat("6", "2", ts=1004.0)
    snap = led.snapshot()
    assert "6:1" in snap and "6:2" in snap and "6" in snap
    # the session key saw both window heartbeats: 4s between them
    assert snap["6"]["input_events"] == 2
    assert snap["6"]["total_user_input_ms"] == 4000
    # each window saw exactly one heartbeat
    assert snap["6:1"]["input_events"] == 1 and snap["6:2"]["input_events"] == 1


def test_prune_drops_dead_sessions(tmp_path):
    led = _ledger(tmp_path)
    led.heartbeat("6", "1", ts=1000.0)
    led.heartbeat("7", "0", ts=1000.0)
    led.prune({"6"})
    snap = led.snapshot()
    assert "6" in snap and "6:1" in snap
    assert "7" not in snap and "7:0" not in snap


def test_atomic_persistence_round_trip(tmp_path):
    led = _ledger(tmp_path)
    led.heartbeat("6", "1", ts=1000.0)
    led.heartbeat("6", "1", ts=1005.0)
    led.flush()
    reloaded = _ledger(tmp_path)
    reloaded.load()
    assert reloaded.snapshot()["6:1"]["total_user_input_ms"] == 5000


def test_load_corrupt_activity_json_resets_records_and_logs(tmp_path, caplog):
    led = _ledger(tmp_path)
    led.heartbeat("6", "1", ts=1000.0)
    led.path.write_text("{not json", encoding="utf-8")

    led.load()

    assert led.snapshot() == {}
    assert "resetting unreadable activity ledger" in caplog.text


def test_agent_active_coalesces_separately(tmp_path):
    led = _ledger(tmp_path, idle_gap_seconds=120.0)
    led.note_agent_active("6", "1", ts=1000.0)
    led.note_agent_active("6", "1", ts=1003.0)
    led.note_agent_active("6", "1", ts=2000.0)  # gap beyond idle -> capped
    rec = led.snapshot()["6:1"]
    assert rec["agent_active_ms"] == 3000 + 120000
    assert rec["total_user_input_ms"] == 0  # agent activity is not user typing


def test_heartbeat_rotation_drops_old_lines(tmp_path):
    led = _ledger(tmp_path, retention_days=1.0)
    now = 1_000_000.0
    led.heartbeat("6", "1", ts=now - 2 * 86400)  # 2 days old
    led.heartbeat("6", "1", ts=now - 60)         # 1 min old
    kept = led.rotate_heartbeats(now=now)
    assert kept == 1
    lines = [l for l in (tmp_path / "activity-heartbeats.jsonl").read_text().splitlines() if l.strip()]
    assert len(lines) == 1 and json.loads(lines[0])["ts"] == now - 60
