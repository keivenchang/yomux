# DOIT: pausable daemon subsystems

**SUPERSEDED 2026-07-29 for the open remainder.** 47 of 58 boxes are done and shipped; Phases A, 0, 1, 2, 3, 4, 5, B and C are complete. The remaining Phase D work is an architectural refactor, not a safety fix -- the outage risk it was chasing is already closed by Phase C (`ee51ca52`). Active work continues in **`DOIT.land-and-close.md`**. Keep this file as the record of what was built and why; do not work the open boxes from here.

Goal: let a deployment run only the subsystems it needs. In production that means switching off services the user does not use (Finder, Tabber, auto-approval, ...) to cut idle CPU. In tests it means a worker cohort starts the essential chain instead of all fifteen subsystems.

**The model, as specified by the user: EVERY service launches paused and waits for a signal to start.** This is not a mode or an opt-in; it is the universal startup state. Nothing is running until something asks for it.

Activation intent comes from exactly two sources, with test override taking precedence:

| context | what activates a subsystem |
| --- | --- |
| user / production | Preferences: the daemon reconciles the enabled set at startup and on change |
| tests | an explicit override naming the subsystems to start; Preferences are ignored |

Consequences that must hold:

- There is **one activation path**, not a "start everything then pause some" path and a separate lazy path. Starting from running is the thing being removed.
- A test cohort starts the three subsystems it needs, not fifteen. That is the whole point for the gate.
- The test override must be **explicit and total**: a test must never silently inherit the developer's Preferences from the host, or the suite becomes environment-dependent and its failures unreproducible. This is the same class of defect as a test fixture standing in for a specification.
- Startup reconciliation must respect the dependency graph: activating a feature activates what it consumes, in order.
- Status and diagnostics must enumerate **all** subsystems with their state, so `paused` is visible rather than inferred from missing data.
- For users the CPU saving comes from Preferences being off, not from laziness -- an enabled subsystem is activated at boot, so first use is not slow. Decide and record whether any subsystem is instead demand-activated, and why.

**The feature-to-dependency map is a hard prerequisite, not a rule to remember.** Nothing may be pausable until the graph is declared and enforced in code, because a pause that silently starves a consumer is worse than no pause at all. Build the graph first even though it delivers nothing visible on its own.

Relevant existing machinery to reuse rather than reinvent: the metrics lifecycle already has demand-driven activation (`demand.active`, `publisher_connected`, `metrics_demand_ttl_seconds` in the storaged deployment identity). That is launch-paused-and-activate-on-demand for one subsystem. Read it before designing a second mechanism.

## Measured evidence this rests on

Baseline load on this 24-core host with no test running is **~5.4**. Source, from `ps -eo pcpu,pid,etimes,args --sort=-pcpu` on 2026-07-29:

| %CPU (lifetime avg) | elapsed | process |
| --- | --- | --- |
| 63.8 | 24.8 h | `yolomux_lib.daemon.process --serve` (`~/.local/state/...`) |
| 30.9 | 12.3 h | `yolomux_lib.daemon.process --serve` |
| 21.7 | 24.8 h | `yolomux.py --port 7770` |
| 20.6 | 12.8 h | `yolomux_lib.daemon.process --serve` |
| 16.1 | 18.6 h | `yolomux_lib.daemon.process` (`/tmp/yolomux-daemon-...`) |
| 9.0 | 24.8 h | `yolomux_lib.storaged_process --serve` |

Four long-lived daemons plus storaged plus the web server average over 160% CPU combined, roughly 1.6 cores burned continuously. On 24 cores that consumes a third of the headroom to the documented starvation knee (`docs/DEVELOPMENT.md:58`, load 14-21) before pytest starts.

Per-lane measurement, each lane run alone, 2026-07-29 (full table in `/tmp/lane-measurements.tsv`): the browser lanes are the *gentlest* on CPU (`pytest-browser` peak load 7.24, 24.1% CPU; `pytest-e2e` peak 7.88, 27.3%). The spikes are `pytest` (peak 13.44) and `pytest-socket` (peak 12.37, 46.5% CPU). Chromium is not the load source; the per-worker cohort is. Every xdist worker spawns a daemon, a storaged and up to three webservers, and that daemon starts every subsystem whether the test needs it or not.

## Subsystem inventory

**CORRECTED 2026-07-29.** My first pass counted fifteen by grepping reference density, which is code-shaped and wrong in both directions. The authoritative set is **eleven daemon-owned subsystems**, and `docs/DEVELOPMENT.md:149` and the string literals defined in `yolomux_lib/` agree exactly:

| daemon-owned (11) | |
| --- | --- |
| `daemon.metrics.host` | `daemon.fs.watch` |
| `daemon.metrics.services` | `daemon.fs.read` |
| `daemon.metrics.aggregator` | `daemon.fs.index` |
| `daemon.tmux.status` | `daemon.fs.transcript` |
| `daemon.tmux.approval` | `daemon.fs.git` |
| | `daemon.fs.session_metadata` |

What my first pass got wrong, and both errors matter for the Preferences UI:

- **Wrongly included** `tmux.runtime`, `fs.search`, `fs.routing`, `metrics.sources`, `metrics.lifecycle`. These are modules and internals, not registered owners. `daemon.fs`, `daemon.metrics` and `daemon.tmux` are domain prefixes; `daemon.kernel`, `daemon.products`, `daemon.subsystems` are module names; `daemon.sock` is a socket filename.
- **Wrongly omitted** `daemon.metrics.services`, which is canonical.

Storaged owns **eight authoritative storage namespaces** -- all RAM, SQLite, generations, subscriptions and durability:

`storaged.stats`, `storaged.search`, `storaged.search_index`, `storaged.products`, `storaged.finder`, `storaged.chat`, `storaged.auth`, `storaged.pricing`

Two traps the doc calls out explicitly: `storaged.status` and `storaged.approval` were **planned but never built** and must not be cited as owners. Tmux status and approval are **daemon**-owned, and their completed bytes are retained in `storaged.products` under the shared product contract rather than in dedicated namespaces. (`storaged.shared_state` and `storaged.sock` are an internal and a socket filename, not namespaces.)

**Phase A's graph and Phase 3's Preferences UI must use these eleven names.** A toggle should name the architectural owner or its user-facing feature, never an internal module path.

No enable/disable knob exists for any of them today. Confirmed by grep of `yolomux_lib/daemon/registry.py` and `yolomux_lib/local_services/registry.py`.

## Rules this feature must obey

- [x] **Paused must never look like empty.** A paused subsystem returns a typed `paused` reason the client can render. If `metrics.host` is paused the UI says "paused", never 0% CPU. An absent value rendered as zero is the same defect class as a swallowed exception: a confident wrong answer. Reuse the existing `unavailable_spans` machinery and write a span with reason `paused` -- E1 acceptance already requires every unavailable interval to carry a reason, so this fits rather than adding a parallel concept. DONE 2026-07-29: the registry's typed paused payload and paused unavailable span are covered by `tests/test_daemon_subsystems.py`.
- [x] **Declare the dependency graph before allowing any pause.** `metrics.aggregator` consumes `metrics.sources`; `fs.transcript` feeds agent attribution. Pausing a producer under a live consumer must cascade or be refused, never silently starve the consumer. Without a declared graph this feature generates bugs faster than it saves CPU. DONE 2026-07-29: `SubsystemRegistry` enforces `activation_order()` and rejects a pause that would starve an active consumer.
- [x] **Auto-approval fails closed.** Pausing `tmux.approval` blocks approvals. It must never auto-grant. This is a safety decision, not a convenience toggle. DONE 2026-07-29: paused approval replies are typed and denied in the registry tests.
- [x] **One owner for the policy.** A single registry maps subsystem to state, consulted at scheduling time. Do not scatter `if enabled` through fifteen modules. DONE 2026-07-29: `SubsystemRegistry` is the sole lifecycle owner and scheduler admission consults it before handlers start.
- [x] **Keep one full-chain lane in the default gate.** If each test picks its own subsystems, a test green on a minimal set can break on the full chain. A test fixture is not a specification. DONE 2026-07-29: config-less `DaemonProcessSupervisor` uses the explicit eleven-owner `TEST_FULL_CHAIN_PROFILE`; focused process coverage passed 63 tests in 78.01s.

## Phase A -- the feature-to-dependency graph (blocks Phase 2; do this first)

- [x] Declare, in one place, what each of the eleven daemon-owned subsystems produces and what it consumes. Derived from `compose_daemon_runtime`, `compose_daemon_metrics`, `DaemonTmuxModule`, `FilesystemDomain`, and `PRODUCT_ROUTES`; source data is `yolomux_lib/daemon/subsystems.py`. RECONCILED 2026-07-29: `tmux.runtime`, `fs.routing`, `fs.search`, `metrics.sources`, and `metrics.lifecycle` are implementation details beneath the canonical owners, not registry peers; `daemon.metrics.services` is restored as a canonical owner.
- [x] Express it as data the scheduler can enforce, not as a comment. `activation_order()` returns dependency-first startup order and `starved_consumers()` names every active direct or transitive consumer a pause would starve.
- [x] Map subsystems to the user-facing features they back (Finder, Tabber, auto-approval, YO!metrics, transcript/cost, search, ...), so a Preferences toggle can name a feature rather than a module path. The user asked for this explicitly: knowing feature -> dependency is a must.
- [x] Record the graph in this file. It is the artifact the rest of the work depends on.
- [x] Test: every declared dependency is exercised, and a pause that would starve a live consumer is refused or cascades. `tests/test_daemon_subsystems.py` validates every declared edge and the transitive `fs.transcript` consumer set.

| subsystem | consumes | feature(s) | produces |
| --- | --- | --- | --- |
| `daemon.metrics.host` | — | YO!metrics | native CPU, GPU, memory, and network facts |
| `daemon.metrics.services` | — | YO!metrics | registered-service CPU and RSS facts |
| `daemon.metrics.aggregator` | `daemon.metrics.host`, `daemon.metrics.services`, `daemon.tmux.status`, `daemon.fs.transcript` | YO!metrics; Transcripts & cost | cadenced metric publication plus range and cost materializations |
| `daemon.tmux.status` | — | Terminal & Tabber; Session metadata; YO!metrics | tmux inventory and status products |
| `daemon.tmux.approval` | — | Auto-approval | fail-closed approval decisions |
| `daemon.fs.watch` | — | Finder; Search | filesystem-change generations |
| `daemon.fs.read` | — | Finder | Finder and exact-file reads |
| `daemon.fs.index` | `daemon.fs.watch` | Search; Session metadata | indexed repository and Quick Open snapshots |
| `daemon.fs.transcript` | — | Transcripts & cost; Session metadata; YO!metrics | transcript products and agent usage facts |
| `daemon.fs.git` | — | Git & pull requests; Session metadata | repository facts and watched pull requests |
| `daemon.fs.session_metadata` | `daemon.tmux.status`, `daemon.fs.transcript`, `daemon.fs.git`, `daemon.fs.index` | Session metadata; Terminal & Tabber | joined session metadata products |

## Phase 0 -- measure before designing

- [x] Per-subsystem CPU cost on an otherwise idle daemon, over a representative window. Produce a table: subsystem, CPU seconds, wakeups per minute, share of daemon total. DONE 2026-07-29: the 49.30-second read-only live-daemon sample below attributes the directly-owned threads and records OS context switches as an explicitly labelled scheduling proxy.
- [x] Answer the specific question: what accounts for the **63.8% lifetime average** on the largest daemon? It may be one subsystem polling hot rather than fifteen each costing a little. If so, fixing that beats pausing fourteen. DONE 2026-07-29: it was the bounded scheduler result cache filling, not a continuing subsystem cost; `94383799` makes it plateau at the 1,024-record ceiling. The active CPU sample is dominated by tmux status, filesystem notification handling, and the shared metrics/scheduler pump.
- [x] Record the answer here before starting Phase 2. The essentials split must come from this table, not from intuition. DONE 2026-07-29: Phase 2 may proceed; no daemon restart or further instrumentation is warranted.

PARTIAL 2026-07-29: a clean 180-second private-container full-domain daemon profile is at `/tmp/yolomux-daemon-pause-phase0-long-20260729-114325.raw`; it used a private tmux server, an explicit empty serialized agent roster, no mounted user state, and emitted no service errors. It captured 297 active stack samples: 39 in `daemon-scheduler-pump`, 20 in `daemon-tmux-status-refresh`, and 158 waiting in the mux listener. The active paths concentrate in `metrics.lifecycle -> metrics.sources.collect -> tmux.status._snapshot` and tmux status-refresh discovery, not fifteen equal loops; `fs` had no active sample. This is directional only: `py-spy` active samples cannot supply per-subsystem CPU seconds or wakeups/minute, and the 63.8% long-lived production daemon remains unattributed. The two earlier 60-second profiles are discarded because they exercised missing-tmux/GPU/roster error paths; container attach-after-ready was denied by ptrace policy. Do not start Phase 2 from this evidence. The remaining Phase 0 work is a per-subsystem accounting counter plus an isolated representative workload that records CPU and wakeups from the child itself.

PARTIAL 2026-07-29 live-daemon measurement: read-only observation of production daemon pid `2591351` (never restarted or signalled) is at `/tmp/yolomux-daemon-phase0-live-samples-20260729-120247.jsonl`. Eight 25-second samples over 175 seconds rose from 1,318,812 to 1,360,296 KiB RSS: +41,484 KiB / 40.5 MiB, or 13.9 MiB/minute. The counter deltas in that same interval were CPU collector attempts +174 (59.7/min), agent-status +12 (4.1/min), agent-token +12 (4.1/min; failures +10), tmux-status builds +12 (4.1/min), filesystem-watch publish batches +568 (194.7/min), and product requests +470 (161.1/min). Mux connections remained 3, scheduler queue/failures stayed 0, and the scheduler completed counter saturated at 1,024 after +25; no observed counter tracks the bursty RSS increments (+23,672, +14,060, +40, +860, +1,736, +1,072, +44 KiB). The live leak is therefore confirmed but **UNATTRIBUTED**: this is not evidence to blame metrics, tmux, filesystem watch, products, or the scheduler. A nonblocking `py-spy dump` showed all named threads idle at its instant; a bounded `py-spy record --nonblocking --format raw --duration 90` attached but left no retained raw output, so it is not used as CPU attribution. Per-owner CPU seconds and wakeups/minute remain unavailable for every canonical owner; do not check any Phase 0 box or start Phase 2 from this evidence.

| canonical owner | CPU seconds | wakeups/minute | share of daemon total | current evidence |
| --- | --- | --- | --- | --- |
| `daemon.metrics.host` | unmeasured | unmeasured | unmeasured | CPU collector advanced 59.7/min; does not track RSS bursts |
| `daemon.metrics.services` | unmeasured | unmeasured | unmeasured | service-load remained empty; no attribution |
| `daemon.metrics.aggregator` | unmeasured | unmeasured | unmeasured | no attributable counter trend |
| `daemon.tmux.status` | unmeasured | unmeasured | unmeasured | builds advanced 4.1/min; does not track RSS bursts |
| `daemon.tmux.approval` | unmeasured | unmeasured | unmeasured | no active targets |
| `daemon.fs.watch` | unmeasured | unmeasured | unmeasured | batches advanced 194.7/min; does not track RSS bursts |
| `daemon.fs.read` | unmeasured | unmeasured | unmeasured | no owner-specific measurement |
| `daemon.fs.index` | unmeasured | unmeasured | unmeasured | no owner-specific measurement |
| `daemon.fs.transcript` | unmeasured | unmeasured | unmeasured | agent-token failures advanced 3.4/min; does not track RSS bursts |
| `daemon.fs.git` | unmeasured | unmeasured | unmeasured | no owner-specific measurement |
| `daemon.fs.session_metadata` | unmeasured | unmeasured | unmeasured | no owner-specific measurement |

**COMPLETE 2026-07-29 12:35 PT -- read-only per-owner accounting, live pid `2591351`.** `/tmp/yolomux-daemon-phase0-thread-accounting.ZS8ob5` pairs nonblocking `py-spy dump` thread-name snapshots with `/proc/<pid>/task/<tid>/{stat,status}` deltas over 49.297 seconds. The daemon used 4.590 CPU seconds. “Scheduling proxy/minute” is voluntary plus involuntary OS context switches, not a claim that every switch is one application wakeup; it is the only non-instrumenting per-thread rate available and must not be used as a literal wake count.

| canonical owner | CPU seconds | share of daemon CPU | scheduling proxy/minute | attribution |
| --- | ---: | ---: | ---: | --- |
| `daemon.metrics.host` | 0.000 observed | 0.0% | 0 | no callback CPU sampled outside the shared lifecycle pump |
| `daemon.metrics.services` | 0.000 observed | 0.0% | 0 | service-load was idle |
| `daemon.metrics.aggregator` | 0.920 | 20.0% | 8,984.8 | `daemon-scheduler-pump`, which owns metrics lifecycle publication and scheduler maintenance |
| `daemon.tmux.status` | 2.090 | 45.5% | 8,647.6 | `daemon-tmux-status-refresh` |
| `daemon.tmux.approval` | 0.000 observed | 0.0% | 0 | no active targets |
| `daemon.fs.watch` | 0.580 | 12.6% | 107,661.6 | `daemon-fs-watch` plus native notifier |
| `daemon.fs.read` | 0.000 observed | 0.0% | 0 | no bounded-read work in the window |
| `daemon.fs.index` | 0.000 observed | 0.0% | 0 | no index work in the window |
| `daemon.fs.transcript` | 0.000 observed | 0.0% | 0 | no transcript work in the window |
| `daemon.fs.git` | 0.000 observed | 0.0% | 0 | no Git work in the window |
| `daemon.fs.session_metadata` | 0.000 observed | 0.0% | 0 | no session-metadata work in the window |

The remaining 1.000 CPU seconds (21.8%) and 35,045.8 scheduling-proxy/minute were shared mux/client transport, not an undeclared subsystem. The evidence supports retaining `daemon.tmux.status`, `daemon.fs.watch`, and the metrics lifecycle in the essential full-chain profile; it does not support an invented per-owner CPU claim for inactive handlers.

**RESOLVED 2026-07-29 12:31 -- there is NO ongoing leak. The growth was a bounded cache filling to its ceiling.**

Ten RSS samples of live pid `2591351`, 20 s apart:

```
12:28:44  1341 MB    12:29:44  1343 MB    12:30:44  1343 MB
12:29:04  1342 MB    12:30:04  1343 MB    12:31:04  1343 MB
12:29:24  1342 MB    12:30:24  1343 MB    12:31:24  1343 MB
                                          12:31:44  1343 MB
```

+2 MB then flat. Against the earlier curve -- 105 MB at restart, ~60 MB/min for the first 20 minutes, 1158 MB at 18 min, 1340 MB at 40 min -- this is a filling curve reaching a ceiling, not linear growth. The live-sample note above measured 13.9 MiB/min at 12:02 and extrapolated it; by 12:28 it was flat. **Two points on a rising curve cannot distinguish a leak from a cache filling**, which is the recurring error in this project.

The decisive corroboration is already in the note above: *"the scheduler completed counter saturated at 1,024 after +25"*. That is `SCHEDULER_MAX_RECORDS` reached, eviction engaging, and RSS levelling off at the same time.

**Restating what is and is not true:**

- The real leak was `prune_records` having **zero callers**, so `self.records` grew forever. That is how the pre-fix daemon reached 48 GB over 27 h with no ceiling. Commit `94383799` fixed it.
- What remains is a **badly denominated cache**, not a defect in kind. `ScheduledWork` retains `request.payload` and `result.payload`; the window is capped at 1,024 *records* while memory is consumed in *bytes*, and per-record cost ranges from a few hundred bytes to 4 MB + 4 MB on the session-metadata route. Nobody chose 1.3 GB; someone chose 1,024.
- Retention itself is correct and must stay: `scheduler.py:344 snapshot(ticket)` is how callers collect results after submission, so terminal records are a deliberate result window.

**MOVED 2026-07-29 -> `DOIT.scheduler-payload-ttl.md`.** The two remediation items that were here (byte-denominated window, typed evicted reason) now live in that queue, alongside the per-product TTL design and the measured evidence. Superseded in one respect: the byte budget is **out of scope** there, because with every product defaulting to `ttl = 0` the payloads are released on delivery and there is no window left to denominate. One backlog per concern; do not work these from this file.

**Priority: housekeeping, not P0.** No production restart or instrumentation is required. The genuine CPU win found in the same investigation was storaged's unbounded browser `OR` clause, fixed in `7c887edf`: 429 ms -> 48 ms per rebuild, ~40% of a core recovered.

## Phase 1 -- make the daemon set visible (small, independent, do first)

- [x] Set the process identity on daemon and storaged startup from the identity that **already exists**: `yolomux_lib/infra/environment.py:24-26` defines `YOLOMUX_DEPLOYMENT_ENV` (valid: production, development, debug, qa, test), `YOLOMUX_BACKEND_COHORT`, `YOLOMUX_NAMESPACE`. DONE: `55a2d601` preserves the real interpreter in `argv[0]` and carries the operator-visible identity as an inert `--identity` argument. This avoids breaking restart wrappers that call `os.execvp(command[0], ...)`.
- [x] Target: `yolomux-daemon [production/stable/default]` vs `yolomux-daemon [test/dev7772/ns-abc]`. DONE: `tests/test_storaged_process.py` reads the live child `/proc/<pid>/cmdline`, asserts `argv[0] == sys.executable`, and verifies the identity argument. The three deterministic restart-topology regressions passed in `/tmp/yolomux-proctitle-topology-isolation-20260729-111937.log` (3 passed, 12.25s).
- [x] Read the identity from the existing `EnvironmentNamespace`. Do not invent a second source. There is no `setproctitle` call anywhere in the tree today -- if it is not already a dependency, use an alternative and say which and why. DONE: `setproctitle` is absent and no dependency was added; `EnvironmentNamespace.process_title()` supplies the inert identity argument. `pytest-unit` and `node-layout` passed, and the full gate passed 7/8 lanes with child-not-ready=0; its one moving-param E2E failure is load-flaky in repeated isolation, not attributed to Phase 1.

## Phase 2 -- pausable subsystem registry

- [x] One registry owning subsystem name, declared dependencies (from Phase A), current state (`running` / `paused`), and the reason surfaced when paused. DONE 2026-07-29: `SubsystemRegistry` is the sole state/graph owner; `STATUS` enumerates all eleven owners and each mux gate uses its typed paused payload.
- [x] **Every subsystem launches paused.** There is no code path that starts one implicitly. Removing "starts running by default" is the core of this phase. DONE 2026-07-29: process composition constructs the registry paused, then the listener-owned pump reconciles an explicit intent.
- [x] One activation entry point, idempotent and race-safe: two consumers asking at once start it once. DONE 2026-07-29: the registry serializes dependency-first hooks under its lifecycle lock.
- [x] Startup reconciliation reads the activation intent (Preferences in production, override in tests) and activates the resulting set in dependency order. DONE 2026-07-29: composition reads `daemon.subsystems`; the bounded settings-signature callback reconciles changes without a restart.
- [x] The test override is explicit and total: naming a set means exactly that set plus its declared dependencies, with Preferences ignored entirely. A test must not be able to pass or fail because of a developer's Preferences. DONE 2026-07-29: the serialized `--subsystem-override-json` child contract invokes `reconcile_test_override`.
- [x] Status/diagnostics enumerate every subsystem with its state, so `paused` is visible rather than inferred from absent data. DONE 2026-07-29: daemon `STATUS` includes name, state and reason for every canonical owner.
- [x] Scheduler consults the registry at scheduling time. DONE 2026-07-29: `DaemonScheduler.set_task_admission()` rejects a paused registered owner before its handler starts.
- [x] Pause writes an unavailable span with reason `paused` for any family that publishes coverage. DONE 2026-07-29: disabling `daemon.metrics.aggregator` appends typed `paused` spans through its existing publisher before stopping the lifecycle.
- [x] Pause is reversible without a restart if that is cheap; if it requires a restart, the UI says so rather than appearing to take effect. DONE 2026-07-29: Preferences reconciliation uses the same activate/pause hooks in either direction.
- [x] Regression tests: paused subsystem reports `paused` and not empty; pausing a producer with a live consumer cascades or refuses; paused `tmux.approval` denies. DONE 2026-07-29: `tests/test_daemon_subsystems.py` covers typed paused metrics/files/approval responses, dependency refusal, total override, and scheduler-time rejection; 72 focused daemon/domain/process tests passed in 80.27s.

## Phase B -- paused feedback contract, API through to UI

When the frontend hits a paused service the user must be told what is paused and where to enable it. Reuse what exists; do not invent a parallel mechanism.

**What already exists and must be the shared parent:**

- Dedupe: `yolomux_lib/server_logs.py:25-50` -- `emit(level, source, message, *, category, dedupe_key, dedupe_seconds)`, a TTL map with capacity-bounded cleanup. This is the anti-spam mechanism; do not write a second one.
- One toast entry point: `static/yolomux.js:12692 function showToast`, with specialised wrappers (`showTerminalConnectionToast`, `showSelfUpdateReloadDeferredToast`) built on it. Paused notices go through the same parent, so they look and behave like every other message.
- Typed unavailable responses already exist in several services (`stats_current/http.py:55`, `login_rate_limit.py:951`).

**The contract:**

- [x] A paused service returns a **typed** response: `paused` is its own state, distinct from `unavailable` and from `error`. A caller must never have to guess whether the thing is off, broken, or slow. Do not return 404, an empty body, or a generic 500. DONE 2026-07-29: `paused` is admitted to the shared mux status vocabulary and daemon gates return it with `ok: false`.
- [x] The response carries what the UI needs to render the message without hardcoding anything: the subsystem, the **user-facing feature** it backs (from the Phase A map), and where to enable it in Preferences. The backend owns that mapping; the frontend must not keep its own copy that can drift. DONE 2026-07-29: `paused_response_payload()` derives all three fields from `SubsystemSpec`.
- [x] **One frontend handler** recognises the paused state from any endpoint and raises the toast. Do not scatter per-feature handling across call sites -- that is the divergent-copy failure this codebase keeps paying for. DONE 2026-07-29: `apiHandlePausedServicePayload()` is the shared parent; generic JSON and the isolated stats transport both forward to it.
- [x] Message shape: "<Feature> is paused. Enable it in Preferences > <section>." Wording owned in one place. DONE 2026-07-29: the shared handler renders that one template.

**No spamming:**

- [x] Dedupe on the **service**, not the request. A hundred polls against a paused `fs.index` produce one message, not a hundred. Use `dedupe_key` keyed by subsystem plus client, with a TTL long enough to survive a polling loop. DONE 2026-07-29: every daemon paused reply records through `record_paused_service_request()` and `server_logs.emit()` with its existing 300-second subsystem/client key.
- [x] Coalesce across services: several paused services at once produce one summarised notice, not one toast each. `dismissCoalescedToast` already exists in the frontend -- check whether the coalescing path can be reused before adding another. DONE 2026-07-29: `paused-services` uses the existing coalesced-toast route and replaces the one active notice with its aggregated lines.
- [x] Re-notify only on a state change or after the TTL, never on every failed call. DONE 2026-07-29: the bounded frontend service map suppresses matching notices for five minutes; a changed service detail re-emits through the same coalesced key.
- [x] Test it: assert that N requests to a paused service produce exactly one notice, and that a second paused service does not produce a second independent toast. DONE 2026-07-29: `tests/stats_current_panel.test.js` covers repeat suppression and coalescing; the daemon runtime test covers the shared server-log call.

## Phase 3 -- Preferences UI

- [x] Per-subsystem toggle, persisted through the normal Preferences path. No parallel config file. DONE 2026-07-29: Services renders the backend catalog's canonical rows and writes `daemon.subsystems` through `saveSettingsPatch`.
- [x] Show each subsystem's measured cost from Phase 0 next to its toggle, so the choice is informed. DONE 2026-07-29: catalog metadata carries the read-only 49.3-second CPU sample for each owner.
- [x] Show paused state distinctly everywhere that subsystem's data appears. DONE 2026-07-29: disabled services are unchecked in Preferences; every data endpoint independently carries the typed paused state into the shared notice rather than rendering missing data as a value.

## Phase 4 -- production essentials

- [x] Define the essential set from the Phase 0 table and default the rest off in `production`. DONE 2026-07-29: `PRODUCTION_ESSENTIAL_SUBSYSTEMS` is the dependency-closed chain for metrics aggregation plus tmux status and filesystem watch; Preferences defaults to exactly that six-owner set.
- [x] State the expected idle-CPU saving with the numbers behind it. DONE 2026-07-29: the 49.297-second profile measured 0.920 CPU s metrics, 2.090 tmux status, 0.580 filesystem watch and 1.000 shared transport (4.590 total); all five default-off owners measured 0.000 CPU s in that window, so the defensible predicted saving from this default alone is 0.000 CPU s / 49.297 s, not an invented percentage.

## Phase 5 -- test profiles

- [x] Let a test cohort start only the subsystems it needs, so a worker does not pay for all eleven. DONE 2026-07-29: `subsystem_test_profile()` derives a dependency-closed total override from daemon domains, and `BackendCohortHarness` supplies it explicitly; `fs-git` maintenance coverage proves a test-local Preferences file cannot change the selected profile.
- [x] Measure the effect on per-lane peak load and on gate failure rate over at least 10 gates. DONE 2026-07-29: ten valid host-locked full gates are in `/tmp/yolomux-phase5-measurement-final-20260729-150114/`; queue time was excluded from gate duration and pre-start samples were excluded from load measurements. “Sustained” below is the mean 1-minute load average across post-start 15-second samples. The pass rate was 4/10 (40%; denominator: 10 valid full gates), versus an informal roughly-3-green-in-9 tally from ad-hoc gates, not a controlled ten-run baseline. This is therefore 40% measured against an unmeasured baseline, not a clean 33->44 improvement or a strong directional comparison. Starting fewer subsystems per cohort did NOT stabilise the gate. Phase 5 did make the gate more correct: it exposed and fixed four deterministic profile failures, closed profiles over their consumers, and added a closure regression test. It did not make it more stable.

| run | gate seconds | verdict | peak load1 | sustained load1 |
| --- | ---: | --- | ---: | ---: |
| 1 | 292.81 | failed | 29.83 | 19.46 |
| 2 | 298.09 | passed | 25.80 | 17.19 |
| 3 | 309.15 | passed | 27.88 | 18.80 |
| 4 | 293.49 | failed | 24.95 | 17.92 |
| 5 | 294.18 | passed | 28.50 | 18.20 |
| 6 | 293.03 | failed | 22.94 | 16.38 |
| 7 | 285.52 | failed | 26.14 | 18.23 |
| 8 | 302.65 | failed | 21.99 | 16.63 |
| 9 | 293.27 | passed | 26.00 | 17.61 |
| 10 | 298.15 | failed | 30.45 | 18.81 |

Gate-only aggregate: 296.03-second mean duration; peak load1 30.45; sustained load1 17.92 across 202 post-start samples. The six red runs recorded their exact node IDs in the adjacent `nodeids-*.txt` artifacts; 135 `child failed to become ready` messages occurred across the ten logs.
- [x] Keep at least one full-chain lane in the default gate. DONE 2026-07-29: a config-less `DaemonProcessSupervisor` selects `TEST_FULL_CHAIN_PROFILE`, while shared cohorts select only their named domains.

## Phase C -- boot.sh must reap a stale-identity backend (P0, blocks every upgrade)

Upgrading the live 7770 deployment on 2026-07-29 took the server down for ~2 minutes and needed a manual `SIGKILL`. This is reproducible and will happen on **every** future upgrade.

**What happened.** `boot.sh` stopped the web server. Its daemon and storaged survived as `PPID 1` orphans, because `runtime.py:262` spawns them with `start_new_session: True` -- deliberately, so a shared backend outlives any one server and the next server adopts it (`cli.py:620 start_or_join_shared_backend_processes`). The new server then refused to adopt them and exited:

```
RuntimeError: daemon child unavailable or deployment mismatch     (cli.py:655)
```

**Why adoption is refused.** `cli.py:637` requires exact equality of the deployment identity dict. Measured old vs new for the daemon:

| component | old `e2d08c7a` | new `55a2d601` |
| --- | --- | --- |
| `service` / `protocol_minimum` / `domains` / `configuration_revision` / `environment_identity` | identical | identical |
| `source_revision` | `8cf4a2e674328123f2a7aa41` | `dc2759402668adfd2b61b20e` |
| `fingerprint` (derived) | `69898802e77481144985d016` | `25c9e2f434d05ecf0f39b6ba` |

`source_revision` is a SHA-256 over the **contents of every `.py` file** under `yolomux_lib/` (`deployment.py:18-27`), so any source change at all produces a mismatch -- 11 `.py` files changed here, and one would have sufficed. The refusal is correct: a new-code server must never adopt an old-code daemon. The defect is that nothing removes the stale one.

**Why no existing mechanism recovers it.**

- `boot.sh` preflight only resolves `port_listener_pids` -- the TCP listener. The daemon holds no TCP port and lives in its own session, so it is invisible: the run reported `"reaped_pids": [], "tracked_pids": []` while a 48 GB daemon sat on the socket.
- Idle exit (`runtime.py:602`) needs no connections **and** no leases **and** 60s since the last client. A lease outliving its holder pins it open forever.
- `supervisor.stop()` (`runtime.py:367`, which does `os.killpg(..., SIGTERM)`) is only reached from in-process paths (`cli.py:96`, `cli.py:690`). A server killed externally never runs it.

The orphaned daemon also **ignored SIGTERM** and required SIGKILL, consistent with the pump spinning through retained records rather than reaching a signal check.

- [x] `boot.sh` preflight resolves the deployment's daemon and storaged from their **socket paths** (`$YOLOMUX_STATE_DIR/services/*.sock`), not from the TCP listener. DONE 2026-07-29: `ee51ca52` reuses the persisted exact-socket registry groups; no new listener probe was added.
- [x] A guarded restart compares the running backend's deployment identity against the one the incoming server will use, and reaps the pair when they differ -- reporting exactly which component differed, not just "mismatch". DONE 2026-07-29: `--print-backend-deployments` supplies the incoming pair and `deployment_mismatches` reports public component-level running/incoming values, including `source_revision`.
- [x] Reap escalates: SIGTERM, then SIGKILL after a bounded wait. Record which was needed; a daemon that needs SIGKILL is itself a finding. DONE 2026-07-29: preflight retained the bounded TERM->KILL escalation; the targeted regression records both signals for a survivor.
- [x] Never reap an identity that **matches** -- that is the adoption path working as designed and it is the whole point of a shared backend. DONE 2026-07-29: matching STATUS deployment returns `reaped_pids: []` and the regression proves no signal is sent.
- [x] Test: start a backend, change the source revision, run a guarded restart, assert the stale pair is reaped and the new server binds. Assert the negative too: an identical identity is adopted and not killed. DONE 2026-07-29: `tests/test_local_services_watchdog.py` covers mismatch component evidence plus TERM/KILL and the exact-match no-signal adoption path; focused suite 35 passed. Full gate `/tmp/yolomux-phase-c-full-gate-20260729-162901.log` was 7/8 green; its only red daemon-metrics node passed 3/3 isolated (2.29s, 2.40s, 2.30s), consistent with the known load-flaky cluster.

## Phase D -- invert startup ownership: services first, healthchecked, then the server

The user's specification, and it supersedes the shape Phase C patches around:

1. Ensure the services are the right version -- stop old, start new.
2. Every service and daemon passes a healthcheck.
3. Only then start the web server, which **connects** to services it did not spawn.

**Why this is the real fix.** Today the web server owns the backend: `cli.py:831 main()` calls `start_or_join_shared_backend_processes()` inline during startup, and `runtime.py:262` spawns the children detached with `start_new_session: True`. Three consequences follow directly, and we hit all three on 2026-07-29:

- The server dying leaves the services orphaned at `PPID 1`, owned by nobody.
- A version change makes the surviving services un-adoptable, so the *new* server crashes at startup with `RuntimeError: daemon child unavailable or deployment mismatch` -- the process that could clean up is the one that cannot start.
- There is no point at which the backend is known-good *before* the server commits to it. `boot.sh` verifies backend readiness only **after** launching the server (`boot.sh:528,548`), so a bad backend is discovered by a failed web deployment.

Inverting it makes an upgrade deterministic: stop services, start the right version, prove them healthy, then start a server that is a pure client.

- [ ] Move backend lifecycle out of `cli.py main()`. Starting services becomes an explicit step the deployer performs, not a side effect of serving HTTP.
- [ ] The server becomes a **client**: if the services are missing or the wrong version it fails fast with the exact mismatching component named, and never tries to spawn its own.
- [ ] A real healthcheck gate between step 1 and step 3: each service reports its deployment identity plus readiness, and the deployer refuses to start the server until every one passes. Reuse the STATUS path `boot.sh:528-548` already calls, but run it **before** the server, not after.
- [ ] Preserve the shared-backend property: several servers may still share one backend, and a second server must adopt rather than restart it. Adoption on identity match stays; only the implicit *spawn* moves out.
- [ ] Preserve one-command dev ergonomics: `./boot.sh <port>` still does the whole sequence. The steps become ordered and individually observable, not manual.
- [ ] Decide and record who owns services across a reboot -- `boot.sh` on demand, or a supervisor. State the choice and why.
- [ ] Handle the concurrent-start race explicitly: two deployers starting services at once must converge on one backend, which is what today's join-then-spawn logic buys. Do not regress it.
- [ ] Test the upgrade path end to end: running backend at revision A, deploy revision B, assert old services stop, new services pass healthchecks, the server starts and serves, and **no orphan survives**. Assert the no-op case too: redeploying the same revision adopts and does not restart the backend.

Phase C remains worth doing first -- it is small and stops the immediate bleeding on the next upgrade. Phase D removes the class of defect.

## Out of scope

Do not change worker counts or lane overlap as part of this. That is separate gate-orchestration work and mixing them makes both unmeasurable.

## Open, unattributed

- [ ] Something rewrites `static/brand.css` during container boot / E1, adding a trailing space and removing the final newline, which fails the `git diff --check` lane. `python3 tools/static_build.py` was tested and leaves the file clean, so the generator is **not** the writer. Writer still unknown.
