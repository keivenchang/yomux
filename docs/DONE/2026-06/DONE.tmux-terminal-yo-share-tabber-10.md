# Tmux, Terminal, YO!share, and Tabber

## DOIT.82 light window buttons, share theme sync, System repaint, and blank terminals
- Completed and removed `DOIT.82.md`. Light-mode active tmux sub-window buttons now keep the shared pressed-fill token even when `data-window-agent` is present; replay-shell YO!share viewers apply live `appearance` and `viewport` frames so host light/dark/system changes flip the mirrored viewer and terminal theme; System/follow-app terminal repaint is covered from both Dark and Light OS-resolved paths; and tmux attaches request immediate plus delayed `refresh-client` passes so a newly opened terminal paints the current PTY screen without manual input.
- Reopened stale prior DONE claims for terminal-theme repaint, share-theme sync, and active tmux sub-window buttons. Live verification after rebuild/restart: active light window button and active pane tab both `rgb(79,158,58)`; real `/share/<id>` viewer flipped light/dark/system with xterm background following `follow-app`; blank terminal crop advanced from one background color to rendered rows without external refresh. Verification: focused pytest/Selenium, `node tests/layout_url.test.js`, and full `python3 tools/check.py` (`CHECK PASSED in 39.64s`).

---

Completed 2026-06-18. Extracted from the 2026-06-18 daily log.
