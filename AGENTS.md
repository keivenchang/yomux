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

### Recent agent learnings

- Start with the local task files (`DOIT*.md`, `TODO.md`, `README.md`) and re-read them after each batch. The user updates these files while work is in progress, and stale assumptions caused repeated misses.
- Separate feature completion from cleanup. Commit/push/sync the completed behavior first when asked, then do refactor work as a distinct pass so regressions and review diffs stay easier to reason about.
- Treat visual bugs as layout-state bugs, not one-off CSS tweaks. Reproduce with the exact URL/layout/tabs query string or screenshot state, then add a layout/browser assertion that checks the failing geometry or DOM contract.
- Do not hard-code menu widths, tab capacity, pane sizes, or dropdown behavior for the current browser. Earlier fixed-size guesses broke on different viewport/font/pane combinations. Measure the container/content and use shared CSS variables, flex/grid, percentages, and viewport clamps.
- Keep all pane/tab/window controls on shared helpers. Inconsistent active borders, Mac/PC close/minimize glyphs, pane focus rings, and file-editor tab buttons came from duplicated local button code. If two controls look or behave the same, move the state sync into a helper before adding another branch.
- Preserve Finder as a pane with special width behavior, not as an overlay. Left/right splits should keep Finder from auto-expanding horizontally when its sibling tab closes; top/bottom siblings can expand when the last normal tab is gone. Empty placeholder panes are part of preserving user-chosen split sizes.
- Menu hover/focus behavior is global UI state. Auto-focus is not just terminal panes; it includes menus, popovers, Finder, Preferences, editors, and pane focus rings. If auto-focus is off, hover should not auto-open menus until the user has explicitly opened a menu.
- Popovers need shared timing and ownership. Tab, image, menu, and file-preview popovers flickered because separate timers competed. Prefer one controller per popover family, with explicit show delay, follow delay, hide delay, and close-on-topbar/pane transitions.
- Generated assets are not source. Edit `static_src/js/yolomux/*.js` and `static_src/css/yolomux/*.css`, then run `python3 tools/static_build.py`; use `python3 tools/static_build.py --check` to catch stale `static/yolomux.js` or `static/yolomux.css`.
- Validate browser UI with both code-level and browser-level tests. `node tests/layout_url.test.js` catches URL/layout logic and generated-source contracts; Selenium tests catch real geometry, clipping, alignment, and toolbar regressions. Full pytest needs local socket access, so sandbox failures with `PermissionError: Operation not permitted` should be rerun outside the sandbox before treating them as product failures.
- Keep screenshots and local debug artifacts out of commits. `cps` must stage explicit files only; untracked `20260*.png` files in `~/yolomux.dev` or `~/yolomux` are local evidence and should remain unstaged.
- For file drag/drop into terminals, browsers may expose dragged files as rich data that Codex/Claude renders as `[Image #...]`, while the shell still receives usable text. Prefer `text/uri-list` plus plain text path data for drag payloads, and test both browser rendering and terminal input behavior.
- File editor/viewer behavior should be capability-based. Markdown/HTML get edit/preview/split; source files get editor/search/diff actions only; large or binary files should show a clear too-large/binary state instead of trying to load everything.
- Context menus and toolbar buttons should carry accessibility state from shared builders: `title`, `aria-label`, `aria-pressed`, `aria-checked`, disabled state, checked state, and keep-open behavior. Missing shared handling caused repeated "same-looking controls behave differently" bugs.
- Prefer small, named state helpers over repeated expressions. Examples: active/pressed button syncing, upload filename defaults, generated upload detection, repo-path matching, and target-session selection. Repeated inline logic was where subtle precedence and drift bugs appeared.
- Keep tests tied to the bug that happened. When a user reports "this exact URL collapses a pane" or "this toolbar shifts after Finder click", add that exact URL/state to a regression test rather than only testing the helper in isolation.
- Finder, Differ, and Modified-files must share tree-row, date/sort, status-chip, icon-column, and toolbar helpers. If one pane gains or changes a control (`None`/`Date`/`Ago`, `A-Z`/`Z-A`/`new`/`old`, git status badges, Codex/File icons), apply it through the shared path in the same change; local one-pane fixes caused repeated visual drift.
- Treat toolbar/icon alignment as a geometry contract. For visual bugs like misaligned blame/diff circles or Finder/Differ header icons, add a browser assertion that compares actual bounding boxes across the affected panes and themes; screenshots alone did not catch the recurrence.
- CodeMirror live toggles must exercise the real UI event path. Wrap, line-number, blame, diff, and theme changes should reconfigure compartments or dispatch state effects without rebuilding the editor, and a browser regression should click the button and assert the document text remains visible. Source-grep tests missed a runtime `scheme is not defined` blank-editor failure.
- Git editor actions are capability-gated as a pair. Blame and diff buttons should appear together only for files inside a git repo with meaningful committed history; untracked, no-history, or outside-repo files should hide both rather than showing disabled/mismatched controls.
- Large Markdown cleanups still need surgical edits. Do not generate a replacement file under `/tmp` and overwrite the tracked Markdown with `\cp`; use `apply_patch` or a reviewed script-generated patch so concurrent user edits and unintended deletions are visible in the diff before the file changes.
- In the YOLOmux repo, `cps` means the `yolo-cps` skill, not the Dynamo-utils `dyn-cps` flow. It requires the version bump, the full local check set, explicit staging, push, production fast-forward sync, and restarting BOTH prod `7777` and dev `7778`.
- Good pattern to keep: after each substantial UI change, run `python3 tools/static_build.py --check`, `node --check static/yolomux.js`, `node tests/layout_url.test.js`, `python3 -m py_compile ...`, `python3 -m pytest tests ...`, and `git diff --check`. Report any sandbox-only failures separately and rerun them with the right permissions.
- Implementer reflections (DOIT.7 retro, 2026-06-01): the rules below are what would have saved time on the build side of the DOIT.5/6 batches.
- Pinned literals live in MULTIPLE test layers — grep before you change one. Every exact CSS token, label, glyph, or constant you edit is usually asserted in more than one place: `tests/layout_url.test.js` (node source-grep), `tests/test_browser_layout.py` (Selenium computed `rgb(...)`), and pytest. This session a single token change (`--pane-tab-unfocused-active-bg` `#285a2f`->`#4f9e3a`) broke both a node source-guard and a Selenium computed-color assertion, and one label (`Branch Info`->`YO!info`) broke three separate node pins. Before changing a pinned value, `grep -rn '<old-literal>' tests/` and update every pin in the same change.
- Reproduce in the harness BEFORE theorizing about whether code is "correct". The vm-built `api` calls bundle internals directly; `tabStrip`/`tabElement` mocks (with a `.rect`) exercise drag-placement math; `prompt_detector` runs against hand-built pane fixtures. For the left->right drag bug I argued from the source that the insert index was already correct for too long — building a 2-tab mock and printing the returned index showed it was a directional-threshold problem (neighbor CENTER vs FAR edge), not the off-by-one the item guessed. Build a failing repro first; it is faster than reading.
- When a DOIT item carries "STUDIED FINDINGS" / "naive fixes that are wrong", implement the prescribed durable fix, not your first instinct — the diagnosing agent has usually already tried and rejected the naive path. The Ctrl-T auto-approve fix is the case in point: enumerating todo glyphs (my first move) was exactly the fragile approach the item warned against; the durable fix was a bounded-overlay `break` at the `^\d+\s+tasks?\s+\(` header. And before adopting a "defense-in-depth" suggestion, confirm it does not contradict an existing guarded invariant — the suggested `codeMirrorConfigSignature` scheme key is explicitly forbidden by a test (it forces full editor rebuilds); the compartment swap (`refreshOpenEditorThemePanels`) is the right mechanism, so I used that and skipped the signature change.
- Cross-realm test equality: values returned from the vm-context bundle have a different prototype than the test realm, so `assert.deepStrictEqual` fails with "same structure but not reference-equal". Spread them first (`[...api.fn()]`) or compare primitives.
- The DOIT/TODO checkbox is the shared source of truth, not your internal task tracker. Flip `[ ]`->`[x]` with a DONE note in the DOIT file as part of finishing each item (I once marked internal tasks done but left the DOIT boxes unchecked, so a re-read showed stale "open" items). Re-read the exact line immediately before editing — these files change under you mid-batch, and concurrent edits cause "file modified since read" churn.
- Push the root cause back to the diagnosis. When the diagnosed cause is wrong, state the ACTUAL cause in the DONE note (drag-reorder was a threshold asymmetry, not an index off-by-one; the "PR pillbox" was a `ready-review` state badge, not a PR chip). The `file:symbol + FIX + Validate` contract only gets better if the implementer corrects it instead of silently coding around it.
- Build + test after EACH item, not per batch. Every item this session ended with `tools/static_build.py` + `node tests/layout_url.test.js` (and pytest for backend) before moving on; that caught pinned-value regressions at the item that caused them instead of in a confusing end-of-batch pile.

### Recurring pitfalls (2026-06-01 retro)

Distilled from the DOIT.5/DOIT.6 batches (see `DOIT.7.md` for the concrete failures behind each). These are the bug CLASSES that recurred — check them by default.

- Setting/theme propagation: a global setting change must re-apply to EVERY live surface, not just the obvious one. When you touch `appearance.*` or any global mode, walk the full list — app chrome, OPEN editors (CodeMirror), terminals, tab badges, YO markers, popovers — and confirm each re-applies. New themed components need BOTH a `body.theme-light` and dark rule from the start. (Caused: illegible light-mode badges, View->light not re-theming open editors, terminal theme left behind, stale YO markers.)
- Cache/signature completeness: any memoization or short-circuit key (e.g. `codeMirrorConfigSignature`) must include every input the output depends on, or a real change will no-op. Prefer a cheap live reconfigure (CodeMirror compartment swap) over a full re-render that can short-circuit.
- Detector chrome vs content: text-scraping detectors (`prompt_detector.py`) must treat persistent CLI chrome (Ctrl-T task overlay, input box, mode hints, footers) as NON-activity; recognize bounded overlay blocks (stop at the header) instead of blanket "anything after the footer" rules; and add a fixture for every new Claude/Codex UI variant.
- No silent duplicate code paths: equivalent user actions (double-click vs drag, two open routes) must funnel through ONE shared function; and watch for a function defined in two concatenated `static_src/js/yolomux/*.js` partials — the last one silently wins. Grep for duplicate definitions.
- Toggles toggle; don't double-show: a control with a checked/visible indicator should open AND hide. Never render the same fact twice (drop the native `title` when a custom popover exists; remove a redundant state label when a dedicated badge exists).
- Right default view mode on open: the file-open path must choose the mode per context (markdown -> preview, untracked/all-added -> edit), not fall through to one default.
- Honest affordances: a control's label/aria/tooltip must match what it actually does, or make it do what it says.
- Stale bundle is not a fix: a frontend change is unverified until `tools/static_build.py` rebuilds and the page is hard-reloaded. Never call a frontend fix done from source inspection alone.
- Stale BACKEND is also not a fix — and it has NO build step to remind you. A Python change (`prompt_detector.py`, `app.py`, `auto_approve_worker.py`, `metadata.py`, `settings.py`, `web.py`, anything under `yolomux_lib/`) only takes effect when the running `yolomux.py` SERVER process is RESTARTED — Python does not hot-reload, and `tools/static_build.py` does not touch it. Passing pytest + a DONE note prove the SOURCE is right, not that the LIVE server runs it. Symptom seen this session: the Ctrl-T auto-approve fix was correct and tested, but the live server still hung because it was started BEFORE the fix landed (and two servers were running — the older one stale). Before reporting a backend behavior fix as live, restart the server (and make sure only ONE instance owns auto-approve — multiple `yolomux.py` instances on the same tmux sessions contend on the auto-approve lock, so a stale older instance can keep serving the old code). Reproduce a backend fix against the actual running process, or restart it, before calling it done.
- Pinned-literal regressions (implementer addendum): a value worth pinning is usually pinned in more than one test layer (node source-grep + Selenium computed style + pytest). Treat any token/label/glyph/constant change as a multi-file edit — grep `tests/` for the old literal and update all pins together, or "correct" changes break CI.
- Reproduce-before-theorize (implementer addendum): the harness can run the real logic (vm `api`, mock `.rect` geometry, detector pane fixtures). A failing repro disambiguates root cause faster than reasoning from source — and stops you from implementing the wrong fix for a misdiagnosed cause (the drag-reorder "off-by-one" was actually a center-vs-far-edge threshold).

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

When the user says **"cps"** (`commit, push, sync`) in the YOLOmux repo, follow the `yolo-cps` skill. The short version from `~/yolomux.dev/` is:

```bash
python3 -m py_compile yolomux.py tmux_wall.py auto_approve_tmux.py yolomux_lib/*.py
python3 -m pytest tests -n 4 -q
python3 tools/static_build.py --check
node --check static/yolomux.js
node --check static/tmux-wall.js
node tests/layout_url.test.js
git add -- <explicit-files>           # never `git add -A`; PNG screenshots and scratch files must not get swept in
git commit -m "<message including Version: 0.1.N>"
git push origin main
cd ~/yolomux && git pull --ff-only origin main
systemctl --user stop yolomux-prod-7777 2>/dev/null
systemd-run --user --quiet --collect --unit=yolomux-prod-7777 ~/.local/bin/yolomux-restart-prod.sh
systemctl --user stop yolomux-dev-7778 2>/dev/null
systemd-run --user --quiet --collect --unit=yolomux-dev-7778 ~/.local/bin/yolomux-restart-dev.sh
```

Rules:
- `cps` is the alias. Same as `commit, push, sync`.
- Bump `YOLOMUX_VERSION` in `yolomux_lib/common.py` in the same commit.
- Run the full check set above before committing. If socket/browser tests fail under sandboxing with `PermissionError: Operation not permitted`, rerun the same command outside the sandbox before treating it as a product failure.
- PRODUCTION pull is `--ff-only`. Never edit, stage, or commit inside `~/yolomux/`.
- Restart both prod and dev after every `cps`, then verify `https://localhost:7777/api/ping`, `https://localhost:7778/api/ping`, and the rendered version on both login pages.
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

The main server entry point is `yolomux.py`, which delegates to `yolomux_lib/cli.py`. Request routing lives in `yolomux_lib/server.py`, application state and tmux actions live in `yolomux_lib/app.py`, and shared helpers live in smaller modules such as `metadata.py`, `sessions.py`, `session_files.py`, `transcripts.py`, `uploads.py`, `events.py`, and `websocket.py`.

Frontend source for the interactive UI lives in ordered partials under `static_src/js/yolomux/` and `static_src/css/yolomux/`. Run `python3 tools/static_build.py` after editing those partials; it regenerates the served single-file outputs `static/yolomux.js` and `static/yolomux.css`, and `python3 tools/static_build.py --check` fails when generated assets drift. Python keeps only the small HTML shell in `yolomux_lib/web.py`, plus bootstrap JSON and versioned static asset URLs. The main app's non-tmux tab types are centralized in the `TAB_TYPES` registry in the JS source partials; add future virtual/editor/viewer tabs there before adding scattered predicate or label branches. The read-only wall has its own frontend files, `static/tmux-wall.js` and `static/tmux-wall.css`, so `tmux_wall.py` stays focused on tmux capture, JSON endpoints, and Server-Sent Events.

The standalone auto-approval detector lives in `auto_approve_tmux.py` at the repo root. YOLOmux imports it as a Python module and wraps one `AutoApproveWorker` (in `yolomux_lib/auto_approve_worker.py`) around each enabled session.

## Local checks before committing

```bash
python3 -m py_compile yolomux.py tmux_wall.py auto_approve_tmux.py yolomux_lib/*.py
python3 -m pytest tests -n 4
python3 tools/static_build.py --check
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
- `GET /api/search?q=text&session=project1` searches captured events and current per-session summaries.
- `GET /api/run-history` returns compact per-session history: cwd, agent, transcript mtime, repo metadata, and recent events.
- `GET /api/session-files?session=project1&hours=24` returns repo-aware AI file changes for one session. Claude changes come from edit tool calls; Codex changes come from `apply_patch`; repo status comes from git.
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
