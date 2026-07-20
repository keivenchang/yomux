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
        assert phases["forced_watch_diff_failure"]["clients"][client]["rejected_counts"].get("/api/fs/watch-diff", 0) >= 1
    assert phases["idle"]["server"]["summary"] == []
    surfaces_after_change = {row["surface"] for row in phases["file_change"]["server"]["summary"]}
    assert "GET /api/fs/watch-diff" in surfaces_after_change
    assert phases["forced_watch_diff_failure"]["server"]["summary"] == []
    surfaces_after_reload = {row["surface"] for row in phases["reload"]["server"]["summary"]}
    assert "POST /api/fs/batch" in surfaces_after_reload
    assert phases["navigation"]["server"]["summary"] == []
