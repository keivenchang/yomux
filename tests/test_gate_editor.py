from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from contextlib import contextmanager
from http import HTTPStatus
from http.client import HTTPConnection
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401
from tests.browser_helpers.browser_layout import assert_live_runtime_boot_healthy
from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_console import assert_browser_journey_error_free
from tests.browser_helpers.browser_console import assert_only_expected_browser_http_error
from tests.browser_helpers.browser_console import validate_server_log_ring_payload
from tests.browser_helpers.browser_console import validate_server_log_ring_transition
from tests.browser_helpers.browser_layout import start_browser_server
from tests.browser_helpers.browser_layout import start_isolated_browser_app
from tests.browser_helpers.browser_layout import stop_browser_server
from tests.browser_helpers.browser_layout import stop_isolated_browser_app
from tests.gate_harness import repeat
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import wait_for_browser_boot
from tests.terminal_state_guard import assert_terminal_transition
from yolomux_lib import filesystem
from yolomux_lib.observability.failure_severity import EXPECTED_OUTCOME_LOG_LEVEL
from yolomux_lib.observability.failure_severity import FAULT_LOG_LEVEL
from yolomux_lib.server_logs import SERVER_LOGS


pytestmark = pytest.mark.socket

FILE_OPEN_BUDGET_SECONDS = 0.5
EDITOR_GLOBALS = {
    "fileEditorItemFor": "function",
    "fileEditorPanelsForPath": "function",
    "markOpenFileMissing": "function",
    "openFileInEditor": "function",
    "clientEventDemandDescriptor": "function",
}


def _auth_header() -> dict[str, str]:
    encoded = base64.b64encode(b"gate-admin:gate-password").decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def _request_once(
    port: int,
    path: str,
    *,
    response_times: list[float] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    started = time.perf_counter()
    try:
        connection.request("GET", path, headers=_auth_header())
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        if response_times is not None:
            response_times.append(time.perf_counter() - started)
        return result
    finally:
        connection.close()


def _request(
    port: int,
    path: str,
    *,
    response_times: list[float] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Consume one filesystem receipt through its exact terminal HTTP result."""

    status, headers, body = _request_once(port, path, response_times=response_times)
    if status != HTTPStatus.ACCEPTED:
        return status, headers, body
    receipt = json.loads(body)
    assert receipt["state"] == "queued" and receipt.get("terminal") is not True, receipt
    request_id = receipt["request"]["id"]
    operation = receipt["operation"]
    operation_id = operation["id"]
    status_url = operation["status_url"]
    assert status_url == f"/api/operations/{operation_id}", receipt
    events_url = operation["events_url"]
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    terminal_event = None
    event_type = ""
    try:
        connection.request("GET", events_url, headers=_auth_header())
        response = connection.getresponse()
        assert response.status == HTTPStatus.OK, (response.status, response.read())
        while terminal_event is None:
            line = response.readline()
            assert line, f"filesystem operation {operation_id} event stream ended before terminalization"
            if line.startswith(b"event: "):
                event_type = line.decode("utf-8").removeprefix("event: ").strip()
            elif event_type == "operation_terminal" and line.startswith(b"data: "):
                terminal_event = json.loads(line.decode("utf-8").removeprefix("data: "))["payload"]
    finally:
        connection.close()
    assert terminal_event["operation"]["id"] == operation_id, terminal_event
    assert terminal_event["result"]["request"]["id"] == request_id, terminal_event
    status, headers, body = _request_once(port, status_url, response_times=response_times)
    payload = json.loads(body)
    assert status == terminal_event["status"], (status, terminal_event)
    assert payload == terminal_event["result"], (payload, terminal_event)
    assert payload["state"] in {"ready", "failed"}, payload
    return status, headers, body


@contextmanager
def _running_server(monkeypatch: pytest.MonkeyPatch, gate_runtime_paths):
    runtime = start_isolated_browser_app(
        monkeypatch,
        gate_runtime_paths.root,
        dangerously_yolo=False,
    )
    server = None
    thread = None
    try:
        server, thread = start_browser_server(
            monkeypatch,
            runtime.paths.config_dir,
            runtime.app,
            auth_bypass=True,
        )
        yield SimpleNamespace(port=server.server_address[1], server=server)
    finally:
        if server is not None and thread is not None:
            stop_browser_server(server, thread)
        stop_isolated_browser_app(runtime)


from tests.helpers.gate_editor import A8_FRAME_COUNTS
from tests.helpers.gate_editor import _a8_latch_transient_inode_miss
from tests.helpers.gate_editor import _a8_missing_snapshot
from tests.helpers.gate_editor import _a8_recovered_snapshot
from tests.helpers.gate_editor import _a8_replace_inode
from tests.helpers.gate_editor import _a8_wait_varying_subsecond_interval
from tests.helpers.gate_editor import _dirty_conflict_snapshot
from tests.helpers.gate_editor import _open_editor
from tests.helpers.gate_editor import _publish_file_change
from tests.helpers.gate_editor import _type_dirty_text
from tests.helpers.gate_editor import _wait_for_file_event_stream
from tests.helpers.gate_editor import gate_browser_runtime  # noqa: F401


@pytest.mark.no_browser
def test_a1_existing_file_opens_twenty_consecutive_times_under_budget(monkeypatch, tmp_path, gate_runtime_paths):
    """A1: GET /api/fs/read must terminalize the exact existing-file content 20 consecutive times under 500 ms without 503."""
    expected = "gate file content\n"
    target = tmp_path / "existing.txt"
    target.write_text(expected, encoding="utf-8")
    path = f"/api/fs/read?{urlencode({'path': str(target)})}"

    with _running_server(monkeypatch, gate_runtime_paths) as runtime:
        prime_status, _prime_headers, prime_body = _request(runtime.port, path)
        prime = json.loads(prime_body)
        assert prime_status == HTTPStatus.OK and prime["state"] == "ready", prime
        assert prime["data"]["content"] == expected, prime
        for iteration in range(1, 21):
            response_times = []
            status, _headers, body = _request(runtime.port, path, response_times=response_times)
            payload = json.loads(body)
            assert status != HTTPStatus.SERVICE_UNAVAILABLE, f"iteration {iteration} returned 503: {payload}"
            assert status == HTTPStatus.OK, f"iteration {iteration} returned {status}: {payload}"
            assert payload["state"] == "ready", payload
            assert payload["data"]["content"] == expected, f"iteration {iteration} returned the wrong content"
            assert response_times and max(response_times) < FILE_OPEN_BUDGET_SECONDS, (
                f"iteration {iteration} finite response took {max(response_times):.3f}s"
            )


@pytest.mark.browser
def test_a2_editor_renders_exact_disk_content_in_codemirror(gate_browser_runtime, tmp_path):
    """A2: opening a fixture file in the browser must render a non-empty CodeMirror document whose text exactly matches the file on disk, not merely return a successful API payload."""
    expected = "rendered through the real file API\n"
    target = tmp_path / "a2-rendered.txt"
    target.write_text(expected, encoding="utf-8")
    _open_editor(gate_browser_runtime, target, expected)


@pytest.mark.browser
def test_a3_clean_buffer_converges_after_external_rewrite(gate_browser_runtime, tmp_path):
    """A3: after an external process rewrites an open file whose editor buffer is clean, the rendered CodeMirror document must converge to the new disk content without a manual reload."""
    target = tmp_path / "a3-clean.txt"
    original = "clean original\n"
    replacement = "clean external replacement\n"
    target.write_text(original, encoding="utf-8")
    _open_editor(gate_browser_runtime, target, original)
    _wait_for_file_event_stream(gate_browser_runtime, target)
    gate_browser_runtime.browser.execute_script("document.activeElement?.blur?.(); document.body.focus();")
    target.write_text(replacement, encoding="utf-8")
    _publish_file_change(gate_browser_runtime, target)
    metrics = gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const expected = arguments[1];
        const done = arguments[arguments.length - 1];
        window.__yolomuxTestWaitFor(() => {
          const state = fileState.get(path);
          const panel = fileEditorPanelsForPath(path)[0];
          return state?.dirty === false && panel?._cmView?.state?.doc?.toString?.() === expected;
        }, {timeoutMs: 10000, description: `clean external reload for ${path}`}).then(() => {
          const state = fileState.get(path);
          const panel = fileEditorPanelsForPath(path)[0];
          done({text: panel?._cmView?.state?.doc?.toString?.() || '', dirty: state?.dirty === true, externalChanged: Boolean(state?.externalChanged), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        }, error => done({error: String(error?.stack || error)}));
        """,
        str(target),
        replacement,
    )
    assert not metrics.get("error"), metrics
    assert metrics["text"] == replacement and metrics["dirty"] is False and metrics["externalChanged"] is False, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


_SHARED_RELOAD_SCRIPT = """
const path = arguments[0];
const expected = arguments[1];
const replacementPayload = arguments[2];
const pushSignature = arguments[3];
const done = arguments[arguments.length - 1];
(async () => {
  const nativeFetch = window.fetch.bind(window);
  const operationId = 'op-gate-shared-reload';
  const requestId = 'r-gate-shared-reload';
  let reads = 0;
  window.fetch = async (input, options = {}) => {
    const url = new URL(String(input), location.href);
    if (url.pathname !== '/api/fs/read' || url.searchParams.get('path') !== path) {
      return nativeFetch(input, options);
    }
    reads += 1;
    if (reads === 1) {
      return new Response(JSON.stringify({
        state: 'queued',
        request: {id: requestId},
        operation: {
          id: operationId,
          kind: 'filesystem_operation',
          status_url: `/api/operations/${operationId}`,
          events_url: `/api/client-events?operation_id=${operationId}`,
          cursor: {epoch: 'gate-shared', seq: 0},
          context: {operation: 'read', path, product_key: `gate-shared:${path}`},
        },
      }), {status: 202, headers: {'Content-Type': 'application/json'}});
    }
    return nativeFetch(input, options);
  };
  try {
    const reload = reloadOpenFileFromDisk(path, {force: true});
    await window.__yolomuxTestWaitFor(() => reads === 1, {
      timeoutMs: 5000,
      description: `queued explicit reload for ${path}`,
    });
    // The push refresh lands while the explicit reload is still waiting on its operation. Both
    // want the same file; only one /api/fs/read should exist unless the push knows about content
    // the shared reload cannot reach.
    const pushed = refreshOpenFilesFromPush({files: [{path, signature: pushSignature}]});
    handleClientPushEventNow('operation_terminal', {
      operation: {id: operationId, kind: 'filesystem_operation', cursor: {epoch: 'gate-shared', seq: 1}},
      result: {
        state: 'ready',
        request: {id: requestId},
        data: replacementPayload,
        quality: {complete: true, stale: false},
        warnings: [],
      },
    });
    await reload;
    await pushed;
    await window.__yolomuxTestWaitFor(() => {
      const state = fileState.get(path);
      const panel = fileEditorPanelsForPath(path)[0];
      return state?.dirty === false && panel?._cmView?.state?.doc?.toString?.() === expected;
    }, {timeoutMs: 10000, description: `shared reload settle for ${path}`});
    // Give any follow-up read the push may have decided it still needs time to be issued.
    await new Promise(resolve => setTimeout(resolve, 1500));
    const state = fileState.get(path);
    done({
      reads,
      text: fileEditorPanelsForPath(path)[0]?._cmView?.state?.doc?.toString?.() || '',
      original: state?.original || '',
      errors: jsDebugFailureEvents('error'),
      rejections: jsDebugFailureEvents('rejection'),
    });
  } catch (error) {
    done({error: String(error?.stack || error), reads});
  } finally {
    window.fetch = nativeFetch;
  }
})();
"""


@pytest.mark.browser
def test_explicit_reload_and_push_refresh_for_one_path_share_one_read(gate_browser_runtime, tmp_path):
    """An explicit reload and a files_changed push for the same file issue one /api/fs/read.

    Before filesystem reads had their own jobd lane this held by accident: a read could not be
    dispatched while the explicit reload's directory batch held the single shared interactive
    worker.  With a reserved point lane the two run concurrently, so the deduplication has to be
    explicit.  Serialization is not deduplication.
    """
    target = tmp_path / "shared-reload.txt"
    original = "content before shared reload\n"
    replacement = "fresh external bytes after shared reload\n"
    target.write_text(original, encoding="utf-8")
    _open_editor(gate_browser_runtime, target, original)
    target.write_text(replacement, encoding="utf-8")
    replacement_payload = filesystem.read_file(str(target))
    # The push describes exactly the rewrite the explicit reload is already fetching.
    push_signature = [
        str(target),
        "file",
        int(replacement_payload.get("mtime_ns") or 0),
        len(replacement.encode("utf-8")),
    ]
    metrics = gate_browser_runtime.browser.execute_async_script(
        _SHARED_RELOAD_SCRIPT,
        str(target),
        replacement,
        replacement_payload,
        push_signature,
    )
    assert not metrics.get("error"), metrics
    assert metrics["reads"] == 1, metrics
    assert metrics["text"] == replacement and metrics["original"] == replacement, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_push_refresh_naming_newer_content_than_the_shared_reload_still_reads_again(gate_browser_runtime, tmp_path):
    """Negative control: the shared reload is only reused when it reaches the pushed content.

    The push names an mtime and size the explicit reload's result does not satisfy, so joining it
    would leave the editor behind the filesystem.  Exactly one additional read must follow.  A
    dedup that could never fall through would silently pin stale bytes.
    """
    target = tmp_path / "shared-reload-newer.txt"
    original = "content before newer push\n"
    replacement = "fresh external bytes after newer push\n"
    target.write_text(original, encoding="utf-8")
    _open_editor(gate_browser_runtime, target, original)
    target.write_text(replacement, encoding="utf-8")
    replacement_payload = filesystem.read_file(str(target))
    # A strictly newer change than the one the explicit reload will return: later mtime, larger
    # size. Well outside FILE_MTIME_NS_CHANGE_TOLERANCE so it cannot be read as precision drift.
    push_signature = [
        str(target),
        "file",
        int(replacement_payload.get("mtime_ns") or 0) + 5_000_000_000,
        len(replacement.encode("utf-8")) + 4096,
    ]
    metrics = gate_browser_runtime.browser.execute_async_script(
        _SHARED_RELOAD_SCRIPT,
        str(target),
        replacement,
        replacement_payload,
        push_signature,
    )
    assert not metrics.get("error"), metrics
    assert metrics["reads"] == 2, metrics
    assert metrics["text"] == replacement and metrics["original"] == replacement, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_a3_explicit_reload_waits_for_exact_bytes_after_external_rewrite(gate_browser_runtime, tmp_path):
    """A3 amend: an explicit reload renders exact bytes from its queued operation terminal."""
    target = tmp_path / "a3-explicit-reload.txt"
    original = "content before explicit reload\n"
    replacement = "fresh external bytes after explicit reload\n"
    target.write_text(original, encoding="utf-8")
    _open_editor(gate_browser_runtime, target, original)
    target.write_text(replacement, encoding="utf-8")
    replacement_payload = filesystem.read_file(str(target))
    metrics = gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const expected = arguments[1];
        const replacementPayload = arguments[2];
        const done = arguments[arguments.length - 1];
        (async () => {
          const nativeFetch = window.fetch.bind(window);
          const key = `gate-exact-read:${path}`;
          const operationId = 'op-gate-a3-reload';
          const requestId = 'r-gate-a3-reload';
          let reads = 0;
          window.fetch = async (input, options = {}) => {
            const url = new URL(String(input), location.href);
            if (url.pathname !== '/api/fs/read' || url.searchParams.get('path') !== path) {
              return nativeFetch(input, options);
            }
            reads += 1;
            if (reads === 1) {
              return new Response(JSON.stringify({
                state: 'queued',
                request: {id: requestId},
                operation: {
                  id: operationId,
                  kind: 'filesystem_operation',
                  status_url: `/api/operations/${operationId}`,
                  events_url: `/api/client-events?operation_id=${operationId}`,
                  cursor: {epoch: 'gate-a3', seq: 0},
                  context: {operation: 'read', path, product_key: key},
                },
              }), {
                status: 202,
                headers: {'Content-Type': 'application/json'},
              });
            }
            return nativeFetch(input, options);
          };
          try {
            const reload = reloadOpenFileFromDisk(path, {force: true});
            await window.__yolomuxTestWaitFor(() => reads === 1, {
              timeoutMs: 3000,
              description: `queued explicit reload for ${path}`,
            });
            handleClientPushEventNow('operation_terminal', {
              operation: {id: operationId, kind: 'filesystem_operation', cursor: {epoch: 'gate-a3', seq: 1}},
              result: {
                state: 'ready',
                request: {id: requestId},
                data: replacementPayload,
                quality: {complete: true, stale: false},
                warnings: [],
              },
            });
            await reload;
            await window.__yolomuxTestWaitFor(() => {
              const state = fileState.get(path);
              const panel = fileEditorPanelsForPath(path)[0];
              return state?.dirty === false && panel?._cmView?.state?.doc?.toString?.() === expected;
            }, {timeoutMs: 10000, description: `exact explicit reload for ${path}`});
            const state = fileState.get(path);
            const panel = fileEditorPanelsForPath(path)[0];
            done({
              reads,
              text: panel?._cmView?.state?.doc?.toString?.() || '',
              original: state?.original || '',
              kind: state?.kind || '',
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error?.stack || error), reads});
          } finally {
            window.fetch = nativeFetch;
          }
        })();
        """,
        str(target),
        replacement,
        replacement_payload,
    )
    assert not metrics.get("error"), metrics
    assert metrics["reads"] == 1, metrics
    assert metrics["text"] == replacement and metrics["original"] == replacement, metrics
    assert metrics["kind"] == "text", metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_a4_dirty_buffer_survives_external_rewrite_and_surfaces_conflict(gate_browser_runtime, tmp_path):
    """A4: after typing unsaved text and externally rewriting that same file, the rendered CodeMirror document must preserve the user's unsaved text and the editor must show a visible conflict state; this test remains in the same file as A3."""
    target = tmp_path / "a4-dirty.txt"
    original = "dirty original\n"
    target.write_text(original, encoding="utf-8")
    _open_editor(gate_browser_runtime, target, original)
    _wait_for_file_event_stream(gate_browser_runtime, target)
    dirty_text = _type_dirty_text(gate_browser_runtime, target, "user unsaved text")
    target.write_text("external rewrite must not win\n", encoding="utf-8")
    _publish_file_change(gate_browser_runtime, target)
    metrics = _dirty_conflict_snapshot(gate_browser_runtime, target, dirty_text)
    assert not metrics.get("error"), metrics
    assert metrics["dirty"] is True and metrics["text"] == dirty_text, metrics
    assert metrics["path"] == str(target) and metrics["tabConnected"] is True and metrics["missing"] is False, metrics
    assert metrics["status"] and metrics["statusDisplay"] != "none", metrics


@pytest.mark.browser
def test_a5_subsecond_replace_keeps_editor_path_tab_and_content_state(gate_browser_runtime, tmp_path):
    """A5: across 10 cp-or-mv replacements completed in under one second at varying sub-second intervals, the editor must retain the same path and tab, never render File not found, and show either new clean content or the preserved dirty buffer with a visible conflict notice."""
    target = tmp_path / "a5-replaced.txt"
    source = tmp_path / "a5-source.txt"
    original = "replace original\n"
    target.write_text(original, encoding="utf-8")
    _open_editor(gate_browser_runtime, target, original)
    _wait_for_file_event_stream(gate_browser_runtime, target)
    dirty_text = _type_dirty_text(gate_browser_runtime, target, "ten-replace unsaved buffer")
    frame_counts = (1, 2, 4, 3, 1, 5, 2, 4, 1, 3)
    previous_signature = ""

    def replace_once(iteration):
        nonlocal previous_signature
        frame_count = frame_counts[iteration - 1]
        gate_browser_runtime.browser.execute_async_script(
            """
            let remaining = arguments[0];
            const done = arguments[arguments.length - 1];
            const next = () => {
              remaining -= 1;
              if (remaining <= 0) done(true);
              else requestAnimationFrame(next);
            };
            requestAnimationFrame(next);
            """,
            frame_count,
        )
        source.write_text(f"external replacement {iteration} with distinct size {'x' * iteration}\n", encoding="utf-8")
        started = time.perf_counter()
        subprocess.run(["cp", "-f", str(source), str(target)], check=True, timeout=0.9)
        elapsed = time.perf_counter() - started
        assert elapsed < 1.0, f"replace {iteration} took {elapsed:.3f}s"
        _publish_file_change(gate_browser_runtime, target)
        metrics = _dirty_conflict_snapshot(gate_browser_runtime, target, dirty_text, previous_signature)
        assert not metrics.get("error"), metrics
        assert metrics["dirty"] is True and metrics["text"] == dirty_text, metrics
        assert metrics["path"] == str(target) and metrics["tabConnected"] is True, metrics
        assert metrics["missing"] is False and metrics["status"] and metrics["statusDisplay"] != "none", metrics
        assert "File not found" not in metrics["body"], metrics
        previous_signature = metrics["signature"]
        return elapsed

    elapsed_replaces = repeat(10, replace_once)
    assert len(set(frame_counts)) > 1
    assert len(elapsed_replaces) == 10 and max(elapsed_replaces) < 1.0


@pytest.mark.no_browser
def test_a6_rapid_read_connection_close_and_reopen_completes_every_request(monkeypatch, tmp_path, gate_runtime_paths):
    """A6: 10 rapid open/close/reopen cycles for the same file must return the exact content on both opens in every cycle, with each HTTP response fully consumed and no request left pending."""
    expected = "rapid reopen content\n"
    target = tmp_path / "rapid.txt"
    target.write_text(expected, encoding="utf-8")
    path = f"/api/fs/read?{urlencode({'path': str(target)})}"

    with _running_server(monkeypatch, gate_runtime_paths) as runtime:
        for iteration in range(1, 11):
            for phase in ("open", "reopen"):
                status, _headers, body = _request(runtime.port, path)
                payload = json.loads(body)
                assert status == HTTPStatus.OK, f"cycle {iteration} {phase} returned {status}: {payload}"
                assert payload["state"] == "ready", payload
                assert payload["data"]["content"] == expected, f"cycle {iteration} {phase} returned the wrong content"


@pytest.mark.browser
def test_a6_reopen_replaces_cached_not_found_after_file_is_created(gate_browser_runtime, tmp_path):
    """A6 amend: creating a file after a typed not-found must make an explicit reopen render its bytes."""
    target = tmp_path / "a6-created-after-miss.txt"
    expected = "created after the typed not-found was cached\n"
    missing = gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            await openFileInEditor(path, {name: path.split('/').at(-1)}, {userInitiated: true, viewMode: 'edit'});
            await window.__yolomuxTestWaitFor(() => {
              const state = fileState.get(path);
              return state?.kind === 'error';
            }, {timeoutMs: 10000, description: `typed direct missing-file result for ${path}`});
            const state = fileState.get(path);
            done({
              kind: state?.kind || '',
              missing: state?.externalMissing === true,
              status: state?.error?.status || 0,
              pendingOperations: apiOperationState.pending.size,
            });
          } catch (error) {
            done({error: String(error?.stack || error)});
          }
        })();
        """,
        str(target),
    )
    assert not missing.get("error"), missing
    assert missing["kind"] == "error" and missing["missing"] is True, missing
    assert missing["pendingOperations"] == 0, missing

    target.write_text(expected, encoding="utf-8")
    reopened = gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const expected = arguments[1];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            await openFileInEditor(path, {name: path.split('/').at(-1)}, {userInitiated: true, viewMode: 'edit'});
            await window.__yolomuxTestWaitFor(() => {
              const state = fileState.get(path);
              const panel = fileEditorPanelsForPath(path)[0];
              return state?.kind === 'text'
                && state.externalMissing !== true
                && panel?._cmView?.state?.doc?.toString?.() === expected;
            }, {timeoutMs: 10000, description: `created file content after reopen for ${path}`});
            const state = fileState.get(path);
            const panel = fileEditorPanelsForPath(path)[0];
            done({
              kind: state?.kind || '',
              missing: state?.externalMissing === true,
              text: panel?._cmView?.state?.doc?.toString?.() || '',
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            const state = fileState.get(path);
            const panel = fileEditorPanelsForPath(path)[0];
            done({
              error: String(error?.stack || error),
              kind: state?.kind || '',
              missing: state?.externalMissing === true,
              original: state?.original || '',
              content: state?.content || '',
              text: panel?._cmView?.state?.doc?.toString?.() || '',
              loading: state?.loading === true,
            });
          }
        })();
        """,
        str(target),
        expected,
    )
    assert not reopened.get("error"), reopened
    assert reopened["kind"] == "text" and reopened["missing"] is False, reopened
    assert reopened["text"] == expected, reopened
    expected_browser_error = assert_only_expected_browser_http_error(
        gate_browser_runtime.browser,
        path="/api/fs/read",
        status=HTTPStatus.NOT_FOUND,
        query={"path": str(target)},
    )
    assert str(expected_browser_error.get("source") or "") == "network", expected_browser_error
    assert reopened["errors"] == [] and reopened["rejections"] == [], reopened
    start = validate_server_log_ring_payload(gate_browser_runtime.server._fixture_server_log_boundary)
    current = validate_server_log_ring_payload(SERVER_LOGS.payload())
    transition = validate_server_log_ring_transition(start, current)
    assert transition["droppedCount"] == 0, transition
    assert [
        (str(entry.get("level") or "").lower(), str(entry.get("source") or ""), str(entry.get("category") or ""), json.loads(str(entry["message"]))["code"])
        for entry in transition["newLogs"]
    ] == [(EXPECTED_OUTCOME_LOG_LEVEL, "api-response", "api", "path_not_found")], transition
    gate_browser_runtime.server._fixture_server_log_boundary = current
    gate_browser_runtime.browser._yolomux_server_log_boundary = current
    assert_browser_journey_error_free(gate_browser_runtime.browser)


@pytest.mark.no_browser
def test_a7_missing_file_is_typed_404_not_transport_failure(monkeypatch, tmp_path, gate_runtime_paths):
    """A7: GET /api/fs/read for a missing file must return 404 with the machine-readable common.pathNotFound reason, never 503 or a transport-failure reason rendered as File not found."""
    target = tmp_path / "missing.txt"
    path = f"/api/fs/read?{urlencode({'path': str(target)})}"

    with _running_server(monkeypatch, gate_runtime_paths) as runtime:
        status, _headers, body = _request(runtime.port, path)

        payload = json.loads(body)
        assert status == HTTPStatus.NOT_FOUND, payload
        assert payload["state"] == "failed"
        assert payload["error"]["code"] == "path_not_found"
        assert payload["error"]["message"]["key"] == "common.pathNotFound"
        assert payload["error"]["message"]["params"] == {"path": str(target)}
        assert payload["error"]["details"]["path"] == str(target)
        # The descriptor-authorized base read is intentionally direct. A missing file has the
        # same typed API outcome without manufacturing a jobd operation/receipt.
        assert "operation_id" not in payload["error"]["details"]
        assert "transport" not in payload["error"]["message"]["key"].lower()

        start = validate_server_log_ring_payload(runtime.server._fixture_server_log_boundary)
        current = validate_server_log_ring_payload(SERVER_LOGS.payload())
        transition = validate_server_log_ring_transition(start, current)
        assert transition["droppedCount"] == 0
        # A 404 for the path this caller asked about is that caller's own outcome -- nothing an
        # operator can act on -- and {"warning", "error"} is the release-blocking set the live
        # browser soak collects. `failure_record_level` owns that rule; this asserts what the
        # operator log actually shows, so an expected outcome that becomes an error again is red.
        blocking = [
            entry for entry in transition["newLogs"]
            if str(entry.get("level") or "").lower() in {"warning", "error"}
        ]
        assert blocking == [], blocking
        # The direct descriptor read has no jobd receipt to replay. Its one API response records
        # the expected caller-owned outcome at info without manufacturing a second operation row.
        outcomes = [
            entry for entry in transition["newLogs"]
            if (entry["source"], entry["category"]) in {("jobd-operation", "operation"), ("api-response", "api")}
        ]
        assert [
            (str(entry["level"]).lower(), entry["source"], entry["category"]) for entry in outcomes
        ] == [(EXPECTED_OUTCOME_LOG_LEVEL, "api-response", "api")], outcomes
        messages = [json.loads(entry["message"]) for entry in outcomes]
        assert [message["code"] for message in messages] == ["path_not_found"]
        assert [message["request"]["id"] for message in messages] == [payload["request"]["id"]]
        assert messages[0]["operation"] is None
        runtime.server._fixture_server_log_boundary = current


@pytest.mark.no_browser
def test_a7_fault_batch_above_the_server_bound_stays_an_operator_error(monkeypatch, gate_runtime_paths):
    """A7 fault side: a /api/fs/batch body above filesystem.MAX_BATCH_REQUESTS must be refused 400 invalid_request AND recorded at error.

    This is the other half of A7. A `path_not_found` is a caller's outcome and is recorded at info;
    an `invalid_request` is the contract being broken by whoever sent it -- the defect class fixed
    in 71ab4d6bc -- and must stay in the release-blocking set. It is also the exact refusal the
    browser used to walk into: the Finder flush drained its whole queue into one body, so any
    Finder operation touching more than this bound failed outright. Asserting the refusal here
    keeps the server-side bound and the browser-side chunk size describing the same number.
    """
    over_limit = filesystem.MAX_BATCH_REQUESTS + 1
    body = json.dumps({
        "requests": [
            {"id": index + 1, "type": "list", "path": f"/tmp/gate-a7-fault/{index}"}
            for index in range(over_limit)
        ],
        "client_scope": "browser",
    }).encode("utf-8")

    with _running_server(monkeypatch, gate_runtime_paths) as runtime:
        connection = HTTPConnection("127.0.0.1", runtime.port, timeout=5)
        try:
            connection.request(
                "POST",
                "/api/fs/batch",
                body=body,
                headers={**_auth_header(), "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            status, raw = response.status, response.read()
        finally:
            connection.close()

        payload = json.loads(raw)
        assert status == HTTPStatus.BAD_REQUEST, payload
        assert payload["state"] == "failed"
        assert payload["error"]["code"] == "invalid_request"
        assert payload["error"]["details"]["maximum"] == filesystem.MAX_BATCH_REQUESTS
        assert payload["error"]["details"]["requests"] == over_limit

        start = validate_server_log_ring_payload(runtime.server._fixture_server_log_boundary)
        current = validate_server_log_ring_payload(SERVER_LOGS.payload())
        transition = validate_server_log_ring_transition(start, current)
        assert transition["droppedCount"] == 0
        faults = [
            entry for entry in transition["newLogs"]
            if str(entry.get("level") or "").lower() in {"warning", "error"}
        ]
        assert [
            (str(entry["level"]).lower(), entry["source"], entry["category"]) for entry in faults
        ] == [(FAULT_LOG_LEVEL, "api-response", "api")], faults
        assert json.loads(faults[0]["message"])["code"] == "invalid_request"
        runtime.server._fixture_server_log_boundary = current


@pytest.mark.browser
def test_a8_editor_recovers_from_ten_atomic_inode_swaps_and_reports_real_delete(gate_browser_runtime, tmp_path):
    """A8 editor: ten varying sub-second os.replace inode swaps must converge without reload/reopen, clear both body and tab missing state, and a real deletion must still render the missing state."""
    target = tmp_path / "a8-editor.txt"
    original = "A8 editor original\n"
    target.write_text(original, encoding="utf-8")
    _open_editor(gate_browser_runtime, target, original)
    _wait_for_file_event_stream(gate_browser_runtime, target)
    gate_browser_runtime.browser.execute_script("document.activeElement?.blur?.(); document.body.focus();")

    def replace_once(iteration):
        _a8_wait_varying_subsecond_interval(gate_browser_runtime, iteration)
        replacement = f"A8 editor inode replacement {iteration} {'x' * iteration}\n"
        elapsed = _a8_replace_inode(target, replacement, iteration)
        _a8_latch_transient_inode_miss(gate_browser_runtime, target)
        _publish_file_change(gate_browser_runtime, target)
        metrics = _a8_recovered_snapshot(gate_browser_runtime, target, replacement)
        assert not metrics.get("error"), metrics
        assert metrics["text"] == replacement and metrics["missing"] is False, metrics
        assert metrics["tabMissing"] is False and metrics["missingBadge"] is False, metrics
        assert metrics["errors"] == [] and metrics["rejections"] == [], metrics
        return elapsed

    elapsed_replaces = repeat(10, replace_once)
    assert len(set(A8_FRAME_COUNTS)) > 1
    assert len(elapsed_replaces) == 10 and max(elapsed_replaces) < 1.0

    target.unlink()
    _publish_file_change(gate_browser_runtime, target)
    missing = _a8_missing_snapshot(gate_browser_runtime, target)
    assert not missing.get("error"), missing
    assert missing["missing"] is True and missing["tabMissing"] is True and missing["missingBadge"] is True, missing
    assert missing["status"], missing
    # The intentionally deleted file must yield exactly one observed 404: consume that exact
    # browser-network receipt here so fixture retirement still rejects every other failure.
    assert_only_expected_browser_http_error(
        gate_browser_runtime.browser,
        path="/api/fs/read",
        status=HTTPStatus.NOT_FOUND,
        query={"path": str(target)},
    )
