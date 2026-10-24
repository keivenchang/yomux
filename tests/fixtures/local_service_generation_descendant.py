# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Spawn one inherited-environment descendant, then exit its session leader."""

from __future__ import annotations

import argparse
import multiprocessing
import os
from pathlib import Path
import signal
import time


def retained_grandchild() -> None:
    signal.pause()


def retained_descendant(connection) -> None:
    grandchild = multiprocessing.get_context("spawn").Process(target=retained_grandchild)
    grandchild.start()
    connection.send((os.getpid(), grandchild.pid))
    connection.close()
    signal.pause()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--socket")
    parser.add_argument("--idle-seconds")
    parser.add_argument("--child-pid-file", required=True)
    parser.add_argument("--spawn-on-term", action="store_true")
    parser.add_argument("--ready-fd", type=int, required=True)
    args = parser.parse_args()
    if not args.serve:
        parser.error("--serve is required")
    if args.spawn_on_term:
        def spawn_descendant_then_exit(_signal_number, _frame) -> None:
            descendant_pid = os.fork()
            if descendant_pid == 0:
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                signal.pause()
                os._exit(0)
            Path(args.child_pid_file).write_text(str(descendant_pid), encoding="utf-8")
            os._exit(0)

        signal.signal(signal.SIGTERM, spawn_descendant_then_exit)
        os.write(args.ready_fd, b"1")
        os.close(args.ready_fd)
        while True:
            time.sleep(60)

    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=retained_descendant,
        args=(child_connection,),
    )
    process.start()
    child_connection.close()
    descendant_pids = parent_connection.recv()
    Path(args.child_pid_file).write_text(
        ",".join(str(pid) for pid in descendant_pids),
        encoding="utf-8",
    )
    parent_connection.close()
    os.write(args.ready_fd, b"1")
    os.close(args.ready_fd)
    os._exit(0)


if __name__ == "__main__":
    main()
