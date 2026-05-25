#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Browser wall for Dynamo tmux panes.

This is a small local dashboard for watching several long-running agent
sessions at once. It intentionally uses only Python stdlib so it can run from a
plain host checkout without installing a web framework.
"""

from __future__ import annotations

import argparse
import fnmatch
import html
import json
import os
import re
import subprocess
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


DEFAULT_SESSIONS = ("dynamo1", "dynamo2", "dynamo3", "dynamo4")
DEFAULT_SLOTS = 6
DEFAULT_LINES = 90
CONTAINER_HELPER = Path.home() / "utils" / "container" / "show_dynamo_containers.py"
AGENT_COMMANDS = {"claude", "codex"}


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


def run_cmd(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def tmux(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return run_cmd(["tmux", *args], timeout=timeout)


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
        err = (result.stderr or result.stdout or "tmux list-panes failed").strip()
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


def split_specs(specs: list[str]) -> list[str]:
    parts: list[str] = []
    for spec in specs:
        for item in spec.split(","):
            item = item.strip()
            if item:
                parts.append(item)
    return parts


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


def capture_pane(target: str, lines: int) -> tuple[str, str | None]:
    result = tmux(["capture-pane", "-t", target, "-p", "-S", f"-{lines}"], timeout=3.0)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "tmux capture-pane failed").strip()
        return "", err
    return result.stdout.rstrip("\n"), None


def parse_container_table(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return rows
    for line in lines[2:]:
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 8:
            continue
        repo, backend, user, container_id, git_head, dynamo_sha, host_path, branch = parts[:8]
        rows.append(
            {
                "repo": repo,
                "backend": backend,
                "user": user,
                "container_id": container_id,
                "git_head": git_head,
                "dynamo_sha": dynamo_sha,
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
        err = (result.stderr or result.stdout or "container helper failed").strip()
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
                text, error = capture_pane(target, capture_lines)
                slots.append(
                    {
                        "index": index,
                        "target": target,
                        "pane": asdict(pane) if pane else None,
                        "text": text,
                        "error": error,
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
        text, error = capture_pane(target, lines)
        return {
            "target": target,
            "pane": asdict(pane_by_target[target]) if target in pane_by_target else None,
            "lines": lines,
            "text": text,
            "error": error or tmux_error,
        }


def html_page() -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YOLOMux tmux wall</title>
<style>
:root {
  color-scheme: dark;
  --bg: #101317;
  --panel: #171b20;
  --panel2: #20262d;
  --text: #d7dde5;
  --muted: #8f9baa;
  --green: #4ade80;
  --red: #fb7185;
  --border: #313944;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--text);
  font: 13px/1.4 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.topbar {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  background: #0d1014;
}
.title { font-weight: 700; font-size: 16px; }
.sub { color: var(--muted); font-size: 12px; margin-top: 2px; }
.actions { display: flex; align-items: center; gap: 8px; }
button {
  background: var(--panel2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 10px;
  cursor: pointer;
}
button:hover { border-color: #556171; }
.status { color: var(--muted); min-width: 170px; text-align: right; }
.wrap { height: calc(100vh - 64px); padding: 10px; display: grid; grid-template-rows: minmax(0, 1fr) auto; gap: 8px; }
.grid {
  min-height: 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  grid-template-rows: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.pane {
  min-width: 0;
  min-height: 0;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  overflow: hidden;
}
.pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 9px;
  background: var(--panel2);
  border-bottom: 1px solid var(--border);
}
.pane-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 650;
}
.pane-meta {
  color: var(--muted);
  white-space: nowrap;
  font-size: 12px;
}
pre.term {
  margin: 0;
  min-height: 0;
  overflow: auto;
  padding: 9px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: 12px/1.25 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  color: #dce5ef;
}
.err { color: var(--red); }
.ok { color: var(--green); }
.containers {
  min-height: 84px;
  max-height: 132px;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel);
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
th, td {
  padding: 5px 8px;
  border-bottom: 1px solid #252c35;
  text-align: left;
  white-space: nowrap;
}
th { color: var(--muted); font-weight: 650; background: var(--panel2); position: sticky; top: 0; }
td.path { max-width: 360px; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 900px) {
  .grid { grid-template-columns: 1fr; grid-template-rows: repeat(6, minmax(260px, 1fr)); }
  .wrap { height: auto; min-height: calc(100vh - 64px); }
  .topbar { height: auto; align-items: flex-start; flex-direction: column; }
  .status { text-align: left; }
}
</style>
</head>
<body>
<header class="topbar">
  <div>
    <div class="title">YOLOMux tmux wall</div>
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
      <thead><tr><th>Repo</th><th>Backend</th><th>User</th><th>Container</th><th>Git HEAD</th><th>Dynamo SHA</th><th>Branch</th><th>Path</th></tr></thead>
      <tbody id="containers"></tbody>
    </table>
  </section>
</main>
<script>
const grid = document.getElementById('grid');
const statusEl = document.getElementById('status');
const containersEl = document.getElementById('containers');
let source = null;
let paused = false;
let lastPayload = null;

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function paneTitle(slot) {
  if (!slot.target) return 'empty';
  const p = slot.pane || {};
  const title = p.title ? ' - ' + p.title : '';
  return slot.target + title;
}

function paneMeta(slot) {
  const p = slot.pane || {};
  const bits = [];
  if (p.command) bits.push(p.command);
  if (p.current_path) bits.push(p.current_path.replace(/^\\/home\\/keivenc\\//, '~/'));
  return bits.join(' | ');
}

function render(payload) {
  lastPayload = payload;
  statusEl.textContent = payload.server_time || 'live';
  if (payload.tmux_error) statusEl.innerHTML = '<span class="err">' + esc(payload.tmux_error) + '</span>';
  grid.innerHTML = '';
  for (const slot of payload.slots || []) {
    const text = slot.error && !slot.text ? slot.error : slot.text;
    const div = document.createElement('article');
    div.className = 'pane';
    div.innerHTML = `
      <div class="pane-head">
        <div class="pane-title" title="${esc(paneTitle(slot))}">${esc(paneTitle(slot))}</div>
        <div class="pane-meta" title="${esc(paneMeta(slot))}">${esc(paneMeta(slot))}</div>
      </div>
      <pre class="term ${slot.error ? 'err' : ''}">${esc(text)}</pre>`;
    grid.appendChild(div);
  }

  containersEl.innerHTML = '';
  const containers = payload.containers || [];
  if (!containers.length) {
    const msg = payload.container_error || 'No running Dynamo containers found.';
    containersEl.innerHTML = '<tr><td colspan="8" class="err">' + esc(msg) + '</td></tr>';
  } else {
    for (const c of containers) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${esc(c.repo)}</td><td>${esc(c.backend)}</td><td>${esc(c.user)}</td><td>${esc(c.container_id)}</td><td>${esc(c.git_head)}</td><td>${esc(c.dynamo_sha)}</td><td>${esc(c.branch)}</td><td class="path" title="${esc(c.host_path)}">${esc(c.host_path)}</td>`;
      containersEl.appendChild(tr);
    }
  }
}

function connect() {
  if (source) source.close();
  source = new EventSource('/events');
  source.onopen = () => { statusEl.textContent = 'connected'; };
  source.onmessage = event => {
    if (!paused) render(JSON.parse(event.data));
  };
  source.onerror = () => {
    statusEl.innerHTML = '<span class="err">disconnected; retrying</span>';
  };
}

document.getElementById('pauseBtn').onclick = () => {
  paused = !paused;
  document.getElementById('pauseBtn').textContent = paused ? 'Resume' : 'Pause';
  if (!paused && lastPayload) render(lastPayload);
};
document.getElementById('refreshBtn').onclick = async () => {
  const r = await fetch('/api/snapshot');
  render(await r.json());
};
document.getElementById('summaryBtn').onclick = () => {
  window.open('/api/summary-input?lines=1200', '_blank');
};

connect();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server: "TmuxWallHTTPServer"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
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
    parser = argparse.ArgumentParser(description="Show Dynamo tmux panes in a browser.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--slots", type=int, default=DEFAULT_SLOTS)
    parser.add_argument("--lines", type=int, default=DEFAULT_LINES)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument(
        "--targets",
        nargs="*",
        default=[],
        help='tmux targets, comma-separated or separate args. Example: --targets "dynamo1:0.0,dynamo2:0.0"',
    )
    parser.add_argument("--print-targets", action="store_true")
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

    server = TmuxWallHTTPServer((args.host, args.port), app)
    url_host = "localhost" if args.host in {"0.0.0.0", "::"} else args.host
    print(f"Serving YOLOMux tmux wall on http://{url_host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
