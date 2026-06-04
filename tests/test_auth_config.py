import json
import re

from yolomux_lib import common
from yolomux_lib import web


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


def test_setup_auth_page_recommends_https(monkeypatch):
    monkeypatch.setattr(web, "login_username", lambda: "keivenc")

    setup_html = web.setup_auth_html()

    assert "Highly recommend that you restart with HTTPS" in setup_html
    assert "--self-signed" in setup_html
    assert "--host 0.0.0.0" not in setup_html
    assert setup_html.index("Highly recommend that you restart with HTTPS") < setup_html.index("Edit <code>")


def test_login_and_setup_screens_show_please_login_for_logged_out_agent(monkeypatch):
    # #39: codex installed but not logged in -> both screens name the exact login command.
    monkeypatch.setattr(web, "login_username", lambda: "keivenc")
    monkeypatch.setattr(web, "agent_auth_status", lambda *a, **k: {
        "claude": {"installed": True, "logged_in": True},
        "codex": {"installed": True, "logged_in": False},
    })
    login = web.login_html()
    setup = web.setup_auth_html()
    assert "Please login (codex)" in login
    assert "codex login" in login
    assert "setup-login-notice" in setup
    assert "codex login" in setup


def test_login_screen_strong_message_when_no_agent_logged_in(monkeypatch):
    monkeypatch.setattr(web, "agent_auth_status", lambda *a, **k: {
        "claude": {"installed": True, "logged_in": False},
        "codex": {"installed": True, "logged_in": False},
    })
    login = web.login_html()
    assert "Please login to Claude or Codex" in login
    assert "claude auth login" in login
    assert "codex login" in login


def test_login_screen_has_no_notice_when_agents_logged_in(monkeypatch):
    # All installed agents logged in (codex not installed) -> no login nag.
    monkeypatch.setattr(web, "login_username", lambda: "keivenc")
    monkeypatch.setattr(web, "agent_auth_status", lambda *a, **k: {
        "claude": {"installed": True, "logged_in": True},
        "codex": {"installed": False, "logged_in": False},
    })
    assert "Please login" not in web.login_html()
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


def test_pre_auth_brand_wordmark_localizes_yo_lo_glyphs():
    # DOIT.13 follow-up: the server-rendered login / auth-setup screens are NOT localized by the JS
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


def test_bootstrap_exposes_agent_launch_commands_and_term_always_available():
    # Menu-bar: the new-session menu shows "Claude — <params>" using the launch commands, and Term is
    # always offered (a plain shell), not greyed "unavailable".
    from yolomux_lib.workdir import available_agent_commands

    assert "term" in available_agent_commands(), "Term (a shell) is always launchable"

    def boot(page):
        match = re.search(r'<script id="yolomux-bootstrap" type="application/json">(.*?)</script>', page, re.DOTALL)
        return json.loads(match.group(1))
    yolo = boot(web.html_page([], "admin", dangerously_yolo=True))
    assert yolo["agentLaunchCommands"]["claude"] == "claude --dangerously-skip-permissions"
    assert yolo["agentLaunchCommands"]["codex"] == "codex --dangerously-bypass-approvals-and-sandbox"
    assert "term" in yolo["agentLaunchCommands"]
    # Without --dangerously-yolo the commands carry no bypass flags.
    plain = boot(web.html_page([], "admin"))
    assert plain["agentLaunchCommands"]["claude"] == "claude"
    assert plain["agentLaunchCommands"]["codex"] == "codex"


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


def test_bootstrap_json_not_html_escaped_so_angle_brackets_round_trip():
    # TODO "Bug Fixes": a <script> element's text is NOT HTML-decoded, so the bootstrap JSON must be
    # JSON-unicode-escaped (</>/&), NOT html.escape'd — otherwise a value like the
    # YO!agent answer-format "<topic>" comes back as the literal "&lt;topic&gt;" and the prefs textarea
    # double-escapes it. The default yoagent.format contains "<topic>", so it exercises this.
    page = web.html_page([])
    script = re.search(r'<script id="yolomux-bootstrap" type="application/json">(.*?)</script>', page, re.DOTALL).group(1)
    # The raw script text uses the JSON \u escape for breakout chars, never HTML entities.
    assert "\\u003c" in script and "&lt;" not in script
    # JSON.parse (here json.loads) of the script text yields the literal characters, not entities.
    fmt = json.loads(script)["settingsPayload"]["settings"]["yoagent"]["format"]
    assert "<topic>" in fmt and "&lt;topic&gt;" not in fmt


def test_bootstrap_json_escapes_breakout_chars_without_html_entities():
    page = web.html_page([])
    match = re.search(r'<script id="yolomux-bootstrap" type="application/json">(.*?)</script>', page, re.DOTALL)
    assert match, "bootstrap script tag is present"
    raw = match.group(1)
    # The YO!agent answer-format default contains <topic>; it must NOT be HTML-entity-escaped (that
    # would leave literal &lt;topic&gt; after JSON.parse) and must NOT contain a raw </script> breakout.
    assert "&lt;topic&gt;" not in raw
    assert "\\u003ctopic\\u003e" in raw
    assert "</script>" not in raw
    # The breakout chars round-trip back to literal <, >, & through JSON.parse (json.loads here).
    parsed = json.loads(raw)
    fmt = parsed["settingsPayload"]["settings"]["yoagent"]["format"]
    assert "<topic>" in fmt
    assert "&lt;" not in fmt


def test_main_page_has_logout_button():
    page = web.html_page([])

    assert 'id="logoutButton"' in page
    assert 'aria-label="Log out"' in page
    assert '"repoRoot":' in page
    assert 'id="fileExplorer"' in page
    assert 'id="fileExplorerTree"' in page
    assert 'id="panelPool"' in page
    assert 'id="fileEditor"' not in page
