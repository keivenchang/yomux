#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Launch a native invocation in an owned launchd resource coalition."""

import argparse
import ctypes
import json
import os
from pathlib import Path
import plistlib
import re
import select
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.native_process_scope import DarwinResourceCoalitionScope, resource_coalition
from tests.browser_helpers.webdriver_lease import process_start_key
from tools import bazel_execution_contract as EXECUTION_CONTRACT
from tools.tmux_paths import cleanup_native_fixture_roots

LIBSYSTEM = ctypes.CDLL(None, use_errno=True)


def peer_uid(connection):
    uid = ctypes.c_uint()
    gid = ctypes.c_uint()
    if LIBSYSTEM.getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise OSError(ctypes.get_errno(), "native worker peer credentials could not be read")
    return uid.value


def cleanup_invocation(scope, phase):
    retired = scope.retire()
    print("YOMUX_NATIVE_INVOCATION_CLEANUP " + json.dumps({
        "phase": phase, "boundary": "darwin-resource-coalition", "complete": True,
        "retired": retired, "coalition": scope.identity,
    }, sort_keys=True), flush=True)
    if not retired:
        raise ValueError("native invocation resource coalition still has owned processes: " + phase)
    if scope.fixture_base is not None:
        cleanup_native_fixture_roots(scope.fixture_base, scope.identity)


def private_request(path):
    metadata = path.lstat()
    directory = path.parent.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600 or not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700):
        raise ValueError("native worker protocol must be user-owned and private")
    return json.loads(path.read_bytes())


def send(connection, value):
    connection.sendall(json.dumps(value, separators=(",", ":")).encode() + b"\n")


def owned_job_pid(service):
    completed = subprocess.run(["/bin/launchctl", "print", service], capture_output=True, text=True, check=True)
    pids = re.findall(r"^\s*pid = (\d+)\s*$", completed.stdout, re.MULTILINE)
    if len(pids) != 1:
        raise ValueError("owned launchd job has no single running worker")
    return int(pids[0])


def worker(request_path):
    request = private_request(request_path)
    controller = request["controller"]
    if process_start_key(controller["pid"]) != controller["birth"]:
        raise ValueError("native launch controller birth changed")
    identity = resource_coalition(os.getpid())
    if identity is None or identity == resource_coalition(controller["pid"]):
        raise ValueError("native worker lacks an independent launchd resource coalition")
    scope = DarwinResourceCoalitionScope(identity, exclude_self=True)
    scope.fixture_base = Path(request["short_tmpdir"]) if "short_tmpdir" in request else None
    with socket.socket(socket.AF_UNIX) as connection:
        connection.connect(request["socket"])
        if peer_uid(connection) != os.getuid():
            raise ValueError("native launch controller UID differs")
        send(connection, {"ready": True, "token": request["token"], "pid": os.getpid(),
                          "birth": process_start_key(os.getpid()), "coalition": identity})
        with connection.makefile("rb") as protocol:
            acknowledgement = json.loads(protocol.readline(1024))
        if acknowledgement != {"accepted": request["token"]}:
            raise ValueError("native worker was not accepted by its launch controller")
        status = 78
        try:
            status = EXECUTION_CONTRACT.bazel_native.run_native(request["arguments"], EXECUTION_CONTRACT,
                                                               invocation_scope=scope)
        except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            detail = EXECUTION_CONTRACT.redact_local_service_text(error)
            EXECUTION_CONTRACT.execution_event(effective="qualified-native", admission="failed",
                coverage_complete=False, failure_phase="admission", detail=detail)
            print("QUALIFIED NATIVE EXECUTION REFUSED: " + detail, file=sys.stderr, flush=True)
        finally:
            cleanup_invocation(scope, "worker-finally")
            send(connection, {"result": status})
    return status


def drain_logs(streams):
    for stream, descriptor in streams:
        data = stream.read()
        while data:
            written = os.write(descriptor, data)
            data = data[written:]


def watch_log(queue, stream):
    # Clear delivered write notifications so drained logs do not keep the controller awake.
    queue.control([select.kevent(stream.fileno(), filter=select.KQ_FILTER_VNODE,
        flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
        fflags=select.KQ_NOTE_WRITE)], 0, 0)


def run_native(arguments, contract, bundle, *, fixture_base=None, source_root=None):
    worker_path = Path(__file__).absolute() if source_root is None else source_root / "tools/native_darwin_launcher.py"
    source_root = source_root or contract.ROOT
    label = "org.yomux.native." + uuid.uuid4().hex
    domain = "gui/" + str(os.getuid())
    cancellation = []

    def cancelled(number, _frame):
        cancellation.append(number)

    handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    for number in handlers:
        signal.signal(number, cancelled)
    bootstrapped = False
    scope = None
    connection = None
    queue = select.kqueue()
    streams = []
    try:
        with tempfile.TemporaryDirectory(prefix="yd-", dir="/tmp") as temporary:
            root = Path(temporary)
            endpoint = root / "control.sock"
            request_path = root / "request.json"
            job_path = root / "job.plist"
            for name, descriptor in (("stdout", 1), ("stderr", 2)):
                path = root / name
                with path.open("xb"):
                    pass
                path.chmod(0o600)
                stream = path.open("rb")
                streams.append((stream, descriptor))
                watch_log(queue, stream)
            token = uuid.uuid4().hex
            request = {"token": token, "socket": str(endpoint), "controller": {
                "pid": os.getpid(), "birth": process_start_key(os.getpid())},
                "arguments": ["--bundle", str(bundle.root), *arguments]}
            if fixture_base is not None:
                request["short_tmpdir"] = str(fixture_base)
            request_path.write_bytes(json.dumps(request).encode())
            request_path.chmod(0o600)
            environment = {"PATH": str(bundle.root / "bin"), "HOME": str(root),
                           "LANG": "C.UTF-8", "TZ": "America/Los_Angeles"}
            for name in ("TMPDIR", "TMP", "TEMP", "CARGO_TARGET_DIR", "YOMUX_NATIVE_CACHE_ROOT"):
                if name in os.environ:
                    environment[name] = os.environ[name]
            job = {"Label": label, "ProgramArguments": [str(bundle.executables["python3"]), "-I", "-B", str(worker_path),
                    "--worker", str(request_path)], "RunAtLoad": True, "WorkingDirectory": str(source_root),
                   "StandardOutPath": str(root / "stdout"), "StandardErrorPath": str(root / "stderr"),
                   "EnvironmentVariables": environment}
            job_path.write_bytes(plistlib.dumps(job))
            job_path.chmod(0o600)
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(endpoint))
                listener.listen(1)
                listener.setblocking(False)
                queue.control([select.kevent(listener.fileno(), filter=select.KQ_FILTER_READ,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE)], 0, 0)
                try:
                    result = subprocess.run(["/bin/launchctl", "bootstrap", domain, str(job_path)],
                                            text=True, capture_output=True)
                    if result.returncode:
                        raise ValueError("private launchd job could not be bootstrapped: " + result.stderr.strip())
                    bootstrapped = True
                    pending = b""
                    status = None
                    startup_deadline = time.monotonic() + 10
                    while True:
                        if cancellation:
                            raise SystemExit(128 + cancellation[0])
                        if scope is None and time.monotonic() >= startup_deadline:
                            raise ValueError("native launch worker did not establish its private ownership protocol")
                        events = queue.control([], 8, 0.1)
                        drain_logs(streams)
                        for event in events:
                            if event.filter != select.KQ_FILTER_READ:
                                continue
                            if event.ident == listener.fileno():
                                if connection is not None:
                                    raise ValueError("native worker protocol received another peer")
                                connection, _address = listener.accept()
                                if peer_uid(connection) != os.getuid():
                                    raise ValueError("native worker protocol UID differs")
                                connection.setblocking(False)
                                queue.control([select.kevent(connection.fileno(), filter=select.KQ_FILTER_READ,
                                    flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE)], 0, 0)
                                continue
                            data = connection.recv(65536)
                            if not data:
                                if status is None:
                                    raise ValueError("native worker exited without an invocation result")
                                return status
                            pending += data
                            if len(pending) > 65536:
                                raise ValueError("native worker protocol exceeded its message limit")
                            while b"\n" in pending:
                                line, pending = pending.split(b"\n", 1)
                                message = json.loads(line)
                                if scope is None:
                                    peer_pid = int.from_bytes(connection.getsockopt(0, 2, 4), sys.byteorder)
                                    if (message.get("ready") is not True or message.get("token") != token
                                            or message.get("pid") != peer_pid
                                            or owned_job_pid(domain + "/" + label) != peer_pid
                                            or process_start_key(peer_pid) != message.get("birth")
                                            or resource_coalition(peer_pid) != message.get("coalition")):
                                        raise ValueError("native launch worker ownership could not be established")
                                    scope = DarwinResourceCoalitionScope(message["coalition"])
                                    scope.fixture_base = fixture_base
                                    send(connection, {"accepted": token})
                                elif set(message) == {"result"} and isinstance(message["result"], int):
                                    status = message["result"]
                                else:
                                    raise ValueError("unexpected native worker protocol message")
                finally:
                    for number in handlers:
                        signal.signal(number, signal.SIG_IGN)
                    try:
                        if scope is not None:
                            cleanup_invocation(scope, "controller-finally")
                    finally:
                        try:
                            if bootstrapped:
                                result = subprocess.run(["/bin/launchctl", "bootout", domain + "/" + label],
                                                        text=True, capture_output=True)
                                if result.returncode:
                                    raise ValueError("owned launchd job could not be removed: " + result.stderr.strip())
                        finally:
                            drain_logs(streams)
    finally:
        if connection is not None:
            connection.close()
        for stream, _descriptor in streams:
            stream.close()
        queue.close()
        for number, handler in handlers.items():
            signal.signal(number, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    return worker(parser.parse_args().worker)


if __name__ == "__main__":
    sys.exit(main())
