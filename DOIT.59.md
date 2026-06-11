# DOIT.59 — Uploads land in a `.upload/` subdir (Preferences default)

Goal: browser uploads (drag-drop of OS files onto a terminal, clipboard paste, the `+` upload button) should default into a `.upload/` subdirectory of the session's resolved working directory instead of writing straight into the cwd / repo root. The destination is a Preference; the default is `.upload`.

## Current behavior

- Every upload routes through `upload_target_dir(session)` (`yolomux_lib/app.py:2856`), which returns the focus root / `session_workdir` / git root / pane cwd; `resolved_upload_dir` (`yolomux_lib/workdir.py:15`) only validates that the dir exists (with a home-guard so an empty/`~` cwd can't dump into `$HOME`). Files are written directly into that dir.
- The `uploads` settings group (`yolomux_lib/settings.py:122`) has `filename_template` and `max_bytes`, but no destination/subdir setting.
- Consequence: pasted screenshots and dropped files clutter the repo root, mix with source, and are easy to commit by accident.

## Change

- Write uploads into `<resolved-dir>/.upload/` by default. A dot-prefixed dir keeps the cwd clean, is easy to `.gitignore`, groups every upload in one place, and is trivial to clear.
- Make it a Preference: `uploads.subdir` (string, default `.upload`). Empty string = write directly into the cwd (today's behavior, opt-out).
- Optional later: `uploads.location` enum (`subdir` | `cwd` | `custom`) + `uploads.custom_dir` for a fixed absolute upload dir. Defer unless asked — `uploads.subdir` covers the request.

## Implementation

- One owner: the subdir logic lives ONLY in `upload_target_dir(session)` — every upload caller already routes through it, so do not fork it per call site. Append `settings.uploads.subdir` to the resolved dir, `mkdir(parents=True, exist_ok=True)` it (mode `0o700`), and return it; fall back to the bare resolved dir if creation fails. Keep `resolved_upload_dir`'s home-guard.
- The path the agent sees and that gets shell-quoted into the terminal becomes `<cwd>/.upload/<file>` automatically (no change needed at the insert sites, including the DOIT.57 drop-action menu).
- Preferences UI: add the `uploads.subdir` field to the existing Uploads settings group; it live-applies (no reload), like the other upload settings.
- Settings wiring: add the default to `settings.py`, plus its `(group, key)` validation/range entry and help-text entry.

## Tests

- pytest (extend the upload coverage in `test_app.py`): default → `upload_target_dir` returns `<cwd>/.upload` and creates it; empty `subdir` → preserves today's cwd behavior; the returned/inserted path reflects the subdir; an unwritable subdir falls back to the cwd; the home-guard still holds.

## Docs

- README uploads section + `docs/GUI_SPEC.md`: note the new `.upload/` default and the `uploads.subdir` setting.

## Relationship

- Independent of DOIT.57 (the drag-into-terminal action menu) but complementary: DOIT.57's external-file drops upload, so they will land in `.upload/` once this ships. Either DOIT can ship first.

## Checklist

- [x] U1 — add `uploads.subdir` (default `.upload`) + range/help in `settings.py`; route `upload_target_dir` through it (mkdir `0o700`, fall back on failure, keep the home-guard); add the Preferences UI field; pytest for default / empty / fallback / inserted-path; note the default in README + GUI_SPEC.
  - **DONE:** `DEFAULT_UPLOAD_SUBDIR = ".upload"` in `common.py`; `uploads.subdir` default + help in `settings.py`, plus a `STRING_ALLOW_EMPTY` set so an empty value is a valid cwd opt-out (other blank strings still revert). `upload_target_dir` now splits into `_resolve_upload_base_dir` (the old resolution) + one `_apply_upload_subdir` owner that reads the setting, rejects absolute/`..` escapes, `mkdir 0o700`, and falls back to the bare dir on failure (home-guard preserved). Preferences field added (`80_panes_preferences.js`) + `pref.uploads.subdir.{label,help}` across all 13 locale catalogs. Tests: 4 `_apply_upload_subdir` cases (default/empty/escape/uncreatable) in `test_app.py` + 3 sanitize cases in `test_settings.py`. Full check green (564 pytest). Doc note: the setting is documented via Preferences help; README has no uploads-destination section and `GUI_SPEC.md` had unrelated in-flight edits, so the persistent-doc note is deferred to avoid an unrelated-change tangle.
