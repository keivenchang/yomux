# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from tools import finder_fs_repro as tool


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
