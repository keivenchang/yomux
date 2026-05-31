# Editor: CodeMirror vs textarea — research & 2-path spike (2026-05-30)

> **STATUS — RESOLVED & SHIPPED (2026-05-30):** CodeMirror 6 won. It's the DEFAULT editor engine (`editor.engine: codemirror`, `static/codemirror.js` vendored); the `<textarea>` stays as the `?editor=textarea` fallback (the "delete the textarea layer" decision resolved to KEEP-as-fallback). In-file Find, line numbers, wrap, split-preview, and dark/white theme landed. This document is now HISTORICAL — kept for the rationale, the gate, and the `@codemirror/merge` diff notes (which the AFCA "open the diff" UX still depends on). No open action here.

Goal: get a real Find (Cmd/Ctrl+F, `1/13`, ^/v, jump+reveal) and the rest of the editor-options list. Today the file editor is a raw `<textarea id="fileEditorTextarea">` (`web.py:139`) + a custom highlight overlay (`#fileEditorHighlight`, `web.py:138`) painted by highlight.js; there is NO editor library. Decide between extending the textarea vs adopting CodeMirror 6, and if CM works well, DELETE the textarea editor layer.

CodeMirror 6 is a third-party editor library with upstream dependency notices recorded in [`../../THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md). It gives, out of the box: Find/Replace (`@codemirror/search`) with match count + all-match highlight, multiple cursors, native soft-wrap + line-number gutter (this RETIRES #6), Lezer syntax highlighting, bracket matching, auto-close, code folding, go-to-line, comment-toggle, auto-indent, configurable tab width, line ops, undo/redo, read-only mode, viewport rendering for big files. Monaco (VS Code's editor) is the heavier alternative (much larger, needs web workers) — out of scope; CM6 is the right size.

## Two paths to prototype (behind one toggle, then compare)

Add an `editor.engine` setting / `?editor=` override with values `textarea` (current) and `codemirror`. Build both, compare against the success gate, keep the winner.

### Path A — CodeMirror 6 (the bet)
Build a `CmFileEditor` adapter mounted in the same `#fileEditor` panel, wired to the existing file/dirty/save API (see "Adapter surface"). Reuse the existing Split-Preview: CM on the left, the current marked.js preview (`#fileEditorPreviewPane`) on the right. If it clears the gate, remove the textarea layer.

Loading (this project has NO JS build step — it serves a single hand-written `yolomux.js` and loads xterm/marked/highlight.js from jsdelivr). Two sub-options:
- **A1 — import-map + ESM CDN.** `<script type="importmap">` mapping `codemirror` / `@codemirror/*` to esm.sh (or jsdelivr `+esm`), then `<script type="module">`. Zero local build; matches the current CDN pattern. Downside: runtime depends on the CDN (broken offline) unless cached.
- **A2 — vendored prebuilt bundle (recommended).** One-time `esbuild` of a tiny entry that re-exports only the CM pieces YOLOmux uses -> commit `static/codemirror.js`, served locally with a CDN fallback. This is EXACTLY how xterm.js is already handled (`web.py:86`, local + `onerror` jsdelivr fallback), keeps YOLOmux self-hostable offline, and is the consistent choice.

### Path B — extend the textarea (no new dependency)
Keep the `<textarea>` + overlay and hand-build Find + the editor-options list on it (`setSelectionRange` + scroll for jump/reveal; overlay rectangles for all-match highlight). Lower risk, no dep/build change. Ceiling: never get multi-cursor / folding / bracket-match, and #6 (the wrap/continuation overlay) stays a char-estimate hack.

## Success gate (CM wins only if ALL hold)
- Parity: open / edit / save (`Cmd/Ctrl+S`), dirty indicator + "changed on disk" reload, Edit/Preview/Split modes, split-scroll-sync, `appearance.editor_font_size` live, wrap toggle, line-numbers toggle, same language coloring set (md/sh/py/js/ts/rust/json/html/xml/css/toml/yaml), readonly-role read-only, URL-layout/tab integration, drag-file-row-to-terminal still works.
- New wins present: Find/Replace with `1/13` + ^/v, multi-cursor, bracket match, folding, and NO #6 misalignment.
- Non-functional: vendored bundle loads offline; bundle size acceptable; dark theme matches the UI; mobile/touch OK.

## What gets REMOVED when CM wins (concrete)
- JS editor-layer functions (`static/yolomux.js`): `renderStandaloneSyntaxHighlight`, `renderSyntaxHighlight`, `renderSyntaxHighlightInto`, `editorVisualHighlightHtml`, `editorVisualColumnCount`, `editorVisualLineFragments`, `applyEditorWrapToTextarea`, `simpleCodeSyntaxHtml`, `highlightLanguageAvailable`, `syncFileEditorSplitScroll`, the wrap/gutter toggles' textarea plumbing, and most of `renderEditorForActive` (~4842-9734). ~53 `fileEditorTextarea`/`fileEditorHighlight` refs collapse into the CM adapter.
- CSS (`static/yolomux.css`): the ~121 lines for `.file-editor-textarea`, `.file-editor-highlight`, `.syntax-highlighted`, `.editor-wrap`, the `--editor-line-number*` / `--editor-wrap-marker*` vars, and the visual-overlay grid. (CM owns gutter/wrap/highlight.)
- `web.py`: the `#fileEditorHighlight` `<pre><code>` + `#fileEditorTextarea` markup (`web.py:138-139`); the `#`/wrap toolbar buttons if CM commands replace them.
- KEEP: marked.js + `#fileEditorPreviewPane` (markdown Preview), the Edit/Preview/Split mode control, `editor_font_size`/`word_wrap`/`line_numbers` settings (now drive CM config). DECIDE on highlight.js: keep it only for the code-file Preview read view, or let a read-only CM render that too and drop highlight.js entirely.
- This change also CLOSES DOIT.5 #6 (gutter/continuation real-wrap mapping) and most of the TODO "More editor options" list.

## Adapter surface (what CM must hook into — keep the rest of the app unchanged)
- `openFiles` per-file state `{content, original, dirty, kind}`; the input -> `state.content` / dirty -> `setEditorStatus` path (~4966).
- `saveCurrentEditor` on `Cmd/Ctrl+S`.
- `renderEditorForActive` / view modes (`editorViewModeFor`, `setFileEditorViewMode`).
- Language from `fileExtensionOf`; `--editor-font-size` CSS var; readonly-role -> `EditorState.readOnly`.
- Split-Preview right pane stays `renderEditorPreviewPane` (marked).

## Diff & merge (CodeMirror `@codemirror/merge`) — diff AND edit at the same time
CM6 ships an official diff/merge package, `@codemirror/merge`, from the same ecosystem and same vendored-bundle loading path. Both views are REAL editors, so you view the diff and edit live in the same surface - which read-only diff libs (diff2html / jsdiff) can't do.
- **`unifiedMergeView` — single (inline/unified) diff.** Original shown as context (deleted chunks as widgets, insertions highlighted), per-chunk accept/reject gutter controls; the editor is editable (you edit the new version while the diff is shown).
- **`MergeView` — double (side-by-side) diff/merge.** Two editors; the `b` side editable by default (configurable so either/both are editable), with change connectors between panes, `revertControls: "a-to-b" | "b-to-a"` (chunk-copy arrows), `highlightChanges`, and `collapseUnchanged` (folds long unchanged regions for big files). Built-in Myers-style `diff` / `presentableDiff`.
- **YOLOmux uses:** editor buffer vs on-disk (the existing "changed on disk; unsaved edits kept" case -> show a merge view, edit, save); file vs git HEAD; compare any two files. This upgrades the TODO P3 "read-only unified diff panel" to an EDITABLE diff. Prefer unified on narrow panes, side-by-side in a wide/split pane.
- **Cost:** one more package in the vendored bundle; side-by-side needs horizontal room. The textarea path cannot do live diff+edit at all — so this is a CM-only win and a strong reason for Path A.

## Risks
No-build project gaining a dep + (one-time) build for the vendored bundle; bundle size; offline load (mitigated by A2 vendoring); CM6 API ramp; matching the existing dark theme; making sure the URL-encoded layout/tab system and drag-to-terminal still behave.

## Recommendation
Prototype Path A with loader A2 behind `editor.engine=codemirror`, keep Path B as the fallback. If A clears the gate, switch the default and delete the textarea layer above. Suggest this becomes a DOIT once you approve the direction.
