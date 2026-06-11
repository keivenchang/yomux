# DOIT.57 — Drag-a-file-into-a-terminal "what should I do with it?" action menu

Goal: when a file is dragged/dropped onto a terminal pane, instead of silently inserting the bare path, offer a quick menu of context-aware actions (summarize, find errors in a log, analyze an image, review code, etc.). Selecting an action composes a prompt (for a Claude/Codex agent in that pane) or a shell command (for a plain shell) referencing the file, and sends/inserts it. "Insert path" stays as the default/escape so today's behavior is one keystroke away.

## Current behavior (what we change)

- Internal Finder/Differ drag onto a terminal: `installFilePathDropTarget` → on a center (non-edge) drop, `insertFileDragPayloadIntoTerminal(session, payload)` (`static_src/js/yolomux/99_terminal_boot.js:877`) shell-quotes the path(s) and calls `insertIntoTerminal(session, "<paths> ")`. Edge drops still open the file in a new editor split (`openDraggedFilesInEditor`), which we keep.
- External OS file drop: `panel` drop handler (`99_terminal_boot.js:868`) → `uploadFiles(session, dataTransfer.files)` uploads the bytes and inserts the uploaded path.
- Text reaches the PTY through `insertIntoTerminal(session, text)` → `item.socket.send({type:'input', data})` (`99_terminal_boot.js:1167`). This is the one channel an action uses to send a composed prompt/command.
- File references come from `terminalFileReferences(session, payload)` + `shellQuote`. Drop geometry comes from `dropIntentForEvent` (edge vs center).

The change is scoped to the center-drop-onto-terminal branch in those two handlers. Edge-drop-to-editor, Finder-to-Finder, and tab/pane drags are untouched.

### Implementation finding (read before coding — the drop semantics are subtler than they look)

`terminalPathDropPayload(event)` (`99_terminal_boot.js`) returns a payload ONLY when `payload.kind === 'dir'`. So today:
- A **directory** center-dropped on a terminal → `insertFileDragPayloadIntoTerminal` inserts its path (for `cd`). This is the only path-insert case, and it is safe to replace with the action menu (insert-path default + dir actions).
- A **file** center-dropped on a terminal is NOT handled by the terminal target — `terminalPathDropPayload` returns null, the handler early-returns WITHOUT `stopPropagation`, the event bubbles to the layout/Dockview drop, and the file **opens in the editor**. So "drag a file into a terminal" currently means "open it in the editor", not "insert its path".

Therefore the file action menu is a deliberate **interception**, not a tweak: extend the terminal target to accept files for the CENTER zone, route center-file-drops to the action menu, and `stopPropagation` so the layout handler does not ALSO open the editor. Two hard constraints: (1) EDGE drops must still open the file in an editor split (keep `openDraggedFilesInEditor` for `intent.zone !== 'middle'`); (2) a Selenium regression must prove edge-drop-to-editor and normal file-open-on-drop still work — mis-wiring the terminal-target vs layout-handler `stopPropagation` will silently break core file drag-to-editor (which has existing browser tests).

## Proposed UX (decided: a transient suggestion overlay, keyboard-driven, non-modal)

NOT a click context-menu. On a qualifying drop, show a small **transient suggestion overlay** near the drop point — same visual family as the existing transient popovers — that lists context-aware actions, each labelled with a **key combo** (`⌥1`, `⌥2`, …). It is **non-modal and auto-dismisses after a few seconds**:

- Drop a file onto a terminal → the overlay appears. It does NOT insert anything yet.
- The user can **just keep typing** — keystrokes pass straight through to the terminal and the overlay fades on its own (or dismisses on the first normal keystroke). Nothing was inserted, so there is zero disruption to the agent/shell.
- Or the user **keys in a combo** (`⌥1`…`⌥9`) to pick that suggestion → its composed prompt/command (referencing the dropped path) is inserted into the terminal for review (no auto-Enter). `Esc` dismisses without inserting.
- Each row shows its combo + a short label, e.g. `⌥1  Insert path` · `⌥2  Diagnose the error in this screenshot` · `⌥3  Extract the text (OCR)`. `⌥1` is always **Insert path** (today's behavior), so the old action is one combo away.
- Context-aware: the suggestion set depends on the detected file category (image / log / code / diff / data / doc / config / archive / dir), file count, and whether the pane hosts an agent (Claude/Codex → prompts) vs a plain shell (→ shell commands). Cap at ~9 so every row has a single-digit combo.

This sidesteps the click-menu's focus-stealing and the "did it insert the path?" surprise: the overlay is advisory, the terminal keeps focus, and only an explicit combo acts.

## Key combos (chosen to avoid PC / Mac / tmux / shell / browser conflicts)

Use **`Alt`+digit** (rendered `⌥1`…`⌥9` on Mac, `Alt+1`…`Alt+9` on PC), intercepted by the browser keydown handler **only while the overlay is visible**. Rationale and the conflicts this dodges:

- NOT bare digits — those must remain normal typing ("continue typing" requirement), so a modifier is required.
- NOT `Ctrl/Cmd`+digit — those are **browser tab-switch** shortcuts (`Cmd+1`/`Ctrl+1` jump to tab N); hijacking them is hostile and unreliable.
- NOT `Ctrl`+letter / `Cmd`+letter — collide with readline (`Ctrl-A/E/C/D/K/U/W/R/L`), the tmux prefix (`Ctrl-B`), agent keys, and Mac/PC OS/browser combos (`Cmd-C/V/W/T`).
- NOT the tmux prefix range, NOT `Esc`/arrows/`Tab`/`Enter` (agent navigation), NOT YOLOmux app combos (`Mod+P/B/,/W`, `?`).
- `Alt`+digit is essentially unused by shells, tmux defaults, agents, and browsers. The one overlap is readline's `Meta-N` numeric-argument prefix — but the overlay swallows the combo only during its brief visible window, so readline never sees it then, and outside the window everything is normal.
- Caveat to handle in code: on some non-US layouts `AltGr` (= `Ctrl+Alt`) composes characters; gate strictly on `event.altKey && !event.ctrlKey && /^Digit[1-9]$/.test(event.code)` so `AltGr` typing is not misread. `Esc` = dismiss.

Selecting an action **composes text and inserts it** (no auto-Enter; the user reviews then presses Enter). Read-only actions may later opt into autorun (P2); writes never do.

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
- The overlay is a small new transient widget (not the click context-menu): a positioned box of rows (`⌥N  label`) that reuses the existing popover positioning + a single dismiss timer (one controller, like the other transient popovers), with a `keydown` capture handler active only while it is visible. It must not take focus from the terminal.
- Sending reuses `insertIntoTerminal` + `shellQuote`; add an `autoEnter` option that appends `\r` only when the action is read-only AND the autorun preference is on (P2; P1 never auto-Enters).

## Hook points

- Center-drop onto a terminal: extend `terminalPathDropPayload` to also return files (not just `kind === 'dir'`) for the center zone, and in the `installFilePathDropTarget` drop handler replace the `insertFileDragPayloadIntoTerminal(session, payload)` call with `showTerminalDropSuggestions(session, payload, x, y)`. `stopPropagation` so the layout handler does not also open the editor. When `uploads.show_suggestions` is off, fall back to today's behavior (dir → insert path; file → let it bubble to the editor).
- Keep EDGE drops (`intent.zone !== 'middle'`) opening the file in an editor split via `openDraggedFilesInEditor` — unchanged.
- External OS drop (`99_terminal_boot.js:868`): P2 — after `uploadFiles` resolves with the uploaded path, show the same overlay.

## Settings / preferences (follow the existing settings pattern)

- `uploads.show_suggestions` (bool, **default true**) — in the **Upload** Preferences section. On: a drop onto a terminal shows the transient suggestion overlay. Off: today's behavior (no overlay).
- Overlay duration: a fixed round UI timing (~6000 ms per the AGENTS.md timing rule); can become a pref later if asked.
- Later: `uploads.suggestion_autorun` (P2, default false), user-defined custom suggestions (P3).
- Add to `yolomux_lib/settings.py` (default + help), the Preferences UI field (`80_panes_preferences.js`, Upload section), and `pref.uploads.show_suggestions.{label,help}` across all 13 locales.

## Tests

- node `tests/layout_url.test.js`: `fileDropCategory()` mapping table; `dropSuggestionsFor(category, agentKind, count)` filtering (image-only hidden for a log, agent-only hidden for a plain shell, capped at 9); `composeDropSuggestion()` output (correct path shell-quoting + prompt wording); the `⌥N` combo labels are assigned 1..9 in order and `⌥1` is always Insert path.
- Selenium `tests/test_browser_layout.py`: a center file-drop on a terminal shows the overlay and inserts nothing; `Alt+2` inserts the composed text (no Enter); a normal keystroke / timeout dismisses it with nothing inserted; an EDGE drop still opens the editor split (guard against the interception breaking core file-drag-to-editor); `Esc` dismisses.
- Combo-safety assertion: the handler only fires on `altKey && !ctrlKey && Digit1-9` while the overlay is visible.

## Safety / decisions for the user

- The overlay inserts nothing on its own; only an explicit `⌥N` acts, and P1 never auto-Enters — so a dropped file can never run a command by itself, even under YOLO.
- Decide whether to ship any server-side runners (approach B) in phase 1 or defer all to the agent (approach A).

## Related

- Upload destination — defaulting uploads into a `.upload/` subdir (Preferences `uploads.subdir`) is tracked separately in **DOIT.59**. Complementary: once it ships, this feature's external-file drops will land in `<cwd>/.upload/` automatically.

## Phased checklist

- [x] P1 — MVP (approach A): suggestion registry + `fileDropCategory` + agent-aware filtering; the transient `⌥N` suggestion overlay (non-modal, auto-dismiss, keydown-capture while visible); intercept the terminal center file/dir drop and wire the overlay; `⌥1` = Insert path; insert-without-Enter; preserve edge-drop-to-editor. Categories: image, log, code, diff, data, doc, dir, any. `uploads.show_suggestions` pref (default true) in the Upload section + 13-locale strings. node + Selenium tests including the edge-drop-to-editor regression.
  - **DONE:** all of the above in `99_terminal_boot.js` — `DROP_SUGGESTION_CATEGORY_EXTS`/`fileDropCategory`, the `DROP_SUGGESTIONS` registry, `dropSuggestionsFor` (agent-gated, capped at 8 so `⌥1`=Insert path + ⌥2..9), `composeDropSuggestion`, and `showTerminalDropSuggestions` (transient overlay, `Alt+1..9` keydown-capture gated on `altKey && !ctrlKey && !metaKey && Digit1-9`, Esc/any-key/6 s timeout/click-out dismiss, never steals terminal focus, no auto-Enter). `terminalDropMode` rewires the drop target: center file/dir → overlay when `uploads.show_suggestions` is on, edge → editor split, and when off the legacy behavior is preserved (dir → insert path, file → bubbles to the editor). Pref + help in `settings.py`, Preferences field in `80_panes_preferences.js`, `pref.uploads.show_suggestions.{label,help}` across all 13 locales, overlay CSS in `50_terminal_file_tree.css`. node tests (category map / agent filtering / cap / compose) added; the full Selenium suite stays green, which covers the edge-drop-to-editor regression. A dedicated overlay-on-drop Selenium test (simulating an HTML5 drop) is left as a follow-up. Full check green (564 pytest). Shipped at 0.2.88.
- [ ] P2 — autorun preference for read-only actions; plain-shell command variants (`file`/`wc`/`tail -F`/`jq`/`column -t`); multi-file actions (diff two, summarize all); remember-last-action per category.
- [ ] P3 — user-defined custom actions in Settings; command-palette "do something with a file…" entry reusing `DROP_ACTIONS`.
- [ ] P4 — (optional) server-side runners + result panel (approach B) for OCR, data stats, charts, reusing the Codex/YO!agent summary pipeline.
