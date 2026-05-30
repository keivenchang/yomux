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
- START HERE: the old EF1-EF6 easy-fix list is complete. If `DOIT.4.md` is present, do that easy/fast batch first: lowercase `tmux` label, rename label with session name, YO icon in the YOLO menu item, trimmed tmux-tab right-click menu, file-tree triangle polish, whole-row/follow-cursor image preview, clickable YO in the `Tab` dropdown, `/` / `~` / `/tmp` quick-access buttons, and disconnected overlay. Then follow `DOIT.5.md` build order: relocate `YOLO on: N`, File Explorer root mode, editor Split-Preview, Preferences tab, then YOLO Rule Engine. If the DOIT files are gone, the same items are tracked below in this TODO.

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
- Current LIVE top-level menus: `File`, `View`, `Tmux`, `Tab`, `Settings`, `Help`. Gaps vs the design tree below:
  - NAMING DECISION (2026-05-29): the menu that lists active/minimized/inactive tabs is named `Tab` (singular, Chrome-style). `Tab` is accurate because the menu lists/navigates Tabs, and it avoids overloading `window` (which already means a tmux window via the `<`/`>` step).
  - File today: File Explorer, Open file… (disabled), Log out.
  - Tmux today: New tmux session (submenu), Rename / Kill tmux session, YOLO enable/disable for the active tmux session, Open event log, Resume session (disabled).
  - View today: tab-metadata toggle, Layout submenu (Single pane / Grid / Wall — ALL disabled, "Coming after menu navigation is stable"), inactive tabs. MISSING: Filter/Sort, "Branch Info: show", panel-tabs visibility.
  - Settings today: Tab metadata (`#`), Notify, Refresh. These use framed icons that match the top-right controls, including active state. Log out stays only in File / top-right.
  - Help today: version (disabled), Keyboard shortcuts submenu, Open README.
  - NOT BUILT yet: per-pane left dropdown, compact rich rows in the `Tab` menu.

DESIGN GATE (mostly satisfied — the skeleton is built; remaining gate work is reconciling the items below against the live `appMenuTree()`):

- [x] Decision confirmed: the standalone top session-tab strip is gone; `#sessionButtons` now hosts the menu bar. New session for Claude AND Codex lives in the Tmux menu (`New tmux session` submenu).
- [x] Reconcile the live menu (`File/View/Tmux/Tab/Settings/Help`) with the design tree below — `Log out` stays in File and the top-right controls.
- [x] Rename `Panes` menu -> `Tab` (singular, like Chrome's `Tab` menu). Scope: the `appMenuTree()` entry `id: 'tab'` / `label: 'Tab'`; the top-bar layout and design tree in this section; and the README terminology section. This name matches the glossary (the menu lists Tabs), so the README's pane/tab/window definitions stay correct — only the menu label changed; do NOT rename the `pane` (split region) or `window` (tmux window) concepts.

Current controls to be reorganized (inventory):

- Top bar today: brand + version, session-tab strip (`#sessionButtons`), latency meter, `Notify`, `Refresh`, `Log out`, `status`, tab-metadata toggle (`#`), HTTPS warning.
- Per-pane tab strip today: `<` / `>` window step, `Terminal`, `Tx` (transcript), `AI` (summary), `Log` (events), `Info` (detail toggle), window-close.
- Other surfaces: File Explorer (tree + editor), Files panel, layout (single / grid / wall), inactive-tabs tray menu, per-session state badges (`RUN`/`EXEC?`/`YOLO?`/`QUES?`/`BLK`/`OFF`/`TEST`/`PR`/`IDLE`/`DONE`).

Menu naming follows the latest YOLOmux terminology: `File` (files/app actions), `View` (display options), `Tmux` (tmux session create/manage plus tmux-scoped YOLO controls), `Tab` (Chrome-style — lists and navigates open tabs: active, minimized, inactive), plus `Settings` and `Help`.

MENU INSPIRATIONS (Chrome / Safari / iTerm — what to borrow):

- Chrome `Tab` menu (the direct model for our `Tab` menu): `Search Tabs`, `Next Tab` / `Previous Tab`, `Duplicate Tab`, `Pin Tab`, `Mute Site`, `Move Tab to New Window`, then the LIST of open tabs with a checkmark on the current one. Adopt: type-to-filter (= Search Tabs), Next/Previous, and the rich-row tab list with active-row highlight. Map "Move Tab to New Window" -> move a tab into another YOLOmux pane / split.
- Chrome right-click-tab menu: `New tab`, `Duplicate`, `Pin`, `Mute`, `Close`, `Move to new window`. Map to our right-click-tab context menu (`Rename`, `Kill`, `YOLO policy`, move-to-pane).
- Chrome `File`: `New Tab`, `New Window`, `Reopen Closed Tab`, `Open File`, `Close Tab`. "Reopen Closed Tab" maps to our File -> `Resume` (recent Claude/Codex conversations).
- Safari: a `Show All Tabs` overview (grid of all tabs) — a richer alternative/companion to the dropdown tab list; and it splits tab actions between `File` (New Tab) and `Window` (Next/Previous, Move Tab). We consolidate navigation into one `Tab` menu instead — cleaner.
- iTerm separation, which our top-level split mirrors: `Shell` = create (New Window/Tab, Split, Close) -> our `File`; `Session` = per-session actions (Edit/Rename, Restart, Logging) -> our per-pane kebab + right-click-tab menu; `Window` = navigate (Select Next/Previous Tab, Select Next/Previous Pane, Move Tab, list) -> our `Tab` menu + keyboard nav. Also borrow iTerm `Edit Tab Title` (= our rename) and `View -> Toggle Tab Bar` (= our tab-metadata / panel-tabs toggles).
- Net structure to converge on: `File` = create/open (Chrome File + iTerm Shell), `Tab` = navigate/act-on-tabs (Chrome Tab + iTerm Window's tab/pane selection), `View` = display toggles (all three), per-pane kebab + right-click = per-session actions (Chrome right-click-tab + iTerm Session).

Proposed top-bar layout (left to right): `YOLOmux ver` · `[File ▾] [View ▾] [Tmux ▾] [Tab ▾] [Settings ▾] [Help ▾]` · active-session chip (label + state badge) · latency · `Notify` · `Refresh` · status · `Log out`.

KEEP AS DEDICATED TOP-RIGHT BUTTONS: `Notify`, `Refresh`, `Log out`. These stay as one-click buttons on the right of the top bar exactly as today (same icons, same behavior). The menus absorb the session tabs and the lower-frequency controls; these three high-use buttons remain visible.

ALSO MIRROR KEY DISPLAY CONTROLS IN THE SETTINGS MENU: `#` (tab metadata), `Notify`, and `Refresh` appear as items inside the `Settings ▾` menu, in addition to the top-right buttons. They use the same icon masks and show the same on/off state where applicable. `Log out` remains in File and the top-right cluster, not Settings.

Proposed menu tree (PROPOSAL — review before building):

- [ ] File ▾  (files / app actions)
  - File Explorer (open)
  - Open file…
  - --- (separator)
  - Log out
- [ ] Tmux ▾  (create / manage tmux sessions)
  - New tmux session ▸ : `+ Claude`, `+ Codex`, `+ Term` (each opens the P4 launch dialog: cwd, model/profile, permission mode, initial prompt, optional name)
  - Rename tmux session
  - Kill tmux session
  - Enable/Disable YOLO for Tmux Session `<name>`
  - Open event log
  - Resume ▸ : recent Claude/Codex conversations scoped to cwd (P4)
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
- [ ] Add the remaining YOLO controls under Tmux instead of restoring a top-level YOLO menu: policy modes, Open rule file, Approval queue, Audit log, and Risk labels legend.
- [ ] Settings ▾
  - `#` / Tab metadata (same icon + on/off state as the top-right button)
  - `Notify` (same icon + on/off state as the top-right button)
  - `Refresh` (same icon + behavior as the top-right button)
- [ ] Help ▾
  - Keyboard shortcuts
  - About / version
  - Open README
- [x] Per-pane kebab (`…`) on each panel head (keep the existing Term/Tx/AI/Log/Info strip): YOLO policy for this session, run summary, rename, kill, open event log. (No "attach" item — see the Tmux Session note above.)
- [ ] Add the remaining per-pane kebab actions: peek / reply.
- [x] Right-click context menu on a session tab (the pane tab / panel header): `Rename session` (inline edit, same affordance as the file-tree rename — grep `function beginFileTreeRename`, ~2620), plus `Kill session` and `YOLO policy`. This is the expected shortcut so users do not have to open the Tmux menu just to rename.
- [x] Multi-line tab wrapping: when a pane's tabs wrap to 2+ rows (the `fix: wrap crowded tabs` work, commit `e5df095`), the second row and beyond should use the full width INCLUDING the area beneath the pane toolbar (`<` / terminal / `Tx` / `AI` / `Log` / `Info` / `×`), instead of stopping short and leaving that space blank. Only the FIRST row needs to reserve room for the toolbar. Implemented by floating the pane toolbar and rendering pane tabs as inline-flex items so wrapped rows flow under the toolbar.
- [x] Fix float-based wrapped tab alignment/overflow. The bad path was `.pane-tab { transform: translateY(2px) }`: it made a single-row tab look flush but did not affect layout, so wrapped rows could paint past `.panel-head` and into the detail row. Fixed by removing the transform, making `.pane-tab`'s real height match `.pane-tabs` (`28px`), and removing the header bottom border so the tab bottom and detail row share the same edge. Verified in headless Chrome with one-row and two-row tab strips: active-tab gap is `0`, `.panel-head` grows to contain both rows, and no tab bottom exceeds `.panel-detail-row` top.
- [x] Keep an EMPTY placeholder pane only for Finder's left/right sizing. Concrete case: left = Finder (File Explorer), right = a tab. When the right side's last tab is closed/killed, keep an empty placeholder pane on the right so Finder stays right-sized and the user can drop a new tab into the empty slot. Symmetric case: right = Finder, left closes, keep the left placeholder. But when Finder is split top/bottom with another pane, closing/killing the other pane collapses the vertical split so Finder expands up/down. The codebase already has the placeholder concept: `emptyPlaceholderPaneState()` -> `{tabs: [], placeholder: true}`; `compactLayoutNodeInfo` keeps placeholder leaves only in row splits that contain Finder, plus the global all-empty fallback.

Cross-cutting requirements for all menus:

- [ ] Keyboard accessible (open/close, arrow navigation, Esc), ARIA menu roles, click-outside to close, one menu open at a time (reuse the existing popover-open machinery).
- [ ] App-level keyboard shortcuts: Ctrl/Cmd+B toggles the File Explorer / Finder on/off; Ctrl/Cmd+`,` (comma) opens the Settings / Preferences tab. Add a document-level `keydown` handler (there is no global shortcut registry today — the only Ctrl/Cmd+S handler is bound to `#fileEditorTextarea`, grep `event.key === 's'`). Cmd+B -> toggle: if `itemInLayout(fileExplorerItemId)` close it, else `selectSession(fileExplorerItemId)` (grep both). Cmd+`,` -> open the Preferences tab (`prefsItemId`, from DOIT.5 #5 — until that exists, open the Settings menu). CONFLICT TO HANDLE: Ctrl-B is the tmux PREFIX key — when a terminal pane is focused, Ctrl-B MUST pass through to tmux, so only fire the YOLOmux shortcut when focus is NOT inside a terminal (or make it Cmd-only on mac / a configurable binding). Also register both in the Help -> Keyboard shortcuts submenu so they're discoverable.
- [x] Menu widths are content-measured and viewport-clamped through shared CSS variables. Do not add fixed pixel min/max buckets for dropdown capacity; browser size, OS font metrics, zoom, and scrollbar behavior vary too much. Use intrinsic DOM measurement, percentages/viewport units, and container-relative constraints instead.
- [x] Fix awkward hover timing (classic menubar behavior). FIRST open (no menu currently open) waits 300 ms before popping on hover. Once a menu is ALREADY open, moving the pointer to another top-level menu switches INSTANTLY with no delay.
- [ ] Mobile: menus collapse into a single hamburger (ties to P7).
- [x] Read-only mode disables mutating items (New, Kill, Settings writes, YOLO toggles) the same way the current `Notify`/buttons do.

### P1: YOLO Event Log, Audit, And Queue

- [x] Add a persistent YOLO event log under `~/.local/state/yolomux/events.jsonl`, while keeping compact app state in `~/.config/yolomux/state.json`.
- [x] Record approval decisions, blocked commands, worker errors, session start/stop, terminal disconnects, uploads, pasted images, summary runs, state changes, and user-visible notifications.
- [x] Add a per-session YOLO audit panel showing recent approved/blocked decisions with timestamp, command text, matched rule, and session.
- [ ] Add an approval queue view for pending high-risk actions. Start read-only first if live interception is hard.
- [ ] Add per-session YOLO policy. Initial modes: `off`, `prompt-only`, `safe`, `edit`, `full`. Make policy visible on the tmux-session YOLO control.
- [ ] Risk labels should be boring and concrete: `read`, `edit`, `network`, `process`, `delete`, `credential`, `unknown`.
- [x] Replace the unhelpful top-right `YOLO on: 6` status string. YOLO state now lives on the per-session `YO` markers and the Tmux menu: the menu shows a small enabled-session count badge and includes a YOLO sessions submenu for toggling sessions. The top-right status now reports the action as `enabled YOLO for <session>` / `disabled YOLO for <session>` instead of the old red `YOLO on: N` string.

### P1: YOLO Rule Engine (user-configurable matching via YAML)

Today YOLO matching is hardcoded in `auto_approve_tmux.py`: a fixed `DANGEROUS_COMMANDS` set + `DANGEROUS_PATTERNS` denylist, with a binary outcome (press Enter to approve, or leave the prompt for manual action). Goal: let users declare, in a YAML file, what input patterns map to what action — so the same engine can auto-approve safe commands, auto-decline dangerous ones, or just notify, without editing Python.

DESIGN GATE: propose the schema and decide the options below before implementing. Do not write code until the rule shape is signed off.

- [ ] Decide file location and precedence. Proposal: `~/.config/yolomux/yolo-rules.yaml` (shared default) plus optional per-repo `.yolomux.yaml` that overlays it; per-session override via the Tmux menu.
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
- [ ] Add an "Open rule file" + "Reload rules" action in the Tmux menu, and show the active ruleset path/source in Settings.

### P1: File Explorer & Editor Live Refresh

When files change on disk, the File Explorer (Finder) and the open editor should update on their own instead of showing stale content.

- [x] Refresh the File Explorer tree when the watched directory changes on disk (files added / removed / renamed / modified). Current implementation uses the existing metadata refresh cadence, rebuilds the visible tree, and restores selection, expanded folders, and scroll position so the UI does not jump.
- [x] Refresh the open editor content when the currently-open file changes on disk, for any file type (md, sh, py, etc.); re-render the Markdown preview if it is showing.
- [x] Handle unsaved edits safely: if the editor has local unsaved changes and the file also changed on disk, do not silently overwrite the user's edits — show a conflict notice and let them keep theirs or reload from disk. `filesystem.write_file` already takes `expected_mtime` and rejects a save when the on-disk mtime changed; the frontend now also detects the load-time conflict and shows a Reload action.
- [x] Handle the open file being deleted or moved on disk: show a clear state instead of stale content.
- [x] Mechanism: NO file-watch library is installed (no `inotify`/`watchdog`, verified 2026-05-29) — use mtime polling. Watch the explorer's current directory and the open file on the server via `st_mtime` (read endpoints already return `mtime`) and push changes to the browser over the existing channel; debounce rapid bursts. Reuse existing refresh plumbing rather than adding a new fast poll loop.

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

The file editor today has a single Preview toggle (`#fileEditorPreview`, `web.py`) and a working word-wrap toggle. Make the preview path first-class view modes next.

- [x] Word-wrap toggle: switch the editor textarea between `wrap="off"` (current) and soft word-wrap, so long lines wrap to the pane width instead of scrolling horizontally. Persist the preference (Settings → Terminal/Editor).
- [ ] Three explicit view modes for the editor: `Edit`, `Preview`, `Split-Preview` (a small segmented control in the editor head). Edit = textarea only; Preview = rendered view only (reuse the existing `#fileEditorPreviewPane`).
- [ ] Split-Preview: split the current editor window in half — left = editor (textarea), right = rendered preview — side by side in the same panel.
- [ ] Split-Preview synced scroll: the two halves scroll together (scrolling the editor scrolls the preview to the matching position, and vice-versa). Map by source line / proportional offset so headings and code blocks stay roughly aligned.
- [ ] Preview content by type: Markdown renders to formatted HTML (existing marked.js path); non-Markdown (sh, py, etc.) shows the syntax-highlighted read view (reuse `#fileEditorHighlight`) so Split-Preview is useful for code too, not just `.md`.
- [x] Toolbar icons: make the Wrap button just a wrap glyph (↪) instead of the word "Wrap", and the Save button a disk glyph instead of "Save". Buttons are `#fileEditorWrap` / `#fileEditorSave` (`web.py`, grep the ids). The buttons keep accessible `title` / `aria-label` text.
- [ ] Soft-wrap continuation marker: when word-wrap is on, show a wrap glyph (↪) at the START of each wrapped continuation line, so a soft-wrapped line is visually distinct from a real new line. CONSTRAINT: a plain `<textarea>` (`#fileEditorTextarea`) cannot draw per-visual-line glyphs — the browser wraps text but exposes no per-wrapped-line hook. Needs either a code-editor component (CodeMirror/Monaco) OR a custom overlay that mirrors the textarea wrapping and paints the ↪ markers (could extend the existing `#fileEditorHighlight` `<pre>` overlay). Decide the approach before building — this is NOT a quick textarea tweak.
- [ ] Line-number gutter option: add line numbers as an editor toggle. Same `<textarea>` constraint as the continuation marker (needs the overlay / editor-component approach). Numbers count SOURCE lines — a soft-wrapped line keeps one number and its ↪ continuation rows are not numbered.
- [x] Preview background tint: in Preview and Split-Preview, give the rendered side a gray background so the two halves are visually distinct at a glance (style `#fileEditorPreviewPane` / the preview half). Note: Split-Preview itself is the existing "Split-Preview" bullet above — this just adds the visual distinction.

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

### Preferences Tab (Settings)

DECISION (2026-05-29): implement Settings as a dedicated PREFERENCES TAB — a virtual layout item like the File Explorer / Branch Info, not a modal. It opens from a menu, appears in the `Tab` list, and can be dragged/split into a pane like any other tab. Model it on the existing virtual items (grep `fileExplorerItemId = '__files__'` / `infoItemId = '__info__'` in `static/yolomux.js`; add e.g. `prefsItemId = '__prefs__'`). Group ALL the preferences below into sections within this one tab (e.g. General · Appearance · Notifications · Performance · YOLO · Terminal/Editor · File Explorer · Advanced).

- [ ] Build the Preferences tab. Today the `Settings ▾` menu only contains quick controls (`#`, Notify, Refresh), and tunables are hardcoded as `const`s near the top of `static/yolomux.js` (grep `const metadataRefreshMs`) and on the server, with no UI — making them runtime-configurable means routing them through a settings object instead of module consts. The tab is the UI surface for everything in this section.
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
- [ ] Auto-focus on/off (default ON). Today switching/activating a session auto-focuses its terminal (grep `focusPanel` / `focusedTerminal`; e.g. the `setTimeout(() => focusPanel(options.focusSession), ...)` on activation). Add a preference to turn auto-focus off so the cursor does NOT jump into a pane on switch/activation (the user clicks to focus instead). Gate the auto-focus calls on the setting; keep manual click-to-focus working regardless.
- [ ] Font size preference(s): let the user set the font size. At minimum the TERMINAL font size (xterm.js `fontSize` option, grep `new TerminalCtor` / `fontSize`), and ideally the UI font size too (tab labels / file tree / menus, driven by CSS vars like `--tab-label-size` / `--control-font` — grep them in `static/yolomux.css`). Wire the chosen size(s) to xterm + the CSS vars at runtime so they take effect live; clamp to a sane range; persist in `settings.yaml`.
- [ ] Terminal preferences: scrollback limit (xterm `scrollback`, currently 5000) — alongside the font-size pref above.
- [ ] File Explorer quick-access paths: a user-editable list of pinned directories (e.g. `~`, `/tmp`, custom dirs) the explorer can jump to. See the matching item under "File Explorer".
- [ ] Tab min width: let the user set the minimum tab width. Today `.pane-tab` uses a fixed `width/max-width: min(var(--pane-tab-width), 100%)` with `--pane-tab-width: 240px` (grep `--pane-tab-width` in `static/yolomux.css`). Add a preference that drives a `--pane-tab-min-width` (and/or overrides `--pane-tab-width`) so users can make tabs narrower/wider; clamp to a sane range. Wire the setting to the CSS var at runtime (settings.yaml / localStorage), so it takes effect live.
- [ ] Each numeric setting needs a sane min/max clamp and a Reset-to-default button so a bad value cannot freeze the refresh loop.

### Transcript (Tx) View

- [x] Show the session's transcript file path (the `.jsonl`) in the Transcript (Tx) view. Today the header (`transcript-head`, grep `>Transcript<`, ~6576) just says "Transcript", and the path appears only briefly in the loading placeholder (`path: ${agent.transcript}...`, ~7622) before `refreshTranscriptPreview` overwrites it. Show the path persistently — e.g. under the `transcript-head` — with a copy button (reuse the path-copy affordance). Source is `agent.transcript` (set in `sessions.py` for both claude `read_claude_agent` and codex `read_codex_agent`, ~195 / ~271). Handle the no-transcript case (currently "no agent transcript found", ~7627) with a clear "transcript not found" state rather than a blank path.

### File Explorer

- [x] Add a `Download` item to the file right-click context menu (grep `file-context-menu`, ~2542), alongside the existing Copy full/raw/relative path / Rename / Delete. Downloads the file to the browser. Backend `filesystem.read_raw` already streams bytes; add (or reuse) a raw-file endpoint that sets `Content-Disposition: attachment; filename=...` so the browser saves instead of previewing. For a directory, either disable Download or offer a zip. Support multi-select (download each, or a single zip). Respect read-only mode rules and the `MAX_RAW_BYTES` cap.
- [x] When opening a text file, group it into the EXISTING editor window instead of spawning a new editor panel. If an editor window is already open, add the file as a new tab in that window and focus it (and just focus the tab if the file is already open there). Only create a new editor window when none exists yet. Avoids one-editor-panel-per-file sprawl in the layout.
- [ ] Configurable File Explorer quick-access paths (e.g. `/tmp` + other custom dirs), set in Settings. NOTE: this is shortcuts, not new access — the explorer FS scope is already `/` (`filesystem.py` header: "No sandbox root"), so any path is reachable by navigating; today it just defaults to `homePath` and you have to walk there. Add a list of pinned quick-access roots shown in the explorer (a favorites/shortcuts row, or selectable roots), each opening via the existing `openFileExplorerAt(path)` / `fileExplorerRoot` machinery (grep `openFileExplorerAt`). Store the list as a user setting (`settings.yaml` once the Settings Panel lands; until then, localStorage). Default could include `~` and `/tmp`. Validate paths exist + are readable before showing; gracefully skip missing ones. Ties to the Settings Panel section. CONCRETE: ship built-in jump buttons for `/`, `~`, and `/tmp` as the default quick-access row (a small row of buttons in the explorer header), each calling `openFileExplorerAt(...)`.
- [x] Make the Finder root path bar typable. The overlay and pane variants now use path inputs; Enter jumps via `openFileExplorerAt(value)`, Escape reverts to the current root, bad paths show an inline error, `~` expands against the server home path, and the existing copy button still copies the current root.
- [ ] File Explorer root MODE toggle (a setting + a control in the explorer header): choose how the root is chosen.
  - "Always root" (current behavior): the explorer stays at a fixed root — whatever you typed / navigated to / picked from quick-access — and does NOT move when you switch tmux sessions.
  - "Sync to tmux session": the explorer re-roots to the ACTIVE tmux session's working directory whenever the active session changes, so selecting a session jumps the Finder to that session's cwd.
  Where: re-root via the existing `openFileExplorerAt(path)` / `fileExplorerRoot`; the active session's cwd is already resolved in JS (grep `selected_pane?.current_path` and the session working-dir helper near the `nonHomePane.current_path` logic). Hook the sync onto the active-session-change / focus path. Store the mode as a user setting (`settings.yaml` / localStorage). Edge cases: if the active session has no resolvable cwd, keep the current root; do not yank an open file-editor tab; decide whether a manual navigate while in "sync" mode pins until the next session switch or stays put. Ties to the typable-root + quick-access bullets above and the Settings Panel.
- [x] Image preview on hovering an image file icon. Hovering the `.file-tree-icon` of an image-extension file row pops a capped thumbnail from `/api/fs/raw?path=<enc>` using the shared popover show/hide delays; non-image rows and files over the raw-read cap do not open a preview.
- [ ] Image preview polish: hovering anywhere on an image file's row — the WHOLE NAME, not just the icon (`.file-tree-row` / `.file-tree-name` on an image-extension row) should pop up the thumbnail. Position the thumbnail BELOW and to the RIGHT of the cursor (follow the pointer, like a tooltip), clamped to stay on-screen.
- [ ] File-tree disclosure triangle sizing + color. The dir triangle (`▸` collapsed / `▾` expanded, the `.file-tree-icon` on dir rows — grep `file-tree-icon`) should match the cap height of the row's name text (size it to the capital-letter size, not larger/smaller). Color it by state: EXPANDED -> a distinct non-white color; COLLAPSED -> gray. Key the color on the row's expanded/collapsed state (the open class / `aria-expanded` on the dir row).
- [x] Type-specific file-tree icons. Today every file uses the same `📄` and every dir uses `▸` (grep the `const icon = entry.kind === 'dir' ? '▸' : ...` line in `static/yolomux.js`, ~2965). Add a `fileIconFor(name)` helper that picks an icon by extension / basename: images (`IMAGE_EXTENSIONS`) -> graphics icon, `.log` -> log icon, `.md` -> doc, `.json`/`.yaml`/`.toml`/`.ini`/`.cfg` -> config/gear, `.sh`/`.bash` -> shell, `.py`/`.js`/`.ts`/`.rs`/`.go`/`.c`/`.cpp` -> code, archives (`.zip`/`.tar`/`.gz`) -> archive, plus a sensible default. Reuse `IMAGE_EXTENSIONS` / `TEXT_EXTENSIONS` for the buckets. Keep it CSS-class- or emoji-based (no new asset dependency unless we add an icon font deliberately).
- [x] Special color for repo directories. A directory that is a repo root (has `.git`, optionally `.hg`/`.svn`/`.jj`) should render in a distinct color (and maybe a repo glyph), not plain white. Detection: cheapest is backend — in `_entry_info` (`yolomux_lib/filesystem.py`), for a dir set `is_repo = (path / '.git').exists()` (one extra stat per dir entry) and include it in the listing payload; the client then adds a `.file-tree-row.is-repo` class for the color. (The app already knows git roots via `git_root_for_path` / `path_info`, but the tree listing needs the per-entry flag.) Keep it cheap — only stat for `.git` on dir entries, not files.

### Bug Fixes And Tech Debt

- [x] Detaching from inside a pane (tmux `Ctrl-b d`) leaves it showing `[detached (from session N)]` and feels hung. Fixed server-side: when `bridge_tmux` sees `tmux attach-session` exit cleanly with returncode 0 and the session still exists, it immediately starts a new `tmux attach-session` on the same PTY/WebSocket bridge instead of tearing down the client connection. Non-zero exits still follow the normal disconnect/reconnect path.
- [ ] On disconnect, KEEP the pane's existing content and FLOAT a "Disconnected" overlay instead of mutating the content. Today `socket.onclose` writes `term.writeln('disconnected from N')` directly INTO the terminal buffer (grep `disconnected from`, ~8832), polluting the scrollback. Instead: leave the terminal / file / editor / branch-info content untouched and show a floating "Disconnected — reconnecting…" overlay centered over the pane, auto-removed when the connection is restored. Reuse the per-pane overlay layer that already exists on every pane (`.panel-overlay-root` + `.panel-toast-stack` — terminal, File Explorer, editor, Branch Info; grep them). Applies to all pane types. Clear the overlay on reconnect (socket reopen / data resumes). Pairs with the detach -> seamless re-attach fix above.
- [x] `#` (tab-metadata) toggle hides the wrong things. Today `body.tab-meta-hidden .pane-tab .session-button-text { display: none }` (`yolomux.css`, grep `tab-meta-hidden`) hides the ENTIRE text block — state badge + branch + PR badges + description — while the YO marker badge and the status dot (rendered OUTSIDE `.session-button-text`, via `yoloMarkerHtml` + `.session-button-prefix`) stay visible. Desired: when `#` is unclicked, ONLY the symbol badges should go away (YO marker, state badge `sessionStateHtml`, PR compact badges, the status dot), and the readable text should stay (session number/name in `.session-button-prefix`, branch name, description). This needs restructuring which elements `tab-meta-hidden` targets, because `.session-button-text` currently bundles both symbols and text — split the symbol badges out (or target them individually) so the toggle hides symbols only. CONFIRM the exact "symbols" set with the user before building.
- [x] Panel detail/status row polish (`.panel-detail-row` — the `branch · path · dirty N` row under the tab strip). (1) Push the close `×` (`.panel-detail-close`, grep in `static/yolomux.css` ~1468) further to the right so it sits flush at the far edge. (2) Give the row a more gray background — it is currently near-black `#121823` (grep `background: #121823` / `.panel-detail-row {`, ~1464); use a lighter gray so the status row reads as distinct from the terminal below it.
- [x] Pane toolbar buttons need tooltips with their full names. `Tx` / `AI` / `Log` have NO `title` today — `tabAttrs` (grep in `static/yolomux.js`) only sets `data-tab` / `data-tab-name`. Add `title` + `aria-label`: `Tx` -> "Transcript", `AI` -> "AI summary", `Log` -> "Event log", `Info` -> "Branch Info" (it currently says "hide details"). The `<` / `>` step buttons and the active-window terminal button already have titles (keep them). Hover should reveal the full name.
- [ ] The Enable/Disable YOLO dropdown item should show the YO icon, exactly as it looks on the tab. The menu item is built from `yoloLabel` (grep `Disable' : 'Enable'} YOLO`, ~2232) via `menuCommand(...)`; `menuCommand`/`createAppMenuCommand` already accept an `iconHtml` option (used elsewhere for `agentIcon` / `appMenuUiIcon`). Pass `iconHtml: yoloMarkerHtml(session, auto, {...})` (grep `function yoloMarkerHtml`, ~4507 — the same badge the tab renders) so the menu item shows the identical YO marker. Match the tab's appearance (same `session-yolo-marker` styling / enabled state).
- [ ] Trim the tmux-tab RIGHT-CLICK menu to exactly three items: Enable/Disable YOLO, Rename session, Kill session. Today `showSessionContextMenu` (grep, ~758) appends `tmuxSessionViewCommands` (Transcript, AI summary, Event log) + a separator + `tmuxSessionActionCommands` (Rename session, Kill session, Enable/Disable YOLO, …). Drop the `tmuxSessionViewCommands` group and the separator from the right-click menu, and show only those three actions, in this order: Enable/Disable YOLO (the `yoloLabel` command), Rename session, Kill session. Leave the per-pane kebab (`…`) menu unchanged — it can keep the fuller set. Validate: right-click a tmux tab shows exactly those three items and nothing else.
- [ ] Menu label/text consistency: (1) the dropdown "Rename session" item should read `Rename tmux session '<name>'` — include the session name (grep `'Rename session'`, ~2235). (2) Rename the top-level `Tmux` menu label to lowercase `tmux` to match actual tmux naming (grep `label: 'Tmux'`, ~2390); update the design-tree / README mentions to match.
- [ ] In the `Tab` dropdown rows, make the YO marker clickable to toggle YOLO WITHOUT closing the menu. Rows are built by `menuTabCommand` (grep, ~2208) and render `yoloMarkerHtml` (which has a `toggle` option). Wire the YO marker as a clickable sub-control: on click, toggle YOLO for that session, `preventDefault` + `stopPropagation` so it does NOT fire the row's activate command and does NOT close the dropdown, then re-render the marker's enabled state in place. The rest of the row keeps activating/focusing the tab as today.
- [x] Pane toolbar buttons hug the TOP of the panel-head with empty space below (not vertically centered). Cause: the toolbar `.tabs` is `float: right` (grep `.tabs {`) and a float top-aligns in its container; its buttons (`.tab`, `height: 22px`, grep `.tab {`) are shorter than the session tabs (`.pane-tab`, `height: 28px`) that drive the row height, and `.panel-head` padding is top-heavy (`3px ... 0`). So the 22px toolbar sticks to the top (3px) and the ~6px+ difference shows as bottom space. Fix WITHOUT dropping the float (the float is what makes wrapped tab rows flow under the toolbar): vertically center the floated toolbar in the tab row — e.g. add a top margin to `.tabs` to center the 22px buttons in the ~28-30px row, or raise `.tab` height to match `.pane-tab`, or even out `.panel-head` top/bottom padding. Validate: toolbar buttons are vertically centered relative to the session tabs at both single-row and wrapped multi-row heights.

- [x] Touchpad scrolls the terminal far too fast ("zooms"), while a mouse wheel scrolls fine. Fixed by replacing the sign-only wheel handler with `terminalWheelSignedLines`: pixel-mode wheel deltas are scaled by `terminalWheelPixelLinePx` and accumulated as fractional lines, line-mode deltas use real line counts, page-mode/Shift use page-sized scrolls, and `ctrlKey` wheel events are swallowed so trackpad pinch gestures do not become terminal scroll. `queueTmuxScroll` and `queueLocalTerminalScroll` both batch fractional deltas before rounding, so tiny touchpad events no longer each become a fixed three-line tmux scroll.
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
  - [x] Option C (future UI Kill action, per the menu plan): after killing, trigger the roster reconcile immediately (like `createSession` does) so UI-kills are instant.

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

### Completed Easy Fixes

The earlier EF1-EF6 researched worklist is complete as of 2026-05-29. Keep this section short so it does not look like the active queue.

- [x] EF1: `is_text_path` handles dotfiles, extensionless text names, and uppercase extensions. Covered by `tests/test_filesystem.py`.
- [x] EF2: window-step arrows hide when there is no previous/next tmux window. Covered by `windowStepVisibility` tests in `tests/layout_url.test.js`.
- [x] EF3: file popovers show the full path once and avoid duplicate type/status rows. Covered by `filePopoverRows` tests in `tests/layout_url.test.js`.
- [x] EF4: editor word-wrap toggle applies to overlay and panel editors and persists in localStorage. Covered by `editorWrapValue` tests in `tests/layout_url.test.js`.
- [x] EF5: file context menu has single-file Download backed by `/api/fs/raw?download=1`. Covered by `tests/test_login_auth.py` and `rawFileDownloadUrl` tests.
- [x] EF6: Codex transcript lookup reads the rollout `session_meta` header and missing transcript paths no longer force `BLK`. Covered by `tests/test_sessions.py` and `agentErrorIsBlocking` tests.

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
