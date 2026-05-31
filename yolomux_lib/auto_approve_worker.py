from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
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
    ):
        self.target = target
        self.interval = interval
        self.event_callback = event_callback
        self.dangerously_yolo = dangerously_yolo
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

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=1.0)

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
        except Exception:
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
                except Exception as exc:
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

        prompt_state = module.approval_prompt_state(visible_text)
        prompt_type = prompt_state.get("type") or None
        if prompt_type is None:
            self.last_hash = ""
            self.last_hash_at = 0.0
            self.last_blocked_hash = ""
            self.update(last_action="idle")
            return False

        if not prompt_state.get("yes_selected"):
            self.update(last_action="prompt found, Yes not selected")
            return False

        pane_text = module.tmux_capture_pane(self.target)
        if pane_text is None:
            pane_text = visible_text

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

        action_value = prompt_state.get("action")
        action = action_value if isinstance(action_value, str) and action_value else None

        if prompt_type == "bash":
            return self.handle_bash_prompt(module, pane_text, current_hash, action)
        if prompt_type in {"file", "tool"}:
            return self.handle_non_bash_prompt(module, str(prompt_state.get("text") or ""), current_hash, action, prompt_type)
        self.update(last_action=f"unknown prompt type: {prompt_type}")
        return False

    def send_action(self, module: Any, action: str | None) -> None:
        if action == "option2":
            module.tmux_send_option2(self.target)
        else:
            module.tmux_send_enter(self.target)

    def handle_bash_prompt(self, module: Any, pane_text: str, current_hash: str, action: str | None) -> bool:
        cmd = module.extract_command(pane_text)
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
        }
        self.update(error=decision.get("error") if decision.get("error") else None)

        if rule_action in {"approve", "decline"}:
            send_value = "option2" if rule_action == "decline" else action
            self.send_action(module, send_value)
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

    def approve_prompt(self, module: Any, current_hash: str, action: str | None, prompt_type: str) -> bool:
        self.send_action(module, action)
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
        )
        self.stop_event.wait(3.0)
        return True

    def handle_non_bash_prompt(self, module: Any, prompt_text: str, current_hash: str, action: str | None, prompt_type: str) -> bool:
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
        }
        self.update(error=decision.get("error") if decision.get("error") else None)
        if rule_action in {"approve", "decline"}:
            send_value = "option2" if rule_action == "decline" else action
            self.send_action(module, send_value)
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
