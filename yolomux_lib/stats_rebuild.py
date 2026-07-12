"""Offline, single-writer rebuild of YO!stats transcript usage components."""

from __future__ import annotations

import fcntl
import json
import math
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from . import session_files
from . import sessions
from .local_services import stats_store
from .pricing_catalog import PricingCatalog
from .statsd import STATSD_USAGE_ATOM_MIGRATION_MARKER
from .statsd import STATSD_USAGE_ATOM_MIGRATION_STATUS_KEY
from .statsd import STATSD_USAGE_ATOM_MIGRATION_VERSION
from .statsd import STATS_HISTORY_RETENTION_SECONDS
from .statsd import PersistentStatsService
from .statsd import default_database_path
from .statsd import default_socket_path
from .statsd import normalized_usage_atom
from .statsd import projected_usage_component


OWNER_FRESH_SECONDS = 15.0
STOP_TIMEOUT_SECONDS = 12.0
TOKEN_SERVER_FIELDS = frozenset({"tokens_per_agent_total", "agent_token_samples"})


class StatsRebuildSafetyError(RuntimeError):
    """The target database cannot be proved offline and exclusive."""


@dataclass(frozen=True)
class TranscriptFamily:
    kind: str
    root: Path
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    role: str
    source: str


@dataclass
class RebuildReport:
    database: str
    backup: str
    dry_run: bool
    include_live: bool
    families: int
    transcript_files: int
    atoms: int
    duplicate_atoms: int
    rebuilt_buckets: int
    output_only_buckets: int
    truncated_buckets: int
    billable_lt_output_buckets: int
    live_metric_buckets_preserved: int
    stopped_processes: list[dict[str, Any]]

    def payload(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def state_dir_for_database(database: Path) -> Path:
    return _resolved(database).parent


def socket_for_database(database: Path) -> Path:
    database = _resolved(database)
    if database == _resolved(default_database_path()):
        return _resolved(default_socket_path())
    return database.parent / "services" / "statsd.sock"


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def pid_is_alive(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def live_server_processes(state_dir: Path, *, now: float | None = None) -> list[ProcessRecord]:
    """Return live YOLOmux generations that can respawn the shared stats writer."""

    del now  # PID liveness is authoritative; heartbeat age is diagnostic only.
    owner_dir = _resolved(state_dir) / "background-owner"
    records: dict[int, ProcessRecord] = {}
    for path in [owner_dir / "owner.json", *sorted((owner_dir / "generations").glob("*.json"))]:
        value = _json_object(path)
        try:
            pid = int(value.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if pid_is_alive(pid):
            records[pid] = ProcessRecord(pid, "yolomux-server", str(path))
    return sorted(records.values(), key=lambda item: item.pid)


def live_statsd_processes(state_dir: Path) -> list[ProcessRecord]:
    record_path = _resolved(state_dir) / "services" / "statsd.service.json"
    value = _json_object(record_path)
    try:
        pid = int(value.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    return [ProcessRecord(pid, "statsd", str(record_path))] if pid_is_alive(pid) else []


def fresh_owner_processes(state_dir: Path, *, now: float | None = None) -> list[ProcessRecord]:
    path = _resolved(state_dir) / "background-owner" / "owner.json"
    value = _json_object(path)
    try:
        pid = int(value.get("pid") or 0)
        heartbeat = float(value.get("last_heartbeat") or 0.0)
    except (TypeError, ValueError):
        return []
    current = time.time() if now is None else float(now)
    if value.get("status") == "owner" and pid_is_alive(pid) and abs(current - heartbeat) <= OWNER_FRESH_SECONDS:
        return [ProcessRecord(pid, "background-owner", str(path))]
    return []


def database_openers(database: Path) -> set[int]:
    """Return PIDs with the SQLite database or its WAL/SHM sidecars open."""

    database = _resolved(database)
    targets = [database, database.with_name(database.name + "-wal"), database.with_name(database.name + "-shm")]
    existing = [str(path) for path in targets if path.exists()]
    if not existing:
        return set()
    lsof = shutil.which("lsof")
    if lsof:
        try:
            result = subprocess.run(
                [lsof, "-F", "p", "--", *existing],
                check=False,
                capture_output=True,
                text=True,
                timeout=3.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StatsRebuildSafetyError(f"could not inspect database openers with lsof: {exc}") from exc
        return {
            int(line[1:])
            for line in result.stdout.splitlines()
            if line.startswith("p") and line[1:].isdigit() and int(line[1:]) != os.getpid()
        }
    proc = Path("/proc")
    if proc.is_dir():
        target_set = {str(path) for path in targets}
        found: set[int] = set()
        for fd_dir in proc.glob("[0-9]*/fd"):
            try:
                pid = int(fd_dir.parent.name)
            except ValueError:
                continue
            if pid == os.getpid():
                continue
            for fd in fd_dir.iterdir():
                try:
                    if str(fd.resolve(strict=True)) in target_set:
                        found.add(pid)
                        break
                except OSError:
                    continue
        return found
    raise StatsRebuildSafetyError("cannot prove exclusive database access: neither lsof nor /proc is available")


def safety_blockers(database: Path, socket_path: Path | None = None, *, now: float | None = None) -> list[str]:
    database = _resolved(database)
    state_dir = state_dir_for_database(database)
    socket_path = _resolved(socket_path or socket_for_database(database))
    blockers: list[str] = []
    servers = live_server_processes(state_dir, now=now)
    statsd = live_statsd_processes(state_dir)
    owners = fresh_owner_processes(state_dir, now=now)
    openers = database_openers(database)
    if servers:
        blockers.append("live YOLOmux server(s): " + ", ".join(str(item.pid) for item in servers))
    if statsd:
        blockers.append("live statsd process(es): " + ", ".join(str(item.pid) for item in statsd))
    if owners:
        blockers.append("fresh background owner(s): " + ", ".join(str(item.pid) for item in owners))
    if openers:
        blockers.append("database opener(s): " + ", ".join(str(pid) for pid in sorted(openers)))
    if socket_path.exists() or socket_path.is_symlink():
        blockers.append(f"statsd socket still exists: {socket_path}")
    return blockers


def wait_for_stats_quiescence(
    database: Path,
    socket_path: Path,
    *,
    now: float | None = None,
    timeout_seconds: float = 5.0,
) -> list[str]:
    """Wait for already-spawned, lock-losing statsd contenders to close SQLite."""

    deadline = time.monotonic() + max(0.1, timeout_seconds)
    blockers: list[str] = []
    while time.monotonic() < deadline:
        blockers = safety_blockers(database, socket_path, now=now)
        if not blockers:
            return []
        time.sleep(0.1)
    return blockers


def stop_shared_stats_processes(database: Path, *, timeout_seconds: float = STOP_TIMEOUT_SECONDS) -> list[ProcessRecord]:
    """Stop every recorded server and statsd process sharing this state directory."""

    state_dir = state_dir_for_database(database)
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    stopped: dict[int, ProcessRecord] = {}
    signalled: set[int] = set()
    while time.monotonic() < deadline:
        records = {item.pid: item for item in [*live_server_processes(state_dir), *live_statsd_processes(state_dir)]}
        if not records:
            return sorted(stopped.values(), key=lambda item: (item.role == "statsd", item.pid))
        ordered = sorted(records.values(), key=lambda item: (item.role == "statsd", item.pid))
        _disable_macos_launch_jobs(ordered)
        for item in ordered:
            stopped[item.pid] = item
            if item.pid in signalled:
                continue
            try:
                os.kill(item.pid, signal.SIGTERM)
                signalled.add(item.pid)
            except ProcessLookupError:
                continue
            except PermissionError as exc:
                raise StatsRebuildSafetyError(f"cannot stop {item.role} pid {item.pid}: {exc}") from exc
        # Re-discover after each bounded settle: a supervisor or follower may
        # promote only after the prior owner exits.
        time.sleep(0.1)
    alive = [item for item in stopped.values() if pid_is_alive(item.pid)]
    raise StatsRebuildSafetyError(
        "processes did not stop before the safety deadline: " + ", ".join(f"{item.role} pid {item.pid}" for item in alive)
    )


def _disable_macos_launch_jobs(records: list[ProcessRecord]) -> None:
    """Boot out per-port launchd jobs before SIGTERM so KeepAlive cannot race the rebuild."""

    if sys.platform != "darwin" or not shutil.which("launchctl"):
        return
    ports: set[int] = set()
    for item in records:
        if item.role != "yolomux-server":
            continue
        value = _json_object(Path(item.source))
        try:
            port = int(value.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        if port > 0:
            ports.add(port)
    for port in sorted(ports):
        try:
            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}/local.yolomux.{port}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            # The PID and post-stop discovery remain authoritative; failure to
            # address a supervisor becomes a visible deadline/refusal below.
            continue


@contextmanager
def exclusive_stats_locks(database: Path, socket_path: Path) -> Iterator[None]:
    """Prevent a statsd daemon or registry spawn from acquiring ownership."""

    state_dir = state_dir_for_database(database)
    daemon_lock = _resolved(socket_path).with_suffix(".lock")
    registry_lock = state_dir / "services" / ".statsd.service.lock.lock"
    with ExitStack() as stack:
        for path in (registry_lock, daemon_lock):
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = stack.enter_context(path.open("a+", encoding="utf-8"))
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise StatsRebuildSafetyError(f"stats ownership lock is held: {path}") from exc
            stack.callback(fcntl.flock, handle.fileno(), fcntl.LOCK_UN)
        yield


def _is_claude_subagent(path: Path) -> bool:
    return any(part == sessions.CLAUDE_SUBAGENTS_DIRNAME for part in path.parts)


def discover_transcript_families(
    *,
    claude_root: Path | None = None,
    codex_root: Path | None = None,
) -> list[TranscriptFamily]:
    """Discover every on-disk Claude root and complete Codex parent/child family."""

    claude_root = _resolved(claude_root or (Path.home() / ".claude"))
    codex_root = _resolved(codex_root or (Path.home() / ".codex" / "sessions"))
    families: list[TranscriptFamily] = []
    if claude_root.is_dir():
        for root in sorted(path for path in claude_root.rglob("*.jsonl") if not _is_claude_subagent(path)):
            paths = tuple(_resolved(path) for path in session_files.claude_transcript_family_paths(root) if path.is_file())
            if paths:
                families.append(TranscriptFamily("claude", _resolved(root), paths))
    codex_candidates = sorted(_resolved(path) for path in codex_root.rglob("rollout-*.jsonl")) if codex_root.is_dir() else []
    metadata = {path: sessions.codex_transcript_meta(path) for path in codex_candidates}
    known_ids = {thread_id for thread_id, _parent in metadata.values() if thread_id}
    roots = [path for path in codex_candidates if not metadata[path][1] or metadata[path][1] not in known_ids]
    included: set[Path] = set()
    for root in roots:
        paths = tuple(_resolved(path) for path in session_files.codex_transcript_family_paths(root, candidates=codex_candidates) if path.is_file())
        if paths:
            included.update(paths)
            families.append(TranscriptFamily("codex", root, paths))
    # Malformed/legacy rollouts without linkable metadata remain independent roots.
    for root in codex_candidates:
        if root not in included:
            families.append(TranscriptFamily("codex", root, (root,)))
    return families


def _bucket_selected(bucket: dict[str, Any], start: float | None, end: float | None) -> bool:
    bucket_start = float(bucket.get("start") or 0.0)
    bucket_end = bucket_start + max(1.0, float(bucket.get("duration") or 1.0))
    return (start is None or bucket_end > start) and (end is None or bucket_start < end)


def _atom_selected(timestamp: float, start: float | None, end: float | None) -> bool:
    return math.isfinite(timestamp) and (start is None or timestamp >= start) and (end is None or timestamp < end)


def _has_output_scalar(bucket: dict[str, Any]) -> bool:
    return bool(bucket.get("tokens_per_agent_total") or bucket.get("agent_token_rates"))


def _has_live_metrics(bucket: dict[str, Any]) -> bool:
    host_metrics = bucket.get("host_metrics") if isinstance(bucket.get("host_metrics"), dict) else {}
    host_has_data = any(
        (isinstance(value, dict) and bool(value))
        or (isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) > 0)
        or (isinstance(value, str) and bool(value.strip()))
        for value in host_metrics.values()
    )
    return any(float(bucket.get(field) or 0.0) > 0 for field in stats_store.SERVER_FIELDS if field not in TOKEN_SERVER_FIELDS) or bool(
        host_has_data or bucket.get("clients") or bucket.get("servers")
    )


def _clear_live_metrics(bucket: dict[str, Any]) -> None:
    for field in stats_store.SERVER_FIELDS:
        if field not in TOKEN_SERVER_FIELDS:
            bucket[field] = 0.0
    bucket["host_metrics"] = stats_store.empty_host_metrics()
    bucket["clients"] = {}
    bucket["servers"] = {}


def _usage_totals(bucket: dict[str, Any]) -> tuple[float, float]:
    summary = bucket.get("cost_summary") if isinstance(bucket.get("cost_summary"), dict) else {}
    components = summary.get("components") if isinstance(summary.get("components"), list) else []
    token_components = [item for item in components if isinstance(item, dict) and item.get("unit") == "tokens"]
    billable = sum(max(0.0, float(item.get("quantity") or 0.0)) for item in token_components)
    output = sum(max(0.0, float(item.get("quantity") or 0.0)) for item in token_components if item.get("direction") == "output")
    return billable, output


def _backup_database(connection: sqlite3.Connection, database: Path, *, now: float) -> Path:
    stamp = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = database.with_name(f"{database.name}.backup-{stamp}")
    suffix = 1
    while candidate.exists():
        candidate = database.with_name(f"{database.name}.backup-{stamp}-{suffix}")
        suffix += 1
    target = sqlite3.connect(candidate)
    try:
        connection.backup(target)
    finally:
        target.close()
    candidate.chmod(0o600)
    return candidate


def rebuild_stats_tokens(
    database: Path,
    *,
    socket_path: Path | None = None,
    claude_root: Path | None = None,
    codex_root: Path | None = None,
    start: float | None = None,
    end: float | None = None,
    include_live: bool = False,
    dry_run: bool = True,
    stop_services: bool = False,
    now: float | None = None,
    catalog: Any = None,
    progress: Callable[[str], None] | None = None,
) -> RebuildReport:
    """Rebuild usage components while preserving generated output and live history."""

    database = _resolved(database)
    socket_path = _resolved(socket_path or socket_for_database(database))
    sample_now = float(time.time() if now is None else now)
    if start is not None and end is not None and float(end) <= float(start):
        raise ValueError("end must be greater than start")
    if not database.is_file():
        raise FileNotFoundError(database)
    retained_start = sample_now - STATS_HISTORY_RETENTION_SECONDS
    effective_start = retained_start if start is None else max(float(start), retained_start)
    stopped: list[ProcessRecord] = []
    if not dry_run and stop_services:
        stopped = stop_shared_stats_processes(database)
        blockers = wait_for_stats_quiescence(database, socket_path, now=sample_now)
    else:
        blockers = safety_blockers(database, socket_path, now=sample_now)
    if blockers:
        raise StatsRebuildSafetyError("offline rebuild refused:\n- " + "\n- ".join(blockers))
    families = discover_transcript_families(claude_root=claude_root, codex_root=codex_root)
    if dry_run:
        probe = stats_store.StatsStore(database)
        try:
            buckets = probe.query_buckets()
            output_only = sum(1 for bucket in buckets if _bucket_selected(bucket, effective_start, end) and _has_output_scalar(bucket))
            live = sum(1 for bucket in buckets if _bucket_selected(bucket, effective_start, end) and _has_live_metrics(bucket))
        finally:
            probe.close()
        return RebuildReport(
            str(database), "", True, include_live, len(families), sum(len(family.paths) for family in families), 0, 0, 0,
            output_only, 0, 0, live, [item.__dict__ for item in stopped],
        )
    if progress:
        progress(f"discovered {len(families)} transcript families")
    with exclusive_stats_locks(database, socket_path):
        blockers = safety_blockers(database, socket_path, now=sample_now)
        if blockers:
            raise StatsRebuildSafetyError("exclusive rebuild recheck failed:\n- " + "\n- ".join(blockers))
        service = PersistentStatsService(socket_path, database, pricing_catalog=catalog or PricingCatalog())
        connection = service.store._connection()
        backup = _backup_database(connection, database, now=sample_now)
        buckets = service.store.query_buckets()
        next_sequence = int(service.store.diagnostics().get("sequence") or 0)
        selected_keys: set[tuple[int, int]] = set()
        live_preserved = 0
        atoms_by_bucket: dict[tuple[int, int], list[dict[str, Any]]] = {}
        seen_atoms: set[tuple[str, str, str, str, str]] = set()
        duplicate_atoms = 0
        transcript_files: set[Path] = set()
        with connection:
            for bucket in buckets:
                if not _bucket_selected(bucket, effective_start, end):
                    continue
                key = (int(bucket["start"]), int(bucket["duration"]))
                selected_keys.add(key)
                had_live = _has_live_metrics(bucket)
                if had_live and not include_live:
                    live_preserved += 1
                if include_live:
                    _clear_live_metrics(bucket)
                replacement: dict[str, Any] = {}
                service._recalculate_usage_summary(replacement, [], legacy_output_only=_has_output_scalar(bucket))
                service._apply_pricing_catalog_metadata(replacement)
                bucket["cost_summary"] = replacement
                next_sequence += 1
                bucket["sequence"] = max(int(bucket.get("sequence") or 0), next_sequence)
                bucket["server_sequence"] = max(int(bucket.get("server_sequence") or 0), next_sequence)
                service.store._upsert_bucket(connection, bucket)
            for index, family in enumerate(families, start=1):
                transcript_files.update(family.paths)
                watermarks: dict[str, int] = {}
                for path in family.paths:
                    try:
                        watermarks[str(_resolved(path))] = path.stat().st_size
                    except OSError:
                        continue
                for atom in session_files.transcript_usage_atoms(
                    family.root,
                    family.kind,
                    family_paths=list(family.paths),
                    max_bytes_by_path=watermarks,
                ):
                    timestamp = float(atom.timestamp)
                    if not _atom_selected(timestamp, effective_start, end):
                        continue
                    normalized = normalized_usage_atom(atom)
                    if normalized is None:
                        continue
                    identity = (
                        normalized["event_id"], normalized["direction"], normalized["modality"],
                        normalized["cache_role"], normalized["unit"],
                    )
                    if identity in seen_atoms:
                        duplicate_atoms += 1
                        continue
                    seen_atoms.add(identity)
                    normalized["backfill_source"] = str(family.root)
                    duration = service._bucket_seconds(timestamp, sample_now)
                    key = (int(math.floor(timestamp / duration) * duration), duration)
                    atoms_by_bucket.setdefault(key, []).append(normalized)
                if progress and (index == len(families) or index % 25 == 0):
                    progress(f"parsed {index}/{len(families)} transcript families")
            rebuilt = 0
            for key, atoms in sorted(atoms_by_bucket.items()):
                bucket = service.store.bucket(*key) or stats_store.empty_bucket(*key)
                # Retained buckets are at most one minute, so every atom in a
                # bucket belongs to one effective-date pricing interval. Use
                # statsd's dimensional backfill coalescer before catalog lookup
                # to avoid one SQLite pricing query per verbose event atom.
                coalesced_atoms = service._coalesced_backfill_components(atoms)
                projected = [projected_usage_component(atom, service.pricing_catalog) for atom in coalesced_atoms]
                components = service._coalesced_backfill_components([item for item in projected if item is not None])
                if not components:
                    continue
                replacement: dict[str, Any] = {}
                service._recalculate_usage_summary(replacement, components)
                service._apply_pricing_catalog_metadata(replacement)
                bucket["cost_summary"] = replacement
                next_sequence += 1
                bucket["sequence"] = max(int(bucket.get("sequence") or 0), next_sequence)
                bucket["server_sequence"] = max(int(bucket.get("server_sequence") or 0), next_sequence)
                service.store._upsert_bucket(connection, bucket)
                rebuilt += 1
            status = {
                "state": "complete",
                "completed_at": sample_now,
                "mode": "offline_rebuild",
                "sources": len(families),
                "missing": 0,
            }
            connection.execute(
                "INSERT INTO schema_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (STATSD_USAGE_ATOM_MIGRATION_MARKER, str(STATSD_USAGE_ATOM_MIGRATION_VERSION)),
            )
            connection.execute(
                "INSERT INTO schema_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (STATSD_USAGE_ATOM_MIGRATION_STATUS_KEY, json.dumps(status, sort_keys=True)),
            )
        rebuilt_buckets = service.store.query_buckets()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise sqlite3.DatabaseError(f"post-rebuild integrity check failed: {integrity}")
        output_only = 0
        truncated = 0
        violations = 0
        for bucket in rebuilt_buckets:
            if not _bucket_selected(bucket, effective_start, end):
                continue
            summary = bucket.get("cost_summary") if isinstance(bucket.get("cost_summary"), dict) else {}
            components = summary.get("components") if isinstance(summary.get("components"), list) else []
            if not components and _has_output_scalar(bucket):
                output_only += 1
            if summary.get("truncated"):
                truncated += 1
            billable, output = _usage_totals(bucket)
            if billable + 1e-9 < output:
                violations += 1
        service.store.close()
    return RebuildReport(
        str(database), str(backup), False, include_live, len(families), len(transcript_files), len(seen_atoms),
        duplicate_atoms, rebuilt, output_only, truncated, violations, live_preserved,
        [item.__dict__ for item in stopped],
    )
