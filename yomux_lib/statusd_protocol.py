"""Disabled legacy activity-summary bootstrap shared by web consumers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActivitySummaryAdmission:
    enabled: bool
    reason: str


ACTIVITY_SUMMARY_ADMISSION = ActivitySummaryAdmission(
    enabled=False,
    reason="async_replacement_required",
)


def activity_summary_enabled() -> bool:
    """Return the one process-wide admission decision for the legacy path."""

    return ACTIVITY_SUMMARY_ADMISSION.enabled is True


def activity_summary_bootstrap() -> dict[str, object]:
    return {
        "enabled": activity_summary_enabled(),
        "reason": "" if activity_summary_enabled() else ACTIVITY_SUMMARY_ADMISSION.reason,
    }


def normalized_activity_summary_bootstrap(value: object) -> dict[str, object]:
    enabled = isinstance(value, dict) and value.get("enabled") is True and activity_summary_enabled()
    return {
        "enabled": enabled,
        "reason": "" if enabled else ACTIVITY_SUMMARY_ADMISSION.reason,
    }
