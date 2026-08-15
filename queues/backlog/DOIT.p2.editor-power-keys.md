# DOIT.p2.editor-power-keys.md - Add Remaining CodeMirror Commands

## Goal

Add multi-cursor, select-all-occurrences, cursor above/below, line move/copy/delete, smart select, matching bracket, fold/unfold, symbol jump, and command mode through CodeMirror where possible.

## Plan

- [ ] Map each command to CodeMirror's native command/keymap and record platform conflicts; do not add app-side Ctrl-letter bindings on macOS.
- [ ] Add one command registry consumed by keyboard shortcuts, command UI, labels, enabled state, and tests.
- [ ] Preserve editor text, history, selection, multiple cursors, focus, dirty state, merge view, read-only state, and live reconfiguration.

## Done Criteria

- [ ] Every named command has a declared key/UI path or an explicit unsupported reason, with macOS/Linux parity and conflict tests.
- [ ] Focused Node/real-browser editor tests, generated assets, the canonical gate, and restarted editing journeys pass without editor rebuilds.
