# 2026-07-19 Companion CLIs under tools

- Completed root sweep part 2 from `DOIT.repo-structure.md`. `auto_approve_tmux.py` and `tmux_wall.py` now live under `tools/`; the worker import, dev watcher, package discovery, compiler gate, tests, README, development guide, and GUI spec all use the new location. Direct `python3 tools/<cli>.py ...` commands preserve their repository-root import path, and the wall keeps resolving the served static assets from the repository root.
- Verification beyond the standard gate: both new-path `--help` commands worked and the focused companion/import/structure suite passed 236 tests. The move also exposed duplicate asset-version code, which now uses the shared `path_mtime_or_zero` owner.
