# YOLOmux TODOs

YOLOmux-specific roadmap notes. Keep UI, terminal, YOLO approval, session state, and launch ideas here instead of the Project TODO list.

---

## Product Direction

YOLOmux should stay a lightweight local tmux browser control plane. The useful niche is not another full SaaS-style orchestration stack. It is a fast local UI for existing Claude/Codex/tmux sessions with clear state, safe YOLO controls, repo metadata, file paste/upload, and low-friction attach/reply.

Borrow from other tools only when the feature improves the local control loop: know which session needs attention, understand what changed, approve or block risky work, and jump back into the right terminal quickly.

---

## For The Next Agent (orientation)

- Build/test/restart/CPS commands live in `docs/DEVELOPMENT.md` (one gate: `python3 tools/check.py`). AI-agent behavior rules and recurring-pitfall lessons live in `AGENTS.md`. Do not re-inline those commands here.
- Live servers run side by side — prod `:7777` plus one or more dev worktrees (`:8001`-`:8004`, see the table in `docs/DEVELOPMENT.md`). A settings or roster change should reflect on every running instance.
- Code map: entry `yolomux.py` -> `yolomux_lib/cli.py`; HTTP routing `yolomux_lib/server.py`; app state + tmux actions `yolomux_lib/app.py`; session/agent discovery `yolomux_lib/sessions.py`; repo/PR/CI metadata `yolomux_lib/metadata.py`; file ops `yolomux_lib/filesystem.py`; shared helpers + paths `yolomux_lib/common.py`; HTML shell `yolomux_lib/web.py`; frontend partials `static_src/js/yolomux/*.js` -> generated `static/yolomux.js`; approval detection `yolomux_lib/approvals.py` + worker `yolomux_lib/auto_approve_worker.py`.
- State lives in `~/.config/yolomux/state.json`; the YOLO event log (great for confirming state-machine behavior) is `~/.local/state/yolomux/events.jsonl`.
- NAVIGATION RULE: line numbers in this TODO drift because the source changes constantly. Always GREP THE NAMED SYMBOL (`function foo`, `def foo`, an `id=`/class string) rather than trusting a line number.
- DOIT files are the active work queues; `docs/DONE.md` is the archive. As of 2026-06-13, the active DOIT.63-65 files were archived and removed. The only carried-forward item from the older era is DOIT.11's permission-hook wiring into `~/.claude/settings.json`, which remains user-gated and tracked in Big-Bang #1.

---

## Big-Bang Tasks (high-leverage, multi-day — proposed 2026-06-03)

The open `[ ]` items below are mostly small polish. These are the larger, architecture- or reach-changing efforts worth a dedicated push. Ranked by leverage.

RECOMMENDED SEQUENCE (2026-06-04, after DOIT.36 archive): (1) Dev-velocity trio — FRONTEND live-reload (`static_build.py --watch` + a dev-only SSE/WS reload channel), BACKEND hot-reload (`--dev` re-exec on `yolomux_lib/*.py` change + a stale-second-instance guard), and the HEADLESS SCREENSHOT HARNESS (`tools/snapshot.py` so the agent verifies its own UI); these pay for themselves across all remaining UI polish (see "Dev velocity" section below). (2) DURABLE FIX B — the CSS light-mode rule-pairing lint, to stop light-mode regressions at the source. (3) Tier-3 quick wins — close out the shipped-DOIT follow-ups that need a live agent screen / human eyeball / a small decision (DOIT.17 live-validate, DOIT.21 keyboard chords, DOIT.22 clickable chips, DOIT.26 all-lines blame, ring-opacity eyeball). (4) Then ONE Tier-5 bet — reliable auto-approve via structured channels (DOIT.11 hook wiring + Codex app-server supervisor) is the highest-leverage correctness win. Most Tier-3 items are human-gated (live validation / eyeball / product decision), so they can't be cleared headlessly.

- [ ] [XL] RELIABLE AUTO-APPROVE FOR BOTH AGENTS — replace TUI pixel-scraping + keystroke injection with structured permission channels. (1) CLAUDE: finish the carried-forward DOIT.11 permission-hook wiring — wire the built `claude_permission_hook.py` `PreToolUse` hook into `~/.claude/settings.json`, live-validate (allow/ask/deny, deny-wins, error→prompt fallback), and stand the keystroke worker down for Claude. (2) CODEX: build a `codex app-server` JSON-RPC supervisor so YOLOmux answers each `item/commandExecution/requestApproval` programmatically (own the process instead of scraping the pane). WHY: the recurring footer/spinner/wrap detection bugs (this whole session) all stem from deciding-from-pixels-then-typing; structured channels delete the class. Decisions still flow through the existing `yolo_rules.py` engine, so behavior/UX is unchanged. The former DOIT.10 decision kept the scraper only as a fail-safe fallback.
- [ ] [XL] LAYOUT/RENDER RECONCILIATION — one keyed renderer, zero per-pane special-casing. DOIT.9 patched the worst tab-move latency, but `applyLayoutSlots` still tears down + rebuilds the whole grid AND topbar on every mutation, and dimming/overlays are installed per-pane with `isVirtualItem` branches (the inactive-pane-dim refactor + the separator stack are symptoms). Make the grid, topbar, and tab strips a single keyed/reconciling renderer driven by the layout state, so every mutation is a minimal diff and NO pane type has bespoke logic. WHY: kills layout jank and the whole "this pane behaves differently" bug family the user keeps hitting.
- [ ] [XL] EDGE-PINNED TABS — make Finder/Differ/Tabber a normal virtual tab with a declarative `edge:'left'` pin, persistent default-open policy, generic hidden-by-user state, and generic min-size/rendering metadata instead of bespoke Finder placement code. DOIT.65 shipped the stopgap self-heal and zero-area Dockview guards; the structural fix is to fold Finder into a generic `tabPin` / `edgePinnedItems` / `reconcileEdgePinnedItems` model, then migrate the remaining `isFileExplorerItem` placement/prune/min-width/adoption branches to generic predicates in one parity-tested change.
- [ ] [XL] RESPONSIVE / MOBILE + WEB DELIVERY (P7) — YOLOmux is desktop-browser-shaped today. Add a responsive layout (single-column stacked panes, touch-friendly tab nav, touch drag-reorder, larger hit targets), make the editor/diff usable on a phone, and define a hosted web path. WHY: the product vision ("web and mobile coming soon") needs the UI to survive small screens and touch first.
- [ ] [L] MULTI-MACHINE CONNECTOR (P9) — watch and drive tmux + agents across several hosts from one UI (one pane of glass over the dynamo1–4 / CI hosts you already run). Needs a connection model (SSH/agent), per-host auth, and the layout/state code generalized beyond one local tmux server. WHY: the real workflow already spans machines; today YOLOmux is single-host.
- [ ] [L] WORKTREE-AWARE PARALLEL AGENTS + TASK HUB (P0 + P5) — git worktree management so multiple agents work isolated branches in parallel, plus a real Task Hub (the For-Review → In-Progress → Complete flow from the product vision) that aggregates tasks/changes across sessions and worktrees into one board. WHY: this is the core "identify → manage → automate tasks" product story; the session-state model exists but the hub + worktree isolation do not.


### Active bugs (2026-06-04, from live use)
Small `[S]` follow-ups from this batch are archived in `docs/DONE.md`.

## Dev velocity — bottlenecks & speedups (analysis 2026-06-03)

Measured: the BUILD is not the bottleneck — `tools/static_build.py` is **36ms** (12 JS + 7 CSS partials), `node tests/layout_url.test.js` **0.65s**, a unit pytest file **0.78s**. The real cost is the edit→reload→verify LOOP, the SLOW test tail, and the human screenshot round-trip. Ranked by leverage:

- [x] [M] PARALLELIZE + TIER the tests — DONE 2026-06-11: `tools/check.py` now runs the old full gate as independent default lanes in parallel, supports `--serial`, `--list-lanes`, and repeated `--lane <name>` selectors, adds focused unit/socket/browser lanes by registered pytest markers, and collects known slow tests first. Remaining CI work stays tracked below.
- [ ] [M] ADD CI (there is none — no `.github/workflows/`). A GitHub Actions gate running `static_build.py --check`, `node --check static/yolomux.js`, `node tests/layout_url.test.js`, `py_compile`, and `pytest -n auto` (Selenium in a separate job) catches pinned-literal regressions and stale-asset drift automatically, so local iteration stays fast and "did I rebuild?" is enforced by the gate, not memory.
- [ ] [M] KILL the pinned-literal multi-layer tax (AGENTS.md documents this biting repeatedly: one token/label change breaks node-source-grep + Selenium computed-style + pytest pins). FIX: a single source of truth — have tests READ token values from the built CSS / locale JSON instead of hardcoding them, or generate the pin manifest; failing that, a `tools/list-pins.py <literal>` pre-flight that prints every test asserting a literal before you change it. Turns an N-file edit into one.
- [x] [S] SANDBOX-friendly tests — DONE 2026-06-08: added a registered `socket` pytest marker, marked the localhost login/auth integration tests, and documented `python3 -m pytest tests -m "not socket" -q` as the restricted-sandbox fast lane. Remaining socket-like tests are mocks/source inspections or already avoid real binds.

## Priority Roadmap

### P0: Menu-Bar Navigation (replace top tabs with real dropdown menus)

Goal: replace the always-visible top session-tab strip with a proper application menu bar — top-level menus that open dropdowns, the way a real program does. New session (Claude and Codex) moves INTO the menu; there are no more persistent top tabs. Only the active session stays shown inline.

IMPLEMENTATION STATUS (verified 2026-05-29 — NOTE: line numbers in this file drift because the source is edited constantly; GREP BY SYMBOL NAME, not line number):

- The app menu bar is ALREADY BUILT AND MOUNTED — this is a finish-wiring / gap task, NOT greenfield. The design gate below is effectively passed for the skeleton.
  - `appMenuTree()` (grep `function appMenuTree` in `static/yolomux.js`) defines the tree; `createAppMenuBar()` is appended into `#sessionButtons` (grep `createAppMenuBar`), so the old standalone session-tab strip is already replaced by the menu bar in that container.
  - Open/close, hover, keyboard nav, ARIA roles, and click-outside already exist: helpers `menuCommand` / `menuSubmenu` / `menuSeparator` / `menuGroups`, plus `appMenuIsOpen` / `appMenuCommands` / `createAppMenu` / `createAppSubmenu` / `createAppMenuCommand`.
- Current LIVE top-level menus: `File`, `View`, `tmux`, `Tabs`, `Help`. Gaps vs the design tree below:
  - NAMING DECISION (2026-05-30): the menu that lists the visible/minimized/inactive app tabs is named `Tabs`, while `tmux` is only for tmux-specific controls. The dropdown rows are separated by bars only; no `Active` / `Minimized` / `Inactive` section text.
  - File today: File Explorer, Open file, Preferences, Log out.
  - tmux today: current session YO toggle, direct `+ Claude` / `+ Codex` / `+ Term`, Transcript / AI summary / Event log / Branch Info, Rename / Kill tmux session, Resume session (disabled), YOLO rule-file actions at the bottom.
  - View today: tab-metadata toggle, Alert, Refresh, Branch Info, Layout submenu (Single pane / Grid / Wall — ALL disabled). MISSING: Filter/Sort, panel-tabs visibility.
  - Help today: Command palette, version (disabled), Keyboard shortcuts submenu, Open README.
  - NOT BUILT yet: per-pane left dropdown.

DESIGN GATE (mostly satisfied — the skeleton is built; remaining gate work is reconciling the items below against the live `appMenuTree()`):

- [~] (PARTIAL) View ▾  (display options) — LIVE: Theme, Layout (Single/Split; Grid/Wall disabled), Tab metadata, Alert, Refresh, and Sort tab list (Default/Needs-me/Name, added 2026-06-03). STILL MISSING: a "Panel tabs ▸" per-pane-tab visibility toggle (Terminal/Tx/AI/Log/Info) and an Inactive-tabs show-all/tray control. (Branch Info lives in File ▾ as YO!info.)
  - Layout ▸ : Single / Grid / Wall
  - Filter / Sort ▸ : Needs me, by state, by repo, by PR status
  - Tab metadata: show / hide (the current `#` toggle)
  - Inactive tabs: show all / tray
  - Panel tabs ▸ : toggle Terminal / Tx / AI / Log / Info visibility
  - Branch Info: show — opens the Branch Info viewer for the active session (also still available as the per-pane `Info` tab; once open it appears in the Tabs menu)
- [ ] [M] Add the remaining YOLO controls under Tmux instead of restoring a top-level YOLO menu: policy modes, Open rule file, Approval queue, Audit log, and Risk labels legend.
- [ ] [M] Add the remaining per-pane kebab actions: peek / reply.
- [ ] [M] Mobile: menus collapse into a single hamburger (ties to P7).

### P1: YOLO Event Log, Audit, And Queue

- [ ] [L] Add an approval queue view for pending high-risk actions. Start read-only first if live interception is hard.
- [ ] [M] Add per-session YOLO policy. Initial modes: `off`, `prompt-only`, `safe`, `edit`, `full`. Make policy visible on the tmux-session YOLO control.
Small risk-label cleanup is archived in `docs/DONE.md`.

### P1: YOLO Rule Engine (user-configurable matching via YAML)

YOLO matching now runs through `yolomux_lib/yolo_rules.py`. When `~/.config/yolomux/yolo-rules.yaml` exists, YOLOmux hot-reloads that ordered first-match-wins ruleset; when it does not, YOLOmux uses a built-in fallback equivalent to the previous dangerous-command denylist and keeps approving non-dangerous bash prompts. The remaining work here is policy/profile layering, not the base rule engine.

- [ ] [L] Decide scoping dimensions: global vs per-repo vs per-session, per-agent (`claude` / `codex`), and per prompt-type (`bash` / `file` / `tool`).
- [ ] [M] How does the user EDIT the rules? Today the only path is raw-YAML hand-editing: `openYoloRuleFile` (JS ~2420) POSTs `/api/yolo-rules/open` → `ensure_yolo_rules_file()` creates the file from a commented template if missing, then opens `~/.config/yolomux/yolo-rules.yaml` in the built-in text editor. There's NO structured editor. Decide: keep raw-YAML-only (cheapest; rely on the commented template + validation below), OR add a structured rule modifier (list rules with add / remove / reorder, dropdowns for `type`/`action`/`risk`, a `match` list editor, and the top-level `default:` policy) that round-trips to the YAML. A structured editor also fixes discoverability — most users won't know the file exists.
- [ ] [M] Option B — risk-class map (patterns assign a risk, risk maps to an action):

```yaml
risk_actions:
  read: approve
  edit: approve
  network: ask
  process: approve
  delete: block
  credential: block
  unknown: ask
patterns:
  delete:  ['\brm\b', '\bdd\b', '\bmkfs']
  network: ['curl', 'wget', 'ssh', 'scp']
  credential: ['~/.ssh', 'HF_TOKEN', 'GH_TOKEN']
```

- [ ] [L] Option C — profiles + scope overrides (layer on top of A or B):

```yaml
profiles:
  default: { bash: safe, file: approve, tool: ask }
  codex:   { file: approve }
sessions:
  '6': { bash: full }
```


### P3: Info Drawer And Diff Panel

- [ ] [M] Add a per-session info drawer with full path, branch, dirty/ahead/behind counts, PR, CI, Linear/issue metadata, latest summary, and recent events.

### P3: Editor — Wrap, Preview, Split-Preview

The file editor today has a single Preview toggle (`#fileEditorPreview`, `web.py`) and a working word-wrap toggle. Make the preview path first-class view modes next.

- [ ] [M] Editor power keys, round 2 (from DOIT.10 notes; all should use the platform app modifier: Cmd on Mac, Ctrl on PC, and must not steal Mac Ctrl from tmux):
  - Multi-cursor: add next match (`Cmd+D` / `Ctrl+D`), select all occurrences (`Shift+Cmd+L` / `Shift+Ctrl+L`), add cursor above/below (`Alt+Cmd+↑/↓` / `Alt+Ctrl+↑/↓`), and cursor at end of each selected line (`Shift+Alt+I`).
  - Line ops: move line (`Alt+↑/↓`), copy line (`Shift+Alt+↑/↓`), delete line (`Shift+Cmd+K` / `Shift+Ctrl+K`), insert line below/above (`Cmd+Enter` / `Shift+Cmd+Enter`, Ctrl on PC), and join lines with a non-Ctrl-on-Mac binding.
  - Selection / structure: smart-select expand/shrink with a Cmd/Ctrl binding, and go to matching bracket (`Shift+Cmd+\` / `Shift+Ctrl+\`).
  - Folding: fold/unfold (`Alt+Cmd+[` / `Alt+Cmd+]`, Ctrl on PC), fold all / unfold all if a non-confusing chord is added.
  - Format / block comment: add only when a formatter or block-comment command is wired.
  - Quick-open modes: `:NNN` go to line, `@sym` symbol search, and `>` command mode.
  - In-file symbol jump (`Shift+Cmd+O` / `Shift+Ctrl+O`) and later breadcrumbs (`Shift+Cmd+;` / `Shift+Ctrl+;`).
  - Avoid app-side Ctrl-letter bindings on Mac. Browser-reserved `Cmd+T/W/N/Q/Shift+N` remain best-effort in a browser tab and are only ownable in a PWA.
  - Already shipped: Replace/Replace all, Go to line, cursor status, line comment, indent/outdent, Find, Undo/Redo, quick-open, command palette, Finder toggle, Preferences, and best-effort close-active-tab.
  - Remaining editor display/options:
  - On-save hygiene (optional settings): trim trailing whitespace, ensure a final newline.
  - Reload-from-disk button when the file changed externally (the "changed on disk; unsaved edits kept" warn already exists — add a one-click reload).
  - Word / char / line count in the status bar.
  - NOTE: all-match highlighting, multiple cursors, bracket matching, auto-close brackets/quotes, code folding, and real auto-indent are CodeMirror-owned now; do not rebuild a second editor path.

### P4: Launch And Resume

- [ ] [L] Add a launch dialog behind `+ Claude`, `+ Codex`, and `+ Term` with cwd, agent, model/profile, permission mode, initial prompt, and optional session name.
Small quick-launch guard is archived in `docs/DONE.md`.
- [ ] [M] Add a resume picker for recent Claude/Codex conversations scoped to the selected cwd.
- [ ] [M] Add a `peek/reply` action for a session when it only needs a short response and the user does not need to attach to the full terminal.

### P5: Worktrees

- [ ] [L] Add optional worktree-backed launch mode: create worktree, branch, tmux session, and initial agent prompt together.
Small worktree info-drawer item is archived in `docs/DONE.md`.
- [ ] [M] Add cleanup guardrails: never delete a worktree with uncommitted changes; show the path and stop.
- [ ] [M] Add a read-only file browser for a session worktree before adding edit controls.

### P6: Search, Cost, And History

- [ ] [L] Add full-text search across captured session events and summaries.
- [ ] [M] Add per-session token/cost/context metrics only if they can be read reliably from Claude/Codex metadata without scraping fragile UI text.
- [ ] [M] Add a compact run history: prompt, cwd, agent, started/ended time, final state, PR, and latest summary.

### P7: Mobile And Network Use

- [ ] [L] Add a single-pane mobile focus mode with larger controls for Esc/Tab/Ctrl, paste/upload, YOLO actions, and reply.
Small network-access docs item is archived in `docs/DONE.md`.
- [ ] [M] Consider installable PWA behavior only after mobile layout is usable.

### P8: Host And Process Vitals

- [ ] [M] Add lightweight CPU/memory/load probes and per-session process trees.
- [ ] [M] Add optional `nv-smi` GPU status when available, but do not make GPU support required.
Small vitals-placement item is archived in `docs/DONE.md`.

### P9: Multi-Machine Connector

- [ ] [XL] Defer until the local product is stable. This changes auth, networking, logging, and failure modes.
- [ ] [XL] If built, use a small remote agent that reports tmux sessions, metadata, vitals, and WebSocket terminal streams back to one YOLOmux instance.
Small local-default guard is archived in `docs/DONE.md`.

### Session Share — view-only presenter URL (proposed 2026-06-06)

GOAL: a host mints a URL that gives GUESTS (no account) a VIEW-ONLY live mirror of ONE session — its currently-open window panes plus live activity ("share the screen of what I'm doing"). The URL EXPIRES after a predefined TTL OR when the host terminates (kills/renames) that session. This is a capability/bearer URL — anyone with the link can watch — so scope it hard to the one session and treat the link as a secret.

TWO GUEST TYPES — distinct, do not conflate:
- TYPE A — `auth.yaml` `readonly` account (EXISTS today): a logged-in viewer who navigates the FULL UI on their own — open any session, arrange their own panes, browse menus / Finder / transcripts — but cannot TYPE, change state, or run admin actions (input + non-`/api/event` POSTs blocked). Independent view, not tied to anyone's screen. UNCHANGED by this feature.
- TYPE B — shared-session guest (NEW, this feature): reaches YOLOmux ONLY via a share token, locked to the ONE shared session, strictly view-only AND following the host's screen. Specifically: (1) must NOT resize the shared tmux window; (2) if their browser is SMALLER than the host's terminal they see the FULL host geometry and SCROLL/pan locally to reach the bottom — their small window must never shrink what the host (or other viewers) sees; (3) they FOLLOW the host — see the host's MOUSE pointer, the host SCROLLING within panes, and which single pane the host is ACTIVE in. No menu/nav freedom, no other sessions, no file API.

What "tracks ... cursor movements" means, split by cost: (a) the TMUX TERMINAL text-cursor is FREE — xterm renders it from the live PTY byte stream, so a guest attached to the session sees it move with no extra work; (b) the host's UI activity — which pane is FOCUSED / active tab, scroll offset within panes, and the host's MOUSE POINTER — is purely client-local today and never leaves the host, so mirroring it is net-new (Phase 3); (c) the "currently-open window panes" is the host's layout, which serializes today but only into the URL — live-mirroring it is net-new (Phase 2).

Architecture note (updated 2026-06-10): the live read-only stream already exists and N viewers of one tmux session already works. Auth is stateless/cookie-derived for normal users; share tokens now hook at `require_auth` and are route-whitelisted to `/`, `/ws`, and static assets. The readonly WS bridge (`bridge_tmux`) attaches with `tmux attach -r`, streams byte-identical output, and now drops input/scroll/resize for readonly. The remaining geometry hazard is initial/fixed sizing: a share guest should be sized from the host tmux geometry and scroll locally, not fit-to-container. JS line numbers drift in actively-edited files — grep the named symbol.

Phase 1 (MVP) — view-only LIVE TERMINAL mirror of one session via a tokened guest (delivers "view-only activity" + free terminal cursor):
- [ ] [M] PARTIAL 2026-06-10: readonly resize is now dropped, so a readonly/share viewer cannot keep sending resize events after attach. Still open: size the guest attach pty from the HOST's current tmux geometry (`tmux display -p -t <session> '#{window_width} #{window_height}'`), render xterm at that fixed host geometry, and let a smaller guest viewport overflow/scroll locally. Validate: shrink the guest browser → host panes do NOT change size; the guest scrolls to reach the bottom rows.
- [x] [M] DONE 2026-06-10: Token table on the app: `self.share_tokens` plus `share_tokens_lock`; mint with `secrets.token_urlsafe(32)`; verify with `hmac.compare_digest`, expiry, revoked flag, lazy expired cleanup, and active-session check. In-memory only by default.
- [x] [M] DONE 2026-06-10: Mint endpoint: admin-gated `POST /api/share {session, ttl_seconds, layout, tabs}` → `app.create_share_token(...)`, returning token + absolute URL seeded with `token`, bound `sessions`, and optional `layout`/`tabs`.
- [x] [L] DONE 2026-06-10: Recognize the token at the choke point after `auth_setup_required()` and before cookie/basic auth. A valid `?token=`/`X-Share-Token` synthesizes readonly auth, stashes `_share_session`, whitelists token requests to `/`, `/ws`, and static assets, limits root HTML to the bound session, and rejects `/ws?session=` values outside the token scope.
Small one-session share-stream scope item is archived in `docs/DONE.md`.
- [ ] [M] PARTIAL 2026-06-10: `log_message` now scrubs `token=` values before writing access logs. Still open: exchange first valid token hit for a short-lived HttpOnly guest cookie bound to the session + token expiry, then redirect to the clean URL so the bearer token leaves browser history and Referer paths.
- [x] [M] DONE 2026-06-10: Revoke-on-terminate: `kill_session` and `rename_session` mark every token for that session revoked; `refresh_sessions()` revokes tokens whose sessions disappeared out of band; `verify_share_token()` also denies tokens whose bound session is no longer active.
Small share-token death-sweep item is archived in `docs/DONE.md`.
- [ ] [M] Host Share UI: a Share action (per-session, e.g. in the session popover / pane menu) → dialog with TTL choice + "until I close this session", a copy-link button, and a list of ACTIVE shares with a Revoke button (calls a `DELETE /api/share` / revoke). Make it loud that the link is view-only AND a secret.
- [ ] [M] Guest landing client: visiting the share URL loads the normal app but LOCKED — single shared session only, `readOnlyMode` on (already disables xterm stdin, `99_terminal_boot.js`), no Finder/file API, no other tabs, no session switcher; show a "view-only — <host> is presenting <session>" banner and an "expired / session ended" state when the bridge closes or the token is revoked.
- [ ] Validate: mint a 15-min link, open in a private window → see the session's live output + cursor, cannot type/scroll/resize, cannot reach any other session or the file API; kill the session as host → guest sees "session ended" and the link stops working; let the TTL lapse → link rejected.

Phase 2 — mirror the OPEN WINDOW PANES (layout) so the guest sees the same pane arrangement, live (delivers "current opened window panes"):
Small share-layout-at-load item is archived in `docs/DONE.md` and implemented 2026-06-10 for minted URL load (`POST /api/share` accepts `layout` and `tabs`). The live presenter-layout pipe below remains open.
- [ ] [L] (live) Net-new pipe: on every host `applyLayoutSlots`, POST the layout string to the server; server stores per-session "presenter layout" + broadcasts it; guest subscribes (SSE — model it on `tmux_wall.py:468`'s `text/event-stream` loop, or extend the existing event stream) and calls `layoutFromParam`→`applyLayoutSlots` on each update. Guest MUST suppress its own `updateActiveSessionParam` URL writes (`20_layout_state.js:2359`) so it doesn't fight the host's state.
- [ ] Validate: host splits/moves/opens a pane → the guest's panes rearrange to match within ~1s; guest cannot edit its own layout.

Phase 3 — FOLLOW THE PRESENTER: the TYPE-B guest sees the host's active pane, scrolling, and mouse (FIRM requirements per the spec above, not optional; the terminal text-cursor is already free from Phase 1). All three ride the same host→server→guest broadcast channel as Phase 2 (SSE), applied on the guest WITHOUT echoing back:
- [ ] [L] Active pane ("active in 1 pane"): `focusedPanelItem` + per-slot active tab are client-local module vars toggled by `setFocusedTerminal`/`setFocusedPanelItem`/`updatePanelInactiveOverlays` (`10_core_utils.js`, grep symbols) and are NEVER sent today. Broadcast them; the guest applies via local `setFocusedPanelItem` so its focused-pane highlight + active tab follow the host.
- [ ] [L] Host scrolling in panes: broadcast the host's scroll offset per pane (terminal scrollback position; editor/Finder `scrollTop`); the guest scrolls its OWN local xterm/container to match. Net-new — the existing `tmux-scroll` drives the SHARED session and is readonly-blocked (`app.py:1155`), so this is a per-viewer mirror, not a tmux command. Throttle.
- [ ] [L] Host mouse pointer (collaborator / ghost cursor): broadcast the host's pointer position (throttled `pointermove`, as pane-relative coords + which pane) and render a labeled ghost-cursor overlay on the guest that tracks the host's mouse. Bound the data — only while over a pane, ~30-60ms throttle, hide when idle / off-window. Most net-new piece (a presence layer).
- [ ] Validate: host moves the mouse / scrolls a pane / switches the active pane → within ~100ms the guest's ghost cursor moves, the pane scrolls to the same place, and the active-pane highlight follows.

RESEARCH FINDINGS (2026-06-06) — free vs net-new, and the security model:
- FREE / reuse as-is: live terminal output for one session (`/ws?session=X` → `bridge_tmux` → PTY→WS binary frames → xterm, `server.py:1058/1076/1113`); read-only attach (`tmux attach -r`, `server.py:1083`); the terminal cursor (rendered from the PTY stream); N simultaneous viewers of one session (per-connection attach, `ThreadingHTTPServer`, no cap); layout serialize/apply; the SSE push pattern (model from `tmux_wall.py:468`). `tmux_wall.py` itself is NOT reusable for the mirror (laggy capture-pane snapshots, no auth, refuses non-loopback) — the live WS bridge is the right source.
- NET-NEW: the token table + mint/verify/revoke; the `require_auth` token path + route whitelist; geometry decoupling (size guest pty to host window + drop guest resize + guest scrolls locally); the token→cookie exchange + log scrub; the kill/rename revoke + out-of-band sweep; the host→server→guest broadcast pipe for layout (Phase 2) and active-pane / scroll / MOUSE-POINTER (Phase 3 — the ghost cursor is the most net-new); the guest locked-down client + Share UI.
- SECURITY (must-do, not optional): (1) bearer-in-URL leaks via history/logs/Referer → exchange for an HttpOnly guest cookie + strip the URL + scrub `log_message`; (2) `role="readonly"` alone leaks EVERY session's transcript/events/search — the TYPE-B guest MUST be route-whitelisted to `/`,`/ws`,`/static/*` (TYPE-A `auth.yaml` readonly keeps full nav, unchanged); (3) "expire when host terminates" REQUIRES the server-side revocation table (a signed/stateless token can't be revoked early); (4) sessions killed outside the UI need the `refresh_sessions` sweep; (5) a TYPE-B guest must never drive tmux size — drop its `resize` AND size its pty to the host geometry so a smaller guest scrolls locally instead of shrinking the host; (6) keep local-only default + the existing tunnel guidance (README "Remote access") for exposing the share off-box.

### File Explorer

- [ ] [M] Git-aware Finder: hover popover + inline path annotation for repo dirs/paths. The Finder already knows repo dirs (`entry.is_repo`, `filesystem.py:109` via REPO_MARKERS; the row gets `.is-repo`, `yolomux.js:3830`) but only as a boolean — extend it with real git info.
  - (A) HOVER POPOVER on a repo directory / git path: show repo name, current branch (flag when it's NOT `main`/`master` — reuse `isDefaultBranch`), dirty / ahead / behind counts, and the remote / GitHub URL. Reuse the existing hover-popover infra (the `bindFileImagePreview` pattern + `popoverShowDelayMs`/`popoverHideDelayMs`, follow-cursor positioning). Needs a server side: extend the fs metadata that already resolves `git_root_for_path` / `repo_root` / `relative_path` (`filesystem.py:253`) to also return branch + dirty/ahead/behind + remote — reuse the per-session `project.git` shape from `metadata.py` so the format matches the tab rows. DEBOUNCE + cache (don't run `git` on every hover; key by path, refresh on mtime/expiry).
  - (B) INLINE PATH ANNOTATION: when the rooted directory is inside a repo, the Finder root-path display should append the repo + branch, e.g. `utils (main ...)` (basename + branch + a dirty marker). Render in `setFileExplorerPathDisplay` (`yolomux.js:3294`); reuse the branch helpers (`shortBranch` / the branch-badge formatters ~5602/5618). Non-`main` branches stand out.
  - Validate: hover a repo dir -> popover shows repo + branch + dirty/ahead-behind; root the Finder inside a repo on a feature branch -> path reads `repo (branch ...)`; no `git` spawn per hover (cached).

- [ ] [XL] GLOBAL background summarizer ("what's going on" across ALL sessions) — STATEFUL for speed. A background loop that maintains a rolling per-session summary AND rolls them up into one GLOBAL overview: which sessions need attention, what each is doing, who's blocked / waiting / done. Surface the global view somewhere (a dedicated summary tab / the read-only wall / a top-bar status line). Build on the existing AI-summary plumbing (`codex_summary_prompt` in `yolomux_lib/transcripts.py` ~134 + `app.py` ~429; `/api/summary-stream`; `SUMMARY_CODEX_MODEL`/EFFORT/SERVICE_TIER in `common.py`).
  - SESSION-FUL / incremental for fast I/O: do NOT re-summarize whole transcripts each tick (today's summary path is one-shot and re-sends context). Keep the summarizer STATEFUL — either a persistent Codex/Claude conversation per session that you feed deltas to ("update the summary given these new lines"), or prompt-caching the stable prefix and sending only the delta. Practically: persist `{rolling_summary, last_processed_offset}` per session and each tick feed only `[prior summary] + [new transcript lines since last_offset]` -> small input, fast/cheap output. The global overview is then a cheap roll-up of the per-session summaries.
  - INFER the agent's effective working directory from the transcript (files claude/codex is actually editing / `cd`s into) — more accurate than raw process cwd. Use it to re-implement the per-tab "jump the File Explorer to this session's working path" action removed earlier, and to upgrade root-mode "sync to tmux session" to use the inferred path.
  - Run on an interval, throttled, only when a session is quiet (per the P0 "delay LLM classification until quiet" note). Cost-gate behind a setting / interval (Preferences).
  - RESEARCH FINDINGS (session-ful, 2026-05-29). Today: `run_codex_summary` (server.py ~682) shells `codex exec --json -m gpt-5.5 ... --ephemeral -` and pipes the whole prompt to stdin — STATELESS one-shot; `--ephemeral` explicitly does not persist a session, so every tick re-sends the full transcript window. Methods, from cheapest-to-build to richest:
    - M1 stateless one-shot (current): re-sends the window each tick. Simplest, slowest/priciest on big transcripts. Baseline.
    - M2 incremental-stateless (RECOMMENDED baseline): keep `{rolling_summary, last_offset}` per session; each tick send `[prior summary] + [delta lines]` through the SAME `codex exec` path. Tiny input, no session lifecycle, works today. Global view = cheap roll-up of per-session summaries. Risk: fidelity depends on the rolling summary capturing enough.
    - M3 stateful Codex resume (CONFIRMED available): drop `--ephemeral`, capture the session id from the `--json` stream, then `codex exec resume <session-id> -` feeding only the delta ("update the summary, here are N new lines"). Codex retains full prior context -> best continuity, input = delta. Cost: session-id capture + lifecycle (prune on tmux-session death), and codex keeps a rollout file per summarizer session.
    - M4 prompt-caching: Claude API `cache_control` on the stable prefix (system + prior transcript/summary), send delta -> big input discount on cache hits, but ~5-min cache TTL means idle sessions lose it. Codex/OpenAI backend also auto-caches repeated prefixes, but `--ephemeral` + a sliding window breaks prefix stability.
    - Claude vs Codex: Codex is already wired and now confirmed to support `resume` (M3) — lowest-friction. Claude offers the cleanest EXPLICIT caching (`cache_control`, M4) + `claude --resume`, but switching means new plumbing (auth, streaming, prompt format). Recommendation: implement M2 first (immediate win on the existing path), then try M3 (codex resume) for fidelity; only reach for Claude/M4 if Codex caching proves insufficient.
    - EMPIRICAL RESULTS (RUN 2026-05-29, real `codex exec` on a 3000-line / 470 KB / ~128k-token transcript; gpt-5.5, effort=low, service_tier=fast; steady-state PER-TICK update cost):
      - M1 one-shot full window: ~7.0 s, input ~130,983 tok (cached ~2.4k) — re-sends the whole window every tick.
      - M2 incremental (prior summary + delta): ~4.4 s, input ~20,083 tok (cached ~4-9k) — 6.5x fewer input tokens, fastest.
      - M3 codex `resume` + delta: ~6-9 s, input ~517,380 tok (cached ~391k) and GROWING every tick — codex resume REPLAYS the whole accumulated conversation to the API each call; prefix-caching discounts cost but token volume grows unbounded and it is slower than M2.
      - VERDICT: M2 wins decisively (least input, fastest, simplest, runs on the existing `codex exec --ephemeral` path). M3 ("stateful" resume) is counterproductive here — it is the WORST on tokens and not faster, and would need periodic session resets anyway. Claude `cache_control` (M4) was not benchmarked but offers nothing M2 lacks for this workload. IMPLEMENT M2.
  - PROVIDER: support BOTH Codex and Claude backends behind one `Summarizer` interface (`summarize(prior_summary, delta_lines) -> text`, M2 incremental). Both CLIs are on PATH today; both run headless and emit JSON:
    - Codex backend (existing path): `codex exec --json -m <model> -c model_reasoning_effort=... -c service_tier=... --sandbox read-only --ephemeral --ignore-rules --cd <root> -`, prompt on stdin; parse `agent_message` / `turn.completed` (grep `run_codex_summary`, server.py ~682).
    - Claude backend (new): `claude -p --output-format json --model <model>` with the prompt on stdin; parse the JSON result. (Verified `claude --help`: `-p/--print`, `--output-format json|stream-json`, `--model`.) Same M2 prompt shape.
  - SELECTION (settings-driven): a `summarizer.provider` setting = `codex` | `claude` (Preferences). When BOTH are available -> use the setting's choice. When only ONE is available -> auto-use it (ignore the setting). When NEITHER `claude` nor `codex` is on PATH -> the summarizer is DISABLED / "not available" (no global-summary tab, hide its controls). Detect availability the same way the `+ Claude` / `+ Codex` buttons do (PATH check).
  - CLAUDE BENCHMARK (RUN 2026-05-30, `claude -p --output-format json --model claude-haiku-4-5`, same 3000-line transcript, per-tick): M1 full window ~13-20 s, ~$0.15-0.32/tick; M2 incremental ~9.2 s, ~$0.01-0.03/tick. PARITY CONFIRMED — M2 wins for Claude too (~10-30x cheaper per tick). So M2 is the right approach for BOTH providers.
  - CHEAPEST/FASTEST MODEL per provider (user directive): Claude -> `claude-haiku-4-5` (Haiku; `claude -p` defaults to Opus 4.8 which is far too expensive — MUST pin Haiku). Codex -> `gpt-5.5` with `model_reasoning_effort=low` + `service_tier=fast`. Model availability on THIS ChatGPT-account Codex (probed 2026-05-30): only `gpt-5.5` and `gpt-5.4` work; `gpt-5-mini` / `gpt-5.3` / `gpt-5` / `gpt-5.5-codex` / `gpt-5-codex` all return `invalid_request_error` (a mini would need API-key auth). `gpt-5.4` was tested on M2 and is NOT cheaper/faster (gpt-5.5: 2.5-3.0 s/tick, ~20.2k in; gpt-5.4: 2.8-5.2 s, ~18.9k in — 5.5 is faster + more consistent, tokens ~equal). ChatGPT-account Codex has NO per-token `$` (subscription quota), so model choice is about latency/quota, not dollars. KEEP gpt-5.5/low/fast. The summarizer must DEFAULT to these cheap models (do NOT inherit the global `~/.codex/config.toml` `model_reasoning_effort = "xhigh"`), and expose the model per provider in Preferences.
  - Cross-provider M2 (per-tick): Codex gpt-5.5/low ~4.4 s; Claude Haiku ~9.2 s (~$0.01-0.03). Codex was faster in this run; both are cheap. Provider choice = the settings rule above (setting when both available, auto when one, disabled when none).

### Bug Fixes And Tech Debt

- [x] [S] Diff editor: the editor's left panel (gutter/line-number column) picks up a red background tint from the diff's "from" side coloring. Remove this — the left panel should stay neutral (no red). Only the actual changed-line rows should carry diff color, not the surrounding chrome. (Screenshot: 20260605-033.png) DONE 2026-06-05: `.cm-changedLineGutter` and `.cm-deletedLineGutter` now inherit neutral gutter styling with transparent backgrounds in both themes; light-mode red/green gutter overrides were removed, and source + browser regressions pin the neutral left gutter.

## Good Ideas From Similar Products

- Claude Code agent view: group sessions by `Needs input`, `Working`, `Ready for review`, and `Completed`; show last activity and current action; support peek/reply and attach/detach without killing the session; support cwd-scoped filtering and JSON output.
- Agent Cockpit: risk-rated approval queue, per-agent terminal, per-agent timeline, and full audit trail for tool calls, writes, and approval decisions.
- Cogpit: live stream of tool calls/file edits, file change panel, token/cost/context usage, full-text search, and remote/mobile viewing.
- ClauBoard: explicit event schema, JSONL-style persistence, run lifecycle, task board, event timeline, and dependency/pipeline concepts.
- dmux: tmux plus git worktrees, automated worktree lifecycle, read-only file browser per worktree, native notifications when panes need attention.
- Nova Code: self-hosted workspaces, queues, scheduling/automations, files/git view, rules/templates, and normalized streaming across different agents.
- Agent Watch/lazyagent-style monitors: broad agent discovery across Claude, Codex, Popular IDEs, Gemini, Amp, and OpenCode; status API and menu-bar or notification surfaces.

---

## Feature Comparison

| Feature | Projects that already have it | YOLOmux action |
| --- | --- | --- |
| Multi-session dashboard | Claude Code Agent View, ClauBoard, CLD CTRL, Agent Cockpit | Already has panes and tabs; add better state grouping and session ordering. |
| `Needs input` / `Working` / `Done` grouping | Claude Code Agent View, Agent Conductor-style dashboards | Build first. Put state badges in top tabs and add a `Needs me` view. |
| Background sessions that keep running | Claude Code Agent View, dmux, YOLOmux | Already has this through tmux; make hidden/running state more obvious. |
| Tmux-backed parallel agents | dmux, workmux, YOLOmux | Keep this as a core differentiator. Do not replace tmux with a heavier runtime. |
| Git worktree-backed sessions | dmux, pertmux, webmux, workmux | Add after launch dialog exists. Keep worktree cleanup guarded and explicit. |
| Browser terminal streaming | webmux, Handler, YOLOmux | Already has this. Improve mobile behavior later instead of rewriting terminal plumbing. |
| Approval queue / approval cards | Agent Cockpit, purplemux-style timeline prompts | Add after YOLO event log exists. Start with visibility, then live allow/deny. |
| Risk-rated approvals | Agent Cockpit | Use concrete labels: `read`, `edit`, `network`, `process`, `delete`, `credential`, `unknown`. |
| YOLO / approval audit log | Agent Cockpit, Cogpit, ClauBoard | Add JSONL event log before timeline UI. This is P1. |
| Event timeline | Cogpit, ClauBoard, Blackcrab, purplemux | Build on top of the event log. Avoid making timeline the storage model. |
| Live tool-call stream | Cogpit, Blackcrab, purplemux | Useful for Claude. For Codex, only ship if transcript/tool-call format is stable enough. |
| File changes / diff panel | Cogpit, CC Assist, dmux, CLD CTRL, webmux | Add in P3. Read-only changed-files and unified diff are enough at first. |
| Read-only file browser | dmux, CLD CTRL, webmux | Add with worktree mode. Keep read-only before adding file edit controls. |
| Full-text search | Cogpit, CC Assist, Blackcrab, Claude Chronicle | Add after events/summaries are stored in a searchable local index. |
| Cost/token/context metrics | Cogpit, CC Assist, CLD CTRL, Claudemetry | Add only when Claude/Codex metadata is reliable. Do not scrape fragile terminal text. |
| Resume/fork sessions | CC Assist, CLD CTRL, Blackcrab, Claude Code session history tools | Add resume picker first. Forking can wait. |
| Browser/native notifications | dmux, webmux, Agent Watch-style tools | Add after session state exists. Notify only on state transitions. |
| Mobile check-in UI | webmux, Cogpit, Blackcrab | Add a single-pane focus mode. Do not force the desktop pane layout onto mobile. |
| Multi-agent task board / orchestration | ClauBoard, Cogpit, Agent Cockpit | Defer. YOLOmux should stay a local control plane before becoming an orchestrator. |
| Multi-machine / remote connector | webmux, Agent Cockpit, agentserver, Handler | Defer. This changes auth, networking, logging, and threat model. |

---

## Recommendation

Do next: session state plus `Needs me`. This is the highest leverage feature because it makes every existing YOLOmux pane easier to manage without changing tmux architecture.

Do second: YOLO event log and audit panel. It makes YOLO understandable and creates the data foundation for notifications, timelines, and risk review.

Do third: info drawer with changed files, PR/CI, and latest structured summary. It connects terminal activity to repo state and reduces context switching.

Do not start with visual canvases, multi-machine, or full pipeline orchestration. Those are attractive, but they add architecture before the local control loop is strong.
