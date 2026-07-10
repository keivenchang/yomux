from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import random
import re
import subprocess
import threading
import time
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Callable

import auto_approve_tmux

from .agent_tui import AgentPaneState
from .agent_tui import classify_agent_pane
from .auto_approve_policy import AUTO_APPROVE_QUIET_JITTER_SECONDS
from .auto_approve_policy import AUTO_APPROVE_QUIET_MAX_INTERVAL_SECONDS
from .auto_approve_policy import auto_approve_poll_is_quiet
from .auto_approve_policy import auto_approve_quiet_poll_interval
from . import yolo_rules
from .common import AUTO_APPROVE_LOCK_DIR
from .common import PROJECT_ROOT
from .common import SERVER_HOSTNAME
from .common import truncate_text
from .locales import message_fields
from .types import AutoApproveState

# stop() joins for at least this long so an in-flight capture + keystroke walk (~0.6s) and
# the post-approval / max-interval sleep (interruptible via stop_event, but the send is not) can finish
# and the thread can release its flock before a takeover re-acquires.
AUTO_APPROVE_STOP_JOIN_SECONDS = 5.0
AUTO_APPROVE_MISSING_CAPTURE_LIMIT = 3


LOGGER = logging.getLogger(__name__)
EXPECTED_AUTO_APPROVE_ERRORS = (OSError, subprocess.SubprocessError, TimeoutError, json.JSONDecodeError)
EXPECTED_EVENT_CALLBACK_ERRORS = (OSError,)


def auto_approve_lock_path(target: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", target).strip("._-")[:60] or "session"
    digest = hashlib.sha256(target.encode("utf-8")).hexdigest()[:12]
    return AUTO_APPROVE_LOCK_DIR / f"auto-approve-{safe}-{digest}.lock"


def auto_approve_lock_message(owner: dict[str, Any] | None) -> str:
    if not owner:
        return "locked by another YOLOmux"
    pid = owner.get("pid")
    root = owner.get("project_root")
    if pid and root:
        return f"locked by another YOLOmux pid {pid} ({root})"
    if pid:
        return f"locked by another YOLOmux pid {pid}"
    return "locked by another YOLOmux"


def auto_approve_lock_message_fields(field: str, owner: dict[str, Any] | None) -> dict[str, Any]:
    raw_message = auto_approve_lock_message(owner)
    pid = owner.get("pid") if owner else None
    root = owner.get("project_root") if owner else None
    if pid and root:
        return message_fields(field, "yolo.status.lockedPidRoot", raw_message, {"pid": pid, "root": root})
    if pid:
        return message_fields(field, "yolo.status.lockedPid", raw_message, {"pid": pid})
    return message_fields(field, "yolo.status.locked", raw_message)


def auto_approve_lock_owner_payload(target: str, owner_extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "target": target,
        "pid": os.getpid(),
        "hostname": SERVER_HOSTNAME,
        "project_root": str(PROJECT_ROOT),
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    if owner_extra:
        payload.update(owner_extra)
    return payload


def read_auto_approve_lock_owner(handle: Any) -> dict[str, Any]:
    try:
        handle.seek(0)
        payload = json.loads(handle.read() or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def auto_approve_lock_owner(target: str) -> dict[str, Any] | None:
    path = auto_approve_lock_path(target)
    if not path.exists():
        return None
    try:
        with path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                owner = read_auto_approve_lock_owner(handle)
                owner["lock_path"] = str(path)
                return owner
            finally:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
    except OSError:
        return None
    return None


class AutoApproveProcessLock:
    def __init__(self, target: str, owner_extra: dict[str, Any] | None = None):
        self.target = target
        self.path = auto_approve_lock_path(target)
        self.handle: Any = None
        self.owner = auto_approve_lock_owner_payload(target, owner_extra)

    def acquire(self) -> tuple[bool, dict[str, Any] | None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            owner = read_auto_approve_lock_owner(handle)
            owner["lock_path"] = str(self.path)
            handle.close()
            return False, owner
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(self.owner, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        self.handle = handle
        return True, None

    def release(self) -> None:
        if self.handle is None:
            return
        handle = self.handle
        self.handle = None
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class AutoApproveWorker:
    def __init__(
        self,
        target: str,
        interval: float = 0.5,
        event_callback: Any = None,
        owner_extra: dict[str, Any] | None = None,
        dangerously_yolo: bool = False,
        prompt_source: str = "hybrid",
        capture_gate: Callable[[str], bool] | None = None,
    ):
        self.target = target
        self.interval = interval
        self.event_callback = event_callback
        self.dangerously_yolo = dangerously_yolo
        self.prompt_source = prompt_source if prompt_source in {"pane", "hybrid"} else "hybrid"
        self.capture_gate = capture_gate
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name=f"auto-approve-{target}", daemon=True)
        self.lock = threading.Lock()
        self.started_at = time.time()
        self.approved = 0
        self.blocked = 0
        initial_message = message_fields("last_action", "state.starting", "starting")
        self.last_action = str(initial_message["last_action"])
        self.last_action_key = str(initial_message["last_action_key"])
        self.last_action_params = dict(initial_message["last_action_params"])
        self.error: str | None = None
        self.last_hash = ""
        self.last_hash_at = 0.0
        self.last_blocked_hash = ""
        self.pending_prompt = False
        self.missing_capture_count = 0
        self.capture_gate_observed = False
        self.last_screen_key = ""
        self.last_visible_screen_hash = ""
        self.last_poll_is_quiet = False
        self.process_lock = AutoApproveProcessLock(target, owner_extra)
        self.lock_owner: dict[str, Any] | None = None

    def start(self) -> tuple[bool, dict[str, Any] | None]:
        acquired, owner = self.process_lock.acquire()
        if not acquired:
            self.lock_owner = owner
            self.update(error=auto_approve_lock_message(owner), **auto_approve_lock_message_fields("last_action", owner))
            return False, owner
        self.thread.start()
        return True, None

    def stop(self) -> bool:
        # join long enough for the thread to finish any in-flight capture/send (the relative
        # keystroke walk alone is ~0.6s) and exit + release its flock. A 1.0s join could return while the
        # thread is still alive and about to fire ONE more keystroke after a takeover (two workers, one
        # session). Returns True only when the thread has actually exited.
        self.stop_event.set()
        self.thread.join(timeout=AUTO_APPROVE_STOP_JOIN_SECONDS)
        return not self.thread.is_alive()

    def alive(self) -> bool:
        return self.thread.is_alive() and not self.stop_event.is_set()

    def status(self) -> AutoApproveState:
        with self.lock:
            return {
                "target": self.target,
                "enabled": self.alive(),
                "approved": self.approved,
                "blocked": self.blocked,
                "last_action": self.last_action,
                "last_action_key": self.last_action_key,
                "last_action_params": dict(self.last_action_params),
                "error": self.error,
                "started_at": self.started_at,
                "lock_owner": self.lock_owner,
                "prompt_source": self.prompt_source,
            }

    def has_pending_prompt(self) -> bool:
        return self.pending_prompt or bool(self.last_hash) or bool(self.last_blocked_hash)

    def update(self, **values: Any) -> None:
        with self.lock:
            for key, value in values.items():
                setattr(self, key, value)

    def update_last_action(self, key: str, fallback: str, **params: Any) -> None:
        self.update(**message_fields("last_action", key, fallback, params))

    def emit_event(
        self,
        event_type: str,
        message: str,
        *,
        message_key: str = "",
        message_params: dict[str, Any] | None = None,
        **details: Any,
    ) -> None:
        if self.event_callback is None:
            return
        if message_key:
            details["message_key"] = message_key
            details["message_params"] = dict(message_params or {})
        try:
            self.event_callback(self.target, event_type, message, details)
        except EXPECTED_EVENT_CALLBACK_ERRORS as exc:
            LOGGER.warning("auto approve event callback failed for %s %s: %s", self.target, event_type, exc)
            return

    def run(self) -> None:
        try:
            module = auto_approve_tmux
            idle_since: float | None = None
            self.update(lock_owner=self.process_lock.owner)
            self.update_last_action("yolo.status.watching", "watching")

            while not self.stop_event.is_set():
                try:
                    acted = self.process_once(module)
                    if acted or not self.last_poll_is_quiet:
                        idle_since = None
                        wait_for = self.interval
                    else:
                        now = time.monotonic()
                        if idle_since is None:
                            idle_since = now
                        idle_secs = now - idle_since
                        wait_for = auto_approve_quiet_poll_interval(
                            self.interval,
                            idle_secs,
                            random.uniform(-AUTO_APPROVE_QUIET_JITTER_SECONDS, AUTO_APPROVE_QUIET_JITTER_SECONDS),
                        )
                    self.stop_event.wait(wait_for)
                except EXPECTED_AUTO_APPROVE_ERRORS as exc:
                    self.update(error=str(exc))
                    self.update_last_action("yolo.status.autoApproveError", "auto approve error")
                    self.emit_event(
                        "worker_error",
                        "auto approve error",
                        message_key="yolo.status.autoApproveError",
                        error=str(exc),
                    )
                    self.stop_event.wait(AUTO_APPROVE_QUIET_MAX_INTERVAL_SECONDS)
        finally:
            self.process_lock.release()

    def process_once(self, module: Any) -> bool:
        self.last_poll_is_quiet = False
        if self.capture_gate is not None and self.capture_gate_observed and not self.pending_prompt and not self.last_hash and not self.last_blocked_hash:
            try:
                should_capture = self.capture_gate(self.target)
            except EXPECTED_AUTO_APPROVE_ERRORS as exc:
                self.update(error=str(exc))
                self.update_last_action("yolo.status.activityGateError", "tmux activity gate error")
                should_capture = True
            if should_capture is False:
                self.update_last_action("yolo.status.activityQuiet", "idle; tmux activity quiet")
                self.last_poll_is_quiet = self.last_screen_key != "working"
                return False

        visible_text = module.tmux_capture_pane(self.target, visible_only=True)
        if visible_text is None:
            self.missing_capture_count += 1
            if self.missing_capture_count >= AUTO_APPROVE_MISSING_CAPTURE_LIMIT:
                self.update_last_action("yolo.status.sessionVanished", "session vanished; auto approve stopped")
                self.emit_event(
                    "worker_stopped",
                    "auto approve stopped because the tmux session vanished",
                    message_key="events.message.yolo.sessionVanished",
                    failures=self.missing_capture_count,
                )
                self.stop_event.set()
            else:
                self.update_last_action("yolo.status.captureFailed", "failed to capture pane")
            return False
        self.missing_capture_count = 0
        self.capture_gate_observed = True

        state, pane_text = self.classify_pane_state(module, visible_text)
        screen_key = str(state.screen.get("key") or "")
        visible_hash = hashlib.sha256(visible_text.encode("utf-8", errors="replace")).hexdigest()
        screen_changed = bool(self.last_visible_screen_hash) and visible_hash != self.last_visible_screen_hash
        self.last_visible_screen_hash = visible_hash
        self.last_screen_key = screen_key
        self.last_poll_is_quiet = auto_approve_poll_is_quiet(screen_key, screen_changed)
        prompt_state = state.prompt
        approval_state = state.approval or {}
        if state.reason_code == "needs-input":
            self.last_hash = ""
            self.last_hash_at = 0.0
            self.last_blocked_hash = ""
            self.pending_prompt = False
            self.update_last_action("yolo.status.questionVisible", "question visible; waiting for manual answer")
            return False
        prompt_type = str(approval_state.get("approval_type") or prompt_state.get("type") or "")
        if not prompt_type or not approval_state.get("approval_visible"):
            self.last_hash = ""
            self.last_hash_at = 0.0
            self.last_blocked_hash = ""
            self.pending_prompt = False
            reason = str(prompt_state.get("reason") or prompt_state.get("negative_reason") or state.screen.get("text") or "")
            if reason:
                self.update_last_action("yolo.status.idleReason", f"idle; {reason}", reason=reason)
            else:
                self.update_last_action("yolo.status.idle", "idle")
            return False
        self.pending_prompt = True

        action_value = approval_state.get("approval_action") or prompt_state.get("action")
        action = action_value if isinstance(action_value, str) and action_value else None
        selected_value = approval_state.get("selected_option") or prompt_state.get("selected_option")
        selected_option = selected_value if isinstance(selected_value, int) else 0
        prompt_source = str(approval_state.get("source") or prompt_state.get("source") or "pane")
        if not prompt_state.get("yes_selected") and selected_option <= 0:
            self.update_last_action(
                "yolo.status.noSelectableApproval",
                "prompt found, no selectable approval option is highlighted",
            )
            return False

        current_hash = str(approval_state.get("prompt_hash") or prompt_state.get("hash") or "")
        now = time.monotonic()
        if current_hash == self.last_blocked_hash:
            self.update_last_action("yolo.status.blockedPromptVisible", "blocked prompt still visible; waiting for manual action")
            return False
        if current_hash == self.last_hash and now - self.last_hash_at < module.PROMPT_RETRY_SECONDS:
            self.update_last_action("yolo.status.approvedPromptVisible", "approved prompt still visible; waiting before retry")
            return False
        if current_hash == self.last_hash:
            seconds = f"{module.PROMPT_RETRY_SECONDS:g}"
            self.update_last_action(
                "yolo.status.approvedPromptRetry",
                f"approved prompt still visible after {seconds}s; retrying",
                seconds=seconds,
            )

        if prompt_type == "bash":
            command_value = approval_state.get("command") or prompt_state.get("command")
            command = command_value if isinstance(command_value, str) and command_value.strip() else None
            return self.handle_bash_prompt(module, pane_text, current_hash, action, selected_option, command=command, prompt_source=prompt_source)
        if prompt_type in {"file", "plan", "tool"}:
            rule_input = str(approval_state.get("rule_input_text") or prompt_state.get("text") or "")
            return self.handle_non_bash_prompt(module, rule_input, current_hash, action, prompt_type, selected_option, prompt_source=prompt_source)
        self.update_last_action("yolo.status.unknownPromptType", f"unknown prompt type: {prompt_type}", promptType=prompt_type)
        return False

    def classify_pane_state(self, module: Any, visible_text: str) -> tuple[AgentPaneState, str]:
        pane_capture: dict[str, str | None] = {"text": None}

        def capture_func(_target: str, visible_only: bool = False) -> str | None:
            if visible_only:
                return visible_text
            if pane_capture["text"] is None:
                pane_capture["text"] = module.tmux_capture_pane(self.target)
            return pane_capture["text"] or visible_text

        def capture_styled_func(_target: str, visible_only: bool = False) -> str:
            return ""

        def prompt_classifier(prompt_target: str, current_visible_text: str, pane_text: str | None, prompt_source: str) -> dict[str, Any]:
            if hasattr(module, "hybrid_approval_prompt_state"):
                state = module.hybrid_approval_prompt_state(prompt_target, current_visible_text, pane_text, prompt_source=prompt_source)
            else:
                state = module.approval_prompt_state(current_visible_text)
            if state.get("type") and "visible" not in state:
                state = dict(state)
                state["visible"] = True
            return state

        state = classify_agent_pane(
            self.target,
            session=self.target.split(":", 1)[0],
            prompt_source=self.prompt_source,
            include_composer=False,
            include_cursor=False,
            include_transcript_activity=False,
            capture_full_for_bash=True,
            capture_func=capture_func,
            capture_styled_func=capture_styled_func,
            prompt_classifier=prompt_classifier,
        )
        return state, pane_capture["text"] or visible_text

    def send_action(self, module: Any, action: str | None, selected_option: int = 1) -> bool:
        option = 2 if action == "option2" else 1
        can_verify = hasattr(module, "tmux_capture_pane") and hasattr(module, "selected_prompt_option")
        # Pre-walk: don't even start the walk if the highlight already moved off the expected option.
        if selected_option > 0 and can_verify:
            current_option = module.selected_prompt_option(module.tmux_capture_pane(self.target, visible_only=True) or "")
            if current_option > 0 and current_option != selected_option:
                self.update_last_action(
                    "yolo.status.approvalOptionMoved",
                    f"approval option moved from {selected_option} to {current_option}; waiting for next capture",
                    fromOption=selected_option,
                    toOption=current_option,
                )
                return False
        # walk the highlight to the target, then RE-VERIFY it landed there before pressing
        # Enter — the menu can redraw/move during the ~0.6s relative walk, so confirm the FINAL state
        # instead of confirming blind (which could pick the wrong option, even "No"). Abort+retry if it
        # is not on the target (or the highlight is unreadable).
        if can_verify and hasattr(module, "tmux_move_to_option") and hasattr(module, "tmux_send_enter"):
            if self.stop_event.is_set():
                return False
            module.tmux_move_to_option(self.target, option, selected_option)
            confirmed = module.selected_prompt_option(module.tmux_capture_pane(self.target, visible_only=True) or "")
            if confirmed != option:
                actual = confirmed or "none"
                self.update_last_action(
                    "yolo.status.approvalHighlightMismatch",
                    f"approval highlight is {actual}, expected {option} after move; not confirming",
                    actual=actual,
                    expected=option,
                )
                return False
            if self.stop_event.is_set():
                return False
            module.tmux_send_enter(self.target)
            return True
        if hasattr(module, "tmux_send_option"):
            module.tmux_send_option(self.target, option, selected_option)
            return True
        if action == "option2":
            module.tmux_send_option2(self.target, selected_option)
        elif selected_option > 1 and hasattr(module, "tmux_send_option1"):
            module.tmux_send_option1(self.target, selected_option)
        else:
            module.tmux_send_enter(self.target)
        return True

    def handle_bash_prompt(
        self,
        module: Any,
        pane_text: str,
        current_hash: str,
        action: str | None,
        selected_option: int = 1,
        command: str | None = None,
        prompt_source: str = "pane",
    ) -> bool:
        cmd = command or module.extract_command(pane_text)
        desc = "bash command" if cmd is None else truncate_text(cmd, 180)
        decision = yolo_rules.evaluate(cmd or "", "bash", "", self.target, dangerously_yolo=self.dangerously_yolo)
        rule_action = decision.get("action") if isinstance(decision.get("action"), str) else "ask"
        if rule_action not in yolo_rules.RULE_ACTIONS:
            rule_action = "ask"
        details = {
            "command": truncate_text(cmd, 1000) if cmd else None,
            "prompt_type": "bash",
            "action": rule_action,
            "rule_name": decision.get("rule_name") or "unknown",
            "rule_name_key": decision.get("rule_name_key") or "",
            "rule_name_params": decision.get("rule_name_params") or {},
            "risk": yolo_rules.normalize_risk(decision.get("risk")),
            "ruleset_source": decision.get("source") or "",
            "ruleset_source_key": decision.get("source_key") or "",
            "ruleset_source_params": decision.get("source_params") or {},
            "ruleset_path": decision.get("path") or "",
            "dry_run": decision.get("dry_run") is True,
            "would_action": decision.get("would_action") or "",
            "prompt_source": prompt_source,
        }
        self.update(error=decision.get("error") if decision.get("error") else None)

        if rule_action in {"approve", "decline"}:
            send_value = "option2" if rule_action == "decline" else action
            if not self.send_action(module, send_value, selected_option):
                return False
            self.last_hash = current_hash
            self.last_hash_at = time.monotonic()
            self.last_blocked_hash = ""
            self.approved += 1
            verb = "declined" if rule_action == "decline" else "approved"
            message_key = "yolo.status.declinedBash" if rule_action == "decline" else "yolo.status.approvedBash"
            message_params = {"description": desc}
            self.update_last_action(message_key, f"{verb} bash: {desc}", **message_params)
            self.emit_event(
                "approval_approved",
                f"{verb} bash: {desc}",
                message_key=message_key,
                message_params=message_params,
                **details,
            )
            self.stop_event.wait(3.0)
            return True

        if rule_action in yolo_rules.PASSIVE_RULE_ACTIONS:
            self.last_hash = current_hash
            self.last_hash_at = time.monotonic()
            self.last_blocked_hash = current_hash
            self.blocked += 1
            if decision.get("would_action"):
                last_action = f"dry-run would {decision['would_action']} bash: {desc}"
                last_action_key = "yolo.status.dryRunBash"
                last_action_params = {"action": decision["would_action"], "description": desc}
            elif rule_action == "block":
                last_action = f"blocked bash: {desc}"
                last_action_key = "yolo.status.blockedBash"
                last_action_params = {"description": desc}
            elif rule_action == "notify":
                last_action = f"notified bash: {desc}"
                last_action_key = "yolo.status.notifiedBash"
                last_action_params = {"description": desc}
            elif rule_action == "off":
                last_action = f"YOLO off by rule: {desc}"
                last_action_key = "yolo.status.offByRuleBash"
                last_action_params = {"description": desc}
            else:
                last_action = f"asked for manual bash approval: {desc}"
                last_action_key = "yolo.status.manualBash"
                last_action_params = {"description": desc}
            self.update_last_action(last_action_key, last_action, **last_action_params)
            self.emit_event(
                "approval_blocked",
                last_action,
                message_key=last_action_key,
                message_params=last_action_params,
                **details,
            )
            return True

        self.update_last_action(
            "yolo.status.unknownAction",
            f"unknown YOLO action {rule_action}; waiting for manual approval",
            action=rule_action,
        )
        return False

    def approve_prompt(self, module: Any, current_hash: str, action: str | None, prompt_type: str, selected_option: int = 1, prompt_source: str = "pane") -> bool:
        if not self.send_action(module, action, selected_option):
            return False
        self.last_hash = current_hash
        self.last_hash_at = time.monotonic()
        self.last_blocked_hash = ""
        self.approved += 1
        opt_label = "option2" if action == "option2" else "option1"
        self.update_last_action(
            "yolo.status.approvedPrompt",
            f"approved {prompt_type}: {opt_label}",
            promptType=prompt_type,
            option=opt_label,
        )
        risk = "edit" if prompt_type == "file" else "unknown"
        self.emit_event(
            "approval_approved",
            f"approved {prompt_type}: {opt_label}",
            message_key="yolo.status.approvedPrompt",
            message_params={"promptType": prompt_type, "option": opt_label},
            prompt_type=prompt_type,
            risk=risk,
            action=opt_label,
            prompt_source=prompt_source,
        )
        self.stop_event.wait(3.0)
        return True

    def handle_non_bash_prompt(self, module: Any, prompt_text: str, current_hash: str, action: str | None, prompt_type: str, selected_option: int = 1, prompt_source: str = "pane") -> bool:
        decision = yolo_rules.evaluate(prompt_text or prompt_type, prompt_type, "", self.target, dangerously_yolo=self.dangerously_yolo)
        rule_action = decision.get("action") if isinstance(decision.get("action"), str) else "ask"
        if rule_action not in yolo_rules.RULE_ACTIONS:
            rule_action = "ask"
        details = {
            "prompt_type": prompt_type,
            "action": rule_action,
            "rule_name": decision.get("rule_name") or "unknown",
            "rule_name_key": decision.get("rule_name_key") or "",
            "rule_name_params": decision.get("rule_name_params") or {},
            "risk": yolo_rules.normalize_risk(decision.get("risk")),
            "ruleset_source": decision.get("source") or "",
            "ruleset_source_key": decision.get("source_key") or "",
            "ruleset_source_params": decision.get("source_params") or {},
            "ruleset_path": decision.get("path") or "",
            "dry_run": decision.get("dry_run") is True,
            "would_action": decision.get("would_action") or "",
            "prompt_source": prompt_source,
        }
        self.update(error=decision.get("error") if decision.get("error") else None)
        if rule_action in {"approve", "decline"}:
            send_value = "option2" if rule_action == "decline" else action
            if not self.send_action(module, send_value, selected_option):
                return False
            self.last_hash = current_hash
            self.last_hash_at = time.monotonic()
            self.last_blocked_hash = ""
            self.approved += 1
            verb = "declined" if rule_action == "decline" else "approved"
            rule_name = decision.get("rule_name") or "rule"
            rule_name_param = yolo_rules.decision_rule_name_descriptor(decision)
            message_key = "yolo.status.declinedPromptRule" if rule_action == "decline" else "yolo.status.approvedPromptRule"
            event_message_key = "events.message.yolo.declinedPrompt" if rule_action == "decline" else "events.message.yolo.approvedPromptType"
            self.update_last_action(
                message_key,
                f"{verb} {prompt_type}: {rule_name}",
                promptType=prompt_type,
                ruleName=rule_name_param,
            )
            self.emit_event(
                "approval_approved",
                f"{verb} {prompt_type}",
                message_key=event_message_key,
                message_params={"promptType": prompt_type},
                **details,
            )
            self.stop_event.wait(3.0)
            return True
        self.last_hash = current_hash
        self.last_hash_at = time.monotonic()
        self.last_blocked_hash = current_hash
        self.blocked += 1
        rule_name = decision.get("rule_name") or "rule"
        rule_name_param = yolo_rules.decision_rule_name_descriptor(decision)
        last_action = f"{rule_action} {prompt_type}: {rule_name}"
        last_action_params = {"action": rule_action, "promptType": prompt_type, "ruleName": rule_name_param}
        self.update_last_action("state.reason.yoloRulePrompt", last_action, **last_action_params)
        self.emit_event(
            "approval_blocked",
            last_action,
            message_key="state.reason.yoloRulePrompt",
            message_params=last_action_params,
            **details,
        )
        return True
