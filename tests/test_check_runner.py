# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_PATH = REPO_ROOT / "tools" / "check.py"


def load_check_module():
    spec = importlib.util.spec_from_file_location("yolomux_check", CHECK_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_default_check_lanes_keep_full_pytest_gate():
    check = load_check_module()
    lanes = check.lanes()
    default_names = [lane.name for lane in lanes if lane.default]
    assert default_names == ["py-compile", "static", "node-syntax", "node-layout", "pytest", "whitespace"]
    pytest_lane = next(lane for lane in lanes if lane.name == "pytest")
    # The default pytest lane runs the full suite EXCEPT node_bridge: test_node_suite.py shells out
    # to the same `node tests/layout_url.test.js` the always-on node-layout lane runs, so including it
    # ran that ~20s node suite twice concurrently. The node-layout lane keeps node coverage in the gate.
    assert pytest_lane.steps[0].args == ["python3", "-m", "pytest", "tests", "-n", "auto", "-m", "not node_bridge", "-q"]
    assert "pytest-unit" not in default_names
    assert "pytest-socket" not in default_names
    assert "pytest-browser" not in default_names


def test_focused_pytest_lanes_are_explicit_opt_ins():
    check = load_check_module()
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
        "tests/test_browser_layout.py",
        "-n",
        "auto",
        "-q",
    ]
