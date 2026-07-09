from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from tests.browser_helpers.browser_layout import _reset_browser_state  # noqa: F401

def test_share_viewer_banner_does_not_displace_main_grid(browser, tmp_path):
    page = tmp_path / "share-viewer-banner-grid.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
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
    )
    metrics = browser.execute_script(
        """
        const root = document.getElementById('appRoot');
        const banner = document.querySelector('.share-viewer-banner');
        const grid = document.getElementById('grid');
        const dock = document.querySelector('.yolomux-dockview');
        const {rect} = window.__yolomuxTestHelpers;
        return {
          root: rect(root, {round: true}),
          banner: rect(banner, {round: true}),
          bannerPosition: getComputedStyle(banner).position,
          grid: rect(grid, {round: true}),
          dock: rect(dock, {round: true}),
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
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
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
    )
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
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(
            """
        <button class="share-status-pill share-mode-read">YO!share: 1 viewers · 9:23 left</button>
        <button class="share-status-pill share-mode-write">YO!share: 1 viewers · 9:23 left</button>
        <div class="share-viewer-banner share-mode-read">Viewing keivenc's session - read-only - expires in 9:23</div>
        <div class="share-viewer-banner share-mode-write">Viewing keivenc's session - write - expires in 9:23</div>
      """
        ),
    )
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
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(
            """
        <section id="modal" class="modal app-modal-overlay open share-open">
          <div class="modal-dialog">
          <div class="modal-head"><div id="modalTitle">YO!share</div><button id="closeModal">Close</button></div>
          <div id="modalBody">
            <section class="share-entry">
              <label class="share-field share-url-field share-url-primary">
                <span class="share-url-primary-head"><span>URL</span></span>
                <span class="share-url-control">
                  <input type="text" readonly value="https://share.example.test/share/share123">
                  <button type="button" class="path-copy-button share-url-copy-button" data-share-copy title="Copy" aria-label="Copy"></button>
                </span>
              </label>
              <div class="share-actions">
                <button type="button" class="danger" data-share-stop>Stop</button>
              </div>
            </section>
          </div>
          </div>
        </section>
      """
        ),
    )
    metrics = browser.execute_script(
        """
        const control = document.querySelector('.share-url-control');
        const input = control.querySelector('input');
        const copy = control.querySelector('[data-share-copy]');
        const primary = document.querySelector('.share-url-primary');
        const primaryStyle = getComputedStyle(primary);
        const actions = document.querySelector('.share-actions');
        const {rect} = window.__yolomuxTestHelpers;
        return {
          primary: rect(primary, {round: true}),
          primaryBorder: primaryStyle.borderTopColor,
          primaryBackground: primaryStyle.backgroundColor,
          control: rect(control, {round: true}),
          input: rect(input, {round: true}),
          copy: rect(copy, {round: true}),
          copyCount: document.querySelectorAll('[data-share-copy]').length,
          copyText: copy.textContent.trim(),
          copyTitle: copy.getAttribute('title'),
          actionCopyCount: actions.querySelectorAll('[data-share-copy]').length,
          stopCount: actions.querySelectorAll('[data-share-stop]').length,
        };
        """
    )
    assert metrics["copyText"] == ""
    assert metrics["copyTitle"] == "Copy"
    assert metrics["copyCount"] == 1
    assert metrics["actionCopyCount"] == 0
    assert metrics["stopCount"] == 1
    assert metrics["primary"]["top"] <= metrics["control"]["top"]
    assert metrics["primary"]["bottom"] >= metrics["control"]["bottom"]
    assert metrics["primaryBorder"] != metrics["primaryBackground"]
    assert metrics["input"]["right"] <= metrics["copy"]["left"]
    assert metrics["copy"]["right"] <= metrics["control"]["right"]
    assert metrics["copy"]["width"] == 32
    assert abs(((metrics["input"]["top"] + metrics["input"]["bottom"]) / 2) - ((metrics["copy"]["top"] + metrics["copy"]["bottom"]) / 2)) <= 1


def test_share_modal_users_section_is_inline_not_nested_card(browser, tmp_path):
    page = tmp_path / "share-modal-users-inline.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(
            """
        <section id="modal" class="modal app-modal-overlay open share-open">
          <div class="modal-dialog">
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
                        <input type="text" readonly value="http://share.example.test/share/UKeiu2G-#t=ZD5">
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
          </div>
        </section>
      """
        ),
    )
    metrics = browser.execute_script(
        """
        document.documentElement.style.setProperty('--pane-split-gap', '6px');
        document.documentElement.style.setProperty('--pane-active-ring-opacity', '100%');
        const parseCssColorRgb = color => {
          const srgb = color.match(/^color\\(srgb\\s+([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)/);
          if (srgb) {
            return srgb.slice(1, 4).map(value => Math.round(Number(value) * 255));
          }
          const rgb = color.match(/^rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
          return rgb ? rgb.slice(1, 4).map(value => Number(value)) : [];
        };
        const styles = selector => {
          const style = getComputedStyle(document.querySelector(selector));
          return {
            background: style.backgroundColor,
            borderTopColor: style.borderTopColor,
            borderTopRgb: parseCssColorRgb(style.borderTopColor),
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
        const resultTopRows = Array.from(new Set(Array.from(document.querySelectorAll('.share-result-meta > *')).map(node => Math.round(node.getBoundingClientRect().top))));
        return {
          modal: styles('.modal.share-open .modal-dialog'),
          entry: styles('.share-entry'),
          table: styles('.share-users-table'),
          header: styles('.share-users-row.header'),
          empty: styles('.share-users-empty'),
          dialogRect: rect('.modal-dialog'),
          usersRect: rect('.share-users'),
          tableRect: rect('.share-users-table'),
          resultTopRows,
        };
        """
    )
    assert metrics["modal"]["borderTopWidth"] == "6px"
    assert metrics["modal"]["borderLeftWidth"] == "6px"
    assert metrics["modal"]["borderTopRgb"] == [118, 185, 0], metrics["modal"]["borderTopColor"]
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
    assert metrics["dialogRect"]["width"] >= 900
    assert len(metrics["resultTopRows"]) == 1, metrics
    assert abs(metrics["usersRect"]["left"] - metrics["tableRect"]["left"]) <= 1
    assert abs(metrics["usersRect"]["right"] - metrics["tableRect"]["right"]) <= 1


def test_share_modal_create_controls_stay_on_one_line_when_wide(browser, tmp_path):
    page = tmp_path / "share-modal-create-one-line.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(
            """
        <section id="modal" class="modal app-modal-overlay open share-open">
          <div class="modal-dialog">
            <div class="modal-head"><div id="modalTitle">YO!share</div><button id="closeModal">Close</button></div>
            <div id="modalBody">
              <form class="share-modal-form" id="shareCreateForm">
                <div class="share-create-panel">
                  <div class="share-section-title">New share</div>
                  <div class="share-create-controls">
                    <label class="share-field"><span>Max time to share</span><span class="share-duration-control"><input name="ttl_minutes" type="number" value="10"><span class="share-duration-unit">min</span></span></label>
                    <label class="share-field"><span>Max viewers</span><input name="max_viewers" type="number" value="2"></label>
                    <label class="share-checkbox"><input name="read_only" type="checkbox" checked><span>Read-only</span></label>
                    <label class="share-checkbox"><input name="debug_profile" type="checkbox"><span>Debug/profiling upload</span></label>
                  </div>
                  <fieldset class="share-protocol-group">
                    <legend>Protocol</legend>
                    <label><input name="scheme" type="radio" value="http" checked> http</label>
                    <label><input name="scheme" type="radio" value="https"> https</label>
                    <div class="share-hint" data-share-protocol-hint>http is unencrypted - read-only and short-lived only.</div>
                  </fieldset>
                  <div class="share-security-note">http is unencrypted; write access lets viewers run commands as you.</div>
                  <div class="share-actions"><button type="submit">Share</button></div>
                </div>
              </form>
            </div>
          </div>
        </section>
      """
        ),
    )
    metrics = browser.execute_script(
        """
        const topRows = selector => Array.from(new Set(Array.from(document.querySelectorAll(selector)).map(node => Math.round(node.getBoundingClientRect().top))));
        const bottomRows = selector => Array.from(new Set(Array.from(document.querySelectorAll(selector)).map(node => Math.round(node.getBoundingClientRect().bottom))));
        const dialog = document.querySelector('.modal-dialog').getBoundingClientRect();
        const controls = document.querySelector('.share-create-controls').getBoundingClientRect();
        const protocol = document.querySelector('.share-protocol-group').getBoundingClientRect();
        return {
          dialogWidth: Math.round(dialog.width),
          dialogCenterDeltaX: Math.abs((dialog.left + dialog.width / 2) - (window.innerWidth / 2)),
          dialogCenterDeltaY: Math.abs((dialog.top + dialog.height / 2) - (window.innerHeight / 2)),
          controlBottomRows: bottomRows('.share-create-controls > *'),
          protocolRows: topRows('.share-protocol-group > label, .share-protocol-group > .share-hint'),
          controlsRight: Math.round(controls.right),
          protocolRight: Math.round(protocol.right),
          viewportWidth: window.innerWidth,
        };
        """
    )
    assert metrics["dialogWidth"] >= 900, metrics
    assert metrics["dialogCenterDeltaX"] <= 1, metrics
    assert metrics["dialogCenterDeltaY"] <= 1, metrics
    assert len(metrics["controlBottomRows"]) == 1, metrics
    assert max(metrics["protocolRows"]) - min(metrics["protocolRows"]) <= 2, metrics
    assert metrics["controlsRight"] <= metrics["viewportWidth"], metrics
    assert metrics["protocolRight"] <= metrics["viewportWidth"], metrics


def test_share_modal_long_active_list_scrolls_inside_dialog(browser, tmp_path):
    entries = "\n".join(
        f"""
        <section class="share-entry share-mode-read">
          <div class="share-entry-heading"><strong>{index} · read-only · http</strong><span>id share{index}</span></div>
          <label class="share-field share-url-field">
            <span class="share-url-control"><input type="text" readonly value="http://share.example.test/share/share{index}"><button type="button" class="path-copy-button share-url-copy-button" title="Copy" aria-label="Copy"></button></span>
          </label>
          <div class="share-result-meta"><span>expires in 99:{index:02d}</span><span>0/2 viewers</span><span>read-only</span><span>http</span><button type="button" class="share-extend-button">+10 min</button><button type="button" class="danger share-stop-inline">Stop sharing</button></div>
        </section>
        """
        for index in range(24)
    )
    page = tmp_path / "share-modal-scroll.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        page_html(
            f"""
        <section id="modal" class="modal app-modal-overlay open share-open">
          <div class="modal-dialog">
            <div class="modal-head"><div id="modalTitle">YO!share</div><button id="closeModal">Close</button></div>
            <div id="modalBody">
              <div class="share-modal-form">
                <div class="share-create-panel">
                  <div class="share-section-title">New share</div>
                  <label class="share-field"><span>Max viewers</span><input name="max_viewers" type="number" value="2"></label>
                  <div class="share-actions"><button type="button">Create</button></div>
                </div>
                <div class="share-active-panel">
                  <div class="share-section-title">Active share URLs (24)</div>
                  <div class="share-entry-list">{entries}</div>
                </div>
              </div>
            </div>
          </div>
        </section>
      """
        ),
    )
    metrics = browser.execute_script(
        """
        const dialog = document.querySelector('.modal-dialog');
        const body = document.getElementById('modalBody');
        const create = document.querySelector('.share-create-panel');
        const beforeScrollTop = body.scrollTop;
        const createTopBefore = Math.round(create.getBoundingClientRect().top);
        body.scrollTop = body.scrollHeight;
        const dialogRect = dialog.getBoundingClientRect();
        const bodyStyle = getComputedStyle(body);
        return {
          bodyOverflowY: bodyStyle.overflowY,
          bodyClientHeight: body.clientHeight,
          bodyScrollHeight: body.scrollHeight,
          bodyScrolled: body.scrollTop > beforeScrollTop,
          dialogBottom: Math.round(dialogRect.bottom),
          viewportHeight: window.innerHeight,
          createTopBefore,
        };
        """
    )
    assert metrics["bodyOverflowY"] in {"auto", "scroll"}, metrics
    assert metrics["bodyScrollHeight"] > metrics["bodyClientHeight"], metrics
    assert metrics["bodyScrolled"] is True, metrics
    assert metrics["dialogBottom"] <= metrics["viewportHeight"], metrics
    assert metrics["createTopBefore"] > 0, metrics


def test_share_readonly_diff_scroll_and_popup_mirror_are_host_owned(browser, tmp_path):
    css = app_css()
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    strings = dict(app_english_strings())
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
              const {{frame}} = window.__yolomuxTestHelpers;
              const waitFor = window.__yolomuxTestWaitFor;
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
              const yoagentPanel = createYoagentPanel();
              document.getElementById('mount').append(yoagentPanel);
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
              const modeReady = await waitFor(
                () => panel._cmMode === 'diff' && panel._cmView?.scrollDOM,
                {{timeoutMs: 10000, description: 'shared editor diff mode'}}
              );
              const scroller = panel._cmView?.scrollDOM;
              applyShareScrollState({{target: `editor:${{item}}:editor`, kind: 'editor', path, item, source: 'editor', top: 2320, left: 0, anchor: 0, head: 0}});
              await frame();
              await frame();
              const diffRowsVisible = await waitFor(
                () => panel.querySelectorAll('.cm-changedLine').length > 0
                  && panel.querySelectorAll('.cm-deletedLine, .cm-deletedChunk').length > 0,
                {{timeoutMs: 10000, description: 'shared editor diff rows'}}
              );
              const hostTop = Math.round(scroller?.scrollTop || 0);
              // The product disables intra-line token highlights, so assert the stable full-line/chunk
              // classes rather than the transient cm-insertedLine token mark.
              const insertedRows = panel.querySelectorAll('.cm-changedLine').length;
              const deletedRows = panel.querySelectorAll('.cm-deletedLine, .cm-deletedChunk').length;
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
                legacyInfoSubtab: infoPanelSubTab || '',
                infoHasYoagentSubview: Boolean(infoPanel.querySelector('[data-info-subview="yoagent"], #yoagent-content')),
                yoagentPanelPresent: yoagentPanel.classList.contains('yoagent-panel') && Boolean(yoagentPanel.querySelector('#yoagent-content')),
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
    browser.get(page.as_uri() + "?shareReplay=0")
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
    assert metrics["legacyInfoSubtab"] == "yoagent", metrics
    assert metrics["infoHasYoagentSubview"] is False, metrics
    assert metrics["yoagentPanelPresent"] is True, metrics
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
    strings = dict(app_english_strings())
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
        #info-content .info-tree {{ min-width: 1120px; }}
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
              const {{frame}} = window.__yolomuxTestHelpers;
              infoPanelSubTab = 'info';
              transcriptMetadataState.loaded = true;
              transcriptMetadataState.loading = false;
              transcriptMetadataState.error = '';
              transcriptMetadataState.payload = {{
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
              applyShareUiState({{info: {{grouping: ['path'], sort: {{key: 'name', dir: 'asc'}}, branchRows: hostBranchRows}}}});
              await frame();
              const scroller = document.getElementById('info-content');
              const rowPaths = () => Array.from(document.querySelectorAll('#info-content .info-tree-group[data-info-dimension="path"] .info-tree-group-label')).map(cell => cell.textContent.trim());
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
                rows: rowPaths(),
                hostTop,
                hostLeft,
                afterLocalTop: Math.round(scroller.scrollTop),
                afterLocalLeft: Math.round(scroller.scrollLeft),
                scrollWidth: scroller.scrollWidth,
                clientWidth: scroller.clientWidth,
                errors: window.__shareInfoErrors,
              }};
            }})();
          </script>
        </body></html>""",
        encoding="utf-8",
    )
    browser.get(page.as_uri() + "?shareReplay=0")
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        window.__shareInfoReady.then(done, error => done({error: String(error), errors: window.__shareInfoErrors || []}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rows"][:2] == ["/repo/host-one", "/repo/host-two"], metrics
    assert metrics["scrollWidth"] > metrics["clientWidth"], metrics
    assert metrics["hostTop"] > 0, metrics
    assert metrics["hostLeft"] > 0, metrics
    assert metrics["afterLocalTop"] == metrics["hostTop"], metrics
    assert metrics["afterLocalLeft"] == metrics["hostLeft"], metrics


def test_share_readonly_finder_session_is_host_authoritative(browser, tmp_path):
    css = app_css()
    bundle_uri = (REPO_ROOT / "static" / "codemirror.js").as_uri()
    strings = dict(app_english_strings())
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
              const {{frame}} = window.__yolomuxTestHelpers;
              transcriptMetadataState.loaded = true;
              transcriptMetadataState.payload = {{
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
    browser.get(page.as_uri() + "?shareReplay=0")
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


def test_http_share_browser_keeps_finder_tabs_editor_differ_and_tabber_in_sync(browser, monkeypatch, tmp_path):
    share_root = tmp_path / "yolomux.dev1"
    share_root.mkdir()
    (share_root / "docs").mkdir()
    done_file = share_root / "DONE.md"
    done_file.write_text("# DONE\n\nShare viewer parity.\n", encoding="utf-8")
    done_path = str(done_file)
    root_path = str(share_root)
    expires_at = 4102444800.0
    layout = "row@34(left,right)"
    tabs = f"left:__finder__,__differ__,__tabber__;right:6,file:{done_path}*"
    share_ui_state = {
        "layout": layout,
        "tabs": tabs,
        "chrome": {"tabMetaVisible": True},
        "finder": {"root": root_path, "rootMode": "sync", "mode": "files", "session": "6", "expanded": [root_path]},
        "editor": {"modes": [{"path": done_path, "item": f"file:{done_path}", "mode": "edit"}]},
    }
    record = {
        "session": "6",
        "sessions": ["6"],
        "short_id": "share123",
        "mode": "ro",
        "scheme": "http",
        "expires_at": expires_at,
        "created_by": "host",
        "max_viewers": 5,
        "viewers": 0,
        "http_allowed": True,
        "layout": layout,
        "tabs": tabs,
        "finder": share_ui_state["finder"],
        "ui_state": share_ui_state,
    }
    seen_tokens = []

    def verify_share_token(token):
        seen_tokens.append(token)
        return record if token == "valid-share-token" else None

    transcript_payload = {
        "server_version": "test",
        "session_order": ["6"],
        "sessions": {
            "6": {
                "session": "6",
                "project": {
                    "git": {
                        "root": root_path,
                        "cwd": root_path,
                        "branch": "fix/share-parity",
                        "dirty_count": 3,
                    },
                },
                "selected_pane": {"current_path": root_path},
                "panes": [
                    {
                        "window": "1",
                        "window_name": "codex",
                        "window_active": True,
                        "active": True,
                        "current_path": root_path,
                        "process_label": "codex",
                    },
                ],
                "agents": [{"kind": "codex", "status": "idle"}],
            },
        },
    }
    session_files_payload = {
        "session": "6",
        "loaded": True,
        "files": [
            {
                "session": "6",
                "agent": "codex",
                "status": "M",
                "repo": root_path,
                "path": "DONE.md",
                "abs_path": done_path,
                "mtime": 200,
                "added": 2,
                "removed": 0,
            },
        ],
        "repos": [{"repo": root_path, "from_ref": "HEAD", "to_ref": "current", "added": 2, "removed": 0}],
        "errors": [],
    }
    app = SimpleNamespace(
        sessions=["6"],
        tmux_recency_ordered_sessions=lambda sessions: list(sessions),
        dangerously_yolo=False,
        verify_share_token=verify_share_token,
        share_record_for_short_id=lambda short_id: record if short_id == "share123" else None,
        http_allowed_share_is_active=lambda: True,
        share_record_allows_file_path=lambda share_record, raw_path: share_record == record and raw_path == done_path,
        share_status_payload=lambda token, **kwargs: ({
            "ok": True,
            "active": True,
            "token": token,
            "session": "6",
            "sessions": ["6"],
            "mode": "ro",
            "scheme": "http",
            "short_id": "share123",
            "expires_at": expires_at,
            "max_viewers": 5,
            "viewers": 0,
            "layout": layout,
            "tabs": tabs,
            "finder": share_ui_state["finder"],
            "uiState": share_ui_state,
        }, HTTPStatus.OK),
        transcripts_payload=lambda force=False: transcript_payload,
            activity_payload=lambda **_kwargs: ({
                "activity": {
                    "6": {"session": "6", "last_output_ts": 200},
                    "6:1": {"session": "6", "window": 1, "last_output_ts": 200},
                },
                "agent_windows": {
                    "6": [{
                        "kind": "codex",
                        "state": "idle",
                        "window": "1",
                        "window_index": 1,
                        "window_label": "1:codex",
                        "pid": 0,
                        "active": True,
                        "path_entries": [{"path": root_path, "mtime": 200, "git": transcript_payload["sessions"]["6"]["project"]["git"]}],
                        "paths": [root_path],
                        "git": transcript_payload["sessions"]["6"]["project"]["git"],
                    }],
                },
            }, HTTPStatus.OK),
        session_files_batch_payload=lambda sessions, hours, **kwargs: ({
            "sessions": {"6": session_files_payload | {"session": "6"}},
            "errors": {},
        }, HTTPStatus.OK),
        session_files_payload=lambda session, hours, **kwargs: (session_files_payload | {"session": session}, HTTPStatus.OK),
    )
    server, thread = start_browser_share_server(monkeypatch, tmp_path, app, tls_context=BrowserFakeTlsContext())
    port = server.server_address[1]
    try:
        browser.get(f"http://127.0.0.1:{port}/share/share123?shareReplay=0#t=valid-share-token")
        metrics = browser.execute_async_script(
            """
            const rootPath = arguments[0];
            const donePath = arguments[1];
            const done = arguments[arguments.length - 1];
            (async () => {
              const {frame} = window.__yolomuxTestHelpers;
              const waitFor = window.__yolomuxTestWaitFor;
              const visibleText = selector => Array.from(document.querySelectorAll(selector))
                .map(node => node.textContent || '')
                .join('\\n');
              const tabText = () => {
                const tab = Array.from(document.querySelectorAll('[data-pane-tab], [data-item]'))
                  .find(node => node.dataset.paneTab === '6' || node.dataset.item === '6');
                return tab ? tab.textContent : '';
              };
              const finderReady = await waitFor(() => {
                const pathInput = document.querySelector('#panel-__finder__ .file-explorer-path-inline');
                const hasDone = Array.from(document.querySelectorAll('#panel-__finder__ .file-tree-row[data-path]'))
                  .some(row => row.dataset.path === donePath);
                return pathInput?.value === rootPath && hasDone && !pathInput.classList.contains('error');
              });
              const editorReady = await waitFor(() => (document.body.textContent || '').includes('Share viewer parity.'));
              const sessionTabText = tabText();
              activatePaneTab(slotForItem(differItemId), differItemId, {userInitiated: false});
              await frame();
              const differReady = await waitFor(() => visibleText('#panel-__differ__ .file-explorer-changes-panel').includes('DONE.md')
                && !visibleText('#panel-__differ__ .file-explorer-changes-panel').includes('TypeError'));
              const differText = visibleText('#panel-__differ__ .file-explorer-changes-panel');
              activatePaneTab(slotForItem(tabberItemId), tabberItemId, {userInitiated: false});
              await frame();
              const tabberReady = await waitFor(() => Array.from(document.querySelectorAll('#panel-__tabber__ .file-tree-row[data-tabber-type="window"]'))
                .some(row => (row.textContent || '').includes('1:codex')));
              const tabberText = visibleText('#panel-__tabber__');
              return {
                finderReady,
                editorReady,
                differReady,
                tabberReady,
                pathValue: document.querySelector('#panel-__finder__ .file-explorer-path-inline')?.value || '',
                pathError: document.querySelector('#panel-__finder__ .file-explorer-path-inline')?.classList.contains('error') || false,
                sessionTabText,
                bodyHasLoadFailed: (document.body.textContent || '').includes('TypeError: Load failed')
                  || (document.body.textContent || '').includes('Failed to fetch'),
                differText,
                tabberText,
                shareToken,
              };
            })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
            """,
            root_path,
            done_path,
        )
    finally:
        stop_browser_share_server(server, thread)
    assert "error" not in metrics, metrics
    assert metrics["shareToken"] == "valid-share-token", metrics
    assert metrics["finderReady"] is True, metrics
    assert metrics["editorReady"] is True, metrics
    assert metrics["pathValue"] == root_path, metrics
    assert metrics["pathError"] is False, metrics
    assert metrics["differReady"] is True, metrics
    assert metrics["tabberReady"] is True, metrics
    assert "fix/share-parity" in metrics["sessionTabText"] or "share-parity" in metrics["sessionTabText"], metrics
    assert "no path" not in metrics["sessionTabText"], metrics
    assert "finding branch" not in metrics["sessionTabText"], metrics
    assert metrics["bodyHasLoadFailed"] is False, metrics
    assert "DONE.md" in metrics["differText"], metrics
    assert "1:codex" in metrics["tabberText"], metrics
    assert "Compare:" not in metrics["tabberText"], metrics
    assert "valid-share-token" in seen_tokens


def test_share_replay_readonly_shell_routes_to_inert_mirror_root(browser, monkeypatch, tmp_path):
    expires_at = 4102444800.0
    record = {
        "session": "6",
        "sessions": ["6"],
        "short_id": "share123",
        "mode": "ro",
        "scheme": "http",
        "expires_at": expires_at,
        "created_by": "host",
        "max_viewers": 5,
        "viewers": 0,
        "http_allowed": True,
        "layout": "left",
        "tabs": "left:6",
        "ui_state": {"viewport": {"width": 1440, "height": 900}},
    }
    seen_tokens = []

    def verify_share_token(token):
        seen_tokens.append(token)
        return record if token == "valid-share-token" else None

    app = SimpleNamespace(
        sessions=["6"],
        tmux_recency_ordered_sessions=lambda sessions: list(sessions),
        dangerously_yolo=False,
        verify_share_token=verify_share_token,
        share_record_for_short_id=lambda short_id: record if short_id == "share123" else None,
        http_allowed_share_is_active=lambda: True,
        share_status_payload=lambda token, **kwargs: ({
            "ok": True,
            "active": True,
            "token": token,
            "session": "6",
            "sessions": ["6"],
            "mode": "ro",
            "scheme": "http",
            "short_id": "share123",
            "expires_at": expires_at,
            "max_viewers": 5,
            "viewers": 0,
            "layout": "left",
            "tabs": "left:6",
            "uiState": record["ui_state"],
        }, HTTPStatus.OK),
    )
    server, thread = start_browser_share_server(monkeypatch, tmp_path, app, tls_context=BrowserFakeTlsContext())
    port = server.server_address[1]
    try:
        browser.get(f"http://127.0.0.1:{port}/share/share123#t=valid-share-token")
        WebDriverWait(browser, 5).until(
            lambda driver: driver.execute_script("return document.body.classList.contains('share-replay-shell')")
        )
        metrics = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            (async () => {
              const {frame} = window.__yolomuxTestHelpers;
              for (let attempt = 0; attempt < 60; attempt += 1) {
                if (document.querySelector('.share-viewer-banner [data-share-viewer-control="fit"]')) break;
                await frame();
              }
              const root = document.getElementById('appRoot');
              const stage = document.getElementById('shareMirrorStage');
              const banner = document.querySelector('.share-viewer-banner');
              const fit = document.querySelector('[data-share-viewer-control="fit"]');
              fit?.click();
              await frame();
              done({
                pathname: location.pathname,
                search: location.search,
                replayEnabled: typeof shareReplayFeatureEnabled === 'function' && shareReplayFeatureEnabled(),
                shellEnabled: typeof shareReplayShellEnabled === 'function' && shareReplayShellEnabled(),
                bodyClasses: Array.from(document.body.classList),
                stageContainsRoot: Boolean(stage && root && stage.contains(root)),
                rootParentId: root?.parentElement?.id || '',
                stageReplayFlag: stage?.dataset?.shareReplayShell || '',
                rootReplayFlag: root?.dataset?.shareReplayRoot || '',
                rootInertFlag: root?.dataset?.shareReplayInert || '',
                rootStatus: root?.dataset?.shareReplayStatus || '',
                    mirroredHandlerCount: root ? root.querySelectorAll('[data-tab], [data-pane-actions], [data-pane-minimize], .app-menu, .panel, .terminal, .xterm').length : -1,
                    rootChildCount: root?.children?.length ?? -1,
                bannerExists: Boolean(banner),
                bannerOutsideRoot: Boolean(banner && root && !root.contains(banner)),
                bannerParentIsBody: banner?.parentElement === document.body,
                fitControlExists: Boolean(fit),
                fitToggled: document.body.classList.contains('share-fit-contain') || document.body.classList.contains('share-fit-cover'),
              });
            })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
            """
        )
    finally:
        stop_browser_share_server(server, thread)
    assert "error" not in metrics, metrics
    assert metrics["pathname"] == "/share/share123", metrics
    assert metrics["search"] == "", metrics
    assert metrics["replayEnabled"] is True, metrics
    assert metrics["shellEnabled"] is True, metrics
    assert "share-view-mode" in metrics["bodyClasses"], metrics
    assert "share-view-readonly" in metrics["bodyClasses"], metrics
    assert "share-replay-shell" in metrics["bodyClasses"], metrics
    assert metrics["stageContainsRoot"] is True, metrics
    assert metrics["rootParentId"] == "shareMirrorStage", metrics
    assert metrics["stageReplayFlag"] == "true", metrics
    assert metrics["rootReplayFlag"] == "true", metrics
    assert metrics["rootInertFlag"] == "true", metrics
    assert metrics["rootStatus"] in {"waiting", "keyframe"}, metrics
    assert metrics["mirroredHandlerCount"] == 0, metrics
    assert metrics["rootChildCount"] == 0, metrics
    assert metrics["bannerExists"] is True, metrics
    assert metrics["bannerOutsideRoot"] is True, metrics
    assert metrics["bannerParentIsBody"] is True, metrics
    assert metrics["fitControlExists"] is True, metrics
    assert metrics["fitToggled"] is True, metrics
    assert "valid-share-token" in seen_tokens


def test_share_replay_default_routes_write_share_to_replay_shell(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-write-default",
        "mode": "rw",
        "session": "6",
        "sessions": ["6"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:6",
        "uiState": {"layout": "left", "tabs": "left:6", "viewport": {"width": 1440, "height": 900}},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="#t=valid-share-token",
        sessions=["6"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "token": "valid-share-token", "mode": "rw", "expiresAt": 4102444800.0, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.body.classList.contains('share-view-write') && document.body.classList.contains('share-replay-shell')")
    )
    metrics = browser.execute_script(
        """
        const root = document.getElementById('appRoot');
        return {
          replayEnabled: typeof shareReplayFeatureEnabled === 'function' && shareReplayFeatureEnabled(),
          shellEnabled: typeof shareReplayShellEnabled === 'function' && shareReplayShellEnabled(),
          bodyClasses: Array.from(document.body.classList),
          rootReplayFlag: root?.dataset?.shareReplayRoot || '',
          rootInertFlag: root?.dataset?.shareReplayInert || '',
          gridExists: Boolean(document.getElementById('grid')),
          topbarExists: Boolean(document.querySelector('.topbar')),
          shareWriteMode,
        };
        """
    )
    assert metrics["replayEnabled"] is True, metrics
    assert metrics["shellEnabled"] is True, metrics
    assert metrics["shareWriteMode"] is True, metrics
    assert "share-view-write" in metrics["bodyClasses"], metrics
    assert "share-replay-shell" in metrics["bodyClasses"], metrics
    assert metrics["rootReplayFlag"] == "true", metrics
    assert metrics["rootInertFlag"] == "true", metrics
    assert metrics["gridExists"] is False, metrics
    assert metrics["topbarExists"] is False, metrics
    input_metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFor = window.__yolomuxTestWaitFor;
          const keyframe = (label, sequence) => ({
            digest: `sha256:${label}`,
            viewport: {width: 1200, height: 700},
            root: {
              nodeId: 1,
              tag: 'div',
              attrs: {id: 'appRoot', class: 'app-root host-root'},
              children: [
                {nodeId: 2, tag: 'section', attrs: {'data-testid': 'writer-pane'}, children: [
                  {nodeId: 3, tag: 'p', text: label},
                  {nodeId: 4, tag: 'div', attrs: {class: 'share-terminal-placeholder', 'data-share-terminal-placeholder': '6', 'data-session': '6', 'data-rows': '24', 'data-cols': '80'}, children: []}
                ]}
              ]
            },
            terminals: [{placeholderId: 'term-ph-6', session: '6', rows: 24, cols: 80, terminalEpoch: sequence}]
          });
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 2, sequence: 1, payload: keyframe('host frame one', 1)});
          const mounted = await waitFor(
            () => Boolean(terminals.get('6')?.socket?.readyState === WebSocket.OPEN
              && typeof terminals.get('6')?.term?._onData === 'function'
              && document.querySelector('[data-testid="writer-pane"] #term-6 .xterm')),
            {timeoutMs: 6500, intervalMs: 50, description: 'writable share terminal'}
          );
          const textBeforeInput = document.querySelector('[data-testid="writer-pane"] p')?.textContent || '';
          const terminalSocketBefore = (window.__bootSocketInstances || []).find(socket => socket.url.includes('/ws/share-view') && socket.url.includes('session=6'));
          terminals.get('6')?.term?._onData?.('echo writer\\n');
          await waitFor(
            () => (window.__bootSocketInstances || []).some(socket => socket.url.includes('/ws/share-ui') && socket.sent.length > 0),
            {timeoutMs: 6500, intervalMs: 50, description: 'share UI input message'}
          );
          const textAfterInput = document.querySelector('[data-testid="writer-pane"] p')?.textContent || '';
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 2, sequence: 2, payload: keyframe('host frame two', 2)});
          await waitFor(
            () => (document.querySelector('[data-testid="writer-pane"] p')?.textContent || '') === 'host frame two',
            {timeoutMs: 6500, intervalMs: 50, description: 'second host replay frame'}
          );
          const sockets = (window.__bootSocketInstances || []).map(socket => ({
            url: socket.url,
            sent: socket.sent.map(message => JSON.parse(message)),
          }));
          const shareUiMessages = sockets.filter(socket => socket.url.includes('/ws/share-ui')).flatMap(socket => socket.sent);
          const shareUiInputMessages = shareUiMessages.filter(message => message.type === shareMirrorProtocol.frames.inputIntent);
          const terminalMessages = sockets.filter(socket => socket.url.includes('/ws/share-view') && socket.url.includes('session=6')).flatMap(socket => socket.sent);
          done({
            mounted,
            shellActive: shareReplayShellActive,
            textBeforeInput,
            textAfterInput,
            textAfterHostFrame: document.querySelector('[data-testid="writer-pane"] p')?.textContent || '',
            terminalSocketOpen: terminalSocketBefore?.readyState === WebSocket.OPEN,
            terminalSocketMessages: terminalMessages,
            shareUiMessages,
            shareUiInputMessages,
            shareUiSocketCount: sockets.filter(socket => socket.url.includes('/ws/share-ui')).length,
            terminalSocketCount: sockets.filter(socket => socket.url.includes('/ws/share-view') && socket.url.includes('session=6')).length,
            rows: terminals.get('6')?.term?.rows || 0,
            cols: terminals.get('6')?.term?.cols || 0,
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          });
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in input_metrics, input_metrics
    assert input_metrics["errors"] == [], input_metrics
    assert input_metrics["rejections"] == [], input_metrics
    assert input_metrics["mounted"] is True, input_metrics
    assert input_metrics["shellActive"] is True, input_metrics
    assert input_metrics["textBeforeInput"] == "host frame one", input_metrics
    assert input_metrics["textAfterInput"] == "host frame one", input_metrics
    assert input_metrics["textAfterHostFrame"] == "host frame two", input_metrics
    assert input_metrics["terminalSocketOpen"] is True, input_metrics
    assert input_metrics["terminalSocketMessages"] == [], input_metrics
    assert input_metrics["shareUiSocketCount"] == 1, input_metrics
    assert input_metrics["terminalSocketCount"] == 1, input_metrics
    assert input_metrics["rows"] == 24, input_metrics
    assert input_metrics["cols"] == 80, input_metrics
    assert len(input_metrics["shareUiInputMessages"]) == 1, input_metrics
    intent_message = input_metrics["shareUiInputMessages"][0]
    assert intent_message["type"] == "input-intent", input_metrics
    assert intent_message["payload"] == {"intent": "terminal-input", "session": "6", "data": "echo writer\n"}, input_metrics


def test_share_host_active_share_publishes_and_answers_dom_keyframes(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        sessions=["6"],
        access_role="admin",
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof sharePublishDomKeyframe === 'function'
              && typeof shareReplayFeatureEnabled === 'function'
              && document.getElementById('grid');
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const waitFor = window.__yolomuxTestWaitFor;
          const hostSockets = () => (window.__bootSocketInstances || []).filter(socket => socket.url.includes('/ws/share-host'));
          const hostMessages = () => hostSockets().flatMap(socket => socket.sent.map(message => JSON.parse(message)));
          const seededShare = {
            active: true,
            token: 'host-share-token',
            shortId: 'host-share',
            mode: 'ro',
            session: '6',
            sessions: ['6'],
            expiresAt: 4102444800,
            maxViewers: 5,
          };
          window.__fixtureSharePayload = seededShare;
          setActiveShares([seededShare]);
          ensureShareHostSockets();
          const replayEnabledAfterActivation = shareReplayFeatureEnabled();
          const publishedInitial = sharePublishDomKeyframe('join');
          const sentInitial = await waitFor(
            () => hostMessages().filter(message => message.type === shareMirrorProtocol.frames.domKeyframe).length >= 1,
            {timeoutMs: 6500, intervalMs: 50, description: 'initial share keyframe'}
          );
          const publishedFresh = sharePublishDomKeyframe('manual-debug');
          const keyframesBeforeBurst = hostMessages().filter(message => message.type === shareMirrorProtocol.frames.domKeyframe).length;
          shareReplayHostLastKeyframeAt = Date.now() - shareReplayHostKeyframeMinIntervalMs + 250;
          for (let index = 0; index < 5; index += 1) {
            applyShareUiMessage({
              ch: 'ui',
              type: shareMirrorProtocol.frames.domKeyframeRequest,
              sender: '__server__',
              epoch: 5,
              sequence: index + 1,
              payload: {reason: 'gap', viewerId: 'viewer-a'},
            });
          }
          const keyframesBeforeCoalescedTimer = hostMessages().filter(message => message.type === shareMirrorProtocol.frames.domKeyframe).length;
          const pendingAfterBurst = Boolean(shareReplayHostKeyframeTimer);
          const answeredRequest = await waitFor(
            () => hostMessages().filter(message => message.type === shareMirrorProtocol.frames.domKeyframe).length >= keyframesBeforeBurst + 1,
            {timeoutMs: 6500, intervalMs: 50, description: 'coalesced keyframe response'}
          );
          const sockets = hostSockets();
          const messages = hostMessages();
          const keyframes = messages.filter(message => message.type === shareMirrorProtocol.frames.domKeyframe);
          done({
            replayEnabledAfterActivation,
            publishedInitial,
            publishedFresh,
            sentInitial,
            answeredRequest,
            keyframesBeforeBurst,
            keyframesBeforeCoalescedTimer,
            pendingAfterBurst,
            hostKeyframesSuppressed: shareReplayHostKeyframeSuppressedCount,
            hostSocketCount: sockets.length,
            hostSocketOpen: sockets.some(socket => socket.readyState === WebSocket.OPEN),
            messageTypes: messages.map(message => message.type),
            reasons: keyframes.map(message => message.reason),
            payloadReasons: keyframes.map(message => message.payload?.reason || ''),
            rootTags: keyframes.map(message => message.payload?.root?.tag || ''),
            shareIds: keyframes.map(message => message.payload?.shareId || ''),
            hasRootChildren: keyframes.map(message => (message.payload?.root?.children || []).length > 0),
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          });
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics
    assert metrics["replayEnabledAfterActivation"] is True, metrics
    assert metrics["publishedInitial"] is True, metrics
    assert metrics["publishedFresh"] is True, metrics
    assert metrics["sentInitial"] is True, metrics
    assert metrics["answeredRequest"] is True, metrics
    assert metrics["hostSocketCount"] == 1, metrics
    assert metrics["hostSocketOpen"] is True, metrics
    assert metrics["keyframesBeforeBurst"] >= 2, metrics
    assert metrics["keyframesBeforeCoalescedTimer"] == metrics["keyframesBeforeBurst"], metrics
    assert metrics["pendingAfterBurst"] is True, metrics
    assert metrics["hostKeyframesSuppressed"] >= 5, metrics
    assert metrics["messageTypes"].count("dom-keyframe") == metrics["keyframesBeforeBurst"] + 1, metrics
    assert metrics["reasons"][0] == "join", metrics
    assert metrics["reasons"][-1] == "gap", metrics
    assert metrics["payloadReasons"][0] == "join", metrics
    assert metrics["payloadReasons"][-1] == "gap", metrics
    assert metrics["rootTags"][0] == "div", metrics
    assert metrics["rootTags"][-1] == "div", metrics
    assert metrics["shareIds"][0] == "host-share-token", metrics
    assert metrics["shareIds"][-1] == "host-share-token", metrics
    assert metrics["hasRootChildren"][0] is True, metrics
    assert metrics["hasRootChildren"][-1] is True, metrics


def test_share_dom_keyframe_clears_pending_mutation_delta(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        sessions=["6"],
        access_role="admin",
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return typeof sharePublishDomKeyframeNow === 'function'
              && typeof shareReplayEnqueueMutationRecords === 'function'
              && document.getElementById('appRoot');
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFor = window.__yolomuxTestWaitFor;
          const hostSockets = () => (window.__bootSocketInstances || []).filter(socket => socket.url.includes('/ws/share-host'));
          const hostMessages = () => hostSockets().flatMap(socket => socket.sent.map(message => JSON.parse(message)));
          const seededShare = {
            active: true,
            token: 'host-share-token',
            shortId: 'host-share',
            mode: 'ro',
            session: '6',
            sessions: ['6'],
            expiresAt: 4102444800,
            maxViewers: 5,
          };
          window.__fixtureSharePayload = seededShare;
          setActiveShares([seededShare]);
          ensureShareHostSockets();
          const opened = await waitFor(
            () => hostSockets().some(socket => socket.readyState === WebSocket.OPEN),
            {timeoutMs: 2500, description: 'share host socket'}
          );
          const root = appRootElement();
          const initialPayload = shareCreateDomKeyframePayload('join');
          await frame();
          for (const socket of hostSockets()) socket.sent.splice(0, socket.sent.length);
          const neverMirrored = document.createElement('section');
          neverMirrored.id = 'never-mirrored-delta-target';
          neverMirrored.setAttribute('data-state', 'changed');
          root.appendChild(neverMirrored);
          const neverMirroredEntries = shareReplayEnqueueMutationRecords([{type: 'attributes', target: neverMirrored, attributeName: 'data-state'}]);
          neverMirrored.remove();
          const staleNode = document.createElement('section');
          staleNode.id = 'stale-pending-mutation';
          staleNode.textContent = 'this stale mutation must not flush after keyframe';
          const entries = shareReplayEnqueueMutationRecords([{type: 'childList', target: root, addedNodes: [staleNode], removedNodes: []}]);
          const pendingBefore = shareReplayPendingMutations.length;
          const framePendingBefore = shareReplayDeltaFramePending;
          const published = sharePublishDomKeyframeNow('topology');
          await frame();
          await frame();
          const messages = hostMessages();
          done({
            opened,
            initialPayloadReady: Boolean(initialPayload?.root),
            published,
            neverMirroredEntriesLength: neverMirroredEntries.length,
            entriesLength: entries.length,
            pendingBefore,
            framePendingBefore,
            pendingAfter: shareReplayPendingMutations.length,
            terminalPendingAfter: shareReplayPendingTerminalPlaceholders.length,
            framePendingAfter: shareReplayDeltaFramePending,
            messageTypes: messages.map(message => message.type),
            keyframeReasons: messages.filter(message => message.type === shareMirrorProtocol.frames.domKeyframe).map(message => message.reason),
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          });
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics
    assert metrics["opened"] is True, metrics
    assert metrics["initialPayloadReady"] is True, metrics
    assert metrics["published"] is True, metrics
    assert metrics["neverMirroredEntriesLength"] == 0, metrics
    assert metrics["entriesLength"] == 1, metrics
    assert metrics["pendingBefore"] == 1, metrics
    assert metrics["framePendingBefore"] is True, metrics
    assert metrics["pendingAfter"] == 0, metrics
    assert metrics["terminalPendingAfter"] == 0, metrics
    assert metrics["framePendingAfter"] is False, metrics
    assert metrics["messageTypes"].count("dom-keyframe") == 1, metrics
    assert "dom-delta" not in metrics["messageTypes"], metrics
    assert metrics["keyframeReasons"] == ["topology"], metrics


def test_generated_share_link_receives_large_dom_keyframe(browser, monkeypatch, tmp_path):
    runtime = start_isolated_browser_share_app(monkeypatch, tmp_path)
    session = runtime.sessions[0]
    server, thread = start_browser_share_server(monkeypatch, tmp_path, runtime.app, auth_bypass=True)
    viewer = new_chrome_driver("1220,742")
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    def share_debug_url(url: str) -> str:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["shareDebug"] = "1"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    try:
        browser.get(f"{base_url}/")
        WebDriverWait(browser, 10).until(
            lambda driver: driver.execute_script(
                "return typeof apiFetchJson === 'function' && typeof sharePublishDomKeyframe === 'function' && document.getElementById('appRoot');"
            )
        )
        created = browser.execute_async_script(
            """
            const session = arguments[0];
            const done = arguments[arguments.length - 1];
            (async () => {
              const {frame} = window.__yolomuxTestHelpers;
              const waitFor = window.__yolomuxTestWaitFor;
              const bulk = document.createElement('section');
              bulk.id = 'share-large-dom-payload';
              bulk.style.cssText = 'position:absolute;left:-9999px;top:0;width:1px;height:1px;overflow:hidden;';
              bulk.textContent = `large-share-keyframe ${'x'.repeat(180000)}`;
              appRootElement().append(bulk);
              const seed = shareLayoutSeed();
              const createdShare = normalizeSharePayload(await apiFetchJson('/api/share', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                  session,
                  sessions: [session],
                  ttl_seconds: 600,
                  max_viewers: 5,
                  mode: 'ro',
                  read_only: true,
                  scheme: 'http',
                  layout: seed.layout,
                  tabs: seed.tabs,
                  finder: shareFinderSeed(),
                  ui_state: shareCreateUiStateSnapshot(),
                }),
              }));
              if (!createdShare?.url) throw new Error('share create returned no URL');
              setActiveShares([...activeShares.filter(share => share.token !== createdShare.token), createdShare]);
              ensureShareHostSockets();
              const opened = await waitFor(() => shareHostConnectionSockets().some(socket => socket.readyState === WebSocket.OPEN));
              const published = sharePublishDomKeyframe('join');
              await frame();
              const keyframe = shareCreateDomKeyframePayload('manual-debug');
              done({
                url: createdShare.url,
                opened,
                published,
                keyframeBytes: JSON.stringify(keyframe).length,
                keyframeChildren: keyframe?.root?.children?.length || 0,
              });
            })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
            """,
            session,
        )
        assert "error" not in created, created
        assert created["opened"] is True, created
        assert created["published"] is True, created
        assert created["keyframeBytes"] > 100_000, created
        assert created["keyframeChildren"] > 0, created

        viewer.get(share_debug_url(created["url"]))
        WebDriverWait(viewer, 10).until(lambda driver: driver.execute_script("return document.readyState === 'complete';"))

        def viewer_metrics():
            return viewer.execute_script(
                """
                const root = document.getElementById('appRoot');
                const health = window.yolomuxShareDebug?.replayHealth?.() || {};
                return {
	              status: root?.dataset?.shareReplayStatus || health.status || '',
	              keyframeBytes: Number(health.keyframeBytes || 0),
	              keyframeRequests: Number(health.keyframeRequests || 0),
	              keyframeRequestsSuppressed: Number(health.keyframeRequestsSuppressed || 0),
	              lastReplayError: health.lastReplayError || null,
	              rootChildren: root?.children?.length || 0,
                  hasLargePayload: Boolean(root?.innerText?.includes('large-share-keyframe')),
                  finderText: document.getElementById('share-parity-finder')?.textContent || '',
                  differText: document.getElementById('share-parity-differ')?.textContent || '',
                  hasDebug: typeof window.yolomuxShareDebug === 'object',
                };
                """
            )

        def mirrored(_driver):
            metrics = viewer_metrics()
            if metrics["status"] == "mirrored" and metrics["rootChildren"] > 0 and metrics["keyframeBytes"] > 100_000:
                return metrics
            return False

        metrics = WebDriverWait(viewer, 20, poll_frequency=0.25).until(mirrored)

        assert metrics["hasDebug"] is True, metrics
        assert metrics["status"] == "mirrored", metrics
        assert metrics["rootChildren"] > 0, metrics
        assert metrics["keyframeBytes"] > 100_000, metrics
        assert metrics["keyframeRequests"] <= 2, metrics
        assert metrics["hasLargePayload"] is True, metrics
        def repaired_or_stable(_driver):
            latest = viewer_metrics()
            if (
                latest["status"] == "mirrored"
                and latest["rootChildren"] > 0
                and latest["hasLargePayload"] is True
                and latest["keyframeRequests"] >= metrics["keyframeRequests"]
            ):
                return latest
            return False

        settled_metrics = WebDriverWait(viewer, 8, poll_frequency=0.25).until(repaired_or_stable)
        assert settled_metrics["status"] == "mirrored", settled_metrics
        assert settled_metrics["rootChildren"] > 0, settled_metrics
        assert settled_metrics["hasLargePayload"] is True, settled_metrics
        assert settled_metrics["keyframeRequests"] >= metrics["keyframeRequests"], {"initial": metrics, "settled": settled_metrics}
        assert settled_metrics["keyframeRequests"] <= 2, {"initial": metrics, "settled": settled_metrics}

        finder_published = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            (async () => {
              const {frame} = window.__yolomuxTestHelpers;
              document.getElementById('share-parity-finder')?.remove();
              document.getElementById('share-parity-differ')?.remove();
              const finder = document.createElement('section');
              finder.id = 'share-parity-finder';
              finder.className = 'file-explorer-panel';
              finder.textContent = 'Finder parity marker';
              appRootElement().append(finder);
              await frame();
              await frame();
              const published = sharePublishDomKeyframe('manual-debug');
              await frame();
              done({published});
            })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
            """
        )
        assert "error" not in finder_published, finder_published
        assert finder_published["published"] is True, finder_published

        def finder_mirrored(_driver):
            latest = viewer_metrics()
            if latest["finderText"] == "Finder parity marker" and latest["status"] == "mirrored":
                return latest
            return False

        finder_metrics = WebDriverWait(viewer, 10, poll_frequency=0.25).until(finder_mirrored)
        assert finder_metrics["status"] == "mirrored", finder_metrics
        assert finder_metrics["differText"] == "", finder_metrics

        differ_published = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            (async () => {
              const {frame} = window.__yolomuxTestHelpers;
              document.getElementById('share-parity-finder')?.remove();
              document.getElementById('share-parity-differ')?.remove();
              const differ = document.createElement('section');
              differ.id = 'share-parity-differ';
              differ.className = 'file-changes-panel';
              differ.textContent = 'Differ parity marker';
              appRootElement().append(differ);
              await frame();
              await frame();
              const published = sharePublishDomKeyframe('manual-debug');
              await frame();
              done({published});
            })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
            """
        )
        assert "error" not in differ_published, differ_published
        assert differ_published["published"] is True, differ_published

        def differ_mirrored(_driver):
            latest = viewer_metrics()
            if latest["differText"] == "Differ parity marker" and latest["status"] == "mirrored":
                return latest
            return False

        differ_metrics = WebDriverWait(viewer, 10, poll_frequency=0.25).until(differ_mirrored)
        assert differ_metrics["status"] == "mirrored", differ_metrics
        assert differ_metrics["finderText"] == "", differ_metrics
    finally:
        viewer.quit()
        stop_browser_share_server(server, thread)
        stop_isolated_browser_share_app(runtime)


@pytest.mark.parametrize("matrix_section", ("chrome", "finder", "resilience", "popovers"))
def test_generated_share_link_mirrors_interactive_ui_surface_matrix(browser, monkeypatch, tmp_path, matrix_section):
    repo_root = tmp_path / "share-matrix-repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "docs").mkdir()
    (repo_root / "README.md").write_text("# Share matrix\n", encoding="utf-8")
    (repo_root / "src" / "app.py").write_text("print('share matrix')\n", encoding="utf-8")
    (repo_root / "docs" / "DONE.md").write_text("# DONE\n\nShare matrix coverage.\n", encoding="utf-8")

    runtime = start_isolated_browser_share_app(monkeypatch, tmp_path)
    session = runtime.sessions[0]
    server, thread = start_browser_share_server(monkeypatch, tmp_path, runtime.app, auth_bypass=True)
    # The canonical lane uses xdist worksteal: each parameter can execute in a different process,
    # so a module-scoped Selenium fixture cannot share a driver across this matrix.  Keep the
    # independent viewer local, which preserves the currently measured parallel execution and
    # avoids falsely implying cross-worker browser reuse.
    viewer = new_chrome_driver("1220,742")
    install_browser_websocket_tracker(viewer)
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    def share_debug_url(url: str) -> str:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["shareDebug"] = "1"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def host_phase(name, *args):
        result = browser.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const name = arguments[0];
            const args = Array.from(arguments).slice(1, -1);
            const helper = window.__shareMatrix;
            if (!helper || typeof helper[name] !== 'function') {
              done({error: `missing share matrix helper ${name}`});
              return;
            }
            Promise.resolve(helper[name](...args)).then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
            """,
            name,
            *args,
        )
        assert "error" not in result, result
        assert result["published"] is True, result
        return result

    def viewer_state():
        return viewer.execute_script(
            """
            const root = document.getElementById('appRoot');
            const marker = root?.querySelector?.('#shareMatrixMarker');
            let detail = {};
            try { detail = JSON.parse(marker?.dataset?.detail || '{}'); } catch (_) {}
            const health = window.yolomuxShareDebug?.replayHealth?.() || {};
            const terminalPlaceholders = health.terminalPlaceholders || {};
            const trackedSockets = Array.from(window.__yolomuxTrackedSockets || []);
            const terminalSockets = trackedSockets.filter(socket => String(socket?.url || '').includes('/ws/share-view'));
            const liveTerminalSockets = terminalSockets.filter(socket => ![2, 3].includes(Number(socket?.readyState)));
            const socketSession = socket => {
              try {
                return new URL(String(socket?.url || ''), window.location.href).searchParams.get('session') || '';
              } catch (_) {
                return '';
              }
            };
            const terminalSocketSessions = liveTerminalSockets.map(socketSession).filter(Boolean);
            const terminalSocketCounts = terminalSocketSessions.reduce((acc, session) => {
              acc[session] = (acc[session] || 0) + 1;
              return acc;
            }, {});
            const text = root?.innerText || '';
	            const menu = root?.querySelector?.('.app-menu.open');
	            const menuPopover = menu?.querySelector?.(':scope > .app-menu-popover');
	            const modal = root?.querySelector?.('#modal.open');
	            const shortcuts = root?.querySelector?.('.keyboard-shortcuts-overlay.open');
	            const tabPopover = root?.querySelector?.('.pane-tab-detached-popover.popover-open, .dockview-pane-tab.popover-open > .session-popover, .pane-tab.popover-open > .session-popover');
	            const repoPopover = root?.querySelector?.('#fileTreeRepoPopover, .file-tree-repo-popover');
	            const changesPanels = Array.from(root?.querySelectorAll?.('.file-explorer-changes-panel, #modified-files-panel') || []);
	            const visibleChangesPanels = changesPanels.filter(node => {
	              const style = getComputedStyle(node);
	              const rect = node.getBoundingClientRect();
	              return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
	            });
                const appRect = node => {
                  if (!node) return null;
                  const rect = (typeof appSpaceRect === 'function') ? appSpaceRect(node) : node.getBoundingClientRect();
                  return {left: Math.round(rect.left), top: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height)};
                };
                const visualRect = node => {
                  if (!node) return null;
                  const rect = node.getBoundingClientRect();
                  return {left: Math.round(rect.left), top: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height)};
                };
	                const computedBox = node => {
                  if (!node) return null;
                  const style = getComputedStyle(node);
                  return {
                    id: node.id || '',
                    className: node.className || '',
                    parentId: node.parentElement?.id || '',
                    parentClassName: node.parentElement?.className || '',
                    position: style.position,
                    left: style.left,
                    top: style.top,
                    inset: style.inset,
                    transform: style.transform,
                    appRect: appRect(node),
                    visualRect: visualRect(node),
	                  };
	                };
	                const visibleElement = node => {
	                  if (!node) return false;
	                  const style = getComputedStyle(node);
	                  const rect = node.getBoundingClientRect();
	                  return style.display !== 'none' && style.visibility !== 'hidden' && Number.parseFloat(style.opacity || '1') > 0.9 && rect.width > 20 && rect.height > 20;
	                };
		            return {
              phase: marker?.dataset?.phase || '',
              detail,
              status: root?.dataset?.shareReplayStatus || health.status || '',
              text,
              rootChildren: root?.children?.length || 0,
              keyframeBytes: Number(health.keyframeBytes || 0),
              keyframeRequests: Number(health.keyframeRequests || 0),
              keyframeRequestsSuppressed: Number(health.keyframeRequestsSuppressed || 0),
              keyframeRequestBackoffMs: Number(health.keyframeRequestBackoffMs || 0),
              keyframeRequestInFlight: health.keyframeRequestInFlight === true,
              droppedFrames: Number(health.droppedFrames || 0),
              staleFrames: Number(health.staleFrames || 0),
              lastReplayError: health.lastReplayError || null,
              terminalHealthy: terminalPlaceholders.healthy === true,
              terminalCount: Number(terminalPlaceholders.count || 0),
              terminalConnected: Number(terminalPlaceholders.connected || 0),
              terminalDisconnected: Number(terminalPlaceholders.disconnected || 0),
              terminalEntries: Array.isArray(terminalPlaceholders.entries) ? terminalPlaceholders.entries : [],
              liveTerminalSocketCount: liveTerminalSockets.length,
              terminalSocketCount: terminalSockets.length,
              terminalSocketSessions,
              terminalSocketCounts,
              terminalSocketOverLimit: Object.values(terminalSocketCounts).some(count => Number(count) > 1),
              elapsedMs: Number(window.__shareMatrixViewerStartedAt ? performance.now() - window.__shareMatrixViewerStartedAt : 0),
	              menuOpen: menu?.dataset?.appMenu || '',
	              menuText: menu?.textContent || '',
	              menuVisible: visibleElement(menuPopover),
	              menuCommandCount: menuPopover?.querySelectorAll?.('.app-menu-command')?.length || 0,
	              menuActiveCommandCount: menuPopover?.querySelectorAll?.('.app-menu-command.share-mirror-active')?.length || 0,
	              menuRect: appRect(menuPopover),
	              modalTitle: modal?.querySelector?.('#modalTitle')?.textContent || '',
              modalTitles: Array.from(modal?.querySelectorAll?.('#modalTitle') || []).map(node => node.textContent || ''),
              modalText: modal?.textContent || '',
              modalClass: modal?.className || '',
              shortcutsOpen: Boolean(shortcuts),
              shortcutsText: shortcuts?.textContent || '',
                  tabPopoverText: tabPopover?.textContent || '',
                  tabPopoverRect: appRect(tabPopover),
                  tabPopoverStyle: {left: tabPopover?.style?.left || '', top: tabPopover?.style?.top || '', height: tabPopover?.style?.height || ''},
                  tabPopoverDebug: {
                    popover: computedBox(tabPopover),
                    overlay: computedBox(root?.querySelector?.('#appOverlayRoot')),
                    appRoot: computedBox(root),
                    stage: computedBox(document.getElementById('shareMirrorStage')),
                    transform: (typeof appMirrorTransformState === 'function') ? appMirrorTransformState() : null,
                  },
                  repoPopoverText: repoPopover?.textContent || '',
	              finderPanelCount: root?.querySelectorAll?.('.file-explorer-panel')?.length || 0,
	              finderMode: root?.querySelector?.('.file-explorer-panel')?.dataset?.fileExplorerMode || '',
	              dateMode: root?.querySelector?.('[data-file-explorer-tree-dates]')?.dataset?.dateMode || '',
	              hasSecretToken: text.includes('#t=') || text.includes('valid-share-token'),
	              staleDifferSentinel: text.includes('STALE_DIFF_SENTINEL.md'),
	              changesPanelCount: visibleChangesPanels.length,
	              changesPanelDomCount: changesPanels.length,
	            };
	            """
	        )

    def wait_viewer_phase(phase, timeout=12):
        def ready(_driver):
            metrics = viewer_state()
            if metrics["phase"] == phase and metrics["status"] == "mirrored" and metrics["rootChildren"] > 0:
                return metrics
            return False

        try:
            metrics = WebDriverWait(viewer, timeout, poll_frequency=0.25).until(ready)
        except TimeoutException as exc:
            raise AssertionError({"expectedPhase": phase, "latest": viewer_state()}) from exc
        assert metrics["keyframeBytes"] > 0, metrics
        return metrics

    def assert_terminal_health(metrics):
        placeholder_sessions = {str(entry.get("session") or "") for entry in metrics["terminalEntries"] if entry.get("session")}
        socket_sessions = set(metrics["terminalSocketSessions"])
        assert metrics["terminalCount"] >= 1, metrics
        assert placeholder_sessions, metrics
        assert metrics["terminalHealthy"] is True, metrics
        assert metrics["terminalConnected"] == metrics["terminalCount"], metrics
        assert metrics["terminalDisconnected"] == 0, metrics
        assert metrics["liveTerminalSocketCount"] >= 1, metrics
        assert placeholder_sessions.issubset(socket_sessions), metrics
        assert metrics["terminalSocketOverLimit"] is False, metrics

    try:
        browser.get(f"{base_url}/")
        WebDriverWait(browser, 10).until(
            lambda driver: driver.execute_script(
                "return typeof apiFetchJson === 'function' && typeof sharePublishDomKeyframe === 'function' && typeof renderSessionButtons === 'function' && document.getElementById('appRoot');"
            )
        )
        created = browser.execute_async_script(
            """
            const repo = arguments[0];
            const session = arguments[1];
            const done = arguments[arguments.length - 1];
            (async () => {
              const {frame} = window.__yolomuxTestHelpers;
              const waitFor = window.__yolomuxTestWaitFor;
              await ensureTerminalRunning(session);
              if (!await waitFor(
                () => document.getElementById(terminalDomId(session))?.querySelector?.('.xterm') && terminals.get(session)?.socket?.readyState === WebSocket.OPEN,
                {timeoutMs: 10000, intervalMs: 50, description: 'host terminal socket'}
              )) {
                throw new Error('host terminal did not open before share create');
              }
              const seed = shareLayoutSeed();
              const createdShare = normalizeSharePayload(await apiFetchJson('/api/share', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                  session,
                  sessions: [session],
                  ttl_seconds: 600,
                  max_viewers: 5,
                  mode: 'ro',
                  read_only: true,
                  scheme: 'http',
                  debug_profile: true,
                  layout: seed.layout,
                  tabs: seed.tabs,
                  finder: shareFinderSeed(),
                  ui_state: shareCreateUiStateSnapshot(),
                }),
              }));
              if (!createdShare?.url) throw new Error('share create returned no URL');
              setActiveShares([...activeShares.filter(share => share.token !== createdShare.token), createdShare]);
              ensureShareHostSockets();
              if (!await waitFor(
                () => shareHostConnectionSockets().some(socket => socket.readyState === WebSocket.OPEN),
                {timeoutMs: 10000, intervalMs: 50, description: 'share host connection socket'}
              )) {
                throw new Error('share host socket did not open');
              }
              window.__shareMatrix = (() => {
                const {frame} = window.__yolomuxTestHelpers;
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const marker = () => {
                  let node = document.getElementById('shareMatrixMarker');
                  if (!node) {
                    node = document.createElement('section');
                    node.id = 'shareMatrixMarker';
                    node.dataset.shareVolatile = 'true';
                        node.style.cssText = 'position:absolute;left:8px;top:48px;z-index:20;max-width:760px;padding:4px 6px;border:1px solid var(--border);background:var(--panel2);color:var(--text);font:12px/1.3 var(--ui-font);pointer-events:none;';
                    appRootElement().appendChild(node);
                  }
                  return node;
                };
                const closeTransient = () => {
                  closeAppMenus?.();
                  closeCommandPalette?.();
                  closeKeyboardShortcutsOverlay?.();
                  hideFileTreeRepoPopover?.();
                  document.getElementById('modal')?.classList?.remove('open', 'about-open', 'share-open');
                };
                const detail = extra => ({
                  ...extra,
                  finderOpen: itemInLayout(fileExplorerItemId),
                  finderMode: normalizeFileExplorerMode(fileExplorerMode),
                  rootMode: fileExplorerRootModeValue(),
                  treeSortMode: fileExplorerTreeSortMode,
                  sessionFilesSortMode,
                  dateMode: normalizeFileExplorerTreeDateMode(fileExplorerTreeDateMode),
                  dateLabel: fileExplorerTreeDateModeButtonLabel(fileExplorerTreeDateMode),
                  finderPanelCount: document.querySelectorAll('.file-explorer-panel').length,
                  menuOpen: document.querySelector('.app-menu.open')?.dataset?.appMenu || '',
                  modalTitle: document.querySelector('#modal.open #modalTitle')?.textContent || '',
                });
                const publish = async (phase, extra = {}) => {
                  const state = detail(extra);
                  const node = marker();
                  node.dataset.phase = phase;
                  node.dataset.detail = JSON.stringify(state);
                  node.textContent = `Share matrix phase=${phase} mode=${state.finderMode} root=${state.rootMode} treeSort=${state.treeSortMode} diffSort=${state.sessionFilesSortMode} date=${state.dateMode}/${state.dateLabel}`;
                  await frame();
                  await frame();
                  const published = sharePublishDomKeyframe('manual-debug');
                  await frame();
                  return {published, phase, ...state};
                };
                const ensureFinder = async () => {
                  await openFileExplorerPane();
                  await frame();
                  setFileExplorerRootMode('fixed', {sync: false, persist: false});
                  await openFileExplorerAt(repo);
                  fileExplorerSessionFilesState.payload = {
                    session,
                    loaded: true,
                    errors: [],
                    repos: [{repo, from_ref: 'HEAD', to_ref: 'current', added: 3, removed: 1}],
                    files: [
                      {session, agent: 'codex', status: 'M', repo, path: 'README.md', abs_path: `${repo}/README.md`, mtime: 200, added: 2, removed: 1},
                      {session, agent: 'codex', status: 'M', repo, path: 'src/app.py', abs_path: `${repo}/src/app.py`, mtime: 100, added: 1, removed: 0},
                      {session, agent: 'codex', status: '?', repo, path: 'docs/DONE.md', abs_path: `${repo}/docs/DONE.md`, mtime: 300, added: 1, removed: 0},
                    ],
                  };
                  cacheFileExplorerRepoInfo(repo, {root: repo, name: 'repo-app', branch: 'feature/share-matrix', upstream: 'origin/feature/share-matrix', ahead: 2, behind: 1, dirty_count: 3});
                  renderFileExplorerChangesPanels({force: true});
                  refreshFileExplorerPanelTree(document.querySelector('.file-explorer-panel'), {preserveExpanded: true, preserveScroll: true});
                  await frame();
                  return document.querySelector('.file-explorer-panel');
                };
                const terminalVisible = () => {
                  const terminal = document.getElementById(terminalDomId(session));
                  if (!terminal || terminal.isConnected === false) return false;
                  const rect = terminal.getBoundingClientRect();
                  const style = getComputedStyle(terminal);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const ensureVisibleTerminalWithFinder = async () => {
                  await ensureTerminalRunning(session);
                  if (!itemInLayout(session)) {
                    await placeTmuxSession(session);
                    await frame();
                  }
                  const slot = slotForSession(session);
                  if (slot) activatePaneTab(slot, session);
                  await frame();
                  if (!itemInLayout(fileExplorerItemId)) {
                    await openFileExplorerPane();
                    await frame();
                  }
                  dockFileExplorerPane();
                  await frame();
                  if (!terminalVisible()) {
                    await placeTmuxSession(session);
                    await frame();
                    if (itemInLayout(fileExplorerItemId)) {
                      dockFileExplorerPane();
                      await frame();
                    }
                  }
                  return terminalVisible();
                };
                const clickRootMode = async rootMode => {
                  const panel = await ensureFinder();
                  const button = panel?.querySelector('.file-explorer-root-mode-toggle-panel, #fileExplorerRootMode');
                  for (let attempt = 0; attempt < 3 && fileExplorerRootModeValue() !== rootMode; attempt += 1) {
                    button?.click();
                    await frame();
                  }
                  if (fileExplorerRootModeValue() !== rootMode) setFileExplorerRootMode(rootMode, {sync: false, persist: false});
                  await frame();
                };
                const clickDateMode = async dateMode => {
                  const panel = await ensureFinder();
                  const button = panel?.querySelector('[data-file-explorer-tree-dates]');
                  for (let attempt = 0; attempt < 5 && normalizeFileExplorerTreeDateMode(fileExplorerTreeDateMode) !== dateMode; attempt += 1) {
                    button?.click();
                    await frame();
                  }
                  if (normalizeFileExplorerTreeDateMode(fileExplorerTreeDateMode) !== dateMode) setFileExplorerTreeDateMode(dateMode);
                  await frame();
                };
                return {
                  async initial() {
                    return publish('initial');
                  },
                  async menu(menuId) {
                    closeTransient();
                    renderSessionButtons({force: true});
                    await frame();
                    const wrapper = document.querySelector(`.app-menu[data-app-menu="${menuId}"]`);
                    if (!wrapper) throw new Error(`missing menu ${menuId}`);
                    wrapper.querySelector(':scope > .app-menu-button')?.click();
                    await frame();
                    wrapper.querySelector(':scope > .app-menu-popover .app-menu-command:not([disabled])')?.dispatchEvent(new PointerEvent('pointerenter', {bubbles: true}));
                    await frame();
                    const commands = Array.from(wrapper.querySelectorAll(':scope > .app-menu-popover .app-menu-command'));
                    return publish(`menu-${menuId}`, {
                      menuId,
                      commandCount: commands.length,
                      activeCommandCount: wrapper.querySelectorAll(':scope > .app-menu-popover .app-menu-command.share-mirror-active').length,
                      labels: commands.map(node => clean(node.textContent)).slice(0, 8),
                    });
                  },
                  async shortcuts() {
                    closeTransient();
                    openKeyboardShortcutsOverlay();
                    await frame();
                    return publish('help-shortcuts');
                  },
                  async about() {
                    closeTransient();
                    showAboutModal();
                    await frame();
                    return publish('help-about');
                  },
                  async shareModal() {
                    closeTransient();
                    await showShareModal();
                    await frame();
                    return publish('share-modal', {debugProfile: true});
                  },
                  async info(subtab) {
                    closeTransient();
                    await openInfoSubTab(subtab);
                    await frame();
                    return publish(`info-${subtab}`, {subtab});
                  },
                  async finder(mode, rootMode, sortMode, dateMode) {
                    closeTransient();
                    const panel = await ensureFinder();
                    panel?.querySelector(`[data-file-explorer-mode-set="${mode}"]`)?.click();
                    if (normalizeFileExplorerMode(fileExplorerMode) !== mode) setFileExplorerMode(mode);
                    await clickRootMode(rootMode);
                    if (mode === 'diff') {
                      sessionFilesSortMode = normalizeSessionFilesSortMode(sortMode);
                      renderFileExplorerChangesPanels({force: true});
                      const diffSort = document.querySelector('[data-session-files-sort]');
                      if (diffSort) {
                        diffSort.value = sessionFilesSortMode;
                        diffSort.dispatchEvent(new Event('change', {bubbles: true}));
                      }
                    } else {
                      const treeSort = document.querySelector('[data-file-explorer-tree-sort]');
                      if (treeSort) {
                        treeSort.value = sortMode;
                        treeSort.dispatchEvent(new Event('change', {bubbles: true}));
                      }
                      fileExplorerTreeSortMode = ['az', 'za', 'newest', 'oldest'].includes(sortMode) ? sortMode : 'az';
                      writeStoredFileExplorerTreeSortMode(fileExplorerTreeSortMode);
	                    }
	                    await clickDateMode(dateMode);
	                    await clickRootMode(rootMode);
	                    renderFileExplorerChangesPanels({force: true});
	                    await frame();
	                    return publish(`finder-${mode}-${rootMode}-${sortMode}-${dateMode}`, {mode, rootMode, sortMode, dateMode});
                  },
	                  async visible(visible, mode) {
	                    closeTransient();
	                    if (visible) {
	                      await ensureFinder();
                      const panel = document.querySelector('.file-explorer-panel');
                      panel?.querySelector(`[data-file-explorer-mode-set="${mode}"]`)?.click();
                      if (normalizeFileExplorerMode(fileExplorerMode) !== mode) setFileExplorerMode(mode);
                    } else {
                      minimizePaneFromLayout(fileExplorerItemId);
                    }
	                    await frame();
	                    return publish(`finder-${visible ? 'show' : 'hide'}-${mode}`, {visible, mode});
	                  },
	                  async staleDifferPrecondition() {
	                    closeTransient();
	                    await ensureFinder();
	                    const sentinelFile = {session, agent: 'codex', status: 'M', repo, path: 'STALE_DIFF_SENTINEL.md', abs_path: `${repo}/STALE_DIFF_SENTINEL.md`, mtime: 400, added: 8, removed: 2};
	                    fileExplorerSessionFilesState.payload = {
	                      ...fileExplorerSessionFilesState.payload,
	                      files: [sentinelFile, ...(fileExplorerSessionFilesState.payload.files || [])],
	                    };
	                    setFileExplorerMode('diff');
	                    renderFileExplorerChangesPanels({force: true});
	                    await frame();
	                    return publish('finder-restore-stale-diff', {mode: 'diff', sentinel: sentinelFile.path});
	                  },
	                  async automaticFinderRestore() {
	                    closeTransient();
	                    const originalDeltaPublisher = shareReplayPublishDeltaPayload;
	                    const priorLastKeyframeAt = Math.max(0, Math.round(Number(shareReplayHostLastKeyframeAt) || 0));
	                    let suppressedDeltas = 0;
	                    try {
	                      shareReplayPublishDeltaPayload = () => {
	                        suppressedDeltas += 1;
	                        return false;
	                      };
	                      await ensureFinder();
	                      setFileExplorerMode('files');
	                      await frame();
	                      minimizePaneFromLayout(fileExplorerItemId);
	                      await frame();
	                      await openFileExplorerPane();
	                      setFileExplorerMode('files');
	                      await openFileExplorerAt(repo);
	                      await frame();
	                      await frame();
	                      const state = detail({mode: 'files', visible: itemInLayout(fileExplorerItemId), suppressedDeltas});
	                      const node = marker();
	                      node.dataset.phase = 'finder-restore-topology-keyframe';
	                      node.dataset.detail = JSON.stringify(state);
	                      node.textContent = `Share matrix phase=finder-restore-topology-keyframe mode=${state.finderMode} root=${state.rootMode} suppressedDeltas=${suppressedDeltas}`;
	                      const topologyPublished = await waitFor(
	                        () => Math.max(0, Math.round(Number(shareReplayHostLastKeyframeAt) || 0)) > priorLastKeyframeAt,
	                        {timeoutMs: 7500, description: 'Finder topology keyframe publication'}
	                      );
	                      await frame();
	                      return {published: true, topologyPublished, phase: 'finder-restore-topology-keyframe', suppressedDeltas, ...state};
	                    } finally {
	                      shareReplayPublishDeltaPayload = originalDeltaPublisher;
		                    }
		                  },
		                  async finderToggleLoop(count, pauseMs, label, publishGeometryDigest) {
		                    closeTransient();
		                    await ensureFinder();
		                    setFileExplorerMode('files');
		                    await frame();
		                    const total = Math.max(0, Math.round(Number(count) || 0));
		                    const delay = Math.max(0, Math.round(Number(pauseMs) || 0));
		                    let geometryDigests = 0;
		                    for (let index = 0; index < total; index += 1) {
		                      toggleFileExplorerShortcut();
		                      await frame();
		                      if (publishGeometryDigest === true) {
		                        sharePublish(shareMirrorProtocol.frames.geometryDigest, {
		                          digest: `forced-safari-cadence-${index}`,
		                          snapshot: {
		                            viewport: {width: 1220, height: 742},
		                            fonts: {ui: 249, mono: 297},
		                            slots: {},
		                            tabStrips: [{index, rect: {left: 412, top: 666 + (index % 2), width: 964, height: 21}}],
		                            terminalCells: [],
		                            editors: [],
		                            textWraps: [],
		                          },
		                        }, {reason: 'forced-safari-cadence'});
		                        geometryDigests += 1;
		                        await frame();
		                      }
		                      if (delay > 0 && index + 1 < total) await new Promise(resolve => setTimeout(resolve, delay));
		                    }
		                    if (!itemInLayout(fileExplorerItemId)) {
		                      toggleFileExplorerShortcut();
		                      await frame();
		                    }
		                    await ensureVisibleTerminalWithFinder();
		                    const state = detail({mode: 'files', visible: itemInLayout(fileExplorerItemId), count: total, pauseMs: delay, geometryDigests});
		                    const phase = `finder-toggle-loop-${label || (delay ? 'paused' : 'rapid')}`;
		                    const node = marker();
		                    node.dataset.phase = phase;
		                    node.dataset.detail = JSON.stringify(state);
		                    node.textContent = `Share matrix phase=${phase} visible=${state.finderOpen} count=${total} pauseMs=${delay}`;
		                    return publish(phase, {count: total, pauseMs: delay, visible: state.finderOpen, geometryDigests});
		                  },
	                  async expandWithFinder() {
	                    closeTransient();
	                    await ensureFinder();
                    await ensureVisibleTerminalWithFinder();
                    expandPaneFromLayout(session);
                    await frame();
                    await ensureVisibleTerminalWithFinder();
                    return publish('terminal-expand-with-finder');
                  },
                  async tabPopover() {
                    await waitFor(
                      () => Boolean(document.querySelector('.pane-tab-detached-popover.popover-open, .dockview-pane-tab.popover-open > .session-popover, .pane-tab.popover-open > .session-popover')),
                      {timeoutMs: 2000, description: 'shared tab popover'}
                    );
                    const popover = document.querySelector('.pane-tab-detached-popover.popover-open, .dockview-pane-tab.popover-open > .session-popover, .pane-tab.popover-open > .session-popover');
                    if (!popover) throw new Error('tab hover popover did not open through the real hover path');
                    let priorGeometry = '';
                    let stableFrames = 0;
                    for (let attempt = 0; attempt < 8 && stableFrames < 2; attempt += 1) {
                      await frame();
                      const rect = appSpaceRect(popover);
                      const geometry = [popover.style.left, popover.style.top, popover.style.height, rect.left, rect.top, rect.width, rect.height].join('|');
                      stableFrames = geometry === priorGeometry ? stableFrames + 1 : 0;
                      priorGeometry = geometry;
                    }
                    if (stableFrames < 2) throw new Error('tab hover popover geometry did not settle before share publish');
                    const popoverRect = appSpaceRect(popover);
                    return publish('tab-hover-popover', {
                      tabPopoverRect: {left: Math.round(popoverRect.left), top: Math.round(popoverRect.top), width: Math.round(popoverRect.width), height: Math.round(popoverRect.height)},
                      tabPopoverStyle: {left: popover.style.left || '', top: popover.style.top || '', height: popover.style.height || ''},
                    });
                  },
                  async repoPopover() {
                    closeTransient();
                    const panel = await ensureFinder();
                    await ensureVisibleTerminalWithFinder();
                    const currentPanel = document.querySelector('.file-explorer-panel') || panel;
                    const tree = currentPanel?.querySelector('.file-explorer-tree-panel') || currentPanel;
                    let row = tree?.querySelector(`.file-tree-row[data-path="${CSS.escape(repo)}"]`);
                    if (!row) {
                      row = document.createElement('div');
                      row.className = 'file-tree-row kind-dir is-repo';
                      row.dataset.path = repo;
                      row.innerHTML = '<span class="file-tree-name">repo-app</span>';
                      tree.appendChild(row);
                    }
                    const rect = row.getBoundingClientRect();
                    fileTreeRepoPopoverCursor = {x: Math.round(rect.left + 12), y: Math.round(rect.top + 12)};
                    showFileTreeRepoPopover(row, {root: repo, name: 'repo-app', branch: 'feature/share-matrix', upstream: 'origin/feature/share-matrix', ahead: 2, behind: 1, dirty_count: 3});
                    await frame();
                    return publish('finder-repo-popover');
                  },
                };
              })();
              const initial = await window.__shareMatrix.initial();
              done({url: createdShare.url, token: createdShare.token, initial});
            })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
            """,
            str(repo_root),
            session,
        )
        assert "error" not in created, created
        assert created["initial"]["published"] is True, created

        viewer.get(share_debug_url(created["url"]))
        WebDriverWait(viewer, 10).until(lambda driver: driver.execute_script("return document.readyState === 'complete';"))
        viewer.execute_script("window.__shareMatrixViewerStartedAt = performance.now();")
        host_phase("initial")
        initial_metrics = wait_viewer_phase("initial", timeout=20)
        assert initial_metrics["status"] == "mirrored"
        assert_terminal_health(initial_metrics)

        if matrix_section == "chrome":
            for menu_id in ["file", "view", "tmux", "tabs", "help"]:
                host = host_phase("menu", menu_id)
                metrics = wait_viewer_phase(f"menu-{menu_id}")
                assert host["menuOpen"] == menu_id, host
                assert metrics["menuOpen"] == menu_id, metrics
                assert metrics["detail"]["menuId"] == menu_id, metrics
                assert metrics["menuText"].strip(), metrics
                assert metrics["menuVisible"] is True, metrics
                assert metrics["menuCommandCount"] >= 1, metrics
                assert metrics["menuCommandCount"] == metrics["detail"]["commandCount"], metrics
                assert metrics["menuActiveCommandCount"] >= 1, metrics
                assert metrics["menuActiveCommandCount"] == metrics["detail"]["activeCommandCount"], metrics
                assert metrics["menuRect"]["width"] >= 80 and metrics["menuRect"]["height"] >= 24, metrics

            host_phase("shortcuts")
            shortcuts = wait_viewer_phase("help-shortcuts")
            assert shortcuts["shortcutsOpen"] is True, shortcuts
            assert "Keyboard Shortcuts and Legends" in shortcuts["shortcutsText"], shortcuts

            host_phase("about")
            about = wait_viewer_phase("help-about")
            assert any(title.endswith("About") for title in about["modalTitles"]) or "About" in about["modalText"], about
            assert "Keiven Chang" in about["text"], about

            host_phase("shareModal")
            share_modal = wait_viewer_phase("share-modal")
            assert "share-open" in share_modal["modalClass"], share_modal
            assert any("YO!share" in title for title in share_modal["modalTitles"]) or "YO!share" in share_modal["modalText"], share_modal
            assert "debug upload on" in share_modal["text"] or "Debug/profiling upload" in share_modal["text"], share_modal
            assert share_modal["hasSecretToken"] is False, share_modal
            assert created["token"] not in share_modal["text"], share_modal

            host_phase("info", "info")
            info = wait_viewer_phase("info-info")
            assert "YO!info" in info["text"] or "Session" in info["text"], info

            host_phase("info", "yoagent")
            yoagent = wait_viewer_phase("info-yoagent")
            assert "YO!agent" in yoagent["text"] or "Ask YO!agent" in yoagent["text"], yoagent

        finder_cases = [
            ("files", "sync", "az", "none"),
            ("files", "fixed", "za", "date"),
            ("files", "sync", "newest", "relative"),
            ("files", "fixed", "oldest", "none"),
            ("diff", "sync", "oldest", "relative"),
            ("diff", "fixed", "az", "date"),
            ("files", "sync", "az", "relative"),
        ]
        if matrix_section == "finder":
            for mode, root_mode, sort_mode, date_mode in finder_cases:
                host = host_phase("finder", mode, root_mode, sort_mode, date_mode)
                metrics = wait_viewer_phase(f"finder-{mode}-{root_mode}-{sort_mode}-{date_mode}")
                assert host["finderMode"] == mode, host
                assert host["rootMode"] == root_mode, host
                assert host["dateMode"] == date_mode, host
                assert metrics["detail"]["finderMode"] == mode, metrics
                assert metrics["detail"]["rootMode"] == root_mode, metrics
                assert metrics["detail"]["dateMode"] == date_mode, metrics
                assert metrics["detail"]["finderPanelCount"] <= 3, metrics
                if mode == "diff":
                    assert host["sessionFilesSortMode"] == sort_mode, host
                else:
                    assert host["treeSortMode"] == sort_mode, host
                if date_mode == "relative":
                    assert "Ago" in metrics["text"] or "date=relative/Ago" in metrics["text"], metrics

            for visible, mode in [(False, "files"), (True, "files"), (False, "diff"), (True, "diff")]:
                host = host_phase("visible", visible, mode)
                metrics = wait_viewer_phase(f"finder-{'show' if visible else 'hide'}-{mode}")
                assert host["finderOpen"] is visible, host
                assert metrics["detail"]["finderOpen"] is visible, metrics
                assert metrics["detail"]["finderPanelCount"] <= 3, metrics

            host_phase("staleDifferPrecondition")
            stale_differ = wait_viewer_phase("finder-restore-stale-diff")
            assert stale_differ["staleDifferSentinel"] is True, stale_differ
            assert stale_differ["changesPanelCount"] >= 1, stale_differ
            automatic_restore = host_phase("automaticFinderRestore")
            assert automatic_restore["topologyPublished"] is True, automatic_restore
            restored_finder = wait_viewer_phase("finder-restore-topology-keyframe", timeout=14)
            assert restored_finder["detail"]["finderOpen"] is True, restored_finder
            assert restored_finder["detail"]["finderMode"] == "files", restored_finder
            assert restored_finder["detail"]["finderPanelCount"] <= 3, restored_finder
            assert restored_finder["staleDifferSentinel"] is False, restored_finder
            assert restored_finder["changesPanelCount"] <= 1, restored_finder

        if matrix_section == "resilience":
            before_rapid = viewer_state()
            rapid_loop = host_phase("finderToggleLoop", 6, 0, "rapid")
            assert rapid_loop["finderOpen"] is True, rapid_loop
            rapid = wait_viewer_phase("finder-toggle-loop-rapid", timeout=14)
            assert rapid["detail"]["finderOpen"] is True, rapid
            assert rapid["detail"]["count"] == 6, rapid
            assert_terminal_health(rapid)
            assert rapid["droppedFrames"] == before_rapid["droppedFrames"], json.dumps({
            "beforeDropped": before_rapid["droppedFrames"],
            "afterDropped": rapid["droppedFrames"],
            "lastReplayError": rapid["lastReplayError"],
            "keyframeRequests": rapid["keyframeRequests"],
            "keyframeRequestsSuppressed": rapid["keyframeRequestsSuppressed"],
            "staleFrames": rapid["staleFrames"],
            "status": rapid["status"],
            }, sort_keys=True)
            assert rapid["keyframeRequests"] == before_rapid["keyframeRequests"], {"before": before_rapid, "after": rapid}

            before_paused = viewer_state()
            paused_loop = host_phase("finderToggleLoop", 6, 140, "paused")
            assert paused_loop["finderOpen"] is True, paused_loop
            paused = wait_viewer_phase("finder-toggle-loop-paused", timeout=16)
            assert paused["detail"]["finderOpen"] is True, paused
            assert paused["detail"]["count"] == 6, paused
            assert paused["detail"]["pauseMs"] == 140, paused
            assert_terminal_health(paused)
            assert paused["droppedFrames"] == before_paused["droppedFrames"], json.dumps({
            "beforeDropped": before_paused["droppedFrames"],
            "afterDropped": paused["droppedFrames"],
            "lastReplayError": paused["lastReplayError"],
            "keyframeRequests": paused["keyframeRequests"],
            "keyframeRequestsSuppressed": paused["keyframeRequestsSuppressed"],
            "staleFrames": paused["staleFrames"],
            "status": paused["status"],
            }, sort_keys=True)
            assert paused["keyframeRequests"] == before_paused["keyframeRequests"], {"before": before_paused, "after": paused}

            before_safari = viewer_state()
            safari_loop = host_phase("finderToggleLoop", 3, 2000, "safari-cadence", True)
            assert safari_loop["finderOpen"] is True, safari_loop
            assert safari_loop["geometryDigests"] == 3, safari_loop
            safari = wait_viewer_phase("finder-toggle-loop-safari-cadence", timeout=20)
            assert safari["detail"]["finderOpen"] is True, safari
            assert safari["detail"]["count"] == 3, safari
            assert safari["detail"]["pauseMs"] == 2000, safari
            assert safari["detail"]["geometryDigests"] == 3, safari
            assert_terminal_health(safari)
            assert safari["droppedFrames"] == before_safari["droppedFrames"], json.dumps({
            "beforeDropped": before_safari["droppedFrames"],
            "afterDropped": safari["droppedFrames"],
            "lastReplayError": safari["lastReplayError"],
            "keyframeRequests": safari["keyframeRequests"],
            "keyframeRequestsSuppressed": safari["keyframeRequestsSuppressed"],
            "staleFrames": safari["staleFrames"],
            "status": safari["status"],
            }, sort_keys=True)
            assert safari["keyframeRequests"] == before_safari["keyframeRequests"], {"before": before_safari, "after": safari}

        if matrix_section == "popovers":
            host_phase("expandWithFinder")
            expanded = wait_viewer_phase("terminal-expand-with-finder")
            assert expanded["finderPanelCount"] <= 1, expanded
            assert_terminal_health(expanded)

            browser.execute_script("tabPopoverShowDelayMs = 0; tabPopoverFollowDelayMs = 0;")
            host_tab = browser.execute_script(
            """
            return Array.from(document.querySelectorAll('.dockview-pane-tab, .pane-tab'))
              .find(tab => tab.__yolomuxDetachedPopover || tab.querySelector(':scope > .session-popover'));
            """
            )
            assert host_tab is not None
            fast_pointer_actions(browser).move_to_element(host_tab).perform()
            host_phase("tabPopover")
            tab = wait_viewer_phase("tab-hover-popover")
            assert tab["tabPopoverText"].strip(), tab
            expected_rect = tab["detail"].get("tabPopoverRect")
            actual_rect = tab.get("tabPopoverRect")
            assert expected_rect and actual_rect, tab
            for key in ("left", "top", "width", "height"):
                assert abs(int(actual_rect[key]) - int(expected_rect[key])) <= 2, json.dumps(tab, indent=2, sort_keys=True)
            assert tab["tabPopoverStyle"].get("left") == tab["detail"]["tabPopoverStyle"]["left"], tab
            assert tab["tabPopoverStyle"].get("top") == tab["detail"]["tabPopoverStyle"]["top"], tab
            actual_height = float(str(tab["tabPopoverStyle"].get("height") or "0").removesuffix("px"))
            expected_height = float(str(tab["detail"]["tabPopoverStyle"].get("height") or "0").removesuffix("px"))
            assert abs(actual_height - expected_height) <= 2, tab

            host_phase("repoPopover")
            repo = wait_viewer_phase("finder-repo-popover")
            assert "feature/share-matrix" in repo["repoPopoverText"], repo
            assert "2 ahead" in repo["repoPopoverText"], repo
            assert "3 dirty" in repo["repoPopoverText"], repo

        final_metrics = viewer_state()
        max_expected_keyframe_requests = int(final_metrics["elapsedMs"] // 10000) + 1
        assert final_metrics["status"] == "mirrored", final_metrics
        assert final_metrics["keyframeRequestInFlight"] is False, final_metrics
        assert final_metrics["keyframeRequestBackoffMs"] == 0, final_metrics
        assert final_metrics["keyframeRequests"] <= max_expected_keyframe_requests, final_metrics
        assert final_metrics["rootChildren"] > 0, final_metrics
        # The chrome section ends in YO!agent, whose pane intentionally replaces the visible terminal.
        # The initial replay assertion above already proves its terminal placeholder/socket contract.
        if matrix_section != "chrome":
            assert_terminal_health(final_metrics)
    finally:
        viewer.quit()
        stop_browser_share_server(server, thread)
        stop_isolated_browser_share_app(runtime)


def test_share_replay_shell_applies_static_keyframe_and_rejects_unsafe_nodes(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-replay-keyframe",
        "mode": "ro",
        "session": "6",
        "sessions": ["6"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:6",
        "uiState": {"viewport": {"width": 1440, "height": 900}},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=1#t=valid-share-token",
        sessions=["6"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "token": "valid-share-token", "mode": "ro", "expiresAt": 4102444800.0, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.body.classList.contains('share-replay-shell')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          await frame();
          window.__replayHandlerRan = false;
          window.__replayScriptRan = false;
          const goodPayload = {
            digest: 'sha256:good',
            viewport: {width: 1440, height: 900},
            root: {
              nodeId: 1,
              tag: 'div',
              attrs: {id: 'appRoot', class: 'app-root host-root'},
              children: [
                {nodeId: 2, tag: 'section', attrs: {class: 'file-explorer-panel', 'data-testid': 'finder'}, text: 'Finder mirrored'},
                {nodeId: 3, tag: 'article', attrs: {class: 'file-editor-panel', 'data-testid': 'editor'}, children: [
                  {nodeId: 4, tag: 'span', attrs: {class: 'editor-text'}, text: 'Editor mirrored text'}
                ]},
                {nodeId: 5, tag: 'div', attrs: {class: 'app-menu open', 'data-testid': 'menu'}, children: [
                  {nodeId: 6, tag: 'button', attrs: {'data-testid': 'menu-button', onclick: 'window.__replayHandlerRan = true'}, text: 'Menu action'}
                ]},
	                {nodeId: 7, tag: 'section', attrs: {class: 'preferences-panel', 'data-testid': 'prefs'}, text: 'Preferences mirrored'},
	                {nodeId: 8, tag: 'a', attrs: {'data-testid': 'unsafe-link', href: 'javascript:alert(1)', shareToken: 'secret-token'}, text: 'unsafe link'},
	                {nodeId: 9, tag: 'div', attrs: {class: 'share-terminal-placeholder', 'data-share-terminal-placeholder': '6', 'data-session': '6', 'data-rows': '24', 'data-cols': '80'}, children: []},
	                {nodeId: 10, tag: 'think', attrs: {'data-testid': 'unsupported-tag'}, text: 'Unsupported custom tag text'}
	              ]
            },
            terminals: [{placeholderId: 'term-ph-6', session: '6', rows: 24, cols: 80, terminalEpoch: 3}]
          };
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 1, sequence: 1, payload: goodPayload});
          await frame();
          const root = document.getElementById('appRoot');
          const editorText = document.querySelector('.editor-text');
          if (!editorText) {
            done({
              missingEditor: true,
              rootText: root?.textContent || '',
              rootStatus: root?.dataset?.shareReplayStatus || '',
              rootDigest: root?.dataset?.shareReplayDigest || '',
              shellStatus: shareReplayShellState.status,
              nodeMapSize: shareReplayNodeMap.size,
            });
            return;
          }
          const range = document.createRange();
          range.selectNodeContents(editorText);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          const selectedText = selection.toString();
	          const button = document.querySelector('[data-testid="menu-button"]');
	          button?.click();
	          const unsafeLink = document.querySelector('[data-testid="unsafe-link"]');
	          const unsupportedNode = document.querySelector('[data-testid="unsupported-tag"]');
	          const placeholder = document.querySelector('[data-share-terminal-placeholder="6"]');
          const beforeBadText = root.textContent;
          const beforeBadMapSize = shareReplayNodeMap.size;
          const badPayload = {
            digest: 'sha256:bad',
            viewport: {width: 1200, height: 700},
            root: {
              nodeId: 1,
              tag: 'div',
              attrs: {id: 'appRoot', class: 'app-root bad-root'},
              children: [
                {nodeId: 2, tag: 'script', text: 'window.__replayScriptRan = true'}
              ]
            }
          };
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 1, sequence: 2, payload: badPayload});
          await frame();
          done({
            rootClass: root.className,
            rootNodeId: root.dataset.shareReplayNodeId || '',
            rootStatus: root.dataset.shareReplayStatus || '',
            rootDigest: root.dataset.shareReplayDigest || '',
            finderText: document.querySelector('[data-testid="finder"]')?.textContent || '',
            editorText: editorText?.textContent || '',
            menuText: document.querySelector('[data-testid="menu"]')?.textContent || '',
            prefsText: document.querySelector('[data-testid="prefs"]')?.textContent || '',
            selectedText,
            buttonHasOnclick: button?.hasAttribute('onclick') || false,
            handlerRan: window.__replayHandlerRan === true,
	            unsafeHref: unsafeLink?.getAttribute('href') || '',
	            unsafeTokenAttr: unsafeLink?.getAttribute('shareToken') || unsafeLink?.getAttribute('share-token') || '',
	            unsupportedTagName: unsupportedNode?.tagName || '',
	            unsupportedText: unsupportedNode?.textContent || '',
	            unsupportedThinkCount: document.querySelectorAll('think').length,
	            placeholderSession: placeholder?.dataset.shareTerminalPlaceholder || '',
            placeholderRows: placeholder?.dataset.rows || '',
            nodeMapSize: beforeBadMapSize,
            terminalPlaceholderCount: shareReplayTerminalPlaceholders.size,
            afterBadText: root.textContent,
            afterBadMapSize: shareReplayNodeMap.size,
            scriptRan: window.__replayScriptRan === true,
            shellStatus: shareReplayShellState.status,
            beforeBadText,
            mirrorStatusText: document.querySelector('.share-viewer-mirror-status')?.textContent || '',
          });
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics.get("missingEditor") is not True, metrics
    assert "host-root" in metrics["rootClass"], metrics
    assert "share-replay-root" in metrics["rootClass"], metrics
    assert metrics["rootNodeId"] == "1", metrics
    assert metrics["rootStatus"] == "error", metrics
    assert metrics["rootDigest"] == "sha256:bad", metrics
    assert metrics["finderText"] == "Finder mirrored", metrics
    assert metrics["editorText"] == "Editor mirrored text", metrics
    assert "Menu action" in metrics["menuText"], metrics
    assert metrics["prefsText"] == "Preferences mirrored", metrics
    assert metrics["selectedText"] == "Editor mirrored text", metrics
    assert metrics["buttonHasOnclick"] is False, metrics
    assert metrics["handlerRan"] is False, metrics
    assert metrics["unsafeHref"] == "", metrics
    assert metrics["unsafeTokenAttr"] == "", metrics
    assert metrics["unsupportedTagName"] == "SPAN", metrics
    assert metrics["unsupportedText"] == "Unsupported custom tag text", metrics
    assert metrics["unsupportedThinkCount"] == 0, metrics
    assert metrics["placeholderSession"] == "6", metrics
    assert metrics["placeholderRows"] == "24", metrics
    assert metrics["nodeMapSize"] == 10, metrics
    assert metrics["terminalPlaceholderCount"] == 1, metrics
    assert metrics["afterBadText"] == metrics["beforeBadText"], metrics
    assert metrics["afterBadMapSize"] == metrics["nodeMapSize"], metrics
    assert metrics["scriptRan"] is False, metrics
    assert metrics["shellStatus"] == "error", metrics
    assert metrics["mirrorStatusText"], metrics


def test_share_replay_popups_menus_and_modals_use_normal_dom_replay(browser, tmp_path):
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=1",
        sessions=["6"],
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof shareCreateDomKeyframePayload === 'function' && document.getElementById('sessionButtons')")
    )
    payloads = browser.execute_script(
        """
        const overlay = appOverlayRootElement();
        overlay.replaceChildren();
        const sessionButtons = document.getElementById('sessionButtons');
        const addMenu = (className, text) => {
          const wrapper = document.createElement('div');
          wrapper.className = className;
          const popover = document.createElement('div');
          popover.className = className.includes('topbar-language-menu') ? 'app-menu-popover topbar-language-popover' : 'app-menu-popover';
          popover.textContent = text;
          wrapper.appendChild(popover);
          sessionButtons.appendChild(wrapper);
          return wrapper;
        };
        const appMenu = addMenu('app-menu open', 'P4.3 app menu row');
        const languageMenu = addMenu('app-menu topbar-language-menu open', 'P4.3 language picker row');
        const contextMenu = document.createElement('div');
        contextMenu.className = 'terminal-context-menu file-context-menu';
        contextMenu.textContent = 'P4.3 context menu row';
        overlay.appendChild(contextMenu);
        const tabPopover = document.createElement('div');
        tabPopover.className = 'pane-tab-detached-popover popover-open';
        tabPopover.textContent = 'P4.3 tab popover row';
        overlay.appendChild(tabPopover);
          const {modal, body} = openShareModalChrome('YO!share');
          body.innerHTML = '<div>P4.3 YO!share modal row</div><input data-share-secret value="/share/abc123#t=secret-token">';
          const openPayload = shareCreateDomKeyframePayload('popup-open');
          const openText = JSON.stringify(openPayload);
          const contextParentId = contextMenu.parentElement?.id || '';
          const tabParentId = tabPopover.parentElement?.id || '';
          appMenu.remove();
          languageMenu.remove();
          overlay.replaceChildren();
        modal.classList.remove('open', 'share-open', 'about-open');
        body.textContent = '';
        const closePayload = shareCreateDomKeyframePayload('popup-close');
        return {
          openPayload,
          closePayload,
          openContains: {
            appMenu: openText.includes('P4.3 app menu row'),
            language: openText.includes('P4.3 language picker row'),
            context: openText.includes('P4.3 context menu row'),
            tab: openText.includes('P4.3 tab popover row'),
            modal: openText.includes('P4.3 YO!share modal row'),
            secret: openText.includes('secret-token') || openText.includes('/share/abc123'),
          },
          contextParentId,
          tabParentId,
        };
        """
    )
    assert payloads["openContains"] == {
        "appMenu": True,
        "language": True,
        "context": True,
        "tab": True,
        "modal": True,
        "secret": False,
    }, payloads
    assert payloads["contextParentId"] == "appOverlayRoot", payloads
    assert payloads["tabParentId"] == "appOverlayRoot", payloads

    share_bootstrap = {
        "view": True,
        "id": "share-replay-popups",
        "mode": "ro",
        "session": "6",
        "sessions": ["6"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:6",
        "uiState": {"viewport": {"width": 1200, "height": 700}},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=1#t=valid-share-token",
        sessions=["6"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "token": "valid-share-token", "mode": "ro", "expiresAt": 4102444800.0, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.body.classList.contains('share-replay-shell')")
    )
    metrics = browser.execute_async_script(
        """
        const openPayload = arguments[0];
        const closePayload = arguments[1];
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 22, sequence: 1, payload: openPayload});
          await frame();
          const openText = document.getElementById('appRoot')?.textContent || '';
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 22, sequence: 2, payload: closePayload});
          await frame();
          const closeText = document.getElementById('appRoot')?.textContent || '';
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 22, sequence: 1, payload: openPayload});
          await frame();
          const staleText = document.getElementById('appRoot')?.textContent || '';
          done({
            open: {
              appMenu: openText.includes('P4.3 app menu row'),
              language: openText.includes('P4.3 language picker row'),
              context: openText.includes('P4.3 context menu row'),
              tab: openText.includes('P4.3 tab popover row'),
              modal: openText.includes('P4.3 YO!share modal row'),
              secret: openText.includes('secret-token') || openText.includes('/share/abc123'),
              overlayInRoot: document.getElementById('appOverlayRoot')?.parentElement?.id === 'appRoot',
            },
            closed: {
              appMenu: closeText.includes('P4.3 app menu row'),
              language: closeText.includes('P4.3 language picker row'),
              context: closeText.includes('P4.3 context menu row'),
              tab: closeText.includes('P4.3 tab popover row'),
              modal: closeText.includes('P4.3 YO!share modal row'),
            },
            stale: {
              appMenu: staleText.includes('P4.3 app menu row'),
              language: staleText.includes('P4.3 language picker row'),
              context: staleText.includes('P4.3 context menu row'),
              tab: staleText.includes('P4.3 tab popover row'),
              modal: staleText.includes('P4.3 YO!share modal row'),
            },
            sequence: shareReplayLastSequence,
            status: shareReplayShellState.status,
            popupMirrorLayer: Boolean(document.querySelector('.share-popup-mirror-layer')),
          });
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        payloads["openPayload"],
        payloads["closePayload"],
    )
    assert "error" not in metrics, metrics
    assert metrics["open"] == {
        "appMenu": True,
        "language": True,
        "context": True,
        "tab": True,
        "modal": True,
        "secret": False,
        "overlayInRoot": True,
    }, metrics
    assert metrics["closed"] == {
        "appMenu": False,
        "language": False,
        "context": False,
        "tab": False,
        "modal": False,
    }, metrics
    assert metrics["stale"] == metrics["closed"], metrics
    assert metrics["sequence"] == 2, metrics
    assert metrics["status"] == "mirrored", metrics
    assert metrics["popupMirrorLayer"] is False, metrics


def test_share_replay_shell_rebinds_xterm_to_moving_terminal_placeholder(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-replay-terminal-placeholder",
        "mode": "ro",
        "session": "6",
        "sessions": ["6"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:6",
        "uiState": {"viewport": {"width": 1200, "height": 700}},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=1#t=valid-share-token",
        sessions=["6"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "token": "valid-share-token", "mode": "ro", "expiresAt": 4102444800.0, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.body.classList.contains('share-replay-shell')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFor = window.__yolomuxTestWaitFor;
          const keyframe = (label, terminalEpoch, includeTerminal = true) => ({
            digest: `sha256:${label}`,
            viewport: {width: 1200, height: 700},
            root: {
              nodeId: 1,
              tag: 'div',
              attrs: {id: 'appRoot', class: 'app-root host-root'},
              children: [
                {nodeId: 2, tag: 'section', attrs: {'data-testid': label, class: 'panel'}, children: includeTerminal ? [
                  {nodeId: 3, tag: 'div', attrs: {class: 'share-terminal-placeholder', 'data-share-terminal-placeholder': '6', 'data-session': '6', 'data-rows': '24', 'data-cols': '80'}, children: []}
                ] : []}
              ]
            },
            terminals: includeTerminal ? [{placeholderId: 'term-ph-6', session: '6', rows: 24, cols: 80, terminalEpoch}] : []
          });
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 1, sequence: 1, payload: keyframe('left-pane', 1)});
          const mounted = await waitFor(() => Boolean(terminals.get('6')?.socket?.readyState === WebSocket.OPEN
            && document.querySelector('[data-testid="left-pane"] #term-6 .xterm')));
          const firstItem = terminals.get('6');
          const firstTerm = firstItem?.term || null;
          const firstElement = firstTerm?.element || null;
          const firstContainer = firstItem?.container || null;
          const socketsAfterMount = (window.__bootSockets || []).filter(url => url.includes('/ws/share-view') && url.includes('session=6')).length;
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 1, sequence: 2, payload: keyframe('right-pane', 2)});
          const moved = await waitFor(() => terminals.get('6')?.term === firstTerm
            && terminals.get('6')?.container === document.querySelector('[data-testid="right-pane"] #term-6')
            && document.querySelector('[data-testid="right-pane"] #term-6 .xterm') === firstElement);
          const afterMoveItem = terminals.get('6');
          const socketsAfterMove = (window.__bootSockets || []).filter(url => url.includes('/ws/share-view') && url.includes('session=6')).length;
          const placeholderCountAfterMove = shareReplayTerminalPlaceholders.size;
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 1, sequence: 3, payload: keyframe('empty-pane', 3, false)});
          await frame();
          const afterRemoveItem = terminals.get('6');
          done({
            mounted,
            moved,
            openedCount: window.__terminalOpened,
            sameTermAfterMove: afterMoveItem?.term === firstTerm,
            sameElementAfterMove: afterMoveItem?.term?.element === firstElement,
            oldContainerConnected: firstContainer?.isConnected === true,
            currentContainerTestId: afterMoveItem?.container?.closest('[data-testid]')?.dataset?.testid || '',
            rowsAfterMove: afterMoveItem?.term?.rows || 0,
            colsAfterMove: afterMoveItem?.term?.cols || 0,
            socketsAfterMount,
            socketsAfterMove,
            placeholderCountAfterMove,
            terminalDetachedAfterRemove: firstElement ? !document.body.contains(firstElement) : false,
            containerConnectedAfterRemove: afterRemoveItem?.container?.isConnected === true,
            sameTermAfterRemove: afterRemoveItem?.term === firstTerm,
            placeholderCountAfterRemove: shareReplayTerminalPlaceholders.size,
            socketOpenAfterRemove: afterRemoveItem?.socket?.readyState === WebSocket.OPEN,
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          });
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics
    assert metrics["mounted"] is True, metrics
    assert metrics["moved"] is True, metrics
    assert metrics["openedCount"] == 1, metrics
    assert metrics["sameTermAfterMove"] is True, metrics
    assert metrics["sameElementAfterMove"] is True, metrics
    assert metrics["oldContainerConnected"] is False, metrics
    assert metrics["currentContainerTestId"] == "right-pane", metrics
    assert metrics["rowsAfterMove"] == 24, metrics
    assert metrics["colsAfterMove"] == 80, metrics
    assert metrics["socketsAfterMount"] == 1, metrics
    assert metrics["socketsAfterMove"] == 1, metrics
    assert metrics["placeholderCountAfterMove"] == 1, metrics
    assert metrics["terminalDetachedAfterRemove"] is True, metrics
    assert metrics["containerConnectedAfterRemove"] is False, metrics
    assert metrics["sameTermAfterRemove"] is True, metrics
    assert metrics["placeholderCountAfterRemove"] == 0, metrics
    assert metrics["socketOpenAfterRemove"] is True, metrics


def test_share_replay_delta_rebinds_xterm_to_moved_terminal_placeholder(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-replay-terminal-delta-placeholder",
        "mode": "ro",
        "session": "6",
        "sessions": ["6"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:6",
        "uiState": {"viewport": {"width": 1200, "height": 700}},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=1#t=valid-share-token",
        sessions=["6"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "token": "valid-share-token", "mode": "ro", "expiresAt": 4102444800.0, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.body.classList.contains('share-replay-shell')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFor = window.__yolomuxTestWaitFor;
          const keyframe = {
            digest: '',
            viewport: {width: 1200, height: 700},
            root: {
              nodeId: 1,
              tag: 'div',
              attrs: {id: 'appRoot', class: 'app-root host-root'},
              children: [
                {nodeId: 2, tag: 'section', attrs: {'data-testid': 'left-pane'}, children: [
                  {nodeId: 3, tag: 'div', attrs: {class: 'share-terminal-placeholder', 'data-share-terminal-placeholder': '6', 'data-session': '6', 'data-rows': '24', 'data-cols': '80'}, children: []}
                ]}
              ]
            },
            terminals: [{placeholderId: 'term-ph-6', session: '6', rows: 24, cols: 80, terminalEpoch: 1}]
          };
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 8, sequence: 40, payload: keyframe});
          const mounted = await waitFor(() => Boolean(terminals.get('6')?.socket?.readyState === WebSocket.OPEN
            && document.querySelector('[data-testid="left-pane"] #term-6 .xterm')));
          const firstItem = terminals.get('6');
          const firstTerm = firstItem?.term || null;
          const firstElement = firstTerm?.element || null;
          const originalWrite = firstTerm.write.bind(firstTerm);
          const writes = [];
          firstTerm.write = data => {
            const text = data instanceof Uint8Array ? new TextDecoder().decode(data) : String(data);
            writes.push(text);
            return originalWrite(data);
          };
          const socketsAfterMount = (window.__bootSockets || []).filter(url => url.includes('/ws/share-view') && url.includes('session=6')).length;
          applyShareUiMessage({
            ch: 'ui',
            type: shareMirrorProtocol.frames.domDelta,
            sender: 'host',
            epoch: 8,
            baseSequence: 40,
            sequence: 41,
            payload: {
              mutations: [{
                kind: 'childList',
                target: 1,
                removed: [2],
                added: [
                  {nodeId: 4, tag: 'section', attrs: {'data-testid': 'right-pane'}, children: [
                    {nodeId: 5, tag: 'div', attrs: {class: 'share-terminal-placeholder', 'data-share-terminal-placeholder': '6', 'data-session': '6', 'data-rows': '24', 'data-cols': '80'}, children: []}
                  ]}
                ],
              }],
              terminals: [{placeholderId: 'term-ph-6', session: '6', rows: 24, cols: 80, terminalEpoch: 2}]
            }
          });
          const moved = await waitFor(() => terminals.get('6')?.term === firstTerm
            && terminals.get('6')?.container === document.querySelector('[data-testid="right-pane"] #term-6')
            && document.querySelector('[data-testid="right-pane"] #term-6 .xterm') === firstElement);
          const afterMoveItem = terminals.get('6');
          handleShareViewSocketMessage('6', afterMoveItem, JSON.stringify({ch: 'term', pane: '6', data: btoa('terminal still visible after delta')}));
          await frame();
          const socketsAfterMove = (window.__bootSockets || []).filter(url => url.includes('/ws/share-view') && url.includes('session=6')).length;
          done({
            mounted,
            moved,
            status: shareReplayShellState.status,
            dropped: shareReplayDroppedFrames,
            requests: shareReplayKeyframeRequestCount,
            sameTerm: afterMoveItem?.term === firstTerm,
            sameElement: afterMoveItem?.term?.element === firstElement,
            currentContainerTestId: afterMoveItem?.container?.closest('[data-testid]')?.dataset?.testid || '',
            rows: afterMoveItem?.term?.rows || 0,
            cols: afterMoveItem?.term?.cols || 0,
            hostSize: shareHostTerminalSize('6'),
            writes,
            socketsAfterMount,
            socketsAfterMove,
            openedCount: window.__terminalOpened,
            placeholderCount: shareReplayTerminalPlaceholders.size,
            terminalHealthy: shareReplayHealthDiagnostics().terminalPlaceholders.healthy,
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          });
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics
    assert metrics["mounted"] is True, metrics
    assert metrics["moved"] is True, metrics
    assert metrics["status"] == "mirrored", metrics
    assert metrics["dropped"] == 0, metrics
    assert metrics["requests"] == 0, metrics
    assert metrics["sameTerm"] is True, metrics
    assert metrics["sameElement"] is True, metrics
    assert metrics["currentContainerTestId"] == "right-pane", metrics
    assert metrics["rows"] == 24, metrics
    assert metrics["cols"] == 80, metrics
    assert metrics["hostSize"] == {"rows": 24, "cols": 80}, metrics
    assert metrics["writes"] == ["terminal still visible after delta"], metrics
    assert metrics["socketsAfterMount"] == 1, metrics
    assert metrics["socketsAfterMove"] == 1, metrics
    assert metrics["openedCount"] == 1, metrics
    assert metrics["placeholderCount"] == 1, metrics
    assert metrics["terminalHealthy"] is True, metrics


def test_share_replay_terminal_host_resize_preserves_existing_repaint_bytes(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-replay-terminal-resize",
        "mode": "ro",
        "session": "6",
        "sessions": ["6"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:6",
        "uiState": {"viewport": {"width": 1200, "height": 700}},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=1#t=valid-share-token",
        sessions=["6"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "token": "valid-share-token", "mode": "ro", "expiresAt": 4102444800.0, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.body.classList.contains('share-replay-shell')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFor = window.__yolomuxTestWaitFor;
          const keyframe = {
            digest: 'sha256:resize-terminal',
            viewport: {width: 1200, height: 700},
            root: {
              nodeId: 1,
              tag: 'div',
              attrs: {id: 'appRoot', class: 'app-root host-root'},
              children: [
                {nodeId: 2, tag: 'section', attrs: {'data-testid': 'terminal-pane'}, children: [
                  {nodeId: 3, tag: 'div', attrs: {class: 'share-terminal-placeholder', 'data-share-terminal-placeholder': '6', 'data-session': '6', 'data-rows': '24', 'data-cols': '80'}, children: []}
                ]}
              ]
            },
            terminals: [{placeholderId: 'term-ph-6', session: '6', rows: 24, cols: 80, terminalEpoch: 1}]
          };
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 1, sequence: 1, payload: keyframe});
          const mounted = await waitFor(() => Boolean(terminals.get('6')?.socket?.readyState === WebSocket.OPEN
            && document.querySelector('#term-6 .xterm')));
          const item = terminals.get('6');
          const calls = [];
          const originalResize = item.term.resize.bind(item.term);
          const originalWrite = item.term.write.bind(item.term);
          const originalRefresh = item.term.refresh?.bind(item.term);
          item.term.resize = (cols, rows) => {
            calls.push(['resize', cols, rows]);
            return originalResize(cols, rows);
          };
          item.term.screenText = '';
          item.term.reset = () => {
            calls.push(['reset']);
            item.term.screenText = '';
          };
          item.term.refresh = (start, end) => {
            calls.push(['refresh', start, end]);
            return originalRefresh ? originalRefresh(start, end) : undefined;
          };
          item.term.write = data => {
            const text = data instanceof Uint8Array ? new TextDecoder().decode(data) : String(data);
            calls.push(['write', text]);
            item.term.screenText = `${item.term.screenText || ''}${text}`;
            return originalWrite(data);
          };
          handleShareViewSocketMessage('6', item, JSON.stringify({ch: 'term', pane: '6', data: btoa('existing host screen')}));
          await frame();
          const textBeforeResize = item.term.screenText || '';
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.terminalHostResize, sender: 'host', epoch: 1, sequence: 2, payload: {session: '6', rows: 30, cols: 100}});
          await waitFor(
            () => {
              const resize = calls.findIndex(call => call[0] === 'resize');
              return resize >= 0 && calls.some((call, index) => call[0] === 'refresh' && index > resize);
            },
            {timeoutMs: 1000, description: 'post-resize terminal refresh'}
          );
          const resizeIndex = calls.findIndex(call => call[0] === 'resize');
          const resetIndex = calls.findIndex(call => call[0] === 'reset');
          const writeIndex = calls.findIndex(call => call[0] === 'write');
          const refreshIndex = calls.findIndex(call => call[0] === 'refresh');
          const terminalEntry = shareReplayHealthDiagnostics().terminalPlaceholders.entries.find(entry => entry.session === '6') || {};
          done({
            mounted,
            rows: item.term.rows,
            cols: item.term.cols,
            hostSize: shareHostTerminalSize('6'),
            calls,
            resizeIndex,
            resetIndex,
            writeIndex,
            refreshIndex,
            resetAfterWrite: resetIndex > writeIndex,
            refreshAfterResize: calls.some((call, index) => call[0] === 'refresh' && index > resizeIndex),
            writeText: calls.find(call => call[0] === 'write')?.[1] || '',
            textBeforeResize,
            textAfterResize: item.term.screenText || '',
            bytesReceived: item.shareTerminalBytesReceived === true,
            byteCount: item.shareTerminalByteCount || 0,
            skippedResetCount: item.shareTerminalSkippedResetCount || 0,
            terminalStreamStatus: terminalEntry.streamStatus || '',
            terminalReceivedBytes: terminalEntry.receivedBytes === true,
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          });
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics
    assert metrics["mounted"] is True, metrics
    assert metrics["rows"] == 30, metrics
    assert metrics["cols"] == 100, metrics
    assert metrics["hostSize"] == {"rows": 30, "cols": 100}, metrics
    assert ["resize", 100, 30] in metrics["calls"], metrics
    assert metrics["writeText"] == "existing host screen", metrics
    assert metrics["textBeforeResize"] == "existing host screen", metrics
    assert metrics["textAfterResize"] == "existing host screen", metrics
    assert metrics["resetAfterWrite"] is False, metrics
    assert 0 <= metrics["writeIndex"] < metrics["resizeIndex"], metrics
    assert metrics["refreshAfterResize"] is True, metrics
    assert metrics["bytesReceived"] is True, metrics
    assert metrics["byteCount"] > 0, metrics
    assert metrics["skippedResetCount"] >= 1, metrics
    assert metrics["terminalStreamStatus"] == "received-bytes", metrics
    assert metrics["terminalReceivedBytes"] is True, metrics


def test_share_replay_gap_keeps_terminal_stream_host_sized(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-replay-terminal-backpressure",
        "mode": "ro",
        "session": "6",
        "sessions": ["6"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:6",
        "uiState": {"viewport": {"width": 1200, "height": 700}},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=1#t=valid-share-token",
        sessions=["6"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "token": "valid-share-token", "mode": "ro", "expiresAt": 4102444800.0, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.body.classList.contains('share-replay-shell')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFor = window.__yolomuxTestWaitFor;
          const keyframe = {
            digest: 'sha256:behind-terminal',
            viewport: {width: 1200, height: 700},
            root: {
              nodeId: 1,
              tag: 'div',
              attrs: {id: 'appRoot', class: 'app-root host-root'},
              children: [
                {nodeId: 2, tag: 'section', attrs: {'data-testid': 'terminal-pane'}, children: [
                  {nodeId: 3, tag: 'div', attrs: {class: 'share-terminal-placeholder', 'data-share-terminal-placeholder': '6', 'data-session': '6', 'data-rows': '24', 'data-cols': '80'}, children: []}
                ]}
              ]
            },
            terminals: [{placeholderId: 'term-ph-6', session: '6', rows: 24, cols: 80, terminalEpoch: 1}]
          };
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 5, sequence: 20, payload: keyframe});
          const mounted = await waitFor(() => Boolean(terminals.get('6')?.socket?.readyState === WebSocket.OPEN
            && document.querySelector('#term-6 .xterm')));
          const item = terminals.get('6');
          const firstTerm = item.term;
          const firstContainer = item.container;
          const originalWrite = item.term.write.bind(item.term);
          const writes = [];
          item.term.write = data => {
            const text = data instanceof Uint8Array ? new TextDecoder().decode(data) : String(data);
            writes.push(text);
            return originalWrite(data);
          };
          const beforeRequests = shareReplayKeyframeRequestCount;
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domDelta, sender: 'host', epoch: 5, baseSequence: 99, sequence: 100, payload: {pointer: {x: 32, y: 48, visible: true}}});
          handleShareViewSocketMessage('6', item, JSON.stringify({ch: 'term', pane: '6', data: btoa('terminal keeps streaming')}));
          await frame();
          const status = document.querySelector('.share-viewer-mirror-status');
          done({
            mounted,
            status: shareReplayShellState.status,
            statusText: status?.textContent || '',
            requestDelta: shareReplayKeyframeRequestCount - beforeRequests,
            dropped: shareReplayDroppedFrames,
            writes,
            rows: item.term.rows,
            cols: item.term.cols,
            hostSize: shareHostTerminalSize('6'),
            sameTerm: terminals.get('6')?.term === firstTerm,
            sameContainer: terminals.get('6')?.container === firstContainer,
            openedCount: window.__terminalOpened,
            socketOpen: item.socket?.readyState === WebSocket.OPEN,
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          });
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics
    assert metrics["mounted"] is True, metrics
    assert metrics["status"] == "mirrored", metrics
    assert metrics["statusText"] == "mirror synced", metrics
    assert metrics["requestDelta"] == 1, metrics
    assert metrics["dropped"] == 1, metrics
    assert metrics["writes"] == ["terminal keeps streaming"], metrics
    assert metrics["rows"] == 24, metrics
    assert metrics["cols"] == 80, metrics
    assert metrics["hostSize"] == {"rows": 24, "cols": 80}, metrics
    assert metrics["sameTerm"] is True, metrics
    assert metrics["sameContainer"] is True, metrics
    assert metrics["openedCount"] == 1, metrics
    assert metrics["socketOpen"] is True, metrics


def test_share_replay_shell_applies_only_contiguous_deltas(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-replay-delta",
        "mode": "ro",
        "session": "6",
        "sessions": ["6"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:6",
        "uiState": {"viewport": {"width": 1200, "height": 700}},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=1#t=valid-share-token",
        sessions=["6"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "token": "valid-share-token", "mode": "ro", "expiresAt": 4102444800.0, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.body.classList.contains('share-replay-shell')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          await frame();
          const keyframe = {
            digest: 'sha256:keyframe',
            viewport: {width: 1200, height: 700},
            root: {
              nodeId: 1,
              tag: 'div',
              attrs: {id: 'appRoot', class: 'app-root host-root'},
              children: [
                {nodeId: 2, tag: 'span', attrs: {'data-testid': 'delta-target'}, text: 'one'}
              ]
            },
            terminals: []
          };
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 7, sequence: 10, payload: keyframe});
          await frame();
          const target = document.querySelector('[data-testid="delta-target"]');
          const initialState = {epoch: shareReplayCurrentEpoch, sequence: shareReplayLastSequence, requests: shareReplayKeyframeRequestCount, text: target.textContent};
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domDelta, sender: 'host', epoch: 7, baseSequence: 10, sequence: 11, payload: {mutations: [{kind: 'characterData', target: 2, text: 'two'}]}});
          await frame();
          const afterGood = {epoch: shareReplayCurrentEpoch, sequence: shareReplayLastSequence, requests: shareReplayKeyframeRequestCount, text: target.textContent, status: shareReplayShellState.status};
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domDelta, sender: 'host', epoch: 6, baseSequence: 11, sequence: 12, payload: {mutations: [{kind: 'characterData', target: 2, text: 'stale'}]}});
          await frame();
          const afterStale = {epoch: shareReplayCurrentEpoch, sequence: shareReplayLastSequence, requests: shareReplayKeyframeRequestCount, suppressed: shareReplayKeyframeRequestSuppressedCount, stale: shareReplayStaleFrames, inFlight: shareReplayKeyframeInFlight, backoffMs: shareReplayKeyframeBackoffMs, text: target.textContent, status: shareReplayShellState.status};
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domDelta, sender: 'host', epoch: 7, baseSequence: 10, sequence: 13, payload: {mutations: [{kind: 'characterData', target: 2, text: 'gap'}]}});
          await frame();
          const afterGap = {epoch: shareReplayCurrentEpoch, sequence: shareReplayLastSequence, requests: shareReplayKeyframeRequestCount, suppressed: shareReplayKeyframeRequestSuppressedCount, dropped: shareReplayDroppedFrames, text: target.textContent, status: shareReplayShellState.status};
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domDelta, sender: 'host', epoch: 7, baseSequence: 13, sequence: 14, payload: {mutations: [{kind: 'characterData', target: 999, text: 'unknown'}]}});
          await frame();
          const afterUnknown = {epoch: shareReplayCurrentEpoch, sequence: shareReplayLastSequence, requests: shareReplayKeyframeRequestCount, suppressed: shareReplayKeyframeRequestSuppressedCount, dropped: shareReplayDroppedFrames, stale: shareReplayStaleFrames, text: target.textContent, status: shareReplayShellState.status};
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domDelta, sender: 'host', epoch: 7, baseSequence: 14, sequence: 15, payload: {digest: 'sha256:not-current-dom', mutations: [{kind: 'characterData', target: 2, text: 'digest mismatch'}]}});
          await frame();
          const afterDigest = {epoch: shareReplayCurrentEpoch, sequence: shareReplayLastSequence, requests: shareReplayKeyframeRequestCount, suppressed: shareReplayKeyframeRequestSuppressedCount, dropped: shareReplayDroppedFrames, stale: shareReplayStaleFrames, text: target.textContent, status: shareReplayShellState.status};
          const repairKeyframe = {
            digest: 'sha256:repair-keyframe',
            viewport: {width: 1200, height: 700},
            root: {
              nodeId: 1,
              tag: 'div',
              attrs: {id: 'appRoot', class: 'app-root host-root'},
              children: [
                {nodeId: 2, tag: 'span', attrs: {'data-testid': 'delta-target'}, text: 'repair'}
              ]
            },
            terminals: []
          };
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 7, sequence: 16, payload: repairKeyframe});
          await frame();
          const repairedTarget = document.querySelector('[data-testid="delta-target"]');
          const afterRepair = {epoch: shareReplayCurrentEpoch, sequence: shareReplayLastSequence, requests: shareReplayKeyframeRequestCount, suppressed: shareReplayKeyframeRequestSuppressedCount, inFlight: shareReplayKeyframeInFlight, backoffMs: shareReplayKeyframeBackoffMs, text: repairedTarget?.textContent || '', status: shareReplayShellState.status};
          done({initialState, afterGood, afterStale, afterGap, afterUnknown, afterDigest, afterRepair});
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["initialState"] == {"epoch": 7, "sequence": 10, "requests": 0, "text": "one"}, metrics
    assert metrics["afterGood"] == {"epoch": 7, "sequence": 11, "requests": 0, "text": "two", "status": "mirrored"}, metrics
    assert metrics["afterStale"]["sequence"] == 11, metrics
    assert metrics["afterStale"]["requests"] == 0, metrics
    assert metrics["afterStale"]["suppressed"] == 0, metrics
    assert metrics["afterStale"]["stale"] == 1, metrics
    assert metrics["afterStale"]["inFlight"] is False, metrics
    assert metrics["afterStale"]["backoffMs"] == 0, metrics
    assert metrics["afterStale"]["text"] == "two", metrics
    assert metrics["afterStale"]["status"] == "mirrored", metrics
    assert metrics["afterGap"]["sequence"] == 13, metrics
    assert metrics["afterGap"]["requests"] == 1, metrics
    assert metrics["afterGap"]["suppressed"] == 0, metrics
    assert metrics["afterGap"]["dropped"] == 1, metrics
    assert metrics["afterGap"]["text"] == "gap", metrics
    assert metrics["afterGap"]["status"] == "mirrored", metrics
    assert metrics["afterUnknown"]["sequence"] == 14, metrics
    assert metrics["afterUnknown"]["requests"] == 1, metrics
    assert metrics["afterUnknown"]["suppressed"] == 0, metrics
    assert metrics["afterUnknown"]["dropped"] == 1, metrics
    assert metrics["afterUnknown"]["stale"] == 2, metrics
    assert metrics["afterUnknown"]["text"] == "gap", metrics
    assert metrics["afterUnknown"]["status"] == "mirrored", metrics
    assert metrics["afterDigest"]["sequence"] == 14, metrics
    assert metrics["afterDigest"]["requests"] == 1, metrics
    assert metrics["afterDigest"]["suppressed"] >= 1, metrics
    assert metrics["afterDigest"]["dropped"] == 2, metrics
    assert metrics["afterDigest"]["stale"] == 2, metrics
    assert metrics["afterDigest"]["text"] == "digest mismatch", metrics
    assert metrics["afterDigest"]["status"] == "error", metrics
    assert metrics["afterRepair"]["sequence"] == 16, metrics
    assert metrics["afterRepair"]["requests"] == 1, metrics
    assert metrics["afterRepair"]["inFlight"] is False, metrics
    assert metrics["afterRepair"]["backoffMs"] == 0, metrics
    assert metrics["afterRepair"]["text"] == "repair", metrics
    assert metrics["afterRepair"]["status"] == "mirrored", metrics


def test_share_replay_shell_ignores_interleaved_semantic_finder_frames(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-replay-finder-interleaved",
        "mode": "ro",
        "session": "6",
        "sessions": ["6"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:6",
        "uiState": {"viewport": {"width": 1200, "height": 700}},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=1#t=valid-share-token",
        sessions=["6"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "token": "valid-share-token", "mode": "ro", "expiresAt": 4102444800.0, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.body.classList.contains('share-replay-shell')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const keyframe = (text, nodeId) => ({
            digest: '',
            viewport: {width: 1200, height: 700},
            root: {
              nodeId: 1,
              tag: 'div',
              attrs: {id: 'appRoot', class: 'app-root host-root'},
              children: [
                {nodeId, tag: 'section', attrs: {class: 'file-explorer-panel', 'data-testid': 'finder-replay'}, text}
              ]
            },
            terminals: []
          });
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 2, sequence: 1, payload: keyframe('Finder one pane', 2)});
          await frame();
          applyShareUiMessage({
            ch: 'ui',
            type: 'ui-state',
            sender: 'host',
            epoch: 99,
            sequence: 500,
            payload: {
              layout: 'col@50(left,slot1)',
              tabs: 'left:__files__;slot1:__files__',
              finder: {root: '/home/test/yolomux.dev', rootMode: 'sync', mode: 'diff', session: '6'}
            }
          });
          await frame();
          applyShareUiMessage({
            ch: 'ui',
            type: shareMirrorProtocol.frames.geometryDigest,
            sender: 'host',
            epoch: 100,
            sequence: 501,
            payload: {
              digest: 'safari-one-pixel-tab-strip',
              snapshot: {
                viewport: {width: 1220, height: 742},
                fonts: {ui: 249, mono: 297},
                slots: {},
                tabStrips: [{index: 4, rect: {left: 412, top: 667, width: 964, height: 21}}],
                terminalCells: [],
                editors: [],
                textWraps: [],
              },
            },
          });
          await frame();
          const afterSemantic = {
            text: document.querySelector('[data-testid="finder-replay"]')?.textContent || '',
            finderPanels: document.querySelectorAll('[data-testid="finder-replay"]').length,
            status: shareReplayShellState.status,
            requests: shareReplayKeyframeRequestCount,
            dropped: shareReplayDroppedFrames,
          };
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 3, sequence: 2, payload: keyframe('Finder still one pane', 3)});
          await frame();
          const afterKeyframe = {
            text: document.querySelector('[data-testid="finder-replay"]')?.textContent || '',
            finderPanels: document.querySelectorAll('[data-testid="finder-replay"]').length,
            epoch: shareReplayCurrentEpoch,
            sequence: shareReplayLastSequence,
            status: shareReplayShellState.status,
            requests: shareReplayKeyframeRequestCount,
          };
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domDelta, sender: 'host', epoch: 3, baseSequence: 2, sequence: 3, payload: {mutations: [{kind: 'characterData', target: 3, text: 'Finder delta one pane'}]}});
          await frame();
          const afterDelta = {
            text: document.querySelector('[data-testid="finder-replay"]')?.textContent || '',
            finderPanels: document.querySelectorAll('[data-testid="finder-replay"]').length,
            epoch: shareReplayCurrentEpoch,
            sequence: shareReplayLastSequence,
            status: shareReplayShellState.status,
            requests: shareReplayKeyframeRequestCount,
            dropped: shareReplayDroppedFrames,
          };
          done({afterSemantic, afterKeyframe, afterDelta});
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["afterSemantic"] == {"text": "Finder one pane", "finderPanels": 1, "status": "mirrored", "requests": 0, "dropped": 0}, metrics
    assert metrics["afterKeyframe"] == {"text": "Finder still one pane", "finderPanels": 1, "epoch": 3, "sequence": 2, "status": "mirrored", "requests": 0}, metrics
    assert metrics["afterDelta"] == {"text": "Finder delta one pane", "finderPanels": 1, "epoch": 3, "sequence": 3, "status": "mirrored", "requests": 0, "dropped": 0}, metrics


def test_share_replay_shell_applies_scroll_and_pointer_deltas(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-replay-scroll-pointer",
        "mode": "ro",
        "session": "6",
        "sessions": ["6"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:6",
        "uiState": {"viewport": {"width": 1200, "height": 700}},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=1#t=valid-share-token",
        sessions=["6"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "token": "valid-share-token", "mode": "ro", "expiresAt": 4102444800.0, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.body.classList.contains('share-replay-shell')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          await frame();
          const scrollerAttrs = testid => ({
            'data-testid': testid,
            style: 'height: 80px; width: 140px; overflow: auto; border: 0;'
          });
          const filler = (nodeId, label) => ({
            nodeId,
            tag: 'div',
            attrs: {style: 'height: 520px; width: 520px;'},
            text: label
          });
          const keyframe = {
            digest: 'sha256:scroll-keyframe',
            viewport: {width: 1200, height: 700},
            root: {
              nodeId: 1,
              tag: 'div',
              attrs: {id: 'appRoot', class: 'app-root host-root'},
              children: [
                {nodeId: 2, tag: 'div', attrs: {...scrollerAttrs('finder-scroll'), class: 'file-explorer-tree-panel'}, children: [filler(5, 'Finder rows')]},
                {nodeId: 3, tag: 'div', attrs: {...scrollerAttrs('editor-scroll'), class: 'cm-scroller'}, children: [filler(6, 'Editor rows')]},
                {nodeId: 4, tag: 'div', attrs: {...scrollerAttrs('prefs-scroll'), class: 'preferences-scroll'}, children: [filler(7, 'Preferences rows')]}
              ]
            },
            scroll: [
              {nodeId: 2, top: 32, left: 8},
              {nodeId: 3, top: 44, left: 10},
              {nodeId: 4, top: 56, left: 12}
            ],
            terminals: []
          };
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 11, sequence: 20, payload: keyframe});
          await frame();
          const finder = document.querySelector('[data-testid="finder-scroll"]');
          const editor = document.querySelector('[data-testid="editor-scroll"]');
          const prefs = document.querySelector('[data-testid="prefs-scroll"]');
          const initial = {
            finderTop: finder.scrollTop,
            finderLeft: finder.scrollLeft,
            editorTop: editor.scrollTop,
            editorLeft: editor.scrollLeft,
            prefsTop: prefs.scrollTop,
            prefsLeft: prefs.scrollLeft,
          };
          applyShareUiMessage({
            ch: 'ui',
            type: shareMirrorProtocol.frames.domDelta,
            sender: 'host',
            epoch: 11,
            baseSequence: 20,
            sequence: 21,
            payload: {
              scroll: [
                {nodeId: 2, top: 120, left: 15},
                {nodeId: 3, top: 140, left: 25},
                {nodeId: 4, top: 160, left: 35}
              ],
              pointer: {scope: 'viewport', x: 100, y: 80, visible: true, click: true}
            }
          });
          await frame();
          await frame();
          const expectedPointer = visualPointFromAppSpace(100, 80);
          const ghost = document.querySelector('.share-ghost-cursor.visible');
          const queueBefore = shareHostQueuedMessageCount(shareToken);
          finder.dispatchEvent(new Event('scroll', {bubbles: true}));
          await frame();
          const queueAfter = shareHostQueuedMessageCount(shareToken);
          done({
            initial,
            after: {
              finderTop: finder.scrollTop,
              finderLeft: finder.scrollLeft,
              editorTop: editor.scrollTop,
              editorLeft: editor.scrollLeft,
              prefsTop: prefs.scrollTop,
              prefsLeft: prefs.scrollLeft,
            },
            sequence: shareReplayLastSequence,
            status: shareReplayShellState.status,
            ghostExists: Boolean(ghost),
            ghostSender: ghost?.dataset?.shareSender || '',
            ghostTransform: ghost?.style?.transform || '',
            expectedPointer: {x: Math.round(expectedPointer.x), y: Math.round(expectedPointer.y)},
            queueBefore,
            queueAfter,
          });
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["initial"] == {
        "finderTop": 32,
        "finderLeft": 8,
        "editorTop": 44,
        "editorLeft": 10,
        "prefsTop": 56,
        "prefsLeft": 12,
    }, metrics
    assert metrics["after"] == {
        "finderTop": 120,
        "finderLeft": 15,
        "editorTop": 140,
        "editorLeft": 25,
        "prefsTop": 160,
        "prefsLeft": 35,
    }, metrics
    assert metrics["sequence"] == 21, metrics
    assert metrics["status"] == "mirrored", metrics
    assert metrics["ghostExists"] is True, metrics
    assert metrics["ghostSender"] == "host", metrics
    assert f"{metrics['expectedPointer']['x']}px" in metrics["ghostTransform"], metrics
    assert f"{metrics['expectedPointer']['y']}px" in metrics["ghostTransform"], metrics
    assert metrics["queueAfter"] == metrics["queueBefore"], metrics


def test_share_replay_debug_copy_exports_sanitized_health(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-replay-debug",
        "mode": "ro",
        "session": "6",
        "sessions": ["6"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:6",
        "uiState": {"viewport": {"width": 1200, "height": 700}},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=1&shareDebug=1#t=valid-share-token",
        sessions=["6"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "token": "valid-share-token", "mode": "ro", "expiresAt": 4102444800.0, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.body.classList.contains('share-replay-shell')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          await frame();
          const keyframe = {
            digest: 'sha256:keyframe-debug',
            createdAt: (Date.now() - 125) / 1000,
            viewport: {width: 1200, height: 700},
            root: {
              nodeId: 1,
              tag: 'div',
              attrs: {id: 'appRoot', class: 'app-root host-root'},
              children: [
                {nodeId: 2, tag: 'span', attrs: {'data-testid': 'debug-target'}, text: 'debug text'},
                {nodeId: 3, tag: 'div', attrs: {class: 'share-terminal-placeholder', 'data-share-terminal-placeholder': '6', 'data-session': '6', 'data-rows': '24', 'data-cols': '80'}, children: []}
              ]
            },
            terminals: [{placeholderId: 'term-ph-6', session: '6', rows: 24, cols: 80, terminalEpoch: 9}],
            redaction: {policyVersion: 1, removedCount: 0}
          };
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 13, sequence: 30, payload: keyframe});
          await frame();
          applyShareUiMessage({
            ch: 'ui',
            type: shareMirrorProtocol.frames.domDelta,
            sender: 'host',
            epoch: 13,
            baseSequence: 30,
            sequence: 31,
            payload: {
              capturedAt: Date.now() - 42,
              mutations: [{kind: 'characterData', target: 2, text: 'debug text updated'}]
            }
          });
          await frame();
          const text = shareDebugTextForClipboard();
          const parsed = JSON.parse(text);
          const apiText = window.yolomuxShareDebug?.text?.() || '';
          const apiParsed = apiText ? JSON.parse(apiText) : {};
          done({
            text,
            apiKind: apiParsed.kind || '',
            kind: parsed.kind,
            match: parsed.match,
            status: parsed.status,
            userStatus: parsed.userStatus,
            epoch: parsed.epoch,
            sequence: parsed.sequence,
            keyframeBytes: parsed.keyframeBytes,
            deltaBytes: parsed.deltaBytes,
            droppedFrames: parsed.droppedFrames,
            keyframeRequestsSuppressed: parsed.keyframeRequestsSuppressed,
            keyframeRequestBackoffMs: parsed.keyframeRequestBackoffMs,
            keyframeRequestInFlight: parsed.keyframeRequestInFlight,
            hostKeyframesSuppressed: parsed.hostKeyframesSuppressed,
            hostKeyframePending: parsed.hostKeyframePending,
            replayLatencyMs: parsed.replayLatencyMs,
            domDigest: parsed.domDigest,
            redactionPolicyVersion: parsed.redactionPolicyVersion,
            terminalHealthy: parsed.terminalPlaceholders?.healthy,
            terminalCount: parsed.terminalPlaceholders?.count,
            terminalConnected: parsed.terminalPlaceholders?.connected,
            terminalDisconnected: parsed.terminalPlaceholders?.disconnected,
            terminalSession: parsed.terminalPlaceholders?.entries?.[0]?.session || '',
            contextHash: parsed.context?.location?.hash || '',
            mirrorStatusText: document.querySelector('.share-viewer-mirror-status')?.textContent || '',
            debugButton: Boolean(document.querySelector('[data-share-viewer-control="debug"]')),
          });
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["apiKind"] == "share-replay-health", metrics
    assert metrics["kind"] == "share-replay-health", metrics
    assert metrics["match"] is True, metrics
    assert metrics["status"] == "mirrored", metrics
    assert metrics["userStatus"] == "mirror synced", metrics
    assert metrics["mirrorStatusText"] == "mirror synced", metrics
    assert metrics["epoch"] == 13, metrics
    assert metrics["sequence"] == 31, metrics
    assert metrics["keyframeBytes"] > 0, metrics
    assert metrics["deltaBytes"] > 0, metrics
    assert metrics["droppedFrames"] == 0, metrics
    assert metrics["keyframeRequestsSuppressed"] == 0, metrics
    assert metrics["keyframeRequestBackoffMs"] == 0, metrics
    assert metrics["keyframeRequestInFlight"] is False, metrics
    assert metrics["hostKeyframesSuppressed"] == 0, metrics
    assert metrics["hostKeyframePending"] is False, metrics
    assert metrics["replayLatencyMs"] >= 0, metrics
    assert metrics["domDigest"], metrics
    assert metrics["redactionPolicyVersion"] == 1, metrics
    assert metrics["terminalHealthy"] is True, metrics
    assert metrics["terminalCount"] == 1, metrics
    assert metrics["terminalConnected"] == 1, metrics
    assert metrics["terminalDisconnected"] == 0, metrics
    assert metrics["terminalSession"] == "6", metrics
    assert metrics["debugButton"] is True, metrics
    assert "valid-share-token" not in metrics["text"], metrics
    assert "/share/share123" not in metrics["text"], metrics
    assert "[redacted-share-token]" in metrics["text"], metrics
    assert "[redacted-share-token]" in metrics["contextHash"], metrics


def test_share_replay_debug_copy_exports_last_replay_error(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-replay-debug-error",
        "mode": "ro",
        "session": "6",
        "sessions": ["6"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:6",
        "uiState": {"viewport": {"width": 1200, "height": 700}},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=1&shareDebug=1#t=valid-share-token",
        sessions=["6"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "token": "valid-share-token", "mode": "ro", "expiresAt": 4102444800.0, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.body.classList.contains('share-replay-shell')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const keyframe = {
            digest: 'sha256:keyframe-debug-error',
            createdAt: Date.now() / 1000,
            viewport: {width: 1200, height: 700},
            root: {
              nodeId: 1,
              tag: 'div',
              attrs: {id: 'appRoot', class: 'app-root host-root'},
              children: [
                {nodeId: 2, tag: 'span', attrs: {'data-testid': 'debug-error-target'}, text: 'debug error text'}
              ]
            },
            terminals: [],
            redaction: {policyVersion: 1, removedCount: 0}
          };
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 17, sequence: 50, payload: keyframe});
          await frame();
          applyShareUiMessage({
            ch: 'ui',
            type: shareMirrorProtocol.frames.domDelta,
            sender: 'host',
            epoch: 17,
            baseSequence: 49,
            sequence: 52,
            payload: {
              mutations: []
            }
          });
          await frame();
          const text = shareDebugTextForClipboard();
          const parsed = JSON.parse(text);
          done({
            text,
            status: parsed.status,
            droppedFrames: parsed.droppedFrames,
            keyframeRequests: parsed.keyframeRequests,
            lastReplayError: parsed.lastReplayError || null,
            targetText: document.querySelector('[data-testid="debug-error-target"]')?.textContent || '',
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          });
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics
    assert metrics["status"] == "mirrored", metrics
    assert metrics["droppedFrames"] == 1, metrics
    assert metrics["keyframeRequests"] == 1, metrics
    assert metrics["targetText"] == "debug error text", metrics
    assert metrics["lastReplayError"] == {
        "frameType": "dom-delta",
        "reason": "gap",
        "error": "non-contiguous replay delta",
        "currentEpoch": 17,
        "lastSequence": 50,
        "epoch": 17,
        "sequence": 52,
        "baseSequence": 49,
        "expectedSequence": 51,
        "expectedBaseSequence": 50,
        "frameBytes": metrics["lastReplayError"]["frameBytes"],
    }, metrics
    assert metrics["lastReplayError"]["frameBytes"] > 0, metrics
    assert "valid-share-token" not in metrics["text"], metrics
    assert "/share/share123" not in metrics["text"], metrics
    assert "[redacted-share-token]" in metrics["text"], metrics


def test_share_replay_health_ignores_legacy_geometry_drift_frames(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-replay-health",
        "mode": "ro",
        "session": "6",
        "sessions": ["6"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:6",
        "uiState": {"viewport": {"width": 1200, "height": 700}},
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=1&shareDebug=1#t=valid-share-token",
        sessions=["6"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "token": "valid-share-token", "mode": "ro", "expiresAt": 4102444800.0, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return document.body.classList.contains('share-replay-shell')")
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const keyframe = {
            digest: 'sha256:replay-health-keyframe',
            createdAt: Date.now() / 1000,
            viewport: {width: 1200, height: 700},
            root: {
              nodeId: 1,
              tag: 'div',
              attrs: {id: 'appRoot', class: 'app-root host-root'},
              children: [
                {nodeId: 2, tag: 'span', attrs: {'data-testid': 'health-target'}, text: 'replay health text'},
                {nodeId: 3, tag: 'div', attrs: {class: 'share-terminal-placeholder', 'data-share-terminal-placeholder': '6', 'data-session': '6', 'data-rows': '24', 'data-cols': '80'}, children: []}
              ]
            },
            terminals: [{placeholderId: 'term-ph-6', session: '6', rows: 24, cols: 80, terminalEpoch: 1}],
            redaction: {policyVersion: 1, removedCount: 0}
          };
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.domKeyframe, sender: 'host', epoch: 23, sequence: 40, payload: keyframe});
          await frame();
          const before = shareReplayHealthDiagnostics();
          const legacyGeometryDigest = {
            digest: 'legacy-semantic-drift-digest',
            snapshot: {
              viewport: {width: 1, height: 1},
              fonts: {ui: 'drifted'},
              slots: {layout: 'row@1(left,right)', slots: [{slot: 'left', placeholder: false}]},
              tabStrips: [],
              terminalCells: [],
              editors: [],
              textWraps: [{key: '/semantic/text-wrap', tag: 'span', rect: {left: 0, top: 0, width: 999, height: 99}, scrollHeight: 99}]
            }
          };
          applyShareUiMessage({ch: 'ui', type: shareMirrorProtocol.frames.geometryDigest, sender: 'host', epoch: 23, sequence: 41, payload: legacyGeometryDigest});
          await frame();
          const after = shareReplayHealthDiagnostics();
          const debugText = shareDebugTextForClipboard();
          const debug = JSON.parse(debugText);
          const status = document.querySelector('.share-viewer-mirror-status');
          done({
            before,
            after,
            statusText: status?.textContent || '',
            debugKind: debug.kind || '',
            debugTextHasLegacyDigest: debugText.includes('legacy-semantic-drift-digest'),
            debugTextHasTextWraps: debugText.includes('textWraps'),
            latestKind: window.yolomuxShareDebug?.latest?.kind || '',
            lastGeometryDigest: typeof shareLastGeometryDigest === 'object' && shareLastGeometryDigest ? shareLastGeometryDigest.digest || '' : '',
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          });
        })().catch(error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["errors"] == [], metrics
    assert metrics["rejections"] == [], metrics
    assert metrics["before"]["kind"] == "share-replay-health", metrics
    assert metrics["after"]["kind"] == "share-replay-health", metrics
    assert metrics["after"]["match"] is True, metrics
    assert metrics["after"]["status"] == "mirrored", metrics
    assert metrics["after"]["userStatus"] == "mirror synced", metrics
    assert metrics["statusText"] == "mirror synced", metrics
    assert metrics["after"]["epoch"] == metrics["before"]["epoch"] == 23, metrics
    assert metrics["after"]["sequence"] == metrics["before"]["sequence"] == 40, metrics
    assert metrics["after"]["domDigest"] == metrics["before"]["domDigest"], metrics
    assert metrics["after"]["terminalPlaceholders"]["healthy"] is True, metrics
    assert metrics["after"]["terminalPlaceholders"]["connected"] == 1, metrics
    assert metrics["debugKind"] == "share-replay-health", metrics
    assert metrics["latestKind"] == "", metrics
    assert metrics["lastGeometryDigest"] != "legacy-semantic-drift-digest", metrics
    assert metrics["debugTextHasLegacyDigest"] is False, metrics
    assert metrics["debugTextHasTextWraps"] is False, metrics


def test_share_geometry_digest_repairs_wrapped_text_labels_in_browser(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-wrap",
        "mode": "ro",
        "session": "1",
        "sessions": ["1"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:1",
        "uiState": {
            "layout": "left",
            "tabs": "left:1",
            "viewport": {"width": 1000, "height": 620},
        },
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=0",
        sessions=["1"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "layout": "left", "tabs": "left:1", "uiState": share_bootstrap["uiState"]},
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            "return typeof shareGeometryDigestFrame === 'function' && !!document.querySelector('.share-viewer-mirror-status')"
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const root = appRootElement();
          const label = document.createElement('span');
          label.className = 'file-tree-name';
          label.dataset.path = '/host/wrapped-label';
          label.textContent = 'very long mirrored Finder row label that must use the host measured wrapping box';
          label.style.display = 'block';
          label.style.width = '120px';
          label.style.height = '18px';
          root.appendChild(label);
          await frame();
          const localBefore = shareGeometryDigestFrame();
          const localEntry = localBefore.snapshot.textWraps.find(entry => entry.key === '/host/wrapped-label');
          const hostEntry = {
            ...localEntry,
            rect: {
              ...localEntry.rect,
              width: 420,
              height: 52,
            },
            clientWidth: 420,
            clientHeight: 52,
            scrollWidth: 420,
            scrollHeight: 52,
          };
          const hostSnapshot = {
            ...localBefore.snapshot,
            textWraps: localBefore.snapshot.textWraps.map(entry => entry.key === hostEntry.key ? hostEntry : entry),
          };
          const host = {snapshot: hostSnapshot, digest: shareGeometryDigestValue(hostSnapshot)};
          const beforeDiff = shareGeometryFirstDifference(host, localBefore);
          applyShareGeometryDigest(host);
          await frame();
          await frame();
          const localAfter = shareGeometryDigestFrame();
          const afterEntry = localAfter.snapshot.textWraps.find(entry => entry.key === '/host/wrapped-label');
          const textWrapDiffs = hostSnapshot.textWraps.map(hostMetric => {
            const actual = localAfter.snapshot.textWraps.find(entry => entry.key === hostMetric.key && entry.tag === hostMetric.tag);
            return stableDigestJson(hostMetric) === stableDigestJson(actual) ? null : {key: hostMetric.key, host: hostMetric, actual};
          }).filter(Boolean).slice(0, 6);
          const mirror = document.querySelector('.share-viewer-mirror-status');
          return {
            beforeDiff,
            afterDiff: shareGeometryFirstDifference(host, localAfter),
            match: host.digest === localAfter.digest,
            statusText: mirror?.textContent || '',
            statusMatch: mirror?.classList.contains('match') || false,
            styleWidth: label.style.width,
            styleHeight: label.style.height,
            afterEntry,
            hostEntry,
            textWrapDiffs,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["beforeDiff"] == "textWraps", metrics
    assert metrics["afterDiff"] == "", json.dumps(metrics, indent=2, sort_keys=True)
    assert metrics["match"] is True, metrics
    assert metrics["statusMatch"] is True, metrics
    assert "drift" not in metrics["statusText"], metrics
    assert metrics["styleWidth"] == "420px", metrics
    assert metrics["styleHeight"] == "52px", metrics
    assert metrics["afterEntry"]["scrollHeight"] == 52, metrics


def test_share_geometry_digest_resyncs_slot_drift_in_browser(browser, tmp_path):
    layout = "row@50(left,slot1)"
    tabs = "left:1;slot1:2"
    ui_state = {"layout": layout, "tabs": tabs, "viewport": {"width": 1000, "height": 620}}
    share_bootstrap = {
        "view": True,
        "id": "share-slots",
        "mode": "ro",
        "session": "1",
        "sessions": ["1", "2"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": layout,
        "tabs": tabs,
        "uiState": ui_state,
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=0",
        sessions=["1", "2"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "layout": layout, "tabs": tabs, "uiState": ui_state},
    )
    wait_for_dockview(browser, min_tabs=2)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFor = window.__yolomuxTestWaitFor;
          await frame();
          const host = shareGeometryDigestFrame();
          const drifted = layoutFromParam('row@50(left,slot2)', 'left:1;slot2:2');
          layoutSlots = drifted;
          await waitFor(() => shareGeometryFirstDifference(host, shareGeometryDigestFrame()) === 'slots');
          const beforeDiff = shareGeometryFirstDifference(host, shareGeometryDigestFrame());
          window.__fixtureSharePayload = {
            ok: true,
            active: true,
            layout: 'row@50(left,slot1)',
            tabs: 'left:1;slot1:2',
            uiState: {
              layout: 'row@50(left,slot1)',
              tabs: 'left:1;slot1:2',
              viewport: {width: 1000, height: 620},
            },
          };
          const originalFetch = window.fetch;
          let releaseShareFetch = null;
          let shareFetchStarted = false;
          window.fetch = async (input, options = {}) => {
            const url = new URL(String(input), 'https://localhost');
            if (url.pathname === '/api/share' && !shareFetchStarted) {
              shareFetchStarted = true;
              await new Promise(resolve => { releaseShareFetch = resolve; });
            }
            return originalFetch(input, options);
          };
          applyShareGeometryDigest(host);
          await waitFor(() => shareFetchStarted);
          applyShareGeometryDigest(host);
          const statusDuringRepair = document.querySelector('.share-viewer-mirror-status')?.textContent || '';
          releaseShareFetch?.();
          const cleared = await waitFor(() => shareGeometryFirstDifference(host, shareGeometryDigestFrame()) === ''
            && document.querySelector('.share-viewer-mirror-status')?.classList.contains('match'));
          window.fetch = originalFetch;
          const localAfter = shareGeometryDigestFrame();
          const textWrapDiffs = (host.snapshot.textWraps || []).map(hostMetric => {
            const actual = (localAfter.snapshot.textWraps || []).find(entry => entry.key === hostMetric.key && entry.tag === hostMetric.tag);
            return stableDigestJson(hostMetric) === stableDigestJson(actual) ? null : {key: hostMetric.key, host: hostMetric, actual};
          }).filter(Boolean).slice(0, 6);
          const mirror = document.querySelector('.share-viewer-mirror-status');
          return {
            beforeDiff,
            cleared,
            afterDiff: shareGeometryFirstDifference(host, localAfter),
            match: host.digest === localAfter.digest,
            hostSlots: host.snapshot.slots,
            localSlots: localAfter.snapshot.slots,
            layoutParam: layoutParamValue(layoutSlots),
            layoutTabs: layoutTabsParamValue(layoutSlots),
            statusText: mirror?.textContent || '',
            statusMatch: mirror?.classList.contains('match') || false,
            statusDuringRepair,
            textWrapDiffs,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["beforeDiff"] == "slots", metrics
    assert "drift" not in metrics["statusDuringRepair"], metrics
    assert metrics["cleared"] is True, json.dumps(metrics, indent=2, sort_keys=True)
    assert metrics["afterDiff"] == "", metrics
    assert metrics["match"] is True, metrics
    assert metrics["statusMatch"] is True, metrics
    assert "drift" not in metrics["statusText"], metrics


def test_share_geometry_digest_repairs_terminal_cell_drift_with_host_resize(browser, tmp_path):
    terminal_dims = [{"session": "1", "rows": 24, "cols": 80}]
    share_bootstrap = {
        "view": True,
        "id": "share-terminal-cells",
        "mode": "ro",
        "session": "1",
        "sessions": ["1"],
        "hostDimsBySession": {"1": {"rows": 24, "cols": 80}},
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:1",
        "uiState": {
            "layout": "left",
            "tabs": "left:1",
            "terminalDims": terminal_dims,
            "viewport": {"width": 1000, "height": 620},
        },
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=0",
        sessions=["1"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "layout": "left", "tabs": "left:1", "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    wait_for_dockview(browser, min_tabs=1)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            const item = terminals.get('1');
            return Boolean(item?.term && item.term.rows === 24 && item.term.cols === 80 && typeof shareGeometryDigestFrame === 'function');
            """
        )
    )
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFor = window.__yolomuxTestWaitFor;
          const item = terminals.get('1');
          const host = shareGeometryDigestFrame();
          const hostEntry = host.snapshot.terminalCells.find(entry => entry.session === '1');
          item.term.resize(hostEntry.cols + 9, hostEntry.rows + 4);
          await waitFor(() => shareGeometryFirstDifference(host, shareGeometryDigestFrame()) === 'terminalCells');
          const beforeDiff = shareGeometryFirstDifference(host, shareGeometryDigestFrame());
          const calls = [];
          const originalResize = item.term.resize.bind(item.term);
          const originalReset = item.term.reset ? item.term.reset.bind(item.term) : null;
          const originalRefresh = item.term.refresh ? item.term.refresh.bind(item.term) : null;
          item.term.resize = (cols, rows) => {
            calls.push(['resize', cols, rows]);
            return originalResize(cols, rows);
          };
          item.term.reset = () => {
            calls.push(['reset']);
            return originalReset ? originalReset() : undefined;
          };
          item.term.refresh = (start, end) => {
            calls.push(['refresh', start, end]);
            return originalRefresh ? originalRefresh(start, end) : undefined;
          };
          applyShareGeometryDigest(host);
          const repaired = await waitFor(() => shareGeometryFirstDifference(host, shareGeometryDigestFrame()) === ''
            && calls.some(call => call[0] === 'refresh')
            && document.querySelector('.share-viewer-mirror-status')?.classList.contains('match'));
          const localAfter = shareGeometryDigestFrame();
          const status = document.querySelector('.share-viewer-mirror-status');
          return {
            beforeDiff,
            repaired,
            afterDiff: shareGeometryFirstDifference(host, localAfter),
            match: host.digest === localAfter.digest,
            hostEntry,
            rows: item.term.rows,
            cols: item.term.cols,
            calls,
            repairAction: shareGeometryRepairActionForDiff('terminalCells'),
            statusText: status?.textContent || '',
            statusMatch: status?.classList.contains('match') || false,
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["beforeDiff"] == "terminalCells", metrics
    assert metrics["repairAction"] == "terminal-host-resize", metrics
    assert metrics["repaired"] is True, json.dumps(metrics, indent=2, sort_keys=True)
    assert metrics["afterDiff"] == "", metrics
    assert metrics["match"] is True, metrics
    assert metrics["rows"] == metrics["hostEntry"]["rows"], metrics
    assert metrics["cols"] == metrics["hostEntry"]["cols"], metrics
    assert ["resize", metrics["hostEntry"]["cols"], metrics["hostEntry"]["rows"]] in metrics["calls"], metrics
    assert ["reset"] in metrics["calls"], metrics
    assert ["refresh", 0, metrics["hostEntry"]["rows"] - 1] in metrics["calls"], metrics
    assert metrics["statusMatch"] is True, metrics
    assert "terminalCells" not in metrics["statusText"], metrics
    assert metrics["errors"] == []
    assert metrics["rejections"] == []


def test_share_geometry_digest_slots_use_layout_model_in_contain_viewer(browser, tmp_path):
    browser.set_window_size(1500, 1200)
    layout = "row@22(left,row@50(slot1,col@50(slot2,slot3)))"
    tabs = "left:1;slot1:2;slot2:3;slot3:4"
    ui_state = {"layout": layout, "tabs": tabs, "viewport": {"width": 1800, "height": 980}}
    share_bootstrap = {
        "view": True,
        "id": "share-contain-slots",
        "mode": "ro",
        "session": "1",
        "sessions": ["1", "2", "3", "4"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": layout,
        "tabs": tabs,
        "uiState": ui_state,
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=0",
        sessions=["1", "2", "3", "4"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "layout": layout, "tabs": tabs, "uiState": ui_state},
        grid_width=1800,
        grid_height=920,
        file_explorer_open_intent="0",
        wrap_app_root=True,
    )
    wait_for_dockview(browser, min_tabs=4)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFrames = async count => {
            for (let index = 0; index < count; index += 1) await frame();
          };
          await waitFrames(4);
          setShareViewFit('contain', {persist: false});
          await waitFrames(4);
          const host = shareGeometryDigestFrame();
          const slotDigest = host.snapshot.slots;
          setShareViewFit('cover', {persist: false});
          await waitFrames(4);
          const coverDiff = shareGeometryFirstDifference(host, shareGeometryDigestFrame());
          setShareViewFit('contain', {persist: false});
          await waitFrames(4);
          const containAgain = shareGeometryDigestFrame();
          const containDiff = shareGeometryFirstDifference(host, containAgain);
          applyShareGeometryDigest(host);
          await waitFrames(2);
          const status = document.querySelector('.share-viewer-mirror-status');
          const rootRect = appRootElement().getBoundingClientRect();
          return {
            slotDigestKind: slotDigest && typeof slotDigest === 'object' && !Array.isArray(slotDigest) ? 'layout-model' : 'rendered-rects',
            slotCount: Array.isArray(slotDigest?.slots) ? slotDigest.slots.length : 0,
            hasAnySlotRect: Array.isArray(slotDigest)
              ? slotDigest.some(entry => entry?.rect)
              : (slotDigest?.slots || []).some(entry => entry?.rect),
            layout: slotDigest?.layout || '',
            coverDiff,
            containDiff,
            match: host.digest === containAgain.digest,
            statusText: status?.textContent || '',
            statusMatch: status?.classList.contains('match') || false,
            transform: appMirrorTransformState(),
            rootRect: {left: Math.round(rootRect.left), top: Math.round(rootRect.top), width: Math.round(rootRect.width), height: Math.round(rootRect.height)},
            viewport: appViewport(),
            nativeViewport: nativeViewport(),
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["slotDigestKind"] == "layout-model", metrics
    assert metrics["slotCount"] == 4, metrics
    assert metrics["hasAnySlotRect"] is False, metrics
    assert metrics["layout"] == layout, metrics
    assert metrics["coverDiff"] == "", json.dumps(metrics, indent=2, sort_keys=True)
    assert metrics["containDiff"] == "", json.dumps(metrics, indent=2, sort_keys=True)
    assert metrics["match"] is True, metrics
    assert metrics["statusMatch"] is True, metrics
    assert "drift" not in metrics["statusText"], metrics
    assert metrics["transform"]["scale"] < 1, metrics


def test_share_debug_diagnostics_redact_fragment_token_in_browser(browser, tmp_path):
    secret = "safari-secret-token-12345"
    share_bootstrap = {
        "view": True,
        "id": "share-debug",
        "mode": "ro",
        "session": "1",
        "sessions": ["1"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:1",
        "uiState": {
            "layout": "left",
            "tabs": "left:1",
            "viewport": {"width": 1000, "height": 620},
        },
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search=f"?shareReplay=0&shareDebug=1#t={secret}",
        sessions=["1"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "layout": "left", "tabs": "left:1", "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    wait_for_dockview(browser, min_tabs=1)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFor = window.__yolomuxTestWaitFor;
          await waitFor(() => window.yolomuxShareDebug?.enabled === true && document.querySelector('[data-share-viewer-control="debug"]'));
          const host = shareGeometryDigestFrame();
          const hostSnapshot = {
            ...host.snapshot,
            fonts: {
              ...host.snapshot.fonts,
              safariProbe: 'host-font-metric',
            },
          };
          const payload = {snapshot: hostSnapshot, digest: shareGeometryDigestValue(hostSnapshot)};
          applyShareGeometryDigest(payload);
          const ready = await waitFor(() => window.yolomuxShareDebug?.latest?.diff === 'fonts');
          const report = window.yolomuxShareDebug?.latest || null;
          const text = shareDebugTextForClipboard();
          const status = document.querySelector('.share-viewer-mirror-status');
          return {
            ready,
            enabled: window.yolomuxShareDebug?.enabled === true,
            buttonExists: Boolean(document.querySelector('[data-share-viewer-control="debug"]')),
            diff: report?.diff || '',
            phase: report?.phase || '',
            statusText: status?.textContent || '',
            locationHash: report?.context?.location?.hash || '',
            hasSecret: text.includes(arguments[0]),
            hasRedactedToken: text.includes('[redacted-share-token]'),
            deltaChangedKeys: (report?.delta?.changed || []).map(entry => entry.key),
            reportKind: report?.kind || '',
            reportText: text,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        secret,
    )
    assert "error" not in metrics, metrics
    assert metrics["enabled"] is True, metrics
    assert metrics["buttonExists"] is True, metrics
    assert metrics["ready"] is True, json.dumps(metrics, indent=2, sort_keys=True)
    assert metrics["reportKind"] == "share-geometry-drift", metrics
    assert metrics["diff"] == "fonts", metrics
    assert metrics["phase"] == "persistent", metrics
    assert "fonts" not in metrics["statusText"], metrics
    assert metrics["locationHash"] == "#t=[redacted-share-token]", metrics
    assert metrics["hasSecret"] is False, metrics
    assert metrics["hasRedactedToken"] is True, metrics
    assert "safariProbe" in metrics["deltaChangedKeys"], metrics


def test_share_viewer_expiry_redirects_to_login_without_fragment_token(browser, tmp_path):
    secret = "expired-share-token-should-not-leak"
    share_bootstrap = {
        "view": True,
        "id": "share-expired",
        "mode": "ro",
        "session": "1",
        "sessions": ["1"],
        "createdBy": "host",
        "expiresAt": 1,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:1",
        "uiState": {
            "layout": "left",
            "tabs": "left:1",
            "viewport": {"width": 1000, "height": 620},
        },
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search=f"?shareDebug=1#t={secret}",
        expected_redirect_paths=("/login",),
        sessions=["1"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "layout": "left", "tabs": "left:1", "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    WebDriverWait(browser, 4).until(lambda driver: "/login" in driver.current_url and "next=" in driver.current_url)
    current_url = browser.current_url
    assert secret not in current_url
    assert "#t=" not in current_url
    assert "%23t" not in current_url
    assert "shareDebug%3D1" in current_url or "shareDebug=1" in current_url


def test_share_viewer_finder_minimize_keeps_dockview_terminal_tabs_live(browser, tmp_path):
    layout_with_finder = "row@28(left,slot1)"
    tabs_with_finder = "left:__files__;slot1:1,2"
    layout_without_finder = "left"
    tabs_without_finder = "left:1,2"
    tabs_without_finder_active_2 = "left:1,2*"
    terminal_dims = [
        {"session": "1", "rows": 24, "cols": 80},
        {"session": "2", "rows": 24, "cols": 80},
    ]
    share_bootstrap = {
        "view": True,
        "id": "share-finder-minimize",
        "mode": "ro",
        "session": "1",
        "sessions": ["1", "2"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": layout_with_finder,
        "tabs": tabs_with_finder,
        "uiState": {
            "layout": layout_with_finder,
            "tabs": tabs_with_finder,
            "terminalDims": terminal_dims,
            "viewport": {"width": 1000, "height": 620},
            "finder": {"root": "/home/test/yolomux.dev", "rootMode": "sync", "mode": "files", "session": "1"},
        },
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=0",
        sessions=["1", "2"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "layout": layout_with_finder, "tabs": tabs_with_finder, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    wait_for_dockview(browser, min_tabs=3)
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script(
            """
            return terminals.get('1')?.socket?.readyState === WebSocket.OPEN
              && document.querySelector('#term-1 .xterm') !== null;
            """
        )
    )
    removed = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFor = window.__yolomuxTestWaitFor;
          await applyShareUiState({
            layout: arguments[0],
            tabs: arguments[1],
            terminalDims: arguments[2],
            viewport: {width: 1000, height: 620},
            finder: {root: '/home/test/yolomux.dev', rootMode: 'sync', mode: 'files', session: '1'},
          });
          const ready = await waitFor(() => Boolean(!itemInLayout(fileExplorerItemId)
            && document.querySelectorAll('.dockview-pane-tab[data-pane-tab="1"], .dockview-pane-tab[data-pane-tab="2"]').length === 2
            && document.querySelector('.dockview-pane-tab[data-pane-tab="2"]')));
          return {
            ready,
            finderInLayout: itemInLayout(fileExplorerItemId),
            tabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            active: activeItemForSide('left'),
            terminal1Open: terminals.get('1')?.socket?.readyState === WebSocket.OPEN,
            terminal2OpenBeforeClick: terminals.get('2')?.socket?.readyState === WebSocket.OPEN,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        layout_without_finder,
        tabs_without_finder,
        terminal_dims,
    )
    assert "error" not in removed, removed
    assert removed["ready"] is True, removed
    assert removed["finderInLayout"] is False, removed
    assert "1" in removed["tabs"] and "2" in removed["tabs"], removed
    assert removed["active"] == "1", removed
    assert removed["terminal1Open"] is True, removed

    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFor = window.__yolomuxTestWaitFor;
          await applyShareUiState({
            layout: arguments[0],
            tabs: arguments[1],
            terminalDims: arguments[2],
            viewport: {width: 1000, height: 620},
            finder: {root: '/home/test/yolomux.dev', rootMode: 'sync', mode: 'files', session: '2'},
          });
          const ready = await waitFor(() => Boolean(activeItemForSide('left') === '2'
            && terminals.get('2')?.socket?.readyState === WebSocket.OPEN
            && terminals.get('2')?.container === document.getElementById('term-2')
            && terminals.get('2')?.container?.isConnected === true
            && document.querySelector('#term-2 .xterm')));
          const item = terminals.get('2');
          return {
            ready,
            active: activeItemForSide('left'),
            terminalOpen: item?.socket?.readyState === WebSocket.OPEN,
            containerBound: item?.container === document.getElementById('term-2'),
            containerConnected: item?.container?.isConnected === true,
            hasXterm: document.querySelector('#term-2 .xterm') !== null,
            rows: item?.term?.rows || 0,
            cols: item?.term?.cols || 0,
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        layout_without_finder,
        tabs_without_finder_active_2,
        terminal_dims,
    )
    assert "error" not in result, result
    assert result["ready"] is True, result
    assert result["active"] == "2", result
    assert result["terminalOpen"] is True, result
    assert result["containerBound"] is True, result
    assert result["containerConnected"] is True, result
    assert result["hasXterm"] is True, result
    assert result["rows"] == 24, result
    assert result["cols"] == 80, result
    assert result["errors"] == []
    assert result["rejections"] == []


def test_share_viewer_drops_stale_layout_after_newer_ui_state(browser, tmp_path):
    layout_with_finder = "row@28(left,slot1)"
    tabs_with_finder = "left:__files__;slot1:1,2"
    layout_without_finder = "left"
    tabs_without_finder_active_2 = "left:1,2*"
    terminal_dims = [
        {"session": "1", "rows": 24, "cols": 80},
        {"session": "2", "rows": 24, "cols": 80},
    ]
    share_bootstrap = {
        "view": True,
        "id": "share-stale-layout",
        "mode": "ro",
        "session": "1",
        "sessions": ["1", "2"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": layout_with_finder,
        "tabs": tabs_with_finder,
        "uiState": {
            "layout": layout_with_finder,
            "tabs": tabs_with_finder,
            "terminalDims": terminal_dims,
            "viewport": {"width": 1000, "height": 620},
            "finder": {"root": "/home/test/yolomux.dev", "rootMode": "sync", "mode": "files", "session": "1"},
        },
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=0",
        sessions=["1", "2"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "layout": layout_with_finder, "tabs": tabs_with_finder, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    wait_for_dockview(browser, min_tabs=3)
    result = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFor = window.__yolomuxTestWaitFor;
          applyShareUiMessage({
            ch: 'ui',
            type: 'ui-state',
            sender: 'host-browser',
            epoch: 2,
            sequence: 10,
            reason: 'newer-full-ui-state',
            payload: {
              layout: arguments[0],
              tabs: arguments[1],
              terminalDims: arguments[2],
              viewport: {width: 1000, height: 620},
              finder: {root: '/home/test/yolomux.dev', rootMode: 'sync', mode: 'files', session: '2'},
            },
          });
          const uiApplied = await waitFor(() => !itemInLayout(fileExplorerItemId) && activeItemForSide('left') === '2');
          applyShareUiMessage({
            ch: 'ui',
            type: 'layout',
            sender: 'host-browser',
            epoch: 1,
            sequence: 99,
            reason: 'stale-layout',
            payload: {layout: arguments[3], tabs: arguments[4]},
          });
          await frame();
          await frame();
          await frame();
          return {
            uiApplied,
            finderInLayout: itemInLayout(fileExplorerItemId),
            active: activeItemForSide('left'),
            visibleTabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            layout: layoutParamValue(layoutSlots),
            tabs: layoutTabsParamValue(layoutSlots),
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        layout_without_finder,
        tabs_without_finder_active_2,
        terminal_dims,
        layout_with_finder,
        tabs_with_finder,
    )
    assert "error" not in result, result
    assert result["uiApplied"] is True, result
    assert result["finderInLayout"] is False, result
    assert result["active"] == "2", result
    assert "__files__" not in result["visibleTabs"], result
    assert result["layout"] == layout_without_finder, result
    assert result["errors"] == []
    assert result["rejections"] == []


def test_share_topology_snapshot_converges_finder_tab_move_and_editor_mode(browser, tmp_path):
    layout_with_finder = "row@28(left,slot1)"
    tabs_with_finder = "left:__files__;slot1:1"
    share_bootstrap = {
        "view": True,
        "id": "share-topology-snapshot",
        "mode": "ro",
        "session": "1",
        "sessions": ["1"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": layout_with_finder,
        "tabs": tabs_with_finder,
        "uiState": {
            "layout": layout_with_finder,
            "tabs": tabs_with_finder,
            "terminalDims": [{"session": "1", "rows": 24, "cols": 80}],
            "viewport": {"width": 1000, "height": 620},
            "finder": {"root": "/home/test/yolomux.dev", "rootMode": "sync", "mode": "files", "session": "1"},
        },
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=0",
        sessions=["1"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "layout": layout_with_finder, "tabs": tabs_with_finder, "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    wait_for_dockview(browser, min_tabs=2)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFor = window.__yolomuxTestWaitFor;
          const path = '/home/test/yolomux.dev/DONE.md';
          const item = fileEditorItemFor(path);
          openFiles.set(path, {
            kind: 'text',
            content: '# Done\\n',
            original: '# Done\\n',
            dirty: false,
            mtime: 1,
            size: 7,
            gitRoot: '/home/test/yolomux.dev',
            gitTracked: true,
            gitHasHistory: true,
            gitHistory: [{ref: 'HEAD', short: 'HEAD'}],
          });
          const initial = layoutFromParam(arguments[0], `left:__files__;slot1:1,${item}`);
          applyLayoutSlots(initial, {prune: false, preserveMissingFileExplorer: true});
          await waitFor(() => slotForItem(item) === 'slot1' && itemInLayout(fileExplorerItemId));
          await applyShareUiState({
            layout: 'left',
            tabs: `left:1,${item}*`,
            terminalDims: [{session: '1', rows: 24, cols: 80}],
            viewport: {width: 1000, height: 620},
            finder: {root: '/home/test/yolomux.dev', rootMode: 'sync', mode: 'tabber', session: '1'},
            editor: {modes: [{path, item, mode: 'preview'}]},
          });
          const converged = await waitFor(() => !itemInLayout(fileExplorerItemId)
            && slotForItem(item) === 'left'
            && activeItemForSide('left') === item
            && editorViewModeFor(path, item) === 'preview');
          return {
            converged,
            finderInLayout: itemInLayout(fileExplorerItemId),
            itemSlot: slotForItem(item),
            active: activeItemForSide('left'),
            mode: editorViewModeFor(path, item),
            layout: layoutParamValue(layoutSlots),
            tabs: layoutTabsParamValue(layoutSlots),
            visibleTabs: Array.from(document.querySelectorAll('.dockview-pane-tab')).map(tab => tab.dataset.paneTab || ''),
            errors: window.__bootErrors,
            rejections: window.__bootRejections,
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """,
        layout_with_finder,
    )
    assert "error" not in metrics, metrics
    assert metrics["converged"] is True, json.dumps(metrics, indent=2, sort_keys=True)
    assert metrics["finderInLayout"] is False, metrics
    assert metrics["itemSlot"] == "left", metrics
    assert metrics["active"] == "file:/home/test/yolomux.dev/DONE.md", metrics
    assert metrics["mode"] == "preview", metrics
    assert "__files__" not in metrics["visibleTabs"], metrics
    assert "file:/home/test/yolomux.dev/DONE.md" in metrics["visibleTabs"], metrics
    assert metrics["errors"] == []
    assert metrics["rejections"] == []


def test_share_geometry_digest_tolerates_safari_tab_strip_one_pixel_jitter(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-safari-tab-jitter",
        "mode": "ro",
        "session": "1",
        "sessions": ["1", "2"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "row@50(left,slot1)",
        "tabs": "left:1;slot1:2",
        "uiState": {
            "layout": "row@50(left,slot1)",
            "tabs": "left:1;slot1:2",
            "viewport": {"width": 1220, "height": 742},
        },
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=0&shareDebug=1#t=safari-tab-jitter-secret",
        sessions=["1", "2"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "layout": "row@50(left,slot1)", "tabs": "left:1;slot1:2", "uiState": share_bootstrap["uiState"]},
        wrap_app_root=True,
    )
    wait_for_dockview(browser, min_tabs=2)
    metrics = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        (async () => {
          const {frame} = window.__yolomuxTestHelpers;
          const waitFor = window.__yolomuxTestWaitFor;
          setShareViewFit('contain', {persist: false});
          await frame();
          await frame();
          const base = shareGeometryDigestFrame();
          const jitterTabStrip = (entry, amount) => ({
            ...entry,
            rect: {...entry.rect, top: entry.rect.top + amount},
            first: entry.first ? {...entry.first, top: entry.first.top + amount} : null,
            last: entry.last ? {...entry.last, top: entry.last.top + amount} : null,
          });
          const jitterOneSnapshot = {
            ...base.snapshot,
            tabStrips: base.snapshot.tabStrips.map((entry, index) => index === 0 ? jitterTabStrip(entry, 1) : entry),
          };
          const jitterOne = {snapshot: jitterOneSnapshot, digest: shareGeometryDigestValue(jitterOneSnapshot)};
          const oneDiff = shareGeometryFirstDifference(jitterOne, shareGeometryDigestFrame());
          const oneCompare = shareGeometryDigestCompare(jitterOne);
          applyShareGeometryDigest(jitterOne);
          const statusReady = await waitFor(() => document.querySelector('.share-viewer-mirror-status')?.classList.contains('match'));
          const status = document.querySelector('.share-viewer-mirror-status');
          const jitterLargeSnapshot = {
            ...base.snapshot,
            tabStrips: base.snapshot.tabStrips.map((entry, index) => index === 0 ? jitterTabStrip(entry, 3) : entry),
          };
          const jitterLarge = {snapshot: jitterLargeSnapshot, digest: shareGeometryDigestValue(jitterLargeSnapshot)};
          return {
            tabStripCount: base.snapshot.tabStrips.length,
            oneDiff,
            oneCompareMatch: oneCompare.match,
            oneCompareDiff: oneCompare.diff,
            statusReady,
            statusText: status?.textContent || '',
            statusMatch: status?.classList.contains('match') || false,
            largeDiff: shareGeometryFirstDifference(jitterLarge, shareGeometryDigestFrame()),
          };
        })().then(done, error => done({error: String(error), stack: String(error?.stack || '')}));
        """
    )
    assert "error" not in metrics, metrics
    assert metrics["tabStripCount"] >= 1, metrics
    assert metrics["oneDiff"] == "", metrics
    assert metrics["oneCompareMatch"] is True, metrics
    assert metrics["oneCompareDiff"] == "", metrics
    assert metrics["statusReady"] is True, json.dumps(metrics, indent=2, sort_keys=True)
    assert metrics["statusMatch"] is True, metrics
    assert "drift" not in metrics["statusText"], metrics
    assert metrics["largeDiff"] == "tabStrips", metrics


def test_share_geometry_digest_ignores_editor_scroll_height_jitter_in_browser(browser, tmp_path):
    share_bootstrap = {
        "view": True,
        "id": "share-editor-digest",
        "mode": "ro",
        "session": "1",
        "sessions": ["1"],
        "createdBy": "host",
        "expiresAt": 4102444800.0,
        "maxViewers": 5,
        "layout": "left",
        "tabs": "left:1",
        "uiState": {
            "layout": "left",
            "tabs": "left:1",
            "viewport": {"width": 1000, "height": 620},
        },
    }
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        search="?shareReplay=0",
        sessions=["1"],
        access_role="readonly",
        share_bootstrap=share_bootstrap,
        share_status_payload={"ok": True, "active": True, "layout": "left", "tabs": "left:1", "uiState": share_bootstrap["uiState"]},
    )
    WebDriverWait(browser, 5).until(
        lambda driver: driver.execute_script("return typeof shareGeometryDigestFrame === 'function'")
    )
    metrics = browser.execute_script(
        """
        const root = appRootElement();
        const path = '/host/editor.md';
        const item = `file:${path}`;
        const panel = document.createElement('article');
        panel.className = 'panel file-editor-panel';
        panel.dataset.layoutItem = item;
        panel.dataset.filePath = path;
        panel.style.position = 'absolute';
        panel.style.left = '10px';
        panel.style.top = '120px';
        panel.style.width = '520px';
        panel.style.height = '280px';
        panel.innerHTML = '<div class="file-editor-content"><div class="file-editor-codemirror-panel"></div></div>';
        panel._cmView = {scrollDOM: {scrollHeight: 100}};
        openFiles.set(path, {kind: 'text', content: 'same editor content', original: 'same editor content', dirty: false, mtime: 10, size: 19});
        root.appendChild(panel);
        const host = shareGeometryDigestFrame();
        panel._cmView.scrollDOM.scrollHeight = 9999;
        const scrollOnlyDiff = shareGeometryFirstDifference(host, shareGeometryDigestFrame());
        openFiles.set(path, {kind: 'text', content: 'different editor content', original: 'different editor content', dirty: false, mtime: 10, size: 24});
        const contentDiff = shareGeometryFirstDifference(host, shareGeometryDigestFrame());
        return {
          scrollOnlyDiff,
          contentDiff,
          hostEditor: host.snapshot.editors.find(entry => entry.path === path),
          localEditor: shareGeometryDigestFrame().snapshot.editors.find(entry => entry.path === path),
        };
        """
    )
    assert metrics["scrollOnlyDiff"] == "", metrics
    assert metrics["contentDiff"] == "editors", metrics
    assert "scrollHeight" not in metrics["hostEditor"], metrics
    assert metrics["hostEditor"]["contentHash"] != metrics["localEditor"]["contentHash"], metrics
