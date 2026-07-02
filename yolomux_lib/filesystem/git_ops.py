"""Git-backed filesystem operations."""

from __future__ import annotations

import contextlib
import re
import time
from pathlib import Path
from typing import Any

from ..common import git
from ..common import git_bytes
from ..tmux_utils import cmd_error
from . import paths


def git_repo_info(repo: Path, include_status: bool = True) -> dict[str, Any]:
    branch = git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=str(repo), timeout=1.0)
    if branch.returncode != 0:
        branch = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo), timeout=1.0)
    upstream = git(["rev-parse", "--abbrev-ref", "@{upstream}"], cwd=str(repo), timeout=1.0)
    ahead = 0
    behind = 0
    if upstream.returncode == 0:
        counts = git(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], cwd=str(repo), timeout=2.0)
        if counts.returncode == 0:
            parts = counts.stdout.split()
            if len(parts) >= 2:
                try:
                    ahead = int(parts[0])
                    behind = int(parts[1])
                except ValueError:
                    ahead = 0
                    behind = 0
    dirty_count: int | None = None
    if include_status:
        status = git(["status", "--porcelain=v1"], cwd=str(repo), timeout=2.0)
        dirty_count = len(status.stdout.splitlines()) if status.returncode == 0 else None
    return {
        "root": str(repo),
        "name": repo.name,
        "branch": branch.stdout.strip() if branch.returncode == 0 else "",
        "dirty_count": dirty_count,
        "upstream": upstream.stdout.strip() if upstream.returncode == 0 else "",
        "ahead": ahead,
        "behind": behind,
    }


def git_tracks_path(path: Path) -> bool:
    """True when `path` is a file tracked by git (committed or staged)."""
    if path.is_dir():
        return False
    # ls-files pathspec is resolved relative to cwd (the file's parent), so `name`
    # is enough; returncode is non-zero both when untracked AND when not in a repo.
    result = git(["ls-files", "--error-unmatch", "--", path.name], cwd=str(path.parent), timeout=1.5)
    return result.returncode == 0


def git_file_history(path: Path, limit: int = 60) -> list[dict[str, Any]]:
    if path.is_dir():
        return []
    repo_root = git_root_for_path(path)
    if not repo_root:
        return []
    repo = Path(repo_root)
    try:
        rel_path = path.relative_to(repo).as_posix()
    except ValueError:
        return []
    result = git([
        "log",
        "--follow",
        f"--max-count={max(1, min(int(limit), 100))}",
        "--format=%H%x1f%h%x1f%s%x1f%ct%x1f%an",
        "--",
        rel_path,
    ], cwd=str(repo), timeout=3.0)
    if result.returncode != 0:
        return []
    history: list[dict[str, Any]] = []
    for line in (result.stdout or "").splitlines():
        full, short, subject, date, author = (line.split("\x1f") + ["", "", "", "", ""])[:5]
        if not full:
            continue
        try:
            date_value = int(date)
        except ValueError:
            date_value = 0
        history.append({
            "ref": full,
            "short": short or full[:9],
            "subject": subject,
            "date": date_value,
            "author": author,
        })
    return history


def _git_mv_if_tracked(src: Path, dst: Path) -> bool:
    """Move a git-tracked file with `git mv`; callers fall back to plain rename on False."""
    repo_root = git_root_for_path(src)
    if not repo_root:
        return False
    repo = Path(repo_root)
    try:
        rel_src = src.relative_to(repo).as_posix()
        rel_dst = dst.relative_to(repo).as_posix()
    except ValueError:
        return False
    tracked = git(["ls-files", "--error-unmatch", "--", rel_src], cwd=str(repo), timeout=2.0)
    if tracked.returncode != 0:
        return False
    return git(["mv", "--", rel_src, rel_dst], cwd=str(repo), timeout=5.0).returncode == 0


def _git_blob_text(repo: Path, ref: str, rel_path: str, label: str) -> tuple[str, str]:
    result = git_bytes(["show", f"{ref}:{rel_path}"], cwd=str(repo), timeout=5.0)
    if result.returncode != 0:
        return "", ""
    if len(result.stdout) > paths.MAX_READ_BYTES:
        raise paths.FilesystemError(
            f"{label} too large (max {paths.MAX_READ_BYTES})",
            status=413,
            message_key="fs.error.gitBlobTooLarge",
            message_params={"label": label, "max": paths.MAX_READ_BYTES},
        )
    if paths._looks_binary(result.stdout):
        return "", f"{label} file appears to be binary"
    return result.stdout.decode("utf-8", errors="replace"), ""


def _normal_ref(value: str | None, default: str) -> str:
    ref = str(value or "").strip()
    return ref or default


def _diff_refs(raw_from_ref: str | None, raw_to_ref: str | None) -> tuple[str, str]:
    return _normal_ref(raw_from_ref, "HEAD"), _normal_ref(raw_to_ref, "current")


def _refs_requested(from_ref: str | None, to_ref: str | None) -> bool:
    return bool((from_ref or "").strip() or (to_ref or "").strip())


def _diff_ref_resolution_error(error: Exception) -> bool:
    return isinstance(error, paths.FilesystemError) and error.message_key in {
        "fs.error.unknownFromRef",
        "fs.error.unknownToRef",
        "fs.error.refOrderCurrent",
        "fs.error.refOrder",
    }


def _ref_exists(repo: Path, ref: str) -> bool:
    result = git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=str(repo), timeout=3.0)
    return result.returncode == 0


def _ensure_ref_order(repo: Path, from_ref: str, to_ref: str) -> None:
    if to_ref == "current":
        if from_ref == "current":
            raise paths.FilesystemError(
                "FROM ref must be older than TO ref (current is the working tree)",
                message_key="fs.error.refOrderCurrent",
            )
        if not _ref_exists(repo, from_ref):
            raise paths.FilesystemError(
                f"unknown FROM ref: {from_ref}",
                message_key="fs.error.unknownFromRef",
                message_params={"ref": from_ref},
            )
        return
    if from_ref == "current":
        raise paths.FilesystemError(
            "FROM ref must be older than TO ref (current is the working tree)",
            message_key="fs.error.refOrderCurrent",
        )
    if not _ref_exists(repo, from_ref):
        raise paths.FilesystemError(
            f"unknown FROM ref: {from_ref}",
            message_key="fs.error.unknownFromRef",
            message_params={"ref": from_ref},
        )
    if not _ref_exists(repo, to_ref):
        raise paths.FilesystemError(
            f"unknown TO ref: {to_ref}",
            message_key="fs.error.unknownToRef",
            message_params={"ref": to_ref},
        )
    order = git(["merge-base", "--is-ancestor", from_ref, to_ref], cwd=str(repo), timeout=5.0)
    if order.returncode != 0:
        raise paths.FilesystemError(
            f"FROM ref must be older than TO ref ({from_ref} is not an ancestor of {to_ref})",
            message_key="fs.error.refOrder",
            message_params={"fromRef": from_ref, "toRef": to_ref},
        )


def diff_file(raw_path: str, from_ref: str | None = None, to_ref: str | None = None) -> dict[str, Any]:
    path = paths._validated_path(raw_path)
    repo_root = git_root_for_path(path)
    if not repo_root:
        raise paths.FilesystemError(
            f"not in a git repo: {path}",
            message_key="fs.error.notGitRepo",
            message_params={"path": str(path)},
        )
    repo = Path(repo_root)
    try:
        rel_path = path.relative_to(repo).as_posix()
    except ValueError as exc:
        raise paths.FilesystemError.outside_repo(path) from exc
    tracked = git(["ls-files", "--error-unmatch", "--", rel_path], cwd=str(repo), timeout=3.0)
    diff_from, diff_to = _diff_refs(from_ref, to_ref)
    if not (diff_to == "current" and tracked.returncode != 0):
        try:
            _ensure_ref_order(repo, diff_from, diff_to)
        except paths.FilesystemError as error:
            if not (_refs_requested(from_ref, to_ref) and _diff_ref_resolution_error(error)):
                raise
            diff_from, diff_to = _diff_refs(None, None)
            _ensure_ref_order(repo, diff_from, diff_to)
    original = ""
    original_error = ""
    working = ""
    working_error = ""
    if diff_to == "current" and tracked.returncode != 0:
        result = git(["diff", "--no-index", "--", "/dev/null", str(path)], cwd=str(repo), timeout=5.0)
        untracked = True
    else:
        if diff_to == "current":
            result = git(["diff", diff_from, "--", rel_path], cwd=str(repo), timeout=5.0)
            if tracked.returncode == 0:
                original, original_error = _git_blob_text(repo, diff_from, rel_path, "original")
        else:
            result = git(["diff", diff_from, diff_to, "--", rel_path], cwd=str(repo), timeout=5.0)
            original, original_error = _git_blob_text(repo, diff_from, rel_path, "original")
            working, working_error = _git_blob_text(repo, diff_to, rel_path, "working")
        untracked = False
    if result.returncode not in {0, 1}:
        message = cmd_error(result, "git diff failed")
        raise paths.FilesystemError(
            "git diff failed",
            status=500,
            message_key="fs.error.gitDiffFailed",
            diagnostic=message,
        )
    diff = result.stdout or ""
    if len(diff.encode("utf-8", errors="replace")) > paths.MAX_READ_BYTES:
        raise paths.FilesystemError(
            f"diff too large (max {paths.MAX_READ_BYTES})",
            status=413,
            message_key="fs.error.diffTooLarge",
            message_params={"max": paths.MAX_READ_BYTES},
        )
    return {
        "path": str(path),
        "repo": str(repo),
        "relative_path": rel_path,
        "diff": diff,
        "original": original,
        "original_error": original_error,
        "working": working,
        "working_error": working_error,
        "working_missing": not path.exists(),
        "from_ref": diff_from,
        "to_ref": diff_to,
        "untracked": untracked,
    }


# Inline git blame for the editor. PR number is extracted from the commit summary the same
# way the metadata code does (`(#1234)`). Cached per (path, HEAD sha, file mtime, ref) because blame is
# expensive and only changes when the file or HEAD moves.
_BLAME_PR_RE = re.compile(r"\(#(\d+)\)")
_BLAME_SHA_RE = re.compile(r"[0-9a-f]{40}")
_blame_cache: dict[tuple[str, str, int, str], dict[str, Any]] = {}


def _parse_blame_porcelain(text: str) -> dict[str, dict[str, Any]]:
    """Parse `git blame --line-porcelain` into per-line metadata."""
    lines: dict[str, dict[str, Any]] = {}
    meta: dict[str, dict[str, Any]] = {}
    cur_sha = ""
    final_line: int | None = None
    for raw in text.split("\n"):
        if not raw:
            continue
        if raw[0] == "\t":
            if final_line is not None:
                info = meta.get(cur_sha, {})
                uncommitted = cur_sha == "0" * 40
                summary = info.get("summary", "")
                pr = _BLAME_PR_RE.search(summary)
                lines[str(final_line)] = {
                    "sha": cur_sha,
                    "author": "You" if uncommitted else info.get("author", ""),
                    "time": int(time.time()) if uncommitted else info.get("author_time", 0),
                    "summary": "Uncommitted changes" if uncommitted else summary,
                    "pr": int(pr.group(1)) if pr else None,
                }
            continue
        parts = raw.split(" ", 3)
        if parts and _BLAME_SHA_RE.fullmatch(parts[0]) and len(parts) >= 3:
            cur_sha = parts[0]
            final_line = int(parts[2])
            meta.setdefault(cur_sha, {})
        elif raw.startswith("author "):
            meta.setdefault(cur_sha, {})["author"] = raw[len("author "):]
        elif raw.startswith("author-time "):
            with contextlib.suppress(ValueError):
                meta.setdefault(cur_sha, {})["author_time"] = int(raw[len("author-time "):])
        elif raw.startswith("summary "):
            meta.setdefault(cur_sha, {})["summary"] = raw[len("summary "):]
    return lines


def blame_file(raw_path: str, ref: str | None = None) -> dict[str, Any]:
    path = paths._validated_path(raw_path)
    repo_root = git_root_for_path(path)
    if not repo_root:
        return {"path": str(path), "repo": "", "relative_path": "", "in_repo": False, "lines": {}}
    repo = Path(repo_root)
    try:
        rel_path = path.relative_to(repo).as_posix()
    except ValueError as exc:
        raise paths.FilesystemError.outside_repo(path) from exc
    head = git(["rev-parse", "HEAD"], cwd=str(repo), timeout=1.0)
    head_sha = (head.stdout or "").strip() if head.returncode == 0 else ""
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    use_ref = ref if (ref and ref not in {"current", "working", "HEAD", ""}) else ""
    cache_key = (str(path), head_sha, mtime_ns, use_ref)
    cached = _blame_cache.get(cache_key)
    if cached is not None:
        return cached
    args = ["blame", "--line-porcelain"]
    if use_ref:
        args.append(use_ref)
    args += ["--", rel_path]
    result = git(args, cwd=str(repo), timeout=3.0)
    if result.returncode != 0:
        return {
            "path": str(path),
            "repo": str(repo),
            "relative_path": rel_path,
            "in_repo": True,
            "lines": {},
            "error": (result.stderr or "not committed yet").strip(),
        }
    payload = {
        "path": str(path),
        "repo": str(repo),
        "relative_path": rel_path,
        "head": head_sha,
        "in_repo": True,
        "lines": _parse_blame_porcelain(result.stdout or ""),
    }
    if len(_blame_cache) > 64:
        _blame_cache.clear()
    _blame_cache[cache_key] = payload
    return payload


def git_root_for_path(path: Path) -> str:
    cwd = path if path.is_dir() else path.parent
    result = git(["rev-parse", "--show-toplevel"], cwd=str(cwd), timeout=1.0)
    if result.returncode != 0:
        return ""
    root = result.stdout.strip()
    return root if root.startswith("/") else ""
