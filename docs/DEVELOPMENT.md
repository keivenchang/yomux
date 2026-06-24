# YOLOmux — Development

Conventions, architecture, build/test workflow, restart workflow, and API notes for contributors live here. End-user/operator docs live in [`README.md`](../README.md). AI-agent behavior rules live in [`AGENTS.md`](../AGENTS.md).

## Setup

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt   # adds pytest-xdist for parallel test runs
```

Optional YO!agent managed Claude/Codex SDK transports need the extra SDK package set:

```bash
pip install -r requirements-yoagent-managed.txt
```

On externally managed system Python installs, create a virtual environment first instead of forcing `--break-system-packages`.

## Project Conventions

### Version bump policy

`YOLOMUX_VERSION` in `yolomux_lib/common.py` is bumped only when publishing to `origin/main`. LOCAL `cps` / `yolo-cps` lands work on local `main` with no version bump and no push; ORIGIN / REMOTE `cps` rebases onto `origin/main`, increments the patch segment, folds the bump into the work commit, and includes `Version: 0.4.N` in the commit message body. The self-updater compares `YOLOMUX_VERSION` on `origin/main`, so SHA-only published commits do not cue update notifications, but local integration commits must not create fake versions.

To find the latest committed version before ORIGIN publish: `git show origin/main:yolomux_lib/common.py | grep YOLOMUX_VERSION` and add 1 to the patch segment.

### Timing constants

JavaScript and CSS timing constants in `static_src/`, `static/yolomux.js`, and `static/yolomux.css` are split by purpose:

- UI / popup / display / animation timings use round whole numbers, for example `300`, `1000`, `1550`, `10000`, `20000`.
- Backend polling / refresh intervals SHOULD prefer slightly-staggered (often odd) values, for example `1257`, `3001`, `5003`, to spread client requests across ticks instead of piling up on the same one. This is a preference, not an invariant — several shipped defaults in `yolomux_lib/settings.py` are still round (`event_log_refresh_ms: 5000`, `server_event_poll_ms: 850`, `server_directory_event_poll_ms: 3000`); nudge them toward staggered values when you touch that area, but nothing enforces it today.

UI durations are perceived by users and read as deliberate at round values. When a request says "make it 1000ms", a UI duration keeps `1000`; a backend poll may use a nearby staggered value like `1003`.

### Responsive UI sizing

Do not hard-code layout capacity around one browser window, OS, zoom level, or font rendering. For menus, panes, dropdowns, tab lists, and editor/viewer surfaces, prefer intrinsic sizing, flex/grid allocation, percentages, viewport units, and shared CSS variables over fixed pixel width buckets. Fixed pixels are acceptable for hairlines, icon glyphs, and small spacing tokens, but not for "how much content fits"; if JavaScript must set a size, derive it from measured DOM content and clamp it to the current viewport/container.

## Source Layout

The main server entry point is `yolomux.py`, which delegates to `yolomux_lib/cli.py`. Request routing lives in `yolomux_lib/server.py`, application state lives in `yolomux_lib/app.py`, visible Claude/Codex tmux pane control lives in `yolomux_lib/agent_tui.py`, shared Claude/Codex structured communication lives in `yolomux_lib/agent_comms/` (Codex app-server, Claude stream-json, and normalized stream events), and shared helpers live in smaller modules such as `metadata.py`, `sessions.py`, `session_files.py`, `transcripts.py`, `uploads.py`, `events.py`, `websocket.py`, `approvals.py` (the approval-prompt detection pipeline), `atomic_file.py` (cross-process file lock + atomic write), `cache.py` (the shared `TtlCache`), and `activity.py` (the per-session/window user+agent activity ledger).

Frontend source for the interactive UI lives in ordered partials under `static_src/js/yolomux/` and `static_src/css/yolomux/`. Generated served assets are `static/yolomux.js` and `static/yolomux.css`; do not edit those directly except as generated outputs. Python keeps only the small HTML shell in `yolomux_lib/web.py`, plus bootstrap JSON and versioned static asset URLs. The main app's non-tmux tab types are centralized in the `TAB_TYPES` registry in the JS source partials. The read-only wall has its own frontend files, `static/tmux-wall.js` and `static/tmux-wall.css`, so `tmux_wall.py` stays focused on tmux capture, JSON endpoints, and Server-Sent Events.

The approval-prompt detection pipeline lives in `yolomux_lib/approvals.py` (one shared owner: `app.py`'s read-path, the `AutoApproveWorker` act-path in `yolomux_lib/auto_approve_worker.py`, and the standalone `auto_approve_tmux.py` CLI all call it; the CLI re-exports it). `agent_tui.py` is the public owner for combining that pure detector state with tmux captures, cursor facts, composer draft/ghost state, transcript activity upgrades, clear, paste-submit, and submit verification. One `AutoApproveWorker` wraps each enabled session.

## Specs

Durable behavior specs live under [`docs/specs/`](specs/). Use [`docs/specs/GUI.md`](specs/GUI.md) for pane/tab/Finder/Differ/YO!share visual contracts, [`docs/specs/EDITOR-CODEMIRROR.md`](specs/EDITOR-CODEMIRROR.md) for CodeMirror/editor behavior, [`docs/specs/SHARE_MIRRORING.md`](specs/SHARE_MIRRORING.md) for the YO!share replay architecture, and [`docs/specs/SHARE_TEST_INVENTORY.md`](specs/SHARE_TEST_INVENTORY.md) for YO!share fixture/live-server/manual-repro test coverage.

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

The default lanes are `py-compile`, `static`, `node-syntax`, `node-layout`, `pytest`, and `whitespace`. The `pytest` lane runs `python3 -m pytest tests -n auto -m "not node_bridge" -q`: it excludes the `node_bridge` marker because `test_node_suite.py` shells out to the same `node tests/layout_url.test.js` the always-on `node-layout` lane already runs, so the gate would otherwise run that ~20s node suite twice concurrently (and the two node processes thrash the cores the browser workers need). The `node-layout` lane keeps node coverage in the gate; a bare `python3 -m pytest tests` (outside check.py) still runs the bridge. Focused opt-in lanes are `pytest-unit`, `pytest-socket`, and `pytest-browser`; use them with `--lane` while iterating. Slow known tests are collected first so expensive failures show up earlier.

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

Browser/live tests may launch local throwaway HTTP servers, but they must also isolate tmux and state. Use fixture-owned config/state directories, ephemeral HTTP ports, a private tmux socket, and fixture-created session names such as `yt-<pid>-<uuid>-1`; automated tests must not touch live dev/prod servers on ports like `8001` or `7777`, and live dev/prod access is limited to explicit smoke checks after a requested restart or sync.

For local verification that should skip login, start the dev server with `YOLOMUX_TEST_AUTH_BYPASS=1`. This is a test-only admin bypass for localhost/dev workflows, useful for direct curls or Selenium checks against `/api/settings` and other login-gated routes. Tests that are not validating auth and only need a logged-in host should use this path instead of minting cookies. Tests that validate setup, login, logout, cookies, Basic auth, readonly/admin role boundaries, share-token scoping, or expected 401/403 behavior must not use it. Do not use it for production or any server reachable by untrusted clients.

## Dev And Production Servers

Production and development instances can run side-by-side. The project does not require a fixed dev numbering scheme; choose a checkout path, HTTPS port, and user-unit name that match your machine.

| Role | Directory | Port | Purpose |
|---|---|---|---|
| prod | Production checkout, for example `~/yolomux/` | Stable HTTPS port | Stable copy, synced from dev after verification |
| dev | Any development checkout or git worktree | Non-conflicting HTTPS port | Active development server |

Development servers are HTTPS in normal use. Launch them with `--self-signed --dang --dev` on the port you selected; avoid plain HTTP unless you are testing that path explicitly. `--dev` enables the browser `/api/dev-reload` stream, so an open tab reloads when `static_build.py` rewrites `static/yolomux.css` or `static/yolomux.js`.

For an ad hoc dev run:

```bash
python3 yolomux.py --host 0.0.0.0 --port <port> --self-signed --dang --dev
```

### Restart workflow

For the dev8001 worktree (HTTPS `8001`), use the repo restart owner (`tools/yolomux-restart-dev1.sh` — the filename and its `YOLOMUX_DEV1_*` env vars predate the `dev1`->`dev8001` worktree rename; it still targets port `8001`). It sets `PATH` explicitly before launch so agent CLIs under `~/.local/bin` are visible even when the caller has a stripped environment:

```bash
tools/yolomux-restart-dev1.sh
```

The script defaults to HTTPS `8001`, `--self-signed`, `--dang`, and `--dev`; override with `YOLOMUX_DEV1_PORT`, `YOLOMUX_HOST`, or `YOLOMUX_DEV1_LOG` only when testing a non-standard instance. Logs go under `/tmp`. For the dev8002/dev8003 worktrees use the generic recipe below (port `8002`/`8003`), not this script.

To restart dev8001 in no-auth test mode:

```bash
YOLOMUX_TEST_AUTH_BYPASS=1 tools/yolomux-restart-dev1.sh
```

For any other active dev worktree, use the actual checkout path and port. Kill only the listener for that port, then relaunch detached with an explicit `PATH`. Invoke this from outside long-lived test/browser runs so you do not kill your own verifier:

```bash
role=dev8003
port=8003
checkout="$HOME/yolomux.dev8003"
log="/tmp/yolomux-${role}-${port}.log"
pid="$(ps -eo pid=,args= | awk -v port="$port" '$0 ~ /yolomux.py/ && $0 ~ ("--port " port) {print $1; exit}')"
if [ -n "$pid" ]; then kill "$pid"; fi
( cd "$checkout" && nohup env PATH="$HOME/.local/bin:$PATH" TERM=xterm-256color PYTHONUNBUFFERED=1 python3 -u yolomux.py --host 0.0.0.0 --port "$port" --self-signed --dang --dev > "$log" 2>&1 < /dev/null & )
```

Two footguns: (1) never `pkill -f` a pattern that also appears literally in the same command's launch string because it can match the launching shell; use a port variable and keep kill and relaunch steps separate; (2) do not use stale local helper scripts that point at a different checkout or port; (3) a backend `.py` change only takes effect after the Python process restarts, because `static_build.py` does not touch backend code.

Verify after restart:

```bash
ps -ef | grep "yolomux\.py.*<port>" | grep -v grep
ss -tlnp 2>/dev/null | grep ":<port> "
curl -sk -o /dev/null -w "ping: %{http_code} %{time_total}s\n" https://localhost:<port>/api/ping
curl -skL -u <user>:<pass> https://localhost:<port>/ | grep -oE 'YOLOmux [0-9.]+' | head -1
```

Expected: one `yolomux.py` process, `LISTEN` on the intended port, `ping: 401` in under roughly 100ms, and the rendered version matches `YOLOMUX_VERSION`. If verification fails, inspect the `/tmp/yolomux-<role>-<port>.log` log for the launch error.

If you intentionally started with `YOLOMUX_TEST_AUTH_BYPASS=1`, `/api/ping` and `/api/settings` should return `200` without cookies. If a normal login-gated server returns `200` for those unauthenticated routes, auth is accidentally bypassed.

## Production Sync (`cps`)

`cps` (the `yolo-cps` skill) has two modes, chosen by the trigger word:

- **LOCAL (default — "cps" / "yolo-cps" with no qualifier):** rebase the dev branch onto LOCAL `main` and land it on local `main`. NO version bump, NO push.
- **ORIGIN ("cps origin" / "cps remote"):** rebase onto `origin/main`, bump the version, land on local `main`, then push to `origin/main`.

Edits happen in a dev checkout; the integration/production checkout (`~/yolomux`, which holds `main`) is read-only during sync and shares the same `origin`. Because `main` is checked out in `~/yolomux`, you cannot check it out in a dev worktree — land work by committing on the dev branch, rebasing it onto `main` / `origin/main`, then fast-forwarding `main` from `~/yolomux`.

ORIGIN-mode sequence from the dev checkout:

```bash
python3 tools/check.py                       # the full gate (see Tests above)
git fetch origin && git rebase origin/main   # rebase onto the published tip
# bump YOLOMUX_VERSION in yolomux_lib/common.py, folded into the work commit
git add -- <explicit-files>
git commit -m "<message including Version: 0.4.N>"
git -C ~/yolomux merge --ff-only <branch>    # land on local main — its OWN exit-checked command
git -C ~/yolomux push origin main            # only after the ff-merge succeeds
```

LOCAL mode is the same minus the version bump and the final `push` (stop after the ff-merge).

Rules:

- ORIGIN mode MUST bump `YOLOMUX_VERSION` in `yolomux_lib/common.py` in the same commit; the auto-updater checks this value on `origin/main`, not the commit SHA, so SHA-only commits do not cue the update. LOCAL mode does NOT bump.
- Never use `git add -A`; screenshots and scratch files must not get swept in.
- The `merge --ff-only` into `~/yolomux` MUST be its own exit-checked command — NOT piped through `tail`/`grep` and NOT `&&`-chained straight into the push. A pipe's exit status is the last stage's, so a DIVERGED ff-merge fails silently and a chained `push origin main` then publishes whatever local `main` already points at, not your commit. Confirm the merge succeeded before pushing.
- This is a shared multi-worktree: local `main` can advance from another worktree mid-`cps`, so your ff-merge can suddenly refuse (diverged). Recovery: `git fetch origin`, `git rebase origin/main` your dev branch (disjoint files rebase clean), re-run the gate, then ff-merge + push. No force-push, nothing lost — the other commit is usually a sibling off the same base.
- Production pull/merge is `--ff-only`. Never edit, stage, or commit inside `~/yolomux`.
- Restart is NOT part of `cps`. Restart prod/dev only when explicitly asked (see Restart workflow above), then verify `/api/ping`, the process cwd, and `YOLOMUX_VERSION`. The login page may not expose the version to unauthenticated curl; do not rely on a blank version grep alone.

## xterm.js Assets

YOLOmux serves xterm.js from a local install when available. It checks `YOLOMUX_XTERM_ROOTS` first, then `static/xterm`, then common Popular IDE and agent server installs under the home directory. If `/static/xterm.js` or `/static/xterm.css` is missing, the browser falls back to jsDelivr. Terminals also load `@xterm/addon-unicode11` when available so emoji and other wide glyphs use modern cell widths before first paint.

## Localization

The UI ships in 19 user-facing languages. Locale files are in `static_src/` and generated `static/` locale outputs. When adding a new user-facing string, add the key to all locale files: English, Traditional and Simplified Chinese, Japanese, Korean, Spanish, German, French, Italian, Brazilian Portuguese, Polish, Dutch, Hebrew, Arabic, Russian, Hindi, Vietnamese, Thai, and Turkish, plus `en-XA` pseudo-locale for QA.

Do not seed new locale keys with the English value and leave them there. Preserve interpolation tokens such as `{path}`, `{qpath}`, `{paths}`, `{qpaths}`, `{name}`, `{count}`, `{category}`, `{session}`, and `{command}` exactly, but translate the surrounding prose. `python3 tools/static_build.py --check` prints a warning report for non-allowlisted locale values that still equal `en.json` and fails only if a locale regresses above its recorded baseline; `python3 tools/static_build.py --i18n-untranslated-report` prints the full key list for backfill work.

## How The Webterm Works

The server is a dependency-light Python `ThreadingHTTPServer`. It serves one HTML page, local xterm.js assets, JSON APIs, Server-Sent Events streams, and a WebSocket endpoint.

For each terminal connection, the browser opens `/ws?session=<tmux-session>`. The server creates a PTY, runs `tmux attach-session -t <tmux-session>` for admin users or `tmux attach-session -r -t <tmux-session>` for readonly users on the PTY slave, reads terminal bytes from the PTY master, and sends those bytes to xterm.js as WebSocket binary frames.

Browser input is sent as JSON messages over the same WebSocket. Normal keyboard data becomes `{"type": "input", "data": "..."}`. Resize messages become `{"type": "resize", "cols": ..., "rows": ...}`. Scroll messages become `{"type": "tmux-scroll", "direction": "up|down", "lines": ...}`.

Resizing is handled on the PTY slave file descriptor, then the server sends `SIGWINCH` to the tmux attach process group. The browser sends resize updates only after a debounce, except for an initial fast resize during WebSocket startup.

The browser creates panel DOM nodes for visible sessions at boot, checks that the backing tmux sessions exist, and starts terminal connections for them. Visible layout changes move existing panel nodes into slot containers. Hidden panels move back to `#panelPool`. This keeps xterm instances and WebSocket connections alive while changing layout.

The full layout is encoded in the page URL through readable `sessions`, `layout`, and `tabs` query parameters; split positions are stored as percentages in `layout`. This makes a layout reload-safe and shareable/bookmarkable without browser storage.

Transcript metadata comes from tmux pane discovery plus local process-tree inspection. YOLOmux looks for Claude or Codex processes across the session's tmux windows, finds their transcript/session metadata, and exposes both per-session summaries and per-window rows in the pane header, transcript tab, popovers, Tabber, and API responses. `project.git` is the primary session summary. The backend-owned `agent_windows` rows are the source of truth for Claude/Codex tmux-window UI: state, pid, active window, touched repo roots, and git metadata. `window_metadata[]` remains raw per-window cwd metadata for transcript payload compatibility, but popover, Tabber, Info Bar, and tmux window-bar agent UI must not derive their agent path/git/active state from it.

The transcript tab uses Server-Sent Events from `/api/context-stream`. The AI summary tab uses `/api/summary-stream`, which builds a scoped prompt from the selected session's recent transcript and streams a Codex-generated summary back to the browser. Summary provider defaults live in `settings.yaml` under `summary.*` and in the backend settings catalog; `YOLOMUX_SUMMARY_MODEL`, `YOLOMUX_SUMMARY_EFFORT`, and `YOLOMUX_SUMMARY_SERVICE_TIER` only seed defaults when they name valid catalog values.

YOLO uses `auto_approve_tmux.py` workers behind `/api/auto-approve`. The browser polls YOLO status every `paneStateRefreshMs` and reflects the active state in each pane tab.

## YO!share Transport

YO!share visual sharing uses host-rendered DOM replay. The host opens `/ws/share-host` and publishes sanitized `#appRoot` keyframes, DOM deltas, replay control replies, status, and terminal placeholder metadata. Viewers open `/ws/share-ui` to receive replay frames and share status; HTTPS write viewers may also send validated `input-intent` frames there. `/ws/share-view` remains the terminal byte stream for shared tmux sessions, and terminal content is represented in replay DOM only by placeholders that bind the existing xterm stream to host rows/cols.

Replay deltas are ordered by epoch, sequence, and base sequence. Old or already-applied deltas are stale and should be ignored without repair; missing future deltas are gaps and should request a keyframe. Replay debug/profiling output should expose both paths separately (`staleFrames` versus dropped frames/keyframe requests) plus the frame numbers needed to explain a `viewer behind` report.

The replayed DOM is inert serialized markup. Host app event listeners do not survive keyframe rebuilds or child-list delta rebuilds, so viewer-local read-only affordances such as tab hover details must be rebound by the replay shell after replay applies, or represented as host-owned replay DOM.

Write access requires HTTPS. A write viewer does not publish `layout`, `ui-state`, Finder, editor, popup, or scroll semantic state; it sends allowed input intents and waits for host replay frames for visible feedback. `shareReplay=0`, `shareSemantic=1`, bootstrap `shareReplay: false`, and `localStorage.yolomux.shareReplaySemantic=1` are temporary semantic escape hatches for diagnostics, not the normal product path.

## Webterm API

All API routes require auth unless the process was intentionally started with the local-only `YOLOMUX_TEST_AUTH_BYPASS=1` test bypass. Read endpoints accept `readonly` or `admin`, except `/api/summary-stream` because it launches Codex and requires `admin`. Mutating POST routes require `admin` except `/api/event`, which accepts readonly client telemetry. `/ws` accepts readonly users but attaches tmux with `-r` and ignores keyboard input, tmux-scroll, and resize messages. Share-token guests are narrower than `auth.yaml` readonly users: tokens are scoped to one share and whitelisted only for share-scoped page, static, replay, terminal, status, and readonly file/data routes. The test bypass never escalates a share-token request; share-token scoping still wins.

- `GET /api/transcripts` returns pane, process, transcript-path, `project`, and `window_metadata[]` metadata. Use `project.git` only for the session-level summary. Claude/Codex per-window path/branch/git/active UI must use the backend-owned `agent_windows` rows from `/api/activity` or `/api/auto-approve`, not frontend reconstruction from `window_metadata[]` or pane cwd.
- `GET /api/tmux?session=project1&lines=90` returns a tmux capture-pane snapshot.
- `GET /api/transcript?session=project1&lines=120` returns the transcript tail for one session.
- `GET /api/context?session=project1&messages=40` returns a compact, message-oriented transcript tail.
- `GET /api/context-items?session=project1&messages=40` returns structured transcript items.
- `GET /api/context-stream?session=project1&messages=200` streams structured transcript items with Server-Sent Events.
- `GET /api/summary-stream?session=project1&lookback=3600` streams a Codex-generated summary with Server-Sent Events.
- `GET /api/search?q=text&session=project1` searches captured events plus current per-session, rolling, and global summaries with scan-on-query. `results[]` carries `session`, `timestamp`, `kind`, `source`, `title`, `snippet`, and a `target` object for the UI jump.
- `GET /api/run-history` returns compact per-run history persisted under `~/.local/state/yolomux/run-history.json`: prompt, cwd, agent, started/ended time, final state, PR, latest summary, transcript path/mtime, repo metadata, and recent events.
- `GET /api/activity?hours=24` returns the activity ledger, a sorted `agents` list for Recent Agents, and `agent_windows` keyed by session for Tabber/popover/Info Bar/window-bar rendering: per-session and per-`session:window` ledger aggregates (coalesced user typed-time, input event/byte counts, agent-active time, last input/output/selected timestamps), detected Claude/Codex panes with transcript-derived `last_used_ts`, `sort_ts`, and `running`, plus one backend-owned record per Claude/Codex tmux window with state, pid, `active` from tmux `window_active`, touched repo roots from the cached session-files attribution, and git branch/root/worktree/HEAD/dirty/ahead facts. Heartbeats are emitted from the WS input arm (admin keystrokes only); typed-time is the sum of inter-heartbeat gaps capped at an idle threshold. Persisted ledger state lives in `~/.local/state/yolomux/activity.json` via the atomic-write path and is pruned on the session sweep. The app builds the first Tabber activity snapshot during startup and a dedicated server background warmer refreshes it on `performance.tabber_activity_refresh_ms` (15 seconds by default); clients and Tabber clicks read that cached snapshot instead of rebuilding activity synchronously or fetching per-window paths directly.
- `GET /api/activity-summary?locale=en&force=1` returns the YO!agent activity-summary payload: per-session summaries, global summary lines, cached YO!agent summary text, and the same server-cached Tabber `agents` list used by `/api/activity` for the Recent agents chat header. Agent rows include the tmux window name/label and recent touched path roots. `force=1` refreshes the cached summary work and the Tabber activity cache; otherwise the server may serve the previous summary while background refresh paths keep the ledger current.
- YO!agent is the central command surface for YOLOmux. It should reason over all tmux sessions visible to the current user, maintain enough session/path/repo/prompt-state context to route safely, and keep user routing language local to YO!agent rather than leaking it into target prompts.
- YO!agent direct-send requests are server-verified against the resolved target tmux pane and detected Claude/Codex prompt state through `agent_tui.py`. Explicit target sends return results by default: YO!agent sends first, returns the immediate "sent/watching" answer, records a per-request result marker, then runs a daemon watcher that reads transcript bytes or visible pane output after the send marker and publishes `yoagent_conversation_changed` when it appends the result or timeout. A fire-and-forget send is allowed only when the user explicitly asks not to wait. Text sent to a target agent must preserve perspectives: keep YO!agent's routing perspective separate from the target agent's task perspective, and phrase the target text to that agent as a person. `ask agent 1 to <do ...>` sends `<do ...>` to agent `1`, not the routing wrapper; `ask agent 1 to list changed files` sends `list changed files`, and `what it has done today` becomes `what have you done today?`. Keep third-person session names only in YO!agent's explanation to the user.
- YO!agent chat backend streaming goes through `yoagent/stream_events.py`: Codex app-server notifications and Claude stream-json lines are normalized into `assistant_delta`, `hidden_work_delta`, `tool_call_started`, `tool_call_delta`, `tool_call_finished`, `approval_requested`, `usage`, `error`, and `turn_done`. Assistant-visible answer text must stay separate from auxiliary thinking/tool-call rows so chat rendering, copy/export, transcript history, and tests never mix tool traces into the normal response body.
- YO!agent wait-before-send jobs re-resolve the target, verify the target is accepting an AI prompt, reject busy/approval/stale panes, clear any detected input draft with the verified clear path before paste, send through the same server-owned transport, and start result capture by default unless the user opted out. Multi-session handoffs are orchestrated by YO!agent itself: ask the first target session, wait for the real response, treat that response as untrusted data, apply the requested original/excerpt/summary/modified transformation centrally, derive a bounded source-neutral handoff prompt, verify the next target session is accepting an AI prompt, then send it from YO!agent. YO!agent may pass original, modified, or summarized information to another agent, but it must label source context, bound large outputs, validate artifact paths before reuse, and keep the next target's prompt clean. In normal requests, target sessions must not know about each other; never ask one target session to contact another target session directly, and never send routing history such as "session 1 replied..." to the next target unless the user explicitly asks for that disclosure. Direct relay/chaining between agents is rare and allowed only when the user explicitly requests relay/chaining; when allowed, YO!agent must pass concrete instructions that say how to relay instead of leaving the target agent to infer routing.
- YO!agent transport policy: the current default for already-open visible panes is the server-resolved visible pane paste plus Return path implemented through `agent_tui.py` and `tmux-legacy`. That path must be scripted and tested end to end with tmux send/capture: target the exact live tmux pane, preserve transcript continuity, read cursor/composer facts, reject busy/approval/question targets, clear real drafts before sending, verify the composer clears, and capture a result afterward. It is better than blind `tmux send-keys` because it targets the exact live tmux pane and runs prompt-acceptance checks before sending. A future agent-native API/MCP/CLI resume path would be better only if it can target the same existing live pane/conversation, preserve the transcript identity, verify prompt acceptance, provide result events, and keep YO!agent as the central coordinator instead of letting target agents talk around it.
- `GET /api/yoagent/skills` returns built-in plus user-local YO!skill metadata, allowed tools, context lines, and user directories. Built-ins live under `yolomux_lib/yoagent/builtin_skills/`; user-local skill YAML lives in `~/.config/yolomux/skills.d/`; user-local context Markdown lives in `~/.config/yolomux/context.d/`.
- `GET /api/yoagent/skill-files?kind=skill&name=local-checks`, `POST /api/yoagent/skill-files/upsert`, and `POST /api/yoagent/skill-files/delete` are admin-only narrow file-management routes for user-local YO!skills/context. They validate names, YAML, allowed tools, and canonical paths before writing or deleting; built-in files are not writable through these routes.
- `docs/YOAGENT_SKILLS.md` is the user/developer guide for YO!skill schema, file locations, examples, and YO!agent skill-file management commands.
- `docs/specs/YOAGENT_COMMON_INTENTS_AND_AGENT_COMMUNICATION.md` is the product spec for common YO!agent questions, expected Preference/product-state behavior, multi-agent handoff examples, artifact handoff rules, and the cross-agent communication reliability ladder.
- `POST /api/yoagent/intent` returns a server-side YO!agent preview for a proposed action or job. `POST /api/yoagent/actions/preview-send` resolves a target session into an exact pane/agent/transport preview, and `POST /api/yoagent/actions/execute-send` executes only a live, ready, not-stale preview id. `GET /api/yoagent/jobs`, `POST /api/yoagent/jobs`, `POST /api/yoagent/jobs/<id>/confirm`, `POST /api/yoagent/jobs/<id>/cancel`, and `POST /api/yoagent/jobs/cancel-session` manage YO!agent jobs. These routes are admin-only; share-token and readonly users cannot create jobs or send prompts indirectly through YO!agent chat.
- YO!agent jobs persist under `yoagent_jobs` in `~/.config/yolomux/state.json`. Each job records `id`, `type`, `target`, `predicate`, `action`, timestamps, status, confirmation state, timeout, idempotency hash, last observed state, and audit event ids. Jobs are one-shot in the first implementation; duplicate queued/pending jobs are rejected by idempotency hash. The server polls queued jobs from the existing client-event watch loop, debounces idle/done predicates with `quiet_seconds`, times out expired jobs once, and emits `yoagent_jobs_changed` events for created, confirmed, cancelled, fired, failed, and timed-out jobs.
- Notify jobs cover one session becoming idle/done, one session entering `needs-input`, one session becoming blocked by approval/error/disconnect state, one session finishing after this job observed it working, and all visible tmux sessions becoming idle when all-session discovery is enabled. `POST /api/yoagent/jobs/cancel-session` cancels queued and pending-confirmation jobs for one session without touching fired/failed/timed-out jobs or other sessions. Wait-then-send jobs wait for the resolved target to accept a Claude/Codex prompt, then create a fresh send preview, revalidate the target, paste text plus Return, and start the same result watcher used by direct sends unless the user explicitly opted out. If the session disappears, the job fails and notifies rather than retargeting by name.
- YO!agent risk policy is intentionally narrow and boring. Normal explicit sends do not ask for extra confirmation unless the user asks for preview/confirmation, but high-risk prompt text forces confirmation: secret-like assignments, credential paths, `rm -rf`, `git reset --hard`, broad `pkill -f`, recursive `chmod`/`chown`, and SSH commands. Job idempotency keys are hashes, and stored/displayed YO!agent conversation text plus public job/action payloads redact known `token=`, `secret=`, `password=`, and `api_key=` style values.
- `GET /api/session-files?session=project1&hours=24` returns repo-aware AI file changes for one session. `GET /api/session-files-batch?session=project1&session=project2&hours=24` returns the same payload shape keyed by session after discovering tmux metadata once, then fills missing per-session payloads with bounded concurrency. These endpoints power Modified-files/Differ and feed the server-side `agent_windows` fold; Tabber no longer fetches them directly for per-window touched paths. They use a 30 second stale-while-revalidate in-memory cache, a shared disk cache under `~/.local/state/yolomux/session-files-cache/` (or `YOLOMUX_STATE_DIR/session-files-cache/`), and a per-key cross-process lock so parallel YOLOmux servers can reuse one touched-path payload instead of rebuilding it in every process. Owner refresh completion fans out `background_refresh_done` so followers refetch instead of waiting for disk polling. The `/api/activity` cache reuses those payloads when building `agent_windows`. `force=1` bypasses existing cache data and stores the replacement result.

### Background Ownership

Expensive background work that writes or refreshes shared state is single-owner per `YOLOMUX_STATE_DIR`. The latest launched YOLOmux server owns Tabber activity refresh, session-files refresh, search-index builds, and watch-root polling; older servers become followers even if a browser is actively connected to them. Followers serve shared ready/stale snapshots and request refresh from the owner over the control socket. They must not start warmers, long-lived index builders, or derived watch-root directory polling/listing. They still update the shared watch-root intent index so the owner can observe connected browser interest. Search-index followers read the small per-root manifest for status/count metadata instead of eagerly opening the SQLite entries database; owners use the SQLite database plus WAL instead of rewriting one large JSON entries file. When owner refresh/build work finishes, the owner writes a small shared background client-event manifest and sends `background_client_event` over live follower control sockets so each follower publishes `background_refresh_done` or `background_owner_changed` through its own `ClientEventBroker`; the browser then refetches without waiting for disk polling. On takeover, the new owner adopts warm session-files, Tabber activity, and search-index disk snapshots into memory and only refreshes later from the stale-while-revalidate paths; startup itself does not cold-rebuild those snapshots. If the owner heartbeat is stale or a bounded `background_refresh` control request fails, a follower may run one local one-shot fallback for the immediate response, count and log that fallback, and then return to follower mode. Ownership is still guarded by the global owner flock, so fallback never means split ownership.

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
