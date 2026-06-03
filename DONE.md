# DONE

Archive of completed YOLOmux work, newest first. Concise by design — the full implementation
detail (file/symbol, fix, tests) lives in the git commit history on `main`. Each item shipped to
dev with a test (node `tests/layout_url.test.js` and/or `pytest`) green.

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
