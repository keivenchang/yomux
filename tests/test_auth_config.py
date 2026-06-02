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


def test_main_page_bootstrap_includes_resolved_locale():
    page = web.html_page([])
    match = re.search(r'<script id="yolomux-bootstrap" type="application/json">(.*?)</script>', page, re.DOTALL)
    parsed = json.loads(match.group(1))
    assert parsed["locale"] in {"en", "en-XA"}


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
