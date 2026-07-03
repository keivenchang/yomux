from pathlib import Path
import difflib
from http import HTTPStatus
from io import BytesIO
import json
import os
import re
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace
import uuid
from urllib.parse import parse_qsl
from urllib.parse import urlencode
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

import pytest

from yolomux_lib import common
from yolomux_lib import app as app_module
from yolomux_lib import auto_approve_worker as auto_approve_worker_module
from yolomux_lib import control as control_module
from yolomux_lib import events as events_module
from yolomux_lib import settings as settings_module
from yolomux_lib import yolo_rules as yolo_rules_module
from yolomux_lib import server_auth
from yolomux_lib.locales import locale_registry_payload
from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.server import TmuxWebtermHTTPServer
from yolomux_lib.tmux_utils import YOLOMUX_TMUX_SOCKET_ENV

pytestmark = [pytest.mark.browser, pytest.mark.socket]

pytest.importorskip("selenium")
webdriver = pytest.importorskip("selenium.webdriver")
ActionChains = pytest.importorskip("selenium.webdriver.common.action_chains").ActionChains
Options = pytest.importorskip("selenium.webdriver.chrome.options").Options
TimeoutException = pytest.importorskip("selenium.common.exceptions").TimeoutException
SeleniumWebDriverWait = pytest.importorskip("selenium.webdriver.support.ui").WebDriverWait


XDIST_BROWSER_WAIT_FLOOR_SECONDS = 10.0


def browser_wait_timeout(timeout, worker=None):
    requested = max(0.0, float(timeout))
    active_worker = os.environ.get("PYTEST_XDIST_WORKER") if worker is None else worker
    if not active_worker:
        return requested
    return max(requested, XDIST_BROWSER_WAIT_FLOOR_SECONDS)


class WebDriverWait(SeleniumWebDriverWait):
    def __init__(self, driver, timeout, poll_frequency=0.5, ignored_exceptions=None):
        # `-n auto` starts one Chrome per worker. A five-second assertion budget is enough for an
        # isolated browser but randomly expires before an unrelated fixture gets CPU under the full
        # pool. Keep local focused tests fast while giving xdist one shared, still-bounded floor.
        super().__init__(driver, browser_wait_timeout(timeout), poll_frequency, ignored_exceptions)


REPO_ROOT = Path(__file__).resolve().parents[2]

_APP_CSS_CACHE: str | None = None


def new_chrome_driver(window_size: str = "1000,700"):
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not chrome:
        pytest.skip("Chrome/Chromium is not installed")
    options = Options()
    options.binary_location = chrome
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--window-size={window_size}")
    return webdriver.Chrome(options=options)


def fast_pointer_actions(browser):
    # Hover/click tests need real W3C pointer events, not Selenium's human-speed 250 ms travel. Keep
    # roughly three 60 Hz frames so hover paint/popover geometry can settle before the next command.
    # Drag tests keep constructing ActionChains directly so their motion duration remains deliberate.
    return ActionChains(browser, duration=50)


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


def browser_auth_yaml() -> str:
    return """users:
  - username: "keivenc"
    password: "random-password"
    role: "admin"
  - username: "guest"
    password: "guest"
    role: "readonly"
"""


class BrowserFakeTlsContext:
    def wrap_socket(self, *_args, **_kwargs):
        raise AssertionError("plain HTTP share requests should not be TLS-wrapped")


def isolate_browser_runtime_paths(monkeypatch, tmp_path):
    config_dir = tmp_path / "yolomux-config"
    state_dir = tmp_path / "yolomux-state"
    config_dir.mkdir(exist_ok=True)
    state_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("YOLOMUX_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("YOLOMUX_STATE_DIR", str(state_dir))
    state_path = config_dir / "state.json"
    event_log_path = state_dir / "events.jsonl"
    activity_path = state_dir / "activity.json"
    activity_heartbeats_path = state_dir / "activity-heartbeats.jsonl"
    watch_index_path = state_dir / "watch-index.json"
    auto_approve_lock_dir = state_dir / "locks"
    control_socket_dir = Path("/tmp") / f"ycs-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    control_socket_dir.mkdir(mode=0o700)
    for module in (common,):
        monkeypatch.setattr(module, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(module, "STATE_DIR", state_dir)
        monkeypatch.setattr(module, "STATE_PATH", state_path)
        monkeypatch.setattr(module, "EVENT_LOG_PATH", event_log_path)
        monkeypatch.setattr(module, "ACTIVITY_PATH", activity_path)
        monkeypatch.setattr(module, "ACTIVITY_HEARTBEATS_PATH", activity_heartbeats_path)
        monkeypatch.setattr(module, "WATCH_INDEX_PATH", watch_index_path)
        monkeypatch.setattr(module, "AUTO_APPROVE_LOCK_DIR", auto_approve_lock_dir)
        monkeypatch.setattr(module, "CONTROL_SOCKET_DIR", control_socket_dir)
    monkeypatch.setattr(app_module, "ACTIVITY_PATH", activity_path)
    monkeypatch.setattr(app_module, "ACTIVITY_HEARTBEATS_PATH", activity_heartbeats_path)
    monkeypatch.setattr(app_module, "EVENT_LOG_PATH", event_log_path)
    monkeypatch.setattr(app_module, "WATCH_INDEX_PATH", watch_index_path)
    monkeypatch.setattr(app_module, "SESSION_FILES_CACHE_DIR", state_dir / "session-files-cache")
    monkeypatch.setattr(events_module, "STATE_PATH", state_path)
    monkeypatch.setattr(control_module, "CONTROL_SOCKET_DIR", control_socket_dir)
    monkeypatch.setattr(auto_approve_worker_module, "AUTO_APPROVE_LOCK_DIR", auto_approve_lock_dir)
    monkeypatch.setattr(settings_module, "SETTINGS_PATH", config_dir / "settings.yaml")
    monkeypatch.setattr(app_module, "SETTINGS_PATH", config_dir / "settings.yaml")
    monkeypatch.setattr(yolo_rules_module, "YOLO_RULES_PATH", config_dir / "yolo-rules.yaml")
    return SimpleNamespace(config_dir=config_dir, state_dir=state_dir, control_socket_dir=control_socket_dir)


def cleanup_isolated_browser_runtime_paths(paths):
    if paths is None:
        return
    shutil.rmtree(paths.control_socket_dir, ignore_errors=True)


def run_isolated_tmux(runtime, *args, timeout=8):
    return subprocess.run(
        [runtime.tmux_binary, "-S", str(runtime.socket_path), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def capture_isolated_tmux_pane(runtime, session, timeout=8):
    return run_isolated_tmux(runtime, "capture-pane", "-p", "-t", f"{session}:", timeout=timeout).stdout or ""


def wait_for_isolated_tmux_panes(runtime, sessions, predicate, timeout=20, poll_interval=0.4):
    session_names = list(sessions)
    deadline = time.monotonic() + timeout
    panes = {}
    while time.monotonic() < deadline:
        panes = {session: capture_isolated_tmux_pane(runtime, session) for session in session_names}
        if predicate(panes):
            return True, panes
        time.sleep(poll_interval)
    return False, panes


def start_isolated_tmux_runtime(
    monkeypatch,
    tmp_path,
    session_count=1,
    *,
    session_commands=None,
    columns=120,
    rows=36,
):
    tmux_binary = shutil.which("tmux")
    if not tmux_binary:
        pytest.skip("tmux is not installed")
    socket_dir = Path("/tmp") / f"yts-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    socket_dir.mkdir(mode=0o700)
    socket_path = socket_dir / "s"
    commands = dict(session_commands or {})
    session_names = list(commands) if session_commands is not None else [f"yt-{os.getpid()}-{uuid.uuid4().hex[:10]}-{index + 1}" for index in range(session_count)]
    if not session_names:
        shutil.rmtree(socket_dir, ignore_errors=True)
        raise ValueError("at least one isolated tmux session is required")
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))
    runtime = SimpleNamespace(tmux_binary=tmux_binary, socket_path=socket_path, socket_dir=socket_dir, sessions=session_names)
    try:
        for session in session_names:
            args = ["new-session", "-d", "-s", session, "-x", str(columns), "-y", str(rows)]
            command = commands.get(session)
            if command is not None:
                args.append(command)
            result = run_isolated_tmux(runtime, *args, timeout=10)
            if result.returncode != 0:
                raise AssertionError(f"isolated tmux session failed: {result.stderr or result.stdout}")
            if command is None:
                run_isolated_tmux(runtime, "send-keys", "-t", f"{session}:", f"printf 'isolated {session}\\n'", "Enter", timeout=5)
        return runtime
    except Exception:
        stop_isolated_tmux_runtime(runtime)
        raise


def stop_isolated_tmux_runtime(runtime):
    if runtime is None:
        return
    run_isolated_tmux(runtime, "kill-server", timeout=5)
    shutil.rmtree(runtime.socket_dir, ignore_errors=True)


def start_isolated_browser_share_app(monkeypatch, tmp_path, session_count=1, *, dangerously_yolo=True):
    paths = None
    tmux_runtime = None
    try:
        paths = isolate_browser_runtime_paths(monkeypatch, tmp_path)
        tmux_runtime = start_isolated_tmux_runtime(monkeypatch, tmp_path, session_count=session_count)
        app = TmuxWebtermApp(list(tmux_runtime.sessions), dangerously_yolo=dangerously_yolo)
        return SimpleNamespace(app=app, sessions=list(tmux_runtime.sessions), tmux=tmux_runtime, paths=paths)
    except Exception:
        stop_isolated_tmux_runtime(tmux_runtime)
        cleanup_isolated_browser_runtime_paths(paths)
        raise


def stop_isolated_browser_share_app(runtime):
    if runtime is None:
        return
    runtime.app.control_server.stop()
    stop_isolated_tmux_runtime(runtime.tmux)
    cleanup_isolated_browser_runtime_paths(runtime.paths)


def start_browser_share_server(monkeypatch, tmp_path, app, *, tls_context=None, auth_bypass=False):
    auth_path = tmp_path / "auth.yaml"
    auth_path.write_text(browser_auth_yaml(), encoding="utf-8")
    monkeypatch.setattr(common, "AUTH_CONFIG_PATH", auth_path)
    monkeypatch.setattr(server_auth, "current_language_pref", lambda: "system")
    if auth_bypass:
        monkeypatch.setenv(common.TEST_AUTH_BYPASS_ENV, "1")
    server = TmuxWebtermHTTPServer(("127.0.0.1", 0), app, tls_context=tls_context)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop_browser_share_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    if "control_server" in vars(server.app):
        server.app.control_server.stop()


def install_browser_websocket_tracker(driver):
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
        (() => {
          const NativeWebSocket = window.WebSocket;
          if (!NativeWebSocket || NativeWebSocket.__yolomuxTracked) return;
          const tracked = [];
          function TrackedWebSocket(url, protocols) {
            const socket = protocols === undefined ? new NativeWebSocket(url) : new NativeWebSocket(url, protocols);
            tracked.push(socket);
            return socket;
          }
          Object.setPrototypeOf(TrackedWebSocket, NativeWebSocket);
          TrackedWebSocket.prototype = NativeWebSocket.prototype;
          for (const key of ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED']) {
            Object.defineProperty(TrackedWebSocket, key, {value: NativeWebSocket[key]});
          }
          Object.defineProperty(TrackedWebSocket, '__yolomuxTracked', {value: true});
          window.__yolomuxTrackedSockets = tracked;
          window.WebSocket = TrackedWebSocket;
        })();
        """,
    })


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
    driver = new_chrome_driver()
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
                <button class="tab tmux-window-button" data-window-agent="shell"><span class="tmux-window-name-label">bash</span><span class="tmux-window-number-label">0</span></button>
                <button class="tab tmux-window-button active" data-window-agent="codex" aria-pressed="true"><span class="tmux-window-name-label">codex</span><span class="tmux-window-number-label">1</span></button>
                <button class="tab tmux-window-button" data-window-agent="other"><span class="tmux-window-name-label">pytest</span><span class="tmux-window-number-label">2</span></button>
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
          <div id="collapsed-dir" class="file-tree-row kind-dir" aria-expanded="false"><span class="file-tree-icon ui-disclosure-triangle" data-disclosure-expanded="false">›</span><span class="file-tree-name">Alpha</span></div>
          <div id="expanded-dir" class="file-tree-row kind-dir expanded" aria-expanded="true"><span class="file-tree-icon ui-disclosure-triangle" data-disclosure-expanded="true">›</span><span class="file-tree-name">Bravo</span></div>
          <div id="selected-file-row" class="file-tree-row kind-file selected"><span class="file-tree-icon">M</span><span class="file-tree-name">clicked.md</span></div>
          <div id="current-file-row" class="file-tree-row kind-file current-file"><span class="file-tree-icon">M</span><span class="file-tree-name">README.md</span></div>
          <div id="repo-dir" class="file-tree-row kind-dir is-repo repo-non-main"><span class="file-tree-icon ui-disclosure-triangle" data-disclosure-expanded="false">›</span><span class="file-tree-name">yolomux <span class="file-tree-repo-meta">[<span class="file-tree-repo-branch">feature/repo-row</span>]</span></span><span class="file-tree-agent" hidden></span><span class="file-tree-diff"><span class="changes-diff-add">+5</span> <span class="changes-diff-remove">-3</span></span><span class="file-tree-dir-count" hidden></span><span class="file-tree-git-status" hidden></span><span class="file-tree-date" hidden></span></div>
        </div>
        <div id="test-context-menu" class="terminal-context-menu" style="top: 220px; left: 24px;"></div>
        <div id="test-image-preview" class="file-image-preview-popover" style="top: 220px; left: 24px;"></div>
        <div class="pane-tab session-popover-host popover-open" style="position: fixed; top: 220px; left: 24px;">
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
        <div role="button" tabindex="0" class="pane-tab session-popover-host {'active popover-open' if index == 2 else ''}">
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
                      <button class="cm-button" name="next" title="下一项 (Enter)">下一项</button>
                      <button class="cm-button" name="prev" title="上一项 (Shift+Enter)">上一项</button>
                      <button class="cm-button" name="select" data-search-label="全部">全部</button>
                      <label id="match-label"><input id="match-case" type="checkbox">区分大小写</label>
                      <label><input type="checkbox">正则表达式</label>
                      <label data-search-label="全字匹配"><input name="word" type="checkbox">全字匹配</label>
                      <button class="cm-dialog-close" type="button">x</button>
                      <br>
                      <input id="replace-field" class="cm-textfield" name="replace" placeholder="替换">
                      <button class="cm-button" name="replace">替换</button>
                      <button class="cm-button" name="replaceAll" data-search-label="全部">全部替换</button>
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
              <button id="wrap-button" type="button" class="file-editor-wrap-panel active" aria-pressed="true"><span class="file-editor-icon file-editor-icon-wrap" aria-hidden="true"></span></button>
              <button id="diff-button" type="button" class="file-editor-diff-panel active" aria-pressed="true">Differ</button>
              <button id="diff-expand-button" type="button" class="file-editor-diff-expand-panel" aria-pressed="true">↕</button>
              <span id="diff-ref-panel" class="file-editor-diff-ref-panel">
                <span class="diff-ref-controls compact" data-diff-ref-controls data-diff-ref-repo="/repo/app">
                  <label class="diff-ref-control">FROM <input id="from-ref" class="diff-ref-input" data-diff-ref-from value="2eb21b3339/HEAD"></label>
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


def codemirror_todo_diff_overview_texts():
    """Frozen (original, current) pair for the TODO CodeMirror diff-overview fixture.

    Both sides are pinned so the merge-view chunk shape can't drift. ``original`` is the immutable
    ``05f22a8646:TODO.md`` snapshot; ``current`` is a deterministic single-block replacement of it
    (stable common prefix + a rewritten middle + stable common suffix). This preserves the realistic
    large-doc geometry the test asserts while removing the dependency on the moving ``docs/TODO.md``
    roadmap doc, whose edits had grown the diff from one chunk to many and broke the test.
    """
    original = git_show_text("05f22a8646:TODO.md")
    original_lines = original.splitlines(keepends=True)
    # Keep the changed region near the top of the editor (a tiny common prefix) so the deleted-row
    # block widget renders inside the initial viewport. CodeMirror virtualizes the merge view's
    # deleted chunk by the position of the first changed current line; if that line sits below the
    # fold the deleted widgets never mount and ``deletedDomRows`` reads 0.
    prefix = original_lines[:2]
    suffix = original_lines[-2:]
    replacement = [f"rewritten roadmap line {index:03d}\n" for index in range(1, 121)]
    current = "".join(prefix + replacement + suffix)
    return original, current


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
    original, current = codemirror_todo_diff_overview_texts()
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
            const content = view.scrollDOM.querySelector('.cm-content');
            if (content) content.style.minHeight = `${{Math.ceil(rows.totalRows * diffOverviewLineHeight(view, container))}}px`;
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
            const collectMetrics = () => {{
              const overview = container.querySelector('.cm-diff-overview');
              if (!overview) {{
                requestAnimationFrame(collectMetrics);
                return;
              }}
              const overviewRect = overview.getBoundingClientRect();
              const scrollerRect = view.scrollDOM.getBoundingClientRect();
              const verticalTrackBottom = scrollerRect.top + view.scrollDOM.clientHeight;
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
                insertedRangeRows: rows?.bands?.filter(band => band.kind === 'add').reduce((total, band) => total + band.end - band.start, 0),
                removedRangeRows: rows?.bands?.filter(band => band.kind === 'remove').reduce((total, band) => total + band.end - band.start, 0),
                overviewTopDelta: Math.abs(overviewRect.top - scrollerRect.top),
                overviewBottomDelta: Math.abs(overviewRect.bottom - verticalTrackBottom),
              }};
            }};
            updateCodeMirrorDiffOverview(panel, container, {{diff: ''}}, current, original);
            collectMetrics();
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
    original = subprocess.check_output(["git", "show", f"521bbfd:{path}"], cwd=REPO_ROOT, text=True)
    current = subprocess.check_output(["git", "show", f"6a967aaa70:{path}"], cwd=REPO_ROOT, text=True)
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


def codemirror_diff_wrapped_inserted_line_fixture_html():
    """Reproduce the screenshot bug: a long INSERTED line that soft-wraps in the unified merge diff.

    In @codemirror/merge the inserted/deleted text are inline marks (``Decoration.mark`` -> ``<ins>`` /
    ``<del>`` with class ``cm-insertedLine`` / ``cm-deletedLine``) nested inside a block
    ``.cm-line.cm-changedLine``. The earlier diff CSS applied the full-bleed
    ``box-shadow``/``clip-path: inset(0 -100vw)`` trick to those inline marks too; on a soft-wrapped
    inline element that buried the wrapped continuation rows' text under the parent block's green band
    (gutter numbers stayed, text went blank). This fixture builds that exact state with word-wrap on so
    the regression asserts the continuation visual row paints visible inserted text.
    """
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
    app_script = app_bundle_before_boot_script()
    # A long inserted bullet (the screenshot's "### Gate speedup" added block) plus a run of unchanged
    # lines so collapseUnchanged engages and the collapsed-unchanged state is also covered. TAILTOKEN_END
    # is the marker on the LAST wrapped fragment, so seeing it proves the final continuation row rendered.
    long_line = (
        "- The default tools check gate ran the node layout suite twice concurrently once as the "
        "node-syntax lane and again folded into the pytest lane so every push paid the cost a second "
        "time for no added coverage TAILTOKEN_END"
    )
    unchanged = "".join(f"unchanged context line {index:03d}\n" for index in range(40))
    original = "# DONE\n" + unchanged + "tail unchanged a\ntail unchanged b\n"
    current = "# DONE\n" + unchanged + "### Gate speedup\n" + long_line + "\ntail unchanged a\ntail unchanged b\n"
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
          body {{ margin: 0; padding: 8px; display: block; background: #11151d; }}
          #mount {{ width: 460px; height: 520px; }}
          .file-editor-panel {{ width: 460px; height: 520px; }}
          .file-editor-codemirror-panel {{ height: 100%; }}
        </style>
      </head>
      <body class="theme-dark editor-theme-dark file-editor-open">
        <script id="yolomux-bootstrap" type="application/json">{bootstrap}</script>
        <div id="mount"></div>
        <script>{app_script}</script>
        <script>
          (function() {{
            const original = {original_json};
            const current = {current_json};
            const CM = window.YOLOmuxCodeMirror;
            // editor-wrap on the panel + EditorView.lineWrapping is the word-wrap-on state.
            const panel = document.createElement('article');
            panel.className = 'panel file-editor-panel active-pane editor-wrap';
            const container = document.createElement('div');
            container.className = 'file-editor-codemirror-panel file-editor-diff-codemirror';
            panel.append(container);
            document.getElementById('mount').append(panel);
            const view = new CM.EditorView({{
              state: CM.EditorState.create({{
                doc: current,
                extensions: [
                  CM.unifiedMergeView({{original, highlightChanges: false, gutter: true, collapseUnchanged: {{margin: 3, minSize: 8}}}}),
                  CM.lineNumbers(),
                  CM.EditorView.lineWrapping,
                ],
              }}),
              parent: container,
            }});
            panel._cmView = view;
            panel._cmMode = 'diff';
            const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            window.__diffWrapMetrics = async function() {{
              await settle();
              const inserted = Array.from(container.querySelectorAll('.cm-insertedLine'))
                .find(node => node.textContent.includes('TAILTOKEN_END'));
              if (!inserted) {{ await settle(); }}
              const ins = Array.from(container.querySelectorAll('.cm-insertedLine'))
                .find(node => node.textContent.includes('TAILTOKEN_END'));
              if (!ins) return {{insertedFound: false}};
              ins.scrollIntoView({{block: 'center'}});
              await settle();
              const lineHeight = Math.max(1, Number(view.defaultLineHeight || 0));
              const bcr = ins.getBoundingClientRect();
              const wraps = bcr.height > lineHeight * 1.5;
              // Probe every visual row of the wrapped inserted mark. For each row record the topmost
              // painted element (elementsFromPoint) and the rendered text under the caret. The bug
              // painted the parent .cm-changedLine block band OVER continuation rows, so the topmost
              // element was the block, not the inserted mark, and the text was buried.
              const rows = [];
              let rowIndex = 0;
              for (let y = bcr.top + lineHeight * 0.5; y < bcr.bottom - 1; y += lineHeight) {{
                const x = bcr.left + 30;
                const topEl = document.elementsFromPoint(x, y)[0];
                const range = document.caretRangeFromPoint ? document.caretRangeFromPoint(x, y) : null;
                const caretText = range && range.startContainer ? String(range.startContainer.textContent || '') : '';
                rows.push({{
                  rowIndex: rowIndex++,
                  topElInsideInserted: Boolean(topEl && topEl.closest && topEl.closest('.cm-insertedLine')),
                  topElClass: topEl ? (topEl.className || topEl.tagName) : null,
                  caretTextLen: caretText.length,
                }});
              }}
              const continuationRows = rows.slice(1);
              // The collapsed-unchanged widget must stay in normal flow and not vertically overlap the
              // inserted block (W5: it only LOOKED like it floated over the green block because the
              // continuation rows were blank).
              const collapse = container.querySelector('.cm-collapsedLines');
              let collapseOverlapPx = 0;
              let collapsePosition = null;
              if (collapse) {{
                const cr = collapse.getBoundingClientRect();
                const cs = getComputedStyle(collapse);
                collapsePosition = cs.position;
                collapseOverlapPx = Math.max(0, Math.min(cr.bottom, bcr.bottom) - Math.max(cr.top, bcr.top));
              }}
              return {{
                insertedFound: true,
                wraps,
                boundingHeight: Math.round(bcr.height),
                lineHeight,
                hasTailText: ins.textContent.includes('TAILTOKEN_END'),
                rowCount: rows.length,
                rows,
                continuationRowsAllVisible: continuationRows.length > 0
                  && continuationRows.every(row => row.topElInsideInserted && row.caretTextLen > 0),
                collapsePresent: Boolean(collapse),
                collapsePosition,
                collapseOverlapPx: Math.round(collapseOverlapPx),
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
              <section class="changes-repo-group">
                <button type="button" id="modified-files-repo-head" class="changes-repo-head">
                  <span class="ui-disclosure-triangle changes-repo-caret" data-disclosure-expanded="true">›</span>
                  <span class="changes-repo-title">~/yolomux.dev8002</span>
                  <span class="changes-repo-totals"><span class="changes-diff-add">+0</span><span class="changes-diff-remove">-0</span><span class="changes-repo-count">0</span></span>
                </button>
              </section>
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
            <span class="file-tree-agent"><span class="agent-icon codex">A</span></span>
            <span class="file-tree-diff"><span class="changes-diff-add">+7</span><span class="changes-diff-remove">-4</span></span>
            <span class="file-tree-dir-count" hidden></span>
            <span class="file-tree-git-status">M</span>
            <span class="file-tree-date">20 min ago</span>
          </div>
          <div id="status-row-t" class="file-tree-row kind-file git-transcript has-agent" style="padding-left: 92px">
            <span class="file-tree-icon file-icon-code">*</span>
            <span class="file-tree-name">50_editor_settings_runtime.js</span>
            <span class="file-tree-agent"><span class="agent-icon codex">A</span></span>
            <span class="file-tree-diff" hidden></span>
            <span class="file-tree-dir-count" hidden></span>
            <span class="file-tree-git-status">T</span>
            <span class="file-tree-date">2.5 hrs ago</span>
          </div>
          <div id="status-row-q" class="file-tree-row kind-file git-untracked" style="padding-left: 52px">
            <span class="file-tree-icon file-icon-image">*</span>
            <span class="file-tree-name">20260605-026.png</span>
            <span class="file-tree-agent" hidden></span>
            <span class="file-tree-diff" hidden></span>
            <span class="file-tree-dir-count" hidden></span>
            <span class="file-tree-git-status">?</span>
            <span class="file-tree-date">1 hr ago</span>
          </div>
        </div>
        <div class="file-explorer-tree narrow-file-explorer-tree">
          <div id="status-row-long" class="file-tree-row kind-file git-modified has-agent" style="padding-left: 92px">
            <span class="file-tree-icon file-icon-doc">*</span>
            <span class="file-tree-name">TOOLCALLING_STREAMING_CASES.md</span>
            <span class="file-tree-agent"><span class="agent-icon codex">A</span></span>
            <span class="file-tree-diff"><span class="changes-diff-add">+66</span></span>
            <span class="file-tree-dir-count" hidden></span>
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


def live_runtime_boot_fixture_html(settings=None, transcript_current_path="/home/test/yolomux.dev", transcript_git_root="/home/test/yolomux.dev", session_files_payload=None, fs_entries=None, sessions=None, transcript_sessions=None, session_files_payloads=None, terminal_css=".terminal { width: 720px; height: 360px; }", grid_width=1000, grid_height=620, file_explorer_open_intent=None, auto_approve_payload=None, access_role="admin", share_bootstrap=None, share_status_payload=None, wrap_app_root=False, yoagent_chat_mode=None, available_agents=None, agent_auth=None, background_status_payload=None, runtime_script_uri=None):
    css = app_css()
    brand_css = (REPO_ROOT / "static" / "brand.css").read_text(encoding="utf-8")
    script_uri = runtime_script_uri or (REPO_ROOT / "static" / "yolomux.js").as_uri()
    dockview_css_uri = (REPO_ROOT / "static" / "vendor" / "dockview.css").as_uri()
    dockview_script_uri = (REPO_ROOT / "static" / "vendor" / "dockview-core.noStyle.js").as_uri()
    settings = settings or {}
    sessions = sessions or ["1"]
    session_files_payload = session_files_payload or {"session": sessions[0], "files": [], "repos": [], "errors": [], "loaded": True}
    fs_entries = fs_entries or {}
    bootstrap = {
        "sessions": sessions,
        "availableAgents": list(available_agents) if available_agents is not None else ["term"],
        "accessRole": access_role,
        "homePath": "/home/test",
        "repoRoot": "/home/test/yolomux.dev",
        "maxSessionTabs": 9,
        "serverHostname": "localhost",
        "version": "test",
        "versionCommitTime": "test",
        "settingsPayload": {
            "settings": settings,
            "defaults": settings_module.default_settings(),
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
        "localeRegistry": locale_registry_payload(),
        "strings": {"en": json.loads((REPO_ROOT / "static" / "locales" / "en.json").read_text())},
    }
    if share_bootstrap is not None:
        bootstrap["share"] = share_bootstrap
    if agent_auth is not None:
        bootstrap["agentAuth"] = agent_auth
    file_explorer_intent_script = ""
    if file_explorer_open_intent is not None:
        file_explorer_intent_script = f"""
          try {{ sessionStorage.setItem('yolomux.fileExplorerOpen.v1', {json.dumps(str(file_explorer_open_intent))}); }} catch (error) {{}}
        """
    app_root_open = '<div id="appRoot" class="app-root">' if wrap_app_root else ""
    app_root_close = "</div>" if wrap_app_root else ""
    stub_script = """
      window.__bootErrors = [];
      window.__bootRejections = [];
      window.__bootFetches = [];
      window.__bootSockets = [];
      window.__bootSocketInstances = [];
      window.__eventSources = [];
      window.__bootTerminalInstances = [];
      window.__terminalOpened = 0;
      window.__terminalResizeCalls = [];
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
          window.__bootTerminalInstances.push(this);
        }
        open(container) {
          this.element = document.createElement('div');
          this.element.className = 'xterm';
          this.element.textContent = 'fake terminal';
          container.appendChild(this.element);
          window.__terminalOpened += 1;
        }
        resize(cols, rows) {
          this.cols = cols;
          this.rows = rows;
          window.__terminalResizeCalls.push({cols, rows});
        }
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
          window.__bootSocketInstances.push(this);
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
      function emitFixtureClientEvent(type, payload = {}) {
        for (const source of window.__eventSources || []) {
          if (typeof source.emit === 'function') source.emit(type, payload);
        }
      }
      function applyFixtureSettingsPatch(patch) {
        window.__settingsPayload.settings = mergeSettings(window.__settingsPayload.settings || {}, patch || {});
        window.__settingsPayload.mtime_ns = ++window.__settingsMtime;
        emitFixtureClientEvent('settings_changed', {data: window.__settingsPayload});
      }
      function fixtureSettingAnswer(path, before, after, where = 'Preferences -> Appearance') {
        return [
          'Updated this Preference:',
          '',
          '| setting | before | after | where | live apply |',
          '| --- | --- | --- | --- | --- |',
          `| \\`${path}\\` | \\`${before}\\` | \\`${after}\\` | ${where} | \\`live\\` |`,
        ].join('\\n');
      }
      function fixtureYoagentChatResponse(message) {
        const text = String(message || '');
        const lower = text.toLowerCase();
        if (window.__fixtureAccessRole !== 'admin' && /\\b(set|change|switch|make|turn|enable|disable|add|remove|reset)\\b/.test(lower)) {
          return '`appearance.theme` is readable, but changing Preferences requires an admin login. I did not change anything.';
        }
        if (lower.includes('maybe theme')) {
          return 'Which setting do you mean: `appearance.theme` or `appearance.terminal_theme`?';
        }
        if (lower.includes('theme') && lower.includes('light')) {
          applyFixtureSettingsPatch({appearance: {theme: 'light'}});
          return fixtureSettingAnswer('appearance.theme', 'dark', 'light');
        }
        if (lower.includes('active color') && lower.includes('blue')) {
          applyFixtureSettingsPatch({appearance: {active_color: 'blue'}});
          return fixtureSettingAnswer('appearance.active_color', 'green', 'blue');
        }
        if (lower.includes('tab width')) {
          applyFixtureSettingsPatch({appearance: {tab_width: 220}});
          return fixtureSettingAnswer('appearance.tab_width', '180', '220');
        }
        if (lower.includes('font size')) {
          applyFixtureSettingsPatch({appearance: {terminal_font_size: 18}});
          return fixtureSettingAnswer('appearance.terminal_font_size', '13', '18', 'Preferences -> Terminal and Editor');
        }
        if (lower.includes('notification level') || lower.includes('notify level')) {
          applyFixtureSettingsPatch({updates: {notify_level: 'none'}});
          return fixtureSettingAnswer('updates.notify_level', 'patch', 'none', 'Preferences -> Notifications');
        }
        return 'No fixture answer.';
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
      window.__fixtureAccessRole = JSON.parse(document.getElementById('yolomux-bootstrap').textContent).accessRole || 'admin';
      window.__fixtureYoagentMessages = [];
      window.fetch = async (input, options = {}) => {
        const url = new URL(String(input), 'https://localhost');
        const body = options.body ? JSON.parse(options.body || '{}') : null;
        window.__bootFetches.push({path: url.pathname, search: url.search, method: options.method || 'GET', body});
        if (url.pathname === '/api/yoagent/chat' && window.__fixtureYoagentChatMode === 'settings') {
          const message = String(body?.message || '');
          const answer = fixtureYoagentChatResponse(message);
          const now = new Date().toISOString();
          window.__fixtureYoagentMessages.push({role: 'user', content: message, createdAt: now});
          window.__fixtureYoagentMessages.push({role: 'assistant', content: answer, createdAt: now});
          return jsonResponse({
            answer,
            backend: 'yolomux',
            backend_used: 'yolomux',
            deterministic: true,
            timing: {ttfr_ms: 1},
            conversation: {
              messages: window.__fixtureYoagentMessages,
              transcript_path: '/home/test/.local/state/yolomux/yoagent/conversation.jsonl',
              transcript_display_path: '~/.local/state/yolomux/yoagent/conversation.jsonl',
              pending_waits: [],
            },
          });
        }
        if (url.pathname === '/api/settings') {
          if ((options.method || 'GET') === 'POST') {
            const body = JSON.parse(options.body || '{}');
            window.__settingsPayload.settings = mergeSettings(window.__settingsPayload.settings || {}, body.settings || body);
            window.__settingsPayload.mtime_ns = ++window.__settingsMtime;
          }
          return jsonResponse(window.__settingsPayload);
        }
        if (url.pathname === '/api/notify') return jsonResponse({enabled: false});
        if (url.pathname === '/api/create-session') {
          const agent = url.searchParams.get('agent') || 'term';
          const explicitSession = String(window.__fixtureNextCreatedSession || '').trim();
          const numericSessions = window.__fixtureSessions.map(session => Number(session)).filter(Number.isFinite);
          const session = explicitSession || String((numericSessions.length ? Math.max(...numericSessions) : 0) + 1);
          const createSessions = Array.isArray(window.__fixtureCreateSessionRoster)
            ? window.__fixtureCreateSessionRoster.slice()
            : window.__fixtureSessions.slice();
          return jsonResponse({ok: true, created: true, session, sessions: createSessions, agent});
        }
        if (url.pathname === '/api/rename-session') {
          const session = url.searchParams.get('session') || '';
          const newSession = url.searchParams.get('new_name') || session;
          const staleSessions = window.__fixtureSessions.filter(item => item !== session);
          return jsonResponse({ok: true, renamed: true, session, new_session: newSession, sessions: staleSessions});
        }
        if (url.pathname === '/api/ensure-session') return jsonResponse({ok: true, created: false});
        if (url.pathname === '/api/attention-ack') {
          const acknowledged = Array.from(new Set((Array.isArray(body?.keys) ? body.keys : [body?.key]).map(key => String(key || '')).filter(Boolean)));
          const autoPayload = window.__fixtureAutoApprovePayload || {
            session_order: window.__fixtureSessions,
            sessions: Object.fromEntries(window.__fixtureSessions.map(session => [session, {target: session, enabled: false, last_action: 'off'}])),
            rules: {path: '/home/test/.config/yolomux/yolo-rules.yaml', source: 'default', rules: [], errors: []},
          };
          for (const key of acknowledged) {
            let parts = [];
            try { parts = JSON.parse(key); } catch (error) {}
            const session = String(Array.isArray(parts) ? parts[1] || '' : '');
            const state = autoPayload.sessions?.[session];
            if (!state) continue;
            const keys = Array.isArray(state.attention_acks?.keys) ? state.attention_acks.keys : [];
            if (!keys.includes(key)) keys.push(key);
            state.attention_acks = {keys};
            if (state.prompt?.attention_key === key || state.prompt_attention_key === key) {
              if (state.prompt) state.prompt.attention_acknowledged = true;
              state.prompt_attention_acknowledged = true;
            }
            for (const row of Array.isArray(state.agent_windows) ? state.agent_windows : []) {
              if (row.attention_key === key) row.attention_acknowledged = true;
              if (row.cooldown_attention_key === key) row.cooldown_acknowledged = true;
            }
          }
          window.__fixtureAutoApprovePayload = autoPayload;
          emitFixtureClientEvent('auto_approve_changed', {status: 200, data: autoPayload});
          return jsonResponse({ok: true, acknowledged, auto_approve: autoPayload, status: 200});
        }
        if (url.pathname === '/api/auto-approve') {
          return jsonResponse(window.__fixtureAutoApprovePayload || {
            session_order: window.__fixtureSessions,
            sessions: Object.fromEntries(window.__fixtureSessions.map(session => [session, {target: session, enabled: false, last_action: 'off'}])),
            rules: {path: '/home/test/.config/yolomux/yolo-rules.yaml', source: 'default', rules: [], errors: []},
          });
        }
        if (url.pathname === '/api/share') return jsonResponse(window.__fixtureSharePayload || {});
        if (url.pathname === '/api/session-metadata' || url.pathname === '/api/transcripts') {
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
        if (url.pathname === '/api/background/status') return jsonResponse(window.__fixtureBackgroundStatusPayload || {
          owner: true,
          status: 'disabled',
          generation: {},
          current_owner: {},
          roles: {
            'search-index': {role: 'search-index', owner: true, status: 'disabled'},
            'stats-sampler': {role: 'stats-sampler', owner: true, status: 'disabled'},
          },
          search_index: {role: 'search-index', owner: true, mode: 'indexing-server', current_server: {}, owner_server: {}, status: 'disabled'},
        });
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
        {app_root_open}
        <header class="topbar">
          <div class="brand-cell"><div class="brand brand-title title" aria-label="YOLOmux test"><span class="brand-yolo brand-nv">YO</span><span class="brand-lo brand-nv">LO</span><span class="brand-blue">m</span><span class="brand-red">u</span><span class="brand-yellow">x</span><span class="brand-version">test</span></div><span id="httpsWarning" class="transport-warning" hidden></span></div>
          <div id="sessionButtons" class="app-menu-area" aria-label="Application menus"></div>
          <div class="actions">
            <div id="latencyMeter" class="latency-meter topbar-status-surface"><svg class="latency-graph" viewBox="0 0 44 18"><polyline id="latencyLine" class="latency-line" points=""></polyline></svg><span id="latencyNumber" class="latency-number">-- ms</span></div>
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
        <section id="modal" class="modal app-modal-overlay"><div class="modal-dialog"><div class="modal-head"><div id="modalTitle">Transcript</div><button id="closeModal">Close</button></div><pre id="modalBody"></pre></div></section>
        {app_root_close}
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
          window.__fixtureSharePayload = {json.dumps(share_status_payload, separators=(",", ":")) if share_status_payload is not None else "null"};
          window.__fixtureYoagentChatMode = {json.dumps(yoagent_chat_mode)};
          window.__fixtureBackgroundStatusPayload = {json.dumps(background_status_payload, separators=(",", ":")) if background_status_payload is not None else "null"};
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


def load_codemirror_diff_wrapped_inserted_line_fixture(browser, tmp_path):
    page = tmp_path / "codemirror-diff-wrapped-inserted-line.html"
    page.write_text(codemirror_diff_wrapped_inserted_line_fixture_html(), encoding="utf-8")
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


def live_runtime_boot_health(browser):
    return browser.execute_script(
        """
        const root = document.getElementById('appRoot');
        const grid = document.getElementById('grid');
        const rootRect = root?.getBoundingClientRect();
        let activeLocale = '';
        let localeError = '';
        let collapsedPreferenceSectionIds = [];
        let collapsedPreferenceSectionsError = '';
        try {
          activeLocale = typeof i18nActiveLocaleId === 'function' ? i18nActiveLocaleId() : '';
        } catch (error) {
          localeError = String(error?.stack || error);
        }
        try {
          collapsedPreferenceSectionIds = Array.from(collapsedPreferenceSections || []);
        } catch (error) {
          collapsedPreferenceSectionsError = String(error?.stack || error);
        }
        const visiblePanels = Array.from(document.querySelectorAll('.panel')).filter(panel => {
          const rect = panel.getBoundingClientRect();
          const style = getComputedStyle(panel);
          return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
        });
        return {
          documentReady: document.readyState,
          errors: window.__bootErrors || [],
          rejections: window.__bootRejections || [],
          localeError,
          collapsedPreferenceSectionsError,
          activeLocale,
          htmlLang: document.documentElement.lang || '',
          htmlDir: document.documentElement.dir || '',
          bodyTextLength: document.body?.innerText?.length || 0,
          appRootPresent: Boolean(root),
          appRootWidth: rootRect?.width || 0,
          appRootHeight: rootRect?.height || 0,
          gridChildren: grid?.children?.length || 0,
          gridHtmlLength: grid?.innerHTML?.length || 0,
          visiblePanels: visiblePanels.length,
          paneTabs: document.querySelectorAll('.dockview-pane-tab').length,
          collapsedPreferenceSectionIds,
        };
        """
    )


def install_live_runtime_boot_error_tracker(browser):
    browser.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
              window.__bootErrors = [];
              window.__bootRejections = [];
              addEventListener('error', event => window.__bootErrors.push({
                message: event.message || String(event.error || event),
                filename: event.filename || '',
                lineno: event.lineno || 0,
                colno: event.colno || 0,
                stack: event.error?.stack || '',
              }));
              addEventListener('unhandledrejection', event => window.__bootRejections.push(String(event.reason || event)));
            """,
        },
    )


def assert_live_runtime_boot_healthy(browser, case_name, *, expected_locale="en", timeout=8):
    def settled(driver):
        metrics = live_runtime_boot_health(driver)
        fatal = bool(metrics["errors"] or metrics["rejections"] or metrics["localeError"] or metrics["collapsedPreferenceSectionsError"])
        rendered = metrics["gridChildren"] > 0 and metrics["visiblePanels"] > 0 and metrics["paneTabs"] > 0
        return fatal or rendered

    try:
        WebDriverWait(browser, timeout).until(settled)
    except TimeoutException:
        pass
    metrics = live_runtime_boot_health(browser)
    message = f"full-bundle boot case {case_name!r} failed: {metrics}"
    assert metrics["documentReady"] == "complete", message
    assert metrics["errors"] == [], message
    assert metrics["rejections"] == [], message
    assert metrics["localeError"] == "", message
    assert metrics["collapsedPreferenceSectionsError"] == "", message
    assert metrics["activeLocale"] == expected_locale, message
    assert metrics["htmlLang"] == expected_locale, message
    assert metrics["bodyTextLength"] > 0, message
    assert metrics["appRootPresent"] is True, message
    assert metrics["appRootWidth"] > 0 and metrics["appRootHeight"] > 0, message
    assert metrics["gridChildren"] > 0 and metrics["gridHtmlLength"] > 0, message
    assert metrics["visiblePanels"] > 0, message
    assert metrics["paneTabs"] > 0, message
    return metrics


def load_dockview_runtime_boot_fixture(browser, tmp_path, search="", **fixture_kwargs):
    browser.set_window_size(1200, 700)
    fixture_kwargs.setdefault("file_explorer_open_intent", "0")
    load_live_runtime_boot_fixture(browser, tmp_path, search, **fixture_kwargs)


def wait_for_dockview(browser, min_tabs=1):
    # Full-gate browser workers contend for CPU; Dockview is a fixture boot readiness condition, not
    # a product latency budget, so leave enough room for the concurrent lane to schedule it.
    WebDriverWait(browser, 8).until(
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
