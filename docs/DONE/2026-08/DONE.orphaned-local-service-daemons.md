# DOIT.p1.e2.orphaned-local-service-daemons.md - Daemons Outlive Their Servers By Weeks

Found 2026-08-18 while researching a `jobd.produce` failure burst.

## Evidence

**Nine jobd processes running**, several bound to sockets belonging to servers that exited days ago:

```
1443586  14d 04h  --socket /tmp/yo7773-p95-f...
3283440  13d 19h  --socket /tmp/yo7772-profi...
2371716  13d 13h  --socket /tmp/y1776734304/...
3159583  11d 10h  --socket /tmp/yag-nrrph7sz...   (YO!agent run, long finished)
3195706  11d 10h  --socket /tmp/yag-o93mh_bn...   (YO!agent run, long finished)
```

Same shape elsewhere: **27 `statsd.*.lock` files** in one services directory, and **4 statsd processes** live at once.

## The failure that surfaced it

Eight `service_unavailable` errors in five seconds (05:01:39-05:01:44 PT) with the stack `server.http GET /api/fs/watch-diff -> local_services.jobd jobd.produce`, plus one on `POST /api/stats-observations` six seconds earlier.

**Whether a stale-socket mismatch caused that specific burst is NOT established.** Do not assume it. Daemons outliving their servers by two weeks is a defect on its own terms, and it is also the most obvious candidate to check first.

## Plan

- [x] Establish whether the burst and the orphans are related before fixing either. Identify which jobd the web process was talking to at 05:01 and whether it was live, saturated, or gone. If unrelated, split this queue. DONE: the p7772 web process PID 25102 was routed to its correct live jobd PID 18465/socket throughout the burst; `/api/ping` remained 200, accepted watch-diff work later completed with 503, and the route recovered at 05:01:44. An isolated red reproducer found the first incorrect transition at completion-slot reservation before identical watch-diff work reaches jobd coalescing. That separate defect now lives in `DOIT.p1.e3.jobd-reload-fanout-admission.md`; no orphan evidence is being used to close it.
- [x] Give every local service a lifetime bound to its owning server. A daemon whose server has exited must exit, not linger for two weeks holding a socket. DONE: the shared `run_local_rpc_service` listener records its direct launching parent and exits as soon as that parent identity changes, before accepting more work; all six current local-service callers inherit the same owner.
- [x] Reap on startup. A new server should retire orphans matching its own identity rather than adding to them. DONE: `LocalServiceRegistry` now treats a compatible or newer daemon as reclaimable only when the durable record and tracked process group prove the exact socket/PID and its launcher is dead; startup retires that generation and spawns one replacement instead of adopting it.
- [x] Clean up the lock-file accumulation in the services directory; 27 generations is evidence nothing prunes them. DONE: one registry maintenance owner now runs once per web generation before even the recently-healthy adoption shortcut, under the service start file lock; candidate locks still require a nonblocking lock plus inode recheck. The exact wrapper regression passed, and fenced production maintenance removed 26 unlocked obsolete statsd generations, reducing 27 locks to the one active `d079435ab846ac84` generation without restarting 7770.
- [x] Decide deliberately what YO!agent-spawned daemons (`yag-*` sockets) belong to and who retires them. DONE: `yag-*` services belong to the direct YO!agent launcher that created their isolated runtime; they use the same parent-PID lifetime owner, so a finished run cannot leave its service generation behind.

## Done Criteria

- [x] No local-service process outlives its owning server, covered by a test that kills a server and asserts its daemons exit. DONE: the red subprocess service remained alive beyond three seconds after its launcher died; the fixed test now observes both process and socket gone, and the full owning modules pass 190/190.
- [x] A fresh server start leaves no orphan from a prior generation. DONE: the registry regression covers both compatible and newer exact dead-launcher generations, proves `shutdown` occurs, and proves exactly one replacement starts; the focused startup group passes 5/5.
- [x] Current orphans are cleared, with the count recorded before and after. DONE: exact PID/start/PGID/socket/launcher revalidation authorized five stale jobd process groups and one legacy approvald group; SIGTERM retired all group members. Jobd fell from nine processes to four current instances (7770-7773), the stale approvald fell away while four per-instance approvald peers remained, and live 7770/7771/7772 identities were explicitly protected.
- [x] Canonical functional gate green; record exact-SHA certification separately. DONE: all nine functional lanes passed on candidate `80b4bf8ce`, including the corrected parent-bound restart acceptance and orphan-registry owners. Two certification-only attempts were NOT CERTIFIABLE because the shared host exceeded measured I/O/CPU stall limits; no certification pass is claimed. This recorded exception follows Keiven's tiered evidence policy and does not create a tag, push, or production restart claim.
