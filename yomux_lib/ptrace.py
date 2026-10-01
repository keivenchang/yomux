"""Best-effort Linux ptrace opt-in for explicitly dangerous development servers."""

from __future__ import annotations

import ctypes
import sys


# linux/prctl.h: allow a same-user diagnostic process to ptrace this server despite
# yama ptrace_scope=1. This is deliberately called only by the --dang CLI path.
_PR_SET_PTRACER = 0x59616D61
_PR_SET_PTRACER_ANY = ctypes.c_ulong(-1).value

try:
    _LIBC = ctypes.CDLL("libc.so.6", use_errno=True) if sys.platform.startswith("linux") else None
except OSError:
    _LIBC = None


def allow_diagnostic_ptrace() -> bool:
    """Best-effort opt-in for same-user diagnostic attachment; never raises on unsupported hosts."""
    if _LIBC is None:
        return False
    try:
        return _LIBC.prctl(_PR_SET_PTRACER, _PR_SET_PTRACER_ANY, 0, 0, 0) == 0
    except OSError:
        return False
