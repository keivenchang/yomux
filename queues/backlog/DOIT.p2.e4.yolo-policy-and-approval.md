# DOIT.p2.e4.yolo-policy-and-approval.md - Add Visible Scoped YOLO Policy

## Goal

Expose pending high-risk actions and make YOLO policy visible and editable across global, repository, session, agent, and prompt-type scopes.

## Plan

- [ ] Add a read-only approval queue before any live allow/deny interception; define pending identity, risk, source, age, terminal outcome, and retention.
- [ ] Add per-session modes `off`, `prompt-only`, `safe`, `edit`, and `full`, visible on the tmux-session YOLO control.
- [ ] Define rule precedence for global/repository/session, Claude/Codex, and bash/file/tool prompt scopes through the existing first-match-wins YAML engine.
- [ ] Decide `RAW_YAML` or `STRUCTURED_EDITOR`; if structured, support add/remove/reorder, type/action/risk selectors, match lists, and top-level default without a second rule model.
- [ ] Add concrete `read`, `edit`, `network`, `process`, `delete`, `credential`, and `unknown` risk profiles with audit events and fail-closed unknown behavior.

## Done Criteria

- [ ] One policy evaluator and one precedence table drive the visible control, queue, YAML validation, and any later interception; every scope/risk/mode combination has table tests.
- [ ] Browser/accessibility/security tests, the canonical gate, and restarted Claude/Codex policy journeys pass before interception is enabled.
