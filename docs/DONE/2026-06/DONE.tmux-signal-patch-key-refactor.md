# Tmux signal patch key refactor

- Completed and removed `DOIT.refactor_tmux_signal_keys.md`. Tmux signal window patching now routes record keys through one backend helper (`window_record_key()`, backed by `window_key()`) and one frontend helper (`tmuxSignalWindowKey()`), instead of rebuilding `session:window_index` in `app.py`, patch merge code, pane lookup, and global activity counters. Added regression coverage for fallback session/window records with no explicit `key`.

---

Completed 2026-06-24. Extracted from the 2026-06-24 daily log.
