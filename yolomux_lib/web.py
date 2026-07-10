from __future__ import annotations

import html
import json
import re
from functools import lru_cache
from pathlib import Path

from .common import AUTH_CONFIG_DISPLAY_PATH
from .common import DEFAULT_LINEAR_ISSUE_BASE_URL
from .common import MAX_YOLOMUX_SESSION_TABS
from .common import SERVER_HOSTNAME
from .common import SERVER_STARTED_AT
from .common import STATIC_DIR
from .common import YOLOMUX_VERSION
from .common import login_username
from .common import xterm_asset_path
from .common import yolomux_client_revision
from .common import yolomux_commit_count
from .common import yolomux_commit_sha
from .common import yolomux_commit_time_pt
from .common import yolomux_dev_bundle_revision
from .locales import LANGUAGE_PREFERENCES
from .locales import LOCALE_ENDONYMS
from .locales import FALLBACK_LOCALE
from .locales import PSEUDO_LOCALE
from .locales import SHIPPED_LOCALES
from .locales import SYSTEM_LOCALE_PREFERENCE
from .locales import locale_direction
from .locales import locale_registry_payload
from .locales import normalize_locale
from .locales import plural_category
from .locales import resolve_locale_preference
from .settings import save_settings
from .settings import settings_payload
from .workdir import AGENT_LOGIN_COMMANDS
from .workdir import agent_auth_status
from .workdir import agent_command
from .workdir import available_agent_commands
from .workdir import available_terminal_commands
from .yolo_rules import rules_status


STATIC_CONTENT_TYPES = {
    "brand.css": "text/css; charset=utf-8",
    "login.css": "text/css; charset=utf-8",
    "setup-auth.css": "text/css; charset=utf-8",
    "setup-auth.js": "application/javascript; charset=utf-8",
    "preauth-locale.js": "application/javascript; charset=utf-8",
    "codemirror.js": "application/javascript; charset=utf-8",
    "emoji-data.js": "application/javascript; charset=utf-8",
    "xterm.css": "text/css; charset=utf-8",
    "xterm.js": "application/javascript; charset=utf-8",
    "xterm-addon-unicode11.js": "application/javascript; charset=utf-8",
    "yolomux.css": "text/css; charset=utf-8",
    "yolomux.js": "application/javascript; charset=utf-8",
    "vendor/dockview.css": "text/css; charset=utf-8",
    "vendor/dockview-core.noStyle.js": "application/javascript; charset=utf-8",
    "vendor/mermaid.min.js": "application/javascript; charset=utf-8",
}


# i18n locale catalogs are served from /static/locales/<locale>.json (all-static-fetch). The strict
# pattern (no "/" or ".." in the locale) prevents path traversal.
_LOCALE_ASSET_RE = re.compile(r"^locales/[A-Za-z0-9_-]+\.json$")
_FONT_ASSET_RE = re.compile(r"^fonts/[A-Za-z0-9_-]+\.woff2$")


def static_content_type(asset: str) -> str | None:
    if _LOCALE_ASSET_RE.match(asset):
        return "application/json; charset=utf-8"
    if _FONT_ASSET_RE.match(asset):
        return "font/woff2"
    return STATIC_CONTENT_TYPES.get(asset)


def static_asset_path(asset: str) -> Path | None:
    if asset in {"xterm.css", "xterm.js", "xterm-addon-unicode11.js"}:
        return xterm_asset_path(asset)
    if asset in STATIC_CONTENT_TYPES or _LOCALE_ASSET_RE.match(asset) or _FONT_ASSET_RE.match(asset):
        path = STATIC_DIR / asset
        return path if path.is_file() else None
    return None


def bootstrap_locale(settings_data: dict, accept_language: str = "") -> str:
    """Resolve and validate the active UI locale for first paint."""
    settings = settings_data.get("settings", {}) if isinstance(settings_data, dict) else {}
    language = (settings.get("general") or {}).get("language", SYSTEM_LOCALE_PREFERENCE)
    return resolve_locale_preference(language, accept_language)


@lru_cache(maxsize=len(SHIPPED_LOCALES) + 1)
def bootstrap_locale_catalogs(locale: str) -> dict:
    """Inline the active locale's catalog (+ the en fallback) so t() resolves on the first render.

    boot-time surfaces (menu bar, tabs, wordmark) render synchronously before a client-side
    fetch could complete, so the active + fallback catalogs are embedded in the bootstrap. Other locales
    are still fetched from /static/locales on a language switch.
    """
    catalogs: dict = {}
    normalized = normalize_locale(locale)
    for code in dict.fromkeys([FALLBACK_LOCALE, normalized]):
        path = STATIC_DIR / "locales" / f"{code}.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            catalogs[code] = data
    return catalogs


def html_lang_dir_attrs(locale: str) -> str:
    """`lang="…" dir="…"` for the <html> shell. Phase 2: RTL locales (ar) get dir="rtl" so the
    server-rendered first paint already mirrors before the JS i18n runtime sets it."""
    code = normalize_locale(locale)
    direction = locale_direction(code)
    return f'lang="{html.escape(code, quote=True)}" dir="{direction}"'


def server_string(locale: str, key: str, **params: str) -> str:
    """Resolve a catalog key SERVER-SIDE (active locale -> en fallback -> key) for the pre-auth shell.

    The login/setup screens are rendered server-side (not by the localized JS), so they look up strings
    directly from the same /static/locales catalogs the client uses, keyed by the saved general.language.
    """
    code = normalize_locale(locale)
    catalogs = bootstrap_locale_catalogs(code)
    value = catalogs.get(code, {}).get(key) or catalogs.get(FALLBACK_LOCALE, {}).get(key) or key
    for name, replacement in params.items():
        value = value.replace("{" + name + "}", str(replacement))
    return value


def server_plural(locale: str, key: str, count: object, **params: object) -> str:
    """Resolve one plural family with the same active/English fallback order as the browser."""
    code = normalize_locale(locale)
    catalogs = bootstrap_locale_catalogs(code)
    category = plural_category(code, count)
    active = catalogs.get(code, {})
    fallback = catalogs.get(FALLBACK_LOCALE, {})
    value = (
        active.get(f"{key}.{category}")
        or active.get(f"{key}.other")
        or fallback.get(f"{key}.{category}")
        or fallback.get(f"{key}.other")
        or key
    )
    for name, replacement in {**params, "count": count}.items():
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


def brand_html(class_name: str = "brand-title", tag: str = "span", locale: str | None = None) -> str:
    active_locale = normalize_locale(locale)
    commit_count = yolomux_commit_count()
    commit_count_line = f"\n{server_string(active_locale, 'menu.help.about.commits', count=commit_count)}" if commit_count > 0 else ""
    version_title = html.escape(
        f"{server_string(active_locale, 'menu.help.about.sha', sha=yolomux_commit_sha())}\n"
        f"{server_string(active_locale, 'menu.help.lastCommit', time=yolomux_commit_time_pt())}{commit_count_line}",
        quote=True,
    )
    # follow-up: the server-rendered pre-auth screens (login / auth-setup) are NOT localized by
    # the JS renderBrandWordmark(), so localize the YO/LO glyphs here too — otherwise a Chinese locale
    # showed "YO/LOmux" instead of 優樂mux / 优乐mux. Pass a locale on those pages; the main app leaves it
    # None (English) and re-localizes client-side after bootstrap.
    yo = html.escape(server_string(active_locale, "brand.marker"))
    lo = html.escape(server_string(active_locale, "brand.wordmark.lo"))
    brand_aria = html.escape(server_string(active_locale, "brand.version", version=YOLOMUX_VERSION), quote=True)
    update_title = html.escape(server_string(active_locale, "update.badgeTitle"), quote=True)
    update_aria = html.escape(server_string(active_locale, "update.badgeAria"), quote=True)
    update_label = html.escape(server_string(active_locale, "update.badgeLabel"))
    return (
        f'<{tag} class="{html.escape(class_name, quote=True)}" aria-label="{brand_aria}">'
        f'<span class="brand-yolo brand-green">{yo}</span>'
        f'<span class="brand-lo brand-green">{lo}</span>'
        '<span class="brand-blue">m</span>'
        '<span class="brand-red">u</span>'
        '<span class="brand-yellow">x</span>'
        f'<span class="brand-version" title="{version_title}">{html.escape(YOLOMUX_VERSION)}</span>'
        '<button type="button" class="brand-update-badge" data-update-badge hidden '
        f'title="{update_title}" aria-label="{update_aria}">{update_label}</button>'
        f"</{tag}>"
    )


def bootstrap_agent_auth_status() -> dict[str, dict[str, object]]:
    try:
        return agent_auth_status(block=False, allow_stale=True, refresh=True)
    except TypeError:
        return agent_auth_status()


def bootstrap_settings_payload(settings_data: dict) -> dict:
    """Return first-paint settings without the heavy Preferences-only metadata."""
    if not isinstance(settings_data, dict):
        return {}
    payload = {key: settings_data[key] for key in ("settings", "defaults", "path", "display_path", "mtime_ns", "error") if key in settings_data}
    catalog = settings_data.get("catalog") if isinstance(settings_data.get("catalog"), dict) else {}
    locale_key_overrides: dict[str, dict[str, str]] = {}
    for path, entry in catalog.items():
        locale_keys = entry.get("locale_keys") if isinstance(entry, dict) else {}
        if not isinstance(locale_keys, dict):
            continue
        defaults = {"description": f"pref.{path}.help", "label": f"pref.{path}.label"}
        overrides = {name: str(value) for name, value in locale_keys.items() if value and value != defaults.get(name)}
        if overrides:
            locale_key_overrides[str(path)] = overrides
    if locale_key_overrides:
        payload["localeKeyOverrides"] = locale_key_overrides
    if catalog or "choices" in settings_data:
        payload["deferred_metadata"] = True
    return payload


def html_page(
    sessions: list[str],
    access_role: str = "admin",
    dev: bool = False,
    dangerously_yolo: bool = False,
    share: dict | None = None,
    accept_language: str = "",
    auth_username: str = "",
    recent_sessions: list[str] | None = None,
) -> str:
    settings_data = settings_payload()
    locale = bootstrap_locale(settings_data, accept_language)
    bootstrap = {
        "sessions": sessions,
        "recentSessions": recent_sessions if isinstance(recent_sessions, list) else sessions,
        # Dev-velocity #1b: when true the page subscribes to /api/dev-reload and reloads on bundle change.
        "dev": dev,
        # Full-access agent launch controls are deliberately available only when the server operator
        # explicitly opted in with --dangerously-yolo.
        "dangerouslyYolo": dangerously_yolo,
        "availableAgents": available_agent_commands(),
        "terminalCommands": available_terminal_commands(),
        # The new-session menu exposes both explicit launch modes. Keep the command source server-owned
        # so the UI describes the exact flags the selected Claude/Codex session will receive.
        "agentLaunchCommands": {
            agent: {
                "normal": agent_command(agent, False),
                "full_access": agent_command(agent, True),
            }
            for agent in ("claude", "codex", "term")
        },
        # per-agent {installed, logged_in} so the GUI can grey an installed-but-logged-out
        # agent in the new-session picker (cached server-side; not probed per request).
        "agentAuth": bootstrap_agent_auth_status(),
        "accessRole": access_role,
        "authUsername": auth_username if not share else "",
        "homePath": str(Path.home()),
        "repoRoot": str(Path(__file__).resolve().parents[1]),
        "maxSessionTabs": MAX_YOLOMUX_SESSION_TABS,
        "serverHostname": SERVER_HOSTNAME,
        "serverStartedAt": SERVER_STARTED_AT,
        "serverStartedAtMs": int(SERVER_STARTED_AT * 1000),
        "linearIssueBaseUrl": DEFAULT_LINEAR_ISSUE_BASE_URL,
        "version": YOLOMUX_VERSION,
        "clientRevision": yolomux_client_revision(),
        "devBundleRevision": yolomux_dev_bundle_revision(),
        "versionCommit": yolomux_commit_sha(),
        "versionCommitTime": yolomux_commit_time_pt(),
        "versionCommitCount": yolomux_commit_count(),
        "settingsPayload": bootstrap_settings_payload(settings_data),
        # i18n: resolved active locale for first paint plus the canonical registry that every client
        # uses for normalization, system resolution, endonyms, and direction. The active locale's catalog (+ the en fallback) is INLINED
        # so t() resolves SYNCHRONOUSLY on the first render — the menu bar, tabs, and wordmark paint at
        # boot before any fetch could complete. Other locales are still fetched on a language switch.
        "locale": locale,
        "localeRegistry": locale_registry_payload(accept_language),
        "strings": bootstrap_locale_catalogs(locale),
        "yoloRulesPayload": rules_status(),
        "codeMirrorAssetUrl": static_asset_url("codemirror.js"),
        "share": share or None,
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
<html {html_lang_dir_attrs(bootstrap["locale"])}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(server_string(locale, "app.documentTitle"))}</title>
<link rel="stylesheet" href="{static_asset_url("xterm.css")}" onerror="this.onerror=null;this.href='https://cdn.jsdelivr.net/npm/@xterm/xterm/css/xterm.css';">
<link rel="stylesheet" href="{static_asset_url("brand.css")}">
<link rel="stylesheet" href="{static_asset_url("vendor/dockview.css")}">
<link rel="stylesheet" href="{static_asset_url("yolomux.css")}">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/github-dark.min.css">
<script src="{static_asset_url("xterm.js")}" onerror="this.onerror=null;this.src='https://cdn.jsdelivr.net/npm/@xterm/xterm/lib/xterm.js';"></script>
<script src="{static_asset_url("xterm-addon-unicode11.js")}" onerror="this.onerror=null;this.src='https://cdn.jsdelivr.net/npm/@xterm/addon-unicode11/lib/addon-unicode11.js';"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js" defer></script>
<script src="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/highlight.min.js" defer></script>
</head>
<body>
<div id="appRoot" class="app-root">
<header class="topbar">
  <div class="brand-cell">
    {brand_html("brand brand-title title", "div", locale=locale)}
    <span id="httpsWarning" class="transport-warning" hidden aria-label="{html.escape(server_string(locale, "app.noHttps"), quote=True)}"></span>
  </div>
  <div id="sessionButtons" class="app-menu-area" aria-label="{html.escape(server_string(locale, "app.menusAria"), quote=True)}"></div>
  <div class="actions">
    <div id="latencyMeter" class="latency-meter topbar-status-surface" title="{html.escape(server_string(locale, "app.latencyTitle"), quote=True)}">
      <svg class="latency-graph" viewBox="0 0 44 18" aria-hidden="true">
        <polyline id="latencyLine" class="latency-line" points=""></polyline>
      </svg>
      <span id="latencyNumber" class="latency-number">-- ms</span>
    </div>
    <button id="notifyToggle" class="notify-toggle" title="{html.escape(server_string(locale, "notify.toggleTitle", state=server_string(locale, "state.off")), quote=True)}">{html.escape(server_string(locale, "pref.section.notifications"))}</button>
    <button id="refreshMeta">{html.escape(server_string(locale, "common.refresh"))}</button>
    <button id="logoutButton" title="{html.escape(server_string(locale, "menu.file.logout"), quote=True)}" aria-label="{html.escape(server_string(locale, "menu.file.logout"), quote=True)}">{html.escape(server_string(locale, "menu.file.logout"))}</button>
    <span id="status" class="sub a11y-only" role="status" aria-live="polite" aria-atomic="true">{html.escape(server_string(locale, "state.starting"))}</span>
  </div>
</header>
<div id="attentionAlerts" class="attention-alerts" aria-live="polite"></div>
<aside id="fileExplorer" class="file-explorer" hidden aria-label="{html.escape(server_string(locale, "finder.label.explorer"), quote=True)}">
  <div class="file-explorer-tree-col">
    <div class="file-explorer-head">
      <input class="file-explorer-path" id="fileExplorerPath" type="text" value="/" spellcheck="false" aria-label="{html.escape(server_string(locale, "finder.toolbar.rootPath", name=server_string(locale, "finder.label.explorer")), quote=True)}">
      <button type="button" id="fileExplorerPathCopy" class="path-copy-button file-explorer-path-copy" title="{html.escape(server_string(locale, "finder.toolbar.copyPath"), quote=True)}" aria-label="{html.escape(server_string(locale, "finder.toolbar.copyPath"), quote=True)}"></button>
      <button type="button" class="file-explorer-header-action" data-file-explorer-collapse title="{html.escape(server_string(locale, "common.collapseAll"), quote=True)}" aria-label="{html.escape(server_string(locale, "common.collapseAll"), quote=True)}">▤</button>
      <button type="button" id="fileExplorerHiddenToggle" class="file-explorer-hidden-toggle" title="{html.escape(server_string(locale, "finder.toolbar.hidden"), quote=True)}" aria-pressed="false">.*</button>
      <button type="button" id="fileExplorerRootMode" class="file-explorer-root-mode-toggle active" title="{html.escape(server_string(locale, "finder.toolbar.syncTitle"), quote=True)}" aria-pressed="true">{html.escape(server_string(locale, "finder.toolbar.syncLabel"))}</button>
      <div id="fileExplorerQuickAccess" class="file-explorer-quick-access" aria-label="{html.escape(server_string(locale, "common.quickPaths"), quote=True)}"></div>
      <button type="button" class="file-explorer-header-action" data-file-explorer-new-file title="{html.escape(server_string(locale, "finder.toolbar.newFile"), quote=True)}" aria-label="{html.escape(server_string(locale, "finder.toolbar.newFile"), quote=True)}">+</button>
      <button type="button" class="file-explorer-header-action" data-file-explorer-new-folder title="{html.escape(server_string(locale, "finder.toolbar.newFolder"), quote=True)}" aria-label="{html.escape(server_string(locale, "finder.toolbar.newFolder"), quote=True)}">▣</button>
      <button type="button" class="file-explorer-header-action" data-file-explorer-refresh title="{html.escape(server_string(locale, "common.refresh"), quote=True)}" aria-label="{html.escape(server_string(locale, "common.refresh"), quote=True)}">↻</button>
      <button type="button" id="fileExplorerClose" class="file-explorer-close" title="{html.escape(server_string(locale, "finder.close", name=server_string(locale, "finder.label.explorer")), quote=True)}" aria-label="{html.escape(server_string(locale, "common.close"), quote=True)}"></button>
    </div>
    <div class="file-explorer-tree" id="fileExplorerTree" role="tree" tabindex="0"></div>
  </div>
</aside>
<main id="grid" class="grid"></main>
<div id="panelPool" class="panel-pool" aria-hidden="true"></div>
<section id="modal" class="modal app-modal-overlay">
  <div class="modal-dialog">
    <div class="modal-head">
      <div id="modalTitle">{html.escape(server_string(locale, "common.transcript"))}</div>
      <button id="closeModal" title="{html.escape(server_string(locale, "common.close"), quote=True)}" aria-label="{html.escape(server_string(locale, "common.close"), quote=True)}">X</button>
    </div>
    <pre id="modalBody"></pre>
  </div>
</section>
</div>
<script id="yolomux-bootstrap" type="application/json">{bootstrap_json}</script>
<script src="{static_asset_url("vendor/dockview-core.noStyle.js")}"></script>
<script src="{static_asset_url("yolomux.js")}"></script>
</body>
</html>
    """


def agent_login_notice_html(css_class: str = "login-warning", locale: str = FALLBACK_LOCALE) -> str:
    # if an installed agent (claude/codex) is not logged in, tell the user the exact login
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
        lead = server_string(locale, "login.agent.loginTo", names=names)
    else:
        lead = server_string(locale, "login.agent.login", names=", ".join(logged_out))
    notice = server_string(locale, "login.agent.run", lead=html.escape(lead), commands=commands)
    return f'<div class="{html.escape(css_class)}">{notice}</div>'


# Phase 1: the login-screen language picker (entry point #1). Endonym-labeled in the same
# product-priority order as the client switchers. 'system' = follow the browser. A choice here is saved
# to general.language after a successful sign-in, so all three entry points write the SAME setting.
LOGIN_LOCALE_CHOICES: list[tuple[str, str]] = [(SYSTEM_LOCALE_PREFERENCE, "System"), *LOCALE_ENDONYMS]
_LOGIN_LOCALE_VALUES = set(LANGUAGE_PREFERENCES - {PSEUDO_LOCALE})


def current_language_pref() -> str:
    settings = (settings_payload().get("settings") or {})
    language = (settings.get("general") or {}).get("language", SYSTEM_LOCALE_PREFERENCE)
    return normalize_locale(language, default=SYSTEM_LOCALE_PREFERENCE, allow_system=True)


def login_appearance_class() -> str:
    """Return the saved, non-sensitive appearance choices for the pre-auth login shell.

    The login page intentionally does not load the authenticated application bundle, but its
    first paint should still look like the application the user just left.  These two settings
    are presentation-only and are already validated by ``settings_payload``; keep a defensive
    allow-list here because this class is rendered into HTML before authentication.
    """
    try:
        settings = settings_payload().get("settings") or {}
    except (OSError, ValueError):
        settings = {}
    appearance = settings.get("appearance") if isinstance(settings, dict) else {}
    appearance = appearance if isinstance(appearance, dict) else {}
    theme = str(appearance.get("theme") or "dark")
    accent = str(appearance.get("active_color") or "green")
    if theme not in {"dark", "light", "system"}:
        theme = "dark"
    if accent not in {"green", "blue", "orange", "yellow", "purple", "white"}:
        accent = "green"
    return f"login-theme-{theme} login-accent-{accent}"


def locale_field_html(current: str = SYSTEM_LOCALE_PREFERENCE, css_class: str = "login-locale", display_locale: str | None = None) -> str:
    """The endonym-labeled language picker, shared by the login and setup screens."""
    locale = display_locale or current

    def option_label(value: str, label: str) -> str:
        return server_string(locale, "pref.general.language.system") if value == SYSTEM_LOCALE_PREFERENCE else label

    selected_value = current if current in _LOGIN_LOCALE_VALUES else SYSTEM_LOCALE_PREFERENCE
    selected_label = option_label(selected_value, dict(LOGIN_LOCALE_CHOICES).get(selected_value, "System"))
    options = "".join(
        (
            f'<button type="button" role="option" class="locale-option" data-locale-value="{html.escape(value, quote=True)}"'
            f' aria-selected="{"true" if value == selected_value else "false"}">'
            f"{html.escape(option_label(value, label))}</button>"
        )
        for value, label in LOGIN_LOCALE_CHOICES
    )
    language_label = html.escape(server_string(locale, "common.language"))
    return (
        f'<label class="{html.escape(css_class, quote=True)}">'
        f"<span>{language_label}</span>"
        f'<span class="locale-picker" data-locale-picker>'
        f'<input type="hidden" name="locale" value="{html.escape(selected_value, quote=True)}" data-locale-input>'
        f'<button type="button" class="locale-toggle" aria-label="{language_label}" aria-haspopup="listbox" aria-expanded="false" data-locale-toggle>{html.escape(selected_label)}</button>'
        f'<span class="locale-options" role="listbox" aria-label="{language_label}" hidden>{options}</span>'
        f'</span>'
        "</label>"
    )


def login_locale_field_html(current: str = SYSTEM_LOCALE_PREFERENCE, display_locale: str | None = None) -> str:
    return locale_field_html(current, "login-locale", display_locale)


def save_login_locale(value: str) -> None:
    """Persist a login-screen language choice to general.language (called only AFTER successful auth)."""
    locale = str(value or "").strip()
    if locale in _LOGIN_LOCALE_VALUES:
        save_settings({"general": {"language": locale}})


def login_html(next_path: str = "/", error: str = "", secure: bool = True, current_locale: str = SYSTEM_LOCALE_PREFERENCE, accept_language: str = "") -> str:
    locale = resolve_locale_preference(current_locale, accept_language)
    safe_next = html.escape(next_path if next_path.startswith("/") else "/", quote=True)
    # Phase 1: localize the pre-auth login chrome via the SAVED locale (the JS i18n runtime is not
    # loaded here). The HTTPS warning + agent-login notice carry shell commands, so they stay English.
    sign_in = html.escape(server_string(locale, "login.signIn"))
    username_label = html.escape(server_string(locale, "login.username"))
    password_label = html.escape(server_string(locale, "login.password"))
    show_label = server_string(locale, "login.show")
    show_aria = server_string(locale, "login.showPassword")
    error_html = f'<div class="login-error" role="alert">{html.escape(error)}</div>' if error else ""
    command = "python3 yolomux.py --port 9998 --self-signed"
    security_html = "" if secure else (
        f'<div class="login-warning">{server_string(locale, "login.noHttps", command=f"<code>{command}</code>")}</div>'
    )
    agent_notice_html = agent_login_notice_html(locale=locale)
    toggle_labels = json.dumps({
        "show": show_label,
        "hide": server_string(locale, "common.hide"),
        "showAria": show_aria,
        "hideAria": server_string(locale, "login.hidePassword"),
    })
    return f"""<!doctype html>
<html {html_lang_dir_attrs(locale)}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(server_string(locale, "login.documentTitle"))}</title>
<link rel="stylesheet" href="{static_asset_url("brand.css")}">
<link rel="stylesheet" href="{static_asset_url("login.css")}">
</head>
<body class="{login_appearance_class()}">
<main class="login-shell">
  <section class="login-panel">
    <div class="login-brand">{brand_html("brand-title login-brand-title", "div", locale=locale)}</div>
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
      {login_locale_field_html(current_locale, locale)}
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
<script src="{static_asset_url("preauth-locale.js")}"></script>
</body>
</html>
"""


def setup_auth_html(current_locale: str = SYSTEM_LOCALE_PREFERENCE, accept_language: str = "") -> str:
    # Localize the pre-auth setup chrome via the resolved locale (the JS i18n runtime is not loaded
    # here), the same way login_html() does. The auth.yaml example block is literal config, not UI.
    # The locale is carried pre-auth by request_locale_pref (query/cookie), so the
    # setup-screen picker can switch language WITHOUT writing settings.
    preference = normalize_locale(current_locale, default=SYSTEM_LOCALE_PREFERENCE, allow_system=True)
    locale = resolve_locale_preference(preference, accept_language)
    locale_picker = locale_field_html(preference, "setup-locale", locale)
    auth_path = html.escape(AUTH_CONFIG_DISPLAY_PATH)
    login = html.escape(login_username())
    agent_notice_html = agent_login_notice_html("setup-login-notice", locale)
    set_up = html.escape(server_string(locale, "setup.setUp"))
    edit_label = html.escape(server_string(locale, "common.edit"))
    # The status line is also rewritten client-side on each poll, so the same localized strings are
    # handed to setup-auth.js via window.__setupStrings.
    setup_strings = json.dumps({
        "waiting": server_string(locale, "setup.waiting"),
        "waitingServer": server_string(locale, "setup.waitingServer"),
        "authUpdated": server_string(locale, "setup.authUpdated"),
    })
    waiting = html.escape(server_string(locale, "setup.waiting"))
    return f"""<!doctype html>
<html {html_lang_dir_attrs(locale)}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(server_string(locale, "setup.documentTitle"))}</title>
<link rel="stylesheet" href="{static_asset_url("brand.css")}">
<link rel="stylesheet" href="{static_asset_url("setup-auth.css")}">
</head>
<body>
<main>
  {locale_picker}
  <h1>{set_up} {brand_html("brand-title setup-brand setup-brand-waiting", locale=locale)}</h1>
  <p id="setupSecurity" class="setup-security">{server_string(locale, "setup.httpsRecommended", command="<code>python3 yolomux.py --port 9998 --self-signed</code>")}</p>
  <p>{edit_label} <code>{auth_path}</code></p>
  <pre>users:
  - username: "{login}"
    password: "your-admin-password"
    role: "admin"
  - username: "guest"
    password: "guest"
    role: "readonly"</pre>
  {agent_notice_html}
  <p id="setupStatus" class="setup-status">{waiting}<span class="setup-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span></p>
</main>
<script>window.__setupStrings = {setup_strings};</script>
<script src="{static_asset_url("preauth-locale.js")}"></script>
<script src="{static_asset_url("setup-auth.js")}"></script>
</body>
</html>
"""
