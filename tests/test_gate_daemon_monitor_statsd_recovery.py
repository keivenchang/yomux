# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""F3 acceptance A1 (second half): statsd self-recovery, observed end to end.

The FIRST half of A1 -- that the record producer REFUSES to publish a pid<=1 identity -- is proven
by unit tests (`test_local_services_launch.py`). The SECOND half is the one that was never observed
end to end: an `invalid_pid` record ALREADY on disk (left by a 0.7.0 build that published pid 0 after
a dropped status RPC) must not permanently brick statsd. A correctly-formed `invalid_pid` record is
reclaimed as record-only cleanup (`registry.py:854-873`), statsd starts fresh, and the server serves
stats again.

This test poisons the on-disk statsd record BEFORE the isolated server starts, then proves recovery
purely over HTTP against the running process:

  * GET /api/stats-stream flips from HTTP 424 `{"status":"unavailable"}` to an HTTP 200 event stream
    emitting `delta`/`ready` events,
  * authenticated POST /api/stats-observations succeeds (HTTP 200),
  * GET /api/system-status reports statsd `health.state == "ready"` with `history_coverage == "full"`,
  * and NO new failure episode fires after recovery -- statsd's recovery outcome never becomes
    `retry_exhausted`/`retry_blocked_*` and no new `down`/`exited` transition appears, which is how a
    `remove_stale_record` block, a `service_unavailable` fault, or a readiness timeout would surface.

Injection is deterministic by forcing the LEGACY socket path (`storage.default_socket_path`,
`storage.py:73-79`): a short state dir keeps the legacy path under the socket length cap, and an inert
`statsd.p24s7.sock` makes the product resolve statsd to that exact path, so the poisoned
`statsd.p24s7.service.json` beside it is the record the running server reads.
"""

from __future__ import annotations

import json
import socket
import time
import uuid
from http import HTTPStatus
from http.client import HTTPConnection
from pathlib import Path

import pytest

from tests.isolated_dev_server import IsolatedDevServer
from tests.isolated_dev_server import build_paths
from tests.isolated_dev_server import start_isolated_dev_server
from tests.isolated_dev_server import stop_and_reap_daemons
from tests.tmux_runtime import start_isolated_tmux_runtime
from tests.tmux_runtime import stop_isolated_tmux_runtime
from yolomux_lib.infra.host_identity import current_host_identity
from yolomux_lib.stats_current.storage import MIN_WRITER_PROTOCOL
from yolomux_lib.stats_current.storage import SCHEMA_VERSION
from yolomux_lib.stats_current.storage import SOCKET_FILENAME


pytestmark = pytest.mark.socket

RECOVERY_TIMEOUT_SECONDS = 30.0
POLL_SECONDS = 0.25
# After statsd reaches ready, hold this long and prove no new failure episode fires.
POST_RECOVERY_SETTLE_SECONDS = 6.0

STATSD_RECORD_NAME = f"{SOCKET_FILENAME.removesuffix('.sock')}.service.json"
# The terminal recovery outcome that means statsd gave up. `retry_blocked_*`/`retry_scheduled` are
# benign transients during a normal demand-start and are NOT failure evidence once statsd is ready.
GIVE_UP_RECOVERY_OUTCOME = "retry_exhausted"


def _write_poisoned_statsd_record(state_dir: Path) -> Path:
    """Leave a correctly-formed `invalid_pid` (pid 0) statsd record on disk, plus its inert socket.

    The socket must EXIST for `storage.default_socket_path` to take its legacy branch, so the running
    server resolves statsd to exactly this path and reads exactly this record. Nothing listens on it.
    """

    services_dir = state_dir / "services"
    services_dir.mkdir(parents=True, exist_ok=True)
    socket_path = services_dir / SOCKET_FILENAME
    socket_path.touch()
    identity = current_host_identity()
    record = {
        **identity.process_record_fields(pid=0, start_identity=""),
        "version": 2,
        "service": "statsd",
        "module": "yolomux_lib.stats_current.service",
        "protocol_version": MIN_WRITER_PROTOCOL,
        "socket": str(socket_path),
        "started_at": 1.0,
        "updated_at": 1.0,
    }
    record_path = services_dir / STATSD_RECORD_NAME
    record_path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return record_path


def _stats_stream_state(server: IsolatedDevServer, *, timeout: float = 3.0) -> tuple[int, str]:
    """Return (status, first-event-name) from /api/stats-stream, reading just the first SSE event.

    A statsd that is down returns a plain non-200 JSON body; a recovered statsd returns HTTP 200
    `text/event-stream` whose first event is `delta` or `ready`.
    """

    # The delta stream takes an EXACT numeric cursor (no AUTO): range/resolution must be a supported
    # pair (300 s @ 1 s is), plus a fresh after_cache_generation/after_revision of 0.
    query = "range_seconds=300&resolution_seconds=1&client_id=a1-recovery&after_cache_generation=0&after_revision=0"
    connection = HTTPConnection("127.0.0.1", server.port, timeout=timeout)
    try:
        connection.request("GET", f"/api/stats-stream?{query}")
        response = connection.getresponse()
        status = int(response.status)
        if status != HTTPStatus.OK:
            response.read()
            return status, ""
        deadline = time.monotonic() + timeout
        event_name = ""
        while time.monotonic() < deadline:
            try:
                raw = response.readline()
            except (socket.timeout, TimeoutError, OSError):
                break
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
                break
        return status, event_name
    finally:
        connection.close()


def _post(server: IsolatedDevServer, path: str, body: bytes, *, timeout: float = 5.0) -> tuple[int, bytes]:
    connection = HTTPConnection("127.0.0.1", server.port, timeout=timeout)
    try:
        connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        return int(response.status), response.read()
    finally:
        connection.close()


def _health_document(server: IsolatedDevServer) -> dict[str, object]:
    """The retained backend-health document the observer writes for this instance's port.

    Read straight off disk -- the same file `/api/system-status` is fed from, without the F1 snapshot
    envelope in between. It carries statsd's observed state, recovery outcome, per-resource coverage,
    lifetime transition count, and the document-level history coverage this test asserts on.
    """

    path = server.paths.state_dir / "backend-health" / f"{server.port}.json"
    deadline = time.monotonic() + 10.0
    document: dict[str, object] | None = None
    while time.monotonic() < deadline:
        try:
            raw = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            time.sleep(POLL_SECONDS)
            continue
        try:
            parsed = json.loads(raw)
        except ValueError:
            time.sleep(POLL_SECONDS)
            continue
        if isinstance(parsed, dict):
            document = parsed
            break
    assert document is not None, (path, server.output[-20:])
    return document


def _statsd_resource(document: dict[str, object]) -> dict[str, object]:
    resources = document.get("resources")
    assert isinstance(resources, dict), document.keys()
    statsd = resources.get("statsd")
    assert isinstance(statsd, dict), (
        f"no statsd resource in backend-health document: {sorted(resources.keys())}"
    )
    return statsd


def _statsd_current(document: dict[str, object]) -> dict[str, object]:
    current = _statsd_resource(document).get("current")
    assert isinstance(current, dict), _statsd_resource(document)
    return current


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="spawns a real isolated dev server")
def test_a1_statsd_self_recovers_from_a_poisoned_invalid_pid_record_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A1: a poisoned invalid_pid record on disk does not brick statsd; the server self-recovers."""

    # A SHORT state dir keeps the legacy statsd socket path under the length cap so it is not
    # relocated under /tmp with a hashed name -- the poisoned record has to be at the path the
    # running server actually reads.
    state_dir = Path(f"/tmp/yA1-{uuid.uuid4().hex[:8]}/s")
    record_path = _write_poisoned_statsd_record(state_dir)
    assert record_path.exists()
    poisoned = json.loads(record_path.read_text(encoding="utf-8"))
    assert int(poisoned["pid"]) <= 1 and poisoned["service"] == "statsd", poisoned

    root = tmp_path / "a1-runtime"
    paths = build_paths(root, state_dir=state_dir)
    tmux_runtime = start_isolated_tmux_runtime(monkeypatch, root, session_count=1)
    server: IsolatedDevServer | None = None
    try:
        # A production-like idle keeps statsd from idle-cycling between demands, so a new health
        # transition after recovery is a real failure episode rather than a benign idle-exit.
        server = start_isolated_dev_server(
            "a1",
            Path(__file__).resolve().parents[1],
            paths,
            tmux_runtime,
            env_overrides={"YOLOMUX_LOCAL_SERVICE_IDLE_SECONDS": "45"},
        )
        server.assert_serving()

        # Demand stats: this is what drives `ensure_started` -> `_remove_stale_record`, which reclaims
        # the poisoned record (record-only unlink) and spawns a fresh statsd.
        status, _headers, snapshot = server.request(
            "/api/stats-snapshot?range_seconds=300&resolution=AUTO&client_id=a1-recovery"
        )
        assert status != HTTPStatus.INTERNAL_SERVER_ERROR, (status, snapshot, server.output[-20:])

        # /api/stats-stream reaches ready: an HTTP 200 event stream. A recovered statsd answers with
        # a `ready` (NOT_MODIFIED), a `delta` (new data), or a `repair` (the cursor was stale but
        # statsd IS serving and holds a current cache generation to compare against). A statsd still
        # bricked by the poison cannot produce any of these -- it returns a non-200 unavailable body.
        serving_events = ("delta", "ready", "repair")
        stream_status = 0
        stream_event = ""
        deadline = time.monotonic() + RECOVERY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            stream_status, stream_event = _stats_stream_state(server)
            if stream_status == HTTPStatus.OK and stream_event in serving_events:
                break
            time.sleep(POLL_SECONDS)
        assert stream_status == HTTPStatus.OK, (
            f"a1: /api/stats-stream never recovered from statsd-unavailable; last status={stream_status}, "
            f"event={stream_event!r}, output={server.output[-20:]}"
        )
        assert stream_event in serving_events, (stream_status, stream_event)

        # Authenticated POST /api/stats-observations succeeds. The batch carries the current
        # protocol/schema and one well-formed browser observation, the shape statsd accepts.
        observation = json.dumps(
            {
                "protocol_version": MIN_WRITER_PROTOCOL,
                "schema_generation": SCHEMA_VERSION,
                "client_id": "a1-recovery",
                "observations": [
                    {
                        "event_id": "a1-request-1",
                        "family": "browser",
                        "source_id": "a1-recovery",
                        "observed_at": 100.5,
                        "epoch_id": "a1-page-1",
                        "payload": {"kind": "api", "latency_ms": 12, "bytes": 345},
                    }
                ],
            }
        ).encode("utf-8")
        post_status, post_body = _post(server, "/api/stats-observations", observation)
        assert post_status == HTTPStatus.OK, (post_status, post_body, server.output[-20:])

        # Retained health: wait until the observer records statsd READY, then assert the recovery
        # shape -- history coverage full (the poison did not reset history), the statsd resource
        # coverage full, and the recovery outcome not the terminal give-up. "history coverage exits
        # retry" is exactly this: statsd leaves the retry_blocked_* startup transients for a settled
        # ready state whose coverage is full.
        deadline = time.monotonic() + RECOVERY_TIMEOUT_SECONDS
        document = _health_document(server)
        while time.monotonic() < deadline:
            document = _health_document(server)
            if str(_statsd_current(document).get("state")) == "ready":
                break
            _stats_stream_state(server)
            time.sleep(POLL_SECONDS)
        current = _statsd_current(document)
        assert str(current.get("state")) == "ready", (current, server.output[-20:])
        assert str(current.get("recovery_outcome") or "") != GIVE_UP_RECOVERY_OUTCOME, current
        statsd = _statsd_resource(document)
        aggregate = statsd.get("aggregate")
        assert isinstance(aggregate, dict) and str(aggregate.get("coverage")) == "full", statsd
        assert str(document.get("history_coverage") or "") == "full", {
            k: document.get(k) for k in ("history_coverage", "history_reset_reason")
        }

        # No new failure episode after recovery: with a production-like idle statsd stays ready and
        # gains no new transition over a settle window. A `remove_stale_record` block, a
        # `service_unavailable` fault, or a readiness timeout would each drive one here.
        before_total = int(statsd.get("transitions_total") or 0)
        time.sleep(POST_RECOVERY_SETTLE_SECONDS)
        after_document = _health_document(server)
        after_current = _statsd_current(after_document)
        after_statsd = _statsd_resource(after_document)
        assert str(after_current.get("state")) == "ready", (after_current, server.output[-20:])
        assert int(after_statsd.get("transitions_total") or 0) == before_total, (
            f"a1: statsd recorded a new health transition after recovery: {before_total} -> "
            f"{after_statsd.get('transitions_total')} (state={after_current.get('state')}, "
            f"reason={after_current.get('reason_code')}, recovery={after_current.get('recovery_outcome')})"
        )
        assert str(after_current.get("recovery_outcome") or "") != GIVE_UP_RECOVERY_OUTCOME, after_current
    finally:
        # A 45 s idle means the daemons would otherwise outlive this test; reap them now.
        if server is not None:
            stop_and_reap_daemons(server)
        stop_isolated_tmux_runtime(tmux_runtime)
