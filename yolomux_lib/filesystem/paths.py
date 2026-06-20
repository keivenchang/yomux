"""Path validation and path-scoped metadata for filesystem APIs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..common import AUTH_CONFIG_PATH
from ..common import AUTH_COOKIE_SECRET_PATH
from ..common import CONFIG_DIR

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


class FilesystemError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:BINARY_SNIFF_BYTES]


def _validated_path(raw: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise FilesystemError("path is required")
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise FilesystemError("path contains illegal characters")
    if not raw.startswith("/") and not raw.startswith("~"):
        raise FilesystemError("path must be absolute")
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
    values = [item for item in raw.split(os.pathsep) if item.strip()] if raw else list(DEFAULT_FS_ROOTS)
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
    home = Path.home()
    return tuple(_normalized_scope_path(path) for path in (
        AUTH_CONFIG_PATH,
        AUTH_COOKIE_SECRET_PATH,
        CONFIG_DIR / "auth.yaml",
        CONFIG_DIR / "auth-cookie-secret",
        home / ".config" / "gitlab-token",
        home / ".cache" / "huggingface" / "token",
        home / ".docker" / "config.json",
        home / ".ngc" / "config",
    ))


def _secret_directories() -> tuple[Path, ...]:
    home = Path.home()
    return tuple(_normalized_scope_path(path) for path in (
        home / ".ssh",
        home / ".gnupg",
        home / ".config" / "gh",
        home / ".config" / "git",
        home / ".docker",
        home / ".ngc",
    ))


def _path_is_secret(path: Path) -> bool:
    resolved = _normalized_scope_path(path)
    if any(resolved == secret for secret in _secret_exact_paths()):
        return True
    if any(resolved == secret or _path_is_within(resolved, secret) for secret in _secret_directories()):
        return True
    parts = resolved.parts
    if any(part in SECRET_DIR_COMPONENTS for part in parts):
        return True
    if resolved.name in SECRET_FILE_NAMES:
        return True
    for suffix in SECRET_DIR_SUFFIXES:
        size = len(suffix)
        for index in range(0, len(parts) - size + 1):
            if parts[index:index + size] == suffix:
                return True
    for suffix in SECRET_FILE_SUFFIXES:
        size = len(suffix)
        if size <= len(parts) and parts[-size:] == suffix:
            return True
    return False


def _ensure_path_allowed(path: Path) -> None:
    resolved = _normalized_scope_path(path)
    roots = _configured_fs_roots()
    if not roots or not any(resolved == root or _path_is_within(resolved, root) for root in roots):
        roots_text = ", ".join(str(root) for root in roots) or "(none)"
        raise FilesystemError(f"path outside configured filesystem roots: {path} (allowed: {roots_text})", status=403)
    if _path_is_secret(path):
        raise FilesystemError(f"path is blocked because it may contain credentials: {path}", status=403)


def _ensure_not_configured_root(path: Path, action: str) -> None:
    resolved = _normalized_scope_path(path)
    if resolved == Path("/"):
        raise FilesystemError(f"refusing to {action} filesystem root", status=403)
    for root in _configured_fs_roots():
        if resolved == root:
            raise FilesystemError(f"refusing to {action} configured filesystem root: {path}", status=403)


def _physical_file_identity(path: Path) -> dict[str, Any]:
    try:
        _ensure_path_allowed(path)
        st = path.stat()
    except (FilesystemError, OSError):
        return {}
    file_id = f"{st.st_dev}:{st.st_ino}"
    return {
        "realpath": os.path.realpath(path),
        "file_id": file_id,
        "file_identity": f"id:{file_id}",
    }
