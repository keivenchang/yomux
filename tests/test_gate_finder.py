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
from yomux_lib import app as app_module


FINDER_ROOT = "/home/test"
FINDER_DIRECTORY = f"{FINDER_ROOT}/project"
FINDER_CHILD = f"{FINDER_DIRECTORY}/nested.txt"
FINDER_ENTRIES = {
    FINDER_ROOT: [{"name": "project", "kind": "dir"}],
    FINDER_DIRECTORY: [{"name": "nested.txt", "kind": "file"}],
}
QUIET_POLL_CYCLES = 25


def _load_finder(browser, tmp_path) -> None:
    source_bundle = serve_repo_fixture_page("finder-source.js", static_build.build_asset("yomux.js"))
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
def test_b1_five_reexpansions_render_cached_children_before_fast_refresh_settles(browser, tmp_path):
    """Cached children finish rendering while a direct fast refresh remains held."""
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
            item.path === '/api/fs/fast/list' && new URLSearchParams(item.search).get('path') === directory
          )).length;

          row().click();
          await window.__yomuxTestWaitFor(
            () => child() && row()?.getAttribute('aria-expanded') === 'true' && !row()?.classList.contains('loading-children'),
            {timeoutMs: 2000, description: 'cold Finder directory expansion'},
          );
          const coldRequests = directoryRequests();
          row().click();
          if (row()?.getAttribute('aria-expanded') !== 'false' || child()) throw new Error('cold expansion did not collapse synchronously');

          const originalFetch = window.fetch;
          const cycles = [];
          let releaseRefresh;
          window.fetch = async (input, options) => {
            const url = new URL(String(input), location.href);
            if (url.pathname === '/api/fs/fast/list' && url.searchParams.get('path') === directory) {
              await new Promise(resolve => { releaseRefresh = resolve; });
            }
            return originalFetch(input, options);
          };
          try {
            for (let index = 0; index < 5; index += 1) {
              const requestsBefore = directoryRequests();
              const startedAt = performance.now();
              row().click();
              await fileExplorerPendingExpansionOwners.get(directory)?.promise;
              const refreshRequest = fileExplorerFsResourceRecords.get(fileExplorerFsBatchKey('list', directory))?.request;
              if (!releaseRefresh || !refreshRequest) throw new Error(`cycle ${index} has no held fast refresh`);
              cycles.push({
                index,
                elapsedMs: performance.now() - startedAt,
                expanded: row()?.getAttribute('aria-expanded') || '',
                childVisible: Boolean(child()),
                loading: row()?.classList.contains('loading-children') === true,
                requestsBefore,
                requestsAfter: directoryRequests(),
              });
              row().click();
              if (row()?.getAttribute('aria-expanded') !== 'false' || child()) throw new Error(`cycle ${index} did not collapse synchronously`);
              releaseRefresh();
              releaseRefresh = null;
              await refreshRequest;
            }
          } finally {
            releaseRefresh?.();
            window.fetch = originalFetch;
          }
          done({coldRequests, cycles, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        })().catch(error => done({error: String(error?.stack || error), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')}));
        """
    )

    assert not metrics.get("error"), metrics
    assert metrics["coldRequests"] == 1, metrics
    assert len(metrics["cycles"]) == 5, metrics
    assert all(cycle["expanded"] == "true" and cycle["childVisible"] and not cycle["loading"] for cycle in metrics["cycles"]), metrics
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
          await window.__yomuxTestWaitFor(
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
def test_expansion_uses_one_direct_fast_list_and_never_batches_list_work(browser, tmp_path):
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
          let directListCount = 0;
          let batchListCount = 0;
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            const body = options.body ? JSON.parse(options.body) : null;
            if (url.pathname === '/api/fs/fast/list' && url.searchParams.get('path') === directory) directListCount += 1;
            if (url.pathname === '/api/fs/batch'
                && body?.requests?.some(request => request.type === 'list' && request.path === directory)) batchListCount += 1;
            return originalFetch(input, options);
          };

          row().click();
          await window.__yomuxTestWaitFor(
            () => child() && row()?.getAttribute('aria-expanded') === 'true' && !row()?.classList.contains('loading-children'),
            {timeoutMs: 2000, description: 'Finder direct fast-list expansion'},
          );
          window.fetch = originalFetch;
          done({
            directListCount,
            batchListCount,
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
    assert metrics["directListCount"] == 1 and metrics["batchListCount"] == 0, metrics
    assert metrics["childVisible"] is True and metrics["expanded"] == "true", metrics
    assert metrics["pending"] is False and metrics["loading"] is False and metrics["errorText"] == "", metrics
    assert consume_only_expected_js_debug_api_errors(browser, ()) == (), metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_background_directory_demand_retires_files_and_backs_off_deleted_paths(browser, tmp_path):
    _load_finder(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch;
        (async () => {
          const root = '/home/test';
          const directory = '/home/test/project';
          const file = '/home/test/project/nested.txt';
          let mode = 'classify';
          let directoryRequests = 0;
          let fileRequests = 0;
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            const path = url.searchParams.get('path');
            if (url.pathname === '/api/fs/fast/list' && path === directory) {
              directoryRequests += 1;
              if (mode === 'deleted') {
                return Promise.resolve(new Response(JSON.stringify({
                  error: 'path not found',
                  user_message: {key: 'common.pathNotFound', params: {path}, fallback: 'path not found'},
                }), {status: 404, headers: {'Content-Type': 'application/json'}}));
              }
            }
            if (url.pathname === '/api/fs/fast/list' && path === file) fileRequests += 1;
            return originalFetch(input, options);
          };

          fileExplorerExpanded.clear();
          fileExplorerExpanded.add(directory);
          fileExplorerExpanded.add(file);
          await fileExplorerEntriesByWatchedDirectory(root, {fresh: true});
          const classified = {
            fileRequests,
            expanded: Array.from(fileExplorerExpanded).sort(),
          };

          mode = 'deleted';
          directoryRequests = 0;
          fileExplorerExpanded.add(directory);
          await fileExplorerEntriesByWatchedDirectory(root, {fresh: true});
          await fileExplorerEntriesByWatchedDirectory(root, {fresh: true});
          const negative = fileExplorerFsResourceRecords.get(`list\x1f${directory}`);
          window.fetch = originalFetch;
          done({
            classified,
            deleted: {
              directoryRequests,
              expanded: Array.from(fileExplorerExpanded).sort(),
              failureStatus: Number(negative?.failureStatus || 0),
              retryPending: Number(negative?.retryAt || 0) > Date.now(),
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
    assert metrics["classified"] == {"fileRequests": 0, "expanded": [FINDER_DIRECTORY]}, metrics
    assert metrics["deleted"] == {
        "directoryRequests": 1,
        "expanded": [],
        "failureStatus": 404,
        "retryPending": True,
    }, metrics
    expected_api_errors = consume_only_expected_js_debug_api_errors(
        browser,
        ({
            "path": "/api/fs/fast/list",
            "method": "GET",
            "query": {"path": FINDER_DIRECTORY},
            "status": 404,
            "ok": False,
        },),
    )
    assert len(expected_api_errors) == 1, metrics
    assert len(metrics["errors"]) == 1 and metrics["rejections"] == [], metrics
    assert_only_expected_browser_warning(
        browser,
        message="fs list failed",
        correlation=f'"{FINDER_DIRECTORY}" 404 "path not found: {FINDER_DIRECTORY}"',
    )


@pytest.mark.browser
def test_deferred_git_info_patches_after_fast_paint_in_bounded_waves(browser, tmp_path):
    entries = [
        {"name": f"repo-{index:02d}", "kind": "dir", "mtime": 1786640000 + index, "repo_info_deferred": True}
        for index in range(18)
    ]
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed", "dir_cache_ms": 5000}},
        fs_entries={FINDER_ROOT: entries},
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return fileExplorerRepoInfoEnrichmentState.pending.size === 0 && fileExplorerRepoInfoEnrichmentState.inFlight.size === 0 && fileExplorerRepoInfoEnrichmentState.frame === null"
        )
    )
    browser.execute_script("setFileExplorerTreeDateMode('date', 'finder')")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return fileExplorerTreeDateModeForView('finder') === 'date' && fileExplorerRepoInfoEnrichmentState.pending.size === 0 && fileExplorerRepoInfoEnrichmentState.inFlight.size === 0 && fileExplorerRepoInfoEnrichmentState.frame === null"
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch;
        const batchSizes = [];
        (async () => {
          const root = '/home/test';
          fileExplorerRepoInfoCache.clear();
          fileExplorerRepoInfoEnrichmentState.resolved.clear();
          invalidateFileExplorerFsCaches();
          window.fetch = async (input, options = {}) => {
            const url = new URL(String(input), location.href);
            if (url.pathname !== '/api/fs/batch') return originalFetch(input, options);
            const requests = JSON.parse(options.body || '{}').requests || [];
            if (!requests.every(request => request.type === 'info')) throw new Error('deferred detail batch contains non-INFO work');
            batchSizes.push(requests.length);
            return new Response(JSON.stringify({responses: requests.map(request => ({
              id: request.id,
              ok: true,
              status: 200,
              payload: {
                path: request.path,
                kind: 'dir',
                repo: {root: request.path, name: request.path.split('/').at(-1), branch: `feature-${request.path.split('/').at(-1)}`},
              },
            }))}), {status: 200, headers: {'Content-Type': 'application/json'}});
          };

          await openFileExplorerAt(root, {user: true, manualSelection: true, refreshPanels: false});
          const finderRows = () => [...new Map(
            [...document.querySelectorAll('.file-tree-row[data-path]')]
              .filter(row => row.dataset.path.startsWith(root + '/'))
              .map(row => [row.dataset.path, row])
          ).values()];
          const firstPaint = {
            rows: finderRows().length,
            dates: finderRows().filter(row => !row.querySelector(':scope > .file-tree-date')?.hidden).length,
            repoRows: finderRows().filter(row => row.dataset.isRepo === 'true').length,
            batchSizes: batchSizes.slice(),
          };
          await window.__yomuxTestWaitFor(
            () => batchSizes.reduce((sum, size) => sum + size, 0) === 19
              && fileExplorerRepoInfoEnrichmentState.pending.size === 0
              && fileExplorerRepoInfoEnrichmentState.inFlight.size === 0
              && fileExplorerRepoInfoEnrichmentState.frame === null,
            {timeoutMs: 3000, description: 'bounded deferred Git-info waves'},
          );
          window.fetch = originalFetch;
          done({
            firstPaint,
            batchSizes,
            repoRows: finderRows().filter(row => row.dataset.isRepo === 'true').length,
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        })().catch(error => {
          window.fetch = originalFetch;
          done({
            error: String(error?.stack || error),
            batchSizes,
            repoRows: [...document.querySelectorAll('.file-tree-row[data-is-repo="true"]')].length,
            enrichmentState: {
              pending: fileExplorerRepoInfoEnrichmentState.pending.size,
              inFlight: fileExplorerRepoInfoEnrichmentState.inFlight.size,
              resolved: fileExplorerRepoInfoEnrichmentState.resolved.size,
              framed: fileExplorerRepoInfoEnrichmentState.frame !== null,
            },
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        });
        """
    )

    assert not metrics.get("error"), metrics
    assert metrics["firstPaint"] == {"rows": 18, "dates": 18, "repoRows": 0, "batchSizes": []}, metrics
    assert metrics["batchSizes"] == [8, 8, 3], metrics
    assert metrics["repoRows"] == 18, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_finder_fast_paint_wins_when_session_files_completes_first(browser, tmp_path):
    _load_finder(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch.bind(window);
        const waitFor = window.__yomuxTestWaitFor;
        const jsonResponse = payload => new Response(JSON.stringify(payload), {headers: {'Content-Type': 'application/json'}});
        let releaseFastList = null;
        const requestOrder = [];
        window.fetch = (input, options = {}) => {
          const url = new URL(String(input), location.href);
          if (url.pathname === '/api/fs/fast/list' && url.searchParams.get('path') === '/home/test') {
            requestOrder.push('fast-list-start');
            return new Promise(resolve => { releaseFastList = () => { requestOrder.push('fast-list-end'); resolve(jsonResponse({path: '/home/test', entries: [{name: 'project', kind: 'dir', mtime: 1, is_repo: true, repo_info_deferred: true}]})); }; });
          }
          return originalFetch(input, options);
        };
        (async () => {
          invalidateFileExplorerFsCaches();
          const tree = document.querySelector('#panel-__finder__ .file-explorer-tree-panel');
          tree.replaceChildren();
          activateFileExplorerSurface('__finder__');
          await waitFor(() => requestOrder.includes('fast-list-start'), {timeoutMs: 2000, description: 'held Finder fast listing'});
          // Rich metadata can arrive from the Go server's background owner before the direct list.
          // It is valid data, but it must not delay or decorate the first visible directory rows.
          retireSessionFilesRequest('finder', 'test completion owns Finder payload');
          if (!finderSessionFilesDiffIsDeferred()) throw new Error('the production Finder first-paint gate was not active');
          setSessionFilesPayloadForDestination('finder', {
            session: '1',
            files: [{repo: '/home/test/project', path: 'nested.txt', abs_path: '/home/test/project/nested.txt', status: 'M', added: 5, removed: 2, diff_tracked: true, mtime: 1}],
            repos: [{repo: '/home/test/project', count: 1, added: 5, removed: 2}],
            refs_by_repo: {}, errors: [], warnings: [], from_ref: 'HEAD', to_ref: 'current', loaded: true,
          }, {invalidateRequest: false});
          // Model the completion tail after its payload publication: the finite request no longer
          // owns the Finder destination, but the first-paint gate still does.
          fileExplorerFinderSessionFilesState.abortController = null;
          releaseFastList();
          await waitFor(() => tree.querySelector('.file-tree-row[data-path="/home/test/project"]'), {timeoutMs: 2000, description: 'Finder fast first paint'});
          const firstPaintDiff = tree.querySelector('.file-tree-row[data-path="/home/test/project"]')?.querySelector(':scope > .file-tree-diff')?.textContent.trim() || '';
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const released = releaseFinderSessionFilesDiffBackfillIfReady();
          updateFileTreeGitStatusRows();
          const backfilledDiff = tree.querySelector('.file-tree-row[data-path="/home/test/project"]')?.querySelector(':scope > .file-tree-diff')?.textContent.trim() || '';
          window.fetch = originalFetch;
          done({firstPaintDiff, backfilledDiff, released, pending: fileExplorerFinderSessionFilesState.diffBackfillPending, target: fileExplorerFinderTargetSession(), payload: fileExplorerFinderSessionFilesState.payload, requestOrder, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        })().catch(error => {
          window.fetch = originalFetch;
          done({error: String(error?.stack || error), requestOrder, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        });
        """
    )

    assert not metrics.get("error"), metrics
    assert metrics["firstPaintDiff"] == "", metrics
    assert metrics["pending"] is False, metrics
    assert metrics["backfilledDiff"] == "+5 -2", metrics
    assert metrics["requestOrder"] == ["fast-list-start", "fast-list-end"], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_finder_fast_paint_wins_when_an_existing_session_files_request_completes_first(browser, tmp_path):
    _load_finder(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch.bind(window);
        const waitFor = window.__yomuxTestWaitFor;
        const jsonResponse = payload => new Response(JSON.stringify(payload), {headers: {'Content-Type': 'application/json'}});
        const sessionFilesStreamResponse = value => new Response([
          `event: repo\ndata: ${JSON.stringify({session: value.session, repos: value.repos, files: value.files, refs_by_repo: value.refs_by_repo})}\n\n`,
          `event: done\ndata: ${JSON.stringify({session: value.session, from_ref: value.from_ref, to_ref: value.to_ref})}\n\n`,
        ].join(''), {headers: {'Content-Type': 'text/event-stream'}});
        let releaseFastList = null;
        const requestOrder = [];
        const payload = {
          session: '1',
          files: [{repo: '/home/test/project', path: 'nested.txt', abs_path: '/home/test/project/nested.txt', status: 'M', added: 5, removed: 2, diff_tracked: true, mtime: 1}],
          repos: [{repo: '/home/test/project', count: 1, added: 5, removed: 2}],
          refs_by_repo: {}, errors: [], warnings: [], from_ref: 'HEAD', to_ref: 'current', loaded: true,
        };
        window.fetch = (input, options = {}) => {
          const url = new URL(String(input), location.href);
          if (url.pathname === '/api/fs/fast/list' && url.searchParams.get('path') === '/home/test') {
            requestOrder.push('fast-list-start');
            return new Promise(resolve => { releaseFastList = () => { requestOrder.push('fast-list-end'); resolve(jsonResponse({path: '/home/test', entries: [{name: 'project', kind: 'dir', mtime: 1, is_repo: true, repo_info_deferred: true}]})); }; });
          }
          if (url.pathname === '/api/session-files' || url.pathname === '/api/session-files-stream') {
            requestOrder.push('session-files-start');
            const response = url.pathname === '/api/session-files-stream' ? sessionFilesStreamResponse(payload) : jsonResponse(payload);
            return Promise.resolve(response).then(response => {
              requestOrder.push('session-files-end');
              return response;
            });
          }
          return originalFetch(input, options);
        };
        (async () => {
          invalidateFileExplorerFsCaches();
          retireSessionFilesRequest('finder', 'test existing request reset');
          fileExplorerFinderSessionFilesState.payload = {session: '', files: [], repos: [], refs_by_repo: {}, errors: [], warnings: [], loaded: false};
          fileExplorerFinderSessionFilesState.signature = '';
          fileExplorerFinderSessionFilesState.loading = false;
          fileExplorerFinderSessionFilesState.diffBackfillPending = false;
          fileExplorerFinderSessionFilesState.fastListingPainted = false;
          const tree = document.querySelector('#panel-__finder__ .file-explorer-tree-panel');
          tree.replaceChildren();
          const existingRequest = fetchSessionFiles({destination: 'finder', session: '1', silent: true, force: true});
          activateFileExplorerSurface('__finder__');
          await waitFor(() => requestOrder.includes('fast-list-start'), {timeoutMs: 2000, description: 'held Finder fast listing'});
          await waitFor(() => requestOrder.includes('session-files-end'), {timeoutMs: 2000, description: 'existing session-files completion'});
          if (tree.querySelector('.file-tree-row[data-path]')) throw new Error('held listing unexpectedly painted a row');
          if (!finderSessionFilesDiffIsDeferred()) throw new Error('Finder did not defer metadata while the listing was held');
          releaseFastList();
          await existingRequest;
          await waitFor(() => tree.querySelector('.file-tree-row[data-path="/home/test/project"]'), {timeoutMs: 2000, description: 'Finder fast first paint'});
          const firstPaintDiff = tree.querySelector('.file-tree-row[data-path="/home/test/project"]')?.querySelector(':scope > .file-tree-diff')?.textContent.trim() || '';
          await waitFor(() => tree.querySelector('.file-tree-row[data-path="/home/test/project"]')?.textContent.includes('+5 -2'), {timeoutMs: 2000, description: 'Finder metadata backfill'});
          window.fetch = originalFetch;
          done({firstPaintDiff, finalDiff: tree.querySelector('.file-tree-row[data-path="/home/test/project"]')?.querySelector(':scope > .file-tree-diff')?.textContent.trim() || '', requestOrder, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        })().catch(error => {
          window.fetch = originalFetch;
          done({error: String(error?.stack || error), requestOrder, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        });
        """
    )

    assert not metrics.get("error"), metrics
    assert metrics["firstPaintDiff"] == "", metrics
    assert metrics["finalDiff"] == "+5 -2", metrics
    assert metrics["requestOrder"] == ["session-files-start", "fast-list-start", "session-files-end", "fast-list-end"], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_finder_fast_listing_paints_before_session_files_backfill_starts(browser, tmp_path):
    _load_finder(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch.bind(window);
        const waitFor = window.__yomuxTestWaitFor;
        const jsonResponse = payload => new Response(JSON.stringify(payload), {headers: {'Content-Type': 'application/json'}});
        const sessionFilesStreamResponse = payload => new Response([
          `event: repo\ndata: ${JSON.stringify({session: payload.session, repos: payload.repos, files: payload.files, refs_by_repo: payload.refs_by_repo})}\n\n`,
          `event: done\ndata: ${JSON.stringify({session: payload.session, from_ref: payload.from_ref, to_ref: payload.to_ref})}\n\n`,
        ].join(''), {headers: {'Content-Type': 'text/event-stream'}});
        let releaseFastList = null;
        let releaseSessionFiles = null;
        const requestOrder = [];
        window.fetch = (input, options = {}) => {
          const url = new URL(String(input), location.href);
          if (url.pathname === '/api/fs/fast/list' && url.searchParams.get('path') === '/home/test') {
            requestOrder.push('fast-list-start');
            return new Promise(resolve => { releaseFastList = () => { requestOrder.push('fast-list-end'); resolve(jsonResponse({path: '/home/test', entries: [{name: 'project', kind: 'dir', mtime: 1, is_repo: true, repo_info_deferred: true}]})); }; });
          }
          if (url.pathname === '/api/fs/batch') {
            const requests = JSON.parse(options.body || '{}').requests || [];
            return Promise.resolve(jsonResponse({responses: requests.map(request => ({
              id: request.id,
              ok: true,
              status: 200,
              payload: {path: request.path, name: 'project', kind: 'dir', realpath: request.path, repo: {root: request.path, name: 'project', branch: 'main', dirty_count: 0, ahead: 0, behind: 0}},
            }))}));
          }
          if (url.pathname === '/api/session-files' || url.pathname === '/api/session-files-stream') {
            requestOrder.push('session-files-start');
            return new Promise(resolve => { releaseSessionFiles = () => { requestOrder.push('session-files-end'); const payload = {session: '1', files: [{repo: '/home/test/project', path: 'nested.txt', abs_path: '/home/test/project/nested.txt', status: 'M', added: 5, removed: 2, mtime: 1}], repos: [{repo: '/home/test/project', count: 1, added: 5, removed: 2}], refs_by_repo: {}, errors: [], warnings: [], from_ref: 'HEAD', to_ref: 'current', loaded: true}; resolve(url.pathname === '/api/session-files-stream' ? sessionFilesStreamResponse(payload) : jsonResponse(payload)); }; });
          }
          return originalFetch(input, options);
        };
        (async () => {
          invalidateFileExplorerFsCaches();
          // Keep stale repository totals in the browser while the next session-files request is
          // pending. The direct listing must still paint without those slower +/- annotations.
          fileExplorerFinderSessionFilesState.payload = {session: 'files', files: [], repos: [{repo: '/home/test/project', count: 1, added: 99, removed: 77}], refs_by_repo: {}, errors: [], warnings: [], loaded: false};
          fileExplorerFinderSessionFilesState.signature = '';
          fileExplorerFinderSessionFilesState.loading = false;
          const tree = document.querySelector('#panel-__finder__ .file-explorer-tree-panel');
          tree.replaceChildren();
          activateFileExplorerSurface('__finder__');
          await waitFor(() => requestOrder.includes('fast-list-start'), {timeoutMs: 2000, description: 'Finder fast listing request'});
          const beforeFastList = {order: requestOrder.slice(), sessionStarted: requestOrder.includes('session-files-start'), rows: tree.querySelectorAll('.file-tree-row[data-path]').length};
          if (beforeFastList.sessionStarted) throw new Error(`session-files started before fast listing completed: ${JSON.stringify(beforeFastList)}`);
          releaseFastList();
          await waitFor(() => tree.querySelector('.file-tree-row[data-path="/home/test/project"]'), {timeoutMs: 2000, description: 'Finder fast listing paint'});
          const fastPaintDiff = tree.querySelector('.file-tree-row[data-path="/home/test/project"]')?.querySelector(':scope > .file-tree-diff')?.textContent.trim() || '';
          await waitFor(() => requestOrder.includes('session-files-start'), {timeoutMs: 2000, description: 'deferred session-files backfill'});
          const pendingBackfillDiff = tree.querySelector('.file-tree-row[data-path="/home/test/project"]')?.querySelector(':scope > .file-tree-diff')?.textContent.trim() || '';
          if (pendingBackfillDiff) throw new Error(`stale +/- appeared before session-files backfill completed: ${pendingBackfillDiff}`);
          releaseSessionFiles();
          await waitFor(() => fileExplorerFinderSessionFilesState.payload.loaded === true, {timeoutMs: 2000, description: 'session-files backfill completion'});
          await waitFor(() => tree.querySelector('.file-tree-row[data-path="/home/test/project"]')?.textContent.includes('+5 -2'), {timeoutMs: 2000, description: 'Finder session-files diff backfill'});
          window.fetch = originalFetch;
          done({beforeFastList, fastPaintDiff, order: requestOrder, rows: tree.querySelectorAll('.file-tree-row[data-path]').length, diffText: tree.querySelector('.file-tree-row[data-path="/home/test/project"]')?.textContent.trim() || '', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        })().catch(error => {
          window.fetch = originalFetch;
          done({error: String(error?.stack || error), order: requestOrder, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        });
        """
    )

    assert not metrics.get("error"), metrics
    assert metrics["beforeFastList"] == {"order": ["fast-list-start"], "sessionStarted": False, "rows": 0}, metrics
    assert metrics["fastPaintDiff"] == "", metrics
    assert metrics["order"] == ["fast-list-start", "fast-list-end", "session-files-start", "session-files-end"], metrics
    assert metrics["rows"] == 1, metrics
    assert "+5 -2" in metrics["diffText"], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


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
          let failedFastListCount = 0;
          let batchListCount = 0;
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            const body = options.body ? JSON.parse(options.body) : null;
            if (url.pathname === '/api/fs/batch'
                && body?.requests?.some(request => request.type === 'list' && request.path === directory)) batchListCount += 1;
            if (url.pathname === '/api/fs/fast/list'
                && url.searchParams.get('path') === directory
                && failedFastListCount === 0) {
              failedFastListCount += 1;
              return Promise.reject(new TypeError('Failed to fetch'));
            }
            return originalFetch(input, options);
          };

          row().click();
          await window.__yomuxTestWaitFor(
            () => failedFastListCount === 1 && !fileExplorerPendingExpansions.has(directory),
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
          await window.__yomuxTestWaitFor(
            () => child() && row()?.getAttribute('aria-expanded') === 'true' && !row()?.classList.contains('loading-children'),
            {timeoutMs: 3000, description: 'Finder expansion recovers after network restore'},
          );
          done({
            failedFastListCount,
            batchListCount,
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
    assert metrics["failedFastListCount"] == 1 and metrics["batchListCount"] == 0, metrics
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
        ({
            "path": "/api/fs/fast/list",
            "method": "GET",
            "query": {"path": FINDER_DIRECTORY},
            "error": "Failed to fetch",
        },),
    )
    assert len(expected_api_errors) == metrics["failedFastListCount"], metrics
    assert metrics["errors"] == list(expected_api_errors) and metrics["rejections"] == [], metrics
    assert_only_expected_browser_warning(
        browser,
        message="fs list error",
        correlation='"/home/test/project" TypeError: Failed to fetch',
    )


@pytest.mark.browser
def test_b3_second_click_cancels_a_pending_direct_fast_list_expansion(browser, tmp_path):
    """Hold the real fast LIST response; ``fetchDirectory`` itself is never replaced."""
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
          let releaseList = null;
          let heldListCount = 0;
          let heldRequest = null;
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            const ownsExpansion = url.pathname === '/api/fs/fast/list'
              && url.searchParams.get('path') === directory;
            if (!ownsExpansion) return originalFetch(input, options);
            heldListCount += 1;
            heldRequest = {path: url.pathname, method: String(options.method || 'GET').toUpperCase(), queryPath: url.searchParams.get('path')};
            return new Promise((resolve, reject) => {
              releaseList = () => Promise.resolve(originalFetch(input, options)).then(resolve, reject);
            });
          };

          row().click();
          await window.__yomuxTestWaitFor(
            () => releaseList && fileExplorerPendingExpansions.has(directory) && row()?.classList.contains('loading-children'),
            {timeoutMs: 2000, description: 'pending production Finder fast LIST'},
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
          releaseList();
          await window.__yomuxTestWaitFor(
            () => !fileExplorerPendingExpansions.has(directory),
            {timeoutMs: 2000, description: 'settled cancelled Finder fast LIST'},
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
            heldListCount,
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
    assert metrics["heldListCount"] == 1, metrics
    assert metrics["heldRequest"] == {
        "path": "/api/fs/fast/list",
        "method": "GET",
        "queryPath": FINDER_DIRECTORY,
    }, metrics
    assert metrics["eventSourceCount"] >= 1, metrics
    assert metrics["afterFirst"] == {"expanded": "true", "pending": True, "loading": True}, metrics
    assert metrics["afterSecond"] == {"expanded": "false", "pending": False, "loading": False, "childVisible": False}, metrics
    assert metrics["final"] == {"expanded": "false", "pending": False, "loading": False, "childVisible": False, "expandedSet": False}, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


@pytest.mark.browser
def test_b5_failed_direct_list_clears_spinner_and_renders_typed_error(browser, tmp_path):
    """A direct LIST error must settle the row; a permanent spinner hides the failure."""
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
            if (url.pathname !== '/api/fs/fast/list' || url.searchParams.get('path') !== directory) return originalFetch(input, options);
            return new Response(JSON.stringify({error: 'directory service unavailable'}), {
              status: 503,
              headers: {'Content-Type': 'application/json'},
            });
          };
          row().click();
          await window.__yomuxTestWaitFor(
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
    expected_api_errors = consume_only_expected_js_debug_api_errors(
        browser,
        ({
            "path": "/api/fs/fast/list",
            "method": "GET",
            "query": {"path": FINDER_DIRECTORY},
            "status": 503,
            "ok": False,
        },),
    )
    assert len(expected_api_errors) == 1, metrics
    assert len(metrics["errors"]) == 1 and metrics["errors"][0]["status"] == 503, metrics
    assert metrics["rejections"] == [], metrics
    assert_only_expected_browser_warning(
        browser,
        message="fs list failed",
        correlation='"/home/test/project" 503 "directory service unavailable"',
    )


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
          let releaseList = null;
          let heldListCount = 0;
          window.fetch = (input, options = {}) => {
            const url = new URL(String(input), location.href);
            const ownsExpansion = url.pathname === '/api/fs/fast/list'
              && url.searchParams.get('path') === directory;
            if (!ownsExpansion) return originalFetch(input, options);
            heldListCount += 1;
            return new Promise((resolve, reject) => {
              releaseList = () => Promise.resolve(originalFetch(input, options)).then(resolve, reject);
            });
          };
          panelRow().click();
          await window.__yomuxTestWaitFor(
            () => releaseList && panelRow()?.classList.contains('loading-children'),
            {timeoutMs: 2000, description: 'held Finder panel expansion'},
          );
          toggleFileExplorer();
          await window.__yomuxTestWaitFor(
            () => sidebarRow()?.classList.contains('loading-children'),
            {timeoutMs: 2000, description: 'sidebar row rendered while expansion is pending'},
          );
          const pendingBeforeRelease = {
            path: directory,
            pending: fileExplorerPendingExpansions.has(directory),
            panelLoading: panelRow()?.classList.contains('loading-children') === true,
            sidebarLoading: sidebarRow()?.classList.contains('loading-children') === true,
          };
          releaseList();
          await window.__yomuxTestWaitFor(
            () => !fileExplorerPendingExpansions.has(directory),
            {timeoutMs: 2000, description: 'Finder expansion completion'},
          );
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const result = {
            heldListCount,
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
    assert metrics["heldListCount"] == 1, metrics
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
            item.path === '/api/fs/fast/list' && new URLSearchParams(item.search).get('path') === '/home/test'
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
          const indexButton = await window.__yomuxTestWaitFor(
            () => Array.from(document.querySelectorAll('.file-context-menu button')).find(button => button.textContent.trim() === 'Include in index'),
            {timeoutMs: 2000, description: 'Finder Index context action'},
          );
          indexButton.click();
          await window.__yomuxTestWaitFor(
            () => row()?.dataset.indexed === 'true' && badge() === indexedLabel,
            {timeoutMs: 2000, description: 'rendered Finder Indexed label'},
          );
          const afterIndex = {indexed: row()?.dataset.indexed || '', badge: badge()};
          const requestsBeforeRefresh = directoryRequests();
          document.querySelector('#panel-__finder__ .file-explorer-refresh-cluster').click();
          await window.__yomuxTestWaitFor(
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


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_b5_unchanged_readable_tree_emits_zero_fs_changed_events_and_bytes(
    monkeypatch, tmp_path, gate_runtime_paths, make_tmux_webterm_app
):
    """Twenty-five unchanged production polls are a deterministic quiet SSE window."""
    metrics = _quiet_fs_changed_volume(monkeypatch, tmp_path, make_tmux_webterm_app, unreadable_descendant=False)
    _assert_quiet_fs_changed_volume(metrics)


# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
@pytest.mark.deprecated_python_server
@pytest.mark.browser
def test_b5_unreadable_descendant_keeps_quiet_fs_changed_volume_at_zero(
    monkeypatch, tmp_path, gate_runtime_paths, make_tmux_webterm_app
):
    """A mode-000 descendant cannot turn the same quiet window into polling or SSE churn."""
    metrics = _quiet_fs_changed_volume(monkeypatch, tmp_path, make_tmux_webterm_app, unreadable_descendant=True)
    assert metrics["restricted_mode"] == 0, metrics
    _assert_quiet_fs_changed_volume(metrics)
