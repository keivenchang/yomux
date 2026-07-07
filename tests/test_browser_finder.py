from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401

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
          done({folderButtons, fileButtons, errors: window.__bootErrors, rejections: window.__bootRejections});
        })().catch(error => done({error: String(error), stack: error?.stack || '', errors: window.__bootErrors, rejections: window.__bootRejections}));
        """
    )
    assert not metrics.get("error"), metrics
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    folder_zip = [button for button in metrics["folderButtons"] if button["text"] == "Zip & download"]
    assert folder_zip == [{"text": "Zip & download", "disabled": False}], metrics
    assert all(button["text"] != "Zip & download" for button in metrics["fileButtons"]), metrics


@pytest.mark.parametrize("legacy_token", ["changes", "__changes__"])
def test_legacy_changes_url_opens_finder_diff_mode(browser, tmp_path, legacy_token):
    load_live_runtime_boot_fixture(browser, tmp_path, f"?layout=left&tabs=left:{legacy_token}")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('#panel-__files__')?.dataset.fileExplorerMode === 'diff'"
        )
    )
    metrics = browser.execute_script(
        """
        const panel = document.querySelector('#panel-__files__');
        const filesButton = panel.querySelector('[data-file-explorer-mode-set="files"]');
        const diffButton = panel.querySelector('[data-file-explorer-mode-set="diff"]');
        const tree = panel.querySelector('.file-explorer-tree-panel');
        const changes = panel.querySelector('.file-explorer-changes-panel');
        const newFile = panel.querySelector('[data-file-explorer-new-file]');
        const visible = selector => Array.from(panel.querySelectorAll(selector)).filter(node => node.getClientRects().length > 0);
        return {
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
          panelConnected: panel?.isConnected === true,
          bodyDiff: document.body.classList.contains('file-explorer-mode-diff'),
          bodyFiles: document.body.classList.contains('file-explorer-mode-files'),
          panelMode: panel?.dataset.fileExplorerMode,
          filesPressed: filesButton?.getAttribute('aria-pressed'),
          diffPressed: diffButton?.getAttribute('aria-pressed'),
          modeTexts: Array.from(panel.querySelectorAll('[data-file-explorer-mode-set]')).map(button => button.textContent.trim().replace(/\\s+/g, ' ')),
          treeDisplay: getComputedStyle(tree).display,
          changesDisplay: getComputedStyle(changes).display,
          titleCount: panel.querySelectorAll('.file-explorer-panel-title').length,
          newFileDisplay: getComputedStyle(newFile).display,
          visibleRootControls: visible('.file-explorer-root-mode-toggle-panel').length,
          visibleSessionSelects: visible('[data-session-files-session]').length,
          visibleSortSelects: visible('[data-session-files-sort]').length,
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
    assert metrics["bodyDiff"]
    assert not metrics["bodyFiles"]
    assert metrics["panelMode"] == "diff"
    assert metrics["filesPressed"] == "false"
    assert metrics["diffPressed"] == "true"
    assert metrics["modeTexts"] == ["Finder", "Differ", "Tabber"]
    assert metrics["treeDisplay"] == "none"
    assert metrics["changesDisplay"] != "none"
    assert metrics["titleCount"] == 0
    assert metrics["newFileDisplay"] == "none"
    assert metrics["visibleRootControls"] == 0, metrics
    assert metrics["visibleSessionSelects"] == 1, metrics
    assert metrics["visibleSortSelects"] == 1, metrics
    assert metrics["visibleDateButtons"] == 1, metrics
    assert metrics["visibleReloadButtons"] == 1, metrics
    assert all(text != "1 1" for text in metrics["sessionOptionTexts"]), metrics
    assert metrics["sessionFilesFetches"] >= 1


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
                "return Array.from(document.querySelectorAll('#panel-__files__ .file-tree-dir-count:not([hidden])')).some(node => node.textContent.trim() === '2')"
        )
    )
    metrics = browser.execute_script(
        """
        const panel = document.querySelector('#panel-__files__');
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
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
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
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
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
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
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
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test/dynamo'
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
          background: getComputedStyle(row).backgroundColor,
          nameWeight: Number(getComputedStyle(row.querySelector(':scope > .file-tree-name')).fontWeight),
        }));
        return {
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
          root: document.querySelector('.file-explorer-path-inline')?.value || '',
          rows,
          fetchedPaths: window.__bootFetches.filter(item => item.path === '/api/fs/list' || item.path === '/api/fs/batch').length,
          plan: fileExplorerSyncPlan('1'),
          expandedSet: Array.from(fileExplorerExpanded),
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["root"] == "/home/test/dynamo", metrics
    row_paths = {row["path"] for row in metrics["rows"]}
    assert {"/home/test/dynamo/repo-a", "/home/test/dynamo/repo-a/src", "/home/test/dynamo/repo-b", "/home/test/dynamo/repo-b/lib"}.issubset(row_paths), metrics
    expanded_paths = {row["path"] for row in metrics["rows"] if row["expanded"]}
    assert {"/home/test/dynamo/repo-a", "/home/test/dynamo/repo-b"}.issubset(expanded_paths), metrics
    assert "/home/test/dynamo/repo-a/src" not in expanded_paths, metrics
    assert "/home/test/dynamo/repo-b/lib" not in expanded_paths, metrics
    rows_by_path = {row["path"]: row for row in metrics["rows"]}
    for path in ["/home/test/dynamo/repo-a", "/home/test/dynamo/repo-b"]:
        assert "file-tree-row--sync-expanded" in rows_by_path[path]["classes"], metrics
        assert "file-tree-row--session-repo" not in rows_by_path[path]["classes"], metrics
        assert "file-tree-row--session-touched" not in rows_by_path[path]["classes"], metrics
        assert rows_by_path[path]["background"] == "rgba(0, 0, 0, 0)", metrics
        assert rows_by_path[path]["nameWeight"] >= 700, metrics
    for path in ["/home/test/dynamo/repo-a/src", "/home/test/dynamo/repo-b/lib"]:
        assert "file-tree-row--sync-expanded" not in rows_by_path[path]["classes"], metrics
        assert "file-tree-row--session-repo" not in rows_by_path[path]["classes"], metrics
        assert "file-tree-row--session-touched" not in rows_by_path[path]["classes"], metrics
        assert "file-tree-row--changed-ancestor" in rows_by_path[path]["classes"], metrics
        assert rows_by_path[path]["background"] == "rgba(0, 0, 0, 0)", metrics
        assert rows_by_path[path]["nameWeight"] >= 700, metrics
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
              root: document.querySelector('.file-explorer-path-inline')?.value || '',
            });
          }));
        });
        """
    )
    assert manual_collapse["root"] == "/home/test/dynamo", manual_collapse
    assert manual_collapse["expanded"] == "false", manual_collapse
    assert manual_collapse["childVisible"] is False, manual_collapse
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
              manualCollapsed: Array.from(fileExplorerSyncManualCollapsedPaths),
            });
          }));
        }).catch(error => done({error: String(error)}));
        """,
        updated_session_files_payload,
    )
    assert payload_change["root"] == "/home/test/dynamo", payload_change
    assert payload_change["plan"]["expandPaths"] == ["/home/test/dynamo/repo-a", "/home/test/dynamo/repo-b", "/home/test/dynamo/repo-c"], payload_change
    assert payload_change["repoAExpanded"] == "false", payload_change
    assert payload_change["repoAChildVisible"] is False, payload_change
    assert payload_change["repoCExpanded"] == "true", payload_change
    assert payload_change["repoCChildVisible"] is True, payload_change
    assert "/home/test/dynamo/repo-a" in payload_change["manualCollapsed"], payload_change
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
            "return document.querySelector('.file-explorer-path-inline')?.value === '/home/test' && document.getElementById('panel-1') !== null;"
        )
    )
    click_visible_panel(browser, "panel-1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            const rows = new Map(Array.from(tree?.querySelectorAll('.file-tree-row') || []).map(row => [row.dataset.path, row]));
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test/dynamo'
              && rows.get('/home/test/dynamo/repo-a')?.getAttribute('aria-expanded') === 'true'
              && rows.get('/home/test/dynamo/repo-b')?.getAttribute('aria-expanded') === 'true';
            """
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
        "/home/test/yolomux.dev3/docs/preview-samples": [
            {"name": "03-mixed.md", "kind": "file", "size": 128, "mtime_ns": 1781300000000000000},
        ],
    }
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1&layout=left&tabs=left:1", fs_entries=fs_entries)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        fetchFileEntryStatus('/home/test/yolomux.dev3/docs/preview-samples/03-mixed.md')
          .then(result => done({
            entry: result.entry,
            missing: result.missing,
            lookupError: result.error,
            network: result.network,
            fsFetches: window.__bootFetches
              .filter(item => item.path === '/api/fs/list' || item.path === '/api/fs/batch')
              .map(item => ({path: item.path, body: item.body, search: item.search})),
          }))
          .catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
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
        "files": [
            {
                "repo": "/home/test/dynamo/frontend-crates",
                "path": "conformance/utils/tests/parity/reasoning/table.py",
                "abs_path": path,
            },
        ],
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
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
              && document.querySelector('.file-editor-panel[data-file-path="/home/test/dynamo/frontend-crates/conformance/utils/tests/parity/reasoning/table.py"]');
            """
        )
    )
    click_visible_selector(browser, f'.file-editor-panel[data-file-path="{path}"]')
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test/dynamo/frontend-crates'
              && tree.querySelector('.file-tree-row[data-path="/home/test/dynamo/frontend-crates/conformance/utils/tests/parity/reasoning/table.py"]') !== null;
            """
        )
    )
    metrics = browser.execute_script(
        """
        const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
        const path = '/home/test/dynamo/frontend-crates/conformance/utils/tests/parity/reasoning/table.py';
        return {
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
          root: document.querySelector('.file-explorer-path-inline')?.value || '',
          mode: fileExplorerRootModeValue(),
          plan: fileExplorerSyncPlanForFile(path),
          fileVisible: tree.querySelector(`.file-tree-row[data-path="${path}"]`) !== null,
          fileCurrent: tree.querySelector(`.file-tree-row[data-path="${path}"]`)?.classList.contains('current-file') || false,
          expandedSet: Array.from(fileExplorerExpanded),
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["root"] == "/home/test/dynamo/frontend-crates", metrics
    assert metrics["mode"] == "sync", metrics
    assert metrics["plan"]["root"] == "/home/test/dynamo/frontend-crates", metrics
    assert metrics["plan"]["session"] == "2", metrics
    assert metrics["plan"]["expandPaths"] == [path], metrics
    assert metrics["fileVisible"] is True, metrics
    assert metrics["fileCurrent"] is True, metrics
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
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
              && document.getElementById('panel-1') !== null;
            """
        )
    )
    click_visible_panel(browser, "panel-1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
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
    assert collapsed_parent["root"] == "/home/test", collapsed_parent
    assert set(collapsed_parent["plan"]["expandPaths"]) == {"/home/test/dynamo/repo-a", "/home/test/yolomux.dev"}, collapsed_parent
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
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
              && document.getElementById('panel-5') !== null;
            """
        )
    )
    click_visible_panel(browser, "panel-5")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test/yolomux.dev'
              && currentFileExplorerRoot() === '/home/test/yolomux.dev';
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const input = document.querySelector('.file-explorer-path-inline');
        const realFetchDirectory = fetchDirectory;
        let releaseTmp = null;
        fetchDirectory = (path, options = {}) => {
          if (path === '/tmp') {
            return new Promise(resolve => {
              releaseTmp = () => resolve([{name: 'scratch.txt', kind: 'file'}]);
            });
          }
          return realFetchDirectory(path, options);
        };
        input.focus();
        input.value = '/tmp';
        const openPromise = commitFileExplorerPathInput(input);
        requestAnimationFrame(() => {
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
        "currentRoot": "/home/test/yolomux.dev",
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
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
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
            });
          }
          if (path === '/tmp') {
            return new Promise(resolve => {
              releaseTmp = () => resolve([{name: 'scratch.txt', kind: 'file'}]);
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
        requestAnimationFrame(() => {
          if (!releaseDev2) {
            fetchDirectory = realFetchDirectory;
            done({error: 'stale session fetch was not started'});
            return;
          }
          input.focus();
          input.value = '/tmp';
          const manualOpenPromise = commitFileExplorerPathInput(input);
          requestAnimationFrame(() => {
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
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
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
        const realFetchFilesystemBatchItem = fetchFilesystemBatchItem;
        let releaseDev2 = null;
        clientPushConnectedForData = () => true;
        fileExplorerUserIsActive = () => false;
        fetchFilesystemBatchItem = (kind, path, options = {}) => {
          if (kind === 'list' && path === '/home/test/yolomux.dev2') {
            return new Promise(resolve => {
              releaseDev2 = () => resolve({entries: [{name: 'src', kind: 'dir'}]});
            });
          }
          return realFetchFilesystemBatchItem(kind, path, options);
        };
        const restore = () => {
          clientPushConnectedForData = realPushConnected;
          fileExplorerUserIsActive = realUserActive;
          fetchFilesystemBatchItem = realFetchFilesystemBatchItem;
        };
        selectSession('8002', {userInitiated: true}).then(() => {
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
              gitRoot: transcriptMetadataState.payload.sessions?.['8002']?.project?.git?.root || '',
              planRoot: fileExplorerSyncPlan('8002').root,
              searchingVisible: tree.querySelector('.file-tree-status-searching .file-tree-searching-dots') !== null,
              dev2Visible: tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev2/src"]') !== null,
              heldFetch: releaseDev2 !== null,
            };
            if (pending.root === '/home/test/yolomux.dev2' && pending.searchingVisible && releaseDev2) {
              releaseDev2();
              let finalAttempts = 0;
              const waitForFinal = () => {
                finalAttempts += 1;
                const finalRoot = document.querySelector('.file-explorer-path-inline')?.value || '';
                const finalTree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
                if (finalTree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev2/src"]') || finalAttempts > 180) {
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
                      gitRoot: transcriptMetadataState.payload.sessions?.['8002']?.project?.git?.root || '',
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
        "root": "/home/test/yolomux.dev2",
        "mode": "sync",
        "manual": False,
        "explicitSession": "8002",
        "targetSession": "8002",
        "currentPath": "/home/test/yolomux.dev2/src",
        "gitRoot": "/home/test/yolomux.dev2",
        "planRoot": "/home/test/yolomux.dev2",
        "searchingVisible": True,
        "dev2Visible": False,
        "heldFetch": True,
    }, metrics
    assert metrics["final"] == {
        "root": "/home/test/yolomux.dev2",
        "mode": "sync",
        "manual": False,
        "explicitSession": "8002",
        "targetSession": "8002",
        "currentPath": "/home/test/yolomux.dev2/src",
        "gitRoot": "/home/test/yolomux.dev2",
        "planRoot": "/home/test/yolomux.dev2",
        "syncInFlight": "",
        "appliedKey": "8002\x1f/home/test/yolomux.dev2",
        "searchingVisible": False,
        "dev2Visible": True,
    }, metrics


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
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
              && document.getElementById('panel-5') !== null;
            """
        )
    )
    sync_root_metrics = browser.execute_script(
        """
        return {
          mode: fileExplorerRootModeValue(),
          syncPressed: document.querySelector('.file-explorer-root-mode-toggle-panel')?.getAttribute('aria-pressed') || '',
          quickButtonCount: document.querySelectorAll('.file-explorer-quick-access-button').length,
          quickPanelCount: document.querySelectorAll('.file-explorer-quick-access-panel').length,
        };
        """
    )
    assert sync_root_metrics == {
        "mode": "sync",
        "syncPressed": "true",
        "quickButtonCount": 0,
        "quickPanelCount": 0,
    }, sync_root_metrics
    click_visible_panel(browser, "panel-5")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.file-explorer-path-inline')?.value === '/home/test/yolomux.dev'"
        )
    )
    manual_metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const input = document.querySelector('.file-explorer-path-inline');
        input.focus();
        input.value = '/home/test';
        commitFileExplorerPathInput(input).then(() => {
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
              quickButtonCount: document.querySelectorAll('.file-explorer-quick-access-button').length,
              quickPanelCount: document.querySelectorAll('.file-explorer-quick-access-panel').length,
            });
          }));
        }).catch(error => done({error: String(error)}));
        """
    )
    assert "error" not in manual_metrics, manual_metrics
    assert manual_metrics["root"] == "/home/test", manual_metrics
    assert manual_metrics["manual"] is True, manual_metrics
    assert manual_metrics["mode"] == "fixed", manual_metrics
    assert manual_metrics["explicitSession"] == "5", manual_metrics
    assert manual_metrics["planRoot"] == "/home/test/yolomux.dev", manual_metrics
    assert manual_metrics["syncPressed"] == "false", manual_metrics
    assert manual_metrics["quickButtonCount"] == 0, manual_metrics
    assert manual_metrics["quickPanelCount"] == 0, manual_metrics
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
              quickButtonCount: document.querySelectorAll('.file-explorer-quick-access-button').length,
            });
          }));
        """
    )
    assert unchanged_metrics == {
        "root": "/home/test",
        "mode": "fixed",
        "syncPressed": "false",
        "quickButtonCount": 0,
    }, unchanged_metrics
    browser.execute_script(
        "document.querySelector('.file-explorer-root-mode-toggle-panel')?.click();"
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test/yolomux.dev'
              && fileExplorerRootModeValue() === 'sync'
              && fileExplorerManualSelectionActive === false
              && document.querySelector('.file-explorer-root-mode-toggle-panel')?.getAttribute('aria-pressed') === 'true'
              && document.querySelectorAll('.file-explorer-quick-access-button').length === 0;
            """
        )
    )


def test_quick_access_roots_are_not_visible_in_finder_toolbar(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@35(slot1,left)&tabs=slot1:files;left:1",
        settings={"file_explorer": {"root_mode": "fixed", "quick_access_paths": ["~", "/*", "/tmp"]}},
        fs_entries={
            "/home/test": [{"name": "project", "kind": "dir"}],
            "/": [{"name": "tmp", "kind": "dir"}],
            "/tmp": [{"name": "scratch.txt", "kind": "file"}],
        },
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.file-explorer-panel .file-explorer-toolbar') !== null
              && document.querySelector('.file-explorer-path-inline')?.value === '/home/test';
            """
        )
    )
    metrics = browser.execute_script(
        """
        return {
          root: document.querySelector('.file-explorer-path-inline')?.value || '',
          mode: fileExplorerRootModeValue(),
          quickPanelCount: document.querySelectorAll('.file-explorer-quick-access-panel').length,
          quickButtonCount: document.querySelectorAll('.file-explorer-quick-access-button').length,
          rootLabel: displayQuickAccessPath('/'),
          starLabel: displayQuickAccessPath('/*'),
          starPath: expandQuickAccessPath('/*'),
          tmpLabel: displayQuickAccessPath('/tmp'),
        };
        """
    )
    assert metrics == {
        "root": "/home/test",
        "mode": "fixed",
        "quickPanelCount": 0,
        "quickButtonCount": 0,
        "rootLabel": "/*",
        "starLabel": "/*",
        "starPath": "/",
        "tmpLabel": "/tmp",
    }, metrics


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
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
              && document.getElementById('panel-5') !== null
              && document.getElementById('panel-6') !== null;
            """
        )
    )
    wait_for_visible_panel(browser, "panel-6")
    click_visible_panel(browser, "panel-5")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.file-explorer-path-inline')?.value === '/home/test/yolomux.dev'"
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
    move_to_visible_panel(browser, "panel-6")
    hover_metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        setFocusedTerminal('6');
        scheduleFileExplorerActiveTabSync();
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
          const plan = fileExplorerSyncPlan();
          done({
            root: document.querySelector('.file-explorer-path-inline')?.value || '',
            activeTmux: activeTmuxDirectoryPath(),
            planSession: plan.session,
            planRoot: plan.root,
            otherVisible: tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other"]') !== null,
            otherRepoVisible: tree.querySelector('.file-tree-row[data-path="/home/test/other.dev"]') !== null,
            otherExpanded: tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other"]')?.getAttribute('aria-expanded') || '',
          });
        }));
        """
    )
    assert hover_metrics["root"] == "/home/test/yolomux.dev", hover_metrics
    assert hover_metrics["activeTmux"] == "/home/test/yolomux.dev/src", hover_metrics
    assert hover_metrics["planSession"] == "5", hover_metrics
    assert hover_metrics["planRoot"] == "/home/test/yolomux.dev", hover_metrics
    assert hover_metrics["otherVisible"] is True, hover_metrics
    assert hover_metrics["otherRepoVisible"] is False, hover_metrics
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
    assert focus_report_metrics["planRoot"] == "/home/test/yolomux.dev", focus_report_metrics
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
    assert passive_select_metrics["root"] == "/home/test/yolomux.dev", passive_select_metrics
    assert passive_select_metrics["target"] == "5", passive_select_metrics
    assert passive_select_metrics["payloadSession"] == "5", passive_select_metrics
    assert passive_select_metrics["activeTmux"] == "/home/test/yolomux.dev/src", passive_select_metrics
    assert passive_select_metrics["planRoot"] == "/home/test/yolomux.dev", passive_select_metrics
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
            "return document.querySelector('.file-explorer-path-inline')?.value === '/home/test/other.dev'"
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
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test/yolomux.dev'
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
    assert restored_metrics["root"] == "/home/test/yolomux.dev", restored_metrics
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
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
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
            if (root === '/home/test/yolomux.dev2') {
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
        "root": "/home/test/yolomux.dev2",
        "explicitSession": "6",
        "manual": False,
        "planRoot": "/home/test/yolomux.dev2",
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
              && document.querySelector('#panel-__files__ .file-explorer-changes-panel') !== null
              && document.getElementById('panel-5') !== null
              && document.getElementById('panel-6') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
        (async () => {
          try {
            if (fileExplorerMode !== 'files') setFileExplorerMode('files', {force: true});
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
              if (heldFetch && root === '/home/test/yolomux.dev2') {
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
        "root": "/home/test/yolomux.dev2",
        "explicitSession": "6",
        "payloadSession": "6",
        "loading": True,
        "planRoot": "/home/test/yolomux.dev2",
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
              && document.querySelector('#panel-__files__ .file-explorer-changes-panel') !== null
              && document.getElementById('panel-5') !== null
              && document.getElementById('panel-6') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
        (async () => {
          try {
            if (fileExplorerMode !== 'files') setFileExplorerMode('files', {force: true});
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
              if (heldFetch && root === '/home/test/yolomux.dev2') {
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
        "root": "/home/test/yolomux.dev2",
        "explicitSession": "6",
        "payloadSession": "6",
        "loading": False,
        "planRoot": "/home/test/yolomux.dev2",
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
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
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
        "root": "/home/test",
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
    assert hover_metrics["root"] == "/home/test", hover_metrics
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
        "root": "/home/test",
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
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
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
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
          root: document.querySelector('.file-explorer-path-inline')?.value || '',
          rows,
          fetchedPaths: window.__bootFetches.filter(item => item.path === '/api/fs/list' || item.path === '/api/fs/batch').map(item => item.path),
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["root"] == "/home/test", metrics
    assert "/home/test/stale" in {row["path"] for row in metrics["rows"]}, metrics
    assert not any(row["hasRepo"] or row["hasTouched"] for row in metrics["rows"]), metrics
