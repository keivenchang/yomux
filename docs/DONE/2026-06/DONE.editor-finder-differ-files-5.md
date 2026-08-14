# Editor, Finder, Differ, and Files

## DOIT.50 caret/active color parent cleanup
- Collapsed Active color and caret color onto one shared UI color parent with one default caret constant, removed the dead caret-color per-color locale keys, exposed backend setting choice metadata for both color settings, and made Preferences/tests derive caret ordering and labels from the shared parent. The CSS boot default now routes through the active accent token until JS applies the configured caret color, avoiding a second hardcoded Solar gold literal. Verification beyond the standard gate: `python3 -m pytest tests/test_settings.py -q`, `python3 -m pytest tests/test_browser_layout.py -q -k "scrollbar or active_color_radios_recolor_live_pane_chrome"`.

---

Completed 2026-06-08. Extracted from the 2026-06-08 daily log.
