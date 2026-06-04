# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Authentication configuration and credential helpers for YOLOmux."""

from __future__ import annotations

import getpass
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from pathlib import Path


CONFIG_DIR = Path(os.environ.get("YOLOMUX_CONFIG_DIR", str(Path.home() / ".config" / "yolomux")))
AUTH_CONFIG_PATH = CONFIG_DIR / "auth.yaml"
AUTH_CONFIG_DISPLAY_PATH = "~/.config/yolomux/auth.yaml"
PLACEHOLDER_AUTH_USERNAME = "user"
PLACEHOLDER_AUTH_PASSWORD = "password"
GUEST_AUTH_USERNAME = "guest"
GUEST_AUTH_PASSWORD = "guest"
PASSWORD_HASH_PREFIX = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 210_000


@dataclass(frozen=True)
class AuthUser:
    username: str
    password: str
    role: str


@dataclass(frozen=True)
class AuthIdentity:
    username: str
    password: str
    role: str


def yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def yaml_scalar(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def strip_yaml_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_double:
            escaped = True
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == "#" and not in_single and not in_double:
            return line[:index]
    return line


def parse_yaml_key_value(line: str) -> tuple[str, str] | None:
    key, separator, value = line.partition(":")
    if not separator:
        return None
    key = key.strip()
    if not key:
        return None
    return key, yaml_scalar(value)


def normalize_auth_role(value: str) -> str:
    role = value.strip().lower()
    if role in {"admin", "readonly"}:
        return role
    if role in {"read-only", "read_only", "viewer", "view", "ro"}:
        return "readonly"
    return "readonly"


def auth_user_from_mapping(mapping: dict[str, str]) -> AuthUser | None:
    username = mapping.get("username", "").strip()
    password = mapping.get("password_hash", "") or mapping.get("password", "")
    role = normalize_auth_role(mapping.get("role", mapping.get("access", "readonly")))
    if not username or not password:
        return None
    return AuthUser(username=username, password=password, role=role)


def parse_auth_yaml(text: str) -> tuple[AuthUser, ...]:
    users: list[AuthUser] = []
    in_users = False
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = strip_yaml_comment(raw_line).rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped == "users:":
            in_users = True
            if current is not None:
                user = auth_user_from_mapping(current)
                if user:
                    users.append(user)
                current = None
            continue
        if not in_users:
            continue
        if stripped.startswith("- "):
            if current is not None:
                user = auth_user_from_mapping(current)
                if user:
                    users.append(user)
            current = {}
            remainder = stripped[2:].strip()
            if remainder:
                pair = parse_yaml_key_value(remainder)
                if pair:
                    current[pair[0]] = pair[1]
            continue
        if current is None and stripped.endswith(":"):
            current = {"username": yaml_scalar(stripped[:-1])}
            continue
        if current is None:
            continue
        pair = parse_yaml_key_value(stripped)
        if pair:
            current[pair[0]] = pair[1]
    if current is not None:
        user = auth_user_from_mapping(current)
        if user:
            users.append(user)
    return tuple(users)


def auth_config_text(users: tuple[AuthUser, ...]) -> str:
    lines = [
        "users:",
    ]
    for user in users:
        password_key = "password_hash" if auth_password_is_hash(user.password) else "password"
        lines.extend(
            [
                f"  - username: {yaml_quote(user.username)}",
                f"    {password_key}: {yaml_quote(user.password)}",
                f"    role: {yaml_quote(user.role)}",
            ]
        )
    return "\n".join(lines) + "\n"


def read_auth_users(path: Path) -> tuple[AuthUser, ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    return parse_auth_yaml(text)


def login_username() -> str:
    username = getpass.getuser().strip()
    if username:
        return username
    return os.environ.get("USER", "").strip() or "admin"


def random_auth_password() -> str:
    return secrets.token_urlsafe(18)


def starter_auth_users() -> tuple[AuthUser, ...]:
    return (
        AuthUser(username=login_username(), password=random_auth_password(), role="admin"),
    )


def commented_auth_config_text(users: tuple[AuthUser, ...]) -> str:
    lines = [
        "YOLOmux authentication is disabled until one or more account entries are uncommented.",
        "Uncomment and edit the account entries below, save the file, then refresh the browser.",
        "Keep admin credentials private. Add readonly accounts explicitly only when you need them.",
        "",
    ]
    for line in auth_config_text(users).splitlines():
        if line == "users:":
            lines.append(line)
        else:
            lines.append(f"# {line}" if line else "#")
    return "\n".join((f"# {line}" if line else "#") if index < 4 else line for index, line in enumerate(lines)) + "\n"


def initialize_auth_config(path: Path) -> tuple[AuthUser, ...]:
    if path.exists():
        secure_auth_config_permissions(path)
        users = read_auth_users(path)
        if legacy_placeholder_auth_active(users):
            write_auth_config(path, commented_auth_config_text(starter_auth_users()))
            return ()
        normalized = hash_plaintext_auth_users(users)
        if normalized != users:
            write_auth_config(path, auth_config_text(normalized))
        return normalized
    write_auth_config(path, commented_auth_config_text(starter_auth_users()))
    return ()


def current_auth_users() -> tuple[AuthUser, ...]:
    return initialize_auth_config(AUTH_CONFIG_PATH)


def legacy_placeholder_auth_active(users: tuple[AuthUser, ...]) -> bool:
    return len(users) == 1 and users[0].username == PLACEHOLDER_AUTH_USERNAME and users[0].password == PLACEHOLDER_AUTH_PASSWORD


def auth_password_is_hash(value: str) -> bool:
    parts = str(value or "").split("$")
    return len(parts) == 4 and parts[0] == PASSWORD_HASH_PREFIX


def hash_auth_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), PASSWORD_HASH_ITERATIONS).hex()
    return f"{PASSWORD_HASH_PREFIX}${PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def auth_password_matches(password: str, stored: str) -> bool:
    if not auth_password_is_hash(stored):
        return hmac.compare_digest(password, stored)
    _, iterations_text, salt, expected = stored.split("$", 3)
    try:
        iterations = int(iterations_text)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), iterations).hex()
    return hmac.compare_digest(digest, expected)


def hash_plaintext_auth_users(users: tuple[AuthUser, ...]) -> tuple[AuthUser, ...]:
    return tuple(
        user if auth_password_is_hash(user.password) else AuthUser(username=user.username, password=hash_auth_password(user.password), role=user.role)
        for user in users
    )


def auth_setup_required() -> bool:
    users = current_auth_users()
    return not users or legacy_placeholder_auth_active(users)


AUTH_COOKIE_NAME = "yolomux_auth"
AUTH_LOGOUT_COOKIE_NAME = "yolomux_logged_out"
AUTH_COOKIE_MAX_AGE_SECONDS = 90 * 24 * 60 * 60
AUTH_COOKIE_SECRET_PATH = CONFIG_DIR / "auth-cookie-secret"


def load_auth_cookie_secret(path: Path = AUTH_COOKIE_SECRET_PATH) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.exists():
        path.chmod(0o600)
        raw = path.read_text(encoding="utf-8").strip()
        try:
            secret = bytes.fromhex(raw)
        except ValueError:
            secret = b""
        if len(secret) == 32:
            return secret
    secret = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{secret.hex()}\n")
    path.chmod(0o600)
    return secret


AUTH_COOKIE_SECRET = load_auth_cookie_secret()


def write_auth_config(path: Path, text: str) -> None:
    secure_auth_config_permissions(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    path.chmod(0o600)


def secure_auth_config_permissions(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.exists():
        path.chmod(0o600)


def auth_cookie_value(username: str, password: str) -> str:
    return hmac.new(
        AUTH_COOKIE_SECRET,
        f"{username}:{password}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def auth_identity_for_credentials(username: str, password: str) -> AuthIdentity | None:
    for user in current_auth_users():
        username_matches = hmac.compare_digest(username, user.username)
        password_matches = auth_password_matches(password, user.password)
        if username_matches and password_matches:
            return AuthIdentity(username=user.username, password=user.password, role=user.role)
    return None


AUTH_CONFIG = initialize_auth_config(AUTH_CONFIG_PATH)
