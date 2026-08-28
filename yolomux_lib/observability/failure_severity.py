# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The one owner of whether a recorded failure is a server fault or a caller's own outcome.

Every failure the server records reaches the operator log through one of two writers -- the
synchronous one in ``Handler.write_api_response`` and the asynchronous one in
``TmuxWebtermApp.record_operation_failure``.  Both used to hardcode ``level="error"``, so the
rule for what an error *is* lived in two places and could not be stated once.  It is stated
here, and both writers ask this module.

``{"warning", "error"}`` is the release-blocking set: the live browser soak collects exactly
those levels as ``serverLogErrors`` (``yolomux_lib/live_browser_soak.py:1489``) and every gate
fixture retirement helper filters on the same pair (``tests/gate_harness.py:553``, ``:625``,
``:689``).  A record an operator must never have to read therefore cannot be a warning either;
the not-blocking level is ``info``, and the record is otherwise byte-identical so correlation,
dedupe and the Logs panel keep working.

WHICH CODES ARE OUTCOMES.  A failure is an expected caller outcome when it describes the state
of the target the caller asked about, not the validity of the request and not the health of a
server component -- an operator can do nothing about it and nothing is wrong.  The typed codes a
filesystem operation can carry are produced by exactly one owner,
``TmuxWebtermApp.typed_filesystem_operation_failed_result`` (``yolomux_lib/app.py``), which maps
``FilesystemError.status`` (``yolomux_lib/filesystem/errors.py``) onto a code:

* ``path_not_found`` (404, from ``FilesystemError.path_not_found``) -- the path is gone.  OUTCOME.
* ``permission_denied`` (403, from ``FilesystemError.os_error`` on ``PermissionError``) -- the
  caller may not read it.  OUTCOME.
* ``upgrade_required`` (426) -- an old browser stats payload is a caller outcome only when statsd's
  payload validator names itself with ``caller_outcome_owner=statsd.browser_upload``.  A web-to-
  daemon protocol fence uses the same HTTP code but requires operator action, so it remains a
  fault even on ``POST /api/stats-observations``.  MARKED PAYLOAD REJECTION: OUTCOME; OTHER: FAULT.
* ``conflict`` (409, ``target_exists``), ``request_too_large`` (413, ``file_too_large``),
  ``unsupported_media_type`` (415) -- also target state, but each code is ALSO produced by the
  generic status map in ``Handler.write_api_response`` for unrelated routes (session creation,
  upload limits, yoagent job conflicts), where the same code does mean a fault.  The code alone
  cannot decide them, so they stay faults until each producer names itself.
* ``invalid_request`` (any other 4xx) -- the caller sent something the contract forbids, or the
  server accepted a request it could have refused.  That is the defect class fixed in 71ab4d6bc
  and it must stay visible.  FAULT.
* ``dependency_failed`` (>= 500) and every service failure code
  (``service_unavailable``, ``service_busy``, ``producer_failed``, ``producer_abandoned``, ...)
  -- a component of this server did not do its job.  FAULT.

An unrecognized code, a malformed failure record, or a code carried at a status outside 4xx is a
FAULT: this module downgrades only what it can positively identify.
"""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any


EXPECTED_CALLER_OUTCOME_CODES = frozenset({
    "path_not_found",
    "permission_denied",
    "upgrade_required",
})

CALLER_OUTCOME_OWNER_FIELD = "caller_outcome_owner"
BROWSER_UPLOAD_OUTCOME_OWNER = "statsd.browser_upload"

EXPECTED_OUTCOME_LOG_LEVEL = "info"
FAULT_LOG_LEVEL = "error"


def expected_caller_outcome(error: Any, *, status: int = 0) -> bool:
    """Return whether one typed failure record is an ordinary outcome for its caller.

    ``status`` is the HTTP status the caller is being told, when the writer knows it.  When it is
    not supplied the record's own ``details.status`` decides, because that is where
    ``typed_filesystem_operation_failed_result`` records the status the worker produced.  A record
    with neither is not identifiable and stays a fault.
    """

    if not isinstance(error, Mapping):
        return False
    code = error.get("code")
    if not isinstance(code, str) or code not in EXPECTED_CALLER_OUTCOME_CODES:
        return False
    if not _typed_failure_record_is_well_formed(error):
        return False
    resolved = int(status) if isinstance(status, int) and not isinstance(status, bool) and status > 0 else 0
    if resolved <= 0:
        details = error.get("details")
        recorded = details.get("status") if isinstance(details, Mapping) else None
        if isinstance(recorded, bool) or not isinstance(recorded, int):
            return False
        resolved = recorded
    if code == "upgrade_required":
        # Fail closed: 426 is also the daemon-wide protocol fence an operator must act on, so
        # only the named producer of a stale-payload rejection buys the downgrade.
        details = error["details"]
        return (
            resolved == HTTPStatus.UPGRADE_REQUIRED
            and details.get(CALLER_OUTCOME_OWNER_FIELD) == BROWSER_UPLOAD_OUTCOME_OWNER
        )
    return 400 <= resolved < 500


def failure_record_level(error: Any, *, status: int = 0) -> str:
    """Return the operator-log level one failure record must be written at."""

    return EXPECTED_OUTCOME_LOG_LEVEL if expected_caller_outcome(error, status=status) else FAULT_LOG_LEVEL


def _typed_failure_record_is_well_formed(error: Mapping[str, Any]) -> bool:
    """Reject anything that is not the full typed failure contract.

    A producer that emitted a half-built record is itself the fault being reported, so a
    recognized code inside a malformed envelope must not buy a downgrade.
    """

    message = error.get("message")
    stack = error.get("stack")
    if (
        not isinstance(error.get("origin"), str)
        or not error["origin"]
        or not isinstance(error.get("retryable"), bool)
        or not isinstance(error.get("details"), Mapping)
        or not isinstance(message, Mapping)
        or not isinstance(message.get("key"), str)
        or not message["key"]
        or not isinstance(message.get("fallback"), str)
        or not isinstance(message.get("params"), Mapping)
        or not isinstance(stack, list)
        or not stack
    ):
        return False
    return all(
        isinstance(frame, Mapping)
        and isinstance(frame.get("component"), str)
        and bool(frame["component"])
        and isinstance(frame.get("operation"), str)
        and bool(frame["operation"])
        and isinstance(frame.get("code"), str)
        and bool(frame["code"])
        for frame in stack
    )
