from __future__ import annotations

import html
import json
from pathlib import Path

from .common import AUTH_CONFIG_DISPLAY_PATH
from .common import MAX_YOLOMUX_SESSION_TABS
from .common import SERVER_HOSTNAME
from .common import STATIC_DIR
from .common import YOLOMUX_VERSION
from .common import login_username
from .common import xterm_asset_path
from .common import yolomux_commit_time_pt
from .settings import settings_payload
from .workdir import available_agent_commands
from .yolo_rules import rules_status


STATIC_CONTENT_TYPES = {
    "brand.css": "text/css; charset=utf-8",
    "login.css": "text/css; charset=utf-8",
    "setup-auth.css": "text/css; charset=utf-8",
    "setup-auth.js": "application/javascript; charset=utf-8",
    "codemirror.js": "application/javascript; charset=utf-8",
    "xterm.css": "text/css; charset=utf-8",
    "xterm.js": "application/javascript; charset=utf-8",
    "yolomux.css": "text/css; charset=utf-8",
    "yolomux.js": "application/javascript; charset=utf-8",
}


def static_content_type(asset: str) -> str | None:
    return STATIC_CONTENT_TYPES.get(asset)


def static_asset_path(asset: str) -> Path | None:
    if asset in {"xterm.css", "xterm.js"}:
        return xterm_asset_path(asset)
    if asset not in STATIC_CONTENT_TYPES:
        return None
    path = STATIC_DIR / asset
    return path if path.is_file() else None


def static_asset_version(asset: str) -> int:
    path = static_asset_path(asset)
    if path is None:
        return 0
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def static_asset_url(asset: str) -> str:
    return f"/static/{asset}?v={static_asset_version(asset)}"


def brand_html(class_name: str = "brand-title", tag: str = "span") -> str:
    version_title = html.escape(f"Last commit: {yolomux_commit_time_pt()}", quote=True)
    return (
        f'<{tag} class="{html.escape(class_name, quote=True)}" aria-label="YOLOmux {html.escape(YOLOMUX_VERSION, quote=True)}">'
        '<span class="brand-yolo brand-green">YO</span>'
        '<span class="brand-lo brand-green">LO</span>'
        '<span class="brand-blue">m</span>'
        '<span class="brand-red">u</span>'
        '<span class="brand-yellow">x</span>'
        f'<span class="brand-version" title="{version_title}">{html.escape(YOLOMUX_VERSION)}</span>'
        f"</{tag}>"
    )


def html_page(sessions: list[str], access_role: str = "admin") -> str:
    bootstrap = {
        "sessions": sessions,
        "availableAgents": available_agent_commands(),
        "accessRole": access_role,
        "homePath": str(Path.home()),
        "repoRoot": str(Path(__file__).resolve().parents[1]),
        "maxSessionTabs": MAX_YOLOMUX_SESSION_TABS,
        "serverHostname": SERVER_HOSTNAME,
        "version": YOLOMUX_VERSION,
        "versionCommitTime": yolomux_commit_time_pt(),
        "settingsPayload": settings_payload(),
        "yoloRulesPayload": rules_status(),
        "codeMirrorAssetUrl": static_asset_url("codemirror.js"),
    }
    bootstrap_json = html.escape(json.dumps(bootstrap, separators=(",", ":")), quote=False)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YOLOmux</title>
<link rel="stylesheet" href="{static_asset_url("xterm.css")}" onerror="this.onerror=null;this.href='https://cdn.jsdelivr.net/npm/@xterm/xterm/css/xterm.css';">
<link rel="stylesheet" href="{static_asset_url("brand.css")}">
<link rel="stylesheet" href="{static_asset_url("yolomux.css")}">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/styles/github-dark.min.css">
<script src="{static_asset_url("xterm.js")}" onerror="this.onerror=null;this.src='https://cdn.jsdelivr.net/npm/@xterm/xterm/lib/xterm.js';"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js" defer></script>
<script src="https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/lib/common.min.js" defer></script>
</head>
<body>
<header class="topbar">
  <div class="brand-cell">
    {brand_html("brand brand-title title", "div")}
    <span id="httpsWarning" class="transport-warning" hidden aria-label="No HTTPS"></span>
  </div>
  <div id="sessionButtons" class="app-menu-area" aria-label="Application menus"></div>
  <div class="actions">
    <div id="latencyMeter" class="latency-meter" title="Browser to YOLOmux latency">
      <svg class="latency-graph" viewBox="0 0 44 18" aria-hidden="true">
        <polyline id="latencyLine" class="latency-line" points=""></polyline>
      </svg>
      <span id="latencyNumber" class="latency-number">-- ms</span>
    </div>
    <button id="notifyToggle" class="notify-toggle" title="notify when a session needs attention">Notify</button>
    <button id="refreshMeta">Refresh</button>
    <button id="logoutButton" title="Log out" aria-label="Log out">Log out</button>
    <span id="status" class="sub">starting</span>
  </div>
</header>
<div id="attentionAlerts" class="attention-alerts" aria-live="polite"></div>
<aside id="fileExplorer" class="file-explorer" hidden aria-label="File Explorer">
  <div class="file-explorer-tree-col">
    <div class="file-explorer-head">
      <button type="button" id="fileExplorerHiddenToggle" class="file-explorer-hidden-toggle" title="Show hidden files (dotfiles)" aria-pressed="false">.*</button>
      <button type="button" id="fileExplorerRootMode" class="file-explorer-root-mode-toggle" title="Root mode: fixed" aria-pressed="false">Root</button>
      <div id="fileExplorerQuickAccess" class="file-explorer-quick-access" aria-label="Quick paths"></div>
      <button type="button" class="file-explorer-header-action" data-file-explorer-new-file title="New file" aria-label="New file">+</button>
      <button type="button" class="file-explorer-header-action" data-file-explorer-new-folder title="New folder" aria-label="New folder">▣</button>
      <button type="button" class="file-explorer-header-action" data-file-explorer-refresh title="Refresh" aria-label="Refresh">↻</button>
      <button type="button" class="file-explorer-header-action" data-file-explorer-collapse title="Collapse all" aria-label="Collapse all">▤</button>
      <input class="file-explorer-path" id="fileExplorerPath" type="text" value="/" spellcheck="false" aria-label="File Explorer root path">
      <button type="button" id="fileExplorerPathCopy" class="path-copy-button file-explorer-path-copy" title="Copy current path" aria-label="Copy current path"></button>
      <button type="button" id="fileExplorerClose" class="file-explorer-close" title="Close File Explorer" aria-label="Close"></button>
    </div>
    <div class="file-explorer-tree" id="fileExplorerTree" role="tree" tabindex="0"></div>
  </div>
</aside>
<main id="grid" class="grid"></main>
<div id="panelPool" class="panel-pool" aria-hidden="true"></div>
<section id="modal" class="modal">
  <div class="modal-head">
    <div id="modalTitle">Transcript</div>
    <button id="closeModal">Close</button>
  </div>
  <pre id="modalBody"></pre>
</section>
<script id="yolomux-bootstrap" type="application/json">{bootstrap_json}</script>
<script src="{static_asset_url("yolomux.js")}"></script>
</body>
</html>
    """


def login_html(next_path: str = "/", error: str = "", secure: bool = True) -> str:
    safe_next = html.escape(next_path if next_path.startswith("/") else "/", quote=True)
    error_html = f'<div class="login-error" role="alert">{html.escape(error)}</div>' if error else ""
    security_html = "" if secure else '<div class="login-warning">No HTTPS. Highly recommend that you restart with <code>python3 yolomux.py --port 9998 --self-signed</code>.</div>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YOLOmux login</title>
<link rel="stylesheet" href="{static_asset_url("brand.css")}">
<link rel="stylesheet" href="{static_asset_url("login.css")}">
</head>
<body>
<main class="login-shell">
  <section class="login-panel">
    <div class="login-brand">{brand_html("brand-title login-brand-title", "div")}</div>
    <h1>Sign in</h1>
    {security_html}
    {error_html}
    <form method="post" action="/login" class="login-form">
      <input type="hidden" name="next" value="{safe_next}">
      <label>
        <span>Username</span>
        <input name="username" autocomplete="username" autofocus required>
      </label>
      <label>
        <span>Password</span>
        <span class="password-field">
          <input id="loginPassword" name="password" type="password" autocomplete="current-password" required>
          <button id="togglePassword" class="password-toggle" type="button" aria-label="Show password" aria-pressed="false">Show</button>
        </span>
      </label>
      <button type="submit">Sign in</button>
    </form>
  </section>
</main>
<script>
(() => {{
  const password = document.getElementById('loginPassword');
  const toggle = document.getElementById('togglePassword');
  if (!password || !toggle) return;
  toggle.addEventListener('click', () => {{
    const show = password.type === 'password';
    password.type = show ? 'text' : 'password';
    toggle.textContent = show ? 'Hide' : 'Show';
    toggle.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    toggle.setAttribute('aria-pressed', show ? 'true' : 'false');
    password.focus();
  }});
}})();
</script>
</body>
</html>
"""


def setup_auth_html() -> str:
    auth_path = html.escape(AUTH_CONFIG_DISPLAY_PATH)
    login = html.escape(login_username())
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YOLOmux auth setup</title>
<link rel="stylesheet" href="{static_asset_url("brand.css")}">
<link rel="stylesheet" href="{static_asset_url("setup-auth.css")}">
</head>
<body>
<main>
  <h1>Set up {brand_html("brand-title setup-brand setup-brand-waiting")}</h1>
  <p id="setupSecurity" class="setup-security">Highly recommend that you restart with HTTPS: <code>python3 yolomux.py --port 9998 --self-signed</code></p>
  <p>Edit <code>{auth_path}</code></p>
  <pre>users:
  - username: "{login}"
    password: "your-admin-password"
    role: "admin"
  - username: "guest"
    password: "guest"
    role: "readonly"</pre>
  <p id="setupStatus" class="setup-status">Waiting for auth.yaml changes<span class="setup-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span></p>
</main>
<script src="{static_asset_url("setup-auth.js")}"></script>
</body>
</html>
"""
