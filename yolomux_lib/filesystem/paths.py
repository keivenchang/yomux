"""Path validation and path-scoped metadata for filesystem APIs."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..common import AUTH_CONFIG_PATH
from ..common import AUTH_COOKIE_SECRET_PATH
from ..common import CONFIG_DIR
from .errors import FilesystemError

MAX_READ_BYTES = 20 * 1024 * 1024  # 20 MB cap on file read
BINARY_SNIFF_BYTES = 8 * 1024  # bytes inspected for NUL when classifying
FS_ROOTS_ENV = "YOLOMUX_FS_ROOTS"
DEFAULT_FS_ROOTS = ("/",)
SECRET_DIR_COMPONENTS = frozenset({
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
})
SECRET_FILE_NAMES = frozenset({
    ".netrc",
    ".npmrc",
    ".pypirc",
})
SECRET_DIR_SUFFIXES = (
    (".config", "gh"),
    (".config", "git"),
)
SECRET_FILE_SUFFIXES = (
    (".config", "gitlab-token"),
    (".cache", "huggingface", "token"),
    (".docker", "config.json"),
    (".ngc", "config"),
)


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:BINARY_SNIFF_BYTES]


def _validated_path(raw: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise FilesystemError("path is required", message_key="fs.error.pathRequired")
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise FilesystemError("path contains illegal characters", message_key="fs.error.pathIllegal")
    if not raw.startswith("/") and not raw.startswith("~"):
        raise FilesystemError("path must be absolute", message_key="fs.error.pathAbsolute")
    path = Path(os.path.expanduser(raw))
    _ensure_path_allowed(path)
    return path


def _canonical_root(path: Path) -> Path:
    """Use the real directory as a search/index root so symlink aliases don't duplicate results."""
    return _normalized_scope_path(path)


def _normalized_scope_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _configured_fs_roots() -> tuple[Path, ...]:
    raw = os.environ.get(FS_ROOTS_ENV, "")
    return _configured_fs_roots_for_value(raw, tuple(str(root) for root in DEFAULT_FS_ROOTS))


@lru_cache(maxsize=32)
def _configured_fs_roots_for_value(raw: str, default_roots: tuple[str, ...]) -> tuple[Path, ...]:
    """Canonical configured roots, cached until the configuration value changes."""
    values = [item for item in raw.split(os.pathsep) if item.strip()] if raw else list(default_roots)
    roots: list[Path] = []
    for value in values:
        try:
            root = _normalized_scope_path(Path(os.path.expanduser(value.strip())))
        except OSError:
            continue
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _secret_exact_paths() -> tuple[Path, ...]:
    return _secret_exact_paths_for_values(
        str(Path.home()), str(AUTH_CONFIG_PATH), str(AUTH_COOKIE_SECRET_PATH), str(CONFIG_DIR),
    )


@lru_cache(maxsize=32)
def _secret_exact_paths_for_values(home_value: str, auth_config: str, auth_cookie_secret: str, config_dir: str) -> tuple[Path, ...]:
    home = Path(home_value)
    return tuple(_normalized_scope_path(path) for path in (
        Path(auth_config),
        Path(auth_cookie_secret),
        Path(config_dir) / "auth.yaml",
        Path(config_dir) / "auth-cookie-secret",
        home / ".config" / "gitlab-token",
        home / ".cache" / "huggingface" / "token",
        home / ".docker" / "config.json",
        home / ".ngc" / "config",
    ))


def _secret_directories() -> tuple[Path, ...]:
    return _secret_directories_for_home(str(Path.home()))


@lru_cache(maxsize=32)
def _secret_directories_for_home(home_value: str) -> tuple[Path, ...]:
    home = Path(home_value)
    return tuple(_normalized_scope_path(path) for path in (
        home / ".ssh",
        home / ".gnupg",
        home / ".config" / "gh",
        home / ".config" / "git",
        home / ".docker",
        home / ".ngc",
    ))


def invalidate_path_policy_caches() -> None:
    """Drop canonical policy roots after a filesystem mutation can replace a symlink."""
    _configured_fs_roots_for_value.cache_clear()
    _secret_exact_paths_for_values.cache_clear()
    _secret_directories_for_home.cache_clear()


def _path_is_secret(path: Path, *, resolved: Path | None = None, resolve: bool = True) -> bool:
    """Return whether ``path`` is secret, reusing a caller's canonical path when available."""
    exact_paths = _secret_exact_paths()
    secret_directories = _secret_directories()

    def matches(candidate: Path) -> bool:
        if any(candidate == secret for secret in exact_paths):
            return True
        if any(candidate == secret or _path_is_within(candidate, secret) for secret in secret_directories):
            return True
        parts = candidate.parts
        if any(part in SECRET_DIR_COMPONENTS for part in parts):
            return True
        if candidate.name in SECRET_FILE_NAMES:
            return True
        for suffix in SECRET_DIR_SUFFIXES:
            size = len(suffix)
            for index in range(0, len(parts) - size + 1):
                if parts[index:index + size] == suffix:
                    return True
        return any(
            size <= len(parts) and parts[-size:] == suffix
            for suffix in SECRET_FILE_SUFFIXES
            for size in (len(suffix),)
        )

    lexical = path.expanduser()
    if matches(lexical):
        return True
    if resolved is None:
        if not resolve:
            return False
        resolved = _normalized_scope_path(path)
    return resolved != lexical and matches(resolved)


def _ensure_path_allowed(path: Path, *, resolved: Path | None = None) -> None:
    resolved = resolved if resolved is not None else _normalized_scope_path(path)
    roots = _configured_fs_roots()
    if not roots or not any(resolved == root or _path_is_within(resolved, root) for root in roots):
        roots_text = ", ".join(str(root) for root in roots) or "(none)"
        raise FilesystemError(
            f"path outside configured filesystem roots: {path} (allowed: {roots_text})",
            status=403,
            message_key="fs.error.outsideRoots",
            message_params={"path": str(path)},
            diagnostic=f"allowed roots: {roots_text}",
        )
    if _path_is_secret(path, resolved=resolved):
        raise FilesystemError(
            f"path is blocked because it may contain credentials: {path}",
            status=403,
            message_key="fs.error.credentialBlocked",
            message_params={"path": str(path)},
        )


def _ensure_not_configured_root(path: Path, action: str) -> None:
    resolved = _normalized_scope_path(path)
    if resolved == Path("/"):
        raise FilesystemError(
            f"refusing to {action} filesystem root",
            status=403,
            message_key="fs.error.rootMutation",
            message_params={"action": action},
        )
    for root in _configured_fs_roots():
        if resolved == root:
            raise FilesystemError(
                f"refusing to {action} configured filesystem root: {path}",
                status=403,
                message_key="fs.error.configuredRootMutation",
                message_params={"action": action, "path": str(path)},
            )


def _physical_file_identity(
    path: Path,
    *,
    resolved: Path | None = None,
    stat_result: os.stat_result | None = None,
) -> dict[str, Any]:
    """Return safe file identity without repeating validation/metadata work from listings."""
    try:
        resolved = resolved if resolved is not None else _normalized_scope_path(path)
        _ensure_path_allowed(path, resolved=resolved)
        st = stat_result if stat_result is not None else path.stat()
    except (FilesystemError, OSError):
        return {}
    file_id = f"{st.st_dev}:{st.st_ino}"
    return {
        "realpath": str(resolved),
        "file_id": file_id,
        "file_identity": f"id:{file_id}",
    }
