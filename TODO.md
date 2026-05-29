# YOLOmux TODOs

YOLOmux-specific roadmap notes. Keep UI, terminal, YOLO approval, session state, and launch ideas here instead of the Project TODO list.

---

## Product Direction

YOLOmux should stay a lightweight local tmux browser control plane. The useful niche is not another full SaaS-style orchestration stack. It is a fast local UI for existing Claude/Codex/tmux sessions with clear state, safe YOLO controls, repo metadata, file paste/upload, and low-friction attach/reply.

Borrow from other tools only when the feature improves the local control loop: know which session needs attention, understand what changed, approve or block risky work, and jump back into the right terminal quickly.

---

## Priority Roadmap

### P0: Session State And Needs-Me View

- [x] Add a per-session state model. States: `working`, `idle`, `needs input`, `needs approval`, `blocked`, `tests running`, `ready for review`, `done`, `disconnected`.
- [x] Show state in top tabs and pane headers using compact badges, not extra text-heavy panels.
- [x] Add a `Needs me` filter/sort mode that brings `needs input`, `needs approval`, `blocked`, and `disconnected` sessions first.
- [x] Detect state from tmux pane output, agent process status, recent terminal activity, YOLO worker status, PR/CI metadata, and known prompt patterns.
- [x] Keep LLM classification optional and delayed until a session is quiet; first ship deterministic heuristics.

### P0: Menu-Bar Navigation (replace top tabs with real dropdown menus)

Goal: replace the always-visible top session-tab strip with a proper application menu bar — top-level menus that open dropdowns, the way a real program does. New session (Claude and Codex) moves INTO the menu; there are no more persistent top tabs. Only the active session stays shown inline.

DESIGN GATE (do this first, do not write code yet):

- [ ] Confirm decision: remove the always-on top session-tab strip (`#sessionButtons`); New session for Claude AND Codex lives inside the Tmux Session menu, not as top tabs.
- [ ] Review and sign off the menu tree below before any implementation. Iterate on labels, grouping, and which existing controls move where. Implementation starts only after the structure is approved.

Current controls to be reorganized (inventory):

- Top bar today: brand + version, session-tab strip (`#sessionButtons`), latency meter, `Notify`, `Refresh`, `Log out`, `status`, tab-metadata toggle (`#`), HTTPS warning.
- Per-pane tab strip today: `<` / `>` window step, `Terminal`, `Tx` (transcript), `AI` (summary), `Log` (events), `Info` (detail toggle), window-close.
- Other surfaces: File Explorer (tree + editor), Files panel, layout (single / grid / wall), inactive-tabs tray menu, per-session state badges (`RUN`/`EXEC?`/`YOLO?`/`QUES?`/`BLK`/`OFF`/`TEST`/`PR`/`IDLE`/`DONE`).

Menu naming follows the macOS / iTerm convention: `File` (create/manage), `View` (display options), `Windows` (navigate every open window/view, like iTerm's Window menu), plus app-specific `YOLO` and `Settings`, and `Help`.

Proposed top-bar layout (left to right): `YOLOmux ver` · `[File ▾] [View ▾] [Windows ▾] [YOLO ▾] [Settings ▾] [Help ▾]` · active-session chip (label + state badge) · latency · `Notify` · `Refresh` · status · `Log out`.

KEEP AS DEDICATED TOP-RIGHT BUTTONS: `Notify`, `Refresh`, `Log out`. These stay as one-click buttons on the right of the top bar exactly as today (same icons, same behavior). The menus absorb the session tabs and the lower-frequency controls; these three high-use buttons remain visible.

ALSO MIRROR THEM IN THE SETTINGS MENU: the same `Notify`, `Refresh`, and `Log out` (identical icons and behavior) also appear as items inside the `Settings ▾` menu, in addition to the top-right buttons. They are duplicated, not moved — both surfaces stay in sync (e.g. Notify shows the same on/off state in both). This keeps them reachable when the top-right buttons collapse on narrow/mobile widths and improves discoverability.

Proposed menu tree (PROPOSAL — review before building):

- [ ] File ▾  (create / manage)
  - New tmux session ▸ : `+ Claude`, `+ Codex`, `+ Term` (each opens the P4 launch dialog: cwd, model/profile, permission mode, initial prompt, optional name)
  - Rename tmux session
  - Kill tmux session
  - Resume ▸ : recent Claude/Codex conversations scoped to cwd (P4)
  - --- (separator)
  - File Explorer (open)
  - Open file…
  - NOTE: no Attach / Detach item. YOLOmux streams every pane over WebSocket and is always "attached"; switching sessions only changes which stream is shown. tmux attach/detach is a terminal-client concept that does not map to this UI, so it is intentionally omitted.
- [ ] View ▾  (display options)
  - Layout ▸ : Single / Grid / Wall
  - Filter / Sort ▸ : Needs me, by state, by repo, by PR status
  - Tab metadata: show / hide (the current `#` toggle)
  - Inactive tabs: show all / tray
  - Panel tabs ▸ : toggle Terminal / Tx / AI / Log / Info visibility
  - Branch Info: show — opens the Branch Info viewer for the active session (also still available as the per-pane `Info` tab; once open it appears in the Windows list)
- [ ] Windows ▾  (navigate — iTerm-style; a FLAT list of only the windows that are currently open, checkmark/highlight on the active one, click/Enter to focus, type-to-filter)
  - Just lists the currently-open windows, nothing to "launch" from here — opening windows happens in File / View. Ordering, top to bottom:
  - tmux windows first : each open session/terminal window
  - then editors : each open File Editor window
  - then other open viewers : File Explorer, Branch Info, Transcript, AI Summary, Event Log (only the ones currently open)
  - Each row carries the SAME rich info already shown in the existing tab-metadata view, but packed more compactly so it reads like a real dropdown menu (one tight row per window): agent badge (`YO`/`BL`/… + session number), state badge (`RUN`/`BLK`/…), PR number, commit-style title, branch, repo path (e.g. `dynamo2 -> dynamo3`), and dirty count. The active window's row is highlighted (green) like the current selection.
  - This is a denser re-layout of the existing rich rows, not new data — reuse the metadata that already feeds the tab strip.
- [ ] Per-window left dropdown (the caret on the LEFT of each panel's tab strip): looks IDENTICAL to the Windows ▾ menu above (same compact rich-row format), but scoped to just THAT window's tabs — i.e. only the tabs/views belonging to that one panel (its tmux windows + Terminal / Tx / AI / Log / Info), not every window in the app. Same row styling, same active-row highlight.
- [ ] YOLO ▾
  - Auto-approve: on / off (global)
  - Policy ▸ : off / prompt-only / safe / edit / full (default for new sessions + per-session override) — ties to P1 policy modes
  - Open rule file (YAML)… — ties to "YOLO Rule Engine" below
  - Approval queue (P1)
  - Audit log
  - Risk labels legend (`read` / `edit` / `network` / `process` / `delete` / `credential` / `unknown`)
- [ ] Settings ▾
  - Global settings… (opens the Settings panel: System/General, Notifications, Appearance, Performance, YOLO, Terminal, Advanced)
  - --- (separator)
  - `Notify` (same icon + on/off state as the top-right button — mirrored, not moved)
  - `Refresh` (same icon + behavior as the top-right button — mirrored)
  - `Log out` (same icon + behavior as the top-right button — mirrored)
- [ ] Help ▾
  - Keyboard shortcuts
  - About / version
  - Docs link
- [ ] Per-pane kebab (`…`) on each panel head (keep the existing Term/Tx/AI/Log/Info strip): YOLO policy for this session, peek / reply, run summary, rename, kill. (No "attach" item — see the Tmux Session note above.)
- [ ] Right-click context menu on a session tab (the `window-session-tab` / panel header): `Rename session` (inline edit, same affordance as the file-tree rename at `static/yolomux.js:2076`), plus `Kill session` and `YOLO policy`. This is the expected shortcut so users do not have to open the Tmux Session menu just to rename.

Cross-cutting requirements for all menus:

- [ ] Keyboard accessible (open/close, arrow navigation, Esc), ARIA menu roles, click-outside to close, one menu open at a time (reuse the existing popover-open machinery).
- [ ] Mobile: menus collapse into a single hamburger (ties to P7).
- [ ] Read-only mode disables mutating items (New, Kill, Settings writes, YOLO toggles) the same way the current `Notify`/buttons do.

### P1: YOLO Event Log, Audit, And Queue

- [x] Add a persistent YOLO event log under `~/.local/state/yolomux/events.jsonl`, while keeping compact app state in `~/.config/yolomux/state.json`.
- [x] Record approval decisions, blocked commands, worker errors, session start/stop, terminal disconnects, uploads, pasted images, summary runs, state changes, and user-visible notifications.
- [x] Add a per-session YOLO audit panel showing recent approved/blocked decisions with timestamp, command text, matched rule, and session.
- [ ] Add an approval queue view for pending high-risk actions. Start read-only first if live interception is hard.
- [ ] Add per-session YOLO policy. Initial modes: `off`, `prompt-only`, `safe`, `edit`, `full`. Make policy visible on the YOLO button.
- [ ] Risk labels should be boring and concrete: `read`, `edit`, `network`, `process`, `delete`, `credential`, `unknown`.

### P1: YOLO Rule Engine (user-configurable matching via YAML)

Today YOLO matching is hardcoded in `auto_approve_tmux.py`: a fixed `DANGEROUS_COMMANDS` set + `DANGEROUS_PATTERNS` denylist, with a binary outcome (press Enter to approve, or leave the prompt for manual action). Goal: let users declare, in a YAML file, what input patterns map to what action — so the same engine can auto-approve safe commands, auto-decline dangerous ones, or just notify, without editing Python.

DESIGN GATE: propose the schema and decide the options below before implementing. Do not write code until the rule shape is signed off.

- [ ] Decide file location and precedence. Proposal: `~/.config/yolomux/yolo-rules.yaml` (shared default) plus optional per-repo `.yolomux.yaml` that overlays it; per-session override via the YOLO menu.
- [ ] Decide the action verbs. Proposal: `approve` (press Enter / select Yes), `decline` (select No / option2), `block` (leave for manual, current behavior), `ask` (notify + wait), `notify` (log only, take no action).
- [ ] Decide match types. Proposal: `contains` (substring), `regex`, `glob`, and `command` (argv-aware parse so `echo "rm"` is data, not a delete). Argv-aware matching is the main upgrade over today's whole-line regex.
- [ ] Decide scoping dimensions: global vs per-repo vs per-session, per-agent (`claude` / `codex`), and per prompt-type (`bash` / `file` / `tool`).

Schema options to choose between (PROPOSAL — pick one or blend):

- [ ] Option A — ordered rule list, first match wins:

```yaml
default: ask            # off | approve | decline | block | ask
rules:
  - name: block destructive
    type: command       # command | regex | glob | contains
    match: ['rm', 'rmdir', 'shred', 'dd', 'mkfs']
    action: block
    risk: delete
  - name: safe reads
    type: regex
    match: '^(ls|cat|grep|git (status|log|diff))\b'
    action: approve
    risk: read
```

- [ ] Option B — risk-class map (patterns assign a risk, risk maps to an action):

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

- [ ] Option C — profiles + scope overrides (layer on top of A or B):

```yaml
profiles:
  default: { bash: safe, file: approve, tool: ask }
  codex:   { file: approve }
sessions:
  '6': { bash: full }
```

Safety and operational requirements (regardless of schema):

- [ ] Deny always beats allow. Keep a hard floor (`rm -rf /`, `dd` to a block device, fork bomb, `mkfs`, redirect to `/dev/sd*`) that the YAML cannot relax unless YOLOmux was started with `--dangerously-yolo`.
- [ ] Dry-run / shadow mode: evaluate rules and log what WOULD happen (matched rule + action) without acting, so a new ruleset can be validated against real prompts first.
- [ ] Hot-reload on file change; validate the schema on load and surface errors in the UI instead of silently falling back.
- [ ] Ship today's hardcoded denylist AS the default `yolo-rules.yaml` so behavior is unchanged out of the box; the hardcoded version becomes the fallback when no file exists.
- [ ] Record the matched rule name in every audit event (the audit panel already shows a "matched rule" column).
- [ ] Add an "Open rule file" + "Reload rules" action in the YOLO menu, and show the active ruleset path/source in Settings.

### P1: File Explorer & Editor Live Refresh

When files change on disk, the File Explorer (Finder) and the open editor should update on their own instead of showing stale content.

- [ ] Refresh the File Explorer tree when the watched directory changes on disk (files added / removed / renamed / modified). Do NOT lose state on refresh: keep the current selection, keep expanded folders expanded, and keep the scroll position exactly where it was (no jump to top, no collapse). Diff the tree and patch in place rather than rebuilding it.
- [ ] Refresh the open editor content when the currently-open file changes on disk, for any file type (md, sh, py, etc.); re-render the Markdown preview if it is showing.
- [ ] Handle unsaved edits safely: if the editor has local unsaved changes and the file also changed on disk, do not silently overwrite the user's edits — show a conflict notice and let them keep theirs or reload from disk.
- [ ] Handle the open file being deleted or moved on disk: show a clear state instead of stale content.
- [ ] Mechanism: watch the explorer's current directory and the open file on the server (inotify, fall back to mtime poll) and push changes to the browser over the existing channel; debounce rapid bursts. Reuse existing refresh plumbing rather than adding a new fast poll loop.

### P2: Conditional Window-Step Arrows

The per-pane window-step buttons (`<` / `>`, the `window-step` controls in `static/yolomux.js` that page through `[Codex, Claude, bash, ...]` tmux windows in the current session) currently always show. Make them appear only when there is somewhere to step to.

- [ ] Show `<` only when a tmux window exists BEFORE the current window in this session.
- [ ] Show `>` only when a tmux window exists AFTER the current window in this session.
- [ ] When the session has only one window, hide BOTH arrows.
- [ ] Edge cases: recompute on window create/close/move and on session switch; do not reserve empty space when an arrow is hidden (the label should not jump). Decide whether ordering follows tmux window index or the current display order.

### P2: Notifications

- [x] Add browser notifications for state transitions only: `needs input`, `needs approval`, `YOLO blocked`, `terminal disconnected`, and `PR ready`.
- [x] Add notification throttling per session so repeated prompt text does not spam.
- [x] Add a small notification history in the event log so missed browser notifications are still visible later.

### P3: Info Drawer And Diff Panel

- [ ] Add a per-session info drawer with full path, branch, dirty/ahead/behind counts, PR, CI, Linear/issue metadata, latest summary, and recent events.
- [ ] Add a read-only changed-files list and unified diff panel using the session cwd.
- [ ] Make PR/CI/issue links clickable, but keep local branch names as text unless a real remote branch/PR exists.
- [ ] Add an explicit refresh button for repo metadata plus background polling with sane intervals.
- [ ] Remove redundant info in the file-viewer detail/info panel. Today it repeats itself: the filename shows up as both the tab label AND the bold heading, and the full path shows up twice — once as the subtitle line under the heading and again in the `path` row (with the copy button). Show the filename once and the full path once. Keep the path row (it has the copy affordance) and drop the duplicate subtitle, or vice-versa. Also collapse `type: loading` / `status: loading` so a viewer that has no meaningful type/status does not show two placeholder "loading" rows.

### P3: Editor — Wrap, Preview, Split-Preview

The file editor today has a single Preview toggle (`#fileEditorPreview`, `web.py:119`) and a textarea hardcoded to `wrap="off"` (`web.py:123`). Make these first-class view modes plus a wrap toggle.

- [ ] Word-wrap toggle: switch the editor textarea between `wrap="off"` (current) and soft word-wrap, so long lines wrap to the pane width instead of scrolling horizontally. Persist the preference (Settings → Terminal/Editor).
- [ ] Three explicit view modes for the editor: `Edit`, `Preview`, `Split-Preview` (a small segmented control in the editor head). Edit = textarea only; Preview = rendered view only (reuse the existing `#fileEditorPreviewPane`).
- [ ] Split-Preview: split the current editor window in half — left = editor (textarea), right = rendered preview — side by side in the same panel.
- [ ] Split-Preview synced scroll: the two halves scroll together (scrolling the editor scrolls the preview to the matching position, and vice-versa). Map by source line / proportional offset so headings and code blocks stay roughly aligned.
- [ ] Preview content by type: Markdown renders to formatted HTML (existing marked.js path); non-Markdown (sh, py, etc.) shows the syntax-highlighted read view (reuse `#fileEditorHighlight`) so Split-Preview is useful for code too, not just `.md`.

### P4: Launch And Resume

- [ ] Add a launch dialog behind `+ Claude`, `+ Codex`, and `+ Term` with cwd, agent, model/profile, permission mode, initial prompt, and optional session name.
- [ ] Keep the current quick `+` path for defaults; the dialog should not slow down simple launches.
- [ ] Add a resume picker for recent Claude/Codex conversations scoped to the selected cwd.
- [ ] Add a `peek/reply` action for a session when it only needs a short response and the user does not need to attach to the full terminal.

### P5: Worktrees

- [ ] Add optional worktree-backed launch mode: create worktree, branch, tmux session, and initial agent prompt together.
- [ ] Show worktree path and parent repo in the info drawer.
- [ ] Add cleanup guardrails: never delete a worktree with uncommitted changes; show the path and stop.
- [ ] Add a read-only file browser for a session worktree before adding edit controls.

### P6: Search, Cost, And History

- [ ] Add full-text search across captured session events and summaries.
- [ ] Add per-session token/cost/context metrics only if they can be read reliably from Claude/Codex metadata without scraping fragile UI text.
- [ ] Add a compact run history: prompt, cwd, agent, started/ended time, final state, PR, and latest summary.

### P7: Mobile And Network Use

- [ ] Add a single-pane mobile focus mode with larger controls for Esc/Tab/Ctrl, paste/upload, YOLO actions, and reply.
- [ ] Add network-access setup guidance that is explicit about auth, host binding, and local-only defaults.
- [ ] Consider installable PWA behavior only after mobile layout is usable.

### P8: Host And Process Vitals

- [ ] Add lightweight CPU/memory/load probes and per-session process trees.
- [ ] Add optional `nv-smi` GPU status when available, but do not make GPU support required.
- [ ] Show vitals in an info drawer or compact topbar popover, not as a dominant dashboard.

### P9: Multi-Machine Connector

- [ ] Defer until the local product is stable. This changes auth, networking, logging, and failure modes.
- [ ] If built, use a small remote agent that reports tmux sessions, metadata, vitals, and WebSocket terminal streams back to one YOLOmux instance.
- [ ] Keep local-only as the default.

### Settings Panel

- [ ] Add a Settings panel, reachable from the menu (Tools ▾ → Settings…). Today the tunables are hardcoded in `static/yolomux.js:85-97` and on the server, with no UI.
- [ ] Persistent storage: write settings to a single human-readable file, `~/.config/yolomux/settings.yaml`. Use YAML specifically so it is easy to read and hand-edit, WITH inline comments documenting each key, its units, default, and allowed range. Keep machine state (badge pulses, auto-approve-enabled session list) in the existing `state.json`; `settings.yaml` is for user preferences only.
- [ ] Live propagation to ALL running servers: a settings change made in one YOLOmux instance must take effect in every other launched server (e.g. the `:7777` and `:7778` instances) without a restart. Since `settings.yaml` is the shared source of truth, each server watches the file (mtime/inotify) and reloads on change, and each open browser is pushed the new values over the existing WebSocket/poll channel so live pages update too. Writes must be atomic (temp file + rename) and last-write-wins; preserve comments on rewrite (round-trip YAML, e.g. ruamel) so hand-added notes survive a UI save.
- [ ] Notifications: Notify on/off (already a toggle); choose which state transitions notify (`needs input`, `needs approval`, `YOLO blocked`, `terminal disconnected`, `PR ready`); per-session mute; notify throttle interval.
- [ ] Toast duration (`toastDurationMs`, default 10000 ms).
- [ ] Refresh frequencies: metadata (`metadataRefreshMs`, 15001 ms), pane/agent state (`paneStateRefreshMs`, 1257 ms), latency meter (`latencyRefreshMs`, 3001 ms), event log (`eventLogRefreshMs`, 5003 ms).
- [ ] Blinking RED attention-reminder cycle frequency (`redReminderMs`, 1550 ms), plus an off switch.
- [ ] YO rotation cycle frequency (`yoloRotateMs`, 20000 ms), plus off.
- [ ] Metadata-badge pulse duration (`METADATA_BADGE_PULSE_SECONDS`, 20 s, server-side).
- [ ] Popover show/hide delay (`popoverShowDelayMs` / `popoverHideDelayMs`, 300 ms); remote resize debounce (`remoteResizeDelayMs`, 220 ms).
- [ ] Auto-approve poll interval (auto_approve `--interval`, default 0.5 s) and default YOLO policy for new sessions (ties to P1 policy modes).
- [ ] Tab metadata visibility (the existing show/hide tab-metadata toggle), default layout (single/grid/wall), and default sessions on load.
- [ ] Terminal preferences: font size and scrollback limit.
- [ ] Each numeric setting needs a sane min/max clamp and a Reset-to-default button so a bad value cannot freeze the refresh loop.

### File Explorer

- [ ] Add a `Download` item to the file right-click context menu (`file-context-menu`, `static/yolomux.js:1993`), alongside the existing Copy full path / Copy relative path / Rename / Delete. Downloads the file to the browser. Backend `filesystem.read_raw` already streams bytes; add (or reuse) a raw-file endpoint that sets `Content-Disposition: attachment; filename=...` so the browser saves instead of previewing. For a directory, either disable Download or offer a zip. Support multi-select (download each, or a single zip). Respect read-only mode rules and the `MAX_RAW_BYTES` cap.
- [ ] When opening a text file, group it into the EXISTING editor window instead of spawning a new editor panel. If an editor window is already open, add the file as a new tab in that window and focus it (and just focus the tab if the file is already open there). Only create a new editor window when none exists yet. Avoids one-editor-panel-per-file sprawl in the layout.

### Bug Fixes And Tech Debt

- [ ] Fix `tests/test_filesystem.py:177` `test_is_text_path_recognizes_known_extensions`. It passes but only spot-checks 5 extensions and hides a real bug: `filesystem.is_text_path` matches on `Path(...).suffix`, which is `''` for dotfiles and extensionless names. So `.gitignore`, `.dockerignore`, and `.dockerfile` in `TEXT_EXTENSIONS` are unreachable, and `Dockerfile` / `Makefile` / `LICENSE` / `README` return False. Fix `is_text_path` to also match on the basename for those cases, then expand the test to cover the full `TEXT_EXTENSIONS` set, dotfiles, extensionless names, and uppercase extensions (`.PY`, `.PNG`).
- [ ] Verify (and fix if needed) that killing a tmux session removes it from the UI immediately. When a session is killed — from the YOLOmux Kill action, an external `tmux kill-session`, or the agent process exiting — its tab/window/row should disappear from the top bar, the Windows list, and any panel right away, not linger until the next metadata poll or require a manual refresh. Check the refresh/diff path so dead sessions are pruned promptly; if a panel was showing that session, fall back to another window or an empty state instead of a frozen/disconnected pane.
- [ ] Verify (and fix if needed) that a newly launched tmux session gets picked up automatically. Whether started from YOLOmux (New tmux session) or externally (`tmux new-session` in another terminal), the new session should appear in the top bar / Windows list on its own, promptly, without a manual refresh. Confirm the session-discovery/refresh loop adds new sessions, not just prunes dead ones, and decide whether a brand-new session auto-focuses or just shows up in the list.

---

## Good Ideas From Similar Products

- Claude Code agent view: group sessions by `Needs input`, `Working`, `Ready for review`, and `Completed`; show last activity and current action; support peek/reply and attach/detach without killing the session; support cwd-scoped filtering and JSON output.
- Agent Cockpit: risk-rated approval queue, per-agent terminal, per-agent timeline, and full audit trail for tool calls, writes, and approval decisions.
- Cogpit: live stream of tool calls/file edits, file change panel, token/cost/context usage, full-text search, and remote/mobile viewing.
- ClauBoard: explicit event schema, JSONL-style persistence, run lifecycle, task board, event timeline, and dependency/pipeline concepts.
- dmux: tmux plus git worktrees, automated worktree lifecycle, read-only file browser per worktree, native notifications when panes need attention.
- Nova Code: self-hosted workspaces, queues, scheduling/automations, files/git view, rules/templates, and normalized streaming across different agents.
- Agent Watch/lazyagent-style monitors: broad agent discovery across Claude, Codex, Cursor, Gemini, Amp, and OpenCode; status API and menu-bar or notification surfaces.

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
