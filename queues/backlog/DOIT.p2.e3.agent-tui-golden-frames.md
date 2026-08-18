# DOIT.p2.e3.agent-tui-golden-frames.md - Pin Remaining Scrape-And-Type Frames

## Goal

Record real Claude and Codex `capture-pane` frames for every remaining scrape/type fallback so upstream TUI changes fail tests instead of silently changing readiness, approval, spinner, or footer detection.

## Plan

- [ ] Inventory every remaining fallback and the exact upstream client/version/state it needs; remove cases already covered by structured control.
- [ ] Capture real frames for Claude and Codex, classify provider-specific versus parity states, sanitize them, and add them to the existing prompt corpus and inventory.
- [ ] Update mocks, detectors, docs, and regressions from the real captures; do not fabricate a same-name peer fixture when the product has no equivalent surface.

## Done Criteria

- [ ] Every remaining fallback maps to a current real capture or an explicit blocked capture target, and every adopted frame has ready/working/approval/footer negative tests.
- [ ] Detector, mock parity, corpus, and canonical gate tests pass with no unsanitized terminal content committed.
