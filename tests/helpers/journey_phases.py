"""Typed phase manifests for browser journeys with one shared fixture setup."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class JourneyChannel(StrEnum):
    OBSERVATIONS = "observations"
    FETCHES = "fetches"
    SOCKETS = "sockets"
    EVENTS = "events"


ALL_JOURNEY_CHANNELS = frozenset(JourneyChannel)


@dataclass(frozen=True)
class JourneyPhase:
    name: str
    predecessor: str | None
    channels: frozenset[JourneyChannel] = ALL_JOURNEY_CHANNELS


class JourneySentinel:
    """Fail closed when an aggregate skips or reorders an extracted phase."""

    def __init__(self, phases: Iterable[JourneyPhase]) -> None:
        self.phases = tuple(phases)
        if not self.phases:
            raise AssertionError("a journey needs at least one phase")
        names = [phase.name for phase in self.phases]
        if len(names) != len(set(names)):
            raise AssertionError(f"journey phase names must be unique: {names}")
        previous = None
        for phase in self.phases:
            if phase.predecessor != previous:
                raise AssertionError(
                    f"journey phase {phase.name!r} expected predecessor "
                    f"{previous!r}, got {phase.predecessor!r}"
                )
            if phase.channels != ALL_JOURNEY_CHANNELS:
                raise AssertionError(
                    f"journey phase {phase.name!r} has incomplete manifest channels: "
                    f"{sorted(phase.channels)}"
                )
            previous = phase.name
        self._visited: list[str] = []

    def enter(self, name: str) -> None:
        index = len(self._visited)
        expected = self.phases[index].name if index < len(self.phases) else None
        if name != expected:
            raise AssertionError(
                f"journey phase order mismatch after {self._visited[-1] if self._visited else None!r}: "
                f"expected {expected!r}, got {name!r}"
            )
        self._visited.append(name)

    def assert_complete(self) -> tuple[str, ...]:
        expected = tuple(phase.name for phase in self.phases)
        actual = tuple(self._visited)
        if actual != expected:
            raise AssertionError(f"incomplete journey manifest: expected {expected}, got {actual}")
        return actual

    def manifest(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Canonical aggregate receipt compared by the retained mega-test sentinel."""
        self.assert_complete()
        return tuple(
            (phase.name, tuple(sorted(channel.value for channel in phase.channels)))
            for phase in self.phases
        )


def phase_chain(*names: str) -> tuple[JourneyPhase, ...]:
    return tuple(
        JourneyPhase(name=name, predecessor=names[index - 1] if index else None)
        for index, name in enumerate(names)
    )


STATS_LOGS_PHASES = phase_chain(
    "shared-runtime-setup",
    "stats-and-logs-observations",
    "empty-filter-reload",
    "persisted-filter-reload",
)

YOCHAT_PHASES = phase_chain(
    "shared-runtime-setup",
    "bootstrap-and-live-message",
    "notification-and-read-cursor",
    "typing-and-search",
    "composer-and-emoji",
    "send-reconciliation",
    "yoagent-and-media",
    "reload-paging-and-cleanup",
)

GENERATED_SHARE_PHASES = {
    section: phase_chain(
        "shared-host-viewer-setup",
        "initial-replay-and-socket-sentinel",
        f"{section}-surface-matrix",
        "complete-manifest",
    )
    for section in ("chrome", "finder", "resilience", "popovers")
}
