# YOLOmux Work Queues

`STATUS-REPORT.md` is the sole source for the current release goal, order, and progress. Active work outside that goal lives in `backlog/DOIT.p0.*.md`, `backlog/DOIT.p1.*.md`, and `backlog/DOIT.p2.*.md`; completed work lives in `docs/DONE/`.

There is no broad TODO backlog. Every open requirement must have one concrete DOIT owner, priority, reproduction or decision boundary, and done criteria. Find every active queue with `rg --files -uu queues/backlog -g 'DOIT*.md' | sort`; `DOIT*.md` is gitignored, so normal Git status does not enumerate it.

YOLOmux remains a lightweight workspace for managing AI work: agent state and control, editing and viewing, collaboration, repository and file context, and low-friction attach/reply. Borrowed features must improve that local control loop. Event and audit data precede timeline UI; broad multi-machine orchestration, visual canvases, and pipeline boards remain deferred until the local product has a measured need.

When a queue is complete, verify its current evidence, archive the result in `docs/DONE/`, update user-facing docs and specifications where required, and remove the queue. Requirements must not survive in both an active queue and a DONE record.
