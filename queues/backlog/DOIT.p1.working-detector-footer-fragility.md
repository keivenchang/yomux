# DOIT.p1.working-detector-footer-fragility.md - An Unrecognized Footer Silently Cancels "Working"

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

- [ ] Settle the narrow-pane prediction first. Drive a real Codex pane at a width that truncates the footer while the agent is mid-turn, capture the exact footer text, and record whether `visible_agent_working` returns False. If it does not reproduce, say so and re-scope this queue to the general fragility rather than quietly closing it.
- [ ] Enumerate the real footer shapes rather than guessing. Collect actual last lines from Claude and Codex panes at several widths and with several trailing states, and use them as fixtures.
- [ ] Make an unrecognized trailing line non-authoritative. A line the recognizer cannot classify must not be promoted to "later prompt" and must not cancel a working row. Defaulting to "this cancels the signal" is the wrong default for an unknown input.
- [ ] Make the cancellation observable. When a working row is discarded because of a later-prompt verdict, that decision needs to be inspectable; today it is a silent boolean and the reason never reaches a diagnostic.
- [ ] Add fixtures per collected footer shape asserting the classification does not change when only the footer's incidental text changes.

## Done Criteria

- [ ] The narrow-pane case is either reproduced and fixed, or explicitly recorded as not reproducible with the evidence that settles it.
- [ ] A byte-identical working row classifies identically across every collected footer shape, covered by fixtures seen to fail first.
- [ ] An unrecognized trailing line never cancels a working row.
- [ ] A discarded working row records why, and a test covers it.
- [ ] Canonical gate green, no new Warnings or Errors.

## Completion

Record in `docs/DONE/` with the collected footer fixtures, then delete this queue.
