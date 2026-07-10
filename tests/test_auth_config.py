import json
import re
from pathlib import Path

from yolomux_lib import common
from yolomux_lib import web
from yolomux_lib.locales import locale_registry_payload
from yolomux_lib.locales import normalize_locale
from yolomux_lib.locales import resolve_locale_preference
from yolomux_lib.workdir import available_agent_commands

SOURCE_STATIC_DIR = Path(__file__).resolve().parents[1] / "static_src"


def active_auth_yaml() -> str:
    return """users:
  - username: "keivenc"
    password: "random-password"
    role: "admin"
  - username: "guest"
    password: "guest"
    role: "readonly"
"""


def test_missing_auth_yaml_creates_commented_starter(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.yaml"
    monkeypatch.setattr(common, "AUTH_CONFIG_PATH", auth_path)
    monkeypatch.setattr(common, "login_username", lambda: "keivenc")
    monkeypatch.setattr(common, "random_auth_password", lambda: "random-password")

    assert common.initialize_auth_config(auth_path) == ()

    text = auth_path.read_text(encoding="utf-8")
    assert "\nusers:\n" in text
    assert '#   - username: "keivenc"' in text
    assert '#     password: "random-password"' in text
    assert "guest" not in text
    assert common.read_auth_users(auth_path) == ()
    assert common.auth_setup_required() is True
    assert auth_path.stat().st_mode & 0o777 == 0o600
    assert auth_path.parent.stat().st_mode & 0o777 == 0o700


def test_test_auth_bypass_disables_setup_requirement(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.yaml"
    monkeypatch.setattr(common, "AUTH_CONFIG_PATH", auth_path)
    monkeypatch.setenv(common.TEST_AUTH_BYPASS_ENV, "1")

    assert common.auth_setup_required() is False
    assert auth_path.exists() is False


def test_legacy_placeholder_auth_yaml_is_replaced_with_commented_starter(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.yaml"
    auth_path.write_text(
        """users:
  - username: "user"
    password: "password"
    role: "admin"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(common, "AUTH_CONFIG_PATH", auth_path)
    monkeypatch.setattr(common, "login_username", lambda: "keivenc")
    monkeypatch.setattr(common, "random_auth_password", lambda: "random-password")

    assert common.initialize_auth_config(auth_path) == ()

    text = auth_path.read_text(encoding="utf-8")
    assert "\nusers:\n" in text
    assert '#   - username: "keivenc"' in text
    assert '#     password: "random-password"' in text
    assert "guest" not in text
    assert common.auth_setup_required() is True
    assert auth_path.stat().st_mode & 0o777 == 0o600


def test_uncommented_auth_yaml_is_active(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.yaml"
    auth_path.write_text(active_auth_yaml(), encoding="utf-8")
    auth_path.chmod(0o644)
    auth_path.parent.chmod(0o755)
    monkeypatch.setattr(common, "AUTH_CONFIG_PATH", auth_path)

    users = common.initialize_auth_config(auth_path)
    assert len(users) == 2
    assert users[0].username == "keivenc"
    assert users[0].role == "admin"
    assert common.auth_password_is_hash(users[0].password)
    assert common.auth_password_matches("random-password", users[0].password)
    assert users[1].username == "guest"
    assert users[1].role == "readonly"
    assert common.auth_password_is_hash(users[1].password)
    assert common.auth_password_matches("guest", users[1].password)
    rewritten = auth_path.read_text(encoding="utf-8")
    assert "password_hash:" in rewritten
    assert 'password: "random-password"' not in rewritten
    assert 'password: "guest"' not in rewritten
    assert common.auth_setup_required() is False
    assert auth_path.stat().st_mode & 0o777 == 0o600
    assert auth_path.parent.stat().st_mode & 0o777 == 0o700


def test_auth_cookie_secret_persists(tmp_path):
    secret_path = tmp_path / "auth-cookie-secret"

    first = common.load_auth_cookie_secret(secret_path)
    second = common.load_auth_cookie_secret(secret_path)

    assert len(first) == 32
    assert second == first
    assert secret_path.stat().st_mode & 0o777 == 0o600
    assert secret_path.parent.stat().st_mode & 0o777 == 0o700


def test_auth_cookie_value_survives_secret_reload(monkeypatch, tmp_path):
    secret_path = tmp_path / "auth-cookie-secret"

    monkeypatch.setattr(common, "AUTH_COOKIE_SECRET", common.load_auth_cookie_secret(secret_path))
    first = common.auth_cookie_value("keivenc", "random-password")
    monkeypatch.setattr(common, "AUTH_COOKIE_SECRET", common.load_auth_cookie_secret(secret_path))

    assert common.auth_cookie_value("keivenc", "random-password") == first


def test_invalid_auth_cookie_secret_is_rewritten(tmp_path):
    secret_path = tmp_path / "auth-cookie-secret"
    secret_path.write_text("not-hex\n", encoding="utf-8")

    secret = common.load_auth_cookie_secret(secret_path)

    assert len(secret) == 32
    assert secret_path.read_text(encoding="utf-8").strip() == secret.hex()
    assert secret_path.stat().st_mode & 0o777 == 0o600


def test_commit_time_display_uses_pt(monkeypatch):
    class GitResult:
        stdout = "2026-01-15T12:34:56+00:00\n"

    monkeypatch.setattr(common, "_YOLOMUX_COMMIT_TIME_PT", None)
    monkeypatch.setattr(common.subprocess, "run", lambda *args, **kwargs: GitResult())

    assert common.yolomux_commit_time_pt() == "2026-01-15 04:34:56 PT"


def test_commit_sha_display_uses_git_head(monkeypatch):
    class GitResult:
        stdout = "abcdef123456\n"

    monkeypatch.setattr(common, "_YOLOMUX_COMMIT_SHA", None)
    monkeypatch.setattr(common.subprocess, "run", lambda *args, **kwargs: GitResult())

    assert common.yolomux_commit_sha() == "abcdef123456"


def test_commit_count_display_uses_git_head(monkeypatch):
    class GitResult:
        stdout = "1234\n"

    monkeypatch.setattr(common, "_YOLOMUX_COMMIT_COUNT", None)
    monkeypatch.setattr(common.subprocess, "run", lambda *args, **kwargs: GitResult())

    assert common.yolomux_commit_count() == 1234


def test_main_page_bootstrap_includes_version_commit(monkeypatch):
    monkeypatch.setattr(web, "STATIC_DIR", SOURCE_STATIC_DIR)
    monkeypatch.setattr(web, "available_agent_commands", lambda: [])
    monkeypatch.setattr(web, "agent_auth_status", lambda: {})
    monkeypatch.setattr(web, "rules_status", lambda: {})
    monkeypatch.setattr(web, "settings_payload", lambda: {"settings": {"general": {"language": "en"}}})
    monkeypatch.setattr(web, "yolomux_commit_sha", lambda: "abcdef123456")
    monkeypatch.setattr(web, "yolomux_commit_time_pt", lambda: "2026-01-15 04:34:56 PT")
    monkeypatch.setattr(web, "yolomux_commit_count", lambda: 1234)
    monkeypatch.setattr(web, "yolomux_client_revision", lambda: "client-rev-test")
    monkeypatch.setattr(web, "yolomux_dev_bundle_revision", lambda: "bundle-rev-test")
    monkeypatch.setattr(web, "SERVER_STARTED_AT", 1234.5)

    page = web.html_page([])
    match = re.search(r'<script id="yolomux-bootstrap" type="application/json">(.*?)</script>', page, re.DOTALL)
    assert match
    payload = json.loads(match.group(1))
    assert payload["versionCommit"] == "abcdef123456"
    assert payload["versionCommitCount"] == 1234
    assert payload["clientRevision"] == "client-rev-test"
    assert payload["devBundleRevision"] == "bundle-rev-test"
    assert payload["serverStartedAt"] == 1234.5
    assert payload["serverStartedAtMs"] == 1234500
    assert web.server_string("en", "menu.help.about.sha", sha="abcdef123456") in page
    assert web.server_string("en", "menu.help.about.commits", count="1234") in page


def test_setup_auth_page_recommends_https(monkeypatch):
    monkeypatch.setattr(web, "STATIC_DIR", SOURCE_STATIC_DIR)
    monkeypatch.setattr(web, "login_username", lambda: "keivenc")

    setup_html = web.setup_auth_html()

    recommendation = web.server_string(
        "en",
        "setup.httpsRecommended",
        command="<code>python3 yolomux.py --port 9998 --self-signed</code>",
    )
    assert recommendation in setup_html
    assert "--self-signed" in setup_html
    assert "--host 0.0.0.0" not in setup_html
    edit_label = web.server_string("en", "common.edit")
    assert setup_html.index(recommendation) < setup_html.index(f"{edit_label} <code>")


def test_login_and_setup_screens_show_please_login_for_logged_out_agent(monkeypatch):
    # #39: codex installed but not logged in -> both screens name the exact login command.
    monkeypatch.setattr(web, "STATIC_DIR", SOURCE_STATIC_DIR)
    monkeypatch.setattr(web, "login_username", lambda: "keivenc")
    monkeypatch.setattr(web, "agent_auth_status", lambda *a, **k: {
        "claude": {"installed": True, "logged_in": True},
        "codex": {"installed": True, "logged_in": False},
    })
    login = web.login_html()
    setup = web.setup_auth_html()
    lead = web.server_string("en", "login.agent.login", names="codex")
    notice = web.server_string("en", "login.agent.run", lead=lead, commands="<code>codex login</code>")
    assert notice in login
    assert "codex login" in login
    assert "setup-login-notice" in setup
    assert notice in setup


def test_login_screen_strong_message_when_no_agent_logged_in(monkeypatch):
    monkeypatch.setattr(web, "STATIC_DIR", SOURCE_STATIC_DIR)
    monkeypatch.setattr(web, "agent_auth_status", lambda *a, **k: {
        "claude": {"installed": True, "logged_in": False},
        "codex": {"installed": True, "logged_in": False},
    })
    login = web.login_html()
    lead = web.server_string("en", "login.agent.loginTo", names="Claude or Codex")
    commands = "<code>claude auth login</code> <code>codex login</code>"
    assert web.server_string("en", "login.agent.run", lead=lead, commands=commands) in login


def test_login_screen_has_no_notice_when_agents_logged_in(monkeypatch):
    # All installed agents logged in (codex not installed) -> no login nag.
    monkeypatch.setattr(web, "STATIC_DIR", SOURCE_STATIC_DIR)
    monkeypatch.setattr(web, "login_username", lambda: "keivenc")
    monkeypatch.setattr(web, "agent_auth_status", lambda *a, **k: {
        "claude": {"installed": True, "logged_in": True},
        "codex": {"installed": False, "logged_in": False},
    })
    login = web.login_html()
    assert web.server_string("en", "login.agent.login", names="codex") not in login
    assert web.server_string("en", "login.agent.loginTo", names="Claude or Codex") not in login
    assert "setup-login-notice" not in web.setup_auth_html()


def test_main_page_bootstrap_includes_agent_auth(monkeypatch):
    monkeypatch.setattr(web, "agent_auth_status", lambda *a, **k: {
        "claude": {"installed": True, "logged_in": True},
        "codex": {"installed": True, "logged_in": False},
    })
    page = web.html_page([])
    match = re.search(r'<script id="yolomux-bootstrap" type="application/json">(.*?)</script>', page, re.DOTALL)
    parsed = json.loads(match.group(1))
    assert parsed["agentAuth"]["codex"] == {"installed": True, "logged_in": False}


def test_bootstrap_locale_resolves_language_and_serves_locale_catalogs():
    # bootstrap_locale: an explicit locale passes through; "system"/unknown falls back to en.
    assert web.bootstrap_locale({"settings": {"general": {"language": "en-XA"}}}) == "en-XA"
    assert web.bootstrap_locale({"settings": {"general": {"language": "system"}}}) == "en"
    assert web.bootstrap_locale({}) == "en"
    # /static/locales/<locale>.json is a served JSON asset; path traversal is rejected.
    assert web.static_content_type("locales/en.json") == "application/json; charset=utf-8"
    assert web.static_content_type("locales/en-XA.json") == "application/json; charset=utf-8"
    assert web.static_content_type("locales/../settings.yaml") is None
    assert web.static_content_type("locales/sub/dir.json") is None
    # /static/fonts/<asset>.woff2 is a served font asset for mirror-stable share rendering; traversal is rejected.
    assert web.static_content_type("fonts/yolomux-ui.woff2") == "font/woff2"
    assert web.static_asset_path("fonts/yolomux-ui.woff2").is_file()
    assert web.static_content_type("fonts/../settings.yaml") is None
    assert web.static_content_type("fonts/sub/dir.woff2") is None


def test_pre_auth_brand_wordmark_localizes_yo_lo_glyphs():
    # follow-up: the server-rendered login / auth-setup screens are NOT localized by the JS
    # renderBrandWordmark(), so brand_html must localize YO/LO itself — a Chinese locale showed
    # "YO/LOmux" instead of 優樂mux / 优乐mux.
    assert "優" in web.brand_html(locale="zh-Hant") and "樂" in web.brand_html(locale="zh-Hant")
    assert "优" in web.brand_html(locale="zh-Hans") and "乐" in web.brand_html(locale="zh-Hans")
    # No locale (main app, which re-localizes client-side) and "system" stay English.
    assert ">YO<" in web.brand_html() and ">LO<" in web.brand_html()
    assert ">YO<" in web.brand_html(locale="system")
    # The full pre-auth pages carry the localized wordmark (login_html takes the locale by keyword —
    # its first positional is next_path).
    renderers = [
        ("setup", lambda loc: web.setup_auth_html(loc)),
        ("login", lambda loc: web.login_html(current_locale=loc)),
    ]
    for name, render in renderers:
        zh_hant = render("zh-Hant")
        zh_hans = render("zh-Hans")
        assert "優" in zh_hant and "樂" in zh_hant, f"{name} zh-Hant wordmark"
        assert "优" in zh_hans and "乐" in zh_hans, f"{name} zh-Hans wordmark"
        assert ">YO<" in render("en"), f"{name} en wordmark stays YO/LO"


def test_setup_auth_script_has_no_parallel_english_status_fallbacks():
    source = (Path(__file__).resolve().parents[1] / "static" / "setup-auth.js").read_text(encoding="utf-8")

    for leaked in ("Waiting for auth.yaml changes", "Waiting for server", "Auth updated. Reloading"):
        assert leaked not in source
    assert "setupInitialStatus" in source
    assert "window.__setupStrings" in source


def test_bootstrap_exposes_agent_launch_commands_and_term_always_available():
    # Menu-bar: the new-session menu offers an explicit normal/full-access choice using server-owned
    # commands, and Term is always offered (a plain shell), not greyed "unavailable".
    assert "term" in available_agent_commands(), "Term (a shell) is always launchable"

    def boot(page):
        match = re.search(r'<script id="yolomux-bootstrap" type="application/json">(.*?)</script>', page, re.DOTALL)
        return json.loads(match.group(1))
    yolo = boot(web.html_page([], "admin", dangerously_yolo=True))
    assert yolo["dangerouslyYolo"] is True
    assert yolo["agentLaunchCommands"]["claude"] == {
        "normal": "claude",
        "full_access": "claude --dangerously-skip-permissions",
    }
    assert yolo["agentLaunchCommands"]["codex"] == {
        "normal": "codex",
        "full_access": "codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust",
    }
    assert "term" in yolo["agentLaunchCommands"]
    assert isinstance(yolo["terminalCommands"], list)
    assert all(isinstance(command, str) for command in yolo["terminalCommands"])
    # Normal servers retain the commands for server-side validation but do not expose full-access UI.
    plain = boot(web.html_page([], "admin"))
    assert plain["dangerouslyYolo"] is False
    assert plain["agentLaunchCommands"] == yolo["agentLaunchCommands"]


def test_main_page_bootstrap_includes_resolved_locale():
    page = web.html_page([])
    match = re.search(r'<script id="yolomux-bootstrap" type="application/json">(.*?)</script>', page, re.DOTALL)
    parsed = json.loads(match.group(1))
    assert parsed["locale"] in {"en", "en-XA"}


def test_bootstrap_dev_flag_is_off_by_default_and_set_under_dev():
    # Dev-velocity #1b: the bootstrap carries a dev flag; the client only subscribes to /api/dev-reload
    # when it is true. Off by default so production never auto-reloads.
    def boot(page):
        match = re.search(r'<script id="yolomux-bootstrap" type="application/json">(.*?)</script>', page, re.DOTALL)
        return json.loads(match.group(1))
    assert boot(web.html_page([]))["dev"] is False
    assert boot(web.html_page([], "admin", dev=True))["dev"] is True


def test_bootstrap_json_escapes_breakout_chars_without_html_entities():
    page = web.html_page([])
    match = re.search(r'<script id="yolomux-bootstrap" type="application/json">(.*?)</script>', page, re.DOTALL)
    assert match, "bootstrap script tag is present"
    raw = match.group(1)
    # The YO!agent answer-format default is embedded as JSON, not HTML; it must not contain a raw
    # </script> breakout or HTML-entity escaped text that would survive JSON.parse.
    assert "&lt;" not in raw
    assert "</script>" not in raw
    parsed = json.loads(raw)
    fmt = parsed["settingsPayload"]["settings"]["yoagent"]["format"]
    assert "columns: tmux session, full path, last worked, details" in fmt
    assert "[`2`](?yoagent-session=2)" in fmt
    assert "&lt;" not in fmt


def test_main_page_bootstrap_defers_preferences_metadata():
    page = web.html_page([])
    match = re.search(r'<script id="yolomux-bootstrap" type="application/json">(.*?)</script>', page, re.DOTALL)
    parsed = json.loads(match.group(1))
    settings_payload = parsed["settingsPayload"]

    assert settings_payload["deferred_metadata"] is True
    assert "settings" in settings_payload
    assert "defaults" in settings_payload
    assert "catalog" not in settings_payload
    assert "choices" not in settings_payload
    assert settings_payload["localeKeyOverrides"] == {
        "appearance.preview_font_size": {"label": "common.previewFontSize"},
        "file_explorer.quick_access_paths": {"label": "common.quickPaths"},
        "general.language": {"label": "common.language"},
        "github.watched_prs": {"label": "common.watchedPrs"},
    }


def test_main_page_has_logout_button():
    page = web.html_page([])

    assert 'id="logoutButton"' in page
    assert 'aria-label="Log out"' in page
    assert '"repoRoot":' in page
    assert 'id="fileExplorer"' in page
    assert 'id="fileExplorerTree"' in page
    assert 'id="panelPool"' in page
    assert 'id="fileEditor"' not in page


def test_locale_registry_normalizes_untrusted_values_and_browser_languages():
    assert normalize_locale("pt-br") == "pt-BR"
    assert normalize_locale("ZH-hant") == "zh-Hant"
    assert normalize_locale("../../outside") == "en"
    assert normalize_locale("/tmp/ar") == "en"
    assert normalize_locale("bogus") == "en"
    assert web.bootstrap_locale_catalogs("../../outside").keys() == {"en"}
    assert web.static_asset_path("locales/../settings.yaml") is None
    assert web.static_asset_path("locales/%2e%2e%2fsettings.yaml") is None
    assert resolve_locale_preference("system", "zh-TW,zh;q=0.9,en;q=0.8") == "zh-Hant"
    assert resolve_locale_preference("system", "zh-CN,zh;q=0.9") == "zh-Hans"
    assert resolve_locale_preference("system", "fr-CA;q=0.8,de;q=0.9") == "de"
    assert resolve_locale_preference("system", "de;q=0,fr;q=0.8,*;q=1") == "fr"

    registry = locale_registry_payload("fr-CA;q=0.8,de;q=0.9")
    assert registry["fallback"] == "en"
    assert registry["pseudo"] == "en-XA"
    assert registry["systemPreference"] == "system"
    assert registry["systemLocale"] == "de"
    assert [item["id"] for item in registry["locales"]] == [value for value, _label in web.LOCALE_ENDONYMS]
    assert next(item for item in registry["locales"] if item["id"] == "he")["direction"] == "rtl"
    assert next(item for item in registry["locales"] if item["id"] == "de")["direction"] == "ltr"


def test_html_page_system_language_uses_accept_language_for_first_paint(monkeypatch):
    payload = web.settings_payload()
    payload.setdefault("settings", {}).setdefault("general", {})["language"] = "system"
    monkeypatch.setattr(web, "settings_payload", lambda: payload)
    page = web.html_page([], accept_language="he-IL, en;q=0.5")
    match = re.search(r'<script id="yolomux-bootstrap" type="application/json">(.*?)</script>', page, re.DOTALL)
    bootstrap = json.loads(match.group(1))

    assert bootstrap["locale"] == "he"
    assert 'lang="he" dir="rtl"' in page
    assert bootstrap["localeRegistry"] == locale_registry_payload("he-IL, en;q=0.5")
    assert "supportedLocales" not in bootstrap
    assert "localeChoices" not in bootstrap


def test_pre_auth_pages_share_reload_picker_and_localize_first_paint(monkeypatch):
    # Read the source catalogs so this focused pre-build test does not depend on generated locale
    # assets being refreshed by another parallel translation lane.
    monkeypatch.setattr(web, "STATIC_DIR", SOURCE_STATIC_DIR)
    monkeypatch.setattr(web, "agent_auth_status", lambda *args, **kwargs: {
        "claude": {"installed": True, "logged_in": True},
        "codex": {"installed": True, "logged_in": False},
    })

    login = web.login_html(current_locale="system", accept_language="he-IL", secure=False)
    setup = web.setup_auth_html("system", "he-IL")
    for page in (login, setup):
        assert '<html lang="he" dir="rtl">' in page
        assert page.count("preauth-locale.js") == 1
        assert 'name="locale" value="system"' in page

    assert web.server_string("he", "login.documentTitle") in login
    assert web.server_string("he", "login.signIn") in login
    assert web.server_string("he", "login.username") in login
    assert web.server_string("he", "login.password") in login
    assert web.server_string("en", "login.signIn") not in login
    assert web.server_string("he", "setup.documentTitle") in setup
    assert web.server_string("he", "setup.setUp") in setup
    assert web.server_string("en", "setup.setUp") not in setup
    localized_agent_notice = web.server_string("he", "login.agent.login", names="codex")
    assert localized_agent_notice in login and localized_agent_notice in setup
    assert web.server_string("en", "login.agent.login", names="codex") not in login
    assert web.server_string("en", "login.agent.login", names="codex") not in setup
    assert "Highly recommend that you restart with HTTPS" not in login
    assert "Highly recommend that you restart with HTTPS" not in setup

    picker_source = (Path(__file__).resolve().parents[1] / "static" / "preauth-locale.js").read_text(encoding="utf-8")
    assert "document.cookie = `yolomux_locale=${encodeURIComponent(input.value)}" in picker_source
    assert picker_source.index("document.cookie =") < picker_source.index("location.reload()")
