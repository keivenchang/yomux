# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Real native launch transitions with a PATH containing no Python interpreter."""

import hashlib
from http.client import HTTPConnection
import json
import os
import re
import shlex
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

import pytest

from tests.isolated_dev_server import go_test_server_binary
from tests.helpers.fixture_tools import fixture_install_update_source
from tests.helpers.fixture_tools import run_fixture_cleanup_phases
from tests.gate_harness import FixtureMemberExitBarrier
from tests.tmux_runtime import start_isolated_default_tmux_runtime
from tests.tmux_runtime import stop_isolated_tmux_runtime
from tools.instance_isolation import synthetic_test_environment
from tools.tmux_paths import short_temp_base
from yomux_lib.infra.worktree_writer import acquire_worktree_writer
from yomux_lib.host_identity import process_start_identity


pytestmark = pytest.mark.shared_tooling
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def native_launch_environment(tmp_path):
    binary = go_test_server_binary(REPO)
    executable_path = tmp_path / "bin"
    executable_path.mkdir()
    commands = ("bash", "env", "dirname", "uname", "sha256sum", "mkdir", "cp", "mv", "rm", "chmod", "tmux", "git", "ps", "sh", "cat", "date", "sleep", "nohup", "curl")
    if sys.platform == "darwin":
        commands += ("lsof",)
    else:
        commands += ("setsid",)
    for command in commands:
        source = shutil.which(command)
        assert source, command
        destination = executable_path / command
        if sys.platform == "darwin":
            # macOS kills relocated platform-signed tools such as lsof. Keep
            # their executable identity while exposing no Python on PATH.
            destination.write_text(f'#!/bin/sh\nexec {shlex.quote(source)} "$@"\n')
            destination.chmod(0o755)
        else:
            shutil.copy2(source, destination)
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


@pytest.fixture
def authenticated_native_launch_environment(native_launch_environment):
    binary, environment = native_launch_environment
    config = Path(environment["YOMUX_ROOT"]) / "config"
    config.mkdir(parents=True)
    (config / "auth.yaml").write_text("users:\n  - username: fixture\n    password: fixture-password\n    role: admin\n")
    (config / "auth-cookie-secret").write_text("ab" * 32)
    return binary, environment


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
    source = fixture_install_update_source(tmp_path, environment)
    result = subprocess.run([str(REPO / "tools/install.sh"), "--prefix", str(installed), "--source-root", str(source)],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert not [path for path in installed.rglob("*") if path.suffix in {".py", ".pyc", ".pyo"}]
    declaration = (REPO / "BUILD.bazel").read_text().split('name = "runtime_assets"', 1)[1].split("\n)", 1)[0]
    expected = {Path(name).name for name in re.findall(r'"(assets/runtime/[^"]+\.json)"', declaration)}
    assert len(expected) == 8
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


def test_installed_boot_uses_verified_assets_without_its_recorded_source(authenticated_native_launch_environment, monkeypatch, tmp_path):
    _binary, environment = authenticated_native_launch_environment
    prefix = tmp_path / "installed"
    source = fixture_install_update_source(tmp_path, environment)
    installed = subprocess.run([str(REPO / "tools/install.sh"), "--prefix", str(prefix), "--source-root", str(source)],
                               env=environment, capture_output=True, text=True, timeout=30)
    assert installed.returncode == 0, installed.stderr
    root = prefix / "lib/yomux"
    for name in (".git", "static_src", "node_modules", "tools/codemirror-bundle", "tools/prosemirror-bundle"):
        assert not (root / name).exists(), name
    metadata = json.loads((root / "install-source.json").read_bytes())
    metadata["source_root"] = str(tmp_path / "unavailable-source")
    (root / "install-source.json").write_bytes(json.dumps(metadata).encode())
    environment = dict(environment)
    environment.pop("YOMUX_GO_BINARY", None)
    environment.pop("YOLOMUX_GO_BINARY", None)
    assert shutil.which("node", path=environment["PATH"]) is None
    assert shutil.which("npm", path=environment["PATH"]) is None
    binary = root / "yomux-go"
    for operation, success in (("startup-assets", True), ("update-source", False)):
        result = run_native(binary, environment, "--operator", operation, "--root", str(root))
        assert (result.returncode == 0) is success, (operation, result.stdout, result.stderr)

    dependency = root / "static/vendor/marked.min.js"
    original = dependency.read_bytes()
    before = {path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (root / "static").rglob("*") if path.is_file()}
    for mode in ("corrupt", "missing"):
        if mode == "corrupt":
            dependency.write_bytes(b"corrupt dependency")
        else:
            dependency.unlink()
        result = run_native(binary, environment, "--operator", "startup-assets", "--root", str(root))
        assert result.returncode != 0 and "make build" in result.stderr, (result.stdout, result.stderr)
        if mode == "corrupt":
            assert dependency.read_bytes() == b"corrupt dependency"
        else:
            assert not dependency.exists(), "startup acquired a missing dependency"
    dependency.write_bytes(original)
    assert {path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (root / "static").rglob("*") if path.is_file()} == before

    tmux = start_isolated_default_tmux_runtime(monkeypatch, tmp_path)
    environment.update({"TMUX_TMPDIR": str(tmux.socket_dir), "YOMUX_TMUX_SOCKET": "", "YOLOMUX_TMUX_SOCKET": ""})
    runtime_root = Path(environment["HOME"]).parent
    temporary = runtime_root / "tmp"
    temporary.mkdir()
    environment.update({"TMPDIR": str(temporary), "YOMUX_LOG_DIR": str(runtime_root / "logs"),
                        "YOMUX_START_LOCK_DIR": str(runtime_root / "startup-locks")})
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    ready_args = ("--operator", "ready", "--root", str(root), "--http", "--host", "127.0.0.1", "--port", str(port), "--timeout", "20s")
    with (tmp_path / "replacement.log").open("w+") as output:
        def stop_boot_server():
            # The managed replacement owner verifies the detached listener before
            # retiring it; its replacement is our direct, unreaped child.
            lease = Path(environment["YOMUX_ROOT"]) / "instance.lock"
            if not lease.exists():
                return
            pid = json.loads(lease.read_bytes())["pid"]
            identity = process_start_identity(pid)
            if identity is None:
                return
            with FixtureMemberExitBarrier(((pid, identity),)) as retired:
                replacement = subprocess.Popen([str(prefix / "bin/yomux"), "--force", "--http", "--host", "127.0.0.1", "--port", str(port)],
                                               env=environment, stdout=output, stderr=output)
                try:
                    assert retired.wait(20), "managed replacement did not retire the detached boot server"
                    ready = run_native(binary, environment, *ready_args)
                    assert ready.returncode == 0, ready.stderr
                    assert json.loads(lease.read_bytes())["pid"] == replacement.pid
                finally:
                    if replacement.poll() is None:
                        replacement.terminate()
                    replacement.wait(timeout=20)
        try:
            result = subprocess.run([str(root / "boot.sh"), "--no-dev", "--http", "--host", "127.0.0.1", "--port", str(port)],
                                    cwd=root, env=environment, capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, result.stdout + result.stderr
            assert "restarted port" in result.stdout
            for name, content_type in (("yomux.js", "javascript"), ("yomux.css", "text/css")):
                connection = HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    connection.request("GET", "/static/" + name)
                    response = connection.getresponse()
                    assert response.status == 200
                    assert content_type in response.getheader("Content-Type")
                    assert response.read() == (root / "static" / name).read_bytes()
                finally:
                    connection.close()
        finally:
            run_fixture_cleanup_phases("installed offline boot", (
                ("server", stop_boot_server),
                ("tmux", lambda: stop_isolated_tmux_runtime(tmux)),
            ))


def test_install_reads_prepared_source_under_writer_and_refuses_locked_repair(native_launch_environment, tmp_path):
    binary, environment = native_launch_environment
    source = tmp_path / "source"
    source.mkdir()
    for name in ("version.yaml", "boot.sh", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md",
                 "tools/install.sh", "tools/startup_common.sh", "tools/product_environment.sh", "tools/setup-tls.sh"):
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, destination)
    for name in ("assets/runtime", "yomux_lib/yoagent/builtin_skills", "yomux_lib/yoagent/builtin_context"):
        shutil.copytree(REPO / name, source / name)
    contents = {
        "static/yomux.js": b"prepared application",
        "static/yomux.css": b"prepared stylesheet",
        "static/codemirror.js": b"prepared editor bundle",
        "static/vendor/fixture.js": b"verified dependency",
        "static/vendor/pdfjs/web/viewer.html": b"verified PDF viewer",
    }
    for name, body in contents.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    digest = hashlib.sha256(contents["static/vendor/fixture.js"]).hexdigest()
    editor = source / "tools/codemirror-bundle"
    editor.mkdir()
    inputs = {"package.json": b"package", "package-lock.json": b"lock", "codemirror-entry.js": b"custom entry"}
    for name, body in inputs.items():
        (editor / name).write_bytes(body)
    editor_digest = hashlib.sha256(contents["static/codemirror.js"]).hexdigest()
    (source / "assets/runtime/browser-assets.json").write_bytes(json.dumps({"files": {
        "static/vendor/fixture.js": {"sha256": digest, "urls": []},
    }, "bundles": [{"directory": "codemirror", "output": "static/codemirror.js", "sha256": editor_digest,
                    "input_sha256": hashlib.sha256(b"".join(inputs.values())).hexdigest()}]}).encode())
    (source / "assets/runtime/pdfjs-assets.json").write_bytes(json.dumps({"version": "fixture", "archive_sha256": "0" * 64, "files": {
        "web/viewer.html": hashlib.sha256(contents["static/vendor/pdfjs/web/viewer.html"]).hexdigest(),
    }}).encode())
    subprocess.run(["git", "init", "--initial-branch=main", str(source)], env=environment,
                   check=True, capture_output=True)
    environment = dict(environment, XDG_CACHE_HOME=str(tmp_path / "cache"))
    cached = Path(environment["XDG_CACHE_HOME"]) / "yomux/vendor/browser" / digest
    cached.parent.mkdir(parents=True)
    cached.write_bytes(contents["static/vendor/fixture.js"])
    editor_cached = cached.parent / editor_digest
    editor_cached.write_bytes(contents["static/codemirror.js"])
    prefix = tmp_path / "installed"
    command = [str(source / "tools/install.sh"), "--prefix", str(prefix)]
    payload = [source, *(path for path in source.rglob("*") if ".git" not in path.relative_to(source).parts)]
    modes = {path: path.stat().st_mode & 0o777 for path in payload}
    original = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in contents}
    with acquire_worktree_writer(source, purpose="fixture-install-reader", environ={},
                                 slot_dir=source / ".git/yolomux/worktree-writer") as lease:
        try:
            for path, mode in modes.items():
                path.chmod(0o555 if path.is_dir() or mode & 0o111 else 0o444)
            installed = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
            assert installed.returncode == 0, installed.stderr
            assert {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in contents} == original
            assert (prefix / "lib/yomux/static/vendor/fixture.js").read_bytes() == contents["static/vendor/fixture.js"]
            installed_root = prefix / "lib/yomux"
            assert not (installed_root / "tools/codemirror-bundle").exists()
            verified_runtime = subprocess.run([str(installed_root / "yomux-go"), "--operator", "verify-assets", "--root", str(installed_root)],
                                            env=environment, capture_output=True, text=True, timeout=30)
            assert verified_runtime.returncode == 0, verified_runtime.stderr
            before = {path.relative_to(prefix): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode)
                      for path in prefix.rglob("*") if path.is_file()}
            for name, body in inputs.items():
                path = editor / name
                for mutation in ("changed", "missing"):
                    editor.chmod(0o755)
                    path.chmod(0o644)
                    if mutation == "changed":
                        path.write_bytes(b"unreviewed source")
                        path.chmod(0o444)
                    else:
                        path.unlink()
                    editor.chmod(0o555)
                    try:
                        for prepared in ("0", "1"):
                            invalid = subprocess.run(command, env=dict(environment, YOMUX_INSTALL_PREPARED=prepared),
                                                     capture_output=True, text=True, timeout=30)
                            assert invalid.returncode != 0, (name, mutation, prepared, invalid.stdout, invalid.stderr)
                            assert {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in contents} == original
                            assert editor_cached.read_bytes() == contents["static/codemirror.js"]
                            assert {path.relative_to(prefix): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode)
                                    for path in prefix.rglob("*") if path.is_file()} == before
                    finally:
                        editor.chmod(0o755)
                        if path.exists():
                            path.chmod(0o644)
                        path.write_bytes(body)
                        path.chmod(0o444)
                        editor.chmod(0o555)
            damaged = source / "static/vendor/fixture.js"
            damaged.chmod(0o644)
            damaged.write_bytes(b"damaged dependency")
            damaged.chmod(0o444)
            refused = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
            assert refused.returncode != 0
            assert "writer" in refused.stderr
            assert {path.relative_to(prefix): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode)
                    for path in prefix.rglob("*") if path.is_file()} == before
            assert damaged.read_bytes() == b"damaged dependency"
            assert json.loads(lease.record_path.read_bytes())["token"] == lease.token
        finally:
            for path, mode in modes.items():
                path.chmod(mode)


@pytest.mark.parametrize("initial_http", [True, False], ids=["http-to-tls", "tls-to-http"])
def test_authenticated_replacement_can_change_protocol_with_strict_readiness(authenticated_native_launch_environment, tmp_path, initial_http):
    binary, environment = authenticated_native_launch_environment
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
