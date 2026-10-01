"""Shared validation and Linux CPU accounting for YO!stats capture tools."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess


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


def process_cpu_seconds(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
    runner=subprocess.run,
) -> float | None:
    if pid <= 0:
        return None
    try:
        raw = (proc_root / str(pid) / "stat").read_text(encoding="utf-8")
        _head, separator, tail = raw.rpartition(")")
        if not separator:
            return None
        fields = tail.split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    except (IndexError, OSError, ValueError):
        try:
            completed = runner(
                ["ps", "-p", str(pid), "-o", "time="],
                capture_output=True,
                text=True,
                check=False,
                timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        raw = completed.stdout.strip()
        days = 0
        if "-" in raw:
            day_text, _separator, raw = raw.partition("-")
            try:
                days = int(day_text)
            except ValueError:
                return None
        try:
            parts = [float(part) for part in raw.split(":")]
        except ValueError:
            return None
        if completed.returncode != 0 or not parts:
            return None
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60.0 + part
        return seconds + days * 86400.0
