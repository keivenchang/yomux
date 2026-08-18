# DOIT.p1.e2.working-detector-footer-fragility.md - An Unrecognized Footer Silently Cancels "Working"

**p1 for 0.7.8.** Found 2026-08-15 while investigating why the `yo7771-b` tab showed a red `BLOCKED` badge while its Codex pane was visibly working.

## What is NOT the bug

Worth stating first, because the obvious hypothesis is wrong and re-deriving it costs an hour.

The badge was **correct**. `Goal blocked (/goal resume)` is a *sticky* Codex footer: it persists until the operator runs `/goal resume`, and it had been on screen for hours while Codex committed and drained its queue. Codex's goal loop genuinely was blocked; only its per-turn activity was healthy. The detector at `yolomux_lib/approval/prompt_detector.py:1569` is also correctly ordered — the status-counter branch returns before the `goal blocked` check, so a real mid-turn pane classifies as `working`:

| pane state | key |
| --- | --- |
| mid-turn: `Working (Ns)` counter + footer | `working` |
| between turns: footer only | `blocked` |
| between turns: no footer | `idle` |

Since a pane is only mid-turn for a small fraction of wall-clock time, the badge sits on `blocked` most of the time. That is a reporting-granularity question, not a defect, and it is not what this queue is about.

## The actual defect

`visible_agent_working` (`yolomux_lib/approval/prompt_detector.py:1064`) ends with:

```python
working_index = _last_working_index(lines)
return working_index >= 0 and not _working_line_has_later_prompt(lines, working_index)
```

A trailing line that the footer/separator recognizers do not classify is treated as a **later prompt**, which cancels an otherwise valid working row. Measured A/B with a byte-identical `Working (1s • esc to interrupt)` line and only the footer text changed:

```
footer '  gpt-5.6-sol high  Goal blocked (/goal resume)'
    -> key='blocked'   visible_agent_working=False

footer '  gpt-5.6-sol high · ~/dev/yolomux.dev7771        Goal blocked (/goal resume)'
    -> key='working'   visible_agent_working=True
```

The only difference is the `· <cwd>` segment. Recognition of the footer — and therefore the entire working classification — depends on an incidental piece of text that has nothing to do with whether the agent is working.

## Why this matters in practice

Codex truncates its footer to fit the pane width, and the cwd is the long, droppable part. So the predicted failure is: **narrow pane, footer renders without the cwd, and a genuinely working agent is classified `blocked`.**

**That prediction is unverified.** The live pane went idle during the sampling window, so the narrow-pane case was never reproduced. Do not treat it as established — item 1 exists to settle it.

This is the same shape as `DOIT.p0.window-strip-vanishes.md`: an input the recognizer does not understand silently discards a good signal instead of being ignored or reported.

## Plan

- [x] Settle the narrow-pane prediction first. Drive a real Codex pane at a width that truncates the footer while the agent is mid-turn, capture the exact footer text, and record whether `visible_agent_working` returns False. If it does not reproduce, say so and re-scope this queue to the general fragility rather than quietly closing it.
- [x] Enumerate the real footer shapes rather than guessing. Collect actual last lines from Claude and Codex panes at several widths and with several trailing states, and use them as fixtures.
- [x] Make an unrecognized trailing line non-authoritative. A line the recognizer cannot classify must not be promoted to "later prompt" and must not cancel a working row. Defaulting to "this cancels the signal" is the wrong default for an unknown input.
- [x] Make the cancellation observable. When a working row is discarded because of a later-prompt verdict, that decision needs to be inspectable; today it is a silent boolean and the reason never reaches a diagnostic.
- [x] Add fixtures per collected footer shape asserting the classification does not change when only the footer's incidental text changes.

## Done Criteria

- [x] The narrow-pane case is either reproduced and fixed, or explicitly recorded as not reproducible with the evidence that settles it.
- [x] A byte-identical working row classifies identically across every collected footer shape, covered by fixtures seen to fail first.
- [x] An unrecognized trailing line never cancels a working row.
- [x] A discarded working row records why, and a test covers it.
- [ ] Canonical gate green, no new Warnings or Errors.

## Evidence

- Real Codex 0.147.0 was exercised at 120, 100, 90, 80, 70, 60, and 50 columns. Every sampled width retained the Working row, composer, and model/cwd footer, so the predicted narrow-width failure did not reproduce; the queue is explicitly re-scoped to the general unknown-footer defect.
- Red-first fixtures showed unknown no-cwd and future footer shapes canceling a byte-identical Working row. Additional red regressions caught stale Working incorrectly surviving assistant completion, generic choice, AskUserQuestion, and current questions combined with sticky `Goal blocked`.
- One structured verdict now owns Working classification and diagnostic reason/evidence/row. Unknown footer chrome is non-authoritative; recognized shell prompts, completion rows, generic choices, and AskUserQuestion are authoritative; a current question outranks stale `Goal blocked`, while live Working still outranks stale questions.
- `python3 -m pytest tests/test_auto_approve_detector.py -q` passed 179 tests after the final precedence correction, and `git diff --check` passes. The final landing gate remains open.
- The pre-landing source audit found that a discarded Working verdict lost its structured reason when sticky `Goal blocked` won the final screen state. Blocked and idle now share `_nonworking_screen_diagnostics`; the exact sticky-blocked regression failed first, then the detector suite passed 180/180 with current-question and live-Working precedence unchanged.
- The combined `pytest-unit` lane passed 17,230 tests before failing only the unrelated live-port fixture guard. That exact guard passed after its inert test key moved from live port 7772 to safe port 7442; under the reduced evidence policy the 11-minute lane was not repeated. The exact-SHA full gate remains the landing step.

## Completion

Record in `docs/DONE/` with the collected footer fixtures, then delete this queue.
