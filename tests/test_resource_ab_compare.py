"""Contract tests for the post-change statsd resource A/B harness.

Each test pins a way the harness could quietly produce a confident wrong number. The fixtures are
synthetic on purpose: a test that reads the live baseline would change its verdict every time the
recorder appended a line.
"""

import json
import math
import subprocess
import sys

import pytest

from tools.measurement import resource_ab_compare as ab


def _sample(epoch, *, write=0, cpu=0.0, frames=0, wal=1000, txn=0, db=1000, rss_kb=1000,
            epoch_index=0, commits=0, pt=""):
    return {
        "epoch": epoch, "io_cum_write_bytes": write, "statsd_cpu_seconds_cum": cpu,
        "wal_cum_frames": frames, "wal_bytes": wal, "io_cum_syscw": 0, "io_cum_syscr": 0,
        "wal_cum_commits": commits, "change_counter": txn, "db_bytes": db, "mem_Rss_kb": rss_kb,
        "subject_epoch_index": epoch_index, "pt": pt or f"T{epoch}",
    }


def _series(count, *, start=0.0, step=1.0, rate=100.0, level=1000):
    return [
        _sample(start + index * step, write=int(index * step * rate), txn=index,
                frames=index, wal=level, db=level, rss_kb=level, commits=index)
        for index in range(count)
    ]


def test_a_cumulative_counter_that_went_backwards_is_refused_not_averaged():
    """The respawn trap. `/proc/<pid>/io` restarts at zero, and a mean would absorb the negative."""

    block = [_sample(0.0, write=1_000_000), _sample(10.0, write=5_000)]
    metric = ab.FAST_LANE_METRICS[0]
    with pytest.raises(ValueError, match="went backwards"):
        ab.block_value(metric, block)


def test_every_rate_metric_reads_the_respawn_corrected_series():
    """A metric reading the raw per-process counter would be silently wrong across a respawn."""

    raw_fields = {"io_write_bytes", "statsd_cpu_seconds", "io_syscw", "io_syscr", "wal_frames"}
    for metric in ab.FAST_LANE_METRICS:
        if metric.kind == ab.CUMULATIVE:
            assert metric.field not in raw_fields, metric.name
            assert metric.field.startswith(("io_cum_", "statsd_cpu_seconds_cum", "wal_cum_")) or (
                metric.field == "change_counter"
            ), metric.name


def test_a_partial_trailing_block_is_dropped_rather_than_kept_short():
    """A short block's rate is not exchangeable with a full one and would bias the variance."""

    window = ab.Window("w", 0.0, 250.0)
    grouped = ab.blocks(_series(251), window, 100.0)
    assert len(grouped) == 2
    for block in grouped:
        assert block[-1]["epoch"] - block[0]["epoch"] >= 99.0


def test_a_p_value_is_refused_rather_than_approximated_below_the_degrees_of_freedom_floor():
    """Under 30 df the normal approximation is not trustworthy, so no number is printed."""

    result = ab.compare_series(ab.FAST_LANE_METRICS[0], [1.0, 2.0, 3.0, 4.0], [9.0, 10.0, 11.0, 12.0])
    assert result.p_value is None
    assert "REFUSED" in result.note
    assert result.significant is False


def test_a_significant_p_below_the_minimum_detectable_effect_is_not_called_a_change():
    """The shape of a result that does not replicate. Both conditions must hold."""

    baseline = [10.0 + (index % 7) for index in range(60)]
    candidate = [10.0 + (index % 7) for index in range(60)]
    candidate[0] += 1e-9
    result = ab.compare_series(ab.FAST_LANE_METRICS[0], baseline, candidate)
    assert result.significant is False
    assert result.minimum_detectable_effect > abs(result.difference)


def test_identical_arms_report_no_change_and_a_stated_resolution():
    values = [100.0 + math.sin(index) for index in range(80)]
    result = ab.compare_series(ab.FAST_LANE_METRICS[0], values, list(values))
    assert result.difference == pytest.approx(0.0, abs=1e-9)
    assert result.significant is False
    assert result.minimum_detectable_effect > 0.0
    assert result.minimum_detectable_percent is not None


def test_a_monotone_series_is_called_drifting_even_when_its_range_is_small():
    """A database that only grows moves under the range gate yet differs between any two halves."""

    window = ab.Window("w", 0.0, 800.0)
    samples = [_sample(float(index), db=1000 + index, write=index * 100, txn=index, frames=index)
               for index in range(801)]
    verdict = ab.stationarity(samples, window)["verdict"]
    assert verdict["db_bytes"]["verdict"] == "drifting"
    assert verdict["db_bytes"]["direction"] == "rising"


def test_a_flat_noisy_series_is_called_stationary():
    window = ab.Window("w", 0.0, 800.0)
    samples = [_sample(float(index), db=1000 + (index % 3), write=index * 100, txn=index, frames=index)
               for index in range(801)]
    verdict = ab.stationarity(samples, window)["verdict"]
    assert verdict["db_bytes"]["verdict"] == "stationary"


def _service_vacuum(instant, previous=0.0):
    return {"kind": "vacuum_detected", "detector": "service_timestamp", "epoch": instant + 40.0,
            "last_vacuumed_at": instant, "last_vacuumed_at_pt": f"T{instant}",
            "previous_last_vacuumed_at": previous}


def _byte_step_vacuum(start, end, before=596_357_120, after=537_182_208, cost=1e9):
    return {"kind": "vacuum_detected", "detector": "byte_step", "start_epoch": start,
            "end_epoch": end, "start_pt": "s", "end_pt": "e", "db_bytes_before": before,
            "db_bytes_after": after, "write_bytes_cost": cost, "wal_reset_during": 1}


def test_compactions_are_counted_by_service_timestamp_never_by_byte_step():
    """`byte_step` re-reported one rewrite five times, so counting on it understates ~3x."""

    events = [_byte_step_vacuum(100.0 + offset, 160.0 + offset) for offset in (0.0, 2.0, 4.0, 6.0, 8.0)]
    events.append(_service_vacuum(120.0))
    window = ab.Window("w", 0.0, 400.0)
    assert len(ab.completed_vacuums(events, window)) == 1
    result = ab.vacuum_write_bytes(_series(400), events, window)
    assert result["events"] == 1
    assert result["byte_step_detections"] == 5
    assert result["counting_detector"] == "service_timestamp"


def test_two_completed_compactions_are_counted_separately():
    events = [_service_vacuum(100.0), _service_vacuum(300.0, previous=100.0)]
    assert len(ab.completed_vacuums(events, ab.Window("w", 0.0, 400.0))) == 2


def test_last_vacuumed_at_is_carried_so_a_reader_can_tell_which_limb_a_measurement_sits_on():
    """Requirement (4) of the A/B contract."""

    entries = ab.completed_vacuums([_service_vacuum(1787719861.0)], ab.Window("w", 0.0, 2e9))
    assert entries[0]["last_vacuumed_at"] == 1787719861.0
    assert "last_vacuumed_at_pt" in entries[0]


def test_vacuum_cost_is_burst_bounded_and_not_clipped_by_a_fixed_bracket():
    """A fixed [t-30, t+3] bracket reports 2.020x post-size where the truth is ~3.008x.

    The fixture is a 6 s burst of 300 MB against a 1 kB/s background, deliberately wider than a
    3-second tail, so a fixed bracket would miss most of it.
    """

    samples, total = [], 0
    for index in range(600):
        epoch = float(index)
        rate = 50_000_000 if 300 <= index < 306 else 1_000
        total += rate
        samples.append(_sample(epoch, write=total, commits=index, db=100_000_000))
    result = ab.vacuum_write_bytes(samples, [_service_vacuum(303.0)], ab.Window("w", 0.0, 600.0))
    assert result["events"] == 1
    entry = result["vacuums"][0]
    assert entry["write_bytes"] is not None
    # The burst carries 6 x 50 MB; anything much under that means the tail was clipped.
    assert entry["write_bytes"] >= 250_000_000, entry
    assert entry["burst_seconds"] <= 30.0, entry


def test_a_compaction_with_no_burst_above_background_is_reported_not_guessed():
    samples = [_sample(float(i), write=i * 1000, commits=i) for i in range(600)]
    result = ab.vacuum_write_bytes(samples, [_service_vacuum(300.0)], ab.Window("w", 0.0, 600.0))
    assert result["vacuums"][0]["write_bytes"] is None
    assert "no burst" in result["vacuums"][0]["reason"]


def test_arms_at_different_phases_of_their_compaction_cycle_are_refused():
    """Requirement (1), restated: nothing settles, so two phases measure two different points.

    This is the comparison that would have reported a false improvement -- and under the retired
    100-minute rule the late arm would have been called "settled" and accepted.
    """

    vacuums = ab.completed_vacuums(
        [_service_vacuum(0.0), _service_vacuum(7600.0, previous=0.0),
         _service_vacuum(15200.0, previous=7600.0)], ab.Window("w", -10.0, 1e9))
    early = ab.Window("candidate", 15_260.0, 16_000.0)     # 1 minute into a cycle
    late = ab.Window("baseline", 21_000.0, 22_000.0)       # ~76 minutes into one
    verdict = ab.vacuum_alignment(vacuums, late, vacuums, early)
    assert verdict["aligned"] is False
    assert "not phase-matched" in verdict["problems"][0]
    assert verdict["baseline_phase"] is not None and verdict["candidate_phase"] is not None


def test_arms_at_the_same_phase_are_accepted():
    vacuums = ab.completed_vacuums(
        [_service_vacuum(0.0), _service_vacuum(7600.0, previous=0.0),
         _service_vacuum(15200.0, previous=7600.0)], ab.Window("w", -10.0, 1e9))
    verdict = ab.vacuum_alignment(
        vacuums, ab.Window("baseline", 7600.0 + 1800.0, 10_000.0),
        vacuums, ab.Window("candidate", 15200.0 + 1900.0, 17500.0))
    assert verdict["aligned"] is True, verdict["problems"]
    assert verdict["problems"] == []


def test_phase_is_computed_against_a_MEASURED_cycle_not_a_constant():
    vacuums = ab.completed_vacuums(
        [_service_vacuum(0.0), _service_vacuum(7600.0, previous=0.0)], ab.Window("w", -10.0, 1e9))
    assert ab.compaction_cycle_seconds(vacuums) == 7600.0


def test_an_unmeasurable_cycle_refuses_rather_than_assuming_one():
    """Fewer than two completed compactions means the cycle is unknown, and unknown fails closed."""

    one = ab.completed_vacuums([_service_vacuum(0.0)], ab.Window("w", -10.0, 1e9))
    assert ab.compaction_cycle_seconds(one) is None
    verdict = ab.vacuum_alignment(one, ab.Window("b", 100.0, 200.0), one, ab.Window("c", 100.0, 200.0))
    assert verdict["aligned"] is False
    assert any("cycle length is not measurable" in p for p in verdict["problems"])


def test_phase_distance_is_circular_so_end_of_cycle_matches_start_of_the_next():
    """0.98 and 0.02 are 0.04 apart, not 0.96. Treating it linearly refuses a matched pair."""

    vacuums = ab.completed_vacuums(
        [_service_vacuum(0.0), _service_vacuum(1000.0, previous=0.0),
         _service_vacuum(2000.0, previous=1000.0)], ab.Window("w", -10.0, 1e9))
    verdict = ab.vacuum_alignment(
        vacuums, ab.Window("baseline", 2000.0 + 980.0, 3100.0),
        vacuums, ab.Window("candidate", 2000.0 + 1020.0, 3200.0))
    assert verdict["aligned"] is True, verdict["problems"]


def test_a_window_must_have_positive_duration():
    with pytest.raises(ValueError, match="window end must follow its start"):
        ab.Window("w", 10.0, 10.0)


def test_reading_a_jsonl_file_with_a_bad_interior_line_fails_rather_than_skipping_it(tmp_path):
    path = tmp_path / "raw.jsonl"
    path.write_text('{"a": 1}\nnot json\n{"a": 2}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2 is not valid JSON"):
        ab.read_jsonl(path)


def test_a_stationary_synthetic_window_passes_its_own_half_split_self_test():
    """The end-to-end contract: one condition compared against itself reports no false positive."""

    samples = [
        _sample(float(index), write=index * 100, cpu=index * 0.1, frames=index * 5,
                wal=1000 + (index % 11), txn=index, db=1000 + (index % 11),
                rss_kb=1000 + (index % 11))
        for index in range(7200)
    ]
    lane = ab.Lane("synthetic", tuple(samples), (), (), ())
    result = ab.self_test(lane, ab.Window("w", 0.0, 7199.0), 60.0)
    assert result["self_test"]["false_positives"] == []
    assert result["self_test"]["passed"] is True


def test_a_numerically_constant_metric_is_not_called_changed_by_floating_point_noise():
    """The defect the end-to-end self-test caught, pinned directly.

    A constant rate recomputed per block does not give zero variance: it gives about 1e-32,
    which yields a minimum detectable effect near 1e-16 and a p-value of 0.0006 on a difference
    of 3.5e-16. Without the noise floor this reported an exactly-flat CPU series as CHANGED.
    """

    baseline = [0.10000000000000023] * 59
    candidate = [0.10000000000000057] * 59
    result = ab.compare_series(ab.FAST_LANE_METRICS[1], baseline, candidate)
    assert abs(result.difference) > 0.0, "the fixture must actually differ in its last bits"
    assert result.significant is False
    assert result.p_value == 1.0
    assert "noise floor" in result.note


def test_a_real_difference_well_above_the_noise_floor_is_still_detected():
    """The floor must not become a way to miss a genuine change."""

    baseline = [100.0 + (index % 5) for index in range(60)]
    candidate = [140.0 + (index % 5) for index in range(60)]
    result = ab.compare_series(ab.FAST_LANE_METRICS[0], baseline, candidate)
    assert result.significant is True
    assert result.p_value is not None and result.p_value < 0.05
    assert abs(result.difference) >= result.minimum_detectable_effect


def test_pooled_blocks_never_straddle_an_excluded_recovery_gap():
    """Blocking across the gap would average recovery samples back in, undoing requirement (3)."""

    samples = [_sample(float(i), write=i * 100, commits=i) for i in range(2000)]
    spans = (ab.Window("a", 0.0, 300.0), ab.Window("b", 1000.0, 1300.0))
    grouped = ab.pooled_blocks(samples, spans, 100.0)
    assert len(grouped) == 6
    for block in grouped:
        low, high = float(block[0]["epoch"]), float(block[-1]["epoch"])
        assert any(w.start_epoch <= low and high <= w.end_epoch for w in spans), (low, high)


def test_pooling_recovers_the_block_count_a_single_steady_span_cannot_reach():
    """One 27-minute steady span is five blocks at 300 s; three of them is fifteen."""

    samples = [_sample(float(i), write=i * 100, commits=i) for i in range(20000)]
    one = ab.Window("one", 0.0, 1630.0)
    three = (one, ab.Window("two", 8000.0, 9630.0), ab.Window("three", 16000.0, 17630.0))
    assert len(ab.blocks(ab.samples_in(samples, one), one, 300.0)) == 5
    assert len(ab.pooled_blocks(samples, three, 300.0)) == 15


def test_a_window_with_too_few_blocks_is_refused_with_a_reason_not_raised():
    """"Cannot compare" is an outcome with a reason. Raising buried a true, useful answer.

    With `--steady-state-only` on the recorded baseline the 21:51 compaction's recovery swallows
    the whole first half, leaving it zero blocks. That is worth reporting, not tracebacking.
    """

    samples = [_sample(float(i), write=i * 100, commits=i) for i in range(1200)]
    lane = ab.Lane("short", tuple(samples), (), (), ())
    result = ab.compare_lanes(lane, ab.Window("a", 0.0, 100.0), lane, ab.Window("b", 200.0, 1100.0), 300.0)
    assert "refused" in result
    assert "not enough blocks" in result["refused"]
    assert result["comparisons"] == []
    assert result["significant_metrics"] == []


# --- E3-RECOVERY-118: nothing settles, so acceptance cannot rest on a settling constant ---------

def _cycle(rate, commits_rate, *, count=60, start=0.0, step=10.0):
    """One matched-phase arm: a constant block-write rate and a constant commit rate."""
    samples, written, committed = [], 0, 0
    for index in range(count):
        samples.append(_sample(start + index * step, write=int(written), commits=int(committed)))
        written += rate * step
        committed += commits_rate * step
    return samples


def test_the_three_cycle_counterexample_is_not_called_a_change():
    """`E3-RECOVERY-118`: cycles A and B agree within 2.2%; C sits 17.7-18% below on
    `block_write_bytes` while `wal_cum_commits` holds to 0.1%. That is cycle-to-cycle variation on
    an UNCHANGED subject, so the harness must not report it as a change.

    `block_write_bytes` has a demonstrated ~25% reproducibility floor. An 18% difference is below
    it and is therefore not distinguishable from the next cycle, whatever the t-test says.
    """

    cycle_a = ab.Lane("A", tuple(_cycle(700_000.0, 2.20)), (), (), ())
    cycle_c = ab.Lane("C", tuple(_cycle(700_000.0 * 0.822, 2.2022)), (), (), ())
    window = ab.Window("cycle", 0.0, 590.0)
    result = ab.compare_lanes(cycle_a, window, cycle_c, window, 60.0)
    rows = {row["metric"]: row for row in result["comparisons"]}

    assert abs(rows["block_write_bytes"]["percent_change"]) > 17.0, rows["block_write_bytes"]
    assert rows["block_write_bytes"]["significant"] is False, (
        "an 17.7-18% block_write_bytes difference is inside its ~25% reproducibility floor and "
        "must not be reported as a change"
    )
    assert abs(rows["commits"]["percent_change"]) < 0.5, rows["commits"]
    assert "block_write_bytes" not in result["significant_metrics"]


def test_a_metric_with_no_established_reproducibility_floor_refuses_rather_than_assuming():
    """Fail closed. An unknown floor is not a zero floor."""

    for name in ("main_file_change_counter", "statsd_rss_bytes", "read_syscalls"):
        assert ab.REPRODUCIBILITY_FLOOR[name] is None, name
    lane_a = ab.Lane("a", tuple(_cycle(700_000.0, 2.2)), (), (), ())
    lane_b = ab.Lane("b", tuple(_cycle(1_400_000.0, 2.2)), (), (), ())
    window = ab.Window("w", 0.0, 590.0)
    result = ab.compare_lanes(lane_a, window, lane_b, window, 60.0)
    rows = {row["metric"]: row for row in result["comparisons"]}
    for name in ("main_file_change_counter", "statsd_rss_bytes", "read_syscalls"):
        assert rows[name]["significant"] is False, name
        assert "no established reproducibility floor" in rows[name]["note"], rows[name]["note"]


def test_a_difference_above_the_floor_is_still_detected():
    """The floor must not become a way to miss a real change. 100% is far above 25%."""

    lane_a = ab.Lane("a", tuple(_cycle(700_000.0, 2.2)), (), (), ())
    lane_b = ab.Lane("b", tuple(_cycle(1_400_000.0, 4.4)), (), (), ())
    window = ab.Window("w", 0.0, 590.0)
    result = ab.compare_lanes(lane_a, window, lane_b, window, 60.0)
    rows = {row["metric"]: row for row in result["comparisons"]}
    assert rows["block_write_bytes"]["significant"] is True, rows["block_write_bytes"]
    assert rows["commits"]["significant"] is True, rows["commits"]


def test_wal_cum_commits_is_the_reproducible_comparison_owner():
    """0.1% cycle-to-cycle, an order of magnitude tighter than anything else measured."""

    assert ab.REPRODUCIBILITY_FLOOR["commits"] == 0.001
    assert min(v for v in ab.REPRODUCIBILITY_FLOOR.values() if v is not None) == 0.001
    commits = next(m for m in ab.FAST_LANE_METRICS if m.name == "commits")
    assert commits.field == "wal_cum_commits"


def test_the_fixed_settling_constant_is_gone():
    """Nothing settles inside a cycle, so no constant can mark the end of settling."""

    assert not hasattr(ab, "VACUUM_RECOVERY_SECONDS")
    assert not hasattr(ab, "steady_state_windows")


def test_the_command_line_runs_end_to_end_against_a_recorded_lane(tmp_path):
    """Exercises `main()`, which no unit test reached.

    The phase-matching rewrite left the CLI printing a key the report no longer carries, and every
    unit test still passed because they all call `compare_lanes` directly. A harness whose entry
    point crashes is not usable at 04:00, whatever its internals do.
    """

    raw = tmp_path / "raw"
    raw.mkdir()
    samples = [
        _sample(float(index), write=index * 700_000, commits=index * 2, cpu=index * 0.1,
                frames=index * 5, wal=1000 + (index % 7), db=1_000_000 + index,
                rss_kb=1000 + (index % 7), pt=f"T{index}")
        for index in range(1200)
    ]
    (raw / "fast5.jsonl").write_text("".join(json.dumps(s) + "\n" for s in samples), encoding="utf-8")
    for name in ("fast5.events.jsonl", "vacuum_events.jsonl", "acquisitions.jsonl"):
        (raw / name).write_text("", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, ab.__file__, "--baseline-dir", str(tmp_path),
         "--baseline-start", "0.0", "--baseline-end", "1199.0",
         "--self-test", "--block-seconds", "60"],
        capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stderr, completed.stderr
    assert "phase" in completed.stdout
    assert "commits" in completed.stdout
