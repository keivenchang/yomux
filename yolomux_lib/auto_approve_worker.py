from __future__ import annotations

from .common import *


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
    def __init__(self, target: str, interval: float = 0.5, event_callback: Any = None, owner_extra: dict[str, Any] | None = None):
        self.target = target
        self.interval = interval
        self.event_callback = event_callback
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
            try:
                module = auto_approve_module()
            except Exception as exc:
                self.update(error=str(exc), last_action="failed to load auto_approve_tmux.py")
                self.emit_event("worker_error", "failed to load auto_approve_tmux.py", error=str(exc))
                return

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
        if prompt_type == "file":
            return self.approve_prompt(module, current_hash, action, "file")
        if prompt_type == "tool":
            return self.approve_prompt(module, current_hash, action, "tool")
        self.update(last_action=f"unknown prompt type: {prompt_type}")
        return False

    def send_action(self, module: Any, action: str | None) -> None:
        if action == "option2":
            module.tmux_send_option2(self.target)
        else:
            module.tmux_send_enter(self.target)

    def handle_bash_prompt(self, module: Any, pane_text: str, current_hash: str, action: str | None) -> bool:
        cmd = module.extract_command(pane_text)
        if cmd is not None and module.is_dangerous(cmd):
            self.last_hash = current_hash
            self.last_hash_at = time.monotonic()
            self.last_blocked_hash = current_hash
            self.blocked += 1
            self.update(last_action=f"blocked bash: {truncate_text(cmd, 180)}")
            self.emit_event(
                "approval_blocked",
                "blocked bash command",
                command=truncate_text(cmd, 1000),
                risk="delete" if re.search(r"\brm\b|\brmdir\b", cmd) else "unknown",
                prompt_type="bash",
            )
            return True

        self.send_action(module, action)
        self.last_hash = current_hash
        self.last_hash_at = time.monotonic()
        self.last_blocked_hash = ""
        self.approved += 1
        desc = "bash command" if cmd is None else truncate_text(cmd, 180)
        self.update(last_action=f"approved bash: {desc}")
        self.emit_event(
            "approval_approved",
            f"approved bash: {desc}",
            command=truncate_text(cmd, 1000) if cmd else None,
            risk="process",
            prompt_type="bash",
            action=action or "option1",
        )
        self.stop_event.wait(3.0)
        return True

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
