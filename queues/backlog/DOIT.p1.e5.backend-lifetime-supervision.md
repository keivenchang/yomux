# DOIT.p1.e5.backend-lifetime-supervision.md - Bound Backend Lifetime And Simplify Root Ownership

Source provenance: Bugs 9, 9b, 9c, 9e and S4 from `EVIDENCE-ARCHIVE.md`, the launch-lifecycle rules from `REGRESSION-GATE.md`, `DOIT.p2.same-root-coordination-simplification.md`, and the rotating-daemon fixture evidence formerly retained in sibling worktrees.

## Goal

Every backend and sidecar has one explicit lifetime owner, exits within a numeric budget after its last valid claim disappears, and uses same-root coordination only for the deliberate caller-shared-root compatibility case.

## Why These Tasks Are One Queue

Backend retirement and same-root owner election use the same host/boot/process/generation identity, service records, locks, signals, unlink, reclaim, and adoption paths. Implementing them in separate queues would make two agents edit the same lifecycle owner and could leave private-root and caller-shared-root behavior inconsistent.

## Context

- Historical evidence found daemon/storaged processes reparented to PID 1 for 66 minutes because startup-only preflight was the sole reaper, self-connections prevented idle expiry, and the sidecar ledger was empty. Current topology must be re-inventoried before accepting old symbols or owners.
- A historical fixture failure reached `atomic_write_text(...); path.chmod(mode)` after its pytest temporary directory disappeared. That proves a useful lifetime boundary to reproduce, but not the retracted claim that an orphan child was the root cause.
- Managed instances derive private roots and need no cross-instance election or compatible-service reuse. A caller-set root may be deliberately shared, so removing all coordination would be unsafe.
- Exact host, boot, PID start identity, namespace, service kind, generation, and lifetime claim are the destructive-authority boundary. Ambiguity must leak visibly and fail closed rather than signal or unlink.

## Ownership And Execution Order

- One implementation owner controls the lifecycle/identity conflict group in local-service watchdogs, owner records, process ledgers, signal/unlink/reclaim paths, and same-root coordination. Do not assign concurrent writers to those files.
- Two read-only audits may run in parallel before implementation: one inventories every backend/sidecar lifetime and connection; the other inventories every background-election, compatible-service-reuse, signal, unlink, reclaim, and adoption caller and classifies it as `private-root-remove` or `caller-shared-root-retain`.
- After the shared owner is stable, fixture/test work may be split between lifetime-retirement cases and private-root/caller-shared-root topology cases. Runtime verification is the final serial lane.

## Plan

- [ ] Freeze the current topology and authority matrix: record every backend/sidecar, launcher/client claim, self-connection, idle rule, service record, lock, shutdown/reap path, and same-root coordination caller; give each row one lifetime owner and one `private-root-remove` or `caller-shared-root-retain` decision.
- [ ] Add one shared lifetime owner that observes the last valid external claim disappearing and performs bounded graceful then forced shutdown without waiting for a future restart; self-connections never count as demand and deliberately retained services must state their surviving supervisor.
- [ ] Remove background-owner election, compatible-service reuse, signal, unlink, reclaim, and adoption from managed private-root instances; preserve only the explicitly justified caller-shared-root compatibility path through the same lifecycle owner.
- [ ] Bind every destructive decision to stable host and boot identity plus exact PID start identity, namespace, service kind, generation, and claim; ambiguous, legacy, stale, or reused identities receive zero signals/unlinks and one typed retained orphan diagnostic.
- [ ] Add a bounded host-local repair path for verified legacy/untracked processes using the same identity verifier, with retained orphan age, attempted action, result, and failure reason visible through the existing status owner.
- [ ] Add deterministic fixture regressions for launcher crash without restart, self-connection, last-client disconnect, replacement handoff, old/new generation mismatch, PID/PGID reuse, stale lease, a child whose fixture directory is removed, two private roots, two callers sharing one root, incompatible generations on one caller-shared root, and graceful-to-forced escalation without touching unrelated processes.
- [ ] Update `docs/specs/BACKEND_ARCHITECTURE.md`, run the focused lifecycle/topology suites and the canonical gate, then restart two isolated fixture servers and prove stopping either changes no process, socket, or file owned by the other.

## Rejected Shortcuts

- Do not use hostname, PPID, PGID, command text, socket path, or a future restart as sufficient authority.
- Do not add a broad host sweeper, keep private-root election for convenience, or let a process count its own connection as external demand.
- Do not claim the historical temporary-directory traceback proves its former orphan-child attribution; reproduce the current first failing boundary.

## Done Criteria

- [ ] The DONE note records the implementation HEAD, the complete authority matrix, exact focused node IDs, commands/exit codes, numeric graceful/forced budgets, and `/tmp` evidence; every current backend/sidecar and coordination caller appears exactly once.
- [ ] `python3 -m pytest -q tests/test_local_services_watchdog.py tests/test_local_services_launch.py tests/test_local_service_registry_state.py tests/test_local_services_host_identity.py` exits 0 with a controllable clock and covers every topology and lifecycle case named in the Plan.
- [ ] For every non-retained service, removing the last valid external claim produces graceful exit no later than the declared grace deadline plus one supervision pass; a deliberately wedged child is force-terminated no later than the declared force deadline plus one pass, with both deadlines asserted without sleeps.
- [ ] Managed private-root fixtures perform zero cross-root election, reuse, signal, unlink, reclaim, or adoption; caller-shared-root fixtures retain exactly one compatible owner only where the matrix says `caller-shared-root-retain`, and incompatible generations fail typed and isolated.
- [ ] PID/PGID reuse, stale leases, old generations, ambiguous foreign identity, and two co-tenant deployments receive exactly zero signals and zero unlinks; each ambiguous survivor emits one typed orphan record within one supervision pass rather than remaining silent.
- [ ] The removed-fixture-directory regression terminates or fences the exact child before its next write and produces no `atomic_write_text`/`chmod` after fixture teardown; any different reproduced owner is named in the DONE note instead of preserving the historical hypothesis.
- [ ] An unmodified `python3 tools/check.py` exits 0; after restarting two isolated fixture servers, the recorded runtime report lists every expected retained service and zero dead-owner or silent-orphan rows, and stopping either instance leaves the other's roster and files byte-for-byte unchanged.

## Completion

When every checkbox is checked, summarize the merged lifecycle/root-ownership contract in `docs/DONE/`, remove this queue, and do not retain the absorbed `DOIT.p2.same-root-coordination-simplification.md` as a second source.
