import ast
import json
from pathlib import Path
import re
import signal
import subprocess
from types import SimpleNamespace

import pytest

from tools import docker_image
from tools import bazel_execution_contract
from tools.bazel_execution_contract import repository_release_context
from tests.source_inventory import parsed_python_source
from tests.source_inventory import python_source_paths
from yomux_lib import agent_tui
from yomux_lib import app
from yomux_lib import common
from yomux_lib import session_files
from yomux_lib import version
from yomux_lib import web
from yomux_lib.filesystem import git_ops
from yomux_lib.tmux import process_group_ownership
from yomux_lib.yoagent import conversation


def test_package_and_runtime_versions_use_one_source():
    root = Path(common.PROJECT_ROOT)
    version_source = (root / "version.yaml").read_text(encoding="utf-8")
    version_match = common.YOMUX_VERSION_ASSIGNMENT_RE.search(version_source)
    assert version_match is not None
    assert version_match.group(1) == version.YOMUX_VERSION == common.YOMUX_VERSION
    assert 'version = { attr = "yomux_lib.version.YOMUX_VERSION" }' in (root / "pyproject.toml").read_text(encoding="utf-8")
    go_source = (root / "cmd" / "yomux-go" / "main.go").read_text(encoding="utf-8")
    assert not re.search(r'^const version\s*=', go_source, re.MULTILINE)
    go_build = (root / "cmd" / "yomux-go" / "BUILD.bazel").read_text(encoding="utf-8")
    assert 'product_version(\n    name = "generate_product_version",\n)' in go_build
    version_rule = (root / "tools" / "bazel_rules.bzl").read_text(encoding="utf-8")
    assert '"source": attr.label(allow_single_file = True, default = "//:version.yaml")' in version_rule
    assert '"generator": attr.label(executable = True, cfg = "exec", default = "//tools/versiongen:versiongen")' in version_rule
    assert "YOMUX_VERSION = \"" not in (root / "yomux_lib" / "common.py").read_text(encoding="utf-8")
    assert "YOMUX_VERSION = \"" not in (root / "yomux_lib" / "infra" / "common.py").read_text(encoding="utf-8")


def test_go_product_version_generator_reads_the_canonical_source(tmp_path):
    root = Path(common.PROJECT_ROOT)
    output = tmp_path / "product_version_generated.go"
    overlay = tmp_path / "go-overlay.json"
    subprocess.run(
        [
            "python3",
            str(root / "tools" / "generate_go_version.py"),
            "--source",
            str(root / "version.yaml"),
            "--output",
            str(output),
            "--overlay",
            str(overlay),
            "--overlay-target",
            str(root / "cmd" / "yomux-go" / "product_version_generated.go"),
        ],
        cwd=root,
        check=True,
    )
    assert output.read_text(encoding="utf-8") == (
        "// Code generated from version.yaml; DO NOT EDIT.\n\n"
        "package main\n\n"
        f'const version = "{version.YOMUX_VERSION}"\n'
    )
    assert json.loads(overlay.read_text(encoding="utf-8")) == {
        "Replace": {str(root / "cmd" / "yomux-go" / "product_version_generated.go"): str(output)}
    }


def test_go_and_bazel_toolchain_pins_match_their_build_inputs():
    root = Path(common.PROJECT_ROOT)
    bazel_version = (root / ".bazelversion").read_text(encoding="utf-8").strip()
    module = (root / "MODULE.bazel").read_text(encoding="utf-8")
    go_mod = (root / "go.mod").read_text(encoding="utf-8")
    dockerfile = (root / "docker" / "Dockerfile.test").read_text(encoding="utf-8")
    sdk_helper = (root / "tools" / "bazel_test_common.sh").read_text(encoding="utf-8")

    build_args = dict(argument.split("=", 1) for argument in docker_image.toolchain_build_args(root))
    downloads = [node for node in ast.walk(ast.parse(module)) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "download"]
    assert len(downloads) == 1, "all supported hosts must share the default SDK owner"
    pins = {item.arg: ast.literal_eval(item.value) for item in downloads[0].keywords}
    assert build_args["BAZEL_VERSION"] == bazel_version
    assert build_args["GO_VERSION"] == pins["version"]
    assert build_args["GO_SHA256"] == pins["sdks"]["linux_amd64"][1]
    for name in ("BAZEL_VERSION", "GO_VERSION", "GO_SHA256"):
        assert f"ARG {name}\n" in dockerfile
    for architecture in ("linux_amd64", "linux_arm64", "darwin_amd64", "darwin_arm64"):
        archive, checksum = pins["sdks"][architecture]
        assert archive == f"go{pins['version']}.{architecture.replace('_', '-')}.tar.gz"
        assert re.fullmatch(r"[0-9a-f]{64}", checksum)
    go_mod_version = re.search(r"^go (\d+\.\d+(?:\.\d+)?)$", go_mod, re.MULTILINE)
    assert go_mod_version is not None
    assert tuple(map(int, pins["version"].split("."))) >= tuple(map(int, go_mod_version.group(1).split(".")))
    darwin_helper = re.search(r"printf '([0-9.]+)\\n'", sdk_helper)
    assert darwin_helper is not None and darwin_helper.group(1) == pins["version"]


def test_release_commit_and_tag_match_product_version():
    root = Path(common.PROJECT_ROOT)
    context = repository_release_context(root, declared=True)
    head_version_source = context["head_version_source"]
    head_version = common.parse_yomux_version_source(head_version_source)
    if head_version is not None and head_version != version.YOMUX_VERSION:
        return

    release_tags = sorted(context["tags"])
    commit_subject = context["commit_subject"]
    if not release_tags and not commit_subject.startswith("Release YOmux "):
        return

    assert commit_subject == f"Release YOmux {version.YOMUX_VERSION}"
    if not release_tags:
        return

    assert release_tags == [f"v{version.YOMUX_VERSION}"]
    tag_type = context["tags"][release_tags[0]]["type"]
    tag_subject = context["tags"][release_tags[0]]["subject"]
    assert commit_subject == f"Release YOmux {version.YOMUX_VERSION}"
    assert tag_type == "tag"
    assert tag_subject == f"YOmux {version.YOMUX_VERSION}"


@pytest.mark.parametrize("invalid", [None, "type", "subject", "commit_subject", "extra_tag"])
def test_declared_release_check_retains_release_assertions_without_git(monkeypatch, tmp_path, invalid):
    root = tmp_path.resolve()
    release_tag = f"v{version.YOMUX_VERSION}"
    context = {"revision": "a" * 40, "head_version_source": f'version: "{version.YOMUX_VERSION}"\n',
               "commit_subject": f"Release YOmux {version.YOMUX_VERSION}",
               "tags": {release_tag: {"type": "tag", "subject": f"YOmux {version.YOMUX_VERSION}"}}}
    if invalid in {"type", "subject"}:
        context["tags"][release_tag][invalid] = "wrong"
    elif invalid == "commit_subject":
        context["commit_subject"] = "wrong"
    elif invalid == "extra_tag":
        context["tags"]["v999.999.999"] = {"type": "tag", "subject": "wrong"}
    monkeypatch.setattr(bazel_execution_contract, "ROOT", root)
    monkeypatch.setattr(common, "PROJECT_ROOT", str(root))
    monkeypatch.setenv("YOMUX_BAZEL_EXECUTION", "qualified-native")
    monkeypatch.setenv("YOMUX_BAZEL_CONTRACT", "fixture-policy")
    monkeypatch.setenv("YOMUX_TEST_REVISION", "a" * 40)
    monkeypatch.setenv("YOMUX_TEST_RELEASE_CONTEXT", json.dumps(context))

    def undeclared_git(*args, **kwargs):
        raise RuntimeError("release assertion attempted undeclared Git access")

    monkeypatch.setattr(subprocess, "run", undeclared_git)
    if invalid is None:
        test_release_commit_and_tag_match_product_version()
    else:
        with pytest.raises(AssertionError):
            test_release_commit_and_tag_match_product_version()
    assert not (root / ".git").exists()


def test_version_source_parser_reads_manifest():
    assert common.parse_yomux_version_source('version: "0.9.5"\n') == "0.9.5"
    assert common.parse_yomux_version_source('# comment\nversion: "0.9.5"\n') == "0.9.5"
    assert common.parse_yomux_version_source('version: "0.9.5"\nversion: "0.9.6"\n') is None
    assert common.parse_yomux_version_source('version: 0.9.5\n') is None
    assert common.parse_yomux_version_source('version: "00.9.5"\n') is None


def test_git_version_reader_reads_only_canonical_manifest(monkeypatch):
    calls = []

    def fake_git(args, cwd, timeout=3.0):
        calls.append(args)
        if args == ["show", "v0.9.5:version.yaml"]:
            return SimpleNamespace(returncode=0, stdout='version: "0.9.5"\n', stderr="")
        raise AssertionError(f"unexpected git args: {args}")

    monkeypatch.setattr(common, "git", fake_git)
    assert common.git_yomux_version_at_ref("/repo", "v0.9.5") == ("0.9.5", None)
    assert calls == [
        ["show", "v0.9.5:version.yaml"],
    ]


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_record_owned_thread_starts_use_shared_rollback_owner():
    root = Path(common.PROJECT_ROOT)
    owners = {
        "yomux_lib/app.py": {
            "start_client_event_watcher",
            "start_watchd_revision_watcher",
            "start_input_heartbeat_worker",
            "start_tabber_activity_cache_refresh",
            "start_tabber_activity_cache_warmer",
            "indexed_repo_roots_snapshot",
            "warm_metadata_cache_async",
            "start_update_check_thread",
        },
        "yomux_lib/search/file_index.py": {"_start_build"},
        "yomux_lib/yoagent/controller.py": {
            "start_yoagent_action_result_watcher",
            "start_yoagent_backend_prewarm",
        },
    }
    for relative, names in owners.items():
        source, tree = parsed_python_source(root / relative)
        functions = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
            and "start_thread_with_rollback(" in (ast.get_source_segment(source, node) or "")
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


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_main_process_cpu_work_has_named_allowlist():
    root = Path(common.PROJECT_ROOT)
    app_path = root / "yomux_lib" / "app.py"
    source, tree = parsed_python_source(app_path)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    def owner_name(node):
        parent = node
        while parent in parents:
            parent = parents[parent]
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return parent.name
        return "<module>"

    threadpool_owners = {
        owner_name(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "ThreadPoolExecutor"
    }
    assert threadpool_owners == set()

    thread_owners = {
        owner_name(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "threading"
        and node.func.attr in {"Thread", "Timer"}
    }
    assert thread_owners == {
        "chat_yoagent",
        "indexed_repo_roots_snapshot",
        "invalidate_status_topology_async",
        "start_client_event_watcher",
        "start_client_watch_snapshot_publish",
        "start_input_heartbeat_worker",
        "start_watchd_revision_watcher",
        "start_session_files_cache_refresh",
        "start_status_generation_watcher",
        "start_tabber_activity_cache_refresh",
        "start_tabber_activity_cache_warmer",
        "_start_transcripts_payload_refresh",
        "start_update_check_thread",
        "warm_metadata_cache_async",
        "schedule_tmux_roster_rebuild",
    }

    retired_patterns = (
        "AutoApproveWorker(",
        "AutoApproveWorkerRecord",
        "auto_worker_records",
        "record_stats_process_sample",
        "stats_history_encode_records_locked",
        "stats_history_bucket_seconds",
        "merge_shared_stats_history",
        "write_shared_stats_history",
    )
    for pattern in retired_patterns:
        assert pattern not in source

    batchd_source = (root / "yomux_lib" / "infra" / "batchd.py").read_text(encoding="utf-8")
    assert '"transcript_view": _transcript_view' in batchd_source
    routes_source = (root / "yomux_lib" / "http_routes.py").read_text(encoding="utf-8")
    assert routes_source.count("write_json_bytes(result.body, status=result.status)") == 2


def test_positive_finite_number_normalizes_counter_inputs():
    assert common.positive_finite_number("3.5") == 3.5
    assert common.positive_finite_number(0) == 0.0
    assert common.positive_finite_number(-1) == 0.0
    assert common.positive_finite_number(float("inf")) == 0.0
    assert common.positive_finite_number(float("nan")) == 0.0
    assert common.positive_finite_number("invalid") == 0.0


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_backend_primitive_consumers_share_the_canonical_owners():
    assert app.normalized_prompt_state is agent_tui.normalized_prompt_state
    assert app.cached_agent_auth_status_snapshot is web.bootstrap_agent_auth_status
    assert app.sanitized_yoagent_stream_items is conversation.sanitized_stream_items
    assert session_files.normal_ref is git_ops.normal_ref
    assert session_files.diff_refs is git_ops.diff_refs
    assert session_files.refs_requested is git_ops.refs_requested

    calls = []

    class SignatureOwner:
        def stable_client_event_signature_payload(self, payload):
            calls.append(payload)
            return {"owned": payload}

    payload = {"value": 1}
    assert app.TmuxWebtermApp.tmux_signal_signature_payload(SignatureOwner(), payload) == {"owned": payload}
    assert calls == [payload]


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
def test_backend_primitive_implementation_bodies_have_one_owner():
    root = Path(common.PROJECT_ROOT) / "yomux_lib"
    functions = []
    for path in python_source_paths(str(root)):
        _source, tree = parsed_python_source(path)
        functions.extend((path, node) for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))

    owner_names = {
        "normalized_prompt_state",
        "sanitized_stream_items",
        "positive_finite_number",
        "normal_ref",
        "diff_refs",
        "refs_requested",
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

    process = FakeProcess()
    monkeypatch.setattr(process_group_ownership.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(process_group_ownership.os, "killpg", lambda pid, sig: calls.append(("killpg", pid, sig)))
    monkeypatch.setattr(process_group_ownership, "process_start_identity", lambda pid: "fixture-start")
    process_group_ownership.record_owned_process_group(process)

    common.terminate_process_group(process)

    assert calls == [
        ("killpg", 12345, signal.SIGTERM),
        ("wait", 2.0),
        ("killpg", 12345, signal.SIGKILL),
        ("wait", 2.0),
    ]
