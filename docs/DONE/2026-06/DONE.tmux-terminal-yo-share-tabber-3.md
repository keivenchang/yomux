# Tmux, Terminal, YO!share, and Tabber

## DOIT.56 neon cursor choices, focused-pane opens, and terminal tab labels
- Completed and removed `DOIT.56.md`. Cursor color now has cursor-only neon presets (`laser-lime`, `neon-green`, `neon-cyan`, `neon-magenta`, `neon-orange`) without adding neon active-pane ring colors; the same preset parent drives editor cursor, active terminal cursor, and pane scrollbar thumb, with darker light-theme variants for readability. New tabs and file opens now target the focused non-Finder pane instead of the first pane, while Finder focus still falls back to a normal content pane. Terminal pane header tabs now show the compact `Term` label while keeping the active process/window detail in title/ARIA and the Info Bar.
- Verification for this archive entry was focused rather than the full standard gate: `python3 -m pytest tests/test_settings.py tests/test_static_build.py -q`, `node --check static/yolomux.js`, `node tests/layout_url.test.js`, `python3 -m pytest tests/test_browser_layout.py -q -k "active_color_radios_recolor_live_pane_chrome or new_virtual_and_file_tabs_open_in_focused_pane or new_tabs_do_not_open_in_focused_finder or terminal_info_bar_alignment"`.

---

Completed 2026-06-11. Extracted from the 2026-06-11 daily log.
