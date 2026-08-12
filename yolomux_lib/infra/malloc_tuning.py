# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Cap glibc malloc arenas so a hand-launched server cannot fragment to gigabytes.

`MALLOC_ARENA_MAX` is read by glibc once, effectively at the first allocation, so
setting `os.environ` from Python after startup is too late for THIS process. Two
launch paths existed: `boot.sh`/`tools/startup_common.sh` export
`MALLOC_ARENA_MAX=2` before exec, but a direct `python yolomux.py` invocation
inherited no cap and glibc then opened up to `8 * ncpu` arenas, each retaining its
peak transient allocation. On a 32-CPU box that reached ~1.8 GB RSS with a stable,
non-leaking working set purely from arena fragmentation. `mallopt()` sets the
arena cap at runtime, fixing the current process regardless of launcher; we also
publish the env var so spawned children and the self-restart passthrough inherit
it.
"""
from __future__ import annotations

import ctypes
import os
import sys

# glibc <malloc.h>: `#define M_ARENA_MAX -8`. mallopt() returns 1 on success.
_M_ARENA_MAX = -8
DEFAULT_ARENA_MAX = 2


def cap_malloc_arenas(arena_max: int = DEFAULT_ARENA_MAX) -> bool:
    """Best-effort cap of glibc malloc arenas for this process and its children.

    An explicit `MALLOC_ARENA_MAX` already in the environment (for example set by
    `boot.sh`) wins over the argument, so an operator can still tune it. Returns
    True only when `mallopt` reported success. Non-glibc platforms (macOS, musl)
    have no such tunable, so this degrades to publishing the env var and returning
    False rather than failing server startup over a performance hint.
    """
    env_value = os.environ.get("MALLOC_ARENA_MAX")
    if env_value:
        try:
            arena_max = int(env_value)
        except ValueError:
            arena_max = DEFAULT_ARENA_MAX
    # Publish for children we spawn and for SELF_RESTART_ENV_KEYS passthrough.
    os.environ["MALLOC_ARENA_MAX"] = str(arena_max)
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        # No glibc present; the published env var is the only remaining lever.
        return False
    return libc.mallopt(_M_ARENA_MAX, int(arena_max)) == 1
