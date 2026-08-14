# Tmux, Terminal, YO!share, and Tabber

## Tmux popover hierarchy labels and shared working-dot pulse
- Completed and removed `DOIT.window_working_circle_pulsate.md` and `DOIT.popover_label_tmux_session_window.md`.
- Claude/Codex working dots now inherit a shared `status-indicator status-indicator--dot status-indicator--working` parent behavior: the green `●` pulses continuously with the existing `command-palette-thinking` animation, idle `○` remains static, and reduced-motion disables the pulse.
- Session popovers now label hierarchy explicitly with localized `popover.tmuxSession` and `popover.tmuxWindow` strings, so headers read `tmux session 8001 · …` and agent rows read `tmux sub-window 0:codex — working …` while preserving the canonical agent-kind label and avoiding duplicate prefixes.
- Verification: `node tests/editor_preview.test.js`, focused browser tests for the rendered working-dot animation and popover title, `python3 tools/static_build.py`, final `python3 tools/check.py`, and 8001 restart/ping all passed.

## Backend tmux resize and runner cleanup
- Completed the Refactor Audit Backlog rows for backend resize helper, attach command builder, and fire-and-forget tmux calls. `resize_pty_and_signal_process` now owns PTY resize plus live-process `SIGWINCH` for both `ShareTerminalUpstream.update_dimensions` and WebSocket resize; `tmux_attach_command(readonly=...)` already covers readonly/admin attach paths; server list-client, refresh-client, and has-session checks now use the shared `tmux()` runner instead of direct `subprocess.run(tmux_command(...))` calls.
- Reconciled backend-poll default fallbacks through the settings default snapshot: app-side server poll, background file poll, directory poll, Tabber activity refresh, and auto-approve interval methods now read fallback values from `DEFAULT_PERFORMANCE_SETTINGS`, while existing settings migrations and catalog metadata remain the single source for stale saved defaults and UI descriptions.
- Verification: focused backend regression subset passed (`python3 -m pytest tests/test_server_query.py -k 'resize or tmux_attach or routing_ws_readonly or configure_session_tmux_options'`: 9 passed), full backend query tests passed (`python3 -m pytest tests/test_server_query.py`: 68 passed), focused app/settings poll checks passed (`python3 -m pytest tests/test_app.py tests/test_settings.py`: 218 passed, one existing fake Codex app-server thread warning), exact browser-editor rerun passed after one transient full-gate `Script error.`, and final `python3 tools/check.py` passed (`CHECK PASSED in 50.54s`).

## Tmux sub-window stale-signal bounce and roadmap cleanup
- Fixed the direct tmux-window selection bounce where clicking `1:claude` could briefly repaint `0:codex` from a stale `/api/tmux-signals` readback before settling back to `1:claude`. Explicit window targets now also normalize the cached signal snapshot while the override is active, so the window bar, Tabber active row, active path, and Finder sync source do not contradict the clicked target during stale readbacks.
- Pruned shipped roadmap rows from `docs/TODO.md`: visible YO!agent job list, editor save hygiene, reload-from-disk, editor status counts, Search & Runs full-text search, compact run history, Finder/Differ/Tabber shared-tree row rendering, HTTP share parity, and share replay-health diagnostics; those features are already covered by current code, README/DEVELOPMENT/spec docs, DONE entries, and tests.
- Verification: `node tests/editor_preview.test.js`, `node tests/tabber.test.js`, `python3 tools/check.py`, ignored-queue scan (`rg --files -uu -g 'DOIT*.md'`), TODO link check, `git diff --check`, and 8001 restart/ping (`pid 2556156`, `/api/ping` returned expected unauthenticated `401`).

## Tmux sub-window button immediate highlight
- Completed and removed `DOIT.window_button_immediate_highlight.md`. Direct tmux sub-window clicks and `Ctrl-b <num>` now set the active window button and `aria-pressed` synchronously before the backend/network round trip, keep that explicit target through interim renders to avoid flicker, and still run the authoritative tmux POST/read-back path afterward. Relative `Ctrl-b n/p` remains read-back-driven and does not guess a local next/previous window; its read-back uses the fast transcript metadata path without auto/activity refresh fan-out.
- Verification: `node tests/editor_preview.test.js` covers synchronous direct-click and numeric-prefix highlighting before unresolved network calls, no local metadata prediction, and relative `n/p` landing on backend `window_active`; `python3 tools/static_build.py` rebuilt the bundle and the final `python3 tools/check.py` passed (`CHECK PASSED in 56.29s`).

## Tab popover agent working and idle time
- Completed and removed `DOIT.tab_popover_agent_working_time.md`. The tab/session popover now lists every Claude/Codex agent window near the top, sorted with working agents first, showing live status-counter elapsed time for working panes and activity-ledger idle time for idle panes; tabs with no agent windows say so explicitly.
- Verification: backend payload regression passed, Node layout suite passed (`layout suite: 166 passed, 0 failed`), full `python3 tools/check.py` passed (`CHECK PASSED in 51.56s`), dev8001 was restarted with `tools/yolomux-restart-dev1.sh`, auth-gated `/api/ping` returned 401, and a live authenticated browser render against 8001 showed session `8001` with `codex — working 18m 23s` above `claude — idle 3h 00m`.

## Tmux sub-window switch read-back
- Completed and removed `DOIT.tmux_window_keys_nav_sync.md`. Tmux sub-window switches no longer preview a local relative-index guess: direct buttons still POST `/api/tmux-window`, raw `Ctrl-b n/p/<num>` bytes still pass through unchanged, and both paths wait for forced transcript metadata read-back so the highlighted button comes from backend `window_active`/`window_name`.
- Verification: live `tmux list-windows -t 8001 -F '#{window_index} #{window_name} #{window_active}'` showed three windows, `python3 tools/static_build.py`, `node --test tests/editor_preview.test.js`, `python3 -m pytest tests/test_browser_dockview.py::test_dockview_window_bar_buttons_select_tmux_windows -q`, focused rerun of the one flaky browser diff test passed, and full `python3 tools/check.py` passed (`CHECK PASSED in 47.35s`).

## Tabber compact home paths
- Completed and removed `DOIT.tabber_home_path_tilde.md`. Tabber row rendering now runs human-visible labels, descriptions, and titles through the shared `compactHomePath()` helper, so repo paths under the configured home display as `~/...` and the bare home displays as `~`, while non-home paths, already-tilde paths, absolute repo metadata, and synthetic `/s_.../w_...` ids are unchanged.
- Verification: `node tests/tabber.test.js` passed (`35 passed`, with existing disabled-fetch fixture warnings only) and full `python3 tools/check.py` passed (`CHECK PASSED in 47.48s`).

---

Completed 2026-06-20. Extracted from the 2026-06-20 daily log.
