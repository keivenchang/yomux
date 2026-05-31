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
- START HERE: the old EF1-EF6 easy-fix list and the follow-up easy-fix batches through 7 are complete. `DOIT.7.md` was removed after completion; `DOIT.8.md` tracks larger forward product features and deferred build-system refactors.

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
  - Open/close, hover, keyboard nav, ARIA roles, and click-outside already exist: helpers `menuCommand` / `menuSubmenu` / `menuSeparator` / `menuGroups`, plus `appMenuIsOpen` / `appMenuCommands` / `createAppMenu` / `createAppSubmenu` / `createAppMenuCommand`.
- Current LIVE top-level menus: `File`, `View`, `tmux`, `Tabs`, `Help`. Gaps vs the design tree below:
  - NAMING DECISION (2026-05-30): the menu that lists the visible/minimized/inactive app tabs is named `Tabs`, while `tmux` is only for tmux-specific controls. The dropdown rows are separated by bars only; no `Active` / `Minimized` / `Inactive` section text.
  - File today: File Explorer, Open file, Preferences, Log out.
  - tmux today: current session YO toggle, direct `+ Claude` / `+ Codex` / `+ Term`, Transcript / AI summary / Event log / Branch Info, Rename / Kill tmux session, Resume session (disabled), YOLO rule-file actions at the bottom.
  - View today: tab-metadata toggle, Alert, Refresh, Branch Info, Layout submenu (Single pane / Grid / Wall — ALL disabled). MISSING: Filter/Sort, panel-tabs visibility.
  - Help today: Command palette, version (disabled), Keyboard shortcuts submenu, Open README.
  - NOT BUILT yet: per-pane left dropdown.

DESIGN GATE (mostly satisfied — the skeleton is built; remaining gate work is reconciling the items below against the live `appMenuTree()`):

- [x] Decision confirmed: the standalone top session-tab strip is gone; `#sessionButtons` now hosts the menu bar. New session for Claude AND Codex lives as direct `tmux` menu items, not a nested submenu.
- [x] Reconcile the live menu (`File/View/tmux/Tabs/Help`) with the design tree below — `Log out` stays in File and the top-right controls.
- [x] Rename the app-tab list menu to `Tabs`. `tmux` is now tmux-scoped actions only; `Tabs` lists the app's open panes/tabs (tmux sessions, Finder, editors, viewers, Preferences, Branch Info) with rich rows.

Current controls to be reorganized (inventory):

- Top bar today: brand + version, session-tab strip (`#sessionButtons`), latency meter, `Notify`, `Refresh`, `Log out`, `status`, tab-metadata toggle (`#`), HTTPS warning.
- Per-pane tab strip today: `<` / `>` window step, `Terminal`, `Tx` (transcript), `AI` (summary), `Log` (events), `Info` (detail toggle), window-close.
- Other surfaces: File Explorer (tree + editor), Files panel, layout (single / grid / wall), inactive-tabs tray menu, per-session state badges (`RUN`/`EXEC?`/`YOLO?`/`QUES?`/`BLK`/`OFF`/`TEST`/`PR`/`IDLE`/`DONE`).

Menu naming follows the latest YOLOmux terminology: `File` (files/app actions), `View` (display options), `tmux` (tmux session create/manage plus tmux-scoped YOLO controls), `Tabs` (lists and navigates app tabs), and `Help`.

MENU INSPIRATIONS (Chrome / Safari / iTerm — what to borrow):

- Chrome `Tab` menu (the direct model for our `Tabs` menu): `Search Tabs`, `Next Tab` / `Previous Tab`, `Duplicate Tab`, `Pin Tab`, `Mute Site`, `Move Tab to New Window`, then the LIST of open tabs with a checkmark on the current one. Adopt: type-to-filter (= Search Tabs), Next/Previous, and the rich-row tab list with active-row highlight. Map "Move Tab to New Window" -> move a tab into another YOLOmux pane / split.
- Chrome right-click-tab menu: `New tab`, `Duplicate`, `Pin`, `Mute`, `Close`, `Move to new window`. Map to our right-click-tab context menu (`Rename`, `Kill`, `YOLO policy`, move-to-pane).
- Chrome `File`: `New Tab`, `New Window`, `Reopen Closed Tab`, `Open File`, `Close Tab`. "Reopen Closed Tab" maps to our File -> `Resume` (recent Claude/Codex conversations).
- Safari: a `Show All Tabs` overview (grid of all tabs) — a richer alternative/companion to the dropdown tab list; and it splits tab actions between `File` (New Tab) and `Window` (Next/Previous, Move Tab). We consolidate navigation into the `Tabs` menu instead.
- iTerm separation, which our top-level split mirrors: `Shell` = create (New Window/Tab, Split, Close) -> our `File`; `Session` = per-session actions (Edit/Rename, Restart, Logging) -> our per-pane kebab + right-click-tab menu; `Window` = navigate (Select Next/Previous Tab, Select Next/Previous Pane, Move Tab, list) -> our `Tabs` menu + keyboard nav. Also borrow iTerm `Edit Tab Title` (= our rename) and `View -> Toggle Tab Bar` (= our tab-metadata / panel-tabs toggles).
- Net structure to converge on: `File` = create/open (Chrome File + iTerm Shell), `Tabs` = navigate/act-on-tabs (Chrome Tab + iTerm Window's tab/pane selection), `View` = display toggles (all three), per-pane kebab + right-click = per-session actions (Chrome right-click-tab + iTerm Session).

Proposed top-bar layout (left to right): `YOLOmux ver` · `[File ▾] [View ▾] [tmux ▾] [Tabs ▾] [Help ▾]` · active-session chip (label + state badge) · latency · `Notify` · `Refresh` · status · `Log out`.

KEEP AS DEDICATED TOP-RIGHT BUTTONS: `Notify`, `Refresh`, `Log out`. These stay as one-click buttons on the right of the top bar exactly as today (same icons, same behavior). The menus absorb the session tabs and the lower-frequency controls; these three high-use buttons remain visible.

ALSO MIRROR KEY DISPLAY CONTROLS IN THE SETTINGS MENU: `#` (tab metadata), `Notify`, and `Refresh` appear as items inside the `Settings ▾` menu, in addition to the top-right buttons. They use the same icon masks and show the same on/off state where applicable. `Log out` remains in File and the top-right cluster, not Settings.

Proposed menu tree (PROPOSAL — review before building):

- [ ] [S] File ▾  (files / app actions)
  - File Explorer (open)
  - Open file…
  - --- (separator)
  - Log out
- [ ] [S] Tmux ▾  (create / manage tmux sessions)
  - `+ Claude`, `+ Codex`, `+ Term` (each opens the P4 launch dialog: cwd, model/profile, permission mode, initial prompt, optional name)
  - Rename tmux session
  - Kill tmux session
  - Enable/Disable YOLO for Tmux Session `<name>`
  - Open event log
  - Resume ▸ : recent Claude/Codex conversations scoped to cwd (P4)
  - NOTE: no Attach / Detach item. YOLOmux streams every pane over WebSocket and is always "attached"; switching sessions only changes which stream is shown. tmux attach/detach is a terminal-client concept that does not map to this UI, so it is intentionally omitted.
- [ ] [S] View ▾  (display options)
  - Layout ▸ : Single / Grid / Wall
  - Filter / Sort ▸ : Needs me, by state, by repo, by PR status
  - Tab metadata: show / hide (the current `#` toggle)
  - Inactive tabs: show all / tray
  - Panel tabs ▸ : toggle Terminal / Tx / AI / Log / Info visibility
  - Branch Info: show — opens the Branch Info viewer for the active session (also still available as the per-pane `Info` tab; once open it appears in the Tabs menu)
- [ ] [M] Tabs ▾  (navigate — a flat rich-row list of active, minimized, and inactive tabs, with checkmark/highlight on the active one, click/Enter to focus, type-to-filter)
  - Just lists tabs and panes, nothing to "launch" from here — opening items happens in File / View. Ordering, top to bottom:
  - tmux sessions first : each open terminal tab
  - then editors : each open File Editor tab
  - then other open viewers : File Explorer, Branch Info, Transcript, AI Summary, Event Log (only the ones currently open)
  - Each row carries the SAME rich info already shown in the existing tab-metadata view, but packed more compactly so it reads like a real dropdown menu (one tight row per tab): agent badge (`YO`/`BL`/… + session number), state badge (`RUN`/`BLK`/…), PR number, commit-style title, branch, repo path (e.g. `dynamo2 -> dynamo3`), and dirty count. The active tab's row is highlighted (green) like the current selection.
  - This is a denser re-layout of the existing rich rows, not new data — reuse the metadata that already feeds the tab strip.
- [ ] [M] Per-pane left dropdown (the caret on the LEFT of each panel's tab strip): looks IDENTICAL to the Tabs ▾ menu above (same compact rich-row format), but scoped to just THAT pane's tabs — i.e. only the tabs/views belonging to that one panel (its tmux sessions + Terminal / Tx / AI / Log / Info), not every pane in the app. Same row styling, same active-row highlight.
- [ ] [M] Add the remaining YOLO controls under Tmux instead of restoring a top-level YOLO menu: policy modes, Open rule file, Approval queue, Audit log, and Risk labels legend.
- [ ] [S] Settings ▾
  - `#` / Tab metadata (same icon + on/off state as the top-right button)
  - `Notify` (same icon + on/off state as the top-right button)
  - `Refresh` (same icon + behavior as the top-right button)
- [ ] [S] Help ▾
  - Keyboard shortcuts
  - About / version
  - Open README
- [x] Per-pane kebab (`…`) on each panel head (keep the existing Term/Tx/AI/Log/Info strip): YOLO policy for this session, run summary, rename, kill, open event log. (No "attach" item — see the Tmux Session note above.)
- [ ] [M] Add the remaining per-pane kebab actions: peek / reply.
- [x] Right-click context menu on a session tab (the pane tab / panel header): `Rename session` (inline edit, same affordance as the file-tree rename — grep `function beginFileTreeRename`, ~2620), plus `Kill session` and `YOLO policy`. This is the expected shortcut so users do not have to open the Tmux menu just to rename.
- [x] Multi-line tab wrapping: when a pane's tabs wrap to 2+ rows (the `fix: wrap crowded tabs` work, commit `e5df095`), the second row and beyond should use the full width INCLUDING the area beneath the pane toolbar (`<` / terminal / `Tx` / `AI` / `Log` / `Info` / `×`), instead of stopping short and leaving that space blank. Only the FIRST row needs to reserve room for the toolbar. Implemented by floating the pane toolbar and rendering pane tabs as inline-flex items so wrapped rows flow under the toolbar.
- [x] Fix float-based wrapped tab alignment/overflow. The bad path was `.pane-tab { transform: translateY(2px) }`: it made a single-row tab look flush but did not affect layout, so wrapped rows could paint past `.panel-head` and into the detail row. Fixed by removing the transform, making `.pane-tab`'s real height match `.pane-tabs` (`28px`), and removing the header bottom border so the tab bottom and detail row share the same edge. Verified in headless Chrome with one-row and two-row tab strips: active-tab gap is `0`, `.panel-head` grows to contain both rows, and no tab bottom exceeds `.panel-detail-row` top.
- [x] Keep an EMPTY placeholder pane only for Finder's direct left/right sizing. Concrete case: left = Finder (File Explorer), right = a tab. When the right side's last tab is closed/killed, keep an empty placeholder pane on the right so Finder stays right-sized and the user can drop a new tab into the empty slot. Symmetric case: right = Finder, left closes, keep the left placeholder. But when Finder is split top/bottom with another pane, closing/killing the other pane collapses the vertical split so Finder expands up/down. Also collapse a stale empty pane when Finder is nested inside another real pane group; otherwise a removed tmux/editor slot can leave a giant blank area next to Finder+editor. The codebase already has the placeholder concept: `emptyPlaceholderPaneState()` -> `{tabs: [], placeholder: true}`; `compactLayoutNodeInfo` keeps placeholder leaves only in row splits with a direct Finder leaf, plus the global all-empty fallback.

Cross-cutting requirements for all menus:

- [x] Menu keyboard accessibility, app-level shortcuts, and `View ▸ Layout` command implementation completed in the DOIT.7 batch.
- [x] Menu widths are content-measured and viewport-clamped through shared CSS variables. Do not add fixed pixel min/max buckets for dropdown capacity; browser size, OS font metrics, zoom, and scrollbar behavior vary too much. Use intrinsic DOM measurement, percentages/viewport units, and container-relative constraints instead.
- [x] Fix awkward hover timing (classic menubar behavior). FIRST open (no menu currently open) waits 300 ms before popping on hover. Once a menu is ALREADY open, moving the pointer to another top-level menu switches INSTANTLY with no delay.
- [ ] [M] Mobile: menus collapse into a single hamburger (ties to P7).
- [x] Read-only mode disables mutating items (New, Kill, Settings writes, YOLO toggles) the same way the current `Notify`/buttons do.

### P1: YOLO Event Log, Audit, And Queue

- [x] Add a persistent YOLO event log under `~/.local/state/yolomux/events.jsonl`, while keeping compact app state in `~/.config/yolomux/state.json`.
- [x] Record approval decisions, blocked commands, worker errors, session start/stop, terminal disconnects, uploads, pasted images, summary runs, state changes, and user-visible notifications.
- [x] Add a per-session YOLO audit panel showing recent approved/blocked decisions with timestamp, command text, matched rule, and session.
- [ ] [L] Add an approval queue view for pending high-risk actions. Start read-only first if live interception is hard.
- [ ] [M] Add per-session YOLO policy. Initial modes: `off`, `prompt-only`, `safe`, `edit`, `full`. Make policy visible on the tmux-session YOLO control.
- [ ] [S] Risk labels should be boring and concrete: `read`, `edit`, `network`, `process`, `delete`, `credential`, `unknown`.
- [x] Replace the unhelpful top-right `YOLO on: 6` status string. YOLO state now lives on the per-session `YO` markers and the enabled-session count badge on the `Tabs` menu. The top-right status now reports the action as `enabled YOLO for <session>` / `disabled YOLO for <session>` instead of the old red `YOLO on: N` string.

### P1: YOLO Rule Engine (user-configurable matching via YAML)

YOLO matching now runs through `yolomux_lib/yolo_rules.py`. When `~/.config/yolomux/yolo-rules.yaml` exists, YOLOmux hot-reloads that ordered first-match-wins ruleset; when it does not, YOLOmux uses a built-in fallback equivalent to the previous dangerous-command denylist and keeps approving non-dangerous bash prompts. The remaining work here is policy/profile layering, not the base rule engine.

- [x] BUG (single source of truth): the default-when-no-rule-matches is defined in TWO places — `settings.yaml` `yolo.default_policy` AND `yolo-rules.yaml` `default:`. FIXED 2026-05-30: the rule file's `default:` is canonical, `yolo.default_policy` is removed from settings/preferences, missing `default:` falls back to `ask`, and README documents the rule-file source of truth.

- [x] BUG (false-positive blocks on DATA): the built-in dangerous-pattern rule matched raw text and could block quoted data such as `echo "rm -rf /"` or `grep "dd if=/dev/sda" notes.txt`. FIXED 2026-05-30: standalone `auto_approve_tmux.py` now uses the shared YOLO rule engine, dangerous commands are argv-aware, and raw regex matching is retained only for command-position/redirect style block-device hazards.

Schema A is implemented:

- [x] Decide file location and precedence. Implemented shared file: `~/.config/yolomux/yolo-rules.yaml`; per-repo/session overlays remain future work.
- [x] Decide the action verbs. Implemented: `approve` (press Enter / select Yes), `decline` (select No / option2), `block` (leave for manual, current behavior), `ask` (notify + wait), `notify` (log only, take no action), and `off`.
- [x] Decide match types. Implemented: `contains` (substring), `regex`, `glob`, and `command` (argv-aware parse so `echo "rm"` is data, not a delete).
- [ ] [L] Decide scoping dimensions: global vs per-repo vs per-session, per-agent (`claude` / `codex`), and per prompt-type (`bash` / `file` / `tool`).
- [ ] [S] (minor) Non-bash prompts auto-`approve` unconditionally. `evaluate()` returns `approve` for `prompt_type != "bash"` (`yolo_rules.py` ~532), so file-edit / tool approvals bypass the ruleset AND the hard floor. Matches prior behavior; decide whether file/tool prompts should be rule-governed and floor-checked too. (Carried over from the removed DOIT.5; the other minor, `notify_transitions` validation, already landed.)
- [ ] [M] How does the user EDIT the rules? Today the only path is raw-YAML hand-editing: `openYoloRuleFile` (JS ~2420) POSTs `/api/yolo-rules/open` → `ensure_yolo_rules_file()` creates the file from a commented template if missing, then opens `~/.config/yolomux/yolo-rules.yaml` in the built-in text editor. There's NO structured editor. Decide: keep raw-YAML-only (cheapest; rely on the commented template + validation below), OR add a structured rule modifier (list rules with add / remove / reorder, dropdowns for `type`/`action`/`risk`, a `match` list editor, and the top-level `default:` policy) that round-trips to the YAML. A structured editor also fixes discoverability — most users won't know the file exists.
- [x] VALIDATE the rule YAML in the editor / modifier. FIXED 2026-05-30 for raw-file editing: `/api/fs/write` validates the active YOLO rules file with `validate_rules` before persisting, rejects invalid YAML/schema with HTTP 400, and the editor status shows the inline save error. A structured rule modifier remains a separate future UI task.

Schema options to choose between (PROPOSAL — pick one or blend):

- [x] Option A — ordered rule list, first match wins:

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

Safety and operational requirements:

- [x] Deny always beats allow. Keep a hard floor (`rm -rf /`, `dd` to a block device, fork bomb, `mkfs`, redirect to `/dev/sd*`) that the YAML cannot relax unless YOLOmux was started with `--dangerously-yolo`.
- [x] Dry-run / shadow mode: evaluate rules and log what WOULD happen (matched rule + action) without acting, so a new ruleset can be validated against real prompts first.
- [x] Hot-reload on file change; validate the schema on load and surface errors in the UI instead of silently falling back.
- [x] Ship today's hardcoded denylist as the built-in fallback when no file exists. The `Open rule file` action creates an editable starter `yolo-rules.yaml` from the same rules.
- [x] Record the matched rule name in every audit event.
- [x] Add an "Open rule file" + "Reload rules" action in the Tmux menu, and show the active ruleset path/source in Settings.

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

- [ ] [M] Add a per-session info drawer with full path, branch, dirty/ahead/behind counts, PR, CI, Linear/issue metadata, latest summary, and recent events.
- [x] **Changed Files panel, phase 1 — list AI-attributed changed files and open plain files.** Landed 2026-05-30: backend `/api/session-files?session=N&hours=24` scans Claude Edit/Write/MultiEdit/NotebookEdit calls and Codex `apply_patch` Add/Update/Delete paths, resolves touched paths to git roots, merges repo state from `git diff --name-status HEAD` plus `git ls-files --others --exclude-standard`, falls back to tool-call paths outside repos, and returns `{repo, path, abs_path, status A/M/D, mtime, agent, session}`. The `Changes` virtual tab renders grouped repo sections, a selected-session control, recent/name sort, colored A/M/D badges, and row-click opening for changed/new files.
- [ ] [L] **Changed Files panel, phase 2 — diff mode and mirrors.** Add the Cursor-style diff/plain toggle: tracked files -> `@codemirror/merge` editable diff (`git show HEAD:<relpath>` vs working file), new untracked files -> plain, deleted files -> removed content read-only. Also add the compact mirror in the per-session info drawer and an auto-detected count badge on the pane/menu when a session has uncommitted AI changes. DETAILED DESIGN -> **DOIT.9.md** (in-editor Diff button gated on repo+changes; changed-file click opens directly in diff mode; SIDE-BY-SIDE when the pane is wide / INLINE when narrow via `@codemirror/merge` `MergeView`/`unifiedMergeView`; HEAD base via `git show HEAD:<relpath>` added to `/api/fs/diff`; auto-update without flash). PREREQ: `@codemirror/merge` is not bundled yet — needs the CM bundle rebuild.
- [ ] [S] Make PR/CI/issue links clickable, but keep local branch names as text unless a real remote branch/PR exists.
- [ ] [S] Add an explicit refresh button for repo metadata plus background polling with sane intervals.
- [x] Remove redundant info in the file-viewer detail/info panel. Today it repeats itself: the filename shows up as both the tab label AND the bold heading, and the full path shows up twice — once as the subtitle line under the heading and again in the `path` row (with the copy button). Show the filename once and the full path once. Keep the path row (it has the copy affordance) and drop the duplicate subtitle, or vice-versa. Also collapse `type: loading` / `status: loading` so a viewer that has no meaningful type/status does not show two placeholder "loading" rows.

### P3: Editor — Wrap, Preview, Split-Preview

The file editor today has a single Preview toggle (`#fileEditorPreview`, `web.py`) and a working word-wrap toggle. Make the preview path first-class view modes next.

- [x] Word-wrap toggle: switch the CodeMirror editor between horizontal scrolling and soft word-wrap, so long lines wrap to the pane width. Persist the preference (Settings → Terminal/Editor).
- [x] Three explicit view modes for the editor: `Edit`, `Preview`, `Split-Preview` (a small segmented control in the editor head). Edit = CodeMirror only; Preview = rendered view only.
- [x] Split-Preview: split the current editor window in half — left = CodeMirror editor, right = rendered preview — side by side in the same panel.
- [x] Split-Preview synced scroll: the two halves scroll together (scrolling the editor scrolls the preview to the matching position, and vice-versa). Map by source line / proportional offset so headings and code blocks stay roughly aligned.
- [x] Preview content by type: Preview/Split is only shown for renderable files. Markdown renders to formatted HTML; HTML/HTM renders in a sandboxed iframe with JavaScript disabled; code/source files stay edit-only with syntax coloring in CodeMirror.
- [x] CROSS-PANE split + CM-exclusive. Shipped 2026-05-31: Split-Preview can split ACROSS two panes (left CodeMirror editor / right rendered preview, SAME file) with generalized scroll-sync, and the editor is now CodeMirror-only.
- [x] Toolbar icons: make the Wrap button just a wrap glyph (↪) instead of the word "Wrap", and the Save button a disk glyph instead of "Save". Buttons are `#fileEditorWrap` / `#fileEditorSave` (`web.py`, grep the ids). The buttons keep accessible `title` / `aria-label` text.
- [x] Soft-wrap continuation marker: when word-wrap is on, show a wrap glyph (↪) at the START of each wrapped continuation line, so a soft-wrapped line is visually distinct from a real new line. Implemented with the dependency-light custom overlay path instead of pulling in CodeMirror/Monaco.
- [x] Line-number gutter option: add line numbers as an editor toggle. CodeMirror owns the gutter; numbers count SOURCE lines, and wrapped continuation rows are not numbered.
- [x] Preview background tint: in Preview and Split-Preview, give the rendered side a gray background so the two halves are visually distinct at a glance (style `#fileEditorPreviewPane` / the preview half). Note: Split-Preview itself is the existing "Split-Preview" bullet above — this just adds the visual distinction.
- [x] In-document Find. Shipped 2026-05-30 on the CodeMirror path: platform app modifier+F and the editor Find button open CodeMirror's in-file search panel with match count, previous/next, close, all-match highlighting, and replace controls on editable files.
- [ ] [M] More editor options to consider (decide which to build — Find above is the headline):
  - Replace / Replace all (platform app modifier+H) — pairs with Find; same bar with a replace row.
  - Go to line (platform app modifier+G) — jump to + reveal a line number.
  - Cursor position in the editor status bar: `line:col`, plus selection length / count.
  - Toggle line comment (platform app modifier+/) per language (reuse the syntax language detection).
  - Indent / outdent selection (`Tab` / `Shift+Tab`) + configurable tab width / spaces-for-tab.
  - Line ops: duplicate (Shift+platform app modifier+D), move up/down (`Alt+↑/↓`), delete (Shift+platform app modifier+K).
  - On-save hygiene (optional settings): trim trailing whitespace, ensure a final newline.
  - Reload-from-disk button when the file changed externally (the "changed on disk; unsaved edits kept" warn already exists — add a one-click reload).
  - Word / char / line count in the status bar.
  - NOTE: all-match highlighting, multiple cursors, bracket matching, auto-close brackets/quotes, code folding, and real auto-indent are CodeMirror-owned now; do not rebuild a second editor path.
- [x] DECISION/SPIKE: CodeMirror 6 vs extend the textarea — RESOLVED & shipped 2026-05-31: CodeMirror 6 won and is now the only editable engine (`static/codemirror.js` vendored). The old textarea editor path, `editor.engine` setting, and `?editor=` override were removed. Rationale/gate/`@codemirror/merge` diff notes are preserved in `EDITOR-CODEMIRROR.md` (now historical).

### P4: Launch And Resume

- [ ] [L] Add a launch dialog behind `+ Claude`, `+ Codex`, and `+ Term` with cwd, agent, model/profile, permission mode, initial prompt, and optional session name.
- [ ] [S] Keep the current quick `+` path for defaults; the dialog should not slow down simple launches.
- [ ] [M] Add a resume picker for recent Claude/Codex conversations scoped to the selected cwd.
- [ ] [M] Add a `peek/reply` action for a session when it only needs a short response and the user does not need to attach to the full terminal.

### P5: Worktrees

- [ ] [L] Add optional worktree-backed launch mode: create worktree, branch, tmux session, and initial agent prompt together.
- [ ] [S] Show worktree path and parent repo in the info drawer.
- [ ] [M] Add cleanup guardrails: never delete a worktree with uncommitted changes; show the path and stop.
- [ ] [M] Add a read-only file browser for a session worktree before adding edit controls.

### P6: Search, Cost, And History

- [ ] [L] Add full-text search across captured session events and summaries.
- [x] Find / command palette (universal). Shipped 2026-05-30 and expanded 2026-05-31: platform app modifier+K, Shift+platform app modifier+P, and `Help` -> `Command palette` search tabs, menu actions from `appMenuTree()`, and settings. Platform app modifier+P opens file quick-open, and the Tabs menu has its own fuzzy search input. Results are grouped and Enter activates/invokes; settings results open Preferences and focus the control.
- [ ] [M] Add per-session token/cost/context metrics only if they can be read reliably from Claude/Codex metadata without scraping fragile UI text.
- [ ] [M] Add a compact run history: prompt, cwd, agent, started/ended time, final state, PR, and latest summary.

### P7: Mobile And Network Use

- [ ] [L] Add a single-pane mobile focus mode with larger controls for Esc/Tab/Ctrl, paste/upload, YOLO actions, and reply.
- [ ] [S] Add network-access setup guidance that is explicit about auth, host binding, and local-only defaults.
- [ ] [M] Consider installable PWA behavior only after mobile layout is usable.

### P8: Host And Process Vitals

- [ ] [M] Add lightweight CPU/memory/load probes and per-session process trees.
- [ ] [M] Add optional `nv-smi` GPU status when available, but do not make GPU support required.
- [ ] [S] Show vitals in an info drawer or compact topbar popover, not as a dominant dashboard.

### P9: Multi-Machine Connector

- [ ] [XL] Defer until the local product is stable. This changes auth, networking, logging, and failure modes.
- [ ] [XL] If built, use a small remote agent that reports tmux sessions, metadata, vitals, and WebSocket terminal streams back to one YOLOmux instance.
- [ ] [S] Keep local-only as the default.

### Preferences Tab (Settings)

DECISION (2026-05-29): implement Settings as a dedicated PREFERENCES TAB — a virtual layout item like the File Explorer / Branch Info, not a modal. It opens from a menu, appears in the `Tab` list, and can be dragged/split into a pane like any other tab. Model it on the existing virtual items (grep `fileExplorerItemId = '__files__'` / `infoItemId = '__info__'` in `static/yolomux.js`; add e.g. `prefsItemId = '__prefs__'`). Group ALL the preferences below into sections within this one tab (e.g. General · Appearance · Notifications · Performance · YOLO · Terminal/Editor · File Explorer · Advanced).

- [x] Build the Preferences tab. Today the `Settings ▾` menu only contains quick controls (`#`, Notify, Refresh), and tunables are hardcoded as `const`s near the top of `static/yolomux.js` (grep `const metadataRefreshMs`) and on the server, with no UI — making them runtime-configurable means routing them through a settings object instead of module consts. The tab is the UI surface for everything in this section.
- [x] Make the Preferences tab look SIMPLER: a search bar on top, everything else in collapsed disclosure groups. Shipped 2026-05-30: sections persist collapsed/expanded state, search auto-expands matches, and help text is inline instead of a flashing hover popup.
- [x] Add a Search/Find box at the top of the Preferences tab that filters the settings as you type. Shipped 2026-05-30: matches section titles, labels, paths, and help text; matching groups expand while search is active.
  - Environment facts (verified 2026-05-29): no file-watch library is installed (no `inotify`/`watchdog`) — use mtime polling for cross-server reload. Only `pyyaml 6.0.1` is available, NOT `ruamel.yaml` — so preserving inline comments on a UI-driven save needs a decision: add a `ruamel` dependency, OR re-emit `settings.yaml` from a fixed commented template (don't round-trip), OR accept that a UI save drops hand-added comments. Hand-editing the file keeps comments either way.
- [x] Persistent storage: write settings to a single human-readable file, `~/.config/yolomux/settings.yaml`. Use YAML specifically so it is easy to read and hand-edit, WITH inline comments documenting each key, its units, default, and allowed range. Keep machine state (badge pulses, auto-approve-enabled session list) in the existing `state.json`; `settings.yaml` is for user preferences only.
- [x] Live propagation to ALL running servers: a settings change made in one YOLOmux instance must take effect in every other launched server (e.g. the `:7777` and `:7778` instances) without a restart. Since `settings.yaml` is the shared source of truth, each server watches the file (mtime/inotify) and reloads on change, and each open browser is pushed the new values over the existing WebSocket/poll channel so live pages update too. Writes must be atomic (temp file + rename) and last-write-wins; preserve comments on rewrite (round-trip YAML, e.g. ruamel) so hand-added notes survive a UI save.
- [x] Notifications: Notify on/off (already a toggle); choose which state transitions notify (`needs input`, `needs approval`, `YOLO blocked`, `terminal disconnected`, `PR ready`); per-session mute; notify throttle interval.
- [x] Toast duration (`toastDurationMs`, default 10000 ms).
- [x] Refresh frequencies: metadata (`metadataRefreshMs`, 15001 ms), pane/agent state (`paneStateRefreshMs`, 1257 ms), latency meter (`latencyRefreshMs`, 3001 ms), event log (`eventLogRefreshMs`, 5003 ms).
- [x] Blinking RED attention-reminder cycle frequency (`redReminderMs`, 1550 ms), plus an off switch.
- [x] YO rotation cycle frequency (`yoloRotateMs`, 20000 ms), plus off.
- [x] Metadata-badge pulse duration (`METADATA_BADGE_PULSE_SECONDS`, 20 s, server-side).
- [x] Popover show/hide delay (`popoverShowDelayMs` / `popoverHideDelayMs`, 300 ms); remote resize debounce (`remoteResizeDelayMs`, 220 ms).
- [x] Auto-approve poll interval (auto_approve `--interval`, default 0.5 s) and default YOLO policy for new sessions (ties to P1 policy modes).
- [x] Tab metadata visibility (the existing show/hide tab-metadata toggle), default layout (single/grid/wall), and default sessions on load.
- [x] Auto-focus on/off (default ON). Switching/activating a pane can focus the active view: terminal, editor, Finder/File Explorer, Preferences, or other tab content. The preference turns that programmatic focus off so the cursor does NOT jump into a pane/view on switch/activation (the user clicks to focus instead). Gate the auto-focus calls on the setting; keep manual click-to-focus working regardless.
- [x] Font size preference(s): let the user set the font size. At minimum the TERMINAL font size (xterm.js `fontSize` option, grep `new TerminalCtor` / `fontSize`), and ideally the UI font size too (tab labels / file tree / menus, driven by CSS vars like `--tab-label-size` / `--control-font` — grep them in `static/yolomux.css`). Wire the chosen size(s) to xterm + the CSS vars at runtime so they take effect live; clamp to a sane range; persist in `settings.yaml`.
- [x] Terminal preferences: scrollback limit (xterm `scrollback`, currently 5000) — alongside the font-size pref above.
- [x] Finder (File Explorer) font size in Preferences. Shipped 2026-05-30 as `appearance.file_explorer_font_size`, clamped in settings and applied through `--file-explorer-font-size`.
- [x] Editor font size in Preferences — and it is NOT pegged to the terminal. Shipped 2026-05-30 as `appearance.editor_font_size`, clamped in settings and applied through `--editor-font-size` to CodeMirror, split editor, and preview surfaces.
- [x] File Explorer quick-access paths: a user-editable list of pinned directories (e.g. `~`, `/tmp`, custom dirs) the explorer can jump to. See the matching item under "File Explorer".
- [x] Tab min width: let the user set the minimum tab width. Today `.pane-tab` uses a fixed `width/max-width: min(var(--pane-tab-width), 100%)` with `--pane-tab-width: 240px` (grep `--pane-tab-width` in `static/yolomux.css`). Add a preference that drives a `--pane-tab-min-width` (and/or overrides `--pane-tab-width`) so users can make tabs narrower/wider; clamp to a sane range. Wire the setting to the CSS var at runtime (settings.yaml / localStorage), so it takes effect live.
- [ ] [M] Maximum tabs per pane + auto LRU eviction (Preference). Add `appearance.max_tabs_per_pane` (default ~8-10; clamp ~2-30) in `settings.py` DEFAULT_SETTINGS + SETTING_RANGES + SETTING_COMMENTS. When a pane exceeds the cap, AUTO-EVICT the LEAST-RECENTLY-USED tab — needs per-tab last-activated tracking (none today; record a timestamp in `activatePaneTab`) and enforcement on the tab-append paths (`paneStateWithTabs` `yolomux.js:1805`, `appendUniqueItems`). Keep the active + most-recent; NEVER silently evict a dirty/unsaved editor tab (skip it or prompt). Preferences row description should EXPLAIN it: "Caps open tabs per pane; the oldest unused tabs auto-close (LRU) when the limit is exceeded (dirty editors are kept)."
- [x] Each numeric setting needs a sane min/max clamp and a Reset-to-default button so a bad value cannot freeze the refresh loop.

### Transcript (Tx) View

- [x] Show the session's transcript file path (the `.jsonl`) in the Transcript (Tx) view. Today the header (`transcript-head`, grep `>Transcript<`, ~6576) just says "Transcript", and the path appears only briefly in the loading placeholder (`path: ${agent.transcript}...`, ~7622) before `refreshTranscriptPreview` overwrites it. Show the path persistently — e.g. under the `transcript-head` — with a copy button (reuse the path-copy affordance). Source is `agent.transcript` (set in `sessions.py` for both claude `read_claude_agent` and codex `read_codex_agent`, ~195 / ~271). Handle the no-transcript case (currently "no agent transcript found", ~7627) with a clear "transcript not found" state rather than a blank path.

### File Explorer

- [ ] [M] Git-aware Finder: hover popover + inline path annotation for repo dirs/paths. The Finder already knows repo dirs (`entry.is_repo`, `filesystem.py:109` via REPO_MARKERS; the row gets `.is-repo`, `yolomux.js:3830`) but only as a boolean — extend it with real git info.
  - (A) HOVER POPOVER on a repo directory / git path: show repo name, current branch (flag when it's NOT `main`/`master` — reuse `isDefaultBranch`), dirty / ahead / behind counts, and the remote / GitHub URL. Reuse the existing hover-popover infra (the `bindFileImagePreview` pattern + `popoverShowDelayMs`/`popoverHideDelayMs`, follow-cursor positioning). Needs a server side: extend the fs metadata that already resolves `git_root_for_path` / `repo_root` / `relative_path` (`filesystem.py:253`) to also return branch + dirty/ahead/behind + remote — reuse the per-session `project.git` shape from `metadata.py` so the format matches the tab rows. DEBOUNCE + cache (don't run `git` on every hover; key by path, refresh on mtime/expiry).
  - (B) INLINE PATH ANNOTATION: when the rooted directory is inside a repo, the Finder root-path display should append the repo + branch, e.g. `utils (main ...)` (basename + branch + a dirty marker). Render in `setFileExplorerPathDisplay` (`yolomux.js:3294`); reuse the branch helpers (`shortBranch` / the branch-badge formatters ~5602/5618). Non-`main` branches stand out.
  - Validate: hover a repo dir -> popover shows repo + branch + dirty/ahead-behind; root the Finder inside a repo on a feature branch -> path reads `repo (branch ...)`; no `git` spawn per hover (cached).

- [x] Finder "flashes" on filesystem change — update the tree incrementally instead of rebuilding. Shipped 2026-05-30: `renderTreeChildren` reconciles direct rows keyed by path, preserves expanded child containers, scroll, selection, and new-entry coloring, and refresh paths no longer clear then rebuild the tree first.

- [x] Add a `Download` item to the file right-click context menu (grep `file-context-menu`, ~2542), alongside the existing Copy full/raw/relative path / Rename / Delete. Downloads the file to the browser. Backend `filesystem.read_raw` already streams bytes; add (or reuse) a raw-file endpoint that sets `Content-Disposition: attachment; filename=...` so the browser saves instead of previewing. For a directory, either disable Download or offer a zip. Support multi-select (download each, or a single zip). Respect read-only mode rules and the `MAX_RAW_BYTES` cap.
- [x] When opening a text file, group it into the EXISTING editor window instead of spawning a new editor panel. If an editor window is already open, add the file as a new tab in that window and focus it (and just focus the tab if the file is already open there). Only create a new editor window when none exists yet. Avoids one-editor-panel-per-file sprawl in the layout.
- [x] PROJECTS + live modified-files panel + editable diff view. Shipped through DOIT.8 on 2026-05-31: Finder has a bottom modified-files panel for the active tmux session/repo, `/api/session-files` reports changes since default-branch merge-base plus working-tree files, `/api/fs/diff` powers editable CodeMirror diff decorations, refreshes preserve scroll, and rows show compact status chips, agent icons, dates, and +/- counts.
- [ ] [S] Configurable File Explorer quick-access paths (e.g. `/tmp` + other custom dirs), set in Settings. NOTE: this is shortcuts, not new access — the explorer FS scope is already `/` (`filesystem.py` header: "No sandbox root"), so any path is reachable by navigating; today it just defaults to `homePath` and you have to walk there. Add a list of pinned quick-access roots shown in the explorer (a favorites/shortcuts row, or selectable roots), each opening via the existing `openFileExplorerAt(path)` / `fileExplorerRoot` machinery (grep `openFileExplorerAt`). Store the list as a user setting (`settings.yaml` once the Settings Panel lands; until then, localStorage). Default could include `~` and `/tmp`. Validate paths exist + are readable before showing; gracefully skip missing ones. Ties to the Settings Panel section. CONCRETE: ship built-in jump buttons for `/`, `~`, and `/tmp` as the default quick-access row (a small row of buttons in the explorer header), each calling `openFileExplorerAt(...)`.
- [x] Make the Finder root path bar typable. The overlay and pane variants now use path inputs; Enter jumps via `openFileExplorerAt(value)`, Escape reverts to the current root, bad paths show an inline error, `~` expands against the server home path, and the existing copy button still copies the current root.
- [x] File Explorer root MODE toggle. The explorer header now has a `Root` / `Sync` toggle stored in localStorage. `Root` preserves the current fixed-root behavior. `Sync` re-roots an open Finder/File Explorer to the focused tmux session's current working directory when a tmux session becomes active; if no cwd is resolvable, the current root is kept. Manual navigation in Sync mode stays until the next tmux session switch.
- [x] Image preview on hovering an image file icon. Hovering the `.file-tree-icon` of an image-extension file row pops a capped thumbnail from `/api/fs/raw?path=<enc>` using the shared popover show/hide delays; non-image rows and files over the raw-read cap do not open a preview.
- [x] Image preview polish: hovering anywhere on an image file's row — the whole row/name, not just the icon — pops up the thumbnail below-right of the cursor and follows pointer movement, clamped on-screen.
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
- [x] File-tree disclosure triangle sizing + color. The dir triangle (`▸` collapsed / `▾` expanded) now uses `0.95em` so it tracks the row text size, collapsed dirs are gray, expanded dirs are green, and dir rows carry `aria-expanded` for stateful styling.
- [x] Type-specific file-tree icons. Today every file uses the same `📄` and every dir uses `▸` (grep the `const icon = entry.kind === 'dir' ? '▸' : ...` line in `static/yolomux.js`, ~2965). Add a `fileIconFor(name)` helper that picks an icon by extension / basename: images (`IMAGE_EXTENSIONS`) -> graphics icon, `.log` -> log icon, `.md` -> doc, `.json`/`.yaml`/`.toml`/`.ini`/`.cfg` -> config/gear, `.sh`/`.bash` -> shell, `.py`/`.js`/`.ts`/`.rs`/`.go`/`.c`/`.cpp` -> code, archives (`.zip`/`.tar`/`.gz`) -> archive, plus a sensible default. Reuse `IMAGE_EXTENSIONS` / `TEXT_EXTENSIONS` for the buckets. Keep it CSS-class- or emoji-based (no new asset dependency unless we add an icon font deliberately).
- [x] Special color for repo directories. A directory that is a repo root (has `.git`, optionally `.hg`/`.svn`/`.jj`) should render in a distinct color (and maybe a repo glyph), not plain white. Detection: cheapest is backend — in `_entry_info` (`yolomux_lib/filesystem.py`), for a dir set `is_repo = (path / '.git').exists()` (one extra stat per dir entry) and include it in the listing payload; the client then adds a `.file-tree-row.is-repo` class for the color. (The app already knows git roots via `git_root_for_path` / `path_info`, but the tree listing needs the per-entry flag.) Keep it cheap — only stat for `.git` on dir entries, not files.

### Bug Fixes And Tech Debt

- [x] Calm the "working" YO badge. Fixed 2026-05-31: working state no longer uses the red attention pulse or the orbiting dot; the surviving cue is the calm YO badge rotation.
- [x] Tab hover popover flicker, redundant passing-CI pill, and double-click tab rename completed in the DOIT.7 batch.

- [x] Pane active-tab toolbars now share a common right-edge frame-control cluster. Tmux sessions keep their Tx/AI/Log/Info content buttons, editors keep Edit/Preview/Split/wrap/find/save, Finder/Info/Preferences keep their own content, and all tab types reuse the same `_` / zoom / close frame rendering and event path.

- [x] YOLO approver handles the new Claude no-caret prompt format. Fixed 2026-05-30: a current prompt with visible `1. Yes` / `2. No` and no selector defaults to yes, stale no-caret prompts are rejected, and the new footer is covered by detector tests.

- [x] The pane `+` (expand) button is toned down at rest and only brightens on hover/focus, matching the weight of the `_` minimize and `...` actions buttons next to it.

- [x] Clicking a YO badge in the `Tabs` dropdown row toggles YOLO without re-sorting or closing the menu.
- [x] Dense `Tabs` dropdown rows stay readable on small viewports; old tmux/YOLO session-submenu rows were removed when sessions moved under `Tabs`.
- [x] Editor LEFT HALF goes blank the moment you type. FIXED 2026-05-30 on the textarea-era path; CodeMirror-only editor later removed that overlay failure mode entirely.
- [x] Preferences shows a YELLOW popup that keeps FLASHING. FIXED 2026-05-30: Preferences help is inline, render refresh keeps focused DOM intact, and periodic YOLO/settings status updates no longer recreate hover popups.
- [x] Always use PC-style pane frame controls (`_` minimize, `□` maximize, `×` close) regardless of platform — drop the macOS traffic-light circles. FIXED 2026-05-30: `platformWindowControlClass()` always returns PC classes and the unused Mac traffic-light CSS was removed. The `?platform=` override remains only for labels such as Finder/File Explorer.
  - PROPOSAL — distinguish `_` (minimize) from `...` (session actions). The actions button is `pane-actions` rendered as a literal horizontal `...` (JS ~9077) and the PC minimize is a low horizontal bar `_` — both are small, low, horizontal marks and read as similar. Recommended distinction: (1) make the actions button a VERTICAL ellipsis `⋮` (the universal "more/overflow menu" affordance) so it can't be confused with the horizontal `_`; (2) GROUP the true pane frame controls together at the far right with a small gap/separator, and keep the `⋮` actions menu to the LEFT with the content buttons, so "menu" and pane management are spatially separated; (3) give the frame controls the PC-style square hover highlight (and red hover for close) while the `⋮` stays a plain icon button. Net: shape (vertical vs horizontal), grouping (left vs far-right), and hover treatment all differ.

- [x] Top-bar menu popup casts a shadow over its TOP edge — drop the top shadow, keep bottom/left/right. FIXED 2026-05-30: app menus and submenus now use layered shadows so the floating edge is clearer.
- [x] Network disconnect wipes the screen — retain it across reconnects. FIXED 2026-05-30: transient reconnects keep the existing xterm instance and reopen only the WebSocket; disconnect status is shown as a pane toast and no text is written into the terminal buffer.

- [x] Detaching from inside a pane (tmux `Ctrl-b d`) leaves it showing `[detached (from session N)]` and feels hung. Fixed server-side: when `bridge_tmux` sees `tmux attach-session` exit cleanly with returncode 0 and the session still exists, it immediately starts a new `tmux attach-session` on the same PTY/WebSocket bridge instead of tearing down the client connection. Non-zero exits still follow the normal disconnect/reconnect path.
- [x] On disconnect, KEEP the pane's existing content and FLOAT a "Disconnected" overlay instead of mutating the content. FIXED 2026-05-30 with per-pane reconnect toasts and in-place WebSocket reopen.
- [x] `#` (tab-metadata) toggle hides the wrong things. Today `body.tab-meta-hidden .pane-tab .session-button-text { display: none }` (`yolomux.css`, grep `tab-meta-hidden`) hides the ENTIRE text block — state badge + branch + PR badges + description — while the YO marker badge and the status dot (rendered OUTSIDE `.session-button-text`, via `yoloMarkerHtml` + `.session-button-prefix`) stay visible. Desired: when `#` is unclicked, ONLY the symbol badges should go away (YO marker, state badge `sessionStateHtml`, PR compact badges, the status dot), and the readable text should stay (session number/name in `.session-button-prefix`, branch name, description). This needs restructuring which elements `tab-meta-hidden` targets, because `.session-button-text` currently bundles both symbols and text — split the symbol badges out (or target them individually) so the toggle hides symbols only. CONFIRM the exact "symbols" set with the user before building.
- [x] Panel detail/status row polish (`.panel-detail-row` — the `branch · path · dirty N` row under the tab strip). (1) Push the close `×` (`.panel-detail-close`, grep in `static/yolomux.css` ~1468) further to the right so it sits flush at the far edge. (2) Give the row a more gray background — it is currently near-black `#121823` (grep `background: #121823` / `.panel-detail-row {`, ~1464); use a lighter gray so the status row reads as distinct from the terminal below it.
- [x] Pane toolbar buttons need tooltips with their full names. `Tx` / `AI` / `Log` have NO `title` today — `tabAttrs` (grep in `static/yolomux.js`) only sets `data-tab` / `data-tab-name`. Add `title` + `aria-label`: `Tx` -> "Transcript", `AI` -> "AI summary", `Log` -> "Event log", `Info` -> "Branch Info" (it currently says "hide details"). The `<` / `>` step buttons and the active-window terminal button already have titles (keep them). Hover should reveal the full name.
- [x] The Enable/Disable YOLO dropdown item shows the YO icon, exactly as it looks on the tab. The command passes `iconHtml: yoloMarkerHtml(session, auto, {...})` so the menu item uses the identical marker and enabled state.
- [x] Trim the tmux-tab RIGHT-CLICK menu to exactly three items: Enable/Disable YOLO, Rename session, Kill session. `showSessionContextMenu` now uses only `tmuxSessionActionCommands`, and that helper orders the YOLO toggle first; the per-pane kebab still keeps the fuller menu.
- [x] Menu label/text consistency: top-level `Tmux` was renamed to lowercase `tmux`; README and the live-menu notes here were updated.
- [x] Rename menu labels include the session name: `Rename tmux session '<name>'`.
- [x] In the `Tabs` dropdown rows, the YO marker is clickable to toggle YOLO WITHOUT closing the menu. Rows are built by `menuTabCommand` and render `yoloMarkerHtml(..., {toggle: true})`; the click handler intercepts `[data-auto-session]`, prevents row activation, keeps the dropdown open, and re-renders the marker state.
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
  - [ ] [M] Option B (event-driven kill): when a terminal WebSocket closes (already detected), confirm via a roster check that the session is gone and prune it from the UI immediately, instead of waiting for the poll. Handles the open-pane kill case fastest; pair with A for closed-pane sessions.
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
