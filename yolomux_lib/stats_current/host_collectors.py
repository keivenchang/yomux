# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Host-only samplers owned by the persistent stats daemon."""

from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


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


class CpuSampler:
    """Stateful CPU baseline for one web PID, held exclusively by statsd."""

    def __init__(self) -> None:
        self._previous_system: tuple[float, float] | None = None
        self._previous_process: tuple[float, float] | None = None

    def sample(self, pid: int) -> dict[str, float | int]:
        now = time.time()
        monotonic = time.monotonic()
        process = _linux_process_ticks(pid)
        system = _linux_system_times()
        process_percent = 0.0
        system_percent = 0.0
        rss = 0
        if process is not None:
            ticks, rss = process
            if self._previous_process is not None:
                previous_ticks, previous_at = self._previous_process
                elapsed = monotonic - previous_at
                if elapsed > 0:
                    process_percent = max(0.0, ((ticks - previous_ticks) / float(os.sysconf("SC_CLK_TCK"))) / elapsed * 100.0)
            self._previous_process = (ticks, monotonic)
        if system is not None:
            if self._previous_system is not None:
                previous_total, previous_busy = self._previous_system
                total_delta = system[0] - previous_total
                busy_delta = system[1] - previous_busy
                if total_delta > 0 and busy_delta >= 0:
                    system_percent = _clamp(busy_delta / total_delta * 100.0)
            self._previous_system = system
        return {"time": now, "pid": pid, "cpu_percent": round(process_percent, 3), "system_cpu_percent": round(system_percent, 3), "rss_bytes": rss}


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
