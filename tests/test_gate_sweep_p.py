# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Deferred recovery sentinels and the credential-admission regression."""
from __future__ import annotations
import pytest
from http import HTTPStatus
from tests.helpers.login_throttle import (
    VALID_USER, VALID_PASSWORD, start_server, stop_server,
)
from tests.helpers.fixture_http_server import basic_auth_header as auth_header
from tests.helpers.fixture_http_server import request_fixture_http_tuple as request
from yomux_lib.login_rate_limit import BucketPolicy, LoginRatePolicy

def _missing(item: str, contract: str) -> None:
    assert False, f"{item} waits for its deferred user-boundary contract: {contract}"

@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P2 recovery preflight contract")
def test_p2_same_worktree_commit_is_not_recovery_drift(): _missing("F-P2", "HTTP preflight permits same-root HEAD movement")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P3 recovery dialog contract")
def test_p3_blocked_recover_is_visible_and_nonmutating(): _missing("F-P3", "DOM disabled control is visibly blocked and does not dispatch")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P4 recovery transcript contract")
def test_p4_recovery_keeps_process_discovered_transcript_identity(): _missing("F-P4", "recovered browser terminal resumes its pane transcript")
@pytest.mark.xfail(strict=True, reason="Deferred, not in current release scope: F-P5 pane lifecycle evidence contract")
def test_p5_deliberate_pane_close_is_not_lost(): _missing("F-P5", "matched clean lifecycle evidence removes no Lost row")
@pytest.mark.socket
@pytest.mark.no_browser
# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_p13_repeated_valid_basic_requests_are_not_throttled(monkeypatch, tmp_path):
    bucket = BucketPolicy(2, 0.001)
    policy = LoginRatePolicy(exact_bucket=bucket, nearby_bucket=bucket,
                             broad_bucket=bucket, global_bucket=bucket)
    server, thread, _limiter = start_server(monkeypatch, tmp_path, policy=policy)
    try:
        for _ in range(6):
            status, _headers, _body = request(server.server_address[1], "GET", "/api/ping",
                                            headers=auth_header(VALID_USER, VALID_PASSWORD))
            assert status == HTTPStatus.OK
    finally:
        stop_server(server, thread)
