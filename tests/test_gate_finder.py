from __future__ import annotations

import stat
import time

import pytest
from selenium.webdriver.support.ui import WebDriverWait

from tools import static_build
from tests.browser_helpers.browser_console import assert_only_expected_browser_warning
from tests.browser_helpers.browser_console import consume_only_expected_js_debug_api_errors
from tests.browser_helpers.browser_layout import browser
from tests.browser_helpers.browser_layout import fixture_page_url
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture
from tests.browser_helpers.browser_layout import serve_repo_fixture_page
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.terminal_state_guard import assert_terminal_transition
from yolomux_lib import app as app_module


FINDER_ROOT = "/home/test"
FINDER_DIRECTORY = f"{FINDER_ROOT}/project"
FINDER_CHILD = f"{FINDER_DIRECTORY}/nested.txt"
FINDER_ENTRIES = {
    FINDER_ROOT: [{"name": "project", "kind": "dir"}],
    FINDER_DIRECTORY: [{"name": "nested.txt", "kind": "file"}],
}
QUIET_POLL_CYCLES = 25


def _load_finder(browser, tmp_path) -> None:
    source_bundle = serve_repo_fixture_page("finder-source.js", static_build.build_asset("yolomux.js"))
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed", "dir_cache_ms": 5000}},
        fs_entries=FINDER_ENTRIES,
        runtime_script_uri=fixture_page_url(source_bundle),
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__finder__ .file-tree-row[data-path=\"/home/test/project\"]')?.getAttribute('aria-expanded') === 'false';"
        )
    )


@pytest.mark.browser
def test_b1_five_reexpansions_render_cached_children_without_a_network_round_trip(browser, tmp_path):
    """Five collapse/re-expand cycles render in the cache turn and issue no new filesystem request."""
    _load_finder(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        (async () => {
          const directory = '/home/test/project';
          const childPath = '/home/test/project/nested.txt';
          const row = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${directory}"]`);
          const child = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${childPath}"]`);
          const directoryRequests = () => window.__bootFetches.filter(item => (
            (item.path === '/api/fs/batch' && item.body?.requests?.some(request => request.type === 'list' && request.path === directory))
            || (item.path === '/api/fs/list' && new URLSearchParams(item.search).get('path') === directory)
          )).length;

          row().click();
          await window.__yolomuxTestWaitFor(
            () => child() && row()?.getAttribute('aria-expanded') === 'true' && !row()?.classList.contains('loading-children'),
            {timeoutMs: 2000, description: 'cold Finder directory expansion'},
          );
          const coldRequests = directoryRequests();
          row().click();
          if (row()?.getAttribute('aria-expanded') !== 'false' || child()) throw new Error('cold expansion did not collapse synchronously');

          const cycles = [];
          for (let index = 0; index < 5; index += 1) {
            const requestsBefore = directoryRequests();
            const startedAt = performance.now();
            row().click();
            await Promise.resolve();
            await Promise.resolve();
              cycles.push({
                index,
                elapsedMs: performance.now() - startedAt,
                expanded: row()?.getAttribute('aria-expanded') || '',
                childVisible: Boolean(child()),
                loading: row()?.classList.contains('loading-children') === true,
                requestsBefore,
                requestsAfter: directoryRequests(),
              });
              await window.__yolomuxTestWaitFor(
                () => child() && row()?.getAttribute('aria-expanded') === 'true' && !row()?.classList.contains('loading-children'),
                {timeoutMs: 2000, description: `cached Finder re-expansion ${index}`},
              );
              row().click();
              if (row()?.getAttribute('aria-expanded') !== 'false' || child()) throw new Error(`cycle ${index} did not collapse synchronously`);
          }
          done({coldRequests, cycles, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        })().catch(error => done({error: String(error?.stack || error), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')}));
        """
    )

    assert not metrics.get("error"), metrics
    assert metrics["coldRequests"] == 1, metrics
    assert len(metrics["cycles"]) == 5, metrics
    assert all(cycle["expanded"] == "true" and cycle["childVisible"] and not cycle["loading"] for cycle in metrics["cycles"]), metrics
    assert all(cycle["requestsAfter"] == cycle["requestsBefore"] == 1 for cycle in metrics["cycles"]), metrics
    assert all(cycle["elapsedMs"] < 100 for cycle in metrics["cycles"]), metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_b2_expansion_remains_rendered_after_its_disclosure_animation_settles(browser, tmp_path):
    _load_finder(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        (async () => {
          const directory = '/home/test/project';
          const childPath = '/home/test/project/nested.txt';
          const row = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${directory}"]`);
          const child = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${childPath}"]`);
          row().click();
          await window.__yolomuxTestWaitFor(
            () => child() && !row()?.classList.contains('loading-children'),
            {timeoutMs: 2000, description: 'Finder expansion content'},
          );
          const finiteAnimations = row().getAnimations({subtree: true}).filter(animation => animation.effect?.getTiming?.().iterations !== Infinity);
          await Promise.allSettled(finiteAnimations.map(animation => animation.finished));
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          done({
            expanded: row()?.getAttribute('aria-expanded') || '',
            expandedSet: fileExplorerExpanded.has(directory),
            childVisible: Boolean(child()),
            loading: row()?.classList.contains('loading-children') === true,
            finiteAnimations: finiteAnimations.length,
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        })().catch(error => done({error: String(error?.stack || error), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')}));
        """
    )

    assert not metrics.get("error"), metrics
    assert metrics["expanded"] == "true" and metrics["expandedSet"] is True, metrics
    assert metrics["childVisible"] is True and metrics["loading"] is False, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_transient_fs_batch_failure_falls_back_to_direct_list_and_renders_children(browser, tmp_path):
    _load_finder(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch;
        (async () => {
          const directory = '/home/test/project';
          const childPath = '/home/test/project/nested.txt';
          const row = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${directory}"]`);
          const child = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${childPath}"]`);
          let failedBatchCount = 0;
          let directListCount = 0;
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            const body = options.body ? JSON.parse(options.body) : null;
            const ownsExpansion = url.pathname === '/api/fs/batch'
              && body?.requests?.some(request => request.type === 'list' && request.path === directory);
            if (ownsExpansion && failedBatchCount === 0) {
              failedBatchCount += 1;
              return Promise.reject(new TypeError('Failed to fetch'));
            }
            if (url.pathname === '/api/fs/list' && url.searchParams.get('path') === directory) directListCount += 1;
            return originalFetch(input, options);
          };

          row().click();
          await window.__yolomuxTestWaitFor(
            () => child() && row()?.getAttribute('aria-expanded') === 'true' && !row()?.classList.contains('loading-children'),
            {timeoutMs: 2000, description: 'Finder direct-list recovery after failed batch'},
          );
          window.fetch = originalFetch;
          done({
            failedBatchCount,
            directListCount,
            childVisible: Boolean(child()),
            expanded: row()?.getAttribute('aria-expanded') || '',
            pending: fileExplorerPendingExpansions.has(directory),
            loading: row()?.classList.contains('loading-children') === true,
            errorText: row()?.nextElementSibling?.querySelector?.('.file-tree-status-error')?.textContent.trim() || '',
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        })().catch(error => {
          window.fetch = originalFetch;
          done({error: String(error?.stack || error), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        });
        """
    )

    assert not metrics.get("error"), metrics
    assert metrics["failedBatchCount"] == 1 and metrics["directListCount"] == 1, metrics
    assert metrics["childVisible"] is True and metrics["expanded"] == "true", metrics
    assert metrics["pending"] is False and metrics["loading"] is False and metrics["errorText"] == "", metrics
    expected_api_errors = consume_only_expected_js_debug_api_errors(
        browser,
        ({"path": "/api/fs/batch", "method": "POST", "query": {}, "error": "Failed to fetch"},),
    )
    assert len(expected_api_errors) == metrics["failedBatchCount"], metrics
    assert metrics["errors"] == list(expected_api_errors) and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_network_restore_retries_a_directory_expansion_lost_during_the_outage(browser, tmp_path):
    _load_finder(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch;
        (async () => {
          const directory = '/home/test/project';
          const childPath = '/home/test/project/nested.txt';
          const row = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${directory}"]`);
          const child = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${childPath}"]`);
          let failedBatchCount = 0;
          let failedDirectListCount = 0;
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            const body = options.body ? JSON.parse(options.body) : null;
            const ownsExpansionBatch = url.pathname === '/api/fs/batch'
              && body?.requests?.some(request => request.type === 'list' && request.path === directory);
            if (ownsExpansionBatch && failedBatchCount === 0) {
              failedBatchCount += 1;
              return Promise.reject(new TypeError('Failed to fetch'));
            }
            if (url.pathname === '/api/fs/list' && url.searchParams.get('path') === directory && failedDirectListCount === 0) {
              failedDirectListCount += 1;
              return Promise.reject(new TypeError('Failed to fetch'));
            }
            return originalFetch(input, options);
          };

          row().click();
          await window.__yolomuxTestWaitFor(
            () => failedDirectListCount === 1 && !fileExplorerPendingExpansions.has(directory),
            {timeoutMs: 2000, description: 'Finder expansion settles after network outage'},
          );
          const failed = {
            childVisible: Boolean(child()),
            expanded: row()?.getAttribute('aria-expanded') || '',
            pending: fileExplorerPendingExpansions.has(directory),
            loading: row()?.classList.contains('loading-children') === true,
            errorText: row()?.nextElementSibling?.querySelector?.('.file-tree-status-error')?.textContent.trim() || '',
          };

          window.fetch = originalFetch;
          window.dispatchEvent(new Event('online'));
          await window.__yolomuxTestWaitFor(
            () => child() && row()?.getAttribute('aria-expanded') === 'true' && !row()?.classList.contains('loading-children'),
            {timeoutMs: 3000, description: 'Finder expansion recovers after network restore'},
          );
          done({
            failedBatchCount,
            failedDirectListCount,
            failed,
            recovered: {
              childVisible: Boolean(child()),
              expanded: row()?.getAttribute('aria-expanded') || '',
              pending: fileExplorerPendingExpansions.has(directory),
              loading: row()?.classList.contains('loading-children') === true,
              errorText: row()?.nextElementSibling?.querySelector?.('.file-tree-status-error')?.textContent.trim() || '',
            },
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        })().catch(error => {
          window.fetch = originalFetch;
          done({error: String(error?.stack || error), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        });
        """
    )

    assert not metrics.get("error"), metrics
    assert metrics["failedBatchCount"] == 1 and metrics["failedDirectListCount"] == 1, metrics
    assert metrics["failed"]["childVisible"] is False and metrics["failed"]["expanded"] == "false", metrics
    assert metrics["failed"]["pending"] is False and metrics["failed"]["loading"] is False, metrics
    assert metrics["failed"]["errorText"], metrics
    assert metrics["recovered"] == {
        "childVisible": True,
        "expanded": "true",
        "pending": False,
        "loading": False,
        "errorText": "",
    }, metrics
    expected_api_errors = consume_only_expected_js_debug_api_errors(
        browser,
        (
            {"path": "/api/fs/batch", "method": "POST", "query": {}, "error": "Failed to fetch"},
            {
                "path": "/api/fs/list",
                "method": "GET",
                "query": {"path": FINDER_DIRECTORY},
                "error": "Failed to fetch",
            },
        ),
    )
    expected_count = metrics["failedBatchCount"] + metrics["failedDirectListCount"]
    assert len(expected_api_errors) == expected_count, metrics
    assert metrics["errors"] == list(expected_api_errors) and metrics["rejections"] == [], metrics
    assert_only_expected_browser_warning(
        browser,
        message="fs list error",
        correlation='"/home/test/project" TypeError: Failed to fetch',
    )


@pytest.mark.browser
def test_b3_second_click_cancels_a_pending_production_batch_expansion(browser, tmp_path):
    """Hold the real ``/api/fs/batch`` response; ``fetchDirectory`` itself is never replaced."""
    _load_finder(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch;
        const originalFetchDirectory = fetchDirectory;
        (async () => {
          const directory = '/home/test/project';
          const childPath = '/home/test/project/nested.txt';
          const row = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${directory}"]`);
          const child = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${childPath}"]`);
          let releaseBatch = null;
          let heldBatchCount = 0;
          let heldRequest = null;
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            const body = options.body ? JSON.parse(options.body) : null;
            const ownsExpansion = url.pathname === '/api/fs/batch'
              && body?.requests?.some(request => request.type === 'list' && request.path === directory);
            if (!ownsExpansion) return originalFetch(input, options);
            heldBatchCount += 1;
            heldRequest = body.requests.find(request => request.type === 'list' && request.path === directory);
            return new Promise((resolve, reject) => {
              releaseBatch = () => Promise.resolve(originalFetch(input, options)).then(resolve, reject);
            });
          };

          row().click();
          await window.__yolomuxTestWaitFor(
            () => releaseBatch && fileExplorerPendingExpansions.has(directory) && row()?.classList.contains('loading-children'),
            {timeoutMs: 2000, description: 'pending production Finder batch'},
          );
          const afterFirst = {
            expanded: row()?.getAttribute('aria-expanded') || '',
            pending: fileExplorerPendingExpansions.has(directory),
            loading: row()?.classList.contains('loading-children') === true,
          };
          row().click();
          await Promise.resolve();
          const afterSecond = {
            expanded: row()?.getAttribute('aria-expanded') || '',
            pending: fileExplorerPendingExpansions.has(directory),
            loading: row()?.classList.contains('loading-children') === true,
            childVisible: Boolean(child()),
          };
          releaseBatch();
          await window.__yolomuxTestWaitFor(
            () => !fileExplorerPendingExpansions.has(directory),
            {timeoutMs: 2000, description: 'settled cancelled Finder batch'},
          );
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const final = {
            expanded: row()?.getAttribute('aria-expanded') || '',
            pending: fileExplorerPendingExpansions.has(directory),
            loading: row()?.classList.contains('loading-children') === true,
            childVisible: Boolean(child()),
            expandedSet: fileExplorerExpanded.has(directory),
          };
          window.fetch = originalFetch;
          done({
            fetchDirectoryPreserved: fetchDirectory === originalFetchDirectory,
            heldBatchCount,
            heldRequest,
            eventSourceCount: window.__eventSources.length,
            afterFirst,
            afterSecond,
            final,
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        })().catch(error => {
          window.fetch = originalFetch;
          done({error: String(error?.stack || error), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        });
        """
    )

    assert not metrics.get("error"), metrics
    assert metrics["fetchDirectoryPreserved"] is True, metrics
    assert metrics["heldBatchCount"] == 1, metrics
    assert metrics["heldRequest"]["trigger_counts"]["explicit-user"] == 1, metrics
    assert metrics["eventSourceCount"] >= 1, metrics
    assert metrics["afterFirst"] == {"expanded": "true", "pending": True, "loading": True}, metrics
    assert metrics["afterSecond"] == {"expanded": "false", "pending": False, "loading": False, "childVisible": False}, metrics
    assert metrics["final"] == {"expanded": "false", "pending": False, "loading": False, "childVisible": False, "expandedSet": False}, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_b5_queued_directory_expansion_clears_spinner_and_renders_typed_error(browser, tmp_path):
    _load_finder(browser, tmp_path)
    metrics = browser.execute_async_script("""
      const done=arguments[0], originalFetch=window.fetch, directory='/home/test/project';
      const row=()=>document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${directory}"]`);
      (async()=>{
        window.fetch=async(input,options={})=>{const url=new URL(String(input),location.href);const body=options.body?JSON.parse(options.body):null;const request=body?.requests?.find(value=>value.type==='list'&&value.path===directory);if(url.pathname!=='/api/fs/batch'||!request)return originalFetch(input,options);return new Response(JSON.stringify({responses:[{id:request.id,ok:false,status:202,error:'QUEUED'}]}),{status:200,headers:{'Content-Type':'application/json'}});};
        row().click();await window.__yolomuxTestWaitFor(()=>fileExplorerPendingExpansions.has(directory),{timeoutMs:2000,description:'queued Finder expansion starts'});await window.__yolomuxTestWaitFor(()=>!fileExplorerPendingExpansions.has(directory)&&!row()?.classList.contains('loading-children'),{timeoutMs:2000,description:'queued Finder expansion settles'});const errorText=row()?.nextElementSibling?.querySelector?.('.file-tree-status-error')?.textContent.trim()||'';window.fetch=originalFetch;done({loading:row()?.classList.contains('loading-children')===true,pending:fileExplorerPendingExpansions.has(directory),errorText,errors:jsDebugFailureEvents('error'),rejections:jsDebugFailureEvents('rejection')});
      })().catch(error=>{window.fetch=originalFetch;done({error:String(error?.stack||error),errors:jsDebugFailureEvents('error'),rejections:jsDebugFailureEvents('rejection')});});
    """)
    assert not metrics.get("error"), metrics
    assert metrics["loading"] is False and metrics["pending"] is False and metrics["errorText"] == "QUEUED", metrics
    assert consume_only_expected_js_debug_api_errors(browser, ()) == (), metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics
    assert_only_expected_browser_warning(
        browser,
        message="fs list failed",
        correlation='"/home/test/project" 202 "QUEUED"',
    )


@pytest.mark.browser
def test_b5_failed_directory_expansion_clears_spinner_and_renders_typed_error(browser, tmp_path):
    """A real batch error must settle the row; a permanent spinner hides the failure."""
    _load_finder(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch;
        (async () => {
          const directory = '/home/test/project';
          const row = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${directory}"]`);
          window.fetch = async (input, options = {}) => {
            const url = new URL(String(input), location.href);
            const body = options.body ? JSON.parse(options.body) : null;
            const request = body?.requests?.find(request => request.type === 'list' && request.path === directory);
            if (url.pathname !== '/api/fs/batch' || !request) return originalFetch(input, options);
            return new Response(JSON.stringify({responses: [{id: request.id, ok: false, status: 503, error: 'directory service unavailable'}]}), {
              status: 200,
              headers: {'Content-Type': 'application/json'},
            });
          };
          row().click();
          await window.__yolomuxTestWaitFor(
            () => !fileExplorerPendingExpansions.has(directory) && !row()?.classList.contains('loading-children'),
            {timeoutMs: 2000, description: 'failed Finder expansion settles'},
          );
          const errorRow = row()?.nextElementSibling?.querySelector?.('.file-tree-status-error');
          window.fetch = originalFetch;
          done({
            expanded: row()?.getAttribute('aria-expanded') || '',
            loading: row()?.classList.contains('loading-children') === true,
            pending: fileExplorerPendingExpansions.has(directory),
            errorText: errorRow?.textContent.trim() || '',
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        })().catch(error => {
          window.fetch = originalFetch;
          done({error: String(error?.stack || error), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        });
        """
    )

    assert not metrics.get("error"), metrics
    assert metrics["expanded"] == "false" and metrics["loading"] is False and metrics["pending"] is False, metrics
    assert metrics["errorText"] == "directory service unavailable", metrics
    assert consume_only_expected_js_debug_api_errors(browser, ()) == (), metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics
    assert_only_expected_browser_warning(
        browser,
        message="fs list failed",
        correlation='"/home/test/project" 503 "directory service unavailable"',
    )


@pytest.mark.browser
def test_finder_requeued_expansion_repairs_after_stream_reconnect_and_clears_spinner(browser, tmp_path):
    """A 202/QUEUED directory listing stays owned until its generation can be read."""
    _load_finder(browser, tmp_path)
    screenshot_path = tmp_path / "finder-queued-expansion-repaired.png"
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch;
        let batchCalls = 0;
        let directCalls = 0;
        (async () => {
          const directory = '/home/test/project';
          const childPath = '/home/test/project/nested.txt';
          const productKey = `storaged.products:finder:${directory}`;
          const row = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${directory}"]`);
          const child = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${childPath}"]`);
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            const body = options.body ? JSON.parse(options.body) : null;
            if (url.pathname === '/api/fs/batch'
                && body?.requests?.some(request => request.type === 'list' && request.path === directory)) {
              batchCalls += 1;
              return Promise.resolve(new Response(JSON.stringify({
                responses: body.requests.map(request => request.path === directory ? {
                  id: request.id,
                  ok: false,
                  pending: true,
                  status: 202,
                  payload: {status: 'QUEUED', ticket: 'finder-gate-ticket', key: productKey},
                } : {id: request.id, ok: false, status: 404, error: 'unexpected request'}),
              }), {status: 200, headers: {'Content-Type': 'application/json'}}));
            }
            if (url.pathname === '/api/fs/list' && url.searchParams.get('path') === directory) {
              directCalls += 1;
              if (directCalls === 1) {
                return Promise.resolve(new Response(JSON.stringify({
                  status: 'QUEUED', ticket: 'finder-gate-ticket', key: productKey,
                }), {status: 202, headers: {'Content-Type': 'application/json'}}));
              }
              return originalFetch(input, options);
            }
            return originalFetch(input, options);
          };

          row().click();
          await window.__yolomuxTestWaitFor(() => (
            batchCalls === 1
            && Array.from(fileExplorerFsBatchPending.values()).some(pending => (
              pending.item?.queuedProduct?.key === productKey && pending.item?.repairing !== true
            ))
          ), {
            timeoutMs: 2000,
            description: 'forced queued Finder batch',
          });
          await Promise.resolve();
          await Promise.resolve();
          const queued = {
            expanded: row()?.getAttribute('aria-expanded') || '',
            loading: row()?.classList.contains('loading-children') === true,
            pending: fileExplorerPendingExpansions.has(directory),
            childVisible: Boolean(child()),
          };

          const eventSource = clientEventTransportState.source;
          if (!eventSource || typeof eventSource.onerror !== 'function') throw new Error('Finder client-events source is unavailable');
          eventSource.onerror();
          await window.__yolomuxTestWaitFor(() => (
            directCalls === 1
            && Array.from(fileExplorerFsBatchPending.values()).every(pending => pending.item?.repairing !== true)
          ), {
            timeoutMs: 2000,
            description: 'second queued Finder direct fallback',
          });
          await Promise.resolve();
          const requeued = {
            expanded: row()?.getAttribute('aria-expanded') || '',
            loading: row()?.classList.contains('loading-children') === true,
            pending: fileExplorerPendingExpansions.has(directory),
            childVisible: Boolean(child()),
          };
          eventSource.emit('ready', {epoch: 'finder-gate-reconnect', resource_revisions: {}});
          await window.__yolomuxTestWaitFor(
            () => directCalls === 2 && child() && !row()?.classList.contains('loading-children'),
            {timeoutMs: 3000, description: 'queued Finder generation repair'},
          );
          const repaired = {
            expanded: row()?.getAttribute('aria-expanded') || '',
            loading: row()?.classList.contains('loading-children') === true,
            pending: fileExplorerPendingExpansions.has(directory),
            childVisible: Boolean(child()),
          };
          window.fetch = originalFetch;
          done({
            batchCalls,
            directCalls,
            queued,
            requeued,
            repaired,
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        })().catch(error => {
          window.fetch = originalFetch;
          done({
            error: String(error?.stack || error),
            batchCalls,
            directCalls,
            reconnectPending: clientEventTransportState.reconnectPending,
            batchPending: fileExplorerFsBatchPending.size,
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        });
        """
    )
    browser.save_screenshot(str(screenshot_path))

    assert not metrics.get("error"), metrics
    assert metrics["batchCalls"] == 1 and metrics["directCalls"] == 2, metrics
    assert metrics["queued"] == {
        "expanded": "true",
        "loading": True,
        "pending": True,
        "childVisible": False,
    }, metrics
    assert metrics["requeued"] == metrics["queued"], metrics
    assert metrics["repaired"] == {
        "expanded": "true",
        "loading": False,
        "pending": False,
        "childVisible": True,
    }, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics
    assert screenshot_path.stat().st_size > 0


@pytest.mark.browser
def test_b3a_pending_expansion_settles_every_live_finder_surface(browser, tmp_path):
    """Opening the legacy sidebar during a held panel expansion must not strand its matching row."""
    _load_finder(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch;
        (async () => {
          const directory = '/home/test/project';
          const panelRow = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${directory}"]`);
          const sidebarRow = () => document.querySelector(`#fileExplorerTree .file-tree-row[data-path="${directory}"]`);
          const sidebarChild = () => document.querySelector(`#fileExplorerTree .file-tree-children[data-parent="${directory}"]`);
          let releaseBatch = null;
          let heldBatchCount = 0;
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            const body = options.body ? JSON.parse(options.body) : null;
            const ownsExpansion = url.pathname === '/api/fs/batch'
              && body?.requests?.some(request => request.type === 'list' && request.path === directory);
            if (!ownsExpansion) return originalFetch(input, options);
            heldBatchCount += 1;
            return new Promise((resolve, reject) => {
              releaseBatch = () => Promise.resolve(originalFetch(input, options)).then(resolve, reject);
            });
          };
          panelRow().click();
          await window.__yolomuxTestWaitFor(
            () => releaseBatch && panelRow()?.classList.contains('loading-children'),
            {timeoutMs: 2000, description: 'held Finder panel expansion'},
          );
          toggleFileExplorer();
          await window.__yolomuxTestWaitFor(
            () => sidebarRow()?.classList.contains('loading-children'),
            {timeoutMs: 2000, description: 'sidebar row rendered while expansion is pending'},
          );
          const pendingBeforeRelease = {
            path: directory,
            pending: fileExplorerPendingExpansions.has(directory),
            panelLoading: panelRow()?.classList.contains('loading-children') === true,
            sidebarLoading: sidebarRow()?.classList.contains('loading-children') === true,
          };
          releaseBatch();
          await window.__yolomuxTestWaitFor(
            () => !fileExplorerPendingExpansions.has(directory),
            {timeoutMs: 2000, description: 'Finder expansion completion'},
          );
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const result = {
            heldBatchCount,
            pendingBeforeRelease,
            panelAfter: {loading: panelRow()?.classList.contains('loading-children') === true},
            sidebarAfter: {
              loading: sidebarRow()?.classList.contains('loading-children') === true,
              childVisible: Boolean(sidebarChild()),
            },
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          };
          window.fetch = originalFetch;
          done(result);
        })().catch(error => {
          window.fetch = originalFetch;
          done({error: String(error?.stack || error), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        });
        """
    )

    if metrics.get("sidebarAfter", {}).get("loading"):
        browser.save_screenshot("/tmp/yolomux-finder-pending-surface-red.png")
    assert not metrics.get("error"), metrics
    assert metrics["heldBatchCount"] == 1, metrics
    assert metrics["panelAfter"] == {"loading": False}, metrics
    assert metrics["sidebarAfter"] == {"loading": False, "childVisible": True}, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics
    assert_terminal_transition(
        contract_id="finder-expansion-all-surfaces",
        pending_observed=metrics["pendingBeforeRelease"] == {
            "path": FINDER_DIRECTORY,
            "pending": True,
            "panelLoading": True,
            "sidebarLoading": True,
        },
        terminal_observed=(
            metrics["panelAfter"] == {"loading": False}
            and metrics["sidebarAfter"] == {"loading": False, "childVisible": True}
        ),
        evidence=metrics,
    )


@pytest.mark.browser
def test_b4_indexed_label_appears_after_context_index_and_survives_finder_refresh(browser, tmp_path):
    _load_finder(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch;
        (async () => {
          const directory = '/home/test/project';
          const row = () => document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${directory}"]`);
          const badge = () => row()?.querySelector(':scope > .file-tree-git-status')?.textContent.trim() || '';
          const indexedLabel = t('finder.index.indexed');
          const directoryRequests = () => window.__bootFetches.filter(item => (
            (item.path === '/api/fs/batch' && item.body?.requests?.some(request => request.type === 'list' && request.path === '/home/test'))
            || (item.path === '/api/fs/list' && new URLSearchParams(item.search).get('path') === '/home/test')
          )).length;
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            if (url.pathname === '/api/fs/index-status' && url.searchParams.get('root') === directory) {
              window.__bootFetches.push({path: url.pathname, search: url.search, method: options.method || 'GET', body: null});
              return Promise.resolve(new Response(JSON.stringify({root: directory, state: 'ready', ready: true, generation: 1}), {
                status: 200,
                headers: {'Content-Type': 'application/json'},
              }));
            }
            return originalFetch(input, options);
          };

          row().dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32}));
          const indexButton = await window.__yolomuxTestWaitFor(
            () => Array.from(document.querySelectorAll('.file-context-menu button')).find(button => button.textContent.trim() === 'Include in index'),
            {timeoutMs: 2000, description: 'Finder Index context action'},
          );
          indexButton.click();
          await window.__yolomuxTestWaitFor(
            () => row()?.dataset.indexed === 'true' && badge() === indexedLabel,
            {timeoutMs: 2000, description: 'rendered Finder Indexed label'},
          );
          const afterIndex = {indexed: row()?.dataset.indexed || '', badge: badge()};
          const requestsBeforeRefresh = directoryRequests();
          document.querySelector('#panel-__finder__ .file-explorer-refresh-cluster').click();
          await window.__yolomuxTestWaitFor(
            () => directoryRequests() > requestsBeforeRefresh && row()?.dataset.indexed === 'true' && badge() === indexedLabel,
            {timeoutMs: 2000, description: 'Indexed label after Finder refresh'},
          );
          const afterRefresh = {
            indexed: row()?.dataset.indexed || '',
            badge: badge(),
            setting: window.__settingsPayload.settings.file_explorer?.indexed_dirs || [],
            requestsBeforeRefresh,
            requestsAfterRefresh: directoryRequests(),
          };
          window.fetch = originalFetch;
          done({indexedLabel, afterIndex, afterRefresh, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        })().catch(error => {
          window.fetch = originalFetch;
          done({error: String(error?.stack || error), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        });
        """
    )

    assert not metrics.get("error"), metrics
    assert metrics["indexedLabel"] == "indexed", metrics
    assert metrics["afterIndex"] == {"indexed": "true", "badge": metrics["indexedLabel"]}, metrics
    assert metrics["afterRefresh"]["indexed"] == "true" and metrics["afterRefresh"]["badge"] == metrics["indexedLabel"], metrics
    assert FINDER_DIRECTORY in metrics["afterRefresh"]["setting"], metrics
    assert metrics["afterRefresh"]["requestsAfterRefresh"] > metrics["afterRefresh"]["requestsBeforeRefresh"], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def _quiet_fs_changed_volume(monkeypatch, tmp_path, make_tmux_webterm_app, *, unreadable_descendant: bool) -> dict[str, object]:
    root = tmp_path / "quiet-tree"
    root.mkdir()
    stable = root / "stable.txt"
    stable.write_text("unchanged\n", encoding="utf-8")
    restricted = root / "restricted"
    if unreadable_descendant:
        restricted.mkdir()
        (restricted / "hidden.txt").write_text("unchanged and inaccessible\n", encoding="utf-8")
        restricted.chmod(0)
    restricted_mode = stat.S_IMODE(restricted.stat().st_mode) if unreadable_descendant else None

    webapp = make_tmux_webterm_app(())
    subscriber_id = None
    try:
        registered = webapp.update_client_watch_roots({"client_id": "gate-finder", "roots": [str(root)]})
        subscriber_id, subscriber_queue = webapp.client_events.subscribe(channels={"files"}, client_id="gate-finder")
        record = app_module.ClientEventWatcherRecord()
        with webapp.client_watch_service.lock:
            webapp.client_watch_service.event_watcher_record = record
        positive_before = webapp.client_events.snapshot()
        stable.write_text("readable sibling changed\n", encoding="utf-8")
        revision = {
            "epoch": "gate-finder-watchd",
            "revision": 1,
            "watch_generation": 1,
            "roots": [str(root)],
            "root_generations": {str(root): 1},
            "token": "gate-finder-watchd:1",
            "created_at": time.time(),
            "healthy": True,
            "changed_paths": [str(stable)],
        }
        positive_events = webapp.apply_watchd_revision(record, revision)
        positive_delivery = subscriber_queue.get(timeout=5.0)
        positive_after = webapp.client_events.snapshot()
        with webapp.client_watch_service.lock:
            signatures = [webapp.client_watch_service.filesystem_signature]
        rounds = [webapp.apply_watchd_revision(record, revision) for _index in range(QUIET_POLL_CYCLES)]
        with webapp.client_watch_service.lock:
            signatures.append(webapp.client_watch_service.filesystem_signature)
        after = webapp.client_events.snapshot()
    finally:
        if subscriber_id is not None:
            webapp.client_events.unsubscribe(subscriber_id)
        if unreadable_descendant:
            restricted.chmod(0o700)

    def counter(snapshot, field):
        return snapshot.get(field, {}).get("fs_changed", {"events": 0, "bytes": 0})

    positive_published_before = counter(positive_before, "published_by_type")
    positive_published_after = counter(positive_after, "published_by_type")
    positive_delivered_before = counter(positive_before, "delivered_by_type")
    positive_delivered_after = counter(positive_after, "delivered_by_type")
    published_before = counter(positive_after, "published_by_type")
    published_after = counter(after, "published_by_type")
    delivered_before = counter(positive_after, "delivered_by_type")
    delivered_after = counter(after, "delivered_by_type")
    return {
        "registered": registered,
        "positive_events": positive_events,
        "positive_delivery": positive_delivery,
        "positive_published_events": positive_published_after["events"] - positive_published_before["events"],
        "positive_published_bytes": positive_published_after["bytes"] - positive_published_before["bytes"],
        "positive_delivered_events": positive_delivered_after["events"] - positive_delivered_before["events"],
        "positive_delivered_bytes": positive_delivered_after["bytes"] - positive_delivered_before["bytes"],
        "quiet_queue_empty": subscriber_queue.empty(),
        "rounds": rounds,
        "signatures": signatures,
        "published_events": published_after["events"] - published_before["events"],
        "published_bytes": published_after["bytes"] - published_before["bytes"],
        "delivered_events": delivered_after["events"] - delivered_before["events"],
        "delivered_bytes": delivered_after["bytes"] - delivered_before["bytes"],
        "restricted_mode": restricted_mode,
    }


def _assert_quiet_fs_changed_volume(metrics: dict[str, object]) -> None:
    assert metrics["registered"]["roots"], metrics
    assert metrics["positive_events"] in ([], ["fs_changed"]), metrics
    assert metrics["positive_delivery"] and metrics["positive_delivery"]["type"] == "fs_changed", metrics
    assert metrics["positive_published_events"] == metrics["positive_delivered_events"] == 1, metrics
    assert metrics["positive_published_bytes"] > 0 and metrics["positive_delivered_bytes"] > 0, metrics
    assert metrics["quiet_queue_empty"] is True, metrics
    assert len(metrics["rounds"]) == QUIET_POLL_CYCLES, metrics
    assert all("fs_changed" not in events for events in metrics["rounds"]), metrics
    assert metrics["signatures"][0] == metrics["signatures"][1], metrics
    assert metrics["published_events"] == 0 and metrics["published_bytes"] == 0, metrics
    assert metrics["delivered_events"] == 0 and metrics["delivered_bytes"] == 0, metrics


@pytest.mark.browser
def test_b5_unchanged_readable_tree_emits_zero_fs_changed_events_and_bytes(
    monkeypatch, tmp_path, gate_runtime_paths, make_tmux_webterm_app
):
    """Twenty-five unchanged production polls are a deterministic quiet SSE window."""
    metrics = _quiet_fs_changed_volume(monkeypatch, tmp_path, make_tmux_webterm_app, unreadable_descendant=False)
    _assert_quiet_fs_changed_volume(metrics)


@pytest.mark.browser
def test_b5_unreadable_descendant_keeps_quiet_fs_changed_volume_at_zero(
    monkeypatch, tmp_path, gate_runtime_paths, make_tmux_webterm_app
):
    """A mode-000 descendant cannot turn the same quiet window into polling or SSE churn."""
    metrics = _quiet_fs_changed_volume(monkeypatch, tmp_path, make_tmux_webterm_app, unreadable_descendant=True)
    assert metrics["restricted_mode"] == 0, metrics
    _assert_quiet_fs_changed_volume(metrics)
