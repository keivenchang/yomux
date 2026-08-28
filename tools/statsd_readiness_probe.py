#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Measure a disposable statsd's cold start: peak RSS to readiness, and serving latency.

This exists because an earlier campaign's numbers could not be re-derived. Every
figure this prints is reproducible from this file plus the fixture it names, and
the raw per-sample series is written out alongside the summary so a later run can
be compared rather than trusted.

Three traps this deliberately avoids:

* **Readiness is observed, never assumed.** The daemon sets `cache_ready_event`
  immediately after its first `_publish` succeeds, and that same publish is what
  makes `status.materializer.state` report `ready` with a non-null cache
  generation. This polls that RPC and keys off the transition. There is no
  "sleep N then measure".
* **The harness does not weigh itself.** Memory is read from the daemon's own
  `/proc/<pid>/status` and `/proc/<pid>/smaps_rollup`, in a different process. A
  previous harness held the generation and store alive after claiming to drop
  them and therefore measured itself as much as the subject.
* **Peak is `VmHWM`, not a sampled maximum.** The kernel maintains `VmHWM` as a
  true high-water mark, so reading it once at the readiness transition cannot
  miss a spike between two samples. The sampled `VmRSS` series is recorded too,
  but only to show the shape of the curve.

Run one cold start:

    python3 tools/statsd_readiness_probe.py run \
        --master /tmp/.../masters/1x.sqlite3 --scratch /tmp/.../run-01 \
        --label 1x-a --out /tmp/.../results/1x-a.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from yolomux_lib.stats_current import storage  # noqa: E402
from yolomux_lib.stats_current.client import SERVICE_NAME  # noqa: E402
from yolomux_lib.stats_current.client import _append_payload  # noqa: E402
from yolomux_lib.stats_current.client import _wire_rpc  # noqa: E402

DATABASE_FILENAME = storage.DATABASE_FILENAME
# Poll cadences. These bound the resolution of a reported transition; they are
# never used as a substitute for observing one.
READY_POLL_SECONDS = 0.05
SAMPLE_SECONDS = 0.05
# Settle is a measured state, not a duration: RSS must stop moving. The window and
# tolerance are reported with the result so the definition travels with the number.
SETTLE_WINDOW_SAMPLES = 40
SETTLE_TOLERANCE_KB = 2048
SETTLE_CAP_SECONDS = 120.0
# A daemon that idles out mid-measurement would silently truncate the run.
IDLE_SECONDS = 900.0


def _now() -> float:
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def _pt(stamp: float | None = None) -> str:
    os.environ["TZ"] = "America/Los_Angeles"
    time.tzset()
    return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(stamp))


def _loadavg() -> list[float]:
    return [float(value) for value in Path("/proc/loadavg").read_text().split()[:3]]


def _proc_status(pid: int) -> dict[str, int]:
    """VmRSS/VmHWM in kB, or an empty dict once the process is gone."""

    try:
        text = Path(f"/proc/{pid}/status").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return {}
    out = {}
    for line in text.splitlines():
        if line.startswith(("VmRSS:", "VmHWM:", "VmSize:", "VmPeak:", "Threads:")):
            key, _, rest = line.partition(":")
            out[key] = int(rest.split()[0])
    return out


def _smaps_rollup(pid: int) -> dict[str, int]:
    """Pss and the private (USS) components in kB."""

    try:
        text = Path(f"/proc/{pid}/smaps_rollup").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return {}
    out = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in {"Rss", "Pss", "Private_Clean", "Private_Dirty", "Swap"}:
            out[key] = int(rest.split()[0])
    if "Private_Clean" in out and "Private_Dirty" in out:
        out["Uss"] = out["Private_Clean"] + out["Private_Dirty"]
    return out


TICKS_PER_SECOND = os.sysconf("SC_CLK_TCK")


def _proc_counters(pid: int) -> dict[str, int]:
    """Fault and CPU counters from /proc/<pid>/stat, in the kernel's own units.

    These are what separate "the first serve computed something" from "the first
    serve waited". CPU time moving means work; minor faults moving means pages
    being touched for the first time; major faults or read_bytes moving means the
    disk. The comm field can contain spaces and parentheses, so the split is taken
    after the LAST close paren, which is the only safe way to parse this file.
    """

    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return {}
    fields = text[text.rfind(")") + 2:].split()
    # fields[0] is `state`, i.e. the 3rd field of the file; offsets follow proc(5).
    return {
        "minflt": int(fields[7]),
        "majflt": int(fields[9]),
        "utime_ticks": int(fields[11]),
        "stime_ticks": int(fields[12]),
        "num_threads": int(fields[17]),
    }


def _thread_counters(pid: int) -> dict[str, dict[str, int]]:
    """Per-thread CPU, keyed by tid, with each thread's comm name.

    This is what decides the whole question. Process-wide CPU cannot tell "the
    serving path computed something expensive" apart from "the serving path was
    blocked while a background thread computed something expensive" -- under
    CPython those look identical from outside, because a background thread holding
    the GIL starves the socket thread just as effectively as a lock would. Reading
    each task's own utime/stime separates them.
    """

    out: dict[str, dict[str, int]] = {}
    try:
        tids = os.listdir(f"/proc/{pid}/task")
    except (FileNotFoundError, ProcessLookupError):
        return out
    for tid in tids:
        try:
            text = Path(f"/proc/{pid}/task/{tid}/stat").read_text()
        except (FileNotFoundError, ProcessLookupError):
            continue
        comm = text[text.find("(") + 1:text.rfind(")")]
        fields = text[text.rfind(")") + 2:].split()
        out[tid] = {
            "comm": comm,
            "utime_ticks": int(fields[11]),
            "stime_ticks": int(fields[12]),
        }
    return out


def _thread_cpu_delta(before: dict, after: dict) -> dict[str, float]:
    """Milliseconds of CPU each thread burned across the bracketed call."""

    out = {}
    for tid, entry in after.items():
        was = before.get(tid, {"utime_ticks": 0, "stime_ticks": 0})
        ticks = (entry["utime_ticks"] - was["utime_ticks"]) + (entry["stime_ticks"] - was["stime_ticks"])
        if ticks:
            out[f"{tid}:{entry['comm']}"] = round(ticks * 1000 / TICKS_PER_SECOND, 1)
    return out


def _proc_io(pid: int) -> dict[str, int]:
    """rchar/read_bytes so a stall on cold SQLite pages is visible as real disk reads."""

    try:
        text = Path(f"/proc/{pid}/io").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return {}
    out = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in {"rchar", "syscr", "read_bytes"}:
            out[key] = int(rest.strip())
    return out


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {key: after.get(key, 0) - value for key, value in before.items()}


def _rpc(sock: Path, action: str, payload: dict | None, timeout: float = 10.0):
    return _wire_rpc(sock, SERVICE_NAME, action, payload or {}, timeout)


LAST_PROBE_ERROR: list[str] = [""]


def _status_or_none(sock: Path) -> dict | None:
    """One status probe, or None while the daemon has not opened its socket yet.

    Documented poller boundary: before the daemon binds, a probe fails as
    FileNotFoundError, ConnectionRefusedError, timeout, or a transport-level
    LocalRpcError depending on how far it got, and none of those mean anything
    except "not yet". The reason is retained so a run that never becomes ready
    reports why instead of just timing out.
    """

    try:
        response, _binary = _rpc(sock, "status", {}, timeout=5.0)
    except Exception as error:  # noqa: BLE001 - see docstring; reason is recorded, not discarded
        LAST_PROBE_ERROR[0] = f"{type(error).__name__}: {error}"
        return None
    return response if isinstance(response, dict) else None


def _is_ready(status: dict | None) -> bool:
    """The externally visible form of `cache_ready_event` being set.

    `cache_ready_event.set()` runs immediately after the first successful
    `_publish`, and that publish is what installs `self._cache`. The status
    projector reports `cache_generation = 0 if self._cache is None else
    self._cache.generation.cache_generation`, and a real generation is
    `max(self._next_cache_generation + 1, int(now * 1000))`, so it is always
    positive. The first status reporting a POSITIVE cache generation is therefore
    the same instant as `cache_ready_event.set()`.

    A zero is emphatically not readiness: the daemon answers status within a few
    seconds of spawn, long before its first build, and reports
    `cache_generation: 0`, `materializer.state: "dirty"`, `warm.ready: 0`. Keying
    off "not None" would have reported readiness at 4.4 s and a 46 MiB peak while
    the real build had not started.
    """

    if not status or not status.get("ok"):
        return False
    generation = status.get("cache_generation")
    return isinstance(generation, int) and not isinstance(generation, bool) and generation > 0


def _sample_series(pid: int, deadline: float, stop_when=None) -> list[tuple[float, int, int]]:
    series = []
    while _now() < deadline:
        status = _proc_status(pid)
        if not status:
            break
        series.append((_now(), status.get("VmRSS", 0), status.get("VmHWM", 0)))
        if stop_when is not None and stop_when(series):
            break
        time.sleep(SAMPLE_SECONDS)
    return series


def _settled(series: list[tuple[float, int, int]]) -> bool:
    if len(series) < SETTLE_WINDOW_SAMPLES:
        return False
    window = [rss for _stamp, rss, _hwm in series[-SETTLE_WINDOW_SAMPLES:]]
    return max(window) - min(window) <= SETTLE_TOLERANCE_KB


def _percentiles(samples: list[float]) -> dict[str, float]:
    """Nearest-rank percentiles; with the sample count reported beside them."""

    if not samples:
        return {"count": 0}
    ordered = sorted(samples)
    def rank(fraction: float) -> float:
        index = max(0, min(len(ordered) - 1, int(round(fraction * len(ordered) + 0.5)) - 1))
        return ordered[index]
    return {
        "count": len(ordered),
        "min_ms": round(ordered[0] * 1000, 4),
        "p50_ms": round(rank(0.50) * 1000, 4),
        "p95_ms": round(rank(0.95) * 1000, 4),
        "p99_ms": round(rank(0.99) * 1000, 4),
        "max_ms": round(ordered[-1] * 1000, 4),
        "mean_ms": round(sum(ordered) / len(ordered) * 1000, 4),
    }


def _snapshot_probe(sock: Path, count: int, client_id: str) -> tuple[list[float], dict]:
    samples: list[float] = []
    last: dict = {}
    for index in range(count):
        payload = {"range_seconds": 3600, "resolution": 60, "client_id": f"{client_id}-{index % 4}"}
        started = time.perf_counter()
        response, binary = _rpc(sock, "snapshot", payload, timeout=30.0)
        samples.append(time.perf_counter() - started)
        last = {"ok": response.get("ok"), "state": response.get("state"), "binary_bytes": len(binary), "keys": sorted(response)[:12]}
    return samples, last


def _append_probe(sock: Path, count: int, tag: str) -> tuple[list[float], dict]:
    samples: list[float] = []
    last: dict = {}
    now = time.time()
    for index in range(count):
        observation = storage.Observation(
            event_id=f"{tag}-{index}",
            family="system_memory",
            source_id=f"probe-{tag}",
            observed_at=now + index,
            epoch_id=f"{tag}-epoch",
            owner_generation=1,
            payload={"rss_bytes": 1024 * index, "probe": True},
        )
        epoch = storage.CoverageEpoch(
            family="system_memory",
            source_id=f"probe-{tag}",
            epoch_id=f"{tag}-epoch",
            started_at=now,
            ended_at=None,
            native_cadence_seconds=1.0,
            owner_generation=1,
        )
        payload = _append_payload([observation], [], [epoch] if index == 0 else [], [])
        started = time.perf_counter()
        response, _binary = _rpc(sock, "append", payload, timeout=30.0)
        samples.append(time.perf_counter() - started)
        last = {"ok": response.get("ok"), "keys": sorted(response)[:12]}
    return samples, last


def _reap(process: subprocess.Popen, log: Path) -> dict:
    """SIGTERM the daemon's own group, escalate only after bounded polling."""

    record: dict = {"pid": process.pid, "terminated": False, "killed": False}
    if process.poll() is not None:
        record["already_exited"] = process.returncode
        return record
    group = os.getpgid(process.pid)
    os.killpg(group, signal.SIGTERM)
    record["terminated"] = True
    deadline = _now() + 30.0
    while _now() < deadline and process.poll() is None:
        time.sleep(0.05)
    if process.poll() is None:
        os.killpg(group, signal.SIGKILL)
        record["killed"] = True
        deadline = _now() + 15.0
        while _now() < deadline and process.poll() is None:
            time.sleep(0.05)
    record["returncode"] = process.poll()
    record["log_tail"] = log.read_text(errors="replace")[-2000:] if log.exists() else ""
    return record


def _prepare_root(master: Path, root: Path, label: str) -> dict:
    """Persistent private root, plus a per-run private copy of the store with its WAL.

    The root persists across runs on purpose. Live statsd runs with
    `PYTHONPYCACHEPREFIX` under its own `YOLOMUX_ROOT`, so that bytecode cache
    survives a restart; recreating it per run would charge every cold start a
    full recompilation that a real restart never pays, and inflate both the
    readiness time and the peak. Only the database directory is fresh, because
    the daemon opens the store for write and would otherwise carry state from
    the previous run into the next one.
    """

    for part in ("config", "state", "cache", "runtime", "runtime/python-cache"):
        (root / part).mkdir(parents=True, exist_ok=True)
    scratch = root / f"db-{label}"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    database = scratch / DATABASE_FILENAME
    shutil.copy2(master, database)
    provenance = {"database": str(database), "master": str(master), "wal_copied": False}
    wal = master.with_name(master.name + "-wal")
    if wal.exists():
        shutil.copy2(wal, database.with_name(database.name + "-wal"))
        provenance["wal_copied"] = True
        provenance["wal_bytes"] = wal.stat().st_size
    provenance["database_bytes"] = database.stat().st_size
    provenance["run_dir"] = str(scratch)
    return provenance


def _launch_daemon(master: Path, root: Path, label: str) -> dict:
    """Private root, private copy of the store, one fresh statsd. Shared by every probe.

    Every measurement in this file must start a daemon exactly the same way, or
    two runs are not comparable. This is the single place that does it.
    """

    provenance = _prepare_root(master, root, label)
    database = Path(provenance["database"])
    scratch = Path(provenance["run_dir"])
    sock = root / "runtime" / f"statsd-{label}.sock"
    log = scratch / "daemon.log"

    env = dict(os.environ)
    env["YOLOMUX_ROOT"] = str(root)
    env["YOLOMUX_CONFIG_DIR"] = str(root / "config")
    env["YOLOMUX_STATE_DIR"] = str(root / "state")
    env["YOLOMUX_CACHE_DIR"] = str(root / "cache")
    env["YOLOMUX_RUNTIME_DIR"] = str(root / "runtime")
    env["PYTHONPYCACHEPREFIX"] = str(root / "runtime" / "python-cache")
    env.pop("YOLOMUX_GENERATED_PYTHONPYCACHEPREFIX", None)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), env.get("PYTHONPATH", "")]).strip(os.pathsep)
    env.setdefault("MALLOC_ARENA_MAX", "2")

    command = [
        sys.executable, "-m", "yolomux_lib.stats_current.service", "--serve",
        "--socket", str(sock), "--database", str(database),
        "--idle-seconds", str(IDLE_SECONDS),
    ]
    started = _now()
    with log.open("wb") as output:
        process = subprocess.Popen(
            command, cwd=str(REPO_ROOT), env=env, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
        )
    return {
        "process": process, "sock": sock, "log": log, "scratch": scratch,
        "provenance": provenance, "command": command, "started": started,
    }


def _await_ready(daemon: dict, ready_timeout: float, series: list | None = None) -> dict:
    """Poll to the readiness transition, sampling RSS on the way. Never sleeps to a deadline."""

    process, sock, started = daemon["process"], daemon["sock"], daemon["started"]
    deadline = started + ready_timeout
    ready_at = ready_status = first_rpc_at = None
    error = None
    while _now() < deadline:
        if process.poll() is not None:
            error = f"daemon exited early rc={process.returncode}"
            break
        status = _proc_status(process.pid)
        if status and series is not None:
            series.append((_now(), status.get("VmRSS", 0), status.get("VmHWM", 0)))
        probe = _status_or_none(sock)
        if probe is not None and first_rpc_at is None:
            first_rpc_at = _now()
        if _is_ready(probe):
            ready_at, ready_status = _now(), probe
            break
        time.sleep(READY_POLL_SECONDS)
    if ready_at is None and error is None:
        error = f"readiness not observed before timeout; last probe: {LAST_PROBE_ERROR[0]}"
    return {"ready_at": ready_at, "status": ready_status, "first_rpc_at": first_rpc_at, "error": error}


def run(args: argparse.Namespace) -> int:
    master = Path(args.master).resolve()
    root = Path(args.root).resolve()
    daemon = _launch_daemon(master, root, args.label)
    process, sock, log = daemon["process"], daemon["sock"], daemon["log"]
    scratch, provenance, command = daemon["scratch"], daemon["provenance"], daemon["command"]
    started = daemon["started"]

    record: dict = {
        "label": args.label,
        "command": command,
        "fixture": provenance,
        "settle_definition": {
            "window_samples": SETTLE_WINDOW_SAMPLES,
            "tolerance_kb": SETTLE_TOLERANCE_KB,
            "sample_seconds": SAMPLE_SECONDS,
            "cap_seconds": SETTLE_CAP_SECONDS,
        },
        "ready_poll_seconds": READY_POLL_SECONDS,
        "start_pt": _pt(),
        "start_load": _loadavg(),
        "start_monotonic": started,
        "pid": process.pid,
    }

    try:
        # --- readiness, observed on the status transition -------------------
        series: list[tuple[float, int, int]] = []
        waited = _await_ready(daemon, args.ready_timeout, series)
        ready_at, ready_status, first_rpc_at = waited["ready_at"], waited["status"], waited["first_rpc_at"]
        if waited["error"]:
            record["error"] = waited["error"]

        if ready_at is None:
            record["reap"] = _reap(process, log)
            record["end_pt"], record["end_load"] = _pt(), _loadavg()
            record["end_monotonic"] = _now()
            return _emit(record, args)

        hwm_at_ready = _proc_status(process.pid)
        record["readiness"] = {
            "seconds_from_spawn": round(ready_at - started, 4),
            "seconds_from_first_rpc_answer": None if first_rpc_at is None else round(ready_at - first_rpc_at, 4),
            "socket_answered_after_seconds": None if first_rpc_at is None else round(first_rpc_at - started, 4),
            "peak_rss_kb_at_ready": hwm_at_ready.get("VmHWM", 0),
            "rss_kb_at_ready": hwm_at_ready.get("VmRSS", 0),
            "threads_at_ready": hwm_at_ready.get("Threads", 0),
            "cache_generation": ready_status.get("cache_generation"),
            "source_generation": ready_status.get("source_generation"),
            "materializer": ready_status.get("materializer"),
            "warm": ready_status.get("warm"),
        }

        # --- settle, observed on RSS going quiet ----------------------------
        settle_series = _sample_series(process.pid, _now() + SETTLE_CAP_SECONDS, stop_when=_settled)
        series.extend(settle_series)
        settled = _settled(settle_series)
        steady = _proc_status(process.pid)
        rollup = _smaps_rollup(process.pid)
        record["steady"] = {
            "settled": settled,
            "settle_seconds": round(settle_series[-1][0] - ready_at, 4) if settle_series else 0.0,
            "rss_kb": steady.get("VmRSS", 0),
            "peak_rss_kb": steady.get("VmHWM", 0),
            "threads": steady.get("Threads", 0),
            "pss_kb": rollup.get("Pss", 0),
            "uss_kb": rollup.get("Uss", 0),
            "rollup_rss_kb": rollup.get("Rss", 0),
            "swap_kb": rollup.get("Swap", 0),
        }
        peak = record["readiness"]["peak_rss_kb_at_ready"]
        steady_rss = record["steady"]["rss_kb"]
        record["startup_contribution"] = {
            "peak_rss_to_ready_kb": peak,
            "steady_rss_kb": steady_rss,
            "difference_kb": peak - steady_rss,
            "difference_share_of_peak": round((peak - steady_rss) / peak, 4) if peak else None,
        }

        # --- the regression limb -------------------------------------------
        record["latency_load_before"] = _loadavg()
        snapshot_samples, snapshot_last = _snapshot_probe(sock, args.snapshot_calls, f"probe-{args.label}")
        record["snapshot_latency"] = _percentiles(snapshot_samples)
        record["snapshot_last_response"] = snapshot_last
        append_samples, append_last = _append_probe(sock, args.append_calls, f"probe-{args.label}")
        record["append_latency"] = _percentiles(append_samples)
        record["append_last_response"] = append_last
        record["latency_load_after"] = _loadavg()
        record["latency_samples"] = {"snapshot_s": snapshot_samples, "append_s": append_samples}
        record["after_latency"] = _proc_status(process.pid)
        record["final_status"] = _status_or_none(sock)
    finally:
        record["reap"] = _reap(process, log)
        record["end_pt"], record["end_load"] = _pt(), _loadavg()
        record["end_monotonic"] = _now()
        record["rss_series"] = [[round(stamp - started, 3), rss, hwm] for stamp, rss, hwm in series]
        record["scratch_bytes"] = sum(f.stat().st_size for f in scratch.rglob("*") if f.is_file())
        record["root_bytes"] = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())

    if not args.keep:
        shutil.rmtree(scratch, ignore_errors=True)
    return _emit(record, args)



# Every (range_seconds, resolution) the resolution policy admits, so a probe can ask
# for a view nobody has asked for yet and see whether "first of its kind" is what costs.
VIEW_MATRIX: tuple[tuple[int, int], ...] = (
    (300, 1), (300, 10), (900, 10), (900, 60), (1800, 10), (1800, 60),
    (3600, 60), (3600, 300), (7200, 60), (7200, 300), (14400, 60), (14400, 300),
    (28800, 60), (28800, 300), (57600, 300), (86400, 300),
)


def _timed_snapshot(sock: Path, pid: int, range_seconds: int, resolution: int, client_id: str) -> dict:
    """One snapshot call, timed, with the daemon's own counters bracketed around it.

    The counters are what turn a number into an attribution. A call that burns CPU
    ticks computed something; a call that moves minflt touched pages for the first
    time; a call that moves read_bytes went to the disk; a call that moves none of
    them was waiting on a lock or on another thread.
    """

    before_counters, before_io, before_status = _proc_counters(pid), _proc_io(pid), _proc_status(pid)
    before_threads = _thread_counters(pid)
    payload = {"range_seconds": range_seconds, "resolution": resolution, "client_id": client_id}
    started = time.perf_counter()
    response, binary = _rpc(sock, "snapshot", payload, timeout=60.0)
    elapsed = time.perf_counter() - started
    after_counters, after_io, after_status = _proc_counters(pid), _proc_io(pid), _proc_status(pid)
    after_threads = _thread_counters(pid)
    counter_delta = _delta(before_counters, after_counters)
    return {
        "range_seconds": range_seconds,
        "resolution": resolution,
        "client_id": client_id,
        "ms": round(elapsed * 1000, 4),
        "state": response.get("state"),
        "ok": response.get("ok"),
        "retry_after_seconds": response.get("retry_after_seconds"),
        "resolution_seconds": response.get("resolution_seconds"),
        "cache_generation": response.get("cache_generation"),
        "binary_bytes": len(binary),
        "cpu_ms_by_thread": _thread_cpu_delta(before_threads, after_threads),
        "cpu_ms_in_daemon": round((counter_delta.get("utime_ticks", 0) + counter_delta.get("stime_ticks", 0)) * 1000 / TICKS_PER_SECOND, 1),
        "minflt": counter_delta.get("minflt", 0),
        "majflt": counter_delta.get("majflt", 0),
        "read_bytes": _delta(before_io, after_io).get("read_bytes", 0),
        "rchar": _delta(before_io, after_io).get("rchar", 0),
        "rss_kb_before": before_status.get("VmRSS", 0),
        "rss_kb_after": after_status.get("VmRSS", 0),
    }


def firstsnapshot(args: argparse.Namespace) -> int:
    """Characterise the FIRST snapshot served after readiness, and attribute its cost.

    One cold start yields exactly one honest "first snapshot after readiness"
    sample, so the distribution comes from repeating whole cold starts. Within each
    start the probe then separates three candidate explanations without restarting:

    * repeating the SAME view -- if only call #1 is slow, the cost is one-time.
    * asking for views nobody has requested yet -- if each first-of-its-kind is
      slow, the cost is per cache key, not per process.
    * re-asking the original view under a NEW client id -- private entries take a
      different path from shared ones, so this separates the two.
    """

    master = Path(args.master).resolve()
    root = Path(args.root).resolve()
    daemon = _launch_daemon(master, root, args.label)
    process, sock, log = daemon["process"], daemon["sock"], daemon["log"]
    started = daemon["started"]
    record: dict = {
        "label": args.label,
        "fixture": daemon["provenance"],
        "command": daemon["command"],
        "start_pt": _pt(),
        "start_load": _loadavg(),
        "start_monotonic": started,
        "pid": process.pid,
        "ticks_per_second": TICKS_PER_SECOND,
    }
    try:
        waited = _await_ready(daemon, args.ready_timeout)
        if waited["ready_at"] is None:
            record["error"] = waited["error"]
            return _emit_first(record, args)
        ready_at = waited["ready_at"]
        record["readiness"] = {
            "seconds_from_spawn": round(ready_at - started, 4),
            "cache_generation": waited["status"].get("cache_generation"),
            "warm": waited["status"].get("warm"),
            "materializer": waited["status"].get("materializer"),
            "queue": waited["status"].get("queue"),
            "rss_kb_at_ready": _proc_status(process.pid).get("VmRSS", 0),
        }
        base_range, base_resolution = args.range_seconds, args.resolution

        # The single sample this whole task is about: nothing has been served yet.
        record["first"] = _timed_snapshot(sock, process.pid, base_range, base_resolution, f"{args.label}-primary")
        record["first"]["seconds_after_ready"] = round(_now() - ready_at, 4)

        # Same view, same client, repeated: is the cost one-time?
        record["same_view_repeat"] = [
            _timed_snapshot(sock, process.pid, base_range, base_resolution, f"{args.label}-primary")
            for _index in range(args.repeat)
        ]
        # Same view, brand-new client ids: does a private entry pay it again?
        record["new_client_same_view"] = [
            _timed_snapshot(sock, process.pid, base_range, base_resolution, f"{args.label}-fresh-{index}")
            for index in range(args.fresh_clients)
        ]
        # Views nobody has asked for yet, one call each, in policy order.
        record["first_of_each_view"] = [
            _timed_snapshot(sock, process.pid, view_range, view_resolution, f"{args.label}-primary")
            for view_range, view_resolution in VIEW_MATRIX
            if (view_range, view_resolution) != (base_range, base_resolution)
        ]
        # And each of those a second time, to show the same one-time/not-one-time split.
        record["second_of_each_view"] = [
            _timed_snapshot(sock, process.pid, view_range, view_resolution, f"{args.label}-primary")
            for view_range, view_resolution in VIEW_MATRIX
            if (view_range, view_resolution) != (base_range, base_resolution)
        ]
        record["final_status"] = _status_or_none(sock)
    finally:
        record["reap"] = _reap(process, log)
        record["end_pt"], record["end_load"] = _pt(), _loadavg()
        record["end_monotonic"] = _now()
        record["scratch_bytes"] = sum(f.stat().st_size for f in daemon["scratch"].rglob("*") if f.is_file())
    if not args.keep:
        shutil.rmtree(daemon["scratch"], ignore_errors=True)
    return _emit_first(record, args)



def sweep(args: argparse.Namespace) -> int:
    """Poll one view steadily from the readiness instant and record every latency.

    One cold start gives exactly one "first snapshot after readiness". Mapping the
    slow window needs the whole curve instead, so this keeps asking for the same
    view at a fixed gap and stamps each answer against seconds-since-ready. The
    steady demand is deliberate and realistic: a browser watching a chart polls,
    and `_build_once` only encodes when there IS public demand, so a probe that
    goes quiet would measure a different daemon than a user sees.
    """

    master = Path(args.master).resolve()
    root = Path(args.root).resolve()
    daemon = _launch_daemon(master, root, args.label)
    process, sock, log = daemon["process"], daemon["sock"], daemon["log"]
    record: dict = {
        "label": args.label,
        "fixture": daemon["provenance"],
        "start_pt": _pt(),
        "start_load": _loadavg(),
        "start_monotonic": daemon["started"],
        "pid": process.pid,
        "gap_ms": args.gap_ms,
        "sweep_seconds": args.sweep_seconds,
        "view": [args.range_seconds, args.resolution],
    }
    try:
        waited = _await_ready(daemon, args.ready_timeout)
        if waited["ready_at"] is None:
            record["error"] = waited["error"]
            return _emit_first(record, args)
        ready_at = waited["ready_at"]
        record["readiness"] = {
            "seconds_from_spawn": round(ready_at - daemon["started"], 4),
            "cache_generation": waited["status"].get("cache_generation"),
            "warm": waited["status"].get("warm"),
            "materializer": waited["status"].get("materializer"),
            "queue": waited["status"].get("queue"),
        }
        calls = []
        deadline = ready_at + args.sweep_seconds
        while _now() < deadline:
            offset = _now() - ready_at
            call = _timed_snapshot(sock, process.pid, args.range_seconds, args.resolution, f"{args.label}-sweep")
            call["t_since_ready"] = round(offset, 4)
            calls.append(call)
            time.sleep(args.gap_ms / 1000.0)
        record["calls"] = calls
        record["final_status"] = _status_or_none(sock)
    finally:
        record["reap"] = _reap(process, log)
        record["end_pt"], record["end_load"] = _pt(), _loadavg()
        record["end_monotonic"] = _now()
        record["scratch_bytes"] = sum(f.stat().st_size for f in daemon["scratch"].rglob("*") if f.is_file())
    if not args.keep:
        shutil.rmtree(daemon["scratch"], ignore_errors=True)
    calls = record.get("calls") or []
    over = [c for c in calls if c["ms"] >= args.slow_ms]
    print(json.dumps({
        "label": args.label,
        "ready_s": (record.get("readiness") or {}).get("seconds_from_spawn"),
        "calls": len(calls),
        "first_ms": calls[0]["ms"] if calls else None,
        "slow_count": len(over),
        "slow_share": round(len(over) / len(calls), 4) if calls else None,
        "max_ms": max((c["ms"] for c in calls), default=None),
        "distinct_binary_sizes": sorted({c["binary_bytes"] for c in calls}),
        "states": sorted({str(c["state"]) for c in calls}),
        "error": record.get("error"),
    }, sort_keys=True))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(record, indent=1, sort_keys=True))
    return 1 if record.get("error") else 0



def distill(args: argparse.Namespace) -> int:
    """Shrink sweep records for commit while keeping every claim re-derivable.

    A sweep holds ~1,100 calls each carrying per-thread CPU, fault and I/O
    counters, which is half a megabyte per cold start. The fast calls are
    interchangeable, so they are kept only as (seconds-since-ready, milliseconds,
    binary bytes) -- enough to recompute any percentile. Every call at or above
    `--detail-ms`, and every pending response, is kept whole, because those are
    the ones the conclusions rest on.
    """

    kept = []
    for source in sorted(Path(part) for part in args.inputs):
        record = json.loads(source.read_text())
        calls = record.get("calls") or []
        record["calls_distilled"] = {
            "note": "fast calls reduced to [t_since_ready, ms, binary_bytes]; see calls_detail for the rest",
            "detail_threshold_ms": args.detail_ms,
            "series": [[c["t_since_ready"], c["ms"], c["binary_bytes"]] for c in calls],
        }
        record["calls_detail"] = [
            c for c in calls if c["ms"] >= args.detail_ms or c["binary_bytes"] == 0
        ]
        record.pop("calls", None)
        target = Path(args.outdir) / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record, indent=1, sort_keys=True))
        kept.append({
            "file": target.name,
            "calls": len(calls),
            "detailed": len(record["calls_detail"]),
            "bytes_before": source.stat().st_size,
            "bytes_after": target.stat().st_size,
        })
    print(json.dumps(kept, indent=1, sort_keys=True))
    return 0


def _emit_first(record: dict, args: argparse.Namespace) -> int:
    text = json.dumps(record, indent=1, sort_keys=True)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    first = record.get("first") or {}
    repeats = record.get("same_view_repeat") or []
    print(json.dumps({
        "label": record.get("label"),
        "ready_s": (record.get("readiness") or {}).get("seconds_from_spawn"),
        "first_ms": first.get("ms"),
        "first_state": first.get("state"),
        "first_cpu_ms": first.get("cpu_ms_in_daemon"),
        "first_minflt": first.get("minflt"),
        "first_read_bytes": first.get("read_bytes"),
        "first_binary_bytes": first.get("binary_bytes"),
        "repeat_ms": [item["ms"] for item in repeats[:5]],
        "error": record.get("error"),
    }, sort_keys=True))
    return 1 if record.get("error") else 0


def _emit(record: dict, args: argparse.Namespace) -> int:
    text = json.dumps(record, indent=1, sort_keys=True)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    summary = {key: record.get(key) for key in ("label", "readiness", "steady", "startup_contribution", "snapshot_latency", "append_latency", "error")}
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 1 if record.get("error") else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run", help="one cold start, measured end to end")
    runner.add_argument("--master", required=True, help="pristine store to copy; never opened in place")
    runner.add_argument("--root", required=True, help="persistent private YOLOMUX_ROOT; reused across runs")
    runner.add_argument("--label", required=True)
    runner.add_argument("--out", default="")
    runner.add_argument("--ready-timeout", type=float, default=600.0)
    runner.add_argument("--snapshot-calls", type=int, default=200)
    runner.add_argument("--append-calls", type=int, default=200)
    runner.add_argument("--keep", action="store_true")
    runner.set_defaults(func=run)
    first = sub.add_parser("firstsnapshot", help="characterise and attribute the first snapshot after readiness")
    first.add_argument("--master", required=True)
    first.add_argument("--root", required=True)
    first.add_argument("--label", required=True)
    first.add_argument("--out", default="")
    first.add_argument("--ready-timeout", type=float, default=900.0)
    first.add_argument("--range-seconds", type=int, default=3600)
    first.add_argument("--resolution", type=int, default=60)
    first.add_argument("--repeat", type=int, default=20)
    first.add_argument("--fresh-clients", type=int, default=5)
    first.add_argument("--keep", action="store_true")
    first.set_defaults(func=firstsnapshot)
    sweeper = sub.add_parser("sweep", help="poll one view from the readiness instant and record every latency")
    sweeper.add_argument("--master", required=True)
    sweeper.add_argument("--root", required=True)
    sweeper.add_argument("--label", required=True)
    sweeper.add_argument("--out", default="")
    sweeper.add_argument("--ready-timeout", type=float, default=900.0)
    sweeper.add_argument("--range-seconds", type=int, default=3600)
    sweeper.add_argument("--resolution", type=int, default=60)
    sweeper.add_argument("--sweep-seconds", type=float, default=90.0)
    sweeper.add_argument("--gap-ms", type=float, default=25.0)
    sweeper.add_argument("--slow-ms", type=float, default=50.0)
    sweeper.add_argument("--keep", action="store_true")
    sweeper.set_defaults(func=sweep)
    shrink = sub.add_parser("distill", help="shrink sweep records for commit, keeping every slow call whole")
    shrink.add_argument("inputs", nargs="+")
    shrink.add_argument("--outdir", required=True)
    shrink.add_argument("--detail-ms", type=float, default=20.0)
    shrink.set_defaults(func=distill)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
