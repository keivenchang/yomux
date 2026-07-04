"""Read/write and path mutation helpers for filesystem APIs."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from . import git_ops
from . import paths
from .errors import raise_os_error

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
    path = paths._validated_path(raw_path)
    if not path.exists():
        raise paths.FilesystemError.path_not_found(path)
    if path.is_dir():
        raise paths.FilesystemError.is_directory(path)
    file_stat = path.stat()
    size = file_stat.st_size
    if size > paths.MAX_READ_BYTES:
        raise paths.FilesystemError.file_too_large(size, paths.MAX_READ_BYTES)
    with path.open("rb") as fh:
        raw = fh.read(paths.MAX_READ_BYTES + 1)
    if paths._looks_binary(raw):
        raise paths.FilesystemError("file appears to be binary", status=415, message_key="fs.error.binary")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")
    git_root = git_ops.git_root_for_path(path)
    git_tracked = git_ops.git_tracks_path(path) if git_root else False
    git_history = git_ops.git_file_history(path) if git_tracked else []
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
        **paths._physical_file_identity(path),
    }


def write_file(raw_path: str, content: str, expected_mtime: int | None = None) -> dict[str, Any]:
    path = paths._validated_path(raw_path)
    paths._ensure_not_configured_root(path, "write")
    if path.exists() and path.is_dir():
        raise paths.FilesystemError.is_directory(path)
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
    if expected_mtime is not None and path.exists():
        actual_stat = path.stat()
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(data)
    file_stat = path.stat()
    return {
        "path": str(path),
        "size": len(data),
        "mtime": int(file_stat.st_mtime),
        "mtime_ns": int(file_stat.st_mtime_ns),
        **paths._physical_file_identity(path),
    }


def _mtime_matches_expected(expected: int, actual_ns: int, actual_legacy: int) -> bool:
    if expected == actual_legacy:
        return True
    return abs(expected - actual_ns) <= MTIME_NS_CONFLICT_TOLERANCE


def _validated_child_name(raw_name: str) -> str:
    if not isinstance(raw_name, str):
        raise paths.FilesystemError("name must be a string", message_key="fs.error.nameString")
    name = raw_name.strip()
    if not name:
        raise paths.FilesystemError("name is required", message_key="fs.error.nameRequired")
    if name in {".", ".."} or "/" in name or "\x00" in name or "\n" in name or "\r" in name:
        raise paths.FilesystemError("name contains illegal characters", message_key="fs.error.nameIllegal")
    return name


def delete_path(raw_path: str) -> dict[str, Any]:
    path = paths._validated_path(raw_path)
    paths._ensure_not_configured_root(path, "delete")
    if not path.exists() and not path.is_symlink():
        raise paths.FilesystemError.path_not_found(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        kind = "dir"
    else:
        path.unlink()
        kind = "file"
    return {"path": str(path), "deleted": True, "kind": kind}


def rename_path(raw_path: str, new_name: str) -> dict[str, Any]:
    path = paths._validated_path(raw_path)
    paths._ensure_not_configured_root(path, "rename")
    if not path.exists() and not path.is_symlink():
        raise paths.FilesystemError.path_not_found(path)
    name = _validated_child_name(new_name)
    target = path.with_name(name)
    if target.exists() or target.is_symlink():
        raise paths.FilesystemError.target_exists(target)
    # A tracked file moves with `git mv` (history-preserving, index-aware); everything else uses a plain rename.
    if not git_ops._git_mv_if_tracked(path, target):
        path.rename(target)
    return {"path": str(target), "old_path": str(path), "name": name}


def create_directory(raw_path: str) -> dict[str, Any]:
    path = paths._validated_path(raw_path)
    if path.exists() or path.is_symlink():
        raise paths.FilesystemError.target_exists(path)
    path.mkdir()
    return {"path": str(path), "created": True, "kind": "dir"}


def path_info(raw_path: str) -> dict[str, Any]:
    path = paths._validated_path(raw_path)
    exists = path.exists()
    is_symlink = path.is_symlink()
    if not exists and not is_symlink:
        raise paths.FilesystemError.path_not_found(path)
    kind = "symlink-broken" if is_symlink and not exists else "dir" if path.is_dir() else "file"
    size: int | None = None
    mtime: int | None = None
    mtime_ns: int | None = None
    preview_mime = ""
    if kind == "file":
        file_stat = path.stat()
        size = int(file_stat.st_size)
        mtime = int(file_stat.st_mtime)
        mtime_ns = int(file_stat.st_mtime_ns)
        with path.open("rb") as fh:
            preview_mime = _sniff_raw_mime(fh.read(512)) or IMAGE_EXTENSIONS.get(path.suffix.lower(), "")
    repo_root = git_ops.git_root_for_path(path)
    relative_path = ""
    repo_info: dict[str, Any] | None = None
    if repo_root:
        repo = Path(repo_root)
        try:
            relative_path = path.relative_to(repo).as_posix()
        except ValueError:
            relative_path = ""
        repo_info = git_ops.git_repo_info(repo, include_status=True)
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
        **paths._physical_file_identity(path),
    }


def is_text_path(raw_path: str) -> bool:
    try:
        path = paths._validated_path(raw_path)
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
    path = paths._validated_path(raw_path)
    if not path.exists():
        raise paths.FilesystemError.path_not_found(path)
    if path.is_dir():
        raise paths.FilesystemError.is_directory(path)
    size = path.stat().st_size
    byte_cap = int(max_bytes) if isinstance(max_bytes, (int, float)) and max_bytes > 0 else MAX_RAW_BYTES
    if size > byte_cap:
        raise paths.FilesystemError.file_too_large(size, byte_cap)
    with path.open("rb") as fh:
        data = fh.read(byte_cap + 1)
    mime = _sniff_raw_mime(data) or IMAGE_EXTENSIONS.get(path.suffix.lower(), "application/octet-stream")
    return data, mime


def _format_zip_size(size: int) -> str:
    mib = size / (1024 * 1024)
    return f"{mib:.1f} MB ({size} bytes)"


def _zip_limit_message(path: Path, size: int, size_limit: int) -> str:
    return f"Folder is {_format_zip_size(size)}; over the {_format_zip_size(size_limit)} file transfer size cap. Please zip it yourself (e.g. `zip -r {path.name}.zip {path.name}`)."


def _walk_directory_sources(path: Path, size_limit: int | None = None) -> tuple[list[Path], list[Path], int]:
    directories: list[Path] = [path]
    files: list[Path] = []
    total_size = 0

    for root, dirnames, filenames in os.walk(path, topdown=True, onerror=raise_os_error, followlinks=False):
        root_path = Path(root)
        kept_dirnames: list[str] = []
        for dirname in dirnames:
            child = root_path / dirname
            mode = child.lstat().st_mode
            if stat.S_ISDIR(mode):
                kept_dirnames.append(dirname)
                directories.append(child)
        dirnames[:] = kept_dirnames
        for filename in filenames:
            child = root_path / filename
            child_stat = child.lstat()
            if not stat.S_ISREG(child_stat.st_mode):
                continue
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


def _walk_zip_sources(path: Path, max_bytes: int | None = None) -> tuple[list[Path], list[Path], int]:
    byte_cap = int(max_bytes) if isinstance(max_bytes, (int, float)) and max_bytes > 0 else FS_ZIP_MAX_BYTES
    return _walk_directory_sources(path, byte_cap)


def count_directory_files(raw_path: str) -> dict[str, Any]:
    path = paths._validated_path(raw_path)
    if not path.exists():
        raise paths.FilesystemError.path_not_found(path)
    if not path.is_dir():
        raise paths.FilesystemError.not_directory(path)
    _directories, files, _total_size = _walk_directory_sources(path)
    return {"path": str(path), "kind": "dir", "files": len(files), "recursive": True}


def zip_directory(raw_path: str, max_bytes: int | None = None) -> tuple[Any, int]:
    path = paths._validated_path(raw_path)
    if not path.exists():
        raise paths.FilesystemError.path_not_found(path)
    if not path.is_dir():
        raise paths.FilesystemError.not_directory(path)
    directories, files, _total_size = _walk_zip_sources(path, max_bytes)
    base_parent = path.parent
    data = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    try:
        with zipfile.ZipFile(data, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for directory in directories:
                archive.write(directory, directory.relative_to(base_parent).as_posix() + "/")
            for file_path in files:
                archive.write(file_path, file_path.relative_to(base_parent).as_posix())
        size = data.tell()
        data.seek(0)
        return data, size
    except Exception:
        data.close()
        raise
