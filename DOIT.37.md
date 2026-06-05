# DOIT.37 — Refactor: repeated patterns, token centralization, JS helpers, CSS inheritance (2026-06-05)

REQUEST (user): study code carefully, look at repeated patterns, CSS/JS widget relationships that should inherit shared attributes/behaviors. Find patterns that repeat across API, CSS, JS. Refactor. Use best industry practices.

## Scope

Code audit across all `static_src/js/yolomux/*.js`, `static_src/css/yolomux/*.css`, and `yolomux_lib/*.py`. Items are ordered by impact and ease: violations of project rules first, then high-reuse helpers, then structural consolidation.

---

## Checklist

### A. Timing constant violations (AGENTS.md rule — backend poll = odd rounded-up, UI = round)

- [x] A1 — Fix backend poll defaults that use round numbers. DONE: 15001, 60001, 1253, 3001, 5003, 3001.

- [x] A2 — Fix UI timing that is not a round number. DONE: `remoteResizeDelayMs` → 200.

---

### B. Z-index token centralization

- [x] B1 — All z-index literals centralized as `--z-*` tokens in `00_tokens_base.css`; every `z-index:` in all CSS files now uses `var(--z-*)`. No raw literals remain. DONE.

---

### C. Shared CSS color tokens for repeated literal hex values

- [x] C1 — PR status color tokens (`--pr-status-failing`, `--pr-status-passing`, `--pr-status-merged`, `--pr-status-closed`) in `00_tokens_base.css`; `20_sessions_popovers.css` uses `var(--pr-status-*)`. DONE.

- [x] C2 — Transport-warning tokens (`--transport-warning-text`, `--transport-warning-bg`, `--transport-warning-border`) in `00_tokens_base.css`; `10_topbar_menus.css` uses `var(--transport-warning-*)`. DONE.

---

### D. `apiFetchJson` helper — eliminate 42 copy-pasted fetch+check blocks

- [x] D1 — `apiFetchJson` helper added to `10_core_utils.js` and used in `30_app_menus.js` (3 calls) and `90_changes_editor.js` (1 call). DONE: rolled out to `40_file_explorer_files.js`, `70_layout_actions.js`, `80_panes_preferences.js`, and `99_terminal_boot.js`; helper errors now preserve `status` and `payload` so callers keep 413/404 handling. Validated with `python3 tools/static_build.py --check`, `node --check static/yolomux.js`, and `node tests/layout_url.test.js`.

---

### E. `readStoredSet` / `readStoredMap` helpers — eliminate repeated localStorage parse pattern

- [x] E1 — `readStoredSet` and `readStoredJson` added to `10_core_utils.js`; `00_bootstrap_state.js` and `40_file_explorer_files.js` use them. DONE.

---

### F. Consolidated file-state map — replace 8 parallel Maps with one

- [x] F1 — Replace the 8 parallel Maps/Sets in `00_bootstrap_state.js` that all key by file path with a single `fileState` Map:
  - `openFiles` (line 225): `{mtime, size, kind, original, content, dirty, externalChanged, externalMissing}`
  - `fileEditorTabPaths` (line 226): → `fileState.get(path).editorTabItems` (Set of layout items)
  - `filePreviewTabPaths` (line 227): → `fileState.get(path).previewTabItems`
  - `openFileOwnerSessions` (line 228): → `fileState.get(path).ownerSessions`
  - `fileEditorViewMode` (line 247): → `fileState.get(path).viewMode`
  - `fileEditorImageMode` (line 249): → `fileState.get(path).imageMode`
  - `editorBlameByPath` (line 253): → `fileState.get(path).blame`
  - `fileEditorConflictDialogs` (line 265): → `fileState.get(path).conflictDialogOpen`

  Keep `openFiles` as an alias pointing at `fileState` during migration to avoid a flag-day rewrite; migrate call sites incrementally. DONE: `fileState` now owns editor tab items, preview tab items, owner sessions, per-item/path view modes, image mode, blame payload, and conflict-dialog state; `openFiles` is the compatibility alias; close/rename/delete flows use `deleteFileState` / `setFileState` rather than parallel map cleanup. Validated with `python3 tools/static_build.py --check`, `node --check static/yolomux.js`, and `node tests/layout_url.test.js`.

---

### G. Popover timing: stop defining the same values in both JS and CSS

- [x] G1 — `50_editor_settings_runtime.js` sets `--popover-show-delay` and `--popover-hide-delay` CSS variables via `root.setProperty(...)` on every settings apply. JS is now the source of truth; CSS values are initial-only fallbacks. DONE.

---

### H. `minSplitPaneWidthPx` — eliminate hardcoded layout-capacity constants

- [x] H1 — `minSplitPaneWidthPx` / `minSplitPaneHeightPx` named constants removed; pane configs now use `rootCssLengthPx('--min-split-pane-width') || 320` inline. `--min-split-pane-width: 320px` and `--min-split-pane-height: 220px` added to `00_tokens_base.css`. DONE.

---

### I. CSS shared button base — stop repeating the same inline-flex + reset pattern

- [x] I1 — At least 6 button variants (`actions button`, `.session-button-*`, `.info-refresh`, `.changes-repo-head`, `.control-button-*`, `.file-editor-toolbar button`) independently define the same base: `display:inline-flex; align-items:center; border:0; background:transparent; cursor:pointer`. DONE: added a combined shared base rule in `00_tokens_base.css` for action/info/changes/editor-toolbar copy-button controls and removed repeated reset declarations from concrete variants while leaving distinctive sizing/color/borders in their component rules. Skipped `.session-button-*` text spans because they are labels inside larger tab controls, not independent buttons. Validated with `python3 tools/static_build.py --check`, `python3 -m pytest tests/test_static_build.py -q`, `node --check static/yolomux.js`, and `node tests/layout_url.test.js`.

---

### J. Transcript meta normalizer — stop ad-hoc field chains at 10+ call sites

- [x] J1 — `sessionTranscriptInfo(session)` added to `10_core_utils.js`; used in `60_popovers_tabs.js`, `20_layout_state.js`, and `90_changes_editor.js`. DONE.

---

### K. Build-time lint: catch duplicate function definitions before they silently shadow

- [x] K1 — Add a check to `tools/static_build.py` (or a standalone `tools/lint_js.py`) that scans `static_src/js/yolomux/*.js` for top-level `function <name>` declarations that appear in more than one file. Run this as part of `--check`. DONE: `lint_duplicate_functions()` runs during `tools/static_build.py --check`, uses top-level imports, and has focused coverage for the clean tree plus an injected cross-file duplicate. Validated with `python3 -m pytest tests/test_static_build.py -q`, `python3 tools/static_build.py --check`, and `python3 -m py_compile tools/static_build.py`. One-liner prototype:
  ```bash
  grep -h "^function " static_src/js/yolomux/*.js | sort | uniq -d
  ```
  Any output = build failure.

---

### L. Python: TypedDict schemas for repeated API payload shapes

- [x] L1 — `yolomux_lib/types.py` created with `SessionFileEntry`, `RepoPayload`, `SessionFilesPayload` TypedDicts. DONE: corrected and completed `AutoApproveState` and `RunHistoryEntry` to match the live API payloads, added `AutoApproveStatusPayload` and `RunHistoryPayload`, and annotated the app/worker payload builders. Validated with `python3 -m py_compile yolomux_lib/types.py yolomux_lib/app.py yolomux_lib/auto_approve_worker.py` and `YOLOMUX_CONFIG_DIR=/tmp/yolomux-test-config python3 -m pytest tests/test_observability.py tests/test_app.py tests/test_auto_approve_worker.py -q`.

---

### M. Compact OS notification title

- [x] M1 — OS notifications currently use a verbose title: `YOLOmux - ${serverHostname}: ${sessionLabel(session)} ${state.label}` (`20_layout_state.js:2141`). This is too long for the OS notification banner. Compact it to `YOLOmux[session] message` format. DONE: added `compactNotificationTitle`, `sessionNotificationTitle`, and `hostNotificationTitle`; attention alerts, browser notifications, watched-PR notifications, terminal connection toasts, and `notify.testTitle` now use the compact bracket format. Validated with `python3 tools/static_build.py --check`, `node --check static/yolomux.js`, and `node tests/layout_url.test.js`.
  - **Title**: `YOLOmux[${sessionLabel(session)}] ${state.label}` — drops the `- ${serverHostname}:` segment (hostname is noise when you're the one running it; move to `body` if needed for multi-host setups).
  - **Body**: keep `state.reason · projectDirName(...)` as-is, optionally prepend `@ ${serverHostname}` if that info is wanted.
  - Same fix applies to the test notification (`20_layout_state.js:2072`) and the connect-notification variant (`20_layout_state.js:2215`): `YOLOmux - ${serverHostname}: ${message}` → `YOLOmux[${serverHostname}] ${message}` (or drop the host entirely for single-machine use).
  - Also update `showAttentionAlert` (`20_layout_state.js:2030`) which uses the same long format as the in-page toast title.
  - Locale key `notify.testTitle` should be updated to match.

### N. HTTPS redirect on TLS server

- [x] N1 — HTTP → HTTPS auto-redirect is implemented in `TmuxWebtermHTTPServer.get_request()`: plaintext requests on a TLS-enabled port receive `https_redirect_response(...)` before TLS wrapping, while TLS first bytes still use the SSL context. DONE: validated with `YOLOMUX_CONFIG_DIR=/tmp/yolomux-test-config python3 -m pytest tests/test_tls_config.py tests/test_server_query.py -q` (`20 passed`).

---

## Remaining (open items only)

No open items.
