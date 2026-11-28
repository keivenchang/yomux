# HTML ViewEdit implementation and verification plan

HTML ViewEdit edits source-backed text directly in the sandboxed rendering. A clicked text run or supported empty boundary opens one local editing host. The source editor owns the transaction, history, save and transport. When a browser suppresses callbacks inside the iframe, the application receives trusted input outside it and mirrors the caret and selection into the sandbox. Mouse selection stays native until release. Browser-generated structure and hidden content have no write authority.

## Implementation sequence

1. Match the current iframe and CodeMirror revision, then map visible DOM text to exact authored offsets. Accept authored text only when the rendered content and source projection agree. Track entity boundaries and reject cloned or ambiguous mappings.
2. Route rendered input through one source transaction. Patch the active rendered text locally for ordinary edits. Repaint and restore the source caret after structural changes. Reuse the same history in TextEdit and ViewEdit.
3. For normal text and cells, Enter and Shift+Enter insert an explicit `<br>`. Preformatted text inserts a literal newline. Clicking either side of an authored `<hr>` can start a new paragraph on that side. Character deletion protects adjacent structure. Replacement across separately mapped runs requires a deliberate multi-range implementation.
4. Keep the iframe script sandbox. Reject platform controls, custom native editing hosts, hidden or ambiguous ranges, stale buffers and expired write grants. A browser-provided IME composition stays local until it commits. Paste accepts plain text and escapes it as HTML. Source attributes, comments, and untouched entity spellings remain authoritative.
5. Verify exact source and rendered DOM before and after each native gesture, plus undo, save/reopen and concurrent revision checks. Add range-aware inline formatting and document structure commands only after they can preserve authored attributes and protected spans.

## Coverage matrix

| Input or structure | Positions and checks |
| --- | --- |
| Ordinary text, body-level text, headings, paragraphs, list items, cells and captions | Start, middle, end, empty and one-character runs. Insert, replace forward or reverse selection, Backspace, Delete, Enter, Shift+Enter, paste and cut. Verify caret, rendered text and exact unaffected source. Drag-selecting a complete or partial text run activates it for replacement and preserves selection direction. |
| Inline text boundaries, comments, links, bold and italic runs | Both sides, repeated text, and selection crossing a hidden comment or tag. Verify protected markup stays byte-identical and unsafe ranges cannot write. |
| `<hr>`, `<br>`, images and empty blocks | Immediately before and after, leading, trailing, sole or consecutive atoms, and adjacent text edges. Check no implicit atom deletion and valid insertion where mapped. |
| Tables and lists | Header/body cells, empty cells, first and final rows, nested lists, implicit closing tags, inserted tbody and fostered text. Preserve unrelated cells and tags. |
| Preformatted and rendered whitespace | Literal newlines, tabs, collapsed whitespace, spaces, wrapped visual lines and right-to-left text. Check that visual wrapping does not determine a source boundary. Activating or editing a text run preserves the authored whitespace layout. Up and Down move across wrapped lines, explicit line breaks, paragraphs, and table rows while remembering the preferred horizontal column. |
| Encodings | Named and numeric entities, entity interiors, non-BMP code points, combining sequences, emoji, carriage returns and final newline. Enforce exact mapped boundaries. |
| Unsafe and ambiguous content | Scripts, styles, SVG, controls, embedded frames, templates, authored contenteditable, hidden content, generated content, malformed or cloned markup. Exercise direct clicks and clipboard or browser input routes. |
| Browser-extension changes | Elements added, wrapped, or removed by an extension before or after load. Remaining elements pair with the source by tag, id, class, and own text, so only touched elements lose write authority; untouched text stays editable at exact offsets. |
| State and timing | Native IME, clipboard, Undo/Redo, save and reopen, View/Split transitions, stale or superseded renders, external saves, and permissions. Check immediate local response and settled content separately. Input parsing gets a 500 ms budget and repaint 20 ms, so a busy browser cannot drop the first click. A failed editor load retries after 3, 10, and 30 seconds. |
| Collaboration | Writable and read-only guests, simultaneous source changes, stale revisions, revocation, disconnect and reconnect. Check source operations, persistence and permission enforcement before enabling any additional rich commands. |
