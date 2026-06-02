from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .common import AUTH_CONFIG_DISPLAY_PATH
from .common import MAX_YOLOMUX_SESSION_TABS
from .common import SERVER_HOSTNAME
from .common import STATIC_DIR
from .common import YOLOMUX_VERSION
from .common import login_username
from .common import xterm_asset_path
from .common import yolomux_commit_time_pt
from .settings import save_settings
from .settings import settings_payload
from .workdir import AGENT_LOGIN_COMMANDS
from .workdir import agent_auth_status
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


# i18n locale catalogs are served from /static/locales/<locale>.json (all-static-fetch). The strict
# pattern (no "/" or ".." in the locale) prevents path traversal.
_LOCALE_ASSET_RE = re.compile(r"^locales/[A-Za-z0-9_-]+\.json$")


def static_content_type(asset: str) -> str | None:
    if _LOCALE_ASSET_RE.match(asset):
        return "application/json; charset=utf-8"
    return STATIC_CONTENT_TYPES.get(asset)


def static_asset_path(asset: str) -> Path | None:
    if asset in {"xterm.css", "xterm.js"}:
        return xterm_asset_path(asset)
    if asset in STATIC_CONTENT_TYPES or _LOCALE_ASSET_RE.match(asset):
        path = STATIC_DIR / asset
        return path if path.is_file() else None
    return None


def bootstrap_locale(settings_data: dict) -> str:
    """Resolve the active UI locale for first paint. 'system' (or unknown) falls back to 'en'."""
    settings = settings_data.get("settings", {}) if isinstance(settings_data, dict) else {}
    language = (settings.get("general") or {}).get("language", "system")
    return language if isinstance(language, str) and language and language != "system" else "en"


def bootstrap_locale_catalogs(locale: str) -> dict:
    """Inline the active locale's catalog (+ the en fallback) so t() resolves on the first render.

    DOIT.8: boot-time surfaces (menu bar, tabs, wordmark) render synchronously before a client-side
    fetch could complete, so the active + fallback catalogs are embedded in the bootstrap. Other locales
    are still fetched from /static/locales on a language switch.
    """
    catalogs: dict = {}
    for code in dict.fromkeys(["en", str(locale or "en")]):
        path = STATIC_DIR / "locales" / f"{code}.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            catalogs[code] = data
    return catalogs


def server_string(locale: str, key: str, **params: str) -> str:
    """Resolve a catalog key SERVER-SIDE (active locale -> en fallback -> key) for the pre-auth shell.

    The login/setup screens are rendered server-side (not by the localized JS), so they look up strings
    directly from the same /static/locales catalogs the client uses, keyed by the saved general.language.
    """
    catalogs = bootstrap_locale_catalogs(locale)
    value = catalogs.get(str(locale or ""), {}).get(key) or catalogs.get("en", {}).get(key) or key
    for name, replacement in params.items():
        value = value.replace("{" + name + "}", str(replacement))
    return value


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
    settings_data = settings_payload()
    bootstrap = {
        "sessions": sessions,
        "availableAgents": available_agent_commands(),
        # DOIT.6 #39: per-agent {installed, logged_in} so the GUI can grey an installed-but-logged-out
        # agent in the new-session picker (cached server-side; not probed per request).
        "agentAuth": agent_auth_status(),
        "accessRole": access_role,
        "homePath": str(Path.home()),
        "repoRoot": str(Path(__file__).resolve().parents[1]),
        "maxSessionTabs": MAX_YOLOMUX_SESSION_TABS,
        "serverHostname": SERVER_HOSTNAME,
        "version": YOLOMUX_VERSION,
        "versionCommitTime": yolomux_commit_time_pt(),
        "settingsPayload": settings_data,
        # i18n (DOIT.8): resolved active locale for first paint ("system" -> en server-side; the client
        # may refine via navigator.language). The active locale's catalog (+ the en fallback) is INLINED
        # so t() resolves SYNCHRONOUSLY on the first render — the menu bar, tabs, and wordmark paint at
        # boot before any fetch could complete. Other locales are still fetched on a language switch.
        "locale": bootstrap_locale(settings_data),
        "strings": bootstrap_locale_catalogs(bootstrap_locale(settings_data)),
        "yoloRulesPayload": rules_status(),
        "codeMirrorAssetUrl": static_asset_url("codemirror.js"),
    }
    # Embed JSON in a <script> tag WITHOUT html.escape: a script element's text content is not
    # HTML-decoded, so html.escape would leave literal &lt;/&gt;/&amp; inside parsed strings (e.g. the
    # YO!agent answer-format <topic> placeholders). Escape only the breakout characters as JSON \u
    # escapes — JSON.parse turns them back into <, >, & and they also prevent a </script> breakout.
    bootstrap_json = (
        json.dumps(bootstrap, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
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


def agent_login_notice_html(css_class: str = "login-warning") -> str:
    # DOIT.6 #39: if an installed agent (claude/codex) is not logged in, tell the user the exact login
    # command on the login + auth-setup screens. If NEITHER installed agent is logged in, lead with a
    # stronger "Please login to Claude or Codex". Returns '' when every installed agent is logged in
    # (or none are installed — a terminal-only host needs no agent login).
    status = agent_auth_status()
    installed = [agent for agent in ("claude", "codex") if status.get(agent, {}).get("installed")]
    logged_out = [agent for agent in installed if not status[agent]["logged_in"]]
    if not installed or not logged_out:
        return ""
    commands = " ".join(f"<code>{html.escape(AGENT_LOGIN_COMMANDS[agent])}</code>" for agent in logged_out)
    if not any(status[agent]["logged_in"] for agent in installed):
        names = " or ".join(agent.capitalize() for agent in logged_out)
        lead = f"Please login to {html.escape(names)}"
    else:
        lead = f"Please login ({html.escape(', '.join(logged_out))})"
    return f'<div class="{html.escape(css_class)}">{lead} — run {commands}</div>'


# DOIT.8 Phase 1: the login-screen language picker (entry point #1). Endonym-labeled (each language in
# its own script) so the pre-auth screen needs no localization; Traditional Chinese before Simplified.
# 'system' = follow the browser. A choice here is saved to general.language after a successful sign-in,
# so all three entry points (login / topbar / Preferences) write the SAME setting.
LOGIN_LOCALE_CHOICES: list[tuple[str, str]] = [
    ("system", "System"),
    ("en", "English"),
    ("zh-Hant", "繁體中文"),
    ("zh-Hans", "简体中文"),
    ("es", "Español"),
    ("ja", "日本語"),
]
_LOGIN_LOCALE_VALUES = {value for value, _ in LOGIN_LOCALE_CHOICES}


def current_language_pref() -> str:
    settings = (settings_payload().get("settings") or {})
    language = (settings.get("general") or {}).get("language", "system")
    return language if isinstance(language, str) and language in _LOGIN_LOCALE_VALUES else "system"


def login_locale_field_html(current: str = "system") -> str:
    options = "".join(
        f'<option value="{html.escape(value, quote=True)}"{" selected" if value == current else ""}>{html.escape(label)}</option>'
        for value, label in LOGIN_LOCALE_CHOICES
    )
    language_label = html.escape(server_string(current, "login.language"))
    return (
        '<label class="login-locale">'
        f"<span>{language_label}</span>"
        f'<select name="locale" aria-label="{language_label}">{options}</select>'
        "</label>"
    )


def save_login_locale(value: str) -> None:
    """Persist a login-screen language choice to general.language (called only AFTER successful auth)."""
    locale = str(value or "").strip()
    if locale in _LOGIN_LOCALE_VALUES:
        save_settings({"general": {"language": locale}})


def login_html(next_path: str = "/", error: str = "", secure: bool = True, current_locale: str = "system") -> str:
    safe_next = html.escape(next_path if next_path.startswith("/") else "/", quote=True)
    # DOIT.8 Phase 1: localize the pre-auth login chrome via the SAVED locale (the JS i18n runtime is not
    # loaded here). The HTTPS warning + agent-login notice carry shell commands, so they stay English.
    sign_in = html.escape(server_string(current_locale, "login.signIn"))
    username_label = html.escape(server_string(current_locale, "login.username"))
    password_label = html.escape(server_string(current_locale, "login.password"))
    show_label = server_string(current_locale, "login.show")
    show_aria = server_string(current_locale, "login.showPassword")
    error_html = f'<div class="login-error" role="alert">{html.escape(error)}</div>' if error else ""
    security_html = "" if secure else '<div class="login-warning">No HTTPS. Highly recommend that you restart with <code>python3 yolomux.py --port 9998 --self-signed</code>.</div>'
    agent_notice_html = agent_login_notice_html()
    toggle_labels = json.dumps({
        "show": show_label,
        "hide": server_string(current_locale, "login.hide"),
        "showAria": show_aria,
        "hideAria": server_string(current_locale, "login.hidePassword"),
    })
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
    <h1>{sign_in}</h1>
    {security_html}
    {agent_notice_html}
    {error_html}
    <form method="post" action="/login" class="login-form">
      <input type="hidden" name="next" value="{safe_next}">
      <label>
        <span>{username_label}</span>
        <input name="username" autocomplete="username" autofocus required>
      </label>
      <label>
        <span>{password_label}</span>
        <span class="password-field">
          <input id="loginPassword" name="password" type="password" autocomplete="current-password" required>
          <button id="togglePassword" class="password-toggle" type="button" aria-label="{html.escape(show_aria, quote=True)}" aria-pressed="false">{html.escape(show_label)}</button>
        </span>
      </label>
      {login_locale_field_html(current_locale)}
      <button type="submit">{sign_in}</button>
    </form>
  </section>
</main>
<script>
(() => {{
  const labels = {toggle_labels};
  const password = document.getElementById('loginPassword');
  const toggle = document.getElementById('togglePassword');
  if (!password || !toggle) return;
  toggle.addEventListener('click', () => {{
    const show = password.type === 'password';
    password.type = show ? 'text' : 'password';
    toggle.textContent = show ? labels.hide : labels.show;
    toggle.setAttribute('aria-label', show ? labels.hideAria : labels.showAria);
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
    agent_notice_html = agent_login_notice_html("setup-login-notice")
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
  {agent_notice_html}
  <p id="setupStatus" class="setup-status">Waiting for auth.yaml changes<span class="setup-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span></p>
</main>
<script src="{static_asset_url("setup-auth.js")}"></script>
</body>
</html>
"""
