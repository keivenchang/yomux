#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Reject test assertions that only constrain inspected text or source shape.

Source-contract tests are occasionally the right boundary, but an assertion such
as ``assert \"body\" not in json.dumps(details)`` cannot demonstrate the
behaviour it claims to protect.  Every surviving source contract therefore
needs a reviewed, non-empty reason in ``TEXT_SHAPE_ASSERTION_ALLOWLIST``.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final


REPO_ROOT = Path(__file__).resolve().parent.parent

# Keep exceptions specific and explain the product contract they protect.  The
# guard rejects stale entries, so this is an audit trail rather than a blanket
# opt-out for a file or class of tests.
TEXT_SHAPE_ASSERTION_ALLOWLIST: Final[dict[str, str]] = {
    "tests/test_stats_current_prune_schedule.py:test_preferences_panel_offers_the_schedule_in_the_stats_section": "The client must render the server-owned prune-time choices and must never spell its own copy of the default; that wiring is the contract, and the values themselves are proven behaviourally at test_default_prune_time_is_half_past_two_and_is_offered.",
    "tests/test_chat_store.py:test_chat_store_paging_sql_reads_the_real_implementation_and_avoids_offset": "The OFFSET paging contract is only falsifiable if the scanned file really defines the store's SQL; the prior form read a six-line sys.modules alias with no SQL and could never fail.",
    "tests/test_app.py:test_session_http_guards_use_shared_decorator": "The decorator is the declared HTTP guard boundary, so its single shared attachment is a structural contract.",
    "tests/test_app.py:test_yoagent_controller_facade_allows_only_declared_dependencies": "The controller facade deliberately limits imports and dependencies; this is an architecture contract.",
    "tests/test_app.py:test_stats_history_sampler_parallel_state_is_retired": "The retired sampler maps must remain absent to prevent a second state owner.",
    "tests/test_app.py:test_share_debug_profile_is_opt_in_and_redacted": "The debug profile source is audited for opt-in and redaction before any profile can be collected.",
    "tests/test_app.py:test_auto_approve_worker_parallel_maps_are_retired": "The retired worker maps must remain absent to preserve the one-worker ownership design.",
    "tests/test_app.py:test_transcripts_payload_parallel_cache_state_is_retired": "The retired transcript cache maps must remain absent to preserve one cache owner.",
    "tests/test_app.py:test_tabber_activity_parallel_cache_state_is_retired": "The retired tabber cache maps must remain absent to preserve one cache owner.",
    "tests/test_app.py:test_tabber_activity_warmer_parallel_state_is_retired": "The retired tabber warmer state must remain absent to preserve one warmer owner.",
    "tests/test_app.py:test_session_files_disk_prune_parallel_state_is_retired": "The retired disk-prune state must remain absent to prevent a duplicate pruner.",
    "tests/test_app.py:test_client_watch_file_parallel_state_maps_are_retired": "The retired watch-file maps must remain absent to preserve one event owner.",
    "tests/test_app.py:test_the_cpu_sample_staleness_policy_has_exactly_one_owner": "The retired `3.0` staleness literal must remain absent from app.py so the reader's threshold cannot drift from the producer's cadence; the arithmetic identity asserted beside it holds regardless of a second hardcoded copy reappearing, so only the source check can detect that copy.",
    "tests/test_app.py:test_yoagent_session_summary_parallel_worker_fields_are_retired": "The retired summary worker fields must remain absent to preserve the shared worker boundary.",
    "tests/test_auth_config.py:test_missing_auth_yaml_creates_commented_starter": "The shipped starter-file comments are the user-facing configuration contract.",
    "tests/test_auth_config.py:test_legacy_placeholder_auth_yaml_is_replaced_with_commented_starter": "The migration writes the same documented starter-file contract.",
    "tests/test_auth_config.py:test_uncommented_auth_yaml_is_active": "The parser's active-versus-commented configuration grammar is intentionally text-defined.",
    "tests/test_auth_config.py:test_setup_auth_script_has_no_parallel_english_status_fallbacks": "The setup script must route status strings through the locale owner rather than duplicate literals.",
    "tests/test_auth_config.py:test_pre_auth_pages_share_reload_picker_and_localize_first_paint": "The pre-auth template's shared picker and localization bootstrap are a static first-paint contract.",
    "tests/test_background_owner.py:test_background_owner_required_log_event_names_have_emitters": "The event-name catalog is a compatibility contract between the producer and event consumers.",
    "tests/test_browser_editor.py:test_direct_mermaid_sample_real_bundle_keeps_svg_text_labels": "The embedded Mermaid sample is a fixed rendering fixture, not a substitute for browser behavior tests.",
    "tests/test_browser_stats_coverage.py:test_current_stats_fixture_uses_canonical_resolution_policy": "The browser fixture must import the canonical stats resolution policy rather than clone it.",
    "tests/test_client_events.py:test_browser_client_event_contract_matches_server_event_types": "The declared client-event names are a producer/consumer protocol contract.",
    "tests/test_common.py:test_main_process_cpu_work_has_named_allowlist": "The process-accounting allowlist is an explicit static inventory.",
    "tests/test_dev_restart_script.py:test_boot_restart_waits_for_stable_listener_after_ready": "The boot script's listener sequencing is an operator safety contract best checked before execution.",
    "tests/test_dev_restart_script.py:test_boot_restart_requires_old_listener_to_stop_before_launch": "The boot script's old-listener shutdown protocol is an operator safety contract best checked before execution.",
    "tests/test_filesystem.py:test_filesystem_implementations_leave_os_error_normalization_to_package_facade": "The package facade is the deliberate sole owner of OS-error normalization.",
    "tests/test_gate_stats_range.py:test_debug_stats_sample_endpoint_is_not_a_live_client_contract": "The debug endpoint must stay absent from shipped client source.",
    "tests/test_gate_worktree_protection.py:test_docs_separate_relocated_artifacts_from_single_writer_generated_source": "The written worktree ownership policy is an explicit repository contract.",
    "tests/test_install_metadata.py:test_install_metadata_owns_python_floor_and_watchfiles_dependency": "Packaging metadata is itself the install-time product contract.",
    "tests/test_local_services_launch.py:test_pyproject_package_discovery_includes_local_service_subpackages": "Package discovery configuration is an install-time source contract.",
    "tests/test_login_auth.py:test_login_page_uses_saved_theme_and_active_color": "The pre-login template needs its static theme bootstrap before JavaScript is available.",
    "tests/test_metadata.py:test_session_metadata_retirement_guards_keep_git_ownership_in_work_graph_only": "The retired metadata path must remain absent to preserve work-graph ownership.",
    "tests/test_metadata_badge_pulses.py:test_metadata_badge_state_has_one_typed_session_owner": "The typed badge state has one intentional owner and no duplicate state map.",
    "tests/test_mock_agents.py:test_background_codex_working_animation_reuses_its_status_row": "The mock renderer's static markup reuse is its exact TUI parity contract.",
    "tests/test_mock_agents.py:test_all_mock_timer_paths_obey_the_no_repaint_contract": "The mock timer paths are intentionally enumerated static rendering contracts.",
    "tests/test_server_query.py:test_activity_hours_routes_share_float_validation_owner": "Both routes must call the same validation owner rather than duplicate parsing rules.",
    "tests/test_server_query.py:test_both_attach_paths_route_through_shared_tmux_options": "Host and share attach paths must call one tmux-option helper to avoid divergent terminal behavior.",
    "tests/test_server_query.py:test_tmux_attach_paths_refresh_clients_after_attach": "Both attach paths must call the shared client-refresh helper.",
    "tests/test_server_query.py:test_configure_session_tmux_options_uses_bounded_tmux_helper": "The helper must use the bounded tmux wrapper and never bypass it with subprocess.run.",
    "tests/test_server_query.py:test_request_body_reader_owns_content_length_validation": "Content-length validation has one deliberate request-body owner.",
    "tests/test_server_query.py:test_http_route_registry_groups_dispatch_and_keeps_verbs_thin": "Route registration and verb dispatch are intentionally centralized architecture boundaries.",
    "tests/test_server_query.py:test_tmux_signal_event_watcher_is_owned_by_client_event_lifecycle": "The signal watcher lifecycle has one intentional client-event owner.",
    "tests/test_server_query.py:test_server_source_wires_routing_ws_readonly_and_pty_setup": "The server's route, readonly, and PTY wiring form an architecture contract.",
    "tests/test_server_query.py:test_share_pointer_events_are_coalesced_server_side": "Pointer coalescing must remain server-owned to bound share traffic.",
    "tests/test_server_query.py:test_share_pointer_parallel_state_maps_are_retired": "The retired pointer maps must remain absent to prevent duplicate ownership.",
    "tests/test_server_query.py:test_share_ui_socket_source_wiring_remains_explicit": "The share UI socket protocol has explicit source-level producer and consumer wiring.",
    "tests/test_server_query.py:test_share_replay_parallel_state_maps_are_retired": "The retired replay maps must remain absent to preserve one replay owner.",
    "tests/test_server_query.py:test_share_viewers_receive_host_terminal_dimensions": "Viewer geometry must use host dimensions through the designated shared path.",
    "tests/test_server_query.py:test_share_terminal_reader_uses_owned_fd_duplicate_before_reading": "The terminal reader's duplicate-FD ownership is a process-safety boundary.",
    "tests/test_session_files.py:test_transcript_scan_store_survives_cold_reload_and_resumes_append": "The persistent scan store's source contains the intentional recovery markers.",
    "tests/test_session_files.py:test_transcript_scan_cache_has_one_owner_and_bounds_claude_message_ids": "The scan cache has one deliberately bounded state owner.",
    "tests/test_settings.py:test_preferences_source_paths_are_in_backend_catalog": "The backend preference catalog is a static source-of-truth mapping.",
    "tests/test_static_build.py:test_markdown_source_change_invalidates_derived_preview_artifacts": "The static builder's dependency declarations are build-system contracts.",
    "tests/test_static_build.py:test_dialog_capacities_have_one_content_relative_token_owner": "Dialog capacity CSS tokens must have one source owner.",
    "tests/test_static_build.py:test_scroll_restoration_browser_checks_wait_for_observable_state": "The browser test harness wait owner is intentionally a source-level contract.",
    "tests/test_static_build.py:test_event_rows_use_one_container_responsive_layout_owner": "Responsive event-row layout must use one CSS owner.",
    "tests/test_static_build.py:test_browser_fixture_wait_loops_have_one_injected_owner": "Browser fixture wait loops must use the injected common owner.",
    "tests/test_static_build.py:test_tokenized_component_base_rules_have_no_identical_light_restatements": "Theme CSS must use tokens rather than duplicate light-mode declarations.",
    "tests/test_static_build.py:test_compact_overflow_strips_have_one_shared_layout_owner": "Compact overflow layout must have one shared CSS owner.",
    "tests/test_static_build.py:test_audited_css_families_have_one_grouped_owner": "Audited CSS families intentionally enforce a single grouped owner.",
    "tests/test_static_build.py:test_shared_ui_ownership_map_and_agent_reuse_protocol_remain_routable": "The shared UI ownership document is a repository routing contract.",
    "tests/test_static_build.py:test_panel_frame_builder_owns_the_shared_panel_chrome": "Shared panel chrome must route through the sole frame builder.",
    "tests/test_static_build.py:test_node_shard_launcher_has_unique_behavior_owners_and_a_terminal_summary": "The node shard launcher requires static unique-owner and terminal-summary wiring.",
    "tests/test_static_build.py:test_opaque_white_has_one_css_paint_owner": "Opaque white paint must have one tokenized CSS owner.",
    "tests/test_static_build.py:test_tree_row_hover_and_selection_paint_have_one_base_owner": "Tree hover and selection paint must have one base CSS owner.",
    "tests/test_stats_current_app.py:test_legacy_stats_handlers_and_scheduler_bodies_are_deleted": "The retired stats handlers must remain absent after the daemon migration.",
    "tests/test_stats_current_client.py:test_default_paths_are_version_scoped_and_never_use_the_legacy_filename": "Version-scoped storage paths must not revive the legacy filename.",
    "tests/test_stats_current_materializer.py:test_materializer_source_has_no_synthetic_cost_series_or_metadata_codec": "Synthetic cost and metadata codec paths were intentionally retired.",
    "tests/test_stats_current_service.py:test_server_wire_builders_do_not_revalidate_each_preencoded_private_variant": "Wire builders must preserve the one validation boundary for private variants.",
    "tests/test_stats_current_service.py:test_system_status_exposes_current_pipeline_health_without_private_values": "Status source must not expose private values while reporting pipeline health.",
    "tests/test_system_status_snapshot.py:test_the_serving_process_owns_the_producer_lifecycle": "The snapshot producer must have exactly one lifecycle owner, and 'exactly one' is a count over the serving process's source that no runtime assertion can make: a second start call in a second place still yields a running producer, so a behavioural test cannot see it. The producer's observable start/stop behaviour is proven separately; this assertion protects only the single-owner wiring.",
    "tests/test_text_client_common_metadata.py:test_text_clients_use_shared_agent_comms_primitives": "Text clients must import the shared communication primitives rather than fork them.",
    "tests/test_tmux_runtime.py:test_e2e_auto_approve_routes_tmux_waits_through_the_selenium_free_shared_owner": "The E2E harness must route tmux waits through the shared Selenium-free owner.",
    "tests/test_uploads.py:test_upload_request_limit_comes_from_live_settings": "The upload route must use the live settings limit rather than a duplicate literal.",
    "tests/test_yostats_active_browser_window.py:test_capture_tools_share_proc_cpu_reader_and_positive_validators": "The capture tools must import shared CPU and positive-value validators.",
    "tests/test_yostats_active_browser_window.py:test_active_browser_window_workload_source_contract": "The operator capture tool's workload and measurement setup is an audited static contract; authentication is exercised separately.",
    "tests/test_yostats_active_browser_window.py:test_benchmark_child_runs_in_its_own_process_group_and_is_group_stopped": "The benchmark subprocess group boundary is an operator cleanup contract.",
    "tests/test_yostats_active_browser_window.py:test_main_installs_signal_handlers_deadline_and_selenium_timeouts": "The capture CLI's bounded signal, deadline, and Selenium setup is an operator safety contract.",
    "tests/test_filesystem_access_policy.py:test_one_owner_builds_every_filesystem_job_descriptor": "The one-owner scan IS a source-shape contract: a filesystem job descriptor built by hand anywhere in app.py, jobd.py or tests/ is the second construction site that reintroduces the cross-port authorization bypass. Its allowlist is keyed by occurrence count so a second copy of an allowlisted literal still fails, and it was watched firing on a reverted fixture.",
}

# A function-level reason covers related assertions in that one source-contract
# test. Pin the complete current assertion inventory as well, so adding a new
# text-shape assertion to an already allowlisted function still fails the guard
# until a reviewer deliberately updates this value and its reason. The sequence
# is stable when unrelated code moves an assertion to a different source line.
TEXT_SHAPE_ASSERTION_INVENTORY_SHA256: Final[str] = "7d19d89efe0f3741032f53de5f630dd48f9b62e2e6d637011b49fe8fed59e005"


@dataclass(frozen=True, slots=True)
class TextShapeFinding:
    path: str
    function: str
    line: int
    sequence: int

    @property
    def key(self) -> str:
        return f"{self.path}:{self.function}:{self.line}"

    @property
    def inventory_key(self) -> str:
        return f"{self.path}:{self.function}:{self.sequence}"

    @property
    def allowlist_key(self) -> str:
        return f"{self.path}:{self.function}"


def _attribute_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _attribute_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _is_text_producer(node: ast.AST) -> bool:
    """Whether *node* directly produces inspected source or serialized text."""

    if not isinstance(node, ast.Call):
        return False
    called = _attribute_name(node.func)
    return called in {"inspect.getsource", "getsource", "json.dumps", "dumps"} or (
        isinstance(node.func, ast.Attribute) and node.func.attr == "read_text"
    )


def _name_targets(statement: ast.stmt) -> tuple[str, ...]:
    if isinstance(statement, ast.Assign):
        return tuple(target.id for target in statement.targets if isinstance(target, ast.Name))
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return (statement.target.id,)
    return ()


def _assignment_value(statement: ast.stmt) -> ast.AST | None:
    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
        return statement.value
    return None


def _text_derived_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Find direct producers and simple aliases in one test function."""

    names: set[str] = set()
    assignments = [node for node in ast.walk(function) if isinstance(node, (ast.Assign, ast.AnnAssign))]
    changed = True
    while changed:
        changed = False
        for statement in assignments:
            value = _assignment_value(statement)
            if value is None:
                continue
            is_text = _is_text_producer(value) or (isinstance(value, ast.Name) and value.id in names)
            if not is_text:
                continue
            for target in _name_targets(statement):
                if target not in names:
                    names.add(target)
                    changed = True
    return names


def _is_text_value(node: ast.AST, names: set[str]) -> bool:
    return _is_text_producer(node) or (isinstance(node, ast.Name) and node.id in names)


def _is_literal_text(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_text_comparison(node: ast.AST, names: set[str]) -> bool:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return False
    left, right = node.left, node.comparators[0]
    operator = node.ops[0]
    if isinstance(operator, (ast.In, ast.NotIn)):
        return _is_literal_text(left) and _is_text_value(right, names)
    if isinstance(operator, (ast.Eq, ast.NotEq)):
        return (_is_literal_text(left) and _is_text_value(right, names)) or (
            _is_text_value(left, names) and _is_literal_text(right)
        )
    return False


def _is_textshape_predicate(node: ast.AST, names: set[str]) -> bool:
    if _is_text_comparison(node, names):
        return True
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        return bool(node.values) and all(_is_textshape_predicate(value, names) for value in node.values)
    return False


def find_textshape_assertions(test_root: Path, *, repo_root: Path) -> list[TextShapeFinding]:
    """Return assertions whose complete predicate is source/text shape only."""

    findings: list[TextShapeFinding] = []
    for path in sorted(test_root.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(repo_root).as_posix()
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or not function.name.startswith("test_"):
                continue
            names = _text_derived_names(function)
            if not names:
                continue
            sequence = 0
            for assertion in ast.walk(function):
                if isinstance(assertion, ast.Assert) and _is_textshape_predicate(assertion.test, names):
                    sequence += 1
                    findings.append(TextShapeFinding(relative, function.name, assertion.lineno, sequence))
    return findings


def assertion_inventory_sha256(findings: list[TextShapeFinding]) -> str:
    """Return the exact current candidate inventory digest for allowlist review."""

    payload = "\n".join(finding.inventory_key for finding in findings).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_allowlist(
    findings: list[TextShapeFinding],
    *,
    expected_inventory_sha256: str | None = None,
) -> list[str]:
    """Return malformed or stale allowlist failures for the current findings."""

    finding_keys = {finding.allowlist_key for finding in findings}
    failures = []
    for key, reason in TEXT_SHAPE_ASSERTION_ALLOWLIST.items():
        if not reason.strip():
            failures.append(f"{key}: allowlist reason must be non-empty")
        if key not in finding_keys:
            failures.append(f"{key}: stale text-shape allowlist entry")
    if expected_inventory_sha256 is not None:
        actual_inventory_sha256 = assertion_inventory_sha256(findings)
        if actual_inventory_sha256 != expected_inventory_sha256:
            failures.append(
                "text-shape assertion inventory changed; review each added or removed candidate and update the allowlist inventory hash"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root")
    parser.add_argument("--test-root", type=Path, help="test directory (defaults to ROOT/tests)")
    parser.add_argument("--report", action="store_true", help="print all candidates, including allowlisted ones")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    test_root = (args.test_root or root / "tests").resolve()
    findings = find_textshape_assertions(test_root, repo_root=root)
    allowlist_failures = validate_allowlist(
        findings,
        expected_inventory_sha256=TEXT_SHAPE_ASSERTION_INVENTORY_SHA256,
    )
    unallowlisted = [finding for finding in findings if finding.allowlist_key not in TEXT_SHAPE_ASSERTION_ALLOWLIST]
    visible = findings if args.report else unallowlisted

    for finding in visible:
        suffix = f" (allowlisted: {TEXT_SHAPE_ASSERTION_ALLOWLIST[finding.allowlist_key]})" if finding.allowlist_key in TEXT_SHAPE_ASSERTION_ALLOWLIST else ""
        print(f"{finding.key}: assertion only checks inspected text/source shape{suffix}")
    for failure in allowlist_failures:
        print(f"allowlist error: {failure}")
    if args.report or unallowlisted or allowlist_failures:
        print(f"text-shape assertions: {len(findings)} candidates, {len(findings) - len(unallowlisted)} allowlisted, {len(unallowlisted)} unallowlisted")
    return 1 if unallowlisted or allowlist_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
