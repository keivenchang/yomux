"""Typed reliability repetitions and counter-delta observations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from numbers import Real
from typing import Any, TypeVar


T = TypeVar("T")


class RepeatFailure(AssertionError):
    """An assertion failed during a consecutive reliability run."""

    def __init__(self, iteration: int, total: int, cause: Exception):
        self.iteration = iteration
        self.total = total
        self.cause = cause
        super().__init__(f"iteration {iteration}/{total} failed: {cause}")


def repeat(count: int, assertion: Callable[[int], T]) -> list[T]:
    """Run ``assertion`` consecutively, passing a one-based iteration number."""

    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("repeat count must be a positive integer")
    if not callable(assertion):
        raise TypeError("repeat assertion must be callable")
    results = []
    for iteration in range(1, count + 1):
        try:
            results.append(assertion(iteration))
        except Exception as exc:
            raise RepeatFailure(iteration, count, exc) from exc
    return results


@dataclass(frozen=True)
class CounterDelta:
    label: str
    before: Real
    after: Real

    @property
    def delta(self) -> Real:
        return self.after - self.before


def sample_counter_delta(
    sample: Callable[[], Real],
    observe_quiescent_window: Callable[[], Any],
    *,
    label: str = "counter",
) -> CounterDelta:
    """Take exactly two counter samples around a caller-owned quiet observation."""

    if not callable(sample) or not callable(observe_quiescent_window):
        raise TypeError("counter sample and quiescent-window observer must be callable")
    before = sample()
    observe_quiescent_window()
    after = sample()
    if isinstance(before, bool) or not isinstance(before, Real):
        raise TypeError(f"{label} first sample is not numeric: {before!r}")
    if isinstance(after, bool) or not isinstance(after, Real):
        raise TypeError(f"{label} second sample is not numeric: {after!r}")
    return CounterDelta(label=label, before=before, after=after)


def assert_counter_delta(
    sample: Callable[[], Real],
    observe_quiescent_window: Callable[[], Any],
    *,
    label: str = "counter",
    exactly: Real | None = None,
    at_least: Real | None = None,
    at_most: Real | None = None,
) -> CounterDelta:
    """Assert a two-sample delta without relying on the absolute total."""

    if exactly is not None and (at_least is not None or at_most is not None):
        raise ValueError("exactly cannot be combined with at_least or at_most")
    observation = sample_counter_delta(sample, observe_quiescent_window, label=label)
    delta = observation.delta
    if exactly is not None and delta != exactly:
        raise AssertionError(
            f"{label} delta was {delta!r}, expected exactly {exactly!r} "
            f"(before={observation.before!r}, after={observation.after!r})"
        )
    if at_least is not None and delta < at_least:
        raise AssertionError(
            f"{label} delta was {delta!r}, expected at least {at_least!r} "
            f"(before={observation.before!r}, after={observation.after!r})"
        )
    if at_most is not None and delta > at_most:
        raise AssertionError(
            f"{label} delta was {delta!r}, expected at most {at_most!r} "
            f"(before={observation.before!r}, after={observation.after!r})"
        )
    return observation
