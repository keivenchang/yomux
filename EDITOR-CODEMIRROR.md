# Editor: CodeMirror vs textarea — research & 2-path spike (2026-05-30)

> **STATUS — RESOLVED & SHIPPED (2026-05-31):** CodeMirror 6 is now the only editable file editor. The old `editor.engine` setting, `?editor=` override, textarea/highlight overlay, and textarea fallback were removed. If CodeMirror fails to load, YOLOmux shows a read-only raw-text pane with an explicit error. This document is now historical research, kept for the rationale, the success gate, and the `@codemirror/merge` diff notes.

Original goal: get a real Find (Cmd/Ctrl+F, `1/13`, ^/v, jump+reveal) and the rest of the editor-options list. Before the CodeMirror work, the file editor was a raw `<textarea id="fileEditorTextarea">` plus a custom highlight overlay painted by highlight.js. The decision was to adopt CodeMirror 6 and delete the textarea editor layer.

CodeMirror 6 is a third-party editor library with upstream dependency notices recorded in [`../../THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md). It gives, out of the box: Find/Replace (`@codemirror/search`) with match count + all-match highlight, multiple cursors, native soft-wrap + line-number gutter (this RETIRES #6), Lezer syntax highlighting, bracket matching, auto-close, code folding, go-to-line, comment-toggle, auto-indent, configurable tab width, line ops, undo/redo, read-only mode, viewport rendering for big files. Monaco (VS Code's editor) is the heavier alternative (much larger, needs web workers) — out of scope; CM6 is the right size.

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
Path A with loader A2 shipped. CodeMirror is the only editor engine, and the textarea layer was deleted. Future editor work should build on the CodeMirror adapter and the vendored bundle rather than reviving a second editor path.
