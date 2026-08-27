# statsd resource-state projection, `/livez` and `/readyz` — specification

Task `YOLO-V0717-E3-READYZ-33`. Measured on `keivenc-linux1`, 2026-08-25, against live statsd pid `2088396` (read-only `/proc`) and against source at `3d1fe4da8` plus the standard 32-path base diff. Specification only; nothing here is implemented.

`grep -rn 'livez\|readyz' yolomux_lib/stats_current/` returns nothing, so both endpoints are new. The web `/healthz` is not to be overloaded.

## 0. The three measurements that shape every decision below

**One.** `_status()` at `service.py:4735` opens with `with self.work_lock:` and then `with self.cache_lock:`. `work_lock` is the lock the materializer worker holds across a build. A separate lane measured the worker burning 800–940 ms of CPU in a single post-readiness burst while the socket thread got 20–30 ms. **Any endpoint routed through `_status()` inherits that stall.** A health endpoint that blocks for ~0.9 s behind the subject it is measuring is the "worse than none" case. So `/livez` and `/readyz` must not call `_status()`.

**Two.** `/proc/<pid>/smaps_rollup` — the source for PSS, USS and the anon-versus-file split the item asks for — costs **20,794 µs median** (p95 21,208 µs, n=200) against this 1.5 GiB daemon. Every other candidate read costs tens of microseconds. It is **1,270× more expensive than `/proc/<pid>/status`**, because the kernel walks every page-table entry, and it takes `mmap_read_lock` on the target, which blocks the target's own `mmap`/`munmap`. A process whose USS is 96.40% pymalloc arena is precisely one that churns `mmap`. **`smaps_rollup` is not free and is not side-effect-free.** Section 3 replaces it.

**Three.** Live statsd right now reports `VmRSS 630,200 kB` but `VmSwap 1,015,336 kB`. **A projection reporting RSS alone would call this daemon 615 MiB and healthy while it is holding 1,593 MiB of anonymous pages, 991 MiB of which the kernel has evicted to swap.** `RssAnon + VmSwap = 1,631,248 kB` against `VmHWM 1,638,792 kB` — within 0.46%. The memory budget quantity must be `RssAnon + VmSwap`, never `VmRSS`.

## 1. Field list, with source and measured cost

All costs are medians over 200 reads against live statsd pid `2088396`, this host, tonight.

### 1a. Externally observable — no cooperation from statsd, no lock, no GIL

These need only the pid. Any process can read them, including while statsd is wedged. **This is the whole of `/livez` and most of `/readyz`.**

| field | source | cost | new? |
|---|---|---|---|
| steady anon memory | `/proc/<pid>/status` `RssAnon` | **16.4 µs** for the whole file | new |
| file-backed memory | `/proc/<pid>/status` `RssFile` | same read | new |
| shmem | `/proc/<pid>/status` `RssShmem` | same read | new |
| swap | `/proc/<pid>/status` `VmSwap` | same read | new |
| peak RSS | `/proc/<pid>/status` `VmHWM` | same read | new |
| peak virtual | `/proc/<pid>/status` `VmPeak` | same read | new |
| threads | `/proc/<pid>/status` `Threads` | same read | new |
| fd table size | `/proc/<pid>/status` `FDSize` (64 live) | same read | new |
| **open fds** | `len(os.listdir('/proc/<pid>/fd'))` (16 live) | **12.7 µs** | new |
| CPU time, for progress | `/proc/<pid>/stat` fields 14–15 | **39.7 µs** | new |
| process state, for wedge detection | `/proc/<pid>/stat` field 3 | same read | new |
| context switches | `/proc/<pid>/status` `voluntary_ctxt_switches` | 16.4 µs read | new |
| block I/O | `/proc/<pid>/io` `read_bytes`/`write_bytes` | **37.2 µs** | new |
| **DB / WAL / SHM sizes** | `Path.stat()` on the database, `-wal`, `-shm` | **33.0 µs** for all three | new |
| TEMP size | `Path.stat()` over the SQLite temp directory — see §6 | ~33 µs | new |

**Total fast path: `/proc/<pid>/status` + `/proc/<pid>/stat` + fd count + three `stat()` calls ≈ 102 µs.** That is the entire externally observable projection, and it costs one four-hundredth of a single `smaps_rollup`.

### 1b. Internally sourced — needs statsd, already exists in `_status()`

Every one of these is already built today; the work is exposing them **without** `work_lock`.

| field | source | already exists? |
|---|---|---|
| build phase | `materializer_state`, `service.py:4837` | yes |
| building flag | `self._building`, `service.py:4898` | yes |
| ring cursor | `self._ring_published_cursors`, read at `service.py:3933` | yes |
| last ring source generation | `ring_writer.last_source_generation`, `service.py:4911` | yes |
| **staged ring cells** | `ring_writer.pending_cells`, **`service.py:4908`** | yes |
| dirty materializer cells | `materializer.dirty_cells`, `service.py:4897` | yes |
| cache generation | `cache_generation`, `service.py:3253` | yes |
| source generation | `self._latest_source_generation` | yes |
| failed builds / last failure | `build.failed`, `build.last_failure` | yes |
| encoded wire-cache bytes | `cache.shared_bytes` + `cache.private_bytes` | yes |
| migration state | `migration.state` | yes |
| ring failure | `ring_writer.failure` | yes |

**`append_persistence` does not exist in this worktree.** `grep -rn append_persistence --include=*.py .` returns nothing outside tests. If a sibling lane is adding it, its fields fold into 1b at zero extra cost; if not, the sizes it would have carried are already covered by the `stat()` reads in 1a.

### 1c. Genuinely new and genuinely expensive

| field | source | cost | verdict |
|---|---|---|---|
| PSS, USS, `Pss_Anon`/`Pss_File` split | `/proc/<pid>/smaps_rollup` | **20,794 µs**, takes target `mmap_read_lock` | **not on any health path** — §3 |
| retained-owner accounting | walk of the daemon's own live objects | seconds; needs `cache_lock` | **on demand only** — §7 |

## 2. Side-effect freedom, proven rather than asserted

- **Nothing in §1a enters the statsd process at all.** Reading `/proc/<pid>/*` and `stat()`ing files is done by the *caller*. statsd is not scheduled, not interrupted, holds no lock, and does not even learn it happened. This is the strongest possible form of the property, and it is why the split matters.
- **Nothing in §1a or §1b may take `work_lock`.** §1b fields are plain attribute reads and `len()` on a `set`. Under CPython each is a single bytecode-level operation, so a read is never torn; the cost is that a *set* of them may not be mutually consistent. **That is the correct trade**: a health endpoint wants a recent inconsistent snapshot, not a consistent one that waited 900 ms. Document it as "sampled, not transactional".
- **No write connection is opened.** All sizes come from `stat()`, never from `PRAGMA page_count`, which would need a connection and a read transaction.
- **The one honest residual: the GIL.** Any §1b field read inside statsd waits for the GIL, and the worker holds it for the full 800–940 ms build burst. **Lock-free is not stall-free in CPython.** This is why `/livez` is specified in §5 from §1a alone.
- **`smaps_rollup` is the one read that does perturb.** It takes `mmap_read_lock` on the target, blocking that process's own `mmap`/`munmap`/`brk` for its ~20.8 ms duration. Excluded from all health paths, rate-limited elsewhere.

## 3. Replacing PSS/USS on the fast path — with the divergence measured

The item asks for PSS/USS and the anon-versus-file split. §0 shows the only source costs 20.8 ms and perturbs. **Measured substitution, live statsd, both sources read back to back:**

| quantity | cheap source (16.4 µs) | expensive truth (20,794 µs) | divergence |
|---|---|---|---|
| Rss | `VmRSS` 630,200 kB | `Rss` 630,200 kB | **0.000%** |
| anonymous | `RssAnon` 615,912 kB | `Pss_Anon` 615,912 kB | **0.000%** |
| swap | `VmSwap` 1,015,336 kB | `Swap` 1,015,336 kB | **0.000%** |
| file-backed | `RssFile` 14,288 kB | `Pss_File` 1,334 kB | over-counts by 12,954 kB |
| whole-process | `VmRSS` 630,200 kB | `Pss` 617,246 kB / `Uss` 616,968 kB | `VmRSS` reads **2.06% / 2.10% high** |

**`RssAnon` equals `Pss_Anon` exactly**, because statsd's anonymous memory is entirely private — it forks nothing and maps no shared anonymous regions. The *only* divergence is the file-backed share, 12,954 kB of libc and libpython text shared with every other Python process on the box, and it is near-constant.

**Specification.** The fast path reports `anon_bytes = RssAnon`, `file_bytes = RssFile`, `swap_bytes = VmSwap`, `peak_rss_bytes = VmHWM`, each labelled `source: "status"`. It reports **no PSS or USS field at all** rather than a cheap number wearing an expensive name. A caller wanting true PSS/USS calls the on-demand endpoint of §7, which is rate-limited and never on a health path.

**The budget quantity is `anon_bytes + swap_bytes`**, for the reason in §0-three: this daemon reads 601 MiB resident and is actually holding 1,593 MiB.

## 4. `/readyz` — fail closed

`/readyz` answers "can this process serve a correct snapshot right now". It is **not** `cache_ready_event`.

### Why mirroring the event would be wrong

A sibling lane measured, on **6 of 6** cold starts, that `cache_ready_event` fires while the served window's ring is still staged: at that instant the daemon reports pending work, and a snapshot for a standard view is **legitimately refused** with `pending` + `retry_after_seconds: 1` after blocking ~0.9 s. The refusal is correct — serving the stale entry is the regression the cursor freshness floor exists to prevent. **A `/readyz` wired to the event would report ready at the exact instant the daemon cannot serve.**

Note also that `status.queue.pending` is `int(pending)` over a **boolean** (`service.py`, `pending = materializer_pending or bool(self._pending_ring_dirty)`), so it reads `1` whether one cell is staged or thousands. The real count is `ring_writer.pending_cells` (`service.py:4908`), measured at **1,248** at the readiness instant — which is exactly the `aggregate_ring_slots` row count, i.e. the entire ring. **`/readyz` must read `pending_cells`, never `queue.pending`.**

### Conditions — all must hold, or `/readyz` fails

1. `cache_generation > 0` — a generation is published. (Positive, not merely non-null: the projector reports literal `0` when `self._cache is None`, and the daemon answers RPC with that zero for ~24 s before its first build.)
2. **`ring_writer.pending_cells == 0`** — the ring for the served window is established, not staged. This is the condition the event omits and the whole reason `/readyz` is not a rename of it.
3. `ring_writer.failure` is falsy, and `build.failed` has not increased since the last successful publication.
4. `materializer.state` is not `"failed"`.
5. `migration.state == "ready"`.
6. **`anon_bytes + swap_bytes <= memory_budget_bytes`** — the §3 quantity against a configured ceiling.
7. `open_fds <= fd_budget` — 16 of 64 today, so a budget of 48 leaves headroom without hiding a leak.
8. Recovery state clean: no owed startup slots outstanding.

### While unhealthy

- HTTP **503**, `Retry-After: 1` — matching the `retry_after_seconds: 1` the snapshot path already advertises, so a client sees one retry cadence rather than two.
- Body names **every** failing condition, not the first, with the measured value beside its budget. A `/readyz` that reports one cause per poll costs an operator one restart cycle per cause.
- **Fail closed on error.** An exception reading any input is a **fail**, never a pass. Unknown state is not ready.
- **No transition to ready is inferred.** Each call re-reads.

### Cost

Conditions 1–5 and 8 are §1b (in-process, GIL-bound). 6–7 are §1a (external, free). Worst case is one GIL acquisition, i.e. up to ~0.9 s while a build holds it. **That is acceptable for `/readyz`** — a caller asking "can you serve" during a build genuinely cannot be served, so a slow answer and a negative answer carry the same operational meaning. It is **not** acceptable for `/livez`, which is why §5 uses nothing from §1b.

## 5. `/livez` — narrow progress check

`/livez` answers "is this process capable of making progress", and nothing else. It must not fail merely because the daemon is busy, and must not pass when the daemon is wedged.

**Computed entirely from §1a, by the caller, from `/proc`.** It never enters statsd, never takes the GIL, and therefore **cannot be blocked by the condition it is trying to detect**. A `/livez` that hangs when the daemon hangs reports nothing.

### What "progress" means concretely

Three externally readable signals, sampled against the previous call:

- **`cpu_ticks`** = `utime + stime` from `/proc/<pid>/stat`. Advancing CPU proves the process is executing Python, which distinguishes "busy in a 25–56 s cold build" from "wedged".
- **`state`** = field 3 of `/proc/<pid>/stat`. `R`/`S`/`D` are alive; `Z` and `T` are not.
- **`voluntary_ctxt_switches`** from `/proc/<pid>/status`. Advancing switches prove the process is still entering and leaving waits — a deadlocked-on-a-mutex thread stops advancing this while CPU is also flat.

### Verdict

**PASS** when the process exists, `state` is `R`/`S`/`D`, and **either** `cpu_ticks` advanced since the previous sample **or** context switches advanced.

**FAIL** when the process is `Z`/`T`/absent, or when **CPU and context switches have both been flat for `LIVEZ_STALL_SECONDS` while `/proc/<pid>/io` also shows no I/O**. That triple-flat state is the wedge signature: a process that is neither computing, nor waiting-and-waking, nor moving bytes, is not making progress by any definition.

### Why it cannot pass while wedged

A wedged process burns no CPU (a deadlocked mutex is an uninterruptible sleep, not a spin), performs no I/O, and stops switching. All three signals go flat together. There is no wedge that leaves any of them advancing, because advancing any of them requires the thing that is wedged.

### Why it cannot fail while merely busy

The pathological case is a legitimate long build — measured at **25–30 s** at 1× cardinality and **52–56 s** at 2×. Throughout, the worker burns CPU continuously (measured: 800–940 ms of solid worker CPU in a single burst). CPU advances, so `/livez` passes. **`LIVEZ_STALL_SECONDS` must nonetheless exceed the longest legitimate flat period**; an idle daemon with no work is flat by definition. Recommend **`LIVEZ_STALL_SECONDS = 120`**, i.e. roughly 2× the measured 2× cold-build time, and gate the check on there being outstanding work (`pending_cells > 0` from a *cached* prior `/readyz`, never a fresh in-process read) so a legitimately idle daemon is never called dead.

## 6. Where it lives, and the one thing I need in `service.py`

**`yolomux_lib/stats_current/http.py`** (333 lines, free). It already owns `StatsHttpForwarder` and the pending/unavailable wire shapes, so the projection, both verdict functions, and the 503 body belong beside them. §1a needs no daemon cooperation, so most of `/readyz` and **all** of `/livez` can be computed in `http.py` from the pid and the database path.

**What I need in `service.py` — do not let me edit it, sequence it:**

1. **One entry in `CONTROL_FIELDS`** (`service.py:124–136`): `"resource_state": FENCE_FIELDS`. The router at `service.py:138` derives `_handle_resource_state` automatically from the action name; no routing change.
2. **One handler**, `_handle_resource_state`, roughly ten lines, that returns the §1b fields **without taking `work_lock` or `cache_lock`** — plain attribute reads plus `len()` on the two pending sets. It must not call `_status()`. This is the entire ask, and its correctness property is "acquires no lock", which is reviewable by reading the ten lines.
3. **Nothing else.** No new thread, no new socket, no change to the readiness ordering. Whether `cache_ready_event` should move is a different item; this one only stops `/readyz` from *believing* it.

**TEMP size (§1a) needs a decision I could not resolve from source.** SQLite temp files land in `SQLITE_TMPDIR`, then `TMPDIR`, then `/tmp`, and are unlinked immediately after creation, so they have no stat-able path. Two options for the implementer: report `PRAGMA temp_store` and the resolved temp directory's free space (cheap, no connection needed for the directory part), or drop the field. **I recommend reporting the resolved directory and its free bytes and naming the field `temp_dir_free_bytes`**, because the operationally interesting failure is "the temp filesystem filled", not "the temp file is N bytes".

## 7. The reconciliation tolerance, stated twice

The item asks to reconcile internal retained-owner accounting to process USS "within a documented tolerance". **Today that tolerance cannot be tight, and the honest projection says why rather than hiding it.**

### Today

Measured by a sibling lane: process USS **1,470 MiB** reconciles to **52.96 MiB** of retained owners. The residual is **96.40%**, and its cause is named: **1,411 empty pymalloc arenas holding 49.8 MiB of live blocks**, an arena-to-live ratio of **17.10**.

- Ratio: `1470 / 52.96` = **27.76×**
- Retained share: `52.96 / 1470` = **3.60%**
- Residual: **96.40%**

**A "tolerance" of 96.4% is not a tolerance, it is a confession.** So the field must not be phrased as one. **Report the ratio, the residual, and the named cause**, and set the assertion as a *ratio ceiling*: `uss_bytes <= 30 × retained_owner_bytes` — 8% above today's 27.76×, so it fires on a real regression and does not pretend to be a memory budget.

**This also settles what the item means by "rather than reporting only encoded wire-cache bytes".** The existing `cache.shared_bytes + cache.private_bytes` is a *subset* of the 52.96 MiB, which is itself 3.60% of USS. Reporting wire-cache bytes alone would describe under 4% of the process and imply it was the whole story.

### After chunked folding

A separate lane measured that chunked folding takes the arena-to-live ratio from **17.10 to 1.32**, a factor of **12.95**.

Applying that factor to the arena-attributable residue, and holding retained owners constant:

- residue today: `1470 − 52.96` = **1,417.04 MiB**
- residue after: `1417.04 / 12.95` = **109.4 MiB**
- USS after: `52.96 + 109.4` = **162.4 MiB**
- ratio after: `162.4 / 52.96` = **3.07×**; residual **67.4%**

**Target: `uss_bytes <= 3.5 × retained_owner_bytes`**, ~14% above the projection.

**The assumption, stated because it carries the number:** that the residue scales with the arena ratio and retained owners are unchanged by chunking. If chunking also changes what is retained, the projection moves and must be re-derived from a direct measurement after it lands. **Do not adopt the 3.5× ceiling on the strength of this arithmetic alone** — adopt it after one measurement on the post-chunking build.

### Cost, and where it is not

Retained-owner accounting walks the daemon's live objects and needs `cache_lock`; USS needs the 20.8 ms `smaps_rollup`. **Neither belongs on `/livez` or `/readyz`.** Both belong on a separate on-demand endpoint, rate-limited to at most one sample per minute, documented as perturbing, and never polled by a supervisor.

## 8. Fields the item names that I could not specify side-effect-free

Per the brief's instruction to say so and propose a replacement rather than specify something that stalls the daemon:

| asked for | problem | replacement |
|---|---|---|
| peak and steady **PSS/USS** on the projection | only source is `smaps_rollup`, 20,794 µs, takes target `mmap_read_lock` | `RssAnon`/`RssFile`/`VmSwap`/`VmHWM` at 16.4 µs; **exact** for anon and swap, 2.06% high whole-process. True PSS/USS moves to the §7 on-demand endpoint. |
| **anonymous versus file PSS** | same source | `RssAnon` / `RssFile`. Anon is exact (0.000% divergence); file over-counts by a near-constant 12,954 kB of shared libc/libpython, which must be **labelled** rather than silently reported as PSS. |
| **TEMP size** | SQLite unlinks temp files immediately; no stat-able path | `temp_dir_free_bytes` on the resolved temp directory — §6. |
| retained-owner reconciliation | needs an object walk and `cache_lock` | on-demand, rate-limited, off every health path — §7. |

## 9. Open items

- **The `_status()` stall is inferred, not directly timed.** Two measured facts support it — `_status()` opens with `with self.work_lock:` at `service.py:4736`, and the worker was measured holding CPU for 800–940 ms — but I did not time a `status` RPC against a building daemon. One command settles it, after the disk freeze: run `tools/statsd_readiness_probe.py sweep` with the probe action changed from `snapshot` to `status` and compare the first-second distribution. I did not run it because it needs a full-size store.
- **`append_persistence` is absent from this worktree.** If a sibling lane lands it, §1b gains fields at no cost; the §1a `stat()` reads already cover the sizes.
- **The 3.5× post-chunking ceiling is projected arithmetic**, not measurement — §7 says so and says what to do instead.
- **`LIVEZ_STALL_SECONDS = 120` is derived from cold-build times measured at 1× and 2× cardinality on this host.** A materially larger store pushes cold build past it; the constant should be expressed as a multiple of the measured `build.last_full_seconds` rather than a literal.
