# Tmux, Terminal, YO!share, and Tabber

## DOIT.80 terminal green selection blocks
- Completed and removed `DOIT.80.md`. Terminal visible-selection handling now has one classifier/cleanup path in `static_src/js/yolomux/10_core_utils.js`: it records xterm selection, browser DOM selection inside the terminal container, recent OSC 52 fallback text, and best-effort tmux pane-mode labels, then clears browser/xterm selection after a terminal copy/open action consumes the selected text.
- Explicit `Copy tmux selection` no longer leaves tmux copy-mode rows painted after the clipboard payload is captured. `yolomux_lib/app.py::tmux_copy_selection()` still uses `copy-selection-no-clear` for the existing fresh-buffer signature check, then sends `send-keys -X cancel` after success, no-copy, or save-buffer failure.
- Regression coverage now includes Node/source assertions for context-menu cleanup routing, backend pytest assertions for tmux cancel calls, and a focused Selenium test proving DOM/xterm visible selection clears in the live runtime fixture. README and `docs/specs/GUI.md` document the preserve-until-consumed, then clear-stale-selection rule.
- Verification: `python3 tools/static_build.py`, `node tests/layout_url.test.js` (`147 passed, 0 failed`), `python3 -m pytest tests/test_session_actions.py -q` (`8 passed`), focused Selenium `python3 -m pytest tests/test_browser_layout.py -k 'terminal_visible_selection_cleanup_clears_browser_and_xterm_state' -q` (`1 passed, 181 deselected`), full `python3 tools/check.py` (`CHECK PASSED in 48.15s` after an unrelated README diff Selenium flake passed in isolated rerun), and dev1 restart/smoke on port `8001` (`ping: 401 0.048482s`).

## DOIT.77 tmux-signal activity awareness
- Completed and removed `DOIT.77.md`. YOLOmux now builds a server-wide tmux signal snapshot, invalidates it from a read-only control-mode watcher/hooks/subscriptions, counts active/attention windows outside the current tab, gates idle pane captures without skipping pending prompts, and discovers server-wide agent panes for YOLO.
- The YO!agent/activity UI now reflects tmux dead/running/silence/bell/presence/zoom/layout/mode/read-only/synchronized signals, prefers live tmux pane path/command for Finder and agent context, sorts/dims recent-agent rows by tmux recency, and uses tmux recency to prioritize activity payload and rolling-summary refreshes.
- `/api/tmux-snapshot` is scrollback-aware through `history_size`/`history_bytes`, caps capture depth, and returns `unchanged` when history has not grown; client sizing data now includes active client details and an authoritative non-control viewer candidate.
- Verification beyond the standard gate: focused tmux/app/server/auto-approve pytest set (`206 passed`), new B5 recency-priority tests, full `python3 tools/check.py` (`CHECK PASSED in 49.86s`), dev1 restart on port `8001`, and `/api/ping` returning the expected unauthenticated `401`.

## DOIT.0 test isolation: YO!share Selenium no longer touches live tmux/dev state
- Completed and removed `DOIT.0.md`. Browser/live generated-share tests now use an isolated fixture with tmp config/state, ephemeral HTTP server ports, short private `/tmp` tmux/control sockets, and generated `yt-<pid>-<uuid>-N` tmux sessions instead of live `8001`/`7777` ports or real host sessions.
- Added the `YOLOMUX_TMUX_SOCKET` tmux command hook so app/server tmux calls can target the fixture-owned socket, plus a source guard that rejects live YOLOmux port literals and generated-share tests that bypass the isolated runtime helper.
- YO!share DOM replay now filters terminal placeholder serialization by the active share's authorized session scope, so an unauthorized host terminal cannot appear as a healthy placeholder and then fail `/ws/share-view` with `403`.
- Verification beyond the standard gate: focused generated-share Selenium pair (`2 passed in 29.85s`), focused affected pytest sets (`12 passed`, `128 passed`), dev1 restart on port `8001`, and `/api/ping` returning the expected unauthenticated `401`.

---

Completed 2026-06-16. Extracted from the 2026-06-16 daily log.
