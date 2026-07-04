# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Rolling session summary control flow for Yoagent."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from ..common import MAX_TRANSCRIPT_TAIL_LINES
from ..common import SUMMARY_MAX_PROMPT_CHARS
from ..common import SessionInfo
from ..common import tail_file_lines
from ..common import truncate_text
from ..transcripts import compact_transcript_items_since
from ..transcripts import format_transcript_item
from ..transcripts import newest_transcript_timestamp
from ..transcripts import trim_prompt_text


YOAGENT_SESSION_SUMMARIES_STATE_KEY = "yoagent_session_summaries"
YOAGENT_SESSION_SUMMARY_STATES = {"working", "waiting", "blocked", "done", "idle"}
YOAGENT_SESSION_SUMMARY_QUIET_SECONDS = 15.0
YOAGENT_SESSION_SUMMARY_MAX_ITEMS = 120


@dataclass
class YoagentSummaryWorkerRecord:
    worker: threading.Thread | None = None
    running: bool = False
    first_launch_started: bool = False


class YoagentSessionSummariesMixin:
    def sanitized_yoagent_session_summaries(self, value: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(value, dict):
            return {}
        clean: dict[str, dict[str, Any]] = {}
        for session, item in value.items():
            if not isinstance(session, str) or not isinstance(item, dict):
                continue
            summary = truncate_text(str(item.get("rolling_summary") or "").strip(), 1200)
            if not summary:
                continue
            state = str(item.get("state") or "idle").strip().lower()
            if state not in YOAGENT_SESSION_SUMMARY_STATES:
                state = "idle"
            clean[session] = {
                "rolling_summary": summary,
                "last_processed_ts": max(0.0, self.float_value(item.get("last_processed_ts"), 0.0)),
                "updated_ts": max(0.0, self.float_value(item.get("updated_ts"), 0.0)),
                "state": state,
            }
        return clean


    def load_yoagent_session_summaries(self) -> None:
        state = self.deps.read_yolomux_state()
        with self.yoagent_session_summary_lock:
            self.yoagent_session_summaries = self.deps.sanitized_yoagent_session_summaries(
                state.get(YOAGENT_SESSION_SUMMARIES_STATE_KEY)
            )


    def persist_yoagent_session_summaries_locked(self) -> None:
        self.deps.update_yolomux_state({YOAGENT_SESSION_SUMMARIES_STATE_KEY: self.yoagent_session_summaries})


    def prune_yoagent_session_summaries(self, live_sessions: set[str]) -> None:
        with self.yoagent_session_summary_lock:
            stale = [session for session in self.yoagent_session_summaries if session not in live_sessions]
            if not stale:
                return
            for session in stale:
                self.yoagent_session_summaries.pop(session, None)
            self.deps.persist_yoagent_session_summaries_locked()


    def attach_yoagent_session_summary(self, session: str, summary: dict[str, Any]) -> None:
        with self.yoagent_session_summary_lock:
            rolling = dict(self.yoagent_session_summaries.get(session) or {})
        if not rolling.get("rolling_summary"):
            return
        summary["rolling_summary"] = rolling["rolling_summary"]
        summary["rolling_state"] = rolling.get("state") or "idle"
        summary["rolling_updated_ts"] = rolling.get("updated_ts") or 0


    def latest_yoagent_session_summary_updated_ts(self) -> float:
        with self.yoagent_session_summary_lock:
            return max((float(item.get("updated_ts") or 0) for item in self.yoagent_session_summaries.values()), default=0.0)


    def build_yoagent_session_summary_update_prompt(
        self,
        session: str,
        transcript_path: str,
        prior_summary: str,
        transcript_text: str,
    ) -> str:
        return "\n\n".join([
            "Update one YO!agent rolling summary for a single tmux session.",
            f"tmux session: `{session}`",
            f"transcript: `{transcript_path}`",
            "Use the prior summary plus only the new transcript lines below. Do not invent facts.",
            "Return exactly two fields:\nstate: working|waiting|blocked|done|idle\nsummary: 1-3 short factual lines about what this session is doing now.",
            "Prior summary:\n" + (prior_summary or "(none)"),
            "New transcript lines:\n" + transcript_text,
        ])


    def parse_yoagent_session_summary_response(self, text: str, default_state: str = "idle") -> dict[str, str]:
        raw = str(text or "").strip()
        state = default_state if default_state in YOAGENT_SESSION_SUMMARY_STATES else "idle"
        match = re.search(r"(?im)^\s*state\s*:\s*([a-z-]+)\s*$", raw)
        if match and match.group(1).lower() in YOAGENT_SESSION_SUMMARY_STATES:
            state = match.group(1).lower()
        summary_match = re.search(r"(?ims)^\s*summary\s*:\s*(.+)$", raw)
        summary = summary_match.group(1).strip() if summary_match else raw
        summary = re.sub(r"(?im)^\s*state\s*:\s*[a-z-]+\s*$", "", summary).strip()
        return {"state": state, "rolling_summary": truncate_text(summary, 1200)}


    def update_yoagent_session_summary(
        self,
        session: str,
        info: SessionInfo,
        settings: dict[str, Any] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        agent = next((item for item in info.agents if item.transcript), None)
        if agent is None or not agent.transcript:
            return {"session": session, "updated": False, "reason": "no transcript"}
        transcript_path = Path(agent.transcript)
        try:
            text = tail_file_lines(transcript_path, MAX_TRANSCRIPT_TAIL_LINES)
        except OSError as exc:
            return {"session": session, "updated": False, "reason": str(exc)}
        if not force and self.deps.transcript_activity_is_recent(transcript_path, text, recency_seconds=YOAGENT_SESSION_SUMMARY_QUIET_SECONDS):
            return {"session": session, "updated": False, "reason": "transcript still active"}
        with self.yoagent_session_summary_lock:
            previous = dict(self.yoagent_session_summaries.get(session) or {})
        last_processed_ts = self.float_value(previous.get("last_processed_ts"), 0.0)
        since = datetime.fromtimestamp(last_processed_ts + 0.000001, timezone.utc) if last_processed_ts > 0 else datetime.fromtimestamp(0, timezone.utc)
        items, stats = compact_transcript_items_since(text, since)
        items = items[-YOAGENT_SESSION_SUMMARY_MAX_ITEMS:]
        newest = newest_transcript_timestamp(text)
        if not items or newest is None:
            return {"session": session, "updated": False, "reason": "no new transcript lines", "stats": stats}
        transcript_text = "\n\n".join(format_transcript_item(item) for item in items)
        transcript_text, truncated = trim_prompt_text(transcript_text, SUMMARY_MAX_PROMPT_CHARS)
        prompt = self.deps.build_yoagent_session_summary_update_prompt(
            session,
            str(transcript_path),
            str(previous.get("rolling_summary") or ""),
            transcript_text,
        )
        current_settings = settings or self.yoagent_settings()
        requested_backend = str(current_settings.get("backend") or "deterministic").strip().lower()
        backend = self.deps.resolve_yoagent_backend(requested_backend)
        invocation = str(current_settings.get("invocation") or "cli").strip().lower()
        if backend not in {"codex", "claude"} or invocation != "cli":
            return {"session": session, "updated": False, "reason": "no CLI backend available", "backend": backend}
        answer, fallback_reason, cli_status = self.deps.run_yoagent_direct_prompt_backend(backend, prompt, settings=current_settings)
        if not answer:
            return {"session": session, "updated": False, "reason": fallback_reason or "empty summary response", "backend": backend, "cli": cli_status}
        parsed = self.deps.parse_yoagent_session_summary_response(answer, default_state="working" if agent.status == "running" else "idle")
        if not parsed["rolling_summary"]:
            return {"session": session, "updated": False, "reason": "empty parsed summary", "backend": backend, "cli": cli_status}
        now = time.time()
        record = {
            "rolling_summary": parsed["rolling_summary"],
            "last_processed_ts": newest.timestamp(),
            "updated_ts": now,
            "state": parsed["state"],
        }
        with self.yoagent_session_summary_lock:
            self.yoagent_session_summaries[session] = record
            self.deps.persist_yoagent_session_summaries_locked()
        return {
            "session": session,
            "updated": True,
            "backend": backend,
            "state": parsed["state"],
            "items": len(items),
            "truncated": truncated,
            "stats": stats,
            "cli": cli_status,
        }


    def tick_yoagent_session_summaries(self, settings: dict[str, Any] | None = None, *, force: bool = False) -> dict[str, Any]:
        current_settings = settings or self.yoagent_settings()
        sessions, errors = self.deps.discover_sessions(self.sessions)
        self.deps.prune_yoagent_session_summaries(set(sessions))
        updated: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for session in self.tmux_recency_ordered_sessions(self.sessions):
            info = sessions.get(session)
            if info is None:
                continue
            result = self.deps.update_yoagent_session_summary(session, info, current_settings, force=force)
            if result.get("updated"):
                updated.append(result)
            else:
                skipped.append(result)
        return {"enabled": True, "mode": "first_launch", "updated": updated, "skipped": skipped, "errors": errors}


    def maybe_start_yoagent_summary_worker(self) -> None:
        with self.yoagent_summary_worker_lock:
            current = self.yoagent_summary_worker_record
            if current.running or current.first_launch_started:
                return
            record = YoagentSummaryWorkerRecord(running=True, first_launch_started=True)
            worker = threading.Thread(
                target=lambda: self.yoagent_summary_worker_loop(record),
                name="yoagent-summary-first-launch",
                daemon=True,
            )
            record.worker = worker
            self.yoagent_summary_worker_record = record
        try:
            worker.start()
        except RuntimeError:
            with self.yoagent_summary_worker_lock:
                if self.yoagent_summary_worker_record is record and record.worker is worker:
                    self.yoagent_summary_worker_record = YoagentSummaryWorkerRecord()
            raise


    def yoagent_summary_worker_loop(self, record: YoagentSummaryWorkerRecord | None = None) -> None:
        current = record or self.yoagent_summary_worker_record
        try:
            self.deps.tick_yoagent_session_summaries(self.yoagent_settings(), force=True)
        finally:
            with self.yoagent_summary_worker_lock:
                if self.yoagent_summary_worker_record is current:
                    current.worker = None
                    current.running = False
