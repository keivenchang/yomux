from pathlib import Path
import shutil

import pytest


pytest.importorskip("selenium")
webdriver = pytest.importorskip("selenium.webdriver")
ActionChains = pytest.importorskip("selenium.webdriver.common.action_chains").ActionChains
Options = pytest.importorskip("selenium.webdriver.chrome.options").Options


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
              <button class="tab panel-detail-toggle active">Info</button>
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
                  <span class="ci-indicator pr-indicator">PR</span>
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
        <article id="finder-panel" class="panel file-explorer-panel">
          <div class="panel-head file-explorer-head">
            <div class="pane-tabs" hidden></div>
            <div class="file-explorer-toolbar">
              <button type="button" class="file-explorer-hidden-toggle file-explorer-hidden-toggle-panel">.*</button>
              <input class="file-explorer-path-inline" value="/home/test">
              <div class="tabs pane-frame-controls file-explorer-frame-controls">
                <button type="button" class="tab pane-close pc-window-control pc-close file-explorer-panel-close"></button>
              </div>
            </div>
          </div>
          <div class="file-explorer-pane"></div>
        </article>
        <article id="terminal-panel" class="panel active-pane">
          <div class="panel-head">
            <div id="terminal-toolbar" class="tabs" role="tablist">
              <button class="tab active terminal-tab">1</button>
              <button class="tab">Tx</button>
              <button class="tab">AI</button>
              <button class="tab">Log</button>
              <button class="tab panel-detail-toggle active">Info</button>
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


def test_pane_tabs_use_available_space_below_toolbar(browser, tmp_path):
    metrics = load_fixture(browser, tmp_path, 860)
    assert metrics["toolbar"]["right"] <= metrics["panel"]["right"]
    assert [row["count"] for row in metrics["rows"]] == [2, 3, 1]

    first_row = metrics["rows"][0]
    assert max(first_row["rights"]) < metrics["toolbar"]["left"]
    lower_row_rights = [right for row in metrics["rows"][1:] for right in row["rights"]]
    assert max(lower_row_rights) > metrics["toolbar"]["left"]
    assert metrics["toolbarCenterDelta"] <= 2
    assert metrics["tabHeadBottomGap"] <= 2
    assert metrics["detailRow"]["height"] <= 20
    assert metrics["hiddenTextDisplay"] != "none"
    assert metrics["hiddenSymbolDisplay"] == "none"
    assert metrics["detailBg"] != "rgb(18, 24, 35)"
    assert metrics["detailCloseRightGap"] <= 3


def test_pane_tabs_and_controls_stay_bounded_when_narrow(browser, tmp_path):
    metrics = load_fixture(browser, tmp_path, 493)
    assert metrics["toolbar"]["right"] <= metrics["panel"]["right"]
    assert [row["count"] for row in metrics["rows"]] == [1, 1, 1, 1, 1, 1]
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
    assert dots_center_delta["dotsColor"] != dots_center_delta["hashColor"]
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
        const panelRing = getComputedStyle(document.querySelector('.file-editor-panel'), '::after');
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
          panelRingWidth: Number.parseFloat(panelRing.borderTopWidth),
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
    assert metrics["panelRingWidth"] >= 3
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
    assert metrics["filePopoverPointerEvents"] == "none"
    assert metrics["filePopoverCopyPointerEvents"] == "auto"
    assert metrics["findControlClickable"]
    assert metrics["wrapControlClickable"]
    assert metrics["previewControlClickable"]


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
