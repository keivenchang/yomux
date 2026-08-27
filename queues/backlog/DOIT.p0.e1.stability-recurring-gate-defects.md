# DOIT.p0.e1.stability-recurring-gate-defects.md - The gate is red because of real races; the first was focused-control paint deferral

## Priority

P0. The gate reaches all-lane-green on 34.7% of full runs. An independent audit (`gate-audit-01`, 2026-08-24) refuted the theory that this is contention and instead found **named product defects recurring inside the failing population across different SHAs**. This queue owns those defects. Until they are fixed, no scheduling or serialization experiment in `DOIT.p0.e2.gate-tiering-and-serialization.md` can measure anything but these races, so this queue blocks that one.

## RELEASE RISK, found 2026-08-25 by `E3-E1EVIDENCE-22`: every closed defect's FIX is uncommitted

**The fixes that closed Defects 3, 4 and 5 do not exist at `HEAD`. They exist only in the working tree.** Verified independently by the coordinator with `git show HEAD:<file> | grep -c <symbol>` against the same `grep` on disk:

| file | symbol | at `HEAD` | in worktree |
| --- | --- | ---: | ---: |
| `tests/test_session_files.py` | `first_incomplete_forced_transition` | **0** | 4 |
| `static_src/js/yolomux/99_terminal_boot.js` | `reconcileDeferredSealedAutoApprove` | **0** | 2 |
| `tests/test_gate_agent_state.py` | `f6ConvergenceSatisfied` | **0** | 6 |

Defect 4's restored bounded invariant, Defect 5's deferred-payload reconciliation owner, and Defect 5's convergence predicate are all uncommitted, as are `yolomux_lib/infra/listener_census.py` and `tools/system_status_latency_probe.py`, which carry Defect 3's root-class correction.

**Consequence: losing this working tree loses the closures themselves, not merely their evidence, and a `docs/DONE/` record written now would describe fixes that are not in git history.** This is a larger risk than the `/tmp` citations that prompted the audit.

**Mitigated 2026-08-25 21:31 PT, and the mitigation was verified rather than assumed.** The complete uncommitted state is backed up to `/home/keivenc/dev/yolomux-e3-worktree-backup-20260825/` — a 380,516-byte patch plus a `files/` tree of raw copies plus the untracked scratch file, taken against `3d1fe4da8`. All three symbols are present in the patch at the expected counts, and **the patch was proven to apply cleanly to a fresh detached checkout at `3d1fe4da8`** before being trusted. That is a safety net, not a fix.

**The actual fix is integration**, which is already the plan: this working tree's content *is* the candidate, and landing it commits these closures. Until that happens, treat the working tree as the only live copy and do not reset, clean, pathless-stash, or broadly restore anything in it. **Re-take the backup after any material change to it.**

## Evidence durability, audited 2026-08-25 by `E3-E1EVIDENCE-22`

**`/tmp` here is not "transient eventually" - it is emptied on every boot.** Verified by the coordinator: `/usr/lib/tmpfiles.d/tmp.conf` contains `D /tmp 1777 root root 30d`, and `systemd-tmpfiles-clean.timer` is **active**. Type `D` empties the directory at boot; the timer separately ages contents out at 30 days; and the volume is ext4 at **84% used**, which is its own pressure. **The nearer trigger is the next reboot.**

**Five irreplaceable artifacts copied 2026-08-25 21:33 PT to `/home/keivenc/dev/yolomux-e3-evidence-20260825/`, 444 KB total.** The copy was verified, not assumed: `/tmp/v0717-task54.s848nzyu/listener-real-current.json` still hashes to `bd37f3c15564e6dd50e67101ac292b1436db8f13af813af757752a4206c0a94d` in its new home.

| Artifact | Why it cannot be regenerated |
| --- | --- |
| `v0717-task54.s848nzyu/listener-real-current.json` | Defect 3's only real-kernel `EACCES` evidence; required a same-UID non-dumpable child holding an inherited listener inode |
| `v0717-task52-evidence.rs2tZup3/` | the only surviving proof of `76 / 26 / 628+2`; the node selections were never recorded, so it cannot be regenerated even in principle |
| `v0716-task49/` | Defect 6's only isolated reproduction of the `rpc.py:910` transition |
| `v0716-p0e1-f6-classification-02/` | the 1/8 Defect 5 rate; the failure is a race and may not reproduce on demand |
| `v0716-p0e1-f6-pair-03/` | the 3/8 pair arm, same reasoning |

### CORRECTION 2026-08-25 by `E3-EVIDENCELOSS-51`: the observed loss is NOT the boot wipe, and it is the release procedure losing its own evidence

The `tmpfiles` facts above are accurate but they are **not** what actually destroyed the four missing check reports, and a `docs/DONE/` record must not blame ageing or reboots for them. Measured refutations, each independent:

| candidate cause | verdict |
| --- | --- |
| the 30-day age rule | **Refuted.** The newest missing run is 2026-08-25 12:19:44, ~11 hours before it was looked for. Only 1 of 785 reports is older than 24 days. |
| a reboot emptying `/tmp` | **Refuted as the cause of THESE four losses, but do not read it as safety.** No reboot has occurred in 24 days (`up 3 weeks, 3 days`), so no reboot can have destroyed reports created about 11 hours ago. **The original wording here was backwards and is corrected:** `uptime -s` is `2026-08-01 13:28:34` and the oldest surviving corpus run is `2026-08-01 13:44` - the corpus begins **16 minutes after the last boot**. That is the signature of a boot wipe having already happened once, not evidence that boot wipes do not occur. **The risk to the corpus is unobserved, not refuted, and the next reboot destroys all 5 GB of it.** |
| a cleanup path in `tools/check.py` | **Refuted.** One non-test hit for `yolomux-check-runs` repo-wide (`check.py:1066`, the path constructor). No `unlink`, `rmtree`, rotation, retention cap or prune touches the directory anywhere. |
| `--performance-report <path>` diverting the write | **Refuted.** `_tmp_only_path` (`check.py:608`) validates without rewriting. |
| the host was idle during the gaps | **Refuted decisively.** `/tmp` received 3,546 and 4,391 entries in the two gaps - *more* than a comparable busy window - including a gate log written inside one. |

**The pattern is sharp: every one of the 27 reports a gate log ever named still exists; all 4 missing ones are release-certification runs of a "clean detached exact-SHA checkout" for v0.7.15 and v0.7.16.** Their footprint is absent from **two** top-level directories - no `cert-` directory and no `.artifacts` bundle either, though artifact retention is unconditional and 31 of 34 runs in that window left one. The reports were genuinely written (`check.py` prints the path only after the write), on correct paths, on **a `/tmp` that is not this host's**.

**Conclusion: this is a procedure defect, not a `tools/check.py` defect.** The tooling that creates the clean detached checkout is not in this repository. The certification runs execute in a context - private mount namespace, or `docker run --rm`, whose `/tmp` is not mounted through - and its teardown takes the evidence with it. **A release that cites evidence written inside a disposable context is citing something nobody can ever re-read.**

**Cheap probe that settles which context, not yet run:** inside the release clean-detached procedure and before it exits, record `readlink -f /proc/self/root`, `stat -c %d /tmp`, and whether `/tmp/yolomux-check-runs` is the same inode as the host's. A different device number proves the namespace case immediately. Not run here because it requires a full gate, which the resource freeze forbids.

**This outranks the archival item below.** A subset copied tomorrow will have exactly these holes, and the next certification adds another.

**Mitigated in part, 2026-08-25 23:23 PDT:** the certification corpus was snapshotted to `/home/keivenc/dev/yolomux-e3-evidence-20260825/certification-corpus-snapshot-20260825/` - 1,941 files, 21,212,387 bytes, tree hash `c2804b31a8a76ae2c708fae2d291c65d9622535a2ee637da8cb3a3dc4c791d76` identical on both sides, taken in 0.42 s. It preserves **7 of the 11** cited `cert-` directories. The snapshot cannot restore what was already gone.


**STANDING RISK, deferred by decision and needing an owner: `/tmp/yolomux-check-runs/` is 5.0 GB** and is the 200-run historical corpus behind **every denominator in this queue** - the 2/163, 3/117 and 4/117 populations - and it is cited by `DOIT.p0.e2` as well. Its claims are **not** re-derivable: this queue's own text says the mixed historical subjects cannot be retrofitted. It sits behind the same boot-wiped path. It was **not** copied because 5 GB exceeds the active disk freeze protecting the resource baseline and the host is at 84%. **Schedule the copy in the post-window batch and decide once for both queues.**

### The approved corpus-subset invocation, recorded here 2026-08-25 because it was cited before it was written

`E3-MANIFEST-55` correctly reported that no `B+` plan and no `420 MB` figure existed anywhere in `queues/` or `STATUS-REPORT.md`. **That was a coordinator error: the plan was accepted from `E3-EVIDENCELOSS-51` and cited to lanes as if it were already on disk.** It is written down now so nobody has to guess it. Run after 04:00 PT: one read pass over 5.2 GB, about 420 MB written. Steps 1 and 2 copy byte-for-byte; step 3 writes a verdict-only projection of each remaining large report.

1. Small reports under 5 MB and every `.artifacts` bundle, copied byte-for-byte, about 216 MB.
2. The 13 large reports cited by name in the release documents, copied byte-for-byte, about 190 MB. Derive the name list by grepping the release `.md` files for `check-<17 digits>-<n>.json` and sorting unique.
3. Every remaining report over 5 MB, re-serialised with `steps[].test_durations` removed from each lane step and a `_verdict_only` marker added, about 14 MB.
4. Verify with a file count and a byte count against `find` and `du -sb`.

Destination `/home/keivenc/dev/yolomux-e3-evidence-20260825/check-runs-snapshot-20260826`, source `/tmp/yolomux-check-runs`. The exact script as accepted from `E3-EVIDENCELOSS-51` is retained verbatim in the supervision log at `/home/keivenc/dev/agent-comm/results/20260825-p0-e3-e1-v0717-supervision.md`; copy it from there rather than retyping it.

**Why this shape, measured in `E3-CORPUS-47`:** 785 reports hold 5,203.5 MB and the 31 artifact bundles hold 167.0 MB, total 5,370.5 MB. The 365 reports over 5 MB carry **99.1%** of all bytes, and inside them one field - `steps[].test_durations`, 52,800 entries in a sampled run - is **99.65% to 99.89%** of each file. The verdict surface a run-count denominator actually needs is **37.2 KB per run**, so all 365 project down to 13.9 MB. **Precondition, and it has been checked: no cited claim depends on a per-test duration** - all 63 timing citations in the release documents are per-lane wall times, and the only `--durations` mention in any `.md` describes the mechanism rather than quoting a value.


**Two cited test counts cannot be re-run, and a `docs/DONE/` record must not pretend otherwise.** The retained logs contain exactly the cited numbers - `76 passed in 7.78s`, `26 passed in 11.72s`, `628 passed, 2 skipped in 236.57s` - so the figures are genuine. But **the logs record only pytest's output, never the command**, and this queue never names "core" or the seven owners; `tools/check.py` has no lane called `core`. **CORRECTED 2026-08-25: the six-name list previously written here was itself wrong in two further ways and must not be quoted.** Read from `tools.test_plan.LANE_SPECS` and confirmed independently by the coordinator, the lanes are exactly twelve: `py-compile`, `static`, `node-syntax`, `node-layout`, `pytest`, `pytest-boot`, `pytest-browser`, `pytest-e2e`, `pytest-gate-serial`, `pytest-unit`, `pytest-socket`, `whitespace`. The non-browser lane is named **`pytest`**, not `pytest-nonbrowser`; and **`pytest-browser-golden` is not a lane at all** - it is a step inside `pytest-browser`, whose `resolved_lane_step_ids` returns `("pytest-boot", "pytest-browser", "pytest-browser-golden")`. Cite lane names from `LANE_SPECS`, never from a prose list in a queue or an audit. **This is the same gap Audit 13 filed as "record exact commands and exit codes in focused logs", and it was evidently not closed for these two.** Only the architecture count reproduces on demand: **26 passed, rc 0**, via `python3 -m pytest tests/test_architecture_budgets.py -p no:randomly -q`. When the DONE record is written, cite the reproducible count with its command and cite the other two as retained-log evidence with their path, not as re-runnable results.

Per-owner counts have all **grown** since their citing audits, which is consistent with later tasks adding coverage and is not a defect: `test_listener_census.py` **54 passed** (cited 50, then 49/49); `test_system_status_latency_probe.py` **16 passed, 1 skipped** (cited 14 plus one environment skip); `test_system_status_latency_tool.py` **22 passed** (cited 13). All rc 0. **No cited per-owner count is still current**, so quote today's numbers in the DONE record rather than the audit-era ones.

## Integration package, measured 2026-08-25 23:30-23:40 PT by `E3-RECHECK-38` on composition `6c726f96b`

Base `3d1fe4da8`, 30 distinct commits above it, base patch clean across 32 paths. **Every number here is from ONE composition**, with the fast rungs re-run after rungs 5 and 7 so nothing is quoted from a slightly older tree - which mattered, because rung 1 moved 361 to 368 between them.

| Rung | Result | Verdict |
| --- | --- | --- |
| 1 - storage/ledger/service/batched/prune | 368 passed, 46.43s | green |
| 2 - pricing/usage/http/overlay/malloc/procperf/defect2/materializer/migration/collectors/v7v8/storm/serviceperf | 329 passed, 2 skipped, 26.87s | green |
| 3 - Node units | 2/2 files, 0 failed | green |
| 4 - `python3 tools/static_build.py --check` | exit 0 | green |
| 5 - `python3 tools/architecture_budgets.py --manifest tests/fixtures/architecture_budgets/v1.json` | **22 violations, exit 1** | expected red until the keys are applied |
| 6 - `test_check_runner.py::test_python_imports_are_module_scoped` | 1 passed, 11.56s | green, the old blocker is closed |
| 7 - seven-owner focused set | **1 failed, 1013 passed, 2 skipped**, 288.37s | red on one node |

### The lane-spec census: the delta is node ids, not files, and that is why it was misrouted twice

**File level has NO delta.** `discover_pytest_phase_files()` already yields `tests/test_stats_current_service_performance.py` and always did; the catalog was correct before any of this. Files-yielded-but-should-not is empty and files-should-yield-but-does-not is empty. **There is no file list to edit** - a coordinator routed this fix to `tools/check.py` and then to a branch, and both were wrong for this reason.

**The entire delta is the `gate_serial_nodes` literal at `tests/test_check_runner.py:275`.** The test builds its left side by walking the five `gate_serial` files with `test_catalog.test_definitions()`; only the right side is a literal, and only that literal is stale. Discovered 14, literal 9, **5 additions, 0 removals**, all five in one module:

```
tests/test_stats_current_service_performance.py::test_batched_recording_holds_the_joint_cost_ceilings
tests/test_stats_current_service_performance.py::test_per_fact_commits_breach_the_ceiling_that_batching_clears
tests/test_stats_current_service_performance.py::test_recording_facts_holds_the_joint_cost_ceilings_on_a_production_store
tests/test_stats_current_service_performance.py::test_the_probe_still_covers_the_products_real_append_path
tests/test_stats_current_service_performance.py::test_whole_history_cold_start_breaches_the_peak_ceiling
```

The reverse direction is genuinely empty - the renamed node an earlier audit worried about does not survive into this composition. **This is the single deterministic red in rungs 1-7**: `test_lane_specs_are_the_one_owner_of_names_defaults_and_shared_steps`, failing at `tests/test_check_runner.py:277`.

**It is discovered, not declared, so it is only valid for the tree that lands.** Re-derive on the final tree before pasting:

```python
from tools import test_catalog
sorted({n for rel in test_catalog.PYTEST_PHASE_FILES["gate_serial"]
        for n, ph in test_catalog.test_definitions(ROOT / rel) if ph == "gate_serial"})
```

**Two of those five nodes will SKIP** unless `YOLOMUX_COST_GATE_STORE` points at a copy of a production-sized store, which the freeze forbids. Not a blocker, but do not read a green run as coverage of them.

### The architecture ratchet moves under you - demonstrated four times in 105 minutes

22 keys, no shrinks, no removals. `extension_families`, `lane_ownership`, `partial_global_writes`, `source_text_assertions`, `test_to_test_imports` and `manifest_version` were all diffed and are unchanged. But **five values moved on every single measurement**:

| Key | 21:53 | 22:52 | 23:06 | 23:36 |
| --- | ---: | ---: | ---: | ---: |
| `file_lines yolomux_lib/stats_current/service.py` | 5460 | 5552 | 5575 | **5665** |
| `test_owner_lines tests/test_stats_batched_persistence.py` | 299 | 486 | 528 | **851** |
| `class_budgets ...StatsCurrentService.self_fields` | 141 | 142 | 142 | **145** |
| `test_owner_lines tests/test_stats_current_service_performance.py` | - | 307 | 307 | **326** |
| `class_budgets ...StatsCurrentService.methods` | 99 | 100 | 100 | **100** |

Thirteen values were stable across all four runs; five were not. **Regenerate the keys from the composed tree immediately before writing `v1.json`, and never carry them from a branch or from an earlier measurement.** At least one of the five was already stale when measured, because `batch-persistence` moved to `c4811420b` afterwards. Two further adjustments cannot be copied at all: pasting the census block pushes `tests/test_check_runner.py` past 3253 by five lines, and `wq/v0717-e3-statsd-health`'s `2738c1bfe` adds `tests/test_stats_current_health.py` (+320) and 424 lines to `http.py`, which will add at least one more key.

### `pending-overlay` exclusion is provably a no-op, shown three ways

The branch is one commit adding one file, `tests/test_stats_pending_overlay_reproducer.py`. That file is byte-identical on `batch-persistence` (482 lines, same hash). Strongest form: **composing with and without the branch yields the same git tree object**, `d533e10238db948af5160c6f50ca31e3fd0fa79c` both ways. Nothing is being kept out and nothing needs to be.

### The `test_app.py` intermittent, classified but not attributed

`tests/test_app.py::test_auto_approve_roster_uses_live_pane_working_signal` failed once in **2 identical full-rung runs** and passed **3 of 3** isolated. `tests/test_app.py` is base-patch content and **no commit in the composition touches it**, so it is **PRE-EXISTING, not composition-caused**. That is intermittency, not cause: order-dependence and load-dependence are not separated, and doing so needs a quiet host. **A one-in-two flake in the non-browser lane will hit the landing gate roughly half the time.** Owner: `E3-APPCLASSIFY-52`. Do not green it by retry, serialization, sleeps, or weaker scope - re-rolling until green is how a real defect becomes a permanent tax.


## The `test_app.py` gate flake is a NAMED product defect, classified 2026-08-25 by `E3-APPCLASSIFY-52`. Fix filed to `DOIT.p1.e5.backend-lifetime-supervision.md`; it is not e1's to implement

**Verdict: PRE-EXISTING, order-dependent, and NOT parallel-only.** This is the failure that made `tests/test_app.py::test_auto_approve_roster_uses_live_pane_working_signal` fail 1 of 2 identical full-rung runs while passing 3 of 3 isolated. The isolated reruns passed because they selected the target **without its predecessor**, not because load was absent.

**Reproduced serially, in one process, with no xdist, no concurrency and no load:**

```
tests/test_app.py::test_session_scoped_endpoints_refresh_before_unknown_session_guard
tests/test_app.py::test_auto_approve_roster_uses_live_pane_working_signal
  -> 10 serial runs, one process:  9 passed, 1 FAILED
     E  AssertionError: assert [('5', '6'), ('new',)] == [('5', '6')]

target alone, 10 serial runs -> 10 passed, 0 failed
```

The failing run reproduces the production assertion character for character, including the `('new',)` element. **The predecessor is necessary and sufficient; parallelism plays no part.**

### The named conflicting mutable resource

**`yolomux_lib.app.discover_sessions`, a module-level global bound at `yolomux_lib/app.py:186`.** Two owners cross a test boundary on it:

| role | location | what it does |
| --- | --- | --- |
| writer | `tests/test_app.py:3863` | `monkeypatch.setattr(app_module, "discover_sessions", lambda sessions: ...)` recording every call |
| leaked reader | `yolomux_lib/app.py:13228` | `sessions, errors = discover_sessions(self.sessions)`, called by a daemon thread owned by the **previous** test's app instance |

The predecessor constructs a real app at `tests/test_app.py:3814`, leaves `self.sessions == ["new"]`, and tears down **only** the control server at `:3830`. The worker it never reaps is spawned at `yolomux_lib/app.py:11112` as `threading.Thread(target=run, daemon=True)`. Captured stack of the offending call:

```
UNINVITED CALL from thread 'Thread-1 (run)' with sessions=('new',)
    yolomux_lib/app.py:11106  in run
    yolomux_lib/app.py:11149  in refresh_transcripts_payload_cache
    yolomux_lib/app.py:13455  in build_transcripts_payload
    yolomux_lib/app.py:13228  in build_session_metadata_payload
```

**In one sentence: the predecessor's transcripts-payload rebuild worker outlives the test whose app owns it, and when the next test installs a recording lambda over the module global, that orphan calls the recorder with its own app's session list and appends an element the next test never asked for.**

### Why the obvious fix is not a fix, measured

"Make the tests call the real teardown" **reduces the probability and does not remove the defect.** `TmuxWebtermApp.stop_auto_approve_all()` (`yolomux_lib/app.py:17586`) stops nine subsystems and joins none of the transcripts-payload workers. Five runs each:

```
teardown = control_server.stop()      2/5 leaked 'Thread-1 (run)' and produced ('new',)
teardown = stop_auto_approve_all()    1/5 leaked 'Thread-1 (run)' and produced ('new',)
```

**And the class is 390 sites wide, not two.** `tests/test_app.py` alone holds 403 `TmuxWebtermApp(` constructions, 390 `control_server.stop()` and 3 `stop_auto_approve_all()`; suite-wide it is about 560 constructions across 23 files with the same shape. Fixing the one adjacency that happens to be armed today leaves every other one armed.

### The real fix, specified and deliberately not landed

1. `TmuxWebtermApp` must own the lifetime of the worker spawned at `yolomux_lib/app.py:11112`. `begin_transcripts_payload_work` / `finish_transcripts_payload_work` (`:10974`, `:11041`) already track it and nothing joins it at shutdown; add a bounded-timeout reap to `stop_auto_approve_all()`.
2. Give the tests **one** owner for that teardown - an autouse fixture in `tests/conftest.py` reaping every app constructed during a test - rather than editing 390 call sites. That needs a `weakref.WeakSet` registry in `TmuxWebtermApp.__init__`, which is the shared parent this class currently lacks.

**Deferred for a hazard, not a preference, and the coordinator accepts the reasoning.** Committing needed a new branch cut in a worktree checked out on `fix/v0716-gate-serial-lanes` with 33 dirty files that several lanes were actively working in; switching branches there would have moved the branch out from under every other lane mid-commit. The change also touches a 17,600-line shared product file plus a suite-wide conftest, during a resource freeze, on a tree that lane does not own. **That risk is larger than the defect.**

**Owner: `DOIT.p1.e5.backend-lifetime-supervision.md`** - it is that queue's exact shape, an owner that starts a background worker with no path that reaps it. Parity test on landing: the pair loop above run 30 times, expecting 30 passes.

**Nothing was suppressed.** No test serialized, no concurrency lowered, no retry, no sleep, no service stopped, no assertion weakened, nothing marked flaky. The reproduction is preserved at `/home/keivenc/dev/yolomux-e3-evidence-20260825/APPCLASSIFY-52-order-race-repro/` (6.4 MB) with the full report beside it, because `/tmp` here is boot-wiped.

**Consequence for the landing gate, stated plainly: this will fire on roughly 1 run in 10 and it is not ours to fix tonight.** A red on this node is a known pre-existing order race, not a regression from this release - but **do not re-roll the gate until it passes and call that green**, because that is how a real defect becomes a permanent tax. Record the node, cite this section, and move on.

**Still unestablished:** why the race is roughly 1 in 10 rather than deterministic; whether other adjacent pairs among the 390 leak sites are already armed, since only this pair was proven; and whether load raises the rate, which is plausible but was not measured and does not affect the classification.


## Defect 2 attribution: the rate design is INERT and the item is GENUINELY BLOCKED for v0.7.17. Measured 2026-08-26 by `E3-DEFECT2PREP-61`

**The harness is ready and proven. The experiment is not runnable inside any achievable window.** `tests/test_defect2_harness.py` passes 26 of 26, and the extractor was driven through its CLI on the real recorded occurrence #42 rather than only self-tested. Without telemetry it **refuses to guess** and names what is missing (`streamEvidence was not attached`, `no server log ring payload was retained`); with `TELEMETRY-09`'s instrumentation attached it returns `is_defect_2: True`, `first_bad_boundary: transport_or_connection_closed`, a bounded **4.0 s** silence window and an ordered timeline. **The instrument works. What it lacks is an occurrence carrying the telemetry.**

### The arithmetic, independently re-derived rather than restated

| quantity | queue | recomputed | agree |
| --- | ---: | ---: | --- |
| `p0 = 2/163` | 1.22699% | 1.22699% | yes |
| `P(zero in 20)` | 0.78122 | 0.78121 | yes |
| smallest significant 20-v-20 split | 5/20 vs 0/20, p=0.023562 | same, exact | yes |
| attempts/arm for a 2x effect | 1,883 | 1,880 | yes |

**Minimum detectable effect at 20 attempts per arm and 80% power is a rate ratio of about 28x - a ~34% per-attempt failure rate against a 1.227% base.** Detecting a plausible 2x needs 1,880 attempts per arm, **322.8 h per arm and 26.9 days for both** at the measured 617.918569 s per full gate. **The rate design cannot detect the effect at issue and no achievable wall clock makes it able to. Do not run it and do not report a rate.** One correction that changes nothing: the queue says one control event raises the bar to 7 failures; one-sided, **6** is already significant at p = 0.045738.

### COORDINATOR DECISION, recorded 2026-08-25 23:59 PT: drop the arms. Run the released configuration only.

The item's own words are *"driven by first-transition telemetry, **not** by a failure-rate comparison"*, and the closure evidence is **one instrumented occurrence**, not N attempts. The two arms were vestigial from the design the item already disowns, and keeping them caused two problems that vanish without them:

- **The treatment arm turns on code that is deliberately shipped off.** `ARMS = {"control_synchronous": "0", "treatment_batched": "10.0"}` sets `YOLOMUX_STATS_APPEND_FLUSH_SECONDS`, and `10.0` enables batched persistence - which ships **disabled and fail-closed** by Keiven's decision because of the unresolved `source_generation` collision, and which fails two named ring correctness tests at any flush > 0. Every treatment attempt would run a full parallel gate red by construction, for a reason unrelated to Defect 2, and the arms would then differ in observable behaviour **beyond the one named substitution** - the exact property the arm definition forbids. At a 1.227% base rate any such confound is larger than the effect.
- **A treatment result would describe a configuration that is not being released.**

**So the release-relevant question is answered by the control arm alone**, and the lane's own falsification list already says so: an occurrence reproducing with the arm observed as `0` establishes that the persistence owner is not the cause and the classification is independent. The treatment comparison moves to `DOIT.p1.e4.statsd-batched-persistence-generation-key.md`, where the collision that blocks it already lives. **Precondition 3 - predeclaring the two failing ring nodes as excluded from both arms - is withdrawn as unnecessary rather than decided.**

### Why it is still blocked, and this is the number that decides it

At a 1.227% per-attempt occurrence rate and 617.918569 s per full parallel gate, catching **one** instrumented occurrence takes **81.5 attempts, about 14.0 hours of continuous gate running.** That is not a scheduling inconvenience; it is longer than the release window and it drives real browsers throughout.

**Therefore this checkbox stays open and v0.7.17 lands with it open.** That is the honest state: the instrument is built and proven, the attribution path is specified and falsifiable, and the only missing input is an event that occurs about once every 14 hours of gate time.

### KEIVEN'S DECISION, recorded 2026-08-25 23:59 PT: land v0.7.17 with this item open

**Timestamp corrected 2026-08-26 by `E3-PRELANDAUDIT-66`, and the correction matters more than the minute.** This heading originally read `2026-08-26 00:20 PT`, which is **twenty-one minutes after the file was actually written** (`stat` gives 2026-08-25 23:59:38.501 PT). **A coordinator wrote a future time onto a named human's release authorization.** The decision itself is real - Keiven was presented with the straight choice below and chose to land - but the exact minute the answer arrived was not retained, so the only defensible timestamp is when the record was written. **A reader auditing this release cannot distinguish a mistyped clock from a pre-dated authorization, and that is the defect**: every other claim in this record is machine-checkable and was checked, while this one is not.

Presented as a straight choice - land at 8 of 9 with Defect 2 documented as blocked, or hold the release 14-plus hours to hunt the occurrence. **Keiven chose to land.** The two options were put in those terms, with the 14-hour figure and its geometric-wait caveat stated before the choice was made.

So this checkbox is **not** deferred quietly and **not** redefined to make a percentage look better. It stays open, in a released queue, with the reason measured and the closure path written down: the instrument exists and is proven, the one comparison that closes it is specified, and the only missing input is an event that arrives about once per 14 hours of gate time. The occurrence hunt runs after the release, on its own schedule, against the composed tree.

**The 14-hour figure is an expectation, not a bound.** At a 1.227% per-attempt rate the wait is geometric, so it can easily run to twice that. Whoever picks this up should plan for a long unattended run rather than a session.

### What the attribution will read, when an occurrence arrives

Subject `tests/test_browser_stats_widen.py::test_real_stats_cpu_value_round_trips_through_rpc_and_rendered_svg`, running in **`pytest-e2e`**, not `pytest-browser`. Predicate exactly `YO!stats stream generation stalled for more than 3s`; the retired `cpuAxisMax == 100` mismatch enters no numerator. **No reduced command is a reproducer** - the ordered predecessor pair (8/8), the whole file (3/3) and the solo target (8/8) have all failed to reproduce, so the envelope is the canonical full parallel gate at `browser=5, e2e=3, nonbrowser=8`. The classification is one comparison: **`deliverySequence` against `acceptedDeltaSequence` across the silence window.** Advancing delivery with flat acceptance is client-side rejection, which makes the `ready`-path throw at `84_stats_current.js:1141-1168` the first bad transition and exonerates statsd; flat delivery with server frames produced puts the loss in transport. Either outcome closes the item.

### The container plumbing hole is FIXED, and the fix was observed rather than read

The queue records `admitted: False | reason: arm_env_not_forwarded_into_container`. **That is no longer the reason.** The batching lane added the sixth allowlist name in the same change that introduced the variable. Proven by running the documented control - the same command twice with only the allowlist differing, observed **inside** the container:

```
five names : IN-CONTAINER arm=[<ABSENT>]  in_container=[1]
six names  : IN-CONTAINER arm=[10.0]      in_container=[1]
```

**The silent null is now measured rather than inferred:** with five names the subject never sees the variable, both arms run identical code, and the experiment reports a clean nothing. Container tag `yolomux-test:2fe10ac2d641` matches both retained occurrence reports exactly, so the image has not drifted.

**But the harness and the fix are on different branches.** On `wq/v0717-e3-defect2-telemetry` the preflight now fails with `no_arm_env_name`, because `APPEND_FLUSH_ENV_NAME` is owned by `wq/v0717-e3-batch-persistence`. The experiment needs a composed tree carrying both; that is an integration precondition, not a defect.

### EVIDENCE LOSS, precondition 9: the two retained occurrence artifacts cannot be found

`find` and `grep -rl "jsf-dce0de9d"` across `~/dev`, `~/notes` and `/tmp` return nothing. **The queue transcribes specific values from those two occurrences and those citations can no longer be re-derived from the artifacts.** This is the same class as the four missing certification reports recorded above, and it now touches the evidence behind an open P0 item. Treat the transcribed values as the surviving record and say so wherever they are cited.


## KEIVEN'S DECISION, recorded 2026-08-26 00:12 PT: commit the scoped queue documents. The release record goes into git.

**The timestamp above is the minute this file was written, verified against `TZ=America/Los_Angeles date` immediately before writing it.** That practice exists because a coordinator wrote a future time onto Keiven's previous authorization tonight and `E3-PRELANDAUDIT-66` caught it.

### The problem, as measured

`.gitignore:9` is `DOIT*.md`, under the comment *"Scratch worklists - never committed (kept local only)"*. **The rule is already applied inconsistently**, and that inconsistency is what forced the question:

| queue | git state |
| --- | --- |
| `DOIT.p0.e1.stability-recurring-gate-defects.md` | untracked + ignored |
| `DOIT.p0.e2.gate-tiering-and-serialization.md` | untracked + ignored |
| `DOIT.p0.e3.statsd-resource-bounds.md` | untracked + ignored |
| `DOIT.p1.e4.statsd-batched-persistence-generation-key.md` | untracked + ignored |
| `DOIT.p1.e4.watchd-native-watch-bounds.md` | untracked + ignored |
| `DOIT.p1.e3.filesystem-delete-lane-split.md` | **tracked** |
| `DOIT.p1.e5.backend-lifetime-supervision.md` | **tracked** |
| `DOIT.p2.e2.descriptor-residuals.md` | **tracked** |
| `DOIT.p2.e2.filesystem-descriptor-authorization.md` | **tracked** |

**A tracked `STATUS-REPORT.md` cites, as its primary evidence, five documents git cannot see - including both P0 subjects and the two documents carrying the release exception.** Goal 6 says *archive the queues*; for five of eight scoped queues, archiving could not have meant committing them.

### The decision

**Commit them at integration**, matching the four DOIT files already tracked. Anyone reviewing v0.7.17 from git alone must be able to read the evidence the release record cites; a record whose sources live only on one disk is not a record. **Do not edit `.gitignore` to achieve this** - force-add the scoped files explicitly, so the override is visible in the commit rather than hidden in a rule change.

**Loss risk was already covered and this is not what motivated the decision:** the 2026-08-26 00:02 backup holds 45 files including the gitignored queues in `ignored-queues/`, with the restore proven by applying the patch rather than only `--check`. The decision is about reviewability, not durability.


## Cited evidence commits are now anchored by tag, 2026-08-26 00:14 PT (`E3-ANCHORREFS-75`)

`E3-PRELANDAUDIT-66` found that **eight commits cited across both P0 queues were unreachable from every branch and tag**, so the next `git gc` would have deleted them. That includes `6c726f96b`, which anchors the strongest classification evidence in this release - the full-rung reproduction and the complete rungs 1-7 measurement. **Unreachable evidence does not become wrong; it becomes unfalsifiable, which is worse.**

**Nothing had been collected. All eight were still alive and all eight are now anchored**, each verified unreachable before tagging and `git tag --contains` confirmed non-empty after:

| SHA | tag | cited at |
| --- | --- | --- |
| `6c726f96b` | `evidence/v0717-composition-6c726f96b` | `DOIT.p0.e1:82` |
| `24e2d8a72` | `evidence/v0717-composition-24e2d8a72` | `DOIT.p0.e3:260` |
| `58bd95579` | `evidence/v0717-composition-58bd95579` | `DOIT.p0.e3:594` |
| `17c218feff94` | `evidence/v0717-candidate-17c218feff` | `DOIT.p0.e1:373` |
| `4a0e94be2` | `evidence/v0717-candidate-4a0e94be2` | `DOIT.p0.e1:389` |
| `8adc6108` | `evidence/v0717-occurrence-8adc61081` | `DOIT.p0.e3:554` |
| `c69c50ab` | `evidence/v0717-historical-c69c50ab1` | `DOIT.p0.e1` |
| `926e4a1662` | `evidence/v0717-historical-926e4a166` | `DOIT.p0.e1` |

Three role names rather than one, so a reader can tell a throwaway integration composition from a former release candidate without opening the annotation. `8adc6108` is named `occurrence` because `DOIT.p0.e3:554` identifies it as the subject SHA both known Defect 2 occurrences ran against - a more useful role than "historical". **Tags are local and unpushed; publishing them is a separate decision.**

**This is the third distinct evidence-loss class found tonight**, after the four missing release-certification reports and the two unlocatable Defect 2 occurrence artifacts. The pattern is consistent: evidence is cited from a durable document and stored somewhere that expires.


## Integration rehearsal on current tips, 2026-08-26 (`E3-RECOMPOSE-77`), and one hazard premise corrected

Run because the 23:40 manifest was already stale: **at least nine commits had landed since**, and an inventory drafted against a moving tree has now gone wrong four times in one night. The rehearsal was done in a throwaway worktree on `rehearsal/v0717-recompose-77`; nothing was pushed, merged into a real branch, or committed outside it.

**Branch set discovered rather than supplied: 15 branches, 42 distinct commits, and `3d1fe4da8` is an ancestor of every one, so no rebase is needed.** `wq/v0717-e3-boundary-flush` had moved from zero commits to five (tip `2562eef44`) since the manifest named it empty - exactly the re-check its author warned would be needed.

**One conflict, and it is add/add rather than semantic.** Merging `wq/v0717-e3-cursor-validation` collides on one hunk each in `yolomux_lib/stats_current/storage.py` and `tests/test_stats_current_storage.py`: the batch lane inserts `_coverage_conflict_reason` and the cursor lane inserts `_require_exact_fixed_rows` at the same point, and git cannot choose an order. **Neither side modifies the other's lines.** The rehearsing lane declined to resolve it, in the right words - *"looks like a keep-both is exactly the judgement that should not be made by the lane that noticed it"*. **Coordinator decision: keep both, ordered for readability, with both owners' suites run on the resolved tree and every added symbol verified present by name rather than inferred from a clean merge.** Holding that branch out, **the remaining ten compose cleanly at `8b39e24a0`**.

**The 32-path base patch applies to the composed tree at exit 0 and hazard H1 does not bite:** `static/yolomux.js` has two independent writers, the base patch and `defect2-telemetry`, and `python3 tools/static_build.py --check` returns **exit 0** afterwards. The generated bundle is self-consistent with no regeneration and no hand resolution.

**Architecture ratchet regenerated from the composed tree: 27 keys, nothing carried.** The values moved again - `service.py` `file_lines` is now **5800** and the two `class_budgets` keys are **93 -> 103** and **134 -> 146**, against 99 and 141 on an earlier composition. **This set will move once `cursor-validation` lands**, since it touches two keys already in the list, so it must be regenerated once more after the resolution rather than adjusted by hand.

**Lane-spec census: 5 additions, 0 removals, symmetric difference unchanged - and that is a useful NEGATIVE result.** Two new test files landed since the last measurement and the expectation was that the delta would grow; it did not, because **neither new file adds a `gate_serial` node**. All five additions still come from `tests/test_stats_current_service_performance.py`, so **the literal update at `tests/test_check_runner.py:277` is exactly those five lines and no larger.**

**What a full gate would see, without running one: 126 changed files** - 27 changed test files in `nonbrowser`, 2 in `browser` (`test_gate_agent_state.py`, `test_live_browser_soak.py`), 1 in `e2e` (`test_browser_stats_widen.py`), 1 in `gate_serial`. **Two known failures will fire and both land in the non-browser lane**, so read the node id before concluding which: the census node, deterministic until the literal is updated; and `tests/test_app.py::test_auto_approve_roster_uses_live_pane_working_signal` at roughly 1 run in 10, pre-existing, reaching the composition only through the base patch since no composed commit touches that file. **Do not retry it away.**

### Hazard H5's premise is wrong, and it is corrected here rather than carried forward

H5 records that `tests/test_gate_agent_state.py` *"drives a real browser but carries no `browser` marker"*. **It does carry them - 6 at base `3d1fe4da8` and 7 after the base patch adds one.** The file is **mixed**: `test_definitions` reports **7 browser nodes and 11 non-browser nodes**, and the eleven `test_f6_*` record-shape nodes are pure-Python assertions that legitimately belong in the non-browser lane. So the accurate description is **partially marked, not unmarked**, and on this evidence H5 does not describe a mis-laned browser test. **Its standing as a candidate contributor to quiescence failures should be re-examined rather than assumed.** Recorded as a correction to the premise, not as a verdict on the hazard - nothing was reproduced against the failing predicate.


## A SECOND isolation-green / rung-red node, found 2026-08-26 by `E3-SETTLE-82`. Not investigated, and it will meet the landing gate

`tests/test_system_status_latency_tool.py::test_standalone_probe_drives_an_ephemeral_authenticated_daemon` is **22 of 22 green in isolation and failed 2 of the 3 full-rung parallel runs tonight** - the 17:54:33 run (`check-1787705029757837668-4108636`) and the 18:33:42 run (`check-1787707405958598287-1372922`), passing only in the 18:17:06 one.

**This is the same SHAPE as the `test_app.py` order race - isolation-green, rung-red - and no claim is made that it is the same cause.** Nobody has investigated it. It is recorded here because the release gate runs at full rung and this node has a measured 2-in-3 failure rate there, which is far worse than the order race's 1-in-10.

**Why it is recorded beside a green count rather than instead of one.** The release record quotes `22 passed, rc 0` for that file, and that number is correct and current - re-measured 2026-08-26 00:23 PT on the candidate working tree at exit 0. **A focused green on this file does not mean the file is stable at full rung**, and printing the clean count with no note invites exactly the misreading this queue warns about in the Defect 6 section: quoting the clean arm alone.

**Consequence for the 04:00 landing gate, stated plainly: there are now three known reds, not two.** The lane-spec census node, deterministic until the literal at `tests/test_check_runner.py:277` gains its five entries; the `test_app.py` order race at roughly 1 run in 10; and this node at roughly 2 runs in 3. **All three land in the non-browser lane, so read the node id before concluding which fired.** None of them may be retried away.

### Related: the per-owner counts are current, and depend on the working tree rather than on HEAD

Re-measured 2026-08-26 00:23 PT in the candidate tree at `3d1fe4da8`, all three files `M` and therefore **candidate-tree counts, not HEAD counts**: `tests/test_listener_census.py` **54 passed** (0.82s), `tests/test_system_status_latency_probe.py` **16 passed, 1 skipped** (0.46s), `tests/test_system_status_latency_tool.py` **22 passed** (9.24s), all exit 0, via `python3 -m pytest <file> -q -p no:cacheprovider`. **Every figure the release record carries matches exactly**, and the record's claim that no audit-era count is still current also holds - 50/49-49, 14+1 and 13 are all superseded. **A reader who checks out `3d1fe4da8` and re-runs will get different numbers**, so the counts must be dated and stated as working-tree measurements.

### The compaction figures are traceable after all, and durably so

An earlier cross-check reported the four reclaimable-space figures at the release record's compaction paragraph as untraceable. **That was a search limitation, not an evidence-loss class**, and the reporting lane said so plainly: it had searched the queues, the results directory and the evidence directory, and not branch content or commit messages. The figures - `3.600%`, `3.864%`, `3.929%`, `3.576%` reclaimable against `0.0000%` truly recoverable, the `74.09%` bulk-delete case, the `15.0%` threshold and the `3.008x` rewrite cost - are recorded in **four independent places on `wq/v0717-e3-compaction-guard` (`1e3422658`)**, three of them product source: the commit message; `yolomux_lib/stats_current/service.py:68` and `:75` as comments beside the guard; `yolomux_lib/stats_current/storage.py:2742`; and test docstrings at `tests/test_stats_current_storage.py:228-229` and `tests/test_stats_current_service.py:2047`. **The numbers live in the source of the change they justify, which is a better home than any report** - once `1e3422658` lands they are in the candidate tree, and until then they sit on a named branch ref rather than a dangling object. The release record should cite `1e3422658` and `service.py:68` so a reader knows where to look. **Still not established: whether the four source databases themselves are retained** - the figures are traceable but not independently re-derivable.

### H5's correction needs one further clause

The corrected premise - `tests/test_gate_agent_state.py` is **mixed, not unmarked** - was verified independently: **6 `browser` markers at HEAD and 0 explicit non-browser markers**, against **7 `browser` and 11 `no_browser`** in the candidate working tree, with 18 nodes collecting, exactly 7 + 11. **But the eleven `no_browser` markers exist only in the uncommitted base patch.** So the corrected H5 is a statement about the candidate tree and depends on that patch landing. **If the base patch is dropped or only partly composed, the lane assignment of the other twelve nodes is unstated again** - not with the original wrong premise, but as a genuinely open question.


## The second rung-red node is CLASSIFIED and ALREADY FIXED by the candidate, 2026-08-26 (`E3-PROBECLASSIFY-85`)

**`tests/test_system_status_latency_tool.py::test_standalone_probe_drives_an_ephemeral_authenticated_daemon` is PRE-EXISTING, and the working tree is what fixes it.** It fails on clean `HEAD 3d1fe4da8` **deterministically, 5 runs of 5**, with the exact production error string. **It is a different class from the `test_app.py` order race** - not a leaked thread, not a port, not a socket path, not a token, not a fixture lifetime - and the classifying lane started from the failure rather than from the previous answer, as instructed.

**What actually fails is not an assertion about latency.** Both retained failures are identical:

```
E  AssertionError: ('', 'ERROR: cannot enumerate file descriptors for Linux process 39841\n', [...])
E  assert 2 in {0, 1}
   tests/test_system_status_latency_tool.py:156
```

The daemon, the port and the auth cookie are all fine - the server is serving and the probe reached the point of validating the listener owner. The probe exits **2** because the listener census raised.

**The conflicting mutable resource is the HOST PROCESS TABLE - every process on the machine, not the daemon under test.** At `yolomux_lib/infra/listener_census.py:183-190`, HEAD's `proc_listener_pids` walks **every** entry in `/proc` by design - its own comment at `:178-180` says *"The TCP-table UID is the socket creator, but another UID can inherit or receive the FD. Scan every readable process."* A `FileNotFoundError`/`ProcessLookupError` is tolerated as a process exiting mid-scan, but **any other `OSError`, including a plain `EACCES` on a neighbour the kernel simply will not let us read, is fatal for the whole census.** So the census's success depends on the set of processes that exist and are readable at the instant it runs - and **every parallel pytest worker mutates that set** by starting and stopping daemons.

**The retained artifacts show exactly that.** Both failures tripped on a high-numbered transient neighbour rather than on init: container pid **39841** in the 17:54:33 run and **39227** in the 18:33:42 run, neither the subject nor PID 1. **That is why it is 2 in 3 rather than 3 in 3** - in isolation the container's process table is small and same-UID, so the census finds nothing it cannot read.

**Proven deterministic on this host, read-only.** The machine currently presents **558 of 913 processes returning `EACCES` on `/proc/<pid>/fd`**, four of them same-UID:

| arm | result |
| --- | --- |
| HEAD `3d1fe4da8` library | **RAISED, 5 of 5** - `ListenerCensusError: cannot enumerate file descriptors for Linux process 1`, caused by `PermissionError: [Errno 13] Permission denied: '/proc/1/fd'` |
| candidate working tree | **SUCCEEDED, 5 of 5** - returns the right listener `(2088133,)` in 0.08s with **559 degradations recorded**, each unreadable process captured as `ListenerDegradation(pid=..., stage='fd directory', errno_value=13, uid=..., scope='global visibility')` |

*(On this host it trips at PID 1, unreadable here. Inside the test container PID 1 **is** readable, which is why the container needs a foreign neighbour before it fails - intermittent there, deterministic here. Same defect, different process table.)*

**The fix is the uncommitted `yolomux_lib/infra/listener_census.py` change, +412/-75, which carries this queue's Defect 3 root-class correction.** It renames the entry point to `proc_listener_census` and classifies the error before deciding, recording unreadable processes as typed degradations and continuing instead of failing the census. **Nothing needs to be written for this item; the classifying lane wrote no code.**

### Consequence for the 04:00 landing gate: TWO known reds, not three

This node is **not** among them, because the candidate fixes it. What remains: the lane-spec census node, deterministic until the literal at `tests/test_check_runner.py:277` gains its five entries; and `tests/test_app.py::test_auto_approve_roster_uses_live_pane_working_signal` at roughly 1 run in 10. **Both land in the non-browser lane, so read the node id before concluding which fired, and do not retry either away.**

**A dependency worth stating: this fix exists only as an uncommitted file.** If the 32-path base patch is dropped or only partly composed, the 2-in-3 failure returns and it is deterministic on any host whose process table contains a neighbour we cannot read - which is every real host.

## Independent confirmation of the `test_app.py` classification (`E3-BLINDVERIFY-71`)

A findings-blind lane, given only the failing node and the retained run report and **explicitly forbidden from reading the first lane's scratch**, reached the same conclusion: **PRE-EXISTING**, the same named resource - the module-level `discover_sessions` at `yolomux_lib/app.py:186` - the same unreaped daemon thread spawned at `yolomux_lib/app.py:11112` by `start_transcripts_payload_refresh`, and the same call site at `yolomux_lib/app.py:13228` resolving the global **at call time**, so it reads whatever the currently running test has installed.

It added the chain the first classification did not capture: the neighbouring test at `tests/test_app.py:3813` patches the session list to `["new"]`; `@requires_known_session(refresh=True)` calls `refresh_sessions` (`app.py:9430`) then `apply_session_roster` (`app.py:9443`); membership changed, so `app.py:9455` starts the refresh thread; and the test's `finally` at `tests/test_app.py:3829` stops **only** `webapp.control_server`. Per-method reproduction in isolated processes shows exactly which scenarios leak after they finish - `events_payload` and `build_auto_approve_status` both record `('new',)` **after** the scenario ends, while three others record theirs inside it.

**Two independent classifications agree on file, line, thread and mechanism.** That is the standard for accepting a root cause here.


## Base-patch dependency map, 2026-08-26 (`E3-PATCHDEPS-93`) - verified by RECONSTRUCTION, not by `git apply --check`

**The patch is complete and faithful.** Proved by rebuilding the tree from `HEAD` plus the patch and comparing every file's SHA-256 three ways - `HEAD + patch`, the working tree, and the backup's `files/`. **31 of 32 byte-identical in both comparisons**, zero missing. That proves the patch's **post-image equals the tree**, which `--check` cannot.

**The single divergence is `STATUS-REPORT.md` and it is benign:** three lines - the `Updated:` stamp, the current history row, and the `progress-report-history` JSON - because the coordinator edited it after the backup was taken. **No code or test file has drifted from the patch.** Path reconciliation is exact: 32 in the patch, 32 tracked changes, `comm` finds zero in either direction; the untracked `test_session_files_narrowed.py` is correctly outside the patch and preserved separately; and the backup carries all **9** gitignored `DOIT*.md` under `ignored-queues/`, **which is the only reason those survive at all.** The `DOIT.p1.e5.memory-hog.md` deletion applies correctly and the file is absent from both.

**Sixteen load-bearing symbols verified by name in all three places**, including the eleven `@pytest.mark.no_browser` markers in `tests/test_gate_agent_state.py` at 11/11/11. Patch counts exceed tree counts where a hunk touches a symbol on several lines; that is expected, not a discrepancy.

### Group A - the listener census. THREE consumers, and one of them no queue had recorded

| path | lines | role |
| --- | ---: | --- |
| `yolomux_lib/infra/listener_census.py` | +361/-51 | the fix itself |
| `tools/system_status_latency_probe.py` | +127/-13 | consumer |
| **`tools/yostats_active_browser_window.py`** | **+3/-1** | **consumer - NO QUEUE MENTIONED THIS FILE** |
| `tests/test_listener_census.py` | +562/-120 | tests |
| `tests/test_system_status_latency_tool.py` | +179/-11 | tests |
| `tests/test_system_status_latency_probe.py` | +71/-0 | tests |
| `tests/test_yostats_active_browser_window.py` | +5/-2 | tests |
| `tests/test_dev_restart_script.py` | +76/-47 | tests |

**The entry point was renamed and the signatures changed, so a partial compose fails immediately and loudly rather than subtly** - which is the good case. HEAD exports `proc_listener_pids`; the tree exports `proc_listener_census`; **neither name exists in the other**. HEAD's probe does `from ...listener_census import listener_pids` and the tree's does `import listener_census`, so **either half alone raises `ImportError`**. `require_unique_listener_pid` goes from `(port, pids: list[int])` to `(port, census: ListenerCensus, *, strict: bool = False)`. And the newly-found third consumer calls `unique_listener_pid(args.port, strict=False, timeout_seconds=2.0)`, so **without the census change it raises `TypeError` on `strict`.**

**Why this group is load-bearing, already proven independently:** without `listener_census.py` the `test_standalone_probe_drives_an_ephemeral_authenticated_daemon` failure is **deterministic** on any host whose process table holds a process we cannot read - measured at 5 of 5 raising on HEAD and 5 of 5 succeeding on the candidate, on a host presenting 558 such processes.

### Group B - jobd maintenance launch. Two files, hard coupling

`yolomux_lib/app.py` (+16) passes `launch=False` at four sites; `yolomux_lib/infra/jobd.py` (+21/-4) is what accepts it. **HEAD's `jobd.submit` does not accept `launch=` at all**, so `app.py` without `jobd.py` raises `TypeError: submit() got an unexpected keyword argument 'launch'` on every maintenance submission. `tests/test_app.py` (+184) holds the regressions.

### Group C - the generated bundle has THREE origins, not the two H1 names

`static/yolomux.js` (+64/-11) is generated, and **the base patch alone contributes two independent concerns**: the deferred-seal reconcile (`reconcileDeferredSealedAutoApprove`, `heldSealIsFullyOvertaken`, `finalizeSessionMetadataOutcome`) from `static_src/js/yolomux/99_terminal_boot.js` (+59/-10), and the unlabelled-413 predicate change from `static_src/js/yolomux/45_file_explorer_actions.js` (+5/-1). **H1 counts the base patch as one writer and `defect2-telemetry` as the second, which is fair at composition granularity - but at compose time three independent origins write this one generated file.** Dropping any `static_src/` contribution leaves the bundle and its sources disagreeing. **`static_build.py --check` after composing is what catches it, and H1's instruction applies with more force than its wording suggests.**

### Group D - failed-node persistence

`tools/check.py` (+279/-20) and `tests/test_check_runner.py` (+340/-15): `FailedNodes`, `MAX_FAILED_NODE_IDS`, `MAX_FAILED_NODE_ID_BYTES`, `_PYTEST_SUMMARY_RE`. This is P0 e2's schema-5 exact-node persistence. **Load-bearing for evidence, not for a green gate** - without it a failure is recorded without its node ids.

### Group E - lane markers

`tests/test_gate_agent_state.py` (+792/-30) carries **11 `@pytest.mark.no_browser` markers that exist nowhere else**; at HEAD the file has 6 `browser` markers and none non-browser. **Without the patch, 12 of its 18 nodes have no explicit lane** - which is the corrected H5 premise, and it depends on this patch landing.

### Incidental, safe in any order

`docs/specs/GUI.md`, `queues/README.md`, `boot.sh`, `tests/test_live_browser_soak.py` and `yolomux_lib/live_browser_soak.py` (one `unique` call site), `tests/test_session_files.py`, `tests/layout_async.test.js`, the four tracked `DOIT*.md` and `STATUS-REPORT.md`. **One exception that is not incidental despite its size: `tests/fixtures/architecture_budgets/v1.json` (+15/-15) - regenerate from the composed tree, never carry these keys from a lane.**


## Integration rehearsal, second pass, 2026-08-26 00:51 PT (`E3-RECOMPOSE2-95` and `E3-CONFLICTFIX2-97`)

**These are METHOD, not values.** Every number below is scoped to the tips named beside it, and **five tips moved between a measurement and its use tonight - that is a rate, not a coincidence.** Re-run `git for-each-ref 'refs/heads/wq/v0717-*'` immediately before the real merge and regenerate the ratchet and census from the composed tree. **Do not paste any key set from this file.**

**Discovered set: 16 branches, 50 commits**, base `3d1fe4da8` an ancestor of every one, so **no rebase is needed**. `wq/v0717-e3-cost-gate` is new since the first rehearsal and carries 9 commits.

**Minimal merge set is ELEVEN, because four branches are contained in others:** `compaction-guard` and `ring-invalidation-retention` inside `batch-persistence`; `statsd-health` inside `boundary-flush`; and **`readiness-p95` inside `cost-gate`** - the last is new, since `readiness-p95` was an independent merge in the first rehearsal and merging it separately is now a no-op. `pending-overlay` stays excluded by decision and is provably a no-op.

**Resolved composition `e6ebc6ebeab2d4f8fe5133012f9930467e662d76`, tree `9e39dc1ad0082993e2010eb035e14af4f79fcb0b`**, with the 32-path base patch applied, scoped to `cursor-validation` at **`1d04a48da`** - which moved *during* the resolution task, from the `4ba40d6e5` the rehearsal forty minutes earlier had used.

**The same one conflict, same shape, same decision: keep both, HEAD side first then incoming**, one hunk each in `storage.py` and `tests/test_stats_current_storage.py`, no line of either side edited, reordered or dropped. **The symbol-set proof holds on the new tip: every addition from both sides present by name, and the set added by BOTH sides is empty** - so the extra commit introduced no symbol the composition already had, and there is still no half-implemented duplicate concept. That emptiness is what makes it evidence rather than a merge that happened to be clean.

**Ratchet: 29 keys regenerated from the resolved tree** - up from 27 at the first rehearsal and 28 forty minutes ago, because **one commit added a whole ratchet key after a rehearsal had called the set complete.** `tests/test_stats_current_service_performance.py` is now `29 -> 436` and `tests/test_stats_current_materializer.py` `1572 -> 1986`; three test files are newly unbudgeted (`test_pragma_joint_grid.py` 277, plus growth in `test_resource_ab_compare.py` and `test_stats_health_route.py`). **That single late key is the whole argument for regenerating at merge time rather than trusting any manifest, including this one.**

**Census: 7 additions, 0 removals, all in one file.** The seventh is `test_the_probe_publishes_a_ring_head_before_it_measures`, which arrived with the ring-head fix and which no earlier report could have known about; the sixth is `test_the_cost_gate_states_the_smallest_regression_it_can_resolve`. `cursor-validation` adds no `gate_serial` node at `1d04a48da`. The exact block to paste at the `gate_serial_nodes` literal is retained in `/home/keivenc/dev/yolomux-e3-evidence-20260825/`.

**Bundle: `python3 tools/static_build.py --check` exit 0** on the full composition with the base patch. This matters more than H1's wording suggests - **`static/yolomux.js` takes hunks from three independent origins at compose time, not two.**

**Two known reds, both non-browser, and the third candidate stays off the list for a verified reason:** the `listener_census.py` fix is present in the composed tree - **53 `degrad`-prefixed references, checked rather than assumed** - so `test_standalone_probe_drives_an_ephemeral_authenticated_daemon` gets the fixed library. **That remains contingent on the base patch composing whole.** Read the node id before concluding which red fired; retry neither away.

**Both owners' focused suites pass on the resolved tree**, run single-process: `test_stats_current_storage.py` 80, `test_stats_replay_ledger.py` 100, `test_stats_batched_persistence.py` 34, plus the ring-buffer and migration suites. **The rest of the gate is unrun** - no full gate has executed on any composition tonight.


## Frozen-tip manifest, 2026-08-26 01:35:21-01:36:27 PDT (`E3-FREEZE-113`)

**Nothing moved during the 66-second measurement - the first measurement tonight that can say that.** All 16 branches re-checked at the end and identical; base patch hash identical at both ends.

**16 branches, every one with `3d1fe4da8` as an ancestor, so no rebase is needed.** Five are newer than any earlier list: `cursor-validation`, `release-docs`, `cost-gate`, `boundary-flush`, `statsd-health`. **The count went 11 to 16 in three hours.**

### THE ACTIONABLE FINDING: the base patch's product content IS frozen; only `STATUS-REPORT.md` moves

The patch hash has been three different values in four hours - `e4cba95b10f2` at 21:48, `c22122ec6b74` at the 00:05 backup, **`d1bae311bc7c4088d1b8c1610bfcb486b38196ec35476a729d6340af47389236` now, 406,201 bytes, 0 binary hunks.** Diffed per file against the 00:05 backup:

```
path SET identical (32 both)     changed since 00:05 : 1     added: []     removed: []
    STATUS-REPORT.md   (+8,686 bytes)
```

**The other 31 tracked paths have been byte-stable for 91 minutes and no path has been added or removed.** So the churn is entirely the coordinator's own status document.

**COORDINATOR DECISION: verify the 31 product paths at integration, not the whole-patch hash.** Freezing `STATUS-REPORT.md` would mean stopping the ledger updates the landing objective requires, so the patch hash **will** differ at 04:00 and that difference is expected rather than a signal. **The check that actually matters is that the path set is still 32, that no path was added or removed, and that the 31 non-status paths are unchanged.**

Confirmed by counting rather than assumption: **32 tracked paths in the diff + 1 protected untracked scratch file (`test_session_files_narrowed.py`) = 33 porcelain lines.**

### Three branches are still moving; the practical re-check list is three, not sixteen

| band | branches |
| --- | --- |
| **MOVING - assume stale, re-check immediately before merging** | `process-binary-cache` `0dc337f18` (11 min), `cursor-validation` `3d28124d6` (15 min), `boundary-flush` `0808b441a` (28 min) |
| SETTLING - verify cheaply | `release-docs` (50 min), `cost-gate` (60 min), `ab-harness` (69 min) |
| SETTLED - quiet 2+ hours | the remaining ten |

**`ab-harness` `f4d1893ab` and `shared-transaction-measurement` `3da704ea9` are DONE with certainty rather than by age** - the reporting lane owns both and has no further work queued on either. **Everything else is inference from tip age, which the lane was careful to call evidence rather than proof:** a quiet branch may be quiet because its lane is writing a report and about to commit again.

### Containment: four branches are redundant, computed as a full 16x16 ancestry matrix

`ring-invalidation-retention` inside both `compaction-guard` and `batch-persistence`; `compaction-guard` inside `batch-persistence`; **`readiness-p95` inside `cost-gate`**; `statsd-health` inside `boundary-flush`. **So merging `batch-persistence`, `cost-gate` and `boundary-flush` brings four others with them.**

**Git containment is not content redundancy, and `pending-overlay` is where they differ.** It is an ancestor of nothing, so the matrix keeps it - but its entire content is one file, byte-identical on `batch-persistence` at `ff9fe92d0b4d5dad...`, **re-verified at tonight's tips rather than cited**, and `batch-persistence` has moved twice since that was first proven. **The exclusion decision stands and the practical merge set is 11.**

### The one-line check before merging

**Re-run `git for-each-ref 'refs/heads/wq/v0717-*'` and compare against the manifest.** If only the three moving tips differ, the rest holds. **If the count is not 16, a new lane appeared and the containment matrix must be recomputed** - it went 11 to 16 tonight.

**Not established, and stated rather than glossed:** whether a quiet branch is finished or merely paused, since tip age is a signal and only its lane knows; whether the three moving tips settle before 04:00; and **whether all 16 merge cleanly together** - this manifest is identity and ancestry only, no merge was run, and the last clean composition was built from 8 branches of which 5 of today's 16 did not yet exist.


## `wq/v0717-e3-boundary-flush` is FROZEN at `0808b441ae5095c100e23c89c9bb7383ce16b1c4`, declared by its owner 2026-08-26 02:0x PT (`E3-DECLARE-114`)

**Nothing outstanding; working tree 0 dirty paths; the tip is the same one the frozen-tip manifest measured. Stop re-checking it.** The owner committed nothing to answer, which is the right response to a freeze question.

**Both release claims re-derived AT THE TIP rather than remembered**, because they were named urgent if untrue:

```
/readyz   resolves=True  role='readonly'  handler=get_readyz    -> authenticated
/livez    resolves=True  role='public'    handler=get_livez     -> public
```

**The release documentation is correct about this tip.** The standing caveat is unchanged and the owner declined to drop it: **route resolution and dispatcher behaviour are proven; a live HTTP request is not.** One `curl` after 04:00 upgrades "reachable" from route-resolution evidence to end-to-end.

It also re-checked that the code still matches every claim in its own docstrings, which is the class that bit four times tonight: `/livez` writes exactly `{"ok": ..., "live": ...}` at `http_routes.py:452`, one line with no branch around it; `get_readyz` contains **zero** `require_auth` calls of its own, as its docstring forbids; and `_handle_resource_state`'s **body** contains zero references to `work_lock`, `cache_lock` or `_status()` - **the two matches a naive grep finds are inside the docstring, which names them to say why it does not use them.**

### CONTAINMENT CORRECTION - a coordinator brief said this branch carries four lineages. It carries ONE

Measured by the owner and **independently re-verified by the coordinator**:

| branch | contained in `boundary-flush` |
| --- | --- |
| `statsd-health` | **YES** |
| `compaction-guard` | **NO** |
| `ring-invalidation-retention` | **NO** |
| `readiness-p95` | **NO** |

**Merging `boundary-flush` brings the health lineage and nothing else. It does NOT bring the retention prune or the compaction guard**, which are ancestors of `wq/v0717-e3-batch-persistence`. The frozen-tip manifest states the aggregate correctly - three branches bring four others *between them* - but the per-branch mapping must be read as: **`batch-persistence` brings `compaction-guard` and `ring-invalidation-retention`; `cost-gate` brings `readiness-p95`; `boundary-flush` brings `statsd-health`.** A coordinator conflated the aggregate with one branch's share and was corrected.

**Final writable-path manifest for the branch:** `service.py`, `http_routes.py` (granted), `client.py` (reported as growth, verified unowned), and two new test files. **Carried but not modified:** `http.py` and `tests/test_stats_current_health.py` from the `2738c1bfe` merge - read, neither rewritten, no diff touching either. `storage.py`, `materializer.py`, `app.py`, `server.py`, `tools/`, `docs/`, `README.md`, `v1.json`, the protected scratch file and every queue document were never touched.

**Ratchet keys the branch contributes, for the coordinator to apply from the composed tree rather than from this list:** `StatsCurrentService.methods` 93 to 96, `self_fields` 134 to 135, `file_lines service.py` 5095 to 5221, three previously unbudgeted test files, and a new `daemon_actions` extension family entry for `STATS_COMMAND_ROUTER:resource_state`. **The lane did not write `v1.json`.**

**Green at the tip: 46 passed, rc 0**, and **nothing touched the statsd store** - every test uses `tmp_path` fixtures and in-process fakes, so the 02:05 compaction watch was undisturbed.

**Two small things told and deliberately left uncommitted, per the freeze rule:** the docstring-versus-role parity test asserts over an explicit three-route list, so a fourth health route added later is not pinned automatically - deriving it from the route table needs a health-route marker, and **inventing one at 02:30 to cover a route nobody has written is not worth a tip move**; and `/readyz` is `"readonly"` rather than `"admin"`, matching the other stats routes, which is a policy call that can land after.

**Still open elsewhere and explicitly not on this branch:** the `service_load` half of the CPU backoff item, which belongs to `app.py`'s owner and keeps its queue item open until it lands and consults `_stats_are_watched()`; and the boundary-flush item itself, deferred by decision and sequenced after `batch-persistence`, for which the owner is keeping its worktree and branch.


## The 04:00 runbook is current, checked 2026-08-26 02:1x PT (`E3-RUNBOOKCHECK-116`). 514 lines, five genuinely open unknowns

**One thing in it was WRONG rather than incomplete, and it was self-contradictory.** Line 297 carried `**UNKNOWN:** whether e3-cost-gate adds gate_serial nodes` while **line 274, twenty-three lines above, already answered it** naming both nodes and citing two rehearsals, and the ledger's own row said the effect was measured at +2. **The runbook stated in three places that the same answer was measured, unknown, and measured.** That is the failure mode a stale instruction produces: **a reader at 04:00 hits an UNKNOWN, treats the section as open, and reasonably starts distrusting the answers sitting above it.** The checking lane verified the answer independently from branch blobs before touching it - both `def test_` names present on `cost-gate` and absent on `readiness-p95`, that file carrying 6 tests on one and 8 on the other, 7 `gate_serial` occurrences - then replaced the marker with the measurement **while keeping the stop-the-line instruction to re-derive anyway.**

**Three branch tips in the runbook were stale and are corrected:** `boundary-flush` `4786b6156` to `0808b441a`, `cursor-validation` `4ba40d6e5` to `3d28124d6`, `process-binary-cache` `d34c603b0` to `0dc337f18`. The other nine matched, and all twelve now match `git rev-parse` exactly. **Those three are precisely the frozen-tip manifest's "still moving" band, which is an independent confirmation of that banding rather than a coincidence.**

**Two unknowns closed by measurement, none by inference.** Whether `cost-gate` adds census nodes (it adds exactly 2), and whether the rehearsal worktree is reusable - 70 MB at `e6ebc6ebe` / tree `9e39dc1ad008` with the base patch applied, **containing 8 of the 12 minimal tips current**, behind on exactly the three moving branches plus `pending-overlay`, which may simply never have been merged given the exclusion decision. **Every one of the five that remains open is a measurement taken at 04:00.**

**Three coordinator concerns checked, and two were already right.** The runbook **never** cited a whole-patch hash as a verification step - its only `sha256sum` builds a manifest for the baseline archive, a different artifact - and Phase 0.3 already verifies identity the correct way. It was **strengthened** rather than corrected, with an explicit warning not to reach for a whole-diff hash and the reason: only `STATUS-REPORT.md` moves, the other 31 paths were byte-stable for 91 minutes, **and content assurance means diffing those 31 against the 00:05 backup rather than comparing whole-patch hashes.** The branch count and 12-branch minimal set were already right, with four containments matching an independent matrix exactly. **And the census already said seven, not five** - the stale thing was the contradicting marker below it.

**Deliberately not changed, each with its reason:** the `~02:05 predicted` compaction line, left to resolve rather than pre-empted; unknown #2, because `cursor-validation`'s tip **moved again** since the runbook flagged it, so *"re-derive it; do not paste the old resolution"* is more true rather than less; #4, because measuring ratchet keys for a 12-branch composition is not a read-only operation; and #9, which needs a full gate.

**The lane introduced one inconsistency and fixed it rather than hiding it:** after closing two, the header said five while the table still listed seven rows. It added a reconciling paragraph rather than deleting rows and losing their content.

**Unestablished, and stated plainly: whether "wrong and unmarked" steps remain.** The lane checked the ten listed unknowns and three inline markers and read the whole file, but **a step can be wrong without being marked, and that check is only as good as one reading of 514 lines of someone else's procedure.**


## ALL THREE MOVING TIPS ARE NOW DECLARED FROZEN BY THEIR OWNERS, 2026-08-26 02:09 PT

The frozen-tip manifest banded three branches as *still moving* from tip age and said explicitly that only their lanes could settle it. **All three have now answered, and the coordinator re-verified each tip afterwards - none has moved since the manifest.**

| branch | frozen at | last commit | declared by |
| --- | --- | --- | --- |
| `wq/v0717-e3-boundary-flush` | **`0808b441a`** | 01:07:09 | its owner (`E3-DECLARE-114`), 0 dirty paths |
| `wq/v0717-e3-process-binary-cache` | **`0dc337f18`** | 01:23:40 | **both contributors** (`E3-DECLARE2-115`, `E3-DECLARE3-117`) |
| `wq/v0717-e3-cursor-validation` | **`3d28124d6`** | 01:20:14 | its lane's `E3-FIGURES-110` reported DONE at this commit |

**`process-binary-cache` needed two declarations and that distinction was drawn by a lane rather than by the coordinator.** The docs-authority lane declared its own two commits final and then **refused to declare the branch frozen**, on the grounds that it holds docs authority rather than ownership and cannot speak for another lane's work. **A coordinator would have taken the partial declaration as a whole one.** The original owner then confirmed separately: its two commits are `7942cdd32` and `d34c603b0`, nothing is outstanding behind the three later docs commits, and its worktree verifies at 32 paths, 0 untracked, with the base diff excluding the spec byte-identical to the shared base at `e4cba95b10f2248a7862a38d901dd1b78554caaa05d51d69da6be70723a67566`.

### The spec author endorsed all three changes to the document he wrote, after checking rather than assuming

He re-verified the load-bearing claim himself in the candidate tree: **`PrivateOverlay` has a class definition at `materializer.py:187` and a field default at `:202` and NO construction site; `_private_browser_sources` at `:654` has no caller; the tests reference neither.** So the private path is genuinely dead and `len(RESOLUTIONS)` is correct today.

**His own assessment of the change, which is the useful part: "This is better than what I wrote."** His original was a bare literal `4` with no precondition - **it happened to be correct, but it did not say why, which is the difference between a number and a bound.** The withdrawn correction was wrong to multiply it today, the revert was right, **and the text that survived is stronger than either.** He also noted that the fixture sentence at `:71` directly closes a finding he had himself filed against this same spec two tasks earlier, and that it says plainly neither figure has been re-derived - *"the distinction I would have wanted and did not draw myself."*

### One off-by-one in the spec, reported and deliberately NOT committed - coordinator ratifies

`docs/specs/STATS_BOUNDED_REBUILD.md` cites `materializer.py:505` twice, at spec lines 51 and 57, for `observation_cells.setdefault(cell, []).append(projected)`. **That statement is at `:506`; line 505 is the `for cell in cells:` above it. Verified independently by the coordinator against `3d1fe4da8`.**

**The other twelve `file:line` citations in the document verify exactly** - `storage.py:3306`, `:421`, `:3329`, `:3331`; `service.py:2365`, `:2372`, `:2484`; `materializer.py:364`, `:732`, `:753`, `:1074`, `:1173`.

**The lane declined to commit it and gave its reasoning so it could be overridden in one line. The coordinator ratifies that judgement.** It qualifies literally under the standing rule - prose contradicting code should land - **but it is the weakest member of that class: the quoted statement text is verbatim correct, so no reader reaches a wrong conclusion about what the code does.** The harm is two seconds against unfreezing a tip during integration, and one off-by-one among thirteen exact citations does not earn that trade. **Recorded as a known residual for after the release**, not as an open item.


## Current open work - read this before reading the ledger below

**Exactly one checkbox is open: the Defect 2 attribution.** Eight of nine are done. Everything else in this file is a chronological ledger of work that has already closed, and several of its paragraphs are written in the imperative voice of the moment they were recorded - "Audit 29 still rejected Defect 3 closure", "The next correction must keep one typed census owner", and similar. **Those are history, not open work.** Do not reopen a defect because a mid-file paragraph describes a correction that was subsequently made.

- **Defect 1** - closed. Owner traced and fixed in ancestor `f716e980e`, deterministic regression added, 23 sequential current-HEAD runs with 0 failures.
- **Defect 3** - closed and independently accepted by Tasks 50-54. **Frozen accepted evidence; do not reopen it and do not re-derive it.** Final six-path subject diff SHA-256 `c98001b8ce31570fc9b59546b78e69f292b04576ededaf500330e4ae4ae7a87c`; real-kernel artifact `/tmp/v0717-task54.s848nzyu/listener-real-current.json` SHA-256 `bd37f3c15564e6dd50e67101ac292b1436db8f13af813af757752a4206c0a94d`; AST SHA-256 `013f8f5d3b74325f350ece35b5b7f320d43ea6fccfb745350c1ec9bb5f15f956`; core `76 passed`; architecture `26 passed`; focused seven-owner set `628 passed, 2 skipped`; two independent reviews returned no findings. The Audit 29 and Audit 31 rejections recorded below were both answered - by Task 30/Task 32 respectively - and then accepted by findings-blind Audit 34 and closed by Tasks 50-54.
- **Defect 4** - closed and independently accepted by Tasks 45-48 on frozen non-STATUS subject `9fbf9ba191b9b8cd60e9790e02284a7b38dbeac88b36b1700b590d81640e4589`.
- **Defect 5** - closed.
- **Defect 6** - **classified and FILED, not fixed.** The checkbox is legitimately `[x]` because the item reads *"classify and fix **or file**"*, and Audit 21 classified the retained 3/16 Task 18 boundary failures as **an unresolved product lifetime and deadline gap, filed to `DOIT.p1.e5.backend-lifetime-supervision.md`**, with any association to the unmeasured Task 18 host state still unestablished. Task 49 reproduced the missing isolated transition through the fixture's public lifetime seams and its exact red is retained. **Zero signatures in 32 qualified-host browser-fixture boundaries measures its absence under those conditions; it does not close the underlying gap.** A coordinator revision of this section wrote "closed", which is wrong and is corrected here - **a `docs/DONE/` record must not count Defect 6 among the fixed.**
- **Defect 2** - **OPEN, and it is the only thing blocking this queue.** It is not resolved here. `DOIT.p0.e3.statsd-resource-bounds.md` owns the controlled attribution, because the candidate cause is the statsd persistence owner and only that queue can produce the corrected owner to compare against. This queue's last box may be checked only after e3 returns an accepted classification of statsd-caused, statsd-amplified, or independent, backed by first-transition telemetry at the failing three-second interval.

There is no second open grouped box. Any statement elsewhere in this file that two items remain is stale and is superseded by this section.


## Defect 1: a focused graph control deferred an accepted generation until focusout

`tests/test_browser_ring_landing_verify.py::test_ring_landing_real_page_restart_and_zero_gap`

### Historical reproduction on a separate lineage

Eight consecutive isolated runs on 2026-08-24 at HEAD `35e765675`, host load 8-12, nothing else running, each `python3 -m pytest "tests/test_browser_ring_landing_verify.py::test_ring_landing_real_page_restart_and_zero_gap" -q -p no:randomly`:

| Run | Result | Seconds |
| --- | --- | ---: |
| 1 | **FAILED** | 71.25 |
| 2 | passed | 40.23 |
| 3 | passed | 39.03 |
| 4 | passed | 38.64 |
| 5 | passed | 42.94 |
| 6 | passed | 42.17 |
| 7 | passed | 41.81 |
| 8 | passed | 49.67 |

**1 failure in 8, about 12.5%, without demonstrated contention.** Passing runs cluster at 38.6-49.7s; the failure took 71.25s because it consumed the full 20-second `WebDriverWait` before giving up. The audit found this test failing at three distinct SHAs: `6c360ec3`, `8adc6108`, and `35e76567`.

Runs 2-8 confirm the invocation goes through the isolated test container, so this is the same execution path the gate uses.

### The captured state names the visible failure, not the owner

Failure raised at `tests/test_browser_ring_landing_verify.py:469`, from the `ready()` predicate inside a `WebDriverWait(driver, 20, poll_frequency=0.05)`:

```
graphGenerationKey:   3600:60:60:0:1787639532605
paintedGenerationKey: 3600:60:60:0:1787639532605     identical -> renderer believes it painted
sourceGeneration:     0
renderPaths:          0                               it painted nothing
renderedCharts:       12
historyState:         ready
busy:                 false
costRows:             Input 0, Cache 0, Output 0, Total 0
readiness.generation: 7
readiness.phase:      ready
readiness.storeCoverageIntervals: full 60s coverage for all 8 families
```

The original interpretation was wrong. `renderDebugGraph` writes `graphGenerationKey` from `paintedGenerationKey` immediately after committing the paint, so equality is expected and does not show two owners disagreeing. `readiness.generation` is a local history-readiness transition counter, not `source_generation`; comparing 7 with 0 joined unrelated owners. The state does prove the user-visible failure: the panel reported ready and idle while rendering zero paths and all-zero cost for the full wait.

### Current-head resolution, verified 2026-08-25

The owner was the focused-control gate in parent-of-fix `113a9a85e`: `debugGraphFocusedControl`, its deferral branch in `refreshDebugGraphElement`, and the matching refusal in `flushDeferredDebugGraphRefresh`, all in `static_src/js/yolomux/85_debug_panel.js`. The journey clicked a chart toggle inside `.js-debug-graph-controls`, leaving it focused; the parent gate then set `jsDebugGraphRefreshPending = 'true'`, returned without painting, and refused to drain while that control remained focused. This is a product defect because a normal user click could leave a ready chart frozen.

The fix already landed in ancestor `f716e980e`: it removes `debugGraphFocusedControl` and every focused-control deferral consumer, while preserving explicit deferral for active chart gestures. Current `HEAD` has zero focused-control deferral markers against seven in the parent file.

The deterministic Node suite is green 96/96 on current `HEAD` and on a shadow negative control using the same file. Holding the current test constant and substituting only the parent `85_debug_panel.js` yields 92 passed and 4 failed, exit 1. The behavioral failure leaves `forcedRepaints: 0` instead of 1 and `pending: 'true'` instead of empty. The current journey also injects a newer generation while the chart toggle remains focused and requires the controller, painted key, graph key, and rendered paths to converge.

Twenty-three sequential isolated current-HEAD runs all passed: **0 failures in 23**, min 41.04s, max 52.11s, mean 46.84s. The historical 1-in-8 run used a different predicate on a non-ancestor lineage, so it is not a like-for-like before/after rate. If treated only as a statistical reference, 0/23 rejects a sustained 12.5% rate one-sided at 5% by a narrow margin. The retained historical artifact lacks focus and pending-generation fields, so the focused-control attribution is strongly supported by the exact journey, red/green isolation, and current rate rather than directly recorded in that artifact.

### Explicitly do not

Do not raise the 20-second wait, add a retry, add a sleep, or weaken the `renderPaths > 0` predicate. A renderer that reports `historyState: ready`, `busy: false`, and a matching painted key while having drawn zero paths and zero cost is wrong whether or not a test observes it. Users see the same zeros.

### Note on where it fails

The audit captured this test failing at its restart assertion, `assert restarted_total == 12` at line 805. The isolated reproduction fails earlier, at line 469 in the readiness wait. Treat both assertion sites as consequences of one shared cause until proven otherwise, and check both when validating a fix.

## Defects 2-4: historical signatures recovered; current owners split

Independent Audit 23 corrected the original co-failure sampling result against the retained `/tmp/yolomux-check-runs/` corpus. The audit analyzer counted a run only when at least two heavy lanes failed together, which produced the queue's old 2/2/2 occurrence counts. Across all 200 retained nine-lane runs longer than 60 seconds, the exact primary artifacts preserve two Defect 2 occurrences, three Defect 3 occurrences, and four Defect 4 occurrences with complete assertion bodies. The corpus also preserves per-lane outcomes and `certification.release.full_sha` for 189/200 runs; the earlier claim that only node names and counts survived and that P0 e2 failed-node persistence had to supply the historical denominator is withdrawn.

| Defect | Exact retained signature | Occurrences and subject identity | Historical known-verdict denominator | Evidence-backed state and owner |
| --- | --- | --- | ---: | --- |
| 2: `test_real_stats_cpu_value_round_trips_through_rpc_and_rendered_svg` | `warnings == []` failed because `YO!stats stream generation stalled for more than 3s`; the otherwise-ready CPU state included `cpuAxisMax: 50` and `cpuPointCount: 8`. | 2, both on non-ancestor `8adc6108` with the same dirty 21-file state, 21 minutes apart. | 2/163 known-verdict `pytest-e2e` executions of 200; 37 failing-lane verdicts are unrecoverable. | Unresolved between product and resource amplification. The watchdog owner is `static_src/js/yolomux/84_stats_current.js:426`; the controlled attribution belongs to `DOIT.p0.e3.statsd-resource-bounds.md`, not another quiet-host solo arm here. |
| 3: `test_standalone_probe_drives_an_ephemeral_authenticated_daemon` | Probe exit 2 after `cannot inspect Linux file descriptor ...` or `cannot enumerate file descriptors for Linux process ...`; the test explicitly rejects setup/probe exit 2. | 3 across non-ancestor `6c360ec3` and `c69c50ab` clean subjects plus dirty `35e76567`. | 3/117 known-verdict `pytest` executions of 200; 83 failing-lane verdicts are unrecoverable. | Product defect in the observability listener census's fail-closed non-race `OSError` path at `listener_census.py:188/198/211`, with the underlying errno then discarded by `system_status_latency_probe.py:253-255`. The root-class handling decision, preserved cause, deterministic regression, and post-fix evidence remain open. |
| 4: `test_older_deferred_completion_cannot_replace_newer_forced_canonical_cache` | `forced_terminalized.wait(timeout=2)` returned false at historical `test_session_files.py:574/586`. | 4 across non-ancestor `c69c50ab` and `8adc6108`; three occurrences share one dirty `8adc6108` state. | 4/117 known-verdict `pytest` executions of 200; 83 failing-lane verdicts are unrecoverable. | Not measurable on the current subject: the bounded predicate was removed and current `tests/test_session_files.py:586` uses unbounded `wait()`. Identify the removal and intent, then restore the bounded invariant or file its replacement with a deterministic non-hanging owner. |

The historical denominator is real but not a like-for-like rate: the 189 identified runs span 68 commits, 139/189 were dirty, and none of the four occurrence SHAs is an ancestor of current `HEAD`. The 2/163, 3/117, and 4/117 figures are bounded known-verdict populations, not current-subject before/after rates; the unknown failing-lane buckets cannot be reconstructed. Schema 5 now preserves exact failed nodes, complete assertion artifacts, and call-duration denominators prospectively, but cannot retrofit those mixed historical subjects.

The same Defect 2 node also has three older 2026-08-05 artifacts with a different `cpuAxisMax == 100` assertion against an observed 50. Task 24 traced that separate signature to a retired dirty-lineage mismatch between removing CPU's `fixedMax: 100` and updating the expectation; it is not the two stats-watchdog occurrences counted above. The current data-derived owner `debugGraphChartAxisMax` and its pure regression pass 96/96, and Task 25 corrected the stale GUI sentence so the spec now distinguishes the System CPU line's 0-100% bound from the shared axis expanding past 100% for multi-core process peaks.

Task 26 attempted the Defect 3 correction on frozen non-STATUS diff `478e30fc9f810febb5365a1c07b69e7f30d3231798a8a0bcc40dd9f7894cc437`. Its probe diagnostic preserves the exception chain and errno, and Audit 27 accepted that behavior plus its regressions. The listener-census behavior is rejected: Audit 27 demonstrated an accessible pid 123 and denied pid 456 sharing listening inode 4242, after which `proc_listener_pids` returned `[123]` and `require_unique_listener_pid` accepted 123 even though uniqueness was unprovable; the retired fail-closed path raised. The subject also remained red at 1 failed, 71 passed, 1 skipped because the standalone probe target still failed on three denied same-UID non-dumpable processes, and the architecture lane independently measured one subject-owned failure with exact growth from 341 to 388 lines in `listener_census.py`, 513 to 627 in `test_listener_census.py`, and 580 to 619 in `test_system_status_latency_probe.py`.

Task 26 therefore remains PARTIAL. The complete correction must represent degraded census explicitly, let raw observability retain the partial result, make every identity or exclusivity caller refuse a degraded result, give the `ss`, `lsof`, and `/proc` producers one shared unreadable-owner contract, preserve both `fd_stat` and `fdinfo` causes without printing suppressed context, and add the shared-inode regression at the public uniqueness API. Re-measure the actual test-container namespace, then update the three exact architecture pins only after the accepted design stabilizes.

Task 28 implemented typed `ListenerCensus` degradation on frozen non-STATUS diff `1db11195295ee43976e751a040bf6e081ca26ba303fb4de614280a5cc36fd401`. It closes the demonstrated `/proc` fail-open hole: same-UID and different-UID shared-inode regressions carry every denial through canonicalization, and the public exact-one gate refuses degradation or a bare list. Independent Audit 29 reproduced that red/green, verified the focused owners at 50 census, 14 probe plus one environment skip, 13 tool, 64 YO!stats, 2 live-soak, and architecture 26/26, and found no weakened assertion or caller that discards `/proc` degradation.

Audit 29 still rejected Defect 3 closure. The `ss` and `lsof` backends assert that successful output is complete even though each can omit an unreadable co-holder while reporting a visible one; the operator summary counts degradation records as processes; exception rendering still prints suppressed context; two raw-list wrappers have no product caller; and `canonicalized_listener_census` lacks its return annotation. More importantly, the correct container-namespace measurement found two processes and zero denials in an idle container, so it does not identify the errno or reproduce the historical parallel-gate condition. The accepted state is therefore: the `/proc` fail-open exclusivity hole is closed and future exit-2 output is legible; Defect 3 remains open and unclassified until a real occurrence preserves its errno and owner context.

Task 30 corrected every Audit 29 code finding on frozen non-STATUS diff `ebd9aa5f7217e0f3e466ca82b7196f30c467cee96cb105571b0e7e77194fba42`, but findings-blind Audit 31 rejected the resulting host contract. On the supported shared Linux host, 557 of 866 live processes were unreadable, so an owned occupied port produced 561 degradation records, `require_unique_listener_pid` refused the correctly identified owner, and the real shell boundary exited 2 with about 26 KB of stderr. Non-Linux uniqueness is degraded by construction and can never succeed. The seven focused owners all passed, exposing a coverage hole; `tests/test_dev_restart_script.py` also widened one exact boundary assertion to `returncode in {0, 2}` and removed planted-scanner ownership cases, the Linux `/proc` path silently retired the caller's timeout, and `boot.sh` retained stale `ss`/`lsof` advice.

The next correction must keep one typed census owner and distinguish target-specific uncertainty from unrelated host visibility. Missing target listener inodes, multiple visible owners, malformed/fatal reads, and any explicit strict-completeness mode remain fail closed. The default supported-host identity path must not reject solely because unrelated processes are unreadable; strict whole-host visibility may be an explicit mode only where the caller and isolated environment require it. Restore exact shell-boundary outcomes against a known owned listener, add a host-scale or generated hundreds-of-denials regression for the selected contract, either honor the existing timeout or retire it and its claim consistently, cap degradation rendering with the omitted count, and correct operator advice. Acceptance requires the real public API and shell boundary to succeed for a known owned listener on the shared host without weakening target-inode missing or multi-owner refusal. Defect 3 remains open and unclassified until a real occurrence preserves errno and owner context.

Task 32 returned that correction on pre-rebase non-STATUS diff `03ab685ba9c4b5b2c74659698b64d5f5e49fa54e291680bb15f94e5ae399fe87`. One selector now distinguishes target-scoped operational identity from explicit strict whole-host visibility; default mode ignores unrelated global degradation but still refuses missing target inodes, zero or multiple target owners, fatal reads, and timeout. The subject caps rendered records at five plus exact omitted counts, honors the Linux timeout, restores exact shell PID/error assertions, covers 300 unrelated denials and simulated Darwin default/strict modes, and passed 479 focused tests plus one environment skip, architecture 26/26, forced red, and a shared-host fixture where default API and shell returned the exact PID with empty stderr while strict refused in 327 characters.

At the user's request, Task 33 rebased the full dirty subject onto signed `v0.7.16` commit `eb42a872e56c397cd2191fcc7549c09714312f8f`. New branch HEAD is `17c218feff94df2e6c68b20df801bcce3e38d624`, with rebased non-STATUS diff `ebee18ef312ee191a3a069ec4187e275d5dc2d2df0c3c791551829334beaa8b3`. The v0.7.16 tag is an ancestor; all 21 dirty paths and coordinator documents were preserved, upstream release bookkeeping replaced the duplicate replayed draft, upstream debug-panel source was composed by rebuilding the generated bundle, and post-rebase evidence is focused 479 passed plus one environment skip, architecture 26/26, Node 97/97 and 181/181, static-build check green, and exact shared-host PID acceptance. Independent audit of this rebased subject is still required; no checkbox moves from implementer or rebase evidence alone.

Findings-blind Audit 34 accepted the rebased listener contract and rebase composition for release on exact subject `ebee18ef312e`: independent host evidence returned the exact owner through default API and shell with empty stderr despite 559 unrelated unreadable processes, strict mode refused in 327 characters, all target/fatal/timeout cases remained fail closed, and focused, architecture, Node, generated-asset, caller, shell, and destructive-stop audits passed. It left two non-blocking P3 traceability findings: a stale `ListenerCensus` class docstring and a rebased top commit still titled as the release after its duplicate release hunks were correctly dropped.

Task 35 corrected only those two findings. The docstring now names default target degradation and additive strict global degradation through the one selector. The message-only amend changed HEAD to `3d1fe4da8caf0dc80ee821425e97dcf5d30c7f83` while preserving parent `c5d27738406e0670446f17df8ee75312afd564df` and tree `e75ed7a87badf537bcf71e33aa5a3a5634a6f7cc`; the new subject is `Add the latency probe to the gate serial lane`, and false release framing is gone. Independent Task 36 re-audit accepted both P3 corrections with no new finding on non-STATUS subject `76a01035c572527fd10baf5dcbacbe4016923644755eeae829980f43ce7a6a88`; listener census 49/49, architecture 26/26, and diff check passed. This accepts the root-class listener correction and rebase composition; Defect 3 itself remains open and unclassified until a real occurrence preserves errno and owner context.

Task 37 traced Defect 4's bounded assertion removal to the v0.7.14 squashed release, restored the exact two-second bound with a first-incomplete-transition diagnostic, and preserved one failure in eight isolated runs: both forced product waits returned while the ledger still reported 202 and the ordinary operation remained held. The test module passed 157/157, the predecessor pair passed 2/2, architecture passed 26/26, and the forced-red control failed at a different named transition. Findings-blind Audit 38 accepts this as a diagnostic improvement and rejects classification or checkbox movement. Its eight idle-host attempts passed, but the forced wait consumed 37-59% of the budget and local-service cold start accounted for about half of one measured window; the current classifier cannot name that cause, and its failure-path ledger read can replace the original assertion if it raises. Defect 4 remains open pending correction at the local-service/terminalization owner, a property-derived regression that excludes service startup from the forced-flight invariant, a safe ledger-unreadable diagnostic branch, and new frozen-subject evidence.

Tasks 45-48 closed Defect 4 on frozen non-STATUS subject `9fbf9ba191b9b8cd60e9790e02284a7b38dbeac88b36b1700b590d81640e4589`. Task 45 removed maintenance jobd cold starts from the forced interactive terminalization window by routing all four app maintenance submission sites through the existing non-launching `JobClient.submit`/`produce(..., launch=False)` twins; eight frozen-subject attempts passed with the bounded window consuming 1.9-3.3% of the two-second budget instead of 37-59%. Audit 46 accepted the owner correction and found three P3 issues. Task 47 gave declined prunes a bounded 30-second retry floor without consuming the five-minute accepted-work cooldown, replaced the file-local source-shape check with a repository-wide AST property using `tests/source_inventory.py`, and routed the target assertion plus unreadable-ledger regression through one module-level `first_incomplete_forced_transition`. Findings-blind Task 48 re-audit accepted all three corrections with no new finding and accepted Defect 4 closure. The four historical occurrences were not reproduced or individually attributed; closure is against this queue's named-owner, deterministic-regression, and post-fix-rate standard.

Tasks 50-54 closed Defect 3 on final frozen six-path subject `c98001b8ce31570fc9b59546b78e69f292b04576ededaf500330e4ae4ae7a87c`. The real-kernel artifact `/tmp/v0717-task54.s848nzyu/listener-real-current.json` (SHA-256 `bd37f3c15564e6dd50e67101ac292b1436db8f13af813af757752a4206c0a94d`) made a same-UID child non-dumpable after it inherited the exact listener inode; `/proc/<pid>/fd` then produced real `EACCES` with the child's PID, UID, and inode context. The default target-scoped selector returned the one visible parent and retained the unreadable global record without claiming whole-host exclusivity; an injected target degradation refused final ownership. The historical failure class is an unreadable same-UID process boundary, not a corrupt listener table or missing scanner.

The final correction binds every response PID/server lifetime and performance-record PID to the selected process, keeps non-permission `stat` failures fatal, preserves degraded/fatal/timeout final-scan causes, and enforces the timeout across table reads, process enumeration, every process/descriptor operation, and final `continue` paths. Retained current-subject outputs under `/tmp/v0717-task52-evidence.rs2tZup3/` are core 76 passed, architecture 26 passed, and seven-owner focused 628 passed with two environment skips. Task 52's findings-blind audit recorded zero MUST/SHOULD issues after the two blocker corrections; Task 54's independent audit recorded zero findings. The wording-only closeout preserved docstring-stripped AST SHA-256 `013f8f5d3b74325f350ece35b5b7f320d43ea6fccfb745350c1ec9bb5f15f956` and the 651-line architecture pin.

### Current-head isolated measurement, classification pending

On pre-rebase `HEAD` `4a0e94be2`, each exact target ran eight times sequentially on a quiet host with `python3 -m pytest "<node id>" -q -p no:randomly`. All 24 processes exited 0 and each log independently reported one passing test.

| Defect | Failures / attempts | Wall seconds, min-max | Classification from this arm |
| --- | ---: | ---: | --- |
| 2 | 0/8 | 29.97-32.51 | Unresolved; solo execution removes the predecessor page and shared WebDriver state that can trigger this browser test. |
| 3 | 0/8 | 10.95-13.32 | Unresolved; the exact one-sided 95% upper bound after zero failures in eight attempts is 31.2%. |
| 4 | 0/8 | 5.18-6.95 | Unresolved; the exact one-sided 95% upper bound after zero failures in eight attempts is 31.2%. |

The 0/8 rates satisfy the minimum attempt count, not the checkbox's evidence-backed classification requirement. A defect with a true 12.5% failure rate still produces zero failures in eight attempts 34.4% of the time.

A second sequential read-only arm then ran 129 pytest invocations and 174 individual test executions over 26.9 minutes. Every invocation exited 0 and every log carried the expected independent verdict:

| Defect | Additional current-head evidence | Result | Classification |
| --- | --- | ---: | --- |
| 2 | Immediate predecessor plus target, 8 ordered pairs; whole browser file, 3 file-order runs | 0/8 pairs; 0/3 files, each 8/8 tests | Unresolved. The pair bound remains 31.2%, the file-order bound is 63.2%, and neither reproduces the historical full-gate failure. |
| 3 | Immediate predecessor plus target, 8 ordered pairs; exact solo target extended from 8 to 59 total | 0/8 pairs; 0/59 solo | Not reproduced on current `HEAD` above a 4.95% one-sided 95% isolation bound. The retained fail-closed listener-census signatures still classify the historical root as a product defect. |
| 4 | Immediate predecessor plus target, 8 ordered pairs; exact solo target extended from 8 to 59 total | 0/8 pairs; 0/59 solo | Not a Defect 4 rate: current `HEAD` deleted the bounded `wait(timeout=2)` predicate that produced every retained occurrence, so these passes cannot exercise the historical failure. |

No product or test edit followed these clean arms. Audit 23 shows that more quiet-host solo repetition would lower only Defect 3's current isolation bound, would not distinguish the Defect 2 watchdog from its P0 e3 resource owner, and would measure a different unbounded predicate for Defect 4. The two grouped checkboxes remain open on the named owner work above, not on missing failed-node persistence or more repetition of unchanged targets.

### First schema-5 full-gate observation

The first canonical current-subject attempt on 2026-08-25 used HEAD `4a0e94be2`, `tools/check.py` sha256 `7338d3dbf117`, and `tests/test_check_runner.py` sha256 `d8382a332668`. It exited 1 after 730.34 seconds and preserved its report at `/tmp/v0716-p0e1-current-subject-gate-20260825-0623/performance-report.json`. Defects 2, 3, and 4 all passed in their owning lanes; Defect 1 also did not appear in the persisted failure set. This is one clean in-gate observation for each node, not a rate or classification.

The browser lane persisted one new exact failure, `tests/test_gate_agent_state.py::test_f6_realistic_consumers_converge_to_the_published_roster_revision`, with zero unresolved rows and no truncation. The failure was a 20-second timeout waiting for 14-session higher-revision consumer convergence. The isolated arm below reproduced the same signature, so this is now Defect 5 rather than a single loaded observation.

The two non-browser persisted failures were the architecture-budget and text-shape guards caused by the uncommitted failed-node persistence tests. They remain owned by P0 e2 and are not Defects 2-4 evidence. Certification separately returned seven JUnit-resolved host-qualification errors and `host_unqualified_postflight`; those certification outcomes are not functional-lane failures and are not pooled into this queue's defect population.

## Defect 5: 14-session consumers can fail to converge to the published higher revision

`tests/test_gate_agent_state.py::test_f6_realistic_consumers_converge_to_the_published_roster_revision`

Eight sequential exact-target attempts ran on 2026-08-25 through the isolated-container path with `-q -p no:randomly`. Attempt 3 reproduced the full-gate signature exactly; the other seven passed. The evidence is preserved under `/tmp/v0716-p0e1-f6-classification-02/`.

| Attempt | Exit | Wall seconds | Result |
| ---: | ---: | ---: | --- |
| 1 | 0 | 22.22 | passed |
| 2 | 0 | 19.54 | passed |
| 3 | 1 | 41.10 | 20,000 ms timeout waiting for 14-session higher-revision consumer convergence |
| 4 | 0 | 19.77 | passed |
| 5 | 0 | 23.33 | passed |
| 6 | 0 | 20.59 | passed |
| 7 | 0 | 21.00 | passed |
| 8 | 0 | 22.80 | passed |

The observed isolated rate is 1/8, or 12.5%, with an exact one-sided 95% upper bound of 47.1%. The failure consumed the 20-second wait and then failed at `tests/test_gate_agent_state.py:486`; passing attempts took 19.54-23.33 seconds. A loaded full gate is not necessary for this defect, so it is a real race rather than a contention-only failure. Product ownership versus test synchronization remained unresolved after this arm because the failing state proved only that the consumer did not reach the higher revision before the deadline.

The canonical immediate predecessor is `tests/test_gate_agent_state.py::test_f5_realistic_roster_count_matches_its_stale_session_breakdown`. Eight read-only ordered predecessor-plus-target attempts then ran on the same current subject under `/tmp/v0716-p0e1-f6-pair-03/`; every F5 passed, and F6 failed three times with the same signature:

| Attempt | Exit | Wall seconds | F5 | F6 |
| ---: | ---: | ---: | --- | --- |
| 1 | 1 | 49.77 | passed | **20,000 ms 14-session higher-revision convergence timeout** |
| 2 | 0 | 29.26 | passed | passed |
| 3 | 1 | 47.09 | passed | **same timeout** |
| 4 | 0 | 30.54 | passed | passed |
| 5 | 1 | 50.06 | passed | **same timeout** |
| 6 | 0 | 26.81 | passed | passed |
| 7 | 0 | 31.13 | passed | passed |
| 8 | 0 | 29.42 | passed | passed |

The pair arm is 3/8 F6 failures against 1/8 solo; Fisher exact two-tailed gives `p = 0.5692`, so the small arms do not distinguish those rates and do not support blaming F5. Across all 16 quiet F6 executions the observed rate is 4/16, or 25.0%, with a 9.0%-48.4% one-sided interval. All five retained failures across the full gate, solo, and pair arms are field-identical: `metadataCount = 14`, `metadataRequestCount = 2`, `tailText = 'Live status is waiting for the chart snapshot'`, and the same 20,000 ms timeout. The first recorded disagreement is therefore between completed metadata publication and consumer revision advance: metadata reached all 14 sessions and the second build was requested, while the consumer revisions never exceeded the initial revision. Load is not required, F5 contribution is not measured, and product versus test-synchronization ownership remains unresolved until the publication path names the first incorrect transition and its owner.

The bounded read-only owner trace `v0716-p0e1-f6-owner-trace-04` identified and coordinator source verification confirmed one product defect in `static_src/js/yolomux/99_terminal_boot.js`. `applyAutoApprovePayload` deliberately holds a sealed status payload at lines 5504-5506 when its sessions are absent from metadata, but `applySessionMetadataPayload` has exactly one release site at lines 5981-5984 and three terminal returns before it: `malformed_payload` at 5944, `superseded_request` at 5955, and `older_work_graph_generation` at 5976. No timer, poll, or sibling path revisits the held payload. The violated invariant is that every deferred sealed status payload must eventually be applied or explicitly discarded with a recorded reason; a terminal metadata outcome cannot silently strand it.

This proves a product defect, but the saved F6 failures still do not prove which early return fired: none records `transcriptMetadataState.lastApply.reason`. The chart text is not the failing gate; `Live status is waiting for the chart snapshot` reads the separate stats-side `jsDebugStatsPollState.agentWindowSnapshotRevision`, while F6 reads revisions stored in `autoApproveStates`. Treat it as an independent downstream symptom. The next subject must add `lastApply.reason` to the F6 observation without changing product behavior and reproduce the exact failure before attributing F6 to this owner. The existing `tests/layout_async.test.js` harness already drives `superseded_request` and `older_work_graph_generation`, so any eventual fix also requires a deterministic no-timeout regression for deferred-payload reconciliation across every terminal metadata outcome.

The observer-only subject added per-snapshot `metadataApply` and `deferredSealed` projections without changing product state. `tests/layout_async.test.js` passed 180/180, the focused file passed 6/6, and all 16 sequential F6 attempts passed with zero survivors before or after each attempt. This arm captured no failing `lastApply.reason`, so it does not attribute F6 or prove a rate change. Both projections execute inside the 20 ms polling snapshot; prior uninstrumented arms failed 4/16 against 0/16 here (`p = 0.1012`, Fisher exact two-tailed), and `P(0/16 | p = 0.25) = 0.0100`, so observer perturbation is a credible concern. Do not pool the arms. Defect 5 remains open; the next subject must capture the existing reason and deferred state exactly once on wait failure, prove that failure-only projection is serializable, and stop on its first failure or bounded exhaustion.

The corrected arm removed both projections from the 20 ms snapshot and passed the Node suite 180/180, the focused file 7/7, and 16 sequential F6 attempts with zero survivors. The second 0/16 result after removing polling work weakens that work as the explanation for task 05, but no failure reason was captured and no rate change is claimed. Coordinator audit found that the new diagnostic `catch` also encloses the post-wait success snapshots and `done()` call; an exception after successful convergence would therefore run diagnostics even though the wait passed. The observer design is not accepted until the catch is narrowed to wait rejection only. Defect 5 remains open. After that correction, this bounded exhaustion is sufficient to move to the deterministic shared-owner regression and fix because the source trace already proves the stranded deferred-payload defect independently of F6 attribution.

Task 07 narrowed the observer to one diagnostic call site guarded by `waitFailure`, whose only non-null assignment is the convergence wait's catch. Post-wait reporting faults now use `postWaitError` with zero diagnostic captures. The Node suite passed 180/180 and the focused file passed 7/7. That file contains seven tests, but the corrected F6 success branch itself ran once; do not count all seven as executions of that branch. The real-rejection branch and `deferred.present = true` remain unobserved, so F6 attribution remains unresolved. The observer design is accepted for future failures. Work now moves to a deterministic shared-owner regression and fix for the independently source-proven stranded deferred-payload invariant across every metadata terminal outcome.

Task 08 produced a deterministic red/green and a clean focused ladder, but coordinator source audit rejected closure. The regression omits `committed_render_superseded`; its claimed discard case never holds a payload and asserts `none`, so `discarded_superseded_revision` is untested; and `appliedAgentWindowSnapshotRevision()` uses the maximum revision from any session, which can discard a held multi-session payload after only one session overtakes it. A `retained_awaiting_metadata` label is also insufficient unless a concrete already-scheduled owner is proven to revisit that holder. The ordered-pair arm found one F6 failure in eight with F5 green, but `yolomux_lib/filesystem/git_ops.py` changed after pair 4, so the eight attempts are not one frozen-subject rate. Pair 4 proves only that the last metadata apply succeeded at generation 7 in this post-fix failure; pytest truncated the deferred state, and this does not settle historical F6 attribution. Defect 5 remains open pending deterministic root-class corrections and a constant-subject rate.

Task 09 corrected the task-08 authority and coverage defects. The final matrix now covers all four terminal outcomes, a real held-payload discard, partial per-session overtake retention, full per-session discard, and retained-to-applied/discarded transitions. The final red failed on the global-max authority bug; the rebuilt bundle passed the async suite 181/181, all 19 Node shards, static-build check, and focused pytest 7/7. The concrete product correction is accepted. One design gap remains filed: `retained_awaiting_metadata` depends on a future server-driven metadata event and has no unconditional local bound. Defect 5 remains open because the post-fix rate crossed subject drift and the real failure diagnostic was truncated. The next subject must emit a compact failure record and verify an unchanged tracked diff before every ordered pair.

Task 10 made that failure record readable without changing product behavior. The formatter regression used a synthetic 1,000-observation payload, preserved every apply/deferred field, omitted observations, and kept the one-line JSON below 1,200 characters; `tests/test_gate_agent_state.py` passed 9/9 and `tests/layout_async.test.js` passed 181/181. All sixteen pair-boundary manifest comparisons matched, the tracked diff hash stayed `2d9f5d3113766bce49cd679a7e580e3cbdcd418a3793dcc9f3953623fd1dc088`, F5 passed 8/8, and F6 failed 1/8. That failure had no deferred payload, `lastApply.reason = applied`, all 14 consumer revisions advanced from 1 to 7, and every recorded visible predicate was satisfied immediately after timeout, excluding deferred-seal attribution for this occurrence only. The compact record omitted `metadataRequestsBefore`, leaving two unresolved cases: the baseline was already 2 and the request-count predicate began impossible, or the baseline was below 2 and convergence crossed the 20-second deadline. Defect 5 remains open; the next unchanged-subject arm must add that baseline to the compact record and classify the first failing transition without raising the timeout, retrying, or sleeping.

Task 11 settled that split for two new occurrences on one frozen subject. The compact record now includes `metadataRequestsBefore`; the focused file passed 9/9 before the post-arm classifier and 10/10 afterward, while the async suite passed 181/181 both times. The accepted arm ran eight ordered F5-to-F6 pairs with all sixteen boundary manifests matching and no survivors: F5 passed 8/8 and F6 failed 2/8. Both failures had `metadataRequestsBefore = 2`, final `metadataRequestCount = 2`, all 14 sessions/metadata/status/model rows present, and every consumer revision advanced from 1 to 7, so the request-count clause `2 > 2` was impossible before the wait began even though convergence succeeded. Source tracing confirms the test's inline `transcripts_changed` route calls `applyTranscriptsPayload` and `applySessionMetadataPayload` without an HTTP metadata request; the alternative missing-data route requests metadata. These two occurrences are therefore a test-synchronization defect: F6 incorrectly requires one transport mechanism instead of the observable convergence contract. Earlier preserved F6 failures remain unclassified because they lack the baseline field. The post-arm deterministic classifier records this impossible-predicate shape, but Defect 5 remains open until F6 accepts both legitimate delivery paths and a newly frozen post-fix rate is measured.

Task 12 corrected the core F6 convergence predicate through one shared JavaScript owner used by the real wait and deterministic regression. The regression proves a fully converged inline payload with an unchanged request count fails the retired transport clause and passes the corrected predicate, while missing counts, short/null revision arrays, and stalled/regressed revisions remain red. The focused file passed 12/12 and the async suite 181/181 before and after a frozen eight-pair arm; all sixteen boundary manifests matched, F5 passed 8/8, and F6 passed 8/8. This is a post-fix rate of 0/8 with a 31.2% one-sided 95% upper bound; it does not demonstrate a rate change against the prior 2/8 arm (`p = 0.4667`). An external owner removed the unrelated dirty `git_ops.py` timeout change before Task 12; the worker stopped before editing, the coordinator verified the new clean HEAD identity, and the task re-froze rather than restoring another owner's work.

Independent Audit 13 accepted the core predicate but rejected Defect 5 closure on two P1 evidence-integrity defects and five P2 gaps. The live compact record still applies `metadata_request_clause_unsatisfiable` even though that clause no longer exists, so a genuine current convergence failure can be misclassified; and a failure-path reporting exception can discard the original wait error and falsely label the path as successful convergence. Required corrections also include passing the request baseline at every record site, bounding `detailText` while recording truncation, eliminating the weaker Python convergence restatement, recording exact commands and exit codes in focused logs, and removing dead guard implications from the new arm evidence. Defect 5 remains open pending those corrections, a newly frozen post-correction arm, and independent re-audit.

Tasks 14 and 16 corrected the Audit 13 findings and the next independent audit's architecture-budget and evidence gaps. The correction removed the retired classifier, preserved wait and reporting errors separately, required the request baseline, routed convergence and outcome decisions through single JavaScript owners, bounded every untrusted compact-record string, removed weaker Python restatements, renamed the `sessions`-shadowing local, corrected the retention comment, rebuilt the generated bundle, and updated the exact architecture pins. The architecture lane first reproduced four subject-owned violations, then passed 26/26 before and after the frozen Task 16 arm with zero violations and zero stale pins. The two Task 14 reds are reconstructions of intermediate uncommitted implementations, not substituted-tree reds; they prove the reconstructed logic is defective, not a recoverable historical tree.

Task 18 closed the last two P2 gaps from Audit 17. A live deterministic red showed JSON-escape expansion could still return a 2,394-character record against the 1,200-character contract; the corrected shared drop owner returned 987 characters with every surrendered field named, and now raises rather than silently returning an oversized record if the fixed-shape core cannot fit. The last dead full-result assertion was removed. The focused post-arm ladder passed architecture 26/26, `tests/test_gate_agent_state.py` 18/18, async Node 181/181, and static-build check on the final frozen subject.

Independent Audit 19 accepted Defect 5 for closure. The named first incorrect transitions are the retired transport-specific request-count clause in the test and the product path where terminal `applySessionMetadataPayload` outcomes could strand a deferred sealed payload; the shared owners are `f6ConvergenceSatisfied` and `reconcileDeferredSealedAutoApprove`. Deterministic coverage includes every metadata terminal outcome, partial and full per-session overtake, the convergence predicate, and the outcome/error taxonomy. The final Task 18 arm ran eight ordered F5-to-F6 pairs on byte-identical manifest `1e2b0194632314bed77fbcdd629b9ff10f0eac73f5044b5c9c5849d068a50bec`: the F6 convergence predicate held 8/8, for 0/8 convergence failures and a 31.2% exact one-sided 95% upper bound. This does not demonstrate a rate change against the prior 2/8 arm. The arm was not clean: the F6 node failed 2/8 and F5 failed 1/8 at the shared browser-fixture boundary on the separate local-service faults filed as Defect 6 below. `retained_awaiting_metadata` still has no unconditional local bound and remains explicitly filed, but the literal Defect 5 criterion permits fix or file and Audit 19 confirmed that gap does not block this checkbox.

Do not raise the timeout, retry, reconnect, sleep, serialize, or fix only one return site. The shared terminal-path owner and every sibling outcome must be covered together.

## Defect 6: shared browser-fixture boundary fails on local-service RPC faults

Task 18 preserved three first-attempt failures in eight ordered pairs on the frozen subject. None is the Defect 5 convergence signature: the compact F6 failure record did not fire in any pair, and both F6 bodies satisfied the 20-second convergence wait before failing during fixture teardown.

| Pair | Node | Pacific wall time | First failing transition |
| ---: | --- | --- | --- |
| 1 | F6 | 2026-08-25 10:34:12 AM PT | `local-service:jobd`, `action=result`, `TimeoutError` at `yolomux_lib/local_services/rpc.py:721` while `connection.recv` crossed the 500 ms client deadline at 501.150 ms. |
| 2 | F6 | 2026-08-25 10:36:00 AM PT | `local-service:jobd`, `action=result`, the same `TimeoutError` owner at 501.184 ms. |
| 7 | F5 | 2026-08-25 10:39:27 AM PT | `local-service:statusd`, `action=wait_generation`, `FileNotFoundError` at `yolomux_lib/local_services/rpc.py:910` because the Unix socket did not exist; fixture shutdown then reported `owned=0, supplied=1` for the exact live origin. |

Task 20 ran 24 qualified-host invocations on the same frozen subject: eight ordered F5-to-F6 pairs, eight F6-only invocations, and eight F5-only invocations. Across 32 browser-fixture boundary executions, all six checked-in host-qualifier samples passed without an override or recovery window, all manifests and survivor checks matched, and neither a Defect 6 signature nor an F6 convergence signature appeared. The comparable boundary rates are therefore Task 18 at 3/16, with a 41.7% exact one-sided 95% upper bound, and Task 20 at 0/32, with an 8.9% upper bound. The two-tailed Fisher exact comparison is `p = 0.03238`; under the Task 18 point rate of 0.1875, the probability of observing zero failures in 32 boundaries is 0.0013.

Independent Audit 21 accepted the measurements with this classification: **UNRESOLVED.** A real, unimplemented product lifetime and deadline gap owned by `DOIT.p1.e5.backend-lifetime-supervision.md`, preserved here as three exact occurrences. It did not reproduce in 32 browser-fixture boundary executions across three arms on a host that passed the gate's own qualifier at all six boundaries with no override and no recovery window. The failing arm's host condition was never measured, so the association between host state and these failures is observational and remains unestablished in either direction.

The response-timeout signature first surfaces at `yolomux_lib/local_services/rpc.py:721`; its 0.5-second defaults are owned by `yolomux_lib/local_services/client.py:407` and `yolomux_lib/local_services/runtime.py:46`. The absent-socket signature first surfaces at `yolomux_lib/local_services/rpc.py:910`. Fixture lifetime ownership is at `tests/gate_harness.py:1953` and `tests/gate_harness.py:1967`; the shared fixture surfaces the faults through `tests/gate_harness.py:2994`, `tests/gate_harness.py:3106`, `tests/browser_helpers/browser_console.py:957`, and `tests/browser_helpers/browser_console.py:1047`. Do not duplicate the lifetime fix here. Do not run a deliberately loaded-host experiment without explicit user authorization: that attribution experiment belongs to P0 e3, while the product lifetime fix belongs to P1 e5.

## Why the evidence is this thin, and the fix for that

The audit could recover failed node IDs for **under 11% of failing lanes**: 5 of 53 co-failing `pytest` outputs, 2 of 47 browser, 4 of 35 E2E. Everything above is what could be read from that fraction. There may be more recurring defects; they are currently invisible.

Persisting failed node IDs into the run report is required before this queue can be called complete. It is carried as a checkbox in `DOIT.p0.e2.gate-tiering-and-serialization.md`; do not duplicate the work here, but do not treat this queue as closed until that data exists and has been re-read.

## Statsd disk churn moved out of this queue

An earlier revision carried a "Defect 5" covering statsd write amplification, hourly `VACUUM`, and telemetry cadence. **That work now lives in `DOIT.p0.e3.statsd-resource-bounds.md`**, which already owned statsd disk, memory, and CPU. Every measurement written here was moved there intact; none was dropped.

This queue excludes statsd because the retained evidence establishes a host-resource amplifier but no causal link to Defects 1-4. The historical Defect 1 run observed one failure in eight without demonstrated contention; the fixed current lineage measured zero in 23.

**It does not establish that statsd cannot widen the race window, and an earlier revision of this file overreached by saying disk pressure "is not what makes it fail".** There is no per-run process and disk census proving statsd was absent during those eight attempts, and "idle" is a weak word here: even the disposable CPU-only daemon wrote 53,423 B/s while idle, and the retained live production interval wrote 494,113 B/s before any compaction burst. An idle *host* is not a quiet *disk*.

Keep the product-race fixes and any host-load comparison as separate claims. Do not attribute a rate change in Defects 1-4 to a statsd change without the controlled A/B that `p0.e3` owns - and equally, do not cite this queue as having ruled statsd out.

## Plan

- [x] **Trace Defect 1 to its owner.** DONE: parent `113a9a85e` deferred accepted generations in `debugGraphFocusedControl`, `refreshDebugGraphElement`, and `flushDeferredDebugGraphRefresh` while a chart toggle remained focused. The mirror-key and readiness-generation interpretations are retired. This is a product defect because a normal focused control could leave a ready chart frozen.
- [x] **Fix Defect 1 at that owner.** DONE in ancestor `f716e980e`: remove focused-control deferral and repaint accepted generations immediately while retaining active-gesture ownership. No new product edit was required on v0.7.16 `HEAD`.
- [x] **Add a deterministic regression that fails on the state transition, not on timing.** DONE: the current 96-test Node suite includes focused-control generation convergence and pending-repaint release. Current and shadow-current pass 96/96; substituting only parent `85_debug_panel.js` returns 92 passed, 4 failed, exit 1, including the exact pending repaint left set with zero forced repaint.
- [x] **Re-run Defect 1 at least 20 times in isolation after the fix.** DONE: 23 sequential current-HEAD runs, 0 failures, min 41.04s, max 52.11s, mean 46.84s. The historical 1-in-8 test used a different predicate on a non-ancestor lineage and remains discovery evidence rather than a like-for-like baseline.
- [ ] **Establish evidence-backed classifications for Defects 2, 3, and 4 from the retained signatures and current-subject arms.** PARTIAL 2026-08-25: Audit 23 recovered complete historical occurrence sets and mixed-subject denominators. Tasks 50-54 classified and independently accepted Defect 3's real same-UID non-dumpable `EACCES` boundary and root-class correction. Tasks 45-48 classified and independently accepted Defect 4's maintenance cold-start owner, bounded invariant, shared failure diagnostic, and 0/8 frozen-subject rate. Defect 2 remains unresolved pending the P0 e3 controlled attribution, so this item stays open.
- [x] **Fix or file each of Defects 2-4** with a named owner, deterministic regression, and post-fix current-subject evidence where the predicate still exists. DONE 2026-08-25: Defect 2 is filed to `DOIT.p0.e3.statsd-resource-bounds.md`; Tasks 50-54 close Defect 3 on the final audited six-path subject with real-kernel errno/owner evidence, deterministic root-class regressions, and 628-passed focused evidence; findings-blind Task 48 closes Defect 4 on subject `9fbf9ba191b9b8cd60e9790e02284a7b38dbeac88b36b1700b590d81640e4589`.
- [x] **Re-read the failed-node-ID data once it is being persisted** and add any further recurring defect found to this queue. DONE: the first schema-5 report persisted the exact F6 roster-convergence node with zero unresolved rows and no truncation; an independent isolated arm reproduced the same signature at 1/8, so it is now Defect 5 above. The two task-owned guard failures were corrected in P0 e2 and were not added as product defects.
- [x] **Trace, classify, and fix or file Defect 5** with the same standard as Defect 1: at least eight ordered predecessor-plus-target runs, the first incorrect publication or consumer transition, a named owner, a deterministic regression, and a post-fix repetition rate. DONE: Audit 19 accepted the two named transitions and shared owners, deterministic root-class coverage, and the final frozen 0/8 convergence-signature rate with a 31.2% upper bound. The final arm was not clean: F6 failed 2/8 and F5 1/8 on the separate shared local-service boundary faults now filed as Defect 6.
- [x] **Classify and fix or file Defect 6** with quiet-host ordered repetition and an isolated reproduction. DONE 2026-08-25: Task 20 measured zero Defect 6 signatures in 32 qualified-host browser-fixture boundaries across ordered, F6-only, and F5-only arms. Audit 21 classified the retained 3/16 Task 18 boundary failures as an unresolved product lifetime and deadline gap filed to `DOIT.p1.e5.backend-lifetime-supervision.md`, with any association to the unmeasured Task 18 host state still unestablished. Task 49 then reproduced the missing isolated transition through the fixture's public lifetime seams: the same live `jobd action=result` call returned a typed payload before fixture-owned retirement and escaped as `FileNotFoundError` at `rpc.py:910` afterward. The exact red remains under `/tmp/v0716-task49/`; candidate source was byte-restored, and independent closeout reran the restored focused file at 6/6 plus architecture at 26/26. Preserve the separate 500 ms live-but-silent `jobd` timeout signature; do not duplicate the lifetime fix or start a deliberately loaded-host experiment here.

## Gotchas

- **One run is not a measurement, in either direction.** This defect was first declared "a real bug, not load" on a single failing run, and the very next isolated run passed. Both a red and a green need repetition before they mean anything. Every rate in this file is `failures / attempts` with both numbers shown, and every rate added must be too.
- The historical isolated run shows the defect can appear without demonstrated contention. A controlled A/B is required before claiming how load or statsd changes its rate.
- Do not serialize, retry, sleep, lower concurrency, or relax an assertion to make any of these green. That is the documented wrong fix and it is what has kept the gate red at 34.7% rather than surfacing these four.
- The failing run takes longer than the passing run - 71.25s against about 41s - because the timeout is the failure. Do not read a slow run as a hung one.
- Browser tests here share a driver and page. Before changing product code for any of Defects 2-6, reproduce the predecessor-then-target pair, per the project rule on shared-state browser tests.

## Done Criteria

- Defect 1 has a named historical owner, an already-landed ancestor fix, deterministic red-parent and green-current evidence, and a current-HEAD isolated rate of 0/23. The historical 1-in-8 run is retained as discovery evidence, not a like-for-like baseline.
- Defect 2's exact stats-watchdog signature is owned and resolved through the P0 e3 controlled attribution; the separate retired `cpuAxisMax == 100` mismatch remains distinguished from that population.
- Defect 3 has an explicit listener-census OSError contract, preserved cause/errno evidence, a deterministic regression, a root-class product correction, and post-fix current-subject evidence.
- Defect 4's bounded-wait removal and intent are identified; a bounded deterministic invariant is restored or an explicit non-hanging replacement is filed and verified.
- Failed node IDs are persisted by the gate, that data has been re-read, and any further recurring defect it reveals is listed here.
- Defect 5 has an ordered predecessor-pair rate, a named first incorrect transition and owner, a deterministic regression, and a post-fix repetition rate.
- Defect 6 has quiet-host ordered repetition, an isolated reproduction, an evidence-backed classification, and a fix or a filed owner without duplicating the P1 e5 lifetime queue.
- `DOIT.p0.e2.gate-tiering-and-serialization.md` is unblocked: its A/B experiment can run on a sample that no longer contains these defects.
