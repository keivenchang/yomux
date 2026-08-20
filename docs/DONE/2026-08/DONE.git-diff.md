# DONE.git-diff.md - Finder Git Diff Viewer

## Status

Completed on 2026-08-17 PT after Keiven approved the mocks and invoked `workqueue-run DOIT.git-diff.md`. The approved interaction contract, mocks, implementation evidence, and completion measurements are retained together in this archive; the implementation identity is `221e232e5cdd61ac8507f357b228e092caa6b118` on YOLOmux 0.7.8.

## Goal

Add Finder right-click actions that match the selected entry. A Git-backed directory shows `Diff repo` and opens a new repository-history tab; a file shows `Edit in new tab`, `Preview in new tab`, and `Diff in new tab`, with all three actions targeting the same normal file tab and selecting a mode inside it. Expanding a commit reveals its full message and a tree of changed files with added and removed line counts, and selecting a changed file opens one normal Editor/Preview/Diff tab with Diff selected for that exact commit.

## User Contract

- A single selected directory shows exactly `Diff repo` when `/api/fs/info` proves the path belongs to an allowed Git repository. It is absent for multi-selection or a proven non-repository path and disabled with an accessible reason when verification or read authorization fails.
- A single selected file shows exactly `Edit in new tab`, `Preview in new tab`, and `Diff in new tab` in that order. Edit and Preview follow normal readable-file eligibility; Diff additionally requires verified Git metadata and a diff-capable file state, with an accessible disabled reason when it is unavailable.
- All three file actions create or activate one canonical working-tree file tab for that physical file, then select `edit`, `preview`, or `diff` inside that tab. Editor, Preview, and Diff are modes of the same tab, never separate tab identities; changing mode preserves unsaved content and the tab's placement/history.
- The file actions reuse the existing editor placement rule, file-state owner, Preview renderer, CodeMirror editor/MergeView renderer, FROM/TO picker, and loading/error states; they do not create another editor, preview, or diff renderer.
- `Diff repo` opens a Generic Pane tab labeled `Diff repo · <repo>`. Selecting the repository root shows repository history; selecting a nested directory shows commits and changed files restricted to that directory.
- Commit history is newest first. Each commit summary is one line whose visual order is `SHA → date/time → files/+lines/-lines → author → description`. Responsive retention priority is `SHA` always, then description, changes, date/time, and author; lower-priority fields disappear before higher-priority fields when width is constrained.
- Expanding a commit loads and retains the full commit message plus a directory tree of changed files. Each file row shows status and `+lines`/`-lines`; binary files show `binary`, renames show `old → new`, and deleted files remain selectable.
- Selecting a commit file opens another instance of the current Editor tab type with Diff selected and pins FROM to the selected commit's first parent and TO to the selected commit. Preview renders the immutable TO-side commit content; Edit is disabled with a read-only historical-state reason. A root commit compares the empty tree to the commit. A merge commit uses its first parent in v1 and labels that choice.
- Reopening the same directory history activates its existing tab in its current pane. Repeating any of the three Finder file actions activates the same canonical working-tree file tab and switches only its selected mode. Reopening the same exact historical file/path/FROM/TO identity activates that ref-pinned file tab; a different historical ref pair creates a different historical tab.
- Directory history is read-only. It never stages, unstages, commits, checks out, resets, applies, reverts, deletes, renames, fetches, or contacts a remote.

## Mock Approval Gate

- [x] Review and approve or revise the context-menu, directory-history, and file-diff mocks below. DONE 2026-08-17 PT: Keiven revised the action labels, shared current-Editor identity, and commit-row layout, then explicitly invoked `workqueue-run DOIT.git-diff.md`; implementation is authorized from this approved queue revision.

### Mock A - Finder Context Menu

```text
Finder
┌──────────────────────────────────────────────┐
│ ▾ yolomux.dev7773/                    main  │
│   ▸ static_src/                              │
│   ▸ yolomux_lib/                             │
│     README.md                                │
│                                              │
│ Right-click: yolomux.dev7773/                │
│                    ┌───────────────────────┐ │
│                    │ Diff repo            │ │
│                    ├───────────────────────┤ │
│                    │ Copy relative path   │ │
│                    │ Copy full path       │ │
│                    │ Zip & download       │ │
│                    │ Exclude from index   │ │
│                    │ Rename               │ │
│                    │ Delete               │ │
│                    └───────────────────────┘ │
└──────────────────────────────────────────────┘

Right-click: static_src/js/yolomux/45_file_explorer_actions.js
                    ┌───────────────────────┐
                    │ Edit in new tab       │
                    │ Preview in new tab    │
                    │ Diff in new tab       │
                    ├───────────────────────┤
                    │ Copy relative path   │
                    │ Copy full path       │
                    │ Download             │
                    │ Rename               │
                    │ Delete               │
                    └───────────────────────┘
```

Mock behavior:

- `Diff repo` is the directory action and appears before copy/download/mutation actions only for one Git-backed directory.
- A file shows `Edit in new tab`, `Preview in new tab`, and `Diff in new tab` in that order before copy/download/mutation actions. They all create or activate the same file tab; the selected action only chooses its initial/current Editor, Preview, or Diff mode.
- The menu waits for the existing bounded path-info result. Multi-selection or a directory proven to be outside a Git repo omits `Diff repo`; an unverified/blocked path or a role that cannot read filesystem content renders the applicable action disabled with an accessible reason instead of starting a failing request.
- These actions are Finder-scoped in v1. Differ and Tabber keep their existing menus unless a later approved revision broadens them.

### Mock B - Directory Diff Repo Tab

```text
[ Terminal ] [ Diff repo · yolomux.dev7773 × ]
┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ Diff repo                                                                                                            │
│ ~/dev/yolomux.dev7773                                                        dev/0.7.7-7773   Refresh               │
│ Scope: repository root                                                       50 newest commits                       │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ ▾ 0da574142  Aug 15, 2026 10:54 AM PT  3 files +42 -11    Keiven Chang  Record YOLOmux 0.7.7 release evidence      │
│   ┌────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐ │
│   │ Record YOLOmux 0.7.7 release evidence                                                                           │ │
│   │                                                                                                                  │ │
│   │ Preserve the final gate, browser acceptance, and release identity.                                              │ │
│   └────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘ │
│   ▾ docs/                                                                                                            │
│       M  releases/0.7.7.md                                                                                +24  -2   │
│   ▾ static_src/js/yolomux/                                                                                            │
│       M  45_file_explorer_actions.js                                                                      +16  -9   │
│       R  old_name.js → new_name.js                                                                         +2  -0   │
│                                                                                                                      │
│ ▸ fb58cac56  Aug 14, 2026 4:18 PM PT   22 files +381 -94  Keiven Chang  Release YOLOmux 0.7.7                      │
│ ▸ 89bf6be95  Aug 13, 2026 2:07 PM PT    4 files +88 -3     Keiven Chang  Add 0.7.5 and 0.7.6 release evidence      │
│ ▸ 20123ae33  Aug 12, 2026 11:31 AM PT   2 files +19 -6     Keiven Chang  Close v0.7.6 release                      │
│                                                                                                                      │
│                                                  [ Load older commits ]                                              │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Mock behavior:

- The commit row is the disclosure owner. Clicking its arrow, pressing Left/Right, or pressing Enter while the disclosure control is focused collapses/expands it; clicking a changed file opens that file's historical Diff tab.
- At sufficient width, every commit summary remains one line in the exact order `SHA, date/time, files/+/- changes, author, description`. When space shrinks, retention priority is `SHA` always, then description, changes, date/time, and author; author disappears first, while the description may truncate only after lower-priority fields are removed.
- Multiple commits may remain expanded. Expansion state, scroll position, loaded details, and the exact history HEAD/cursor survive tab switches, relocalization, panel moves, and unrelated Finder refreshes for the life of the tab.
- `Refresh` starts a new immutable history snapshot at the repository's current HEAD. `Load older commits` paginates against that frozen HEAD so a new commit cannot duplicate or skip rows mid-list.
- The full message preserves paragraphs but is bounded and rendered as text, never trusted HTML or Markdown.
- A nested-directory tab replaces `Scope: repository root` with the Git-relative directory and filters both commits and file rows to that path.

### Mock C - Changed Commit File Opens The Current Editor Tab Type With Diff Selected

```text
[ Diff repo · yolomux.dev7773 ] [ 45_file_explorer_actions.js × ]
┌──────────────────────────────────────────────────────────────────────────────┐
│ ~/dev/yolomux.dev7773/static_src/js/yolomux/45_file_explorer_actions.js     │
│ [ Edit (read-only) ] [ Preview ] [ Diff ]             #  Wrap  Expand        │
│                      inactive     selected                                    │
│ FROM  fb58cac56 (parent)                         TO  0da574142 (commit)      │
├───────────────────────────────────┬──────────────────────────────────────────┤
│ fb58cac56                         │ 0da574142                                │
│ 121  const openActions = ...      │ 121  const openActions = ...             │
│ 122- appendContextMenuButton(...) │ 122+ appendContextMenuButton(...)        │
│                                   │ 123+ appendGitDiffViewAction(...)         │
│ 123  ...                          │ 124  ...                                  │
└───────────────────────────────────┴──────────────────────────────────────────┘
```

Mock behavior:

- This is another instance of the current Editor tab type and existing CodeMirror Diff renderer, not a new file-tab type, separate Editor/Preview/Diff tabs, or a commit-specific editor implementation.
- The first open for one historical path/FROM/TO identity creates the Editor tab with Diff selected. Repeating the same open activates that exact tab; Preview renders the immutable TO-side content, Edit stays disabled/read-only, and a different file or commit/ref pair creates another historical Editor tab.
- The file tab carries explicit immutable FROM/TO refs from the commit row. The directory tab remains open and Back returns to it through normal tab navigation.
- Deleted and renamed historical paths may have no working-tree file. The Git payload remains authoritative and must still render both historical sides or a typed unavailable state.

## Draft Decisions To Revise With The Mock

- History page size: 50 commits, newest first, followed by `Load older commits`.
- Directory scope: repo root means all paths; a nested selected directory means path-filtered history.
- Disclosure: multiple commits may remain expanded; expansion is lazy and cached per exact commit SHA.
- Commit summary layout: one line in `SHA → date/time → files/+/- → author → description` order, with responsive retention priority `SHA → description → changes → date/time → author`.
- Commit comparison: first parent to commit; root commit uses Git's empty-tree SHA; merge rows say `first parent`.
- Finder file-tab behavior: `Edit in new tab`, `Preview in new tab`, and `Diff in new tab` converge on one canonical working-tree file tab and select only its mode; they never create three mode-specific tabs.
- Historical file-tab behavior: a changed-file click creates or activates another instance of the current Editor tab type keyed by canonical file identity plus exact FROM/TO refs; Diff is selected, Preview renders immutable TO-side content, Edit is disabled/read-only, and a different historical ref pair remains separate from that tab and from the canonical working-tree file tab.
- Working-tree changes are not a synthetic commit row in v1. Finder `Diff in new tab` uses the existing `HEAD → current` behavior inside the same working-tree file tab used by Edit and Preview.
- Commit details show raw text message paragraphs. No Markdown rendering, comments, signatures, branches, tags, avatars, remote links, graph lanes, blame, or patch actions are added in v1.

## Existing Owners To Reuse

- `static_src/js/yolomux/45_file_explorer_actions.js::showFileTreeContextMenu` owns Finder row context menus, path-info lookup, single/multi-selection state, and action ordering.
- `static_src/js/yolomux/45_file_explorer_actions.js::openFileInAdditionalEditorTab` plus `openFileInEditor` owns new-tab placement, physical-file identity convergence, and initial `viewMode` selection for working-tree files.
- `static_src/js/yolomux/86_changes_editor.js::openChangedFileInDiff` plus `enterFileEditorDiffMode` owns historical changed-file opening, explicit ref plumbing, fallback behavior, target-pane placement, and Diff mode entry.
- `static_src/js/yolomux/45_file_explorer_actions.js::refreshOpenFileDiff` and `applyOpenFileDiffPayload` own `/api/fs/diff` loading and file-state convergence.
- `static_src/js/yolomux/92_codemirror_editor.js::ensureCodeMirrorDiffPanel` owns the actual unified/side-by-side CodeMirror Diff rendering.
- `static_src/js/yolomux/00_bootstrap_state.js::TAB_TYPES` owns dynamic tab identity, placement, labels, rendering, cleanup, relocalization, and search capability.
- `static_src/js/yolomux/86_changes_editor.js::renderChangesRoot` and the shared `TreeRowViewModel`/`patchTreeRow` path own tree disclosure, file status, icons, dates, selection, and `+N/-N` metadata geometry.
- `yolomux_lib/filesystem/git_ops.py` owns descriptor-pinned Git execution, ref validation, file history, repo metadata, blob reads, and diff construction.
- `yolomux_lib/filesystem/__init__.py`, `yolomux_lib/infra/jobd.py`, `yolomux_lib/app.py::filesystem_operation_http_payload`, `yolomux_lib/server.py::FilesystemHttpAdapter`, and `yolomux_lib/http_routes.py::FILESYSTEM_ROUTES` form the existing authorized, queued, retained filesystem-read pipeline.

## API Contract

### Commit History

`GET /api/fs/git-history?path=<absolute-path>&limit=50&cursor=<opaque-cursor>` returns one frozen, bounded history page:

```json
{
  "path": "/repo/subdir",
  "repo": "/repo",
  "relative_path": "subdir",
  "head": "<full-sha>",
  "commits": [
    {
      "sha": "<full-sha>",
      "short": "0da574142",
      "parents": ["<full-sha>"],
      "subject": "Record release evidence",
      "author": "Keiven Chang",
      "authored_at": 1786816440,
      "files": 3,
      "added": 42,
      "removed": 11,
      "binary_files": 0
    }
  ],
  "next_cursor": "<opaque-cursor-or-empty>",
  "truncated": false
}
```

### Commit Detail

`GET /api/fs/git-commit?path=<absolute-path>&commit=<full-sha>&head=<frozen-head>` returns one bounded commit expansion:

```json
{
  "repo": "/repo",
  "scope_path": "subdir",
  "sha": "<full-sha>",
  "parents": ["<full-sha>"],
  "from_ref": "<first-parent-or-empty-tree>",
  "to_ref": "<full-sha>",
  "subject": "Record release evidence",
  "message": "Record release evidence\n\nPreserve the final evidence.",
  "files": [
    {"status": "M", "path": "subdir/app.py", "old_path": "", "added": 16, "removed": 9, "binary": false}
  ],
  "truncated": false
}
```

Contract rules:

- Both history routes accept the authorized existing directory selected by `Diff repo`, resolve and pin its repository through the existing safe-path descriptor owner, and reject files, paths outside allowed roots, repo-boundary changes, special files, unverified symlink escapes, unknown commits, malformed cursors, and rewritten/stale snapshots with typed errors. File actions continue through the existing file-read and diff operations.
- The history cursor is opaque and bound to canonical repo identity, selected relative scope, frozen HEAD, ordering, and page position. A cursor cannot be replayed for another path/repo/HEAD.
- History and detail parsing use NUL/record separators that preserve spaces, tabs, newlines, Unicode, renames, copies, binary markers, mode-only changes, root commits, and merge parents. Line-oriented split-on-tab parsing is not acceptable.
- History performs a bounded constant number of Git subprocesses per page, and one expansion performs a bounded constant number per commit. There is no Git process per row or per file.
- `limit` is clamped to 1-50. Full messages, file lists, aggregate counts, JSON bytes, Git runtime, and stderr are bounded through existing filesystem read/deadline/error conventions; a truncated result says what was truncated instead of presenting partial data as complete.
- Both operations use jobd's existing unbounded/interactive filesystem lane, retained-product envelope, generation fence, queue/pending/error delivery, and user/auth scope. They do not run Git on the request thread and do not take the point-read lane.
- Commit-file clicks continue to use `/api/fs/diff` with the detail payload's exact `from_ref`/`to_ref`; no second historical-diff endpoint is added.

## Implementation Plan

- [x] Freeze the approved interaction contract in `docs/specs/GUI.md` before product code: exact `Diff repo` and `Edit/Preview/Diff in new tab` eligibility/action order, one working-tree file-tab identity across its three modes, directory tab identity and Generic Pane placement, history scope, disclosure behavior, commit/file row metadata, historical file-tab ref semantics, loading/empty/error/truncated states, keyboard/touch behavior, and explicit non-goals. DONE 2026-08-17 PT: added the authoritative Finder/`Diff repo`/current-Editor contract at `docs/specs/GUI.md:303`, including frozen pagination, responsive row order/priority, immutable historical Preview, disabled Edit, explicit failures, and read-only non-goals; `git diff --check -- docs/specs/GUI.md` passed and the focused contract search found every approved label and invariant.
- [x] Add failing-first backend fixtures for repository-root and nested-directory history, lazy commit detail, root/merge commits, pagination frozen to HEAD, binary/mode-only/rename/copy/delete entries, hostile filenames, unknown/stale cursors, rewritten refs, repo/path replacement, blocked/symlink paths, permission failures, timeouts, payload caps, and constant Git-subprocess counts; capture the exact red nodes before implementation. DONE 2026-08-17 PT: added the shared history fixture in `tests/mock_git_repo.py` and four focused nodes in `tests/test_filesystem.py`; the exact pre-implementation run collected four failures solely at the missing `filesystem.git_history`/`filesystem.git_commit` entrypoints, with raw output in `/tmp/yolomux-git-history-red-final.txt`, and `git diff --check` passed for both test files.
- [x] Add one descriptor-pinned Git history owner in `yolomux_lib/filesystem/git_ops.py`, export it once through `yolomux_lib/filesystem/__init__.py`, and reuse that owner for list and detail primitives without duplicating repo discovery, safe-path validation, ref parsing, subprocess wrappers, error normalization, or numstat/status parsing. DONE 2026-08-17 PT: implemented one `_pinned_git_history_scope` owner for SHA-1/SHA-256 repositories, immutable object/ref/control snapshots, bounded construction/retirement deadlines, frozen cursors/shallow state, strict machine-delimited parsing, and typed stale/unsupported/truncated failures; the current focused selection passed 91 tests, the full filesystem module passed 212 tests, linked-worktree and SHA-256 smokes returned pinned history, compileall exited 0, focused Ruff passed, differential Ruff added no findings against HEAD, and `git diff --check` exited 0.
- [x] Extend the existing retained filesystem-operation pipeline with `git_history` and `git_commit`: add route registry entries, adapter query validation, jobd dispatch, operation/refusal allowlists, authorization scope, generation/product keys, interactive-lane classification, pending completion, and typed API envelopes; negative-search for any direct request-thread Git call or parallel route allowlist. DONE 2026-08-17 PT: routed both operations through the existing authorized retained filesystem lane across app, jobd, server adapter, and route registry with fresh-only generation/product identities and typed validation/refusal behavior; the current six-module isolated gate passed 986 tests with `PytestUnhandledThreadExceptionWarning` promoted to an error. The earlier `yolomux-indexed-repos` warning did not reproduce in the named two-node test, its immediate-predecessor three-node run, or this integrated rerun; HEAD inspection pins its unrelated `time.sleep` worker lifecycle as pre-existing rather than a Git-history request-thread call.
- [x] Add a dynamic Generic-Pane `gitdiff:` tab descriptor through `TAB_TYPES` with one item encoder/decoder for canonical selected path plus frozen history identity; implement create/render/cleanup/relocalize/URL-state hooks and ensure reload, Back, drag/move, close, LRU pruning, pane-role rejection, and duplicate-open activation follow existing tab contracts. DONE 2026-08-17 PT: the dynamic descriptor, immutable URL snapshot cursor, lifecycle hooks, navigation, placement, and duplicate activation are covered by the current 54/54 cross-surface, 164/164 async-layout, and 94/94 restore shards plus the passing repo-root/nested-path browser journey.
- [x] Extend Finder's explicit context-menu action registry, not its menu DOM ad hoc: directories get `Diff repo` only when `primaryInfo.repo_root` proves Git eligibility; files get `Edit in new tab`, `Preview in new tab`, and `Diff in new tab` in that exact order; preserve shared accessibility state and touch long-press, omit `Diff repo` for multi-select/proven non-repo directories, disable unavailable file modes with reasons, and keep the actions absent from Differ/Tabber in v1. DONE 2026-08-17 PT: the registry emits the approved order, touch path, and absence rules; the current three-test real-browser selection passed with typed binary and oversized Diff disable reasons.
- [x] Route all three Finder file actions through the shared `openFileInAdditionalEditorTab`/`openFileInEditor` owner with one canonical working-tree file-tab identity, current-pane placement on first open, exact-repeat activation thereafter, and item-scoped `edit`/`preview`/`diff` selection; Diff uses `HEAD → current`, mode changes preserve dirty content and view state, and no mode-specific tab or rendering path is added. DONE 2026-08-17 PT: Finder mode actions now converge through the shared current-Editor owner, explicitly reload stale refs as `HEAD → current`, and preserve the existing pane; the current cross-surface shard and dirty-file browser journey pass.
- [x] Build the directory tab from the shared tree/action-row controls: bounded initial history, one-line commit summaries ordered `SHA → date/time → files/+/- → author → description` with responsive retention priority `SHA → description → changes → date/time → author`, lazy per-SHA expansion, multiple retained disclosures, full message text, folder grouping, status chips, aggregate/file `+N/-N`, binary/rename/delete rendering, refresh, frozen pagination, and explicit loading/queued/empty/error/truncated states. DONE 2026-08-17 PT: the shared tree renders the approved row order/retention, multi-disclosure details, typed states, binary/count metadata, refresh, and frozen pagination; Node shards pass and the browser journey expands two commits, loads an older page, and restores the frozen first page.
- [x] Route commit-file selection through the shared historical editor Diff owner with another current-Editor tab instance per canonical file/FROM/TO tuple and exact parent/empty-tree FROM plus commit TO refs; Diff is selected, Preview renders immutable TO-side content, and Edit is disabled/read-only. Cover current-missing/deleted, historical rename, binary, root commit, merge first-parent, file outside current worktree, and diff-unavailable cases while the canonical working-tree file tab, directory tab, and normal Back navigation remain intact. DONE 2026-08-17 PT: immutable tuple tabs reuse the current Editor/Diff renderer, retain exact comparison refs and typed unavailable states, isolate working-file chrome/state, and close without mutation prompts; the current Node shards and historical-file browser journey pass.
- [x] Add accessibility, locale, theme, and responsive behavior through shared owners: tree semantics and `aria-expanded`, roving keyboard focus, Enter/Left/Right disclosure, localized labels/status/errors, localized visible date/time plus absolute-time tooltip, dark/light/editor-light paint tokens, narrow-pane field elision/truncation that preserves the approved priority without wrapping the commit summary, focus restoration after async expansion, and motion-independent loading state. DONE 2026-08-17 PT: commit/file trees use the shared interaction controller, localized labels/dates and logical RTL spacing, shared paint tokens, and responsive field retention; real-browser roving focus exposed and verified the post-reconciliation focus fix, and the locale/theme/narrow checks pass.
- [x] Add deterministic frontend regressions for exact context labels/order, one directory tab per canonical path, commit-summary field order and responsive retention priority, one working-tree file tab across Edit/Preview/Diff actions, ref-pinned current-Editor historical instances with Diff selected, immutable Preview, and disabled/read-only Edit, tab placement and URL restore, frozen pagination, stale async generation suppression, disclosure persistence, tree grouping/ordering, exact line/status rendering, explicit refs on historical file open, keyboard/touch/accessibility state, locale rerender, themes, and narrow geometry; use the existing Node shard owners and live browser harness rather than a second fixture stack. DONE 2026-08-17 PT: coverage stays in the existing three Node shard owners and `test_browser_finder.py`; current evidence is 54/54, 164/164, 94/94, and 3/3 respectively, with zero captured browser errors or rejected promises.
- [x] Update `README.md` and the GUI/test coverage maps only after the behavior exists; rebuild from `static_src/` with `python3 tools/static_build.py`, verify `python3 tools/static_build.py --check`, run focused backend/Node/browser tests after each checkbox, then run the unmodified canonical `python3 tools/check.py` without retries, sleeps, serialization, lowered concurrency, or weaker assertions. DONE 2026-08-17 PT: README and GUI/test coverage maps describe the implemented behavior; the generated bundles match the served 7773 assets, the current `python3 tools/static_build.py --check` exited 0, and the unmodified canonical run passed every functional lane before correctly returning 4 at certification because the checkout is dirty and the host I/O preflight exceeded its limits. Exact-SHA certification remains owned by the final gate below.
- [x] After explicit implementation approval and code completion, restart only 7773 through the guarded launcher, record PID/CWD/HEAD/served-bundle identity, and run an authenticated browser journey on the exact same SHA: exercise all three Finder file actions and prove one tab changes modes, exercise repo-root and nested-directory `Diff repo`, expand two commits, open modified/renamed/deleted files, load older commits, refresh after a new fixture commit, reload/Back, light/dark, keyboard, and narrow-width checks with zero unexpected API/SSE/console errors. DONE 2026-08-17 PT: guarded restart left only PID 3680310 listening on 7773 from the exact worktree and implementation SHA; served JS/CSS matched disk. Authenticated acceptance returned `ok: true` and proved the exact action order, one working-tree file tab across modes, root/nested history, two disclosures, modified/renamed/deleted historical files, immutable Preview/read-only Edit, 50-to-55 pagination, Refresh, reload/Back, both themes, responsive retention, keyboard focus, no retained browser/API/SSE failures, and clean WebDriver retirement.
- [x] Before closing, read the complete diff, search every new route/operation/item-prefix/locale key and every retired temporary path repo-wide, report the shared parents used and net non-generated lines, archive the completed result under `docs/DONE/`, update queue/status accounting if this work is admitted into a release, and remove this drained DOIT file only after every done criterion is measured. DONE 2026-08-17 PT: the complete 89-file diff is +9706/-398; excluding generated `static/`, 67 files are +7872/-252, net +7620. Route/operation/item-prefix searches leave direct history calls in jobd, all 19 new keys in all 19 source and 20 built catalogs, and no retained acceptance-fixture path. The implementation reuses the descriptor-pinned filesystem/jobd pipeline, Generic Pane/TAB_TYPES lifecycle, current Editor/Preview/Diff owners, CodeMirror Diff renderer, and shared tree/action-row controls. This archive and the DONE index close the queue; release status was not changed because this work has not been admitted into another release.

## Verification Matrix

| Layer | Required evidence |
| --- | --- |
| Filesystem/Git | Focused `python3 -m pytest` nodes in `tests/test_filesystem.py` prove descriptor-pinned repo/scope/ref identity, frozen pagination, message/status/numstat parsing, bounds, constant subprocess count, and hostile filenames. |
| API/jobd | Focused nodes in `tests/test_server_query.py`, `tests/test_app.py`, `tests/test_jobd.py`, and route-sweep coverage prove registry/auth/refusal/pending/retained/failure behavior and no request-thread Git. |
| Frontend state | Existing Node shard owners cover exact context labels/order, one working-tree file-tab identity across Edit/Preview/Diff modes, tab placement/restore, generation fences, disclosure state, tree view models, ref-pinned historical file tabs, and reuse of existing Preview/editor/Diff renderers. |
| Browser behavior | `tests/test_browser_finder.py` covers the real right-click/long-press actions, all three file modes converging on one tab, and the `Diff repo` directory tab; existing editor/Differ browser owners cover the historical file tab, MergeView, deleted/renamed cases, Back/reload, accessibility, themes, and geometry. |
| Generated assets | `python3 tools/static_build.py` followed by `python3 tools/static_build.py --check`; generated artifacts remain unstaged unless explicitly requested. |
| Canonical gate | An unmodified `python3 tools/check.py` exits 0 on the final source identity and reaches its terminal summary. |
| Live 7773 | One authenticated real-browser run records listener PID, `/proc/<pid>/cwd`, HEAD, served bundle, page/API/SSE health, exact interactions above, and zero unexpected browser/server errors. |

## Gotchas

- Do not turn this into another Differ global mode. `Diff repo` is a new ordinary Generic-Pane tab; historical files still use the existing Editor/Preview/Diff shell.
- Do not create separate working-tree tabs or tab types for `Edit in new tab`, `Preview in new tab`, and `Diff in new tab`. They are three entry actions into one file tab with one selected mode.
- Do not infer repo eligibility from `.git` path strings in the browser. Use the authorized `/api/fs/info` payload already fetched by the context menu and revalidate on the backend operation.
- Do not run `git log`, `git show`, `git diff`, or `git numstat` on the HTTP request thread, per commit row, per file row, or from browser-supplied cwd/ref text without the existing descriptor-pinned owner.
- Do not let a history refresh overwrite a newer tab generation, collapse user-open commits, reset scroll/focus, alter an already-open historical file's immutable refs, or change the canonical working-tree file tab's selected mode.
- Do not silently fall back from a selected historical commit to `HEAD → current`. If exact refs are unavailable or reordered, show a typed error in that file tab.
- Do not treat `-` numstat fields as zero. They mean binary/unknown line counts and must render as `binary` or an explicit unavailable count.
- Do not flatten renames into two unrelated files or lose paths containing tabs/newlines. Preserve old/new paths and parse machine-delimited Git output.
- Do not claim a complete commit/file list after a cap, timeout, stale cursor, partial Git response, or truncated payload. Keep prior valid rows and show the typed partial/error state.
- Do not duplicate Finder/Differ tree rows, context-menu button construction, tab lifecycle, editor Diff rendering, ref controls, or locale/theme paint rules.
- Do not hand-edit `static/yolomux.js` or `static/yolomux.css`; edit `static_src/` and rebuild.
- Do not modify or restart 7770-7772 during implementation or acceptance.

## Done Criteria

- [x] Keiven approved the final mock revision before the first implementation edit, and the DONE record links the approved revision of this queue. DONE 2026-08-17 PT: approval is recorded in the Mock Approval Gate and Status above before any product edit.
- [x] Finder shows exactly `Diff repo` for one eligible Git-backed directory and exactly `Edit in new tab`, `Preview in new tab`, and `Diff in new tab` for one file, with the approved order and disabled/absent behavior for multi-select, no-repo, blocked, readonly, Differ, and Tabber cases. DONE 2026-08-17 PT: current Node registry tests plus the passing right-click/touch browser tests cover the exact labels/order, absence cases, and typed binary/oversized disable reasons.
- [x] The three Finder file actions create or activate one canonical working-tree Editor/Preview/Diff tab, select only the requested mode, preserve dirty content and placement across mode changes, use `HEAD → current` for Diff, and add no duplicate tab type or renderer. DONE 2026-08-17 PT: the current cross-surface and real-browser tests prove one dirty working-tree tab survives Edit → Preview → Diff, stays in its pane, and replaces arbitrary stale refs with `HEAD → current`.
- [x] A repo-root or nested-directory `Diff repo` action opens one movable/restorable Generic-Pane directory history tab with bounded newest-first commits, approved one-line field order and responsive retention priority, frozen pagination, refresh, lazy multi-disclosure details, messages, file trees, statuses, and honest `+N/-N` or binary metadata. DONE 2026-08-17 PT: the current Node state/restore coverage and passing browser journey prove root/nested identity, exact row geometry, two disclosures, older-page loading, frozen reload, status/count rendering, locale/theme behavior, and duplicate activation.
- [x] Selecting modified/added/deleted/renamed/binary files from ordinary, root, and merge commits creates another current-Editor tab instance keyed by exact immutable parent/empty-tree → commit refs or shows a typed unavailable state; Diff is selected, Preview renders immutable TO-side content, Edit is disabled/read-only, and the tab never silently substitutes working-tree refs. DONE 2026-08-17 PT: current Node cases cover root/merge/status/ref variants and typed unavailable results; the passing browser journey proves a merge first-parent rename opens the current Editor shell with exact refs, immutable Preview, disabled Edit, isolated chrome, and safe close.
- [x] Backend tests prove allowed-root and descriptor-bound authorization, exact repo/scope/ref identity, hostile filename parsing, constant subprocess counts, bounded runtime/bytes/rows, retained/pending delivery, stale generation/cursor refusal, and zero request-thread Git. DONE 2026-08-17 PT: the current six-module focused selection passed 99 tests with the unhandled-thread warning promoted to an error; the unmodified canonical run also passed its complete non-browser lane in 374.69 seconds, and the repo-wide call search leaves `filesystem.git_history`/`filesystem.git_commit` invoked only by jobd.
- [x] Node and real-browser tests prove async generation fencing, disclosure/focus/scroll persistence, keyboard/touch/ARIA behavior, localization, dark/light/editor-light themes, narrow geometry, URL/tab restore, Back navigation, and zero unexpected JS/API/SSE errors. DONE 2026-08-17 PT: current Node shards passed 54/54, 164/164, 94/94, and 46/46; the four focused Finder/readonly/history browser journeys passed with explicit zero-error assertions; and the unmodified canonical run passed its full browser lane in 480.29 seconds.
- [x] Focused tests, generated-asset check, the unmodified canonical gate, and same-SHA authenticated 7773 acceptance all pass with their real exit codes/output read in the completion turn; implementation, restart, and live acceptance remain reported as separate states. DONE 2026-08-17 PT: focused backend, Node, and browser evidence plus `python3 tools/static_build.py --check` passed on implementation SHA `221e232e5`; the final unmodified `python3 tools/check.py` exited 0 after all functional lanes and 7/7 exclusive certification units passed on a qualified host. The retained authenticated 7773 artifact for the same SHA was read as `ok: true`; restart identity and browser behavior remain separately recorded above.
- [x] Specs/user docs, DONE archive, release queue/status accounting when applicable, complete-diff review, duplicate/negative searches, shared-parent inventory, and non-generated line accounting are complete; the drained queue is then removed. DONE 2026-08-17 PT: the GUI contract, README, and test coverage maps describe the shipped behavior; this full approved queue is archived under `docs/DONE/2026-08`, the DONE index is updated, the complete-diff and repo-wide searches are recorded above, and no release-status file changed because the feature is not admitted into another release.
