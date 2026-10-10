# YOmux Work Queues

Active work lives in concrete `backlog/DOIT*.md` files; there is no separate historical `STATUS-REPORT.md` ledger. Queue filenames use `DOIT.p<num>.e<num>.<desc>.md`: priority ranges from `p0` urgent through `p2` deferred, and effort ranges from `e1` small or bounded through `e5` architecture-scale, cross-system, or long-soak work. Shipped behavior belongs in current user documentation and specifications; release summaries group scope by minor version.

Existing queue bodies preserve the Python architecture and evidence in effect when they were written unless a queue explicitly names Go. Do not rewrite a paused queue to pretend its `statsd`, `watchd`, descriptor, or service-lifetime findings were measured against the Go modular monolith. The product target is Go-only: every supported Python-only behavior and Python runtime/tooling dependency must be ported or replaced before Python removal. When work resumes, revalidate the reproduction on Go and either update the queue with dated backend-specific evidence or create the one concrete Go owner for a distinct requirement.

There is no broad TODO backlog. Every newly authorized open requirement must have one concrete DOIT owner, priority, reproduction or decision boundary, and done criteria. Find every active queue with `rg --files -uu queues/backlog -g 'DOIT*.md' | sort`; `DOIT*.md` is gitignored, so normal Git status does not enumerate it.

YOmux remains a lightweight workspace for managing AI work: agent state and control, editing and viewing, collaboration, repository and file context, and low-friction attach/reply. Borrowed features must improve that local control loop. Event and audit data precede timeline UI; broad multi-machine orchestration, visual canvases, and pipeline boards remain deferred until the local product has a measured need.

When a queue is complete, verify its current evidence, update user-facing docs and specifications where required, and remove the queue. Do not create a separate DONE archive. Keep unfinished requirements in exactly one active queue.
