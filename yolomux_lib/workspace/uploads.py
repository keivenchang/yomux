from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
import time
from datetime import datetime
from email.message import Message
from pathlib import Path
from typing import Callable

from ..infra.common import DEFAULT_UPLOAD_FILENAME_TEMPLATE
from ..infra.common import PASTE_UPLOAD_NAME_RE
from ..infra.common import UPLOAD_MAX_BYTES
from ..infra.common import UPLOAD_MAX_FILES
from ..infra.common import UPLOAD_SAFE_NAME_RE
from ..infra.common import UploadedFile


UPLOAD_TMP_BASE = Path("/tmp")
UPLOAD_RETENTION_SWEEP_SECONDS = 24 * 60 * 60
UPLOAD_RETENTION_MAX_ENTRIES = 10_000


class UploadTargetError(OSError):
    """The central upload tree failed its ownership/symlink safety checks."""


def upload_path_component(value: str, fallback: str, max_length: int = 80) -> str:
    original = str(value or "").strip()
    if not original:
        return fallback
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", original).strip("._-") or fallback
    normalized = normalized[:max_length]
    if normalized != original:
        digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:10]
        normalized = f"{normalized[: max(1, max_length - 11)]}-{digest}"
    return normalized


def ensure_private_upload_dir(path: Path) -> Path:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise UploadTargetError(f"failed to create private upload directory {path}: {exc}") from exc
    try:
        info = path.lstat()
    except OSError as exc:
        raise UploadTargetError(f"failed to inspect private upload directory {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise UploadTargetError(f"refusing symlink upload directory: {path}")
    if not stat.S_ISDIR(info.st_mode):
        raise UploadTargetError(f"refusing non-directory upload path: {path}")
    if info.st_uid != os.geteuid():
        raise UploadTargetError(f"refusing upload directory owned by uid {info.st_uid}: {path}")
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise UploadTargetError(f"failed to secure upload directory {path}: {exc}") from exc
    return path


def central_upload_target(auth_username: str, session: str, *, tmp_base: Path | None = None) -> tuple[Path, Path]:
    base = Path(tmp_base) if tmp_base is not None else UPLOAD_TMP_BASE
    username = upload_path_component(auth_username, "default")
    session_name = upload_path_component(session, "session")
    user_root = ensure_private_upload_dir(base / f"yolomux.{username}")
    uploads_root = ensure_private_upload_dir(user_root / "uploads")
    return ensure_private_upload_dir(uploads_root / session_name), user_root


def prune_expired_uploads(
    user_root: Path,
    retention_days: int,
    *,
    now: float | None = None,
    max_entries: int = UPLOAD_RETENTION_MAX_ENTRIES,
) -> dict[str, int]:
    uploads_root = user_root / "uploads"
    root_info = uploads_root.lstat()
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.geteuid():
        raise UploadTargetError(f"refusing unsafe upload retention root: {uploads_root}")
    cutoff = (time.time() if now is None else float(now)) - (max(1, int(retention_days)) * 24 * 60 * 60)
    scanned = 0
    removed = 0
    pending = [uploads_root]
    while pending and scanned < max_entries:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if scanned >= max_entries:
                        break
                    scanned += 1
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISLNK(info.st_mode):
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(Path(entry.path))
                        elif stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                            os.unlink(entry.path)
                            removed += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return {"scanned": scanned, "removed": removed}


class UploadRetentionSweeper:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._schedule: dict[Path, tuple[float, int]] = {}

    def maybe_prune(self, user_root: Path, retention_days: int, *, force: bool = False) -> dict[str, int]:
        now = self._clock()
        days = max(1, int(retention_days))
        with self._lock:
            next_sweep, previous_days = self._schedule.get(user_root, (0.0, days))
            if not force and previous_days == days and now < next_sweep:
                return {"scanned": 0, "removed": 0}
            self._schedule[user_root] = (now + UPLOAD_RETENTION_SWEEP_SECONDS, days)
        try:
            return prune_expired_uploads(user_root, days)
        except OSError:
            with self._lock:
                self._schedule.pop(user_root, None)
            raise


def sanitize_upload_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip()
    name = UPLOAD_SAFE_NAME_RE.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name or name in {".", ".."}:
        return "upload.bin"
    return name[:180]


def upload_template_parts(filename: str) -> tuple[str, str, int]:
    path = Path(filename)
    paste_match = PASTE_UPLOAD_NAME_RE.fullmatch(filename)
    if paste_match:
        return "", paste_match.group("suffix"), int(paste_match.group("index"))
    return path.stem or "upload", path.suffix, 1


def format_upload_template(template: str, original_name: str, suffix: str, sequence: int, now: datetime) -> str:
    source = str(template or DEFAULT_UPLOAD_FILENAME_TEMPLATE)
    if "{name}" in source and not original_name:
        source = source.replace("-{name}", "").replace("_{name}", "").replace(" {name}", "").replace("{name}", "")

    def replace_date(match: re.Match[str]) -> str:
        fmt = match.group("format") or "%Y%m%d"
        return now.strftime(fmt)

    def replace_seq(match: re.Match[str]) -> str:
        fmt = match.group("format") or "03d"
        try:
            return format(sequence, fmt)
        except ValueError:
            return f"{sequence:03d}"

    rendered = re.sub(r"\{date(?::(?P<format>[^}]+))?\}", replace_date, source)
    rendered = re.sub(r"\{seq(?::(?P<format>[^}]+))?\}", replace_seq, rendered)
    rendered = rendered.replace("{name}", original_name).replace("{ext}", suffix)
    return sanitize_upload_filename(rendered)


def unique_upload_path(target_dir: Path, filename: str, template: str | None = None) -> Path:
    if template is None or template == DEFAULT_UPLOAD_FILENAME_TEMPLATE:
        paste_path = unique_paste_upload_path(target_dir, filename)
        if paste_path is not None:
            return paste_path
    if template is None:
        path = target_dir / filename
        if not path.exists():
            return path
        stem = path.stem or "upload"
        suffix = path.suffix
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for index in range(1, 1000):
            candidate = target_dir / f"{stem}-{timestamp}-{index}{suffix}"
            if not candidate.exists():
                return candidate
        raise OSError(f"failed to choose unique upload name for {filename}")

    original_name, suffix, start_index = upload_template_parts(filename)
    now = datetime.now()
    for index in range(start_index, 1000):
        rendered = format_upload_template(template, original_name, suffix, index, now)
        candidate = target_dir / rendered
        if not candidate.exists():
            return candidate
    raise OSError(f"failed to choose unique upload name for {filename}")


def unique_paste_upload_path(target_dir: Path, filename: str) -> Path | None:
    match = PASTE_UPLOAD_NAME_RE.fullmatch(filename)
    if not match:
        return None
    date_text = match.group("date")
    suffix = match.group("suffix")
    start_index = int(match.group("index"))
    for index in range(start_index, 1000):
        candidate = target_dir / f"{date_text}-{index:03d}{suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"failed to choose unique paste upload name for {date_text}{suffix}")

def header_value_and_params(header_name: str, value: str) -> tuple[str, dict[str, str]]:
    message = Message()
    message[header_name] = value
    params = message.get_params(header=header_name) or []
    if not params:
        return "", {}
    primary = str(params[0][0]).lower()
    parsed_params: dict[str, str] = {}
    for key, param_value in params[1:]:
        if not key:
            continue
        if isinstance(param_value, tuple):
            parsed_params[str(key).lower()] = str(param_value[2] if len(param_value) >= 3 else "")
        else:
            parsed_params[str(key).lower()] = str(param_value)
    return primary, parsed_params

def parse_multipart_headers(header_block: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in header_block.decode("utf-8", errors="replace").split("\r\n"):
        name, separator, value = line.partition(":")
        if separator:
            headers[name.strip().lower()] = value.strip()
    return headers

def parse_multipart_upload(content_type: str, body: bytes, max_part_bytes: int = UPLOAD_MAX_BYTES) -> list[UploadedFile]:
    media_type, params = header_value_and_params("content-type", content_type)
    if media_type != "multipart/form-data":
        raise ValueError("expected multipart/form-data")
    boundary = params.get("boundary")
    if not boundary:
        raise ValueError("missing multipart boundary")

    boundary_bytes = f"--{boundary}".encode("utf-8")
    files: list[UploadedFile] = []
    for raw_part in body.split(boundary_bytes):
        if not raw_part or raw_part in {b"--", b"--\r\n"}:
            continue
        if raw_part.startswith(b"--"):
            continue
        part = raw_part[2:] if raw_part.startswith(b"\r\n") else raw_part
        if part.endswith(b"\r\n"):
            part = part[:-2]
        header_block, separator, content = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        headers = parse_multipart_headers(header_block)
        disposition, disposition_params = header_value_and_params(
            "content-disposition",
            headers.get("content-disposition", ""),
        )
        if disposition != "form-data":
            continue
        filename = disposition_params.get("filename") or disposition_params.get("filename*") or ""
        if not filename:
            continue
        if len(files) >= UPLOAD_MAX_FILES:
            raise ValueError(f"too many files; limit is {UPLOAD_MAX_FILES}")
        if len(content) > max_part_bytes:
            raise ValueError(f"file is too large; limit is {max_part_bytes} bytes")
        files.append(UploadedFile(filename=filename, content=content))
    return files
