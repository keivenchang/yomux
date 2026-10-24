# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Gate admission, lane reuse, and action boundary regressions."""

import fcntl
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.helpers import fixture_tools
from tools import bazel_execution_contract as contract
from tools import bazel_native
from tools import bazel_native_step
from tools.bazel_native_entrypoint import action_write_policy
from tools.native_process_scope import TEST_OUTPUT_FILE_ENV_KEYS
from tools.tmux_paths import short_temp_directory


@pytest.mark.parametrize("options,expected", [([], 0), (["--test_filter=TestSpecific"], 0),
                                           (["--spawn_strategy=local"], 78), (["--", "--test_filter=TestSpecific"], 78)])
def test_contract_cli_consumes_only_its_argument_separator(monkeypatch, options, expected):
    monkeypatch.setattr(sys, "argv", ["contract", "--validate-options", "--", *options])
    assert contract.main() == expected


@pytest.mark.parametrize("interpreter", ["sh", "bash", "python3"])
def test_fixture_script_pins_interpreter_and_preserves_body(monkeypatch, tmp_path, interpreter):
    executable = tmp_path / interpreter
    executable.write_bytes(b"fixture executable")
    executable.chmod(0o700)
    monkeypatch.setattr(fixture_tools, "_TOOL_PATH", str(tmp_path))
    monkeypatch.setenv("PATH", "/missing-fixture-path")
    source = "#!/usr/bin/env " + interpreter + "\nprintf 'body stays exactly as supplied'\n"
    result = fixture_tools.fixture_script(source)
    expected = sys.executable if interpreter == "python3" else str(executable)
    assert result == "#!" + expected + "\n" + source.partition("\n")[2]
    assert fixture_tools.fixture_script(source.encode()) == result.encode()


def test_missing_fixture_tool_fails_before_creating_script(monkeypatch):
    monkeypatch.setattr(fixture_tools, "_TOOL_PATH", "/missing-fixture-path")
    with pytest.raises(ValueError, match="required fixture executable is missing: bash"):
        fixture_tools.fixture_script("#!/bin/bash\nexit 0\n")


def test_lane_commands_share_configuration_and_dispatch_original_bodies(monkeypatch, tmp_path):
    steps = [{"target": "//:qualified_node_yomux_syntax", "options": ['--test_arg={"args":["node","--check","first.js"],"env":[]}']},
             {"target": "//:qualified_node_wall_syntax", "options": ['--test_arg={"args":["node","--check","second.js"],"env":[]}']}]
    shared = bazel_native.shared_lane_options(steps)
    assert shared[0]["options"] == shared[1]["options"]
    source = tmp_path / "fixture"
    source.mkdir()
    commands = []
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: commands.append(command) or SimpleNamespace(returncode=0))
    monkeypatch.setenv("TEST_SRCDIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for index, step in enumerate(shared):
        monkeypatch.setenv("TEST_TMPDIR", str(tmp_path / str(index)))
        monkeypatch.setattr(sys, "argv", ["step", "fixture", step["target"].removeprefix("//:qualified_").replace("_", "-"), step["options"][0].partition("=")[2]])
        assert bazel_native_step.main() == 0
    assert commands == [["node", "--check", "first.js"], ["node", "--check", "second.js"]]


@pytest.mark.parametrize(("name", "expected"), [
    ("rootfs/opt/yomux-bazel-registry/modules/rules_go/source.json", None),
    ("rootfs/opt/yomux-bazel-repository-cache/contents/hash/file", None),
    ("rootfs/opt/yomux-go-mod-cache/example/module.go", None),
    ("bazel-registry/modules/rules_go/source.json", None),
    ("go-mod-cache/example/module.go", None),
    ("sdk/usr/include/stdio.h", "compiler"),
    ("rootfs/usr/include/stdio.h", "compiler"),
    ("rootfs/usr/lib/gcc/x86_64-linux-gnu/14/cc1", "compiler"),
    ("rootfs/usr/bin/x86_64-linux-gnu-gcc-14", "compiler"),
    ("bin/cc", "compiler"),
    ("rootfs/usr/bin/x86_64-linux-gnu-ar", "compiler"),
    ("rootfs/usr/bin/x86_64-linux-gnu-cpp-14", "compiler"),
    ("rootfs/usr/bin/x86_64-linux-gnu-gcov-dump-14", "compiler"),
    ("rootfs/usr/bin/clang-19", "compiler"),
    ("rootfs/usr/lib/linux/uapi/asm/errno.h", "compiler"),
    ("python/include/python3.12/Python.h", "compiler"),
    ("python/lib/python3.12/site-packages/mypyc/lib-rt/CPy.h", "compiler"),
    ("rootfs/usr/lib/x86_64-linux-gnu/perl/5.40/CORE/perl.h", "compiler"),
    ("python/lib/pkgconfig/python-3.12.pc", "compiler"),
    ("rootfs/usr/lib/x86_64-linux-gnu/libc.so", "compiler"),
    ("rootfs/usr/lib/x86_64-linux-gnu/libc.so.6", "common"),
    ("rootfs/usr/bin/x86_64-linux-gnu-as", "compiler"),
    ("rootfs/usr/bin/x86_64-linux-gnu-ld.bfd", "compiler"),
    ("rootfs/usr/lib/x86_64-linux-gnu/Scrt1.o", "compiler"),
    ("rootfs/usr/lib/x86_64-linux-gnu/libc.a", "compiler"),
    ("rootfs/usr/lib/x86_64-linux-gnu/libgcc_s.so.1", "common"),
    ("chrome/Google Chrome.app/Contents/MacOS/Chrome", "browsers"),
    ("bin/chrome", "browsers"),
    ("rootfs/usr/lib/chromium/chromium", "browsers"),
    ("bin/codex", "clients"),
    ("rootfs/usr/lib/git-core/git", "git_tmux"),
    ("python/lib/python3.12/site.py", "common"),
])
def test_runtime_group_policy_excludes_heavy_and_preparation_inputs(name, expected):
    # The repository rule is Starlark with a Python-compatible pure classifier.
    tree = ast.parse((contract.ROOT / "tools/bazel_runtime_toolchain.bzl").read_text())
    classifier = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_input_group")
    namespace = {}
    exec(compile(ast.Module(body=[classifier], type_ignores=[]), "runtime-group-policy", "exec"), namespace)
    assert namespace["_input_group"](name) == expected


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_cache_identity_partitions_bundles_and_grouping_policy(monkeypatch, system):
    bundle = SimpleNamespace(manifest_digest="first-bundle", system=system, machine="x86_64")
    hashes = {}
    monkeypatch.setattr(contract, "_sha256_file", lambda path: hashes.get(path.name, "locked-source"))
    initial = contract.native_identity(bundle, [])
    bundle.manifest_digest = "second-bundle"
    assert contract.native_identity(bundle, []) != initial
    bundle.manifest_digest = "first-bundle"
    hashes["bazel_runtime_toolchain.bzl"] = "changed-grouping-policy"
    assert contract.native_identity(bundle, []) != initial
    hashes.clear()
    hashes["bazel_native_entrypoint.py"] = "changed-test-policy"
    assert contract.native_identity(bundle, []) != initial
    hashes.clear()
    hashes["fixture_storage.py"] = "changed-fixture-storage-policy"
    assert contract.native_identity(bundle, []) != initial
    hashes.clear()
    scope = contract.native_execution_scope(system)
    monkeypatch.setattr(contract, "native_execution_scope", lambda _system: dict(scope, sandbox_scope="obsolete"))
    assert contract.native_identity(bundle, []) != initial


@pytest.mark.skipif(platform.system() != "Linux", reason="requires kernel child adoption")
def test_worker_reaps_daemon_for_shutdown_without_consuming_direct_status(tmp_path):
    script = """import pathlib,subprocess,sys
from tools.native_process_scope import NativeProcessScope
scope = NativeProcessScope(private_supervisor=True)
try:
    direct = scope.run([sys.executable,'-c','raise SystemExit(7)'], timeout=5)
    assert direct.returncode == 7
    launcher = "import subprocess,sys; child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True);print(child.pid)"
    daemon = scope.run([sys.executable,'-c',launcher],capture_output=True,text=True,timeout=5)
    assert daemon.returncode == 0, daemon.stderr
    pid = int(daemon.stdout)
    shutdown = '''import os,pathlib,signal,sys,threading,time
pid=int(sys.argv[1]); os.kill(pid,signal.SIGTERM)
deadline=time.monotonic()+3
while pathlib.Path('/proc',str(pid)).exists():
    if time.monotonic() >= deadline:
        raise AssertionError('daemon zombie prevented shutdown')
    threading.Event().wait(0.01)
'''
    result = scope.run([sys.executable,'-c',shutdown,str(pid)],timeout=5)
    assert result.returncode == 0
finally:
    assert scope.retire()
"""
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=contract.ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def lane_request(tmp_path):
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / ".bazelversion").write_bytes(b"9.2.0\n")
    cache = tmp_path / "cache"
    cache.mkdir(mode=0o700)
    return {"root": str(tmp_path), "workspace": str(workspace), "cache": str(cache), "flags": [],
            "bazel": "/pinned/bazel", "system": "Linux", "home": str(tmp_path),
            "admitted": False, "admission_only": False, "reuse": True, "cache_on": True,
            "identity": "fixture-policy", "revision": "a" * 40, "source_digest": "fixture-source",
            "host_pid": "12345", "host_port": "1234",
            "steps": [{"target": "//:qualified_node_yomux_syntax", "options": []},
                      {"target": "//:qualified_node_wall_syntax", "options": []}]}


def execute_mock_lane(monkeypatch, request, *, failure=None):
    commands = []
    events = []
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "bazel 9.2.0\n")

    def run(command, **kwargs):
        commands.append(command)
        assert "--batch" not in command
        if any(target in command for target in ("//:native_runtime_admission", "//:native_sandbox_admission")):
            assert "--cache_test_results=no" in command
            assert "--test_env=YOMUX_NATIVE_HOST_PID=12345" in command
            count = 1 if request["admitted"] else 2
            return SimpleNamespace(returncode=int(failure == "probe"),
                stdout="YOMUX_NATIVE_PROBE {}\n" + 'YOMUX_NATIVE_CLEANUP {"retired":true}\n' * count, stderr="")
        if "aquery" in command:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"artifacts": [{"pathFragmentId": "1"}, {"pathFragmentId": "2"}],
                "pathFragments": [{"id": "1", "label": "external/runtime/inputs/common/python"}, {"id": "2", "label": "external/runtime/inputs/node/node"}]}), stderr="")
        return SimpleNamespace(returncode=int(failure == "graph" and "--nobuild" in command))

    monkeypatch.setattr(subprocess, "run", run)
    owner = SimpleNamespace(test_result_cache_mode=contract.test_result_cache_mode, validate_native_probe=lambda *args: None, execution_event=lambda **event: events.append(event))
    return commands, events, lambda: bazel_native.execute_lane(request, {}, owner)


@pytest.mark.parametrize("unwanted", ["external/runtime/inputs/browsers/chrome", "external/runtime/inputs/compiler/sdk", "bazel-out/cmd/yomux-go/yomux-go_/yomux-go"])
def test_lightweight_graph_refuses_heavy_inputs_and_compiled_server(unwanted):
    paths = ["external/runtime/inputs/common/python", "external/runtime/inputs/node/node", unwanted]
    report = {"artifacts": [{"pathFragmentId": str(index)} for index in range(1, 4)],
              "pathFragments": [{"id": str(index), "label": name} for index, name in enumerate(paths, 1)]}
    with pytest.raises(ValueError, match="compiled Go server|unnecessary tool groups"):
        bazel_native.validate_lightweight_graph(report, None)


@pytest.mark.parametrize("admitted", [False, True])
@pytest.mark.parametrize("reuse", [False, True])
def test_lane_reuses_output_and_server_after_fresh_admission(monkeypatch, lane_request, admitted, reuse):
    lane_request["admitted"] = admitted
    lane_request["reuse"] = reuse
    commands, events, execute = execute_mock_lane(monkeypatch, lane_request)
    assert execute() == 0
    assert ("//:go_module_closure_test" in commands[0]) is not admitted
    assert sum("--nobuild" in command for command in commands) == int(not admitted)
    assert len({value for command in commands for value in command if value.startswith("--output_user_root=")}) == 1
    tests = [command for command in commands if any(step["target"] in command for step in lane_request["steps"]) and "test" in command]
    assert len(tests) == 2
    assert all("--cache_test_results=" + ("auto" if reuse else "no") in command and "--test_timeout=1800" in command for command in tests)
    assert [event["coverage_scope"] for event in events if "coverage_scope" in event] == [step["target"] for step in lane_request["steps"]]
    assert commands[-1][-1] == "shutdown"
    with (Path(lane_request["cache"]) / "slot-0.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("failure", ["probe", "graph"])
def test_admission_failure_blocks_result_bearing_tests_and_releases_lease(monkeypatch, lane_request, failure):
    commands, events, execute = execute_mock_lane(monkeypatch, lane_request, failure=failure)
    with pytest.raises(ValueError, match="admission failed|graph could not be admitted"):
        execute()
    assert not events
    assert not any("test" in command and "--cache_test_results=auto" in command for command in commands)
    assert commands[-1][-1] == "shutdown"
    with (Path(lane_request["cache"]) / "slot-0.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("linux", [False, True])
def test_failed_shutdown_retires_descendants_before_releasing_output_lease(monkeypatch, lane_request, linux):
    commands, _events, _execute = execute_mock_lane(monkeypatch, lane_request)
    run = subprocess.run
    retired = []

    def fail_shutdown(command, **kwargs):
        if command[-1] == "shutdown":
            raise subprocess.TimeoutExpired(command, 30)
        return run(command, **kwargs)

    def retire():
        with (Path(lane_request["cache"]) / "slot-0.lock").open("a") as lease:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        retired.append(True)
        return True

    monkeypatch.setattr(subprocess, "run", fail_shutdown)
    owner = SimpleNamespace(test_result_cache_mode=contract.test_result_cache_mode, validate_native_probe=lambda *args: None, execution_event=lambda **event: None)
    scope = SimpleNamespace(run=fail_shutdown, retire=retire) if linux else None
    with pytest.raises(subprocess.TimeoutExpired):
        bazel_native.execute_lane(lane_request, {}, owner, process_scope=scope, retire=None if linux else retire)
    assert retired == [True]
    with (Path(lane_request["cache"]) / "slot-0.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("raises", [False, True])
def test_failed_retirement_quarantines_output_slot(monkeypatch, lane_request, raises):
    _commands, _events, _execute = execute_mock_lane(monkeypatch, lane_request)

    def retire():
        if raises:
            raise ValueError("retirement failed")
        return False

    owner = SimpleNamespace(test_result_cache_mode=contract.test_result_cache_mode, validate_native_probe=lambda *args: None, execution_event=lambda **event: None)
    with pytest.raises(ValueError, match="retirement failed|survived cleanup"):
        bazel_native.execute_lane(lane_request, {}, owner, retire=retire)
    assert (Path(lane_request["cache"]) / "slot-0.quarantine").is_file()
    lane_request["root"] = str(Path(lane_request["root"]) / "next")
    Path(lane_request["root"]).mkdir()
    commands, _events, execute = execute_mock_lane(monkeypatch, lane_request)
    assert execute() == 0
    assert all("--output_user_root=" + str(Path(lane_request["cache"]) / "output-1") in command for command in commands)


def test_git_provenance_fixture_preserves_index_and_frozen_blobs_without_operator_config(tmp_path):
    workspace = tmp_path / "source"
    manifest = workspace / "tests/fixtures/refactor_characterization/v1.json"
    manifest.parent.mkdir(parents=True)
    original = contract.ROOT / manifest.relative_to(workspace)
    manifest.write_bytes(original.read_bytes())
    (workspace / ".gitignore").write_bytes((contract.ROOT / ".gitignore").read_bytes())
    revision = subprocess.check_output(["git", "-C", str(contract.ROOT), "rev-parse", "HEAD"], text=True).strip()
    fixture = bazel_native.capture_git_fixture(contract.ROOT, workspace, revision, contract.repository_git_environment())
    (workspace / ".git").write_bytes(b"gitdir: tools/native_git_fixture\n")
    assert subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip() == revision
    baseline = json.loads(manifest.read_bytes())["source"].removeprefix("git:")
    reference = baseline + ":tools/check.py"
    assert subprocess.check_output(["git", "-C", str(workspace), "show", reference]) == subprocess.check_output(["git", "-C", str(contract.ROOT), "show", reference])
    assert subprocess.run(["git", "-C", str(workspace), "ls-files", "--error-unmatch", "--", "static/yomux.css"], capture_output=True).returncode == 1
    assert subprocess.run(["git", "-C", str(workspace), "check-ignore", "--quiet", "--no-index", "--", "static/yomux.css"]).returncode == 0
    assert subprocess.run(["git", "--git-dir=" + str(fixture), "remote"], capture_output=True, text=True, check=True).stdout == ""
    assert not (fixture / "hooks").exists()
    assert not (fixture / "objects/info/alternates").exists()
    second = tmp_path / "second"
    second_manifest = second / manifest.relative_to(workspace)
    second_manifest.parent.mkdir(parents=True)
    second_manifest.write_bytes(manifest.read_bytes())
    paths = [*fixture.rglob("*"), fixture]
    try:
        for path in paths:
            path.chmod(0o555 if path.is_dir() else 0o444)
        copied = bazel_native.capture_git_fixture(workspace, second, revision, contract.repository_git_environment())

        def packed_object_inventory(repository):
            index = next((repository / "objects/pack").glob("*.idx"))
            output = subprocess.check_output(
                ["git", "--git-dir=" + str(repository), "verify-pack", "-v", str(index)], text=True
            )
            return {
                fields[0]: (fields[1], int(fields[2]))
                for line in output.splitlines()
                if len(fields := line.split()) >= 5 and len(fields[0]) == 40
            }

        # Pack ordering and headers can vary even when every content-addressed object is identical.
        assert packed_object_inventory(copied) == packed_object_inventory(fixture)
    finally:
        for path in paths:
            path.chmod(0o700 if path.is_dir() else 0o600)


@pytest.fixture
def gate_owner(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "input.py").write_bytes(b"original source\n")
    payload = tmp_path / "bundle/payload"
    payload.mkdir(parents=True)
    (payload.parent / "manifest.sha256").write_bytes(b"locked-bundle\n")
    bundle = SimpleNamespace(root=payload, system="Linux")
    verifications = []

    def verify(*args):
        verifications.append(args)
        return bundle

    owner = SimpleNamespace(ROOT=source, validate_options=contract.validate_options, command_arguments=contract.command_arguments,
        verify_native_bundle=verify, repository_head_sha=lambda *args: "a" * 40,
        repository_release_context=lambda *args: {"revision": "a" * 40},
        repository_git_environment=lambda: {}, _sha256_file=contract._sha256_file, hashlib=hashlib)
    monkeypatch.setattr(bazel_native, "native_configuration", lambda *args: ([], [], "fixture-policy"))
    monkeypatch.setattr(bazel_native, "capture_git_fixture", lambda *args: None)
    return source, payload, owner, verifications


def test_gate_captures_one_source_and_requires_admission_before_publication(gate_owner, monkeypatch):
    source, payload, owner, verifications = gate_owner
    calls = []

    def run(arguments, _owner):
        record = json.loads(Path(arguments[arguments.index("--session") + 1]).read_bytes())
        assert record["admitted"] is False
        assert "--admission-only" in arguments
        calls.append(arguments)
        return 0

    monkeypatch.setattr(bazel_native, "run_native", run)
    with bazel_native.NativeGateSession(["--bundle", str(payload)], owner) as gate:
        snapshot = Path(gate.record["workspace"])
        (source / "input.py").write_bytes(b"changed live source\n")
        assert (snapshot / "input.py").read_bytes() == b"original source\n"
        assert not gate.admitted
        gate.admit(["//:go_tests", "//:qualified_node_yomux_syntax"])
        assert gate.admitted
        assert json.loads(gate.path.read_bytes())["admitted"] is True
        assert len(verifications) == 1
    assert len(calls) == 1
    assert len(verifications) == 2
    assert not snapshot.exists()


def test_gate_refuses_external_symlinks(gate_owner):
    source, payload, owner, _verifications = gate_owner
    external = source.parent / "external.py"
    external.write_bytes(b"mutable external source\n")
    (source / "link.py").symlink_to(external)
    with pytest.raises(ValueError, match="symlink escapes checkout"):
        with bazel_native.NativeGateSession(["--bundle", str(payload)], owner):
            pytest.fail("external input was admitted")


def test_bundle_mutation_blocks_acceptance_and_still_retires_snapshot(gate_owner):
    _source, payload, owner, _verifications = gate_owner
    with pytest.raises(ValueError, match="bundle changed"):
        with bazel_native.NativeGateSession(["--bundle", str(payload)], owner) as gate:
            snapshot = Path(gate.record["workspace"])
            def changed(*args):
                raise ValueError("bundle changed")
            owner.verify_native_bundle = changed
    assert not snapshot.exists()


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires the actual Seatbelt policy")
def test_action_policy_allows_owned_writes_and_denies_siblings(tmp_path, request):
    if os.environ.get("YOMUX_BAZEL_CONTRACT"):
        # The qualified action already inherited the sole test-tree policy.
        own = Path(os.environ["YOMUX_NATIVE_SHORT_TMPDIR"])
        sibling = own.parent / ("denied-" + own.name)
        (own / "policy-write").write_bytes(b"owned")
        with pytest.raises(PermissionError):
            sibling.write_bytes(b"sibling")
        return
    tmp_path = Path(short_temp_directory("sp-"))
    request.addfinalizer(lambda: shutil.rmtree(tmp_path))
    own, sibling, temporary, output, home, bundle, cache = [tmp_path / name for name in
        ("own", "sibling", "temporary", "output", "operator-home", "bundle", "cache")]
    for directory in (own, sibling, temporary, output, home, bundle, cache):
        directory.mkdir()
    (home / "credential").write_bytes(b"synthetic credential")
    (own / "alias").symlink_to(sibling, target_is_directory=True)
    (own / "home-alias").symlink_to(home, target_is_directory=True)
    output_files = {key: str(tmp_path / key) for key in TEST_OUTPUT_FILE_ENV_KEYS}
    policy = action_write_policy(own, {"TEST_TMPDIR": str(temporary), "TEST_UNDECLARED_OUTPUTS_DIR": str(output), **output_files},
                                 operator_home=home)
    script = """import errno,json,os,pathlib,socket,sys
paths = json.loads(sys.argv[1])
for path in paths['files'].values():
    pathlib.Path(path).write_bytes(b'declared output')
for name in ('own', 'temporary', 'output'):
    (pathlib.Path(paths[name])/'allowed').write_bytes(b'owned')
for name in ('sibling', 'bundle', 'cache', 'home', 'parent', 'alias', 'home-alias'):
    try:
        (pathlib.Path(paths[name])/'denied').write_bytes(b'forbidden')
    except PermissionError:
        pass
    else:
        raise AssertionError('write allowed: '+name)
for name in ('home', 'home-alias'):
    try:
        (pathlib.Path(paths[name])/'credential').read_bytes()
    except PermissionError:
        pass
    else:
        raise AssertionError('operator home read allowed: '+name)
with socket.socket() as external:
    try:
        external.connect(('1.1.1.1',443))
    except OSError as error:
        assert error.errno in (errno.EPERM,errno.EACCES), error
    else:
        raise AssertionError('external network allowed')
master,slave = os.openpty()
try:
    os.write(master,b'pty\\n')
    assert os.read(slave,64) == b'pty\\n'
finally:
    os.close(master)
    os.close(slave)
for family,address in ((socket.AF_INET,('127.0.0.1',0)),(socket.AF_UNIX,paths['socket'])):
    with socket.socket(family) as server:
        server.bind(address)
        server.listen()
        with socket.socket(family) as client:
            client.connect(server.getsockname())
            connection,_ = server.accept()
            connection.close()
"""
    paths = dict(files=output_files, own=str(own), sibling=str(sibling), temporary=str(temporary), output=str(output), home=str(home),
                 bundle=str(bundle), cache=str(cache), parent=str(tmp_path), alias=str(own / "alias"),
                 **{"home-alias": str(own / "home-alias"), "socket": str(own / "s")})
    result = subprocess.run(["/usr/bin/sandbox-exec", "-p", policy, "/usr/bin/python3", "-I", "-B", "-c", script,
                             json.dumps(paths)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert all((directory / "allowed").read_bytes() == b"owned" for directory in (own, temporary, output))
    assert all(Path(path).read_bytes() == b"declared output" for path in output_files.values())
    assert all(not list(directory.iterdir()) for directory in (sibling, bundle, cache))


def test_policy_has_one_fixture_write_owner_and_required_protections(tmp_path):
    own, sibling, home = (tmp_path / name for name in ("own", "sibling", "operator"))
    policy = action_write_policy(own, {}, operator_home=home)
    assert "(deny job-creation)" in policy
    assert "(deny lsopen)" in policy
    assert "(deny appleevent-send)" in policy
    assert "(deny network*)" in policy
    assert '(remote ip "localhost:*")' in policy
    assert '(remote unix-socket)' in policy
    assert '(deny file-read* (subpath ' + json.dumps(str(home.resolve())) + '))' in policy
    assert '(deny file-write* (subpath ' + json.dumps(str(home.resolve())) + '))' in policy
    assert '(subpath ' + json.dumps(str(own.resolve())) + ')' in policy
    assert json.dumps(str(sibling)) not in policy
    assert '(subpath ' + json.dumps(str(tmp_path)) + ')' not in policy


def test_darwin_actions_use_processwrapper_and_single_entrypoint(monkeypatch, tmp_path):
    payload = tmp_path / "payload"
    payload.mkdir()
    (tmp_path / "manifest.json").write_bytes(b'{"sdk":{"os":"sdk","path":"sdk","version":"sdk"}}')
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "sdk")
    bundle = SimpleNamespace(system="Darwin", root=payload, executables={
        name: payload / "bin" / name for name in ("bash", "chrome", "chromedriver", "cc", "codesign_allocate")})
    flags = contract.native_flags(bundle, [tmp_path])
    assert "--spawn_strategy=processwrapper-sandbox" in flags
    assert "--strategy=TestRunner=processwrapper-sandbox" in flags
    assert flags.count("--run_under=//:native_test_entrypoint") == 1
    assert not any("darwin-sandbox" in flag or flag.startswith("--sandbox_block_path=") for flag in flags)


@pytest.mark.parametrize("reuse", [False, True])
def test_darwin_failed_retirement_preserves_invocation_state(monkeypatch, lane_request, reuse):
    _commands, _events, _execute = execute_mock_lane(monkeypatch, lane_request)
    lane_request.update(system="Darwin", reuse=reuse)
    owner = SimpleNamespace(test_result_cache_mode=contract.test_result_cache_mode, validate_native_probe=lambda *args: None,
                            execution_event=lambda **event: None)
    with bazel_native.darwin_invocation_directory() as temporary:
        lane_request["root"] = temporary
        sentinel = Path(temporary) / "output/state"
        sentinel.parent.mkdir()
        sentinel.write_bytes(b"owned temporary output")
        with pytest.raises(ValueError, match="survived cleanup"):
            bazel_native.execute_lane(lane_request, {}, owner, retire=lambda: False)
    try:
        assert sentinel.read_bytes() == b"owned temporary output"
        assert (Path(temporary) / "process-cleanup.quarantine").is_file()
    finally:
        # No processes were launched by this mocked failure.
        shutil.rmtree(temporary)


def test_darwin_successful_invocation_removes_temporary_state():
    with bazel_native.darwin_invocation_directory() as temporary:
        assert Path(temporary).is_dir()
    assert not Path(temporary).exists()


@pytest.mark.parametrize('reuse, expected', [('yes', 'auto'), ('no', 'no')])
def test_passing_result_cache_policy_is_shared_by_shell_and_native(monkeypatch, capsys, reuse, expected):
    monkeypatch.setattr(sys, 'argv', ['contract', '--test-result-cache', reuse])
    assert contract.main() == 0
    assert capsys.readouterr().out.strip() == expected
    assert contract.test_result_cache_mode(reuse == 'yes') == expected
