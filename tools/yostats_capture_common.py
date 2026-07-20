"""Shared validation and Linux CPU accounting for YO!stats capture tools."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def process_cpu_seconds(pid: int) -> float | None:
    if pid <= 0:
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        _head, separator, tail = raw.rpartition(")")
        if not separator:
            return None
        fields = tail.split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    except (IndexError, OSError, ValueError):
        return None
