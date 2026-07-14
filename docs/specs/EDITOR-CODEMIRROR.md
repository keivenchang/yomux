# Editor: CodeMirror vs textarea — research & 2-path spike (2026-05-30)

> **STATUS — RESOLVED & SHIPPED (2026-05-31):** CodeMirror 6 is now the only editable file editor. The old `editor.engine` setting, `?editor=` override, textarea/highlight overlay, and textarea fallback were removed. If CodeMirror fails to load, YOLOmux shows a read-only raw-text pane with an explicit error. This document is now historical research, kept for the rationale, the success gate, and the `@codemirror/merge` diff notes.

Original goal: get a real Find (Cmd/Ctrl+F, `1/13`, ^/v, jump+reveal) and the rest of the editor-options list. Before the CodeMirror work, the file editor was a raw `<textarea id="fileEditorTextarea">` plus a custom highlight overlay painted by highlight.js. The decision was to adopt CodeMirror 6 and delete the textarea editor layer.

CodeMirror 6 is a third-party editor library with upstream dependency notices recorded in [`../../THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md). It gives, out of the box: Find/Replace (`@codemirror/search`) with match count + all-match highlight, multiple carets, native soft-wrap + line-number gutter (this RETIRES #6), Lezer syntax highlighting, bracket matching, auto-close, code folding, go-to-line, comment-toggle, auto-indent, configurable tab width, line ops, undo/redo, read-only mode, viewport rendering for big files. Heavier Popular IDE editor engines need more runtime plumbing and web workers, so CM6 is the right size here.

## Current paste contract

- Image paste is owned by the focused surface before browser default paste can leak rich image data: a focused Markdown CodeMirror editor uploads through the editor upload route and inserts absolute Markdown image links, while a terminal-focused paste uploads through the session route and inserts terminal-safe `[Image #N] '/abs/path'` text.
- Editor image paste is Markdown-only: `.md` and `.markdown` editors receive `![image](/tmp/yolomux.<login-user>/uploads/editor/name.png)` references using the server-returned absolute central-upload path; non-Markdown editors, Finder, Preferences, YO!agent, and terminal focus continue through the existing terminal paste path when a terminal target exists. These links are intentionally temporary rather than document-relative.
- The editor and terminal paste paths share `dataTransferHasImagePayload` and `dataTransferImageFiles`; File items, File lists, image MIME items, multiple images, and extractable `text/html` data URLs are claimed and uploaded, and unextractable remote `<img>` HTML is still claimed with an error so raw image clipboard data never reaches an agent TUI.

## Two paths to prototype (behind one toggle, then compare)

Historical prototype plan: add an `editor.engine` setting / `?editor=` override with values `textarea` and `codemirror`, build both, compare against the success gate, and keep the winner. The shipped result kept CodeMirror and deleted the textarea engine.

### Path A — CodeMirror 6 (the bet)
Build a `CmFileEditor` adapter mounted in the same `#fileEditor` panel, wired to the existing file/dirty/save API (see "Adapter surface"). Reuse the existing Split-Preview: CM on the left, the current marked.js preview (`#fileEditorPreviewPane`) on the right. If it clears the gate, remove the textarea layer.

Loading at prototype time used one hand-written `yolomux.js` and CDN/vendor fallbacks for xterm/marked/highlight.js. The current app now has a no-dependency concat build for `static/yolomux.js` / `static/yolomux.css`, but CodeMirror itself is still a vendored prebuilt bundle. Two prototype sub-options:
- **A1 — import-map + ESM CDN.** `<script type="importmap">` mapping `codemirror` / `@codemirror/*` to esm.sh (or jsdelivr `+esm`), then `<script type="module">`. Zero local build; matches the current CDN pattern. Downside: runtime depends on the CDN (broken offline) unless cached.
- **A2 — vendored prebuilt bundle (recommended).** One-time `esbuild` of a tiny entry that re-exports only the CM pieces YOLOmux uses -> commit `static/codemirror.js`, served locally with a CDN fallback. This is EXACTLY how xterm.js is already handled (`web.py:86`, local + `onerror` jsdelivr fallback), keeps YOLOmux self-hostable offline, and is the consistent choice.

### Vendored CodeMirror bundle record
The rebuild manifest and exact lockfile live in `prototypes/codemirror-bundle/`. Rebuild with `cd prototypes/codemirror-bundle && npm ci && npm run build`. The committed `static/codemirror.js` currently has SHA256 `96f7c47927e35527d86f1b8a82d9f0e73d2a58ffb51005b436daa5a00b5ba081`.
Direct package versions recorded for the current bundle: `codemirror@6.0.2`, `@codemirror/autocomplete@6.20.2` (transitive via `codemirror` and language packages), `@codemirror/commands@6.10.3`, `@codemirror/lang-css@6.3.1`, `@codemirror/lang-html@6.4.11`, `@codemirror/lang-javascript@6.2.5`, `@codemirror/lang-json@6.0.2`, `@codemirror/lang-markdown@6.5.0`, `@codemirror/lang-python@6.2.1`, `@codemirror/lang-rust@6.0.2`, `@codemirror/lang-xml@6.1.0`, `@codemirror/lang-yaml@6.1.3`, `@codemirror/language@6.12.3`, `@codemirror/legacy-modes@6.5.3`, `@codemirror/lint@6.9.6` (transitive via JavaScript/codemirror), `@codemirror/merge@6.12.1`, `@codemirror/search@6.7.0`, `@codemirror/state@6.6.0`, and `@codemirror/view@6.43.0`. The lockfile also records the exact Lezer parser packages, `style-mod`, `crelt`, `w3c-keyname`, and `esbuild` versions with npm integrity hashes.

### Path B — extend the textarea (historical alternative, rejected)
Keep the `<textarea>` + overlay and hand-build Find + the editor-options list on it (`setSelectionRange` + scroll for jump/reveal; overlay rectangles for all-match highlight). Lower risk, no dep/build change. Ceiling: never get multi-cursor / folding / bracket-match, and #6 (the wrap/continuation overlay) stays a char-estimate hack.

## Success gate (CM wins only if ALL hold)
- Parity: open / edit / save (`Cmd/Ctrl+S`), dirty indicator + "changed on disk" reload, Edit/Preview/Split modes, split-scroll-sync, `appearance.editor_font_size` live, wrap toggle, line-numbers toggle, same language coloring set (md/sh/py/js/ts/rust/json/html/xml/css/toml/yaml), readonly-role read-only, URL-layout/tab integration, drag-file-row-to-terminal still works.
- New wins present: Find/Replace with `1/13` + ^/v, multi-cursor, bracket match, folding, and NO #6 misalignment.
- Non-functional: vendored bundle loads offline; bundle size acceptable; dark theme matches the UI; mobile/touch OK.

## What got removed when CM won (concrete)
- JS editor-layer functions (`static/yolomux.js`): `renderStandaloneSyntaxHighlight`, `renderSyntaxHighlight`, `renderSyntaxHighlightInto`, `editorVisualColumnCount`, `editorVisualLineFragments`, `applyEditorWrapToTextarea`, `highlightLanguageAvailable`, the wrap/gutter toggles' textarea plumbing, and the textarea-era `renderEditorForActive`. ~53 `fileEditorTextarea`/`fileEditorHighlight` refs collapse into the CM adapter.
  - Correction: `editorVisualHighlightHtml`, `simpleCodeSyntaxHtml`, and `syncFileEditorSplitScroll` were previously listed here as removed but are still defined+used in `95_codemirror_editor.js` (the editor module's CodeMirror half, split out of `90_changes_editor.js`); they power the raw/preview render + split-scroll. Only the textarea layer itself (`renderStandaloneSyntaxHighlight`, `fileEditorTextarea`) is gone.
- CSS (`static/yolomux.css`): the ~121 lines for `.file-editor-textarea`, `.file-editor-highlight`, `.syntax-highlighted`, `.editor-wrap`, the `--editor-line-number*` / `--editor-wrap-marker*` vars, and the visual-overlay grid. (CM owns gutter/wrap/highlight.)
- `web.py`: the `#fileEditorHighlight` `<pre><code>` + `#fileEditorTextarea` markup (`web.py:138-139`); the `#`/wrap toolbar buttons if CM commands replace them.
- KEEP: marked.js + `#fileEditorPreviewPane` (markdown Preview), the Edit/Preview/Split mode control, `editor_font_size`/`word_wrap`/`line_numbers` settings (now drive CM config). DECIDE on highlight.js: keep it only for the code-file Preview read view, or let a read-only CM render that too and drop highlight.js entirely.
- This change also retired the gutter/continuation real-wrap mapping bug (#6) and most of the TODO "More editor options" list.

## Adapter surface (what CM must hook into — keep the rest of the app unchanged)
- `openFiles` per-file state `{content, original, dirty, kind}`; the input -> `state.content` / dirty -> `setEditorStatus` path (~4966).
- `saveCurrentEditor` on `Cmd/Ctrl+S`.
- `renderFileEditorRawPane` (the textarea-era `renderEditorForActive` is gone) / view modes (`editorViewModeFor`, `setFileEditorViewMode`).
- Language from `fileExtensionOf`; `--editor-font-size` CSS var; readonly-role -> `EditorState.readOnly`.
- Split-Preview right pane stays `renderEditorPreviewPane` (marked).

## Diff & merge (CodeMirror `@codemirror/merge`) — diff AND edit at the same time
CM6 ships an official diff/merge package, `@codemirror/merge`, from the same ecosystem and same vendored-bundle loading path. Both views are REAL editors, so you view the diff and edit live in the same surface - which read-only diff libs (diff2html / jsdiff) can't do.
- **`unifiedMergeView` — single (inline/unified) diff.** Original shown as context (deleted chunks as widgets, insertions highlighted), per-chunk accept/reject gutter controls; the editor is editable (you edit the new version while the diff is shown).
- **`MergeView` — double (side-by-side) diff/merge.** Two editors; the `b` side editable by default (configurable so either/both are editable), with change connectors between panes, `revertControls: "a-to-b" | "b-to-a"` (chunk-copy arrows), `highlightChanges`, and `collapseUnchanged` (folds long unchanged regions for big files). Built-in Myers-style `diff` / `presentableDiff`.
- **YOLOmux uses:** editor buffer vs on-disk (the existing "changed on disk; unsaved edits kept" case -> show a merge view, edit, save); file vs git HEAD; compare any two files. This upgrades the TODO P3 "read-only unified diff panel" to an EDITABLE diff. Prefer unified on narrow panes, side-by-side in a wide/split pane.
- **Cost:** one more package in the vendored bundle; side-by-side needs horizontal room. The textarea path cannot do live diff+edit at all — so this is a CM-only win and a strong reason for Path A.
- **Diff band-extension MUST stay block-level only (soft-wrap gotcha, fixed 2026-06-17).** `@codemirror/merge` renders inserted/deleted text as INLINE marks (`<ins class="cm-insertedLine">` / `<del class="cm-deletedLine">`) nested inside the block line (`.cm-changedLine`). The full-bleed green/red band uses a `box-shadow: -100vw 0 0 …` + `clip-path: inset(0 -100vw)` trick to paint past the content box. That trick may be applied ONLY to block-level elements (`.cm-changedLine`, `.cm-deletedChunk`, `.cm-insertedChunk`, `.cm-inlineChangedLine`); applying it to the inline `cm-insertedLine`/`cm-deletedLine` marks clips/buries every soft-wrapped continuation row of a long added/changed line under the parent block's band, so the wrapped text renders BLANK (text is in the DOM but painted over). Reset `box-shadow: none; clip-path: none` on the inline marks. Regression: `tests/test_browser_layout.py::test_diff_wrapped_inserted_line_continuation_rows_show_text` asserts each wrapped continuation row has height > 0, non-empty caret text, and the inserted mark as the topmost painted element (with word-wrap on and `collapseUnchanged` active); a `tests/layout_url.test.js` guard pins the CSS contract.

## Risks
No-build project gaining a dep + (one-time) build for the vendored bundle; bundle size; offline load (mitigated by A2 vendoring); CM6 API ramp; matching the existing dark theme; making sure the URL-encoded layout/tab system and drag-to-terminal still behave.

## Recommendation
Path A with loader A2 shipped. CodeMirror is the only editor engine, and the textarea layer was deleted. Future editor work should build on the CodeMirror adapter and the vendored bundle rather than reviving a second editor path.

## Diff overview rail — rendering invariants (95_codemirror_editor.js)

The diff overview is the colored rail painted alongside the `unifiedMergeView` scroller (red removed bands, green added bands) as a single `linear-gradient`. `updateCodeMirrorDiffOverview(panel, container, state, currentText, original)` builds it. Two invariants, the first learned by reintroducing the exact bug a guard test exists for:

- **Never draw the rail while the scroller "looks current-only".** In a unified merge, deleted rows mount as block widgets *after* the initial layout; until they do, the scroller height reflects only the current side, so a rail computed then is miscomputed (it shows only the green/current portion — a wrong "temporary rail"). When `diffOverviewScrollLooksCurrentOnly(...)` is true the function must `return` without drawing, scheduling exactly one `scheduleDiffOverviewSettledRebuild` the first time (guarded by `_diffOverviewWaitingForDeletedRows`). The bug to avoid: making it fall through and draw on the second pass (when already waiting) repaints the temporary rail and fails the node guard test `t@2560` ("current-side-only CodeMirror geometry draws no temporary rail before the deleted widgets settle"). Once the widgets settle, the scroller no longer looks current-only and the next rebuild draws the correct red+green rail.
- **The rail rebuilds on geometry change.** A fold expand/collapse fires `heightChanged`; the overview rebuilds (debounced via rAF) against the current scroll surface, because the rail is positioned against live scroller geometry, not a one-time measurement.

## Diff overview test fixtures — virtualization and frozen-text conditions

The browser tests that assert the rail (`test_diff_overview_matches_actual_todo_codemirror_rows`, fixture `codemirror_todo_diff_overview_texts` / `codemirror_todo_diff_overview_fixture_html` in `tests/test_browser_layout.py`) depend on two conditions that are easy to get wrong:

- **CodeMirror virtualizes the deleted-row block widget by the position of the first changed current line.** If that line sits below the viewport, the deleted chunk never mounts and `deletedDomRows` reads 0 (the test then asserts `0 == removedRangeRows` and fails, or the rail never appears and the wait times out). A fixture that needs the deleted rows rendered must EITHER force `cm-content` `min-height` to the full diff height (`rows.totalRows * lineHeight`, so CodeMirror renders all rows) OR keep the changed region near the top of the editor (a tiny common prefix). The sibling file-explorer fixture proves a large deleted chunk renders fully (1986 rows) once its anchor is in view.
- **Freeze both sides of the fixture; never pin to a moving file.** The TODO fixture originally read the live `docs/TODO.md` as the B-side and asserted a single diff chunk; when that doc was rewritten the diff grew to many chunks and the test failed for a data reason, not a code regression. The fix routes both `original` and `current` through one helper (`codemirror_todo_diff_overview_texts`) that builds `current` as a deterministic single contiguous block replacement of the frozen `7f5a7e82ce:TODO.md` (stable common prefix + rewritten middle + stable common suffix). That keeps a realistic large diff while guaranteeing one stable chunk forever. Any fixture asserting diff shape must freeze its inputs the same way.
