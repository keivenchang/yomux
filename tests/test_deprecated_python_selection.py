# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Default backend selection and explicit maintenance admission."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.helpers.fixture_tools import clear_fixture_settings

from tools import check
from tools import test_catalog
from tools.instance_isolation import synthetic_test_environment


pytestmark = pytest.mark.shared_tooling
REPO_ROOT = Path(__file__).resolve().parents[1]


def run_selection_probe(root, *arguments):
    output = root / "collected.json"
    environment = synthetic_test_environment({
        "PATH": os.environ["PATH"], "PYTHONPATH": str(REPO_ROOT),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1",
    })
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tools.pytest_catalog_plugin", "--collect-only", "-q",
         "-o", "cache_dir=" + str(root / "cache"), "--yolomux-catalog-output=" + str(output), *arguments],
        cwd=root, env=environment, text=True, capture_output=True, check=False,
    )
    return result, json.loads(output.read_text()) if output.exists() else []


@pytest.fixture
def selection_probe(tmp_path):
    # A fully deprecated module must never import, even when explicitly named.
    (tmp_path / "test_retired.py").write_text(
        "import pytest\npytestmark = pytest.mark.deprecated_python_server\n"
        "raise RuntimeError('retired module imported')\ndef test_old(): pass\n", encoding="utf-8")
    (tmp_path / "test_retired_reexports.py").write_text(
        "import pytest\npytestmark = pytest.mark.deprecated_python_server\n"
        "raise RuntimeError('retired reexports imported')\n", encoding="utf-8")
    (tmp_path / "test_mixed.py").write_text(
        "import pytest\n"
        "@pytest.fixture\ndef legacy_fixture(): raise RuntimeError('legacy fixture executed')\n"
        "def test_go(): pass\n"
        "@pytest.mark.deprecated_python_server\ndef test_old(legacy_fixture): pass\n"
        "@pytest.mark.deprecated_python_server\nclass TestRetired:\n    def test_inherited(self): pass\n"
        "class TestAssigned:\n    pytestmark = pytest.mark.deprecated_python_server\n    def test_inherited(self): pass\n",
        encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("arguments", [(), ("test_retired.py", "test_mixed.py"), ("test_mixed.py", "-m", "not deprecated_python_server")])
def test_default_selection_never_imports_retired_modules(selection_probe, arguments):
    result, rows = run_selection_probe(selection_probe, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row["nodeid"] for row in rows] == ["test_mixed.py::test_go"]


def test_marker_expression_alone_cannot_enable_deprecated_tests(selection_probe):
    result, rows = run_selection_probe(selection_probe, "-m", "deprecated_python_server")
    assert result.returncode == 5, result.stdout + result.stderr
    assert rows == []


@pytest.mark.parametrize("marker,expected", [(None, 4), ("deprecated_python_server", 3)])
def test_explicit_opt_in_admits_inherited_and_mixed_cases(selection_probe, marker, expected):
    arguments = ["test_mixed.py", "--run-deprecated-python"]
    if marker:
        arguments.extend(["-m", marker])
    result, rows = run_selection_probe(selection_probe, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(rows) == expected
    assert sum(test_catalog.DEPRECATED_PYTHON_MARKER in row["markers"] for row in rows) == 3


def test_static_catalog_filters_inherited_deprecations(selection_probe):
    default = test_catalog.discover_pytest_phase_files(selection_probe, repo_root=selection_probe)
    maintenance = test_catalog.discover_pytest_phase_files(selection_probe, repo_root=selection_probe, run_deprecated_python=True)
    assert default["nonbrowser"] == ("test_mixed.py",)
    assert maintenance["nonbrowser"] == ("test_mixed.py", "test_retired.py")


def test_static_and_runtime_selection_inherit_base_class_markers(selection_probe):
    sample = selection_probe / "test_base.py"
    sample.write_text("import pytest\n@pytest.mark.deprecated_python_server\nclass Base:\n pass\nclass TestChild(Base):\n def test_old(self): pass\n", encoding="utf-8")
    assert test_catalog.wholly_deprecated(sample, repo_root=selection_probe)
    result, rows = run_selection_probe(selection_probe, "test_base.py", "test_mixed.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row["nodeid"] for row in rows] == ["test_mixed.py::test_go"]
    result, rows = run_selection_probe(selection_probe, "test_base.py", "--run-deprecated-python")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row["nodeid"] for row in rows] == ["test_base.py::TestChild::test_old"]


def test_legacy_case_is_deselected_before_fixture_setup(selection_probe):
    # Collection alone cannot establish that the forbidden fixture stays idle.
    environment = synthetic_test_environment({"PATH": os.environ["PATH"], "PYTHONPATH": str(REPO_ROOT), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
    result = subprocess.run([sys.executable, "-m", "pytest", "-p", "tools.pytest_catalog_plugin", "test_mixed.py", "-q"],
                            cwd=selection_probe, env=environment, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 3 deselected" in result.stdout


def test_check_opt_in_preserves_phase_filters_and_docker_arguments():
    default, maintenance = check.step_catalog(), check.step_catalog(run_deprecated_python=True)
    for step_id, phases in check.STEP_PHASES.items():
        if not phases:
            continue
        step = maintenance[step_id]
        assert "--run-deprecated-python" in step.args
        assert "deprecated_python_server" not in step.args[step.args.index("-m") + 1]
        assert ("YOMUX_CHECK_GO_ONLY", "0") in step.env
        assert "tests/test_file_index.py" not in default[step_id].args
    assert "--test_env=YOMUX_RUN_DEPRECATED_PYTHON=1" in maintenance[check.StepId.BAZEL_GO].args


def test_docker_forwards_maintenance_admission(monkeypatch, request):
    clear_fixture_settings(monkeypatch, "BAZEL_EXECUTION", "BAZEL_CONTRACT")
    config_module = request.config.pluginmanager.get_plugin(str(REPO_ROOT / "conftest.py"))
    assert config_module is not None
    captured = []
    class Config:
        class invocation_params:
            args = ("tests/test_file_index.py", "--run-deprecated-python", "-m", "deprecated_python_server")
        class option:
            collectonly = True
    monkeypatch.setattr(config_module.docker_image, "container_available", lambda root: (True, ""))
    monkeypatch.setattr(config_module, "run_reaped_container_command", lambda command, **kwargs: captured.append(command) or 0)
    assert config_module.pytest_cmdline_main(Config()) == 0
    command = captured[0]
    assert command[-len(Config.invocation_params.args)-2:-2] == list(Config.invocation_params.args)


@pytest.mark.parametrize("source,expected", [
    ("import importlib\ndef test_new(): importlib.import_module('yomux_lib.search')\n", ("test_new",)),
    ("def test_new(request): request.getfixturevalue('gate_live_server')\n", ("test_new",)),
    ("import pytest\nclass Base:\n @pytest.fixture(autouse=True)\n def backend(self, gate_live_server): pass\nclass TestChild(Base):\n def test_new(self): pass\n", ("TestChild::test_new",)),
    ("class TestChild:\n def helper(self, gate_live_server): pass\n def test_new(self): self.helper()\n", ("TestChild::test_new",)),
    ("class TestOld:\n def test_same(self, gate_live_server): pass\nclass TestGo:\n def test_same(self): pass\n", ("TestOld::test_same",)),
    ("import pytest\n@pytest.mark.usefixtures('gate_live_server')\nclass TestChild:\n def test_new(self): pass\n", ("TestChild::test_new",)),
    ("from yomux_lib import app\nfactory = app.TmuxWebtermApp\ndef test_new(): factory([])\n", ("test_new",)),
    ("import pytest\npytestmark = pytest.mark.usefixtures('gate_live_server')\ndef test_new(): pass\n", ("test_new",)),
    ("import pytest\nclass TestChild:\n pytestmark = pytest.mark.usefixtures('gate_live_server')\n def test_new(self): pass\n", ("TestChild::test_new",)),
    ("import importlib\nbackend = importlib.import_module('yomux_lib.search')\ndef test_new(): backend.search()\n", ("test_new",)),
])
def test_classifier_follows_backend_dependencies(tmp_path, source, expected):
    sample = tmp_path / "test_dependencies.py"
    sample.write_text(source, encoding="utf-8")
    assert test_catalog.unclassified_legacy_tests(sample, repo_root=tmp_path) == tuple("test_dependencies.py::" + name for name in expected)


def test_new_legacy_backend_tests_require_classification_metadata(tmp_path):
    sample = tmp_path / "test_legacy.py"
    sample.write_text("from yomux_lib import file_index\ndef test_new(): file_index.request_file_index('/fixture')\n", encoding="utf-8")
    assert test_catalog.unclassified_legacy_tests(sample, repo_root=tmp_path) == ("test_legacy.py::test_new",)
    sample.write_text("import pytest\nfrom yomux_lib import file_index\npytestmark = pytest.mark.deprecated_python_server\ndef test_new(): file_index.request_file_index('/fixture')\n", encoding="utf-8")
    assert test_catalog.unclassified_legacy_tests(sample, repo_root=tmp_path) == ()


def test_every_legacy_backend_consumer_has_an_explicit_test_role():
    missing = [nodeid for path in sorted((REPO_ROOT / "tests").rglob("test_*.py"))
               for nodeid in test_catalog.unclassified_legacy_tests(path)]
    assert missing == [], missing


def test_python_server_fixture_refuses_start_without_opt_in(request):
    from_fixture = request.getfixturevalue("deny_legacy_python_server_without_opt_in")
    assert from_fixture is None
    if request.config.getoption("run_deprecated_python"):
        pytest.skip("maintenance invocation intentionally permits the retired server")
    # Import at module scope is required; retrieve the fixture's existing backend class.
    server_class = sys.modules["yomux_lib.server"].TmuxWebtermHTTPServer
    with pytest.raises(RuntimeError, match="without --run-deprecated-python"):
        server_class(("127.0.0.1", 0), object())
