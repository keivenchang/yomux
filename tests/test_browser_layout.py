from pathlib import Path
import json
import re
import shutil

import pytest


pytest.importorskip("selenium")
webdriver = pytest.importorskip("selenium.webdriver")
ActionChains = pytest.importorskip("selenium.webdriver.common.action_chains").ActionChains
Options = pytest.importorskip("selenium.webdriver.chrome.options").Options
WebDriverWait = pytest.importorskip("selenium.webdriver.support.ui").WebDriverWait


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def browser():
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
                <button type="button" class="file-editor-cross-split-panel"><span class="file-editor-icon file-editor-icon-side-split"></span></button>
              </div>
              <button type="button" class="file-editor-wrap-panel active"><span class="file-editor-icon file-editor-icon-wrap"></span></button>
              <button type="button" class="file-editor-find-panel"><span class="file-editor-icon file-editor-icon-find"></span></button>
              <button type="button" class="file-editor-theme-panel theme-light" data-editor-theme="light"><span class="file-editor-icon file-editor-icon-theme"></span></button>
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


def finder_click_toolbar_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <style>
          body {{ margin: 0; padding: 8px; display: grid; grid-template-columns: 260px 1fr; gap: 8px; height: auto; min-height: 0; }}
          .panel {{ height: 170px; }}
        </style>
      </head>
      <body>
        <article id="finder-panel" class="panel file-explorer-panel active-pane">
          <div class="panel-head file-explorer-head">
            <div class="pane-tabs" hidden></div>
            <div class="file-explorer-toolbar">
              <input class="file-explorer-path-inline" value="/home/keivenc/yolomux.dev/static_src/js/yolomux">
              <button type="button" class="path-copy-button file-explorer-path-copy-panel"></button>
              <button type="button" class="file-explorer-hidden-toggle file-explorer-hidden-toggle-panel">.*</button>
              <button type="button" class="file-explorer-root-mode-toggle file-explorer-root-mode-toggle-panel">根目錄</button>
              <button type="button" class="file-explorer-header-action">+</button>
              <button type="button" class="file-explorer-header-action">▣</button>
              <button type="button" class="file-explorer-header-action">↻</button>
              <button type="button" class="file-explorer-header-action">▤</button>
              <button type="button" class="file-explorer-header-action file-explorer-date-toggle">日期</button>
              <button type="button" class="file-explorer-header-action file-explorer-changes-toggle">Δ</button>
              <select class="file-explorer-sort-select"><option>A-Z</option></select>
              <div class="tabs pane-frame-controls file-explorer-frame-controls">
                <button type="button" class="tab pane-close pc-window-control pc-close file-explorer-panel-close"></button>
              </div>
            </div>
          </div>
          <div class="file-explorer-pane">
            <div class="file-explorer-tree-panel" tabindex="0"></div>
            <div id="modified-files-panel" class="file-explorer-changes-panel" tabindex="0">
              <div id="modified-files-head" class="file-explorer-changes-head">
                <span class="changes-title">Differ: '5'</span>
                <button type="button" class="changes-refresh">Refresh</button>
                <button type="button" class="changes-close">x</button>
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


def live_runtime_boot_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    script_uri = (REPO_ROOT / "static" / "yolomux.js").as_uri()
    bootstrap = {
        "sessions": ["1"],
        "availableAgents": ["term"],
        "accessRole": "admin",
        "homePath": "/home/test",
        "repoRoot": "/home/test/yolomux.dev",
        "maxSessionTabs": 9,
        "serverHostname": "localhost",
        "version": "test",
        "versionCommitTime": "test",
        "settingsPayload": {
            "settings": {},
            "defaults": {},
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
      window.fetch = async (input, options = {}) => {
        const url = new URL(String(input), 'https://localhost');
        window.__bootFetches.push({path: url.pathname, method: options.method || 'GET'});
        if (url.pathname === '/api/notify') return jsonResponse({enabled: false});
        if (url.pathname === '/api/ensure-session') return jsonResponse({ok: true, created: false});
        if (url.pathname === '/api/auto-approve') {
          return jsonResponse({
            session_order: ['1'],
            sessions: {'1': {target: '1', enabled: false, last_action: 'off'}},
            rules: {path: '/home/test/.config/yolomux/yolo-rules.yaml', source: 'default', rules: [], errors: []},
          });
        }
        if (url.pathname === '/api/transcripts') {
          return jsonResponse({
            session_order: ['1'],
            sessions: {
              '1': {
                session: '1',
                selected_pane: {current_path: '/home/test/yolomux.dev'},
                project: {git: {root: '/home/test/yolomux.dev', branch: 'main'}},
                agents: [],
              },
            },
          });
        }
        if (url.pathname === '/api/activity-summary') return jsonResponse({sessions: {}, global: {lines: []}, session_order: ['1']});
        if (url.pathname === '/api/session-files') return jsonResponse({session: '1', files: [], repos: [], errors: [], loaded: true});
        if (url.pathname === '/api/ping') return jsonResponse({ok: true});
        if (url.pathname === '/api/event') return jsonResponse({ok: true});
        if (url.pathname === '/api/events') return jsonResponse({events: []});
        if (url.pathname === '/api/fs/list') return jsonResponse({path: '/home/test', entries: []});
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
              <button type="button" id="fileExplorerHiddenToggle" class="file-explorer-hidden-toggle">.*</button>
              <button type="button" id="fileExplorerRootMode" class="file-explorer-root-mode-toggle">Root</button>
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


def load_finder_click_toolbar_fixture(browser, tmp_path):
    page = tmp_path / "finder-click-toolbar.html"
    page.write_text(finder_click_toolbar_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_split_seam_fixture(browser, tmp_path):
    page = tmp_path / "split-seam.html"
    page.write_text(split_seam_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_live_runtime_boot_fixture(browser, tmp_path):
    page = tmp_path / "live-runtime-boot.html"
    page.write_text(live_runtime_boot_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


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


def test_pane_tabs_use_available_space_below_toolbar(browser, tmp_path):
    metrics = load_fixture(browser, tmp_path, 860)
    assert metrics["toolbar"]["right"] <= metrics["panel"]["right"]
    assert [row["count"] for row in metrics["rows"]] == [3, 3]

    first_row = metrics["rows"][0]
    assert max(first_row["rights"]) < metrics["toolbar"]["left"]
    lower_row_rights = [right for row in metrics["rows"][1:] for right in row["rights"]]
    assert max(lower_row_rights) <= metrics["panel"]["right"]
    assert metrics["toolbarCenterDelta"] <= 2
    assert metrics["tabHeadBottomGap"] <= 2
    assert metrics["detailRow"]["height"] <= 20
    assert metrics["hiddenTextDisplay"] != "none"
    assert metrics["hiddenSymbolDisplay"] == "none"
    assert metrics["detailBg"] != "rgb(18, 24, 35)"
    assert metrics["detailCloseRightGap"] <= 3
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
    assert theme_metrics["dark"]["panelHeadBg"] == "rgb(31, 48, 38)"
    assert theme_metrics["light"]["panelHeadBg"] == "rgb(220, 232, 210)"
    # Shared pane-chrome buttons (image 009): every UNPRESSED control is white (light) / near-black (dark)
    # via --pane-ctl-bg — including the expand "+" (formerly always-green). Only PRESSED/ACTIVE buttons go
    # green (asserted via toolbarActiveBg below). No per-button one-off colors.
    assert theme_metrics["dark"]["paneControlBg"] == "rgb(27, 36, 50)"
    assert theme_metrics["light"]["paneControlBg"] == "rgb(247, 249, 252)"
    assert theme_metrics["dark"]["zoomControlBg"] == "rgb(27, 36, 50)"      # "+" is NOT green when unpressed
    assert theme_metrics["light"]["zoomControlBg"] == "rgb(247, 249, 252)"
    assert theme_metrics["dark"]["zoomControlBg"] == theme_metrics["dark"]["paneControlBg"]  # all unpressed controls share one bg
    # The active control tab (the agent/"claude" pill) is PRESSED -> green, in both themes (shared rule).
    assert theme_metrics["dark"]["toolbarActiveBg"] == "rgb(134, 214, 0)"
    assert theme_metrics["light"]["toolbarActiveBg"] == "rgb(79, 158, 58)"
    # DOIT.6 #31: the active-tab greens are tuned PER THEME so a theme switch visibly repaints the active
    # pane tab; the frame controls are also theme-specific now (image 043). Every OTHER surface stays
    # token-equal across themes.
    # inactiveTabBg is theme-specific now (images 003/004): light gets a very-light-green #e6f1dd while
    # dark keeps #285a2f, so it must NOT be required equal across themes.
    # toolbarActiveBg/Border are the PRESSED control tab's green, which is theme-specific (light #4f9e3a /
    # dark #86d600); detail-row bg now follows --pane-bar-bg so it is theme-specific too.
    theme_specific = {"panelHeadBg", "activeTabBg", "activeTabColor", "inactiveActiveTabBg", "inactiveActiveTabColor", "inactiveTabBg", "inactiveDirColor", "paneControlBg", "paneControlBorder", "zoomControlBg", "toolbarActiveBg", "toolbarActiveBorder"}
    for key, value in theme_metrics["dark"].items():
        if key not in theme_specific:
            assert theme_metrics["light"][key] == value
    assert theme_metrics["dark"]["activeTabBg"] == "rgb(134, 214, 0)"
    assert theme_metrics["light"]["activeTabBg"] == "rgb(79, 158, 58)"
    assert theme_metrics["light"]["activeTabBg"] != theme_metrics["dark"]["activeTabBg"]
    assert theme_metrics["light"]["inactiveActiveTabBg"] != theme_metrics["dark"]["inactiveActiveTabBg"]
    # Active-tab text stays legible against its (theme-specific) green in light mode.
    assert theme_metrics["light"]["activeTabColor"] != theme_metrics["light"]["activeTabBg"]
    assert theme_metrics["dark"]["activeTabShadow"] == "none"
    # images 003/004: an unfocused pane's active tab now uses the SAME full green as the focused pane's
    # active tab (no lightening) — the unfocused-active tokens are aliased to the focused ones.
    assert theme_metrics["dark"]["inactiveActiveTabBg"] == "rgb(134, 214, 0)"
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


def test_pane_tabs_and_controls_stay_bounded_when_narrow(browser, tmp_path):
    metrics = load_fixture(browser, tmp_path, 493)
    assert metrics["toolbar"]["right"] <= metrics["panel"]["right"]
    assert [row["count"] for row in metrics["rows"]] == [1, 2, 2, 1]
    assert all(tab["right"] <= metrics["panel"]["right"] for tab in metrics["tabs"])
    assert metrics["toolbarCenterDelta"] <= 2
    assert metrics["tabHeadBottomGap"] <= 2


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


def test_topbar_finder_and_modified_files_headers_hover_green_in_light_mode(browser, tmp_path):
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
              neutral: tokenColor('--panel2'),
              green: tokenColor('--pane-tab-strip-bg'),
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
    wait_background("#topbar-fixture", tokens["green"])
    ActionChains(browser).move_to_element(browser.find_element("css selector", ".pane-tab")).perform()
    wait_background("#topbar-fixture", tokens["neutral"])

    load_finder_click_toolbar_fixture(browser, tmp_path)
    tokens = theme_tokens()
    wait_background("#finder-panel .file-explorer-head", tokens["neutral"])
    wait_background("#modified-files-head", tokens["neutral"])
    ActionChains(browser).move_to_element(browser.find_element("css selector", "#finder-panel .file-explorer-head")).perform()
    wait_background("#finder-panel .file-explorer-head", tokens["green"])
    wait_background("#modified-files-head", tokens["neutral"])
    ActionChains(browser).move_to_element(browser.find_element("id", "terminal-panel")).perform()
    wait_background("#finder-panel .file-explorer-head", tokens["neutral"])
    wait_background("#modified-files-head", tokens["neutral"])
    ActionChains(browser).move_to_element(browser.find_element("id", "modified-files-panel")).perform()
    wait_background("#finder-panel .file-explorer-head", tokens["neutral"])
    wait_background("#modified-files-head", tokens["green"])


def test_finder_path_is_first_and_readable_in_wrapped_toolbar(browser, tmp_path):
    load_finder_click_toolbar_fixture(browser, tmp_path)
    metrics = browser.execute_script(
        """
        const toolbar = document.querySelector('#finder-panel .file-explorer-toolbar');
        const path = toolbar.querySelector('.file-explorer-path-inline');
        const toolbarRect = toolbar.getBoundingClientRect();
        const pathRect = path.getBoundingClientRect();
        const textProbe = document.createElement('span');
        textProbe.style.color = 'var(--text)';
        document.body.appendChild(textProbe);
        const textColor = getComputedStyle(textProbe).color;
        textProbe.remove();
        return {
          firstIsPath: toolbar.firstElementChild === path,
          pathLeft: pathRect.left,
          toolbarLeft: toolbarRect.left,
          pathWidth: pathRect.width,
          toolbarWidth: toolbarRect.width,
          pathColor: getComputedStyle(path).color,
          textColor,
        };
        """
    )
    assert metrics["firstIsPath"]
    assert metrics["pathLeft"] <= metrics["toolbarLeft"] + 1
    assert metrics["pathWidth"] >= min(220, metrics["toolbarWidth"] - 1)
    assert metrics["pathColor"] == metrics["textColor"]


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
        const collapsed = getComputedStyle(document.querySelector('#collapsed-dir > .file-tree-icon'));
        const expanded = getComputedStyle(document.querySelector('#expanded-dir > .file-tree-icon'));
        return {
          collapsedSize: Number.parseFloat(collapsed.fontSize),
          expandedSize: Number.parseFloat(expanded.fontSize),
          collapsedWidth: document.querySelector('#collapsed-dir > .file-tree-icon').getBoundingClientRect().width,
          expandedColor: expanded.color,
          collapsedColor: collapsed.color,
        };
        """
    )
    assert triangle_sizes["collapsedSize"] >= 16
    assert triangle_sizes["expandedSize"] >= 16
    assert triangle_sizes["collapsedWidth"] >= 20
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
        const modeIconDeltas = Array.from(document.querySelectorAll('.file-editor-mode-control button')).map(button => {
          const buttonRect = button.getBoundingClientRect();
          const iconRect = button.querySelector('.file-editor-icon').getBoundingClientRect();
          return Math.abs((buttonRect.top + buttonRect.height / 2) - (iconRect.top + iconRect.height / 2));
        });
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
    assert metrics["themeBg"] == "rgba(0, 0, 0, 0)"
    assert metrics["wrapBg"] not in ("rgb(255, 255, 255)", "rgb(221, 244, 255)")
    assert metrics["findBg"] == "rgba(0, 0, 0, 0)"
    assert metrics["previewBg"] == "rgba(0, 0, 0, 0)"
    assert metrics["closeBg"] != metrics["editorBg"]
    assert metrics["closeBg"] != "rgb(255, 235, 233)"
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
    assert light_metrics["detailBg"] == "rgb(220, 232, 210)"
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
