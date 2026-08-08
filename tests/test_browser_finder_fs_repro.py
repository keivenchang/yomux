# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import pytest

from tools import finder_fs_repro as tool


pytestmark = [pytest.mark.browser, pytest.mark.socket]


def test_two_client_measurement_records_idle_change_failure_reload_and_navigation(monkeypatch, tmp_path):
    report = tool.run_measurement(monkeypatch, tmp_path, idle_seconds=1.0, event_timeout=10.0)

    assert report["version"] == 2
    phases = report["phases"]
    assert set(phases) == {
        "idle",
        "file_change",
        "forced_watch_diff_failure",
        "reload",
        "navigation",
    }
    for client in ("client-a", "client-b"):
        assert phases["idle"]["clients"][client]["request_counts"].get("/api/fs/batch", 0) == 0
        assert phases["file_change"]["clients"][client]["request_counts"].get("/api/fs/watch-diff", 0) >= 1
        assert phases["file_change"]["finder_state"][client]["watch_token"]
        assert phases["forced_watch_diff_failure"]["clients"][client]["rejected_counts"].get("/api/fs/watch-diff", 0) >= 1
    assert phases["idle"]["server"]["summary"] == []
    assert phases["idle"]["server"]["process_cpu_seconds"] >= 0.0
    surfaces_after_change = {row["surface"] for row in phases["file_change"]["server"]["summary"]}
    assert "GET /api/fs/watch-diff" in surfaces_after_change
    assert phases["forced_watch_diff_failure"]["server"]["summary"] == []
    surfaces_after_reload = {row["surface"] for row in phases["reload"]["server"]["summary"]}
    assert "POST /api/fs/batch" in surfaces_after_reload
    # Nested navigation may reuse a warm recursive listing or issue one first-time batch,
    # but it must never fall back to watch-diff polling or multiply listing requests.
    surfaces_after_navigation = {row["surface"] for row in phases["navigation"]["server"]["summary"]}
    assert surfaces_after_navigation <= {"POST /api/fs/batch"}
    for client in phases["navigation"]["clients"].values():
        assert client["request_counts"].get("/api/fs/watch-diff", 0) == 0
        assert client["request_counts"].get("/api/fs/batch", 0) <= 1


def test_two_client_full_sse_keyframe_converges_without_watch_diff(monkeypatch, tmp_path):
    report = tool.run_measurement(monkeypatch, tmp_path, idle_seconds=0.1, event_timeout=10.0, force_full_filesystem_event=True)

    assert report["file_change_delivery"] == "full-sse"
    phase = report["phases"]["file_change"]
    for client in ("client-a", "client-b"):
        assert phase["finder_state"][client]["watch_token"]
        assert phase["clients"][client]["request_counts"].get("/api/fs/watch-diff", 0) == 0
        assert phase["clients"][client]["request_counts"].get("/api/fs/batch", 0) == 0
    assert phase["server"]["summary"] == []
