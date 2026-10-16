#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Probe the actual sandbox; never rely on cached test-result admission."""

import errno
import json
import os
from pathlib import Path
import platform
import socket
import shutil
import sys
import subprocess

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.instance_isolation import INHERITED_CREDENTIAL_ENV_KEYS
from tools.product_environment import require_product_env
from tools.tmux_paths import SHORT_TMPDIR_ENV, short_temp_directory


def admit_tools():
    imports = "import yaml,PIL,anyio,ast_serialize,attrs,certifi,execnet,h11,idna,iniconfig,librt,mypy,mypy_extensions,outcome,packaging,pathspec,pluggy,socks,pygments,pytest,pytest_timeout,xdist,selenium,sniffio,sortedcontainers,trio,trio_websocket,typing_extensions,urllib3,watchfiles,websocket,wsproto"
    subprocess.run([sys.executable, "-I", "-c", imports], check=True, timeout=30)
    for arguments in (("node", "--version"), ("git", "--version"), ("tmux", "-V"), (os.environ["CHROME_BIN"], "--version"),
                      (os.environ["CHROMEDRIVER_BIN"], "--version"), ("claude", "--version"), ("codex", "--version"),
                      ("opencode", "--version"), ("buildifier", "--version"), ("cc", "--version")):
        subprocess.run(arguments, check=True, capture_output=True, timeout=30)
    if platform.system() == "Darwin":
        identity = ("import os,sys;from pathlib import Path;sys.path.insert(0," + repr(str(ROOT)) + ");"
                    "from yomux_lib.infra.host_identity import HostIdentity;HostIdentity.from_system(environ={});"
                    "from yomux_lib.infra.filesystem_preflight import preflight_mutable_roots;"
                    "preflight_mutable_roots(unix_sockets=[Path(os.environ['TMPDIR'])/'admission.sock'],environ={})")
        subprocess.run([sys.executable, "-I", "-B", "-c", identity], check=True, capture_output=True, timeout=10)
        hardware_types = subprocess.check_output(["system_profiler", "-listDataTypes"], text=True, timeout=10)
        require("SPHardwareDataType" in hardware_types, "verified system_profiler cannot inspect hardware")
    directory = Path(short_temp_directory("yomux-shell-admission-"))
    socket_path = directory / "s"
    command = ["tmux", "-f", "/dev/null", "-S", str(socket_path)]
    try:
        subprocess.run([*command, "new-session", "-d", "-s", "admission", "sleep 30"], check=True, capture_output=True, timeout=10)
        shell = subprocess.check_output([*command, "show-options", "-gv", "default-shell"], text=True, timeout=10).strip()
        require(shell == os.environ["SHELL"], "tmux did not select the verified fixture shell")
    finally:
        subprocess.run([*command, "kill-server"], check=True, capture_output=True, timeout=10)
        shutil.rmtree(directory)


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def probe(*, admission=True, tools=True):
    system = platform.system()
    require(system in {"Linux", "Darwin"}, "unsupported native sandbox")
    home = Path(os.environ["HOME"])
    fixture_root = Path(os.environ[SHORT_TMPDIR_ENV] if system == "Darwin" else os.environ["TEST_TMPDIR"])
    require(home.is_relative_to(fixture_root), "HOME is not fixture-owned")
    require(not INHERITED_CREDENTIAL_ENV_KEYS.intersection(os.environ), "ambient credential environment was inherited")
    if system == "Linux":
        for directory in (Path("/tmp"), fixture_root, home):
            # Query the kernel through the admitted GNU stat. A mount inventory
            # can include covered mounts at the same path and misclassify them.
            filesystem = subprocess.check_output(["stat", "--file-system", "--format=%T", "--", str(directory)], text=True, timeout=5).strip()
            require(filesystem == "tmpfs", "fixture storage is not action-private tmpfs: " + str(directory))
    if admission:
        if tools:
            admit_tools()
        runtime_probe = Path(sys.executable).resolve().parent / (".yomux-admission-write-" + str(os.getpid()))
        try:
            descriptor = os.open(runtime_probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as error:
            require(error.errno in {errno.EROFS, errno.EPERM, errno.EACCES}, "runtime write probe did not reach the verified tool directory")
        else:
            os.close(descriptor)
            runtime_probe.unlink()
            raise AssertionError("verified tool runtime is writable")
        credentials = Path(require_product_env("NATIVE_CREDENTIAL_PROBE"))
        try:
            credentials.read_bytes()
        except OSError:
            pass
        else:
            raise AssertionError("host credential file is readable")
        try:
            credentials.write_bytes(b"synthetic admission write probe\n")
        except OSError:
            pass
        else:
            raise AssertionError("host credential file is writable")
    sibling_writes_blocked = system == "Linux"
    if system == "Darwin" and admission:
        sibling = Path(require_product_env("NATIVE_SIBLING_PROBE"))
        alias = fixture_root / "sibling-alias"
        alias.symlink_to(sibling.parent, target_is_directory=True)
        try:
            for candidate in (sibling, alias / sibling.name):
                try:
                    descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except OSError as error:
                    require(error.errno in {errno.EPERM, errno.EACCES}, "sibling write probe failed without a policy denial")
                else:
                    os.close(descriptor)
                    candidate.unlink()
                    raise AssertionError("sibling fixture is writable")
            sibling_writes_blocked = True
        finally:
            alias.unlink()
    external_error = None
    with socket.socket() as external:
        external.settimeout(1)
        try:
            external.connect(("1.1.1.1", 443))
        except OSError as error:
            external_error = error.errno
        else:
            raise AssertionError("external network connection was allowed")
    if system == "Darwin":
        require(external_error in {errno.EPERM, errno.EACCES}, "external networking was not denied by policy")
    else:
        interfaces = Path("/proc/net/dev").read_text().splitlines()[2:]
        require({row.partition(":")[0].strip() for row in interfaces} == {"lo"}, "host network is visible")
        status = dict(row.split(":", 1) for row in Path("/proc/self/status").read_text().splitlines())
        for field in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd"):
            require(int(status[field], 16) == 0, "capabilities are available: " + field)
        require(status["NoNewPrivs"].strip() == "1", "no-new-privileges is missing")
    master, slave = os.openpty()
    try:
        os.write(master, b"private-pty\n")
        require(os.read(slave, 64) == b"private-pty\n", "PTY is unusable")
    finally:
        os.close(master)
        os.close(slave)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with socket.create_connection(listener.getsockname(), timeout=1):
            connection, _address = listener.accept()
            connection.close()
    local_root = Path(short_temp_directory("native-unix-"))
    try:
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(local_root / "s"))
            listener.listen()
            with socket.socket(socket.AF_UNIX) as client:
                client.connect(str(local_root / "s"))
                connection, _address = listener.accept()
                connection.close()
    finally:
        shutil.rmtree(local_root)
    host_reads = host_localhost = host_process = None
    if admission:
        host_reads = Path(require_product_env("NATIVE_HOST_PROBE")).exists()
        host_localhost = False
        try:
            connection = socket.create_connection(("127.0.0.1", int(require_product_env("NATIVE_HOST_PORT"))), timeout=1)
        except OSError:
            pass
        else:
            host_localhost = True
            connection.close()
        host_pid = int(require_product_env("NATIVE_HOST_PID"))
        try:
            os.kill(host_pid, 0)
        except ProcessLookupError:
            host_process = False
        except PermissionError:
            host_process = True
        else:
            host_process = True
        if system == "Linux":
            require(not host_reads, "undeclared host file is visible")
            require(not host_localhost, "host localhost listener is visible")
            require(not host_process, "host process is visible")
        # A detached descendant must be retired by the shared entrypoint, not
        # merely disappear from its parent's ordinary process group.
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True)
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True, env={},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("YOMUX_NATIVE_PROBE " + json.dumps({
        "external_network_blocked": True, "operator_home_blocked": True, "operator_home_writes_blocked": True, "tool_runtime_readonly": True,
        "credential_environment_scrubbed": True,
        "fixture_state_private": True, "fixture_writes_isolated": sibling_writes_blocked, "pty_usable": True,
        "fixture_storage_tmpfs": system == "Linux",
        "undeclared_host_reads_blocked": None if host_reads is None else not host_reads,
        "host_localhost_isolated": None if host_localhost is None else not host_localhost,
        "host_processes_isolated": None if host_process is None else not host_process,
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    probe(admission="--test-action" not in sys.argv[1:], tools="--probe-only" not in sys.argv[1:])
