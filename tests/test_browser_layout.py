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
        <div class="app-menu" data-app-menu="tab">
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
          <button id="pane-actions" class="tab pane-actions"><span id="pane-actions-dots" class="pane-actions-dots">...</span></button>
          <button id="pane-zoom" class="tab pane-expand pc-window-control pc-zoom"></button>
          <button id="hidden-pane-zoom" class="tab pane-expand pc-window-control pc-zoom" hidden></button>
        </div>
        <button type="button" class="pane-tab active">
          <span class="pane-tab-core"><span class="session-button-name">1</span></span>
          <span id="tab-minimize" class="pane-tab-close pc-window-control pc-minimize"></span>
        </button>
        <button id="finder-close" class="file-explorer-panel-close pc-window-control pc-close"></button>
        <button id="editor-close" class="file-editor-panel-close pc-window-control pc-close"></button>
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
      </body>
    </html>
    """


def editor_highlight_fallback_fixture_html():
    css = (REPO_ROOT / "static" / "yolomux.css").read_text(encoding="utf-8")
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>{css}</style>
        <style>
          body {{ margin: 0; padding: 24px; display: block; height: auto; min-height: 0; }}
          .file-editor-panel {{ position: relative; width: 420px; height: 120px; margin-bottom: 16px; }}
        </style>
      </head>
      <body>
        <div id="ready" class="file-editor-panel syntax-highlighted" data-syntax-highlight-ready="true">
          <textarea id="ready-textarea" class="file-editor-textarea-panel"># TITLE</textarea>
        </div>
        <div id="fallback" class="file-editor-panel syntax-highlighted">
          <textarea id="fallback-textarea" class="file-editor-textarea-panel"># TITLE</textarea>
        </div>
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


def load_fixture(browser, tmp_path, width):
    page = tmp_path / f"pane-{width}.html"
    page.write_text(pane_fixture_html(width), encoding="utf-8")
    browser.get(page.as_uri())
    return browser.execute_script(
        """
        const panel = document.querySelector('.panel').getBoundingClientRect();
        const toolbar = document.querySelector('.tabs').getBoundingClientRect();
        const toolbarButton = document.querySelector('.tabs .tab').getBoundingClientRect();
        const firstPaneTab = document.querySelector('.pane-tab').getBoundingClientRect();
        const detailRow = document.querySelector('.panel-detail-row').getBoundingClientRect();
        const detailClose = document.querySelector('.panel-detail-close').getBoundingClientRect();
        const detailStyle = window.getComputedStyle(document.querySelector('.panel-detail-row'));
        document.body.classList.add('tab-meta-hidden');
        const hiddenTextDisplay = window.getComputedStyle(document.querySelector('.pane-tab .session-button-text')).display;
        const hiddenSymbolDisplay = window.getComputedStyle(document.querySelector('.pane-tab .tab-symbol')).display;
        const tabs = Array.from(document.querySelectorAll('.pane-tab')).map(tab => {
          const rect = tab.getBoundingClientRect();
          return {top: Math.round(rect.top), left: rect.left, right: rect.right, width: rect.width};
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
          toolbar: {left: toolbar.left, right: toolbar.right, width: toolbar.width},
          toolbarCenterDelta: Math.abs((toolbarButton.top + toolbarButton.height / 2) - (firstPaneTab.top + firstPaneTab.height / 2)),
          detailBg: detailStyle.backgroundColor,
          detailCloseRightGap: Math.round(panel.right - detailClose.right),
          detailRow: {left: detailRow.left, right: detailRow.right},
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


def load_editor_highlight_fallback_fixture(browser, tmp_path):
    page = tmp_path / "editor-highlight-fallback.html"
    page.write_text(editor_highlight_fallback_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def load_editor_pane_legacy_body_fixture(browser, tmp_path):
    page = tmp_path / "editor-pane-legacy-body.html"
    page.write_text(editor_pane_ignores_legacy_body_class_fixture_html(), encoding="utf-8")
    browser.get(page.as_uri())


def computed_color_alpha(browser, element_id):
    return browser.execute_script(
        """
        const color = getComputedStyle(document.getElementById(arguments[0])).color;
        const match = color.match(/rgba?\\(([^)]+)\\)/);
        if (!match) return 1;
        const parts = match[1].split(',').map(part => part.trim());
        return parts.length >= 4 ? Number.parseFloat(parts[3]) : 1;
        """,
        element_id,
    )


def test_pane_tabs_use_available_space_below_toolbar(browser, tmp_path):
    metrics = load_fixture(browser, tmp_path, 860)
    assert metrics["toolbar"]["right"] <= metrics["panel"]["right"]
    assert [row["count"] for row in metrics["rows"]] == [2, 3, 1]

    first_row = metrics["rows"][0]
    assert max(first_row["rights"]) < metrics["toolbar"]["left"]
    lower_row_rights = [right for row in metrics["rows"][1:] for right in row["rights"]]
    assert max(lower_row_rights) > metrics["toolbar"]["left"]
    assert metrics["toolbarCenterDelta"] <= 2
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


def test_tab_menu_rows_are_compact_for_many_windows(browser, tmp_path):
    metrics = load_menu_fixture(browser, tmp_path)
    assert metrics["count"] == 30
    assert metrics["maxHeight"] <= 23
    assert metrics["maxStep"] <= 24
    assert metrics["firstTwentyFiveSpan"] <= 575
    assert metrics["width"] > 0
    assert metrics["width"] <= metrics["maxInlineSize"] + metrics["devicePixelRatio"]
    assert metrics["secondRowBorderTopColor"] != "rgba(0, 0, 0, 0)"
    assert metrics["scrollHeight"] <= 700


def test_platform_controls_use_pc_glyphs(browser, tmp_path):
    load_pc_controls_fixture(browser, tmp_path)
    assert browser.execute_script("return getComputedStyle(document.getElementById('hidden-pane-zoom')).display") == "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('tab-minimize'), '::after').display") == "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('finder-close'), '::after').display") != "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('editor-close'), '::after').display") != "none"
    assert browser.execute_script("return getComputedStyle(document.getElementById('pane-zoom'), '::after').display") != "none"
    assert browser.execute_script("return document.getElementById('editor-close').getBoundingClientRect().width") <= 24
    assert browser.execute_script("return getComputedStyle(document.getElementById('collapsed-preferences')).display") == "none"
    assert browser.execute_script(
        """
        const button = document.getElementById('pane-actions').getBoundingClientRect();
        const dots = document.getElementById('pane-actions-dots').getBoundingClientRect();
        return Math.abs((button.left + button.width / 2) - (dots.left + dots.width / 2));
        """
    ) <= 1

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
    assert tree_metrics["iconSize"] <= tree_metrics["nameSize"]
    assert tree_metrics["iconSize"] >= tree_metrics["nameSize"] * 0.85


def test_editor_text_stays_visible_until_highlight_overlay_is_ready(browser, tmp_path):
    load_editor_highlight_fallback_fixture(browser, tmp_path)
    assert computed_color_alpha(browser, "ready-textarea") == 0
    assert computed_color_alpha(browser, "fallback-textarea") == 1


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
