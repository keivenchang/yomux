"""Deterministic clocks shared by tests that must never sleep."""

from __future__ import annotations

import math


class FakeClock:
    """A strict manually advanced clock with one behavior in every test module."""

    def __init__(self, start: float = 1000.0) -> None:
        value = float(start)
        if not math.isfinite(value):
            raise ValueError("clock start must be finite")
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> float:
        delta = float(seconds)
        if not math.isfinite(delta):
            raise ValueError("clock advance must be finite")
        self.value += delta
        return self.value
