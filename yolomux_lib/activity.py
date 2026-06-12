"""Per-session/window user + agent activity ledger (DOIT.58 Phase 1).

WakaTime/ActivityWatch model: callers emit cheap HEARTBEATS (a timestamp + target,
nothing else); typed-time is the coalesced sum of inter-heartbeat gaps, each gap
CAPPED at an idle threshold so leaving a pane idle never inflates the total. There
are no stopwatches and no start/stop events.

Aggregates are keyed by a target string: a session (``"6"``) and each window
(``"6:1"``). A window heartbeat also bumps its session key, so the session total is
the roll-up of all its windows. Only COUNTS and TIMESTAMPS are recorded — keystroke
content is never stored (privacy + size). The Tabber (Phase 2) sorts on the
timestamps; a future Statistics pane consumes the same ledger, which is why this
records more (events, bytes, agent-active, output, selected) than the Tabber needs.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .atomic_file import atomic_write_text, file_lock


logger = logging.getLogger(__name__)


@dataclass
class ActivityRecord:
    """Aggregated activity for one target key (a session or a session:window)."""

    created_ts: float = 0.0
    last_user_input_ts: float = 0.0
    total_user_input_ms: int = 0
    input_events: int = 0
    input_bytes: int = 0
    last_agent_active_ts: float = 0.0
    agent_active_ms: int = 0
    last_output_ts: float = 0.0
    last_selected_ts: float = 0.0


def session_of(target: str) -> str:
    """The session part of a target key (``"6:1"`` -> ``"6"``)."""
    return str(target).split(":", 1)[0]


class ActivityLedger:
    """Thread-safe heartbeat ledger with atomic JSON persistence.

    idle_gap_seconds bounds how much a single inter-heartbeat gap can contribute to
    typed-time; a gap larger than it is the user having walked away, so only the
    idle gap (not the raw gap) is counted before the clock effectively restarts.
    """

    def __init__(
        self,
        path: Path,
        heartbeat_path: Path | None = None,
        idle_gap_seconds: float = 120.0,
        retention_days: float = 14.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.heartbeat_path = Path(heartbeat_path) if heartbeat_path else None
        self.idle_gap_ms = max(0.0, float(idle_gap_seconds) * 1000.0)
        self.retention_ms = max(0.0, float(retention_days) * 86_400_000.0)
        self._clock = clock
        self._records: dict[str, ActivityRecord] = {}
        self._lock = threading.RLock()

    # ---- recording -------------------------------------------------------

    def _bump_input(self, key: str, ts: float, byte_count: int) -> None:
        rec = self._records.get(key)
        if rec is None:
            rec = ActivityRecord(created_ts=ts)
            self._records[key] = rec
        if rec.last_user_input_ts > 0:
            gap_ms = (ts - rec.last_user_input_ts) * 1000.0
            if gap_ms > 0:
                rec.total_user_input_ms += int(min(gap_ms, self.idle_gap_ms))
        rec.last_user_input_ts = ts
        rec.input_events += 1
        rec.input_bytes += max(0, int(byte_count))

    def _bump_agent(self, key: str, ts: float) -> None:
        rec = self._records.get(key)
        if rec is None:
            rec = ActivityRecord(created_ts=ts)
            self._records[key] = rec
        if rec.last_agent_active_ts > 0:
            gap_ms = (ts - rec.last_agent_active_ts) * 1000.0
            if gap_ms > 0:
                rec.agent_active_ms += int(min(gap_ms, self.idle_gap_ms))
        rec.last_agent_active_ts = ts

    def heartbeat(
        self,
        session: str,
        window: str | None = None,
        ts: float | None = None,
        byte_count: int = 0,
    ) -> None:
        """Record one user-input heartbeat. Bumps the session key and, when a window
        is known, the ``session:window`` key (which rolls up via the session bump)."""
        if not session:
            return
        moment = self._clock() if ts is None else float(ts)
        with self._lock:
            self._bump_input(str(session), moment, byte_count)
            if window not in (None, ""):
                self._bump_input(f"{session}:{window}", moment, byte_count)
            self._append_heartbeat(session, window, moment, byte_count)

    def note_agent_active(self, session: str, window: str | None = None, ts: float | None = None) -> None:
        if not session:
            return
        moment = self._clock() if ts is None else float(ts)
        with self._lock:
            self._bump_agent(str(session), moment)
            if window not in (None, ""):
                self._bump_agent(f"{session}:{window}", moment)

    def note_output(self, target: str, ts: float | None = None) -> None:
        moment = self._clock() if ts is None else float(ts)
        with self._lock:
            rec = self._records.get(str(target)) or ActivityRecord(created_ts=moment)
            rec.last_output_ts = moment
            self._records[str(target)] = rec

    def note_selected(self, target: str, ts: float | None = None) -> None:
        moment = self._clock() if ts is None else float(ts)
        with self._lock:
            rec = self._records.get(str(target)) or ActivityRecord(created_ts=moment)
            rec.last_selected_ts = moment
            self._records[str(target)] = rec

    # ---- maintenance -----------------------------------------------------

    def prune(self, live_sessions: set[str]) -> None:
        """Drop every key whose session is no longer live."""
        live = {str(s) for s in (live_sessions or set())}
        with self._lock:
            for key in [k for k in self._records if session_of(k) not in live]:
                del self._records[key]

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {key: asdict(rec) for key, rec in self._records.items()}

    # ---- persistence -----------------------------------------------------

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("resetting unreadable activity ledger %s: %s", self.path, exc)
            with self._lock:
                self._records.clear()
            return
        if not isinstance(raw, dict):
            logger.warning("resetting invalid activity ledger %s: expected object", self.path)
            with self._lock:
                self._records.clear()
            return
        records = raw.get("records", {}) if isinstance(raw, dict) else {}
        if not isinstance(records, dict):
            logger.warning("resetting invalid activity ledger %s: records must be an object", self.path)
            with self._lock:
                self._records.clear()
            return
        valid = {f.name for f in ActivityRecord.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        with self._lock:
            self._records.clear()
            for key, data in records.items():
                if isinstance(data, dict):
                    self._records[str(key)] = ActivityRecord(**{k: v for k, v in data.items() if k in valid})

    def flush(self) -> None:
        with self._lock:
            payload = {"records": {key: asdict(rec) for key, rec in self._records.items()}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.path):
            atomic_write_text(self.path, json.dumps(payload, separators=(",", ":")))

    def _append_heartbeat(self, session: str, window: str | None, ts: float, byte_count: int) -> None:
        if not self.heartbeat_path:
            return
        line = json.dumps({"ts": ts, "s": session, "w": window, "b": max(0, int(byte_count))}, separators=(",", ":"))
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.heartbeat_path):
            with open(self.heartbeat_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def rotate_heartbeats(self, now: float | None = None) -> int:
        """Drop heartbeat-log lines older than the retention window. Returns kept count."""
        if not self.heartbeat_path or not self.heartbeat_path.exists() or self.retention_ms <= 0:
            return 0
        cutoff = (self._clock() if now is None else float(now)) - (self.retention_ms / 1000.0)
        with file_lock(self.heartbeat_path):
            kept: list[str] = []
            for line in self.heartbeat_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    if float(json.loads(line).get("ts", 0)) >= cutoff:
                        kept.append(line)
                except ValueError:
                    continue
            atomic_write_text(self.heartbeat_path, ("\n".join(kept) + "\n") if kept else "")
        return len(kept)
