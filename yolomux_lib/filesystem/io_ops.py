"""Read/write and path mutation helpers for filesystem APIs."""

from __future__ import annotations

import contextlib
import errno
import os
import json
import shutil
import stat
import tempfile
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


def read_file(raw_path: str) -> dict[str, Any]:
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
        git_root, git_tracked, git_history, _relative_path, _repo_info = git_ops.pinned_file_git_metadata(
            handle,
            operation="read_file",
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


def _delete_directory_contents(
    directory_fd: int,
    requested_directory: Path,
    resolved_directory: Path,
) -> None:
    """Delete descendants through pinned directory descriptors so every generation is observable."""

    with os.scandir(directory_fd) as entries:
        for entry in sorted(entries, key=lambda item: item.name.lower()):
            requested_child = requested_directory / entry.name
            resolved_child = resolved_directory / entry.name
            paths.name_observed("delete_path", requested_child)
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
                    )
                os.rmdir(entry.name, dir_fd=directory_fd)
            else:
                paths.authority_pinned("delete_path", requested_child)
                os.unlink(entry.name, dir_fd=directory_fd)


def delete_path(raw_path: str) -> dict[str, Any]:
    with paths.safe_parent(raw_path, operation="delete_path") as handle:
        path = handle.requested
        paths._ensure_not_configured_root(path, "delete", resolved=handle.resolved_target)
        try:
            entry_stat = os.stat(handle.name, dir_fd=handle.descriptor, follow_symlinks=False)
        except FileNotFoundError as error:
            raise paths.FilesystemError.path_not_found(path) from error
        if stat.S_ISDIR(entry_stat.st_mode):
            with paths.safe_child(
                handle.descriptor,
                path,
                handle.namespace_target,
                flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                operation="delete_path",
                observe_name=False,
            ) as target:
                _delete_directory_contents(
                    target.descriptor,
                    path,
                    handle.namespace_target,
                )
            os.rmdir(handle.name, dir_fd=handle.descriptor)
            kind = "dir"
        else:
            os.unlink(handle.name, dir_fd=handle.descriptor)
            kind = "file"
        return {"path": str(path), "deleted": True, "kind": kind}


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
                flags=getattr(os, "O_PATH", os.O_RDONLY),
                operation="rename_path",
                observe_name=False,
            )
            with source_context as source_handle, git_ops.pinned_repo_path(
                source_handle,
                operation="rename_path",
            ) as pinned_repo:
                try:
                    os.stat(name, dir_fd=handle.descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise paths.FilesystemError.target_exists(target)
                tracked = False
                if pinned_repo is not None:
                    _repo, old_relative, repo_handle = pinned_repo
                    tracked = git_ops._git_with_pinned_repo(
                        repo_handle,
                        ["ls-files", "--error-unmatch", "--", old_relative],
                        timeout=1.5,
                    ).returncode == 0
                os.rename(handle.name, name, src_dir_fd=handle.descriptor, dst_dir_fd=handle.descriptor)
                if tracked and pinned_repo is not None:
                    repo, old_relative, repo_handle = pinned_repo
                    new_relative = handle.namespace_target.with_name(name).relative_to(repo).as_posix()
                    staged = git_ops._git_with_pinned_repo(
                        repo_handle,
                        ["add", "-A", "--", old_relative, new_relative],
                        timeout=3.0,
                    )
                    if staged.returncode != 0:
                        raise paths.FilesystemError(
                            "git rename staging failed",
                            status=500,
                            message_key="fs.error.operationFailed",
                            diagnostic=git_ops.cmd_error(staged, "git add after rename failed"),
                        )
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


def _existing_path_info(raw_path: str, *, operation: str) -> dict[str, Any]:
    with paths.safe_path(raw_path, operation=operation) as handle:
        path = handle.requested
        file_stat = handle.stat_result
        kind = "dir" if stat.S_ISDIR(file_stat.st_mode) else "file"
        size: int | None = None
        mtime: int | None = None
        mtime_ns: int | None = None
        preview_mime = ""
        if kind == "file":
            size = int(file_stat.st_size)
            mtime = int(file_stat.st_mtime)
            mtime_ns = int(file_stat.st_mtime_ns)
            with os.fdopen(os.dup(handle.descriptor), "rb") as fh:
                preview_mime = _sniff_raw_mime(fh.read(512)) or IMAGE_EXTENSIONS.get(path.suffix.lower(), "")
        repo_root, _tracked, _history, relative_path, repo_info = git_ops.pinned_file_git_metadata(
            handle,
            include_repo_info=True,
            operation=operation,
        )
        return {
            "path": str(path),
            "name": path.name,
            "kind": kind,
            "size": size,
            "mtime": mtime,
            "mtime_ns": mtime_ns,
            "preview_mime": preview_mime,
            "repo_root": repo_root,
            "relative_path": relative_path,
            "repo": repo_info,
            **paths._physical_file_identity(path, resolved=handle.resolved, stat_result=file_stat),
        }


def path_info(raw_path: str, *, operation: str = "path_info") -> dict[str, Any]:
    try:
        return _existing_path_info(raw_path, operation=operation)
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
            "repo_root": "",
            "relative_path": "",
            "repo": None,
        }


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


def _format_zip_size(size: int) -> str:
    mib = size / (1024 * 1024)
    return f"{mib:.1f} MB ({size} bytes)"


def _zip_limit_message(path: Path, size: int, size_limit: int) -> str:
    return f"Folder is {_format_zip_size(size)}; over the {_format_zip_size(size_limit)} file transfer size cap. Please zip it yourself (e.g. `zip -r {path.name}.zip {path.name}`)."


def _walk_directory_sources(
    path: Path,
    size_limit: int | None = None,
    *,
    root_fd: int | None = None,
    operation: str = "",
    requested_root: Path | None = None,
) -> tuple[list[Path], list[Path], int]:
    directories: list[Path] = [path]
    files: list[Path] = []
    total_size = 0
    owned_root_fd = None
    if root_fd is None:
        owned_root_fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        root_fd = owned_root_fd
    try:
        walker = paths.walk_directory(
            root_fd,
            operation=operation,
            requested_root=requested_root or path,
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
    finally:
        if owned_root_fd is not None:
            os.close(owned_root_fd)
    return directories, files, total_size


def _walk_zip_sources(path: Path, max_bytes: int | None = None) -> tuple[list[Path], list[Path], int]:
    byte_cap = int(max_bytes) if isinstance(max_bytes, (int, float)) and max_bytes > 0 else FS_ZIP_MAX_BYTES
    return _walk_directory_sources(path, byte_cap)


def count_directory_files(raw_path: str) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(raw_path, flags=flags, operation="count_directory_files") as handle:
        path = handle.requested
        _directories, files, _total_size = _walk_directory_sources(
            handle.descriptor_path(),
            root_fd=handle.descriptor,
            operation="count_directory_files",
            requested_root=path,
        )
        return {"path": str(path), "kind": "dir", "files": len(files), "recursive": True}


def zip_directory(raw_path: str, max_bytes: int | None = None) -> tuple[Any, int]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with paths.safe_path(raw_path, flags=flags, operation="zip_directory") as handle:
        path = handle.requested
        byte_cap = int(max_bytes) if isinstance(max_bytes, (int, float)) and max_bytes > 0 else FS_ZIP_MAX_BYTES
        data = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
        try:
            with zipfile.ZipFile(data, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                root_name = path.name or "root"
                archive.writestr(f"{root_name}/", b"")
                total_size = 0
                walker = paths.walk_directory(
                    handle.descriptor,
                    operation="zip_directory",
                    requested_root=path,
                )
                with contextlib.closing(walker):
                    for relative_directory, directory_fd, dirnames, file_rows in walker:
                        for dirname in dirnames:
                            archive_name = Path(root_name) / relative_directory / dirname
                            archive.writestr(archive_name.as_posix().rstrip("/") + "/", b"")
                        for filename, _scanned_stat in file_rows:
                            try:
                                member_path = path / relative_directory / filename
                                member_fd = os.open(
                                    filename,
                                    os.O_RDONLY | paths.nofollow_flag(),
                                    dir_fd=directory_fd,
                                )
                            except OSError as error:
                                if error.errno in {errno.ELOOP, errno.ENOENT}:
                                    continue
                                raise
                            try:
                                paths.authority_pinned("zip_directory", member_path)
                                member_stat = os.fstat(member_fd)
                                if not stat.S_ISREG(member_stat.st_mode):
                                    continue
                                total_size += member_stat.st_size
                                if total_size > byte_cap:
                                    raise paths.FilesystemError(
                                        _zip_limit_message(path, total_size, byte_cap),
                                        status=413,
                                        message_key="fs.error.folderTooLarge",
                                        message_params={"path": str(path), "size": total_size, "max": byte_cap},
                                    )
                                archive_name = (Path(root_name) / relative_directory / filename).as_posix()
                                with os.fdopen(os.dup(member_fd), "rb") as source, archive.open(archive_name, "w") as target:
                                    shutil.copyfileobj(source, target, length=1024 * 1024)
                            finally:
                                os.close(member_fd)
            size = data.tell()
            data.seek(0)
            return data, size
        except Exception:
            data.close()
            raise
