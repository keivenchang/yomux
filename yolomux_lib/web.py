from __future__ import annotations

from .core import *


STATIC_CONTENT_TYPES = {
    "setup-auth.css": "text/css; charset=utf-8",
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


def html_page(sessions: list[str]) -> str:
    bootstrap = {
        "sessions": sessions,
        "availableAgents": available_agent_commands(),
        "homePath": str(Path.home()),
        "maxSessionTabs": MAX_YOLOMUX_SESSION_TABS,
        "serverHostname": SERVER_HOSTNAME,
    }
    bootstrap_json = html.escape(json.dumps(bootstrap, separators=(",", ":")), quote=False)
    return '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>YOLOMux</title>\n<link rel="stylesheet" href="/static/xterm.css" onerror="this.onerror=null;this.href=\'https://cdn.jsdelivr.net/npm/@xterm/xterm/css/xterm.css\';">\n<link rel="stylesheet" href="/static/yolomux.css">\n<script src="/static/xterm.js" onerror="this.onerror=null;this.src=\'https://cdn.jsdelivr.net/npm/@xterm/xterm/lib/xterm.js\';"></script>\n</head>\n<body>\n<header class="topbar">\n  <div class="brand">\n    <div class="title" aria-label="YOLOMux"><span class="brand-greenidia">YOLO</span><span class="brand-blue">M</span><span class="brand-red">u</span><span class="brand-yellow">x</span></div>\n  </div>\n  <div id="sessionButtons" class="session-buttons" aria-label="Sessions"></div>\n  <div class="actions">\n    <div id="latencyMeter" class="latency-meter" title="Browser to YOLOMux latency">\n      <svg class="latency-graph" viewBox="0 0 44 18" aria-hidden="true">\n        <polyline id="latencyLine" class="latency-line" points=""></polyline>\n      </svg>\n      <span id="latencyNumber" class="latency-number">-- ms</span>\n    </div>\n    <button id="notifyToggle" class="notify-toggle" title="notify when a session needs attention">Notify</button>\n    <button id="refreshMeta">Refresh state</button>\n    <span id="status" class="sub">starting</span>\n  </div>\n</header>\n<div id="attentionAlerts" class="attention-alerts" aria-live="polite"></div>\n<main id="grid" class="grid"></main>\n<div id="panelPool" class="panel-pool" aria-hidden="true"></div>\n<section id="modal" class="modal">\n  <div class="modal-head">\n    <div id="modalTitle">Transcript</div>\n    <button id="closeModal">Close</button>\n  </div>\n  <pre id="modalBody"></pre>\n</section>\n' + f'<script id="yolomux-bootstrap" type="application/json">{bootstrap_json}</script>\n<script src="/static/yolomux.js"></script>\n' + '</body>\n</html>\n'


def setup_auth_html() -> str:
    auth_path = html.escape(AUTH_CONFIG_DISPLAY_PATH)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YOLOMux auth setup</title>
<link rel="stylesheet" href="/static/setup-auth.css">
</head>
<body>
<main>
  <h1>Set up YOLOMux auth</h1>
  <p>YOLOMux created <code>{auth_path}</code> with placeholder credentials.</p>
  <p class="accent">Edit that JSON file before using this program.</p>
  <pre>{{
  "user": "your-user",
  "password": "your-password"
}}</pre>
  <p>After saving the file, refresh this page. YOLOMux reads the latest JSON auth on each request.</p>
</main>
</body>
</html>
"""
