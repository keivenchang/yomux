# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import json
import select
import signal
import subprocess
import sys

import pytest

from tools.verify_go_stats_restart import FixtureServer, StreamProbe, observe_snapshot_publication


def test_sse_snapshot_replaces_buckets_and_delta_removes_tombstones():
    bucket = lambda start: {"start": start, "duration": 10, "series": {
        "model_tokens_per_minute:all:fixture": {"value": 18}}}
    frames = [
        ("snapshot", {"cache_generation": 1, "buckets": [bucket(10), bucket(20)]}),
        ("snapshot", {"cache_generation": 2, "buckets": [bucket(20)]}),
        ("delta", {"base_cache_generation": 2, "cache_generation": 3,
                   "buckets": [bucket(30)], "tombstones": [
                       {"kind": "bucket", "start": 20, "duration": 10},
                       {"kind": "no_data", "start": 20, "end": 30, "family": "agent_status"}]}),
    ]
    probe = StreamProbe.__new__(StreamProbe)
    probe.events, probe.buckets = [], {}
    probe.tokens, probe.last_id = 0, -1
    probe.generation, probe.error = None, None
    probe.ready = False
    probe.response = [line.encode() for identity, (event, payload) in enumerate(frames)
                      for line in (f"event: {event}\n", f"id: {identity}\n",
                                   f"data: {json.dumps(payload)}\n", "\n")]
    probe.read()
    assert probe.error is None
    assert [frame["tokens"] for frame in probe.events] == [6, 3, 3]
    assert list(probe.buckets) == [(30, 10)]


def test_frozen_fixture_cleanup_kills_without_termination_timeout(tmp_path):
    fixture = FixtureServer.__new__(FixtureServer)
    fixture.log = (tmp_path / "fixture.log").open("w")
    fixture.frozen = False
    fixture.process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        fixture.freeze()
        fixture.stop()
        assert fixture.process.returncode == -signal.SIGKILL
        assert fixture.log.closed
    finally:
        if fixture.process.poll() is None:
            fixture.process.kill()
            fixture.process.wait(timeout=5)
        fixture.log.close()


@pytest.mark.parametrize(("status", "payload"), [
    (503, {"error": "authentication temporarily unavailable", "retryable": True}),
    (202, {"status": "pending", "reason": "new product generation"}),
])
def test_nonpublication_response_retains_body_and_last_actual_publication(status, payload):
    published = [(0.0, 0), (1.0, 1), (2.0, 2)]
    first, latest = observe_snapshot_publication(
        200, {"cost_report": {"total_tokens": 9}}, 0.01, 16.0, None, published, 15.0)
    response, retained = observe_snapshot_publication(
        status, payload, 0.02, 16.5, latest, published, 15.0)
    assert first["tokens"] == 9
    assert response == {"at": 16.5, "status": status, "latency": 0.02,
                        "tokens": None, "payload": payload}
    assert retained is latest
    assert retained["tokens"] == 9


@pytest.mark.parametrize("status", [200, 202, 503])
def test_publication_deadline_still_rejects_actual_behind_sample(status):
    published = [(0.0, 0), (1.0, 1), (2.0, 2)]
    _, latest = observe_snapshot_publication(
        200, {"cost_report": {"total_tokens": 6}}, 0.01, 16.0, None, published, 15.0)
    payload = {"cost_report": {"total_tokens": 6}} if status == 200 else {"status": "pending"}
    with pytest.raises(AssertionError, match="live publication exceeded 15s: required 2"):
        observe_snapshot_publication(status, payload, 0.01, 17.0, latest, published, 15.0)


def test_sustained_unavailability_cannot_extend_publication_budget():
    published = [(float(number), number) for number in range(4)]
    _, latest = observe_snapshot_publication(
        200, {"cost_report": {"total_tokens": 9}}, 0.01, 16.0, None, published, 15.0)
    _, retained = observe_snapshot_publication(
        503, {"error": "unavailable"}, 0.01, 17.0, latest, published, 15.0)
    with pytest.raises(AssertionError, match="live publication exceeded 15s: required 3"):
        observe_snapshot_publication(
            503, {"error": "unavailable"}, 0.01, 18.0, retained, published, 15.0)


def test_restart_requires_current_generation_readiness():
    published = [(0.0, 0), (1.0, 1)]
    _, prior_generation = observe_snapshot_publication(
        200, {"cost_report": {"total_tokens": 6}}, 0.01, 16.0, None, published, 15.0)
    assert prior_generation is not None
    # qualify resets the previous publication when replacing the server/stream.
    response, current_generation = observe_snapshot_publication(
        202, {"status": "pending"}, 0.01, 101.0, None, published, 100.0)
    assert response["tokens"] is None
    assert current_generation is None
    with pytest.raises(AssertionError, match="snapshot readiness exceeded three collector cadences"):
        observe_snapshot_publication(
            503, {"error": "unavailable"}, 0.01, 115.001, current_generation, published, 100.0)


def test_fixture_freeze_quiesces_writes_without_releasing_child_lease(tmp_path):
    lease = tmp_path / "lease"
    journal = tmp_path / "journal"
    owner_source = r"""import fcntl, pathlib, sys
lease = open(sys.argv[1], 'w')
fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
journal = pathlib.Path(sys.argv[2])
journal.write_bytes(b'initial\n')
print('ready', flush=True)
for command in sys.stdin:
    with journal.open('ab') as output:
        output.write(command.encode())
    print('written', flush=True)
"""
    contender_source = """import fcntl, sys
with open(sys.argv[1], 'a') as lease:
    try:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(0)
    sys.exit(1)
"""
    fixture = FixtureServer.__new__(FixtureServer)
    fixture.log = (tmp_path / "freeze.log").open("w")
    fixture.frozen = False
    fixture.process = subprocess.Popen(
        [sys.executable, "-c", owner_source, str(lease), str(journal)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=fixture.log, text=True)
    try:
        assert select.select([fixture.process.stdout], [], [], 5)[0]
        assert fixture.process.stdout.readline() == "ready\n"
        fixture.freeze()
        assert fixture.frozen
        before = journal.read_bytes()
        fixture.process.stdin.write("after-freeze\n")
        fixture.process.stdin.flush()
        contender = subprocess.run(
            [sys.executable, "-c", contender_source, str(lease)], timeout=5, check=False)
        assert contender.returncode == 0
        assert fixture.process.poll() is None
        assert select.select([fixture.process.stdout], [], [], 0)[0] == []
        assert journal.read_bytes() == before
        fixture.process.send_signal(signal.SIGCONT)
        fixture.frozen = False
        assert select.select([fixture.process.stdout], [], [], 5)[0]
        assert fixture.process.stdout.readline() == "written\n"
        assert journal.read_bytes() == before + b"after-freeze\n"
    finally:
        fixture.stop(kill=True)
        fixture.process.stdin.close()
        fixture.process.stdout.close()
