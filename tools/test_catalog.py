#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Canonical pytest file catalogs for the local check lanes.

Pytest only needs to import files which can contribute to a lane.  Passing the
whole ``tests`` directory to every marker-filtered invocation made even the
two-case boot smoke collect the entire suite.  Keep the ownership here, and
validate it against real pytest collection in ``test_check_runner.py``.
"""

from __future__ import annotations

from typing import Final


NONBROWSER_FILES: Final[tuple[str, ...]] = (
    "tests/test_activity.py", "tests/test_activity_summary.py", "tests/test_agent_tui.py", "tests/test_app.py", "tests/test_approvald.py", "tests/test_atomic_file.py", "tests/test_attention_ack_sync.py", "tests/test_auth_config.py", "tests/test_auto_approve_detector.py", "tests/test_auto_approve_lock.py", "tests/test_auto_approve_worker.py", "tests/test_background_owner.py", "tests/test_cache.py", "tests/test_chat_service.py", "tests/test_chat_store.py", "tests/test_check_runner.py", "tests/test_claude_permission_hook.py", "tests/test_client_events.py", "tests/test_common.py", "tests/test_control.py", "tests/test_dev_restart_script.py", "tests/test_drop_actions.py", "tests/test_events_state.py", "tests/test_file_index.py", "tests/test_filesystem.py", "tests/test_install_metadata.py", "tests/test_jobd.py", "tests/test_local_services_launch.py", "tests/test_local_services_rpc.py", "tests/test_login_auth.py", "tests/test_login_escalation.py", "tests/test_login_rate_limit.py", "tests/test_login_throttle_integration.py", "tests/test_metadata.py", "tests/test_metadata_badge_pulses.py", "tests/test_mock_agents.py", "tests/test_observability.py", "tests/test_pricing_catalog.py", "tests/test_search_indexer.py", "tests/test_server_lease.py", "tests/test_server_logs.py", "tests/test_server_query.py", "tests/test_server_static.py", "tests/test_source_inventory.py", "tests/test_service_failure_matrix.py", "tests/test_session_actions.py", "tests/test_session_files.py", "tests/test_sessions.py", "tests/test_settings.py", "tests/test_static_build.py", "tests/test_stats_current_app.py", "tests/test_stats_current_client.py", "tests/test_stats_current_collectors.py", "tests/test_stats_current_families.py", "tests/test_stats_current_http.py", "tests/test_stats_current_materializer.py", "tests/test_stats_current_migration.py", "tests/test_stats_current_observations.py", "tests/test_stats_current_pricing.py", "tests/test_stats_current_protocol.py", "tests/test_stats_current_revision.py", "tests/test_stats_current_runtime.py", "tests/test_stats_current_scheduler.py", "tests/test_stats_current_service.py", "tests/test_stats_current_storage.py", "tests/test_stats_current_transcripts.py", "tests/test_stats_current_usage.py", "tests/test_stats_resolution.py", "tests/test_statusd.py", "tests/test_statusd_protocol.py", "tests/test_test_isolation.py", "tests/test_text_client_common_metadata.py", "tests/test_tls_config.py", "tests/test_tmux_signals.py", "tests/test_tmux_runtime.py", "tests/test_tmux_theme.py", "tests/test_tmux_utils.py", "tests/test_tmux_wall.py", "tests/test_transcripts.py", "tests/test_ui_pins.py", "tests/test_uploads.py", "tests/test_web.py", "tests/test_websocket.py", "tests/test_workdir.py", "tests/test_yoagent_actions.py", "tests/test_yoagent_backend.py", "tests/test_yoagent_frontend.py", "tests/test_yoagent_model_intents.py", "tests/test_yoagent_orchestration.py", "tests/test_yoagent_preferences.py", "tests/test_yoagent_skills.py", "tests/test_yoagent_stream_events.py", "tests/test_yoagent_stream_state.py", "tests/test_yoagent_transports.py", "tests/test_yolo_rules.py",
)

BOOT_FILES: Final[tuple[str, ...]] = ("tests/test_browser_boot.py",)
BROWSER_FILES: Final[tuple[str, ...]] = ("tests/test_browser_dockview.py", "tests/test_browser_editor.py", "tests/test_browser_finder.py", "tests/test_browser_layout.py", "tests/test_browser_share.py", "tests/test_browser_stats_coverage.py", "tests/test_browser_stats_widen.py")
GOLDEN_FILES: Final[tuple[str, ...]] = ("tests/test_browser_golden.py",)
E2E_FILES: Final[tuple[str, ...]] = ("tests/test_browser_layout.py", "tests/test_e2e_auto_approve.py", "tests/test_mock_agents.py")
NODE_BRIDGE_FILES: Final[tuple[str, ...]] = ("tests/test_node_suite.py",)

PYTEST_PHASE_FILES: Final[dict[str, tuple[str, ...]]] = {
    "nonbrowser": NONBROWSER_FILES,
    "boot": BOOT_FILES,
    "browser": BROWSER_FILES,
    "golden": GOLDEN_FILES,
    "e2e": E2E_FILES,
    "node_bridge": NODE_BRIDGE_FILES,
}


def pytest_files(phase: str) -> list[str]:
    """Return the explicit pytest targets for one canonical phase."""
    return list(PYTEST_PHASE_FILES[phase])
