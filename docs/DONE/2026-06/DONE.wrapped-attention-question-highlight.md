# Wrapped attention question highlight

- Completed and removed `DOIT.question-highlight-wrap.md`. attention question highlighting now matches the triggering question sentence across wrapped terminal rows, paints one overlay segment per visual row, stops before unrelated text such as the explanatory parenthetical after `Want me ... chat?`, and uses a slightly stronger red fill/ring without adding a heavy left-edge block. Regression coverage now includes the existing single-line prompt, an explicit wrapped `Want me to draft...?` payload, a generic fallback wrapped prompt, and nearby non-question rows. Verification: `node tests/editor_preview.test.js` (`96 passed`), `python3 tools/static_build.py --check`, and full `python3 tools/check.py` (`CHECK PASSED in 54.94s`).

---

Completed 2026-06-22. Extracted from the 2026-06-22 daily log.
