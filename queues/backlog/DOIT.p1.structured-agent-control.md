# DOIT.p1.structured-agent-control.md - Replace Scrape-And-Type Control Where Supported

## Goal

Use structured agent channels for approvals, state, and controlled sends wherever the provider supports them, while keeping `tmux-legacy` as the verified visible-pane fallback.

## Plan

- [ ] Inventory Claude permission hooks, Codex app-server/SDK/MCP, sessions YOLOmux owns or can safely resume, and every current scrape/type action; record identity, authorization, reply, cancellation, timeout, and restart semantics.
- [ ] Define one provider-neutral control contract and capability adapters for Claude and Codex; do not hide provider-specific guarantees behind a false common denominator.
- [ ] Expose the authenticated local control contract through an MCP/ACP-style API so agents can query session/activity state and request server-verified sends.
- [ ] Preserve a tested visible-pane `tmux-legacy` fallback with golden-frame detection and explicit degraded state.

## Done Criteria

- [ ] Every control action selects one declared adapter, reports structured versus fallback ownership, and has authorization, exactly-once outcome, timeout, cancellation, reconnect, and restart tests.
- [ ] Focused provider tests, browser tests, security checks, the canonical gate, and restarted real Claude/Codex journeys pass on one unchanged HEAD.
