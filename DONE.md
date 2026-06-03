# DONE

Archive of completed YOLOmux work, newest first. Concise by design — the full implementation
detail (file/symbol, fix, tests) lives in the git commit history on `main`. Each item shipped to
dev with a test (node `tests/layout_url.test.js` and/or `pytest`) green.

## 2026-06-03

### Pane chrome — one shared, state-driven theming system (no per-button/per-bar one-offs)
- All pane-head BARS (tab strip, info/detail row, editor toolbar, CodeMirror find panel) read one `--pane-bar-bg`: the bright tab-strip green when the pane is focused, neutral gray when not — so the info bar/find no longer stay green when unfocused (DOIT.12 B5 / images 010/011).
- All control BUTTONS (terminal/agent pill, window-step, actions, info toggle, minimize, expand `+`, close) read `--pane-ctl-*`: rest = white (light) / near-black (dark), pressed/active = green. Killed the navy active-tab and always-green `+` one-offs (DOIT.12 B5 toolbar `.tab.active`; image 009). The agent pill is green when active, `+` is white/black.
- Light-mode tab text fixed (was white-on-white): `--pane-tab-text` dark in light, plus a dark override for the `.session-button-name/dir/detail` spans that hardcoded near-white. Added a headless contrast guard (computed text color vs tab bg) — the gap that let it ship was a browser test measuring backgrounds but never nested text colors.
- Inactive-pane dimming is ONE CSS rule keyed off the uniformly-toggled `.focused-pane` class; deleted the per-pane JS overlay + `isVirtualItem` special-casing (DOIT.12 B2).

### Topbar
- The "big space" above the green tabs was the topbar's own cool-gray background showing where the shorter pills sit centered in a too-tall bar (DOIT.12 A1/A2). Painted the topbar the green tab-strip color (`--pane-tab-strip-bg`) in both themes so it's one continuous green with no band, tightened its height (`+4px`, `line-height:1` on the brand title), and completed the Notify-toggle active state. Guard in layout_url.
- Topbar light-mode coloring sweep (DOIT.12 A4): defined `--accent` per theme (was undefined → literal light-blue hover hairline in both themes); and light overrides for the menu-count badge halo, the near-black `.transport-warning` tooltip, the garish `#tabMetaToggle.active` green (→ `#5f9800`), and the invisible menu row separator.

### Auto-approve (YOLO) reliability
- Claude `PreToolUse` permission hook shipped (DOIT.11): `yolomux_lib/claude_permission_hook.py` decides allow/deny/ask from the agent's structured request via the existing `yolo_rules` engine (no TUI scraping, no keystrokes), with 17 tests pinning the mapping + the hard-floor + fail-safe. **Remaining is user-gated / deferred and tracked in TODO Big-Bang #1**: the manual `~/.claude/settings.json` install (must not be auto-edited), live validation, the keystroke-worker stand-down (gated on the hook being live), and the Codex `app-server` re-architecture. DOIT.11.md is kept as the research record until those land.

### Diff
- Diff "expand / collapse all unchanged" toolbar toggle (DOIT.12 B4): a diff-only button (persisted `diffExpandUnchanged`, green when on) that shows full context (omits `collapseUnchanged`) or restores the collapsed runs, in both side-by-side and unified diff; the config signature includes it so toggling rebuilds. Localized in all 13 locales.
- Diff overview-ruler ticks were misaligned and went stale after expanding an "N unchanged lines" fold (DOIT.12 B3). Now positioned from the editor's rendered geometry (`lineBlockAt`/`contentHeight`, so they track folded space; line-fraction fallback before first measure), click-to-scroll jumps to the chunk's real rendered top, and a CM `updateListener` rebuilds the ticks on any geometry/height change (incl. fold expand/collapse) on both the unified and side-by-side views.

### Build / structural
- `10_topbar_menus.css` had a TRUNCATED `.notify-toggle.active {` rule whose body was split into the next partial — it only rebalanced by accident in the bundle (DOIT.12 B1). Completed the rule, removed the orphaned body, and added a `check_css_braces()` build step that fails on any brace-unbalanced CSS partial (with a regression test). The check immediately caught the latent split.

### Light mode — full surface sweep (DOIT.12 B5, all HIGH items in AUDIT-LIGHTMODE.md)
- Cleared every HIGH "dark box / invisible text on white" bug. The root cause was component rules hardcoding a dark color literal with no `body.theme-light` / `body.editor-theme-light` counterpart, so the dark value rendered on the white surface. Fixed surfaces: the whole session/tab/file hover popover (near-black-green box + pale-green text → light card, dark text, re-tinted the label/value/desc/legend cluster — the audit's "highest single fix"); the neutral session-state badges + inactive YO marker (dark-slate chips → light gray); the command palette + keyboard-shortcuts dialog (were dark-on-dark: dark fill with theme-aware `--text` → light cards, dark rows/kbd); the global-reset warning bar (cream-on-brown wash → light amber tint, dark amber text); the `.agent-icon` glyphs (white → dark); the drop-target fill, the file-missing tab (dark maroon → light error tint), the server-update banner (dark navy slab → white), the file-tree current/repo-non-main/indexed rows (near-white-green / washed-amber text → darkened) + a visible selection ring; the rename input (near-black fill with `--text` → white); YO!agent markdown code blocks (`#0b0e14` → light); and in the light editor: the image backdrop + transparency checkerboard (dark/inverted → light), the diff overview-ruler ticks + inline diff decorations (saturated/dark → matched to the light `--code-diff-*` fills).
- Made it durable: two headless `test_browser_layout.py` guards (`test_light_mode_surfaces_are_readable_not_dark_boxes`, `test_light_editor_image_backdrop_is_light`) build each surface in light mode and assert backgrounds are light AND nested text meets a real contrast ratio (≥3.0). This closes the exact gap that let the white-on-white tab text ship — the prior tests measured backgrounds but never nested text colors. Verified live with a headless computed-color probe across all surfaces (every fixed text 5–15:1 on its surface). The pane-head `.tab.active` and white-on-white tab text were already cleared via the pane-chrome refactor (above); CSS brace/parse enforcement (audit Durable Fix C) shipped as `check_css_braces()`.

## 2026-06-02 (versions 0.1.76 – 0.1.101)

### Internationalization (i18n)
- Extracted every user-facing string in the app to a key-based `t()`/`tPlural()` catalog — menu bar (incl. the whole tmux menu), Modified-files panel, diff-ref, comparison, file-explorer + editor toolbars, command palette, keyboard-shortcuts overlay, pane chrome, rename dialog, branch list, version banner, PR-review chips, YO!info/YO!agent panel + chat, Preferences, file-editor dialogs, file-tab tooltips, hover-popover labels, session-state labels, toasts/status lines. The `en-XA` pseudo-locale shows zero plain English except intentional proper nouns (Codex/Claude/tmux/git/PR/README/theme names).
- Shipped 12 UI locales at full key parity (build-enforced): English, Traditional Chinese, Simplified Chinese, Spanish, Japanese, German, French, Brazilian Portuguese, Russian, Korean, Hindi, Arabic — plus the `en-XA` pseudo-locale. The five least-common were parallel-translated by subagents.
- Three language entry points, all endonym-labeled, all writing the same `general.language` setting: the login-screen picker (persists after sign-in), a top-right topbar switcher, and the Preferences picker. `system` resolves against the browser locale.
- Right-to-left support: Arabic drives `dir="rtl"` (client + server-rendered shell); converted all physical margin/padding/border-left|right and text-align to logical properties so the layout mirrors under RTL with no LTR change.
- Locale-aware formatting: relative time via `Intl.RelativeTimeFormat`, dates via `Intl.DateTimeFormat` (LA timezone), counts via `tPlural`/`Intl.PluralRules`.
- Chinese brand glyphs (優/优 marker, 優樂mux/优乐mux wordmark) render and re-render on a language switch; the login screen localizes server-side; the YO!agent LLM backend gets a "Respond in <language>" directive and the deterministic backend's fixed framing localizes.

### Performance
- Tab moves no longer take several seconds: a layout change no longer re-polls the server, and same-shape changes (reorder/activate/move) swap only the affected panes in place instead of tearing down and rebuilding the whole topbar + grid. Markdown preview renders are guarded by a path+content signature.

### Drag, layout & panes
- Fixed the real tab-drag root cause — a full panel re-render mid-drag was wiping the grid and aborting the native drag; it now defers and flushes on drop.
- Tab drag-reorder works in both directions (left→right and right→left), from any pane including Preferences.
- Every pane keeps its active tab highlighted (not just the focused pane); the focused pane keeps an extra ring.
- File-menu Finder entry toggles; File→Finder/Preferences/etc. open in place.

### Auto-approve & prompt detection
- Auto-approve fires with the Ctrl-T task overlay shown below a live approval prompt (bounded-overlay break instead of treating the overlay as newer output).
- Footer-hint matching accepts multi-key + parenthetical footers (e.g. `(ctrl+b ctrl+b (twice) to run in background)`) so a live prompt stays auto-approvable.
- `capture-pane` uses `-J` so a wrapped command is captured as one logical line (a wrap can otherwise split a token and flip a danger verdict).
- YO marker spins only while working (slow, configurable period), never when idle.
- Backend safety/correctness pass (P0/P1/P2): YOLO hard-floor always-on, takeover re-acquire, send-action re-verify, worker stop-join, WS frame cap, metadata TTL + bounded cache, settings coerced-keys reporting, transcript tail windowing, and related hardening.
- DECISION (DOIT.10, resolved/archived): keep the fail-safe TUI prompt-liveness guard as-is. Rejected the "default-to-live" rework — it flips the safety bias toward typing into a stale prompt (false-positive keystrokes), which is worse than the recurring footer whack-a-mole it would fix. The footer-hint band-aid + `-J` capture are the standing solution; the recurring breakage is the correct price of failing safe.
- Reliable-auto-approve groundwork (DOIT.11, partial): the Claude `PreToolUse` permission hook (`yolomux_lib/claude_permission_hook.py`) + 17 tests landed — reuses the existing rule engine, returns allow/deny/ask programmatically (no keystrokes, no TUI scraping), fails safe. Still OPEN and user-gated (tracked in DOIT.11.md): wiring it into `~/.claude/settings.json`, live validation, standing the keystroke worker down once live; Codex (`app-server` JSON-RPC) deferred.

### Editor, diff & markdown
- The editor is plain by default — no auto-loaded diff and no inline diff coloring on open; changes appear only in the explicit diff view (the diff button / Modified-files menu).
- Clicking a relative link in a rendered markdown preview opens+renders the target file in the same pane (with a path normalizer; out-of-root links are rejected server-side and toast).
- Tightened YO!agent markdown spacing (loose lists render compact); block cursor fills the full character cell.
- README opens in rendered preview; View→Theme re-themes open editors and terminals in lockstep; theme is chosen via macOS-style preview cards.

### Tabs, badges, search & menus
- Light-mode tab badges are legible; removed the redundant "PR" pill; killed the duplicate native tooltip on hover; the session popover shows review status and reviewer.
- The default search bar blends matching commands/tabs/settings with file results; duplicate file-search results (mirrors/symlinks) collapse.
- "Branch Info" renamed to "YO!info"; merged YO!info + YO!agent into one pane with a sub-tab toggle; Preferences gained a max-tabs-per-pane field and the Performance section sits above YO!agent.

### Process & learnings
- Recorded recurring-failure lessons from the batch (stale-backend pitfalls, build/restart discipline, falsely-marked-done detection) and folded them into the working notes.
