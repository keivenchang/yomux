# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Host-only samplers owned by the persistent stats daemon."""

from __future__ import annotations

import ctypes
import json
import os
import plistlib
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..local_services.registry import read_process_cpu_seconds_and_rss


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _linux_system_times() -> tuple[float, float] | None:
    try:
        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
        values = [float(item) for item in fields[1:]]
    except (IndexError, OSError, ValueError):
        return None
    total = sum(values)
    idle = values[3] + (values[4] if len(values) > 4 else 0.0)
    return (total, total - idle) if total > 0 else None


def _linux_process_ticks(pid: int) -> tuple[float, int] | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()
        ticks = float(fields[11]) + float(fields[12])
        rss = int(fields[21]) * int(os.sysconf("SC_PAGE_SIZE"))
    except (IndexError, OSError, ValueError):
        return None
    return ticks, max(0, rss)


def _darwin_system_times() -> tuple[float, float] | None:
    """Read aggregate CPU ticks through Mach without spawning a process."""

    if sys.platform != "darwin":
        return None
    try:
        libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        libsystem.mach_host_self.restype = ctypes.c_uint32
        libsystem.mach_task_self.restype = ctypes.c_uint32
        libsystem.host_processor_info.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.POINTER(ctypes.c_int)), ctypes.POINTER(ctypes.c_uint32)]
        libsystem.host_processor_info.restype = ctypes.c_int
        libsystem.vm_deallocate.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint64]
        libsystem.vm_deallocate.restype = ctypes.c_int
        processor_count = ctypes.c_uint32()
        info = ctypes.POINTER(ctypes.c_int)()
        info_count = ctypes.c_uint32()
        if libsystem.host_processor_info(libsystem.mach_host_self(), 2, ctypes.byref(processor_count), ctypes.byref(info), ctypes.byref(info_count)) != 0:
            return None
        try:
            values = [int(info[index]) for index in range(int(info_count.value))]
            total = float(sum(values))
            idle = float(sum(values[index] for index in range(2, len(values), 4)))
            return (total, total - idle) if total > 0 else None
        finally:
            address = ctypes.cast(info, ctypes.c_void_p).value
            if address:
                libsystem.vm_deallocate(libsystem.mach_task_self(), address, ctypes.sizeof(ctypes.c_int) * int(info_count.value))
    except (AttributeError, OSError):
        return None


def _system_times() -> tuple[float, float] | None:
    return _darwin_system_times() if sys.platform == "darwin" else _linux_system_times()


def _process_cpu_seconds_and_rss(pid: int) -> tuple[float, int] | None:
    if sys.platform == "darwin":
        return read_process_cpu_seconds_and_rss(pid)
    reading = _linux_process_ticks(pid)
    if reading is None:
        return None
    ticks, rss = reading
    return ticks / float(os.sysconf("SC_CLK_TCK")), rss


# The ONE cadence/staleness policy for the web process's own CPU/memory sample.
#
# `CpuSampler` below produces that sample on HOST_CPU_CADENCE_SECONDS and statsd pushes it to the
# web process, which is its only writer. A reader may treat a pushed sample as describing NOW for
# HOST_CPU_SAMPLE_STALE_CADENCES cadences; past that the number is no longer a measurement of the
# present and must be published as absent rather than frozen at its last value.
#
# This lives beside the sampler because the sampler sets the cadence. It used to be two
# independent literals -- `HOST_CPU_CADENCE_SECONDS = 1.0` in stats_current/service.py and a bare
# `3.0` in app.py's CPU-budget staleness test -- so the reader's idea of "recent" and the
# producer's idea of "how often" could drift apart with nothing to catch it.
HOST_CPU_CADENCE_SECONDS = 1.0
HOST_CPU_SAMPLE_STALE_CADENCES = 3
HOST_CPU_SAMPLE_STALE_AFTER_SECONDS = HOST_CPU_CADENCE_SECONDS * HOST_CPU_SAMPLE_STALE_CADENCES


def host_cpu_sample_age_seconds(sample: dict[str, Any] | None, now: float) -> float | None:
    """Age of a pushed sample, or None when no sample has ever been pushed."""

    pushed_at = float((sample or {}).get("time") or 0.0)
    return max(0.0, now - pushed_at) if pushed_at > 0 else None


def host_cpu_sample_is_stale(age_seconds: float | None) -> bool:
    """Whether a sample is too old to describe the present. Never pushed counts as stale."""

    return age_seconds is None or age_seconds > HOST_CPU_SAMPLE_STALE_AFTER_SECONDS


class CpuSampler:
    """Stateful CPU baseline for one web PID, held exclusively by statsd."""

    def __init__(self) -> None:
        self._previous_system: tuple[float, float] | None = None
        self._previous_process: tuple[float, float] | None = None

    def sample(self, pid: int) -> dict[str, float | int | None]:
        """Sample this process and the host, reporting absence rather than a fabricated 0.0.

        Both percentages are DERIVED from a difference between two readings. On the first call
        after every statsd start there is no previous reading to difference against, and there is
        also nothing to measure when the elapsed window or the host tick total did not advance.
        Those cases used to fall through to the `0.0` these two locals were initialized to, which
        left an unmeasured value beside a real `time`, a real `pid` and a real `rss_bytes` --
        indistinguishable from a measured idle process, and `system_cpu_percent: 0.0` is a
        whole-host claim that is physically impossible. That row reached `observations`, which is
        retained for 48 hours, and owned the whole 1s bucket at the default five-minute view: a
        full-depth dip to the axis on the CPU graph and `CPU 0%` stamped `measured` on the Daemons
        web row at every statsd start.

        `None` is the value's own statement that nobody measured it; the caller
        (`StatsCurrentService._collect_host_facts_if_due`) is what decides to publish nothing that
        cycle. A genuinely measured `0.0` -- a real difference that came out at zero -- is still a
        float and still published.

        `rss_bytes` is an ABSOLUTE read needing no baseline, so it stays a measurement on the very
        first sample; blanking it would trade a fabricated number for a lost one.
        """

        now = time.time()
        monotonic = time.monotonic()
        process = _process_cpu_seconds_and_rss(pid)
        system = _system_times()
        process_percent: float | None = None
        system_percent: float | None = None
        rss = 0
        if process is not None:
            cpu_seconds, rss = process
            if self._previous_process is not None:
                previous_cpu_seconds, previous_at = self._previous_process
                elapsed = monotonic - previous_at
                if elapsed > 0:
                    process_percent = max(0.0, (cpu_seconds - previous_cpu_seconds) / elapsed * 100.0)
            self._previous_process = (cpu_seconds, monotonic)
        if system is not None:
            if self._previous_system is not None:
                previous_total, previous_busy = self._previous_system
                total_delta = system[0] - previous_total
                busy_delta = system[1] - previous_busy
                if total_delta > 0 and busy_delta >= 0:
                    system_percent = _clamp(busy_delta / total_delta * 100.0)
            self._previous_system = system
        return {
            "time": now,
            "pid": pid,
            "cpu_percent": None if process_percent is None else round(process_percent, 3),
            "system_cpu_percent": None if system_percent is None else round(system_percent, 3),
            "rss_bytes": rss,
        }


def generic_gpu_devices() -> dict[str, dict[str, float | int | str]]:
    try:
        result = subprocess.run(["gpu-info", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=0.75, check=False)
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    devices: dict[str, dict[str, float | int | str]] = {}
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 5:
            continue
        try:
            index = int(parts[0])
            used, capacity = float(parts[3]), float(parts[4])
            utilization = _clamp(float(parts[2]))
        except ValueError:
            continue
        devices[f"gpu:{index}"] = {"label": f"GPU {index} ({parts[1]})" if parts[1] else f"GPU {index}", "util_percent": utilization, "memory_used_bytes": int(max(0, used) * 1024 * 1024), "memory_capacity_bytes": int(max(0, capacity) * 1024 * 1024)}
    return devices


def gpu_devices() -> dict[str, dict[str, float | int | str]]:
    if sys.platform != "darwin":
        return generic_gpu_devices()
    try:
        result = subprocess.run(["ioreg", "-a", "-r", "-d1", "-w0", "-c", "IOAccelerator"], capture_output=True, timeout=0.75, check=False)
        rows = plistlib.loads(result.stdout) if result.returncode == 0 and result.stdout else []
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException):
        return {}
    devices: dict[str, dict[str, float | int | str]] = {}
    if not isinstance(rows, list):
        return devices
    for index, row in enumerate(rows):
        stats = row.get("PerformanceStatistics") if isinstance(row, dict) else None
        if not isinstance(stats, dict):
            continue
        utilization = stats.get("Device Utilization %", stats.get("GPU Activity(%)", stats.get("GPU Activity")))
        try:
            if utilization is None:
                continue
            devices[f"gpu:{index}"] = {"label": f"GPU {index}", "util_percent": _clamp(float(utilization)), "memory_used_bytes": max(0, int(stats.get("In use system memory", stats.get("In use video memory", 0)))), "memory_capacity_bytes": 0}
        except (TypeError, ValueError):
            continue
    return devices


def macos_hardware_metadata() -> dict[str, str]:
    if sys.platform != "darwin":
        return {}
    try:
        result = subprocess.run(["system_profiler", "-json", "SPHardwareDataType", "SPMemoryDataType", "SPDisplaysDataType"], capture_output=True, text=True, timeout=0.75, check=False)
        payload = json.loads(result.stdout) if result.returncode == 0 and result.stdout else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    hardware = next((item for item in payload.get("SPHardwareDataType", []) if isinstance(item, dict)), {})
    memory = next((item for item in payload.get("SPMemoryDataType", []) if isinstance(item, dict)), {})
    display = next((item for item in payload.get("SPDisplaysDataType", []) if isinstance(item, dict)), {})
    chip = str(hardware.get("chip_type") or display.get("sppci_model") or "").strip()
    cores = re.findall(r"\d+", str(hardware.get("number_processors") or ""))
    cpu_label = chip
    if len(cores) >= 3:
        cpu_label = f"{chip} · {cores[0]} cores ({cores[1]} performance + {cores[2]} efficiency)" if chip else f"{cores[0]} cores ({cores[1]} performance + {cores[2]} efficiency)"
    elif cores:
        cpu_label = f"{chip} · {cores[0]} cores" if chip else f"{cores[0]} cores"
    metadata = {"cpu_label": cpu_label, "gpu_label": str(display.get("sppci_model") or chip).strip()}
    memory_type = str(memory.get("dimm_type") or "").strip()
    if memory_type:
        metadata["system_memory_label"] = f"{memory_type} unified memory"
    return {key: value for key, value in metadata.items() if value}
