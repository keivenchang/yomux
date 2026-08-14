"""Fail-closed filesystem policy for live WAL databases and Unix sockets."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from typing import Iterable


NETWORK_FILESYSTEM_TYPES = frozenset({"nfs", "nfs4", "cifs", "smb", "smbfs", "9p"})
LOCAL_FILESYSTEM_TYPES = frozenset({"apfs", "btrfs", "ext2", "ext3", "ext4", "overlay", "tmpfs", "xfs", "zfs"})
NETWORK_FILESYSTEM_ESCAPE_HATCH = "YOLOMUX_ALLOW_NETWORK_FILESYSTEM_MUTABLE_ROOTS"
_CLASSIFICATION_CACHE: dict[tuple[Path, Path], "FilesystemClassification"] = {}
_CACHE_MOUNTINFO_SIGNATURE: tuple[int, int] | None = None
_CACHE_LOCK = threading.Lock()
LINUX_MOUNTINFO_PATH = Path("/proc/self/mountinfo")


@dataclass(frozen=True)
class FilesystemClassification:
    path: Path
    filesystem_type: str
    is_network: bool
    determined: bool


class FilesystemPreflightError(RuntimeError):
    pass


def _existing_ancestor(path: Path) -> Path:
    candidate = path.expanduser().resolve(strict=False)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def clear_filesystem_classification_cache() -> None:
    """Clear cached mount classifications, primarily for mount-transition tests."""
    global _CACHE_MOUNTINFO_SIGNATURE
    with _CACHE_LOCK:
        _CLASSIFICATION_CACHE.clear()
        _CACHE_MOUNTINFO_SIGNATURE = None


def _classification(target: Path, filesystem_type: str) -> FilesystemClassification:
    normalized = filesystem_type.strip().lower()
    is_network = normalized in NETWORK_FILESYSTEM_TYPES or normalized.startswith("fuse")
    return FilesystemClassification(target, normalized or "unknown", is_network, is_network or normalized in LOCAL_FILESYSTEM_TYPES)


def _classify_darwin_filesystem(
    target: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> FilesystemClassification:
    try:
        completed = runner(("mount",), capture_output=True, text=True, timeout=2, check=False)
    except (OSError, ValueError, subprocess.SubprocessError):
        return FilesystemClassification(target, "unknown", False, False)
    if completed.returncode != 0:
        return FilesystemClassification(target, "unknown", False, False)
    matches: list[tuple[int, str]] = []
    for line in str(completed.stdout or "").splitlines():
        match = re.match(r"^.+ on (.+) \(([^,()]+)(?:,.*)?\)$", line)
        if match is None:
            continue
        mount = Path(match.group(1).replace("\\040", " "))
        try:
            target.relative_to(mount)
        except ValueError:
            continue
        matches.append((len(str(mount)), match.group(2)))
    return _classification(target, max(matches)[1]) if matches else FilesystemClassification(target, "unknown", False, False)


def classify_filesystem(
    path: Path,
    mountinfo_path: Path = LINUX_MOUNTINFO_PATH,
    *,
    platform_name: str = sys.platform,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> FilesystemClassification:
    """Classify a Linux mountinfo path or a macOS mount filesystem type."""
    target = _existing_ancestor(Path(path))
    if platform_name.casefold() == "darwin" and mountinfo_path == LINUX_MOUNTINFO_PATH:
        return _classify_darwin_filesystem(target, runner=runner)
    try:
        stat = mountinfo_path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return FilesystemClassification(target, "unknown", False, False)
    global _CACHE_MOUNTINFO_SIGNATURE
    key = (target, mountinfo_path)
    with _CACHE_LOCK:
        if _CACHE_MOUNTINFO_SIGNATURE != signature:
            _CLASSIFICATION_CACHE.clear()
            _CACHE_MOUNTINFO_SIGNATURE = signature
        cached = _CLASSIFICATION_CACHE.get(key)
        if cached is not None:
            return cached
    try:
        lines = mountinfo_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return FilesystemClassification(target, "unknown", False, False)
    matches: list[tuple[int, str]] = []
    for line in lines:
        left, marker, right = line.partition(" - ")
        fields = left.split()
        right_fields = right.split()
        if not marker or len(fields) < 5 or not right_fields:
            continue
        mount = Path(fields[4].replace("\\040", " "))
        try:
            target.relative_to(mount)
        except ValueError:
            continue
        matches.append((len(str(mount)), right_fields[0].lower()))
    if not matches:
        result = FilesystemClassification(target, "unknown", False, False)
    else:
        result = _classification(target, max(matches)[1])
    with _CACHE_LOCK:
        _CLASSIFICATION_CACHE[key] = result
    return result


def preflight_mutable_roots(
    *,
    wal_databases: Iterable[Path] = (),
    unix_sockets: Iterable[Path] = (),
    classifier: Callable[[Path], FilesystemClassification] | None = None,
    environ: dict[str, str] | None = None,
) -> list[FilesystemClassification]:
    """Refuse unsafe mutable roots before opening WAL or binding a socket."""
    active_classifier = classify_filesystem if classifier is None else classifier
    roots = [("WAL SQLite database", Path(path)) for path in wal_databases]
    roots.extend(("Unix socket", Path(path)) for path in unix_sockets)
    classifications: list[FilesystemClassification] = []
    unsafe: list[str] = []
    for kind, path in roots:
        result = active_classifier(path)
        classifications.append(result)
        if result.is_network or not result.determined:
            filesystem = result.filesystem_type if result.determined else "unknown"
            unsafe.append(f"{kind} {path} is on {filesystem}")
    if not unsafe:
        return classifications
    message = "; ".join(unsafe) + f"; set {NETWORK_FILESYSTEM_ESCAPE_HATCH}=1 only for a proven supported local setup, or choose a host-local runtime/data root"
    values = os.environ if environ is None else environ
    if values.get(NETWORK_FILESYSTEM_ESCAPE_HATCH) == "1":
        warnings.warn(message, RuntimeWarning, stacklevel=2)
        return classifications
    raise FilesystemPreflightError(message)
