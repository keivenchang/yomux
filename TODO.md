# YOLOmux TODOs

YOLOmux-specific roadmap notes. Keep UI, terminal, YOLO approval, session state, and launch ideas here instead of the Project TODO list.

---

## Product Direction

YOLOmux should stay a lightweight local tmux browser control plane. The useful niche is not another full SaaS-style orchestration stack. It is a fast local UI for existing Claude/Codex/tmux sessions with clear state, safe YOLO controls, repo metadata, file paste/upload, and low-friction attach/reply.

Borrow from other tools only when the feature improves the local control loop: know which session needs attention, understand what changed, approve or block risky work, and jump back into the right terminal quickly.

---

## For The Next Agent (orientation)

- Read `AGENTS.md` FIRST. It has the canonical: version-bump rule, dev-server restart procedure (port `7778` MUST restart via `systemd-run` — a naive `pkill`+relaunch in a Bash call gets reaped and won't persist), and validation commands: `python3 -m py_compile yolomux.py tmux_wall.py auto_approve_tmux.py yolomux_lib/*.py`, then `python3 -m pytest tests`, then `node tests/layout_url.test.js`.
- Two live server instances run side by side: `:7777` and `:7778`. A settings or roster change should reflect on BOTH (see Settings Panel + Session roster lag).
- Code map: entry `yolomux.py` -> `yolomux_lib/cli.py`; HTTP routing `yolomux_lib/server.py`; app state + tmux actions `yolomux_lib/app.py`; session/agent discovery `yolomux_lib/sessions.py`; repo/PR/CI metadata `yolomux_lib/metadata.py`; file ops `yolomux_lib/filesystem.py`; shared helpers + paths `yolomux_lib/common.py`; server-rendered HTML `yolomux_lib/web.py`; all frontend logic `static/yolomux.js` (+ `static/yolomux.css`); YOLO approval detector `auto_approve_tmux.py` and worker `yolomux_lib/auto_approve_worker.py`.
- State lives in `~/.config/yolomux/state.json`; the YOLO event log (great for confirming state-machine behavior) is `~/.local/state/yolomux/events.jsonl`.
- NAVIGATION RULE: line numbers in this TODO drift because the source changes constantly. Always GREP THE NAMED SYMBOL (`function foo`, `def foo`, an `id=`/class string) rather than trusting a line number. Numbers here were last verified 2026-05-29.
- START HERE: the "Easy Fixes — Researched (ready to implement)" section near the bottom is the vetted worklist (each has Repro / Where / Fix / Validate). EF1 (`is_text_path`) is the safest first; EF6 (spurious BLK) is the highest value. The "Bug Fixes And Tech Debt" section has the two deeper investigations (roster lag, BLK) written up in full.

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

IMPLEMENTATION STATUS (verified 2026-05-29 — NOTE: line numbers in this file drift because the source is edited constantly; GREP BY SYMBOL NAME, not line number):

- The app menu bar is ALREADY BUILT AND MOUNTED — this is a finish-wiring / gap task, NOT greenfield. The design gate below is effectively passed for the skeleton.
  - `appMenuTree()` (grep `function appMenuTree` in `static/yolomux.js`) defines the tree; `createAppMenuBar()` is appended into `#sessionButtons` (grep `createAppMenuBar`), so the old standalone session-tab strip is already replaced by the menu bar in that container.
  - Open/close, hover, keyboard nav, ARIA roles, and click-outside already exist: helpers `menuCommand` / `menuSubmenu` / `menuSection` / `menuSeparator`, plus `appMenuIsOpen` / `appMenuCommands` / `createAppMenu` / `createAppSubmenu` / `createAppMenuCommand`.
- Current LIVE top-level menus: `File`, `View`, `Tab`, `YOLO`, `Settings`, `Help`. Gaps vs the design tree below:
  - NAMING DECISION (2026-05-29): the menu that lists active/minimized/inactive tabs is named `Tab` (singular, Chrome-style). `Tab` is accurate because the menu lists/navigates Tabs, and it avoids overloading `window` (which already means a tmux window via the `<`/`>` step).
  - File today: New tmux session (submenu), File Explorer, Open file… (disabled), Log out. MISSING: Rename / Kill tmux session, Resume. NOTE: Log out currently lives in File, not Settings.
  - View today: tab-metadata toggle, Layout submenu (Single pane / Grid / Wall — ALL disabled, "Coming after menu navigation is stable"), inactive tabs. MISSING: Filter/Sort, "Branch Info: show", panel-tabs visibility.
  - YOLO today: Enable/Disable YOLO for the active session, Open event log. MISSING: Policy, Open rule file, Approval queue, Audit log, Risk legend.
  - Settings today: Notify (mirror), Refresh (mirror). MISSING: "Global settings…" (no Settings panel exists yet), Log out mirror.
  - Help today: version (disabled), Keyboard shortcuts (disabled). MISSING: real shortcuts list, Docs link.
  - NOT BUILT yet: per-pane left dropdown, compact rich rows in the `Tab` menu, per-pane kebab, right-click-tab rename.

DESIGN GATE (mostly satisfied — the skeleton is built; remaining gate work is reconciling the items below against the live `appMenuTree()`):

- [x] Decision confirmed: the standalone top session-tab strip is gone; `#sessionButtons` now hosts the menu bar. New session for Claude AND Codex lives in the File menu (`New tmux session` submenu).
- [x] Reconcile the live menu (`File/View/Tab/YOLO/Settings/Help`) with the design tree below — `Log out` stays in File for now and can be mirrored in Settings later.
- [x] Rename `Panes` menu -> `Tab` (singular, like Chrome's `Tab` menu). Scope: the `appMenuTree()` entry `id: 'tab'` / `label: 'Tab'`; the top-bar layout and design tree in this section; and the README terminology section. This name matches the glossary (the menu lists Tabs), so the README's pane/tab/window definitions stay correct — only the menu label changed; do NOT rename the `pane` (split region) or `window` (tmux window) concepts.

Current controls to be reorganized (inventory):

- Top bar today: brand + version, session-tab strip (`#sessionButtons`), latency meter, `Notify`, `Refresh`, `Log out`, `status`, tab-metadata toggle (`#`), HTTPS warning.
- Per-pane tab strip today: `<` / `>` window step, `Terminal`, `Tx` (transcript), `AI` (summary), `Log` (events), `Info` (detail toggle), window-close.
- Other surfaces: File Explorer (tree + editor), Files panel, layout (single / grid / wall), inactive-tabs tray menu, per-session state badges (`RUN`/`EXEC?`/`YOLO?`/`QUES?`/`BLK`/`OFF`/`TEST`/`PR`/`IDLE`/`DONE`).

Menu naming follows the latest YOLOmux terminology: `File` (create/manage), `View` (display options), `Tab` (Chrome-style — lists and navigates open tabs: active, minimized, inactive), plus app-specific `YOLO` and `Settings`, and `Help`.

MENU INSPIRATIONS (Chrome / Safari / iTerm — what to borrow):

- Chrome `Tab` menu (the direct model for our `Tab` menu): `Search Tabs`, `Next Tab` / `Previous Tab`, `Duplicate Tab`, `Pin Tab`, `Mute Site`, `Move Tab to New Window`, then the LIST of open tabs with a checkmark on the current one. Adopt: type-to-filter (= Search Tabs), Next/Previous, and the rich-row tab list with active-row highlight. Map "Move Tab to New Window" -> move a tab into another YOLOmux pane / split.
- Chrome right-click-tab menu: `New tab`, `Duplicate`, `Pin`, `Mute`, `Close`, `Move to new window`. Map to our right-click-tab context menu (`Rename`, `Kill`, `YOLO policy`, move-to-pane).
- Chrome `File`: `New Tab`, `New Window`, `Reopen Closed Tab`, `Open File`, `Close Tab`. "Reopen Closed Tab" maps to our File -> `Resume` (recent Claude/Codex conversations).
- Safari: a `Show All Tabs` overview (grid of all tabs) — a richer alternative/companion to the dropdown tab list; and it splits tab actions between `File` (New Tab) and `Window` (Next/Previous, Move Tab). We consolidate navigation into one `Tab` menu instead — cleaner.
- iTerm separation, which our top-level split mirrors: `Shell` = create (New Window/Tab, Split, Close) -> our `File`; `Session` = per-session actions (Edit/Rename, Restart, Logging) -> our per-pane kebab + right-click-tab menu; `Window` = navigate (Select Next/Previous Tab, Select Next/Previous Pane, Move Tab, list) -> our `Tab` menu + keyboard nav. Also borrow iTerm `Edit Tab Title` (= our rename) and `View -> Toggle Tab Bar` (= our tab-metadata / panel-tabs toggles).
- Net structure to converge on: `File` = create/open (Chrome File + iTerm Shell), `Tab` = navigate/act-on-tabs (Chrome Tab + iTerm Window's tab/pane selection), `View` = display toggles (all three), per-pane kebab + right-click = per-session actions (Chrome right-click-tab + iTerm Session).

Proposed top-bar layout (left to right): `YOLOmux ver` · `[File ▾] [View ▾] [Tab ▾] [YOLO ▾] [Settings ▾] [Help ▾]` · active-session chip (label + state badge) · latency · `Notify` · `Refresh` · status · `Log out`.

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
  - Branch Info: show — opens the Branch Info viewer for the active session (also still available as the per-pane `Info` tab; once open it appears in the Tab menu)
- [ ] Tab ▾  (navigate — a FLAT list of active, minimized, and inactive tabs, with checkmark/highlight on the active one, click/Enter to focus, type-to-filter)
  - Just lists tabs and panes, nothing to "launch" from here — opening items happens in File / View. Ordering, top to bottom:
  - tmux sessions first : each open terminal tab
  - then editors : each open File Editor tab
  - then other open viewers : File Explorer, Branch Info, Transcript, AI Summary, Event Log (only the ones currently open)
  - Each row carries the SAME rich info already shown in the existing tab-metadata view, but packed more compactly so it reads like a real dropdown menu (one tight row per tab): agent badge (`YO`/`BL`/… + session number), state badge (`RUN`/`BLK`/…), PR number, commit-style title, branch, repo path (e.g. `dynamo2 -> dynamo3`), and dirty count. The active tab's row is highlighted (green) like the current selection.
  - This is a denser re-layout of the existing rich rows, not new data — reuse the metadata that already feeds the tab strip.
- [ ] Per-pane left dropdown (the caret on the LEFT of each panel's tab strip): looks IDENTICAL to the Tab ▾ menu above (same compact rich-row format), but scoped to just THAT pane's tabs — i.e. only the tabs/views belonging to that one panel (its tmux sessions + Terminal / Tx / AI / Log / Info), not every pane in the app. Same row styling, same active-row highlight.
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
- [ ] Right-click context menu on a session tab (the pane tab / panel header): `Rename session` (inline edit, same affordance as the file-tree rename — grep `function beginFileTreeRename`, ~2620), plus `Kill session` and `YOLO policy`. This is the expected shortcut so users do not have to open the File menu just to rename.
- [x] Multi-line tab wrapping: when a pane's tabs wrap to 2+ rows (the `fix: wrap crowded tabs` work, commit `e5df095`), the second row and beyond should use the full width INCLUDING the area beneath the pane toolbar (`<` / terminal / `Tx` / `AI` / `Log` / `Info` / `×`), instead of stopping short and leaving that space blank. Only the FIRST row needs to reserve room for the toolbar. Implemented by floating the pane toolbar and rendering pane tabs as inline-flex items so wrapped rows flow under the toolbar.
- [x] Fix float-based wrapped tab alignment/overflow. The bad path was `.pane-tab { transform: translateY(2px) }`: it made a single-row tab look flush but did not affect layout, so wrapped rows could paint past `.panel-head` and into the detail row. Fixed by removing the transform, making `.pane-tab`'s real height match `.pane-tabs` (`28px`), and removing the header bottom border so the tab bottom and detail row share the same edge. Verified in headless Chrome with one-row and two-row tab strips: active-tab gap is `0`, `.panel-head` grows to contain both rows, and no tab bottom exceeds `.panel-detail-row` top.
- [x] Keep an EMPTY placeholder pane only for Finder's left/right sizing. Concrete case: left = Finder (File Explorer), right = a tab. When the right side's last tab is closed/killed, keep an empty placeholder pane on the right so Finder stays right-sized and the user can drop a new tab into the empty slot. Symmetric case: right = Finder, left closes, keep the left placeholder. But when Finder is split top/bottom with another pane, closing/killing the other pane collapses the vertical split so Finder expands up/down. The codebase already has the placeholder concept: `emptyPlaceholderPaneState()` -> `{tabs: [], placeholder: true}`; `compactLayoutNodeInfo` keeps placeholder leaves only in row splits that contain Finder, plus the global all-empty fallback.

Cross-cutting requirements for all menus:

- [ ] Keyboard accessible (open/close, arrow navigation, Esc), ARIA menu roles, click-outside to close, one menu open at a time (reuse the existing popover-open machinery).
- [x] Fix awkward hover timing (classic menubar behavior). FIRST open (no menu currently open) waits 300 ms before popping on hover. Once a menu is ALREADY open, moving the pointer to another top-level menu switches INSTANTLY with no delay.
- [ ] Mobile: menus collapse into a single hamburger (ties to P7).
- [ ] Read-only mode disables mutating items (New, Kill, Settings writes, YOLO toggles) the same way the current `Notify`/buttons do.

### P1: YOLO Event Log, Audit, And Queue

- [x] Add a persistent YOLO event log under `~/.local/state/yolomux/events.jsonl`, while keeping compact app state in `~/.config/yolomux/state.json`.
- [x] Record approval decisions, blocked commands, worker errors, session start/stop, terminal disconnects, uploads, pasted images, summary runs, state changes, and user-visible notifications.
- [x] Add a per-session YOLO audit panel showing recent approved/blocked decisions with timestamp, command text, matched rule, and session.
- [ ] Add an approval queue view for pending high-risk actions. Start read-only first if live interception is hard.
- [ ] Add per-session YOLO policy. Initial modes: `off`, `prompt-only`, `safe`, `edit`, `full`. Make policy visible on the YOLO button.
- [ ] Risk labels should be boring and concrete: `read`, `edit`, `network`, `process`, `delete`, `credential`, `unknown`.
- [ ] Replace the unhelpful top-right `YOLO on: 6` status string. A bare count is not actionable and does not say WHICH sessions are auto-approving. Move YOLO state to two better surfaces: (1) per-session — a small YOLO indicator on each session's badge/row (top bar + Tab menu) so you can see exactly which sessions have auto-approve on; (2) global — fold the count into the `YOLO ▾` menu as a badge (e.g. `YOLO ▾ 6`), and clicking it lists/toggles the enabled sessions. Then drop the standalone red `YOLO on: N` text from the top-right cluster.

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
- [ ] Handle unsaved edits safely: if the editor has local unsaved changes and the file also changed on disk, do not silently overwrite the user's edits — show a conflict notice and let them keep theirs or reload from disk. PARTIALLY DONE: `filesystem.write_file` already takes `expected_mtime` and rejects a save when the on-disk mtime changed (grep `expected_mtime` — raises "file changed on disk"). So save-time conflict detection exists; this item is the load-time / live-refresh side plus the keep-mine/reload UI.
- [ ] Handle the open file being deleted or moved on disk: show a clear state instead of stale content.
- [ ] Mechanism: NO file-watch library is installed (no `inotify`/`watchdog`, verified 2026-05-29) — use mtime polling. Watch the explorer's current directory and the open file on the server via `st_mtime` (read endpoints already return `mtime`) and push changes to the browser over the existing channel; debounce rapid bursts. Reuse existing refresh plumbing rather than adding a new fast poll loop.

### P2: Conditional Window-Step Arrows

The per-pane window-step buttons (`<` / `>`, the `window-step` controls in `static/yolomux.js` that page through `[Codex, Claude, bash, ...]` tmux windows in the current session) currently always show. Make them appear only when there is somewhere to step to.

- [x] Show `<` only when a tmux window exists BEFORE the current window in this session.
- [x] Show `>` only when a tmux window exists AFTER the current window in this session.
- [x] When the session has only one window, hide BOTH arrows.
- [x] Edge cases: recompute on window create/close/move and on session switch; do not reserve empty space when an arrow is hidden (the label should not jump). Decide whether ordering follows tmux window index or the current display order.

### P2: Notifications

- [x] Add browser notifications for state transitions only: `needs input`, `needs approval`, `YOLO blocked`, `terminal disconnected`, and `PR ready`.
- [x] Add notification throttling per session so repeated prompt text does not spam.
- [x] Add a small notification history in the event log so missed browser notifications are still visible later.

### P3: Info Drawer And Diff Panel

- [ ] Add a per-session info drawer with full path, branch, dirty/ahead/behind counts, PR, CI, Linear/issue metadata, latest summary, and recent events.
- [ ] Add a read-only changed-files list and unified diff panel using the session cwd.
- [ ] Make PR/CI/issue links clickable, but keep local branch names as text unless a real remote branch/PR exists.
- [ ] Add an explicit refresh button for repo metadata plus background polling with sane intervals.
- [x] Remove redundant info in the file-viewer detail/info panel. Today it repeats itself: the filename shows up as both the tab label AND the bold heading, and the full path shows up twice — once as the subtitle line under the heading and again in the `path` row (with the copy button). Show the filename once and the full path once. Keep the path row (it has the copy affordance) and drop the duplicate subtitle, or vice-versa. Also collapse `type: loading` / `status: loading` so a viewer that has no meaningful type/status does not show two placeholder "loading" rows.

### P3: Editor — Wrap, Preview, Split-Preview

The file editor today has a single Preview toggle (`#fileEditorPreview`, `web.py:119`) and a textarea hardcoded to `wrap="off"` (`web.py:123`). Make these first-class view modes plus a wrap toggle.

- [x] Word-wrap toggle: switch the editor textarea between `wrap="off"` (current) and soft word-wrap, so long lines wrap to the pane width instead of scrolling horizontally. Persist the preference (Settings → Terminal/Editor).
- [ ] Three explicit view modes for the editor: `Edit`, `Preview`, `Split-Preview` (a small segmented control in the editor head). Edit = textarea only; Preview = rendered view only (reuse the existing `#fileEditorPreviewPane`).
- [ ] Split-Preview: split the current editor window in half — left = editor (textarea), right = rendered preview — side by side in the same panel.
- [ ] Split-Preview synced scroll: the two halves scroll together (scrolling the editor scrolls the preview to the matching position, and vice-versa). Map by source line / proportional offset so headings and code blocks stay roughly aligned.
- [ ] Preview content by type: Markdown renders to formatted HTML (existing marked.js path); non-Markdown (sh, py, etc.) shows the syntax-highlighted read view (reuse `#fileEditorHighlight`) so Split-Preview is useful for code too, not just `.md`.
- [ ] Toolbar icons (EASY): make the Wrap button just a wrap glyph (↪) instead of the word "Wrap", and the Save button a disk glyph instead of "Save". Buttons are `#fileEditorWrap` / `#fileEditorSave` (`web.py`, grep the ids). Swap the button text for the glyph + CSS; keep the accessible `title` / `aria-label` text so it is still discoverable.
- [ ] Soft-wrap continuation marker: when word-wrap is on, show a wrap glyph (↪) at the START of each wrapped continuation line, so a soft-wrapped line is visually distinct from a real new line. CONSTRAINT: a plain `<textarea>` (`#fileEditorTextarea`) cannot draw per-visual-line glyphs — the browser wraps text but exposes no per-wrapped-line hook. Needs either a code-editor component (CodeMirror/Monaco) OR a custom overlay that mirrors the textarea wrapping and paints the ↪ markers (could extend the existing `#fileEditorHighlight` `<pre>` overlay). Decide the approach before building — this is NOT a quick textarea tweak.
- [ ] Line-number gutter option: add line numbers as an editor toggle. Same `<textarea>` constraint as the continuation marker (needs the overlay / editor-component approach). Numbers count SOURCE lines — a soft-wrapped line keeps one number and its ↪ continuation rows are not numbered.
- [ ] Preview background tint: in Preview and Split-Preview, give the rendered side a slightly grayer background than the editor so the two halves are visually distinct at a glance (style `#fileEditorPreviewPane` / the preview half). Note: Split-Preview itself is the existing "Split-Preview" bullet above — this just adds the visual distinction.

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

- [ ] Add a Settings panel, reachable from the menu (`Settings ▾` → `Global settings…`; the `Settings` menu already exists and currently holds only the Notify/Refresh mirrors). Today the tunables are hardcoded as `const`s near the top of `static/yolomux.js` (grep `const metadataRefreshMs`) and on the server, with no UI — making them runtime-configurable means routing them through a settings object instead of module consts.
  - Environment facts (verified 2026-05-29): no file-watch library is installed (no `inotify`/`watchdog`) — use mtime polling for cross-server reload. Only `pyyaml 6.0.1` is available, NOT `ruamel.yaml` — so preserving inline comments on a UI-driven save needs a decision: add a `ruamel` dependency, OR re-emit `settings.yaml` from a fixed commented template (don't round-trip), OR accept that a UI save drops hand-added comments. Hand-editing the file keeps comments either way.
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
- [ ] File Explorer quick-access paths: a user-editable list of pinned directories (e.g. `~`, `/tmp`, custom dirs) the explorer can jump to. See the matching item under "File Explorer".
- [ ] Each numeric setting needs a sane min/max clamp and a Reset-to-default button so a bad value cannot freeze the refresh loop.

### Transcript (Tx) View

- [ ] Show the session's transcript file path (the `.jsonl`) in the Transcript (Tx) view. Today the header (`transcript-head`, grep `>Transcript<`, ~6576) just says "Transcript", and the path appears only briefly in the loading placeholder (`path: ${agent.transcript}...`, ~7622) before `refreshTranscriptPreview` overwrites it. Show the path persistently — e.g. under the `transcript-head` — with a copy button (reuse the path-copy affordance). Source is `agent.transcript` (set in `sessions.py` for both claude `read_claude_agent` and codex `read_codex_agent`, ~195 / ~271). Handle the no-transcript case (currently "no agent transcript found", ~7627) with a clear "transcript not found" state rather than a blank path.

### File Explorer

- [x] Add a `Download` item to the file right-click context menu (grep `file-context-menu`, ~2542), alongside the existing Copy full/raw/relative path / Rename / Delete. Downloads the file to the browser. Backend `filesystem.read_raw` already streams bytes; add (or reuse) a raw-file endpoint that sets `Content-Disposition: attachment; filename=...` so the browser saves instead of previewing. For a directory, either disable Download or offer a zip. Support multi-select (download each, or a single zip). Respect read-only mode rules and the `MAX_RAW_BYTES` cap.
- [x] When opening a text file, group it into the EXISTING editor window instead of spawning a new editor panel. If an editor window is already open, add the file as a new tab in that window and focus it (and just focus the tab if the file is already open there). Only create a new editor window when none exists yet. Avoids one-editor-panel-per-file sprawl in the layout.
- [ ] Configurable File Explorer quick-access paths (e.g. `/tmp` + other custom dirs), set in Settings. NOTE: this is shortcuts, not new access — the explorer FS scope is already `/` (`filesystem.py` header: "No sandbox root"), so any path is reachable by navigating; today it just defaults to `homePath` and you have to walk there. Add a list of pinned quick-access roots shown in the explorer (a favorites/shortcuts row, or selectable roots), each opening via the existing `openFileExplorerAt(path)` / `fileExplorerRoot` machinery (grep `openFileExplorerAt`). Store the list as a user setting (`settings.yaml` once the Settings Panel lands; until then, localStorage). Default could include `~` and `/tmp`. Validate paths exist + are readable before showing; gracefully skip missing ones. Ties to the Settings Panel section.
- [ ] Make the Finder root path bar typable. Today it is a static `<div id="fileExplorerPath">` (`web.py`, set via `textContent` at the `fileExplorerPath` assignments in `static/yolomux.js`) — read-only. Make it an editable input (or contenteditable): type/paste a path, press Enter to jump there via `openFileExplorerAt(value)`; Escape reverts to the current root; validate the path (exists + is a readable dir) and show an inline error instead of navigating on a bad path. Apply to both the overlay explorer and the panel variant (`.file-explorer-path-copy-panel` head). Keep the existing copy button. Nice-to-have: `~` expansion and basic tab/`Tab`-key path completion.

### Bug Fixes And Tech Debt

- [ ] Touchpad scrolls the terminal far too fast ("zooms"), while a mouse wheel scrolls fine. Root cause: the terminal wheel handler `enableTerminalScroll` (grep `function enableTerminalScroll`, ~5026) uses only the SIGN of `event.deltaY` and maps EVERY wheel event to a fixed line count — `terminalWheelScrollLines = 3` (grep the const), or `term.rows * terminalWheelPageFraction` with Shift. It ignores `deltaY` magnitude and `event.deltaMode`. A mouse wheel fires one discrete event per notch (so fixed-3-lines feels right), but a touchpad fires many high-frequency, small-pixel-delta events per gesture, each mapped to the same 3 lines -> runaway scroll. Fix: derive the scroll amount from the actual delta — when `deltaMode === 0` (pixels, typical touchpad) divide `deltaY` by a line-height / sensitivity factor; when `deltaMode === 1` (lines, typical wheel) use `deltaY` directly; accumulate FRACTIONAL lines (`queueTmuxScroll`, ~5043, already accumulates and `Math.ceil`s, so fractions work). Add a touchpad sensitivity factor (and consider a heuristic: `deltaMode === 0 && Math.abs(deltaY) < ~50` => touchpad => scale down). Also handle `event.ctrlKey` wheel events (trackpad pinch) explicitly — today they fall through as fast scroll; either ignore them or treat as intentional font zoom, not scroll. Consider exposing the sensitivity as a Settings value later. Verify: a mouse-wheel notch still scrolls ~3 lines (unchanged feel); a gentle two-finger swipe scrolls a comparable, non-runaway amount; a pinch does not fly-scroll. Cross-check the README "Mouse wheel scrolling in a terminal sends tmux copy-mode scroll commands" note still holds.
- [x] Fix `tests/test_filesystem.py:177` `test_is_text_path_recognizes_known_extensions`. It passes but only spot-checks 5 extensions and hides a real bug: `filesystem.is_text_path` matches on `Path(...).suffix`, which is `''` for dotfiles and extensionless names. So `.gitignore`, `.dockerignore`, and `.dockerfile` in `TEXT_EXTENSIONS` are unreachable, and `Dockerfile` / `Makefile` / `LICENSE` / `README` return False. Fix `is_text_path` to also match on the basename for those cases, then expand the test to cover the full `TEXT_EXTENSIONS` set, dotfiles, extensionless names, and uppercase extensions (`.PY`, `.PNG`).
- [x] Session roster lag: killed sessions linger and externally-created sessions are slow to appear (~15 s). Covers both the "kill removal" and "new-session pickup" bugs — same root cause.

  Current code — what it does (researched):
  - The server roster is `app.sessions`, refreshed ONLY by `refresh_sessions()` (`yolomux_lib/app.py:29` -> `list_tmux_session_names()`), which is called ONLY inside `transcripts_payload()` (`app.py:135`). So the live tmux set is re-scanned only when `/api/transcripts` is served.
  - The client fetches `/api/transcripts` on `setInterval(refreshTranscripts, metadataRefreshMs)` = every 15001 ms (`static/yolomux.js:88`). `refreshTranscripts` (`yolomux.js:7257`) calls `updateSessionList(session_order)` (`yolomux.js:1728`), which DOES reconcile correctly — drops killed sessions, adds new ones, rebuilds `layoutItems`/tabs, re-renders panels on change. The logic is right; it just runs on a ~15 s cadence.
  - The fast poll `refreshAutoStatuses` (every 1257 ms) hits `/api/auto-approve`; `auto_approve_status(None)` (`app.py:821`) returns statuses keyed by the STALE `self.sessions` and does NOT call `refresh_sessions`; the client `loadAutoStatuses` only loops over its existing `sessions`. So the fast poll can neither discover nor remove sessions.
  - UI-created sessions are instant: `createNextSession` (grep `async function createNextSession`) POSTs `/api/create-session?agent=...`, gets `payload.sessions`, and calls `updateSessionList` + render immediately.
  - There is NO UI "kill tmux session" action today — `data-pane-close` only hides tabs from the layout (`removePaneFromLayout`), it does not kill tmux. So sessions die externally (agent exits / `tmux kill-session` elsewhere).

  Consequence:
  - New session (#6): UI-created -> instant; externally created -> appears only on the next `/api/transcripts` poll, up to ~15 s later.
  - Killed session (#5): removed from the roster only on the next `/api/transcripts` poll, up to ~15 s later. If its pane is open, the terminal WebSocket closes on death -> `terminalDisconnected(session)` flips the badge to `disconnected`/OFF within seconds (`sessionState`, `yolomux.js:1221`), but the tab/row/panel is NOT removed until the 15 s roster refresh.

  What needs to change (reconcile the roster on the fast cadence; pick one, A recommended):
  - [x] Option A (cheapest): include a fresh roster in the fast status payload. Have `auto_approve_status(None)` call `refresh_sessions()` first (one cheap `tmux list-sessions`, 3 s timeout — `common.py:483`) and add `"session_order": self.sessions`. Client `loadAutoStatuses`/`refreshAutoStatuses` then calls `updateSessionList(payload.session_order)` and re-renders on change. Result: add + remove reflected within ~1.25 s instead of ~15 s. Optionally debounce the tmux call to ~1 s to avoid one subprocess per fast tick.
  - [ ] Option B (event-driven kill): when a terminal WebSocket closes (already detected), confirm via a roster check that the session is gone and prune it from the UI immediately, instead of waiting for the poll. Handles the open-pane kill case fastest; pair with A for closed-pane sessions.
  - [ ] Option C (future UI Kill action, per the menu plan): after killing, trigger the roster reconcile immediately (like `createSession` does) so UI-kills are instant.

  Repro:
  - #6: `tmux new-session -d -s testX` in another terminal; time until it appears in the YOLOmux top bar (expect up to ~15 s today).
  - #5: `tmux kill-session -t testX`; time until its tab/row disappears (up to ~15 s today; badge may flip to OFF sooner if a pane was open).

  Validate:
  - Server test: assert `auto_approve_status(None)` payload includes a fresh `session_order` after the tmux set changes (stub `list_tmux_session_names`).
  - JS test: `updateSessionList` is already a pure function — `node tests/session_list.test.js` asserting add returns `changed=true` and appends to `layoutItems`, and remove drops the session and rebuilds slots.
  - Manual: after the fix, the repros above reflect within ~1.25 s.
- [x] Fix the spurious `BLK` (blocked) badge that flaps onto healthy, actively-working Codex sessions.

  Findings (root cause, confirmed against a live session and the event log):
  - The badge comes from `sessionState` (`static/yolomux.js`, the `agents.some(agent => agent.error) || /blocked|error|failed|failure|stuck/.test(agentText)` branch) returning `blocked` with reason `"agent reported an error or blocker"`. The YOLO event log shows this exact reason flapping `working -> blocked -> working` every 1-2 s on a session that is just running.
  - The `agent.error` is set by `read_codex_agent` (`yolomux_lib/sessions.py:246`): `error = None if transcript_path else "codex transcript not found by cwd"`. So whenever the transcript lookup returns `None`, the agent is (wrongly) treated as errored.
  - The lookup `find_recent_codex_transcript` (`yolomux_lib/sessions.py:215`) matches a rollout file to the session by checking whether the cwd string appears in only the LAST 300 LINES (`tail_file_lines(path, 300)`). Codex records the cwd sporadically through the rollout, and on an active session the gaps between cwd occurrences near the tail exceed 300 lines (measured 404 and 557 line gaps in a 5895-line rollout). As Codex streams output (big tool dumps, file edits), the sliding 300-line window repeatedly lands inside a gap with no cwd -> lookup returns `None` -> `agent.error` set -> `BLK`. When the window next includes a cwd line, transcript is found, `error=None`, and it flips back to working. That race is the RUN<->BLK oscillation.

  Suggested fixes (do the first; the second is defense-in-depth):
  - [x] Make `find_recent_codex_transcript` robust instead of tail-only. The cwd is written once in the rollout HEADER (session-meta, line 1), so match on the head rather than (or in addition to) the tail. Better: resolve the transcript once and CACHE it per Codex pid (and/or by rollout session-id), so a running session keeps a stable transcript path and the lookup does not re-scan and flap every poll. Invalidate the cache when the pid dies.
  - [x] Stop treating "transcript not found" as a blocker in `sessionState`. A missing/unresolved transcript is not an agent error. Either give `AgentInfo` a separate non-error field for "transcript unresolved" (so `.error` is reserved for genuine failures), or exclude the transcript-not-found case from the `agents.some(agent => agent.error)` blocker test. Also tighten the broad `/blocked|error|failed|failure|stuck/` keyword fallback, which is independently false-positive-prone (e.g. an agent literally working on fixing an "error").
  - [x] Add a regression test: a long rollout (>300 lines) whose cwd appears only in the header and in sparse, >300-line-apart positions should still resolve to its transcript, and the session should report `working`, not `blocked`.

### Easy Fixes — Researched (ready to implement)

Each item below was reproduced this session and traced to an exact location. Small, localized, low-risk. Order is roughly easiest-first. (These duplicate the fuller bullets in the sections above but collect the actionable Repro/Where/Fix/Validate in one worklist.) Test harness: Python via `python3 -m pytest tests/`; JS via standalone `node tests/<name>.test.js` (see `AGENTS.md:140`); there is no HTTP/server test yet. NOTE: line numbers below are as of 2026-05-29 and drift as the source changes — GREP THE NAMED FUNCTION rather than trusting the line.

EF1 — `is_text_path` misses dotfiles + extensionless names (Bug Fixes)
- Repro (confirmed): `python3 -c "from yolomux_lib import filesystem as f; print(f.is_text_path('/tmp/.gitignore'), f.is_text_path('/tmp/Dockerfile'), f.is_text_path('/tmp/Makefile'))"` prints `False False False`. Note `/tmp/foo.PY` already returns True — uppercase is handled by `.suffix.lower()`, so that part is NOT broken.
- Where: `yolomux_lib/filesystem.py:268` `is_text_path` uses `path.suffix.lower() in TEXT_EXTENSIONS`. `Path('.gitignore').suffix == ''`, so the `.gitignore` / `.dockerignore` / `.dockerfile` entries in `TEXT_EXTENSIONS` (`filesystem.py:23`) are dead and extensionless names never match.
- Fix: also test the basename — `name = path.name.lower(); return path.suffix.lower() in TEXT_EXTENSIONS or name in TEXT_EXTENSIONS or name in EXTENSIONLESS_TEXT_NAMES`. Add `EXTENSIONLESS_TEXT_NAMES = {'dockerfile','makefile','license','readme', ...}`; the existing `.gitignore`-style entries then match via `name in TEXT_EXTENSIONS`.
- Validate: expand `tests/test_filesystem.py:177` to assert True for `.gitignore`, `Dockerfile`, `Makefile`, `LICENSE`, every entry in `TEXT_EXTENSIONS`, and `foo.PY`; False for `foo.png` / `foo.exe`. Run `python3 -m pytest tests/test_filesystem.py -q`.

EF2 — Conditional window-step arrows (P2)
- Repro (confirmed live): session 6 has exactly one tmux window (`tmux list-panes -t 6` shows one `window_index`), yet both `<` and `>` render; both should be hidden.
- Where: `static/yolomux.js` `panelControlsHtml` (grep `function panelControlsHtml`, ~6220) renders the `window-step` buttons unconditionally; `stepAttrs` is the inner helper (~6225). Data is client-side already: `transcriptMeta.sessions?.[session].panes`, each pane has `.window` and `.window_active` (server sends all windows' panes — confirmed in `sessions.py` session build).
- Fix: add a pure helper `windowStepVisibility(panes) -> {prev, next}`: `windows = unique(panes.map(p=>p.window))`; if `windows.length <= 1` return both false; `active = panes.find(p=>p.window_active)?.window`; `prev = windows.some(w=>w<active)`, `next = windows.some(w=>w>active)`. In `panelControlsHtml`, omit each arrow when false (do not reserve empty space).
- Validate: `node tests/window_step.test.js` (new) on `windowStepVisibility`: one window → both false; first-of-three → prev false/next true; last → prev true/next false; middle → both true. Manual: session 6 shows no arrows; a multi-window session shows arrows only toward an existing neighbor.

EF3 — Redundant info in file popover (P3)
- Repro: open a file (e.g. common.py); popover shows filename as title AND full path twice (`.popover-subtitle` + the `path` row), and `type` / `status` show the same value in the normal case (both `loading` while loading).
- Where: `filePopoverHtml` (grep `function filePopoverHtml`, ~3725). The `.popover-subtitle` line (~3739) duplicates the full path already in the `path` row (`popoverRow('path', filePopoverPathHtml(path))`, ~3730, which carries the copy button). `popoverPairRow('type', kind, 'status', status)` (~3731) shows `status == kind` normally (the `status = ... : kind` line, ~3729). Body verified unchanged 2026-05-29.
- Fix: remove the `.popover-subtitle` line (keep the `path` row + copy button). Render the status half only when it differs from `kind` (`modified`/`loading`/`error`); otherwise emit just `popoverRow('type', kind)`.
- Validate: extract the rows into a pure `filePopoverRows(path, state)` helper and `node tests/file_popover.test.js` asserting the full path appears once and no duplicate type/status. Manual: open popover — path once, no double `loading`.

EF4 — Editor word-wrap toggle (P3 editor)
- Repro: open a long-line file; the textarea (`wrap="off"`, `web.py:123`) scrolls horizontally instead of wrapping.
- Where: editor head `yolomux_lib/web.py:116-126` (Preview/Save/Close buttons); textarea `#fileEditorTextarea`. Per-panel editors (`file-editor-panel`) exist too — apply globally.
- Fix: add a `Wrap` toggle button (`id=fileEditorWrap`) next to Preview; on click flip `textarea.wrap` between `off` and `soft` (applies live in modern browsers) and toggle an `is-wrap` class; persist in `localStorage 'yolomux.editorWrap'` until the Settings panel lands; apply to all open editor textareas.
- Validate: manual — toggle with a long-line file; preference survives reload. Optional: unit-test a pure `nextWrapMode(cur)` helper via node. (No editor-DOM test harness today.)

EF5 — Download in file right-click menu (File Explorer; single-file v1)
- Repro: right-click a file — menu has Copy full/raw/relative path, Rename, Delete; no Download.
- Where: frontend — the `file-context-menu` builder (grep `file-context-menu`, ~2542, built with `appendContextMenuButton`; the Rename button is added right there). Backend `handle_fs_raw` (grep `def handle_fs_raw`, server.py ~337; route `/api/fs/raw`) already streams bytes via `filesystem.read_raw` but serves inline (Content-Type only, no disposition).
- Fix: backend — when query has `download=1`, add `Content-Disposition: attachment; filename="<sanitized basename>"`. Frontend — `appendContextMenuButton(menu, 'Download', () => triggerDownload(fullPath))`; `triggerDownload` creates a hidden `<a href="/api/fs/raw?path=<enc>&download=1" download>` and clicks it (cookie auth carries). Disable for directories (`entry.kind==='dir'`); multi-select/zip deferred; `MAX_RAW_BYTES` already enforced (413) by `read_raw`.
- Validate: add the first server test — request `/api/fs/raw?path=<file>&download=1` and assert `Content-Disposition: attachment` + the bytes. Manual: click Download — browser saves the file.

EF6 — Spurious BLK from flaky Codex transcript lookup (Bug Fixes; highest value) — see the detailed "spurious BLK" entry above
- Repro (confirmed): a 5895-line real rollout records cwd only sparsely near the tail (measured 404- and 557-line gaps); the last-300-line window intermittently misses it -> `None`; event log shows session 6 flapping `working->blocked->working`.
- Where: `yolomux_lib/sessions.py:215` `find_recent_codex_transcript` (tail-only, `tail_file_lines(path,300)`); error at `sessions.py:246`; consumed by `sessionState` `agents.some(a=>a.error)` branch.
- Fix: match cwd from the rollout HEADER — confirmed the first JSONL line is `type: session_meta` with `cwd` in its payload (always present). Read the first line, `json.loads`, compare `payload.cwd`; keep the tail scan as fallback. Optionally cache the resolved path per Codex pid to stop per-poll rescans.
- Validate: add an optional `root: Path | None = None` param to `find_recent_codex_transcript` (default `Path.home()/'.codex'/'sessions'`) for testability. New `tests/test_sessions.py`: build a temp `root` with a `rollout-*.jsonl` whose cwd appears only in a `session_meta` header followed by >300 non-cwd lines; assert the OLD tail-only logic returns None (reproduces the bug) and the header-based fix resolves it. Manual: session 6 stays RUN with no BLK flapping in the event log.

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
