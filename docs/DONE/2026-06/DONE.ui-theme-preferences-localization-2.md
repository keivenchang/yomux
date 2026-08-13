# UI, Theme, Preferences, and Localization

## GUI and localization polish
- About is now a localized modal at the bottom of Help: the large brand glyph spins, Chinese uses `優` / `优` and `樂` / `乐`, Close is the visible `X`, `SHA` remains literal, and Keiven Chang links to LinkedIn.
- Localized the upload/attention toast shell, upload result messages, YO!info / YO!agent Chinese labels, user-facing PR/session status labels, fallback relative-time strings, `Refresh summary`, git/status popover text, and info-table empty/header/sort strings. Added tests so Chinese surfaces do not regress while protocol terms like `SHA`, `PR`, `CI`, `HEAD`, `git`, and `tmux` stay literal where intended.
- Recorded the vendored CodeMirror package versions and pinned the bundle dependency record so future CodeMirror upgrades are explicit.

---

Completed 2026-06-04. Extracted from the 2026-06-04 daily log.
