# UI, Theme, Preferences, and Localization

## DOIT.67 Dockview tab overflow record archived
- Completed and removed `DOIT.67.md`, which was a note-only checklist from the dev2 Dockview tab overflow regression. The checked items confirm wrapped Dockview tabs, auto-height Dockview headers, right-side action reservation, crowded tmux/file-editor browser coverage, generated CSS rebuild, and dev-server restart expectations.
- The durable tab-wrapping contract remains in `docs/specs/GUI.md`: crowded Dockview tabs wrap into additional rows, the header grows, toolbar/content starts below the full wrapped header, and tabs must not scroll or paint under the first-row action cluster.

---

Completed 2026-06-13. Extracted from the 2026-06-13 daily log.
