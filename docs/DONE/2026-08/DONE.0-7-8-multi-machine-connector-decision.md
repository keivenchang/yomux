# 2026-08-15 v0.7.8 multi-machine connector decision

Decision: `NO_BUILD`.

The only current workflow that needs unattended lin1/lin2 coordination is agent tell/mail/job handoff. `~/dev/ai-config/claude/bin/xhost.sh` already owns that workflow through SSH plus durable shared files, while separate authenticated YOLOmux tabs own host-local viewing and control. The cross-host read-view lane is deliberately read-only, so it cannot execute tell/mail/job sends or any other mutating handoff. Adding a YOLOmux connector would duplicate transport and grant delegated server-to-server administration without a product workflow that requires it.

The retained trust boundary is browser-to-target-host authentication; `NO_BUILD` adds no peer listener or server-to-server TLS endpoint. Any future connector proposal must separately justify stable host and boot identity, explicit peer discovery and allowlisting, mutually authenticated or pinned peer TLS, action allowlists, key rotation and revocation, schema/version negotiation, bounded deadlines, correlated audit records, and rollback. Partitions must not impair either local deployment; ambiguous mutations must not auto-retry; spoofed or reused identities, compromised keys, clock skew, mixed versions, reboot generations, partial responses, and unavailable peers must fail closed.

Current evidence is the independent-deployment contract in `README.md`, local-session scope and durable-bridge guidance in `docs/YOAGENT.md`, the absence of connector routes or a cross-host service owner, and the existing cross-host decision that rejects a general aggregator without concrete demand. No runtime code changed for this decision.
