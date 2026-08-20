# DOIT.p1.e2.background-owner-live-fleet-acceptance.md - Verify Shared Background Ownership On 7770-7773

## Goal

Prove the shipped single-owner background indexing and cache model on the real Linux fleet without confusing isolated fixture evidence with multi-port acceptance.

## 2026-08-18 Measured Findings - Requirements Retained, One Decision Needed

Nothing below is deleted. The original Plan and Done Criteria remain in force, including explicit mutation permission, per-port pre-state capture, the same-SHA requirement, exact multi-port cache writes, and responsive authenticated UI on every requested port.

WHAT WAS DRIVEN AND PROVEN (partial, under explicit user authorization to drive 7770-7773):

- Per-port pre-state was captured before mutation: PID, CWD, HEAD, state root, and served bundle for 7770, 7771, 7772, and 7773.
- Same-SHA achieved for 7770, 7771, and 7772 at `7cb75e3a5`. 7773 was deliberately EXCLUDED because its worktree is on `dev/0.7.8-7773`, 4 commits ahead with 20 dirty files of unrelated live work; the queue requires unrelated ports and state remain unchanged, so aligning it was refused.
- Exactly one background owner in the durable root, holding all five roles: `search-index`, `session-files`, `stats-sampler`, `tabber-activity`, `watch-roots`. Owner counters `takeover_success=1`, `takeover_failed=0`, `owner_released=0`, `follower_stale_reads=0`.
- Fleet topology restored afterwards; all four ports serve `/healthz` 200 with unauthenticated `/api/ping` 401.

BLOCKING TOPOLOGY FINDING REQUIRING A USER DECISION:

Measured roots: 7770 runs with no `YOLOMUX_ROOT` override on the durable root, while 7771, 7772, and 7773 each run on a private ephemeral root under `/tmp/y1776734304/p777x` with its own background-owner directory. `app.py:start_background_owner` gives a private-root process a `DisabledBackgroundOwner` that bypasses same-root election by design. A driven owner-loss test confirmed the consequence rather than refuting it: after SIGTERM to the 7770 owner the heartbeat aged to 28.0s against a 3.0s unresponsive threshold and the live generation count fell to 0 for 18s while 7771 and 7772 stayed healthy.

Therefore "7771 takeover after restart", "multi-port Tabber/Finder cache writes", and "shared-root Quick Open/search indexing" cannot be satisfied against this fleet as deployed. Declaring them not-applicable would MATERIALLY WEAKEN this acceptance, so it must not be done unilaterally. THE USER MUST DECIDE between: (a) stand up a deliberately shared-root multi-port fixture and run the original criteria there unchanged, or (b) accept durable-root-plus-private-dev-roots as the contract and record precisely which original criteria are being retired and why. Until that decision is recorded, the affected boxes stay open.

TWO MEASUREMENT DEFECTS TO AVOID REPEATING:

- Follower worker-thread absence cannot be shown by thread name: every thread on every fleet process reports `comm=python3`, so such a check is incapable of failing and is not evidence. Use a per-process owner-claim query, or add real thread naming first.
- Authenticated UI responsiveness must be measured on a qualifying host. The 2026-08-18 attempt ran at load 11-17 on 32 CPUs and is contaminated; record host load with every sample and state plainly whether it qualifies.

LATENT ISSUE, RECORDED NOT FIXED: `background_owner_priority` documents "prefer one configured server", but `boot.sh` sets each process's `YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT` to its own port, so every server computes priority 100 for itself and the tiebreak falls through to `started_at_ns`. Within the single-participant durable root this never fires, so it is latent rather than active.

SCOPE BOUNDARY: the two-host NFS configuration-lock acceptance is a separate concern and must not be merged into this queue. It completed 2026-08-18 and is archived at `docs/DONE/2026-08/DONE.nfs-configuration-lock-acceptance.md`.

## 2026-08-19 Read-Only Topology Refresh - Fixture Decision Still Required

At 8:20 AM PT, all four listeners were healthy (`/healthz` 200, unauthenticated `/api/ping` 401), but the acceptance topology and source identity still did not qualify. Port 7770 is the sole elected durable-root owner; managed 7771-7773 remain separate private roots with `DisabledBackgroundOwner`, so the deployed rows cannot prove shared takeover or cache convergence. Source identity is mixed: 7770 processes are stale relative to their now-clean checkout, 7773 is exact-current for its checkout, 7771/7772 listeners re-execed from a dirty tree without an exact SHA, and their independent daemons did not all reload later jobd/materializer/storage changes. Current source also lacks a watchd lifecycle projection, so typed absence of follower work across all five roles is not yet observable.

The earlier 8004-8007 fixture range is no longer free: unrelated listeners occupy 8005 and 8006. No process or port was changed. The smallest safe acceptance decision is either exact retirement/restoration authority for those listeners or approval of a preflighted alternate range such as 8014-8017. Use one clean integrated SHA, one shared short `YOLOMUX_ROOT`, `YOLOMUX_INSTANCE` unset, and one fixed primary port; require exact PID/CWD/SHA/bundle/lease/socket/owner/service identity, authenticated `worker_records`, deterministic primary loss/takeover, cache/index writes, a qualifying-host large rebuild, and at least 300 seconds of soak before exact fixture-PGID teardown. Until that authority and the missing lifecycle projection exist, all five boxes remain open.

## Plan

- [ ] With explicit permission to restart or drive 7770-7773, record each port's PID, CWD, HEAD, state root, owner generation, service roster, and served bundle before mutation.
- [ ] Verify startup ownership order, 7771 takeover after restart, multi-port Tabber/Finder cache writes, shared-root Quick Open/search indexing, follower worker-thread absence, and UI responsiveness during a large index rebuild.
- [ ] Restore the requested fleet topology and retain only bounded evidence under `/tmp`; unrelated ports and state must remain unchanged.

## Done Criteria

- [ ] One current same-SHA run proves exactly one background owner, deterministic takeover, shared cache convergence, no follower worker, and responsive authenticated UI on every requested port.
- [ ] The DONE record links the already-complete isolated 8004-8007 implementation evidence and separately records the real-fleet acceptance result.

## 2026-08-18 Read-Only Revalidation (Snapshot `77adf861a`)

- Read-only process inspection found 7770 at `7cb75e3a5` in `/home/keivenc/dev/yolomux`; 7771 and 7772 at `77adf861a` in this checkout; and 7773 was deliberately not queried. 7771/7772 each retain separate private roots at `/tmp/y1776734304/p7771` and `/tmp/y1776734304/p7772`, so they cannot prove shared-root election or cache convergence.
- Unauthenticated reads proved the same served `static/yolomux.js` digest on 7770-7772, but `/api/background/status` returned 401 and was not authenticated. This does not prove loaded Python identity, owner generation, or service roster.
- Existing unit coverage proves deterministic lock election, release/stale takeover, owner-only index walking, and follower cache replay. It does not provide a two-process shared-root multi-port acceptance fixture, a typed per-process follower-worker absence proof, or a current 8004-8007 evidence record. `launcher_probe owner` can verify owner payload identity once such a fixture exists, but not cache writes or actual worker absence.
- Remaining authority blocker: live acceptance needs an explicit mutation plan for 7770-7773. The only safe independent path is a short-root 8004-8007 fixture with one shared `YOLOMUX_ROOT`, one fixed primary port, private HOME/XDG/tmux/browser resources, per-row listeners, authenticated status reads, fixture-only takeover, and complete teardown. Do not use `boot.sh` for that fixture because it assigns each row its own primary port.

## 2026-08-18 Isolated Fixture v2 - BLOCKED On Typed Follower-Worker Evidence

- Red first: `python3 -c '... BackgroundOwnerRegistry(...).status_payload(); assert "worker_records" in payload'` failed because the payload contains only `owner`, `generation`, `current_owner`, `roles`, counters, queue, and diagnostics; it has no lifecycle-backed per-process worker records. `roles` proves logical `can_run()` admission, not that a follower has no actual worker.
- A direct 8004-8007 launch with `YOLOMUX_INSTANCE` unset and one explicit `YOLOMUX_ROOT` would use shared election, not managed local-owner mode; `is_managed_instance_port()` measured `False` for all four ports. The fixture still requires four explicitly leased ports, one fleet-level exact-identity teardown after every web row stops, authenticated status reads, host qualification before and after, and fixture-only HOME/XDG/tmux/browser/config/state/cache/runtime/socket/log resources.
- Product-owner arbitration is required before a truthful fixture can satisfy the follower criterion. Requested narrow diff: `TmuxWebtermApp.background_owner_status_payload()` exposes a bounded `worker_records` projection from the existing `ActivityCache` tabber refresh/warmer records, with `{role, kind, owner_generation_id, state, worker_alive}`; followers emit `[]`. Add active/idle/demotion coverage in `tests/test_background_owner.py`, authenticated route-shape coverage, then a two-process shared-root fixture assertion. This is not evidence for all five roles until jobd/statsd/indexd/watchd expose their own lifecycle records.
