# 2026-06-02 (versions 0.1.76 – 0.1.101)

### Agents and Automation

#### Auto-approve & prompt detection
- Auto-approve fires with the Ctrl-T task overlay shown below a live approval prompt (bounded-overlay break instead of treating the overlay as newer output).
- Footer-hint matching accepts multi-key + parenthetical footers (e.g. `(ctrl+b ctrl+b (twice) to run in background)`) so a live prompt stays auto-approvable.
- `capture-pane` uses `-J` so a wrapped command is captured as one logical line (a wrap can otherwise split a token and flip a danger verdict).
- YO marker spins only while working (slow, configurable period), never when idle.
- Backend safety/correctness pass (P0/P1/P2): YOLO hard-floor always-on, takeover re-acquire, send-action re-verify, worker stop-join, WS frame cap, metadata TTL + bounded cache, settings coerced-keys reporting, transcript tail windowing, and related hardening.
- DECISION (DOIT.10, resolved/archived): keep the fail-safe TUI prompt-liveness guard as-is. Rejected the "default-to-live" rework — it flips the safety bias toward typing into a stale prompt (false-positive keystrokes), which is worse than the recurring footer whack-a-mole it would fix. The footer-hint band-aid + `-J` capture are the standing solution; the recurring breakage is the correct price of failing safe.
- Reliable-auto-approve groundwork (DOIT.11, partial): the Claude `PreToolUse` permission hook (`yolomux_lib/claude_permission_hook.py`) + 17 tests landed — reuses the existing rule engine, returns allow/deny/ask programmatically (no keystrokes, no TUI scraping), fails safe. Still OPEN and user-gated (tracked in TODO Big-Bang #1): wiring it into `~/.claude/settings.json`, live validation, standing the keystroke worker down once live; Codex (`app-server` JSON-RPC) deferred.

### Editor, Finder, Differ, and Files

#### Editor, diff & markdown
- The editor is plain by default — no auto-loaded diff and no inline diff coloring on open; changes appear only in the explicit diff view (the diff button / Modified-files menu).
- Clicking a relative link in a rendered markdown preview opens+renders the target file in the same pane (with a path normalizer; out-of-root links are rejected server-side and toast).
- Tightened YO!agent markdown spacing (loose lists render compact); block cursor fills the full character cell.
- README opens in rendered preview; View→Theme re-themes open editors and terminals in lockstep; theme is chosen via macOS-style preview cards.

### UI, Theme, Preferences, and Localization

#### Internationalization (i18n)
- Extracted every user-facing string in the app to a key-based `t()`/`tPlural()` catalog — menu bar (incl. the whole tmux menu), Modified-files panel, diff-ref, comparison, file-explorer + editor toolbars, command palette, keyboard-shortcuts overlay, pane chrome, rename dialog, branch list, version banner, PR-review chips, YO!info/YO!agent panel + chat, Preferences, file-editor dialogs, file-tab tooltips, hover-popover labels, session-state labels, toasts/status lines. The `en-XA` pseudo-locale shows zero plain English except intentional proper nouns (Codex/Claude/tmux/git/PR/README/theme names).
- Shipped 12 UI locales at full key parity (build-enforced): English, Traditional Chinese, Simplified Chinese, Spanish, Japanese, German, French, Brazilian Portuguese, Russian, Korean, Hindi, Arabic — plus the `en-XA` pseudo-locale. The five least-common were parallel-translated by subagents.
- Three language entry points, all endonym-labeled, all writing the same `general.language` setting: the login-screen picker (persists after sign-in), a top-right topbar switcher, and the Preferences picker. `system` resolves against the browser locale.
- Right-to-left support: Arabic drives `dir="rtl"` (client + server-rendered shell); converted all physical margin/padding/border-left|right and text-align to logical properties so the layout mirrors under RTL with no LTR change.
- Locale-aware formatting: relative time via `Intl.RelativeTimeFormat`, dates via `Intl.DateTimeFormat` (LA timezone), counts via `tPlural`/`Intl.PluralRules`.
- Chinese brand glyphs (優/优 marker, 優樂mux/优乐mux wordmark) render and re-render on a language switch; the login screen localizes server-side; the YO!agent LLM backend gets a "Respond in <language>" directive and the deterministic backend's fixed framing localizes.

#### Drag, layout & panes
- Fixed the real tab-drag root cause — a full panel re-render mid-drag was wiping the grid and aborting the native drag; it now defers and flushes on drop.
- Tab drag-reorder works in both directions (left→right and right→left), from any pane including Preferences.
- Every pane keeps its active tab highlighted (not just the focused pane); the focused pane keeps an extra ring.
- File-menu Finder entry toggles; File→Finder/Preferences/etc. open in place.

#### Tabs, badges, search & menus
- Light-mode tab badges are legible; removed the redundant "PR" pill; killed the duplicate native tooltip on hover; the session popover shows review status and reviewer.
- The default search bar blends matching commands/tabs/settings with file results; duplicate file-search results (mirrors/symlinks) collapse.
- "Branch Info" renamed to "YO!info"; merged YO!info + YO!agent into one pane with a sub-tab toggle; Preferences gained a max-tabs-per-pane field and the Performance section sits above YO!agent.

### Build, Refactor, Docs, and Tests

#### Performance
- Tab moves no longer take several seconds: a layout change no longer re-polls the server, and same-shape changes (reorder/activate/move) swap only the affected panes in place instead of tearing down and rebuilding the whole topbar + grid. Markdown preview renders are guarded by a path+content signature.
- Completed and removed `DOIT.exit_not_exit.md`. Exit-linger fallback polling now uses a 1.5s interactive base with existing jitter, and YO!stats/debug state records tab/window removal latency from WS close or tmux pane/window events through browser removal. Verified with `python3 tools/static_build.py --check`, `node tests/layout_restore.test.js`, `node tests/editor_preview.test.js`, `node tests/layout_async.test.js`, `python3 -m pytest tests/test_app.py::test_client_status_poll_fallbacks_are_interactive_with_jitter tests/test_app.py::test_tmux_signal_event_publishes_changed_window_patch tests/test_app.py::test_tmux_signal_event_publishes_removed_window_origin tests/test_app.py::test_tmux_signal_full_snapshot_keeps_removed_window_origin tests/test_app.py::test_tmux_signal_event_does_not_force_auto_approve_poll -q`, `python3 -m py_compile yolomux_lib/app.py`, and targeted `git diff --check`.

#### Process & learnings
- Recorded recurring-failure lessons from the batch (stale-backend pitfalls, build/restart discipline, falsely-marked-done detection) and folded them into the working notes.
