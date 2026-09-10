"""Shared access to the worker-local browser console."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
import json
from numbers import Real
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlsplit

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

from yolomux_lib import browser_diagnostic_receipts
from yolomux_lib.live_browser_soak import js_debug_event_enrichment_matches


# Live activity-summary and session-files errors arrived 8 and 15 seconds after the first
# transport failure. Sixteen seconds covers the latest measured arrival with a one-second margin.
BROWSER_JOURNEY_OBSERVATION_SECONDS = 16.0
BROWSER_JOURNEY_POLL_SECONDS = 0.25


def read_browser_console_log(driver) -> tuple[dict[str, Any], ...]:
    """Read and drain Chrome's browser log through one shared owner."""

    return tuple(dict(entry) for entry in driver.get_log("browser"))


def _browser_console_failures(entries: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [entry for entry in entries if str(entry.get("level") or "").upper() in {"WARNING", "SEVERE"}]


def assert_browser_console_error_free(driver) -> tuple[dict[str, Any], ...]:
    """Fail when Chrome emitted a severe console entry, including on static fixtures."""

    entries = read_browser_console_log(driver)
    severe_entries = [entry for entry in entries if str(entry.get("level") or "").upper() == "SEVERE"]
    if severe_entries:
        raise AssertionError(f"browser emitted severe console entries: {json.dumps(severe_entries, sort_keys=True)}")
    return entries


def assert_only_expected_browser_http_error(
    driver,
    *,
    path: str,
    status: int,
    query: Mapping[str, str],
) -> dict[str, Any]:
    """Consume one deliberate HTTP error while rejecting every other browser failure."""

    entries = read_browser_console_log(driver)
    failure_entries = _browser_console_failures(entries)
    matches = []
    for entry in failure_entries:
        message = str(entry.get("message") or "")
        url_text = message.split(" - Failed to load resource:", 1)[0]
        parsed = urlsplit(url_text)
        actual_query = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
        if (
            str(entry.get("level") or "").upper() == "SEVERE"
            and str(entry.get("source") or "") == "network"
            and parsed.path == path
            and actual_query == dict(query)
            and f"status of {int(status)}" in message
        ):
            matches.append(entry)
    unexpected = [entry for entry in failure_entries if entry not in matches]
    if len(matches) != 1 or unexpected:
        raise AssertionError(
            "expected exactly one matching browser HTTP error: "
            f"{json.dumps({'matches': matches, 'unexpected': unexpected}, sort_keys=True)}"
        )
    return matches[0]


def assert_only_expected_browser_network_error(driver, *, url: str, reason: str) -> dict[str, Any]:
    """Consume one deliberate external-resource failure and reject every other browser failure."""

    entries = read_browser_console_log(driver)
    failure_entries = _browser_console_failures(entries)
    matches = [
        entry
        for entry in failure_entries
        if str(entry.get("level") or "").upper() == "SEVERE"
        and str(entry.get("source") or "") == "network"
        and str(entry.get("message") or "").startswith(f"{url} - Failed to load resource:")
        and reason in str(entry.get("message") or "")
    ]
    unexpected = [entry for entry in failure_entries if entry not in matches]
    if len(matches) != 1 or unexpected:
        raise AssertionError(
            "expected exactly one matching browser network error: "
            f"{json.dumps({'matches': matches, 'unexpected': unexpected}, sort_keys=True)}"
        )
    return matches[0]


def assert_only_expected_browser_warning(
    driver,
    *,
    message: str,
    correlation: str,
) -> dict[str, Any]:
    """Consume one deliberate console warning with exact message and correlation."""

    failure_entries = _browser_console_failures(read_browser_console_log(driver))
    marker = f'"{message}" '
    matches = []
    for entry in failure_entries:
        text = str(entry.get("message") or "")
        if marker not in text:
            continue
        actual_correlation = text.split(marker, 1)[1].splitlines()[0]
        if (
            str(entry.get("level") or "").upper() == "WARNING"
            and str(entry.get("source") or "") == "console-api"
            and actual_correlation == correlation
        ):
            matches.append(entry)
    unexpected = [entry for entry in failure_entries if entry not in matches]
    if len(matches) != 1 or unexpected:
        raise AssertionError(
            "expected exactly one matching browser warning: "
            f"{json.dumps({'matches': matches, 'unexpected': unexpected}, sort_keys=True)}"
        )
    return matches[0]


def _is_known_codemirror_measure_warning(entry: Mapping[str, Any]) -> bool:
    return (
        str(entry.get("level") or "").upper() == "WARNING"
        and str(entry.get("source") or "") == "console-api"
        and "/static/codemirror.js" in str(entry.get("message") or "")
        and '"Measure loop restarted more than 5 times"' in str(entry.get("message") or "")
    )


def emit_js_debug_event(driver, event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Emit through the product owner and return the exact retained event."""

    event = driver.execute_script(
        "return recordJsDebugEvent(arguments[0], arguments[1]);",
        str(event_type),
        dict(payload),
    )
    if not isinstance(event, Mapping) or isinstance(event.get("id"), bool) or not isinstance(event.get("id"), int):
        raise AssertionError(f"JS debug event emission returned malformed evidence: {json.dumps(event, sort_keys=True)}")
    return dict(event)


def _js_debug_api_failure_shape(event: Mapping[str, Any]) -> dict[str, Any]:
    parsed = urlsplit(str(event.get("url") or ""))
    return {
        "type": str(event.get("type") or ""),
        "method": str(event.get("method") or "").upper(),
        "path": parsed.path,
        "query": {key: values[-1] for key, values in parse_qs(parsed.query).items() if values},
        "status": event.get("status") if isinstance(event.get("status"), int) else None,
        "ok": event.get("ok") if isinstance(event.get("ok"), bool) else None,
        "error": str(event.get("error") or ""),
    }


def consume_only_expected_js_debug_api_errors(
    driver,
    expected: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Validate and consume an exact ordered list of deliberate API failures."""

    js_debug = _read_js_debug_store(driver)
    failures = list(js_debug.get("errors") or ())
    expected_shapes = [
        {
            "type": "api",
            "method": str(item.get("method") or "GET").upper(),
            "path": str(item.get("path") or ""),
            "query": dict(item.get("query") or {}),
            "status": item.get("status") if isinstance(item.get("status"), int) else None,
            "ok": item.get("ok") if isinstance(item.get("ok"), bool) else None,
            "error": str(item.get("error") or ""),
        }
        for item in expected
    ]
    actual_shapes = [_js_debug_api_failure_shape(event) for event in failures]
    if actual_shapes != expected_shapes:
        raise AssertionError(
            "expected exact JS API error list: "
            f"{json.dumps({'expected': expected_shapes, 'actual': actual_shapes}, sort_keys=True)}"
        )
    return acknowledge_and_consume_only_expected_js_debug_failures(driver, failures)


def consume_only_expected_js_debug_api_error(
    driver,
    *,
    path: str,
    status: int,
    method: str,
    query: Mapping[str, str],
) -> dict[str, Any]:
    """Validate and consume one deliberate API failure from the real JS event store."""

    return consume_only_expected_js_debug_api_errors(
        driver,
        ({"path": path, "status": int(status), "ok": False, "method": method, "query": query},),
    )[0]


def acknowledge_browser_diagnostic_receipts(driver) -> Mapping[str, Any]:
    """Force the shared uploader and require durable receipt before local test retirement."""

    WebDriverWait(driver, 5, poll_frequency=BROWSER_JOURNEY_POLL_SECONDS).until(
        lambda current: current.execute_script("return typeof statsWriterFence !== 'undefined' && statsWriterFence !== null")
    )
    receipt = driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          for (let attempt = 0; attempt < 10; attempt += 1) {
            const barrier = jsDebugCurrentObservationReceiptBarrier();
            if (barrier?.quiescent === true) return barrier;
            await flushJsDebugCurrentObservations();
          }
          return jsDebugCurrentObservationReceiptBarrier();
        })().then(done, error => done({error: String(error?.message || error)}));
        """
    )
    receipt = browser_diagnostic_receipts.validate_browser_receipt_barrier(receipt)
    if receipt.get("quiescent") is not True:
        raise AssertionError(f"browser diagnostic receipt did not quiesce: {json.dumps(receipt, sort_keys=True)}")
    return receipt


def acknowledge_and_consume_only_expected_js_debug_failures(
    driver,
    expected: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Acknowledge durable receipts, then retire only the exact expected failures."""

    expected_events = tuple(dict(event) for event in expected)
    actual_events = tuple(dict(event) for event in _read_js_debug_store(driver).get("errors") or ())
    if (
        len(actual_events) != len(expected_events)
        or any(
            not js_debug_event_enrichment_matches(expected_event, actual_event)
            for expected_event, actual_event in zip(expected_events, actual_events, strict=True)
        )
    ):
        raise AssertionError(
            "expected exact JS debug failure list: "
            f"{json.dumps({'expected': expected_events, 'actual': actual_events}, sort_keys=True)}"
        )
    event_ids = [event.get("id") for event in expected_events]
    if (
        any(isinstance(event_id, bool) or not isinstance(event_id, int) for event_id in event_ids)
        or len(set(event_ids)) != len(event_ids)
    ):
        raise AssertionError(f"expected JS debug failures to carry unique integer IDs: {event_ids}")

    acknowledge_browser_diagnostic_receipts(driver)
    retired = driver.execute_script(
        """
        const ids = new Set(arguments[0]);
        const retired = [];
        for (let index = jsDebugEvents.length - 1; index >= 0; index -= 1) {
          if (!ids.has(jsDebugEvents[index]?.id)) continue;
          retired.unshift(...jsDebugEvents.splice(index, 1));
        }
        return retired;
        """,
        event_ids,
    )
    retired_events = tuple(dict(event) for event in retired or ())
    if (
        len(retired_events) != len(expected_events)
        or any(
            not js_debug_event_enrichment_matches(expected_event, retired_event)
            for expected_event, retired_event in zip(expected_events, retired_events, strict=True)
        )
    ):
        raise AssertionError(
            "exact JS debug failure retirement changed the expected list: "
            f"{json.dumps({'expected': expected_events, 'retired': retired_events}, sort_keys=True)}"
        )
    remaining = tuple(dict(event) for event in _read_js_debug_store(driver).get("errors") or ())
    if remaining:
        raise AssertionError(f"unexpected JS debug failures remained after exact retirement: {json.dumps(remaining, sort_keys=True)}")
    return retired_events


def retire_only_nonfailure_js_debug_events(driver) -> tuple[dict[str, Any], ...]:
    """Retire an exact clean-event snapshot without masking a browser failure."""

    driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(() => done(true)));
        """
    )
    snapshot = driver.execute_script(
        """
        const snapshot = Array.from(jsDebugEvents, event => ({...event}));
        const failures = Array.from(jsDebugFailureEvents(), event => ({...event}));
        if (failures.length) return {failures, retired: []};
        const ids = new Set(snapshot.map(event => event.id));
        const retired = [];
        for (let index = jsDebugEvents.length - 1; index >= 0; index -= 1) {
          if (!ids.has(jsDebugEvents[index]?.id)) continue;
          jsDebugEvents.splice(index, 1);
        }
        return {failures, retired: snapshot};
        """
    )
    if not isinstance(snapshot, Mapping):
        raise AssertionError("clean JS debug retirement snapshot is malformed")
    failures = tuple(dict(event) for event in snapshot.get("failures") or ())
    if failures:
        raise AssertionError(
            f"cannot retire a JS debug baseline containing failures: {json.dumps(failures, sort_keys=True)}"
        )
    retired_events = tuple(dict(event) for event in snapshot.get("retired") or ())
    event_ids = [event.get("id") for event in retired_events]
    if (
        any(isinstance(event_id, bool) or not isinstance(event_id, int) for event_id in event_ids)
        or len(set(event_ids)) != len(event_ids)
    ):
        raise AssertionError(f"clean JS debug events must carry unique integer IDs: {event_ids}")
    return retired_events


def _server_log_failure_shape(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "level": str(entry.get("level") or "").lower(),
        "source": str(entry.get("source") or ""),
        "category": str(entry.get("category") or ""),
        "message": str(entry.get("message") or ""),
    }


def _server_log_epoch(payload: Mapping[str, Any]) -> str:
    epoch = payload.get("epoch")
    if epoch is None:
        raise AssertionError("browser journey server-log epoch is missing")
    if not isinstance(epoch, str) or not epoch:
        raise AssertionError("browser journey server-log epoch is malformed")
    return epoch


def _consumed_server_log_ids(driver, epoch: str) -> set[int]:
    consumed = driver.execute_script(
        """
        const epoch = arguments[0];
        let state = window.__yolomuxBrowserJourneyGate;
        if (typeof state === 'undefined') {
          state = {
            visitedSurfaces: [],
            consumedServerLogIds: [],
            serverLogEpoch: epoch,
            observer: null,
            observe: null,
          };
          window.__yolomuxBrowserJourneyGate = state;
        } else if (!state || typeof state !== 'object'
            || !Array.isArray(state.consumedServerLogIds)
            || !Object.prototype.hasOwnProperty.call(state, 'serverLogEpoch')
            || (state.serverLogEpoch !== null && typeof state.serverLogEpoch !== 'string')) {
          return null;
        }
        if (state.serverLogEpoch !== epoch) {
          state.serverLogEpoch = epoch;
          state.consumedServerLogIds.splice(0, state.consumedServerLogIds.length);
        }
        return [...state.consumedServerLogIds];
        """,
        epoch,
    )
    if not isinstance(consumed, list) or any(isinstance(item, bool) or not isinstance(item, int) for item in consumed):
        raise AssertionError("browser journey consumed server-log IDs are malformed")
    return set(consumed)


def validate_server_log_ring_payload(payload: Any) -> Mapping[str, Any]:
    """Validate one network or in-process server-ring payload identically."""

    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        raise AssertionError("browser journey cannot gate /api/logs because its payload is malformed")
    logs = payload.get("logs")
    if not isinstance(logs, list) or any(not isinstance(entry, Mapping) for entry in logs):
        raise AssertionError("browser journey cannot gate /api/logs because logs is not an array of entries")
    _server_log_epoch(payload)
    sequence = payload.get("sequence")
    capacity = payload.get("capacity")
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
        or isinstance(capacity, bool)
        or not isinstance(capacity, int)
        or capacity < 1
        or any(
            isinstance(entry.get("id"), bool)
            or not isinstance(entry.get("id"), int)
            or entry["id"] < 1
            for entry in logs
        )
    ):
        raise AssertionError("browser journey cannot gate /api/logs because its sequence evidence is malformed")
    dropped = payload.get("dropped")
    if not isinstance(dropped, Mapping):
        raise AssertionError("browser journey cannot gate /api/logs because dropped-entry evidence is missing")
    count = dropped.get("count")
    first_id = dropped.get("first_id")
    last_id = dropped.get("last_id")
    by_level = dropped.get("by_level")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        or (first_id is not None and (isinstance(first_id, bool) or not isinstance(first_id, int)))
        or (last_id is not None and (isinstance(last_id, bool) or not isinstance(last_id, int)))
        or not isinstance(by_level, Mapping)
        or any(
            not isinstance(level, str)
            or isinstance(level_count, bool)
            or not isinstance(level_count, int)
            or level_count < 0
            for level, level_count in by_level.items()
        )
    ):
        raise AssertionError("browser journey cannot gate /api/logs because dropped-entry evidence is malformed")
    log_ids = [int(entry["id"]) for entry in logs]
    dropped_level_total = sum(int(level_count) for level_count in by_level.values())
    dropped_range_valid = (
        (count == 0 and first_id is None and last_id is None)
        or (count > 0 and first_id == 1 and last_id == count)
    )
    expected_retained_count = min(sequence, capacity)
    expected_dropped_count = max(0, sequence - capacity)
    expected_log_ids = list(range(count + 1, sequence + 1))
    if (
        len(logs) != expected_retained_count
        or count != expected_dropped_count
        or log_ids != expected_log_ids
        or not dropped_range_valid
        or dropped_level_total != count
    ):
        raise AssertionError("browser journey cannot gate /api/logs because ring continuity is malformed")
    return payload


def server_log_ring_cursor(payload: Any) -> Mapping[str, Any]:
    """Copy the validated immutable evidence needed across fixture phases."""

    validated = validate_server_log_ring_payload(payload)
    dropped = validated["dropped"]
    return {
        **dict(validated),
        "logs": [dict(entry) for entry in validated["logs"]],
        "dropped": {
            **dict(dropped),
            "by_level": dict(dropped["by_level"]),
        },
    }


def validate_server_log_ring_transition(previous: Any, current: Any) -> Mapping[str, Any]:
    """Prove one ring snapshot only appended entries or evicted an immutable prefix."""

    before = server_log_ring_cursor(previous)
    after = server_log_ring_cursor(current)
    before_sequence = int(before["sequence"])
    after_sequence = int(after["sequence"])
    before_dropped = int(before["dropped"]["count"])
    after_dropped = int(after["dropped"]["count"])
    if before["epoch"] != after["epoch"]:
        raise AssertionError("browser fixture server ring epoch changed across one lifecycle boundary")
    if before["capacity"] != after["capacity"]:
        raise AssertionError("browser fixture server ring capacity changed across one lifecycle boundary")
    if after_sequence < before_sequence or after_dropped < before_dropped:
        raise AssertionError("browser fixture server ring moved behind its prior lifecycle boundary")
    if (
        after_sequence == before_sequence
        and (after["logs"] != before["logs"] or after["dropped"] != before["dropped"])
    ):
        raise AssertionError(
            "browser fixture server ring changed without advancing sequence: "
            f"{json.dumps({'before': before, 'after': after}, sort_keys=True)}"
        )
    after_by_id = {int(entry["id"]): entry for entry in after["logs"]}
    for entry in before["logs"]:
        entry_id = int(entry["id"])
        if entry_id <= after_dropped:
            continue
        if after_by_id.get(entry_id) != entry:
            raise AssertionError(
                f"browser fixture server ring mutated retained entry {entry_id}: "
                f"{json.dumps({'before': entry, 'after': after_by_id.get(entry_id)}, sort_keys=True)}"
            )
    levels = set(before["dropped"]["by_level"]) | set(after["dropped"]["by_level"])
    dropped_by_level = {
        level: int(after["dropped"]["by_level"].get(level, 0))
        - int(before["dropped"]["by_level"].get(level, 0))
        for level in levels
    }
    if any(level_count < 0 for level_count in dropped_by_level.values()):
        raise AssertionError("browser fixture server ring dropped levels moved behind their prior boundary")
    return {
        "newLogs": [dict(entry) for entry in after["logs"] if int(entry["id"]) > before_sequence],
        "droppedCount": after_dropped - before_dropped,
        "droppedByLevel": {level: value for level, value in dropped_by_level.items() if value},
    }


def _server_log_ring_after_boundary(
    payload: Mapping[str, Any],
    boundary: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Scope a shared process ring to one server's captured start boundary."""

    current = validate_server_log_ring_payload(payload)
    start = validate_server_log_ring_payload(boundary)
    current_epoch = _server_log_epoch(current)
    start_epoch = _server_log_epoch(start)
    current_sequence = int(current["sequence"])
    start_sequence = int(start["sequence"])
    current_dropped = current["dropped"]
    start_dropped = start["dropped"]
    current_dropped_count = int(current_dropped["count"])
    start_dropped_count = int(start_dropped["count"])
    if current_epoch != start_epoch:
        raise AssertionError("browser journey server-log epoch changed after the fixture boundary")
    if current_sequence < start_sequence:
        raise AssertionError("browser journey server-log sequence moved behind the fixture boundary")
    if current_dropped_count < start_dropped_count:
        raise AssertionError("browser journey server-log dropped count moved behind the fixture boundary")
    levels = set(current_dropped["by_level"]) | set(start_dropped["by_level"])
    dropped_by_level = {
        level: int(current_dropped["by_level"].get(level, 0)) - int(start_dropped["by_level"].get(level, 0))
        for level in levels
    }
    if any(count < 0 for count in dropped_by_level.values()):
        raise AssertionError("browser journey server-log dropped levels moved behind the fixture boundary")
    dropped_by_level = {level: count for level, count in dropped_by_level.items() if count}
    dropped_count = current_dropped_count - start_dropped_count
    return {
        **dict(current),
        "logs": [dict(entry) for entry in current["logs"] if int(entry["id"]) > start_sequence],
        "dropped": {
            "count": dropped_count,
            "first_id": current_dropped.get("first_id") if dropped_count else None,
            "last_id": current_dropped.get("last_id") if dropped_count else None,
            "by_level": dropped_by_level,
        },
    }


def _read_server_log_ring(driver) -> Mapping[str, Any]:
    result = driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        fetch('/api/logs', {cache: 'no-store', credentials: 'same-origin'})
          .then(async response => {
            const text = await response.text();
            let payload = null;
            let parseError = '';
            try { payload = JSON.parse(text); } catch (error) { parseError = String(error?.message || error); }
            done({reachable: true, status: Number(response.status || 0), payload, parseError});
          })
          .catch(error => done({reachable: false, status: 0, payload: null, parseError: String(error?.message || error)}));
        """
    )
    if not isinstance(result, Mapping) or result.get("reachable") is not True:
        detail = str(result.get("parseError") or "") if isinstance(result, Mapping) else "invalid fetch result"
        raise AssertionError(f"browser journey cannot gate /api/logs because the ring is unreachable: {detail}")
    if result.get("status") != 200:
        raise AssertionError(f"browser journey cannot gate /api/logs because it returned HTTP {result.get('status')}")
    if result.get("parseError"):
        raise AssertionError(f"browser journey cannot gate /api/logs because JSON is malformed: {result.get('parseError')}")
    return validate_server_log_ring_payload(result.get("payload"))


def consume_only_expected_server_log_errors(
    driver,
    expected: Sequence[Mapping[str, Any]],
    *,
    server_log_boundary: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Validate and consume an exact ordered list of deliberate warning/error ring entries."""

    begin_browser_journey_surface_tracking(driver)
    raw_payload = _read_server_log_ring(driver)
    payload = (
        _server_log_ring_after_boundary(raw_payload, server_log_boundary)
        if server_log_boundary is not None
        else raw_payload
    )
    epoch = _server_log_epoch(raw_payload)
    consumed_ids = _consumed_server_log_ids(driver, epoch)
    failures = [
        entry for entry in payload["logs"]
        if str(entry.get("level") or "").lower() in {"warning", "error"}
        and entry.get("id") not in consumed_ids
    ]
    expected_shapes = [
        {
            "level": str(item.get("level") or "").lower(),
            "source": str(item.get("source") or ""),
            "category": str(item.get("category") or ""),
            "message": str(item.get("message") or ""),
        }
        for item in expected
    ]
    actual_shapes = [_server_log_failure_shape(entry) for entry in failures]
    if actual_shapes != expected_shapes:
        raise AssertionError(
            "expected exact server log error list: "
            f"{json.dumps({'expected': expected_shapes, 'actual': actual_shapes}, sort_keys=True)}"
        )
    event_ids = [entry.get("id") for entry in failures]
    if any(isinstance(event_id, bool) or not isinstance(event_id, int) for event_id in event_ids):
        raise AssertionError("expected server log failures to carry integer IDs")
    registered = driver.execute_script(
        """
        const state = window.__yolomuxBrowserJourneyGate;
        if (!state || !Array.isArray(state.consumedServerLogIds)
            || state.serverLogEpoch !== arguments[0]) return false;
        const known = new Set(state.consumedServerLogIds);
        for (const id of arguments[1]) {
          if (!known.has(id)) state.consumedServerLogIds.push(id);
        }
        return true;
        """,
        epoch,
        event_ids,
    )
    if registered is not True:
        raise AssertionError("browser journey cannot consume server log errors because tracking is unreachable")
    return tuple(dict(entry) for entry in failures)


def begin_browser_journey_surface_tracking(driver) -> dict[str, Any]:
    """Observe product-owned surface visits for the lifetime of the current page."""

    state = driver.execute_script(
        """
        const existing = window.__yolomuxBrowserJourneyGate;
        if (typeof existing !== 'undefined') {
          if (!existing || typeof existing !== 'object'
              || !Array.isArray(existing.visitedSurfaces)
              || !Array.isArray(existing.consumedServerLogIds)
              || !Object.prototype.hasOwnProperty.call(existing, 'serverLogEpoch')
              || (existing.serverLogEpoch !== null && typeof existing.serverLogEpoch !== 'string')) {
            return {malformed: true, visitedSurfaces: []};
          }
          if (existing.observer && typeof existing.observe === 'function') {
            existing.observe();
            return {malformed: false, visitedSurfaces: [...existing.visitedSurfaces]};
          }
          if (existing.observer !== null || existing.observe !== null) {
            return {malformed: true, visitedSurfaces: []};
          }
        }
        const state = existing || {
          visitedSurfaces: [],
          consumedServerLogIds: [],
          serverLogEpoch: null,
          observer: null,
          observe: null,
        };
        const visible = element => {
          if (!element?.isConnected) return false;
          const style = getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          return style.display !== 'none' && style.visibility !== 'hidden'
            && Number.parseFloat(style.opacity || '1') > 0 && rect.width > 0 && rect.height > 0;
        };
        state.observe = () => {
          if (visible(document.querySelector('.js-debug-panel')) && !state.visitedSurfaces.includes('stats')) {
            state.visitedSurfaces.push('stats');
          }
        };
        state.observer = new MutationObserver(state.observe);
        state.observer.observe(document.documentElement, {
          childList: true,
          subtree: true,
          attributes: true,
          attributeFilter: ['class', 'hidden', 'style'],
        });
        window.__yolomuxBrowserJourneyGate = state;
        state.observe();
        return {malformed: false, visitedSurfaces: [...state.visitedSurfaces]};
        """
    )
    if not isinstance(state, Mapping) or state.get("malformed") is not False:
        raise AssertionError("browser journey tracking state is malformed")
    visited = state.get("visitedSurfaces")
    if not isinstance(visited, list) or any(not isinstance(surface, str) for surface in visited):
        raise AssertionError("browser journey visited surfaces are malformed")
    return dict(state)


def _read_browser_journey_surfaces(driver, claimed_clean_surfaces: Sequence[str]) -> dict[str, Any]:
    state = driver.execute_script(
        """
        const state = window.__yolomuxBrowserJourneyGate;
        if (!state || !Array.isArray(state.visitedSurfaces)) return {reachable: false, visitedSurfaces: []};
        state.observe?.();
        return {reachable: true, visitedSurfaces: [...state.visitedSurfaces]};
        """
    )
    return _validate_browser_journey_surfaces(state, claimed_clean_surfaces)


def _validate_browser_journey_surfaces(
    state: Any,
    claimed_clean_surfaces: Sequence[str],
) -> dict[str, Any]:
    claimed = tuple(dict.fromkeys(str(surface) for surface in claimed_clean_surfaces))
    unsupported = [surface for surface in claimed if surface not in {"stats"}]
    if unsupported:
        raise AssertionError(f"browser journey claimed unknown clean surfaces: {unsupported}")
    if claimed and (not isinstance(state, Mapping) or state.get("reachable") is not True):
        raise AssertionError("browser journey cannot verify claimed clean surfaces because tracking is unreachable")
    visited = list(state.get("visitedSurfaces") or ()) if isinstance(state, Mapping) else []
    missing = [surface for surface in claimed if surface not in visited]
    if missing:
        raise AssertionError(f"browser journey claimed clean surface {', '.join(missing)} not visited")
    return {"claimedCleanSurfaces": list(claimed), "visitedSurfaces": visited}


def _read_js_debug_store(driver, *, required: bool = True) -> Mapping[str, Any]:
    js_debug = driver.execute_script(
        """
        const eventsDefined = typeof jsDebugEvents !== 'undefined';
        const failureReaderDefined = typeof jsDebugFailureEvents === 'function';
        const receiptBarrierDefined = typeof jsDebugCurrentObservationReceiptBarrier === 'function';
        if (!eventsDefined && !failureReaderDefined && !receiptBarrierDefined) {
          return {reachable: false, isArray: false, events: [], errors: []};
        }
        if (!eventsDefined || !failureReaderDefined || !receiptBarrierDefined) {
          return {reachable: true, isArray: false, events: [], errors: []};
        }
        const isArray = Array.isArray(jsDebugEvents);
        const events = isArray ? Array.from(jsDebugEvents) : [];
        return {
          reachable: true,
          isArray,
          events,
          errors: jsDebugFailureEvents(),
          receiptBarrier: jsDebugCurrentObservationReceiptBarrier(),
        };
        """
    )
    return _validate_js_debug_store(js_debug, required=required)


def _validate_js_debug_store(js_debug: Any, *, required: bool) -> Mapping[str, Any]:
    if not isinstance(js_debug, Mapping) or js_debug.get("reachable") is not True:
        if not required and isinstance(js_debug, Mapping) and js_debug.get("reachable") is False:
            return js_debug
        raise AssertionError("browser journey cannot gate JS errors because jsDebugEvents is unreachable")
    if js_debug.get("isArray") is not True:
        raise AssertionError("browser journey cannot gate JS errors because jsDebugEvents is not an array")
    return {
        **js_debug,
        "receiptBarrier": browser_diagnostic_receipts.validate_browser_receipt_barrier(js_debug.get("receiptBarrier")),
    }


def _browser_local_failure_evidence(event: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the correlation fields needed to diagnose one retained browser failure."""

    level = str(event.get("level") or "").lower()
    status = event.get("status") if isinstance(event.get("status"), int) else None
    delivery_outcome = str(event.get("deliveryOutcome") or event.get("outcome") or "")
    if not delivery_outcome:
        if event.get("error") or event.get("ok") is False or (status is not None and status >= 400):
            delivery_outcome = "failed"
        elif event.get("ok") is True or (status is not None and status < 400):
            delivery_outcome = "delivered"
        elif level in {"warning", "error"}:
            delivery_outcome = "failed"
        else:
            delivery_outcome = "unknown"
    route = str(event.get("endpoint") or event.get("url") or "")
    event_name = str(event.get("eventType") or event.get("type") or "")
    return {
        "id": event.get("id"),
        "level": level,
        "message": str(event.get("message") or event.get("error") or event.get("reason") or ""),
        "requestId": str(event.get("requestId") or ""),
        "source": str(event.get("source") or "browser"),
        "route": route,
        "event": event_name,
        "wallTime": str(event.get("wallTime") or event.get("ts") or ""),
        "deliveryOutcome": delivery_outcome,
        "status": status,
    }


def _read_browser_local_error_evidence(
    driver,
    *,
    claimed_clean_surfaces: Sequence[str],
    require_js_debug_store: bool,
) -> dict[str, Any]:
    js_debug = _read_js_debug_store(driver, required=require_js_debug_store)
    surface_evidence = _read_browser_journey_surfaces(driver, claimed_clean_surfaces)
    console_entries = read_browser_console_log(driver)
    return _browser_local_error_evidence_from_snapshots(js_debug, surface_evidence, console_entries)


def _browser_local_error_evidence_from_snapshots(
    js_debug: Mapping[str, Any],
    surface_evidence: Mapping[str, Any],
    console_entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    js_debug_errors = list(js_debug.get("errors") or ())
    failure_events: list[Mapping[str, Any]] = []
    failure_ids: set[Any] = set()
    for event in (*js_debug_errors, *(js_debug.get("events") or ())):
        if not isinstance(event, Mapping):
            continue
        level = str(event.get("level") or "").lower()
        if event not in js_debug_errors and level not in {"warning", "error"}:
            continue
        event_id = event.get("id")
        identity = event_id if event_id is not None else json.dumps(dict(event), sort_keys=True)
        if identity in failure_ids:
            continue
        failure_ids.add(identity)
        failure_events.append(event)
    browser_local_failures = [_browser_local_failure_evidence(event) for event in failure_events]
    receipt_barrier = js_debug.get("receiptBarrier")
    browser_log_failures = [
        entry
        for entry in console_entries
        if str(entry.get("level") or "").upper() in {"WARNING", "SEVERE"}
        and not _is_known_codemirror_measure_warning(entry)
    ]
    return {
        "jsDebugStoreReachable": js_debug.get("reachable") is True,
        "jsDebugEventCount": len(js_debug.get("events") or ()),
        "jsDebugErrors": js_debug_errors,
        "browserLocalFailures": browser_local_failures,
        "browserReceiptBarrier": receipt_barrier,
        **surface_evidence,
        "browserLogFailures": browser_log_failures,
        "severeBrowserLogEntries": [
            entry for entry in browser_log_failures
            if str(entry.get("level") or "").upper() == "SEVERE"
        ],
    }


def retire_browser_after_strict_diagnostic_gate(
    driver,
    *,
    claimed_clean_surfaces: Sequence[str] = (),
    require_js_debug_store: bool = True,
) -> dict[str, Any]:
    """Atomically snapshot browser diagnostics and retire the page in one JS task."""

    snapshot = driver.execute_script(
        """
        const eventsDefined = typeof jsDebugEvents !== 'undefined';
        const failureReaderDefined = typeof jsDebugFailureEvents === 'function';
        const receiptBarrierDefined = typeof jsDebugCurrentObservationReceiptBarrier === 'function';
        let jsDebug;
        if (!eventsDefined && !failureReaderDefined && !receiptBarrierDefined) {
          jsDebug = {reachable: false, isArray: false, events: [], errors: []};
        } else if (!eventsDefined || !failureReaderDefined || !receiptBarrierDefined) {
          jsDebug = {reachable: true, isArray: false, events: [], errors: []};
        } else {
          const isArray = Array.isArray(jsDebugEvents);
          jsDebug = {
            reachable: true,
            isArray,
            events: isArray ? Array.from(jsDebugEvents) : [],
            errors: jsDebugFailureEvents(),
            receiptBarrier: jsDebugCurrentObservationReceiptBarrier(),
          };
        }
        const journeyState = window.__yolomuxBrowserJourneyGate;
        journeyState?.observe?.();
        const journey = journeyState && Array.isArray(journeyState.visitedSurfaces)
          ? {reachable: true, visitedSurfaces: [...journeyState.visitedSurfaces]}
          : {reachable: false, visitedSurfaces: []};
        const result = {jsDebug, journey};
        window.location.replace('about:blank');
        return result;
        """
    )
    # ``location.replace`` starts retirement in the same task as the diagnostic snapshot, but
    # WebDriver may return before the navigation commits.  Observe that commit before fixture
    # cleanup so persistent HTTP transports cannot remain attached to a server that is stopping.
    WebDriverWait(driver, 3, poll_frequency=0.05).until(
        lambda current: str(current.current_url) == "about:blank"
    )
    if not isinstance(snapshot, Mapping):
        raise AssertionError("browser diagnostic retirement snapshot is malformed")
    js_debug = _validate_js_debug_store(snapshot.get("jsDebug"), required=require_js_debug_store)
    surface_evidence = _validate_browser_journey_surfaces(snapshot.get("journey"), claimed_clean_surfaces)
    evidence = _browser_local_error_evidence_from_snapshots(
        js_debug,
        surface_evidence,
        read_browser_console_log(driver),
    )
    if _browser_local_evidence_has_failure(evidence):
        raise AssertionError(f"browser journey emitted errors at retirement: {json.dumps(evidence, sort_keys=True)}")
    return evidence


def _browser_local_evidence_has_failure(evidence: Mapping[str, Any]) -> bool:
    receipt = evidence.get("browserReceiptBarrier")
    receipt_failed = evidence.get("jsDebugStoreReachable") is True and (
        not isinstance(receipt, Mapping) or receipt.get("quiescent") is not True
    )
    return bool(
        evidence.get("browserLocalFailures")
        or evidence.get("browserLogFailures")
        or receipt_failed
    )


def _assert_observed_browser_evidence(
    driver,
    read_evidence,
    has_failure,
    *,
    observation_seconds: float,
) -> dict[str, Any]:
    if isinstance(observation_seconds, bool) or not isinstance(observation_seconds, Real) or observation_seconds < 0:
        raise ValueError("browser journey observation seconds must be a non-negative number")

    samples = 0

    def sample() -> dict[str, Any]:
        nonlocal samples
        samples += 1
        return read_evidence()

    evidence = sample()
    if not has_failure(evidence) and observation_seconds:
        def poll_for_failure(_driver):
            nonlocal evidence
            evidence = sample()
            return evidence if has_failure(evidence) else False

        try:
            WebDriverWait(
                driver,
                float(observation_seconds),
                poll_frequency=BROWSER_JOURNEY_POLL_SECONDS,
            ).until(poll_for_failure)
        except TimeoutException:
            pass

    evidence["observationSeconds"] = float(observation_seconds)
    evidence["observationSamples"] = samples
    if has_failure(evidence):
        raise AssertionError(f"browser journey emitted errors: {json.dumps(evidence, sort_keys=True)}")
    return evidence


def assert_browser_local_error_free(
    driver,
    *,
    claimed_clean_surfaces: Sequence[str] = (),
    observation_seconds: float = 0.0,
) -> dict[str, Any]:
    """Gate retained browser-owned diagnostics without assuming a live server ring."""

    return _assert_observed_browser_evidence(
        driver,
        lambda: _read_browser_local_error_evidence(
            driver,
            claimed_clean_surfaces=claimed_clean_surfaces,
            require_js_debug_store=False,
        ),
        _browser_local_evidence_has_failure,
        observation_seconds=observation_seconds,
    )


def assert_browser_journey_error_free(
    driver,
    *,
    claimed_clean_surfaces: Sequence[str] = (),
    observation_seconds: float = 0.0,
    server_log_reader: Callable[[], Mapping[str, Any]] | None = None,
    server_log_boundary: Mapping[str, Any] | None = None,
    require_js_debug_store: bool = True,
) -> dict[str, Any]:
    """Gate a product journey on retained browser, server, and Chrome diagnostics."""

    effective_server_log_boundary = (
        server_log_boundary
        if server_log_boundary is not None
        else getattr(driver, "_yolomux_server_log_boundary", None)
    )

    def read_evidence() -> dict[str, Any]:
        browser_evidence = _read_browser_local_error_evidence(
            driver,
            claimed_clean_surfaces=claimed_clean_surfaces,
            require_js_debug_store=require_js_debug_store,
        )
        raw_server_log_payload = server_log_ring_cursor(
            _read_server_log_ring(driver) if server_log_reader is None else server_log_reader()
        )
        server_log_payload = raw_server_log_payload
        if effective_server_log_boundary is not None:
            server_log_payload = _server_log_ring_after_boundary(
                server_log_payload,
                effective_server_log_boundary,
            )
        server_log_epoch = _server_log_epoch(server_log_payload)
        consumed_server_log_ids = _consumed_server_log_ids(driver, server_log_epoch)
        server_log_errors = [
            dict(entry) for entry in server_log_payload["logs"]
            if str(entry.get("level") or "").lower() in {"warning", "error"}
            and entry.get("id") not in consumed_server_log_ids
        ]
        return {
            **browser_evidence,
            "serverLogRingReachable": True,
            "serverLogEpoch": server_log_epoch,
            "serverLogEntryCount": len(server_log_payload["logs"]),
            "serverLogDropped": dict(server_log_payload["dropped"]),
            "serverLogErrors": server_log_errors,
            "serverLogCursor": raw_server_log_payload,
        }

    def has_failure(evidence: Mapping[str, Any]) -> bool:
        dropped = evidence.get("serverLogDropped")
        dropped_count = dropped.get("count", 0) if isinstance(dropped, Mapping) else 0
        return bool(
            evidence.get("browserLocalFailures")
            or evidence.get("serverLogErrors")
            or evidence.get("browserLogFailures")
            or (
                require_js_debug_store
                and (
                    not isinstance(evidence.get("browserReceiptBarrier"), Mapping)
                    or evidence["browserReceiptBarrier"].get("quiescent") is not True
                )
            )
            or dropped_count
        )

    return _assert_observed_browser_evidence(
        driver,
        read_evidence,
        has_failure,
        observation_seconds=observation_seconds,
    )
