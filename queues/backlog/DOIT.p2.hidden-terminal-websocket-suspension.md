# DOIT.p2.hidden-terminal-websocket-suspension.md - Evaluate Hidden-Document Terminal Suspension

## Goal

Decide whether terminal WebSockets can suspend after a bounded hidden-document grace period without losing input, state, or attention transitions.

## Plan

- [ ] Freeze current socket, resize authority, scrollback, attention, reconnect, and current-snapshot behavior under visible/hidden transitions.
- [ ] Prototype one demand owner with a bounded grace period; keep HTTP/SSE demand gating independent.
- [ ] Prove no lost input, duplicate socket, resize-authority drift, scrollback loss, missed attention transition, or incomplete current-snapshot recovery.

## Done Criteria

- [ ] Record `KEEP_CONNECTED` or `SUSPEND_AFTER_<N>_SECONDS` from measured CPU/network savings and the full correctness matrix; do not enable suspension from idle traffic alone.
- [ ] Node/browser/lifecycle tests and the canonical gate pass; a real hidden/resume soak proves the selected behavior.
