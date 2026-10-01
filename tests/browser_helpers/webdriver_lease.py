# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""One owner for a WebDriver's lifetime: spawn identity, and a bounded, proof-guarded retirement.

Before this, four call sites - the browser fixture, the live soak, the active-window capture, and
the Finder repro - each rolled their own teardown: a bare `driver.quit()` in two of them, a threaded
quit with a `service.process.terminate()` fallback in a third, and a threaded quit plus a
descendant-tree `SIGKILL` in the fourth. Divergent copies of one lifetime is exactly the shape that
leaves a chromedriver behind when `quit()` hangs, and - worse - lets an escalation signal a PID the
kernel has already reused for an unrelated process.

This module is the shared parent. A lease captures an immutable generation at spawn - the
chromedriver PID together with its kernel start key - and every authorization, signal, and exit
barrier is decided against that one proof. The retirement sequence is bounded quit -> TERM -> KILL ->
reap -> final proof; it aggregates every error instead of raising through the middle of a teardown,
and it NEVER sends a signal to a PID it cannot prove is still the exact process this lease spawned.
A reused or reparented PID fails that proof and is left untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from typing import Any
from typing import Callable


def process_start_key(pid: int | None) -> str | None:
    """An identity that is stable for one process life and changes when the PID is reused.

    Returns None when the PID is not alive, which is itself a proof of absence. On Linux the key is
    the kernel start time from /proc/<pid>/stat field 22; on a host without /proc (macOS) it is the
    process start timestamp from `ps`. Two processes that reuse one PID get different keys, so a
    stale generation can never authorize a signal against the new occupant.
    """

    if pid is None:
        return None
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.exists():
        try:
            data = stat_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        # comm (field 2) is parenthesised and may itself contain spaces or ')', so split after the
        # final ')': the remaining fields begin at field 3 (state), and starttime is field 22.
        rparen = data.rfind(")")
        if rparen == -1:
            return None
        remaining = data[rparen + 1:].split()
        if len(remaining) < 20:
            return None
        # A zombie (state Z) is a dead process the kernel has not yet reaped; its /proc entry lingers
        # only so a parent can collect the exit status. For retirement it is gone - it runs nothing
        # and will never run again - so it must read as absent, not as a still-live owned identity.
        if remaining[0] == "Z":
            return None
        return remaining[19]
    completed = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return None
    key = completed.stdout.strip()
    return key or None


def chromedriver_pid(driver: Any) -> int | None:
    """The PID of the chromedriver service backing this driver, or None when it cannot be read."""

    service = getattr(driver, "service", None)
    process = getattr(service, "process", None)
    pid = getattr(process, "pid", None)
    return int(pid) if isinstance(pid, int) else None


@dataclass(frozen=True)
class DriverGeneration:
    """The one immutable proof of a driver's identity, captured at spawn and never recomputed."""

    pid: int | None
    start_key: str | None

    def as_dict(self) -> dict[str, object]:
        return {"pid": self.pid, "start_key": self.start_key}


@dataclass
class RetirementResult:
    """What one retirement did and whether the leased process is provably gone afterward."""

    generation: dict[str, object]
    proven_gone: bool
    steps: list[str] = field(default_factory=list)
    signals_sent: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # The exact exception driver.quit() raised, kept alongside its aggregated string form so a
    # consumer can build a typed terminal failure from the original cause rather than a repr.
    quit_error: Exception | None = None

    @property
    def quit_timed_out(self) -> bool:
        """True when driver.quit() hit its deadline instead of returning or raising."""

        return any(step == "quit:timeout" for step in self.steps)

    def as_dict(self) -> dict[str, object]:
        return {
            "generation": self.generation,
            "proven_gone": self.proven_gone,
            "steps": list(self.steps),
            "signals_sent": list(self.signals_sent),
            "errors": list(self.errors),
        }


class WebDriverLease:
    """One driver, one generation, one bounded and proof-guarded retirement."""

    def __init__(
        self,
        driver: Any,
        *,
        generation: DriverGeneration,
        quit_timeout: float = 15.0,
        reap_timeout: float = 5.0,
        poll_seconds: float = 0.05,
        identity_fn: Callable[[int | None], str | None] = process_start_key,
        signal_fn: Callable[[int, int], None] = os.kill,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.driver = driver
        self.generation = generation
        self.quit_timeout = quit_timeout
        self.reap_timeout = reap_timeout
        self.poll_seconds = poll_seconds
        self._identity_fn = identity_fn
        self._signal_fn = signal_fn
        self._clock = clock
        self._sleep = sleep

    @classmethod
    def acquire(
        cls,
        factory: Callable[[], Any],
        *,
        identity_fn: Callable[[int | None], str | None] = process_start_key,
        **kwargs: Any,
    ) -> "WebDriverLease":
        """Spawn a driver and capture its generation in the same step, so identity is never inferred later."""

        driver = factory()
        return cls.from_driver(driver, identity_fn=identity_fn, **kwargs)

    @classmethod
    def from_driver(
        cls,
        driver: Any,
        *,
        identity_fn: Callable[[int | None], str | None] = process_start_key,
        **kwargs: Any,
    ) -> "WebDriverLease":
        """Capture the generation of an already-spawned driver. Do it once, at hand-off, not later."""

        pid = chromedriver_pid(driver)
        generation = DriverGeneration(pid=pid, start_key=identity_fn(pid))
        return cls(driver, generation=generation, identity_fn=identity_fn, **kwargs)

    def is_current_process(self) -> bool:
        """True only when the leased PID is alive AND carries the exact start key captured at spawn.

        This is the one gate on every signal. A PID that died and was reused reads a different key;
        a reparented descendant no longer answers to the leased PID at all. Either way this is False,
        and an unproved process is never signalled.
        """

        if self.generation.pid is None:
            return False
        current = self._identity_fn(self.generation.pid)
        return current is not None and current == self.generation.start_key

    def _bounded_quit(self) -> tuple[str, Exception | None]:
        """Run driver.quit() with a deadline. A hang returns 'timeout'; a raise is captured, not thrown."""

        outcome: dict[str, Any] = {"state": "returned", "error": None}

        def run() -> None:
            try:
                self.driver.quit()
            except Exception as exc:  # a teardown boundary: aggregate the failure, never kill the caller
                outcome["state"] = "raised"
                outcome["error"] = exc

        worker = threading.Thread(target=run, name="webdriver-lease-quit", daemon=True)
        worker.start()
        worker.join(self.quit_timeout)
        if worker.is_alive():
            return "timeout", None
        return outcome["state"], outcome["error"]

    def _reap_wait(self) -> None:
        """Wait, bounded, until the leased process is provably gone or its key has changed."""

        deadline = self._clock() + self.reap_timeout
        while self.is_current_process() and self._clock() < deadline:
            self._sleep(self.poll_seconds)

    def _escalate(self, sig_name: str, sig: int, result: RetirementResult) -> None:
        if not self.is_current_process():
            return
        try:
            self._signal_fn(self.generation.pid, sig)
            result.signals_sent.append(sig_name)
        except ProcessLookupError:
            # It exited between the proof and the signal; that is success, not an error.
            return
        except OSError as exc:
            # Permission denial and the like are recorded, never swallowed, and never retried blind.
            result.errors.append(f"{sig_name}: {exc!r}")
            return
        self._reap_wait()

    def retire(self) -> RetirementResult:
        """Bounded quit -> TERM -> KILL -> reap -> final proof. Aggregate every error; prove the end."""

        result = RetirementResult(generation=self.generation.as_dict(), proven_gone=False)
        quit_state, quit_error = self._bounded_quit()
        result.steps.append(f"quit:{quit_state}")
        if quit_error is not None:
            result.quit_error = quit_error
            result.errors.append(f"quit: {quit_error!r}")
        self._reap_wait()
        for sig_name, sig in (("SIGTERM", signal.SIGTERM), ("SIGKILL", signal.SIGKILL)):
            if not self.is_current_process():
                break
            result.steps.append(f"escalate:{sig_name}")
            self._escalate(sig_name, sig, result)
        result.proven_gone = not self.is_current_process()
        result.steps.append(f"final:{'gone' if result.proven_gone else 'alive'}")
        return result


def retire_all(leases: list[WebDriverLease]) -> dict[str, object]:
    """Retire several leases and aggregate the result, so one hung driver never abandons the others.

    Every lease is retired even if an earlier one raised on the way in; the aggregate carries each
    per-driver result and is proven-clean only when every leased process is proven gone.
    """

    results: list[dict[str, object]] = []
    errors: list[str] = []
    for index, lease in enumerate(leases):
        try:
            results.append(lease.retire().as_dict())
        except Exception as exc:  # a per-unit boundary: one bad lease must not strand the rest
            errors.append(f"lease[{index}]: {exc!r}")
    return {
        "proven_gone": bool(results) and all(result["proven_gone"] for result in results) and not errors,
        "results": results,
        "errors": errors,
    }
