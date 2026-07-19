# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import ast
from contextlib import contextmanager
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.source_inventory import parsed_python_source
from tests.source_inventory import python_source_paths
from yolomux_lib.background_owner import pid_is_alive as background_owner_pid_is_alive


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
        REPO_ROOT / "tmux_wall.py",
        REPO_ROOT / "auto_approve_tmux.py",
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


def test_check_lock_is_one_per_user_across_worktrees_and_tmpdirs():
    check = load_check_module()
    assert check.DEFAULT_TOOL_LOCK_PATH == Path.home() / ".cache" / "yolomux" / "expensive-tools.lock"


def test_default_check_lanes_keep_full_pytest_gate():
    check = load_check_module()
    nonbrowser_workers, browser_workers, e2e_workers = check.pytest_worker_counts()
    lanes = check.lanes()
    default_names = [lane.name for lane in lanes if lane.default]
    assert default_names == ["py-compile", "static", "node-syntax", "node-layout", "pytest", "pytest-browser", "pytest-e2e", "whitespace"]
    pytest_lane = next(lane for lane in lanes if lane.name == "pytest")
    # The default pytest lane runs the full suite EXCEPT node_bridge and e2e: test_node_suite.py shells
    # out to the same `node tests/layout_url.test.js` the always-on node-layout lane runs (so including
    # it ran that ~20s node suite twice concurrently), e2e tests launch real tmux + mock agents, and
    # browser tests need Selenium/Chrome. Each slow class has its own default lane so failures name the
    # failing subsystem instead of hiding under "pytest full".
    assert pytest_lane.steps[0].args == ["python3", "-m", "pytest", *check.pytest_files("nonbrowser"), "-n", nonbrowser_workers, "-m", "not node_bridge and not e2e and not browser", "-q"]
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
    assert "pytest-unit" not in default_names
    assert "pytest-socket" not in default_names


def test_focused_pytest_lanes_keep_expected_filters():
    check = load_check_module()
    _nonbrowser_workers, browser_workers, _e2e_workers = check.pytest_worker_counts()
    lanes = {lane.name: lane for lane in check.lanes()}
    assert lanes["pytest-unit"].steps[0].args == [
        "python3",
        "-m",
        "pytest",
        "tests",
        "--ignore=tests/test_browser_layout.py",
        "-m",
        "not socket and not browser and not node_bridge",
        "-q",
    ]
    assert lanes["pytest-socket"].steps[0].args == [
        "python3",
        "-m",
        "pytest",
        "tests",
        "--ignore=tests/test_browser_layout.py",
        "-m",
        "socket and not browser",
        "-q",
    ]
    assert lanes["pytest-browser"].steps[0].args == [
        "python3",
        "-m",
        "pytest",
        *check.pytest_files("boot"),
        "-m",
        "boot",
        "-q",
    ]
    assert lanes["pytest-browser"].steps[1].args == [
        "python3",
        "-m",
        "pytest",
        *check.pytest_files("browser"),
        "-n",
        browser_workers,
        "--dist",
        "worksteal",
        "-m",
        "browser and not e2e and not boot and not visual_golden",
        "-q",
    ]
    assert lanes["pytest-browser"].steps[2].args == [
        "python3",
        "-m",
        "pytest",
        *check.pytest_files("golden"),
        "-m",
        "visual_golden",
        "-q",
    ]
    assert lanes["pytest-browser-behavior"].default is False
    assert lanes["pytest-browser-behavior"].steps == (lanes["pytest-browser"].steps[1],)
    assert lanes["pytest-boot"].steps[0].args == [
        "python3",
        "-m",
        "pytest",
        *check.pytest_files("boot"),
        "-m",
        "boot",
        "-q",
    ]


def test_check_runner_scales_one_concurrent_pytest_budget_from_host_cores(monkeypatch):
    check = load_check_module()
    monkeypatch.delenv("YOLOMUX_PYTEST_WORKERS", raising=False)
    monkeypatch.setattr(check.platform, "system", lambda: "Linux")

    expected = {
        4: ("1", "1", "1"),
        10: ("3", "2", "2"),
        14: ("5", "3", "2"),
        32: ("12", "8", "4"),
    }
    for cores, counts in expected.items():
        monkeypatch.setattr(check.os, "cpu_count", lambda cores=cores: cores)
        assert check.pytest_worker_counts() == counts

    monkeypatch.setenv("YOLOMUX_PYTEST_WORKERS", "5,3,1")
    assert check.pytest_worker_counts() == ("5", "3", "1")

    monkeypatch.delenv("YOLOMUX_PYTEST_WORKERS", raising=False)
    monkeypatch.setattr(check.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(check.os, "cpu_count", lambda: 32)
    assert check.pytest_worker_counts() == ("2", "1", "1")


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
        check=True,
    )
    collection_rows = json.loads(output_path.read_text(encoding="utf-8"))
    collected = {row["nodeid"] for row in collection_rows}
    collected_base = {nodeid.split("[", 1)[0] for nodeid in collected}
    missing = [nodeid for nodeid in test_config.SLOWEST_FIRST_TESTS if nodeid not in collected_base]
    assert missing == []
    check = load_check_module()
    expected_phase_by_marker = (
        ("node_bridge", "node_bridge"),
        ("e2e", "e2e"),
        ("visual_golden", "golden"),
        ("boot", "boot"),
        ("browser", "browser"),
    )
    catalog_files = {phase: set(paths) for phase, paths in check.PYTEST_PHASE_FILES.items()}
    phase_rows = {phase: [] for phase in catalog_files}
    for row in collection_rows:
        phase = next((candidate for marker, candidate in expected_phase_by_marker if marker in row["markers"]), "nonbrowser")
        phase_rows[phase].append(row["nodeid"])
    ownership_errors = []
    for phase, nodeids in phase_rows.items():
        for nodeid in nodeids:
            path = nodeid.split("::", 1)[0]
            if path not in catalog_files[phase]:
                ownership_errors.append(f"{nodeid} belongs to {phase}, but {path} is absent from its catalog")
    assert ownership_errors == []
    assert set().union(*map(set, phase_rows.values())) == collected


def test_non_drag_browser_actions_use_the_shared_fast_pointer_helper():
    paths = [
        REPO_ROOT / "tests" / "test_browser_layout.py",
        REPO_ROOT / "tests" / "test_browser_dockview.py",
        REPO_ROOT / "tests" / "test_browser_share.py",
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

    def fake_run_parallel(selected):
        events.append(("run", [lane.name for lane in selected]))
        return [check.LaneResult(lane.name, lane.label, True, 0.0, "") for lane in selected]

    monkeypatch.setattr(check, "expensive_tool_lock", fake_expensive_tool_lock)
    monkeypatch.setattr(check, "active_yolomux_server_records", lambda: [{"port": 7772}, {"port": 7770}])
    monkeypatch.setattr(check, "lower_current_process_priority", lambda records: events.append(("nice", records)) or True)
    monkeypatch.setattr(check, "run_parallel", fake_run_parallel)

    assert check.main([]) == 0

    assert events[0] == ("lock", True, check.DEFAULT_TOOL_LOCK_PATH)
    assert events[1] == ("nice", [{"port": 7772}, {"port": 7770}])
    assert events[2][0] == "run"
    assert events[2][1] == ["py-compile", "static", "node-syntax", "node-layout", "pytest", "pytest-browser", "pytest-e2e", "whitespace"]
    output = capsys.readouterr().out
    assert "Acquiring YOLOmux expensive-tool lock" in output
    assert "lowered check priority by nice +5" in output


def test_focused_cheap_lane_skips_live_server_priority_work(monkeypatch):
    check = load_check_module()
    events = []

    @contextmanager
    def fake_expensive_tool_lock(enabled=True, lock_path=check.DEFAULT_TOOL_LOCK_PATH):
        events.append(("lock", enabled))
        yield enabled

    def fail_active_records():
        raise AssertionError("cheap focused lanes should not probe live YOLOmux server state")

    monkeypatch.setattr(check, "expensive_tool_lock", fake_expensive_tool_lock)
    monkeypatch.setattr(check, "active_yolomux_server_records", fail_active_records)
    monkeypatch.setattr(check, "run_parallel", lambda selected: [check.LaneResult(selected[0].name, selected[0].label, True, 0.0, "")])

    assert check.main(["--lane", "whitespace"]) == 0

    assert events == [("lock", False)]


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
    )
    path = tmp_path / "report.json"
    check.write_performance_report(path, payload)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "child_usage": {"max_rss": 1024, "max_rss_unit": "KiB", "system_seconds": 0.25, "user_seconds": 0.5},
        "interrupted": False,
        "lanes": [{"label": "demo lane", "name": "demo", "ok": True, "steps": [{"command": "python3 -m demo", "label": "demo step", "returncode": 0, "slow_tests": [], "wall_seconds": 0.75}], "wall_seconds": 1.25}],
        "mode": "parallel",
        "pytest_workers": {"browser": check.pytest_worker_counts()[1], "e2e": check.pytest_worker_counts()[2], "nonbrowser": check.pytest_worker_counts()[0]},
        "schema": 1,
        "selected_lanes": ["demo"],
        "wall_seconds": 1.5,
    }


def test_performance_report_path_is_tmp_only(monkeypatch):
    check = load_check_module()
    monkeypatch.setattr(check.os, "getpid", lambda: 4321)

    assert check.performance_report_path("") == Path("/tmp/yolomux-check-4321.json")
    assert check.performance_report_path("/tmp/yolomux-report.json") == Path("/tmp/yolomux-report.json")
    with pytest.raises(ValueError, match="under /tmp"):
        check.performance_report_path("report.json")


def test_performance_instrumentation_adds_bounded_pytest_durations_and_parses_them():
    check = load_check_module()
    lane = check.Lane("demo", "demo", (check.Step("pytest", ["python3", "-m", "pytest", "tests", "-q"]), check.Step("node", ["node", "--check", "static/yolomux.js"])))

    instrumented = check.instrument_lane_for_performance(lane)

    assert instrumented.steps[0].args[-1] == "--durations=10"
    assert instrumented.steps[1] == lane.steps[1]
    assert check.pytest_slowest_calls("0.52s call tests/test_demo.py::test_fast\n0.11s setup tests/test_demo.py::test_fast\n") == (
        {"seconds": 0.52, "nodeid": "tests/test_demo.py::test_fast"},
        {"seconds": 0.11, "nodeid": "tests/test_demo.py::test_fast"},
    )
