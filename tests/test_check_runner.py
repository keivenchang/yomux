# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import ast
from contextlib import contextmanager
import errno
import hashlib
import importlib.util
import json
import math
import multiprocessing
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import latency_calibration
from tests.browser_helpers import webdriver_lease
from tests.source_inventory import parsed_python_source
from tests.source_inventory import python_source_paths
from tools import static_build
from tools import instance_isolation
from tools import test_catalog
from tools import test_plan
from tools import pytest_catalog_plugin
from tools.test_catalog import discover_pytest_phase_files
from tools.tool_guard import container_command_with_host_tool_guard
from yolomux_lib.background_owner import pid_is_alive as background_owner_pid_is_alive
from yolomux_lib.stats_current import service as service_module


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_PATH = REPO_ROOT / "tools" / "check.py"


def load_check_module():
    spec = importlib.util.spec_from_file_location("yolomux_check", CHECK_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_python_imports_are_module_scoped():
    roots = [
        REPO_ROOT / "yolomux.py",
        REPO_ROOT / "yolomux_lib",
        REPO_ROOT / "tools",
        REPO_ROOT / "tests",
    ]
    paths = [path for root in roots for path in python_source_paths(str(root))]
    violations = []
    for path in paths:
        _source, tree = parsed_python_source(path)
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent = parents.get(parent)
            if parent is not None:
                violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {ast.unparse(node)}")
    assert violations == []


def test_runtime_and_tool_function_bodies_are_not_exact_duplicates():
    functions = {}
    duplicates = []
    for root in (REPO_ROOT / "yolomux_lib", REPO_ROOT / "tools"):
        for path in python_source_paths(str(root)):
            _source, tree = parsed_python_source(path)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or len(node.body) < 2:
                    continue
                body = ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False)
                location = f"{path.relative_to(REPO_ROOT)}:{node.lineno}:{node.name}"
                if body in functions:
                    duplicates.append((functions[body], location))
                else:
                    functions[body] = location
    assert duplicates == []


def test_check_runner_reuses_background_owner_process_liveness():
    check = load_check_module()
    assert check.pid_is_alive is background_owner_pid_is_alive


def test_check_lock_is_one_per_user_across_worktrees_and_tmpdirs(monkeypatch, tmp_path):
    monkeypatch.delenv("YOLOMUX_TOOL_LOCK_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    check = load_check_module()
    assert check.DEFAULT_TOOL_LOCK_PATH == Path.home() / ".cache" / "yolomux" / "expensive-tools.lock"


def test_parent_check_lock_ownership_is_inherited_by_pytest(monkeypatch, tmp_path):
    check = load_check_module()
    lock_path = tmp_path / "shared" / "expensive-tools.lock"
    command = ["python3", "-m", "pytest"]
    monkeypatch.delenv("YOLOMUX_CHECK_TOOL_LOCK_OWNER", raising=False)

    with check.expensive_tool_lock(lock_path=lock_path):
        assert os.environ["YOLOMUX_CHECK_TOOL_LOCK_OWNER"] == f"{os.getpid()}:{lock_path}"
        assert container_command_with_host_tool_guard(
            command,
            lock_path=lock_path,
            collect_only=False,
            environ=dict(os.environ),
            parent_pid=os.getpid(),
        ) == command
        assert container_command_with_host_tool_guard(
            command,
            lock_path=lock_path,
            collect_only=False,
            environ=dict(os.environ),
            parent_pid=os.getpid() + 1,
        ) == ["flock", str(lock_path), *command]

    assert "YOLOMUX_CHECK_TOOL_LOCK_OWNER" not in os.environ


def test_default_check_lanes_keep_full_pytest_gate():
    check = load_check_module()
    nonbrowser_workers, browser_workers, e2e_workers = check.pytest_worker_counts()
    lanes = check.lanes()
    default_names = [lane.name for lane in lanes if lane.default]
    assert default_names == ["py-compile", "static", "node-syntax", "node-layout", "pytest", "pytest-browser", "pytest-e2e", "pytest-gate-serial", "whitespace"]
    static_lane = next(lane for lane in lanes if lane.name == "static")
    assert static_lane.steps == (
        check.Step("static_build --check", ["python3", "tools/static_build.py", "--check"]),
        check.Step("textshape_assertion_guard", ["python3", "tools/textshape_assertion_guard.py"]),
        check.Step("architecture budgets", ["python3", "tools/architecture_budgets.py"]),
        check.Step("local-service type gate", ["python3", "tools/check_local_service_types.py"]),
    )
    pytest_lane = next(lane for lane in lanes if lane.name == "pytest")
    # The default pytest lane runs the full suite EXCEPT node_bridge and e2e: test_node_suite.py shells
    # out to the same launcher the always-on node-layout lane runs (so including
    # it ran that ~20s node suite twice concurrently), e2e tests launch real tmux + mock agents, and
    # browser tests need Selenium/Chrome. Each slow class has its own default lane so failures name the
    # failing subsystem instead of hiding under "pytest full".
    assert pytest_lane.steps[0].args == ["python3", "-m", "pytest", *check.pytest_files("nonbrowser"), "-n", nonbrowser_workers, "-m", "not node_bridge and not gate_serial and not e2e and not browser", "-q"]
    assert check.MOCK_TRANSCRIPT_FILES == ("tests/test_mock_transcripts.py",)
    assert set(check.MOCK_TRANSCRIPT_FILES).issubset(check.pytest_files("nonbrowser"))
    node_lane = next(lane for lane in lanes if lane.name == "node-layout")
    assert node_lane.steps[0].args == ["node", "tests/layout_url.test.js", *check.NODE_LAYOUT_FILES]
    # The lane passes these as argv, which OVERRIDES the launcher's own default list, so a shard
    # missing here runs in no gate lane at all: test_node_suite.py runs the bare launcher but carries
    # the node_bridge marker the default pytest lane excludes.
    assert "tests/cross_surface_state.test.js" in check.NODE_LAYOUT_FILES
    assert "tests/gate_panels.test.js" not in check.NODE_LAYOUT_FILES
    boot_lane = next(lane for lane in lanes if lane.name == "pytest-boot")
    assert boot_lane.default is False
    assert boot_lane.steps[0].args == ["python3", "-m", "pytest", *check.pytest_files("boot"), "-m", "boot", "-q"]
    browser_lane = next(lane for lane in lanes if lane.name == "pytest-browser")
    assert browser_lane.default is True
    assert browser_lane.steps[0].args == ["python3", "-m", "pytest", *check.pytest_files("boot"), "-m", "boot", "-q"]
    assert browser_lane.steps[1].args == ["python3", "-m", "pytest", *check.pytest_files("browser"), "-n", browser_workers, "--dist", "worksteal", "-m", "browser and not e2e and not boot and not visual_golden", "-q"]
    assert browser_lane.steps[2].args == ["python3", "-m", "pytest", *check.pytest_files("golden"), "-m", "visual_golden", "-q"]
    e2e_lane = next(lane for lane in lanes if lane.name == "pytest-e2e")
    assert e2e_lane.default is True
    assert e2e_lane.steps[0].args == ["python3", "-m", "pytest", *check.pytest_files("e2e"), "-n", e2e_workers, "-m", "e2e", "-q"]
    assert "pytest-unit" not in {lane.name for lane in lanes}
    assert "pytest-socket" not in {lane.name for lane in lanes}


def test_every_node_shard_runs_in_the_gate_unless_one_owner_excludes_it():
    """A shard may leave the gate only through NODE_LAYOUT_EXCLUDED_FILES, in both list owners.

    The gate lane passes NODE_LAYOUT_FILES as argv and the launcher applies
    defaultGateExcludedSuiteFiles only when run bare, so these were two hand-maintained copies of one
    list. They drifted: share_theme.test.js and share_file_surface_replay.test.js ran in neither
    default lane while the gate still reported green.
    """

    shards = {path.name for path in (REPO_ROOT / "tests").glob("*.test.js")}
    shards.discard(Path(test_catalog.NODE_SHARD_LAUNCHER).name)
    excluded = set(test_catalog.NODE_LAYOUT_EXCLUDED_FILES)

    assert set(test_catalog.NODE_LAYOUT_FILES) == {f"tests/{name}" for name in shards} - excluded
    assert excluded <= {f"tests/{name}" for name in shards}, "excluding a shard that does not exist"

    launcher = (REPO_ROOT / "tests" / "layout_url.test.js").read_text(encoding="utf-8")
    _all_suite_files, excluded_block = launcher.split("const defaultGateExcludedSuiteFiles", 1)
    launcher_excluded = set(re.findall(r"'(tests/[^']+\.test\.js)'", excluded_block.split("]);", 1)[0]))
    assert launcher_excluded == excluded, "the launcher and the check catalog exclude different shards"


def test_focused_pytest_lanes_retain_only_nonduplicated_entry_points():
    check = load_check_module()
    lanes = {lane.name: lane for lane in check.lanes()}
    assert "pytest-unit" not in lanes
    assert "pytest-socket" not in lanes
    assert "pytest-browser-behavior" not in lanes
    assert lanes["pytest-boot"].steps[0].args == [
        "python3",
        "-m",
        "pytest",
        *check.pytest_files("boot"),
        "-m",
        "boot",
        "-q",
    ]
    assert lanes["pytest-boot"].steps[0] is lanes["pytest-browser"].steps[0]

def test_lane_specs_are_the_one_owner_of_names_defaults_and_shared_steps():
    check = load_check_module()
    built = check.lanes()
    assert [(lane.name, lane.label, lane.default) for lane in built] == [
        (spec.name, spec.label, spec.default) for spec in test_plan.LANE_SPECS
    ]
    browser_spec = test_plan.lane_spec("pytest-browser")
    assert browser_spec.prerequisites == ("pytest-boot",)
    assert browser_spec.phases == ("boot", "browser", "golden")
    assert browser_spec.worker_class == "pytest-mixed"
    assert test_plan.resolved_lane_step_ids(browser_spec) == (
        "pytest-boot",
        "pytest-browser",
        "pytest-browser-golden",
    )
    browser = next(lane for lane in built if lane.name == "pytest-browser")
    boot = next(lane for lane in built if lane.name == "pytest-boot")
    timing = next(lane for lane in built if lane.name == "pytest-gate-serial")
    assert browser.steps[0] is boot.steps[0]
    assert timing.run_last is True
    assert timing.steps[0].args == [
        "python3",
        "-m",
        "pytest",
        *test_catalog.PYTEST_PHASE_FILES["gate_serial"],
        "-m",
        "gate_serial",
        "-q",
    ]
    gate_serial_nodes = {
        nodeid
        for relative in test_catalog.PYTEST_PHASE_FILES["gate_serial"]
        for nodeid, phase in test_catalog.test_definitions(REPO_ROOT / relative)
        if phase == "gate_serial"
    }
    assert gate_serial_nodes == {
        "tests/test_client_event_broker_backlog_disambiguation.py::test_enqueue_to_dequeue_and_sse_send_are_not_the_backlog_owner",
        "tests/test_client_event_broker_backlog_disambiguation.py::test_independent_publishers_can_serialize_events_concurrently",
        "tests/test_client_event_broker_backlog_disambiguation.py::test_payload_serialization_never_runs_inside_the_ordering_critical_section",
        "tests/test_client_event_broker_backlog_disambiguation.py::test_the_broker_lock_genuinely_serializes_every_concurrent_publisher",
        "tests/test_gate_tmux.py::test_gate_d7_kill_session_api_returns_promptly_and_removes_scoped_session",
        "tests/test_hot_path_owner.py::test_churn_abandon_and_restart_leaves_no_deleted_fds_and_one_generation",
        "tests/test_batchd.py::test_fs_batch_completion_holds_a_batchd_lease_across_the_broker_idle_window",
        "tests/test_batchd.py::test_batchd_control_plane_is_ready_before_blocked_data_plane_setup",
        "tests/test_batchd.py::test_zero_wait_produce_returns_a_browser_opaque_byte_product_without_a_relay",
        "tests/test_stats_current_service_performance.py::test_batched_recording_holds_the_joint_cost_ceilings",
        "tests/test_stats_current_service_performance.py::test_per_fact_commits_breach_the_ceiling_that_batching_clears",
        "tests/test_stats_current_service_performance.py::test_recording_facts_holds_the_joint_cost_ceilings_on_a_production_store",
        "tests/test_stats_current_service_performance.py::test_the_cost_gate_states_the_smallest_regression_it_can_resolve",
        "tests/test_stats_current_service_performance.py::test_the_probe_publishes_a_ring_head_before_it_measures",
        "tests/test_stats_current_service_performance.py::test_the_probe_still_covers_the_products_real_append_path",
        "tests/test_stats_current_service_performance.py::test_whole_history_cold_start_breaches_the_peak_ceiling",
    }

    default_step_owners = {}
    for spec in test_plan.LANE_SPECS:
        if not spec.default:
            continue
        for step_id in test_plan.resolved_lane_step_ids(spec):
            assert step_id not in default_step_owners, (
                step_id,
                default_step_owners.get(step_id),
                spec.name,
            )
            default_step_owners[step_id] = spec.name


def test_lane_spec_prerequisite_cycle_fails_closed(monkeypatch):
    cyclic = (
        test_plan.LaneSpec("first", "first", (), prerequisites=("second",)),
        test_plan.LaneSpec("second", "second", (), prerequisites=("first",)),
    )
    monkeypatch.setattr(test_plan, "LANE_SPECS", cyclic)
    with pytest.raises(ValueError, match="prerequisite cycle"):
        test_plan.resolved_lane_step_ids(cyclic[0])


def test_lane_registry_references_are_typed_total_and_fail_closed(monkeypatch):
    check = load_check_module()
    catalog = check.step_catalog()
    original_specs = test_plan.LANE_SPECS
    assert set(catalog) == set(test_plan.StepId)
    test_plan.validate_lane_specs(catalog)

    missing = dict(catalog)
    missing.pop(test_plan.StepId.WHITESPACE)
    with pytest.raises(ValueError, match="missing executable step IDs: whitespace"):
        test_plan.validate_lane_specs(missing)

    with pytest.raises(ValueError, match="duplicate step ID in executable catalog"):
        test_plan.validate_lane_specs([*catalog, test_plan.StepId.WHITESPACE])

    with pytest.raises(ValueError, match="extra executable step IDs: invented"):
        test_plan.validate_lane_specs([*catalog, "invented"])

    duplicated = (*test_plan.LANE_SPECS, test_plan.LaneSpec("second-whitespace", "second", (test_plan.StepId.WHITESPACE,)))
    monkeypatch.setattr(test_plan, "LANE_SPECS", duplicated)
    with pytest.raises(ValueError, match="duplicate step ID whitespace"):
        test_plan.validate_lane_specs(catalog)

    unknown_step = (test_plan.LaneSpec("unknown", "unknown", ("invented",)),)
    monkeypatch.setattr(test_plan, "LANE_SPECS", unknown_step)
    with pytest.raises(ValueError, match="unknown step ID in lane unknown"):
        test_plan.validate_lane_specs(catalog)

    drifted = tuple(
        replace(spec, phases=("browser",)) if spec.name == "pytest" else spec
        for spec in original_specs
    )
    monkeypatch.setattr(test_plan, "LANE_SPECS", drifted)
    with pytest.raises(ValueError, match="test phase drift in lane pytest"):
        test_plan.validate_lane_specs(catalog)


def test_browser_capability_preflight_names_missing_dependency_browser_and_driver(monkeypatch, tmp_path):
    check = load_check_module()
    monkeypatch.setattr(check.docker_image, "container_available", lambda _root: (False, "host-only test"))
    monkeypatch.setattr(check.importlib.util, "find_spec", lambda _name: None)
    assert check.browser_capability_preflight().component == "dependency"

    monkeypatch.setattr(check.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(check.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(check.shutil, "which", lambda _name: None)
    monkeypatch.setattr(check.Path, "is_file", lambda _path: False)
    assert check.browser_capability_preflight().component == "browser"

    browser = tmp_path / "chrome"
    browser.write_text("", encoding="utf-8")
    monkeypatch.undo()
    monkeypatch.setattr(check.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(check.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        check.shutil,
        "which",
        lambda name: str(browser) if name == "google-chrome" else None,
    )
    assert check.browser_capability_preflight().component == "driver"


def test_browser_capability_preflight_runs_in_the_container_execution_environment(monkeypatch):
    check = load_check_module()
    monkeypatch.setattr(check.docker_image, "container_available", lambda _root: (True, "docker"))
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=check.BrowserCapabilityDiagnostic(True, "ready", "container capabilities").json_text() + "\n",
        stderr="",
    )
    monkeypatch.setattr(check.subprocess, "run", lambda *args, **kwargs: completed)

    diagnostic = check.browser_capability_preflight()

    assert diagnostic == check.BrowserCapabilityDiagnostic(True, "ready", "container capabilities")


def test_browser_capability_preflight_reports_container_probe_failure(monkeypatch):
    check = load_check_module()
    monkeypatch.setattr(check.docker_image, "container_available", lambda _root: (True, "docker"))
    completed = subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="docker probe failed\n")
    monkeypatch.setattr(check.subprocess, "run", lambda *args, **kwargs: completed)

    diagnostic = check.browser_capability_preflight()

    assert diagnostic.component == "environment"
    assert diagnostic.detail == "isolated test environment probe failed: docker probe failed"


def test_requested_browser_lane_preflight_refuses_before_any_step(monkeypatch, capsys):
    check = load_check_module()
    monkeypatch.setattr(
        check,
        "browser_capability_preflight",
        lambda: check.BrowserCapabilityDiagnostic(False, "driver", "missing exact driver"),
    )
    monkeypatch.setattr(check, "run_parallel", lambda _lanes: pytest.fail("lane must not start"))
    assert check.main(["--lane", "pytest-browser", "--no-tool-guard"]) == check.EXIT_LANE_FAILED
    assert "BROWSER PREFLIGHT FAILED [driver]: missing exact driver" in capsys.readouterr().err


def test_catalog_and_collection_share_filename_marker_owner():
    assert test_catalog.automatic_test_markers is test_plan.automatic_test_markers
    assert test_catalog.PHASE_MARKER_PRECEDENCE is test_plan.PHASE_MARKER_PRECEDENCE
    assert test_plan.automatic_test_markers("test_browser_new.py") == ("browser", "socket")
    assert test_plan.automatic_test_markers("test_new.py") == ()


def test_check_runner_scales_one_concurrent_pytest_budget_from_schedulable_cpus(monkeypatch):
    check = load_check_module()
    monkeypatch.delenv("YOLOMUX_PYTEST_WORKERS", raising=False)
    monkeypatch.delenv("YOLOMUX_CHECK_CPU_PERCENT", raising=False)
    monkeypatch.setattr(check.platform, "system", lambda: "Linux")

    # Linux and macOS both leave half the host for live servers and agent work.
    expected = {
        4: ("1", "1", "1"),
        10: ("2", "1", "2"),
        14: ("3", "2", "2"),
        32: ("8", "5", "3"),
    }
    for cores, counts in expected.items():
        monkeypatch.setattr(check.os, "cpu_count", lambda: 64)
        monkeypatch.setattr(check.os, "sched_getaffinity", lambda _pid, cores=cores: set(range(cores)))
        assert check.pytest_worker_counts() == counts

    monkeypatch.setenv("YOLOMUX_PYTEST_WORKERS", "5,3,1")
    assert check.pytest_worker_counts() == ("5", "3", "1")

    # The platform no longer changes the shared-box default.
    monkeypatch.delenv("YOLOMUX_PYTEST_WORKERS", raising=False)
    monkeypatch.setattr(check.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(check.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(check.os, "sched_getaffinity", lambda _pid: set(range(8)))
    assert check.pytest_worker_counts() == ("2", "1", "1")

    monkeypatch.setattr(check.os, "sched_getaffinity", lambda _pid: (_ for _ in ()).throw(OSError("unavailable")))
    assert check.pytest_worker_counts() == ("2", "1", "1")


def test_check_runner_cpu_percent_knob_tunes_the_worker_budget(monkeypatch):
    check = load_check_module()
    monkeypatch.delenv("YOLOMUX_PYTEST_WORKERS", raising=False)
    monkeypatch.delenv("YOLOMUX_CHECK_CPU_PERCENT", raising=False)
    monkeypatch.setattr(check.platform, "system", lambda: "Linux")
    monkeypatch.setattr(check.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(check.os, "sched_getaffinity", lambda _pid: set(range(32)))

    # Explicit argument (the --cpu-percent flag path) wins.
    assert check.pytest_worker_counts(cpu_percent=50) == ("8", "5", "3")
    assert check.pytest_worker_counts(cpu_percent=100) == ("16", "10", "6")

    # Environment knob applies when no explicit argument is given.
    monkeypatch.setenv("YOLOMUX_CHECK_CPU_PERCENT", "25")
    assert check.pytest_worker_counts() == ("4", "2", "2")

    # The floor keeps every pool alive at tiny percentages.
    monkeypatch.setenv("YOLOMUX_CHECK_CPU_PERCENT", "1")
    assert check.pytest_worker_counts() == ("1", "1", "1")

    # Serial still forces one worker per pool regardless of percent.
    assert check.pytest_worker_counts(serial=True, cpu_percent=100) == ("1", "1", "1")

    for invalid in ("0", "101", "abc", "-5"):
        monkeypatch.setenv("YOLOMUX_CHECK_CPU_PERCENT", invalid)
        with pytest.raises(ValueError):
            check.pytest_worker_counts()


def test_check_runner_launches_slowest_lanes_first(monkeypatch):
    check = load_check_module()
    monkeypatch.delenv("YOLOMUX_PYTEST_WORKERS", raising=False)
    monkeypatch.delenv("YOLOMUX_CHECK_CPU_PERCENT", raising=False)

    default_lanes = [lane for lane in check.lanes() if lane.default]
    ordered = [lane.name for lane in check.slowest_first(default_lanes)]

    assert ordered[0] == "pytest-browser"
    assert ordered[1] == "pytest-e2e"
    assert ordered[2] == "pytest"
    assert ordered[-2] == "whitespace"
    assert ordered[-1] == "pytest-gate-serial"
    # Every default lane survives the reordering exactly once.
    assert sorted(ordered) == sorted(lane.name for lane in default_lanes)
    events = []
    parallel_lane = check.Lane("parallel", "parallel", ())
    final_lane = check.Lane("timing", "timing", (), run_last=True)

    def run(mode):
        def record(selected, **_kwargs):
            events.append((mode, [lane.name for lane in selected]))
            return [check.LaneResult(lane.name, lane.label, True, 0.0, "") for lane in selected]
        return record

    monkeypatch.setattr(check, "run_parallel", run("parallel"))
    monkeypatch.setattr(check, "run_serial", run("serial"))
    results = check.run_functional_lanes([parallel_lane, final_lane], serial=False)
    assert events == [("parallel", ["parallel"]), ("serial", ["timing"])]
    assert [result.name for result in results] == ["parallel", "timing"]


def test_serial_check_gate_forces_every_pytest_pool_to_one_worker(monkeypatch):
    check = load_check_module()
    monkeypatch.setenv("YOLOMUX_PYTEST_WORKERS", "8,8,8")

    pytest_steps = {
        lane.name: lane.steps
        for lane in check.lanes(serial=True)
        if lane.name in {"pytest", "pytest-browser", "pytest-e2e"}
    }
    assert check.pytest_worker_counts(serial=True) == ("1", "1", "1")
    for steps in pytest_steps.values():
        for step in steps:
            assert "-n" not in step.args
            assert "--dist" not in step.args


def test_expensive_tool_lock_refuses_independent_contender_without_queueing(tmp_path):
    lock_path = tmp_path / "shared" / "expensive-tools.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import importlib.util,time;"
                f"s=importlib.util.spec_from_file_location('check_holder',{str(CHECK_PATH)!r});"
                "m=importlib.util.module_from_spec(s);"
                "__import__('sys').modules[s.name]=m;s.loader.exec_module(m);"
                f"c=m.expensive_tool_lock(lock_path=m.Path({str(lock_path)!r}));"
                "c.__enter__();print('locked',flush=True);time.sleep(5);c.__exit__(None,None,None)"
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        check = load_check_module()
        try:
            with check.expensive_tool_lock(lock_path=lock_path):
                raise AssertionError("contender unexpectedly acquired the lock")
        except check.ToolGuardBusy:
            pass
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def test_canonical_catalog_covers_every_collected_node_once(tmp_path):
    test_config = sys.modules["conftest"]
    output_path = tmp_path / "collection.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "tools.pytest_catalog_plugin",
            f"--yolomux-catalog-output={output_path}",
            "tests",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    collection_rows = json.loads(output_path.read_text(encoding="utf-8"))
    collected = {row["nodeid"] for row in collection_rows}
    collected_base = {nodeid.split("[", 1)[0] for nodeid in collected}
    missing = [nodeid for nodeid in test_config.SLOWEST_FIRST_TESTS if nodeid not in collected_base]
    assert missing == []
    check = load_check_module()
    catalog_files = {phase: set(paths) for phase, paths in check.PYTEST_PHASE_FILES.items()}
    phase_rows = {phase: [] for phase in catalog_files}
    for row in collection_rows:
        phase = test_plan.phase_for_markers(set(row["markers"]))
        phase_rows[phase].append(row["nodeid"])
    ownership_errors = []
    for phase, nodeids in phase_rows.items():
        for nodeid in nodeids:
            path = nodeid.split("::", 1)[0]
            if path not in catalog_files[phase]:
                ownership_errors.append(f"{nodeid} belongs to {phase}, but {path} is absent from its catalog")
    assert ownership_errors == []
    assert set().union(*map(set, phase_rows.values())) == collected
    for phase, rows in phase_rows.items():
        owner_order = list(dict.fromkeys(nodeid.split("::", 1)[0] for nodeid in rows))
        assert owner_order == check.pytest_files(phase)


def test_pytest_catalog_discovers_new_files_and_mixed_phases(tmp_path):
    test_root = tmp_path / "tests"
    test_root.mkdir()
    mixed_path = test_root / "test_new_contract.py"
    mixed_path.write_text(
        "def test_unit():\n"
        "    pass\n\n"
        "@pytest.mark.browser\n"
        "def test_browser():\n"
        "    pass\n\n"
        "@pytest.mark.e2e\n"
        "def test_e2e():\n"
        "    pass\n",
        encoding="utf-8",
    )
    automatic_browser_path = test_root / "test_browser_new_surface.py"
    automatic_browser_path.write_text("def test_surface():\n    pass\n", encoding="utf-8")

    catalog = discover_pytest_phase_files(test_root, repo_root=tmp_path)

    assert "tests/test_new_contract.py" in catalog["nonbrowser"]
    assert "tests/test_new_contract.py" in catalog["browser"]
    assert "tests/test_new_contract.py" in catalog["e2e"]
    assert "tests/test_browser_new_surface.py" in catalog["browser"]
    assert "tests/test_browser_new_surface.py" not in catalog["nonbrowser"]


def test_pytest_catalog_ignores_marker_values_inside_parametrize_arguments(tmp_path):
    test_root = tmp_path / "tests"
    test_root.mkdir()
    path = test_root / "test_marker_values.py"
    path.write_text(
        "import pytest\n\n"
        "@pytest.mark.parametrize('marker', [pytest.mark.browser.mark, pytest.mark.no_browser.mark])\n"
        "def test_marker_value(marker): pass\n",
        encoding="utf-8",
    )

    assert test_catalog.test_definitions(path, repo_root=tmp_path) == (
        ("tests/test_marker_values.py::test_marker_value", "nonbrowser"),
    )
    assert discover_pytest_phase_files(test_root, repo_root=tmp_path)["browser"] == ()


def test_phase_catalog_and_runtime_share_slowest_first_owner_order(tmp_path):
    test_root = tmp_path / "tests"
    test_root.mkdir()
    for name, body in {
        "test_browser_heavy.py": "import pytest\npytestmark = pytest.mark.browser\ndef test_generated_surface_matrix(): pass\ndef test_after(): pass\n",
        "test_browser_dockview.py": "import pytest\npytestmark = pytest.mark.browser\ndef test_dockview_wrapped_tab_rows_share_one_control_reserved_flex_grid(): pass\ndef test_differ_reopen_keeps_dragged_file_tab_home(): pass\n",
        "test_plain.py": "def test_plain(): pass\n",
    }.items():
        (test_root / name).write_text(body, encoding="utf-8")

    catalog = discover_pytest_phase_files(test_root, repo_root=tmp_path)

    assert catalog["browser"] == (
        "tests/test_browser_dockview.py",
        "tests/test_browser_heavy.py",
    )
    assert catalog["nonbrowser"] == ("tests/test_plain.py",)


def test_non_drag_browser_actions_use_the_shared_fast_pointer_helper():
    paths = [
        REPO_ROOT / "tests" / "test_browser_layout.py",
        REPO_ROOT / "tests" / "test_browser_dockview.py",
    ]
    direct_uses = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        direct_uses.extend((path.name, line) for line in source.splitlines() if "ActionChains(browser)" in line)
    assert direct_uses == []


def test_active_yolomux_server_records_uses_generation_heartbeats(monkeypatch, tmp_path):
    check = load_check_module()
    generations_dir = tmp_path / "background-owner" / "generations"
    generations_dir.mkdir(parents=True)
    (generations_dir / "live.json").write_text(json.dumps({"pid": 100, "last_heartbeat": 50.0, "port": 8002}), encoding="utf-8")
    (generations_dir / "stale.json").write_text(json.dumps({"pid": 101, "last_heartbeat": 10.0, "port": 8001}), encoding="utf-8")
    (generations_dir / "dead.json").write_text(json.dumps({"pid": 102, "last_heartbeat": 50.0, "port": 8003}), encoding="utf-8")
    (generations_dir / "bad.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(check, "pid_is_alive", lambda pid: pid != 102)

    records = check.active_yolomux_server_records(state_dir=tmp_path, now=55.0, stale_seconds=30.0)

    assert records == [{"pid": 100, "last_heartbeat": 50.0, "port": 8002}]


def test_default_check_gate_uses_guard_and_lowers_priority_when_servers_are_active(monkeypatch, capsys):
    check = load_check_module()
    events = []

    @contextmanager
    def fake_expensive_tool_lock(enabled=True, lock_path=check.DEFAULT_TOOL_LOCK_PATH):
        events.append(("lock", enabled, lock_path))
        yield enabled

    def fake_run_parallel(selected, *, output_root=None):
        events.append(("run", [lane.name for lane in selected]))
        return [check.LaneResult(lane.name, lane.label, True, 0.0, "") for lane in selected]

    def fake_run_serial(selected, *, output_root=None):
        events.append(("serial", [lane.name for lane in selected]))
        return [check.LaneResult(lane.name, lane.label, True, 0.0, "") for lane in selected]

    def fake_certification_phase(*, evidence_dir, expected_containers=False, inherited_descendants=None):
        events.append(("certify", str(evidence_dir)))
        return {
            "result": "certified",
            "reason": "all_units_certified_on_a_qualified_host",
            "evidence": {},
            "wall_seconds": 1.0,
            "evidence_dir": str(evidence_dir),
        }, None

    monkeypatch.setattr(check, "expensive_tool_lock", fake_expensive_tool_lock)
    monkeypatch.setattr(check, "active_yolomux_server_records", lambda: [{"port": 7772}, {"port": 7770}])
    monkeypatch.setattr(check, "lower_current_process_priority", lambda records: events.append(("nice", records)) or True)
    monkeypatch.setattr(check, "run_parallel", fake_run_parallel)
    monkeypatch.setattr(check, "run_serial", fake_run_serial)
    monkeypatch.setattr(check, "run_certification_phase", fake_certification_phase)

    assert check.main([]) == 0

    assert events[0] == ("lock", True, check.DEFAULT_TOOL_LOCK_PATH)
    assert events[1] == ("nice", [{"port": 7772}, {"port": 7770}])
    assert events[2][0] == "run"
    assert events[2][1] == ["pytest-browser", "pytest-e2e", "pytest", "node-layout", "static", "node-syntax", "py-compile", "whitespace"]
    assert events[3] == ("serial", ["pytest-gate-serial"])
    # The exclusive phase belongs to the canonical command, and it runs after the parallel lanes.
    assert events[4][0] == "certify", events
    output = capsys.readouterr().out
    assert "Acquiring YOLOmux expensive-tool lock" in output
    assert "lowered check priority by nice +5" in output
    assert "CERTIFIED" in output


def test_default_check_gate_exits_not_certifiable_when_the_host_is_unqualified(monkeypatch, capsys):
    """A green set of lanes must not carry an unqualified host to exit 0."""

    check = load_check_module()
    monkeypatch.setattr(check, "active_yolomux_server_records", lambda: [])
    monkeypatch.setattr(check, "run_parallel", lambda selected, **_kwargs: [check.LaneResult(lane.name, lane.label, True, 0.0, "") for lane in selected])
    monkeypatch.setattr(
        check,
        "run_certification_phase",
        lambda *, evidence_dir, expected_containers=False, inherited_descendants=None: (
            {
                "result": "not-certifiable",
                "reason": "host_unqualified_preflight",
                "evidence": [{"signal": "disk_busy_fraction_max", "measured": 0.71, "limit": 0.2}],
                "wall_seconds": 1.0,
                "evidence_dir": str(evidence_dir),
            },
            None,
        ),
    )

    assert check.main(["--no-tool-guard"]) == check.EXIT_NOT_CERTIFIABLE
    output = capsys.readouterr().out
    assert latency_calibration.NOT_CERTIFIABLE in output
    assert "host_unqualified_preflight" in output
    assert "CHECK PASSED" not in output


@pytest.mark.parametrize("reason", ["dirty_start_checkout", "start_state_unobservable"])
def test_default_check_gate_exits_nonzero_when_exact_sha_is_not_admitted(monkeypatch, capsys, reason):
    check = load_check_module()
    monkeypatch.setattr(check, "active_yolomux_server_records", lambda: [])
    monkeypatch.setattr(check, "run_parallel", lambda selected, **_kwargs: [check.LaneResult(lane.name, lane.label, True, 0.0, "") for lane in selected])
    monkeypatch.setattr(
        check,
        "run_certification_phase",
        lambda *, evidence_dir, expected_containers=False, inherited_descendants=None: (
            {
                "result": "certified",
                "reason": "all_units_certified_on_a_qualified_host",
                "evidence": {},
                "wall_seconds": 1.0,
                "evidence_dir": str(evidence_dir),
                "release": {"exact_sha_certification": {"admitted": False, "reason": reason}},
            },
            None,
        ),
    )

    assert check.main(["--no-tool-guard"]) == check.EXIT_NOT_CERTIFIABLE
    output = capsys.readouterr().out
    assert "exact_sha_certification_rejected" in output
    assert "CHECK PASSED" not in output


def test_focused_cheap_lane_skips_live_server_priority_work(monkeypatch):
    check = load_check_module()
    events = []
    report_path = Path("/tmp") / f"yolomux-check-runner-{os.getpid()}.json"

    @contextmanager
    def fake_expensive_tool_lock(enabled=True, lock_path=check.DEFAULT_TOOL_LOCK_PATH):
        events.append(("lock", enabled))
        yield enabled

    def fail_active_records():
        raise AssertionError("cheap focused lanes should not probe live YOLOmux server state")

    monkeypatch.setattr(check, "expensive_tool_lock", fake_expensive_tool_lock)
    monkeypatch.setattr(check, "active_yolomux_server_records", fail_active_records)
    monkeypatch.setattr(check, "run_parallel", lambda selected, **_kwargs: [check.LaneResult(selected[0].name, selected[0].label, True, 0.0, "")])
    monkeypatch.setattr(check, "performance_report_path", lambda _value: report_path)

    def fail_certification(*, evidence_dir):
        raise AssertionError("a focused lane selection is not a certification and must not run the phase")

    monkeypatch.setattr(check, "run_certification_phase", fail_certification)

    assert check.main(["--lane", "whitespace"]) == 0

    assert events == [("lock", False)]
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["schema"] == 5
    assert payload["certification"] is None
    assert payload["selected_lanes"] == ["whitespace"]


def test_performance_report_captures_steps_resources_and_worker_budget(tmp_path):
    check = load_check_module()
    lane = check.Lane("demo", "demo lane", ())
    result = check.LaneResult(
        "demo",
        "demo lane",
        True,
        1.25,
        "",
        (check.StepResult("demo step", "python3 -m demo", 0.75, 0),),
    )

    payload = check.performance_report_payload(
        selected=[lane],
        results=[result],
        serial=False,
        elapsed=1.5,
        child_usage={"user_seconds": 0.5, "system_seconds": 0.25, "max_rss": 1024, "max_rss_unit": "KiB"},
        certification={"result": "certified", "reason": "all_units_certified_on_a_qualified_host"},
    )
    path = tmp_path / "report.json"
    check.write_performance_report(path, payload)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "certification": {"reason": "all_units_certified_on_a_qualified_host", "result": "certified"},
        "child_usage": {"max_rss": 1024, "max_rss_unit": "KiB", "system_seconds": 0.25, "user_seconds": 0.5},
        "cpu_percent": check.check_cpu_percent(),
        "interrupted": False,
        "lanes": [{"label": "demo lane", "name": "demo", "ok": True, "steps": [{"command": "python3 -m demo", "label": "demo step", "returncode": 0, "test_durations": [], "wall_seconds": 0.75}], "wall_seconds": 1.25}],
        "mode": "parallel",
        "pytest_workers": {"browser": check.pytest_worker_counts()[1], "e2e": check.pytest_worker_counts()[2], "nonbrowser": check.pytest_worker_counts()[0]},
        "schema": 5,
        "selected_lanes": ["demo"],
        "wall_seconds": 1.5,
    }


def test_serial_lanes_report_preserves_normal_worker_policy():
    check = load_check_module()
    lane = check.Lane("demo", "demo lane", ())

    payload = check.performance_report_payload(
        selected=[lane],
        results=[check.LaneResult("demo", "demo lane", True, 0.0, "")],
        serial=False,
        serial_lanes=True,
        elapsed=0.0,
        child_usage={"user_seconds": 0.0, "system_seconds": 0.0, "max_rss": 0, "max_rss_unit": "KiB"},
    )

    assert payload["mode"] == "serial-lanes"
    assert payload["pytest_workers"] == dict(
        zip(("nonbrowser", "browser", "e2e"), check.pytest_worker_counts(), strict=True)
    )


def test_serial_lanes_separates_lane_scheduling_from_pytest_workers(monkeypatch):
    check = load_check_module()
    monkeypatch.setenv("YOLOMUX_PYTEST_WORKERS", "8,5,3")
    normal = {lane.name: lane for lane in check.lanes()}
    serial = {lane.name: lane for lane in check.lanes(serial=True)}

    assert normal["pytest"].steps[0].args[normal["pytest"].steps[0].args.index("-n") + 1] == "8"
    assert normal["pytest-browser"].steps[1].args[normal["pytest-browser"].steps[1].args.index("-n") + 1] == "5"
    assert normal["pytest-e2e"].steps[0].args[normal["pytest-e2e"].steps[0].args.index("-n") + 1] == "3"
    assert "-n" not in serial["pytest"].steps[0].args
    assert "-n" not in serial["pytest-browser"].steps[1].args
    assert "-n" not in serial["pytest-e2e"].steps[0].args


def test_serial_and_serial_lanes_are_mutually_exclusive(monkeypatch):
    check = load_check_module()
    monkeypatch.setattr(sys, "argv", ["check.py"])

    with pytest.raises(SystemExit) as error:
        check.main(["--serial", "--serial-lanes"])

    assert error.value.code == 2


def test_performance_report_path_is_tmp_only(monkeypatch):
    check = load_check_module()
    monkeypatch.setattr(check.os, "getpid", lambda: 4321)
    monkeypatch.setattr(check.time, "time_ns", lambda: 123456789)

    assert check.performance_report_path("") == Path("/tmp/yolomux-check-runs/check-123456789-4321.json")
    assert check.performance_report_path("/tmp/yolomux-report.json") == Path("/tmp/yolomux-report.json")
    with pytest.raises(ValueError, match="under /tmp"):
        check.performance_report_path("report.json")


def test_check_state_dir_uses_rooted_owner_and_refuses_outside_override(monkeypatch, tmp_path):
    check = load_check_module()
    root = tmp_path / "root"
    monkeypatch.setenv("YOLOMUX_ROOT", str(root))
    monkeypatch.delenv("YOLOMUX_STATE_DIR", raising=False)

    assert check.state_dir_from_env() == root / "state"

    monkeypatch.setenv("YOLOMUX_STATE_DIR", str(tmp_path / "outside"))
    with pytest.raises(ValueError, match="YOLOMUX_STATE_DIR resolves outside YOLOMUX_ROOT"):
        check.state_dir_from_env()


def test_expensive_tool_lock_refuses_direct_worktree_path(monkeypatch, tmp_path):
    check = load_check_module()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    lock_path = worktree / "tool.lock"
    monkeypatch.setattr(instance_isolation, "REPO_ROOT", worktree)

    with pytest.raises(ValueError, match="shared worktree"):
        with check.expensive_tool_lock(lock_path=lock_path):
            pass

    assert not lock_path.exists()


def test_performance_instrumentation_adds_bounded_pytest_durations_and_parses_them():
    check = load_check_module()
    lane = check.Lane("demo", "demo", (check.Step("pytest", ["python3", "-m", "pytest", "tests", "-q"]), check.Step("node", ["node", "--check", "static/yolomux.js"])))

    instrumented = check.instrument_lane_for_performance(lane)

    assert instrumented.steps[0].args[-2:] == ["--durations=0", "--durations-min=0"]
    assert instrumented.steps[1] == lane.steps[1]
    assert check.pytest_duration_phases("0.52s call tests/test_demo.py::test_fast\n0.00s setup tests/test_demo.py::test_fast\n") == (
        {"seconds": 0.52, "phase": "call", "nodeid": "tests/test_demo.py::test_fast"},
        {"seconds": 0.0, "phase": "setup", "nodeid": "tests/test_demo.py::test_fast"},
    )


def test_instrumented_pytest_step_records_every_phase_beyond_ten_rows(tmp_path):
    check = load_check_module()
    probe = tmp_path / "test_duration_probe.py"
    probe.write_text(
        "\n".join(f"def test_probe_{index}():\n    assert True" for index in range(12)) + "\n",
        encoding="utf-8",
    )
    lane = check.instrument_lane_for_performance(
        check.Lane(
            "duration-probe",
            "duration probe",
            (check.Step("pytest duration probe", ["python3", "-m", "pytest", str(probe), "-n", "2", "-q"]),),
        )
    )

    result = check.run_lane(lane)

    assert result.ok, result.output
    rows = result.steps[0].test_durations
    assert len(rows) == 36
    assert {row["phase"] for row in rows} == {"setup", "call", "teardown"}
    assert len({row["nodeid"] for row in rows}) == 12


def test_run_lane_exports_its_identity_to_every_step(monkeypatch):
    check = load_check_module()
    environments = []
    monkeypatch.setattr(check.subprocess, "run", lambda *_args, **kwargs: environments.append(kwargs["env"]) or subprocess.CompletedProcess([], 0, "", ""))

    result = check.run_lane(check.Lane("pytest-e2e", "e2e", (check.Step("probe", ["probe"]),)))

    assert result.ok
    assert environments[0][check.CHECK_LANE_ENV] == "pytest-e2e"


@pytest.mark.parametrize(("returncode", "expected_ok"), ((0, True), (7, False)))
def test_run_lane_retains_complete_private_output_with_hash_and_true_exit(
    tmp_path, returncode, expected_ok
):
    check = load_check_module()
    probe = tmp_path / "lane_probe.py"
    stdout = "stdout-begin\n" + ("o" * 8192) + "\nstdout-end\n"
    stderr = "stderr-begin\n" + ("e" * 8192) + "\nstderr-end\n"
    probe.write_text(
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"raise SystemExit({returncode})\n",
        encoding="utf-8",
    )
    output_root = check.lane_output_root(Path("/tmp") / f"check-retention-{tmp_path.name}.json")
    lane = check.Lane("pytest-browser", "browser", (check.Step("probe", [sys.executable, str(probe)]),))

    result = check.run_lane(lane, output_root=output_root)

    assert result.ok is expected_ok
    assert result.steps[-1].returncode == returncode
    assert result.output_artifact is not None
    artifact_path = Path(result.output_artifact.path)
    retained = artifact_path.read_text(encoding="utf-8")
    assert stdout in retained and stderr in retained
    assert retained == result.output
    assert result.output_artifact.bytes == len(retained.encode("utf-8"))
    assert result.output_artifact.sha256 == hashlib.sha256(retained.encode("utf-8")).hexdigest()
    assert artifact_path.parent == output_root
    assert artifact_path.stat().st_mode & 0o777 == 0o600
    assert output_root.stat().st_mode & 0o777 == 0o700
    if returncode:
        assert retained.endswith(f"exit {returncode}: probe\n")

    payload = check.performance_report_payload(
        selected=[lane],
        results=[result],
        serial=True,
        elapsed=1.0,
        child_usage={"user_seconds": 0.0, "system_seconds": 0.0, "max_rss": 1, "max_rss_unit": "KiB"},
    )
    lane_payload = payload["lanes"][0]
    assert lane_payload["output_artifact"] == {
        "path": str(artifact_path),
        "sha256": result.output_artifact.sha256,
        "bytes": result.output_artifact.bytes,
    }
    assert "output" not in lane_payload
    assert len(json.dumps(payload)) < len(retained)


def test_run_lane_retains_primary_pytest_failure_and_teardown_gate_error(tmp_path):
    check = load_check_module()
    probe = tmp_path / "test_teardown_probe.py"
    primary_sentinel = "primary-failure-sentinel"
    teardown_sentinel = "teardown-gate-sentinel"
    probe.write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def gate():\n"
        "    yield\n"
        f"    raise RuntimeError({teardown_sentinel!r})\n"
        "def test_primary(gate):\n"
        f"    raise AssertionError({primary_sentinel!r})\n",
        encoding="utf-8",
    )
    output_root = check.lane_output_root(Path("/tmp") / f"check-teardown-{tmp_path.name}.json")

    result = check.run_lane(
        check.Lane(
            "pytest-browser",
            "browser",
            (check.Step("pytest browser", [sys.executable, "-m", "pytest", "-q", str(probe)]),),
        ),
        output_root=output_root,
    )

    assert result.ok is False and result.steps[-1].returncode == 1
    retained = Path(result.output_artifact.path).read_text(encoding="utf-8")
    assert primary_sentinel in retained
    assert teardown_sentinel in retained
    assert result.output_artifact.sha256 == hashlib.sha256(retained.encode("utf-8")).hexdigest()


def test_run_lane_reports_insufficient_space_without_changing_a_passing_verdict(monkeypatch, tmp_path):
    check = load_check_module()
    output_root = check.lane_output_root(tmp_path / "runtime.json")
    monkeypatch.setattr(
        check.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=check.LANE_OUTPUT_FREE_SPACE_RESERVE_BYTES),
    )

    result = check.run_lane(
        check.Lane("probe", "probe", (check.Step("probe", [sys.executable, "-c", "print('ok')"]),)),
        output_root=output_root,
    )

    assert result.ok is True and result.steps[-1].returncode == 0
    assert result.output_artifact is None
    failure = result.output_retention_failure
    assert failure is not None
    assert failure.reason == "insufficient_space"
    assert failure.attempted_bytes == len(result.output.encode("utf-8"))
    assert failure.free_bytes == failure.reserve_bytes == check.LANE_OUTPUT_FREE_SPACE_RESERVE_BYTES
    assert not (output_root / "probe.txt").exists()
    assert check.lane_output_report_fields(result) == {
        "output_retention_failure": {
            "reason": "insufficient_space",
            "error_type": "OSError",
            "detail": "retention would cross the reserved free-space floor",
            "attempted_bytes": failure.attempted_bytes,
            "free_bytes": check.LANE_OUTPUT_FREE_SPACE_RESERVE_BYTES,
            "reserve_bytes": check.LANE_OUTPUT_FREE_SPACE_RESERVE_BYTES,
        }
    }


def test_lane_output_root_is_isolated_under_tmp():
    check = load_check_module()

    assert check.lane_output_root(Path("/tmp/yolomux-check-runs/check-1.json")) == Path(
        "/tmp/yolomux-check-runs/check-1.json.artifacts"
    )
    assert check.lane_output_root(Path("/tmp/report.artifacts")) == Path(
        "/tmp/report.artifacts.artifacts"
    )
    with pytest.raises(ValueError, match="under /tmp"):
        check.lane_output_root(REPO_ROOT / "check.json")


@pytest.mark.parametrize(("child_returncode", "expected_main"), ((0, 0), (9, 1)))
def test_main_wires_lane_artifact_into_report_without_false_green(
    monkeypatch, tmp_path, child_returncode, expected_main
):
    check = load_check_module()
    report = tmp_path / "runtime.json"
    probe = tmp_path / "runner_probe.py"
    sentinel = "full-output-sentinel"
    probe.write_text(
        f"print({sentinel!r})\n" f"raise SystemExit({child_returncode})\n",
        encoding="utf-8",
    )
    lane = check.Lane(
        "retention-probe",
        "retention probe",
        (
            check.Step(
                "probe",
                [
                    sys.executable,
                    str(probe),
                ],
            ),
        ),
    )

    monkeypatch.setattr(check, "lanes", lambda **_kwargs: [lane])
    monkeypatch.setattr(check, "performance_report_path", lambda _value: report)
    monkeypatch.setattr(check.docker_image, "container_available", lambda _root: (False, ""))

    @contextmanager
    def writer(*_args, **_kwargs):
        yield None

    monkeypatch.setattr(check.worktree_writer, "acquire_worktree_writer", writer)

    assert check.main(["--lane", "retention-probe", "--serial", "--no-tool-guard"]) == expected_main

    payload = json.loads(report.read_text(encoding="utf-8"))
    lane_payload = payload["lanes"][0]
    artifact = lane_payload["output_artifact"]
    artifact_path = Path(artifact["path"])
    retained = artifact_path.read_text(encoding="utf-8")
    assert artifact_path.parent == check.lane_output_root(report)
    assert sentinel in retained
    assert artifact["sha256"] == hashlib.sha256(retained.encode("utf-8")).hexdigest()
    assert lane_payload["steps"][0]["returncode"] == child_returncode
    assert lane_payload["ok"] is (child_returncode == 0)
    assert sentinel not in report.read_text(encoding="utf-8")


def test_main_report_named_artifacts_stays_json_beside_a_separate_artifact_directory(
    monkeypatch, tmp_path
):
    check = load_check_module()
    report = tmp_path / "report.artifacts"
    probe = tmp_path / "runner_probe.py"
    probe.write_text("raise SystemExit(9)\n", encoding="utf-8")
    lane = check.Lane(
        "retention-probe",
        "retention probe",
        (check.Step("probe", [sys.executable, str(probe)]),),
    )
    monkeypatch.setattr(check, "lanes", lambda **_kwargs: [lane])
    monkeypatch.setattr(check, "performance_report_path", lambda _value: report)
    monkeypatch.setattr(check.docker_image, "container_available", lambda _root: (False, ""))

    @contextmanager
    def writer(*_args, **_kwargs):
        yield None

    monkeypatch.setattr(check.worktree_writer, "acquire_worktree_writer", writer)

    assert check.main(["--lane", "retention-probe", "--serial", "--no-tool-guard"]) == 1

    artifact_root = tmp_path / "report.artifacts.artifacts"
    assert report.is_file() and artifact_root.is_dir()
    payload = json.loads(report.read_text(encoding="utf-8"))
    lane_payload = payload["lanes"][0]
    assert lane_payload["ok"] is False
    assert lane_payload["steps"][0]["returncode"] == 9
    assert Path(lane_payload["output_artifact"]["path"]).parent == artifact_root


@pytest.mark.parametrize(
    ("write_error", "expected_error_type"),
    (
        (PermissionError("permission-denied-" + ("x" * 1000)), "PermissionError"),
        (OSError(errno.ENOSPC, "disk-full-" + ("x" * 1000)), "OSError"),
    ),
)
def test_main_reports_bounded_retention_write_failure_without_masking_child_failure(
    monkeypatch, tmp_path, write_error, expected_error_type
):
    check = load_check_module()
    report = tmp_path / "runtime.json"
    child_sentinel = "completed-child-failure"
    probe = tmp_path / "runner_probe.py"
    probe.write_text(
        f"print({child_sentinel!r})\nraise SystemExit(9)\n",
        encoding="utf-8",
    )
    lane = check.Lane(
        "retention-probe",
        "retention probe",
        (check.Step("probe", [sys.executable, str(probe)]),),
    )
    monkeypatch.setattr(check, "lanes", lambda **_kwargs: [lane])
    monkeypatch.setattr(check, "performance_report_path", lambda _value: report)
    monkeypatch.setattr(check.docker_image, "container_available", lambda _root: (False, ""))
    monkeypatch.setattr(
        check,
        "atomic_write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(write_error),
    )

    @contextmanager
    def writer(*_args, **_kwargs):
        yield None

    monkeypatch.setattr(check.worktree_writer, "acquire_worktree_writer", writer)

    assert check.main(["--lane", "retention-probe", "--serial", "--no-tool-guard"]) == 1

    payload = json.loads(report.read_text(encoding="utf-8"))
    lane_payload = payload["lanes"][0]
    assert lane_payload["ok"] is False
    assert lane_payload["steps"][0]["returncode"] == 9
    failure = lane_payload["output_retention_failure"]
    assert failure["reason"] == "write_failed"
    assert failure["error_type"] == expected_error_type
    assert len(failure["detail"]) == check.LANE_OUTPUT_FAILURE_DETAIL_CHARS
    assert failure["attempted_bytes"] > len(child_sentinel)
    assert failure["free_bytes"] is None
    assert failure["reserve_bytes"] == check.LANE_OUTPUT_FREE_SPACE_RESERVE_BYTES
    assert "output_artifact" not in lane_payload and "output" not in lane_payload


def test_collection_assigns_class_specific_timeout_ceilings(tmp_path):
    test_config = sys.modules["conftest"]

    class Item:
        def __init__(self, name, markers=()):
            self.path = tmp_path / name
            self.path.write_text("", encoding="utf-8")
            self.nodeid = f"{name}::test_probe"
            self.markers = list(markers)

        def add_marker(self, marker, append=True):
            if append:
                self.markers.append(marker.mark)
            else:
                self.markers.insert(0, marker.mark)

        def get_closest_marker(self, name):
            return next((marker for marker in self.markers if marker.name == name), None)

    items = [
        Item("test_unit_probe.py"),
        Item("test_browser_probe.py"),
        Item("test_e2e_probe.py", (pytest.mark.e2e.mark,)),
    ]

    test_config.pytest_collection_modifyitems(None, items)

    timeout_by_nodeid = {
        item.nodeid: item.get_closest_marker("timeout").args[0]
        for item in items
    }
    assert timeout_by_nodeid == {
        "test_unit_probe.py::test_probe": 180,
        "test_browser_probe.py::test_probe": 300,
        "test_e2e_probe.py::test_probe": 600,
    }


def test_pytest_timeout_fails_loudly_with_faulthandler_stack(tmp_path):
    probe = tmp_path / "test_timeout_probe.py"
    probe.write_text(
        "import time\n\ndef test_timeout_probe():\n    time.sleep(1)\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["YOLOMUX_CHECK_CONTAINER"] = "0"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(probe),
            "-n",
            "2",
            "--timeout=0.3",
            "-o",
            "faulthandler_timeout=0.1",
            "-q",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 1, output
    assert "Timeout (0:00:00.100000)!" in output, output
    assert "test_timeout_probe" in output, output
    assert "Failed: Timeout" in output, output


# ---------------------------------------------------------------------------
# The exclusive latency-certification phase
# ---------------------------------------------------------------------------


def _certification_env_allowlist() -> list[str]:
    """Read the forwarded-environment allowlist docker/run-tests.sh actually applies."""

    text = (REPO_ROOT / "docker" / "run-tests.sh").read_text(encoding="utf-8")
    block = re.search(r"FORWARDED_TEST_ENV=\(\n(.*?)\n\)", text, re.DOTALL)
    assert block, text
    return [line.strip() for line in block.group(1).splitlines() if line.strip()]


def test_docker_run_tests_forwards_every_certification_admission_variable():
    """pytest re-executes inside the container, which passes only this allowlist.

    A certification variable missing here does not fail loudly: the node is silently skipped and the
    run still reports green. That is the exact quiet failure the phase exists to remove, so the
    allowlist is asserted against the one owner of those names.
    """

    allowlist = _certification_env_allowlist()
    for name in latency_calibration.CERTIFICATION_ENV_NAMES:
        assert name in allowlist, (name, allowlist)
    # The pre-existing forwards must survive; the loop replaced two hand-written ifs.
    assert {"YOLOMUX_TEST_MOCK_TRANSCRIPTS", "YOLOMUX_WORKTREE_WRITER_TOKEN"} <= set(allowlist), allowlist
    assert len(allowlist) == len(set(allowlist)), allowlist


def _forwarding_loop(allowlist: list[str], environ: dict[str, str]) -> list[str]:
    """Run docker/run-tests.sh's OWN forwarding loop against a supplied allowlist.

    The loop is extracted from the script rather than restated, so this exercises the real
    admission logic. Only the allowlist differs between the two calls below.
    """

    text = (REPO_ROOT / "docker" / "run-tests.sh").read_text(encoding="utf-8")
    loop = re.search(r"^(test_env=\(\)$(?:.|\n)*?^done)$", text, re.MULTILINE)
    assert loop, text
    script = (
        "set -u\n"
        + "FORWARDED_TEST_ENV=(" + " ".join(shlex.quote(name) for name in allowlist) + ")\n"
        + loop.group(1) + "\n"
        + 'printf "%s\\n" "${test_env[@]:-}"\n'
    )
    completed = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **environ},
    )
    return [line for line in completed.stdout.split("\n") if line]


def test_docker_run_tests_forwards_the_stats_persistence_arm_variable():
    """The persistence A/B selects its arm at statsd start, inside the test container.

    An arm variable missing from the allowlist does not fail loudly: both arms run identical
    code and the experiment reports a clean null, which at a low base rate is indistinguishable
    from a real null. The name is read from its sole owner rather than restated here.
    """

    assert service_module.APPEND_FLUSH_ENV_NAME in _certification_env_allowlist()


def test_the_forwarding_loop_only_passes_names_the_allowlist_contains():
    """The control the allowlist doc names: one input, two allowlists, only that differing.

    This proves the forwarding MECHANISM rather than asserting it. It does not launch a
    container, so it does not prove the container then honours `-e`; that half is docker's
    contract and is exercised by every containerised run.
    """

    name = service_module.APPEND_FLUSH_ENV_NAME
    environ = {name: "0"}
    with_name = _forwarding_loop(["YOLOMUX_TEST_MOCK_TRANSCRIPTS", name], environ)
    without_name = _forwarding_loop(["YOLOMUX_TEST_MOCK_TRANSCRIPTS"], environ)
    assert with_name == ["-e", name], with_name
    assert without_name == [], without_name


def test_certification_step_runs_every_named_unit_serially_with_its_admission_env():
    check = load_check_module()
    step = check.certification_step(Path("/tmp/yolomux-certification-probe"))

    for nodeid in check.CERTIFICATION_NODE_IDS:
        assert nodeid in step.args, (nodeid, step.args)
    # Serial by construction: the phase must never hand its units to an xdist pool.
    assert "-n" not in step.args, step.args
    assert ["-p", "no:xdist"] == step.args[step.args.index("-p"):step.args.index("-p") + 2], step.args
    assert f"--junit-xml=/tmp/yolomux-certification-probe/{check.CERTIFICATION_JUNIT_NAME}" in step.args, step.args
    # xunit2, the pytest default, omits the `file` attribute the outcome reader matches on.
    assert ["-o", "junit_family=xunit1"] == step.args[step.args.index("-o"):step.args.index("-o") + 2], step.args
    environment = dict(step.env)
    assert environment["YOLOMUX_E2E_EVIDENCE_DIR"] == "/tmp/yolomux-certification-probe"
    assert environment[check.CHECK_LANE_ENV] == ""
    for name in latency_calibration.CERTIFICATION_ENV_NAMES:
        assert environment[name] == "1", environment


def test_certification_node_ids_all_resolve_to_a_defined_test():
    """A renamed or deleted unit must break the phase loudly instead of certifying nothing."""

    check = load_check_module()
    missing = []
    for nodeid in check.CERTIFICATION_NODE_IDS:
        relative, _, name = nodeid.partition("::")
        _source, tree = parsed_python_source(REPO_ROOT / relative)
        defined = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if name not in defined:
            missing.append(nodeid)
    assert missing == [], missing


def _junit_document(cases: list[tuple[str, str, str]]) -> str:
    body = "".join(
        f'<testcase classname="c" file="{file}" name="{name}" time="1.0">'
        + ("" if outcome == "passed" else f'<{outcome} message="synthetic {outcome}">detail</{outcome}>')
        + "</testcase>"
        for file, name, outcome in cases
    )
    return f'<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" tests="{len(cases)}">{body}</testsuite></testsuites>'


_ADMISSION_EXPECTED = ("tests/test_a.py::test_one", "tests/test_b.py::test_two")


def _write_junit(tmp_path, xml: str) -> Path:
    junit = tmp_path / "certification-junit.xml"
    junit.write_text(xml, encoding="utf-8")
    return junit


def test_certification_junit_admission_accepts_a_well_formed_document(tmp_path):
    check = load_check_module()
    junit = _write_junit(tmp_path, _junit_document([("tests/test_a.py", "test_one", "passed"), ("tests/test_b.py", "test_two", "skipped")]))
    admission = check.certification_junit_admission(junit, _ADMISSION_EXPECTED)
    assert admission["admitted"] is True, admission
    assert admission["outcomes"]["tests/test_a.py::test_one"]["outcome"] == "passed"
    assert admission["outcomes"]["tests/test_b.py::test_two"]["outcome"] == "skipped"


def test_certification_junit_admission_defers_an_absent_document_to_did_not_run(tmp_path):
    """Absent is the ordinary did-not-run the not-collected path owns, not a structural rejection."""

    check = load_check_module()
    admission = check.certification_junit_admission(tmp_path / "absent.xml", _ADMISSION_EXPECTED)
    assert admission["admitted"] is True, admission
    assert all(row["outcome"] == "not-collected" for row in admission["outcomes"].values()), admission


def test_certification_junit_admission_rejects_a_malformed_document(tmp_path):
    check = load_check_module()
    junit = _write_junit(tmp_path, "<testsuite><testcase file='tests/test_a.py' name='test_one' time='1.0'>")
    admission = check.certification_junit_admission(junit, _ADMISSION_EXPECTED)
    assert admission["admitted"] is False and admission["reason"] == "junit_malformed", admission


def test_certification_junit_admission_rejects_a_row_without_identity(tmp_path):
    check = load_check_module()
    junit = _write_junit(tmp_path, '<?xml version="1.0"?><testsuite><testcase classname="c" name="test_one" time="1.0"/></testsuite>')
    admission = check.certification_junit_admission(junit, _ADMISSION_EXPECTED)
    assert admission["admitted"] is False and admission["reason"] == "junit_missing_identity", admission


def test_certification_junit_admission_rejects_an_unexpected_row(tmp_path):
    check = load_check_module()
    junit = _write_junit(tmp_path, _junit_document([("tests/test_a.py", "test_one", "passed"), ("tests/other.py", "test_strange", "passed")]))
    admission = check.certification_junit_admission(junit, _ADMISSION_EXPECTED)
    assert admission["admitted"] is False and admission["reason"] == "junit_unexpected_row", admission
    assert admission["detail"] == "tests/other.py::test_strange", admission


def test_certification_junit_admission_rejects_duplicate_rows(tmp_path):
    check = load_check_module()
    junit = _write_junit(tmp_path, _junit_document([("tests/test_a.py", "test_one", "passed"), ("tests/test_a.py", "test_one", "failure")]))
    admission = check.certification_junit_admission(junit, _ADMISSION_EXPECTED)
    assert admission["admitted"] is False and admission["reason"] == "junit_duplicate_row", admission


def test_certification_junit_admission_rejects_missing_or_nonfinite_timing(tmp_path):
    check = load_check_module()
    missing = _write_junit(tmp_path, '<?xml version="1.0"?><testsuite><testcase classname="c" file="tests/test_a.py" name="test_one"/></testsuite>')
    assert check.certification_junit_admission(missing, _ADMISSION_EXPECTED)["reason"] == "junit_timing_missing"

    invalid = _write_junit(tmp_path, '<?xml version="1.0"?><testsuite><testcase classname="c" file="tests/test_a.py" name="test_one" time="fast"/></testsuite>')
    assert check.certification_junit_admission(invalid, _ADMISSION_EXPECTED)["reason"] == "junit_timing_invalid"

    for spelling in ("inf", "nan", "-1.0"):
        nonfinite = _write_junit(tmp_path, f'<?xml version="1.0"?><testsuite><testcase classname="c" file="tests/test_a.py" name="test_one" time="{spelling}"/></testsuite>')
        assert check.certification_junit_admission(nonfinite, _ADMISSION_EXPECTED)["reason"] == "junit_timing_non_finite", spelling


def test_certification_junit_admission_rejects_contradictory_or_unknown_outcome_children(tmp_path):
    check = load_check_module()
    contradictory = _write_junit(
        tmp_path,
        '<?xml version="1.0"?><testsuite><testcase classname="c" file="tests/test_a.py" name="test_one" time="1.0">'
        '<failure message="breach"/><skipped message="also"/></testcase></testsuite>',
    )
    assert check.certification_junit_admission(contradictory, _ADMISSION_EXPECTED)["reason"] == "junit_contradictory_outcome"

    duplicate_failure = _write_junit(
        tmp_path,
        '<?xml version="1.0"?><testsuite><testcase classname="c" file="tests/test_a.py" name="test_one" time="1.0">'
        '<failure message="first"/><failure message="second"/></testcase></testsuite>',
    )
    assert check.certification_junit_admission(duplicate_failure, _ADMISSION_EXPECTED)["reason"] == "junit_contradictory_outcome"

    unknown = _write_junit(
        tmp_path,
        '<?xml version="1.0"?><testsuite><testcase classname="c" file="tests/test_a.py" name="test_one" time="1.0">'
        '<rerun message="what"/></testcase></testsuite>',
    )
    assert check.certification_junit_admission(unknown, _ADMISSION_EXPECTED)["reason"] == "junit_unknown_outcome_child"

    # system-out/system-err are captured stdio, never an outcome, and must not trip the guard.
    with_stdio = _write_junit(
        tmp_path,
        '<?xml version="1.0"?><testsuite><testcase classname="c" file="tests/test_a.py" name="test_one" time="1.0">'
        '<system-out>logs</system-out></testcase></testsuite>',
    )
    admitted = check.certification_junit_admission(with_stdio, _ADMISSION_EXPECTED)
    assert admitted["admitted"] is True and admitted["outcomes"]["tests/test_a.py::test_one"]["outcome"] == "passed", admitted


def test_certification_verdict_refuses_an_inadmissible_junit_before_reading_outcomes(tmp_path):
    """A rejected document outranks the outcomes; the phase never certifies from a file it distrusts."""

    check = load_check_module()
    qualified = {"qualified": True, "reasons": []}
    retired = {"retired": True, "survivors": []}
    passed = {"tests/test_a.py::test_one": {"outcome": "passed", "detail": "", "seconds": 1.0}}
    rejected = {"admitted": False, "reason": "junit_duplicate_row", "detail": "tests/test_a.py::test_one", "outcomes": {}}
    verdict = check.certification_verdict(retirement=retired, preflight=qualified, postflight=qualified, outcomes=passed, returncode=0, junit_admission=rejected)
    assert (verdict["result"], verdict["reason"]) == ("not-certifiable", "certification_junit_rejected"), verdict
    assert verdict["evidence"]["junit_reason"] == "junit_duplicate_row", verdict

    # An admitted document leaves every existing outcome precedence exactly as it was.
    admitted = {"admitted": True, "reason": "", "detail": "", "outcomes": passed}
    green = check.certification_verdict(retirement=retired, preflight=qualified, postflight=qualified, outcomes=passed, returncode=0, junit_admission=admitted)
    assert green["result"] == "certified", green


def test_certification_outcomes_report_a_skipped_node_as_skipped_not_as_green(tmp_path):
    """The vacuous green this phase exists to prevent: pytest exits 0 while every unit skipped."""

    check = load_check_module()
    expected = ("tests/test_a.py::test_one", "tests/test_b.py::test_two", "tests/test_c.py::test_three")
    junit = tmp_path / "junit.xml"
    junit.write_text(
        _junit_document(
            [
                ("tests/test_a.py", "test_one", "skipped"),
                ("tests/test_b.py", "test_two", "passed"),
            ]
        ),
        encoding="utf-8",
    )
    outcomes = check.certification_outcomes(junit, expected)
    assert [outcomes[nodeid]["outcome"] for nodeid in expected] == ["skipped", "passed", "not-collected"]

    verdict = check.certification_verdict(
        retirement={"retired": True, "survivors": []},
        preflight={"qualified": True, "reasons": []},
        postflight={"qualified": True, "reasons": []},
        outcomes=outcomes,
        returncode=0,
    )
    assert verdict["result"] == "not-certifiable", verdict
    assert verdict["reason"] == "certification_unit_did_not_run", verdict
    assert set(verdict["evidence"]) == {"tests/test_a.py::test_one", "tests/test_c.py::test_three"}, verdict


def test_certification_outcomes_of_a_missing_junit_are_not_certifiable(tmp_path):
    check = load_check_module()
    expected = ("tests/test_a.py::test_one",)
    outcomes = check.certification_outcomes(tmp_path / "absent.xml", expected)
    assert outcomes["tests/test_a.py::test_one"]["outcome"] == "not-collected"


def test_certification_verdict_precedence_never_reaches_green_through_a_refusal():
    check = load_check_module()
    qualified = {"qualified": True, "reasons": []}
    unqualified = {"qualified": False, "reasons": [{"signal": "disk_busy_fraction_max", "measured": 0.7, "limit": 0.2}]}
    passed = {"tests/test_a.py::test_one": {"outcome": "passed", "detail": "", "seconds": 1.0}}
    breached = {"tests/test_a.py::test_one": {"outcome": "failure", "detail": "ceiling", "seconds": 1.0}}
    not_retired = {"retired": False, "survivors": [{"pid": 1, "ppid": 0, "command": "chrome"}]}
    retired = {"retired": True, "survivors": []}

    cases = [
        ((not_retired, qualified, qualified, passed, 0), "not-certifiable", "owned_processes_not_retired"),
        ((retired, unqualified, None, None, None), "not-certifiable", "host_unqualified_preflight"),
        ((retired, qualified, unqualified, passed, 0), "not-certifiable", "host_unqualified_postflight"),
        # A breach measured on a host that went unqualified is a void measurement, not evidence
        # about the product; it must not be reported as a product failure.
        ((retired, qualified, unqualified, breached, 1), "not-certifiable", "host_unqualified_postflight"),
        ((retired, qualified, qualified, breached, 1), "failed", "certification_unit_failed"),
        ((retired, qualified, qualified, passed, 3), "failed", "certification_command_failed"),
        ((retired, qualified, qualified, passed, 0), "certified", "all_units_certified_on_a_qualified_host"),
    ]
    for (retirement, preflight, postflight, outcomes, returncode), result, reason in cases:
        verdict = check.certification_verdict(
            retirement=retirement,
            preflight=preflight,
            postflight=postflight,
            outcomes=outcomes,
            returncode=returncode,
        )
        assert (verdict["result"], verdict["reason"]) == (result, reason), (verdict, result, reason)


def test_certification_verdict_reports_a_units_own_refusal_as_not_certifiable():
    """A unit that refused its own host is a void measurement, not a product failure.

    The phase preflight is taken before the whole serial run; each unit re-qualifies at the moment
    it runs, which is the only measurement that describes the host that unit competed with. Without
    this branch that refusal was reported as `certification_unit_failed` and blamed on the product.
    """

    check = load_check_module()
    qualified = {"qualified": True, "reasons": []}
    retired = {"retired": True, "survivors": []}
    refused = {
        "tests/test_a.py::test_one": {
            "outcome": "error",
            "detail": f'failed on setup with "NotCertifiableError: {latency_calibration.NOT_CERTIFIABLE}: '
            '{\\"reasons\\": [{\\"signal\\": \\"procs_running_p75\\", \\"measured\\": 40.0, \\"limit\\": 12.0}]}"',
            "seconds": 2.0,
        },
        "tests/test_b.py::test_two": {"outcome": "passed", "detail": "", "seconds": 1.0},
    }
    verdict = check.certification_verdict(retirement=retired, preflight=qualified, postflight=qualified, outcomes=refused, returncode=1)
    assert (verdict["result"], verdict["reason"]) == ("not-certifiable", "certification_unit_not_certifiable"), verdict
    assert set(verdict["evidence"]["refused"]) == {"tests/test_a.py::test_one"}, verdict
    assert verdict["evidence"]["also_failed"] == {}, verdict

    # A breach that is NOT a refusal still reads as a product failure; the new branch must not
    # launder every red into "not certifiable".
    breached = {"tests/test_a.py::test_one": {"outcome": "failure", "detail": "I3b dockview_load_layout breached its fixed ceiling", "seconds": 1.0}}
    breach_verdict = check.certification_verdict(retirement=retired, preflight=qualified, postflight=qualified, outcomes=breached, returncode=1)
    assert (breach_verdict["result"], breach_verdict["reason"]) == ("failed", "certification_unit_failed"), breach_verdict

    # One unit refusing must never hide a DIFFERENT unit's genuine red. Observed exactly once in a
    # real --certification-only run: the phase's own control refused on the retired
    # cpu_work_p75_ms while S1 failed on its terminal-pressure warm-up, and only the refusal
    # reached the printed evidence.
    mixed = {
        "tests/test_a.py::test_one": refused["tests/test_a.py::test_one"],
        "tests/test_d.py::test_four": {"outcome": "failure", "detail": "breached its fixed ceiling", "seconds": 1.0},
        "tests/test_c.py::test_three": {"outcome": "passed", "detail": "", "seconds": 1.0},
    }
    mixed_verdict = check.certification_verdict(retirement=retired, preflight=qualified, postflight=qualified, outcomes=mixed, returncode=1)
    assert (mixed_verdict["result"], mixed_verdict["reason"]) == ("not-certifiable", "certification_unit_not_certifiable"), mixed_verdict
    assert set(mixed_verdict["evidence"]["refused"]) == {"tests/test_a.py::test_one"}, mixed_verdict
    assert set(mixed_verdict["evidence"]["also_failed"]) == {"tests/test_d.py::test_four"}, mixed_verdict


def _certification_unit_modules():
    check = load_check_module()
    return sorted({nodeid.partition("::")[0] for nodeid in check.CERTIFICATION_NODE_IDS})


def test_no_certification_unit_keeps_a_private_host_qualifier_or_admission():
    """One owner for "may this host certify". A second owner can veto the better one.

    Measured on keivenc-linux1: 20 s after 40 spinners exited, `os.getloadavg()[0]` still read
    35.34 against the private 16.0 limit that used to live in test_chat_store.py, while the
    windowed owner measured procs_running p75 7 and cpu some-stall 0.0074 and correctly qualified
    the host. The same estimator failed 2 of 6 full gate runs at 17.31 and 17.52 after the parallel
    lanes it was measuring had already retired.
    """

    banned_calls = {"getloadavg"}
    offenders = {}
    for relative in [*_certification_unit_modules(), "tools/check.py", "tests/latency_calibration.py"]:
        _source, tree = parsed_python_source(REPO_ROOT / relative)
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in banned_calls
        }
        # latency_calibration records the lagging averages as evidence and asserts nothing on them.
        if relative == "tests/latency_calibration.py":
            assert called == {"getloadavg"}, called
            assert "lagging_load_average" not in latency_calibration.HOST_QUALIFICATION_LIMITS
            continue
        if called:
            offenders[relative] = sorted(called)
        # The private admission helper each unit used to carry, sharing only the env-var name.
        private = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"_certification_phase_requested", "certification_phase_only"}
        }
        if private:
            offenders.setdefault(relative, []).extend(sorted(private))
        # And no private threshold on a host signal, under any name.
        thresholds = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and ("LOAD_PER_CORE" in target.id or target.id.endswith("_MAX_LOAD"))
        }
        if thresholds:
            offenders.setdefault(relative, []).extend(sorted(thresholds))
    assert offenders == {}, offenders


def test_every_certification_unit_asks_the_shared_owner_before_it_measures():
    """Each unit requests `certification_phase_only`, and gets it from the one factory.

    It must also be the FIRST fixture in the signature: it decides admission and host fitness, and
    both decisions have to be made before the unit's own browser/server fixtures load the machine
    they are about to describe.
    """

    check = load_check_module()
    factory_modules = set()
    for nodeid in check.CERTIFICATION_NODE_IDS:
        relative, _, name = nodeid.partition("::")
        _source, tree = parsed_python_source(REPO_ROOT / relative)
        definitions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
        assert len(definitions) == 1, (nodeid, len(definitions))
        parameters = [argument.arg for argument in definitions[0].args.args]
        assert parameters and parameters[0] == "certification_phase_only", (nodeid, parameters)
        assigned = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "certification_phase_only" for target in node.targets)
            and isinstance(node.value, ast.Call)
            and (getattr(node.value.func, "attr", None) or getattr(node.value.func, "id", None)) == "certification_phase_fixture"
        ]
        assert len(assigned) == 1, (relative, "certification_phase_only must come from certification_phase_fixture()")
        factory_modules.add(relative)
    assert factory_modules == set(_certification_unit_modules()), factory_modules


def test_require_qualified_host_refuses_instead_of_skipping_and_names_every_reason():
    """Negative control: an unqualified host reds the node with the literal and its evidence.

    A skip is the failure mode this whole phase exists to remove - it is a green a reader cannot
    tell apart from a certification.
    """

    limits = latency_calibration.HOST_QUALIFICATION_LIMITS
    loaded = {signal: limit * 4 for signal, limit in limits.items()}
    with pytest.raises(latency_calibration.NotCertifiableError) as refusal:
        latency_calibration.require_qualified_host(
            nodeid="tests/test_check_runner.py::negative-control",
            label="negative control",
            measurement=loaded,
            evidence_root=Path("/tmp/yolomux-latency-evidence"),
        )
    assert latency_calibration.NOT_CERTIFIABLE in str(refusal.value)
    assert {reason["signal"] for reason in refusal.value.evidence["reasons"]} == set(limits), refusal.value.evidence
    assert refusal.value.evidence["stage"] == "host_qualification"
    artifact = json.loads(Path(refusal.value.evidence["artifact"]).read_text(encoding="utf-8"))
    assert artifact["qualification"]["qualified"] is False and artifact["qualification"]["reasons"], artifact

    # It is an AssertionError, so it reds the node; it is NOT a Skipped, so nothing reports green.
    assert isinstance(refusal.value, AssertionError)
    assert not isinstance(refusal.value, pytest.skip.Exception)

    # A qualified host returns the measurement rather than raising, and returns the same dict shape
    # the phase runner reads.
    inside = {signal: limit / 2 for signal, limit in limits.items()}
    qualified = latency_calibration.require_qualified_host(nodeid="n", label="l", measurement=inside)
    assert qualified["qualified"] is True and qualified["reasons"] == []


def test_certification_host_qualification_requires_two_clean_windows_after_a_transient(monkeypatch):
    limits = latency_calibration.HOST_QUALIFICATION_LIMITS
    red = latency_calibration.host_qualification({signal: limit * 4 for signal, limit in limits.items()})
    green = latency_calibration.host_qualification({signal: limit / 4 for signal, limit in limits.items()})
    windows = iter((red, green, green))
    calls = []

    def qualify(**kwargs):
        calls.append(kwargs)
        return next(windows)

    monkeypatch.setattr(latency_calibration, "host_qualification", qualify)
    recovered = latency_calibration.certification_host_qualification()

    assert recovered["qualified"] is True
    assert len(calls) == 3
    assert recovered["confirmation"]["recovered_transient"] is True
    assert [window["qualified"] for window in recovered["confirmation"]["windows"]] == [False, True, True]
    assert recovered["confirmation"]["maximum_windows"] == latency_calibration.HOST_QUALIFICATION_MAX_WINDOWS


def test_certification_host_qualification_converges_after_the_observed_post_gate_red_green_red(monkeypatch):
    limits = latency_calibration.HOST_QUALIFICATION_LIMITS
    red = latency_calibration.host_qualification({signal: limit * 4 for signal, limit in limits.items()})
    green = latency_calibration.host_qualification({signal: limit / 4 for signal, limit in limits.items()})
    sequence = (False, True, False, True, True)
    windows = iter(green if qualified else red for qualified in sequence)
    monkeypatch.setattr(latency_calibration, "host_qualification", lambda **_kwargs: next(windows))

    recovered = latency_calibration.certification_host_qualification()

    assert recovered["qualified"] is True
    assert recovered["confirmation"]["recovered_transient"] is True
    assert [window["qualified"] for window in recovered["confirmation"]["windows"]] == list(sequence)


@pytest.mark.parametrize(
    "sequence",
    [
        (False, False, False, False, False),
        (False, True, False, True, False),
        (False, False, True, False, True),
    ],
)
def test_certification_host_qualification_never_recovers_without_two_clean_confirmations(monkeypatch, sequence):
    limits = latency_calibration.HOST_QUALIFICATION_LIMITS
    red = latency_calibration.host_qualification({signal: limit * 4 for signal, limit in limits.items()})
    green = latency_calibration.host_qualification({signal: limit / 4 for signal, limit in limits.items()})
    windows = iter(green if qualified else red for qualified in sequence)
    monkeypatch.setattr(latency_calibration, "host_qualification", lambda **_kwargs: next(windows))

    refused = latency_calibration.certification_host_qualification()

    assert refused["qualified"] is False
    assert refused["confirmation"]["recovered_transient"] is False
    assert [window["qualified"] for window in refused["confirmation"]["windows"]] == list(sequence)


def test_certification_phase_fixture_skips_when_unasked_and_refuses_on_an_unqualified_host(monkeypatch):
    """The two outcomes of the shared fixture are deliberately different, and neither is a pass."""

    class _Node:
        name = "test_probe_certification_unit"
        nodeid = "tests/test_probe.py::test_probe_certification_unit"

    class _Request:
        node = _Node()

        class config:
            class invocation_params:
                args = ("tests/",)

    fixture_function = latency_calibration.certification_phase_fixture("YOLOMUX_PROBE_CERTIFICATION").__wrapped__

    # Not asked for: a skip, which the phase runner reports as certification_unit_did_not_run.
    monkeypatch.delenv("YOLOMUX_PROBE_CERTIFICATION", raising=False)
    with pytest.raises(pytest.skip.Exception) as skipped:
        fixture_function(_Request())
    assert "YOLOMUX_PROBE_CERTIFICATION" in str(skipped.value)

    # Asked for on an unqualified host: NOT CERTIFIABLE, never a skip.
    monkeypatch.setenv("YOLOMUX_PROBE_CERTIFICATION", "1")
    limits = latency_calibration.HOST_QUALIFICATION_LIMITS
    monkeypatch.setattr(
        latency_calibration,
        "measure_host_resources",
        lambda **_kwargs: {signal: limit * 5 for signal, limit in limits.items()},
    )
    with pytest.raises(latency_calibration.NotCertifiableError) as refusal:
        fixture_function(_Request())
    assert latency_calibration.NOT_CERTIFIABLE in str(refusal.value)
    assert refusal.value.evidence["nodeid"] == _Node.nodeid

    # Asked for on a qualified host: the measurement is handed to the unit, not swallowed.
    monkeypatch.setattr(
        latency_calibration,
        "measure_host_resources",
        lambda **_kwargs: {signal: limit / 3 for signal, limit in limits.items()},
    )
    assert fixture_function(_Request())["qualified"] is True


def test_certify_verdicts_cannot_reach_green_through_a_refusal(tmp_path):
    """Negative control: every verdict inside its ceiling still refuses on an unqualified host.

    The measurement here is comfortably green. Only the host decides the outcome, and the refusal
    carries the raw verdicts it declined to certify rather than discarding them.
    """

    evidence_root = Path("/tmp") / "yolomux-latency-evidence"
    green = [latency_calibration.fixed_ceiling_verdict(label=f"op-{index}", raw_measured_ms=1.0, ceiling_ms=100.0) for index in range(3)]
    assert all(verdict["passed"] for verdict in green)

    unqualified = {
        "qualified": False,
        "reasons": [
            {
                "signal": "cpu_stall_some_fraction",
                "measured": 0.42,
                "limit": latency_calibration.HOST_QUALIFICATION_LIMITS["cpu_stall_some_fraction"],
                "reason": "over_limit",
            }
        ],
    }
    with pytest.raises(latency_calibration.NotCertifiableError) as refusal:
        latency_calibration.certify_verdicts(
            nodeid="tests/test_check_runner.py::negative-control",
            label="negative control",
            verdicts=green,
            qualification=unqualified,
            evidence_root=evidence_root,
        )
    assert latency_calibration.NOT_CERTIFIABLE in str(refusal.value)
    assert [verdict["passed"] for verdict in refusal.value.evidence["verdicts"]] == [True, True, True]
    assert refusal.value.evidence["reasons"] == unqualified["reasons"]

    # And a qualified host with one breach is a breach, not a refusal: the ceiling never moves.
    qualified = {"qualified": True, "reasons": []}
    breach = [*green, latency_calibration.fixed_ceiling_verdict(label="op-slow", raw_measured_ms=101.0, ceiling_ms=100.0)]
    with pytest.raises(AssertionError) as failure:
        latency_calibration.certify_verdicts(nodeid="n", label="breach", verdicts=breach, qualification=qualified, evidence_root=evidence_root)
    assert not isinstance(failure.value, latency_calibration.NotCertifiableError)
    assert "op-slow" in str(failure.value)
    assert latency_calibration.certify_verdicts(nodeid="n", label="green", verdicts=green, qualification=qualified, evidence_root=evidence_root)["verdicts"] == green


CERTIFICATION_LOAD_FILE_BYTES = 262_144


def _burn_cpu_and_disk(deadline_monotonic, scratch):
    """One fixed load unit: a busy CPU loop plus repeated fsynced writes, until the deadline."""

    value = 1
    payload = b"x" * CERTIFICATION_LOAD_FILE_BYTES
    with open(scratch, "wb", buffering=0) as handle:
        while time.monotonic() < deadline_monotonic:
            for index in range(200_000):
                value = (value ^ index) * 2654435761 & 0xFFFFFFFF
            handle.seek(0)
            handle.write(payload)
            os.fsync(handle.fileno())


certification_phase_only = latency_calibration.certification_phase_fixture()


def test_certification_host_qualifier_refuses_a_genuinely_loaded_host(certification_phase_only, tmp_path):
    """Prove the qualifier still separates its own qualified baseline from real CPU and fsync load.
    The refusal must name a signal this load moved, so ambient load cannot satisfy the control.
    """

    baseline = certification_phase_only["measurement"]
    scratch_filesystem = os.statvfs(tmp_path)
    available_disk_bytes = scratch_filesystem.f_bavail * scratch_filesystem.f_frsize
    # Keep one file span in reserve per worker so the negative control cannot consume all scratch.
    disk_worker_limit = available_disk_bytes // (2 * CERTIFICATION_LOAD_FILE_BYTES)
    assert disk_worker_limit >= 1, f"negative control scratch has only {available_disk_bytes} free bytes"
    workers = min(4 * (os.cpu_count() or 1), 96, disk_worker_limit)
    deadline = time.monotonic() + 6.0
    scratch_paths = [tmp_path / f"load-{index}.bin" for index in range(workers)]
    processes = [
        multiprocessing.Process(target=_burn_cpu_and_disk, args=(deadline, str(scratch_path)))
        for scratch_path in scratch_paths
    ]
    try:
        for process in processes:
            process.start()
        loaded = latency_calibration.measure_host_resources(evidence_root=Path("/tmp/yolomux-latency-evidence"), sample_seconds=1.5)
    finally:
        for process in processes:
            process.join(timeout=30)
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
        for scratch_path in scratch_paths:
            with scratch_path.open("r+b", buffering=0) as handle:
                os.fsync(handle.fileno())
            scratch_path.unlink()
        io_signals = ("io_stall_full_fraction", "io_stall_some_fraction")
        io_limits = {signal: latency_calibration.HOST_QUALIFICATION_LIMITS[signal] for signal in io_signals}
        io_drain = latency_calibration.certification_host_qualification(
            evidence_root=Path("/tmp/yolomux-latency-evidence"), limits=io_limits
        )
    assert io_drain["qualified"], io_drain
    refusal = latency_calibration.host_qualification(loaded)
    reported = [*latency_calibration.HOST_QUALIFICATION_LIMITS, *latency_calibration.HOST_QUALIFICATION_EVIDENCE_ONLY]
    evidence = {
        "workers": workers,
        "baseline": {signal: baseline.get(signal) for signal in reported},
        "loaded": {signal: loaded.get(signal) for signal in reported},
        "evidence_only": refusal["evidence_only"],
        "reasons": refusal["reasons"],
    }
    artifact = latency_calibration.write_latency_evidence(
        nodeid="tests/test_check_runner.py::test_certification_host_qualifier_refuses_a_genuinely_loaded_host", label="host-qualifier-negative-control", payload=evidence,
    )
    print(f"host qualifier negative control: {evidence}; artifact={artifact}")

    assert refusal["qualified"] is False, {**evidence, "artifact": str(artifact)}
    moved = [
        reason["signal"]
        for reason in refusal["reasons"]
        if reason["reason"] == "over_limit"
        and baseline.get(reason["signal"]) is not None
        and float(reason["measured"]) > float(baseline[reason["signal"]])
    ]
    assert moved, {**evidence, "artifact": str(artifact)}
    # Require both CPU signals so one fragile signal cannot carry the control; up to 96 workers moved them from 4-17 to 95-120 and 0.0018-0.0100 to 0.483-0.842.
    # Disk signals are not required because container scratch differs from the host volumes measured by /proc/diskstats; host-side probes of this load read 0.918-0.980.
    # disk_in_flight_max remains evidence-only because qualified hosts reach higher values than saturated ones.
    over_limit = {reason["signal"] for reason in refusal["reasons"] if reason["reason"] == "over_limit"}
    assert {"procs_running_p75", "cpu_stall_some_fraction"} <= over_limit, {
        **evidence,
        "over_limit": sorted(over_limit),
        "artifact": str(artifact),
    }
    assert "disk_in_flight_max" not in over_limit, sorted(over_limit)
    assert "disk_in_flight_max" in latency_calibration.HOST_QUALIFICATION_EVIDENCE_ONLY
    # The dropped signal remains measured on both sides, and identical limits apply in both directions.
    assert baseline["disk_in_flight_max"] is not None and loaded["disk_in_flight_max"] is not None
    assert refusal["limits"] == latency_calibration.host_qualification(baseline)["limits"] == dict(latency_calibration.HOST_QUALIFICATION_LIMITS)


def test_certification_refusal_prints_the_literal_not_certifiable_with_its_evidence(capsys):
    check = load_check_module()
    payload = {
        "result": "not-certifiable",
        "reason": "host_unqualified_preflight",
        "evidence": [{"signal": "io_stall_some_fraction", "measured": 0.31, "limit": 0.05}],
        "wall_seconds": 2.0,
        "evidence_dir": "/tmp/yolomux-certification/probe",
    }
    check.print_certification(payload, None)
    output = capsys.readouterr().out
    assert latency_calibration.NOT_CERTIFIABLE in output, output
    assert "io_stall_some_fraction" in output and "0.31" in output, output
    assert "/tmp/yolomux-certification/probe" in output, output


def test_certification_exit_code_is_distinct_from_a_lane_failure():
    check = load_check_module()
    assert check.EXIT_NOT_CERTIFIABLE == 4
    assert len({check.EXIT_LANE_FAILED, check.EXIT_USAGE, check.EXIT_REFUSED, check.EXIT_NOT_CERTIFIABLE}) == 4


@contextmanager
def owned_process_tree():
    """One process that owns one child, so assertions never depend on the caller's own tree.

    The pytest process running this test has its own descendants, and inside the gate it has many.
    A retirement assertion made against `os.getpid()` therefore measures the suite, not the code.
    """

    owner = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, sys, time; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "time.sleep(60)",
        ]
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not descendant_pids_of(owner.pid):
            time.sleep(0.05)
        yield owner
    finally:
        owner.kill()
        owner.wait(timeout=10)
        for pid in descendant_pids_of(owner.pid):
            os.kill(pid, signal.SIGKILL)


def descendant_pids_of(pid):
    check = load_check_module()
    return [process["pid"] for process in check.descendant_processes(pid)]


def test_retirement_joins_a_real_owned_child_and_reports_a_survivor_by_deadline(monkeypatch):
    """Retirement is a join on a measured predicate, and a breach names the surviving process."""

    check = load_check_module()
    # The container half has its own tests below; this one is about the process walk, and the gate
    # itself runs inside the test image where no docker client exists at all.
    monkeypatch.setattr(check, "running_test_containers", lambda: {"available": True, "reason": "", "image": "probe", "containers": []})
    with owned_process_tree() as owner:
        grandchildren = descendant_pids_of(owner.pid)
        assert grandchildren, "the owner process never started its child"
        breach = check.retire_owned_processes(pid=owner.pid, deadline_seconds=0.5)
        assert breach["retired"] is False, breach
        assert set(grandchildren) <= {process["pid"] for process in breach["survivors"]}, breach
        assert breach["seconds"] >= 0.5, breach
        assert breach["owner_pid"] == owner.pid

    joined = check.retire_owned_processes(pid=owner.pid, deadline_seconds=10)
    assert joined["retired"] is True and joined["survivors"] == [], joined
    assert joined["sync_seconds"] >= 0, joined


def test_certification_evidence_dir_must_stay_under_tmp():
    check = load_check_module()
    assert check.certification_evidence_dir("/tmp/yolomux-certification/explicit") == Path("/tmp/yolomux-certification/explicit")
    assert str(check.certification_evidence_dir(None)).startswith("/tmp/yolomux-certification/")
    with pytest.raises(ValueError):
        check.certification_evidence_dir(str(REPO_ROOT / "docs"))


def test_certification_only_refuses_a_lane_selection():
    """--certification-only runs no lanes, so combining it with --lane is a usage error, not a silent win."""

    check = load_check_module()
    with pytest.raises(SystemExit) as exit_info:
        check.main(["--certification-only", "--lane", "whitespace"])
    assert exit_info.value.code == check.EXIT_USAGE


def test_host_qualification_refuses_over_limit_signals_and_names_each_one():
    """Every unqualified signal is reported with its measured value and its limit, never summarised."""

    limits = dict(latency_calibration.HOST_QUALIFICATION_LIMITS)
    inside = {signal: limit / 2 for signal, limit in limits.items()}
    assert latency_calibration.host_qualification(inside, limits=limits)["qualified"] is True

    over = {**inside, "disk_busy_fraction_max": limits["disk_busy_fraction_max"] * 3, "procs_running_p75": limits["procs_running_p75"] * 4}
    refusal = latency_calibration.host_qualification(over, limits=limits)
    assert refusal["qualified"] is False
    assert {reason["signal"] for reason in refusal["reasons"]} == {"disk_busy_fraction_max", "procs_running_p75"}
    assert all(reason["reason"] == "over_limit" and reason["measured"] > reason["limit"] for reason in refusal["reasons"])

    # Fail closed: a signal this kernel does not expose is an unqualified host, never a silent pass.
    absent = {**inside, "io_stall_some_fraction": None}
    unavailable = latency_calibration.host_qualification(absent, limits=limits)
    assert unavailable["qualified"] is False
    assert unavailable["reasons"] == [{"signal": "io_stall_some_fraction", "measured": None, "limit": limits["io_stall_some_fraction"], "reason": "signal_unavailable"}]


def test_host_qualification_measures_this_host_with_windowed_and_instantaneous_signals():
    """The real probe must return every asserted signal; a decaying average is evidence only."""

    measurement = latency_calibration.measure_host_resources(evidence_root=Path("/tmp/yolomux-latency-evidence"))
    for signal in latency_calibration.HOST_QUALIFICATION_LIMITS:
        assert signal in measurement, (signal, sorted(measurement))
    assert measurement["window_seconds"] >= latency_calibration.HOST_SAMPLE_SECONDS
    # Never "at least 2": the instantaneous samples are taken in whatever is left of the window
    # after the two work units, which is least when the host is busiest. A 2026-08-08 probe under
    # 96-worker saturation came back with ONE procs_running sample and reported its p75 from it.
    assert measurement["procs_running_samples"] >= latency_calibration.HOST_INSTANT_SAMPLE_MINIMUM, measurement
    assert len(measurement["cpu_work_samples_ms"]) == latency_calibration.HOST_CPU_WORK_SAMPLES
    assert len(measurement["storage_work_samples_ms"]) == latency_calibration.HOST_STORAGE_WORK_SAMPLES
    assert measurement["cpu_work_median_ms"] == round(latency_calibration.work_unit_statistic(measurement["cpu_work_samples_ms"]), 3)
    assert measurement["storage_work_median_ms"] == round(latency_calibration.work_unit_statistic(measurement["storage_work_samples_ms"]), 3)
    # Recorded, never asserted.
    assert "lagging_load_average" in measurement
    assert "lagging_load_average" not in latency_calibration.HOST_QUALIFICATION_LIMITS

    # Same for every signal the placement rule retired from the limits: measured, reported, and
    # reported with the detail that disqualified it. `disk_in_flight_max` is a literal maximum over
    # instantaneous queue depths, so its own p75 and per-device split are what show the maximum to
    # be a lone instant on a stacked device-mapper volume rather than a busy host.
    for signal, reason in latency_calibration.HOST_QUALIFICATION_EVIDENCE_ONLY.items():
        assert signal in measurement, (signal, sorted(measurement))
        assert signal not in latency_calibration.HOST_QUALIFICATION_LIMITS, signal
        assert reason and reason.split(":")[0].isidentifier(), (signal, reason)
    assert measurement["disk_in_flight_p75"] is not None and measurement["disk_in_flight_samples"] >= latency_calibration.HOST_INSTANT_SAMPLE_MINIMUM
    assert measurement["disk_in_flight_p75"] <= measurement["disk_in_flight_max"], measurement
    assert set(measurement["disk_in_flight_max_per_device"]) == set(measurement["disk_devices"]), measurement

    # And a qualification carries the same reasons, so one artifact is enough to audit the drop.
    qualification = latency_calibration.host_qualification(measurement)
    assert qualification["evidence_only"] == dict(latency_calibration.HOST_QUALIFICATION_EVIDENCE_ONLY)
    assert {reason["signal"] for reason in qualification["reasons"]} & set(qualification["evidence_only"]) == set()
    # A caller that supplies its own limits covering one of these names DID assert it, so the
    # payload must stop claiming it was left unasserted rather than repeat the module constant.
    asserted_instead = latency_calibration.host_qualification(measurement, limits={"disk_in_flight_max": 8.0})
    assert asserted_instead["evidence_only"] == {}, asserted_instead["evidence_only"]


def test_host_qualification_initializes_storage_probe_before_measured_counters(monkeypatch, tmp_path):
    """A fresh evidence root must not count its own SQLite initialization as host load."""

    evidence_root = tmp_path / "host-qualification"
    probe_path = latency_calibration.host_storage_probe_path(evidence_root)
    events = []
    original_storage_work = latency_calibration._storage_work_samples_ms

    def read_pressure():
        events.append(("pressure", probe_path.exists()))
        return None

    def read_disk():
        events.append(("disk", probe_path.exists()))
        return {}

    def storage_work(path):
        events.append(("storage-before", path.exists()))
        samples = original_storage_work(path)
        events.append(("storage-after", path.exists()))
        return samples

    monkeypatch.setattr(latency_calibration, "_read_pressure_totals", read_pressure)
    monkeypatch.setattr(latency_calibration, "_read_disk_counters", read_disk)
    monkeypatch.setattr(latency_calibration, "_read_procs_running", lambda: None)
    monkeypatch.setattr(latency_calibration, "_cpu_work_samples_ms", lambda: [1.0] * latency_calibration.HOST_CPU_WORK_SAMPLES)
    monkeypatch.setattr(latency_calibration, "_storage_work_samples_ms", storage_work)
    monkeypatch.setattr(latency_calibration, "HOST_INSTANT_SAMPLE_MINIMUM", 1)

    measurement = latency_calibration.measure_host_resources(evidence_root=evidence_root, sample_seconds=0)

    assert probe_path.exists()
    assert events == [
        ("pressure", True),
        ("disk", True),
        ("disk", True),
        ("storage-before", True),
        ("storage-after", True),
        ("pressure", True),
        ("disk", True),
    ]
    assert len(measurement["storage_work_samples_ms"]) == latency_calibration.HOST_STORAGE_WORK_SAMPLES


def _with_outliers(samples: list[float], count: int, value: float = 10_000.0) -> list[float]:
    replaced = sorted(samples)
    for index in range(count):
        replaced[-(index + 1)] = value
    return replaced


def test_no_host_qualifier_statistic_is_moved_by_a_minority_of_its_samples():
    """The defect class this whole module exists to avoid: a statistic that is effectively a maximum.

    `cpu_work_p75_ms` used to be `nearest_rank(7 samples, 0.75)`, which selects the 6th of 7. Two
    scheduler excursions out of seven therefore set the reported value, and on 2026-08-08 that
    refused this box twice while every other signal sat at baseline. A statistic a minority of its
    own samples can move reports the worst instants of a probe, not what the probe measured.
    """

    for sample_count in (latency_calibration.HOST_CPU_WORK_SAMPLES, latency_calibration.HOST_STORAGE_WORK_SAMPLES):
        quiet = [10.0 + index / 1000 for index in range(sample_count)]
        at_baseline = latency_calibration.work_unit_statistic(quiet)
        minority = (sample_count - 1) // 2
        assert minority >= 3, sample_count
        assert latency_calibration.work_unit_statistic(_with_outliers(quiet, minority)) == at_baseline, sample_count
        # Not a constant either: a MAJORITY of starved samples still moves it, so the guard fires.
        assert latency_calibration.work_unit_statistic(_with_outliers(quiet, minority + 1)) > at_baseline, sample_count

    # procs_running keeps nearest-rank p75 - measured as a real p75, not a near-maximum, because
    # HOST_INSTANT_SAMPLE_MINIMUM keeps a quarter of the samples above the selected index.
    instants = [1.0] * latency_calibration.HOST_INSTANT_SAMPLE_MINIMUM
    selected_index = math.ceil(len(instants) * 0.75) - 1
    assert latency_calibration.outliers_required_to_move(len(instants), selected_index) >= 5
    assert latency_calibration.nearest_rank(_with_outliers(instants, 4), 0.75) == 1.0
    # The retired shape, kept as an executable statement of what was wrong: 7 samples needed 2.
    assert latency_calibration.outliers_required_to_move(7, math.ceil(7 * 0.75) - 1) == 2


def test_the_instantaneous_sample_floor_still_terminates_when_the_kernel_exposes_nothing(monkeypatch):
    """Fail closed, and above all do not hang: the floor counts READS, not collected values.

    `HOST_INSTANT_SAMPLE_MINIMUM` keeps `procs_running_p75` an actual p75 when the work units eat
    the window. Spelling that floor as "until enough samples exist" would spin forever on any host
    without /proc/stat - every macOS run of this same module - instead of refusing it.
    """

    monkeypatch.setattr(latency_calibration, "_read_procs_running", lambda: None)
    started = time.monotonic()
    measurement = latency_calibration.measure_host_resources(evidence_root=Path("/tmp/yolomux-latency-evidence"), sample_seconds=0.2)
    assert time.monotonic() - started < 60.0
    assert measurement["procs_running_p75"] is None and measurement["procs_running_samples"] == 0, measurement
    refusal = latency_calibration.host_qualification(measurement)
    assert refusal["qualified"] is False
    unavailable_limit = latency_calibration.HOST_QUALIFICATION_LIMITS["procs_running_p75"]
    assert {"signal": "procs_running_p75", "measured": None, "limit": unavailable_limit, "reason": "signal_unavailable"} in refusal["reasons"], refusal["reasons"]


def test_recorded_baseline_samples_qualify_while_recorded_saturated_samples_still_refuse():
    """Red before green, on the exact raw samples that were measured on this box on 2026-08-08.

    The first vector is the certification-phase refusal the user hit: five rounds clustered near
    12 ms and two excursions. Its median is 12.48 ms and every other signal in that run was at
    baseline, so the host was fit and the statistic said otherwise. The second is a real probe from
    the 96-worker load the phase's own negative control drives, and it must still refuse.
    """

    recorded_false_refusal_ms = [12.28, 12.26, 12.28, 12.48, 16.29, 21.52, 22.42]
    recorded_saturated_ms = [29.88, 17.06, 54.08, 52.86, 52.14, 35.88, 31.84, 61.99, 58.06, 52.36, 86.64, 40.82, 16.97, 40.4, 32.22]
    limits = latency_calibration.HOST_QUALIFICATION_LIMITS
    elsewhere_at_baseline = {signal: limit / 4 for signal, limit in limits.items()}

    fit = latency_calibration.host_qualification(
        {**elsewhere_at_baseline, "cpu_work_median_ms": latency_calibration.work_unit_statistic(recorded_false_refusal_ms)}
    )
    assert fit["qualified"] is True, fit["reasons"]
    # And the retired statistic on the same samples is what refused it.
    assert latency_calibration.nearest_rank(recorded_false_refusal_ms, 0.75) > 20.0

    refused = latency_calibration.host_qualification(
        {**elsewhere_at_baseline, "cpu_work_median_ms": latency_calibration.work_unit_statistic(recorded_saturated_ms)}
    )
    assert refused["qualified"] is False
    assert [reason["signal"] for reason in refused["reasons"]] == ["cpu_work_median_ms"], refused["reasons"]
    assert refused["reasons"][0]["reason"] == "over_limit"


def test_recorded_post_lane_refusals_now_qualify_while_a_recorded_saturated_probe_still_refuses():
    """Red before green on whole measurements, not one signal at a time.

    The first vector is verbatim the probe that refused
    tests/test_gate_interaction.py::test_i3b_certification_dockview_load_layout_holds_the_fixed_ceiling
    inside a real exclusive phase on 2026-08-08, artifact
    test_i3b...-host-qualification-7-1786202890311143193-6509da16.json. Every signal in it sits
    between 3x and 12x inside its own limit and the host was refused anyway, on a single
    instantaneous device-mapper queue depth of 21 against a limit of 8. Two of three canonical gate
    attempts and one of two measured here died exactly this way.

    The second is the least extreme of 12 probes measured under the negative control's own
    96-worker CPU+fsync load, chosen deliberately: if the weakest saturated probe still refuses,
    every one of them does.
    """

    recorded_i3b_refusal = {
        "procs_running_p75": 8.0,
        "cpu_stall_some_fraction": 0.007364,
        "io_stall_some_fraction": 0.005838,
        "io_stall_full_fraction": 0.004946,
        "memory_stall_full_fraction": 0.0,
        "disk_busy_fraction_max": 0.109894,
        "disk_in_flight_max": 21,
        "cpu_work_median_ms": 12.2,
        "storage_work_median_ms": 2.76,
    }
    # The other canonical attempt, refused mid-run on cpu some-stall while its limit sat inside the
    # range an idle box already reaches.
    recorded_cpu_stall_refusal = {**recorded_i3b_refusal, "disk_in_flight_max": 1, "cpu_stall_some_fraction": 0.033485}
    weakest_saturated_probe = {
        "procs_running_p75": 104.0,
        "cpu_stall_some_fraction": 0.482971,
        "io_stall_some_fraction": 0.220091,
        "io_stall_full_fraction": 0.105317,
        "memory_stall_full_fraction": 0.0,
        "disk_busy_fraction_max": 0.917673,
        "disk_in_flight_max": 68,
        "cpu_work_median_ms": 16.225,
        "storage_work_median_ms": 9.647,
    }

    for recorded in (recorded_i3b_refusal, recorded_cpu_stall_refusal):
        # Red before: the retired set refused these, and on exactly the signals it was refused on.
        retired = latency_calibration.host_qualification(recorded, limits={**latency_calibration.HOST_QUALIFICATION_LIMITS, "disk_in_flight_max": 8.0, "cpu_stall_some_fraction": 0.030})
        assert retired["qualified"] is False, recorded
        # Green after.
        qualified = latency_calibration.host_qualification(recorded)
        assert qualified["qualified"] is True, qualified["reasons"]
        # The dropped signal is still measured and still reported, with its reason attached.
        assert qualified["measurement"]["disk_in_flight_max"] == recorded["disk_in_flight_max"]
        assert "disk_in_flight_max" in qualified["evidence_only"], qualified["evidence_only"]

    refused = latency_calibration.host_qualification(weakest_saturated_probe)
    assert refused["qualified"] is False
    fired = sorted(reason["signal"] for reason in refused["reasons"])
    # Never one signal away from a gate that cannot fail, and never carried by the dropped one.
    assert len(fired) >= 4, fired
    assert "disk_in_flight_max" not in fired, fired
    assert {"procs_running_p75", "cpu_stall_some_fraction", "io_stall_some_fraction", "disk_busy_fraction_max"} <= set(fired), fired


def test_every_asserted_limit_keeps_its_declared_margin_over_the_post_lane_population():
    """A threshold is answerable to a recorded population, not to the red it was asked to remove.

    And to the RIGHT population: the exclusive phase runs after `retire_owned_processes()`, never
    on an idle box, so `post_lane` is what every limit is placed against. Two properties, one
    margin constant, checked for every signal so the two sets cannot drift from the data:

    * safety - an asserted limit sits at or above GUARD_MARGIN x the highest value the statistic
      reached post-lane. Below that it refuses hosts the phase routinely runs on, which is what the
      retired set did: replayed over 84 post-lane probes it refused 12 of them.
    * reachability - a signal whose safety floor lands at or above the highest value real
      saturation produces cannot both stop refusing a fit host and still refuse a loaded one. It
      carries no threshold and appears in HOST_QUALIFICATION_EVIDENCE_ONLY with its reason.
    """

    populations = latency_calibration.HOST_QUALIFICATION_MEASURED_POPULATIONS
    limits = latency_calibration.HOST_QUALIFICATION_LIMITS
    evidence_only = latency_calibration.HOST_QUALIFICATION_EVIDENCE_ONLY
    margin = latency_calibration.HOST_QUALIFICATION_GUARD_MARGIN
    measured_signals = set(limits) | set(evidence_only)
    assert set(limits) & set(evidence_only) == set(), "a signal is asserted or it is evidence, never both"

    for name in ("post_lane", "baseline", "gate_loaded", "saturated"):
        recorded = populations[name]
        assert recorded["probes"] >= 19, (name, recorded["probes"])
        missing = [signal for signal in measured_signals if signal not in recorded]
        assert missing == [], (name, missing)
        for signal in measured_signals:
            low, high = recorded[signal]
            assert low <= high, (name, signal, low, high)

    for signal in sorted(measured_signals):
        floor = margin * populations["post_lane"][signal][1]
        saturated_high = populations["saturated"][signal][1]
        if signal in evidence_only:
            # The rule itself makes the drop, so the drop cannot be a preference. Both halves are
            # asserted: no safe limit is reachable, AND the inversion that causes it is recorded.
            assert floor >= saturated_high, (signal, floor, saturated_high)
            assert populations["post_lane"][signal][1] > populations["saturated"][signal][0], signal
            assert evidence_only[signal].startswith("populations_inverted"), signal
            continue
        assert limits[signal] >= floor, (signal, limits[signal], floor)

    for signal in ("cpu_work_median_ms", "storage_work_median_ms"):
        # A guard that a real gate trips is a discriminator with a badly placed limit, and this box
        # has already shown it is neither: keep the limit above the whole recorded gate population.
        assert limits[signal] > populations["gate_loaded"][signal][1], (signal, limits[signal])

    # Every asserted limit the negative control's load can reach must still be reachable by it, so
    # nothing survives here as a threshold that can no longer refuse anything.
    for signal in sorted(limits):
        saturated_low, saturated_high = populations["saturated"][signal]
        if saturated_high == 0.0:
            # The one exemption, and it is not a judgement call: this load never moved the signal
            # at all, so it is a fail-closed guard on a mode the negative control does not create.
            assert [saturated_low, saturated_high] == [0.0, 0.0], signal
            assert signal == "memory_stall_full_fraction", signal
            continue
        assert limits[signal] < saturated_high, (signal, limits[signal], saturated_high)


def test_host_qualification_cli_exits_four_with_the_literal_on_an_unqualified_host(tmp_path):
    """The same refusal, reachable as a standalone command for an operator or another runner."""

    environment = dict(os.environ)
    completed = subprocess.run(
        [sys.executable, "-m", "tests.latency_calibration", "--evidence-root", "/tmp/yolomux-latency-evidence"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode in (0, 4), completed.stdout + completed.stderr
    payload, _end = json.JSONDecoder().raw_decode(completed.stdout)
    assert payload["qualified"] is (completed.returncode == 0)
    if completed.returncode == 4:
        assert latency_calibration.NOT_CERTIFIABLE in completed.stdout
        assert payload["reasons"], payload


def test_retirement_probe_does_not_observe_itself(monkeypatch):
    """The snapshot is taken by a child process, so an unfiltered table can never retire.

    Observed exactly once as `owned_processes_not_retired` naming `ps -eo pid=,ppid=,command=` as
    the sole survivor: the probe was measuring the measurement.
    """

    check = load_check_module()
    monkeypatch.setattr(check, "running_test_containers", lambda: {"available": True, "reason": "", "image": "probe", "containers": []})
    quiet = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        owned = check.descendant_processes(quiet.pid)
        assert owned == [], owned
        assert [process for process in check.descendant_processes(os.getpid()) if "ps -eo" in str(process["command"])] == []
        joined = check.retire_owned_processes(pid=quiet.pid, deadline_seconds=5)
        assert joined["retired"] is True, joined
        assert joined["seconds"] < 5, joined
    finally:
        quiet.kill()
        quiet.wait(timeout=10)


def test_retirement_counts_live_test_containers_the_process_walk_cannot_see(monkeypatch):
    """pytest re-executes inside the test image, so the lanes' real work is not our descendant.

    A retirement check that only walked the process tree reported "retired" while a full browser
    suite was still winding down inside a container, and the qualifier then measured that wind-down
    as ambient load.
    """

    check = load_check_module()
    image = check.docker_image.image_name(check.REPO_ROOT)
    observed_command = []

    class Completed:
        returncode = 0
        stdout = "abc123\tUp 12 seconds\ndef456\tUp 3 seconds\n"

    def fake_run(command, **_kwargs):
        observed_command.append(command)
        return Completed()

    monkeypatch.delenv(check.CHECK_RUN_TOKEN_ENV, raising=False)
    monkeypatch.setattr(check.subprocess, "run", fake_run)
    probe = check.running_test_containers()
    assert observed_command[0] == ["docker", "ps", "--filter", f"ancestor={image}", "--format", "{{.ID}}\t{{.Status}}"]
    assert probe["available"] is True and probe["image"] == image
    assert [entry["container"] for entry in probe["containers"]] == ["abc123", "def456"]

    monkeypatch.setattr(check, "descendant_processes", lambda _pid: [])
    monkeypatch.setattr(check, "running_test_containers", lambda: probe)
    retirement = check.retire_owned_processes(deadline_seconds=0.3)
    assert retirement["retired"] is False, retirement
    assert retirement["container_probe"] == probe
    assert retirement["sync_seconds"] >= 0

    verdict = check.certification_verdict(retirement=retirement, preflight=None, postflight=None, outcomes=None, returncode=None)
    assert verdict["reason"] == "owned_processes_not_retired"
    assert verdict["evidence"]["container_probe"] == probe


def test_running_test_containers_reports_why_it_could_not_observe_docker(monkeypatch):
    """An unobservable docker client must carry its reason, never look like a proven-clean result.

    The gate's own tests execute inside the test image, which has no docker client: the first
    version raised FileNotFoundError straight through the phase and failed this suite in all four
    A/B runs.
    """

    check = load_check_module()

    class Failed:
        returncode = 1
        stdout = ""
        stderr = "Cannot connect to the Docker daemon"

    monkeypatch.setattr(check.subprocess, "run", lambda command, **_kwargs: Failed())
    refused = check.running_test_containers()
    assert refused == {"available": False, "reason": "Cannot connect to the Docker daemon", "image": check.docker_image.image_name(check.REPO_ROOT), "containers": []}

    def missing_binary(command, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", "docker")

    monkeypatch.setattr(check.subprocess, "run", missing_binary)
    absent = check.running_test_containers()
    assert absent["available"] is False and absent["containers"] == []
    assert "docker client unavailable" in absent["reason"], absent

    # And retirement still completes on such a host rather than raising through the phase.
    monkeypatch.setattr(check, "descendant_processes", lambda _pid: [])
    retirement = check.retire_owned_processes(deadline_seconds=0.3)
    assert retirement["retired"] is True, retirement
    assert retirement["container_probe"]["available"] is False


def test_running_test_containers_tracks_exact_run_owned_ids_by_token_label(monkeypatch):
    """A run that minted a token filters on its exact owner label, never the shared image ancestor.

    A foreign agent's container built from the identical test image carries no owner label, so it
    can neither block this run's certification nor falsely clear it.
    """

    check = load_check_module()
    image = check.docker_image.image_name(check.REPO_ROOT)
    observed = []

    class Completed:
        returncode = 0
        stdout = "owned123\tUp 4 seconds\n"

    monkeypatch.setattr(check.subprocess, "run", lambda command, **_kwargs: observed.append(command) or Completed())

    # With a token, ownership is proven by the exact label; the ancestor filter never appears.
    monkeypatch.setenv(check.CHECK_RUN_TOKEN_ENV, "tok-abc")
    owned = check.running_test_containers()
    assert observed[-1] == ["docker", "ps", "--filter", f"label={check.CONTAINER_OWNER_LABEL}=tok-abc", "--format", "{{.ID}}\t{{.Status}}"], observed[-1]
    assert [entry["container"] for entry in owned["containers"]] == ["owned123"]

    # An explicit token argument overrides the environment.
    check.running_test_containers(run_token="tok-explicit")
    assert observed[-1][3] == f"label={check.CONTAINER_OWNER_LABEL}=tok-explicit", observed[-1]

    # No token anywhere: image-ancestor discovery remains the fallback for a bare invocation.
    monkeypatch.delenv(check.CHECK_RUN_TOKEN_ENV, raising=False)
    check.running_test_containers()
    assert observed[-1] == ["docker", "ps", "--filter", f"ancestor={image}", "--format", "{{.ID}}\t{{.Status}}"], observed[-1]


def test_retirement_refuses_when_docker_is_unobservable_after_the_gate_used_containers(monkeypatch):
    """An unobservable client cannot prove an owned container is gone; only a host-only run clears."""

    check = load_check_module()
    monkeypatch.setattr(check, "descendant_processes", lambda _pid: [])
    unobservable = {"available": False, "reason": "docker daemon is not reachable", "image": "probe", "containers": []}
    monkeypatch.setattr(check, "running_test_containers", lambda: unobservable)

    # The gate routed into Docker: absence is not proved, so retirement refuses and names the cause.
    refused = check.retire_owned_processes(deadline_seconds=0.3, expected_containers=True)
    assert refused["retired"] is False, refused
    assert refused["docker_unobservable"] is True, refused
    verdict = check.certification_verdict(retirement=refused, preflight=None, postflight=None, outcomes=None, returncode=None)
    assert verdict["reason"] == "owned_processes_not_retired", verdict
    assert verdict["evidence"]["docker_unobservable"] is True, verdict

    # The gate never used Docker (host run, or units running inside the image where there is no
    # client at all): there is no owned container to prove absent, and the process walk covered
    # what ran, so an unobservable client does not block.
    cleared = check.retire_owned_processes(deadline_seconds=0.3, expected_containers=False)
    assert cleared["retired"] is True and cleared["docker_unobservable"] is False, cleared


def test_retirement_shares_the_lease_start_key_owner_for_its_member_proof():
    """One owner of the reuse-proof identity: the barrier reads the WebDriver lease's start key.

    A second copy of process-start-key logic is exactly the divergence that lets one path guard a
    signal while another does not; this pins that the barrier imports the lease's owner, not a fork.
    """

    check = load_check_module()
    assert check.process_start_key is webdriver_lease.process_start_key


def test_retirement_authorizes_a_survivor_only_by_its_immutable_start_key(monkeypatch):
    """A PID whose start key changed was reused; the barrier proves the owned process gone, not alive.

    A bare descendant walk sees PID 5000 present on every poll and never retires. The proof-guarded
    barrier captures the key it first observed and, when `identity_fn` later reads a different key,
    knows the process it owned exited and the number belongs to someone else - so it retires and
    never treats the new occupant as its survivor.
    """

    check = load_check_module()
    monkeypatch.setattr(check, "running_test_containers", lambda: {"available": True, "reason": "", "image": "probe", "containers": []})
    # The descendant walk keeps reporting PID 5000 as present with the key we first captured.
    monkeypatch.setattr(check, "descendant_processes", lambda _pid: [{"pid": 5000, "ppid": 1, "command": "chrome", "start_key": "gen-A"}])

    # While the key still holds, the member is a proven survivor and retirement refuses by deadline.
    held = check.retire_owned_processes(pid=999, deadline_seconds=0.3, identity_fn=lambda _pid: "gen-A")
    assert held["retired"] is False, held
    assert [member["pid"] for member in held["survivors"]] == [5000], held

    # The PID is now reused (its key changed): the process we owned is gone. We must not keep waiting
    # on, nor ever signal, the new occupant under the stale proof - the barrier retires.
    reused = check.retire_owned_processes(pid=999, deadline_seconds=2.0, identity_fn=lambda _pid: "someone-elses-key")
    assert reused["retired"] is True, reused
    assert reused["survivors"] == [], reused
    assert reused["seconds"] < 2.0, reused


def test_retirement_proves_a_reparented_or_exited_member_gone_without_a_bare_pid_match(monkeypatch):
    """A reparented-away or exited PID reads no key at all: proven gone, never a lingering survivor."""

    check = load_check_module()
    monkeypatch.setattr(check, "running_test_containers", lambda: {"available": True, "reason": "", "image": "probe", "containers": []})
    monkeypatch.setattr(check, "descendant_processes", lambda _pid: [{"pid": 5000, "ppid": 1, "command": "chrome", "start_key": "gen-A"}])
    retirement = check.retire_owned_processes(pid=999, deadline_seconds=2.0, identity_fn=lambda _pid: None)
    assert retirement["retired"] is True, retirement
    assert retirement["survivors"] == [], retirement


def test_retirement_excludes_an_inherited_login_shell_child_but_not_its_reused_pid(monkeypatch):
    """An exec-preserved shell child predates the gate; a later PID occupant does not."""

    check = load_check_module()
    monkeypatch.setattr(check, "running_test_containers", lambda: {"available": True, "reason": "", "image": "probe", "containers": []})
    member = {"pid": 5000, "ppid": 999, "command": "ambient-helper", "start_key": "old-life"}
    monkeypatch.setattr(check, "descendant_processes", lambda _pid: [member])

    inherited = check.retire_owned_processes(
        pid=999,
        deadline_seconds=0.3,
        identity_fn=lambda _pid: "old-life",
        inherited_descendants={5000: "old-life"},
    )
    assert inherited["retired"] is True and inherited["survivors"] == [], inherited

    member["start_key"] = "new-life"
    reused = check.retire_owned_processes(
        pid=999,
        deadline_seconds=0.3,
        identity_fn=lambda _pid: "new-life",
        inherited_descendants={5000: "old-life"},
    )
    assert reused["retired"] is False and [row["pid"] for row in reused["survivors"]] == [5000], reused


def test_platform_profile_owner_keeps_linux_and_fails_closed_off_it():
    """One owner of which signals a platform certifies. Linux keeps them; Darwin omits and refuses."""

    linux = latency_calibration.platform_profile("Linux")
    assert linux["certifiable"] is True
    assert linux["limits"] == dict(latency_calibration.HOST_QUALIFICATION_LIMITS)
    assert linux["uses_inotify_capacity"] is True and linux["omitted_signals"] == []

    darwin = latency_calibration.platform_profile("Darwin")
    assert darwin["certifiable"] is False
    assert darwin["reason_code"] == "no_recorded_platform_reference_population"
    assert darwin["limits"] == {}
    # Every Linux-only kernel signal is explicitly omitted, never asserted against an absent surface.
    assert set(darwin["omitted_signals"]) == set(latency_calibration.LINUX_ONLY_SIGNALS)
    assert darwin["uses_inotify_capacity"] is False

    # PSI stalls, procs_running and the disk signals are all Linux /proc surfaces.
    assert {"cpu_stall_some_fraction", "procs_running_p75", "disk_busy_fraction_max"} <= set(latency_calibration.LINUX_ONLY_SIGNALS)


def test_darwin_profile_is_admitted_only_by_retained_discriminating_populations(monkeypatch):
    populations = {
        "quiet": {"probes": 20, "cpu_work_median_ms": [4.0, 5.0], "storage_work_median_ms": [2.0, 3.0]},
        "post_lane": {"probes": 20, "cpu_work_median_ms": [5.0, 6.0], "storage_work_median_ms": [3.0, 4.0]},
        "saturated": {"probes": 20, "cpu_work_median_ms": [20.0, 30.0], "storage_work_median_ms": [12.0, 18.0]},
    }
    monkeypatch.setattr(latency_calibration, "DARWIN_HOST_QUALIFICATION_MEASURED_POPULATIONS", populations)

    profile = latency_calibration.platform_profile("Darwin")
    assert profile["certifiable"] is True, profile
    assert profile["limits"] == {"cpu_work_median_ms": 12.0, "storage_work_median_ms": 8.0}
    assert profile["reference_populations"] == ["quiet", "post_lane", "saturated"]
    assert profile["uses_inotify_capacity"] is False


def test_darwin_profile_rejects_present_but_non_discriminating_populations(monkeypatch):
    populations = {
        "quiet": {"probes": 20, "cpu_work_median_ms": [4.0, 5.0], "storage_work_median_ms": [2.0, 3.0]},
        "post_lane": {"probes": 20, "cpu_work_median_ms": [5.0, 6.0], "storage_work_median_ms": [3.0, 4.0]},
        "saturated": {"probes": 20, "cpu_work_median_ms": [10.0, 30.0], "storage_work_median_ms": [12.0, 18.0]},
    }
    monkeypatch.setattr(latency_calibration, "DARWIN_HOST_QUALIFICATION_MEASURED_POPULATIONS", populations)
    profile = latency_calibration.platform_profile("Darwin")
    assert profile["certifiable"] is False, profile
    assert profile["reason_code"] == "darwin_reference_population_not_discriminating"


def test_host_qualification_fails_closed_on_an_unprofiled_platform(monkeypatch):
    """An unprofiled platform (no recorded reference population) is NOT CERTIFIABLE, never a silent pass."""

    monkeypatch.setattr(latency_calibration.platform, "system", lambda: "Darwin")
    # Even handed a measurement that would satisfy every Linux limit, an unprofiled platform refuses.
    inside = {signal: limit / 2 for signal, limit in latency_calibration.HOST_QUALIFICATION_LIMITS.items()}
    qualification = latency_calibration.host_qualification(inside)
    assert qualification["qualified"] is False, qualification
    assert qualification["reasons"] == [{"signal": "platform", "measured": "Darwin", "limit": None, "reason": "no_recorded_platform_reference_population"}], qualification
    assert qualification["platform_profile"]["system"] == "Darwin"

    # An explicit limit set is a deliberate override and still qualifies - the owner only governs the
    # default profile, so a caller measuring a known signal set is never blocked by the platform gate.
    explicit = latency_calibration.host_qualification(inside, limits=dict(latency_calibration.HOST_QUALIFICATION_LIMITS))
    assert explicit["qualified"] is True, explicit


def test_certification_release_context_reports_sha_platform_and_generated_bundle_hashes():
    check = load_check_module()
    context = check.certification_release_context(REPO_ROOT)
    sha = context["full_sha"]
    assert sha is None or (len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)), sha
    assert context["platform"]["system"] and context["platform"]["machine"], context
    assert set(context["generated_bundle_hashes"]) == set(static_build.ASSETS), context["generated_bundle_hashes"]
    for asset, digest in context["generated_bundle_hashes"].items():
        assert digest is None or (len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)), (asset, digest)


def test_working_tree_clean_state_names_every_tracked_and_untracked_path(tmp_path):
    check = load_check_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)

    clean = check.working_tree_clean_state(repo)
    assert clean == {"observable": True, "clean": True, "reason": "", "tracked": [], "untracked": [], "transient_untracked": []}, clean

    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    (repo / "new.txt").write_text("x\n", encoding="utf-8")
    dirty = check.working_tree_clean_state(repo)
    assert dirty["clean"] is False and dirty["observable"] is True, dirty
    assert dirty["tracked"] == ["tracked.txt"] and dirty["untracked"] == ["new.txt"], dirty


def test_release_certification_ignores_untracked_progress_scratchpads(tmp_path):
    """A STATUS-REPORT or DOIT file the agent keeps its own progress in is not a build input, so an
    UNTRACKED one must not cost a release its exact-SHA certificate. Refusing over it bought no
    safety: the operator's only remedy was to move the file out of the tree and put it back after.
    A tracked modification is still refused -- that one really does change what the SHA names."""

    check = load_check_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True)
    assert check.working_tree_clean_state(repo)["clean"] is True

    (repo / "STATUS-REPORT.md").write_text("progress\n", encoding="utf-8")
    (repo / "queues").mkdir()
    (repo / "queues" / "DOIT.7.md").write_text("queue\n", encoding="utf-8")
    excused = check.working_tree_clean_state(repo)
    assert excused["clean"] is True, excused
    # Excused, never hidden: the evidence still names every path the certificate chose to ignore.
    assert sorted(excused["transient_untracked"]) == ["STATUS-REPORT.md", "queues/DOIT.7.md"], excused
    assert excused["untracked"] == [], excused

    # A name that merely starts with the same letters is NOT a scratchpad, and still refuses.
    (repo / "DOITNOT.py").write_text("real = 1\n", encoding="utf-8")
    assert check.working_tree_clean_state(repo)["clean"] is False

    # A tracked edit changes what the SHA names, so it refuses even for a scratchpad name.
    (repo / "DOITNOT.py").unlink()
    subprocess.run(["git", "-C", str(repo), "add", "STATUS-REPORT.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "track it"], check=True)
    (repo / "STATUS-REPORT.md").write_text("edited\n", encoding="utf-8")
    tracked_edit = check.working_tree_clean_state(repo)
    assert tracked_edit["clean"] is False, tracked_edit
    assert tracked_edit["tracked"] == ["STATUS-REPORT.md"], tracked_edit


def test_exact_sha_certification_requires_a_fresh_checkout_at_both_ends():
    check = load_check_module()
    clean = {"observable": True, "clean": True, "reason": "", "tracked": [], "untracked": []}
    dirty = {"observable": True, "clean": False, "reason": "", "tracked": ["a.py"], "untracked": ["b.py"]}
    unobservable = {"observable": False, "clean": False, "reason": "no git", "tracked": [], "untracked": []}

    assert check.exact_sha_certification_admission(start_state=clean, end_state=clean)["admitted"] is True
    assert check.exact_sha_certification_admission(start_state=dirty, end_state=clean)["reason"] == "dirty_start_checkout"
    # A tree the run itself dirtied no longer certifies the exact SHA either.
    assert check.exact_sha_certification_admission(start_state=clean, end_state=dirty)["reason"] == "dirty_end_checkout"
    refused = check.exact_sha_certification_admission(start_state=dirty, end_state=clean)
    assert set(refused["detail"]) == {"tracked", "untracked"}, refused
    # Fail closed: an unobservable git state is never a clean one.
    assert check.exact_sha_certification_admission(start_state=unobservable, end_state=clean)["reason"] == "start_state_unobservable"


def test_run_certification_phase_records_the_release_context(monkeypatch, tmp_path):
    """Every certification payload carries SHA, platform, start/end clean state and bundle hashes."""

    check = load_check_module()
    monkeypatch.setattr(check, "retire_owned_processes", lambda **_kwargs: {"retired": False, "survivors": [{"pid": 1, "ppid": 0, "command": "x"}], "container_probe": {}, "docker_unobservable": False})
    payload, _lane = check.run_certification_phase(evidence_dir=tmp_path / "cert")
    release = payload["release"]
    assert set(release) >= {"full_sha", "platform", "generated_bundle_hashes", "start_clean_state", "end_clean_state", "exact_sha_certification"}, release
    assert release["start_clean_state"]["observable"] in (True, False)
    assert "admitted" in release["exact_sha_certification"], release


def test_run_certification_phase_refuses_a_certified_result_when_exact_sha_is_dirty(monkeypatch, tmp_path):
    check = load_check_module()
    clean = {"observable": True, "clean": True, "reason": "", "tracked": [], "untracked": []}
    dirty = {"observable": True, "clean": False, "reason": "", "tracked": ["x.py"], "untracked": []}
    states = iter((dirty, clean))
    monkeypatch.setattr(check, "working_tree_clean_state", lambda: next(states))
    monkeypatch.setattr(check, "retire_owned_processes", lambda **_kwargs: {"retired": True, "survivors": []})
    monkeypatch.setattr(check.latency_calibration, "host_qualification", lambda **_kwargs: {"qualified": True, "reasons": []})
    monkeypatch.setattr(check, "run_lane", lambda _lane, **_kwargs: check.LaneResult("certification", "latency certification", True, 0.0, "", (check.StepResult("s", "c", 0.0, 0),)))
    monkeypatch.setattr(check, "certification_junit_admission", lambda _path: {"admitted": True, "reason": "", "detail": "", "outcomes": {nodeid: {"outcome": "passed", "detail": "", "seconds": 0.1} for nodeid in check.CERTIFICATION_NODE_IDS}})

    payload, _lane = check.run_certification_phase(evidence_dir=tmp_path / "cert")
    assert (payload["result"], payload["reason"]) == ("not-certifiable", "exact_sha_certification_rejected"), payload
    assert payload["evidence"]["reason"] == "dirty_start_checkout"


def test_run_tests_sh_stamps_the_owner_label_only_when_a_run_token_is_present():
    """docker/run-tests.sh must label the container it launches with this run's ownership token."""

    check = load_check_module()
    text = (REPO_ROOT / "docker" / "run-tests.sh").read_text(encoding="utf-8")
    assert "YOLOMUX_CHECK_RUN_TOKEN" in text, text
    assert f'--label "{check.CONTAINER_OWNER_LABEL}=$YOLOMUX_CHECK_RUN_TOKEN"' in text, text
    # The label array is threaded into the real docker run, not built and dropped.
    assert '"${owner_label[@]+"${owner_label[@]}"}"' in text, text


def test_run_tests_sh_uses_root_for_rootless_worktree_mounts():
    """Rootless Docker cannot map the directory-service UID into its subordinate range."""

    text = (REPO_ROOT / "docker" / "run-tests.sh").read_text(encoding="utf-8")
    assert "name=rootless" in text, text
    assert 'run_user=(--user 0)' in text, text
    assert '"${run_user[@]+"${run_user[@]}"}"' in text, text


def test_linux_cpu_budget_is_the_same_number_in_code_help_text_and_docs(monkeypatch, capsys):
    """One worker contract, four surfaces. They previously disagreed on the shared-box default.

    Resolved by same-tree A/B, not by picking a side. The current 100% baseline reached peak load
    76.016 and failed timing-sensitive lanes; 50% reduced peak load to 49.380 while preserving the
    exact canonical phase ownership and leaving explicit overrides available.
    """

    check = load_check_module()
    monkeypatch.delenv("YOLOMUX_PYTEST_WORKERS", raising=False)
    monkeypatch.delenv("YOLOMUX_CHECK_CPU_PERCENT", raising=False)
    monkeypatch.setattr(check.platform, "system", lambda: "Linux")
    monkeypatch.setattr(check.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(check.os, "sched_getaffinity", lambda _pid: set(range(24)))

    percent = check.check_cpu_percent()
    counts = check.pytest_worker_counts()
    assert percent == 50
    assert counts == ("6", "4", "2")
    assert sum(int(count) for count in counts) == 12

    with pytest.raises(SystemExit):
        check.main(["--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert f"default: {percent}" in help_text, help_text

    documentation = " ".join((REPO_ROOT / "docs" / "DEVELOPMENT.md").read_text(encoding="utf-8").split())
    assert f"Linux makes {percent}% of that capacity available to pytest" in documentation
    assert f"32 logical but 24 schedulable Linux CPUs produce {counts[0]}/{counts[1]}/{counts[2]}" in documentation


def _instr_lane(check, probe, extra=()):
    """A pytest lane carrying the duration instrumentation the gate applies.

    `--durations=0` publishes the exact node IDs the report resolves against, so
    a lane without it cannot yield exact identity by design.
    """
    lane = check.Lane("pytest", "pytest probe", (check.Step(
        "pytest", ["python3", "-m", "pytest", str(probe), "-q", "-p", "no:randomly", *extra]),))
    return check.instrument_lane_for_performance(lane)


def _probe(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _run_output(node_ids, failed, reasons=True):
    """One synthetic transcript: duration table (exact IDs) plus summary rows."""
    return ("".join(f"0.01s call {n}\n" for n in node_ids)
            + "=========================== short test summary info ====================\n"
            + "".join(f"FAILED {n}{' - boom' if reasons else ''}\n" for n in failed))


def _tails(node_ids, stem):
    return {n.split(stem + "::")[-1] for n in node_ids}


def _usage():
    return {"user_seconds": 0.0, "system_seconds": 0.0, "max_rss": 0, "max_rss_unit": "KiB"}


def test_failed_pytest_lane_persists_exact_ordered_node_ids_with_parameters(tmp_path):
    """Params survive verbatim, including one containing pytest's own ` - ` separator."""
    check = load_check_module()
    probe = _probe(tmp_path, "test_np.py",
                   "import pytest\n"
                   "@pytest.mark.parametrize('v', [1, 2, 'a-b c', 'has - dash'])\n"
                   "def test_param(v):\n    assert v == 1\n"
                   "def test_plain():\n    assert False\n"
                   "def test_passes():\n    assert True\n")
    result = check.run_lane(_instr_lane(check, probe))
    failed = result.failed_nodes
    assert result.ok is False and failed is not None and failed.unavailable == ""
    # Sorted, so the field is stable under xdist's nondeterministic completion order.
    assert list(failed.node_ids) == sorted(failed.node_ids)
    assert _tails(failed.node_ids, "test_np.py") == {
        "test_param[2]", "test_param[a-b c]", "test_param[has - dash]", "test_plain"}
    assert not any(n.endswith("::test_passes") for n in failed.node_ids)
    assert (failed.total, failed.unresolved_rows) == (4, 0)


def test_failed_node_ids_survive_ansi_colored_lane_output(tmp_path):
    """Escapes wrap the test name INSIDE the node ID, not merely the row prefix."""
    check = load_check_module()
    probe = _probe(tmp_path, "test_ansi.py",
                   "import pytest\n@pytest.mark.parametrize('v', [1, 'has - dash'])\n"
                   "def test_param(v):\n    assert v == 0\n"
                   "def test_plain():\n    assert False\n")
    result = check.run_lane(_instr_lane(check, probe, ("--color=yes",)))
    failed = result.failed_nodes
    # The retained transcript keeps its ANSI on purpose; only the parsers normalize.
    assert "\x1b[" in result.output and result.ok is False
    assert failed is not None and failed.unavailable == ""
    assert _tails(failed.node_ids, "test_ansi.py") == {"test_param[1]", "test_param[has - dash]", "test_plain"}
    # No escape may survive into a node ID, or every later query silently misses.
    assert all("\x1b" not in n for n in failed.node_ids) and failed.unresolved_rows == 0


def test_bracket_characters_in_parameters_survive_byte_for_byte(tmp_path):
    """No text heuristic can split these; identity comes from the duration table."""
    check = load_check_module()
    probe = _probe(tmp_path, "test_br.py",
                   "import pytest\n"
                   "@pytest.mark.parametrize('v', ['a', 'b'], ids=['has ] - dash', 'has [ - dash'])\n"
                   "def test_bracket(v):\n    assert False\n")
    failed = check.run_lane(_instr_lane(check, probe)).failed_nodes
    assert failed is not None and failed.unavailable == ""
    assert _tails(failed.node_ids, "test_br.py") == {"test_bracket[has ] - dash]", "test_bracket[has [ - dash]"}
    assert failed.unresolved_rows == 0


def test_failed_node_ids_resolve_only_against_ids_the_run_published():
    """Dedup across FAILED/ERROR, keep distinct params, never split on a param's ` - `."""
    check = load_check_module()
    ids = ["t.py::test_one", "t.py::test_two[a]", "t.py::test_two[b]", "t.py::test_three[has - dash]", "t.py::test_four"]
    output = _run_output(ids, ids) + "ERROR t.py::test_one - teardown too\n"
    assert check.resolve_failed_node_ids(output) == (tuple(sorted(ids)), 0)


def test_an_unresolvable_summary_row_is_counted_never_guessed():
    """A row naming a node the run never published is counted, not invented."""
    check = load_check_module()
    output = _run_output(["t.py::test_known"], ["t.py::test_known"]) + "FAILED t.py::test_ghost[who - knows] - boom\n"
    assert check.resolve_failed_node_ids(output) == (("t.py::test_known",), 1)


def test_a_published_id_quoted_inside_a_failure_reason_is_not_the_identity():
    """Searching from the right matched the copy in the reason and kept the whole row."""
    check = load_check_module()
    output = ("0.01s call test_probe.py::test_bad\n"
              "FAILED ../../../../tmp/probe/test_probe.py::test_bad"
              " - AssertionError: expected test_probe.py::test_bad\n")
    assert check.resolve_failed_node_ids(output) == (("../../../../tmp/probe/test_probe.py::test_bad",), 0)


def test_relative_path_spelling_difference_between_producers_still_resolves():
    """Durations print the short spelling; the summary prints the rootdir-relative one."""
    check = load_check_module()
    output = "0.01s call test_probe.py::test_bad\nFAILED ../../../../tmp/probe/test_probe.py::test_bad - boom\n"
    assert check.resolve_failed_node_ids(output) == (("../../../../tmp/probe/test_probe.py::test_bad",), 0)


def test_failed_node_extraction_ignores_banner_and_indented_rows():
    """Section banners and indented traceback echoes are not summary rows at all."""
    check = load_check_module()
    output = _run_output(["tests/test_real.py::test_real"], []) + (
        "==================================== ERRORS ==========================\n"
        "______________________ ERROR at setup of test_setup_error ____________\n"
        "    FAILED indented/not/a/row.py::test_indented - boom\n"
        "FAILED tests/test_real.py::test_real - boom\n")
    assert check.resolve_failed_node_ids(output) == (("tests/test_real.py::test_real",), 0)


def test_summary_rows_without_an_exact_producer_report_unavailable():
    """Uninstrumented lane, collection error, or interrupt: say so, never guess."""
    check = load_check_module()
    lane = check.Lane("pytest", "pytest", (check.Step("pytest", ["python3", "-m", "pytest"]),))
    for output in ("FAILED tests/t.py::test_one - boom\n", "ERROR tests/test_broken_import.py\n"):
        failed = check.lane_failed_nodes(lane, False, output)
        assert failed is not None
        assert (failed.unavailable, failed.unresolved_rows, failed.node_ids) == ("no_exact_node_id_source", 1, ())


def test_failed_pytest_lane_without_a_summary_reports_an_explicit_unavailable_reason():
    """Distinct from "rows we refused to guess at": there were no rows at all."""
    check = load_check_module()
    lane = check.Lane("pytest", "pytest probe", (check.Step(
        "pytest", ["python3", "-m", "pytest", "--this-flag-does-not-exist"]),))
    result = check.run_lane(lane)
    assert result.ok is False and result.failed_nodes is not None
    assert result.failed_nodes.unavailable == "no_pytest_summary_in_output"
    assert check.failed_nodes_report_field(result) == {
        "failed_nodes": {"unavailable": "no_pytest_summary_in_output", "unresolved_rows": 0}}


def test_failed_non_pytest_lane_uses_a_distinct_not_applicable_state():
    """"Could never name node IDs" must not read like "should have and did not"."""
    check = load_check_module()
    lane = check.Lane("whitespace", "whitespace", (check.Step("p", [sys.executable, "-c", "raise SystemExit(3)"]),))
    result = check.run_lane(lane)
    assert result.ok is False and result.failed_nodes is not None
    assert result.failed_nodes.unavailable == "lane_is_not_pytest"


def test_passing_lane_carries_no_failed_node_field(tmp_path):
    check = load_check_module()
    probe = _probe(tmp_path, "test_green.py", "def test_ok():\n    assert True\n")
    result = check.run_lane(_instr_lane(check, probe))
    assert result.ok is True and result.failed_nodes is None
    assert check.failed_nodes_report_field(result) == {}
    assert "failed_nodes" not in check.lane_output_report_fields(result)


def test_failed_node_bounds_are_enforced_and_their_counters_are_independent():
    """An over-long ID is dropped and counted, never truncated into a wrong ID."""
    check = load_check_module()
    lane = check.Lane("pytest", "pytest", (check.Step("pytest", ["python3", "-m", "pytest"]),))
    oversize = "tests/t.py::test_big[" + "x" * check.MAX_FAILED_NODE_ID_BYTES + "]"
    many = [f"tests/t.py::test_{i}" for i in range(check.MAX_FAILED_NODE_IDS + 25)] + [oversize]
    over = check.lane_failed_nodes(lane, False, _run_output(many, many))
    assert over is not None and over.unavailable == ""
    assert len(over.node_ids) == check.MAX_FAILED_NODE_IDS and over.truncated is True
    assert over.oversize_dropped == 1 and oversize not in over.node_ids
    assert over.total == check.MAX_FAILED_NODE_IDS + 26
    assert all(len(n.encode("utf-8")) <= check.MAX_FAILED_NODE_ID_BYTES for n in over.node_ids)
    # Under the cardinality cap, dropped is set while truncated stays false.
    few = ["tests/t.py::test_small", oversize]
    under = check.lane_failed_nodes(lane, False, _run_output(few, few))
    assert under is not None and under.node_ids == ("tests/t.py::test_small",)
    assert (under.total, under.oversize_dropped, under.truncated) == (2, 1, False)


def test_failed_nodes_reach_both_final_and_interrupted_report_payloads():
    """One serializer feeds both paths, so neither can silently lose the field."""
    check = load_check_module()
    lane = check.Lane("pytest", "pytest", (check.Step("pytest", ["python3", "-m", "pytest"]),))
    result = check.LaneResult("pytest", "pytest", False, 1.0, "", (), None, None,
                              check.FailedNodes(node_ids=("tests/t.py::test_one",), total=1))
    expected = {"node_ids": ["tests/t.py::test_one"], "total": 1, "truncated": False,
                "oversize_dropped": 0, "unresolved_rows": 0}
    payloads = [check.performance_report_payload(selected=[lane], results=[result], serial=True,
                                                 elapsed=1.0, child_usage=_usage(), interrupted=flag)
                for flag in (False, True)]
    assert [p["lanes"][0]["failed_nodes"] for p in payloads] == [expected, expected]
    assert payloads[1]["interrupted"] is True


def test_failed_nodes_do_not_disturb_retained_artifact_fields_or_verdicts(tmp_path):
    """The pre-existing artifact contract is unchanged; the new field rides alongside."""
    check = load_check_module()
    root = check.lane_output_root(tmp_path / "runtime.json")
    probe = _probe(tmp_path, "test_ret.py", "def test_bad():\n    assert False\n")
    result = check.run_lane(_instr_lane(check, probe), output_root=root)
    fields = check.lane_output_report_fields(result)
    art = result.output_artifact
    assert result.ok is False and art is not None
    assert fields["output_artifact"] == {"path": art.path, "sha256": art.sha256, "bytes": art.bytes}
    assert "output_retention_failure" not in fields
    ids = fields["failed_nodes"]["node_ids"]
    assert len(ids) == 1 and ids[0].endswith("test_ret.py::test_bad")


def test_both_extractors_normalize_through_the_repo_wide_ansi_owner():
    """The gate must not grow a second ANSI stripper beside `strip_ansi_sgr`."""
    check = load_check_module()
    # The bound function IS the repo-wide owner, proven by where it is defined.
    assert check.strip_ansi_sgr.__module__ == "yolomux_lib.tmux.agent_tui"
    assert not hasattr(check, "strip_ansi"), "a second ANSI helper reappeared in the check runner"
    colored = "\x1b[31mFAILED\x1b[0m tests/t.py::\x1b[1mtest_one\x1b[0m - boom\n"
    assert check.strip_ansi_sgr(colored) == "FAILED tests/t.py::test_one - boom\n"
    assert check.pytest_duration_phases("\x1b[32m0.52s\x1b[0m call tests/t.py::test_one\n") == (
        {"seconds": 0.52, "phase": "call", "nodeid": "tests/t.py::test_one"},)
    assert check.pytest_failed_node_ids("\x1b[32m0.01s\x1b[0m call tests/t.py::test_one\n" + colored) == (
        "tests/t.py::test_one",)


def test_pytest_step_predicate_is_one_shared_owner():
    """Instrumentation routes through the predicate rather than re-spelling it."""
    check = load_check_module()
    pytest_step = check.Step("pytest", ["python3", "-m", "pytest", "tests", "-q"])
    other = check.Step("node", ["node", "--check", "static/yolomux.js"])
    assert check.is_pytest_step(pytest_step) is True and check.is_pytest_step(other) is False
    instrumented = check.instrument_lane_for_performance(check.Lane("d", "d", (pytest_step, other)))
    assert instrumented.steps[0].args[-2:] == ["--durations=0", "--durations-min=0"]
    assert instrumented.steps[1] == other


def test_schema_is_five_and_no_retired_owner_was_revived():
    """Owner identity is asserted through the module, never through its source text."""
    check = load_check_module()
    assert check.performance_report_payload(
        selected=[], results=[], serial=True, elapsed=0.0, child_usage=_usage())["schema"] == 5
    # The removed text heuristic and the parallel ANSI helper must stay removed.
    for retired in ("strip_ansi", "_failed_node_id"):
        assert not hasattr(check, retired), f"{retired} was revived in the check runner"
    assert callable(check.is_pytest_step) and callable(check.strip_ansi_sgr)


def test_no_duplicate_module_level_helper_shadows_another():
    """A late duplicate definition silently rebinds the earlier one.

    A second `_junit_document` here made `passed` an XML child and changed three
    certification-admission tests without touching them.
    """
    for path in (Path(__file__), CHECK_PATH):
        names = [n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        assert sorted({n for n in names if names.count(n) > 1}) == [], f"{path.name} rebinds a module-level name"


def test_certification_failed_nodes_come_from_its_junit_not_summary_text(tmp_path):
    """Certification is never instrumented, so JUnit attributes carry its identity."""
    check = load_check_module()
    junit = tmp_path / check.CERTIFICATION_JUNIT_NAME
    junit.write_text(_junit_document([("tests/t.py", "test_slow[has ] - dash]", "failure"),
                                      ("tests/t.py", "test_broken", "error"),
                                      ("tests/t.py", "test_fine", "passed")]), encoding="utf-8")
    failed = check.certification_failed_nodes(junit, lane_ok=False)
    assert failed is not None and failed.unavailable == ""
    assert failed.node_ids == ("tests/t.py::test_broken", "tests/t.py::test_slow[has ] - dash]")
    assert (failed.total, failed.unresolved_rows) == (2, 0)
    assert check.certification_failed_nodes(junit, lane_ok=True) is None


def test_certification_junit_problems_report_typed_reasons(tmp_path):
    """xunit2 drops `file`, which is why the step pins xunit1; refuse, never half-read."""
    check = load_check_module()
    cases = {}
    cases["junit_absent"] = tmp_path / "absent.xml"
    for name, text in (("junit_malformed", "<testsuites><testsuite>"),
                       ("junit_missing_identity",
                        '<testsuites><testsuite><testcase name="t"><failure/></testcase></testsuite></testsuites>'),
                       ("junit_named_no_failure", _junit_document([("tests/t.py", "test_ok", "passed")]))):
        path = tmp_path / f"{name}.xml"
        path.write_text(text, encoding="utf-8")
        cases[name] = path
    for reason, path in cases.items():
        assert check.certification_failed_nodes(path, lane_ok=False).unavailable == reason


def test_run_certification_phase_payload_carries_exact_failed_nodes_from_its_junit(monkeypatch, tmp_path):
    """The real certification path with bounded stubs, not a serializer spread."""
    check = load_check_module()
    evidence = tmp_path / "cert"
    evidence.mkdir(parents=True, exist_ok=True)
    failing = check.CERTIFICATION_NODE_IDS[0]
    rows = [(n.split("::")[0], n.split("::", 1)[1], "failure" if n == failing else "passed")
            for n in dict.fromkeys(check.CERTIFICATION_NODE_IDS)]
    (evidence / check.CERTIFICATION_JUNIT_NAME).write_text(_junit_document(rows), encoding="utf-8")
    clean = {"observable": True, "clean": True, "reason": "", "tracked": [], "untracked": []}
    monkeypatch.setattr(check, "working_tree_clean_state", lambda: clean)
    monkeypatch.setattr(check, "retire_owned_processes", lambda **_k: {"retired": True, "survivors": []})
    monkeypatch.setattr(check.latency_calibration, "certification_host_qualification",
                        lambda **_k: {"qualified": True, "reasons": []})
    monkeypatch.setattr(check, "run_lane", lambda _l, **_k: check.LaneResult(
        "certification", "latency certification", False, 1.0, f"FAILED {failing} - boom\n",
        (check.StepResult("s", "c", 1.0, 1),)))
    payload, lane_result = check.run_certification_phase(evidence_dir=evidence)
    # Identity came from JUnit attributes, never from the lane's summary text.
    assert lane_result is not None and lane_result.failed_nodes is not None
    assert payload["failed_nodes"] == {"node_ids": [failing], "total": 1, "truncated": False,
                                       "oversize_dropped": 0, "unresolved_rows": 0}


def test_main_interrupted_inside_lanes_writes_a_report_with_no_lanes_and_exits_130(monkeypatch, tmp_path, capsys):
    """`main()` binds `results` only when `run_functional_lanes` RETURNS.

    An interrupt inside it leaves the initial empty list, so the written report is
    honest but lane-less: partial lane results are NOT persisted. This regression
    keeps that limitation visible rather than implied.
    """
    check = load_check_module()
    report = tmp_path / "interrupted.json"

    def _interrupt(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(check, "active_yolomux_server_records", lambda: [])
    monkeypatch.setattr(check, "run_functional_lanes", _interrupt)
    exit_code = check.main(["--no-tool-guard", "--lane", "whitespace", "--performance-report", str(report)])
    assert exit_code == 130 and "CHECK INTERRUPTED" in capsys.readouterr().err
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["interrupted"] is True and payload["selected_lanes"] == ["whitespace"]
    # No lane returned, so no failed node IDs can exist for work already done.
    assert payload["lanes"] == []
