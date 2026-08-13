# UI, Theme, Preferences, and Localization

## i18n untranslated-value lint and Chinese backfill
- Completed and removed `DOIT.i18n_untranslated_values_lint_and_backfill.md`.
- Static builds now report locale values that still equal `en.json`, with allowlists for intentionally identical brand/token strings and baseline regression errors so existing low-coverage locales warn without blocking while new regressions fail.
- `zh-Hans` and `zh-Hant` now have zero non-allowlisted untranslated entries; the cited `pref.uploads.custom_actions.label` and `.help` strings are translated in source and generated catalogs with `{path}`, `{qpath}`, `{paths}`, `{qpaths}`, `{name}`, `{count}`, and `{category}` preserved.
- Documentation now requires new locale keys to be translated rather than English-seeded, and `docs/TODO.md` records the locale-by-locale backfill path for low-coverage catalogs.
- Verification: `python3 -m pytest tests/test_static_build.py -q`, `python3 tools/static_build.py --check`, `python3 tools/check.py`, live 8001-served zh-Hant locale fetch, generated Preferences rendering under zh-Hant, and 8001 restart/ping all passed.

## CSS theme token cleanup
- Completed the remaining Refactor Audit Backlog row for repeated raw CSS hex colors. Inactive agent/YO markers now use shared text/fill/border tokens, YO!agent inactive backend dots share the same marker fill with their own border token, transcript role accents now use transcript-specific tokens, repeated light muted detail text uses `--text-muted-soft`, and the light submenu background uses its own surface token instead of a raw duplicate.
- Verification: targeted non-token CSS scan for `#f8fafc|#050608|#cfd8e3|#d9e2ee|#60a5fa|#4ade80|#f472b6|#667085` returned no matches, `python3 tools/static_build.py --check`, `node tests/editor_preview.test.js`, `node tests/tabber.test.js`, `git diff --check`, and final `python3 tools/check.py` passed (`CHECK PASSED in 48.52s`).

---

Completed 2026-06-20. Extracted from the 2026-06-20 daily log.
