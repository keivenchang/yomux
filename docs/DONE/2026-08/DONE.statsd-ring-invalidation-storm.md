# Statsd ring-invalidation storm fix

The scoped statsd component landed locally as `7a5f751e180baf0ab01d4b15efae9774afc0e344` on both local `main` and `integration/v0.7.12-one-ai`. Release publication and 7220 acceptance moved to `queues/backlog/DOIT.p1.e5.unified-v0.7.12-release.md` because `STATUS-REPORT.md` owns one unified v0.7.12 boundary across five component goals.

## Cause and fix

A live collector re-offered its coverage epoch once per cadence. The storage path invalidated the epoch's full retained extent instead of the interval that changed, so one one-second extension dirtied all 1,248 populated ring slots and prevented readiness from draining. The existing storage appliers now return the exact changed coverage and unavailable intervals; identical re-offers invalidate nothing, and a normal extension invalidates only its new tail.

The same scoped commit fixes the adjacent scheduler timestamp clamp, transcript scan fairness and incomplete-record cursor handling, and the stats identity validation hot path. These owners were kept together because the retained fix tree and its regression matrix already defined one reviewed component boundary.

## Evidence

- Six exact scheduler, storage, and transcript barriers failed on `929085bd7`; the retained current-candidate artifacts passed 11 and 10 focused owner tests, 516 broader statsd tests, six ring browser tests, static source checks, and the architecture budget. The final closure rerun passed 38 focused storage/transcript lifecycle tests.
- The canonical first attempt on exact SHA `7a5f751e1` passed browser, E2E, static, Node, compile, serial, and exact-SHA certification. It remained non-green at 18,004 passed with three non-browser failures: two reproduced unchanged on parent `2c1d0954c`, and one parallel ownership failure was already present in the earlier parent full run. Evidence is retained at `/tmp/yolomux-check-runs/check-1787513359590552823-341665.json` and `/tmp/yolomux-certification/cert-1787513359590790032-341665`.
- An independent current-candidate audit returned no MUST or SHOULD findings after 38 focused lifecycle checks.
- Parent and equivalent-owner audit: the implementation was reconciled against `origin/main` and local parent `2c1d0954c`; repo-wide searches covered `ring_invalidations`, coverage epoch application, unavailable-span application, transcript cursor tiers, scheduler early-wake timestamps, and identity control-character validation. The commit adds 560 and deletes 39 non-generated source/test lines, net +521; the reviewed architecture-budget fixture adds one line separately.

## Landing boundary

This DONE record closes the component queue at its verified local-integration boundary. It does not claim v0.7.12 publication, a green canonical first attempt, a working 7771 runtime, or 7220 acceptance. Those remain unified-release requirements.
