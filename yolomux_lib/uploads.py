from __future__ import annotations

from .common import *


def sanitize_upload_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip()
    name = UPLOAD_SAFE_NAME_RE.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name or name in {".", ".."}:
        return "upload.bin"
    return name[:180]

def unique_upload_path(target_dir: Path, filename: str) -> Path:
    paste_path = unique_paste_upload_path(target_dir, filename)
    if paste_path is not None:
        return paste_path
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
        parsed_params[str(key).lower()] = str(param_value)
    return primary, parsed_params

def parse_multipart_headers(header_block: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in header_block.decode("utf-8", errors="replace").split("\r\n"):
        name, separator, value = line.partition(":")
        if separator:
            headers[name.strip().lower()] = value.strip()
    return headers

def parse_multipart_upload(content_type: str, body: bytes) -> list[UploadedFile]:
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
        files.append(UploadedFile(filename=filename, content=content))
        if len(files) > UPLOAD_MAX_FILES:
            raise ValueError(f"too many files; limit is {UPLOAD_MAX_FILES}")
    return files
