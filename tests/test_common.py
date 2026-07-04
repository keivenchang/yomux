import ast
from pathlib import Path
import signal
import subprocess

import pytest

from yolomux_lib import agent_tui
from yolomux_lib import app
from yolomux_lib import common
from yolomux_lib import session_files
from yolomux_lib import web
from yolomux_lib.filesystem import git_ops
from yolomux_lib.yoagent import conversation


def test_record_owned_thread_starts_use_shared_rollback_owner():
    root = Path(common.PROJECT_ROOT)
    owners = {
        "yolomux_lib/app.py": {
            "start_stats_history_sampler",
            "start_client_event_watcher",
            "start_client_directory_poll",
            "request_session_files_disk_cache_prune",
            "start_input_heartbeat_worker",
            "start_tabber_activity_cache_refresh",
            "start_tabber_activity_cache_warmer",
            "warm_metadata_cache_async",
        },
        "yolomux_lib/file_index.py": {"_start_build"},
        "yolomux_lib/yoagent/controller.py": {
            "start_yoagent_action_result_watcher",
            "start_yoagent_backend_prewarm",
        },
    }
    for relative, names in owners.items():
        source = (root / relative).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
        functions = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
        }
        assert set(functions) == names
        for name, function_source in functions.items():
            assert "start_thread_with_rollback(" in function_source, f"{relative}:{name}"
            assert ".start()" not in function_source, f"{relative}:{name}"

    rolled_back = []

    class FailedWorker:
        def start(self):
            raise RuntimeError("cannot start")

    with pytest.raises(RuntimeError, match="cannot start"):
        common.start_thread_with_rollback(FailedWorker(), lambda: rolled_back.append(True))
    assert rolled_back == [True]


def test_project_git_normalizes_missing_and_malformed_git_payloads():
    git = {"root": "/repo", "branch": "main"}

    assert common.project_git({"git": git}) is git
    assert common.project_git({"git": None}) == {}
    assert common.project_git({"git": "not-a-dict"}) == {}
    assert common.project_git({}) == {}
    assert common.project_git(None) == {}


def test_project_git_helper_owns_project_git_extraction_sites():
    root = Path(common.PROJECT_ROOT)
    offenders = []
    for relative in ("yolomux_lib/activity_summary.py", "yolomux_lib/app.py"):
        source = (root / relative).read_text(encoding="utf-8")
        if 'project.get("git")' in source or "project.get('git')" in source:
            offenders.append(relative)

    assert offenders == []


def test_positive_finite_number_normalizes_counter_inputs():
    assert common.positive_finite_number("3.5") == 3.5
    assert common.positive_finite_number(0) == 0.0
    assert common.positive_finite_number(-1) == 0.0
    assert common.positive_finite_number(float("inf")) == 0.0
    assert common.positive_finite_number(float("nan")) == 0.0
    assert common.positive_finite_number("invalid") == 0.0


def test_backend_primitive_consumers_share_the_canonical_owners():
    assert app.normalized_prompt_state is agent_tui.normalized_prompt_state
    assert app.cached_agent_auth_status_snapshot is web.bootstrap_agent_auth_status
    assert app.sanitized_yoagent_stream_items is conversation.sanitized_stream_items
    assert session_files.normal_ref is git_ops.normal_ref
    assert session_files.diff_refs is git_ops.diff_refs
    assert session_files.refs_requested is git_ops.refs_requested
    assert session_files.git_ref_exists is git_ops.git_ref_exists

    calls = []

    class SignatureOwner:
        def stable_client_event_signature_payload(self, payload):
            calls.append(payload)
            return {"owned": payload}

    payload = {"value": 1}
    assert app.TmuxWebtermApp.tmux_signal_signature_payload(SignatureOwner(), payload) == {"owned": payload}
    assert calls == [payload]


def test_backend_primitive_implementation_bodies_have_one_owner():
    root = Path(common.PROJECT_ROOT) / "yolomux_lib"
    functions = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions.extend((path, node) for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))

    owner_names = {
        "normalized_prompt_state",
        "sanitized_stream_items",
        "positive_finite_number",
        "normal_ref",
        "diff_refs",
        "refs_requested",
        "git_ref_exists",
        "bootstrap_agent_auth_status",
        "healed_runtime_path",
    }
    owners = {node.name: (path, node) for path, node in functions if node.name in owner_names}
    assert set(owners) == owner_names

    for owner_name, (owner_path, owner) in owners.items():
        fingerprint = ast.dump(ast.Module(body=owner.body, type_ignores=[]), include_attributes=False)
        matches = [
            f"{path.relative_to(root)}:{node.name}"
            for path, node in functions
            if ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False) == fingerprint
        ]
        assert matches == [f"{owner_path.relative_to(root)}:{owner_name}"]


def test_terminate_process_group_waits_after_sigkill(monkeypatch):
    calls = []

    class FakeProcess:
        pid = 12345
        waits = 0

        def poll(self):
            return None

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("cmd", timeout)
            return 0

    monkeypatch.setattr(common.os, "killpg", lambda pid, sig: calls.append(("killpg", pid, sig)))

    common.terminate_process_group(FakeProcess())

    assert calls == [
        ("killpg", 12345, signal.SIGTERM),
        ("wait", 2.0),
        ("killpg", 12345, signal.SIGKILL),
        ("wait", 2.0),
    ]
