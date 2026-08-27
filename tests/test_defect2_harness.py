# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The Defect 2 experiment harness, proven on synthetic failures before a real one arrives.

The recurring failure of this release has been experiments whose evidence could not be
re-derived afterwards. So the extractor is exercised against constructed occurrences here: an
extractor first met during a real 1-in-163 event is an extractor nobody can trust.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import defect2_harness as harness


REPO_ROOT = Path(__file__).resolve().parents[1]


def _evidence(*, stream_evidence=None, server_records=(), stall_ts=None):
    """One attempt's retained evidence, in the exact shape the gate artifacts carry."""

    stall = {
        "type": "stats_history",
        "level": "warning",
        "category": "stats_stream",
        "route": "/api/stats-stream",
        "deliveryOutcome": "stalled",
        "signature": "jsf-synthetic",
        "message": harness.STALL_PREDICATE,
        "ts": stall_ts or STALL_TS,
    }
    if stream_evidence is not None:
        stall["streamEvidence"] = stream_evidence
    return {
        "browser_failures": [stall],
        "server_logs": {"logs": list(server_records)},
    }


# One realistic episode: the watchdog fires four seconds after the last accepted frame, which is
# just past its three-second budget. Every timestamp below is derived from these two so a fixture
# can never describe a window the product could not produce.
STALL_TS = "2026-08-25T20:00:10.000Z"
STALL_EPOCH = harness._epoch_seconds(STALL_TS)
LAST_ARRIVAL_EPOCH = STALL_EPOCH - 4.0


def _stream_evidence(**overrides):
    base = {
        "running": True, "visible": True, "healthy": True, "streamOpen": True,
        "streamEpoch": 2, "deliverySequence": 11, "acceptedDeltaSequence": 9,
        "lastDeliveryKind": "ready", "lastDeliveryAtMs": int(LAST_ARRIVAL_EPOCH * 1000),
        "lastDeliveryEmitMs": 4000, "lastDeliveryEpoch": 2, "rangeSeconds": 300,
        "resolutionSeconds": 1, "sourceGeneration": 77, "cacheGeneration": 78, "deltaRevision": 5,
    }
    base.update(overrides)
    return base


def _server_record(event, boundary, at, **detail):
    return {
        "id": 1, "level": "info", "source": "stats-stream", "category": "stats_stream",
        "event": event, "route": "/api/stats-stream", "timestamp": at,
        "message": json.dumps({"boundary": boundary, "cadence_seconds": 1.0, **detail}, sort_keys=True),
    }


# ---------------------------------------------------------------------------
# The extractor, on constructed occurrences
# ---------------------------------------------------------------------------


def test_a_slow_statsd_rpc_inside_the_window_is_named_as_the_first_bad_boundary():
    timeline = harness.first_transition_timeline(_evidence(
        stream_evidence=_stream_evidence(),
        server_records=[
            # Before the window: must not be mistaken for the cause of this silence.
            _server_record("rpc-slow", "statsd_delta_rpc", LAST_ARRIVAL_EPOCH - 30, rpc_seconds=9.0, status=304),
            _server_record("rpc-slow", "statsd_delta_rpc", LAST_ARRIVAL_EPOCH + 2, rpc_seconds=7.5, status=304),
        ],
    ))

    assert timeline["predicate_fired"] is True
    assert timeline["classifiable"] is True, timeline
    assert timeline["first_bad_boundary"] == "statsd_delta_rpc", timeline
    events = [item["event"] for item in timeline["timeline"]]
    assert events == ["last-accepted-frame", "rpc-slow", "stall-reported"], timeline
    assert [item["order"] for item in timeline["timeline"]] == [0, 1, 2]
    assert timeline["missing_evidence"] == [], timeline


def test_a_late_emit_loop_is_separated_from_a_slow_rpc():
    timeline = harness.first_transition_timeline(_evidence(
        stream_evidence=_stream_evidence(),
        server_records=[_server_record("tick-late", "frame_production", LAST_ARRIVAL_EPOCH + 3, slip_seconds=4.2)],
    ))

    assert timeline["first_bad_boundary"] == "frame_production", timeline
    assert timeline["classifiable"] is True, timeline


def test_a_closed_stream_reads_as_client_side_rather_than_upstream_silence():
    """A rejected frame tears the stream down here; it is never upstream silence.

    This is the case the message string alone could never separate, and it is why the whole
    evidence snapshot rather than one counter is attached to the failure.
    """

    timeline = harness.first_transition_timeline(_evidence(
        stream_evidence=_stream_evidence(streamOpen=False, streamEpoch=7),
        server_records=[_server_record("tick-late", "frame_production", LAST_ARRIVAL_EPOCH + 2, slip_seconds=4.0)],
    ))

    assert timeline["first_bad_boundary"] == "client_rejection_or_transport_error", timeline
    assert "streamOpen was false" in " ".join(timeline["reasons"]), timeline


def test_a_quiet_server_with_a_live_stream_reads_as_transport():
    """The server recorded no anomaly, so it believed it was emitting while nothing arrived."""

    timeline = harness.first_transition_timeline(_evidence(
        stream_evidence=_stream_evidence(),
        server_records=[_server_record("status-change", "delta_stream_status", LAST_ARRIVAL_EPOCH - 60, status=202)],
    ))

    assert timeline["first_bad_boundary"] == "transport_or_connection_closed", timeline


def test_missing_evidence_is_reported_as_unclassifiable_rather_than_guessed():
    """An extractor that guesses is worse than none, because the guess is what gets quoted."""

    no_snapshot = harness.first_transition_timeline(_evidence(stream_evidence=None, server_records=[]))
    assert no_snapshot["classifiable"] is False, no_snapshot
    assert any("streamEvidence" in item for item in no_snapshot["missing_evidence"]), no_snapshot
    assert any("server log ring" in item for item in no_snapshot["missing_evidence"]), no_snapshot

    no_emit_clock = harness.first_transition_timeline(_evidence(
        stream_evidence=_stream_evidence(lastDeliveryEmitMs=0),
        server_records=[_server_record("rpc-slow", "statsd_delta_rpc", LAST_ARRIVAL_EPOCH + 2, rpc_seconds=8.0, status=304)],
    ))
    assert no_emit_clock["classifiable"] is False, no_emit_clock
    assert any("emit timestamp" in item for item in no_emit_clock["missing_evidence"]), no_emit_clock


def test_a_cold_start_pending_window_is_not_counted_as_this_defect():
    """A sibling defect fires `pending` for ~0.9 s on 6 of 6 cold starts. It is not Defect 2.

    `cache_ready_event` fires before the served window's ring is flushed, so a snapshot requested
    at the readiness instant is legitimately refused with `pending` and `retry_after_seconds: 1`.
    A post-settle `pending` frame routes through `routeStreamFailure`, which closes the stream --
    so a statsd restart mid-attempt leaves `streamOpen` false and would otherwise be read as a
    browser-side rejection. The server's own 202 is the discriminator and it outranks that.
    """

    restarted = harness.first_transition_timeline(_evidence(
        stream_evidence=_stream_evidence(streamOpen=False, streamEpoch=9),
        server_records=[
            _server_record("status-change", "delta_stream_status", LAST_ARRIVAL_EPOCH + 1, status=202),
            _server_record("tick-late", "frame_production", LAST_ARRIVAL_EPOCH + 3, slip_seconds=4.0),
        ],
    ))
    assert restarted["first_bad_boundary"] == "server_pending_restart_window", restarted
    assert restarted["is_defect_2"] is False, restarted
    assert any("refreshing" in reason for reason in restarted["reasons"]), restarted

    # Without the 202 the same shape is a genuine client-side close, and stays one.
    genuine = harness.first_transition_timeline(_evidence(
        stream_evidence=_stream_evidence(streamOpen=False, streamEpoch=9),
        server_records=[_server_record("tick-late", "frame_production", LAST_ARRIVAL_EPOCH + 3, slip_seconds=4.0)],
    ))
    assert genuine["first_bad_boundary"] == "client_rejection_or_transport_error", genuine
    assert genuine["is_defect_2"] is True, genuine


def test_every_classified_occurrence_states_whether_it_is_defect_2():
    """The predicate alone is the scope; a verdict must never be quoted without that flag."""

    for records, expected in (
        ([_server_record("rpc-slow", "statsd_delta_rpc", LAST_ARRIVAL_EPOCH + 2, rpc_seconds=8.0, status=304)], True),
        ([_server_record("status-change", "delta_stream_status", LAST_ARRIVAL_EPOCH + 1, status=202)], False),
    ):
        timeline = harness.first_transition_timeline(_evidence(
            stream_evidence=_stream_evidence(), server_records=records))
        assert timeline["is_defect_2"] is expected, timeline


def test_a_window_whose_bounds_disagree_is_refused_instead_of_read_as_transport():
    """The failure mode I hit while demonstrating the extractor, turned into a guard.

    With an arrival time and a stall time that are not one episode, every server record falls
    outside the window, the in-window set is empty, and the verdict reads as a confident
    "transport" -- a wrong answer stated with full confidence. Refuse the window instead.
    """

    too_short = harness.first_transition_timeline(_evidence(
        stream_evidence=_stream_evidence(lastDeliveryAtMs=int((STALL_EPOCH - 0.5) * 1000)),
        server_records=[_server_record("rpc-slow", "statsd_delta_rpc", STALL_EPOCH - 0.2, rpc_seconds=8.0, status=304)],
    ))
    assert too_short["classifiable"] is False, too_short
    assert any("implausible silence window" in item for item in too_short["missing_evidence"]), too_short

    mismatched_clocks = harness.first_transition_timeline(_evidence(
        stream_evidence=_stream_evidence(lastDeliveryAtMs=1_000_000_000_000),
    ))
    assert mismatched_clocks["classifiable"] is False, mismatched_clocks
    assert any("do not agree" in item for item in mismatched_clocks["missing_evidence"]), mismatched_clocks


def test_a_passing_attempt_is_not_forced_into_a_classification():
    timeline = harness.first_transition_timeline({"browser_failures": [], "server_logs": {"logs": []}})
    assert timeline["predicate_fired"] is False
    assert timeline["first_bad_boundary"] == "not_applicable"


def test_the_retired_axis_mismatch_is_not_treated_as_this_defect():
    """Only the exact stall predicate is in scope; the retired cpuAxisMax failure is excluded."""

    other = {
        "browser_failures": [{"message": "YO!stats cpuAxisMax == 100 mismatch", "ts": "2026-08-25T20:00:10.000Z"}],
        "server_logs": {"logs": []},
    }
    assert harness.first_transition_timeline(other)["predicate_fired"] is False


# ---------------------------------------------------------------------------
# Arm equality, as failures rather than prose
# ---------------------------------------------------------------------------


def _attempt(arm, attempt_id, **overrides):
    record = {
        "schema": harness.ATTEMPT_SCHEMA,
        "attempt_id": attempt_id,
        "arm": arm,
        "subject": {"node_id": harness.SUBJECT_NODE_ID, "lane": harness.SUBJECT_LANE},
        "tree": {
            "head_sha": "8adc6108157f86e93b593906dae6eb0095925805",
            "start_clean_state": {"observable": True, "clean": True, "tracked": [], "untracked": []},
            "end_clean_state": {"observable": True, "clean": True, "tracked": [], "untracked": []},
        },
        "artifacts": {"generated_bundle_hashes": {"yolomux.js": "4dda432e", "yolomux.css": "c0", "emoji-data.js": "e0"}},
        "container": {"tag": "yolomux-test:2fe10ac2d641", "image_id": "sha256:aaa", "image_present": True},
        "arm_env": {"name": "YOLOMUX_STATS_PERSISTENCE_OWNER", "forwarded": True, "observed_by_subject": True},
        "statsd": {"source_shape": [{"source": "storage.py", "source_sha256": f"sha-{arm}"}]},
        "workers": {"counts": dict(harness.ENVELOPE_WORKERS), "mode": "parallel"},
    }
    for key, value in overrides.items():
        record[key] = value
    return record


ARM_ENV = "YOLOMUX_STATS_PERSISTENCE_OWNER"


def test_artifact_equal_arms_pass_and_a_differing_bundle_fails():
    """The historical pair that looked like one defect were the same SHA with different bundles.

    Same commit `8adc6108...`, same 21 dirty paths, `yolomux.js` differing `4dda432e` vs
    `3247b973`, and `84_stats_current.js` -- the file that owns the watchdog -- dirty in both.
    Source-equal is not artifact-equal, and only the bundle triple catches it.
    """

    clean = [_attempt("batched", "a1"), _attempt("per_append", "b1")]
    assert harness.arm_equality_violations(clean, arm_env_name=ARM_ENV) == []

    drifted = [_attempt("batched", "a1"), _attempt("per_append", "b1")]
    drifted[1]["artifacts"]["generated_bundle_hashes"]["yolomux.js"] = "3247b973"
    violations = harness.arm_equality_violations(drifted, arm_env_name=ARM_ENV)
    assert any("generated_bundle_hashes differ" in item for item in violations), violations


def test_a_dirty_checkout_or_a_rebuilt_container_fails_the_comparison():
    dirty = [_attempt("batched", "a1"), _attempt("per_append", "b1")]
    dirty[0]["tree"]["start_clean_state"] = {"observable": True, "clean": False, "tracked": ["static/yolomux.js"], "untracked": []}
    assert any("dirty checkout" in item for item in harness.arm_equality_violations(dirty, arm_env_name=ARM_ENV))

    rebuilt = [_attempt("batched", "a1"), _attempt("per_append", "b1")]
    rebuilt[1]["container"]["image_id"] = "sha256:bbb"
    violations = harness.arm_equality_violations(rebuilt, arm_env_name=ARM_ENV)
    assert any("container image id differs" in item for item in violations), violations


def test_an_arm_variable_the_subject_never_saw_fails_the_attempt():
    """The most expensive way this experiment can lie is to run both arms as one and report green."""

    unseen = [_attempt("batched", "a1"), _attempt("per_append", "b1")]
    unseen[1]["arm_env"]["observed_by_subject"] = False
    violations = harness.arm_equality_violations(unseen, arm_env_name=ARM_ENV)
    assert any("never observed by the subject" in item for item in violations), violations

    unforwarded = [_attempt("batched", "a1"), _attempt("per_append", "b1")]
    unforwarded[0]["arm_env"]["forwarded"] = False
    assert any("not forwarded into the test container" in item for item in harness.arm_equality_violations(unforwarded, arm_env_name=ARM_ENV))


def test_one_arm_alone_and_a_confounded_arm_both_fail():
    assert any("only one arm present" in item for item in harness.arm_equality_violations(
        [_attempt("batched", "a1"), _attempt("batched", "a2")], arm_env_name=ARM_ENV))

    confounded = [_attempt("batched", "a1"), _attempt("batched", "a2"), _attempt("per_append", "b1")]
    confounded[1]["statsd"]["source_shape"] = [{"source": "storage.py", "source_sha256": "sha-other"}]
    violations = harness.arm_equality_violations(confounded, arm_env_name=ARM_ENV)
    assert any("did not hold one statsd source identity" in item for item in violations), violations


def test_the_envelope_is_the_full_parallel_gate_and_the_e2e_lane():
    """A narrower envelope has already failed to reproduce this, and the browser lane is the wrong knob."""

    assert harness.envelope_violations(_attempt("batched", "a1")) == []

    wrong_lane = _attempt("batched", "a1")
    wrong_lane["subject"]["lane"] = "pytest-browser"
    assert any("subject lane is not pytest-e2e" in item for item in harness.envelope_violations(wrong_lane))

    narrowed = _attempt("batched", "a1")
    narrowed["workers"] = {"counts": {"browser": "1", "e2e": "1", "nonbrowser": "1"}, "mode": "serial"}
    violations = harness.envelope_violations(narrowed)
    assert any("are not the envelope" in item for item in violations), violations
    assert any("is not 'parallel'" in item for item in violations), violations


# ---------------------------------------------------------------------------
# Admission, identity, retention
# ---------------------------------------------------------------------------


def test_an_arm_variable_missing_from_the_container_allowlist_is_refused_before_any_attempt():
    """docker/run-tests.sh forwards a fixed allowlist and a missing name skips silently.

    The node is skipped, the run reports green, and both arms have run identical code. Refusing
    up front is the only way that failure becomes visible.
    """

    allowlist = harness.forwarded_test_env()
    assert "YOLOMUX_WORKTREE_WRITER_TOKEN" in allowlist, allowlist

    refused = harness.arm_env_admission("YOLOMUX_DEFECT2_NOT_FORWARDED")
    assert refused["admitted"] is False
    assert refused["reason"] == "arm_env_not_forwarded_into_container"

    assert harness.arm_env_admission("")["reason"] == "no_arm_env_name"
    assert harness.arm_env_admission(allowlist[0])["admitted"] is True


SERVICE_SOURCE_WITH_OWNER = '''
APPEND_FLUSH_SECONDS = 10.0
APPEND_FLUSH_ENV_NAME = "YOLOMUX_STATS_APPEND_FLUSH_SECONDS"
'''


def test_the_arm_variable_name_is_read_from_its_owner_not_restated():
    """One owner for the name, the way the certification allowlist reads its own constant.

    A literal restated here would keep passing after the owner renamed it, which is precisely
    how an arm variable stops reaching the subject without anything going red.
    """

    assert harness.arm_env_name(SERVICE_SOURCE_WITH_OWNER) == "YOLOMUX_STATS_APPEND_FLUSH_SECONDS"
    with pytest.raises(ValueError):
        harness.arm_env_name("APPEND_FLUSH_SECONDS = 10.0\n")


def test_neither_arm_may_leave_the_variable_unset():
    """Unset resolves to the 10.0 default, which is the treatment arm.

    So an unset control is indistinguishable from a control whose variable never arrived: both
    run the treatment owner while the record says control. Both arms therefore state an explicit
    value, and the harness refuses a plan where either does not.
    """

    assert harness.ARMS == {"control_synchronous": "0", "treatment_batched": "10.0"}
    assert harness.arm_plan_violations(harness.ARMS) == []
    assert any("leaves the variable unset" in item for item in harness.arm_plan_violations(
        {"control_synchronous": "0", "treatment_batched": ""}))
    assert any("only one arm" in item for item in harness.arm_plan_violations({"control_synchronous": "0"}))
    assert any("same value" in item for item in harness.arm_plan_violations(
        {"control_synchronous": "0", "treatment_batched": "0"}))


def test_statsd_source_identity_names_every_file_statsd_respawns_from():
    """statsd respawns from disk and no served response distinguishes patched from unpatched."""

    records = harness.statsd_source_identity()
    assert [record["source"] for record in records] == [
        "yolomux_lib/stats_current/storage.py",
        "yolomux_lib/stats_current/service.py",
    ], records
    for record in records:
        assert record["present"] is True, record
        assert record["source_bytes"] > 0 and len(record["source_sha256"]) == 64, record
        for compiled in record["pyc"]:
            assert set(compiled) == {"path", "bytes", "mtime_ns", "stale_against_source"}, compiled


def test_worker_assignment_pins_in_worker_predecessors_not_file_order(tmp_path):
    """Predecessor identity is a property of xdist sharding, because one Chrome is leased per worker."""

    directory = harness.enable_attribution(tmp_path)
    (directory / "worker-gw0.jsonl").write_text("\n".join(json.dumps(row) for row in [
        {"nodeid": "tests/a.py::first", "worker": "gw0", "outcome": "passed", "start": 100.0},
        {"nodeid": harness.SUBJECT_NODE_ID, "worker": "gw0", "outcome": "failed", "start": 102.0},
    ]) + "\n", encoding="utf-8")
    (directory / "worker-gw1.jsonl").write_text(
        json.dumps({"nodeid": "tests/z.py::other", "worker": "gw1", "outcome": "passed", "start": 101.0}) + "\n",
        encoding="utf-8",
    )

    rows = harness.worker_assignment(tmp_path)
    assert [row["nodeid"] for row in rows] == ["tests/a.py::first", harness.SUBJECT_NODE_ID, "tests/z.py::other"]
    assert [row["worker_order"] for row in rows] == [0, 1, 0]
    # `tests/z.py::other` ran between them in wall time but on another worker, so it shares no
    # browser with the subject and is not a predecessor.
    assert harness.predecessors_of(rows, harness.SUBJECT_NODE_ID) == ["tests/a.py::first"]
    assert harness.worker_assignment(tmp_path / "absent") == []


def test_a_leftover_serial_record_never_doubles_a_sharded_run(tmp_path):
    """Caught by smoke-running the hook under real xdist, not by reading it.

    Under xdist the controller receives every worker's report as well, so the hook first wrote a
    `worker-master.jsonl` holding a second copy of every row with no worker attribution. Reading
    both shapes doubled the assignment and would have made every predecessor list wrong.
    """

    directory = harness.enable_attribution(tmp_path)
    rows = [
        {"nodeid": "tests/a.py::first", "worker": "gw0", "outcome": "passed", "start": 100.0},
        {"nodeid": harness.SUBJECT_NODE_ID, "worker": "gw1", "outcome": "failed", "start": 101.0},
    ]
    (directory / "worker-gw0.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    (directory / "worker-gw1.jsonl").write_text(json.dumps(rows[1]) + "\n", encoding="utf-8")
    (directory / "worker-master.jsonl").write_text(
        "".join(json.dumps({**row, "worker": "master"}) + "\n" for row in rows), encoding="utf-8"
    )

    assignment = harness.worker_assignment(tmp_path)
    assert len(assignment) == 2, assignment
    assert sorted(row["worker"] for row in assignment) == ["gw0", "gw1"], assignment
    # A genuinely serial run has only the master file and must still be read.
    serial = harness.enable_attribution(tmp_path / "serial")
    (serial / "worker-master.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    assert [row["worker"] for row in harness.worker_assignment(tmp_path / "serial")] == ["gw0"]


def _conftest_module():
    """The already-imported tests/conftest.py, found rather than re-imported.

    Importing it a second time would re-run its module body, minting fresh config/state temp dirs
    and rebinding this process's environment underneath the running suite.
    """

    wanted = str(REPO_ROOT / "tests" / "conftest.py")
    for module in list(sys.modules.values()):
        if getattr(module, "__file__", None) == wanted:
            return module
    raise AssertionError("tests/conftest.py is not loaded")


def test_the_conftest_hook_never_writes_from_the_xdist_controller(tmp_path, monkeypatch):
    """The controller sees every report with `node` set; only the process that ran it may record."""

    conftest = _conftest_module()
    monkeypatch.setenv("YOLOMUX_E2E_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    harness.enable_attribution(tmp_path)

    ran_here = SimpleNamespace(when="call", outcome="passed", nodeid="tests/a.py::x", duration=0.5, start=100.0)
    forwarded = SimpleNamespace(
        when="call", outcome="failed", nodeid="tests/a.py::y", duration=0.5, start=101.0,
        node=SimpleNamespace(gateway="gw3"),
    )
    conftest.pytest_runtest_logreport(ran_here)
    conftest.pytest_runtest_logreport(forwarded)

    rows = harness.worker_assignment(tmp_path)
    assert [row["nodeid"] for row in rows] == ["tests/a.py::x"], rows

    # A passing setup phase is noise; a failing one is the only record that test will ever produce.
    conftest.pytest_runtest_logreport(SimpleNamespace(when="setup", outcome="passed", nodeid="tests/a.py::z", duration=0.0, start=102.0))
    conftest.pytest_runtest_logreport(SimpleNamespace(when="setup", outcome="error", nodeid="tests/a.py::w", duration=0.0, start=103.0))
    assert [row["nodeid"] for row in harness.worker_assignment(tmp_path)] == ["tests/a.py::x", "tests/a.py::w"]


def test_attribution_never_breaks_the_run_it_observes(tmp_path, monkeypatch):
    """A lost record is acceptable; a test that fails because of its own instrumentation is not."""

    conftest = _conftest_module()
    monkeypatch.setenv("YOLOMUX_E2E_EVIDENCE_DIR", str(tmp_path))
    directory = harness.enable_attribution(tmp_path)
    # Replace the writable file with a directory so the append raises OSError.
    (directory / "worker-master.jsonl").mkdir()
    conftest.pytest_runtest_logreport(
        SimpleNamespace(when="call", outcome="passed", nodeid="tests/a.py::x", duration=0.1, start=100.0)
    )

    monkeypatch.delenv("YOLOMUX_E2E_EVIDENCE_DIR", raising=False)
    conftest.pytest_runtest_logreport(
        SimpleNamespace(when="call", outcome="passed", nodeid="tests/a.py::x", duration=0.1, start=100.0)
    )


def test_attribution_is_off_until_the_harness_turns_it_on(tmp_path):
    """An ordinary run must write nothing; the directory's existence is the whole switch."""

    assert not (tmp_path / harness.ATTRIBUTION_DIR_NAME).exists()
    assert harness.worker_assignment(tmp_path) == []
    harness.enable_attribution(tmp_path)
    assert (tmp_path / harness.ATTRIBUTION_DIR_NAME).is_dir()


def test_retention_is_self_describing_and_refuses_to_leave_tmp(tmp_path):
    """Evidence that cannot be re-derived is the recurring failure this layout exists to end."""

    root = tmp_path / "run-1"
    harness.write_attempt(root, _attempt("batched", "a1"))
    harness.write_attempt(root, _attempt("per_append", "b1"))

    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["predicate"] == harness.STALL_PREDICATE
    assert manifest["subject"] == harness.SUBJECT_NODE_ID
    assert manifest["lane"] == harness.SUBJECT_LANE
    assert manifest["envelope"] == {"workers": harness.ENVELOPE_WORKERS, "mode": "parallel"}
    assert manifest["attempts"] == ["batched-a1.json", "per_append-b1.json"]
    assert [record["attempt_id"] for record in harness.read_attempts(root)] == ["a1", "b1"]

    with pytest.raises(ValueError):
        harness.retention_root(str(REPO_ROOT / "not-tmp"))


def test_the_harness_offers_no_rate_design():
    """A rate design is inert at this base rate and must not be reachable by accident.

    Twenty attempts per arm expect 0.2454 events; Fisher's exact on 20-vs-20 needs a 20.4x rate
    ratio before it separates anything, and a plausible 2x needs 1,883 attempts per arm, about
    26.9 days of continuous gate. So nothing here counts or compares failure rates: the arm
    checks answer "are these the same subject", and the extractor answers "which boundary went
    bad first". Neither is a hypothesis test.
    """

    exported = {name for name in dir(harness) if not name.startswith("_")}
    assert not {name for name in exported if "rate" in name.lower()}, exported
    assert not {name for name in exported if "fisher" in name.lower() or "pvalue" in name.lower()}, exported
    assert not {name for name in exported if "significan" in name.lower()}, exported
    # What it does export is identity, admission, extraction and retention -- no inference.
    assert {"arm_equality_violations", "first_transition_timeline", "write_attempt", "arm_env_admission"} <= exported
