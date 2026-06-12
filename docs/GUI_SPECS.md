# YOLOmux GUI Behavior Spec

This document is the working GUI contract for pane, tab, Finder/Differ, editor, preview, Preferences, keyboard shortcuts, and drag/drop behavior. It is meant to drive future implementation and tests. If a UI change conflicts with this file, update the spec and the tests in the same change.

## Popular IDE Parity Checklist

- [x] Activity/workbench panes can be arranged as tabs inside split panes.
- [x] Tabs can be reordered within the same pane by pointer drag.
- [x] Tabs can be moved from one pane to another pane's tab strip.
- [x] Tabs can be dropped on pane edges to split the target pane.
- [x] Tabs can be dropped on root edges to create full-span panes beside the current layout.
- [x] Tab insertion preview appears between tabs, not over the target tab.
- [x] Active tabs have rounded top corners and a distinct active background.
- [x] Inactive tabs inherit the tab-container background instead of using a disconnected color.
- [x] Pinned tabs stay at the front of a pane, show a pin icon, are protected from LRU tab eviction, and can be toggled from the tab context menu or the `Mod+K` then `Enter` shortcut.
- [x] Tab hover shows a detail popover with session/file state.
- [x] Tab context menu exposes tab-level actions before destructive session actions.
- [x] Open editors support edit, preview, split, and diff-capable modes based on file capability.
- [x] Markdown task checkboxes in Preview are interactive and update the source document.
- [x] Editor back/forward buttons navigate the visited-file stack.
- [x] Search results can render image/file references as `[Image #N] '/absolute/path'`.
- [x] File quick-open ranks basename matches ahead of path-only matches.
- [x] Newly opened tabs (files, Preferences, YO!info/YO!agent, and other non-tmux tab types) open in the pane that owns the currently active tab; Finder/Differ stays reserved and tmux sessions keep their placement rules. DONE 2026-06-11: generalized from the former quick-open-only targeting rule.
- [x] Finder/Differ rows support selection, multi-select, rename/delete, context menu, date/sort controls, and image preview.
- [x] Preferences exposes searchable, collapsible sections with reset controls and live-applied settings.
- [x] Keyboard shortcut labels use the concrete platform modifier: Mac uses `Cmd`; PC/Linux uses `Ctrl`.
- [x] Global app shortcuts are captured before focused panes swallow them when the shortcut is platform-owned.
- [x] Code editor shortcuts include save, find, replace, go-to-line, comment toggle, indent/outdent, undo/redo, and editor back/forward.
- [x] Inline git blame can annotate the current caret line, with an option to annotate all lines.
- [x] Editor themes include dark/light mode switching, separate dark/light scheme defaults, caret style, and caret color.
- [ ] Partial: pinned-tab behavior is per browser profile through local storage, not yet a shareable URL or server-side preference.
- [ ] Partial: file tabs and tmux tabs share pinning, movement, and pane placement; Finder/Differ remains a reserved special pane.
- [ ] Partial: preview/edit interactions are implemented for Markdown task checkboxes, not for every possible rendered widget.
- [ ] Keyboard-only tab reordering and pane splitting are not implemented.
- [ ] Multi-root project workspaces are represented through Finder roots and companion directories, not a full workspace model.
- [ ] Extension/plugin APIs for editor behavior are not implemented.
- [ ] Integrated source-control side panels are partial: Differ and inline blame exist, but a full branch/commit workbench is not implemented.
- [ ] Problems/debug/test runner panels are not implemented as first-class workbench panels.
- [ ] Terminal/profile management is tmux-session based, not a full shell-profile workbench.

## Terms

- Pane: a visible browser layout region that contains one tab strip and one active tab body.
- Tab: a layout item inside a pane. Current tab types include tmux sessions, Finder/Differ, file editors and image viewers, Preferences, and YO!info/YO!agent.
- Finder/Differ: one special file pane. Finder mode shows the file tree. Differ mode shows changed files and diff controls. Tabber mode shows a live tree of open tabs, tmux windows, and touched repo roots, sorted by recent activity. Legacy `changes` / `__changes__` URLs resolve to Finder in Differ mode.
- Root edge: the outside edge of the pane layout. Dropping there creates a full-span pane beside the existing layout.
- Pane edge: the edge of one target pane. Dropping there splits only that target pane.
- Cross-gutter: the separator between two sibling panes. Dropping there creates a full-span split at that sibling boundary.

## Layout Model

- Pane layout is percentage-based and reload-safe. `layout` and `tabs` URL parameters are the shareable source of truth.
- A pane split must not advertise a preview if the target rect cannot fit both resulting panes. Left/right splits require enough width for the current target pane plus the incoming tab. Top/bottom splits require enough height for both panes.
- A middle drop must not advertise a preview if the target pane cannot display the incoming tab at its minimum width and height.
- Pane minimum sizes come from shared CSS/JS sizing tokens and tab-type minimums. Do not hard-code a one-window pixel capacity in a local drag handler.
- A local pane-edge drop splits only the target pane. It must not flatten same-axis siblings into equal thirds. Example: dragging into the right half of `1/2 | 1/2` should be able to produce `1/2 | 1/4 | 1/4`.
- A root-edge drop creates a full-span pane beside the entire current content area. If panes are stacked top-to-bottom, dragging to the left or right root edge creates a full-height leftmost or rightmost pane.
- Root top/bottom drops with a docked Finder/Differ column split only the non-Finder content area, so the preview and resulting pane do not cover the reserved Finder/Differ column.
- Root drops over the Finder/Differ column are reserved and must not show a preview.
- Cross-gutter drops preserve the sibling boundary and create a full-span pane at that boundary.
- Closing or moving tabs must preserve user-chosen split percentages where possible. Empty placeholder panes are allowed when they preserve a meaningful user split.
- Resizing the Finder/Differ sash changes the root Finder/Differ percentage and preserves the nested content split percentages. Example: in `Finder | Pane1 | Pane2`, dragging the Finder sash changes the available content width while Pane1 and Pane2 keep their relative ratio.
- The pane-header detail-toggle button controls the pane detail strip only. It must be labeled as `show details` or `hide details` based on state and must not reuse the YO!info pane label.

## Finder/Differ Pane Rules

- Finder/Differ is a real pane in the layout, not an overlay.
- Finder/Differ/Tabber are three modes of the same reserved pane. Dockview adoption, Tabber refresh, Differ refresh, and Finder refresh must preserve that pane and its current tree position unless the user explicitly hides it with the close button, File -> Finder toggle, or the Finder shortcut.
- Finder/Differ is reserved as a target. Nothing can be dropped into its center, left edge, right edge, or top edge.
- The only allowed drop onto Finder/Differ is the bottom edge, and only when Finder/Differ is large enough for the existing Finder/Differ pane and the incoming tab after the split.
- Finder/Differ itself is not draggable as a layout tab. Dragging Finder/Differ must not advertise a pane split, root split, or gutter split.
- A file dragged from Finder or Differ into a normal pane opens in that pane. Dropping near a normal pane edge opens it in a new split and shows the same dashed preview used for tab drops.
- A file dragged from Finder or Differ onto Finder/Differ follows the reserved-target rule: no preview except the allowed bottom split when there is enough room.
- Directory drags into terminal content keep the terminal path-insertion affordance. They may show a normal-pane split preview when the target is not Finder/Differ and the pane has enough room.
- Finder and Differ rows share selection, multi-select, context menu, date columns, status columns, image preview, rename/delete, and keyboard behavior through shared helpers.
- Finder mode and Differ mode must keep the same toolbar alignment rules, date-mode vocabulary (`None`, `Date`, `Ago`), sort vocabulary (`A-Z`, `Z-A`, `new`, `old`), and readable row metadata. The trailing tree controls are ordered `Date | Expand all | Collapse all | Reload` on both Finder and Differ, and Expand all/Collapse all use compact skinny in/out arrow toolbar icons rather than square text glyphs.
- When Differ auto-switches to a session whose changed-file payload is being fetched and no cached payload is available, it must show an inline loading state with subtle movement, not `not loaded`. The current standard is the small spinning YO marker followed by loading text and the session label. If a loaded cached payload exists for that session and comparison, Differ must keep showing it while refreshing in the background instead of replacing it with the loading state.
- Finder Sync mode `Expand all` is bounded to the sync plan: expand affected repo/directories such as the active `yolomux.dev2` path and do not recursively crawl every directory under a broad home root. Fixed-root Finder mode may still recursively expand the current root.
- Differ's target session is committed only by explicit user intent: clicking/selecting a pane or typing/pasting/inserting input into a terminal. Hover auto-focus, passive xterm focus, pointerenter pane selection, background refresh, SSE updates, and tab-hover popovers must not switch the Differ session or fetch another session's changed-files payload.
- The Finder path field shows its invalid (red) state only on a real directory-open failure, scoped to that exact path. A deferred or suppressed background refresh — the SSE push channel will supply the listing, a push refresh is mid-flight, or a fresh cache hit — is not a failure and must never flag the field, and a recorded error for one path must not taint another. (Otherwise the path flashed red for ~one push cycle whenever an idle background refresh was skipped.)

## Tabber Pane Rules

- The Tabber is the third mode of the one shared file-pane mode control (`Finder / Differ / Tabber`); like Differ it takes over the pane (the file tree is hidden, the changes panel fills the pane). Legacy `files`/`diff` behavior is unchanged. A newly restored/default file pane opens in Finder mode; Tabber is an explicit mode selection, not the startup default for the left file pane.
- Tabber shows a live tree of everything open and defaults to fully expanded. Level 0 = tmux sessions (sorted first), then the other open tabs (Preferences, YO!info/YO!agent, file editors) as leaf rows at the end. Session rows always render expanded so their window/process rows are visible. The session row splits the session name and work description into separate click targets: clicking the session name opens/activates that tmux tab, while clicking the description expands the session. Level 1 = the session's tmux windows (`index:process (pid=N)`, e.g. `0:claude (pid=12345)`, tinted by agent); Claude/Codex windows show the same shared agent icon used by the tmux menu after the process name and before the pid, scaled to the row font, and the session's current tmux window is marked with a trailing `●`. Under a `claude`/`codex` window, Level 2 = absolute touched repo roots from `/api/session-files`, lazily fetched + cached per session, with a folder icon and the git branch on the matching repo row. These path rows are leaves and use normal text color; Tabber must not list every individual file, nested repo subdirectory, `/tmp` artifact, or `.claude/projects/...` transcript path under them. While the first touched-path fetch is in flight, the agent window shows a moving `Fetching paths...` row. Non-agent windows (e.g. `bash (pid=54321)`) are leaf rows.
- Startup behavior: immediately after launch transcript metadata is available, Tabber should already be able to render the Level 0/Level 1 session, process, PID, active-window, and Claude/Codex icon rows. This first useful paint must not wait for `/api/session-files`; touched repo/path hydration is background/lazy and may fill in later under the already-visible agent window.
- Toolbar behavior: Tabber keeps only the shared mode switcher and pane frame controls. Finder-only controls (`.*`, Sync/current-root controls, quick-root buttons, create file, create folder, Finder sort/date/reload controls) and Differ-only controls (session picker, Differ expand/collapse) are hidden because Tabber is an inventory/navigation view, not a filesystem editing or changed-files view.
- Rows render through the SHARED Finder/Differ row pipeline (`renderTreeChildren` -> `updateFileTreeRow` -> `updateFileTreeRowContents`) via a `mode: 'tabber'` render option whose per-row display values are precomputed as data — never a bespoke per-pane row builder. Selection, the icon column, and the shared date modes (`None` / `Date` / `Ago`) all apply unchanged. Tabber uses restrained level colors: level 0 session/other-pane rows share one accent, level 1 window/process rows share one accent, and level 2 paths stay normal foreground text. Purple remains reserved for merged PR status. The collapse state is a persisted COLLAPSED set (default expanded) keyed by a stable id-based node path (`s_<id>/w_<i>/r_<n>`), so it survives reloads and a session add/remove never shifts another node's open/closed state. Clicking a row's disclosure icon toggles that node; the toolbar `Expand all` / `Collapse all` apply to the whole Tabber tree.
- Time column + sort: each row's time is the activity-ledger recency (`max(last_user_input_ts, last_agent_active_ts, last_output_ts)`); a parent row inherits the most-recent time among its children, and a window with no ledger/child time falls back to the session's recency so every window shows a time. Touched repo-root rows use the newest actual file mtime among files folded into that repo root, falling back to transcript time only when the touched file no longer exists. The tree honors the active Finder sort control for top-level sessions and touched-path rows — `A-Z`/`Z-A` sort by the human label, `new`/`old` by the recency time — and re-sorts on the normal poll without collapsing the tree (collapse state is keyed by the stable path, not display order). The immediate window/process rows under each session are the exception: they always stay in tmux window-index order (`0`, `1`, `2`, ...), regardless of recency or the active tree sort. The server keeps an indefinite `/api/activity` cache, warms it at startup, refreshes it about every 60 seconds, and serves stale cache immediately while a background refresh runs. The browser fetches that cached `/api/activity` once after transcript metadata is available, once when Tabber opens, then every `performance.tabber_activity_refresh_ms` while Tabber is active; the default is 15 seconds and Preferences shows it in Performance. The `/api/session-files` touched-path data is fetched lazily once per agent session and cached until manual reload clears the cache; known repo roots are the display unit, so descendant touched directories fold into a top-level repo such as `/home/keivenc/ai-config` instead of rendering redundant child rows like `/home/keivenc/ai-config/claude/skills/...`.
- Row actions: clicking a session name opens/activates that tab; clicking a session description expands that session; clicking a non-session tab row opens/activates that tab; clicking the YO!info row opens the merged info pane on the YO!info sub-tab, not the remembered YO!agent sub-tab; clicking a window row opens the tab and switches to that tmux window (shared `tmuxWindow({windowIndex})` path); clicking an absolute path row points the Finder at that path (switching to Finder mode) and also opens the tab + switches the window; right-clicking an absolute path row reuses the shared file context menu for that directory. Hover/passive focus never act.

## Tab Strip Behavior

- Clicking a tab activates it in its current pane.
- Dragging a tab within the same tab strip reorders it. The preview must appear between tabs, not on top of the target tab.
- A same-strip reorder must commit on a slow, deliberate drag, not only on a quick flick. Drag-and-drop is pointer-based (`dndStrategy: 'pointer'`), so the dragged tab smooth-reorders under the cursor; the drop-target tab must be resolved from the pointer x-coordinate with the dragged tab excluded (nearest remaining tab when the pointer sits over the dragged tab's own gap), never by hit-testing whatever element is under the pointer. Hit-testing the element under the pointer resolves to the dragged tab itself, makes the drop look like a `source == target` no-op, and silently swallows the reorder — only a flick that outran the smooth-reorder transform would land, which reads to the user as "drag sometimes does nothing".
- In a two-tab strip the dragged tab fully covers the drop point. The reorder must still commit, and a secondary pinned pointer-reorder fallback must stand down once the primary drop pipeline has handled the gesture, so it cannot re-run and mirror-swap the order back (which makes the completed drag look undone).
- Dragging a tab to another tab strip moves it to that pane at the indicated insertion position.
- Pinned tabs stay in their current pane. A pinned tab can reorder within the pinned partition of its own pane, but it cannot be moved to another pane or split into a new pane.
- Invalid pinned-tab affordances must not take ownership of drag state. They must be derived from the current Dockview preview/target geometry and must clear immediately when the target changes or the drag ends; do not cache an invalid target and replay or repair the drop after release. Invalid pinned/non-pinned partition targets, including pinned tab-strip drops into another pane, show the normal between-tabs dashed preview in the danger color.
- Dragging a tab to another pane edge splits that target pane only.
- Dragging a tab to a root edge creates a full-span pane beside the current layout.
- The tab insertion preview is a visible dashed box between tabs and uses the same configurable color as the pane separator.
- The active tab keeps rounded top corners. Inactive tabs use the same background as the tab container.
- The active pane's tab container is slightly brighter than inactive panes. Inactive tabs inside the active pane use that same active-pane tab-container background.
- Tab hover details must show the session/file information popover. Dockview tab popovers must not be clipped by the Dockview tab scroller.
- Header actions such as back/forward, minimize, close, and add must stay on the first header row when there is room.
- The pane info (detail) row reads `path · dirty-count | tmux window buttons | ×` with NO separate agent badge — terminal agent identity is carried by the window buttons (an agent window's name falls back to its process label, so it reads `N:claude` / `N:codex`). The agent marker must never appear in the first tab-title row.
- Tmux session detail rows show a window bar with one button per tmux window, immediately before the detail-row close control. Buttons use compact `index:name` labels, keep duplicate-name suffixes when needed, never show process IDs in the visible button text, switch directly to that tmux window on click, hide with collapsed details, and fall back to number-only labels when the name bar would be too crowded. The ACTIVE window's button reads as a pressed toggle styled through the shared `--active-control-*` tokens (so it follows the user's Active color preference and flips with the theme); inactive buttons keep the plain button look.
- Many tabs may wrap, but wrapping must not overlap content or force action buttons into a second line when first-line space is available.

## Pane Chrome And Resize

- Pane separators are skinny at rest and use the shared separator token. Hover and active hit targets may be wider, but the visible rest line stays skinny.
- Separator hover thickness is at least 5px.
- Dashed tab/file/root previews use the same configurable separator color.
- Pane spacing and active pane rings must not cover terminal/editor content. Content surfaces resize inside the ring/spacing.
- Dockview sash hit targets are transparent at rest so only the shared separator line is visible.
- Active/typing-ready panes feed the same active-ring color into Dockview and legacy pane chrome.
- The active ring is a state indicator, not layout capacity. Changing pane spacing must not shift terminal text under the ring.

## File Editor And Preview Behavior

- Markdown opens in the right mode for the source context. README-style opens may default to Preview; code/source files default to edit/search/diff-capable editor views.
- Markdown Preview task checkboxes rendered from `- [ ]` and `- [x]` are clickable for admin users. Clicking one updates the Markdown source, refreshes preview panes/popouts, updates editor documents, and uses the normal dirty/autosave path.
- Markdown Preview is not read-only when the user interacts with task checkboxes, but unsafe HTML remains blocked.
- The file editor Differ button appears only when blame/diff capability exists for that file. Blame and Differ controls appear or hide as a pair.
- A clean git-tracked file with meaningful history can stay in diff mode so the FROM/TO picker remains usable.
- Preview pop-outs and preview panes must not drive Differ editor scrolling.

## Search, Menus, And Popovers

- Search results that reference images or files should use a Popular IDE-style display when possible: `[Image #N] '/absolute/path'`.
- Cmd-P file quick-open and other newly opened non-tmux tabs preserve active-pane context. A normal file, Preferences, or YO!info/YO!agent open lands in the pane that owns the currently active tab; explicit split-open keeps its split behavior; Finder/Differ remains a reserved pane target.
- Quick Search and Tabs search should collapse duplicate open-file rows by path and surface open view chips instead of showing duplicate file entries.
- Tab search indexes session labels, branch/PR metadata, Linear IDs, file paths, and tab details.
- Unified search ranking contract: `Mod+P` and `Shift+Mod+P` search the SAME universe (files, panes/tabs, commands, settings) and differ ONLY in ranking prior — `Mod+P` ranks files first, `Shift+Mod+P` ranks panes first. Dominance order within results: (1) per-surface domain prior, (2) anchored prefix on the primary name (typing `6` puts file `6.md` first in `Mod+P`, pane `6 …` first in `Shift+Mod+P`), (3) contiguity (`123` ranks `hello 123` above `1 a 2 b 3 c`), (4) recency (file index mtime; pane recency uses latest agent transcript mtime from the session payload, then summary timestamps as fallback), (5) focused-session repo affinity. Match quality dominates recency; recency dominates repo affinity. For typed blended results, the first visible screen keeps a bounded mix of file and pane matches so async file-index results cannot crowd a strong pane hit offscreen. The scoring and first-screen mix weights are ONE exported data table guarded by table-driven ranking tests, so behavior is tuned by editing weights + expected-order rows, not by rewriting ranking code.
- Unified search shows a compact loading status while backend file search is in flight, even when local pane/command/recent results are already displayed.
- Menus and popovers are global UI state. Hover must not auto-open menus when auto-focus is off unless the user explicitly opened a menu first.
- Popovers use shared timing and ownership so tab, image, menu, and file-preview popovers do not fight each other.
- Custom popovers should replace duplicate native `title` text when the custom popover already provides the same information.

## Preferences Contract

- Preferences is a normal tab type and may be moved, activated, minimized, and closed like other non-Finder tabs.
- Preferences must expose a search field, collapsible sections, per-row reset controls, a global reset flow, the settings file path, and a settings age/status indicator.
- Preference changes save through the shared settings API and live-apply when the setting affects visible UI. Appearance, terminal/editor, Finder/Differ, popover timing, notification, and YOLO changes must not require a page reload unless the setting explicitly says so.
- General settings must include language, auto-focus, startup tips, and default sessions.
- Appearance settings must include global theme, default layout, UI and Finder font sizes, tab width, max tabs per pane, pane spacing, pane ring opacity, inactive pane opacity, active color, separator color, editor cursor color, YOLO rotation timing, and 12-hour/24-hour clock mode.
- Terminal / Editor settings must include terminal theme, terminal/editor/preview font sizes, terminal scrollback, editor dark/light schemes, editor cursor style, word wrap, line numbers, autosave, autosave delay, and all-lines blame.
- Notifications must include update notification, notify transition keys, toast duration, throttle, red reminder timing, and metadata badge pulse timing.
- Finder/Differ settings must include root mode, image open mode, image preview size, quick-access paths, indexed directories, index refresh, companion directories, tree refresh interval, directory cache timing, and new-entry highlight timing.
- Uploads must include filename template and upload size cap.
- Performance must include auto-reload on update, metadata refresh, watched-PR refresh, pane-state refresh, latency refresh, event-log refresh, popover show/hide timing, menu hover timing, tab popover timing, and remote resize debounce.
- GitHub must include watched PRs.
- YOLO must include auto-approve interval, rule file path, dry-run mode, and prompt source.
- YO!agent must include backend, invocation mode, auto-refresh, refresh interval, system prompt, intro, and format.
- Any new Preference row must be represented in the server defaults, sanitization/clamping rules, frontend `preferenceSections()`, locale catalogs, settings tests, and live-apply runtime if it affects visible UI.

## Keyboard Shortcuts Contract

- The app modifier is platform-specific: Mac uses `Cmd`; PC/Linux uses `Ctrl`. Shortcut labels must say the concrete platform modifier and must not display combined `Ctrl/Cmd` text.
- Mac app shortcuts use `Cmd` so they can be captured even when a terminal is focused; Mac `Ctrl` stays available for terminal/tmux input. PC/Linux app shortcuts use `Ctrl`.
- Global shortcuts are captured before focused controls swallow them, but text inputs, rename fields, editors, and terminal input must keep their normal typing shortcuts unless a shortcut is explicitly platform-owned by the app.
- Implemented app shortcuts: `Mod+P` opens unified search with file-first ranking, `Shift+Mod+P` opens the same unified search with pane/command-first ranking, `Mod+B` toggles Finder/Differ, `Mod+,` opens Preferences, `?` opens Keyboard shortcuts, `Esc` closes shortcut/menu overlays, and `Mod+W` closes the active closable tab.
- Close-tab fallback: outside text-editing contexts, `Mod+Backspace` / `Mod+Delete` may close the active closable tab. This fallback must not fire inside inputs, rename fields, editors, or terminal text entry.
- Editor shortcuts: `Mod+S` saves, `Mod+F` finds, `Mod+H` replaces, `Mod+G` goes to line, `Mod+/` toggles comments, `Tab` / `Shift+Tab` indent and outdent, `Mod+Z` / `Shift+Mod+Z` undo and redo, and `Mod+Alt+[` / `Mod+Alt+]` move editor navigation back and forward.
- Diff editor chunk shortcuts reuse editor undo/redo: `Mod+Z` undoes the selected chunk action and `Shift+Mod+Z` redoes it when the diff editor owns the focus.
- Finder/Differ keyboard behavior follows Finder-style list navigation: Arrow Up/Down moves selection, Shift+Arrow extends selection, Home/End jump, Shift+Home/End extend to edge, Arrow Right expands or steps into children, Arrow Left collapses or steps to parent, Space toggles quick preview, type-ahead selects by prefix, `Mod+A` selects all, `Mod+O` and `Mod+Down` open, `Mod+Up` opens the enclosing folder, and Return starts rename.
- Finder/Differ delete is platform-specific: Mac uses `Cmd+Delete` or `Cmd+Backspace`; PC/Linux uses plain `Delete`. It must be scoped to Finder/Differ and must not run from text inputs or rename controls.
- Terminal copy handling must preserve normal terminal input: `Cmd+C` on Mac or `Ctrl+C` on PC/Linux copies xterm selection when present; without a browser/xterm selection, the tmux copy bridge may request the tmux copy-mode selection.
- Tab drag, pane split, and root-edge layout changes are currently pointer-driven. Keyboard equivalents for moving tabs/panes are not implemented and must not be advertised as available until they exist and are tested.

## Visual Preview Contract

- Valid drops show exactly one preview owner: root preview on the grid, pane preview on the target group/pane, or tab insertion preview between tabs.
- Invalid drops show no dashed preview and must suppress any native Dockview overlay for that invalid target, except pinned/non-pinned tab partition violations which reuse the native tab insertion preview geometry in the danger color.
- Finder/Differ reserved areas must not show a preview except the allowed bottom split case.
- Root top/bottom previews with Finder/Differ docked must start at the non-Finder content column and never cover the Finder/Differ column.
- Preview geometry must be measured from DOM rects, not guessed from a fixed browser size.
- Preview color follows the pane separator preference. Tests should compare computed preview border color with the separator token.

## Current Test Coverage

- `tests/layout_url.test.js` guards layout serialization, legacy `changes` URL migration, old four-pane URL migration, Finder/Differ reserved drop rules, min-size drop gating, source structure for Dockview root/pane/file drop hooks, tab insertion placement math, empty tab-strip end drops, file-drag payload shape, Markdown task toggles, Finder/Differ shared row helpers, search image/file reference quoting, shortcut overlay honesty, and many source-level invariants.
- `tests/layout_url.test.js` includes a source x target x zone drag/drop matrix fixture that covers tabs, file rows, directory rows, Finder/Differ, normal panes, root edges, and gutters through the shared tab/file/path intent gates.
- `tests/test_browser_layout.py::test_dockview_tabs_keep_yolomux_active_inactive_style` covers active/inactive Dockview tab visual parity with screenshot pixel sampling plus computed spacing, rounded active tab, inactive background, hidden scrollbar, and header-action geometry assertions.
- `tests/test_browser_layout.py::test_dockview_drag_reorders_tabs_in_same_pane` covers same-pane tab reorder by asserting the result of a real pointer drag (not a scripted layout write), so a slow-drag reorder that gets silently swallowed by the no-op veto fails the test.
- `tests/test_browser_layout.py::test_dockview_drag_reorders_two_tab_pane` and `test_dockview_drag_reorders_two_pinned_tabs` cover the two-tab strip where the dragged tab covers the drop point, guarding against both the no-op veto and the pinned-fallback mirror-swap revert.
- `tests/test_browser_layout.py::test_dockview_tab_drag_preview_is_between_tabs` covers the visible between-tabs preview.
- `tests/test_browser_layout.py::test_dockview_drag_moves_tab_to_other_pane` covers moving a tab into another pane's tab strip.
- `tests/test_browser_layout.py::test_dockview_drag_splits_tab_to_right_pane_and_measures_geometry` covers basic pane-edge split geometry.
- `tests/test_browser_layout.py::test_dockview_same_axis_second_split_preserves_target_half` covers `1/2 | 1/4 | 1/4` same-axis split preservation.
- `tests/test_browser_layout.py::test_dockview_drag_to_root_left_of_stacked_panes_creates_full_height_pane` and `test_dockview_drag_to_root_right_of_stacked_panes_creates_full_height_pane` cover full-height root drops beside stacked panes.
- `tests/test_browser_layout.py::test_dockview_root_left_drag_shows_full_span_preview_before_drop` and `test_dockview_root_right_drag_shows_full_span_preview_before_drop` cover full-span root previews.
- `tests/test_browser_layout.py::test_dockview_too_small_pane_edge_rejects_tab_preview` covers no preview for too-small target panes.
- `tests/test_browser_layout.py::test_dockview_finder_drop_previews_are_bottom_only_and_size_gated` covers Finder/Differ reserved tab-drop previews and the bottom-only exception.
- `tests/test_browser_layout.py::test_dockview_file_drag_from_finder_opens_in_target_pane_with_preview` covers Finder/Differ file drags into normal panes.
- `tests/test_browser_layout.py::test_dockview_file_drag_to_finder_previews_only_roomy_bottom` covers Finder/Differ reserved file-drop previews and the bottom-only exception.
- `tests/test_browser_layout.py::test_dockview_multi_file_drag_preserves_order_dedupes_and_uses_one_target` covers multi-file Finder/Differ drags: one preview, one target slot, stable insertion order, and deduped file-editor tabs.
- `tests/test_browser_layout.py::test_dockview_directory_drag_over_finder_is_reserved_but_terminal_path_target_stays_allowed` covers directory drags over Finder/Differ reserved layout targets while preserving terminal path insertion into terminal content.
- `tests/test_browser_layout.py::test_dockview_root_top_drag_preview_preserves_docked_finder_column`, `test_dockview_root_bottom_preview_preserves_docked_finder_column`, and `test_dockview_root_top_bottom_preview_normalizes_right_finder_and_avoids_reserved_column` cover root top/bottom previews that avoid the docked Finder/Differ column, including the persisted right-docked legacy layout normalization case.
- `tests/test_browser_layout.py::test_dockview_docked_finder_sash_resize_updates_root_pct` covers Finder/Differ sash resize and nested content ratio preservation.
- `tests/test_browser_layout.py::test_dockview_active_ring_follows_pane_spacing_without_thickening_sash`, `test_dockview_pane_spacing_multiple_values_keep_terminal_inside_ring`, `test_dockview_complex_layout_sash_hit_targets_stay_transparent`, and `test_dockview_hidden_inner_header_keeps_terminal_content_full_height` cover pane chrome, separators, and terminal/editor content geometry.
- `tests/test_browser_layout.py::test_separator_color_preference_recolors_drop_previews` covers the separator color preference across tab insertion, pane preview, root preview, and file-drag preview.
- `tests/test_browser_layout.py::test_markdown_preview_task_checkbox_updates_split_source_and_preview` covers clicking Markdown Preview task checkboxes while split source and preview panes are open, asserting both source and preview update.
- `tests/layout_url.test.js` guards the platform app modifier, shortcut catalog, shortcut labels, Finder/Differ key intent map, Finder/Differ delete scoping, Preferences section order, Preference path rendering, settings defaults, and settings clamps.

## Audit Backlog For Future Specs And Tests

- [x] High priority: terminal Info Bar agent labels stay muted, tmux window buttons align at the right next to the detail close control, and hiding/showing details refits xterm without overlap or clipped content. DONE 2026-06-11: fixed shared detail-row styling, Dockview collapsed grid rows, and terminal refit scheduling; guarded by `tests/layout_url.test.js` and `tests/test_browser_layout.py::test_dockview_terminal_info_bar_alignment_and_detail_toggle_refits_xterm`.
- [x] Preferences: expose the Active YO rotation period control from the YOLO dropdown, keeping it synced with the Preferences value. DONE 2026-06-11: added a number-setting row in the YOLO submenu that writes `appearance.yolo_rotate_ms` through the shared settings patch path and live-applies CSS settings; guarded by `tests/layout_url.test.js`.
- [x] Add a source x target x zone drag/drop matrix fixture that enumerates tabs, file rows, directory rows, Finder/Differ, normal panes, root edges, and gutters. This should assert preview owner, drop effect, and final layout for each valid case. DONE 2026-06-11: added a table-driven intent matrix in `tests/layout_url.test.js` over `dropIntentAllowsSession`, `fileDropIntentAllowsPayload`, and `pathDropIntentAllowsPayload`.
- [x] Add visual screenshot checks for active/inactive Dockview tabs against the pre-Dockview tab style so spacing, rounded active tabs, inactive background, and header actions cannot drift. DONE 2026-06-11: strengthened `test_dockview_tabs_keep_yolomux_active_inactive_style` with screenshot pixel sampling plus computed geometry assertions for spacing, rounded active tabs, inactive background, hidden scrollbars, and header actions.
- [x] Add a multi-file Finder/Differ drag test. It should verify one preview, one target slot, stable insertion order, and no duplicate file-editor items for repeated paths. DONE 2026-06-11: added `test_dockview_multi_file_drag_preserves_order_dedupes_and_uses_one_target`.
- [x] Add a directory drag test over Finder/Differ. It should verify terminal directory insertion behavior stays intact while Finder/Differ remains a reserved layout target. DONE 2026-06-11: added `test_dockview_directory_drag_over_finder_is_reserved_but_terminal_path_target_stays_allowed`.
- [x] Add a persisted URL migration fixture for old four-pane layouts and legacy `changes` URLs after the Dockview rewrite. DONE 2026-06-11: added the old four-pane `tree:` plus legacy `changes` URL migration fixture in `tests/layout_url.test.js`.
- [x] Add a root-edge drag test with a docked Finder/Differ column on both left and right sides. It should distinguish allowed content-root drops from reserved Finder/Differ-root drops. DONE 2026-06-11: added browser coverage for the supported left reserved dock and the persisted right-docked legacy normalization path; the product normalizes Finder/Differ back to the left reserved dock, and previews avoid that reserved column.
- [x] Add tests for root top/bottom preview when the pointer is inside a content pane but horizontally near the Finder/Differ boundary. DONE 2026-06-11: added `test_dockview_root_top_bottom_preview_normalizes_right_finder_and_avoids_reserved_column`.
- [x] Add a browser test for pane spacing changes at several values, asserting terminal/editor content rects never overlap the ring or separator. DONE 2026-06-11: added `test_dockview_pane_spacing_multiple_values_keep_terminal_inside_ring`.
- [x] Add a test that changes the separator color preference and verifies tab insertion, pane preview, root preview, and file-drag preview all use the new color. DONE 2026-06-11: added `appearance.separator_color`, live CSS token application, Preferences/locales/settings coverage, and `test_separator_color_preference_recolors_drop_previews`.
- [x] Add keyboard-accessible tab and pane movement if the product decides to support non-pointer layout changes. Until then, keep the UI and shortcut overlay honest that tab/pane layout changes are pointer-only. DONE 2026-06-11: kept layout movement pointer-only and guarded the shortcut overlay copy so it advertises drag/pointer behavior, not nonexistent keyboard movement.
- [x] Add a Search rendering fixture for `[Image #N] '/path'` output, including file paths with spaces and shell-special characters. DONE 2026-06-11: quoted image/file reference paths with `shellQuote()` and added fixtures for spaces, brackets, apostrophes, and `$`.
- [x] Add a Markdown Preview task checkbox browser test with split editor/preview panes open at the same time, asserting both source and preview update after a click. DONE 2026-06-11: added `test_markdown_preview_task_checkbox_updates_split_source_and_preview`.
- [x] Collapse any future duplicate drop validation into `dropIntentAllowsSession`, `fileDropIntentAllowsPayload`, or a shared parent. Do not add a one-off Dockview-only or terminal-only target gate. DONE 2026-06-11: exported the shared file/path intent gates for tests and routed the new Dockview matrix through the shared validation path.
- [x] Implement and test "drop a tab on the empty tab-strip background past the last tab = move to end". This is currently a no-op: only drops that resolve onto an existing tab reorder, so a release in the empty strip area beyond the last tab leaves the order unchanged. DONE 2026-06-11: added empty tab-strip end-drop intent helpers, integrated them into Dockview drop/no-op/pinned guards, and covered move-to-end/already-last/pinned-partition cases in `tests/layout_url.test.js`.
