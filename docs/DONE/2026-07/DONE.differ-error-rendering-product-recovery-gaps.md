# DOIT: Differ error rendering + product recovery gaps

Handed over from the dev7771 Claude session. Everything below was reproduced against the live 7771 server, not inferred. Each item states the evidence so you can re-verify rather than trust it.

Context: the daemon scheduler-pump wedge is already FIXED and committed as `cf4f896b`. These are the gaps that investigation surfaced and did not close.

## 1. Differ renders `[object Object]` instead of the failure reason

- [x] Fix the fallback at `static_src/js/yolomux/86_changes_editor.js:1847` and add a test. DONE: `6b30c534` replaces the object-string fallback with the localized generic descriptor; `node --test tests/share_theme.test.js` and `python3 tools/static_build.py --check` pass.

**Symptom (screenshot-confirmed):** the Differ pane for session `yo7773` shows `0 repos, 0 files changed in 'yo7773'`, then a red error box containing literally `[object Object]`, then `No Differ results for this session.`

**Root cause, traced end to end:**

- `86_changes_editor.js:1847` renders each entry with `messageDescriptorText(error, String(error || ''))`.
- `messageDescriptorText` (`static_src/js/yolomux/10_core_utils.js`) ends with `return String(value.fallback || fallback || '')`. When the descriptor has no resolvable `key` AND no `fallback` field, it returns the caller's fallback verbatim.
- The caller's fallback is `String(error || '')`, and `errors` only ever holds descriptor OBJECTS — `86_changes_editor.js:1122` builds them as `userMessageSnapshot(err, String(err?.message || err)).user_message`.
- So `String(<object>)` is `[object Object]` every time. That fallback is guaranteed garbage in exactly the case it exists to serve, and the real failure reason is discarded (CLAUDE.md 3.1: a failure may never be DISCARDED).

**Scope:** this is a one-off, not systemic. It is the only one of the 15 `messageDescriptorText` call sites that passes `String(<object>)` as its fallback — verified with `grep -rn "messageDescriptorText(" static_src/js/`.

**What the fix must do:** surface the actual reason. Pass a meaningful fallback (a localized generic message, or the serialized descriptor) so a descriptor missing both `key` and `fallback` still tells the user what failed. Do not just suppress the box.

**Test to add:** a unit test on the render path asserting that a descriptor with neither `key` nor `fallback` renders something other than `[object Object]`. There is currently no test covering this path, which is why it shipped.

## 2. Confirm whether yo7773 genuinely has no changes

- [x] Determine whether `0 repos, 0 files changed` for `yo7773` is correct or a second defect. DONE: live `https://127.0.0.1:7771/api/session-files?from=HEAD&to=current&session=yo7773&hours=24` returned one repo, `count: 0`, `files: []`, and `errors: []`; the 62 touched transcript files were not Git changes. The earlier red box was the dead scheduler-pump incident, not a second Differ defect.

`yo7774` returned `READY` with 430 files across 2 repos from `/api/session-files?session=yo7774`, so the endpoint works. `yo7773` returned an error descriptor instead. Once item 1 surfaces the real reason, decide whether that reason is legitimate (nothing changed in that worktree) or a real failure that item 1 was masking. Do not close item 1 without reading the reason it exposes.

## 3. Queued products are not re-driven across a daemon restart

- [x] Decide whether to re-drive or explicitly fail queued products when the daemon restarts. DONE: retain the existing re-drive contract. A replacement `RemoteStoragedProducts` bridge attaches the storage-owned queued ticket on its next request and schedules it; `tests/test_daemon_products.py::test_replacement_remote_bridge_redrives_a_storage_ticket_after_daemon_restart` and the stale-LKG counterpart pass (2 passed in 0.15s). The reported live ticket was caused by the dead pump that `cf4f896b` now prevents, not missing restart recovery.

**Evidence:** a queued ticket survived a full server restart because `boot.sh` reuses an existing daemon — ticket `1370c9cd30d7484a9c16f410aa1223bf` was returned identically before and after restarting the web server. The client holds a ticket for work that no longer exists, and polls `QUEUED` forever.

The pump fix in `cf4f896b` removes the common cause (the pump thread died on a `RuntimeError` and never restarted, leaving depth 42 / 41 overdue while STATUS still reported `healthy: true`). The readiness gate added in the same commit stops a wedged daemon from being reused at boot. Neither re-drives a ticket that is stranded mid-session.

Options worth weighing: re-drive outstanding product keys on daemon start, or return a typed terminal failure so the client re-requests instead of polling a dead ticket. A typed failure is likely cheaper and matches the "typed result for EXPECTED failures" rule.

## Verification notes for whoever picks this up

- Everything runs in Docker on this box: `./docker/run-gate.sh`, or `./docker/run-tests.sh -- python3 -m pytest ...` for a subset.
- The `browser`, `visual_golden`, `boot`, `e2e`, `node_bridge` and `contention_prone` markers are load-sensitive. `contention_prone` (`2814e1af`) marks nine multi-process tests measured failing together at load 18.21 and passing in 30.20s in isolation on the same commit. Re-run once before treating any failure in those lanes as real.
- To reproduce the Differ symptom against a live server, the auth cookie is port-scoped: `yolomux_auth_<port>`, value from `auth_cookie_value(user.username, user.password)` using the namespaced config in `~/.config/yolomux-dev7771/`.

---

No date recorded in this queue; 2026-07-28 is the file mtime, not a landing measurement.
