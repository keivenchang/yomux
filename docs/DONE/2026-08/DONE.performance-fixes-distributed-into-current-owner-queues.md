# Archived DOIT.performance-fixes.md — Distributed Into Current Owner Queues

## Current status — 2026-08-03

Not started. **This queue was audited by yo7775 after its first draft and rewritten; two items were unsafe as written and one was mis-sequenced.** See *Audit corrections* below before implementing anything. One change has already landed ahead of the queue: `30_app_menus.js` no longer sends `force=1` on session metadata.

Archive status: valid phase-attribution work moved to `DOIT.p2.latency-boundaries.md`; refresh, watch-root, Finder-batch, deepcopy, jobd, statsd, and statusd work moved to `DOIT.p1.refresh-fanout-background-cpu.md`; activity summary remains in its P0-disable/P1-replacement chain; SSE deltas remain in `DOIT.p2.sse-payload-delivery.md`; copy feedback moved to `DOIT.p2.copy-feedback.md`. The rejected security/performance shortcuts remain below as historical guardrails, not active checkboxes.

## Goal

The 7771 web process stops saturating a CPU core during normal browsing, and no browser-visible request computes expensive work synchronously on the request thread. Keiven's stated requirement is that async is the default everywhere and that filesystem work is limited to what the GUI actually asked for, rather than stats and listings for every path on every cycle.

## Audit corrections — read before implementing

The first draft of this queue proposed a memoization and a string-prefix rewrite that would both have introduced defects. Both counterexamples were independently reverified against source.

- **Full-result memoization of `_path_is_secret` is unsafe and is deleted.** The decision depends on the lexical path **plus** the `resolved`/`resolve` arguments **plus** a mutable policy. `paths.py:185` `invalidate_path_policy_caches()` exists precisely because "a filesystem mutation can replace a symlink". The same lexical path can be safe with `resolve=False`, then be retargeted into `~/.ssh` and become secret with `resolve=True`; a path-string result cache returns a stale `False`. A bounded LRU also cannot approach a 100 percent hit rate for a sequential working set larger than its capacity, so the performance premise was wrong too.
- **The string-prefix rewrite of `_path_is_within` is wrong as stated and must not be applied globally.** `DEFAULT_FS_ROOTS = ("/",)` at `paths.py:21`. For root `/`, `root + os.sep` is `//`, so `"/tmp".startswith("//")` is `False` while `Path("/tmp").relative_to(Path("/"))` succeeds — the rewrite would deny every child of the default filesystem root. It also disagrees for a relative root such as `.`, and not all callers pass normalized input.
- **The measurements identify a candidate, not a cause.** Re-measured minima over three runs of 200 calls: `_path_is_secret` **223.6 us** with a real `resolved`, **257.2 us** with internal resolution; `_path_is_within` **18.8 us**; string prefix **0.172 us**. The original 370.7 us is not reproducible outside one benchmark shape. The profile is weaker than claimed: 162 samples against **222 failed attempts**, and of 98 samples in the target-named thread only 61 contain `run_client_directory_poll_once`, 46 `_visible_directory_names`, 35 `_path_is_secret`, 25 `_path_is_within`.
- **The GIL explanation is not established.** The 3030 ms ping is a client-side figure including browser queueing, connection, network and TLS. `ThreadingHTTPServer` does not exclude pre-route queueing, and `server.py`'s `request_line_wait` explicitly includes keep-alive idle and TLS. The clustered completions fit connection or client scheduling, or another shared bottleneck, equally well.
- **The 512 figure was wrong.** `watch_signature` calls `_visible_directory_names`, which scans up to `MAX_DIRECTORY_ENTRIES = 1000` and only then slices names to 512.
- **`child_limit=0` is not safe on its own.** A content edit that leaves the directory mtime unchanged causes `record_filesystem_watch_snapshot` to reuse the token, and `publish_filesystem_ready_event` then returns empty at `app.py:5996`, suppressing the native `fs_changed` entirely. Removing child entries without first making event generation or touched-root identity advance the token deletes change detection.
- **The 18 watch roots are not 12 extras.** `clientServerWatchRoots` deliberately adds visible Modified-files repos and every displayed file parent (`45_file_explorer_actions.js:2403-2411`).
- **`normal_session_local_service` marks 16 routes, not nine**, and is a test inventory marker rather than an offload switch.

## Context

- `yolomux_lib/filesystem/paths.py` — `_path_is_within` (119), `_path_is_secret` (192), `_secret_exact_paths` (147), `_secret_directories` (168), `DEFAULT_FS_ROOTS` (21), `invalidate_path_policy_caches` (185).
- `yolomux_lib/filesystem/listing.py` — `_visible_directory_names` (263), `watch_signature` (509), `MAX_DIRECTORY_ENTRIES`.
- `yolomux_lib/app.py` — `DIRECTORY_WATCH_ENTRY_LIMIT` (1133), `NATIVE_FILESYSTEM_RECONCILE_SECONDS = 300.0` (1137), `VISIBLE_FILESYSTEM_FALLBACK_POLL_SECONDS = 2.0` (1141), `filesystem_roots_for_watch` (5365), `filesystem_roots_watch_signature` (5399), `publish_filesystem_ready_event` (5996), the poll scheduler (6716), `session_metadata_payload` (11918), `activity_summary_payload` (8639).
- `yolomux_lib/infra/background_owner.py:45-55` — the five real background roles. `session-files` is the reference 202-then-SSE-then-200 implementation.
- Keiven's requests, recorded so they are not lost: async is the default and there are to be no more synchronous calls; filesystem work must cover only the directories the GUI expanded and requested; and he asked for refactor and simplification opportunities that reduce CPU.
- The local gate is `python3 tools/check.py`. Regenerate bundles with `python3 tools/static_build.py`; never hand-edit `static/yolomux.js`.

## Plan — Phase A, establish the cause before optimizing

- [ ] Recapture the profile admissibly. Record the exact `py-spy` invocation, rate, duration, thread and GIL flags, sample count and **error count**, and set an error ceiling above which the capture is discarded. The prior capture had 222 failures against 162 samples and cannot serve as a baseline.
- [ ] Produce a matched client-and-server staged measurement for one slow request, carrying a shared measurement id: browser queue and connect time on the client, and accept, request-line wait, route dispatch, handler operation and write time on the server, plus an on-CPU sample. Only after that may any document name the cause. Do not assert GIL starvation before this exists.
- [ ] Establish why `filesystem_healthy` is not selecting the 300 s reconcile interval. Measured polls were about 12 s apart while `watchfiles` 1.2.0 was importable, a `notify-rs` thread was live, and no shim was on `PYTHONPATH`. The two branches differ by 150x. **This gates the watch-cost items and must complete first.**
- [ ] Classify each of the 18 declared watch roots by the surface that declared it (Finder expansion, Differ, Tabber, Modified-files repo, displayed file parent). Only then decide whether any can be dropped, per Keiven's request to watch only what the GUI asked for. Do this before establishing the performance baseline, since the root set changes the baseline.

## Plan — Phase B, the filesystem path cost

- [ ] Cache a **compiled policy or pure candidate classifier keyed by policy generation**, never the final secret/not-secret decision for a path string. The generation must advance on `invalidate_path_policy_caches()`. Prove with a test that a symlink retargeted into a secret directory flips the answer for the same lexical path, and that a `resolve=False` result is never reused for a `resolve=True` query.
- [ ] Add a **normalized-absolute containment helper with an explicit root case**, and migrate only audited call sites to it. Do not replace `_path_is_within` globally. The helper must handle root `/` (where `root + os.sep` would be `//`), a relative root such as `.`, trailing and repeated separators, and non-normalized input. Prove equivalence against the current implementation over a corpus that includes those cases plus the `/home` to `/nfs` alias in this environment.
- [ ] Precompute the secret exact-paths and secret-directories sets as normalized strings keyed by policy generation, so `matches()` performs string comparisons only.
- [ ] Reprofile after the above **before** deciding whether the `SECRET_DIR_SUFFIXES` / `SECRET_FILE_SUFFIXES` nested loops are worth changing. They may not be, once the dominant term moves.
- [ ] Only after the `filesystem_healthy` item and the token fix: pass `child_limit=0` when native watching is healthy. First make native event generation or touched-root identity advance the watch token, so a content edit with an unchanged directory mtime still publishes `fs_changed` rather than being suppressed at `app.py:5996`. Keep the child enumeration for the unhealthy and network-filesystem fallback.

## Plan — Phase C, synchronous request work

- [ ] Give `activity-summary` a background role. Concurrent callers currently block on a shared `Future` and then `copy.deepcopy` the result, and `discover_sessions()` runs per request. Follow the `session-files` pattern.
- [ ] Measure **server operation time** for `background/status` and `auto-approve` before changing them. The slow client-side durations do not by themselves prove the route computes; gate the work on the server-side figure.
- [ ] Decide `normal_session_local_service`: it marks 16 routes and is read only by `tests/test_e2e_api_errors.py`. Rename it to reflect that it is a test inventory, or delete it. **Do not wire runtime behavior from this flag in this queue** — routing requests to local services is a separate architectural change with its own risks.

## Plan — Phase D, independent of the CPU sequence

These are real but do not belong in the causal chain above. They may proceed in parallel and must not be used as evidence that CPU improved.

- [ ] Give every copy affordance one shared feedback parent, and migrate all of them in the same change. Keiven reports the API/SSE Copy control shows no "Copied" indicator, and requires that every place offering copy shows one. The 17 direct `copyTextToClipboard()` call sites are a floor, not the inventory: image copy via `ClipboardItem` and the synchronous terminal copy-event paths bypass that helper. `runDebugCopy` shows the two existing patterns are genuinely different surfaces, not sloppiness — Logs owns control-local transient state while API/SSE falls back to the global status line. The parent needs control-local state that survives a re-render, an updated `aria-label`, a toast or status fallback for ephemeral menus that close on click, preserved synchronous activation on terminal paths, and the existing specialized messages. The parity inventory must cover registered copy affordances and raw clipboard APIs, not only `copyTextToClipboard` calls.
- [ ] Review hot-path `deepcopy` use, starting with `activity_summary_payload` and `tmux_signal_cache` (`app.py:4537-4539`). Correctness-sensitive; prefer frozen or copy-on-write payloads.
- [ ] Send deltas rather than full snapshots for `auto_approve_changed` and `tmux_signals_changed`.
- [ ] Chunk the Finder batch queue to `MAX_FS_BATCH_REQUESTS = 64`. `flushFileExplorerFsBatch()` splices the whole queue uncapped, so more than 64 listings in one 8 ms window returns HTTP 400 and the `catch` falls back to one request per directory. Latent: zero 400s observed on 7771.
- [ ] Tests and docs. A regression test that fails if `watch_signature` runs with a non-zero `child_limit` while the watcher is healthy **and** the token advance is in place; a parity test asserting every copy affordance routes through the shared feedback parent; and a note in `docs/DEVELOPMENT.md` stating that browser-visible routes must not compute on the request thread, naming the background-role pattern.

## Gotchas

- Async fixes perceived sluggishness, not CPU. Under one GIL, moving work to a background thread changes who waits, not how much burns. Do not report a CPU fix on the strength of improved request latency.
- `_path_is_secret` is a security boundary and its policy is mutable at runtime. Any caching must be keyed by policy generation. Caching the final decision by path string is the specific mistake this queue already made once.
- Sibling prefixes: `/home/keivenc/.sshx` must never be treated as inside `/home/keivenc/.ssh`. Prefix comparison is only safe with a separator appended **and** an explicit case for root `/`.
- Removing child entries from the watch signature deletes change detection unless the token advance lands first. Faster is not the same as still watching.
- `filesystem_roots_for_watch` already scopes to GUI-declared roots and the extra roots are deliberate. Do not add a second scoping mechanism; classify before trimming.
- Dropping `force` from a client call is only safe when the unforced server path publishes a change event the client consumes. Verified for session metadata (`transcripts_changed`, handled at `99_terminal_boot.js:7303`). Verify the equivalent before dropping `force` anywhere else.
- Most `force: true` occurrences in `static_src/js/yolomux/*.js` are client-side render forces, not server calls.
- `static/yolomux.js` is generated. Regenerate it; a hand-merged bundle matches no branch's source.
- Measure before attributing cause. Two hypotheses in this investigation were refuted by measurement after they had already been written down as fact.

## Done Criteria

Existing tests are close to worthless for this queue and must not be treated as evidence on their own. Performance work does not fail loudly; it fails by silently deleting detection or widening a security boundary while every test stays green. This repository has already shipped a 1100x speedup with 78 passing tests that hid an arbitrary read/write. Validation is differential and adversarial, not confirmatory.

### Correctness of the security boundary

- [ ] Differential oracle: retain the current implementation under a test-only name and assert both classify identically over a corpus that includes root `/`, relative root `.`, the sibling-prefix case, `..` traversal, trailing and repeated separators, the `/home` to `/nfs` alias in this environment, and a **same-lexical-path symlink retarget from safe to secret**.
- [ ] Cache invalidation test: prove the policy-generation key makes a retarget flip the answer, and that a `resolve=False` result is never served for a `resolve=True` query.
- [ ] Property-based fuzzing over generated path shapes on top of the oracle.
- [ ] Secret canary end to end, **in an isolated environment only**: a temporary HOME, state directory and port. **Never create secret canaries in the real home directory or against the live 7771 server.** Live verification afterwards is read-only.
- [ ] Independent adversarial audit before landing, briefed as "find the path that now leaks".

### Proof the feature still works

- [ ] For the `child_limit` change: a content edit that leaves the directory mtime unchanged still publishes `fs_changed`; then with the watcher forced unhealthy, the fallback still detects it. Both halves, or the change is not done.
- [ ] Negative control per fix: revert only that change and confirm the regression returns.
- [ ] For the copy parent: every affordance in the authoritative inventory shows its indicator on the **first** press in a real browser, and again after revert on a second press.

### Performance evidence

- [ ] Before and after profiles using the recorded invocation from Phase A, with sample counts, error counts and per-frame shares, not impressions.
- [ ] Re-measured `_path_is_secret` cost stated with its benchmark shape, alongside the audited baselines of 223.6 us with a real `resolved` and 257.2 us with internal resolution.
- [ ] Client ping stages reported separately from server budgets. A client-side total is not a server measurement.
- [ ] At least three runs under real load. One run is not a measurement; this host has produced pass, FAIL, pass on an identical build.

### Gate and live verification

- [ ] `python3 tools/check.py` passes, and the affected focused suites pass per checkbox.
- [ ] An explicit **route matrix** showing, per browser-visible route, whether it computes on the request thread and what it returns instead. Do not make a repo-wide claim about all routes.
- [ ] Verified on the live server Keiven uses, with his layout state, ending by reading the JavaScript error logs.

## Rejected shortcuts

- Caching the final secret decision by path string, in any form.
- Replacing `_path_is_within` globally with prefix comparison.
- Passing `child_limit=0` before the watch token advances on native events.
- Raising timeouts, adding retries, adding sleeps, or serializing to hide contention.
- Disabling or narrowing the secret-path filter to make it cheaper.
- Treating async conversion as sufficient and closing CPU items on latency improvements.
- Reducing `DIRECTORY_WATCH_ENTRY_LIMIT` as the primary fix.
- Creating secret canaries in the real home or against the live server.
- Naming a cause from a client-side timing alone.

Current owners: [native network-filesystem watch admission](../../../DOIT.p1.native-watch-network-fs.md), [refresh fan-out and background CPU](../../../DOIT.p1.refresh-fanout-background-cpu.md), [latency phase attribution](../../../DOIT.p2.latency-boundaries.md), [SSE payload delivery](../../../DOIT.p2.sse-payload-delivery.md), [copy feedback](../../../DOIT.p2.copy-feedback.md), and [terminal wheel consistency](../../../DOIT.p2.terminal-wheel-consistency.md).
