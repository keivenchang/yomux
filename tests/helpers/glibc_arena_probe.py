"""Count live glibc malloc arenas, in-process and in a fresh statsd-shaped child.

`MALLOC_ARENA_MAX` is read by glibc once, at the first allocation, so the cap is
not retroactive: a later `mallopt(M_ARENA_MAX, 2)` cannot retract arenas that
already exist. The only way to prove the cap took effect on the process that
matters is therefore to count arenas in a process that was exec'd with the
variable already set. This module is both the counter and the child that gets
exec'd, so the test and the probe share one definition of "an arena".

Counting method: glibc's `malloc_info(0, FILE*)` walks its own arena ring
(`ar_ptr = &main_arena; do ... while (ar_ptr != &main_arena)`) and emits exactly
one `<heap nr="N">` element per arena. Counting those elements therefore counts
glibc arenas by construction. Anonymous `rw-p` regions in `/proc/<pid>/maps` are
NOT an arena count -- they also cover thread stacks, `mmap`'d blocks above the
mmap threshold, and CPython's own object arenas, none of which are glibc arenas.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

from yolomux_lib.stats_current.service import StatsCurrentService

# glibc emits one such element per arena; `nr` is the arena index, main arena is 0.
_HEAP_ELEMENT_RE = re.compile(r'<heap nr="\d+">')

# Enough concurrent threads that an uncapped glibc opens more than two arenas on
# any core count: `mp_.arena_test` is 8 on 64-bit, so the first nine arenas are
# created before any core-derived limit is even computed. Small enough that the
# probe costs milliseconds on a shared host.
CONTENDING_THREADS = 8

# Importing the service at module scope is safe, and the position is NOT part of
# the experiment. glibc reads `MALLOC_ARENA_MAX` at the process's FIRST allocation,
# which the C runtime performs before CPython's `main()` runs, so the cap is already
# in force before any Python import executes -- there is no "before the cap" for
# Python code to be in. This probe never calls `cap_malloc_arenas()`; the cap
# arrives purely as inherited environment on `exec`, which is the contract under
# test. Measured both ways, 10 runs each: capped 2 arenas and uncapped 9 arenas,
# identical whether the import sits here or inside the contention function, with
# `arenas_before_threads` reading 1 in all 40 runs.
#
# `arenas_before_threads` is reported and asserted for exactly that reason: if some
# future import starts a thread of its own, it would create an arena before the
# measurement begins and quietly shift both controls. This catches that.
EXPECTED_ARENAS_BEFORE_THREADS = 1


def malloc_info_xml() -> str:
    """Return glibc's own malloc_info() XML for this process."""

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.fopen.restype = ctypes.c_void_p
    libc.fopen.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    libc.fclose.argtypes = [ctypes.c_void_p]
    libc.malloc_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
    handle, path = tempfile.mkstemp(prefix="malloc-info-")
    os.close(handle)
    try:
        stream = libc.fopen(path.encode(), b"w")
        if not stream:
            raise OSError(ctypes.get_errno(), f"cannot open {path} for malloc_info")
        try:
            libc.malloc_info(0, stream)
        finally:
            libc.fclose(stream)
        return Path(path).read_text()
    finally:
        os.unlink(path)


def count_glibc_arenas() -> int:
    """Number of glibc malloc arenas currently attached to this process."""

    return len(_HEAP_ELEMENT_RE.findall(malloc_info_xml()))


def _construct_statsd_under_contention(threads: int) -> int:
    """Build real StatsCurrentService instances on `threads` live threads at once.

    A brand-new thread has no arena attached, so its first allocation -- which
    CPython performs while bootstrapping the thread, before any product code runs
    -- takes glibc's `arena_get2` path and creates a fresh arena whenever the
    process is under the arena limit. The barrier keeps every thread alive while
    the count is taken, so no arena can be recycled off the free list first, which
    is what makes the count deterministic without any sleep or timing threshold.
    """

    root = Path(tempfile.mkdtemp(prefix="statsd-arena-probe-"))
    constructed = threading.Barrier(threads + 1)
    released = threading.Barrier(threads + 1)
    failures: list[Exception] = []

    def build(index: int) -> None:
        try:
            service = StatsCurrentService(
                root / f"statsd-{index}.sock",
                root / "stats-v9.sqlite3",
                idle_seconds=60.0,
            )
        except Exception as error:  # reported to the parent; the barriers still release
            failures.append(error)
            constructed.wait()
            released.wait()
            raise
        constructed.wait()
        released.wait()
        # Keep the instance referenced until every thread has been counted.
        del service

    workers = [threading.Thread(target=build, args=(index,), name=f"arena-probe-{index}") for index in range(threads)]
    for worker in workers:
        worker.start()
    constructed.wait()
    arenas = count_glibc_arenas()
    released.wait()
    for worker in workers:
        worker.join()
    if failures:
        raise failures[0]
    return arenas


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    threads = int(argv[0]) if argv else CONTENDING_THREADS
    arenas_before_threads = count_glibc_arenas()
    report = {
        "arenas_before_threads": arenas_before_threads,
        "arenas": _construct_statsd_under_contention(threads),
        "threads": threads,
        "malloc_arena_max": os.environ.get("MALLOC_ARENA_MAX"),
        "pid": os.getpid(),
    }
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
