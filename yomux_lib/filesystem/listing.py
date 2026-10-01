"""Directory listing helpers for the File Explorer panel."""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path
from typing import Any

from . import paths
from .git_ops import pinned_git_repo_info

MAX_DIRECTORY_ENTRIES = 1_000
# Finder must render its directory controls even when one repository has a wedged Git filesystem.
# A normal repo takes a few milliseconds; this aggregate cap means a slow row loses its optional
# branch badge instead of holding every repo's expansion behind serial subprocess timeouts.
FINDER_REPO_INFO_BUDGET_SECONDS = 0.5
REPO_MARKERS = (".git", ".hg", ".svn", ".jj")


def _timing_started(performance_details: dict[str, float] | None) -> float:
    return time.perf_counter() if performance_details is not None else 0.0


def _record_elapsed(
    performance_details: dict[str, float] | None,
    key: str,
    started: float,
) -> None:
    if performance_details is None:
        return
    performance_details[key] = performance_details.get(key, 0.0) + max(
        0.0,
        (time.perf_counter() - started) * 1000,
    )


class _ResolvedDirectoryName(str):
    """Directory entry name carrying the canonical path from the security scan."""

    def __new__(
        cls,
        value: str,
        resolved: Path,
        *,
        symlink_target_stat: os.stat_result | None = None,
        symlink_target_text: str | None = None,
        info: dict[str, Any] | None = None,
    ):
        instance = super().__new__(cls, value)
        instance.resolved = resolved
        instance.symlink_target_stat = symlink_target_stat
        instance.symlink_target_text = symlink_target_text
        instance.info = info
        return instance


def _directory_is_repo(directory_descriptor: int) -> bool:
    for marker in REPO_MARKERS:
        try:
            marker_stat = os.stat(marker, dir_fd=directory_descriptor, follow_symlinks=False)
            if marker == ".git":
                if stat.S_ISREG(marker_stat.st_mode):
                    return True
                if stat.S_ISDIR(marker_stat.st_mode):
                    os.stat(".git/HEAD", dir_fd=directory_descriptor, follow_symlinks=False)
                    return True
                continue
            required_children = {
                ".hg": ("requires", "store"),
                ".svn": ("wc.db", "entries"),
                ".jj": ("repo", "working_copy"),
            }.get(marker, ())
            if stat.S_ISDIR(marker_stat.st_mode):
                for required_child in required_children:
                    try:
                        os.stat(f"{marker}/{required_child}", dir_fd=directory_descriptor, follow_symlinks=False)
                        return True
                    except OSError:
                        continue
        except OSError:
            continue
    return False


def _entry_info(
    path: Path,
    name: str,
    *,
    entry_stat: os.stat_result,
    resolved: Path,
    repo_info_cache: dict[Path, dict[str, Any]] | None = None,
    repo_info_deadline: float | None = None,
    performance_details: dict[str, float] | None = None,
    symlink_target_stat: os.stat_result | None = None,
    symlink_target_text: str | None = None,
    inspection_descriptor: int = -1,
    inspection_handle: paths.SafePathHandle | None = None,
    include_repo_info: bool = True,
) -> dict[str, Any]:
    started = _timing_started(performance_details)
    st = entry_stat
    _record_elapsed(performance_details, "entry_lstat_ms", started)
    mode = st.st_mode
    if stat.S_ISLNK(mode):
        started = _timing_started(performance_details)
        target_st = symlink_target_stat
        try:
            if target_st is None:
                raise FileNotFoundError(path)
            target_mode = target_st.st_mode
            kind = "dir" if stat.S_ISDIR(target_mode) else "file"
            size = target_st.st_size
        except OSError:
            kind = "symlink-broken"
            size = 0
        _record_elapsed(performance_details, "symlink_stat_ms", started)
    elif stat.S_ISDIR(mode):
        kind = "dir"
        size = 0
    elif stat.S_ISREG(mode):
        kind = "file"
        size = st.st_size
    else:
        kind = "other"
        size = st.st_size
    info = {
        "name": name,
        "kind": kind,
        "size": int(size),
        "mtime": int(st.st_mtime),
        "mtime_ns": int(st.st_mtime_ns),
        "is_symlink": stat.S_ISLNK(mode),
    }
    if stat.S_ISLNK(mode):
        # Surface where the link points so the Finder row can show "name -> target".
        if symlink_target_text is None:
            raise paths.FilesystemError(
                "symlink metadata requires descriptor-bound link text",
                status=500,
                message_key="fs.error.operationFailed",
            )
        info["symlink_target"] = symlink_target_text
    if kind == "dir":
        started = _timing_started(performance_details)
        if inspection_descriptor < 0:
            raise paths.FilesystemError(
                "repository inspection requires a pinned directory descriptor",
                status=500,
                message_key="fs.error.operationFailed",
            )
        info["is_repo"] = _directory_is_repo(inspection_descriptor)
        _record_elapsed(performance_details, "repo_probe_ms", started)
        if not include_repo_info and info["is_repo"] is True:
            info["repo_info_deferred"] = True
        if include_repo_info and info.get("is_repo") is True:
            repo_key = resolved
            remaining = None if repo_info_deadline is None else repo_info_deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                # `is_repo` is still enough to draw a usable, expandable Finder row.  Do not invent
                # a status-shaped fallback: an empty optional badge is honest, while a spinner is not.
                info["repo_info_deferred"] = True
            elif repo_info_cache is None:
                started = _timing_started(performance_details)
                if inspection_handle is None:
                    raise paths.FilesystemError(
                        "repository inspection requires a pinned directory handle",
                        status=500,
                        message_key="fs.error.operationFailed",
                    )
                display_root = resolved
                try:
                    repo_payload = pinned_git_repo_info(
                        inspection_handle,
                        display_root=display_root,
                        include_status=False,
                        timeout=remaining,
                    )
                except paths.FilesystemError as error:
                    if error.message_key != "fs.error.notGitRepo" and error.status not in {413, 504}:
                        raise
                    info["repo_info_deferred"] = True
                else:
                    repo_payload["root"] = str(display_root)
                    repo_payload["name"] = display_root.name
                    info["repo"] = repo_payload
                _record_elapsed(performance_details, "repo_info_ms", started)
            else:
                repo = repo_info_cache.get(repo_key)
                if repo is None:
                    started = _timing_started(performance_details)
                    if inspection_handle is None:
                        raise paths.FilesystemError(
                            "repository inspection requires a pinned directory handle",
                            status=500,
                            message_key="fs.error.operationFailed",
                        )
                    display_root = resolved
                    try:
                        repo = pinned_git_repo_info(
                            inspection_handle,
                            display_root=display_root,
                            include_status=False,
                            timeout=remaining,
                        )
                    except paths.FilesystemError as error:
                        if error.message_key != "fs.error.notGitRepo" and error.status not in {413, 504}:
                            raise
                        info["repo_info_deferred"] = True
                    if repo is not None:
                        repo["root"] = str(display_root)
                        repo["name"] = display_root.name
                    _record_elapsed(performance_details, "repo_info_ms", started)
                    if repo is not None:
                        repo_info_cache[repo_key] = repo
                if repo is not None:
                    info["repo"] = repo
    # The directory scan already canonicalized the entry for its secret-path check.  Its
    # target stat is the same stat needed for a symlink's physical identity.
    identity_stat = target_st if stat.S_ISLNK(mode) and kind != "symlink-broken" else st
    started = _timing_started(performance_details)
    info.update(paths._physical_file_identity(path, resolved=resolved, stat_result=identity_stat))
    _record_elapsed(performance_details, "identity_ms", started)
    return info


def _resolved_symlink_target(
    path: Path,
    *,
    target_text: str,
) -> Path:
    """Resolve a symlink target without carrying raced state into later rows."""

    try:
        target = Path(target_text)
    except OSError:
        return paths._normalized_scope_path(path)
    if not target.is_absolute():
        target = path.parent / target
    if target.name in {"", ".", ".."}:
        return paths._normalized_scope_path(path)
    target_parent = target.parent
    resolved_parent = paths._normalized_scope_path(target_parent)
    resolved = resolved_parent / target.name
    if resolved.is_symlink():
        return paths._normalized_scope_path(path)
    return resolved


def _watch_signature_from_entries(
    requested_path: Path,
    file_stat: os.stat_result,
    entries: list[dict[str, Any]],
    child_limit: int,
) -> tuple[Any, ...]:
    if not stat.S_ISDIR(file_stat.st_mode) or child_limit <= 0:
        kind = "dir" if stat.S_ISDIR(file_stat.st_mode) else "file"
        return (str(requested_path), kind, int(file_stat.st_mtime_ns), int(file_stat.st_size))
    children = tuple(
        (
            str(info.get("name") or ""),
            str(info.get("kind") or "other"),
            int(info.get("mtime_ns") or 0),
            int(info.get("size") or 0),
        )
        for info in entries[:max(1, int(child_limit))]
    )
    return (
        str(requested_path),
        "dir",
        int(file_stat.st_mtime_ns),
        int(file_stat.st_size),
        children,
    )


def _visible_directory_names(
    root_descriptor: int,
    *,
    resolved_parent: Path,
    requested_path: Path,
    child_limit: int | None = None,
    performance_details: dict[str, float] | None = None,
    include_repo_info: bool = True,
    operation: str = "list_directory",
) -> tuple[list[str], bool]:
    limit = max(1, min(int(MAX_DIRECTORY_ENTRIES), int(child_limit))) if child_limit is not None else max(1, int(MAX_DIRECTORY_ENTRIES))
    names: list[str] = []
    truncated = False
    repo_info_cache: dict[Path, dict[str, Any]] = {}
    repo_info_deadline = time.monotonic() + FINDER_REPO_INFO_BUDGET_SECONDS
    started = _timing_started(performance_details)
    directory_descriptor = None
    try:
        requested_parent = requested_path
        directory_descriptor = os.open(
            ".",
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=root_descriptor,
        )
    finally:
        _record_elapsed(performance_details, "scan_resolve_ms", started)
    started = _timing_started(performance_details)
    try:
        entries = os.scandir(directory_descriptor)
    finally:
        _record_elapsed(performance_details, "scan_open_ms", started)
    try:
        with entries:
            while True:
                started = _timing_started(performance_details)
                try:
                    entry = next(entries)
                except StopIteration:
                    break
                finally:
                    _record_elapsed(performance_details, "scan_iterate_ms", started)
                name = entry.name
                # Once the caller has enough visible children, no later entry can affect its
                # result. Stop before resolving, credential filtering, or descriptor work.
                if len(names) >= limit:
                    truncated = True
                    break
                entry_path = requested_parent / name
                try:
                    paths._authorize_requested_path(
                        entry_path,
                        resolved_parent / name,
                        operation=operation,
                    )
                    entry_stat = entry.stat(follow_symlinks=False)
                except (paths.FilesystemError, OSError):
                    continue
                started = _timing_started(performance_details)
                try:
                    is_symlink = stat.S_ISLNK(entry_stat.st_mode)
                    target_st = None
                    target_text = None
                    if is_symlink:
                        link_fd = None
                        try:
                            link_fd = os.open(
                                name,
                                paths.descriptor_open_flags(paths.metadata_descriptor_flags()),
                                dir_fd=directory_descriptor,
                            )
                            paths.authority_pinned(operation, entry_path)
                            entry_stat = os.fstat(link_fd)
                            target_text = paths.symlink_target_from_descriptor(
                                link_fd,
                                entry_path,
                                parent_descriptor=directory_descriptor,
                                name=name,
                            )
                        finally:
                            if link_fd is not None:
                                os.close(link_fd)
                        resolved = _resolved_symlink_target(
                            entry_path,
                            target_text=target_text,
                        )
                    else:
                        resolved = resolved_parent / name
                finally:
                    _record_elapsed(performance_details, "scan_resolve_ms", started)
                started = _timing_started(performance_details)
                try:
                    paths._authorize_requested_path(
                        entry_path,
                        resolved,
                        operation=operation,
                        observe_name=False,
                    )
                    blocked = False
                except paths.FilesystemError as error:
                    if error.status != 403:
                        raise
                    blocked = True
                finally:
                    _record_elapsed(performance_details, "scan_secret_filter_ms", started)
                if blocked:
                    continue
                try:
                    child_context = (
                        paths.safe_path(
                            str(entry_path),
                            flags=paths.metadata_descriptor_flags(),
                            resolved_path=resolved,
                            operation=operation,
                            observe_name=False,
                        )
                        if is_symlink
                        else paths.safe_child(
                            directory_descriptor,
                            entry_path,
                            resolved,
                            flags=paths.metadata_descriptor_flags(),
                            operation=operation,
                            observe_name=False,
                        )
                    )
                    child_context_started = _timing_started(performance_details)
                    try:
                        with child_context as child_handle:
                            _record_elapsed(performance_details, "scan_child_open_ms", child_context_started)
                            child_info_started = _timing_started(performance_details)
                            try:
                                if not is_symlink and stat.S_ISLNK(child_handle.stat_result.st_mode):
                                    continue
                                target_st = child_handle.stat_result if is_symlink else None
                                info = _entry_info(
                                    entry_path,
                                    name,
                                    resolved=child_handle.resolved,
                                    repo_info_cache=repo_info_cache,
                                    repo_info_deadline=repo_info_deadline,
                                    performance_details=performance_details,
                                    symlink_target_stat=child_handle.stat_result if is_symlink else None,
                                    symlink_target_text=target_text,
                                    entry_stat=entry_stat if is_symlink else child_handle.stat_result,
                                    inspection_descriptor=child_handle.descriptor,
                                    inspection_handle=child_handle,
                                    include_repo_info=include_repo_info,
                                )
                            finally:
                                _record_elapsed(performance_details, "scan_child_info_ms", child_info_started)
                    finally:
                        _record_elapsed(performance_details, "scan_child_context_ms", child_context_started)
                except paths.FilesystemError as error:
                    if error.status == 403:
                        continue
                    if is_symlink and error.status in {403, 404}:
                        info = _entry_info(
                            entry_path,
                            name,
                            resolved=resolved,
                            repo_info_cache=repo_info_cache,
                            repo_info_deadline=repo_info_deadline,
                            performance_details=performance_details,
                            symlink_target_stat=None,
                            symlink_target_text=target_text,
                            entry_stat=entry_stat,
                            include_repo_info=include_repo_info,
                        )
                    else:
                        raise
                except OSError:
                    continue
                names.append(_ResolvedDirectoryName(
                    name,
                    resolved,
                    symlink_target_stat=target_st,
                    symlink_target_text=target_text,
                    info=info,
                ))
    finally:
        if directory_descriptor is not None:
            os.close(directory_descriptor)
    return names, truncated


def _list_directory_from_pinned_root(
    root_descriptor: int,
    *,
    resolved_root: Path,
    performance_details: dict[str, float] | None = None,
    display_path: Path | None = None,
    root_stat: os.stat_result | None = None,
    watch_signature_child_limit: int = 0,
    include_repo_info: bool = True,
) -> dict[str, Any]:
    if performance_details is not None:
        performance_details.update({
            "validate_ms": 0.0,
            "scan_ms": 0.0,
            "scan_open_ms": 0.0,
            "scan_iterate_ms": 0.0,
            "scan_resolve_ms": 0.0,
            "scan_secret_filter_ms": 0.0,
            "scan_child_context_ms": 0.0,
            "scan_child_open_ms": 0.0,
            "scan_child_info_ms": 0.0,
            "entry_loop_ms": 0.0,
            "entry_lstat_ms": 0.0,
            "symlink_stat_ms": 0.0,
            "repo_probe_ms": 0.0,
            "repo_info_ms": 0.0,
            "identity_ms": 0.0,
            "sort_ms": 0.0,
            "assemble_ms": 0.0,
            "entry_count": 0.0,
            "repo_count": 0.0,
            "repo_deferred_count": 0.0,
        })
    started = _timing_started(performance_details)
    try:
        path = resolved_root
    finally:
        _record_elapsed(performance_details, "validate_ms", started)
    started = _timing_started(performance_details)
    try:
        names, truncated = _visible_directory_names(
            root_descriptor,
            resolved_parent=resolved_root,
            performance_details=performance_details,
            include_repo_info=include_repo_info,
            requested_path=display_path,
        )
    finally:
        _record_elapsed(performance_details, "scan_ms", started)
    repo_info_cache: dict[Path, dict[str, Any]] = {}
    repo_info_deadline = time.monotonic() + FINDER_REPO_INFO_BUDGET_SECONDS
    entries = []
    started = _timing_started(performance_details)
    try:
        for name in names:
            if not isinstance(name, _ResolvedDirectoryName) or name.info is None:
                raise paths.FilesystemError(
                    "directory scan returned an entry without descriptor-bound metadata",
                    status=500,
                    message_key="fs.error.operationFailed",
                )
            info = name.info
            entries.append(info)
            if performance_details is not None:
                if info.get("is_repo") is True:
                    performance_details["repo_count"] += 1
                if info.get("repo_info_deferred") is True:
                    performance_details["repo_deferred_count"] += 1
    finally:
        _record_elapsed(performance_details, "entry_loop_ms", started)
    if performance_details is not None:
        performance_details["entry_count"] = float(len(entries))
    response_path = display_path if display_path is not None else path
    watch_signature_value = (
        _watch_signature_from_entries(
            response_path,
            root_stat,
            entries,
            watch_signature_child_limit,
        )
        if root_stat is not None and watch_signature_child_limit > 0
        else None
    )
    started = _timing_started(performance_details)
    try:
        entries.sort(key=lambda entry: (entry.get("kind") != "dir", str(entry.get("name", "")).lower()))
    finally:
        _record_elapsed(performance_details, "sort_ms", started)
    started = _timing_started(performance_details)
    try:
        parent = str(response_path.parent) if str(response_path) != "/" else None
        result = {
            "path": str(response_path),
            "parent": parent,
            "entries": entries,
            "truncated": truncated,
            "entry_limit": MAX_DIRECTORY_ENTRIES,
        }
        if watch_signature_value is not None:
            result["watch_signature"] = watch_signature_value
        return result
    finally:
        _record_elapsed(performance_details, "assemble_ms", started)


def list_directory(
    raw_path: str,
    *,
    performance_details: dict[str, float] | None = None,
    watch_signature_child_limit: int = 0,
    include_repo_info: bool = True,
) -> dict[str, Any]:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(raw_path, flags=directory_flags, operation="list_directory") as handle:
        return _list_directory_from_pinned_root(
            handle.descriptor,
            resolved_root=handle.resolved,
            performance_details=performance_details,
            display_path=handle.requested,
            root_stat=handle.stat_result,
            watch_signature_child_limit=watch_signature_child_limit,
            include_repo_info=include_repo_info,
        )


def watch_signature(raw_path: str, *, child_limit: int = 0) -> tuple[Any, ...]:
    try:
        with paths.safe_path(raw_path, flags=paths.metadata_descriptor_flags(), operation="watch_signature") as handle:
            file_stat = handle.stat_result
            if not stat.S_ISDIR(file_stat.st_mode) or child_limit <= 0:
                kind = "dir" if stat.S_ISDIR(file_stat.st_mode) else "file"
                return (str(handle.requested), kind, int(file_stat.st_mtime_ns), int(file_stat.st_size))
            names, _truncated = _visible_directory_names(
                handle.descriptor,
                resolved_parent=handle.resolved,
                child_limit=child_limit,
                include_repo_info=False,
                requested_path=handle.requested,
                operation="watch_signature",
            )
            entries: list[dict[str, Any]] = []
            for name in names[:max(1, int(child_limit))]:
                info = name.info if isinstance(name, _ResolvedDirectoryName) else None
                if not isinstance(info, dict):
                    continue
                entries.append(info)
            return _watch_signature_from_entries(
                handle.requested,
                file_stat,
                entries,
                child_limit,
            )
    except paths.FilesystemError as error:
        if error.status != 404:
            raise
        return (str(paths.parsed_request_path(raw_path)), "missing")
