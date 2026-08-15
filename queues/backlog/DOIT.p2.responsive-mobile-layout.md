# DOIT.p2.responsive-mobile-layout.md - Add A Usable Mobile Focus Mode

## Goal

Provide a single-pane mobile focus mode with touch-friendly navigation, larger controls, upload/paste affordances, and enough editor/diff behavior for remote check-ins.

## Plan

- [ ] Freeze current phone and tablet geometry, focus, keyboard, touch, terminal, editor, diff, menu, upload, and pane-switch behavior with real viewport tests.
- [ ] Add one responsive state owner for pane focus and navigation; preserve desktop layout and URL restoration.
- [ ] Implement touch-sized controls and mobile editor/diff/upload flows without duplicating desktop actions.

## Done Criteria

- [ ] Phone/tablet browser matrices prove pane navigation, terminal input, editor/diff inspection, upload/paste, focus, safe-area, rotation, and desktop parity.
- [ ] Generated assets, accessibility checks, the canonical gate, and real-device or device-emulation acceptance pass on one HEAD.
