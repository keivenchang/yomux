# AGENTS.md — YOLOmux developer / AI agent guide

This file documents conventions, architecture, and API surface for people (and AI agents) writing code in this repo. End-user / operator docs live in `README.md`.

## Project conventions

These rules are **specific to yolomux** — they do not apply to other projects.

### Version bump per commit

`YOLOMUX_VERSION` in `yolomux_lib/common.py` must be bumped on every commit. The patch segment increments monotonically:

- `0.1` → `0.1.1` → `0.1.2` → `0.1.3` → …
- If the current value has no patch segment (just `0.1`), the next commit appends one (`0.1.1`).
- Include the version-bump line in the same commit as the actual code change. Do **not** make a standalone "bump version" commit.
- Surface the new version in the commit message body (e.g. `Version: 0.1.N`).

To find the latest committed version: `git show HEAD:yolomux_lib/common.py | grep YOLOMUX_VERSION` and add 1 to the patch segment.

### Timing constants — split rule

JavaScript and CSS timing constants in `static/yolomux.js` and `static/yolomux.css` are split by purpose:

- **UI / popup / display / animation timings → round whole numbers** (300, 1000, 1550, 10000, 20000).
  - Examples: `popoverShowDelayMs`, `popoverHideDelayMs`, `remoteResizeDelayMs`, `redReminderMs`, `yoloRotateMs`, `toastDurationMs`, CSS `--popover-show-delay`, CSS `--popover-hide-delay`.
- **Backend polling / refresh intervals → odd numbers, rounded UP** (1257, 3001, 5003, 15001, 20003).
  - Examples: `metadataRefreshMs`, `paneStateRefreshMs`, `latencyRefreshMs`, `eventLogRefreshMs`.

Rationale: UI durations are perceived by users and read as deliberate at round values. Backend poll intervals stagger client requests to avoid pile-ups on the same tick — odd-and-rounded-up reads as intentional ("3001" not "3000"). When a request says "make it 1000ms", route to the appropriate bucket: UI keeps `1000`, backend uses `1003` or `1009`.

### Responsive UI sizing

Do not hard-code layout capacity around one browser window, OS, zoom level, or font rendering. For menus, panes, dropdowns, tab lists, and editor/viewer surfaces, prefer intrinsic sizing, flex/grid allocation, percentages, viewport units, and shared CSS variables over fixed pixel width buckets. Fixed pixels are acceptable for hairlines, icon glyphs, and small spacing tokens, but not for "how much content fits"; if JavaScript must set a size, derive it from measured DOM content and clamp it to the current viewport/container rather than baking in min/max constants.

### Dev server restart workflow

Restarts of the YOLOmux dev server (port `7778`) must delegate the `(pkill, sleep, relaunch)` chain to **systemd-run as a transient user unit**. Running the chain directly in a Claude-harness `Bash` call does not work: the harness places each Bash invocation in a process container (cgroup/pid-namespace), and when the Bash exits, everything spawned inside — including processes detached with `nohup`, `disown`, or `setsid` — is reaped along with it. Only a process owned by an *external* daemon escapes. The user-mode systemd manager (`user@<uid>.service`) is that daemon.

The setup has two pieces:

**1) Local-only restart scripts** at `~/.local/bin/yolomux-restart-{dev,prod}.sh`. These are **not committed** to the repo because they hardcode local paths and port choices. Recreate them on every machine. Both must be `chmod +x`. Reference template:

```bash
#!/bin/bash
# ~/.local/bin/yolomux-restart-dev.sh
set -u
cd /home/keivenc/yolomux.dev
pkill -f "python3 -u yolomux\.py.*--port 7778" 2>/dev/null
sleep 2
exec python3 -u yolomux.py --host 0.0.0.0 --port 7778 --dangerously-yolo --self-signed \
  >> /tmp/yolomux-dev-7778.log 2>&1 < /dev/null
```

The prod script is the same shape with port `7777` and cwd `~/yolomux` and log `/tmp/yolomux-prod-7777.log`.

The `exec` matters: it replaces the script shell with python3 in the systemd unit's main-PID slot, so systemd treats python3 as the unit's primary process.

**2) Idempotent invocation** from a Claude `Bash` call. Both commands return in milliseconds; the actual `pkill+sleep+exec` chain runs inside the systemd unit's own cgroup, independent of the harness:

```bash
systemctl --user stop yolomux-dev-7778 2>/dev/null
systemd-run --user --quiet --collect --unit=yolomux-dev-7778 ~/.local/bin/yolomux-restart-dev.sh
```

Notes:
- `--collect` garbage-collects the unit after exit, keeping the name reusable.
- `--unit=yolomux-dev-7778` gives a stable name so subsequent restarts can `systemctl stop` it cleanly.
- Without the `stop` first, `systemd-run` refuses to start if the unit is still active.
- For prod, swap `dev`/`7778` for `prod`/`7777` in both lines.

**Always verify after restart** (these are read-only and fast):

```bash
ps -ef | grep "python3 -u yolomux\.py.*7778" | grep -v grep
ss -tlnp 2>/dev/null | grep ":7778 "
curl -sk -o /dev/null -w "ping: %{http_code} %{time_total}s\n" https://localhost:7778/api/ping
curl -sk -u <user>:<pass> https://localhost:7778/ | grep -oE 'YOLOmux [0-9.]+' | head -1
```

Expected: one `python3 -u yolomux.py` process under `user@.service/app.slice/yolomux-dev-7778.service`, `LISTEN` on `:7778`, `ping: 401` in under ~100ms, and the rendered version matches `YOLOMUX_VERSION` in the just-committed code. The version check is what catches the case where the running server is still on stale bytecode.

If a verification step fails, inspect the unit and the log:

```bash
systemctl --user status yolomux-dev-7778 --no-pager
tail -30 /tmp/yolomux-dev-7778.log
```

### Production sync (`cps`)

Two side-by-side checkouts of the yolomux repo share `origin`:

- `~/yolomux.dev/` — where all edits happen.
- `~/yolomux/` — read-only production checkout.

When the user says **"cps"** (`commit, push, sync`), do exactly this from `~/yolomux.dev/`:

```bash
git add -- <explicit-files>           # never `git add -A`; PNG screenshots and scratch files must not get swept in
git commit -m "<message including Version: 0.1.N>"
git push origin main
cd ~/yolomux && git pull --ff-only origin main
```

Rules:
- `cps` is the alias. Same as `commit, push, sync`.
- PRODUCTION pull is `--ff-only`. Never edit, stage, or commit inside `~/yolomux/`.
- If the user has untracked screenshots (`20260*.png`) in `~/yolomux.dev/`, leave them. They are local debug artifacts and must not be committed.
- If `~/yolomux.dev/` has modifications unrelated to the user's request, ask before staging — keep the commit scoped.

## How the webterm works

The server is a dependency-light Python `ThreadingHTTPServer`. It serves one HTML page, local xterm.js assets, JSON APIs, Server-Sent Events streams, and a WebSocket endpoint.

For each terminal connection, the browser opens `/ws?session=<tmux-session>`. The server creates a PTY, runs `tmux attach-session -t <tmux-session>` for admin users or `tmux attach-session -r -t <tmux-session>` for readonly users on the PTY slave, reads terminal bytes from the PTY master, and sends those bytes to xterm.js as WebSocket binary frames.

Browser input is sent as JSON messages over the same WebSocket. Normal keyboard data becomes `{"type": "input", "data": "..."}`. Resize messages become `{"type": "resize", "cols": ..., "rows": ...}`. Scroll messages become `{"type": "tmux-scroll", "direction": "up|down", "lines": ...}`.

Resizing is handled on the PTY slave file descriptor, then the server sends `SIGWINCH` to the tmux attach process group. The browser sends resize updates only after a debounce, except for an initial fast resize during WebSocket startup.

The browser creates panel DOM nodes for visible sessions at boot, checks that the backing tmux sessions exist, and starts terminal connections for them. Visible layout changes move existing panel nodes into slot containers. Hidden panels move back to `#panelPool`. This keeps xterm instances and WebSocket connections alive while changing layout.

The full layout is encoded in the page URL through readable `sessions`, `layout`, and `tabs` query parameters; split positions are stored as percentages in `layout`. This makes a layout reload-safe and shareable/bookmarkable without browser storage.

Transcript metadata comes from tmux pane discovery plus local process-tree inspection. YOLOmux looks for Claude or Codex processes in the selected tmux pane, finds their transcript/session metadata, and exposes it in the pane header, transcript tab, and API responses.

The transcript tab uses Server-Sent Events from `/api/context-stream`. The AI summary tab uses `/api/summary-stream`, which builds a scoped prompt from the selected session's recent transcript and streams a Codex-generated summary back to the browser. Summary model settings can be overridden with `YOLOMUX_SUMMARY_MODEL`, `YOLOMUX_SUMMARY_EFFORT`, and `YOLOMUX_SUMMARY_SERVICE_TIER`.

YOLO uses `auto_approve_tmux.py` workers behind `/api/auto-approve`. The browser polls YOLO status every `paneStateRefreshMs` (see timing-constant rule above) and reflects the active state in each pane tab.

## Code layout

The main server entry point is `yolomux.py`, which delegates to `yolomux_lib/cli.py`. Request routing lives in `yolomux_lib/server.py`, application state and tmux actions live in `yolomux_lib/app.py`, and shared helpers live in smaller modules such as `metadata.py`, `sessions.py`, `transcripts.py`, `uploads.py`, `events.py`, and `websocket.py`.

Frontend code for the interactive UI lives in `static/yolomux.js` and `static/yolomux.css`. Python keeps only the small HTML shell in `yolomux_lib/web.py`, plus bootstrap JSON and versioned static asset URLs. The read-only wall has its own frontend files, `static/tmux-wall.js` and `static/tmux-wall.css`, so `tmux_wall.py` stays focused on tmux capture, JSON endpoints, and Server-Sent Events.

The standalone auto-approval detector lives in `auto_approve_tmux.py` at the repo root. YOLOmux imports it as a Python module and wraps one `AutoApproveWorker` (in `yolomux_lib/auto_approve_worker.py`) around each enabled session.

## Local checks before committing

```bash
python3 -m py_compile yolomux.py tmux_wall.py auto_approve_tmux.py yolomux_lib/*.py
python3 -m pytest tests
node --check static/yolomux.js
node --check static/tmux-wall.js
node tests/layout_url.test.js
```

## Webterm API

All API routes require auth. Read endpoints accept `readonly` or `admin`, except `/api/summary-stream` because it launches Codex and requires `admin`. Mutating POST routes require `admin` except `/api/event`, which accepts readonly client telemetry. `/ws` accepts readonly users but attaches tmux with `-r` and ignores keyboard input and tmux-scroll messages.

- `GET /api/transcripts` returns pane, process, and transcript-path metadata.
- `GET /api/tmux?session=project1&lines=90` returns a tmux capture-pane snapshot.
- `GET /api/transcript?session=project1&lines=120` returns the transcript tail for one session.
- `GET /api/context?session=project1&messages=40` returns a compact, message-oriented transcript tail.
- `GET /api/context-items?session=project1&messages=40` returns structured transcript items.
- `GET /api/context-stream?session=project1&messages=200` streams structured transcript items with Server-Sent Events.
- `GET /api/summary-stream?session=project1&lookback=3600` streams a Codex-generated summary with Server-Sent Events.
- `GET /api/auto-approve` returns YOLO status for all sessions.
- `GET /api/auto-approve?session=project1` returns YOLO status for one session.
- `POST /api/create-session?agent=claude`, `POST /api/create-session?agent=codex`, or `POST /api/create-session?agent=term` creates the next numbered tmux session with the selected agent, capped at nine visible sessions. Claude and Codex requests are rejected if the selected CLI is not available on the YOLOmux server PATH; `term` is available only as the fallback when neither CLI is available.
- `POST /api/ensure-session?session=project1` checks that a tmux session still exists.
- `POST /api/auto-approve?session=project1&enabled=1` enables or disables YOLO for a session.
- `POST /api/tmux-next?session=project1` moves the session to the next tmux window.
- `GET /ws?session=project1` attaches a browser terminal to tmux.

Inspect the transcript mapping without starting the server:

```bash
python3 yolomux.py --print-transcripts
```

## Wall API

The read-only wall (`tmux_wall.py`) exposes:

- `GET /api/snapshot` returns the current six-pane dashboard payload.
- `GET /api/transcript?target=project1:0.0&lines=2000` returns one tmux pane transcript.
- `GET /api/summary-input?lines=1200` returns the active dashboard panes and container metadata as one JSON payload.

These wall endpoints do not call an LLM. They are the stable input surface for a later summarizer.
