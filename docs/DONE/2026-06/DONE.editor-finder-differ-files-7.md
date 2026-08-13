# Editor, Finder, Differ, and Files

## DOIT.53 active-pane Quick Open, pane chrome, Finder/Differ toolbar, and working-state polish archived
- Completed and removed `DOIT.53.md`. Cmd-P normal file opens now target the pane that owns the active tab while explicit split-open keeps split behavior; the session agent badge moved from the top tab-title row to the pane Info Bar; the pane-header detail toggle now says Show/Hide details instead of YO!info and syncs title/aria/pressed state; tmux panes render a per-window button bar with duplicate-name disambiguation and numeric overflow fallback; Codex working-state detection treats bottom composer/model-status chrome as chrome so the YO marker spins while the agent is actually working.
- Finder and Differ now share `Date | Expand all | Collapse all | Reload` toolbar controls through one parent path. The Expand/Collapse buttons use skinny in/out arrow SVG icons, stay narrow in all toolbar contexts, and Sync-mode Expand is bounded to affected sync-plan paths such as `yolomux.dev2` instead of recursively crawling broad home roots. Verification beyond the standard gate: the focused Finder Sync browser regression.

---

Completed 2026-06-10. Extracted from the 2026-06-10 daily log.
