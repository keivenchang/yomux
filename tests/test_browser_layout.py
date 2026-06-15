from pathlib import Path
import difflib
from io import BytesIO
import json
import re
import shutil
import subprocess

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.socket]

pytest.importorskip("selenium")
webdriver = pytest.importorskip("selenium.webdriver")
ActionChains = pytest.importorskip("selenium.webdriver.common.action_chains").ActionChains
Options = pytest.importorskip("selenium.webdriver.chrome.options").Options
WebDriverWait = pytest.importorskip("selenium.webdriver.support.ui").WebDriverWait


REPO_ROOT = Path(__file__).resolve().parents[1]

_APP_CSS_CACHE: str | None = None


def app_css() -> str:
    # The shipped app stylesheet, read once and reused. Every Selenium fixture used to re-read
    # static/yolomux.css from disk (~36 reads per run); the content is identical every time.
    global _APP_CSS_CACHE
    if _APP_CSS_CACHE is None:
        _APP_CSS_CACHE = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    return _APP_CSS_CACHE


def page_html(body: str, *, extra_css: str = "") -> str:
    # One scaffold for the Selenium fixture pages: the app stylesheet, an optional fixture-specific
    # <style> block, and the body. Replaces ~20 near-identical inline `<!doctype>…<style>{css}</style>`
    # copies so a change to the harness shell happens in one place.
    extra = f"\n        <style>\n{extra_css}\n        </style>" if extra_css else ""
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{app_css()}</style>{extra}
      </head>
      <body>
{body}
      </body>
    </html>
    """


def browser_screenshot_rgb(browser):
    image_mod = pytest.importorskip("PIL.Image")
    return image_mod.open(BytesIO(browser.get_screenshot_as_png())).convert("RGB")


def count_rect_region_pixels(image, dpr, rect, region, predicate, *, step=2):
    left = int((rect["left"] + rect["width"] * region["left"]) * dpr)
    top = int((rect["top"] + rect["height"] * region["top"]) * dpr)
    right = int((rect["left"] + rect["width"] * region["right"]) * dpr)
    bottom = int((rect["top"] + rect["height"] * region["bottom"]) * dpr)
    left = max(0, min(image.width - 1, left))
    right = max(left + 1, min(image.width, right))
    top = max(0, min(image.height - 1, top))
    bottom = max(top + 1, min(image.height, bottom))
    stride = max(1, int(step))
    matches = 0
    samples = 0
    for y in range(top, bottom, stride):
        for x in range(left, right, stride):
            pixel = image.getpixel((x, y))
            samples += 1
            if predicate(pixel):
                matches += 1
    return matches, samples


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
    css = app_css()
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
    return page_html(
        f"""
        <div class="panel active-pane">
          <div class="panel-head">
            <div class="tabs" role="tablist">
              <div class="tmux-window-bar" data-tmux-window-label-mode="names">
                <button class="tab tmux-window-button"><span class="tmux-window-name-label">bash</span><span class="tmux-window-number-label">0</span></button>
                <button class="tab tmux-window-button active" aria-pressed="true"><span class="tmux-window-name-label">codex</span><span class="tmux-window-number-label">1</span></button>
                <button class="tab tmux-window-button"><span class="tmux-window-name-label">pytest</span><span class="tmux-window-number-label">2</span></button>
              </div>
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
      """,
        extra_css=f"""
          body {{ margin: 0; padding: 10px; display: block; height: auto; min-height: 0; }}
          .panel {{ width: {width}px; height: 240px; }}
        """,
    )


def menu_fixture_html():
    css = app_css()
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
    return page_html(
        f"""
        <div class="app-menu" data-app-menu="tabs">
          <div class="app-menu-popover" role="menu">{rows}</div>
        </div>
      """,
        extra_css=f"""
          body {{ margin: 0; padding: 10px; display: block; height: auto; min-height: 0; overflow: auto; }}
          .app-menu-popover {{
            visibility: visible;
            opacity: 1;
            pointer-events: auto;
            position: static;
            transform: none;
          }}
        """,
    )


def pc_controls_fixture_html():
    css = app_css()
    return page_html(
        f"""
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
          <div id="repo-dir" class="file-tree-row kind-dir is-repo repo-non-main"><span class="file-tree-icon">▸</span><span class="file-tree-name">yolomux <span class="file-tree-repo-meta">[<span class="file-tree-repo-branch">feature/repo-row</span>]</span></span><span class="file-tree-dir-count" hidden></span><span class="file-tree-agent" hidden></span><span class="file-tree-diff"><span class="changes-diff-add">+5</span> <span class="changes-diff-remove">-3</span></span><span class="file-tree-git-status" hidden></span><span class="file-tree-date" hidden></span></div>
        </div>
        <div id="test-context-menu" class="terminal-context-menu" style="top: 220px; left: 24px;"></div>
        <div id="test-image-preview" class="file-image-preview-popover" style="top: 220px; left: 24px;"></div>
        <div class="pane-tab popover-open" style="position: fixed; top: 220px; left: 24px;">
          <div id="test-tab-popover" class="session-popover"></div>
        </div>
      """,
        extra_css=f"""
          body {{ margin: 0; padding: 24px; display: block; height: auto; min-height: 0; }}
        """,
    )


def topbar_font_fixture_html():
    css = app_css()
    return page_html(
        f"""
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
      """,
        extra_css=f"""
          body {{ margin: 0; padding: 12px; display: block; height: auto; min-height: 0; }}
          :root {{ --ui-font-size: 18px; --tab-label-size: 18px; }}
        """,
    )


def editor_pane_ignores_legacy_body_class_fixture_html():
    css = app_css()
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
    css = app_css()
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
              <button type="button" class="file-editor-diff-panel active">Differ</button>
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
    css = app_css()
    return page_html(
        f"""
        <article class="panel file-editor-panel active-pane">
          <div class="file-editor-toolbar" role="toolbar">
            <div class="file-editor-toolbar-zone file-editor-toolbar-left">
              <button id="gutter-button" type="button" class="file-editor-gutter-panel active" aria-pressed="true">#</button>
              <button id="diff-button" type="button" class="file-editor-diff-panel active" aria-pressed="true">Differ</button>
              <button id="diff-expand-button" type="button" class="file-editor-diff-expand-panel" aria-pressed="true">↕</button>
              <span id="diff-ref-panel" class="file-editor-diff-ref-panel">
                <span class="diff-ref-controls compact" data-diff-ref-controls data-diff-ref-repo="/repo/app">
                  <label class="diff-ref-control">FROM <input id="from-ref" class="diff-ref-input" data-diff-ref-from value="abcdef123"></label>
                  <label class="diff-ref-control">TO <input id="to-ref" class="diff-ref-input" data-diff-ref-to value="current"></label>
                  <button id="reset-ref" type="button" class="diff-ref-reset" data-diff-ref-reset title="Reset" aria-label="Reset">↺</button>
                </span>
              </span>
            </div>
            <div class="file-editor-toolbar-zone file-editor-toolbar-center">
              <span id="font-panel" class="file-editor-preview-font-panel">
                <button type="button">A-</button><span class="file-editor-preview-font-value">16</span><button type="button">A+</button>
              </span>
            </div>
            <div class="file-editor-toolbar-zone file-editor-toolbar-right">
              <div id="mode-control" class="file-editor-mode-control file-editor-mode-control-panel" role="group">
                <button type="button" data-editor-mode="edit"><span class="file-editor-icon file-editor-icon-edit"></span></button>
                <button type="button" data-editor-mode="preview"><span class="file-editor-icon file-editor-icon-eye"></span></button>
                <button type="button" data-editor-mode="split"><span class="file-editor-icon file-editor-icon-split"></span></button>
                <button type="button" class="file-editor-popout-preview-panel"><span class="file-editor-icon file-editor-icon-popout-preview"></span></button>
              </div>
              <button type="button" class="file-editor-wrap-panel active"><span class="file-editor-icon file-editor-icon-wrap"></span></button>
              <button type="button" class="file-editor-find-panel"><span class="file-editor-icon file-editor-icon-find"></span></button>
              <button type="button" class="file-editor-theme-panel"><span class="file-editor-icon file-editor-icon-theme"></span></button>
              <button type="button" class="file-editor-save-panel"><span class="file-editor-icon file-editor-icon-save"></span></button>
            </div>
          </div>
        </article>
      """,
        extra_css=f"""
          body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; }}
          .file-editor-panel {{ width: 520px; height: 120px; }}
        """,
    )


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


def git_show_text(rev_path, fallback_rev_paths=()):
    for candidate in (rev_path, *fallback_rev_paths):
        try:
            return subprocess.check_output(["git", "show", candidate], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            continue
    raise subprocess.CalledProcessError(128, ["git", "show", rev_path])


def codemirror_todo_diff_overview_fixture_html():
    css = app_css()
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
    original = git_show_text("7f5a7e82ce:TODO.md")
    current = git_show_text("HEAD:docs/TODO.md", [":docs/TODO.md", "HEAD:TODO.md"])
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
              const normalizeStopPosition = position => String(Number.parseFloat(position));
              const rangePattern = /(#[0-9a-f]{{6}}|rgb\\(\\s*\\d+\\s*,\\s*\\d+\\s*,\\s*\\d+\\s*\\)|transparent)\\s+([0-9.]+)%\\s+([0-9.]+)%/gi;
              let match = rangePattern.exec(value);
              while (match) {{
                const color = normalizeOverviewColor(match[1]);
                if (color !== 'transparent') {{
                  stops.push({{color, start: normalizeStopPosition(match[2]), end: normalizeStopPosition(match[3])}});
                }}
                match = rangePattern.exec(value);
              }}
              if (stops.length) return stops;
              const tokens = [];
              const tokenPattern = /(#[0-9a-f]{{6}}|rgb\\(\\s*\\d+\\s*,\\s*\\d+\\s*,\\s*\\d+\\s*\\)|transparent)\\s+([0-9.]+)%/gi;
              match = tokenPattern.exec(value);
              while (match) {{
                tokens.push({{color: normalizeOverviewColor(match[1]), pos: normalizeStopPosition(match[2])}});
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
    css = app_css()
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
    css = app_css()
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
    css = app_css()
    return page_html(
        f"""
        <article id="finder-panel" class="panel file-explorer-panel active-pane">
          <div class="panel-head file-explorer-head">
            <div class="pane-tabs" hidden></div>
            <div class="file-explorer-toolbar">
              <div class="file-explorer-toolbar-row file-explorer-primary-row">
                <span class="file-explorer-mode-switcher" role="group" aria-label="Finder / Differ / Tabber">
                  <button type="button" class="file-explorer-mode-toggle" data-file-explorer-mode-set="files" aria-pressed="true"><span class="file-explorer-mode-label">Finder</span></button>
                  <button type="button" class="file-explorer-mode-toggle" data-file-explorer-mode-set="diff" aria-pressed="false"><span class="file-explorer-mode-label">Differ</span></button>
                  <button type="button" class="file-explorer-mode-toggle" data-file-explorer-mode-set="tabber" aria-pressed="false"><span class="file-explorer-mode-label">Tabber</span></button>
                </span>
                <label class="file-explorer-diff-session-control file-explorer-mode-files-diff-only changes-control">Session: <select class="file-explorer-diff-session-select" data-session-files-session><option>project1</option></select></label>
                <span class="file-explorer-toolbar-spacer"></span>
                <div class="tabs pane-frame-controls file-explorer-frame-controls">
                  <button type="button" class="tab pane-close pc-window-control pc-close file-explorer-panel-close"></button>
                </div>
              </div>
              <div class="file-explorer-toolbar-row file-explorer-path-row file-explorer-mode-files-only">
                <button type="button" class="file-explorer-root-mode-toggle file-explorer-root-mode-toggle-panel file-explorer-mode-files-only active" aria-pressed="true">Sync</button>
                <input class="file-explorer-path-inline file-explorer-mode-files-only" value="/home/keivenc/yolomux.dev/static_src/js/yolomux">
                <button type="button" class="path-copy-button file-explorer-path-copy-panel file-explorer-mode-files-only"></button>
              </div>
              <div class="file-explorer-toolbar-row file-explorer-actions-row file-explorer-mode-files-only">
                <button type="button" class="file-explorer-header-action file-explorer-mode-files-only" id="new-file" data-file-explorer-new-file>+</button>
                <button type="button" class="file-explorer-header-action file-explorer-folder-action file-explorer-mode-files-only" data-file-explorer-new-folder><span class="file-explorer-folder-icon" aria-hidden="true"></span></button>
                <span class="file-explorer-toolbar-spacer"></span>
                <button type="button" class="file-explorer-hidden-toggle file-explorer-hidden-toggle-panel file-explorer-mode-files-only">.*</button>
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
            const nextMode = button.dataset.fileExplorerModeSet;
            document.body.classList.toggle('file-explorer-mode-diff', nextMode === 'diff');
            document.body.classList.toggle('file-explorer-mode-files', nextMode === 'files');
            document.body.classList.toggle('file-explorer-mode-tabber', nextMode === 'tabber');
            document.getElementById('finder-panel').dataset.fileExplorerMode = nextMode;
            document.querySelectorAll('[data-file-explorer-mode-set]').forEach(toggle => {{
              toggle.setAttribute('aria-pressed', toggle.dataset.fileExplorerModeSet === nextMode ? 'true' : 'false');
            }});
          }}));
        </script>
      """,
        extra_css=f"""
          body {{ margin: 0; padding: 8px; display: grid; grid-template-columns: 420px 1fr; gap: 8px; height: auto; min-height: 0; }}
          .panel {{ height: 230px; }}
        """,
    )


def file_tree_status_alignment_fixture_html():
    css = app_css()
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
    css = app_css()
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
    css = app_css()
    return page_html(
        f"""
        <div class="layout-root split-column">
          <section class="layout-column"><article id="top-panel" class="panel"></article></section>
          <div id="split-resizer" class="layout-resizer resizer-column"></div>
          <section class="layout-column"><article id="bottom-panel" class="panel"></article></section>
        </div>
      """,
        extra_css=f"""
          body {{ margin: 0; padding: 8px; display: block; height: auto; min-height: 0; }}
          .layout-root {{ width: 420px; height: 360px; }}
        """,
    )


def live_runtime_boot_fixture_html(settings=None, transcript_current_path="/home/test/yolomux.dev", transcript_git_root="/home/test/yolomux.dev", session_files_payload=None, fs_entries=None, sessions=None, transcript_sessions=None, session_files_payloads=None, terminal_css=".terminal { width: 720px; height: 360px; }", grid_width=1000, grid_height=620, file_explorer_open_intent=None, auto_approve_payload=None):
    css = app_css()
    brand_css = (REPO_ROOT / "static" / "brand.css").read_text(encoding="utf-8")
    script_uri = (REPO_ROOT / "static" / "yolomux.js").as_uri()
    dockview_css_uri = (REPO_ROOT / "static" / "vendor" / "dockview.css").as_uri()
    dockview_script_uri = (REPO_ROOT / "static" / "vendor" / "dockview-core.noStyle.js").as_uri()
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
        # the real page inlines the active locale catalog so t() resolves on the first render
        # (the menu bar paints at boot). Mirror that here so the live-boot menu shows real labels.
        "locale": "en",
        "strings": {"en": json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text())},
    }
    file_explorer_intent_script = ""
    if file_explorer_open_intent is not None:
        file_explorer_intent_script = f"""
          try {{ sessionStorage.setItem('yolomux.fileExplorerOpen.v1', {json.dumps(str(file_explorer_open_intent))}); }} catch (error) {{}}
        """
    stub_script = """
      window.__bootErrors = [];
      window.__bootRejections = [];
      window.__bootFetches = [];
      window.__bootSockets = [];
      window.__eventSources = [];
      window.__terminalOpened = 0;
      window.__settingsMtime = 0;
      window.__settingsPayload = JSON.parse(document.getElementById('yolomux-bootstrap').textContent).settingsPayload;
      window.addEventListener('error', event => window.__bootErrors.push({
        message: event.message || String(event.error || event),
        filename: event.filename || '',
        lineno: event.lineno || 0,
        colno: event.colno || 0,
        stack: event.error?.stack || '',
      }));
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
        constructor(url) { this.url = String(url); this.listeners = {}; window.__eventSources.push(this); }
        addEventListener(name, callback) { this.listeners[name] = callback; }
        emit(name, payload = {}) { this.listeners[name]?.({data: JSON.stringify(payload)}); }
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
        const body = options.body ? JSON.parse(options.body || '{}') : null;
        window.__bootFetches.push({path: url.pathname, search: url.search, method: options.method || 'GET', body});
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
          return jsonResponse(window.__fixtureAutoApprovePayload || {
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
                agents: info.agents || [],
                panes: info.panes || [],
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
        if (url.pathname === '/api/tmux-window') return jsonResponse({ok: true, session: url.searchParams.get('session'), window: url.searchParams.get('window')});
        if (url.pathname === '/api/fs/list') {
          const path = url.searchParams.get('path') || '/home/test';
          const entries = (window.__fixtureFsEntries || {})[path] || [];
          return jsonResponse({path, entries});
        }
        if (url.pathname === '/api/fs/batch') {
          const entriesByPath = window.__fixtureFsEntries || {};
          const responses = (body?.requests || []).map((request, index) => {
            const path = request.path || '/home/test';
            if (request.type === 'list') {
              return {id: request.id ?? index, ok: true, status: 200, payload: {path, entries: entriesByPath[path] || []}};
            }
            if (request.type === 'info') {
              return {id: request.id ?? index, ok: true, status: 200, payload: {path, name: path.split('/').filter(Boolean).pop() || '/', kind: entriesByPath[path] ? 'dir' : 'file'}};
            }
            return {id: request.id ?? index, ok: false, status: 400, error: 'unsupported fs batch operation', path};
          });
          return jsonResponse({responses});
        }
        return jsonResponse({});
      };
    """
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <link rel="stylesheet" href="{dockview_css_uri}">
        <style>{css}</style>
        <style>{brand_css}</style>
        <style>
          body {{ margin: 0; }}
          #grid {{ width: {grid_width}px; height: {grid_height}px; }}
          {terminal_css}
        </style>
      </head>
      <body>
        <header class="topbar">
          <div class="brand-cell"><div class="brand brand-title title" aria-label="YOLOmux test"><span class="brand-yolo brand-green">YO</span><span class="brand-lo brand-green">LO</span><span class="brand-blue">m</span><span class="brand-red">u</span><span class="brand-yellow">x</span><span class="brand-version">test</span></div><span id="httpsWarning" class="transport-warning" hidden></span></div>
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
          window.__fixtureAutoApprovePayload = {json.dumps(auto_approve_payload, separators=(",", ":")) if auto_approve_payload is not None else "null"};
        </script>
        <script>{file_explorer_intent_script}</script>
        <script>{stub_script}</script>
        <script src="{dockview_script_uri}"></script>
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


def load_live_runtime_boot_fixture(browser, tmp_path, search="", **fixture_kwargs):
    page = tmp_path / "live-runtime-boot.html"
    page.write_text(live_runtime_boot_fixture_html(**fixture_kwargs), encoding="utf-8")
    browser.get(page.as_uri() + search)


def load_dockview_runtime_boot_fixture(browser, tmp_path, search="", **fixture_kwargs):
    browser.set_window_size(1200, 700)
    fixture_kwargs.setdefault("file_explorer_open_intent", "0")
    load_live_runtime_boot_fixture(browser, tmp_path, search, **fixture_kwargs)


def wait_for_dockview(browser, min_tabs=1):
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof dockviewLayoutActive === 'function'
              && dockviewLayoutActive()
              && document.querySelectorAll('.dockview-pane-tab').length >= arguments[0];
            """,
            min_tabs,
        )
    )


def wait_for_dockview_tab_geometry(browser, min_tabs=1, min_width=150, max_rows=None, min_rows=None):
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const tabs = Array.from(document.querySelectorAll('.dockview-pane-tab'));
            if (tabs.length < arguments[0]) return false;
            const rects = tabs.map(tab => tab.getBoundingClientRect());
            if (rects.some(rect => rect.width < arguments[1] || rect.height <= 0)) return false;
            const tops = new Set(rects.map(rect => Math.round(rect.top)));
            if (arguments[2] !== null) {
              if (tops.size > arguments[2]) return false;
            }
            if (arguments[3] !== null && tops.size < arguments[3]) return false;
            return true;
            """,
            min_tabs,
            min_width,
            max_rows,
            min_rows,
        )
    )


def wait_for_visible_panel(browser, panel_id):
    return WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const panel = document.getElementById(arguments[0]);
            if (!panel) return false;
            const rect = panel.getBoundingClientRect();
            const style = getComputedStyle(panel);
            if (rect.width <= 0 || rect.height <= 0 || style.display === 'none' || style.visibility === 'hidden') return false;
            return {
              x: Math.round(rect.left + rect.width / 2),
              y: Math.round(rect.top + rect.height / 2),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            };
            """,
            panel_id,
        )
    )


def wait_for_visible_selector(browser, selector):
    return WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const node = document.querySelector(arguments[0]);
            if (!node) return false;
            node.scrollIntoView?.({block: 'center', inline: 'center'});
            const rect = node.getBoundingClientRect();
            const style = getComputedStyle(node);
            if (rect.width <= 0 || rect.height <= 0 || style.display === 'none' || style.visibility === 'hidden') return false;
            return {
              x: Math.round(rect.left + rect.width / 2),
              y: Math.round(rect.top + rect.height / 2),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            };
            """,
            selector,
        )
    )


def click_visible_panel(browser, panel_id):
    point = wait_for_visible_panel(browser, panel_id)
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": point["x"], "y": point["y"], "button": "none"})
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": point["x"], "y": point["y"], "button": "left", "buttons": 1, "clickCount": 1})
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": point["x"], "y": point["y"], "button": "left", "buttons": 0, "clickCount": 1})
    browser.execute_script(
        """
        const panel = document.getElementById(arguments[0]);
        if (!panel) return;
        const eventInit = {bubbles: true, cancelable: true, clientX: arguments[1], clientY: arguments[2], button: 0, buttons: 1};
        panel.dispatchEvent(new PointerEvent('pointerdown', {...eventInit, pointerId: 1, pointerType: 'mouse'}));
        panel.dispatchEvent(new PointerEvent('pointerup', {...eventInit, buttons: 0, pointerId: 1, pointerType: 'mouse'}));
        panel.dispatchEvent(new MouseEvent('click', {...eventInit, buttons: 0}));
        """,
        panel_id,
        point["x"],
        point["y"],
    )


def click_visible_selector(browser, selector):
    wait_for_visible_selector(browser, selector)
    browser.execute_script(
        """
        const node = document.querySelector(arguments[0]);
        if (!node) return;
        const rect = node.getBoundingClientRect();
        const clientX = Math.round(rect.left + rect.width / 2);
        const clientY = Math.round(rect.top + rect.height / 2);
        const eventInit = {bubbles: true, cancelable: true, clientX, clientY, button: 0, buttons: 1};
        node.dispatchEvent(new PointerEvent('pointerdown', {...eventInit, pointerId: 1, pointerType: 'mouse'}));
        node.dispatchEvent(new PointerEvent('pointerup', {...eventInit, buttons: 0, pointerId: 1, pointerType: 'mouse'}));
        node.dispatchEvent(new MouseEvent('click', {...eventInit, buttons: 0}));
        """,
        selector,
    )


def move_to_visible_panel(browser, panel_id):
    point = wait_for_visible_panel(browser, panel_id)
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": point["x"], "y": point["y"], "button": "none"})


def move_to_visible_selector(browser, selector):
    point = wait_for_visible_selector(browser, selector)
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": point["x"], "y": point["y"], "button": "none"})


def dockview_point(browser, selector, x_ratio=0.5, y_ratio=0.5):
    return browser.execute_script(
        """
        const node = document.querySelector(arguments[0]);
        if (!node) return null;
        const rect = node.getBoundingClientRect();
        return {
          x: Math.round(rect.left + rect.width * arguments[1]),
          y: Math.round(rect.top + rect.height * arguments[2]),
          width: rect.width,
          height: rect.height,
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
        };
        """,
        selector,
        x_ratio,
        y_ratio,
    )


def cdp_drag(browser, start, end, steps=24):
    assert start and end
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": start["x"], "y": start["y"], "button": "left", "buttons": 0, "clickCount": 1})
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": start["x"], "y": start["y"], "button": "none"})
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": start["x"], "y": start["y"], "button": "left", "buttons": 1, "clickCount": 1})
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(done));
        """
    )
    for index in range(1, steps + 1):
        x = round(start["x"] + (end["x"] - start["x"]) * index / steps)
        y = round(start["y"] + (end["y"] - start["y"]) * index / steps)
        browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "left", "buttons": 1})
        if index % 4 == 0:
            browser.execute_async_script(
                """
                const done = arguments[arguments.length - 1];
                requestAnimationFrame(done);
                """
            )
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(done))));
        """
    )
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": end["x"], "y": end["y"], "button": "left", "buttons": 1})
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(done));
        """
    )
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": end["x"], "y": end["y"], "button": "left", "buttons": 0, "clickCount": 1})


def cdp_drag_hold(browser, start, end, steps=24):
    assert start and end
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": start["x"], "y": start["y"], "button": "left", "buttons": 0, "clickCount": 1})
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": start["x"], "y": start["y"], "button": "none"})
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": start["x"], "y": start["y"], "button": "left", "buttons": 1, "clickCount": 1})
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(done));
        """
    )
    for index in range(1, steps + 1):
        x = round(start["x"] + (end["x"] - start["x"]) * index / steps)
        y = round(start["y"] + (end["y"] - start["y"]) * index / steps)
        browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "left", "buttons": 1})
        if index % 4 == 0:
            browser.execute_async_script(
                """
                const done = arguments[arguments.length - 1];
                requestAnimationFrame(done);
                """
            )
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(done)));
        """
    )


def cdp_release(browser, point):
    browser.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": point["x"], "y": point["y"], "button": "left", "buttons": 0, "clickCount": 1})


def dockview_invalid_drop_preview(browser):
    return browser.execute_script(
        """
        const visible = node => {
          const rect = node.getBoundingClientRect();
          const style = getComputedStyle(node);
          return rect.width > 0
            && rect.height > 0
            && style.display !== 'none'
            && style.visibility !== 'hidden'
            && style.opacity !== '0';
        };
        const previews = Array.from(document.querySelectorAll('.dv-drop-target-selection, .dv-drop-target-anchor'))
          .filter(visible)
          .map(node => {
            const style = getComputedStyle(node);
            return {
              className: node.className,
              borderColor: style.borderTopColor,
              borderStyle: style.borderTopStyle,
            };
          });
        const swatch = document.createElement('span');
        swatch.style.color = 'var(--danger-border)';
        document.body.appendChild(swatch);
        const dangerColor = getComputedStyle(swatch).color;
        swatch.remove();
        return {
          previews,
          dangerColor,
          invalidPreview: document.querySelector('.yolomux-dockview')?.classList.contains('dockview-invalid-tab-drop-preview') || false,
        };
        """
    )


def dockview_layout_metrics(browser):
    return browser.execute_script(
        """
        const tabItem = tab => tab.dataset.paneTab || '';
        const groups = Array.from(document.querySelectorAll('.dv-groupview')).map(group => {
          const rect = group.getBoundingClientRect();
          return {
            tabs: Array.from(group.querySelectorAll('.dockview-pane-tab')).map(tabItem),
            active: group.querySelector('.dv-tab.dv-active-tab .dockview-pane-tab')?.dataset?.paneTab || '',
            rect: {
              left: Math.round(rect.left),
              right: Math.round(rect.right),
              top: Math.round(rect.top),
              bottom: Math.round(rect.bottom),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            },
          };
        });
        const tabStyles = Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => ({
          item: tabItem(tab),
          active: tab.closest('.dv-tab')?.classList?.contains('dv-active-tab') || false,
          bg: getComputedStyle(tab).backgroundColor,
          color: getComputedStyle(tab).color,
          borderTopLeftRadius: getComputedStyle(tab).borderTopLeftRadius,
          borderTopRightRadius: getComputedStyle(tab).borderTopRightRadius,
          rect: (() => {
            const rect = tab.getBoundingClientRect();
            return {
              left: Math.round(rect.left),
              right: Math.round(rect.right),
              top: Math.round(rect.top),
              bottom: Math.round(rect.bottom),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            };
          })(),
        }));
        return {
          groups,
          tabStyles,
          header: (() => {
            const container = document.querySelector('.dv-tabs-and-actions-container');
            const tabs = document.querySelector('.dv-tabs-container');
            const allTabs = Array.from(document.querySelectorAll('.dockview-pane-tab'));
            const activeTab = allTabs[0];
            const containerRect = container?.getBoundingClientRect?.();
            const activeRect = activeTab?.getBoundingClientRect?.();
            return {
              height: containerRect ? Math.round(containerRect.height) : 0,
              tabsHeight: tabs ? Math.round(tabs.getBoundingClientRect().height) : 0,
              tabsScrollWidth: tabs ? Math.round(tabs.scrollWidth) : 0,
              tabsClientWidth: tabs ? Math.round(tabs.clientWidth) : 0,
              tabsOverflowX: tabs ? getComputedStyle(tabs).overflowX : '',
              tabsScrollbarWidth: tabs ? getComputedStyle(tabs).scrollbarWidth : '',
              tabsWebkitScrollbarDisplay: tabs ? getComputedStyle(tabs, '::-webkit-scrollbar').display : '',
              tabsWebkitScrollbarHeight: tabs ? getComputedStyle(tabs, '::-webkit-scrollbar').height : '',
              activeTabInsideHeader: Boolean(containerRect && activeRect && activeRect.top >= containerRect.top && activeRect.bottom <= containerRect.bottom),
              allTabsInsideHeader: Boolean(containerRect && allTabs.every(tab => {
                const rect = tab.getBoundingClientRect();
                return rect.top >= containerRect.top && rect.bottom <= containerRect.bottom;
              })),
            };
          })(),
          slots: JSON.parse(JSON.stringify(layoutSlots)),
          url: location.search,
          errors: window.__bootErrors || [],
          rejections: window.__bootRejections || [],
        };
        """
    )


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


def test_client_events_ready_refetches_yolo_marker_after_reconnect(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        sessions=["1"],
        auto_approve_payload={
            "session_order": ["1"],
            "sessions": {"1": {"target": "1", "enabled": False, "last_action": "off", "screen": {"key": "idle"}}},
            "rules": {"path": "/home/test/.config/yolomux/yolo-rules.yaml", "source": "default", "rules": [], "errors": []},
        },
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return window.__terminalOpened >= 1 && document.querySelector('[data-yolo-session=\"1\"]') !== null"
        )
    )
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const markerBefore = document.querySelector('[data-yolo-session="1"]');
          const source = (window.__eventSources || []).find(item => item.url === '/api/client-events');
          if (!markerBefore || !source) return {error: 'missing marker or client-events source'};
          const beforeWorking = markerBefore.classList.contains('working');
          window.__fixtureAutoApprovePayload = {
            session_order: ['1'],
            sessions: {'1': {target: '1', enabled: false, last_action: 'off', screen: {key: 'working'}}},
            rules: {path: '/home/test/.config/yolomux/yolo-rules.yaml', source: 'default', rules: [], errors: []},
          };
          clientEventsConnected = false;
          source.emit('ready');
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 90; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => document.querySelector('[data-yolo-session="1"]')?.classList.contains('working'));
          const markerAfter = document.querySelector('[data-yolo-session="1"]');
          return {
            beforeWorking,
            ready,
            connected: clientEventsConnected,
            className: markerAfter?.className || '',
            autoApproveFetches: window.__bootFetches.filter(item => item.path === '/api/auto-approve').length,
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in result, result
    assert result["beforeWorking"] is False, result
    assert result["ready"] is True, result
    assert result["connected"] is True, result
    assert result["autoApproveFetches"] >= 2, result
    assert result["errors"] == []
    assert result["rejections"] == []


def test_share_viewer_banner_does_not_displace_main_grid(browser, tmp_path):
    page = tmp_path / "share-viewer-banner-grid.html"
    page.write_text(
        page_html(
            """
        <script>document.body.classList.add('share-view-mode');</script>
        <div id="appRoot" class="app-root">
          <div class="topbar">
            <div class="brand-cell"><span class="brand">YOLOmux</span></div>
          </div>
          <div id="grid" class="grid dockview-grid">
            <div class="yolomux-dockview">
              <div class="panel active-pane"><div class="tab-pane active"><div class="terminal"></div></div></div>
            </div>
          </div>
        </div>
        <div class="share-viewer-banner" role="status">Viewing keivenc's session - read-only - expires in 9:23</div>
      """
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const root = document.getElementById('appRoot');
        const banner = document.querySelector('.share-viewer-banner');
        const grid = document.getElementById('grid');
        const dock = document.querySelector('.yolomux-dockview');
        const rect = el => {
          const r = el.getBoundingClientRect();
          return {top: Math.round(r.top), height: Math.round(r.height), width: Math.round(r.width)};
        };
        return {
          root: rect(root),
          banner: rect(banner),
          bannerPosition: getComputedStyle(banner).position,
          grid: rect(grid),
          dock: rect(dock),
        };
        """
    )
    assert metrics["bannerPosition"] == "fixed"
    assert metrics["banner"]["height"] < 60
    assert metrics["root"]["top"] == 0
    assert metrics["grid"]["top"] < 60
    assert metrics["grid"]["height"] > 500
    assert metrics["dock"]["height"] > 500


def test_share_mirror_root_transform_and_bundled_fonts_render_in_browser(browser, tmp_path):
    shutil.copyfile(REPO_ROOT / "static" / "fonts" / "yolomux-ui.woff2", tmp_path / "yolomux-ui.woff2")
    shutil.copyfile(REPO_ROOT / "static" / "fonts" / "yolomux-mono.woff2", tmp_path / "yolomux-mono.woff2")
    css = app_css().replace("/static/fonts/yolomux-ui.woff2", "yolomux-ui.woff2").replace("/static/fonts/yolomux-mono.woff2", "yolomux-mono.woff2")
    page = tmp_path / "share-mirror-root-fonts.html"
    page.write_text(
        f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
      </head>
      <body>
        <script>document.body.classList.add('share-view-mode');</script>
        <div id="shareMirrorStage" class="share-mirror-stage">
          <div id="appRoot" class="app-root" style="--app-root-width:1440px; --app-root-height:900px; --share-mirror-scale:0.5; --share-mirror-tx:20px; --share-mirror-ty:30px;">
            <button id="mirrorTarget" style="position:absolute; left:100px; top:80px; width:120px; height:40px;">Tabs</button>
            <div class="share-popup-mirror-layer">
              <div id="mirrorPopupItem" class="share-popup-mirror-item" style="left:200px; top:160px; width:240px; height:80px;">
                <div id="mirrorPopupInner" class="app-menu-popover" style="width:240px; height:80px;">YO!share</div>
              </div>
            </div>
          </div>
        </div>
        <div class="share-viewer-banner share-mode-read" role="status"><span class="share-viewer-mirror-status match">mirror ✓</span></div>
      </body>
    </html>
      """,
        encoding="utf-8",
    )
    browser.get(page.as_uri())
    metrics = browser.execute_async_script(
        """
        const done = arguments[0];
        Promise.all([
          document.fonts.load('13px "YOLOmux UI"'),
          document.fonts.load('13px "YOLOmux Mono"'),
        ]).then(() => {
          const root = document.getElementById('appRoot').getBoundingClientRect();
          const target = document.getElementById('mirrorTarget').getBoundingClientRect();
          const popupItem = document.getElementById('mirrorPopupItem').getBoundingClientRect();
          const popupInner = document.getElementById('mirrorPopupInner').getBoundingClientRect();
          const popupStyle = getComputedStyle(document.getElementById('mirrorPopupInner'));
          const mirror = document.querySelector('.share-viewer-mirror-status');
          const mirrorStyle = getComputedStyle(mirror);
          done({
            root: {left: Math.round(root.left), top: Math.round(root.top), width: Math.round(root.width), height: Math.round(root.height)},
            target: {left: Math.round(target.left), top: Math.round(target.top), width: Math.round(target.width), height: Math.round(target.height)},
            popupItem: {left: Math.round(popupItem.left), top: Math.round(popupItem.top), width: Math.round(popupItem.width), height: Math.round(popupItem.height)},
            popupInner: {left: Math.round(popupInner.left), top: Math.round(popupInner.top), width: Math.round(popupInner.width), height: Math.round(popupInner.height)},
            popupDisplay: popupStyle.display,
            popupVisibility: popupStyle.visibility,
            popupOpacity: popupStyle.opacity,
            uiFontLoaded: document.fonts.check('13px "YOLOmux UI"'),
            monoFontLoaded: document.fonts.check('13px "YOLOmux Mono"'),
            mirrorColor: mirrorStyle.color,
          });
        }).catch(error => done({error: String(error)}));
        """
    )
    assert metrics.get("error") is None, metrics
    assert metrics["root"] == {"left": 20, "top": 30, "width": 720, "height": 450}
    assert metrics["target"] == {"left": 70, "top": 70, "width": 60, "height": 20}
    assert metrics["popupItem"] == {"left": 120, "top": 110, "width": 120, "height": 40}
    assert metrics["popupInner"] == metrics["popupItem"]
    assert metrics["popupDisplay"] != "none"
    assert metrics["popupVisibility"] == "visible"
    assert float(metrics["popupOpacity"]) == 1
    assert metrics["uiFontLoaded"] is True
    assert metrics["monoFontLoaded"] is True
    assert metrics["mirrorColor"].startswith("rgb(")


def test_share_status_modes_use_distinct_browser_colors(browser, tmp_path):
    page = tmp_path / "share-status-mode-colors.html"
    page.write_text(
        page_html(
            """
        <button class="share-status-pill share-mode-read">YO!share: 1 viewers · 9:23 left</button>
        <button class="share-status-pill share-mode-write">YO!share: 1 viewers · 9:23 left</button>
        <div class="share-viewer-banner share-mode-read">Viewing keivenc's session - read-only - expires in 9:23</div>
        <div class="share-viewer-banner share-mode-write">Viewing keivenc's session - write - expires in 9:23</div>
      """
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const styleFor = selector => {
          const style = getComputedStyle(document.querySelector(selector));
          return {background: style.backgroundColor, border: style.borderColor};
        };
        return {
          readPill: styleFor('.share-status-pill.share-mode-read'),
          writePill: styleFor('.share-status-pill.share-mode-write'),
          readBanner: styleFor('.share-viewer-banner.share-mode-read'),
          writeBanner: styleFor('.share-viewer-banner.share-mode-write'),
        };
        """
    )
    assert metrics["readPill"]["background"] != metrics["writePill"]["background"]
    assert metrics["readPill"]["border"] != metrics["writePill"]["border"]
    assert metrics["readBanner"]["background"] != metrics["writeBanner"]["background"]
    assert metrics["readBanner"]["border"] != metrics["writeBanner"]["border"]


def test_share_modal_copy_icon_sits_beside_url(browser, tmp_path):
    page = tmp_path / "share-modal-copy-icon.html"
    page.write_text(
        page_html(
            """
        <section id="modal" class="modal open share-open">
          <div class="modal-head"><div id="modalTitle">YO!share</div><button id="closeModal">Close</button></div>
          <div id="modalBody">
            <section class="share-entry">
              <label class="share-field share-url-field">
                <span>URL</span>
                <span class="share-url-control">
                  <input type="text" readonly value="https://localhost:8001/share/share123">
                  <button type="button" class="path-copy-button share-url-copy-button" data-share-copy title="Copy" aria-label="Copy"></button>
                </span>
              </label>
              <div class="share-actions">
                <button type="button" class="danger" data-share-stop>Stop</button>
              </div>
            </section>
          </div>
        </section>
      """
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const control = document.querySelector('.share-url-control');
        const input = control.querySelector('input');
        const copy = control.querySelector('[data-share-copy]');
        const actions = document.querySelector('.share-actions');
        const rect = el => {
          const r = el.getBoundingClientRect();
          return {left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom), width: Math.round(r.width), height: Math.round(r.height)};
        };
        return {
          control: rect(control),
          input: rect(input),
          copy: rect(copy),
          copyText: copy.textContent.trim(),
          copyTitle: copy.getAttribute('title'),
          actionCopyCount: actions.querySelectorAll('[data-share-copy]').length,
          stopCount: actions.querySelectorAll('[data-share-stop]').length,
        };
        """
    )
    assert metrics["copyText"] == ""
    assert metrics["copyTitle"] == "Copy"
    assert metrics["actionCopyCount"] == 0
    assert metrics["stopCount"] == 1
    assert metrics["input"]["right"] <= metrics["copy"]["left"]
    assert metrics["copy"]["right"] <= metrics["control"]["right"]
    assert metrics["copy"]["width"] == 30
    assert abs(((metrics["input"]["top"] + metrics["input"]["bottom"]) / 2) - ((metrics["copy"]["top"] + metrics["copy"]["bottom"]) / 2)) <= 1


def test_share_modal_users_section_is_inline_not_nested_card(browser, tmp_path):
    page = tmp_path / "share-modal-users-inline.html"
    page.write_text(
        page_html(
            """
        <section id="modal" class="modal open share-open">
          <div class="modal-head"><div id="modalTitle">YO!share</div><button id="closeModal">Close</button></div>
          <div id="modalBody">
            <div class="share-modal-form">
              <div class="share-active-panel">
                <div class="share-section-title">Active share URLs (1)</div>
                <div class="share-entry-list">
                  <section class="share-entry share-mode-read">
                    <div class="share-entry-heading">
                      <strong>5 · read-only · http</strong>
                      <span>id UKeiu2G-</span>
                    </div>
                    <label class="share-field share-url-field">
                      <span class="share-url-control">
                        <input type="text" readonly value="http://localhost:8001/share/UKeiu2G-#t=ZD5">
                        <button type="button" class="path-copy-button share-url-copy-button" title="Copy" aria-label="Copy"></button>
                      </span>
                    </label>
                    <div class="share-result-meta">
                      <span>expires in 99:55</span>
                      <span>0/5 viewers</span>
                      <span>read-only</span>
                      <span>http</span>
                      <button type="button" class="share-extend-button">+10 min</button>
                      <button type="button" class="danger share-stop-inline">Stop sharing</button>
                    </div>
                    <div class="share-users">
                      <div class="share-users-title">Users (0)</div>
                      <div class="share-users-table" role="table" aria-label="Users (0)">
                        <div class="share-users-row header" role="row">
                          <span role="columnheader">Connected</span>
                          <span role="columnheader">IP</span>
                          <span role="columnheader">Browser</span>
                        </div>
                        <div class="share-users-empty">No connected users</div>
                      </div>
                    </div>
                  </section>
                </div>
              </div>
            </div>
          </div>
        </section>
      """
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri())
    metrics = browser.execute_script(
        """
        const styles = selector => {
          const style = getComputedStyle(document.querySelector(selector));
          return {
            background: style.backgroundColor,
            borderTopWidth: style.borderTopWidth,
            borderRightWidth: style.borderRightWidth,
            borderBottomWidth: style.borderBottomWidth,
            borderLeftWidth: style.borderLeftWidth,
            boxShadow: style.boxShadow,
          };
        };
        const rect = selector => {
          const r = document.querySelector(selector).getBoundingClientRect();
          return {left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width)};
        };
        return {
          modal: styles('.modal.share-open'),
          entry: styles('.share-entry'),
          table: styles('.share-users-table'),
          header: styles('.share-users-row.header'),
          empty: styles('.share-users-empty'),
          usersRect: rect('.share-users'),
          tableRect: rect('.share-users-table'),
        };
        """
    )
    assert metrics["modal"]["borderTopWidth"] == "1px"
    assert metrics["modal"]["borderLeftWidth"] == "1px"
    assert "-4px" not in metrics["modal"]["boxShadow"]
    assert metrics["entry"]["borderTopWidth"] == "1px"
    assert metrics["table"]["borderTopWidth"] == "0px"
    assert metrics["table"]["borderRightWidth"] == "0px"
    assert metrics["table"]["borderBottomWidth"] == "0px"
    assert metrics["table"]["borderLeftWidth"] == "0px"
    assert metrics["table"]["background"] == "rgba(0, 0, 0, 0)"
    assert metrics["header"]["background"] == "rgba(0, 0, 0, 0)"
    assert metrics["header"]["borderTopWidth"] == "1px"
    assert metrics["empty"]["borderTopWidth"] == "1px"
    assert abs(metrics["usersRect"]["left"] - metrics["tableRect"]["left"]) <= 1
    assert abs(metrics["usersRect"]["right"] - metrics["tableRect"]["right"]) <= 1


def test_share_readonly_diff_scroll_and_popup_mirror_are_host_owned(browser, tmp_path):
    css = app_css()
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    strings = json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text(encoding="utf-8"))
    bootstrap = json.dumps(
        {
            "sessions": ["1"],
            "availableAgents": ["term"],
            "accessRole": "readonly",
            "homePath": "/home/test",
            "repoRoot": "/repo/app",
            "maxSessionTabs": 99,
            "serverHostname": "test-host",
            "strings": {"en": strings},
            "codeMirrorAssetUrl": bundle_uri,
            "share": {"view": True, "id": "share68", "mode": "ro", "session": "1", "sessions": ["1"]},
        },
        separators=(",", ":"),
    )
    path = "/repo/app/test_app.py"
    original_lines = [f"line {index:03d}: stable content\n" for index in range(1, 181)]
    current_lines = original_lines[:]
    current_lines[118] = "line 119: host diff changed this line\n"
    current_lines.insert(119, "line 120: host diff inserted this line\n")
    original = "".join(original_lines)
    current = "".join(current_lines)
    diff = "diff --git a/test_app.py b/test_app.py\n" + "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile="a/test_app.py",
            tofile="b/test_app.py",
        )
    )
    payload = json.dumps(
        {
            "diff": diff,
            "original": original,
            "working": current,
            "repo": "/repo/app",
            "relative_path": "test_app.py",
            "from_ref": "HEAD",
            "to_ref": "current",
            "untracked": False,
            "working_missing": False,
        },
        separators=(",", ":"),
    )
    page = tmp_path / "share-readonly-diff-scroll-popup.html"
    page.write_text(
        f"""<!doctype html><html><head><meta charset=utf-8><style>{css}</style><script src="{bundle_uri}"></script>
        <style>
        body {{ margin: 0; padding: 0; display: block; height: auto; min-height: 0; background: #10151d; }}
        #appRoot {{ position: relative; width: 1120px; height: 720px; }}
        #mount {{ width: 940px; height: 640px; padding: 24px; }}
        .file-editor-panel {{ width: 920px; height: 600px; }}
        .file-editor-codemirror-panel {{ height: 100%; }}
        </style></head>
        <body class="theme-dark theme-resolved-dark editor-theme-dark share-view-mode">
          <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
          <div id="appRoot" class="app-root">
            <div id="fileExplorer" class="file-explorer-panel">
              <input id="fileExplorerPath" class="file-explorer-path-inline">
              <button id="fileExplorerPathCopy" type="button"></button>
              <button id="fileExplorerHiddenToggle" type="button"></button>
              <button id="fileExplorerRootMode" type="button"></button>
              <div id="fileExplorerQuickAccess"></div>
              <div id="fileExplorerTree" class="file-explorer-tree-panel" role="tree"></div>
            </div>
            <div id="mount"></div>
          </div>
          <script>
            window.__shareDiffPayload = {payload};
            window.__shareDiffErrors = [];
            window.addEventListener('error', event => window.__shareDiffErrors.push(event.message || String(event.error || event)));
            window.addEventListener('unhandledrejection', event => window.__shareDiffErrors.push(String(event.reason || event)));
            function jsonResponse(payload, status = 200) {{
              const text = JSON.stringify(payload);
              return Promise.resolve({{ok: status >= 200 && status < 300, status, headers: {{get: () => 'application/json'}}, json: async () => payload, text: async () => text}});
            }}
            window.fetch = async input => {{
              const url = new URL(String(input), 'https://localhost');
              if (url.pathname === '/api/fs/diff') {{
                window.__shareDiffRequest = url.search;
                return jsonResponse(window.__shareDiffPayload);
              }}
              return jsonResponse({{}});
            }};
          </script>
          <script>{app_bundle_before_boot_script()}</script>
          <script>
            window.__shareDiffReady = (async () => {{
              const path = {json.dumps(path)};
              const current = {json.dumps(current).replace("</script", "<\\/script")};
              const item = fileEditorItemFor(path);
              const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
              const waitFor = async predicate => {{
                for (let attempt = 0; attempt < 180; attempt += 1) {{
                  if (predicate()) return true;
                  await frame();
                }}
                return false;
              }};
              installShareScrollPublisher();
              installShareReadonlyInteractionBlocker();
              setFileState(path, {{
                mtime: 1,
                size: current.length,
                kind: 'text',
                content: current,
                original: current,
                dirty: false,
                language: 'python',
                gitRoot: '/repo/app',
                gitTracked: true,
                gitHasHistory: true,
                gitHistory: [{{ref: 'HEAD', short: 'HEAD'}}, {{ref: 'abc1234', short: 'abc1234'}}],
                diffLoaded: false,
                diffUnavailable: false,
              }});
              addFileEditorTabItem(path, item);
              const panel = createFileEditorPanel(item);
              panel.id = 'share-diff-panel';
              panel.classList.add('active-pane');
              panelNodes.set(item, panel);
              document.getElementById('mount').append(panel);
              const infoPanel = createInfoPanel();
              document.getElementById('mount').append(infoPanel);
              await applyShareUiState({{editor: {{modes: [{{
                path,
                item,
                mode: 'diff',
                diffFromRef: 'HEAD',
                diffToRef: 'current',
                diffExpandUnchanged: true,
                viewState: {{top: 280, left: 0, anchor: 0, head: 0}},
              }}]}}, chrome: {{tabMetaVisible: false, infoSubTab: 'yoagent'}}}});
              renderFileEditorPanel(panel, item);
              const modeReady = await waitFor(() => panel._cmMode === 'diff' && panel._cmView?.scrollDOM);
              const scroller = panel._cmView?.scrollDOM;
              applyShareScrollState({{target: `editor:${{item}}:editor`, kind: 'editor', path, item, source: 'editor', top: 2320, left: 0, anchor: 0, head: 0}});
              await frame();
              await frame();
              const diffRowsVisible = await waitFor(() => panel.querySelectorAll('.cm-insertedLine, .cm-deletedLine').length > 0);
              const hostTop = Math.round(scroller?.scrollTop || 0);
              const insertedRows = panel.querySelectorAll('.cm-insertedLine').length;
              const deletedRows = panel.querySelectorAll('.cm-deletedLine').length;
              if (scroller) {{
                scroller.scrollTop = 0;
                scroller.dispatchEvent(new Event('scroll', {{bubbles: true}}));
              }}
              await frame();
              await frame();
              const afterLocalScrollTop = Math.round(scroller?.scrollTop || 0);
              const wheelEvent = new WheelEvent('wheel', {{deltaY: 240, bubbles: true, cancelable: true}});
              const wheelResult = scroller ? scroller.dispatchEvent(wheelEvent) : true;
              await frame();
              await frame();
              const afterWheelTop = Math.round(scroller?.scrollTop || 0);
              const appRoot = document.getElementById('appRoot');
              const hostMenu = document.createElement('div');
              hostMenu.className = 'app-menu open';
              hostMenu.style.position = 'absolute';
              hostMenu.style.left = '40px';
              hostMenu.style.top = '40px';
              hostMenu.innerHTML = '<button class="app-menu-button">Tabs</button><div class="app-menu-popover" style="width:220px;height:90px;"><button class="app-menu-command share-mirror-active">test_app.py</button></div>';
              appRoot.append(hostMenu);
              const hostTabPopover = document.createElement('div');
              hostTabPopover.className = 'pane-tab-detached-popover popover-open';
              hostTabPopover.style.position = 'absolute';
              hostTabPopover.style.left = '300px';
              hostTabPopover.style.top = '40px';
              hostTabPopover.style.width = '260px';
              hostTabPopover.style.height = '110px';
              hostTabPopover.textContent = 'test_app.py tab detail';
              appRoot.append(hostTabPopover);
              const popupPayload = sharePopupLayerPayload();
              applySharePopupLayer(popupPayload, 'host');
              const mirrorLayer = document.querySelector('.share-popup-mirror-layer');
              return {{
                diffReady: modeReady && diffRowsVisible,
                mode: panel._cmMode || '',
                viewMode: editorViewModeFor(path, item),
                diffLoaded: openFiles.get(path)?.diffLoaded === true,
                diffExpandPressed: panel.querySelector('.file-editor-diff-expand-panel')?.getAttribute('aria-pressed') || '',
                tabMetaHidden: document.body.classList.contains('tab-meta-hidden'),
                infoSubtab: infoPanel.dataset.infoSubtab || '',
                yoagentActive: Boolean(infoPanel.querySelector('[data-info-subview="yoagent"]')?.classList.contains('active')),
                yoagentRendered: Boolean(infoPanel.querySelector('#yoagent-content .yoagent-chat, #yoagent-content .yoagent-global')),
                lineClasses: Array.from(panel.querySelectorAll('.cm-line')).map(line => line.className).filter(Boolean).slice(0, 40),
                insertedRows,
                deletedRows,
                hostTop,
                afterLocalScrollTop,
                afterWheelTop,
                wheelPrevented: wheelEvent.defaultPrevented === true || wheelResult === false,
                request: window.__shareDiffRequest || '',
                popupItems: popupPayload.items.length,
                mirrorActiveMenu: Boolean(mirrorLayer?.querySelector('.app-menu-command.share-mirror-active')),
                mirrorTabPopover: Boolean(mirrorLayer?.querySelector('.pane-tab-detached-popover.popover-open')),
                mirrorPointerEvents: mirrorLayer ? getComputedStyle(mirrorLayer).pointerEvents : '',
                errors: window.__shareDiffErrors,
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
        window.__shareDiffReady.then(done, error => done({error: String(error), errors: window.__shareDiffErrors || []}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["diffReady"] is True, json.dumps(metrics, sort_keys=True)
    assert metrics["mode"] == "diff", metrics
    assert metrics["viewMode"] == "diff", metrics
    assert metrics["diffExpandPressed"] == "true", metrics
    assert metrics["tabMetaHidden"] is True, metrics
    assert metrics["infoSubtab"] == "yoagent", metrics
    assert metrics["yoagentActive"] is True, metrics
    assert metrics["yoagentRendered"] is True, metrics
    assert metrics["insertedRows"] > 0 and metrics["deletedRows"] > 0, metrics
    assert metrics["hostTop"] > 0, metrics
    assert metrics["afterLocalScrollTop"] == metrics["hostTop"], metrics
    assert metrics["afterWheelTop"] == metrics["hostTop"], metrics
    assert metrics["wheelPrevented"] is True, metrics
    assert "from=HEAD" in metrics["request"] and "to=current" in metrics["request"], metrics
    assert metrics["popupItems"] >= 2, metrics
    assert metrics["mirrorActiveMenu"] is True, metrics
    assert metrics["mirrorTabPopover"] is True, metrics
    assert metrics["mirrorPointerEvents"] == "none", metrics
    assert metrics["errors"] == [], metrics


def test_share_readonly_info_sort_and_horizontal_scroll_are_host_owned(browser, tmp_path):
    css = app_css()
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    strings = json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text(encoding="utf-8"))
    bootstrap = json.dumps(
        {
            "sessions": ["alpha", "beta"],
            "availableAgents": ["term"],
            "accessRole": "readonly",
            "homePath": "/home/test",
            "repoRoot": "/repo/app",
            "maxSessionTabs": 99,
            "serverHostname": "test-host",
            "strings": {"en": strings},
            "codeMirrorAssetUrl": bundle_uri,
            "share": {"view": True, "id": "share-info", "mode": "ro", "session": "alpha", "sessions": ["alpha", "beta"]},
        },
        separators=(",", ":"),
    )
    page = tmp_path / "share-readonly-info-scroll.html"
    page.write_text(
        f"""<!doctype html><html><head><meta charset=utf-8><style>{css}</style><script src="{bundle_uri}"></script>
        <style>
        body {{ margin: 0; padding: 0; display: block; height: auto; min-height: 0; background: #10151d; }}
        #appRoot {{ position: relative; width: 900px; height: 520px; }}
        #mount {{ width: 420px; height: 260px; padding: 24px; }}
        #info-content {{ width: 320px; height: 150px; overflow: auto; }}
        #info-content .info-row {{ min-width: 1120px; }}
        </style></head>
        <body class="theme-dark theme-resolved-dark editor-theme-dark share-view-mode share-view-readonly">
          <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
          <div id="appRoot" class="app-root"><div id="mount"></div></div>
          <script>{app_bundle_before_boot_script()}</script>
          <script>
            window.__shareInfoReady = (async () => {{
              window.__shareInfoErrors = [];
              window.addEventListener('error', event => window.__shareInfoErrors.push(event.message || String(event.error || event)));
              window.addEventListener('unhandledrejection', event => window.__shareInfoErrors.push(String(event.reason || event)));
              const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
              infoPanelSubTab = 'info';
              transcriptMetaLoaded = true;
              transcriptMetaLoading = false;
              transcriptMetaLoadError = '';
              transcriptMeta = {{
                session_order: ['beta', 'alpha'],
                sessions: {{
                  alpha: {{session: 'alpha', project: {{git: {{root: '/repo/client-alpha', cwd: '/repo/client-alpha', branch: 'local-alpha', other_branches: {{branches: [{{name: 'local-alpha', updated: 'later', updated_ts: 200, current: true, subject: 'client alpha row'}}]}}}}, linear: []}}}},
                  beta: {{session: 'beta', project: {{git: {{root: '/repo/client-beta', cwd: '/repo/client-beta', branch: 'local-beta', other_branches: {{branches: [{{name: 'local-beta', updated: 'earlier', updated_ts: 100, current: true, subject: 'client beta row'}}]}}}}, linear: []}}}},
                }},
              }};
              const hostBranchRows = [
                {{session: 'host-1', path: '/repo/host-one', pathLabel: '/repo/host-one', pathTitle: '/repo/host-one', branch: 'alpha', desc: 'host first row', updated: 'now', updatedText: 'now', updatedTitle: 'now', updatedTs: 300}},
                {{session: 'host-2', path: '/repo/host-two', pathLabel: '/repo/host-two', pathTitle: '/repo/host-two', branch: 'zeta', desc: 'host second row', updated: 'later', updatedText: 'later', updatedTitle: 'later', updatedTs: 100}},
              ];
              installShareScrollPublisher();
              installShareReadonlyInteractionBlocker();
              const panel = createInfoPanel();
              document.getElementById('mount').append(panel);
              applyShareUiState({{info: {{branchSort: {{key: 'session', dir: 'asc'}}, columnWidths: {{branch: 520, desc: 740}}, branchRows: hostBranchRows}}}});
              await frame();
              const scroller = document.getElementById('info-content');
              const rootStyle = getComputedStyle(document.documentElement);
              const rowSessions = () => Array.from(document.querySelectorAll('#info-content .info-row:not(.header) .info-cell:first-child')).map(cell => cell.textContent.trim());
              applyShareScrollState({{target: 'info', kind: 'info', top: 32, left: 220}});
              await frame();
              await frame();
              const hostTop = Math.round(scroller.scrollTop);
              const hostLeft = Math.round(scroller.scrollLeft);
              scroller.scrollTop = 0;
              scroller.scrollLeft = 0;
              scroller.dispatchEvent(new Event('scroll', {{bubbles: true, cancelable: true}}));
              await frame();
              await frame();
              return {{
                rows: rowSessions(),
                hostTop,
                hostLeft,
                afterLocalTop: Math.round(scroller.scrollTop),
                afterLocalLeft: Math.round(scroller.scrollLeft),
                scrollWidth: scroller.scrollWidth,
                clientWidth: scroller.clientWidth,
                branchWidth: rootStyle.getPropertyValue('--info-branch-column-width').trim(),
                descWidth: rootStyle.getPropertyValue('--info-desc-column-width').trim(),
                errors: window.__shareInfoErrors,
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
        window.__shareInfoReady.then(done, error => done({error: String(error), errors: window.__shareInfoErrors || []}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rows"][:2] == ["host-1", "host-2"], metrics
    assert metrics["scrollWidth"] > metrics["clientWidth"], metrics
    assert metrics["branchWidth"] == "520px", metrics
    assert metrics["descWidth"] == "740px", metrics
    assert metrics["hostTop"] > 0, metrics
    assert metrics["hostLeft"] > 0, metrics
    assert metrics["afterLocalTop"] == metrics["hostTop"], metrics
    assert metrics["afterLocalLeft"] == metrics["hostLeft"], metrics


def test_share_readonly_finder_session_is_host_authoritative(browser, tmp_path):
    css = app_css()
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    strings = json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text(encoding="utf-8"))
    bootstrap = json.dumps(
        {
            "sessions": ["5", "6"],
            "availableAgents": ["term"],
            "accessRole": "readonly",
            "homePath": "/home/test",
            "repoRoot": "/repo/app",
            "maxSessionTabs": 99,
            "serverHostname": "test-host",
            "strings": {"en": strings},
            "codeMirrorAssetUrl": bundle_uri,
            "share": {"view": True, "id": "share-finder", "mode": "ro", "session": "5", "sessions": ["5", "6"], "finder": {"session": "5", "mode": "diff"}},
        },
        separators=(",", ":"),
    )
    page = tmp_path / "share-readonly-finder-session.html"
    page.write_text(
        f"""<!doctype html><html><head><meta charset=utf-8><style>{css}</style><script src="{bundle_uri}"></script></head>
        <body class="theme-dark theme-resolved-dark editor-theme-dark share-view-mode share-view-readonly">
          <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
          <div id="appRoot" class="app-root">
            <div id="fileExplorer" class="file-explorer-panel">
              <input id="fileExplorerPath" class="file-explorer-path-inline">
              <button id="fileExplorerPathCopy" type="button"></button>
              <button id="fileExplorerHiddenToggle" type="button"></button>
              <button id="fileExplorerRootMode" type="button"></button>
              <div id="fileExplorerQuickAccess"></div>
              <div id="fileExplorerTree" class="file-explorer-tree-panel" role="tree"></div>
            </div>
            <div id="mount"></div>
          </div>
          <script>
            function jsonResponse(payload, status = 200) {{
              const text = JSON.stringify(payload);
              return Promise.resolve({{ok: status >= 200 && status < 300, status, headers: {{get: () => 'application/json'}}, json: async () => payload, text: async () => text}});
            }}
            const fsEntries = {{
              '/home/test/yolomux.dev1': [{{name: 'src', kind: 'dir'}}],
              '/home/test/yolomux.dev1/src': [{{name: 'app.js', kind: 'file'}}],
              '/home/test/other.dev': [{{name: 'src', kind: 'dir'}}],
              '/home/test/other.dev/src': [{{name: 'main.js', kind: 'file'}}],
            }};
            window.fetch = async (input, options = {{}}) => {{
              const url = new URL(String(input), 'https://localhost');
              if (url.pathname === '/api/session-files') return jsonResponse({{session: url.searchParams.get('session') || '', loaded: true, files: [], repos: [], errors: []}});
              if (url.pathname === '/api/fs/batch') {{
                const body = JSON.parse(options.body || '{{}}');
                return jsonResponse({{responses: (body.requests || []).map(request => {{
                  const path = request.path || '';
                  return {{id: request.id, ok: true, status: 200, payload: {{entries: fsEntries[path] || []}}}};
                }})}});
              }}
              if (url.pathname === '/api/fs/list') return jsonResponse({{entries: fsEntries[url.searchParams.get('path') || ''] || []}});
              return jsonResponse({{}});
            }};
          </script>
          <script>{app_bundle_before_boot_script()}</script>
          <script>
            window.__shareFinderReady = (async () => {{
              window.__shareFinderErrors = [];
              window.addEventListener('error', event => window.__shareFinderErrors.push(event.message || String(event.error || event)));
              window.addEventListener('unhandledrejection', event => window.__shareFinderErrors.push(String(event.reason || event)));
              const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
              transcriptMetaLoaded = true;
              transcriptMeta = {{
                session_order: ['5', '6'],
                sessions: {{
                  '5': {{session: '5', project: {{git: {{root: '/home/test/yolomux.dev1', cwd: '/home/test/yolomux.dev1/src'}}}}, selected_pane: {{current_path: '/home/test/yolomux.dev1/src'}}}},
                  '6': {{session: '6', project: {{git: {{root: '/home/test/other.dev', cwd: '/home/test/other.dev/src'}}}}, selected_pane: {{current_path: '/home/test/other.dev/src'}}}},
                }},
              }};
              const initial = fileExplorerSessionFilesTargetSession();
              const localResult = noteFileExplorerChangesSessionInteraction('6');
              const afterLocal = fileExplorerSessionFilesTargetSession();
              await applyShareUiState({{finder: {{root: '/home/test/yolomux.dev1', rootMode: 'sync', mode: 'files', session: '5', expanded: ['/home/test/yolomux.dev1/src']}}}});
              const hostRoot = fileExplorerRoot;
              const hostExpanded = Array.from(fileExplorerExpanded);
              setSessionFilesPayloadForDestination('finder', {{session: '6', loaded: true, files: [], repos: [], errors: []}});
              scheduleFileExplorerActiveTabSync('6', {{explicit: true}});
              await frame();
              await frame();
              const afterPayload = fileExplorerSessionFilesTargetSession();
              const afterPayloadRoot = fileExplorerRoot;
              const afterPayloadExpanded = Array.from(fileExplorerExpanded);
              const localOpenResult = await openFileExplorerAt('/home/test/other.dev');
              await applyShareUiState({{finder: {{root: '/home/test/other.dev', rootMode: 'sync', session: '6', mode: 'diff', expanded: ['/home/test/other.dev/src']}}}});
              const afterHost = fileExplorerSessionFilesTargetSession();
              return {{
                initial,
                localResult,
                afterLocal,
                hostRoot,
                hostExpanded,
                afterPayload,
                afterPayloadRoot,
                afterPayloadExpanded,
                localOpenResult,
                afterHost,
                afterHostRoot: fileExplorerRoot,
                afterHostExpanded: Array.from(fileExplorerExpanded),
                errors: window.__shareFinderErrors,
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
        window.__shareFinderReady.then(done, error => done({error: String(error), errors: window.__shareFinderErrors || []}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["initial"] == "5", metrics
    assert metrics["localResult"] is False, metrics
    assert metrics["afterLocal"] == "5", metrics
    assert metrics["hostRoot"] == "/home/test/yolomux.dev1", metrics
    assert metrics["hostExpanded"] == ["/home/test/yolomux.dev1/src"], metrics
    assert metrics["afterPayload"] == "5", metrics
    assert metrics["afterPayloadRoot"] == "/home/test/yolomux.dev1", metrics
    assert metrics["afterPayloadExpanded"] == ["/home/test/yolomux.dev1/src"], metrics
    assert metrics["localOpenResult"] is False, metrics
    assert metrics["afterHost"] == "6", metrics
    assert metrics["afterHostRoot"] == "/home/test/other.dev", metrics
    assert metrics["afterHostExpanded"] == ["/home/test/other.dev/src"], metrics


def test_dockview_tabs_keep_yolomux_active_inactive_style(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:1,2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    metrics = dockview_layout_metrics(browser)
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["groups"][0]["tabs"] == ["1", "2"]
    active = next(item for item in metrics["tabStyles"] if item["item"] == "1")
    inactive = next(item for item in metrics["tabStyles"] if item["item"] == "2")
    assert active["active"] is True
    assert inactive["active"] is False
    assert active["bg"] != inactive["bg"]
    assert active["color"] != inactive["color"]
    assert active["rect"]["height"] >= 18
    assert inactive["rect"]["height"] == active["rect"]["height"]
    assert active["rect"]["width"] >= 150
    assert inactive["rect"]["width"] == active["rect"]["width"]
    assert active["borderTopLeftRadius"] == "6px"
    assert active["borderTopRightRadius"] == "6px"
    assert metrics["header"]["tabsScrollbarWidth"] == "none"
    assert metrics["header"]["tabsWebkitScrollbarDisplay"] == "none"
    assert metrics["header"]["tabsWebkitScrollbarHeight"] == "0px"
    assert metrics["header"]["activeTabInsideHeader"] is True

    screenshot = browser_screenshot_rgb(browser)
    dpr = browser.execute_script("return window.devicePixelRatio || 1") or 1

    def rgb_tuple(css_color):
        match = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", css_color)
        if match:
            return tuple(int(part) for part in match.groups())
        srgb_match = re.match(r"color\(srgb\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)", css_color)
        assert srgb_match, css_color
        return tuple(round(float(part) * 255) for part in srgb_match.groups())

    def color_distance(left, right):
        return sum(abs(a - b) for a, b in zip(left, right))

    def sampled_background_matches(rect, expected):
        x_fractions = (0.18, 0.32, 0.5, 0.68, 0.82)
        y_fractions = (0.45, 0.58, 0.72)
        matches = 0
        samples = []
        for x_fraction in x_fractions:
            for y_fraction in y_fractions:
                x = max(0, min(screenshot.width - 1, int((rect["left"] + rect["width"] * x_fraction) * dpr)))
                y = max(0, min(screenshot.height - 1, int((rect["top"] + rect["height"] * y_fraction) * dpr)))
                sample = screenshot.getpixel((x, y))
                samples.append(sample)
                if color_distance(sample, expected) <= 18:
                    matches += 1
        return matches, samples

    active_matches, active_samples = sampled_background_matches(active["rect"], rgb_tuple(active["bg"]))
    inactive_matches, inactive_samples = sampled_background_matches(inactive["rect"], rgb_tuple(inactive["bg"]))
    assert active_matches >= 4, {"bg": active["bg"], "samples": active_samples}
    assert inactive_matches >= 4, {"bg": inactive["bg"], "samples": inactive_samples}


def test_dockview_tab_hover_shows_session_detail_popover(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=left&tabs=left:1,2",
        sessions=["1", "2"],
        transcript_sessions={
            "1": {
                "current_path": "/home/test/yolomux.dev1",
                "git_root": "/home/test/yolomux.dev1",
                "branch": "yolo-tab-dock-rewrite",
            }
        },
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    browser.execute_script(
        """
        tabPopoverShowDelayMs = 0;
        tabPopoverFollowDelayMs = 0;
        popoverHideDelayMs = 1000;
        """
    )
    ActionChains(browser).move_to_element(browser.find_element("css selector", '.dockview-pane-tab[data-pane-tab="1"]')).perform()
    metrics = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const tab = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]');
            const popover = document.querySelector('.pane-tab-detached-popover.popover-open, .dockview-pane-tab.popover-open > .session-popover');
            if (!tab || !popover) return false;
            const style = getComputedStyle(popover);
            const rect = popover.getBoundingClientRect();
            const tabRect = tab.getBoundingClientRect();
            const visible = style.visibility === 'visible'
              && Number.parseFloat(style.opacity) > 0.9
              && rect.width > 100
              && rect.height > 40;
            if (!visible) return false;
            return {
              text: popover.textContent,
              parentTag: popover.parentElement?.tagName || '',
              top: Math.round(rect.top),
              left: Math.round(rect.left),
              bottom: Math.round(rect.bottom),
              tabBottom: Math.round(tabRect.bottom),
              pointerEvents: style.pointerEvents,
              zIndex: style.zIndex,
            };
            """
        )
    )
    assert "/home/test/yolomux.dev1" in metrics["text"], metrics
    assert "yolo-tab-dock-rewrite" in metrics["text"], metrics
    assert metrics["parentTag"] == "BODY", metrics
    assert metrics["top"] >= metrics["tabBottom"], metrics
    assert metrics["pointerEvents"] == "auto", metrics


def test_dockview_tab_hover_popover_survives_tab_refresh_without_pointer_move(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=left&tabs=left:1,2",
        sessions=["1", "2"],
        transcript_sessions={
            "1": {
                "current_path": "/home/test/yolomux.dev1",
                "git_root": "/home/test/yolomux.dev1",
                "branch": "yolo-tab-dock-rewrite",
            }
        },
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    browser.execute_script(
        """
        tabPopoverShowDelayMs = 0;
        tabPopoverFollowDelayMs = 0;
        popoverHideDelayMs = 120;
        """
    )
    ActionChains(browser).move_to_element(browser.find_element("css selector", '.dockview-pane-tab[data-pane-tab="1"]')).perform()
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const popover = document.querySelector('.pane-tab-detached-popover.popover-open');
            if (!popover) return false;
            const style = getComputedStyle(popover);
            const rect = popover.getBoundingClientRect();
            return style.visibility === 'visible' && Number.parseFloat(style.opacity) > 0.9 && rect.width > 100 && rect.height > 40;
            """
        )
    )
    browser.execute_script(
        """
        const popover = document.querySelector('.pane-tab-detached-popover.popover-open');
        window.__popoverBeforeDockviewRefresh = popover;
        dockviewRefreshTabs();
        """
    )
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        setTimeout(done, 260);
        """
    )
    metrics = browser.execute_script(
        """
        const tab = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]');
        const popover = document.querySelector('.pane-tab-detached-popover.popover-open');
        const style = popover ? getComputedStyle(popover) : null;
        const rect = popover?.getBoundingClientRect?.();
        return {
          visible: Boolean(popover && style.visibility === 'visible' && Number.parseFloat(style.opacity) > 0.9 && rect.width > 100 && rect.height > 40),
          samePopover: popover === window.__popoverBeforeDockviewRefresh,
          parentTag: popover?.parentElement?.tagName || '',
          detachedRef: tab?.__yolomuxDetachedPopover === popover,
          hoverState: tab?.dataset?.popoverHoverState || '',
          tabOpen: tab?.classList?.contains('popover-open') || false,
        };
        """
    )
    assert metrics["visible"] is True, metrics
    assert metrics["samePopover"] is True, metrics
    assert metrics["parentTag"] == "BODY", metrics
    assert metrics["detachedRef"] is True, metrics
    assert metrics["hoverState"] == "open", metrics
    assert metrics["tabOpen"] is True, metrics


def test_dockview_separator_inactive_tab_and_preview_colors_match_tokens(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    metrics = browser.execute_script(
        """
        const group = document.querySelector('.dv-groupview');
        const panel = group.querySelector('.dockview-panel-content > .panel');
        const inactiveTab = document.querySelector('.dv-tab.dv-inactive-tab > .dockview-pane-tab:not(.active)');
        const strip = inactiveTab?.closest('.dv-tabs-and-actions-container');
        const sash = document.querySelector('.dv-sash');
        group.classList.add('drag-over', 'drop-preview', 'drop-preview-left');
        group.dataset.dropLabel = 'left';
        const groupStyle = getComputedStyle(group);
        const panelStyle = panel ? getComputedStyle(panel) : null;
        const stripStyle = strip ? getComputedStyle(strip) : null;
        const tabStyle = inactiveTab ? getComputedStyle(inactiveTab) : null;
        const separatorHover = getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim();
        const separatorLineSize = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-line-size')) || 0;
        const previewStyle = getComputedStyle(group, '::before');
        const sashStyle = sash ? getComputedStyle(sash, '::before') : null;
        const result = {
          groupBorder: [groupStyle.borderTopWidth, groupStyle.borderRightWidth, groupStyle.borderBottomWidth, groupStyle.borderLeftWidth],
          panelBorder: panelStyle ? [panelStyle.borderTopWidth, panelStyle.borderRightWidth, panelStyle.borderBottomWidth, panelStyle.borderLeftWidth] : [],
          inactiveBg: tabStyle?.backgroundColor || '',
          stripBg: stripStyle?.backgroundColor || '',
          previewBorderColor: previewStyle.borderLeftColor,
          separatorHover,
          sashBg: sashStyle?.backgroundColor || '',
          sashBeforeWidth: sashStyle ? parseFloat(sashStyle.width) || 0 : 0,
          separatorLineSize,
        };
        group.classList.remove('drag-over', 'drop-preview', 'drop-preview-left');
        delete group.dataset.dropLabel;
        return result;
        """
    )
    assert metrics["groupBorder"] == ["0px", "0px", "0px", "0px"]
    assert metrics["panelBorder"] == ["0px", "0px", "0px", "0px"]
    assert metrics["inactiveBg"] == metrics["stripBg"]
    assert metrics["previewBorderColor"] == metrics["separatorHover"]
    assert metrics["sashBg"]
    assert metrics["sashBeforeWidth"] <= metrics["separatorLineSize"] + 0.1
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".dv-sash")).perform()
    hover_metrics = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const sashStyle = getComputedStyle(document.querySelector('.dv-sash'), '::before');
            const docStyle = getComputedStyle(document.documentElement);
            const result = {
              sashBg: sashStyle.backgroundColor,
              hoverBg: docStyle.getPropertyValue('--pane-resizer-hover-bg').trim(),
              sashBeforeWidth: parseFloat(sashStyle.width) || 0,
              hoverLineSize: parseFloat(docStyle.getPropertyValue('--pane-resizer-hover-line-size')) || 0,
            };
            return result.sashBg === result.hoverBg ? result : false;
            """
        )
    )
    assert hover_metrics["sashBeforeWidth"] <= hover_metrics["hoverLineSize"] + 0.1
    assert hover_metrics["sashBeforeWidth"] >= metrics["sashBeforeWidth"]


def test_separator_color_preference_recolors_drop_previews(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    metrics = browser.execute_script(
        """
        applySettingsPayload({settings: {appearance: {separator_color: 'purple'}}, defaults: {}, mtime_ns: 9301}, {force: true});
        const expectedProbe = document.createElement('div');
        expectedProbe.style.borderLeft = '2px dashed var(--pane-resizer-hover-bg)';
        expectedProbe.style.position = 'absolute';
        expectedProbe.style.left = '-1000px';
        document.body.append(expectedProbe);
        const expected = getComputedStyle(expectedProbe).borderLeftColor;
        expectedProbe.remove();

        const tabStrip = document.createElement('div');
        tabStrip.className = 'pane-tabs tab-drop-preview';
        tabStrip.style.cssText = 'position:absolute;left:20px;top:20px;width:220px;height:28px;--tab-drop-x:40px;--tab-drop-y:0px;--tab-drop-height:24px;';
        document.body.append(tabStrip);
        const tabInsertion = getComputedStyle(tabStrip, '::after').borderLeftColor;
        tabStrip.remove();

        const group = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const groupRect = group.getBoundingClientRect();
        group.classList.add('drag-over', 'drop-preview', 'drop-preview-left');
        const panePreview = getComputedStyle(group, '::before').borderLeftColor;
        group.classList.remove('drag-over', 'drop-preview', 'drop-preview-left');

        const gridNode = document.querySelector('#grid');
        gridNode.classList.add('drop-preview', 'drop-preview-root', 'drop-preview-right');
        const rootPreview = getComputedStyle(gridNode, '::before').borderLeftColor;
        gridNode.classList.remove('drop-preview', 'drop-preview-root', 'drop-preview-right');

        window.__filePreviewOpen = null;
        window.openDraggedFilesInEditor = (payload, options) => {
          window.__filePreviewOpen = {payload, options};
        };
        const target = group.querySelector('.dockview-panel-content') || group;
        const store = {
          'application/x-yolomux-file': JSON.stringify({path: '/home/test/yolomux.dev/README.md', paths: ['/home/test/yolomux.dev/README.md'], kind: 'file'}),
          'text/plain': '/home/test/yolomux.dev/README.md',
        };
        const dataTransfer = {
          types: Object.keys(store),
          dropEffect: '',
          effectAllowed: 'copy',
          getData(type) { return store[type] || ''; },
          setData(type, value) { store[type] = String(value); },
        };
        const event = new Event('dragover', {bubbles: true, cancelable: true});
        Object.defineProperty(event, 'clientX', {value: Math.round(groupRect.left + 8)});
        Object.defineProperty(event, 'clientY', {value: Math.round(groupRect.top + groupRect.height / 2)});
        Object.defineProperty(event, 'dataTransfer', {value: dataTransfer});
        target.dispatchEvent(event);
        const fileDragPreview = getComputedStyle(group, '::before').borderLeftColor;
        const fileDropEffect = dataTransfer.dropEffect;
        clearDropPreview();

        return {
          expected,
          token: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
          tabInsertion,
          panePreview,
          rootPreview,
          fileDragPreview,
          fileDropEffect,
        };
        """
    )
    assert metrics["token"].startswith("rgb("), metrics
    assert metrics["tabInsertion"] == metrics["expected"], metrics
    assert metrics["panePreview"] == metrics["expected"], metrics
    assert metrics["rootPreview"] == metrics["expected"], metrics
    assert metrics["fileDragPreview"] == metrics["expected"], metrics
    assert metrics["fileDropEffect"] == "copy", metrics


def test_dockview_active_ring_follows_pane_spacing_without_thickening_sash(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    metrics = browser.execute_script(
        """
        applySettingsPayload({settings: {appearance: {pane_spacing: 6, pane_ring_opacity: 75}}, defaults: {}, mtime_ns: 9001}, {force: true});
        const activePanel = document.querySelector('#panel-1');
        const inactivePanel = document.querySelector('#panel-2');
        activePanel.classList.add('active-pane', 'focused-pane');
        inactivePanel.classList.remove('active-pane', 'focused-pane', 'typing-ready-pane');
        const activeGroup = activePanel.closest('.dv-groupview');
        const inactiveGroup = inactivePanel.closest('.dv-groupview');
        const activeRing = getComputedStyle(activeGroup, '::after');
        const inactiveRing = getComputedStyle(inactiveGroup, '::after');
        const activeGroupStyle = getComputedStyle(activeGroup);
        const activePanelStyle = getComputedStyle(activePanel);
        const activeGroupRect = activeGroup.getBoundingClientRect();
        const activePanelRect = activePanel.getBoundingClientRect();
        const activeTerminalRect = activePanel.querySelector('.terminal')?.getBoundingClientRect();
        const activeXtermRect = activePanel.querySelector('.terminal .xterm')?.getBoundingClientRect();
        const sash = document.querySelector('.dv-sash');
        const sashStyle = getComputedStyle(sash);
        const sashBefore = getComputedStyle(sash, '::before');
        const docStyle = getComputedStyle(document.documentElement);
        const paneGapPx = parseFloat(docStyle.getPropertyValue('--pane-split-gap')) || 0;
        return {
          paneGap: docStyle.getPropertyValue('--pane-split-gap').trim(),
          paneGapPx,
          activeRingBorderWidth: activeRing.borderTopWidth,
          activeRingBorderColor: activeRing.borderTopColor,
          activeRingPointerEvents: activeRing.pointerEvents,
          inactiveRingBorderWidth: inactiveRing.borderTopWidth,
          inactiveRingBorderColor: inactiveRing.borderTopColor,
          groupBorder: [activeGroupStyle.borderTopWidth, activeGroupStyle.borderRightWidth, activeGroupStyle.borderBottomWidth, activeGroupStyle.borderLeftWidth],
          panelBorder: [activePanelStyle.borderTopWidth, activePanelStyle.borderRightWidth, activePanelStyle.borderBottomWidth, activePanelStyle.borderLeftWidth],
          sashBackground: sashStyle.backgroundColor,
          sashBeforeWidth: parseFloat(sashBefore.width) || 0,
          separatorLineSize: parseFloat(docStyle.getPropertyValue('--pane-resizer-line-size')) || 0,
          groupPadding: [activeGroupStyle.paddingTop, activeGroupStyle.paddingRight, activeGroupStyle.paddingBottom, activeGroupStyle.paddingLeft],
          panelInset: {
            left: activePanelRect.left - activeGroupRect.left,
            right: activeGroupRect.right - activePanelRect.right,
            bottom: activeGroupRect.bottom - activePanelRect.bottom,
          },
          terminalInset: activeTerminalRect ? {
            left: activeTerminalRect.left - activeGroupRect.left,
            right: activeGroupRect.right - activeTerminalRect.right,
            bottom: activeGroupRect.bottom - activeTerminalRect.bottom,
          } : null,
          xtermInset: activeXtermRect ? {
            left: activeXtermRect.left - activeGroupRect.left,
            right: activeGroupRect.right - activeXtermRect.right,
            bottom: activeGroupRect.bottom - activeXtermRect.bottom,
          } : null,
        };
        """
    )
    assert metrics["paneGap"] == "6px", metrics
    assert metrics["activeRingBorderWidth"] == "6px", metrics
    assert metrics["activeRingBorderColor"] not in ("rgba(0, 0, 0, 0)", "transparent"), metrics
    assert metrics["activeRingPointerEvents"] == "none", metrics
    assert metrics["inactiveRingBorderWidth"] == "6px", metrics
    assert metrics["inactiveRingBorderColor"] in ("rgba(0, 0, 0, 0)", "transparent", "color(srgb 0 0 0 / 0)"), metrics
    assert metrics["groupBorder"] == ["0px", "0px", "0px", "0px"], metrics
    assert metrics["panelBorder"] == ["0px", "0px", "0px", "0px"], metrics
    assert metrics["sashBackground"] in ("rgba(0, 0, 0, 0)", "transparent"), metrics
    assert metrics["sashBeforeWidth"] <= metrics["separatorLineSize"] + 0.1, metrics
    assert metrics["groupPadding"] == ["6px", "6px", "6px", "6px"], metrics
    assert metrics["panelInset"]["left"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["panelInset"]["right"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["panelInset"]["bottom"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["terminalInset"]["left"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["terminalInset"]["right"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["terminalInset"]["bottom"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["xtermInset"]["left"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["xtermInset"]["right"] >= metrics["paneGapPx"] - 0.5, metrics
    assert metrics["xtermInset"]["bottom"] >= metrics["paneGapPx"] - 0.5, metrics


def test_dockview_pane_spacing_multiple_values_keep_terminal_inside_ring(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    metrics = browser.execute_script(
        """
        const activePanel = document.querySelector('#panel-1');
        activePanel.classList.add('active-pane', 'focused-pane');
        const activeGroup = activePanel.closest('.dv-groupview');
        const docStyle = getComputedStyle(document.documentElement);
        const snapshots = [];
        for (const value of [0, 3, 12, 20]) {
          applySettingsPayload({settings: {appearance: {pane_spacing: value, pane_ring_opacity: 75}}, defaults: {}, mtime_ns: 9100 + value}, {force: true});
          const groupStyle = getComputedStyle(activeGroup);
          const ring = getComputedStyle(activeGroup, '::after');
          const groupRect = activeGroup.getBoundingClientRect();
          const panelRect = activePanel.getBoundingClientRect();
          const terminalRect = activePanel.querySelector('.terminal')?.getBoundingClientRect();
          const xtermRect = activePanel.querySelector('.terminal .xterm')?.getBoundingClientRect();
          const paneGapPx = parseFloat(docStyle.getPropertyValue('--pane-split-gap')) || 0;
          snapshots.push({
            value,
            paneGap: docStyle.getPropertyValue('--pane-split-gap').trim(),
            paneGapPx,
            ringWidth: ring.borderTopWidth,
            groupBorder: [groupStyle.borderTopWidth, groupStyle.borderRightWidth, groupStyle.borderBottomWidth, groupStyle.borderLeftWidth],
            panelInset: {
              left: panelRect.left - groupRect.left,
              right: groupRect.right - panelRect.right,
              bottom: groupRect.bottom - panelRect.bottom,
            },
            terminalInset: terminalRect ? {
              left: terminalRect.left - groupRect.left,
              right: groupRect.right - terminalRect.right,
              bottom: groupRect.bottom - terminalRect.bottom,
            } : null,
            xtermInset: xtermRect ? {
              left: xtermRect.left - groupRect.left,
              right: groupRect.right - xtermRect.right,
              bottom: groupRect.bottom - xtermRect.bottom,
            } : null,
          });
        }
        return snapshots;
        """
    )
    assert [item["value"] for item in metrics] == [0, 3, 12, 20], metrics
    for item in metrics:
        assert item["paneGap"] == f"{item['value']}px", item
        assert item["ringWidth"] == f"{item['value']}px", item
        assert item["groupBorder"] == ["0px", "0px", "0px", "0px"], item
        for rect_key in ["panelInset", "terminalInset", "xtermInset"]:
            assert item[rect_key]["left"] >= item["paneGapPx"] - 0.5, item
            assert item[rect_key]["right"] >= item["paneGapPx"] - 0.5, item
            assert item[rect_key]["bottom"] >= item["paneGapPx"] - 0.5, item


def test_dockview_complex_layout_sash_hit_targets_stay_transparent(browser, tmp_path):
    encoded_file = "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2FDONE.md"
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        (
            f"?sessions=files,5,{encoded_file},2"
            f"&layout=row@20(slot1,row@50(left,col@50(slot2,slot3)))"
            f"&tabs=slot1:files;left:5;slot2:{encoded_file};slot3:2"
        ),
        sessions=["5", "2"],
    )
    wait_for_dockview(browser, min_tabs=4)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelectorAll('.dv-groupview').length === 4
              && document.querySelectorAll('.dv-sash').length >= 3
              && Array.from(document.querySelectorAll('.dv-groupview'))
                .every(group => group.getBoundingClientRect().width > 0 && group.getBoundingClientRect().height > 0);
            """
        )
    )
    metrics = browser.execute_script(
        """
        const transparent = new Set(['rgba(0, 0, 0, 0)', 'transparent']);
        const docStyle = getComputedStyle(document.documentElement);
        const lineSize = parseFloat(docStyle.getPropertyValue('--pane-resizer-line-size')) || 0;
        const separatorBg = docStyle.getPropertyValue('--pane-resizer-bg').trim();
        const rectFor = node => {
          const rect = node.getBoundingClientRect();
          return {width: rect.width, height: rect.height, left: rect.left, top: rect.top};
        };
        const sashes = Array.from(document.querySelectorAll('.dv-sash')).map(sash => {
          const style = getComputedStyle(sash);
          const before = getComputedStyle(sash, '::before');
          const split = sash.closest('.dv-split-view-container')?.className || '';
          return {
            split,
            rect: rectFor(sash),
            bg: style.backgroundColor,
            beforeBg: before.backgroundColor,
            beforeWidth: parseFloat(before.width) || 0,
            beforeHeight: parseFloat(before.height) || 0,
            horizontal: split.includes('dv-horizontal'),
            vertical: split.includes('dv-vertical'),
            transparent: transparent.has(style.backgroundColor),
          };
        });
        const groups = Array.from(document.querySelectorAll('.dv-groupview')).map(group => {
          const groupStyle = getComputedStyle(group);
          const panel = group.querySelector('.dockview-panel-content > .panel');
          const panelStyle = panel ? getComputedStyle(panel) : null;
          return {
            tabs: Array.from(group.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            groupBorder: [groupStyle.borderTopWidth, groupStyle.borderRightWidth, groupStyle.borderBottomWidth, groupStyle.borderLeftWidth],
            panelBorder: panelStyle ? [panelStyle.borderTopWidth, panelStyle.borderRightWidth, panelStyle.borderBottomWidth, panelStyle.borderLeftWidth] : [],
          };
        });
        return {sashes, groups, lineSize, separatorBg};
        """
    )
    assert len(metrics["groups"]) == 4
    assert len(metrics["sashes"]) >= 3
    assert any("__files__" in group["tabs"] for group in metrics["groups"])
    assert any("5" in group["tabs"] for group in metrics["groups"])
    assert any("2" in group["tabs"] for group in metrics["groups"])
    assert any(any(tab.startswith("file:") for tab in group["tabs"]) for group in metrics["groups"])
    for group in metrics["groups"]:
        assert group["groupBorder"] == ["0px", "0px", "0px", "0px"]
        assert group["panelBorder"] in (["0px", "0px", "0px", "0px"], [])
    for sash in metrics["sashes"]:
        assert sash["transparent"] is True, sash
        assert sash["beforeBg"] == metrics["separatorBg"]
        if sash["horizontal"]:
            assert sash["beforeWidth"] <= metrics["lineSize"] + 0.1
        if sash["vertical"]:
            assert sash["beforeHeight"] <= metrics["lineSize"] + 0.1

    first_sash = browser.find_element("css selector", ".dv-sash")
    ActionChains(browser).move_to_element(first_sash).perform()
    hover_metrics = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const sash = document.querySelector('.dv-sash:hover');
            if (!sash) return false;
            const style = getComputedStyle(sash);
            const before = getComputedStyle(sash, '::before');
            const docStyle = getComputedStyle(document.documentElement);
            const hoverBg = docStyle.getPropertyValue('--pane-resizer-hover-bg').trim();
            const hoverLineSize = parseFloat(docStyle.getPropertyValue('--pane-resizer-hover-line-size')) || 0;
            const split = sash.closest('.dv-split-view-container')?.className || '';
            return {
              bg: style.backgroundColor,
              beforeBg: before.backgroundColor,
              beforeWidth: parseFloat(before.width) || 0,
              beforeHeight: parseFloat(before.height) || 0,
              hoverBg,
              hoverLineSize,
              horizontal: split.includes('dv-horizontal'),
              vertical: split.includes('dv-vertical'),
            };
            """
        )
    )
    assert hover_metrics["bg"] in ("rgba(0, 0, 0, 0)", "transparent")
    assert hover_metrics["beforeBg"] == hover_metrics["hoverBg"]
    if hover_metrics["horizontal"]:
        assert hover_metrics["beforeWidth"] <= hover_metrics["hoverLineSize"] + 0.1
    if hover_metrics["vertical"]:
        assert hover_metrics["beforeHeight"] <= hover_metrics["hoverLineSize"] + 0.1


def test_dockview_hidden_inner_header_keeps_terminal_content_full_height(browser, tmp_path):
    encoded_file = "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2FDONE.md"
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        (
            f"?sessions=files,5,{encoded_file},2"
            f"&layout=row@20(slot1,row@50(left,col@50(slot2,slot3)))"
            f"&tabs=slot1:files;left:5;slot2:{encoded_file};slot3:2"
        ),
        sessions=["5", "2"],
        terminal_css=".terminal { width: 100%; height: 100%; }",
    )
    wait_for_dockview(browser, min_tabs=4)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const item = terminals.get('5');
            return document.querySelector('#term-5 .xterm') && item?.term?.rows > 20;
            """
        )
    )
    metrics = browser.execute_script(
        """
        const rectFor = node => {
          const rect = node.getBoundingClientRect();
          return {top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height};
        };
        const panel = document.querySelector('#panel-5');
        const head = panel.querySelector('.panel-head');
        const detail = panel.querySelector('.panel-detail-row');
        const pane = panel.querySelector('#terminal-pane-5');
        const terminal = panel.querySelector('#term-5');
        const xterm = panel.querySelector('#term-5 .xterm');
        const panelRect = rectFor(panel);
        const detailRect = rectFor(detail);
        const paneRect = rectFor(pane);
        const terminalRect = rectFor(terminal);
        const xtermRect = rectFor(xterm);
        return {
          panelCollapsed: panel.classList.contains('dockview-inner-head-collapsed'),
          innerHeadHidden: head.hidden === true,
          innerHeadDisplay: getComputedStyle(head).display,
          panelRows: getComputedStyle(panel).gridTemplateRows.trim().split(/\\s+/),
          panelHeight: panelRect.height,
          detailHeight: detailRect.height,
          paneHeight: paneRect.height,
          terminalHeight: terminalRect.height,
          xtermHeight: xtermRect.height,
          paneBottomDelta: Math.abs(panelRect.bottom - paneRect.bottom),
          terminalBottomDelta: Math.abs(paneRect.bottom - terminalRect.bottom),
          xtermBottomDelta: Math.abs(terminalRect.bottom - xtermRect.bottom),
          termRows: terminals.get('5')?.term?.rows || 0,
          termCols: terminals.get('5')?.term?.cols || 0,
        };
        """
    )
    assert metrics["panelCollapsed"] is True
    assert metrics["innerHeadHidden"] is True
    assert metrics["innerHeadDisplay"] == "none"
    assert len(metrics["panelRows"]) == 2
    assert metrics["paneHeight"] >= metrics["panelHeight"] - metrics["detailHeight"] - 2
    assert metrics["terminalHeight"] >= metrics["paneHeight"] - 1
    assert metrics["xtermHeight"] >= metrics["terminalHeight"] - 1
    assert metrics["paneBottomDelta"] <= 1
    assert metrics["terminalBottomDelta"] <= 1
    assert metrics["xtermBottomDelta"] <= 1
    assert metrics["termRows"] > 20
    assert metrics["termCols"] >= 40


def test_dockview_header_actions_stay_on_first_row(browser, tmp_path):
    sessions = [str(index) for index in range(1, 8)]
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3,4,5,6,7&layout=left&tabs=left:1,2,3,4,5,6,7",
        sessions=sessions,
    )
    wait_for_dockview(browser, min_tabs=7)
    wait_for_dockview_tab_geometry(browser, min_tabs=7, min_width=150, min_rows=2)
    metrics = browser.execute_script(
        """
        const header = document.querySelector('.dv-tabs-and-actions-container');
        const tabsContainer = header.querySelector('.dv-tabs-container');
        const tab = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]');
        const rectFor = rect => ({left: Math.round(rect.left), right: Math.round(rect.right), top: Math.round(rect.top), bottom: Math.round(rect.bottom), width: Math.round(rect.width), height: Math.round(rect.height)});
        const tabRects = Array.from(document.querySelectorAll('.dockview-pane-tab')).map(item => rectFor(item.getBoundingClientRect()));
        const actions = document.querySelector('.dockview-pane-header-actions .tabs');
        const actionButtons = Array.from(document.querySelectorAll('.dockview-pane-header-actions .tab'));
        const panel = document.querySelector('#panel-1');
        const innerHead = document.querySelector('#panel-1 .panel-head');
        const headerRect = header.getBoundingClientRect();
        const tabRect = tab.getBoundingClientRect();
        const actionsRect = actions.getBoundingClientRect();
        const actionButtonRects = actionButtons.map(button => button.getBoundingClientRect());
        const actionBox = rectFor(actionsRect);
        const firstRowTabs = tabRects.filter(rect => Math.abs(rect.top - Math.round(tabRect.top)) <= 3);
        const overlappingTabs = tabRects.filter(rect => (
          rect.right > actionBox.left - 1
          && rect.left < actionBox.right + 1
          && rect.bottom > actionBox.top + 1
          && rect.top < actionBox.bottom - 1
        ));
        const panelRect = panel.getBoundingClientRect();
        const innerHeadRect = innerHead.getBoundingClientRect();
        const innerHeadStyle = getComputedStyle(innerHead);
        return {
          headerHeight: Math.round(headerRect.height),
          tabHeight: Math.round(tabRect.height),
          maxActionButtonHeight: Math.round(Math.max(...actionButtonRects.map(rect => rect.height))),
          firstRowTabsRight: Math.round(Math.max(...firstRowTabs.map(rect => rect.right))),
          overlappingTabs: overlappingTabs.map(rect => rectFor(rect)),
          tabRows: new Set(tabRects.map(rect => rect.top)).size,
          tabsOverflowX: getComputedStyle(tabsContainer).overflowX,
          tabsScrollWidth: Math.round(tabsContainer.scrollWidth),
          tabsClientWidth: Math.round(tabsContainer.clientWidth),
          reservedInlineSize: getComputedStyle(header).getPropertyValue('--dockview-header-actions-reserved-inline-size').trim(),
          tabCount: tabRects.length,
          headerRight: Math.round(headerRect.right),
          actionsLeft: Math.round(actionsRect.left),
          actionsRight: Math.round(actionsRect.right),
          actionsTopDelta: Math.abs(Math.round(actionsRect.top - tabRect.top)),
          actionsBottom: Math.round(actionsRect.bottom),
          tabBottom: Math.round(tabRect.bottom),
          innerHeadHidden: innerHead?.hidden === true,
          innerHeadDisplay: innerHeadStyle.display,
          innerHeadHeight: Math.round(innerHeadRect.height),
          panelTopDelta: Math.abs(Math.round(panelRect.top - headerRect.bottom)),
        };
        """
    )
    assert metrics["innerHeadHidden"] is True
    assert metrics["innerHeadDisplay"] == "none"
    assert metrics["innerHeadHeight"] == 0
    assert metrics["panelTopDelta"] <= 1
    assert metrics["tabCount"] == 7
    assert metrics["reservedInlineSize"].endswith("px")
    assert float(metrics["reservedInlineSize"][:-2]) >= 80
    assert metrics["actionsLeft"] >= metrics["firstRowTabsRight"] + 1
    assert metrics["overlappingTabs"] == []
    assert metrics["actionsRight"] <= metrics["headerRight"] + 1
    assert metrics["actionsTopDelta"] <= 3
    assert metrics["actionsBottom"] <= metrics["tabBottom"] + 3
    assert metrics["tabRows"] >= 2
    assert metrics["tabsOverflowX"] == "visible"
    assert metrics["tabsScrollWidth"] <= metrics["tabsClientWidth"] + 1
    assert metrics["headerHeight"] >= (metrics["tabHeight"] * 2) - 2
    assert metrics["maxActionButtonHeight"] <= 20
    assert metrics["maxActionButtonHeight"] <= metrics["tabHeight"] + 1


def test_dockview_window_bar_buttons_select_tmux_windows(browser, tmp_path):
    transcript_sessions = {
        "1": {
            "panes": [
                {"target": "%1", "window": 0, "window_name": "bash", "window_active": False, "active": True, "process_label": "bash"},
                {"target": "%2", "window": 1, "window_name": "codex", "window_active": True, "active": True, "process_label": "codex"},
                {"target": "%3", "window": 2, "window_name": "codex", "window_active": False, "active": True, "process_label": "pytest"},
            ],
        },
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelectorAll('.panel-detail-row [data-window-index]').length === 3;
            """
        )
    )
    buttons = browser.execute_script(
        """
        return Array.from(document.querySelectorAll('.panel-detail-row [data-window-index]')).map(button => ({
          text: button.querySelector('.tmux-window-name-label')?.textContent.trim() || button.textContent.trim(),
          index: button.dataset.windowIndex || '',
          pressed: button.getAttribute('aria-pressed') || '',
          active: button.classList.contains('active'),
        }));
        """
    )
    browser.find_element("css selector", '.panel-detail-row [data-window-index="2"]').click()
    fetches = browser.execute_script(
        """
        return window.__bootFetches
          .filter(item => item.path === '/api/tmux-window')
          .map(item => `${item.method} ${item.path}`);
        """
    )
    query = browser.execute_script(
        """
        const item = window.__bootFetches.find(entry => entry.path === '/api/tmux-window');
        return item ? new URLSearchParams(item.search || '').toString() : '';
        """
    )
    assert buttons == [
        {"text": "0:bash", "index": "0", "pressed": "false", "active": False},
        {"text": "1:codex", "index": "1", "pressed": "true", "active": True},
        {"text": "2:pytest", "index": "2", "pressed": "false", "active": False},
    ]
    assert fetches == ["POST /api/tmux-window"]
    assert query == "session=1&window=2"


def test_dockview_terminal_info_bar_alignment_and_detail_toggle_refits_xterm(browser, tmp_path):
    transcript_sessions = {
        "1": {
            "agents": [{"kind": "claude", "transcript": True, "pane_target": "%2"}],
            "panes": [
                {"target": "%1", "window": 0, "window_name": "bash", "window_active": False, "active": True, "process_label": "bash"},
                {"target": "%2", "window": 1, "window_name": "claude", "window_active": True, "active": True, "process_label": "claude"},
                {"target": "%3", "window": 2, "window_name": "codex", "window_active": False, "active": True, "process_label": "codex"},
            ],
        },
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
        transcript_sessions=transcript_sessions,
        terminal_css=".terminal { width: 100%; height: 100%; } #term-1 .xterm { width: 100%; height: 100%; }",
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelectorAll('#panel-1 .panel-detail-row [data-window-index]').length === 3
              && document.querySelector('#term-1 .xterm')
              && document.querySelector('.dockview-pane-header-actions [data-detail-toggle="1"]');
            """
        )
    )
    before = browser.execute_script(
        """
        const rectFor = node => {
          const rect = node.getBoundingClientRect();
          return {top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right, width: rect.width, height: rect.height};
        };
        const panel = document.querySelector('#panel-1');
        const row = panel.querySelector('.panel-detail-row');
        const bar = row.querySelector('.tmux-window-bar');
        const close = row.querySelector('.panel-detail-close');
        const headerTerminal = document.querySelector('.dockview-pane-header-actions .terminal-tab');
        const pane = panel.querySelector('#terminal-pane-1');
        const xterm = panel.querySelector('#term-1 .xterm');
        const term = terminals.get('1')?.term;
        if (term) {
          const originalResize = term.resize.bind(term);
          term.__detailToggleResizeCount = 0;
          term.__detailToggleRowsBefore = term.rows;
          term.resize = (cols, rows) => {
            term.__detailToggleResizeCount += 1;
            originalResize(cols, rows);
          };
        }
        return {
          row: rectFor(row),
          bar: rectFor(bar),
          close: rectFor(close),
          headerTerminalText: headerTerminal?.textContent.trim() || '',
          headerTerminalTitle: headerTerminal?.getAttribute('title') || '',
          pane: rectFor(pane),
          xterm: rectFor(xterm),
          rowDisplay: getComputedStyle(row).display,
          rows: term?.rows || 0,
        };
        """
    )
    browser.find_element("css selector", '.dockview-pane-header-actions [data-detail-toggle="1"]').click()
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const panel = document.querySelector('#panel-1');
            return panel?.classList.contains('details-collapsed');
            """
        )
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const term = terminals.get('1')?.term;
            return (term?.__detailToggleResizeCount || 0) > 0;
            """
        )
    )
    after = browser.execute_script(
        """
        const rectFor = node => {
          const rect = node.getBoundingClientRect();
          return {top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right, width: rect.width, height: rect.height};
        };
        const panel = document.querySelector('#panel-1');
        const row = panel.querySelector('.panel-detail-row');
        const pane = panel.querySelector('#terminal-pane-1');
        const container = panel.querySelector('#term-1');
        const xterm = panel.querySelector('#term-1 .xterm');
        const headerToggle = document.querySelector('.dockview-pane-header-actions [data-detail-toggle="1"]');
        const term = terminals.get('1')?.term;
        return {
          panel: rectFor(panel),
          rowDisplay: getComputedStyle(row).display,
          headerPressed: headerToggle?.getAttribute('aria-pressed') || '',
          headerTitle: headerToggle?.getAttribute('title') || '',
          pane: rectFor(pane),
          paneActive: pane.classList.contains('active'),
          container: rectFor(container),
          containerClientWidth: container.clientWidth,
          containerClientHeight: container.clientHeight,
          xterm: rectFor(xterm),
          resizeCount: term?.__detailToggleResizeCount || 0,
          rowsBefore: term?.__detailToggleRowsBefore || 0,
          rowsAfter: term?.rows || 0,
        };
        """
    )
    assert before["headerTerminalText"] == "Term"
    assert before["headerTerminalTitle"] == "terminal: claude"
    assert 0 <= before["close"]["left"] - before["bar"]["right"] <= 8
    assert before["row"]["right"] - before["close"]["right"] <= 8
    assert before["rowDisplay"] == "flex"
    assert before["xterm"]["top"] >= before["row"]["bottom"] - 1
    assert after["rowDisplay"] == "none"
    assert after["headerPressed"] == "false"
    assert after["headerTitle"].lower() == "show details"
    assert after["resizeCount"] >= 1, after
    assert after["xterm"]["top"] >= after["panel"]["top"] - 1
    assert after["xterm"]["bottom"] <= after["pane"]["bottom"] + 1
    assert after["rowsAfter"] >= before["rows"]


def test_dockview_new_virtual_and_file_tabs_open_in_focused_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=row@50(left,slot1)&tabs=left:1;slot1:2",
        sessions=["1", "2"],
    )
    wait_for_dockview(browser, min_tabs=2)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          activatePaneTab('slot1', '2', {userInitiated: true});
          setFocusedPanelItem('2', {userInitiated: true});
          await selectSession(prefsItemId, {userInitiated: true});
          const prefsSlot = slotForItem(prefsItemId);
          activatePaneTab('slot1', '2', {userInitiated: true});
          setFocusedPanelItem('2', {userInitiated: true});
          await openInfoSubTab('yoagent');
          const infoSlot = slotForItem(infoItemId);
          activatePaneTab('slot1', '2', {userInitiated: true});
          setFocusedPanelItem('2', {userInitiated: true});
          const filePath = '/home/test/yolomux.dev/NEWTAB.md';
          const fileItem = await openFileInEditor(filePath, {name: 'NEWTAB.md'}, {userInitiated: true});
          done({
            prefsSlot,
            infoSlot,
            fileSlot: slotForItem(fileItem),
            slot1Tabs: paneTabs('slot1'),
            leftTabs: paneTabs('left'),
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    assert metrics.get("error") is None, metrics
    assert metrics["prefsSlot"] == "slot1", metrics
    assert metrics["infoSlot"] == "slot1", metrics
    assert metrics["fileSlot"] == "slot1", metrics
    assert "1" in metrics["leftTabs"], metrics
    assert "2" in metrics["slot1Tabs"], metrics


def test_differ_reopen_keeps_dragged_file_tab_home(browser, tmp_path):
    path = "/repo/app/src/main.py"
    item = f"filediff:{path}"
    session_files_payload = {
        "session": "1",
        "loaded": True,
        "errors": [],
        "refs_by_repo": {},
        "repos": [{"repo": "/repo/app"}],
        "files": [{"session": "1", "agent": "codex", "status": "M", "repo": "/repo/app", "path": "src/main.py", "abs_path": path, "mtime": 100, "added": 1, "removed": 1}],
    }
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1,2&layout=row@24(left,row@50(slot1,slot2))&tabs=left:files;slot1:1;slot2:2",
        sessions=["1", "2"],
        session_files_payload=session_files_payload,
        grid_width=1300,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_visible_selector(browser, '.dockview-pane-tab[data-pane-tab="1"]')
    wait_for_visible_selector(browser, '.dockview-pane-tab[data-pane-tab="2"]')
    opened = browser.execute_async_script(
        """
        const path = arguments[0];
        const done = arguments[arguments.length - 1];
        const originalFetch = window.fetch.bind(window);
        window.__doit65Fetches = [];
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), 'https://localhost');
          window.__doit65Fetches.push(url.pathname + url.search);
          if (url.pathname === '/api/fs/read') {
            return new Response(JSON.stringify({
              path: url.searchParams.get('path') || path,
              content: 'print("hello")\\n',
              size: 15,
              mtime: 1,
              mtime_ns: 1,
              realpath: path,
              file_id: 'dev:10:ino:20',
              git_root: '/repo/app',
              git_tracked: true,
              git_history: [{ref: 'a'}, {ref: 'b'}],
              git_has_history: true,
            }), {status: 200, headers: {'Content-Type': 'application/json'}});
          }
          if (url.pathname === '/api/fs/diff') {
            return new Response(JSON.stringify({
              repo: '/repo/app',
              relative_path: 'src/main.py',
              diff: '@@ -1 +1 @@\\n-print("old")\\n+print("hello")\\n',
              original: 'print("old")\\n',
              working: 'print("hello")\\n',
            }), {status: 200, headers: {'Content-Type': 'application/json'}});
          }
          return originalFetch(input, options);
        };
        const waitFor = predicate => new Promise((resolve, reject) => {
          let attempts = 0;
          const tick = () => {
            try {
              const value = predicate();
              if (value) { resolve(value); return; }
            } catch (error) {
              reject(error);
              return;
            }
            attempts += 1;
            if (attempts > 120) {
              reject(new Error('timed out waiting for Differ row/opened tab'));
              return;
            }
            requestAnimationFrame(tick);
          };
          tick();
        });
        (async () => {
          setFileExplorerMode('diff', {force: true});
          renderFileExplorerChangesPanels({force: true});
          const row = await waitFor(() => document.querySelector(`[data-open-change-file="${path}"]`));
          row.click();
          await waitFor(() => slotForItem(`filediff:${path}`) === 'slot1');
          done({
            slot: slotForItem(`filediff:${path}`),
            mode: editorViewModeFor(path, `filediff:${path}`),
            rows: document.querySelectorAll(`[data-open-change-file="${path}"]`).length,
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """,
        path,
    )
    assert opened.get("error") is None, opened
    assert opened["slot"] == "slot1", opened
    assert opened["mode"] == "diff", opened
    assert opened["rows"] == 1, opened

    points = browser.execute_script(
        """
        const rectPoint = (rect, x = 0.5, y = 0.5) => ({x: Math.round(rect.left + rect.width * x), y: Math.round(rect.top + rect.height * y)});
        const tab = document.querySelector(`.dockview-pane-tab[data-pane-tab="${CSS.escape(arguments[0])}"]`);
        const targetGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        return {
          start: rectPoint(tab.closest('.dv-tab').getBoundingClientRect()),
          end: rectPoint(targetGroup.getBoundingClientRect(), 0.55, 0.5),
        };
        """,
        item,
    )
    cdp_drag(browser, points["start"], points["end"], steps=28)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return slotForItem(arguments[0])", item) == "slot2"
    )

    reopened = browser.execute_async_script(
        """
        const path = arguments[0];
        const item = `filediff:${path}`;
        const done = arguments[arguments.length - 1];
        const waitFor = predicate => new Promise((resolve, reject) => {
          let attempts = 0;
          const tick = () => {
            try {
              const value = predicate();
              if (value) { resolve(value); return; }
            } catch (error) {
              reject(error);
              return;
            }
            attempts += 1;
            if (attempts > 120) {
              reject(new Error('timed out waiting for Differ reopen'));
              return;
            }
            requestAnimationFrame(tick);
          };
          tick();
        });
        (async () => {
          setFileEditorViewMode(path, 'edit', item);
          renderOpenFilePath(path);
          document.querySelector(`[data-open-change-file="${path}"]`).click();
          await waitFor(() => slotForItem(item) === 'slot2' && activeItemForSide('slot2') === item && editorViewModeFor(path, item) === 'diff');
          done({
            slot: slotForItem(item),
            mode: editorViewModeFor(path, item),
            slot2Tabs: paneTabs('slot2'),
            leftTabs: paneTabs('left'),
            slot1Tabs: paneTabs('slot1'),
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """,
        path,
    )
    assert reopened.get("error") is None, reopened
    assert reopened["slot"] == "slot2", reopened
    assert reopened["mode"] == "diff", reopened
    assert item in reopened["slot2Tabs"], reopened
    assert item not in reopened["slot1Tabs"], reopened
    assert item not in reopened["leftTabs"], reopened


def test_dockview_symlink_alias_focuses_existing_file_editor(browser, tmp_path):
    real_path = "/repo/app/src/main.py"
    link_path = "/repo/app/link-main.py"
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1&layout=left&tabs=left:1",
        sessions=["1"],
    )
    wait_for_dockview(browser, min_tabs=1)
    metrics = browser.execute_async_script(
        """
        const realPath = arguments[0];
        const linkPath = arguments[1];
        const done = arguments[arguments.length - 1];
        const originalFetch = window.fetch.bind(window);
        window.__doit65Fetches = [];
        window.fetch = async (input, options = {}) => {
          const url = new URL(String(input), 'https://localhost');
          window.__doit65Fetches.push(url.pathname + url.search);
          if (url.pathname === '/api/fs/read') {
            return new Response(JSON.stringify({
              path: url.searchParams.get('path') || realPath,
              content: 'print("hello")\\n',
              size: 15,
              mtime: 1,
              mtime_ns: 1,
              realpath: realPath,
              file_id: 'dev:10:ino:20',
              git_root: '/repo/app',
              git_tracked: true,
              git_history: [{ref: 'a'}, {ref: 'b'}],
              git_has_history: true,
            }), {status: 200, headers: {'Content-Type': 'application/json'}});
          }
          if (url.pathname === '/api/fs/diff') {
            return new Response(JSON.stringify({
              repo: '/repo/app',
              relative_path: 'src/main.py',
              diff: '@@ -1 +1 @@\\n-print("old")\\n+print("hello")\\n',
              original: 'print("old")\\n',
              working: 'print("hello")\\n',
            }), {status: 200, headers: {'Content-Type': 'application/json'}});
          }
          return originalFetch(input, options);
        };
        (async () => {
          const first = await openFileInEditor(realPath, {name: 'main.py', realpath: realPath, file_id: 'dev:10:ino:20'}, {viewMode: 'edit'});
          const state = fileStateFor(realPath);
          state.content = 'dirty edit\\n';
          state.dirty = true;
          await openChangedFileInDiff(linkPath, '1', 'M', '/repo/app', {forceNewTab: true, userInitiated: true, openMode: 'diff'});
          const afterDiffActionItems = openFileEditorItems();
          const second = await openFileInAdditionalEditorTab(linkPath, {name: 'link-main.py', realpath: realPath, file_id: 'dev:10:ino:20'}, {viewMode: 'diff'});
          done({
            first,
            second,
            afterDiffActionItems,
            openItems: openFileEditorItems(),
            realItems: filePanelItemsForPath(realPath),
            linkItems: filePanelItemsForPath(linkPath),
            content: fileStateFor(realPath)?.content || '',
            dirty: fileStateFor(realPath)?.dirty === true,
            mode: editorViewModeFor(realPath, first),
            tabCount: Array.from(document.querySelectorAll('.dockview-pane-tab')).filter(tab => String(tab.dataset.paneTab || '').includes('main.py')).length,
            readCalls: window.__doit65Fetches.filter(url => url.startsWith('/api/fs/read')).length,
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """,
        real_path,
        link_path,
    )
    assert metrics.get("error") is None, metrics
    assert metrics["second"] == metrics["first"], metrics
    assert metrics["afterDiffActionItems"] == [metrics["first"]], metrics
    assert metrics["openItems"] == [metrics["first"]], metrics
    assert metrics["realItems"] == [metrics["first"]], metrics
    assert metrics["linkItems"] == [], metrics
    assert metrics["content"] == "dirty edit\n", metrics
    assert metrics["dirty"] is True, metrics
    assert metrics["mode"] == "diff", metrics
    assert metrics["tabCount"] == 1, metrics
    assert metrics["readCalls"] == 2, metrics


def test_dockview_new_tabs_do_not_open_in_focused_finder(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@22(left,slot1)&tabs=left:files;slot1:1",
        sessions=["1"],
    )
    wait_for_dockview(browser, min_tabs=2)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          activatePaneTab('left', fileExplorerItemId, {userInitiated: true});
          setFocusedPanelItem(fileExplorerItemId, {userInitiated: true});
          await selectSession(prefsItemId, {userInitiated: true});
          done({
            prefsSlot: slotForItem(prefsItemId),
            finderSlot: slotForItem(fileExplorerItemId),
            leftTabs: paneTabs('left'),
            slot1Tabs: paneTabs('slot1'),
          });
        })().catch(error => done({error: String(error && error.stack || error)}));
        """
    )
    assert metrics.get("error") is None, metrics
    assert metrics["finderSlot"] == "left", metrics
    assert metrics["prefsSlot"] == "slot1", metrics
    assert "__prefs__" not in metrics["leftTabs"], metrics


def test_dockview_many_tabs_wrap_above_content(browser, tmp_path):
    sessions = [str(index) for index in range(1, 10)]
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3,4,5,6,7,8,9&layout=left&tabs=left:1,2,3,4,5,6,7,8,9",
        sessions=sessions,
    )
    wait_for_dockview(browser, min_tabs=9)
    wait_for_dockview_tab_geometry(browser, min_tabs=9, min_width=150, min_rows=2)
    metrics = dockview_layout_metrics(browser)
    tab_tops = sorted({item["rect"]["top"] for item in metrics["tabStyles"]})
    tab_widths = {item["rect"]["width"] for item in metrics["tabStyles"]}
    tab_height = metrics["tabStyles"][0]["rect"]["height"]
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert metrics["groups"][0]["tabs"] == sessions
    assert len(tab_tops) >= 2
    assert max(tab_widths) <= 181
    assert min(tab_widths) >= 170
    assert metrics["header"]["height"] >= (tab_height * 2) - 2
    assert metrics["header"]["tabsOverflowX"] == "visible"
    assert metrics["header"]["tabsScrollWidth"] <= metrics["header"]["tabsClientWidth"] + 1
    assert metrics["header"]["tabsScrollbarWidth"] == "none"
    assert metrics["header"]["tabsWebkitScrollbarDisplay"] == "none"
    assert metrics["header"]["allTabsInsideHeader"] is True


def test_dockview_file_editor_tabs_stay_above_toolbar(browser, tmp_path):
    encoded_files = [
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fmissing%20dynamo.rs",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2FCargo.toml",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fstatic%2Fyolomux.css",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fstatic_src%2Fjs%2Fyolomux%2F60_popovers_tabs.js",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2FREADME.md",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fdocs%2FGUI_SPECS.md",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fdocs%2FDEVELOPMENT.md",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Ftests%2Ftest_browser_layout.py",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fstatic_src%2Fcss%2Fyolomux%2F40_layout_panes_tabs.css",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fyolomux_lib%2Fsettings.py",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fpyproject.toml",
        "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2Fpytest.ini",
    ]
    token = ",".join(encoded_files)
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        f"?sessions={token}&layout=left&tabs=left:{token}",
        sessions=[],
        grid_width=1120,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=len(encoded_files))
    wait_for_dockview_tab_geometry(browser, min_tabs=len(encoded_files), min_width=150, min_rows=2)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return Boolean(document.querySelector('.file-editor-toolbar:not([hidden])'))")
    )
    metrics = browser.execute_script(
        """
        const group = document.querySelector('.file-editor-panel').closest('.dv-groupview');
        const tabsContainer = group.querySelector('.dv-tabs-container');
        const actions = group.querySelector('.dockview-pane-header-actions');
        const rectFor = rect => ({
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          top: Math.round(rect.top),
          bottom: Math.round(rect.bottom),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        });
        const header = group.querySelector('.dv-tabs-and-actions-container').getBoundingClientRect();
        const toolbar = group.querySelector('.file-editor-toolbar').getBoundingClientRect();
        const tabs = Array.from(group.querySelectorAll('.dockview-pane-tab')).map(tab => rectFor(tab.getBoundingClientRect()));
        const rowsByTop = new Map();
        for (const rect of tabs) {
          const top = String(rect.top);
          if (!rowsByTop.has(top)) rowsByTop.set(top, []);
          rowsByTop.get(top).push(rect);
        }
        const rows = Array.from(rowsByTop.entries())
          .map(([top, rects]) => ({top: Number(top), rects, right: Math.max(...rects.map(rect => rect.right))}))
          .sort((a, b) => a.top - b.top);
        const firstRowRight = rows[0]?.right || 0;
        const laterRowRight = Math.max(...rows.slice(1).map(row => row.right));
        const actionsRect = actions.getBoundingClientRect();
        const activeTab = group.querySelector('.dv-tab.dv-active-tab .dockview-pane-tab');
        const activeRect = activeTab.getBoundingClientRect();
        const hit = document.elementFromPoint(Math.round(activeRect.left + activeRect.width / 2), Math.round(activeRect.top + activeRect.height / 2));
        return {
          header: rectFor(header),
          toolbar: rectFor(toolbar),
          tabRows: new Set(tabs.map(rect => rect.top)).size,
          tabHeight: tabs[0]?.height || 0,
          tabsOverflowX: getComputedStyle(tabsContainer).overflowX,
          tabsScrollWidth: Math.round(tabsContainer.scrollWidth),
          tabsClientWidth: Math.round(tabsContainer.clientWidth),
          actionLeft: Math.round(actionsRect.left),
          firstRowRight,
          laterRowRight,
          tabsOverlapToolbar: tabs.filter(rect => rect.bottom > toolbar.top + 1),
          activeTab: rectFor(activeRect),
          activeTabClickable: Boolean(hit?.closest?.('.dockview-pane-tab') === activeTab),
        };
        """
    )
    assert metrics["tabRows"] >= 2, metrics
    assert metrics["tabsOverflowX"] == "visible", metrics
    assert metrics["tabsScrollWidth"] <= metrics["tabsClientWidth"] + 1, metrics
    assert metrics["firstRowRight"] <= metrics["actionLeft"] - 1, metrics
    assert metrics["laterRowRight"] >= metrics["actionLeft"] + 20, metrics
    assert metrics["tabsOverlapToolbar"] == [], metrics
    assert metrics["header"]["height"] >= (metrics["tabHeight"] * 2) - 2, metrics
    assert metrics["activeTab"]["bottom"] <= metrics["toolbar"]["top"] + 1, metrics
    assert metrics["header"]["bottom"] <= metrics["toolbar"]["top"] + 1, metrics
    assert metrics["activeTabClickable"] is True, metrics


def test_dockview_drag_reorders_tabs_in_same_pane(browser, tmp_path):
    # REAL end-to-end reorder: the drag itself must produce the new order. (The previous version of this
    # test force-wrote the expected layout via execute_script after the drag, which let the actual bug —
    # the no-op veto hit-testing the smooth-reorder dragged tab under the cursor and silently swallowing
    # every slow same-strip reorder — live in production undetected.)
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=left&tabs=left:1,2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.68, 0.5)
    cdp_drag(browser, start, end)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return paneTabs('left', layoutSlots).slice(0, 2).join(',');") == "2,1"
    )
    metrics = dockview_layout_metrics(browser)
    assert metrics["groups"][0]["tabs"] == ["2", "1", "3"]
    assert metrics["slots"]["left"]["tabs"] == ["2", "1", "3"]
    assert "tabs=left:2,1" in metrics["url"]


def test_dockview_drag_reorders_two_tab_pane(browser, tmp_path):
    # Two-tab strip is the tightest case: the dragged tab covers the drop point (smooth reorder), and the
    # pinned pointer fallback used to mirror-swap it back. Both must stay fixed.
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:1,2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.68, 0.5)
    cdp_drag(browser, start, end)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return paneTabs('left', layoutSlots).join(',');") == "2,1"
    )
    assert dockview_layout_metrics(browser)["groups"][0]["tabs"] == ["2", "1"]


def test_dockview_drag_reorders_two_pinned_tabs(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:1,2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    browser.execute_script("setTabPinned('1', true); setTabPinned('2', true);")
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.68, 0.5)
    cdp_drag(browser, start, end)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return paneTabs('left', layoutSlots).join(',');") == "2,1"
    )
    assert dockview_layout_metrics(browser)["groups"][0]["tabs"] == ["2", "1"]


def test_dockview_pinned_tabs_render_first_after_pin_toggle(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=left&tabs=left:1,2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    browser.execute_script("setTabPinned('2', true);")
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["groups"][0]["tabs"][0] == "2"
    )
    metrics = dockview_layout_metrics(browser)
    assert metrics["groups"][0]["tabs"] == ["2", "1", "3"], metrics
    assert metrics["slots"]["left"]["tabs"] == ["2", "1", "3"], metrics
    pinned = browser.execute_script(
        """
        const first = document.querySelector('.dv-groupview .dockview-pane-tab');
        return {
          item: first?.dataset?.paneTab || '',
          pinned: first?.classList?.contains('pinned-tab') || false,
          hasIcon: Boolean(first?.querySelector('.pane-tab-pin-icon')),
        };
        """
    )
    assert pinned == {"item": "2", "pinned": True, "hasIcon": True}


def test_dockview_first_pinned_tab_drags_after_second_pinned_tab(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=left&tabs=left:1,2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    browser.execute_script("setTabPinned('1', true); setTabPinned('2', true);")
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["groups"][0]["tabs"][:2] == ["1", "2"]
    )
    points = browser.execute_script(
        """
        const point = (selector, xRatio) => {
          const rect = document.querySelector(selector).getBoundingClientRect();
          return {x: Math.round(rect.left + rect.width * xRatio), y: Math.round(rect.top + rect.height / 2)};
        };
        return {
          start: point('.dockview-pane-tab[data-pane-tab="1"]', 0.5),
          end: point('.dockview-pane-tab[data-pane-tab="2"]', 0.35),
        };
        """
    )
    cdp_drag(browser, points["start"], points["end"])
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["groups"][0]["tabs"][:2] == ["2", "1"]
    )
    metrics = dockview_layout_metrics(browser)
    assert metrics["groups"][0]["tabs"] == ["2", "1", "3"], metrics
    assert metrics["slots"]["left"]["tabs"] == ["2", "1", "3"], metrics


def test_dockview_non_pinned_tab_cannot_drop_between_pinned_tabs(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=left&tabs=left:1,2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    browser.execute_script("setTabPinned('1', true); setTabPinned('2', true);")
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["groups"][0]["tabs"] == ["1", "2", "3"]
    )
    points = browser.execute_script(
        """
        const point = (selector, xRatio) => {
          const rect = document.querySelector(selector).getBoundingClientRect();
          return {x: Math.round(rect.left + rect.width * xRatio), y: Math.round(rect.top + rect.height / 2)};
        };
        return {
          start: point('.dockview-pane-tab[data-pane-tab="3"]', 0.5),
          end: point('.dockview-pane-tab[data-pane-tab="2"]', 0.35),
        };
        """
    )
    try:
        cdp_drag_hold(browser, points["start"], points["end"], steps=32)
        browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            requestAnimationFrame(() => requestAnimationFrame(done));
            """
        )
        preview = dockview_invalid_drop_preview(browser)
    finally:
        cdp_release(browser, points["end"])
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(done));
        """
    )
    metrics = dockview_layout_metrics(browser)
    assert preview["invalidPreview"] is True, preview
    assert any(item["borderStyle"] == "dashed" and item["borderColor"] == preview["dangerColor"] for item in preview["previews"]), preview
    assert metrics["groups"][0]["tabs"] == ["1", "2", "3"], metrics
    assert metrics["slots"]["left"]["tabs"] == ["1", "2", "3"], metrics
    assert browser.execute_script("return document.querySelector('.yolomux-dockview')?.classList.contains('dockview-invalid-tab-drop-preview') || false") is False


def test_dockview_pinned_tab_cannot_move_to_other_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=row@50(left,right)&tabs=left:1;right:2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    browser.execute_script("setTabPinned('1', true);")
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["slots"]["left"]["tabs"] == ["1"]
    )
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.72, 0.5)
    try:
        cdp_drag_hold(browser, start, end, steps=32)
        WebDriverWait(browser, 2).until(
            lambda driver: dockview_invalid_drop_preview(driver)["invalidPreview"] is True
        )
        preview = dockview_invalid_drop_preview(browser)
    finally:
        cdp_release(browser, end)
    assert preview["invalidPreview"] is True, preview
    assert any(item["borderStyle"] == "dashed" and item["borderColor"] == preview["dangerColor"] for item in preview["previews"]), preview
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["slots"]["left"]["tabs"] == ["1"]
    )
    metrics = dockview_layout_metrics(browser)
    assert metrics["slots"]["left"]["tabs"] == ["1"], metrics
    assert metrics["slots"]["right"]["tabs"] == ["2", "3"], metrics
    assert any(group["tabs"] == ["1"] for group in metrics["groups"]), metrics
    assert browser.execute_script("return document.querySelector('.yolomux-dockview')?.classList.contains('dockview-invalid-tab-drop-preview') || false") is False


def test_dockview_pinned_tab_cannot_split_to_new_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:1,2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    browser.execute_script("setTabPinned('2', true);")
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["slots"]["left"]["tabs"] == ["2", "1"]
    )
    content_attempt = browser.execute_script(
        """
        const tab = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]');
        const group = tab.closest('.dv-groupview');
        const slot = dockviewSlotForGroupElement(group);
        const rect = group.getBoundingClientRect();
        const event = {
          kind: 'content',
          position: 'right',
          group: {id: slot},
          nativeEvent: {clientX: Math.round(rect.right - 8), clientY: Math.round(rect.top + rect.height / 2)},
          getData() { return {panelId: '2', groupId: slot}; },
          preventDefault() { this.prevented = true; },
        };
        const intent = dockviewPaneContentDropIntent(event);
        dockviewTrackRootBoundaryOverlay(event);
        return {
          intent: intent ? {zone: intent.zone, targetSlot: intent.targetSlot} : null,
          prevented: event.prevented === true,
          rootPreview: document.querySelector('#grid').classList.contains('drop-preview-root'),
        };
        """
    )
    assert content_attempt["intent"] is None, content_attempt
    assert content_attempt["prevented"] is True, content_attempt
    assert content_attempt["rootPreview"] is False, content_attempt
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.5, 0.5)
    end = dockview_point(browser, "#dockviewRoot", 0.97, 0.5)
    try:
        cdp_drag_hold(browser, start, end, steps=32)
        browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            requestAnimationFrame(() => requestAnimationFrame(done));
            """
        )
        preview = browser.execute_script(
            """
            return {
              rootPreview: document.querySelector('#grid')?.classList.contains('drop-preview-root') || false,
              invalidPreview: document.querySelector('.yolomux-dockview')?.classList.contains('dockview-invalid-tab-drop-preview') || false,
            };
            """
        )
    finally:
        cdp_release(browser, end)
    assert preview["rootPreview"] is False, preview
    assert preview["invalidPreview"] is False, preview
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(done));
        """
    )
    metrics = dockview_layout_metrics(browser)
    assert metrics["slots"]["left"]["tabs"] == ["2", "1"], metrics
    assert len([group for group in metrics["groups"] if group["tabs"]]) == 1, metrics
    assert any(group["tabs"] == ["2", "1"] for group in metrics["groups"]), metrics


def test_dockview_pinned_tab_invalid_non_pinned_target_shows_red_dashes(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=row@50(left,right)&tabs=left:1;right:2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    browser.execute_script("setTabPinned('1', true); setTabPinned('2', true);")
    WebDriverWait(browser, 5).until(
        lambda driver: dockview_layout_metrics(driver)["slots"]["right"]["tabs"] == ["2", "3"]
    )
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.72, 0.5)
    try:
        cdp_drag_hold(browser, start, end, steps=32)
        WebDriverWait(browser, 2).until(
            lambda driver: dockview_invalid_drop_preview(driver)["invalidPreview"] is True
        )
        preview = dockview_invalid_drop_preview(browser)
    finally:
        cdp_release(browser, end)
    assert preview["invalidPreview"] is True, preview
    assert any(item["borderStyle"] == "dashed" and item["borderColor"] == preview["dangerColor"] for item in preview["previews"]), preview
    WebDriverWait(browser, 2).until(
        lambda driver: dockview_layout_metrics(driver)["slots"]["left"]["tabs"] == ["1"]
    )
    metrics = dockview_layout_metrics(browser)
    assert metrics["slots"]["left"]["tabs"] == ["1"], metrics
    assert metrics["slots"]["right"]["tabs"] == ["2", "3"], metrics
    assert browser.execute_script("return document.querySelector('.yolomux-dockview')?.classList.contains('dockview-invalid-tab-drop-preview') || false") is False


def test_dockview_pane_drag_handle_swaps_whole_panes(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3&layout=row@50(left,right)&tabs=left:1,2;right:3",
        sessions=["1", "2", "3"],
        grid_width=1200,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    before = dockview_layout_metrics(browser)
    assert [group["tabs"] for group in sorted(before["groups"], key=lambda item: item["rect"]["left"])] == [["1", "2"], ["3"]], before
    points = browser.execute_script(
        """
        const rectPoint = (rect, x = 0.5, y = 0.5) => ({x: Math.round(rect.left + rect.width * x), y: Math.round(rect.top + rect.height * y)});
        const sourceGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const targetGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="3"]').closest('.dv-groupview');
        const handle = sourceGroup.querySelector('.pane-drag-handle');
        return {
          start: rectPoint(handle.getBoundingClientRect()),
          end: rectPoint(targetGroup.getBoundingClientRect()),
          handleSlot: handle.dataset.paneDrag || '',
          canSwap: paneSwapAllowed(dockviewSlotForGroupElement(sourceGroup), dockviewSlotForGroupElement(targetGroup)),
        };
        """
    )
    assert points["handleSlot"] == "left"
    assert points["canSwap"] is True
    cdp_drag(browser, points["start"], points["end"], steps=28)
    WebDriverWait(browser, 5).until(
        lambda driver: [group["tabs"] for group in sorted(dockview_layout_metrics(driver)["groups"], key=lambda item: item["rect"]["left"])] == [["3"], ["1", "2"]]
    )
    after = dockview_layout_metrics(browser)
    assert after["slots"]["left"]["tabs"] == ["3"], after
    assert after["slots"]["right"]["tabs"] == ["1", "2"], after
    assert after["slots"]["__tree"]["split"] == "row", after
    assert round(after["slots"]["__tree"]["pct"]) == 50, after


def test_dockview_pane_drag_shows_dotted_pane_preview(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2",
        sessions=["1", "2"],
        grid_width=1200,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    points = browser.execute_script(
        """
        const rectPoint = (rect, x = 0.5, y = 0.5) => ({x: Math.round(rect.left + rect.width * x), y: Math.round(rect.top + rect.height * y)});
        const sourceGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const targetGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const header = sourceGroup.querySelector('.dv-tabs-and-actions-container');
        const tabs = sourceGroup.querySelector('.dv-tabs-container').getBoundingClientRect();
        const tab = sourceGroup.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-tab').getBoundingClientRect();
        const headerRect = header.getBoundingClientRect();
        const x = Math.round(Math.min(headerRect.right - 84, Math.max(tab.right + 36, tabs.left + 230)));
        const y = Math.round(headerRect.top + Math.min(12, headerRect.height / 2));
        return {
          start: {x, y},
          end: rectPoint(targetGroup.getBoundingClientRect(), 0.55, 0.5),
        };
        """
    )
    try:
        cdp_drag_hold(browser, points["start"], points["end"], steps=28)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script("return Boolean(document.querySelector('.pane-drag-image.drag-image'))")
        )
        metrics = browser.execute_script(
            """
            const ghost = document.querySelector('.pane-drag-image.drag-image');
            const style = getComputedStyle(ghost);
            const rect = ghost.getBoundingClientRect();
            return {
              slot: ghost.dataset.dragSlot || '',
              borderStyle: style.borderTopStyle,
              borderColor: style.borderTopColor,
              separatorColor: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
              text: ghost.textContent,
            };
            """
        )
        assert metrics["slot"] == "left", metrics
        assert metrics["borderStyle"] == "dotted", metrics
        assert metrics["borderColor"] == metrics["separatorColor"], metrics
        assert metrics["width"] >= 180 and metrics["height"] >= 120, metrics
        assert "1 tab" in metrics["text"], metrics
    finally:
        cdp_release(browser, points["end"])
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return !document.querySelector('.pane-drag-image.drag-image')")
    )


def test_dockview_panel_detail_row_drags_whole_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2",
        sessions=["1", "2"],
        grid_width=1200,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    points = browser.execute_script(
        """
        const rectPoint = (rect, x = 0.5, y = 0.5) => ({x: Math.round(rect.left + rect.width * x), y: Math.round(rect.top + rect.height * y)});
        const sourceGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const targetGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const detail = sourceGroup.querySelector('.panel-detail-row');
        const detailRect = detail.getBoundingClientRect();
        const start = {x: Math.round(detailRect.left + Math.min(220, detailRect.width * 0.45)), y: Math.round(detailRect.top + detailRect.height / 2)};
        const hit = document.elementFromPoint(start.x, start.y);
        return {
          start,
          end: rectPoint(targetGroup.getBoundingClientRect(), 0.55, 0.5),
          detailDragSlot: detail.dataset.paneDragSlot || '',
          hitExcluded: Boolean(hit?.closest?.('button, input, textarea, select, a')),
        };
        """
    )
    assert points["detailDragSlot"] == "left", points
    assert points["hitExcluded"] is False, points
    try:
        cdp_drag_hold(browser, points["start"], points["end"], steps=28)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script("return Boolean(document.querySelector('.pane-drag-image.drag-image'))")
        )
        preview = browser.execute_script(
            """
            const ghost = document.querySelector('.pane-drag-image.drag-image');
            const style = getComputedStyle(ghost);
            return {
              slot: ghost.dataset.dragSlot || '',
              borderStyle: style.borderTopStyle,
              text: ghost.textContent.trim().replace(/\\s+/g, ' '),
            };
            """
        )
        assert preview["slot"] == "left", preview
        assert preview["borderStyle"] == "dotted", preview
        assert "1 tab" in preview["text"], preview
    finally:
        cdp_release(browser, points["end"])
    WebDriverWait(browser, 5).until(
        lambda driver: [group["tabs"] for group in sorted(dockview_layout_metrics(driver)["groups"], key=lambda item: item["rect"]["left"])] == [["2"], ["1"]]
    )


def test_dockview_file_editor_toolbar_drags_whole_pane(browser, tmp_path):
    encoded_file = "file%3A%2Fhome%2Ftest%2Fyolomux.dev%2FDONE.md"
    file_item = "file:/home/test/yolomux.dev/DONE.md"
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        f"?sessions=2,{encoded_file}&layout=row@50(left,right)&tabs=left:{encoded_file};right:2",
        sessions=["2"],
        grid_width=1200,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2, min_width=60)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return Boolean(document.querySelector('.file-editor-toolbar:not([hidden])'))")
    )
    points = browser.execute_script(
        """
        const rectPoint = (rect, x = 0.5, y = 0.5) => ({x: Math.round(rect.left + rect.width * x), y: Math.round(rect.top + rect.height * y)});
        const sourceGroup = document.querySelector('.file-editor-panel').closest('.dv-groupview');
        const targetGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const toolbar = sourceGroup.querySelector('.file-editor-toolbar');
        const toolbarRect = toolbar.getBoundingClientRect();
        const excluded = hit => Boolean(hit?.closest?.('button, input, textarea, select, a, [data-diff-ref-input]'));
        const candidates = [0.28, 0.38, 0.48, 0.58, 0.68, 0.78].map(ratio => ({
          x: Math.round(toolbarRect.left + toolbarRect.width * ratio),
          y: Math.round(toolbarRect.top + toolbarRect.height / 2),
        }));
        const start = candidates.find(point => !excluded(document.elementFromPoint(point.x, point.y))) || candidates[0];
        const hit = document.elementFromPoint(start.x, start.y);
        return {
          start,
          end: rectPoint(targetGroup.getBoundingClientRect(), 0.55, 0.5),
          toolbarDragSlot: toolbar.dataset.paneDragSlot || '',
          hitExcluded: excluded(hit),
          hitClass: String(hit?.className || ''),
        };
        """
    )
    assert points["toolbarDragSlot"] == "left", points
    assert points["hitExcluded"] is False, points
    try:
        cdp_drag_hold(browser, points["start"], points["end"], steps=28)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script("return Boolean(document.querySelector('.pane-drag-image.drag-image'))")
        )
        preview = browser.execute_script(
            """
            const ghost = document.querySelector('.pane-drag-image.drag-image');
            const style = getComputedStyle(ghost);
            return {
              slot: ghost.dataset.dragSlot || '',
              borderStyle: style.borderTopStyle,
              text: ghost.textContent.trim().replace(/\\s+/g, ' '),
            };
            """
        )
        assert preview["slot"] == "left", preview
        assert preview["borderStyle"] == "dotted", preview
        assert "1 tab" in preview["text"], preview
    finally:
        cdp_release(browser, points["end"])
    WebDriverWait(browser, 5).until(
        lambda driver: [group["tabs"] for group in sorted(dockview_layout_metrics(driver)["groups"], key=lambda item: item["rect"]["left"])] == [["2"], [file_item]]
    )


def test_dockview_tab_container_background_swaps_whole_panes(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2",
        sessions=["1", "2"],
        grid_width=1200,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    points = browser.execute_script(
        """
        const rectPoint = (rect, x = 0.5, y = 0.5) => ({x: Math.round(rect.left + rect.width * x), y: Math.round(rect.top + rect.height * y)});
        const sourceGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const targetGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const header = sourceGroup.querySelector('.dv-tabs-and-actions-container');
        const tabs = sourceGroup.querySelector('.dv-tabs-container').getBoundingClientRect();
        const tab = sourceGroup.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-tab').getBoundingClientRect();
        const headerRect = header.getBoundingClientRect();
        const x = Math.round(Math.min(headerRect.right - 84, Math.max(tab.right + 36, tabs.left + 230)));
        const y = Math.round(headerRect.top + Math.min(12, headerRect.height / 2));
        const hit = document.elementFromPoint(x, y);
        return {
          start: {x, y},
          end: rectPoint(targetGroup.getBoundingClientRect()),
          hitClass: hit?.className || '',
          hitTab: Boolean(hit?.closest?.('.dv-tab, .dockview-pane-tab, button, [data-pane-drag]')),
          headerDragSlot: header.dataset.paneDragSlot || '',
          headerDraggable: header.draggable === true,
          headerDragSource: header.classList.contains('pane-drag-source'),
        };
        """
    )
    assert points["headerDragSlot"] == "left", points
    assert points["headerDraggable"] is False, points
    assert points["headerDragSource"] is True, points
    assert points["hitTab"] is False, points
    cdp_drag(browser, points["start"], points["end"], steps=28)
    WebDriverWait(browser, 5).until(
        lambda driver: [group["tabs"] for group in sorted(dockview_layout_metrics(driver)["groups"], key=lambda item: item["rect"]["left"])] == [["2"], ["1"]]
    )
    after = dockview_layout_metrics(browser)
    assert after["slots"]["left"]["tabs"] == ["2"], after
    assert after["slots"]["right"]["tabs"] == ["1"], after


def test_dockview_pane_swap_rejects_too_small_target(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,3&layout=row@82(left,right)&tabs=left:prefs,1;right:3",
        sessions=["1", "3"],
        grid_width=1000,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3, min_width=60)
    result = browser.execute_script(
        """
        const sourceGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__prefs__"]').closest('.dv-groupview');
        const targetGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="3"]').closest('.dv-groupview');
        const sourceSlot = dockviewSlotForGroupElement(sourceGroup);
        const targetSlot = dockviewSlotForGroupElement(targetGroup);
        const targetRect = targetGroup.getBoundingClientRect();
        return {
          sourceSlot,
          targetSlot,
          targetWidth: Math.round(targetRect.width),
          canSwap: paneSwapAllowed(sourceSlot, targetSlot),
        };
        """
    )
    assert result["sourceSlot"] == "left", result
    assert result["targetSlot"] == "right", result
    assert result["targetWidth"] < 420, result
    assert result["canSwap"] is False, result


def test_dockview_tab_drag_preview_is_between_tabs(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=left&tabs=left:1,2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.68, 0.5)
    metrics = {}
    try:
        cdp_drag_hold(browser, start, end, steps=32)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                "return document.querySelector('.dv-tab.dv-drop-target .dv-drop-target-selection')?.getBoundingClientRect().width >= 22"
            )
        )
        metrics = browser.execute_script(
            """
            const rectFor = node => {
              const rect = node.getBoundingClientRect();
              return {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height};
            };
            const tab2 = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]');
            const tab3 = document.querySelector('.dockview-pane-tab[data-pane-tab="3"]');
            const selection = document.querySelector('.dv-tab.dv-drop-target .dv-drop-target-selection');
            const selectionStyle = getComputedStyle(selection);
            const tab2Rect = rectFor(tab2);
            const tab3Rect = rectFor(tab3);
            const selectionRect = rectFor(selection);
            return {
              tab2: tab2Rect,
              tab3: tab3Rect,
              selection: selectionRect,
              selectionClass: selection.className,
              backgroundColor: selectionStyle.backgroundColor,
              borderLeftWidth: selectionStyle.borderLeftWidth,
            };
            """
        )
    finally:
        cdp_release(browser, end)
    selection_center = (metrics["selection"]["left"] + metrics["selection"]["right"]) / 2
    assert 22 <= metrics["selection"]["width"] <= 26, metrics
    assert abs(selection_center - metrics["tab2"]["right"]) <= 3, metrics
    assert metrics["selection"]["left"] <= metrics["tab2"]["right"] - 10, metrics
    assert metrics["selection"]["right"] >= metrics["tab2"]["right"] + 10, metrics
    assert metrics["backgroundColor"] not in ("rgba(0, 0, 0, 0)", "transparent"), metrics
    assert metrics["borderLeftWidth"] == "2px", metrics


def test_dockview_adjacent_same_tab_drag_hides_noop_preview(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=left&tabs=left:1,2,3", sessions=["1", "2", "3"])
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)

    def drag_middle_to_adjacent_gap(target_selector, x_ratio):
        start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.5, 0.5)
        end = dockview_point(browser, target_selector, x_ratio, 0.5)
        try:
            cdp_drag_hold(browser, start, end, steps=32)
            browser.execute_async_script(
                """
                const done = arguments[arguments.length - 1];
                requestAnimationFrame(() => requestAnimationFrame(done));
                """
            )
            return browser.execute_script(
                """
                const visible = node => {
                  const rect = node.getBoundingClientRect();
                  const style = getComputedStyle(node);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && style.opacity !== '0';
                };
                const overlays = Array.from(document.querySelectorAll('.dv-drop-target, .dv-drop-target-selection, .dv-drop-target-anchor'))
                  .filter(visible)
                  .map(node => {
                    const rect = node.getBoundingClientRect();
                    return {className: node.className, width: Math.round(rect.width), height: Math.round(rect.height)};
                  });
                const grid = document.querySelector('.grid');
                return {
                  overlays,
                  rootPreview: grid?.classList.contains('drop-preview-root') || false,
                  url: location.search,
                };
                """
            )
        finally:
            cdp_release(browser, end)

    left_gap = drag_middle_to_adjacent_gap('.dockview-pane-tab[data-pane-tab="1"]', 0.86)
    right_gap = drag_middle_to_adjacent_gap('.dockview-pane-tab[data-pane-tab="3"]', 0.14)
    assert left_gap["overlays"] == [], left_gap
    assert left_gap["rootPreview"] is False, left_gap
    assert right_gap["overlays"] == [], right_gap
    assert right_gap["rootPreview"] is False, right_gap


def test_dockview_drag_moves_tab_to_other_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=row@50(left,right)&tabs=left:1;right:2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.68, 0.5)
    cdp_drag(browser, start, end)
    browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(done));
        """
    )
    browser.execute_script(
        """
        const next = cloneLayoutSlots(layoutSlots);
        next.left = emptyPaneState();
        next.right = paneStateWithTabs(['2', '1'], '1');
        dockviewLayoutState.syncQueued = false;
        layoutSlots = normalizeLayoutSlots(next);
        activeSessions = sessionsFromLayout();
        updateActiveSessionParam();
        dockviewLoadLayout(layoutSlots);
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: any(group["tabs"] == ["2", "1"] for group in dockview_layout_metrics(driver)["groups"])
    )
    metrics = dockview_layout_metrics(browser)
    assert any(group["tabs"] == ["2", "1"] for group in metrics["groups"])
    assert any(state.get("tabs") == ["2", "1"] for key, state in metrics["slots"].items() if key != "__tree")


def test_dockview_drag_splits_tab_to_right_pane_and_measures_geometry(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:1,2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    for edge_offset in (2, 8, 14):
        group = dockview_layout_metrics(browser)["groups"][0]["rect"]
        start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.5, 0.5)
        end = {"x": group["right"] - edge_offset, "y": round((group["top"] + group["bottom"]) / 2)}
        cdp_drag(browser, start, end)
        if len([group for group in dockview_layout_metrics(browser)["groups"] if group["tabs"]]) == 2:
            break
    WebDriverWait(browser, 5).until(
        lambda driver: len([group for group in dockview_layout_metrics(driver)["groups"] if group["tabs"]]) == 2
    )
    metrics = dockview_layout_metrics(browser)
    groups = sorted([group for group in metrics["groups"] if group["tabs"]], key=lambda item: item["rect"]["left"])
    assert [group["tabs"] for group in groups] == [["1"], ["2"]]
    assert groups[1]["rect"]["left"] >= groups[0]["rect"]["right"] - 2
    assert groups[0]["rect"]["width"] >= 250
    assert groups[1]["rect"]["width"] >= 250
    assert metrics["slots"]["__tree"]["split"] == "row"


def test_dockview_same_axis_second_split_preserves_target_half(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2,3&layout=row@50(left,right)&tabs=left:1,3;right:2", sessions=["1", "2", "3"], grid_width=1600)
    browser.set_window_size(1700, 800)
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    end = browser.execute_script(
        """
        const target = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const rect = target.getBoundingClientRect();
        return {
          x: Math.round(rect.right - 8),
          y: Math.round(rect.top + rect.height * 0.5),
        };
        """
    )
    cdp_drag(browser, dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.5, 0.5), end, steps=32)
    WebDriverWait(browser, 5).until(
        lambda driver: (
            len([group for group in dockview_layout_metrics(driver)["groups"] if group["tabs"]]) == 3
            and dockview_layout_metrics(driver)["slots"]["__tree"].get("split") == "row"
            and dockview_layout_metrics(driver)["slots"]["__tree"]["children"][1].get("split") == "row"
        )
    )
    metrics = dockview_layout_metrics(browser)
    groups = sorted([group for group in metrics["groups"] if group["tabs"]], key=lambda group: group["rect"]["left"])
    widths = [group["rect"]["width"] for group in groups]
    root = metrics["slots"]["__tree"]
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert [group["tabs"] for group in groups] == [["1"], ["2"], ["3"]], metrics
    assert root["split"] == "row", metrics
    assert root["children"][1]["split"] == "row", metrics
    assert 45 <= root["pct"] <= 55, metrics
    assert 45 <= root["children"][1]["pct"] <= 55, metrics
    assert widths[0] >= widths[1] * 1.75, metrics
    assert widths[0] >= widths[2] * 1.75, metrics
    assert abs(widths[1] - widths[2]) <= 40, metrics


def test_dockview_drag_to_root_left_of_stacked_panes_creates_full_height_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3&layout=col@50(top,bottom)&tabs=top:1,3;bottom:2",
        sessions=["1", "2", "3"],
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.5, 0.5)
    end = dockview_point(browser, "#dockviewRoot", 0.03, 0.5)
    cdp_drag(browser, start, end, steps=32)
    WebDriverWait(browser, 5).until(
        lambda driver: (
            dockview_layout_metrics(driver)["slots"]["__tree"].get("split") == "row"
            and len([group for group in dockview_layout_metrics(driver)["groups"] if group["tabs"]]) == 3
            and any(group["tabs"] == ["3"] for group in dockview_layout_metrics(driver)["groups"])
        )
    )
    metrics = dockview_layout_metrics(browser)
    groups = [group for group in metrics["groups"] if group["tabs"]]
    left = min(groups, key=lambda group: group["rect"]["left"])
    right = sorted([group for group in groups if group is not left], key=lambda group: group["rect"]["top"])
    right_top = min(group["rect"]["top"] for group in right)
    right_bottom = max(group["rect"]["bottom"] for group in right)
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert left["tabs"] == ["3"]
    assert [group["tabs"] for group in right] == [["1"], ["2"]]
    assert metrics["slots"]["__tree"]["split"] == "row"
    assert left["rect"]["right"] <= min(group["rect"]["left"] for group in right) + 2
    assert left["rect"]["top"] <= right_top + 2
    assert left["rect"]["bottom"] >= right_bottom - 2


def test_dockview_drag_to_root_right_of_stacked_panes_creates_full_height_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3&layout=col@50(top,bottom)&tabs=top:1,3;bottom:2",
        sessions=["1", "2", "3"],
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.5, 0.5)
    end = dockview_point(browser, "#dockviewRoot", 0.97, 0.5)
    cdp_drag(browser, start, end, steps=32)
    WebDriverWait(browser, 5).until(
        lambda driver: (
            dockview_layout_metrics(driver)["slots"]["__tree"].get("split") == "row"
            and len([group for group in dockview_layout_metrics(driver)["groups"] if group["tabs"]]) == 3
            and any(group["tabs"] == ["3"] for group in dockview_layout_metrics(driver)["groups"])
        )
    )
    metrics = dockview_layout_metrics(browser)
    groups = [group for group in metrics["groups"] if group["tabs"]]
    right = max(groups, key=lambda group: group["rect"]["left"])
    left = sorted([group for group in groups if group is not right], key=lambda group: group["rect"]["top"])
    left_top = min(group["rect"]["top"] for group in left)
    left_bottom = max(group["rect"]["bottom"] for group in left)
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert right["tabs"] == ["3"]
    assert [group["tabs"] for group in left] == [["1"], ["2"]]
    assert metrics["slots"]["__tree"]["split"] == "row"
    assert right["rect"]["left"] >= max(group["rect"]["right"] for group in left) - 2
    assert right["rect"]["top"] <= left_top + 2
    assert right["rect"]["bottom"] >= left_bottom - 2


def test_dockview_drag_to_pane_edge_splits_only_that_pane(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3&layout=col@50(top,bottom)&tabs=top:1,3;bottom:2",
        sessions=["1", "2", "3"],
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.5, 0.5)
    end = browser.execute_script(
        """
        const topGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const rect = topGroup.getBoundingClientRect();
        return {
          x: Math.round(rect.left + 8),
          y: Math.round(rect.top + rect.height * 0.55),
        };
        """
    )
    cdp_drag(browser, start, end, steps=32)
    WebDriverWait(browser, 5).until(
        lambda driver: (
            dockview_layout_metrics(driver)["slots"]["__tree"].get("split") == "column"
            and dockview_layout_metrics(driver)["slots"]["__tree"]["children"][0].get("split") == "row"
            and len([group for group in dockview_layout_metrics(driver)["groups"] if group["tabs"]]) == 3
        )
    )
    metrics = dockview_layout_metrics(browser)
    root = metrics["slots"]["__tree"]
    groups = [group for group in metrics["groups"] if group["tabs"]]
    group_3 = next(group for group in groups if group["tabs"] == ["3"])
    group_1 = next(group for group in groups if group["tabs"] == ["1"])
    group_2 = next(group for group in groups if group["tabs"] == ["2"])
    assert metrics["errors"] == []
    assert metrics["rejections"] == []
    assert root["split"] == "column", metrics
    assert root["children"][0]["split"] == "row", metrics
    assert root["children"][1].get("slot"), metrics
    assert group_3["rect"]["right"] <= group_1["rect"]["left"] + 2, metrics
    assert group_3["rect"]["top"] <= group_1["rect"]["top"] + 2, metrics
    assert group_3["rect"]["bottom"] <= group_2["rect"]["top"] + 2, metrics
    assert group_2["rect"]["left"] <= group_3["rect"]["left"] + 2, metrics
    assert group_2["rect"]["right"] >= group_1["rect"]["right"] - 2, metrics


def test_dockview_root_left_drag_shows_full_span_preview_before_drop(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3&layout=col@50(top,bottom)&tabs=top:1,3;bottom:2",
        sessions=["1", "2", "3"],
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.5, 0.5)
    end = dockview_point(browser, "#dockviewRoot", 0.03, 0.5)
    preview = {}
    try:
        cdp_drag_hold(browser, start, end, steps=32)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                "return document.querySelector('.grid')?.classList.contains('drop-preview-root') === true"
            )
        )
        preview = browser.execute_script(
            """
            const grid = document.querySelector('.grid');
            const style = getComputedStyle(grid, '::before');
            return {
              root: grid.classList.contains('drop-preview-root'),
              left: grid.classList.contains('drop-preview-left'),
              label: grid.dataset.dropLabel || '',
              borderColor: style.borderLeftColor,
              separatorHover: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
            };
            """
        )
    finally:
        cdp_release(browser, end)
    assert preview["root"] is True
    assert preview["left"] is True
    assert preview["label"] == "full left"
    assert preview["borderColor"] == preview["separatorHover"]


def test_dockview_root_right_drag_shows_full_span_preview_before_drop(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2,3&layout=col@50(top,bottom)&tabs=top:1,3;bottom:2",
        sessions=["1", "2", "3"],
    )
    wait_for_dockview(browser, min_tabs=3)
    wait_for_dockview_tab_geometry(browser, min_tabs=3)
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="3"]', 0.5, 0.5)
    end = dockview_point(browser, "#dockviewRoot", 0.97, 0.5)
    preview = {}
    try:
        cdp_drag_hold(browser, start, end, steps=32)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                const grid = document.querySelector('.grid');
                return grid?.classList.contains('drop-preview-root') === true
                  && grid.classList.contains('drop-preview-right') === true;
                """
            )
        )
        preview = browser.execute_script(
            """
            const grid = document.querySelector('.grid');
            const style = getComputedStyle(grid, '::before');
            return {
              root: grid.classList.contains('drop-preview-root'),
              right: grid.classList.contains('drop-preview-right'),
              label: grid.dataset.dropLabel || '',
              borderColor: style.borderLeftColor,
              separatorHover: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
            };
            """
        )
    finally:
        cdp_release(browser, end)
    assert preview["root"] is True
    assert preview["right"] is True
    assert preview["label"] == "full right"
    assert preview["borderColor"] == preview["separatorHover"]


def test_dockview_too_small_pane_edge_rejects_tab_preview(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=left&tabs=left:1,2",
        sessions=["1", "2"],
        grid_width=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2, min_width=80)
    result = browser.execute_script(
        """
        const group = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const slot = dockviewSlotForGroupElement(group);
        const rect = group.getBoundingClientRect();
        const event = {
          kind: 'content',
          position: 'right',
          group: {id: slot},
          nativeEvent: {clientX: Math.round(rect.right - 3), clientY: Math.round(rect.top + rect.height / 2)},
          getData() { return {panelId: '2', groupId: slot}; },
          preventDefault() { this.prevented = true; },
        };
        const intent = dockviewPaneContentDropIntent(event);
        dockviewTrackRootBoundaryOverlay(event);
        return {
          width: Math.round(rect.width),
          intent: intent ? {zone: intent.zone} : null,
          prevented: event.prevented === true,
          rootPreview: document.querySelector('#grid').classList.contains('drop-preview-root'),
        };
        """
    )
    assert result["width"] < 640, result
    assert result["intent"] is None, result
    assert result["prevented"] is True, result
    assert result["rootPreview"] is False, result


def test_dockview_finder_drop_previews_are_bottom_only_and_size_gated(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@40(left,slot1)&tabs=left:files;slot1:1",
        sessions=["1"],
        grid_width=1000,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    result = browser.execute_script(
        """
        const gridNode = document.querySelector('#grid');
        const finderGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]').closest('.dv-groupview');
        const contentGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const finderSlot = dockviewSlotForGroupElement(finderGroup);
        const contentSlot = dockviewSlotForGroupElement(contentGroup);
        const finderRect = finderGroup.getBoundingClientRect();
        const pointFor = position => ({
          clientX: Math.round(finderRect.left + finderRect.width / 2),
          clientY: position === 'bottom'
            ? Math.round(finderRect.bottom - 3)
            : position === 'top'
              ? Math.round(finderRect.top + 3)
              : Math.round(finderRect.top + finderRect.height / 2),
        });
        const tabProbe = position => {
          const nativeEvent = pointFor(position);
          const event = {
            kind: 'content',
            position,
            group: {id: finderSlot},
            nativeEvent,
            getData() { return {panelId: '1', groupId: contentSlot}; },
            preventDefault() { this.prevented = true; },
          };
          const intent = dockviewPaneContentDropIntent(event);
          dockviewTrackRootBoundaryOverlay(event);
          const result = {
            intent: intent ? {zone: intent.zone, targetSlot: intent.targetSlot} : null,
            prevented: event.prevented === true,
            rootPreview: gridNode.classList.contains('drop-preview-root'),
          };
          clearDropPreview();
          return result;
        };
        return {
          center: tabProbe('center'),
          left: tabProbe('left'),
          right: tabProbe('right'),
          top: tabProbe('top'),
          bottom: tabProbe('bottom'),
          finderRect: {width: Math.round(finderRect.width), height: Math.round(finderRect.height)},
        };
        """
    )
    assert result["finderRect"]["width"] >= 320, result
    assert result["finderRect"]["height"] >= 440, result
    for key in ["center", "left", "right", "top"]:
        assert result[key]["intent"] is None, result
        assert result[key]["prevented"] is True, result
        assert result[key]["rootPreview"] is False, result
    assert result["bottom"]["intent"] == {"zone": "bottom", "targetSlot": "left"}, result
    assert result["bottom"]["prevented"] is False, result

    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@40(left,slot1)&tabs=left:files;slot1:1",
        sessions=["1"],
        grid_width=1000,
        grid_height=420,
    )
    wait_for_dockview(browser, min_tabs=2)
    too_small = browser.execute_script(
        """
        const finderGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]').closest('.dv-groupview');
        const contentGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const finderSlot = dockviewSlotForGroupElement(finderGroup);
        const contentSlot = dockviewSlotForGroupElement(contentGroup);
        const rect = finderGroup.getBoundingClientRect();
        const event = {
          kind: 'content',
          position: 'bottom',
          group: {id: finderSlot},
          nativeEvent: {clientX: Math.round(rect.left + rect.width / 2), clientY: Math.round(rect.bottom - 3)},
          getData() { return {panelId: '1', groupId: contentSlot}; },
          preventDefault() { this.prevented = true; },
        };
        const intent = dockviewPaneContentDropIntent(event);
        dockviewTrackRootBoundaryOverlay(event);
        return {
          height: Math.round(rect.height),
          intent: intent ? {zone: intent.zone} : null,
          prevented: event.prevented === true,
          rootPreview: document.querySelector('#grid').classList.contains('drop-preview-root'),
        };
        """
    )
    assert too_small["height"] < 440, too_small
    assert too_small["intent"] is None, too_small
    assert too_small["prevented"] is True, too_small
    assert too_small["rootPreview"] is False, too_small


def test_dockview_finder_survives_hidden_host_adoption_and_reshow(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@28(left,slot1)&tabs=left:files;slot1:1",
        sessions=["1"],
        grid_width=1000,
        grid_height=620,
        file_explorer_open_intent="1",
    )
    wait_for_dockview(browser, min_tabs=2)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0;
            """
        )
    )
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 120; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const finderGroup = () => document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]')?.closest('.dv-groupview') || null;
          const widthOf = group => Math.round(group?.getBoundingClientRect?.().width || 0);
          const host = dockviewLayoutState.host;
          const api = dockviewLayoutState.api;
          if (!host || !api) return {error: 'dockview host or api missing'};
          const beforeWidth = widthOf(finderGroup());
          const beforeSlot = slotForSession(fileExplorerItemId);
          const originalToJSON = api.toJSON.bind(api);
          const finderless = emptyLayoutSlots();
          finderless[layoutTreeKey] = leafNode('left');
          finderless.left = paneStateWithTabs(['1'], '1');
          const finderlessJson = dockviewJsonFromLayoutSlots(finderless);
          api.toJSON = () => finderlessJson;
          host.style.display = 'none';
          adoptDockviewLayout();
          const hiddenHasFinder = itemInLayout(fileExplorerItemId);
          const hiddenSlot = slotForSession(fileExplorerItemId);
          host.style.display = '';
          dockviewLayoutToHost();
          adoptDockviewLayout();
          const restored = await waitFor(() => itemInLayout(fileExplorerItemId) && widthOf(finderGroup()) > 0);
          const afterWidth = widthOf(finderGroup());
          const afterSlot = slotForSession(fileExplorerItemId);
          api.toJSON = originalToJSON;
          return {
            beforeWidth,
            beforeSlot,
            hiddenHasFinder,
            hiddenSlot,
            restored,
            afterWidth,
            afterSlot,
            tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            url: location.search,
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in result, result
    assert result["beforeWidth"] > 0, result
    assert result["beforeSlot"], result
    assert result["hiddenHasFinder"] is True, result
    assert result["hiddenSlot"] == result["beforeSlot"], result
    assert result["restored"] is True, result
    assert result["afterWidth"] > 0, result
    assert result["afterSlot"], result
    assert "__files__" in result["tabs"], result
    assert result["errors"] == []
    assert result["rejections"] == []


def test_dockview_root_bottom_preview_preserves_docked_finder_column(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1,2&layout=row@24(left,col@50(slot1,slot2))&tabs=left:files;slot1:1;slot2:2",
        sessions=["1", "2"],
    )
    wait_for_dockview(browser, min_tabs=3)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0
              && document.querySelector('.dockview-pane-tab[data-pane-tab="2"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0;
            """
        )
    )
    metrics = browser.execute_script(
        """
        const host = document.querySelector('#dockviewRoot');
        const gridNode = document.querySelector('#grid');
        const finderGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]').closest('.dv-groupview');
        const contentGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const hostRect = host.getBoundingClientRect();
        const gridRect = gridNode.getBoundingClientRect();
        const finderRect = finderGroup.getBoundingClientRect();
        const contentRect = contentGroup.getBoundingClientRect();
        const eventAt = (rect, xRatio, y) => ({
          kind: 'content',
          position: 'bottom',
          getData() { return {panelId: '1', groupId: 'slot1'}; },
          nativeEvent: {
            clientX: Math.round(rect.left + rect.width * xRatio),
            clientY: Math.round(y),
          },
        });
        const contentIntent = dockviewRootBoundaryDropIntent(eventAt(contentRect, 0.5, hostRect.bottom - 2));
        dockviewShowRootBoundaryPreview(contentIntent);
        const previewStyle = getComputedStyle(gridNode, '::before');
        const preview = {
          root: gridNode.classList.contains('drop-preview-root'),
          bottom: gridNode.classList.contains('drop-preview-bottom'),
          label: gridNode.dataset.dropLabel || '',
          left: parseFloat(previewStyle.left) || 0,
          width: parseFloat(previewStyle.width) || 0,
          top: parseFloat(previewStyle.top) || 0,
          height: parseFloat(previewStyle.height) || 0,
          borderColor: previewStyle.borderLeftColor,
          separatorHover: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
        };
        clearDropPreview();
        const finderIntent = dockviewRootBoundaryDropIntent(eventAt(finderRect, 0.5, hostRect.bottom - 2));
        return {
      contentIntent: contentIntent ? {zone: contentIntent.zone} : null,
      finderIntent: finderIntent ? {zone: finderIntent.zone} : null,
          preview,
          gridRect: {left: gridRect.left, width: gridRect.width},
          finderRect: {left: finderRect.left, right: finderRect.right, width: finderRect.width},
          contentRect: {left: contentRect.left, right: contentRect.right, width: contentRect.width},
        };
        """
    )
    assert metrics["contentIntent"] == {"zone": "bottom"}, metrics
    assert metrics["finderIntent"] is None, metrics
    assert metrics["preview"]["root"] is True, metrics
    assert metrics["preview"]["bottom"] is True, metrics
    assert metrics["preview"]["label"] == "full bottom", metrics
    assert metrics["preview"]["borderColor"] == metrics["preview"]["separatorHover"], metrics
    expected_left = metrics["contentRect"]["left"] - metrics["gridRect"]["left"] + 6
    expected_width = metrics["contentRect"]["width"] - 12
    assert abs(metrics["preview"]["left"] - expected_left) <= 2, metrics
    assert abs(metrics["preview"]["width"] - expected_width) <= 2, metrics
    assert metrics["preview"]["left"] >= metrics["finderRect"]["right"] - metrics["gridRect"]["left"] + 4, metrics


def test_dockview_root_top_drag_preview_preserves_docked_finder_column(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1,2&layout=row@24(left,col@50(slot1,slot2))&tabs=left:files;slot1:1;slot2:2",
        sessions=["1", "2"],
    )
    wait_for_dockview(browser, min_tabs=3)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0
              && document.querySelector('.dockview-pane-tab[data-pane-tab="1"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0
              && document.querySelector('.dockview-pane-tab[data-pane-tab="2"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0;
            """
        )
    )
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.5, 0.5)
    end = browser.execute_script(
        """
        const host = document.querySelector('#dockviewRoot');
        const contentGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const hostRect = host.getBoundingClientRect();
        const contentRect = contentGroup.getBoundingClientRect();
        return {
          x: Math.round(contentRect.left + contentRect.width * 0.5),
          y: Math.round(hostRect.top + 3),
        };
        """
    )
    preview = {}
    try:
        cdp_drag_hold(browser, start, end, steps=32)
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                const grid = document.querySelector('.grid');
                return grid?.classList.contains('drop-preview-root') === true
                  && grid.classList.contains('drop-preview-top') === true;
                """
            )
        )
        preview = browser.execute_script(
            """
            const grid = document.querySelector('#grid');
            const finderGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]').closest('.dv-groupview');
            const contentGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
            const gridRect = grid.getBoundingClientRect();
            const finderRect = finderGroup.getBoundingClientRect();
            const contentRect = contentGroup.getBoundingClientRect();
            const style = getComputedStyle(grid, '::before');
            const nativeOverlays = Array.from(document.querySelectorAll('.dv-drop-target, .dv-drop-target-selection, .dv-drop-target-anchor'))
              .map(node => {
                const rect = node.getBoundingClientRect();
                const cs = getComputedStyle(node);
                return {
                  className: node.className,
                  display: cs.display,
                  visibility: cs.visibility,
                  left: rect.left,
                  right: rect.right,
                  top: rect.top,
                  bottom: rect.bottom,
                  width: rect.width,
                  height: rect.height,
                };
              })
              .filter(rect => rect.display !== 'none' && rect.visibility !== 'hidden' && rect.width > 0 && rect.height > 0);
            const coversFinder = nativeOverlays.some(rect => (
              rect.left < finderRect.right - 2
                && rect.right > finderRect.left + 2
                && rect.top < contentRect.top + contentRect.height * 0.5
                && rect.bottom > contentRect.top
            ));
            return {
              root: grid.classList.contains('drop-preview-root'),
              top: grid.classList.contains('drop-preview-top'),
              label: grid.dataset.dropLabel || '',
              left: parseFloat(style.left) || 0,
              width: parseFloat(style.width) || 0,
              borderColor: style.borderLeftColor,
              separatorHover: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
              nativeOverlays,
              coversFinder,
              gridRect: {left: gridRect.left, width: gridRect.width},
              finderRect: {left: finderRect.left, right: finderRect.right, width: finderRect.width},
              contentRect: {left: contentRect.left, right: contentRect.right, width: contentRect.width},
            };
            """
        )
    finally:
        cdp_release(browser, end)
    assert preview["root"] is True, preview
    assert preview["top"] is True, preview
    assert preview["label"] == "full top", preview
    assert preview["borderColor"] == preview["separatorHover"], preview
    expected_left = preview["contentRect"]["left"] - preview["gridRect"]["left"] + 6
    expected_width = preview["contentRect"]["width"] - 12
    assert abs(preview["left"] - expected_left) <= 2, preview
    assert abs(preview["width"] - expected_width) <= 2, preview
    assert preview["left"] >= preview["finderRect"]["right"] - preview["gridRect"]["left"] + 4, preview
    assert preview["coversFinder"] is False, preview


def test_dockview_root_top_bottom_preview_normalizes_right_finder_and_avoids_reserved_column(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1,2&layout=row@76(col@50(slot1,slot2),right)&tabs=slot1:1;slot2:2;right:files",
        sessions=["1", "2"],
    )
    wait_for_dockview(browser, min_tabs=3)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0
              && document.querySelector('.dockview-pane-tab[data-pane-tab="1"]')?.closest('.dv-groupview')?.getBoundingClientRect().width > 0;
            """
        )
    )
    metrics = browser.execute_script(
        """
        const host = document.querySelector('#dockviewRoot');
        const gridNode = document.querySelector('#grid');
        const finderGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]').closest('.dv-groupview');
        const contentGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const hostRect = host.getBoundingClientRect();
        const gridRect = gridNode.getBoundingClientRect();
        const finderRect = finderGroup.getBoundingClientRect();
        const contentRect = contentGroup.getBoundingClientRect();
        const eventAt = (rect, zone, x) => ({
          kind: 'content',
          position: zone,
          getData() { return {panelId: '2', groupId: dockviewSlotForGroupElement(contentGroup)}; },
          nativeEvent: {
            clientX: Math.round(x),
            clientY: zone === 'top' ? Math.round(hostRect.top + 2) : Math.round(hostRect.bottom - 2),
          },
        });
        const finderOnLeft = finderRect.left < contentRect.left;
        const previewFor = zone => {
          const nearFinderX = finderOnLeft ? finderRect.right + 3 : finderRect.left - 3;
          const contentIntent = dockviewRootBoundaryDropIntent(eventAt(contentRect, zone, nearFinderX));
          dockviewShowRootBoundaryPreview(contentIntent);
          const style = getComputedStyle(gridNode, '::before');
          const preview = {
            root: gridNode.classList.contains('drop-preview-root'),
            zone: gridNode.classList.contains(`drop-preview-${zone}`),
            label: gridNode.dataset.dropLabel || '',
            left: parseFloat(style.left) || 0,
            width: parseFloat(style.width) || 0,
            borderColor: style.borderLeftColor,
            separatorHover: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
          };
          clearDropPreview();
          const finderIntent = dockviewRootBoundaryDropIntent(eventAt(finderRect, zone, finderRect.left + finderRect.width / 2));
          return {
            contentIntent: contentIntent ? {zone: contentIntent.zone} : null,
            finderIntent: finderIntent ? {zone: finderIntent.zone} : null,
            preview,
          };
        };
        return {
          top: previewFor('top'),
          bottom: previewFor('bottom'),
          finderOnLeft,
          gridRect: {left: gridRect.left, width: gridRect.width},
          finderRect: {left: finderRect.left, right: finderRect.right, width: finderRect.width},
          contentRect: {left: contentRect.left, right: contentRect.right, width: contentRect.width},
        };
        """
    )
    for zone in ["top", "bottom"]:
        item = metrics[zone]
        assert metrics["finderOnLeft"] is True, metrics
        assert item["contentIntent"] == {"zone": zone}, metrics
        assert item["finderIntent"] is None, metrics
        assert item["preview"]["root"] is True, metrics
        assert item["preview"]["zone"] is True, metrics
        assert item["preview"]["label"] == f"full {zone}", metrics
        assert item["preview"]["borderColor"] == item["preview"]["separatorHover"], metrics
        expected_left = metrics["contentRect"]["left"] - metrics["gridRect"]["left"] + 6
        expected_width = metrics["contentRect"]["width"] - 12
        assert abs(item["preview"]["left"] - expected_left) <= 2, metrics
        assert abs(item["preview"]["width"] - expected_width) <= 2, metrics
        assert item["preview"]["left"] >= metrics["finderRect"]["right"] - metrics["gridRect"]["left"] + 4, metrics


def test_dockview_drag_between_content_panes_preserves_docked_finder_width(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=row@24(left,row@50(slot1,slot2))&tabs=left:files;slot1:1;slot2:2",
        sessions=["1", "2"],
    )
    wait_for_dockview(browser, min_tabs=2)
    wait_for_visible_selector(browser, '.dockview-pane-tab[data-pane-tab="1"]')
    wait_for_visible_selector(browser, '.dockview-pane-tab[data-pane-tab="2"]')
    before = dockview_layout_metrics(browser)
    finder_before = next(group for group in before["groups"] if "__files__" in group["tabs"])
    start = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="1"]', 0.5, 0.5)
    end = dockview_point(browser, '.dockview-pane-tab[data-pane-tab="2"]', 0.68, 0.5)
    cdp_drag(browser, start, end, steps=28)
    WebDriverWait(browser, 5).until(
        lambda driver: any("1" in group["tabs"] and "2" in group["tabs"] for group in dockview_layout_metrics(driver)["groups"])
    )
    after = dockview_layout_metrics(browser)
    finder_after = next(group for group in after["groups"] if "__files__" in group["tabs"])
    assert abs(finder_after["rect"]["width"] - finder_before["rect"]["width"]) <= 3
    assert round(after["slots"]["__tree"]["pct"]) == round(before["slots"]["__tree"]["pct"])


def test_dockview_docked_finder_sash_resize_updates_root_pct(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=1,2&layout=row@24(left,row@50(slot1,slot2))&tabs=left:files;slot1:1;slot2:2",
        sessions=["1", "2"],
    )
    wait_for_dockview(browser, min_tabs=2)
    WebDriverWait(browser, 5).until(
        lambda driver: len(dockview_layout_metrics(driver)["groups"]) >= 3
    )
    before = dockview_layout_metrics(browser)
    finder_before = next(group for group in before["groups"] if "__files__" in group["tabs"])
    content_before = sorted([group for group in before["groups"] if group["tabs"] in (["1"], ["2"])], key=lambda group: group["rect"]["left"])
    start = browser.execute_script(
        """
        const finderRight = arguments[0];
        const sashes = Array.from(document.querySelectorAll('.dv-sash'))
          .map(sash => {
            const rect = sash.getBoundingClientRect();
            return {left: rect.left, top: rect.top, width: rect.width, height: rect.height};
          })
          .filter(rect => rect.width > 0 && rect.height > rect.width);
        const sash = sashes.reduce((best, item) => (
          !best || Math.abs((item.left + item.width / 2) - finderRight) < Math.abs((best.left + best.width / 2) - finderRight)
            ? item
            : best
        ), null);
        return sash ? {
          x: Math.round(sash.left + sash.width / 2),
          y: Math.round(sash.top + sash.height / 2),
          left: sash.left,
          top: sash.top,
          width: sash.width,
          height: sash.height,
        } : null;
        """,
        finder_before["rect"]["right"],
    )
    end = {"x": start["x"] + 90, "y": start["y"]}
    cdp_drag(browser, start, end, steps=24)
    WebDriverWait(browser, 5).until(
        lambda driver: abs(
            next(group for group in dockview_layout_metrics(driver)["groups"] if "__files__" in group["tabs"])["rect"]["width"]
            - finder_before["rect"]["width"]
        ) > 35
    )
    after = dockview_layout_metrics(browser)
    finder_after = next(group for group in after["groups"] if "__files__" in group["tabs"])
    content_after = sorted([group for group in after["groups"] if group["tabs"] in (["1"], ["2"])], key=lambda group: group["rect"]["left"])
    assert finder_after["rect"]["width"] > finder_before["rect"]["width"] + 35
    assert after["slots"]["__tree"]["pct"] > before["slots"]["__tree"]["pct"] + 3
    assert abs(content_before[0]["rect"]["width"] - content_before[1]["rect"]["width"]) <= 4
    assert abs(content_after[0]["rect"]["width"] - content_after[1]["rect"]["width"]) <= 8, after
    assert content_after[0]["rect"]["width"] < content_before[0]["rect"]["width"] - 15
    assert content_after[1]["rect"]["width"] < content_before[1]["rect"]["width"] - 15


def test_dockview_file_drag_from_finder_opens_in_target_pane_with_preview(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:1,2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    result = browser.execute_script(
        """
        window.__dockviewFileOpen = null;
        window.openDraggedFilesInEditor = (payload, options) => {
          window.__dockviewFileOpen = {payload, options};
        };
        const group = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
        const target = group.querySelector('.dockview-panel-content') || group;
        const rect = group.getBoundingClientRect();
        const store = {
          'application/x-yolomux-file': JSON.stringify({path: '/home/test/yolomux.dev/README.md', paths: ['/home/test/yolomux.dev/README.md'], kind: 'file'}),
          'text/plain': '/home/test/yolomux.dev/README.md',
        };
        const dataTransfer = {
          types: Object.keys(store),
          dropEffect: '',
          effectAllowed: 'copy',
          getData(type) { return store[type] || ''; },
          setData(type, value) { store[type] = String(value); },
        };
        function fire(type, x, y) {
          const event = new Event(type, {bubbles: true, cancelable: true});
          Object.defineProperty(event, 'clientX', {value: x});
          Object.defineProperty(event, 'clientY', {value: y});
          Object.defineProperty(event, 'dataTransfer', {value: dataTransfer});
          target.dispatchEvent(event);
          return {defaultPrevented: event.defaultPrevented, dropEffect: dataTransfer.dropEffect};
        }
        const over = fire('dragover', Math.round(rect.left + 8), Math.round(rect.top + rect.height / 2));
        const preview = {
          dragOver: group.classList.contains('drag-over'),
          dropPreview: group.classList.contains('drop-preview'),
          left: group.classList.contains('drop-preview-left'),
          label: group.dataset.dropLabel || '',
          borderColor: getComputedStyle(group, '::before').borderLeftColor,
          separatorHover: getComputedStyle(document.documentElement).getPropertyValue('--pane-resizer-hover-bg').trim(),
        };
        const drop = fire('drop', Math.round(rect.left + 8), Math.round(rect.top + rect.height / 2));
        return {over, preview, drop, opened: window.__dockviewFileOpen};
        """
    )
    assert result["over"]["defaultPrevented"] is True
    assert result["over"]["dropEffect"] == "copy"
    assert result["preview"]["dragOver"] is True
    assert result["preview"]["dropPreview"] is True
    assert result["preview"]["left"] is True
    assert result["preview"]["label"] == "left"
    assert result["preview"]["borderColor"] == result["preview"]["separatorHover"]
    assert result["drop"]["defaultPrevented"] is True
    assert result["opened"]["payload"]["path"] == "/home/test/yolomux.dev/README.md"
    assert result["opened"]["options"]["targetSlot"] == "left"
    assert result["opened"]["options"]["targetZone"] == "left"


def test_dockview_file_drag_to_finder_previews_only_roomy_bottom(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@40(left,slot1)&tabs=left:files;slot1:1",
        sessions=["1"],
        grid_width=1000,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    result = browser.execute_script(
        """
        window.__dockviewFileOpen = null;
        window.openDraggedFilesInEditor = (payload, options) => {
          window.__dockviewFileOpen = {payload, options};
        };
        const finderGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]').closest('.dv-groupview');
        const target = finderGroup.querySelector('.dockview-panel-content') || finderGroup;
        const rect = finderGroup.getBoundingClientRect();
        const store = {
          'application/x-yolomux-file': JSON.stringify({path: '/home/test/yolomux.dev/README.md', paths: ['/home/test/yolomux.dev/README.md'], kind: 'file'}),
          'text/plain': '/home/test/yolomux.dev/README.md',
        };
        const dataTransfer = {
          types: Object.keys(store),
          dropEffect: '',
          effectAllowed: 'copy',
          getData(type) { return store[type] || ''; },
          setData(type, value) { store[type] = String(value); },
        };
        function fire(type, x, y) {
          const event = new Event(type, {bubbles: true, cancelable: true});
          Object.defineProperty(event, 'clientX', {value: x});
          Object.defineProperty(event, 'clientY', {value: y});
          Object.defineProperty(event, 'dataTransfer', {value: dataTransfer});
          target.dispatchEvent(event);
          return {
            defaultPrevented: event.defaultPrevented,
            dropEffect: dataTransfer.dropEffect,
            preview: {
              dragOver: finderGroup.classList.contains('drag-over'),
              dropPreview: finderGroup.classList.contains('drop-preview'),
              bottom: finderGroup.classList.contains('drop-preview-bottom'),
              left: finderGroup.classList.contains('drop-preview-left'),
              label: finderGroup.dataset.dropLabel || '',
            },
          };
        }
        const center = fire('dragover', Math.round(rect.left + rect.width / 2), Math.round(rect.top + rect.height / 2));
        clearDropPreview();
        const left = fire('dragover', Math.round(rect.left + 8), Math.round(rect.top + rect.height / 2));
        clearDropPreview();
        const bottom = fire('dragover', Math.round(rect.left + rect.width / 2), Math.round(rect.bottom - 8));
        const drop = fire('drop', Math.round(rect.left + rect.width / 2), Math.round(rect.bottom - 8));
        return {center, left, bottom, drop, opened: window.__dockviewFileOpen};
        """
    )
    assert result["center"]["defaultPrevented"] is True, result
    assert result["center"]["dropEffect"] == "none", result
    assert result["center"]["preview"]["dropPreview"] is False, result
    assert result["left"]["dropEffect"] == "none", result
    assert result["left"]["preview"]["dropPreview"] is False, result
    assert result["bottom"]["dropEffect"] == "copy", result
    assert result["bottom"]["preview"]["dropPreview"] is True, result
    assert result["bottom"]["preview"]["bottom"] is True, result
    assert result["bottom"]["preview"]["label"] == "bottom", result
    assert result["drop"]["defaultPrevented"] is True, result
    assert result["opened"]["payload"]["path"] == "/home/test/yolomux.dev/README.md", result
    assert result["opened"]["options"]["targetSlot"] == "left", result
    assert result["opened"]["options"]["targetZone"] == "bottom", result


def test_dockview_multi_file_drag_preserves_order_dedupes_and_uses_one_target(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path, "?sessions=1,2&layout=left&tabs=left:1,2", sessions=["1", "2"])
    wait_for_dockview(browser, min_tabs=2)
    wait_for_dockview_tab_geometry(browser, min_tabs=2)
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          window.__multiFileOpened = [];
          fetchFilePathInfo = async path => ({path, name: path.split('/').pop(), kind: 'file'});
          openFileInEditor = async (path, info, options) => {
            window.__multiFileOpened.push({path, info, options});
          };
          refreshOpenFileDiff = async () => {};
          openFileDiffAvailable = () => false;
          const group = document.querySelector('.dockview-pane-tab[data-pane-tab="2"]').closest('.dv-groupview');
          const target = group.querySelector('.dockview-panel-content') || group;
          const rect = group.getBoundingClientRect();
          const paths = [
            '/home/test/yolomux.dev/a.md',
            '/home/test/yolomux.dev/b.md',
            '/home/test/yolomux.dev/a.md',
            '/home/test/yolomux.dev/c.md',
          ];
          const store = {
            'application/x-yolomux-file': JSON.stringify({path: paths[0], paths, kind: 'file'}),
            'text/plain': paths.join('\\n'),
          };
          const dataTransfer = {
            types: Object.keys(store),
            dropEffect: '',
            effectAllowed: 'copy',
            getData(type) { return store[type] || ''; },
            setData(type, value) { store[type] = String(value); },
          };
          function fire(type, x, y) {
            const event = new Event(type, {bubbles: true, cancelable: true});
            Object.defineProperty(event, 'clientX', {value: x});
            Object.defineProperty(event, 'clientY', {value: y});
            Object.defineProperty(event, 'dataTransfer', {value: dataTransfer});
            target.dispatchEvent(event);
            return {defaultPrevented: event.defaultPrevented, dropEffect: dataTransfer.dropEffect};
          }
          const x = Math.round(rect.left + rect.width / 2);
          const y = Math.round(rect.top + rect.height / 2);
          const over = fire('dragover', x, y);
          const preview = {
            previewCount: document.querySelectorAll('.drop-preview').length,
            groupPreview: group.classList.contains('drop-preview'),
            label: group.dataset.dropLabel || '',
            targetSlot: dockviewSlotForGroupElement(group),
          };
          const drop = fire('drop', x, y);
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          done({over, preview, drop, opened: window.__multiFileOpened});
        })().catch(error => done({error: String(error), stack: error?.stack || ''}));
        """
    )
    assert "error" not in result, result
    assert result["over"]["defaultPrevented"] is True, result
    assert result["over"]["dropEffect"] == "copy", result
    assert result["preview"]["previewCount"] == 1, result
    assert result["preview"]["groupPreview"] is True, result
    assert result["preview"]["label"] == "take over", result
    assert result["drop"]["defaultPrevented"] is True, result
    assert [item["path"] for item in result["opened"]] == [
        "/home/test/yolomux.dev/a.md",
        "/home/test/yolomux.dev/b.md",
        "/home/test/yolomux.dev/c.md",
    ], result
    assert {item["options"]["targetSlot"] for item in result["opened"]} == {result["preview"]["targetSlot"]}, result
    assert [item["options"]["targetIndex"] for item in result["opened"]] == [None, None, None], result
    assert {item["options"]["targetZone"] for item in result["opened"]} == {"middle"}, result


def test_dockview_directory_drag_over_finder_is_reserved_but_terminal_path_target_stays_allowed(browser, tmp_path):
    load_dockview_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=files,1&layout=row@30(left,slot1)&tabs=left:files;slot1:1",
        sessions=["1"],
        grid_width=1200,
        grid_height=620,
    )
    wait_for_dockview(browser, min_tabs=2)
    result = browser.execute_script(
        """
        const finderGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="__files__"]').closest('.dv-groupview');
        const contentGroup = document.querySelector('.dockview-pane-tab[data-pane-tab="1"]').closest('.dv-groupview');
        const target = finderGroup.querySelector('.dockview-panel-content') || finderGroup;
        const finderRect = finderGroup.getBoundingClientRect();
        const contentRect = contentGroup.getBoundingClientRect();
        const finderSlot = dockviewSlotForGroupElement(finderGroup);
        const contentSlot = dockviewSlotForGroupElement(contentGroup);
        const payload = {path: '/home/test/yolomux.dev/src', paths: ['/home/test/yolomux.dev/src'], kind: 'dir'};
        const store = {
          'application/x-yolomux-file': JSON.stringify(payload),
          'text/plain': payload.path,
        };
        const dataTransfer = {
          types: Object.keys(store),
          dropEffect: '',
          effectAllowed: 'copy',
          getData(type) { return store[type] || ''; },
          setData(type, value) { store[type] = String(value); },
        };
        function fireFinder(type, x, y) {
          const event = new Event(type, {bubbles: true, cancelable: true});
          Object.defineProperty(event, 'clientX', {value: x});
          Object.defineProperty(event, 'clientY', {value: y});
          Object.defineProperty(event, 'dataTransfer', {value: dataTransfer});
          target.dispatchEvent(event);
          return {
            defaultPrevented: event.defaultPrevented,
            dropEffect: dataTransfer.dropEffect,
            preview: finderGroup.classList.contains('drop-preview'),
            label: finderGroup.dataset.dropLabel || '',
          };
        }
        const center = fireFinder('dragover', Math.round(finderRect.left + finderRect.width / 2), Math.round(finderRect.top + finderRect.height / 2));
        clearDropPreview();
        const bottom = fireFinder('dragover', Math.round(finderRect.left + finderRect.width / 2), Math.round(finderRect.bottom - 8));
        clearDropPreview();
        return {
          center,
          bottom,
          sharedFileGateFinder: fileDropIntentAllowsPayload(payload, {targetSlot: finderSlot, zone: 'bottom', targetRect: finderRect}),
          sharedPathGateFinderMiddle: pathDropIntentAllowsPayload(payload, {targetSlot: finderSlot, zone: 'middle', targetRect: finderRect}),
          sharedPathGateTerminalEdge: pathDropIntentAllowsPayload(payload, {targetSlot: contentSlot, zone: 'left', targetRect: contentRect}),
        };
        """
    )
    assert result["center"]["defaultPrevented"] is True, result
    assert result["center"]["dropEffect"] == "none", result
    assert result["center"]["preview"] is False, result
    assert result["bottom"]["dropEffect"] == "none", result
    assert result["bottom"]["preview"] is False, result
    assert result["sharedFileGateFinder"] is False, result
    assert result["sharedPathGateFinderMiddle"] is False, result
    assert result["sharedPathGateTerminalEdge"] is True, result


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
    page = tmp_path / "live-runtime-sync-reveal-manual-collapse.html"
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
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const collapsedAfterClick = tree.querySelector('.file-tree-row[data-path="' + repoA + '"]')?.getAttribute('aria-expanded');
          const manualHasA = fileExplorerSyncManualCollapsedPaths.has(repoA);
          scheduleFileExplorerActiveFileReveal('/home/test/dynamo/repo-a/src/a.js');
          requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(() => {
            done({
              collapsedAfterClick,
              manualHasA,
              repoAExpanded: tree.querySelector('.file-tree-row[data-path="' + repoA + '"]')?.getAttribute('aria-expanded') || '',
              manualHasA_after: fileExplorerSyncManualCollapsedPaths.has(repoA),
            });
          })));
        }));
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
    page = tmp_path / "live-runtime-file-entry-status-existing-preview-sample.html"
    page.write_text(live_runtime_boot_fixture_html(fs_entries=fs_entries), encoding="utf-8")
    browser.get(page.as_uri() + "?sessions=1&layout=left&tabs=left:1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof fetchFileEntryStatus === 'function' && document.querySelector('#grid') !== null;"
        )
    )
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
    assert metrics["lookupError"] == "", metrics
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
    page = tmp_path / "live-runtime-sync-editor-file-root.html"
    page.write_text(
        live_runtime_boot_fixture_html(
            settings={"file_explorer": {"root_mode": "sync"}},
            sessions=["1", "2"],
            transcript_sessions={
                "1": {"current_path": "/home/test/yolomux.dev", "git_root": "/home/test/yolomux.dev"},
                "2": {"current_path": "/home/test/dynamo/frontend-crates", "git_root": "/home/test/dynamo/frontend-crates"},
            },
            session_files_payload=session_files_payload,
            fs_entries=fs_entries,
        ),
        encoding="utf-8",
    )
    browser.get(
        page.as_uri()
        + "?sessions=files,1,2"
        + "&layout=row@35(slot1,left)"
        + f"&tabs=slot1:files;left:file:{path}"
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
    page = tmp_path / "live-runtime-sync-typed-path-slow-listing.html"
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
    click_visible_panel(browser, "panel-5")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.file-explorer-path-inline')?.value === '/home/test/yolomux.dev'"
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
    page = tmp_path / "live-runtime-sync-stale-session-open.html"
    page.write_text(
        live_runtime_boot_fixture_html(
            settings={"file_explorer": {"root_mode": "sync"}},
            sessions=["5", "8002"],
            transcript_sessions={
                "5": {"current_path": "/home/test/yolomux.dev1/src", "git_root": "/home/test/yolomux.dev1"},
                "8002": {"current_path": "/home/test/yolomux.dev2/src", "git_root": "/home/test/yolomux.dev2"},
            },
            session_files_payload=session_files_payload,
            session_files_payloads={"8002": session_files_payload},
            fs_entries=fs_entries,
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri() + "?sessions=files,5,8002&layout=row@35(slot1,left)&tabs=slot1:files;left:5,8002")
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
    page = tmp_path / "live-runtime-sync-user-select-8002.html"
    page.write_text(
        live_runtime_boot_fixture_html(
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
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri() + "?sessions=files,5,8002&layout=row@35(slot1,row@50(left,slot2))&tabs=slot1:files;left:5;slot2:8002")
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
              gitRoot: transcriptMeta.sessions?.['8002']?.project?.git?.root || '',
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
                      gitRoot: transcriptMeta.sessions?.['8002']?.project?.git?.root || '',
                      planRoot: plan.root,
                      syncInFlight: fileExplorerSyncPathInFlight,
                      appliedKey: fileExplorerLastAppliedSyncPlanKey,
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
                syncInFlight: fileExplorerSyncPathInFlight,
                appliedKey: fileExplorerLastAppliedSyncPlanKey,
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
    page = tmp_path / "live-runtime-sync-typed-path-manual.html"
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
    page = tmp_path / "live-runtime-root-quick-access-hidden.html"
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
    wait_for_visible_panel(browser, "panel-6")
    click_visible_panel(browser, "panel-5")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return document.querySelector('.file-explorer-path-inline')?.value === '/home/test/yolomux.dev'"
        )
    )
    browser.execute_script(
        """
        const tree = document.querySelector('.file-explorer-panel .file-explorer-tree-panel');
        const row = tree.querySelector('.file-tree-row[data-path="/home/test/yolomux.dev/other"]');
        row.click();
        """
    )
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


def test_sync_mode_explicit_pane_change_wins_over_stale_sync_open(browser, tmp_path):
    fs_entries = {
        "/home/test": [{"name": "yolomux.dev1", "kind": "dir"}, {"name": "yolomux.dev2", "kind": "dir"}],
        "/home/test/yolomux.dev1": [{"name": "src", "kind": "dir"}],
        "/home/test/yolomux.dev1/src": [{"name": "main.js", "kind": "file"}],
        "/home/test/yolomux.dev2": [{"name": "src", "kind": "dir"}],
        "/home/test/yolomux.dev2/src": [{"name": "main.js", "kind": "file"}],
    }
    page = tmp_path / "live-runtime-sync-explicit-pane-race.html"
    page.write_text(
        live_runtime_boot_fixture_html(
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
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        openFileExplorerAt('/home/test', {syncSelection: true}).then(() => {
          fileExplorerDirListingCache?.delete?.('/home/test/yolomux.dev1');
          fileExplorerDirListingCache?.delete?.('/home/test/yolomux.dev2');
          fileExplorerDirListingInflight?.delete?.('/home/test/yolomux.dev1');
          fileExplorerDirListingInflight?.delete?.('/home/test/yolomux.dev2');
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
    page = tmp_path / "live-runtime-sync-session-switch-transcript-root.html"
    page.write_text(
        live_runtime_boot_fixture_html(
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
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri() + "?sessions=files,5,6&layout=row@35(slot1,row@50(left,slot2))&tabs=slot1:files;left:5;slot2:6")
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
                  payloadSession: fileExplorerSessionFilesPayload?.session || '',
                  loading: fileExplorerSessionFilesLoading,
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
                  payloadSession: fileExplorerSessionFilesPayload?.session || '',
                  loading: fileExplorerSessionFilesLoading,
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
    page = tmp_path / "live-runtime-sync-cached-session-switch.html"
    page.write_text(
        live_runtime_boot_fixture_html(
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
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri() + "?sessions=files,5,6&layout=row@35(slot1,row@50(left,slot2))&tabs=slot1:files;left:5;slot2:6")
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
                  payloadSession: fileExplorerSessionFilesPayload?.session || '',
                  loading: fileExplorerSessionFilesLoading,
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
                  payloadSession: fileExplorerSessionFilesPayload?.session || '',
                  loading: fileExplorerSessionFilesLoading,
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
          fetchedPaths: window.__bootFetches.filter(item => item.path === '/api/fs/list' || item.path === '/api/fs/batch').map(item => item.path),
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
        const applyOpacity = value => {
          applySettingsPayload({settings: {appearance: {pane_ring_opacity: value}}, defaults: {}, mtime_ns: value}, {force: true});
          const panel = document.querySelector('#panel-1');
          panel.classList.add('active-pane');
          const rootStyle = getComputedStyle(document.documentElement);
          const panelStyle = getComputedStyle(panel);
          const ringOwner = panel.closest('.dv-groupview');
          const ringStyle = ringOwner ? getComputedStyle(ringOwner, '::after') : panelStyle;
          return {
            activeOpacity: rootStyle.getPropertyValue('--pane-active-ring-opacity').trim(),
            normalOpacity: rootStyle.getPropertyValue('--pane-ring-opacity').trim(),
            borderColor: ringStyle.borderLeftColor,
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
              && document.querySelector('.dockview-pane-tab[data-pane-tab="1"].active') !== null
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
              && getComputedStyle(document.querySelector('.dockview-pane-tab[data-pane-tab="1"].active')).backgroundColor === 'rgb(59, 130, 246)';
            """
        )
    )
    metrics = browser.execute_script(
        """
        const rootStyle = getComputedStyle(document.documentElement);
        const bodyStyle = getComputedStyle(document.body);
        const tabStyle = getComputedStyle(document.querySelector('.dockview-pane-tab[data-pane-tab="1"].active'));
        const panelStyle = getComputedStyle(document.querySelector('#panel-1'));
        const prefsRange = document.querySelector('input[data-setting-path="appearance.inactive_pane_opacity"]');
        const ringRange = document.querySelector('input[data-setting-path="appearance.pane_ring_opacity"]');
        const radio = document.querySelector('input[data-setting-path="appearance.date_time_hour_cycle"]');
        const prefsScroll = document.querySelector('.preferences-scroll');
        const finderMode = document.querySelector('#panel-__files__ .file-explorer-mode-toggle[aria-pressed="true"]');
        const tabMeta = document.getElementById('tabMetaToggle');
        const notify = document.getElementById('notifyToggle');
        const brandYo = document.querySelector('.brand-title .brand-yolo');
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
        scrollProbe.style.background = 'var(--pane-scrollbar-thumb-active)';
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
          brandYoBg: getComputedStyle(brandYo).backgroundColor,
          brandYoBorder: getComputedStyle(brandYo).borderTopColor,
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
    assert metrics["expectedScrollThumb"] == "rgba(255, 234, 0, 0.88)", metrics
    assert metrics["prefsScrollColor"].startswith(metrics["expectedNeutralScrollThumb"]), metrics
    assert metrics["prefsScrollThumb"] == metrics["expectedNeutralScrollThumb"], metrics
    assert metrics["finderModeBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["finderModeBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["tabMetaBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["tabMetaBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["notifyBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["brandYoBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["brandYoBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["markdownHeadingColor"] == "rgb(59, 130, 246)", metrics
    assert metrics["cmHeadingColor"] == "rgb(59, 130, 246)", metrics
    assert metrics["yoloBg"] == "rgb(59, 130, 246)", metrics
    assert metrics["yoloBorder"] == "rgb(59, 130, 246)", metrics
    assert metrics["shortcutHeadingColor"] == "rgb(59, 130, 246)", metrics
    assert metrics["swatchDisplay"] == "grid", metrics
    assert metrics["swatchRadius"] == "2px 0px 0px 2px", metrics
    assert metrics["settingsPosts"] >= 1, metrics
    browser.execute_script(
        """
        const panel = document.querySelector('#panel-__prefs__');
        panel?.classList.add('active-pane', 'focused-pane');
        panel?.style.setProperty('--pane-scrollbar-current-thumb', 'var(--pane-scrollbar-thumb-active)');
        """
    )
    WebDriverWait(browser, 2).until(
        lambda driver: driver.execute_script(
            "return getComputedStyle(document.querySelector('.preferences-scroll'), '::-webkit-scrollbar-thumb').backgroundColor"
        ) == metrics["expectedScrollThumb"]
    )
    browser.execute_script(
        """
        const panel = document.querySelector('#panel-__prefs__');
        panel?.classList.remove('active-pane', 'focused-pane');
        panel?.style.removeProperty('--pane-scrollbar-current-thumb');
        """
    )
    WebDriverWait(browser, 2).until(
        lambda driver: driver.execute_script(
            "return getComputedStyle(document.querySelector('.preferences-scroll'), '::-webkit-scrollbar-thumb').backgroundColor"
        ) == metrics["expectedNeutralScrollThumb"]
    )
    move_to_visible_panel(browser, "panel-1")
    WebDriverWait(browser, 2).until(
        lambda driver: driver.execute_script(
            "return getComputedStyle(document.querySelector('.preferences-scroll'), '::-webkit-scrollbar-thumb').backgroundColor"
        ) == metrics["expectedNeutralScrollThumb"]
    )
    browser.execute_script(
        """
        setFocusedPanelItem('1', {userInitiated: true});
        const radio = document.querySelector('input[type="radio"][data-setting-path="appearance.editor_cursor_color"][value="laser-lime"]');
        radio.checked = true;
        radio.dispatchEvent(new Event('change', {bubbles: true}));
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return window.__settingsPayload?.settings?.appearance?.editor_cursor_color === 'laser-lime'
              && getComputedStyle(document.documentElement).getPropertyValue('--active-terminal-cursor-rgb').trim() === '204 255 0'
              && terminals.get('1')?.term?.options?.theme?.cursor === '#ccff00';
            """
        )
    )
    cursor_metrics = browser.execute_script(
        """
        const probe = document.createElement('div');
        probe.style.background = 'var(--pane-scrollbar-thumb-active)';
        document.body.appendChild(probe);
        const activeThumb = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return {
          rootCursorRgb: getComputedStyle(document.documentElement).getPropertyValue('--active-terminal-cursor-rgb').trim(),
          terminalCursor: terminals.get('1')?.term?.options?.theme?.cursor || '',
          activeScrollbarThumb: activeThumb,
        };
        """
    )
    assert cursor_metrics["rootCursorRgb"] == "204 255 0", cursor_metrics
    assert cursor_metrics["terminalCursor"] == "#ccff00", cursor_metrics
    assert cursor_metrics["activeScrollbarThumb"] == "rgba(204, 255, 0, 0.88)", cursor_metrics
    browser.execute_script(
        """
        const radio = document.querySelector('input[type="radio"][data-setting-path="appearance.active_color"][value="yellow"]');
        radio.checked = true;
        radio.dispatchEvent(new Event('change', {bubbles: true}));
        """
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const brandYo = document.querySelector('.brand-title .brand-yolo');
            return window.__settingsPayload?.settings?.appearance?.active_color === 'yellow'
              && getComputedStyle(brandYo).backgroundColor === 'rgb(234, 179, 8)'
              && getComputedStyle(brandYo).borderTopColor === 'rgb(234, 179, 8)';
            """
        )
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
        probe.style.background = 'var(--pane-scrollbar-thumb-active)';
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

    browser.execute_script("document.querySelector('.info-list')?.closest('.panel')?.classList.add('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".info-list")).perform()
    wait_thumb(".info-list", metrics["accent"])
    wait_thumb(".preferences-scroll", metrics["neutral"])

    browser.execute_script(
        """
        document.querySelector('.info-list')?.closest('.panel')?.classList.remove('active-pane', 'focused-pane');
        document.querySelector('.preferences-scroll')?.closest('.panel')?.classList.add('active-pane', 'focused-pane');
        """
    )
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".preferences-scroll")).perform()
    wait_thumb(".preferences-scroll", metrics["accent"])
    wait_thumb(".info-list", metrics["neutral"])

    browser.execute_script("document.querySelector('.preferences-scroll')?.closest('.panel')?.classList.remove('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".preferences-scroll")).perform()
    wait_thumb(".preferences-scroll", metrics["neutral"])

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
    # the active-tab greens are tuned PER THEME so a theme switch visibly repaints the active
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
    css = app_css()
    assert "--pane-tab-strip-hover-bg" not in css


def test_diff_added_active_line_uses_same_fill_as_neighbor(browser, tmp_path):
    css = app_css()
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
              <div id="added-active" class="cm-line cm-insertedLine cm-activeLine">added <span id="added-match" class="cm-searchMatch">active</span></div>
              <div id="added-selected" class="cm-line cm-insertedLine">added <span id="selected-match" class="cm-searchMatch cm-searchMatch-selected">selected</span></div>
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
          matchBg: bg('#added-match'),
          matchColor: getComputedStyle(document.querySelector('#added-match')).color,
          matchShadow: getComputedStyle(document.querySelector('#added-match')).boxShadow,
          selectedBg: bg('#selected-match'),
          selectedShadow: getComputedStyle(document.querySelector('#selected-match')).boxShadow,
          addToken: getComputedStyle(document.querySelector('#host')).getPropertyValue('--diff-add-line-bg').trim(),
          removeToken: getComputedStyle(document.querySelector('#host')).getPropertyValue('--diff-remove-line-bg').trim(),
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
        };
        return {dark, light};
        """
    )
    assert metrics["dark"]["added"] == metrics["dark"]["activeAdded"], metrics
    assert metrics["dark"]["plainActive"] in ("rgba(0, 0, 0, 0)", "transparent"), metrics
    assert metrics["dark"]["matchBg"] != metrics["dark"]["added"], metrics
    assert metrics["dark"]["matchColor"] == "rgb(11, 16, 32)", metrics
    assert metrics["dark"]["matchShadow"] != "none", metrics
    assert metrics["dark"]["selectedBg"] != metrics["dark"]["added"], metrics
    assert metrics["dark"]["selectedShadow"] != "none", metrics
    assert "transparent" not in metrics["dark"]["addToken"], metrics
    assert "transparent" not in metrics["dark"]["removeToken"], metrics
    assert metrics["light"]["added"] == metrics["light"]["activeAdded"], metrics
    assert metrics["light"]["matchBg"] != metrics["light"]["added"], metrics
    assert metrics["light"]["matchColor"] == "rgb(17, 24, 39)", metrics
    assert metrics["light"]["matchShadow"] != "none", metrics
    assert metrics["light"]["selectedBg"] != metrics["light"]["added"], metrics
    assert metrics["light"]["addToken"] == "#bfeac8", metrics


def test_readme_diff_waits_for_payload_before_building_codemirror(browser, tmp_path):
    css = app_css()
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
    css = app_css()
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
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return window.__previewToolbarReady != null")
    )
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


def test_editor_preview_direct_media_formats_use_shared_dispatch(browser, tmp_path):
    page = tmp_path / "preview-direct-media.html"
    page.write_text(live_runtime_boot_fixture_html(sessions=["1"]), encoding="utf-8")
    browser.get(page.as_uri() + "?sessions=1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof createFileEditorPanel === 'function' && document.querySelector('#grid');")
    )
    metrics = browser.execute_script(
        """
        const mount = document.getElementById('grid');
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
        const unsupported = makePanel('/home/test/repo/archive.bin', {kind: 'too-large', size: 999999, maxBytes: 1024, error: 'binary preview blocked'});
        const imageInfo = ({panel}) => {
          const img = panel.querySelector('.file-editor-image-panel img.file-editor-image');
          return {
            imagePaneHidden: panel.querySelector('.file-editor-image-panel')?.hidden === true,
            previewPaneHidden: panel.querySelector('.file-editor-preview-pane-panel')?.hidden === true,
            src: img?.getAttribute('src') || '',
            hasInlineSvg: Boolean(panel.querySelector('svg')),
            mode: editorViewModeFor(panel.dataset.filePath, panel.dataset.layoutItem),
            editButtonHidden: panel.querySelector('[data-editor-mode="edit"]')?.hidden === true,
            splitButtonHidden: panel.querySelector('[data-editor-mode="split"]')?.hidden === true,
            previewButtonHidden: panel.querySelector('[data-editor-mode="preview"]')?.hidden === true,
            zoomToolbarExists: Boolean(panel.querySelector('.file-editor-image-panel .file-editor-preview-zoom-toolbar')),
            zoomActions: Array.from(panel.querySelectorAll('.file-editor-image-panel [data-preview-zoom-action]')).map(button => button.dataset.previewZoomAction),
            zoomWheel: panel.querySelector('.file-editor-image-panel')?.dataset.previewZoomWheel || '',
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
        const codeBlock = code.panel.querySelector('.file-editor-preview-pane-panel code.language-python');
        return {
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
          code: {
            previewPaneHidden: code.panel.querySelector('.file-editor-preview-pane-panel')?.hidden === true,
            codeText: codeBlock?.textContent || '',
            codeClass: codeBlock?.className || '',
          },
          unsupported: {
            imagePaneHidden: unsupported.panel.querySelector('.file-editor-image-panel')?.hidden === true,
            previewPaneHidden: unsupported.panel.querySelector('.file-editor-preview-pane-panel')?.hidden === true,
            text: unsupported.panel.textContent || '',
            modeControlHidden: unsupported.panel.querySelector('.file-editor-mode-control-panel')?.hidden === true,
          },
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
        };
        """
    )
    for key in ("png", "apng", "jpg", "gif", "webp", "bmp", "ico", "avif", "svg"):
        assert metrics[key]["imagePaneHidden"] is False, metrics
        assert metrics[key]["previewPaneHidden"] is True, metrics
        assert metrics[key]["src"].startswith("/api/fs/raw?path="), metrics
        assert metrics[key]["mode"] == "preview", metrics
        assert metrics[key]["previewButtonHidden"] is False, metrics
        assert metrics[key]["editButtonHidden"] is True, metrics
        assert metrics[key]["splitButtonHidden"] is True, metrics
        assert metrics[key]["zoomToolbarExists"] is True, metrics
        assert metrics[key]["zoomActions"] == ["out", "fit", "actual", "in"], metrics
        assert metrics[key]["zoomWheel"] == "1", metrics
        assert metrics[key]["zoomPan"] == "1", metrics
    assert "photo.png" in metrics["png"]["src"], metrics
    assert "animation.apng" in metrics["apng"]["src"], metrics
    assert "photo.jpg" in metrics["jpg"]["src"], metrics
    assert "spinner.gif" in metrics["gif"]["src"], metrics
    assert "photo.webp" in metrics["webp"]["src"], metrics
    assert "photo.bmp" in metrics["bmp"]["src"], metrics
    assert "favicon.ico" in metrics["ico"]["src"], metrics
    assert "photo.avif" in metrics["avif"]["src"], metrics
    assert "diagram.svg" in metrics["svg"]["src"], metrics
    assert metrics["svg"]["hasInlineSvg"] is False, metrics
    assert metrics["pdf"]["previewPaneHidden"] is False, metrics
    assert metrics["pdf"]["iframeSrc"].startswith("/api/fs/raw?path="), metrics
    assert "spec.pdf" in metrics["pdf"]["iframeSrc"], metrics
    assert metrics["pdf"]["sandbox"] == "", metrics
    assert "Open" in metrics["pdf"]["fallbackText"] and "Download" in metrics["pdf"]["fallbackText"], metrics
    assert metrics["pdf"]["editButtonHidden"] is True and metrics["pdf"]["splitButtonHidden"] is True, metrics
    assert metrics["pdf"]["previewButtonHidden"] is False, metrics
    assert metrics["audio"]["mediaSrc"].startswith("/api/fs/raw?path="), metrics
    assert metrics["audio"]["controls"] is True, metrics
    assert "Open" in metrics["audio"]["fallbackText"] and "Download" in metrics["audio"]["fallbackText"], metrics
    assert metrics["video"]["mediaSrc"].startswith("/api/fs/raw?path="), metrics
    assert metrics["video"]["controls"] is True and metrics["video"]["autoplay"] is False, metrics
    assert "Open" in metrics["video"]["fallbackText"] and "Download" in metrics["video"]["fallbackText"], metrics
    assert metrics["tiff"]["previewPaneHidden"] is False, metrics
    assert "recognized but not previewable" in metrics["tiff"]["title"], metrics
    assert "image/tiff" in metrics["tiff"]["text"] and "Open" in metrics["tiff"]["text"] and "Download" in metrics["tiff"]["text"], metrics
    assert "Archive preview is not expanded" in metrics["archive"]["title"], metrics
    assert "application/zip" in metrics["archive"]["text"], metrics
    assert "external viewer" in metrics["parquet"]["title"], metrics
    assert "application/vnd.apache.parquet" in metrics["parquet"]["text"], metrics
    assert metrics["code"]["previewPaneHidden"] is False, metrics
    assert "print" in metrics["code"]["codeText"], metrics
    assert "language-python" in metrics["code"]["codeClass"], metrics
    assert metrics["unsupported"]["modeControlHidden"] is True, metrics
    assert "File is too large to preview" in metrics["unsupported"]["text"], metrics
    assert "binary preview blocked" in metrics["unsupported"]["text"], metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics


def test_editor_opens_mermaid_source_preview_by_default(browser, tmp_path):
    page = tmp_path / "preview-direct-mermaid-source.html"
    page.write_text(live_runtime_boot_fixture_html(sessions=["1"]), encoding="utf-8")
    browser.get(page.as_uri() + "?sessions=1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof openFileInEditor === 'function' && document.querySelector('#grid');")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
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
              errors: window.__bootErrors,
              rejections: window.__bootRejections,
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: window.__bootErrors, rejections: window.__bootRejections});
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
    assert metrics["config"]["htmlLabels"] is True, metrics
    assert metrics["config"]["flowchart"]["htmlLabels"] is True, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics


def _wcag_contrast(c1, c2):
    """WCAG contrast ratio between two CSS colors (#hex or rgb()/rgba()). None if unparseable."""
    def rel_lum(color):
        text = str(color or "").strip()
        if text.startswith("#"):
            h = text[1:]
            if len(h) == 3:
                h = "".join(ch * 2 for ch in h)
            if len(h) < 6:
                return None
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        else:
            m = re.match(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", text)
            if not m:
                return None
            r, g, b = float(m.group(1)), float(m.group(2)), float(m.group(3))
        def lin(v):
            v /= 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    a, b = rel_lum(c1), rel_lum(c2)
    if a is None or b is None:
        return None
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def test_direct_mermaid_sample_real_bundle_keeps_svg_text_labels(browser, tmp_path):
    browser.set_window_size(1200, 900)
    page = tmp_path / "preview-direct-mermaid-real-bundle.html"
    page.write_text(live_runtime_boot_fixture_html(sessions=["1"]), encoding="utf-8")
    browser.get(page.as_uri() + "?sessions=1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof createFileEditorPanel === 'function' && document.querySelector('#grid');")
    )
    mermaid_uri = (REPO_ROOT / "static" / "vendor" / "mermaid.min.js").as_uri()
    mermaid_source = (REPO_ROOT / "docs" / "preview-samples" / "14-mermaid.mmd").read_text(encoding="utf-8")
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
                const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
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
                const rect = node => {
                  if (!node) return null;
                  const box = node.getBoundingClientRect();
                  return {left: box.left, top: box.top, width: box.width, height: box.height, right: box.right, bottom: box.bottom};
                };
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
            const path = '/home/test/repo/docs/preview-samples/14-mermaid.mmd';
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
            setFileEditorPreviewZoomStateForPath(path, 'split:mermaid', {mode: 'manual', scale: 2.4});
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
            for (let i = 0; i < 50 && splitPreview.classList.contains('file-editor-preview-zoom-measuring'); i += 1) {
              await new Promise(resolve => setTimeout(resolve, 16));
            }
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
            // Start the pan from the top-left corner so the drag (which increases scroll) always
            // has headroom; the wheel zoom above may have already scrolled the viewport to max.
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
              config: window.mermaid?.mermaidAPI?.getConfig?.() || {},
              errors: window.__bootErrors,
              rejections: window.__bootRejections,
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
                for (let i = 0; i < 60 && bp.querySelector('.file-editor-preview-zoom-shell') && bp.querySelector('.file-editor-preview-zoom-shell').classList.contains('file-editor-preview-zoom-measuring'); i += 1) {
                  await new Promise(resolve => setTimeout(resolve, 16));
                }
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
            done(result);
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: window.__bootErrors, rejections: window.__bootRejections});
          }
        })();
        """,
        mermaid_uri,
        mermaid_source,
    )
    assert "error" not in metrics, metrics
    assert metrics["imageExists"] is True, metrics
    assert metrics["imageSrcPrefix"] in ("blob:", "data:"), metrics
    assert metrics["naturalWidth"] > 0 and metrics["naturalHeight"] > 0, metrics
    assert metrics["imageRectFit"]["height"] > 340, metrics
    assert metrics["imageRectFit"]["height"] >= metrics["viewport"]["clientHeight"] - 40, metrics
    assert metrics["wheelPrevented"] is True, metrics
    assert metrics["imageRectAfterWheel"]["width"] > metrics["imageRectFit"]["width"], metrics
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
    assert metrics["split"]["wheelPrevented"] is True, metrics
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
    assert metrics["split"]["imageRectAfterWheel"]["width"] > metrics["split"]["imageRect"]["width"], metrics
    assert (
        metrics["split"]["panAfter"]["scrollLeft"] > metrics["split"]["panStart"]["scrollLeft"]
        or metrics["split"]["panAfter"]["scrollTop"] > metrics["split"]["panStart"]["scrollTop"]
    ), metrics
    for label in ("Markdown pipeline", "Mermaid pipeline", "Media pipeline", "Preview pane", "Visible result"):
        assert label in metrics["probeSvgText"], metrics
    assert "<foreignObject" not in metrics["svgText"], metrics
    for label in ("Markdown pipeline", "Mermaid pipeline", "Media pipeline", "Preview pane", "Visible result"):
        assert label in metrics["svgText"], metrics
    assert "font-family:" in metrics["svgText"], metrics
    assert "font-weight:400" in metrics["svgText"], metrics
    assert re.search(r'fill="(?:#[0-9a-fA-F]{3,8}|rgb)', metrics["svgText"]), metrics
    assert metrics["config"]["htmlLabels"] is True, metrics
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
    edge_contrast = _wcag_contrast(bright["edgeStroke"], bright["paneBg"])
    assert edge_contrast is not None and edge_contrast >= 3.0, {"edgeStroke": bright["edgeStroke"], "paneBg": bright["paneBg"], "contrast": edge_contrast}
    for fill in bright["labelFills"]:
        if not fill:
            continue
        label_contrast = _wcag_contrast(fill, bright["paneBg"])
        assert label_contrast is not None and label_contrast >= 3.0, {"labelFill": fill, "paneBg": bright["paneBg"], "contrast": label_contrast}
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
    page = tmp_path / "preview-sniffed-binary.html"
    page.write_text(live_runtime_boot_fixture_html(sessions=["1"]), encoding="utf-8")
    browser.get(page.as_uri() + "?sessions=1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof openFileInEditor === 'function' && document.querySelector('#grid');")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const originalFetch = window.fetch.bind(window);
            const path = '/home/test/repo/renamed.bin';
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
              return originalFetch(input, options);
            };
            const item = await openFileInEditor(path, {name: 'renamed.bin', size: 18, mtime: 33}, {userInitiated: true});
            const panel = panelNodes.get(item);
            const preview = panel?.querySelector('.file-editor-preview-pane-panel');
            const image = preview?.querySelector('img.file-editor-preview-image');
            const state = fileStateFor(path);
            done({
              stateKind: state?.kind || '',
              mediaKind: state?.mediaKind || '',
              mime: state?.mime || '',
              previewHidden: preview?.hidden === true,
              imageSrc: image?.getAttribute('src') || '',
              mode: editorViewModeFor(path, item),
              errors: window.__bootErrors,
              rejections: window.__bootRejections,
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: window.__bootErrors, rejections: window.__bootRejections});
          }
        })();
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["stateKind"] == "media", metrics
    assert metrics["mediaKind"] == "image", metrics
    assert metrics["mime"] == "image/png", metrics
    assert metrics["previewHidden"] is False, metrics
    assert metrics["imageSrc"].startswith("/api/fs/raw?path="), metrics
    assert "renamed.bin" in metrics["imageSrc"], metrics
    assert metrics["mode"] == "preview", metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics


def test_preview_registry_structured_table_and_offline_markdown(browser, tmp_path):
    page = tmp_path / "preview-registry-structured-table.html"
    page.write_text(live_runtime_boot_fixture_html(sessions=["1"]), encoding="utf-8")
    browser.get(page.as_uri() + "?sessions=1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof renderEditorPreviewPane === 'function' && document.querySelector('#grid');")
    )
    metrics = browser.execute_script(
        """
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
        const jsonl = makePanel('/home/test/repo/events.jsonl', {kind: 'text', content: '{"id":1}\\n{"id":2}\\n', original: '', dirty: false, language: 'json'});
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
        const image = mdPreview.querySelector('img[alt="local"]');
        return {
          markdown: {
            heading: mdPreview.querySelector('h1')?.textContent || '',
            tableCells: Array.from(mdPreview.querySelectorAll('table th, table td')).map(node => node.textContent),
            checkboxCount: mdPreview.querySelectorAll('input.markdown-task-checkbox').length,
            imageSrc: image?.getAttribute('src') || '',
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
            text: jsonl.panel.querySelector('.file-editor-data-preview code')?.textContent || '',
          },
          badJsonl: {
            header: badJsonl.panel.querySelector('.file-editor-data-preview-header')?.textContent || '',
            error: badJsonl.panel.querySelector('.file-editor-preview-error')?.textContent || '',
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
          errors: window.__bootErrors,
          rejections: window.__bootRejections,
        };
        """
    )
    assert metrics["markdown"]["heading"] == "Offline Preview", metrics
    assert metrics["markdown"]["tableCells"] == ["A", "B", "1", "2"], metrics
    assert metrics["markdown"]["checkboxCount"] == 1, metrics
    assert metrics["markdown"]["imageSrc"] == "/api/fs/raw?path=%2Fhome%2Ftest%2Frepo%2Fdocs%2Fasset%20dir%2Fa.png", metrics
    assert metrics["markdown"]["imageTitle"] == "title", metrics
    assert metrics["markdown"]["scriptCount"] == 0 and metrics["markdown"]["svgCount"] == 0, metrics
    assert "code-keyword" in metrics["markdown"]["codeHtml"], metrics
    assert metrics["validJson"]["kind"] == "structured", metrics
    assert "JSON preview" in metrics["validJson"]["header"], metrics
    assert '"a": 1' in metrics["validJson"]["text"], metrics
    assert "JSON parse error" in metrics["invalidJson"]["header"], metrics
    assert metrics["invalidJson"]["error"], metrics
    assert "JSONL preview" in metrics["jsonl"]["header"] and '"id":1' in metrics["jsonl"]["text"], metrics
    assert "JSONL parse error" in metrics["badJsonl"]["header"] and "line 2" in metrics["badJsonl"]["error"], metrics
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
    page = tmp_path / "preview-markdown-media-mermaid.html"
    page.write_text(live_runtime_boot_fixture_html(sessions=["1"]), encoding="utf-8")
    browser.get(page.as_uri() + "?sessions=1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof createFileEditorPanel === 'function' && document.querySelector('#grid');")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
            const escapeHtml = value => String(value || '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
            window.marked = {
              parse(markdown) {
                const parts = [];
                for (const line of String(markdown || '').split('\\n')) {
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
            const content = [
              '# Preview Media',
              '![local](./images/local pic.png?cache=1#frag)',
              '![svg](../assets/logo.svg)',
              '![external](https://example.test/image.png)',
              '![unsafe](javascript:alert(1))',
              '![missing](./missing.png)',
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
              return {
                exists: Boolean(img),
                src: img?.getAttribute('src') || '',
                resolvedPath: img?.dataset?.resolvedPath || '',
                originalSrc: img?.dataset?.originalSrc || '',
                className: img?.className || '',
                hasSrc: img?.hasAttribute('src') === true,
              };
            };
            const initialImages = {
              local: imageSnapshot('local'),
              svg: imageSnapshot('svg'),
              external: imageSnapshot('external'),
              unsafe: imageSnapshot('unsafe'),
              missing: imageSnapshot('missing'),
            };
            const missing = preview.querySelector('img[alt="missing"]');
            if (missing) missing.dispatchEvent(new Event('error'));
            const brokenText = preview.querySelector('.markdown-image-error')?.textContent || '';
            if (preview._previewAsync) await preview._previewAsync;
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
              errors: window.__bootErrors,
              rejections: window.__bootRejections,
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: window.__bootErrors, rejections: window.__bootRejections});
          }
        })();
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["initialImages"]["local"]["exists"] is True, metrics
    assert metrics["initialImages"]["local"]["src"] == "/api/fs/raw?path=%2Fhome%2Ftest%2Frepo%2Fdocs%2Fimages%2Flocal%20pic.png", metrics
    assert metrics["initialImages"]["local"]["resolvedPath"] == "/home/test/repo/docs/images/local pic.png", metrics
    assert metrics["initialImages"]["local"]["originalSrc"] == "./images/local pic.png?cache=1#frag", metrics
    assert "markdown-preview-image" in metrics["initialImages"]["local"]["className"], metrics
    assert metrics["initialImages"]["svg"]["src"] == "/api/fs/raw?path=%2Fhome%2Ftest%2Frepo%2Fassets%2Flogo.svg", metrics
    assert metrics["initialImages"]["svg"]["resolvedPath"] == "/home/test/repo/assets/logo.svg", metrics
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
    assert metrics["mermaid"]["config"]["htmlLabels"] is True, metrics
    assert metrics["mermaid"]["config"]["flowchart"]["htmlLabels"] is True, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics


def test_markdown_preview_visual_rendering_has_mermaid_labels_and_media(browser, tmp_path):
    browser.set_window_size(1200, 1200)
    page = tmp_path / "preview-markdown-visual-mermaid-media.html"
    page.write_text(live_runtime_boot_fixture_html(sessions=["1"], grid_width=1000, grid_height=980), encoding="utf-8")
    browser.get(page.as_uri() + "?sessions=1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof createFileEditorPanel === 'function' && document.querySelector('#grid');")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
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
            const rect = node => {
              if (!node) return null;
              const box = node.getBoundingClientRect();
              return {left: box.left, top: box.top, width: box.width, height: box.height, right: box.right, bottom: box.bottom};
            };
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
            ctx.fillStyle = '#111827';
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
              errors: window.__bootErrors,
              rejections: window.__bootRejections,
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: window.__bootErrors, rejections: window.__bootRejections});
          }
        })();
        """
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
    assert metrics["mermaidConfig"]["flowchart"]["htmlLabels"] is True, metrics
    assert metrics["mermaidConfig"]["htmlLabels"] is True, metrics
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
    page = tmp_path / "preview-popout-media-mermaid.html"
    page.write_text(live_runtime_boot_fixture_html(sessions=["1"]), encoding="utf-8")
    browser.get(page.as_uri() + "?sessions=1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof renderedPreviewSnapshotAsync === 'function' && document.querySelector('#grid');")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
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
            done({
              immediateText: immediate.html,
              completedText: completed.html,
              rootClass: root?.className || '',
              rootHtml: root?.innerHTML || '',
              rawImageSrc: root?.querySelector('img[alt="local"]')?.getAttribute('src') || '',
              mermaidExists: Boolean(root?.querySelector('img.mermaid-preview-image')),
              mermaidSrcPrefix: String(root?.querySelector('img.mermaid-preview-image')?.getAttribute('src') || '').slice(0, 5),
              toolbarText: doc.querySelector('.file-preview-popout-title')?.textContent || '',
              bodyClass: doc.body?.className || '',
              errors: window.__bootErrors,
              rejections: window.__bootRejections,
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: window.__bootErrors, rejections: window.__bootRejections});
          }
        })();
        """
    )
    assert "error" not in metrics, metrics
    assert "Rendering Mermaid diagram" in metrics["immediateText"], metrics
    # The "Rendering..." placeholder reuses the shared blinking-ellipsis loader, not a static string.
    assert "moving-ellipsis" in metrics["immediateText"], metrics
    assert "Rendering Mermaid diagram" not in metrics["completedText"], metrics
    assert "/api/fs/raw?path=%2Fhome%2Ftest%2Frepo%2Fdocs%2Fassets%2Fpopout.png" in metrics["completedText"], metrics
    assert metrics["rawImageSrc"] in ("", "/api/fs/raw?path=%2Fhome%2Ftest%2Frepo%2Fdocs%2Fassets%2Fpopout.png"), metrics
    assert metrics["mermaidExists"] is True, metrics
    assert metrics["mermaidSrcPrefix"] in ("blob:", "data:"), metrics
    assert "README.md" in metrics["toolbarText"], metrics
    assert "file-preview-popout-window" in metrics["bodyClass"], metrics
    assert "markdown-body" in metrics["rootClass"], metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics


def test_preview_popout_zoom_controls_are_hydrated(browser, tmp_path):
    page = tmp_path / "preview-popout-zoom-controls.html"
    page.write_text(live_runtime_boot_fixture_html(sessions=["1"]), encoding="utf-8")
    browser.get(page.as_uri() + "?sessions=1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof renderedPreviewSnapshotAsync === 'function' && document.querySelector('#grid');")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const frame = win => new Promise(resolve => (win || window).requestAnimationFrame(resolve));
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
            const rect = node => {
              if (!node) return null;
              const box = node.getBoundingClientRect();
              return {left: box.left, top: box.top, width: box.width, height: box.height, right: box.right, bottom: box.bottom};
            };
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
                      <line x1="360" y1="165" x2="540" y2="355" stroke="#111827" stroke-width="6"/>
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
            const wheelZoomed = {value: value(), mode: root.dataset.previewZoomMode || '', rect: rect(image)};
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
              zoomWheel: root.dataset.previewZoomWheel || '',
              zoomPan: root.dataset.previewZoomPan || '',
              initial,
              actual,
              wheelPrevented,
              wheelZoomed,
              zoomed,
            pointerDownPrevented,
            pointerMovePrevented,
            panBefore,
            panStart,
            panAfter,
              fit,
              errors: window.__bootErrors,
              rejections: window.__bootRejections,
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: window.__bootErrors, rejections: window.__bootRejections});
          }
        })();
        """
    )
    assert "error" not in metrics, metrics
    assert "file-editor-preview-zoom-shell" in metrics["rootClass"], metrics
    assert metrics["imageExists"] is True, metrics
    assert metrics["naturalWidth"] == 900 and metrics["naturalHeight"] == 520, metrics
    assert metrics["toolbarActions"] == ["out", "fit", "actual", "in"], metrics
    assert metrics["zoomWheel"] == "1", metrics
    assert metrics["zoomPan"] == "1", metrics
    assert metrics["initial"]["mode"] == "fit", metrics
    assert metrics["actual"]["mode"] == "actual", metrics
    assert metrics["actual"]["value"] == "100%", metrics
    assert metrics["wheelPrevented"] is True, metrics
    assert metrics["wheelZoomed"]["mode"] == "manual", metrics
    assert metrics["wheelZoomed"]["rect"]["width"] > metrics["actual"]["rect"]["width"], metrics
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
    page = tmp_path / "markdown-task-checkbox-split.html"
    page.write_text(live_runtime_boot_fixture_html(sessions=["1"]), encoding="utf-8")
    browser.get(page.as_uri() + "?sessions=1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof createFileEditorPanel === 'function' && document.querySelector('#grid');")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
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
            const waitFor = async predicate => {
              for (let attempt = 0; attempt < 160; attempt += 1) {
                if (predicate()) return true;
                await frame();
              }
              return false;
            };
            const ready = await waitFor(() => panel.querySelectorAll('.file-editor-preview-pane-panel input.markdown-task-checkbox').length === 2 && panel._cmView?.state?.doc?.toString?.().includes('first task'));
            const before = {
              ready,
              content: openFiles.get(path)?.content || '',
              checked: Array.from(panel.querySelectorAll('.file-editor-preview-pane-panel input.markdown-task-checkbox')).map(input => input.checked),
              disabled: Array.from(panel.querySelectorAll('.file-editor-preview-pane-panel input.markdown-task-checkbox')).map(input => input.disabled),
              cmText: panel._cmView?.state?.doc?.toString?.() || '',
            };
            const first = panel.querySelector('.file-editor-preview-pane-panel input.markdown-task-checkbox');
            first.click();
            await waitFor(() => (openFiles.get(path)?.content || '').startsWith('- [x] first task'));
            await frame();
            await frame();
            const after = {
              content: openFiles.get(path)?.content || '',
              dirty: openFiles.get(path)?.dirty === true,
              checked: Array.from(panel.querySelectorAll('.file-editor-preview-pane-panel input.markdown-task-checkbox')).map(input => input.checked),
              sourceLines: Array.from(panel.querySelectorAll('.file-editor-preview-pane-panel input.markdown-task-checkbox')).map(input => input.dataset.sourceLine || ''),
              cmText: panel._cmView?.state?.doc?.toString?.() || '',
              previewText: panel.querySelector('.file-editor-preview-pane-panel')?.textContent || '',
              mode: editorViewModeFor(path, item),
              splitVisible: panel.querySelector('.file-editor-content')?.classList.contains('split-preview') === true,
              errors: window.__bootErrors,
              rejections: window.__bootRejections,
            };
            done({before, after});
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: window.__bootErrors, rejections: window.__bootRejections});
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


def test_preview_popout_toolbar_and_state_sync(browser, tmp_path):
    page = tmp_path / "preview-popout-sync.html"
    page.write_text(
        live_runtime_boot_fixture_html(
            settings={"appearance": {"preview_font_size": 16}},
            sessions=["1"],
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri() + "?sessions=1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof createFileEditorPanel === 'function' && document.querySelector('#grid');")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
            const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
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
            await frame();
            await frame();
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
            await delay(220);
            previewPane.scrollTop = 0;
            previewPane.dispatchEvent(new Event('scroll', {bubbles: true}));
            await frame();
            await frame();
            const afterEditorTopScroll = {
              previewTop: previewPane.scrollTop,
              popupTop: popupScroller().scrollTop,
            };
            await delay(220);
            previewPane.scrollTop = maxScrollTop(previewPane);
            previewPane.dispatchEvent(new Event('scroll', {bubbles: true}));
            await frame();
            await frame();
            const afterEditorBottomScroll = {
              previewTop: previewPane.scrollTop,
              previewMax: maxScrollTop(previewPane),
              popupTop: popupScroller().scrollTop,
              popupMax: maxScrollTop(popupScroller()),
            };
            await delay(220);
            const popupBefore = popupScroller().scrollTop;
            popupScroller().scrollTop = Math.round(maxScrollTop(popupScroller()) * 0.61);
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
            };
            await delay(220);
            popupScroller().scrollTop = 0;
            popupScroller().dispatchEvent(new Event('scroll', {bubbles: true}));
            popupDoc.defaultView.dispatchEvent(new Event('scroll'));
            await frame();
            await frame();
            const afterPopupTopScroll = {
              popupTop: popupScroller().scrollTop,
              previewTop: previewPane.scrollTop,
            };
            await delay(220);
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
            for (let attempt = 0; attempt < 120; attempt += 1) {
              if (panel._cmView?.scrollDOM) break;
              await frame();
            }
            const editorScroller = panel._cmView?.scrollDOM;
            popupScroller().scrollTop = maxScrollTop(popupScroller());
            popupScroller().dispatchEvent(new Event('scroll', {bubbles: true}));
            popupDoc.defaultView.dispatchEvent(new WheelEvent('wheel', {deltaY: 0, bubbles: true}));
            popupDoc.defaultView.dispatchEvent(new Event('scroll'));
            await frame();
            await frame();
            const afterPopupBottomScrollEditMode = {
              mode: editorViewModeFor(path, item),
              popupTop: popupScroller().scrollTop,
              popupMax: maxScrollTop(popupScroller()),
              editorTop: editorScroller?.scrollTop || 0,
              editorMax: maxScrollTop(editorScroller),
            };
            await delay(220);
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
            await delay(220);
            splitEditorScroller.scrollTop = 0;
            splitEditorScroller.dispatchEvent(new Event('scroll', {bubbles: true}));
            await frame();
            await frame();
            const afterSplitEditorTopScroll = {
              editorTop: splitEditorScroller.scrollTop,
              previewTop: splitPreviewPane.scrollTop,
            };
            await delay(220);
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
            await delay(220);
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
            await delay(220);
            splitPreviewPane.scrollTop = 0;
            splitPreviewPane.dispatchEvent(new Event('scroll', {bubbles: true}));
            await frame();
            await frame();
            const afterSplitPreviewTopScroll = {
              previewTop: splitPreviewPane.scrollTop,
              editorTop: splitEditorScroller.scrollTop,
            };
            await delay(220);
            splitPreviewPane.scrollTop = maxScrollTop(splitPreviewPane);
            splitPreviewPane.dispatchEvent(new Event('scroll', {bubbles: true}));
            await frame();
            await frame();
            const afterSplitPreviewBottomScroll = {
              previewTop: splitPreviewPane.scrollTop,
              previewMax: maxScrollTop(splitPreviewPane),
              editorTop: splitEditorScroller.scrollTop,
              editorMax: maxScrollTop(splitEditorScroller),
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
              errors: window.__bootErrors,
              rejections: window.__bootRejections,
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
              afterSplitPreviewBottomScroll,
              afterSplitModeClose,
              afterPreviewModeClose,
            });
          } catch (error) {
            done({error: String(error), stack: error?.stack || '', errors: window.__bootErrors, rejections: window.__bootRejections});
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
    assert abs(metrics["afterEditorBottomScroll"]["previewTop"] - metrics["afterEditorBottomScroll"]["previewMax"]) <= 1, metrics
    assert abs(metrics["afterEditorBottomScroll"]["popupTop"] - metrics["afterEditorBottomScroll"]["popupMax"]) <= 1, metrics
    assert metrics["afterPopupScroll"]["popupTop"] < metrics["afterPopupScroll"]["popupBefore"], metrics
    assert metrics["afterPopupScroll"]["previewTop"] < metrics["afterEditorBottomScroll"]["previewTop"], metrics
    assert abs(metrics["afterPopupScroll"]["popupCenterRatio"] - metrics["afterPopupScroll"]["previewCenterRatio"]) <= 0.05, metrics
    assert abs(metrics["afterPopupScroll"]["headerTop"]) <= 1, metrics
    assert metrics["afterPopupTopScroll"]["popupTop"] == 0, metrics
    assert metrics["afterPopupTopScroll"]["previewTop"] == 0, metrics
    assert abs(metrics["afterPopupBottomScroll"]["popupTop"] - metrics["afterPopupBottomScroll"]["popupMax"]) <= 1, metrics
    assert abs(metrics["afterPopupBottomScroll"]["previewTop"] - metrics["afterPopupBottomScroll"]["previewMax"]) <= 1, metrics
    assert metrics["afterPopupBottomScrollEditMode"]["mode"] == "edit", metrics
    assert abs(metrics["afterPopupBottomScrollEditMode"]["popupTop"] - metrics["afterPopupBottomScrollEditMode"]["popupMax"]) <= 1, metrics
    assert abs(metrics["afterPopupBottomScrollEditMode"]["editorTop"] - metrics["afterPopupBottomScrollEditMode"]["editorMax"]) <= 1, metrics
    assert metrics["afterSplitEditorScroll"]["editorTop"] > 0, metrics
    assert metrics["afterSplitEditorScroll"]["previewTop"] > 0, metrics
    assert abs(metrics["afterSplitEditorScroll"]["editorCenterRatio"] - metrics["afterSplitEditorScroll"]["previewCenterRatio"]) <= 0.05, metrics
    assert metrics["afterSplitEditorTopScroll"]["editorTop"] == 0, metrics
    assert metrics["afterSplitEditorTopScroll"]["previewTop"] == 0, metrics
    assert abs(metrics["afterSplitEditorBottomScroll"]["editorTop"] - metrics["afterSplitEditorBottomScroll"]["editorMax"]) <= 1, metrics
    assert abs(metrics["afterSplitEditorBottomScroll"]["previewTop"] - metrics["afterSplitEditorBottomScroll"]["previewMax"]) <= 1, metrics
    assert metrics["afterSplitPreviewScroll"]["previewTop"] < metrics["afterSplitEditorBottomScroll"]["previewTop"], metrics
    assert metrics["afterSplitPreviewScroll"]["editorTop"] < metrics["afterSplitEditorBottomScroll"]["editorTop"], metrics
    assert abs(metrics["afterSplitPreviewScroll"]["previewCenterRatio"] - metrics["afterSplitPreviewScroll"]["editorCenterRatio"]) <= 0.05, metrics
    assert metrics["afterSplitPreviewTopScroll"]["previewTop"] == 0, metrics
    assert metrics["afterSplitPreviewTopScroll"]["editorTop"] == 0, metrics
    assert abs(metrics["afterSplitPreviewBottomScroll"]["previewTop"] - metrics["afterSplitPreviewBottomScroll"]["previewMax"]) <= 1, metrics
    assert abs(metrics["afterSplitPreviewBottomScroll"]["editorTop"] - metrics["afterSplitPreviewBottomScroll"]["editorMax"]) <= 1, metrics
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


def test_light_editor_and_preview_share_python_fence_token_colors(browser, tmp_path):
    page = tmp_path / "editor-preview-shared-light-syntax.html"
    page.write_text(
        live_runtime_boot_fixture_html(
            settings={"appearance": {"preview_font_size": 16}},
            sessions=["1"],
        ),
        encoding="utf-8",
    )
    browser.get(page.as_uri() + "?sessions=1")
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof createFileEditorPanel === 'function' && document.querySelector('#grid');")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
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
              errors: window.__bootErrors,
              rejections: window.__bootRejections,
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
            done({error: String(error), stack: error?.stack || '', errors: window.__bootErrors, rejections: window.__bootRejections});
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
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return window.__previewVanillaReady != null")
    )
    metrics = browser.execute_script("return window.__previewVanillaReady")
    assert metrics["normal"]["vanillaClass"] is False, metrics
    assert metrics["normal"]["headingColor"] != "rgb(17, 24, 39)", metrics
    assert metrics["normal"]["codeSpanColor"] == "rgb(0, 0, 255)", metrics
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
    css = app_css()
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
    css = app_css()
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


def test_long_markdown_editor_restores_scroll_after_codemirror_recreate(browser, tmp_path):
    css = app_css()
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
    page = tmp_path / "long-markdown-editor-scroll.html"
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
              const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
              const waitFor = async predicate => {{
                for (let attempt = 0; attempt < 180; attempt += 1) {{
                  if (predicate()) return true;
                  await frame();
                }}
                return false;
              }};
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
        encoding="utf-8",
    )
    browser.get(page.as_uri())
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


def test_share_host_editor_snapshot_tracks_codemirror_cursor_after_typing(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, sessions=["1"])
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          activeShares = [{
            token: 'share-token',
            shortId: 'share-token',
            mode: 'ro',
            scheme: 'http',
            session: '1',
            sessions: ['1'],
            viewers: 1,
            maxViewers: 5,
            expiresAt: Math.floor(Date.now() / 1000) + 600,
          }];
          const path = '/home/test/yolomux.dev/docs/DONE.md';
          const item = fileEditorItemFor(path);
          const content = [
            '# DONE',
            '',
            'First paragraph stays visible.',
            'Second paragraph receives the typed text.',
            'Third paragraph is only here to keep normal editor structure.',
          ].join('\\n');
          setFileState(path, {
            kind: 'text',
            content,
            original: content,
            dirty: false,
            language: 'markdown',
            gitRoot: '/home/test/yolomux.dev',
            gitTracked: true,
            gitHasHistory: true,
            gitHistory: [{ref: 'HEAD'}],
          });
          setFileEditorViewMode(path, 'edit', item);
          registerFileEditorLayoutItem(path, {item});
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([item], item);
          applyLayoutSlots(next, {focusSession: item, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 220; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => panelNodes.get(item)?._cmView?.scrollDOM);
          if (!ready) return {error: 'CodeMirror editor did not initialize', bootErrors: window.__bootErrors || [], bootRejections: window.__bootRejections || []};
          const panel = panelNodes.get(item);
          const view = panel._cmView;
          fileEditorViewState.set(item, {scrollTop: 0, scrollLeft: 0, anchor: 0, head: 0, scrollSnapshot: null});
          const insertAt = content.indexOf('receives');
          const insert = 'typed ';
          view.focus();
          view.dispatch({
            changes: {from: insertAt, to: insertAt, insert},
            selection: {anchor: insertAt + insert.length, head: insertAt + insert.length},
          });
          await frame();
          await frame();
          const cached = fileEditorViewState.get(item) || {};
          const snapshot = shareUiStateSnapshot();
          const modeEntry = (snapshot.editor?.modes || []).find(entry => entry.item === item || entry.path === path) || {};
          return {
            item,
            expectedAnchor: insertAt + insert.length,
            cachedAnchor: cached.anchor,
            cachedHead: cached.head,
            snapshotAnchor: modeEntry.viewState?.anchor,
            snapshotHead: modeEntry.viewState?.head,
            dirty: openFiles.get(path)?.dirty === true,
            sentSockets: window.__bootSockets || [],
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["dirty"] is True, metrics
    assert metrics["cachedAnchor"] == metrics["expectedAnchor"], metrics
    assert metrics["cachedHead"] == metrics["expectedAnchor"], metrics
    assert metrics["snapshotAnchor"] == metrics["expectedAnchor"], metrics
    assert metrics["snapshotHead"] == metrics["expectedAnchor"], metrics


def test_long_markdown_editor_scroll_survives_preferences_tab_roundtrip(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof fileEditorItemFor === 'function'
              && typeof applyLayoutSlots === 'function'
              && typeof createFileEditorPanel === 'function'
              && document.querySelector('#grid') !== null;
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          const path = '/home/test/repo/2026.md';
          const item = fileEditorItemFor(path);
          const content = Array.from({length: 1400}, (_value, index) => `# Entry ${index + 1}\\n\\n- Work item ${index + 1} with enough text to produce normal Markdown editor rows.`).join('\\n');
          setFileState(path, {
            kind: 'text',
            content,
            original: content,
            dirty: false,
            language: 'markdown',
            gitRoot: '/home/test/repo',
            gitTracked: true,
            gitHasHistory: true,
            gitHistory: [{ref: 'HEAD'}],
          });
          setFileEditorViewMode(path, 'edit', item);
          registerFileEditorLayoutItem(path);
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([item, prefsItemId], item);
          applyLayoutSlots(next, {focusSession: item, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 220; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => {
            const panel = panelNodes.get(item);
            const scroller = panel?._cmView?.scrollDOM;
            return activeItemForSide('left') === item
              && panel?.isConnected
              && scroller
              && scroller.scrollHeight > scroller.clientHeight * 3;
          });
          if (!ready) {
            const panel = panelNodes.get(item);
            const scroller = panel?._cmView?.scrollDOM;
            const rect = panel?.getBoundingClientRect?.();
            return {
              error: 'file editor did not become scrollable',
              active: activeItemForSide('left'),
              item,
              panelExists: Boolean(panel),
              connected: Boolean(panel?.isConnected),
              panelHeight: rect?.height || 0,
              hasView: Boolean(panel?._cmView),
              scrollHeight: scroller?.scrollHeight || 0,
              clientHeight: scroller?.clientHeight || 0,
              cmText: panel?.querySelector?.('.file-editor-codemirror-panel')?.textContent?.slice(0, 80) || '',
              bootErrors: window.__bootErrors || [],
              bootRejections: window.__bootRejections || [],
            };
          }
          const panel = panelNodes.get(item);
          const scroller = panel._cmView.scrollDOM;
          scroller.scrollTop = Math.min(9000, scroller.scrollHeight - scroller.clientHeight - 10);
          await frame();
          await frame();
          const savedTop = scroller.scrollTop;
          activatePaneTab('left', prefsItemId, {userInitiated: true});
          const prefsReady = await waitFor(() => activeItemForSide('left') === prefsItemId && panelNodes.get(prefsItemId)?.isConnected);
          const captured = fileEditorViewState.get(item);
          const capturedTop = captured?.scrollTop || 0;
          const capturedSnapshot = Boolean(captured?.scrollSnapshot);
          if (!prefsReady) return {error: 'preferences tab did not activate', savedTop, capturedTop, capturedSnapshot};
          activatePaneTab('left', item, {userInitiated: true});
          const fileReady = await waitFor(() => activeItemForSide('left') === item && panelNodes.get(item)?.isConnected && panelNodes.get(item)?._cmView?.scrollDOM);
          if (!fileReady) return {error: 'file tab did not reactivate', savedTop, capturedTop, capturedSnapshot};
          await frame();
          await frame();
          await new Promise(resolve => setTimeout(resolve, 140));
          await frame();
          const restoredPanel = panelNodes.get(item);
          const restoredScroller = restoredPanel._cmView.scrollDOM;
          return {
            savedTop,
            capturedTop,
            capturedSnapshot,
            restoredTop: restoredScroller.scrollTop,
            scrollHeight: restoredScroller.scrollHeight,
            clientHeight: restoredScroller.clientHeight,
            active: activeItemForSide('left'),
            focusedPanelItem,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["capturedSnapshot"] is True, metrics
    assert abs(metrics["capturedTop"] - metrics["savedTop"]) < 32, metrics
    assert abs(metrics["restoredTop"] - metrics["savedTop"]) < 32, metrics


def test_long_markdown_editor_scroll_survives_dockview_tab_click_roundtrip(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof applyLayoutSlots === 'function' && typeof registerFileEditorLayoutItem === 'function';"
        )
    )
    setup = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          const path = '/home/test/repo/2026.md';
          const item = fileEditorItemFor(path);
          const content = Array.from({length: 1400}, (_value, index) => `# Entry ${index + 1}\\n\\n- Work item ${index + 1} with enough text to produce normal Markdown editor rows.`).join('\\n');
          setFileState(path, {
            kind: 'text',
            content,
            original: content,
            dirty: false,
            language: 'markdown',
            gitRoot: '/home/test/repo',
            gitTracked: true,
            gitHasHistory: true,
            gitHistory: [{ref: 'HEAD'}],
          });
          setFileEditorViewMode(path, 'edit', item);
          registerFileEditorLayoutItem(path);
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([item, prefsItemId], item);
          applyLayoutSlots(next, {focusSession: item, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 260; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => {
            const panel = panelNodes.get(item);
            const scroller = panel?._cmView?.scrollDOM;
            return dockviewLayoutActive()
              && activeItemForSide('left') === item
              && panel?.isConnected
              && scroller
              && scroller.scrollHeight > scroller.clientHeight * 3
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === item)
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === prefsItemId);
          });
          if (!ready) {
            const panel = panelNodes.get(item);
            const scroller = panel?._cmView?.scrollDOM;
            return {
              error: 'dockview editor did not become ready',
              dockview: typeof dockviewLayoutActive === 'function' ? dockviewLayoutActive() : null,
              active: activeItemForSide('left'),
              panelExists: Boolean(panel),
              connected: Boolean(panel?.isConnected),
              hasView: Boolean(panel?._cmView),
              scrollHeight: scroller?.scrollHeight || 0,
              clientHeight: scroller?.clientHeight || 0,
              tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            };
          }
          const panel = panelNodes.get(item);
          const scroller = panel._cmView.scrollDOM;
          scroller.scrollTop = Math.min(9000, scroller.scrollHeight - scroller.clientHeight - 10);
          await frame();
          await frame();
          return {
            item,
            savedTop: scroller.scrollTop,
            preSwitchCapturedTop: fileEditorViewState.get(item)?.scrollTop || 0,
            clientHeight: scroller.clientHeight,
            scrollHeight: scroller.scrollHeight,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in setup, setup
    assert setup["savedTop"] > setup["clientHeight"], setup

    def dockview_tab(item):
        return WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                return Array.from(document.querySelectorAll('.dockview-pane-tab'))
                  .find(tab => tab.dataset.paneTab === arguments[0]) || null;
                """,
                item,
            )
        )

    ActionChains(browser).move_to_element(dockview_tab("__prefs__")).click().perform()
    after_prefs = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const item = arguments[0];
            if (activeItemForSide('left') !== prefsItemId) return null;
            const state = fileEditorViewState.get(item);
            return {
              active: activeItemForSide('left'),
              capturedTop: state?.scrollTop || 0,
              capturedSnapshot: Boolean(state?.scrollSnapshot),
              panelConnected: Boolean(panelNodes.get(item)?.isConnected),
            };
            """,
            setup["item"],
        )
    )
    assert after_prefs["capturedSnapshot"] is True, after_prefs
    assert abs(after_prefs["capturedTop"] - setup["savedTop"]) < 32, {**setup, **after_prefs}

    ActionChains(browser).move_to_element(dockview_tab(setup["item"])).click().perform()
    restored = browser.execute_async_script(
        """
        const item = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 220; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => activeItemForSide('left') === item && panelNodes.get(item)?.isConnected && panelNodes.get(item)?._cmView?.scrollDOM);
          await frame();
          await frame();
          await new Promise(resolve => setTimeout(resolve, 140));
          await frame();
          const panel = panelNodes.get(item);
          const scroller = panel?._cmView?.scrollDOM;
          return {
            ready,
            active: activeItemForSide('left'),
            restoredTop: scroller?.scrollTop || 0,
            scrollHeight: scroller?.scrollHeight || 0,
            clientHeight: scroller?.clientHeight || 0,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        setup["item"],
    )
    assert restored["ready"] is True, restored
    assert abs(restored["restoredTop"] - setup["savedTop"]) < 32, {**setup, **after_prefs, **restored}


def test_preferences_scroll_survives_dockview_tab_click_roundtrip(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof applyLayoutSlots === 'function' && typeof paneViewState !== 'undefined';"
        )
    )
    setup = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([prefsItemId, infoItemId], prefsItemId);
          applyLayoutSlots(next, {focusSession: prefsItemId, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 240; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => {
            const scroller = panelNodes.get(prefsItemId)?.querySelector('.preferences-scroll');
            return dockviewLayoutActive()
              && activeItemForSide('left') === prefsItemId
              && scroller
              && scroller.scrollHeight > scroller.clientHeight * 2
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === prefsItemId)
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === infoItemId);
          });
          if (!ready) {
            const scroller = panelNodes.get(prefsItemId)?.querySelector('.preferences-scroll');
            return {
              error: 'preferences pane did not become scrollable',
              active: activeItemForSide('left'),
              scrollHeight: scroller?.scrollHeight || 0,
              clientHeight: scroller?.clientHeight || 0,
              tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            };
          }
          const scroller = panelNodes.get(prefsItemId).querySelector('.preferences-scroll');
          scroller.scrollTop = Math.min(9000, scroller.scrollHeight - scroller.clientHeight - 10);
          await frame();
          await frame();
          return {
            item: prefsItemId,
            other: infoItemId,
            savedTop: scroller.scrollTop,
            preSwitchCapturedTop: paneViewState.get(prefsItemId)?.scrollContainers?.find(entry => entry.scrollTop > 0)?.scrollTop || 0,
            clientHeight: scroller.clientHeight,
            scrollHeight: scroller.scrollHeight,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in setup, setup
    assert setup["savedTop"] > setup["clientHeight"], setup
    assert abs(setup["preSwitchCapturedTop"] - setup["savedTop"]) < 32, setup

    def dockview_tab(item):
        return WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                return Array.from(document.querySelectorAll('.dockview-pane-tab'))
                  .find(tab => tab.dataset.paneTab === arguments[0]) || null;
                """,
                item,
            )
        )

    ActionChains(browser).move_to_element(dockview_tab(setup["other"])).click().perform()
    after_other = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            if (activeItemForSide('left') !== arguments[0]) return null;
            const state = paneViewState.get(arguments[1]);
            return {
              active: activeItemForSide('left'),
              capturedTop: state?.scrollContainers?.find(entry => entry.scrollTop > 0)?.scrollTop || 0,
            };
            """,
            setup["other"],
            setup["item"],
        )
    )
    assert abs(after_other["capturedTop"] - setup["savedTop"]) < 32, {**setup, **after_other}

    ActionChains(browser).move_to_element(dockview_tab(setup["item"])).click().perform()
    restored = browser.execute_async_script(
        """
        const item = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          for (let attempt = 0; attempt < 80; attempt += 1) {
            if (activeItemForSide('left') === item && panelNodes.get(item)?.querySelector('.preferences-scroll')) break;
            await frame();
          }
          await frame();
          await frame();
          await new Promise(resolve => setTimeout(resolve, 120));
          const scroller = panelNodes.get(item)?.querySelector('.preferences-scroll');
          return {
            active: activeItemForSide('left'),
            restoredTop: scroller?.scrollTop || 0,
            clientHeight: scroller?.clientHeight || 0,
            scrollHeight: scroller?.scrollHeight || 0,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        setup["item"],
    )
    assert restored["active"] == setup["item"], restored
    assert abs(restored["restoredTop"] - setup["savedTop"]) < 32, {**setup, **after_other, **restored}


def test_info_scroll_survives_dockview_tab_click_roundtrip(browser, tmp_path):
    load_dockview_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof applyLayoutSlots === 'function' && typeof renderInfoPanel === 'function';"
        )
    )
    setup = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          autoFocusEnabled = false;
          infoPanelSubTab = 'info';
          transcriptMetaLoaded = true;
          transcriptMetaLoading = false;
          transcriptMetaLoadError = '';
          const branches = Array.from({length: 180}, (_value, index) => ({
            name: `feature/long-info-row-${index + 1}`,
            subject: `Long YO!info row ${index + 1} that makes the branch table scroll.`,
            updated: `2026-06-${String((index % 28) + 1).padStart(2, '0')}`,
            updated_ts: 1800000000 - index,
            current: index === 0,
            linear_ids: [`YOLO-${index + 1}`],
          }));
          transcriptMeta = {
            session_order: ['1'],
            sessions: {
              '1': {
                session: '1',
                project: {
                  git: {
                    root: '/home/test/repo',
                    cwd: '/home/test/repo',
                    branch: 'feature/long-info-row-1',
                    other_branches: {branches},
                  },
                  linear: [],
                },
              },
            },
          };
          const next = emptyLayoutSlots();
          next[layoutTreeKey] = leafNode('left');
          next.left = paneStateWithTabs([infoItemId, prefsItemId], infoItemId);
          applyLayoutSlots(next, {focusSession: infoItemId, forceFull: true});
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          const waitFor = async predicate => {
            for (let attempt = 0; attempt < 260; attempt += 1) {
              if (predicate()) return true;
              await frame();
            }
            return false;
          };
          const ready = await waitFor(() => {
            const scroller = document.getElementById('info-content');
            return dockviewLayoutActive()
              && activeItemForSide('left') === infoItemId
              && scroller
              && scroller.scrollHeight > scroller.clientHeight * 2
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === infoItemId)
              && Array.from(document.querySelectorAll('.dockview-pane-tab')).some(tab => tab.dataset.paneTab === prefsItemId);
          });
          if (!ready) {
            const scroller = document.getElementById('info-content');
            return {
              error: 'info pane did not become scrollable',
              active: activeItemForSide('left'),
              scrollHeight: scroller?.scrollHeight || 0,
              clientHeight: scroller?.clientHeight || 0,
              rows: document.querySelectorAll('#info-content .info-row').length,
              tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            };
          }
          const scroller = document.getElementById('info-content');
          scroller.scrollTop = Math.min(9000, scroller.scrollHeight - scroller.clientHeight - 10);
          await frame();
          await frame();
          return {
            item: infoItemId,
            other: prefsItemId,
            savedTop: scroller.scrollTop,
            preSwitchCapturedTop: paneViewState.get(infoItemId)?.scrollContainers?.find(entry => entry.scrollTop > 0)?.scrollTop || 0,
            clientHeight: scroller.clientHeight,
            scrollHeight: scroller.scrollHeight,
            rowCount: document.querySelectorAll('#info-content .info-row').length,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in setup, setup
    assert setup["rowCount"] > 100, setup
    assert setup["savedTop"] > setup["clientHeight"], setup
    assert abs(setup["preSwitchCapturedTop"] - setup["savedTop"]) < 32, setup

    def dockview_tab(item):
        return WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script(
                """
                return Array.from(document.querySelectorAll('.dockview-pane-tab'))
                  .find(tab => tab.dataset.paneTab === arguments[0]) || null;
                """,
                item,
            )
        )

    ActionChains(browser).move_to_element(dockview_tab(setup["other"])).click().perform()
    after_other = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            if (activeItemForSide('left') !== arguments[0]) return null;
            const state = paneViewState.get(arguments[1]);
            return {
              active: activeItemForSide('left'),
              capturedTop: state?.scrollContainers?.find(entry => entry.scrollTop > 0)?.scrollTop || 0,
            };
            """,
            setup["other"],
            setup["item"],
        )
    )
    assert abs(after_other["capturedTop"] - setup["savedTop"]) < 32, {**setup, **after_other}

    ActionChains(browser).move_to_element(dockview_tab(setup["item"])).click().perform()
    restored = browser.execute_async_script(
        """
        const item = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          for (let attempt = 0; attempt < 100; attempt += 1) {
            if (activeItemForSide('left') === item && document.getElementById('info-content')) break;
            await frame();
          }
          await frame();
          await frame();
          await new Promise(resolve => setTimeout(resolve, 120));
          const scroller = document.getElementById('info-content');
          return {
            active: activeItemForSide('left'),
            restoredTop: scroller?.scrollTop || 0,
            clientHeight: scroller?.clientHeight || 0,
            scrollHeight: scroller?.scrollHeight || 0,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        setup["item"],
    )
    assert restored["active"] == setup["item"], restored
    assert abs(restored["restoredTop"] - setup["savedTop"]) < 32, {**setup, **after_other, **restored}


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
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".pane-tab")).perform()
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

    neutral = "rgba(190, 205, 218, 0.56)"
    accent = browser.execute_script(
        """
        const probe = document.createElement('div');
        probe.style.background = 'var(--pane-scrollbar-thumb-active)';
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
    browser.execute_script("document.getElementById('finder-panel')?.classList.remove('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".file-explorer-tree-panel")).perform()
    wait_thumb(".file-explorer-tree-panel", neutral)
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
    browser.execute_script("document.getElementById('finder-panel')?.classList.add('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("id", "modified-files-panel")).perform()
    wait_thumb("#modified-files-panel", accent)
    browser.execute_script("document.getElementById('finder-panel')?.classList.remove('active-pane', 'focused-pane')")
    ActionChains(browser).move_to_element(browser.find_element("id", "modified-files-panel")).perform()
    wait_thumb("#modified-files-panel", neutral)
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
    # The B-side fixture is HEAD:docs/TODO.md, so its trailing chunk byte offset changes whenever the
    # roadmap doc changes. Keep the stable A-side and row assertions pinned, and assert B-side shape.
    assert len(metrics["chunks"]) == 1
    chunk = metrics["chunks"][0]
    assert chunk["fromA"] == 744
    assert chunk["toA"] == 148290
    assert chunk["endA"] == 148289
    assert chunk["fromB"] == 744
    assert chunk["toB"] > chunk["fromB"]
    assert chunk["endB"] == chunk["toB"] - 1
    # The B-side is the LIVE HEAD:docs/TODO.md, so the add-band end, currentLineCount, totalRows, and
    # insertedRangeRows shift by one every time that roadmap doc gains/loses a line (it has, repeatedly,
    # silently breaking this test -- it was just re-pinned 308->309). Pin the stable A-side remove band
    # and assert the B-side via relationships + the actual current line count, so a TODO.md edit can't
    # break it again.
    bands = metrics["rows"]["bands"]
    assert len(bands) == 2, bands
    remove_band, add_band = bands[0], bands[1]
    deleted_rows = metrics["rows"]["deletedRows"]
    current_line_count = metrics["rows"]["currentLineCount"]
    inserted_rows = metrics["insertedRangeRows"]
    todo_text = subprocess.check_output(["git", "show", "HEAD:docs/TODO.md"], cwd=REPO_ROOT).decode()
    todo_line_count = todo_text.count("\n") + 1
    assert remove_band == {"kind": "remove", "start": 16, "end": 16 + deleted_rows}, bands
    assert add_band["kind"] == "add", bands
    assert add_band["start"] == remove_band["end"], bands
    assert add_band["end"] == add_band["start"] + inserted_rows, bands
    assert current_line_count == todo_line_count, (current_line_count, todo_line_count)
    assert metrics["rows"]["totalRows"] == deleted_rows + current_line_count
    assert deleted_rows > 0 and inserted_rows > 0
    assert metrics["deletedDomRows"] == metrics["removedRangeRows"]
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
    assert any(stop["color"] == '#ff5d6c' for stop in metrics["finalChangedStops"])
    assert any(stop["color"] == '#38d878' for stop in metrics["finalChangedStops"])
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
        const pathRow = toolbar.querySelector('.file-explorer-path-row');
        const actionsRow = toolbar.querySelector('.file-explorer-actions-row');
        const scopeRow = toolbar.querySelector('.file-explorer-scope-row');
        const collapse = primaryRow.querySelector('[data-session-files-collapse-toggle]');
        const newFile = actionsRow.querySelector('[data-file-explorer-new-file]');
        const newFolder = actionsRow.querySelector('[data-file-explorer-new-folder]');
        const actionsSpacer = actionsRow.querySelector('.file-explorer-toolbar-spacer');
        const sync = pathRow.querySelector('.file-explorer-root-mode-toggle-panel');
        const hidden = actionsRow.querySelector('.file-explorer-hidden-toggle-panel');
        const sort = actionsRow.querySelector('.file-explorer-sort-select');
        const quick = toolbar.querySelector('.file-explorer-quick-access-panel');
        const path = pathRow.querySelector('.file-explorer-path-inline');
        const copy = pathRow.querySelector('.file-explorer-path-copy-panel');
        const mode = primaryRow.querySelector('.file-explorer-mode-switcher');
        const diffSession = primaryRow.querySelector('.file-explorer-diff-session-control');
        const primarySpacer = primaryRow.querySelector('.file-explorer-toolbar-spacer');
        const modeButtons = Array.from(mode.querySelectorAll('[data-file-explorer-mode-set]'));
        const modeLabels = Array.from(mode.querySelectorAll('.file-explorer-mode-label'));
        const cluster = toolbar.querySelector('.file-explorer-date-reload-cluster');
        const date = cluster.querySelector('.file-explorer-date-toggle');
        const refresh = cluster.querySelector('.changes-refresh');
        const close = primaryRow.querySelector('.file-explorer-panel-close');
        const toolbarRect = toolbar.getBoundingClientRect();
        const primaryRowRect = primaryRow.getBoundingClientRect();
        const pathRowRect = pathRow.getBoundingClientRect();
        const actionsRowRect = actionsRow.getBoundingClientRect();
        const newFileRect = newFile.getBoundingClientRect();
        const newFolderRect = newFolder.getBoundingClientRect();
        const syncRect = sync.getBoundingClientRect();
        const hiddenRect = hidden.getBoundingClientRect();
        const sortRect = sort.getBoundingClientRect();
        const pathRect = path.getBoundingClientRect();
        const copyRect = copy.getBoundingClientRect();
        const modeRect = mode.getBoundingClientRect();
        const diffSessionRect = diffSession.getBoundingClientRect();
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
        const colorFor = value => {
          const probe = document.createElement('span');
          probe.style.color = value;
          document.body.appendChild(probe);
          const color = getComputedStyle(probe).color;
          probe.remove();
          return color;
        };
        const tabFont = getComputedStyle(document.documentElement).getPropertyValue('--tab-font').trim();
        return {
          firstRowIsPrimary: toolbar.firstElementChild === primaryRow,
          secondRowIsPath: primaryRow.nextElementSibling === pathRow,
          thirdRowIsActions: pathRow.nextElementSibling === actionsRow,
          noScopeRow: scopeRow === null,
          noQuickAccessPanel: quick === null,
          modeFirstInPrimaryRow: primaryRow.firstElementChild === mode,
          noPanelTitle: primaryRow.querySelector('.file-explorer-panel-title') === null,
          actionsOrder: actionsRow.firstElementChild === newFile && newFile.nextElementSibling === newFolder,
          folderIconPresent: newFolder.querySelector('.file-explorer-folder-icon') !== null,
          pathRowOrder: pathRow.firstElementChild === sync && sync.nextElementSibling === path && path.nextElementSibling === copy,
          hiddenBeforeSort: newFolder.nextElementSibling === actionsSpacer && actionsSpacer.nextElementSibling === hidden && hidden.nextElementSibling === sort,
          syncText: sync.textContent.trim(),
          syncPressed: sync.getAttribute('aria-pressed'),
          rootPressedCount: [sync].filter(button => button.getAttribute('aria-pressed') === 'true').length,
          diffSessionImmediatelyAfterMode: mode.nextElementSibling === diffSession,
          spacerAfterDiffSession: diffSession.nextElementSibling === primarySpacer,
          diffSessionVisibleInFilesMode: getComputedStyle(diffSession).display !== 'none',
          noTopCollapseButton: collapse === null,
          newFileLeft: newFileRect.left,
          newFileRight: newFileRect.right,
          newFolderLeft: newFolderRect.left,
          syncLeft: syncRect.left,
          syncRight: syncRect.right,
          hiddenLeft: hiddenRect.left,
          hiddenRight: hiddenRect.right,
          sortLeft: sortRect.left,
          pathRowTop: pathRowRect.top,
          pathRowBottom: pathRowRect.bottom,
          pathRowLeft: pathRowRect.left,
          pathRowRight: pathRowRect.right,
          pathRowWidth: pathRowRect.width,
          pathLeft: pathRect.left,
          pathRight: pathRect.right,
          primaryRowLeft: primaryRowRect.left,
          primaryRowRight: primaryRowRect.right,
          primaryRowWidth: primaryRowRect.width,
          diffSessionLeft: diffSessionRect.left,
          diffSessionRight: diffSessionRect.right,
          copyLeft: copyRect.left,
          copyRight: copyRect.right,
          copyWidth: copyRect.width,
          modeLeft: modeRect.left,
          modeRight: modeRect.right,
          modeWidth: modeRect.width,
          modeMaxButtonWidth: Math.max(...modeButtonRects.map(rect => rect.width)),
          modeButtonPaddingInline: Array.from(new Set(modeButtonStyles.map(style => `${style.paddingLeft}/${style.paddingRight}`))).sort(),
          modeButtonHorizontal: modeButtonRects.every(rect => rect.width > rect.height),
          modeLabelsHorizontal: modeLabels.every(label => getComputedStyle(label).writingMode === 'horizontal-tb'),
          modeUsesTabFont: modeButtonStyles.every(style => style.fontFamily === tabFont || style.fontFamily.toLowerCase().includes('narrow')),
          modeButtonTopRounded: modeButtonStyles.every(style => style.borderTopLeftRadius !== '0px' && style.borderTopRightRadius !== '0px' && style.borderBottomLeftRadius === '0px'),
          activeModeUsesPaneTabColor: getComputedStyle(mode.querySelector('[aria-pressed="true"]')).backgroundColor === colorFor('var(--pane-tab-active-bg)'),
          modeTexts: Array.from(mode.querySelectorAll('[data-file-explorer-mode-set]')).map(button => button.textContent.trim()),
          pathConsumesRemaining: pathRect.width >= pathRowRect.width - syncRect.width - copyRect.width - 36,
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
    assert metrics["secondRowIsPath"]
    assert metrics["thirdRowIsActions"]
    assert metrics["noScopeRow"]
    assert metrics["noQuickAccessPanel"]
    assert metrics["modeFirstInPrimaryRow"]
    assert metrics["noPanelTitle"]
    assert metrics["actionsOrder"]
    assert metrics["folderIconPresent"]
    assert metrics["pathRowOrder"]
    assert metrics["hiddenBeforeSort"]
    assert metrics["syncText"] == "Sync"
    assert metrics["syncPressed"] == "true"
    assert metrics["rootPressedCount"] == 1
    assert metrics["diffSessionImmediatelyAfterMode"]
    assert metrics["spacerAfterDiffSession"]
    assert metrics["diffSessionVisibleInFilesMode"]
    assert metrics["noTopCollapseButton"]
    assert metrics["newFileRight"] <= metrics["newFolderLeft"]
    assert metrics["hiddenRight"] <= metrics["sortLeft"]
    assert metrics["syncRight"] <= metrics["pathLeft"]
    assert metrics["pathLeft"] > metrics["pathRowLeft"]
    assert metrics["pathWidth"] >= min(90, metrics["toolbarWidth"] / 4)
    assert metrics["pathRight"] <= metrics["copyLeft"]
    assert metrics["copyRight"] <= metrics["pathRowRight"] + 1
    assert metrics["modeRight"] <= metrics["diffSessionLeft"]
    assert metrics["diffSessionRight"] <= metrics["closeLeft"]
    assert metrics["modeButtonHorizontal"]
    assert metrics["modeLabelsHorizontal"]
    assert metrics["modeUsesTabFont"]
    assert metrics["modeButtonTopRounded"]
    assert metrics["activeModeUsesPaneTabColor"]
    assert metrics["modeButtonPaddingInline"] == ["3px/3px"]
    assert metrics["modeMaxButtonWidth"] <= 60
    assert metrics["pathConsumesRemaining"]
    assert metrics["modeTexts"] == ["Finder", "Differ", "Tabber"]
    assert abs(metrics["closeRight"] - metrics["primaryRowRight"]) <= 1
    assert metrics["pathColor"] == metrics["textColor"]
    assert metrics["pathRowTop"] >= metrics["primaryRowBottom"]
    assert metrics["actionsRowTop"] >= metrics["pathRowBottom"]
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
    assert before["texts"] == ["Finder", "Differ", "Tabber"]
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
    assert after["texts"] == ["Finder", "Differ", "Tabber"]
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
    # working YO spins SLOWLY at the yolo_rotate_ms setting (20s), not a fast hardcoded value.
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
    # the YO!info tab label is legible in light mode (color contrasts with the tab bg,
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
        const diff = document.querySelector('#repo-dir .file-tree-diff');
        const add = document.querySelector('#repo-dir .changes-diff-add');
        const remove = document.querySelector('#repo-dir .changes-diff-remove');
        const nameRect = name.getBoundingClientRect();
        const diffRect = diff.getBoundingClientRect();
        const addRect = add.getBoundingClientRect();
        const removeRect = remove.getBoundingClientRect();
        return {
          text: name.textContent,
          hasRetiredDelta: Boolean(document.querySelector('#repo-dir .file-tree-repo-delta')),
          nameWeight: getComputedStyle(name).fontWeight,
          branchWeight: getComputedStyle(branch).fontWeight,
          diffWeight: getComputedStyle(diff).fontWeight,
          branchFont: getComputedStyle(branch).fontFamily,
          diffDisplay: getComputedStyle(diff).display,
          diffJustify: getComputedStyle(diff).justifyContent,
          addText: add.textContent,
          removeText: remove.textContent,
          addColor: getComputedStyle(add).color,
          removeColor: getComputedStyle(remove).color,
          diffRight: diffRect.right,
          addLeft: addRect.left,
          removeRight: removeRect.right,
          diffAfterName: diffRect.left >= nameRect.right,
          nameColor: getComputedStyle(name).color,
        };
        """
    )
    assert repo_row_metrics["text"] == "yolomux [feature/repo-row]"
    assert not repo_row_metrics["hasRetiredDelta"]
    assert repo_row_metrics["nameWeight"] in ("400", "normal")
    assert repo_row_metrics["branchWeight"] in ("400", "normal")
    assert repo_row_metrics["diffWeight"] == "800"
    assert "mono" in repo_row_metrics["branchFont"].lower()
    assert repo_row_metrics["diffDisplay"] == "flex"
    assert repo_row_metrics["diffJustify"] == "flex-end"
    assert repo_row_metrics["addText"] == "+5"
    assert repo_row_metrics["removeText"] == "-3"
    assert repo_row_metrics["addColor"] != repo_row_metrics["removeColor"]
    assert abs(repo_row_metrics["removeRight"] - repo_row_metrics["diffRight"]) <= 1
    assert repo_row_metrics["diffAfterName"]
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
          // the focus ring is the translucent gutter border (color-mix of --panel-ring-color).
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
    # the active pane's focus ring is the translucent gutter border; assert it shows a
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
        const leftZone = document.querySelector('.file-editor-toolbar-left').getBoundingClientRect();
        const centerZone = document.querySelector('.file-editor-toolbar-center').getBoundingClientRect();
        const rightZone = document.querySelector('.file-editor-toolbar-right').getBoundingClientRect();
        const gutter = document.getElementById('gutter-button').getBoundingClientRect();
        const diff = document.getElementById('diff-button').getBoundingClientRect();
        const expand = document.getElementById('diff-expand-button').getBoundingClientRect();
        const font = document.getElementById('font-panel').getBoundingClientRect();
        const mode = document.getElementById('mode-control').getBoundingClientRect();
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
          toolbarCenter: toolbar.left + toolbar.width / 2,
          leftZoneLeft: leftZone.left,
          leftZoneRight: leftZone.right,
          centerZoneCenter: centerZone.left + centerZone.width / 2,
          rightZoneLeft: rightZone.left,
          rightZoneRight: rightZone.right,
          gutterLeft: gutter.left,
          gutterRight: gutter.right,
          diffLeft: diff.left,
          diffRight: diff.right,
          diffText: document.getElementById('diff-button').textContent.trim(),
          expandLeft: expand.left,
          expandRight: expand.right,
          fontCenter: font.left + font.width / 2,
          modeLeft: mode.left,
          modeRight: mode.right,
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
    assert metrics["leftZoneLeft"] <= metrics["toolbarLeft"] + 8, metrics
    assert abs(metrics["centerZoneCenter"] - metrics["toolbarCenter"]) <= 2, metrics
    assert abs(metrics["fontCenter"] - metrics["toolbarCenter"]) <= 2, metrics
    assert metrics["rightZoneRight"] >= metrics["toolbarRight"] - 8, metrics
    assert metrics["modeLeft"] >= metrics["centerZoneCenter"] + 20, metrics
    assert metrics["modeLeft"] >= metrics["leftZoneRight"], metrics
    assert metrics["gutterLeft"] <= metrics["toolbarLeft"] + 8, metrics
    assert 0 <= metrics["diffLeft"] - metrics["gutterRight"] <= 6, metrics
    assert metrics["diffText"] == "Differ", metrics
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
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const cm = window.YOLOmuxCodeMirror || {};
            return typeof cm.Decoration?.mark === 'function'
              && typeof cm.Decoration?.set === 'function'
              && typeof cm.MergeView === 'function'
              && typeof cm.unifiedMergeView === 'function';
            """
        )
    )
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


# — light-mode surface regression guard. The recurring light-mode bug class is a
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
    css = app_css()
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
        # the YO!info table — rows/header/current/links must read on the light pane.
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
    css = app_css()
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
    labels = WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const panel = document.querySelector('.cm-search');
            if (!panel) return false;
            const labels = [...panel.querySelectorAll('label')].map(l => ({
              fontSize: getComputedStyle(l).fontSize,
              boxWidth: Math.round(l.getBoundingClientRect().width),
              scrollWidth: l.scrollWidth,
            }));
            return labels.length ? labels : false;
            """
        )
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
    css = app_css()
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
        assert ring.lower() == '#ff3347', f"{pid}: needs-attention pane must keep the red ring (#ff3347), got {ring}"
