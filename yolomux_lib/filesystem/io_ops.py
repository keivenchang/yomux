"""Read/write and path mutation helpers for filesystem APIs."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import json
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from . import git_ops
from . import paths

MAX_WRITE_BYTES = 5 * 1024 * 1024  # 5 MB cap on file write
# Save conflict detection compares the mtime captured when YOLOmux loaded the file with the mtime on
# disk at save time. Some filesystems and browser/JSON round trips cannot preserve nanosecond mtimes
# exactly: JavaScript Number cannot represent current epoch nanoseconds safely, and remote/synced
# filesystems can report tiny timestamp drift without changing content. The tolerance is deliberately
# small: 10 ms absorbs precision/rounding jitter like the observed 85 ns drift, while still treating
# normal editor/tool writes as real conflicts so YOLOmux does not overwrite newer disk content.
MTIME_NS_CONFLICT_TOLERANCE = 10_000_000

TEXT_EXTENSIONS = {
    ".rs", ".py", ".md", ".txt", ".json", ".jsonl", ".ndjson", ".geojson", ".ipynb", ".js", ".ts", ".tsx", ".jsx",
    ".css", ".scss", ".html", ".htm", ".xml", ".yaml", ".yml", ".toml",
    ".sh", ".bash", ".zsh", ".fish", ".c", ".h", ".cpp", ".hpp", ".cc",
    ".go", ".rb", ".pl", ".lua", ".sql", ".env", ".cfg", ".ini", ".conf", ".properties", ".props",
    ".mmd", ".mermaid", ".drawio", ".dio", ".excalidraw", ".dot", ".gv", ".puml", ".plantuml",
    ".log", ".trace", ".out", ".rst", ".adoc", ".asciidoc", ".diff", ".patch", ".srt", ".vtt",
    ".gitignore", ".dockerignore", ".dockerfile",
}

EXTENSIONLESS_TEXT_NAMES = {
    "dockerfile", "makefile", "license", "readme", "gemfile", "rakefile",
    "justfile", "procfile",
}

IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".apng": "image/apng",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".pdf": "application/pdf",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".opus": "audio/opus",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".ogv": "video/ogg",
    ".3gp": "video/3gpp",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".sqlite": "application/vnd.sqlite3",
    ".sqlite3": "application/vnd.sqlite3",
    ".db": "application/vnd.sqlite3",
    ".parquet": "application/vnd.apache.parquet",
    ".arrow": "application/vnd.apache.arrow.file",
    ".feather": "application/vnd.apache.arrow.file",
    ".zip": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
    ".tgz": "application/gzip",
    ".bz2": "application/x-bzip2",
    ".xz": "application/x-xz",
    ".7z": "application/x-7z-compressed",
    ".rar": "application/vnd.rar",
}

MAX_RAW_BYTES = 100 * 1024 * 1024  # Fallback raw file download cap when no app transfer cap is supplied.
FS_ZIP_MAX_BYTES = 100 * 1024 * 1024  # Fallback folder zip cap when no app transfer cap is supplied.
TRANSFER_COPY_CHUNK_BYTES = 1024 * 1024


class _BoundedSeekableWriter:
    """Keep a seekable archive output below its configured wire-size cap."""

    def __init__(self, target: Any, maximum: int):
        self.target = target
        self.maximum = maximum
        self.high_water = 0

    def write(self, data: bytes) -> int:
        end = self.target.tell() + len(data)
        if end > self.maximum:
            raise paths.FilesystemError.file_too_large(end, self.maximum, label="archive")
        written = self.target.write(data)
        self.high_water = max(self.high_water, self.target.tell())
        return written

    def tell(self) -> int:
        return self.target.tell()

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        return self.target.seek(offset, whence)

    def flush(self) -> None:
        self.target.flush()


def read_json_file(
    path: Path,
    default: Any,
    *,
    exceptions: tuple[type[Exception], ...] = (OSError, json.JSONDecodeError),
) -> Any:
    """Read JSON from *path*, returning *default* for the caller's recoverable errors."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except exceptions:
        return default


def _sniff_raw_mime(data: bytes) -> str:
    sample = data[:64]
    if sample.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if sample.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if sample.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if sample.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if sample.startswith(b"%PDF-"):
        return "application/pdf"
    if sample.startswith(b"ID3"):
        return "audio/mpeg"
    if sample.startswith(b"SQLite format 3\x00"):
        return "application/vnd.sqlite3"
    if sample.startswith(b"PAR1"):
        return "application/vnd.apache.parquet"
    if sample.startswith(b"PK\x03\x04"):
        return "application/zip"
    if sample.startswith(b"RIFF") and sample[8:12] == b"WEBP":
        return "image/webp"
    if sample.startswith(b"RIFF") and sample[8:12] == b"WAVE":
        return "audio/wav"
    if len(sample) >= 12 and sample[4:8] == b"ftyp":
        brand = sample[8:12]
        if brand in {b"avif", b"avis"}:
            return "image/avif"
        if brand in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}:
            return "image/heic"
        if brand in {b"mp41", b"mp42", b"isom", b"M4A ", b"M4V "}:
            return "video/mp4" if brand not in {b"M4A "} else "audio/mp4"
    return ""


def read_file(raw_path: str, *, include_git: bool = True) -> dict[str, Any]:
    with paths.safe_path(raw_path, operation="read_file") as handle:
        path = handle.requested
        file_stat = handle.stat_result
        if stat.S_ISDIR(file_stat.st_mode):
            raise paths.FilesystemError.is_directory(path)
        size = file_stat.st_size
        if size > paths.MAX_READ_BYTES:
            raise paths.FilesystemError.file_too_large(size, paths.MAX_READ_BYTES)
        with os.fdopen(os.dup(handle.descriptor), "rb") as fh:
            raw = fh.read(paths.MAX_READ_BYTES + 1)
        if paths._looks_binary(raw):
            raise paths.FilesystemError("file appears to be binary", status=415, message_key="fs.error.binary")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("utf-8", errors="replace")
        # The file has already been read at this point. Git history is decoration on top of that
        # answer, so a repository that is slow, huge, or being rewritten reports itself in
        # `git_enrichment` instead of taking the file away from the reader.
        git_root, git_tracked, git_history, _relative_path, _repo_info, git_reason = _optional_file_git_metadata(
            handle, operation="read_file", include_git=include_git,
        )
        return {
            "path": str(path),
            "size": int(size),
            "mtime": int(file_stat.st_mtime),
            "mtime_ns": int(file_stat.st_mtime_ns),
            "content": content,
            "extension": path.suffix.lower(),
            "is_text_extension": path.suffix.lower() in TEXT_EXTENSIONS,
            "git_root": git_root,
            "git_tracked": git_tracked,
            "git_history": git_history,
            "git_has_history": len(git_history) > 1,
            "git_enrichment": {"available": not git_reason, "reason": git_reason},
            **paths._physical_file_identity(path, resolved=handle.resolved, stat_result=file_stat),
        }


def write_file(raw_path: str, content: str, expected_mtime: int | None = None) -> dict[str, Any]:
    if not isinstance(content, str):
        raise paths.FilesystemError("content must be a string", message_key="fs.error.contentString")
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise paths.FilesystemError(
            f"content too large ({len(data)} bytes; max {MAX_WRITE_BYTES})",
            status=413,
            message_key="fs.error.contentTooLarge",
            message_params={"size": len(data), "max": MAX_WRITE_BYTES},
        )
    flags = os.O_WRONLY | os.O_CREAT
    with paths.safe_path(raw_path, flags=flags, create_parents=True, operation="write_file") as handle:
        path = handle.requested
        paths._ensure_not_configured_root(path, "write", resolved=handle.resolved)
        actual_stat = handle.stat_result
        if stat.S_ISDIR(actual_stat.st_mode):
            raise paths.FilesystemError.is_directory(path)
        if expected_mtime is not None:
            actual = int(actual_stat.st_mtime_ns)
            actual_legacy = int(actual_stat.st_mtime)
            if not _mtime_matches_expected(int(expected_mtime), actual, actual_legacy):
                raise paths.FilesystemError(
                    f"file changed on disk (expected mtime {expected_mtime}, got {actual})",
                    status=409,
                    message_key="fs.error.changedOnDisk",
                    message_params={"path": str(path)},
                    diagnostic=f"expected mtime {expected_mtime}, got {actual}",
                )
        os.ftruncate(handle.descriptor, 0)
        with os.fdopen(os.dup(handle.descriptor), "wb") as fh:
            fh.write(data)
        file_stat = os.fstat(handle.descriptor)
        return {
            "path": str(path),
            "size": len(data),
            "mtime": int(file_stat.st_mtime),
            "mtime_ns": int(file_stat.st_mtime_ns),
            **paths._physical_file_identity(path, resolved=handle.resolved, stat_result=file_stat),
        }


def _mtime_matches_expected(expected: int, actual_ns: int, actual_legacy: int) -> bool:
    if expected == actual_legacy:
        return True
    return abs(expected - actual_ns) <= MTIME_NS_CONFLICT_TOLERANCE


def validated_child_name(raw_name: str) -> str:
    """Return the one lexical rule a rename target name must pass.

    Like `paths.validate_request_path_lexical` this reads only the request string -- no descriptor
    and no name service -- so HTTP acceptance can apply it before a rename becomes an accepted
    operation instead of discovering it in the worker.
    """

    if not isinstance(raw_name, str):
        raise paths.FilesystemError("name must be a string", message_key="fs.error.nameString")
    name = raw_name.strip()
    if not name:
        raise paths.FilesystemError("name is required", message_key="fs.error.nameRequired")
    if name in {".", ".."} or "/" in name or "\x00" in name or "\n" in name or "\r" in name:
        raise paths.FilesystemError("name contains illegal characters", message_key="fs.error.nameIllegal")
    return name


class PartialDeleteError(paths.FilesystemError):
    """A recursive delete made a visible change before it stopped.

    A caller must not report this as an ordinary failed delete: the deleted paths need cache and
    search invalidation, and the full failed path is needed to distinguish a stopped walk from a
    request that was refused before it changed anything.
    """

    def __init__(self, reason: str, failed_path: Path, deleted_paths: list[str]):
        super().__init__(
            f"recursive delete stopped at {failed_path}",
            status=409,
            message_key="fs.error.operationFailed",
            message_params={"path": str(failed_path), "reason": str(reason)},
        )
        self.reason = str(reason)
        self.failed_path = str(failed_path)
        self.deleted_paths = list(deleted_paths)

    def payload(self, **fields: Any) -> Any:
        return super().payload(
            **fields,
            partial=bool(self.deleted_paths),
            delete_reason=self.reason,
            failed_path=self.failed_path,
            deleted_paths=list(self.deleted_paths),
        )


def _raise_if_delete_stopped(
    requested_path: Path,
    deleted_paths: list[str],
    *,
    cancel_event: Any | None,
    deadline_monotonic: float | None,
) -> None:
    """Cooperatively stop before another destructive recursive-delete step."""

    if cancel_event is not None and cancel_event.is_set():
        raise PartialDeleteError("cancelled", requested_path, deleted_paths)
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise PartialDeleteError("deadline_exceeded", requested_path, deleted_paths)


def _require_delete_name_identity(
    parent_descriptor: int,
    name: str,
    expected: os.stat_result,
    requested_path: Path,
) -> None:
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError as error:
        raise paths.FilesystemError.changed_on_disk(requested_path) from error
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise paths.FilesystemError.changed_on_disk(requested_path)


def _delete_directory_contents(
    directory_fd: int,
    requested_directory: Path,
    resolved_directory: Path,
    *,
    deleted_paths: list[str],
    cancel_event: Any | None,
    deadline_monotonic: float | None,
) -> None:
    """Delete descendants through pinned directory descriptors so every generation is observable."""

    _raise_if_delete_stopped(
        requested_directory,
        deleted_paths,
        cancel_event=cancel_event,
        deadline_monotonic=deadline_monotonic,
    )
    with os.scandir(directory_fd) as entries:
        for entry in sorted(entries, key=lambda item: item.name.lower()):
            requested_child = requested_directory / entry.name
            resolved_child = resolved_directory / entry.name
            _raise_if_delete_stopped(
                requested_child,
                deleted_paths,
                cancel_event=cancel_event,
                deadline_monotonic=deadline_monotonic,
            )
            try:
                paths._authorize_requested_path(
                    requested_child,
                    resolved_child,
                    operation="delete_path",
                )
                entry_stat = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(entry_stat.st_mode):
                    with paths.safe_child(
                        directory_fd,
                        requested_child,
                        resolved_child,
                        flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                        operation="delete_path",
                        observe_name=False,
                    ) as child:
                        _delete_directory_contents(
                            child.descriptor,
                            requested_child,
                            resolved_child,
                            deleted_paths=deleted_paths,
                            cancel_event=cancel_event,
                            deadline_monotonic=deadline_monotonic,
                        )
                        _raise_if_delete_stopped(
                            requested_child,
                            deleted_paths,
                            cancel_event=cancel_event,
                            deadline_monotonic=deadline_monotonic,
                        )
                        _require_delete_name_identity(
                            directory_fd,
                            entry.name,
                            child.stat_result,
                            requested_child,
                        )
                        os.rmdir(entry.name, dir_fd=directory_fd)
                else:
                    with paths.safe_child(
                        directory_fd,
                        requested_child,
                        resolved_child,
                        flags=paths.metadata_descriptor_flags(),
                        operation="delete_path",
                        observe_name=False,
                    ) as child:
                        _raise_if_delete_stopped(
                            requested_child,
                            deleted_paths,
                            cancel_event=cancel_event,
                            deadline_monotonic=deadline_monotonic,
                        )
                        _require_delete_name_identity(
                            directory_fd,
                            entry.name,
                            child.stat_result,
                            requested_child,
                        )
                        os.unlink(entry.name, dir_fd=directory_fd)
            except PartialDeleteError:
                raise
            except (OSError, paths.FilesystemError) as error:
                if deleted_paths:
                    raise PartialDeleteError("entry_failed", requested_child, deleted_paths) from error
                raise
            deleted_paths.append(str(requested_child))


def delete_path(
    raw_path: str,
    *,
    recursive: bool = False,
    cancel_event: Any | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Delete one authorized entry, bounded by default and recursive only when asked.

    ONE function and ONE signature for both classes, because both must keep the same authorization
    owner (`safe_parent` plus exactly one validated basename), the same configured-root refusal, and
    the same result shape.  `recursive` selects the COST CLASS, not a second route:

    - non-directory  -> one `unlink`.  Bounded.
    - directory, `recursive=False` -> one `rmdir` probe.  An empty directory is a terminal delete; a
      nonempty one returns the typed NON-TERMINAL `{"deleted": False, "pending": "subtree"}` without
      ever enumerating the subtree, so a bounded request can never pay an input-sized cost.
    - directory, `recursive=True` -> the descriptor-pinned subtree walk, unchanged.

    `pending` is not a failure and not a delete: it is this delete telling its caller which lane the
    work actually belongs on.  The caller re-submits with `recursive=True` under the same operation.
    """

    with paths.safe_parent(raw_path, operation="delete_path_parent") as handle:
        path = handle.requested
        paths._ensure_not_configured_root(path, "delete", resolved=handle.resolved_target)
        try:
            target_context = paths.safe_child(
                handle.descriptor,
                path,
                handle.namespace_target,
                flags=paths.metadata_descriptor_flags(),
                operation="delete_path",
                observe_name=False,
            )
        except FileNotFoundError as error:
            raise paths.FilesystemError.path_not_found(path) from error
        with target_context as target:
            handle.require_target_identity(target)
            entry_stat = target.stat_result
            if not stat.S_ISDIR(entry_stat.st_mode):
                # Symlinks land here and report `kind: "file", matching the pre-split payload.  The
                # link itself is unlinked; `safe_parent` never follows it.
                _require_delete_name_identity(handle.descriptor, handle.name, entry_stat, path)
                os.unlink(handle.name, dir_fd=handle.descriptor)
                return {"path": str(path), "deleted": True, "kind": "file"}
            if not recursive:
                _require_delete_name_identity(handle.descriptor, handle.name, entry_stat, path)
                try:
                    os.rmdir(handle.name, dir_fd=handle.descriptor)
                except OSError as error:
                    if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                        raise
                    return {"path": str(path), "deleted": False, "kind": "dir", "pending": "subtree"}
                return {"path": str(path), "deleted": True, "kind": "dir"}
            deleted_paths: list[str] = []
            try:
                walk_descriptor = os.open(
                    ".",
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=target.descriptor,
                )
                try:
                    _delete_directory_contents(
                        walk_descriptor,
                        path,
                        handle.namespace_target,
                        deleted_paths=deleted_paths,
                        cancel_event=cancel_event,
                        deadline_monotonic=deadline_monotonic,
                    )
                finally:
                    os.close(walk_descriptor)
                _raise_if_delete_stopped(
                    path,
                    deleted_paths,
                    cancel_event=cancel_event,
                    deadline_monotonic=deadline_monotonic,
                )
                _require_delete_name_identity(handle.descriptor, handle.name, entry_stat, path)
                os.rmdir(handle.name, dir_fd=handle.descriptor)
            except PartialDeleteError:
                raise
            except (OSError, paths.FilesystemError) as error:
                if deleted_paths:
                    raise PartialDeleteError("entry_failed", path, deleted_paths) from error
                raise
            return {"path": str(path), "deleted": True, "kind": "dir"}


def rename_path(raw_path: str, new_name: str) -> dict[str, Any]:
    name = validated_child_name(new_name)
    requested = paths.parsed_request_path(raw_path)
    target = requested.with_name(name)
    with paths.safe_parent(
        raw_path,
        operation="rename_path",
        additional_requested=(target,),
    ) as handle:
        path = handle.requested
        paths._ensure_not_configured_root(path, "rename", resolved=handle.resolved_target)
        try:
            source_context = paths.safe_child(
                handle.descriptor,
                path,
                handle.namespace_target,
                flags=paths.metadata_descriptor_flags(),
                operation="rename_path",
                observe_name=False,
            )
            with source_context as source_handle, git_ops.pinned_repo_path(
                source_handle,
                operation="rename_path",
            ) as pinned_repo:
                handle.require_target_identity(source_handle)
                try:
                    os.stat(name, dir_fd=handle.descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise paths.FilesystemError.target_exists(target)
                tracked = False
                if pinned_repo is not None:
                    new_relative = handle.namespace_target.with_name(name).relative_to(pinned_repo.repo).as_posix()
                    tracked = git_ops.prepare_pinned_index_rename(pinned_repo, new_relative)
                current_source = os.stat(handle.name, dir_fd=handle.descriptor, follow_symlinks=False)
                source_stat = source_handle.stat_result
                if (current_source.st_dev, current_source.st_ino) != (source_stat.st_dev, source_stat.st_ino):
                    raise paths.FilesystemError.changed_on_disk(path)
                try:
                    paths.rename_noreplace(handle.descriptor, handle.name, name)
                except FileExistsError as error:
                    raise paths.FilesystemError.target_exists(target) from error
                if tracked and pinned_repo is not None:
                    renamed_stat = os.stat(name, dir_fd=handle.descriptor, follow_symlinks=False)
                    if (renamed_stat.st_dev, renamed_stat.st_ino) != (source_stat.st_dev, source_stat.st_ino):
                        raise paths.FilesystemError(
                            "renamed path changed before Git index publication",
                            status=409,
                            message_key="fs.error.changedOnDisk",
                            message_params={"path": str(target)},
                        )
                    git_ops.publish_pinned_index_rename(pinned_repo)
        except FileNotFoundError as error:
            raise paths.FilesystemError.path_not_found(path) from error
        return {"path": str(target), "old_path": str(path), "name": name}


def create_directory(raw_path: str) -> dict[str, Any]:
    with paths.safe_parent(raw_path, operation="create_directory") as handle:
        path = handle.requested
        try:
            os.stat(handle.name, dir_fd=handle.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise paths.FilesystemError.target_exists(path)
        os.mkdir(handle.name, dir_fd=handle.descriptor)
        return {"path": str(path), "created": True, "kind": "dir"}


def _optional_file_git_metadata(
    handle: paths.SafePathHandle,
    *,
    operation: str,
    include_git: bool,
    repo_info_cache: dict[str, dict[str, Any] | None] | None = None,
) -> tuple[str, bool, list[Any], str, dict[str, Any] | None, str]:
    if not include_git:
        return "", False, [], "", None, "deferred"
    return git_ops.optional_pinned_file_git_metadata(
        handle, include_repo_info=True, repo_info_cache=repo_info_cache, operation=operation,
    )


def _existing_path_info(
    raw_path: str,
    *,
    operation: str,
    repo_info_cache: dict[str, dict[str, Any] | None] | None = None,
    include_git: bool = True,
) -> dict[str, Any]:
    with paths.safe_path(raw_path, operation=operation) as handle:
        path = handle.requested
        file_stat = handle.stat_result
        result = handle.base_capability()
        kind = str(result["kind"])
        preview_mime = ""
        diff_capable = False
        if kind == "file":
            with os.fdopen(os.dup(handle.descriptor), "rb") as fh:
                sample = fh.read(512)
            preview_mime = _sniff_raw_mime(sample) or IMAGE_EXTENSIONS.get(path.suffix.lower(), "")
            diff_capable = int(result["size"] or 0) <= paths.MAX_READ_BYTES and not preview_mime and not paths._looks_binary(sample)
        # Validating a path is the step that decides whether Open is offered at all, so it carries
        # the same rule as the read: Git metadata that cannot be produced is reported, not fatal.
        repo_root, tracked, history, relative_path, repo_info, git_reason = _optional_file_git_metadata(
            handle, operation=operation, include_git=include_git, repo_info_cache=repo_info_cache,
        )
        return {
            **result,
            "preview_mime": preview_mime,
            "diff_capable": diff_capable,
            "repo_root": repo_root,
            "git_tracked": tracked,
            "git_has_history": bool(history),
            "relative_path": relative_path,
            "repo": repo_info,
            "git_enrichment": {"available": not git_reason, "reason": git_reason},
        }


def path_info(
    raw_path: str,
    *,
    operation: str = "path_info",
    repo_info_cache: dict[str, dict[str, Any] | None] | None = None,
    include_git: bool = True,
) -> dict[str, Any]:
    try:
        return _existing_path_info(raw_path, operation=operation, repo_info_cache=repo_info_cache, include_git=include_git)
    except paths.FilesystemError as error:
        if error.status != 404:
            raise
    with paths.safe_parent(raw_path, operation=operation) as parent:
        try:
            entry_stat = os.stat(parent.name, dir_fd=parent.descriptor, follow_symlinks=False)
        except FileNotFoundError as error:
            raise paths.FilesystemError.path_not_found(parent.requested) from error
        if not stat.S_ISLNK(entry_stat.st_mode):
            raise paths.FilesystemError.path_not_found(parent.requested)
        return {
            "path": str(parent.requested),
            "name": parent.requested.name,
            "kind": "symlink-broken",
            "size": None,
            "mtime": None,
            "mtime_ns": None,
            "preview_mime": "",
            "diff_capable": False,
            "repo_root": "",
            "git_tracked": False,
            "git_has_history": False,
            "relative_path": "",
            "repo": None,
            "git_enrichment": {"available": False, "reason": "deferred"},
        }


def resolve_file_candidates(raw_paths: list[str]) -> dict[str, Any]:
    """Probe at most eight ordered paths through the descriptor-authorized base owner."""

    if not isinstance(raw_paths, list) or not raw_paths or len(raw_paths) > 8:
        raise ValueError("paths must contain 1 to 8 items")
    paths_in_order: list[str] = []
    for raw_path in raw_paths:
        if not isinstance(raw_path, str) or raw_path in paths_in_order:
            raise ValueError("paths must be unique strings")
        paths.validate_request_path_lexical(raw_path)
        paths_in_order.append(raw_path)
    misses: list[dict[str, Any]] = []
    for raw_path in paths_in_order:
        try:
            info = path_info(raw_path, operation="resolve_file_candidates", include_git=False)
        except paths.FilesystemError as error:
            if error.status == 404:
                misses.append({"path": raw_path, "status": 404})
                continue
            raise
        if info["kind"] == "file":
            return {"path": info["path"], "info": info, "misses": misses}
        misses.append({"path": raw_path, "status": 404})
    return {"path": "", "info": None, "misses": misses}


def is_text_path(raw_path: str) -> bool:
    try:
        with paths.safe_parent(raw_path) as handle:
            path = handle.requested
    except paths.FilesystemError:
        return False
    name = path.name.lower()
    return (
        path.suffix.lower() in TEXT_EXTENSIONS
        or name in TEXT_EXTENSIONS
        or name in EXTENSIONLESS_TEXT_NAMES
    )


def read_raw(raw_path: str, max_bytes: int | None = None) -> tuple[bytes, str]:
    """Return (bytes, mime_type) for binary previews. Caller decides whether to serve by extension."""
    with paths.safe_path(raw_path, operation="read_raw") as handle:
        path = handle.requested
        if stat.S_ISDIR(handle.stat_result.st_mode):
            raise paths.FilesystemError.is_directory(path)
        size = handle.stat_result.st_size
        byte_cap = int(max_bytes) if isinstance(max_bytes, (int, float)) and max_bytes > 0 else MAX_RAW_BYTES
        if size > byte_cap:
            raise paths.FilesystemError.file_too_large(size, byte_cap)
        with os.fdopen(os.dup(handle.descriptor), "rb") as fh:
            data = fh.read(byte_cap + 1)
        mime = _sniff_raw_mime(data) or IMAGE_EXTENSIONS.get(path.suffix.lower(), "application/octet-stream")
        return data, mime


def copy_raw_to(raw_path: str, target: Any, max_bytes: int | None = None) -> tuple[int, str, str]:
    """Copy one authorized file to a seekable artifact without materializing it in memory."""
    with paths.safe_path(raw_path, operation="read_raw") as handle:
        path = handle.requested
        if stat.S_ISDIR(handle.stat_result.st_mode):
            raise paths.FilesystemError.is_directory(path)
        byte_cap = int(max_bytes) if isinstance(max_bytes, (int, float)) and max_bytes > 0 else MAX_RAW_BYTES
        if handle.stat_result.st_size > byte_cap:
            raise paths.FilesystemError.file_too_large(handle.stat_result.st_size, byte_cap)
        digest = hashlib.sha256()
        sample = b""
        copied = 0
        with os.fdopen(os.dup(handle.descriptor), "rb") as source:
            while True:
                chunk = source.read(min(TRANSFER_COPY_CHUNK_BYTES, byte_cap + 1 - copied))
                if not chunk:
                    break
                copied += len(chunk)
                if copied > byte_cap:
                    raise paths.FilesystemError.file_too_large(copied, byte_cap)
                if not sample:
                    sample = chunk[:64]
                target.write(chunk)
                digest.update(chunk)
        final_stat = os.fstat(handle.descriptor)
        initial_identity = (handle.stat_result.st_dev, handle.stat_result.st_ino, handle.stat_result.st_size, handle.stat_result.st_mtime_ns)
        final_identity = (final_stat.st_dev, final_stat.st_ino, final_stat.st_size, final_stat.st_mtime_ns)
        if stat.S_ISREG(handle.stat_result.st_mode) and (final_identity != initial_identity or copied != handle.stat_result.st_size):
            raise paths.FilesystemError(
                "file changed while it was being transferred",
                status=409,
                message_key="fs.error.changedOnDisk",
                message_params={"path": str(path)},
            )
        mime = _sniff_raw_mime(sample) or IMAGE_EXTENSIONS.get(path.suffix.lower(), "application/octet-stream")
        return copied, mime, digest.hexdigest()


def _format_zip_size(size: int) -> str:
    mib = size / (1024 * 1024)
    return f"{mib:.1f} MB ({size} bytes)"


def _zip_limit_message(path: Path, size: int, size_limit: int) -> str:
    return f"Folder is {_format_zip_size(size)}; over the {_format_zip_size(size_limit)} file transfer size cap. Please zip it yourself (e.g. `zip -r {path.name}.zip {path.name}`)."


def _walk_directory_sources(
    path: Path,
    size_limit: int | None = None,
    *,
    root_fd: int,
    operation: str = "",
    requested_root: Path | None = None,
    resolved_root: Path | None = None,
) -> tuple[list[Path], list[Path], int]:
    directories: list[Path] = [path]
    files: list[Path] = []
    total_size = 0
    walker = paths.walk_directory(
        root_fd,
        operation=operation,
        requested_root=requested_root or path,
        resolved_root=resolved_root or path,
    )
    with contextlib.closing(walker):
        for relative, _directory_fd, dirnames, file_rows in walker:
            root_path = path / relative
            directories.extend(root_path / dirname for dirname in dirnames)
            for filename, child_stat in file_rows:
                child = root_path / filename
                total_size += child_stat.st_size
                if size_limit is not None and total_size > size_limit:
                    raise paths.FilesystemError(
                        _zip_limit_message(path, total_size, size_limit),
                        status=413,
                        message_key="fs.error.folderTooLarge",
                        message_params={"path": str(path), "size": total_size, "max": size_limit},
                    )
                files.append(child)
    return directories, files, total_size


def count_directory_files(raw_path: str) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(raw_path, flags=flags, operation="count_directory_files") as handle:
        path = handle.requested
        _directories, files, _total_size = _walk_directory_sources(
            handle.descriptor_path(),
            root_fd=handle.descriptor,
            operation="count_directory_files",
            requested_root=path,
            resolved_root=handle.resolved,
        )
        return {"path": str(path), "kind": "dir", "files": len(files), "recursive": True}


def zip_directory(raw_path: str, max_bytes: int | None = None) -> tuple[Any, int]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(raw_path, flags=flags, operation="zip_directory") as handle:
        byte_cap = int(max_bytes) if isinstance(max_bytes, (int, float)) and max_bytes > 0 else FS_ZIP_MAX_BYTES
        data = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
        try:
            size, _digest = _zip_directory_handle_to(handle, data, byte_cap)
            return data, size
        except Exception:
            data.close()
            raise


def zip_directory_to(raw_path: str, target: Any, max_bytes: int | None = None) -> tuple[int, str]:
    """Write one authorized directory archive into a bounded seekable artifact."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(raw_path, flags=flags, operation="zip_directory") as handle:
        byte_cap = int(max_bytes) if isinstance(max_bytes, (int, float)) and max_bytes > 0 else FS_ZIP_MAX_BYTES
        return _zip_directory_handle_to(handle, target, byte_cap)


def _zip_directory_handle_to(handle: Any, target: Any, byte_cap: int) -> tuple[int, str]:
    """Create one bounded archive from an already-authorized directory descriptor."""
    path = handle.requested
    # Preserve the existing source-size contract before archive headers can hit the response cap.
    # The second descriptor-pinned walk below creates the snapshot and detects mutation.
    _walk_directory_sources(
        handle.descriptor_path(),
        byte_cap,
        root_fd=handle.descriptor,
        operation="zip_directory",
        requested_root=path,
        resolved_root=handle.resolved,
    )
    output = _BoundedSeekableWriter(target, byte_cap)
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        root_name = path.name or "root"
        archive.writestr(f"{root_name}/", b"")
        source_bytes = 0
        walker = paths.walk_directory(
            handle.descriptor,
            operation="zip_directory",
            requested_root=path,
            resolved_root=handle.resolved,
        )
        with contextlib.closing(walker):
            for relative_directory, directory_fd, dirnames, file_rows in walker:
                for dirname in dirnames:
                    archive_name = Path(root_name) / relative_directory / dirname
                    archive.writestr(archive_name.as_posix().rstrip("/") + "/", b"")
                for filename, _scanned_stat in file_rows:
                    member_path = path / relative_directory / filename
                    try:
                        member_context = paths.safe_child(
                            directory_fd,
                            member_path,
                            handle.resolved / relative_directory / filename,
                            flags=os.O_RDONLY,
                            operation="zip_directory",
                            observe_name=False,
                        )
                        with member_context as member_handle:
                            before = member_handle.stat_result
                            if not stat.S_ISREG(before.st_mode):
                                continue
                            source_bytes += before.st_size
                            if source_bytes > byte_cap:
                                raise paths.FilesystemError(
                                    _zip_limit_message(path, source_bytes, byte_cap),
                                    status=413,
                                    message_key="fs.error.folderTooLarge",
                                    message_params={"path": str(path), "size": source_bytes, "max": byte_cap},
                                )
                            archive_name = (Path(root_name) / relative_directory / filename).as_posix()
                            with os.fdopen(os.dup(member_handle.descriptor), "rb") as source, archive.open(archive_name, "w") as archive_member:
                                shutil.copyfileobj(source, archive_member, length=TRANSFER_COPY_CHUNK_BYTES)
                            after = os.fstat(member_handle.descriptor)
                            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                                raise paths.FilesystemError(
                                    "file changed while its archive was being created",
                                    status=409,
                                    message_key="fs.error.changedOnDisk",
                                    message_params={"path": str(member_path)},
                                )
                    except (paths.FilesystemError, OSError) as error:
                        if isinstance(error, paths.FilesystemError) and error.message_key == "fs.error.credentialBlocked":
                            continue
                        if isinstance(error, OSError) and error.errno in {errno.ELOOP, errno.ENOENT}:
                            continue
                        raise
    size = output.high_water
    target.flush()
    target.seek(0)
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        chunk = target.read(min(TRANSFER_COPY_CHUNK_BYTES, remaining))
        if not chunk:
            raise OSError("archive artifact ended before its declared size")
        digest.update(chunk)
        remaining -= len(chunk)
    target.seek(0)
    return size, digest.hexdigest()
