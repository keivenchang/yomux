# YOLOmux — Development

Conventions, architecture, build/test workflow, restart workflow, and API notes for contributors live here. End-user/operator docs live in [`README.md`](../README.md). AI-agent behavior rules live in [`AGENTS.md`](../AGENTS.md).

## Setup

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt   # adds pytest-xdist for parallel test runs
```

## Project Conventions

### Version bump per commit

`YOLOMUX_VERSION` in `yolomux_lib/common.py` must be bumped on every commit. The patch segment increments monotonically:

- `0.1` -> `0.1.1` -> `0.1.2` -> `0.1.3` -> ...
- If the current value has no patch segment (just `0.1`), the next commit appends one (`0.1.1`).
- Include the version-bump line in the same commit as the actual code change. Do not make a standalone version-bump commit.
- Surface the new version in the commit message body, for example `Version: 0.1.N`.

To find the latest committed version: `git show HEAD:yolomux_lib/common.py | grep YOLOMUX_VERSION` and add 1 to the patch segment.

### Timing constants

JavaScript and CSS timing constants in `static_src/`, `static/yolomux.js`, and `static/yolomux.css` are split by purpose:

- UI / popup / display / animation timings use round whole numbers, for example `300`, `1000`, `1550`, `10000`, `20000`.
- Backend polling / refresh intervals SHOULD prefer slightly-staggered (often odd) values, for example `1257`, `3001`, `5003`, to spread client requests across ticks instead of piling up on the same one. This is a preference, not an invariant — several shipped defaults in `yolomux_lib/settings.py` are still round (`event_log_refresh_ms: 5000`, `server_event_poll_ms: 850`, `server_directory_event_poll_ms: 3000`); nudge them toward staggered values when you touch that area, but nothing enforces it today.

UI durations are perceived by users and read as deliberate at round values. When a request says "make it 1000ms", a UI duration keeps `1000`; a backend poll may use a nearby staggered value like `1003`.

### Responsive UI sizing

Do not hard-code layout capacity around one browser window, OS, zoom level, or font rendering. For menus, panes, dropdowns, tab lists, and editor/viewer surfaces, prefer intrinsic sizing, flex/grid allocation, percentages, viewport units, and shared CSS variables over fixed pixel width buckets. Fixed pixels are acceptable for hairlines, icon glyphs, and small spacing tokens, but not for "how much content fits"; if JavaScript must set a size, derive it from measured DOM content and clamp it to the current viewport/container.

## Source Layout

The main server entry point is `yolomux.py`, which delegates to `yolomux_lib/cli.py`. Request routing lives in `yolomux_lib/server.py`, application state and tmux actions live in `yolomux_lib/app.py`, and shared helpers live in smaller modules such as `metadata.py`, `sessions.py`, `session_files.py`, `transcripts.py`, `uploads.py`, `events.py`, `websocket.py`, `approvals.py` (the approval-prompt detection pipeline), `atomic_file.py` (cross-process file lock + atomic write), `cache.py` (the shared `TtlCache`), and `activity.py` (the per-session/window user+agent activity ledger).

Frontend source for the interactive UI lives in ordered partials under `static_src/js/yolomux/` and `static_src/css/yolomux/`. Generated served assets are `static/yolomux.js` and `static/yolomux.css`; do not edit those directly except as generated outputs. Python keeps only the small HTML shell in `yolomux_lib/web.py`, plus bootstrap JSON and versioned static asset URLs. The main app's non-tmux tab types are centralized in the `TAB_TYPES` registry in the JS source partials. The read-only wall has its own frontend files, `static/tmux-wall.js` and `static/tmux-wall.css`, so `tmux_wall.py` stays focused on tmux capture, JSON endpoints, and Server-Sent Events.

The approval-prompt detection pipeline lives in `yolomux_lib/approvals.py` (one shared owner: `app.py`'s read-path, the `AutoApproveWorker` act-path in `yolomux_lib/auto_approve_worker.py`, and the standalone `auto_approve_tmux.py` CLI all call it; the CLI re-exports it). One `AutoApproveWorker` wraps each enabled session.

## Static Build

After changing any frontend source file, rebuild the served single-file assets:

```bash
python3 tools/static_build.py
```

Run with `--check` before committing to verify generated assets match source:

```bash
python3 tools/static_build.py --check
```

## Tests

`python3 tools/check.py` runs the local gate as parallel lanes and exits non-zero if any lane fails — use it as the one pre-commit/CPS command. Use `python3 tools/check.py --serial` when debugging order, process load, or interleaved output. Use `python3 tools/check.py --list-lanes` to see lane names and repeat `--lane <name>` for focused runs.

The default lanes are `py-compile`, `static`, `node-syntax`, `node-layout`, `pytest`, and `whitespace`, where `pytest` is the same full `python3 -m pytest tests -n auto -q` gate used before the runner was parallelized. Focused opt-in lanes are `pytest-unit`, `pytest-socket`, and `pytest-browser`; use them with `--lane` while iterating. Slow known tests are collected first so expensive failures show up earlier.

Individual commands remain useful while iterating:

```bash
python3 -m py_compile yolomux.py tmux_wall.py auto_approve_tmux.py yolomux_lib/*.py
python3 tools/static_build.py --check
node --check static/yolomux.js
node --check static/tmux-wall.js
node tests/layout_url.test.js
python3 -m pytest tests --ignore=tests/test_browser_layout.py -m "not socket and not browser and not node_bridge" -q
python3 -m pytest tests --ignore=tests/test_browser_layout.py -m "socket and not browser" -q
python3 -m pytest tests/test_browser_layout.py -n auto -q
git diff --check
```

For focused browser layout work, run the relevant Selenium tests directly, for example:

```bash
python3 -m pytest tests/test_browser_layout.py -k 'finder_path_is_first_and_readable_in_wrapped_toolbar' -q
```

Full pytest may need local socket/browser access. If a sandboxed run fails with `PermissionError: Operation not permitted`, rerun the same command outside the sandbox before treating it as a product failure.

## Dev And Production Servers

Production and development instances run side-by-side:

| Instance | Directory | Port | Purpose |
|---|---|---|---|
| prod | `~/yolomux/` | HTTPS `7777` | Stable copy, synced from dev after verification |
| dev1 | `~/yolomux.dev1/` | HTTPS `8001` | Active development worktree 1 |
| dev2 | `~/yolomux.dev2/` | HTTPS `8002` | Active development worktree 2 |
| dev3 | `~/yolomux.dev3/` | HTTPS `8003` | Active development worktree 3 |
| dev4 | `~/yolomux.dev4/` | HTTPS `8004` | Active development worktree 4 |

The dev worktree ports are HTTPS-only in normal use. Launch them with `--self-signed --dang`; do not use plain HTTP for `8001`-`8004`.

For an ad hoc dev run:

```bash
python3 yolomux.py --host 0.0.0.0 --port 8001 --self-signed --dang
```

### Restart workflow

For dev1, use the repo restart owner. It sets `PATH` explicitly before launch so agent CLIs under `~/.local/bin` are visible even when the caller has a stripped systemd/cron-style environment:

```bash
tools/yolomux-restart-dev1.sh
```

The script defaults to HTTPS `8001`, `--self-signed`, and `--dang`; override with `YOLOMUX_DEV1_PORT`, `YOLOMUX_HOST`, or `YOLOMUX_DEV1_LOG` only when testing a non-standard dev1 instance. It prefers `systemd-run --user` and falls back to a detached `nohup` process with logs under `/tmp`.

Manual reference: delegate the kill/relaunch chain to `systemd-run` as a transient user unit, so the server is not a child of the launching shell. Restart prod by swapping `dev1`/`8001`/`~/yolomux.dev1` for `prod`/`7777`/`~/yolomux`:

```bash
systemctl --user stop yolomux-dev1-8001 2>/dev/null
systemd-run --user --quiet --collect --unit=yolomux-dev1-8001 --working-directory=/home/keivenc/yolomux.dev1 env PATH="$HOME/.local/bin:$PATH" TERM=xterm-256color PYTHONUNBUFFERED=1 /usr/bin/python3 /home/keivenc/yolomux.dev1/yolomux.py --host 0.0.0.0 --port 8001 --dangerously-yolo --self-signed
```

Fallback when `systemd-run --user` is unavailable (some AI-harness sandboxes deny the D-Bus call): kill by PID, then relaunch detached with `( cd <checkout> && nohup python3 -u yolomux.py ... >> /tmp/<log> 2>&1 </dev/null & )`. Two footguns: (1) never `pkill -f` a pattern that also appears literally in the same command's launch string — it matches your own shell and TERMs it (use a `$port` variable in the pattern and keep kill and relaunch in separate commands); (2) a backend `.py` change only takes effect after the python process restarts — `static_build.py` does not touch it.

Verify after restart:

```bash
ps -ef | grep "yolomux\.py.*8001" | grep -v grep
ss -tlnp 2>/dev/null | grep ":8001 "
curl -sk -o /dev/null -w "ping: %{http_code} %{time_total}s\n" https://localhost:8001/api/ping
curl -skL -u <user>:<pass> https://localhost:8001/ | grep -oE 'YOLOmux [0-9.]+' | head -1
```

Expected: one `yolomux.py` process, `LISTEN` on `:8001`, `ping: 401` in under roughly 100ms, and the rendered version matches `YOLOMUX_VERSION`. If verification fails, inspect the unit:

```bash
systemctl --user status yolomux-dev1-8001 --no-pager
```

## Production Sync (`cps`)

Edits happen in a dev worktree (`~/yolomux.dev1` ... `~/yolomux.dev4`, see the table above); `~/yolomux/` is the read-only production checkout sharing the same `origin`. The sequence from the dev worktree:

```bash
python3 tools/check.py                 # the full gate (see Tests above)
git add -- <explicit-files>
git commit -m "<message including Version: 0.2.N>"
git push origin <branch>:main
cd ~/yolomux && git pull --ff-only origin main
# restart prod + the dev server (see Restart workflow above)
```

Rules:

- Bump `YOLOMUX_VERSION` in `yolomux_lib/common.py` in the same commit.
- Never use `git add -A`; screenshots and scratch files must not get swept in.
- Production pull is `--ff-only`. Never edit, stage, or commit inside `~/yolomux/`.
- Restart both servers after sync, then verify `/api/ping` and the rendered version on each login page.

## xterm.js Assets

YOLOmux serves xterm.js from a local install when available. It checks `YOLOMUX_XTERM_ROOTS` first, then `static/xterm`, then common Popular IDE and agent server installs under the home directory. If `/static/xterm.js` or `/static/xterm.css` is missing, the browser falls back to jsDelivr.

## Localization

The UI ships in 19 user-facing languages. Locale files are in `static_src/` and generated `static/` locale outputs. When adding a new user-facing string, add the key to all locale files: English, Traditional and Simplified Chinese, Japanese, Korean, Spanish, German, French, Italian, Brazilian Portuguese, Polish, Dutch, Hebrew, Arabic, Russian, Hindi, Vietnamese, Thai, and Turkish, plus `en-XA` pseudo-locale for QA.

## How The Webterm Works

The server is a dependency-light Python `ThreadingHTTPServer`. It serves one HTML page, local xterm.js assets, JSON APIs, Server-Sent Events streams, and a WebSocket endpoint.

For each terminal connection, the browser opens `/ws?session=<tmux-session>`. The server creates a PTY, runs `tmux attach-session -t <tmux-session>` for admin users or `tmux attach-session -r -t <tmux-session>` for readonly users on the PTY slave, reads terminal bytes from the PTY master, and sends those bytes to xterm.js as WebSocket binary frames.

Browser input is sent as JSON messages over the same WebSocket. Normal keyboard data becomes `{"type": "input", "data": "..."}`. Resize messages become `{"type": "resize", "cols": ..., "rows": ...}`. Scroll messages become `{"type": "tmux-scroll", "direction": "up|down", "lines": ...}`.

Resizing is handled on the PTY slave file descriptor, then the server sends `SIGWINCH` to the tmux attach process group. The browser sends resize updates only after a debounce, except for an initial fast resize during WebSocket startup.

The browser creates panel DOM nodes for visible sessions at boot, checks that the backing tmux sessions exist, and starts terminal connections for them. Visible layout changes move existing panel nodes into slot containers. Hidden panels move back to `#panelPool`. This keeps xterm instances and WebSocket connections alive while changing layout.

The full layout is encoded in the page URL through readable `sessions`, `layout`, and `tabs` query parameters; split positions are stored as percentages in `layout`. This makes a layout reload-safe and shareable/bookmarkable without browser storage.

Transcript metadata comes from tmux pane discovery plus local process-tree inspection. YOLOmux looks for Claude or Codex processes in the selected tmux pane, finds their transcript/session metadata, and exposes it in the pane header, transcript tab, and API responses.

The transcript tab uses Server-Sent Events from `/api/context-stream`. The AI summary tab uses `/api/summary-stream`, which builds a scoped prompt from the selected session's recent transcript and streams a Codex-generated summary back to the browser. Summary model settings can be overridden with `YOLOMUX_SUMMARY_MODEL`, `YOLOMUX_SUMMARY_EFFORT`, and `YOLOMUX_SUMMARY_SERVICE_TIER`.

YOLO uses `auto_approve_tmux.py` workers behind `/api/auto-approve`. The browser polls YOLO status every `paneStateRefreshMs` and reflects the active state in each pane tab.

## Webterm API

All API routes require auth. Read endpoints accept `readonly` or `admin`, except `/api/summary-stream` because it launches Codex and requires `admin`. Mutating POST routes require `admin` except `/api/event`, which accepts readonly client telemetry. `/ws` accepts readonly users but attaches tmux with `-r` and ignores keyboard input, tmux-scroll, and resize messages. Share-token guests are narrower than `auth.yaml` readonly users: tokens are scoped to one session and whitelisted only to `/`, `/ws`, and static assets.

- `GET /api/transcripts` returns pane, process, and transcript-path metadata.
- `GET /api/tmux?session=project1&lines=90` returns a tmux capture-pane snapshot.
- `GET /api/transcript?session=project1&lines=120` returns the transcript tail for one session.
- `GET /api/context?session=project1&messages=40` returns a compact, message-oriented transcript tail.
- `GET /api/context-items?session=project1&messages=40` returns structured transcript items.
- `GET /api/context-stream?session=project1&messages=200` streams structured transcript items with Server-Sent Events.
- `GET /api/summary-stream?session=project1&lookback=3600` streams a Codex-generated summary with Server-Sent Events.
- `GET /api/search?q=text&session=project1` searches captured events and current per-session summaries.
- `GET /api/run-history` returns compact per-session history: cwd, agent, transcript mtime, repo metadata, and recent events.
- `GET /api/activity` returns the activity ledger: per-session and per-`session:window` aggregates (coalesced user typed-time, input event/byte counts, agent-active time, last input/output/selected timestamps). Heartbeats are emitted from the WS input arm (admin keystrokes only); typed-time is the sum of inter-heartbeat gaps capped at an idle threshold. Persisted to `~/.local/state/yolomux/activity.json` via the atomic-write path, pruned on the session sweep.
- `GET /api/activity-summary?locale=en&force=1` returns the YO!agent activity-summary payload: per-session summaries, global summary lines, and cached YO!agent summary text. `force=1` refreshes the cached summary work; otherwise the server may serve the previous summary while background refresh paths keep the ledger current.
- `GET /api/session-files?session=project1&hours=24` returns repo-aware AI file changes for one session.
- `GET /api/auto-approve` returns YOLO status for all sessions.
- `GET /api/auto-approve?session=project1` returns YOLO status for one session.
- `POST /api/create-session?agent=claude`, `POST /api/create-session?agent=codex`, or `POST /api/create-session?agent=term` creates the next numbered tmux session with the selected agent, capped at `MAX_YOLOMUX_SESSION_TABS` (99) visible sessions.
- `POST /api/ensure-session?session=project1` checks that a tmux session still exists.
- `POST /api/auto-approve?session=project1&enabled=1` enables or disables YOLO for a session.
- `POST /api/tmux-next?session=project1` moves the session to the next tmux window.
- `GET /ws?session=project1` attaches a browser terminal to tmux.

Inspect transcript mapping without starting the server:

```bash
python3 yolomux.py --print-transcripts
```

## Wall API

The read-only wall (`tmux_wall.py`) exposes:

- `GET /api/snapshot` returns the current six-pane dashboard payload.
- `GET /api/transcript?target=project1:0.0&lines=2000` returns one tmux pane transcript.
- `GET /api/summary-input?lines=1200` returns the active dashboard panes and container metadata as one JSON payload.

These wall endpoints do not call an LLM. They are the stable input surface for a later summarizer.
