# UI, Theme, Preferences, and Localization

## DOIT.79 link context menu order and duplicate copy actions
- Completed and removed `DOIT.79.md`. Terminal and markdown link context menus now share one URL-menu helper in `static_src/js/yolomux/10_core_utils.js`, so `Open URL in a new tab` is the first row, URL selections that already equal the href no longer show a redundant generic `Copy`, and differing visible text is labeled explicitly as `Copy selected text`. The same localized labels now ship in every checked-in locale catalog.
- Regression coverage in `tests/layout_url.test.js` now inspects the rendered terminal context-menu rows for both cases from the queue: selected text equals href and selected text differs from href. README now documents the new right-click URL behavior under terminal copying.
- Verification: `python3 tools/static_build.py`, `node tests/layout_url.test.js`, full `python3 tools/check.py` (`CHECK PASSED in 47.79s`), and dev1 restart/smoke on port `8001` (`ping: 401 0.045859s`).

---

Completed 2026-06-16. Extracted from the 2026-06-16 daily log.
