# Build, Refactor, Docs, and Tests

## Documentation cleanup pass
- Cleaned the Markdown docs so the README leads with the default all-tmux-session launch path, `AGENTS.md` has a clear documentation map, `docs/DEVELOPMENT.md` uses a portable detached `nohup` restart recipe instead of the old transient-unit notes, `docs/TODO.md` drops stale disabled-layout wording, `docs/specs/GUI.md` states that Wall is the separate `tools/tmux_wall.py` companion rather than an app layout mode, and the CodeMirror/vendor notes read more cleanly.
- Verification: `git diff --check -- '*.md'`, a local Markdown link check over all tracked `.md` files, and a stale-phrase grep for the old restart and disabled-layout wording.

---

Completed 2026-06-13. Extracted from the 2026-06-13 daily log.
