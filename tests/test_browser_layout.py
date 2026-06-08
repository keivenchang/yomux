from pathlib import Path
import difflib
import json
import re
import shutil
import subprocess

import pytest


pytest.importorskip("selenium")
webdriver = pytest.importorskip("selenium.webdriver")
ActionChains = pytest.importorskip("selenium.webdriver.common.action_chains").ActionChains
Options = pytest.importorskip("selenium.webdriver.chrome.options").Options
WebDriverWait = pytest.importorskip("selenium.webdriver.support.ui").WebDriverWait


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser():
    # Module-scoped: launch headless Chrome ONCE for the whole file instead of per-test (~38 launches).
    # Each test does its own browser.get(fresh file:// fixture); _reset_browser_state clears storage/cookies
    # between tests so the reused driver can't leak state.
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not chrome:
        pytest.skip("Chrome/Chromium is not installed")
    options = Options()
    options.binary_location = chrome
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1000,700")
    driver = webdriver.Chrome(options=options)
    try:
        yield driver
    finally:
        driver.quit()


@pytest.fixture(autouse=True)
def _reset_browser_state(request):
    # Reset the reused module-scoped driver after each test so per-page state (localStorage, cookies,
    # window size) does not bleed into the next test. No-op for tests that don't use the browser.
    yield
    driver = request.node.funcargs.get("browser")
    if driver is None:
        return
    try:
        driver.delete_all_cookies()
        driver.execute_script("try { localStorage.clear(); sessionStorage.clear(); } catch (e) {}")
        driver.set_window_size(1000, 700)
    except Exception:
        pass


def pane_fixture_html(width):
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    tabs = "\n".join(
        f"""
        <button type="button" class="pane-tab {'active' if number == 5 else ''}">
          <span class="pane-tab-core">
            <span class="session-button-prefix"><span class="session-button-number">YO</span></span>
            <span class="session-button-name">{number}</span>
            <span class="session-button-text">
              <span class="tab-symbol session-state-badge">run</span>
              <span class="session-button-dir">fix: stabilize pane and Finder tab workflow</span>
            </span>
          </span>
          <span class="pane-tab-close pc-window-control pc-minimize"></span>
        </button>
        """
        for number in (1, 2, 3, 5, 6, 9)
    )
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <style>
          body {{ margin: 0; padding: 10px; display: block; height: auto; min-height: 0; }}
          .panel {{ width: {width}px; height: 240px; }}
        </style>
      </head>
      <body>
        <div class="panel active-pane">
          <div class="panel-head">
            <div class="tabs" role="tablist">
              <button class="tab tmux-window-step">&lt;</button>
              <button class="tab active terminal-tab">mock_codex.py</button>
              <button class="tab tmux-window-step">&gt;</button>
              <button class="tab">Tx</button>
              <button class="tab">AI</button>
              <button class="tab">Log</button>
              <button class="tab panel-detail-toggle pane-detail-toggle pc-window-control pc-minimize active"></button>
              <button class="tab pane-minimize pc-window-control pc-minimize"></button>
              <button class="tab pane-expand pc-window-control pc-zoom"></button>
            </div>
            <div class="pane-tabs" role="tablist">{tabs}</div>
          </div>
          <div class="panel-detail-row"><div class="meta">branch · path · dirty 1</div><button class="panel-detail-close"></button></div>
          <div class="tab-pane active"><div class="terminal"></div></div>
        </div>
      </body>
    </html>
    """


def menu_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    rows = "\n".join(
        f"""
        <button type="button" class="app-menu-command app-menu-tab-command">
          <span class="app-menu-check" aria-hidden="true"></span>
          <span class="app-menu-content">
            <span class="app-menu-rich">
              <span class="pane-tab-core">
                <span class="session-button-prefix"><span class="session-button-number">YO</span></span>
                <span class="session-button-name">{number}</span>
                <span class="session-button-text">
                  <span class="session-state-badge">run</span>
                  <span class="ci-indicator pr-status-passing">CI</span>
                  <span class="ci-indicator pr-indicator">#10123</span>
                  <span class="session-button-dir tab-inline-detail">fix compact menu density</span>
                </span>
              </span>
            </span>
          </span>
        </button>
        """
        for number in range(1, 31)
    )
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <style>
          body {{ margin: 0; padding: 10px; display: block; height: auto; min-height: 0; overflow: auto; }}
          .app-menu-popover {{
            visibility: visible;
            opacity: 1;
            pointer-events: auto;
            position: static;
            transform: none;
          }}
        </style>
      </head>
      <body>
        <div class="app-menu" data-app-menu="tabs">
          <div class="app-menu-popover" role="menu">{rows}</div>
        </div>
      </body>
    </html>
    """


def pc_controls_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <style>
          body {{ margin: 0; padding: 24px; display: block; height: auto; min-height: 0; }}
        </style>
      </head>
      <body>
        <div class="tabs" role="tablist">
          <button id="hash-tab" class="tab panel-detail-toggle">#</button>
          <button id="list-tab" class="tab terminal-tab">1.</button>
          <button id="pane-actions" class="tab pane-actions"><span id="pane-actions-dots" class="pane-actions-dots">...</span></button>
          <button id="pane-zoom" class="tab pane-expand pc-window-control pc-zoom"></button>
          <button id="hidden-pane-zoom" class="tab pane-expand pc-window-control pc-zoom" hidden></button>
        </div>
        <span id="working-yolo" class="session-yolo-marker active working">YO</span>
        <span id="idle-yolo" class="session-yolo-marker active">YO</span>
        <button type="button" class="pane-tab active" id="info-tab">
          <span class="pane-tab-core"><span class="session-button-dir pane-tab-info-label">YO!info</span></span>
        </button>
        <button type="button" class="pane-tab active">
          <span class="pane-tab-core"><span class="session-button-name">1</span></span>
          <span id="tab-minimize" class="pane-tab-close pc-window-control pc-minimize"></span>
        </button>
        <div class="tabs pane-frame-controls">
          <button id="finder-close" class="tab pane-close pc-window-control pc-close file-explorer-panel-close"></button>
          <button id="editor-close" class="tab pane-close pc-window-control pc-close file-editor-panel-close"></button>
        </div>
        <section class="preferences-section collapsed">
          <button type="button" class="preferences-section-toggle">Appearance</button>
          <div id="collapsed-preferences" class="preferences-settings" hidden>
            <div class="preferences-setting-row">hidden row</div>
          </div>
        </section>
        <div class="file-explorer-tree-panel">
          <div id="collapsed-dir" class="file-tree-row kind-dir" aria-expanded="false"><span class="file-tree-icon">▸</span><span class="file-tree-name">Alpha</span></div>
          <div id="expanded-dir" class="file-tree-row kind-dir expanded" aria-expanded="true"><span class="file-tree-icon">▾</span><span class="file-tree-name">Bravo</span></div>
          <div id="selected-file-row" class="file-tree-row kind-file selected"><span class="file-tree-icon">M</span><span class="file-tree-name">clicked.md</span></div>
          <div id="current-file-row" class="file-tree-row kind-file current-file"><span class="file-tree-icon">M</span><span class="file-tree-name">README.md</span></div>
          <div id="repo-dir" class="file-tree-row kind-dir is-repo repo-non-main"><span class="file-tree-icon">▸</span><span class="file-tree-name">yolomux <span class="file-tree-repo-meta">[<span class="file-tree-repo-branch">feature/repo-row</span> <span class="file-tree-repo-delta">+5/-3</span>]</span></span></div>
        </div>
        <div id="test-context-menu" class="terminal-context-menu" style="top: 220px; left: 24px;"></div>
        <div id="test-image-preview" class="file-image-preview-popover" style="top: 220px; left: 24px;"></div>
        <div class="pane-tab popover-open" style="position: fixed; top: 220px; left: 24px;">
          <div id="test-tab-popover" class="session-popover"></div>
        </div>
      </body>
    </html>
    """


def topbar_font_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <style>
          body {{ margin: 0; padding: 12px; display: block; height: auto; min-height: 0; }}
          :root {{ --ui-font-size: 18px; --tab-label-size: 18px; }}
        </style>
      </head>
      <body>
        <header id="topbar-fixture" class="topbar">
          <div class="brand"><span class="title">YOLOmux</span></div>
          <div class="app-menu-area">
            <nav class="app-menu-bar" aria-label="Application menu">
              <div class="app-menu"><button id="menu-file" type="button" class="app-menu-button">File</button></div>
            </nav>
          </div>
          <div class="actions">
            <button id="tabMetaToggle" type="button" aria-label="Tab metadata"></button>
            <button id="notifyToggle" type="button" aria-label="Alerts"></button>
            <button id="refreshMeta" type="button" aria-label="Refresh"></button>
          </div>
        </header>
        <button type="button" class="pane-tab active">
          <span class="pane-tab-core"><span class="session-button-name">1</span></span>
        </button>
      </body>
    </html>
    """


def editor_pane_ignores_legacy_body_class_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <style>
          body {{ margin: 0; display: block; height: 700px; min-height: 0; }}
          #grid {{ height: 500px; }}
        </style>
      </head>
      <body class="file-editor-open">
        <main id="grid" class="grid">
          <section class="layout-root">
            <section class="layout-column file-editor-column">
              <section class="drop-slot">
                <article class="panel file-editor-panel"></article>
              </section>
            </section>
          </section>
        </main>
      </body>
    </html>
    """


def codemirror_editor_controls_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    tabs = "\n".join(
        f"""
        <div role="button" tabindex="0" class="pane-tab {'active popover-open' if index == 2 else ''}">
          <span class="pane-tab-core"><span class="session-button-name">file-{index}.md</span></span>
          <span class="pane-tab-close pc-window-control pc-close"></span>
          {'''
          <div id="file-popover" class="session-popover file-popover">
            <button id="file-popover-copy" class="path-copy-button popover-copy-button"></button>
          </div>
          ''' if index == 2 else ''}
        </div>
        """
        for index in range(1, 6)
    )
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <style>
          body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; }}
          .file-editor-panel {{ width: 930px; height: 360px; }}
        </style>
      </head>
      <body class="editor-theme-light">
        <article class="panel file-editor-panel active-pane editor-wrap">
          <div class="panel-head file-editor-panel-head">
            <div id="editor-actions" class="file-editor-panel-actions">
              <div class="file-editor-mode-control file-editor-mode-control-panel" role="group">
                <button type="button" data-editor-mode="edit"><span class="file-editor-icon file-editor-icon-edit"></span></button>
                <button type="button" data-editor-mode="preview"><span class="file-editor-icon file-editor-icon-eye"></span></button>
                <button type="button" data-editor-mode="split"><span class="file-editor-icon file-editor-icon-split"></span></button>
                <button type="button" class="file-editor-popout-preview-panel"><span class="file-editor-icon file-editor-icon-popout-preview"></span></button>
              </div>
              <button type="button" class="file-editor-gutter-panel active">#</button>
              <button type="button" class="file-editor-wrap-panel active"><span class="file-editor-icon file-editor-icon-wrap"></span></button>
              <button type="button" class="file-editor-find-panel"><span class="file-editor-icon file-editor-icon-find"></span></button>
              <button type="button" class="file-editor-blame-panel" aria-pressed="true"><span class="file-editor-icon file-editor-icon-blame"></span></button>
              <button type="button" class="file-editor-diff-panel active">ΔDiff</button>
              <button type="button" class="file-editor-diff-expand-panel" aria-pressed="true">↕</button>
              <button type="button" class="file-editor-theme-panel theme-light" data-editor-theme="light"><span class="file-editor-icon file-editor-icon-theme"></span></button>
              <button type="button" class="file-editor-reload-panel">Reload</button>
              <button type="button" class="file-editor-save-panel"><span class="file-editor-icon file-editor-icon-save"></span></button>
              <div class="tabs pane-frame-controls file-editor-frame-controls">
                <button class="tab pane-minimize pc-window-control pc-minimize"></button>
                <button class="tab pane-expand pc-window-control pc-zoom"></button>
                <button type="button" class="tab pane-close pc-window-control pc-close file-editor-panel-close"></button>
              </div>
            </div>
            <div class="pane-tabs" role="tablist">{tabs}</div>
          </div>
          <div class="file-editor-panel-body">
            <div class="file-editor-content">
              <div class="file-editor-codemirror-panel">
                <div id="cm-editor" class="cm-editor">
                  <div class="cm-panels cm-panels-top">
                    <div class="cm-panel cm-search">
                      <input id="search-field" class="cm-textfield" name="search" value="precedence">
                      <button class="cm-button" name="next" title="Next match (Enter)">next</button>
                      <button class="cm-button" name="prev" title="Previous match (Shift+Enter)">previous</button>
                      <button class="cm-button" name="select">all</button>
                      <label id="match-label"><input id="match-case" type="checkbox">match case</label>
                      <label><input type="checkbox">regexp</label>
                      <label><input type="checkbox">by word</label>
                      <button class="cm-dialog-close" type="button">x</button>
                      <br>
                      <input id="replace-field" class="cm-textfield" name="replace" placeholder="Replace">
                      <button class="cm-button" name="replace">replace</button>
                      <button class="cm-button" name="replaceAll">replace all</button>
                      <span id="search-count" class="cm-search-count">3/102</span>
                    </div>
                  </div>
                  <div class="cm-scroller">
                    <div class="cm-wrap-marker-layer"><span id="wrap-marker" class="cm-wrap-marker" style="left: 10px; top: 20px; height: 16px;">↪</span></div>
                    <div class="cm-content cm-lineWrapping">
                      <div id="wrapped-line" class="cm-line">a very long markdown line that wraps in the editor panel and needs a visible wrap marker</div>
                    </div>
                  </div>
                </div>
              </div>
              <div id="light-syntax-probe" class="file-editor-raw-panel" hidden>
                <span class="code-keyword">if</span>
                <span class="code-string">"value"</span>
                <span class="code-variable">name</span>
                <span class="code-function">call</span>
                <span class="code-comment"># comment</span>
                <span class="md-heading md-heading-1"># title</span>
                <span class="md-code">`code`</span>
                <span class="md-list-marker">- </span>
                <span class="md-link">[link]</span>
              </div>
            </div>
          </div>
        </article>
      </body>
    </html>
    """


def editor_diff_ref_toolbar_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <style>
          body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; }}
          .file-editor-panel {{ width: 520px; height: 120px; }}
        </style>
      </head>
      <body>
        <article class="panel file-editor-panel active-pane">
          <div class="file-editor-toolbar" role="toolbar">
            <button id="gutter-button" type="button" class="file-editor-gutter-panel active" aria-pressed="true">#</button>
            <button id="diff-button" type="button" class="file-editor-diff-panel active" aria-pressed="true">ΔDiff</button>
            <button id="diff-expand-button" type="button" class="file-editor-diff-expand-panel" aria-pressed="true">↕</button>
            <span id="diff-ref-panel" class="file-editor-diff-ref-panel">
              <span class="diff-ref-controls compact" data-diff-ref-controls data-diff-ref-repo="/repo/app">
                <label class="diff-ref-control">FROM <input id="from-ref" class="diff-ref-input" data-diff-ref-from value="abcdef123"></label>
                <label class="diff-ref-control">TO <input id="to-ref" class="diff-ref-input" data-diff-ref-to value="current"></label>
                <button id="reset-ref" type="button" class="diff-ref-reset" data-diff-ref-reset title="Reset" aria-label="Reset">↺</button>
              </span>
            </span>
            <button type="button" class="file-editor-wrap-panel active"><span class="file-editor-icon file-editor-icon-wrap"></span></button>
            <button type="button" class="file-editor-find-panel"><span class="file-editor-icon file-editor-icon-find"></span></button>
            <button type="button" class="file-editor-theme-panel"><span class="file-editor-icon file-editor-icon-theme"></span></button>
            <button type="button" class="file-editor-save-panel"><span class="file-editor-icon file-editor-icon-save"></span></button>
          </div>
        </article>
      </body>
    </html>
    """


def codemirror_bundle_fixture_html():
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <script src="{bundle_uri}"></script>
      </head>
      <body></body>
    </html>
    """


def codemirror_todo_diff_overview_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    strings = json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text(encoding="utf-8"))
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
        }
    )
    original = subprocess.check_output(["git", "show", "7f5a7e82ce:TODO.md"], cwd=REPO_ROOT, text=True)
    current = subprocess.check_output(["git", "show", "HEAD:TODO.md"], cwd=REPO_ROOT, text=True)
    app_script = app_bundle_before_boot_script()
    original_json = json.dumps(original).replace("</script", "<\\/script")
    current_json = json.dumps(current).replace("</script", "<\\/script")
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <script src="{bundle_uri}"></script>
        <style>
          body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; background: #11151d; }}
          #mount {{ width: 920px; height: 640px; }}
          .file-editor-panel {{ width: 920px; height: 640px; }}
          .file-editor-codemirror-panel {{ height: 100%; }}
        </style>
      </head>
      <body class="theme-dark editor-theme-dark">
        <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
        <div id="mount"></div>
        <script>{app_script}</script>
        <script>
          (function() {{
            const original = {original_json};
            const current = {current_json};
            const CM = window.YOLOmuxCodeMirror;
            diffExpandUnchanged = true;
            const panel = document.createElement('article');
            panel.className = 'panel file-editor-panel active-pane';
            const container = document.createElement('div');
            container.className = 'file-editor-codemirror-panel file-editor-diff-codemirror';
            panel.append(container);
            document.getElementById('mount').append(panel);
            const view = new CM.EditorView({{
              state: CM.EditorState.create({{
                doc: current,
                extensions: [
                  CM.unifiedMergeView({{original, highlightChanges: false, gutter: true}}),
                  CM.lineNumbers(),
                ],
              }}),
              parent: container,
            }});
            panel._cmView = view;
            panel._cmMode = 'diff';
            const chunks = diffOverviewCodeMirrorChunks(view, panel);
            const rows = diffOverviewRowsFromCodeMirrorChunks(chunks, current, original);
            updateCodeMirrorDiffOverview(panel, container, {{diff: ''}}, current, original);
            const overview = container.querySelector('.cm-diff-overview');
            const overviewRect = overview.getBoundingClientRect();
            const scrollerRect = view.scrollDOM.getBoundingClientRect();
            const verticalTrackBottom = scrollerRect.top + view.scrollDOM.clientHeight;
            const normalizeOverviewColor = color => {{
              const value = String(color || '').toLowerCase();
              if (value === 'transparent' || value === '#ff5d6c' || value === '#38d878') return value;
              const rgb = /^rgb\\(\\s*(\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)\\s*\\)$/.exec(value);
              if (!rgb) return value;
              const hex = rgb.slice(1).map(part => Number(part).toString(16).padStart(2, '0')).join('');
              return `#${{hex}}`;
            }};
            const parseStops = gradient => {{
              const value = String(gradient || '');
              const stops = [];
              const rangePattern = /(#[0-9a-f]{{6}}|rgb\\(\\s*\\d+\\s*,\\s*\\d+\\s*,\\s*\\d+\\s*\\)|transparent)\\s+([0-9.]+)%\\s+([0-9.]+)%/gi;
              let match = rangePattern.exec(value);
              while (match) {{
                const color = normalizeOverviewColor(match[1]);
                if (color !== 'transparent') {{
                  stops.push({{color, start: match[2], end: match[3]}});
                }}
                match = rangePattern.exec(value);
              }}
              if (stops.length) return stops;
              const tokens = [];
              const tokenPattern = /(#[0-9a-f]{{6}}|rgb\\(\\s*\\d+\\s*,\\s*\\d+\\s*,\\s*\\d+\\s*\\)|transparent)\\s+([0-9.]+)%/gi;
              match = tokenPattern.exec(value);
              while (match) {{
                tokens.push({{color: normalizeOverviewColor(match[1]), pos: match[2]}});
                match = tokenPattern.exec(value);
              }}
              for (let index = 1; index < tokens.length; index += 1) {{
                const prev = tokens[index - 1];
                const next = tokens[index];
                if (prev.color === next.color && prev.color !== 'transparent') {{
                  stops.push({{color: prev.color, start: prev.pos, end: next.pos}});
                }}
              }}
              return stops;
            }};
            const renderedRows = diffOverviewRowsFromCodeMirrorRenderedWeights(view, chunks, current, original, container) || rows;
            const expectedGradient = buildDiffOverviewGradientFromBands(renderedRows.bands, renderedRows.totalRows);
            window.__todoDiffOverviewMetrics = {{
              chunks: chunks.map(chunk => ({{
                fromA: chunk.fromA,
                toA: chunk.toA,
                endA: chunk.endA,
                fromB: chunk.fromB,
                toB: chunk.toB,
                endB: chunk.endB,
              }})),
              rows,
              renderedRows,
              overviewBackground: overview?.style?.background || '',
              overviewStops: parseStops(overview?.style?.background || ''),
              expectedStops: parseStops(expectedGradient),
              tickCount: container.querySelectorAll('.cm-diff-overview-tick').length,
              deletedDomRows: container.querySelectorAll('.cm-deletedLine').length,
              insertedRangeRows: rows?.bands?.find(band => band.kind === 'add')?.end - rows?.bands?.find(band => band.kind === 'add')?.start,
              removedRangeRows: rows?.bands?.find(band => band.kind === 'remove')?.end - rows?.bands?.find(band => band.kind === 'remove')?.start,
              overviewTopDelta: Math.abs(overviewRect.top - scrollerRect.top),
              overviewBottomDelta: Math.abs(overviewRect.bottom - verticalTrackBottom),
            }};
          }})();
        </script>
      </body>
    </html>
    """


def codemirror_file_explorer_diff_overview_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    strings = json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text(encoding="utf-8"))
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
        }
    )
    path = "static_src/js/yolomux/40_file_explorer_files.js"
    original = subprocess.check_output(["git", "show", f"e01be55:{path}"], cwd=REPO_ROOT, text=True)
    current = subprocess.check_output(["git", "show", f"595cad161a:{path}"], cwd=REPO_ROOT, text=True)
    app_script = app_bundle_before_boot_script()
    original_json = json.dumps(original).replace("</script", "<\\/script")
    current_json = json.dumps(current).replace("</script", "<\\/script")
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <script src="{bundle_uri}"></script>
        <style>
          body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; background: #11151d; }}
          #mount {{ width: 920px; height: 640px; }}
          .file-editor-panel {{ width: 920px; height: 640px; }}
          .file-editor-codemirror-panel {{ height: 100%; }}
        </style>
      </head>
      <body class="theme-dark editor-theme-dark">
        <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
        <div id="mount"></div>
        <script>{app_script}</script>
        <script>
          (function() {{
            const original = {original_json};
            const current = {current_json};
            const CM = window.YOLOmuxCodeMirror;
            diffExpandUnchanged = true;
            const panel = document.createElement('article');
            panel.className = 'panel file-editor-panel active-pane';
            const container = document.createElement('div');
            container.className = 'file-editor-codemirror-panel file-editor-diff-codemirror';
            panel.append(container);
            document.getElementById('mount').append(panel);
            const view = new CM.EditorView({{
              state: CM.EditorState.create({{
                doc: current,
                extensions: [
                  CM.unifiedMergeView({{original, highlightChanges: false, gutter: true}}),
                  CM.lineNumbers(),
                ],
              }}),
              parent: container,
            }});
            panel._cmView = view;
            panel._cmMode = 'diff';
            updateCodeMirrorDiffOverview(panel, container, {{diff: ''}}, current, original);

            const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const normalizeColor = color => {{
              const value = String(color || '').toLowerCase();
              if (value === 'transparent' || value === '#ff5d6c' || value === '#38d878') return value;
              const rgb = /^rgba?\\(\\s*(\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)/.exec(value);
              if (!rgb) return value;
              return `#${{rgb.slice(1).map(part => Number(part).toString(16).padStart(2, '0')).join('')}}`;
            }};
            const parseStops = gradient => {{
              const value = String(gradient || '');
              const stops = [];
              const rangePattern = /(#[0-9a-f]{{6}}|rgb\\(\\s*\\d+\\s*,\\s*\\d+\\s*,\\s*\\d+\\s*\\)|transparent)\\s+([0-9.]+)%\\s+([0-9.]+)%/gi;
              let match = rangePattern.exec(value);
              while (match) {{
                stops.push({{color: normalizeColor(match[1]), start: Number(match[2]) / 100, end: Number(match[3]) / 100}});
                match = rangePattern.exec(value);
              }}
              if (stops.length) return stops;
              const tokens = [];
              const tokenPattern = /(#[0-9a-f]{{6}}|rgb\\(\\s*\\d+\\s*,\\s*\\d+\\s*,\\s*\\d+\\s*\\)|transparent)\\s+([0-9.]+)%/gi;
              match = tokenPattern.exec(value);
              while (match) {{
                tokens.push({{color: normalizeColor(match[1]), pos: Number(match[2]) / 100}});
                match = tokenPattern.exec(value);
              }}
              for (let index = 1; index < tokens.length; index += 1) {{
                const prev = tokens[index - 1];
                const next = tokens[index];
                if (next.pos > prev.pos) stops.push({{color: prev.color, start: prev.pos, end: next.pos}});
              }}
              return stops;
            }};
            const changedStops = gradient => parseStops(gradient).filter(item => item.color !== 'transparent');
            const overview = () => container.querySelector('.cm-diff-overview');
            const railColorAt = fraction => {{
              const stop = parseStops(overview()?.style?.background || '').find(item => fraction >= item.start && fraction < item.end);
              if (!stop || stop.color === 'transparent') return 'normal';
              if (stop.color === '#ff5d6c') return 'remove';
              if (stop.color === '#38d878') return 'add';
              return stop.color;
            }};
            const rowKind = node => {{
              for (let currentNode = node; currentNode && currentNode !== document.body; currentNode = currentNode.parentElement) {{
                if (currentNode.classList?.contains('cm-deletedLine') || currentNode.classList?.contains('cm-deletedChunk')) return 'remove';
                if (currentNode.classList?.contains('cm-changedLine') || currentNode.classList?.contains('cm-insertedLine')) return 'add';
              }}
              return 'normal';
            }};
            const visibleKindAt = y => {{
              const rect = view.scrollDOM.getBoundingClientRect();
              const x = rect.left + Math.min(220, Math.max(50, rect.width * 0.35));
              const nodes = document.elementsFromPoint(x, rect.top + y);
              const row = nodes.find(node => node.classList?.contains('cm-line') || node.classList?.contains('cm-deletedLine') || node.classList?.contains('cm-deletedChunk'))
                || nodes.find(node => node.closest?.('.cm-line, .cm-deletedLine, .cm-deletedChunk'))?.closest('.cm-line, .cm-deletedLine, .cm-deletedChunk');
              return row ? rowKind(row) : 'none';
            }};
            window.__fileExplorerDiffOverviewMetrics = async function() {{
              await settle();
              const cmChunks = diffOverviewCodeMirrorChunks(view, panel);
              const chunks = cmChunks.map(chunk => ({{
                fromA: chunk.fromA,
                toA: chunk.toA,
                endA: chunk.endA,
                fromB: chunk.fromB,
                toB: chunk.toB,
                endB: chunk.endB,
              }}));
              const initialBackground = overview()?.style?.background || '';
              const initialOverviewPresent = Boolean(overview());
              const initialDeletedDomRows = container.querySelectorAll('.cm-deletedLine').length;
              const fullRows = diffOverviewRowsFromCodeMirrorRenderedWeights(view, cmChunks, current, original, container)
                || diffOverviewRowsFromCodeMirrorChunks(cmChunks, current, original);
              const expectedFullBackground = buildDiffOverviewGradientFromBands(fullRows.bands, fullRows.totalRows);
              const positions = [
                {{name: 'top-normal', scrollTop: 0}},
                {{name: 'warmup-normal-05', scrollTop: 3504}},
                {{name: 'warmup-normal-15', scrollTop: 10512}},
                {{name: 'warmup-red-25', scrollTop: 17520}},
                {{name: 'warmup-red-30', scrollTop: 21024}},
                {{name: 'warmup-red-34', scrollTop: 23827}},
                {{name: 'warmup-red-38', scrollTop: 26630}},
                {{name: 'warmup-red-42', scrollTop: 29433}},
                {{name: 'warmup-red-45', scrollTop: 31536}},
                {{name: 'warmup-green-50', scrollTop: 35040}},
                {{name: 'warmup-green-60', scrollTop: 42049}},
                {{name: 'red-middle-previous-regression', scrollTop: 49505}},
                {{name: 'red-late-previous-regression', scrollTop: 56065}},
                {{name: 'green-middle', scrollTop: 70081}},
              ];
              const cases = [];
              for (const item of positions) {{
                view.scrollDOM.scrollTop = item.scrollTop;
                await settle();
                updateCodeMirrorDiffOverview(panel, container, {{diff: ''}}, current, original);
                await settle();
                const scrollHeight = Math.max(1, Number(view.scrollDOM.scrollHeight || 0));
                const clientHeight = Math.max(1, Number(view.scrollDOM.clientHeight || 0));
                const sampleYs = [20, 80, 160, 260, 380].filter(y => y < clientHeight);
                const samples = sampleYs.map(y => {{
                  const fraction = (Number(view.scrollDOM.scrollTop || 0) + y) / scrollHeight;
                  return {{y, fraction, visible: visibleKindAt(y), rail: railColorAt(fraction)}};
                }});
                cases.push({{
                  name: item.name,
                  requestedScrollTop: item.scrollTop,
                  scrollTop: view.scrollDOM.scrollTop,
                  scrollHeight,
                  clientHeight,
                  railPresent: Boolean(overview()),
                  background: overview()?.style?.background || '',
                  deletedDomRows: container.querySelectorAll('.cm-deletedLine').length,
                  samples,
                  mismatches: overview() ? samples.filter(sample => sample.visible !== 'none' && sample.visible !== sample.rail) : [],
                }});
              }}
              return {{
                chunks,
                initialBackground,
                initialOverviewPresent,
                initialDeletedDomRows,
                initialChangedStops: changedStops(initialBackground),
                fullRows,
                finalBackground: overview()?.style?.background || '',
                finalOverviewPresent: Boolean(overview()),
                finalChangedStops: changedStops(overview()?.style?.background || ''),
                expectedFullChangedStops: changedStops(expectedFullBackground),
                tickCount: container.querySelectorAll('.cm-diff-overview-tick').length,
                cases,
              }};
            }};
          }})();
        </script>
      </body>
    </html>
    """


def app_bundle_before_boot_script():
    source = (REPO_ROOT / "static" / "yolomux.js").read_text(encoding="utf-8")
    boot_start = source.index("if (refreshMeta) {")
    return source[:boot_start].replace("</script", "<\\/script")


def codemirror_wrap_toggle_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    strings = json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text(encoding="utf-8"))
    bootstrap = json.dumps(
        {
            "sessions": [],
            "availableAgents": [],
            "accessRole": "admin",
            "homePath": "/home/test",
            "repoRoot": "/home/test/yolomux.dev",
            "maxSessionTabs": 99,
            "serverHostname": "test-host",
            "strings": {"en": strings},
        }
    )
    app_script = app_bundle_before_boot_script()
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <script src="{bundle_uri}"></script>
        <style>
          body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; }}
          #wrap-regression-mount {{ width: 760px; height: 360px; }}
          .file-editor-panel {{ width: 760px; height: 360px; }}
        </style>
      </head>
      <body class="theme-dark editor-theme-dark">
        <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
        <div id="wrap-regression-mount"></div>
        <script>try {{ localStorage.removeItem('yolomux.editorWrap'); }} catch (_) {{}}</script>
        <script>
          window.__wrapRegressionErrors = [];
          window.addEventListener('error', event => {{
            window.__wrapRegressionErrors.push(event.message || String(event.error || event));
          }});
          window.addEventListener('unhandledrejection', event => {{
            window.__wrapRegressionErrors.push(String(event.reason || event));
          }});
        </script>
        <script>{app_script}</script>
        <script>
          (function() {{
            const path = '/home/test/yolomux.dev/wrap-regression.txt';
            const doc = [
              'Word wrap regression',
              '',
              'This line must stay visible after clicking Enable Word Wrap in the editor toolbar.',
              'A second line makes an empty CodeMirror document obvious.'
            ].join('\\n');
            const item = fileEditorItemFor(path);
            setFileState(path, {{
              kind: 'text',
              content: doc,
              original: doc,
              dirty: false,
              language: 'markdown',
              gitTracked: false,
              gitHasHistory: false,
              gitHistory: []
            }});
            setFileEditorViewMode(path, 'edit', item);
            addFileEditorTabItem(path, item);
            const panel = createFileEditorPanel(item);
            panel.id = 'wrap-regression-panel';
            panel.classList.add('active-pane');
            document.getElementById('wrap-regression-mount').append(panel);
            renderFileEditorPanel(panel, item);
            window.__wrapRegressionReady = new Promise((resolve, reject) => {{
              let attempts = 0;
              const wait = () => {{
                const viewText = panel._cmView?.state?.doc?.toString?.() || '';
                const contentText = panel.querySelector('.cm-content')?.textContent || '';
                if (panel._cmView && viewText.includes('This line must stay visible') && contentText) {{
                  window.__wrapRegressionInitialView = panel._cmView;
                  window.__wrapRegressionRenderCalls = 0;
                  const originalRender = renderFileEditorPanel;
                  renderFileEditorPanel = function(...args) {{
                    window.__wrapRegressionRenderCalls += 1;
                    return originalRender.apply(this, args);
                  }};
                  window.__wrapRegressionReconfigCalls = [];
                  const originalReconfigure = reconfigureCodeMirrorPanelEditorOptions;
                  reconfigureCodeMirrorPanelEditorOptions = function(targetPanel) {{
                    const result = originalReconfigure(targetPanel);
                    const view = targetPanel?._cmView;
                    const contentFacet = view
                      ? view.state.facet(window.YOLOmuxCodeMirror.EditorView.contentAttributes)
                      : [];
                    window.__wrapRegressionReconfigCalls.push({{
                      result,
                      targetId: targetPanel?.id || '',
                      path: targetPanel?.dataset?.filePath || '',
                      stateKind: fileStateFor(targetPanel?.dataset?.filePath || '')?.kind || '',
                      classes: contentFacet.map(item => item.class || '').join('|'),
                    }});
                    return result;
                  }};
                  resolve(true);
                  return;
                }}
                attempts += 1;
                if (attempts > 120) {{
                  const status = panel.querySelector('.file-editor-status-panel')?.textContent || '';
                  const cmPane = panel.querySelector('.file-editor-codemirror-panel');
                  const rawPane = panel.querySelector('.file-editor-raw-panel');
                  const cmKeys = Object.keys(window.YOLOmuxCodeMirror || {{}}).slice(0, 20).join(',');
                  reject(new Error([
                    'CodeMirror editor did not render test document',
                    `status=${{status}}`,
                    `cmHidden=${{cmPane?.hidden}}`,
                    `cmText=${{cmPane?.textContent || ''}}`,
                    `rawHidden=${{rawPane?.hidden}}`,
                    `rawText=${{rawPane?.textContent || ''}}`,
                    `cmKeys=${{cmKeys}}`
                  ].join(' | ')));
                  return;
                }}
                requestAnimationFrame(wait);
              }};
              wait();
            }});
          }})();
        </script>
      </body>
    </html>
    """


def finder_click_toolbar_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <style>
          body {{ margin: 0; padding: 8px; display: grid; grid-template-columns: 420px 1fr; gap: 8px; height: auto; min-height: 0; }}
          .panel {{ height: 230px; }}
        </style>
      </head>
      <body>
        <article id="finder-panel" class="panel file-explorer-panel active-pane">
          <div class="panel-head file-explorer-head">
            <div class="pane-tabs" hidden></div>
            <div class="file-explorer-toolbar">
              <div class="file-explorer-toolbar-row file-explorer-primary-row">
                <span class="file-explorer-mode-switcher" role="group" aria-label="Finder / ΔDiff">
                  <button type="button" class="file-explorer-mode-toggle" data-file-explorer-mode-set="files" aria-pressed="true"><span class="file-explorer-mode-label">Finder</span></button>
                  <button type="button" class="file-explorer-mode-toggle" data-file-explorer-mode-set="diff" aria-pressed="false"><span class="file-explorer-mode-label">ΔDiff</span></button>
                </span>
                <label class="file-explorer-diff-session-control file-explorer-mode-diff-only changes-control">Session: <select class="file-explorer-diff-session-select" data-session-files-session><option>project1</option></select></label>
                <input class="file-explorer-path-inline file-explorer-mode-files-only" value="/home/keivenc/yolomux.dev/static_src/js/yolomux">
                <button type="button" class="path-copy-button file-explorer-path-copy-panel file-explorer-mode-files-only"></button>
                <span class="file-explorer-toolbar-spacer"></span>
                <button type="button" class="file-explorer-header-action file-explorer-changes-collapse-toggle file-explorer-mode-diff-only" data-session-files-collapse-toggle aria-pressed="false">▴</button>
                <div class="tabs pane-frame-controls file-explorer-frame-controls">
                  <button type="button" class="tab pane-close pc-window-control pc-close file-explorer-panel-close"></button>
                </div>
              </div>
              <div class="file-explorer-toolbar-row file-explorer-scope-row file-explorer-mode-files-only">
                <button type="button" class="file-explorer-hidden-toggle file-explorer-hidden-toggle-panel file-explorer-mode-files-only">.*</button>
                <button type="button" class="file-explorer-root-mode-toggle file-explorer-root-mode-toggle-panel file-explorer-mode-files-only active" aria-pressed="true">Sync</button>
                <div class="file-explorer-quick-access-panel file-explorer-mode-files-only">
                  <button type="button" class="file-explorer-quick-access-button">~</button>
                  <button type="button" class="file-explorer-quick-access-button">/*</button>
                  <button type="button" class="file-explorer-quick-access-button">/tmp</button>
                </div>
                <span class="file-explorer-toolbar-spacer"></span>
              </div>
              <div class="file-explorer-toolbar-row file-explorer-actions-row file-explorer-mode-files-only">
                <button type="button" class="file-explorer-header-action file-explorer-mode-files-only" data-file-explorer-collapse>▤</button>
                <button type="button" class="file-explorer-header-action file-explorer-mode-files-only" id="new-file" data-file-explorer-new-file>+</button>
                <button type="button" class="file-explorer-header-action file-explorer-folder-action file-explorer-mode-files-only" data-file-explorer-new-folder><span class="file-explorer-folder-icon" aria-hidden="true"></span></button>
                <span class="file-explorer-toolbar-spacer"></span>
                <select class="file-explorer-sort-select file-explorer-mode-files-only"><option>A-Z</option></select>
                <span class="file-explorer-date-reload-cluster file-explorer-mode-files-only">
                  <button type="button" class="file-explorer-header-action file-explorer-date-toggle changes-date-toggle">日期</button>
                  <button type="button" class="changes-refresh">Reload</button>
                </span>
              </div>
            </div>
          </div>
          <div class="file-explorer-pane">
            <div class="file-explorer-tree-panel" tabindex="0">tree rows</div>
            <div class="file-explorer-changes-resizer"></div>
            <div id="modified-files-panel" class="file-explorer-changes-panel" tabindex="0">
              <div class="changes-toolbar file-explorer-diff-toolbar">
                <label class="changes-control">Sort <select data-session-files-sort><option>new</option></select></label>
                <button type="button" data-file-explorer-tree-dates>Ago</button>
                <button type="button" class="changes-refresh" data-session-files-refresh>Reload</button>
              </div>
              <div id="modified-files-head" class="file-explorer-changes-head">
                <span class="changes-title">Differ: '5'</span>
              </div>
            </div>
          </div>
        </article>
        <article id="terminal-panel" class="panel active-pane">
          <div class="panel-head">
            <div id="terminal-toolbar" class="tabs" role="tablist">
              <button class="tab active terminal-tab">1</button>
              <button class="tab">Tx</button>
              <button class="tab">AI</button>
              <button class="tab">Log</button>
              <button class="tab panel-detail-toggle pane-detail-toggle pc-window-control pc-minimize active"></button>
              <button class="tab pane-actions"><span class="pane-actions-dots">...</span></button>
              <button class="tab pane-minimize pc-window-control pc-minimize"></button>
              <button class="tab pane-expand pc-window-control pc-zoom"></button>
            </div>
            <div class="pane-tabs" role="tablist">
              <button type="button" class="pane-tab active"><span class="pane-tab-core"><span class="session-button-name">1</span></span></button>
            </div>
          </div>
          <div class="panel-detail-row"><div class="meta">path</div></div>
          <div class="tab-pane active"></div>
        </article>
        <script>
          document.body.classList.add('file-explorer-mode-files');
          document.querySelectorAll('[data-file-explorer-mode-set]').forEach(button => button.addEventListener('click', () => {{
            const nextDiff = button.dataset.fileExplorerModeSet === 'diff';
            document.body.classList.toggle('file-explorer-mode-diff', nextDiff);
            document.body.classList.toggle('file-explorer-mode-files', !nextDiff);
            document.getElementById('finder-panel').dataset.fileExplorerMode = nextDiff ? 'diff' : 'files';
            document.querySelectorAll('[data-file-explorer-mode-set]').forEach(toggle => {{
              toggle.setAttribute('aria-pressed', toggle.dataset.fileExplorerModeSet === (nextDiff ? 'diff' : 'files') ? 'true' : 'false');
            }});
          }}));
        </script>
      </body>
    </html>
    """


def file_tree_status_alignment_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <style>
          body {{ margin: 0; padding: 10px; width: 980px; }}
          .file-explorer-tree {{ width: 960px; overflow: visible; }}
          .narrow-file-explorer-tree {{ width: 360px; overflow: hidden; margin-top: 18px; }}
        </style>
      </head>
      <body class="theme-light">
        <div class="file-explorer-tree">
          <div id="status-row-m" class="file-tree-row kind-file git-modified has-agent" style="padding-left: 92px">
            <span class="file-tree-icon file-icon-code">*</span>
            <span class="file-tree-name">10_core_utils.js</span>
            <span class="file-tree-dir-count" hidden></span>
            <span class="file-tree-agent"><span class="agent-icon codex">A</span></span>
            <span class="file-tree-diff"><span class="changes-diff-add">+7</span><span class="changes-diff-remove">-4</span></span>
            <span class="file-tree-git-status">M</span>
            <span class="file-tree-date">20 min ago</span>
          </div>
          <div id="status-row-t" class="file-tree-row kind-file git-transcript has-agent" style="padding-left: 92px">
            <span class="file-tree-icon file-icon-code">*</span>
            <span class="file-tree-name">50_editor_settings_runtime.js</span>
            <span class="file-tree-dir-count" hidden></span>
            <span class="file-tree-agent"><span class="agent-icon codex">A</span></span>
            <span class="file-tree-diff" hidden></span>
            <span class="file-tree-git-status">T</span>
            <span class="file-tree-date">2.5 hrs ago</span>
          </div>
          <div id="status-row-q" class="file-tree-row kind-file git-untracked" style="padding-left: 52px">
            <span class="file-tree-icon file-icon-image">*</span>
            <span class="file-tree-name">20260605-026.png</span>
            <span class="file-tree-dir-count" hidden></span>
            <span class="file-tree-agent" hidden></span>
            <span class="file-tree-diff" hidden></span>
            <span class="file-tree-git-status">?</span>
            <span class="file-tree-date">1 hr ago</span>
          </div>
        </div>
        <div class="file-explorer-tree narrow-file-explorer-tree">
          <div id="status-row-long" class="file-tree-row kind-file git-modified has-agent" style="padding-left: 92px">
            <span class="file-tree-icon file-icon-doc">*</span>
            <span class="file-tree-name">TOOLCALLING_STREAMING_CASES.md</span>
            <span class="file-tree-dir-count" hidden></span>
            <span class="file-tree-agent"><span class="agent-icon codex">A</span></span>
            <span class="file-tree-diff"><span class="changes-diff-add">+66</span></span>
            <span class="file-tree-git-status">M</span>
            <span class="file-tree-date">Jun 6, 21:44</span>
          </div>
        </div>
      </body>
    </html>
    """


def codemirror_scrollbar_overview_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <style>
          body {{ margin: 0; padding: 12px; background: #11151d; }}
          #host {{ position: relative; width: 420px; height: 220px; }}
          #host .cm-editor {{ height: 100%; }}
          #host .cm-scroller {{ position: absolute; inset: 0; overflow: scroll; }}
          #host .cm-content {{ width: 900px; height: 900px; }}
        </style>
      </head>
      <body class="editor-theme-light">
        <div id="host" class="file-editor-codemirror-panel file-editor-diff-codemirror">
          <div class="cm-editor">
            <div id="scroller" class="cm-scroller">
              <div id="merge-revert" class="cm-merge-revert"><button type="button">↔</button></div>
              <div class="cm-gutters">
                <div id="changed-gutter" class="cm-changedLineGutter">12</div>
                <div id="deleted-gutter" class="cm-deletedLineGutter">13</div>
              </div>
              <div class="cm-content"></div>
            </div>
          </div>
          <div id="overview" class="cm-diff-overview" style="background: linear-gradient(to bottom, #ff5d6c 0.000% 40.000%, #38d878 40.000% 80.000%, transparent 80.000% 100.000%)"></div>
        </div>
      </body>
    </html>
    """


def split_seam_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <style>
          body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; }}
          .layout-root {{ width: 420px; height: 360px; }}
        </style>
      </head>
      <body>
        <div class="layout-root split-column">
          <section class="layout-column"><article id="top-panel" class="panel"></article></section>
          <div id="split-resizer" class="layout-resizer resizer-column"></div>
          <section class="layout-column"><article id="bottom-panel" class="panel"></article></section>
        </div>
      </body>
    </html>
    """


def live_runtime_boot_fixture_html(settings=None, transcript_current_path="/home/test/yolomux.dev", transcript_git_root="/home/test/yolomux.dev", session_files_payload=None, fs_entries=None, sessions=None, transcript_sessions=None, session_files_payloads=None):
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    script_uri = (REPO_ROOT / "static" / "yolomux.js").as_uri()
    settings = settings or {}
    sessions = sessions or ["1"]
    session_files_payload = session_files_payload or {"session": sessions[0], "files": [], "repos": [], "errors": [], "loaded": True}
    fs_entries = fs_entries or {}
    bootstrap = {
        "sessions": sessions,
        "availableAgents": ["term"],
        "accessRole": "admin",
        "homePath": "/home/test",
        "repoRoot": "/home/test/yolomux.dev",
        "maxSessionTabs": 9,
        "serverHostname": "localhost",
        "version": "test",
        "versionCommitTime": "test",
        "settingsPayload": {
            "settings": settings,
            "defaults": {"appearance": {"inactive_pane_opacity": 60}},
            "mtime_ns": 0,
        },
        "yoloRulesPayload": {
            "path": "/home/test/.config/yolomux/yolo-rules.yaml",
            "source": "default",
            "rules": [],
            "errors": [],
        },
        "codeMirrorAssetUrl": (REPO_ROOT / "static" / "codemirror.js").as_uri(),
        # DOIT.8: the real page inlines the active locale catalog so t() resolves on the first render
        # (the menu bar paints at boot). Mirror that here so the live-boot menu shows real labels.
        "locale": "en",
        "strings": {"en": json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text())},
    }
    stub_script = """
      window.__bootErrors = [];
      window.__bootRejections = [];
      window.__bootFetches = [];
      window.__bootSockets = [];
      window.__terminalOpened = 0;
      window.__settingsMtime = 0;
      window.__settingsPayload = JSON.parse(document.getElementById('yolomux-bootstrap').textContent).settingsPayload;
      window.addEventListener('error', event => window.__bootErrors.push(event.message || String(event.error || event)));
      window.addEventListener('unhandledrejection', event => window.__bootRejections.push(String(event.reason || event)));
      window.marked = {parse(text) { return String(text || ''); }};
      window.hljs = {highlightAuto(text) { return {value: String(text || '')}; }, highlightElement() {}};
      window.Notification = {permission: 'denied', requestPermission: async () => 'denied'};
      class FakeTerminal {
        constructor(options = {}) {
          this.cols = options.cols || 80;
          this.rows = options.rows || 24;
          this.options = options;
          this.buffer = {active: {length: 0, getLine() { return null; }}};
        }
        open(container) {
          this.element = document.createElement('div');
          this.element.className = 'xterm';
          this.element.textContent = 'fake terminal';
          container.appendChild(this.element);
          window.__terminalOpened += 1;
        }
        resize(cols, rows) { this.cols = cols; this.rows = rows; }
        refresh() {}
        write(data) { this.lastWrite = data; }
        onData(callback) { this._onData = callback; return {dispose() {}}; }
        onFocus(callback) { this._onFocus = callback; return {dispose() {}}; }
        onBlur(callback) { this._onBlur = callback; return {dispose() {}}; }
        focus() { if (this._onFocus) this._onFocus(); }
        scrollLines() {}
        registerLinkProvider() { return {dispose() {}}; }
        attachCustomKeyEventHandler() {}
        getSelection() { return ''; }
        clearSelection() {}
        dispose() { this.disposed = true; }
      }
      window.Terminal = FakeTerminal;
      class FakeWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor(url) {
          this.url = String(url);
          this.readyState = FakeWebSocket.CONNECTING;
          this.sent = [];
          window.__bootSockets.push(this.url);
          setTimeout(() => {
            this.readyState = FakeWebSocket.OPEN;
            if (this.onopen) this.onopen({type: 'open'});
          }, 0);
        }
        send(message) { this.sent.push(String(message)); }
        close() {
          this.readyState = FakeWebSocket.CLOSED;
          if (this.onclose) this.onclose({type: 'close'});
        }
      }
      window.WebSocket = FakeWebSocket;
      window.EventSource = class {
        constructor(url) { this.url = String(url); this.listeners = {}; }
        addEventListener(name, callback) { this.listeners[name] = callback; }
        close() { this.closed = true; }
      };
      function jsonResponse(payload, status = 200) {
        return Promise.resolve(new Response(JSON.stringify(payload), {
          status,
          headers: {'Content-Type': 'application/json'},
        }));
      }
      function mergeSettings(base, patch) {
        const result = Array.isArray(base) ? base.slice() : {...(base || {})};
        if (!patch || typeof patch !== 'object' || Array.isArray(patch)) return result;
        for (const [key, value] of Object.entries(patch)) {
          if (value && typeof value === 'object' && !Array.isArray(value) && result[key] && typeof result[key] === 'object' && !Array.isArray(result[key])) {
            result[key] = mergeSettings(result[key], value);
          } else {
            result[key] = Array.isArray(value) ? value.slice() : value;
          }
        }
        return result;
      }
      window.fetch = async (input, options = {}) => {
        const url = new URL(String(input), 'https://localhost');
        window.__bootFetches.push({path: url.pathname, method: options.method || 'GET'});
        if (url.pathname === '/api/settings') {
          if ((options.method || 'GET') === 'POST') {
            const body = JSON.parse(options.body || '{}');
            window.__settingsPayload.settings = mergeSettings(window.__settingsPayload.settings || {}, body.settings || body);
            window.__settingsPayload.mtime_ns = ++window.__settingsMtime;
          }
          return jsonResponse(window.__settingsPayload);
        }
        if (url.pathname === '/api/notify') return jsonResponse({enabled: false});
        if (url.pathname === '/api/ensure-session') return jsonResponse({ok: true, created: false});
        if (url.pathname === '/api/auto-approve') {
          return jsonResponse({
            session_order: window.__fixtureSessions,
            sessions: Object.fromEntries(window.__fixtureSessions.map(session => [session, {target: session, enabled: false, last_action: 'off'}])),
            rules: {path: '/home/test/.config/yolomux/yolo-rules.yaml', source: 'default', rules: [], errors: []},
          });
        }
        if (url.pathname === '/api/transcripts') {
          const transcriptSessions = window.__fixtureTranscriptSessions || {};
          const currentPath = window.__fixtureTranscriptCurrentPath || '/home/test/yolomux.dev';
          const gitRoot = window.__fixtureTranscriptGitRoot || '/home/test/yolomux.dev';
          return jsonResponse({
            session_order: window.__fixtureSessions,
            sessions: Object.fromEntries(window.__fixtureSessions.map(session => {
              const info = transcriptSessions[session] || {};
              return [session, {
                session,
                selected_pane: {current_path: info.current_path || currentPath},
                project: {git: {root: info.git_root || gitRoot, branch: info.branch || 'main'}},
                agents: [],
              }];
            })),
          });
        }
        if (url.pathname === '/api/activity-summary') return jsonResponse({sessions: {}, global: {lines: []}, session_order: window.__fixtureSessions});
        if (url.pathname === '/api/session-files') {
          const session = url.searchParams.get('session') || window.__fixtureSessions[0] || '1';
          return jsonResponse((window.__fixtureSessionFilesPayloads || {})[session] || window.__fixtureSessionFilesPayload || {session, files: [], repos: [], errors: [], loaded: true});
        }
        if (url.pathname === '/api/ping') return jsonResponse({ok: true});
        if (url.pathname === '/api/event') return jsonResponse({ok: true});
        if (url.pathname === '/api/events') return jsonResponse({events: []});
        if (url.pathname === '/api/fs/list') {
          const path = url.searchParams.get('path') || '/home/test';
          const entries = (window.__fixtureFsEntries || {})[path] || [];
          return jsonResponse({path, entries});
        }
        return jsonResponse({});
      };
    """
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <style>
          body {{ margin: 0; }}
          #grid {{ width: 1000px; height: 620px; }}
          .terminal {{ width: 720px; height: 360px; }}
        </style>
      </head>
      <body>
        <header class="topbar">
          <div class="brand-cell"><div class="brand title">YOLOmux</div><span id="httpsWarning" class="transport-warning" hidden></span></div>
          <div id="sessionButtons" class="app-menu-area" aria-label="Application menus"></div>
          <div class="actions">
            <div id="latencyMeter" class="latency-meter"><svg class="latency-graph" viewBox="0 0 44 18"><polyline id="latencyLine" class="latency-line" points=""></polyline></svg><span id="latencyNumber" class="latency-number">-- ms</span></div>
            <button id="notifyToggle">Notify</button>
            <button id="refreshMeta">Refresh</button>
            <button id="logoutButton">Log out</button>
            <span id="status" class="sub">starting</span>
          </div>
        </header>
        <div id="attentionAlerts" class="attention-alerts" aria-live="polite"></div>
        <aside id="fileExplorer" class="file-explorer" hidden aria-label="File Explorer">
          <div class="file-explorer-tree-col">
            <div class="file-explorer-head">
              <button type="button" class="file-explorer-header-action" data-file-explorer-collapse>▤</button>
              <button type="button" id="fileExplorerHiddenToggle" class="file-explorer-hidden-toggle">.*</button>
              <button type="button" id="fileExplorerRootMode" class="file-explorer-root-mode-toggle active" aria-pressed="true">Sync</button>
              <div id="fileExplorerQuickAccess" class="file-explorer-quick-access"></div>
              <input class="file-explorer-path" id="fileExplorerPath" type="text" value="/">
              <button type="button" id="fileExplorerPathCopy" class="path-copy-button file-explorer-path-copy"></button>
              <button type="button" id="fileExplorerClose" class="file-explorer-close"></button>
            </div>
            <div class="file-explorer-tree" id="fileExplorerTree" role="tree" tabindex="0"></div>
          </div>
        </aside>
        <main id="grid" class="grid"></main>
        <div id="panelPool" class="panel-pool" aria-hidden="true"></div>
        <section id="modal" class="modal"><div class="modal-head"><div id="modalTitle">Transcript</div><button id="closeModal">Close</button></div><pre id="modalBody"></pre></section>
        <script id="yolomux-bootstrap" type="application/json">{json.dumps(bootstrap, separators=(",", ":"))}</script>
        <script>
          window.__fixtureSessions = {json.dumps(sessions)};
          window.__fixtureTranscriptSessions = {json.dumps(transcript_sessions or {}, separators=(",", ":"))};
          window.__fixtureTranscriptCurrentPath = {json.dumps(transcript_current_path)};
          window.__fixtureTranscriptGitRoot = {json.dumps(transcript_git_root)};
          window.__fixtureSessionFilesPayload = {json.dumps(session_files_payload, separators=(",", ":"))};
          window.__fixtureSessionFilesPayloads = {json.dumps(session_files_payloads or {}, separators=(",", ":"))};
          window.__fixtureFsEntries = {json.dumps(fs_entries, separators=(",", ":"))};
        </script>
        <script>{stub_script}</script>
        <script src="{script_uri}"></script>
      </body>
    </html>
    """


def load_fixture(browser, tmp_path, width):
    page = tmp_path / f"pane-{width}.html"
    page.write_text(pane_fixture_html(width), encoding="utf-8")
    browser.get(page.as_uri())
    return browser.execute_script(
        """
        const panel = document.querySelector('.panel').getBoundingClientRect();
        const toolbar = document.querySelector('.tabs').getBoundingClientRect();
        const toolbarButton = document.querySelector('.tabs .tab').getBoundingClientRect();
        const panelHead = document.querySelector('.panel-head').getBoundingClientRect();
        const firstPaneTab = document.querySelector('.pane-tab').getBoundingClientRect();
        const detailRow = document.querySelector('.panel-detail-row').getBoundingClientRect();
        const detailClose = document.querySelector('.panel-detail-close').getBoundingClientRect();
        const detailStyle = window.getComputedStyle(document.querySelector('.panel-detail-row'));
        document.body.classList.add('tab-meta-hidden');
        const hiddenTextDisplay = window.getComputedStyle(document.querySelector('.pane-tab .session-button-text')).display;
        const hiddenSymbolDisplay = window.getComputedStyle(document.querySelector('.pane-tab .tab-symbol')).display;
        const tabs = Array.from(document.querySelectorAll('.pane-tab')).map(tab => {
          const rect = tab.getBoundingClientRect();
          return {top: Math.round(rect.top), bottom: rect.bottom, left: rect.left, right: rect.right, width: rect.width};
        });
        const rows = [];
        for (const tab of tabs) {
          let row = rows.find(item => item.top === tab.top);
          if (!row) {
            row = {top: tab.top, count: 0, rights: []};
            rows.push(row);
          }
          row.count += 1;
          row.rights.push(tab.right);
        }
        return {
          panel: {left: panel.left, right: panel.right, width: panel.width},
          panelHead: {top: panelHead.top, bottom: panelHead.bottom, height: panelHead.height},
          toolbar: {left: toolbar.left, right: toolbar.right, width: toolbar.width},
          toolbarCenterDelta: Math.abs((toolbarButton.top + toolbarButton.height / 2) - (firstPaneTab.top + firstPaneTab.height / 2)),
          tabHeadBottomGap: Math.round(detailRow.top - Math.max(...tabs.map(tab => tab.bottom))),
          detailBg: detailStyle.backgroundColor,
          detailCloseRightGap: Math.round(panel.right - detailClose.right),
          detailRow: {left: detailRow.left, right: detailRow.right, height: detailRow.height},
          hiddenTextDisplay,
          hiddenSymbolDisplay,
          rows,
          tabs,
        };
        """
    )


def load_pc_controls_fixture(browser, tmp_path):
    page = tmp_path / "pc-controls.html"
    page.write_text(pc_controls_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_topbar_font_fixture(browser, tmp_path):
    page = tmp_path / "topbar-font.html"
    page.write_text(topbar_font_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_editor_diff_ref_toolbar_fixture(browser, tmp_path):
    page = tmp_path / "editor-diff-ref-toolbar.html"
    page.write_text(editor_diff_ref_toolbar_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_codemirror_wrap_toggle_fixture(browser, tmp_path):
    page = tmp_path / "cm-wrap-toggle.html"
    page.write_text(codemirror_wrap_toggle_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_menu_fixture(browser, tmp_path):
    page = tmp_path / "menu.html"
    page.write_text(menu_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())
    return browser.execute_script(
        """
        const rows = Array.from(document.querySelectorAll('.app-menu-tab-command')).map(row => {
          const rect = row.getBoundingClientRect();
          return {top: rect.top, bottom: rect.bottom, height: rect.height};
        });
        const popover = document.querySelector('.app-menu-popover');
        const popoverStyle = window.getComputedStyle(popover);
        const secondRowStyle = window.getComputedStyle(document.querySelectorAll('.app-menu-tab-command')[1]);
        return {
          count: rows.length,
          maxHeight: Math.max(...rows.map(row => row.height)),
          maxStep: Math.max(...rows.slice(1).map((row, index) => row.top - rows[index].top)),
          firstTwentyFiveSpan: rows[24].bottom - rows[0].top,
          width: popover.getBoundingClientRect().width,
          maxInlineSize: Number.parseFloat(popoverStyle.maxWidth),
          devicePixelRatio: window.devicePixelRatio || 1,
          secondRowBorderTopColor: secondRowStyle.borderTopColor,
          scrollHeight: popover.scrollHeight,
        };
        """
    )


def load_editor_pane_legacy_body_fixture(browser, tmp_path):
    page = tmp_path / "editor-pane-legacy-body.html"
    page.write_text(editor_pane_ignores_legacy_body_class_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_codemirror_editor_controls_fixture(browser, tmp_path):
    page = tmp_path / "codemirror-editor-controls.html"
    page.write_text(codemirror_editor_controls_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_codemirror_bundle_fixture(browser, tmp_path):
    page = tmp_path / "codemirror-bundle.html"
    page.write_text(codemirror_bundle_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_codemirror_todo_diff_overview_fixture(browser, tmp_path):
    page = tmp_path / "codemirror-todo-diff-overview.html"
    page.write_text(codemirror_todo_diff_overview_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_codemirror_file_explorer_diff_overview_fixture(browser, tmp_path):
    page = tmp_path / "codemirror-file-explorer-diff-overview.html"
    page.write_text(codemirror_file_explorer_diff_overview_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_finder_click_toolbar_fixture(browser, tmp_path):
    page = tmp_path / "finder-click-toolbar.html"
    page.write_text(finder_click_toolbar_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def activate_finder_diff_fixture(browser):
    browser.execute_script(
        """
        document.body.classList.remove('file-explorer-mode-files');
        document.body.classList.add('file-explorer-mode-diff');
        const panel = document.getElementById('finder-panel');
        if (panel) panel.dataset.fileExplorerMode = 'diff';
        document.querySelectorAll('[data-file-explorer-mode-set]').forEach(toggle => {
          toggle.setAttribute('aria-pressed', toggle.dataset.fileExplorerModeSet === 'diff' ? 'true' : 'false');
        });
        """
    )


def load_file_tree_status_alignment_fixture(browser, tmp_path):
    page = tmp_path / "file-tree-status-alignment.html"
    page.write_text(file_tree_status_alignment_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_codemirror_scrollbar_overview_fixture(browser, tmp_path):
    page = tmp_path / "codemirror-scrollbar-overview.html"
    page.write_text(codemirror_scrollbar_overview_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_split_seam_fixture(browser, tmp_path):
    page = tmp_path / "split-seam.html"
    page.write_text(split_seam_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_live_runtime_boot_fixture(browser, tmp_path, search=""):
    page = tmp_path / "live-runtime-boot.html"
    page.write_text(live_runtime_boot_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri() + search)


def test_generated_app_boots_live_runtime_without_browser_errors(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return window.__terminalOpened >= 1 && document.querySelector('#panel-1 .terminal .xterm') !== null"
        )
    )
    metrics = browser.execute_script(
        """
        return {
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
          fetchPaths: window.__bootFetches.map(item => `${item.method} ${item.path}`),
          sockets: window.__bootSockets,
          menuLabels: Array.from(document.querySelectorAll('.app-menu-button')).map(button => button.textContent.trim()),
          panelCount: document.querySelectorAll('.panel').length,
          paneTabCount: document.querySelectorAll('.pane-tab').length,
          panelVisible: document.querySelector('#panel-1')?.isConnected === true,
          status: document.getElementById('status').textContent,
          terminalText: document.querySelector('#panel-1 .terminal .xterm')?.textContent || '',
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert "GET /api/notify" in metrics["fetchPaths"]
    assert "GET /api/auto-approve" in metrics["fetchPaths"]
    assert "POST /api/ensure-session" in metrics["fetchPaths"]
    assert "GET /api/transcripts" in metrics["fetchPaths"]
    assert "GET /api/ping" in metrics["fetchPaths"]
    assert any("/ws?session=1" in url for url in metrics["sockets"])
    assert {"File", "View", "tmux", "Tabs", "Help"}.issubset(set(metrics["menuLabels"]))
    assert metrics["panelCount"] >= 1
    assert metrics["paneTabCount"] >= 1
    assert metrics["panelVisible"]
    assert metrics["terminalText"] == "fake terminal"


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
    assert metrics["modeTexts"] == ["Finder", "ΔDiff"]
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
    page = tmp_path / "live-runtime-sync-common-root.html"
    page.write_text(
        live_runtime_boot_fixture_html(
            settings={"file_explorer": {"root_mode": "sync"}},
            transcript_current_path="/home/test/dynamo/repo-a/src",
            transcript_git_root="/home/test/dynamo/repo-a",
            session_files_payload=session_files_payload,
            fs_entries=fs_entries,
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri() + "?sessions=files,1&layout=row@35(slot1,left)&tabs=slot1:files;left:1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
              && document.getElementById('panel-1') !== null;
            """
        )
    )
    browser.find_element("css selector", "#panel-1").click()
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
          fetchedPaths: window.__bootFetches.filter(item => item.path === '/api/fs/list').length,
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
    assert metrics["fetchedPaths"] >= 3, metrics
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
    page = tmp_path / "live-runtime-sync-parent-collapse.html"
    page.write_text(
        live_runtime_boot_fixture_html(
            settings={"file_explorer": {"root_mode": "sync"}},
            transcript_current_path="/home/test/dynamo/repo-a/src",
            transcript_git_root="/home/test/dynamo/repo-a",
            session_files_payload=session_files_payload,
            fs_entries=fs_entries,
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri() + "?sessions=files,1&layout=row@35(slot1,left)&tabs=slot1:files;left:1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
              && document.getElementById('panel-1') !== null;
            """
        )
    )
    browser.find_element("css selector", "#panel-1").click()
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
        fileExplorerExplicitSyncSession = '';
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


def test_sync_mode_quick_access_does_not_snap_back_until_explicit_input(browser, tmp_path):
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
    page = tmp_path / "live-runtime-sync-quick-access-manual.html"
    page.write_text(
        live_runtime_boot_fixture_html(
            settings={"file_explorer": {"root_mode": "sync"}},
            sessions=["5"],
            transcript_sessions={
                "5": {"current_path": "/home/test/yolomux.dev/src", "git_root": "/home/test/yolomux.dev"},
            },
            session_files_payload=session_files_payload,
            session_files_payloads={"5": session_files_payload},
            fs_entries=fs_entries,
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri() + "?sessions=files,5&layout=row@35(slot1,left)&tabs=slot1:files;left:5")
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
          quickTexts: Array.from(new Set(Array.from(document.querySelectorAll('.file-explorer-quick-access-button')).map(button => button.textContent.trim()))),
        };
        """
    )
    assert sync_root_metrics == {
        "mode": "sync",
        "syncPressed": "true",
        "quickTexts": ["~", "/*", "/tmp"],
    }, sync_root_metrics
    browser.find_element("css selector", "#panel-5").click()
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.file-explorer-path-inline')?.value === '/home/test/yolomux.dev'"
        )
    )
    manual_metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        document.querySelector('.file-explorer-quick-access-button[data-quick-path="~"]').click();
        requestAnimationFrame(() => {
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
              homePressed: document.querySelector('.file-explorer-quick-access-button[data-quick-path="~"]')?.getAttribute('aria-pressed') || '',
              quickTexts: Array.from(new Set(Array.from(document.querySelectorAll('.file-explorer-quick-access-button')).map(button => button.textContent.trim()))),
            });
          }));
        });
        """
    )
    assert manual_metrics["root"] == "/home/test", manual_metrics
    assert manual_metrics["manual"] is True, manual_metrics
    assert manual_metrics["mode"] == "fixed", manual_metrics
    assert manual_metrics["explicitSession"] == "5", manual_metrics
    assert manual_metrics["planRoot"] == "/home/test/yolomux.dev", manual_metrics
    assert manual_metrics["syncPressed"] == "false", manual_metrics
    assert manual_metrics["homePressed"] == "true", manual_metrics
    assert manual_metrics["quickTexts"] == ["~", "/*", "/tmp"], manual_metrics
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
            homePressed: document.querySelector('.file-explorer-quick-access-button[data-quick-path="~"]')?.getAttribute('aria-pressed') || '',
          });
        }));
        """
    )
    assert unchanged_metrics == {
        "root": "/home/test",
        "mode": "fixed",
        "syncPressed": "false",
        "homePressed": "true",
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
              && document.querySelector('.file-explorer-quick-access-button[data-quick-path="~"]')?.getAttribute('aria-pressed') === 'false';
            """
        )
    )


def test_root_quick_access_star_alias_opens_slash(browser, tmp_path):
    page = tmp_path / "live-runtime-root-quick-access-star.html"
    page.write_text(
        live_runtime_boot_fixture_html(
            settings={"file_explorer": {"root_mode": "fixed", "quick_access_paths": ["~", "/*", "/tmp"]}},
            fs_entries={
                "/home/test": [{"name": "project", "kind": "dir"}],
                "/": [{"name": "tmp", "kind": "dir"}],
                "/tmp": [{"name": "scratch.txt", "kind": "file"}],
            },
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri() + "?sessions=files,1&layout=row@35(slot1,left)&tabs=slot1:files;left:1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
              const buttons = Array.from(document.querySelectorAll('.file-explorer-quick-access-button'));
            return buttons.map(button => button.textContent.trim()).join('|') === '~|/*|/tmp'
              && buttons.some(button => button.dataset.quickPath === '/*');
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        document.querySelector('.file-explorer-quick-access-button[data-quick-path="/*"]').click();
        const wait = () => {
          const root = document.querySelector('.file-explorer-path-inline')?.value || '';
          if (root === '/') {
            done({
              root,
              mode: fileExplorerRootModeValue(),
              starPressed: document.querySelector('.file-explorer-quick-access-button[data-quick-path="/*"]')?.getAttribute('aria-pressed') || '',
              texts: Array.from(new Set(Array.from(document.querySelectorAll('.file-explorer-quick-access-button')).map(button => button.textContent.trim()))),
              paths: Array.from(new Set(Array.from(document.querySelectorAll('.file-explorer-quick-access-button')).map(button => button.dataset.quickPath || ''))),
            });
            return;
          }
          requestAnimationFrame(wait);
        };
        requestAnimationFrame(wait);
        """
    )
    assert metrics == {
        "root": "/",
        "mode": "fixed",
        "starPressed": "true",
        "texts": ["~", "/*", "/tmp"],
        "paths": ["~", "/*", "/tmp"],
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
    page = tmp_path / "live-runtime-sync-hover-sticky.html"
    page.write_text(
        live_runtime_boot_fixture_html(
            settings={"file_explorer": {"root_mode": "sync"}},
            sessions=["5", "6"],
            transcript_sessions={
                "5": {"current_path": "/home/test/yolomux.dev/src", "git_root": "/home/test/yolomux.dev"},
                "6": {"current_path": "/home/test/other.dev/other", "git_root": "/home/test/other.dev"},
            },
            session_files_payload=session_files_payloads["5"],
            session_files_payloads=session_files_payloads,
            fs_entries=fs_entries,
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri() + "?sessions=files,5,6&layout=row@35(slot1,row@50(left,slot2))&tabs=slot1:files;left:5;slot2:6")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.file-explorer-path-inline')?.value === '/home/test'
              && document.getElementById('panel-5') !== null
              && document.getElementById('panel-6') !== null;
            """
        )
    )
    browser.find_element("css selector", "#panel-5").click()
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.file-explorer-path-inline')?.value === '/home/test/yolomux.dev'"
        )
    )
    expanded_before_switch = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
        const row = tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other"]');
        row.click();
        requestAnimationFrame(() => requestAnimationFrame(() => done({
          expanded: row.getAttribute('aria-expanded'),
          childVisible: tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other/touched.js"]') !== null,
          expandedSet: Array.from(fileExplorerExpanded),
        })));
        """
    )
    assert expanded_before_switch["expanded"] == "true", expanded_before_switch
    assert expanded_before_switch["childVisible"] is True, expanded_before_switch
    assert "/home/test/yolomux.dev/other" in expanded_before_switch["expandedSet"], expanded_before_switch
    ActionChains(browser).move_to_element(browser.find_element("css selector", "#panel-6")).perform()
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
            payloadSession: fileExplorerSessionFilesPayload?.session || '',
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
              payloadSession: fileExplorerSessionFilesPayload?.session || '',
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


def test_fixed_finder_reveals_clicked_editor_file_without_changing_root(browser, tmp_path):
    fs_entries = {
        "/home/test": [{"name": "repo-a", "kind": "dir"}, {"name": "repo-b", "kind": "dir"}],
        "/home/test/repo-a": [{"name": "src", "kind": "dir"}],
        "/home/test/repo-a/src": [{"name": "a.md", "kind": "file"}],
        "/home/test/repo-b": [{"name": "other", "kind": "dir"}],
        "/home/test/repo-b/other": [{"name": "b.md", "kind": "file"}],
    }
    page = tmp_path / "live-runtime-fixed-finder-hover-editor.html"
    page.write_text(
        live_runtime_boot_fixture_html(
            settings={"general": {"auto_focus": True}, "file_explorer": {"root_mode": "fixed"}},
            sessions=[],
            fs_entries=fs_entries,
        ),
        encoding="utf-8",
    )
    item_a = "file:/home/test/repo-a/src/a.md"
    item_b = "file:/home/test/repo-b/other/b.md"
    browser.get(
        page.as_uri()
        + "?sessions=files"
        + "&layout=row@35(slot1,row@50(left,slot2))"
        + f"&tabs=slot1:files;left:{item_a};slot2:{item_b}"
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
    browser.find_element("css selector", '.file-editor-panel[data-file-path="/home/test/repo-a/src/a.md"]').click()
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
    ActionChains(browser).move_to_element(browser.find_element("css selector", '.file-editor-panel[data-file-path="/home/test/repo-b/other/b.md"]')).perform()
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
    browser.find_element("css selector", '.file-editor-panel[data-file-path="/home/test/repo-b/other/b.md"]').click()
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
    page = tmp_path / "live-runtime-sync-empty-session-home.html"
    page.write_text(
        live_runtime_boot_fixture_html(
            settings={"file_explorer": {"root_mode": "sync"}},
            transcript_current_path="",
            session_files_payload=session_files_payload,
            fs_entries=fs_entries,
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri() + "?layout=left&tabs=left:files")
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
          fetchedPaths: window.__bootFetches.filter(item => item.path === '/api/fs/list').map(item => item.path),
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["root"] == "/home/test", metrics
    assert "/home/test/stale" in {row["path"] for row in metrics["rows"]}, metrics
    assert not any(row["hasRepo"] or row["hasTouched"] for row in metrics["rows"]), metrics


def test_preferences_scroll_defers_passive_rerender(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof selectSession === 'function' && window.__terminalOpened >= 1")
    )
    opened = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        selectSession('__prefs__').then(
          () => requestAnimationFrame(() => done({ok: true})),
          error => done({ok: false, error: String(error)})
        );
        """
    )
    assert opened["ok"], opened
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.querySelector('.preferences-scroll') !== null")
    )
    metrics = browser.execute_script(
        """
        const scroller = document.querySelector('.preferences-scroll');
        scroller.scrollTop = 60;
        scroller.dispatchEvent(new WheelEvent('wheel', {deltaY: 120, bubbles: true}));
        renderPreferencesPanels();
        const afterPassive = document.querySelector('.preferences-scroll');
        renderPreferencesPanels({force: true});
        const afterForced = document.querySelector('.preferences-scroll');
        return {
          passiveKeptScroller: afterPassive === scroller,
          forcedReplacedScroller: afterForced !== afterPassive,
          scrollTop: afterPassive.scrollTop,
          bodyHtml: document.querySelector('.preferences-body')?.innerHTML || '',
        };
        """
    )
    assert metrics["passiveKeptScroller"], metrics
    assert metrics["forcedReplacedScroller"], metrics
    assert "preferences-sections" in metrics["bodyHtml"]


def test_active_pane_ring_opacity_follows_preference(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof applySettingsPayload === 'function' && document.querySelector('#panel-1') !== null"
        )
    )
    metrics = browser.execute_script(
        """
        const panel = document.querySelector('#panel-1');
        panel.classList.add('active-pane');
        const applyOpacity = value => {
          applySettingsPayload({settings: {appearance: {pane_ring_opacity: value}}, defaults: {}, mtime_ns: value}, {force: true});
          const rootStyle = getComputedStyle(document.documentElement);
          const panelStyle = getComputedStyle(panel);
          return {
            activeOpacity: rootStyle.getPropertyValue('--pane-active-ring-opacity').trim(),
            normalOpacity: rootStyle.getPropertyValue('--pane-ring-opacity').trim(),
            borderColor: panelStyle.borderLeftColor,
          };
        };
        return {low: applyOpacity(5), defaultish: applyOpacity(75)};
        """
    )
    assert metrics["low"]["activeOpacity"] == "5%", metrics
    assert metrics["low"]["normalOpacity"] == "5%", metrics
    assert metrics["defaultish"]["activeOpacity"] == "75%", metrics
    assert metrics["low"]["borderColor"] != metrics["defaultish"]["borderColor"], metrics


def test_active_color_radios_recolor_live_pane_chrome(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, "?sessions=__files__,1,__prefs__&layout=row@32(slot1,row@56(left,right))&tabs=slot1:__files__;left:1;right:__prefs__")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('input[type="radio"][data-setting-path="appearance.active_color"][value="blue"]') !== null
              && document.querySelector('#panel-1 .pane-tab.active') !== null
              && document.querySelector('#panel-__files__ .file-explorer-mode-toggle[aria-pressed="true"]') !== null
              && document.querySelector('input[data-setting-path="appearance.inactive_pane_opacity"]') !== null
              && document.querySelector('input[data-setting-path="appearance.pane_ring_opacity"]') !== null
            """
        )
    )
    browser.execute_script(
        """
        const panel = document.querySelector('#panel-1');
        panel.classList.add('active-pane', 'focused-pane', 'typing-ready-pane', 'yolo-ready-pane');
        document.getElementById('tabMetaToggle')?.classList.add('active');
        const notify = document.getElementById('notifyToggle');
        notify?.classList.add('notify-toggle', 'active');
        const radio = document.querySelector('input[type="radio"][data-setting-path="appearance.active_color"][value="blue"]');
        radio.checked = true;
        radio.dispatchEvent(new Event('change', {bubbles: true}));
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return window.__settingsPayload?.settings?.appearance?.active_color === 'blue'
              && getComputedStyle(document.querySelector('#panel-1 .pane-tab.active')).backgroundColor === 'rgb(59, 130, 246)';
            """
        )
    )
    metrics = browser.execute_script(
        """
        const rootStyle = getComputedStyle(document.documentElement);
        const bodyStyle = getComputedStyle(document.body);
        const tabStyle = getComputedStyle(document.querySelector('#panel-1 .pane-tab.active'));
        const panelStyle = getComputedStyle(document.querySelector('#panel-1'));
        const prefsRange = document.querySelector('input[data-setting-path="appearance.inactive_pane_opacity"]');
        const ringRange = document.querySelector('input[data-setting-path="appearance.pane_ring_opacity"]');
        const radio = document.querySelector('input[data-setting-path="appearance.date_time_hour_cycle"]');
        const prefsScroll = document.querySelector('.preferences-scroll');
        const finderMode = document.querySelector('#panel-__files__ .file-explorer-mode-toggle[aria-pressed="true"]');
        const tabMeta = document.getElementById('tabMetaToggle');
        const notify = document.getElementById('notifyToggle');
        const mdProbe = document.createElement('div');
        mdProbe.className = 'markdown-body';
        mdProbe.innerHTML = '<h1>Probe</h1>';
        document.body.appendChild(mdProbe);
        const cmProbe = document.createElement('div');
        cmProbe.className = 'cm-content';
        cmProbe.innerHTML = '<span class="md-heading"># Probe</span>';
        document.body.appendChild(cmProbe);
        const yoloProbe = document.createElement('span');
        yoloProbe.className = 'session-yolo-marker active';
        yoloProbe.textContent = 'YO';
        document.body.appendChild(yoloProbe);
        const shortcutProbe = document.createElement('section');
        shortcutProbe.className = 'keyboard-shortcuts-section';
        shortcutProbe.innerHTML = '<h3>APP</h3>';
        document.body.appendChild(shortcutProbe);
        const activeSwatch = document.querySelector('input[type="radio"][data-setting-path="appearance.active_color"][value="blue"]').closest('.preferences-radio').querySelector('.preferences-radio-swatch');
        const activeSwatchLabel = activeSwatch.closest('.preferences-radio');
        const scrollProbe = document.createElement('div');
        scrollProbe.style.background = 'var(--active-control-scrollbar-thumb)';
        document.body.appendChild(scrollProbe);
        const expectedScrollThumb = getComputedStyle(scrollProbe).backgroundColor;
        scrollProbe.style.background = 'var(--pane-scrollbar-thumb)';
        const expectedNeutralScrollThumb = getComputedStyle(scrollProbe).backgroundColor;
        scrollProbe.remove();
        const metrics = {
          markdownHeadingColor: getComputedStyle(mdProbe.querySelector('h1')).color,
          cmHeadingColor: getComputedStyle(cmProbe.querySelector('.md-heading')).color,
          yoloBg: getComputedStyle(yoloProbe).backgroundColor,
          yoloBorder: getComputedStyle(yoloProbe).borderTopColor,
          shortcutHeadingColor: getComputedStyle(shortcutProbe.querySelector('h3')).color,
          swatchDisplay: getComputedStyle(activeSwatchLabel).display,
          swatchRadius: getComputedStyle(activeSwatch).borderRadius,
        };
        mdProbe.remove();
        cmProbe.remove();
        yoloProbe.remove();
        shortcutProbe.remove();
        return {
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
          rootAccent: rootStyle.getPropertyValue('--active-accent').trim(),
          bodyAccent: bodyStyle.getPropertyValue('--active-accent').trim(),
          rootRgb: rootStyle.getPropertyValue('--active-accent-rgb').trim(),
          tabBg: tabStyle.backgroundColor,
          tabBorder: tabStyle.borderTopColor,
          panelBorder: panelStyle.borderTopColor,
          prefsRangeAccent: getComputedStyle(prefsRange).accentColor,
          ringRangeAccent: getComputedStyle(ringRange).accentColor,
          radioAccent: getComputedStyle(radio).accentColor,
          prefsScrollColor: getComputedStyle(prefsScroll).scrollbarColor,
          prefsScrollThumb: getComputedStyle(prefsScroll, '::-webkit-scrollbar-thumb').backgroundColor,
          expectedScrollThumb,
          expectedNeutralScrollThumb,
          finderModeBg: getComputedStyle(finderMode).backgroundColor,
          finderModeBorder: getComputedStyle(finderMode).borderTopColor,
          tabMetaBg: getComputedStyle(tabMeta).backgroundColor,
          tabMetaBorder: getComputedStyle(tabMeta).borderTopColor,
          notifyBg: getComputedStyle(notify).backgroundColor,
          markdownHeadingColor: metrics.markdownHeadingColor,
          cmHeadingColor: metrics.cmHeadingColor,
          yoloBg: metrics.yoloBg,
          yoloBorder: metrics.yoloBorder,
          shortcutHeadingColor: metrics.shortcutHeadingColor,
          swatchDisplay: metrics.swatchDisplay,
          swatchRadius: metrics.swatchRadius,
          settingsPosts: window.__bootFetches.filter(item => item.method === 'POST' && item.path === '/api/settings').length,
        };
        """
    )
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["rootAccent"] == "#3b82f6", metrics
    assert metrics["bodyAccent"] == "#3b82f6", metrics
    assert metrics["rootRgb"] == "59 130 246", metrics
    assert metrics["tabBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["tabBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["panelBorder"].startswith("color(srgb 0.231373 0.509804 0.964706 / 0.75)"), metrics
    assert metrics["prefsRangeAccent"] == "rgb(59, 130, 246)", metrics
    assert metrics["ringRangeAccent"] == "rgb(59, 130, 246)", metrics
    assert metrics["radioAccent"] == "rgb(59, 130, 246)", metrics
    assert metrics["expectedScrollThumb"] == "rgba(59, 130, 246, 0.72)", metrics
    assert metrics["prefsScrollColor"].startswith(metrics["expectedNeutralScrollThumb"]), metrics
    assert metrics["prefsScrollThumb"] == metrics["expectedNeutralScrollThumb"], metrics
    assert metrics["finderModeBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["finderModeBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["tabMetaBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["tabMetaBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["notifyBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["markdownHeadingColor"] == "rgb(59, 130, 246)", metrics
    assert metrics["cmHeadingColor"] == "rgb(59, 130, 246)", metrics
    assert metrics["yoloBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["yoloBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["shortcutHeadingColor"] == "rgb(59, 130, 246)", metrics
    assert metrics["swatchDisplay"] == "grid", metrics
    assert metrics["swatchRadius"] == "2px 0px 0px 2px", metrics
    assert metrics["settingsPosts"] >= 1, metrics
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".preferences-scroll")).perform()
    WebDriverWait(browser, 2).until(
        lambda driver: driver.execute_script(
            "return getComputedStyle(document.querySelector('.preferences-scroll'), '::-webkit-scrollbar-thumb').backgroundColor"
        ) == metrics["expectedScrollThumb"]
    )
    ActionChains(browser).move_to_element(browser.find_element("css selector", "#panel-1")).perform()
    WebDriverWait(browser, 2).until(
        lambda driver: driver.execute_script(
            "return getComputedStyle(document.querySelector('.preferences-scroll'), '::-webkit-scrollbar-thumb').backgroundColor"
        ) == metrics["expectedNeutralScrollThumb"]
    )


def test_info_and_preferences_scrollbars_inherit_shared_hover_state(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=__info__,__prefs__,1&layout=row@34(left,row@50(mid,right))&tabs=left:__info__;mid:__prefs__;right:1",
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.info-list') !== null && document.querySelector('.preferences-scroll') !== null"
        )
    )
    metrics = browser.execute_script(
        """
        const info = document.querySelector('.info-list');
        const prefs = document.querySelector('.preferences-scroll');
        info.insertAdjacentHTML('beforeend', '<div style="height: 900px"></div>');
        prefs.insertAdjacentHTML('beforeend', '<div style="height: 900px"></div>');
        const probe = document.createElement('div');
        probe.style.background = 'var(--pane-scrollbar-thumb)';
        document.body.appendChild(probe);
        const neutral = getComputedStyle(probe).backgroundColor;
        probe.style.background = 'var(--active-control-scrollbar-thumb)';
        const accent = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return {
          neutral,
          accent,
          infoOverflow: info.scrollHeight > info.clientHeight,
          prefsOverflow: prefs.scrollHeight > prefs.clientHeight,
          infoThumb: getComputedStyle(info, '::-webkit-scrollbar-thumb').backgroundColor,
          prefsThumb: getComputedStyle(prefs, '::-webkit-scrollbar-thumb').backgroundColor,
        };
        """
    )
    assert metrics["infoOverflow"], metrics
    assert metrics["prefsOverflow"], metrics
    assert metrics["infoThumb"] == metrics["neutral"], metrics
    assert metrics["prefsThumb"] == metrics["neutral"], metrics

    def thumb(selector):
        return browser.execute_script(
            "return getComputedStyle(document.querySelector(arguments[0]), '::-webkit-scrollbar-thumb').backgroundColor",
            selector,
        )

    def wait_thumb(selector, expected):
        WebDriverWait(browser, 2).until(lambda _driver: thumb(selector) == expected)

    ActionChains(browser).move_to_element(browser.find_element("css selector", ".info-list")).perform()
    wait_thumb(".info-list", metrics["accent"])
    wait_thumb(".preferences-scroll", metrics["neutral"])

    ActionChains(browser).move_to_element(browser.find_element("css selector", ".preferences-scroll")).perform()
    wait_thumb(".preferences-scroll", metrics["accent"])
    wait_thumb(".info-list", metrics["neutral"])

    ActionChains(browser).move_to_element(browser.find_element("css selector", ".topbar")).perform()
    wait_thumb(".info-list", metrics["neutral"])
    wait_thumb(".preferences-scroll", metrics["neutral"])


@pytest.mark.parametrize("width, expected_rows", [(860, [3, 3]), (493, [1, 2, 2, 1])])
def test_pane_tabs_stay_within_panel(browser, tmp_path, width, expected_rows):
    # Tabs wrap to fit the panel at any width: the toolbar never overflows the panel, the rows wrap to the
    # expected counts, every tab stays within the panel's right edge, and the toolbar stays centered with no
    # gap below the tab head. (Was two near-identical width tests, at 860 and 493.)
    metrics = load_fixture(browser, tmp_path, width)
    assert metrics["toolbar"]["right"] <= metrics["panel"]["right"]
    assert [row["count"] for row in metrics["rows"]] == expected_rows
    assert all(tab["right"] <= metrics["panel"]["right"] for tab in metrics["tabs"])
    assert metrics["toolbarCenterDelta"] <= 2
    assert metrics["tabHeadBottomGap"] <= 2


def test_pane_tab_wide_layout_shows_compact_detail_row(browser, tmp_path):
    # At a comfortable width the first tab row shares the toolbar's row (sits left of it), lower rows stay
    # within the panel, and the detail row is a single compact strip (text shown, symbol hidden, tinted bg).
    metrics = load_fixture(browser, tmp_path, 860)
    first_row = metrics["rows"][0]
    assert max(first_row["rights"]) < metrics["toolbar"]["left"]
    lower_row_rights = [right for row in metrics["rows"][1:] for right in row["rights"]]
    assert max(lower_row_rights) <= metrics["panel"]["right"]
    assert metrics["detailRow"]["height"] <= 20
    assert metrics["hiddenTextDisplay"] != "none"
    assert metrics["hiddenSymbolDisplay"] == "none"
    assert metrics["detailBg"] != "rgb(18, 24, 35)"
    assert metrics["detailCloseRightGap"] <= 3


def test_pane_tab_active_accent_theming(browser, tmp_path):
    # The active pane tab + the pressed control tab share one --active-accent source (asserted as
    # relationships, not pinned greens, so the appearance.active_color picker can't break it); unpressed
    # controls share one neutral bg; theme-specific surfaces repaint on a theme switch while everything else
    # stays token-equal; and inactive-tab dir text always contrasts with its bg (no white-on-white).
    load_fixture(browser, tmp_path, 860)
    theme_metrics = browser.execute_script(
        """
        const originalPanel = document.querySelector('.panel.active-pane');
        const inactivePanel = originalPanel.cloneNode(true);
        inactivePanel.classList.remove('active-pane');
        inactivePanel.style.marginTop = '12px';
        document.body.appendChild(inactivePanel);
        const readMetrics = () => {
          const panel = document.querySelector('.panel.active-pane');
          const activeTab = panel.querySelector('.pane-tab.active');
          const inactiveActiveTab = inactivePanel.querySelector('.pane-tab.active');
          const inactiveTab = panel.querySelector('.pane-tab:not(.active)');
          const panelHead = panel.querySelector('.panel-head');
          const toolbarActive = panel.querySelector('.panel-head .tab.active:not(.auto-toggle)');
          const paneControl = panel.querySelector('.tabs .pane-minimize');
          const zoomControl = panel.querySelector('.tabs .pc-zoom');
          return {
            panelBorder: getComputedStyle(panel).borderTopColor,
            panelHeadBg: getComputedStyle(panelHead).backgroundColor,
            activeTabBg: getComputedStyle(activeTab).backgroundColor,
            activeTabColor: getComputedStyle(activeTab).color,
            activeTabShadow: getComputedStyle(activeTab).boxShadow,
            inactiveActiveTabBg: getComputedStyle(inactiveActiveTab).backgroundColor,
            inactiveActiveTabColor: getComputedStyle(inactiveActiveTab).color,
            inactiveActiveTabShadow: getComputedStyle(inactiveActiveTab).boxShadow,
            inactiveTabBg: getComputedStyle(inactiveTab).backgroundColor,
            inactiveTabBorder: getComputedStyle(inactiveTab).borderTopColor,
            inactiveDirColor: getComputedStyle(inactiveTab.querySelector('.session-button-dir') || inactiveTab).color,
            toolbarActiveBg: getComputedStyle(toolbarActive).backgroundColor,
            toolbarActiveBorder: getComputedStyle(toolbarActive).borderTopColor,
            paneControlBg: getComputedStyle(paneControl).backgroundColor,
            paneControlBorder: getComputedStyle(paneControl).borderTopColor,
            zoomControlBg: getComputedStyle(zoomControl).backgroundColor,
          };
        };
        const dark = readMetrics();
        document.body.classList.add('theme-light');
        return {dark, light: readMetrics()};
        """
    )
    assert theme_metrics["dark"]["panelHeadBg"].startswith("color(srgb")
    # The light chrome strip is a tinted (active-accent-derived) bar, NOT the neutral control bg — assert
    # the relationship, not a pinned green, so it survives the appearance.active_color picker.
    assert theme_metrics["light"]["panelHeadBg"] != theme_metrics["light"]["paneControlBg"]
    # Shared pane-chrome buttons (image 009): every UNPRESSED control is white (light) / near-black (dark)
    # via --pane-ctl-bg — including the expand "+" (formerly always-green). Only PRESSED/ACTIVE buttons go
    # green (asserted via toolbarActiveBg below). No per-button one-off colors.
    assert theme_metrics["dark"]["paneControlBg"] == "rgb(27, 36, 50)"
    assert theme_metrics["light"]["paneControlBg"] == "rgb(247, 249, 252)"
    assert theme_metrics["dark"]["zoomControlBg"] == "rgb(27, 36, 50)"      # "+" is NOT green when unpressed
    assert theme_metrics["light"]["zoomControlBg"] == "rgb(247, 249, 252)"
    assert theme_metrics["dark"]["zoomControlBg"] == theme_metrics["dark"]["paneControlBg"]  # all unpressed controls share one bg
    # The active control tab (the agent/"claude" pill) is PRESSED -> green, in both themes (shared rule).
    # The pressed/active control tab is the active accent (NOT a pinned green) — distinct from the
    # unpressed control bg in both themes, so the picker (Green/Blue/...) doesn't break the test.
    assert theme_metrics["dark"]["toolbarActiveBg"] != theme_metrics["dark"]["paneControlBg"]
    assert theme_metrics["light"]["toolbarActiveBg"] != theme_metrics["light"]["paneControlBg"]
    # DOIT.6 #31: the active-tab greens are tuned PER THEME so a theme switch visibly repaints the active
    # pane tab; the frame controls are also theme-specific now (image 043). Every OTHER surface stays
    # token-equal across themes.
    # inactiveTabBg is theme-specific now (images 003/004): light gets a very-light-green #e6f1dd while
    # dark keeps #285a2f, so it must NOT be required equal across themes.
    # toolbarActiveBg/Border are the PRESSED control tab's green, which is theme-specific (light #4f9e3a /
    # dark #86d600); detail-row bg now follows --pane-bar-bg so it is theme-specific too.
    theme_specific = {"panelHeadBg", "activeTabBg", "activeTabColor", "inactiveActiveTabBg", "inactiveActiveTabColor", "inactiveTabBg", "inactiveTabBorder", "inactiveDirColor", "paneControlBg", "paneControlBorder", "zoomControlBg", "toolbarActiveBg", "toolbarActiveBorder"}
    for key, value in theme_metrics["dark"].items():
        if key not in theme_specific:
            assert theme_metrics["light"][key] == value
    # The active pane tab shares the active accent with the pressed control tab (one --active-accent
    # source) and stands out from the unpressed control bg — true for any active-color preset.
    assert theme_metrics["dark"]["activeTabBg"] == theme_metrics["dark"]["toolbarActiveBg"]
    assert theme_metrics["light"]["activeTabBg"] == theme_metrics["light"]["toolbarActiveBg"]
    assert theme_metrics["dark"]["activeTabBg"] != theme_metrics["dark"]["paneControlBg"]
    assert theme_metrics["light"]["activeTabBg"] != theme_metrics["dark"]["activeTabBg"]
    assert theme_metrics["light"]["inactiveActiveTabBg"] != theme_metrics["dark"]["inactiveActiveTabBg"]
    # Active-tab text stays legible against its (theme-specific) accent in BOTH modes.
    assert theme_metrics["light"]["activeTabColor"] != theme_metrics["light"]["activeTabBg"]
    assert theme_metrics["dark"]["activeTabColor"] != theme_metrics["dark"]["activeTabBg"]
    assert theme_metrics["dark"]["activeTabShadow"] == "none"
    # images 003/004: an unfocused pane's active tab now uses the SAME full green as the focused pane's
    # active tab (no lightening) — the unfocused-active tokens are aliased to the focused ones.
    assert theme_metrics["dark"]["inactiveActiveTabBg"] == theme_metrics["dark"]["activeTabBg"]
    assert theme_metrics["dark"]["inactiveActiveTabShadow"] == "none"
    # REGRESSION GUARD (image 008): the inactive-tab branch/dir TEXT must contrast with the tab bg in BOTH
    # themes — i.e. NOT white-on-white. This is the check that was missing before: the prior browser test
    # measured tab BACKGROUNDS but never the nested .session-button-* TEXT color, so a near-white dir text
    # on a near-white light tab went uncaught. Compare relative luminance of text vs bg.
    def _lum(css_rgb):
        nums = [int(n) for n in re.findall(r"\d+", css_rgb)[:3]]
        return 0.2126 * nums[0] + 0.7152 * nums[1] + 0.0722 * nums[2]
    for th in ("light", "dark"):
        text_lum = _lum(theme_metrics[th]["inactiveDirColor"])
        bg_lum = _lum(theme_metrics[th]["inactiveTabBg"])
        assert abs(text_lum - bg_lum) > 80, (
            f"{th}: inactive-tab dir text ({theme_metrics[th]['inactiveDirColor']}) must contrast with the "
            f"tab bg ({theme_metrics[th]['inactiveTabBg']}) — not white-on-white"
        )


def test_split_pane_seam_is_a_compact_tile_divider(browser, tmp_path):
    load_split_seam_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const topPanel = document.getElementById('top-panel');
        const bottomPanel = document.getElementById('bottom-panel');
        const resizer = document.getElementById('split-resizer');
        const topRect = topPanel.getBoundingClientRect();
        const bottomRect = bottomPanel.getBoundingClientRect();
        const resizerRect = resizer.getBoundingClientRect();
        const topStyle = getComputedStyle(topPanel);
        const bottomStyle = getComputedStyle(bottomPanel);
        return {
          seamGap: bottomRect.top - topRect.bottom,
          resizerHeight: resizerRect.height,
          topBottomBorder: topStyle.borderBottomWidth,
          bottomTopBorder: bottomStyle.borderTopWidth,
          topBottomRadius: topStyle.borderBottomLeftRadius,
          bottomTopRadius: bottomStyle.borderTopLeftRadius,
        };
        """
    )
    assert metrics["resizerHeight"] <= 2
    assert metrics["seamGap"] <= 2.5
    assert metrics["topBottomBorder"] == "0px"
    assert metrics["bottomTopBorder"] == "0px"
    assert metrics["topBottomRadius"] == "0px"
    assert metrics["bottomTopRadius"] == "0px"


def test_tab_menu_rows_are_compact_for_many_tabs(browser, tmp_path):
    metrics = load_menu_fixture(browser, tmp_path)
    assert metrics["count"] == 30
    assert metrics["maxHeight"] <= 23
    assert metrics["maxStep"] <= 24
    assert metrics["firstTwentyFiveSpan"] <= 575
    assert metrics["width"] > 0
    assert metrics["width"] <= metrics["maxInlineSize"] + metrics["devicePixelRatio"]
    assert metrics["secondRowBorderTopColor"] != "rgba(0, 0, 0, 0)"
    assert metrics["scrollHeight"] <= 700


def test_topbar_uses_ui_font_size_and_compact_actions(browser, tmp_path):
    load_topbar_font_fixture(browser, tmp_path)
    topbar_metrics = browser.execute_script(
        """
        const menu = document.getElementById('menu-file');
        const action = document.getElementById('tabMetaToggle');
        const paneTab = document.querySelector('.pane-tab');
        const menuRect = menu.getBoundingClientRect();
        const actionRect = action.getBoundingClientRect();
        const paneTabRect = paneTab.getBoundingClientRect();
        return {
          menuFontSize: Number.parseFloat(getComputedStyle(menu).fontSize),
          menuHeight: menuRect.height,
          actionWidth: actionRect.width,
          actionHeight: actionRect.height,
          paneTabHeight: paneTabRect.height,
        };
        """
    )
    assert topbar_metrics["menuFontSize"] >= 17.5
    assert 23 <= topbar_metrics["menuHeight"] <= 25
    assert 22 <= topbar_metrics["paneTabHeight"] <= 24
    assert topbar_metrics["actionWidth"] <= 31
    assert topbar_metrics["actionHeight"] <= 31
    compact_metrics = browser.execute_script(
        """
        document.documentElement.style.setProperty('--ui-font-size', '13px');
        document.documentElement.style.setProperty('--tab-label-size', '13px');
        const action = document.getElementById('tabMetaToggle').getBoundingClientRect();
        const paneTab = document.querySelector('.pane-tab').getBoundingClientRect();
        return {actionWidth: action.width, actionHeight: action.height, paneTabHeight: paneTab.height};
        """
    )
    assert compact_metrics["actionWidth"] <= 21
    assert compact_metrics["actionHeight"] <= 21
    assert compact_metrics["paneTabHeight"] <= 21
    tiny_metrics = browser.execute_script(
        """
        document.documentElement.style.setProperty('--ui-font-size', '8px');
        document.documentElement.style.setProperty('--tab-label-size', '8px');
        const action = document.getElementById('tabMetaToggle').getBoundingClientRect();
        const paneTab = document.querySelector('.pane-tab').getBoundingClientRect();
        return {actionHeight: action.height, paneTabHeight: paneTab.height};
        """
    )
    assert tiny_metrics["actionHeight"] <= 18
    assert tiny_metrics["paneTabHeight"] <= 18


def test_active_pane_tab_container_lightens_in_dark_only(browser, tmp_path):
    load_fixture(browser, tmp_path, 860)
    metrics = browser.execute_script(
        """
        function colorFor(styleValue) {
          const probe = document.createElement('div');
          probe.style.position = 'absolute';
          probe.style.left = '-1000px';
          probe.style.top = '-1000px';
          probe.style.background = styleValue;
          document.body.appendChild(probe);
          const color = getComputedStyle(probe).backgroundColor;
          probe.remove();
          return color;
        }
        function brightness(color) {
          const nums = (color.match(/\\d+(?:\\.\\d+)?/g) || []).slice(0, 3).map(Number);
          if (color.startsWith('color(srgb')) return nums.reduce((sum, value) => sum + value * 255, 0);
          return nums[0] + nums[1] + nums[2];
        }
        document.body.classList.add('theme-dark');
        const head = document.querySelector('.panel-head');
        const darkStrip = colorFor('var(--pane-tab-strip-bg)');
        const darkHead = getComputedStyle(head).backgroundColor;
        document.body.classList.remove('theme-dark');
        document.body.classList.add('theme-light');
        const lightStrip = colorFor('var(--pane-tab-strip-bg)');
        const lightHead = getComputedStyle(head).backgroundColor;
        return {
          darkStrip,
          darkHead,
          darkStripBrightness: brightness(darkStrip),
          lightStrip,
          lightHead,
        };
        """
    )
    assert metrics["darkHead"] == metrics["darkStrip"], metrics
    assert metrics["darkStripBrightness"] > 0, metrics
    assert metrics["lightHead"] == metrics["lightStrip"], metrics


def test_pane_tab_strip_hover_token_is_removed():
    # The dark-only --pane-tab-strip-hover-bg was removed when the tab container + info bar were unified
    # onto one token. Cheap string guard against its reintroduction — no browser needed (P3 demotion).
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    assert "--pane-tab-strip-hover-bg" not in css


def test_diff_added_active_line_uses_same_fill_as_neighbor(browser, tmp_path):
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    page = tmp_path / "diff-active-line-fill.html"
    page.write_text(
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
              <div id="added-active" class="cm-line cm-insertedLine cm-activeLine">added active</div>
              <div id="plain-active" class="cm-line cm-activeLine">plain active</div>
            </div></div>
          </div>
        </body></html>""",
        encoding="utf-8",
    )
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        function bg(selector) {
          return getComputedStyle(document.querySelector(selector)).backgroundColor;
        }
        const dark = {
          added: bg('#added-a'),
          activeAdded: bg('#added-active'),
          plainActive: bg('#plain-active'),
          addToken: getComputedStyle(document.querySelector('#host')).getPropertyValue('--diff-add-line-bg').trim(),
          removeToken: getComputedStyle(document.querySelector('#host')).getPropertyValue('--diff-remove-line-bg').trim(),
        };
        document.body.classList.remove('editor-theme-dark');
        document.body.classList.add('editor-theme-light');
        const light = {
          added: bg('#added-a'),
          activeAdded: bg('#added-active'),
          addToken: getComputedStyle(document.querySelector('#host')).getPropertyValue('--diff-add-line-bg').trim(),
        };
        return {dark, light};
        """
    )
    assert metrics["dark"]["added"] == metrics["dark"]["activeAdded"], metrics
    assert metrics["dark"]["plainActive"] in ("rgba(0, 0, 0, 0)", "transparent"), metrics
    assert "transparent" not in metrics["dark"]["addToken"], metrics
    assert "transparent" not in metrics["dark"]["removeToken"], metrics
    assert metrics["light"]["added"] == metrics["light"]["activeAdded"], metrics
    assert metrics["light"]["addToken"] == "#d2f0d6", metrics


def test_readme_diff_waits_for_payload_before_building_codemirror(browser, tmp_path):
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    strings = json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text(encoding="utf-8"))
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
    current = original.replace(
        "Browser tools for watching, driving, and summarizing tmux sessions.",
        "Browser tools for watching and summarizing tmux sessions.\n\nDeterministic test-only README diff line.",
        1,
    )
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
    page.write_text(
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
              const originalLoadCodeMirrorApi = loadCodeMirrorApi;
              window.__holdReadmeCodeMirrorLoad = false;
              window.__readmeDiffLoadEntered = false;
              let releaseLoad = null;
              loadCodeMirrorApi = async function(...args) {{
                if (window.__holdReadmeCodeMirrorLoad) {{
                  window.__readmeDiffLoadEntered = true;
                  await new Promise(resolve => {{
                    releaseLoad = resolve;
                    window.__releaseReadmeCodeMirrorLoad = resolve;
                  }});
                }}
                return originalLoadCodeMirrorApi.apply(this, args);
              }};
              const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
              const waitFor = async predicate => {{
                for (let attempt = 0; attempt < 120; attempt += 1) {{
                  if (predicate()) return true;
                  await frame();
                }}
                return false;
              }};
              renderFileEditorPanel(panel, item);
              await waitFor(() => window.__resolveReadmeDiffFetch);
              await frame();
              await frame();
              const modeWhilePayloadUnresolved = panel._cmMode || '';
              const textWhilePayloadUnresolved = panel._cmView?.state?.doc?.toString?.() || '';
              window.__holdReadmeCodeMirrorLoad = true;
              window.__resolveReadmeDiffFetch();
              const diffLoadedWait = await waitFor(() => openFiles.get(path)?.diffLoaded === true);
              let manualRenderCalled = false;
              if (!window.__readmeDiffLoadEntered) {{
                manualRenderCalled = true;
                renderFileEditorPanel(panel, item);
              }}
              const loadEnteredWait = await waitFor(() => window.__readmeDiffLoadEntered);
              const modeBeforeDiffBuildRelease = panel._cmMode || '';
              if (releaseLoad) releaseLoad();
              const finalWait = await waitFor(() => panel._cmMode === 'diff' && panel._cmView?.state?.doc?.toString?.().includes('Browser tools for watching'));
              const finalText = panel._cmView?.state?.doc?.toString?.() || '';
              const state = openFiles.get(path) || {{}};
              return {{
                modeWhilePayloadUnresolved,
                textWhilePayloadUnresolved,
                diffLoadedWait,
                loadEnteredWait,
                finalWait,
                manualRenderCalled,
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
                loadEntered: window.__readmeDiffLoadEntered === true,
                apiHasMergeView: Boolean(window.YOLOmuxCodeMirror?.MergeView),
                apiHasUnifiedMergeView: Boolean(window.YOLOmuxCodeMirror?.unifiedMergeView),
                statusText: panel.querySelector('.file-editor-status-message')?.textContent || '',
                request: window.__readmeDiffRequest || '',
                errors: window.__readmeDiffErrors,
              }};
            }})();
          </script>
        </body></html>""",
        encoding="utf-8",
    )
    browser.get(page.as_uri())
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__readmeDiffReady.then(done, error => done({error: String(error), errors: window.__readmeDiffErrors || []}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["modeWhilePayloadUnresolved"] != "edit", metrics
    assert "Browser tools for watching" not in metrics["textWhilePayloadUnresolved"], metrics
    assert metrics["finalMode"] == "diff", json.dumps(metrics, sort_keys=True)
    assert metrics["finalTextLength"] == metrics["expectedTextLength"], metrics
    assert metrics["deletedRows"] > 0, metrics
    assert "from=HEAD" in metrics["request"] and "to=current" in metrics["request"], metrics
    assert metrics["errors"] == [], metrics


def test_editor_diff_button_waits_for_clean_payload_before_showing_refs(browser, tmp_path):
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    strings = json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text(encoding="utf-8"))
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
    page.write_text(
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
              const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
              const waitFor = async predicate => {{
                for (let attempt = 0; attempt < 120; attempt += 1) {{
                  if (predicate()) return true;
                  await frame();
                }}
                return false;
              }};
              const snapshot = () => {{
                const button = panel.querySelector('.file-editor-diff-panel');
                const refs = panel.querySelector('.file-editor-diff-ref-panel');
                const refInputs = Array.from(refs?.querySelectorAll('.diff-ref-input') || []).map(input => input.value || '');
                const state = openFiles.get(path) || {{}};
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
              await waitFor(() => window.__resolveCleanDiffFetch && openFiles.get(path)?.diffLoading === true);
              await frame();
              await frame();
              const whileUnresolved = snapshot();
              window.__resolveCleanDiffFetch();
              await waitFor(() => openFiles.get(path)?.diffLoaded === true && openFiles.get(path)?.diffLoading === false && editorViewModeFor(path, item) === 'diff');
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
        encoding="utf-8",
    )
    browser.get(page.as_uri())
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
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    strings = json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text(encoding="utf-8"))
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
    page.write_text(
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
        encoding="utf-8",
    )
    browser.get(page.as_uri())
    metrics = browser.execute_script("return window.__previewToolbarReady")
    assert metrics["preview"]["previewHidden"] is False, metrics
    assert metrics["preview"]["gutterHidden"] is True, metrics
    assert metrics["preview"]["wrapHidden"] is True, metrics
    assert metrics["preview"]["findHidden"] is True, metrics
    assert metrics["preview"]["modeHidden"] is False, metrics
    assert metrics["preview"]["diffExpandHidden"] is True, metrics
    assert metrics["preview"]["diffRefsHidden"] is True, metrics
    assert metrics["preview"]["popoutHidden"] is False, metrics
    assert metrics["preview"]["themeHidden"] is False, metrics
    assert metrics["preview"]["fontHidden"] is False, metrics
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


def test_editor_preview_vanilla_mode_uses_neutral_email_friendly_styles(browser, tmp_path):
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    strings = json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text(encoding="utf-8"))
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
    page.write_text(
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
              const preview = panel.querySelector('.file-editor-preview-pane-panel');
              const read = () => {{
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
        encoding="utf-8",
    )
    browser.get(page.as_uri())
    metrics = browser.execute_script("return window.__previewVanillaReady")
    assert metrics["normal"]["vanillaClass"] is False, metrics
    assert metrics["normal"]["headingColor"] != "rgb(17, 24, 39)", metrics
    assert metrics["normal"]["codeSpanColor"] == "rgb(255, 0, 0)", metrics
    assert metrics["vanilla"]["vanillaClass"] is True, metrics
    assert metrics["vanilla"]["previewBg"] == "rgb(255, 255, 255)", metrics
    assert metrics["vanilla"]["previewColor"] == "rgb(17, 24, 39)", metrics
    assert metrics["vanilla"]["headingColor"] == "rgb(17, 24, 39)", metrics
    assert metrics["vanilla"]["headingBg"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["vanilla"]["linkColor"] == "rgb(6, 69, 173)", metrics
    assert metrics["vanilla"]["codeSpanColor"] in ("rgb(17, 24, 39)", ""), metrics
    assert metrics["vanilla"]["buttonTheme"] == "vanilla", metrics
    assert "Vanilla preview" in metrics["vanilla"]["buttonTitle"], metrics


def test_markdown_edit_mode_keeps_colored_syntax_in_codemirror(browser, tmp_path):
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    strings = json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text(encoding="utf-8"))
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
    page.write_text(
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
              const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
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
              const root = getComputedStyle(document.documentElement);
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
                expectedHeading: root.getPropertyValue('--markdown-heading').trim(),
                hasBold: Boolean(bold),
                hasLink: Boolean(link),
                hasBoldAfterTheme: Boolean(boldAfterTheme),
                hasLinkAfterTheme: Boolean(linkAfterTheme),
              }};
            }})();
          </script>
        </body></html>""",
        encoding="utf-8",
    )
    browser.get(page.as_uri())
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
    assert metrics["headingColor"] == "rgb(37, 99, 235)", metrics
    assert metrics["headingBg"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["visibleHeadingColor"] == "rgb(37, 99, 235)", metrics
    assert metrics["visibleHeadingBg"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["hasBold"] is True, metrics
    assert metrics["hasLink"] is True, metrics
    assert metrics["afterHeadingText"].startswith("# YOLOmux"), metrics
    assert metrics["afterHeadingColor"] == "rgb(37, 99, 235)", metrics
    assert metrics["afterHeadingBg"] == "rgba(0, 0, 0, 0)", metrics
    assert metrics["hasBoldAfterTheme"] is True, metrics
    assert metrics["hasLinkAfterTheme"] is True, metrics


def test_editor_search_button_toggles_pressed_state_with_codemirror_panel(browser, tmp_path):
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    strings = json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text(encoding="utf-8"))
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
    page.write_text(
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
              const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
              const waitFor = async predicate => {{
                for (let attempt = 0; attempt < 160; attempt += 1) {{
                  if (predicate()) return true;
                  await frame();
                }}
                return false;
              }};
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
        encoding="utf-8",
    )
    browser.get(page.as_uri())
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


def test_topbar_finder_and_modified_files_headers_hover_accent_in_light_mode(browser, tmp_path):
    def theme_tokens():
        return browser.execute_script(
            """
            document.body.classList.add('theme-light');
            function tokenColor(name) {
              const probe = document.createElement('div');
              probe.style.background = `var(${name})`;
              probe.style.position = 'absolute';
              probe.style.left = '-1000px';
              probe.style.top = '-1000px';
              document.body.appendChild(probe);
              const color = getComputedStyle(probe).backgroundColor;
              probe.remove();
              return color;
            }
            return {
              panel: tokenColor('--panel'),
              neutral: tokenColor('--panel2'),
              accent: tokenColor('--pane-tab-strip-bg'),
            };
            """
        )

    def background(selector):
        return browser.execute_script("return getComputedStyle(document.querySelector(arguments[0])).backgroundColor", selector)

    def wait_background(selector, expected):
        WebDriverWait(browser, 2).until(lambda _driver: background(selector) == expected)

    load_topbar_font_fixture(browser, tmp_path)
    tokens = theme_tokens()
    wait_background("#topbar-fixture", tokens["neutral"])
    ActionChains(browser).move_to_element(browser.find_element("id", "topbar-fixture")).perform()
    wait_background("#topbar-fixture", tokens["accent"])
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".pane-tab")).perform()
    wait_background("#topbar-fixture", tokens["neutral"])

    load_finder_click_toolbar_fixture(browser, tmp_path)
    tokens = theme_tokens()
    wait_background("#finder-panel .file-explorer-head", tokens["neutral"])
    ActionChains(browser).move_to_element(browser.find_element("css selector", "#finder-panel .file-explorer-head")).perform()
    wait_background("#finder-panel .file-explorer-head", tokens["accent"])
    ActionChains(browser).move_to_element(browser.find_element("id", "terminal-panel")).perform()
    wait_background("#finder-panel .file-explorer-head", tokens["neutral"])

    activate_finder_diff_fixture(browser)
    wait_background("#modified-files-panel .changes-toolbar", tokens["panel"])
    ActionChains(browser).move_to_element(browser.find_element("id", "modified-files-panel")).perform()
    wait_background("#finder-panel .file-explorer-head", tokens["neutral"])
    wait_background("#modified-files-panel .changes-toolbar", tokens["accent"])


def test_finder_and_embedded_differ_scrollbars_hover_independently(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    browser.execute_script(
        """
        const tree = document.querySelector('.file-explorer-tree-panel');
        tree.innerHTML = '<div style="height: 520px"></div>';
        """
    )

    def thumb(selector):
        return browser.execute_script(
            "return getComputedStyle(document.querySelector(arguments[0]), '::-webkit-scrollbar-thumb').backgroundColor",
            selector,
        )

    def wait_thumb(selector, expected):
        WebDriverWait(browser, 2).until(lambda _driver: thumb(selector) == expected)

    neutral = "rgba(190, 205, 218, 0.62)"
    accent = browser.execute_script(
        """
        const probe = document.createElement('div');
        probe.style.background = 'var(--active-control-scrollbar-thumb)';
        document.body.appendChild(probe);
        const color = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return color;
        """
    )
    overflow = browser.execute_script(
        """
        const tree = document.querySelector('.file-explorer-tree-panel');
        return {
          tree: tree.scrollHeight > tree.clientHeight,
        };
        """
    )
    assert overflow["tree"]

    wait_thumb(".file-explorer-tree-panel", neutral)
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".file-explorer-tree-panel")).perform()
    wait_thumb(".file-explorer-tree-panel", accent)
    ActionChains(browser).move_to_element(browser.find_element("id", "terminal-panel")).perform()
    wait_thumb(".file-explorer-tree-panel", neutral)

    activate_finder_diff_fixture(browser)
    browser.execute_script(
        """
        const differ = document.getElementById('modified-files-panel');
        differ.insertAdjacentHTML('beforeend', '<div style="height: 520px"></div>');
        """
    )
    overflow = browser.execute_script(
        """
        const differ = document.getElementById('modified-files-panel');
        return {differ: differ.scrollHeight > differ.clientHeight};
        """
    )
    assert overflow["differ"]
    wait_thumb("#modified-files-panel", neutral)
    ActionChains(browser).move_to_element(browser.find_element("id", "modified-files-panel")).perform()
    wait_thumb("#modified-files-panel", accent)
    ActionChains(browser).move_to_element(browser.find_element("id", "terminal-panel")).perform()
    wait_thumb("#modified-files-panel", neutral)


def test_finder_differ_row_hover_and_embedded_refresh_are_visible_in_light_mode(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    activate_finder_diff_fixture(browser)
    refresh_metrics = browser.execute_script(
        """
        document.body.classList.add('theme-light');
        const button = document.querySelector('#modified-files-panel .changes-refresh');
        const style = getComputedStyle(button);
        const before = getComputedStyle(button, '::before');
        const rect = button.getBoundingClientRect();
        return {
          background: style.backgroundColor,
          borderColor: style.borderTopColor,
          color: style.color,
          beforeContent: before.content,
          beforeDisplay: before.display,
          beforeFontSize: Number.parseFloat(before.fontSize),
          height: rect.height,
          width: rect.width,
        };
        """
    )
    assert refresh_metrics["background"] != "rgb(255, 255, 255)"
    assert refresh_metrics["color"] != "rgb(255, 255, 255)"
    assert refresh_metrics["borderColor"] != "rgb(255, 255, 255)"
    assert refresh_metrics["beforeContent"] == '"↻"'
    assert refresh_metrics["beforeDisplay"] != "none"
    assert refresh_metrics["beforeFontSize"] >= 12
    assert refresh_metrics["height"] >= 18
    assert refresh_metrics["width"] >= 20

    load_pc_controls_fixture(browser, tmp_path)
    hover_tokens = browser.execute_script(
        """
        document.body.classList.add('theme-light');
        const probe = document.createElement('div');
        probe.style.position = 'absolute';
        probe.style.left = '-1000px';
        probe.style.top = '-1000px';
        probe.style.background = 'var(--file-hover-bg)';
        document.body.appendChild(probe);
        const hoverBg = getComputedStyle(probe).backgroundColor;
        probe.style.background = 'var(--file-hover-border)';
        const hoverBorder = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return {hoverBg, hoverBorder};
        """
    )
    ActionChains(browser).move_to_element(browser.find_element("id", "collapsed-dir")).perform()
    row_metrics = browser.execute_script(
        """
        const row = document.getElementById('collapsed-dir');
        const style = getComputedStyle(row);
        return {
          background: style.backgroundColor,
          boxShadow: style.boxShadow,
        };
        """
    )
    assert hover_tokens["hoverBg"] == "rgb(255, 242, 168)"
    assert row_metrics["background"] == hover_tokens["hoverBg"]
    assert hover_tokens["hoverBorder"] in row_metrics["boxShadow"]


def test_finder_sync_current_file_reuses_selected_row_colors(browser, tmp_path):
    load_pc_controls_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const read = () => {
          const selected = getComputedStyle(document.getElementById('selected-file-row'));
          const current = getComputedStyle(document.getElementById('current-file-row'));
          const selectedName = getComputedStyle(document.querySelector('#selected-file-row .file-tree-name'));
          const currentName = getComputedStyle(document.querySelector('#current-file-row .file-tree-name'));
          return {
            selectedColor: selected.color,
            currentColor: current.color,
            selectedNameColor: selectedName.color,
            currentNameColor: currentName.color,
            selectedBg: selected.backgroundColor,
            currentBg: current.backgroundColor,
            selectedShadow: selected.boxShadow,
            currentShadow: current.boxShadow,
          };
        };
        document.body.classList.remove('theme-light');
        document.body.classList.add('theme-dark');
        const dark = read();
        document.body.classList.remove('theme-dark');
        document.body.classList.add('theme-light');
        const light = read();
        return {dark, light};
        """
    )
    for theme in ("dark", "light"):
        assert metrics[theme]["currentColor"] == metrics[theme]["selectedColor"], metrics
        assert metrics[theme]["currentNameColor"] == metrics[theme]["selectedNameColor"], metrics
        assert metrics[theme]["currentBg"] == metrics[theme]["selectedBg"], metrics
        assert metrics[theme]["currentShadow"] == metrics[theme]["selectedShadow"], metrics


def test_finder_differ_status_badges_share_one_column(browser, tmp_path):
    load_file_tree_status_alignment_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const rowIds = ['status-row-m', 'status-row-t', 'status-row-q'];
        const rows = rowIds.map(id => {
          const row = document.getElementById(id);
          const status = row.querySelector('.file-tree-git-status');
          const date = row.querySelector('.file-tree-date');
          const rowRect = row.getBoundingClientRect();
          const statusRect = status.getBoundingClientRect();
          const dateRect = date.getBoundingClientRect();
          return {
            statusCenterX: statusRect.left + statusRect.width / 2,
            statusCenterY: statusRect.top + statusRect.height / 2,
            rowCenterY: rowRect.top + rowRect.height / 2,
            statusRight: statusRect.right,
            dateLeft: dateRect.left,
            dateRight: dateRect.right,
          };
        });
        const xs = rows.map(row => row.statusCenterX);
        const centerYs = rows.map(row => Math.abs(row.statusCenterY - row.rowCenterY));
        const dateRights = rows.map(row => row.dateRight);
        return {
          statusCenterDelta: Math.max(...xs) - Math.min(...xs),
          maxVerticalDelta: Math.max(...centerYs),
          dateRightDelta: Math.max(...dateRights) - Math.min(...dateRights),
          statusBeforeDate: rows.every(row => row.statusRight <= row.dateLeft + 0.5),
        };
        """
    )
    assert metrics["statusCenterDelta"] <= 0.75
    assert metrics["dateRightDelta"] <= 0.75
    assert metrics["maxVerticalDelta"] <= 1.0
    assert metrics["statusBeforeDate"]
    hidden_date_metrics = browser.execute_script(
        """
        const row = document.getElementById('status-row-m');
        const status = row.querySelector('.file-tree-git-status');
        const diff = row.querySelector('.file-tree-diff');
        const date = row.querySelector('.file-tree-date');
        const beforeStatusRight = status.getBoundingClientRect().right;
        const beforeDiffRight = diff.getBoundingClientRect().right;
        date.hidden = true;
        const rowRect = row.getBoundingClientRect();
        const statusRect = status.getBoundingClientRect();
        const diffRect = diff.getBoundingClientRect();
        return {
          dateDisplay: getComputedStyle(date).display,
          statusGain: statusRect.right - beforeStatusRight,
          diffGain: diffRect.right - beforeDiffRight,
          statusRightGap: rowRect.right - statusRect.right,
        };
        """
    )
    assert hidden_date_metrics["dateDisplay"] == "none"
    assert hidden_date_metrics["statusGain"] >= 80
    assert hidden_date_metrics["diffGain"] >= 80
    assert hidden_date_metrics["statusRightGap"] <= 10


def test_differ_long_filename_ellipsizes_before_date_column(browser, tmp_path):
    load_file_tree_status_alignment_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const row = document.getElementById('status-row-long');
        const shortRow = document.getElementById('status-row-m');
        const tree = row.parentElement;
        const name = row.querySelector('.file-tree-name');
        const agent = row.querySelector('.file-tree-agent');
        const diff = row.querySelector('.file-tree-diff');
        const status = row.querySelector('.file-tree-git-status');
        const date = row.querySelector('.file-tree-date');
        const shortName = shortRow.querySelector('.file-tree-name');
        const shortAgent = shortRow.querySelector('.file-tree-agent');
        const rowRect = row.getBoundingClientRect();
        const treeRect = tree.getBoundingClientRect();
        const nameRect = name.getBoundingClientRect();
        const agentRect = agent.getBoundingClientRect();
        const diffRect = diff.getBoundingClientRect();
        const statusRect = status.getBoundingClientRect();
        const dateRect = date.getBoundingClientRect();
        const shortNameRect = shortName.getBoundingClientRect();
        const shortAgentRect = shortAgent.getBoundingClientRect();
        return {
          treeRight: treeRect.right,
          rowRight: rowRect.right,
          nameRight: nameRect.right,
          agentLeft: agentRect.left,
          diffLeft: diffRect.left,
          statusLeft: statusRect.left,
          dateLeft: dateRect.left,
          dateRight: dateRect.right,
          dateClientWidth: date.clientWidth,
          dateScrollWidth: date.scrollWidth,
          nameClientWidth: name.clientWidth,
          nameScrollWidth: name.scrollWidth,
          nameFlex: getComputedStyle(name).flex,
          agentMarginInlineEnd: getComputedStyle(agent).marginInlineEnd,
          shortNameRight: shortNameRect.right,
          shortAgentLeft: shortAgentRect.left,
          shortNameFlex: getComputedStyle(shortName).flex,
        };
        """
    )
    assert metrics["dateRight"] <= metrics["treeRight"] + 0.5, metrics
    assert metrics["dateScrollWidth"] <= metrics["dateClientWidth"] + 1, metrics
    assert metrics["nameScrollWidth"] > metrics["nameClientWidth"] + 1, metrics
    assert metrics["nameFlex"].startswith("0 1"), metrics
    assert metrics["shortNameFlex"].startswith("0 1"), metrics
    assert metrics["agentMarginInlineEnd"] == "0px", metrics
    assert metrics["nameRight"] <= metrics["agentLeft"] + 0.5, metrics
    assert metrics["shortAgentLeft"] - metrics["shortNameRight"] <= 8, metrics
    assert metrics["agentLeft"] <= metrics["diffLeft"] <= metrics["statusLeft"] <= metrics["dateLeft"], metrics


def test_diff_overview_does_not_cover_editor_scrollbar(browser, tmp_path):
    load_codemirror_scrollbar_overview_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const hostRect = document.getElementById('host').getBoundingClientRect();
        const overviewRect = document.getElementById('overview').getBoundingClientRect();
        const overviewStyle = getComputedStyle(document.getElementById('overview'));
        const scroller = document.getElementById('scroller');
        const scrollerRect = scroller.getBoundingClientRect();
        const scrollbarStyle = getComputedStyle(scroller, '::-webkit-scrollbar');
        const cornerStyle = getComputedStyle(scroller, '::-webkit-scrollbar-corner');
        document.getElementById('overview').style.top = '0px';
        document.getElementById('overview').style.bottom = 'auto';
        document.getElementById('overview').style.height = `${scroller.clientHeight}px`;
        const adjustedOverviewRect = document.getElementById('overview').getBoundingClientRect();
        const verticalTrackBottom = scrollerRect.top + scroller.clientHeight;
        return {
          overviewRightGap: hostRect.right - adjustedOverviewRect.right,
          overviewTopDelta: Math.abs(adjustedOverviewRect.top - scrollerRect.top),
          overviewBottomDelta: Math.abs(adjustedOverviewRect.bottom - verticalTrackBottom),
          overviewWidth: adjustedOverviewRect.width,
          overviewBackground: overviewStyle.backgroundImage,
          overviewPointerEvents: overviewStyle.pointerEvents,
          tickCount: document.querySelectorAll('.cm-diff-overview-tick').length,
          scrollbarWidth: Number.parseFloat(scrollbarStyle.width || '0'),
          cornerBackground: cornerStyle.backgroundColor,
        };
        """
    )
    assert metrics["overviewRightGap"] >= 12
    assert metrics["overviewTopDelta"] <= 1
    assert metrics["overviewBottomDelta"] <= 1
    assert 3 <= metrics["overviewWidth"] <= 5
    assert "linear-gradient" in metrics["overviewBackground"]
    assert metrics["overviewPointerEvents"] == "none"
    assert metrics["tickCount"] == 0
    assert 11 <= metrics["scrollbarWidth"] <= 13
    assert metrics["cornerBackground"] in {
        "rgba(255, 255, 255, 0.04)",
        "rgba(255, 255, 255, 0.05)",
        "rgba(15, 23, 42, 0.1)",
    }


def test_diff_overview_matches_actual_todo_codemirror_rows(browser, tmp_path):
    load_codemirror_todo_diff_overview_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return window.__todoDiffOverviewMetrics != null")
    )
    metrics = browser.execute_script("return window.__todoDiffOverviewMetrics")
    assert metrics["chunks"] == [
        {
            "fromA": 2235,
            "toA": 147096,
            "endA": 147095,
            "fromB": 2235,
            "toB": 45532,
            "endB": 45531,
        }
    ]
    assert metrics["rows"]["bands"] == [
        {"kind": "remove", "start": 21, "end": 561},
        {"kind": "add", "start": 561, "end": 801},
    ]
    assert metrics["rows"]["currentLineCount"] == 311
    assert metrics["rows"]["deletedRows"] == 540
    assert metrics["rows"]["totalRows"] == 851
    assert metrics["deletedDomRows"] == metrics["removedRangeRows"]
    assert metrics["insertedRangeRows"] == 240
    assert "linear-gradient" in metrics["overviewBackground"]
    assert metrics["overviewStops"] == metrics["expectedStops"], metrics["overviewBackground"]
    assert metrics["tickCount"] == 0
    assert metrics["overviewTopDelta"] <= 1
    assert metrics["overviewBottomDelta"] <= 1


def test_diff_overview_matches_actual_file_explorer_visible_rows_after_scroll(browser, tmp_path):
    load_codemirror_file_explorer_diff_overview_fixture(browser, tmp_path)
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        window.__fileExplorerDiffOverviewMetrics().then(done);
        """
    )
    assert metrics["chunks"] == [
        {
            "fromA": 56602,
            "toA": 138459,
            "endA": 138458,
            "fromB": 56602,
            "toB": 144134,
            "endB": 144133,
        }
    ]
    assert metrics["tickCount"] == 0
    assert metrics["initialBackground"] != metrics["finalBackground"]
    assert metrics["initialBackground"] == ""
    assert metrics["initialOverviewPresent"] is False
    assert metrics["initialDeletedDomRows"] == 0
    assert metrics["initialChangedStops"] == []
    assert metrics["fullRows"]["deletedRows"] == 1986
    assert metrics["finalOverviewPresent"] is True
    assert metrics["finalChangedStops"] == metrics["expectedFullChangedStops"], metrics["finalBackground"]
    assert any(stop["color"] == "#ff5d6c" for stop in metrics["finalChangedStops"])
    assert any(stop["color"] == "#38d878" for stop in metrics["finalChangedStops"])
    cases = {case["name"]: case for case in metrics["cases"]}
    assert cases["top-normal"]["deletedDomRows"] == 0
    assert cases["red-middle-previous-regression"]["deletedDomRows"] == 1986
    checked_cases = [
        cases["red-middle-previous-regression"],
        cases["red-late-previous-regression"],
        cases["green-middle"],
    ]
    for case in checked_cases:
        assert case["mismatches"] == [], f"{case['name']} mismatched visible rows: {case['mismatches']}"
    for case in cases.values():
        if not case["railPresent"]:
            assert case["background"] == ""
            assert not any(sample["rail"] == "remove" for sample in case["samples"]), case
    assert any(sample["visible"] == "normal" for sample in cases["top-normal"]["samples"])
    assert any(sample["visible"] == "remove" for sample in cases["red-middle-previous-regression"]["samples"])
    assert any(sample["visible"] == "remove" for sample in cases["red-late-previous-regression"]["samples"])
    assert any(sample["visible"] == "add" for sample in cases["green-middle"]["samples"])


def test_diff_left_gutter_stays_neutral(browser, tmp_path):
    load_codemirror_scrollbar_overview_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const changed = document.getElementById('changed-gutter');
        const deleted = document.getElementById('deleted-gutter');
        const mergeRevert = document.getElementById('merge-revert');
        const changedStyle = getComputedStyle(changed);
        const deletedStyle = getComputedStyle(deleted);
        const mergeRevertStyle = getComputedStyle(mergeRevert);
        return {
          changedBg: changedStyle.backgroundColor,
          deletedBg: deletedStyle.backgroundColor,
          changedColor: changedStyle.color,
          deletedColor: deletedStyle.color,
          mergeRevertDisplay: mergeRevertStyle.display,
        };
        """
    )
    assert metrics["changedBg"] == "rgba(0, 0, 0, 0)"
    assert metrics["deletedBg"] == "rgba(0, 0, 0, 0)"
    assert metrics["changedColor"] == metrics["deletedColor"]
    assert metrics["mergeRevertDisplay"] == "none"


def test_finder_path_is_first_and_readable_in_wrapped_toolbar(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const toolbar = document.querySelector('#finder-panel .file-explorer-toolbar');
        const primaryRow = toolbar.querySelector('.file-explorer-primary-row');
        const scopeRow = toolbar.querySelector('.file-explorer-scope-row');
        const actionsRow = toolbar.querySelector('.file-explorer-actions-row');
        const collapse = actionsRow.querySelector('[data-file-explorer-collapse]');
        const newFile = actionsRow.querySelector('[data-file-explorer-new-file]');
        const newFolder = actionsRow.querySelector('[data-file-explorer-new-folder]');
        const sync = scopeRow.querySelector('.file-explorer-root-mode-toggle-panel');
        const hidden = scopeRow.querySelector('.file-explorer-hidden-toggle-panel');
        const quick = scopeRow.querySelector('.file-explorer-quick-access-panel');
        const quickButtons = Array.from(quick.querySelectorAll('.file-explorer-quick-access-button'));
        const path = primaryRow.querySelector('.file-explorer-path-inline');
        const copy = primaryRow.querySelector('.file-explorer-path-copy-panel');
        const mode = primaryRow.querySelector('.file-explorer-mode-switcher');
        const diffSession = primaryRow.querySelector('.file-explorer-diff-session-control');
        const modeButtons = Array.from(mode.querySelectorAll('[data-file-explorer-mode-set]'));
        const modeLabels = Array.from(mode.querySelectorAll('.file-explorer-mode-label'));
        const cluster = toolbar.querySelector('.file-explorer-date-reload-cluster');
        const date = cluster.querySelector('.file-explorer-date-toggle');
        const refresh = cluster.querySelector('.changes-refresh');
        const close = primaryRow.querySelector('.file-explorer-panel-close');
        const toolbarRect = toolbar.getBoundingClientRect();
        const primaryRowRect = primaryRow.getBoundingClientRect();
        const scopeRowRect = scopeRow.getBoundingClientRect();
        const actionsRowRect = actionsRow.getBoundingClientRect();
        const collapseRect = collapse.getBoundingClientRect();
        const newFileRect = newFile.getBoundingClientRect();
        const newFolderRect = newFolder.getBoundingClientRect();
        const syncRect = sync.getBoundingClientRect();
        const hiddenRect = hidden.getBoundingClientRect();
        const quickRect = quick.getBoundingClientRect();
        const firstQuickStyle = getComputedStyle(quickButtons[0]);
        const pathRect = path.getBoundingClientRect();
        const copyRect = copy.getBoundingClientRect();
        const modeRect = mode.getBoundingClientRect();
        const modeButtonRects = modeButtons.map(button => button.getBoundingClientRect());
        const modeButtonStyles = modeButtons.map(button => getComputedStyle(button));
        const clusterRect = cluster.getBoundingClientRect();
        const dateRect = date.getBoundingClientRect();
        const refreshRect = refresh.getBoundingClientRect();
        const closeRect = close.getBoundingClientRect();
        const textProbe = document.createElement('span');
        textProbe.style.color = 'var(--text)';
        document.body.appendChild(textProbe);
        const textColor = getComputedStyle(textProbe).color;
        textProbe.remove();
        return {
          firstRowIsPrimary: toolbar.firstElementChild === primaryRow,
          modeFirstInPrimaryRow: primaryRow.firstElementChild === mode,
          noPanelTitle: primaryRow.querySelector('.file-explorer-panel-title') === null,
          actionsOrder: actionsRow.firstElementChild === collapse && collapse.nextElementSibling === newFile && newFile.nextElementSibling === newFolder,
          folderIconPresent: newFolder.querySelector('.file-explorer-folder-icon') !== null,
          hiddenInScopeRow: scopeRow.firstElementChild === hidden,
          syncAfterHidden: hidden.nextElementSibling === sync,
          quickAfterSync: sync.nextElementSibling === quick,
          syncText: sync.textContent.trim(),
          syncPressed: sync.getAttribute('aria-pressed'),
          quickTexts: quickButtons.map(button => button.textContent.trim()),
          rootPressedCount: [sync, ...quickButtons].filter(button => button.getAttribute('aria-pressed') === 'true').length,
          quickBorderStyle: firstQuickStyle.borderTopStyle,
          quickBorderWidth: firstQuickStyle.borderTopWidth,
          diffSessionAfterMode: mode.nextElementSibling === diffSession,
          pathAfterDiffSession: diffSession.nextElementSibling === path,
          diffSessionHiddenInFilesMode: getComputedStyle(diffSession).display === 'none',
          collapseRight: collapseRect.right,
          newFileLeft: newFileRect.left,
          newFileRight: newFileRect.right,
          newFolderLeft: newFolderRect.left,
          syncLeft: syncRect.left,
          syncRight: syncRect.right,
          hiddenLeft: hiddenRect.left,
          hiddenRight: hiddenRect.right,
          quickLeft: quickRect.left,
          scopeRowTop: scopeRowRect.top,
          scopeRowBottom: scopeRowRect.bottom,
          pathLeft: pathRect.left,
          pathRight: pathRect.right,
          primaryRowLeft: primaryRowRect.left,
          primaryRowRight: primaryRowRect.right,
          primaryRowWidth: primaryRowRect.width,
          copyLeft: copyRect.left,
          copyRight: copyRect.right,
          copyWidth: copyRect.width,
          modeLeft: modeRect.left,
          modeRight: modeRect.right,
          modeWidth: modeRect.width,
          modeMaxButtonWidth: Math.max(...modeButtonRects.map(rect => rect.width)),
          modeButtonHorizontal: modeButtonRects.every(rect => rect.width > rect.height),
          modeLabelsHorizontal: modeLabels.every(label => getComputedStyle(label).writingMode === 'horizontal-tb'),
          modeUsesCondensedControlFont: modeButtonStyles.every(style => style.fontFamily.toLowerCase().includes('narrow') || style.fontStretch === 'condensed'),
          modeTexts: Array.from(mode.querySelectorAll('[data-file-explorer-mode-set]')).map(button => button.textContent.trim()),
          pathConsumesRemaining: pathRect.width >= primaryRowRect.width - modeRect.width - copyRect.width - closeRect.width - 36,
          actionsRowTop: actionsRowRect.top,
          primaryRowBottom: primaryRowRect.bottom,
          actionsRowRight: actionsRowRect.right,
          clusterRight: clusterRect.right,
          clusterLeft: clusterRect.left,
          dateRight: dateRect.right,
          refreshLeft: refreshRect.left,
          refreshRight: refreshRect.right,
          closeLeft: closeRect.left,
          closeRight: closeRect.right,
          pathWidth: pathRect.width,
          toolbarWidth: toolbarRect.width,
          pathColor: getComputedStyle(path).color,
          textColor,
        };
        """
    )
    assert metrics["firstRowIsPrimary"]
    assert metrics["modeFirstInPrimaryRow"]
    assert metrics["noPanelTitle"]
    assert metrics["actionsOrder"]
    assert metrics["folderIconPresent"]
    assert metrics["hiddenInScopeRow"]
    assert metrics["syncAfterHidden"]
    assert metrics["quickAfterSync"]
    assert metrics["syncText"] == "Sync"
    assert metrics["syncPressed"] == "true"
    assert metrics["quickTexts"] == ["~", "/*", "/tmp"]
    assert metrics["rootPressedCount"] == 1
    assert metrics["quickBorderStyle"] == "solid"
    assert metrics["quickBorderWidth"] == "1px"
    assert metrics["diffSessionAfterMode"]
    assert metrics["pathAfterDiffSession"]
    assert metrics["diffSessionHiddenInFilesMode"]
    assert metrics["collapseRight"] <= metrics["newFileLeft"]
    assert metrics["newFileRight"] <= metrics["newFolderLeft"]
    assert metrics["hiddenRight"] <= metrics["syncLeft"]
    assert metrics["syncRight"] <= metrics["quickLeft"]
    assert metrics["modeRight"] <= metrics["pathLeft"]
    assert metrics["pathLeft"] > metrics["primaryRowLeft"]
    assert metrics["pathWidth"] >= min(90, metrics["toolbarWidth"] / 4)
    assert metrics["pathRight"] <= metrics["copyLeft"]
    assert metrics["copyRight"] <= metrics["closeLeft"]
    assert metrics["modeButtonHorizontal"]
    assert metrics["modeLabelsHorizontal"]
    assert metrics["modeUsesCondensedControlFont"]
    assert metrics["modeMaxButtonWidth"] <= 62
    assert metrics["pathConsumesRemaining"]
    assert metrics["modeTexts"] == ["Finder", "ΔDiff"]
    assert abs(metrics["closeRight"] - metrics["primaryRowRight"]) <= 1
    assert metrics["pathColor"] == metrics["textColor"]
    assert metrics["scopeRowTop"] >= metrics["primaryRowBottom"]
    assert metrics["actionsRowTop"] >= metrics["scopeRowBottom"]
    assert metrics["dateRight"] <= metrics["refreshLeft"]
    assert metrics["refreshRight"] <= metrics["actionsRowRight"] + 1
    assert metrics["clusterLeft"] > metrics["pathLeft"]


def test_finder_diff_mode_toggle_fills_pane(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    before = browser.execute_script(
        """
        const filesButton = document.querySelector('[data-file-explorer-mode-set="files"]');
        const diffButton = document.querySelector('[data-file-explorer-mode-set="diff"]');
        const newFile = document.getElementById('new-file');
        const tree = document.querySelector('.file-explorer-tree-panel');
        const changes = document.querySelector('.file-explorer-changes-panel');
        return {
          bodyFiles: document.body.classList.contains('file-explorer-mode-files'),
          bodyDiff: document.body.classList.contains('file-explorer-mode-diff'),
          filesPressed: filesButton.getAttribute('aria-pressed'),
          diffPressed: diffButton.getAttribute('aria-pressed'),
          texts: Array.from(document.querySelectorAll('[data-file-explorer-mode-set]')).map(button => button.textContent.trim().replace(/\\s+/g, ' ')),
          diffButtonBg: getComputedStyle(diffButton).backgroundColor,
          newFileDisplay: getComputedStyle(newFile).display,
          treeDisplay: getComputedStyle(tree).display,
          changesDisplay: getComputedStyle(changes).display,
          titleCount: document.querySelectorAll('.file-explorer-panel-title').length,
        };
        """
    )
    assert before["bodyFiles"]
    assert not before["bodyDiff"]
    assert before["filesPressed"] == "true"
    assert before["diffPressed"] == "false"
    assert before["texts"] == ["Finder", "ΔDiff"]
    assert before["newFileDisplay"] != "none"
    assert before["treeDisplay"] != "none"
    assert before["changesDisplay"] == "none"
    assert before["titleCount"] == 0

    browser.find_element("css selector", "[data-file-explorer-mode-set='diff']").click()
    after = browser.execute_script(
        """
        const filesButton = document.querySelector('[data-file-explorer-mode-set="files"]');
        const diffButton = document.querySelector('[data-file-explorer-mode-set="diff"]');
        const newFile = document.getElementById('new-file');
        const pane = document.querySelector('.file-explorer-pane');
        const tree = document.querySelector('.file-explorer-tree-panel');
        const changes = document.querySelector('.file-explorer-changes-panel');
        const visible = selector => Array.from(document.querySelectorAll(selector)).filter(node => node.getClientRects().length > 0);
        const changesStyle = getComputedStyle(changes);
        const paneRect = pane.getBoundingClientRect();
        const changesRect = changes.getBoundingClientRect();
        return {
          bodyFiles: document.body.classList.contains('file-explorer-mode-files'),
          bodyDiff: document.body.classList.contains('file-explorer-mode-diff'),
          panelMode: document.getElementById('finder-panel').dataset.fileExplorerMode,
          filesPressed: filesButton.getAttribute('aria-pressed'),
          diffPressed: diffButton.getAttribute('aria-pressed'),
          texts: Array.from(document.querySelectorAll('[data-file-explorer-mode-set]')).map(button => button.textContent.trim().replace(/\\s+/g, ' ')),
          diffButtonBg: getComputedStyle(diffButton).backgroundColor,
          newFileDisplay: getComputedStyle(newFile).display,
          treeDisplay: getComputedStyle(tree).display,
          changesDisplay: changesStyle.display,
          changesFlexGrow: changesStyle.flexGrow,
          changesMaxBlockSize: changesStyle.maxBlockSize,
          paneHeight: paneRect.height,
          changesHeight: changesRect.height,
          titleCount: document.querySelectorAll('.file-explorer-panel-title').length,
          visibleRootControls: visible('.file-explorer-root-mode-toggle-panel').length,
          visibleSessionSelects: visible('[data-session-files-session]').length,
          visibleSortSelects: visible('[data-session-files-sort]').length,
          visibleDateButtons: visible('[data-file-explorer-tree-dates]').length,
          visibleReloadButtons: visible('[data-session-files-refresh], [data-file-explorer-refresh]').length,
        };
        """
    )
    assert not after["bodyFiles"]
    assert after["bodyDiff"]
    assert after["panelMode"] == "diff"
    assert after["filesPressed"] == "false"
    assert after["diffPressed"] == "true"
    assert after["texts"] == ["Finder", "ΔDiff"]
    assert after["diffButtonBg"] != before["diffButtonBg"]
    assert after["newFileDisplay"] == "none"
    assert after["treeDisplay"] == "none"
    assert after["changesDisplay"] != "none"
    assert after["changesFlexGrow"] == "1"
    assert after["changesMaxBlockSize"] == "none"
    assert abs(after["changesHeight"] - after["paneHeight"]) <= 1
    assert after["titleCount"] == 0
    assert after["visibleRootControls"] == 0
    assert after["visibleSessionSelects"] == 1
    assert after["visibleSortSelects"] == 1
    assert after["visibleDateButtons"] == 1
    assert after["visibleReloadButtons"] == 1

    browser.find_element("css selector", "[data-file-explorer-mode-set='files']").click()
    restored = browser.execute_script(
        """
        const filesButton = document.querySelector('[data-file-explorer-mode-set="files"]');
        const diffButton = document.querySelector('[data-file-explorer-mode-set="diff"]');
        return {
          bodyFiles: document.body.classList.contains('file-explorer-mode-files'),
          bodyDiff: document.body.classList.contains('file-explorer-mode-diff'),
          filesPressed: filesButton.getAttribute('aria-pressed'),
          diffPressed: diffButton.getAttribute('aria-pressed'),
          treeDisplay: getComputedStyle(document.querySelector('.file-explorer-tree-panel')).display,
          changesDisplay: getComputedStyle(document.querySelector('.file-explorer-changes-panel')).display,
        };
        """
    )
    assert restored["bodyFiles"]
    assert not restored["bodyDiff"]
    assert restored["filesPressed"] == "true"
    assert restored["diffPressed"] == "false"
    assert restored["treeDisplay"] != "none"
    assert restored["changesDisplay"] == "none"


def test_platform_controls_use_pc_glyphs(browser, tmp_path):
    load_pc_controls_fixture(browser, tmp_path)
    assert browser.execute_script("return getComputedStyle(document.getElementById('hidden-pane-zoom')).display") == "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('tab-minimize'), '::after').display") == "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('finder-close'), '::after').display") != "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('editor-close'), '::after').display") != "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('pane-zoom'), '::after').display") != "none"
    assert browser.execute_script("return document.getElementById('editor-close').getBoundingClientRect().width") <= 24
    assert browser.execute_script("return document.getElementById('tab-minimize').getBoundingClientRect().width") >= 18
    assert browser.execute_script("return getComputedStyle(document.getElementById('collapsed-preferences')).display") == "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('working-yolo')).animationName") == "yolo-marker-rotate"
    assert browser.execute_script("return getComputedStyle(document.getElementById('working-yolo'), '::after').content") == "none"
    # DOIT.6 #23: working YO spins SLOWLY at the yolo_rotate_ms setting (20s), not a fast hardcoded value.
    assert browser.execute_script("return getComputedStyle(document.getElementById('working-yolo')).animationDuration") == "20s"
    # An idle (auto-on, NON-working) marker must be STATIC — no ambient rotation.
    assert browser.execute_script("return getComputedStyle(document.getElementById('idle-yolo')).animationName") == "none"
    triangle_sizes = browser.execute_script(
        """
        const root = document.documentElement;
        const collapsed = getComputedStyle(document.querySelector('#collapsed-dir > .file-tree-icon'));
        const expanded = getComputedStyle(document.querySelector('#expanded-dir > .file-tree-icon'));
        const defaultWidth = document.querySelector('#collapsed-dir > .file-tree-icon').getBoundingClientRect().width;
        const defaultFontSize = Number.parseFloat(collapsed.fontSize);
        root.style.setProperty('--file-explorer-font-size', '8px');
        const smallIcon = document.querySelector('#collapsed-dir > .file-tree-icon');
        const smallStyle = getComputedStyle(smallIcon);
        const smallWidth = smallIcon.getBoundingClientRect().width;
        const smallFontSize = Number.parseFloat(smallStyle.fontSize);
        root.style.removeProperty('--file-explorer-font-size');
        return {
          collapsedSize: Number.parseFloat(collapsed.fontSize),
          expandedSize: Number.parseFloat(expanded.fontSize),
          collapsedWidth: defaultWidth,
          defaultFontSize,
          smallWidth,
          smallFontSize,
          expandedColor: expanded.color,
          collapsedColor: collapsed.color,
        };
        """
    )
    assert triangle_sizes["collapsedSize"] > 0
    assert triangle_sizes["expandedSize"] > 0
    assert triangle_sizes["smallWidth"] < triangle_sizes["collapsedWidth"]
    assert triangle_sizes["smallFontSize"] < triangle_sizes["defaultFontSize"]
    assert triangle_sizes["expandedColor"] != triangle_sizes["collapsedColor"]
    dots_center_delta = browser.execute_script(
        """
        const button = document.getElementById('pane-actions').getBoundingClientRect();
        const dots = document.getElementById('pane-actions-dots').getBoundingClientRect();
        const actionsStyle = getComputedStyle(document.getElementById('pane-actions'));
        const dotsStyle = getComputedStyle(document.getElementById('pane-actions-dots'));
        const hashStyle = getComputedStyle(document.getElementById('hash-tab'));
        return {
          x: Math.abs((button.left + button.width / 2) - (dots.left + dots.width / 2)),
          y: Math.abs((button.top + button.height / 2) - (dots.top + dots.height / 2)),
          background: actionsStyle.backgroundColor,
          borderColor: actionsStyle.borderTopColor,
          dotsColor: dotsStyle.color,
          hashColor: hashStyle.color,
        };
        """
    )
    assert dots_center_delta["x"] <= 1
    assert dots_center_delta["y"] <= 1
    assert dots_center_delta["background"] != "rgba(0, 0, 0, 0)"
    assert dots_center_delta["borderColor"] != "rgba(0, 0, 0, 0)"
    # Shared pane-chrome treatment: the "..." actions dots and the "#" control share ONE foreground color
    # (--pane-ctl-fg) now — consistent, not per-button (image 009).
    assert dots_center_delta["dotsColor"] == dots_center_delta["hashColor"]
    light_control = browser.execute_script(
        """
        document.body.classList.add('theme-light');
        const actionsStyle = getComputedStyle(document.getElementById('pane-actions'));
        const closeStyle = getComputedStyle(document.getElementById('finder-close'));
        return {
          actionsColor: actionsStyle.color,
          actionsBg: actionsStyle.backgroundColor,
          closeColor: closeStyle.color,
          closeBg: closeStyle.backgroundColor,
          infoLabelColor: getComputedStyle(document.querySelector('#info-tab .pane-tab-info-label')).color,
          infoTabBg: getComputedStyle(document.getElementById('info-tab')).backgroundColor,
        };
        """
    )
    assert light_control["actionsColor"] == "rgb(31, 41, 55)"
    assert light_control["actionsColor"] != light_control["actionsBg"]
    assert light_control["closeColor"] == "rgb(31, 41, 55)"
    assert light_control["closeColor"] != light_control["closeBg"]
    # DOIT.6 #27: the YO!info tab label is legible in light mode (color contrasts with the tab bg,
    # not white-on-white) now that it uses the themed .session-button-dir treatment.
    assert light_control["infoLabelColor"] != light_control["infoTabBg"]
    z_indexes = browser.execute_script(
        """
        return {
          contextMenu: Number.parseInt(getComputedStyle(document.getElementById('test-context-menu')).zIndex, 10),
          imagePreview: Number.parseInt(getComputedStyle(document.getElementById('test-image-preview')).zIndex, 10),
          tabPopover: Number.parseInt(getComputedStyle(document.getElementById('test-tab-popover')).zIndex, 10),
        };
        """
    )
    assert z_indexes["contextMenu"] > z_indexes["imagePreview"]
    assert z_indexes["contextMenu"] > z_indexes["tabPopover"]

    ActionChains(browser).move_to_element(browser.find_element("id", "tab-minimize")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('tab-minimize')).opacity") == "1"

    ActionChains(browser).move_to_element(browser.find_element("id", "pane-zoom")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('pane-zoom')).backgroundColor") != "rgba(0, 0, 0, 0)"

    ActionChains(browser).move_to_element(browser.find_element("id", "finder-close")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('finder-close')).opacity") == "1"

    ActionChains(browser).move_to_element(browser.find_element("id", "editor-close")).perform()
    assert browser.execute_script("return getComputedStyle(document.getElementById('editor-close')).opacity") == "1"

    tree_metrics = browser.execute_script(
        """
        const collapsedIcon = document.querySelector('#collapsed-dir .file-tree-icon');
        const expandedIcon = document.querySelector('#expanded-dir .file-tree-icon');
        const collapsedName = document.querySelector('#collapsed-dir .file-tree-name');
        return {
          collapsedColor: getComputedStyle(collapsedIcon).color,
          expandedColor: getComputedStyle(expandedIcon).color,
          iconSize: Number.parseFloat(getComputedStyle(collapsedIcon).fontSize),
          nameSize: Number.parseFloat(getComputedStyle(collapsedName).fontSize),
        };
        """
    )
    assert tree_metrics["collapsedColor"] != tree_metrics["expandedColor"]
    assert tree_metrics["iconSize"] > tree_metrics["nameSize"]
    repo_row_metrics = browser.execute_script(
        """
        const name = document.querySelector('#repo-dir .file-tree-name');
        const branch = document.querySelector('#repo-dir .file-tree-repo-branch');
        const delta = document.querySelector('#repo-dir .file-tree-repo-delta');
        return {
          nameWeight: getComputedStyle(name).fontWeight,
          branchWeight: getComputedStyle(branch).fontWeight,
          deltaWeight: getComputedStyle(delta).fontWeight,
          branchFont: getComputedStyle(branch).fontFamily,
          nameColor: getComputedStyle(name).color,
        };
        """
    )
    assert repo_row_metrics["nameWeight"] in ("400", "normal")
    assert repo_row_metrics["branchWeight"] in ("400", "normal")
    assert repo_row_metrics["deltaWeight"] in ("400", "normal")
    assert "mono" in repo_row_metrics["branchFont"].lower()
    assert repo_row_metrics["nameColor"] != tree_metrics["collapsedColor"]


def test_editor_pane_does_not_shift_grid_when_legacy_body_class_is_present(browser, tmp_path):
    load_editor_pane_legacy_body_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const grid = document.getElementById('grid');
        const gridStyle = getComputedStyle(grid);
        const panel = document.querySelector('.file-editor-panel').getBoundingClientRect();
        return {
          paddingLeft: Number.parseFloat(gridStyle.paddingLeft),
          panelLeft: panel.left,
        };
        """
    )
    assert metrics["paddingLeft"] <= 10
    assert metrics["panelLeft"] <= 16


def test_codemirror_editor_controls_are_sized_and_aligned(browser, tmp_path):
    load_codemirror_editor_controls_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const firstTab = document.querySelector('.pane-tab').getBoundingClientRect();
        const actions = document.getElementById('editor-actions').getBoundingClientRect();
        const search = document.getElementById('search-field').getBoundingClientRect();
        const replace = document.getElementById('replace-field').getBoundingClientRect();
        const nextButton = document.querySelector('.cm-button[name="next"]').getBoundingClientRect();
        const previousButton = document.querySelector('.cm-button[name="prev"]').getBoundingClientRect();
        const allButton = document.querySelector('.cm-button[name="select"]').getBoundingClientRect();
        const replaceButton = document.querySelector('.cm-button[name="replace"]').getBoundingClientRect();
        const replaceAllButton = document.querySelector('.cm-button[name="replaceAll"]').getBoundingClientRect();
        const count = document.getElementById('search-count').getBoundingClientRect();
        const label = document.getElementById('match-label').getBoundingClientRect();
        const regexpLabel = document.querySelectorAll('.cm-search label')[1].getBoundingClientRect();
        const wordLabel = document.querySelectorAll('.cm-search label')[2].getBoundingClientRect();
        const labelStyle = getComputedStyle(document.getElementById('match-label'));
        const checkbox = document.getElementById('match-case').getBoundingClientRect();
        const markerContent = getComputedStyle(document.getElementById('wrapped-line'), '::before').content;
        const marker = document.getElementById('wrap-marker').getBoundingClientRect();
        const markerStyle = getComputedStyle(document.getElementById('wrap-marker'));
        const panelRing = getComputedStyle(document.querySelector('.file-editor-panel'));
        const searchLabel = getComputedStyle(document.querySelector('.cm-search'), '::before').content;
        const editorStyle = getComputedStyle(document.getElementById('cm-editor'));
        const themeStyle = getComputedStyle(document.querySelector('.file-editor-theme-panel'));
        const wrapStyle = getComputedStyle(document.querySelector('.file-editor-wrap-panel'));
        const findStyle = getComputedStyle(document.querySelector('.file-editor-find-panel'));
        const previewStyle = getComputedStyle(document.querySelector('[data-editor-mode="preview"]'));
        const closeStyle = getComputedStyle(document.querySelector('.file-editor-panel-close'));
        const searchCloseStyle = getComputedStyle(document.querySelector('.cm-dialog-close'));
        const syntaxProbe = Array.from(document.querySelectorAll('#light-syntax-probe span')).map(node => {
          const style = getComputedStyle(node);
          return {color: style.color, background: style.backgroundColor, border: style.borderTopColor};
        });
        const filePopoverStyle = getComputedStyle(document.getElementById('file-popover'));
        const filePopoverCopyStyle = getComputedStyle(document.getElementById('file-popover-copy'));
        const findControl = document.querySelector('.file-editor-find-panel').getBoundingClientRect();
        const wrapControl = document.querySelector('.file-editor-wrap-panel').getBoundingClientRect();
        const modeControl = document.querySelector('[data-editor-mode="preview"]').getBoundingClientRect();
        const modeButtonRects = Array.from(document.querySelectorAll('.file-editor-mode-control button')).map(button => button.getBoundingClientRect());
        const toolbarButtons = Array.from(document.querySelectorAll([
          '.file-editor-gutter-panel',
          '.file-editor-wrap-panel',
          '.file-editor-find-panel',
          '.file-editor-blame-panel',
          '.file-editor-diff-panel',
          '.file-editor-diff-expand-panel',
          '.file-editor-theme-panel',
          '.file-editor-reload-panel',
          '.file-editor-save-panel',
        ].join(',')));
        const modeIconDeltas = Array.from(document.querySelectorAll('.file-editor-mode-control button')).map(button => {
          const buttonRect = button.getBoundingClientRect();
          const iconRect = button.querySelector('.file-editor-icon').getBoundingClientRect();
          return Math.abs((buttonRect.top + buttonRect.height / 2) - (iconRect.top + iconRect.height / 2));
        });
        const toolbarIconDeltas = toolbarButtons
          .filter(button => button.querySelector('.file-editor-icon'))
          .map(button => {
            const buttonRect = button.getBoundingClientRect();
            const iconRect = button.querySelector('.file-editor-icon').getBoundingClientRect();
            return {
              cls: button.className,
              dx: Math.abs((buttonRect.left + buttonRect.width / 2) - (iconRect.left + iconRect.width / 2)),
              dy: Math.abs((buttonRect.top + buttonRect.height / 2) - (iconRect.top + iconRect.height / 2)),
            };
          });
        const toolbarButtonRects = toolbarButtons.map(button => button.getBoundingClientRect());
        const elementAtCenter = rect => document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        const tabRows = [];
        for (const tab of Array.from(document.querySelectorAll('.pane-tab'))) {
          const rect = tab.getBoundingClientRect();
          let row = tabRows.find(item => Math.abs(item.top - rect.top) <= 1);
          if (!row) {
            row = {top: rect.top, rights: []};
            tabRows.push(row);
          }
          row.rights.push(rect.right);
        }
        return {
          actionsTopDelta: Math.abs(actions.top - firstTab.top),
          searchWidth: search.width,
          replaceWidth: replace.width,
          nextWidth: nextButton.width,
          previousWidth: previousButton.width,
          allWidth: allButton.width,
          countText: document.getElementById('search-count').textContent,
          countColor: getComputedStyle(document.getElementById('search-count')).color,
          nextTitle: document.querySelector('.cm-button[name="next"]').title,
          previousTitle: document.querySelector('.cm-button[name="prev"]').title,
          searchFirstToggleGap: label.left - search.right,
          toggleCountGap: count.left - regexpLabel.right,
          previousNextGap: nextButton.left - previousButton.right,
          nextAllGap: allButton.left - nextButton.right,
          replaceReplaceAllGap: replaceAllButton.left - replaceButton.right,
          labelRegexpGap: wordLabel.left - label.right,
          regexpWordGap: regexpLabel.left - wordLabel.right,
          replaceLeftDelta: Math.abs(search.left - replace.left),
          replaceWidthDelta: Math.abs(search.width - replace.width),
          checkboxCenterDelta: Math.abs((checkbox.top + checkbox.height / 2) - (label.top + label.height / 2)),
          labelFontFamily: labelStyle.fontFamily,
          labelFontSize: Number.parseFloat(labelStyle.fontSize),
          markerContent,
          markerHeight: marker.height,
          markerColor: markerStyle.color,
          // DOIT.24 C1: the focus ring is the translucent gutter border (color-mix of --panel-ring-color).
          panelRingBorderColor: getComputedStyle(document.querySelector('.file-editor-panel')).borderTopColor,
          searchLabel,
          editorBg: editorStyle.backgroundColor,
          editorColor: editorStyle.color,
          themeBg: themeStyle.backgroundColor,
          themeBorderColor: themeStyle.borderTopColor,
          themeColor: themeStyle.color,
          wrapBg: wrapStyle.backgroundColor,
          wrapBorderColor: wrapStyle.borderTopColor,
          findBg: findStyle.backgroundColor,
          previewBg: previewStyle.backgroundColor,
          closeBg: closeStyle.backgroundColor,
          searchCloseColor: searchCloseStyle.color,
          searchCloseBg: searchCloseStyle.backgroundColor,
          syntaxColorCount: new Set(syntaxProbe.map(item => item.color)).size,
          keywordColor: syntaxProbe[0].color,
          stringColor: syntaxProbe[1].color,
          functionColor: syntaxProbe[3].color,
          commentColor: syntaxProbe[4].color,
          headingColor: syntaxProbe[5].color,
          inlineCodeColor: syntaxProbe[6].color,
          inlineCodeBg: syntaxProbe[6].background,
          inlineCodeBorder: syntaxProbe[6].border,
          listMarkerColor: syntaxProbe[7].color,
          linkColor: syntaxProbe[8].color,
          filePopoverPointerEvents: filePopoverStyle.pointerEvents,
          filePopoverCopyPointerEvents: filePopoverCopyStyle.pointerEvents,
          findControlClickable: Boolean(elementAtCenter(findControl)?.closest?.('.file-editor-find-panel')),
          wrapControlClickable: Boolean(elementAtCenter(wrapControl)?.closest?.('.file-editor-wrap-panel')),
          previewControlClickable: Boolean(elementAtCenter(modeControl)?.closest?.('[data-editor-mode="preview"]')),
          modeButtonTopSpread: Math.max(...modeButtonRects.map(rect => rect.top)) - Math.min(...modeButtonRects.map(rect => rect.top)),
          modeButtonHeightSpread: Math.max(...modeButtonRects.map(rect => rect.height)) - Math.min(...modeButtonRects.map(rect => rect.height)),
          modeIconCenterMaxDelta: Math.max(...modeIconDeltas),
          toolbarButtonTopSpread: Math.max(...toolbarButtonRects.map(rect => rect.top)) - Math.min(...toolbarButtonRects.map(rect => rect.top)),
          toolbarButtonHeightSpread: Math.max(...toolbarButtonRects.map(rect => rect.height)) - Math.min(...toolbarButtonRects.map(rect => rect.height)),
          toolbarIconCenterMaxDx: Math.max(...toolbarIconDeltas.map(item => item.dx)),
          toolbarIconCenterMaxDy: Math.max(...toolbarIconDeltas.map(item => item.dy)),
          toolbarIconDeltas,
          tabRowCount: tabRows.length,
          lowerTabRowsUseFullWidth: tabRows.slice(1).some(row => Math.max(...row.rights) > actions.left + 20),
        };
        """
    )
    assert metrics["actionsTopDelta"] <= 2
    assert metrics["searchWidth"] >= 120
    assert metrics["searchWidth"] <= 210
    assert metrics["replaceWidth"] >= 120
    assert metrics["nextWidth"] <= 45
    assert metrics["previousWidth"] <= 75
    assert metrics["allWidth"] <= 38
    assert metrics["countText"] == "3/102"
    assert metrics["countColor"] != "rgb(0, 0, 0)"
    assert metrics["nextTitle"] == "Next match (Enter)"
    assert metrics["previousTitle"] == "Previous match (Shift+Enter)"
    assert 0 <= metrics["searchFirstToggleGap"] <= 8
    assert 0 <= metrics["toggleCountGap"] <= 10
    assert metrics["previousNextGap"] <= 6
    assert metrics["nextAllGap"] <= 6
    assert metrics["replaceReplaceAllGap"] <= 4
    assert metrics["labelRegexpGap"] <= 4
    assert metrics["regexpWordGap"] <= 4
    assert metrics["replaceLeftDelta"] <= 1.5
    assert metrics["replaceWidthDelta"] <= 2
    assert metrics["checkboxCenterDelta"] <= 1.5
    assert (
        "Arial Narrow" in metrics["labelFontFamily"]
        or "Roboto Condensed" in metrics["labelFontFamily"]
        or metrics["labelFontSize"] <= 11
    )
    assert metrics["markerContent"] in ("none", '""')
    assert metrics["markerHeight"] > 0
    assert metrics["markerColor"] != "rgb(0, 0, 0)"
    # DOIT.24 C1: the active pane's focus ring is the translucent gutter border; assert it shows a
    # colored (non-transparent) ring color (color-mix of --panel-ring-color at --pane-ring-opacity).
    assert metrics["panelRingBorderColor"] not in ("rgba(0, 0, 0, 0)", "transparent")
    assert metrics["searchLabel"] in ("none", '""')
    assert metrics["editorBg"] != "rgb(15, 17, 21)"
    assert metrics["editorColor"] != "rgb(228, 232, 238)"
    assert metrics["themeBorderColor"] != "rgba(0, 0, 0, 0)"
    assert metrics["themeColor"] != metrics["editorColor"]
    assert metrics["wrapBorderColor"] != "rgba(0, 0, 0, 0)"
    assert metrics["themeBg"] != "rgba(0, 0, 0, 0)"
    assert metrics["wrapBg"] not in ("rgb(255, 255, 255)", "rgb(221, 244, 255)")
    assert metrics["findBg"] != "rgba(0, 0, 0, 0)"
    assert metrics["previewBg"] != "rgba(0, 0, 0, 0)"
    assert metrics["wrapBg"] != metrics["findBg"]
    assert metrics["closeBg"] != metrics["editorBg"]
    assert metrics["closeBg"] != "rgb(255, 235, 233)"
    assert metrics["searchCloseColor"] != "rgb(255, 255, 255)"
    assert metrics["searchCloseColor"] != metrics["searchCloseBg"]
    assert metrics["syntaxColorCount"] >= 6
    assert metrics["keywordColor"] != metrics["stringColor"]
    assert metrics["functionColor"] != metrics["keywordColor"]
    assert metrics["inlineCodeColor"] != metrics["headingColor"]
    assert metrics["inlineCodeColor"] != metrics["linkColor"]
    assert metrics["inlineCodeColor"] != metrics["listMarkerColor"]
    assert metrics["headingColor"] != metrics["linkColor"]
    assert metrics["commentColor"] == metrics["listMarkerColor"]
    assert metrics["inlineCodeBg"] != "rgba(0, 0, 0, 0)"
    assert metrics["inlineCodeBorder"] != "rgba(0, 0, 0, 0)"
    assert metrics["filePopoverPointerEvents"] == "auto"  # popover-open tab: interactive when visible
    assert metrics["filePopoverCopyPointerEvents"] == "auto"
    assert metrics["findControlClickable"]
    assert metrics["wrapControlClickable"]
    assert metrics["previewControlClickable"]
    assert metrics["modeButtonTopSpread"] <= 1
    assert metrics["modeButtonHeightSpread"] <= 1
    assert metrics["modeIconCenterMaxDelta"] <= 1.5
    assert metrics["toolbarButtonTopSpread"] <= 1
    assert metrics["toolbarButtonHeightSpread"] <= 1
    assert metrics["toolbarIconCenterMaxDx"] <= 1.5, metrics["toolbarIconDeltas"]
    assert metrics["toolbarIconCenterMaxDy"] <= 1.5, metrics["toolbarIconDeltas"]


def test_editor_diff_ref_reset_is_visible_and_hittable(browser, tmp_path):
    load_editor_diff_ref_toolbar_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const toolbar = document.querySelector('.file-editor-toolbar').getBoundingClientRect();
        const gutter = document.getElementById('gutter-button').getBoundingClientRect();
        const diff = document.getElementById('diff-button').getBoundingClientRect();
        const expand = document.getElementById('diff-expand-button').getBoundingClientRect();
        const diffStyle = getComputedStyle(document.getElementById('diff-button'));
        const panel = document.getElementById('diff-ref-panel').getBoundingClientRect();
        const controls = document.querySelector('[data-diff-ref-controls]').getBoundingClientRect();
        const to = document.getElementById('to-ref').getBoundingClientRect();
        const reset = document.getElementById('reset-ref').getBoundingClientRect();
        const resetStyle = getComputedStyle(document.getElementById('reset-ref'));
        const panelStyle = getComputedStyle(document.getElementById('diff-ref-panel'));
        const hit = document.elementFromPoint(reset.left + reset.width / 2, reset.top + reset.height / 2);
        return {
          toolbarLeft: toolbar.left,
          toolbarRight: toolbar.right,
          gutterLeft: gutter.left,
          gutterRight: gutter.right,
          diffLeft: diff.left,
          diffRight: diff.right,
          diffText: document.getElementById('diff-button').textContent.trim(),
          expandLeft: expand.left,
          expandRight: expand.right,
          diffBg: diffStyle.backgroundColor,
          diffBorder: diffStyle.borderTopColor,
          panelRight: panel.right,
          panelLeft: panel.left,
          controlsRight: controls.right,
          toRight: to.right,
          resetLeft: reset.left,
          resetRight: reset.right,
          resetWidth: reset.width,
          resetDisplay: resetStyle.display,
          panelOverflow: panelStyle.overflow,
          hitReset: Boolean(hit?.closest?.('#reset-ref')),
        };
        """
    )
    assert metrics["gutterLeft"] <= metrics["toolbarLeft"] + 8, metrics
    assert 0 <= metrics["diffLeft"] - metrics["gutterRight"] <= 6, metrics
    assert metrics["diffText"] == "ΔDiff", metrics
    assert 0 <= metrics["expandLeft"] - metrics["diffRight"] <= 6, metrics
    assert 0 <= metrics["panelLeft"] - metrics["expandRight"] <= 6, metrics
    assert metrics["diffBg"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["diffBorder"] != "rgba(0, 0, 0, 0)", metrics
    assert metrics["resetDisplay"] != "none"
    assert metrics["resetWidth"] >= 18
    assert metrics["panelOverflow"] == "visible"
    assert 0 <= metrics["resetLeft"] - metrics["toRight"] <= 5
    assert metrics["resetRight"] <= metrics["panelRight"] + 1
    assert metrics["controlsRight"] <= metrics["panelRight"] + 1
    assert metrics["panelRight"] <= metrics["toolbarRight"] + 1
    assert metrics["hitReset"]


def test_codemirror_word_wrap_toggle_keeps_existing_content_visible(browser, tmp_path):
    load_codemirror_wrap_toggle_fixture(browser, tmp_path)
    ready = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__wrapRegressionReady.then(
          () => done({ok: true}),
          error => done({ok: false, message: String(error)})
        );
        """
    )
    assert ready["ok"], ready
    before = browser.execute_script(
        """
        const panel = document.getElementById('wrap-regression-panel');
        const content = panel.querySelector('.cm-content');
        return {
          sameView: panel._cmView === window.__wrapRegressionInitialView,
          renderCalls: window.__wrapRegressionRenderCalls,
          doc: panel._cmView.state.doc.toString(),
          visibleText: content.textContent,
          lineWrapping: content.classList.contains('cm-lineWrapping'),
          buttonActive: panel.querySelector('.file-editor-wrap-panel').classList.contains('active'),
          contentHeight: content.getBoundingClientRect().height,
        };
        """
    )
    assert before["sameView"]
    assert before["renderCalls"] == 0
    assert "This line must stay visible" in before["doc"]
    assert "This line must stay visible" in before["visibleText"]
    assert before["lineWrapping"] is False
    assert before["buttonActive"] is False
    assert before["contentHeight"] > 0

    after = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const panel = document.getElementById('wrap-regression-panel');
        panel.querySelector('.file-editor-wrap-panel').click();
        let attempts = 0;
        const finish = () => {
          const content = panel.querySelector('.cm-content');
          const metrics = {
            sameView: panel._cmView === window.__wrapRegressionInitialView,
            renderCalls: window.__wrapRegressionRenderCalls,
            doc: panel._cmView.state.doc.toString(),
            visibleText: content.textContent,
            lineWrapping: Boolean(panel.querySelector('.cm-lineWrapping')),
            panelWrap: panel.classList.contains('editor-wrap'),
            buttonActive: panel.querySelector('.file-editor-wrap-panel').classList.contains('active'),
            contentHeight: content.getBoundingClientRect().height,
            editorClass: panel.querySelector('.cm-editor')?.className || '',
            scrollerClass: panel.querySelector('.cm-scroller')?.className || '',
            contentClass: content.className,
            contentWhiteSpace: getComputedStyle(content).whiteSpace,
            reconfigCalls: window.__wrapRegressionReconfigCalls,
            errors: window.__wrapRegressionErrors,
            optionViews: panel._cmEditorOptionViews?.length || 0,
            loadingText: panel.querySelector('.file-editor-codemirror-panel').textContent,
          };
          if (metrics.lineWrapping || attempts > 20) done(metrics);
          else {
            attempts += 1;
            requestAnimationFrame(finish);
          }
        };
        requestAnimationFrame(finish);
        """
    )
    assert after["sameView"], after
    assert after["renderCalls"] == 0, after
    assert "This line must stay visible" in after["doc"]
    assert "This line must stay visible" in after["visibleText"]
    assert after["lineWrapping"] is True, (
        f"contentClass={after['contentClass']} "
        f"contentWhiteSpace={after['contentWhiteSpace']} "
        f"reconfigCalls={after['reconfigCalls']} "
        f"errors={after['errors']} "
        f"optionViews={after['optionViews']}"
    )
    assert after["panelWrap"] is True
    assert after["buttonActive"] is True
    assert after["contentHeight"] > 0
    assert after["reconfigCalls"], after
    assert after["reconfigCalls"][-1]["result"] is True, after
    assert "cm-lineWrapping" in after["reconfigCalls"][-1]["classes"]
    assert after["errors"] == []
    assert "loading CodeMirror" not in after["loadingText"]


def test_codemirror_bundle_exports_decoration_for_html_semantic_marks(browser, tmp_path):
    load_codemirror_bundle_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const cm = window.YOLOmuxCodeMirror || {};
        const mark = cm.Decoration?.mark?.({attributes: {style: 'font-weight:700'}});
        return {
          hasDecoration: typeof cm.Decoration?.mark === 'function',
          hasDecorationSet: typeof cm.Decoration?.set === 'function',
          hasMergeView: typeof cm.MergeView === 'function',
          hasUnifiedMergeView: typeof cm.unifiedMergeView === 'function',
          markWorks: Boolean(mark && typeof mark.range === 'function'),
        };
        """
    )
    assert metrics["hasDecoration"]
    assert metrics["hasDecorationSet"]
    assert metrics["hasMergeView"]
    assert metrics["hasUnifiedMergeView"]
    assert metrics["markWorks"]


def test_clicking_finder_does_not_change_terminal_pane_toolbar(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    light_metrics = browser.execute_script(
        """
        document.body.classList.add('theme-light');
        const detail = document.querySelector('#terminal-panel .panel-detail-row');
        const meta = detail.querySelector('.meta');
        const action = document.querySelector('#terminal-panel .pane-actions');
        return {
          detailBg: getComputedStyle(detail).backgroundColor,
          metaColor: getComputedStyle(meta).color,
          actionColor: getComputedStyle(action).color,
          actionBg: getComputedStyle(action).backgroundColor,
        };
        """
    )
    # The detail row is the tinted (active-accent-derived) chrome strip with readable dark meta text —
    # assert the readability relationship, not a pinned green, so the active_color picker doesn't break it.
    assert light_metrics["detailBg"] != light_metrics["metaColor"]
    assert light_metrics["metaColor"] == "rgb(31, 41, 55)"
    assert light_metrics["actionColor"] == "rgb(31, 41, 55)"
    assert light_metrics["actionColor"] != light_metrics["actionBg"]
    before = browser.execute_script(
        """
        const toolbar = document.getElementById('terminal-toolbar');
        const rect = toolbar.getBoundingClientRect();
        return {
          html: toolbar.innerHTML,
          display: getComputedStyle(toolbar).display,
          buttonCount: toolbar.querySelectorAll('.tab').length,
          left: rect.left,
          width: rect.width,
        };
        """
    )
    browser.find_element("id", "finder-panel").click()
    after = browser.execute_script(
        """
        const toolbar = document.getElementById('terminal-toolbar');
        const rect = toolbar.getBoundingClientRect();
        return {
          html: toolbar.innerHTML,
          display: getComputedStyle(toolbar).display,
          buttonCount: toolbar.querySelectorAll('.tab').length,
          left: rect.left,
          width: rect.width,
        };
        """
    )
    assert after == before


# DOIT.12 B5 — light-mode surface regression guard. The recurring light-mode bug class is a
# component rule that hardcodes a DARK color literal with no body.theme-light / body.editor-theme-light
# counterpart, so it renders as a dark box (or invisible pale text) on the white surface. The earlier
# white-on-white miss slipped through because the test measured BACKGROUNDS but never the nested TEXT
# color. This builds each fixed surface in light mode and asserts (a) container backgrounds are LIGHT
# and (b) text vs its surface meets a real contrast ratio — the same thing a human reading it needs.
LIGHT_MODE_SURFACES = """
<div class="command-palette-dialog" id="cp-dlg">
  <input class="command-palette-input" id="cp-inp" value="x">
  <button class="command-palette-row active" id="cp-row">
    <span class="command-palette-group" id="cp-grp">FILES</span>
    <span class="command-palette-detail" id="cp-det">detail</span>
    <span class="command-palette-keybinding" id="cp-kb">^P</span>
  </button>
</div>
<div class="keyboard-shortcuts-dialog" id="ks-dlg">
  <div class="keyboard-shortcut-row"><span>act</span><kbd id="ks-kbd">Ctrl</kbd></div>
</div>
<div class="preferences-global-reset" id="gr">
  <div class="preferences-global-reset-title" id="gr-title">Reset</div>
  <div class="preferences-global-reset-warning" id="gr-warn">warn</div>
</div>
<span class="agent-icon codex" id="agent-ico">A</span>
<span class="session-state-badge" id="badge-neutral">run</span>
<span class="session-state-badge session-state-done" id="badge-done">done</span>
<span class="session-yolo-marker inactive" id="ym-inactive">YO</span>
<button class="pane-tab file-missing" id="fm-tab">
  <span class="session-button-dir" id="fm-dir">gone</span>
  <span class="file-tab-missing-badge" id="fm-badge">!</span>
</button>
<div class="server-update-banner" id="sub">
  update <button class="server-update-banner-dismiss" id="sub-dismiss">x</button>
</div>
<div class="file-tree-row repo-non-main"><span class="file-tree-name" id="rnm-name">repo</span></div>
<div class="file-tree-row indexed-directory">
  <span class="file-tree-name" id="idx-name">dir</span>
  <span class="file-tree-git-status" id="idx-status">INDEXED</span>
</div>
<input class="file-tree-rename-input" id="rename-inp" value="name">
<div class="yoagent-message-body markdown-body"><pre id="md-pre"><code>code</code></pre></div>
<div class="info-pane" style="background:var(--bg)">
  <div class="info-row header"><div class="info-cell" id="info-hdr">Session</div></div>
  <div class="info-row"><div class="info-cell" id="info-row-text">main</div>
    <div class="info-cell"><a id="info-link" href="#">branch</a></div></div>
  <div class="info-row current"><div class="info-cell" id="info-cur">current</div></div>
</div>
"""


def light_mode_surfaces_fixture_html(body_class):
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    return f"""
    <!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>
    <body class="{body_class}" style="background:#fff">{LIGHT_MODE_SURFACES}</body></html>
    """


def _contrast_ratio(rgb_a, rgb_b):
    def rel_lum(css_rgb):
        nums = [int(n) for n in re.findall(r"\d+", css_rgb)[:3]]

        def chan(c):
            c = c / 255.0
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * chan(nums[0]) + 0.7152 * chan(nums[1]) + 0.0722 * chan(nums[2])

    la, lb = rel_lum(rgb_a), rel_lum(rgb_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_light_mode_surfaces_are_readable_not_dark_boxes(browser, tmp_path):
    page = tmp_path / "light-surfaces.html"
    page.write_text(light_mode_surfaces_fixture_html("theme-light"), encoding="utf-8")
    browser.get(page.as_uri())
    style = browser.execute_script(
        """
        const out = {};
        for (const el of document.querySelectorAll('[id]')) {
          const s = getComputedStyle(el);
          out[el.id] = {color: s.color, bg: s.backgroundColor};
        }
        return out;
        """
    )

    # (a) Surfaces that were dark boxes must now have LIGHT backgrounds (luminance high).
    def _lum(css_rgb):
        nums = [int(n) for n in re.findall(r"\d+", css_rgb)[:3]]
        return 0.2126 * nums[0] + 0.7152 * nums[1] + 0.0722 * nums[2]

    for box in ("cp-dlg", "ks-dlg", "sub", "rename-inp", "md-pre"):
        assert _lum(style[box]["bg"]) > 180, f"{box} background must be light in light mode, got {style[box]['bg']}"

    # (b) Text must contrast with its surface. Where the element bg is transparent, it sits on the white page.
    page_white = "rgb(255, 255, 255)"
    text_checks = {
        "cp-row": "cp-dlg", "cp-grp": "cp-dlg", "cp-det": "cp-dlg", "cp-kb": "cp-dlg",
        "ks-kbd": "ks-kbd", "gr-title": "gr", "gr-warn": "gr", "agent-ico": None,
        "badge-neutral": "badge-neutral", "badge-done": "badge-done", "ym-inactive": "ym-inactive",
        "fm-dir": "fm-tab", "fm-badge": "fm-tab", "sub": "sub", "sub-dismiss": "sub",
        "rnm-name": None, "idx-name": None, "idx-status": None, "rename-inp": "rename-inp", "md-pre": "md-pre",
        # DOIT.18 C1: the YO!info table — rows/header/current/links must read on the light pane.
        "info-hdr": None, "info-row-text": None, "info-link": None, "info-cur": None,
    }
    for eid, bg_id in text_checks.items():
        bg = style[bg_id]["bg"] if bg_id else page_white
        if "rgba(0, 0, 0, 0)" in bg or bg == "transparent":
            bg = page_white
        ratio = _contrast_ratio(style[eid]["color"], bg)
        assert ratio >= 3.0, f"{eid}: text {style[eid]['color']} on {bg} contrast {ratio:.1f} < 3.0 (dark-box/invisible)"


def test_light_editor_image_backdrop_is_light(browser, tmp_path):
    page = tmp_path / "light-editor-image.html"
    page.write_text(
        light_mode_surfaces_fixture_html("editor-theme-light").replace(
            LIGHT_MODE_SURFACES,
            '<div class="file-editor-image-panel" id="imgp"><img class="file-editor-image" id="img" src="#"></div>',
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri())
    style = browser.execute_script(
        "return {panel: getComputedStyle(document.getElementById('imgp')).backgroundColor,"
        " img: getComputedStyle(document.getElementById('img')).backgroundColor};"
    )

    def _lum(css_rgb):
        nums = [int(n) for n in re.findall(r"\d+", css_rgb)[:3]]
        return 0.2126 * nums[0] + 0.7152 * nums[1] + 0.0722 * nums[2]

    assert _lum(style["panel"]) > 180, f"editor-light image panel must be light, got {style['panel']}"
    assert _lum(style["img"]) > 180, f"editor-light image backdrop must be light, got {style['img']}"


def codemirror_search_panel_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <script src="{bundle_uri}"></script>
        <style>.file-editor-codemirror {{ width: 680px; height: 220px; }}</style>
      </head>
      <body class="editor-theme-light">
        <div class="panel file-editor-panel active-pane">
          <div class="file-editor-content file-editor-codemirror" id="cm-host"></div>
        </div>
        <script>
          (function() {{
            const CM = window.YOLOmuxCodeMirror;
            const exts = CM.search ? [CM.search()] : [];
            const view = new CM.EditorView({{
              state: CM.EditorState.create({{doc: "hello world\\nfind me\\n", extensions: exts}}),
              parent: document.getElementById('cm-host'),
            }});
            CM.openSearchPanel(view);
          }})();
        </script>
      </body>
    </html>
    """


def load_codemirror_search_panel_fixture(browser, tmp_path):
    page = tmp_path / "cm-search-panel.html"
    page.write_text(codemirror_search_panel_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def test_codemirror_search_toggle_labels_collapse_to_glyph_not_overflow(browser, tmp_path):
    # CodeMirror's baseTheme injects `.cm-panel.cm-search label { font-size: 80% }` at RUNTIME, a
    # specificity TIE with our label rule that wins on source order — un-hiding the native toggle
    # text ("match case"/"regexp"/"by word") so it overflows the 24px box and collides with our
    # compact ::after glyph (images 019/021). The +1-class override must keep the label font-size 0.
    load_codemirror_search_panel_fixture(browser, tmp_path)
    labels = browser.execute_script(
        """
        const panel = document.querySelector('.cm-search');
        if (!panel) return null;
        return [...panel.querySelectorAll('label')].map(l => ({
          fontSize: getComputedStyle(l).fontSize,
          boxWidth: Math.round(l.getBoundingClientRect().width),
          scrollWidth: l.scrollWidth,
        }));
        """
    )
    assert labels, "search panel did not open (CodeMirror bundle missing search export?)"
    assert len(labels) == 3
    for lb in labels:
        assert lb["fontSize"] == "0px", f"toggle label native text must be hidden (font-size 0), got {lb['fontSize']}"
        assert lb["scrollWidth"] <= lb["boxWidth"] + 1, f"toggle label overflows its 24px box: {lb}"


def test_needs_attention_pane_stays_red_when_focused_and_yolo_ready(browser, tmp_path):
    # image 20260603-028: focusing/hovering a needs-attention (red) pane on a --dangerously-yolo server
    # made it `typing-ready-pane yolo-ready-pane needs-input-pane`; the yolo-ready green --panel-ring-color
    # (0,3,0) out-specified the needs red (0,2,0), so the alert went GREEN. The red must always win.
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    combos = [
        "needs-input-pane",                                       # unfocused alert -> red (ring)
        "active-pane needs-input-pane",                           # focused alert -> red
        "typing-ready-pane yolo-ready-pane needs-input-pane",     # the bug: hovered + yolo + alert -> red
        "active-pane yolo-ready-pane needs-blocked-pane",
    ]
    panels = "".join(f'<div class="panel {c}" id="p{i}" style="width:160px;height:60px"></div>' for i, c in enumerate(combos))
    page = tmp_path / "needs-ring.html"
    page.write_text(f"<!doctype html><html><head><meta charset=utf-8><style>{css}</style></head>"
                    f'<body class="theme-dark">{panels}</body></html>', encoding="utf-8")
    browser.get(page.as_uri())
    rings = browser.execute_script(
        """
        const out = {};
        document.querySelectorAll('.panel').forEach(p => {
          out[p.id] = getComputedStyle(p).getPropertyValue('--panel-ring-color').trim();
        });
        return out;
        """
    )
    # Every needs-attention pane resolves the red ring color, regardless of focus/yolo-ready state.
    for pid, ring in rings.items():
        assert ring.lower() == "#ff3347", f"{pid}: needs-attention pane must keep the red ring (#ff3347), got {ring}"
