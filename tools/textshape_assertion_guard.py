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
    "tests/test_auth_config.py:test_missing_auth_yaml_creates_commented_starter": "The shipped starter-file comments are the user-facing configuration contract.",
    "tests/test_auth_config.py:test_legacy_placeholder_auth_yaml_is_replaced_with_commented_starter": "The migration writes the same documented starter-file contract.",
    "tests/test_auth_config.py:test_uncommented_auth_yaml_is_active": "The parser's active-versus-commented configuration grammar is intentionally text-defined.",
    "tests/test_auth_config.py:test_setup_auth_script_has_no_parallel_english_status_fallbacks": "The setup script must route status strings through the locale owner rather than duplicate literals.",
    "tests/test_auth_config.py:test_auth_setup_brand_pulses_without_rotating": "The auth-setup stylesheet is the animation contract: the pulse keyframe must remain wired while the retired rotation name stays absent; the rendered no-rotation behavior is also checked in the real browser.",
    "tests/test_auth_config.py:test_pre_auth_pages_share_reload_picker_and_localize_first_paint": "The pre-auth template's shared picker and localization bootstrap are a static first-paint contract.",
    "tests/test_common.py:test_package_and_runtime_versions_use_one_source": "The single version module and package metadata are a source-of-truth contract that cannot be established by runtime behavior alone.",
    "tests/test_browser_editor.py:test_direct_mermaid_sample_real_bundle_keeps_svg_text_labels": "The embedded Mermaid sample is a fixed rendering fixture, not a substitute for browser behavior tests.",
    "tests/test_browser_stats_coverage.py:test_current_stats_fixture_uses_canonical_resolution_policy": "The browser fixture must import the canonical stats resolution policy rather than clone it.",
    "tests/test_dev_restart_script.py:test_legacy_startup_admission_helpers_are_nonblocking": "The retired load and resource predicates must remain absent from the compatibility helpers; runtime assertions separately prove both helpers return immediately and successfully.",
    "tests/test_gate_stats_range.py:test_debug_stats_sample_endpoint_is_not_a_live_client_contract": "The debug endpoint must stay absent from shipped client source.",
    "tests/test_gate_worktree_protection.py:test_docs_keep_gate_state_local_to_nfs_checkouts": "The contributor guide must state that automated gates use host-local temporary storage, fixture-owned state and ports, and avoid the operator server; these are operator instructions rather than runtime behavior.",
    "tests/test_mock_agents.py:test_background_codex_working_animation_reuses_its_status_row": "The mock renderer's static markup reuse is its exact TUI parity contract.",
    "tests/test_mock_agents.py:test_all_mock_timer_paths_obey_the_no_repaint_contract": "The mock timer paths are intentionally enumerated static rendering contracts.",
    "tests/test_tmux_utils.py:test_launchers_declare_the_default_server_intent_for_control_mode": "The launcher default-server opt-in and explicit socket clearing are an audited source contract for machine-wide tmux discovery.",
    "tests/test_settings.py:test_preferences_source_paths_are_in_backend_catalog": "The backend preference catalog is a static source-of-truth mapping.",
    "tests/test_static_build.py:test_markdown_source_change_invalidates_derived_preview_artifacts": "The static builder's dependency declarations are build-system contracts.",
    "tests/test_static_build.py:test_dialog_capacities_have_one_content_relative_token_owner": "Dialog capacity CSS tokens must have one source owner.",
    "tests/test_static_build.py:test_event_rows_use_one_container_responsive_layout_owner": "Responsive event-row layout must use one CSS owner.",
    "tests/test_static_build.py:test_browser_fixture_wait_loops_have_one_injected_owner": "Browser fixture wait loops must use the injected common owner.",
    "tests/test_static_build.py:test_tokenized_component_base_rules_have_no_identical_light_restatements": "Theme CSS must use tokens rather than duplicate light-mode declarations.",
    "tests/test_static_build.py:test_compact_overflow_strips_have_one_shared_layout_owner": "Compact overflow layout must have one shared CSS owner.",
    "tests/test_static_build.py:test_audited_css_families_have_one_grouped_owner": "Audited CSS families intentionally enforce a single grouped owner.",
    "tests/test_static_build.py:test_shared_ui_reuse_protocol_stays_in_agent_guide": "The agent guide owns implementation search and shared-parent rules, while contributor and user guides must not duplicate the UI ownership map.",
    "tests/test_static_build.py:test_panel_frame_builder_owns_the_shared_panel_chrome": "Shared panel chrome must route through the sole frame builder.",
    "tests/test_static_build.py:test_node_shard_launcher_has_unique_behavior_owners_and_a_terminal_summary": "The node shard launcher requires static unique-owner and terminal-summary wiring.",
    "tests/test_static_build.py:test_opaque_white_has_one_css_paint_owner": "Opaque white paint must have one tokenized CSS owner.",
    "tests/test_static_build.py:test_tree_row_hover_and_selection_paint_have_one_base_owner": "Tree hover and selection paint must have one base CSS owner.",
    "tests/test_text_client_common_metadata.py:test_text_clients_use_shared_agent_comms_primitives": "Text clients must import the shared communication primitives rather than fork them.",
    "tests/test_yostats_active_browser_window.py:test_active_browser_window_workload_source_contract": "The operator capture tool's workload and measurement setup is an audited static contract; authentication is exercised separately.",
    "tests/test_yostats_active_browser_window.py:test_benchmark_child_runs_in_its_own_process_group_and_is_group_stopped": "The benchmark subprocess group boundary is an operator cleanup contract.",
    "tests/test_yostats_active_browser_window.py:test_deterministic_operation_batch_quiesces_existing_operations_before_freezing_ack_flushes": "The frozen operator workload must quiesce the pre-existing operation and acknowledgement owners before suppressing the batch timer, then reject missing, unrelated, ignored, or retained acknowledgement IDs. The live 75-second run proves the behavior; this source contract pins the ordering inside the injected browser script that a Python fake driver cannot execute.",
    "tests/test_launcher_row_wiring.py:test_launcher_has_no_inherited_global_primary_port_export": "The retired global scheduler primary-port export, the Darwin launchctl setenv, and the two dropped early/managed instance-port vars must remain ABSENT from the live launcher; a negative source search is the only way to prove a removed global env export never returns, and the launcher starts real dev servers so it cannot be exercised at runtime here.",
    "tests/test_launcher_row_wiring.py:test_launcher_captures_one_row_plan_and_reuses_it_for_server_and_both_probes": "The supported bash launcher's per-row wiring is a source contract: one plan captured per row and reused for the server launch and BOTH authenticated probes, bound to the unique listener PID, with no drift. The launcher spawns real dev servers, so its script wiring cannot be unit-run here; the exec-mode and owner-probe primitives are separately proven at runtime by test_exec_launched_server_passes_the_real_owner_probe_and_tears_down_by_pid, but the launcher script itself (plan capture, sessions probe, per-row reuse) is only checkable as source.",
    "tests/test_launcher_row_wiring.py:test_launcher_macos_carries_the_plan_through_the_shared_launcher": "The macOS launcher row carries the captured plan through the shared launcher and injects a primary only for the default/durable row; Darwin execution is deferred (W14) and cannot run on this Linux host, so the macOS launch wiring is a source-only contract.",
    "tests/test_launcher_row_wiring.py:test_shared_macos_launcher_execs_plan_when_present_and_stays_direct_otherwise": "The one shared macOS launcher must route through the exec plan when a row plan is present and keep the historical direct exec otherwise, so the supported launcher and boot.sh never drift into two macOS launch copies; this single-owner branch is macOS-only and checkable only as source on this host.",
    "tests/test_check_runner.py:test_run_tests_sh_stamps_the_owner_label_only_when_a_run_token_is_present": "docker/run-tests.sh must stamp each container it launches with this run's ownership token (YOLOMUX_CHECK_RUN_TOKEN via the CONTAINER_OWNER_LABEL --label, threaded into the real docker run). The label lives in a shell script that cannot be exercised without a real docker run here, so pinning that exact wiring is the only way to keep W10's run-owned-container retirement from silently regressing to image-wide discovery; container ownership is proven behaviourally elsewhere.",
    "tests/test_check_runner.py:test_run_tests_sh_keeps_go_and_writer_state_inside_each_container": "The Docker runner's read-only source mount, network isolation, private Go binary mount, and per-container writer slot are source contracts; the runner integration is exercised in Docker, while these assertions prevent later yo7222 alignment from reintroducing host mounts or shared mutable state.",
    "tests/test_check_runner.py:test_run_tests_sh_preserves_the_image_non_root_user_for_rootless_docker": "Rootless Docker must preserve the image's non-root identity instead of forcing UID 0; the source assertions protect this security boundary, while the Dockerized runner and rootless preflight tests exercise the actual launch path.",
    "tests/test_check_runner.py:test_dockerfile_keeps_expensive_dependency_layers_above_go_source_copies": "The Dockerfile's dependency-layer ordering is a build-cache contract: the dependency installation must precede Go source copies and consume the exact lock; the image build and Bazel Docker tests exercise the build, while this source check prevents accidental cache invalidation regressions.",
    "tests/test_check_runner.py:test_docker_test_os_dependencies_and_go_toolchain_are_pinned": "The Docker image's Debian snapshot, OS package versions, and verified Go and Bazel archives are reproducible build inputs; the pinned source contract complements the actual image build and Dockerized Bazel execution.",
    "tests/test_check_runner.py:test_bazel_browser_and_agent_wrappers_use_the_shared_action_environment": "The three Bazel browser and agent entry points must all install the same action-owned HOME, client roots, runtime directory, and short temporary directory before launching tests; the shared helper's runtime behavior is exercised in Docker, while this source contract prevents one wrapper from silently drifting to inherited host state.",
    "tests/test_check_runner.py:test_docker_backed_bazel_wrappers_prepare_only_inside_the_disposable_container": "The two outer Docker-backed integration wrappers must not regain a nested host/container branch; the real wrappers execute in the canonical Bazel integration lane, and this source contract catches the forbidden second owner.",
    "tests/test_check_runner.py:test_module_bazel_uses_pinned_sdk_and_supported_platform_checksums": "The module's SDK archive names, checksums, direct rule modules, and WORKSPACE retirement are source-controlled pins; the Linux archive and offline module graph are exercised by the canonical Bazel suite, while Darwin archives require their native host.",
    "tests/test_check_runner.py:test_bazel_runner_uses_a_persistent_action_cache_and_private_output_roots": "The persistent cache partition and private output-root wiring define cross-invocation ownership; Docker integration exercises the cache, while this structural contract prevents shared execution state from returning.",
    "tests/test_check_runner.py:test_bazel_runtime_trees_are_consumer_scoped_and_frontend_is_generated": "Runfiles ownership and generated frontend outputs are Bazel graph contracts; the graph is consumed by the canonical Go and browser targets, and this assertion guards accidental source-group widening or checkout generation.",
    "tests/test_check_runner.py:test_go_gate_excludes_deprecated_backend_comparison": "The canonical Go gate and Go browser lane must not select deprecated Python-versus-Go implementation comparisons.",
    "tests/test_check_runner.py:test_bazel_stats_integration_has_explicit_normal_and_race_owners": "The normal and race agent-status integration targets are deliberate Bazel ownership entries; the actual targets are executed in the canonical Dockerized suite, while this source contract prevents one mode from disappearing from the test graph.",
    "tests/test_browser_layout.py:test_static_browser_fixtures_have_one_write_and_navigation_owner": "The static browser fixture must keep one write/navigation owner; runtime scenarios exercise the behavior separately.",
}

# A function-level reason covers related assertions in that one source-contract
# test. Pin the complete current assertion inventory as well, so adding a new
# text-shape assertion to an already allowlisted function still fails the guard
# until a reviewer deliberately updates this value and its reason. The sequence
# is stable when unrelated code moves an assertion to a different source line.
TEXT_SHAPE_ASSERTION_INVENTORY_SHA256: Final[str] = "2a1c5a3cea068a76aa68dea6784ce106613cd7d0c46a7336035943265152d409"


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
