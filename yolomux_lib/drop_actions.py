# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Read-only server-side file drop actions."""

from __future__ import annotations

import csv
import json
import shutil
import statistics
import subprocess
from collections import Counter
from io import StringIO
from pathlib import Path
from typing import Any

from . import filesystem
from .locales import message_descriptor
from .locales import user_message_payload


MAX_ACTION_PATHS = 8
MAX_PREVIEW_LINES = 80
MAX_SCAN_LINES = 2_000
MAX_ERROR_LINES = 80
MAX_DATA_ROWS = 2_000
ERROR_TOKENS = ("error", "exception", "traceback", "failed", "fatal", "warn", "warning")


def _payload_paths(payload: dict[str, Any]) -> list[str]:
    raw_paths = payload.get("paths")
    if not isinstance(raw_paths, list):
        raw_paths = [payload.get("path", "")]
    paths: list[str] = []
    for raw in raw_paths:
        path = str(raw or "").strip()
        if path and path not in paths:
            paths.append(path)
        if len(paths) >= MAX_ACTION_PATHS:
            break
    return paths


def _message(key: str, **params: Any) -> dict[str, Any]:
    return message_descriptor(key, "", params)


def _raw(value: object) -> dict[str, str]:
    return {"raw": str(value or "")}


def _block(sections: list[list[dict[str, Any]]], *, path: str = "") -> dict[str, Any]:
    return {"path": path, "sections": sections}


def _result(
    action: str,
    title: str,
    body: str,
    paths: list[str],
    *,
    title_key: str,
    blocks: list[dict[str, Any]],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "ok": True,
        "action": action,
        "title": title,
        "body": body,
        "paths": paths,
        "result": {"title_key": title_key, "title_params": {}, "blocks": blocks},
        **extra,
    }


def _file_info(path: str) -> dict[str, Any]:
    info = filesystem.path_info(path)
    file_path = Path(str(info["path"]))
    result = {
        "path": str(info["path"]),
        "kind": str(info["kind"]),
        "size_bytes": None,
        "modified": None,
        "repo": str(info.get("repo_root") or ""),
        "relative": str(info.get("relative_path") or ""),
    }
    if file_path.exists() and file_path.is_file():
        stat = file_path.stat()
        result["size_bytes"] = stat.st_size
        result["modified"] = round(stat.st_mtime)
    return result


def _format_info(info: dict[str, Any]) -> str:
    lines = [f"path: {info['path']}", f"kind: {info['kind']}"]
    if info["size_bytes"] is not None:
        lines.append(f"size: {info['size_bytes']} bytes")
    if info["modified"] is not None:
        lines.append(f"modified: {info['modified']}")
    if info["repo"]:
        lines.append(f"repo: {info['repo']}")
    if info["relative"]:
        lines.append(f"relative: {info['relative']}")
    return "\n".join(lines)


def _info_messages(info: dict[str, Any]) -> list[dict[str, Any]]:
    messages = [
        _message("drop.result.info.path", path=info["path"]),
        _message("drop.result.info.kind", kind=info["kind"]),
    ]
    if info["size_bytes"] is not None:
        messages.append(_message("drop.result.info.size", size=info["size_bytes"]))
    if info["modified"] is not None:
        messages.append(_message("drop.result.info.modified", modified=info["modified"]))
    if info["repo"]:
        messages.append(_message("common.repoDetail", repo=info["repo"]))
    if info["relative"]:
        messages.append(_message("drop.result.info.relative", relative=info["relative"]))
    return messages


def _head_result(action: str, paths: list[str]) -> dict[str, Any]:
    blocks = []
    result_blocks = []
    for path in paths:
        payload = filesystem.read_file(path)
        lines = str(payload.get("content", "")).splitlines()
        preview = "\n".join(lines[:MAX_PREVIEW_LINES])
        truncated = len(lines) > MAX_PREVIEW_LINES
        suffix = f"\n... truncated after {MAX_PREVIEW_LINES} lines" if truncated else ""
        blocks.append(f"## {path}\n\n{preview}{suffix}")
        items = [_raw(preview)]
        if truncated:
            items.append(_message("drop.result.preview.truncated", count=MAX_PREVIEW_LINES))
        result_blocks.append(_block([items], path=path))
    return _result(
        action,
        "File preview",
        "\n\n".join(blocks),
        paths,
        title_key="drop.result.title.filePreview",
        blocks=result_blocks,
    )


def _info_result(action: str, paths: list[str]) -> dict[str, Any]:
    infos = [_file_info(path) for path in paths]
    return _result(
        action,
        "File information",
        "\n\n".join(_format_info(info) for info in infos),
        paths,
        title_key="drop.result.title.fileInformation",
        blocks=[_block([_info_messages(info)]) for info in infos],
    )


def _log_errors_result(action: str, paths: list[str]) -> dict[str, Any]:
    blocks = []
    result_blocks = []
    for path in paths:
        payload = filesystem.read_file(path)
        matches: list[dict[str, Any]] = []
        counts: Counter[str] = Counter()
        for number, line in enumerate(str(payload.get("content", "")).splitlines()[:MAX_SCAN_LINES], start=1):
            lower = line.lower()
            matched = [token for token in ERROR_TOKENS if token in lower]
            if not matched:
                continue
            for token in matched:
                counts[token] += 1
            if len(matches) < MAX_ERROR_LINES:
                matches.append({"number": number, "line": line})
        summary = ", ".join(f"{token}={count}" for token, count in counts.most_common()) or "none"
        body = (
            "\n".join(f"{match['number']}: {match['line']}" for match in matches)
            if matches
            else "No obvious error or warning lines found."
        )
        blocks.append(f"## {path}\n\nsummary: {summary}\n\n{body}")
        summary_item = (
            _message("drop.result.log.summary", summary=summary)
            if counts
            else _message("drop.result.log.summaryNone")
        )
        match_items = (
            [_raw(f"{match['number']}: {match['line']}") for match in matches]
            if matches
            else [_message("drop.result.log.noMatches")]
        )
        result_blocks.append(_block([[summary_item], match_items], path=path))
    return _result(
        action,
        "Log errors and warnings",
        "\n\n".join(blocks),
        paths,
        title_key="drop.result.title.logErrors",
        blocks=result_blocks,
    )


def _json_stats(content: str) -> tuple[str, list[list[dict[str, Any]]]]:
    data = json.loads(content)
    if isinstance(data, list):
        keys = Counter()
        for item in data[:MAX_DATA_ROWS]:
            if isinstance(item, dict):
                keys.update(item.keys())
        common = ", ".join(f"{key}({count})" for key, count in keys.most_common(20))
        body = f"type: JSON array\nitems: {len(data)}\nobject keys: {common or 'not object rows'}"
        messages = [
            _message("drop.result.data.typeJsonArray"),
            _message("drop.result.data.items", count=len(data)),
            _message("drop.result.data.objectKeys", keys=common) if common else _message("drop.result.data.objectKeysNone"),
        ]
        return body, [messages]
    if isinstance(data, dict):
        keys = ", ".join(map(str, list(data.keys())[:40]))
        return f"type: JSON object\nkeys: {keys}", [[
            _message("drop.result.data.typeJsonObject"),
            _message("drop.result.data.keys", keys=keys),
        ]]
    raw_value = repr(data)
    body = f"type: {type(data).__name__}\nvalue: {raw_value}"[:4_000]
    return body, [[
        _message("drop.result.data.typeValue", type=type(data).__name__),
        _message("drop.result.data.value", value=raw_value[:4_000]),
    ]]


def _csv_stats(content: str, delimiter: str) -> tuple[str, list[list[dict[str, Any]]]]:
    reader = csv.DictReader(StringIO(content), delimiter=delimiter)
    rows = []
    for row in reader:
        rows.append(row)
        if len(rows) >= MAX_DATA_ROWS:
            break
    fields = list(reader.fieldnames or [])
    lines = [f"rows scanned: {len(rows)}", f"columns: {', '.join(fields) if fields else 'none'}"]
    messages = [
        _message("drop.result.data.rowsScanned", count=len(rows)),
        _message("drop.result.data.columns", columns=", ".join(fields)) if fields else _message("drop.result.data.columnsNone"),
    ]
    for field in fields[:20]:
        values = [str(row.get(field, "") or "") for row in rows]
        missing = sum(1 for value in values if not value.strip())
        numeric = []
        for value in values:
            try:
                numeric.append(float(value))
            except ValueError:
                continue
        if numeric:
            avg = statistics.fmean(numeric)
            lines.append(
                f"{field}: numeric count={len(numeric)} min={min(numeric):.4g} "
                f"max={max(numeric):.4g} avg={avg:.4g} missing={missing}"
            )
            messages.append(_message(
                "drop.result.data.numericField",
                field=field,
                count=len(numeric),
                minimum=f"{min(numeric):.4g}",
                maximum=f"{max(numeric):.4g}",
                average=f"{avg:.4g}",
                missing=missing,
            ))
        else:
            distinct = len(set(value for value in values if value.strip()))
            lines.append(f"{field}: text distinct={distinct} missing={missing}")
            messages.append(_message("drop.result.data.textField", field=field, distinct=distinct, missing=missing))
    chart, chart_messages = _numeric_chart(rows, fields)
    sections = [messages]
    if chart:
        lines.append("\nchart:\n" + chart)
        sections.append([_message("drop.result.data.chart"), *chart_messages])
    return "\n".join(lines), sections


def _numeric_chart(rows: list[dict[str, str]], fields: list[str]) -> tuple[str, list[dict[str, Any]]]:
    for field in fields:
        values = []
        for row in rows:
            try:
                values.append(float(str(row.get(field, "") or "")))
            except ValueError:
                continue
        if len(values) < 2:
            continue
        lo = min(values)
        hi = max(values)
        if lo == hi:
            value = f"{lo:.4g}"
            return f"{field}: all values {value}", [_message("drop.result.data.chartConstant", field=field, value=value)]
        buckets = [0] * 8
        for value in values:
            index = min(7, int(((value - lo) / (hi - lo)) * 8))
            buckets[index] += 1
        max_bucket = max(buckets) or 1
        bars = ["#" * max(1, round(count / max_bucket * 20)) if count else "." for count in buckets]
        minimum = f"{lo:.4g}"
        maximum = f"{hi:.4g}"
        messages = [_message("drop.result.data.chartRange", field=field, minimum=minimum, maximum=maximum)]
        messages.extend(
            _message("drop.result.data.chartBucket", index=index + 1, bar=bar, count=count)
            for index, (bar, count) in enumerate(zip(bars, buckets))
        )
        text = f"{field} {minimum}..{maximum}\n" + "\n".join(f"{i + 1}: {bar} {count}" for i, (bar, count) in enumerate(zip(bars, buckets)))
        return text, messages
    return "", []


def _data_stats_result(action: str, paths: list[str]) -> dict[str, Any]:
    blocks = []
    result_blocks = []
    for path in paths:
        payload = filesystem.read_file(path)
        content = str(payload.get("content", ""))
        suffix = Path(path).suffix.lower()
        if suffix == ".json":
            stats, sections = _json_stats(content)
        elif suffix == ".ndjson":
            lines = [line for line in content.splitlines() if line.strip()]
            stats = f"type: NDJSON\nrecords: {len(lines)}"
            sections = [[
                _message("drop.result.data.typeNdjson"),
                _message("drop.result.data.records", count=len(lines)),
            ]]
        else:
            stats, sections = _csv_stats(content, "\t" if suffix == ".tsv" else ",")
        blocks.append(f"## {path}\n\n{stats}")
        result_blocks.append(_block(sections, path=path))
    return _result(
        action,
        "Data stats",
        "\n\n".join(blocks),
        paths,
        title_key="drop.result.title.dataStats",
        blocks=result_blocks,
    )


def _ocr_result(action: str, paths: list[str]) -> dict[str, Any]:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return _result(
            action,
            "OCR unavailable",
            "OCR needs the `tesseract` executable on the server PATH. No image text was extracted.",
            paths,
            title_key="drop.result.title.ocrUnavailable",
            blocks=[_block([[_message("drop.result.ocr.unavailable", executable="tesseract")]])],
            unavailable=True,
        )
    blocks = []
    result_blocks = []
    for path in paths:
        filesystem.read_raw(path)
        completed = subprocess.run(
            [tesseract, path, "stdout"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
        if completed.returncode == 0:
            raw_text = completed.stdout.strip()
            text = raw_text or "(no text detected)"
            item = _raw(raw_text[:12_000]) if raw_text else _message("drop.result.ocr.noText")
        else:
            raw_text = (completed.stderr or completed.stdout).strip()
            text = raw_text or "OCR failed"
            item = _raw(raw_text[:12_000]) if raw_text else _message("drop.result.ocr.failed")
        blocks.append(f"## {path}\n\n{text[:12_000]}")
        result_blocks.append(_block([[item]], path=path))
    return _result(
        action,
        "OCR result",
        "\n\n".join(blocks),
        paths,
        title_key="drop.result.title.ocr",
        blocks=result_blocks,
    )


def run_drop_action(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    action = str(payload.get("action") or payload.get("id") or "").strip()
    paths = _payload_paths(payload)
    if not action:
        diagnostic = "action is required"
        return {"ok": False, **user_message_payload("drop.error.actionRequired", diagnostic)}, 400
    if not paths:
        diagnostic = "path is required"
        return {"ok": False, **user_message_payload("drop.error.pathRequired", diagnostic)}, 400
    try:
        if action == "server-info":
            return _info_result(action, paths), 200
        if action == "server-head":
            return _head_result(action, paths), 200
        if action == "server-log-errors":
            return _log_errors_result(action, paths), 200
        if action == "server-data-stats":
            return _data_stats_result(action, paths), 200
        if action == "server-ocr":
            return _ocr_result(action, paths), 200
    except filesystem.FilesystemError as exc:
        diagnostic = str(exc)
        return {"ok": False, "action": action, "paths": paths, **user_message_payload("drop.error.failed", diagnostic, error=diagnostic)}, int(exc.status)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, csv.Error, ValueError) as exc:
        diagnostic = str(exc)
        return {"ok": False, "action": action, "paths": paths, **user_message_payload("drop.error.failed", diagnostic, error=diagnostic)}, 500
    diagnostic = f"unknown action: {action}"
    return {"ok": False, "action": action, "paths": paths, **user_message_payload("drop.error.unknownAction", diagnostic, action=action)}, 400
