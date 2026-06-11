# DOIT.57 — Drag-a-file-into-a-terminal "what should I do with it?" action menu

Goal: when a file is dragged/dropped onto a terminal pane, instead of silently inserting the bare path, offer a quick menu of context-aware actions (summarize, find errors in a log, analyze an image, review code, etc.). Selecting an action composes a prompt (for a Claude/Codex agent in that pane) or a shell command (for a plain shell) referencing the file, and sends/inserts it. "Insert path" stays as the default/escape so today's behavior is one keystroke away.

## Current behavior (what we change)

- Internal Finder/Differ drag onto a terminal: `installFilePathDropTarget` → on a center (non-edge) drop, `insertFileDragPayloadIntoTerminal(session, payload)` (`static_src/js/yolomux/99_terminal_boot.js:877`) shell-quotes the path(s) and calls `insertIntoTerminal(session, "<paths> ")`. Edge drops still open the file in a new editor split (`openDraggedFilesInEditor`), which we keep.
- External OS file drop: `panel` drop handler (`99_terminal_boot.js:868`) → `uploadFiles(session, dataTransfer.files)` uploads the bytes and inserts the uploaded path.
- Text reaches the PTY through `insertIntoTerminal(session, text)` → `item.socket.send({type:'input', data})` (`99_terminal_boot.js:1167`). This is the one channel an action uses to send a composed prompt/command.
- File references come from `terminalFileReferences(session, payload)` + `shellQuote`. Drop geometry comes from `dropIntentForEvent` (edge vs center).

The change is scoped to the center-drop-onto-terminal branch in those two handlers. Edge-drop-to-editor, Finder-to-Finder, and tab/pane drags are untouched.

## Proposed UX

- Center-drop a file (or files) onto a terminal pane → a small action menu opens at the drop point. The first item is **Insert path** (today's behavior); pressing Enter or clicking outside picks it, so nothing regresses. Type-ahead and arrow keys select; Esc cancels (and still inserts nothing).
- The menu is **context-aware**: items shown depend on (a) the detected file category (image / log / code / diff / data / doc / config / archive / dir / binary), (b) how many files, and (c) whether the terminal currently hosts an agent (Claude/Codex) versus a plain shell. An agent pane offers natural-language prompts; a plain shell offers shell commands (`file`, `wc -l`, `tail -f`, `jq`, `column -t`, …).
- Selecting an action **composes text and inserts it** into the terminal. By default it does NOT auto-press Enter — the user reviews then sends — controlled by a preference (see Settings). Read-only actions may opt into autorun; writes never do.
- A modifier can bypass the menu: plain drop = open menu; Shift/Alt-drop = old "just insert the path" (or invert via a preference `terminal.drop_opens_action_menu`, default on).

## Action catalog (brainstorm — pick the useful subset per phase)

Universal (any file):
- Insert path (default), Insert all paths (multi), Open in YOLOmux editor/viewer, Copy absolute path, Show info (size, type, mtime, line count), `cat`/preview head, Compute hash (`sha256sum`), Reveal in Finder.

Image (`.png .jpg .jpeg .gif .webp .svg .heic .bmp`):
- Describe the image, **Diagnose the error in this screenshot** (very common: user screenshots a stack trace / red UI), Extract the text (OCR), Extract code shown in the screenshot, Identify UI elements / describe the layout, Compare to a design/spec, Get dimensions + EXIF (`exiftool`/`identify`), Convert/resize, Find what's wrong/different vs another image (multi).

Log / console output (`.log`, `.txt`, `.out`, `.err`, journald dumps):
- **Find the errors/warnings**, Summarize what happened, **Find the root cause of the failure**, Extract stack traces, Build a timeline of events, Count/group error types, Find anomalies or latency spikes, Tail & watch (`tail -F`), Redact secrets before sharing, Correlate by timestamp/request-id, Suggest a fix for the top error.

Code (`.py .js .ts .rs .go .java .c .cpp .sh …`):
- Review for bugs, Explain what it does, **Find security issues**, Suggest a refactor, Write tests, Add docstrings/comments, Find performance hot spots, Summarize the public API, Trace a call graph, Lint/type-check (`ruff`, `tsc`, …), Find dead code.

Diff / patch (`.diff .patch`):
- Summarize the change, Review the diff for risks/regressions, Write a commit message / PR description, Explain the intent, Spot missing test coverage.

Structured data (`.csv .tsv .json .yaml .parquet .ndjson`):
- Summarize schema/columns, Describe stats (min/max/null %), Find anomalies/outliers, Validate against a schema, Convert format (`jq`, `csvjson`, `yq`), Answer a question about the data, Detect PII, Plot/chart, Show as a table (`column -t`).

Document (`.md .pdf .docx .rst .html`):
- Summarize, Extract action items / TODOs, Outline, Proofread / fix grammar, Translate, Answer questions, Convert to Markdown, Find broken links.

Config (`.toml .ini .env Dockerfile *.yaml k8s manifests`):
- Explain each setting, **Find misconfigurations / security issues**, Diff against defaults, Validate syntax, Suggest hardening.

Archive (`.zip .tar .tar.gz .whl`):
- List contents, Extract here, Summarize what's inside, Scan for risky files.

Directory:
- Summarize the tree, Find the largest files, Find recently changed files, Count files by type, `cd` into it, Git status (if a repo), Run a chosen command inside it.

Binary / unknown:
- `file` type, `hexdump | head`, `strings`, size + hash, "what is this file?".

Multi-file:
- Diff two files, Compare/contrast, Summarize all, Find differences, Batch-apply one action.

Cross-cutting:
- "Ask about this file…" (free-text prompt with the path pre-filled), Remember-last-action per category, User-defined custom actions (Settings).

## Implementation approaches

A. MVP — compose a prompt/command and send it to the pane (low effort, leverages the agent). The agent (Claude Code / Codex) can already read files (and images) by path, so an action is just a templated string sent through `insertIntoTerminal`. No server-side parsing needed. This is the recommended first phase.

B. Richer — YOLOmux runs the action itself server-side and shows the result in a panel (e.g. read the log + grep errors, or summarize via the existing Codex pipeline). Reuses `codex_exec_argv` / the YO!agent summary infra (`build_yoagent_chat_prompt`, `activity_summary`). Bigger build; do later for actions that benefit from a dedicated result view (image OCR, data stats, chart).

Hybrid: most actions are A (prompt to the agent). A few read-only shell ones (`file`, `wc`, `head`) insert a shell command. Server-side (B) is reserved for things the agent can't do well in-band.

## Architecture (one source of truth — per AGENTS.md shared-parent rule)

- One action registry, data-driven, consumed by the drop menu (and reusable by a command-palette "do something with file…" entry later):
  ```js
  // DROP_ACTIONS: the single list. No per-call-site forks.
  const DROP_ACTIONS = [
    {id, label, icon, category: 'image'|'log'|'code'|..|'any', minFiles, maxFiles,
     agentOnly: bool, readOnly: bool,
     kind: 'prompt' | 'shell' | 'app',
     template(refs, meta, agentKind) -> string,   // composed text
     autoEnterDefault: bool},
    ...
  ];
  function dropActionsFor(meta, agentKind) { return DROP_ACTIONS.filter(a => applies(a, meta, agentKind)); }
  ```
- `fileCategory(path, payload)` — one detector mapping extension/kind → category. EXTEND the existing extension→icon helper and `entryIsImageFile` rather than adding a parallel map (grep first: the `.md/.txt/.rst` doc mapping and image-extension checks in `40_file_explorer_files.js` / `45_file_explorer_actions.js`).
- Agent-awareness: reuse the session's agent kind (`claude` / `codex` / `term` from `AGENT_COMMANDS`) to choose prompt vs shell and to word the prompt; do not re-detect.
- The menu UI reuses the existing context-menu/popover parent (`appendContextMenuButton`, the `terminal-context-menu` CSS, popover positioning) — no new bespoke menu.
- Sending reuses `insertIntoTerminal` + `shellQuote`; add an `autoEnter` option that appends `\r` only when the action is read-only AND the autorun preference is on.

## Hook points

- Replace the body of `insertFileDragPayloadIntoTerminal(session, payload)` (`99_terminal_boot.js:877`) with: build `meta = fileMetaFor(payload)`, `actions = dropActionsFor(meta, agentKindFor(session))`, open the action menu at the drop point; on select, compose + insert. Keep "Insert path" as the default item calling the current `insertIntoTerminal(...paths...)` logic.
- External OS drop (`99_terminal_boot.js:868`): after `uploadFiles` resolves with the uploaded path, route through the same menu instead of the implicit insert.
- Center-vs-edge: only open the menu for center drops (`intent.zone === 'middle'` or no edge intent); edge drops keep `openDraggedFilesInEditor`.

## Settings / preferences (follow the existing settings pattern)

- `terminal.drop_opens_action_menu` (bool, default true) — drop opens the menu vs. always insert the path.
- `terminal.drop_action_autorun` (bool, default false) — auto-press Enter for read-only actions.
- `terminal.drop_default_action` (string, default `insert_path`) — which item is pre-selected.
- `terminal.drop_custom_actions` (list of `{label, category, template}`) — user-defined prompts.
- Add to `yolomux_lib/settings.py` (+ ranges/help), the Preferences UI, and the GUI_SPEC Finder/terminal section.

## Tests

- node `tests/layout_url.test.js`: `fileCategory()` mapping table; `dropActionsFor(meta, agentKind)` filtering (image-only items hidden for a log, agent-only items hidden for a plain shell, multi-file gating); `template()` output (correct path shell-quoting, prompt wording, autoEnter flag). Pin the menu item set per category so it can't silently drift.
- Selenium `tests/test_browser_layout.py`: a center-drop onto a terminal opens the menu; selecting an action inserts the composed text (and does not auto-send unless autorun is on); edge-drop still opens the editor; Shift-drop bypasses the menu.

## Safety / decisions for the user

- Never auto-execute a write/destructive command from a drop, even under YOLO; only read-only actions may autorun, and only when the preference is on. Prompts are inserted for review by default.
- Decide the default gesture: plain drop opens the menu (proposed) vs plain drop inserts the path and a modifier opens the menu.
- Decide whether to ship any server-side runners (approach B) in phase 1 or defer all to the agent (approach A).

## Related

- Upload destination — defaulting uploads into a `.upload/` subdir (Preferences `uploads.subdir`) is tracked separately in **DOIT.59**. Complementary: once it ships, this feature's external-file drops will land in `<cwd>/.upload/` automatically.

## Phased checklist

- [ ] P1 — MVP (approach A): action registry + `fileCategory` + agent-aware filtering; reuse the context-menu UI; wire the two drop hook points; "Insert path" default; insert-without-autorun. Categories: image, log, code, diff, data, doc, dir, any. node + Selenium tests. Preference `terminal.drop_opens_action_menu`.
- [ ] P2 — autorun preference for read-only actions; plain-shell command variants (`file`/`wc`/`tail -F`/`jq`/`column -t`); multi-file actions (diff two, summarize all); remember-last-action per category.
- [ ] P3 — user-defined custom actions in Settings; command-palette "do something with a file…" entry reusing `DROP_ACTIONS`.
- [ ] P4 — (optional) server-side runners + result panel (approach B) for OCR, data stats, charts, reusing the Codex/YO!agent summary pipeline.
