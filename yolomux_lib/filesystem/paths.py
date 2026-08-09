"""Path validation and path-scoped metadata for filesystem APIs."""

from __future__ import annotations

import contextvars
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

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
_PATH_POLICY_GENERATION = 0


@dataclass(frozen=True)
class _CompiledSecretPolicy:
    generation: int
    exact_paths: frozenset[str]
    secret_directories: frozenset[str]
    secret_dir_components: frozenset[str]
    secret_file_names: frozenset[str]
    secret_dir_suffixes: tuple[tuple[int, frozenset[tuple[str, ...]]], ...]
    secret_file_suffixes: tuple[tuple[int, frozenset[tuple[str, ...]]], ...]


class AuthorizationObserver(Protocol):
    """Test observer for the two descriptor-authorization race boundaries."""

    def name_observed(self, operation: str, requested_path: Path) -> None: ...

    def authority_pinned(self, operation: str, requested_path: Path) -> None: ...


_AUTHORIZATION_OBSERVER: contextvars.ContextVar[AuthorizationObserver | None] = contextvars.ContextVar(
    "filesystem_authorization_observer",
    default=None,
)


@contextmanager
def observe_authorization(observer: AuthorizationObserver) -> Iterator[None]:
    """Install a request-local test observer without changing process-global state."""

    token = _AUTHORIZATION_OBSERVER.set(observer)
    try:
        yield
    finally:
        _AUTHORIZATION_OBSERVER.reset(token)


def name_observed(operation: str, requested_path: Path) -> None:
    observer = _AUTHORIZATION_OBSERVER.get()
    if observer is not None and operation:
        observer.name_observed(operation, requested_path)


def authority_pinned(operation: str, requested_path: Path) -> None:
    observer = _AUTHORIZATION_OBSERVER.get()
    if observer is not None and operation:
        observer.authority_pinned(operation, requested_path)


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:BINARY_SNIFF_BYTES]


def nofollow_flag() -> int:
    """Return the no-follow open flag or reject an unsafe platform."""

    flag = getattr(os, "O_NOFOLLOW", 0)
    if not flag:
        raise FilesystemError(
            "safe no-follow filesystem opens are unsupported on this platform",
            status=500,
            message_key="fs.error.operationFailed",
        )
    return flag


def validate_request_path_lexical(raw: str) -> str:
    """Return the one lexical acceptance rule every filesystem request must pass.

    These three refusals read only the request string: no descriptor, no authorization, no
    filesystem access and no name service.  That is what makes them safe on the web thread, which
    applies them before it accepts an operation, so a request the worker would refuse can never be
    accepted as a 202 receipt.  This is the only implementation of the rule; the jobd worker
    reaches it through ``parsed_request_path``.

    Expansion is deliberately NOT part of it.  ``os.path.expanduser`` on a ``~user/...`` path is an
    NSS/passwd lookup, which blocks on a networked passwd source and would stall every request
    sharing the web process -- exactly the work the operation queue exists to move off this thread.
    """

    if not isinstance(raw, str) or not raw:
        raise FilesystemError("path is required", message_key="fs.error.pathRequired")
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise FilesystemError("path contains illegal characters", message_key="fs.error.pathIllegal")
    if not raw.startswith("/") and not raw.startswith("~"):
        raise FilesystemError("path must be absolute", message_key="fs.error.pathAbsolute")
    return raw


def parsed_request_path(raw: str) -> Path:
    """Apply the lexical acceptance rule, then expand the user -- worker side only.

    ``expanduser`` may block on a name-service lookup, so every caller of this function must
    already be off the web thread.  Web-thread acceptance calls ``validate_request_path_lexical``.
    """

    return Path(os.path.expanduser(validate_request_path_lexical(raw)))


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


def _absolute_lexical_path(path: Path) -> str:
    expanded = path.expanduser()
    return str(expanded if expanded.is_absolute() else Path.cwd() / expanded)


def _normalized_absolute_text_is_within(path: str, root: str) -> bool:
    if path == root:
        return True
    if root == os.sep:
        return path.startswith(os.sep)
    return path.startswith(root + os.sep)


def _normalized_absolute_path_is_within(path: Path, root: Path) -> bool:
    """Cheap lexical containment after anchoring relative inputs to the current directory."""
    return _normalized_absolute_text_is_within(_absolute_lexical_path(path), _absolute_lexical_path(root))


def _configured_fs_roots() -> tuple[Path, ...]:
    raw = os.environ.get(FS_ROOTS_ENV, "")
    return _configured_fs_roots_for_value(_PATH_POLICY_GENERATION, raw, tuple(str(root) for root in DEFAULT_FS_ROOTS))


@lru_cache(maxsize=32)
def _configured_fs_roots_for_value(generation: int, raw: str, default_roots: tuple[str, ...]) -> tuple[Path, ...]:
    """Canonical configured roots, cached until the configuration value changes."""
    del generation
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
        _PATH_POLICY_GENERATION, str(Path.home()), str(AUTH_CONFIG_PATH), str(AUTH_COOKIE_SECRET_PATH), str(CONFIG_DIR),
    )


@lru_cache(maxsize=32)
def _secret_exact_paths_for_values(generation: int, home_value: str, auth_config: str, auth_cookie_secret: str, config_dir: str) -> tuple[Path, ...]:
    del generation
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
    return _secret_directories_for_home(_PATH_POLICY_GENERATION, str(Path.home()))


@lru_cache(maxsize=32)
def _secret_directories_for_home(generation: int, home_value: str) -> tuple[Path, ...]:
    del generation
    home = Path(home_value)
    return tuple(_normalized_scope_path(path) for path in (
        home / ".ssh",
        home / ".gnupg",
        home / ".config" / "gh",
        home / ".config" / "git",
        home / ".docker",
        home / ".ngc",
    ))


def _suffix_groups(values: tuple[tuple[str, ...], ...]) -> tuple[tuple[int, frozenset[tuple[str, ...]]], ...]:
    sizes = sorted({len(value) for value in values})
    return tuple((size, frozenset(value for value in values if len(value) == size)) for size in sizes)


def _compiled_secret_policy() -> _CompiledSecretPolicy:
    return _compiled_secret_policy_for_values(
        _PATH_POLICY_GENERATION,
        str(Path.home()),
        str(AUTH_CONFIG_PATH),
        str(AUTH_COOKIE_SECRET_PATH),
        str(CONFIG_DIR),
        tuple(sorted(SECRET_DIR_COMPONENTS)),
        tuple(sorted(SECRET_FILE_NAMES)),
        tuple(tuple(value) for value in SECRET_DIR_SUFFIXES),
        tuple(tuple(value) for value in SECRET_FILE_SUFFIXES),
    )


@lru_cache(maxsize=32)
def _compiled_secret_policy_for_values(
    generation: int,
    home_value: str,
    auth_config: str,
    auth_cookie_secret: str,
    config_dir: str,
    secret_dir_components: tuple[str, ...],
    secret_file_names: tuple[str, ...],
    secret_dir_suffixes: tuple[tuple[str, ...], ...],
    secret_file_suffixes: tuple[tuple[str, ...], ...],
) -> _CompiledSecretPolicy:
    exact_paths = _secret_exact_paths_for_values(generation, home_value, auth_config, auth_cookie_secret, config_dir)
    secret_directories = _secret_directories_for_home(generation, home_value)
    return _CompiledSecretPolicy(
        generation=generation,
        exact_paths=frozenset(str(path) for path in exact_paths),
        secret_directories=frozenset(str(path) for path in secret_directories),
        secret_dir_components=frozenset(secret_dir_components),
        secret_file_names=frozenset(secret_file_names),
        secret_dir_suffixes=_suffix_groups(secret_dir_suffixes),
        secret_file_suffixes=_suffix_groups(secret_file_suffixes),
    )


def invalidate_path_policy_caches() -> None:
    """Drop canonical policy roots after a filesystem mutation can replace a symlink."""
    global _PATH_POLICY_GENERATION
    _PATH_POLICY_GENERATION += 1
    _configured_fs_roots_for_value.cache_clear()
    _secret_exact_paths_for_values.cache_clear()
    _secret_directories_for_home.cache_clear()
    _compiled_secret_policy_for_values.cache_clear()


def _path_is_secret_reference(path: Path, *, resolved: Path | None = None, resolve: bool = True) -> bool:
    """Retained pre-optimization classifier used as a differential test oracle."""
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


def _candidate_is_secret(candidate: Path, policy: _CompiledSecretPolicy) -> bool:
    candidate_text = str(candidate)
    if candidate_text in policy.exact_paths:
        return True
    if candidate.is_absolute() and any(
        _normalized_absolute_text_is_within(candidate_text, secret)
        for secret in policy.secret_directories
    ):
        return True
    parts = candidate.parts
    if not policy.secret_dir_components.isdisjoint(parts):
        return True
    if candidate.name in policy.secret_file_names:
        return True
    for size, suffixes in policy.secret_dir_suffixes:
        if size <= len(parts):
            windows = {parts[index:index + size] for index in range(len(parts) - size + 1)}
            if not suffixes.isdisjoint(windows):
                return True
    return any(size <= len(parts) and parts[-size:] in suffixes for size, suffixes in policy.secret_file_suffixes)


def _path_is_secret(path: Path, *, resolved: Path | None = None, resolve: bool = True) -> bool:
    """Return whether ``path`` is secret, reusing a caller's canonical path when available."""
    policy = _compiled_secret_policy()
    lexical = path.expanduser()
    if _candidate_is_secret(lexical, policy):
        return True
    if resolved is None:
        if not resolve:
            return False
        resolved = _normalized_scope_path(path)
    return resolved != lexical and _candidate_is_secret(resolved, policy)


def _ensure_path_allowed(path: Path, *, resolved: Path | None = None) -> None:
    resolved = resolved if resolved is not None else _normalized_scope_path(path)
    roots = _configured_fs_roots()
    resolved_text = str(resolved)
    if not roots or not any(_normalized_absolute_text_is_within(resolved_text, str(root)) for root in roots):
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


class SafePathHandle:
    """One authorized canonical path and the descriptor opened from that value."""

    def __init__(self, requested: Path, resolved: Path, descriptor: int):
        self.requested = requested
        self.resolved = resolved
        self.descriptor = descriptor
        self.stat_result = os.fstat(descriptor)

    def close(self) -> None:
        os.close(self.descriptor)

    def descriptor_path(self) -> Path:
        for root in (Path("/proc/self/fd"), Path("/dev/fd")):
            candidate = root / str(self.descriptor)
            if candidate.exists():
                return candidate
        raise FilesystemError(
            "this platform cannot expose the pinned filesystem descriptor",
            status=500,
            message_key="fs.error.operationFailed",
        )


class SafeParentHandle:
    """Pinned authorized parent used for descriptor-relative namespace mutations."""

    def __init__(self, requested: Path, resolved_target: Path, resolved_parent: Path, descriptor: int):
        self.requested = requested
        self.resolved_target = resolved_target
        self.resolved_parent = resolved_parent
        self.namespace_target = resolved_parent / requested.name
        self.resolved = resolved_parent
        self.descriptor = descriptor
        self.name = requested.name
        self.stat_result = os.fstat(descriptor)

    def close(self) -> None:
        os.close(self.descriptor)


def _open_resolved_path(
    resolved: Path,
    flags: int,
    mode: int = 0o666,
    *,
    create_parents: bool = False,
) -> int:
    """Open an absolute canonical path without following another mutable symlink."""

    if not resolved.is_absolute():
        raise FilesystemError("resolved filesystem path must be absolute", status=500)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    nofollow = nofollow_flag()
    current_fd = os.open("/", directory_flags)
    try:
        parts = resolved.parts[1:]
        if not parts:
            if flags & getattr(os, "O_DIRECTORY", 0):
                return os.dup(current_fd)
            return os.open(".", flags | nofollow, mode, dir_fd=current_fd)
        for component in parts[:-1]:
            try:
                next_fd = os.open(component, directory_flags | nofollow, dir_fd=current_fd)
            except FileNotFoundError:
                if not create_parents:
                    raise
                os.mkdir(component, dir_fd=current_fd)
                next_fd = os.open(component, directory_flags | nofollow, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return os.open(parts[-1], flags | nofollow | getattr(os, "O_CLOEXEC", 0), mode, dir_fd=current_fd)
    finally:
        os.close(current_fd)


@contextmanager
def safe_path(
    raw_path: str,
    *,
    flags: int = os.O_RDONLY,
    mode: int = 0o666,
    resolved_path: Path | None = None,
    create_parents: bool = False,
    operation: str = "",
    observe_name: bool = True,
) -> Iterator[SafePathHandle]:
    """Resolve once, authorize that value, then consume only its no-follow descriptor."""

    requested = parsed_request_path(raw_path)
    if observe_name:
        name_observed(operation, requested)
    resolved = resolved_path if resolved_path is not None else _normalized_scope_path(requested)
    _ensure_path_allowed(requested, resolved=resolved)
    try:
        descriptor = _open_resolved_path(resolved, flags, mode, create_parents=create_parents)
    except FileNotFoundError as error:
        raise FilesystemError.path_not_found(requested) from error
    except IsADirectoryError as error:
        raise FilesystemError.is_directory(requested) from error
    except NotADirectoryError as error:
        raise FilesystemError.not_directory(requested) from error
    try:
        authority_pinned(operation, requested)
        handle = SafePathHandle(requested, resolved, descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    try:
        yield handle
    finally:
        handle.close()


def _validated_path(raw_path: str, **kwargs: Any):
    """Legacy private name retained only as the descriptor-returning primitive."""
    return safe_path(raw_path, **kwargs)


@contextmanager
def safe_child(
    parent_descriptor: int,
    requested: Path,
    resolved: Path,
    *,
    flags: int = os.O_RDONLY,
    operation: str = "",
    observe_name: bool = True,
) -> Iterator[SafePathHandle]:
    """Authorize and open one child relative to an already-pinned directory."""

    if observe_name:
        name_observed(operation, requested)
    _ensure_path_allowed(requested, resolved=resolved)
    try:
        descriptor = os.open(
            requested.name,
            flags | nofollow_flag() | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_descriptor,
        )
    except FileNotFoundError as error:
        raise FilesystemError.path_not_found(requested) from error
    except IsADirectoryError as error:
        raise FilesystemError.is_directory(requested) from error
    except NotADirectoryError as error:
        raise FilesystemError.not_directory(requested) from error
    try:
        authority_pinned(operation, requested)
        handle = SafePathHandle(requested, resolved, descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    try:
        yield handle
    finally:
        handle.close()


@contextmanager
def safe_parent(
    raw_path: str,
    *,
    operation: str = "",
    additional_requested: tuple[Path, ...] = (),
) -> Iterator[SafeParentHandle]:
    """Pin the authorized lexical parent before a create, rename, or delete."""

    requested = parsed_request_path(raw_path)
    observed_paths = (requested, *additional_requested)
    for observed_path in observed_paths:
        name_observed(operation, observed_path)
    resolved_target = _normalized_scope_path(requested)
    resolved_parent = _normalized_scope_path(requested.parent)
    _ensure_path_allowed(requested, resolved=resolved_target)
    _ensure_path_allowed(requested.parent, resolved=resolved_parent)
    try:
        descriptor = _open_resolved_path(
            resolved_parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
    except FileNotFoundError as error:
        raise FilesystemError.path_not_found(requested.parent) from error
    except NotADirectoryError as error:
        raise FilesystemError.not_directory(requested.parent) from error
    try:
        for observed_path in observed_paths:
            authority_pinned(operation, observed_path)
        handle = SafeParentHandle(requested, resolved_target, resolved_parent, descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    try:
        yield handle
    finally:
        handle.close()


def walk_directory(
    descriptor: int,
    *,
    include_directory: Callable[[Path], bool] | None = None,
    operation: str = "",
    requested_root: Path | None = None,
) -> Iterator[tuple[Path, int, tuple[str, ...], tuple[tuple[str, os.stat_result], ...]]]:
    """Walk from a pinned root, opening every child directory before its parent is released."""

    pending: list[tuple[Path, int]] = [(Path("."), os.dup(descriptor))]
    try:
        while pending:
            relative, directory_fd = pending.pop()
            child_directories: list[tuple[str, int]] = []
            files: list[tuple[str, os.stat_result]] = []
            try:
                try:
                    with os.scandir(directory_fd) as entries:
                        for entry in sorted(entries, key=lambda item: item.name.lower()):
                            child_relative = relative / entry.name
                            requested_child = (requested_root or Path(".")) / child_relative
                            name_observed(operation, requested_child)
                            try:
                                entry_stat = entry.stat(follow_symlinks=False)
                            except OSError:
                                continue
                            if stat.S_ISDIR(entry_stat.st_mode):
                                if include_directory is not None and not include_directory(child_relative):
                                    continue
                                try:
                                    child_fd = os.open(
                                        entry.name,
                                        os.O_RDONLY
                                        | getattr(os, "O_DIRECTORY", 0)
                                        | nofollow_flag()
                                        | getattr(os, "O_CLOEXEC", 0),
                                        dir_fd=directory_fd,
                                    )
                                except OSError:
                                    continue
                                try:
                                    authority_pinned(operation, requested_child)
                                    child_stat = os.fstat(child_fd)
                                except BaseException:
                                    os.close(child_fd)
                                    raise
                                if (child_stat.st_dev, child_stat.st_ino) != (entry_stat.st_dev, entry_stat.st_ino):
                                    os.close(child_fd)
                                    continue
                                child_directories.append((entry.name, child_fd))
                            elif stat.S_ISREG(entry_stat.st_mode):
                                try:
                                    child_fd = os.open(
                                        entry.name,
                                        getattr(os, "O_PATH", os.O_RDONLY)
                                        | nofollow_flag()
                                        | getattr(os, "O_CLOEXEC", 0),
                                        dir_fd=directory_fd,
                                    )
                                except OSError:
                                    continue
                                try:
                                    authority_pinned(operation, requested_child)
                                    child_stat = os.fstat(child_fd)
                                    if not stat.S_ISREG(child_stat.st_mode):
                                        continue
                                    if (child_stat.st_dev, child_stat.st_ino) != (
                                        entry_stat.st_dev,
                                        entry_stat.st_ino,
                                    ):
                                        continue
                                    files.append((entry.name, child_stat))
                                finally:
                                    os.close(child_fd)
                    yield (
                        relative,
                        directory_fd,
                        tuple(name for name, _child_fd in child_directories),
                        tuple(files),
                    )
                    pending.extend(
                        (relative / name, child_fd)
                        for name, child_fd in reversed(child_directories)
                    )
                    child_directories = []
                finally:
                    for _name, child_fd in child_directories:
                        os.close(child_fd)
            finally:
                os.close(directory_fd)
    finally:
        for _relative, directory_fd in pending:
            os.close(directory_fd)


def _ensure_not_configured_root(path: Path, action: str, *, resolved: Path | None = None) -> None:
    resolved = resolved if resolved is not None else _normalized_scope_path(path)
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
