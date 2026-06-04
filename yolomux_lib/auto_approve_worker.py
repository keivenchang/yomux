from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

import auto_approve_tmux

from . import yolo_rules
from .common import AUTO_APPROVE_LOCK_DIR
from .common import PROJECT_ROOT
from .common import SERVER_HOSTNAME
from .common import truncate_text

# DOIT.6 #70: stop() joins for at least this long so an in-flight capture + keystroke walk (~0.6s) and
# the post-approval / max-interval sleep (interruptible via stop_event, but the send is not) can finish
# and the thread can release its flock before a takeover re-acquires.
AUTO_APPROVE_STOP_JOIN_SECONDS = 5.0


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
    ):
        self.target = target
        self.interval = interval
        self.event_callback = event_callback
        self.dangerously_yolo = dangerously_yolo
        self.prompt_source = prompt_source if prompt_source in {"pane", "hybrid"} else "hybrid"
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name=f"auto-approve-{target}", daemon=True)
        self.lock = threading.Lock()
        self.started_at = time.time()
        self.approved = 0
        self.blocked = 0
        self.last_action = "starting"
        self.error: str | None = None
        self.last_hash = ""
        self.last_hash_at = 0.0
        self.last_blocked_hash = ""
        self.process_lock = AutoApproveProcessLock(target, owner_extra)
        self.lock_owner: dict[str, Any] | None = None

    def start(self) -> tuple[bool, dict[str, Any] | None]:
        acquired, owner = self.process_lock.acquire()
        if not acquired:
            self.lock_owner = owner
            self.update(error=auto_approve_lock_message(owner), last_action="locked by another YOLOmux")
            return False, owner
        self.thread.start()
        return True, None

    def stop(self) -> bool:
        # DOIT.6 #70: join long enough for the thread to finish any in-flight capture/send (the relative
        # keystroke walk alone is ~0.6s) and exit + release its flock. A 1.0s join could return while the
        # thread is still alive and about to fire ONE more keystroke after a takeover (two workers, one
        # session). Returns True only when the thread has actually exited.
        self.stop_event.set()
        self.thread.join(timeout=AUTO_APPROVE_STOP_JOIN_SECONDS)
        return not self.thread.is_alive()

    def alive(self) -> bool:
        return self.thread.is_alive() and not self.stop_event.is_set()

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "target": self.target,
                "enabled": self.alive(),
                "approved": self.approved,
                "blocked": self.blocked,
                "last_action": self.last_action,
                "error": self.error,
                "started_at": self.started_at,
                "lock_owner": self.lock_owner,
                "prompt_source": self.prompt_source,
            }

    def update(self, **values: Any) -> None:
        with self.lock:
            for key, value in values.items():
                setattr(self, key, value)

    def emit_event(self, event_type: str, message: str, **details: Any) -> None:
        if self.event_callback is None:
            return
        try:
            self.event_callback(self.target, event_type, message, details)
        except EXPECTED_EVENT_CALLBACK_ERRORS as exc:
            LOGGER.warning("auto approve event callback failed for %s %s: %s", self.target, event_type, exc)
            return

    def run(self) -> None:
        try:
            module = auto_approve_tmux
            idle_since: float | None = None
            max_interval = max(2.5, self.interval)
            ramp_duration = 60.0
            self.update(last_action="watching", lock_owner=self.process_lock.owner)

            while not self.stop_event.is_set():
                try:
                    acted = self.process_once(module)
                    if acted:
                        idle_since = None
                        wait_for = self.interval
                    else:
                        now = time.monotonic()
                        if idle_since is None:
                            idle_since = now
                        idle_secs = now - idle_since
                        t = min(idle_secs / ramp_duration, 1.0)
                        wait_for = self.interval + t * (max_interval - self.interval)
                    self.stop_event.wait(wait_for)
                except EXPECTED_AUTO_APPROVE_ERRORS as exc:
                    self.update(error=str(exc), last_action="auto approve error")
                    self.emit_event("worker_error", "auto approve error", error=str(exc))
                    self.stop_event.wait(max_interval)
        finally:
            self.process_lock.release()

    def process_once(self, module: Any) -> bool:
        visible_text = module.tmux_capture_pane(self.target, visible_only=True)
        if visible_text is None:
            self.update(last_action="failed to capture pane")
            return False

        if hasattr(module, "hybrid_approval_prompt_state"):
            prompt_state = module.hybrid_approval_prompt_state(self.target, visible_text, prompt_source=self.prompt_source)
        else:
            prompt_state = module.approval_prompt_state(visible_text)
        prompt_type = prompt_state.get("type") or None
        if prompt_type is None:
            self.last_hash = ""
            self.last_hash_at = 0.0
            self.last_blocked_hash = ""
            reason = str(prompt_state.get("reason") or "")
            self.update(last_action=f"idle; {reason}" if reason else "idle")
            return False

        action_value = prompt_state.get("action")
        action = action_value if isinstance(action_value, str) and action_value else None
        selected_value = prompt_state.get("selected_option")
        selected_option = selected_value if isinstance(selected_value, int) else 0
        prompt_source = str(prompt_state.get("source") or "pane")
        if not prompt_state.get("yes_selected") and selected_option <= 0:
            self.update(last_action="prompt found, no selectable approval option is highlighted")
            return False

        pane_text = module.tmux_capture_pane(self.target)
        if pane_text is None:
            pane_text = visible_text
        if prompt_source == "pane" and hasattr(module, "hybrid_approval_prompt_state"):
            prompt_state = module.hybrid_approval_prompt_state(self.target, visible_text, pane_text, prompt_source=self.prompt_source)
            action_value = prompt_state.get("action")
            if isinstance(action_value, str) and action_value:
                action = action_value

        current_hash = str(prompt_state.get("hash") or "")
        now = time.monotonic()
        if current_hash == self.last_blocked_hash:
            self.update(last_action="blocked prompt still visible; waiting for manual action")
            return False
        if current_hash == self.last_hash and now - self.last_hash_at < module.PROMPT_RETRY_SECONDS:
            self.update(last_action="approved prompt still visible; waiting before retry")
            return False
        if current_hash == self.last_hash:
            self.update(last_action=f"approved prompt still visible after {module.PROMPT_RETRY_SECONDS:g}s; retrying")

        if prompt_type == "bash":
            command_value = prompt_state.get("command")
            command = command_value if isinstance(command_value, str) and command_value.strip() else None
            return self.handle_bash_prompt(module, pane_text, current_hash, action, selected_option, command=command, prompt_source=prompt_source)
        if prompt_type in {"file", "tool"}:
            return self.handle_non_bash_prompt(module, str(prompt_state.get("text") or ""), current_hash, action, prompt_type, selected_option, prompt_source=prompt_source)
        self.update(last_action=f"unknown prompt type: {prompt_type}")
        return False

    def send_action(self, module: Any, action: str | None, selected_option: int = 1) -> bool:
        option = 2 if action == "option2" else 1
        can_verify = hasattr(module, "tmux_capture_pane") and hasattr(module, "selected_prompt_option")
        # Pre-walk: don't even start the walk if the highlight already moved off the expected option.
        if selected_option > 0 and can_verify:
            current_option = module.selected_prompt_option(module.tmux_capture_pane(self.target, visible_only=True) or "")
            if current_option > 0 and current_option != selected_option:
                self.update(last_action=f"approval option moved from {selected_option} to {current_option}; waiting for next capture")
                return False
        # DOIT.6 #66: walk the highlight to the target, then RE-VERIFY it landed there before pressing
        # Enter — the menu can redraw/move during the ~0.6s relative walk, so confirm the FINAL state
        # instead of confirming blind (which could pick the wrong option, even "No"). Abort+retry if it
        # is not on the target (or the highlight is unreadable).
        if can_verify and hasattr(module, "tmux_move_to_option") and hasattr(module, "tmux_send_enter"):
            if self.stop_event.is_set():
                return False
            module.tmux_move_to_option(self.target, option, selected_option)
            confirmed = module.selected_prompt_option(module.tmux_capture_pane(self.target, visible_only=True) or "")
            if confirmed != option:
                self.update(last_action=f"approval highlight is {confirmed or 'none'}, expected {option} after move; not confirming")
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
            "risk": decision.get("risk") or "unknown",
            "ruleset_source": decision.get("source") or "",
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
            self.update(last_action=f"{verb} bash: {desc}")
            self.emit_event(
                "approval_approved",
                f"{verb} bash: {desc}",
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
            elif rule_action == "block":
                last_action = f"blocked bash: {desc}"
            elif rule_action == "notify":
                last_action = f"notified bash: {desc}"
            elif rule_action == "off":
                last_action = f"YOLO off by rule: {desc}"
            else:
                last_action = f"asked for manual bash approval: {desc}"
            self.update(last_action=last_action)
            self.emit_event(
                "approval_blocked",
                last_action,
                **details,
            )
            return True

        self.update(last_action=f"unknown YOLO action {rule_action}; waiting for manual approval")
        return False

    def approve_prompt(self, module: Any, current_hash: str, action: str | None, prompt_type: str, selected_option: int = 1, prompt_source: str = "pane") -> bool:
        if not self.send_action(module, action, selected_option):
            return False
        self.last_hash = current_hash
        self.last_hash_at = time.monotonic()
        self.last_blocked_hash = ""
        self.approved += 1
        opt_label = "option2" if action == "option2" else "option1"
        self.update(last_action=f"approved {prompt_type}: {opt_label}")
        risk = "edit" if prompt_type == "file" else "unknown"
        self.emit_event(
            "approval_approved",
            f"approved {prompt_type}: {opt_label}",
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
            "risk": decision.get("risk") or "unknown",
            "ruleset_source": decision.get("source") or "",
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
            self.update(last_action=f"{verb} {prompt_type}: {decision.get('rule_name') or 'rule'}")
            self.emit_event("approval_approved", f"{verb} {prompt_type}", **details)
            self.stop_event.wait(3.0)
            return True
        self.last_hash = current_hash
        self.last_hash_at = time.monotonic()
        self.last_blocked_hash = current_hash
        self.blocked += 1
        last_action = f"{rule_action} {prompt_type}: {decision.get('rule_name') or 'rule'}"
        self.update(last_action=last_action)
        self.emit_event("approval_blocked", last_action, **details)
        return True
