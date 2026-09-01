"""Path validation and path-scoped metadata for filesystem APIs."""

from __future__ import annotations

import contextvars
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import stat
import sys
from contextlib import ExitStack
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
# `F_GETPATH` (darwin) answers "what pathname does this descriptor currently have", which is a
# RE-RESOLUTION, not a pin: the consumer that receives it opens the name again.  It is retained only
# for descriptor introspection in diagnostics/tests and must never become an authorization path.
DARWIN_F_GETPATH = 50
DARWIN_PATH_BUFFER_BYTES = 1024
# The only pathnames `descriptor_path()` may hand a consumer.  Both are magic per-descriptor entries
# (`/proc/self/fd/N` on Linux, devfs `/dev/fd/N` elsewhere): reopening one reaches THIS descriptor's
# object, so a rename or namespace replacement between authorization and consumption cannot redirect
# it.  Anything else -- including an `F_GETPATH` pathname -- is name-bound and is refused.
DESCRIPTOR_PATH_ROOTS: tuple[Path, ...] = (Path("/proc/self/fd"), Path("/dev/fd"))
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


def metadata_descriptor_flags() -> int:
    """Open one leaf for identity without following it on each supported platform."""

    path_flag = getattr(os, "O_PATH", 0)
    if path_flag:
        return path_flag
    return os.O_RDONLY | getattr(os, "O_SYMLINK", 0)


def descriptor_open_flags(flags: int) -> int:
    """Add the platform's descriptor-safe leaf flags without defeating ``O_SYMLINK``."""

    symlink_flag = getattr(os, "O_SYMLINK", 0)
    nofollow = 0 if symlink_flag and flags & symlink_flag else nofollow_flag()
    return flags | nofollow | getattr(os, "O_CLOEXEC", 0)


def _renameat_with_flags(parent_descriptor: int, source: str, target: str, flags: int) -> None:
    """Apply a platform-native atomic rename flag within one pinned directory."""

    source_bytes = os.fsencode(source)
    target_bytes = os.fsencode(target)
    try:
        library = ctypes.CDLL(None, use_errno=True)
        if sys.platform.startswith("linux"):
            rename_call = library.renameat2
        elif sys.platform == "darwin":
            rename_call = library.renameatx_np
        else:
            raise AttributeError
    except (AttributeError, OSError) as error:
        raise OSError(errno.ENOSYS, "atomic rename flags are unavailable") from error
    rename_call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename_call.restype = ctypes.c_int
    if rename_call(parent_descriptor, source_bytes, parent_descriptor, target_bytes, flags) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), target)


def rename_noreplace(parent_descriptor: int, source: str, target: str) -> None:
    """Rename one child without replacing a concurrently created destination."""

    flag = 1 if sys.platform.startswith("linux") else 4
    _renameat_with_flags(parent_descriptor, source, target, flag)


def rename_exchange(parent_descriptor: int, first: str, second: str) -> None:
    """Atomically exchange two children so the displaced generation can be verified."""

    _renameat_with_flags(parent_descriptor, first, second, 2)


def validate_request_path_lexical(raw: str) -> str:
    """Return the one lexical acceptance rule every filesystem request must pass.

    These three refusals read only the request string: no descriptor, no authorization, no
    filesystem access and no name service.  That is what makes them safe on the web thread, which
    applies them before it accepts an operation, so a request the worker would refuse can never be
    accepted as a 202 receipt.  This is the only implementation of the rule; the batchd worker
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


# One filesystem access policy, captured by the server that ACCEPTS a request and enforced by
# whatever process finally executes it.
#
# `batchd` and `watchd` are shared per-user daemons: the first server to need one launches it, and
# every other server on every other port then reuses that same process.  A filesystem job
# descriptor used to carry only `op`, `path` and `args`, so the worker authorized the path against
# `YOLOMUX_FS_ROOTS` in its OWN environment -- the environment of whichever server launched it
# first.  A server configured with narrow roots therefore borrowed a broader launcher's authority:
# its own direct read answered `403 fs.error.outsideRoots`, and the identical descriptor executed
# in the shared daemon returned the file's contents.  That is a confused deputy, and the reverse
# (a broad server denied by a narrow launcher) is the same defect with the sign flipped.
#
# The fix is to make the policy part of the descriptor: capture it at HTTP accept time, carry it
# with the job (so it is also part of every product/coalescing identity), and bind it at execution.
# A descriptor whose policy is absent, malformed, or from a different policy version is REFUSED --
# it must never fall back to the executing process's environment, because that fallback is the
# vulnerability itself.
#
# Bump FS_ACCESS_POLICY_VERSION whenever the meaning of a serialized policy changes, so a daemon
# running older or newer code refuses a descriptor it cannot interpret rather than guessing.
FS_ACCESS_POLICY_VERSION = 1
FS_ACCESS_POLICY_FIELD = "access_policy"


@dataclass(frozen=True)
class FilesystemAccessPolicy:
    """One accepting server's immutable canonical filesystem roots plus its policy version."""

    version: int
    roots: tuple[str, ...]

    @property
    def root_paths(self) -> tuple[Path, ...]:
        """The captured roots as paths; they are already canonical, so never re-resolve them."""
        return tuple(Path(root) for root in self.roots)

    def digest(self) -> str:
        return _access_policy_digest(int(self.version), self.roots)

    def descriptor(self) -> dict[str, Any]:
        """The serialized form carried on a job descriptor and hashed into its product identity."""
        return {"version": int(self.version), "roots": list(self.roots), "digest": self.digest()}


_ACTIVE_ACCESS_POLICY: contextvars.ContextVar[FilesystemAccessPolicy | None] = contextvars.ContextVar(
    "filesystem_access_policy",
    default=None,
)


def access_policy_refused(reason: str) -> FilesystemError:
    """Return the one typed refusal for a descriptor that carries no usable access policy."""
    return FilesystemError(
        f"filesystem access policy is unusable: {reason}",
        status=403,
        message_key="fs.error.operationFailed",
        diagnostic=f"filesystem access policy refused: {reason}",
    )


@lru_cache(maxsize=64)
def _access_policy_digest(version: int, roots: tuple[str, ...]) -> str:
    return hashlib.sha256(json.dumps(
        {"version": version, "roots": list(roots)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def capture_access_policy() -> FilesystemAccessPolicy:
    """Capture this process's configured roots as one immutable value, at HTTP accept time.

    Canonicalizing roots costs real syscalls, and this now runs on the request thread, so the
    captured value is cached on the same generation/configuration key the roots themselves use.
    """
    return _captured_access_policy(_PATH_POLICY_GENERATION, os.environ.get(FS_ROOTS_ENV, ""))


@lru_cache(maxsize=32)
def _captured_access_policy(generation: int, raw: str) -> FilesystemAccessPolicy:
    del raw  # part of the cache key; the roots below read the same configuration value
    del generation
    return FilesystemAccessPolicy(
        version=FS_ACCESS_POLICY_VERSION,
        roots=tuple(str(root) for root in _configured_fs_roots()),
    )


def access_policy_descriptor() -> dict[str, Any]:
    """The accepting server's policy, ready to serialize onto a job descriptor."""
    return capture_access_policy().descriptor()


def access_policy_from_descriptor(value: Any) -> FilesystemAccessPolicy:
    """Parse a descriptor's carried policy, or refuse; never fall back to this process's roots."""
    if value is None:
        raise access_policy_refused("policy_missing")
    if not isinstance(value, dict):
        raise access_policy_refused("policy_malformed")
    version = value.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise access_policy_refused("policy_version_invalid")
    if version != FS_ACCESS_POLICY_VERSION:
        raise access_policy_refused(f"policy_version_mismatch:{version}")
    raw_roots = value.get("roots")
    if not isinstance(raw_roots, list) or any(
        not isinstance(root, str) or not root.startswith(os.sep) for root in raw_roots
    ):
        raise access_policy_refused("policy_roots_invalid")
    policy = FilesystemAccessPolicy(version=version, roots=tuple(raw_roots))
    if value.get("digest") != policy.digest():
        raise access_policy_refused("policy_digest_mismatch")
    return policy


@contextmanager
def enforce_access_policy(policy: FilesystemAccessPolicy) -> Iterator[None]:
    """Bind one accepting server's policy for the duration of an execution, without mutating env."""
    if not isinstance(policy, FilesystemAccessPolicy):
        raise access_policy_refused("policy_unbound")
    token = _ACTIVE_ACCESS_POLICY.set(policy)
    try:
        yield
    finally:
        _ACTIVE_ACCESS_POLICY.reset(token)


def active_access_policy() -> FilesystemAccessPolicy:
    """The bound accepting-server policy, or this process's own when nothing is bound.

    In-process callers (the web process answering its own request, watchd building its own product)
    have no descriptor and no other server's authority to borrow, so their policy is their own
    environment.  Descriptor-carried execution never reaches this fallback: the batchd task entry
    points refuse a descriptor without a parsable policy before any path is touched.
    """
    bound = _ACTIVE_ACCESS_POLICY.get()
    return bound if bound is not None else capture_access_policy()


def authorized_fs_roots() -> tuple[Path, ...]:
    """The one root set every authorization decision reads."""
    return active_access_policy().root_paths


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
    _captured_access_policy.cache_clear()
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
    roots = authorized_fs_roots()
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


def _authorize_requested_path(
    requested: Path,
    resolved: Path,
    *,
    operation: str,
    observe_name: bool = True,
) -> None:
    """Apply the one path policy before a caller pins and consumes this generation."""

    if observe_name:
        name_observed(operation, requested)
    _ensure_path_allowed(requested, resolved=resolved)


def descriptor_path(descriptor: int) -> Path:
    """Return the shared generation-bound pathname for one live descriptor."""

    for root in DESCRIPTOR_PATH_ROOTS:
        candidate = root / str(descriptor)
        if candidate.exists():
            return candidate
    raise FilesystemError(
        "this platform cannot expose the pinned filesystem descriptor",
        status=500,
        message_key="fs.error.operationFailed",
        diagnostic=(
            "descriptor-bound authorization refused: no per-descriptor path root "
            f"({', '.join(str(root) for root in DESCRIPTOR_PATH_ROOTS)}) is available on this "
            "platform, so the authorized generation cannot be named for the consumer"
        ),
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
        """Return a pathname that names THIS descriptor generation, or fail closed.

        Every consumer of this value (`git -C`, the count/zip walkers, the multi-repo scan) opens
        the returned name AGAIN, so a name that the kernel re-resolves would reintroduce exactly the
        check/use race the descriptor pin exists to close.  Only the magic per-descriptor roots
        qualify.  Darwin previously preferred the `F_GETPATH` pathname here, which is a genuine
        re-resolution -- that branch is gone; darwin now takes `/dev/fd/N` like every other
        non-Linux platform, and refuses when even that is unavailable.
        """
        return descriptor_path(self.descriptor)

    def base_capability(self) -> dict[str, Any]:
        """Return non-Git facts for this already-authorized descriptor generation."""

        metadata = self.stat_result
        kind = "dir" if stat.S_ISDIR(metadata.st_mode) else "file"
        return {
            "path": str(self.requested),
            "name": self.requested.name,
            "kind": kind,
            "size": int(metadata.st_size) if kind == "file" else None,
            "mtime": int(metadata.st_mtime) if kind == "file" else None,
            "mtime_ns": int(metadata.st_mtime_ns) if kind == "file" else None,
            **_physical_file_identity(self.requested, resolved=self.resolved, stat_result=metadata),
        }


class SafeParentHandle:
    """Pinned authorized parent used for descriptor-relative namespace mutations."""

    def __init__(
        self,
        requested: Path,
        resolved_target: Path,
        resolved_parent: Path,
        descriptor: int,
        target_descriptor: int | None,
    ):
        self.requested = requested
        self.resolved_target = resolved_target
        self.resolved_parent = resolved_parent
        self.namespace_target = resolved_parent / requested.name
        self.resolved = resolved_parent
        self.descriptor = descriptor
        self.name = requested.name
        self.stat_result = os.fstat(descriptor)
        self.target_descriptor = target_descriptor
        self.target_identity = (
            _stat_identity(os.fstat(target_descriptor))
            if target_descriptor is not None
            else None
        )

    def require_target_identity(self, target: SafePathHandle) -> None:
        """Require a consumed child to be the namespace generation observed before authorization."""

        _require_descriptor_identity(target.descriptor, self.target_identity, self.requested)

    def close(self) -> None:
        try:
            os.close(self.descriptor)
        finally:
            if self.target_descriptor is not None:
                os.close(self.target_descriptor)


FileIdentity = tuple[int, int]


def _stat_identity(metadata: os.stat_result) -> FileIdentity:
    return int(metadata.st_dev), int(metadata.st_ino)


def _observed_resolved_chain(resolved: Path) -> dict[Path, FileIdentity]:
    """Snapshot every existing canonical component before policy authorization runs."""

    observed: dict[Path, FileIdentity] = {}
    current = Path(resolved.anchor)
    observed[current] = _stat_identity(os.stat(current, follow_symlinks=False))
    for component in resolved.parts[1:]:
        current /= component
        try:
            observed[current] = _stat_identity(os.stat(current, follow_symlinks=False))
        except FileNotFoundError:
            break
    return observed


def _require_descriptor_identity(
    descriptor: int,
    expected: FileIdentity | None,
    requested: Path,
) -> None:
    if expected is None or _stat_identity(os.fstat(descriptor)) != expected:
        raise FilesystemError.changed_on_disk(
            requested,
            diagnostic="authorized filesystem generation changed before descriptor pin",
        )


def _open_resolved_path(
    resolved: Path,
    flags: int,
    mode: int = 0o666,
    *,
    create_parents: bool = False,
    observed_chain: dict[Path, FileIdentity] | None = None,
    requested: Path | None = None,
) -> int:
    """Open an absolute canonical path without following another mutable symlink."""

    if not resolved.is_absolute():
        raise FilesystemError("resolved filesystem path must be absolute", status=500)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    nofollow = nofollow_flag()
    current_fd = os.open("/", directory_flags)
    current_path = Path(resolved.anchor)
    requested_path = requested or resolved
    try:
        if observed_chain is not None:
            _require_descriptor_identity(current_fd, observed_chain.get(current_path), requested_path)
        parts = resolved.parts[1:]
        if not parts:
            if flags & getattr(os, "O_DIRECTORY", 0):
                return os.dup(current_fd)
            return os.open(".", descriptor_open_flags(flags), mode, dir_fd=current_fd)
        for component in parts[:-1]:
            next_path = current_path / component
            if observed_chain is not None and next_path not in observed_chain:
                if not create_parents:
                    raise FileNotFoundError(component)
                try:
                    os.mkdir(component, dir_fd=current_fd)
                except FileExistsError as error:
                    raise FilesystemError.changed_on_disk(requested_path) from error
                next_fd = os.open(component, directory_flags | nofollow, dir_fd=current_fd)
            else:
                next_fd = os.open(component, directory_flags | nofollow, dir_fd=current_fd)
                if observed_chain is not None:
                    try:
                        _require_descriptor_identity(next_fd, observed_chain.get(next_path), requested_path)
                    except BaseException:
                        os.close(next_fd)
                        raise
            os.close(current_fd)
            current_fd = next_fd
            current_path = next_path
        target_flags = descriptor_open_flags(flags)
        expected_target = observed_chain.get(resolved) if observed_chain is not None else None
        if observed_chain is not None and expected_target is None and flags & os.O_CREAT:
            target_flags |= os.O_EXCL
        descriptor = os.open(parts[-1], target_flags, mode, dir_fd=current_fd)
        try:
            if observed_chain is not None and expected_target is not None:
                _require_descriptor_identity(descriptor, expected_target, requested_path)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor
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
    resolved = resolved_path if resolved_path is not None else _normalized_scope_path(requested)
    observed_chain = _observed_resolved_chain(resolved)
    _authorize_requested_path(
        requested,
        resolved,
        operation=operation,
        observe_name=observe_name,
    )
    try:
        descriptor = _open_resolved_path(
            resolved,
            flags,
            mode,
            create_parents=create_parents,
            observed_chain=observed_chain,
            requested=requested,
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

    try:
        observed_identity = _stat_identity(os.stat(requested.name, dir_fd=parent_descriptor, follow_symlinks=False))
    except FileNotFoundError:
        observed_identity = None
    _authorize_requested_path(
        requested,
        resolved,
        operation=operation,
        observe_name=observe_name,
    )
    try:
        descriptor = os.open(requested.name, descriptor_open_flags(flags), dir_fd=parent_descriptor)
    except FileNotFoundError as error:
        raise FilesystemError.path_not_found(requested) from error
    except IsADirectoryError as error:
        raise FilesystemError.is_directory(requested) from error
    except NotADirectoryError as error:
        raise FilesystemError.not_directory(requested) from error
    try:
        authority_pinned(operation, requested)
        _require_descriptor_identity(descriptor, observed_identity, requested)
        handle = SafePathHandle(requested, resolved, descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    try:
        yield handle
    finally:
        handle.close()


@contextmanager
def safe_descendant(
    root_descriptor: int,
    requested_root: Path,
    resolved_root: Path,
    relative: Path,
    *,
    flags: int = os.O_RDONLY,
    operation: str = "",
    observe_name: bool = True,
) -> Iterator[SafePathHandle]:
    """Authorize and pin a nested descendant relative to one already-authorized root descriptor."""

    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise FilesystemError.changed_on_disk(
            requested_root / relative,
            diagnostic="descriptor descendant escaped its authorized root",
        )
    parts = [part for part in relative.parts if part not in {"", "."}]
    if not parts:
        descriptor = os.dup(root_descriptor)
        handle = SafePathHandle(requested_root, resolved_root, descriptor)
        try:
            yield handle
        finally:
            handle.close()
        return

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    current_descriptor = root_descriptor
    requested = requested_root
    resolved = resolved_root
    with ExitStack() as stack:
        handle: SafePathHandle | None = None
        for index, component in enumerate(parts):
            requested /= component
            resolved /= component
            handle = stack.enter_context(
                safe_child(
                    current_descriptor,
                    requested,
                    resolved,
                    flags=flags if index == len(parts) - 1 else directory_flags,
                    operation=operation,
                    observe_name=observe_name,
                )
            )
            current_descriptor = handle.descriptor
        if handle is None:
            raise FilesystemError.changed_on_disk(requested_root)
        if stat.S_ISLNK(handle.stat_result.st_mode):
            try:
                target_text = os.readlink("", dir_fd=handle.descriptor)
            except OSError as error:
                raise FilesystemError.changed_on_disk(requested, diagnostic=error) from error
            target = Path(target_text)
            if not target.is_absolute():
                target = requested.parent / target
            target_resolved = _normalized_scope_path(target)
            _authorize_requested_path(
                requested,
                target_resolved,
                operation=operation,
                observe_name=False,
            )
        yield handle


@contextmanager
def safe_parent(
    raw_path: str,
    *,
    operation: str = "",
    additional_requested: tuple[Path, ...] = (),
) -> Iterator[SafeParentHandle]:
    """Pin the authorized lexical parent before a create, rename, or delete."""

    requested = parsed_request_path(raw_path)
    target_descriptor: int | None = None
    try:
        target_descriptor = os.open(requested, descriptor_open_flags(metadata_descriptor_flags()))
    except FileNotFoundError:
        pass
    handle: SafeParentHandle | None = None
    try:
        resolved_target = _normalized_scope_path(requested)
        resolved_parent = _normalized_scope_path(requested.parent)
        observed_chain = _observed_resolved_chain(resolved_parent)
        _authorize_requested_path(requested, resolved_target, operation=operation)
        _ensure_path_allowed(requested.parent, resolved=resolved_parent)
        for additional_path in additional_requested:
            if additional_path.parent != requested.parent:
                raise ValueError("namespace mutation target must share the authorized parent")
            _authorize_requested_path(
                additional_path,
                resolved_parent / additional_path.name,
                operation=operation,
            )
        try:
            descriptor = _open_resolved_path(
                resolved_parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                observed_chain=observed_chain,
                requested=requested,
            )
        except FileNotFoundError as error:
            raise FilesystemError.path_not_found(requested.parent) from error
        except NotADirectoryError as error:
            raise FilesystemError.not_directory(requested.parent) from error
        try:
            authority_pinned(operation, requested)
            for additional_path in additional_requested:
                authority_pinned(operation, additional_path)
            handle = SafeParentHandle(
                requested,
                resolved_target,
                resolved_parent,
                descriptor,
                target_descriptor,
            )
            target_descriptor = None
        except BaseException:
            os.close(descriptor)
            raise
        yield handle
    finally:
        if handle is not None:
            handle.close()
        elif target_descriptor is not None:
            os.close(target_descriptor)


def walk_directory(
    descriptor: int,
    *,
    include_directory: Callable[[Path], bool] | None = None,
    operation: str = "",
    requested_root: Path,
    resolved_root: Path,
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
                            requested_child = requested_root / child_relative
                            resolved_child = resolved_root / child_relative
                            try:
                                _authorize_requested_path(
                                    requested_child,
                                    resolved_child,
                                    operation=operation,
                                )
                                entry_stat = entry.stat(follow_symlinks=False)
                            except (FilesystemError, OSError):
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
                                        descriptor_open_flags(metadata_descriptor_flags()),
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
    for root in authorized_fs_roots():
        if resolved == root:
            raise FilesystemError(
                f"refusing to {action} configured filesystem root: {path}",
                status=403,
                message_key="fs.error.configuredRootMutation",
                message_params={"action": action, "path": str(path)},
            )


def _darwin_devfs_live_realpath(resolved: Path) -> Path:
    """The live, fd-authoritative real path for a `/dev/fd/N`-rooted path, Darwin only.

    Listing walks a pinned root via its `/dev/fd/N`/`/proc/self/fd/N` descriptor path (see
    `SafePathHandle.descriptor_path`), and on Linux `.resolve()` on that root correctly follows
    the `/proc/self/fd/N` symlink back to the true directory -- procfs fd entries answer "what is
    the CURRENT path of this exact live descriptor", so this is fd-authoritative, not a by-name
    re-lookup, and both the "realpath"/repo "root" display fields and `_path_is_secret`'s
    directory-component matching rely on it reflecting the truth. Darwin's `/dev/fd/N` is a devfs
    character-special node, not a symlink, so the same `.resolve()` leaves the raw
    `/dev/fd/N/...` path untouched, leaking it verbatim into display fields, AND silently
    defeating secret-directory detection for a child whose parent was swapped after this exact
    descriptor's authorization (the case `test_listing_opens_regular_children_from_the_pinned_parent_generation`
    exercises). `F_GETPATH` is the Darwin equivalent of that same "ask the kernel this fd's
    current path" query -- it is NOT the name-based re-resolution `DESCRIPTOR_PATH_ROOTS`'s
    docstring warns against (that danger is a caller re-`open()`ing a returned pathname, which
    reopens the check/use race; nothing here re-opens anything with this result, it is only ever
    compared as a string).
    """
    if sys.platform != "darwin":
        return resolved
    parts = resolved.parts
    if len(parts) < 4 or parts[:3] != ("/", "dev", "fd"):
        return resolved
    try:
        descriptor = int(parts[3])
    except ValueError:
        return resolved
    try:
        raw = fcntl.fcntl(descriptor, DARWIN_F_GETPATH, b"\0" * DARWIN_PATH_BUFFER_BYTES)
    except OSError:
        return resolved
    root_text = raw.split(b"\0", 1)[0].decode("utf-8", "surrogateescape")
    if not root_text:
        return resolved
    return Path(root_text).joinpath(*parts[4:])


def _physical_file_identity(
    path: Path,
    *,
    resolved: Path,
    stat_result: os.stat_result,
) -> dict[str, Any]:
    """Return safe file identity without repeating validation/metadata work from listings."""
    try:
        _ensure_path_allowed(path, resolved=resolved)
        st = stat_result
    except (FilesystemError, OSError):
        return {}
    file_id = f"{st.st_dev}:{st.st_ino}"
    return {
        "realpath": str(_darwin_devfs_live_realpath(resolved)),
        "file_id": file_id,
        "file_identity": f"id:{file_id}",
    }
