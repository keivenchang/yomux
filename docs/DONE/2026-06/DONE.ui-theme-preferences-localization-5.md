# UI, Theme, Preferences, and Localization

## DOIT.48 Startup Tips
- Added lightweight Startup Tips that show one delayed feature tip after app load for admin users with Tips enabled. The tip catalog covers drag/drop, image prompts, YO!agent, YO auto-approval, YOLO rules, `Differ`, Finder Sync/Reload, editor ref diffs, notifications, watched PRs, quick search, Markdown Preview, and rsync for large uploads. The tip uses the existing toast renderer, rotates serially through localStorage, never focuses controls, offers `Next tip`, `Hide this`, and `Turn off Tips forever`, and persists the forever-off choice through `general.startup_tips`. Preferences can re-enable it from General. Added source/runtime guards plus settings coverage.

---

Completed 2026-06-07. Extracted from the 2026-06-07 daily log.
