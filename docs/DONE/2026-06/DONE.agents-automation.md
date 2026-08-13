# Agents and Automation

## YO ball spins on □/✓ task lists + auto-focus records nav history (DOIT.35)
- C1: a session actively working with a Ctrl-T task list showed the YO ball NOT spinning. This Claude version renders task rows with `□` (U+25A1) / `✓` (U+2713) / `✗` / `◯`, but `prompt_detector._is_prompt_trailing_ui_line` only knew the U+2610 ballot-box family — so the task rows read as new output, `visible_agent_working` flipped to False, state went `idle`, and the spin stopped. Extended the task-row glyph class + `startswith` tuple to include `□✓✔✗✘◯`. Now the task list reads as prompt-trailing chrome and the ball spins while the agent works. Regression test covers the `□`/`✓` rows + the old `☐` form.
- C2: auto-focus didn't record back/forward history (only `userInitiated` activations did), so an auto-focus jump was invisible to Back. Added a debounced `recordAutoFocusNav` in `setFocusedPanelItem` (gated on `autoFocusEnabled`): it records the focus that LANDS after a ~500ms dwell, so rapid auto-focus flapping doesn't flood the stack and a back/forward re-activation no-ops via the consecutive-dedupe. Node guard covers the wiring.

## Auto-approve: `extract_command` read the wrong (stale) command (DOIT.17)
- A Bash permission prompt was detected as `approval`, but `extract_command` walked its backward `● Bash(…)` search past the `─────` separator and the prior `● Done.` into the PREVIOUS step, returning that step's stale (safe) `chmod` instead of the pending `cp -r src/ dist/`. The worker then classified danger on the wrong command — a real safety hole (a dangerous `cp -r` approved on the strength of an unrelated safe command) and a cause of attention not lighting under auto-approve. Fix (detector only — the mock correctly matches real Claude): bound the `● Bash(…)` search to the current prompt block (stop at the separator/box-top), stop the box-body path at the tool description instead of folding it into the command, anchor `_SKIP_LINE` as `Bash command\b` so the `(unsandboxed)` header is always skipped, and make the Codex path accept an un-prefixed command line. Regression test (`test_extract_command_does_not_cross_separator_into_prior_step`) added; detector+worker suites 60 passed. Remaining is user-gated live validation (auto-approve ON/OFF on a real agent screen), tracked in TODO.

## Auto-approve (YOLO) reliability
- Claude `PreToolUse` permission hook shipped (DOIT.11): `yolomux_lib/claude_permission_hook.py` decides allow/deny/ask from the agent's structured request via the existing `yolo_rules` engine (no TUI scraping, no keystrokes), with 17 tests pinning the mapping + the hard-floor + fail-safe. **Remaining is user-gated / deferred and tracked in TODO Big-Bang #1**: the manual `~/.claude/settings.json` install (must not be auto-edited), live validation, the keystroke-worker stand-down (gated on the hook being live), and the Codex `app-server` re-architecture.

---

Completed 2026-06-03. Extracted from the 2026-06-03 daily log.
