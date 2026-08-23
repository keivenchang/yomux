"""Reusable backend-health scenarios, separate from pytest test modules."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
from typing import Any

from tests.helpers.clock import FakeClock
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_OBSERVER_THREAD_PREFIX
from yolomux_lib.backend_health.observer import BACKEND_HEALTH_OBSERVE_SECONDS
from yolomux_lib.backend_health.observer import BackendHealthObserver
from yolomux_lib.backend_health.store import BackendHealthStore
from yolomux_lib.local_service_projection import LOCAL_SERVICE_INVENTORY
from yolomux_lib.local_services.rpc import local_service_traffic_class


BACKEND_HEALTH_TEST_PORT = 7799


class RefusingRegistry:
    def __init__(self) -> None:
        self.status_calls = 0

    def ensure_started(self) -> bool:
        raise AssertionError("the health observer must never start a demand-scoped service")

    def acquire_lease(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("the health observer must never lease a demand-scoped service")

    def status(self) -> dict[str, Any]:
        self.status_calls += 1
        return {"healthy": True}


class FakeService:
    def __init__(self, name: str, *, pid: int = 100, demand_started: bool = True) -> None:
        self.name = name
        self.registry = RefusingRegistry()
        self.calls = 0
        self.error: BaseException | None = None
        self.gate: threading.Event | None = None
        self.traffic_classes: list[str] = []
        self.row: dict[str, Any] = {"service": name, "pid": pid, "started_at": 10.0, "healthy": True, "last_failure": "", "demand_started": demand_started, "resources": {"cpu_percent": 1.0, "rss_bytes": 2048}}

    def runtime_status(self) -> dict[str, Any]:
        self.calls += 1
        self.registry.status()
        self.traffic_classes.append(local_service_traffic_class("query"))
        if self.gate is not None:
            self.gate.wait(timeout=5.0)
        if self.error is not None:
            raise self.error
        return dict(self.row)

    def down(self, reason: str = "worker exited") -> None:
        self.row["pid"] = 0
        self.row["last_failure"] = reason

    def absent(self) -> None:
        self.row["pid"] = 0
        self.row["last_failure"] = ""

    def up(self) -> None:
        self.row["pid"] = 100
        self.row["healthy"] = True
        self.row["last_failure"] = ""


class BackendHealthHarness:
    def __init__(self, tmp_path: Path, **kwargs: Any) -> None:
        demand_scoped = frozenset({"indexd", "statusd", "watchd", "approvald"})
        self.services = {
            name: FakeService(name, demand_started=name in demand_scoped)
            for name in LOCAL_SERVICE_INVENTORY
        }
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.monotonic = FakeClock(500.0)
        self.wall = FakeClock(1_000_000.0)
        self.waits: list[float] = []
        self.wake_result = False
        self.store = BackendHealthStore(BACKEND_HEALTH_TEST_PORT, state_dir=tmp_path, clock=self.wall)
        self.observer = BackendHealthObserver(row_producers=self.row_producers, store=self.store, publish=self.publish, label_source=lambda service: f"label:{service}", monotonic=self.monotonic, wall_clock=self.wall, wait=self.wait, identity_source=lambda pid: f"proc:{pid}" if pid > 0 else "", **kwargs)

    def row_producers(self) -> dict[str, Any]:
        return {name: service.runtime_status for name, service in self.services.items()}

    def publish(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.published.append((event_type, payload))
        return {"type": event_type, "payload": payload}

    def wait(self, timeout: float) -> bool:
        self.waits.append(timeout)
        self.monotonic.advance(timeout)
        return self.wake_result

    def arm_pool(self) -> None:
        self.observer._executor = ThreadPoolExecutor(max_workers=len(LOCAL_SERVICE_INVENTORY), thread_name_prefix=f"{BACKEND_HEALTH_OBSERVER_THREAD_PREFIX}-probe")
        self.observer._monotonic = time.monotonic

    def cycle(self, count: int = 1):
        result = None
        for _ in range(count):
            self.wall.advance(BACKEND_HEALTH_OBSERVE_SECONDS)
            result = self.observer.observe_once()
        return result

    def states(self) -> dict[str, str]:
        return {name: state for name, (state, _) in self.observer._accepted.items()}


class RecoveryHarness(BackendHealthHarness):
    def __init__(self, tmp_path: Path, control: Any = None, **kwargs: Any) -> None:
        kwargs.setdefault("recovery_arming_seconds", 0.0)
        super().__init__(tmp_path, recovery_control=control, **kwargs)
        self.control = control

    def tick(self, count: int = 1, seconds: float = BACKEND_HEALTH_OBSERVE_SECONDS):
        result = None
        for _ in range(count):
            self.wall.advance(seconds)
            self.monotonic.advance(seconds)
            result = self.observer.observe_once()
        return result

    def outcome(self, resource: str) -> str:
        current = (((self.store.document().get("resources") or {}).get(resource) or {}).get("current") or {})
        return str(current.get("recovery_outcome") or "")
