#!/usr/bin/env python3
"""Capture and sanitize a tmux pane into a prompt-corpus fixture candidate."""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


TOKEN_RE = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9_-]{10,}"
    r"|ghp_[A-Za-z0-9_]{10,}"
    r"|github_pat_[A-Za-z0-9_]{10,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|lin_api_[A-Za-z0-9_]{10,}"
    r"|hf_[A-Za-z0-9_]{10,}"
    r")\b"
)


def parse_size(value: str) -> tuple[int, int]:
    match = re.match(r"^\s*(\d+)x(\d+)\s*$", str(value or ""))
    if not match:
        raise argparse.ArgumentTypeError("size must be COLSxROWS, for example 120x40")
    cols = int(match.group(1))
    rows = int(match.group(2))
    if cols < 40 or rows < 10:
        raise argparse.ArgumentTypeError("size must be at least 40x10")
    return cols, rows


def sanitize_text(text: str) -> str:
    home = os.path.expanduser("~")
    sanitized = text.replace(home, "~") if home else text
    sanitized = TOKEN_RE.sub("<redacted-token>", sanitized)
    sanitized = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "<redacted-email>", sanitized)
    return sanitized.rstrip() + "\n"


def command_version(command: str) -> str:
    executable = shutil.which(command)
    if not executable:
        return ""
    result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5, check=False)
    output = (result.stdout or result.stderr or "").strip().splitlines()
    return sanitize_text(output[0]).strip() if output else ""


def tmux_base_command(socket: str = "") -> list[str]:
    tmux = shutil.which("tmux")
    if not tmux:
        raise SystemExit("tmux is not installed")
    cmd = [tmux]
    if socket:
        cmd.extend(["-S", socket])
    return cmd


def run_tmux(socket: str, args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([*tmux_base_command(socket), *args], capture_output=True, text=True, timeout=timeout, check=False)
    return result


def capture_tmux(target: str, include_scrollback: bool, socket: str = "") -> str:
    cmd = ["capture-pane", "-p", "-J", "-t", target]
    if include_scrollback:
        cmd.extend(["-S", "-"])
    result = run_tmux(socket, cmd)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or "tmux capture-pane failed")
    return result.stdout


def wait_for_text(target: str, needles: list[str], timeout: float, include_scrollback: bool, socket: str = "") -> str:
    if not needles:
        return capture_tmux(target, include_scrollback, socket)
    deadline = datetime.datetime.now().timestamp() + timeout
    last = ""
    while datetime.datetime.now().timestamp() < deadline:
        last = capture_tmux(target, include_scrollback, socket)
        if all(needle in last for needle in needles):
            return last
        # This is the harness's polling loop; callers choose timeout and expected text.
        time.sleep(0.4)
    raise SystemExit(f"timed out waiting for {needles!r}; last capture:\n{sanitize_text(last)}")


def fixture_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
    return normalized.strip("._-")


def fixture_paths(output_dir: Path, base: str, sizes: list[tuple[int, int]]) -> list[tuple[tuple[int, int], Path, Path]]:
    multi = len(sizes) > 1
    paths = []
    for cols, rows in sizes:
        suffix = f".{cols}x{rows}" if multi else ""
        paths.append(((cols, rows), output_dir / f"{base}{suffix}.txt", output_dir / f"{base}{suffix}.capture.json"))
    return paths


def agent_command(agent: str) -> str:
    if agent == "claude":
        return "claude"
    if agent == "codex":
        return "codex"
    raise SystemExit("--launch-agent must be claude or codex")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", help="tmux target, for example session:0.0 or %%pane_id; required unless launching")
    parser.add_argument("--id", required=True, help="fixture id and base filename")
    parser.add_argument("--agent", choices=["claude", "codex", "unknown"], default="unknown")
    parser.add_argument("--launch-agent", choices=["claude", "codex"], help="launch a real agent command in a new tmux session")
    parser.add_argument("--launch-command", help="launch this exact shell command in a new tmux session")
    parser.add_argument("--session", help="tmux session name to create when launching; defaults to prompt-capture-<id>")
    parser.add_argument("--socket", default="", help="tmux socket path; useful for isolated reruns")
    parser.add_argument("--send-line", action="append", default=[], help="line to send with Enter after launch/attach; repeatable")
    parser.add_argument("--ready-text", action="append", default=[], help="wait for text before sending lines; repeatable")
    parser.add_argument("--wait-text", action="append", default=[], help="wait for text before capturing; repeatable")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for ready/wait text")
    parser.add_argument("--size", type=parse_size, action="append", default=[], help="capture size COLSxROWS; repeat for width variants")
    parser.add_argument("--kill-launched", action="store_true", help="kill the launched tmux session after capture")
    parser.add_argument("--expected-screen-key", choices=["approval", "needs-input", "working", "idle"], required=True)
    parser.add_argument("--mode", default="", help="agent permission/sandbox mode, if known")
    parser.add_argument("--width", type=int, default=0, help="terminal width, if known; ignored when --size is used")
    parser.add_argument("--source", default="real capture", help="fixture provenance label")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--include-scrollback", action="store_true", help="capture scrollback as well as the visible pane")
    parser.add_argument("--dry-run", action="store_true", help="print sanitized capture and metadata without writing files")
    return parser.parse_args()


def launched_target(args: argparse.Namespace, base: str, initial_size: tuple[int, int]) -> tuple[str, str]:
    launch_command = args.launch_command or (agent_command(args.launch_agent) if args.launch_agent else "")
    if not launch_command:
        if not args.target:
            raise SystemExit("--target is required unless --launch-agent or --launch-command is used")
        return args.target, ""
    session = args.session or f"prompt-capture-{base}"
    cols, rows = initial_size
    result = run_tmux(args.socket, ["new-session", "-d", "-s", session, "-x", str(cols), "-y", str(rows), launch_command])
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or "tmux new-session failed")
    return f"{session}:", session


def drive_prompt(args: argparse.Namespace, target: str) -> None:
    if args.ready_text:
        wait_for_text(target, args.ready_text, args.timeout, args.include_scrollback, args.socket)
    for line in args.send_line:
        result = run_tmux(args.socket, ["send-keys", "-t", target, line, "Enter"])
        if result.returncode != 0:
            raise SystemExit(result.stderr.strip() or result.stdout.strip() or "tmux send-keys failed")
    if args.wait_text:
        wait_for_text(target, args.wait_text, args.timeout, args.include_scrollback, args.socket)


def main() -> int:
    args = parse_args()
    base = fixture_id(args.id)
    if not base:
        raise SystemExit("--id must contain at least one filename-safe character")
    sizes = list(args.size) or [(args.width or 0, 0)]
    initial_size = next((size for size in sizes if size[0] and size[1]), (120, 40))
    target, launched_session = launched_target(args, base, initial_size)
    try:
        drive_prompt(args, target)
        outputs = []
        for (cols, rows), text_path, meta_path in fixture_paths(args.output_dir, base, sizes):
            if cols and rows:
                result = run_tmux(args.socket, ["resize-window", "-t", target, "-x", str(cols), "-y", str(rows)])
                if result.returncode != 0:
                    raise SystemExit(result.stderr.strip() or result.stdout.strip() or "tmux resize-window failed")
            captured = sanitize_text(capture_tmux(target, args.include_scrollback, args.socket))
            metadata = {
                "id": base,
                "agent": args.agent,
                "expected_screen_key": args.expected_screen_key,
                "source": args.source,
                "mode": args.mode,
                "width": cols or None,
                "height": rows or None,
                "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "target": target,
                "tmux_socket": args.socket,
                "launched_session": launched_session,
                "launch_agent": args.launch_agent or "",
                "launch_command": args.launch_command or (agent_command(args.launch_agent) if args.launch_agent else ""),
                "send_lines": list(args.send_line),
                "ready_text": list(args.ready_text),
                "wait_text": list(args.wait_text),
                "include_scrollback": bool(args.include_scrollback),
                "claude_version": command_version("claude") if args.agent in {"claude", "unknown"} else "",
                "codex_version": command_version("codex") if args.agent in {"codex", "unknown"} else "",
            }
            outputs.append((captured, metadata, text_path, meta_path))
        if args.dry_run:
            for captured, metadata, _text_path, _meta_path in outputs:
                print(captured, end="")
                print(json.dumps(metadata, indent=2, sort_keys=True), file=sys.stderr)
            return 0
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for captured, metadata, text_path, meta_path in outputs:
            text_path.write_text(captured, encoding="utf-8")
            meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"wrote {text_path}")
            print(f"wrote {meta_path}")
        return 0
    finally:
        if launched_session and args.kill_launched:
            run_tmux(args.socket, ["kill-session", "-t", launched_session])


if __name__ == "__main__":
    raise SystemExit(main())
