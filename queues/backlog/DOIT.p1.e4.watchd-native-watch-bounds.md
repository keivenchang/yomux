# DOIT.p1.e4.watchd-native-watch-bounds.md - Bound native watch registrations by visible surface, not indexed-tree size

## Consolidation lineage

Split out of `DOIT.p1.e5.memory-hog.md` on 2026-08-25 for this v0.7.16 candidate because the old queue mixed watchd registration work with statsd resource work. The v0.7.22 release decision closed the former P0 resource queue without claiming its unfinished measurements passed. This queue remains the sole watchd native-registration owner.

`STATUS-REPORT.md` goal item 4 now spans both queues rather than the retired mixed queue.

## Goal

Native watch registrations scale with the directories a user can actually see - open Finder directories and exact-file parents - rather than with the total directory count of a configured Quick Open or indexed root. Indexed-root freshness stays correct, covered by the existing breadth-first frontier, mutation evidence, and bounded periodic reconciliation, without a recursive registration per directory.

## Measured incident baseline, 2026-08-11

The released processes ran from `/home/keivenc/dev/yolomux.stable7771` at `926e4a16621c6f96de319a441f8692742a97d856`.

| Service | Confirmed scale factor | RSS | PSS | USS | Peak RSS |
| --- | --- | ---: | ---: | ---: | ---: |
| `watchd` | one recursive registration with 126,028 inotify watch descriptors | 155.5 MiB | 142.4 MiB | 142.0 MiB | 155.4 MiB |

The released configuration unions every `indexed_dir` into recursive native `watch_paths`, so `/home/keivenc/dev` created 126,028 inotify descriptors. Event exclusion runs **after** native registration and therefore cannot bound that topology (`yolomux_lib/watchd.py`). Kernel inotify memory is additional to process PSS.

A 2026-08-19 cardinality run measured released recursive watch registrations of 1, 1,001, 10,001, and 100,001 descriptors at 0, 1k, 10k, and 100k directories. That run modelled current behaviour with `paths[:512]` instead of the product's actual `native_capacity_exceeded` refusal, so it establishes the released scaling law but not the current candidate's behaviour.

## Plan

- [ ] **Freeze a production-scale reproduction and derive an explicit budget.** Build a large nested indexed root that makes the released recursive watch owner exceed the intended registration bound, and first prove the released behaviour exceeds the proposed cap. Measure at 0, 1k, 10k, and 100k directories, recording process PSS/USS **and** kernel slab deltas, because inotify memory lives outside process PSS. Derive the budget from the intended bounded architecture, not by rounding above the incident baseline. Keep raw output under `/tmp`; retain only fixture identities, summarised measurements, and exact repro commands.
- [ ] **Separate shallow native freshness from large indexed-root reconciliation.** Keep immediate native notification only for visible or open Finder directories and exact-file parents. Do not recursively register configured Quick Open or indexed roots; cover them through the existing BFS/frontier, mutation evidence, and bounded periodic reconciliation owners. Apply exclusion **before** registration wherever a native subtree is eligible, enforce one daemon-wide registration union with an explicit cap, and provide a typed bounded fallback when the cap or the platform facility is unavailable.
- [ ] **Audit the existing dirty `watchd`/protocol candidate rather than creating a parallel owner.** The current candidate contains non-recursive and capped work using a cap of 512. Treat it as the thing to finish, not as prior art to duplicate.
- [ ] **Verify the full topology matrix**: overlapping descriptors, symlinks, VCS/cache/dependency exclusions, root add, remove and repoint, policy changes, Linux and Darwin behaviour, native failure and recovery, daemon restart, and no lost visible-directory or exact-file updates.
- [ ] **Expose measured registration readiness.** Report native registration count, cap, and fallback state in the same side-effect-free projection that carries process identity, threads, and FDs. `/readyz` fails closed until the registration cap or a typed fallback is satisfied; `/livez` stays a narrow progress check. Readiness must not depend only on a listener or a process existing.
- [ ] **Add a resource gate that counts registrations, not instances.** Count `inotify wd:` entries, never inotify instance count or host maxima. Fail on descriptor count, swap, readiness latency, and a positive post-settle watch slope. Include a negative control that runs the released recursive-registration path and proves the gate fails for the incident class. Cover startup, incremental change, idle, crash and restart, stale descriptor cleanup, failed native registration with bounded fallback, web restart while daemons exist, and full owned-watch retirement.
- [ ] **Document and archive.** Update `README.md`, `docs/DEVELOPMENT.md`, the watchd and search specifications, and the GUI coverage map with the shallow-native versus reconciliation split, the registration budget, `/livez` versus `/readyz`, and supported recovery behaviour. Record before and after measurements and the frozen release identity in `docs/DONE/`, then remove this queue.

## Gotchas

- **Event filtering is not registration pruning.** A single inotify instance holding 126,028 `inotify wd:` entries is not healthy because the instance count is one. Any gate that counts instances will pass the incident.
- **Kernel inotify memory is outside process PSS and USS.** Measure both process memory and host slab changes, or the cardinality experiment will under-report the real cost.
- Exclusion applied after registration cannot bound the topology. Order matters, and it is the defect.
- Do not treat the current non-recursive/capped diff as closed until its exact scale regression, full gate, same-SHA deployment, and live registration reduction are proven.
- Do not add a second watch service or readiness owner. Extend `watchd`, the local-service projection, and the launcher paths that already exist.
- PSS is the aggregate denominator; summing RSS double-counts shared pages.
- Release certification for this queue runs on one frozen identity together with the statsd queue, from fresh clean Linux and real Darwin checkouts of the same SHA, with maximum allowed indexed-root state. Any relevant post-freeze edit invalidates downstream evidence.

## Done Criteria

- Native registrations stay within the declared cap and scale with shallow visible and exact parents rather than total indexed-tree directories, while indexed-root freshness and mutation convergence remain correct.
- The released recursive path fails the new gate and the final candidate passes it, with recorded fixture and artifact identities.
- `/readyz` proves registration cap or typed fallback before browser launch; `/livez` remains a distinct progress signal.
- Fresh clean Linux and Darwin checks pass on the same frozen SHA, and the authorised deployment proves identity, settle, soak, negative control, exact Filesystem user paths, and full watch retirement.
- Documentation and `docs/DONE/` record the before and after measurements, budget, and architecture before this queue is removed.
