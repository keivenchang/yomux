# DOIT.p2.multi-machine-connector.md - Decide And Bound Multi-Machine Connectivity

## Goal

Defer implementation until the local product is stable, then decide whether a concrete workflow justifies a connector that changes authentication, networking, logging, and failure modes.

## Plan

- [ ] Name one user workflow that requires connected machines and cannot be served correctly by separate authenticated tabs or the cross-host read-view decision.
- [ ] Define source identity, authentication, authorization, discovery, TLS, clock/schema compatibility, partial/unavailable state, audit logging, upgrade, rollback, and network-partition behavior.
- [ ] Record `NO_BUILD` unless the concrete workflow and security/failure costs justify one narrow separately approved implementation queue.

## Done Criteria

- [ ] The decision names the workflow, evidence, trust boundary, failure model, and literal `NO_BUILD` or `PROCEED_WITH_SEPARATE_QUEUE`; this queue changes no runtime code.
