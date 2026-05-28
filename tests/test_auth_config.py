from yolomux_lib import common
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


def test_commit_time_display_uses_pt(monkeypatch):
    class GitResult:
        stdout = "2026-01-15T12:34:56+00:00\n"

    monkeypatch.setattr(common, "_YOLOMUX_COMMIT_TIME_PT", None)
    monkeypatch.setattr(common.subprocess, "run", lambda *args, **kwargs: GitResult())

    assert common.yolomux_commit_time_pt() == "2026-01-15 04:34:56 PT"
