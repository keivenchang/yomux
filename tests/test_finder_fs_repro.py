# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from tools import finder_fs_repro as tool


class _CaptureApp:
    def __init__(self, records):
        self.performance_capture_records = list(records)

    def performance_metrics_payload(self, measurement_scope=""):
        assert measurement_scope == "capture"
        return {"recent": [dict(row) for row in self.performance_capture_records]}


def _capture_row(marker: str, surface: str, compute_ms: float):
    return {
        "role": "http-endpoint",
        "surface": surface,
        "compute_ms": compute_ms,
        "payload_bytes": 10,
        "details": {
            "measurement_scope": "capture",
            "measurement_request_id": tool.measurement_request_id(marker),
        },
    }


def test_capture_server_measurements_retains_prior_phase_but_reports_only_current_identity():
    phase_a = "capture-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    phase_b = "capture-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    app = _CaptureApp([
        _capture_row(phase_a, "GET /api/fs/watch-diff", 11.0),
        _capture_row(phase_b, "POST /api/fs/batch", 7.0),
    ])

    report = tool.capture_server_measurements(app, tool.measurement_request_id(phase_b))

    assert [row["surface"] for row in report["summary"]] == ["POST /api/fs/batch"]
    assert [row["details"]["measurement_request_id"] for row in report["recent"]] == [
        tool.measurement_request_id(phase_b),
    ]
    assert len(app.performance_capture_records) == 2


def test_summarize_fetch_log_counts_watch_diff_batch_and_rejections():
    summary = tool.summarize_fetch_log([
        {"path": "/api/fs/watch-diff", "method": "GET", "result": "fulfilled"},
        {"path": "/api/fs/watch-diff", "method": "GET", "result": "rejected"},
        {"path": "/api/fs/batch", "method": "POST", "result": "fulfilled"},
        {"path": "/api/ping", "method": "GET", "result": "fulfilled"},
    ])

    assert summary["request_counts"] == {
        "/api/fs/batch": 1,
        "/api/fs/watch-diff": 2,
    }
    assert summary["request_counts_by_method"] == {
        "GET /api/fs/watch-diff": 2,
        "POST /api/fs/batch": 1,
    }
    assert summary["rejected_counts"] == {
        "/api/fs/watch-diff": 1,
    }


def test_saved_layout_search_keeps_files_panel_bootstrap():
    search = tool.saved_layout_search("1", "/tmp/finder-root")

    assert "bootCase=finder-fs-repro" in search
    assert "sessions=files%2C1" in search
    assert "tabs=slot1%3Afiles" in search
    assert "finder=files" in search


def test_wait_for_finder_settled_requires_continuous_idle_after_pending_operation(monkeypatch):
    now = 0.0
    observed = []

    def clock():
        return now

    def wait(duration):
        nonlocal now
        now += duration

    def finder_is_settled(_driver):
        state = "idle" if now < 0.1 or now >= 0.25 else "pending"
        observed.append((round(now, 3), state))
        return state == "idle"

    monkeypatch.setattr(tool, "finder_is_settled", finder_is_settled)

    tool.wait_for_finder_settled(
        {"client-a": object()},
        timeout=1.0,
        quiet_seconds=0.3,
        clock=clock,
        wait=wait,
    )

    assert any(state == "pending" for _, state in observed)
    assert now >= 0.55
