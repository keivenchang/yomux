# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Backend health observation, retention, and publication.

M5 of ``DOIT.p0.daemon-monitor.md`` lives in :mod:`yolomux_lib.backend_health.store`:
the port-scoped retained history file. M4 lives in
:mod:`yolomux_lib.backend_health.observer`: the continuous observer that probes the
six services, reduces each to one typed state, debounces, and publishes. The
collector (M3) stays a separate owner in
:mod:`yolomux_lib.local_service_projection`, and nothing in this package may start
a service while OBSERVING one.

M7 -- bounded, non-destructive recovery -- lives in the observer module beside the
reducer it depends on: :class:`~yolomux_lib.backend_health.observer.ServiceRecoveryPlanner`
may ask an injected service control for ``retry`` and for nothing else, only for a
verified-down service, and at most once per backoff boundary. Every other cause performs
zero mutations and publishes its own bounded ``retry_blocked_<cause>`` outcome.
"""

from .observer import BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS
from .observer import BACKEND_HEALTH_DEGRADED_STATES
from .observer import BACKEND_HEALTH_EVENT
from .observer import BACKEND_HEALTH_EVENT_MAX_RESOURCES
from .observer import BACKEND_HEALTH_IMMEDIATE_STATES
from .observer import BACKEND_HEALTH_OBSERVE_SECONDS
from .observer import BACKEND_HEALTH_PROBE_TIMEOUT_SECONDS
from .observer import BACKEND_HEALTH_RECOVERY_ARMING_SECONDS
from .observer import BACKEND_HEALTH_RECOVERY_BACKOFF_SECONDS
from .observer import BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS
from .observer import BACKEND_HEALTH_STATE_SEVERITY
from .observer import BackendHealthObserver
from .observer import ObservationCycle
from .observer import RECOVERY_ELIGIBLE_REASONS
from .observer import RecoveryDecision
from .observer import ServiceRecoveryPlanner
from .observer import observed_health
from .observer import overall_health_state
from .observer import recovery_blocked_cause
from .observer import recovery_blocked_token
from .observer import recovery_row_fence
from .store import BACKEND_HEALTH_DIRECTORY_NAME
from .store import BACKEND_HEALTH_MAX_RESOURCES
from .store import BACKEND_HEALTH_MAX_TRANSITIONS
from .store import BACKEND_HEALTH_QUARANTINE_MAX_BYTES
from .store import BACKEND_HEALTH_REASON_CODES
from .store import BACKEND_HEALTH_RECOVERY_OUTCOMES
from .store import BACKEND_HEALTH_SCHEMA_VERSION
from .store import BACKEND_HEALTH_STATES
from .store import BackendHealthContractError
from .store import BackendHealthDiagnostic
from .store import BackendHealthStore
from .store import HealthSnapshot
from .store import PublishResult
from .store import ResourceObservation
from .store import TRANSITION_ROW_FIELDS
from .store import UNVERIFIED_PROCESS_EPOCH
from .store import WriterIdentity
from .store import process_epoch_token


__all__ = [
    "BACKEND_HEALTH_DEBOUNCE_OBSERVATIONS",
    "BACKEND_HEALTH_DEGRADED_STATES",
    "BACKEND_HEALTH_DIRECTORY_NAME",
    "BACKEND_HEALTH_EVENT",
    "BACKEND_HEALTH_EVENT_MAX_RESOURCES",
    "BACKEND_HEALTH_IMMEDIATE_STATES",
    "BACKEND_HEALTH_OBSERVE_SECONDS",
    "BACKEND_HEALTH_PROBE_TIMEOUT_SECONDS",
    "BACKEND_HEALTH_RECOVERY_ARMING_SECONDS",
    "BACKEND_HEALTH_RECOVERY_BACKOFF_SECONDS",
    "BACKEND_HEALTH_RECOVERY_MAX_ATTEMPTS",
    "BACKEND_HEALTH_STATE_SEVERITY",
    "BackendHealthObserver",
    "ObservationCycle",
    "RECOVERY_ELIGIBLE_REASONS",
    "RecoveryDecision",
    "ServiceRecoveryPlanner",
    "observed_health",
    "overall_health_state",
    "recovery_blocked_cause",
    "recovery_blocked_token",
    "recovery_row_fence",
    "BACKEND_HEALTH_MAX_RESOURCES",
    "BACKEND_HEALTH_MAX_TRANSITIONS",
    "BACKEND_HEALTH_QUARANTINE_MAX_BYTES",
    "BACKEND_HEALTH_REASON_CODES",
    "BACKEND_HEALTH_RECOVERY_OUTCOMES",
    "BACKEND_HEALTH_SCHEMA_VERSION",
    "BACKEND_HEALTH_STATES",
    "BackendHealthContractError",
    "BackendHealthDiagnostic",
    "BackendHealthStore",
    "HealthSnapshot",
    "PublishResult",
    "ResourceObservation",
    "TRANSITION_ROW_FIELDS",
    "UNVERIFIED_PROCESS_EPOCH",
    "WriterIdentity",
    "process_epoch_token",
]
