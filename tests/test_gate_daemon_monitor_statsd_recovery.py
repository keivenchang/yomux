# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Go stats acceptance when retired Python statsd state is present."""

from __future__ import annotations

import hashlib
import json
import socket
import time
import uuid
from http import HTTPStatus
from http.client import HTTPConnection
from pathlib import Path

import pytest

from tests.isolated_dev_server import build_paths
from tests.isolated_dev_server import start_isolated_dev_server
from tests.tmux_runtime import start_isolated_tmux_runtime
from tests.tmux_runtime import stop_isolated_tmux_runtime
from yomux_lib.infra.host_identity import current_host_identity
from yomux_lib.stats_current.storage import MIN_WRITER_PROTOCOL
from yomux_lib.stats_current.storage import SCHEMA_VERSION
from yomux_lib.stats_current.storage import SOCKET_FILENAME

pytestmark = pytest.mark.socket
POLL_SECONDS = 0.25
STATSD_RECORD_NAME = f"{SOCKET_FILENAME.removesuffix('.sock')}.service.json"


def _write_poisoned_statsd_record(state_dir: Path) -> Path:
    """Seed state owned by the retired Python statsd service."""

    services_dir = state_dir / "services"
    services_dir.mkdir(parents=True, exist_ok=True)
    socket_path = services_dir / SOCKET_FILENAME
    socket_path.touch()
    record = {
        **current_host_identity().process_record_fields(pid=0, start_identity=""),
        "version": 2,
        "service": "statsd",
        "module": "yomux_lib.stats_current.service",
        "protocol_version": MIN_WRITER_PROTOCOL,
        "socket": str(socket_path),
        "started_at": 1.0,
        "updated_at": 1.0,
    }
    record_path = services_dir / STATSD_RECORD_NAME
    record_path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return record_path


def _stats_stream_state(port: int, *, timeout: float = 5.0) -> tuple[int, str]:
    connection = HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", "/api/stats-stream?range_seconds=300&resolution=1&client_id=go-stats-stale-record")
        response = connection.getresponse()
        status = int(response.status)
        if status != HTTPStatus.OK:
            response.read()
            return status, ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = response.readline()
            except (socket.timeout, TimeoutError, OSError):
                break
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("event:"):
                return status, line.split(":", 1)[1].strip()
        return status, ""
    finally:
        connection.close()


def test_go_stats_ignore_poisoned_legacy_statsd_record_end_to_end(monkeypatch, tmp_path):
    """Stale Python service files do not block Go capabilities, streaming, or ingestion."""

    root = tmp_path / "go-stats-runtime"
    state_dir = root / "state"
    record_path = _write_poisoned_statsd_record(state_dir)
    original_record = record_path.read_bytes()
    original_digest = hashlib.sha256(original_record).hexdigest()
    paths = build_paths(root, state_dir=state_dir)
    tmux_runtime = start_isolated_tmux_runtime(monkeypatch, root, session_count=1)
    server = None
    try:
        server = start_isolated_dev_server("go-stats-stale-record", Path(__file__).resolve().parents[1], paths, tmux_runtime)
        server.assert_serving()

        status, _headers, capabilities_body = server.request("/api/stats-capabilities")
        assert status == HTTPStatus.OK, capabilities_body
        capabilities = json.loads(capabilities_body)
        assert capabilities.get("state") == "ready", capabilities

        stream_status, event_name = _stats_stream_state(server.port)
        assert stream_status == HTTPStatus.OK, (stream_status, event_name, server.output[-20:])
        assert event_name in {"ack", "pending"}, (stream_status, event_name)

        observation = json.dumps({
            "protocol_version": MIN_WRITER_PROTOCOL,
            "schema_generation": SCHEMA_VERSION,
            "client_id": "go-stats-stale-record",
            "observations": [{
                "event_id": "go-stats-stale-record-1",
                "family": "browser",
                "source_id": "go-stats-stale-record",
                "observed_at": 100.5,
                "epoch_id": "go-stats-stale-record-page",
                "payload": {"kind": "api", "latency_ms": 12, "bytes": 345},
            }],
        }).encode("utf-8")
        connection = HTTPConnection("127.0.0.1", server.port, timeout=5.0)
        try:
            connection.request("POST", "/api/stats-observations", body=observation, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            response_body = response.read()
            assert response.status == HTTPStatus.OK, response_body
        finally:
            connection.close()

        assert record_path.read_bytes() == original_record
        assert hashlib.sha256(record_path.read_bytes()).hexdigest() == original_digest
    finally:
        if server is not None:
            server.stop()
        stop_isolated_tmux_runtime(tmux_runtime)
