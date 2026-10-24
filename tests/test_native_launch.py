# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Real native launch transitions with a PATH containing no Python interpreter."""

import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

import pytest

from tests.isolated_dev_server import go_test_server_binary
from tools.instance_isolation import synthetic_test_environment
from tools.tmux_paths import short_temp_base


pytestmark = pytest.mark.shared_tooling
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def native_launch_environment(tmp_path):
    binary = go_test_server_binary(REPO)
    executable_path = tmp_path / "bin"
    executable_path.mkdir()
    for command in ("bash", "env", "dirname", "uname", "sha256sum", "mkdir", "cp", "mv", "rm", "chmod", "tmux", "git", "ps", "sh"):
        source = shutil.which(command)
        assert source, command
        shutil.copy2(source, executable_path / command)
    runtime = tempfile.TemporaryDirectory(prefix="yn-", dir=short_temp_base())
    runtime_root = Path(runtime.name)
    environment = synthetic_test_environment({
        "PATH": str(executable_path), "HOME": str(runtime_root / "home"),
        "YOMUX_ROOT": str(runtime_root / "product"),
        "YOMUX_GO_BINARY": str(binary),
        "YOMUX_TMUX_SOCKET": str(runtime_root / "absent-tmux.sock"),
        "YOMUX_STATS_DISABLE_COLLECTORS": "1",
    })
    assert shutil.which("python3", path=environment["PATH"]) is None
    try:
        yield binary, environment
    finally:
        runtime.cleanup()


def run_native(binary, environment, *arguments):
    return subprocess.run([str(binary), *arguments], env=environment, cwd=REPO,
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("selector", ["--deprecated-python", "--backend=python", "--backend=unknown", "-backend=python", "-backend=unknown"])
def test_native_rejected_selector_does_not_create_roots(native_launch_environment, selector):
    binary, environment = native_launch_environment
    result = run_native(binary, environment, "--launch", selector)
    assert result.returncode == 2, result.stderr
    assert not Path(environment["YOMUX_ROOT"]).exists()


@pytest.mark.parametrize("prefix", ["YOMUX_", "YOLOMUX_"])
def test_native_raw_python_environment_rejected_even_with_go_flag(native_launch_environment, prefix):
    binary, environment = native_launch_environment
    environment[prefix + "BACKEND"] = "python"
    result = run_native(binary, environment, "--launch", "--backend=go")
    assert result.returncode == 2, result.stderr
    assert not Path(environment["YOMUX_ROOT"]).exists()


def test_native_force_refuses_foreign_listener_without_root_mutation(native_launch_environment):
    binary, environment = native_launch_environment
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = run_native(binary, environment, "--launch", "--force", "--root", str(REPO),
                            "--http", "--host", "127.0.0.1", "--port", str(port))
        assert result.returncode != 0
        assert not Path(environment["YOMUX_ROOT"]).exists()
        with socket.create_connection(listener.getsockname(), timeout=2):
            pass


def test_installed_native_launch_duplicate_and_verified_replacement(native_launch_environment, tmp_path):
    binary, environment = native_launch_environment
    installed = tmp_path / "installed"
    result = subprocess.run([str(REPO / "tools/install.sh"), "--prefix", str(installed)],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert not [path for path in installed.rglob("*") if path.suffix in {".py", ".pyc", ".pyo"}]
    declaration = (REPO / "BUILD.bazel").read_text().split('name = "runtime_assets"', 1)[1].split("\n)", 1)[0]
    expected = {Path(name).name for name in re.findall(r'"(assets/runtime/[^"]+\.json)"', declaration)}
    assert len(expected) == 6
    assert {path.name for path in (installed / "lib/yomux/assets/runtime").iterdir()} == expected
    environment = dict(environment)
    environment.pop("YOMUX_GO_BINARY", None)
    environment.pop("YOLOMUX_GO_BINARY", None)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    version = subprocess.run([str(installed / "bin/yomux"), "--version"], env=environment, capture_output=True, text=True, timeout=10)
    assert version.returncode == 0, version.stderr
    environment["YOMUX_DEFAULT_PORT"] = str(port)
    arguments = [str(installed / "bin/yomux"), "--http", "--host", "127.0.0.1"]
    process = None
    replacement = None
    with (tmp_path / "server.log").open("w+") as output:
        try:
            process = subprocess.Popen(arguments, env=environment, cwd=REPO, stdout=output, stderr=output)
            installed_binary = installed / "lib/yomux/yomux-go"
            ready_args = ("--operator", "ready", "--root", str(installed / "lib/yomux"), "--http", "--host", "127.0.0.1", "--port", str(port), "--timeout", "20s")
            ready = run_native(installed_binary, environment, *ready_args)
            output.flush()
            assert ready.returncode == 0, ready.stderr + (tmp_path / "server.log").read_text()
            lease = Path(environment["YOMUX_ROOT"]) / "instance.lock"
            for command, selector in (("yomux", "--backend=auto"), ("yolomux", "--golang")):
                alias = subprocess.run([str(installed / "bin" / command), selector, "--version"], env=environment, capture_output=True, text=True, timeout=10)
                assert alias.returncode == 0, alias.stderr
                assert alias.stdout.startswith("yomux-go ")
            before = lease.read_bytes()
            duplicate = subprocess.run(arguments, env=environment, cwd=REPO, capture_output=True, text=True, timeout=30)
            assert duplicate.returncode != 0
            assert lease.read_bytes() == before
            assert process.poll() is None
            replacement = subprocess.Popen([*arguments, "--force"], env=environment, cwd=REPO, stdout=output, stderr=output)
            process.wait(timeout=20)
            ready = run_native(installed_binary, environment, *ready_args)
            assert ready.returncode == 0, ready.stderr + (tmp_path / "server.log").read_text()
            assert replacement.poll() is None
            assert json.loads(lease.read_bytes())["pid"] == replacement.pid
        finally:
            for owned in (replacement, process):
                if owned is not None and owned.poll() is None:
                    owned.terminate()
                    owned.wait(timeout=20)


@pytest.mark.parametrize("initial_http", [True, False], ids=["http-to-tls", "tls-to-http"])
def test_authenticated_replacement_can_change_protocol_with_strict_readiness(native_launch_environment, tmp_path, initial_http):
    binary, environment = native_launch_environment
    config = Path(environment["YOMUX_ROOT"]) / "config"
    config.mkdir(parents=True)
    (config / "auth.yaml").write_text("users:\n  - username: fixture\n    password: fixture-password\n    role: admin\n")
    (config / "auth-cookie-secret").write_text("ab" * 32)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    base = [str(binary), "--launch", "--root", str(REPO), "--port", str(port), "--host", "127.0.0.1"]
    initial = replacement = None
    with (tmp_path / "protocol-transition.log").open("w") as output:
        try:
            initial = subprocess.Popen([*base, *(["--http"] if initial_http else [])],
                                       env=environment, cwd=REPO, stdout=output, stderr=output)
            ready_args = ["--operator", "ready", "--root", str(REPO), "--port", str(port), "--host", "127.0.0.1"]
            initial_ready = run_native(binary, environment, *ready_args, "--timeout", "20s", *(["--http"] if initial_http else []))
            assert initial_ready.returncode == 0, initial_ready.stderr + (tmp_path / "protocol-transition.log").read_text()
            proof = run_native(binary, environment, "--operator", "verify-replacement", "--root", str(REPO),
                               "--port", str(port), "--host", "127.0.0.1", *([] if initial_http else ["--http"]))
            assert proof.returncode == 0, proof.stderr
            assert initial.poll() is None
            replacement = subprocess.Popen([*base, "--force", *([] if initial_http else ["--http"])],
                                           env=environment, cwd=REPO, stdout=output, stderr=output)
            initial.wait(timeout=20)
            ready = run_native(binary, environment, *ready_args, "--timeout", "20s", *([] if initial_http else ["--http"]))
            assert ready.returncode == 0, ready.stderr + (tmp_path / "protocol-transition.log").read_text()
            assert replacement.poll() is None
            wrong_protocol = run_native(binary, environment, *ready_args, "--timeout", "1s", *(["--http"] if initial_http else []))
            assert wrong_protocol.returncode != 0
        finally:
            for owned in (replacement, initial):
                if owned is not None and owned.poll() is None:
                    owned.terminate()
                    owned.wait(timeout=20)


@pytest.mark.parametrize("tls", [False, True])
def test_managed_native_launch_adopts_inherited_listener(native_launch_environment, tmp_path, tls):
    binary, environment = native_launch_environment
    http_flags = [] if tls else ["--http"]
    listener = socket.socket()
    process = None
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        descriptor = listener.fileno()
        planned = run_native(binary, environment, "--operator", "plan", "--root", str(REPO), "--port", str(port), *http_flags)
        assert planned.returncode == 0, planned.stderr
        plan = json.loads(planned.stdout)
        plan["assign"]["YOMUX_ROOT"] = environment["YOMUX_ROOT"]
        raw_plan = json.dumps(plan)
        plan_file = tmp_path / "row-plan.json"
        plan_file.write_text(raw_plan)
        with (tmp_path / "inherited.log").open("w") as output:
            process = subprocess.Popen([str(binary), "--operator", "exec", "--root", str(REPO), "--plan-json", raw_plan,
                                       "--", str(binary), "--launch", "--launch-plan-json", raw_plan, "--root", str(REPO),
                                       *http_flags, "--host", "127.0.0.1", "--port", str(port),
                                       "--listen-fd", str(descriptor)], env=environment, cwd=REPO,
                                      pass_fds=(descriptor,), stdout=output, stderr=output)
            listener.close()
            ready = run_native(binary, environment, "--operator", "ready", "--root", str(REPO),
                               *http_flags, "--host", "127.0.0.1", "--port", str(port), "--timeout", "20s")
            assert ready.returncode == 0, ready.stderr + (tmp_path / "inherited.log").read_text()
            assert process.poll() is None
            verification = run_native(binary, environment, "--operator", "verify-environment", "--root", str(REPO),
                                       "--port", str(port), *http_flags, "--pid", str(process.pid), "--plan-file", str(plan_file))
            assert verification.returncode == 0, verification.stderr
    finally:
        listener.close()
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=20)


@pytest.mark.parametrize("inherit_listener", [False, True])
def test_direct_native_plan_is_visible_to_process_environment_verification(native_launch_environment, tmp_path, inherit_listener):
    binary, environment = native_launch_environment
    environment = dict(environment)
    environment["TMPDIR"] = str(Path(environment.pop("YOMUX_ROOT")).parent)
    environment.pop("YOLOMUX_ROOT", None)
    listener = socket.socket()
    process = None
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        planned = run_native(binary, environment, "--operator", "plan-direct", "--root", str(REPO), "--port", str(port))
        assert planned.returncode == 0, planned.stderr
        plan_file = tmp_path / "native-plan.json"
        plan_file.write_text(planned.stdout)
        arguments = [str(binary), "--launch", "--root", str(REPO), "--port", str(port), "--http", "--host", "127.0.0.1"]
        descriptors = ()
        if inherit_listener:
            descriptors = (listener.fileno(),)
            arguments.extend(("--listen-fd", str(listener.fileno())))
        else:
            listener.close()
        with (tmp_path / "direct-native.log").open("w") as output:
            process = subprocess.Popen(arguments, env=environment, cwd=REPO, pass_fds=descriptors, stdout=output, stderr=output)
            listener.close()
            ready = run_native(binary, environment, "--operator", "ready", "--root", str(REPO),
                               "--port", str(port), "--http", "--host", "127.0.0.1", "--plan-file", str(plan_file), "--timeout", "20s")
            assert ready.returncode == 0, ready.stderr + (tmp_path / "direct-native.log").read_text()
            verified = run_native(binary, environment, "--operator", "verify-environment", "--root", str(REPO),
                                  "--port", str(port), "--http", "--pid", str(process.pid), "--plan-file", str(plan_file))
            assert verified.returncode == 0, verified.stderr
            assert process.poll() is None
    finally:
        listener.close()
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=20)
