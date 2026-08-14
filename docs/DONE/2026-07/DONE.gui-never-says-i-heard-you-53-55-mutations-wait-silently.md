# DOIT: the GUI never says "I heard you" — 53 of 55 mutations wait silently, and the status surface is invisible

Goal: every user-initiated command must acknowledge visually within one frame, stay acknowledged while the backend works, and then settle. Today almost none of them do, and the one feedback channel the code *thinks* it is using is screen-reader-only.

Reported by the user, 2026-07-30: *"I rename, press enter, and nothing happens for 2-3 seconds. This causes me to keep pressing enter. Then the GUI responds and finally renames my tab."*

Researched against `main` at `90a0338c7`. Separate from the other DOITs in this worktree; do not interleave.

---

## Finding 0 — the headline: the status surface is invisible to sighted users

This is the root of everything below, and it is not what the code appears to do.

`yolomux_lib/web.py:334`:

```html
<span id="status" class="sub a11y-only" role="status" aria-live="polite" aria-atomic="true">
```

`static_src/css/yolomux/00_tokens_base.css:1460`:

```css
.a11y-only {
  position: absolute !important;  width: 1px !important;  height: 1px !important;
  overflow: hidden !important;    clip: rect(0, 0, 0, 0) !important;  ...
}
```

`#status` is clipped to a 1×1 px box. The **only** rule that reveals it is `#status.layout-status-visible` (`10_topbar_menus.css:374`, `clip: auto !important`), and that class is added **only** by `showLayoutStatus(message, kind)` when `kind` is `danger` or `advisory`.

Now look at the three functions everything else calls (`10_core_utils.js:1982-2008`):

```javascript
function resetLayoutStatusSurface() {
  statusEl.classList.remove('layout-status-visible', 'layout-status-danger', 'layout-status-advisory');
  ...
}
function statusErr(html) { resetLayoutStatusSurface(); statusEl.innerHTML = `<span class="err">${html}</span>`; }
function statusOk(html)  { resetLayoutStatusSurface(); statusEl.innerHTML = `<span class="ok">${html}</span>`; }
```

Both **remove** the only class that could make the element visible, then write into the clipped node. Counted across `static_src/js/yolomux/`:

| channel | call sites | visible to a sighted user? |
|---|---:|---|
| `statusErr(...)` | 114 | **no** |
| `statusEl.textContent = ...` | 60 | **no** |
| `statusOk(...)` | 16 | **no** |
| `showLayoutStatus(...)` with `danger`/`advisory` | 7 | yes |

**190 invisible, 7 visible.** No toast mirrors them — `showToast` (`20_layout_state.js:5253`) has 2 callers, and nothing observes `#status` with a `MutationObserver`.

Consequence beyond the reported bug: **errors are invisible too.** A failed rename, a failed delete, a failed YOLO toggle all call `statusErr(...)` and vanish. The user is told nothing on success *or* failure.

## Finding 1 — the reported case, end to end

`70_layout_actions.js:2341-2357` (dialog submit) and `:2368-2405` (`renameTmuxSession`):

```javascript
form.addEventListener('submit', async event => {
  event.preventDefault();
  const nextName = input.value.trim();
  ...
  errorNode.hidden = true;
  const renameResult = await renameTmuxSession(session, nextName);   // <-- nothing disabled, nothing shown
  ...
});
```

Four defects in one flow:

1. **No pending affordance.** The submit button is never disabled; the dialog shows nothing. The only feedback is `statusEl.textContent = t('status.sessionRenaming', ...)` at `:2385` — invisible per Finding 0.
2. **No re-entrancy guard.** The form stays live, so every extra Enter fires another `POST /api/rename-session`. This is exactly the "keep pressing enter" loop the user described.
3. **Duplicate submits produce a spurious error.** Each closure captures the *old* `session` name. Once the first request wins, later duplicates target a session that no longer exists, fail, and call `statusErr(...)` — so a rename that actually succeeded reports failure (invisibly, per Finding 0; visibly if Finding 0 is fixed first without fixing this).
4. **The tab does not rename until a full roster rebuild lands.** `:2394-2397` does `replaceTmuxSessionInClient(...)` then `await ensureTerminalRunning(...)` then `refreshTranscripts({force: true})`.

Measured on live 7770 (2026-07-30), three samples each:

```
/api/tmux-status         1.51s  1.61s  1.48s     <- same daemon control path as rename
/api/tmux-session-exists 0.22s  0.16s  0.15s
/api/session-metadata    3.19s  4.51s  3.25s     <- what refreshTranscripts({force:true}) drives
```

So ~1.5s for the rename RPC and 3-4.5s before the roster settles. The user's "2-3 seconds" is conservative.

**The infrastructure for an optimistic rename already exists and is used at the wrong time.** `pendingTmuxSessions` (`00_bootstrap_state.js:840`) with `markPendingTmuxSession` / `isPendingTmuxSession` already models "this client renamed to X and the server has not confirmed yet" — see the comment at `70_layout_actions.js:2155`. But `markPendingTmuxSession(newName)` is called at `:2283` inside `replaceTmuxSessionInClient`, which runs **after** the await. It is a reconciliation guard against stale rosters, never a user-facing state. Renaming the tab optimistically and marking it pending *before* the fetch is a small change to sequencing, not new machinery.

## Finding 2 — the survey: 53 of 55 mutations have no pending state

Every `method: 'POST'` call site in `static_src/js/yolomux/`, checked for any of `.disabled = true`, `aria-busy`, a `busy`/`pending`/`loading` class, or a spinner within 30 lines above the call. **Exactly two have one.**

### Has a pending affordance (the precedent to reuse)

| site | what it does |
|---|---|
| `85_debug_panel.js:6988` `handleDebugSystemServiceControl` | early-returns if `button.disabled` (re-entrancy guard), disables all sibling controls, sets label to `…`, restores in `finally` |
| `95_share_admin.js:423` `createShareFromForm` | `submit.disabled = true`, clears prior error, `finally { submit.disabled = false; }` |

These two are hand-rolled and differ from each other. There is **no shared helper** — which is why the other 53 skipped it.

### The gold standard already in the tree

`82_chat_panel.js` is the only surface that does this properly: an optimistic message goes into `chatState.pending` (`:37`), renders immediately merged with real messages (`:178`), shows a `chat-message-pending` "sending" label (`:307`), and offers a `chat-retry` button on failure (`:306`). Instant echo + pending indicator + failure recovery. **Reuse this shape; do not invent a third.**

### No pending affordance — user-initiated commands

Grouped by surface. All of these are direct responses to a click, Enter, or menu choice, and all are silent while the backend works.

**tmux session lifecycle** (`70_layout_actions.js`, `99_terminal_boot.js`)
- `:2391` `renameTmuxSession` → `/api/rename-session` — the reported bug
- `:2421` `killTmuxSession` → `/api/kill-session` (also broken by the guard in `DOIT.kill-session-guard.md`)
- `:2070` `createNextSession` → `/api/create-session`
- `:1969` `ensureSession` → `/api/ensure-session`
- `99_terminal_boot.js:4893` `tmuxWindow` → `/api/tmux-window`
- `99_terminal_boot.js:1049` `cycleTmuxStatusMode` → `/api/tmux-status`
- `99_terminal_boot.js:5515` `setAutoApprove` → `/api/auto-approve` — **worst offender for double-fire.** It is a *toggle*: the button is not flipped until the response lands (`:5515-5521`), so on a ~1.5s path the control looks dead, the user clicks again, and the second click toggles it back. Net effect: nothing changed, twice.
- `99_terminal_boot.js:7147` `triggerSelfUpdate` → `/api/self-update`

**file explorer / Finder** (`45_file_explorer_actions.js`, `40_file_explorer_files.js`, `46_file_drop_actions.js`)
- `45:332` `createFileExplorerFile` → `/api/fs/write`
- `45:355` `createFileExplorerFolder` → `/api/fs/mkdir`
- `45:572` `deleteFileTreePath` → `/api/fs/delete`
- `45:928` `renameFileTreePath` → `/api/fs/rename` — same shape as the tmux rename: no optimistic rename, no disable, settles only on response
- `40:4042` / `40:4116` `setFileExplorerDirectoryIndexed` → `/api/fs/unindex`
- `46:351` `runServerDropAction` → `/api/drop-action/run`

**recovery / Tabber** (`40_file_explorer_files.js`, `10_core_utils.js`) — these are the *slowest* actions in the product and have the least feedback
- `40:5142` attach-existing, `40:5154` repair-pane, `40:5165` recover, `40:5190` dismiss, `40:5211` + `40:5222` + `40:5246` recover-all, `40:4999` + `40:5032` preflight, `10:5176` `adoptLiveSessionRecoveryRecipe` → `/api/recovery/adopt`

**editor / settings**
- `92_codemirror_editor.js:2288` `saveFileEditor` → `/api/fs/write` — a save with no saving indicator
- `86_changes_editor.js:2525` `saveSettingsPatch` → `/api/settings`

**YO!agent** (`81_yoagent_panel.js`)
- `:1479` `startYoagentChatRequest`, `:1549` `executeYoagentActionSend`, `:1385` `clearYoagentConversation`, `:1404` `updateYoagentJob`, `:1435` `clearYoagentPendingWait`, `:1358` `cancelActiveYoagentChatRequest`

**YO!share** (`95_share_admin.js`)
- `:454` `stopActiveShare`, `:472` `extendActiveShare` (note `createShareFromForm` in the same file *does* guard — inconsistent within one file)

**misc**
- `30_app_menus.js:376` `openYoloRuleFile`, `:388` `reloadYoloRules`
- `99_terminal_boot.js:4055` `uploadFiles`, `:4106` `uploadEditorFiles` — uploads with no progress
- `84_stats_current.js:642` / `:794` `/api/stats-retry`
- `85_debug_panel.js:5764` `refreshDebugCostPricing`
- `94_share_replay.js:3568` `shareUploadDebugProfile`
- `10_core_utils.js:4745` `fetchTmuxSelectionText`

### Not user-initiated — out of scope

Background/automatic; they should stay silent: `40:443` `repairPendingFileExplorerFsBatchItem`, `40:486` `flushFileExplorerFsBatch`, `45:2529` `syncServerWatchRootsNow`, `20:2098` `submitAttentionAcknowledgementKeys`, `85:1966` `flushJsDebugCurrentObservations`, `99:6756` `postEvent`, `81:1578` `prewarmYoagent`, `82:93` `chatApiPost`.

---

## The fix

### [ ] 1. Make the acknowledgement surface actually visible

Decide and write down what the visible channel is. `#status` being permanently `a11y-only` while 190 call sites write to it is the defect; either it gains a visible presentation for non-`danger` tones, or `statusOk`/`statusErr`/`statusEl.textContent` are migrated to a channel that renders. Do **not** leave two channels where one is a no-op.

Keep the `role="status" aria-live="polite"` semantics — the screen-reader path is correct today and must not regress. This is about adding the sighted path, not moving the accessible one.

### [ ] 2. Add ONE shared pending helper and route every mutation through it

There is no shared parent today; two sites hand-rolled it differently and 53 skipped it. Create the parent, then migrate **every** call site in Finding 2 in the same change, with a parity test (CLAUDE.md 3 — reuse the shared parent, never copy-paste; migrate every copy in the SAME change).

The helper must provide, at minimum:

- **Instant acknowledgement** — a visible state change within one frame of the gesture, before any `await`.
- **Re-entrancy refusal** — a second Enter/click while in flight is dropped, not queued and not re-sent. This alone fixes the reported symptom and the spurious-error defect in Finding 1.
- **Disabled affordance** — the submitting control is visibly disabled (greyed), not merely inert, so the user can see why the second press did nothing.
- **Settle** — success or failure both clear the pending state, in a `finally`, including on throw. `createShareFromForm` already models this.
- **Failure surfacing** — the reason reaches the user (CLAUDE.md 3.1: a failure may never be discarded).

### [ ] 3. Optimistic where the outcome is predictable, pending where it is not

- **Optimistic** (apply immediately, reconcile on response, roll back on failure): tmux rename — reuse the existing `pendingTmuxSessions` machinery by marking pending and renaming the tab *before* the fetch instead of after; the YOLO toggle — flip the button immediately, revert on failure; file rename in Finder.
- **Pending indicator only** (outcome not predictable): recovery actions, uploads, self-update, session create/kill.

For rename specifically, also reconsider `refreshTranscripts({force: true})` on the success path (`:2396`). Forcing a 3-4.5s roster rebuild inside a user-facing action is what makes the settle so slow; the optimistic rename should make the forced refresh unnecessary or deferrable.

### [ ] 4. Do not paper over the latency

The pending indicator is not a licence to leave a 1.5s control-path call in an interactive action. Note where the latency itself is the defect and file it separately.

## Tests

- [ ] A control that has been submitted is `disabled` before the request resolves, and re-enabled in `finally` on both success and failure.
- [ ] Pressing Enter N times in the rename dialog issues exactly **one** `POST /api/rename-session`.
- [ ] A rename that succeeds never surfaces an error, even when the user pressed Enter repeatedly.
- [ ] The acknowledgement is visible: assert computed style, not merely that text was assigned to `#status`. A test that only checks `textContent` would pass today against an invisible element — that is precisely how this escaped.
- [ ] The YOLO toggle reflects the requested state immediately and reverts on failure.
- [ ] `role="status"` / `aria-live` semantics are preserved.

Each must fail against `main` before the change.

## Also update the spec

`docs/specs/GUI.md` gains a **Command Acknowledgement Contract** section stating this as a binding rule, so new surfaces cannot reintroduce the pattern. The spec's own header says user direction during implementation overrides older wording and the spec must be updated in the same change — this DOIT is that direction.

## Ground rules

`python3 -m pytest`, never bare `pytest`. Browser tests matter here — this is a UI contract. Gate is `python3 tools/check.py` in the foreground with a tee; it takes a shared flock at `~/.cache/yolomux/expensive-tools.lock` — wait rather than bypass. Rebuild the bundle with `python3 tools/static_build.py` after touching `static_src/`, or `static_build --check` in the gate will fail. Commit locally with explicit paths and `--signoff`; do not push, do not merge, do not run cps. Do not restart 7770 — it is the user's live deployment.

---

# The design: one command registry, one pipeline

A single recommended design, not a menu. It is chosen because **this repo already solved the identical problem on the server side**; the frontend should mirror that rather than invent a second idea.

## Why a registry, and not a base class

`static_src/js/` is ordered partials concatenated by `tools/static_build.py` into one bundle — plain globals, no ES modules, no `import`/`export`. A class hierarchy would mean ~55 subclasses for 55 one-off actions: more code, not less, and cross-cutting behavior through inheritance is the fragile-base-class problem. The shared behavior here is *lifecycle*, which is composition, not specialization.

More importantly, a helper that call sites must remember to call — `runCommand(...)` — only converts "did you add a spinner?" into "did you call the helper?". That is still convention. **A registry removes the choice**: a call site does not call `fetch` at all, it declares a command. There is no ad-hoc mutation to forget, because the mutating endpoints are only reachable through the pipeline.

## The precedent to mirror: `PRODUCT_ROUTES`

The daemon already declares one frozen descriptor per command kind in one table, and the executor reads policy from the descriptor instead of hardcoding it (`yolomux_lib/daemon/products.py:308`):

```python
PRODUCT_ROUTES: dict[ProductKind, ProductRoute] = {
    ProductKind.SESSION_METADATA: ProductRoute(
        ProductKind.SESSION_METADATA, "daemon.fs.session_metadata", Lane.INTERACTIVE,
        ExecutionKind.SPAWN_WORKER, DurationKind.UNBOUNDED,
        generation_fenced_payload=True,
        max_request_bytes=PRODUCT_SESSION_METADATA_MAX_REQUEST_BYTES,
        max_result_bytes=PRODUCT_SESSION_METADATA_MAX_RESULT_BYTES),
    ...
}
```

`ProductPayloadContract`'s own docstring states the principle this DOIT applies to the client — the contract *"belongs beside `ProductRoute` rather than being hidden in each executor."* The frontend disease is exactly that: presentation and lifecycle policy hidden inside 53 handlers.

The codebase has already proved the payoff. While the result budget was a literal inside the executor (512 KB for every product), it was wrong for one route and nobody could see it; moving it onto the route made it reviewable and fixable per command (`229c81f6`, `a233d3c8`). Presentation budgets rot the same way when they live in handlers.

## `COMMAND_ROUTES` — one frozen descriptor per user command

```javascript
const COMMAND_ROUTES = Object.freeze({
  'rename-session': commandRoute({
    method: 'POST', path: '/api/rename-session',
    pendingLabel: 'command.renaming',          // localized key, not a literal string
    disables: '.session-rename-actions button',
    budgetMs: 6000,                            // soft deadline, justified below
    idempotent: true,                          // mints an Idempotency-Key per gesture
    fence: 'tmux-session',                     // which generation guard reconciles a late reply
    optimistic: renameTabOptimistically,       // returns an undo token
  }),
  'auto-approve-toggle': commandRoute({
    method: 'POST', path: '/api/auto-approve',
    optimistic: flipToggleOptimistically, supersede: true, budgetMs: 4000,
  }),
  ...
});
```

`commandRoute()` freezes the descriptor and applies defaults, exactly as `ProductRoute`'s dataclass defaults do. Adding a command becomes a data edit, not new lifecycle code.

## One pipeline

`bindActionDispatcher` (`76_panel_dom_actions.js:137`) already resolves `data-action` → handler, already knows the originating element, already skips `disabled` targets, and already awaits. It becomes the entry point: `data-action` resolves to a **route**, and no per-action fetch code remains.

```javascript
async function dispatchCommand(route, params, source) {
  if (commandInFlight(source, route)) return COMMAND_REENTRANT;   // drop; never queue
  const cmd = beginCommand(route, source);      // disable + pendingLabel + aria-busy, synchronously
  const undo = route.optimistic?.(params);      // applied BEFORE any await
  const overdue = armSoftDeadline(cmd, route.budgetMs);  // timer only — never aborts
  try {
    const result = await apiFetchJson(route.path, {method: route.method, command: cmd, body: ...});
    if (isStale(cmd, route.fence)) return reportSuperseded(cmd);
    return applyResult(cmd, result);
  } catch (error) {
    route.rollback?.(undo);
    return surfaceCommandFailure(cmd, error);   // visible, with reason
  } finally {
    disarm(overdue);                            // revokes the overdue notice by id
    endCommand(cmd);                            // re-enable, clear label — always
  }
}
```

Every requirement in this DOIT lands in one place:

| requirement | where it lives |
|---|---|
| acknowledge within one frame | `beginCommand`, before any `await` |
| refuse re-entry, visibly | `commandInFlight` + the disabled affordance |
| settle always | `finally` |
| surface failures with a reason | `surfaceCommandFailure` |
| optimistic + rollback | `route.optimistic` / `route.rollback` |
| soft deadline + revocable notice | `armSoftDeadline` / `disarm` |
| late-arrival fencing | `isStale(cmd, route.fence)` |
| idempotency key per gesture | `beginCommand` mints it; `route.idempotent` opts in |
| supersede semantics | `route.supersede` aborts the prior command, not the new one |

Change how pending *looks* once, and all 55 surfaces change. That is the property the current code lacks.

## The soft deadline

User direction: *"if backend does not return within a generous timeout (seconds), the GUI shall notify user a timeout/error. If the backend does eventually return, that notification is removed and the GUI updates."*

It is a **soft** deadline. Aborting at the deadline would make "eventually returns" impossible by construction. `armSoftDeadline` starts a timer only; the request keeps running.

```
acknowledged ──(budgetMs)──► overdue ──(response)──► settled
     └──────────(response)──────┴────────────────────┘
```

- **overdue**: raise a persistent, identity-keyed notice — "Still renaming 'x'… taking longer than usual". The control **stays disabled**; optimistic state is **not** rolled back. Reverting at 6s and re-applying at 8s is a flicker that reads as corruption.
- **late success**: revoke the notice by id, apply the result.
- **late failure**: revoke the notice, surface the reason, roll back optimistic state.
- **superseded late reply**: never clobber newer state, and never drop it silently — report it (`CLAUDE.md` 3.1).

Today `apiFetch` has **no timeout at all** (`10_core_utils.js:46`), so a hung mutation hangs forever with no feedback. The four existing `AbortController` sites (`85_debug_panel.js:6130`, `82_chat_panel.js:102`, `20_layout_state.js:4826`, `81_yoagent_panel.js:1463`) are supersede/cancel, not deadlines.

Reuse rather than rebuild:

- **Revocable notice** — `showToast` (`20_layout_state.js:5253`) already returns an id, records it in `toastRecords`, supports `persistent: true` and `actions`, and `removeAttentionAlert(id)` revokes it. The overdue notice must **also** appear at the control, not only in a corner toast: the user is looking at the dialog they just submitted.
- **Fencing** — reuse `pendingTmuxSessions` / `markPendingTmuxSession` (`00_bootstrap_state.js:840`, `70_layout_actions.js:2155`) and server `source_generation`. Do not invent a second staleness rule.
- **Genuinely long work** — the generic `202` / `QUEUED` / `ticket` / `key` protocol already exists and is parsed centrally (`10_core_utils.js:125-132`). A route may declare `longRunning: true` and settle on the ticket instead of holding a socket open; that also survives a reload, which an in-flight fetch does not.

**Budget choice.** `budgetMs` is per route, justified against measurement, never one global literal. This repo has been bitten twice by exactly that — the 3.0s stats-owner budget against a growing DB, and per-call deadline literals centralized in `d65ee323`. Measured here: `/api/tmux-status` ~1.5s, `/api/session-metadata` 3.2-4.5s. A 2s blanket deadline cries wolf on every rename; 30s never fires. **The client budget must exceed the server's own deadline plus transport slack**, or the client announces a timeout moments before the server returns a perfectly good typed `deadline_expired` failure, and the user sees two contradictory messages for one action.

## Enforcement: structural, then ratcheted

1. **Fail closed at the boundary.** `apiFetch` refuses a mutating request without `options.command`. Browsers have no `AsyncLocalStorage`, so do **not** fake an ambient context with a module-scoped flag — it mis-attributes the moment a handler awaits a confirm dialog first. The token is passed explicitly, which is also what makes step 2 statically checkable. Dev builds throw; production reports rather than breaking a user action.

2. **A shrink-only architecture test — the durable part.** This repo already enforces architecture this way *and already statically analyses the frontend from Python*: `tests/test_exception_propagation_architecture.py:29-33` documents `FRONTEND_DISCARD_ALLOWLIST`, which brace-matches JS `catch` blocks and `.catch` callbacks and recursively scans `static_src/js`. Seven such suites exist. Reuse that machinery — one shared parent (`CLAUDE.md` 3).

   Add `FRONTEND_UNROUTED_MUTATION_ALLOWLIST`: every mutating `apiFetch`/`apiFetchJson` site not reached through `dispatchCommand`. Copy the ratchet at `:1133` verbatim:

   ```python
   unreviewed = actual - ALLOWLIST      # new violation -> fail
   stale      = ALLOWLIST - actual      # fixed entries MUST be deleted -> shrink-only
   assert sum(unreviewed.values()) <= VIOLATION_BUDGET   # 0
   assert not stale
   assert sum(actual.values()) <= REVIEWED_BUDGET        # ratchet, only lowered
   ```

   Seed `REVIEWED_BUDGET` at 53 and lower it per migration.

   **Lane placement matters.** These guards live in `--lane pytest-unit`, which is **not** in the default gate — that is how a regression escaped once already. Put this guard where the default gate runs it, or make the lane non-optional. Otherwise the enforcement is theatre.

3. **One table-driven contract test.** Enumerate `COMMAND_ROUTES` and assert the contract for every entry, so command #56 is covered without anyone remembering: acknowledged before the response resolves; N gestures issue exactly one request; re-enabled on success and on failure; the overdue notice appears past `budgetMs` and is revoked by a late reply. Assert **computed style**, not `textContent` — a `textContent` assertion passes against the clipped `#status`, which is exactly how this class of bug survived (Finding 0).

## Server side

Mutating endpoints accept an `Idempotency-Key` minted per *gesture* (not per request) and replay the original outcome instead of re-executing. This closes the hole the client cannot: two tabs, a reconnect retry, or a share mirror. It also directly fixes Finding 1 defect 3 — the duplicate rename replays the first result instead of failing against a session the first call already renamed. Scope it to the destructive/stateful routes first: `rename-session`, `kill-session`, `create-session`, `fs/delete`, `fs/rename`, `fs/write`, `recovery/*`.

## Order of work

1. `COMMAND_ROUTES` + `dispatchCommand` + the soft deadline, with **rename as the reference migration**. Prove the reported bug is gone.
2. Finding 0 — make the acknowledgement surface visible, or none of this is observable.
3. Migrate the rest through `bindActionDispatcher`; each is a data edit.
4. Land the ratchet seeded at the then-current count, in a lane the default gate runs.
5. `apiFetch` fail-closed refusal; server idempotency keys.

Steps 1-2 fix what the user reported. Step 4 is what stops it returning.
