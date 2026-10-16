#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Retire native test descendants by a private scope and immutable birth proof."""

import ctypes
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from tests.browser_helpers.webdriver_lease import DriverGeneration, LIBPROC, process_start_key
from tools.instance_isolation import TEST_PROCESS_SCOPE_ENV

SCOPE_ENV = TEST_PROCESS_SCOPE_ENV


TEST_OUTPUT_FILE_ENV_KEYS = (
    "XML_OUTPUT_FILE", "TEST_SHARD_STATUS_FILE", "TEST_PREMATURE_EXIT_FILE", "TEST_INFRASTRUCTURE_FAILURE_FILE",
    "TEST_WARNINGS_OUTPUT_FILE", "TEST_UNUSED_RUNFILES_LOG_FILE", "TEST_LOGSPLITTER_OUTPUT_FILE",
)


TEST_WRITABLE_DIRECTORY_ENV_KEYS = (
    "TEST_TMPDIR", "TEST_UNDECLARED_OUTPUTS_DIR", "TEST_UNDECLARED_OUTPUTS_ANNOTATIONS_DIR",
)


def action_writable_directories(environment):
    return {Path(environment[key]).resolve() for key in TEST_WRITABLE_DIRECTORY_ENV_KEYS if environment.get(key)}


def native_execution_scope(system):
    if system == "Darwin":
        return {"sandbox_scope": "test-process-tree", "setup_os_sandboxed": False,
                "non_test_actions_os_sandboxed": False}
    if system == "Linux":
        return {"sandbox_scope": "invocation", "setup_os_sandboxed": True,
                "non_test_actions_os_sandboxed": True}
    raise ValueError("unsupported native sandbox platform")



class DarwinCoalitionInfo(ctypes.Structure):
    _fields_ = [("identity", ctypes.c_uint64), ("kind", ctypes.c_uint32), ("tasks", ctypes.c_uint32)]


def resource_coalition(pid):
    if LIBPROC is None:
        raise ValueError("Darwin resource coalitions are unavailable")
    information = (ctypes.c_uint64 * 5)()
    ctypes.set_errno(0)
    copied = LIBPROC.proc_pidinfo(pid, 20, 0, ctypes.byref(information), ctypes.sizeof(information))
    if copied != ctypes.sizeof(information):
        if ctypes.get_errno() in {errno.ESRCH, errno.ENOENT, errno.EPERM, errno.EACCES}:
            return None
        raise OSError(ctypes.get_errno(), "resource coalition could not be inspected")
    return information[0]


def resource_coalition_task_count(identity):
    # The kernel counts tasks while holding the coalition lock. An empty PID
    # scan alone can miss a child forked while its original parent exits.
    size = LIBPROC.proc_listcoalitions(2, 0, None, 0)
    if size <= 0:
        raise OSError(ctypes.get_errno(), "resource coalition inventory is unavailable")
    for _attempt in range(4):
        entries = (DarwinCoalitionInfo * (size // ctypes.sizeof(DarwinCoalitionInfo) + 128))()
        copied = LIBPROC.proc_listcoalitions(2, 0, entries, ctypes.sizeof(entries))
        if copied <= 0:
            raise OSError(ctypes.get_errno(), "resource coalition inventory could not be read")
        if copied >= ctypes.sizeof(entries):
            size = copied
            continue
        for entry in entries[:copied // ctypes.sizeof(DarwinCoalitionInfo)]:
            if entry.identity == identity:
                return entry.tasks
        return 0
    raise ValueError("resource coalition inventory changed beyond its bounded capacity")


class DarwinResourceCoalitionScope:
    """Own a dedicated launchd job's descendants regardless of their environment."""

    def __init__(self, identity, *, exclude_self=False):
        if not isinstance(identity, int) or identity <= 0:
            raise ValueError("a measured Darwin resource coalition is required")
        own = resource_coalition(os.getpid())
        if exclude_self and own != identity:
            raise ValueError("worker is outside its private resource coalition")
        if not exclude_self and own == identity:
            raise ValueError("controller must be outside the execution coalition")
        self.identity = identity
        self.fixture_base = None
        self.excluded = DriverGeneration(os.getpid(), process_start_key(os.getpid())) if exclude_self else None

    def members(self):
        count = LIBPROC.proc_listallpids(None, 0)
        if count <= 0:
            raise OSError(ctypes.get_errno(), "native process inventory is unavailable")
        for _attempt in range(4):
            pids = (ctypes.c_int * (count + 128))()
            copied = LIBPROC.proc_listallpids(pids, ctypes.sizeof(pids))
            if copied <= 0:
                raise OSError(ctypes.get_errno(), "native process inventory could not be read")
            if copied >= len(pids):
                count = copied
                continue
            proofs = []
            for pid in pids[:copied]:
                if pid <= 0 or resource_coalition(pid) != self.identity:
                    continue
                key = process_start_key(pid)
                if key is not None:
                    proof = DriverGeneration(pid, key)
                    if proof != self.excluded:
                        proofs.append(proof)
            return proofs
        raise ValueError("native process inventory changed beyond its bounded capacity")

    def retired(self):
        expected = 0
        if self.excluded is not None:
            if (process_start_key(self.excluded.pid) != self.excluded.start_key
                    or resource_coalition(self.excluded.pid) != self.identity):
                raise ValueError("execution worker birth or coalition changed")
            expected = 1
        return resource_coalition_task_count(self.identity) == expected

    def retire(self, deadline_seconds=2):
        event = threading.Event()
        for number in (signal.SIGTERM, signal.SIGKILL):
            deadline = time.monotonic() + deadline_seconds
            while True:
                for proof in self.members():
                    if (process_start_key(proof.pid) == proof.start_key
                            and resource_coalition(proof.pid) == self.identity):
                        try:
                            os.kill(proof.pid, number)
                        except ProcessLookupError:
                            pass
                if self.retired():
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                event.wait(min(0.05, remaining))
        return self.retired()


class NativeProcessScope:
    def __init__(self, *, private_supervisor=False):
        self.identity = uuid.uuid4().hex
        self.generations = {}
        self.descendants = {}
        self.private_supervisor = private_supervisor
        if private_supervisor:
            if sys.platform != "linux":
                raise ValueError("a private child subreaper requires Linux")
            library = ctypes.CDLL(None, use_errno=True)
            if library.prctl(36, 1, 0, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER failed")
            self.supervisor = DriverGeneration(os.getpid(), process_start_key(os.getpid()))
            if self.supervisor.start_key is None:
                raise ValueError("private supervisor has no immutable birth proof")

    def linux_process(self, pid):
        try:
            content = (Path("/proc") / str(pid) / "stat").read_text()
        except (FileNotFoundError, ProcessLookupError):
            return None
        fields = content[content.rfind(")") + 1:].split()
        if len(fields) < 20:
            raise ValueError("native child process stat is incomplete")
        return fields[0], int(fields[1]), fields[19]

    def children_of(self, parent):
        before = self.linux_process(parent.pid)
        if before is None or before[2] != parent.start_key:
            return set()
        children = set()
        try:
            threads = list((Path("/proc") / str(parent.pid) / "task").iterdir())
        except FileNotFoundError:
            return set()
        for thread in threads:
            try:
                children.update(map(int, (thread / "children").read_text().split()))
            except FileNotFoundError:
                continue
        after = self.linux_process(parent.pid)
        if after is None or after[2] != parent.start_key:
            return set()
        return children

    def private_children(self):
        if process_start_key(self.supervisor.pid) != self.supervisor.start_key:
            raise ValueError("private supervisor birth changed")
        return self.children_of(self.supervisor)

    def reap_adopted(self):
        if not self.private_supervisor:
            return
        for pid in self.private_children():
            record = self.linux_process(pid)
            if record is None or record[0] != "Z":
                continue
            proof = self.generations.get(pid)
            if proof is not None and proof.start_key == record[2]:
                # Popen owns the direct command's wait status. Reap only its
                # kernel-adopted descendants, never consume that direct status.
                continue
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, ProcessLookupError):
                pass

    def private_retired(self):
        self.reap_adopted()
        before = self.private_children()
        for pid in before:
            record = self.linux_process(pid)
            proof = self.generations.get(pid)
            if record is None or record[0] != "Z" or proof is None or proof.start_key != record[2]:
                return False
        # Adoption is complete before a parent becomes a zombie. A stable
        # children list containing only managed zombies cannot conceal a live
        # orphan produced between the ancestry scan and the wait-state check.
        return before == self.private_children()

    def track(self, process):
        record = self.linux_process(process.pid) if self.private_supervisor else None
        key = record[2] if record is not None else process_start_key(process.pid)
        if key is not None:
            self.generations[process.pid] = DriverGeneration(process.pid, key)
            if self.private_supervisor:
                self.descendants[process.pid] = self.generations[process.pid]

    @contextmanager
    def reaping(self):
        if not self.private_supervisor:
            yield
            return
        previous = signal.getsignal(signal.SIGCHLD)
        signal.signal(signal.SIGCHLD, lambda _number, _frame: self.reap_adopted())
        try:
            # Children can exit between spawning/registering the direct wait
            # owner and installing SIGCHLD. Drain that adoption window too.
            self.reap_adopted()
            yield
        finally:
            signal.signal(signal.SIGCHLD, previous)

    def run(self, command, *, capture_output=False, timeout=None, check=False, **kwargs):
        """Keep direct wait status while reaping daemonized adopted children."""
        if capture_output:
            kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with subprocess.Popen(command, **kwargs) as process:
            self.track(process)
            with self.reaping():
                try:
                    stdout, stderr = process.communicate(timeout=timeout)
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
            result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            if check:
                result.check_returncode()
            return result

    def environment(self, values):
        return {**values, SCOPE_ENV: self.identity}

    def members(self):
        members = []
        members.extend(proof for proof in self.generations.values()
                       if process_start_key(proof.pid) == proof.start_key)
        if self.private_supervisor:
            parents = [self.supervisor, *self.descendants.values()]
            visited = set()
            while parents:
                parent = parents.pop()
                generation = (parent.pid, parent.start_key)
                if generation in visited:
                    continue
                visited.add(generation)
                for pid in self.children_of(parent):
                    child = self.linux_process(pid)
                    after = self.linux_process(parent.pid)
                    if (child is None or child[1] != parent.pid or after is None
                            or after[2] != parent.start_key):
                        continue
                    proof = DriverGeneration(pid, child[2])
                    self.descendants[pid] = proof
                    parents.append(proof)
            members.extend(proof for proof in self.descendants.values()
                           if process_start_key(proof.pid) == proof.start_key)
            return list({proof.pid: proof for proof in members}.values())
        if Path("/proc/self/environ").is_file():
            marker = (SCOPE_ENV + "=" + self.identity).encode()
            for directory in Path("/proc").iterdir():
                if not directory.name.isdigit():
                    continue
                try:
                    environment = (directory / "environ").read_bytes().split(b"\0")
                except (FileNotFoundError, PermissionError, ProcessLookupError):
                    continue
                if marker in environment:
                    pid = int(directory.name)
                    key = process_start_key(pid)
                    if key is not None:
                        members.append(DriverGeneration(pid, key))
        else:
            inspection_environment = {key: value for key, value in os.environ.items() if key != SCOPE_ENV}
            completed = subprocess.run(["ps", "-Eww", "-axo", "pid=,command="],
                env=inspection_environment, capture_output=True, check=True)
            # Process environments are byte strings and may contain non-UTF-8 values.
            marker = (SCOPE_ENV + "=" + self.identity).encode()
            for row in completed.stdout.splitlines():
                fields = row.split()
                if fields and marker in fields:
                    pid = int(fields[0])
                    if pid == os.getpid():
                        continue
                    key = process_start_key(pid)
                    if key is not None:
                        members.append(DriverGeneration(pid, key))
        return list({proof.pid: proof for proof in members}.values())

    def retire(self, deadline_seconds=2):
        proofs = self.members()
        for generation in proofs:
            if process_start_key(generation.pid) == generation.start_key:
                try:
                    os.kill(generation.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + deadline_seconds
        event = threading.Event()
        while proofs and time.monotonic() < deadline:
            self.reap_adopted()
            proofs = [proof for proof in proofs if process_start_key(proof.pid) == proof.start_key]
            if proofs:
                event.wait(min(0.05, max(0, deadline - time.monotonic())))
        for generation in proofs:
            if process_start_key(generation.pid) == generation.start_key:
                try:
                    os.kill(generation.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            self.reap_adopted()
            if not self.members() and (not self.private_supervisor or self.private_retired()):
                return True
            if self.private_supervisor:
                for proof in self.members():
                    if process_start_key(proof.pid) == proof.start_key:
                        try:
                            os.kill(proof.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
            event.wait(min(0.05, max(0, deadline - time.monotonic())))
        return not self.members() and (not self.private_supervisor or self.private_retired())
