#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Browser wall for Project tmux panes.

This is a small local dashboard for watching several long-running agent
sessions at once. It intentionally uses only Python stdlib so it can run from a
plain host checkout without installing a web framework.
"""

from __future__ import annotations

import argparse
import fnmatch
import ipaddress
import json
import re
import sys
import time
from dataclasses import asdict
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlparse

from yolomux_lib.agent_tui import capture_agent_pane
from yolomux_lib.agent_tui import classify_agent_pane
from yolomux_lib.common import split_csv
from yolomux_lib.tmux_utils import cmd_error
from yolomux_lib.tmux_utils import run_cmd
from yolomux_lib.tmux_utils import tmux
from yolomux_lib.tmux_utils import tmux_capture_pane
from yolomux_lib.tmux_utils import tmux_capture_pane_styled


DEFAULT_SESSIONS = ("project1", "project2", "project3", "project4")
DEFAULT_SLOTS = 6
DEFAULT_LINES = 90
CONTAINER_HELPER = Path.home() / "utils" / "container" / "show_project_containers.py"
AGENT_COMMANDS = {"claude", "codex"}
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_CONTENT_TYPES = {
    "tmux-wall.css": "text/css; charset=utf-8",
    "tmux-wall.js": "application/javascript; charset=utf-8",
}
UNAUTHENTICATED_REMOTE_BIND_ERROR = (
    "tmux_wall.py has no authentication. Bind to 127.0.0.1/localhost, or pass "
    "--allow-unauthenticated-non-loopback if you intentionally want to expose tmux snapshots."
)


@dataclass(frozen=True)
class PaneInfo:
    target: str
    session: str
    window: str
    pane: str
    current_path: str
    command: str
    active: bool
    title: str

    @property
    def is_agent(self) -> bool:
        title = self.title.lower()
        return self.command in AGENT_COMMANDS or "claude" in title or "codex" in title


def static_asset_path(asset: str) -> Path | None:
    if asset not in STATIC_CONTENT_TYPES:
        return None
    path = STATIC_DIR / asset
    return path if path.is_file() else None


def static_asset_version(asset: str) -> int:
    path = static_asset_path(asset)
    if path is None:
        return 0
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def is_loopback_bind_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def remote_bind_error(host: str, allow_unauthenticated_non_loopback: bool) -> str:
    if allow_unauthenticated_non_loopback or is_loopback_bind_host(host):
        return ""
    return UNAUTHENTICATED_REMOTE_BIND_ERROR


def list_panes() -> tuple[list[PaneInfo], str | None]:
    fmt = "\t".join(
        [
            "#{session_name}",
            "#{window_index}",
            "#{pane_index}",
            "#{pane_current_path}",
            "#{pane_current_command}",
            "#{pane_active}",
            "#{pane_title}",
        ]
    )
    result = tmux(["list-panes", "-a", "-F", fmt])
    if result.returncode != 0:
        err = cmd_error(result, "tmux list-panes failed")
        return [], err

    panes: list[PaneInfo] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        session, window, pane, current_path, command, active, title = parts
        panes.append(
            PaneInfo(
                target=f"{session}:{window}.{pane}",
                session=session,
                window=window,
                pane=pane,
                current_path=current_path,
                command=command,
                active=active == "1",
                title=title,
            )
        )
    return panes, None


# comma-flattening is common.split_csv (also used by auto_approve_tmux); was a third copy here.
split_specs = split_csv


def pane_sort_key(pane: PaneInfo) -> tuple[str, int, int]:
    return (pane.session, int(pane.window), int(pane.pane))


def resolve_targets(specs: list[str], panes: list[PaneInfo], slots: int) -> list[str]:
    parts = split_specs(specs)
    if not parts:
        return default_targets(panes, slots)

    by_target = {p.target: p for p in panes}
    by_session: dict[str, list[PaneInfo]] = {}
    for pane in panes:
        by_session.setdefault(pane.session, []).append(pane)

    targets: list[str] = []
    seen: set[str] = set()
    for part in parts:
        session_part = part.split(":", 1)[0]
        if any(ch in session_part for ch in "*?[]"):
            suffix = part[len(session_part) :]
            for pane in sorted(panes, key=pane_sort_key):
                if fnmatch.fnmatch(pane.session, session_part):
                    target = pane.session + suffix if suffix else pane.target
                    if target in by_target and target not in seen:
                        targets.append(target)
                        seen.add(target)
            continue

        if ":" in part:
            if part not in seen:
                targets.append(part)
                seen.add(part)
            continue

        session_panes = by_session.get(part, [])
        selected = preferred_pane(session_panes)
        target = selected.target if selected else part
        if target not in seen:
            targets.append(target)
            seen.add(target)

    return targets[:slots]


def preferred_pane(panes: list[PaneInfo]) -> PaneInfo | None:
    if not panes:
        return None
    ordered = sorted(panes, key=pane_sort_key)
    for pane in ordered:
        if pane.is_agent:
            return pane
    for pane in ordered:
        if pane.active:
            return pane
    return ordered[0]


def default_targets(panes: list[PaneInfo], slots: int) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    session_panes = {
        name: sorted([p for p in panes if p.session == name], key=pane_sort_key)
        for name in DEFAULT_SESSIONS
    }

    for name in DEFAULT_SESSIONS:
        pane = preferred_pane(session_panes.get(name, []))
        if pane and pane.target not in seen:
            selected.append(pane.target)
            seen.add(pane.target)

    extras = [
        pane
        for name in DEFAULT_SESSIONS
        for pane in session_panes.get(name, [])
        if pane.target not in seen
    ]
    for pane in sorted(extras, key=pane_sort_key):
        if len(selected) >= slots:
            break
        selected.append(pane.target)
        seen.add(pane.target)

    return selected[:slots]


def capture_pane_state(target: str, lines: int) -> tuple[str, str | None, dict[str, Any]]:
    def capture_func(pane_target: str, visible_only: bool = False) -> str | None:
        return tmux_capture_pane(pane_target, lines=lines, visible_only=visible_only, timeout=3.0)

    def capture_styled_func(pane_target: str, visible_only: bool = False) -> str | None:
        return tmux_capture_pane_styled(pane_target, lines=lines, visible_only=visible_only, timeout=3.0)

    capture = capture_agent_pane(
        target,
        visible_only=False,
        styled=False,
        include_cursor=False,
        capture_func=capture_func,
        capture_styled_func=capture_styled_func,
    )
    state = classify_agent_pane(
        target,
        session=target.split(":", 1)[0],
        prompt_source="pane",
        include_composer=False,
        include_cursor=False,
        include_transcript_activity=False,
        capture_func=capture_func,
        capture_styled_func=capture_styled_func,
    )
    state_payload = state.as_dict()
    text = capture.visible_text.rstrip("\n") if capture.ok else ""
    error = capture.error or None
    return text, error, state_payload


def capture_pane(target: str, lines: int) -> tuple[str, str | None]:
    text, error, _state = capture_pane_state(target, lines)
    return text, error


def parse_container_table(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return rows
    for line in lines[2:]:
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 8:
            continue
        repo, backend, user, container_id, git_head, project_sha, host_path, branch = parts[:8]
        rows.append(
            {
                "repo": repo,
                "backend": backend,
                "user": user,
                "container_id": container_id,
                "git_head": git_head,
                "project_sha": project_sha,
                "host_path": host_path,
                "branch": branch,
            }
        )
    return rows


def load_container_info() -> tuple[list[dict[str, str]], str | None]:
    if not CONTAINER_HELPER.exists():
        return [], f"missing container helper: {CONTAINER_HELPER}"
    result = run_cmd(["python3", str(CONTAINER_HELPER)], timeout=8.0)
    if result.returncode != 0:
        err = cmd_error(result, "container helper failed")
        return [], err
    return parse_container_table(result.stdout), None


class TmuxWallApp:
    def __init__(self, targets: list[str], slots: int, lines: int, interval: float):
        self.requested_targets = targets
        self.slots = slots
        self.lines = lines
        self.interval = interval

    def discover(self) -> dict[str, Any]:
        panes, tmux_error = list_panes()
        targets = resolve_targets(self.requested_targets, panes, self.slots)
        pane_by_target = {pane.target: pane for pane in panes}
        containers, container_error = load_container_info()
        return {
            "panes": panes,
            "targets": targets,
            "pane_by_target": pane_by_target,
            "tmux_error": tmux_error,
            "containers": containers,
            "container_error": container_error,
        }

    def snapshot(self, lines: int | None = None) -> dict[str, Any]:
        discovered = self.discover()
        capture_lines = lines if lines is not None else self.lines
        slots: list[dict[str, Any]] = []
        for index in range(self.slots):
            if index < len(discovered["targets"]):
                target = discovered["targets"][index]
                pane = discovered["pane_by_target"].get(target)
                text, error, state = capture_pane_state(target, capture_lines)
                slots.append(
                    {
                        "index": index,
                        "target": target,
                        "pane": asdict(pane) if pane else None,
                        "text": text,
                        "error": error,
                        "state": state,
                        "screen": state.get("screen") or {},
                        "display": state.get("display") or {},
                        "approval": state.get("approval") or {},
                        "attention_kind": state.get("attention_kind") or "",
                        "attention_label": state.get("attention_label") or "",
                        "agent_kind": state.get("agent_kind") or "",
                        "reason_code": state.get("reason_code") or "",
                    }
                )
            else:
                slots.append(
                    {
                        "index": index,
                        "target": "",
                        "pane": None,
                        "text": "",
                        "error": "empty slot",
                    }
                )
        return {
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "slots": slots,
            "containers": discovered["containers"],
            "container_error": discovered["container_error"],
            "tmux_error": discovered["tmux_error"],
            "interval": self.interval,
        }

    def transcript(self, target: str, lines: int) -> dict[str, Any]:
        panes, tmux_error = list_panes()
        pane_by_target = {pane.target: pane for pane in panes}
        text, error, state = capture_pane_state(target, lines)
        return {
            "target": target,
            "pane": asdict(pane_by_target[target]) if target in pane_by_target else None,
            "lines": lines,
            "text": text,
            "error": error or tmux_error,
            "state": state,
            "screen": state.get("screen") or {},
            "display": state.get("display") or {},
            "approval": state.get("approval") or {},
            "attention_kind": state.get("attention_kind") or "",
            "attention_label": state.get("attention_label") or "",
            "agent_kind": state.get("agent_kind") or "",
            "reason_code": state.get("reason_code") or "",
        }


def html_page() -> str:
    css_version = static_asset_version("tmux-wall.css")
    js_version = static_asset_version("tmux-wall.js")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YOLOmux tmux wall</title>
<link rel="stylesheet" href="/static/tmux-wall.css?v={css_version}">
</head>
<body>
<header class="topbar">
  <div>
    <div class="title">YOLOmux tmux wall</div>
    <div class="sub">Six live tmux snapshots, container metadata, and AI-readable transcript endpoints.</div>
  </div>
  <div class="actions">
    <button id="pauseBtn">Pause</button>
    <button id="refreshBtn">Refresh</button>
    <button id="summaryBtn">Open summary JSON</button>
    <div id="status" class="status">connecting...</div>
  </div>
</header>
<main class="wrap">
  <section id="grid" class="grid"></section>
  <section class="containers">
    <table>
      <thead><tr><th>Repo</th><th>Backend</th><th>User</th><th>Container</th><th>Git HEAD</th><th>Project SHA</th><th>Branch</th><th>Path</th></tr></thead>
      <tbody id="containers"></tbody>
    </table>
  </section>
</main>
<script src="/static/tmux-wall.js?v={js_version}"></script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server: "TmuxWallHTTPServer"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/static/"):
            asset = parsed.path.removeprefix("/static/")
            content_type = STATIC_CONTENT_TYPES.get(asset)
            if content_type:
                self.write_static_asset(asset, content_type)
                return
        if parsed.path == "/":
            self.write_html(html_page())
            return
        if parsed.path == "/api/snapshot":
            self.write_json(self.server.app.snapshot())
            return
        if parsed.path == "/api/transcript":
            qs = parse_qs(parsed.query)
            target = qs.get("target", [""])[0]
            lines = int(qs.get("lines", ["2000"])[0])
            if not target:
                self.write_json({"error": "missing target"}, status=HTTPStatus.BAD_REQUEST)
                return
            self.write_json(self.server.app.transcript(target, max(1, min(lines, 20000))))
            return
        if parsed.path == "/api/summary-input":
            qs = parse_qs(parsed.query)
            lines = int(qs.get("lines", ["1200"])[0])
            self.write_json(self.server.app.snapshot(lines=max(1, min(lines, 20000))))
            return
        if parsed.path == "/events":
            self.stream_events()
            return
        self.write_text("not found\n", status=HTTPStatus.NOT_FOUND)

    def write_html(self, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_text(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_static_asset(self, asset: str, content_type: str) -> None:
        path = static_asset_path(asset)
        if path is None:
            self.write_text(f"missing static asset: {asset}\n", status=HTTPStatus.NOT_FOUND)
            return
        try:
            data = path.read_bytes()
        except OSError as exc:
            self.write_text(f"failed to read static asset: {exc}\n", status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def stream_events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        while True:
            payload = self.server.app.snapshot()
            data = json.dumps(payload, ensure_ascii=False)
            try:
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            time.sleep(self.server.app.interval)


class TmuxWallHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], app: TmuxWallApp):
        super().__init__(server_address, Handler)
        self.app = app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show Project tmux panes in a browser.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--slots", type=int, default=DEFAULT_SLOTS)
    parser.add_argument("--lines", type=int, default=DEFAULT_LINES)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument(
        "--targets",
        nargs="*",
        default=[],
        help='tmux targets, comma-separated or separate args. Example: --targets "project1:0.0,project2:0.0"',
    )
    parser.add_argument("--print-targets", action="store_true")
    parser.add_argument(
        "--allow-unauthenticated-non-loopback",
        action="store_true",
        help="Allow binding this unauthenticated wall server to a non-loopback address.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    slots = max(1, args.slots)
    lines = max(1, args.lines)
    interval = max(0.2, args.interval)
    app = TmuxWallApp(args.targets, slots=slots, lines=lines, interval=interval)

    if args.print_targets:
        discovered = app.discover()
        if discovered["tmux_error"]:
            print(discovered["tmux_error"], file=sys.stderr)
            return 1
        for target in discovered["targets"]:
            pane = discovered["pane_by_target"].get(target)
            label = f"{pane.command} {pane.current_path}" if pane else ""
            print(f"{target}\t{label}")
        return 0

    bind_error = remote_bind_error(args.host, args.allow_unauthenticated_non_loopback)
    if bind_error:
        print(bind_error, file=sys.stderr)
        return 2

    server = TmuxWallHTTPServer((args.host, args.port), app)
    url_host = "localhost" if args.host in {"0.0.0.0", "::"} else args.host
    print(f"Serving YOLOmux tmux wall on http://{url_host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
