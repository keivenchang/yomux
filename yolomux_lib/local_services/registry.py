"""Cross-port lifecycle owner for bounded local YOLOmux services."""

from __future__ import annotations

import json
import os
import platform
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Callable
from time import monotonic as monotonic_clock
from time import sleep as sleep_clock
from time import time as wall_clock

from ..atomic_file import atomic_write_text
from ..atomic_file import file_lock
from ..background_owner import pid_is_alive
from .rpc import LocalRpcError
from .rpc import new_envelope
from .rpc import request
from .rpc import safe_socket_path


LOCAL_SERVICE_REGISTRY_VERSION = 1
LOCAL_SERVICE_IDLE_SECONDS = 60.0
# A cold daemon can be delayed by concurrent browser/E2E workers on a
# developer machine. Startup remains bounded, but it must outlast that normal
# scheduler pressure before declaring the shared service unavailable.
LOCAL_SERVICE_START_TIMEOUT_SECONDS = 5.0
LOCAL_SERVICE_BACKOFF_SECONDS = 0.25
LOCAL_SERVICE_MAX_BACKOFF_SECONDS = 8.0
LOCAL_SERVICE_HEALTH_CACHE_SECONDS = 1.0
LOCAL_SERVICE_IDLE_SECONDS_ENV = "YOLOMUX_LOCAL_SERVICE_IDLE_SECONDS"


@dataclass(frozen=True)
class LocalServiceSpec:
    name: str
    module: str
    socket_name: str
    protocol_version: int
    idle_seconds: float = LOCAL_SERVICE_IDLE_SECONDS
    extra_args: tuple[str, ...] = ()


class LocalServiceRegistry:
    """Discover or start exactly one service for a state directory and spec."""

    def __init__(
        self,
        state_dir: Path,
        spec: LocalServiceSpec,
        *,
        socket_path: Path | None = None,
        popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
        clock: Callable[[], float] = monotonic_clock,
        sleep: Callable[[float], None] = sleep_clock,
    ):
        self.state_dir = Path(state_dir).expanduser()
        self.spec = spec
        self._socket_path = safe_socket_path(socket_path, prefix=f"yolomux-{spec.name}") if socket_path is not None else None
        self.popen = popen
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.Lock()
        self.process: subprocess.Popen[Any] | None = None
        self.failures = 0
        self.next_start_at = 0.0
        self._healthy_until = 0.0
        self._last_resource_sample: tuple[float, float] | None = None

    @property
    def service_dir(self) -> Path:
        return self.state_dir / "services"

    @property
    def socket_path(self) -> Path:
        if self._socket_path is not None:
            return self._socket_path
        return safe_socket_path(self.service_dir / self.spec.socket_name, prefix=f"yolomux-{self.spec.name}")

    @property
    def record_path(self) -> Path:
        return self.socket_path.with_suffix(".service.json")

    @property
    def lock_path(self) -> Path:
        # A long socket path can fall back under /tmp. Keep durable locks in
        # the configured state directory so service startup never chmods /tmp.
        return self.service_dir / f"{self.spec.name}.service.lock"

    def _read_record(self) -> dict[str, Any]:
        try:
            value = json.loads(self.record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_record(self, record: dict[str, Any]) -> None:
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.record_path, json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", mode=0o600)

    def _remove_stale_record(self) -> None:
        record = self._read_record()
        pid = int(record.get("pid") or 0)
        if pid and pid_is_alive(pid):
            return
        try:
            self.record_path.unlink()
        except FileNotFoundError:
            pass

    def _retire_incompatible_service(self) -> None:
        """Stop the service currently bound to our socket after a protocol bump."""
        response = self._request("ping", timeout=0.15)
        service_pid = int(response.get("pid") or 0)
        service_version = int(response.get("version") or 0)
        if not response.get("ok") or not service_pid or service_version == self.spec.protocol_version:
            return
        record = self._read_record()
        record_pid = int(record.get("pid") or 0)
        if record_pid and record_pid != service_pid:
            return
        self._request("shutdown", timeout=0.25)
        deadline = self.clock() + 0.5
        while pid_is_alive(service_pid) and self.clock() < deadline:
            self.sleep(0.03)
        if pid_is_alive(service_pid):
            try:
                os.kill(service_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError:
                return
            deadline = self.clock() + 0.5
            while pid_is_alive(service_pid) and self.clock() < deadline:
                self.sleep(0.03)
        if not pid_is_alive(service_pid):
            self._remove_stale_record()
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass

    def _request(self, method: str, payload: dict[str, Any] | None = None, timeout: float = 0.2) -> dict[str, Any]:
        try:
            request_payload = {"action": method, "protocol_version": self.spec.protocol_version, **(payload or {})}
            envelope = new_envelope(self.spec.name, method, request_payload, timeout_seconds=timeout)
            response, _binary = request(self.socket_path, envelope, timeout_seconds=timeout, fallback_legacy=True)
        except (OSError, LocalRpcError):
            return {}
        return response if isinstance(response, dict) else {}

    def healthy(self) -> bool:
        response = self._request("ping", timeout=0.15)
        healthy = (
            bool(response.get("ok"))
            and int(response.get("version") or 0) == self.spec.protocol_version
            and int(response.get("pid") or 0) > 0
        )
        if healthy:
            self.note_rpc_success()
        else:
            self.note_rpc_failure()
        return healthy

    def note_rpc_success(self) -> None:
        """Cache recent transport health to avoid ping/status fan-out per action."""
        self._healthy_until = self.clock() + LOCAL_SERVICE_HEALTH_CACHE_SECONDS

    def note_rpc_failure(self) -> None:
        self._healthy_until = 0.0

    def recently_healthy(self) -> bool:
        return self.clock() < self._healthy_until

    def _record_from_status(self, status: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": LOCAL_SERVICE_REGISTRY_VERSION,
            "service": self.spec.name,
            "module": self.spec.module,
            "pid": int(status.get("pid") or 0),
            "protocol_version": int(status.get("version") or 0),
            "socket": str(self.socket_path),
            "started_at": float(status.get("started_at") or wall_clock()),
            "updated_at": wall_clock(),
        }

    def _mark_failure(self) -> None:
        self.failures += 1
        delay = min(LOCAL_SERVICE_MAX_BACKOFF_SECONDS, LOCAL_SERVICE_BACKOFF_SECONDS * (2 ** max(0, self.failures - 1)))
        self.next_start_at = self.clock() + delay

    def _spawn(self) -> subprocess.Popen[Any] | None:
        idle_seconds = self.spec.idle_seconds
        configured_idle = os.environ.get(LOCAL_SERVICE_IDLE_SECONDS_ENV)
        if configured_idle:
            try:
                idle_seconds = max(0.1, float(configured_idle))
            except ValueError:
                pass
        args = [
            sys.executable,
            "-m",
            self.spec.module,
            "--serve",
            "--socket",
            str(self.socket_path),
            "--idle-seconds",
            str(idle_seconds),
            *self.spec.extra_args,
        ]
        try:
            return self.popen(
                args,
                close_fds=True,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return None

    def ensure_started(self) -> bool:
        if self.recently_healthy():
            return True
        if self.healthy():
            self._write_record(self._record_from_status(self._request("status", timeout=0.2)))
            return True
        with self.lock:
            if self.healthy():
                self._write_record(self._record_from_status(self._request("status", timeout=0.2)))
                return True
            if self.clock() < self.next_start_at:
                return False
            with file_lock(self.lock_path, dir_mode=0o700):
                if self.healthy():
                    self._write_record(self._record_from_status(self._request("status", timeout=0.2)))
                    return True
                self._retire_incompatible_service()
                self._remove_stale_record()
                process = self._spawn()
                if process is None:
                    self._mark_failure()
                    return False
                self.process = process
                deadline = self.clock() + LOCAL_SERVICE_START_TIMEOUT_SECONDS
                while self.clock() < deadline:
                    if self.healthy():
                        status = self._request("status", timeout=0.2)
                        self._write_record(self._record_from_status(status))
                        self.failures = 0
                        self.next_start_at = 0.0
                        return True
                    if process.poll() is not None:
                        break
                    self.sleep(0.03)
                self._mark_failure()
        return False

    def acquire_lease(self) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": f"{self.spec.name} unavailable"}
        response = self._request("lease", {"client_pid": os.getpid()}, timeout=0.25)
        if response.get("ok"):
            self._write_record(self._record_from_status(self._request("status", timeout=0.2)))
        return response

    def release_lease(self, lease_id: str) -> dict[str, Any]:
        return self._request("release", {"lease_id": lease_id}, timeout=0.25)

    def status(self) -> dict[str, Any]:
        status = self._request("status", timeout=0.25)
        return {
            "service": self.spec.name,
            "socket": str(self.socket_path),
            "healthy": bool(status.get("ok")) and int(status.get("version") or 0) == self.spec.protocol_version,
            "failures": self.failures,
            "next_start_at": self.next_start_at,
            "record": self._read_record(),
            "status": status,
        }

    def resources(self, pid: int) -> dict[str, float | int | None]:
        """Return best-effort worker CPU/RSS without starting a subprocess."""
        if pid <= 0:
            return {"cpu_percent": None, "rss_bytes": None}
        if platform.system() != "Linux":
            return {"cpu_percent": None, "rss_bytes": None}
        try:
            stat_fields = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8").split()
            statm_fields = (Path("/proc") / str(pid) / "statm").read_text(encoding="utf-8").split()
            cpu_seconds = (float(stat_fields[13]) + float(stat_fields[14])) / float(os.sysconf("SC_CLK_TCK"))
            rss_bytes = int(statm_fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
        except (IndexError, OSError, ValueError):
            return {"cpu_percent": None, "rss_bytes": None}
        now = self.clock()
        previous = self._last_resource_sample
        self._last_resource_sample = (now, cpu_seconds)
        cpu_percent: float | None = None
        if previous is not None and now > previous[0]:
            cpu_percent = round(max(0.0, (cpu_seconds - previous[1]) / (now - previous[0]) * 100.0), 3)
        return {"cpu_percent": cpu_percent, "rss_bytes": rss_bytes}
