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

For local verification that should skip login, start the dev server with `YOLOMUX_TEST_AUTH_BYPASS=1`. This is a test-only admin bypass for localhost/dev workflows, useful for direct curls or Selenium checks against `/api/settings` and other login-gated routes. Tests that are not validating auth and only need a logged-in host should use this path instead of minting cookies. Tests that validate setup, login, logout, cookies, Basic auth, readonly/admin role boundaries, share-token scoping, or expected 401/403 behavior must not use it. Do not use it for production or any server reachable by untrusted clients.

## Dev And Production Servers

Production and development instances can run side-by-side. The project does not require a fixed dev numbering scheme; choose a checkout path, HTTPS port, and user-unit name that match your machine.

| Role | Directory | Port | Purpose |
|---|---|---|---|
| prod | Production checkout, for example `~/yolomux/` | Stable HTTPS port | Stable copy, synced from dev after verification |
| dev | Any development checkout or git worktree | Non-conflicting HTTPS port | Active development server |

Development servers are HTTPS in normal use. Launch them with `--self-signed --dang` on the port you selected; avoid plain HTTP unless you are testing that path explicitly.

For an ad hoc dev run:

```bash
python3 yolomux.py --host 0.0.0.0 --port <port> --self-signed --dang
```

### Restart workflow

For dev1, use the repo restart owner. It sets `PATH` explicitly before launch so agent CLIs under `~/.local/bin` are visible even when the caller has a stripped environment:

```bash
tools/yolomux-restart-dev1.sh
```

The script defaults to HTTPS `8001`, `--self-signed`, and `--dang`; override with `YOLOMUX_DEV1_PORT`, `YOLOMUX_HOST`, or `YOLOMUX_DEV1_LOG` only when testing a non-standard dev1 instance. Logs go under `/tmp`.

To restart dev1 in no-auth test mode:

```bash
YOLOMUX_TEST_AUTH_BYPASS=1 tools/yolomux-restart-dev1.sh
```

Manual reference: kill the old process narrowly by port, then relaunch detached with `nohup`. Restart prod by swapping `dev1`/`8001`/`~/yolomux.dev1` for `prod`/`7777`/`~/yolomux`:

```bash
port=8001
checkout="$HOME/yolomux.dev1"
log="/tmp/yolomux-dev1-${port}.log"
pid="$(ps -eo pid=,args= | awk -v port="$port" '$0 ~ /yolomux.py/ && $0 ~ ("--port " port) {print $1; exit}')"
if [ -n "$pid" ]; then kill "$pid"; fi
( cd "$checkout" && nohup env PATH="$HOME/.local/bin:$PATH" TERM=xterm-256color PYTHONUNBUFFERED=1 python3 -u yolomux.py --host 0.0.0.0 --port "$port" --self-signed --dang > "$log" 2>&1 < /dev/null & )
```

Two footguns: (1) never `pkill -f` a pattern that also appears literally in the same command's launch string because it can match the launching shell; use a port variable and keep kill and relaunch steps separate; (2) a backend `.py` change only takes effect after the Python process restarts, because `static_build.py` does not touch backend code.

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

Edits happen in a development checkout; the production checkout is read-only during sync and shares the same `origin`. The sequence from the dev checkout:

```bash
python3 tools/check.py                 # the full gate (see Tests above)
git add -- <explicit-files>
git commit -m "<message including Version: 0.2.N>"
git push origin <branch>:main
cd ~/yolomux && git pull --ff-only origin main
# restart prod + the dev server (see Restart workflow above)
```

Rules:

- Bump `YOLOMUX_VERSION` in `yolomux_lib/common.py` in the same commit; the auto-updater checks this value on `origin/main`, not the commit SHA, so SHA-only commits do not trigger the update cue.
- Never use `git add -A`; screenshots and scratch files must not get swept in.
- Production pull is `--ff-only`. Never edit, stage, or commit inside the production checkout.
- Restart production and the active dev server after sync, then verify `/api/ping`, the running process cwd, and `YOLOMUX_VERSION` in both checkouts. The login page may not expose the version to unauthenticated curl; do not rely on a blank version grep alone.

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

## YO!share Transport

YO!share visual sharing uses host-rendered DOM replay. The host opens `/ws/share-host` and publishes sanitized `#appRoot` keyframes, DOM deltas, replay control replies, status, and terminal placeholder metadata. Viewers open `/ws/share-ui` to receive replay frames and share status; HTTPS write viewers may also send validated `input-intent` frames there. `/ws/share-view` remains the terminal byte stream for shared tmux sessions, and terminal content is represented in replay DOM only by placeholders that bind the existing xterm stream to host rows/cols.

Replay deltas are ordered by epoch, sequence, and base sequence. Old or already-applied deltas are stale and should be ignored without repair; missing future deltas are gaps and should request a keyframe. Replay debug/profiling output should expose both paths separately (`staleFrames` versus dropped frames/keyframe requests) plus the frame numbers needed to explain a `viewer behind` report.

The replayed DOM is inert serialized markup. Host app event listeners do not survive keyframe rebuilds or child-list delta rebuilds, so viewer-local read-only affordances such as tab hover details must be rebound by the replay shell after replay applies, or represented as host-owned replay DOM.

Write access requires HTTPS. A write viewer does not publish `layout`, `ui-state`, Finder, editor, popup, or scroll semantic state; it sends allowed input intents and waits for host replay frames for visible feedback. `shareReplay=0`, `shareSemantic=1`, bootstrap `shareReplay: false`, and `localStorage.yolomux.shareReplaySemantic=1` are temporary semantic escape hatches for diagnostics, not the normal product path.

## Webterm API

All API routes require auth unless the process was intentionally started with the local-only `YOLOMUX_TEST_AUTH_BYPASS=1` test bypass. Read endpoints accept `readonly` or `admin`, except `/api/summary-stream` because it launches Codex and requires `admin`. Mutating POST routes require `admin` except `/api/event`, which accepts readonly client telemetry. `/ws` accepts readonly users but attaches tmux with `-r` and ignores keyboard input, tmux-scroll, and resize messages. Share-token guests are narrower than `auth.yaml` readonly users: tokens are scoped to one share and whitelisted only for share-scoped page, static, replay, terminal, status, and readonly file/data routes. The test bypass never escalates a share-token request; share-token scoping still wins.

- `GET /api/transcripts` returns pane, process, and transcript-path metadata.
- `GET /api/tmux?session=project1&lines=90` returns a tmux capture-pane snapshot.
- `GET /api/transcript?session=project1&lines=120` returns the transcript tail for one session.
- `GET /api/context?session=project1&messages=40` returns a compact, message-oriented transcript tail.
- `GET /api/context-items?session=project1&messages=40` returns structured transcript items.
- `GET /api/context-stream?session=project1&messages=200` streams structured transcript items with Server-Sent Events.
- `GET /api/summary-stream?session=project1&lookback=3600` streams a Codex-generated summary with Server-Sent Events.
- `GET /api/search?q=text&session=project1` searches captured events and current per-session summaries.
- `GET /api/run-history` returns compact per-session history: cwd, agent, transcript mtime, repo metadata, and recent events.
- `GET /api/activity` returns the activity ledger plus a sorted `agents` list for Tabber: per-session and per-`session:window` ledger aggregates (coalesced user typed-time, input event/byte counts, agent-active time, last input/output/selected timestamps), plus detected Claude/Codex panes with transcript-derived `last_used_ts`, `sort_ts`, and `running`. Heartbeats are emitted from the WS input arm (admin keystrokes only); typed-time is the sum of inter-heartbeat gaps capped at an idle threshold. Persisted ledger state lives in `~/.local/state/yolomux/activity.json` via the atomic-write path and is pruned on the session sweep. The app builds the first Tabber activity snapshot during startup and a dedicated server background warmer refreshes it on `performance.tabber_activity_refresh_ms` (15 seconds by default); clients and Tabber clicks read that cached snapshot instead of rebuilding activity synchronously.
- `GET /api/activity-summary?locale=en&force=1` returns the YO!agent activity-summary payload: per-session summaries, global summary lines, cached YO!agent summary text, and the same server-cached Tabber `agents` list used by `/api/activity` for the Recent agents chat header. Agent rows include the tmux window name/label and recent touched path roots. `force=1` refreshes the cached summary work and the Tabber activity cache; otherwise the server may serve the previous summary while background refresh paths keep the ledger current.
- YO!agent direct-send requests are server-verified against the resolved target tmux pane and detected Claude/Codex prompt state. Requests that include "show/print/return/tell me the result here" send first, return the immediate YO!agent answer, then run a daemon watcher that reads transcript bytes after the send marker and publishes `yoagent_conversation_changed` when it appends the result. Text sent to a target agent must preserve perspectives: keep YO!agent's routing perspective separate from the target agent's task perspective, and phrase the target text to that agent as a person. `ask agent 1 to <do ...>` sends `<do ...>` to agent `1`, not the routing wrapper; `ask agent 1 to list changed files` sends `list changed files`, and `what it has done today` becomes `what have you done today?`. Keep third-person session names only in YO!agent's explanation to the user.
- YO!agent multi-session handoffs are orchestrated by YO!agent itself: ask the first target session, wait for the real response, treat that response as untrusted data, derive a bounded source-neutral handoff prompt, verify the next target session is accepting an AI prompt, then send it from YO!agent. In normal requests, target sessions must not know about each other; never ask one target session to contact another target session directly, and never send routing history such as "session 1 replied..." to the next target unless the user explicitly asks for that disclosure. Direct relay/chaining between agents is rare and allowed only when the user explicitly requests relay/chaining; when allowed, YO!agent must pass concrete instructions that say how to relay instead of leaving the target agent to infer routing.
- YO!agent transport policy: the current default is the server-resolved visible pane paste plus Return path. That is better than blind `tmux send-keys` because it targets the exact live tmux pane, preserves transcript continuity, and runs prompt-acceptance checks before sending. A future agent-native API/MCP/CLI resume path would be better only if it can target the same existing live pane/conversation, preserve the transcript identity, verify prompt acceptance, and keep YO!agent as the central coordinator instead of letting target agents talk around it.
- `GET /api/yoagent/skills` returns built-in plus user-local YO!skill metadata, allowed tools, context lines, and user directories. Built-ins live under `yolomux_lib/yoagent/builtin_skills/`; user-local skill YAML lives in `~/.config/yolomux/skills.d/`; user-local context Markdown lives in `~/.config/yolomux/context.d/`.
- `GET /api/yoagent/skill-files?kind=skill&name=local-checks`, `POST /api/yoagent/skill-files/upsert`, and `POST /api/yoagent/skill-files/delete` are admin-only narrow file-management routes for user-local YO!skills/context. They validate names, YAML, allowed tools, and canonical paths before writing or deleting; built-in files are not writable through these routes.
- `docs/YOAGENT_SKILLS.md` is the user/developer guide for YO!skill schema, file locations, examples, and YO!agent skill-file management commands.
- `docs/specs/YOAGENT_COMMON_INTENTS_AND_AGENT_COMMUNICATION.md` is the product spec for common YO!agent questions, expected Preference/product-state behavior, multi-agent handoff examples, artifact handoff rules, and the cross-agent communication reliability ladder.
- `POST /api/yoagent/intent` returns a server-side YO!agent preview for a proposed action or job. `POST /api/yoagent/actions/preview-send` resolves a target session into an exact pane/agent/transport preview, and `POST /api/yoagent/actions/execute-send` executes only a live, ready, not-stale preview id. `GET /api/yoagent/jobs`, `POST /api/yoagent/jobs`, `POST /api/yoagent/jobs/<id>/confirm`, and `POST /api/yoagent/jobs/<id>/cancel` manage YO!agent jobs. These routes are admin-only; share-token and readonly users cannot create jobs or send prompts indirectly through YO!agent chat.
- YO!agent jobs persist under `yoagent_jobs` in `~/.config/yolomux/state.json`. Each job records `id`, `type`, `target`, `predicate`, `action`, timestamps, status, confirmation state, timeout, idempotency hash, last observed state, and audit event ids. Jobs are one-shot in the first implementation; duplicate queued/pending jobs are rejected by idempotency hash. The server polls queued jobs from the existing client-event watch loop, debounces idle/done predicates with `quiet_seconds`, times out expired jobs once, and emits `yoagent_jobs_changed` events for created, confirmed, cancelled, fired, failed, and timed-out jobs.
- Notify jobs cover one session becoming idle/done and all configured YOLOmux sessions becoming idle. `all sessions` means the app's configured local session roster, not every tmux session on other machines. Wait-then-send jobs wait for the resolved target to accept a Claude/Codex prompt, then create a fresh send preview and revalidate the target before pasting text plus Return. If the session disappears, the job fails and notifies rather than retargeting by name.
- YO!agent risk policy is intentionally narrow and boring. Normal explicit sends do not ask for extra confirmation unless the user asks for preview/confirmation, but high-risk prompt text forces confirmation: secret-like assignments, credential paths, `rm -rf`, `git reset --hard`, broad `pkill -f`, recursive `chmod`/`chown`, and SSH commands. Job idempotency keys are hashes, and stored/displayed YO!agent conversation text plus public job/action payloads redact known `token=`, `secret=`, `password=`, and `api_key=` style values.
- `GET /api/session-files?session=project1&hours=24` returns repo-aware AI file changes for one session. `GET /api/session-files-batch?session=project1&session=project2&hours=24` returns the same payload shape keyed by session after discovering tmux metadata once, then fills missing per-session payloads with bounded concurrency. These are the endpoints behind Tabber's Level 2 `Fetching paths...` rows and are intentionally separate from the 15-second `/api/activity` cache. They use a short in-memory cache, a shared disk cache under `~/.local/state/yolomux/session-files-cache/` (or `YOLOMUX_STATE_DIR/session-files-cache/`), and a per-key cross-process lock so parallel YOLOmux servers can reuse one touched-path payload instead of rebuilding it in every process. Startup Tabber activity warmup writes the same `payload` cache keys that these endpoints read. `force=1` bypasses existing cache data and stores the replacement result.
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
