import json
import re
from pathlib import Path
from urllib.parse import quote

from selenium.webdriver.common.by import By

from yolomux_lib import filesystem
from yolomux_lib import web

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401

_BOOTSTRAP_SCRIPT_RE = re.compile(r'<script id="yolomux-bootstrap"[^>]*>(.*?)</script>', re.DOTALL)


def test_finder_parent_collapse_converges_all_surfaces_before_reexpand(browser, tmp_path):
    fs_entries = {
        "/home/test": [{"name": "dev", "kind": "dir"}],
        "/home/test/dev": [{"name": "ai-config", "kind": "dir"}],
        "/home/test/dev/ai-config": [{"name": "CLAUDE.md", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@35(slot1,left)&tabs=slot1:files;left:1",
        fs_entries=fs_entries,
    )
    opened = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        openFileExplorerAt('/home/test', {user: true})
          .then(result => done({result}))
          .catch(error => done({error: String(error)}));
        """
    )
    assert opened == {"result": True}, opened
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const rows = liveDirectoryRows('/home/test/dev', null);
            return rows.length === 2 && rows.every(row => row.getAttribute('aria-expanded') === 'false');
            """
        )
    )

    browser.find_element(By.CSS_SELECTOR, '#panel-__finder__ .file-tree-row[data-path="/home/test/dev"]').click()
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const rows = liveDirectoryRows('/home/test/dev', null);
            return rows.length === 2 && rows.every(row => (
              row.getAttribute('aria-expanded') === 'true'
              && childContainerForRow(row, '/home/test/dev') !== null
            ));
            """
        )
    )
    browser.find_element(By.CSS_SELECTOR, '#panel-__finder__ .file-tree-row[data-path="/home/test/dev/ai-config"]').click()
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const rows = liveDirectoryRows('/home/test/dev/ai-config', null);
            return rows.length === 2 && rows.every(row => (
              row.getAttribute('aria-expanded') === 'true'
              && childContainerForRow(row, '/home/test/dev/ai-config') !== null
            ));
            """
        )
    )

    browser.find_element(By.CSS_SELECTOR, '#panel-__finder__ .file-tree-row[data-path="/home/test/dev"]').click()
    # The click's own activation is intentionally deferred by one macrotask (see
    # scheduleFileTreeRowActivation in 40_file_explorer_files.js -- the deferral lets a
    # same-gesture contextmenu cancel it first, a documented, load-bearing behavior this
    # fix does not touch). A bare synchronous read here would race that deferred tick,
    # exactly like the two preceding expand assertions above already wait for their own
    # async settling instead of reading immediately. This waits for the complete
    # collapsed state -- both rows, both stores -- then captures the SAME state the
    # assertion below already expected; no invariant is relaxed, only correctly timed.
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const rows = liveDirectoryRows('/home/test/dev', null);
            return rows.length === 2
              && rows.every(row => (
                row.getAttribute('aria-expanded') === 'false'
                && childContainerForRow(row, '/home/test/dev') === null
              ))
              && !fileExplorerExpanded.has('/home/test/dev')
              && fileExplorerPendingExpansions.size === 0;
            """
        )
    )
    collapsed = browser.execute_script(
        """
        const rows = liveDirectoryRows('/home/test/dev', null);
        return {
          surfaces: rows.map(row => ({
            expanded: row.getAttribute('aria-expanded'),
            hasChildren: childContainerForRow(row, '/home/test/dev') !== null,
          })),
          expandedPaths: Array.from(fileExplorerExpanded).sort(),
          pendingPaths: Array.from(fileExplorerPendingExpansions).sort(),
        };
        """
    )
    assert collapsed == {
        "surfaces": [
            {"expanded": "false", "hasChildren": False},
            {"expanded": "false", "hasChildren": False},
        ],
        "expandedPaths": ["/home/test/dev/ai-config"],
        "pendingPaths": [],
    }

    browser.find_element(By.CSS_SELECTOR, '#panel-__finder__ .file-tree-row[data-path="/home/test/dev"]').click()
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const rows = liveDirectoryRows('/home/test/dev', null);
            return rows.length === 2
              && rows.every(row => (
                row.getAttribute('aria-expanded') === 'true'
                && childContainerForRow(row, '/home/test/dev') !== null
              ))
              && fileExplorerPendingExpansions.size === 0;
            """
        )
    )


def test_finder_64_item_batch_settles_from_operation_terminal_event(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        fs_entries={"/home/test": []},
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const originalFetch = window.fetch;
          const root = '/home/test/workspace/projects/provider/component-with-a-realistic-long-name';
          const paths = Array.from({length: 64}, (_, index) => `${root}/entry-${String(index).padStart(2, '0')}.json`);
          let batchRequests = [];
          window.fetch = async (input, options = {}) => {
            const url = new URL(String(input), location.href);
            if (url.pathname !== '/api/fs/batch') return originalFetch(input, options);
            batchRequests = JSON.parse(options.body || '{}').requests || [];
            return new Response(JSON.stringify({
              state: 'queued',
              request: {id: 'r-fs-batch-64'},
              operation: {
                id: 'op-fs-batch-64',
                kind: 'fs_batch',
                status_url: '/api/operations/op-fs-batch-64',
                events_url: '/api/client-events?operation_id=op-fs-batch-64',
                cursor: {epoch: 'epoch', seq: 0},
                context: {product_key: 'fs-batch:64'},
              },
            }), {status: 202, headers: {'Content-Type': 'application/json'}});
          };
          try {
            const pending = paths.map(path => fetchFilePathInfo(path, {fresh: true, trigger: 'tree-render'}));
            await flushFileExplorerFsBatch();
            if (batchRequests.length !== 64) throw new Error(`expected one 64-item batch, got ${batchRequests.length}`);
            handleClientPushEventNow('operation_terminal', {
              operation: {id: 'op-fs-batch-64', cursor: {epoch: 'epoch', seq: 1}},
              result: {
                state: 'ready',
                request: {id: 'r-fs-batch-64'},
                data: {
                  responses: batchRequests.map(request => ({
                    id: request.id,
                    ok: true,
                    status: 200,
                    payload: {path: request.path, name: request.path.split('/').pop(), kind: 'file'},
                  })),
                },
                quality: {complete: true, stale: false},
                warnings: [],
              },
            });
            const results = await Promise.all(pending);
            done({
              batchSize: batchRequests.length,
              ids: batchRequests.map(request => request.id),
              resultPaths: results.map(result => result.path),
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } finally {
            window.fetch = originalFetch;
          }
        })().catch(error => done({error: String(error?.stack || error), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')}));
        """
    )

    assert not metrics.get("error"), metrics
    assert metrics["batchSize"] == 64
    assert len(set(metrics["ids"])) == 64
    assert len(metrics["resultPaths"]) == 64
    assert metrics["errors"] == []
    assert metrics["rejections"] == []


def test_live_bundle_batches_at_the_bound_a_real_boot_page_states(browser, tmp_path):
    """The size the flush splits at must be the bound a REAL rendered page states.

    `fileExplorerFsBatchRequestLimit` is what /api/fs/batch posts are chunked at, and it fails
    closed to one item when the boot payload states no bound — so a page that does not deliver
    `filesystemBatchLimits`, or a bundle that reads it before `bootstrap` exists, turns every
    Finder listing into one HTTP request per entry while every payload-level assertion stays
    green. Take the bound from the production `html_page` render (parsed, not grepped) and
    require the const a live bundle actually batches with to equal it.
    """
    rendered = web.html_page(["1"], "admin")
    stated = json.loads(_BOOTSTRAP_SCRIPT_RE.search(rendered).group(1)).get("filesystemBatchLimits")
    assert stated == {
        "maxRequests": filesystem.MAX_BATCH_REQUESTS,
        "triggerCountLimit": filesystem.BATCH_TRIGGER_COUNT_LIMIT,
    }, stated

    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        fs_entries={"/home/test": []},
    )
    live = browser.execute_script(
        """
        return {
          maxRequests: fileExplorerFsBatchRequestLimit,
          triggerCountLimit: fileExplorerFsBatchTriggerCountLimit,
          bootstrapType: typeof bootstrap,
          payload: (typeof bootstrap === 'object' && bootstrap && bootstrap.filesystemBatchLimits) || null,
        };
        """
    )
    assert live["bootstrapType"] == "object", live
    assert live["payload"] == stated, live
    assert {"maxRequests": live["maxRequests"], "triggerCountLimit": live["triggerCountLimit"]} == stated, live


def test_file_tree_disclosure_chevron_scales_and_rotates_from_row_font(browser, tmp_path):
    page = tmp_path / "disclosure-size.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(
            """
            <div id="row" class="file-tree-row kind-dir" aria-expanded="false">
              <span id="icon" class="file-tree-icon ui-disclosure-triangle" data-disclosure-expanded="false">›</span>
              <span class="file-tree-name">project</span>
            </div>
            """,
            extra_css="body { display:block; padding:12px; } #row { width:240px; }",
        ),
    )
    before = browser.execute_script(
        """
        const row = document.getElementById('row');
        const icon = document.getElementById('icon');
        return {
          rowFont: parseFloat(getComputedStyle(row).fontSize),
          iconFont: parseFloat(getComputedStyle(icon).fontSize),
          iconWidth: icon.getBoundingClientRect().width,
          iconTransform: getComputedStyle(icon).transform,
          iconText: icon.textContent.trim(),
          rowHeight: row.getBoundingClientRect().height,
        };
        """
    )
    browser.execute_script(
        """
        document.getElementById('row').setAttribute('aria-expanded', 'true');
        document.getElementById('icon').dataset.disclosureExpanded = 'true';
        """
    )
    after = browser.execute_script(
        """
        const row = document.getElementById('row');
        const icon = document.getElementById('icon');
        return {
          rowFont: parseFloat(getComputedStyle(row).fontSize),
          iconFont: parseFloat(getComputedStyle(icon).fontSize),
          iconTransform: getComputedStyle(icon).transform,
          iconText: icon.textContent.trim(),
          rowHeight: row.getBoundingClientRect().height,
        };
        """
    )
    metrics = {"before": before, "after": after}
    assert before["iconText"] == "›", metrics
    assert after["iconText"] == "›", metrics
    assert before["iconTransform"] == "none", metrics
    assert after["iconTransform"] != "none", metrics
    assert abs(before["iconFont"] - before["rowFont"]) <= 0.5, metrics
    assert abs(after["iconFont"] - after["rowFont"]) <= 0.5, metrics
    assert before["iconWidth"] >= before["iconFont"], metrics
    assert abs(after["rowHeight"] - before["rowHeight"]) <= 0.5, metrics


def test_file_tree_context_menu_zip_download_is_folder_only(browser, tmp_path):
    fs_entries = {
        "/home/test": [
            {"name": "project", "kind": "dir"},
            {"name": "note.txt", "kind": "file"},
        ],
        "/home/test/project": [{"name": "src", "kind": "dir"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@35(slot1,left)&tabs=slot1:files;left:1",
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.file-tree-row[data-path="/home/test/project"]')
              && document.querySelector('.file-tree-row[data-path="/home/test/note.txt"]');
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        (async () => {
          const rowsForMenu = () => Array.from(document.querySelectorAll('.file-context-menu button')).map(button => ({
            text: button.textContent.trim(),
            disabled: button.disabled,
          }));
          const folderRow = document.querySelector('.file-tree-row[data-path="/home/test/project"]');
          await showFileTreeContextMenu(folderRow, '/home/test/project', {kind: 'dir', name: 'project'}, 32, 32);
          const folderButtons = rowsForMenu();
          closeFileContextMenu();
          const fileRow = document.querySelector('.file-tree-row[data-path="/home/test/note.txt"]');
          await showFileTreeContextMenu(fileRow, '/home/test/note.txt', {kind: 'file', name: 'note.txt'}, 32, 32);
          const fileButtons = rowsForMenu();
          closeFileContextMenu();
          done({folderButtons, fileButtons, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        })().catch(error => done({error: String(error), stack: error?.stack || '', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')}));
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    folder_zip = [button for button in metrics["folderButtons"] if button["text"] == "Zip & download"]
    assert folder_zip == [{"text": "Zip & download", "disabled": False}], metrics
    assert all(button["text"] != "Zip & download" for button in metrics["fileButtons"]), metrics


def test_finder_copy_path_uses_the_visible_pending_root_input(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=files,1&layout=left&tabs=left:files", fs_entries={"/home/test": []})
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('.file-explorer-path-inline') && document.querySelector('.file-explorer-path-copy-panel')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const copied = [];
        Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async text => copied.push(text)}});
        const input = Array.from(document.querySelectorAll('.file-explorer-path-inline')).find(node => node.getClientRects().length > 0);
        input.value = '/home/test/pending-root';
        const button = document.querySelector('.file-explorer-path-copy-panel');
        const before = {aria: button.getAttribute('aria-label'), title: button.getAttribute('title')};
        const deadline = performance.now() + 4000;
        const waitFor = predicate => new Promise((resolve, reject) => {
          const poll = () => {
            if (predicate()) return resolve();
            if (performance.now() >= deadline) return reject(new Error('copy-feedback state did not settle'));
            requestAnimationFrame(poll);
          };
          requestAnimationFrame(poll);
        });
        (async () => {
          button.click();
          await waitFor(() => button.dataset.copyFeedbackActive === 'true' && /copied/i.test(button.getAttribute('aria-label') || ''));
          const first = {aria: button.getAttribute('aria-label'), title: button.getAttribute('title'), checkmark: getComputedStyle(button, '::after').content, active: button.dataset.copyFeedbackActive};
          await waitFor(() => !button.dataset.copyFeedbackActive && button.getAttribute('aria-label') === before.aria && button.getAttribute('title') === before.title);
          button.click();
          await waitFor(() => copied.length === 2 && button.dataset.copyFeedbackActive === 'true' && /copied/i.test(button.getAttribute('aria-label') || ''));
          const repeat = {aria: button.getAttribute('aria-label'), title: button.getAttribute('title'), active: button.dataset.copyFeedbackActive};
          await waitFor(() => !button.dataset.copyFeedbackActive);
          Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async () => { throw new Error('clipboard denied'); }}});
          button.click();
          await waitFor(() => Boolean(document.querySelector('#status .err')));
          done({
            copied,
            before,
            first,
            repeat,
            copyFailure: document.getElementById('status')?.textContent || '',
            copyFailureIsVisible: Boolean(document.querySelector('#status .err')),
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        })().catch(error => done({error: String(error?.stack || error), copied, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')}));
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["copied"] == ["/home/test/pending-root", "/home/test/pending-root"]
    assert "copied" in metrics["first"]["aria"].lower(), metrics
    assert "copied" in metrics["first"]["title"].lower(), metrics
    assert metrics["first"]["active"] == "true", metrics
    assert "✓" in metrics["first"]["checkmark"], metrics
    assert "copied" in metrics["repeat"]["aria"].lower(), metrics
    assert metrics["repeat"]["active"] == "true", metrics
    assert metrics["copyFailureIsVisible"] is True and "copy failed" in metrics["copyFailure"].lower(), metrics


def test_finder_reload_button_uses_the_delegated_force_refresh(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=files,1&layout=left&tabs=left:files", fs_entries={"/home/test": [{"name": "shown", "kind": "file"}]})
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('.file-explorer-refresh-cluster') && document.querySelector('.file-tree-row[data-path=\"/home/test/shown\"]')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const sessionFileFetches = () => window.__bootFetches.filter(item => item.path === '/api/session-files').length;
        const filesystemFetches = () => window.__bootFetches.filter(item => item.path === '/api/fs/batch' || item.path === '/api/fs/list' || item.path === '/api/fs/fast/list').length;
        const before = {sessionFiles: sessionFileFetches(), filesystem: filesystemFetches()};
        document.querySelector('.file-explorer-refresh-cluster').click();
        const deadline = performance.now() + 2000;
        const inspect = () => {
          const after = {sessionFiles: sessionFileFetches(), filesystem: filesystemFetches()};
          if ((after.sessionFiles <= before.sessionFiles || after.filesystem <= before.filesystem) && performance.now() < deadline) {
            requestAnimationFrame(inspect);
            return;
          }
          done({before, after, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        };
        requestAnimationFrame(inspect);
        """
    )
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics
    assert metrics["after"]["sessionFiles"] > metrics["before"]["sessionFiles"], metrics
    assert metrics["after"]["filesystem"] > metrics["before"]["filesystem"], metrics


def test_finder_tree_toolbar_and_disclosure_use_real_events(browser, tmp_path):
    fs_entries = {
        "/home/test": [{"name": "project", "kind": "dir"}, {"name": "note.txt", "kind": "file"}],
        "/home/test/project": [{"name": "nested.txt", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed"}},
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('.file-tree-row[data-path=\"/home/test/project\"]') && document.querySelector('[data-file-explorer-tree-sort]')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const panel = document.querySelector('#panel-__finder__');
        const tree = panel.querySelector('.file-explorer-tree-panel');
        const projectPath = '/home/test/project';
        const row = () => tree.querySelector(`.file-tree-row[data-path="${projectPath}"]`);
        const child = () => tree.querySelector('.file-tree-row[data-path="/home/test/project/nested.txt"]');
        const waitFor = (predicate, next, deadline = performance.now() + 2000) => {
          if (predicate()) return next();
          if (performance.now() >= deadline) return done({error: 'timed out waiting for toolbar action', state: snapshot()});
          requestAnimationFrame(() => waitFor(predicate, next, deadline));
        };
        const snapshot = () => ({
          sort: fileExplorerTreeSortModeForView('finder'),
          date: fileExplorerTreeDateModeForView('finder'),
          mode: fileExplorerRootModeValue(),
          root: currentFileExplorerRoot(),
          expandedPaths: Array.from(fileExplorerExpanded),
          filesystemFetches: window.__bootFetches.filter(item => item.path === '/api/fs/batch' || item.path === '/api/fs/list' || item.path === '/api/fs/fast/list'),
          expanded: row()?.getAttribute('aria-expanded') || '',
          child: Boolean(child()),
          errors: jsDebugFailureEvents('error'),
          rejections: jsDebugFailureEvents('rejection'),
        });
        const sort = panel.querySelector('[data-file-explorer-tree-sort]');
        sort.value = 'newest';
        sort.dispatchEvent(new Event('change', {bubbles: true, cancelable: true}));
        panel.querySelector('[data-file-explorer-tree-dates]').click();
        panel.querySelector('[data-file-tree-expand-collapse-all="expand"]').click();
        waitFor(() => child(), () => {
          const expanded = snapshot();
          panel.querySelector('[data-file-tree-expand-collapse-all="collapse"]').click();
          waitFor(() => !child() && row()?.getAttribute('aria-expanded') === 'false', () => {
            row().click();
            waitFor(() => child() && row()?.getAttribute('aria-expanded') === 'true', () => done({expanded, final: snapshot()}));
          });
        });
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["expanded"]["sort"] == "newest", metrics
    assert metrics["expanded"]["date"] != "none", metrics
    assert metrics["expanded"]["expanded"] == "true" and metrics["expanded"]["child"] is True, metrics
    assert metrics["final"]["expanded"] == "true" and metrics["final"]["child"] is True, metrics
    assert metrics["final"]["errors"] == [] and metrics["final"]["rejections"] == [], metrics


def test_finder_create_actions_use_visible_root_and_reject_invalid_names(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed"}},
        fs_entries={"/home/test": []},
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('#panel-__finder__ [data-file-explorer-new-file]') && document.querySelector('#panel-__finder__ [data-file-explorer-new-folder]')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const panel = document.querySelector('#panel-__finder__');
        const originalFetch = window.fetch;
        const originalPrompt = window.prompt;
        const prompts = ['created.txt', 'created-dir', 'bad/name'];
        const requests = [];
        window.prompt = () => prompts.shift() || '';
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), location.href);
          if (url.pathname === '/api/fs/write' || url.pathname === '/api/fs/mkdir') {
            const body = JSON.parse(options.body || '{}');
            requests.push({path: url.pathname, body});
            const parent = body.path.slice(0, body.path.lastIndexOf('/')) || '/';
            const name = body.path.slice(body.path.lastIndexOf('/') + 1);
            const kind = url.pathname === '/api/fs/mkdir' ? 'dir' : 'file';
            window.__fixtureFsEntries[parent] = [...(window.__fixtureFsEntries[parent] || []), {name, kind}];
            if (kind === 'dir') window.__fixtureFsEntries[body.path] = [];
            return new Response(JSON.stringify({path: body.path}), {headers: {'Content-Type': 'application/json'}});
          }
          if (url.pathname === '/api/fs/raw') return new Response('', {headers: {'Content-Type': 'text/plain'}});
          return originalFetch(input, options);
        };
        const waitFor = (predicate, next, deadline = performance.now() + 2000) => {
          if (predicate()) return next();
          if (performance.now() >= deadline) return done({error: 'timed out waiting for create action', requests, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          requestAnimationFrame(() => waitFor(predicate, next, deadline));
        };
        panel.querySelector('[data-file-explorer-new-file]').click();
        waitFor(() => requests.length === 1, () => {
          panel.querySelector('[data-file-explorer-new-folder]').click();
          waitFor(() => requests.length === 2 && panel.querySelector('.file-tree-row[data-path="/home/test/created.txt"]') && panel.querySelector('.file-tree-row[data-path="/home/test/created-dir"]'), () => {
            panel.querySelector('[data-file-explorer-new-file]').click();
            requestAnimationFrame(() => requestAnimationFrame(() => {
              window.fetch = originalFetch;
              window.prompt = originalPrompt;
              done({requests, root: currentFileExplorerRoot(), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
            }));
          });
        });
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["root"] == "/home/test", metrics
    assert metrics["requests"] == [
        {"path": "/api/fs/write", "body": {"path": "/home/test/created.txt", "content": ""}},
        {"path": "/api/fs/mkdir", "body": {"path": "/home/test/created-dir"}},
    ], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_finder_file_context_menu_uses_the_real_right_click_and_copy_action(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed"}},
        fs_entries={"/home/test": [{"name": "note.txt", "kind": "file"}]},
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('#panel-__finder__ .file-tree-row[data-path=\"/home/test/note.txt\"]')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const copied = [];
        Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async text => copied.push(text)}});
        const row = document.querySelector('#panel-__finder__ .file-tree-row[data-path="/home/test/note.txt"]');
        row.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32}));
        const deadline = performance.now() + 2000;
        const inspect = () => {
          const button = Array.from(document.querySelectorAll('.file-context-menu button')).find(node => /copy full path/i.test(node.textContent));
          if (!button && performance.now() < deadline) {
            requestAnimationFrame(inspect);
            return;
          }
          if (!button) return done({error: 'full-path button missing', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          button.click();
          requestAnimationFrame(() => done({copied, menuOpen: Boolean(document.querySelector('.file-context-menu')), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')}));
        };
        requestAnimationFrame(inspect);
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["copied"] == ["/home/test/note.txt"], metrics
    assert metrics["menuOpen"] is False, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_finder_context_rename_uses_the_real_input_submit_path(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed"}},
        fs_entries={"/home/test": [{"name": "note.txt", "kind": "file"}]},
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('#panel-__finder__ .file-tree-row[data-path=\"/home/test/note.txt\"]')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch;
        const requests = [];
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), location.href);
          if (url.pathname === '/api/fs/rename') {
            const body = JSON.parse(options.body || '{}');
            requests.push(body);
            window.__fixtureFsEntries['/home/test'] = [{name: body.new_name, kind: 'file'}];
            return new Response(JSON.stringify({path: `/home/test/${body.new_name}`, reindex_roots: []}), {headers: {'Content-Type': 'application/json'}});
          }
          return originalFetch(input, options);
        };
        const row = document.querySelector('#panel-__finder__ .file-tree-row[data-path="/home/test/note.txt"]');
        row.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32}));
        const deadline = performance.now() + 2000;
        const inspect = () => {
          const rename = Array.from(document.querySelectorAll('.file-context-menu button')).find(node => /^rename$/i.test(node.textContent.trim()));
          if (!rename && performance.now() < deadline) return requestAnimationFrame(inspect);
          if (!rename) return done({error: 'rename button missing', requests, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          rename.click();
          requestAnimationFrame(() => {
            const input = document.querySelector('#panel-__finder__ .file-tree-rename-input');
            if (!input) return done({error: 'rename input missing', requests, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
            input.value = 'renamed.txt';
            input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}));
            const waitForRename = () => {
              if (requests.length && document.querySelector('#panel-__finder__ .file-tree-row[data-path="/home/test/renamed.txt"]')) {
                window.fetch = originalFetch;
                return done({requests, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
              }
              if (performance.now() >= deadline) return done({error: 'rename did not finish', requests, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
              requestAnimationFrame(waitForRename);
            };
            requestAnimationFrame(waitForRename);
          });
        };
        requestAnimationFrame(inspect);
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["requests"] == [{"path": "/home/test/note.txt", "new_name": "renamed.txt"}], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_finder_context_delete_invalidates_parent_and_removes_the_real_row(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed"}},
        fs_entries={"/home/test": [{"name": "remove-me.txt", "kind": "file"}]},
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('#panel-__finder__ .file-tree-row[data-path=\"/home/test/remove-me.txt\"]')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch;
        const originalConfirm = window.confirm;
        const requests = [];
        window.confirm = () => true;
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), location.href);
          if (url.pathname === '/api/fs/delete') {
            const body = JSON.parse(options.body || '{}');
            requests.push(body);
            window.__fixtureFsEntries['/home/test'] = [];
            return new Response(JSON.stringify({path: body.path}), {headers: {'Content-Type': 'application/json'}});
          }
          return originalFetch(input, options);
        };
        const row = document.querySelector('#panel-__finder__ .file-tree-row[data-path="/home/test/remove-me.txt"]');
        row.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32}));
        const deadline = performance.now() + 2000;
        const inspect = () => {
          const remove = Array.from(document.querySelectorAll('.file-context-menu button')).find(node => /^delete$/i.test(node.textContent.trim()));
          if (!remove && performance.now() < deadline) return requestAnimationFrame(inspect);
          if (!remove) return done({error: 'delete button missing', requests, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          remove.click();
          const waitForDelete = () => {
            if (requests.length && !document.querySelector('#panel-__finder__ .file-tree-row[data-path="/home/test/remove-me.txt"]')) {
              window.fetch = originalFetch;
              window.confirm = originalConfirm;
              return done({requests, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
            }
            if (performance.now() >= deadline) return done({error: 'delete did not refresh the Finder tree', requests, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
            requestAnimationFrame(waitForDelete);
          };
          requestAnimationFrame(waitForDelete);
        };
        requestAnimationFrame(inspect);
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["requests"] == [{"path": "/home/test/remove-me.txt"}], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_finder_keyboard_selection_drag_payload_and_readonly_context_are_real_paths(browser, tmp_path):
    entries = {"/home/test": [{"name": "alpha.txt", "kind": "file"}, {"name": "beta.txt", "kind": "file"}]}
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed"}},
        fs_entries=entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelectorAll('#panel-__finder__ .file-tree-row[data-path]').length >= 2")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const panel = document.querySelector('#panel-__finder__');
        const alpha = panel.querySelector('.file-tree-row[data-path="/home/test/alpha.txt"]');
        const beta = panel.querySelector('.file-tree-row[data-path="/home/test/beta.txt"]');
        alpha.click();
        panel.querySelector('.file-explorer-tree-panel').dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true, cancelable: true}));
        panel.querySelector('.file-explorer-tree-panel').dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowUp', shiftKey: true, bubbles: true, cancelable: true}));
        const dataTransfer = new DataTransfer();
        beta.dispatchEvent(new DragEvent('dragstart', {bubbles: true, cancelable: true, dataTransfer}));
        const selection = Array.from(fileExplorerSelectedPaths).sort();
        const drag = {plain: dataTransfer.getData('text/plain'), custom: JSON.parse(dataTransfer.getData('application/x-yolomux-file') || '{}')};
        done({selection, lead: fileExplorerSelectionLead, drag, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        """
    )
    assert metrics["selection"] == ["/home/test/alpha.txt", "/home/test/beta.txt"], metrics
    assert metrics["lead"] == "/home/test/alpha.txt", metrics
    assert metrics["drag"] == {
        "plain": "/home/test/alpha.txt\n/home/test/beta.txt",
        "custom": {"path": "/home/test/beta.txt", "paths": ["/home/test/alpha.txt", "/home/test/beta.txt"], "kind": "file", "name": "beta.txt"},
    }, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics

    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed"}},
        fs_entries={"/home/test": [{"name": "locked.txt", "kind": "file"}]},
        access_role="readonly",
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('#panel-__finder__ .file-tree-row[data-path=\"/home/test/locked.txt\"]')")
    )
    readonly = browser.execute_async_script(
        """
        const done = arguments[0];
        const row = document.querySelector('#panel-__finder__ .file-tree-row[data-path="/home/test/locked.txt"]');
        row.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32}));
        const deadline = performance.now() + 2000;
        const inspect = () => {
          const buttons = Array.from(document.querySelectorAll('.file-context-menu button')).map(button => ({text: button.textContent.trim(), disabled: button.disabled}));
          if (!buttons.length && performance.now() < deadline) return requestAnimationFrame(inspect);
          done({buttons, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        };
        requestAnimationFrame(inspect);
        """
    )
    disabled = {button["text"]: button["disabled"] for button in readonly["buttons"]}
    assert [button["text"] for button in readonly["buttons"][:3]] == [
        "Edit in new tab",
        "Preview in new tab",
        "ΔShow Diff",
    ], readonly
    assert all(disabled[label] is True for label in ("Edit in new tab", "Preview in new tab", "ΔShow Diff")), readonly
    assert disabled["Rename"] is True and disabled["Delete"] is True and disabled["Download"] is True, readonly
    assert readonly["errors"] == [] and readonly["rejections"] == [], readonly


def test_finder_context_download_actions_use_the_selected_file_or_folder(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed"}},
        fs_entries={
            "/home/test": [{"name": "note.txt", "kind": "file"}, {"name": "project", "kind": "dir"}],
            "/home/test/project": [{"name": "nested.txt", "kind": "file"}],
        },
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('#panel-__finder__ .file-tree-row[data-path=\"/home/test/note.txt\"]') && document.querySelector('#panel-__finder__ .file-tree-row[data-path=\"/home/test/project\"]')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalDownload = triggerFileDownload;
        const originalZip = triggerFolderZipDownload;
        const calls = [];
        triggerFileDownload = path => calls.push({kind: 'file', path});
        triggerFolderZipDownload = path => calls.push({kind: 'folder', path});
        const open = (path, label, next) => {
          const row = document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${path}"]`);
          row.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32}));
          const deadline = performance.now() + 2000;
          const inspect = () => {
            const button = Array.from(document.querySelectorAll('.file-context-menu button')).find(node => node.textContent.trim() === label);
            if (!button && performance.now() < deadline) return requestAnimationFrame(inspect);
            if (!button) return done({error: `${label} missing`, calls, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
            button.click();
            requestAnimationFrame(next);
          };
          requestAnimationFrame(inspect);
        };
        open('/home/test/note.txt', 'Download', () => open('/home/test/project', 'Zip & download', () => {
          triggerFileDownload = originalDownload;
          triggerFolderZipDownload = originalZip;
          done({calls, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        }));
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["calls"] == [{"kind": "file", "path": "/home/test/note.txt"}, {"kind": "folder", "path": "/home/test/project"}], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_finder_context_index_action_and_nonimage_guard_use_real_menu_state(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed"}},
        fs_entries={"/home/test": [{"name": "project", "kind": "dir"}, {"name": "note.txt", "kind": "file"}]},
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('#panel-__finder__ .file-tree-row[data-path=\"/home/test/project\"]') && document.querySelector('#panel-__finder__ .file-tree-row[data-path=\"/home/test/note.txt\"]')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const open = (path, next) => {
          document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${path}"]`).dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32}));
          const deadline = performance.now() + 2000;
          const inspect = () => {
            const buttons = Array.from(document.querySelectorAll('.file-context-menu button'));
            if (!buttons.length && performance.now() < deadline) return requestAnimationFrame(inspect);
            next(buttons);
          };
          requestAnimationFrame(inspect);
        };
        open('/home/test/project', buttons => {
          const include = buttons.find(button => button.textContent.trim() === 'Include in index');
          if (!include) return done({error: 'include action missing', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          include.click();
          requestAnimationFrame(() => open('/home/test/note.txt', fileButtons => done({
            indexed: fileExplorerDirectoryIsIndexed('/home/test/project'),
            fileButtons: fileButtons.map(button => ({text: button.textContent.trim(), disabled: button.disabled})),
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          })));
        });
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["indexed"] is True, metrics
    nonimage = next(button for button in metrics["fileButtons"] if button["text"] == "Copy image")
    assert nonimage["disabled"] is True, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_finder_context_file_actions_share_one_dirty_editor_tab(browser, tmp_path):
    path = "/home/test/note.md"
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed"}},
        fs_entries={"/home/test": [{"name": "note.md", "kind": "file"}]},
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__finder__ .file-tree-row[data-path=\"/home/test/note.md\"]')"
        )
    )
    metrics = browser.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        const originalFetch = window.fetch.bind(window);
        const requests = [];
        const waitFor = window.__yolomuxTestWaitFor;
        const jsonResponse = payload => new Response(JSON.stringify(payload), {headers: {'Content-Type': 'application/json'}});
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), location.href);
          requests.push(url.pathname + url.search);
          if (url.pathname === '/api/fs/batch') {
            const body = JSON.parse(options.body || '{}');
            return jsonResponse({responses: (body.requests || []).map((request, index) => ({
              id: request.id ?? index,
              ok: true,
              status: 200,
              payload: {
                path: request.path,
                name: request.path.split('/').filter(Boolean).pop() || '/',
                kind: request.path === '/home/test' ? 'dir' : 'file',
                realpath: request.path,
                repo_root: '/home/test',
                relative_path: request.path === path ? 'note.md' : '',
              },
            }))});
          }
          if (url.pathname === '/api/fs/read') return jsonResponse({
            path,
            content: '# original\\n',
            size: 11,
            mtime: 1,
            mtime_ns: 1,
            realpath: path,
            file_id: 'dev:1:ino:2',
            git_root: '/home/test',
            git_tracked: true,
            git_history: [{ref: 'HEAD'}],
            git_has_history: true,
          });
          if (url.pathname === '/api/fs/info') return jsonResponse({path, kind: 'file', realpath: path, repo_root: '/home/test', relative_path: 'note.md', diff_capable: true, git_tracked: true, git_history: [{ref: 'HEAD'}, {ref: 'parent'}], git_has_history: true});
          if (url.pathname === '/api/fs/diff') return jsonResponse({
            repo: '/home/test',
            relative_path: 'note.md',
            from_ref: url.searchParams.get('from'),
            to_ref: url.searchParams.get('to'),
            diff: '@@ -1 +1 @@\\n-# original\\n+# dirty\\n',
            original: '# original\\n',
            working: '# dirty\\n',
          });
          return originalFetch(input, options);
        };
        const openMenu = async () => {
          closeFileContextMenu();
          const row = document.querySelector(`#panel-__finder__ .file-tree-row[data-path="${path}"]`);
          row.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32}));
          return waitFor(() => {
            const buttons = Array.from(document.querySelectorAll('.file-context-menu button'));
            const diff = buttons.find(node => node.textContent.trim() === 'ΔShow Diff');
            return buttons.length && diff && !diff.disabled ? buttons : null;
          });
        };
        const choose = async label => {
          const buttons = await openMenu();
          const button = buttons.find(node => node.textContent.trim() === label);
          if (!button) throw new Error(`${label} action missing: ${buttons.map(node => node.textContent.trim()).join(', ')}`);
          if (button.disabled) throw new Error(`${label} action unexpectedly disabled: ${button.title}`);
          button.click();
        };
        (async () => {
          invalidateFileExplorerFsCaches();
          const initialButtons = await openMenu();
          const initialOrder = initialButtons.slice(0, 3).map(button => button.textContent.trim());
          closeFileContextMenu();

          await choose('Edit in new tab');
          const item = fileEditorItemFor(path);
          const panel = await waitFor(() => {
            const candidate = panelNodes.get(item);
            return fileState.has(path) && editorViewModeFor(path, item) === 'edit' && candidate?._cmView ? candidate : null;
          });
          panel._cmView.dispatch({
            changes: {from: 0, to: panel._cmView.state.doc.length, insert: '# dirty and unsaved\\n'},
          });
          await waitFor(() => fileStateFor(path)?.dirty === true);

          await choose('Preview in new tab');
          await waitFor(() => editorViewModeFor(path, item) === 'preview');
          const afterPreview = {
            item: activeItemForSide(slotForItem(item)),
            content: fileStateFor(path)?.content,
            dirty: fileStateFor(path)?.dirty === true,
            mode: editorViewModeFor(path, item),
            tabLabel: document.querySelector('[data-pane-tab="' + CSS.escape(item) + '"] .session-button-dir')?.textContent || '',
          };

          Object.assign(fileStateFor(path), {
            diffLoaded: true,
            diffUnavailable: false,
            diff: 'stale arbitrary refs',
            diffFromRef: 'old-from',
            diffToRef: 'old-to',
            diffPinnedFromRef: 'old-from',
            diffPinnedToRef: 'old-to',
          });
          await choose('ΔShow Diff');
          await waitFor(() => editorViewModeFor(path, item) === 'diff' && fileStateFor(path)?.diffLoaded === true);
          const finalState = fileStateFor(path);
          done({
            initialOrder,
            expectedItem: item,
            afterPreview,
            openItems: openFileEditorItems(),
            panelItems: filePanelItemsForPath(path),
            content: finalState?.content,
            dirty: finalState?.dirty === true,
            mode: editorViewModeFor(path, item),
            diffFrom: finalState?.diffFromRef,
            diffTo: finalState?.diffToRef,
            tabLabel: document.querySelector('[data-pane-tab="' + CSS.escape(item) + '"] .session-button-dir')?.textContent || '',
            diffRequests: requests.filter(request => request.startsWith('/api/fs/diff')),
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        })().catch(error => done({error: String(error?.stack || error), requests, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')})).finally(() => {
          window.fetch = originalFetch;
        });
        """,
        path,
    )
    assert not metrics.get("error"), metrics
    assert metrics["initialOrder"] == ["Edit in new tab", "Preview in new tab", "ΔShow Diff"], metrics
    assert metrics["expectedItem"] == f"file:{path}", metrics
    assert metrics["afterPreview"] == {
        "item": f"file:{path}",
        "content": "# dirty and unsaved\n",
        "dirty": True,
        "mode": "preview",
        "tabLabel": "note.md",
    }, metrics
    assert metrics["openItems"] == [f"file:{path}"], metrics
    assert metrics["panelItems"] == [f"file:{path}"], metrics
    assert metrics["content"] == "# dirty and unsaved\n" and metrics["dirty"] is True, metrics
    assert metrics["mode"] == "diff" and metrics["diffFrom"] == "HEAD" and metrics["diffTo"] == "current", metrics
    assert metrics["tabLabel"] == "Δnote.md;test", metrics
    assert metrics["diffRequests"] == [f"/api/fs/diff?path=%2Fhome%2Ftest%2Fnote.md&from=HEAD&to=current"], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_finder_repo_context_menu_and_diff_tab_are_immediate_while_metadata_is_deferred(browser, tmp_path):
    repo = "/home/test/notes"
    sha = "a" * 40
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@50(left,right)&tabs=left:files;right:1",
        settings={"file_explorer": {"root_mode": "fixed"}},
        fs_entries={"/home/test": [{"name": "notes", "kind": "dir", "is_repo": True}], repo: []},
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('#panel-__finder__ .file-tree-row[data-path=\"/home/test/notes\"]')")
    )
    metrics = browser.execute_async_script(
        """
        const repo = arguments[0];
        const sha = arguments[1];
        const done = arguments[arguments.length - 1];
        const originalFetch = window.fetch.bind(window);
        const waitFor = window.__yolomuxTestWaitFor;
        const jsonResponse = payload => new Response(JSON.stringify(payload), {headers: {'Content-Type': 'application/json'}});
        const batchResolvers = [];
        const historyResolvers = [];
        const requests = [];
        window.fetch = (input, options = {}) => {
          const url = new URL(String(input), location.href);
          requests.push(url.pathname + url.search);
          if (url.pathname === '/api/fs/batch') return new Promise(resolve => {
            const requests = JSON.parse(options.body || '{}').requests || [];
            const payload = {responses: requests.map((request, index) => ({id: request.id ?? index, ok: true, status: 200, payload: {path: request.path, name: 'notes', kind: 'dir', realpath: request.path, repo_root: repo}}))};
            batchResolvers.push(() => resolve(jsonResponse(payload)));
          });
          if (url.pathname === '/api/fs/git-history') return new Promise(resolve => {
            const payload = {path: repo, repo, relative_path: '', head: sha, snapshot_cursor: 'notes-snapshot', next_cursor: '', truncated: false, commits: [{sha, short: sha.slice(0, 9), parents: [], subject: 'Immediate tab test', author: 'Test', authored_at: 0, files: 1, added: 1, removed: 1, binary_files: 0}]};
            historyResolvers.push(() => resolve(jsonResponse(payload)));
          });
          return originalFetch(input, options);
        };
        (async () => {
          invalidateFileExplorerFsCaches();
          const row = document.querySelector('#panel-__finder__ .file-tree-row[data-path="/home/test/notes"]');
          const event = new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32});
          row.dispatchEvent(event);
          const diffButton = Array.from(document.querySelectorAll('.file-context-menu button')).find(button => button.textContent.trim() === 'ΔShow Diff');
          if (!diffButton || diffButton.disabled) throw new Error('repository Diff action did not paint enabled before path info');
          const infoRequestsBeforeClick = requests.filter(request => request.startsWith('/api/fs/batch')).length;
          diffButton.click();
          const item = gitDiffItemFor(repo);
          const loading = await waitFor(() => panelNodes.get(item)?.querySelector('.git-diff-state-loading[role="status"]') || null);
          if (batchResolvers.length !== 1 || historyResolvers.length !== 1) throw new Error('expected one context-menu metadata refresh and one tab history request');
          batchResolvers.shift()();
          historyResolvers.shift()();
          done({nativeSuppressed: event.defaultPrevented, item, active: itemIsActivePaneTab(item), loadingText: loading.textContent, movingEllipsis: Boolean(loading.querySelector('.moving-ellipsis')), infoRequestsBeforeClick, infoRequestsAfterClick: requests.filter(request => request.startsWith('/api/fs/batch')).length, historyRequests: requests.filter(request => request.startsWith('/api/fs/git-history')).length, requests, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        })().catch(error => done({error: String(error?.stack || error), requests, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')})).finally(() => { window.fetch = originalFetch; });
        """,
        repo,
        sha,
    )
    assert not metrics.get("error"), metrics
    assert metrics["nativeSuppressed"] is True and metrics["item"] == f"gitdiff:{quote(repo, safe='')}", metrics
    assert metrics["active"] is True and metrics["loadingText"] == "loading..." and metrics["movingEllipsis"] is True, metrics
    assert metrics["infoRequestsBeforeClick"] == metrics["infoRequestsAfterClick"] == 1, metrics
    assert metrics["historyRequests"] == 1, metrics
    assert metrics["requests"][-1].startswith("/api/fs/git-history?path=%2Fhome%2Ftest%2Fnotes"), metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_finder_context_diff_repo_eligibility_and_touch_long_press(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
            settings={"file_explorer": {"root_mode": "fixed"}},
            fs_entries={
                "/home/test": [
                    {"name": "repo", "kind": "dir", "is_repo": True},
                    {"name": "plain", "kind": "dir"},
                ],
            "/home/test/repo": [
                {"name": "binary.dat", "kind": "file", "size": 32},
                {"name": "large.txt", "kind": "file", "size": 20971521},
            ],
            "/home/test/plain": [],
        },
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('#panel-__finder__ .file-tree-row[data-path="/home/test/repo"]')
              && document.querySelector('#panel-__finder__ .file-tree-row[data-path="/home/test/plain"]');
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const originalFetch = window.fetch.bind(window);
        const waitFor = window.__yolomuxTestWaitFor;
        const jsonResponse = payload => new Response(JSON.stringify(payload), {headers: {'Content-Type': 'application/json'}});
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), location.href);
          const info = path => { const repoBacked = path === '/home/test/repo' || path.startsWith('/home/test/repo/'), binary = path.endsWith('/binary.dat'), large = path.endsWith('/large.txt'); return {path, name: path.split('/').filter(Boolean).pop() || '/', kind: binary || large ? 'file' : 'dir', realpath: path, ...(repoBacked ? {repo_root: '/home/test/repo', relative_path: path.slice('/home/test/repo'.length).replace(/^\\//, ''), diff_capable: true, git_tracked: true, git_history: [{ref: 'HEAD'}, {ref: 'parent'}], git_has_history: true} : {}), ...(binary ? {size: 32, preview_mime: 'application/octet-stream', diff_capable: false} : {}), ...(large ? {size: 20971521, preview_mime: 'text/plain', diff_capable: false} : {})}; };
          if (url.pathname === '/api/fs/fast/list') { const path = url.searchParams.get('path') || ''; return jsonResponse({path, entries: window.__fixtureFsEntries[path] || []}); }
          if (url.pathname === '/api/fs/info') return jsonResponse(info(url.searchParams.get('path') || ''));
          if (url.pathname === '/api/fs/batch') {
            const body = JSON.parse(options.body || '{}');
            return jsonResponse({responses: (body.requests || []).map((request, index) => {
              const repoBacked = request.path === '/home/test/repo' || request.path.startsWith('/home/test/repo/');
              const binary = request.path.endsWith('/binary.dat');
              const large = request.path.endsWith('/large.txt');
              return {
                id: request.id ?? index,
                ok: true,
                status: 200,
                    payload: info(request.path),
              };
            })});
          }
          return originalFetch(input, options);
        };
        const menuButtons = async path => {
          closeFileContextMenu();
              const row = document.querySelector('#panel-__finder__ .file-tree-row[data-path="' + path + '"]');
              row.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32}));
              const buttons = await waitFor(() => { const values = Array.from(document.querySelectorAll('.file-context-menu button')), diff = values.find(button => button.textContent.trim() === 'ΔShow Diff'); if (!values.length || (path === '/home/test/repo' && fileTreeActionPaths(path).length === 1 && (!diff || diff.disabled)) || (path.startsWith('/home/test/repo/') && (!diff || diff.title === 'Diff not available'))) return null; return values; });
          return buttons;
        };
        const menuLabels = async path => {
          const buttons = await menuButtons(path);
          return buttons.map(button => button.textContent.trim());
        };
        (async () => {
          invalidateFileExplorerFsCaches();
          const eligible = await menuLabels('/home/test/repo');
          const plain = await menuLabels('/home/test/plain');
          document.querySelector('#panel-__finder__ .file-tree-row[data-path="/home/test/repo"]').click();
          document.querySelector('#panel-__finder__ .file-tree-row[data-path="/home/test/repo"]').click();
          await waitFor(() => document.querySelector('#panel-__finder__ .file-tree-row[data-path="/home/test/repo/binary.dat"]'));
          const binary = (await menuButtons('/home/test/repo/binary.dat')).map(button => ({
            label: button.textContent.trim(), disabled: button.disabled, title: button.title,
          }));
          const large = (await menuButtons('/home/test/repo/large.txt')).map(button => ({
            label: button.textContent.trim(), disabled: button.disabled, title: button.title,
          }));

          selectFileTreePath('/home/test/repo');
          selectFileTreePath('/home/test/plain', {clear: false});
          const multiple = await menuLabels('/home/test/repo');

          selectFileTreePath('/home/test/repo');
          closeFileContextMenu();
          const touchRow = document.querySelector('#panel-__finder__ .file-tree-row[data-path="/home/test/repo"]');
          touchRow.dispatchEvent(new PointerEvent('pointerdown', {
            bubbles: true,
            cancelable: true,
            pointerId: 41,
            pointerType: 'touch',
            isPrimary: true,
            button: 0,
            clientX: 36,
            clientY: 36,
          }));
          const touchButtons = await waitFor(() => {
            const values = Array.from(document.querySelectorAll('.file-context-menu button'));
            return values.length ? values : null;
          });
          done({
            eligible,
            plain,
            binary,
            large,
            multiple,
            touch: touchButtons.map(button => button.textContent.trim()),
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        })().catch(error => done({error: String(error?.stack || error), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')})).finally(() => {
          window.fetch = originalFetch;
        });
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["eligible"][0] == "ΔShow Diff", metrics
    assert all(label not in metrics["eligible"] for label in ["Edit in new tab", "Preview in new tab"]), metrics
    assert "ΔShow Diff" not in metrics["plain"], metrics
    assert "ΔShow Diff" not in metrics["multiple"], metrics
    binary_diff = next(button for button in metrics["binary"] if button["label"] == "ΔShow Diff")
    large_diff = next(button for button in metrics["large"] if button["label"] == "ΔShow Diff")
    assert binary_diff["disabled"] is True and "binary" in binary_diff["title"].lower(), metrics
    assert large_diff["disabled"] is True and "large" in large_diff["title"].lower(), metrics
    assert metrics["touch"][0] == "ΔShow Diff", metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_finder_diff_repo_history_opens_ref_pinned_current_editor(browser, tmp_path):
    repo = "/home/test/repo"
    nested = f"{repo}/src"
    sha_a = "a" * 40
    parent_a = "b" * 40
    second_parent = "c" * 40
    sha_b = "d" * 40
    parent_b = "e" * 40
    historical_path = f"{nested}/new.js"
    fr_catalog = json.loads((Path(__file__).resolve().parents[1] / "static/locales/fr.json").read_text(encoding="utf-8"))
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed"}},
        fs_entries={
            "/home/test": [{"name": "repo", "kind": "dir"}],
            repo: [{"name": "src", "kind": "dir"}, {"name": "README.md", "kind": "file"}],
            nested: [{"name": "new.js", "kind": "file"}],
        },
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__finder__ .file-tree-row[data-path=\"/home/test/repo\"]')"
        )
    )
    metrics = browser.execute_async_script(
        """
        const repo = arguments[0];
        const nested = arguments[1];
        const shaA = arguments[2];
        const parentA = arguments[3];
        const secondParent = arguments[4];
        const shaB = arguments[5];
        const parentB = arguments[6];
        const historicalPath = arguments[7];
        const frCatalog = arguments[8];
        const done = arguments[arguments.length - 1];
        const originalFetch = window.fetch.bind(window);
        const waitFor = window.__yolomuxTestWaitFor;
        const requests = [];
        const jsonResponse = payload => new Response(JSON.stringify(payload), {headers: {'Content-Type': 'application/json'}});
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), location.href);
          requests.push(url.pathname + url.search);
          if (url.pathname === '/api/fs/batch') {
            const body = JSON.parse(options.body || '{}');
            return jsonResponse({responses: (body.requests || []).map((request, index) => ({
              id: request.id ?? index,
              ok: true,
              status: 200,
              payload: {
                path: request.path,
                name: request.path.split('/').filter(Boolean).pop() || '/',
                kind: request.path === historicalPath ? 'file' : 'dir',
                realpath: request.path,
                ...(request.path === repo || request.path.startsWith(repo + '/') ? {repo_root: repo} : {}),
              },
            }))});
          }
          if (url.pathname === '/api/fs/git-history') {
            const path = url.searchParams.get('path');
            if (url.searchParams.get('cursor') === 'page-2') return jsonResponse({
              path,
              repo,
              relative_path: path === repo ? '' : 'src',
              head: shaA,
              hosted_remote: {provider: 'github', base_url: 'https://github.com/example/project'},
              snapshot_cursor: 'snapshot-zero',
              next_cursor: '',
              truncated: false,
              commits: [{
                sha: 'f'.repeat(40),
                short: 'f'.repeat(9),
                parents: [shaB],
                subject: 'Oldest loaded page',
                author: 'Older Author',
                authored_at: 1786758840,
                files: 1,
                added: 1,
                removed: 0,
                binary_files: 0,
              }],
            });
            return jsonResponse({
              path,
              repo,
              relative_path: path === repo ? '' : 'src',
              head: shaA,
              hosted_remote: {provider: 'github', base_url: 'https://github.com/example/project'},
              snapshot_cursor: 'snapshot-zero',
              next_cursor: 'page-2',
              truncated: false,
              commits: [
                {
                  sha: shaA,
                  short: shaA.slice(0, 9),
                  parents: [parentA, secondParent],
                  subject: 'Merge exact history #123',
                  author: 'Keiven Chang',
                  authored_at: 1786931640,
                  files: 3,
                  added: 5,
                  removed: 6,
                  binary_files: 1,
                },
                {
                  sha: shaB,
                  short: shaB.slice(0, 9),
                  parents: [parentB],
                  subject: 'Older ordinary change',
                  author: 'Other Author',
                  authored_at: 1786845240,
                  files: 1,
                  added: 2,
                  removed: 1,
                  binary_files: 0,
                },
              ],
            });
          }
          if (url.pathname === '/api/fs/git-commit') {
            const sha = url.searchParams.get('commit');
            if (sha === shaA) return jsonResponse({
              repo,
              scope_path: '',
              sha: shaA,
              parents: [parentA, secondParent],
              from_ref: parentA,
              to_ref: shaA,
              subject: 'Merge exact history',
              message: 'Merge exact history\\n\\nBody text',
              authored_at: 1786931640,
              message_truncated: false,
              files_truncated: false,
              truncated: false,
              files: [
                {status: 'R', path: 'src/new.js', old_path: 'src/old.js', added: 5, removed: 2, binary: false, counts_available: true},
                {status: 'M', path: 'assets/data.bin', old_path: '', added: null, removed: null, binary: true, counts_available: true},
                {status: 'D', path: 'gone.txt', old_path: '', added: 0, removed: 4, binary: false, counts_available: true},
              ],
            });
            return jsonResponse({
              repo,
              scope_path: '',
              sha: shaB,
              parents: [parentB],
              from_ref: parentB,
              to_ref: shaB,
              subject: 'Older ordinary change',
              message: 'Older ordinary change\\n\\nSecond body',
              authored_at: 1786845240,
              message_truncated: false,
              files_truncated: false,
              truncated: false,
              files: [
                {status: 'M', path: 'README.md', old_path: '', added: 2, removed: 1, binary: false, counts_available: true},
              ],
            });
          }
          if (url.pathname === '/api/fs/diff') return jsonResponse({
            repo,
            relative_path: 'src/new.js',
            from_ref: url.searchParams.get('from'),
            to_ref: url.searchParams.get('to'),
            diff: '@@ -1 +1 @@\\n-export const value = 1;\\n+export const value = 2;\\n',
            original: 'export const value = 1;\\n',
            working: 'export const value = 2;\\n',
          });
          if (url.pathname === '/static/locales/fr.json') return jsonResponse(frCatalog);
          return originalFetch(input, options);
        };
        const chooseFinderAction = async (path, label) => {
          closeFileContextMenu();
          const row = document.querySelector('#panel-__finder__ .file-tree-row[data-path="' + path + '"]');
          if (!row) throw new Error('Finder row missing: ' + path);
          row.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32}));
          const button = await waitFor(() => Array.from(document.querySelectorAll('.file-context-menu button')).find(
            candidate => candidate.textContent.trim() === label,
          ));
          if (button.disabled) throw new Error(label + ' disabled: ' + button.title);
          button.click();
        };
        const fieldVisibility = panel => {
          const shown = selector => getComputedStyle(panel.querySelector(selector)).display !== 'none';
          return {
            sha: shown('.git-diff-commit-sha'),
            date: shown('.git-diff-commit-date'),
            changes: shown('.git-diff-commit-changes'),
            author: shown('.git-diff-commit-author'),
            description: shown('.git-diff-commit-description'),
          };
        };
        (async () => {
          invalidateFileExplorerFsCaches();
          document.querySelector('#panel-__finder__ .file-tree-row[data-path="' + repo + '"]').click();
          await waitFor(() => document.querySelector('#panel-__finder__ .file-tree-row[data-path="' + nested + '"]'));

          await chooseFinderAction(repo, 'ΔShow Diff');
          const rootItem = gitDiffItemFor(repo);
          let rootPanel = await waitFor(() => {
            const panel = panelNodes.get(rootItem);
            return panel && panel.querySelectorAll('.git-diff-commit-row').length === 2 ? panel : null;
          });
          const rootRows = Array.from(rootPanel.querySelectorAll('.git-diff-commit-row'));
          const commitOrder = rootRows.map(row => row.dataset.gitDiffCommit);
          const fieldOrder = Array.from(rootRows[0].children).map(node => node.className.split(' ')[0]);
          const shaLink = rootRows[0].querySelector('.git-diff-commit-sha');
          const changeLink = rootRows[0].querySelector('.git-diff-change-link');
          changeLink.addEventListener('click', event => event.preventDefault(), {once: true});
          changeLink.click();
          await new Promise(resolve => requestAnimationFrame(resolve));
          const hostedLinks = {
            sha: shaLink?.href || '',
            change: changeLink?.href || '',
            expandedAfterLink: rootRows[0].getAttribute('aria-expanded'),
          };
          const summaryColors = {
            added: getComputedStyle(rootRows[0].querySelector('.git-diff-commit-added')).color,
            removed: getComputedStyle(rootRows[0].querySelector('.git-diff-commit-removed')).color,
          };
          rootPanel.style.width = '1200px';
          await new Promise(resolve => requestAnimationFrame(resolve));
          const alignedColumnVisibility = fieldVisibility(rootPanel);
          const columnStarts = Object.fromEntries([
            'sha',
            'date',
            'changes',
            'author',
            'description',
          ].map(field => [field, rootRows.map(row => Number(
            row.querySelector('.git-diff-commit-' + field).getBoundingClientRect().left.toFixed(2),
          ))]));
          const rowRects = rootRows.map(row => row.getBoundingClientRect());
          const rowDensity = {
            heights: rowRects.map(rect => Number(rect.height.toFixed(2))),
            gaps: rowRects.slice(1).map((rect, index) => Number(
              (rect.top - rowRects[index].bottom).toFixed(2),
            )),
          };
          rootPanel.style.width = '';
          await new Promise(resolve => requestAnimationFrame(resolve));
          rootRows[0].click();
          rootRows[0].dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true, cancelable: true}));
          await waitFor(() => document.activeElement === rootRows[1]);
          const commitRoving = {
            focused: document.activeElement?.dataset?.gitDiffCommit || '',
            tabStops: rootRows.filter(row => row.tabIndex === 0).map(row => row.dataset.gitDiffCommit),
          };
          rootRows[1].dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true, cancelable: true}));
          await waitFor(() => (
            rootPanel.querySelectorAll('.git-diff-commit-detail').length === 2
            && rootPanel.querySelectorAll('.git-diff-commit-detail .git-diff-file-tree').length === 2
            && Array.from(rootPanel.querySelectorAll('.git-diff-commit-row')).every(row => row.getAttribute('aria-expanded') === 'true')
          ));
          const details = Array.from(rootPanel.querySelectorAll('.git-diff-commit-detail'));
          const firstDetail = details.find(detail => detail.dataset.gitDiffCommitDetail === shaA);
          const secondDetail = details.find(detail => detail.dataset.gitDiffCommitDetail === shaB);
          const focusAfterAsync = document.activeElement?.dataset?.gitDiffCommit || '';
          const firstFileTree = firstDetail.querySelector('.git-diff-file-tree');
          const firstTreeRows = Array.from(firstFileTree.querySelectorAll('.file-tree-row[data-path]'));
          firstTreeRows[0].focus();
          firstTreeRows[0].dispatchEvent(new KeyboardEvent('keydown', {key: 'End', bubbles: true, cancelable: true}));
          await waitFor(() => document.activeElement === firstTreeRows[firstTreeRows.length - 1]);
          const fileRoving = {
            focused: document.activeElement?.dataset?.path || '',
            expected: firstTreeRows[firstTreeRows.length - 1].dataset.path,
            tabStops: firstTreeRows.filter(row => row.tabIndex === 0).map(row => row.dataset.path),
          };
          // Regression: collapsing then re-expanding a directory row inside a git-diff commit's file
          // tree must keep aria-expanded (and the child rows) in sync with the diff viewer's own
          // per-tab collapsedSet, not some unrelated global collapse tracker.
          const dirSelector = '.file-tree-row[data-path="' + nested + '"][data-kind="dir"]';
          const childSelector = '.file-tree-row[data-git-diff-commit-path="' + historicalPath + '"]';
          const dirRowBefore = firstFileTree.querySelector(dirSelector);
          if (!dirRowBefore) throw new Error('diff file-tree dir row missing: ' + nested);
          const dirToggle = {
            expandedBefore: dirRowBefore.getAttribute('aria-expanded'),
            childPresentBefore: firstFileTree.querySelector(childSelector) != null,
          };
          dirRowBefore.click();
          await waitFor(() => firstFileTree.querySelector(dirSelector)?.getAttribute('aria-expanded') === 'false');
          dirToggle.expandedAfterCollapse = firstFileTree.querySelector(dirSelector)?.getAttribute('aria-expanded') || '';
          dirToggle.childPresentAfterCollapse = firstFileTree.querySelector(childSelector) != null;
          firstFileTree.querySelector(dirSelector).click();
          await waitFor(() => firstFileTree.querySelector(childSelector) != null);
          dirToggle.expandedAfterReexpand = firstFileTree.querySelector(dirSelector)?.getAttribute('aria-expanded') || '';
          dirToggle.childPresentAfterReexpand = firstFileTree.querySelector(childSelector) != null;
          const changedRows = Object.fromEntries(Array.from(firstDetail.querySelectorAll('.file-tree-row[data-git-diff-commit-path]')).map(row => [
              row.dataset.gitDiffCommitPath,
              {
                text: row.textContent.replace(/\\s+/g, ' ').trim(),
                name: row.querySelector('.file-tree-name')?.textContent || '',
                status: row.querySelector('.file-tree-git-status')?.textContent || '',
                diff: row.querySelector('.file-tree-diff')?.textContent.replace(/\\s+/g, ' ').trim() || '',
            },
          ]));
          const renameRow = firstDetail.querySelector('.file-tree-row[data-git-diff-commit-path="' + historicalPath + '"]');
          if (!renameRow) throw new Error('renamed historical file row missing: ' + JSON.stringify({
            expected: historicalPath,
            rows: Array.from(firstDetail.querySelectorAll('.file-tree-row')).map(row => ({
              path: row.dataset.gitDiffCommitPath || '',
              kind: row.dataset.kind || '',
              text: row.textContent.replace(/\\s+/g, ' ').trim(),
            })),
          }));
          setFileState(historicalPath, {
            kind: 'text',
            original: 'working tree\\n',
            content: 'working tree dirty\\n',
            dirty: true,
            externalMissing: true,
          });
          const workingItem = fileEditorItemFor(historicalPath);
          addFileEditorTabItem(historicalPath, workingItem);
          applyLayoutSlots(layoutWithItems(layoutSlots, [workingItem], slotForItem(rootItem)), {focusSession: rootItem, prune: false});
          renameRow.click();

          const historicalItem = historicalFileEditorItemFor(historicalPath, parentA, shaA);
          const historicalPanel = await waitFor(() => {
            const panel = panelNodes.get(historicalItem);
            const state = fileEditorStateForItem(historicalPath, historicalItem);
            return panel
              && state?.diffLoaded === true
              && state.content === 'export const value = 2;\\n'
              && editorViewModeFor(historicalPath, historicalItem) === 'diff'
              ? panel
              : null;
          });
          const historicalState = fileEditorStateForItem(historicalPath, historicalItem);
          const editButton = historicalPanel.querySelector('[data-editor-mode="edit"]');
          const previewButton = historicalPanel.querySelector('[data-editor-mode="preview"]');
          const diffButton = historicalPanel.querySelector('.file-editor-diff-panel');
          const historicalTab = document.querySelector('[data-pane-tab="' + CSS.escape(historicalItem) + '"]');
          const historicalBeforePreview = {
            tabType: tabTypeForItem(historicalItem)?.key,
            mode: editorViewModeFor(historicalPath, historicalItem),
            comparison: historicalState.historicalComparisonKind,
            content: historicalState.content,
            from: historicalState.diffFromRef,
            to: historicalState.diffToRef,
            editDisabled: editButton?.disabled === true,
            diffSelected: diffButton?.getAttribute('aria-pressed') === 'true',
            dirtyChrome: historicalTab?.querySelector('.file-tab-dirty') != null,
            missingChrome: historicalTab?.classList.contains('file-missing') === true,
            missingAria: historicalTab?.getAttribute('aria-label')?.includes('Missing') === true,
            tabLabel: historicalTab?.querySelector('.session-button-dir')?.textContent || '',
          };
          previewButton.click();
          await waitFor(() => editorViewModeFor(historicalPath, historicalItem) === 'preview');
          editButton.click();
          const historicalAfterPreview = {
            mode: editorViewModeFor(historicalPath, historicalItem),
            content: fileEditorStateForItem(historicalPath, historicalItem)?.content,
            dirty: fileEditorStateForItem(historicalPath, historicalItem)?.dirty === true,
            previewSelected: previewButton.getAttribute('aria-pressed') === 'true',
            tabLabel: document.querySelector('[data-pane-tab="' + CSS.escape(historicalItem) + '"] .session-button-dir')?.textContent || '',
          };

          await editorNavBack();
          await waitFor(() => activeItemForSide(slotForItem(rootItem)) === rootItem);
          activatePaneTab(slotForItem(historicalItem), historicalItem, {userInitiated: true});
          await waitFor(() => activeItemForSide(slotForItem(historicalItem)) === historicalItem);

          let renamePromptCalls = 0;
          const originalPrompt = window.prompt;
          window.prompt = () => {
            renamePromptCalls += 1;
            return 'renamed.js';
          };
          historicalTab.dispatchEvent(new MouseEvent('dblclick', {bubbles: true, cancelable: true}));
          window.prompt = originalPrompt;
          const minimizeButtons = Array.from(document.querySelectorAll(
            '[data-pane-minimize="' + CSS.escape(historicalItem) + '"]'
          ));
          const minimizeButton = minimizeButtons.find(button => button.offsetParent !== null) || minimizeButtons[0];
          if (!minimizeButton) throw new Error('historical diff pane minimize control missing');
          minimizeButton.click();
          await waitFor(() => activeItemForSide(slotForItem(rootItem)) === rootItem);
          const historicalAfterMinimize = {
            stillInLayout: itemInLayout(historicalItem),
            activeItem: activeItemForSide(slotForItem(rootItem)),
            historicalState: fileEditorStateForItem(historicalPath, historicalItem) != null,
            workingContent: fileStateFor(historicalPath)?.content,
            workingDirty: fileStateFor(historicalPath)?.dirty === true,
          };

          activatePaneTab(slotForItem(historicalItem), historicalItem, {userInitiated: true});
          await waitFor(() => activeItemForSide(slotForItem(historicalItem)) === historicalItem);
          const dismissButton = document.querySelector(
            '[data-pane-tab="' + CSS.escape(historicalItem) + '"] [data-pane-tab-close]'
          );
          if (!dismissButton) throw new Error('historical diff tab dismiss control missing');
          dismissButton.click();
          await waitFor(() => !itemInLayout(historicalItem));
          const historicalAfterClose = {
            renamePromptCalls,
            stillInLayout: itemInLayout(historicalItem),
            activeItem: activeItemForSide(slotForItem(rootItem)),
            historicalState: fileEditorStateForItem(historicalPath, historicalItem) != null,
            workingContent: fileStateFor(historicalPath)?.content,
            workingDirty: fileStateFor(historicalPath)?.dirty === true,
          };

          const rootHistoryBody = rootPanel.querySelector('.git-diff-panel-body');
          if (!rootHistoryBody) throw new Error('root diff history body is missing');
          rootHistoryBody.scrollTop = rootHistoryBody.scrollHeight;
          rootHistoryBody.dispatchEvent(new Event('scroll'));
          await waitFor(() => rootPanel.querySelectorAll('.git-diff-commit-row').length === 3);
          const loadedOrder = Array.from(rootPanel.querySelectorAll('.git-diff-commit-row')).map(row => row.dataset.gitDiffCommit);

          rootPanel.style.width = '900px';
          await new Promise(resolve => requestAnimationFrame(resolve));
          const wideRetention = fieldVisibility(rootPanel);
          rootPanel.style.width = '700px';
          await new Promise(resolve => requestAnimationFrame(resolve));
          const mediumRetention = fieldVisibility(rootPanel);
          rootPanel.style.width = '500px';
          await new Promise(resolve => requestAnimationFrame(resolve));
          const narrowRetention = fieldVisibility(rootPanel);
          rootPanel.style.width = '';

          const darkPaint = getComputedStyle(rootPanel).backgroundColor;
          globalThemeMode = 'light';
          applyGlobalThemeMode({updateEditor: true});
          await new Promise(resolve => requestAnimationFrame(resolve));
          const lightPaint = getComputedStyle(rootPanel).backgroundColor;
          const lightSeparator = getComputedStyle(rootRows[0].closest('.git-diff-commit')).boxShadow;
          const lightSecondaryText = getComputedStyle(rootRows[0].querySelector('.git-diff-commit-date')).color;
          const lightClass = document.body.classList.contains('theme-light');
          globalThemeMode = 'dark';
          applyGlobalThemeMode({updateEditor: true});

          await applyLocale('fr');
          const french = {
            heading: rootPanel.querySelector('.git-diff-heading')?.textContent || '',
            meta: rootPanel.querySelector('.git-diff-meta')?.textContent || '',
          };
          await applyLocale('en');

          const frozenSnapshot = layoutUrlStateSnapshot();
          cleanupGitDiffTab(rootItem);
          applyLayoutUrlStateSeed(frozenSnapshot);
          resolveLayoutItem(rootItem);
          renderGitDiffPanel(rootItem);
          await waitFor(() => {
            const state = gitDiffTabState.get(rootItem);
            return state?.loaded === true && state.details.size === 2;
          });
          const restoredRootState = gitDiffTabState.get(rootItem);
          rootPanel = panelNodes.get(rootItem);
          if (!rootPanel) throw new Error('restored root diff panel is missing');
          const restoredSnapshot = {
            head: restoredRootState.head,
            snapshotCursor: restoredRootState.snapshotCursor,
            focusedSha: restoredRootState.focusedSha,
            expanded: Array.from(restoredRootState.expanded),
            tabStop: rootPanel.querySelector('.git-diff-commit-row[tabindex="0"]')?.dataset?.gitDiffCommit || '',
          };

          await selectSession(fileExplorerItemId, {userInitiated: true});
          const finderRepoRow = await waitFor(() => document.querySelector(
            '#panel-__finder__ .file-tree-row[data-path="' + repo + '"]',
          ));
              if (finderRepoRow.getAttribute('aria-expanded') !== 'true') finderRepoRow.click();
              if (finderRepoRow.getAttribute('aria-expanded') !== 'true') finderRepoRow.click();
          await waitFor(() => document.querySelector('#panel-__finder__ .file-tree-row[data-path="' + nested + '"]'));
          await chooseFinderAction(nested, 'ΔShow Diff');
          const nestedItem = gitDiffItemFor(nested);
          await waitFor(() => {
            const panel = panelNodes.get(nestedItem);
            return panel && panel.querySelectorAll('.git-diff-commit-row').length === 2;
          });
          const tabLabels = {
            root: document.querySelector('[data-pane-tab="' + CSS.escape(rootItem) + '"] .session-button-dir')?.textContent || '',
            nested: document.querySelector('[data-pane-tab="' + CSS.escape(nestedItem) + '"] .session-button-dir')?.textContent || '',
          };
          await chooseFinderAction(nested, 'ΔShow Diff');
          await waitFor(() => activeItemForSide(slotForItem(nestedItem)) === nestedItem);
          const tabs = paneTabs(slotForItem(nestedItem));
          done({
            rootItem,
            nestedItem,
            commitOrder,
            loadedOrder,
            fieldOrder,
            hostedLinks,
            summaryColors,
            alignedColumnVisibility,
            columnStarts,
            rowDensity,
            commitRoving,
            focusAfterAsync,
            fileRoving,
            dirToggle,
            expanded: Array.from(rootPanel.querySelectorAll('.git-diff-commit-row')).map(row => row.getAttribute('aria-expanded')),
            messages: [firstDetail, secondDetail].map(detail => detail.querySelector('.git-diff-commit-message')?.textContent || ''),
            refs: firstDetail.querySelector('.git-diff-commit-refs')?.textContent || '',
            detailRoles: details.map(detail => detail.getAttribute('role')),
            treeRoles: details.map(detail => detail.querySelector('.git-diff-file-tree')?.getAttribute('role')),
            changedRows,
            historicalItem,
            historicalBeforePreview,
            historicalAfterPreview,
            historicalAfterMinimize,
            historicalAfterClose,
            restoredSnapshot,
            backItem: rootItem,
            wideRetention,
            mediumRetention,
            narrowRetention,
            darkPaint,
            lightPaint,
            lightSeparator,
            lightSecondaryText,
            lightClass,
            french,
            tabs,
            tabLabels,
            historyRequests: requests.filter(request => request.startsWith('/api/fs/git-history')),
            detailRequests: requests.filter(request => request.startsWith('/api/fs/git-commit')),
            diffRequests: requests.filter(request => request.startsWith('/api/fs/diff')),
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        })().catch(error => done({error: String(error?.stack || error), requests, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')})).finally(() => {
          window.fetch = originalFetch;
        });
        """,
        repo,
        nested,
        sha_a,
        parent_a,
        second_parent,
        sha_b,
        parent_b,
        historical_path,
        fr_catalog,
    )
    assert not metrics.get("error"), metrics
    root_item = f"gitdiff:{quote(repo, safe='')}"
    nested_item = f"gitdiff:{quote(nested, safe='')}"
    historical_item = "filehistory:" + quote(
        json.dumps([historical_path, parent_a, sha_a], separators=(",", ":")),
        safe="-_.!~*'()",
    )
    assert metrics["rootItem"] == root_item and metrics["nestedItem"] == nested_item, metrics
    assert metrics["commitOrder"] == [sha_a, sha_b], metrics
    assert metrics["loadedOrder"] == [sha_a, sha_b, "f" * 40], metrics
    assert metrics["fieldOrder"] == [
        "git-diff-commit-caret",
        "git-diff-commit-sha",
        "git-diff-commit-date",
        "git-diff-commit-changes",
        "git-diff-commit-author",
        "git-diff-commit-description",
    ], metrics
    assert metrics["hostedLinks"] == {
        "sha": f"https://github.com/example/project/commit/{sha_a}",
        "change": "https://github.com/example/project/pull/123",
        "expandedAfterLink": "false",
    }, metrics
    assert metrics["summaryColors"]["added"] != metrics["summaryColors"]["removed"], metrics
    assert all(metrics["alignedColumnVisibility"].values()), metrics["alignedColumnVisibility"]
    assert all(
        max(starts) - min(starts) <= 0.5 for starts in metrics["columnStarts"].values()
    ), metrics["columnStarts"]
    assert max(metrics["rowDensity"]["heights"]) <= 24.5, metrics["rowDensity"]
    assert max(metrics["rowDensity"]["gaps"], default=0) <= 0.5, metrics["rowDensity"]
    assert metrics["commitRoving"] == {"focused": sha_b, "tabStops": [sha_b]}, metrics
    assert metrics["focusAfterAsync"] == sha_b, metrics
    assert metrics["fileRoving"]["focused"] == metrics["fileRoving"]["expected"], metrics
    assert metrics["fileRoving"]["tabStops"] == [metrics["fileRoving"]["expected"]], metrics
    assert metrics["dirToggle"] == {
        "expandedBefore": "true",
        "childPresentBefore": True,
        "expandedAfterCollapse": "false",
        "childPresentAfterCollapse": False,
        "expandedAfterReexpand": "true",
        "childPresentAfterReexpand": True,
    }, metrics["dirToggle"]
    assert metrics["expanded"] == ["true", "true"], metrics
    assert metrics["messages"] == ["Merge exact history\n\nBody text", "Older ordinary change\n\nSecond body"], metrics
    assert "FROM bbbbbbbbb" in metrics["refs"] and "TO aaaaaaaaa" in metrics["refs"] and "first parent" in metrics["refs"], metrics
    assert metrics["detailRoles"] == ["group", "group"] and metrics["treeRoles"] == ["tree", "tree"], metrics
    assert metrics["changedRows"][historical_path]["name"] == "src/old.js → src/new.js", metrics
    assert metrics["changedRows"][historical_path]["status"] == "R" and metrics["changedRows"][historical_path]["diff"] == "+5 -2", metrics
    assert metrics["changedRows"][f"{repo}/assets/data.bin"]["diff"] == "binary", metrics
    assert metrics["changedRows"][f"{repo}/gone.txt"]["name"] == "gone.txt", metrics
    assert metrics["changedRows"][f"{repo}/gone.txt"]["status"] == "D" and metrics["changedRows"][f"{repo}/gone.txt"]["diff"] == "-4", metrics
    assert metrics["historicalItem"] == historical_item, metrics
    assert metrics["historicalBeforePreview"] == {
        "tabType": "file-editor",
        "mode": "diff",
        "comparison": "merge-first-parent",
        "content": "export const value = 2;\n",
        "from": parent_a,
        "to": sha_a,
        "editDisabled": True,
        "diffSelected": True,
        "dirtyChrome": False,
        "missingChrome": False,
        "missingAria": False,
        "tabLabel": "Δnew.js;repo/src",
    }, metrics
    assert metrics["historicalAfterPreview"] == {
        "mode": "preview",
        "content": "export const value = 2;\n",
        "dirty": False,
        "previewSelected": True,
        "tabLabel": "Δnew.js;repo/src",
    }, metrics
    assert metrics["historicalAfterMinimize"] == {
        "stillInLayout": True,
        "activeItem": root_item,
        "historicalState": True,
        "workingContent": "working tree dirty\n",
        "workingDirty": True,
    }, metrics
    assert metrics["historicalAfterClose"] == {
        "renamePromptCalls": 0,
        "stillInLayout": False,
        "activeItem": root_item,
        "historicalState": False,
        "workingContent": "working tree dirty\n",
        "workingDirty": True,
    }, metrics
    assert metrics["restoredSnapshot"] == {
        "head": sha_a,
        "snapshotCursor": "snapshot-zero",
        "focusedSha": sha_b,
        "expanded": [sha_a, sha_b],
        "tabStop": sha_b,
    }, metrics
    assert metrics["wideRetention"] == {"sha": True, "date": True, "changes": True, "author": False, "description": True}, metrics
    assert metrics["mediumRetention"] == {"sha": True, "date": False, "changes": True, "author": False, "description": True}, metrics
    assert metrics["narrowRetention"] == {"sha": True, "date": False, "changes": False, "author": False, "description": True}, metrics
    assert metrics["darkPaint"] != metrics["lightPaint"] and metrics["lightClass"] is True, metrics
    assert metrics["lightSeparator"] != metrics["lightPaint"], metrics
    assert metrics["lightSecondaryText"] != metrics["lightPaint"], metrics
    assert metrics["french"]["heading"] == "ΔAfficher les différences" and metrics["french"]["meta"].startswith("Périmètre :"), metrics
    assert metrics["tabs"].count(root_item) == 1 and metrics["tabs"].count(nested_item) == 1, metrics
    assert metrics["tabLabels"] == {"root": "Δrepo", "nested": "Δrepo;src"}, metrics
    assert metrics["historyRequests"] == [
        f"/api/fs/git-history?path=%2Fhome%2Ftest%2Frepo&limit=40",
        f"/api/fs/git-history?path=%2Fhome%2Ftest%2Frepo&limit=40&cursor=page-2",
        f"/api/fs/git-history?path=%2Fhome%2Ftest%2Frepo&limit=40&cursor=snapshot-zero",
        f"/api/fs/git-history?path=%2Fhome%2Ftest%2Frepo%2Fsrc&limit=40",
    ], metrics
    assert len(metrics["detailRequests"]) == 4, metrics
    assert metrics["diffRequests"] == [
        f"/api/fs/diff?path=%2Fhome%2Ftest%2Frepo%2Fsrc%2Fnew.js&from={parent_a}&to={sha_a}"
    ], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_finder_context_copy_image_fetches_bytes_and_writes_image_clipboard(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed"}},
        fs_entries={"/home/test": [{"name": "diagram.png", "kind": "file"}]},
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('#panel-__finder__ .file-tree-row[data-path=\"/home/test/diagram.png\"]')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const originalFetch = window.fetch;
        const writes = [];
        class FixtureClipboardItem { constructor(data) { this.data = data; } }
        Object.defineProperty(window, 'ClipboardItem', {configurable: true, value: FixtureClipboardItem});
        Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {write: async items => writes.push(items.map(item => Object.keys(item.data)))} });
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), location.href);
          if (url.pathname === '/api/fs/raw') return new Response(new Blob(['image'], {type: 'image/png'}), {headers: {'Content-Type': 'image/png'}});
          return originalFetch(input, options);
        };
        const row = document.querySelector('#panel-__finder__ .file-tree-row[data-path="/home/test/diagram.png"]');
        row.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32}));
        const deadline = performance.now() + 2000;
        const inspect = () => {
          const button = Array.from(document.querySelectorAll('.file-context-menu button')).find(node => node.textContent.trim() === 'Copy image');
          if (!button && performance.now() < deadline) return requestAnimationFrame(inspect);
          if (!button) return done({error: 'copy-image action missing', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          button.click();
          const waitForWrite = () => {
            if (writes.length) {
              window.fetch = originalFetch;
              return done({writes, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
            }
            if (performance.now() >= deadline) return done({error: 'image clipboard write missing', writes, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
            requestAnimationFrame(waitForWrite);
          };
          requestAnimationFrame(waitForWrite);
        };
        requestAnimationFrame(inspect);
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["writes"] == [[["image/png"]]], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_finder_hidden_button_uses_the_real_click_path(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=left&tabs=left:files",
        fs_entries={"/home/test": [{"name": ".hidden", "kind": "file"}, {"name": "shown", "kind": "file"}]},
    )
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return document.querySelector('.file-explorer-hidden-toggle-panel') && document.querySelector('.file-tree-row[data-path=\"/home/test/shown\"]')"))
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const button = document.querySelector('.file-explorer-hidden-toggle-panel');
        const before = button.getAttribute('aria-pressed');
        button.click();
        const deadline = performance.now() + 2000;
        const inspect = () => {
          const hiddenVisible = Boolean(document.querySelector('.file-tree-row[data-path="/home/test/.hidden"]'));
          if (!hiddenVisible && performance.now() < deadline) {
            requestAnimationFrame(inspect);
            return;
          }
          done({
            before,
            pressed: document.querySelector('.file-explorer-hidden-toggle-panel')?.getAttribute('aria-pressed'),
            hiddenVisible,
            mode: fileExplorerRootModeValue(),
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        };
        requestAnimationFrame(inspect);
        """
    )
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics
    assert metrics["before"] != metrics["pressed"] and metrics["hiddenVisible"] is True and metrics["mode"] == "fixed", metrics


def test_finder_context_relative_copy_uses_visible_root_and_fails_closed(browser, tmp_path):
    root = "/home/test/repo/docs"
    selected = f"{root}/note.md"
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@35(slot1,left)&tabs=slot1:files;left:1",
        fs_entries={
            "/home/test": [{"name": "repo", "kind": "dir"}],
            "/home/test/repo": [{"name": "docs", "kind": "dir"}],
            root: [{"name": "note.md", "kind": "file"}],
        },
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('.file-explorer-path-inline') !== null")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        (async () => {
          const root = '/home/test/repo/docs';
          const selected = `${root}/note.md`;
          const copied = [];
          Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async text => copied.push(text)}});
          const originalFetch = window.fetch;
          let rejectRootInfo = false;
          window.fetch = async (input, options = {}) => {
            const url = new URL(String(input), location.href);
            if (url.pathname === '/api/fs/info') { const path = url.searchParams.get('path') || ''; return rejectRootInfo && path === root ? new Response(JSON.stringify({error: 'root info unavailable'}), {status: 503, headers: {'Content-Type': 'application/json'}}) : new Response(JSON.stringify({path, name: path.split('/').filter(Boolean).pop() || '/', kind: window.__fixtureFsEntries[path] ? 'dir' : 'file', realpath: `/resolved${path}`}), {headers: {'Content-Type': 'application/json'}}); }
            if (url.pathname !== '/api/fs/batch') return originalFetch(input, options);
            const requests = JSON.parse(options.body || '{}').requests || [];
            const responses = requests.map(request => {
              if (request.type === 'list') {
                return {id: request.id, ok: true, status: 200, payload: {path: request.path, entries: window.__fixtureFsEntries[request.path] || []}};
              }
              if (rejectRootInfo && request.path === root) return {id: request.id, ok: false, status: 503, error: 'root info unavailable'};
              return {id: request.id, ok: true, status: 200, payload: {
                path: request.path,
                name: request.path.split('/').filter(Boolean).pop() || '/',
                kind: window.__fixtureFsEntries[request.path] ? 'dir' : 'file',
                realpath: `/resolved${request.path}`,
              }};
            });
            return new Response(JSON.stringify({responses}), {headers: {'Content-Type': 'application/json'}});
          };
          try {
                await openFileExplorerManualRoot(root);
                const row = document.querySelector(`.file-tree-row[data-path="${selected}"]`);
                await showFileTreeContextMenu(row, selected, {kind: 'file', name: 'note.md'}, 32, 32);
                const copyRelative = await window.__yolomuxTestWaitFor(() => {
                  const button = Array.from(document.querySelectorAll('.file-context-menu button')).find(button => /copy relative path/i.test(button.textContent));
                  return button && !button.disabled ? button : null;
                }, {timeoutMs: 5000, description: 'enabled Copy relative path after deferred metadata'});
                const enabled = !copyRelative.disabled;
            copyRelative?.click();
            await new Promise(resolve => requestAnimationFrame(resolve));
            closeFileContextMenu();

            rejectRootInfo = true;
            await showFileTreeContextMenu(row, selected, {kind: 'file', name: 'note.md'}, 32, 32);
            const unavailable = Array.from(document.querySelectorAll('.file-context-menu button')).find(button => /copy relative path/i.test(button.textContent));
            const disabledAfterRootFailure = Boolean(unavailable?.disabled);
            closeFileContextMenu();
            done({enabled, copied, disabledAfterRootFailure, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          } finally {
            window.fetch = originalFetch;
          }
        })().catch(error => done({error: String(error?.stack || error), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')}));
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["enabled"] is True, metrics
    assert metrics["copied"] == ["note.md"], metrics
    assert metrics["disabledAfterRootFailure"] is True, metrics


@pytest.mark.parametrize("legacy_token", ["changes", "__changes__"])
def test_legacy_changes_url_opens_differ(browser, tmp_path, legacy_token):
    load_live_runtime_boot_fixture(browser, tmp_path, f"?layout=left&tabs=left:{legacy_token}")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__differ__')?.dataset.fileExplorerMode === 'diff'"
        )
    )
    metrics = browser.execute_script(
        """
        const panel = document.querySelector('#panel-__differ__');
        const tree = panel.querySelector('.file-explorer-tree-panel');
        const changes = panel.querySelector('.file-explorer-changes-panel');
        const visible = selector => Array.from(panel.querySelectorAll(selector)).filter(node => node.getClientRects().length > 0);
        return {
          errors: jsDebugFailureEvents('error'),
          rejections: jsDebugFailureEvents('rejection'),
          panelConnected: panel?.isConnected === true,
          panelMode: panel?.dataset.fileExplorerMode,
          noModeSwitcher: panel?.querySelector('.file-explorer-mode-switcher') === null,
          treeDisplay: getComputedStyle(tree).display,
          changesDisplay: getComputedStyle(changes).display,
          titleCount: panel.querySelectorAll('.file-explorer-panel-title').length,
          newFileAbsent: panel.querySelector('[data-file-explorer-new-file]') === null,
          visibleRootControls: visible('.file-explorer-root-mode-toggle-panel').length,
          visibleSessionSelects: visible('[data-session-files-session]').length,
          visibleSortSelects: visible('[data-file-explorer-tree-sort]').length,
          visibleDateButtons: visible('[data-file-explorer-tree-dates]').length,
          visibleReloadButtons: visible('[data-session-files-refresh], [data-file-explorer-refresh]').length,
          sessionOptionTexts: Array.from(panel.querySelectorAll('[data-session-files-session] option')).map(option => option.textContent.trim()),
          sessionFilesFetches: window.__bootFetches.filter(item => item.path === '/api/session-files').length,
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["panelConnected"]
    assert metrics["panelMode"] == "diff"
    assert metrics["noModeSwitcher"]
    assert metrics["treeDisplay"] == "none"
    assert metrics["changesDisplay"] != "none"
    assert metrics["titleCount"] == 0
    assert metrics["newFileAbsent"]
    assert metrics["visibleRootControls"] == 0, metrics
    assert metrics["visibleSessionSelects"] == 1, metrics
    assert metrics["visibleSortSelects"] == 1, metrics
    assert metrics["visibleDateButtons"] == 1, metrics
    assert metrics["visibleReloadButtons"] == 1, metrics
    assert all(text != "1 1" for text in metrics["sessionOptionTexts"]), metrics
    assert metrics["sessionFilesFetches"] >= 1


def test_differ_controls_use_real_session_reload_sort_and_date_events(browser, tmp_path):
    payloads = {
        "1": {"session": "1", "loaded": True, "errors": [], "repos": [], "files": []},
        "2": {"session": "2", "loaded": True, "errors": [], "repos": [], "files": []},
    }
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:__changes__", sessions=["1", "2"], session_files_payloads=payloads)
    WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return document.querySelector('#panel-__differ__ [data-session-files-session]')"))
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const panel = document.querySelector('#panel-__differ__');
        const fetches = () => window.__bootFetches.filter(item => item.path === '/api/session-files').length;
        const select = panel.querySelector('[data-session-files-session]');
        const before = fetches();
        select.value = '2';
        select.dispatchEvent(new Event('change', {bubbles: true, cancelable: true}));
        const deadline = performance.now() + 2000;
        const wait = () => {
          if (fetches() <= before && performance.now() < deadline) return requestAnimationFrame(wait);
          const sort = panel.querySelector('[data-file-explorer-tree-sort]');
          sort.value = 'newest';
          sort.dispatchEvent(new Event('change', {bubbles: true, cancelable: true}));
          panel.querySelector('[data-file-explorer-tree-dates]').click();
          panel.querySelector('[data-session-files-refresh]').click();
          requestAnimationFrame(() => requestAnimationFrame(() => done({
            session: fileExplorerSessionFilesTargetSession(),
            fetches: fetches(), before,
            sort: fileExplorerTreeSortModeForView('differ'),
            date: fileExplorerTreeDateModeForView('differ'),
            errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection'),
          })));
        };
        requestAnimationFrame(wait);
        """
    )
    assert metrics["session"] == "2" and metrics["fetches"] > metrics["before"], metrics
    assert metrics["sort"] == "newest" and metrics["date"] != "none", metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_differ_repo_folder_and_global_collapse_controls_use_real_events(browser, tmp_path):
    payload = {
        "session": "1",
        "loaded": True,
        "errors": [],
        "repos": [{"repo": "/repo/app", "count": 2}],
        "files": [
            {"session": "1", "repo": "/repo/app", "path": "src/a.py", "abs_path": "/repo/app/src/a.py", "status": "M", "mtime": 100},
            {"session": "1", "repo": "/repo/app", "path": "docs/b.md", "abs_path": "/repo/app/docs/b.md", "status": "A", "mtime": 101},
        ],
    }
    load_live_runtime_boot_fixture(browser, tmp_path, "?layout=left&tabs=left:__changes__", sessions=["1"], session_files_payload=payload)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("const panel = document.querySelector('#panel-__differ__'); return panel?.querySelector('[data-changes-repo-toggle]') && panel.querySelector('[data-changes-folder-toggle]') && panel.querySelector('[data-file-tree-expand-collapse-all]')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const panel = document.querySelector('#panel-__differ__');
        const click = selector => panel.querySelector(selector).click();
        const repo = '/repo/app';
        const folder = '/repo/app/src';
        click('[data-changes-repo-toggle]');
        const repoCollapsed = changesRepoCollapsed.has(repo);
        click('[data-changes-repo-toggle]');
        const repoExpanded = !changesRepoCollapsed.has(repo);
        click('[data-changes-folder-toggle="/repo/app/src"]');
        const folderCollapsed = changesFolderCollapsed.has(folder);
        click('[data-file-tree-expand-collapse-all="collapse"]');
        const treesCollapsed = changesRepoCollapsed.has(repo) && changesFolderCollapsed.has(folder);
        click('[data-file-tree-expand-collapse-all="expand"]');
        requestAnimationFrame(() => done({
          repoCollapsed, repoExpanded, folderCollapsed, treesCollapsed,
          treesExpanded: !changesRepoCollapsed.size && !changesFolderCollapsed.size,
          errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection'),
        }));
        """
    )
    assert metrics["repoCollapsed"] and metrics["repoExpanded"] and metrics["folderCollapsed"], metrics
    assert metrics["treesCollapsed"] and metrics["treesExpanded"], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_differ_ref_input_change_uses_its_own_repository_context(browser, tmp_path):
    repo = "/repo/app"
    payload = {
        "session": "1",
        "loaded": True,
        "errors": [],
        "refs_by_repo": {repo: [{"ref": "abc123def456", "short": "abc123d", "subject": "older base"}]},
        "repos": [{"repo": repo, "count": 1}],
        "files": [{"session": "1", "repo": repo, "path": "src/a.py", "abs_path": "/repo/app/src/a.py", "status": "M", "mtime": 100}],
    }
    load_live_runtime_boot_fixture(browser, tmp_path, "?layout=left&tabs=left:__changes__", sessions=["1"], session_files_payload=payload)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('#panel-__differ__ [data-diff-ref-controls][data-diff-ref-repo=\"/repo/app\"] [data-diff-ref-from]')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const panel = document.querySelector('#panel-__differ__');
        const input = panel.querySelector('[data-diff-ref-controls][data-diff-ref-repo="/repo/app"] [data-diff-ref-from]');
        const before = window.__bootFetches.filter(item => item.path === '/api/session-files').length;
        input.value = 'abc123def456';
        input.dispatchEvent(new Event('change', {bubbles: true, cancelable: true}));
        const deadline = performance.now() + 2000;
        const inspect = () => {
          const selected = diffRefsByRepo['/repo/app']?.from || '';
          const fetches = window.__bootFetches.filter(item => item.path === '/api/session-files').length;
          if (selected === 'abc123def456' && fetches > before) return done({selected, before, fetches, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          if (performance.now() >= deadline) return done({error: 'repo-scoped ref did not commit', selected, before, fetches, errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          requestAnimationFrame(inspect);
        };
        requestAnimationFrame(inspect);
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["selected"] == "abc123def456" and metrics["fetches"] > metrics["before"], metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_differ_ref_change_preserves_finder_worktree_counts(browser, tmp_path):
    repo = "/repo/app"
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=row@30(left,right)&tabs=left:finder,differ;right:1",
        sessions=["1"],
        session_files_payload={"session": "1", "loaded": True, "errors": [], "repos": [], "files": []},
        grid_width=1300,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=3)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return Boolean(panelNodes.get(finderItemId)?.querySelector('.file-explorer-tree-panel') && panelNodes.get(differItemId))"
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const repo = '/repo/app';
        const finderPanel = panelNodes.get(finderItemId);
        const differPanel = panelNodes.get(differItemId);
        const finderTree = finderPanel.querySelector('.file-explorer-tree-panel');
        const worktreePayload = {
          session: '1', loaded: true, from_ref: 'HEAD', to_ref: 'current', errors: [],
          repos: [{repo, count: 1, added: 2, removed: 1}],
          files: [{session: '1', repo, path: 'a.py', abs_path: `${repo}/a.py`, status: 'M', added: 2, removed: 1}],
        };
        const initialDifferPayload = {
          session: '1', loaded: true, from_ref: 'HEAD', to_ref: 'current', errors: [],
          refs_by_repo: {[repo]: [{ref: 'abc123def456', short: 'abc123d', subject: 'older base'}]},
          repos: [{repo, count: 1, added: 2, removed: 1}],
          files: [{session: '1', repo, path: 'a.py', abs_path: `${repo}/a.py`, status: 'M', added: 2, removed: 1}],
        };
        const repoEntries = [{name: 'app', kind: 'dir', is_repo: true, repo: {root: repo, name: 'app', branch: 'main'}}];
        setSessionFilesPayloadForDestination('finder', worktreePayload);
        setSessionFilesPayloadForDestination('differ', initialDifferPayload);
        renderTreeChildren(finderTree, '/repo', repoEntries);
        activatePaneTab(slotForItem(differItemId), differItemId, {userInitiated: true});
        renderFileExplorerChangesPanel(differPanel, {force: true});
        const finderDiff = () => panelNodes.get(finderItemId)?.querySelector('.file-tree-row[data-path="/repo/app"] > .file-tree-diff')?.textContent.replace(/\\s+/g, ' ').trim() || '';
        const differTotals = () => panelNodes.get(differItemId)?.querySelector('.changes-repo-totals')?.textContent.replace(/\\s+/g, ' ').trim() || '';
        const beforeFinder = finderDiff();
        window.__fixtureSessionFilesPayload = {
          session: '1', loaded: true, from_ref: 'HEAD', to_ref: 'current', errors: [],
          refs_by_repo: initialDifferPayload.refs_by_repo,
          repos: [{repo, from_ref: 'abc123def456', to_ref: 'current', count: 1, added: 200, removed: 100}],
          files: [{session: '1', repo, path: 'a.py', abs_path: `${repo}/a.py`, status: 'A', added: 200, removed: 100}],
        };
        const input = differPanel.querySelector('[data-diff-ref-controls][data-diff-ref-repo="/repo/app"] [data-diff-ref-from]');
        input.value = 'abc123def456';
        input.dispatchEvent(new Event('change', {bubbles: true, cancelable: true}));
        const deadline = performance.now() + 2500;
        let finishing = false;
        const inspect = () => {
          const selected = diffRefsByRepo[repo]?.from || '';
          const differ = differTotals();
          if (!finishing && selected === 'abc123def456' && differ.includes('+200') && differ.includes('-100')) {
            finishing = true;
            activatePaneTab(slotForItem(finderItemId), finderItemId, {userInitiated: true});
            requestAnimationFrame(() => {
              const activeFinderTree = panelNodes.get(finderItemId).querySelector('.file-explorer-tree-panel');
              renderTreeChildren(activeFinderTree, '/repo', repoEntries);
              requestAnimationFrame(() => done({
                beforeFinder,
                afterFinder: finderDiff(),
                differ,
                finderFrom: fileExplorerFinderSessionFilesState.payload.from_ref,
                finderTo: fileExplorerFinderSessionFilesState.payload.to_ref,
                differFrom: fileExplorerSessionFilesState.payload.repos[0]?.from_ref || '',
                errors: jsDebugFailureEvents('error'),
                rejections: jsDebugFailureEvents('rejection'),
              }));
            });
            return;
          }
          if (performance.now() >= deadline) {
            return done({
              error: 'comparison did not settle',
              selected,
              differ,
              finder: finderDiff(),
              payload: fileExplorerSessionFilesState.payload,
              requests: window.__bootFetches.filter(item => item.path === '/api/session-files'),
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          }
          requestAnimationFrame(inspect);
        };
        requestAnimationFrame(() => requestAnimationFrame(inspect));
        """
    )
    assert not metrics.get("error"), json.dumps(metrics, sort_keys=True)
    assert metrics["beforeFinder"] == "+2 -1", metrics
    assert metrics["afterFinder"] == "+2 -1", metrics
    assert "+200" in metrics["differ"] and "-100" in metrics["differ"], metrics
    assert metrics["finderFrom"] == "HEAD" and metrics["finderTo"] == "current", metrics
    assert metrics["differFrom"] == "abc123def456", metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_differ_directory_context_expands_the_target_in_finder(browser, tmp_path):
    payload = {
        "session": "1",
        "loaded": True,
        "errors": [],
        "repos": [{"repo": "/repo/app", "count": 1}],
        "files": [{"session": "1", "repo": "/repo/app", "path": "src/a.py", "abs_path": "/repo/app/src/a.py", "status": "M", "mtime": 100}],
    }
    load_live_runtime_boot_fixture(browser, tmp_path, "?layout=left&tabs=left:__changes__", sessions=["1"], session_files_payload=payload, fs_entries={"/repo/app/src": [{"name": "a.py", "kind": "file"}]})
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('#panel-__differ__ [data-open-change-directory=\"/repo/app/src\"]')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        const row = document.querySelector('#panel-__differ__ [data-open-change-directory="/repo/app/src"]');
        row.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 32, clientY: 32}));
        const deadline = performance.now() + 2000;
        const inspect = () => {
              const button = Array.from(document.querySelectorAll('.file-context-menu button')).find(node => /expand/i.test(node.textContent));
          if (!button && performance.now() < deadline) return requestAnimationFrame(inspect);
          if (!button) return done({error: 'expand-in-Finder action missing', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          button.click();
          const waitForFinder = () => {
            if (itemInLayout(finderItemId) && currentFileExplorerRoot() === '/repo/app/src') {
              return done({finderOpen: true, root: currentFileExplorerRoot(), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
            }
            if (performance.now() >= deadline) return done({error: 'Finder did not open the changed directory', root: currentFileExplorerRoot(), errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
            requestAnimationFrame(waitForFinder);
          };
          requestAnimationFrame(waitForFinder);
        };
        requestAnimationFrame(inspect);
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["finderOpen"] is True and metrics["root"] == "/repo/app/src", metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_finder_differ_directory_diff_counts_are_bare_numbers(browser, tmp_path):
    payload = {
        "session": "1",
        "loaded": True,
        "errors": [],
        "repos": [{"repo": "/repo/app", "count": 3, "added": 14, "removed": 5}],
        "files": [
            {"session": "1", "repo": "/repo/app", "path": "src/a.py", "abs_path": "/repo/app/src/a.py", "status": "M", "mtime": 100, "added": 2, "removed": 1},
            {"session": "1", "repo": "/repo/app", "path": "src/b.py", "abs_path": "/repo/app/src/b.py", "status": "M", "mtime": 110, "added": 3, "removed": 4},
            {"session": "1", "repo": "/repo/app", "path": "docs/only.md", "abs_path": "/repo/app/docs/only.md", "status": "A", "mtime": 120, "added": 9, "removed": 0},
            {"session": "1", "repo": "/repo/app", "path": "archive/old.md", "abs_path": "/repo/app/archive/old.md", "status": "D", "mtime": 130, "added": 0, "removed": 9},
        ],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?layout=left&tabs=left:__changes__",
        sessions=["1"],
        session_files_payload=payload,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
                "return Array.from(document.querySelectorAll('#panel-__differ__ .file-tree-dir-count:not([hidden])')).some(node => node.textContent.trim() === '2')"
        )
    )
    metrics = browser.execute_script(
        """
        const panel = document.querySelector('#panel-__differ__');
        const rows = Array.from(panel.querySelectorAll('.file-tree-row.kind-dir'));
        const records = rows.map(row => {
          const count = row.querySelector(':scope > .file-tree-dir-count');
          const diff = row.querySelector(':scope > .file-tree-diff');
          const signedCount = diff?.querySelector('.changes-diff-add, .changes-diff-remove');
          const label = diff?.querySelector('.changes-diff-file-label');
          return {
            path: row.dataset.path || '',
            text: row.textContent.trim().replace(/\\s+/g, ' '),
            diff: diff?.textContent.trim().replace(/\\s+/g, ' ') || '',
            count: count?.textContent.trim() || '',
            hidden: count?.hidden !== false,
            signedColor: signedCount ? getComputedStyle(signedCount).color : '',
            labelColor: label ? getComputedStyle(label).color : '',
          };
        });
        return {
          errors: jsDebugFailureEvents('error'),
          rejections: jsDebugFailureEvents('rejection'),
          records,
          visibleCounts: records.filter(record => !record.hidden).map(record => record.count),
          hasFileChangedLabel: records.some(record => /\\bfiles? changed\\b/.test(record.text)),
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert not metrics["hasFileChangedLabel"], metrics
    assert "2" in set(metrics["visibleCounts"]), metrics
    assert all(re.fullmatch(r"[0-9]+", count) for count in metrics["visibleCounts"]), metrics
    diff_by_path = {record["path"]: record["diff"] for record in metrics["records"]}
    records_by_path = {record["path"]: record for record in metrics["records"]}
    assert diff_by_path["/repo/app/docs"] == "+1 files", metrics
    assert diff_by_path["/repo/app/archive"] == "-1 files", metrics
    assert records_by_path["/repo/app/docs"]["hidden"] is True, metrics
    assert records_by_path["/repo/app/archive"]["hidden"] is True, metrics
    assert records_by_path["/repo/app/docs"]["signedColor"] != records_by_path["/repo/app/docs"]["labelColor"], metrics
    assert records_by_path["/repo/app/archive"]["signedColor"] != records_by_path["/repo/app/archive"]["labelColor"], metrics


def test_differ_expanded_directory_chevrons_follow_row_state_through_reload(browser, tmp_path):
    payload = {
        "session": "1",
        "loaded": True,
        "errors": [],
        "repos": [{"repo": "/repo/app", "count": 3}],
        "files": [
            {"session": "1", "repo": "/repo/app", "path": "toolcalling/fixtures/inputs/qwen3_coder/one.yaml", "abs_path": "/repo/app/toolcalling/fixtures/inputs/qwen3_coder/one.yaml", "status": "M", "mtime": 100},
            {"session": "1", "repo": "/repo/app", "path": "toolcalling/fixtures/inputs/qwen3_coder/two.yaml", "abs_path": "/repo/app/toolcalling/fixtures/inputs/qwen3_coder/two.yaml", "status": "M", "mtime": 101},
        ],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?layout=left&tabs=left:__changes__",
        sessions=["1"],
        session_files_payload=payload,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.file-tree-row[data-path=\"/repo/app/toolcalling/fixtures/inputs/qwen3_coder/one.yaml\"]') !== null"
        )
    )
    browser.refresh()
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.file-tree-row[data-path=\"/repo/app/toolcalling/fixtures/inputs/qwen3_coder/one.yaml\"]') !== null"
        )
    )
    metrics = browser.execute_script(
        """
        const expandedRows = Array.from(document.querySelectorAll('.file-explorer-changes-panel .file-tree-row.kind-dir[aria-expanded="true"]'));
        const stateFor = row => {
          const icon = row.querySelector(':scope > .file-tree-icon.ui-disclosure-triangle');
          return {
            path: row.dataset.path,
            ariaExpanded: row.getAttribute('aria-expanded'),
            disclosureExpanded: icon?.dataset.disclosureExpanded || '',
            transform: icon ? getComputedStyle(icon).transform : '',
          };
        };
        const initial = expandedRows.map(stateFor);
        // A DOM restore can momentarily retain a previous icon data attribute. The row's semantic
        // expansion state must still keep the visible chevron aligned with its visible children.
        expandedRows.forEach(row => {
          row.querySelector(':scope > .file-tree-icon')?.setAttribute('data-disclosure-expanded', 'false');
        });
        return {
          initial,
          restored: expandedRows.map(stateFor),
          errors: jsDebugFailureEvents('error'),
          rejections: jsDebugFailureEvents('rejection'),
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert len(metrics["initial"]) >= 4, metrics
    assert all(row["ariaExpanded"] == "true" for row in metrics["initial"]), metrics
    assert all(row["disclosureExpanded"] == "true" for row in metrics["initial"]), metrics
    assert all(row["transform"] != "none" for row in metrics["initial"]), metrics
    assert all(row["transform"] != "none" for row in metrics["restored"]), metrics


def test_sync_mode_opens_common_repo_parent_and_expands_affected_dirs(browser, tmp_path):
    session_files_payload = {
        "session": "1",
        "loaded": True,
        "errors": [],
        "repos": [{"repo": "/home/test/dynamo/repo-a"}, {"repo": "/home/test/dynamo/repo-b"}],
        "files": [
            {"repo": "/home/test/dynamo/repo-a", "path": "src/a.js", "abs_path": "/home/test/dynamo/repo-a/src/a.js"},
            {"repo": "/home/test/dynamo/repo-b", "path": "lib/b.py", "abs_path": "/home/test/dynamo/repo-b/lib/b.py"},
        ],
    }
    updated_session_files_payload = {
        "session": "1",
        "loaded": True,
        "errors": [],
        "repos": [{"repo": "/home/test/dynamo/repo-a"}, {"repo": "/home/test/dynamo/repo-b"}, {"repo": "/home/test/dynamo/repo-c"}],
        "files": [
            {"repo": "/home/test/dynamo/repo-a", "path": "src/a.js", "abs_path": "/home/test/dynamo/repo-a/src/a.js"},
            {"repo": "/home/test/dynamo/repo-b", "path": "lib/b.py", "abs_path": "/home/test/dynamo/repo-b/lib/b.py"},
            {"repo": "/home/test/dynamo/repo-c", "path": "docs/c.md", "abs_path": "/home/test/dynamo/repo-c/docs/c.md"},
        ],
    }
    fs_entries = {
        "/home/test": [{"name": "dynamo", "kind": "dir"}],
        "/home/test/dynamo": [{"name": "repo-a", "kind": "dir"}, {"name": "repo-b", "kind": "dir"}, {"name": "repo-c", "kind": "dir"}],
        "/home/test/dynamo/repo-a": [{"name": "src", "kind": "dir"}],
        "/home/test/dynamo/repo-a/src": [{"name": "a.js", "kind": "file"}],
        "/home/test/dynamo/repo-b": [{"name": "lib", "kind": "dir"}],
        "/home/test/dynamo/repo-b/lib": [{"name": "b.py", "kind": "file"}],
        "/home/test/dynamo/repo-c": [{"name": "docs", "kind": "dir"}],
        "/home/test/dynamo/repo-c/docs": [{"name": "c.md", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@35(slot1,left)&tabs=slot1:files;left:1",
        settings={"file_explorer": {"root_mode": "sync"}},
        transcript_current_path="/home/test/dynamo/repo-a/src",
        transcript_git_root="/home/test/dynamo/repo-a",
        session_files_payload=session_files_payload,
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return ['/home/test', '~'].includes(document.querySelector('.file-explorer-path-inline')?.value)
              && document.getElementById('panel-1') !== null;
            """
        )
    )
    click_visible_panel(browser, "panel-1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            const rows = new Map(Array.from(tree?.querySelectorAll('.file-tree-row') || []).map(row => [row.dataset.path, row]));
            return fileExplorerSyncState.inFlightSignature === ''
              && document.querySelector('.file-explorer-path-inline')?.value === '~'
              && rows.get('/home/test/dynamo')?.getAttribute('aria-expanded') === 'true'
              && rows.get('/home/test/dynamo/repo-a')?.getAttribute('aria-expanded') === 'true'
              && rows.get('/home/test/dynamo/repo-b')?.getAttribute('aria-expanded') === 'true'
              && rows.has('/home/test/dynamo/repo-a/src')
              && rows.has('/home/test/dynamo/repo-b/lib');
            """
        )
    )
    metrics = browser.execute_script(
        """
        const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
	        const rows = Array.from(tree?.querySelectorAll('.file-tree-row') || []).map(row => ({
	          path: row.dataset.path,
	          kind: row.dataset.kind,
	          expanded: row.getAttribute('aria-expanded') === 'true',
	          classes: Array.from(row.classList),
	          syncStar: row.querySelector(':scope > .file-tree-sync-target')?.textContent || '',
	          syncStarHidden: row.querySelector(':scope > .file-tree-sync-target')?.hidden ?? true,
	          syncStarTitle: row.querySelector(':scope > .file-tree-sync-target')?.getAttribute('title') || '',
	          background: getComputedStyle(row).backgroundColor,
	          nameWeight: Number(getComputedStyle(row.querySelector(':scope > .file-tree-name')).fontWeight),
	        }));
        return {
          errors: jsDebugFailureEvents('error'),
          rejections: jsDebugFailureEvents('rejection'),
          root: document.querySelector('.file-explorer-path-inline')?.value || '',
          rows,
          fetchedPaths: window.__bootFetches.filter(item => item.path === '/api/fs/list' || item.path === '/api/fs/fast/list' || item.path === '/api/fs/batch').length,
          plan: fileExplorerSyncPlan('1'),
          expandedSet: Array.from(fileExplorerExpanded),
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["root"] == "~", metrics
    assert metrics["plan"]["root"] == "/home/test", metrics
    row_paths = {row["path"] for row in metrics["rows"]}
    assert {"/home/test/dynamo/repo-a", "/home/test/dynamo/repo-a/src", "/home/test/dynamo/repo-b", "/home/test/dynamo/repo-b/lib"}.issubset(row_paths), metrics
    expanded_paths = {row["path"] for row in metrics["rows"] if row["expanded"]}
    assert {"/home/test/dynamo", "/home/test/dynamo/repo-a", "/home/test/dynamo/repo-a/src", "/home/test/dynamo/repo-b", "/home/test/dynamo/repo-b/lib"}.issubset(expanded_paths), metrics
    rows_by_path = {row["path"]: row for row in metrics["rows"]}
    for path in ["/home/test/dynamo/repo-a", "/home/test/dynamo/repo-b"]:
        assert "file-tree-row--sync-expanded" in rows_by_path[path]["classes"], metrics
        assert "file-tree-row--session-repo" not in rows_by_path[path]["classes"], metrics
        assert "file-tree-row--session-touched" not in rows_by_path[path]["classes"], metrics
        assert "file-tree-row--sync-target" in rows_by_path[path]["classes"], metrics
        assert rows_by_path[path]["syncStar"] == "★", metrics
        assert rows_by_path[path]["syncStarHidden"] is False, metrics
        assert "1" in rows_by_path[path]["syncStarTitle"], metrics
        assert rows_by_path[path]["background"] == "rgba(0, 0, 0, 0)", metrics
        assert rows_by_path[path]["nameWeight"] >= 700, metrics
    for path in ["/home/test/dynamo/repo-a/src", "/home/test/dynamo/repo-b/lib"]:
        assert "file-tree-row--sync-expanded" not in rows_by_path[path]["classes"], metrics
        assert "file-tree-row--session-repo" not in rows_by_path[path]["classes"], metrics
        assert "file-tree-row--session-touched" not in rows_by_path[path]["classes"], metrics
        assert "file-tree-row--sync-target" in rows_by_path[path]["classes"], metrics
        assert rows_by_path[path]["syncStar"] == "★", metrics
        assert rows_by_path[path]["syncStarHidden"] is False, metrics
        assert "1" in rows_by_path[path]["syncStarTitle"], metrics
        assert "file-tree-row--changed-ancestor" in rows_by_path[path]["classes"], metrics
        assert rows_by_path[path]["background"] == "rgba(0, 0, 0, 0)", metrics
        assert rows_by_path[path]["nameWeight"] >= 700, metrics
    hover_row = next(
        row
        for row in browser.find_elements(By.CSS_SELECTOR, '.file-tree-row[data-path="/home/test/dynamo/repo-a/src"]')
        if row.is_displayed() and row.rect["width"] > 0 and row.rect["height"] > 0
    )
    browser.execute_script("arguments[0].scrollIntoView({block: 'center'});", hover_row)
    fast_pointer_actions(browser).move_to_element(hover_row).perform()
    hover_detail = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const text = document.getElementById('fileTreeRepoPopover')?.textContent || '';
            return text.includes('★') && text.includes('Modified by 1') ? text : '';
            """
        )
        or False
    )
    assert "★" in hover_detail and "Modified by 1" in hover_detail, hover_detail
    assert metrics["fetchedPaths"] >= 1, metrics
    manual_collapse = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
        const row = tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/repo-a"]');
        row.click();
        requestAnimationFrame(() => {
          scheduleFileExplorerActiveTabSync();
          requestAnimationFrame(() => requestAnimationFrame(() => {
            const current = tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/repo-a"]');
            done({
              expanded: current?.getAttribute('aria-expanded'),
              childVisible: tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/repo-a/src"]') !== null,
              syncStar: current?.querySelector(':scope > .file-tree-sync-target')?.textContent || '',
              syncStarHidden: current?.querySelector(':scope > .file-tree-sync-target')?.hidden ?? true,
              root: document.querySelector('.file-explorer-path-inline')?.value || '',
            });
          }));
        });
        """
    )
    assert manual_collapse["root"] == "~", manual_collapse
    assert manual_collapse["expanded"] == "false", manual_collapse
    assert manual_collapse["childVisible"] is False, manual_collapse
    assert manual_collapse["syncStar"] == "★", manual_collapse
    assert manual_collapse["syncStarHidden"] is False, manual_collapse
    payload_change = browser.execute_async_script(
        """
        const updatedPayload = arguments[0];
        const done = arguments[arguments.length - 1];
        window.__fixtureSessionFilesPayload = updatedPayload;
        fetchSessionFiles({destination: 'finder', session: '1', force: true, silent: true}).then(() => {
          rememberFileExplorerExplicitSyncSession('1');
          return syncFileExplorerRootToActiveTmux('1');
        }).then(() => {
          requestAnimationFrame(() => requestAnimationFrame(() => {
            const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            done({
              root: document.querySelector('.file-explorer-path-inline')?.value || '',
              plan: fileExplorerSyncPlan('1'),
              repoAExpanded: tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/repo-a"]')?.getAttribute('aria-expanded') || '',
              repoAChildVisible: tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/repo-a/src"]') !== null,
              repoCExpanded: tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/repo-c"]')?.getAttribute('aria-expanded') || '',
              repoCChildVisible: tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/repo-c/docs"]') !== null,
              repoCStar: tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/repo-c/docs"] > .file-tree-sync-target')?.textContent || '',
              repoCStarHidden: tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/repo-c/docs"] > .file-tree-sync-target')?.hidden ?? true,
              manualCollapsed: Array.from(fileExplorerSyncManualCollapsedPaths),
            });
          }));
        }).catch(error => done({error: String(error)}));
        """,
        updated_session_files_payload,
    )
    assert payload_change["root"] == "~", payload_change
    assert payload_change["plan"]["root"] == "/home/test", payload_change
    assert payload_change["plan"]["expandPaths"] == [
        "/home/test/dynamo",
        "/home/test/dynamo/repo-a",
        "/home/test/dynamo/repo-b",
        "/home/test/dynamo/repo-c",
        "/home/test/dynamo/repo-a/src",
        "/home/test/dynamo/repo-b/lib",
        "/home/test/dynamo/repo-c/docs",
    ], payload_change
    assert payload_change["repoAExpanded"] == "false", payload_change
    assert payload_change["repoAChildVisible"] is False, payload_change
    assert payload_change["repoCExpanded"] == "true", payload_change
    assert payload_change["repoCChildVisible"] is True, payload_change
    assert payload_change["repoCStar"] == "★", payload_change
    assert payload_change["repoCStarHidden"] is False, payload_change
    assert "/home/test/dynamo/repo-a" in payload_change["manualCollapsed"], payload_change
    collapsed_starred = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
        const row = tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/repo-c/docs"]');
        row.click();
        syncFileExplorerRootToActiveTmux('1').then(() => {
          requestAnimationFrame(() => requestAnimationFrame(() => {
            const current = tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/repo-c/docs"]');
            done({
              expanded: current?.getAttribute('aria-expanded') || '',
              childVisible: tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/repo-c/docs/c.md"]') !== null,
              syncStar: current?.querySelector(':scope > .file-tree-sync-target')?.textContent || '',
              syncStarHidden: current?.querySelector(':scope > .file-tree-sync-target')?.hidden ?? true,
              manualCollapsed: Array.from(fileExplorerSyncManualCollapsedPaths),
            });
          }));
        }).catch(error => done({error: String(error)}));
        """
    )
    assert collapsed_starred["expanded"] == "false", collapsed_starred
    assert collapsed_starred["childVisible"] is False, collapsed_starred
    assert collapsed_starred["syncStar"] == "★", collapsed_starred
    assert collapsed_starred["syncStarHidden"] is False, collapsed_starred
    assert "/home/test/dynamo/repo-c/docs" in collapsed_starred["manualCollapsed"], collapsed_starred
    cleared = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        setFileExplorerRootMode('fixed', {sync: false});
        requestAnimationFrame(() => done(Array.from(document.querySelectorAll('.file-tree-row')).map(row => ({
          path: row.dataset.path,
          hasExpanded: row.classList.contains('file-tree-row--sync-expanded'),
          hasRepo: row.classList.contains('file-tree-row--session-repo'),
          hasTouched: row.classList.contains('file-tree-row--session-touched'),
        }))));
        """
    )
    assert not any(row["hasExpanded"] or row["hasRepo"] or row["hasTouched"] for row in cleared), cleared


def test_sync_mode_active_file_reveal_keeps_manual_collapse(browser, tmp_path):
    """Deterministic guard for the ~5%-under-load flake in
    test_sync_mode_opens_common_repo_parent_and_expands_affected_dirs.

    When the active tab's path is inside a repo the user just manually collapsed, the deferred
    "reveal active file" auto-expand walked every ancestor and resurrected the collapsed repo,
    leaving manualCollapsed intact but the directory re-expanded. The fix routes the auto-reveal
    ancestor expansion through the same fileExplorerSyncPathSuppressed predicate the sync expand-loop
    and remembered-state restore already use. Here we collapse repo-a, then fire the reveal for a
    file inside repo-a directly (no contention needed) and assert repo-a stays collapsed.
    """
    session_files_payload = {
        "session": "1",
        "loaded": True,
        "errors": [],
        "repos": [{"repo": "/home/test/dynamo/repo-a"}, {"repo": "/home/test/dynamo/repo-b"}],
        "files": [
            {"repo": "/home/test/dynamo/repo-a", "path": "src/a.js", "abs_path": "/home/test/dynamo/repo-a/src/a.js"},
            {"repo": "/home/test/dynamo/repo-b", "path": "lib/b.py", "abs_path": "/home/test/dynamo/repo-b/lib/b.py"},
        ],
    }
    fs_entries = {
        "/home/test": [{"name": "dynamo", "kind": "dir"}],
        "/home/test/dynamo": [{"name": "repo-a", "kind": "dir"}, {"name": "repo-b", "kind": "dir"}],
        "/home/test/dynamo/repo-a": [{"name": "src", "kind": "dir"}],
        "/home/test/dynamo/repo-a/src": [{"name": "a.js", "kind": "file"}],
        "/home/test/dynamo/repo-b": [{"name": "lib", "kind": "dir"}],
        "/home/test/dynamo/repo-b/lib": [{"name": "b.py", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@35(slot1,left)&tabs=slot1:files;left:1",
        settings={"file_explorer": {"root_mode": "sync"}},
        transcript_current_path="/home/test/dynamo/repo-a/src",
        transcript_git_root="/home/test/dynamo/repo-a",
        session_files_payload=session_files_payload,
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return ['/home/test', '~'].includes(document.querySelector('.file-explorer-path-inline')?.value) && document.getElementById('panel-1') !== null;"
        )
    )
    click_visible_panel(browser, "panel-1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return fileExplorerSyncState.inFlightSignature === '';"
        )
    )
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const repoA = '/home/test/dynamo/repo-a';
        const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
        tree.querySelector('.file-tree-row[data-path="' + repoA + '"]').click();
        const collapseDeadline = performance.now() + 2000;
        const waitForCollapse = () => {
          const collapsedAfterClick = tree.querySelector('.file-tree-row[data-path="' + repoA + '"]')?.getAttribute('aria-expanded') || '';
          const manualHasA = fileExplorerSyncManualCollapsedPaths.has(repoA);
          if (collapsedAfterClick !== 'false' || !manualHasA) {
            if (performance.now() >= collapseDeadline) {
              done({collapsedAfterClick, manualHasA, repoAExpanded: collapsedAfterClick, manualHasA_after: manualHasA});
            } else {
              setTimeout(waitForCollapse, 10);
            }
            return;
          }
          scheduleFileExplorerActiveFileReveal('/home/test/dynamo/repo-a/src/a.js');
          const revealDeadline = performance.now() + 500;
          let revealExpanded = false;
          const observeReveal = () => {
            const expanded = tree.querySelector('.file-tree-row[data-path="' + repoA + '"]')?.getAttribute('aria-expanded') || '';
            revealExpanded ||= expanded === 'true';
            if (performance.now() < revealDeadline) {
              setTimeout(observeReveal, 10);
              return;
            }
            done({
              collapsedAfterClick,
              manualHasA,
              repoAExpanded: revealExpanded ? 'true' : expanded,
              manualHasA_after: fileExplorerSyncManualCollapsedPaths.has(repoA),
            });
          };
          observeReveal();
        };
        waitForCollapse();
        """
    )
    # Preconditions: the click collapsed repo-a and recorded the manual collapse.
    assert result["collapsedAfterClick"] == "false", result
    assert result["manualHasA"] is True, result
    # The fix: the active-file reveal must not resurrect the manually-collapsed ancestor...
    assert result["repoAExpanded"] == "false", result
    # ...and the manual-collapse record is left intact (matching the original flake's signature).
    assert result["manualHasA_after"] is True, result


def test_fetch_file_entry_status_succeeds_for_existing_preview_sample(browser, tmp_path):
    fs_entries = {
        "/home/test/yolomux.dev3/tests/fixtures/preview-samples": [
            {"name": "03-mixed.md", "kind": "file", "size": 128, "mtime_ns": 1781300000000000000},
        ],
    }
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1&layout=left&tabs=left:1", fs_entries=fs_entries)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        fetchFileEntryStatus('/home/test/yolomux.dev3/tests/fixtures/preview-samples/03-mixed.md')
          .then(result => done({
            entry: result.entry,
            missing: result.missing,
            lookupError: result.error,
            network: result.network,
            fsFetches: window.__bootFetches
              .filter(item => item.path === '/api/fs/list' || item.path === '/api/fs/fast/list' || item.path === '/api/fs/batch')
              .map(item => ({path: item.path, body: item.body, search: item.search})),
          }))
          .catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["entry"] is not None, metrics
    assert metrics["entry"]["name"] == "03-mixed.md", metrics
    assert metrics["entry"]["kind"] == "file", metrics
    assert metrics["missing"] is False, metrics
    assert metrics["network"] is False, metrics
    assert metrics["lookupError"] is None, metrics
    assert metrics["fsFetches"], metrics


def test_sync_finder_follows_clicked_editor_file_to_repo(browser, tmp_path):
    path = "/home/test/dynamo/frontend-crates/conformance/utils/tests/parity/reasoning/table.py"
    session_files_payload = {
        "session": "2",
        "loaded": True,
        "errors": [],
        "repos": [{"repo": "/home/test/dynamo/frontend-crates"}],
        "files": [{"repo": "/home/test/dynamo/frontend-crates", "path": "conformance/utils/tests/parity/reasoning/table.py", "abs_path": path}],
    }
    fs_entries = {
        "/home/test": [{"name": "dynamo", "kind": "dir"}],
        "/home/test/dynamo": [{"name": "frontend-crates", "kind": "dir"}],
        "/home/test/dynamo/frontend-crates": [{"name": "conformance", "kind": "dir"}],
        "/home/test/dynamo/frontend-crates/conformance": [{"name": "utils", "kind": "dir"}],
        "/home/test/dynamo/frontend-crates/conformance/utils": [{"name": "tests", "kind": "dir"}],
        "/home/test/dynamo/frontend-crates/conformance/utils/tests": [{"name": "parity", "kind": "dir"}],
        "/home/test/dynamo/frontend-crates/conformance/utils/tests/parity": [{"name": "reasoning", "kind": "dir"}],
        "/home/test/dynamo/frontend-crates/conformance/utils/tests/parity/reasoning": [{"name": "table.py", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1,2" + "&layout=row@35(slot1,left)" + f"&tabs=slot1:files;left:file:{path}",
        settings={"file_explorer": {"root_mode": "sync"}},
        sessions=["1", "2"],
        transcript_sessions={
            "1": {"current_path": "/home/test/yolomux.dev", "git_root": "/home/test/yolomux.dev"},
            "2": {"current_path": "/home/test/dynamo/frontend-crates", "git_root": "/home/test/dynamo/frontend-crates"},
        },
        session_files_payload=session_files_payload,
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return ['/home/test', '~'].includes(document.querySelector('.file-explorer-path-inline')?.value)
              && document.querySelector('.file-editor-panel[data-file-path="/home/test/dynamo/frontend-crates/conformance/utils/tests/parity/reasoning/table.py"]');
            """
        )
    )
    click_visible_selector(browser, f'.file-editor-panel[data-file-path="{path}"]')
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            return document.querySelector('.file-explorer-path-inline')?.value === '~'
              && tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/frontend-crates/conformance/utils/tests/parity/reasoning/table.py"]') !== null;
            """
        )
    )
    metrics = browser.execute_script(
        """
        const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
        const path = '/home/test/dynamo/frontend-crates/conformance/utils/tests/parity/reasoning/table.py';
        return {
          errors: jsDebugFailureEvents('error'),
          rejections: jsDebugFailureEvents('rejection'),
          root: document.querySelector('.file-explorer-path-inline')?.value || '',
          mode: fileExplorerRootModeValue(),
          plan: fileExplorerSyncPlanForFile(path),
          fileVisible: tree.querySelector(`.file-tree-row[data-path="${path}"]`) !== null,
          fileCurrent: tree.querySelector(`.file-tree-row[data-path="${path}"]`)?.classList.contains('current-file') || false,
          expandedSet: Array.from(fileExplorerExpanded),
          listedPaths: window.__bootFetches.filter(item => item.path === '/api/fs/list' || item.path === '/api/fs/fast/list' || item.path === '/api/fs/batch')
            .flatMap(item => item.path === '/api/fs/list' || item.path === '/api/fs/fast/list' ? [new URLSearchParams(item.search || '').get('path')]
              : (item.body?.requests || []).filter(request => request.type === 'list').map(request => request.path)),
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["root"] == "~", metrics
    assert metrics["mode"] == "sync", metrics
    assert metrics["plan"]["root"] == "/home/test", metrics
    assert metrics["plan"]["session"] == "2", metrics
    parent = "/home/test/dynamo/frontend-crates/conformance/utils/tests/parity/reasoning"
    assert metrics["plan"]["expandPaths"] == metrics["plan"]["affectedDirs"] == [parent], metrics
    assert metrics["fileVisible"] is True, metrics
    assert metrics["fileCurrent"] is True, metrics
    assert path not in metrics["listedPaths"] and parent in metrics["listedPaths"], metrics
    assert "/home/test/dynamo/frontend-crates/conformance/utils/tests/parity/reasoning" in metrics["expandedSet"], metrics

    collapsed = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
        tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/frontend-crates/conformance"]').click();
        requestAnimationFrame(() => requestAnimationFrame(() => done({
          conformanceExpanded: tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/frontend-crates/conformance"]')?.getAttribute('aria-expanded') || '',
          manualCollapsed: Array.from(fileExplorerSyncManualCollapsedPaths),
        })));
        """
    )
    assert collapsed["conformanceExpanded"] == "false", collapsed
    assert "/home/test/dynamo/frontend-crates/conformance" in collapsed["manualCollapsed"], collapsed
    click_visible_selector(browser, f'.file-editor-panel[data-file-path="{path}"]')
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            return tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/frontend-crates/conformance"]')?.getAttribute('aria-expanded') === 'true'
              && tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/frontend-crates/conformance/utils/tests/parity/reasoning/table.py"]') !== null;
            """
        )
    )
    reveal_after_manual_collapse = browser.execute_script(
        """
        const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
        const filePath = '/home/test/dynamo/frontend-crates/conformance/utils/tests/parity/reasoning/table.py';
        return {
          conformanceExpanded: tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/frontend-crates/conformance"]')?.getAttribute('aria-expanded') || '',
          fileVisible: tree.querySelector(`.file-tree-row[data-path="${filePath}"]`) !== null,
          manualCollapsed: Array.from(fileExplorerSyncManualCollapsedPaths),
        };
        """
    )
    assert reveal_after_manual_collapse["conformanceExpanded"] == "true", reveal_after_manual_collapse
    assert reveal_after_manual_collapse["fileVisible"] is True, reveal_after_manual_collapse
    assert "/home/test/dynamo/frontend-crates/conformance" not in reveal_after_manual_collapse["manualCollapsed"], reveal_after_manual_collapse


def test_sync_mode_remembers_collapsed_parent_directory(browser, tmp_path):
    session_files_payload = {
        "session": "1",
        "loaded": True,
        "errors": [],
        "repos": [{"repo": "/home/test/dynamo/repo-a"}, {"repo": "/home/test/yolomux.dev"}],
        "files": [
            {"repo": "/home/test/dynamo/repo-a", "path": "src/a.js", "abs_path": "/home/test/dynamo/repo-a/src/a.js"},
            {"repo": "/home/test/yolomux.dev", "path": "static/app.js", "abs_path": "/home/test/yolomux.dev/static/app.js"},
        ],
    }
    fs_entries = {
        "/home/test": [{"name": "dynamo", "kind": "dir"}, {"name": "yolomux.dev", "kind": "dir"}],
        "/home/test/dynamo": [{"name": "repo-a", "kind": "dir"}],
        "/home/test/dynamo/repo-a": [{"name": "src", "kind": "dir"}],
        "/home/test/dynamo/repo-a/src": [{"name": "a.js", "kind": "file"}],
        "/home/test/yolomux.dev": [{"name": "static", "kind": "dir"}],
        "/home/test/yolomux.dev/static": [{"name": "app.js", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@35(slot1,left)&tabs=slot1:files;left:1",
        settings={"file_explorer": {"root_mode": "sync"}},
        transcript_current_path="/home/test/dynamo/repo-a/src",
        transcript_git_root="/home/test/dynamo/repo-a",
        session_files_payload=session_files_payload,
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return ['/home/test', '~'].includes(document.querySelector('.file-explorer-path-inline')?.value)
              && document.getElementById('panel-1') !== null;
            """
        )
    )
    click_visible_panel(browser, "panel-1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            return document.querySelector('.file-explorer-path-inline')?.value === '~'
              && tree.querySelector('.file-tree-row[data-path="/home/test/dynamo"]')?.getAttribute('aria-expanded') === 'true'
              && tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/repo-a"]')?.getAttribute('aria-expanded') === 'true'
              && tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev"]')?.getAttribute('aria-expanded') === 'true';
            """
        )
    )
    collapsed_parent = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
        const row = tree.querySelector('.file-tree-row[data-path="/home/test/dynamo"]');
        setExplicitPaneFocusItem('', {allowInactive: true, clearTmux: true});
        row.click();
        syncFileExplorerRootToActiveTmux('1').then(() => {
          requestAnimationFrame(() => requestAnimationFrame(() => {
            const current = tree.querySelector('.file-tree-row[data-path="/home/test/dynamo"]');
            done({
              root: document.querySelector('.file-explorer-path-inline')?.value || '',
              plan: fileExplorerSyncPlan('1'),
              visibleSession: fileExplorerVisibleSyncSession,
              manualCollapsed: Array.from(fileExplorerSyncManualCollapsedPaths),
              dynamoExpanded: current?.getAttribute('aria-expanded') || '',
              repoVisible: tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/repo-a"]') !== null,
              yolomuxExpanded: tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev"]')?.getAttribute('aria-expanded') || '',
            });
          }));
        }).catch(error => done({error: String(error)}));
        """
    )
    assert collapsed_parent["root"] == "~", collapsed_parent
    assert set(collapsed_parent["plan"]["expandPaths"]) == {
        "/home/test/dynamo",
        "/home/test/dynamo/repo-a",
        "/home/test/dynamo/repo-a/src",
        "/home/test/yolomux.dev",
        "/home/test/yolomux.dev/static",
    }, collapsed_parent
    assert collapsed_parent["visibleSession"] == "1", collapsed_parent
    assert "/home/test/dynamo" in collapsed_parent["manualCollapsed"], collapsed_parent
    assert collapsed_parent["dynamoExpanded"] == "false", collapsed_parent
    assert collapsed_parent["repoVisible"] is False, collapsed_parent
    assert collapsed_parent["yolomuxExpanded"] == "true", collapsed_parent


def test_sync_mode_typed_manual_path_disables_sync_before_slow_listing(browser, tmp_path):
    session_files_payload = {
        "session": "5",
        "loaded": True,
        "errors": [],
        "repos": [{"repo": "/home/test/yolomux.dev"}],
        "files": [],
    }
    fs_entries = {
        "/home/test": [{"name": "yolomux.dev", "kind": "dir"}],
        "/home/test/yolomux.dev": [{"name": "src", "kind": "dir"}],
        "/home/test/yolomux.dev/src": [{"name": "main.js", "kind": "file"}],
        "/tmp": [{"name": "scratch.txt", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,5&layout=row@35(slot1,left)&tabs=slot1:files;left:5",
        settings={"file_explorer": {"root_mode": "sync"}},
        sessions=["5"],
        transcript_sessions={
            "5": {"current_path": "/home/test/yolomux.dev/src", "git_root": "/home/test/yolomux.dev"},
        },
        session_files_payload=session_files_payload,
        session_files_payloads={"5": session_files_payload},
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return ['/home/test', '~'].includes(document.querySelector('.file-explorer-path-inline')?.value)
              && document.getElementById('panel-5') !== null;
            """
        )
    )
    click_visible_panel(browser, "panel-5")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.file-explorer-path-inline')?.value === '~'
              && currentFileExplorerRoot() === '/home/test';
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const input = document.querySelector('.file-explorer-path-inline');
        const realFetchDirectory = fetchDirectory;
        let releaseTmp = null;
        let notifyTmpListingStarted = null;
        const tmpListingStarted = new Promise(resolve => { notifyTmpListingStarted = resolve; });
        fetchDirectory = (path, options = {}) => {
          if (path === '/tmp') {
            return new Promise(resolve => {
              releaseTmp = () => resolve([{name: 'scratch.txt', kind: 'file'}]);
              notifyTmpListingStarted();
            });
          }
          return realFetchDirectory(path, options);
        };
        input.focus();
        input.value = '/tmp';
        const openPromise = commitFileExplorerPathInput(input);
        tmpListingStarted.then(() => {
          const duringTree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
          const duringOpen = {
            path: document.querySelector('.file-explorer-path-inline')?.value || '',
            mode: fileExplorerRootModeValue(),
            syncPressed: document.querySelector('.file-explorer-root-mode-toggle-panel')?.getAttribute('aria-pressed') || '',
            manual: fileExplorerManualSelectionActive,
            currentRoot: currentFileExplorerRoot(),
            oldPathVisible: duringTree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev"], .file-tree-row[data-path="/home/test/yolomux.dev/src"]') !== null,
            searchingVisible: duringTree.querySelector('.file-tree-status-searching .file-tree-searching-dots') !== null,
            treeText: duringTree.textContent.trim(),
          };
          scheduleFileExplorerActiveTabSync('5', {explicit: true});
          requestAnimationFrame(() => {
            const afterTree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            const afterSyncAttempt = {
              path: document.querySelector('.file-explorer-path-inline')?.value || '',
              mode: fileExplorerRootModeValue(),
              syncPressed: document.querySelector('.file-explorer-root-mode-toggle-panel')?.getAttribute('aria-pressed') || '',
              manual: fileExplorerManualSelectionActive,
              currentRoot: currentFileExplorerRoot(),
              oldPathVisible: afterTree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev"], .file-tree-row[data-path="/home/test/yolomux.dev/src"]') !== null,
              searchingVisible: afterTree.querySelector('.file-tree-status-searching .file-tree-searching-dots') !== null,
              treeText: afterTree.textContent.trim(),
            };
            releaseTmp();
            openPromise.then(opened => {
              requestAnimationFrame(() => requestAnimationFrame(() => {
                fetchDirectory = realFetchDirectory;
                const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
                done({
                  opened,
                  duringOpen,
                  afterSyncAttempt,
                  final: {
                    path: document.querySelector('.file-explorer-path-inline')?.value || '',
                    mode: fileExplorerRootModeValue(),
                    syncPressed: document.querySelector('.file-explorer-root-mode-toggle-panel')?.getAttribute('aria-pressed') || '',
                    manual: fileExplorerManualSelectionActive,
                    currentRoot: currentFileExplorerRoot(),
                    tmpFileVisible: tree.querySelector('.file-tree-row[data-path="/tmp/scratch.txt"]') !== null,
                    searchingVisible: tree.querySelector('.file-tree-status-searching') !== null,
                  },
                });
              }));
            }).catch(error => {
              fetchDirectory = realFetchDirectory;
              done({error: String(error)});
            });
          });
        });
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["duringOpen"] == {
        "path": "/tmp",
        "mode": "fixed",
        "syncPressed": "false",
        "manual": True,
        "currentRoot": "/home/test",
        "oldPathVisible": False,
        "searchingVisible": True,
        "treeText": "searching...",
    }, metrics
    assert metrics["afterSyncAttempt"] == metrics["duringOpen"], metrics
    assert metrics["opened"] is True, metrics
    assert metrics["final"] == {
        "path": "/tmp",
        "mode": "fixed",
        "syncPressed": "false",
        "manual": True,
        "currentRoot": "/tmp",
        "tmpFileVisible": True,
        "searchingVisible": False,
    }, metrics


def test_sync_finder_remembers_same_root_cursor_without_stealing_browser_focus(browser, tmp_path):
    root = "/home/test"
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1,2&layout=row@35(slot1,left)&tabs=slot1:files;left:1,2",
        settings={"file_explorer": {"root_mode": "sync"}},
        sessions=["1", "2"],
        fs_entries={root: [{"name": "one.txt", "kind": "file"}, {"name": "two.txt", "kind": "file"}]},
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.file-tree-row[data-path=\"/home/test/one.txt\"]') !== null;"
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const root = '/home/test';
            const plan = session => ({session, root, expandPaths: [], affectedDirs: [root]});
            const sentinel = document.createElement('button');
            sentinel.type = 'button';
            sentinel.textContent = 'focus sentinel';
            document.body.append(sentinel);
            await syncFileExplorerRootToPlan(plan('1'), '1', {force: true});
            selectFileTreePath(`${root}/one.txt`);
            await syncFileExplorerRootToPlan(plan('2'), '2', {force: true});
            selectFileTreePath(`${root}/two.txt`);
            sentinel.focus();
            await syncFileExplorerRootToPlan(plan('1'), '1', {force: true});
            const tree = document.querySelector('.file-explorer-tree-panel');
            const first = tree?.querySelector('.file-tree-row[data-path="/home/test/one.txt"]');
            const firstState = {
              lead: fileExplorerSelectionLead,
              activeDescendant: tree?.getAttribute('aria-activedescendant') || '',
              rowId: first?.id || '',
              focusPreserved: document.activeElement === sentinel,
            };
            await syncFileExplorerRootToPlan(plan('2'), '2', {force: true});
            const second = tree?.querySelector('.file-tree-row[data-path="/home/test/two.txt"]');
            done({
              first: firstState,
              second: {
                lead: fileExplorerSelectionLead,
                activeDescendant: tree?.getAttribute('aria-activedescendant') || '',
                rowId: second?.id || '',
                focusPreserved: document.activeElement === sentinel,
              },
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          }
        })();
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["first"] == {
        "lead": "/home/test/one.txt",
        "activeDescendant": metrics["first"]["rowId"],
        "rowId": metrics["first"]["rowId"],
        "focusPreserved": True,
    }, metrics
    assert metrics["second"] == {
        "lead": "/home/test/two.txt",
        "activeDescendant": metrics["second"]["rowId"],
        "rowId": metrics["second"]["rowId"],
        "focusPreserved": True,
    }, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_sync_mode_stale_session_root_open_cannot_override_typed_manual_path(browser, tmp_path):
    session_files_payload = {
        "session": "8002",
        "loaded": True,
        "errors": [],
        "repos": [{"repo": "/home/test/yolomux.dev2"}],
        "files": [],
    }
    fs_entries = {
        "/home/test": [{"name": "yolomux.dev1", "kind": "dir"}, {"name": "yolomux.dev2", "kind": "dir"}],
        "/home/test/yolomux.dev1": [{"name": "src", "kind": "dir"}],
        "/home/test/yolomux.dev2": [{"name": "src", "kind": "dir"}],
        "/tmp": [{"name": "scratch.txt", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,5,8002&layout=row@35(slot1,left)&tabs=slot1:files;left:5,8002",
        settings={"file_explorer": {"root_mode": "sync"}},
        sessions=["5", "8002"],
        transcript_sessions={
            "5": {"current_path": "/home/test/yolomux.dev1/src", "git_root": "/home/test/yolomux.dev1"},
            "8002": {"current_path": "/home/test/yolomux.dev2/src", "git_root": "/home/test/yolomux.dev2"},
        },
        session_files_payload=session_files_payload,
        session_files_payloads={"8002": session_files_payload},
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return ['/home/test', '~'].includes(document.querySelector('.file-explorer-path-inline')?.value)
              && document.getElementById('panel-5') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const input = document.querySelector('.file-explorer-path-inline');
        const realFetchDirectory = fetchDirectory;
        let releaseDev2 = null;
        let releaseTmp = null;
        let notifyDev2ListingStarted = null;
        let notifyTmpListingStarted = null;
        const dev2ListingStarted = new Promise(resolve => { notifyDev2ListingStarted = resolve; });
        const tmpListingStarted = new Promise(resolve => { notifyTmpListingStarted = resolve; });
        const snapshot = () => {
          const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
          const searchingRow = tree.querySelector('.file-tree-status-searching');
          return {
            path: document.querySelector('.file-explorer-path-inline')?.value || '',
            mode: fileExplorerRootModeValue(),
            syncPressed: document.querySelector('.file-explorer-root-mode-toggle-panel')?.getAttribute('aria-pressed') || '',
            manual: fileExplorerManualSelectionActive,
            currentRoot: currentFileExplorerRoot(),
            searchingVisible: Boolean(searchingRow?.querySelector('.file-tree-searching-dots')),
            dev2Visible: tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev2/src"]') !== null,
            tmpFileVisible: tree.querySelector('.file-tree-row[data-path="/tmp/scratch.txt"]') !== null,
            searchingText: searchingRow?.textContent.trim() || '',
          };
        };
        fetchDirectory = (path, options = {}) => {
          if (path === '/home/test/yolomux.dev2') {
            return new Promise(resolve => {
              releaseDev2 = () => resolve([{name: 'src', kind: 'dir'}]);
              notifyDev2ListingStarted();
            });
          }
          if (path === '/tmp') {
            return new Promise(resolve => {
              releaseTmp = () => resolve([{name: 'scratch.txt', kind: 'file'}]);
              notifyTmpListingStarted();
            });
          }
          return realFetchDirectory(path, options);
        };
        const staleSyncPromise = syncFileExplorerRootToPlan({
          session: '8002',
          root: '/home/test/yolomux.dev2',
          expandPaths: [],
          affectedDirs: ['/home/test/yolomux.dev2'],
        }, '8002', {force: true});
        dev2ListingStarted.then(() => {
          input.focus();
          input.value = '/tmp';
          const manualOpenPromise = commitFileExplorerPathInput(input);
          tmpListingStarted.then(() => {
            const duringManual = snapshot();
            releaseDev2();
            staleSyncPromise.then(staleOpened => {
              requestAnimationFrame(() => {
                const afterStaleSync = snapshot();
                releaseTmp();
                manualOpenPromise.then(manualOpened => {
                  requestAnimationFrame(() => requestAnimationFrame(() => {
                    fetchDirectory = realFetchDirectory;
                    done({
                      staleOpened,
                      manualOpened,
                      duringManual,
                      afterStaleSync,
                      final: snapshot(),
                    });
                  }));
                }).catch(error => {
                  fetchDirectory = realFetchDirectory;
                  done({error: String(error)});
                });
              });
            }).catch(error => {
              fetchDirectory = realFetchDirectory;
              done({error: String(error)});
            });
          });
        });
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["duringManual"] == {
        "path": "/tmp",
        "mode": "fixed",
        "syncPressed": "false",
        "manual": True,
        "currentRoot": "/home/test",
        "searchingVisible": True,
        "dev2Visible": False,
        "tmpFileVisible": False,
        "searchingText": "searching...",
    }, metrics
    assert metrics["staleOpened"] is False, metrics
    assert metrics["afterStaleSync"] == metrics["duringManual"], metrics
    assert metrics["manualOpened"] is True, metrics
    assert metrics["final"] == {
        "path": "/tmp",
        "mode": "fixed",
        "syncPressed": "false",
        "manual": True,
        "currentRoot": "/tmp",
        "searchingVisible": False,
        "dev2Visible": False,
        "tmpFileVisible": True,
        "searchingText": "",
    }, metrics


def test_sync_mode_user_select_session_8002_opens_transcript_root(browser, tmp_path):
    fs_entries = {
        "/home/test": [{"name": "yolomux.dev1", "kind": "dir"}, {"name": "yolomux.dev2", "kind": "dir"}],
        "/home/test/yolomux.dev1": [{"name": "src", "kind": "dir"}],
        "/home/test/yolomux.dev2": [{"name": "src", "kind": "dir"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,5,8002&layout=row@35(slot1,row@50(left,slot2))&tabs=slot1:files;left:5;slot2:8002",
        settings={"file_explorer": {"root_mode": "sync"}},
        sessions=["5", "8002"],
        transcript_sessions={
            "5": {"current_path": "/home/test/yolomux.dev1/src", "git_root": "/home/test/yolomux.dev1"},
            "8002": {"current_path": "/home/test/yolomux.dev2/src", "git_root": "/home/test/yolomux.dev2"},
        },
        session_files_payload={"session": "5", "loaded": True, "errors": [], "repos": [{"repo": "/home/test/yolomux.dev1"}], "files": []},
        session_files_payloads={
            "5": {"session": "5", "loaded": True, "errors": [], "repos": [{"repo": "/home/test/yolomux.dev1"}], "files": []},
            "8002": {"session": "8002", "loaded": True, "errors": [], "repos": [{"repo": "/home/test/yolomux.dev2"}], "files": []},
        },
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return ['/home/test', '~'].includes(document.querySelector('.file-explorer-path-inline')?.value)
              && document.getElementById('panel-5') !== null
              && document.getElementById('panel-8002') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const realPushConnected = clientPushConnectedForData;
        const realUserActive = fileExplorerUserIsActive;
        const realFetchDirectory = fetchDirectory;
        let releaseDev2 = null;
        clientPushConnectedForData = () => true;
        fileExplorerUserIsActive = () => false;
        fetchDirectory = (path, options = {}) => {
          if (path === '/home/test/yolomux.dev2') {
            return new Promise(resolve => {
              releaseDev2 = () => resolve([{name: 'src', kind: 'dir'}]);
            });
          }
          return realFetchDirectory(path, options);
        };
        const restore = () => {
          clientPushConnectedForData = realPushConnected;
          fileExplorerUserIsActive = realUserActive;
          fetchDirectory = realFetchDirectory;
        };
        const finderSessionSelect = Array.from(document.querySelectorAll('#panel-__finder__ [data-session-files-session]')).find(select => select.getClientRects().length > 0);
        finderSessionSelect.value = '8002';
        finderSessionSelect.dispatchEvent(new Event('change', {bubbles: true, cancelable: true}));
        Promise.resolve().then(() => {
          let attempts = 0;
          const waitForPending = () => {
            attempts += 1;
            const root = document.querySelector('.file-explorer-path-inline')?.value || '';
            const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            const pending = {
              root,
              mode: fileExplorerRootModeValue(),
              manual: fileExplorerManualSelectionActive,
              explicitSession: fileExplorerExplicitSyncSessionTarget(),
              targetSession: fileExplorerSessionFilesTargetSession(),
              currentPath: terminalCurrentPath('8002'),
              gitRoot: sessionWorkSummary('8002', transcriptMetadataState.payload.sessions?.['8002'] || {}).git?.root || '',
              planRoot: fileExplorerSyncPlan('8002').root,
              searchingVisible: tree.querySelector('.file-tree-status-searching .file-tree-searching-dots') !== null,
              dev2Visible: tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev2/src"]') !== null,
              heldFetch: releaseDev2 !== null,
            };
            if (pending.root === '~' && releaseDev2) {
              releaseDev2();
              let finalAttempts = 0;
              const waitForFinal = () => {
                finalAttempts += 1;
                const finalRoot = document.querySelector('.file-explorer-path-inline')?.value || '';
                const finalTree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
                if (
                  (
                    finalTree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev2/src"]')
                    && fileExplorerSyncState.inFlightSignature === ''
                  )
                  || finalAttempts > 180
                ) {
                  const plan = fileExplorerSyncPlan('8002');
                  const result = {
                    pending,
                    final: {
                      root: finalRoot,
                      mode: fileExplorerRootModeValue(),
                      manual: fileExplorerManualSelectionActive,
                      explicitSession: fileExplorerExplicitSyncSessionTarget(),
                      targetSession: fileExplorerSessionFilesTargetSession(),
                      currentPath: terminalCurrentPath('8002'),
                      gitRoot: sessionWorkSummary('8002', transcriptMetadataState.payload.sessions?.['8002'] || {}).git?.root || '',
                      planRoot: plan.root,
                      syncInFlight: fileExplorerSyncState.inFlightSignature,
                      appliedKey: fileExplorerSyncState.appliedPlanKey,
                      searchingVisible: finalTree.querySelector('.file-tree-status-searching') !== null,
                      dev2Visible: finalTree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev2/src"]') !== null,
                    },
                  };
                  restore();
                  done(result);
                  return;
                }
                requestAnimationFrame(waitForFinal);
              };
              requestAnimationFrame(waitForFinal);
              return;
            }
            if (attempts > 180) {
              const plan = fileExplorerSyncPlan('8002');
              restore();
              done({
                error: 'session 8002 did not claim Finder before held directory listing resolved',
                pending,
                syncInFlight: fileExplorerSyncState.inFlightSignature,
                appliedKey: fileExplorerSyncState.appliedPlanKey,
                planRoot: plan.root,
              });
              return;
            }
            requestAnimationFrame(waitForPending);
          };
          requestAnimationFrame(waitForPending);
        }).catch(error => {
          restore();
          done({error: String(error)});
        });
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["pending"] == {
        "root": "~",
        "mode": "sync",
        "manual": False,
        "explicitSession": "8002",
        "targetSession": "8002",
        "currentPath": "/home/test/yolomux.dev2/src",
        "gitRoot": "/home/test/yolomux.dev2",
        "planRoot": "/home/test",
        "searchingVisible": False,
        "dev2Visible": False,
        "heldFetch": True,
    }, metrics
    assert metrics["final"] == {
        "root": "~",
        "mode": "sync",
        "manual": False,
        "explicitSession": "8002",
        "targetSession": "8002",
        "currentPath": "/home/test/yolomux.dev2/src",
        "gitRoot": "/home/test/yolomux.dev2",
        "planRoot": "/home/test",
        "syncInFlight": "",
        "appliedKey": "8002\x1f/home/test\x1f/home/test/yolomux.dev2\x1f/home/test/yolomux.dev2/src",
        "searchingVisible": False,
        "dev2Visible": True,
    }, metrics


def test_fixed_finder_session_dropdown_change_does_not_move_root(browser, tmp_path):
    # Requirement 3: with Sync OFF (fixed mode), changing the Finder Session dropdown must NOT move
    # the Finder path/root. The tree's root change happens ONLY via the dropdown when Sync is ON;
    # the companion test_sync_mode_user_select_session_8002_opens_transcript_root covers the Sync-ON
    # case. The guard lives in scheduleFileExplorerActiveTabSync (mode !== 'sync' early-returns).
    fs_entries = {
        "/home/test": [{"name": "yolomux.dev1", "kind": "dir"}, {"name": "yolomux.dev2", "kind": "dir"}],
        "/home/test/yolomux.dev1": [{"name": "src", "kind": "dir"}],
        "/home/test/yolomux.dev2": [{"name": "src", "kind": "dir"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,5,8002&layout=row@35(slot1,row@50(left,slot2))&tabs=slot1:files;left:5;slot2:8002",
        settings={"file_explorer": {"root_mode": "fixed"}},
        sessions=["5", "8002"],
        transcript_sessions={
            "5": {"current_path": "/home/test/yolomux.dev1/src", "git_root": "/home/test/yolomux.dev1"},
            "8002": {"current_path": "/home/test/yolomux.dev2/src", "git_root": "/home/test/yolomux.dev2"},
        },
        session_files_payloads={
            "5": {"session": "5", "loaded": True, "errors": [], "repos": [{"repo": "/home/test/yolomux.dev1"}], "files": []},
            "8002": {"session": "8002", "loaded": True, "errors": [], "repos": [{"repo": "/home/test/yolomux.dev2"}], "files": []},
        },
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return !!Array.from(document.querySelectorAll('#panel-__finder__ [data-session-files-session]'))"
            ".find(s => s.getClientRects().length > 0) && document.querySelector('.file-explorer-path-inline') !== null;"
        )
    )
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const before = {
          root: document.querySelector('.file-explorer-path-inline')?.value || '',
          mode: fileExplorerRootModeValue(),
        };
        const sel = Array.from(document.querySelectorAll('#panel-__finder__ [data-session-files-session]'))
          .find(s => s.getClientRects().length > 0);
        sel.value = '8002';
        sel.dispatchEvent(new Event('change', {bubbles: true, cancelable: true}));
        // Let any (incorrectly) scheduled sync settle before asserting the root did not move.
        setTimeout(() => {
          done({
            before,
            after: {
              root: document.querySelector('.file-explorer-path-inline')?.value || '',
              mode: fileExplorerRootModeValue(),
            },
            errors: jsDebugFailureEvents('error'),
            rejections: jsDebugFailureEvents('rejection'),
          });
        }, 600);
        """
    )
    assert result["errors"] == [], result
    assert result["rejections"] == [], result
    assert result["before"]["mode"] == "fixed", result
    assert result["after"]["mode"] == "fixed", result
    # Sync OFF: the Session-dropdown change must leave the path/root exactly where it was.
    assert result["after"]["root"] == result["before"]["root"], result


def test_sync_mode_typed_manual_path_does_not_snap_back_until_explicit_input(browser, tmp_path):
    session_files_payload = {
        "session": "5",
        "loaded": True,
        "errors": [],
        "repos": [{"repo": "/home/test/yolomux.dev"}],
        "files": [],
    }
    fs_entries = {
        "/home/test": [{"name": "yolomux.dev", "kind": "dir"}, {"name": "scratch", "kind": "dir"}],
        "/home/test/yolomux.dev": [{"name": "src", "kind": "dir"}],
        "/home/test/yolomux.dev/src": [{"name": "main.js", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,5&layout=row@35(slot1,left)&tabs=slot1:files;left:5",
        settings={"file_explorer": {"root_mode": "sync"}},
        sessions=["5"],
        transcript_sessions={
            "5": {"current_path": "/home/test/yolomux.dev/src", "git_root": "/home/test/yolomux.dev"},
        },
        session_files_payload=session_files_payload,
        session_files_payloads={"5": session_files_payload},
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return ['/home/test', '~'].includes(document.querySelector('.file-explorer-path-inline')?.value)
              && document.getElementById('panel-5') !== null;
            """
        )
    )
    sync_root_metrics = browser.execute_script(
        """
        return {
          mode: fileExplorerRootModeValue(),
          syncPressed: document.querySelector('.file-explorer-root-mode-toggle-panel')?.getAttribute('aria-pressed') || '',
        };
        """
    )
    assert sync_root_metrics == {
        "mode": "sync",
        "syncPressed": "true",
    }, sync_root_metrics
    click_visible_panel(browser, "panel-5")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.file-explorer-path-inline')?.value === '~'"
        )
    )
    manual_metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const input = document.querySelector('.file-explorer-path-inline');
        const realCommitFileExplorerPathInput = commitFileExplorerPathInput;
        let commitPromise = null;
        commitFileExplorerPathInput = target => {
          commitFileExplorerPathInput = realCommitFileExplorerPathInput;
          commitPromise = realCommitFileExplorerPathInput(target);
          return commitPromise;
        };
        input.focus();
        input.value = '/home/test';
        input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}));
        Promise.resolve(commitPromise).then(() => {
          scheduleFileExplorerActiveTabSync();
          setSessionFilesPayloadForDestination('finder', {session: '5', loaded: true, errors: [], repos: [{repo: '/home/test/yolomux.dev'}], files: []});
          requestAnimationFrame(() => requestAnimationFrame(() => {
            done({
              root: document.querySelector('.file-explorer-path-inline')?.value || '',
              manual: fileExplorerManualSelectionActive,
              mode: fileExplorerRootModeValue(),
              explicitSession: fileExplorerExplicitSyncSessionTarget(),
              planRoot: fileExplorerSyncPlan('5').root,
              syncPressed: document.querySelector('.file-explorer-root-mode-toggle-panel')?.getAttribute('aria-pressed') || '',
                });
              }));
        }).catch(error => {
          commitFileExplorerPathInput = realCommitFileExplorerPathInput;
          done({error: String(error)});
        });
        """
    )
    assert "error" not in manual_metrics, manual_metrics
    assert manual_metrics["root"] == "~", manual_metrics
    assert manual_metrics["manual"] is True, manual_metrics
    assert manual_metrics["mode"] == "fixed", manual_metrics
    assert manual_metrics["explicitSession"] == "5", manual_metrics
    assert manual_metrics["planRoot"] == "/home/test", manual_metrics
    assert manual_metrics["syncPressed"] == "false", manual_metrics
    browser.execute_script(
        """
        setFocusedTerminal('5', {userInitiated: true});
        setFocusedPanelItem('5', {userInitiated: true});
        document.getElementById('panel-5')?.click();
        """
    )
    unchanged_metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(() => {
          done({
              root: document.querySelector('.file-explorer-path-inline')?.value || '',
              mode: fileExplorerRootModeValue(),
              syncPressed: document.querySelector('.file-explorer-root-mode-toggle-panel')?.getAttribute('aria-pressed') || '',
            });
          }));
        """
    )
    assert unchanged_metrics == {
        "root": "~",
        "mode": "fixed",
        "syncPressed": "false",
    }, unchanged_metrics
    browser.execute_script(
        "document.querySelector('.file-explorer-root-mode-toggle-panel')?.click();"
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.file-explorer-path-inline')?.value === '~'
              && fileExplorerRootModeValue() === 'sync'
              && fileExplorerManualSelectionActive === false
              && document.querySelector('.file-explorer-root-mode-toggle-panel')?.getAttribute('aria-pressed') === 'true';
            """
        )
    )


def test_sync_mode_does_not_follow_hovered_tmux_session(browser, tmp_path):
    session_files_payloads = {
        "5": {
            "session": "5",
            "loaded": True,
            "errors": [],
            "repos": [{"repo": "/home/test/yolomux.dev"}],
            "files": [],
        },
        "6": {
            "session": "6",
            "loaded": True,
            "errors": [],
            "repos": [{"repo": "/home/test/other.dev"}],
            "files": [
                {"repo": "/home/test/other.dev", "path": "other/touched.js", "abs_path": "/home/test/other.dev/other/touched.js"},
            ],
        },
    }
    fs_entries = {
        "/home/test": [{"name": "yolomux.dev", "kind": "dir"}, {"name": "other.dev", "kind": "dir"}],
        "/home/test/yolomux.dev": [{"name": "src", "kind": "dir"}, {"name": "other", "kind": "dir"}],
        "/home/test/yolomux.dev/src": [{"name": "main.js", "kind": "file"}],
        "/home/test/yolomux.dev/other": [{"name": "touched.js", "kind": "file"}],
        "/home/test/other.dev": [{"name": "other", "kind": "dir"}],
        "/home/test/other.dev/other": [{"name": "touched.js", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,5,6&layout=row@35(slot1,row@50(left,slot2))&tabs=slot1:files;left:5;slot2:6",
        settings={"file_explorer": {"root_mode": "sync"}},
        sessions=["5", "6"],
        transcript_sessions={
            "5": {"current_path": "/home/test/yolomux.dev/src", "git_root": "/home/test/yolomux.dev"},
            "6": {"current_path": "/home/test/other.dev/other", "git_root": "/home/test/other.dev"},
        },
        session_files_payload=session_files_payloads["5"],
        session_files_payloads=session_files_payloads,
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return ['/home/test', '~'].includes(document.querySelector('.file-explorer-path-inline')?.value)
              && document.getElementById('panel-5') !== null
              && document.getElementById('panel-6') !== null;
            """
        )
    )
    wait_for_visible_panel(browser, "panel-6")
    click_visible_panel(browser, "panel-5")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.file-explorer-path-inline')?.value === '~'"
        )
    )
    click_visible_selector(browser, '.file-explorer-panel .file-tree-row[data-path="/home/test/yolomux.dev/other"]')
    expanded_before_switch = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            const row = tree?.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other"]');
            const result = {
              expanded: row.getAttribute('aria-expanded'),
              childVisible: tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other/touched.js"]') !== null,
              expandedSet: Array.from(fileExplorerExpanded),
            };
            return result.expanded === 'true' && result.childVisible ? result : false;
            """
        )
    )
    assert expanded_before_switch["expanded"] == "true", expanded_before_switch
    assert expanded_before_switch["childVisible"] is True, expanded_before_switch
    assert "/home/test/yolomux.dev/other" in expanded_before_switch["expandedSet"], expanded_before_switch
    click_visible_selector(browser, '.file-explorer-panel .file-tree-row[data-path="/home/test/yolomux.dev/other/touched.js"]')
    pinned_before_refresh = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            const row = tree?.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other/touched.js"]');
            return fileExplorerManualSelectionActive && row?.getAttribute('aria-selected') === 'true' ? {
              root: document.querySelector('.file-explorer-path-inline')?.value || '',
              expanded: Array.from(fileExplorerExpanded),
              selected: Array.from(fileExplorerSelectedPaths),
            } : false;
            """
        )
    )
    assert pinned_before_refresh["root"] == "~", pinned_before_refresh
    move_to_visible_panel(browser, "panel-6")
    hover_metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        setFocusedTerminal('6');
        applySessionMetadataPayload(transcriptMetadataState.payload, {
          refreshAuto: false,
          refreshActivity: false,
          refreshContext: false,
        }).then(() => requestAnimationFrame(() => requestAnimationFrame(() => {
          const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
          const plan = fileExplorerSyncPlan();
          done({
            root: document.querySelector('.file-explorer-path-inline')?.value || '',
            manual: fileExplorerManualSelectionActive,
            selected: Array.from(fileExplorerSelectedPaths),
            expanded: Array.from(fileExplorerExpanded),
            activeTmux: activeTmuxDirectoryPath(),
            planSession: plan.session,
            planRoot: plan.root,
            otherVisible: tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other"]') !== null,
            otherRepoVisible: tree.querySelector('.file-tree-row[data-path="/home/test/other.dev"]') !== null,
            otherExpanded: tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other"]')?.getAttribute('aria-expanded') || '',
          });
        }))).catch(error => done({error: String(error)}));
        """
    )
    assert hover_metrics["root"] == "~", hover_metrics
    assert hover_metrics["manual"] is True, hover_metrics
    assert hover_metrics["selected"] == ["/home/test/yolomux.dev/other/touched.js"], hover_metrics
    assert hover_metrics["expanded"] == pinned_before_refresh["expanded"], hover_metrics
    assert hover_metrics["activeTmux"] == "/home/test/yolomux.dev/src", hover_metrics
    assert hover_metrics["planSession"] == "5", hover_metrics
    assert hover_metrics["planRoot"] == "/home/test", hover_metrics
    assert hover_metrics["otherVisible"] is True, hover_metrics
    assert hover_metrics["otherRepoVisible"] is True, hover_metrics
    assert hover_metrics["otherExpanded"] == "true", hover_metrics
    focus_report_metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        terminals.get('6')?.term?._onData?.('\\x1b[I');
        requestAnimationFrame(() => requestAnimationFrame(() => {
          done({
            target: fileExplorerSessionFilesTargetSession(),
            activeTmux: activeTmuxDirectoryPath(),
            planSession: fileExplorerSyncPlan().session,
            planRoot: fileExplorerSyncPlan().root,
            payloadSession: fileExplorerSessionFilesState.payload?.session || '',
          });
        }));
        """
    )
    assert focus_report_metrics["target"] == "5", focus_report_metrics
    assert focus_report_metrics["activeTmux"] == "/home/test/yolomux.dev/src", focus_report_metrics
    assert focus_report_metrics["planSession"] == "5", focus_report_metrics
    assert focus_report_metrics["planRoot"] == "/home/test", focus_report_metrics
    assert focus_report_metrics["payloadSession"] == "5", focus_report_metrics
    passive_select_metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        selectSession('6');
        fetchSessionFiles({destination: 'finder', silent: true, force: true}).then(() => {
          scheduleFileExplorerActiveTabSync();
          requestAnimationFrame(() => requestAnimationFrame(() => {
            const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            done({
              root: document.querySelector('.file-explorer-path-inline')?.value || '',
              target: fileExplorerSessionFilesTargetSession(),
              payloadSession: fileExplorerSessionFilesState.payload?.session || '',
              activeTmux: activeTmuxDirectoryPath(),
              planRoot: fileExplorerSyncPlan().root,
              otherExpanded: tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other"]')?.getAttribute('aria-expanded') || '',
            });
          }));
        }).catch(error => done({error: String(error)}));
        """
    )
    assert passive_select_metrics["root"] == "~", passive_select_metrics
    assert passive_select_metrics["target"] == "5", passive_select_metrics
    assert passive_select_metrics["payloadSession"] == "5", passive_select_metrics
    assert passive_select_metrics["activeTmux"] == "/home/test/yolomux.dev/src", passive_select_metrics
    assert passive_select_metrics["planRoot"] == "/home/test", passive_select_metrics
    assert passive_select_metrics["otherExpanded"] == "true", passive_select_metrics
    typed_metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        document.getElementById('term-6')?.dispatchEvent(new KeyboardEvent('keydown', {key: 'x', bubbles: true}));
        terminals.get('6')?.term?._onData?.('x');
        setTimeout(() => {
          done({
            target: fileExplorerSessionFilesTargetSession(),
            activeTmux: activeTmuxDirectoryPath(),
            planSession: fileExplorerSyncPlan().session,
          });
        }, 0);
        """
    )
    assert typed_metrics["target"] == "6", typed_metrics
    assert typed_metrics["activeTmux"] == "/home/test/other.dev/other", typed_metrics
    assert typed_metrics["planSession"] == "6", typed_metrics
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.file-explorer-path-inline')?.value === '~'"
        )
    )
    browser.execute_script(
        """
        document.getElementById('term-5')?.dispatchEvent(new KeyboardEvent('keydown', {key: 'y', bubbles: true}));
        terminals.get('5')?.term?._onData?.('y');
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            return document.querySelector('.file-explorer-path-inline')?.value === '~'
              && tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other"]')?.getAttribute('aria-expanded') === 'true'
              && tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other/touched.js"]') !== null;
            """
        )
    )
    restored_metrics = browser.execute_script(
        """
        const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
        return {
          root: document.querySelector('.file-explorer-path-inline')?.value || '',
          target: fileExplorerSessionFilesTargetSession(),
          activeTmux: activeTmuxDirectoryPath(),
          planSession: fileExplorerSyncPlan().session,
          rememberedExpanded: tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other"]')?.getAttribute('aria-expanded') || '',
          childVisible: tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other/touched.js"]') !== null,
          expandedSet: Array.from(fileExplorerExpanded),
        };
        """
    )
    assert restored_metrics["root"] == "~", restored_metrics
    assert restored_metrics["target"] == "5", restored_metrics
    assert restored_metrics["activeTmux"] == "/home/test/yolomux.dev/src", restored_metrics
    assert restored_metrics["planSession"] == "5", restored_metrics
    assert restored_metrics["rememberedExpanded"] == "true", restored_metrics
    assert restored_metrics["childVisible"] is True, restored_metrics
    assert "/home/test/yolomux.dev/other" in restored_metrics["expandedSet"], restored_metrics


def test_sync_mode_explicit_pane_change_wins_over_stale_sync_open(browser, tmp_path):
    fs_entries = {
        "/home/test": [{"name": "yolomux.dev1", "kind": "dir"}, {"name": "yolomux.dev2", "kind": "dir"}],
        "/home/test/yolomux.dev1": [{"name": "src", "kind": "dir"}],
        "/home/test/yolomux.dev1/src": [{"name": "main.js", "kind": "file"}],
        "/home/test/yolomux.dev2": [{"name": "src", "kind": "dir"}],
        "/home/test/yolomux.dev2/src": [{"name": "main.js", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,5,6&layout=row@35(slot1,row@50(left,slot2))&tabs=slot1:files;left:5;slot2:6",
        settings={"file_explorer": {"root_mode": "sync"}},
        sessions=["5", "6"],
        transcript_sessions={
            "5": {"current_path": "/home/test/yolomux.dev1/src", "git_root": "/home/test/yolomux.dev1"},
            "6": {"current_path": "/home/test/yolomux.dev2/src", "git_root": "/home/test/yolomux.dev2"},
        },
        session_files_payload={"session": "5", "loaded": True, "errors": [], "repos": [{"repo": "/home/test/yolomux.dev1"}], "files": []},
        session_files_payloads={
            "5": {"session": "5", "loaded": True, "errors": [], "repos": [{"repo": "/home/test/yolomux.dev1"}], "files": []},
            "6": {"session": "6", "loaded": True, "errors": [], "repos": [{"repo": "/home/test/yolomux.dev2"}], "files": []},
        },
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return ['/home/test', '~'].includes(document.querySelector('.file-explorer-path-inline')?.value)
              && document.getElementById('panel-5') !== null
              && document.getElementById('panel-6') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        openFileExplorerAt('/home/test', {syncSelection: true}).then(() => {
          invalidateFileExplorerRoots(['/home/test/yolomux.dev1', '/home/test/yolomux.dev2']);
          resetFileExplorerAppliedSyncPlan();
          const realFetchDirectory = fetchDirectory;
          let releaseDev1 = null;
          fetchDirectory = (path, options = {}) => {
            if (path === '/home/test/yolomux.dev1' && !releaseDev1) {
              return new Promise(resolve => {
                releaseDev1 = () => realFetchDirectory(path, options).then(resolve);
              });
            }
            return realFetchDirectory(path, options);
          };
          setFocusedTerminal('5', {userInitiated: true});
          let holdAttempts = 0;
          const waitForHeldSync = () => {
            if (!releaseDev1) {
              holdAttempts += 1;
              if (holdAttempts > 180) {
                done({
                  error: 'dev1 sync did not reach held fetch',
                  root: document.querySelector('.file-explorer-path-inline')?.value || '',
                  explicitSession: fileExplorerExplicitSyncSessionTarget(),
                  plan5: fileExplorerSyncPlan('5').root,
                  plan6: fileExplorerSyncPlan('6').root,
                });
                return;
              }
              requestAnimationFrame(waitForHeldSync);
              return;
            }
            setFocusedTerminal('6', {userInitiated: true});
            releaseDev1();
            let dev2Attempts = 0;
            const waitForDev2 = () => {
              dev2Attempts += 1;
              if (dev2Attempts > 180) {
                done({
                  error: 'dev2 sync did not win',
                  root: document.querySelector('.file-explorer-path-inline')?.value || '',
                  explicitSession: fileExplorerExplicitSyncSessionTarget(),
                  manual: fileExplorerManualSelectionActive,
                  planRoot: fileExplorerSyncPlan('6').root,
                });
                return;
              }
            const root = document.querySelector('.file-explorer-path-inline')?.value || '';
            const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            if (root === '~' && tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev2/src"]')) {
              done({
                root,
                explicitSession: fileExplorerExplicitSyncSessionTarget(),
                manual: fileExplorerManualSelectionActive,
                planRoot: fileExplorerSyncPlan('6').root,
              });
              return;
            }
            requestAnimationFrame(waitForDev2);
            };
            requestAnimationFrame(waitForDev2);
          };
          requestAnimationFrame(waitForHeldSync);
        }).catch(error => done({error: String(error)}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics == {
        "root": "~",
        "explicitSession": "6",
        "manual": False,
        "planRoot": "/home/test",
    }, metrics


def test_sync_mode_session_switch_uses_transcript_root_before_session_files_refresh(browser, tmp_path):
    fs_entries = {
        "/home/test": [{"name": "yolomux.dev1", "kind": "dir"}, {"name": "yolomux.dev2", "kind": "dir"}],
        "/home/test/yolomux.dev1": [{"name": "src", "kind": "dir"}],
        "/home/test/yolomux.dev1/src": [{"name": "main.js", "kind": "file"}],
        "/home/test/yolomux.dev2": [{"name": "src", "kind": "dir"}],
        "/home/test/yolomux.dev2/src": [{"name": "main.js", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,5,6&layout=row@35(slot1,row@50(left,slot2))&tabs=slot1:files;left:5;slot2:6",
        settings={"file_explorer": {"root_mode": "sync"}},
        sessions=["5", "6"],
        transcript_sessions={
            "5": {"current_path": "/home/test/yolomux.dev1/src", "git_root": "/home/test/yolomux.dev1"},
            "6": {"current_path": "/home/test/yolomux.dev2/src", "git_root": "/home/test/yolomux.dev2"},
        },
        session_files_payload={"session": "5", "loaded": True, "errors": [], "repos": [{"repo": "/home/test/yolomux.dev1"}], "files": []},
        session_files_payloads={
            "5": {"session": "5", "loaded": True, "errors": [], "repos": [{"repo": "/home/test/yolomux.dev1"}], "files": []},
            "6": {"session": "6", "loaded": True, "errors": [], "repos": [{"repo": "/home/test/yolomux.dev2"}], "files": []},
        },
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.file-explorer-path-inline') !== null
              && document.querySelector('#panel-__finder__ .file-explorer-tree-panel') !== null
              && document.getElementById('panel-5') !== null
              && document.getElementById('panel-6') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const {frame} = window.__yolomuxTestHelpers;
        (async () => {
          try {
            await openFileExplorerAt('/home/test/yolomux.dev1', {syncSelection: true});
            resetFileExplorerAppliedSyncPlan();
            fileExplorerSessionFilesCache.delete(sessionFilesCacheKey('6'));
            await frame();
            const originalFetch = window.fetch;
            let heldFetch = false;
            let releaseFetch = null;
            window.fetch = (input, options = {}) => {
              const url = new URL(String(input), location.href);
              if (url.pathname === '/api/session-files' && url.searchParams.get('session') === '6') {
                heldFetch = true;
                return new Promise(resolve => {
                  releaseFetch = () => originalFetch(input, options).then(resolve);
                });
              }
              return originalFetch(input, options);
            };
            const changed = noteFileExplorerChangesSessionInteraction('6');
            let attempts = 0;
            const wait = () => {
              attempts += 1;
              const root = document.querySelector('.file-explorer-path-inline')?.value || '';
              if (heldFetch && root === '~') {
                const result = {
                  changed,
                  heldFetch,
                  root,
                  explicitSession: fileExplorerExplicitSyncSessionTarget(),
                  payloadSession: fileExplorerSessionFilesState.payload?.session || '',
                  loading: fileExplorerSessionFilesState.loading,
                  planRoot: fileExplorerSyncPlan('6').root,
                };
                window.fetch = originalFetch;
                releaseFetch?.();
                done(result);
                return;
              }
              if (attempts > 180) {
                window.fetch = originalFetch;
                releaseFetch?.();
                done({
                  error: 'session switch waited for session-files instead of transcript root',
                  changed,
                  heldFetch,
                  root,
                  explicitSession: fileExplorerExplicitSyncSessionTarget(),
                  payloadSession: fileExplorerSessionFilesState.payload?.session || '',
                  loading: fileExplorerSessionFilesState.loading,
                  planRoot: fileExplorerSyncPlan('6').root,
                });
                return;
              }
              requestAnimationFrame(wait);
            };
            requestAnimationFrame(wait);
          } catch (error) {
            done({error: String(error)});
          }
        })();
        """
    )
    assert "error" not in metrics, metrics
    assert metrics == {
        "changed": True,
        "heldFetch": True,
        "root": "~",
        "explicitSession": "6",
        "payloadSession": "6",
        "loading": True,
        "planRoot": "/home/test",
    }, metrics


def test_sync_mode_session_switch_uses_cached_payload_before_refresh(browser, tmp_path):
    fs_entries = {
        "/home/test": [{"name": "yolomux.dev1", "kind": "dir"}, {"name": "yolomux.dev2", "kind": "dir"}],
        "/home/test/yolomux.dev1": [{"name": "src", "kind": "dir"}],
        "/home/test/yolomux.dev1/src": [{"name": "main.js", "kind": "file"}],
        "/home/test/yolomux.dev2": [{"name": "src", "kind": "dir"}],
        "/home/test/yolomux.dev2/src": [{"name": "main.js", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,5,6&layout=row@35(slot1,row@50(left,slot2))&tabs=slot1:files;left:5;slot2:6",
        settings={"file_explorer": {"root_mode": "sync"}},
        sessions=["5", "6"],
        transcript_sessions={
            "5": {"current_path": "/home/test/yolomux.dev1/src", "git_root": "/home/test/yolomux.dev1"},
            "6": {"current_path": "/home/test/yolomux.dev2/src", "git_root": "/home/test/yolomux.dev2"},
        },
        session_files_payload={"session": "5", "loaded": True, "errors": [], "repos": [{"repo": "/home/test/yolomux.dev1"}], "files": []},
        session_files_payloads={
            "5": {"session": "5", "loaded": True, "errors": [], "repos": [{"repo": "/home/test/yolomux.dev1"}], "files": []},
            "6": {"session": "6", "loaded": True, "errors": [], "repos": [{"repo": "/home/test/yolomux.dev2"}], "files": []},
        },
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.file-explorer-path-inline') !== null
              && document.querySelector('#panel-__finder__ .file-explorer-tree-panel') !== null
              && document.getElementById('panel-5') !== null
              && document.getElementById('panel-6') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const {frame} = window.__yolomuxTestHelpers;
        (async () => {
          try {
            await openFileExplorerAt('/home/test/yolomux.dev1', {syncSelection: true});
            resetFileExplorerAppliedSyncPlan();
            await frame();
            const payload6 = {session: '6', loaded: true, errors: [], refs_by_repo: {}, repos: [{repo: '/home/test/yolomux.dev2'}], files: []};
            fileExplorerSessionFilesCache.set(sessionFilesCacheKey('6'), {
              payload: payload6,
              signature: sessionFilesPayloadSignatureForPayload(payload6),
            });
            const originalFetch = window.fetch;
            let heldFetch = false;
            let releaseFetch = null;
            window.fetch = (input, options = {}) => {
              const url = new URL(String(input), location.href);
              if (url.pathname === '/api/session-files' && url.searchParams.get('session') === '6') {
                heldFetch = true;
                return new Promise(resolve => {
                  releaseFetch = () => originalFetch(input, options).then(resolve);
                });
              }
              return originalFetch(input, options);
            };
            const changed = noteFileExplorerChangesSessionInteraction('6');
            let attempts = 0;
            const wait = () => {
              attempts += 1;
              const root = document.querySelector('.file-explorer-path-inline')?.value || '';
              if (heldFetch && root === '~') {
                const result = {
                  changed,
                  heldFetch,
                  root,
                  explicitSession: fileExplorerExplicitSyncSessionTarget(),
                  payloadSession: fileExplorerSessionFilesState.payload?.session || '',
                  loading: fileExplorerSessionFilesState.loading,
                  planRoot: fileExplorerSyncPlan('6').root,
                };
                window.fetch = originalFetch;
                releaseFetch?.();
                done(result);
                return;
              }
              if (attempts > 180) {
                window.fetch = originalFetch;
                releaseFetch?.();
                done({
                  error: 'cached session switch did not open cached root before refresh resolved',
                  changed,
                  heldFetch,
                  root,
                  explicitSession: fileExplorerExplicitSyncSessionTarget(),
                  payloadSession: fileExplorerSessionFilesState.payload?.session || '',
                  loading: fileExplorerSessionFilesState.loading,
                  planRoot: fileExplorerSyncPlan('6').root,
                });
                return;
              }
              requestAnimationFrame(wait);
            };
            requestAnimationFrame(wait);
          } catch (error) {
            done({error: String(error)});
          }
        })();
        """
    )
    assert "error" not in metrics, metrics
    assert metrics == {
        "changed": True,
        "heldFetch": True,
        "root": "~",
        "explicitSession": "6",
        "payloadSession": "6",
        "loading": False,
        "planRoot": "/home/test",
    }, metrics


def test_fixed_finder_reveals_clicked_editor_file_without_changing_root(browser, tmp_path):
    fs_entries = {
        "/home/test": [{"name": "repo-a", "kind": "dir"}, {"name": "repo-b", "kind": "dir"}],
        "/home/test/repo-a": [{"name": "src", "kind": "dir"}],
        "/home/test/repo-a/src": [{"name": "a.md", "kind": "file"}],
        "/home/test/repo-b": [{"name": "other", "kind": "dir"}],
        "/home/test/repo-b/other": [{"name": "b.md", "kind": "file"}],
    }
    item_a = "file:/home/test/repo-a/src/a.md"
    item_b = "file:/home/test/repo-b/other/b.md"
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files" + "&layout=row@35(slot1,row@50(left,slot2))" + f"&tabs=slot1:files;left:{item_a};slot2:{item_b}",
        settings={"general": {"auto_focus": True}, "file_explorer": {"root_mode": "fixed"}},
        sessions=[],
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return ['/home/test', '~'].includes(document.querySelector('.file-explorer-path-inline')?.value)
              && document.querySelector('.file-editor-panel[data-file-path="/home/test/repo-a/src/a.md"]')
              && document.querySelector('.file-editor-panel[data-file-path="/home/test/repo-b/other/b.md"]');
            """
        )
    )
    click_visible_selector(browser, '.file-editor-panel[data-file-path="/home/test/repo-a/src/a.md"]')
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.file-explorer-panel .file-explorer-tree-panel .file-tree-row[data-path="/home/test/repo-a/src/a.md"]') !== null;
            """
        )
    )
    click_a_metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
          done({
            root: document.querySelector('.file-explorer-path-inline')?.value || '',
            mode: fileExplorerRootModeValue(),
            syncPressed: document.querySelector('.file-explorer-root-mode-toggle-panel')?.getAttribute('aria-pressed') || '',
            repoAExpanded: tree.querySelector('.file-tree-row[data-path="/home/test/repo-a"]')?.getAttribute('aria-expanded') || '',
            fileAVisible: tree.querySelector('.file-tree-row[data-path="/home/test/repo-a/src/a.md"]') !== null,
          });
        }));
        """
    )
    assert click_a_metrics == {
        "root": "~",
        "mode": "fixed",
        "syncPressed": "false",
        "repoAExpanded": "true",
        "fileAVisible": True,
    }, click_a_metrics
    move_to_visible_selector(browser, '.file-editor-panel[data-file-path="/home/test/repo-b/other/b.md"]')
    hover_metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        scheduleFileExplorerActiveTabSync();
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
          done({
            root: document.querySelector('.file-explorer-path-inline')?.value || '',
            repoBExpanded: tree.querySelector('.file-tree-row[data-path="/home/test/repo-b"]')?.getAttribute('aria-expanded') || '',
            otherVisible: tree.querySelector('.file-tree-row[data-path="/home/test/repo-b/other"]') !== null,
          });
        }));
        """
    )
    assert hover_metrics["root"] == "~", hover_metrics
    assert hover_metrics["repoBExpanded"] == "false", hover_metrics
    assert hover_metrics["otherVisible"] is False, hover_metrics
    click_visible_selector(browser, '.file-editor-panel[data-file-path="/home/test/repo-b/other/b.md"]')
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.file-explorer-panel .file-explorer-tree-panel .file-tree-row[data-path="/home/test/repo-b/other/b.md"]') !== null;
            """
        )
    )
    click_b_metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
          done({
            root: document.querySelector('.file-explorer-path-inline')?.value || '',
            mode: fileExplorerRootModeValue(),
            syncPressed: document.querySelector('.file-explorer-root-mode-toggle-panel')?.getAttribute('aria-pressed') || '',
            repoBExpanded: tree.querySelector('.file-tree-row[data-path="/home/test/repo-b"]')?.getAttribute('aria-expanded') || '',
            fileBVisible: tree.querySelector('.file-tree-row[data-path="/home/test/repo-b/other/b.md"]') !== null,
          });
        }));
        """
    )
    assert click_b_metrics == {
        "root": "~",
        "mode": "fixed",
        "syncPressed": "false",
        "repoBExpanded": "true",
        "fileBVisible": True,
    }, click_b_metrics


def test_sync_mode_empty_session_opens_home_not_stale_payload(browser, tmp_path):
    session_files_payload = {
        "session": "old",
        "loaded": True,
        "errors": [],
        "repos": [{"repo": "/home/test/stale"}],
        "files": [{"repo": "/home/test/stale", "path": "old.js", "abs_path": "/home/test/stale/old.js"}],
    }
    fs_entries = {
        "/home/test": [{"name": "stale", "kind": "dir"}, {"name": "fresh.txt", "kind": "file"}],
        "/home/test/stale": [{"name": "old.js", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "sync"}},
        transcript_current_path="",
        session_files_payload=session_files_payload,
        fs_entries=fs_entries,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return ['/home/test', '~'].includes(document.querySelector('.file-explorer-path-inline')?.value)
              && Array.from(document.querySelectorAll('.file-tree-row')).some(row => row.dataset.path === '/home/test/stale');
            """
        )
    )
    metrics = browser.execute_script(
        """
        const rows = Array.from(document.querySelectorAll('.file-tree-row')).map(row => ({
          path: row.dataset.path,
          hasRepo: row.classList.contains('file-tree-row--session-repo'),
          hasTouched: row.classList.contains('file-tree-row--session-touched'),
        }));
        return {
          errors: jsDebugFailureEvents('error'),
          rejections: jsDebugFailureEvents('rejection'),
          root: document.querySelector('.file-explorer-path-inline')?.value || '',
          rows,
          fetchedPaths: window.__bootFetches.filter(item => item.path === '/api/fs/list' || item.path === '/api/fs/fast/list' || item.path === '/api/fs/batch').map(item => item.path),
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["root"] == "~", metrics
    assert "/home/test/stale" in {row["path"] for row in metrics["rows"]}, metrics
    assert not any(row["hasRepo"] or row["hasTouched"] for row in metrics["rows"]), metrics


def test_finder_panel_paints_initial_path_under_push_without_sync(browser, tmp_path):
    # Regression for the reported bug: a Finder PANEL whose tree has not yet painted must list the
    # current path (~ -> home) on a no-entries refresh under the live client-push transport,
    # regardless of Sync. createFileExplorerPanel ends with `refreshFileExplorerPanelTree(panel)`
    # (no entries); before the fix that call early-returned under clientPushCanSupplyData() before
    # the first paint (deferring to a filesystem push that only covers registered watch roots), so
    # the tree stayed empty until Sync was pressed. This test drives that exact gated path: it
    # empties the tree, then calls the no-entries panel refresh with push active and Sync OFF, and
    # asserts the rows render. It fails against the old early-return and passes with the first-paint fix.
    fs_entries = {
        "/home/test": [{"name": "alpha", "kind": "dir"}, {"name": "readme.txt", "kind": "file"}],
        "/home/test/alpha": [{"name": "inner.js", "kind": "file"}],
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?layout=left&tabs=left:files",
        settings={"file_explorer": {"root_mode": "fixed"}},
        transcript_current_path="",
        fs_entries=fs_entries,
    )
    # Wait for the panel to exist, then reproduce an un-painted panel-refresh under push.
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return !!document.querySelector('.file-explorer-finder .file-explorer-tree-panel');"
        )
    )
    setup = browser.execute_script(
        """
        const panel = document.querySelector('.file-explorer-finder');
        const tree = panel.querySelector('.file-explorer-tree-panel');
        tree.innerHTML = '';  // simulate a freshly-created, not-yet-painted panel tree
        const pushActive = typeof clientPushCanSupplyData === 'function' && clientPushCanSupplyData() === true;
        // Drive the exact gated call from createFileExplorerPanel (no entries, no force).
        refreshFileExplorerPanelTree(panel);
        return {
          pushActive,
          rootMode: typeof fileExplorerRootMode !== 'undefined' ? fileExplorerRootMode : null,
          emptiedTo: tree.childElementCount,
        };
        """
    )
    assert setup["pushActive"] is True, setup
    assert setup["rootMode"] == "fixed", setup
    assert setup["emptiedTo"] == 0, setup
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return Array.from(document.querySelectorAll('.file-explorer-finder .file-tree-row')).some("
            "row => row.dataset.path === '/home/test/alpha' || row.dataset.path === '/home/test/readme.txt');"
        )
    )
    metrics = browser.execute_script(
        """
        return {
          errors: jsDebugFailureEvents('error'),
          rejections: jsDebugFailureEvents('rejection'),
          root: document.querySelector('.file-explorer-finder .file-explorer-path-inline')?.value || '',
          rows: Array.from(document.querySelectorAll('.file-explorer-finder .file-tree-row')).map(row => row.dataset.path),
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["root"] == "~", metrics
    row_paths = set(metrics["rows"])
    assert "/home/test/alpha" in row_paths, metrics
    assert "/home/test/readme.txt" in row_paths, metrics
