from yolomux_lib import common
from yolomux_lib import web
from yolomux_lib.common import AuthUser


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
    assert '#   - username: "guest"' in text
    assert '#     password: "guest"' in text
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
    assert '#   - username: "guest"' in text
    assert '#     password: "guest"' in text
    assert common.auth_setup_required() is True
    assert auth_path.stat().st_mode & 0o777 == 0o600


def test_uncommented_auth_yaml_is_active(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.yaml"
    auth_path.write_text(active_auth_yaml(), encoding="utf-8")
    auth_path.chmod(0o644)
    auth_path.parent.chmod(0o755)
    monkeypatch.setattr(common, "AUTH_CONFIG_PATH", auth_path)

    assert common.initialize_auth_config(auth_path) == (
        AuthUser(username="keivenc", password="random-password", role="admin"),
        AuthUser(username="guest", password="guest", role="readonly"),
    )
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


def test_main_page_has_logout_button():
    page = web.html_page([])

    assert 'id="logoutButton"' in page
    assert 'aria-label="Log out"' in page
    assert '"repoRoot":' in page
    assert 'id="fileEditorWrap"' in page
    assert 'file-editor-icon-wrap' in page
    assert 'id="fileEditorSave"' in page
    assert 'file-editor-icon-save' in page
    assert "Save (Ctrl/Cmd+S)" in page
