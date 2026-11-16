# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Pipe-held children let lifecycle tests record identity before exit."""

import os
import time

from yomux_lib.infra.host_identity import process_state


def fork_blocking_child():
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(write_fd)
        try:
            os.read(read_fd, 1)
        finally:
            os._exit(0)
    os.close(read_fd)
    return pid, write_fd


def await_zombie(pid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process_state(pid) == "Z":
            return
        time.sleep(0.005)
    raise AssertionError(f"child {pid} did not reach state Z")
