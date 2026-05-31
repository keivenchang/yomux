#!/usr/bin/env python3
"""PROTOTYPE — which files did each AI session change, mapped to live tmux sessions.

Inspiration for a future YOLOmux feature (e.g. a `/api/session-files` endpoint +
a per-pane "files changed" view). YOLOmux already knows each tmux session's cwd
and agent, so the incorporation is: match a tracked tmux session to the freshest
transcript/rollout for that agent+cwd, then list the files it touched.

Sources:
  Claude Code : ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
                -> assistant tool_use Edit/Write/MultiEdit/NotebookEdit .input.file_path
  Codex       : ~/.codex/sessions/YYYY/MM/DD/rollout-*-<id>.jsonl
                -> session_meta.payload.{id,cwd} + apply_patch "*** Add|Update|Delete File: <path>"

Change types: A=add, M=modify, D=delete (best-effort; see classify()).

Usage:
  ai_session_files.py [--hours N] [--cwd SUBSTR] [--by-path] [--no-tmp] [--json]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import time
from pathlib import Path

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude" / "projects"
CODEX_DIR = HOME / ".codex" / "sessions"
CLAUDE_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
CODEX_PATCH_RE = re.compile(r"\*\*\* (Add|Update|Delete) File: ([^\"\\\n]+)")
AGENT_COMMANDS = {"claude", "codex"}


def classify(types: set[str]) -> str:
    """Collapse the change markers seen for one path into one label."""
    if "A" in types:
        return "A"
    if "D" in types:
        return "D"
    return "M"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def scan_claude(cutoff: float) -> list[dict]:
    sessions = []
    for f in glob.glob(str(CLAUDE_DIR / "*" / "*.jsonl")):
        p = Path(f)
        if _mtime(p) < cutoff:
            continue
        cwd = ""
        changes: dict[str, set[str]] = {}
        with p.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not cwd and isinstance(obj.get("cwd"), str):
                    cwd = obj["cwd"]  # first cwd = launch dir
                if obj.get("type") != "assistant":
                    continue
                for item in (obj.get("message", {}) or {}).get("content", []) or []:
                    if not isinstance(item, dict) or item.get("type") != "tool_use":
                        continue
                    if item.get("name") not in CLAUDE_EDIT_TOOLS:
                        continue
                    fp = (item.get("input") or {}).get("file_path")
                    if not fp:
                        continue
                    kind = "A" if item["name"] == "Write" else "M"  # best-effort
                    changes.setdefault(fp, set()).add(kind)
        if changes:
            sessions.append({"agent": "claude", "id": p.stem, "cwd": cwd,
                             "mtime": _mtime(p), "changes": changes})
    return sessions


def scan_codex(cutoff: float) -> list[dict]:
    sessions = []
    for f in glob.glob(str(CODEX_DIR / "*" / "*" / "*" / "rollout-*.jsonl")):
        p = Path(f)
        if _mtime(p) < cutoff:
            continue
        sid, cwd = "", ""
        changes: dict[str, set[str]] = {}
        with p.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"session_meta"' in line and not sid:
                    try:
                        meta = json.loads(line).get("payload", {})
                        sid, cwd = meta.get("id", ""), meta.get("cwd", "")
                    except ValueError:
                        pass
                for verb, path in CODEX_PATCH_RE.findall(line):
                    kind = {"Add": "A", "Update": "M", "Delete": "D"}[verb]
                    changes.setdefault(path.strip(), set()).add(kind)
        if changes:
            sessions.append({"agent": "codex", "id": sid or p.stem, "cwd": cwd,
                             "mtime": _mtime(p), "changes": changes})
    return sessions


def live_tmux_panes() -> list[dict]:
    """Live agent panes: [{session, cmd, path}]."""
    try:
        out = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}\t#{pane_current_command}\t#{pane_current_path}"],
            capture_output=True, text=True, timeout=5).stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return []
    panes = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[1] in AGENT_COMMANDS:
            panes.append({"session": parts[0], "cmd": parts[1], "path": parts[2]})
    return panes


def match_tmux(session: dict, panes: list[dict]) -> str | None:
    """Map an AI session to a live tmux session by agent + cwd (prefix either way)."""
    cwd = session["cwd"]
    for pane in panes:
        if pane["cmd"] != session["agent"]:
            continue
        if cwd and (cwd == pane["path"] or cwd.startswith(pane["path"])
                    or pane["path"].startswith(cwd)):
            return pane["session"]
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--cwd", default="", help="filter sessions whose launch cwd contains this")
    ap.add_argument("--by-path", default="", help="filter to sessions that touched a path containing this")
    ap.add_argument("--no-tmp", action="store_true", help="hide /tmp scratch files")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cutoff = time.time() - args.hours * 3600
    panes = live_tmux_panes()
    sessions = scan_claude(cutoff) + scan_codex(cutoff)

    rows = []
    for s in sessions:
        if args.cwd and args.cwd not in s["cwd"]:
            continue
        files = {fp: classify(t) for fp, t in s["changes"].items()
                 if not (args.no_tmp and fp.startswith("/tmp"))}
        if args.by_path:
            files = {fp: k for fp, k in files.items() if args.by_path in fp}
        if not files:
            continue
        rows.append({"agent": s["agent"], "id": s["id"], "cwd": s["cwd"],
                     "tmux": match_tmux(s, panes),
                     "last_activity": time.strftime("%m-%d %H:%M", time.localtime(s["mtime"])),
                     "files": dict(sorted(files.items()))})

    rows.sort(key=lambda r: (r["tmux"] or "~", r["last_activity"]))

    if args.json:
        print(json.dumps(rows, indent=2))
        return

    if not rows:
        print("(no AI file changes in window)")
        return
    for r in rows:
        tmux = f"tmux:{r['tmux']}" if r["tmux"] else "(no live tmux pane)"
        print(f"\n== {tmux}  {r['agent']}  {r['cwd']}  [{r['id'][:8]}]  ({r['last_activity']}) "
              f"— {len(r['files'])} files")
        for fp, kind in r["files"].items():
            print(f"   {kind}  {fp}")


if __name__ == "__main__":
    main()
