from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401
from tests.browser_helpers.browser_console import assert_only_expected_browser_network_error
from tests.browser_helpers.browser_console import assert_only_expected_browser_warning
from tests.browser_helpers.browser_console import consume_only_expected_js_debug_api_error
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
from selenium.webdriver.common.by import By


def test_preview_frame_shared_chrome_keeps_html_and_pdf_height_policies(browser, tmp_path):
    css = app_css()
    page = tmp_path / "preview-frame-sizing.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"""<!doctype html><html><head><meta charset=utf-8><style>{css}</style><style>
        #host {{ display:flex; width:900px; height:600px; }}
        </style></head><body class="theme-dark editor-theme-dark"><div id="host"><iframe id="html" class="file-editor-html-preview"></iframe><iframe id="pdf" class="file-editor-pdf-preview"></iframe></div></body></html>""",
    )
    metrics = browser.execute_script(
        """const get = id => { const node = document.getElementById(id); const style = getComputedStyle(node); return {display: style.display, flex: style.flex, width: style.width, border: style.borderTopWidth, background: style.backgroundColor, colorScheme: style.colorScheme, minHeight: style.minHeight, height: style.height}; }; return {html: get('html'), pdf: get('pdf')};"""
    )
    for key in ("display", "flex", "width", "border", "background", "colorScheme"):
        assert metrics["html"][key] == metrics["pdf"][key], metrics
    assert metrics["html"]["minHeight"] == "0px", metrics
    assert metrics["pdf"]["minHeight"] == "420px", metrics
    assert metrics["html"]["height"] != metrics["pdf"]["height"], metrics

def test_diff_added_active_line_uses_same_fill_as_neighbor(browser, tmp_path):
    css = app_css()
    page = tmp_path / "diff-active-line-fill.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"""<!doctype html><html><head><meta charset=utf-8><style>{css}</style>
        <style>
        body {{ margin: 0; padding: 20px; background: #0f1115; color: #dfe6ef; }}
        .file-editor-diff-codemirror {{ width: 520px; background: var(--editor-scheme-bg); }}
        .cm-editor {{ background: var(--editor-scheme-bg); }}
        .cm-content {{ padding: 0; }}
        .cm-line {{ display: block; min-height: 22px; line-height: 22px; padding-inline: 8px; }}
        .cm-editor .cm-activeLine {{ background-color: rgba(255, 255, 255, 0.04); }}
        </style></head>
        <body class="theme-dark editor-theme-dark">
          <div id="host" class="file-editor-codemirror-panel file-editor-diff-codemirror">
            <div class="cm-editor"><div class="cm-content">
              <div id="added-a" class="cm-line cm-insertedLine">added one</div>
              <div id="added-active" class="cm-line cm-insertedLine cm-activeLine">added <span id="added-match" class="cm-searchMatch">active</span></div>
              <div id="added-selected" class="cm-line cm-insertedLine">added <span id="selected-match" class="cm-searchMatch cm-searchMatch-selected">selected</span></div>
              <div id="plain-active" class="cm-line cm-activeLine">plain active</div>
            </div></div>
          </div>
        </body></html>""",
    )
    metrics = browser.execute_script(
        """
        const {probePaint} = window.__yolomuxTestHelpers;
        function bg(selector) {
          return getComputedStyle(document.querySelector(selector)).backgroundColor;
        }
        const dark = {
          added: bg('#added-a'),
          activeAdded: bg('#added-active'),
          plainActive: bg('#plain-active'),
          matchBg: bg('#added-match'),
          matchColor: getComputedStyle(document.querySelector('#added-match')).color,
          matchShadow: getComputedStyle(document.querySelector('#added-match')).boxShadow,
          selectedBg: bg('#selected-match'),
          selectedShadow: getComputedStyle(document.querySelector('#selected-match')).boxShadow,
          addToken: getComputedStyle(document.querySelector('#host')).getPropertyValue('--diff-add-line-bg').trim(),
          removeToken: getComputedStyle(document.querySelector('#host')).getPropertyValue('--diff-remove-line-bg').trim(),
          expectedMatchColor: probePaint('color:var(--diff-search-match-fg)', document.querySelector('#host')).color,
        };
        document.body.classList.remove('editor-theme-dark');
        document.body.classList.add('editor-theme-light');
        const light = {
          added: bg('#added-a'),
          activeAdded: bg('#added-active'),
          matchBg: bg('#added-match'),
          matchColor: getComputedStyle(document.querySelector('#added-match')).color,
          matchShadow: getComputedStyle(document.querySelector('#added-match')).boxShadow,
          selectedBg: bg('#selected-match'),
          addToken: getComputedStyle(document.querySelector('#host')).getPropertyValue('--diff-add-line-bg').trim(),
          expectedMatchColor: probePaint('color:var(--diff-search-match-fg)', document.querySelector('#host')).color,
        };
        return {dark, light};
        """
    )
    assert metrics["dark"]["added"] == metrics["dark"]["activeAdded"], metrics
    assert metrics["dark"]["plainActive"] in ("rgba(0, 0, 0, 0)", "transparent"), metrics
    assert metrics["dark"]["matchBg"] != metrics["dark"]["added"], metrics
    assert metrics["dark"]["matchColor"] == metrics["dark"]["expectedMatchColor"], metrics
    assert metrics["dark"]["matchShadow"] != "none", metrics
    assert metrics["dark"]["selectedBg"] != metrics["dark"]["added"], metrics
    assert metrics["dark"]["selectedShadow"] != "none", metrics
    assert "transparent" not in metrics["dark"]["addToken"], metrics
    assert "transparent" not in metrics["dark"]["removeToken"], metrics
    assert metrics["light"]["added"] == metrics["light"]["activeAdded"], metrics
    assert metrics["light"]["matchBg"] != metrics["light"]["added"], metrics
    assert metrics["light"]["matchColor"] == metrics["light"]["expectedMatchColor"], metrics
    assert metrics["light"]["matchShadow"] != "none", metrics
    assert metrics["light"]["selectedBg"] != metrics["light"]["added"], metrics
    assert metrics["light"]["addToken"] == "#bfeac8", metrics


def test_readme_diff_waits_for_payload_before_building_codemirror(browser, tmp_path):
    css = app_css()
    bundle_uri = fixture_asset_url("static", "codemirror.js")
    strings = dict(app_english_strings())
    bootstrap = json.dumps(
        {
            "sessions": [],
            "availableAgents": [],
            "accessRole": "admin",
            "homePath": "/home/test",
            "repoRoot": str(REPO_ROOT),
            "maxSessionTabs": 99,
            "serverHostname": "test-host",
            "strings": {"en": strings},
            "codeMirrorAssetUrl": bundle_uri,
        },
        separators=(",", ":"),
    )
    path = str(REPO_ROOT / "README.md")
    original = subprocess.check_output(["git", "show", "HEAD:README.md"], cwd=REPO_ROOT, text=True)
    current = original.replace("# YOLOmux\n", "# YOLOmux — Deterministic test-only README diff line.\n", 1)
    diff = "diff --git a/README.md b/README.md\n" + "".join(difflib.unified_diff(
        original.splitlines(keepends=True),
        current.splitlines(keepends=True),
        fromfile="a/README.md",
        tofile="b/README.md",
    ))
    payload = json.dumps(
        {
            "diff": diff,
            "original": original,
            "working": current,
            "repo": str(REPO_ROOT),
            "relative_path": "README.md",
            "from_ref": "HEAD",
            "to_ref": "current",
            "untracked": False,
            "working_missing": False,
        },
        separators=(",", ":"),
    )
    page = tmp_path / "readme-diff-race.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"""<!doctype html><html><head><meta charset=utf-8><style>{css}</style><script src="{bundle_uri}"></script>
        <style>
        body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; background: #ffffff; }}
        #mount {{ width: 920px; height: 640px; }}
        .file-editor-panel {{ width: 920px; height: 640px; }}
        .file-editor-codemirror-panel {{ height: 100%; }}
        </style></head>
        <body class="theme-light theme-resolved-light editor-theme-light">
          <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
          <div id="mount"></div>
          <script>
            window.__readmeDiffPayload = {payload};
            window.__readmeDiffErrors = [];
            window.addEventListener('error', event => window.__readmeDiffErrors.push(event.message || String(event.error || event)));
            window.addEventListener('unhandledrejection', event => window.__readmeDiffErrors.push(String(event.reason || event)));
            function jsonResponse(payload) {{
              const text = JSON.stringify(payload);
              return Promise.resolve({{ok: true, status: 200, headers: {{get: () => 'application/json'}}, json: async () => payload, text: async () => text}});
            }}
            window.fetch = async input => {{
              const url = new URL(String(input), 'https://localhost');
              if (url.pathname === '/api/fs/diff') {{
                window.__readmeDiffRequest = url.search;
                return new Promise(resolve => {{
                  window.__resolveReadmeDiffFetch = () => resolve(jsonResponse(window.__readmeDiffPayload));
                }});
              }}
              return jsonResponse({{}});
            }};
          </script>
          <script>{app_bundle_before_boot_script()}</script>
          <script>
            window.__readmeDiffReady = (async () => {{
              const path = {json.dumps(path)};
              const current = {json.dumps(current).replace("</script", "<\\/script")};
              const item = fileEditorItemFor(path);
              setFileState(path, {{
                kind: 'text',
                content: current,
                original: current,
                dirty: false,
                language: 'markdown',
                gitRoot: {json.dumps(str(REPO_ROOT))},
                gitTracked: true,
                gitHasHistory: true,
                gitHistory: [{{sha: 'HEAD'}}],
                diffLoaded: false,
                diffUnavailable: false,
                diff: '',
                diffOriginal: '',
              }});
              setFileEditorViewMode(path, 'diff', item);
              addFileEditorTabItem(path, item);
              const panel = createFileEditorPanel(item);
              panel.id = 'readme-diff-panel';
              panel.classList.add('active-pane');
              panelNodes.set(item, panel);
              document.getElementById('mount').append(panel);
              const {{frame}} = window.__yolomuxTestHelpers;
              const waitFor = window.__yolomuxTestWaitFor;
              renderFileEditorPanel(panel, item);
              await waitFor(() => window.__resolveReadmeDiffFetch);
              await frame();
              await frame();
              const modeWhilePayloadUnresolved = panel._cmMode || '';
              const textWhilePayloadUnresolved = panel._cmView?.state?.doc?.toString?.() || '';
              window.__resolveReadmeDiffFetch();
              const diffLoadedWait = await waitFor(() => fileState.get(path)?.diffLoaded === true);
              const modeBeforeDiffBuildRelease = panel._cmMode || '';
              const finalWait = await waitFor(
                () => panel._cmMode === 'diff' && panel._cmView?.state?.doc?.toString?.().includes('Deterministic test-only README diff line.'),
                {{timeoutMs: 15000, description: 'README diff CodeMirror payload build'}}
              );
              const finalText = panel._cmView?.state?.doc?.toString?.() || '';
              const state = fileState.get(path) || {{}};
              return {{
                modeWhilePayloadUnresolved,
                textWhilePayloadUnresolved,
                diffLoadedWait,
                finalWait,
                modeBeforeDiffBuildRelease,
                finalMode: panel._cmMode || '',
                finalTextLength: finalText.length,
                expectedTextLength: current.length,
                deletedRows: panel.querySelectorAll('.cm-deletedLine').length,
                diffLoaded: state.diffLoaded === true,
                diffLoading: state.diffLoading === true,
                diffUnavailable: state.diffUnavailable === true,
                diffLength: String(state.diff || '').length,
                originalLength: String(state.diffOriginal || '').length,
                viewMode: editorViewModeFor(path, item),
                panelItems: filePanelItemsForPath(path),
                panelConnected: panel.isConnected === true,
                apiHasMergeView: Boolean(window.YOLOmuxCodeMirror?.MergeView),
                apiHasUnifiedMergeView: Boolean(window.YOLOmuxCodeMirror?.unifiedMergeView),
                plainFallback: panel._cmPlainFallback === true,
                statusText: panel.querySelector('.file-editor-status-message')?.textContent || '',
                request: window.__readmeDiffRequest || '',
                errors: window.__readmeDiffErrors,
              }};
            }})();
          </script>
        </body></html>""",
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__readmeDiffReady.then(done, error => done({error: String(error), errors: window.__readmeDiffErrors || []}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["modeWhilePayloadUnresolved"] != "edit", metrics
    assert "Deterministic test-only README diff line." not in metrics["textWhilePayloadUnresolved"], metrics
    assert metrics["finalMode"] == "diff", json.dumps(metrics, sort_keys=True)
    assert metrics["finalTextLength"] == metrics["expectedTextLength"], metrics
    assert metrics["deletedRows"] > 0, metrics
    assert metrics["plainFallback"] is False, metrics
    assert "from=HEAD" in metrics["request"] and "to=current" in metrics["request"], metrics
    assert metrics["errors"] == [], metrics


def test_editor_diff_button_waits_for_clean_payload_before_showing_refs(browser, tmp_path):
    css = app_css()
    bundle_uri = fixture_asset_url("static", "codemirror.js")
    strings = dict(app_english_strings())
    bootstrap = json.dumps(
        {
            "sessions": [],
            "availableAgents": [],
            "accessRole": "admin",
            "homePath": "/home/test",
            "repoRoot": str(REPO_ROOT),
            "maxSessionTabs": 99,
            "serverHostname": "test-host",
            "strings": {"en": strings},
            "codeMirrorAssetUrl": bundle_uri,
        },
        separators=(",", ":"),
    )
    path = "/home/test/repo/clean.md"
    content = "clean file\nunchanged line\n"
    payload = json.dumps(
        {
            "diff": "",
            "original": content,
            "working": content,
            "repo": "/home/test/repo",
            "relative_path": "clean.md",
            "from_ref": "HEAD",
            "to_ref": "current",
            "untracked": False,
            "working_missing": False,
        },
        separators=(",", ":"),
    )
    page = tmp_path / "clean-diff-button-race.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"""<!doctype html><html><head><meta charset=utf-8><style>{css}</style><script src="{bundle_uri}"></script>
        <style>
        body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; background: #ffffff; }}
        #mount {{ width: 920px; height: 520px; }}
        .file-editor-panel {{ width: 920px; height: 520px; }}
        .file-editor-codemirror-panel {{ height: 100%; }}
        </style></head>
        <body class="theme-light theme-resolved-light editor-theme-light">
          <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
          <div id="mount"></div>
          <script>
            window.__cleanDiffPayload = {payload};
            window.__cleanDiffErrors = [];
            window.addEventListener('error', event => window.__cleanDiffErrors.push(event.message || String(event.error || event)));
            window.addEventListener('unhandledrejection', event => window.__cleanDiffErrors.push(String(event.reason || event)));
            function jsonResponse(payload) {{
              const text = JSON.stringify(payload);
              return Promise.resolve({{ok: true, status: 200, headers: {{get: () => 'application/json'}}, json: async () => payload, text: async () => text}});
            }}
            window.fetch = async input => {{
              const url = new URL(String(input), 'https://localhost');
              if (url.pathname === '/api/fs/diff') {{
                window.__cleanDiffRequest = url.search;
                return new Promise(resolve => {{
                  window.__resolveCleanDiffFetch = () => resolve(jsonResponse(window.__cleanDiffPayload));
                }});
              }}
              return jsonResponse({{}});
            }};
          </script>
          <script>{app_bundle_before_boot_script()}</script>
          <script>
            window.__cleanDiffReady = (async () => {{
              const path = {json.dumps(path)};
              const content = {json.dumps(content)};
              const item = fileEditorItemFor(path);
              setFileState(path, {{
                kind: 'text',
                content,
                original: content,
                dirty: false,
                language: 'markdown',
                gitRoot: '/home/test/repo',
                gitTracked: true,
                gitHasHistory: true,
                gitHistory: [
                  {{ref: 'HEAD', short: 'HEAD', subject: 'current file'}},
                  {{ref: 'abc123def456', short: 'abc123d', subject: 'create file'}}
                ],
                diffLoaded: false,
                diffUnavailable: false,
                diff: '',
                diffOriginal: '',
              }});
              setFileEditorViewMode(path, 'edit', item);
              addFileEditorTabItem(path, item);
              const panel = createFileEditorPanel(item);
              panel.id = 'clean-diff-panel';
              panel.classList.add('active-pane');
              panelNodes.set(item, panel);
              document.getElementById('mount').append(panel);
              const {{frame}} = window.__yolomuxTestHelpers;
              const waitFor = window.__yolomuxTestWaitFor;
              const snapshot = () => {{
                const button = panel.querySelector('.file-editor-diff-panel');
                const refs = panel.querySelector('.file-editor-diff-ref-panel');
                const refInputs = Array.from(refs?.querySelectorAll('.diff-ref-input') || []).map(input => input.value || '');
                const state = fileState.get(path) || {{}};
                return {{
                  viewMode: editorViewModeFor(path, item),
                  cmMode: panel._cmMode || '',
                  buttonHidden: button?.hidden === true,
                  buttonDisabled: button?.disabled === true,
                  buttonTitle: button?.title || '',
                  refsHidden: refs?.hidden === true,
                  refsVisible: refs?.hidden === false,
                  refsText: refs?.textContent || '',
                  refInputs,
                  diffLoaded: state.diffLoaded === true,
                  diffLoading: state.diffLoading === true,
                  diffUnavailable: state.diffUnavailable === true,
                  diffLength: String(state.diff || '').length,
                }};
              }};
              renderFileEditorPanel(panel, item);
              await waitFor(() => panel._cmMode === 'edit' && panel.querySelector('.file-editor-diff-panel')?.hidden === false);
              const beforeClick = snapshot();
              panel.querySelector('.file-editor-diff-panel').click();
              await waitFor(() => window.__resolveCleanDiffFetch && fileState.get(path)?.diffLoading === true);
              await frame();
              await frame();
              const whileUnresolved = snapshot();
              window.__resolveCleanDiffFetch();
              await waitFor(() => fileState.get(path)?.diffLoaded === true && fileState.get(path)?.diffLoading === false && editorViewModeFor(path, item) === 'diff');
              await frame();
              await frame();
              const afterCleanDiff = snapshot();
              return {{
                beforeClick,
                whileUnresolved,
                afterCleanDiff,
                request: window.__cleanDiffRequest || '',
                errors: window.__cleanDiffErrors,
              }};
            }})();
          </script>
        </body></html>""",
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__cleanDiffReady.then(done, error => done({error: String(error), errors: window.__cleanDiffErrors || []}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["beforeClick"]["viewMode"] == "edit", metrics
    assert metrics["beforeClick"]["buttonHidden"] is False, metrics
    assert metrics["beforeClick"]["refsHidden"] is True, metrics
    assert metrics["whileUnresolved"]["viewMode"] == "edit", metrics
    assert metrics["whileUnresolved"]["buttonHidden"] is False, metrics
    assert metrics["whileUnresolved"]["buttonDisabled"] is True, metrics
    assert metrics["whileUnresolved"]["buttonTitle"] == "Loading diff", metrics
    assert metrics["whileUnresolved"]["refsHidden"] is True, metrics
    assert metrics["whileUnresolved"]["refsVisible"] is False, metrics
    assert metrics["afterCleanDiff"]["viewMode"] == "diff", metrics
    assert metrics["afterCleanDiff"]["cmMode"] == "diff", metrics
    assert metrics["afterCleanDiff"]["buttonHidden"] is False, metrics
    assert metrics["afterCleanDiff"]["refsHidden"] is False, metrics
    assert metrics["afterCleanDiff"]["refsVisible"] is True, metrics
    assert metrics["afterCleanDiff"]["refInputs"][:2] == ["HEAD", "current"], metrics
    assert metrics["afterCleanDiff"]["diffLoaded"] is True, metrics
    assert metrics["afterCleanDiff"]["diffLoading"] is False, metrics
    assert metrics["afterCleanDiff"]["diffUnavailable"] is False, metrics
    assert metrics["afterCleanDiff"]["diffLength"] == 0, metrics
    assert "from=HEAD" in metrics["request"] and "to=current" in metrics["request"], metrics
    assert metrics["errors"] == [], metrics


def test_editor_preview_mode_hides_codemirror_only_toolbar_buttons(browser, tmp_path):
    css = app_css()
    strings = dict(app_english_strings())
    bootstrap = json.dumps(
        {
            "sessions": [],
            "availableAgents": [],
            "accessRole": "admin",
            "homePath": "/home/test",
            "repoRoot": str(REPO_ROOT),
            "maxSessionTabs": 99,
            "serverHostname": "test-host",
            "strings": {"en": strings},
        },
        separators=(",", ":"),
    )
    page = tmp_path / "preview-toolbar.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"""<!doctype html><html><head><meta charset=utf-8><style>{css}</style>
        <style>
        body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; background: #11151d; }}
        #mount {{ width: 920px; height: 520px; }}
        .file-editor-panel {{ width: 920px; height: 520px; }}
        </style></head>
        <body class="theme-dark editor-theme-dark">
          <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
          <div id="mount"></div>
          <script>{app_bundle_before_boot_script()}</script>
          <script>
            window.__previewToolbarReady = (() => {{
              const path = '/home/test/repo/DONE.md';
              const item = fileEditorItemFor(path);
              setFileState(path, {{
                kind: 'text',
                content: '# Done\\n\\nSearchable preview text\\n',
                original: '# Done\\n\\nSearchable preview text\\n',
                dirty: false,
                language: 'markdown',
                externalChanged: true,
                gitRoot: '/home/test/repo',
                gitTracked: true,
                gitHasHistory: true,
                gitHistory: [{{ref: 'HEAD'}}, {{ref: 'abc123def'}}],
              }});
              addFileEditorTabItem(path, item);
              const panel = createFileEditorPanel(item);
              panel.classList.add('active-pane');
              document.getElementById('mount').append(panel);
              const snapshot = mode => {{
                setFileEditorViewMode(path, mode, item);
                renderFileEditorPanel(panel, item);
                const toolbar = panel.querySelector('.file-editor-toolbar')?.getBoundingClientRect();
                const font = panel.querySelector('.file-editor-preview-font-panel')?.getBoundingClientRect();
                return {{
                  gutterHidden: panel.querySelector('.file-editor-gutter-panel')?.hidden === true,
                  wrapHidden: panel.querySelector('.file-editor-wrap-panel')?.hidden === true,
                  findHidden: panel.querySelector('.file-editor-find-panel')?.hidden === true,
                  modeHidden: panel.querySelector('.file-editor-mode-control-panel')?.hidden === true,
                  blameHidden: panel.querySelector('.file-editor-blame-panel')?.hidden === true,
                  diffHidden: panel.querySelector('.file-editor-diff-panel')?.hidden === true,
                  diffExpandHidden: panel.querySelector('.file-editor-diff-expand-panel')?.hidden === true,
                  diffRefsHidden: panel.querySelector('.file-editor-diff-ref-panel')?.hidden === true,
                  popoutHidden: panel.querySelector('.file-editor-popout-preview-panel')?.hidden === true,
                  reloadHidden: panel.querySelector('.file-editor-reload-panel')?.hidden === true,
                  saveHidden: panel.querySelector('.file-editor-save-panel')?.hidden === true,
                  themeHidden: panel.querySelector('.file-editor-theme-panel')?.hidden === true,
                  previewHidden: panel.querySelector('.file-editor-preview-pane-panel')?.hidden === true,
                  fontHidden: panel.querySelector('.file-editor-preview-font-panel')?.hidden === true,
                  pathText: panel.querySelector('.file-editor-path')?.textContent || '',
                  pathTitle: panel.querySelector('.file-editor-path')?.title || '',
                  fontCenterDelta: toolbar && font ? Math.abs((font.left + font.width / 2) - (toolbar.left + toolbar.width / 2)) : 999,
                }};
              }};
              setFileEditorViewMode(path, 'edit', item);
              renderFileEditorPanel(panel, item);
              return {{
                preview: snapshot('preview'),
                split: snapshot('split'),
                edit: snapshot('edit'),
              }};
            }})();
          </script>
        </body></html>""",
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return window.__previewToolbarReady != null")
    )
    metrics = browser.execute_script("return window.__previewToolbarReady")
    assert metrics["preview"]["previewHidden"] is False, metrics
    assert metrics["preview"]["gutterHidden"] is True, metrics
    assert metrics["preview"]["wrapHidden"] is True, metrics
    assert metrics["preview"]["findHidden"] is False, metrics
    assert metrics["preview"]["modeHidden"] is False, metrics
    assert metrics["preview"]["diffExpandHidden"] is True, metrics
    assert metrics["preview"]["diffRefsHidden"] is True, metrics
    assert metrics["preview"]["popoutHidden"] is False, metrics
    assert metrics["preview"]["themeHidden"] is False, metrics
    assert metrics["preview"]["fontHidden"] is False, metrics
    assert metrics["preview"]["pathText"] == "~/repo/DONE.md", metrics
    assert metrics["preview"]["pathTitle"] == "/home/test/repo/DONE.md", metrics
    assert metrics["preview"]["fontCenterDelta"] <= 1.5, metrics
    assert metrics["split"]["modeHidden"] is False, metrics
    assert metrics["split"]["gutterHidden"] is False, metrics
    assert metrics["split"]["wrapHidden"] is False, metrics
    assert metrics["split"]["findHidden"] is False, metrics
    assert metrics["split"]["saveHidden"] is False, metrics
    assert metrics["edit"]["modeHidden"] is False, metrics
    assert metrics["edit"]["gutterHidden"] is False, metrics
    assert metrics["edit"]["wrapHidden"] is False, metrics
    assert metrics["edit"]["findHidden"] is False, metrics
    assert metrics["edit"]["saveHidden"] is False, metrics
    assert metrics["edit"]["pathText"] == "~/repo/DONE.md", metrics


def test_editor_preview_direct_media_formats_use_shared_dispatch(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
        try {
        const originalFetch = window.fetch.bind(window);
        const rawRequests = [];
        const fixturePng = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='), ch => ch.charCodeAt(0));
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), window.location.href);
          if (url.pathname === '/api/fs/raw') {
            rawRequests.push(`${url.pathname}${url.search}`);
            if (!['.mp3', '.mp4'].some(suffix => String(url.searchParams.get('path') || '').toLowerCase().endsWith(suffix))) {
              return new Response(fixturePng, {status: 200, headers: {'Content-Type': 'image/png'}});
            }
          }
          return originalFetch(input, options);
        };
        const mount = document.getElementById('grid');
        const waitImage = image => new Promise(resolve => {
          if (!image) return resolve(false);
          if (image.complete && image.naturalWidth > 0) return resolve(true);
          const finish = () => resolve(image.naturalWidth > 0);
          image.addEventListener('load', finish, {once: true});
          image.addEventListener('error', finish, {once: true});
        });
        const makePanel = (path, state, mode = 'preview') => {
          const item = fileEditorItemFor(path);
          setFileState(path, state);
          setFileEditorViewMode(path, mode, item);
          addFileEditorTabItem(path, item);
          const panel = createFileEditorPanel(item);
          panel.classList.add('active-pane');
          panel.style.width = '860px';
          panel.style.height = '420px';
          panelNodes.set(item, panel);
          mount.append(panel);
          renderFileEditorPanel(panel, item);
          return {item, panel};
        };
        const png = makePanel('/home/test/repo/assets/photo.png', {kind: 'image', mtime: 11, size: 1234, content: '', original: '', dirty: false});
        const apng = makePanel('/home/test/repo/assets/animation.apng', {kind: 'image', mtime: 111, size: 1534, content: '', original: '', dirty: false});
        const jpg = makePanel('/home/test/repo/assets/photo.jpg', {kind: 'image', mtime: 12, size: 2234, content: '', original: '', dirty: false});
        const gif = makePanel('/home/test/repo/assets/spinner.gif', {kind: 'image', mtime: 13, size: 3234, content: '', original: '', dirty: false});
        const webp = makePanel('/home/test/repo/assets/photo.webp', {kind: 'image', mtime: 131, size: 3334, content: '', original: '', dirty: false});
        const bmp = makePanel('/home/test/repo/assets/photo.bmp', {kind: 'image', mtime: 132, size: 3434, content: '', original: '', dirty: false});
        const ico = makePanel('/home/test/repo/assets/favicon.ico', {kind: 'image', mtime: 133, size: 3534, content: '', original: '', dirty: false});
        const avif = makePanel('/home/test/repo/assets/photo.avif', {kind: 'image', mtime: 134, size: 3634, content: '', original: '', dirty: false});
        const svg = makePanel('/home/test/repo/assets/diagram.svg', {kind: 'image', mtime: 14, size: 4234, content: '', original: '', dirty: false});
        const pdf = makePanel('/home/test/repo/spec.pdf', {kind: 'media', mediaKind: 'pdf', mtime: 15, size: 5234, content: '', original: '', dirty: false});
        const audio = makePanel('/home/test/repo/sound.mp3', {kind: 'media', mediaKind: 'audio', mtime: 151, size: 6234, content: '', original: '', dirty: false});
        const video = makePanel('/home/test/repo/movie.mp4', {kind: 'media', mediaKind: 'video', mtime: 152, size: 7234, content: '', original: '', dirty: false});
        const tiff = makePanel('/home/test/repo/assets/photo.tiff', {kind: 'media', mediaKind: 'unsupported-image', mime: 'image/tiff', mtime: 153, size: 8234, content: '', original: '', dirty: false});
        const archive = makePanel('/home/test/repo/archive.zip', {kind: 'media', mediaKind: 'unsupported-archive', mime: 'application/zip', mtime: 154, size: 9234, content: '', original: '', dirty: false});
        const parquet = makePanel('/home/test/repo/data.parquet', {kind: 'media', mediaKind: 'unsupported-data', mime: 'application/vnd.apache.parquet', mtime: 155, size: 10234, content: '', original: '', dirty: false});
        const code = makePanel('/home/test/repo/app.py', {kind: 'text', mtime: 16, size: 64, content: 'print("hello")\\n', original: 'print("hello")\\n', dirty: false, language: 'python'});
        const text = makePanel('/home/test/repo/notes.txt', {kind: 'text', mtime: 17, size: 24, content: 'plain notes\\n', original: 'plain notes\\n', dirty: false, language: 'text'});
        const unsupported = makePanel('/home/test/repo/archive.bin', {kind: 'too-large', size: 999999, maxBytes: 1024, error: 'binary preview blocked'});
        const initialPngPane = png.panel.querySelector('.file-editor-image-panel');
        if (initialPngPane?._previewAsync) await initialPngPane._previewAsync;
        const initialPngImage = initialPngPane?.querySelector('img.file-editor-image');
        await waitImage(initialPngImage);
        const initialPngSrc = initialPngImage?.getAttribute('src') || '';
        setFileState('/home/test/repo/assets/photo.png', {kind: 'image', mtime: 11, mtime_ns: 11000000001, size: 1234, content: '', original: '', dirty: false});
        renderFileEditorPanel(png.panel, png.item);
        const imagePanes = [png, apng, jpg, gif, webp, bmp, ico, avif, svg]
          .map(({panel}) => panel.querySelector('.file-editor-image-panel'));
        const mediaPanes = [
          audio.panel.querySelector('.file-editor-preview-pane-panel'),
          video.panel.querySelector('.file-editor-preview-pane-panel'),
        ];
        const pendingPanes = imagePanes.concat(mediaPanes);
        await Promise.all(pendingPanes.map(pane => pane?._previewAsync).filter(Boolean));
        await Promise.all(imagePanes.map(pane => waitImage(pane?.querySelector('img'))));
        const refreshedPngImage = png.panel.querySelector('.file-editor-image-panel img.file-editor-image');
        const imageInfo = ({panel}) => {
          const img = panel.querySelector('.file-editor-image-panel img.file-editor-image');
          return {
            imagePaneHidden: panel.querySelector('.file-editor-image-panel')?.hidden === true,
            previewPaneHidden: panel.querySelector('.file-editor-preview-pane-panel')?.hidden === true,
            src: img?.getAttribute('src') || '',
            naturalWidth: img?.naturalWidth || 0,
            naturalHeight: img?.naturalHeight || 0,
            hasInlineSvg: Boolean(panel.querySelector('svg')),
            mode: editorViewModeFor(panel.dataset.filePath, panel.dataset.layoutItem),
            editButtonHidden: panel.querySelector('[data-editor-mode="edit"]')?.hidden === true,
            splitButtonHidden: panel.querySelector('[data-editor-mode="split"]')?.hidden === true,
            previewButtonHidden: panel.querySelector('[data-editor-mode="preview"]')?.hidden === true,
            zoomToolbarExists: Boolean(panel.querySelector('.file-editor-image-panel .file-editor-preview-zoom-toolbar')),
            zoomActions: Array.from(panel.querySelectorAll('.file-editor-image-panel [data-preview-zoom-action]')).map(button => button.dataset.previewZoomAction),
            zoomPan: panel.querySelector('.file-editor-image-panel')?.dataset.previewZoomPan || '',
          };
        };
        const fallbackInfo = ({panel}) => ({
          previewPaneHidden: panel.querySelector('.file-editor-preview-pane-panel')?.hidden === true,
          title: panel.querySelector('.file-editor-preview-fallback .file-editor-empty-title')?.textContent || '',
          text: panel.querySelector('.file-editor-preview-fallback')?.textContent || '',
          modeControlHidden: panel.querySelector('.file-editor-mode-control-panel')?.hidden === true,
        });
        const pdfFrame = pdf.panel.querySelector('.file-editor-pdf-preview');
        const editorOnlyInfo = ({panel}) => ({
          mode: editorViewModeFor(panel.dataset.filePath, panel.dataset.layoutItem),
          previewPaneHidden: panel.querySelector('.file-editor-preview-pane-panel')?.hidden === true,
          codeMirrorHidden: panel.querySelector('.file-editor-codemirror-panel')?.hidden === true,
          modeControlHidden: panel.querySelector('.file-editor-mode-control-panel')?.hidden === true,
          popoutHidden: panel.querySelector('.file-editor-popout-preview-panel')?.hidden === true,
        });
        const result = {
          png: imageInfo(png),
          apng: imageInfo(apng),
          jpg: imageInfo(jpg),
          gif: imageInfo(gif),
          webp: imageInfo(webp),
          bmp: imageInfo(bmp),
          ico: imageInfo(ico),
          avif: imageInfo(avif),
          svg: imageInfo(svg),
          pdf: {
            previewPaneHidden: pdf.panel.querySelector('.file-editor-preview-pane-panel')?.hidden === true,
            iframeSrc: pdfFrame?.getAttribute('src') || '',
            sandbox: pdfFrame?.getAttribute('sandbox'),
            fallbackText: pdf.panel.querySelector('.file-editor-preview-fallback')?.textContent || '',
            editButtonHidden: pdf.panel.querySelector('[data-editor-mode="edit"]')?.hidden === true,
            splitButtonHidden: pdf.panel.querySelector('[data-editor-mode="split"]')?.hidden === true,
            previewButtonHidden: pdf.panel.querySelector('[data-editor-mode="preview"]')?.hidden === true,
          },
          audio: {
            mediaSrc: audio.panel.querySelector('audio.file-editor-native-media')?.getAttribute('src') || '',
            controls: audio.panel.querySelector('audio.file-editor-native-media')?.controls === true,
            fallbackText: audio.panel.querySelector('.file-editor-preview-fallback')?.textContent || '',
          },
          video: {
            mediaSrc: video.panel.querySelector('video.file-editor-native-media')?.getAttribute('src') || '',
            controls: video.panel.querySelector('video.file-editor-native-media')?.controls === true,
            autoplay: video.panel.querySelector('video.file-editor-native-media')?.autoplay === true,
            fallbackText: video.panel.querySelector('.file-editor-preview-fallback')?.textContent || '',
          },
          tiff: fallbackInfo(tiff),
          archive: fallbackInfo(archive),
          parquet: fallbackInfo(parquet),
          code: editorOnlyInfo(code),
          text: editorOnlyInfo(text),
          unsupported: {
            imagePaneHidden: unsupported.panel.querySelector('.file-editor-image-panel')?.hidden === true,
            previewPaneHidden: unsupported.panel.querySelector('.file-editor-preview-pane-panel')?.hidden === true,
            text: unsupported.panel.textContent || '',
            modeControlHidden: unsupported.panel.querySelector('.file-editor-mode-control-panel')?.hidden === true,
          },
          pngRefresh: {
            before: initialPngSrc,
            after: refreshedPngImage?.getAttribute('src') || '',
            version: png.panel.querySelector('.file-editor-image-panel')?.dataset.imageVersion || '',
          },
          rawRequests,
          errors: jsDebugFailureEvents('error'),
          rejections: jsDebugFailureEvents('rejection'),
        };
        done(result);
        } catch (error) {
          done({error: String(error), stack: error?.stack || '', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        }
        })();
        """
    )
    assert "error" not in metrics, metrics
    for key in ("png", "apng", "jpg", "gif", "webp", "bmp", "ico", "avif", "svg"):
        assert metrics[key]["imagePaneHidden"] is False, metrics
        assert metrics[key]["previewPaneHidden"] is True, metrics
        assert metrics[key]["src"].startswith("blob:"), f"{key}: {metrics[key]!r}; requests={metrics['rawRequests']!r}"
        assert metrics[key]["naturalWidth"] > 0 and metrics[key]["naturalHeight"] > 0, (key, metrics[key])
        assert metrics[key]["mode"] == "preview", metrics
        assert metrics[key]["previewButtonHidden"] is False, metrics
        assert metrics[key]["editButtonHidden"] is True, metrics
        assert metrics[key]["splitButtonHidden"] is True, metrics
        assert metrics[key]["zoomToolbarExists"] is True, metrics
        assert metrics[key]["zoomActions"] == ["out", "fit", "actual", "in"], metrics
        assert metrics[key]["zoomPan"] == "1", metrics
    assert metrics["pngRefresh"]["before"].startswith("blob:"), metrics
    assert metrics["pngRefresh"]["after"].startswith("blob:"), metrics
    assert metrics["pngRefresh"]["before"] != metrics["pngRefresh"]["after"], metrics
    assert metrics["pngRefresh"]["version"] == "11000000001", metrics
    requested_paths = {request.partition("?")[2] for request in metrics["rawRequests"]}
    for expected in (
        "path=%2Fhome%2Ftest%2Frepo%2Fassets%2Fphoto.png&v=11",
        "path=%2Fhome%2Ftest%2Frepo%2Fassets%2Fphoto.png&v=11000000001",
        "path=%2Fhome%2Ftest%2Frepo%2Fassets%2Fanimation.apng&v=111",
        "path=%2Fhome%2Ftest%2Frepo%2Fassets%2Fphoto.jpg&v=12",
        "path=%2Fhome%2Ftest%2Frepo%2Fassets%2Fspinner.gif&v=13",
        "path=%2Fhome%2Ftest%2Frepo%2Fassets%2Fphoto.webp&v=131",
        "path=%2Fhome%2Ftest%2Frepo%2Fassets%2Fphoto.bmp&v=132",
        "path=%2Fhome%2Ftest%2Frepo%2Fassets%2Ffavicon.ico&v=133",
        "path=%2Fhome%2Ftest%2Frepo%2Fassets%2Fphoto.avif&v=134",
        "path=%2Fhome%2Ftest%2Frepo%2Fassets%2Fdiagram.svg&v=14",
        "path=%2Fhome%2Ftest%2Frepo%2Fsound.mp3&v=151",
        "path=%2Fhome%2Ftest%2Frepo%2Fmovie.mp4&v=152",
    ):
        assert expected in requested_paths, metrics
    assert metrics["svg"]["hasInlineSvg"] is False, metrics
    assert metrics["pdf"]["previewPaneHidden"] is False, metrics
    assert metrics["pdf"]["iframeSrc"].startswith("/api/fs/raw?path="), metrics
    assert "spec.pdf" in metrics["pdf"]["iframeSrc"], metrics
    assert metrics["pdf"]["sandbox"] == "", metrics
    assert "Open" in metrics["pdf"]["fallbackText"] and "Download" in metrics["pdf"]["fallbackText"], metrics
    assert metrics["pdf"]["editButtonHidden"] is True and metrics["pdf"]["splitButtonHidden"] is True, metrics
    assert metrics["pdf"]["previewButtonHidden"] is False, metrics
    assert metrics["audio"]["mediaSrc"].startswith("blob:"), metrics
    assert metrics["audio"]["controls"] is True, metrics
    assert "Open" in metrics["audio"]["fallbackText"] and "Download" in metrics["audio"]["fallbackText"], metrics
    assert metrics["video"]["mediaSrc"].startswith("blob:"), metrics
    assert metrics["video"]["controls"] is True and metrics["video"]["autoplay"] is False, metrics
    assert "Open" in metrics["video"]["fallbackText"] and "Download" in metrics["video"]["fallbackText"], metrics
    assert metrics["tiff"]["previewPaneHidden"] is False, metrics
    assert "recognized but not previewable" in metrics["tiff"]["title"], metrics
    assert "image/tiff" in metrics["tiff"]["text"] and "Open" in metrics["tiff"]["text"] and "Download" in metrics["tiff"]["text"], metrics
    assert "Archive preview is not expanded" in metrics["archive"]["title"], metrics
    assert "application/zip" in metrics["archive"]["text"], metrics
    assert "external viewer" in metrics["parquet"]["title"], metrics
    assert "application/vnd.apache.parquet" in metrics["parquet"]["text"], metrics
    assert metrics["code"]["mode"] == "edit", metrics
    assert metrics["code"]["previewPaneHidden"] is True, metrics
    assert metrics["code"]["modeControlHidden"] is True, metrics
    assert metrics["code"]["popoutHidden"] is True, metrics
    assert metrics["text"]["mode"] == "edit", metrics
    assert metrics["text"]["previewPaneHidden"] is True, metrics
    assert metrics["text"]["modeControlHidden"] is True, metrics
    assert metrics["text"]["popoutHidden"] is True, metrics
    assert metrics["unsupported"]["modeControlHidden"] is True, metrics
    assert "File is too large to preview" in metrics["unsupported"]["text"], metrics
    assert "binary preview blocked" in metrics["unsupported"]["text"], metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics


def test_editor_opens_mermaid_source_preview_by_default(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const {frame} = window.__yolomuxTestHelpers;
            const originalFetch = window.fetch.bind(window);
            const path = '/home/test/repo/chart.mmd';
            window.fetch = async (input, options = {}) => {
              const url = new URL(String(input), 'https://localhost');
              if (url.pathname === '/api/fs/read') {
                return new Response(JSON.stringify({
                  path,
                  name: 'chart.mmd',
                  size: 17,
                  mtime: 20,
                  content: 'graph TD; A-->B;',
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
              return originalFetch(input, options);
            };
            const mermaidCalls = [];
            window.mermaid = {
              initialize(config) {
                window.__directMermaidConfig = config;
              },
              async render(id, source) {
                mermaidCalls.push({id, source});
                return {svg: '<svg viewBox="0 0 100 40"><text>Direct Mermaid</text></svg>'};
              },
            };
            const item = await openFileInEditor(path, {name: 'chart.mmd', size: 17, mtime: 20}, {userInitiated: true});
            const panel = panelNodes.get(item);
            const preview = panel?.querySelector('.file-editor-preview-pane-panel');
            if (preview?._previewAsync) await preview._previewAsync;
            await frame();
            await frame();
            done({
              item,
              mode: editorViewModeFor(path, item),
              previewHidden: preview?.hidden === true,
              imageExists: Boolean(preview?.querySelector('img.mermaid-preview-image')),
              imageSrcPrefix: String(preview?.querySelector('img.mermaid-preview-image')?.getAttribute('src') || '').slice(0, 5),
              calls: mermaidCalls,
              config: window.__directMermaidConfig || {},
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
    assert metrics["mode"] == "preview", metrics
    assert metrics["previewHidden"] is False, metrics
    assert metrics["imageExists"] is True, metrics
    assert metrics["imageSrcPrefix"] in ("blob:", "data:"), metrics
    assert metrics["calls"], metrics
    assert {call["source"] for call in metrics["calls"]} == {"graph TD; A-->B;"}, metrics
    assert metrics["config"]["startOnLoad"] is False, metrics
    assert metrics["config"]["securityLevel"] == "strict", metrics
    assert metrics["config"]["htmlLabels"] is False, metrics
    assert metrics["config"]["flowchart"]["htmlLabels"] is False, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics


def test_editor_opens_jsonl_table_preview_by_default_and_keeps_raw_edit(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const {frame} = window.__yolomuxTestHelpers;
            const originalFetch = window.fetch.bind(window);
            const path = '/home/test/repo/transcript.jsonl';
            const content = [
              JSON.stringify({type: 'session_meta', session: {id: 's1'}}),
              JSON.stringify({type: 'assistant', message: {text: 'hello'}}),
              '{still-streaming',
            ].join('\\n');
            window.fetch = async (input, options = {}) => {
              const url = new URL(String(input), 'https://localhost');
              if (url.pathname === '/api/fs/read') {
                return new Response(JSON.stringify({path, name: 'transcript.jsonl', size: content.length, mtime: 20, content}), {
                  status: 200,
                  headers: {'Content-Type': 'application/json'},
                });
              }
              return originalFetch(input, options);
            };
            const item = await openFileInEditor(path, {name: 'transcript.jsonl', size: content.length, mtime: 20}, {userInitiated: true});
            await frame();
            const panel = panelNodes.get(item);
            const preview = panel?.querySelector('.file-editor-preview-pane-panel');
            const initial = {
              mode: editorViewModeFor(path, item),
              previewHidden: preview?.hidden === true,
              headers: Array.from(preview?.querySelectorAll('.file-editor-jsonl-preview th') || []).map(node => node.textContent),
              rows: preview?.querySelectorAll('.file-editor-jsonl-preview tbody tr').length || 0,
              unparsed: preview?.querySelector('[data-unparsed-line]')?.textContent || '',
            };
            const editButton = panel?.querySelector('[data-editor-mode="edit"]');
            editButton?.click();
            await frame();
            done({
              initial,
              editButtonVisible: editButton?.hidden === false,
              finalMode: editorViewModeFor(path, item),
              editorHidden: panel?.querySelector('.file-editor-codemirror-panel')?.hidden === true,
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
    assert metrics["initial"]["mode"] == "preview" and metrics["initial"]["previewHidden"] is False, metrics
    assert metrics["initial"]["headers"] == ["type", "session", "message"], metrics
    assert metrics["initial"]["rows"] == 3 and metrics["initial"]["unparsed"] == "{still-streaming", metrics
    assert metrics["editButtonVisible"] is True, metrics
    assert metrics["finalMode"] == "edit" and metrics["editorHidden"] is False, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_direct_mermaid_network_topology_uses_native_svg_labels(browser, tmp_path):
    browser.set_window_size(1200, 900)
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    mermaid_uri = fixture_asset_url("static", "vendor", "mermaid.min.js")
    linux_port = str(7769 + 1)
    linux_port_range = f"{linux_port}-{int(linux_port) + 3}"
    mac_port_range = f"{int(linux_port) + 1110}-{int(linux_port) + 1113}"
    mermaid_source = f"""flowchart TB
    relay[\"ereview.com relay<br/>account: conway\"]
    linux[\"keivenc-linux1<br/>YOLOmux: 127.0.0.1:{linux_port_range}<br/>yo{linux_port}-yo{int(linux_port) + 3}\"]
    mac[\"Mac<br/>YOLOmux: 0.0.0.0:{mac_port_range}<br/>yo{int(linux_port) + 1110}-yo{int(linux_port) + 1113}\"]
    macForward[\"Mac Linux-forward listener<br/>localhost/LAN:{linux_port_range}\"]
    home[\"Home client<br/>192.168.1.x\"]
    internet[\"Internet client\"]

    linux -->|\"autossh -R: ereview:{linux_port_range}\"| relay
    mac -->|\"autossh -R: ereview:{mac_port_range}\"| relay
    macForward -->|\"autossh -L direct, no relay: Linux 127.0.0.1:{linux_port_range}\"| linux
    mac --- macForward
    home --> mac
    home --> linux
    internet --> relay
    """
    metrics = browser.execute_async_script(
        """
        const mermaidUri = arguments[0];
        const mermaidSource = arguments[1];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const waitFor = window.__yolomuxTestWaitFor;
            const waitImage = image => new Promise(resolve => {
              if (!image) return resolve(false);
              if (image.complete && image.naturalWidth > 0) return resolve(true);
              image.addEventListener('load', () => resolve(image.naturalWidth > 0), {once: true});
              image.addEventListener('error', () => resolve(false), {once: true});
            });
            const readBlobText = url => new Promise((resolve, reject) => {
              const request = new XMLHttpRequest();
              request.open('GET', url);
              request.onload = () => resolve(String(request.responseText || ''));
              request.onerror = () => reject(new Error('failed to read preview SVG'));
              request.send();
            });
            await new Promise((resolve, reject) => {
              const script = document.createElement('script');
              script.src = mermaidUri;
              script.onload = resolve;
              script.onerror = () => reject(new Error('failed to load Mermaid'));
              document.head.append(script);
            });
            window.mermaid.initialize(mermaidPreviewConfig());
            const raw = await window.mermaid.render('yolomux-network-topology-probe', mermaidSource);
            const rawSvg = typeof raw === 'string' ? raw : raw?.svg || '';
            const path = '/home/test/repo/docs/network-topology.mmd';
            const item = fileEditorItemFor(path);
            setFileState(path, {kind: 'text', content: mermaidSource, original: mermaidSource, dirty: false, language: 'mermaid'});
            setFileEditorViewMode(path, 'preview', item);
            addFileEditorTabItem(path, item);
            const panel = createFileEditorPanel(item);
            panel.classList.add('active-pane');
            panel.style.width = '960px';
            panel.style.height = '620px';
            panelNodes.set(item, panel);
            document.getElementById('grid').replaceChildren(panel);
            renderFileEditorPanel(panel, item);
            const preview = panel.querySelector('.file-editor-preview-pane-panel');
            if (preview._previewAsync) await preview._previewAsync;
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            await waitFor(
              () => !preview.classList.contains('file-editor-preview-zoom-measuring'),
              {description: 'network topology Mermaid preview reveal'}
            );
            const image = preview.querySelector('img.mermaid-preview-image');
            await waitImage(image);
            const previewSvg = image?.src ? await readBlobText(image.src) : '';
            const previewNativeLabelTexts = Array.from(new DOMParser().parseFromString(previewSvg, 'image/svg+xml').querySelectorAll('text'))
              .map(node => String(node.textContent || '').replace(/\\s+/g, ' ').trim())
              .filter(Boolean);
            done({
              rawForeignObjects: (rawSvg.match(/<foreignObject\\b/gi) || []).length,
              previewForeignObjects: (previewSvg.match(/<foreignObject\\b/gi) || []).length,
              previewHasCollapsedMacLabel: previewSvg.includes('MacYOLOmux:'),
              previewHasCollapsedForwardLabel: previewSvg.includes('Mac Linux-forward listenerlocalhost/LAN'),
              previewNativeLabelTexts,
              naturalWidth: image?.naturalWidth || 0,
              naturalHeight: image?.naturalHeight || 0,
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || ''});
          }
        })();
        """,
        mermaid_uri,
        mermaid_source,
    )
    browser.save_screenshot("/tmp/yolomux-mermaid-network-topology-after.png")
    assert "error" not in metrics, metrics
    assert metrics["rawForeignObjects"] == 0, metrics
    assert metrics["previewForeignObjects"] == 0, metrics
    assert metrics["previewHasCollapsedMacLabel"] is False, metrics
    assert metrics["previewHasCollapsedForwardLabel"] is False, metrics
    preview_label_text = " ".join(metrics["previewNativeLabelTexts"]).replace(" ", "")
    assert "Homeclient" in preview_label_text, metrics
    assert "Internetclient" in preview_label_text, metrics
    assert metrics["naturalWidth"] > 0 and metrics["naturalHeight"] > 0, metrics
    assert metrics["errors"] == [] and metrics["rejections"] == [], metrics


def test_direct_mermaid_sample_real_bundle_keeps_svg_text_labels(browser, tmp_path):
    browser.set_window_size(1200, 900)
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    mermaid_uri = fixture_asset_url("static", "vendor", "mermaid.min.js")
    mermaid_source = (REPO_ROOT / "tests" / "fixtures" / "preview-samples" / "14-mermaid.mmd").read_text(encoding="utf-8")
    assert "flowchart TD" in mermaid_source
    assert "direction LR" in mermaid_source
    assert "subgraph MarkdownFlow" in mermaid_source
    assert "subgraph MermaidFlow" in mermaid_source
    assert "subgraph MediaFlow" in mermaid_source
    metrics = browser.execute_async_script(
        """
        const mermaidUri = arguments[0];
        const mermaidSource = arguments[1];
        const done = arguments[arguments.length - 1];
            (async () => {
              try {
                const {frame} = window.__yolomuxTestHelpers;
                const waitFor = window.__yolomuxTestWaitFor;
                const waitImage = image => new Promise(resolve => {
                  if (!image) {
                    resolve(false);
                    return;
                  }
                  if (image.complete && image.naturalWidth > 0) {
                    resolve(true);
                    return;
                  }
                  const finish = () => resolve(image.naturalWidth > 0);
                  image.addEventListener('load', finish, {once: true});
                  image.addEventListener('error', finish, {once: true});
                });
                const {rect} = window.__yolomuxTestHelpers;
                const readBlobText = url => new Promise((resolve, reject) => {
                  const request = new XMLHttpRequest();
                  request.open('GET', url);
                  request.onload = () => resolve(String(request.responseText || ''));
                  request.onerror = () => reject(new Error(`failed to read ${url}`));
                  request.send();
                });
                await new Promise((resolve, reject) => {
              const script = document.createElement('script');
              script.src = mermaidUri;
              script.onload = resolve;
              script.onerror = () => reject(new Error(`failed to load ${mermaidUri}`));
              document.head.append(script);
            });
            window.mermaid.initialize(mermaidPreviewConfig());
            const probeResult = await window.mermaid.render('yolomux-mermaid-probe', mermaidSource);
            const probeSvgText = typeof probeResult === 'string' ? probeResult : probeResult?.svg || '';
            const probeNativeLabelTexts = Array.from(new DOMParser().parseFromString(probeSvgText, 'image/svg+xml').querySelectorAll('text'))
              .map(node => String(node.textContent || '').trim())
              .filter(Boolean);
            const path = '/home/test/repo/tests/fixtures/preview-samples/14-mermaid.mmd';
            const item = fileEditorItemFor(path);
            setFileState(path, {kind: 'text', content: mermaidSource, original: mermaidSource, dirty: false, language: 'mermaid'});
            setFileEditorViewMode(path, 'preview', item);
            addFileEditorTabItem(path, item);
            const panel = createFileEditorPanel(item);
            panel.classList.add('active-pane');
            panel.style.width = '960px';
            panel.style.height = '620px';
            panelNodes.set(item, panel);
            const grid = document.getElementById('grid');
            grid.replaceChildren(panel);
            renderFileEditorPanel(panel, item);
            const preview = panel.querySelector('.file-editor-preview-pane-panel');
            if (preview._previewAsync) await preview._previewAsync;
            await frame();
            await frame();
            const image = preview.querySelector('img.mermaid-preview-image');
            await waitImage(image);
            await frame();
            await frame();
            // Idempotent re-render: a periodic pane refresh re-runs renderEditorPreviewPane with the
            // SAME source. Tag the rendered diagram, re-run, and the SAME <img> must survive — a
            // rebuild here is what flashed the diagram on every refresh tick.
            image.dataset.idemTag = 'keep';
            renderEditorPreviewPane(preview, path, mermaidSource, {context: 'preview'});
            if (preview._previewAsync) await preview._previewAsync;
            await frame();
            const idempotentRerender = Boolean(preview.querySelector('img.mermaid-preview-image[data-idem-tag="keep"]'));
            const svgText = image?.src ? await readBlobText(image.src) : '';
            const previewNativeLabelTexts = Array.from(new DOMParser().parseFromString(svgText, 'image/svg+xml').querySelectorAll('text'))
              .map(node => String(node.textContent || '').replace(/[\\t\\r\\n ]+/g, ' ').trim())
              .filter(Boolean);
            const viewport = preview.querySelector('.file-editor-preview-zoom-viewport');
            const zoomIn = preview.querySelector('[data-preview-zoom-action="in"]');
            const zoomActual = preview.querySelector('[data-preview-zoom-action="actual"]');
            const zoomValueBefore = preview.querySelector('.file-editor-preview-zoom-value')?.textContent || '';
            const imageRectFit = rect(image);
            const wheelRect = rect(viewport);
            const wheelEvent = new WheelEvent('wheel', {
              deltaY: -160,
              clientX: wheelRect.left + (wheelRect.width * 0.5),
              clientY: wheelRect.top + (wheelRect.height * 0.5),
              bubbles: true,
              cancelable: true,
            });
            const wheelPrevented = !viewport.dispatchEvent(wheelEvent);
            await frame();
            await frame();
            const imageRectAfterWheel = rect(image);
            zoomActual?.click();
            await frame();
            await frame();
            const imageRectActual = rect(image);
            for (let index = 0; index < 8 && zoomIn && !zoomIn.disabled; index += 1) {
              zoomIn.click();
              await frame();
            }
            await frame();
            await frame();
            const imageRectAfterZoom = rect(image);
            const zoomValueAfterManual = preview.querySelector('.file-editor-preview-zoom-value')?.textContent || '';
            const zoomModeAfterManual = preview.dataset.previewZoomMode || '';
            if (viewport) {
              viewport.scrollLeft = Math.min(80, Math.max(0, viewport.scrollWidth - viewport.clientWidth));
              viewport.scrollTop = Math.min(80, Math.max(0, viewport.scrollHeight - viewport.clientHeight));
            }
            const viewportAfterManual = viewport ? {
              clientWidth: viewport.clientWidth,
              clientHeight: viewport.clientHeight,
              scrollWidth: viewport.scrollWidth,
              scrollHeight: viewport.scrollHeight,
              scrollLeft: viewport.scrollLeft,
              scrollTop: viewport.scrollTop,
            } : null;
            setFileEditorViewMode(path, 'split', item);
            renderFileEditorPanel(panel, item);
            const splitPreview = panel.querySelector('.file-editor-preview-pane-panel');
            if (splitPreview._previewAsync) await splitPreview._previewAsync;
            await frame();
            await frame();
            const splitImage = splitPreview.querySelector('img.mermaid-preview-image');
            await waitImage(splitImage);
            await frame();
            // Wait for the zoom surface to reveal (measuring class cleared) so geometry/pixels are
            // sampled at the settled size, not a transient pre-settle size that is hidden anyway.
            await waitFor(
              () => !splitPreview.classList.contains('file-editor-preview-zoom-measuring'),
              {description: 'split Mermaid zoom measurement'}
            );
            await frame();
            const splitMeasuring = splitPreview.classList.contains('file-editor-preview-zoom-measuring');
            const splitViewport = splitPreview.querySelector('.file-editor-preview-zoom-viewport');
            const splitImageRect = rect(splitImage);
            const splitZoomValueBeforeWheel = splitPreview.querySelector('.file-editor-preview-zoom-value')?.textContent || '';
            const splitToolbarButtons = Array.from(splitPreview.querySelectorAll('[data-preview-zoom-action]'));
            const splitWheelRect = rect(splitViewport);
            const splitViewportBeforeWheel = splitViewport ? {
              clientWidth: splitViewport.clientWidth,
              clientHeight: splitViewport.clientHeight,
              scrollWidth: splitViewport.scrollWidth,
              scrollHeight: splitViewport.scrollHeight,
              scrollLeft: splitViewport.scrollLeft,
              scrollTop: splitViewport.scrollTop,
            } : null;
            const splitWheelEvent = new WheelEvent('wheel', {
              deltaY: -160,
              clientX: splitWheelRect.left + (splitWheelRect.width * 0.55),
              clientY: splitWheelRect.top + (splitWheelRect.height * 0.45),
              bubbles: true,
              cancelable: true,
            });
            const splitWheelPrevented = !splitViewport.dispatchEvent(splitWheelEvent);
            await frame();
            await frame();
            const splitImageRectAfterWheel = rect(splitImage);
            const splitZoomIn = splitPreview.querySelector('[data-preview-zoom-action="in"]');
            for (let index = 0; index < 4 && splitZoomIn && !splitZoomIn.disabled; index += 1) {
              splitZoomIn.click();
              await frame();
            }
            await frame();
            const splitImageRectAfterManualZoom = rect(splitImage);
            // Start the pan from the top-left corner so the drag (which increases scroll) always
            // has headroom after the explicit toolbar zoom.
            splitViewport.scrollLeft = 0;
            splitViewport.scrollTop = 0;
            await frame();
            const splitPanStart = {
              scrollLeft: splitViewport.scrollLeft,
              scrollTop: splitViewport.scrollTop,
              clientX: splitWheelRect.left + (splitWheelRect.width * 0.5),
              clientY: splitWheelRect.top + (splitWheelRect.height * 0.5),
            };
            splitViewport.dispatchEvent(new PointerEvent('pointerdown', {
              pointerId: 9,
              pointerType: 'mouse',
              button: 0,
              buttons: 1,
              clientX: splitPanStart.clientX,
              clientY: splitPanStart.clientY,
              bubbles: true,
              cancelable: true,
            }));
            splitViewport.dispatchEvent(new PointerEvent('pointermove', {
              pointerId: 9,
              pointerType: 'mouse',
              buttons: 1,
              clientX: splitPanStart.clientX - 90,
              clientY: splitPanStart.clientY - 70,
              bubbles: true,
              cancelable: true,
            }));
            splitViewport.dispatchEvent(new PointerEvent('pointerup', {
              pointerId: 9,
              pointerType: 'mouse',
              button: 0,
              buttons: 0,
              clientX: splitPanStart.clientX - 90,
              clientY: splitPanStart.clientY - 70,
              bubbles: true,
              cancelable: true,
            }));
            await frame();
            const nodeContrast = (() => {
              // Parse the rendered SVG and verify every node label contrasts with its node's own
              // background shape fill (the dark-on-dark Mermaid readability bug). Light text on a dark
              // default node and dark text on a light classDef node must both clear WCAG-ish 3:1.
              const lum = hx => { const m = /^#([0-9a-f]{6})$/i.exec(hx || ''); if (!m) return null;
                const n = parseInt(m[1], 16), r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
                const f = c => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
                return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
              const con = (a, b) => { const la = lum(a), lb = lum(b); if (la == null || lb == null) return null;
                return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05); };
              const norm = c => { if (!c) return c; const m = /^#([0-9a-f]{3})$/i.exec(c);
                return m ? '#' + m[1].split('').map(x => x + x).join('') : c; };
              const fillOf = el => { const s = (el && el.getAttribute && el.getAttribute('style')) || '';
                const m = /fill:\\s*(#[0-9a-fA-F]{3,8})/.exec(s); return norm(m ? m[1] : ((el && el.getAttribute && el.getAttribute('fill')) || '')); };
              const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml');
              const bgFor = node => { for (const sh of node.querySelectorAll('rect,polygon,path,circle,ellipse')) {
                if (sh.closest('.label')) continue; const f = fillOf(sh); if (/^#[0-9a-f]{6}$/i.test(f)) return f; } return null; };
              const fails = []; let minc = 99;
              doc.querySelectorAll('.node text').forEach(t => {
                const tf = fillOf(t); const bg = bgFor(t.closest('.node'));
                if (!/^#[0-9a-f]{6}$/i.test(tf || '') || !bg) return;
                const c = con(tf, bg); if (c == null) return; if (c < minc) minc = c;
                if (c < 3) fails.push({text: (t.textContent || '').trim().slice(0, 24), tf, bg, c: Math.round(c * 100) / 100});
              });
              return {minContrast: minc === 99 ? null : Math.round(minc * 100) / 100, failCount: fails.length, failures: fails.slice(0, 8)};
            })();
            const result = {
              nodeContrast,
              idempotentRerender,
              imageExists: Boolean(image),
              imageSrcPrefix: String(image?.getAttribute('src') || '').slice(0, 5),
              imageRect: rect(image),
              imageRectFit,
              wheelPrevented,
              imageRectAfterWheel,
              imageRectActual,
              imageRectAfterZoom,
              naturalWidth: image?.naturalWidth || 0,
              naturalHeight: image?.naturalHeight || 0,
              zoomToolbarExists: Boolean(preview.querySelector('.file-editor-preview-zoom-toolbar')),
              zoomValueBefore,
              zoomValueAfter: zoomValueAfterManual,
              zoomModeAfter: zoomModeAfterManual,
              viewport: viewportAfterManual,
              split: {
                mode: editorViewModeFor(path, item),
                contentSplit: panel.querySelector('.file-editor-content')?.classList.contains('split-preview') === true,
                zoomMode: splitPreview.dataset.previewZoomMode || '',
                zoomValue: splitPreview.querySelector('.file-editor-preview-zoom-value')?.textContent || '',
                toolbarActions: splitToolbarButtons.map(button => button.dataset.previewZoomAction),
                toolbarLabels: splitToolbarButtons.map(button => button.textContent),
                toolbarRects: splitToolbarButtons.map(button => rect(button)),
                imageRect: splitImageRect,
                paneRect: rect(splitPreview),
                contentRect: rect(panel.querySelector('.file-editor-content')),
                measuring: splitMeasuring,
                viewportBeforeWheel: splitViewportBeforeWheel,
                wheelPrevented: splitWheelPrevented,
                zoomValueBeforeWheel: splitZoomValueBeforeWheel,
                imageRectAfterWheel: splitImageRectAfterWheel,
                imageRectAfterManualZoom: splitImageRectAfterManualZoom,
                panStart: splitPanStart,
                panAfter: {
                  scrollLeft: splitViewport.scrollLeft,
                  scrollTop: splitViewport.scrollTop,
                },
                viewport: splitViewport ? {
                  clientWidth: splitViewport.clientWidth,
                  clientHeight: splitViewport.clientHeight,
                  scrollWidth: splitViewport.scrollWidth,
                  scrollHeight: splitViewport.scrollHeight,
                } : null,
              },
              svgText,
              probeSvgText,
              probeNativeLabelTexts,
              previewNativeLabelTexts,
              config: window.mermaid?.mermaidAPI?.getConfig?.() || {},
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            };
            // Bright/Vanilla preview: the diagram's lines and labels must follow the white preview
            // surface (dark on white), not the dark app theme; the app `--text` (light) would paint
            // illegible light-gray lines on white. Run LAST (it re-renders the pane) so it does not
            // detach the split elements measured above. (User-reported gray-on-white.)
            let brightContrast = null;
            try {
              if (typeof setFileEditorPreviewDisplayMode === 'function') {
                setFileEditorPreviewDisplayMode('vanilla');
                const bp = panel.querySelector('.file-editor-preview-pane-panel');
                renderEditorPreviewPane(bp, path, mermaidSource, {context: 'split'});
                if (bp._previewAsync) await bp._previewAsync;
                await waitFor(() => {
                  const shell = bp.querySelector('.file-editor-preview-zoom-shell');
                  return !shell || !shell.classList.contains('file-editor-preview-zoom-measuring');
                }, {description: 'bright Mermaid zoom measurement'});
                const bImg = bp.querySelector('img.mermaid-preview-image');
                await waitImage(bImg);
                const bTxt = bImg && bImg.src ? await readBlobText(bImg.src) : '';
                const bDoc = new DOMParser().parseFromString(bTxt, 'image/svg+xml');
                const ep = bDoc.querySelector('.edgePaths path, .edgePath path, path.flowchart-link');
                const edgeStroke = ep ? (((/stroke:\\s*(#[0-9a-fA-F]{3,8})/.exec(ep.getAttribute('style') || '') || [])[1]) || ep.getAttribute('stroke') || '') : '';
                const labelFills = Array.from(bDoc.querySelectorAll('.node text')).slice(0, 6)
                  .map(t => (/fill:\\s*(#[0-9a-fA-F]{3,8})/.exec(t.getAttribute('style') || '') || [])[1] || '');
                brightContrast = {
                  mode: typeof editorPreviewThemeState === 'function' ? editorPreviewThemeState() : '?',
                  paneBg: getComputedStyle(bp).backgroundColor,
                  edgeStroke,
                  labelFills,
                };
              }
                } catch (error) { brightContrast = {error: String(error)}; }
                result.brightContrast = brightContrast;
                const wheelProbe = panel.querySelector('.file-editor-preview-pane-panel .file-editor-preview-zoom-viewport');
                const wheelProbeImage = wheelProbe?.querySelector('.mermaid-preview-image');
                const wheelProbeShell = wheelProbe?.closest('.file-editor-preview-zoom-shell');
                wheelProbeShell?.querySelector('[data-preview-zoom-action="in"]')?.click();
                wheelProbeShell?.querySelector('[data-preview-zoom-action="in"]')?.click();
                await frame();
                await frame();
                if (wheelProbe && wheelProbeImage) wheelProbe.dataset.previewWheelProbe = 'mermaid';
            done(result);
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          }
        })();
        """,
        mermaid_uri,
        mermaid_source,
    )
    wheel_probe = browser.find_element(By.CSS_SELECTOR, '[data-preview-wheel-probe="mermaid"]')
    native_wheel_before = browser.execute_script(
        """const viewport = arguments[0]; const image = viewport.querySelector('.mermaid-preview-image');
        viewport.scrollTop = 0;
        const rect = image.getBoundingClientRect();
        return {scrollTop: viewport.scrollTop, scrollHeight: viewport.scrollHeight, clientHeight: viewport.clientHeight,
          image: {width: rect.width, height: rect.height}};""",
        wheel_probe,
    )
    ActionChains(browser).scroll_from_origin(ScrollOrigin.from_element(wheel_probe), 0, 220).perform()
    browser.execute_async_script("requestAnimationFrame(() => requestAnimationFrame(arguments[arguments.length - 1]));")
    native_wheel_after = browser.execute_script(
        """const viewport = arguments[0]; const image = viewport.querySelector('.mermaid-preview-image');
        const rect = image.getBoundingClientRect();
        return {scrollTop: viewport.scrollTop, image: {width: rect.width, height: rect.height}};""",
        wheel_probe,
    )
    assert "error" not in metrics, metrics
    assert metrics["imageExists"] is True, metrics
    assert metrics["imageSrcPrefix"] in ("blob:", "data:"), metrics
    assert metrics["naturalWidth"] > 0 and metrics["naturalHeight"] > 0, metrics
    assert metrics["imageRectFit"]["height"] > 340, metrics
    assert metrics["imageRectFit"]["height"] >= metrics["viewport"]["clientHeight"] - 40, metrics
    assert metrics["wheelPrevented"] is False, metrics
    assert abs(metrics["imageRectAfterWheel"]["width"] - metrics["imageRectFit"]["width"]) <= 1, metrics
    assert metrics["imageRectActual"]["height"] < metrics["imageRectFit"]["height"], metrics
    assert metrics["imageRectAfterZoom"]["height"] > metrics["imageRectActual"]["height"], metrics
    assert metrics["zoomToolbarExists"] is True, metrics
    assert metrics["zoomValueBefore"].endswith("%"), metrics
    assert metrics["zoomValueAfter"].endswith("%"), metrics
    assert metrics["zoomModeAfter"] == "manual", metrics
    assert metrics["viewport"]["scrollHeight"] > metrics["viewport"]["clientHeight"], metrics
    assert metrics["viewport"]["scrollTop"] > 0, metrics
    assert metrics["split"]["mode"] == "split", metrics
    assert metrics["split"]["contentSplit"] is True, metrics
    assert metrics["split"]["toolbarActions"] == ["out", "fit", "actual", "in"], metrics
    assert metrics["split"]["toolbarLabels"] == ["-", "Fit", "1:1", "+"], metrics
    for toolbar_rect in metrics["split"]["toolbarRects"]:
        assert toolbar_rect["width"] > 0 and toolbar_rect["height"] > 0, metrics
    assert metrics["split"]["zoomValueBeforeWheel"].endswith("%"), metrics
    assert metrics["split"]["wheelPrevented"] is False, metrics
    assert metrics["split"]["zoomMode"] == "manual", metrics
    # Split preview pane must occupy ~half the editor content (right half), NOT full width.
    # Regression: `.file-editor-preview-zoom-full { width:100% }` overrode `right:0` on the
    # absolutely-positioned `inset:0 0 0 50%` split pane, so it rendered full-width offset to 50%
    # and its right half (with the diagram) ran off-screen and was chopped. The image-vs-viewport
    # asserts below passed anyway because the viewport itself was the wrong (full) width.
    split_pane = metrics["split"]["paneRect"]
    split_content = metrics["split"]["contentRect"]
    # The diagram must reveal (not stay hidden) once its size settles; the reveal gate hides it only
    # while the pane height is still settling so it does not visibly jump from a transient fit size.
    assert metrics["split"]["measuring"] is False, metrics
    assert split_content["width"] > 0, metrics
    pane_ratio = split_pane["width"] / split_content["width"]
    assert 0.4 <= pane_ratio <= 0.6, {"pane_ratio": pane_ratio, "pane": split_pane, "content": split_content}
    assert split_pane["right"] <= split_content["right"] + 2, {"pane": split_pane, "content": split_content}
    assert metrics["split"]["imageRect"]["width"] <= metrics["split"]["viewport"]["clientWidth"] + 2, metrics
    assert metrics["split"]["imageRect"]["height"] <= metrics["split"]["viewport"]["clientHeight"] + 2, metrics
    # The diagram must fit inside the visible content area, not extend past its right edge.
    assert metrics["split"]["imageRect"]["right"] <= split_content["right"] + 2, {"image": metrics["split"]["imageRect"], "content": split_content}
    assert metrics["split"]["viewportBeforeWheel"]["scrollLeft"] == 0, metrics
    assert metrics["split"]["viewportBeforeWheel"]["scrollTop"] == 0, metrics
    assert abs(metrics["split"]["imageRectAfterWheel"]["width"] - metrics["split"]["imageRect"]["width"]) <= 1, metrics
    assert metrics["split"]["imageRectAfterManualZoom"]["width"] > metrics["split"]["imageRect"]["width"], metrics
    assert native_wheel_before["scrollHeight"] > native_wheel_before["clientHeight"], native_wheel_before
    assert native_wheel_after["scrollTop"] > native_wheel_before["scrollTop"], {
        "before": native_wheel_before,
        "after": native_wheel_after,
    }
    assert abs(native_wheel_after["image"]["width"] - native_wheel_before["image"]["width"]) <= 1, {
        "before": native_wheel_before,
        "after": native_wheel_after,
    }
    assert abs(native_wheel_after["image"]["height"] - native_wheel_before["image"]["height"]) <= 1, {
        "before": native_wheel_before,
        "after": native_wheel_after,
    }
    assert (
        metrics["split"]["panAfter"]["scrollLeft"] > metrics["split"]["panStart"]["scrollLeft"]
        or metrics["split"]["panAfter"]["scrollTop"] > metrics["split"]["panStart"]["scrollTop"]
    ), metrics
    probe_label_text = " ".join(metrics["probeNativeLabelTexts"])
    for label in ("Markdown pipeline", "Mermaid pipeline", "Media pipeline", "Preview pane", "Visible result"):
        assert label.replace(" ", "") in probe_label_text.replace(" ", ""), metrics
    assert "<foreignObject" not in metrics["svgText"], metrics
    preview_label_text = " ".join(metrics["previewNativeLabelTexts"])
    for label in ("Markdown pipeline", "Mermaid pipeline", "Media pipeline", "Preview pane", "Visible result"):
        assert label.replace(" ", "") in preview_label_text.replace(" ", ""), metrics
    assert "font-family:" in metrics["svgText"], metrics
    assert "font-weight:400" in metrics["svgText"], metrics
    assert re.search(r'fill="(?:#[0-9a-fA-F]{3,8}|rgb)', metrics["svgText"]), metrics
    assert metrics["config"]["htmlLabels"] is False, metrics
    # Dark-on-dark readability: every node label must contrast with its node's own background fill.
    # This catches the bug where default (dark-fill) nodes rendered dark text -> invisible, while the
    # label-presence/font assertions above still passed. Both light-on-dark and dark-on-light qualify.
    # Re-rendering identical Mermaid source must NOT rebuild the diagram (it flashed on every periodic
    # pane refresh because the reveal gate re-hid the rebuilt SVG). The tagged <img> must survive.
    assert metrics["idempotentRerender"] is True, metrics
    assert metrics["nodeContrast"]["failCount"] == 0, metrics["nodeContrast"]
    assert metrics["nodeContrast"]["minContrast"] is not None and metrics["nodeContrast"]["minContrast"] >= 3.0, metrics["nodeContrast"]
    # Bright/Vanilla preview: diagram lines and labels must follow the white preview surface (dark on
    # white), not the dark app theme that would paint illegible light-gray lines. (User-reported.)
    bright = metrics.get("brightContrast")
    assert bright and "error" not in bright, bright
    assert bright["mode"] in ("vanilla", "light"), bright
    edge_contrast = wcag_contrast_ratio(bright["edgeStroke"], bright["paneBg"])
    assert edge_contrast >= 3.0, {"edgeStroke": bright["edgeStroke"], "paneBg": bright["paneBg"], "contrast": edge_contrast}
    for fill in bright["labelFills"]:
        if not fill:
            continue
        label_contrast = wcag_contrast_ratio(fill, bright["paneBg"])
        assert label_contrast >= 3.0, {"labelFill": fill, "paneBg": bright["paneBg"], "contrast": label_contrast}
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics

    # Ensure the diagram has revealed (reveal gate cleared) before pixel-sampling the screenshot.
    WebDriverWait(browser, 3).until(lambda drv: drv.execute_script(
        "const s=document.querySelector('.file-editor-preview-pane-panel.file-editor-preview-zoom-shell');"
        " return !!s && !s.classList.contains('file-editor-preview-zoom-measuring');"))
    screenshot = browser_screenshot_rgb(browser)
    dpr = browser.execute_script("return window.devicePixelRatio || 1") or 1
    bright_pixels, bright_samples = count_rect_region_pixels(
        screenshot,
        dpr,
        metrics["split"]["imageRect"],
        {"left": 0.02, "top": 0.02, "right": 0.98, "bottom": 0.98},
        lambda pixel: max(pixel) > 145 and sum(pixel) > 390,
    )
    saturated_pixels, saturated_samples = count_rect_region_pixels(
        screenshot,
        dpr,
        metrics["split"]["imageRect"],
        {"left": 0.02, "top": 0.02, "right": 0.98, "bottom": 0.98},
        lambda pixel: max(pixel) - min(pixel) > 35 and max(pixel) > 110,
    )
    assert bright_pixels > 120, {"matches": bright_pixels, "samples": bright_samples, "rect": metrics["split"]["imageRect"]}
    assert saturated_pixels > 120, {"matches": saturated_pixels, "samples": saturated_samples, "rect": metrics["split"]["imageRect"]}


def test_editor_open_misleading_binary_uses_sniffed_preview_mime(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const originalFetch = window.fetch.bind(window);
            const path = '/home/test/repo/renamed.bin';
            const rawRequests = [];
            const fixturePng = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='), ch => ch.charCodeAt(0));
            const waitImage = image => new Promise(resolve => {
              if (!image) return resolve(false);
              if (image.complete && image.naturalWidth > 0) return resolve(true);
              const finish = () => resolve(image.naturalWidth > 0);
              image.addEventListener('load', finish, {once: true});
              image.addEventListener('error', finish, {once: true});
            });
            window.fetch = async (input, options = {}) => {
              const url = new URL(String(input), 'https://localhost');
              if (url.pathname === '/api/fs/read') {
                return new Response(JSON.stringify({error: 'file appears to be binary'}), {status: 415, headers: {'Content-Type': 'application/json'}});
              }
              if (url.pathname === '/api/fs/info') {
                return new Response(JSON.stringify({
                  path,
                  name: 'renamed.bin',
                  kind: 'file',
                  size: 18,
                  mtime: 33,
                  mtime_ns: 33000000000,
                  preview_mime: 'image/png',
                  realpath: path,
                  file_id: '1:2',
                  file_identity: 'id:1:2',
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
              if (url.pathname === '/api/fs/raw') {
                rawRequests.push(`${url.pathname}${url.search}`);
                return new Response(fixturePng, {status: 200, headers: {'Content-Type': 'image/png'}});
              }
              return originalFetch(input, options);
            };
            const item = await openFileInEditor(path, {name: 'renamed.bin', size: 18, mtime: 33}, {userInitiated: true});
            const panel = panelNodes.get(item);
            const preview = panel?.querySelector('.file-editor-preview-pane-panel');
            if (preview?._previewAsync) await preview._previewAsync;
            const image = preview?.querySelector('img.file-editor-preview-image');
            await waitImage(image);
            const state = fileStateFor(path);
            done({
              stateKind: state?.kind || '',
              mediaKind: state?.mediaKind || '',
              mime: state?.mime || '',
              previewHidden: preview?.hidden === true,
              imageSrc: image?.getAttribute('src') || '',
              imageNaturalWidth: image?.naturalWidth || 0,
              imageNaturalHeight: image?.naturalHeight || 0,
              rawRequests,
              mode: editorViewModeFor(path, item),
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          }
        })();
        """
    )
    expected_api_error = consume_only_expected_js_debug_api_error(
        browser,
        path="/api/fs/read",
        status=415,
        method="GET",
        query={"path": "/home/test/repo/renamed.bin"},
    )
    assert "error" not in metrics, metrics
    assert metrics["stateKind"] == "media", metrics
    assert metrics["mediaKind"] == "image", metrics
    assert metrics["mime"] == "image/png", metrics
    assert metrics["previewHidden"] is False, metrics
    assert metrics["imageSrc"].startswith("blob:"), metrics
    assert metrics["imageNaturalWidth"] > 0 and metrics["imageNaturalHeight"] > 0, metrics
    assert "/api/fs/raw?path=%2Fhome%2Ftest%2Frepo%2Frenamed.bin&v=33" in metrics["rawRequests"], metrics
    assert metrics["mode"] == "preview", metrics
    assert len(metrics["errors"]) == 1, metrics
    assert all(expected_api_error.get(key) == value for key, value in metrics["errors"][0].items()), {
        "initial": metrics["errors"][0],
        "retired": expected_api_error,
    }
    assert metrics["rejections"] == [], metrics


def test_preview_registry_structured_table_and_offline_markdown(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
        try {
        const originalFetch = window.fetch.bind(window);
        const rawRequests = [];
        const fixturePng = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='), ch => ch.charCodeAt(0));
        const waitImage = image => new Promise(resolve => {
          if (!image) return resolve(false);
          if (image.complete && image.naturalWidth > 0) return resolve(true);
          const finish = () => resolve(image.naturalWidth > 0);
          image.addEventListener('load', finish, {once: true});
          image.addEventListener('error', finish, {once: true});
        });
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), window.location.href);
          if (url.pathname === '/api/fs/raw') {
            rawRequests.push(`${url.pathname}${url.search}`);
            return new Response(fixturePng, {status: 200, headers: {'Content-Type': 'image/png'}});
          }
          return originalFetch(input, options);
        };
        const mount = document.getElementById('grid');
        const makePanel = (path, state, mode = 'preview') => {
          const item = fileEditorItemFor(path);
          setFileState(path, state);
          setFileEditorViewMode(path, mode, item);
          addFileEditorTabItem(path, item);
          const panel = createFileEditorPanel(item);
          panel.classList.add('active-pane');
          panel.style.width = '920px';
          panel.style.height = '520px';
          panelNodes.set(item, panel);
          mount.append(panel);
          renderFileEditorPanel(panel, item);
          return {item, panel};
        };
        delete window.marked;
        delete window.hljs;
        const mdText = [
          '# Offline Preview',
          '',
          '| A | B |',
          '|---|---|',
          '| 1 | 2 |',
          '',
          '- [ ] task one',
          '',
          '![local](./asset dir/a.png "title")',
          '',
          '<script>bad()</script><svg><script>bad()</script></svg>',
          '',
          '```js',
          'const value = 1;',
          '```',
          '',
        ].join('\\n');
        const markdown = makePanel('/home/test/repo/docs/README.md', {kind: 'text', content: mdText, original: mdText, dirty: false, language: 'markdown'});
        const validJson = makePanel('/home/test/repo/config.json', {kind: 'text', content: '{"b":2,"a":1}', original: '{"b":2,"a":1}', dirty: false, language: 'json'});
        const invalidJson = makePanel('/home/test/repo/bad.json', {kind: 'text', content: '{"b":', original: '{"b":', dirty: false, language: 'json'});
        const jsonl = makePanel('/home/test/repo/events.jsonl', {kind: 'text', content: [
          JSON.stringify({timestamp: '2026-07-12T19:00:00Z', type: 'assistant', payload: {message: 'short'}}),
          JSON.stringify({timestamp: '2026-07-12T19:00:01Z', type: 'tool', payload: {message: 'x'.repeat(120)}}),
        ].join('\\n'), original: '', dirty: false, language: 'json'});
        const badJsonl = makePanel('/home/test/repo/bad.jsonl', {kind: 'text', content: '{"id":1}\\n{"id":\\n', original: '', dirty: false, language: 'json'});
        const geojson = makePanel('/home/test/repo/map.geojson', {kind: 'text', content: '{"type":"FeatureCollection","features":[]}', original: '', dirty: false, language: 'json'});
        const notebookText = JSON.stringify({cells: [{cell_type: 'markdown', source: ['# Title\\n']}, {cell_type: 'code', source: ['print(1)\\n'], outputs: [{output_type: 'stream'}]}]});
        const notebook = makePanel('/home/test/repo/notebook.ipynb', {kind: 'text', content: notebookText, original: '', dirty: false, language: 'json'});
        const yaml = makePanel('/home/test/repo/config.yaml', {kind: 'text', content: 'name: test\\nitems:\\n  - one\\n', original: '', dirty: false, language: 'yaml'});
        const toml = makePanel('/home/test/repo/config.toml', {kind: 'text', content: 'name = "test"\\ncount = 2\\n', original: '', dirty: false, language: 'toml'});
        const xml = makePanel('/home/test/repo/layout.drawio', {kind: 'text', content: '<mxfile><diagram>safe</diagram></mxfile>', original: '', dirty: false, language: 'xml'});
        const envFile = makePanel('/home/test/repo/.env', {kind: 'text', content: 'A=1\\nB=two\\n', original: '', dirty: false, language: 'ini'});
        const csv = makePanel('/home/test/repo/table.csv', {kind: 'text', content: 'name,count\\nalpha,1\\n"beta,gamma",2\\n', original: '', dirty: false, language: 'text'});
        const tsv = makePanel('/home/test/repo/table.tsv', {kind: 'text', content: 'name\\tcount\\nalpha\\t1\\n', original: '', dirty: false, language: 'text'});
        const mdPreview = markdown.panel.querySelector('.file-editor-preview-pane-panel');
        if (mdPreview?._previewAsync) await mdPreview._previewAsync;
        const image = mdPreview.querySelector('img[alt="local"]');
        await waitImage(image);
        const jsonlThemeMetrics = light => {
          document.body.classList.toggle('theme-light', light);
          document.body.classList.toggle('editor-theme-light', light);
          const heading = jsonl.panel.querySelector('.file-editor-jsonl-preview th');
          const cell = jsonl.panel.querySelector('.file-editor-jsonl-preview td');
          const headingRect = heading.getBoundingClientRect();
          const cellRect = cell.getBoundingClientRect();
          return {
            headingLeft: headingRect.left,
            cellLeft: cellRect.left,
            headingWidth: headingRect.width,
            cellWidth: cellRect.width,
            headingBackground: getComputedStyle(heading).backgroundColor,
            cellColor: getComputedStyle(cell).color,
          };
        };
        const jsonlDarkTheme = jsonlThemeMetrics(false);
        const jsonlLightTheme = jsonlThemeMetrics(true);
        const jsonlLayout = () => {
          const preview = jsonl.panel.querySelector('.file-editor-jsonl-preview');
          const table = preview?.querySelector('table');
          const payload = preview?.querySelector('th.file-editor-jsonl-payload');
          const compact = preview?.querySelector('th.file-editor-jsonl-compact-column');
          const previewRect = preview?.getBoundingClientRect();
          const tableRect = table?.getBoundingClientRect();
          const payloadRect = payload?.getBoundingClientRect();
          const compactRect = compact?.getBoundingClientRect();
          return {
            previewWidth: previewRect?.width || 0,
            tableWidth: tableRect?.width || 0,
            payloadWidth: payloadRect?.width || 0,
            compactWidth: compactRect?.width || 0,
            payloadTitle: Array.from(preview?.querySelectorAll('td.file-editor-jsonl-payload') || []).at(-1)?.title || '',
          };
        };
        const jsonlWideLayout = jsonlLayout();
        jsonl.panel.style.width = '460px';
        const jsonlNarrowLayout = jsonlLayout();
        document.body.classList.remove('theme-light', 'editor-theme-light');
        const result = {
          markdown: {
            heading: mdPreview.querySelector('h1')?.textContent || '',
            tableCells: Array.from(mdPreview.querySelectorAll('table th, table td')).map(node => node.textContent),
            checkboxCount: mdPreview.querySelectorAll('input.markdown-task-checkbox').length,
            imageSrc: image?.getAttribute('src') || '',
            imageNaturalWidth: image?.naturalWidth || 0,
            imageNaturalHeight: image?.naturalHeight || 0,
            imageTitle: image?.getAttribute('title') || '',
            scriptCount: mdPreview.querySelectorAll('script').length,
            svgCount: mdPreview.querySelectorAll('svg').length,
            codeHtml: mdPreview.querySelector('pre code')?.innerHTML || '',
          },
          validJson: {
            kind: previewKindForPath('/home/test/repo/config.json'),
            header: validJson.panel.querySelector('.file-editor-data-preview-header')?.textContent || '',
            text: validJson.panel.querySelector('.file-editor-data-preview code')?.textContent || '',
          },
          invalidJson: {
            header: invalidJson.panel.querySelector('.file-editor-data-preview-header')?.textContent || '',
            error: invalidJson.panel.querySelector('.file-editor-preview-error')?.textContent || '',
          },
          jsonl: {
            header: jsonl.panel.querySelector('.file-editor-data-preview-header')?.textContent || '',
            headers: Array.from(jsonl.panel.querySelectorAll('.file-editor-jsonl-preview th')).map(node => node.textContent),
            cells: Array.from(jsonl.panel.querySelectorAll('.file-editor-jsonl-preview td')).map(node => node.textContent),
            modeButtons: Array.from(jsonl.panel.querySelectorAll('[data-editor-mode]')).filter(node => !node.hidden).map(node => node.dataset.editorMode),
            tableOverflowX: getComputedStyle(jsonl.panel.querySelector('.file-editor-jsonl-preview')).overflowX,
            darkTheme: jsonlDarkTheme,
            lightTheme: jsonlLightTheme,
            wideLayout: jsonlWideLayout,
            narrowLayout: jsonlNarrowLayout,
          },
          badJsonl: {
            header: badJsonl.panel.querySelector('.file-editor-data-preview-header')?.textContent || '',
            unparsed: badJsonl.panel.querySelector('[data-unparsed-line]')?.textContent || '',
            unparsedLine: badJsonl.panel.querySelector('[data-unparsed-line]')?.dataset.unparsedLine || '',
          },
          geojson: {
            header: geojson.panel.querySelector('.file-editor-data-preview-header')?.textContent || '',
            text: geojson.panel.querySelector('.file-editor-data-preview code')?.textContent || '',
          },
          notebook: {
            header: notebook.panel.querySelector('.file-editor-data-preview-header')?.textContent || '',
            text: notebook.panel.querySelector('.file-editor-data-preview code')?.textContent || '',
          },
          yaml: {
            header: yaml.panel.querySelector('.file-editor-data-preview-header')?.textContent || '',
            text: yaml.panel.querySelector('.file-editor-data-preview code')?.textContent || '',
          },
          toml: {
            header: toml.panel.querySelector('.file-editor-data-preview-header')?.textContent || '',
            text: toml.panel.querySelector('.file-editor-data-preview code')?.textContent || '',
          },
          xml: {
            header: xml.panel.querySelector('.file-editor-data-preview-header')?.textContent || '',
            text: xml.panel.querySelector('.file-editor-data-preview code')?.textContent || '',
            scriptCount: xml.panel.querySelectorAll('script').length,
          },
          envFile: {
            header: envFile.panel.querySelector('.file-editor-data-preview-header')?.textContent || '',
            text: envFile.panel.querySelector('.file-editor-data-preview code')?.textContent || '',
          },
          csv: {
            header: csv.panel.querySelector('.file-editor-data-preview-header')?.textContent || '',
            cells: Array.from(csv.panel.querySelectorAll('.file-editor-table-preview th, .file-editor-table-preview td')).map(node => node.textContent),
          },
          tsv: {
            header: tsv.panel.querySelector('.file-editor-data-preview-header')?.textContent || '',
            cells: Array.from(tsv.panel.querySelectorAll('.file-editor-table-preview th, .file-editor-table-preview td')).map(node => node.textContent),
          },
          rawRequests,
          errors: jsDebugFailureEvents('error'),
          rejections: jsDebugFailureEvents('rejection'),
        };
        done(result);
        } catch (error) {
          done({error: String(error), stack: error?.stack || '', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
        }
        })();
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["markdown"]["heading"] == "Offline Preview", metrics
    assert metrics["markdown"]["tableCells"] == ["A", "B", "1", "2"], metrics
    assert metrics["markdown"]["checkboxCount"] == 1, metrics
    assert metrics["markdown"]["imageSrc"].startswith("blob:"), metrics
    assert metrics["markdown"]["imageNaturalWidth"] > 0 and metrics["markdown"]["imageNaturalHeight"] > 0, metrics
    assert "/api/fs/raw?path=%2Fhome%2Ftest%2Frepo%2Fdocs%2Fasset%20dir%2Fa.png" in metrics["rawRequests"], metrics
    assert metrics["markdown"]["imageTitle"] == "title", metrics
    assert metrics["markdown"]["scriptCount"] == 0 and metrics["markdown"]["svgCount"] == 0, metrics
    assert "code-keyword" in metrics["markdown"]["codeHtml"], metrics
    assert metrics["validJson"]["kind"] == "structured", metrics
    assert "JSON preview" in metrics["validJson"]["header"], metrics
    assert '"a": 1' in metrics["validJson"]["text"], metrics
    assert "JSON parse error" in metrics["invalidJson"]["header"], metrics
    assert metrics["invalidJson"]["error"], metrics
    assert "JSONL preview" in metrics["jsonl"]["header"], metrics
    assert metrics["jsonl"]["headers"] == ["timestamp", "type", "payload"], metrics
    assert metrics["jsonl"]["cells"][0:3] == ["2026-07-12T19:00:00Z", "assistant", '{"message":"short"}'], metrics
    assert metrics["jsonl"]["modeButtons"][:3] == ["edit", "preview", "split"], metrics
    assert metrics["jsonl"]["tableOverflowX"] == "auto", metrics
    for theme in ("darkTheme", "lightTheme"):
        assert abs(metrics["jsonl"][theme]["headingLeft"] - metrics["jsonl"][theme]["cellLeft"]) < 0.5, metrics
        assert abs(metrics["jsonl"][theme]["headingWidth"] - metrics["jsonl"][theme]["cellWidth"]) < 0.5, metrics
        assert metrics["jsonl"][theme]["headingBackground"] != "rgba(0, 0, 0, 0)", metrics
        assert metrics["jsonl"][theme]["cellColor"] != "rgba(0, 0, 0, 0)", metrics
    wide = metrics["jsonl"]["wideLayout"]
    narrow = metrics["jsonl"]["narrowLayout"]
    assert abs(wide["tableWidth"] - wide["previewWidth"]) < 1.5, metrics
    assert abs(narrow["tableWidth"] - narrow["previewWidth"]) < 1.5, metrics
    assert narrow["payloadWidth"] < wide["payloadWidth"], metrics
    assert wide["payloadWidth"] > wide["compactWidth"], metrics
    assert len(wide["payloadTitle"]) > 120, metrics
    assert metrics["badJsonl"]["unparsed"] == '{"id":' and metrics["badJsonl"]["unparsedLine"] == "2", metrics
    assert "GeoJSON preview" in metrics["geojson"]["header"] and '"FeatureCollection"' in metrics["geojson"]["text"], metrics
    assert "Notebook preview" in metrics["notebook"]["header"], metrics
    assert "2 cells" in metrics["notebook"]["text"] and "outputs hidden" in metrics["notebook"]["text"], metrics
    assert "YAML preview" in metrics["yaml"]["header"] and "items" in metrics["yaml"]["text"], metrics
    assert "TOML preview" in metrics["toml"]["header"] and "count" in metrics["toml"]["text"], metrics
    assert "Draw.io XML preview" in metrics["xml"]["header"] and "mxfile" in metrics["xml"]["text"], metrics
    assert metrics["xml"]["scriptCount"] == 0, metrics
    assert "Config preview" in metrics["envFile"]["header"] and "A=1" in metrics["envFile"]["text"], metrics
    assert "CSV preview" in metrics["csv"]["header"], metrics
    assert metrics["csv"]["cells"][:6] == ["name", "count", "alpha", "1", "beta,gamma", "2"], metrics
    assert "TSV preview" in metrics["tsv"]["header"], metrics
    assert metrics["tsv"]["cells"][:4] == ["name", "count", "alpha", "1"], metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics


def test_markdown_preview_media_and_mermaid_rendering(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const {frame} = window.__yolomuxTestHelpers;
            const originalFetch = window.fetch.bind(window);
            const rawRequests = [];
            const fixturePng = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='), ch => ch.charCodeAt(0));
            window.fetch = async (input, options = {}) => {
              const url = new URL(String(input), window.location.href);
              if (url.pathname === '/api/fs/raw') {
                rawRequests.push(`${url.pathname}${url.search}`);
                return new Response(fixturePng, {status: 200, headers: {'Content-Type': 'image/png'}});
              }
              return originalFetch(input, options);
            };
            const escapeHtml = value => String(value || '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
            window.marked = {
              parse(markdown) {
                const parts = [];
                for (const line of String(markdown || '').split('\\n')) {
                  if (line.startsWith('<img ')) {
                    parts.push(line);
                    continue;
                  }
                  if (line.startsWith('![')) {
                    const match = line.match(/^!\\[([^\\]]*)\\]\\(([^)]+)\\)/);
                    if (match) parts.push(`<p><img alt="${escapeHtml(match[1])}" src="${escapeHtml(match[2])}"></p>`);
                    continue;
                  }
                  if (line.startsWith('```mermaid')) {
                    window.__mermaidFenceOpen = true;
                    window.__mermaidSource = [];
                    continue;
                  }
                  if (line.startsWith('```') && window.__mermaidFenceOpen) {
                    parts.push(`<pre><code class="language-mermaid">${escapeHtml(window.__mermaidSource.join('\\n'))}</code></pre>`);
                    window.__mermaidFenceOpen = false;
                    window.__mermaidSource = [];
                    continue;
                  }
                  if (window.__mermaidFenceOpen) {
                    window.__mermaidSource.push(line);
                    continue;
                  }
                  if (line.trim()) parts.push(`<p>${escapeHtml(line)}</p>`);
                }
                return parts.join('');
              },
            };
            const pngDataUrl = (width, height, color) => {
              const canvas = document.createElement('canvas');
              canvas.width = width;
              canvas.height = height;
              const ctx = canvas.getContext('2d');
              ctx.fillStyle = color;
              ctx.fillRect(0, 0, width, height);
              return canvas.toDataURL('image/png');
            };
            const waitImage = image => new Promise(resolve => {
              if (!image) {
                resolve(false);
                return;
              }
              if (image.complete) {
                resolve(image.naturalWidth > 0);
                return;
              }
              const finish = () => resolve(image.naturalWidth > 0);
              image.addEventListener('load', finish, {once: true});
              image.addEventListener('error', finish, {once: true});
            });
            const mermaidCalls = [];
            window.mermaid = {
              initialize(config) {
                window.__mermaidConfig = config;
              },
              async render(id, source) {
                mermaidCalls.push({id, source});
                if (source.includes('BROKEN')) throw new Error('parse failed for test');
                return {
                  svg: '<svg onclick="evil()" viewBox="0 0 100 40"><script>alert(1)</script><foreignObject>x</foreignObject><image href="https://evil.test/a.png"/><a href="#local"><text>Graph OK</text></a><style>@import url(https://evil.test/x.css); .a { fill: red; }</style></svg>',
                };
              },
            };
            const path = '/home/test/repo/docs/README.md';
            const wideFixed = pngDataUrl(720, 120, '#f97316');
            const tallFixed = pngDataUrl(120, 720, '#14b8a6');
            const content = [
              '# Preview Media',
              '![local](./images/local pic.png?cache=1#frag)',
              '![bare](images/bare.png)',
              '![svg](../assets/logo.svg)',
              '![external](https://example.test/image.png)',
              '![unsafe](javascript:alert(1))',
              '![missing](./missing.png)',
              '<img alt="html bare" src="images/html-bare.png" width="300">',
              `<img alt="fixed wide" src="${wideFixed}" width="220">`,
              `<img alt="fixed tall" src="${tallFixed}" width="220">`,
              '```mermaid',
              'graph TD; A-->B;',
              '```',
              '```mermaid',
              'BROKEN',
              '```',
              '',
            ].join('\\n');
            const item = fileEditorItemFor(path);
            setFileState(path, {kind: 'text', content, original: content, dirty: false, language: 'markdown'});
            setFileEditorViewMode(path, 'preview', item);
            addFileEditorTabItem(path, item);
            const panel = createFileEditorPanel(item);
            panel.classList.add('active-pane');
            panel.style.width = '900px';
            panel.style.height = '520px';
            panelNodes.set(item, panel);
            document.getElementById('grid').append(panel);
            renderFileEditorPanel(panel, item);
            const preview = panel.querySelector('.file-editor-preview-pane-panel');
            const imageSnapshot = label => {
              const img = preview.querySelector(`img[alt="${label}"]`);
              const box = img?.getBoundingClientRect();
              return {
                exists: Boolean(img),
                src: img?.getAttribute('src') || '',
                resolvedPath: img?.dataset?.resolvedPath || '',
                originalSrc: img?.dataset?.originalSrc || '',
                className: img?.className || '',
                hasSrc: img?.hasAttribute('src') === true,
                widthAttr: img?.getAttribute('width') || '',
                renderedWidth: box?.width || 0,
                renderedHeight: box?.height || 0,
                naturalWidth: img?.naturalWidth || 0,
                naturalHeight: img?.naturalHeight || 0,
              };
            };
            const fixedImages = Array.from(preview.querySelectorAll('img[alt^="fixed "]'));
            const externalImage = preview.querySelector('img[alt="external"]');
            const externalInitial = imageSnapshot('external');
            const externalCompletion = waitImage(externalImage);
            if (preview._previewAsync) await preview._previewAsync;
            await Promise.all(Array.from(preview.querySelectorAll('img.markdown-preview-image')).map(waitImage));
            await Promise.all(fixedImages.map(waitImage));
            await externalCompletion;
            const initialImages = {
              local: imageSnapshot('local'),
              bare: imageSnapshot('bare'),
              htmlBare: imageSnapshot('html bare'),
              svg: imageSnapshot('svg'),
              external: externalInitial,
              unsafe: imageSnapshot('unsafe'),
              missing: imageSnapshot('missing'),
              fixedWide: imageSnapshot('fixed wide'),
              fixedTall: imageSnapshot('fixed tall'),
            };
            const missing = preview.querySelector('img[alt="missing"]');
            if (missing) missing.dispatchEvent(new Event('error'));
            const brokenText = Array.from(preview.querySelectorAll('.markdown-image-error'))
              .find(node => String(node.textContent || '').includes('/home/test/repo/docs/missing.png'))?.textContent || '';
            await frame();
            await frame();
            const mermaidImage = preview.querySelector('.mermaid-preview-host img.mermaid-preview-image');
            const mermaidError = preview.querySelector('.mermaid-preview-error');
            done({
              initialImages,
              brokenText,
              mermaid: {
                imageExists: Boolean(mermaidImage),
                imageSrcPrefix: String(mermaidImage?.getAttribute('src') || '').slice(0, 5),
                errorText: mermaidError?.textContent || '',
                hostCount: preview.querySelectorAll('.mermaid-preview-host').length,
                inlineSvgCount: preview.querySelectorAll('svg').length,
                scriptCount: preview.querySelectorAll('script').length,
                calls: mermaidCalls,
                config: window.__mermaidConfig || {},
              },
              previewText: preview.textContent || '',
              rawRequests,
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          }
        })();
        """
    )
    assert_only_expected_browser_network_error(
        browser,
        url="https://example.test/image.png",
        reason="net::ERR_NAME_NOT_RESOLVED",
    )
    assert "error" not in metrics, metrics
    assert metrics["initialImages"]["local"]["exists"] is True, metrics
    assert metrics["initialImages"]["local"]["src"].startswith("blob:"), metrics
    assert metrics["initialImages"]["local"]["naturalWidth"] > 0 and metrics["initialImages"]["local"]["naturalHeight"] > 0, metrics
    assert metrics["initialImages"]["local"]["resolvedPath"] == "/home/test/repo/docs/images/local pic.png", metrics
    assert metrics["initialImages"]["local"]["originalSrc"] == "./images/local pic.png?cache=1#frag", metrics
    assert "markdown-preview-image" in metrics["initialImages"]["local"]["className"], metrics
    assert metrics["initialImages"]["bare"]["src"].startswith("blob:"), metrics
    assert metrics["initialImages"]["bare"]["naturalWidth"] > 0 and metrics["initialImages"]["bare"]["naturalHeight"] > 0, metrics
    assert metrics["initialImages"]["bare"]["resolvedPath"] == "/home/test/repo/docs/images/bare.png", metrics
    assert metrics["initialImages"]["bare"]["originalSrc"] == "images/bare.png", metrics
    assert "markdown-preview-image" in metrics["initialImages"]["bare"]["className"], metrics
    assert metrics["initialImages"]["htmlBare"]["src"].startswith("blob:"), metrics
    assert metrics["initialImages"]["htmlBare"]["naturalWidth"] > 0 and metrics["initialImages"]["htmlBare"]["naturalHeight"] > 0, metrics
    assert metrics["initialImages"]["htmlBare"]["resolvedPath"] == "/home/test/repo/docs/images/html-bare.png", metrics
    assert metrics["initialImages"]["htmlBare"]["originalSrc"] == "images/html-bare.png", metrics
    assert metrics["initialImages"]["htmlBare"]["widthAttr"] == "300", metrics
    assert "markdown-preview-image" in metrics["initialImages"]["htmlBare"]["className"], metrics
    assert metrics["initialImages"]["fixedWide"]["exists"] is True, metrics
    assert metrics["initialImages"]["fixedTall"]["exists"] is True, metrics
    assert metrics["initialImages"]["fixedWide"]["widthAttr"] == "220", metrics
    assert metrics["initialImages"]["fixedTall"]["widthAttr"] == "220", metrics
    assert "markdown-preview-image" in metrics["initialImages"]["fixedWide"]["className"], metrics
    assert "markdown-preview-image" in metrics["initialImages"]["fixedTall"]["className"], metrics
    assert metrics["initialImages"]["fixedWide"]["naturalWidth"] == 720 and metrics["initialImages"]["fixedWide"]["naturalHeight"] == 120, metrics
    assert metrics["initialImages"]["fixedTall"]["naturalWidth"] == 120 and metrics["initialImages"]["fixedTall"]["naturalHeight"] == 720, metrics
    assert abs(metrics["initialImages"]["fixedWide"]["renderedWidth"] - 220) <= 1, metrics
    assert abs(metrics["initialImages"]["fixedTall"]["renderedWidth"] - 220) <= 1, metrics
    assert metrics["initialImages"]["svg"]["src"].startswith("blob:"), metrics
    assert metrics["initialImages"]["svg"]["naturalWidth"] > 0 and metrics["initialImages"]["svg"]["naturalHeight"] > 0, metrics
    assert metrics["initialImages"]["svg"]["resolvedPath"] == "/home/test/repo/assets/logo.svg", metrics
    for expected in (
        "/api/fs/raw?path=%2Fhome%2Ftest%2Frepo%2Fdocs%2Fimages%2Flocal%20pic.png",
        "/api/fs/raw?path=%2Fhome%2Ftest%2Frepo%2Fdocs%2Fimages%2Fbare.png",
        "/api/fs/raw?path=%2Fhome%2Ftest%2Frepo%2Fdocs%2Fimages%2Fhtml-bare.png",
        "/api/fs/raw?path=%2Fhome%2Ftest%2Frepo%2Fassets%2Flogo.svg",
    ):
        assert expected in metrics["rawRequests"], metrics
    assert metrics["initialImages"]["external"]["src"] == "https://example.test/image.png", metrics
    assert metrics["initialImages"]["external"]["resolvedPath"] == "", metrics
    assert metrics["initialImages"]["unsafe"]["exists"] is True, metrics
    assert metrics["initialImages"]["unsafe"]["hasSrc"] is False, metrics
    assert "Image unavailable: /home/test/repo/docs/missing.png" in metrics["brokenText"], metrics
    assert "Open" in metrics["brokenText"] and "Download" in metrics["brokenText"], metrics
    assert metrics["mermaid"]["imageExists"] is True, metrics
    assert metrics["mermaid"]["imageSrcPrefix"] in ("blob:", "data:"), metrics
    assert "Mermaid diagram could not be rendered" in metrics["mermaid"]["errorText"], metrics
    assert "BROKEN" in metrics["mermaid"]["errorText"], metrics
    assert metrics["mermaid"]["hostCount"] == 2, metrics
    assert metrics["mermaid"]["inlineSvgCount"] == 0, metrics
    assert metrics["mermaid"]["scriptCount"] == 0, metrics
    assert len(metrics["mermaid"]["calls"]) == 2, metrics
    assert metrics["mermaid"]["config"]["startOnLoad"] is False, metrics
    assert metrics["mermaid"]["config"]["securityLevel"] == "strict", metrics
    assert metrics["mermaid"]["config"]["deterministicIds"] is True, metrics
    assert metrics["mermaid"]["config"]["htmlLabels"] is False, metrics
    assert metrics["mermaid"]["config"]["flowchart"]["htmlLabels"] is False, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics


def test_markdown_split_preview_scroll_sync_tracks_source_lines_with_tall_images(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"], grid_width=920, grid_height=620)
    metrics = browser.execute_async_script(
        """
        const neutralDark = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const {frame} = window.__yolomuxTestHelpers;
            const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
            const escapeHtml = value => String(value || '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
            window.marked = {
              parse(markdown) {
                const parts = [];
                for (const line of String(markdown || '').split('\\n')) {
                  const trimmed = line.trim();
                  if (trimmed === '<details>' || trimmed === '</details>' || /^<summary>.*<\\/summary>$/.test(trimmed)) {
                    parts.push(trimmed);
                    continue;
                  }
                  const heading = trimmed.match(/^(#{1,6})\\s+(.+)$/);
                  if (heading) {
                    const level = heading[1].length;
                    parts.push(`<h${level}>${escapeHtml(heading[2])}</h${level}>`);
                    continue;
                  }
                  if (trimmed.startsWith('<img ')) {
                    parts.push(trimmed);
                    continue;
                  }
                  if (trimmed) parts.push(`<p>${escapeHtml(trimmed)}</p>`);
                }
                return parts.join('');
              },
            };
            const canvas = document.createElement('canvas');
            canvas.width = 120;
            canvas.height = 720;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#14b8a6';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.fillStyle = neutralDark;
            ctx.font = '700 20px Arial';
            ctx.fillText('TALL', 30, 360);
            const tallImage = canvas.toDataURL('image/png');
            const path = '/home/test/repo/docs/media-scroll.md';
            const intro = Array.from({length: 18}, (_, index) => `Intro paragraph ${index + 1} before the expandable image block.`);
            const content = [
              '# Source Line Sync',
              '',
              ...intro.flatMap(line => [line, '']),
              '<details>',
              '<summary>Expandable image block</summary>',
              '',
              'Hidden paragraph before the tall image.',
              '',
              `<img alt="tall media" src="${tallImage}" width="420">`,
              '',
              'Hidden paragraph after the tall image.',
              '',
              '</details>',
              '',
              '## 2. Target Section',
              '',
              'The editor and preview should agree that this is the current section.',
              '',
              ...Array.from({length: 30}, (_, index) => `Tail paragraph ${index + 1}`),
              '',
            ].join('\\n');
            const targetLine = content.split('\\n').findIndex(line => line.startsWith('## 2.')) + 1;
            const item = fileEditorItemFor(path);
            setFileState(path, {kind: 'text', content, original: content, dirty: false, language: 'markdown'});
            setFileEditorViewMode(path, 'split', item);
            addFileEditorTabItem(path, item);
            const panel = createFileEditorPanel(item);
            panel.classList.add('active-pane');
            panel.style.width = '900px';
            panel.style.height = '560px';
            panelNodes.set(item, panel);
            document.getElementById('grid').append(panel);
            renderFileEditorPanel(panel, item);
            for (let attempt = 0; attempt < 120; attempt += 1) {
              const image = panel.querySelector('img[alt="tall media"]');
              const preview = panel.querySelector('.file-editor-preview-pane-panel');
              if (panel._cmView?.scrollDOM && preview && !preview.hidden && image?.complete && image.naturalHeight > 0 && preview.scrollHeight > preview.clientHeight) break;
              await frame();
            }
            const editorScroller = panel._cmView.scrollDOM;
            const preview = panel.querySelector('.file-editor-preview-pane-panel');
            const targetHeading = preview.querySelector(`h2[data-source-line="${targetLine}"]`);
            const tall = preview.querySelector('img[alt="tall media"]');
            const details = preview.querySelector('details');
            const summary = details?.querySelector('summary');
            const maxScroll = element => Math.max(0, Number(element.scrollHeight || 0) - Number(element.clientHeight || 0));
            const settle = async (ms = 40) => {
              await frame();
              await frame();
              await delay(ms);
            };
            const centerDelta = () => {
              const previewBox = preview.getBoundingClientRect();
              const headingBox = targetHeading.getBoundingClientRect();
              return headingBox.top - (previewBox.top + (preview.clientHeight * 0.5));
            };
            const editorCenterLine = () => {
              const block = panel._cmView.lineBlockAtHeight(editorScroller.scrollTop + (editorScroller.clientHeight * 0.5));
              return panel._cmView.state.doc.lineAt(block.from).number;
            };
            const snapshot = () => ({
              editorTop: editorScroller.scrollTop,
              previewTop: preview.scrollTop,
              targetCenterDelta: centerDelta(),
              editorCenterLine: editorCenterLine(),
              detailsOpen: details?.open === true,
              tallHeight: tall.getBoundingClientRect().height,
              previewClientHeight: preview.clientHeight,
            });
            const centerEditorOnTarget = async () => {
              const targetBlock = panel._cmView.lineBlockAt(panel._cmView.state.doc.line(targetLine).from);
              editorScroller.scrollTop = Math.min(maxScroll(editorScroller), Math.max(0, targetBlock.top - (editorScroller.clientHeight * 0.5)));
              editorScroller.dispatchEvent(new Event('scroll', {bubbles: true}));
              await settle();
              return snapshot();
            };
            const afterClosedEditorDrive = await centerEditorOnTarget();
            summary.click();
            await settle(80);
            for (let attempt = 0; attempt < 120; attempt += 1) {
              if (details.open && tall.complete && tall.naturalHeight > 0 && tall.getBoundingClientRect().height > preview.clientHeight) break;
              await frame();
            }
            await settle(80);
            const afterOpenToggle = snapshot();
            summary.click();
            await settle(80);
            const afterCloseToggle = snapshot();
            panel._previewLayoutScrollUntil = 0;
            preview.scrollTop = Math.min(maxScroll(preview), Math.max(0, scrollTopForPreviewElement(preview, targetHeading) - (preview.clientHeight * 0.5)));
            preview.dispatchEvent(new Event('scroll', {bubbles: true}));
            await settle();
            done({
              targetLine,
              afterClosedEditorDrive,
              afterOpenToggle,
              afterCloseToggle,
              afterPreviewDrive: {
                previewTop: preview.scrollTop,
                editorTop: editorScroller.scrollTop,
                editorCenterLine: editorCenterLine(),
              },
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          }
        })();
        """,
        ui_pin("paneMetaPathLight"),
    )
    assert "error" not in metrics, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics
    assert metrics["afterClosedEditorDrive"]["detailsOpen"] is False, metrics
    assert metrics["afterOpenToggle"]["detailsOpen"] is True, metrics
    assert metrics["afterCloseToggle"]["detailsOpen"] is False, metrics
    assert metrics["afterOpenToggle"]["tallHeight"] > metrics["afterOpenToggle"]["previewClientHeight"], metrics
    assert metrics["afterClosedEditorDrive"]["previewTop"] > 0, metrics
    assert metrics["afterOpenToggle"]["previewTop"] > metrics["afterClosedEditorDrive"]["previewTop"], metrics
    assert abs(metrics["afterClosedEditorDrive"]["targetCenterDelta"]) <= 32, metrics
    assert abs(metrics["afterOpenToggle"]["targetCenterDelta"]) <= 32, metrics
    assert abs(metrics["afterCloseToggle"]["targetCenterDelta"]) <= 32, metrics
    assert metrics["targetLine"] <= metrics["afterPreviewDrive"]["editorCenterLine"] <= metrics["targetLine"] + 1, metrics


def test_markdown_preview_visual_rendering_has_mermaid_labels_and_media(browser, tmp_path):
    browser.set_window_size(1200, 1200)
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"], grid_width=1000, grid_height=980)
    metrics = browser.execute_async_script(
        """
        const neutralDark = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const {frame} = window.__yolomuxTestHelpers;
            const waitImage = image => new Promise(resolve => {
              if (!image) {
                resolve(false);
                return;
              }
              if (image.complete && image.naturalWidth > 0) {
                resolve(true);
                return;
              }
              const finish = () => resolve(image.naturalWidth > 0);
              image.addEventListener('load', finish, {once: true});
              image.addEventListener('error', finish, {once: true});
            });
            const {rect} = window.__yolomuxTestHelpers;
            const readBlobText = url => new Promise((resolve, reject) => {
              const request = new XMLHttpRequest();
              request.open('GET', url);
              request.onload = () => resolve(String(request.responseText || ''));
              request.onerror = () => reject(new Error(`failed to read ${url}`));
              request.send();
            });
            const canvas = document.createElement('canvas');
            canvas.width = 120;
            canvas.height = 64;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#f97316';
            ctx.fillRect(0, 0, 60, 64);
            ctx.fillStyle = '#14b8a6';
            ctx.fillRect(60, 0, 60, 64);
            ctx.fillStyle = neutralDark;
            ctx.font = '700 18px Arial';
            ctx.fillText('PNG', 36, 38);
            const mediaDataUrl = canvas.toDataURL('image/png');
            delete window.marked;
            delete window.hljs;
            const mermaidCalls = [];
            window.mermaid = {
              initialize(config) {
                window.__visualMermaidConfig = config;
              },
              async render(id, source) {
                mermaidCalls.push({id, source});
                return {
                  svg: `
                    <svg xmlns="http://www.w3.org/2000/svg" width="720" height="260" viewBox="0 0 720 260">
                      <style>
                        .node rect { fill: #151922; stroke: #020617; stroke-width: 3px; }
                        .nodeLabel { color: #020617; font-family: Arial, sans-serif; font-size: 22px; font-weight: 700; }
                        .flowchart-link { stroke: #020617; stroke-width: 4px; fill: none; }
                        marker path { fill: #020617; stroke: #020617; }
                      </style>
                      <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z"/></marker></defs>
                      <rect x="1" y="1" width="718" height="258" rx="10" fill="#151922" stroke="#334155" stroke-width="2"/>
                      <g class="node"><rect x="32" y="74" width="150" height="58" rx="8"/><g class="label"><foreignObject x="32" y="74" width="150" height="58"><div><span class="nodeLabel">Data file</span></div></foreignObject></g></g>
                      <g class="node"><rect x="286" y="74" width="160" height="58" rx="8"/><g class="label"><foreignObject x="286" y="74" width="160" height="58"><div><span class="nodeLabel">Registry</span></div></foreignObject></g></g>
                      <g class="node"><rect x="542" y="34" width="150" height="58" rx="8"/><g class="label"><foreignObject x="542" y="34" width="150" height="58"><div><span class="nodeLabel">Preview</span></div></foreignObject></g></g>
                      <g class="node"><rect x="542" y="154" width="150" height="58" rx="8"/><g class="label"><foreignObject x="542" y="154" width="150" height="58"><div><span class="nodeLabel">Fallback</span></div></foreignObject></g></g>
                      <path class="flowchart-link" d="M182 103L286 103" marker-end="url(#arrow)"/>
                      <path class="flowchart-link" d="M446 86L542 63" marker-end="url(#arrow)"/>
                      <path class="flowchart-link" d="M446 120L542 183" marker-end="url(#arrow)"/>
                    </svg>
                  `,
                };
              },
            };
            const path = '/home/test/repo/docs/visual.md';
            const content = [
              '# Visual Preview',
              '',
              '| Format | Status |',
              '|---|---|',
              '| PNG | rendered |',
              '| Mermaid | labels visible |',
              '',
              `![generated media](${mediaDataUrl})`,
              '',
              '```mermaid',
              'flowchart LR',
              '  Data[Data file] --> Registry[Registry]',
              '  Registry --> Preview[Preview]',
              '  Registry --> Fallback[Fallback]',
              '```',
              '',
            ].join('\\n');
            const item = fileEditorItemFor(path);
            setFileState(path, {kind: 'text', content, original: content, dirty: false, language: 'markdown'});
            setFileEditorViewMode(path, 'preview', item);
            addFileEditorTabItem(path, item);
            const panel = createFileEditorPanel(item);
            panel.classList.add('active-pane');
            panel.style.width = '960px';
            panel.style.height = '620px';
            panelNodes.set(item, panel);
            const grid = document.getElementById('grid');
            grid.replaceChildren();
            grid.append(panel);
            renderFileEditorPanel(panel, item);
            const preview = panel.querySelector('.file-editor-preview-pane-panel');
            if (preview._previewAsync) await preview._previewAsync;
            await frame();
            await frame();
            const mediaImage = preview.querySelector('img[alt="generated media"]');
            const mermaidImage = preview.querySelector('img.mermaid-preview-image');
            await Promise.all([waitImage(mediaImage), waitImage(mermaidImage)]);
            await frame();
            await frame();
            const mermaidSvgText = mermaidImage?.src ? await readBlobText(mermaidImage.src) : '';
            done({
              previewText: preview.textContent || '',
              tableCells: Array.from(preview.querySelectorAll('table th, table td')).map(node => node.textContent),
              mediaRect: rect(mediaImage),
              mediaNaturalWidth: mediaImage?.naturalWidth || 0,
              mediaNaturalHeight: mediaImage?.naturalHeight || 0,
              mermaidRect: rect(mermaidImage),
              mermaidNaturalWidth: mermaidImage?.naturalWidth || 0,
              mermaidNaturalHeight: mermaidImage?.naturalHeight || 0,
              mermaidCalls,
              mermaidSvgText,
              mermaidConfig: window.__visualMermaidConfig || {},
              labelRegions: {
                data: {left: 0.08, top: 0.35, right: 0.24, bottom: 0.46},
                registry: {left: 0.42, top: 0.35, right: 0.60, bottom: 0.46},
                preview: {left: 0.77, top: 0.19, right: 0.95, bottom: 0.31},
                fallback: {left: 0.76, top: 0.65, right: 0.96, bottom: 0.77},
              },
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          }
        })();
        """,
        ui_pin("paneMetaPathLight"),
    )
    assert "error" not in metrics, metrics
    assert metrics["tableCells"] == ["Format", "Status", "PNG", "rendered", "Mermaid", "labels visible"], metrics
    assert metrics["mediaNaturalWidth"] == 120 and metrics["mediaNaturalHeight"] == 64, metrics
    assert metrics["mermaidNaturalWidth"] >= 700 and metrics["mermaidNaturalHeight"] >= 250, metrics
    assert metrics["mermaidCalls"] and "Data[Data file]" in metrics["mermaidCalls"][0]["source"], metrics
    assert "<foreignObject" not in metrics["mermaidSvgText"], metrics
    assert "Data file" in metrics["mermaidSvgText"], metrics
    assert "fill:#e4e8ee" in metrics["mermaidSvgText"], metrics
    assert "stroke:#e4e8ee" in metrics["mermaidSvgText"], metrics
    assert "font-weight:400" in metrics["mermaidSvgText"], metrics
    assert "stroke:none" in metrics["mermaidSvgText"], metrics
    assert metrics["mermaidConfig"]["flowchart"]["htmlLabels"] is False, metrics
    assert metrics["mermaidConfig"]["htmlLabels"] is False, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics

    # Wait for every zoom surface (the embedded Mermaid diagram) to reveal before pixel-sampling;
    # the reveal gate keeps the diagram hidden until its size settles so it does not visibly jump.
    WebDriverWait(browser, 3).until(lambda drv: drv.execute_script(
        "return Array.from(document.querySelectorAll('.file-editor-preview-zoom-shell'))"
        ".every(s => !s.classList.contains('file-editor-preview-zoom-measuring'));"))
    screenshot = browser_screenshot_rgb(browser)
    dpr = browser.execute_script("return window.devicePixelRatio || 1") or 1
    saturated_media, media_samples = count_rect_region_pixels(
        screenshot,
        dpr,
        metrics["mediaRect"],
        {"left": 0.02, "top": 0.02, "right": 0.98, "bottom": 0.98},
        lambda pixel: max(pixel) - min(pixel) > 70 and max(pixel) > 150,
    )
    assert saturated_media > 300, {"matches": saturated_media, "samples": media_samples, "rect": metrics["mediaRect"]}

    bright_mermaid, mermaid_samples = count_rect_region_pixels(
        screenshot,
        dpr,
        metrics["mermaidRect"],
        {"left": 0.04, "top": 0.04, "right": 0.96, "bottom": 0.96},
        lambda pixel: max(pixel) > 155 and sum(pixel) > 390,
    )
    assert bright_mermaid > 300, {"matches": bright_mermaid, "samples": mermaid_samples, "rect": metrics["mermaidRect"]}


def test_preview_popout_snapshot_waits_for_media_and_mermaid(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const {frame} = window.__yolomuxTestHelpers;
            const originalFetch = window.fetch.bind(window);
            const rawRequests = [];
            window.fetch = async (input, options = {}) => {
              const url = new URL(String(input), window.location.href);
              if (url.pathname === '/api/fs/raw') rawRequests.push(`${url.pathname}${url.search}`);
              return originalFetch(input, options);
            };
            const escapeHtml = value => String(value || '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
            window.marked = {
              parse(markdown) {
                const lines = String(markdown || '').split('\\n');
                const parts = [];
                for (let index = 0; index < lines.length; index += 1) {
                  const line = lines[index];
                  const image = line.match(/^!\\[([^\\]]*)\\]\\(([^)]+)\\)/);
                  if (image) {
                    parts.push(`<p><img alt="${escapeHtml(image[1])}" src="${escapeHtml(image[2])}"></p>`);
                    continue;
                  }
                  if (line.startsWith('```mermaid')) {
                    const code = [];
                    index += 1;
                    while (index < lines.length && !lines[index].startsWith('```')) {
                      code.push(lines[index]);
                      index += 1;
                    }
                    parts.push(`<pre><code class="language-mermaid">${escapeHtml(code.join('\\n'))}</code></pre>`);
                    continue;
                  }
                  if (line.trim()) parts.push(`<p>${escapeHtml(line)}</p>`);
                }
                return parts.join('');
              },
            };
            window.mermaid = {
              initialize(config) {
                window.__popoutMermaidConfig = config;
              },
              async render() {
                await frame();
                return {svg: '<svg viewBox="0 0 100 40"><text>Popout Graph</text></svg>'};
              },
            };
            const path = '/home/test/repo/docs/README.md';
            const content = [
              '# Popout Preview',
              '![local](./assets/popout.png)',
              '```mermaid',
              'graph TD; A-->B;',
              '```',
              '',
            ].join('\\n');
            const item = fileEditorItemFor(path);
            setFileState(path, {kind: 'text', content, original: content, dirty: false, language: 'markdown'});
            const iframe = document.createElement('iframe');
            iframe.style.width = '900px';
            iframe.style.height = '520px';
            document.body.append(iframe);
            const immediate = renderedPreviewSnapshot(path, content);
            const completed = await renderedPreviewSnapshotAsync(path, content);
            writeFilePreviewPopoutDocument(path, iframe.contentWindow, completed);
            const doc = iframe.contentDocument;
            const root = doc.querySelector('[data-preview-root]');
            const rawImage = root?.querySelector('img[alt="local"]');
            done({
              immediateText: immediate.html,
              completedText: completed.html,
              rootClass: root?.className || '',
              rootHtml: root?.innerHTML || '',
              rawImageSrc: rawImage?.getAttribute('src') || '',
              rawRequests,
              mermaidExists: Boolean(root?.querySelector('img.mermaid-preview-image')),
              mermaidSrcPrefix: String(root?.querySelector('img.mermaid-preview-image')?.getAttribute('src') || '').slice(0, 5),
              toolbarText: doc.querySelector('.file-preview-popout-title')?.textContent || '',
              bodyClass: doc.body?.className || '',
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
    assert "Rendering Mermaid diagram" in metrics["immediateText"], metrics
    # The "Rendering..." placeholder reuses the shared blinking-ellipsis loader, not a static string.
    assert "moving-ellipsis" in metrics["immediateText"], metrics
    assert "Rendering Mermaid diagram" not in metrics["completedText"], metrics
    assert "blob:" in metrics["completedText"], metrics
    assert metrics["rawImageSrc"] == "" or metrics["rawImageSrc"].startswith("blob:"), metrics
    assert "/api/fs/raw?path=%2Fhome%2Ftest%2Frepo%2Fdocs%2Fassets%2Fpopout.png" in metrics["rawRequests"], metrics
    assert metrics["mermaidExists"] is True, metrics
    assert metrics["mermaidSrcPrefix"] in ("blob:", "data:"), metrics
    assert "README.md" in metrics["toolbarText"], metrics
    assert "file-preview-popout-window" in metrics["bodyClass"], metrics
    assert "markdown-body" in metrics["rootClass"], metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics


def test_preview_popout_zoom_controls_are_hydrated(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script(
        """
        const neutralDark = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const {frame} = window.__yolomuxTestHelpers;
            const waitImage = image => new Promise(resolve => {
              if (!image) {
                resolve(false);
                return;
              }
              if (image.complete && image.naturalWidth > 0) {
                resolve(true);
                return;
              }
              const finish = () => resolve(image.naturalWidth > 0);
              image.addEventListener('load', finish, {once: true});
              image.addEventListener('error', finish, {once: true});
            });
            const {rect} = window.__yolomuxTestHelpers;
            window.mermaid = {
              initialize(config) {
                window.__popoutZoomMermaidConfig = config;
              },
              async render() {
                return {
                  svg: `
                    <svg xmlns="http://www.w3.org/2000/svg" width="900" height="520" viewBox="0 0 900 520">
                      <rect x="1" y="1" width="898" height="518" fill="#f8fafc" stroke="#334155" stroke-width="2"/>
                      <rect x="120" y="120" width="240" height="90" fill="#dbeafe" stroke="#1d4ed8" stroke-width="4"/>
                      <text x="240" y="172" text-anchor="middle" font-family="Arial" font-size="34" font-weight="700">Popout</text>
                      <rect x="540" y="310" width="240" height="90" fill="#dcfce7" stroke="#166534" stroke-width="4"/>
                      <text x="660" y="362" text-anchor="middle" font-family="Arial" font-size="34" font-weight="700">Zoom</text>
                      <line x1="360" y1="165" x2="540" y2="355" stroke="${neutralDark}" stroke-width="6"/>
                    </svg>
                  `,
                };
              },
            };
            const path = '/home/test/repo/docs/popout.mmd';
            const content = 'flowchart LR\\n  Popout --> Zoom\\n';
            setFileState(path, {kind: 'text', content, original: content, dirty: false, language: 'mermaid'});
            const iframe = document.createElement('iframe');
            iframe.style.width = '980px';
            iframe.style.height = '720px';
            document.body.append(iframe);
            const snapshot = await renderedPreviewSnapshotAsync(path, content);
            writeFilePreviewPopoutDocument(path, iframe.contentWindow, snapshot);
            const doc = iframe.contentDocument;
            const win = iframe.contentWindow;
            await frame(win);
            await frame(win);
            const root = doc.querySelector('[data-preview-root]');
            const image = root.querySelector('img.mermaid-preview-image');
            await waitImage(image);
            await frame(win);
            await frame(win);
            const viewport = root.querySelector('.file-editor-preview-zoom-viewport');
            const value = () => root.querySelector('.file-editor-preview-zoom-value')?.textContent || '';
            const click = async action => {
              root.querySelector(`[data-preview-zoom-action="${action}"]`)?.click();
              await frame(win);
              await frame(win);
            };
            const initial = {value: value(), mode: root.dataset.previewZoomMode || '', rect: rect(image), viewport: rect(viewport)};
            await click('actual');
            const actual = {value: value(), mode: root.dataset.previewZoomMode || '', rect: rect(image)};
            const wheelRect = rect(viewport);
            const wheelEvent = new win.WheelEvent('wheel', {
              deltaY: -160,
              clientX: wheelRect.left + (wheelRect.width * 0.52),
              clientY: wheelRect.top + (wheelRect.height * 0.48),
              bubbles: true,
              cancelable: true,
            });
            const wheelPrevented = !viewport.dispatchEvent(wheelEvent);
            await frame(win);
            await frame(win);
            const wheelUnchanged = {value: value(), mode: root.dataset.previewZoomMode || '', rect: rect(image)};
            await click('in');
            await click('in');
            const zoomed = {
              value: value(),
              mode: root.dataset.previewZoomMode || '',
              rect: rect(image),
              stageRect: rect(root.querySelector('.file-editor-preview-zoom-stage')),
              viewportScrollWidth: viewport.scrollWidth,
              viewportClientWidth: viewport.clientWidth,
              viewportScrollHeight: viewport.scrollHeight,
              viewportClientHeight: viewport.clientHeight,
            };
            const panBefore = {
              scrollLeft: Math.max(0, (viewport.scrollWidth - viewport.clientWidth) / 2),
              scrollTop: Math.max(0, (viewport.scrollHeight - viewport.clientHeight) / 2),
              maxScrollLeft: Math.max(0, viewport.scrollWidth - viewport.clientWidth),
              maxScrollTop: Math.max(0, viewport.scrollHeight - viewport.clientHeight),
              clientX: wheelRect.left + (wheelRect.width * 0.5),
              clientY: wheelRect.top + (wheelRect.height * 0.5),
            };
            viewport.scrollLeft = panBefore.scrollLeft;
            viewport.scrollTop = panBefore.scrollTop;
            await frame(win);
            const panStart = {scrollLeft: viewport.scrollLeft, scrollTop: viewport.scrollTop};
            const pointerDownPrevented = !viewport.dispatchEvent(new win.PointerEvent('pointerdown', {
              pointerId: 4,
              pointerType: 'mouse',
              button: 0,
              buttons: 1,
              clientX: panBefore.clientX,
              clientY: panBefore.clientY,
              bubbles: true,
              cancelable: true,
            }));
            const pointerMovePrevented = !viewport.dispatchEvent(new win.PointerEvent('pointermove', {
              pointerId: 4,
              pointerType: 'mouse',
              buttons: 1,
              clientX: panBefore.clientX - 90,
              clientY: panBefore.clientY - 70,
              bubbles: true,
              cancelable: true,
            }));
            viewport.dispatchEvent(new win.PointerEvent('pointerup', {
              pointerId: 4,
              pointerType: 'mouse',
              button: 0,
              buttons: 0,
              clientX: panBefore.clientX - 90,
              clientY: panBefore.clientY - 70,
              bubbles: true,
              cancelable: true,
            }));
            await frame(win);
            const panAfter = {scrollLeft: viewport.scrollLeft, scrollTop: viewport.scrollTop};
            await click('fit');
            const fit = {value: value(), mode: root.dataset.previewZoomMode || '', rect: rect(image)};
            done({
              rootClass: root.className || '',
              imageExists: Boolean(image),
              naturalWidth: image?.naturalWidth || 0,
              naturalHeight: image?.naturalHeight || 0,
              toolbarActions: Array.from(root.querySelectorAll('[data-preview-zoom-action]')).map(button => button.dataset.previewZoomAction),
              zoomPan: root.dataset.previewZoomPan || '',
              initial,
              actual,
              wheelPrevented,
              wheelUnchanged,
              zoomed,
            pointerDownPrevented,
            pointerMovePrevented,
            panBefore,
            panStart,
            panAfter,
              fit,
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          }
        })();
        """,
        ui_pin("paneMetaPathLight"),
    )
    assert "error" not in metrics, metrics
    assert "file-editor-preview-zoom-shell" in metrics["rootClass"], metrics
    assert metrics["imageExists"] is True, metrics
    assert metrics["naturalWidth"] == 900 and metrics["naturalHeight"] == 520, metrics
    assert metrics["toolbarActions"] == ["out", "fit", "actual", "in"], metrics
    assert metrics["zoomPan"] == "1", metrics
    assert metrics["initial"]["mode"] == "fit", metrics
    assert metrics["actual"]["mode"] == "actual", metrics
    assert metrics["actual"]["value"] == "100%", metrics
    assert metrics["wheelPrevented"] is False, metrics
    assert metrics["wheelUnchanged"]["mode"] == "actual", metrics
    assert abs(metrics["wheelUnchanged"]["rect"]["width"] - metrics["actual"]["rect"]["width"]) <= 1, metrics
    assert metrics["zoomed"]["mode"] == "manual", metrics
    assert metrics["zoomed"]["rect"]["width"] > metrics["actual"]["rect"]["width"], metrics
    assert metrics["zoomed"]["viewportScrollWidth"] > metrics["zoomed"]["viewportClientWidth"] or metrics["zoomed"]["viewportScrollHeight"] > metrics["zoomed"]["viewportClientHeight"], metrics
    assert metrics["pointerDownPrevented"] is True, metrics
    assert metrics["pointerMovePrevented"] is True, metrics
    assert metrics["panAfter"]["scrollLeft"] > metrics["panStart"]["scrollLeft"] or metrics["panAfter"]["scrollTop"] > metrics["panStart"]["scrollTop"], (
        f"zoomed={metrics['zoomed']} panBefore={metrics['panBefore']} panStart={metrics['panStart']} panAfter={metrics['panAfter']}"
    )
    assert metrics["fit"]["mode"] == "fit", metrics
    assert metrics["fit"]["rect"]["width"] <= metrics["initial"]["viewport"]["width"] + 2, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics


def test_markdown_preview_task_checkbox_updates_split_source_and_preview(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const {frame} = window.__yolomuxTestHelpers;
            const escapeHtml = value => String(value || '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
            window.marked = {
              parse(markdown) {
                const items = [];
                for (const line of String(markdown || '').split('\\n')) {
                  const match = line.match(/^\\s*- \\[([ xX])\\]\\s*(.*)$/);
                  if (!match) continue;
                  items.push(`<li class="task-list-item"><input type="checkbox" ${match[1].toLowerCase() === 'x' ? 'checked' : ''} disabled> ${escapeHtml(match[2])}</li>`);
                }
                return `<ul>${items.join('')}</ul>`;
              },
            };
            const path = '/home/test/yolomux.dev/TODO.md';
            const original = '- [ ] first task\\n- [x] second task\\n';
            const item = fileEditorItemFor(path);
            setFileState(path, {
              kind: 'text',
              content: original,
              original,
              dirty: false,
              language: 'markdown',
              gitRoot: '/home/test/yolomux.dev',
              gitTracked: true,
              gitHasHistory: true,
              gitHistory: [{ref: 'HEAD'}],
            });
            setFileEditorViewMode(path, 'split', item);
            addFileEditorTabItem(path, item);
            const panel = createFileEditorPanel(item);
            panel.classList.add('active-pane');
            panel.style.width = '980px';
            panel.style.height = '560px';
            panelNodes.set(item, panel);
            document.getElementById('grid').append(panel);
            renderFileEditorPanel(panel, item);
            const waitFor = window.__yolomuxTestWaitFor;
            const ready = await waitFor(() => panel.querySelectorAll('.file-editor-preview-pane-panel input.markdown-task-checkbox').length === 2 && panel._cmView?.state?.doc?.toString?.().includes('first task'));
            const before = {
              ready,
              content: fileState.get(path)?.content || '',
              checked: Array.from(panel.querySelectorAll('.file-editor-preview-pane-panel input.markdown-task-checkbox')).map(input => input.checked),
              disabled: Array.from(panel.querySelectorAll('.file-editor-preview-pane-panel input.markdown-task-checkbox')).map(input => input.disabled),
              cmText: panel._cmView?.state?.doc?.toString?.() || '',
            };
            const first = panel.querySelector('.file-editor-preview-pane-panel input.markdown-task-checkbox');
            first.click();
            await waitFor(() => (fileState.get(path)?.content || '').startsWith('- [x] first task'));
            await frame();
            await frame();
            const after = {
              content: fileState.get(path)?.content || '',
              dirty: fileState.get(path)?.dirty === true,
              checked: Array.from(panel.querySelectorAll('.file-editor-preview-pane-panel input.markdown-task-checkbox')).map(input => input.checked),
              sourceLines: Array.from(panel.querySelectorAll('.file-editor-preview-pane-panel input.markdown-task-checkbox')).map(input => input.dataset.sourceLine || ''),
              cmText: panel._cmView?.state?.doc?.toString?.() || '',
              previewText: panel.querySelector('.file-editor-preview-pane-panel')?.textContent || '',
              mode: editorViewModeFor(path, item),
              splitVisible: panel.querySelector('.file-editor-content')?.classList.contains('split-preview') === true,
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
            };
            done({before, after});
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          }
        })();
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["before"]["ready"] is True, metrics
    assert metrics["before"]["checked"] == [False, True], metrics
    assert metrics["before"]["disabled"] == [False, False], metrics
    assert metrics["after"]["content"] == "- [x] first task\n- [x] second task\n", metrics
    assert metrics["after"]["dirty"] is True, metrics
    assert metrics["after"]["checked"] == [True, True], metrics
    assert metrics["after"]["sourceLines"] == ["1", "2"], metrics
    assert metrics["after"]["cmText"] == metrics["after"]["content"], metrics
    assert "first task" in metrics["after"]["previewText"], metrics
    assert metrics["after"]["mode"] == "split", metrics
    assert metrics["after"]["splitVisible"] is True, metrics
    assert metrics["after"]["errors"] == [], metrics
    assert metrics["after"]["rejections"] == [], metrics


def test_markdown_preview_html_callout_uses_dark_highlight_in_dark_mode(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_script(
        """
        document.body.classList.remove('theme-light', 'editor-theme-light');
        document.body.classList.add('theme-dark', 'editor-theme-dark');
        const highlightCss = document.createElement('style');
        highlightCss.textContent = 'pre code.hljs { display: block; overflow-x: auto; padding: 1em; }';
        document.head.append(highlightCss);
        window.hljs = {highlightElement(block) { block.classList.add('hljs'); }};
        window.marked = {
          parse() {
            return `
              <table id="normal"><tr><td id="normal-cell">Normal dark preview table</td></tr></table>
              <blockquote id="md-warning">
                <p>[!WARNING] There is no one way.</p>
                <p><strong id="md-warning-strong">IMPORTANT NOTE:</strong> run <code id="md-warning-code">gpu-info</code> before continuing.</p>
              </blockquote>
              <blockquote id="md-caution">
                <p>[!CAUTION] Do not skip.</p>
                <p><strong id="md-caution-strong">IMPORTANT NOTE:</strong> read <code id="md-caution-code">OSS and OSRB</code> before continuing.</p>
                <pre><code id="md-caution-block-code">
oss scan
</code></pre>
              </blockquote>
              <blockquote id="md-caution-break">
                <p>[!CAUTION]<br><strong id="md-caution-break-strong">Familiarity with Docker is absolutely necessary.</strong></p>
              </blockquote>
              <blockquote id="md-caution-list">
                <p>[!CAUTION]<br><strong id="md-caution-list-strong">Familiarity with Docker is absolutely necessary.</strong> At minimum, understand Docker basics.</p>
                <ul id="md-caution-list-items">
                  <li>Docker images are static snapshots.</li>
                  <li>Runtime containers are live instances.</li>
                </ul>
              </blockquote>
              <pre id="edge-pre"><code id="edge-code">
python -V
</code></pre>
              <blockquote id="md-caution-direct">
                [!CAUTION]<br>
                <strong id="md-caution-direct-strong">IMPORTANT NOTE:</strong> protect the GPU.
              </blockquote>
              <table id="callout" bgcolor="#fff8cc" border="0" cellpadding="10" cellspacing="0" width="100%">
                <tr>
                  <td width="50" align="center">&#9888;</td>
                  <td id="callout-cell">There is no one way.<br><br><strong id="callout-strong">IMPORTANT NOTE:</strong> run <code id="callout-code">gpu-info</code> before continuing.</td>
                </tr>
              </table>
            `;
          },
        };
        const path = '/home/test/repo/CALLOUT.md';
        const content = 'legacy html callout';
        setFileState(path, {kind: 'text', content, original: content, dirty: false, language: 'markdown'});
        const container = document.createElement('div');
        container.className = 'file-editor-content';
        container.style.position = 'static';
        container.style.width = '900px';
        container.style.height = '520px';
        const preview = document.createElement('article');
        preview.id = 'preview';
        preview.className = 'file-editor-preview-pane-panel markdown-body';
        preview.style.position = 'static';
        container.append(preview);
        document.querySelector('#grid').replaceChildren(container);
        renderEditorPreviewPane(preview, path, content, {context: 'preview'});
        document.querySelectorAll('.markdown-html-light-bg').forEach(element => element.classList.remove('markdown-html-light-bg'));
        const alertSourceAnchor = document.createElement('span');
        alertSourceAnchor.className = 'markdown-source-anchor';
        alertSourceAnchor.dataset.sourceLine = '1';
        document.querySelector('#md-caution-list').append(alertSourceAnchor);
        const style = selector => getComputedStyle(document.querySelector(selector));
        const firstVisibleNode = selector => {
          const element = document.querySelector(selector);
          for (const node of element.childNodes) {
            if (node.nodeType === Node.TEXT_NODE && node.nodeValue.trim()) return '#text';
            if (node.nodeType === Node.ELEMENT_NODE && !node.classList.contains('markdown-source-anchor')) return node.tagName;
          }
          return '';
        };
        const tailGap = (containerSelector, lastSelector) => {
          const containerRect = document.querySelector(containerSelector).getBoundingClientRect();
          const lastRect = document.querySelector(lastSelector).getBoundingClientRect();
          return Math.round(containerRect.bottom - lastRect.bottom);
        };
        const calloutStyle = style('#callout');
        const calloutCellStyle = style('#callout-cell');
        const calloutCodeStyle = style('#callout-code');
        const mdWarningStyle = style('#md-warning');
        const mdWarningCodeStyle = style('#md-warning-code');
        const mdCautionStyle = style('#md-caution');
        const mdCautionCodeStyle = style('#md-caution-code');
        const mdCautionBreakStyle = style('#md-caution-break');
        const mdCautionDirectStyle = style('#md-caution-direct');
        const {probePaint} = window.__yolomuxTestHelpers;
            const expectedPreview = probePaint('color:var(--text);background:var(--markdown-preview-bg);border:1px solid var(--line)', preview);
        const expectedWarning = probePaint('color:var(--markdown-html-dark-text);background:var(--markdown-html-dark-bg);border:1px solid var(--markdown-html-dark-border)');
        const expectedCaution = probePaint('color:var(--markdown-html-dark-text);background:var(--markdown-alert-caution-dark-bg)');
        const expectedCode = probePaint('color:var(--markdown-html-dark-code);background:var(--markdown-html-dark-code-bg);border:1px solid var(--markdown-html-dark-code-border)');
        return {
          expectedPreview,
          expectedWarning,
          expectedCaution,
          expectedCode,
          lightClass: document.querySelector('#callout').classList.contains('markdown-html-light-bg'),
          mdWarningClass: document.querySelector('#md-warning').classList.contains('markdown-alert-warning'),
          mdWarningText: document.querySelector('#md-warning').textContent,
          mdWarningBg: mdWarningStyle.backgroundColor,
          mdWarningBorderLeftWidth: mdWarningStyle.borderLeftWidth,
          mdWarningColor: mdWarningStyle.color,
          mdWarningStrongColor: style('#md-warning-strong').color,
          mdWarningCodeColor: mdWarningCodeStyle.color,
          mdWarningCodeBg: mdWarningCodeStyle.backgroundColor,
          mdCautionClass: document.querySelector('#md-caution').classList.contains('markdown-alert-caution'),
          mdCautionText: document.querySelector('#md-caution').textContent,
          mdCautionBg: mdCautionStyle.backgroundColor,
          mdCautionBorderLeftWidth: mdCautionStyle.borderLeftWidth,
          mdCautionColor: mdCautionStyle.color,
          mdCautionStrongColor: style('#md-caution-strong').color,
          mdCautionCodeColor: mdCautionCodeStyle.color,
          mdCautionCodeBg: mdCautionCodeStyle.backgroundColor,
          mdCautionBlockCodeText: document.querySelector('#md-caution-block-code').textContent,
          mdCautionBreakClass: document.querySelector('#md-caution-break').classList.contains('markdown-alert-caution'),
          mdCautionBreakText: document.querySelector('#md-caution-break').textContent,
          mdCautionBreakBg: mdCautionBreakStyle.backgroundColor,
          mdCautionBreakFirstVisibleNode: firstVisibleNode('#md-caution-break > p'),
          mdCautionListClass: document.querySelector('#md-caution-list').classList.contains('markdown-alert-caution'),
          mdCautionListText: document.querySelector('#md-caution-list').textContent,
          mdCautionListFirstVisibleNode: firstVisibleNode('#md-caution-list > p'),
          mdCautionListTailGap: tailGap('#md-caution-list', '#md-caution-list-items'),
          alertSourceAnchorDisplay: style('#md-caution-list > .markdown-source-anchor').display,
          edgeCodeText: document.querySelector('#edge-code').textContent,
          edgeCodePaddingTop: style('#edge-code').paddingTop,
          edgeCodeDisplay: style('#edge-code').display,
          edgePreTailGap: tailGap('#edge-pre', '#edge-code'),
          mdCautionDirectClass: document.querySelector('#md-caution-direct').classList.contains('markdown-alert-caution'),
          mdCautionDirectText: document.querySelector('#md-caution-direct').textContent,
          mdCautionDirectBg: mdCautionDirectStyle.backgroundColor,
          mdCautionDirectStrongColor: style('#md-caution-direct-strong').color,
          mdCautionDirectFirstVisibleNode: firstVisibleNode('#md-caution-direct'),
          previewBg: style('#preview').backgroundColor,
          previewColor: style('#preview').color,
          normalColor: style('#normal-cell').color,
          normalBorderColor: style('#normal-cell').borderTopColor,
          calloutBg: calloutStyle.backgroundColor,
          calloutCellBg: calloutCellStyle.backgroundColor,
          calloutColor: calloutCellStyle.color,
          calloutStrongColor: style('#callout-strong').color,
          calloutCodeColor: calloutCodeStyle.color,
          calloutCodeBg: calloutCodeStyle.backgroundColor,
          calloutCodeBorderColor: calloutCodeStyle.borderTopColor,
          calloutBorderColor: calloutCellStyle.borderTopColor,
          calloutPaddingTop: calloutCellStyle.paddingTop,
        };
        """
    )
    assert metrics["lightClass"] is False, metrics
    assert metrics["previewBg"] == metrics["expectedPreview"]["background"], metrics
    assert metrics["previewColor"] == metrics["expectedPreview"]["color"], metrics
    assert metrics["normalColor"] == metrics["expectedPreview"]["color"], metrics
    assert metrics["normalBorderColor"] == metrics["expectedPreview"]["border"], metrics
    assert metrics["mdWarningClass"] is True, metrics
    assert "[!WARNING]" not in metrics["mdWarningText"], metrics
    assert metrics["mdWarningBg"] == metrics["expectedWarning"]["background"], metrics
    assert metrics["mdWarningBorderLeftWidth"] == "0px", metrics
    assert metrics["mdWarningColor"] == metrics["expectedWarning"]["color"], metrics
    assert metrics["mdWarningStrongColor"] == metrics["expectedWarning"]["color"], metrics
    assert metrics["mdWarningCodeColor"] == metrics["expectedCode"]["color"], metrics
    assert metrics["mdWarningCodeBg"] == metrics["expectedCode"]["background"], metrics
    assert metrics["mdCautionClass"] is True, metrics
    assert "[!CAUTION]" not in metrics["mdCautionText"], metrics
    assert metrics["mdCautionBg"] == metrics["expectedCaution"]["background"], metrics
    assert metrics["mdCautionBorderLeftWidth"] == "0px", metrics
    assert metrics["mdCautionColor"] == metrics["expectedCaution"]["color"], metrics
    assert metrics["mdCautionStrongColor"] == metrics["expectedCaution"]["color"], metrics
    assert metrics["mdCautionCodeColor"] == metrics["expectedCode"]["color"], metrics
    assert metrics["mdCautionCodeBg"] == metrics["expectedCode"]["background"], metrics
    assert metrics["mdCautionBlockCodeText"] == "oss scan", metrics
    assert metrics["mdCautionBreakClass"] is True, metrics
    assert "[!CAUTION]" not in metrics["mdCautionBreakText"], metrics
    assert metrics["mdCautionBreakBg"] == metrics["expectedCaution"]["background"], metrics
    assert metrics["mdCautionBreakFirstVisibleNode"] == "STRONG", metrics
    assert metrics["mdCautionListClass"] is True, metrics
    assert "[!CAUTION]" not in metrics["mdCautionListText"], metrics
    assert metrics["mdCautionListFirstVisibleNode"] == "STRONG", metrics
    assert metrics["mdCautionListTailGap"] <= 12, metrics
    assert metrics["alertSourceAnchorDisplay"] == "none", metrics
    assert metrics["edgeCodeText"] == "python -V", metrics
    assert metrics["edgeCodePaddingTop"] == "0px", metrics
    assert metrics["edgeCodeDisplay"] == "inline", metrics
    assert metrics["edgePreTailGap"] <= 8, metrics
    assert metrics["mdCautionDirectClass"] is True, metrics
    assert "[!CAUTION]" not in metrics["mdCautionDirectText"], metrics
    assert metrics["mdCautionDirectBg"] == metrics["expectedCaution"]["background"], metrics
    assert metrics["mdCautionDirectStrongColor"] == metrics["expectedCaution"]["color"], metrics
    assert metrics["mdCautionDirectFirstVisibleNode"] == "STRONG", metrics
    assert metrics["calloutBg"] == metrics["expectedWarning"]["background"], metrics
    assert metrics["calloutCellBg"] == metrics["expectedWarning"]["background"], metrics
    assert metrics["calloutColor"] == metrics["expectedWarning"]["color"], metrics
    assert metrics["calloutStrongColor"] == metrics["expectedWarning"]["color"], metrics
    assert metrics["calloutCodeColor"] == metrics["expectedCode"]["color"], metrics
    assert metrics["calloutCodeBg"] == metrics["expectedCode"]["background"], metrics
    assert metrics["calloutCodeBorderColor"] == metrics["expectedCode"]["border"], metrics
    assert metrics["calloutBorderColor"] == metrics["expectedWarning"]["border"], metrics
    assert metrics["calloutPaddingTop"] == "10px", metrics


def test_markdown_preview_code_block_background_is_grayer_only_in_dark_mode(browser, tmp_path):
    page = tmp_path / "preview-code-block-bg.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"""<!doctype html><html><head><meta charset=utf-8><style>{app_css()}</style></head>
        <body class="theme-dark editor-theme-dark">
          <div class="file-editor-content">
            <article id="preview" class="file-editor-preview-pane-panel markdown-body" style="position: static">
              <table id="callout" bgcolor="#fff8cc">
                <tr><td id="callout-cell"><strong id="callout-strong">IMPORTANT NOTE:</strong> run <code id="callout-code">gpu-info</code>.</td></tr>
              </table>
              <pre id="code-block"><code>echo hello</code></pre>
            </article>
          </div>
        </body></html>""",
    )
    metrics = browser.execute_script(
        """
        const preview = document.querySelector('#preview');
        const pre = document.querySelector('#code-block');
        const {probePaint} = window.__yolomuxTestHelpers;
        const read = () => ({
          preview: getComputedStyle(preview).backgroundColor,
          code: getComputedStyle(pre).backgroundColor,
          callout: getComputedStyle(document.querySelector('#callout')).backgroundColor,
          calloutCell: getComputedStyle(document.querySelector('#callout-cell')).backgroundColor,
          calloutText: getComputedStyle(document.querySelector('#callout-cell')).color,
          calloutStrong: getComputedStyle(document.querySelector('#callout-strong')).color,
          calloutCode: getComputedStyle(document.querySelector('#callout-code')).color,
          calloutCodeBg: getComputedStyle(document.querySelector('#callout-code')).backgroundColor,
          expectedPreview: probePaint(
            `background:var(${document.body.classList.contains('editor-theme-light') ? '--editor-preview-bg' : '--markdown-preview-bg'})`,
            preview,
          ).background,
          expectedCode: probePaint(
            `background:var(${document.body.classList.contains('editor-theme-light') ? '--lt-panel' : '--markdown-code-block-bg'})`,
            preview,
          ).background,
          expectedDarkCallout: probePaint('color:var(--markdown-html-dark-text);background:var(--markdown-html-dark-bg)', preview),
          expectedLightCallout: probePaint('color:var(--markdown-html-light-text)', preview),
          expectedInlineCode: probePaint('color:var(--code-inline);background:var(--code-inline-bg)', document.querySelector('#callout-cell')),
        });
        const dark = read();
        document.body.classList.remove('theme-dark', 'editor-theme-dark');
        document.body.classList.add('theme-light', 'editor-theme-light');
        const light = read();
        return {dark, light};
        """
    )
    assert metrics["dark"]["preview"] == metrics["dark"]["expectedPreview"], metrics
    assert metrics["dark"]["code"] == metrics["dark"]["expectedCode"], metrics
    assert metrics["dark"]["code"] != metrics["dark"]["preview"], metrics
    assert metrics["dark"]["callout"] == metrics["dark"]["expectedDarkCallout"]["background"], metrics
    assert metrics["dark"]["calloutCell"] == metrics["dark"]["expectedDarkCallout"]["background"], metrics
    assert metrics["dark"]["calloutText"] == metrics["dark"]["expectedDarkCallout"]["color"], metrics
    assert metrics["dark"]["calloutStrong"] == metrics["dark"]["expectedDarkCallout"]["color"], metrics
    assert metrics["dark"]["calloutCode"] == metrics["dark"]["expectedInlineCode"]["color"], metrics
    assert metrics["dark"]["calloutCodeBg"] == metrics["dark"]["expectedInlineCode"]["background"], metrics
    assert metrics["light"]["preview"] == metrics["light"]["expectedPreview"], metrics
    assert metrics["light"]["code"] == metrics["light"]["expectedCode"], metrics
    # This native bgcolor comes from the HTML fixture. It deliberately proves that a vanilla
    # HTML table retains its authored paint in light mode; it is not a product design token.
    assert metrics["light"]["callout"] == "rgb(255, 248, 204)", metrics
    assert metrics["light"]["calloutText"] == metrics["light"]["expectedLightCallout"]["color"], metrics
    assert metrics["light"]["calloutStrong"] == metrics["light"]["expectedLightCallout"]["color"], metrics
    assert metrics["light"]["calloutCode"] == metrics["light"]["expectedInlineCode"]["color"], metrics
    assert metrics["light"]["calloutCodeBg"] == metrics["light"]["expectedInlineCode"]["background"], metrics


def test_preview_popout_toolbar_and_state_sync(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1",
        settings={"appearance": {"preview_font_size": 16}},
        sessions=["1"],
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
            (async () => {
              try {
                const {frame} = window.__yolomuxTestHelpers;
                const waitFor = window.__yolomuxTestWaitFor;
                const waitForScrollSyncReady = (...records) => waitFor(
                  () => records.every(record => !fileEditorScrollSyncBlocked(record)
                    && (!record?.window || filePreviewPopoutScrollSyncReady(record))),
                  {timeoutMs: 500, description: 'editor scroll-sync readiness'}
                );
                const escapeHtml = value => String(value || '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
            window.marked = {
              parse(markdown) {
                const lines = String(markdown || '').split('\\n');
                const parts = [];
                for (let index = 0; index < lines.length; index += 1) {
                  const line = lines[index];
                  if (!line.trim()) continue;
                  if (line.startsWith('# ')) {
                    parts.push(`<h1>${escapeHtml(line.slice(2))}</h1>`);
                    continue;
                  }
                  if (line.startsWith('```')) {
                    const lang = escapeHtml(line.slice(3).trim() || 'text');
                    const code = [];
                    index += 1;
                    while (index < lines.length && !lines[index].startsWith('```')) {
                      code.push(lines[index]);
                      index += 1;
                    }
                    parts.push(`<pre><code class="language-${lang}">${escapeHtml(code.join('\\n'))}</code></pre>`);
                    continue;
                  }
                  parts.push(`<p>${escapeHtml(line)}</p>`);
                }
                return parts.join('');
              },
            };
            window.hljs = {
              highlightElement(block) {
                if (block.classList.contains('language-rust')) {
                  block.classList.add('hljs');
                  return;
                }
                block.classList.add('hljs');
                block.innerHTML = '<span class="hljs-keyword">function</span> <span class="hljs-title function_">paint</span>() { <span class="hljs-keyword">return</span> <span class="hljs-string">"blue"</span>; }';
              },
            };
            const path = '/home/test/repo/README.md';
            const longTail = Array.from({length: 42}, (_, index) => `Paragraph ${index + 1} pop-out scroll sync text`).join('\\n\\n');
            const original = `# Preview\\n\\nOriginal pop-out text\\n\\n\\`\\`\\`javascript\\nfunction paint() { return "blue"; }\\n\\`\\`\\`\\n\\n\\`\\`\\`rust\\npub fn main() { let value: Option<u32> = Some(1); }\\n\\`\\`\\`\\n\\n${longTail}\\n`;
            const updated = original.replace('Original pop-out text', 'Updated pop-out text');
            const item = fileEditorItemFor(path);
            setFileEditorThemeMode('yolomux-light');
            setFileState(path, {
              kind: 'text',
              gitRoot: '/home/test/repo',
              gitTracked: true,
              gitHasHistory: true,
              gitHistory: [{ref: 'HEAD'}, {ref: 'abc123def'}],
              content: original,
              original,
              dirty: false,
              language: 'markdown',
            });
            setFileEditorViewMode(path, 'preview', item);
            addFileEditorTabItem(path, item);
            const panel = createFileEditorPanel(item);
            panel.classList.add('active-pane');
            panel.style.width = '900px';
            panel.style.height = '520px';
            panelNodes.set(item, panel);
            document.getElementById('grid').append(panel);
            renderFileEditorPanel(panel, item);
            const iframe = document.createElement('iframe');
            iframe.style.width = '900px';
            iframe.style.height = '520px';
            iframe.style.position = 'absolute';
            iframe.style.left = '0';
            iframe.style.top = '0';
            document.body.append(iframe);
            const realOpen = window.open;
            let openedUrl = '';
            window.open = (url) => {
              openedUrl = String(url || '');
              return iframe.contentWindow;
            };
            panel.querySelector('.file-editor-popout-preview-panel').click();
            await frame();
            await frame();
            window.open = realOpen;
            const afterPopoutButtonClick = {
              mode: editorViewModeFor(path, item),
              openedUrl,
              hasRecord: filePreviewPopouts.has(path),
              editMode: panel.querySelector('.file-editor-content')?.classList.contains('edit-mode') === true,
              editorThemeText: panel.querySelector('.file-editor-theme-panel')?.dataset?.editorThemeShort || '',
              editorThemeClass: panel.querySelector('.file-editor-theme-panel')?.className || '',
            };
            filePreviewPopouts.delete(path);
            setFileEditorViewMode(path, 'preview', item);
            renderFileEditorPanel(panel, item);
            filePreviewPopouts.set(path, {window: iframe.contentWindow});
            writeFilePreviewPopoutDocument(path, iframe.contentWindow, renderedPreviewSnapshot(path, original));
            await frame();
            await frame();
            const popupDoc = iframe.contentDocument;
            const popupValue = () => popupDoc.querySelector('.file-editor-preview-font-value')?.textContent?.trim() || '';
            const editorValue = () => panel.querySelector('.file-editor-preview-font-value')?.textContent?.trim() || '';
            const popupThemeButton = () => popupDoc.querySelector('[data-preview-popout-theme]');
            const popupThemeText = () => popupThemeButton()?.dataset?.editorThemeShort || '';
            const popupScroller = () => popupDoc.scrollingElement || popupDoc.documentElement;
            const maxScrollTop = element => Math.max(0, Number(element?.scrollHeight || 0) - Number(element?.clientHeight || 0));
            const scrollRatio = element => {
              const max = maxScrollTop(element);
              return max > 0 ? Number(element.scrollTop || 0) / max : 0;
            };
            const visibleCenterRatio = element => {
              const size = Math.max(0, Number(element?.scrollHeight || 0));
              if (size <= 0) return 0;
              return (Number(element.scrollTop || 0) + Number(element.clientHeight || 0) / 2) / size;
            };
            const editorKeyword = () => getComputedStyle(panel.querySelector('code.language-javascript .code-keyword')).color;
            const popupKeyword = () => getComputedStyle(popupDoc.querySelector('code.language-javascript .code-keyword')).color;
            const editorRustControl = () => panel.querySelector('code.language-rust .code-control')?.textContent || '';
            const popupRustControl = () => popupDoc.querySelector('code.language-rust .code-control')?.textContent || '';
            const popupRustControlColor = () => getComputedStyle(popupDoc.querySelector('code.language-rust .code-control')).color;
            const popupRustType = () => popupDoc.querySelector('code.language-rust .code-type')?.textContent || '';
            const popupRustTypeColor = () => getComputedStyle(popupDoc.querySelector('code.language-rust .code-type')).color;
            const snapshotGeometry = () => {
              const header = popupDoc.querySelector('.file-preview-popout-title').getBoundingClientRect();
              const pathNode = popupDoc.querySelector('.file-preview-popout-title-path').getBoundingClientRect();
              const font = popupDoc.querySelector('.file-editor-preview-font-panel').getBoundingClientRect();
              const theme = popupThemeButton().getBoundingClientRect();
              return {
                headerLeft: header.left,
                headerRight: header.right,
                headerCenter: header.left + header.width / 2,
                pathLeft: pathNode.left,
                pathRight: pathNode.right,
                fontLeft: font.left,
                fontCenter: font.left + font.width / 2,
                themeRight: theme.right,
                themeLeft: theme.left,
                headerTop: header.top,
              };
            };
            const initialGeometry = snapshotGeometry();
            const initial = {
              popupValue: popupValue(),
              editorValue: editorValue(),
              popupText: popupDoc.querySelector('[data-preview-root]')?.textContent || '',
              popupBodyClass: popupDoc.body.className,
              popupThemeClass: popupThemeButton().className,
              popupThemeText: popupThemeText(),
              editorKeywordColor: editorKeyword(),
              popupKeywordColor: popupKeyword(),
              editorRustControl: editorRustControl(),
              popupRustControl: popupRustControl(),
              popupRustControlColor: popupRustControlColor(),
              popupRustType: popupRustType(),
              popupRustTypeColor: popupRustTypeColor(),
              editorCodeClass: panel.querySelector('pre code')?.className || '',
              popupCodeClass: popupDoc.querySelector('pre code')?.className || '',
            };
            setEditorPreviewFontSize(19);
            await frame();
            await frame();
            const afterEditorFont = {
              popupValue: popupValue(),
              editorValue: editorValue(),
              popupStyle: popupDoc.body.getAttribute('style') || '',
            };
            handleFileEditorContentChanged(panel, path, updated, {syntax: false});
            await waitFor(() => (popupDoc.querySelector('[data-preview-root]')?.textContent || '').includes('Updated pop-out text'));
            const afterEditorContent = popupDoc.querySelector('[data-preview-root]')?.textContent || '';
            setFileEditorThemeMode(configuredEditorSchemeForMode(false));
            await frame();
            await frame();
            const afterEditorTheme = {
              popupBodyClass: popupDoc.body.className,
              editorThemeClass: panel.querySelector('.file-editor-theme-panel')?.className || '',
              popupThemeClass: popupThemeButton().className,
              popupThemeText: popupThemeText(),
            };
            popupDoc.querySelector('[data-editor-preview-font-step="1"]').click();
            await frame();
            await frame();
            const afterPopupFont = {
              popupValue: popupValue(),
              editorValue: editorValue(),
            };
                popupThemeButton().click();
                await frame();
                await frame();
                const previewPane = panel.querySelector('.file-editor-preview-pane-panel');
                panel._previewLayoutScrollUntil = performance.now() + fileEditorPreviewLayoutScrollSyncMs;
                previewPane.scrollTop = Math.round(maxScrollTop(previewPane) * 0.38);
                previewPane.dispatchEvent(new Event('scroll', {bubbles: true}));
                await frame();
                await frame();
            const afterEditorScroll = {
              previewTop: previewPane.scrollTop,
              popupTop: popupScroller().scrollTop,
              previewRatio: scrollRatio(previewPane),
              popupRatio: scrollRatio(popupScroller()),
              previewCenterRatio: visibleCenterRatio(previewPane),
              popupCenterRatio: visibleCenterRatio(popupScroller()),
            };
                await waitForScrollSyncReady(panel, filePreviewPopouts.get(path));
            previewPane.scrollTop = 0;
            previewPane.dispatchEvent(new Event('scroll', {bubbles: true}));
            await frame();
            await frame();
            const afterEditorTopScroll = {
              previewTop: previewPane.scrollTop,
              popupTop: popupScroller().scrollTop,
            };
                await waitForScrollSyncReady(panel, filePreviewPopouts.get(path));
            previewPane.scrollTop = maxScrollTop(previewPane);
            previewPane.dispatchEvent(new Event('scroll', {bubbles: true}));
            await frame();
            const delayedPopupGrowth = popupDoc.createElement('div');
            delayedPopupGrowth.style.height = '240px';
            delayedPopupGrowth.dataset.previewDelayedGrowth = 'true';
            popupDoc.querySelector('[data-preview-root]').append(delayedPopupGrowth);
            await frame();
            await waitForScrollSyncReady(panel, filePreviewPopouts.get(path));
            const afterEditorBottomScroll = {
              previewTop: previewPane.scrollTop,
              previewMax: maxScrollTop(previewPane),
              popupTop: popupScroller().scrollTop,
              popupMax: maxScrollTop(popupScroller()),
              completedGeneration: filePreviewPopouts.get(path)?.scrollConvergenceCompletedGeneration || 0,
              stableSnapshots: filePreviewPopouts.get(path)?.scrollConvergenceStableSnapshots || 0,
            };
            const popupBefore = popupScroller().scrollTop;
            popupScroller().scrollTop = Math.round(maxScrollTop(popupScroller()) * 0.61);
            popupDoc.defaultView.dispatchEvent(new WheelEvent('wheel', {deltaY: -1, bubbles: true}));
            popupScroller().dispatchEvent(new Event('scroll', {bubbles: true}));
            popupDoc.defaultView.dispatchEvent(new Event('scroll'));
            await frame();
            await frame();
            const afterPopupScroll = {
              popupBefore,
              popupTop: popupScroller().scrollTop,
              previewTop: previewPane.scrollTop,
              popupRatio: scrollRatio(popupScroller()),
              previewRatio: scrollRatio(previewPane),
              popupCenterRatio: visibleCenterRatio(popupScroller()),
              previewCenterRatio: visibleCenterRatio(previewPane),
              headerTop: snapshotGeometry().headerTop,
              owner: filePreviewPopouts.get(path)?.scrollSyncOwner?.kind || '',
            };
                await waitForScrollSyncReady(panel, filePreviewPopouts.get(path));
            popupScroller().scrollTop = 0;
            popupScroller().dispatchEvent(new Event('scroll', {bubbles: true}));
            popupDoc.defaultView.dispatchEvent(new Event('scroll'));
            await frame();
            await frame();
            const afterPopupTopScroll = {
              popupTop: popupScroller().scrollTop,
              previewTop: previewPane.scrollTop,
            };
                await waitForScrollSyncReady(panel, filePreviewPopouts.get(path));
            popupScroller().scrollTop = maxScrollTop(popupScroller());
            popupScroller().dispatchEvent(new Event('scroll', {bubbles: true}));
            popupDoc.defaultView.dispatchEvent(new Event('scroll'));
            await frame();
            await frame();
            const afterPopupBottomScroll = {
              popupTop: popupScroller().scrollTop,
              popupMax: maxScrollTop(popupScroller()),
              previewTop: previewPane.scrollTop,
              previewMax: maxScrollTop(previewPane),
            };
            const afterPopupTheme = {
              bodyClass: document.body.className,
              popupBodyClass: popupDoc.body.className,
              editorThemeClass: panel.querySelector('.file-editor-theme-panel')?.className || '',
              popupThemeClass: popupThemeButton().className,
              editorThemeText: panel.querySelector('.file-editor-theme-panel')?.dataset?.editorThemeShort || '',
              popupThemeText: popupThemeText(),
            };
            popupThemeButton().click();
            await frame();
            await frame();
            const afterPopupThemeDark = {
              bodyClass: document.body.className,
              popupBodyClass: popupDoc.body.className,
              editorThemeClass: panel.querySelector('.file-editor-theme-panel')?.className || '',
              popupThemeClass: popupThemeButton().className,
              editorThemeText: panel.querySelector('.file-editor-theme-panel')?.dataset?.editorThemeShort || '',
              popupThemeText: popupThemeText(),
            };
            setFileEditorViewMode(path, 'edit', item);
            renderFileEditorPanel(panel, item);
            let stableEditorMax = -1;
            for (let attempt = 0; attempt < 120; attempt += 1) {
              await frame();
              const candidateScroller = panel._cmView?.scrollDOM;
              const candidateMax = maxScrollTop(candidateScroller);
              if (candidateScroller?.isConnected && candidateMax > 0 && candidateMax === stableEditorMax) break;
              stableEditorMax = candidateMax;
            }
            const editorScroller = panel._cmView?.scrollDOM;
            popupScroller().scrollTop = maxScrollTop(popupScroller());
            popupScroller().dispatchEvent(new Event('scroll', {bubbles: true}));
            popupDoc.defaultView.dispatchEvent(new WheelEvent('wheel', {deltaY: 0, bubbles: true}));
            popupDoc.defaultView.dispatchEvent(new Event('scroll'));
            await frame();
            await frame();
            await waitForScrollSyncReady(panel, filePreviewPopouts.get(path));
            const afterPopupBottomScrollEditMode = {
              mode: editorViewModeFor(path, item),
              popupTop: popupScroller().scrollTop,
              popupMax: maxScrollTop(popupScroller()),
              editorTop: editorScroller?.scrollTop || 0,
              editorMax: maxScrollTop(editorScroller),
            };
            const splitPath = '/home/test/repo/SPLIT_SCROLL.md';
            const splitTail = Array.from({length: 120}, (_, index) => `Split paragraph ${index + 1} smooth scroll text`).join('\\n\\n');
            const splitOriginal = `# Split Scroll\n\n${splitTail}\n`;
            const splitItem = fileEditorItemFor(splitPath);
            setFileState(splitPath, {
              kind: 'text',
              gitRoot: '/home/test/repo',
              gitTracked: true,
              gitHasHistory: true,
              gitHistory: [{ref: 'HEAD'}, {ref: 'abc123def'}],
              content: splitOriginal,
              original: splitOriginal,
              dirty: false,
              language: 'markdown',
            });
            setFileEditorViewMode(splitPath, 'split', splitItem);
            addFileEditorTabItem(splitPath, splitItem);
            const splitPanel = createFileEditorPanel(splitItem);
            splitPanel.classList.add('active-pane');
            splitPanel.style.width = '900px';
            splitPanel.style.height = '520px';
            panelNodes.set(splitItem, splitPanel);
            document.getElementById('grid').append(splitPanel);
            renderFileEditorPanel(splitPanel, splitItem);
            for (let attempt = 0; attempt < 120; attempt += 1) {
              const candidateScroller = splitPanel._cmView?.scrollDOM;
              const candidatePreview = splitPanel.querySelector('.file-editor-preview-pane-panel');
              if (candidateScroller?.isConnected && maxScrollTop(candidateScroller) > 0 && candidatePreview && !candidatePreview.hidden && maxScrollTop(candidatePreview) > 0) break;
              await frame();
            }
            await frame();
            await frame();
            const splitEditorScroller = splitPanel._cmView?.scrollDOM;
            const splitPreviewPane = splitPanel.querySelector('.file-editor-preview-pane-panel');
            splitEditorScroller.scrollTop = Math.round(maxScrollTop(splitEditorScroller) * 0.37);
            splitEditorScroller.dispatchEvent(new Event('scroll', {bubbles: true}));
            await frame();
            await frame();
            const afterSplitEditorScroll = {
              editorTop: splitEditorScroller.scrollTop,
              previewTop: splitPreviewPane.scrollTop,
              editorCenterRatio: visibleCenterRatio(splitEditorScroller),
              previewCenterRatio: visibleCenterRatio(splitPreviewPane),
            };
                await waitForScrollSyncReady(splitPanel);
            splitEditorScroller.scrollTop = 0;
            splitEditorScroller.dispatchEvent(new Event('scroll', {bubbles: true}));
            await frame();
            await frame();
            const afterSplitEditorTopScroll = {
              editorTop: splitEditorScroller.scrollTop,
              previewTop: splitPreviewPane.scrollTop,
            };
                await waitForScrollSyncReady(splitPanel);
            splitEditorScroller.scrollTop = maxScrollTop(splitEditorScroller);
            splitEditorScroller.dispatchEvent(new Event('scroll', {bubbles: true}));
            await frame();
            await frame();
            const afterSplitEditorBottomScroll = {
              editorTop: splitEditorScroller.scrollTop,
              editorMax: maxScrollTop(splitEditorScroller),
              previewTop: splitPreviewPane.scrollTop,
              previewMax: maxScrollTop(splitPreviewPane),
            };
                await waitForScrollSyncReady(splitPanel);
            splitPreviewPane.scrollTop = Math.round(maxScrollTop(splitPreviewPane) * 0.63);
            splitPreviewPane.dispatchEvent(new Event('scroll', {bubbles: true}));
            await frame();
            await frame();
            const afterSplitPreviewScroll = {
              previewTop: splitPreviewPane.scrollTop,
              editorTop: splitEditorScroller.scrollTop,
              previewCenterRatio: visibleCenterRatio(splitPreviewPane),
              editorCenterRatio: visibleCenterRatio(splitEditorScroller),
            };
                await waitForScrollSyncReady(splitPanel);
            splitPreviewPane.scrollTop = 0;
            splitPreviewPane.dispatchEvent(new Event('scroll', {bubbles: true}));
            await frame();
            await frame();
            const afterSplitPreviewTopScroll = {
              previewTop: splitPreviewPane.scrollTop,
              editorTop: splitEditorScroller.scrollTop,
            };
                await waitForScrollSyncReady(splitPanel);
            splitPreviewPane.scrollTop = maxScrollTop(splitPreviewPane);
            splitPreviewPane.dispatchEvent(new Event('scroll', {bubbles: true}));
            const finalSplitOwnerBeforeWait = {
              pending: Boolean(splitPanel._splitScrollFrame),
              previewTop: splitPreviewPane.scrollTop,
              previewMax: maxScrollTop(splitPreviewPane),
              editorTop: splitEditorScroller.scrollTop,
              editorMax: maxScrollTop(splitEditorScroller),
            };
            await waitFor(
              () => !splitPanel._splitScrollFrame
                && !fileEditorScrollSyncBlocked(splitPanel)
                && splitPanel._splitScrollBottomConvergence == null,
              {timeoutMs: 500, description: 'final measured split-preview scroll-sync owner'},
            );
            const splitBottomSnapshots = [];
            for (let snapshot = 0; snapshot < 2; snapshot += 1) {
              await frame();
              splitBottomSnapshots.push({
                previewTop: splitPreviewPane.scrollTop,
                previewMax: maxScrollTop(splitPreviewPane),
                editorTop: splitEditorScroller.scrollTop,
                editorMax: maxScrollTop(splitEditorScroller),
              });
            }
            if (splitBottomSnapshots.some(snapshot => snapshot.previewTop !== snapshot.previewMax || snapshot.editorTop !== snapshot.editorMax)
                || JSON.stringify(splitBottomSnapshots[0]) !== JSON.stringify(splitBottomSnapshots[1])) {
              throw new Error(`split preview bottom did not remain exact across measured frames: ${JSON.stringify(splitBottomSnapshots)}`);
            }
            const afterSplitPreviewBottomScroll = {
              previewTop: splitPreviewPane.scrollTop,
              previewMax: maxScrollTop(splitPreviewPane),
              editorTop: splitEditorScroller.scrollTop,
              editorMax: maxScrollTop(splitEditorScroller),
              stableSnapshots: splitBottomSnapshots.length,
            };
            let splitCloseCount = 0;
            const splitWindow = {closed: false, close() { splitCloseCount += 1; this.closed = true; }};
            filePreviewPopouts.set(path, {window: splitWindow});
            setFileEditorViewMode(path, 'split', item);
            const afterSplitModeClose = {
              mode: editorViewModeFor(path, item),
              closed: splitWindow.closed,
              closeCount: splitCloseCount,
              hasRecord: filePreviewPopouts.has(path),
            };
            let previewCloseCount = 0;
            const previewWindow = {closed: false, close() { previewCloseCount += 1; this.closed = true; }};
            filePreviewPopouts.set(path, {window: previewWindow});
            setFileEditorViewMode(path, 'preview', item);
            const afterPreviewModeClose = {
              mode: editorViewModeFor(path, item),
              closed: previewWindow.closed,
              closeCount: previewCloseCount,
              hasRecord: filePreviewPopouts.has(path),
            };
            done({
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
              initialGeometry,
              afterPopoutButtonClick,
              initial,
              afterEditorFont,
              afterEditorContent,
              afterEditorTheme,
              afterPopupFont,
              afterEditorScroll,
              afterEditorTopScroll,
              afterEditorBottomScroll,
              afterPopupScroll,
              afterPopupTopScroll,
              afterPopupBottomScroll,
              afterPopupTheme,
              afterPopupThemeDark,
              afterPopupBottomScrollEditMode,
              afterSplitEditorScroll,
              afterSplitEditorTopScroll,
              afterSplitEditorBottomScroll,
              afterSplitPreviewScroll,
              afterSplitPreviewTopScroll,
              finalSplitOwnerBeforeWait,
              afterSplitPreviewBottomScroll,
              afterSplitModeClose,
              afterPreviewModeClose,
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          }
        })();
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    geometry = metrics["initialGeometry"]
    assert geometry["pathLeft"] <= geometry["headerLeft"] + 2, metrics
    assert geometry["pathRight"] <= geometry["fontLeft"] - 8, metrics
    assert abs(geometry["fontCenter"] - geometry["headerCenter"]) <= 3, metrics
    assert geometry["themeRight"] >= geometry["headerRight"] - 2, metrics
    assert geometry["themeLeft"] > geometry["fontCenter"], metrics
    assert abs(geometry["headerTop"]) <= 1, metrics
    assert metrics["afterPopoutButtonClick"]["mode"] == "edit", metrics
    assert metrics["afterPopoutButtonClick"]["editMode"] is True, metrics
    assert metrics["afterPopoutButtonClick"]["hasRecord"] is True, metrics
    assert metrics["afterPopoutButtonClick"]["openedUrl"].startswith("/preview-popout?path="), metrics
    assert metrics["afterPopoutButtonClick"]["editorThemeText"] == "Bright", metrics
    assert "theme-with-label" in metrics["afterPopoutButtonClick"]["editorThemeClass"], metrics
    assert metrics["initial"]["popupValue"] == "16", metrics
    assert metrics["initial"]["editorValue"] == "16", metrics
    assert "Original pop-out text" in metrics["initial"]["popupText"], metrics
    assert metrics["initial"]["popupThemeText"] == "Bright", metrics
    assert "hljs" in metrics["initial"]["popupCodeClass"], metrics
    assert metrics["initial"]["editorKeywordColor"] == metrics["initial"]["popupKeywordColor"], metrics
    # These are the fixed colors emitted by the bundled syntax highlighter, not CSS tokens.
    # Pin them because this test is specifically the editor/popout syntax-renderer parity check.
    assert metrics["initial"]["editorKeywordColor"] == "rgb(0, 0, 255)", metrics
    assert metrics["initial"]["editorRustControl"] == "pub", metrics
    assert metrics["initial"]["popupRustControl"] == "pub", metrics
    assert metrics["initial"]["popupRustControlColor"] == "rgb(175, 0, 219)", metrics
    assert metrics["initial"]["popupRustType"] == "Option", metrics
    assert metrics["initial"]["popupRustTypeColor"] == "rgb(0, 128, 128)", metrics
    assert metrics["afterEditorFont"]["popupValue"] == "19", metrics
    assert metrics["afterEditorFont"]["editorValue"] == "19", metrics
    assert "--editor-preview-font-size: 19px" in metrics["afterEditorFont"]["popupStyle"], metrics
    assert "Updated pop-out text" in metrics["afterEditorContent"], metrics
    assert "editor-theme-light" in metrics["afterEditorTheme"]["popupBodyClass"], metrics
    assert "theme-light" in metrics["afterEditorTheme"]["popupThemeClass"], metrics
    assert metrics["afterEditorTheme"]["popupThemeText"] == "Bright", metrics
    assert metrics["afterPopupFont"]["popupValue"] == "20", metrics
    assert metrics["afterPopupFont"]["editorValue"] == "20", metrics
    assert metrics["afterEditorScroll"]["previewTop"] > 0, metrics
    assert metrics["afterEditorScroll"]["popupTop"] > 0, metrics
    assert abs(metrics["afterEditorScroll"]["previewCenterRatio"] - metrics["afterEditorScroll"]["popupCenterRatio"]) <= 0.05, metrics
    assert metrics["afterEditorTopScroll"]["previewTop"] == 0, metrics
    assert metrics["afterEditorTopScroll"]["popupTop"] == 0, metrics
    # Bottom-scroll settle tolerance is 3px, not 1px: under heavy machine load the smooth-scroll
    # settles 1-2px short of scrollTopMax (recurring flake, 4 observed). Direction/sync checks stay exact.
    assert abs(metrics["afterEditorBottomScroll"]["previewTop"] - metrics["afterEditorBottomScroll"]["previewMax"]) <= 3, metrics
    assert abs(metrics["afterEditorBottomScroll"]["popupTop"] - metrics["afterEditorBottomScroll"]["popupMax"]) <= 3, metrics
    assert metrics["afterEditorBottomScroll"]["completedGeneration"] > 0, metrics
    assert metrics["afterEditorBottomScroll"]["stableSnapshots"] >= 2, metrics
    assert metrics["afterPopupScroll"]["popupTop"] < metrics["afterPopupScroll"]["popupBefore"], metrics
    assert metrics["afterPopupScroll"]["previewTop"] < metrics["afterEditorBottomScroll"]["previewTop"], metrics
    assert abs(metrics["afterPopupScroll"]["popupCenterRatio"] - metrics["afterPopupScroll"]["previewCenterRatio"]) <= 0.05, metrics
    assert abs(metrics["afterPopupScroll"]["headerTop"]) <= 1, metrics
    assert metrics["afterPopupScroll"]["owner"] == "popout", metrics
    assert metrics["afterPopupTopScroll"]["popupTop"] == 0, metrics
    assert metrics["afterPopupTopScroll"]["previewTop"] == 0, metrics
    assert abs(metrics["afterPopupBottomScroll"]["popupTop"] - metrics["afterPopupBottomScroll"]["popupMax"]) <= 3, metrics
    assert abs(metrics["afterPopupBottomScroll"]["previewTop"] - metrics["afterPopupBottomScroll"]["previewMax"]) <= 3, metrics
    assert metrics["afterPopupBottomScrollEditMode"]["mode"] == "edit", metrics
    assert abs(metrics["afterPopupBottomScrollEditMode"]["popupTop"] - metrics["afterPopupBottomScrollEditMode"]["popupMax"]) <= 3, metrics
    assert abs(metrics["afterPopupBottomScrollEditMode"]["editorTop"] - metrics["afterPopupBottomScrollEditMode"]["editorMax"]) <= 3, metrics
    assert metrics["afterSplitEditorScroll"]["editorTop"] > 0, metrics
    assert metrics["afterSplitEditorScroll"]["previewTop"] > 0, metrics
    assert abs(metrics["afterSplitEditorScroll"]["editorCenterRatio"] - metrics["afterSplitEditorScroll"]["previewCenterRatio"]) <= 0.05, metrics
    assert metrics["afterSplitEditorTopScroll"]["editorTop"] == 0, metrics
    assert metrics["afterSplitEditorTopScroll"]["previewTop"] == 0, metrics
    assert abs(metrics["afterSplitEditorBottomScroll"]["editorTop"] - metrics["afterSplitEditorBottomScroll"]["editorMax"]) <= 3, metrics
    assert abs(metrics["afterSplitEditorBottomScroll"]["previewTop"] - metrics["afterSplitEditorBottomScroll"]["previewMax"]) <= 3, metrics
    assert metrics["afterSplitPreviewScroll"]["previewTop"] < metrics["afterSplitEditorBottomScroll"]["previewTop"], metrics
    assert metrics["afterSplitPreviewScroll"]["editorTop"] < metrics["afterSplitEditorBottomScroll"]["editorTop"], metrics
    assert abs(metrics["afterSplitPreviewScroll"]["previewCenterRatio"] - metrics["afterSplitPreviewScroll"]["editorCenterRatio"]) <= 0.05, metrics
    assert metrics["afterSplitPreviewTopScroll"]["previewTop"] == 0, metrics
    assert metrics["afterSplitPreviewTopScroll"]["editorTop"] == 0, metrics
    assert metrics["finalSplitOwnerBeforeWait"]["pending"] is True, metrics
    assert metrics["finalSplitOwnerBeforeWait"]["editorTop"] < metrics["finalSplitOwnerBeforeWait"]["editorMax"], metrics
    assert abs(metrics["afterSplitPreviewBottomScroll"]["previewTop"] - metrics["afterSplitPreviewBottomScroll"]["previewMax"]) <= 1, metrics
    assert abs(metrics["afterSplitPreviewBottomScroll"]["editorTop"] - metrics["afterSplitPreviewBottomScroll"]["editorMax"]) <= 1, metrics
    assert metrics["afterSplitPreviewBottomScroll"]["stableSnapshots"] >= 2, metrics
    assert "editor-preview-vanilla" in metrics["afterPopupTheme"]["popupBodyClass"], metrics
    assert "editor-preview-vanilla" in metrics["afterPopupTheme"]["bodyClass"], metrics
    assert "theme-vanilla" in metrics["afterPopupTheme"]["editorThemeClass"], metrics
    assert "theme-vanilla" in metrics["afterPopupTheme"]["popupThemeClass"], metrics
    assert metrics["afterPopupTheme"]["editorThemeText"] == "Vanilla", metrics
    assert metrics["afterPopupTheme"]["popupThemeText"] == "Vanilla", metrics
    assert "editor-theme-dark" in metrics["afterPopupThemeDark"]["popupBodyClass"], metrics
    assert "editor-theme-dark" in metrics["afterPopupThemeDark"]["bodyClass"], metrics
    assert "theme-dark" in metrics["afterPopupThemeDark"]["editorThemeClass"], metrics
    assert "theme-dark" in metrics["afterPopupThemeDark"]["popupThemeClass"], metrics
    assert metrics["afterPopupThemeDark"]["editorThemeText"] == "Dark", metrics
    assert metrics["afterPopupThemeDark"]["popupThemeText"] == "Dark", metrics
    assert metrics["afterSplitModeClose"] == {"mode": "split", "closed": True, "closeCount": 1, "hasRecord": False}, metrics
    assert metrics["afterPreviewModeClose"] == {"mode": "preview", "closed": True, "closeCount": 1, "hasRecord": False}, metrics


def test_preview_popout_delayed_navigation_write_is_generation_bound(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=1", sessions=["1"])
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const path = '/home/test/repo/DELAYED.md';
          const writes = [];
          const loadCallbacks = [];
          const timeoutCallbacks = [];
          const asyncControls = new Map();
          const originalRendered = renderedPreviewSnapshot;
          const originalRenderedAsync = renderedPreviewSnapshotAsync;
          const originalWrite = writeFilePreviewPopoutDocument;
          try {
            renderedPreviewSnapshot = (_path, text) => ({className: '', dataAttributes: {}, html: `sync:${text}`});
            renderedPreviewSnapshotAsync = (_path, text) => new Promise((resolve, reject) => asyncControls.set(text, {resolve, reject}));
            writeFilePreviewPopoutDocument = (_path, _previewWindow, snapshot) => {
              writes.push(snapshot.html);
              return true;
            };
            const previewWindow = {
              closed: false,
              location: {pathname: '/pending-preview'},
              document: {readyState: 'loading'},
              addEventListener(type, callback) {
                if (type === 'load') loadCallbacks.push(callback);
              },
              setTimeout(callback) {
                timeoutCallbacks.push(callback);
                return timeoutCallbacks.length;
              },
              focus() {},
            };
            filePreviewPopouts.set(path, {window: previewWindow});
            writeFilePreviewPopoutWhenReady(path, previewWindow, 'old');
            writeFilePreviewPopoutWhenReady(path, previewWindow, 'new');

            loadCallbacks[0]();
            const staleWritesAfterOldLoad = [...writes];
            asyncControls.get('old').resolve({className: '', dataAttributes: {}, html: 'async:old'});
            await Promise.resolve();
            await Promise.resolve();
            const callbacksAfterOldAsync = loadCallbacks.length;

            asyncControls.get('new').resolve({className: '', dataAttributes: {}, html: 'async:new'});
            await Promise.resolve();
            await Promise.resolve();
            const record = filePreviewPopouts.get(path);
            const readyBeforeWinningLoad = filePreviewPopoutScrollSyncReady(record);
            const winningCompletion = record.previewAsync.promise;
            const winningLoadIndex = loadCallbacks.length - 1;
            loadCallbacks[winningLoadIndex]();
            await winningCompletion;
            await Promise.resolve();
            const writesAfterWinningLoad = [...writes];
            const readyAfterWinningLoad = filePreviewPopoutScrollSyncReady(record);

            writeFilePreviewPopoutWhenReady(path, previewWindow, 'reject');
            const rejectedCompletion = record.previewAsync.promise;
            asyncControls.get('reject').reject(new Error('expected async render rejection'));
            await rejectedCompletion;
            await Promise.resolve();
            const readyAfterRejectBeforeInitialLoad = filePreviewPopoutScrollSyncReady(record);
            loadCallbacks[loadCallbacks.length - 1]();
            await Promise.resolve();
            done({
              staleWritesAfterOldLoad,
              callbacksAfterOldAsync,
              readyBeforeWinningLoad,
              writesAfterWinningLoad,
              readyAfterWinningLoad,
              readyAfterRejectBeforeInitialLoad,
              writesAfterRejectedInitialLoad: [...writes],
              readyAfterRejectedInitialLoad: filePreviewPopoutScrollSyncReady(record),
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', writes: [...writes]});
          } finally {
            renderedPreviewSnapshot = originalRendered;
            renderedPreviewSnapshotAsync = originalRenderedAsync;
            writeFilePreviewPopoutDocument = originalWrite;
            filePreviewPopouts.delete(path);
          }
        })();
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["staleWritesAfterOldLoad"] == [], metrics
    assert metrics["callbacksAfterOldAsync"] == 2, metrics
    assert metrics["readyBeforeWinningLoad"] is False, metrics
    assert metrics["writesAfterWinningLoad"] == ["async:new"], metrics
    assert metrics["readyAfterWinningLoad"] is True, metrics
    assert metrics["readyAfterRejectBeforeInitialLoad"] is False, metrics
    assert metrics["writesAfterRejectedInitialLoad"] == ["async:new", "sync:reject"], metrics
    assert metrics["readyAfterRejectedInitialLoad"] is True, metrics


def test_light_editor_and_preview_share_python_fence_token_colors(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1",
        settings={"appearance": {"preview_font_size": 16}},
        sessions=["1"],
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const {frame} = window.__yolomuxTestHelpers;
            const escapeHtml = value => String(value || '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
            window.marked = {
              parse(markdown) {
                const lines = String(markdown || '').split('\\n');
                const parts = [];
                for (let index = 0; index < lines.length; index += 1) {
                  const line = lines[index];
                  if (line.startsWith('```')) {
                    const lang = escapeHtml(line.slice(3).trim() || 'text');
                    const code = [];
                    index += 1;
                    while (index < lines.length && !lines[index].startsWith('```')) {
                      code.push(lines[index]);
                      index += 1;
                    }
                    parts.push(`<pre><code class="language-${lang}">${escapeHtml(code.join('\\n'))}</code></pre>`);
                  }
                }
                return parts.join('');
              },
            };
            window.hljs = {
              highlightElement(block) {
                block.classList.add('hljs');
                block.innerHTML = escapeHtml(block.textContent || '')
                  .replace(/\\bclass\\b/g, '<span class="hljs-keyword">class</span>')
                  .replace(/\\bdef\\b/g, '<span class="hljs-keyword">def</span>')
                  .replace(/\\breturn\\b/g, '<span class="hljs-keyword">return</span>')
                  .replace(/\\bextract_tool_calls\\b/g, '<span class="hljs-title function_">extract_tool_calls</span>');
              },
            };
            setFileEditorThemeMode('yolomux-light');
            const path = '/home/test/repo/README.md';
            const content = [
              '```python',
              'class ToolParser:',
              '    def extract_tool_calls(self, model_output: str) -> DeltaMessage | None:',
              '        return None',
              '    def extract_tool_calls_streaming(',
              '        self,',
              '        previous_text: str, current_text: str, delta_text: str,',
              '        previous_token_ids, current_token_ids, delta_token_ids,',
              '        request,',
              '    ) -> DeltaMessage | None:',
              '        return None',
              '',
              'class DeltaFunctionCall(BaseModel):',
              '    name: str | None = None',
              '    arguments: str | None = None',
              '',
              'class DeltaToolCall(OpenAIBaseModel):',
              '    id: str | None = None',
              '    type: Literal["function"] | None = None',
              '    index: int',
              '    function: DeltaFunctionCall | None = None',
              '',
              'class DeltaMessage(OpenAIBaseModel):',
              '    role: str | None = None',
              '    content: str | None = None',
              '    reasoning: str | None = None',
              '    tool_calls: list[DeltaToolCall] = []',
              '```',
              '',
            ].join('\\n');
            const item = fileEditorItemFor(path);
            setFileState(path, {
              kind: 'text',
              gitRoot: '/home/test/repo',
              gitTracked: true,
              gitHasHistory: true,
              gitHistory: [{ref: 'HEAD'}, {ref: 'abc123def'}],
              content,
              original: content,
              dirty: false,
              language: 'markdown',
            });
            setFileEditorViewMode(path, 'split', item);
            addFileEditorTabItem(path, item);
            const panel = createFileEditorPanel(item);
            panel.classList.add('active-pane');
            panel.style.width = '1160px';
            panel.style.height = '520px';
            panelNodes.set(item, panel);
            document.getElementById('grid').append(panel);
            renderFileEditorPanel(panel, item);
            for (let attempt = 0; attempt < 160; attempt += 1) {
              if (panel._cmView && panel.querySelector('code.language-python .code-type')) break;
              await frame();
            }
            const rootStyle = getComputedStyle(document.documentElement);
            const visibleSpans = selector => Array.from(panel.querySelectorAll(selector))
              .filter(node => node.textContent && node.getClientRects().length);
            const editorByClassText = (className, text, occurrence = 0) => {
              const nodes = visibleSpans(`.cm-content .${className}`).filter(candidate => candidate.textContent === text);
              const node = nodes[occurrence] || nodes[0] || null;
              if (!node) return {text: '', color: '', leafColor: ''};
              const descendants = Array.from(node.querySelectorAll('span')).filter(candidate => candidate.textContent === text);
              const leaf = descendants[descendants.length - 1] || node;
              return {text: node.textContent, color: getComputedStyle(node).color, leafColor: getComputedStyle(leaf).color};
            };
            const previewByClassText = (className, text, occurrence = 0) => {
              const nodes = Array.from(panel.querySelectorAll(`code.language-python .${className}`)).filter(candidate => candidate.textContent === text);
              const node = nodes[occurrence] || nodes[0] || null;
              return node ? {text: node.textContent, color: getComputedStyle(node).color} : {text: '', color: ''};
            };
            done({
              errors: jsDebugFailureEvents('error'),
              rejections: jsDebugFailureEvents('rejection'),
              editor: {
                keyword: editorByClassText('code-keyword', 'class'),
                type: editorByClassText('code-type', 'ToolParser'),
                functionName: editorByClassText('code-function', 'extract_tool_calls'),
                streamingFunctionName: editorByClassText('code-function', 'extract_tool_calls_streaming'),
                property: editorByClassText('code-property', 'model_output'),
                request: editorByClassText('code-variable', 'request'),
                previousText: editorByClassText('code-property', 'previous_text'),
                currentText: editorByClassText('code-property', 'current_text'),
                deltaText: editorByClassText('code-property', 'delta_text'),
                previousTokenIds: editorByClassText('code-variable', 'previous_token_ids'),
                currentTokenIds: editorByClassText('code-variable', 'current_token_ids'),
                deltaTokenIds: editorByClassText('code-variable', 'delta_token_ids'),
                functionField: editorByClassText('code-property', 'function'),
                annotation: editorByClassText('code-type', 'str'),
                deltaMessage: editorByClassText('code-type', 'DeltaMessage'),
                deltaFunctionCall: editorByClassText('code-type', 'DeltaFunctionCall'),
                literalType: editorByClassText('code-type', 'Literal'),
                atom: editorByClassText('code-constant', 'None'),
              },
              preview: {
                keyword: previewByClassText('code-keyword', 'class'),
                type: previewByClassText('code-type', 'ToolParser'),
                functionName: previewByClassText('code-function', 'extract_tool_calls'),
                streamingFunctionName: previewByClassText('code-function', 'extract_tool_calls_streaming'),
                property: previewByClassText('code-property', 'model_output'),
                request: previewByClassText('code-variable', 'request'),
                previousText: previewByClassText('code-property', 'previous_text'),
                currentText: previewByClassText('code-property', 'current_text'),
                deltaText: previewByClassText('code-property', 'delta_text'),
                previousTokenIds: previewByClassText('code-variable', 'previous_token_ids'),
                currentTokenIds: previewByClassText('code-variable', 'current_token_ids'),
                deltaTokenIds: previewByClassText('code-variable', 'delta_token_ids'),
                functionField: previewByClassText('code-property', 'function'),
                annotation: previewByClassText('code-type', 'str'),
                deltaMessage: previewByClassText('code-type', 'DeltaMessage'),
                deltaFunctionCall: previewByClassText('code-type', 'DeltaFunctionCall'),
                literalType: previewByClassText('code-type', 'Literal'),
                atom: previewByClassText('code-constant', 'None'),
              },
              vars: {
                keyword: rootStyle.getPropertyValue('--code-keyword').trim(),
                type: rootStyle.getPropertyValue('--code-type').trim(),
                functionName: rootStyle.getPropertyValue('--code-function').trim(),
                property: rootStyle.getPropertyValue('--code-property').trim(),
                atom: rootStyle.getPropertyValue('--code-atom').trim(),
              },
              editorHtml: panel.querySelector('.cm-content')?.innerHTML || '',
              previewHtml: panel.querySelector('code.language-python')?.innerHTML || '',
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: jsDebugFailureEvents('error'), rejections: jsDebugFailureEvents('rejection')});
          }
        })();
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["editor"]["keyword"]["color"] == metrics["preview"]["keyword"]["color"], metrics
    assert metrics["editor"]["type"]["color"] == metrics["preview"]["type"]["color"], metrics
    assert metrics["editor"]["functionName"]["color"] == metrics["preview"]["functionName"]["color"], metrics
    assert metrics["editor"]["streamingFunctionName"]["color"] == metrics["preview"]["streamingFunctionName"]["color"], metrics
    assert metrics["editor"]["property"]["color"] == metrics["preview"]["property"]["color"], metrics
    assert metrics["editor"]["request"]["color"] == metrics["preview"]["request"]["color"], metrics
    assert metrics["editor"]["previousText"]["color"] == metrics["preview"]["previousText"]["color"], metrics
    assert metrics["editor"]["currentText"]["color"] == metrics["preview"]["currentText"]["color"], metrics
    assert metrics["editor"]["deltaText"]["color"] == metrics["preview"]["deltaText"]["color"], metrics
    assert metrics["editor"]["previousTokenIds"]["color"] == metrics["preview"]["previousTokenIds"]["color"], metrics
    assert metrics["editor"]["currentTokenIds"]["color"] == metrics["preview"]["currentTokenIds"]["color"], metrics
    assert metrics["editor"]["deltaTokenIds"]["color"] == metrics["preview"]["deltaTokenIds"]["color"], metrics
    assert metrics["editor"]["functionField"]["color"] == metrics["preview"]["functionField"]["color"], metrics
    assert metrics["editor"]["atom"]["color"] == metrics["preview"]["atom"]["color"], metrics
    assert metrics["editor"]["annotation"]["color"] == metrics["preview"]["type"]["color"], metrics
    assert metrics["editor"]["deltaMessage"]["color"] == metrics["preview"]["deltaMessage"]["color"], metrics
    assert metrics["editor"]["deltaFunctionCall"]["color"] == metrics["preview"]["deltaFunctionCall"]["color"], metrics
    assert metrics["editor"]["literalType"]["color"] == metrics["preview"]["literalType"]["color"], metrics
    assert metrics["editor"]["keyword"]["leafColor"] == metrics["preview"]["keyword"]["color"], metrics
    assert metrics["editor"]["type"]["leafColor"] == metrics["preview"]["type"]["color"], metrics
    assert metrics["editor"]["functionName"]["leafColor"] == metrics["preview"]["functionName"]["color"], metrics
    assert metrics["editor"]["streamingFunctionName"]["leafColor"] == metrics["preview"]["streamingFunctionName"]["color"], metrics
    assert metrics["editor"]["property"]["leafColor"] == metrics["preview"]["property"]["color"], metrics
    assert metrics["editor"]["request"]["leafColor"] == metrics["preview"]["request"]["color"], metrics
    assert metrics["editor"]["previousText"]["leafColor"] == metrics["preview"]["previousText"]["color"], metrics
    assert metrics["editor"]["currentText"]["leafColor"] == metrics["preview"]["currentText"]["color"], metrics
    assert metrics["editor"]["deltaText"]["leafColor"] == metrics["preview"]["deltaText"]["color"], metrics
    assert metrics["editor"]["previousTokenIds"]["leafColor"] == metrics["preview"]["previousTokenIds"]["color"], metrics
    assert metrics["editor"]["currentTokenIds"]["leafColor"] == metrics["preview"]["currentTokenIds"]["color"], metrics
    assert metrics["editor"]["deltaTokenIds"]["leafColor"] == metrics["preview"]["deltaTokenIds"]["color"], metrics
    assert metrics["editor"]["functionField"]["leafColor"] == metrics["preview"]["functionField"]["color"], metrics
    assert metrics["editor"]["atom"]["leafColor"] == metrics["preview"]["atom"]["color"], metrics
    assert metrics["editor"]["annotation"]["leafColor"] == metrics["preview"]["type"]["color"], metrics
    assert metrics["editor"]["deltaMessage"]["leafColor"] == metrics["preview"]["deltaMessage"]["color"], metrics
    assert metrics["editor"]["deltaFunctionCall"]["leafColor"] == metrics["preview"]["deltaFunctionCall"]["color"], metrics
    assert metrics["editor"]["literalType"]["leafColor"] == metrics["preview"]["literalType"]["color"], metrics
    assert metrics["editor"]["keyword"]["text"] == "class", metrics
    assert metrics["editor"]["type"]["text"] == "ToolParser", metrics
    assert metrics["editor"]["functionName"]["text"] == "extract_tool_calls", metrics
    assert metrics["editor"]["streamingFunctionName"]["text"] == "extract_tool_calls_streaming", metrics
    assert metrics["editor"]["property"]["text"] == "model_output", metrics
    assert metrics["editor"]["request"]["text"] == "request", metrics
    assert metrics["editor"]["previousTokenIds"]["text"] == "previous_token_ids", metrics
    assert metrics["editor"]["atom"]["text"] == "None", metrics
    assert metrics["preview"]["keyword"]["text"] == "class", metrics
    assert metrics["preview"]["type"]["text"] == "ToolParser", metrics
    assert metrics["preview"]["functionName"]["text"] == "extract_tool_calls", metrics
    assert metrics["preview"]["streamingFunctionName"]["text"] == "extract_tool_calls_streaming", metrics
    assert metrics["preview"]["property"]["text"] == "model_output", metrics
    assert metrics["preview"]["request"]["text"] == "request", metrics
    assert metrics["preview"]["previousTokenIds"]["text"] == "previous_token_ids", metrics
    assert metrics["preview"]["atom"]["text"] == "None", metrics


def test_editor_preview_vanilla_mode_uses_neutral_email_friendly_styles(browser, tmp_path):
    css = app_css()
    strings = dict(app_english_strings())
    bootstrap = json.dumps(
        {
            "sessions": [],
            "availableAgents": [],
            "accessRole": "admin",
            "homePath": "/home/test",
            "repoRoot": str(REPO_ROOT),
            "maxSessionTabs": 99,
            "serverHostname": "test-host",
            "strings": {"en": strings},
        },
        separators=(",", ":"),
    )
    page = tmp_path / "preview-vanilla.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"""<!doctype html><html><head><meta charset=utf-8><style>{css}</style>
        <style>
        body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; background: #ffffff; }}
        #mount {{ width: 760px; height: 420px; }}
        .file-editor-panel {{ width: 760px; height: 420px; }}
        </style></head>
        <body class="theme-light theme-resolved-light editor-theme-light">
          <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
          <div id="mount"></div>
          <script>{app_bundle_before_boot_script()}</script>
          <script>
            window.marked = {{
              parse() {{
                return '<h1>Heading</h1><p><strong>Bold</strong> and <a href="https://example.com">link</a></p><pre><code>const x = 1;</code></pre>';
              }}
            }};
            window.hljs = {{
              highlightElement(block) {{
                block.innerHTML = '<span class="hljs-keyword" style="color: rgb(255, 0, 0)">const</span> x = 1;';
              }}
            }};
            window.__previewVanillaReady = (() => {{
              const path = '/home/test/repo/README.md';
              const content = '# Heading\\n\\n**Bold** and [link](https://example.com)\\n\\n```js\\nconst x = 1;\\n```\\n';
              const item = fileEditorItemFor(path);
              setFileEditorThemeMode('yolomux-light');
              setFileState(path, {{
                kind: 'text',
                content,
                original: content,
                dirty: false,
                language: 'markdown',
              }});
              setFileEditorViewMode(path, 'preview', item);
              addFileEditorTabItem(path, item);
              const panel = createFileEditorPanel(item);
              panel.classList.add('active-pane');
              document.getElementById('mount').append(panel);
              renderFileEditorPanel(panel, item);
              const {{probePaint}} = window.__yolomuxTestHelpers;
              const read = () => {{
                const preview = panel.querySelector('.file-editor-preview-pane-panel');
                const heading = getComputedStyle(preview.querySelector('h1'));
                const link = getComputedStyle(preview.querySelector('a'));
                const codeSpan = preview.querySelector('pre code span');
                return {{
                  previewBg: getComputedStyle(preview).backgroundColor,
                  previewColor: getComputedStyle(preview).color,
                  headingColor: heading.color,
                  headingBg: heading.backgroundColor,
                  linkColor: link.color,
                  codeSpanColor: codeSpan ? getComputedStyle(codeSpan).color : '',
                  expectedVanilla: {{
                    preview: probePaint('color:var(--markdown-html-light-text);background:var(--paint-white)', preview),
                    link: probePaint('color:var(--vanilla-preview-link)', preview).color,
                  }},
                  vanillaClass: preview.classList.contains('vanilla-preview-body'),
                  buttonTheme: panel.querySelector('.file-editor-theme-panel')?.dataset.editorTheme || '',
                  buttonTitle: panel.querySelector('.file-editor-theme-panel')?.title || '',
                }};
              }};
              const normal = read();
              setFileEditorPreviewDisplayMode('vanilla');
              const vanilla = read();
              return {{normal, vanilla}};
            }})();
          </script>
        </body></html>""",
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return window.__previewVanillaReady != null")
    )
    metrics = browser.execute_script("return window.__previewVanillaReady")
    assert metrics["normal"]["vanillaClass"] is False, metrics
    assert metrics["normal"]["headingColor"] != metrics["normal"]["expectedVanilla"]["preview"]["color"], metrics
    # The highlighter's blue keyword is an external syntax semantic, not a YOLOmux token.
    assert metrics["normal"]["codeSpanColor"] == "rgb(0, 0, 255)", metrics
    assert metrics["vanilla"]["vanillaClass"] is True, metrics
    assert metrics["vanilla"]["previewBg"] == metrics["vanilla"]["expectedVanilla"]["preview"]["background"], metrics
    assert metrics["vanilla"]["previewColor"] == metrics["vanilla"]["expectedVanilla"]["preview"]["color"], metrics
    assert metrics["vanilla"]["headingColor"] == metrics["vanilla"]["expectedVanilla"]["preview"]["color"], metrics
    assert metrics["vanilla"]["headingBg"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["vanilla"]["linkColor"] == metrics["vanilla"]["expectedVanilla"]["link"], metrics
    assert metrics["vanilla"]["codeSpanColor"] in (metrics["vanilla"]["expectedVanilla"]["preview"]["color"], ""), metrics
    assert metrics["vanilla"]["buttonTheme"] == "vanilla", metrics
    assert "Vanilla preview" in metrics["vanilla"]["buttonTitle"], metrics


def test_markdown_edit_mode_keeps_colored_syntax_in_codemirror(browser, tmp_path):
    css = app_css()
    bundle_uri = fixture_asset_url("static", "codemirror.js")
    strings = dict(app_english_strings())
    bootstrap = json.dumps(
        {
            "sessions": [],
            "availableAgents": [],
            "accessRole": "admin",
            "homePath": "/home/test",
            "repoRoot": str(REPO_ROOT),
            "maxSessionTabs": 99,
            "serverHostname": "test-host",
            "strings": {"en": strings},
            "codeMirrorAssetUrl": bundle_uri,
        },
        separators=(",", ":"),
    )
    page = tmp_path / "markdown-edit-color.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"""<!doctype html><html><head><meta charset=utf-8><style>{css}</style><script src="{bundle_uri}"></script>
        <style>
        body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; background: #ffffff; }}
        #mount {{ width: 920px; height: 520px; }}
        .file-editor-panel {{ width: 920px; height: 520px; }}
        .file-editor-codemirror-panel {{ height: 100%; }}
        </style></head>
        <body class="theme-light theme-resolved-light editor-theme-light">
          <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
          <div id="mount"></div>
          <script>{app_bundle_before_boot_script()}</script>
          <script>
            window.__markdownColorReady = (async () => {{
              applyActiveColor('blue');
              setFileEditorThemeMode('yolomux-light');
              codeMirrorLanguageExtension = function() {{
                return [{{notARealCodeMirrorExtension: true}}];
              }};
              const path = '/home/test/repo/README.md';
              const content = '# YOLOmux\\n\\n**bold** and [link](README.md)\\n';
              const item = fileEditorItemFor(path);
              setFileState(path, {{
                kind: 'text',
                content,
                original: content,
                dirty: false,
                language: 'markdown',
                gitRoot: '/home/test/repo',
                gitTracked: true,
                gitHasHistory: true,
                gitHistory: [{{ref: 'HEAD'}}, {{ref: 'abc123def'}}],
              }});
              setFileEditorViewMode(path, 'edit', item);
              addFileEditorTabItem(path, item);
              const panel = createFileEditorPanel(item);
              panel.classList.add('active-pane');
              document.getElementById('mount').append(panel);
              renderFileEditorPanel(panel, item);
              const {{frame, probePaint}} = window.__yolomuxTestHelpers;
              for (let attempt = 0; attempt < 120; attempt += 1) {{
                if (panel.querySelector('.cm-content .md-heading')) break;
                await frame();
              }}
              const heading = panel.querySelector('.cm-content .md-heading');
              const visibleHeading = heading?.querySelector('span') || heading;
              const bold = panel.querySelector('.cm-content .md-bold');
              const link = panel.querySelector('.cm-content .md-link');
              setFileEditorThemeMode('yolomux-dark');
              for (let attempt = 0; attempt < 20; attempt += 1) await frame();
              const headingAfterTheme = panel.querySelector('.cm-content .md-heading');
              const boldAfterTheme = panel.querySelector('.cm-content .md-bold');
              const linkAfterTheme = panel.querySelector('.cm-content .md-link');
              return {{
                cmMode: panel._cmMode || '',
                plainFallback: panel._cmPlainFallback === true,
                headingText: heading?.textContent || '',
                headingColor: heading ? getComputedStyle(heading).color : '',
                headingBg: heading ? getComputedStyle(heading).backgroundColor : '',
                visibleHeadingColor: visibleHeading ? getComputedStyle(visibleHeading).color : '',
                visibleHeadingBg: visibleHeading ? getComputedStyle(visibleHeading).backgroundColor : '',
                afterHeadingText: headingAfterTheme?.textContent || '',
                afterHeadingColor: headingAfterTheme ? getComputedStyle(headingAfterTheme).color : '',
                afterHeadingBg: headingAfterTheme ? getComputedStyle(headingAfterTheme).backgroundColor : '',
                expectedHeading: probePaint('color:var(--markdown-heading)', panel).color,
                hasBold: Boolean(bold),
                hasLink: Boolean(link),
                hasBoldAfterTheme: Boolean(boldAfterTheme),
                hasLinkAfterTheme: Boolean(linkAfterTheme),
              }};
            }})();
          </script>
        </body></html>""",
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__markdownColorReady.then(done, error => done({error: String(error)}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["cmMode"] == "edit", metrics
    assert metrics["plainFallback"] is True, metrics
    assert metrics["headingText"].startswith("# YOLOmux"), metrics
    assert metrics["headingColor"] == metrics["expectedHeading"], metrics
    assert metrics["headingBg"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["visibleHeadingColor"] == metrics["expectedHeading"], metrics
    assert metrics["visibleHeadingBg"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["hasBold"] is True, metrics
    assert metrics["hasLink"] is True, metrics
    assert metrics["afterHeadingText"].startswith("# YOLOmux"), metrics
    assert metrics["afterHeadingColor"] == metrics["expectedHeading"], metrics
    assert metrics["afterHeadingBg"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["hasBoldAfterTheme"] is True, metrics
    assert metrics["hasLinkAfterTheme"] is True, metrics
    assert_only_expected_browser_warning(
        browser,
        message="CodeMirror language parser failed; retrying plain editable editor",
        correlation=(
            "Error: Unrecognized extension value in extension set ([object Object]). "
            "This sometimes happens because multiple instances of @codemirror/state are loaded, breaking instanceof checks."
        ),
    )


def test_editor_search_button_toggles_pressed_state_with_codemirror_panel(browser, tmp_path):
    css = app_css()
    bundle_uri = fixture_asset_url("static", "codemirror.js")
    strings = dict(app_english_strings())
    bootstrap = json.dumps(
        {
            "sessions": [],
            "availableAgents": [],
            "accessRole": "admin",
            "homePath": "/home/test",
            "repoRoot": str(REPO_ROOT),
            "maxSessionTabs": 99,
            "serverHostname": "test-host",
            "strings": {"en": strings},
            "codeMirrorAssetUrl": bundle_uri,
        },
        separators=(",", ":"),
    )
    page = tmp_path / "editor-search-toggle.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"""<!doctype html><html><head><meta charset=utf-8><style>{css}</style><script src="{bundle_uri}"></script>
        <style>
        body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; background: #11151d; }}
        #mount {{ width: 920px; height: 520px; }}
        .file-editor-panel {{ width: 920px; height: 520px; }}
        .file-editor-codemirror-panel {{ height: 100%; }}
        </style></head>
        <body class="theme-dark editor-theme-dark">
          <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
          <div id="mount"></div>
          <script>{app_bundle_before_boot_script()}</script>
          <script>
            window.__searchToggleReady = (async () => {{
              const path = '/home/test/repo/app.py';
              const content = 'alpha\\nbeta\\nalpha\\n';
              const item = fileEditorItemFor(path);
              setFileState(path, {{
                kind: 'text',
                content,
                original: content,
                dirty: false,
                language: 'python',
                gitRoot: '/home/test/repo',
                gitTracked: true,
                gitHasHistory: true,
                gitHistory: [{{ref: 'HEAD'}}, {{ref: 'abc123def'}}],
              }});
              setFileEditorViewMode(path, 'edit', item);
              addFileEditorTabItem(path, item);
              const panel = createFileEditorPanel(item);
              panel.classList.add('active-pane');
              document.getElementById('mount').append(panel);
              renderFileEditorPanel(panel, item);
              const {{frame}} = window.__yolomuxTestHelpers;
              const waitFor = window.__yolomuxTestWaitFor;
              await waitFor(() => panel._cmView && panel.querySelector('.file-editor-find-panel')?.hidden === false);
              const button = panel.querySelector('.file-editor-find-panel');
              const before = {{
                pressed: button.getAttribute('aria-pressed'),
                bg: getComputedStyle(button).backgroundColor,
                panelOpen: Boolean(panel.querySelector('.cm-search')),
              }};
              button.click();
              await waitFor(() => button.getAttribute('aria-pressed') === 'true' && panel.querySelector('.cm-search'));
              const opened = {{
                pressed: button.getAttribute('aria-pressed'),
                bg: getComputedStyle(button).backgroundColor,
                panelOpen: Boolean(panel.querySelector('.cm-search')),
              }};
              button.click();
              await waitFor(() => button.getAttribute('aria-pressed') === 'false' && !panel.querySelector('.cm-search'));
              const closed = {{
                pressed: button.getAttribute('aria-pressed'),
                bg: getComputedStyle(button).backgroundColor,
                panelOpen: Boolean(panel.querySelector('.cm-search')),
              }};
              return {{before, opened, closed}};
            }})();
          </script>
        </body></html>""",
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__searchToggleReady.then(done, error => done({error: String(error)}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["before"]["pressed"] == "false", metrics
    assert metrics["before"]["panelOpen"] is False, metrics
    assert metrics["opened"]["pressed"] == "true", metrics
    assert metrics["opened"]["panelOpen"] is True, metrics
    assert metrics["opened"]["bg"] != metrics["before"]["bg"], metrics
    assert metrics["closed"]["pressed"] == "false", metrics
    assert metrics["closed"]["panelOpen"] is False, metrics
    assert metrics["closed"]["bg"] == metrics["before"]["bg"], metrics


def test_codemirror_find_scroll_advances_visible_result_without_snapping(browser, tmp_path):
    css = app_css()
    bundle_uri = fixture_asset_url("static", "codemirror.js")
    bootstrap = json.dumps(
        {
            "sessions": [],
            "availableAgents": [],
            "accessRole": "admin",
            "homePath": "/home/test",
            "repoRoot": str(REPO_ROOT),
            "maxSessionTabs": 99,
            "serverHostname": "test-host",
            "settingsPayload": {
                "settings": {"appearance": {"editor_cursor_color": "yellow"}},
                "defaults": {},
                "mtime_ns": 1,
            },
            "strings": {"en": dict(app_english_strings())},
            "codeMirrorAssetUrl": bundle_uri,
        },
        separators=(",", ":"),
    )
    page = tmp_path / "codemirror-find-scroll.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"""<!doctype html><html><head><meta charset=utf-8><style>{css}</style><script src="{bundle_uri}"></script>
        <style>
        body {{ margin: 0; padding: 8px; display: block; min-height: 0; background: #11151d; }}
        #mount, .file-editor-panel {{ width: 920px; height: 520px; }}
        .file-editor-codemirror-panel {{ height: 100%; }}
        </style></head><body class="theme-dark editor-theme-dark">
          <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script><div id="mount"></div>
          <script>{app_bundle_before_boot_script()}</script>
          <script>
            window.__findScrollReady = (async () => {{
              // Match the app boot path: the configured preference, rather than a test CSS
              // override, re-themes the live CodeMirror search extension.
              applySettingsPayload(clientSettingsPayload, {{initial: true, force: true}});
              const path = '/home/test/repo/2026.md';
              const markedLines = new Set([30, 130, 230, 330]);
              const content = Array.from({{length: 420}}, (_value, index) => (
                markedLines.has(index + 1) ? `Legal result ${{index + 1}}` : `filler line ${{index + 1}}`
              )).join('\\n');
              const item = fileEditorItemFor(path);
              setFileState(path, {{kind: 'text', content, original: content, dirty: false, language: 'markdown'}});
              setFileEditorViewMode(path, 'edit', item);
              addFileEditorTabItem(path, item);
              const panel = createFileEditorPanel(item);
              panel.classList.add('active-pane');
              document.getElementById('mount').append(panel);
              renderFileEditorPanel(panel, item);
              const {{frame}} = window.__yolomuxTestHelpers;
              const waitFor = window.__yolomuxTestWaitFor;
              const ready = await waitFor(() => panel._cmView?.scrollDOM?.scrollHeight > panel._cmView?.scrollDOM?.clientHeight * 3);
              if (!ready) return {{error: 'CodeMirror editor did not become scrollable'}};
              await openEditorFind(panel);
              const input = panel.querySelector('.cm-search input[name="search"]');
              input.value = 'Legal';
              input.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: 'Legal'}}));
              await new Promise(resolve => setTimeout(resolve, 100));
              panel.querySelector('.cm-search .cm-button[name="next"]')?.click();
              await new Promise(resolve => setTimeout(resolve, 50));
              const view = panel._cmView;
              const matches = codeMirrorSearchMatches(content, 'Legal');
              view.dispatch({{selection: {{anchor: matches[0].from, head: matches[0].to}}}});
              await new Promise(resolve => setTimeout(resolve, 50));
              const count = panel.querySelector('.cm-search-count');
              const found = count?.textContent === '1/4' && view.state.selection.main.from === matches[0].from;
              if (!found) return {{error: `Find did not select the first result (${{count?.textContent || ''}})`, searchHtml: panel.querySelector('.cm-search')?.innerHTML || '', inputName: input?.name || '', inputValue: input?.value || ''}};
              const scroller = view.scrollDOM;
              // CodeMirror's viewport can omit the text decoration in headless layout. Probe the
              // same selected-Find class inside this live view, where the actual theme extension
              // and configured preference have installed its scoped rule.
              const selectedMatch = document.createElement('span');
              selectedMatch.className = 'cm-searchMatch-selected';
              selectedMatch.textContent = 'Legal';
              view.contentDOM.append(selectedMatch);
              const firstBackground = getComputedStyle(selectedMatch).backgroundColor;
              selectedMatch.remove();
              const secondBlock = view.lineBlockAt(matches[1].from);
              scroller.scrollTop = Math.max(0, secondBlock.top - 20);
              scroller.dispatchEvent(new Event('scroll', {{bubbles: true}}));
              const manualTop = scroller.scrollTop;
              const advanced = await waitFor(() => (
                count?.textContent === '2/4' && view.state.selection.main.from === matches[1].from
              ));
              await frame(); await frame(); await frame();
              return {{
                advanced,
                beforeCount: '1/4',
                afterCount: count.textContent,
                manualTop,
                afterTop: scroller.scrollTop,
                selectedFrom: view.state.selection.main.from,
                    secondFrom: matches[1].from,
                    firstBackground,
                  }};
            }})();
          </script></body></html>""",
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__findScrollReady.then(done, error => done({error: String(error)}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["advanced"] is True, metrics
    assert metrics["beforeCount"] == "1/4" and metrics["afterCount"] == "2/4", metrics
    assert metrics["selectedFrom"] == metrics["secondFrom"], metrics
    assert abs(metrics["afterTop"] - metrics["manualTop"]) < 32, metrics
    assert metrics["firstBackground"] == "rgb(255, 234, 0)", metrics


def test_long_markdown_editor_restores_scroll_after_codemirror_recreate(browser, tmp_path):
    css = app_css()
    bundle_uri = fixture_asset_url("static", "codemirror.js")
    strings = dict(app_english_strings())
    bootstrap = json.dumps(
        {
            "sessions": [],
            "availableAgents": [],
            "accessRole": "admin",
            "homePath": "/home/test",
            "repoRoot": str(REPO_ROOT),
            "maxSessionTabs": 99,
            "serverHostname": "test-host",
            "strings": {"en": strings},
            "codeMirrorAssetUrl": bundle_uri,
        },
        separators=(",", ":"),
    )
    page = tmp_path / "long-markdown-editor-scroll.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"""<!doctype html><html><head><meta charset=utf-8><style>{css}</style><script src="{bundle_uri}"></script>
        <style>
        body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; background: #11151d; }}
        #mount {{ width: 920px; height: 520px; }}
        .file-editor-panel {{ width: 920px; height: 520px; }}
        .file-editor-codemirror-panel {{ height: 100%; }}
        </style></head>
        <body class="theme-dark editor-theme-dark">
          <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
          <div id="mount"></div>
          <script>{app_bundle_before_boot_script()}</script>
          <script>
            window.__longScrollReady = (async () => {{
              const path = '/home/test/repo/2026.md';
              const content = Array.from({{length: 1400}}, (_value, index) => `# Entry ${{index + 1}}\\n\\n- Work item ${{index + 1}} with enough text to produce normal Markdown editor rows.`).join('\\n');
              const item = fileEditorItemFor(path);
              setFileState(path, {{
                kind: 'text',
                content,
                original: content,
                dirty: false,
                language: 'markdown',
                gitRoot: '/home/test/repo',
                gitTracked: true,
                gitHasHistory: true,
                gitHistory: [{{ref: 'HEAD'}}],
              }});
              setFileEditorViewMode(path, 'edit', item);
              addFileEditorTabItem(path, item);
              const panel = createFileEditorPanel(item);
              panel.classList.add('active-pane');
              document.getElementById('mount').append(panel);
              renderFileEditorPanel(panel, item);
              const {{frame}} = window.__yolomuxTestHelpers;
              const waitFor = window.__yolomuxTestWaitFor;
              const ready = await waitFor(() => {{
                const scroller = panel._cmView?.scrollDOM;
                return scroller && scroller.scrollHeight > scroller.clientHeight * 3;
              }});
              if (!ready) return {{error: 'CodeMirror long editor did not become scrollable'}};
              const firstScroller = panel._cmView.scrollDOM;
              firstScroller.scrollTop = Math.min(9000, firstScroller.scrollHeight - firstScroller.clientHeight - 10);
              await frame();
              await frame();
              const savedTop = firstScroller.scrollTop;
              captureFileEditorPanelViewState(item, panel);
              const savedSnapshot = Boolean(fileEditorViewState.get(item)?.scrollSnapshot);
              destroyCodeMirrorPanel(panel);
              renderFileEditorPanel(panel, item, {{updateActiveFile: false, captureViewState: false}});
              const recreated = await waitFor(() => panel._cmView?.scrollDOM && panel._cmView.scrollDOM !== firstScroller);
              if (!recreated) return {{error: 'CodeMirror editor was not recreated'}};
              restoreFileEditorPanelViewState(item, panel);
              const restored = await waitFor(() => Math.abs((panel._cmView?.scrollDOM?.scrollTop || 0) - savedTop) < 32);
              const finalScroller = panel._cmView.scrollDOM;
              return {{
                savedTop,
                restored,
                restoredTop: finalScroller.scrollTop,
                scrollHeight: finalScroller.scrollHeight,
                clientHeight: finalScroller.clientHeight,
                savedSnapshot,
              }};
            }})();
          </script>
        </body></html>""",
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__longScrollReady.then(done, error => done({error: String(error)}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["savedSnapshot"] is True, metrics
    assert metrics["savedTop"] > metrics["clientHeight"], metrics
    assert metrics["restored"] is True, metrics
    assert abs(metrics["restoredTop"] - metrics["savedTop"]) < 32, metrics


def test_focused_editor_background_rerender_keeps_live_selection_and_scroll(browser, tmp_path):
    css = app_css()
    bundle_uri = fixture_asset_url("static", "codemirror.js")
    bootstrap = json.dumps(
        {
            "sessions": [],
            "availableAgents": [],
            "accessRole": "admin",
            "homePath": "/home/test",
            "repoRoot": str(REPO_ROOT),
            "maxSessionTabs": 99,
            "serverHostname": "test-host",
            "strings": {"en": dict(app_english_strings())},
            "codeMirrorAssetUrl": bundle_uri,
        },
        separators=(",", ":"),
    )
    page = tmp_path / "focused-editor-background-rerender.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"""<!doctype html><html><head><meta charset=utf-8><style>{css}</style><script src="{bundle_uri}"></script>
        <style>
        body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; background: #11151d; }}
        #mount {{ width: 920px; height: 520px; }}
        .file-editor-panel {{ width: 920px; height: 520px; }}
        .file-editor-codemirror-panel {{ height: 100%; }}
        </style></head>
        <body class="theme-dark editor-theme-dark">
          <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
          <div id="mount"></div>
          <script>{app_bundle_before_boot_script()}</script>
          <script>
            window.__focusedRerenderReady = (async () => {{
              const path = '/home/test/repo/interview.md';
              const content = Array.from({{length: 500}}, (_value, index) => `line ${{index + 1}} with enough text to keep CodeMirror scrolling normally`).join('\\n');
              const item = fileEditorItemFor(path);
              setFileState(path, {{kind: 'text', content, original: content, dirty: false, language: 'markdown'}});
              setFileEditorViewMode(path, 'edit', item);
              addFileEditorTabItem(path, item);
              const panel = createFileEditorPanel(item);
              panel.classList.add('active-pane');
              document.getElementById('mount').append(panel);
              renderFileEditorPanel(panel, item);
              const {{frame}} = window.__yolomuxTestHelpers;
              const waitFor = window.__yolomuxTestWaitFor;
              const ready = await waitFor(() => panel._cmView?.scrollDOM?.scrollHeight > panel._cmView?.scrollDOM?.clientHeight * 3);
              if (!ready) return {{error: 'CodeMirror editor did not become scrollable'}};
              const view = panel._cmView;
              const scroller = view.scrollDOM;
              view.focus();
              view.dispatch({{selection: {{anchor: 12, head: 48}}}});
              scroller.scrollTop = Math.min(1600, scroller.scrollHeight - scroller.clientHeight - 10);
              await frame();
              captureFileEditorPanelViewState(item, panel);
              const stale = fileEditorViewState.get(item);
              view.dispatch({{selection: {{anchor: 180, head: 420}}}});
              scroller.scrollTop = Math.min(5200, scroller.scrollHeight - scroller.clientHeight - 10);
              await frame();
              const before = {{anchor: view.state.selection.main.anchor, head: view.state.selection.main.head, top: scroller.scrollTop}};
              fileEditorViewState.set(item, stale);
              renderFileEditorPanel(panel, item, {{updateActiveFile: false, captureViewState: false}});
              await frame();
              await frame();
              await frame();
              const afterFocused = {{anchor: view.state.selection.main.anchor, head: view.state.selection.main.head, top: scroller.scrollTop}};
              const focusedAfterFirstRender = view.hasFocus;
              const focusSink = document.createElement('input');
              document.body.append(focusSink);
              focusSink.focus();
              const unfocusedStale = fileEditorViewState.get(item);
              view.dispatch({{selection: {{anchor: 600, head: 840}}}});
              scroller.scrollTop = Math.min(7600, scroller.scrollHeight - scroller.clientHeight - 10);
              await frame();
              const passiveBefore = {{anchor: view.state.selection.main.anchor, head: view.state.selection.main.head, top: scroller.scrollTop}};
              fileEditorViewState.set(item, unfocusedStale);
              renderFileEditorPanel(panel, item, {{updateActiveFile: false, captureViewState: false}});
              await frame();
              await frame();
              await frame();
              return {{
                focused: focusedAfterFirstRender,
                sameView: panel._cmView === view,
                before,
                after: afterFocused,
                passiveFocused: view.hasFocus,
                passiveBefore,
                passiveAfter: {{anchor: view.state.selection.main.anchor, head: view.state.selection.main.head, top: scroller.scrollTop}},
                stale: {{anchor: stale.anchor, head: stale.head, top: stale.scrollTop}},
              }};
            }})();
          </script>
        </body></html>""",
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__focusedRerenderReady.then(done, error => done({error: String(error)}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["focused"] is True, metrics
    assert metrics["sameView"] is True, metrics
    assert metrics["before"]["anchor"] != metrics["stale"]["anchor"], metrics
    assert metrics["before"]["head"] != metrics["stale"]["head"], metrics
    assert metrics["before"]["top"] > metrics["stale"]["top"], metrics
    assert metrics["after"]["anchor"] == metrics["before"]["anchor"], metrics
    assert metrics["after"]["head"] == metrics["before"]["head"], metrics
    assert abs(metrics["after"]["top"] - metrics["before"]["top"]) < 32, metrics
    assert metrics["passiveFocused"] is False, metrics
    assert metrics["passiveBefore"]["anchor"] != metrics["stale"]["anchor"], metrics
    assert metrics["passiveBefore"]["head"] != metrics["stale"]["head"], metrics
    assert metrics["passiveBefore"]["top"] > metrics["stale"]["top"], metrics
    assert metrics["passiveAfter"]["anchor"] == metrics["passiveBefore"]["anchor"], metrics
    assert metrics["passiveAfter"]["head"] == metrics["passiveBefore"]["head"], metrics
    assert abs(metrics["passiveAfter"]["top"] - metrics["passiveBefore"]["top"]) < 32, metrics
