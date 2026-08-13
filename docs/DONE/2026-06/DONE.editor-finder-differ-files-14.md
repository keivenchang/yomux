# Editor, Finder, Differ, and Files

## Diff/Differ view: wrapped continuation rows of long added/changed lines no longer render blank (DOIT.2)
- In the `@codemirror/merge` diff view with word-wrap on, a long inserted/changed line that soft-wrapped showed only its first visual row — the continuation rows rendered blank (text was in the DOM but painted over). Root cause: the full-bleed green/red change band (`box-shadow: -100vw 0 0 …` + `clip-path: inset(0 -100vw)`) was applied to the INLINE `cm-insertedLine`/`cm-deletedLine` marks, not only the block lines; on a soft-wrapped inline mark that clip buries every continuation row under the parent `.cm-changedLine` block's band. Fix in `static_src/css/yolomux/60_editor_file_panels.css`: keep the band trick on block-level elements only (`.cm-changedLine`, `.cm-deletedChunk`, `.cm-insertedChunk`, `.cm-inlineChangedLine`) and reset `box-shadow: none; clip-path: none` on the inline marks — the block still paints the full-width fill so the band is unchanged and the buried text returns. No JS change. The "Expand / collapse all unchanged lines" control was never floating (`position: static`, zero overlap); it only looked wrong because the rows were blank.
- Regression: Selenium `tests/test_browser_layout.py::test_diff_wrapped_inserted_line_continuation_rows_show_text` asserts each wrapped continuation row has height > 0, non-empty caret text, and the inserted mark as the topmost painted element (word-wrap on + `collapseUnchanged` active) — verified it FAILS on the old CSS and passes on the fix; plus a `tests/layout_url.test.js` CSS-contract guard. Contract documented in `docs/specs/EDITOR-CODEMIRROR.md` and `docs/specs/GUI.md`. DOIT.2 queued and fully drained.

---

Completed 2026-06-17. Extracted from the 2026-06-17 daily log.
