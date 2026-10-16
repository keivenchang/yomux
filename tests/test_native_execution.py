# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Exercise preparation and process ownership with real detached processes."""

from tests.helpers.fixture_tools import fixture_script, fixture_executable
from tests import latency_calibration
import ast
import os
from functools import wraps
import json
from pathlib import Path
import platform
import plistlib
import select
import shlex
import signal
import shutil
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

from tools import native_bundle_acquire
from tools import native_bundle_prepare
from tools.bazel_execution_contract import native_flags, validate_native_probe
from tools.instance_isolation import synthetic_test_environment
from tools.native_process_scope import NativeProcessScope, SCOPE_ENV
from tools.native_process_scope import DarwinResourceCoalitionScope, resource_coalition, resource_coalition_task_count
from tools import native_darwin_launcher
from tools import bazel_native
from tools import bazel_native_step
from tools import bazel_native_probe
from tools import bazel_native_entrypoint
from tools import bazel_go_module_closure
from tools import check as check_runner
from tools import migrate_yomux_data
from tools import system_status_latency_probe
from tools import yostats_active_browser_window
from yomux_lib.local_services import registry as local_services_registry
from tools.bazel_generate import copy_runtime_tree
from tests.browser_helpers.webdriver_lease import DriverGeneration, process_start_key
from tools import native_process_scope
from tools.product_environment import product_env
from tools import tmux_paths
from tools.instance_isolation import rooted_socket_candidates
from tools.tmux_paths import SHORT_TMPDIR_ENV, short_temp_base, create_native_fixture_root, cleanup_native_fixture_roots


def test_diagnostic_module_closure_prepares_private_locked_seed(monkeypatch, tmp_path):
    monkeypatch.setenv("YOMUX_BAZEL_EXECUTION", "native")
    monkeypatch.setenv("GOMODCACHE", "")
    monkeypatch.setattr(bazel_go_module_closure.shutil, "which", lambda name: "/declared/go")
    calls = []
    monkeypatch.setattr(bazel_go_module_closure, "_download_go_modules", lambda *args: calls.append(args))
    source = tmp_path / "source"
    modules = tmp_path / "modules"
    bazel_go_module_closure.prepare_module_cache(source, modules)
    assert modules.is_dir()
    assert calls == [(Path("/declared/go"), source, modules)]


def test_qualified_module_closure_uses_seed_without_network(monkeypatch, tmp_path):
    monkeypatch.setenv("YOMUX_BAZEL_EXECUTION", "qualified-native")
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "locked.zip").write_bytes(b"locked module")
    monkeypatch.setenv("GOMODCACHE", str(seed))
    monkeypatch.setattr(bazel_go_module_closure, "_download_go_modules", lambda *args: pytest.fail("qualified closure downloaded modules"))
    modules = tmp_path / "modules"
    bazel_go_module_closure.prepare_module_cache(tmp_path / "source", modules)
    assert (modules / "locked.zip").read_bytes() == b"locked module"
    monkeypatch.setenv("GOMODCACHE", "")
    with pytest.raises(ValueError, match="absolute Go module cache seed"):
        bazel_go_module_closure.prepare_module_cache(tmp_path / "source", tmp_path / "invalid")


@pytest.mark.parametrize(("versions", "accepted"), [("0.58.3", True), ("0.58.3\n0.58.3", False), ("0.58.2", False), ("", False)])
def test_bazel_source_check_requires_one_matching_rules_go_version(tmp_path, versions, accepted):
    module = tmp_path / "MODULE.bazel"
    module.write_bytes(b'bazel_dep(name = "rules_go", version = "0.58.3")\n')
    go_mod = tmp_path / "go.mod"
    go_mod.write_bytes("".join(f"github.com/bazelbuild/rules_go v{version}\n" for version in versions.splitlines()).encode())
    checker = Path(__file__).resolve().parents[1] / "tools/bazel_source_check.sh"
    result = subprocess.run([fixture_executable("bash"), str(checker), fixture_executable("true"), str(module), str(go_mod)], capture_output=True, text=True)
    assert (result.returncode == 0) is accepted, result.stderr


def assert_launchd_job_creation_denied(tmp_path, envelope=()):
    label = "org.yomux.denied." + uuid.uuid4().hex
    service = "gui/" + str(os.getuid()) + "/" + label
    job = tmp_path / "owned.plist"
    job.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": ["/usr/bin/true"], "RunAtLoad": True}))
    result = subprocess.run([*envelope, "/bin/launchctl", "bootstrap", "gui/" + str(os.getuid()), str(job)],
        capture_output=True, text=True)
    try:
        assert result.returncode != 0, "test policy allowed a launchd job outside the native invocation coalition"
        status = subprocess.run(["/bin/launchctl", "print", service], capture_output=True, text=True)
        assert status.returncode != 0
    finally:
        if result.returncode == 0:
            subprocess.run(["/bin/launchctl", "bootout", service], capture_output=True, text=True, check=True)


def launchd_boundary_contract(test):
    @wraps(test)
    def execute(*args, **kwargs):
        if (platform.system() == "Darwin" and product_env("BAZEL_EXECUTION") == "qualified-native"
                and product_env("BAZEL_CONTRACT")):
            # The controller and uncached detached-env admission exercise kernel
            # coalition retirement before the test sandbox. Actions must prove
            # they cannot create a launchd job that escapes that same coalition.
            assert_launchd_job_creation_denied(kwargs["tmp_path"])
            return
        return test(*args, **kwargs)
    return execute


def inherited_test_tree_contract(test):
    @wraps(test)
    def execute(*args, **kwargs):
        if platform.system() == "Darwin" and product_env("BAZEL_EXECUTION") == "qualified-native" and product_env("BAZEL_CONTRACT"):
            # Launch/cancellation integration runs on the unsandboxed controller.
            # Qualified payloads exercise the inherited policy without nesting it.
            bazel_native_probe.probe(admission=False, tools=False)
            return
        return test(*args, **kwargs)
    return execute


def test_source_copy_preserves_executable_and_precreated_document_parents(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    script = source / "script"
    script.write_bytes(fixture_script(b"#!/bin/sh\nexit 0\n"))
    script.chmod(0o755)
    destination = tmp_path / "destination"
    destination.mkdir()
    native_bundle_acquire.copy_action(source, destination)
    assert os.access(destination / "script", os.X_OK)
    assert subprocess.run([str(destination / "script")]).returncode == 0


def test_layout_copy_accepts_precreated_parent_and_rebases_virtual_symlink(tmp_path):
    source = tmp_path / "source"
    (source / "rootfs/usr").mkdir(parents=True)
    (source / "rootfs/bin").symlink_to("/usr")
    destination = tmp_path / "destination"
    destination.mkdir()
    native_bundle_prepare.copy_layout(source, destination)
    assert (destination / "rootfs/bin").resolve() == destination / "rootfs/usr"


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_guarantee_inventory_requires_all_promised_results(system):
    record = dict.fromkeys(("external_network_blocked", "operator_home_blocked", "operator_home_writes_blocked", "tool_runtime_readonly", "credential_environment_scrubbed", "fixture_state_private", "fixture_writes_isolated",
        "pty_usable", "undeclared_host_reads_blocked", "host_localhost_isolated", "host_processes_isolated", "fixture_storage_tmpfs"), True)
    validate_native_probe(record, system)
    record["external_network_blocked"] = False
    with pytest.raises(ValueError, match="external_network_blocked"):
        validate_native_probe(record, system)
    record["external_network_blocked"] = True
    record["host_processes_isolated"] = False
    if system == "Linux":
        with pytest.raises(ValueError, match="host_processes_isolated"):
            validate_native_probe(record, system)
    else:
        validate_native_probe(record, system)
    record["host_processes_isolated"] = True
    record["fixture_storage_tmpfs"] = False
    if system == "Linux":
        with pytest.raises(ValueError, match="fixture_storage_tmpfs"):
            validate_native_probe(record, system)
    else:
        validate_native_probe(record, system)


@pytest.mark.parametrize("disk_index", [0, 1, 2])
def test_probe_refuses_disk_backed_fixture_before_tool_admission(monkeypatch, tmp_path, disk_index):
    for key in bazel_native_probe.INHERITED_CREDENTIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setenv("TEST_TMPDIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    directories = ["/tmp", str(tmp_path), str(tmp_path / "home")]
    requested = []

    def filesystem(command, **kwargs):
        assert command[:4] == ["stat", "--file-system", "--format=%T", "--"]
        requested.append(command[-1])
        return "ext4\n" if len(requested) - 1 == disk_index else "tmpfs\n"

    monkeypatch.setattr(subprocess, "check_output", filesystem)
    with pytest.raises(ValueError, match="fixture storage is not action-private tmpfs"):
        bazel_native_probe.probe()
    assert requested == directories[:disk_index + 1]


@pytest.mark.parametrize("missing", ["identity", "hardware", None])
def test_darwin_admission_requires_platform_identity_and_hardware_tools(monkeypatch, tmp_path, missing):
    monkeypatch.setenv("CHROME_BIN", "/verified/browser")
    monkeypatch.setenv("CHROMEDRIVER_BIN", "/verified/driver")
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setenv("SHELL", "/verified/bash")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if "HostIdentity.from_system(environ={})" in command[-1] and missing == "identity":
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, "", "")

    def output(command, **kwargs):
        commands.append(command)
        if command == ["system_profiler", "-listDataTypes"]:
            return "" if missing == "hardware" else "SPHardwareDataType\n"
        return "/verified/bash\n"

    directory = tmp_path / "tool-admission"
    directory.mkdir()
    monkeypatch.setattr(bazel_native_probe, "short_temp_directory", lambda _prefix: str(directory))
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "check_output", output)
    if missing == "identity":
        with pytest.raises(subprocess.CalledProcessError):
            bazel_native_probe.admit_tools()
    elif missing == "hardware":
        with pytest.raises(ValueError, match="system_profiler"):
            bazel_native_probe.admit_tools()
    else:
        bazel_native_probe.admit_tools()
    assert any("HostIdentity.from_system(environ={})" in command[-1] for command in commands)
    if missing is not None:
        assert not any("new-session" in command for command in commands)


def test_declared_admission_identity_imports_without_checkout(tmp_path):
    source = Path(__file__).resolve().parents[1]
    build = ast.parse((source / "BUILD.bazel").read_text())
    for node in build.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Name) or call.func.id != "filegroup":
            continue
        fields = {item.arg: ast.literal_eval(item.value) for item in call.keywords
                  if item.arg in {"name", "srcs"} and isinstance(item.value, (ast.Constant, ast.List))}
        if fields.get("name") != "native_execution_support":
            continue
        for name in fields["srcs"]:
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target)
        break
    else:
        pytest.fail("native execution support declaration is missing")
    command = ("import sys;sys.path.insert(0," + repr(str(tmp_path)) + ");"
               "from yomux_lib.infra.host_identity import HostIdentity;"
               "from yomux_lib.infra.filesystem_preflight import classify_filesystem;"
               "assert callable(HostIdentity.from_system) and callable(classify_filesystem)")
    result = subprocess.run([sys.executable, "-I", "-B", "-c", command], cwd=tmp_path,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_linux_fixture_roots_are_private_unique_and_preserve_artifact_destination(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    artifacts = str(tmp_path / "artifacts")
    monkeypatch.setenv("TEST_UNDECLARED_OUTPUTS_DIR", artifacts)
    monkeypatch.setenv("TEST_TMPDIR", str(tmp_path / "disk-backed-bazel-directory"))
    monkeypatch.setattr(bazel_native_entrypoint, "NATIVE_ACTION_TMPDIR", str(tmp_path))
    roots = []
    try:
        for _ in range(2):
            root = bazel_native_entrypoint.install_fixture_environment("Linux")
            roots.append(root.parent)
            assert root.parent.parent == tmp_path
            assert root.parent.stat().st_mode & 0o777 == 0o700
            assert Path(os.environ["HOME"]).is_relative_to(root)
            assert Path(os.environ["TMPDIR"]).is_relative_to(root)
            assert os.environ["TEST_UNDECLARED_OUTPUTS_DIR"] == artifacts
            assert os.environ["TEST_TMPDIR"] == str(root.parent)
        assert roots[0] != roots[1]
    finally:
        for root in roots:
            shutil.rmtree(root)


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_fixture_environment_rederives_bootstrap_caches_inside_owned_root(monkeypatch, tmp_path, system):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(bazel_native_entrypoint, "NATIVE_ACTION_TMPDIR", str(tmp_path))
    monkeypatch.setattr(bazel_native_entrypoint, "resource_coalition", lambda _pid: 123)
    monkeypatch.setenv(tmux_paths.SHORT_TMPDIR_ENV, str(tmp_path))
    foreign = tmp_path / "foreign-cache"
    for key in ("PYTHONPYCACHEPREFIX", "PIP_CACHE_DIR", "NPM_CONFIG_CACHE", "COVERAGE_FILE",
                "YOMUX_HOST_ARTIFACT_DIR", "YOLOMUX_HOST_ARTIFACT_DIR",
                "YOMUX_GENERATED_PYTHONPYCACHEPREFIX", "YOLOMUX_GENERATED_PYTHONPYCACHEPREFIX"):
        monkeypatch.setenv(key, str(foreign))
    root = bazel_native_entrypoint.install_fixture_environment(system)
    source = root / "example.py"
    source.write_bytes(b"value = 1\n")
    command = [sys.executable, "-B", "-c",
               "import py_compile,sys;print(py_compile.compile(sys.argv[1],doraise=True))",
               str(source)]
    result = subprocess.run(command, cwd=root, env=os.environ, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    destination = Path(result.stdout.strip())
    assert destination.is_relative_to(root)
    assert destination.is_file()
    assert not foreign.exists()


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
@pytest.mark.parametrize("declared_outputs", [False, True])
def test_fixture_evidence_uses_owned_outputs_and_preserves_both_aliases(monkeypatch, tmp_path, system, declared_outputs):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(bazel_native_entrypoint, "NATIVE_ACTION_TMPDIR", str(tmp_path))
    monkeypatch.setattr(bazel_native_entrypoint, "resource_coalition", lambda _pid: 123)
    monkeypatch.setenv(tmux_paths.SHORT_TMPDIR_ENV, str(tmp_path))
    action = tmp_path / "action"
    action.mkdir(mode=0o700)
    monkeypatch.setenv("TEST_TMPDIR", str(action))
    outputs = tmp_path / "outputs"
    if declared_outputs:
        monkeypatch.setenv("TEST_UNDECLARED_OUTPUTS_DIR", str(outputs))
    else:
        monkeypatch.delenv("TEST_UNDECLARED_OUTPUTS_DIR", raising=False)
    foreign = tmp_path / "foreign-evidence"
    monkeypatch.setenv("YOMUX_E2E_EVIDENCE_DIR", str(foreign))
    monkeypatch.setenv("YOLOMUX_E2E_EVIDENCE_DIR", str(foreign))
    root = bazel_native_entrypoint.install_fixture_environment(system)
    expected = (outputs if declared_outputs else root) / "evidence"
    assert os.environ["YOMUX_E2E_EVIDENCE_DIR"] == os.environ["YOLOMUX_E2E_EVIDENCE_DIR"] == str(expected)
    artifact = latency_calibration.write_latency_evidence(nodeid="fixture-evidence", label="owned", payload={"value": 1})
    assert artifact.is_relative_to(expected)
    assert json.loads(artifact.read_text())["value"] == 1
    assert expected.stat().st_mode & 0o777 == 0o700
    assert not foreign.exists()
    result = subprocess.run([fixture_executable("bash"), "-c",
        'source "$1"; bazel_test_prepare_environment; product_env_print E2E_EVIDENCE_DIR',
        "fixture", str(bazel_native_entrypoint.ROOT / "tools/bazel_test_common.sh")],
        env=os.environ, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == str(expected)


@pytest.mark.parametrize("key", native_process_scope.TEST_WRITABLE_DIRECTORY_ENV_KEYS)
@pytest.mark.parametrize("directory", ["/mnt/sda/tmp/native-cache/test.outputs", "/n/action-state"])
@pytest.mark.parametrize("prefix", ["YOMUX", "YOLOMUX"])
def test_fixture_evidence_accepts_only_declared_non_tmp_roots(monkeypatch, key, directory, prefix):
    for name in native_process_scope.TEST_WRITABLE_DIRECTORY_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    for name in ("YOMUX_E2E_EVIDENCE_DIR", "YOLOMUX_E2E_EVIDENCE_DIR"):
        monkeypatch.delenv(name, raising=False)
    root = Path(directory)
    monkeypatch.setenv(key, str(root))
    monkeypatch.setenv(prefix + "_E2E_EVIDENCE_DIR", str(root / "evidence"))
    assert latency_calibration._latency_evidence_root() == root / "evidence/latency-calibration"
    with pytest.raises(ValueError, match="declared action directories"):
        latency_calibration._latency_evidence_root(root / "../sibling")


def test_hermetic_linux_tmpfs_is_also_bound_into_pivoted_root(tmp_path):
    payload = tmp_path / "payload"
    for name in ("usr", "bin", "lib", "lib64", "etc", "opt"):
        (payload / "rootfs" / name).mkdir(parents=True)
    bundle = SimpleNamespace(root=payload, system="Linux", executables={
        name: payload / "bin" / name for name in ("bash", "chrome", "chromedriver", "cc")})
    flags = native_flags(bundle, [])
    assert "--sandbox_tmpfs_path=/tmp" in flags
    assert "--sandbox_add_mount_pair=/tmp:/tmp" in flags
    assert "--sandbox_tmpfs_path=/n" in flags
    assert "--sandbox_add_mount_pair=/n:/n" in flags
    assert not any(flag.startswith("--test_tmpdir=") for flag in flags)


def test_detached_synthetic_client_is_retired_without_signalling_foreign_child(monkeypatch):
    scope = NativeProcessScope()
    monkeypatch.setenv(SCOPE_ENV, scope.identity)
    foreign_env = dict(os.environ)
    foreign_env.pop(SCOPE_ENV)
    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], env=foreign_env)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
        env=synthetic_test_environment(), start_new_session=True)
    try:
        assert scope.retire()
        assert child.wait(timeout=3) < 0
        assert foreign.poll() is None
    finally:
        for process in (child, foreign):
            if process.poll() is None:
                process.kill()
            process.wait()


@pytest.mark.skipif(platform.system() != "Darwin", reason="Darwin inspects process environments through ps")
def test_process_scope_retires_child_with_non_utf8_environment():
    scope = NativeProcessScope()
    environment = scope.environment({"PATH": os.defpath, "YOMUX_TEST_NON_UTF8": "\udcc0"})
    child = subprocess.Popen([sys.executable, "-I", "-B", "-c",
        "import signal;print('ready',flush=True);signal.pause()"], env=environment,
        stdout=subprocess.PIPE, text=True, start_new_session=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        assert child.pid in {proof.pid for proof in scope.members()}
        assert scope.retire()
        assert child.wait(timeout=3) < 0
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


@pytest.mark.skipif(platform.system() != "Darwin", reason="Darwin ps preserves non-UTF-8 process arguments")
@pytest.mark.parametrize("reader", ["gate", "socket", "migration", "latency", "renderer", "registry"])
def test_process_census_handles_non_utf8_arguments(tmp_path, monkeypatch, reader):
    socket_path = str(tmp_path / "socket")
    child = subprocess.Popen([sys.executable, "-I", "-B", "-c",
        "import signal;print('ready',flush=True);signal.pause()", "\udcc0", "--socket", socket_path, "yomux.py", "--type=renderer"],
        env={"PATH": os.defpath}, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        if reader == "gate":
            rows = check_runner.process_table()
            command = next(command for pid, _parent, command in rows if pid == child.pid)
            assert b"\xc0" in os.fsencode(command)
        elif reader == "socket":
            assert yostats_active_browser_window.service_pid_for_socket(socket_path) == child.pid
        elif reader == "migration":
            rows = migrate_yomux_data.active_writer_processes(tmp_path / "no-proc")
            assert child.pid in {row["pid"] for row in rows}
        elif reader == "latency":
            assert b"\xc0" in os.fsencode(system_status_latency_probe.process_command(child.pid))
        elif reader == "renderer":
            monkeypatch.setattr(yostats_active_browser_window, "descendants_of", lambda _pid: [child.pid])
            assert yostats_active_browser_window.chrome_renderer_cpu_snapshot(child.pid)["pids"] == [child.pid]
        else:
            rows = local_services_registry.bounded_process_table(require_complete=True)
            assert b"\xc0" in os.fsencode(rows[child.pid].command)
    finally:
        child.terminate()
        child.wait()


@pytest.mark.skipif(platform.system() != "Darwin", reason="Linux entrypoint requires a qualified devpts sandbox")
@inherited_test_tree_contract
def test_entrypoint_cancellation_retires_its_separate_session(tmp_path):
    tmp_path.chmod(0o700)
    environment = dict(os.environ, TEST_TMPDIR=str(tmp_path), YOMUX_NATIVE_SHORT_TMPDIR=str(tmp_path))
    process = subprocess.Popen([sys.executable, "tools/bazel_native_entrypoint.py", "/usr/bin/python3", "-I", "-B", "-c",
        "import os,time; print(os.getpid(),flush=True); time.sleep(120)"], env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    child_pid = int(process.stdout.readline())
    try:
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 128 + signal.SIGTERM, stderr
        assert '"retired": true' in stdout
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        cleanup_native_fixture_roots(tmp_path, resource_coalition(os.getpid()))


@pytest.mark.skipif(platform.system() != "Darwin", reason="Linux entrypoint requires a qualified devpts sandbox")
@inherited_test_tree_contract
def test_second_cancellation_does_not_interrupt_bounded_retirement(tmp_path):
    tmp_path.chmod(0o700)
    command = "import os,signal,time; signal.signal(signal.SIGTERM,lambda *_: print('TERM',flush=True)); print(os.getpid(),flush=True); time.sleep(120)"
    process = subprocess.Popen([sys.executable, "tools/bazel_native_entrypoint.py", "/usr/bin/python3", "-I", "-B", "-c", command],
        env=dict(os.environ, TEST_TMPDIR=str(tmp_path), YOMUX_NATIVE_SHORT_TMPDIR=str(tmp_path)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    child_pid = int(process.stdout.readline())
    try:
        process.send_signal(signal.SIGTERM)
        assert process.stdout.readline().strip() == "TERM"
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 128 + signal.SIGTERM, stderr
        assert '"retired": true' in stdout
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        cleanup_native_fixture_roots(tmp_path, resource_coalition(os.getpid()))


@pytest.mark.skipif(platform.system() != "Darwin", reason="launchd resource coalitions are a Darwin execution boundary")
@launchd_boundary_contract
def test_launchd_coalition_retires_orphaned_multigeneration_empty_environment(tmp_path):
    label = "org.yomux.test." + uuid.uuid4().hex
    domain = "gui/" + str(os.getuid())
    status = tmp_path / "status.json"
    status.write_bytes(b"")
    child_code = "import os,signal;print(os.getpid(),flush=True);signal.pause()"
    middle_code = ("import subprocess,sys;child=subprocess.Popen([sys.executable,'-c',sys.argv[1]],env={},"
                   "start_new_session=True,stdout=subprocess.PIPE,text=True);print(child.stdout.readline().strip(),flush=True)")
    parent_code = ("import ctypes,json,os,pathlib,subprocess,sys;"
                   "middle=subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[3]],env={},"
                   "start_new_session=True,stdout=subprocess.PIPE,text=True);child=int(middle.stdout.readline());"
                   "middle.wait();lib=ctypes.CDLL('/usr/lib/libproc.dylib');data=(ctypes.c_uint64*5)();"
                   "lib.proc_pidinfo(os.getpid(),20,0,ctypes.byref(data),40);"
                   "pathlib.Path(sys.argv[1]).write_text(json.dumps({'child':child,'coalition':data[0]}));os._exit(0)")
    job = tmp_path / "job.plist"
    job.write_bytes(plistlib.dumps({"Label": label, "RunAtLoad": True,
        "ProgramArguments": [sys.executable, "-I", "-c", parent_code, str(status), middle_code, child_code],
        "StandardOutPath": str(tmp_path / "job.out"), "StandardErrorPath": str(tmp_path / "job.err")}))
    job.chmod(0o600)
    foreign = subprocess.Popen([sys.executable, "-c", "import signal;signal.pause()"], start_new_session=True)
    queue = select.kqueue()
    bootstrapped = False
    scope = None
    try:
        with status.open("rb") as ready:
            queue.control([select.kevent(ready.fileno(), filter=select.KQ_FILTER_VNODE,
                flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE, fflags=select.KQ_NOTE_WRITE)], 0, 0)
            result = subprocess.run(["/bin/launchctl", "bootstrap", domain, str(job)], capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
            bootstrapped = True
            queue.control([], 1, 10)
        record = json.loads(status.read_bytes())
        scope = DarwinResourceCoalitionScope(record["coalition"])
        assert resource_coalition(foreign.pid) != scope.identity
        assert record["child"] in {proof.pid for proof in scope.members()}
        assert resource_coalition_task_count(scope.identity) > 0
        assert scope.retire()
        assert resource_coalition_task_count(scope.identity) == 0
        assert process_start_key(record["child"]) is None
        assert foreign.poll() is None
    finally:
        queue.close()
        try:
            if scope is not None:
                assert scope.retire()
        finally:
            if foreign.poll() is None:
                foreign.kill()
            foreign.wait()
            if bootstrapped:
                result = subprocess.run(["/bin/launchctl", "bootout", domain + "/" + label], capture_output=True, text=True)
                assert result.returncode == 0, result.stderr


@pytest.mark.skipif(platform.system() != "Darwin", reason="kqueue log watches are Darwin-specific")
def test_launch_controller_log_watch_clears_drained_events_and_observes_later_writes(tmp_path):
    path = tmp_path / "worker.log"
    path.write_bytes(b"")
    queue = select.kqueue()
    try:
        with path.open("rb") as stream, path.open("ab", buffering=0) as writer:
            native_darwin_launcher.watch_log(queue, stream)
            writer.write(b"first\n")
            assert len(queue.control([], 1, 1)) == 1
            assert stream.read() == b"first\n"
            assert queue.control([], 1, 0) == []
            writer.write(b"second\n")
            assert len(queue.control([], 1, 1)) == 1
            assert stream.read() == b"second\n"
            assert queue.control([], 1, 0) == []
    finally:
        queue.close()


@pytest.mark.skipif(platform.system() != "Darwin", reason="launchd resource coalitions are a Darwin execution boundary")
@launchd_boundary_contract
def test_launch_controller_propagates_worker_failure_and_removes_exact_job(tmp_path, monkeypatch, capfd):
    identity = uuid.uuid4()
    monkeypatch.setattr(native_darwin_launcher.uuid, "uuid4", lambda: identity)
    contract = SimpleNamespace(ROOT=Path(__file__).absolute().parents[1])
    bundle = SimpleNamespace(root=tmp_path / "missing-payload", executables={"python3": Path(sys.executable)})
    assert native_darwin_launcher.run_native([], contract, bundle) == 78
    captured = capfd.readouterr()
    assert "QUALIFIED NATIVE EXECUTION REFUSED" in captured.err
    assert '"phase": "controller-finally"' in captured.out
    assert '"boundary": "darwin-resource-coalition"' in captured.out
    result = subprocess.run(["/bin/launchctl", "print", "gui/" + str(os.getuid()) + "/org.yomux.native." + identity.hex],
        capture_output=True, text=True)
    assert result.returncode != 0


@pytest.mark.skipif(platform.system() != "Darwin", reason="launchd resource coalitions are a Darwin execution boundary")
@pytest.mark.parametrize("mode", ["success", "failure", "cancel", "kill"])
@launchd_boundary_contract
def test_launch_controller_cleans_detached_empty_environment_on_every_exit(tmp_path, mode):
    source_root = Path(__file__).absolute().parents[1]
    tmp_path.chmod(0o700)
    fixture_base = tmp_path / "fixtures"
    fixture_base.mkdir(mode=0o700)
    foreign_fixture = create_native_fixture_root(fixture_base, resource_coalition(os.getpid()))
    (foreign_fixture / "sentinel").write_bytes(b"foreign")
    ready = tmp_path / "ready.json"
    ready.write_bytes(b"")
    identity = uuid.uuid4().hex
    worker_script = tmp_path / "worker.py"
    sleeper = "import os,signal;print(os.getpid(),flush=True);signal.pause()"
    intermediate = ("import subprocess,sys;child=subprocess.Popen([sys.executable,'-c',sys.argv[1]],"
                    "env={},start_new_session=True,stdout=subprocess.PIPE,text=True);"
                    "print(child.stdout.readline().strip(),flush=True)")
    worker_script.write_text(f"""import json
import os
import signal
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, {str(source_root)!r})
from tools import native_darwin_launcher as launcher
from tools.tmux_paths import create_native_fixture_root

def exercise(arguments, contract, *, invocation_scope):
    fixture = create_native_fixture_root(invocation_scope.fixture_base, invocation_scope.identity)
    (fixture / "sentinel").write_bytes(b"owned")
    intermediate = subprocess.Popen([sys.executable, '-c', {intermediate!r}, {sleeper!r}],
        env={{}}, start_new_session=True, stdout=subprocess.PIPE, text=True)
    child = int(intermediate.stdout.readline())
    intermediate.wait()
    Path({str(ready)!r}).write_text(json.dumps({{'child': child, 'coalition': invocation_scope.identity, 'fixture': str(fixture), 'worker': os.getpid()}}))
    print('fixture stdout', flush=True)
    print('fixture stderr', file=sys.stderr, flush=True)
    if {mode!r} in ('cancel', 'kill'):
        signal.pause()
    if {mode!r} == 'failure':
        raise ValueError('fixture worker failed')
    return 0

launcher.EXECUTION_CONTRACT.bazel_native.run_native = exercise
sys.exit(launcher.worker(Path(sys.argv[2])))
""")
    controller_code = f"""import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, {str(source_root)!r})
from tools import native_darwin_launcher as launcher
launcher.__file__ = {str(worker_script)!r}
launcher.uuid.uuid4 = lambda: uuid.UUID(hex={identity!r})
sys.exit(launcher.run_native([], SimpleNamespace(ROOT=Path({str(tmp_path)!r})),
    SimpleNamespace(root=Path({str(tmp_path / 'payload')!r}), executables={{'python3': Path(sys.executable)}}), fixture_base=Path({str(fixture_base)!r})))
"""
    foreign = subprocess.Popen([sys.executable, "-c", "import signal;signal.pause()"], start_new_session=True)
    controller = None
    scope = None
    queue = select.kqueue()
    try:
        with ready.open("rb") as indicator:
            queue.control([select.kevent(indicator.fileno(), filter=select.KQ_FILTER_VNODE,
                flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE, fflags=select.KQ_NOTE_WRITE)], 0, 0)
            controller = subprocess.Popen([sys.executable, "-I", "-c", controller_code],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            queue.control([], 1, 10)
        record = json.loads(ready.read_bytes())
        scope = DarwinResourceCoalitionScope(record["coalition"])
        if mode in ("cancel", "kill"):
            assert (Path(record["fixture"]) / "sentinel").read_bytes() == b"owned"
            if mode == "cancel":
                controller.send_signal(signal.SIGTERM)
            else:
                os.kill(record["worker"], signal.SIGKILL)
        stdout, stderr = controller.communicate(timeout=15)
        if mode == "kill":
            assert controller.returncode != 0, stderr
        else:
            assert controller.returncode == {"success": 0, "failure": 78, "cancel": 143}[mode], stderr
        assert not Path(record["fixture"]).exists()
        assert (foreign_fixture / "sentinel").read_bytes() == b"foreign"
        assert "fixture stdout" in stdout
        assert "fixture stderr" in stderr
        assert '"phase": "controller-finally"' in stdout
        assert resource_coalition_task_count(scope.identity) == 0
        assert process_start_key(record["child"]) is None
        assert foreign.poll() is None
        result = subprocess.run(["/bin/launchctl", "print", "gui/" + str(os.getuid()) + "/org.yomux.native." + identity],
            capture_output=True, text=True)
        assert result.returncode != 0
    finally:
        queue.close()
        if controller is not None and controller.poll() is None:
            controller.terminate()
            controller.wait(timeout=10)
        if scope is not None:
            assert scope.retire()
        if foreign.poll() is None:
            foreign.kill()
        foreign.wait()
        cleanup_native_fixture_roots(fixture_base, resource_coalition(os.getpid()))


def test_darwin_bazel_controller_has_no_os_sandbox(tmp_path):
    envelope, policy = bazel_native.offline_envelope(
        SimpleNamespace(system="Darwin", root=tmp_path / "bundle"), None, tmp_path / "home")
    assert envelope == []
    assert json.loads(policy) == {"sandbox_scope": "test-process-tree", "setup_os_sandboxed": False,
                                  "non_test_actions_os_sandboxed": False}


@pytest.mark.skipif(platform.system() != "Linux", reason="private subreaper ownership requires Linux")
def test_private_linux_subreaper_retires_multigeneration_empty_environment(tmp_path):
    source_root = Path(__file__).absolute().parents[1]
    ready = tmp_path / "owned.json"
    sleeper = "import signal;signal.pause()"
    middle = ("import subprocess,sys;child=subprocess.Popen([sys.executable,'-c',sys.argv[1]],env={},"
              "start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print(child.pid,flush=True)")
    command = ("import subprocess,sys;middle=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],env={},"
               "start_new_session=True,stdout=subprocess.PIPE,text=True);print(middle.stdout.readline().strip(),flush=True);"
               "middle.wait();sys.exit(23)")
    supervisor = f"""import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, {str(source_root)!r})
from tools.native_process_scope import NativeProcessScope
from tests.browser_helpers.webdriver_lease import process_start_key
scope = NativeProcessScope(private_supervisor=True)
command = subprocess.Popen([sys.executable, '-c', {command!r}, {middle!r}, {sleeper!r}], env={{}},
    stdout=subprocess.PIPE, text=True)
scope.track(command)
child = int(command.stdout.readline())
Path({str(ready)!r}).write_text(json.dumps({{'pid': child, 'birth': process_start_key(child)}}))
status = command.wait()
retired = scope.retire()
print(json.dumps({{'child': child, 'status': status, 'retired': retired}}), flush=True)
"""
    foreign = subprocess.Popen([sys.executable, "-c", "import signal;signal.pause()"], start_new_session=True)
    try:
        result = subprocess.run([sys.executable, "-I", "-c", supervisor], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        record = json.loads(result.stdout)
        assert record["retired"] is True
        assert record["status"] == 23
        assert process_start_key(record["child"]) is None
        assert foreign.poll() is None
    finally:
        if ready.exists():
            record = json.loads(ready.read_bytes())
            if process_start_key(record["pid"]) == record["birth"] and record["birth"] is not None:
                os.kill(record["pid"], signal.SIGKILL)
        if foreign.poll() is None:
            foreign.kill()
        foreign.wait()


@pytest.mark.skipif(platform.system() != "Linux", reason="private subreaper ownership requires Linux")
def test_private_linux_subreaper_preserves_direct_popen_wait_status():
    source_root = Path(__file__).absolute().parents[1]
    command = "import os,signal;signal.signal(signal.SIGTERM,signal.SIG_IGN);print(os.getpid(),flush=True);signal.pause()"
    supervisor = f"""import json
import subprocess
import sys
sys.path.insert(0, {str(source_root)!r})
from tools.native_process_scope import NativeProcessScope
scope = NativeProcessScope(private_supervisor=True)
command = subprocess.Popen([sys.executable, '-c', {command!r}], stdout=subprocess.PIPE, text=True)
scope.track(command)
command.stdout.readline()
retired = scope.retire()
print(json.dumps({{'retired': retired, 'status': command.wait()}}), flush=True)
"""
    result = subprocess.run([sys.executable, "-I", "-c", supervisor], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"retired": True, "status": -signal.SIGKILL}


@pytest.mark.skipif(platform.system() != "Linux", reason="private subreaper ownership requires Linux")
def test_private_linux_reaps_orphans_while_direct_test_is_running():
    source_root = Path(__file__).absolute().parents[1]
    orphan = ("import os;from pathlib import Path;record=Path('/proc/self/stat').read_text();"
              "birth=record[record.rfind(')')+1:].split()[19];print(os.getpid(),'proc:'+birth,flush=True);os._exit(0)")
    middle = ("import os,subprocess,sys;child=subprocess.Popen([sys.executable,'-I','-B','-c',sys.argv[1]],"
              "stdout=subprocess.PIPE,text=True);print(child.stdout.readline().strip(),flush=True);os._exit(0)")
    direct = ("import subprocess,sys;child=subprocess.Popen([sys.executable,'-I','-B','-c',sys.argv[1],sys.argv[2]],"
              "stdout=subprocess.PIPE,text=True);line=child.stdout.readline();child.wait();"
              "print(line.strip(),flush=True);sys.stdin.readline();sys.exit(37)")
    supervisor = f"""import json,os,signal,subprocess,sys
sys.path.insert(0, {str(source_root)!r})
sys.path.insert(0, {str(Path(pytest.__file__).absolute().parents[1])!r})
from tools.native_process_scope import NativeProcessScope
from tests.tmux_runtime import wait_for_isolated_tmux_pane_exit
scope=NativeProcessScope(private_supervisor=True)
previous=signal.getsignal(signal.SIGCHLD)
command=subprocess.Popen([sys.executable,'-I','-B','-c',{direct!r},{middle!r},{orphan!r}],
    stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
scope.track(command)
try:
    with scope.reaping():
        pid,birth=command.stdout.readline().split()
        wait_for_isolated_tmux_pane_exit(((int(pid),birth),))
        assert command.poll() is None
        command.stdin.write('release\\n')
        command.stdin.flush()
        status=command.wait()
    assert signal.getsignal(signal.SIGCHLD)==previous
    immediate=subprocess.Popen([sys.executable,'-I','-B','-c','import sys;sys.exit(41)'])
    os.waitid(os.P_PID,immediate.pid,os.WEXITED|os.WNOWAIT)
    scope.track(immediate)
    with scope.reaping():
        early_status=immediate.wait()
    print(json.dumps({{'status':status,'early_status':early_status,'reaped_while_running':True}}))
finally:
    assert scope.retire()
    command.wait()
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", supervisor], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": 37, "early_status": 41, "reaped_while_running": True}


def test_private_supervisor_does_not_authorize_a_reused_parents_foreign_child(monkeypatch):
    scope = NativeProcessScope()
    scope.private_supervisor = True
    scope.supervisor = DriverGeneration(10, "root")
    records = {10: ("S", 1, "root"), 20: ("S", 10, "owned-parent"), 30: ("S", 20, "foreign-child")}

    def process(pid):
        if pid == 30:
            records[20] = ("S", 999, "reused-parent")
        return records.get(pid)

    monkeypatch.setattr(scope, "children_of", lambda parent: {20} if parent.pid == 10 else {30})
    monkeypatch.setattr(scope, "linux_process", process)
    monkeypatch.setattr(native_process_scope, "process_start_key", lambda pid: records[pid][2])
    assert scope.members() == []
    assert 30 not in scope.descendants


def test_short_temp_base_preserves_defaults_and_requires_private_absolute_root(monkeypatch, tmp_path):
    monkeypatch.delenv(SHORT_TMPDIR_ENV, raising=False)
    assert short_temp_base() == Path("/tmp")
    monkeypatch.setenv(SHORT_TMPDIR_ENV, "relative")
    with pytest.raises(ValueError, match="absolute"):
        short_temp_base()
    tmp_path.chmod(0o755)
    monkeypatch.setenv(SHORT_TMPDIR_ENV, str(tmp_path))
    with pytest.raises(ValueError, match="0700"):
        short_temp_base()
    tmp_path.chmod(0o700)
    assert short_temp_base() == tmp_path
    assert synthetic_test_environment()[SHORT_TMPDIR_ENV] == str(tmp_path)


def test_fixture_registry_cleanup_is_exact_and_handles_preallocation_sigkill(tmp_path):
    tmp_path.chmod(0o700)
    owned = create_native_fixture_root(tmp_path, 123)
    foreign = create_native_fixture_root(tmp_path, 1234)
    (owned / "sentinel").write_bytes(b"owned")
    (foreign / "sentinel").write_bytes(b"foreign")
    # A killed allocator can leave its journal entry before mkdir.
    pending = tmp_path / ".owners/c123-a0000"
    pending.touch(mode=0o600)
    cleanup_native_fixture_roots(tmp_path, 123)
    assert not owned.exists() and not pending.exists()
    assert (foreign / "sentinel").read_bytes() == b"foreign"
    assert len(list((tmp_path / ".owners").iterdir())) == 2
    cleanup_native_fixture_roots(tmp_path, 1234)


def test_fixture_cleanup_removes_frozen_directories_without_following_aliases_or_changing_shared_files(tmp_path):
    tmp_path.chmod(0o700)
    owned = create_native_fixture_root(tmp_path, 123)
    foreign = create_native_fixture_root(tmp_path, 1234)
    shared = foreign / "immutable-blob"
    shared.write_bytes(b"foreign")
    shared.chmod(0o444)
    frozen = owned / "copied-git"
    nested = frozen / "objects"
    nested.mkdir(parents=True)
    os.link(shared, nested / "blob")
    (frozen / "alias").symlink_to(foreign, target_is_directory=True)
    nested.chmod(0o555)
    frozen.chmod(0o555)
    cleanup_native_fixture_roots(tmp_path, 123)
    assert not owned.exists()
    assert shared.read_bytes() == b"foreign"
    assert shared.stat().st_mode & 0o777 == 0o444
    assert foreign.stat().st_mode & 0o777 == 0o700
    cleanup_native_fixture_roots(tmp_path, 1234)


def test_fixture_registry_refuses_symlinked_roots_without_touching_target(tmp_path):
    tmp_path.chmod(0o700)
    owned = create_native_fixture_root(tmp_path, 123)
    foreign = tmp_path / "foreign"
    foreign.mkdir(mode=0o700)
    (foreign / "sentinel").write_bytes(b"foreign")
    owned.rmdir()
    owned.symlink_to(foreign, target_is_directory=True)
    with pytest.raises(ValueError, match="0700"):
        cleanup_native_fixture_roots(tmp_path, 123)
    assert (foreign / "sentinel").read_bytes() == b"foreign"


def test_fixture_cleanup_waits_for_successful_kernel_retirement(tmp_path, capsys):
    tmp_path.chmod(0o700)
    root = create_native_fixture_root(tmp_path, 123)
    scope = SimpleNamespace(identity=123, fixture_base=tmp_path, retire=lambda: False)
    with pytest.raises(ValueError, match="still has owned processes"):
        native_darwin_launcher.cleanup_invocation(scope, "cancelled")
    assert root.exists()
    scope.retire = lambda: True
    native_darwin_launcher.cleanup_invocation(scope, "cancelled")
    assert not root.exists()


def test_unreserved_colliding_journal_does_not_own_another_coalition_root(tmp_path):
    tmp_path.chmod(0o700)
    foreign = create_native_fixture_root(tmp_path, 1234)
    (foreign / "sentinel").write_bytes(b"foreign")
    forged_collision = tmp_path / ".owners" / ("c123-" + foreign.name)
    forged_collision.touch(mode=0o600)
    cleanup_native_fixture_roots(tmp_path, 123)
    assert (foreign / "sentinel").read_bytes() == b"foreign"
    assert not forged_collision.exists()
    cleanup_native_fixture_roots(tmp_path, 1234)


def test_admitted_short_fixture_root_fits_all_portable_product_sockets(monkeypatch, tmp_path):
    base = Path("/private/tmp/abcd/a0123")
    # Do not allocate the synthetic path: the existing root owner is validated separately.
    monkeypatch.setattr("tools.tmux_paths.short_temp_base", lambda: base)
    monkeypatch.setenv(SHORT_TMPDIR_ENV, str(base))
    root = base / "f012345"
    for candidate in (root, base / "y4294967295" / "p65535"):
        for path in rooted_socket_candidates(candidate / "runtime"):
            assert len(os.fsencode(path)) <= 96


def test_short_fixture_paths_reserve_distinct_names_under_forced_collision(monkeypatch, tmp_path):
    tmp_path.chmod(0o700)
    monkeypatch.setenv(SHORT_TMPDIR_ENV, str(tmp_path))
    tokens = iter(("000000", "000000", "000001"))
    monkeypatch.setattr(tmux_paths.uuid, "uuid4", lambda: SimpleNamespace(hex=next(tokens)))
    first = tmux_paths.short_fixture_path("first")
    second = tmux_paths.short_fixture_path("second")
    assert first != second
    assert not first.exists() and not second.exists()
    assert len(list((tmp_path / ".paths").iterdir())) == 2


def test_native_cache_root_requires_absolute_private_directory_before_resolving(monkeypatch, tmp_path):
    monkeypatch.setenv("YOMUX_NATIVE_CACHE_ROOT", "relative")
    with pytest.raises(ValueError, match="absolute"):
        bazel_native.native_cache_root()
    root = tmp_path / "owned-cache"
    root.mkdir(mode=0o755)
    monkeypatch.setenv("YOMUX_NATIVE_CACHE_ROOT", str(root))
    with pytest.raises(ValueError, match="0700"):
        bazel_native.native_cache_root()
    root.chmod(0o700)
    assert bazel_native.native_cache_root() == root.resolve()
    link = tmp_path / "cache-link"
    link.symlink_to(root, target_is_directory=True)
    monkeypatch.setenv("YOMUX_NATIVE_CACHE_ROOT", str(link))
    with pytest.raises(ValueError, match="0700"):
        bazel_native.native_cache_root()


@pytest.mark.skipif(platform.system() != "Darwin", reason="launchd job admission is a Darwin kernel policy")
@launchd_boundary_contract
def test_darwin_test_policy_refuses_launchd_jobs(tmp_path):
    policy = bazel_native_entrypoint.action_write_policy(tmp_path, {})
    assert_launchd_job_creation_denied(tmp_path, ["/usr/bin/sandbox-exec", "-p", policy])


@pytest.mark.skipif(platform.system() != "Darwin", reason="launchd sandbox action routing is Darwin-specific")
@launchd_boundary_contract
def test_native_action_launchd_cases_execute_the_denial_contract(tmp_path):
    source_root = Path(__file__).absolute().parents[1]
    script = f"""import sys
from pathlib import Path
sys.path.insert(0, {str(source_root)!r})
sys.path.insert(0, {str(Path(pytest.__file__).absolute().parents[1])!r})
from tests.test_native_execution import (
    test_launchd_coalition_retires_orphaned_multigeneration_empty_environment,
    test_launch_controller_propagates_worker_failure_and_removes_exact_job,
    test_launch_controller_cleans_detached_empty_environment_on_every_exit,
)
for index, test in enumerate((test_launchd_coalition_retires_orphaned_multigeneration_empty_environment,
        test_launch_controller_propagates_worker_failure_and_removes_exact_job,
        test_launch_controller_cleans_detached_empty_environment_on_every_exit)):
    root = Path({str(tmp_path)!r}) / str(index)
    root.mkdir()
    test(tmp_path=root)
print('denial contracts executed: 3')
"""
    policy = bazel_native_entrypoint.action_write_policy(tmp_path, {}, operator_home=tmp_path / "operator-home")
    envelope = ["/usr/bin/sandbox-exec", "-p", policy]
    home = tmp_path / "home"
    temporary = tmp_path / "temporary"
    home.mkdir(mode=0o700)
    temporary.mkdir(mode=0o700)
    environment = synthetic_test_environment({"HOME": str(home), "TMPDIR": str(temporary),
        "YOMUX_BAZEL_EXECUTION": "qualified-native", "YOMUX_BAZEL_CONTRACT": "fixture-policy"})
    completed = subprocess.run([*envelope, sys.executable, "-I", "-c", script], env=environment,
        capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "denial contracts executed: 3"


@pytest.mark.skipif(platform.system() != "Darwin", reason="Linux entrypoint requires a qualified devpts sandbox")
@inherited_test_tree_contract
def test_entrypoint_private_writer_does_not_require_git_metadata(tmp_path):
    tmp_path.chmod(0o700)
    workspace = tmp_path / "declared-workspace"
    workspace.mkdir()
    root = Path(__file__).resolve().parents[1]
    declared = tmp_path / "declared-inputs"
    for name in ("yomux_lib", "tools"):
        shutil.copytree(root / name, declared / name, ignore=shutil.ignore_patterns("__pycache__"))
    command = (
        "import os,sys; from pathlib import Path; "
        + "sys.path.insert(0," + repr(str(declared)) + "); "
        + "from yomux_lib.infra.worktree_writer import acquire_worktree_writer; "
        + "lease=acquire_worktree_writer(Path.cwd(),purpose='declared-action'); "
        + "assert lease.slot_dir.is_relative_to(Path(os.environ['HOME']).parent); "
        + "lease.release(); print('PRIVATE_DECLARED_WRITER')"
    )
    result = subprocess.run([sys.executable, str(root / "tools/bazel_native_entrypoint.py"), "/usr/bin/python3", "-I", "-B", "-c", command],
        env=dict(os.environ, TEST_TMPDIR=str(tmp_path), YOMUX_NATIVE_SHORT_TMPDIR=str(tmp_path)),
        cwd=workspace, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "PRIVATE_DECLARED_WRITER" in result.stdout
    assert not (workspace / ".git").exists()


@pytest.mark.parametrize("absolute", [False, True])
def test_qualified_pytest_preserves_subdirectory_context(monkeypatch, pytestconfig, absolute):
    root = Path(__file__).resolve().parents[1]
    config_module = pytestconfig.pluginmanager.get_plugin(str(root / "conftest.py"))
    monkeypatch.chdir(root / "tests")
    monkeypatch.setenv("YOMUX_BAZEL_EXECUTION", "qualified-native")
    monkeypatch.delenv("YOMUX_BAZEL_CONTRACT", raising=False)
    monkeypatch.delenv("YOLOMUX_BAZEL_CONTRACT", raising=False)
    argument = str(root / "tests/test_native_execution.py") if absolute else "test_native_execution.py"
    captured = []
    monkeypatch.setattr(config_module, "_run_delegated_pytest", lambda config, command, environment: captured.append(command) or 0)
    assert config_module.pytest_cmdline_main(SimpleNamespace(invocation_params=SimpleNamespace(args=(argument,)))) == 0
    descriptor = json.loads(captured[0][-1].removeprefix("--test_arg="))
    assert descriptor["args"][-1] == "test_native_execution.py"
    assert bazel_native_step.working_directory(root, descriptor) == root / "tests"


@pytest.mark.parametrize("relative", ["..", "/tmp", "../outside"])
def test_native_step_refuses_working_directory_outside_declared_tree(tmp_path, relative):
    work = tmp_path / "workspace"
    work.mkdir()
    (tmp_path / "outside").mkdir()
    with pytest.raises(ValueError, match="working directory"):
        bazel_native_step.working_directory(work, {"cwd": relative})


def test_private_runtime_copy_restores_writable_cached_inputs(tmp_path):
    source = tmp_path / "cached-tree"
    source.mkdir()
    (source / "go.mod").write_bytes(b"module fixture\n")
    (source / "go.mod").chmod(0o444)
    (source / "command").write_bytes(fixture_script(b"#!/bin/sh\nexit 0\n"))
    (source / "command").chmod(0o555)
    source.chmod(0o555)
    destination = tmp_path / "private-tree"
    copy_runtime_tree(source, destination, writable=True)
    (destination / "go.mod").write_bytes(b"module updated\n")
    assert (source / "go.mod").read_bytes() == b"module fixture\n"
    assert subprocess.run([str(destination / "command")]).returncode == 0


def test_missing_go_seed_dependency_refuses_admission_before_cached_results(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / ".bazelversion").write_bytes(b"9.2.0\n")
    cache = tmp_path / "cache"
    cache.mkdir(mode=0o700)
    events = []
    context = {"revision": "a" * 40}
    contract = SimpleNamespace(validate_native_probe=lambda *args: None, execution_event=lambda **event: events.append(event))
    request = {"root": str(tmp_path), "workspace": str(source), "cache": str(cache),
               "flags": ["--test_env=YOMUX_TEST_REVISION=" + "a" * 40,
                         "--test_env=YOMUX_TEST_RELEASE_CONTEXT=" + json.dumps(context)],
               "bazel": "/fixture/bazel", "steps": [{"target": "//:go_tests", "options": []}],
               "admitted": False, "admission_only": False, "reuse": True, "cache_on": True,
               "system": "Linux", "host_pid": "1", "host_port": "1234"}
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "bazel 9.2.0\n")
    requested = []

    def execute(command, **kwargs):
        if "//:native_runtime_admission" in command:
            assert "--test_env=YOMUX_TEST_REVISION=" + "a" * 40 in command
            release_flags = [value for value in command if value.startswith("--test_env=YOMUX_TEST_RELEASE_CONTEXT=")]
            assert len(release_flags) == 1
            assert json.loads(release_flags[0].partition("YOMUX_TEST_RELEASE_CONTEXT=")[2]) == {"revision": "a" * 40}
            # The metadata/package graph can succeed while tidy's upstream
            # test dependency is absent. A cached requested test would pass.
            missing_dependency = "//:go_module_closure_test" in command
            output = "YOMUX_NATIVE_PROBE {}\n" + "YOMUX_NATIVE_CLEANUP {\"retired\":true}\n" * 2
            return SimpleNamespace(returncode=int(missing_dependency), stdout=output, stderr="")
        if "shutdown" not in command:
            requested.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", execute)
    with pytest.raises(ValueError, match="uncached native sandbox admission failed"):
        bazel_native.execute_lane(request, {}, contract)
    assert requested == []
    assert events == []


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires Seatbelt launch enforcement")
@inherited_test_tree_contract
def test_failed_seatbelt_application_never_runs_test_command(tmp_path):
    sentinel = tmp_path / "payload-ran"
    script = (
        "import sys;sys.path.insert(0," + repr(str(Path(__file__).absolute().parents[1])) + ");"
        "from tools import bazel_native_entrypoint as entry;"
        "entry.action_write_policy=lambda *args: '(invalid-policy)';"
        "sys.argv=['entry','/usr/bin/touch'," + repr(str(sentinel)) + "];"
        "sys.exit(entry.main())"
    )
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", script],
        env=dict(os.environ, TEST_TMPDIR=str(tmp_path), YOMUX_NATIVE_SHORT_TMPDIR=str(tmp_path)),
        capture_output=True, text=True, timeout=10)
    assert completed.returncode != 0
    assert not sentinel.exists()
    cleanup_native_fixture_roots(tmp_path, resource_coalition(os.getpid()))


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires LaunchServices and Seatbelt")
def test_darwin_test_policy_refuses_launchservices_escape(tmp_path):
    app = tmp_path / "Probe.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    label = "org.yomux.sandbox.probe." + uuid.uuid4().hex
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": label,
        "CFBundleExecutable": "probe", "CFBundlePackageType": "APPL", "LSBackgroundOnly": True}))
    sentinel = tmp_path / "escaped"
    executable = app / "Contents/MacOS/probe"
    executable.write_bytes(fixture_script("#!/bin/sh\nprintf '%s' \"$$\" > " + shlex.quote(str(sentinel)) +
                                           "\nexec /bin/sleep 120\n").encode())
    executable.chmod(0o700)

    def launch(envelope):
        sentinel.write_bytes(b"")
        queue = select.kqueue()
        try:
            with sentinel.open("rb") as indicator:
                queue.control([select.kevent(indicator.fileno(), filter=select.KQ_FILTER_VNODE,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE, fflags=select.KQ_NOTE_WRITE)], 0, 0)
                completed = subprocess.run([*envelope, "/usr/bin/open", "-n", str(app)],
                    capture_output=True, text=True, timeout=10)
                if completed.returncode == 0:
                    queue.control([], 1, 5)
            identity = sentinel.read_text()
            if identity:
                scope = DarwinResourceCoalitionScope(resource_coalition(int(identity)))
                assert scope.retire()
                jobs = subprocess.check_output(["/bin/launchctl", "list"], text=True)
                for line in jobs.splitlines():
                    if label in line:
                        subprocess.run(["/bin/launchctl", "bootout", "gui/" + str(os.getuid()) + "/" + line.split()[-1]],
                            check=True, capture_output=True)
            return completed.returncode, bool(identity)
        finally:
            queue.close()

    inherited = product_env("BAZEL_EXECUTION") == "qualified-native" and product_env("BAZEL_CONTRACT")
    if inherited:
        envelope = []
    else:
        assert launch([]) == (0, True), "LaunchServices positive control could not execute the fixture app"
        envelope = ["/usr/bin/sandbox-exec", "-p", bazel_native_entrypoint.action_write_policy(tmp_path, {})]
    status, escaped = launch(envelope)
    assert status != 0, "LaunchServices accepted an app launch outside the test coalition"
    assert not escaped, "LaunchServices app escaped the test policy"
