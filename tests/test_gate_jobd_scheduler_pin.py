# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The gate live-server fixture must pin jobd for the whole fixture window, and release once.

The gate app is a local background owner: ``DisabledBackgroundOwner.is_owner()`` and
``can_run(role)`` both return True, so a Finder/session-files interaction starts the
owner-side session-files background refresh worker, and that worker submits
``session_files_view`` to jobd (``TmuxWebtermApp.submit_session_files_job`` ->
``job_client.submit``).  In production the elected owner first pins jobd by taking the
scheduler lease (``handle_background_owner_acquired`` -> ``job_client.start_for_scheduler``),
so the jobd Unix socket is present and warm before any refresh worker submits.  The gate
fixture used to skip that pin entirely while its teardown still released the lease, so every
jobd interaction during the window was an on-demand cold start.  Under -n16 CPU contention
that cold start raced an absent socket (``FileNotFoundError`` at ``client.connect``) or timed
out, and the strict browser-journey gate caught the emitted ``local-service:jobd`` transport
error.  These tests prove the fixture now guarantees the socket is present for the window and
releases the lease exactly once on teardown.
"""

from __future__ import annotations

import pytest

from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_live_server  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.gate_harness import pin_fixture_jobd_scheduler
from tests.gate_harness import prepare_fixture_http_app
from tests.gate_harness import stop_fixture_app_runtime


def test_gate_live_server_pins_jobd_scheduler_lease_for_the_window(gate_live_server) -> None:
    """A background refresh worker cannot race an absent jobd socket during the window."""

    app = gate_live_server.app
    # The scheduler lease is what pins jobd up and keeps its socket from idling out, exactly
    # as the elected background owner does in production before any session-files refresh runs.
    assert app.job_client.holds_scheduler_lease is True
    # The lease guarantee is only real if the daemon it pins actually has a serving socket on
    # disk for the whole fixture window; a lease held against an absent socket would not fence
    # the background refresh worker's submit.
    assert app.job_client.socket_path.exists()


def test_pinned_jobd_scheduler_lease_is_released_exactly_once_on_teardown(
    monkeypatch: pytest.MonkeyPatch,
    gate_runtime_paths,
    gate_tmux,
    make_tmux_webterm_app,
) -> None:
    """The setup pin has a matching teardown release, called exactly once and never doubled."""

    app = make_tmux_webterm_app(tuple(gate_tmux.sessions))
    prepare_fixture_http_app(monkeypatch, app)
    pin_fixture_jobd_scheduler(app)
    assert app.job_client.holds_scheduler_lease is True
    assert app.job_client.socket_path.exists()

    # Count the EFFECTIVE lease release at the registry, not the ``stop_for_scheduler`` calls:
    # teardown calls the release from two owners (``demote_background_owner`` and
    # ``stop_auto_approve_all``), but ``JobClient.stop_for_scheduler`` is idempotent and only the
    # first, while the lease is held, reaches ``registry.release_lease``.  Wrapping the registry
    # therefore proves the lease is released exactly once, and a real double-release would count 2.
    releases: list[str] = []
    original_release_lease = app.job_client.registry.release_lease

    def counting_release_lease(lease_id: str):
        releases.append(lease_id)
        return original_release_lease(lease_id)

    monkeypatch.setattr(app.job_client.registry, "release_lease", counting_release_lease)

    stop_fixture_app_runtime(app, label="jobd-scheduler-pin-regression")
    assert len(releases) == 1
    assert app.job_client.holds_scheduler_lease is False

    # A second teardown must not double-release the lease: the runtime-stopped guard short-circuits
    # a re-entered teardown, and even reaching ``stop_for_scheduler`` again finds no held lease, so
    # the registry release count stays at exactly one.
    stop_fixture_app_runtime(app, label="jobd-scheduler-pin-regression")
    assert len(releases) == 1
    assert app.job_client.holds_scheduler_lease is False
