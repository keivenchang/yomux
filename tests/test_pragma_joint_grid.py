"""Contract tests for the joint `cache_size` / `mmap_size` grid.

Each pins a way the grid could report a confident wrong pragma choice. The decision-rule tests
use synthetic point records so the arithmetic is exercised without a production-sized store, which
the freeze forbids; the mechanics tests use a real SQLite file built in `tmp_path`.
"""

import json
import sqlite3
import subprocess
import sys

import pytest

from tools.measurement import pragma_joint_grid as grid


def _point(cache, mmap_size, syscr, *, anonymous=0, mapped=0, write_bytes=0, note="",
           effective_mmap=None):
    return {
        "arm": "read", "requested_cache_size": cache, "requested_mmap_size": mmap_size,
        "effective": {"cache_size": cache,
                      "mmap_size": mmap_size if effective_mmap is None else effective_mmap},
        "io_delta": {"syscr": syscr, "syscw": 0, "read_bytes": 0, "write_bytes": write_bytes},
        "memory_peak": {"anonymous": anonymous, "mapped": mapped},
        "memory_steady": {"anonymous": anonymous, "mapped": mapped},
        "workload_units": 1, "wall_seconds": 0.0, "monotonic_start": 0.0, "monotonic_stop": 0.0,
        "ring_published": True, "note": note,
    }


def _store(tmp_path, rows=200):
    path = tmp_path / "stats-v9.sqlite3"
    connection = sqlite3.connect(str(path), isolation_level=None)
    connection.execute(
        "CREATE TABLE observations(event_id TEXT PRIMARY KEY, family TEXT, source_id TEXT, "
        "observed_at REAL, epoch_id TEXT, owner_generation INTEGER, payload_json TEXT)")
    connection.executemany(
        "INSERT INTO observations VALUES(?, ?, ?, ?, ?, ?, ?)",
        [(f"e{i}", "cpu", "s0", float(i), "ep", 0, json.dumps({"v": i})) for i in range(rows)])
    connection.close()
    return path


# --- the skip contract ------------------------------------------------------------------------

def test_a_missing_store_is_a_named_skip_never_a_silent_pass_or_a_default():
    """A grid that quietly measured something else would return a winner that means nothing."""

    completed = subprocess.run(
        [sys.executable, grid.__file__], capture_output=True, text=True, check=False)
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["skipped"] is True
    assert "production-sized" in payload["reason"]
    assert "winner" in payload["reason"]


def test_a_store_path_that_does_not_exist_is_also_a_named_skip(tmp_path):
    completed = subprocess.run(
        [sys.executable, grid.__file__, "--store", str(tmp_path / "absent.sqlite3")],
        capture_output=True, text=True, check=False)
    payload = json.loads(completed.stdout)
    assert payload["skipped"] is True
    assert "does not exist" in payload["reason"]


# --- the mechanics that silently lie ------------------------------------------------------------

def test_the_effective_mmap_size_is_read_back_not_the_requested_one(tmp_path):
    """SQLite clamps `mmap_size` and may refuse it; recording the request invents a result."""

    store = _store(tmp_path)
    huge = 1 << 62
    result = grid.run_point(store, "read", -2000, huge, settle_seconds=0.0, rows=0, batches=0,
                            publish_ring=False)
    assert result.effective["mmap_size"] != huge
    assert result.effective["mmap_size"] <= 0x7fff0000
    assert "clamped" in result.note or "refused" in result.note


def test_an_mmap_arm_that_never_engaged_is_flagged_and_excluded_from_the_decision():
    points = [
        _point(-2000, 0, 500, anonymous=0),
        _point(-2000, 268435456, 400, effective_mmap=0,
               note="mmap_size was requested but reads back as 0: the build or VFS refused it, "
                    "so this arm did NOT exercise mmap and must not be reported as an mmap result"),
        _point(-16000, 0, 100, anonymous=1_000_000),
    ]
    decision = grid.decide(points, [])
    assert len(decision["refused_mmap_points"]) == 1
    assert decision["smallest_mmap_that_flattens"] is None


def test_memory_is_reported_as_two_limbs_and_they_are_never_summed(tmp_path):
    """`RssAnon + VmSwap` is blind to `mmap_size`; a single number hides the whole mmap axis."""

    store = _store(tmp_path)
    result = grid.run_point(store, "read", -2000, 0, settle_seconds=0.0, rows=0, batches=0,
                            publish_ring=False)
    for sample in (result.memory_peak, result.memory_steady):
        assert sample["anonymous"] == sample["RssAnon"] + sample["VmSwap"]
        assert sample["mapped"] == sample["RssFile"]
        assert "total" not in sample and "combined" not in sample


def test_memory_is_sampled_at_peak_and_again_at_steady(tmp_path):
    store = _store(tmp_path)
    result = grid.run_point(store, "read", -2000, 0, settle_seconds=0.05, rows=0, batches=0,
                            publish_ring=False)
    assert result.memory_peak and result.memory_steady
    assert set(result.memory_peak) == set(result.memory_steady)


def test_the_page_cache_is_dropped_before_a_read_point(tmp_path, monkeypatch):
    """Without this every point after the first measures a warm cache and the arm reads as flat."""

    store = _store(tmp_path)
    dropped = []
    monkeypatch.setattr(grid, "drop_page_cache", lambda path: dropped.append(path))
    grid.run_point(store, "read", -2000, 0, settle_seconds=0.0, rows=0, batches=0,
                   publish_ring=False)
    assert dropped == [store]


def test_a_write_point_does_not_drop_the_page_cache(tmp_path, monkeypatch):
    """The write arm measures writes; evicting the cache would measure a cold read it never does."""

    store = _store(tmp_path)
    dropped = []
    monkeypatch.setattr(grid, "drop_page_cache", lambda path: dropped.append(path))
    grid.run_point(store, "write", -2000, 0, settle_seconds=0.0, rows=10, batches=2,
                   publish_ring=False)
    assert dropped == []


def test_a_store_with_no_published_ring_says_so_and_prices_the_understatement(tmp_path):
    """Appends that intersect no published slot understate the cost by about 46%."""

    store = _store(tmp_path)
    result = grid.run_point(store, "write", -2000, 0, settle_seconds=0.0, rows=10, batches=2,
                            publish_ring=True)
    assert result.ring_published is False
    assert "46%" in result.note


def test_each_grid_point_runs_in_a_fresh_subprocess(tmp_path):
    """`VmHWM` is a process-lifetime high-water mark and does not reset."""

    store = _store(tmp_path)
    first = grid.spawn_point(store, "read", -2000, 0, settle_seconds=0.0, rows=0, batches=0)
    second = grid.spawn_point(store, "read", -2000, 0, settle_seconds=0.0, rows=0, batches=0)
    assert first["memory_peak"]["VmHWM"] > 0 and second["memory_peak"]["VmHWM"] > 0
    # Two points in one process would make the second's HWM at least the first's plus its own
    # workload; fresh processes make them independent samples of the same shape.
    assert abs(first["memory_peak"]["VmHWM"] - second["memory_peak"]["VmHWM"]) < first["memory_peak"]["VmHWM"]


# --- the falsification that gates the campaign --------------------------------------------------

def test_the_write_collapse_holds_when_write_bytes_ignores_mmap_size(monkeypatch, tmp_path):
    monkeypatch.setattr(grid, "copy_store", lambda source, destination: 0)
    monkeypatch.setattr(grid, "spawn_point", lambda *a, **k: _point(-2000, 0, 0, write_bytes=16_887_808))
    verdict = grid.falsify_write_collapse(tmp_path / "s", tmp_path, settle_seconds=0.0, rows=1, batches=1)
    assert verdict["collapse_holds"] is True
    assert "1-D" in verdict["verdict"]


def test_the_campaign_stops_when_the_write_collapse_is_falsified(monkeypatch, tmp_path):
    """It changes the campaign's size fourfold, so it must run first and stop the rest."""

    sizes = iter([16_887_808, 25_000_000])
    monkeypatch.setattr(grid, "copy_store", lambda source, destination: 0)
    monkeypatch.setattr(grid, "spawn_point",
                        lambda *a, **k: _point(-2000, 0, 0, write_bytes=next(sizes)))
    verdict = grid.falsify_write_collapse(tmp_path / "s", tmp_path, settle_seconds=0.0, rows=1, batches=1)
    assert verdict["collapse_holds"] is False
    assert "COLLAPSE VOID" in verdict["verdict"]
    assert verdict["relative_difference"] > grid.FALSIFICATION_TOLERANCE


# --- the decision rule, step by step ------------------------------------------------------------

def test_step_one_chooses_the_smallest_value_that_flattens_not_the_best_one():
    """"Smallest that flattens" is the queue's own wording made numeric at 1.05 x best."""

    points = [
        _point(-2000, 0, 1000),
        _point(-2000, 67108864, 102),      # within 1.05 x 100 -> flattens
        _point(-2000, 268435456, 100),     # the best, but larger
        _point(-2000, 629145600, 100),
    ]
    decision = grid.decide(points, [])
    assert decision["smallest_mmap_that_flattens"]["effective"]["mmap_size"] == 67108864


def test_step_three_prefers_mmap_when_both_axes_flatten():
    """Anonymous memory must be swapped and the daemon already holds 978.4 MiB there."""

    points = [
        _point(-2000, 0, 1000),
        _point(-2000, 67108864, 100),
        _point(-16000, 0, 100, anonymous=1_000_000),
    ]
    decision = grid.decide(points, [])
    assert decision["choice"]["mmap_size"] == 67108864
    assert decision["choice"]["cache_size"] == grid.CACHE_SIZE_CONTROL
    assert "droppable at zero IO" in decision["choice"]["reason"]


def test_step_two_refuses_a_cache_point_that_breaches_the_anonymous_budget():
    """64 MiB over control, because there is no anonymous headroom left to spend."""

    over = grid.ANONYMOUS_BUDGET_BYTES + 1
    points = [
        _point(-2000, 0, 1000, anonymous=0),
        _point(-262144, 0, 100, anonymous=over),
    ]
    decision = grid.decide(points, [])
    assert decision["cache_within_anonymous_budget"] is False
    assert decision["choice"]["cache_size"] == grid.CACHE_SIZE_CONTROL
    assert decision["choice"]["mmap_size"] == grid.MMAP_SIZE_CONTROL
    assert "defaults stand" in decision["choice"]["reason"]


def test_a_cache_point_inside_the_budget_is_taken_when_mmap_cannot_flatten():
    points = [
        _point(-2000, 0, 1000, anonymous=0),
        _point(-2000, 67108864, 900),                       # mmap does not reach the threshold
        _point(-65536, 0, 100, anonymous=1_000_000),        # cache does, and is affordable
    ]
    decision = grid.decide(points, [])
    assert decision["choice"]["cache_size"] == -65536
    assert decision["choice"]["mmap_size"] == grid.MMAP_SIZE_CONTROL
    assert "price of the cache_size increase" in decision["choice"]["reason"]


def test_step_four_rejects_a_cache_point_that_raises_write_bytes():
    """A candidate that reports only one side is not acceptable -- the queue's words."""

    reads = [_point(-2000, 0, 1000), _point(-65536, 0, 100, anonymous=1000)]
    writes = [
        {"requested_cache_size": -2000, "io_delta": {"write_bytes": 1_000_000}},
        {"requested_cache_size": -65536, "io_delta": {"write_bytes": 1_500_000}},
    ]
    decision = grid.decide(reads, writes)
    assert decision["rejected_on_write_regression"] == [
        {"cache_size": -65536, "write_bytes": 1_500_000, "control_write_bytes": 1_000_000}]


def test_step_five_publishes_the_losing_arms_beside_the_winner():
    points = [_point(-2000, 0, 1000), _point(-2000, 67108864, 100), _point(-16000, 0, 900)]
    decision = grid.decide(points, [])
    assert len(decision["losing_arms"]) >= 2


def test_a_grid_with_no_usable_read_point_refuses_rather_than_choosing():
    refused = _point(-2000, 268435456, 100, effective_mmap=0,
                     note="mmap_size was requested but reads back as 0")
    with pytest.raises(grid.GridError, match="nothing can be decided"):
        grid.decide([refused], [])


def test_a_grid_missing_its_control_arm_refuses_rather_than_comparing_to_nothing():
    points = [_point(-16000, 67108864, 100)]
    with pytest.raises(grid.GridError, match="control arm is missing"):
        grid.decide(points, [])


def test_the_grid_bounds_are_the_shipped_defaults_and_the_whole_file():
    """The control arms must be what ships, or the grid has no baseline."""

    assert grid.CACHE_SIZE_POINTS[0] == grid.CACHE_SIZE_CONTROL == -2000
    assert grid.MMAP_SIZE_POINTS[0] == grid.MMAP_SIZE_CONTROL == 0
    assert grid.CACHE_SIZE_POINTS[-1] == -582740          # the whole 569.1 MiB database, in KiB
    assert grid.MMAP_SIZE_POINTS[-1] == 629145600         # 600 MiB, covering it with headroom
    assert len(grid.CACHE_SIZE_POINTS) * len(grid.MMAP_SIZE_POINTS) == 20
