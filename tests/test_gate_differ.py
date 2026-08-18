from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
from http import HTTPStatus
from urllib.parse import parse_qs

import pytest
from selenium.webdriver.support.ui import WebDriverWait

from tools import static_build
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401
from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_console import consume_only_expected_js_debug_api_error
from tests.browser_helpers.browser_console import consume_only_expected_js_debug_api_errors
from tests.gate_harness import repeat
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import retire_expected_fixture_typed_api_failure
from tests.gate_harness import wait_for_browser_boot
from tests.mock_git_repo import create_mock_git_repository
from tests.helpers.gate_editor import A8_FRAME_COUNTS
from tests.helpers.gate_editor import _a8_missing_snapshot, _a8_latch_transient_inode_miss, _a8_recovered_snapshot, _a8_replace_inode, _a8_wait_varying_subsecond_interval
from tests.helpers.gate_editor import _wait_for_active_watchd_descriptor, _wait_for_watchd_path_change, _dirty_conflict_snapshot, _open_editor, _publish_file_change, _type_dirty_text, _wait_for_file_event_stream
from tests.helpers.gate_editor import gate_browser_runtime
from tests.terminal_state_guard import assert_terminal_transition
from yolomux_lib import filesystem
from yolomux_lib import server as server_module
from yolomux_lib import web as web_module


pytestmark = pytest.mark.socket

DIFFER_GLOBALS = {
    "applySessionFilesPayloadFromPush": "function",
    "clientEventDemandDescriptor": "function",
    "clientSessionFilesWatchRequests": "function",
    "fetchSessionFiles": "function",
    "openFileSurface": "function",
    "renderFileExplorerChangesPanels": "function",
}


def _control_filesystem_operation_product(
    gate_browser_runtime,
    monkeypatch,
    target,
    operation,
    result,
    started,
    release=None,
    *,
    ready_after_seconds=None,
):
    client = gate_browser_runtime.runtime.app.job_client
    original_produce = client.produce
    original_product = client.product
    product_keys = set()
    product_started_at = {}
    job_id = f"job-gate-hung-{operation}"
    body = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def produce(task, payload, **kwargs):
        if task != "filesystem_operation" or payload.get("op") != operation or Path(payload.get("path") or "") != target:
            return original_produce(task, payload, **kwargs)
        started.set()
        product_key = str(kwargs["coalesce_key"])
        product_keys.add(product_key)
        product_started_at[product_key] = time.monotonic()
        return {
            "ok": True,
            "state": "queued",
            "job": {"job_id": job_id, "status": "running", "generation": kwargs["generation"]},
            "product": {"coalesce_key": product_key, "generation": 0},
        }, b""

    def product(key):
        if key not in product_keys:
            return original_product(key)
        release_pending = release is not None and not release.is_set()
        delay_pending = (
            ready_after_seconds is not None
            and time.monotonic() - product_started_at[key] < ready_after_seconds
        )
        if release_pending or delay_pending:
            return {"ok": True, "state": "pending", "generation": 0, "inflight": True}, b""
        return {
            "ok": True,
            "state": "ready",
            "generation": 1,
            "inflight": False,
            "product": {
                "format": "json",
                "content_type": "application/json; charset=utf-8",
                "length": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "disposition": "inline",
                "filename": "",
            },
        }, body

    monkeypatch.setattr(client, "produce", produce)
    monkeypatch.setattr(client, "product", product)


def _settle_controlled_filesystem_operation(gate_browser_runtime, release):
    """Release the fake product and retain its patch until the accepted completion is terminal."""
    release.set()
    gate_browser_runtime.runtime.app.wait_for_jobd_operations_terminal(3)


DIFFER_TERMINAL_STATE_TIMEOUT_MS = 12_000
DIFFER_STALL_CONTRACT_TIMEOUT_MS = 8_000
API_FETCH_DEADLINE_MS = 15_000
API_FETCH_CONTRACT_TIMEOUT_MS = 18_000
SLOW_BACKEND_DELAY_SECONDS = 0.25
REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def differ_source_bundle(monkeypatch, tmp_path):
    """Serve this module a fixture-owned bundle built from the current source partials."""
    asset_dir = tmp_path / "differ-static"
    asset_dir.mkdir()
    for name in ("brand.css", "codemirror.js", "yolomux.css"):
        shutil.copy2(REPO_ROOT / "static" / name, asset_dir / name)
    for name in ("fonts", "locales", "vendor"):
        shutil.copytree(REPO_ROOT / "static" / name, asset_dir / name)
    (asset_dir / "yolomux.js").write_text(static_build.build_asset("yolomux.js"), encoding="utf-8")
    monkeypatch.setattr(web_module, "STATIC_DIR", asset_dir)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _make_changed_repo(tmp_path):
    repo = tmp_path / "differ-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Gate Fixture")
    _git(repo, "config", "user.email", "gate@example.invalid")
    target = repo / "changed.txt"
    target.write_text("committed content\n", encoding="utf-8")
    _git(repo, "add", "changed.txt")
    _git(repo, "commit", "-q", "-m", "fixture baseline")
    target.write_text("second committed content\n", encoding="utf-8")
    _git(repo, "add", "changed.txt")
    _git(repo, "commit", "-q", "-m", "fixture history")
    target.write_text("working tree content\n", encoding="utf-8")
    return repo, target


def _differ_payload(repo, target, session):
    stat = target.stat() if target.exists() else None
    return {
        "session": session,
        "loaded": True,
        "errors": [],
        "refs_by_repo": {str(repo): {"from_ref": "HEAD", "to_ref": "current"}},
        "repos": [{"repo": str(repo), "count": 1}],
        "files": [{
            "session": session,
            "agent": "codex",
            "status": "M" if stat is not None else "D",
            "repo": str(repo),
            "path": target.name,
            "abs_path": str(target),
            "mtime": stat.st_mtime if stat is not None else 0,
            "size": stat.st_size if stat is not None else 0,
            "added": 1 if stat is not None else 0,
            "removed": 1,
        }],
    }


def _configure_differ_payload(gate_browser_runtime, repo, target):
    session = gate_browser_runtime.session

    def payload(_session=None, _hours=24.0, **_kwargs):
        return _differ_payload(repo, target, session), HTTPStatus.OK

    gate_browser_runtime.runtime.app.session_files_payload = payload


def _open_differ(gate_browser_runtime, target):
    browser = gate_browser_runtime.browser
    wait_for_browser_boot(browser, globals_required=DIFFER_GLOBALS, dom_anchors=("#grid",), timeout=12)
    metrics = browser.execute_async_script(
        """
        const session = arguments[0];
        const path = arguments[1];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            await openFileSurface(differItemId);
            await fetchSessionFiles({destination: 'finder', session, force: true, silent: true});
            renderFileExplorerChangesPanels({force: true, view: 'differ'});
            const row = await window.__yolomuxTestWaitFor(
              () => document.querySelector(`#panel-__differ__ [data-open-change-file="${CSS.escape(path)}"]`),
              {timeoutMs: 10000, description: `Differ row for ${path}`},
            );
            const panel = document.querySelector('#panel-__differ__');
            done({
              rowConnected: row?.isConnected === true,
              panelActive: panel?.classList.contains('active-pane') === true,
              mode: panel?.dataset?.fileExplorerMode || '',
              size: row?.dataset?.changeSize || '',
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error?.stack || error), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          }
        })();
        """,
        gate_browser_runtime.session,
        str(target),
    )
    assert not metrics.get("error"), metrics
    assert metrics["rowConnected"] is True and metrics["panelActive"] is True and metrics["mode"] == "diff", metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics
    return metrics


def _open_file_from_differ(gate_browser_runtime, target):
    metrics = gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const row = document.querySelector(`#panel-__differ__ [data-open-change-file="${CSS.escape(path)}"]`);
            if (!row) throw new Error(`listed Differ row missing for ${path}`);
            row.click();
            await window.__yolomuxTestWaitFor(() => {
              const state = fileState.get(path);
              const panel = fileEditorPanelsForPath(path)[0];
              const item = panel?.dataset?.layoutItem || fileEditorItemFor(path);
              return state?.diffLoaded === true
                && editorViewModeFor(path, item) === 'diff'
                && Boolean(panel?._cmView?.state?.doc?.toString?.());
            }, {timeoutMs: 10000, description: `A8 Differ file open for ${path}`});
            const panel = fileEditorPanelsForPath(path)[0];
            const item = panel?.dataset?.layoutItem || fileEditorItemFor(path);
            done({
              text: panel?._cmView?.state?.doc?.toString?.() || '',
              viewMode: editorViewModeFor(path, item),
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error?.stack || error)});
          }
        })();
        """,
        str(target),
    )
    assert not metrics.get("error"), metrics
    assert metrics["viewMode"] == "diff" and metrics["text"], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics
    return metrics


def _a8_deleted_differ_row_snapshot(gate_browser_runtime, target):
    return gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        window.__yolomuxTestWaitFor(() => {
          const row = document.querySelector(`#panel-__differ__ [data-open-change-file="${CSS.escape(path)}"]`);
          return row?.querySelector('.file-tree-git-status')?.textContent?.trim() === 'D'
            && row.querySelector('.changes-diff-remove')?.textContent?.trim() === '-1';
        }, {timeoutMs: 10000, description: `A8 deleted Differ row for ${path}`}).then(() => {
          const row = document.querySelector(`#panel-__differ__ [data-open-change-file="${CSS.escape(path)}"]`);
          done({
            status: row?.querySelector('.file-tree-git-status')?.textContent?.trim() || '',
            added: row?.querySelector('.changes-diff-add, .changes-diff-add-neutral')?.textContent?.trim() || '',
            removed: row?.querySelector('.changes-diff-remove')?.textContent?.trim() || '',
          });
        }, error => done({error: String(error?.stack || error)}));
        """,
        str(target),
    )


@pytest.mark.browser
def test_differ_only_open_reaches_content_or_typed_error_within_bound(gate_browser_runtime, tmp_path):
    """A Differ-only open must leave its loading state for content or a typed error within twelve seconds."""
    repo, target = _make_changed_repo(tmp_path)
    requests = []

    def payload(_session=None, _hours=24.0, **_kwargs):
        requests.append(_session)
        return _differ_payload(repo, target, gate_browser_runtime.session), HTTPStatus.OK

    gate_browser_runtime.runtime.app.session_files_payload = payload
    wait_for_browser_boot(
        gate_browser_runtime.browser,
        globals_required=DIFFER_GLOBALS,
        dom_anchors=("#grid",),
        timeout=12,
    )
    metrics = gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const timeoutMs = arguments[1];
        const pushPayload = arguments[2];
        const done = arguments[arguments.length - 1];
        (async () => {
          const nativeFetch = window.fetch.bind(window);
          let sessionFilesFetches = 0;
          try {
            await window.__yolomuxTestWaitFor(
              () => clientPushCanSupplyData() === true && clientEventTransportState.connected === true,
              {timeoutMs, description: 'connected client push before Differ-only open'},
            );
            const preOpenDemand = clientEventDemandDescriptor();
            window.fetch = (input, options = {}) => {
              const url = new URL(String(input), location.href);
              if (url.pathname === '/api/session-files') {
                sessionFilesFetches += 1;
                return new Promise(() => {});
              }
              return nativeFetch(input, options);
            };
            const started = performance.now();
            await openFileSurface(differItemId);
            const panel = await window.__yolomuxTestWaitFor(
              () => document.querySelector('#panel-__differ__'),
              {timeoutMs, description: 'Differ panel mount'},
            );
            let terminal = null;
            let waitError = '';
            let pushApplied = false;
            try {
              terminal = await window.__yolomuxTestWaitFor(() => {
                const demand = clientEventDemandDescriptor();
                if (!pushApplied && demand.channels.includes('files')) {
                  const request = clientSessionFilesWatchRequests()[0] || {
                    session: pushPayload.session,
                    hours: 24,
                    from_ref: 'HEAD',
                    to_ref: 'current',
                    repo_refs: {},
                  };
                  pushApplied = applySessionFilesPayloadFromPush(pushPayload, request) === true;
                }
                const row = panel.querySelector(`[data-open-change-file="${CSS.escape(path)}"]`);
                const error = panel.querySelector('.changes-error');
                if (row?.isConnected) return {kind: 'content', text: row.textContent || ''};
                if (error?.textContent?.trim()) return {kind: 'error', text: error.textContent.trim()};
                return null;
              }, {timeoutMs, description: `Differ content or typed error for ${path}`});
            } catch (error) {
              waitError = String(error?.stack || error);
            }
            const loading = panel.querySelector('.changes-loading');
            done({
              elapsedMs: performance.now() - started,
              terminal,
              waitError,
              sessionFilesFetches,
              pushApplied,
              loading: loading?.textContent?.trim() || '',
              ariaBusy: loading?.getAttribute('aria-busy') || '',
              payload: {
                loaded: fileExplorerSessionFilesState.payload?.loaded === true,
                session: fileExplorerSessionFilesState.payload?.session || '',
                files: fileExplorerSessionFilesState.payload?.files?.length || 0,
                errors: fileExplorerSessionFilesState.payload?.errors || [],
              },
              preOpenDemand,
              demand: clientEventDemandDescriptor(),
              panelActive: panel.classList.contains('active-pane'),
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error?.stack || error)});
          } finally {
            window.fetch = nativeFetch;
          }
        })();
        """,
        str(target),
        DIFFER_TERMINAL_STATE_TIMEOUT_MS,
        _differ_payload(repo, target, gate_browser_runtime.session),
    )
    observed = metrics.get("elapsedMs", -1) / 1000
    assert not metrics.get("error"), f"Differ terminal-state probe failed after {observed:.3f}s: {metrics}"
    assert (metrics.get("terminal") or {}).get("kind") in {"content", "error"}, (
        f"Differ remained outside content/error terminal states for {observed:.3f}s; "
        f"backend requests={requests!r}; snapshot={metrics!r}"
    )
    assert metrics["panelActive"] is True and metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_mock_git_differ_stall_ends_in_visible_deadline_error(gate_browser_runtime, tmp_path):
    """A hung real-repository Differ request must paint a typed error and remove its spinner within eight seconds."""
    repo = create_mock_git_repository(tmp_path / "test-differ-stall")
    status_lines = repo.status_lines()
    backing_started = threading.Event()
    release_backing = threading.Event()
    session = gate_browser_runtime.session
    payload = {
        "session": session,
        "loaded": True,
        "errors": [],
        "refs_by_repo": {str(repo.root): {"from_ref": "HEAD", "to_ref": "current"}},
        "repos": [{"repo": str(repo.root), "count": 5}],
        "files": repo.differ_files(session),
    }

    def hung_payload(_session=None, _hours=24.0, **_kwargs):
        backing_started.set()
        assert release_backing.wait(30), "fixture-owned hung Differ backing call was not released"
        return payload, HTTPStatus.OK

    gate_browser_runtime.runtime.app.session_files_payload = hung_payload
    wait_for_browser_boot(
        gate_browser_runtime.browser,
        globals_required=DIFFER_GLOBALS,
        dom_anchors=("#grid",),
        timeout=12,
    )
    try:
        metrics = gate_browser_runtime.browser.execute_async_script(
            """
            const timeoutMs = arguments[0];
            const done = arguments[arguments.length - 1];
            (async () => {
              try {
                const started = performance.now();
                await openFileSurface(differItemId);
                const panel = await window.__yolomuxTestWaitFor(
                  () => document.querySelector('#panel-__differ__'),
                  {timeoutMs, description: 'mock-git Differ panel mount'},
                );
                let terminal = null;
                let waitError = '';
                try {
                  terminal = await window.__yolomuxTestWaitFor(() => {
                    const error = panel.querySelector('.changes-error');
                    if (error?.textContent?.trim() && getComputedStyle(error).display !== 'none') {
                      return {kind: 'error', text: error.textContent.trim()};
                    }
                    const row = panel.querySelector('[data-open-change-file]');
                    return row?.isConnected ? {kind: 'content', text: row.textContent || ''} : null;
                  }, {timeoutMs, description: 'visible Differ content or deadline error'});
                } catch (error) {
                  waitError = String(error?.stack || error);
                }
                const loading = panel.querySelector('.changes-loading');
                done({
                  elapsedMs: performance.now() - started,
                  terminal,
                  waitError,
                  loading: loading?.textContent?.trim() || '',
                  ariaBusy: loading?.getAttribute('aria-busy') || '',
                  payloadLoaded: fileExplorerSessionFilesState.payload?.loaded === true,
                  payloadErrors: fileExplorerSessionFilesState.payload?.errors || [],
                  panelActive: panel.classList.contains('active-pane'),
                  errors: jsDebugFailureEvents('error'),
                  rejections: jsDebugFailureEvents('rejection'),
                });
              } catch (error) {
                done({error: String(error?.stack || error)});
              }
            })();
            """,
            DIFFER_STALL_CONTRACT_TIMEOUT_MS,
        )
    finally:
        release_backing.set()

    observed = metrics.get("elapsedMs", -1) / 1000
    assert backing_started.is_set(), "the mock Git Differ request never reached its deliberately hung backing call"
    assert len(status_lines) == 5, status_lines
    assert not metrics.get("error"), f"Differ stall probe failed after {observed:.3f}s: {metrics}"
    assert 0 <= metrics["elapsedMs"] <= DIFFER_STALL_CONTRACT_TIMEOUT_MS + 500, metrics
    assert (metrics.get("terminal") or {}).get("kind") == "error", (
        f"hung Differ request had no visible terminal error after {observed:.3f}s: {metrics}"
    )
    assert "deadline_expired" in metrics["terminal"]["text"], metrics
    assert metrics["loading"] == "" and metrics["ariaBusy"] == "", metrics
    assert metrics["payloadLoaded"] is True and "deadline_expired" in str(metrics["payloadErrors"]), metrics
    expected_api_errors = consume_only_expected_js_debug_api_errors(
        gate_browser_runtime.browser,
        ({
            "path": "/api/session-files",
            "method": "GET",
            "query": {
                "from": "HEAD",
                "to": "current",
                "session": session,
                "hours": "24",
            },
            "error": "deadline_expired: request exceeded its 5s deadline",
        },),
    )
    assert metrics["panelActive"] is True, metrics
    assert metrics["errors"] == list(expected_api_errors) and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_mock_git_differ_pending_producer_without_publish_ends_in_visible_deadline_error(
    gate_browser_runtime, tmp_path
):
    """A completed HTTP request cannot leave Differ waiting forever for a producer publish."""
    repo = create_mock_git_repository(tmp_path / "test-differ-pending-producer")
    session = gate_browser_runtime.session
    payload = {
        "session": session,
        "loaded": True,
        "errors": [],
        "refreshing_elsewhere": True,
        "refs_by_repo": {str(repo.root): {"from_ref": "HEAD", "to_ref": "current"}},
        "repos": [{"repo": str(repo.root), "count": 0, "added": 0, "removed": 0, "behind": 0, "ahead": 0}],
        "files": [],
    }
    requests = []

    def pending_payload(_session=None, _hours=24.0, **_kwargs):
        requests.append(_session)
        return payload, HTTPStatus.OK

    gate_browser_runtime.runtime.app.session_files_payload = pending_payload
    wait_for_browser_boot(
        gate_browser_runtime.browser,
        globals_required=DIFFER_GLOBALS,
        dom_anchors=("#grid",),
        timeout=12,
    )
    metrics = gate_browser_runtime.browser.execute_async_script(
        """
        const timeoutMs = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const started = performance.now();
            await openFileSurface(differItemId);
            const panel = await window.__yolomuxTestWaitFor(
              () => document.querySelector('#panel-__differ__'),
              {timeoutMs, description: 'pending-producer Differ panel mount'},
            );
            let terminal = null;
            let waitError = '';
            try {
              terminal = await window.__yolomuxTestWaitFor(() => {
                const error = panel.querySelector('.changes-error');
                if (error?.textContent?.trim() && getComputedStyle(error).display !== 'none') {
                  return {kind: 'error', text: error.textContent.trim()};
                }
                const row = panel.querySelector('[data-open-change-file]');
                return row?.isConnected ? {kind: 'content', text: row.textContent || ''} : null;
              }, {timeoutMs, description: 'pending producer visible terminal state'});
            } catch (error) {
              waitError = String(error?.stack || error);
            }
            const loading = panel.querySelector('.changes-loading');
            done({
              elapsedMs: performance.now() - started,
              terminal,
              waitError,
              loading: loading?.textContent?.trim() || '',
              ariaBusy: loading?.getAttribute('aria-busy') || '',
              stateLoading: fileExplorerSessionFilesState.loading,
              payloadRefreshing: fileExplorerSessionFilesState.payload?.refreshing_elsewhere === true,
              payloadErrors: fileExplorerSessionFilesState.payload?.errors || [],
              panelText: panel.innerText || '',
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error?.stack || error)});
          }
        })();
        """,
        DIFFER_STALL_CONTRACT_TIMEOUT_MS,
    )

    observed = metrics.get("elapsedMs", -1) / 1000
    terminal = metrics.get("terminal") or {}
    assert_terminal_transition(
        contract_id="differ-refreshing-elsewhere",
        pending_observed=payload["refreshing_elsewhere"] is True,
        terminal_observed=(
            terminal.get("kind") == "error"
            and metrics.get("loading") == ""
            and metrics.get("ariaBusy") == ""
            and metrics.get("stateLoading") is False
            and metrics.get("payloadRefreshing") is False
        ),
        evidence=metrics,
    )
    assert requests, f"the pending-producer request was never issued: {metrics}"
    assert not metrics.get("error"), f"pending-producer probe failed after {observed:.3f}s: {metrics}"
    assert (metrics.get("terminal") or {}).get("kind") == "error", (
        f"completed HTTP request left Differ waiting for an absent producer publish after {observed:.3f}s: {metrics}"
    )
    assert "deadline_expired" in metrics["terminal"]["text"], metrics
    assert metrics["loading"] == "" and metrics["ariaBusy"] == "", metrics
    assert metrics["stateLoading"] is False and metrics["payloadRefreshing"] is False, metrics
    assert "deadline_expired" in str(metrics["payloadErrors"]), metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_mock_git_differ_queued_producer_completion_settles_every_visible_surface(
    gate_browser_runtime, tmp_path
):
    """A 202 receipt remains pending until its persisted terminal data settles every surface."""
    repo = create_mock_git_repository(tmp_path / "test-differ-queued-producer")
    session = gate_browser_runtime.session
    operation_id = "op-fixture-session-files"
    request_id = "r-fixture-session-files"
    operation_epoch = "fixture-epoch"
    queued = {
        "state": "queued",
        "request": {"id": request_id},
        "operation": {
            "id": operation_id,
            "kind": "session_files",
            "context": {"session": session, "from_ref": "HEAD", "to_ref": "current"},
            "deadline_at": "2026-08-03T08:00:00Z",
            "status_url": f"/api/operations/{operation_id}",
            "events_url": f"/api/client-events?operation_id={operation_id}",
            "cursor": {"epoch": operation_epoch, "seq": 0},
            "progress": {"phase": "waiting_for_product", "producer": "jobd", "producer_state": "queued"},
        },
    }
    ready = {
        "session": session,
        "loaded": True,
        "errors": [],
        "refs_by_repo": {str(repo.root): {"from_ref": "HEAD", "to_ref": "current"}},
        "repos": [{"repo": str(repo.root), "count": 0, "added": 0, "removed": 0, "behind": 0, "ahead": 0}],
        "files": [],
    }
    application_error = {
        "code": "service_unavailable",
        "message": {"key": "common.requestFailed", "params": {}, "fallback": "service socket is absent"},
        "origin": "local_services.jobd",
        "retryable": False,
        "details": {"service": "jobd", "reason": "absent"},
        "stack": [
            {"component": "server.http", "operation": "GET /api/session-files", "code": "dependency_failed"},
            {
                "component": "local_services.jobd",
                "operation": "jobd.result",
                "code": "service_unavailable",
                "exception": {"type": "FileNotFoundError", "message": "service socket is absent"},
                "frames": [{"file": "yolomux_lib/local_services/rpc.py", "line": 272, "function": "request"}],
            },
        ],
    }
    requests = []

    def accepted_receipt(_session=None, _hours=24.0, **_kwargs):
        requests.append(_session)
        return queued, HTTPStatus.ACCEPTED

    gate_browser_runtime.runtime.app.session_files_http_payload = accepted_receipt
    wait_for_browser_boot(
        gate_browser_runtime.browser,
        globals_required=DIFFER_GLOBALS,
        dom_anchors=("#grid",),
        timeout=12,
    )
    metrics = gate_browser_runtime.browser.execute_async_script(
        """
        const timeoutMs = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            await openFileSurface(differItemId);
            await window.__yolomuxTestWaitFor(() => (
              fileExplorerSessionFilesState.payload?.refreshing_elsewhere === true
              && fileExplorerSessionFilesState.loading === false
            ) ? true : false, {timeoutMs, description: 'queued session-files acknowledgement'});
            const pending = {
              refreshing: fileExplorerSessionFilesState.payload?.refreshing_elsewhere === true,
              loading: fileExplorerSessionFilesState.loading,
              key: fileExplorerSessionFilesState.payload?.pending_key || '',
              epoch: fileExplorerSessionFilesState.payload?.pending_epoch || '',
              panels: document.querySelectorAll('.file-explorer-changes-panel').length,
            };
            handleClientPushEventNow('operation_terminal', {
              operation: {id: arguments[2], cursor: {epoch: arguments[3], seq: 1}},
              result: {
                state: 'ready',
                request: {id: arguments[4]},
                data: arguments[5],
                quality: {complete: true, stale: false},
                warnings: [],
              },
            });
            await window.__yolomuxTestWaitFor(() => (
              fileExplorerSessionFilesState.payload?.refreshing_elsewhere !== true
              && fileExplorerSessionFilesState.loading === false
            ) ? true : false, {timeoutMs, description: 'queued session-files terminal completion'});
            const surfaces = [...document.querySelectorAll('.file-explorer-changes-panel')].map(panel => ({
              loading: panel.querySelector('.changes-loading')?.textContent?.trim() || '',
              ariaBusy: panel.querySelector('.changes-loading')?.getAttribute('aria-busy') || '',
            }));
            const failureOperationId = `${arguments[2]}-failure`;
            registerApiOperationReceipt(new ApiPendingResponse({
              request: {id: `${arguments[4]}-failure`},
              operation: {
                id: failureOperationId,
                kind: 'session_files',
                context: {session: arguments[1], from_ref: 'HEAD', to_ref: 'current'},
                cursor: {epoch: arguments[3], seq: 0},
              },
            }));
            handleClientPushEventNow('operation_terminal', {
              operation: {id: failureOperationId, cursor: {epoch: arguments[3], seq: 1}},
              result: {
                state: 'failed',
                request: {id: `${arguments[4]}-failure`},
                error: arguments[6],
              },
            });
            done({
              pending,
              terminal: {
                refreshing: fileExplorerSessionFilesState.payload?.refreshing_elsewhere === true,
                loading: fileExplorerSessionFilesState.loading,
                loaded: fileExplorerSessionFilesState.payload?.loaded === true,
                surfaces,
              },
              applicationError: fileExplorerSessionFilesState.payload?.operation_error || null,
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error?.stack || error)});
          }
        })();
        """,
        DIFFER_STALL_CONTRACT_TIMEOUT_MS,
        session,
        operation_id,
        operation_epoch,
        request_id,
        ready,
        application_error,
    )

    terminal = metrics.get("terminal") or {}
    assert_terminal_transition(
        contract_id="differ-queued-producer-completion",
        pending_observed=metrics.get("pending", {}).get("refreshing") is True,
        terminal_observed=(
            terminal.get("refreshing") is False
            and terminal.get("loading") is False
            and terminal.get("loaded") is True
            and all(not surface["loading"] and not surface["ariaBusy"] for surface in terminal.get("surfaces", []))
        ),
        evidence=metrics,
    )
    assert not metrics.get("error"), metrics
    assert metrics["pending"]["key"] == operation_id, metrics
    assert metrics["pending"]["epoch"] == operation_epoch, metrics
    assert requests == [session], metrics
    assert terminal.get("surfaces"), metrics
    assert metrics["applicationError"] == application_error, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_hung_fs_read_paints_visible_typed_deadline_error(gate_browser_runtime, tmp_path, monkeypatch):
    """A real fixture-server read that never answers must become a persistent typed editor error."""
    repo = create_mock_git_repository(tmp_path / "test-hung-fs-read")
    target = repo.modified
    backing_started = threading.Event()
    release_backing = threading.Event()
    _control_filesystem_operation_product(
        gate_browser_runtime,
        monkeypatch,
        target,
        "read",
        filesystem.read_file(str(target)),
        backing_started,
        release_backing,
    )
    wait_for_browser_boot(
        gate_browser_runtime.browser,
        globals_required=DIFFER_GLOBALS,
        dom_anchors=("#grid",),
        timeout=12,
    )
    try:
        metrics = gate_browser_runtime.browser.execute_async_script(
            """
            const path = arguments[0];
            const timeoutMs = arguments[1];
            const done = arguments[arguments.length - 1];
            (async () => {
              const started = performance.now();
              openFileInEditor(path, {name: path.split('/').at(-1)}, {userInitiated: true, viewMode: 'edit'});
              let terminal = null;
              let waitError = '';
              try {
                terminal = await window.__yolomuxTestWaitFor(() => {
                  const state = fileState.get(path);
                  const panel = fileEditorPanelsForPath(path)[0];
                  const status = panel?.querySelector('.file-editor-status-message')?.textContent?.trim() || '';
                  const emptyState = panel?.querySelector('.file-editor-empty-state')?.textContent?.trim() || '';
                  const text = `${status} ${emptyState}`.trim();
                  if (state?.kind === 'error' && text.includes('deadline_expired')) return {kind: state.kind, text};
                  return null;
                }, {timeoutMs, description: `visible typed /api/fs/read deadline for ${path}`});
              } catch (error) {
                waitError = String(error?.stack || error);
              }
              const state = fileState.get(path);
              done({
                elapsedMs: performance.now() - started,
                terminal,
                waitError,
                stateKind: state?.kind || '',
                stateError: state?.error || null,
                errors: jsDebugFailureEvents('error'),
                rejections: jsDebugFailureEvents('rejection'),
              });
            })();
            """,
            str(target),
            API_FETCH_CONTRACT_TIMEOUT_MS,
        )
    finally:
        _settle_controlled_filesystem_operation(gate_browser_runtime, release_backing)

    assert backing_started.is_set(), "the /api/fs/read request never reached its deliberately hung callback"
    assert not metrics.get("error"), metrics
    assert (metrics.get("terminal") or {}).get("kind") == "error", metrics
    assert "deadline_expired" in metrics["terminal"]["text"], metrics
    assert API_FETCH_DEADLINE_MS <= metrics["elapsedMs"] <= API_FETCH_CONTRACT_TIMEOUT_MS + 500, metrics
    expected_api_errors = consume_only_expected_js_debug_api_errors(
        gate_browser_runtime.browser,
        ({
            "path": "/api/fs/read",
            "method": "GET",
            "query": {"path": str(target)},
            "error": "deadline_expired: request exceeded its 15s deadline",
        },),
    )
    assert metrics["errors"] == list(expected_api_errors) and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_stalled_fs_read_body_paints_visible_typed_deadline_error(gate_browser_runtime, tmp_path, monkeypatch):
    """Headers alone are not completion: a stalled real response body must retain the typed deadline."""
    repo = create_mock_git_repository(tmp_path / "test-stalled-fs-read-body")
    target = repo.modified
    body_started = threading.Event()
    release_body = threading.Event()
    original_handle_fs_read = server_module.Handler.handle_fs_read

    def stalled_handle_fs_read(request, parsed):
        raw_path = str((parse_qs(parsed.query).get("path") or [""])[0])
        if Path(raw_path) != target:
            return original_handle_fs_read(request, parsed)
        partial = b'{"content":"partial fixture body'
        request.send_response(HTTPStatus.OK)
        request.send_header("Content-Type", "application/json")
        request.send_header("Content-Length", str(len(partial) + 128))
        request.end_headers()
        request.wfile.write(partial)
        request.wfile.flush()
        body_started.set()
        assert release_body.wait(30), "fixture-owned stalled /api/fs/read response body was not released"

    monkeypatch.setattr(server_module.Handler, "handle_fs_read", stalled_handle_fs_read)
    try:
        metrics = gate_browser_runtime.browser.execute_async_script(
            """
            const path = arguments[0];
            const timeoutMs = arguments[1];
            const done = arguments[arguments.length - 1];
            (async () => {
              const started = performance.now();
              openFileInEditor(path, {name: path.split('/').at(-1)}, {userInitiated: true, viewMode: 'edit'});
              let terminal = null;
              let waitError = '';
              try {
                terminal = await window.__yolomuxTestWaitFor(() => {
                  const state = fileState.get(path);
                  const panel = fileEditorPanelsForPath(path)[0];
                  const status = panel?.querySelector('.file-editor-status-message')?.textContent?.trim() || '';
                  const emptyState = panel?.querySelector('.file-editor-empty-state')?.textContent?.trim() || '';
                  const text = `${status} ${emptyState}`.trim();
                  if (state?.kind === 'error' && text.includes('deadline_expired')) return {kind: state.kind, text};
                  return null;
                }, {timeoutMs, description: `visible typed stalled-body deadline for ${path}`});
              } catch (error) {
                waitError = String(error?.stack || error);
              }
              done({
                elapsedMs: performance.now() - started,
                terminal,
                waitError,
                errors: jsDebugFailureEvents('error'),
                rejections: jsDebugFailureEvents('rejection'),
              });
            })();
            """,
            str(target),
            API_FETCH_CONTRACT_TIMEOUT_MS,
        )
    finally:
        release_body.set()

    assert body_started.is_set(), "the /api/fs/read response never reached its deliberately stalled body"
    assert not metrics.get("error"), metrics
    assert (metrics.get("terminal") or {}).get("kind") == "error", metrics
    assert "deadline_expired" in metrics["terminal"]["text"], metrics
    assert API_FETCH_DEADLINE_MS <= metrics["elapsedMs"] <= API_FETCH_CONTRACT_TIMEOUT_MS + 500, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_hung_fs_diff_paints_visible_typed_deadline_error(gate_browser_runtime, tmp_path, monkeypatch):
    """A real fixture-server diff that never answers must leave the editor in a visible typed state."""
    repo = create_mock_git_repository(tmp_path / "test-hung-fs-diff")
    target = repo.modified
    _open_editor(gate_browser_runtime, target, target.read_text(encoding="utf-8"))
    backing_started = threading.Event()
    release_backing = threading.Event()
    _control_filesystem_operation_product(
        gate_browser_runtime,
        monkeypatch,
        target,
        "diff",
        filesystem.diff_file(str(target)),
        backing_started,
        release_backing,
    )
    try:
        metrics = gate_browser_runtime.browser.execute_async_script(
            """
            const path = arguments[0];
            const timeoutMs = arguments[1];
            const done = arguments[arguments.length - 1];
            (async () => {
              const started = performance.now();
              const item = fileEditorItemFor(path);
              setFileEditorViewMode(path, 'diff', item);
              refreshOpenFileDiff(path, {silent: false});
              let terminal = null;
              let waitError = '';
              try {
                terminal = await window.__yolomuxTestWaitFor(() => {
                  const state = fileState.get(path);
                  const panel = fileEditorPanelsForPath(path)[0];
                  const status = panel?.querySelector('.file-editor-status-message')?.textContent?.trim() || '';
                  if (state?.diffUnavailable === true && status.includes('deadline_expired')) {
                    return {unavailable: true, diffError: state.diffError || '', text: status};
                  }
                  return null;
                }, {timeoutMs, description: `visible typed /api/fs/diff deadline for ${path}`});
              } catch (error) {
                waitError = String(error?.stack || error);
              }
              done({
                elapsedMs: performance.now() - started,
                terminal,
                waitError,
                errors: jsDebugFailureEvents('error'),
                rejections: jsDebugFailureEvents('rejection'),
              });
            })();
            """,
            str(target),
            API_FETCH_CONTRACT_TIMEOUT_MS,
        )
    finally:
        _settle_controlled_filesystem_operation(gate_browser_runtime, release_backing)

    assert backing_started.is_set(), "the /api/fs/diff request never reached its deliberately hung callback"
    assert not metrics.get("error"), metrics
    assert (metrics.get("terminal") or {}).get("unavailable") is True, metrics
    assert "deadline_expired" in metrics["terminal"]["diffError"] and "deadline_expired" in metrics["terminal"]["text"], metrics
    assert API_FETCH_DEADLINE_MS <= metrics["elapsedMs"] <= API_FETCH_CONTRACT_TIMEOUT_MS + 500, metrics
    expected_api_errors = consume_only_expected_js_debug_api_errors(
        gate_browser_runtime.browser,
        ({
            "path": "/api/fs/diff",
            "method": "GET",
            "query": {"path": str(target), "from": "HEAD", "to": "current"},
            "error": "deadline_expired: request exceeded its 15s deadline",
        },),
    )
    assert metrics["errors"] == list(expected_api_errors) and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_slow_fs_read_and_diff_below_deadline_render_normal_content(gate_browser_runtime, tmp_path, monkeypatch):
    """Delayed job products below the deadline must still render their normal payloads."""
    repo = create_mock_git_repository(tmp_path / "test-slow-fs-success")
    target = repo.modified
    read_started = threading.Event()
    diff_started = threading.Event()
    _control_filesystem_operation_product(
        gate_browser_runtime,
        monkeypatch,
        target,
        "read",
        filesystem.read_file(str(target)),
        read_started,
        ready_after_seconds=SLOW_BACKEND_DELAY_SECONDS,
    )
    _control_filesystem_operation_product(
        gate_browser_runtime,
        monkeypatch,
        target,
        "diff",
        filesystem.diff_file(str(target)),
        diff_started,
        ready_after_seconds=SLOW_BACKEND_DELAY_SECONDS,
    )
    metrics = gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const expected = arguments[1];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const readStarted = performance.now();
            const item = await openFileInEditor(path, {name: path.split('/').at(-1)}, {userInitiated: true, viewMode: 'edit'});
            const readElapsedMs = performance.now() - readStarted;
            setFileEditorViewMode(path, 'diff', item);
            const diffStarted = performance.now();
            const diffLoaded = await refreshOpenFileDiff(path, {silent: false});
            const diffElapsedMs = performance.now() - diffStarted;
            renderOpenFilePath(path);
            const terminal = await window.__yolomuxTestWaitFor(() => {
              const state = fileState.get(path);
              const panel = fileEditorPanelsForPath(path)[0];
              const text = panel?._cmView?.state?.doc?.toString?.() || '';
              return state?.kind === 'text' && state.diffLoaded === true && state.diffUnavailable !== true && text === expected
                ? {text, diff: state.diff || '', status: panel.querySelector('.file-editor-status-message')?.textContent || ''}
                : null;
            }, {timeoutMs: 5000, description: `slow successful read and diff for ${path}`});
            done({readElapsedMs, diffElapsedMs, diffLoaded, terminal, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          } catch (error) {
            done({error: String(error?.stack || error)});
          }
        })();
        """,
        str(target),
        target.read_text(encoding="utf-8"),
    )

    assert not metrics.get("error"), metrics
    assert read_started.is_set() and diff_started.is_set(), metrics
    assert metrics["readElapsedMs"] >= SLOW_BACKEND_DELAY_SECONDS * 1000, metrics
    assert metrics["diffElapsedMs"] >= SLOW_BACKEND_DELAY_SECONDS * 1000, metrics
    assert metrics["readElapsedMs"] < API_FETCH_DEADLINE_MS and metrics["diffElapsedMs"] < API_FETCH_DEADLINE_MS, metrics
    assert metrics["diffLoaded"] is True and metrics["terminal"]["text"] == target.read_text(encoding="utf-8"), metrics
    assert metrics["terminal"]["diff"] and "deadline_expired" not in str(metrics), metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_c1_differ_opens_listed_file_twenty_consecutive_times(gate_browser_runtime, tmp_path):
    """C1: a file listed by Differ must open successfully in the rendered editor 20 consecutive times with zero failures; one successful open is insufficient."""
    repo, target = _make_changed_repo(tmp_path)
    _configure_differ_payload(gate_browser_runtime, repo, target)
    _open_differ(gate_browser_runtime, target)

    def open_once(iteration):
        metrics = gate_browser_runtime.browser.execute_async_script(
            """
            const path = arguments[0];
            const done = arguments[arguments.length - 1];
            (async () => {
              try {
                const row = document.querySelector(`#panel-__differ__ [data-open-change-file="${CSS.escape(path)}"]`);
                if (!row) throw new Error('listed Differ row disappeared');
                row.click();
                const opened = await window.__yolomuxTestWaitFor(() => {
                  const state = fileState.get(path);
                  const panels = fileEditorPanelsForPath(path);
                  return state?.diffLoaded === true
                    && panels.some(panel => panel?._cmView?.state?.doc?.toString?.().includes('working tree content'));
                }, {timeoutMs: 10000, description: `Differ open for ${path}`});
                const state = fileState.get(path);
                const panels = fileEditorPanelsForPath(path);
                const status = panels.map(panel => panel.querySelector('.file-editor-status-message')?.textContent || '').join(' ');
                const text = panels.map(panel => panel?._cmView?.state?.doc?.toString?.() || '').join('\\n');
                await removeOpenFile(path, {confirmDirty: false});
                await openFileSurface(differItemId);
                renderFileExplorerChangesPanels({force: true, view: 'differ'});
                await window.__yolomuxTestWaitFor(() => !fileState.has(path), {timeoutMs: 3000, description: `Differ close for ${path}`});
                done({opened, text, status, stateError: state?.error || '', tabClosed: !fileState.has(path)});
              } catch (error) {
                done({error: String(error?.stack || error)});
              }
            })();
            """,
            str(target),
        )
        assert not metrics.get("error"), f"iteration {iteration}: {metrics}"
        assert metrics["opened"] is True and "working tree content" in metrics["text"], metrics
        assert not metrics["stateError"] and "failed to load" not in metrics["status"].lower(), metrics
        assert metrics["tabClosed"] is True, metrics
        return metrics

    results = repeat(20, open_once)
    assert len(results) == 20


@pytest.mark.browser
def test_c2_differ_control_is_clickable_when_diff_is_available(gate_browser_runtime, tmp_path):
    """C2: for a fixture file with committed history and an available diff, the rendered Differ control must be present, enabled, and clickable, not merely present in the DOM."""
    _repo, target = _make_changed_repo(tmp_path)
    _open_editor(gate_browser_runtime, target, target.read_text(encoding="utf-8"))
    metrics = gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const button = await window.__yolomuxTestWaitFor(() => {
              const panel = fileEditorPanelsForPath(path)[0];
              const candidate = panel?.querySelector('.file-editor-diff-panel');
              return candidate && !candidate.hidden && !candidate.disabled ? candidate : null;
            }, {timeoutMs: 10000, description: `enabled Differ control for ${path}`});
            const before = {disabled: button.disabled, hidden: button.hidden, pointerEvents: getComputedStyle(button).pointerEvents};
            button.click();
            const clicked = await window.__yolomuxTestWaitFor(() => {
              const item = fileEditorPanelsForPath(path)[0]?.dataset?.layoutItem || fileEditorItemFor(path);
              return editorViewModeFor(path, item) === 'diff' && fileState.get(path)?.diffLoaded === true;
            }, {timeoutMs: 10000, description: `Differ mode after click for ${path}`});
            done({before, clicked, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          } catch (error) {
            done({error: String(error?.stack || error)});
          }
        })();
        """,
        str(target),
    )
    assert not metrics.get("error"), metrics
    assert metrics["before"] == {"disabled": False, "hidden": False, "pointerEvents": "auto"}, metrics
    assert metrics["clicked"] is True and metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_c1_c2_vanished_working_file_keeps_enabled_differ_and_renders_git_diff(
    gate_browser_runtime, tmp_path
):
    """C1/C2 amend: a listed file that vanishes must retain an enabled Differ control and render committed bytes."""
    repo, target = _make_changed_repo(tmp_path)
    payload = _differ_payload(repo, target, gate_browser_runtime.session)
    gate_browser_runtime.runtime.app.session_files_payload = (
        lambda _session=None, _hours=24.0, **_kwargs: (payload, HTTPStatus.OK)
    )
    _open_differ(gate_browser_runtime, target)
    target.unlink()
    metrics = gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const row = document.querySelector(`#panel-__differ__ [data-open-change-file="${CSS.escape(path)}"]`);
            if (!row) throw new Error(`listed Differ row missing for ${path}`);
            row.click();
            const panel = await window.__yolomuxTestWaitFor(
              () => fileEditorPanelsForPath(path)[0],
              {timeoutMs: 10000, description: `vanished Differ editor for ${path}`},
            );
            const button = await window.__yolomuxTestWaitFor(() => {
              const candidate = panel.querySelector('.file-editor-diff-panel');
              return candidate && !candidate.hidden && !candidate.disabled ? candidate : null;
            }, {timeoutMs: 10000, description: `enabled vanished Differ control for ${path}`});
            button.click();
            await window.__yolomuxTestWaitFor(() => {
              const state = fileState.get(path);
              const item = panel.dataset.layoutItem || fileEditorItemFor(path);
              const rendered = [...panel.querySelectorAll('.cm-content')].map(node => node.textContent || '').join('\\n');
              return state?.diffLoaded === true
                && state.diffWorkingMissing === true
                && editorViewModeFor(path, item) === 'diff'
                && rendered.includes('second committed content');
            }, {timeoutMs: 10000, description: `committed Git diff for vanished ${path}`});
            const state = fileState.get(path);
            const rendered = [...panel.querySelectorAll('.cm-content')].map(node => node.textContent || '').join('\\n');
            done({
              button: {disabled: button.disabled, hidden: button.hidden, pointerEvents: getComputedStyle(button).pointerEvents},
              diffLoaded: state?.diffLoaded === true,
              workingMissing: state?.diffWorkingMissing === true,
              diff: state?.diff || '',
              original: state?.diffOriginal || '',
              working: state?.diffWorking || '',
              rendered,
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error?.stack || error)});
          }
        })();
        """,
        str(target),
    )
    assert not metrics.get("error"), metrics
    assert metrics["button"] == {"disabled": False, "hidden": False, "pointerEvents": "auto"}, metrics
    assert metrics["diffLoaded"] is True and metrics["workingMissing"] is True, metrics
    assert "second committed content" in metrics["original"] and metrics["working"] == "", metrics
    assert metrics["diff"] and "second committed content" in metrics["rendered"], metrics
    expected_api_error = consume_only_expected_js_debug_api_error(
        gate_browser_runtime.browser,
        path="/api/fs/read",
        status=HTTPStatus.NOT_FOUND,
        method="GET",
        query={"path": str(target)},
    )
    assert metrics["errors"] == [expected_api_error] and metrics["rejections"] == [], metrics
    retire_expected_fixture_typed_api_failure(
        gate_browser_runtime.browser,
        gate_browser_runtime.server,
        expected_api_error,
        method="GET",
        path="/api/fs/read",
        source="jobd-operation",
        code="path_not_found",
    )


@pytest.mark.browser
def test_c3_git_control_only_change_publishes_no_ignored_path_as_a_transport_signal(gate_browser_runtime, tmp_path):
    """C3: a .git-only branch change must publish no changed path at all.

    ``.git`` is an ignored directory exactly like ``.cache``, ``node_modules`` or
    a user-configured exclusion, so nothing beneath it may reach a watch
    revision, a generation bump, browser filesystem history or a diagnostic.
    This test previously required the opposite: it waited for ``.git/HEAD`` to
    appear in ``changed_paths`` and drove the badge refresh from it, which made
    an ignored pathname the transport signal.  The user-visible contract is kept
    and re-pointed at an admissible signal: the branch/status badges and
    Modified-files must still converge, but they converge off an ordinary
    working-tree change, not off anything beneath ``.git``.  The ignored-only
    change itself must publish nothing at all, which is asserted directly.
    """
    repo, target = _make_changed_repo(tmp_path)
    session = gate_browser_runtime.session

    def payload(_session=None, _hours=24.0, **_kwargs):
        result = _differ_payload(repo, target, session)
        result["repos"][0]["branch"] = _git(repo, "branch", "--show-current").stdout.strip()
        return result, HTTPStatus.OK

    gate_browser_runtime.runtime.app.session_files_payload = payload
    _open_differ(gate_browser_runtime, target)
    registration = gate_browser_runtime.browser.execute_async_script(
        """
        const repo = arguments[0];
        const done = arguments[arguments.length - 1];
        const previousSyncedAt = Number(serverWatchRootsState.syncedAt || 0);
        syncServerWatchRoots({immediate: true, force: true});
        window.__yolomuxTestWaitFor(() => {
          const descriptor = clientEventDemandDescriptor();
          return descriptor.channels.includes('files')
            && descriptor.active_panes.includes(differItemId)
            && clientEventTransportState.connected === true
            && clientServerWatchRootDescriptor().roots.includes(repo)
            && Number(serverWatchRootsState.syncedAt || 0) > previousSyncedAt
            && serverWatchRootsState.inFlight !== true;
        }, {timeoutMs: 10000, description: `real watchd registration for ${repo}`}).then(
          () => done({ok: true, roots: clientServerWatchRootDescriptor().roots, descriptor: clientEventDemandDescriptor()}),
          error => done({error: String(error?.stack || error)}),
        );
        """,
        str(repo),
    )
    assert registration.get("error") is None and registration.get("ok") is True, registration
    assert str(repo) in registration["roots"], registration
    assert registration["descriptor"]["active_panes"] and registration["descriptor"]["channels"], registration

    app = gate_browser_runtime.runtime.app
    baseline_watchd = _wait_for_active_watchd_descriptor(gate_browser_runtime, repo, "roots")
    before_bytes = target.read_bytes()
    before_stat = target.stat()
    branch = "gate-control-only"
    _git(repo, "switch", "-q", "-c", branch)
    after_stat = target.stat()
    assert target.read_bytes() == before_bytes
    assert (after_stat.st_ino, after_stat.st_size, after_stat.st_mtime_ns) == (
        before_stat.st_ino,
        before_stat.st_size,
        before_stat.st_mtime_ns,
    )

    # Proving a negative needs a barrier, not a sleep: publish one ordinary
    # working-tree file and wait for that admissible path to arrive. Every
    # revision up to and including it has therefore been observed, so any
    # ignored path the daemon would have published has already had its chance.
    barrier_file = repo / "c3_barrier.txt"
    barrier_file.write_text("barrier\n", encoding="utf-8")
    changed_watchd = _wait_for_watchd_path_change(
        gate_browser_runtime,
        baseline_watchd,
        barrier_file,
        "changed_paths",
    )
    git_control_root = str((repo / ".git").resolve(strict=False))
    with app.client_watch_service.lock:
        published_paths = [
            str(published)
            for history_record in app.client_watch_service.filesystem_history
            for published in (history_record.get("changed_paths") or ())
        ]
    leaked_ignored_paths = [
        published
        for published in published_paths
        if published == git_control_root or published.startswith(f"{git_control_root}/")
    ]
    assert leaked_ignored_paths == [], leaked_ignored_paths

    metrics = gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const branch = arguments[1];
        const done = arguments[arguments.length - 1];
        const snapshot = () => {
          const repoInfo = fileExplorerSessionFilesState.payload?.repos?.find(item => item.repo && path.startsWith(item.repo + '/'));
          const row = document.querySelector(`#panel-__differ__ [data-open-change-file="${CSS.escape(path)}"]`);
          const status = row?.querySelector('.file-tree-git-status');
          const branchBadges = [...document.querySelectorAll('.file-tree-repo-branch, .meta-branch, .repo-chip-branch')];
          return {
            payloadBranch: repoInfo?.branch || null,
            rowConnected: row?.isConnected === true,
            status: status?.textContent?.trim() || null,
            branchBadges: branchBadges.map(item => item.textContent.trim()),
            channels: clientEventDemandDescriptor().channels,
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          };
        };
        window.__yolomuxTestWaitFor(() => {
          const current = snapshot();
          return current.payloadBranch === branch
            && current.rowConnected
            && current.status === 'M'
            && current.branchBadges.some(item => item.includes(branch));
        }, {timeoutMs: 10000, description: `Git-control branch/status/Modified-files convergence for ${path}`}).then(
          () => done(snapshot()),
          error => done({...snapshot(), error: String(error?.stack || error)}),
        );
        """,
        str(target),
        branch,
    )
    assert not metrics.get("error"), metrics
    assert metrics["payloadBranch"] == branch and metrics["status"] == "M", metrics
    assert metrics["rowConnected"] is True and branch in metrics["branchBadges"], metrics
    assert "files" in metrics["channels"], metrics
    with app.client_watch_service.lock:
        final_record = app.client_watch_service.event_watcher_record
        final_watchd = (final_record.watchd_epoch, final_record.watchd_revision)
    assert final_watchd[0] == changed_watchd["epoch"]
    assert final_watchd[1] >= changed_watchd["revision"]


@pytest.mark.browser
def test_c4_dirty_editor_survives_differ_open_and_external_refresh(gate_browser_runtime, tmp_path):
    """C4: unsaved editor text must survive opening Differ for the same file and must still survive an external files-channel refresh while Differ remains open."""
    repo, target = _make_changed_repo(tmp_path)
    _configure_differ_payload(gate_browser_runtime, repo, target)
    _open_editor(gate_browser_runtime, target, target.read_text(encoding="utf-8"))
    _wait_for_file_event_stream(gate_browser_runtime, target)
    dirty_text = _type_dirty_text(gate_browser_runtime, target, "dirty text survives Differ")
    _open_differ(gate_browser_runtime, target)
    opened = gate_browser_runtime.browser.execute_script(
        """
        const path = arguments[0];
        const state = fileState.get(path);
        const panels = fileEditorPanelsForPath(path);
        return {dirty: state?.dirty === true, texts: panels.map(panel => panel?._cmView?.state?.doc?.toString?.() || ''), differActive: document.querySelector('#panel-__differ__')?.classList.contains('active-pane') === true};
        """,
        str(target),
    )
    assert opened["dirty"] is True and dirty_text in opened["texts"] and opened["differActive"] is True, opened
    target.write_text("external refresh while Differ is open\n", encoding="utf-8")
    _publish_file_change(gate_browser_runtime, target)
    metrics = _dirty_conflict_snapshot(gate_browser_runtime, target, dirty_text)
    assert not metrics.get("error"), metrics
    assert metrics["dirty"] is True and metrics["text"] == dirty_text and metrics["tabConnected"] is True, metrics
    assert metrics["status"] and metrics["missing"] is False, metrics


@pytest.mark.browser
def test_c5_diff_list_generation_precedes_file_open_without_retry(gate_browser_runtime, tmp_path):
    """C5: the observable diff-list-to-file-open transition must prove the listed revision is ready before the browser issues its one file-open request, with no retry masking an ordering race."""
    repo, target = _make_changed_repo(tmp_path)
    _configure_differ_payload(gate_browser_runtime, repo, target)
    _open_differ(gate_browser_runtime, target)
    gate_browser_runtime.browser.execute_script(
        """
        const nativeFetch = window.fetch.bind(window);
        window.__gateDifferRequests = [];
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), location.href);
          const record = {path: url.pathname, query: url.search, started: performance.now(), finished: null, status: null};
          if (url.pathname === '/api/session-files' || url.pathname === '/api/fs/read' || url.pathname === '/api/fs/diff') window.__gateDifferRequests.push(record);
          const response = await nativeFetch(input, options);
          record.finished = performance.now();
          record.status = response.status;
          return response;
        };
        """
    )
    metrics = gate_browser_runtime.browser.execute_async_script(
        """
        const path = arguments[0];
        const session = arguments[1];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            await fetchSessionFiles({destination: 'finder', session, force: true, silent: true});
            renderFileExplorerChangesPanels({force: true, view: 'differ'});
            const row = await window.__yolomuxTestWaitFor(
              () => document.querySelector(`#panel-__differ__ [data-open-change-file="${CSS.escape(path)}"]`),
              {timeoutMs: 10000, description: `Differ row after list completion for ${path}`},
            );
            row.click();
            await window.__yolomuxTestWaitFor(() => fileState.get(path)?.diffLoaded === true, {timeoutMs: 10000, description: `ordered diff open for ${path}`});
            done({requests: window.__gateDifferRequests, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          } catch (error) {
            done({error: String(error?.stack || error), requests: window.__gateDifferRequests});
          }
        })();
        """,
        str(target),
        gate_browser_runtime.session,
    )
    assert not metrics.get("error"), metrics
    session_files = [request for request in metrics["requests"] if request["path"] == "/api/session-files"]
    forced_session_files = [request for request in session_files if "force=1" in request["query"]]
    reads = [request for request in metrics["requests"] if request["path"] == "/api/fs/read"]
    diffs = [request for request in metrics["requests"] if request["path"] == "/api/fs/diff"]
    assert session_files and all(request["status"] == HTTPStatus.OK for request in session_files), metrics
    assert forced_session_files, metrics
    assert len(reads) == 1 and reads[0]["status"] == HTTPStatus.ACCEPTED, metrics
    assert len(diffs) == 1 and diffs[0]["status"] == HTTPStatus.ACCEPTED, metrics
    assert max(request["finished"] for request in forced_session_files) <= reads[0]["started"] <= diffs[0]["started"], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_a8_differ_recovers_from_ten_atomic_inode_swaps_and_keeps_real_delete_typed(gate_browser_runtime, tmp_path):
    """A8 Differ: ten varying sub-second os.replace inode swaps must recover without reload/reopen and clear the tab decoration; a real delete remains visibly missing while Git diff content stays rendered."""
    repo, target = _make_changed_repo(tmp_path)
    _configure_differ_payload(gate_browser_runtime, repo, target)
    _open_differ(gate_browser_runtime, target)
    _open_file_from_differ(gate_browser_runtime, target)
    _wait_for_file_event_stream(gate_browser_runtime, target)
    gate_browser_runtime.browser.execute_script("document.activeElement?.blur?.(); document.body.focus();")

    def replace_once(iteration):
        _a8_wait_varying_subsecond_interval(gate_browser_runtime, iteration)
        replacement = f"A8 Differ inode replacement {iteration} {'y' * iteration}\n"
        elapsed = _a8_replace_inode(target, replacement, iteration)
        _a8_latch_transient_inode_miss(gate_browser_runtime, target)
        _publish_file_change(gate_browser_runtime, target)
        metrics = _a8_recovered_snapshot(gate_browser_runtime, target, replacement, expected_view_mode="diff")
        assert not metrics.get("error"), metrics
        assert metrics["text"] == replacement and metrics["viewMode"] == "diff", metrics
        assert metrics["missing"] is False and metrics["tabMissing"] is False and metrics["missingBadge"] is False, metrics
        assert metrics["errors"] == [] and metrics["rejections"] == [], metrics
        return elapsed

    elapsed_replaces = repeat(10, replace_once)
    assert len(set(A8_FRAME_COUNTS)) > 1
    assert len(elapsed_replaces) == 10 and max(elapsed_replaces) < 1.0

    target.unlink()
    _publish_file_change(gate_browser_runtime, target)
    missing = _a8_missing_snapshot(gate_browser_runtime, target)
    deleted_row = _a8_deleted_differ_row_snapshot(gate_browser_runtime, target)
    assert not missing.get("error"), missing
    assert not deleted_row.get("error"), deleted_row
    assert missing["missing"] is True and missing["tabMissing"] is True and missing["missingBadge"] is True, missing
    assert "second committed content" in missing["rendered"], missing
    assert deleted_row == {"status": "D", "added": "", "removed": "-1"}, deleted_row
