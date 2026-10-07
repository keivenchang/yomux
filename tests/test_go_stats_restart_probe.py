# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import json
import signal
import subprocess
import sys

from tools.verify_go_stats_restart import FixtureServer, StreamProbe


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
    fixture.frozen = True
    fixture.process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        fixture.process.send_signal(signal.SIGSTOP)
        fixture.stop()
        assert fixture.process.returncode == -signal.SIGKILL
        assert fixture.log.closed
    finally:
        if fixture.process.poll() is None:
            fixture.process.kill()
            fixture.process.wait(timeout=5)
        fixture.log.close()
